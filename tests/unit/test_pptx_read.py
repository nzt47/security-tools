# -*- coding: utf-8 -*-
"""E3-2 收口（纯库路线）：pptx_read（.pptx 每页正文/表格抽取，标准库实现）

为什么：read_file 对 .pptx 只能回 binary（它是 zip），模型等于读不到演示文稿；而 render/convert
需要 LibreOffice（本机与 CI 都没有）。纯结构抽取不引任何依赖，与 docx_read / xlsx_read 同套路。

本文件钉住：
  1. **页序以 ppt/presentation.xml 的 sldIdLst + rels 为准**（不是文件名数字序）；
  2. 每页段落与表格取出、空段落跳过；
  3. 上限（页/字符/每页段落）达成时 truncated=true，**不静默截断**；
  4. 非 .pptx / 不存在 / 坏 zip / 没有幻灯片 / 缺 rels（退回数字序并标注）都给可执行结果。
"""
from __future__ import annotations

import zipfile

from agent.office import pptx_text


P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _sp(*paras: str) -> str:
    body = "".join("<a:p><a:r><a:t>%s</a:t></a:r></a:p>" % t for t in paras)
    return "<p:sp><p:txBody>%s</p:txBody></p:sp>" % body


def _table(rows) -> str:
    trs = []
    for row in rows:
        tcs = []
        for cell in row:
            tcs.append("<a:tc><a:txBody><a:p><a:r><a:t>%s</a:t></a:r></a:p></a:txBody></a:tc>" % cell)
        trs.append("<a:tr>%s</a:tr>" % "".join(tcs))
    return ("<p:graphicFrame><a:graphic><a:graphicData><a:tbl>%s</a:tbl>"
            "</a:graphicData></a:graphic></p:graphicFrame>" % "".join(trs))


def _slide(*blocks: str) -> str:
    return ('<p:sld xmlns:p="%s" xmlns:a="%s"><p:cSld><p:spTree>%s</p:spTree></p:cSld></p:sld>'
            % (P, A, "".join(blocks)))


def make_pptx(path, slides: dict, order=None, with_rels: bool = True):
    """按 {part 名: slide XML} 造一个最小 pptx；order 指定 sldIdLst 顺序（缺省按 dict 顺序）。"""
    order = list(order or slides.keys())
    rels, ids = [], []
    for i, part in enumerate(order, start=2):
        target = part.replace("ppt/", "", 1)
        rels.append('<Relationship Id="rId%d" Type="%s/slide" Target="%s"/>' % (i, R, target))
        ids.append('<p:sldId id="%d" r:id="rId%d"/>' % (255 + i, i))
    pres = ('<p:presentation xmlns:p="%s" xmlns:r="%s"><p:sldIdLst>%s</p:sldIdLst></p:presentation>'
            % (P, R, "".join(ids)))
    rels_xml = '<Relationships xmlns="%s">%s</Relationships>' % (PKG, "".join(rels))
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("ppt/presentation.xml", pres)
        if with_rels:
            zf.writestr("ppt/_rels/presentation.xml.rels", rels_xml)
        for part, xml in slides.items():
            zf.writestr(part, xml)
    return str(path)


class TestOrderAndContent:
    def test_页序以presentation为准_不是文件名序(self, tmp_path):
        p = make_pptx(
            tmp_path / "a.pptx",
            {"ppt/slides/slide1.xml": _slide(_sp("第一页")),
             "ppt/slides/slide2.xml": _slide(_sp("第二页"))},
            order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
        )
        got = pptx_text.extract(p)
        assert got["ok"] is True, got
        assert [s["title"] for s in got["slides"]] == ["第二页", "第一页"]
        assert got["text"] == "第二页\n第一页"
        assert got["counts"]["slides"] == 2

    def test_段落与表格都取出_空段落跳过(self, tmp_path):
        p = make_pptx(tmp_path / "b.pptx", {"ppt/slides/slide1.xml": _slide(
            _sp("标题", "", "正文一"), _table([["A", "B"], ["1", "2"]]))})
        got = pptx_text.extract(p)
        assert got["ok"] is True
        slide = got["slides"][0]
        assert slide["paragraphs"] == ["标题", "正文一"]
        assert slide["title"] == "标题"
        assert slide["tables"] == [[["A", "B"], ["1", "2"]]]
        assert got["counts"]["tables"] == 1
        assert got["truncated"] is False

    def test_缺rels退回数字序并如实标注(self, tmp_path):
        p = make_pptx(
            tmp_path / "c.pptx",
            {"ppt/slides/slide2.xml": _slide(_sp("二")),
             "ppt/slides/slide1.xml": _slide(_sp("一"))},
            order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
            with_rels=False,
        )
        got = pptx_text.extract(p)
        assert got["ok"] is True
        assert [s["title"] for s in got["slides"]] == ["一", "二"]
        assert "数字序" in got["hint"]

    def test_字符上限_如实标truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pptx_text, "MAX_CHARS", 5)
        p = make_pptx(tmp_path / "d.pptx", {"ppt/slides/slide1.xml": _slide(_sp("12345", "67890"))})
        got = pptx_text.extract(p)
        assert got["ok"] is True and got["truncated"] is True
        assert got["counts"]["chars"] == 5
        assert "上限" in got["hint"]

    def test_页数上限(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pptx_text, "MAX_SLIDES", 1)
        p = make_pptx(tmp_path / "e.pptx", {
            "ppt/slides/slide1.xml": _slide(_sp("一")),
            "ppt/slides/slide2.xml": _slide(_sp("二")),
        })
        got = pptx_text.extract(p)
        assert got["counts"]["slides"] == 1 and got["truncated"] is True


class TestErrors:
    def test_缺路径(self):
        assert pptx_text.extract("")["ok"] is False

    def test_非pptx扩展名(self, tmp_path):
        bad = tmp_path / "a.ppt"
        bad.write_bytes(b"whatever")
        got = pptx_text.extract(str(bad))
        assert got["ok"] is False and ".pptx" in got["error"]

    def test_文件不存在(self, tmp_path):
        got = pptx_text.extract(str(tmp_path / "nope.pptx"))
        assert got["ok"] is False and "不存在" in got["error"]

    def test_坏zip(self, tmp_path):
        bad = tmp_path / "bad.pptx"
        bad.write_bytes(b"not a zip")
        got = pptx_text.extract(str(bad))
        assert got["ok"] is False and "zip" in got["error"]

    def test_没有幻灯片(self, tmp_path):
        p = tmp_path / "empty.pptx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("other.xml", "<x/>")
        got = pptx_text.extract(str(p))
        assert got["ok"] is False and "幻灯片" in got["error"]

    def test_坏页XML跳过不炸(self, tmp_path):
        p = make_pptx(tmp_path / "f.pptx", {"ppt/slides/slide1.xml": _slide(_sp("好页"))})
        with zipfile.ZipFile(p, "a") as zf:
            zf.writestr("ppt/slides/slide2.xml", "<p:sld")
        got = pptx_text.extract(p)
        assert got["ok"] is True
        assert got["counts"]["slides"] == 1
        assert "跳过" in got["hint"]


class TestTool:
    def test_工具注册并可用(self, tmp_path):
        from agent.tools.pptx_tools import register_all
        register_all()
        from agent import tools as T
        handler = T._registry["pptx_read"]["handler"]
        p = make_pptx(tmp_path / "g.pptx", {"ppt/slides/slide1.xml": _slide(_sp("工具调用可读"))})
        got = handler(path=p)
        assert got["ok"] is True and "工具调用可读" in got["text"]

    def test_工具缺参数给提示(self):
        from agent.tools.pptx_tools import register_all
        register_all()
        from agent import tools as T
        got = T._registry["pptx_read"]["handler"]()
        assert got["ok"] is False and "path" in got["error"]

    def test_越界路径被拒(self):
        from agent.tools.pptx_tools import register_all
        register_all()
        from agent import tools as T
        got = T._registry["pptx_read"]["handler"](path="../../etc/passwd.pptx")
        assert got["ok"] is False and got["error"]
