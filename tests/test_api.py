from app.timezone import today_east8


def test_healthz(api_client):
    client, _ = api_client
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "pricing_version" in body
    assert "service_version" in body
    assert body["service_version"] != "unknown", (
        "VERSION file unreachable from app.version.service_version()"
    )

def test_quote_200_returns_files(api_client, sample_form):
    client, token = api_client
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "request_id" in body
    assert "preview" in body
    assert "files" in body
    assert "pdf" in body["files"]
    assert "xlsx" in body["files"]
    assert "json" in body["files"]
    # Check preview fields
    assert body["preview"]["brand"] == "集成测试品牌"
    assert body["preview"]["stores"] == 5
    assert body["pricing_version"] is not None

def test_quote_400_factor_without_reason(api_client, sample_form):
    """Regression for issue #1: providing 成交价系数 without 人工改价原因
    must return 400 OUT_OF_RANGE, not 500 INTERNAL_ERROR."""
    client, token = api_client
    sample_form["成交价系数"] = 0.25
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400, f"Expected 400, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["error"]["code"] == "OUT_OF_RANGE"
    assert body["error"]["field"] == "人工改价原因"

def test_quote_manual_factor_no_approval_gate(api_client, sample_form):
    """Manual 成交价系数 (even 0.25 for new client) must NOT trigger approval.

    As of 2026-04-20, approval gating was removed by business decision — the
    system outputs files directly regardless of the factor. Legacy /v1/quote
    must return 200 with files, never 409 APPROVAL_PENDING.
    """
    client, token = api_client
    sample_form["成交价系数"] = 0.25
    sample_form["人工改价原因"] = "总部战略客户"
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["files"].keys()) == {"pdf", "xlsx", "json"}


def test_quote_401_missing_token(api_client, sample_form):
    client, _ = api_client
    resp = client.post("/v1/quote", json=sample_form)
    assert resp.status_code == 401

def test_quote_401_wrong_token(api_client, sample_form):
    client, _ = api_client
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": "Bearer wrong-token-xyz"},
    )
    assert resp.status_code == 401

def test_quote_422_invalid_form(api_client):
    client, token = api_client
    # Missing required fields
    resp = client.post(
        "/v1/quote",
        json={"客户品牌名称": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "INVALID_FORM"

def test_quote_301_stores_rejected_at_schema_layer(api_client, sample_form):
    """301+ stores must be rejected.

    Two-layer defense: Pydantic schema (le=300, returns 422) is the outer
    guard; pricing layer's OutOfRangeError (returns 400) is an inner guard
    covered by tests/test_pricing.py::test_301_stores_rejected. If the
    schema constraint ever drops, this test switches to 400 and the
    pricing layer catches the request — either way 301 stores never succeeds.
    """
    client, token = api_client
    sample_form["门店数量"] = 301
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "INVALID_FORM"


def test_quote_31_stores_now_goes_large_segment(api_client, sample_form):
    """31 店曾被拒,现在走大客户段阶梯报价(不再 422)。"""
    client, token = api_client
    sample_form["门店数量"] = 31
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    # schema 放行。具体 200/400 取决于是否触发其他校验失败;这里只验证不是 422
    assert resp.status_code != 422


def test_quote_56_stores_effective_store_count_is_50(api_client, sample_form):
    """56 店请求 → 主报价按下锚点 50 店生成(effective_store_count=50)。"""
    client, token = api_client
    sample_form["门店数量"] = 56
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # preview 主报价应该按 50 店算(下锚点 tier_window[0])
    assert body["preview"]["stores"] == 50


def test_quote_200_stores_effective_is_200(api_client, sample_form):
    """锚点精确命中:200 店 → effective=200(tier_window=[200,300])。"""
    client, token = api_client
    sample_form["门店数量"] = 200
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["preview"]["stores"] == 200

def test_quote_persists_to_db(api_client, sample_form, test_data_root):
    """POST /v1/quote must write a row to quote + 3 renders + approval row."""
    import sqlite3
    client, token = api_client
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    db = sqlite3.connect(str(test_data_root / "quote.db"))
    try:
        quotes = db.execute("SELECT id, org, total_final FROM quote").fetchall()
        assert len(quotes) == 1
        quote_id = quotes[0][0]
        assert quotes[0][1] == "test-org"

        renders = db.execute(
            "SELECT format FROM quote_render WHERE quote_id=?", (quote_id,)
        ).fetchall()
        assert sorted(r[0] for r in renders) == ["json", "pdf", "xlsx"]

        approvals = db.execute(
            "SELECT state FROM approval WHERE quote_id=?", (quote_id,)
        ).fetchall()
        assert len(approvals) == 1
        assert approvals[0][0] == "not_required"
    finally:
        db.close()


def test_quote_audit_log_includes_token_id(api_client, sample_form, test_data_root):
    """Audit records must identify which token made each request."""
    import json as _json
    client, token = api_client
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200

    today = today_east8()
    audit_file = test_data_root / "audit" / f"{today}.jsonl"
    assert audit_file.exists(), f"audit file missing: {audit_file}"
    lines = [line for line in audit_file.read_text().splitlines() if line.strip()]
    assert lines, "audit log is empty"
    record = _json.loads(lines[-1])
    assert "token_id" in record
    assert record["token_id"].startswith("tok_")
    assert record["org"] == "test-org"


def test_quote_same_form_is_idempotent(api_client, sample_form, test_data_root):
    """Two identical POSTs must produce one DB row (idempotency)."""
    import sqlite3
    client, token = api_client
    for _ in range(2):
        resp = client.post(
            "/v1/quote",
            json=sample_form,
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 200
    db = sqlite3.connect(str(test_data_root / "quote.db"))
    try:
        count = db.execute("SELECT count(*) FROM quote").fetchone()[0]
        assert count == 1
    finally:
        db.close()


def test_quote_pricing_version_small_segment(api_client, sample_form):
    """Top-level pricing_version must reflect the route actually used.

    Small-segment route (≤30 stores) → "catalog-v2-stage2".
    """
    client, token = api_client
    sample_form["门店数量"] = 20
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pricing_version"] == "catalog-v2-stage2"


def test_quote_pricing_version_large_segment(api_client, sample_form):
    """36-store request routes through large-segment and the response's
    pricing_version must say so — not the small-segment baseline marker.

    Regression for the bug where /v1/quote always returned the global
    PRICING_VERSION constant regardless of actual route_strategy.
    """
    client, token = api_client
    sample_form["门店数量"] = 36
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["pricing_version"] == "catalog-v2-tiered-stage2"


def test_quote_200_29_stores(api_client, sample_form):
    client, token = api_client
    sample_form["门店数量"] = 29
    resp = client.post(
        "/v1/quote",
        json=sample_form,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["preview"]["stores"] == 29
