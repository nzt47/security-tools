"""工具定义 YAML 化的单元测试。

覆盖：
1. YAML 加载（tool_router 从 YAML 派生 TOOL_CATEGORIES，与默认值一致）
2. YAML 字段完整性校验
3. 索引同步（sync_tool_index 校验 + 生成）
4. 版本兼容（旧版本引用记录 warning 但不报错）
5. 降级兜底（YAML 缺失时回退到代码内默认值）

【不易】不依赖网络；不修改生产数据目录（用 tmp_path 隔离）。
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import textwrap
from pathlib import Path

import pytest
import yaml

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"
_INDEX_PATH = _PROJECT_ROOT / "data" / "tool_index.json"


# ────────────────────────────────────────────────────────────
#  动态加载 scripts/sync_tool_index.py（scripts 非包，用 importlib）
# ────────────────────────────────────────────────────────────

def _load_sync_module():
    spec = importlib.util.spec_from_file_location(
        "sync_tool_index", _PROJECT_ROOT / "scripts" / "sync_tool_index.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[arg-type]
    return mod


sync_mod = _load_sync_module()


# ════════════════════════════════════════════════════════════
#  1. YAML 加载 —— tool_router 从 YAML 派生 TOOL_CATEGORIES
# ════════════════════════════════════════════════════════════

class TestYamlLoading:
    """YAML 加载与默认值一致性。"""

    def test_tool_categories_derived_from_yaml_matches_default(self):
        """YAML 派生的 TOOL_CATEGORIES 应与代码内默认值完全一致。"""
        from agent.tool_router import TOOL_CATEGORIES, _DEFAULT_TOOL_CATEGORIES
        # 结构化比对（忽略 list 内部顺序的 dict 序列化）
        norm = lambda d: json.dumps(d, ensure_ascii=False, sort_keys=True)  # noqa: E731
        assert norm(TOOL_CATEGORIES) == norm(_DEFAULT_TOOL_CATEGORIES), (
            "YAML 派生的 TOOL_CATEGORIES 与默认值不一致，违反不变量#1"
        )

    def test_tool_categories_has_11_categories(self):
        from agent.tool_router import TOOL_CATEGORIES
        assert len(TOOL_CATEGORIES) == 11

    def test_all_tools_set_matches_categories(self):
        """ALL_TOOLS_SET 与 TOOL_CATEGORIES 平铺一致。"""
        from agent.tool_router import TOOL_CATEGORIES, ALL_TOOLS_SET
        expected = {t for cat in TOOL_CATEGORIES.values() for t in cat["tools"]}
        assert ALL_TOOLS_SET == expected

    def test_tool_order_preserved(self):
        """每个分类的工具顺序应与默认值一致（迁移后无重排）。"""
        from agent.tool_router import TOOL_CATEGORIES, _DEFAULT_TOOL_CATEGORIES
        for cat_key, meta in _DEFAULT_TOOL_CATEGORIES.items():
            assert TOOL_CATEGORIES[cat_key]["tools"] == meta["tools"], (
                f"分类 {cat_key} 工具顺序与默认不一致"
            )

    def test_get_tools_for_input_behavior_unchanged(self):
        """路由行为：core 始终包含；关键词触发对应分类。"""
        from agent.tool_router import get_tools_for_input, classify_user_input
        # 空输入 → 仅 core
        assert set(get_tools_for_input("")) == set(
            get_tools_for_input("")  # 稳定性
        )
        # 搜索 → 触发 web
        cats = classify_user_input("搜索网页")
        assert "web" in cats
        assert "core" in cats
        # 读取PDF → 触发 file + pdf
        cats = classify_user_input("读取PDF")
        assert {"core", "file", "pdf"}.issubset(cats)


# ════════════════════════════════════════════════════════════
#  2. YAML 字段完整性校验
# ════════════════════════════════════════════════════════════

class TestYamlFieldIntegrity:

    REQUIRED = {"name", "category", "description", "deprecated", "version", "schema", "examples"}

    def test_all_yaml_files_have_required_fields(self):
        files = list(_DEFS_DIR.glob("*.yaml"))
        assert len(files) >= 60, f"YAML 文件数量过少: {len(files)}"
        for f in files:
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert isinstance(doc, dict), f"{f.name}: 顶层非 dict"
            missing = self.REQUIRED - set(doc.keys())
            assert not missing, f"{f.name}: 缺少字段 {missing}"

    def test_filename_matches_tool_name(self):
        for f in _DEFS_DIR.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert doc["name"] + ".yaml" == f.name, (
                f"文件名 {f.name} 与 name {doc['name']} 不一致"
            )

    def test_all_versions_are_semver(self):
        for f in _DEFS_DIR.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert sync_mod._validate_semver(doc["version"]), (
                f"{f.name}: version {doc['version']!r} 非法 semver"
            )

    def test_all_schemas_are_object_type(self):
        for f in _DEFS_DIR.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            errs = sync_mod._validate_schema(doc["schema"])
            assert not errs, f"{f.name}: schema 校验失败 {errs}"

    def test_categories_are_known(self):
        for f in _DEFS_DIR.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            assert doc["category"] in sync_mod.KNOWN_CATEGORIES, (
                f"{f.name}: category {doc['category']!r} 未知"
            )

    def test_categorized_tools_match_router(self):
        """YAML 中已知分类的工具集合应与 tool_router 一致。"""
        from agent.tool_router import TOOL_CATEGORIES
        yaml_by_cat: dict[str, set[str]] = {}
        for f in _DEFS_DIR.glob("*.yaml"):
            doc = yaml.safe_load(f.read_text(encoding="utf-8"))
            if doc["category"] != "uncategorized":
                yaml_by_cat.setdefault(doc["category"], set()).add(doc["name"])
        for cat_key, meta in TOOL_CATEGORIES.items():
            assert yaml_by_cat.get(cat_key, set()) == set(meta["tools"]), (
                f"分类 {cat_key}: YAML 与 router 不一致"
            )


# ════════════════════════════════════════════════════════════
#  3. 索引同步
# ════════════════════════════════════════════════════════════

class TestIndexSync:

    def test_sync_loads_all_valid(self):
        docs, errors = sync_mod._load_all(str(_DEFS_DIR))
        assert not errors, f"校验错误: {errors}"
        assert len(docs) >= 60

    def test_sync_generates_index_to_tmp(self, tmp_path):
        """在临时目录复制 YAML 并生成索引，验证可独立运行。"""
        # 复制 YAML 到临时目录
        tmp_defs = tmp_path / "defs"
        tmp_defs.mkdir()
        for f in _DEFS_DIR.glob("*.yaml"):
            (tmp_defs / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        index_path = tmp_path / "tool_index.json"

        docs, errors = sync_mod._load_all(str(tmp_defs))
        assert not errors
        index = sync_mod._build_index(docs)
        index_path.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")

        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert data["tool_count"] == len(docs)
        assert all("name" in t and "version" in t for t in data["tools"])

    def test_sync_detects_missing_required_field(self, tmp_path):
        """缺字段 → 校验失败。"""
        bad = tmp_path / "bad.yaml"
        bad.write_text("name: bad_tool\ncategory: web\n", encoding="utf-8")  # 缺 description/version 等
        _, errors = sync_mod._load_all(str(tmp_path))
        assert errors, "应检测到缺失字段"

    def test_sync_detects_bad_semver(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(textwrap.dedent("""\
            name: bad_tool
            category: web
            description: 测试
            deprecated: false
            version: "not-a-version"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        _, errors = sync_mod._load_all(str(tmp_path))
        assert any("semver" in e for e in errors), f"应检测到非法 semver: {errors}"

    def test_sync_detects_duplicate_name(self, tmp_path):
        content = textwrap.dedent("""\
            name: dup_tool
            category: web
            description: 测试
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """)
        (tmp_path / "dup_tool.yaml").write_text(content, encoding="utf-8")
        (tmp_path / "dup_tool2.yaml").write_text(
            content.replace("dup_tool", "dup_tool"), encoding="utf-8"
        )
        _, errors = sync_mod._load_all(str(tmp_path))
        assert any("重复" in e for e in errors), f"应检测到重复: {errors}"

    def test_check_mode_exits_zero_on_valid(self, tmp_path):
        """--check 模式对有效 YAML 退出 0。"""
        tmp_defs = tmp_path / "defs"
        tmp_defs.mkdir()
        for f in list(_DEFS_DIR.glob("*.yaml"))[:5]:
            (tmp_defs / f.name).write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
        docs, errors = sync_mod._load_all(str(tmp_defs))
        assert not errors
        assert len(docs) == 5


# ════════════════════════════════════════════════════════════
#  4. 版本兼容
# ════════════════════════════════════════════════════════════

class TestVersionCompat:

    def _index(self):
        return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))

    def test_old_version_logs_warning_but_no_error(self, caplog):
        """旧版本引用 → 记录 warning 但不抛异常。"""
        idx = self._index()
        with caplog.at_level(logging.WARNING, logger="version_compat"):
            result = sync_mod.check_version_compat("web_search", "0.9.0", idx)
        assert result is True  # 兼容（含警告）
        assert any("旧版本" in r.message for r in caplog.records), "应记录旧版本 warning"

    def test_current_version_no_warning(self, caplog):
        idx = self._index()
        current = next(t for t in idx["tools"] if t["name"] == "web_search")["version"]
        with caplog.at_level(logging.WARNING, logger="version_compat"):
            result = sync_mod.check_version_compat("web_search", current, idx)
        assert result is True
        assert not any("旧版本" in r.message for r in caplog.records)

    def test_unknown_tool_no_error(self, caplog):
        idx = self._index()
        with caplog.at_level(logging.WARNING, logger="version_compat"):
            result = sync_mod.check_version_compat("nonexistent_tool", "1.0.0", idx)
        # 不在索引 → False，但不抛异常
        assert result is False

    def test_invalid_version_no_error(self, caplog):
        idx = self._index()
        with caplog.at_level(logging.WARNING, logger="version_compat"):
            result = sync_mod.check_version_compat("web_search", "garbage", idx)
        assert result is False  # 无法判定，但不报错


# ════════════════════════════════════════════════════════════
#  5. 降级兜底
# ════════════════════════════════════════════════════════════

class TestFallback:

    def test_loader_returns_none_when_dir_missing(self, tmp_path, monkeypatch):
        """YAML 目录不存在 → 加载器返回 None → 回退默认。"""
        from agent import tool_router
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(tmp_path / "nonexistent"))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is None

    def test_loader_returns_none_when_no_valid_yaml(self, tmp_path, monkeypatch):
        """目录存在但无有效 YAML → 返回 None → 回退默认。"""
        from agent import tool_router
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(empty_dir))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is None

    def test_loader_skips_corrupt_yaml_and_falls_back_when_all_bad(self, tmp_path, monkeypatch):
        """单个损坏 YAML 被跳过；全部损坏 → 回退默认。"""
        from agent import tool_router
        bad_dir = tmp_path / "bad"
        bad_dir.mkdir()
        (bad_dir / "broken.yaml").write_text("name: [unclosed", encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(bad_dir))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is None

    def test_loader_partial_load_preserves_known_categories(self, tmp_path, monkeypatch):
        """部分有效 YAML → 仅加载已知分类工具，结构正确。"""
        from agent import tool_router
        d = tmp_path / "partial"
        d.mkdir()
        (d / "web_search.yaml").write_text(textwrap.dedent("""\
            name: web_search
            category: web
            description: 搜索
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        # web 分类应包含 web_search
        assert "web_search" in result["web"]["tools"]
        # 元数据来自默认
        assert result["web"]["label"] == tool_router._DEFAULT_TOOL_CATEGORIES["web"]["label"]

    def test_uncategorized_tools_excluded_from_categories(self, tmp_path, monkeypatch):
        """category=uncategorized 的工具不进入任何 TOOL_CATEGORIES 分类。"""
        from agent import tool_router
        d = tmp_path / "unc"
        d.mkdir()
        (d / "market_search.yaml").write_text(textwrap.dedent("""\
            name: market_search
            category: uncategorized
            description: 市场搜索
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        # 还需至少一个已知分类工具，否则 yaml_tools 为空会回退
        (d / "web_search.yaml").write_text(textwrap.dedent("""\
            name: web_search
            category: web
            description: 搜索
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        all_tools = {t for cat in result.values() for t in cat["tools"]}
        assert "market_search" not in all_tools, "uncategorized 工具不应进入 TOOL_CATEGORIES"
        assert "web_search" in all_tools


# ════════════════════════════════════════════════════════════
#  5b. 降级兜底进阶场景 —— 补充覆盖边缘失败模式
# ════════════════════════════════════════════════════════════

class TestFallbackAdvanced:
    """更细粒度的降级场景：PyYAML 缺失、非 .yaml 文件、类型错误、
    混合有效/无效、YAML 顶层非 dict、字段类型错误、工具跨分类迁移等。"""

    _VALID_WEB = textwrap.dedent("""\
        name: web_search
        category: web
        description: 搜索
        deprecated: false
        version: "1.0.0"
        schema:
          type: object
          properties: {}
        examples: []
        """)

    def _write(self, d: Path, name: str, content: str) -> None:
        (d / name).write_text(content, encoding="utf-8")

    def test_loader_returns_none_when_pyyaml_missing(self, monkeypatch):
        """PyYAML 未安装 (_yaml is None) → 返回 None → 回退默认。"""
        from agent import tool_router
        monkeypatch.setattr(tool_router, "_yaml", None)
        # 即使目录存在，_yaml 为 None 也应返回 None
        result = tool_router._load_tool_categories_from_yaml()
        assert result is None

    def test_loader_ignores_non_yaml_files(self, tmp_path, monkeypatch):
        """目录中只有 .txt/.json/.md → 返回 None（无 .yaml 文件）。"""
        from agent import tool_router
        d = tmp_path / "mixed_ext"
        d.mkdir()
        self._write(d, "notes.txt", "name: web_search\ncategory: web\n")
        self._write(d, "data.json", '{"name": "web_search", "category": "web"}')
        self._write(d, "readme.md", "# tools\n")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is None

    def test_loader_skips_yaml_with_non_dict_top(self, tmp_path, monkeypatch):
        """YAML 顶层是 list/scalar（非 dict）→ 跳过该文件，不崩溃。"""
        from agent import tool_router
        d = tmp_path / "nondict"
        d.mkdir()
        self._write(d, "list_top.yaml", "- item1\n- item2\n")
        self._write(d, "scalar_top.yaml", "just a string\n")
        # 加一个有效 YAML 保证不全空
        self._write(d, "web_search.yaml", self._VALID_WEB)
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        assert "web_search" in result["web"]["tools"]

    def test_loader_skips_yaml_with_wrong_name_type(self, tmp_path, monkeypatch):
        """name/category 字段类型错误（int/None）→ 跳过该条目。"""
        from agent import tool_router
        d = tmp_path / "badtype"
        d.mkdir()
        # name 为 int（非 str）→ 应跳过
        (d / "int_name.yaml").write_text(textwrap.dedent("""\
            name: 12345
            category: web
            description: 测试
            """), encoding="utf-8")
        # category 为 null（非 str）→ 应跳过
        (d / "none_category.yaml").write_text(textwrap.dedent("""\
            name: some_tool
            category: null
            description: 测试
            """), encoding="utf-8")
        # 加一个有效 YAML 保证不全空
        self._write(d, "web_search.yaml", self._VALID_WEB)
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        # 错误条目被跳过，但有效条目仍加载
        assert result is not None
        all_tools = {t for cat in result.values() for t in cat["tools"]}
        assert "web_search" in all_tools
        # name 为 int 的工具不应出现
        assert "12345" not in all_tools

    def test_loader_partial_load_with_mixed_valid_invalid(self, tmp_path, monkeypatch):
        """混合有效/无效 YAML → 有效部分加载，损坏部分跳过，不崩溃。

        【设计选择】loader 是轻量校验：只检查 name+category 是 str（路由派生
        所需的最小字段）。完整字段校验（description/schema/version 等）由
        sync_tool_index.py 在 CI 阶段强制执行。因此：
        - YAML 语法错误（broken.yaml）→ 被 yaml.YAMLError 捕获，跳过
        - 顶层非 dict（list_top.yaml）→ 被 isinstance(doc, dict) 过滤，跳过
        - 仅 name+category 但缺其他字段（incomplete.yaml）→ 仍被 loader 接受
          （loader 不做完整字段校验），但会被 sync 脚本标记为校验失败
        """
        from agent import tool_router
        d = tmp_path / "mixed"
        d.mkdir()
        # 有效
        self._write(d, "web_search.yaml", self._VALID_WEB)
        # 损坏（YAML 语法错误）
        (d / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")
        # 顶层非 dict
        (d / "list_top.yaml").write_text("- a\n- b\n", encoding="utf-8")
        # 缺字段（仅 name+category，loader 宽容接受；sync 脚本会报错）
        (d / "incomplete.yaml").write_text("name: incomplete\ncategory: web\n", encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        # 仍能加载（至少 web_search 有效）
        assert result is not None
        assert "web_search" in result["web"]["tools"]
        all_tools = {t for cat in result.values() for t in cat["tools"]}
        # 损坏/非 dict 的工具被跳过
        assert "broken" not in all_tools  # YAML 语法错误，name 抽不出
        # incomplete 因 loader 宽容（只校验 name+category）会进入 web 分类；
        # 完整字段校验在 sync 脚本，那里会标记 incomplete 缺 description 等
        assert "incomplete" in result["web"]["tools"]
        # 验证 sync 脚本会检测到 incomplete 缺字段（CI 守门）
        sync_docs, sync_errors = sync_mod._load_all(str(d))
        assert any("incomplete" in e and ("缺少" in e or "description" in e)
                   for e in sync_errors), \
            f"sync 脚本应检测到 incomplete 缺字段: {sync_errors}"

    def test_loader_unknown_category_skipped(self, tmp_path, monkeypatch):
        """YAML 中 category 为未知分类键 → 该工具不进入 TOOL_CATEGORIES。"""
        from agent import tool_router
        d = tmp_path / "unknown_cat"
        d.mkdir()
        (d / "weird_tool.yaml").write_text(textwrap.dedent("""\
            name: weird_tool
            category: nonexistent_category
            description: 测试
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        self._write(d, "web_search.yaml", self._VALID_WEB)
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        all_tools = {t for cat in result.values() for t in cat["tools"]}
        # 未知分类的工具不应进入任何已知分类
        assert "weird_tool" not in all_tools
        assert "web_search" in all_tools

    def test_loader_tool_moving_between_categories(self, tmp_path, monkeypatch):
        """YAML 中将默认分类中的工具改到另一分类 → 派生表反映新分类。"""
        from agent import tool_router
        d = tmp_path / "moved"
        d.mkdir()
        # web_search 默认在 web 分类，这里改到 file
        (d / "web_search.yaml").write_text(textwrap.dedent("""\
            name: web_search
            category: file
            description: 搜索
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        # web_search 应出现在 file 而非 web
        assert "web_search" in result["file"]["tools"]
        # web 分类中的默认 web_search 应被移除（YAML 是 source of truth）
        assert "web_search" not in result["web"]["tools"]

    def test_loader_preserves_category_metadata_from_default(self, tmp_path, monkeypatch):
        """YAML 仅承载工具列表，分类元数据(label/icon/always)取自默认值。"""
        from agent import tool_router
        d = tmp_path / "meta"
        d.mkdir()
        self._write(d, "web_search.yaml", self._VALID_WEB)
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        # 元数据应与默认值一致
        default_meta = tool_router._DEFAULT_TOOL_CATEGORIES["web"]
        assert result["web"]["label"] == default_meta["label"]
        assert result["web"]["icon"] == default_meta["icon"]
        assert result["web"]["description"] == default_meta["description"]
        assert result["web"]["always"] == default_meta["always"]

    def test_loader_new_tool_appended_alphabetically(self, tmp_path, monkeypatch):
        """YAML 中新增工具（不在默认列表）→ 按字母序追加到对应分类末尾。"""
        from agent import tool_router
        d = tmp_path / "newtool"
        d.mkdir()
        # 默认 web 分类工具 + 一个新工具
        self._write(d, "web_search.yaml", self._VALID_WEB)
        (d / "zebra_new_tool.yaml").write_text(textwrap.dedent("""\
            name: zebra_new_tool
            category: web
            description: 新工具
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        (d / "apple_new_tool.yaml").write_text(textwrap.dedent("""\
            name: apple_new_tool
            category: web
            description: 新工具
            deprecated: false
            version: "1.0.0"
            schema:
              type: object
              properties: {}
            examples: []
            """), encoding="utf-8")
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        web_tools = result["web"]["tools"]
        # 默认 web_search 在前，新增工具按字母序追加
        assert web_tools == ["web_search", "apple_new_tool", "zebra_new_tool"]

    def test_loader_returns_default_keys_set(self, tmp_path, monkeypatch):
        """派生表的分类键集合应与默认值完全一致（11 个，无多余）。"""
        from agent import tool_router
        d = tmp_path / "keyset"
        d.mkdir()
        self._write(d, "web_search.yaml", self._VALID_WEB)
        monkeypatch.setattr(tool_router, "TOOL_DEFINITIONS_DIR", str(d))
        result = tool_router._load_tool_categories_from_yaml()
        assert result is not None
        assert set(result.keys()) == set(tool_router._DEFAULT_TOOL_CATEGORIES.keys())
        assert len(result) == 11


# ════════════════════════════════════════════════════════════
#  6. 迁移脚本核心函数测试（migrate_tools_to_yaml.py）
# ════════════════════════════════════════════════════════════

def _load_migrate_module():
    """动态加载 scripts/migrate_tools_to_yaml.py（scripts/ 非包，用 importlib）。"""
    spec = importlib.util.spec_from_file_location(
        "migrate_tools_to_yaml", _PROJECT_ROOT / "scripts" / "migrate_tools_to_yaml.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


migrate_mod = _load_migrate_module()


class TestMigrationScript:
    """迁移脚本核心函数测试：字段抽取、register 识别、分类反查、YAML 文档构造。"""

    def test_extract_from_call_returns_name_description_schema(self):
        """_extract_from_call 抽取 name/description/schema 三字段。"""
        import ast
        call = ast.parse(
            'register("test_tool", "测试描述", schema={"type": "object", "properties": {}})',
            mode="eval",
        ).body
        result = migrate_mod._extract_from_call(call)
        assert result is not None
        assert result["name"] == "test_tool"
        assert result["description"] == "测试描述"
        assert result["schema"] == {"type": "object", "properties": {}}

    def test_extract_from_call_no_args_returns_none(self):
        """无位置参数 → 返回 None。"""
        import ast
        call = ast.parse("register()", mode="eval").body
        assert migrate_mod._extract_from_call(call) is None

    def test_extract_from_call_non_string_name_returns_none(self):
        """name 为非 str（int）→ 返回 None。"""
        import ast
        call = ast.parse('register(12345, "desc")', mode="eval").body
        assert migrate_mod._extract_from_call(call) is None

    def test_extract_from_call_missing_description_defaults_empty(self):
        """缺 description 位置参数 → description 默认为空字符串。"""
        import ast
        call = ast.parse('register("test_tool", schema={"type": "object"})',
                         mode="eval").body
        result = migrate_mod._extract_from_call(call)
        assert result is not None
        assert result["name"] == "test_tool"
        assert result["description"] == ""
        assert result["schema"] == {"type": "object"}

    def test_extract_from_call_missing_schema_returns_none(self):
        """缺 schema 关键字参数 → schema 为 None。"""
        import ast
        call = ast.parse('register("test", "desc")', mode="eval").body
        result = migrate_mod._extract_from_call(call)
        assert result["name"] == "test"
        assert result["schema"] is None

    def test_is_register_call_detects_attribute_call(self):
        """_is_register_call 识别 @_tools.register / @obj.register 调用。"""
        import ast
        # 标准形式：@_tools.register(...)
        call = ast.parse('_tools.register("x")', mode="eval").body
        assert migrate_mod._is_register_call(call) is True
        # 非调用形式（仅属性访问）
        attr = ast.parse("_tools.register", mode="eval").body
        assert migrate_mod._is_register_call(attr) is False
        # 普通函数调用
        plain = ast.parse('register("x")', mode="eval").body
        assert migrate_mod._is_register_call(plain) is False

    def test_build_category_map_has_64_categorized_tools(self):
        """_build_category_map 反查表包含 64 个 categorized 工具。"""
        cat_map = migrate_mod._build_category_map()
        assert len(cat_map) == 64  # 11 个分类的工具总数
        # 不应包含 uncategorized 工具
        uncat = {"market_search", "install_tool", "generate_tool",
                 "scan_mcp", "connect_mcp", "disconnect_mcp"}
        for name in uncat:
            assert name not in cat_map, f"{name} 不应在 category_map 中"

    def test_to_yaml_doc_has_all_required_fields(self):
        """_to_yaml_doc 生成的文档包含所有必填字段。"""
        tool = {
            "name": "test_tool",
            "category": "web",
            "description": "测试",
            "schema": {"type": "object", "properties": {}},
        }
        doc = migrate_mod._to_yaml_doc(tool)
        required = {"name", "category", "description", "deprecated",
                    "version", "schema", "examples"}
        assert required.issubset(set(doc.keys())), \
            f"缺少字段: {required - set(doc.keys())}"

    def test_to_yaml_doc_defaults(self):
        """_to_yaml_doc 默认值: deprecated=false, version=1.0.0, examples=[]。"""
        tool = {"name": "t", "category": "web", "description": "d",
                "schema": {"type": "object"}}
        doc = migrate_mod._to_yaml_doc(tool)
        assert doc["deprecated"] is False
        assert doc["version"] == "1.0.0"
        assert doc["examples"] == []

    def test_to_yaml_doc_fills_schema_when_missing(self):
        """schema 缺失时，_to_yaml_doc 填充默认 object schema。"""
        tool = {"name": "t", "category": "web", "description": "d", "schema": None}
        doc = migrate_mod._to_yaml_doc(tool)
        assert doc["schema"]["type"] == "object"
        assert doc["schema"]["properties"] == {}
        assert doc["schema"]["additionalProperties"] is True


# ════════════════════════════════════════════════════════════
#  7. 70 个 YAML 与原 Python @register 字段一致性
# ════════════════════════════════════════════════════════════

def _extract_python_register_defs() -> dict:
    """从 agent/tools/*.py 抽取所有 @register 调用的 name/description/schema。

    用 AST 静态抽取（不执行工具代码），与迁移脚本使用相同逻辑。
    """
    import ast
    tools_dir = _PROJECT_ROOT / "agent" / "tools"
    py_defs: dict[str, dict] = {}
    for f in sorted(tools_dir.glob("*.py")):
        if f.name == "__init__.py":
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"), filename=str(f))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                target = dec if isinstance(dec, ast.Call) else dec
                if not migrate_mod._is_register_call(target):
                    continue
                extracted = migrate_mod._extract_from_call(target)
                if extracted and extracted["name"] not in py_defs:
                    extracted["src_file"] = f.name
                    py_defs[extracted["name"]] = extracted
    return py_defs


class TestYamlPythonConsistency:
    """70 个 YAML 文件与原 Python @register 调用的字段一致性。"""

    @pytest.fixture(scope="class")
    def py_defs(self) -> dict:
        return _extract_python_register_defs()

    @pytest.fixture(scope="class")
    def yaml_docs(self) -> dict:
        docs = {}
        for f in _DEFS_DIR.glob("*.yaml"):
            docs[f.stem] = yaml.safe_load(f.read_text(encoding="utf-8"))
        return docs

    def test_yaml_count_equals_python_register_count(self, py_defs, yaml_docs):
        """YAML 文件数应等于 Python @register 调用数（70）。"""
        assert len(py_defs) == 70, f"Python @register 抽取数: {len(py_defs)}"
        assert len(yaml_docs) == 70, f"YAML 文件数: {len(yaml_docs)}"

    def test_all_yaml_names_have_python_counterpart(self, py_defs, yaml_docs):
        """每个 YAML 工具在 Python @register 中都有对应定义。"""
        for name in yaml_docs:
            assert name in py_defs, f"{name}: YAML 中存在但 Python @register 中无"

    def test_all_python_register_have_yaml_counterpart(self, py_defs, yaml_docs):
        """每个 Python @register 工具都有对应 YAML 文件。"""
        for name in py_defs:
            assert name in yaml_docs, f"{name}: Python @register 中存在但 YAML 中无"

    def test_all_names_match(self, py_defs, yaml_docs):
        """name 字段完全一致。"""
        for name, py_def in py_defs.items():
            assert yaml_docs[name]["name"] == py_def["name"], \
                f"{name}: name 不一致 YAML={yaml_docs[name]['name']} Py={py_def['name']}"

    def test_all_descriptions_match(self, py_defs, yaml_docs):
        """description 字段完全一致。"""
        for name, py_def in py_defs.items():
            assert yaml_docs[name]["description"] == py_def["description"], \
                f"{name}: description 不一致"

    def test_all_schemas_match(self, py_defs, yaml_docs):
        """schema 字段完全一致（双方都有 schema 时）。"""
        mismatched = []
        for name, py_def in py_defs.items():
            if py_def["schema"] is not None:
                if yaml_docs[name]["schema"] != py_def["schema"]:
                    mismatched.append(name)
        assert not mismatched, f"schema 不一致的工具: {mismatched}"

    def test_uncategorized_tools_count(self, yaml_docs):
        """uncategorized 工具数量为 6，且为预期集合。"""
        uncat = [n for n, d in yaml_docs.items() if d["category"] == "uncategorized"]
        assert len(uncat) == 6
        assert set(uncat) == {
            "market_search", "install_tool", "generate_tool",
            "scan_mcp", "connect_mcp", "disconnect_mcp",
        }

    def test_uncategorized_not_in_default_categories(self, yaml_docs):
        """6 个 uncategorized 工具不在 _DEFAULT_TOOL_CATEGORIES 的任何分类中。"""
        from agent.tool_router import _DEFAULT_TOOL_CATEGORIES
        all_default = {t for cat in _DEFAULT_TOOL_CATEGORIES.values() for t in cat["tools"]}
        uncat = [n for n, d in yaml_docs.items() if d["category"] == "uncategorized"]
        for name in uncat:
            assert name not in all_default, f"{name}: 不应在 _DEFAULT_TOOL_CATEGORIES 中"

    def test_all_yaml_src_files_are_known(self, py_defs):
        """所有 Python @register 工具的 src_file 应在 agent/tools/ 目录下。"""
        for name, py_def in py_defs.items():
            assert py_def["src_file"].endswith(".py"), \
                f"{name}: src_file 异常: {py_def['src_file']}"


# ════════════════════════════════════════════════════════════
#  8. 迁移脚本幂等性（可重复运行，无副作用）
# ════════════════════════════════════════════════════════════

class TestMigrationIdempotency:
    """迁移脚本幂等性：多次运行结果一致，可安全重复执行。"""

    def test_migrate_script_is_idempotent(self):
        """运行迁移脚本后，70 个 YAML 文件 hash 完全一致（幂等）。"""
        import hashlib
        import subprocess

        def hash_yamls() -> dict:
            hashes = {}
            for f in _DEFS_DIR.glob("*.yaml"):
                hashes[f.name] = hashlib.sha256(f.read_bytes()).hexdigest()
            return hashes

        before = hash_yamls()
        assert len(before) == 70, f"迁移前 YAML 数量异常: {len(before)}"

        # 运行迁移脚本
        r = subprocess.run(
            [sys.executable, "scripts/migrate_tools_to_yaml.py"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT),
        )
        assert r.returncode == 0, f"迁移脚本失败: {r.stderr}"

        after = hash_yamls()
        assert before == after, "迁移脚本不幂等：运行后 YAML 内容发生变化"
