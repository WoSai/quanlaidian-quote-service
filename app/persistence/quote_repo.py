import hashlib
import json
import sqlite3
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.persistence.db import gen_short_id
from app.persistence.models import Approval, Quote, QuoteRender


def canonical_form_hash(form: dict) -> str:
    """Deterministic hash of a QuoteForm for idempotency + dedup.

    sort_keys + compact separators + NFC-compatible UTF-8 ensures the same
    logical form always hashes the same even if clients format whitespace
    or reorder keys differently.
    """
    canonical = json.dumps(form, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _gen_id(prefix: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return f"{prefix}_{ts}_{secrets.token_hex(4)}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def find_by_form_hash(conn: sqlite3.Connection, org: str, form_hash: str) -> Optional[Quote]:
    row = conn.execute(
        "SELECT * FROM quote WHERE org=? AND form_hash=?",
        (org, form_hash),
    ).fetchone()
    return _row_to_quote(row) if row else None


def find_by_idempotency_key(
    conn: sqlite3.Connection, org: str, idempotency_key: str
) -> Optional[Quote]:
    row = conn.execute(
        "SELECT * FROM quote WHERE org=? AND idempotency_key=?",
        (org, idempotency_key),
    ).fetchone()
    return _row_to_quote(row) if row else None


def get_quote(conn: sqlite3.Connection, quote_id: str) -> Optional[Quote]:
    row = conn.execute("SELECT * FROM quote WHERE id=?", (quote_id,)).fetchone()
    return _row_to_quote(row) if row else None


def create_quote(
    conn: sqlite3.Connection,
    *,
    org: str,
    form: dict,
    config: dict,
    pricing_version: str,
    idempotency_key: Optional[str] = None,
) -> Quote:
    form_hash = canonical_form_hash(form)
    if idempotency_key:
        existing = find_by_idempotency_key(conn, org, idempotency_key)
        if existing is not None:
            # Replayed key — must be for same form, otherwise client is confused.
            if existing.form_hash != form_hash:
                raise ValueError(
                    f"Idempotency-Key {idempotency_key} was previously used with a different form"
                )
            return existing
    existing = find_by_form_hash(conn, org, form_hash)
    if existing is not None:
        return existing

    totals = config.get("internal_financials", {})
    items = config.get("报价项目", [])
    total_list = sum(int(i.get("标准价", 0) or 0) * int(i.get("数量", 0) or 0) for i in items)
    total_final = int(totals.get("quote_total", 0) or 0)
    factor = float(config.get("pricing_info", {}).get("final_factor", 1.0))

    quote = Quote(
        id=_gen_id("q"),
        org=org,
        form_hash=form_hash,
        idempotency_key=idempotency_key,
        form_json=json.dumps(form, ensure_ascii=False, sort_keys=True),
        config_json=json.dumps(config, ensure_ascii=False),
        factor=factor,
        total_list=total_list,
        total_final=total_final,
        pricing_version=pricing_version,
        created_at=_now_iso(),
    )
    conn.execute(
        """
        INSERT INTO quote (id, org, form_hash, idempotency_key, form_json, config_json,
                           factor, total_list, total_final, pricing_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            quote.id, quote.org, quote.form_hash, quote.idempotency_key,
            quote.form_json, quote.config_json, quote.factor, quote.total_list,
            quote.total_final, quote.pricing_version, quote.created_at,
        ),
    )
    return quote


def persist_render(
    conn: sqlite3.Connection,
    *,
    quote_id: str,
    format: str,
    file_token: str,
    filename: str,
    expires_at: str,
) -> QuoteRender:
    render = QuoteRender(
        id=_gen_id("r"),
        quote_id=quote_id,
        format=format,
        file_token=file_token,
        filename=filename,
        created_at=_now_iso(),
        expires_at=expires_at,
        short_id=gen_short_id(),
    )
    for _ in range(5):
        try:
            conn.execute(
                """
                INSERT INTO quote_render (id, quote_id, format, file_token, filename,
                                          created_at, expires_at, short_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    render.id, render.quote_id, render.format, render.file_token,
                    render.filename, render.created_at, render.expires_at, render.short_id,
                ),
            )
            return render
        except sqlite3.IntegrityError:
            render.short_id = gen_short_id()
    raise RuntimeError("无法为 quote_render 生成唯一 short_id")


def latest_render(conn: sqlite3.Connection, quote_id: str, format: str) -> Optional[QuoteRender]:
    row = conn.execute(
        """
        SELECT * FROM quote_render
         WHERE quote_id=? AND format=?
         ORDER BY created_at DESC
         LIMIT 1
        """,
        (quote_id, format),
    ).fetchone()
    return _row_to_render(row) if row else None


def get_render_by_short_id(conn: sqlite3.Connection, short_id: str) -> Optional[QuoteRender]:
    row = conn.execute(
        "SELECT * FROM quote_render WHERE short_id=?", (short_id,)
    ).fetchone()
    return _row_to_render(row) if row else None


def upsert_approval(
    conn: sqlite3.Connection,
    *,
    quote_id: str,
    required: bool,
    reasons: list[str],
    requested_by: Optional[str] = None,
) -> Approval:
    state = "pending" if required else "not_required"
    approval = Approval(
        id=_gen_id("a"),
        quote_id=quote_id,
        required=required,
        reasons=reasons,
        state=state,
        requested_by=requested_by,
        requested_at=_now_iso(),
    )
    conn.execute(
        """
        INSERT INTO approval (id, quote_id, required, reasons_json, state,
                              requested_by, requested_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(quote_id) DO UPDATE SET
            required=excluded.required,
            reasons_json=excluded.reasons_json
        """,
        (
            approval.id, approval.quote_id, int(approval.required),
            json.dumps(approval.reasons, ensure_ascii=False), approval.state,
            approval.requested_by, approval.requested_at,
        ),
    )
    return get_approval(conn, quote_id)


def get_approval(conn: sqlite3.Connection, quote_id: str) -> Optional[Approval]:
    row = conn.execute(
        "SELECT * FROM approval WHERE quote_id=?", (quote_id,)
    ).fetchone()
    return _row_to_approval(row) if row else None


def _row_to_quote(row: sqlite3.Row) -> Quote:
    return Quote(
        id=row["id"],
        org=row["org"],
        form_hash=row["form_hash"],
        idempotency_key=row["idempotency_key"],
        form_json=row["form_json"],
        config_json=row["config_json"],
        factor=row["factor"],
        total_list=row["total_list"],
        total_final=row["total_final"],
        pricing_version=row["pricing_version"],
        created_at=row["created_at"],
    )


def _row_to_render(row: sqlite3.Row) -> QuoteRender:
    return QuoteRender(
        id=row["id"],
        quote_id=row["quote_id"],
        format=row["format"],
        file_token=row["file_token"],
        filename=row["filename"],
        created_at=row["created_at"],
        expires_at=row["expires_at"],
        short_id=row["short_id"],
    )


def _row_to_approval(row: sqlite3.Row) -> Approval:
    return Approval(
        id=row["id"],
        quote_id=row["quote_id"],
        required=bool(row["required"]),
        reasons=json.loads(row["reasons_json"]),
        state=row["state"],
        requested_by=row["requested_by"],
        requested_at=row["requested_at"],
        decided_by=row["decided_by"],
        decision_reason=row["decision_reason"],
        decided_at=row["decided_at"],
    )
