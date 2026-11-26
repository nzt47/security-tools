# -*- coding: utf-8 -*-
"""E3-2 纯库路线：docx_read（.docx 正文/表格抽取，标准库实现）

为什么：read_file 对 .docx 只能回 binary（它是 zip），模型等于读不到 Word 文档；
而 office_render/convert/recalculate 需要 LibreOffice（本机没有）。纯结构抽取不引任何依赖。

本文件钉住：
  1. 段落与表格按文档顺序取出；空段落不返回；
  2. 上限（段落/字符/表格行）达成时 truncated=true，**不静默截断**；
  3. 非 .docx / 不存在 / 坏 zip / 缺 document.xml 都给可执行错误，不抛异常。
"""
from __future__ import annotations

import zipfile

import pytest

from agent.office import docx_text


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _p(text: str) -> str:
    return "<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % text


def _table(rows):
    out = ["<w:tbl>"]
    for row in rows:
        out.append("<w:tr>")
        for cell in row:
            out.append("<w:tc>%s</w:tc>" % _p(cell))
        out.append("</w:tr>")
    out.append("</w:tbl>")
    return "".join(out)


def make_docx(path, body_xml: str):
    doc = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W, body_xml)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", doc)
    return str(path)


class TestExtract:
    def test_段落按顺序取出_空段落跳过(self, tmp_path):
        p = make_docx(tmp_path / "a.docx", _p("第一段") + _p("") + _p("第二段"))
        got = docx_text.extract(p)
        assert got["ok"] is True
        assert got["text"] == "第一段\n第二段"
        assert got["counts"]["paragraphs"] == 2
        assert got["truncated"] is False

    def test_表格按行列取出(self, tmp_path):
        p = make_docx(tmp_path / "b.docx", _p("引言") + _table([["A", "B"], ["1", "2"]]))
        got = docx_text.extract(p)
        assert got["ok"] is True
        assert got["tables"] == [[["A", "B"], ["1", "2"]]]
        assert got["counts"]["tables"] == 1

    def test_段落上限_如实标truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(docx_text, "MAX_PARAGRAPHS", 2)
        p = make_docx(tmp_path / "c.docx", "".join(_p("p%d" % i) for i in range(5)))
        got = docx_text.extract(p)
        assert got["ok"] is True and got["truncated"] is True
        assert got["counts"]["paragraphs"] == 2
        assert "truncated" in got["hint"]

    def test_字符上限_如实标truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(docx_text, "MAX_CHARS", 5)
        p = make_docx(tmp_path / "d.docx", _p("12345") + _p("67890"))
        got = docx_text.extract(p)
        assert got["truncated"] is True
        assert got["counts"]["chars"] == 5


class TestErrors:
    def test_缺路径(self):
        assert docx_text.extract("")["ok"] is False

    def test_非docx扩展名(self, tmp_path):
        bad = tmp_path / "a.doc"
        bad.write_bytes(b"whatever")
        got = docx_text.extract(str(bad))
        assert got["ok"] is False and ".docx" in got["error"]

    def test_文件不存在(self, tmp_path):
        got = docx_text.extract(str(tmp_path / "nope.docx"))
        assert got["ok"] is False and "不存在" in got["error"]

    def test_坏zip(self, tmp_path):
        bad = tmp_path / "bad.docx"
        bad.write_bytes(b"not a zip")
        got = docx_text.extract(str(bad))
        assert got["ok"] is False and "zip" in got["error"]

    def test_缺document_xml(self, tmp_path):
        p = tmp_path / "empty.docx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("other.xml", "<x/>")
        got = docx_text.extract(str(p))
        assert got["ok"] is False and "document.xml" in got["error"]

    def test_坏XML(self, tmp_path):
        p = tmp_path / "broken.docx"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("word/document.xml", "<w:document")
        got = docx_text.extract(str(p))
        assert got["ok"] is False and "XML" in got["error"]


class TestTool:
    def test_工具注册并可用(self, tmp_path):
        from agent.tools.docx_tools import register_all
        register_all()
        from agent import tools as T
        handler = T._registry["docx_read"]["handler"]
        p = make_docx(tmp_path / "e.docx", _p("工具调用可读"))
        got = handler(path=p)
        assert got["ok"] is True and "工具调用可读" in got["text"]

    def test_工具缺参数给提示(self):
        from agent.tools.docx_tools import register_all
        register_all()
        from agent import tools as T
        got = T._registry["docx_read"]["handler"]()
        assert got["ok"] is False and "path" in got["error"]

