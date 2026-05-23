"""Pricing + persistence + render business logic for the /v1/quote endpoint."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from app.domain.pricing import build_quotation_config
from app.domain.pricing_baseline import load_baseline, pricing_version
from app.domain.product_descriptions import load_descriptions
from app.domain.render_pdf import render_pdf
from app.domain.render_xlsx import render_xlsx
from app.domain.schema import (
    FileRef,
    QuoteItemPreview,
    QuotePreview,
    QuoteTotals,
)
from app.errors import OutOfRangeError, RenderError
from app.persistence import get_conn
from app.persistence.models import Approval, Quote, QuoteRender
from app.persistence.quote_repo import (
    create_quote as _persist_quote,
    latest_render,
    persist_render as _persist_render,
    upsert_approval,
)
from app.storage import OssStorage, Storage


def sanitize(name: str) -> str:
    return str(name).strip().replace("/", "-")


def today_stamp() -> str:
    return datetime.now().strftime("%Y%m%d")


def price_and_persist(
    *,
    form: dict,
    org: str,
    db_path: Path,
    baseline: dict,
    product_catalog_path: Path,
    product_descriptions_path: Optional[Path] = None,
    idempotency_key: Optional[str] = None,
) -> tuple[Quote, Approval, dict]:
    """Price the form, persist quote + approval rows, return (quote, approval, config_dict).

    Idempotent on (org, form_hash) by default. If `idempotency_key` is supplied,
    it takes precedence — replaying the same key must use the same form or we
    raise ValueError (client bug).
    """
    descriptions = load_descriptions(product_descriptions_path)
    try:
        config = build_quotation_config(form, baseline, product_catalog_path, descriptions=descriptions)
    except ValueError as e:
        msg = str(e)
        field = "人工改价原因" if "人工改价" in msg else None
        hint = "提供 成交价系数 时必须同时填写 人工改价原因" if field else None
        raise OutOfRangeError(field=field or "form", message=msg, hint=hint)

    pricing_info = config.get("pricing_info", {})
    with get_conn(db_path) as conn:
        quote = _persist_quote(
            conn,
            org=org,
            form=form,
            config=config,
            pricing_version=pricing_version(),
            idempotency_key=idempotency_key,
        )
        approval = upsert_approval(
            conn,
            quote_id=quote.id,
            required=bool(pricing_info.get("approval_required")),
            reasons=list(pricing_info.get("approval_reason") or []),
            requested_by=org,
        )
    return quote, approval, config


def render_format(
    *,
    quote: Quote,
    format: str,
    db_path: Path,
    storage: Storage,
    fonts_dir: Path,
    force: bool = False,
) -> QuoteRender:
    """Render one format for a persisted quote. Reuse existing render if `force` is False.

    `format` ∈ {"pdf", "xlsx", "json"}. Raises RenderError on failure.
    """
    if format not in {"pdf", "xlsx", "json"}:
        raise ValueError(f"unsupported format: {format}")

    if not force:
        with get_conn(db_path) as conn:
            existing = latest_render(conn, quote.id, format)
            if existing is not None:
                return existing

    form = json.loads(quote.form_json)
    config = json.loads(quote.config_json)
    brand = sanitize(form["客户品牌名称"])
    today = today_stamp()

    if format == "pdf":
        suffix = "报价单"
        content = render_pdf(config, fonts_dir=fonts_dir)
    elif format == "xlsx":
        suffix = "报价单"
        content = render_xlsx(config)
    else:
        suffix = "报价配置"
        content = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")

    filename = f"{brand}-全来店-{suffix}-{today}.{format}"
    try:
        download_url, expires_at, file_token = storage.save(filename, content)
    except Exception as e:
        raise RenderError(message=f"{format.upper()} 保存失败: {e}")

    with get_conn(db_path) as conn:
        return _persist_render(
            conn,
            quote_id=quote.id,
            format=format,
            file_token=file_token or download_url,
            filename=filename,
            expires_at=expires_at.isoformat(),
        )


def render_to_file_ref(render: QuoteRender, base_url: str, storage: Storage | None = None) -> FileRef:
    if render.short_id:
        # Return a stable short link; the high-entropy OSS signature is
        # generated on each visit to /q/{short_id}, never in the chat text.
        # The link itself never expires (it re-signs on access), so expires_at
        # is reported as effectively-permanent rather than the OSS sig window.
        url = f"{base_url.rstrip('/')}/q/{render.short_id}"
        expires_at = datetime.fromisoformat(render.created_at) + timedelta(days=3650)
        return FileRef(url=url, filename=render.filename, expires_at=expires_at)

    # Legacy renders (no short_id): keep returning a signed / local URL directly.
    expires_at = datetime.fromisoformat(render.expires_at)
    if render.file_token.startswith("http://") or render.file_token.startswith("https://"):
        url = render.file_token
    elif isinstance(storage, OssStorage):
        url, expires_at = storage.resolve_url(render.file_token)
    else:
        url = f"{base_url.rstrip('/')}/files/{render.file_token}/{render.filename}"
    return FileRef(
        url=url,
        filename=render.filename,
        expires_at=expires_at,
    )


def build_preview(config: dict, form: dict) -> QuotePreview:
    items_preview: list[QuoteItemPreview] = []
    for item in config.get("报价项目", []):
        items_preview.append(
            QuoteItemPreview(
                name=item["商品名称"],
                qty=item["数量"],
                list=int(item.get("标准价", 0) if item.get("标准价") != "赠送" else 0),
                final=int(item.get("报价小计", 0)),
            )
        )
    total_list = sum(i.list * i.qty for i in items_preview)
    total_final = sum(i.final for i in items_preview)
    pricing_info = config.get("pricing_info", {})
    # preview.stores 反映实际报价使用的门店数。对于大客户段(31-300),
    # config["门店数量"] 已被 build_quotation_config 替换为 effective
    # store count(tier_window[0]);小段情况下 config["门店数量"] ==
    # form["门店数量"]。两种场景都优先用 config。
    effective_stores = int(config.get("门店数量", form["门店数量"]))
    return QuotePreview(
        brand=form["客户品牌名称"],
        meal_type=form["餐饮类型"],
        stores=effective_stores,
        package=form["门店套餐"],
        discount=float(pricing_info.get("final_factor", 1.0)),
        totals=QuoteTotals(list=total_list, final=total_final),
        items=items_preview,
    )


