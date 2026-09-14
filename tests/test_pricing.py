import json
import pytest
from pathlib import Path
from app.domain.pricing import build_quotation_config, load_product_catalog
from app.domain.pricing_baseline import (
    KEY_ENV,
    STRICT_ENV,
    decode_payload,
    encode_payload,
    load_baseline,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PRODUCT_CATALOG = Path(__file__).resolve().parent.parent / "references" / "product_catalog.md"


@pytest.fixture
def empty_baseline():
    return {"items": []}


def _load_form(name):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_light_meal_5_stores(empty_baseline):
    form = _load_form("form_light_meal_5_stores.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    assert config["餐饮类型"] == "轻餐"
    assert config["门店数量"] == 5
    assert config["客户信息"]["公司名称"] == "测试轻餐5店"
    assert len(config["报价项目"]) >= 1  # at least the package
    assert config["pricing_info"]["route_strategy"] == "small-segment"


def test_full_meal_10_stores(empty_baseline):
    form = _load_form("form_full_meal_10_stores.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    assert config["餐饮类型"] == "正餐"
    assert config["门店数量"] == 10
    assert len(config["报价项目"]) >= 2  # package + KDS module


def test_with_delivery_center(empty_baseline):
    form = _load_form("form_with_delivery_center.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    # Should have package + delivery center module
    item_names = [item["商品名称"] for item in config["报价项目"]]
    assert "配送中心" in item_names


def test_hq_module_brand_program_default_qty_one(empty_baseline):
    form = _load_form("form_full_meal_with_brand_program.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    brand_items = [
        item for item in config["报价项目"]
        if item["商品名称"] == "商家小程序号-品牌点位"
    ]
    assert len(brand_items) == 1
    assert brand_items[0]["数量"] == 1
    assert brand_items[0]["模块分类"] == "总部模块"


def test_hq_module_scrm_default_qty_one(empty_baseline):
    form = _load_form("form_light_meal_with_scrm.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    scrm_items = [
        item for item in config["报价项目"]
        if item["商品名称"] == "企业微信SCRM"
    ]
    assert len(scrm_items) == 1
    assert scrm_items[0]["数量"] == 1
    assert scrm_items[0]["模块分类"] == "总部模块"


def test_hq_module_unsupported_still_rejects(empty_baseline):
    form = _load_form("form_full_meal_with_brand_program.json")
    form["总部模块"] = ["不存在的模块"]
    with pytest.raises(ValueError, match="总部模块不支持"):
        build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)


def test_hq_module_delivery_center_still_requires_quantity(empty_baseline):
    form = _load_form("form_with_delivery_center.json")
    del form["配送中心数量"]
    with pytest.raises(ValueError, match="配送中心数量"):
        build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)


def test_301_stores_rejected(empty_baseline):
    """边界搬:31+ 现在走大客户段(tier 对比),只有 301+ 拒绝。"""
    form = _load_form("form_light_meal_5_stores.json")
    form["门店数量"] = 301
    from app.errors import OutOfRangeError
    with pytest.raises((OutOfRangeError, ValueError)):
        build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)


def test_large_segment_150_stores_pricing_info(empty_baseline):
    """150 店走大客户段: pricing_info 标记 route=large-segment, effective=100(tier 下锚)。"""
    form = _load_form("form_light_meal_5_stores.json")
    form["门店数量"] = 150
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    pi = config["pricing_info"]
    assert pi["route_strategy"] == "large-segment"
    assert pi["algorithm_version"] == "catalog-v2-tiered-stage2"
    assert pi["original_requested_store_count"] == 150
    assert pi["effective_store_count"] == 100


def test_config_structure(empty_baseline):
    """Verify the output config has all expected top-level keys"""
    form = _load_form("form_light_meal_5_stores.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    assert "客户信息" in config
    assert "报价日期" in config
    assert "报价项目" in config
    assert "pricing_info" in config
    assert "条款" in config
    assert "internal_financials" in config


def test_load_baseline_missing_file_raises(tmp_path):
    """Missing baseline file must fail loudly, not silently fall back to empty items.

    Silent fallback previously caused fallback pricing (cost_price = catalog list),
    which combined with the cost-plus markup produced quote unit prices ABOVE list.
    """
    missing = tmp_path / "does_not_exist.json"
    with pytest.raises(FileNotFoundError):
        load_baseline(missing)


def _write_obf(path: Path, items, secret_key: str):
    plain = json.dumps({"items": items}, ensure_ascii=False)
    payload = encode_payload(plain, secret_key)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_encode_decode_roundtrip():
    original = '{"items": [{"name": "foo", "cost_price": 123.45}]}'
    payload = encode_payload(original, "test-key")
    assert payload["format"] == "pricing-baseline-obf-v1"
    assert decode_payload(payload, "test-key") == original


def test_load_baseline_decodes_obf(tmp_path, monkeypatch):
    obf = tmp_path / "baseline.obf"
    items = [{"meal_type": "正餐", "group": "总部模块", "name": "配送中心", "cost_price": 20000}]
    _write_obf(obf, items, "real-key")

    monkeypatch.setenv(KEY_ENV, "real-key")
    baseline = load_baseline(json_path=tmp_path / "missing.json", obf_path=obf)
    assert baseline["items"] == items


def test_load_baseline_strict_requires_key(tmp_path, monkeypatch):
    obf = tmp_path / "baseline.obf"
    _write_obf(obf, [], "real-key")
    monkeypatch.setenv(STRICT_ENV, "1")
    with pytest.raises(RuntimeError, match=KEY_ENV):
        load_baseline(json_path=tmp_path / "missing.json", obf_path=obf)


def test_load_baseline_strict_requires_obf_file(tmp_path, monkeypatch):
    monkeypatch.setenv(STRICT_ENV, "1")
    monkeypatch.setenv(KEY_ENV, "real-key")
    with pytest.raises(RuntimeError, match="混淆基线"):
        load_baseline(json_path=tmp_path / "missing.json", obf_path=tmp_path / "missing.obf")


def test_load_baseline_prefers_obf_over_plaintext(tmp_path, monkeypatch):
    obf = tmp_path / "baseline.obf"
    items = [{"name": "from_obf", "cost_price": 1}]
    _write_obf(obf, items, "real-key")

    plaintext = tmp_path / "baseline.json"
    plaintext.write_text(json.dumps({"items": [{"name": "from_plaintext", "cost_price": 2}]}), encoding="utf-8")

    monkeypatch.setenv(KEY_ENV, "real-key")
    baseline = load_baseline(json_path=plaintext, obf_path=obf)
    assert baseline["items"][0]["name"] == "from_obf"


def test_load_baseline_falls_back_to_plaintext_when_no_key(tmp_path):
    obf = tmp_path / "baseline.obf"
    _write_obf(obf, [{"name": "from_obf"}], "real-key")

    plaintext = tmp_path / "baseline.json"
    plaintext.write_text(json.dumps({"items": [{"name": "from_plaintext"}]}), encoding="utf-8")

    baseline = load_baseline(json_path=plaintext, obf_path=obf)
    assert baseline["items"][0]["name"] == "from_plaintext"


# ============================================================================
# Large-segment (31-300 stores) tests — independent of product_catalog fixture.
# ============================================================================


class TestConfirmedTierFactors:
    """All lower anchors use 0.95 and all upper anchors use 0.90."""

    def test_factor_light_50(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(50, "轻餐") == 0.95

    def test_factor_full_50(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(50, "正餐") == 0.95

    def test_factor_light_100(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(100, "轻餐") == 0.95

    def test_factor_full_100(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(100, "正餐") == 0.95

    def test_factor_light_200(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(200, "轻餐") == 0.95

    def test_factor_full_200(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(200, "正餐") == 0.95

    def test_factor_light_300(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(300, "轻餐") == 0.90

    def test_factor_full_300(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(300, "正餐") == 0.90


class TestSmallSegmentRegression:
    """The confirmed 1-30 factor is 1.00 for both meal types."""

    def test_factor_1_store_full(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(1, "正餐") == 1.0

    def test_factor_1_store_light(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(1, "轻餐") == 1.0

    def test_factor_20_stores_full(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        assert recommend_base_deal_price_factor_smooth(20, "正餐") == 1.0

    def test_factor_30_stores_full(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth, round_factor
        assert round_factor(recommend_base_deal_price_factor_smooth(30, "正餐")) == 1.0

    def test_factor_30_stores_light(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth, round_factor
        assert round_factor(recommend_base_deal_price_factor_smooth(30, "轻餐")) == 1.0


class TestLargeSegmentNonAnchorRejected:
    """31-300 non-anchor values must not be fed into factor function — factor is only
    defined at anchors. Large-segment code path picks an anchor via resolve_tier_window."""

    def test_31_stores_raises(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        with pytest.raises(ValueError):
            recommend_base_deal_price_factor_smooth(31, "正餐")

    def test_56_stores_raises(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        with pytest.raises(ValueError):
            recommend_base_deal_price_factor_smooth(56, "正餐")

    def test_150_stores_raises(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        with pytest.raises(ValueError):
            recommend_base_deal_price_factor_smooth(150, "轻餐")

    def test_301_stores_raises_out_of_range(self):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        from app.errors import OutOfRangeError
        with pytest.raises(OutOfRangeError):
            recommend_base_deal_price_factor_smooth(301, "正餐")


class TestResolveTierWindow:
    """resolve_tier_window(n) — picks the [lower, upper] anchor pair covering n."""

    def test_30_stores_rejected(self):
        from app.domain.pricing import resolve_tier_window
        with pytest.raises(ValueError):
            resolve_tier_window(30)

    def test_31_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(31) == [31, 49]

    def test_50_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(50) == [50, 99]

    def test_56_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(56) == [50, 99]

    def test_99_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(99) == [50, 99]

    def test_100_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(100) == [100, 199]

    def test_150_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(150) == [100, 199]

    def test_199_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(199) == [100, 199]

    def test_200_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(200) == [200, 300]

    def test_250_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(250) == [200, 300]

    def test_300_stores(self):
        from app.domain.pricing import resolve_tier_window
        assert resolve_tier_window(300) == [200, 300]

    def test_301_stores_raises(self):
        from app.domain.pricing import resolve_tier_window
        from app.errors import OutOfRangeError
        with pytest.raises(OutOfRangeError):
            resolve_tier_window(301)

    def test_29_stores_raises(self):
        """< 30 不应该走 tier window(属于 small-segment 单点)"""
        from app.domain.pricing import resolve_tier_window
        with pytest.raises(ValueError):
            resolve_tier_window(29)


class TestBuildTierConfigLargeSegment:
    """build_tier_config with store_count >=31 generates 2-tier comparison,
    ignoring the enabled flag."""

    def test_56_stores_gives_50_99_tiers_full(self):
        from app.domain.pricing import build_tier_config
        tiers = build_tier_config(False, "正餐", 56)  # enabled=False, but >=31 forces tiers
        assert [t["门店数"] for t in tiers] == [50, 99]
        assert tiers[0]["成交价系数"] == 0.95
        assert tiers[1]["成交价系数"] == 0.90

    def test_56_stores_gives_50_99_tiers_light(self):
        from app.domain.pricing import build_tier_config
        tiers = build_tier_config(False, "轻餐", 56)
        assert [t["门店数"] for t in tiers] == [50, 99]
        assert tiers[0]["成交价系数"] == 0.95
        assert tiers[1]["成交价系数"] == 0.90

    def test_250_stores_gives_200_300_tiers(self):
        from app.domain.pricing import build_tier_config
        tiers = build_tier_config(False, "正餐", 250)
        assert [t["门店数"] for t in tiers] == [200, 300]

    def test_small_segment_enabled_still_10_20_30(self):
        """≤30 + enabled=True: 保留现状 [10, 20, 30]"""
        from app.domain.pricing import build_tier_config
        tiers = build_tier_config(True, "正餐", 15)
        assert [t["门店数"] for t in tiers] == [10, 20, 30]

    def test_small_segment_disabled_returns_empty(self):
        """≤30 + enabled=False: 保留现状 no tiers"""
        from app.domain.pricing import build_tier_config
        assert build_tier_config(False, "正餐", 15) == []


class TestFactorMonotonic:
    """已确认系数在各区间保持一致，不做连续插值。"""

    @pytest.mark.parametrize("meal_type", ["轻餐", "正餐"])
    def test_small_segment_is_one(self, meal_type):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        factors = [recommend_base_deal_price_factor_smooth(n, meal_type) for n in range(1, 31)]
        assert factors == [1.0] * 30

    @pytest.mark.parametrize("meal_type", ["轻餐", "正餐"])
    def test_confirmed_anchor_factors(self, meal_type):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        anchors = [31, 49, 50, 99, 100, 199, 200, 300]
        factors = [recommend_base_deal_price_factor_smooth(n, meal_type) for n in anchors]
        assert factors == [0.95, 0.90, 0.95, 0.90, 0.95, 0.90, 0.95, 0.90]

    @pytest.mark.parametrize("meal_type", ["轻餐", "正餐"])
    def test_30_to_31_uses_confirmed_step(self, meal_type):
        from app.domain.pricing import recommend_base_deal_price_factor_smooth
        f30 = recommend_base_deal_price_factor_smooth(30, meal_type)
        f31 = recommend_base_deal_price_factor_smooth(31, meal_type)
        assert (f30, f31) == (1.0, 0.95)


def test_module_unit_price_follows_markup_rule(empty_baseline):
    """门店增值模块 / 总部模块 商品单价 = 底价 × 固定倍数（毛利保护）.

    - 增值模块: cost × 1.20
    - 总部模块: cost × 1.50

    定价故意让商品单价可以高于"标准价"（= cost × 1.10 / 1.20），深度折扣只
    吸收在套餐上；增值/总部走成本加成。resolve_product_pricing 的 catalog
    fallback 分支会让 cost=目录标价，此时商品单价达到 120-150% of list，
    属于"基线漏收商品"的告警信号，不是算法 bug。
    """
    form = _load_form("form_with_delivery_center.json")
    config = build_quotation_config(form, empty_baseline, PRODUCT_CATALOG)
    expected_markup = {"门店增值模块": 1.20, "总部模块": 1.50}
    for item in config["报价项目"]:
        cat = item["模块分类"]
        if cat not in expected_markup or item.get("protected_item_bypass"):
            continue
        # 这里是 fallback 分支 (cost=目录价)：unit = 目录价 × markup
        expected = item["成本单价"] * expected_markup[cat]
        assert abs(item["商品单价"] - expected) < 0.01, (
            f"{item['商品名称']} ({cat}): unit {item['商品单价']} != "
            f"cost {item['成本单价']} × {expected_markup[cat]}"
        )


# ============================================================
# 功能说明 & 附加说明 (new feature)
# ============================================================
REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_CATALOG = REPO_ROOT / "references" / "product_catalog.md"
LOCAL_DESCRIPTIONS = REPO_ROOT / "references" / "product_descriptions.json"


def _load_descriptions():
    from app.domain.product_descriptions import load_descriptions
    return load_descriptions(LOCAL_DESCRIPTIONS)


def test_build_quote_item_includes_description_field():
    from app.domain.pricing import build_quote_item
    product = {"name": "X", "unit": "店/年", "meal_type": "轻餐", "group": "门店增值模块"}
    item = build_quote_item(product, 100, 50, 1, 1.0, "增值模块", "门店增值模块", description="hello")
    assert item["功能说明"] == "hello"


def test_build_quote_item_description_defaults_to_empty():
    from app.domain.pricing import build_quote_item
    product = {"name": "X", "unit": "店/年", "meal_type": "轻餐", "group": "门店增值模块"}
    item = build_quote_item(product, 100, 50, 1, 1.0, "增值模块", "门店增值模块")
    assert item["功能说明"] == ""


def test_build_quotation_config_attaches_descriptions(empty_baseline):
    form = _load_form("form_light_meal_5_stores.json")
    desc = _load_descriptions()
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
    # Every line item should carry a 功能说明 key (possibly empty, but present).
    for item in config["报价项目"]:
        assert "功能说明" in item
    # The package row has sub-rows (子项), so its own 功能说明 is cleared and
    # each sub-row carries the individual module description instead.
    first = config["报价项目"][0]
    if first["子项"]:
        assert first["功能说明"] == ""
        for sub in first["子项"]:
            assert sub.get("功能说明"), f"sub-row {sub.get('商品名称')} missing 功能说明"
    else:
        assert first["功能说明"], f"expected non-empty 功能说明 for {first['商品名称']}"


def test_build_quotation_config_expands_package_into_sub_items(empty_baseline):
    form = _load_form("form_light_meal_5_stores.json")
    desc = _load_descriptions()
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
    package_item = config["报价项目"][0]
    assert package_item["模块分类"] == "门店软件套餐"
    assert len(package_item["子项"]) >= 3  # 轻餐标准/基础版 includes 3~4 modules
    names = {sub["商品名称"] for sub in package_item["子项"]}
    assert any("点餐收银" in n or "商户中心" in n for n in names)


def test_build_quotation_config_attaches_annotation(empty_baseline):
    form = _load_form("form_light_meal_5_stores.json")
    desc = _load_descriptions()
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
    assert "附加说明" in config
    blocks = config["附加说明"]
    assert any(b and b.get("title") == "权益类自助充值模块" for b in blocks)
    block = next(b for b in blocks if b.get("title") == "权益类自助充值模块")
    joined = "\n".join(block["text_lines"])
    assert "0.039" in joined and "0.032" in joined and "0.021" in joined


def test_build_quotation_config_without_descriptions_skips_annotation(empty_baseline):
    form = _load_form("form_light_meal_5_stores.json")
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG)
    assert "附加说明" not in config
    for item in config["报价项目"]:
        assert item["功能说明"] == ""
        assert item["子项"] == []


# ============================================================
# 套餐子项 vs 门店增值模块 冲突校验
# ============================================================
def test_mutually_exclusive_supply_chain_with_flagship_rejected(empty_baseline):
    """ZC-04 旗舰版自带"单门店库存"，再勾选"供应链基础-门店点位"应被拒绝，
    且文案应引导改选同价位「正餐连锁营销全能版」+ 增值模块。"""
    form = _load_form("form_full_meal_10_stores.json")
    desc = _load_descriptions()
    form["门店增值模块"] = ["供应链基础-门店点位"]
    with pytest.raises(ValueError, match="改选「正餐连锁营销全能版」"):
        build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)


def test_addon_duplicating_package_sub_rejected(empty_baseline):
    """ZC-05 供应链版自带"供应链基础-门店点位"，再次作为增值模块勾选应被拒。"""
    form = _load_form("form_full_meal_10_stores.json")
    desc = _load_descriptions()
    form["门店套餐"] = "正餐连锁供应链版"
    form["门店增值模块"] = ["供应链基础-门店点位"]
    with pytest.raises(ValueError, match="重复购买"):
        build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)


def test_non_conflicting_addon_still_works(empty_baseline):
    """ZC-04 旗舰版 + 厨房KDS（无冲突）应正常出价。"""
    form = _load_form("form_full_meal_10_stores.json")
    desc = _load_descriptions()
    form["门店增值模块"] = ["厨房KDS"]
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
    item_names = [item["商品名称"] for item in config["报价项目"]]
    assert "厨房KDS" in item_names


# ============================================================
# 总部模块 → 门店端依赖校验
# ============================================================
def test_delivery_center_requires_supply_chain_store_module(empty_baseline):
    """配送中心必须配「供应链基础-门店点位」。ZC-04 旗舰版自带「单门店库存」，
    单纯加配送中心而门店端没有供应链门店点位时应被拒，且文案应引导改选全能版。"""
    form = _load_form("form_with_delivery_center.json")
    form["门店套餐"] = "正餐连锁营销旗舰版"
    form["餐饮类型"] = "正餐"
    form["门店增值模块"] = []
    desc = _load_descriptions()
    with pytest.raises(ValueError, match="改选「正餐连锁营销全能版」"):
        build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)


def test_delivery_center_satisfied_by_package_sub(empty_baseline):
    """ZC-05 供应链版自带「供应链基础-门店点位」，配送中心依赖被套餐满足。"""
    form = _load_form("form_with_delivery_center.json")
    form["门店套餐"] = "正餐连锁供应链版"
    form["餐饮类型"] = "正餐"
    form["门店增值模块"] = []
    desc = _load_descriptions()
    config = build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
    item_names = [item["商品名称"] for item in config["报价项目"]]
    assert "配送中心" in item_names


def test_qc04_flagship_mutex_with_supply_chain_addon_rejected(empty_baseline):
    """轻餐 QC-04 旗舰版 + 增值模块「供应链基础-门店点位」应报错并指引改选全能版。"""
    form = _load_form("form_light_meal_5_stores.json")
    form["门店套餐"] = "轻餐连锁营销旗舰版"
    form["门店增值模块"] = ["供应链基础-门店点位"]
    desc = _load_descriptions()
    with pytest.raises(ValueError, match="改选「轻餐连锁营销全能版」"):
        build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)


def test_qc04_flagship_with_delivery_center_rejected(empty_baseline):
    """轻餐 QC-04 旗舰版 + 配送中心（无增值模块）应报错并指引改选全能版。"""
    form = _load_form("form_with_delivery_center.json")
    form["餐饮类型"] = "轻餐"
    form["门店套餐"] = "轻餐连锁营销旗舰版"
    form["门店增值模块"] = []
    desc = _load_descriptions()
    with pytest.raises(ValueError, match="改选「轻餐连锁营销全能版」"):
        build_quotation_config(form, empty_baseline, LOCAL_CATALOG, descriptions=desc)
