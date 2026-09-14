from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, field_validator


class QuoteForm(BaseModel):
    """Input: quotation request form from OpenClaw"""
    客户品牌名称: str = Field(min_length=1)
    餐饮业态: str = Field(min_length=1)
    餐饮类型: str  # "轻餐" or "正餐"
    门店数量: int = Field(ge=1, le=300)
    所需功能描述: str = Field(min_length=1)
    门店套餐: str
    门店增值模块: list[str] = Field(default_factory=list)
    税号数量: int = Field(default=1, ge=1)
    总部模块: list[str] = Field(default_factory=list)
    配送中心数量: int = Field(default=0, ge=0)
    生产加工中心数量: int = Field(default=0, ge=0)
    企业微信SCRM语鹦版数量: int = Field(default=1, ge=1)
    POS交付方式: str = "远程交付"
    是否购买VIP售后服务: bool = False
    成交价系数: Optional[float] = Field(default=None, ge=0.01, le=1.0)
    人工改价原因: Optional[str] = None
    是否启用阶梯报价: bool = False
    实施服务类型: Optional[str] = None
    实施服务人天: int = Field(default=0, ge=0)

    @field_validator("实施服务类型", "人工改价原因", mode="before")
    @classmethod
    def normalize_empty_string_to_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @field_validator("POS交付方式")
    @classmethod
    def validate_pos_delivery_mode(cls, v: str) -> str:
        allowed = {"远程交付", "现场交付", "正餐大酒楼现场交付"}
        if v not in allowed:
            raise ValueError(f"POS交付方式必须是: {', '.join(sorted(allowed))}")
        return v

    @field_validator("客户品牌名称", "餐饮业态", "所需功能描述")
    @classmethod
    def validate_required_text(cls, v: str) -> str:
        value = v.strip()
        if not value:
            raise ValueError("该字段不能为空")
        return value


class QuoteItemPreview(BaseModel):
    """Single line item in quote preview"""
    name: str
    qty: int
    list: int | float       # list price
    final: int | float      # discounted price


class QuoteTotals(BaseModel):
    list: int | float
    final: int | float


class QuotePreview(BaseModel):
    brand: str
    meal_type: str
    stores: int
    package: str
    discount: float
    totals: QuoteTotals
    items: list[QuoteItemPreview]


class FileRef(BaseModel):
    url: str
    filename: str
    expires_at: datetime


class QuoteResponse(BaseModel):
    request_id: str
    preview: QuotePreview
    files: dict[str, FileRef]
    pricing_version: str
    pricing_info: dict = Field(default_factory=dict)


class ErrorDetail(BaseModel):
    code: str
    field: Optional[str] = None
    message: str
    hint: Optional[str] = None
    request_id: str


class ErrorResponse(BaseModel):
    error: ErrorDetail
