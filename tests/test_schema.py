import pytest
from pydantic import ValidationError
from app.domain.schema import QuoteForm, QuoteResponse

# Test happy path — deserialize example form
def test_example_form_deserializes():
    data = {
        "客户品牌名称": "黑马汇",
        "餐饮业态": "咖啡",
        "餐饮类型": "轻餐",
        "门店数量": 20,
        "所需功能描述": "点餐收银、会员、小程序",
        "门店套餐": "轻餐连锁营销基础版",
        "门店增值模块": ["厨房KDS"],
        "总部模块": [],
        "成交价系数": 0.25,
        "是否启用阶梯报价": False,
        "实施服务类型": "",
        "实施服务人天": 0
    }
    form = QuoteForm(**data)
    assert form.客户品牌名称 == "黑马汇"
    assert form.门店数量 == 20
    assert form.餐饮类型 == "轻餐"

def test_stores_301_rejected():
    """边界搬:31+ 现在走大客户段阶梯,只有 301+ 被 schema 拒绝。"""
    with pytest.raises(ValidationError):
        QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=301, 所需功能描述="点餐", 门店套餐="Y")


def test_stores_31_accepted_now_large_segment():
    """31 店之前被拒,现在进入大客户段(tier 报价)。"""
    form = QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=31, 所需功能描述="点餐", 门店套餐="Y")
    assert form.门店数量 == 31


def test_stores_300_accepted():
    """边界:300 店是大客户段末端,必须通过。"""
    form = QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=300, 所需功能描述="点餐", 门店套餐="Y")
    assert form.门店数量 == 300

def test_stores_0_rejected():
    with pytest.raises(ValidationError):
        QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=0, 所需功能描述="点餐", 门店套餐="Y")

def test_discount_too_high_rejected():
    with pytest.raises(ValidationError):
        QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=5, 所需功能描述="点餐", 门店套餐="Y", 成交价系数=1.5)

def test_discount_too_low_rejected():
    with pytest.raises(ValidationError):
        QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=5, 所需功能描述="点餐", 门店套餐="Y", 成交价系数=0.005)

def test_missing_brand_rejected():
    with pytest.raises(ValidationError):
        QuoteForm(餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=5, 所需功能描述="点餐", 门店套餐="Y")

@pytest.mark.parametrize("field", ["客户品牌名称", "餐饮业态", "所需功能描述"])
def test_required_text_rejects_blank_values(field):
    payload = {
        "客户品牌名称": "测试品牌",
        "餐饮业态": "咖啡",
        "餐饮类型": "轻餐",
        "门店数量": 1,
        "所需功能描述": "点餐收银",
        "门店套餐": "轻餐连锁标准版",
    }
    payload[field] = "   "
    with pytest.raises(ValidationError):
        QuoteForm(**payload)

def test_stores_30_accepted():
    """Boundary: 30 stores should be valid"""
    form = QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=30, 所需功能描述="点餐", 门店套餐="Y")
    assert form.门店数量 == 30

def test_stores_1_accepted():
    """Boundary: 1 store should be valid"""
    form = QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=1, 所需功能描述="点餐", 门店套餐="Y")
    assert form.门店数量 == 1

def test_optional_fields_default():
    """Optional fields should have sensible defaults"""
    form = QuoteForm(客户品牌名称="X", 餐饮业态="咖啡", 餐饮类型="轻餐", 门店数量=5, 所需功能描述="点餐", 门店套餐="Y")
    assert form.门店增值模块 == []
    assert form.总部模块 == []
    assert form.是否启用阶梯报价 is False
    assert form.实施服务人天 == 0
