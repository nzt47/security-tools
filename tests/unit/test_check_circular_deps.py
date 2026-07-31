"""check_circular_deps.py 的单元测试 — 验证 --verbose JSON 输出结构.

用 fixture (tests/fixtures/circular_deps_verbose_sample.json) 自动验证
build_cycles_json() 的输出结构, 确保 cycles[] -> edges[] -> locations[] 三层嵌套
与文档/fixture 一致, 后续脚本可稳定解析.
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import pytest

# 将 scripts/ 加入 sys.path 以导入 check_circular_deps
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_DIR = _PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from check_circular_deps import build_cycles_json, extract

# fixture 文件路径
_FIXTURE_PATH = _PROJECT_ROOT / "tests" / "fixtures" / "circular_deps_verbose_sample.json"


def _get_keys(obj, prefix=""):
    """递归提取 JSON 对象的所有键路径 (用于结构比对, 不比对值)."""
    keys = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.startswith("_"):  # 跳过 fixture 的 _comment/_source/_usage
                continue
            full = f"{prefix}.{k}" if prefix else k
            keys.add(full)
            keys |= _get_keys(v, full)
    elif isinstance(obj, list) and obj:
        keys |= _get_keys(obj[0], prefix + "[]")
    return keys


class TestBuildCyclesJson:
    """测试 build_cycles_json() 的输出结构."""

    def test_structure_matches_fixture(self):
        """验证 build_cycles_json() 输出结构与 fixture 完全一致."""
        # 构造模拟的循环依赖数据 (与 fixture 同构)
        import_locations = defaultdict(list)
        import_locations[("agent.module_a", "agent.module_b")] = [
            ("agent/module_a.py", 5, "from agent.module_b import ...")
        ]
        import_locations[("agent.module_b", "agent.module_a")] = [
            ("agent/module_b.py", 3, "from agent.module_a import ...")
        ]
        found_cycles = [("agent.module_a", "agent.module_b")]

        result = build_cycles_json(found_cycles, import_locations)

        # 与 fixture 比对结构 (键路径集合)
        with open(_FIXTURE_PATH, encoding="utf-8") as f:
            fixture = json.load(f)

        result_keys = _get_keys(result)
        fixture_keys = _get_keys(fixture)
        assert result_keys == fixture_keys, (
            f"结构不一致!\n"
            f"  缺失: {fixture_keys - result_keys}\n"
            f"  多余: {result_keys - fixture_keys}"
        )

    def test_empty_cycles(self):
        """无循环时返回空 cycles + 零计数 summary."""
        result = build_cycles_json([], defaultdict(list))
        assert result["cycles"] == []
        assert result["summary"]["total_cycles"] == 0
        assert result["summary"]["total_edges"] == 0

    def test_cycle_modules_sorted(self):
        """验证 modules 列表是排序的 (确定性输出)."""
        import_locations = defaultdict(list)
        import_locations[("agent.zzz", "agent.aaa")] = [
            ("agent/zzz.py", 1, "from agent.aaa import ...")
        ]
        import_locations[("agent.aaa", "agent.zzz")] = [
            ("agent/aaa.py", 1, "from agent.zzz import ...")
        ]
        found_cycles = [("agent.zzz", "agent.aaa")]

        result = build_cycles_json(found_cycles, import_locations)
        assert result["cycles"][0]["modules"] == ["agent.aaa", "agent.zzz"]

    def test_edge_directions_preserved(self):
        """验证两个方向的有向边都被记录."""
        import_locations = defaultdict(list)
        import_locations[("agent.a", "agent.b")] = [("agent/a.py", 1, "stmt_a")]
        import_locations[("agent.b", "agent.a")] = [("agent/b.py", 2, "stmt_b")]
        found_cycles = [("agent.a", "agent.b")]

        result = build_cycles_json(found_cycles, import_locations)
        edges = result["cycles"][0]["edges"]
        assert len(edges) == 2
        directions = {(e["from"], e["to"]) for e in edges}
        assert ("agent.a", "agent.b") in directions
        assert ("agent.b", "agent.a") in directions

    def test_location_fields_complete(self):
        """验证每个 location 包含 file/line/statement 三个字段."""
        import_locations = defaultdict(list)
        import_locations[("agent.a", "agent.b")] = [("agent/a.py", 42, "from agent.b import x")]
        import_locations[("agent.b", "agent.a")] = [("agent/b.py", 7, "from agent.a import y")]
        found_cycles = [("agent.a", "agent.b")]

        result = build_cycles_json(found_cycles, import_locations)
        for edge in result["cycles"][0]["edges"]:
            for loc in edge["locations"]:
                assert set(loc.keys()) == {"file", "line", "statement"}
                assert isinstance(loc["line"], int)


class TestFixtureValidity:
    """验证 fixture 文件本身的有效性."""

    def test_fixture_json_valid(self):
        """fixture 是有效 JSON."""
        with open(_FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert "cycles" in data
        assert "summary" in data

    def test_fixture_has_one_cycle(self):
        """fixture 包含 1 个循环示例 (与 _comment 描述一致)."""
        with open(_FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert data["summary"]["total_cycles"] == 1
        assert data["summary"]["total_edges"] == 2
        assert len(data["cycles"]) == 1

    def test_fixture_cycle_has_two_edges(self):
        """fixture 的循环包含 2 条有向边 (双向)."""
        with open(_FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        cycle = data["cycles"][0]
        assert len(cycle["edges"]) == 2
        # 两个方向
        directions = {(e["from"], e["to"]) for e in cycle["edges"]}
        assert len(directions) == 2


class TestExtractFunction:
    """测试 extract() 对导入语句的解析."""

    def test_extract_top_level_import(self, tmp_path):
        """extract() 正确识别顶层导入."""
        test_file = tmp_path / "test_mod.py"
        test_file.write_text("from agent.other import foo\n", encoding="utf-8")

        top_imports = defaultdict(set)
        func_imports = defaultdict(set)
        pep562_modules = set()
        import_locations = defaultdict(list)

        extract(str(test_file), top_imports, func_imports, pep562_modules, import_locations)

        # 模块名由 extract() 基于 relpath 计算 (tmp_path 不在 agent/ 下, 名称含路径前缀)
        # 不硬编码 mod_name, 从结果中获取
        assert len(top_imports) == 1, f"应识别出 1 个模块, 实际: {dict(top_imports)}"
        mod_name = list(top_imports.keys())[0]
        assert "agent.other" in top_imports[mod_name]
        assert len(import_locations[(mod_name, "agent.other")]) == 1

    def test_extract_function_level_import_not_top(self, tmp_path):
        """函数内导入不计入 top_imports."""
        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            "def func():\n    from agent.inner import helper\n    return helper\n",
            encoding="utf-8",
        )

        top_imports = defaultdict(set)
        func_imports = defaultdict(set)
        pep562_modules = set()
        import_locations = defaultdict(list)

        extract(str(test_file), top_imports, func_imports, pep562_modules, import_locations)

        # agent.inner 应在 func_imports 而非 top_imports
        assert not any("agent.inner" in dsts for dsts in top_imports.values())
        assert any("agent.inner" in dsts for dsts in func_imports.values())
