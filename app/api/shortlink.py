from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from app.config import settings
from app.domain.quote_service import resolve_file_url
from app.persistence import get_conn
from app.persistence.quote_repo import get_render_by_short_id
from app.storage import build_storage

router = APIRouter()


@router.get("/q/{short_id}")
def redirect_short_link(short_id: str):
    if not settings.enable_shortlink:
        raise HTTPException(status_code=404, detail="链接不存在")

    db_path = settings.data_root / "quote.db"
    with get_conn(db_path) as conn:
        render = get_render_by_short_id(conn, short_id)
    if render is None:
        raise HTTPException(status_code=404, detail="链接不存在")

    storage = build_storage(settings)
    target = resolve_file_url(render, settings.api_base_url, storage)

    # 302 (not 301): the OSS signature is short-lived, so the redirect must not
    # be cached as permanent. no-store keeps proxies from reusing a stale sig.
    return RedirectResponse(
        url=target,
        status_code=302,
        headers={"Cache-Control": "no-store"},
    )
