"""
模块依赖图生成器单元测试
覆盖：初始化、import 解析、跨层识别、白名单加载、JSON/Mermaid 输出、异常处理
目标覆盖率：≥80%
"""
import json
import os
import tempfile
from pathlib import Path
from textwrap import dedent

import pytest

from agent.observability.dependency_graph import (
    DependencyGraphBuilder,
    DependencyEdge,
    DependencyNode,
    DependencyGraphError,
    GraphStats,
    CROSS_LAYER_VIOLATIONS,
    DEFAULT_LAYER_MAPPING,
)


# ── 测试夹具 ──────────────────────────────────────────────────


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """构造一个最小化的测试项目结构

    结构：
        agent/
        ├── __init__.py
        ├── orchestrator/
        │   ├── __init__.py
        │   └── core.py        # import agent.data.repo（违规）
        ├── cognitive/
        │   ├── __init__.py
        │   └── loop.py        # from agent.server_routes import api（违规）
        ├── data/
        │   ├── __init__.py
        │   └── repo.py        # 普通模块
        ├── server_routes/
        │   ├── __init__.py
        │   └── api.py         # 普通模块
        ├── tools/
        │   ├── __init__.py
        │   └── helper.py      # import os, import agent.utils.x（跨层）
        ├── utils/
        │   ├── __init__.py
        │   └── x.py
        └── lazy_loader_async.py  # 含 register() 调用
    """
    agent = tmp_path / "agent"
    (agent / "orchestrator").mkdir(parents=True)
    (agent / "cognitive").mkdir(parents=True)
    (agent / "data").mkdir(parents=True)
    (agent / "server_routes").mkdir(parents=True)
    (agent / "tools").mkdir(parents=True)
    (agent / "utils").mkdir(parents=True)

    # __init__.py
    for sub in ["orchestrator", "cognitive", "data", "server_routes", "tools", "utils"]:
        (agent / sub / "__init__.py").write_text("", encoding="utf-8")
    (agent / "__init__.py").write_text("", encoding="utf-8")

    # orchestrator/core.py — 违规：orchestrator → dao
    (agent / "orchestrator" / "core.py").write_text(
        dedent(
            """
            import json
            from agent.data.repo import Repository

            def run():
                return Repository()
            """
        ),
        encoding="utf-8",
    )

    # cognitive/loop.py — 违规：cognitive → server_routes
    (agent / "cognitive" / "loop.py").write_text(
        dedent(
            """
            import os
            from agent.server_routes.api import router

            def loop():
                return router
            """
        ),
        encoding="utf-8",
    )

    # data/repo.py
    (agent / "data" / "repo.py").write_text(
        "class Repository: pass\n",
        encoding="utf-8",
    )

    # server_routes/api.py
    (agent / "server_routes" / "api.py").write_text(
        "router = None\n",
        encoding="utf-8",
    )

    # tools/helper.py — 跨层调用 utils（同层，不违规）
    (agent / "tools" / "helper.py").write_text(
        dedent(
            """
            import agent.utils.x
            from agent.utils.x import helper_func
            __import__('agent.utils.x')
            """
        ),
        encoding="utf-8",
    )

    # utils/x.py
    (agent / "utils" / "x.py").write_text(
        "def helper_func(): pass\n",
        encoding="utf-8",
    )

    # lazy_loader_async.py — 含 register 调用
    (agent / "lazy_loader_async.py").write_text(
        dedent(
            """
            loader.register('memory', load_func)
            loader.register('ocr', load_func)
            loader.register('lifetrace', load_func)
            """
        ),
        encoding="utf-8",
    )

    return tmp_path


# ── 初始化测试 ──────────────────────────────────────────────────


class TestInitialization:
    """测试构建器初始化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_default_params(self):
        """默认参数初始化应成功"""
        builder = DependencyGraphBuilder(root_dir="agent")
        assert builder.root_dir.exists()
        assert builder.trace_id is not None
        assert len(builder.trace_id) == 16

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_custom_trace_id(self):
        """自定义 trace_id 应被使用"""
        builder = DependencyGraphBuilder(root_dir="agent", trace_id="test123")
        assert builder.trace_id == "test123"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_raises_on_missing_root(self, tmp_path: Path):
        """根目录不存在应抛出 DependencyGraphError"""
        with pytest.raises(DependencyGraphError) as exc_info:
            DependencyGraphBuilder(root_dir=str(tmp_path / "nonexistent"))
        assert exc_info.value.error_code == "DEP_GRAPH_ROOT_NOT_FOUND"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_init_with_custom_layer_mapping(self, sample_project: Path):
        """自定义层级映射应被使用"""
        custom_mapping = {"orchestrator": "my_orchestrator"}
        builder = DependencyGraphBuilder(
            root_dir=str(sample_project / "agent"),
            layer_mapping=custom_mapping,
        )
        assert builder.layer_mapping == custom_mapping


# ── import 解析测试 ──────────────────────────────────────────────


class TestImportParsing:
    """测试 import 关系解析"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_parse_import_statement(self, sample_project: Path):
        """import 语句应被正确解析"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        # data/repo.py 应作为 orchestrator 的目标
        targets = {e.target for e in builder.edges}
        assert "agent.data.repo" in targets

    @pytest.mark.unit
    @pytest.mark.p0
    def test_parse_from_import_statement(self, sample_project: Path):
        """from...import 语句应被正确解析"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        targets = {e.target for e in builder.edges}
        assert "agent.server_routes.api" in targets

    @pytest.mark.unit
    @pytest.mark.p0
    def test_parse_dynamic_import(self, sample_project: Path):
        """__import__() 应被识别为动态 import"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        dynamic_edges = [e for e in builder.edges if e.is_dynamic]
        assert len(dynamic_edges) >= 1
        assert any(e.import_type == "dynamic" for e in dynamic_edges)

    @pytest.mark.unit
    @pytest.mark.p1
    def test_importlib_import_module_detected(self, tmp_path: Path):
        """importlib.import_module() 应被识别为动态 import"""
        agent = tmp_path / "agent"
        (agent / "data").mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        (agent / "data" / "__init__.py").write_text("", encoding="utf-8")
        (agent / "data" / "m.py").write_text("", encoding="utf-8")
        (agent / "core.py").write_text(
            "import importlib\n"
            "m = importlib.import_module('agent.data.m')\n",
            encoding="utf-8",
        )
        builder = DependencyGraphBuilder(root_dir=str(agent))
        builder.build()
        dynamic_edges = [e for e in builder.edges if e.is_dynamic]
        assert len(dynamic_edges) >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_non_project_imports_filtered(self, sample_project: Path):
        """非项目内 import（标准库、第三方）应被过滤"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        targets = {e.target for e in builder.edges}
        # os、json 等标准库不应出现
        assert "os" not in targets
        assert "json" not in targets

    @pytest.mark.unit
    @pytest.mark.p1
    def test_relative_import_skipped(self, tmp_path: Path):
        """纯相对 import（from . import x）应被跳过"""
        agent = tmp_path / "agent"
        (agent / "pkg").mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        (agent / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        (agent / "pkg" / "a.py").write_text("from . import b\n", encoding="utf-8")
        (agent / "pkg" / "b.py").write_text("", encoding="utf-8")
        builder = DependencyGraphBuilder(root_dir=str(agent))
        builder.build()
        # 纯相对 import 不应产生边（module 为 None）
        assert len(builder.edges) == 0


# ── 跨层调用识别测试 ────────────────────────────────────────────


class TestCrossLayerDetection:
    """测试跨层调用与违规识别"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_orchestrator_to_dao_violation(self, sample_project: Path):
        """orchestrator → dao 应被标记为违规"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        violations = [e for e in builder.edges if e.is_violation]
        assert len(violations) >= 1
        # 应包含 orchestrator → dao 的违规
        orc_to_dao = [
            e for e in violations
            if e.source_layer == "orchestrator" and e.target_layer == "dao"
        ]
        assert len(orc_to_dao) >= 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cognitive_to_server_routes_violation(self, sample_project: Path):
        """cognitive → server_routes 应被标记为违规"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        violations = [e for e in builder.edges if e.is_violation]
        cog_to_routes = [
            e for e in violations
            if e.source_layer == "cognitive" and e.target_layer == "server_routes"
        ]
        assert len(cog_to_routes) >= 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_violation_has_description(self, sample_project: Path):
        """违规边应包含描述"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        violations = [e for e in builder.edges if e.is_violation]
        for v in violations:
            assert v.violation_desc != ""
            assert v.is_cross_layer is True


# ── 白名单加载测试 ──────────────────────────────────────────────


class TestWhitelistLoading:
    """测试 lazy_loader 白名单加载"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_load_whitelist_extracts_modules(self, sample_project: Path):
        """应从 lazy_loader_async.py 提取 register 调用"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        whitelist = builder.load_lazy_loader_whitelist()
        assert "memory" in whitelist
        assert "ocr" in whitelist
        assert "lifetrace" in whitelist

    @pytest.mark.unit
    @pytest.mark.p1
    def test_load_whitelist_missing_file(self, tmp_path: Path):
        """白名单文件不存在时应返回空集合（不抛异常）"""
        agent = tmp_path / "agent"
        agent.mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        (agent / "m.py").write_text("", encoding="utf-8")
        builder = DependencyGraphBuilder(root_dir=str(agent))
        whitelist = builder.load_lazy_loader_whitelist()
        assert whitelist == set()


# ── JSON 输出测试 ──────────────────────────────────────────────


class TestJsonOutput:
    """测试 JSON 格式输出"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_json_structure(self, sample_project: Path):
        """JSON 输出应包含 nodes/edges/stats/trace_id"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        data = builder.to_json()
        assert "trace_id" in data
        assert "root_dir" in data
        assert "nodes" in data
        assert "edges" in data
        assert "stats" in data

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_json_nodes_have_required_fields(self, sample_project: Path):
        """节点应包含必要字段"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        for node in builder.to_json()["nodes"]:
            assert "path" in node
            assert "name" in node
            assert "layer" in node
            assert "in_degree" in node
            assert "out_degree" in node

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_json_edges_have_required_fields(self, sample_project: Path):
        """边应包含必要字段"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        for edge in builder.to_json()["edges"]:
            assert "source" in edge
            assert "target" in edge
            assert "source_layer" in edge
            assert "target_layer" in edge
            assert "import_type" in edge
            assert "line" in edge
            assert "is_cross_layer" in edge
            assert "is_violation" in edge

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_json_is_serializable(self, sample_project: Path):
        """JSON 输出应可序列化"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        # 应不抛异常
        json_str = json.dumps(builder.to_json(), ensure_ascii=False)
        assert len(json_str) > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_json_to_file(self, sample_project: Path, tmp_path: Path):
        """应能写入 JSON 文件"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        out = tmp_path / "nested" / "out" / "graph.json"
        path = builder.write_json_to_file(str(out))
        assert Path(path).exists()
        content = json.loads(out.read_text(encoding="utf-8"))
        assert "nodes" in content


# ── Mermaid 输出测试 ────────────────────────────────────────────


class TestMermaidOutput:
    """测试 Mermaid 拓扑图输出"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_mermaid_has_flowchart(self, sample_project: Path):
        """Mermaid 输出应包含 flowchart 声明"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        mermaid = builder.to_mermaid()
        assert "```mermaid" in mermaid
        assert "flowchart" in mermaid
        assert "```" in mermaid

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_mermaid_has_subgraphs(self, sample_project: Path):
        """Mermaid 应按层分组 subgraph"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        mermaid = builder.to_mermaid()
        assert "subgraph" in mermaid

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_mermaid_marks_violations(self, sample_project: Path):
        """Mermaid 应标记违规边"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        mermaid = builder.to_mermaid()
        # 违规标记
        if builder.stats.violation_edges > 0:
            assert "违规" in mermaid

    @pytest.mark.unit
    @pytest.mark.p0
    def test_to_mermaid_has_stats(self, sample_project: Path):
        """Mermaid 应包含统计信息"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        mermaid = builder.to_mermaid()
        assert "扫描文件数" in mermaid
        assert "模块节点数" in mermaid

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_mermaid_to_file(self, sample_project: Path, tmp_path: Path):
        """应能写入 Mermaid 文件"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        out = tmp_path / "docs" / "graph.md"
        path = builder.write_mermaid_to_file(str(out))
        assert Path(path).exists()
        content = out.read_text(encoding="utf-8")
        assert "flowchart" in content


# ── 健康检查与统计测试 ──────────────────────────────────────────


class TestHealthAndStats:
    """测试健康检查与统计"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_health_before_build(self, sample_project: Path):
        """构建前 health 应返回 uninitialized"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        h = builder.health()
        assert h["status"] == "uninitialized"
        assert h["root_exists"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_health_after_build(self, sample_project: Path):
        """构建后 health 应返回 healthy"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        h = builder.health()
        assert h["status"] == "healthy"
        assert h["nodes_count"] > 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stats_after_build(self, sample_project: Path):
        """构建后统计应正确"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        stats = builder.stats
        assert stats.total_files > 0
        assert stats.total_nodes > 0
        assert stats.total_edges > 0
        assert stats.build_duration_ms > 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stats_layers_populated(self, sample_project: Path):
        """统计应包含各层模块数"""
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        assert "orchestrator" in builder.stats.layers
        assert "dao" in builder.stats.layers


# ── 异常处理测试 ──────────────────────────────────────────────


class TestErrorHandling:
    """测试异常处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_build_raises_on_no_python_files(self, tmp_path: Path):
        """无 Python 文件时应抛出异常"""
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("hello", encoding="utf-8")
        builder = DependencyGraphBuilder(root_dir=str(empty))
        with pytest.raises(DependencyGraphError) as exc_info:
            builder.build()
        assert exc_info.value.error_code == "DEP_GRAPH_NO_PYTHON_FILES"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_syntax_error_does_not_crash_build(self, tmp_path: Path):
        """单个文件语法错误不应阻断整体构建"""
        agent = tmp_path / "agent"
        (agent / "data").mkdir(parents=True)
        (agent / "__init__.py").write_text("", encoding="utf-8")
        (agent / "data" / "__init__.py").write_text("", encoding="utf-8")
        (agent / "data" / "good.py").write_text("x = 1\n", encoding="utf-8")
        (agent / "data" / "bad.py").write_text("def broken(:\n", encoding="utf-8")
        builder = DependencyGraphBuilder(root_dir=str(agent))
        # 不应抛异常
        builder.build()
        assert builder.stats.total_files == 4

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_mermaid_raises_on_invalid_path(self, sample_project: Path):
        """写入失败应抛出 DependencyGraphError"""
        from unittest.mock import patch
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        # mock write_text 抛出 OSError，模拟磁盘满/权限问题
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(DependencyGraphError) as exc_info:
                builder.write_mermaid_to_file("any/path/graph.md")
            assert exc_info.value.error_code == "DEP_GRAPH_WRITE_FAIL"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_write_json_raises_on_failure(self, sample_project: Path):
        """JSON 写入失败应抛出 DependencyGraphError"""
        from unittest.mock import patch
        builder = DependencyGraphBuilder(root_dir=str(sample_project / "agent"))
        builder.build()
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            with pytest.raises(DependencyGraphError) as exc_info:
                builder.write_json_to_file("any/path/graph.json")
            assert exc_info.value.error_code == "DEP_GRAPH_JSON_WRITE_FAIL"


# ── 性能测试 ──────────────────────────────────────────────────


class TestPerformance:
    """测试性能要求"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_full_project_scan_under_5_seconds(self):
        """全项目扫描应在 5 秒内完成"""
        builder = DependencyGraphBuilder(root_dir="agent")
        builder.build()
        assert builder.stats.build_duration_ms < 5000, (
            f"扫描耗时 {builder.stats.build_duration_ms:.2f}ms 超过 5 秒阈值"
        )


# ── 数据类测试 ──────────────────────────────────────────────────


class TestDataclasses:
    """测试 dataclass 序列化"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dependency_edge_to_dict(self):
        """DependencyEdge.to_dict 应返回完整字段"""
        edge = DependencyEdge(
            source="a.b",
            target="c.d",
            source_layer="a",
            target_layer="c",
            import_type="import",
            line=1,
            source_file="a/b.py",
            is_cross_layer=True,
            is_violation=False,
        )
        d = edge.to_dict()
        assert d["source"] == "a.b"
        assert d["is_cross_layer"] is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dependency_node_to_dict(self):
        """DependencyNode.to_dict 应返回完整字段"""
        node = DependencyNode(
            path="a.b", name="b", layer="a", file_path="a/b.py"
        )
        d = node.to_dict()
        assert d["path"] == "a.b"
        assert d["in_degree"] == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_graph_stats_to_dict(self):
        """GraphStats.to_dict 应返回完整字段"""
        stats = GraphStats(total_files=10, total_nodes=5)
        d = stats.to_dict()
        assert d["total_files"] == 10
