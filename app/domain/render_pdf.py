#!/usr/bin/env python3
"""
PDF quotation renderer — ported from generate_quotation.py (PDF portion only).

Changes from original:
1. No module-level side effects (font registration is lazy, done in render_pdf())
2. Accepts optional `fonts_dir` parameter for font path resolution
3. Removed sys.path manipulation and pricing_baseline_codec import
4. Returns PDF bytes instead of writing to file
5. build_cost_lookup / load_cost_data / resolve_item_cost / calc_profit removed
"""

import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from pathlib import Path

# ============================================================
# reportlab imports
# ============================================================
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer,
    PageBreak, KeepTogether, Image
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ============================================================
# Module-level font state (set by _register_fonts)
# ============================================================
_CJK_FONT_NAME = 'CJKFont'
_LATIN_FONT_NAME = 'Helvetica'
_CN_FONT_NAME = _CJK_FONT_NAME

_is_complete_font = False
_fonts_registered = False

_SYSTEM_FONT_CANDIDATES = [
    '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
]


def _register_fonts(fonts_dir: Path | None = None):
    """Register CJK fonts lazily. Idempotent."""
    global _CJK_FONT_NAME, _CN_FONT_NAME, _is_complete_font, _fonts_registered

    if _fonts_registered:
        return
    _fonts_registered = True

    candidates = list(_SYSTEM_FONT_CANDIDATES)

    # Prepend fonts_dir candidates if provided
    if fonts_dir is not None:
        fonts_dir = Path(fonts_dir)
        for name in ('NotoSansSC-Regular.otf', 'NotoSansSC-Regular.ttf',
                      'DroidSansFallbackFull.ttf'):
            candidates.insert(0, str(fonts_dir / name))

    cjk_font_path = None
    for fp in candidates:
        if os.path.exists(fp):
            try:
                pdfmetrics.registerFont(TTFont(_CJK_FONT_NAME, fp))
                cjk_font_path = fp
                # Check if font contains Latin digits (complete font)
                from fontTools.ttLib import TTFont as FTFont
                ft = FTFont(fp)
                cmap = ft.getBestCmap()
                _is_complete_font = all(ord(c) in cmap for c in '0123456789')
                ft.close()
                break
            except Exception:
                continue

    if cjk_font_path is None:
        # Fallback to CID font
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        _CJK_FONT_NAME = 'STSong-Light'
        _CN_FONT_NAME = 'STSong-Light'
        _is_complete_font = True
    else:
        _CN_FONT_NAME = _CJK_FONT_NAME


def _mixed_text(text):
    """
    翻转策略：以 CJK 字体作为 Paragraph 默认字体，
    将连续的 ASCII/Latin 字符用 <font name="Helvetica"> 包裹。
    这样所有中文字符（包括标点）自然用 CJK 字体渲染。
    """
    if _is_complete_font:
        return str(text)
    text = str(text)
    if not text:
        return text
    ascii_pattern = re.compile(r'([\x20-\x7e]+)')
    result = ascii_pattern.sub(
        lambda m: f'<font name="{_LATIN_FONT_NAME}">{m.group(0)}</font>',
        text
    )
    return result


# ============================================================
# 颜色定义
# ============================================================
HEADER_BG = colors.HexColor('#FFB300')
HEADER_FG = colors.white
ROW_ALT_BG = colors.HexColor('#FFFBF0')
BORDER_COLOR = colors.HexColor('#d0d5dd')
TOTAL_BG = colors.HexColor('#FFF5D6')
ACCENT = colors.HexColor('#FFB300')

# ============================================================
# Header logos (drawn via canvas callback on every page)
# ============================================================
_LOGO_DIR = Path(__file__).resolve().parents[2] / 'data' / 'logos'
_LOGO_SHOUQIANBA = _LOGO_DIR / 'shouqianba.png'
_LOGO_QUANLAIDIAN = _LOGO_DIR / 'quanlaidian.png'
_LOGO_HEIGHT_MM = 7
_LOGO_GAP_MM = 3
_LOGO_LEFT_MM = 15
_LOGO_TOP_MM = 8

# 水印配置
_WATERMARK_COLOR = colors.HexColor('#AAAAAA')  # 浅灰，可辨认但不抢眼
_WATERMARK_ALPHA = 0.22        # 透明度（0=透明，1=不透明）
_WATERMARK_FONT_SIZE = 14      # 水印字号
_WATERMARK_ANGLE = -30         # 水印倾斜角度（度，顺时针为正）


# ============================================================
# 水印绘制
# ============================================================
def _draw_watermark(canvas, doc):
    """在每一页绘制浅色斜向水印，内容为报价编号+日期。

    水印策略：
    - 中灰 #888888，透明度 0.30，斜 -30°
    - 2×3 网格（共 6 个）均匀覆盖整页，留白处清晰可见
    - 在 onPage 回调中绘制（位于内容下方），表格无显式底色处可透出
    """
    quote_no = getattr(doc, 'title', '') or ''
    date_str = getattr(doc, 'author', '') or ''

    watermark_str = f"{quote_no}  {date_str}".strip()
    if not watermark_str:
        return

    _register_fonts()

    page_width, page_height = A4

    canvas.saveState()
    canvas.setFillColor(_WATERMARK_COLOR)
    canvas.setFillAlpha(_WATERMARK_ALPHA)
    canvas.setFont(_CN_FONT_NAME, _WATERMARK_FONT_SIZE)

    text_width = canvas.stringWidth(watermark_str, _CN_FONT_NAME, _WATERMARK_FONT_SIZE)

    centers = [
        (page_width * x, page_height * y)
        for y in (0.25, 0.55, 0.85)
        for x in (0.5,)
    ]

    for cx, cy in centers:
        canvas.saveState()
        canvas.translate(cx, cy)
        canvas.rotate(_WATERMARK_ANGLE)
        canvas.drawString(-text_width / 2, -_WATERMARK_FONT_SIZE / 2, watermark_str)
        canvas.restoreState()

    canvas.restoreState()


# ============================================================
# 页眉 Logo 绘制（每页回调）
# ============================================================
def _draw_header_logos(canvas, doc):
    """Draw two brand logos in the top-left of every page."""
    from reportlab.lib.utils import ImageReader
    page_width, page_height = A4
    x = _LOGO_LEFT_MM * mm
    target_h = _LOGO_HEIGHT_MM * mm

    for path in (_LOGO_SHOUQIANBA, _LOGO_QUANLAIDIAN):
        if not path.exists():
            continue
        try:
            img = ImageReader(str(path))
            iw, ih = img.getSize()
            target_w = target_h * iw / ih
            y = page_height - _LOGO_TOP_MM * mm - target_h
            canvas.drawImage(
                img, x, y, width=target_w, height=target_h,
                mask='auto', preserveAspectRatio=True,
            )
            x += target_w + _LOGO_GAP_MM * mm
        except Exception:
            pass


# ============================================================
# 样式定义
# ============================================================
def get_styles():
    styles = getSampleStyleSheet()

    styles.add(ParagraphStyle(
        name='CNTitle',
        fontName=_CN_FONT_NAME,
        fontSize=18,
        leading=24,
        alignment=1,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='CNSubtitle',
        fontName=_CN_FONT_NAME,
        fontSize=11,
        leading=15,
        alignment=1,
        textColor=colors.HexColor('#555555'),
        spaceAfter=12,
    ))
    styles.add(ParagraphStyle(
        name='CNNormal',
        fontName=_CN_FONT_NAME,
        fontSize=9,
        leading=13,
    ))
    styles.add(ParagraphStyle(
        name='CNSmall',
        fontName=_CN_FONT_NAME,
        fontSize=8,
        leading=11,
    ))
    styles.add(ParagraphStyle(
        name='CNBold',
        fontName=_CN_FONT_NAME,
        fontSize=9,
        leading=13,
    ))
    styles.add(ParagraphStyle(
        name='CNSection',
        fontName=_CN_FONT_NAME,
        fontSize=12,
        leading=16,
        spaceBefore=12,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name='CNFooter',
        fontName=_CN_FONT_NAME,
        fontSize=8,
        leading=11,
        textColor=colors.HexColor('#888888'),
    ))
    styles.add(ParagraphStyle(
        name='CellStyle',
        fontName=_CN_FONT_NAME,
        fontSize=8,
        leading=11,
        wordWrap='CJK',
    ))
    styles.add(ParagraphStyle(
        name='CellStyleRight',
        fontName=_CN_FONT_NAME,
        fontSize=8,
        leading=11,
        alignment=2,
    ))
    styles.add(ParagraphStyle(
        name='CellStyleCenter',
        fontName=_CN_FONT_NAME,
        fontSize=8,
        leading=11,
        alignment=1,
    ))
    return styles


# ============================================================
# 辅助函数
# ============================================================
def fmt_money(val):
    """格式化金额：带千分位逗号"""
    if val is None or val == '赠送':
        return '赠送'
    try:
        amount = Decimal(str(val)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if amount == amount.to_integral_value():
            return f'{int(amount):,}'
        return f'{amount:,.2f}'
    except:
        return str(val)


def fmt_pct(val):
    """格式化成交价系数百分比"""
    if val is None:
        return '-'
    return f'{val*100:.2f}%'


def get_deal_price_factor(item_or_tier, default=1.0):
    """兼容读取成交价系数，旧字段"折扣"自动转换。"""
    if item_or_tier.get('deal_price_factor') is not None:
        return float(item_or_tier.get('deal_price_factor'))
    if item_or_tier.get('成交价系数') is not None:
        return float(item_or_tier.get('成交价系数'))
    if item_or_tier.get('折扣') is not None:
        return 1 - float(item_or_tier.get('折扣'))
    return float(default)


def calc_actual_price(std_price, deal_price_factor):
    raw = Decimal(str(std_price)) * Decimal(str(deal_price_factor))
    return raw.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _money_decimal(value, default='0.00'):
    if value in (None, '', '赠送'):
        return Decimal(str(default))
    return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def get_item_unit_price(item):
    if item.get('商品单价') not in (None, '', '赠送'):
        return _money_decimal(item.get('商品单价'))
    std_price = item.get('标准价', 0)
    if std_price in ('赠送', None):
        return '赠送'
    return calc_actual_price(std_price, get_deal_price_factor(item))


def get_item_subtotal(item):
    if item.get('报价小计') not in (None, '', '赠送'):
        return _money_decimal(item.get('报价小计'))
    unit_price = get_item_unit_price(item)
    if unit_price == '赠送':
        return '赠送'
    qty = Decimal(str(item.get('数量', 1)))
    return (unit_price * qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def get_item_cost_unit_price(item):
    if item.get('成本单价') not in (None, '', '赠送'):
        return _money_decimal(item.get('成本单价'))
    return None


def get_item_cost_subtotal(item):
    if item.get('成本小计') not in (None, '', '赠送'):
        return _money_decimal(item.get('成本小计'))
    cost_unit_price = get_item_cost_unit_price(item)
    if cost_unit_price is None:
        return None
    qty = Decimal(str(item.get('数量', 1)))
    return (cost_unit_price * qty).quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def get_tier_unit_price(item, tier_factor, tier_store_count=None):
    if item.get('阶梯计价类型') == '配送中心实施费' and tier_store_count is not None:
        from app.domain.pricing import delivery_implementation_price
        return Decimal(str(delivery_implementation_price(int(tier_store_count))))
    if item.get('模块分类') == '门店软件套餐':
        std_price = item.get('标准价', 0)
        if std_price in ('赠送', None):
            return '赠送'
        return calc_actual_price(std_price, tier_factor)
    return get_item_unit_price(item)


def _normalize_profit_group(module_category):
    mapping = {
        '门店软件套餐': '门店套餐',
        '门店增值模块': '门店增值模块',
        '总部模块': '总部模块',
        '实施服务': '实施服务',
    }
    return mapping.get(module_category, module_category)


def number_to_chinese(num):
    """数字转大写中文金额"""
    chinese_digits = '零壹贰叁肆伍陆柒捌玖'
    chinese_units = ['', '拾', '佰', '仟']
    chinese_big_units = ['', '万', '亿']

    if num == 0:
        return '零元整'

    d = Decimal(str(num)).quantize(Decimal('0.00'), rounding=ROUND_HALF_UP)
    int_part = int(d)
    dec_part = int(round((float(d) - int_part) * 100))

    result = ''
    if int_part > 0:
        s = str(int_part)
        n = len(s)
        for i, ch in enumerate(s):
            digit = int(ch)
            pos = n - 1 - i
            big_unit_idx = pos // 4
            unit_idx = pos % 4

            if digit != 0:
                result += chinese_digits[digit] + chinese_units[unit_idx]
                if unit_idx == 0 and big_unit_idx > 0:
                    result += chinese_big_units[big_unit_idx]
            else:
                if unit_idx == 0 and big_unit_idx > 0:
                    result += chinese_big_units[big_unit_idx]
                elif result and not result.endswith('零'):
                    result += '零'

        result = result.rstrip('零')
        result += '元'

    if dec_part == 0:
        result += '整'
    else:
        jiao = dec_part // 10
        fen = dec_part % 10
        if jiao > 0:
            result += chinese_digits[jiao] + '角'
        if fen > 0:
            result += chinese_digits[fen] + '分'

    return result


def gen_quote_number():
    """生成报价编号: QLT-YYYYMMDD-XXX"""
    now = datetime.now()
    return f"QLT-{now.strftime('%Y%m%d')}-001"


# ============================================================
# 标准模板（≤50店）— PDF
# ============================================================
def build_standard_template(data, styles):
    """生成标准单页报价单"""
    story = []

    # === 页眉/标题 ===
    story.append(Paragraph(_mixed_text('"全来店"产品报价单'), styles['CNTitle']))
    story.append(Paragraph(
        _mixed_text('上海收钱吧互联网科技股份有限公司'),
        styles['CNSubtitle']
    ))
    story.append(Spacer(1, 4*mm))

    # === 客户信息 ===
    client = data.get('客户信息', {})
    quote_no = data.get('报价编号', gen_quote_number())
    quote_date = data.get('报价日期', datetime.now().strftime('%Y年%m月%d日'))
    validity = data.get('报价有效期', '30个工作日')

    info_data = [
        [Paragraph(_mixed_text(f'致：{client.get("公司名称", "")}'), styles['CNNormal']),
         Paragraph(_mixed_text(f'报价编号：{quote_no}'), styles['CNNormal'])],
        [Paragraph(_mixed_text(f'联系人：{client.get("联系人", "")}'), styles['CNNormal']),
         Paragraph(_mixed_text(f'报价日期：{quote_date}'), styles['CNNormal'])],
        [Paragraph(_mixed_text(f'地址：{client.get("地址", "")}'), styles['CNNormal']),
         Paragraph(_mixed_text(f'有效期：{validity}'), styles['CNNormal'])],
    ]
    if client.get('电话'):
        info_data.append([
            Paragraph(_mixed_text(f'电话：{client.get("电话", "")}'), styles['CNNormal']),
            Paragraph(_mixed_text(''), styles['CNNormal'])
        ])

    info_table = Table(info_data, colWidths=[95*mm, 75*mm])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('TOPPADDING', (0,0), (-1,-1), 2),
        ('BOTTOMPADDING', (0,0), (-1,-1), 2),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 6*mm))

    # === 报价明细表 ===
    items = data.get('报价项目', [])

    col_widths = [8*mm, 22*mm, 32*mm, 14*mm, 12*mm, 20*mm, 22*mm, 40*mm]
    header = [
        Paragraph(_mixed_text('序号'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('商品分类'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('商品名称'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('单位'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('数量'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('商品单价'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('小计'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('功能说明'), styles['CellStyleCenter']),
    ]

    table_data = [header]
    total = Decimal('0')

    for idx, item in enumerate(items, 1):
        qty = item.get('数量', 1)
        unit_price_d = get_item_unit_price(item)
        subtotal_d = get_item_subtotal(item)

        if unit_price_d == '赠送' or subtotal_d == '赠送':
            unit_price = '赠送'
            subtotal = '赠送'
        else:
            unit_price = float(unit_price_d)
            subtotal = float(subtotal_d)
            total += subtotal_d

        row = [
            Paragraph(_mixed_text(idx), styles['CellStyleCenter']),
            Paragraph(_mixed_text(item.get('商品分类', '')), styles['CellStyle']),
            Paragraph(_mixed_text(item.get('商品名称', '')), styles['CellStyle']),
            Paragraph(_mixed_text(item.get('单位', '')), styles['CellStyleCenter']),
            Paragraph(_mixed_text(qty), styles['CellStyleCenter']),
            Paragraph(_mixed_text(fmt_money(unit_price)), styles['CellStyleRight']),
            Paragraph(_mixed_text(fmt_money(subtotal)), styles['CellStyleRight']),
            Paragraph(_mixed_text(item.get('功能说明', '') or ''), styles['CellStyle']),
        ]
        table_data.append(row)

        # Sub-rows: expand a 门店套餐 into its constituent modules.
        for sub in (item.get('子项') or []):
            table_data.append([
                Paragraph(_mixed_text(''), styles['CellStyleCenter']),
                Paragraph(_mixed_text(sub.get('商品分类', '')), styles['CellStyle']),
                Paragraph(_mixed_text(f"　　{sub.get('商品名称', '')}"), styles['CellStyle']),
                Paragraph(_mixed_text(sub.get('单位', '')), styles['CellStyleCenter']),
                Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                Paragraph(_mixed_text(sub.get('功能说明', '') or ''), styles['CellStyle']),
            ])

    # 合计行
    total_float = float(total)
    total_row = [
        Paragraph(_mixed_text('合计'), styles['CellStyleCenter']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
        Paragraph(_mixed_text(fmt_money(total_float)), styles['CellStyleRight']),
        Paragraph(_mixed_text(''), styles['CellStyle']),
    ]
    table_data.append(total_row)

    t = Table(table_data, colWidths=col_widths, repeatRows=1)

    style_cmds = [
        ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
        ('TEXTCOLOR', (0,0), (-1,0), HEADER_FG),
        ('FONTNAME', (0,0), (-1,-1), _CN_FONT_NAME),
        ('FONTSIZE', (0,0), (-1,-1), 8),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4),
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('LEFTPADDING', (0,0), (-1,-1), 3),
        ('RIGHTPADDING', (0,0), (-1,-1), 3),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('BACKGROUND', (0,-1), (-1,-1), TOTAL_BG),
        ('SPAN', (0,-1), (4,-1)),
    ]

    for i in range(1, len(table_data) - 1):
        if i % 2 == 0:
            style_cmds.append(('BACKGROUND', (0,i), (-1,i), ROW_ALT_BG))

    t.setStyle(TableStyle(style_cmds))
    story.append(t)
    story.append(Spacer(1, 4*mm))

    # === 金额大写 ===
    chinese_total = number_to_chinese(total_float)
    story.append(Paragraph(
        _mixed_text(f'合计金额（大写）：{chinese_total}'),
        styles['CNNormal']
    ))
    story.append(Spacer(1, 6*mm))

    # === 权益类自助充值模块 注释区块 ===
    _append_annotation_blocks(story, data, styles)

    # === 备注条款 ===
    story.append(Paragraph(_mixed_text('备注：'), styles['CNSection']))
    terms = data.get('条款', [
        '以上报价金额均为含税金额，税率为6%；',
        '报价有效期为30个工作日，自报价单生成之日起；',
        '具体折扣金额按签订合同（或销售订单）时具体数量确定价格；',
        '涉及短信、小程序授权、外卖平台接口调用等第三方机构收费部分，需单独计费；',
        '如需要三方代仓对接，需要一事一议。',
    ])
    for i, term in enumerate(terms, 1):
        cn_num = '①②③④⑤⑥⑦⑧⑨⑩'[i-1] if i <= 10 else f'{i}.'
        story.append(Paragraph(_mixed_text(f'{cn_num} {term}'), styles['CNSmall']))

    story.append(Spacer(1, 12*mm))

    # === 页脚公司信息 ===
    story.append(Paragraph(
        _mixed_text('上海收钱吧互联网科技股份有限公司'),
        styles['CNNormal']
    ))
    story.append(Paragraph(
        _mixed_text('地址：上海市闵行区浦江智慧广场陈行公路2168号7号楼'),
        styles['CNFooter']
    ))

    return story


# ============================================================
# 阶梯报价对比页（定制版附页）
# ============================================================
def _build_tiered_section(data, styles):
    """构建阶梯报价对比页，9 列：序号/分类/名称/单位/锚点1单价/锚点1小计/锚点2单价/锚点2小计/说明"""
    tiers = data.get('阶梯配置', [])
    # 规格要求固定 2 档（锚点1/锚点2）。不足 2 档不渲染。
    if len(tiers) < 2:
        return []

    items = data.get('报价项目', [])
    tier_low, tier_high = tiers[0], tiers[1]

    story = [PageBreak()]
    story.append(Paragraph(_mixed_text('阶梯报价参考'), styles['CNSection']))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        _mixed_text('以下为不同门店规模下的费用参考，实际以签订合同为准。'),
        styles['CNSmall']
    ))
    story.append(Spacer(1, 3*mm))

    # 列宽：A=8 B=20 C=30 D=14 E=18 F=20 G=18 H=20 I=32（总 180mm）
    col_widths = [8*mm, 20*mm, 30*mm, 14*mm, 18*mm, 20*mm, 18*mm, 20*mm, 32*mm]

    header = [
        Paragraph(_mixed_text('序号'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('商品分类'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('商品名称'), styles['CellStyleCenter']),
        Paragraph(_mixed_text('单位'), styles['CellStyleCenter']),
        Paragraph(_mixed_text(f"{tier_low['标签']} 单价"), styles['CellStyleCenter']),
        Paragraph(_mixed_text(f"{tier_low['标签']} 小计"), styles['CellStyleCenter']),
        Paragraph(_mixed_text(f"{tier_high['标签']} 单价"), styles['CellStyleCenter']),
        Paragraph(_mixed_text(f"{tier_high['标签']} 小计"), styles['CellStyleCenter']),
        Paragraph(_mixed_text('功能说明'), styles['CellStyleCenter']),
    ]
    table_data = [header]

    cat_order = ['门店软件套餐', '门店增值模块', '总部模块', '实施服务', '售后服务']
    categories = {k: [] for k in cat_order}
    for item in items:
        cat = item.get('模块分类', '门店软件套餐')
        if cat == '硬件设备':
            continue
        if cat in categories:
            categories[cat].append(item)
        else:
            categories['门店软件套餐'].append(item)

    grand_totals = [Decimal('0'), Decimal('0')]
    cat_header_rows = []
    subtotal_rows = []
    NO_TIER_DISCOUNT_CATS = {'实施服务', '售后服务'}
    seq = 0

    for cat_name in cat_order:
        cat_items = categories[cat_name]
        if not cat_items:
            continue

        cat_header_rows.append(len(table_data))
        cat_row = [Paragraph(_mixed_text(cat_name), styles['CNBold'])] + \
                  [Paragraph('', styles['CellStyle'])] * 8
        table_data.append(cat_row)

        cat_subtotals = [Decimal('0'), Decimal('0')]
        apply_tier_discount = cat_name not in NO_TIER_DISCOUNT_CATS

        for item in cat_items:
            seq += 1
            unit = item.get('单位', '')
            item_qty = item.get('数量', 1)
            is_per_store = item.get('数量随门店数', '店' in unit)
            unit_price_item = get_item_unit_price(item)
            is_gift = unit_price_item == '赠送'

            row = [
                Paragraph(_mixed_text(str(seq)), styles['CellStyleCenter']),
                Paragraph(_mixed_text(item.get('商品分类', '')), styles['CellStyle']),
                Paragraph(_mixed_text(item.get('商品名称', '')), styles['CellStyle']),
                Paragraph(_mixed_text(unit), styles['CellStyleCenter']),
            ]
            for tier in (tier_low, tier_high):
                if is_gift:
                    row.append(Paragraph(_mixed_text('赠送'), styles['CellStyleCenter']))
                    row.append(Paragraph(_mixed_text('赠送'), styles['CellStyleCenter']))
                else:
                    d = Decimal(str(get_deal_price_factor(tier))) if apply_tier_discount else Decimal('1')
                    if item.get('阶梯计价类型'):
                        actual_price = get_tier_unit_price(item, d, tier['门店数'])
                    else:
                        actual_price = get_tier_unit_price(item, d, tier['门店数']) if apply_tier_discount else unit_price_item
                    qty = Decimal(str(tier['门店数'])) if is_per_store else Decimal(str(item_qty))
                    subtotal = (actual_price * qty).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                    idx = 0 if tier is tier_low else 1
                    cat_subtotals[idx] += subtotal
                    row.append(Paragraph(_mixed_text(fmt_money(float(actual_price))), styles['CellStyleRight']))
                    row.append(Paragraph(_mixed_text(fmt_money(float(subtotal))), styles['CellStyleRight']))
            row.append(Paragraph(_mixed_text(item.get('功能说明', '') or ''), styles['CellStyle']))
            table_data.append(row)

        # 分类小计行（小计只出现在 F=index 5 和 H=index 7）
        subtotal_rows.append(len(table_data))
        sub_row = [
            Paragraph('', styles['CellStyle']),
            Paragraph(_mixed_text('小计'), styles['CellStyleCenter']),
            Paragraph('', styles['CellStyle']),
            Paragraph('', styles['CellStyle']),
            Paragraph('', styles['CellStyle']),
            Paragraph(_mixed_text(fmt_money(float(cat_subtotals[0]))), styles['CellStyleRight']),
            Paragraph('', styles['CellStyle']),
            Paragraph(_mixed_text(fmt_money(float(cat_subtotals[1]))), styles['CellStyleRight']),
            Paragraph('', styles['CellStyle']),
        ]
        grand_totals[0] += cat_subtotals[0]
        grand_totals[1] += cat_subtotals[1]
        table_data.append(sub_row)

    # 合计行
    total_row = [
        Paragraph('', styles['CellStyle']),
        Paragraph(_mixed_text('合计'), styles['CNBold']),
        Paragraph('', styles['CellStyle']),
        Paragraph('', styles['CellStyle']),
        Paragraph('', styles['CellStyle']),
        Paragraph(_mixed_text(f'¥ {fmt_money(float(grand_totals[0]))}'), styles['CellStyleRight']),
        Paragraph('', styles['CellStyle']),
        Paragraph(_mixed_text(f'¥ {fmt_money(float(grand_totals[1]))}'), styles['CellStyleRight']),
        Paragraph('', styles['CellStyle']),
    ]
    table_data.append(total_row)
    total_row_idx = len(table_data) - 1

    t = Table(table_data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), HEADER_FG),
        ('FONTNAME', (0, 0), (-1, -1), _CN_FONT_NAME),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
        ('LEFTPADDING', (0, 0), (-1, -1), 3),
        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
        ('GRID', (0, 0), (-1, -1), 0.5, BORDER_COLOR),
        ('BACKGROUND', (0, total_row_idx), (-1, total_row_idx), TOTAL_BG),
    ]
    for row_idx in cat_header_rows:
        style_cmds += [
            ('SPAN', (0, row_idx), (-1, row_idx)),
            ('BACKGROUND', (0, row_idx), (-1, row_idx), ROW_ALT_BG),
        ]
    for row_idx in subtotal_rows:
        style_cmds.append(('BACKGROUND', (0, row_idx), (-1, row_idx), TOTAL_BG))

    t.setStyle(TableStyle(style_cmds))
    story.append(t)
    return story


# ============================================================
# 权益类自助充值模块 注释区块
# ============================================================
def _append_annotation_blocks(story, data, styles):
    """Render 附加说明 (e.g. 权益类自助充值模块) as a block of labeled paragraphs."""
    annotations = data.get('附加说明') or []
    for annotation in annotations:
        if not annotation:
            continue
        title = annotation.get('title', '')
        category = annotation.get('category', '')
        heading = f'{title} — {category}' if category else title
        if heading:
            story.append(Paragraph(
                f'<b>{_mixed_text(heading)}</b>',
                styles['CNSection'],
            ))
        for line in annotation.get('text_lines') or []:
            story.append(Paragraph(
                _mixed_text(f'* {line}'),
                styles['CNSmall'],
            ))
        story.append(Spacer(1, 4*mm))


# ============================================================
# 定制多页模板（>50店）— PDF
# ============================================================
def build_custom_template(data, styles):
    """生成定制多页报价单"""
    story = []

    # === 第1页：封面 ===
    story.append(Spacer(1, 15*mm))
    story.append(Paragraph(_mixed_text('"全来店"产品报价方案'), styles['CNTitle']))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        _mixed_text('上海收钱吧互联网科技股份有限公司'),
        styles['CNSubtitle']
    ))
    story.append(Spacer(1, 10*mm))

    client = data.get('客户信息', {})
    quote_no = data.get('报价编号', gen_quote_number())
    quote_date = data.get('报价日期', datetime.now().strftime('%Y年%m月%d日'))

    cover_info = [
        ['客户名称', client.get('公司名称', '')],
        ['联系人', client.get('联系人', '')],
        ['联系电话', client.get('电话', '')],
        ['报价编号', quote_no],
        ['报价日期', quote_date],
        ['有效期', data.get('报价有效期', '30个工作日')],
    ]

    cover_data = [[Paragraph(_mixed_text(r[0]), styles['CNBold']),
                    Paragraph(_mixed_text(r[1]), styles['CNNormal'])] for r in cover_info]

    cover_table = Table(cover_data, colWidths=[40*mm, 100*mm])
    cover_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), _CN_FONT_NAME),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('BACKGROUND', (0,0), (0,-1), ROW_ALT_BG),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 8*mm))

    # === 分类汇总各模块 ===
    items = data.get('报价项目', [])

    categories = {
        '门店软件套餐': [],
        '门店增值模块': [],
        '总部模块': [],
        '实施服务': [],
        '售后服务': [],
    }

    for item in items:
        cat = item.get('模块分类', '门店软件套餐')
        if cat in categories:
            categories[cat].append(item)
        elif cat != '硬件设备':
            categories['门店软件套餐'].append(item)

    grand_total = Decimal('0')
    first_cat = True

    for cat_name, cat_items in categories.items():
        if not cat_items:
            continue

        if first_cat:
            story.append(PageBreak())
            first_cat = False
        else:
            story.append(Spacer(1, 8*mm))
        story.append(Paragraph(_mixed_text(cat_name), styles['CNSection']))
        story.append(Spacer(1, 3*mm))

        col_widths = [8*mm, 22*mm, 32*mm, 14*mm, 12*mm, 20*mm, 22*mm, 42*mm]
        header = [
            Paragraph(_mixed_text('序号'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('商品分类'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('商品名称'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('单位'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('数量'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('商品单价'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('小计'), styles['CellStyleCenter']),
            Paragraph(_mixed_text('功能说明'), styles['CellStyleCenter']),
        ]

        table_data = [header]
        cat_total = Decimal('0')

        for idx, item in enumerate(cat_items, 1):
            qty = item.get('数量', 1)
            unit_price_d = get_item_unit_price(item)
            subtotal_d = get_item_subtotal(item)

            if unit_price_d == '赠送' or subtotal_d == '赠送':
                unit_price = '赠送'
                subtotal = '赠送'
            else:
                unit_price = float(unit_price_d)
                subtotal = float(subtotal_d)
                cat_total += subtotal_d

            row = [
                Paragraph(_mixed_text(idx), styles['CellStyleCenter']),
                Paragraph(_mixed_text(item.get('商品分类', '')), styles['CellStyle']),
                Paragraph(_mixed_text(item.get('商品名称', '')), styles['CellStyle']),
                Paragraph(_mixed_text(item.get('单位', '')), styles['CellStyleCenter']),
                Paragraph(_mixed_text(qty), styles['CellStyleCenter']),
                Paragraph(_mixed_text(fmt_money(unit_price)), styles['CellStyleRight']),
                Paragraph(_mixed_text(fmt_money(subtotal)), styles['CellStyleRight']),
                Paragraph(_mixed_text(item.get('功能说明', '') or ''), styles['CellStyle']),
            ]
            table_data.append(row)

            # Sub-rows for 门店套餐 expansion.
            for sub in (item.get('子项') or []):
                table_data.append([
                    Paragraph(_mixed_text(''), styles['CellStyleCenter']),
                    Paragraph(_mixed_text(sub.get('商品分类', '')), styles['CellStyle']),
                    Paragraph(_mixed_text(f"　　{sub.get('商品名称', '')}"), styles['CellStyle']),
                    Paragraph(_mixed_text(sub.get('单位', '')), styles['CellStyleCenter']),
                    Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                    Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                    Paragraph(_mixed_text('-'), styles['CellStyleCenter']),
                    Paragraph(_mixed_text(sub.get('功能说明', '') or ''), styles['CellStyle']),
                ])

        # 小计行
        cat_total_float = float(cat_total)
        grand_total += cat_total

        subtotal_row = [
            Paragraph(_mixed_text(''), styles['CellStyle']),
            Paragraph(_mixed_text(''), styles['CellStyle']),
            Paragraph(_mixed_text(''), styles['CellStyle']),
            Paragraph(_mixed_text(''), styles['CellStyle']),
            Paragraph(_mixed_text('小计'), styles['CellStyleCenter']),
            Paragraph(_mixed_text(fmt_money(cat_total_float)), styles['CellStyleRight']),
            Paragraph(_mixed_text(''), styles['CellStyle']),
            Paragraph(_mixed_text(''), styles['CellStyle']),
        ]
        table_data.append(subtotal_row)

        t = Table(table_data, colWidths=col_widths, repeatRows=1)
        style_cmds = [
            ('BACKGROUND', (0,0), (-1,0), HEADER_BG),
            ('TEXTCOLOR', (0,0), (-1,0), HEADER_FG),
            ('FONTNAME', (0,0), (-1,-1), _CN_FONT_NAME),
            ('FONTSIZE', (0,0), (-1,-1), 8),
            ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
            ('TOPPADDING', (0,0), (-1,-1), 4),
            ('BOTTOMPADDING', (0,0), (-1,-1), 4),
            ('LEFTPADDING', (0,0), (-1,-1), 3),
            ('RIGHTPADDING', (0,0), (-1,-1), 3),
            ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
            ('BACKGROUND', (0,-1), (-1,-1), TOTAL_BG),
            ('SPAN', (0,-1), (4,-1)),
        ]
        for i in range(1, len(table_data) - 1):
            if i % 2 == 0:
                style_cmds.append(('BACKGROUND', (0,i), (-1,i), ROW_ALT_BG))
        t.setStyle(TableStyle(style_cmds))
        story.append(t)

    # === 最后一页：总计 + 条款 ===
    story.append(PageBreak())
    story.append(Paragraph(_mixed_text('费用汇总'), styles['CNSection']))
    story.append(Spacer(1, 4*mm))

    grand_total_float = float(grand_total)
    chinese_total = number_to_chinese(grand_total_float)

    summary_data = [
        [Paragraph(_mixed_text('项目总计（含税）'), styles['CNBold']),
         Paragraph(_mixed_text(f'¥ {fmt_money(grand_total_float)}'), styles['CellStyleRight'])],
        [Paragraph(_mixed_text('大写金额'), styles['CNBold']),
         Paragraph(_mixed_text(chinese_total), styles['CNNormal'])],
    ]
    summary_table = Table(summary_data, colWidths=[50*mm, 100*mm])
    summary_table.setStyle(TableStyle([
        ('FONTNAME', (0,0), (-1,-1), _CN_FONT_NAME),
        ('GRID', (0,0), (-1,-1), 0.5, BORDER_COLOR),
        ('BACKGROUND', (0,0), (0,-1), ROW_ALT_BG),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 6),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 6*mm))

    # === 权益类自助充值模块 注释区块 ===
    _append_annotation_blocks(story, data, styles)

    # 条款
    story.append(Paragraph(_mixed_text('备注与条款：'), styles['CNSection']))
    terms = data.get('条款', [
        '以上报价金额均为含税金额，税率为6%；',
        '报价有效期为30个工作日，自报价单生成之日起；',
        '具体折扣金额按签订合同（或销售订单）时具体数量确定价格；',
        '涉及短信、小程序授权、外卖平台接口调用等第三方机构收费部分，需单独计费；',
        '如需要三方代仓对接，需要一事一议。',
    ])
    for i, term in enumerate(terms, 1):
        cn_num = '①②③④⑤⑥⑦⑧⑨⑩'[i-1] if i <= 10 else f'{i}.'
        story.append(Paragraph(_mixed_text(f'{cn_num} {term}'), styles['CNSmall']))

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph(
        _mixed_text('上海收钱吧互联网科技股份有限公司'),
        styles['CNNormal']
    ))
    story.append(Paragraph(
        _mixed_text('地址：上海市闵行区浦江智慧广场陈行公路2168号7号楼'),
        styles['CNFooter']
    ))

    # 阶梯报价对比页（如有配置）
    story.extend(_build_tiered_section(data, styles))

    return story


# ============================================================
# Public API
# ============================================================
def render_pdf(config: dict, fonts_dir: Path | None = None) -> bytes:
    """Generate PDF quotation from config dict. Returns PDF bytes.

    Parameters
    ----------
    config : dict
        The quotation configuration dict (output of build_quotation_config).
        Keys include 客户信息, 报价日期, 报价项目, pricing_info, 条款,
        阶梯配置 (optional), etc.
    fonts_dir : Path | None
        Optional directory to search for CJK font files before system paths.

    Returns
    -------
    bytes
        The generated PDF content.
    """
    _register_fonts(fonts_dir)

    styles = get_styles()

    # Determine template based on route_strategy
    pricing_info = config.get('pricing_info', {})
    route_strategy = pricing_info.get('route_strategy', '')

    # "small-segment" uses standard template; others use custom template
    # Original logic: standard for <=50 stores, custom for >50
    # The route_strategy "small-segment" corresponds to <=30 stores (standard)
    if route_strategy == 'small-segment':
        story = build_standard_template(config, styles)
    else:
        story = build_custom_template(config, styles)

    buf = BytesIO()

    # 透传水印内容（通过 doc.title / doc.author 传递给 canvas 回调）
    quote_no = config.get('报价编号', gen_quote_number())
    quote_date = config.get('报价日期', datetime.now().strftime('%Y-%m-%d'))

    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=15*mm,
        rightMargin=15*mm,
        topMargin=18*mm,
        bottomMargin=15*mm,
        title=quote_no,
        author=quote_date,
    )

    def on_first_page(canvas, doc):
        _draw_header_logos(canvas, doc)
        _draw_watermark(canvas, doc)

    def on_later_pages(canvas, doc):
        _draw_header_logos(canvas, doc)
        _draw_watermark(canvas, doc)

    doc.build(story, onFirstPage=on_first_page, onLaterPages=on_later_pages)
    return buf.getvalue()
