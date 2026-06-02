import re
import sqlite3

import pytest

from app.persistence import init_db
from app.persistence.quote_repo import (
    create_quote,
    get_render_by_short_id,
    persist_render,
)


@pytest.fixture
def conn(tmp_path):
    p = tmp_path / "quote.db"
    init_db(p)
    c = sqlite3.connect(str(p))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    yield c
    c.close()


@pytest.fixture
def sample_config():
    return {
        "报价项目": [{"商品名称": "套餐A", "标准价": 100, "数量": 1, "报价小计": 100}],
        "internal_financials": {"quote_total": 100},
        "pricing_info": {"final_factor": 1.0},
    }


def test_persist_render_assigns_short_id(conn, sample_form, sample_config):
    q = create_quote(conn, org="acme", form=sample_form, config=sample_config, pricing_version="v1")
    render = persist_render(
        conn, quote_id=q.id, format="pdf", file_token="t1", filename="a.pdf", expires_at="2099-01-01"
    )
    assert render.short_id
    # pure ASCII base62, no chars needing URL escaping
    assert re.fullmatch(r"[A-Za-z0-9]+", render.short_id)


def test_get_render_by_short_id_roundtrip(conn, sample_form, sample_config):
    q = create_quote(conn, org="acme", form=sample_form, config=sample_config, pricing_version="v1")
    render = persist_render(
        conn, quote_id=q.id, format="pdf", file_token="t1", filename="a.pdf", expires_at="2099-01-01"
    )
    found = get_render_by_short_id(conn, render.short_id)
    assert found is not None
    assert found.id == render.id
    assert found.file_token == "t1"
    assert get_render_by_short_id(conn, "nope") is None


def test_quote_response_returns_short_links(api_client, sample_form):
    client, token = api_client
    resp = client.post("/v1/quote", json=sample_form, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    files = resp.json()["files"]
    for fmt in ("pdf", "xlsx", "json"):
        url = files[fmt]["url"]
        assert re.fullmatch(r"http://testserver/q/[A-Za-z0-9]+", url), url


def test_short_link_redirects_and_downloads(api_client, sample_form):
    client, token = api_client
    resp = client.post("/v1/quote", json=sample_form, headers={"Authorization": f"Bearer {token}"})
    pdf_url = resp.json()["files"]["pdf"]["url"]
    path = pdf_url.replace("http://testserver", "")

    redirect = client.get(path, follow_redirects=False)
    assert redirect.status_code == 302
    assert redirect.headers["location"].startswith("http://testserver/files/")
    assert redirect.headers.get("cache-control") == "no-store"

    downloaded = client.get(path, follow_redirects=True)
    assert downloaded.status_code == 200
    assert len(downloaded.content) > 0


def test_short_link_unknown_returns_404(api_client):
    client, _ = api_client
    assert client.get("/q/doesnotexist").status_code == 404


def test_shortlink_disabled_returns_direct_urls(api_client_no_shortlink, sample_form):
    """With the module off (default), responses return direct /files/ URLs, not /q/."""
    client, token = api_client_no_shortlink
    resp = client.post("/v1/quote", json=sample_form, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    files = resp.json()["files"]
    for fmt in ("pdf", "xlsx", "json"):
        url = files[fmt]["url"]
        assert re.fullmatch(r"http://testserver/files/[^/]+/.+", url), url


def test_shortlink_disabled_route_returns_404(api_client_no_shortlink, sample_form):
    """With the module off, even a valid short_id's /q/ route is 404."""
    client, token = api_client_no_shortlink
    # A render (and its short_id) is still created; the redirect route is just gated off.
    client.post("/v1/quote", json=sample_form, headers={"Authorization": f"Bearer {token}"})
    assert client.get("/q/anything").status_code == 404


def test_short_link_oss_resigns_on_access(api_client, sample_form, monkeypatch):
    """In OSS mode the redirect target is freshly signed at access time."""
    client, token = api_client
    resp = client.post("/v1/quote", json=sample_form, headers={"Authorization": f"Bearer {token}"})
    pdf_path = resp.json()["files"]["pdf"]["url"].replace("http://testserver", "")

    # Swap the configured storage to a fake OSS backend for the redirect call.
    from app.storage import OssStorage
    import app.api.shortlink as shortlink_mod

    class FakeOss(OssStorage):
        def __init__(self):
            pass

        def resolve_url(self, object_key):
            from datetime import datetime, timezone
            return f"https://private-resource.shouqianba.com/{object_key}?fresh=1", datetime.now(timezone.utc)

    monkeypatch.setattr(shortlink_mod, "build_storage", lambda _settings: FakeOss())

    redirect = client.get(pdf_path, follow_redirects=False)
    assert redirect.status_code == 302
    loc = redirect.headers["location"]
    assert loc.startswith("https://private-resource.shouqianba.com/")
    assert "fresh=1" in loc
