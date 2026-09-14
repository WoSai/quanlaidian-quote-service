import unittest
from pathlib import Path

from app.domain.pricing import build_quotation_config

CATALOG = Path(__file__).resolve().parent.parent / "references" / "product_catalog_v2.json"


def form(**overrides):
    payload = {
        "客户品牌名称": "阶段二矩阵",
        "餐饮业态": "咖啡",
        "餐饮类型": "轻餐",
        "门店数量": 1,
        "所需功能描述": "点餐收银",
        "门店套餐": "轻餐连锁标准版",
        "门店增值模块": [],
        "总部模块": [],
    }
    payload.update(overrides)
    return payload


def items_by_name(config):
    return {item["商品名称"]: item for item in config["报价项目"]}


class Stage2MatrixTests(unittest.TestCase):
    def test_invoice_and_reservation_pairing_rules(self):
        config = build_quotation_config(
            form(
                餐饮类型="正餐",
                餐饮业态="酒楼",
                门店套餐="正餐连锁标准版",
                门店增值模块=["电子发票税号数-票通", "电子发票接口"],
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        names = items_by_name(config)
        self.assertIn("电子发票税号数-票通", names)
        self.assertIn("电子发票接口", names)

        invalid_cases = [
            (
                ["宴秘书标准版", "宴秘书接口"],
                "无需重复选择宴秘书接口",
            ),
            (
                ["宴秘书标准版", "预订管理"],
                "二者不能重复选择",
            ),
        ]
        for addons, message in invalid_cases:
            with (
                self.subTest(addons=addons),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_quotation_config(
                    form(
                        餐饮类型="正餐",
                        餐饮业态="酒楼",
                        门店套餐="正餐连锁标准版",
                        门店增值模块=addons,
                    ),
                    {"items": []},
                    CATALOG,
                    descriptions={},
                )

    def test_pos_delivery_matrix(self):
        cases = [
            ("轻餐", "轻餐连锁标准版", "远程交付", "轻餐版-远程交付", 300),
            ("轻餐", "轻餐连锁标准版", "现场交付", "轻餐版-现场交付", 500),
            ("正餐", "正餐连锁标准版", "远程交付", "正餐版-远程交付", 600),
            ("正餐", "正餐连锁标准版", "现场交付", "正餐版-现场交付", 1000),
            (
                "正餐",
                "正餐连锁标准版",
                "正餐大酒楼现场交付",
                "正餐大酒楼-现场交付",
                3000,
            ),
        ]
        for meal, package, mode, service, unit_price in cases:
            with self.subTest(mode=mode):
                config = build_quotation_config(
                    form(
                        餐饮类型=meal,
                        餐饮业态="火锅" if meal == "正餐" else "咖啡",
                        门店套餐=package,
                        门店数量=3,
                        POS交付方式=mode,
                    ),
                    {"items": []},
                    CATALOG,
                    descriptions={},
                )
                item = items_by_name(config)[service]
                self.assertEqual(item["商品单价"], unit_price)
                self.assertEqual(item["数量"], 3)
                self.assertEqual(item["报价小计"], unit_price * 3)
                self.assertEqual(item["成交价系数"], 1.0)

    def test_delivery_center_fee_boundaries(self):
        cases = [
            (1, 5000),
            (10, 5000),
            (11, 10000),
            (50, 10000),
            (51, 10000),  # 主报价按本阶梯的 50 店展示
            (99, 10000),  # 主报价仍按 50 店，99 店费用在阶梯列展示
            (100, 20000),
            (300, 20000),  # 主报价按本阶梯的 200 店展示
        ]
        for stores, expected in cases:
            with self.subTest(stores=stores):
                config = build_quotation_config(
                    form(
                        门店数量=stores,
                        门店套餐="轻餐连锁供应链版",
                        总部模块=["连锁供应链-配送中心-轻/正餐"],
                        配送中心数量=1,
                    ),
                    {"items": []},
                    CATALOG,
                    descriptions={},
                )
                item = items_by_name(config)["供应链-配送中心交付"]
                self.assertEqual(item["商品单价"], expected)
                self.assertEqual(item["报价小计"], expected)
                self.assertEqual(item["成交价系数"], 1.0)
                self.assertIn("300个门店以上30,000元", item["标准报价原文"])

    def test_production_and_scrm_quantities_drive_implementation(self):
        config = build_quotation_config(
            form(
                门店套餐="轻餐连锁供应链版",
                总部模块=[
                    "连锁供应链-配送中心-轻/正餐",
                    "生产加工-轻/正餐",
                    "企微微信SCRM-语鹦版",
                ],
                配送中心数量=1,
                生产加工中心数量=4,
                企业微信SCRM语鹦版数量=3,
            ),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        items = items_by_name(config)
        self.assertEqual(items["供应链-生产加工交付"]["数量"], 4)
        self.assertEqual(items["供应链-生产加工交付"]["报价小计"], 40000)
        self.assertEqual(items["企业微信实施费"]["数量"], 3)
        self.assertEqual(items["企业微信实施费"]["报价小计"], 3000)

    def test_vip_and_rights_do_not_receive_package_discount(self):
        config = build_quotation_config(
            form(门店数量=50, 是否购买VIP售后服务=True),
            {"items": []},
            CATALOG,
            descriptions={},
        )
        items = items_by_name(config)
        self.assertEqual(items["Vip售后服务"]["商品单价"], 100)
        self.assertEqual(items["Vip售后服务"]["数量"], 50)
        self.assertEqual(items["Vip售后服务"]["成交价系数"], 1.0)
        self.assertNotIn("权益账户", items)
        rights = config["附加说明"][0]
        self.assertEqual(rights["title"], "权益账户")
        rights_text = "\n".join(rights["text_lines"])
        self.assertIn("方式一：外卖接单费", rights_text)
        self.assertIn("方式二：外卖包年费", rights_text)
        self.assertIn("收钱吧掌柜端——采购商城", rights_text)
        quoted_total = sum(item["报价小计"] for item in config["报价项目"])
        self.assertEqual(config["internal_financials"]["quote_total"], quoted_total)

    def test_invalid_stage2_combinations_are_rejected(self):
        cases = [
            (
                "生产加工缺配送中心",
                form(
                    门店套餐="轻餐连锁供应链版",
                    总部模块=["生产加工-轻/正餐"],
                    生产加工中心数量=1,
                ),
                "必须同时选择",
            ),
            (
                "门店库存与配送中心互斥",
                form(
                    门店套餐="轻餐门店库存版",
                    总部模块=["连锁供应链-配送中心-轻/正餐"],
                    配送中心数量=1,
                ),
                "互斥",
            ),
            (
                "轻餐不可选正餐大酒楼交付",
                form(POS交付方式="正餐大酒楼现场交付"),
                "仅适用于正餐",
            ),
        ]
        for scenario, payload, message in cases:
            with (
                self.subTest(scenario=scenario),
                self.assertRaisesRegex(ValueError, message),
            ):
                build_quotation_config(
                    payload,
                    {"items": []},
                    CATALOG,
                    descriptions={},
                )


if __name__ == "__main__":
    unittest.main()
