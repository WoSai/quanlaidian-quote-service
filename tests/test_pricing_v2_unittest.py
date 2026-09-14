import json
import unittest
from pathlib import Path

from app.domain.pricing import (
    _compute_quote_unit_price,
    build_quotation_config,
    build_tier_config,
    load_product_catalog,
    recommend_base_deal_price_factor_smooth,
    resolve_product_pricing,
    resolve_tier_window,
)

CATALOG = Path(__file__).resolve().parent.parent / "references" / "product_catalog_v2.json"


def form(**overrides):
    payload = {
        "客户品牌名称": "测试品牌",
        "餐饮业态": "咖啡",
        "餐饮类型": "轻餐",
        "门店数量": 1,
        "所需功能描述": "点餐收银、会员、小程序",
        "门店套餐": "轻餐连锁标准版",
        "门店增值模块": [],
        "总部模块": [],
    }
    payload.update(overrides)
    return payload


class ProductCatalogV2Tests(unittest.TestCase):
    def test_loads_latest_catalog_and_display_category_override(self):
        products = load_product_catalog(CATALOG)
        self.assertEqual(len(products), 112)
        by_name = {item["name"]: item for item in products}
        self.assertEqual(by_name["轻餐连锁标准版"]["price"], 1050)
        self.assertEqual(by_name["轻餐连锁标准版"]["group"], "门店套餐")
        self.assertEqual(by_name["财务通配送中心"]["group"], "总部模块")

    def test_latest_customer_quote_is_standard_price(self):
        product = next(
            item for item in load_product_catalog(CATALOG)
            if item["name"] == "轻餐连锁标准版"
        )
        standard, _cost, source = resolve_product_pricing(product, "轻餐", {"exact": {}, "by_name": {}})
        self.assertEqual(standard, 1050)
        self.assertEqual(source, "latest_catalog_v2")

    def test_latest_bundle_category_counts_and_unique_names(self):
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["category_counts"],
            {
                "门店标准套餐": 35,
                "门店增值模块": 54,
                "总部增值模块": 11,
                "实施服务": 10,
                "售后服务": 1,
                "权益类": 1,
            },
        )
        names = [item["name"] for item in payload["products"]]
        self.assertEqual(len(names), len(set(names)))

    def test_all_35_latest_packages_generate_one_store_quotes(self):
        payload = json.loads(CATALOG.read_text(encoding="utf-8"))
        packages = [
            item for item in payload["products"]
            if item["category"] == "门店标准套餐"
        ]
        for product in packages:
            with self.subTest(package=product["name"]):
                has_supply_chain = (
                    "供应链基础-门店点位" in product["description"]
                    or "连锁供应链门店" in product["description"]
                )
                config = build_quotation_config(
                    form(
                        餐饮业态="火锅"
                        if product["applicable_meal_type"] == "正餐"
                        else "咖啡",
                        餐饮类型=product["applicable_meal_type"],
                        门店套餐=product["name"],
                        总部模块=["连锁供应链-配送中心-轻/正餐"]
                        if has_supply_chain
                        else [],
                        配送中心数量=1 if has_supply_chain else 0,
                    ),
                    {"items": []},
                    CATALOG,
                    descriptions={},
                )
                quoted = config["报价项目"][0]
                self.assertEqual(quoted["商品名称"], product["name"])
                self.assertEqual(quoted["标准价"], product["customer_quote"])
                self.assertEqual(quoted["商品单价"], product["customer_quote"])


class QuoteWindowV2Tests(unittest.TestCase):
    def test_quote_windows_and_factors(self):
        expected = {
            31: ([31, 49], [0.95, 0.90]),
            49: ([31, 49], [0.95, 0.90]),
            50: ([50, 99], [0.95, 0.90]),
            99: ([50, 99], [0.95, 0.90]),
            100: ([100, 199], [0.95, 0.90]),
            199: ([100, 199], [0.95, 0.90]),
            200: ([200, 300], [0.95, 0.90]),
            300: ([200, 300], [0.95, 0.90]),
        }
        for requested, (counts, factors) in expected.items():
            self.assertEqual(resolve_tier_window(requested), counts)
            tiers = build_tier_config(False, "轻餐", requested)
            self.assertEqual([tier["门店数"] for tier in tiers], counts)
            self.assertEqual([tier["成交价系数"] for tier in tiers], factors)

    def test_one_to_thirty_use_factor_one(self):
        for stores in (1, 10, 30):
            self.assertEqual(recommend_base_deal_price_factor_smooth(stores, "轻餐"), 1.0)
            self.assertEqual(recommend_base_deal_price_factor_smooth(stores, "正餐"), 1.0)

    def test_discount_only_applies_to_package(self):
        self.assertEqual(_compute_quote_unit_price("门店软件套餐", 1050, 500, 0.95, False), 997.5)
        self.assertEqual(_compute_quote_unit_price("门店增值模块", 200, 50, 0.95, False), 200)
        self.assertEqual(_compute_quote_unit_price("总部模块", 48000, 20000, 0.90, False), 48000)

    def test_31_store_quote_uses_latest_price_and_only_discounts_package(self):
        config = build_quotation_config(
            form(
                门店数量=31,
                门店增值模块=["厨房KDS-轻餐"],
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        items = {item["商品名称"]: item for item in config["报价项目"]}
        self.assertEqual(items["轻餐连锁标准版"]["标准价"], 1050)
        self.assertEqual(items["轻餐连锁标准版"]["商品单价"], 997.5)
        self.assertEqual(items["厨房KDS-轻餐"]["标准价"], 200)
        self.assertEqual(items["厨房KDS-轻餐"]["商品单价"], 200)
        self.assertEqual(config["阶梯配置"][1]["门店数"], 49)
        self.assertEqual(config["阶梯配置"][1]["成交价系数"], 0.90)

    def test_invoice_piaotong_requires_interface(self):
        with self.assertRaisesRegex(ValueError, "必须同时选择电子发票接口"):
            build_quotation_config(
                form(门店增值模块=["电子发票税号数-票通"]),
                {"items": []},
                CATALOG,
                descriptions={},
            )

    def test_fractional_catalog_price_is_not_rounded_to_zero(self):
        config = build_quotation_config(
            form(门店数量=5, 门店增值模块=["宴秘书接口"]),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        interface = next(item for item in config["报价项目"] if item["商品名称"] == "宴秘书接口")
        self.assertEqual(interface["标准价"], 0.01)
        self.assertEqual(interface["商品单价"], 0.01)
        self.assertEqual(interface["报价小计"], 0.05)

    def test_supply_chain_package_requires_delivery_center(self):
        with self.assertRaisesRegex(ValueError, "必须搭配"):
            build_quotation_config(
                form(门店套餐="轻餐连锁供应链版"),
                {"items": []},
                CATALOG,
                descriptions={},
            )

    def test_manual_factor_cannot_override_confirmed_catalog_factor(self):
        config = build_quotation_config(
            form(门店数量=50, 成交价系数=0.50, 人工改价原因="测试"),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        self.assertEqual(config["pricing_info"]["final_factor"], 0.95)

    def test_default_pos_delivery_and_rights_fixed_display(self):
        config = build_quotation_config(form(), {"items": []}, CATALOG, descriptions={})
        items = {item["商品名称"]: item for item in config["报价项目"]}
        self.assertNotIn("权益账户", items)
        self.assertEqual(items["轻餐版-远程交付"]["商品单价"], 300)
        self.assertEqual(items["轻餐版-远程交付"]["数量"], 1)
        rights_text = "\n".join(config["附加说明"][0]["text_lines"])
        source_rights = next(
            item["price"] for item in load_product_catalog(CATALOG)
            if item["name"] == "权益账户"
        )
        self.assertEqual(rights_text, source_rights)
        self.assertIn("方式一：外卖接单费", rights_text)
        self.assertIn("方式二：外卖包年费", rights_text)
        self.assertIn("收钱吧掌柜端——采购商城", rights_text)

    def test_onsite_pos_delivery_uses_catalog_price_and_keeps_travel_note(self):
        config = build_quotation_config(
            form(
                餐饮业态="火锅",
                餐饮类型="正餐",
                门店套餐="正餐连锁标准版",
                POS交付方式="现场交付",
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        onsite = next(item for item in config["报价项目"] if item["商品名称"] == "正餐版-现场交付")
        self.assertEqual(onsite["商品单价"], 1000)
        self.assertIn("差旅费", onsite["标准报价原文"])

    def test_vip_after_sales_charges_one_hundred_per_store(self):
        config = build_quotation_config(
            form(门店数量=50, 是否购买VIP售后服务=True),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        vip = next(item for item in config["报价项目"] if item["商品名称"] == "Vip售后服务")
        self.assertEqual(vip["商品单价"], 100)
        self.assertEqual(vip["数量"], 50)
        self.assertEqual(vip["报价小计"], 5000)

    def test_delivery_center_implementation_uses_store_bands(self):
        config = build_quotation_config(
            form(
                门店数量=50,
                门店套餐="轻餐连锁供应链版",
                总部模块=["连锁供应链-配送中心-轻/正餐"],
                配送中心数量=1,
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        delivery = next(item for item in config["报价项目"] if item["商品名称"] == "供应链-配送中心交付")
        self.assertEqual(delivery["商品单价"], 10000)
        self.assertIn("1-10个门店5,000元", delivery["标准报价原文"])
        self.assertEqual(delivery["阶梯计价类型"], "配送中心实施费")

    def test_production_and_wechat_add_corresponding_implementation(self):
        config = build_quotation_config(
            form(
                门店套餐="轻餐连锁供应链版",
                总部模块=[
                    "连锁供应链-配送中心-轻/正餐",
                    "生产加工-轻/正餐",
                    "企微微信SCRM-语鹦版",
                ],
                配送中心数量=1,
                生产加工中心数量=2,
                企业微信SCRM语鹦版数量=3,
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        items = {item["商品名称"]: item for item in config["报价项目"]}
        self.assertEqual(items["供应链-生产加工交付"]["数量"], 2)
        self.assertEqual(items["供应链-生产加工交付"]["报价小计"], 20000)
        self.assertEqual(items["企业微信实施费"]["数量"], 3)
        self.assertEqual(items["企业微信实施费"]["报价小计"], 3000)


if __name__ == "__main__":
    unittest.main()
