"""凭单渲染：金额大写、docx 生成、预览上下文。

docx 以目录下的《支 出 凭 单.docx》为版式母版，直接替换文字，保证输出与母版一致：
标题「支 出 凭 单」（楷体 18 磅加粗）→ 日期与编号行（楷体 12 磅）→
带边框的正文表格（楷体 14 磅）→ 页脚「财务总监 记账 出纳 审核 制单」。
"""

from __future__ import annotations

import shutil
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import copy

from docx import Document
from docx.enum.text import WD_UNDERLINE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "output"
CN_NUM = "零壹贰叁肆伍陆柒捌玖"
CN_UNIT = ["", "拾", "佰", "仟"]
CN_SECTION = ["", "万", "亿", "万亿"]
_PAY_PREFIX = "即    付 "


# ---------------------------------------------------------------- 金额大写

def _four_digits(num: int) -> str:
    """0-9999 转大写，内部零压缩。"""
    text = ""
    pending_zero = False
    for exp, unit in ((3, "仟"), (2, "佰"), (1, "拾"), (0, "")):
        digit = num // 10**exp % 10
        if digit == 0:
            pending_zero = bool(text)
        else:
            if pending_zero:
                text += "零"
                pending_zero = False
            text += CN_NUM[digit] + unit
    return text


def _integer_to_cn(num: int) -> str:
    if num == 0:
        return "零"
    sections = []
    while num > 0:
        sections.append(num % 10000)
        num //= 10000
    text = ""
    for index in range(len(sections) - 1, -1, -1):
        section = sections[index]
        if section == 0:
            if text and not text.endswith("零"):
                text += "零"
            continue
        if text and section < 1000 and not text.endswith("零"):
            text += "零"
        text += _four_digits(section) + CN_SECTION[index]
    return text.rstrip("零") or "零"


def amount_to_chinese(amount) -> str:
    """10512.5 -> 壹万零伍佰壹拾贰元伍角"""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value < 0:
        raise ValueError("金额不能为负")
    integer = int(value)
    fraction = int((value - integer) * 100)
    jiao, fen = divmod(fraction, 10)

    text = _integer_to_cn(integer) + "元"
    if jiao == 0 and fen == 0:
        return text + "整"
    if jiao:
        text += CN_NUM[jiao] + "角"
    elif fen:
        text += "零"
    if fen:
        text += CN_NUM[fen] + "分"
    return text


def format_amount(amount) -> str:
    """去掉多余的 0：10512.50 -> 10512.5"""
    value = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------- 文案

def _as_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    return date.today()


def build_context(record: dict) -> dict:
    """把一条凭单记录转换为模板渲染所需字段。"""
    name = (record.get("name") or "").strip()
    identity = (record.get("identity") or "").strip()
    amount = record.get("amount")
    amount_str = format_amount(amount)
    voucher_date = _as_date(record.get("voucher_date"))

    body = (
        "申请支付"
        + identity
        + name
        + (record.get("period_month") or "").strip()
        + (record.get("fee_type") or "").strip()
        + (record.get("purpose") or "").strip()
        + amount_str
        + "元"
    )
    line1 = "即    付 " + body
    if record.get("bank_account"):
        line1 += f"   收款账号：{record['bank_account']}"
    if record.get("bank_name"):
        line1 += f" 开户行：{record['bank_name']}"
    line1 += f" 收款人：{name}"

    serial = record.get("serial_no")
    return {
        "date_text": f"{voucher_date.year}年{voucher_date.month}月{voucher_date.day}日",
        "serial_no": serial if serial is not None else "",
        "line1": line1,
        "line1_body": line1[len(_PAY_PREFIX):] if line1.startswith(_PAY_PREFIX) else line1,
        "amount_str": amount_str,
        "amount_upper": amount_to_chinese(amount),
        "name": name,
    }


def output_filename(record: dict) -> str:
    voucher_date = _as_date(record.get("voucher_date"))
    serial = record.get("serial_no") or 0
    name = (record.get("name") or "未命名").strip()
    return f"支付凭单_{int(serial):03d}_{name}_{voucher_date:%Y%m%d}.docx"


# ---------------------------------------------------------------- docx

def template_path() -> Path:
    """定位母版文件（文件名含不间断空格，故按内容匹配）。"""
    for path in BASE_DIR.iterdir():
        if path.suffix.lower() == ".docx" and not path.name.startswith("~$"):
            if "凭" in path.name and "单" in path.name:
                return path
    raise FileNotFoundError("未找到凭单母版 docx 文件")


STYLE_PLAIN = "plain"        # 无下划线
STYLE_DOTTED = "dotted"      # 虚线（点式下划线）
STYLE_UNDERLINE = "underline"  # 单实线下划线


def _style_tag(run) -> str:
    underline = run.font.underline
    if underline == WD_UNDERLINE.DOTTED:
        return STYLE_DOTTED
    if underline:
        return STYLE_UNDERLINE
    return STYLE_PLAIN


def _capture_styles(paragraph) -> dict:
    """记录段落中各类格式的 rPr，供重建 run 时按样式复用。"""
    styles: dict = {}
    for run in paragraph.runs:
        tag = _style_tag(run)
        if tag not in styles:
            rpr = run._element.rPr
            styles[tag] = copy.deepcopy(rpr) if rpr is not None else None
    return styles


def _trailing_space_runs(paragraph) -> list[tuple[str, str]]:
    """取段落末尾的连续空白 run 及其样式，重建时原样保留（划线延伸）。"""
    tail: list[tuple[str, str]] = []
    for run in reversed(paragraph.runs):
        if run.text and run.text.strip() == "":
            tail.append((run.text, _style_tag(run)))
        else:
            break
    tail.reverse()
    return tail


def _set_runs(paragraph, segments: list[tuple], size_pt: float) -> None:
    """清空段落并按 (文字, 字体[, 样式]) 重建 run。

    样式取自母版原段落的同类 run（plain/dotted/underline），从而保留下划线、
    虚线等格式；若段落中不存在该样式则退化为普通格式。
    """
    styles = _capture_styles(paragraph)
    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)
    for item in segments:
        text, font_name = item[0], item[1]
        tag = item[2] if len(item) > 2 else STYLE_PLAIN
        if tag not in styles:
            tag = next(iter(styles), STYLE_PLAIN)
        run = paragraph.add_run(text)
        rpr = styles.get(tag)
        if rpr is not None:
            old = run._element.rPr
            if old is not None:
                run._element.remove(old)
            run._element.insert(0, copy.deepcopy(rpr))
        run.font.size = Pt(size_pt)
        run.font.name = font_name
        own = run._element.get_or_add_rPr()
        fonts = own.get_or_add_rFonts()
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            fonts.set(qn(attr), font_name)


def _fill_voucher(paragraphs, tables, context) -> None:
    """把一张凭单的文字填进一组段落与表格（预览与 docx 的唯一填充来源）。"""
    # 日期与编号行
    for para in paragraphs:
        if "年" in para.text and "月" in para.text and "第" in para.text:
            leading = " " * 18
            tail = "\xa0" * 4 + " " * 7
            serial = context["serial_no"]
            _set_runs(
                para,
                [(leading + context["date_text"] + tail + "第", "楷体"),
                 (f" {serial} ", "宋体"),
                 ("号", "楷体")],
                12,
            )
            break

    # 正文表格（母版为 1x1 单元格，内含 7 个段落）
    if not tables:
        raise ValueError("母版缺少正文表格")
    cell = tables[0].cell(0, 0)
    cell_paragraphs = cell.paragraphs
    if len(cell_paragraphs) < 7:
        raise ValueError("母版正文段落结构异常")

    # 即付行：前缀与正文均为虚线格式，母版尾部的划线空白原样保留
    _set_runs(
        cell_paragraphs[0],
        [(_PAY_PREFIX, "楷体"), (context["line1_body"], "楷体", STYLE_DOTTED)]
        + [(text, "楷体", tag) for text, tag in _trailing_space_runs(cell_paragraphs[0])],
        14,
    )
    _set_runs(cell_paragraphs[2], [(" " * 26, "楷体", STYLE_DOTTED), ("款   ", "楷体")], 14)
    _set_runs(
        cell_paragraphs[4],
        [
            ("计人民币    ", "楷体"),
            (context["amount_upper"], "楷体", STYLE_DOTTED),
            ("        ", "楷体", STYLE_DOTTED),
            ("¥", "宋体"),
            ("   " + context["amount_str"] + "   ", "宋体", STYLE_UNDERLINE),
        ]
        + [(text, "宋体", tag) for text, tag in _trailing_space_runs(cell_paragraphs[4])],
        14,
    )
    _set_runs(
        cell_paragraphs[6],
        [
            ("领款人：  ", "楷体"),
            (context["name"], "楷体", STYLE_DOTTED),
            (" " * 18 + "主管审批：", "楷体"),
        ]
        + [(text, "楷体", tag) for text, tag in _trailing_space_runs(cell_paragraphs[6])],
        14,
    )


def _group_elements(body, sect_pr) -> list[list]:
    """按分页符把 body 子元素切分成每张凭单一组。"""
    groups: list[list] = []
    current: list = []
    for child in body:
        if child is sect_pr:
            continue
        if child.tag == qn("w:p") and child.xpath(".//w:br[@w:type='page']"):
            groups.append(current)
            current = []
            continue
        current.append(child)
    groups.append(current)
    return [g for g in groups if g]


def build_docx(record: dict, out_dir: Path | None = None) -> Path:
    """按母版生成单张凭单 docx，返回文件路径。"""
    return build_batch_docx([record], out_dir)


def build_batch_docx(records: list[dict], out_dir: Path | None = None,
                     filename: str | None = None) -> Path:
    """按母版生成多张凭单并合并为一个 docx，每张凭单独占一页。"""
    if not records:
        raise ValueError("没有可生成的凭单")
    directory = Path(out_dir or OUTPUT_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    if filename is None:
        if len(records) == 1:
            filename = output_filename(records[0])
        else:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"支付凭单_批量{len(records)}张_{stamp}.docx"
    target = directory / filename

    master = Document(template_path())
    body = master.element.body
    sect_pr = body.find(qn("w:sectPr"))
    proto = [child for child in body if child is not sect_pr]

    # 第二张起：分页符 + 深拷贝母版整页元素
    for _ in range(len(records) - 1):
        brk = master.add_paragraph()
        run = brk.add_run()
        br = OxmlElement("w:br")
        br.set(qn("w:type"), "page")
        run._element.append(br)
        for el in proto:
            sect_pr.addprevious(copy.deepcopy(el))

    from docx.table import Table
    from docx.text.paragraph import Paragraph

    for group, record in zip(_group_elements(body, sect_pr), records):
        paragraphs = [Paragraph(el, master) for el in group if el.tag == qn("w:p")]
        tables = [Table(el, master) for el in group if el.tag == qn("w:tbl")]
        _fill_voucher(paragraphs, tables, build_context(record))

    master.save(target)
    return target
