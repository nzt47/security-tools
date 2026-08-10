#!/usr/bin/env python3
"""scripts/csv_to_md_table.py 单元测试草稿（批次 1 · 门禁类）

覆盖三个纯函数 + main：
1. csv_to_markdown — 正常转换 / 表头+分隔行 / `|` 转义 / 空文件 exit / 仅表头 exit / 读取失败 exit
2. build_section — H3 标题 + 表格拼接
3. upsert_into_report — 同标题幂等替换 / 首次插入
4. main — 仅打印（无 --report）与插入报告两条路径

【缺陷修复记录（2026-08-10）】脚本原本 `import re` 仅在 __main__ 局部导入，模块被 import 后
调用 upsert_into_report 会 NameError；已修复：import re 上移到模块顶层。本测试不再需要注入。
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import patch
import importlib.util


def _load():
    spec = importlib.util.spec_from_file_location(
        "csv_to_md_table",
        Path(__file__).resolve().parents[2] / "scripts" / "csv_to_md_table.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CM = _load()


def _write_csv(tmp_path, rows, bom=False):
    p = tmp_path / "data.csv"
    text = "\n".join(",".join(r) for r in rows)
    p.write_text(("\ufeff" if bom else "") + text, encoding="utf-8")
    return p


class TestCsvToMarkdown:
    def test_basic_conversion(self, tmp_path):
        p = _write_csv(tmp_path, [["name", "age"], ["a", "1"], ["b", "2"]])
        md = CM.csv_to_markdown(p)
        lines = md.splitlines()
        assert lines[0] == "| name | age |"
        assert lines[1] == "|---|---|"  # L43: "|" + join("---") + "|"
        assert lines[2] == "| a | 1 |"

    def test_bom_handled(self, tmp_path):
        """utf-8-sig 解码：BOM 不进入表头"""
        p = _write_csv(tmp_path, [["name"], ["a"]], bom=True)
        assert CM.csv_to_markdown(p).splitlines()[0] == "| name |"

    def test_pipe_escaped(self, tmp_path):
        """单元格内 `|` 被转义为 \\|，防止破坏表格"""
        p = _write_csv(tmp_path, [["desc"], ["a|b"]])
        assert "| a\\|b |" in CM.csv_to_markdown(p)

    def test_empty_file_exits(self, tmp_path):
        p = tmp_path / "empty.csv"
        p.write_text("", encoding="utf-8")
        with pytest.raises(SystemExit) as exc:
            CM.csv_to_markdown(p)
        assert "CSV 为空" in str(exc.value)

    def test_header_only_exits(self, tmp_path):
        p = _write_csv(tmp_path, [["name", "age"]])
        with pytest.raises(SystemExit) as exc:
            CM.csv_to_markdown(p)
        assert "缺少数据行" in str(exc.value)

    def test_unreadable_file_exits(self, tmp_path, monkeypatch):
        p = tmp_path / "x.csv"
        # raising=False：模块命名空间原本无 open 属性，需允许新建
        monkeypatch.setattr(CM, "open",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("denied")),
                            raising=False)
        with pytest.raises(SystemExit) as exc:
            CM.csv_to_markdown(p)
        assert "无法读取 CSV" in str(exc.value)


class TestBuildSection:
    def test_h3_title_and_table(self):
        sec = CM.build_section("回归数据", "| a |\n|---|\n")
        assert sec == "### 回归数据\n\n| a |\n|---|\n\n"  # 源码在 table 后追加 \n


class TestUpsertIntoReport:
    def test_first_insert_prepends(self, tmp_path):
        report = tmp_path / "r.md"
        report.write_text("# 报告\n\n## 四、\n\n内容", encoding="utf-8")
        section = "### 回归数据\n\n表格\n"
        assert CM.upsert_into_report(report, section, "回归数据") is True
        content = report.read_text(encoding="utf-8")
        assert "### 回归数据" in content
        assert content.index("### 回归数据") < content.index("## 四、")

    def test_idempotent_replace(self, tmp_path):
        report = tmp_path / "r.md"
        report.write_text("# 报告\n\n### 回归数据\n\n旧表格\n\n## 其他\n", encoding="utf-8")
        new_section = "### 回归数据\n\n新表格\n"
        CM.upsert_into_report(report, new_section, "回归数据")
        content = report.read_text(encoding="utf-8")
        assert "新表格" in content
        assert "旧表格" not in content

    def test_unreadable_report_exits(self, tmp_path, monkeypatch):
        report = tmp_path / "r.md"
        report.write_text("x", encoding="utf-8")
        monkeypatch.setattr(Path, "read_text",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("denied")))
        with pytest.raises(SystemExit) as exc:
            CM.upsert_into_report(report, "### t\n", "t")
        assert "无法读取报告" in str(exc.value)


class TestMain:
    def test_print_only(self, tmp_path, capsys):
        p = _write_csv(tmp_path, [["name"], ["a"]])
        with patch.object(sys, "argv", ["csv_to_md_table.py", "--csv", str(p)]):
            assert CM.main() == 0
        assert "| name |" in capsys.readouterr().out

    def test_insert_into_report(self, tmp_path, capsys):
        p = _write_csv(tmp_path, [["name"], ["a"]])
        report = tmp_path / "r.md"
        report.write_text("# 报告\n", encoding="utf-8")
        with patch.object(sys, "argv", [
            "csv_to_md_table.py", "--csv", str(p), "--report", str(report),
        ]):
            assert CM.main() == 0
        assert "已插入 Markdown 表格" in capsys.readouterr().out
        assert "| name |" in report.read_text(encoding="utf-8")

    def test_missing_csv_file(self, tmp_path, capsys):
        with patch.object(sys, "argv", [
            "csv_to_md_table.py", "--csv", str(tmp_path / "nope.csv"),
        ]):
            with pytest.raises(SystemExit) as exc:
                CM.main()
        assert "CSV 文件不存在" in str(exc.value)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
