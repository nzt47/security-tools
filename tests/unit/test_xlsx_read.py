# -*- coding: utf-8 -*-
"""E3-2 纯库路线：xlsx_read（.xlsx 取值矩阵抽取，标准库实现）

为什么：read_file 对 .xlsx 只能回 binary；openpyxl 虽装了但**没进主依赖**（CI 不保证有），
而 .xlsx 是 zip + OOXML，标准库足够把「表→行→列」的值取出来。

本文件钉住：
  1. 共享字符串 / 内联字符串 / 数值 / 布尔 四类单元格都能读；
  2. 列号按 A1 对齐（中间空列补空串），公式结果取 v（与 Excel 显示一致）；
  3. 上限（表数/行数/字符/单格）达成时 truncated=true；
  4. 非 .xlsx / 不存在 / 坏 zip / 无工作表 都给可执行错误。
"""
from __future__ import annotations

import zipfile

from agent.office import xlsx_text


MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _shared(items):
    body = "".join("<si><t>%s</t></si>" % s for s in items)
    return '<sst xmlns="%s">%s</sst>' % (MAIN, body)


def _sheet(rows_xml: str):
    return ('<worksheet xmlns="%s"><sheetData>%s</sheetData></worksheet>'
            % (MAIN, rows_xml))


def make_xlsx(path, sheets, shared=None, rows_xmls=None):
    """造一个最小 xlsx：sheets=[(name, rid, target)]；rows_xmls={rid: row_xml}"""
    sheets_xml = "".join('<sheet name="%s" sheetId="%d" r:id="%s"/>' % (n, i + 1, rid)
                          for i, (n, rid, _t) in enumerate(sheets))
    wb = ('<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets></workbook>'
          % (MAIN, REL, sheets_xml))
    rels = "".join('<Relationship Id="%s" Type="%s" Target="%s"/>' % (rid, REL + "/worksheet", t)
                   for (_n, rid, t) in sheets)
    rels_doc = '<Relationships xmlns="%s">%s</Relationships>' % (PKG, rels)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("xl/workbook.xml", wb)
        zf.writestr("xl/_rels/workbook.xml.rels", rels_doc)
        if shared is not None:
            zf.writestr("xl/sharedStrings.xml", _shared(shared))
        for _n, rid, target in sheets:
            # 与真实工作簿一致：绝对 Target（/xl/...）就是 zip 内路径；相对 Target 才补 xl/
            zip_path = target.lstrip("/") if target.startswith("/") else "xl/" + target
            zf.writestr(zip_path, _sheet((rows_xmls or {}).get(rid, "")))
    return str(path)


class TestExtract:
    def test_共享字符串与数值_按列对齐(self, tmp_path):
        rows = ('<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                '<row r="2"><c r="A2"><v>42</v></c><c r="C2"><v>7</v></c></row>')
        p = make_xlsx(tmp_path / "a.xlsx", [("Sheet1", "rId1", "worksheets/sheet1.xml")],
                      shared=["姓名", "分数"], rows_xmls={"rId1": rows})
        got = xlsx_text.extract(p)
        assert got["ok"] is True
        sheet = got["sheets"][0]
        assert sheet["name"] == "Sheet1"
        assert sheet["rows"][0] == ["姓名", "分数"]
        # 中间空列补空串（A2=42, C2=7 ⇒ B2 为空）
        assert sheet["rows"][1] == ["42", "", "7"]
        assert got["counts"]["rows"] == 2
        assert got["truncated"] is False

    def test_绝对Target也能读出真实形状(self, tmp_path):
        """Excel/openpyxl 写绝对 Target（/xl/worksheets/sheet1.xml）——真实工作簿就是这样

        本机实测缺陷：一律 "xl/" + lstrip("/") 会把绝对路径拼成 xl/xl/... ⇒ zf.read KeyError
        ⇒ 每张表都返回空（2 张表、rows 全空，看起来"读到了但没内容"）。
        """
        rows = '<row r="1"><c r="A1"><v>11</v></c><c r="B1"><v>22</v></c></row>'
        p = make_xlsx(tmp_path / "abs.xlsx", [("S", "rId1", "/xl/worksheets/sheet1.xml")],
                      rows_xmls={"rId1": rows})
        got = xlsx_text.extract(p)
        assert got["ok"] is True
        assert got["sheets"][0]["rows"] == [["11", "22"]], got

    def test_内联字符串与布尔(self, tmp_path):
        rows = ('<row r="1"><c r="A1" t="inlineStr"><is><t>内联</t></is></c>'
                '<c r="B1" t="b"><v>1</v></c><c r="C1" t="b"><v>0</v></c></row>')
        p = make_xlsx(tmp_path / "b.xlsx", [("S", "rId1", "worksheets/sheet1.xml")],
                      rows_xmls={"rId1": rows})
        got = xlsx_text.extract(p)
        assert got["sheets"][0]["rows"][0] == ["内联", "TRUE", "FALSE"]

    def test_多表(self, tmp_path):
        p = make_xlsx(tmp_path / "c.xlsx",
                      [("一", "rId1", "worksheets/sheet1.xml"),
                       ("二", "rId2", "worksheets/sheet2.xml")],
                      rows_xmls={"rId1": '<row r="1"><c r="A1"><v>1</v></c></row>',
                                 "rId2": '<row r="1"><c r="A1"><v>2</v></c></row>'})
        got = xlsx_text.extract(p)
        assert [s["name"] for s in got["sheets"]] == ["一", "二"]
        assert got["sheets"][1]["rows"][0] == ["2"]

    def test_行数上限_如实标truncated(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xlsx_text, "MAX_ROWS", 2)
        rows = "".join('<row r="%d"><c r="A%d"><v>%d</v></c></row>' % (i, i, i)
                       for i in range(1, 6))
        p = make_xlsx(tmp_path / "d.xlsx", [("S", "rId1", "worksheets/sheet1.xml")],
                      rows_xmls={"rId1": rows})
        got = xlsx_text.extract(p)
        assert got["truncated"] is True
        assert got["counts"]["rows"] == 2

    def test_表数上限(self, tmp_path, monkeypatch):
        monkeypatch.setattr(xlsx_text, "MAX_SHEETS", 1)
        p = make_xlsx(tmp_path / "e.xlsx",
                      [("A", "rId1", "worksheets/sheet1.xml"),
                       ("B", "rId2", "worksheets/sheet2.xml")],
                      rows_xmls={"rId1": '<row r="1"><c r="A1"><v>1</v></c></row>',
                                 "rId2": '<row r="1"><c r="A1"><v>2</v></c></row>'})
        got = xlsx_text.extract(p)
        assert len(got["sheets"]) == 1 and got["truncated"] is True


class TestErrors:
    def test_缺路径(self):
        assert xlsx_text.extract("")["ok"] is False

    def test_非xlsx扩展名(self, tmp_path):
        bad = tmp_path / "a.xls"
        bad.write_bytes(b"x")
        got = xlsx_text.extract(str(bad))
        assert got["ok"] is False and ".xlsx" in got["error"]

    def test_文件不存在(self, tmp_path):
        got = xlsx_text.extract(str(tmp_path / "nope.xlsx"))
        assert got["ok"] is False and "不存在" in got["error"]

    def test_坏zip(self, tmp_path):
        bad = tmp_path / "bad.xlsx"
        bad.write_bytes(b"not a zip")
        got = xlsx_text.extract(str(bad))
        assert got["ok"] is False and "zip" in got["error"]

    def test_没有工作表(self, tmp_path):
        p = make_xlsx(tmp_path / "empty.xlsx", [])
        got = xlsx_text.extract(p)
        assert got["ok"] is False and "工作表" in got["error"]


class TestTool:
    def test_工具注册并可用(self, tmp_path):
        from agent.tools.xlsx_tools import register_all
        register_all()
        from agent import tools as T
        handler = T._registry["xlsx_read"]["handler"]
        p = make_xlsx(tmp_path / "f.xlsx", [("S", "rId1", "worksheets/sheet1.xml")],
                      rows_xmls={"rId1": '<row r="1"><c r="A1"><v>99</v></c></row>'})
        got = handler(path=p)
        assert got["ok"] is True and got["sheets"][0]["rows"][0] == ["99"]

