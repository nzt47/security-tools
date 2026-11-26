# -*- coding: utf-8 -*-
"""pptx 文本抽取（E3-2 纯库路线）：不依赖 LibreOffice，也不依赖 python-pptx。

【为什么】`read_file` 对 .pptx 只能回 `binary=True`（它是 zip），模型等于读不到演示文稿；
而 office_render/convert/recalculate 需要 LibreOffice（本机与 CI 都没有）。能做且不引依赖的是
纯结构抽取：.pptx 本质是 zip，页序在 `ppt/presentation.xml` 的 `sldIdLst`，正文在
`ppt/slides/slideN.xml` 的 `a:t`，标准库 zipfile + ElementTree 即可。

【边界】
- 只读，不写、不改文档；不取备注页（notesSlides）/母版/图片/批注 —— 那些要看再单独加，别在这里猜；
- **页序以 presentation.xml 的 sldIdLst + rels 为准**（不是文件名数字序）；缺 rels 才退回数字序并标注；
- 上限：页数 / 总字符 / 每页段落，超了 `truncated=true` 如实告知，不静默截断；
- 不是 .pptx（老 .ppt 是二进制 OLE）一律拒绝并说明为什么。
"""
from __future__ import annotations

import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

_NS_A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_NS_P = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
_NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_NS_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

#: 单次最多返回的页数 / 字符数 / 每页段落数（超出如实标 truncated）
MAX_SLIDES = 200
MAX_CHARS = 120_000
MAX_PARAGRAPHS_PER_SLIDE = 200

_SLIDE_PART = re.compile(r"^ppt/slides/slide(\d+)\.xml$")


def _para_text(p: ET.Element) -> str:
    """把 a:p 里的所有 a:t 拼起来（含 a:r / a:fld / a:br 里的文本）"""
    return "".join(node.text or "" for node in p.iter(_NS_A + "t")).strip()


def _slide_parts(zf: zipfile.ZipFile) -> Tuple[List[str], bool]:
    """返回 (按演示顺序排列的 slide part 路径, 是否真的按 presentation.xml 排序)。

    缺 presentation.xml / rels / sldIdLst 时退回 `slideN.xml` 数字序（布尔 False 让调用方
    在 hint 里如实说明"顺序来自文件名"）。
    """
    try:
        pres = ET.fromstring(zf.read("ppt/presentation.xml"))
        rels_root = ET.fromstring(zf.read("ppt/_rels/presentation.xml.rels"))
    except (KeyError, ET.ParseError):
        pres, rels_root = None, None

    ordered: List[str] = []
    if pres is not None and rels_root is not None:
        rels = {}
        for rel in rels_root.findall(_NS_PKG_REL + "Relationship"):
            rels[str(rel.get("Id") or "")] = str(rel.get("Target") or "")
        lst = pres.find(_NS_P + "sldIdLst")
        if lst is not None:
            for sld in lst.findall(_NS_P + "sldId"):
                target = rels.get(str(sld.get(_NS_R + "id") or ""), "").strip().replace("\\", "/")
                if not target:
                    continue
                target = target.lstrip("/")
                if not target.startswith("ppt/"):
                    target = "ppt/" + target
                if target not in ordered:
                    ordered.append(target)

    numeric = sorted(
        (name for name in zf.namelist() if _SLIDE_PART.match(name)),
        key=lambda name: int(_SLIDE_PART.match(name).group(1)),  # type: ignore[union-attr]
    )
    if not ordered:
        return numeric, False
    # presentation.xml 里列出的页若与磁盘上的页不完全一致，用数字序补齐（缺页/孤儿页都别丢）
    extras = [name for name in numeric if name not in ordered]
    return ordered + extras, not extras and len(ordered) == len(numeric)


def extract(path: str) -> Dict[str, Any]:
    """抽取 .pptx 每页的文本段落与表格。

    Returns:
        {ok, path, slides, text, counts, truncated, hint} 或 {ok: False, error}
    """
    target = str(path or "").strip()
    if not target:
        return {"ok": False, "error": "缺少 path"}
    if not target.lower().endswith(".pptx"):
        return {"ok": False,
                "error": "只支持 .pptx（老 .ppt 是二进制 OLE 格式，需要另装转换器）"}
    try:
        zf = zipfile.ZipFile(target)
    except FileNotFoundError:
        return {"ok": False, "error": "文件不存在"}
    except zipfile.BadZipFile:
        return {"ok": False, "error": "不是合法的 .pptx（zip 结构损坏）"}
    except OSError:
        return {"ok": False, "error": "读取文件失败（详见服务日志）"}

    with zf:
        parts, ordered_by_presentation = _slide_parts(zf)
        if not parts:
            return {"ok": False,
                    "error": "pptx 里没有幻灯片（ppt/presentation.xml 与 ppt/slides/*.xml 都没有）"}

        slides: List[Dict[str, Any]] = []
        all_paragraphs: List[str] = []
        chars = 0
        truncated = False
        skipped = 0

        for index, part in enumerate(parts[:MAX_SLIDES], start=1):
            try:
                root = ET.fromstring(zf.read(part))
            except KeyError:
                skipped += 1
                continue
            except ET.ParseError:
                skipped += 1
                continue

            paragraphs: List[str] = []
            for sp in root.iter(_NS_P + "sp"):
                for p in sp.iter(_NS_A + "p"):
                    text = _para_text(p)
                    if not text:
                        continue
                    if len(paragraphs) >= MAX_PARAGRAPHS_PER_SLIDE or chars + len(text) > MAX_CHARS:
                        truncated = True
                        break
                    paragraphs.append(text)
                    chars += len(text)
                if truncated:
                    break

            tables: List[List[List[str]]] = []
            if not truncated:
                for tbl in root.iter(_NS_A + "tbl"):
                    rows: List[List[str]] = []
                    for tr in tbl.findall(_NS_A + "tr"):
                        cells = []
                        for tc in tr.findall(_NS_A + "tc"):
                            cell = " ".join(
                                _para_text(p) for p in tc.iter(_NS_A + "p")).strip()
                            cells.append(cell)
                        rows.append(cells)
                    if rows:
                        tables.append(rows)
            slides.append({
                "index": index,
                "part": part,
                "title": paragraphs[0] if paragraphs else "",
                "paragraphs": paragraphs,
                "tables": tables,
            })
            all_paragraphs.extend(paragraphs)
            if truncated:
                break

        if len(parts) > MAX_SLIDES:
            truncated = True

    text = "\n".join(all_paragraphs)
    notes = ["页序按 presentation.xml 的 sldIdLst 解析"
             if ordered_by_presentation else "页序退回文件名数字序（缺 presentation.xml 或 rels）"]
    if skipped:
        notes.append("有 %d 页无法解析已跳过" % skipped)
    if truncated:
        notes.append("输出已到上限")
    return {
        "ok": True,
        "path": target,
        "slides": slides,
        "text": text,
        "counts": {
            "slides": len(slides),
            "paragraphs": len(all_paragraphs),
            "tables": sum(len(s["tables"]) for s in slides),
            "chars": len(text),
        },
        "truncated": truncated,
        "hint": "这是每页正文与表格的纯文本；备注/版式/图片/批注不在其中（" + "；".join(notes) + "）",
    }


__all__ = ["extract", "MAX_SLIDES", "MAX_CHARS", "MAX_PARAGRAPHS_PER_SLIDE"]
