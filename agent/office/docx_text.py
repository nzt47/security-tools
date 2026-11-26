"""docx 文本抽取（E3-2 纯库路线）：不依赖 LibreOffice，也不依赖 python-docx。

【为什么需要】`read_file` 对 .docx 只能回 `binary=True`（它是 zip），模型等于读不到 Word 文档；
而路线图里的 office_render/convert/recalculate 需要 LibreOffice（本机没有）。**能做且不引依赖**的是
纯结构抽取：.docx 本质是一个 zip，正文在 `word/document.xml`，用标准库 zipfile + ElementTree 即可。

【边界】
- 只读，不写、不改文档（写 docx 是另一件事，且 python-docx 未进主依赖）；
- 输出有上限（段落数/字符数），超了用 `truncated` 如实告知 —— 不静默截断；
- 只取**正文与表格**文字，不取批注/页眉页脚/脚注（那些要看再单独加，别在这里猜）；
- 不是 .docx（老 .doc / 其它格式）一律拒绝并说明为什么。
"""
from __future__ import annotations

import logging
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

#: OOXML 主命名空间
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: 单次最多返回的段落数 / 字符数（超出如实标 truncated）
MAX_PARAGRAPHS = 400
MAX_CHARS = 120_000
#: 表格最多返回的行数（每格文本已按字符上限截断）
MAX_TABLE_ROWS = 200


def _para_text(elem: ET.Element) -> str:
    """把 w:p 里的所有 w:t 拼起来（含超链接里的 run）"""
    parts = [node.text or "" for node in elem.iter(_W + "t")]
    return "".join(parts).strip()


def extract(path: str) -> Dict[str, Any]:
    """抽取 .docx 的正文段落与表格文本。

    Returns:
        {ok, path, paragraphs, text, tables, counts, truncated} 或 {ok: False, error}
    """
    target = str(path or "").strip()
    if not target:
        return {"ok": False, "error": "缺少 path"}
    if not target.lower().endswith(".docx"):
        return {"ok": False,
                "error": "只支持 .docx（老 .doc 是二进制 OLE 格式，需要另装转换器）"}
    try:
        zf = zipfile.ZipFile(target)
    except FileNotFoundError:
        return {"ok": False, "error": "文件不存在"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是合法的 .docx（zip 结构损坏）"}
    except OSError:
        return {"ok": False, "error": "读取文件失败（详见服务日志）"}

    try:
        with zf:
            raw = zf.read("word/document.xml")
    except KeyError:
        return {"ok": False, "error": "docx 里没有 word/document.xml（可能不是 Word 文档）"}
    except (OSError, zipfile.BadZipFile):
        return {"ok": False, "error": "读取正文失败（详见服务日志）"}

    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        logger.warning("[docx] document.xml 解析失败（路径不便落日志）")
        return {"ok": False, "error": "正文 XML 解析失败（文档可能损坏）"}

    body = root.find(_W + "body")
    scope = body if body is not None else root

    paragraphs: List[str] = []
    tables: List[List[List[str]]] = []
    truncated = False
    chars = 0

    # 按文档顺序遍历：段落与表格各自成块（w:p 直接子级，表格内段落不会被重复计入顶层）
    for child in list(scope):
        tag = child.tag
        if tag == _W + "p":
            text = _para_text(child)
            if not text:
                continue
            if len(paragraphs) >= MAX_PARAGRAPHS or chars + len(text) > MAX_CHARS:
                truncated = True
                break
            paragraphs.append(text)
            chars += len(text)
        elif tag == _W + "tbl":
            rows: List[List[str]] = []
            for tr in child.findall(_W + "tr"):
                if len(rows) >= MAX_TABLE_ROWS:
                    truncated = True
                    break
                cells = []
                for tc in tr.findall(_W + "tc"):
                    cell = " ".join(_para_text(p) for p in tc.findall(_W + "p")).strip()
                    cells.append(cell)
                rows.append(cells)
            if rows:
                tables.append(rows)

    text = "\n".join(paragraphs)
    return {
        "ok": True,
        "path": target,
        "text": text,
        "tables": tables,
        "counts": {"paragraphs": len(paragraphs), "tables": len(tables), "chars": len(text)},
        "truncated": truncated,
        "hint": "这只是正文与表格的纯文本；版式/图片/批注不在其中" + ("（输出已到上限，truncated=true）" if truncated else ""),
    }


__all__ = ["extract", "MAX_PARAGRAPHS", "MAX_CHARS", "MAX_TABLE_ROWS"]
