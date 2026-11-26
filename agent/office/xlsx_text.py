"""xlsx 文本抽取（E3-2 纯库路线）：不依赖 openpyxl / LibreOffice。

【为什么】.xlsx 与 .docx 一样是 zip + OOXML：工作簿结构在 xl/workbook.xml，共享字符串在
xl/sharedStrings.xml，每张表在 xl/worksheets/sheetN.xml。标准库 zipfile + ElementTree 足够把
"每张表每行每格的值"取出来；`read_file` 对 .xlsx 只能回 binary，模型等于读不到表格。

【边界】
- 只取值（数字/文本/布尔），不取公式字符串与格式/颜色/批注（公式结果本身就在 v 里）；
- 上限：表数 10 / 每表行数 500 / 总字符 120k，超了 `truncated=true`（不静默截断）；
- 空行空列原样保留结构（行内空格用空串），便于模型对齐列；
- 不是 .xlsx（老 .xls 是二进制 BIFF）一律拒绝并说明。
"""
from __future__ import annotations

import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: 表数 / 行数 / 字符数上限（超出如实标 truncated）
MAX_SHEETS = 10
MAX_ROWS = 500
MAX_CHARS = 120_000
#: 单格文本上限（防一个巨型单元格吃掉全部预算）
MAX_CELL_CHARS = 2_000

_CELL_REF = re.compile(r"^([A-Z]+)(\d+)$")


def _col_index(ref: str) -> int:
    """A1 → 0-based 列号（"AB12" → 27）；解析失败返回 -1"""
    m = _CELL_REF.match(str(ref or "").strip().upper())
    if not m:
        return -1
    letters = m.group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _shared_strings(zf: zipfile.ZipFile) -> List[str]:
    """读 xl/sharedStrings.xml（富文本 run 拼接；缺失返回空表）"""
    try:
        raw = zf.read("xl/sharedStrings.xml")
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out: List[str] = []
    for si in root.findall(_NS_MAIN + "si"):
        parts = [node.text or "" for node in si.iter(_NS_MAIN + "t")]
        out.append("".join(parts))
    return out


def _sheet_files(zf: zipfile.ZipFile) -> List[tuple]:
    """[(表名, zip 内路径)] —— 以 workbook.xml + rels 为准；解析失败回退到按名排序的 sheet*.xml"""
    try:
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    except (KeyError, ET.ParseError):
        names = sorted(n for n in zf.namelist()
                       if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        return [(n.rsplit("/", 1)[-1].replace(".xml", ""), n) for n in names]

    rel_map = {}
    for rel in rels.findall(_NS_PKG_REL + "Relationship"):
        rid = rel.get("Id")
        target = rel.get("Target") or ""
        if rid:
            rel_map[rid] = target

    out = []
    for sheet in wb.findall(_NS_MAIN + "sheets/" + _NS_MAIN + "sheet"):
        name = str(sheet.get("name") or "")
        rid = sheet.get(_NS_REL + "id")
        target = rel_map.get(rid or "", "")
        path = ("xl/" + target.lstrip("/")) if target else ""
        if path and not path.startswith("xl/"):
            path = "xl/" + path
        out.append((name, path))
    return [(n, p) for n, p in out if p]


def _cell_value(cell: ET.Element, shared: List[str]) -> str:
    """取一格的值：共享字符串 / 内联字符串 / 数值 / 布尔；公式结果走 v（与 Excel 显示一致）"""
    ctype = str(cell.get("t") or "")
    if ctype == "inlineStr":
        node = cell.find(_NS_MAIN + "is")
        if node is None:
            return ""
        return "".join(n.text or "" for n in node.iter(_NS_MAIN + "t"))
    v = cell.find(_NS_MAIN + "v")
    raw = "" if v is None or v.text is None else str(v.text)
    if ctype == "s":
        try:
            return shared[int(raw)] if raw else ""
        except (ValueError, IndexError):
            return ""
    if ctype == "b":
        return "TRUE" if raw == "1" else ("FALSE" if raw == "0" else raw)
    return raw


def _sheet_rows(zf: zipfile.ZipFile, path: str, shared: List[str], budget: Dict[str, int],
                state: Dict[str, bool]) -> List[List[str]]:
    """读一张表的前 MAX_ROWS 行（列号按 A1 对齐，空列补空串）"""
    try:
        raw = zf.read(path)
    except KeyError:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []

    rows: List[List[str]] = []
    for row in root.iter(_NS_MAIN + "row"):
        if len(rows) >= MAX_ROWS or budget["chars"] >= MAX_CHARS:
            state["truncated"] = True
            break
        cells: Dict[int, str] = {}
        max_idx = -1
        for cell in row.findall(_NS_MAIN + "c"):
            idx = _col_index(str(cell.get("r") or ""))
            if idx < 0:
                idx = max_idx + 1
            value = _cell_value(cell, shared)
            if len(value) > MAX_CELL_CHARS:
                value = value[:MAX_CELL_CHARS]
                state["truncated"] = True
            cells[idx] = value
            max_idx = max(max_idx, idx)
            budget["chars"] += len(value)
        width = max_idx + 1 if max_idx >= 0 else 0
        rows.append([cells.get(i, "") for i in range(width)])
    return rows


def extract(path: str) -> Dict[str, Any]:
    """抽取 .xlsx 每张表的取值（行 × 列矩阵）。"""
    target = str(path or "").strip()
    if not target:
        return {"ok": False, "error": "缺少 path"}
    if not target.lower().endswith(".xlsx"):
        return {"ok": False,
                "error": "只支持 .xlsx（老 .xls 是二进制 BIFF 格式，需要另装转换器）"}
    try:
        zf = zipfile.ZipFile(target)
    except FileNotFoundError:
        return {"ok": False, "error": "文件不存在"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是合法的 .xlsx（zip 结构损坏）"}
    except OSError:
        return {"ok": False, "error": "读取文件失败（详见服务日志）"}

    state = {"truncated": False}
    budget = {"chars": 0}
    sheets: List[Dict[str, Any]] = []
    try:
        with zf:
            shared = _shared_strings(zf)
            for name, sheet_path in _sheet_files(zf):
                if len(sheets) >= MAX_SHEETS:
                    state["truncated"] = True
                    break
                rows = _sheet_rows(zf, sheet_path, shared, budget, state)
                sheets.append({"name": name, "rows": rows, "row_count": len(rows)})
    except (OSError, zipfile.BadZipFile):
        return {"ok": False, "error": "读取工作簿失败（详见服务日志）"}

    if not sheets:
        return {"ok": False, "error": "工作簿里没有可读的工作表"}

    return {
        "ok": True,
        "path": target,
        "sheets": sheets,
        "counts": {"sheets": len(sheets),
                   "rows": sum(s["row_count"] for s in sheets),
                   "chars": budget["chars"]},
        "truncated": state["truncated"],
        "hint": "只取值（数字/文本/布尔），不含公式原文与格式；空单元格为空串",
    }


__all__ = ["extract", "MAX_SHEETS", "MAX_ROWS", "MAX_CHARS", "MAX_CELL_CHARS"]
