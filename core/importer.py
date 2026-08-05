# -*- coding: utf-8 -*-
"""Excel / CSV 导入导出与模板生成。"""
import csv
import io
import os

from openpyxl import Workbook, load_workbook

HEADERS = ["公司名称", "联系人", "电话", "邮箱", "地区", "客户类型", "状态", "来源", "标签", "备注", "地址"]
HEADER_MAP = {
    "公司名称": "name", "公司": "name", "名称": "name", "name": "name",
    "联系人": "contact", "联系人姓名": "contact",
    "电话": "phone", "手机": "phone", "手机号": "phone", "联系电话": "phone",
    "邮箱": "email", "email": "email", "邮件": "email",
    "地区": "region", "区域": "region", "城市": "region",
    "客户类型": "type", "类型": "type",
    "状态": "status",
    "来源": "source", "线索来源": "source",
    "标签": "tags", "tag": "tags",
    "备注": "note", "备注说明": "note",
    "地址": "address", "公司地址": "address",
}


def _row_to_lead(row, header_index):
    lead = {}
    for cell, idx in header_index.items():
        key = HEADER_MAP.get(str(cell).strip())
        if not key:
            continue
        val = row[idx].value if hasattr(row[idx], "value") else row[idx]
        if val is None:
            val = ""
        lead[key] = str(val).strip()
    return lead


def parse_xlsx(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return [], "文件为空"
    header = [str(c or "").strip() for c in rows[0]]
    header_index = {h: i for i, h in enumerate(header) if h in HEADER_MAP}
    if not header_index:
        return [], "第一行没有识别到表头（如：公司名称、联系人、电话）"
    leads = []
    for row in rows[1:]:
        lead = _row_to_lead(row, header_index)
        if lead.get("name"):
            leads.append(lead)
    return leads, None


def parse_csv(path):
    raw = open(path, "rb").read()
    text = None
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return [], "无法识别文件编码"
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        return [], "文件为空"
    header = [str(c or "").strip() for c in rows[0]]
    header_index = {h: i for i, h in enumerate(header) if h in HEADER_MAP}
    if not header_index:
        return [], "第一行没有识别到表头（如：公司名称、联系人、电话）"
    leads = []
    for row in rows[1:]:
        lead = {}
        for h, idx in header_index.items():
            val = row[idx] if idx < len(row) else ""
            lead[HEADER_MAP[h]] = str(val).strip()
        if lead.get("name"):
            leads.append(lead)
    return leads, None


def parse_file(path, filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".xlsx":
        return parse_xlsx(path)
    if ext in (".csv", ".txt"):
        return parse_csv(path)
    return [], "仅支持 .xlsx / .csv 文件"


def build_template_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "线索导入模板"
    ws.append(HEADERS)
    ws.append(["示例光纤科技有限公司", "张三", "13800138000", "zhang@example.com", "广东深圳", "工程商", "新线索", "批量导入", "光缆", "需要提供报价", "深圳市南山区"])
    ws.append(["", "", "", "", "", "", "", "", "", "", ""])
    widths = [28, 12, 15, 22, 12, 10, 10, 12, 12, 30, 26]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _rows_to_workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "客户线索"
    ws.append(HEADERS)
    for r in rows:
        ws.append([
            r.get("name", ""), r.get("contact", ""), r.get("phone", ""),
            r.get("email", ""), r.get("region", ""), r.get("type", ""),
            r.get("status", ""), r.get("source", ""), r.get("tags", ""),
            r.get("note", ""), r.get("address", ""),
        ])
    for i, w in enumerate([28, 12, 15, 22, 12, 10, 10, 12, 12, 30, 26], start=1):
        ws.column_dimensions[chr(64 + i)].width = w
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_xlsx(rows):
    return _rows_to_workbook(rows).getvalue()


def export_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    for r in rows:
        writer.writerow([
            r.get("name", ""), r.get("contact", ""), r.get("phone", ""),
            r.get("email", ""), r.get("region", ""), r.get("type", ""),
            r.get("status", ""), r.get("source", ""), r.get("tags", ""),
            r.get("note", ""), r.get("address", ""),
        ])
    return "\ufeff" + buf.getvalue()
