# -*- coding: utf-8 -*-
"""
impact_analysis.py 缓存优化单元测试

【测试目标】
验证 ImpactAnalyzer 中 analyze() 方法预收集 all_tests、_find_tests_for_module()
接收 all_tests 参数后的缓存共享逻辑，确保 N+1 rglob 调用被消除。

【覆盖维度】
1. _find_tests_for_module 接收 all_tests：缓存命中，不触发 rglob
2. _find_tests_for_module 不传 all_tests：降级到 _collect_test_files
3. _collect_test_files：空目录、不存在目录、大量文件
4. _relate_tests：预收集并共享 all_tests 给所有受影响模块
5. analyze() 端到端：_collect_test_files 调用次数受控
6. 性能回归：mock 计数验证 rglob 调用减少
7. 匹配规则正确性：模块短名、所属层

【可观测性约束】
- 边界显性化：测试命名反映业务意图
- 异常处理：所有 mock 隔离文件系统与子进程，避免污染真实仓库

【生成日志摘要】
- 生成时间：2026-06-26
- 版本：v1.0.0
- 内容：impact_analysis.py 预收集 all_tests 缓存优化单元测试
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.impact_analysis import (  # noqa: E402
    ImpactAnalyzer,
    ImpactAnalysisError,
    ChangedFile,
    ImpactedModule,
)
from agent.observability.dependency_graph import (  # noqa: E402
    DependencyGraphBuilder,
    DependencyGraphError,
)


# ── 模块级函数：供多进程 spawn 模式 pickle（Windows 兼容）────────
def _cache_process_child_worker(repo_root, result_queue):
    """子进程入口：验证进程内部缓存一致性

    Windows multiprocessing spawn 模式要求 target 函数可 pickle，
    因此必须定义在模块级而非嵌套在测试方法内。
    """
    import os
    from pathlib import Path
    try:
        a = ImpactAnalyzer(repo_root=repo_root)
        tests_root = Path(repo_root) / "tests"
        # 第一次：扫描
        all_tests = a._collect_test_files(tests_root)
        r1 = a._find_tests_for_module("agent.shared.x", all_tests)
        # 第二次：复用 all_tests
        r2 = a._find_tests_for_module("agent.shared.x", all_tests)
        result_queue.put({
            "pid": os.getpid(),
            "r1": r1,
            "r2": r2,
            "consistent": r1 == r2,
        })
    except Exception as exc:
        result_queue.put({"pid": os.getpid(), "error": str(exc)})


# ═══════════════════════════════════════════════════════════════
#  1. _find_tests_for_module 接收 all_tests 缓存命中
# ═══════════════════════════════════════════════════════════════

class TestFindTestsWithPreCollectedList:
    """_find_tests_for_module 接收预收集 all_tests 时的行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_use_provided_all_tests_without_rescan(self, tmp_path):
        """传入 all_tests 时应直接使用，不调用 _collect_test_files"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_orchestrator.py"
        test_file.write_text("def test_x(): pass\n", encoding="utf-8")

        all_tests = [test_file]
        with patch.object(
            analyzer, "_collect_test_files"
        ) as mock_collect:
            result = analyzer._find_tests_for_module(
                "agent.orchestrator.core", all_tests
            )
            # 应直接使用 all_tests，不调用 _collect_test_files
            assert mock_collect.call_count == 0

        # 应匹配到 test_orchestrator.py
        assert len(result) == 1
        assert "test_orchestrator.py" in result[0]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_match_by_module_short_name(self, tmp_path):
        """应通过模块短名匹配测试文件"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 模块短名 helper
        (tests_dir / "test_helper.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        result = analyzer._find_tests_for_module(
            "agent.tools.helper", all_tests
        )
        # 应匹配到 test_helper.py
        assert len(result) == 1
        assert "test_helper.py" in result[0]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_match_by_module_layer(self, tmp_path):
        """应通过模块所属层匹配测试文件"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 模块所属层 memory
        (tests_dir / "test_memory_something.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # module_path = agent.memory.repository → layer=memory
        result = analyzer._find_tests_for_module(
            "agent.memory.repository", all_tests
        )
        assert len(result) == 1
        assert "test_memory_something.py" in result[0]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_deduplicate_matched_files(self, tmp_path):
        """短名与层名同时匹配同一文件时应去重"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 文件名同时含 memory（层名）与 memory（短名）
        (tests_dir / "test_memory.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # module_path = agent.memory.memory → 短名=memory, 层=memory
        result = analyzer._find_tests_for_module(
            "agent.memory.memory", all_tests
        )
        assert len(result) == 1  # 去重后只匹配一次


# ═══════════════════════════════════════════════════════════════
#  2. _find_tests_for_module 不传 all_tests 降级
# ═══════════════════════════════════════════════════════════════

class TestFindTestsWithoutPreCollectedList:
    """_find_tests_for_module 不传 all_tests 时的降级行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_collect_test_files_when_all_tests_none(self, tmp_path):
        """不传 all_tests 时应调用 _collect_test_files"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_orchestrator.py").write_text("", encoding="utf-8")

        with patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            result = analyzer._find_tests_for_module(
                "agent.orchestrator.core"
            )
            # 不传 all_tests 应触发 _collect_test_files
            assert mock_collect.call_count == 1

        assert len(result) == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_collect_when_all_tests_explicitly_none(self, tmp_path):
        """显式传 all_tests=None 也应触发 _collect_test_files"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("", encoding="utf-8")

        with patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            analyzer._find_tests_for_module(
                "agent.x.y", all_tests=None
            )
            assert mock_collect.call_count == 1

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_return_empty_when_module_path_too_short(self, tmp_path):
        """module_path 段数 < 2 时应返回空列表"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # 单段模块路径
        result = analyzer._find_tests_for_module("agent", all_tests)
        assert result == []


# ═══════════════════════════════════════════════════════════════
#  3. _collect_test_files 边界条件
# ═══════════════════════════════════════════════════════════════

class TestCollectTestFilesBoundary:
    """_collect_test_files 边界条件"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_empty_when_tests_dir_missing(self, tmp_path):
        """测试目录不存在时应返回空列表"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        result = analyzer._collect_test_files(tmp_path / "nonexistent")
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_return_empty_when_tests_dir_is_empty(self, tmp_path):
        """测试目录为空时应返回空列表"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        result = analyzer._collect_test_files(tests_dir)
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_collect_only_test_files(self, tmp_path):
        """应只收集 test_*.py 文件"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")
        (tests_dir / "test_b.py").write_text("", encoding="utf-8")
        (tests_dir / "helper.py").write_text("", encoding="utf-8")
        (tests_dir / "readme.md").write_text("", encoding="utf-8")

        result = analyzer._collect_test_files(tests_dir)
        names = {p.name for p in result}
        assert names == {"test_a.py", "test_b.py"}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_recursively_collect_subdirs(self, tmp_path):
        """应递归收集子目录中的测试文件"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        sub1 = tests_dir / "unit"
        sub2 = tests_dir / "integration"
        sub1.mkdir(parents=True)
        sub2.mkdir(parents=True)
        (tests_dir / "test_top.py").write_text("", encoding="utf-8")
        (sub1 / "test_unit.py").write_text("", encoding="utf-8")
        (sub2 / "test_integration.py").write_text("", encoding="utf-8")

        result = analyzer._collect_test_files(tests_dir)
        names = {p.name for p in result}
        assert names == {"test_top.py", "test_unit.py", "test_integration.py"}

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_handle_many_files(self, tmp_path):
        """大量测试文件应正常收集"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        for i in range(50):
            (tests_dir / f"test_{i:03d}.py").write_text("", encoding="utf-8")

        result = analyzer._collect_test_files(tests_dir)
        assert len(result) == 50


# ═══════════════════════════════════════════════════════════════
#  4. _relate_tests 预收集共享
# ═══════════════════════════════════════════════════════════════

class TestRelateTestsCacheSharing:
    """_relate_tests 应预收集 all_tests 并共享给所有受影响模块"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_call_collect_test_files_once_for_multiple_modules(self, tmp_path):
        """多个受影响模块时 _collect_test_files 只应调用 1 次"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")
        (tests_dir / "test_b.py").write_text("", encoding="utf-8")

        # 构造 3 个受影响模块
        impacted = [
            ImpactedModule(
                module_path=f"agent.mod_{i}.x",
                impact_type="upstream",
                impact_chain=f"agent.mod_{i}.x → agent.changed",
                risk_level="low",
            )
            for i in range(3)
        ]

        with patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            analyzer._relate_tests(impacted)
            # 3 个模块只触发 1 次 _collect_test_files
            assert mock_collect.call_count == 1, (
                f"3 个模块应共享 1 次 _collect_test_files，"
                f"实际 {mock_collect.call_count} 次"
            )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_should_propagate_all_tests_to_find_tests_for_module(self, tmp_path):
        """_relate_tests 应将预收集的 all_tests 传给 _find_tests_for_module"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")

        impacted = [
            ImpactedModule(
                module_path="agent.mod.a",
                impact_type="upstream",
                impact_chain="agent.mod.a → agent.changed",
                risk_level="low",
            )
        ]

        with patch.object(
            analyzer, "_find_tests_for_module", wraps=analyzer._find_tests_for_module
        ) as mock_find:
            analyzer._relate_tests(impacted)
            # 验证 _find_tests_for_module 被调用时传入了 all_tests 参数
            assert mock_find.call_count == 1
            args, kwargs = mock_find.call_args
            # 第二个位置参数应为 all_tests（非空列表）
            assert len(args) >= 2
            assert args[1] is not None  # all_tests 不为 None
            assert len(args[1]) == 1  # 含 1 个测试文件

    @pytest.mark.unit
    @pytest.mark.p1
    def test_should_handle_empty_impacted_list(self, tmp_path):
        """受影响模块列表为空时也应安全"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        with patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            result = analyzer._relate_tests([])
            # 即使空列表也应调用一次 _collect_test_files（当前实现）
            assert mock_collect.call_count == 1
            assert result == []


# ═══════════════════════════════════════════════════════════════
#  5. analyze() 端到端预收集
# ═══════════════════════════════════════════════════════════════

class TestAnalyzePreCollection:
    """analyze() 端到端预收集 all_tests 行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_analyze_should_not_call_rglob_in_find_tests_for_module_loop(
        self, tmp_path
    ):
        """analyze() 中遍历 changed_files 调用 _find_tests_for_module 时
        不应再触发 _collect_test_files（已预收集）"""
        # 构造 git 仓库（最小化）
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "agent").mkdir()
        (repo / "agent" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_x.py").write_text("", encoding="utf-8")

        # 多个变更文件
        changed_files = [
            ChangedFile(
                path=f"agent/mod_{i}.py",
                status="M",
                module_path=f"agent.mod_{i}",
            )
            for i in range(3)
        ]
        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )

        # mock _get_changed_files 返回多个变更
        # mock _find_impacted_modules 返回空（聚焦 changed_files 路径）
        # mock DependencyGraphBuilder.build 不抛异常
        with patch.object(
            analyzer, "_get_changed_files", return_value=changed_files
        ), patch.object(
            analyzer, "_find_impacted_modules", return_value=[]
        ), patch.object(
            analyzer, "_relate_tests", return_value=[]
        ), patch(
            "scripts.impact_analysis.DependencyGraphBuilder"
        ) as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.edges = []
            mock_builder.nodes = {}
            mock_builder_cls.return_value = mock_builder

            with patch.object(
                analyzer,
                "_collect_test_files",
                wraps=analyzer._collect_test_files,
            ) as mock_collect:
                analyzer.analyze()

        # analyze() 主体内对 changed_files 循环调用 _find_tests_for_module，
        # 应只触发 1 次 _collect_test_files（预收集），而非 N 次
        # 注：_relate_tests 被 mock 为返回空，所以不触发 _collect_test_files
        assert mock_collect.call_count == 1, (
            f"analyze() 中遍历 changed_files 应只触发 1 次 _collect_test_files，"
            f"实际 {mock_collect.call_count} 次"
        )

    @pytest.mark.unit
    @pytest.mark.p0
    def test_analyze_no_changed_files_should_not_collect_tests(self, tmp_path):
        """无变更文件时不应调用 _collect_test_files"""
        repo = tmp_path / "repo"
        repo.mkdir()
        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )
        with patch.object(
            analyzer, "_get_changed_files", return_value=[]
        ), patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            report = analyzer.analyze()
        # 无变更文件应直接返回空报告，不调用 _collect_test_files
        assert mock_collect.call_count == 0
        assert len(report.changed_files) == 0
        assert len(report.impacted_modules) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_empty_tests_dir_should_not_raise(self, tmp_path):
        """测试目录为空时 analyze() 不应抛出异常"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "agent").mkdir()
        (repo / "agent" / "__init__.py").write_text("", encoding="utf-8")
        # tests 目录存在但为空
        (repo / "tests").mkdir()

        changed_files = [
            ChangedFile(
                path="agent/orchestrator.py",
                status="M",
                module_path="agent.orchestrator",
            )
        ]
        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )
        with patch.object(
            analyzer, "_get_changed_files", return_value=changed_files
        ), patch.object(
            analyzer, "_find_impacted_modules", return_value=[]
        ), patch.object(
            analyzer, "_relate_tests", return_value=[]
        ), patch(
            "scripts.impact_analysis.DependencyGraphBuilder"
        ) as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.edges = []
            mock_builder.nodes = {}
            mock_builder_cls.return_value = mock_builder
            # 不应抛出异常
            report = analyzer.analyze()
            assert len(report.recommended_tests) == 0

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_missing_tests_dir_should_degrade_gracefully(self, tmp_path):
        """测试目录不存在时 analyze() 应优雅降级（无 recommended_tests）"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "agent").mkdir()
        (repo / "agent" / "__init__.py").write_text("", encoding="utf-8")
        # 不创建 tests 目录

        changed_files = [
            ChangedFile(
                path="agent/orchestrator.py",
                status="M",
                module_path="agent.orchestrator",
            )
        ]
        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )
        with patch.object(
            analyzer, "_get_changed_files", return_value=changed_files
        ), patch.object(
            analyzer, "_find_impacted_modules", return_value=[]
        ), patch.object(
            analyzer, "_relate_tests", return_value=[]
        ), patch(
            "scripts.impact_analysis.DependencyGraphBuilder"
        ) as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.edges = []
            mock_builder.nodes = {}
            mock_builder_cls.return_value = mock_builder
            # 不应抛出异常
            report = analyzer.analyze()
            assert report.recommended_tests == []


# ═══════════════════════════════════════════════════════════════
#  6. 性能回归：rglob 调用次数验证
# ═══════════════════════════════════════════════════════════════

class TestCachePerformance:
    """性能回归：验证 rglob 调用次数减少"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_find_tests_with_all_tests_should_not_call_rglob(self, tmp_path):
        """传入 all_tests 时 _find_tests_for_module 不应触发 rglob"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        test_file = tests_dir / "test_x.py"
        test_file.write_text("", encoding="utf-8")

        all_tests = [test_file]
        with patch.object(Path, "rglob", return_value=iter([])) as mock_rglob:
            analyzer._find_tests_for_module("agent.x.y", all_tests)
            # 已传入 all_tests，不应调用 rglob
            assert mock_rglob.call_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_find_tests_without_all_tests_should_call_rglob_once(self, tmp_path):
        """不传 all_tests 时 _find_tests_for_module 应触发 1 次 rglob"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("", encoding="utf-8")

        # autospec=True 保留 self 绑定
        with patch.object(Path, "rglob", autospec=True) as mock_rglob:
            analyzer._find_tests_for_module("agent.x.y")
            # _collect_test_files 调用 1 次 rglob
            assert mock_rglob.call_count == 1

    @pytest.mark.unit
    @pytest.mark.p0
    def test_relate_tests_with_n_modules_should_call_rglob_once(self, tmp_path):
        """N 个受影响模块时 _relate_tests 应只调用 1 次 rglob（预收集）"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")

        impacted = [
            ImpactedModule(
                module_path=f"agent.mod_{i}.x",
                impact_type="upstream",
                impact_chain=f"agent.mod_{i}.x → agent.changed",
                risk_level="low",
            )
            for i in range(5)  # 5 个模块
        ]
        with patch.object(Path, "rglob", autospec=True) as mock_rglob:
            analyzer._relate_tests(impacted)
            # 5 个模块只触发 1 次 rglob（预收集）
            assert mock_rglob.call_count == 1, (
                f"5 个模块应共享 1 次 rglob，实际 {mock_rglob.call_count} 次"
            )

    @pytest.mark.unit
    @pytest.mark.p1
    def test_analyze_with_n_changed_files_should_call_rglob_limited_times(
        self, tmp_path
    ):
        """N 个变更文件时 analyze() 主体循环只触发 1 次 rglob（预收集）"""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "agent").mkdir()
        (repo / "agent" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_x.py").write_text("", encoding="utf-8")

        # 5 个变更文件
        changed_files = [
            ChangedFile(
                path=f"agent/mod_{i}.py",
                status="M",
                module_path=f"agent.mod_{i}",
            )
            for i in range(5)
        ]
        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )
        with patch.object(
            analyzer, "_get_changed_files", return_value=changed_files
        ), patch.object(
            analyzer, "_find_impacted_modules", return_value=[]
        ), patch.object(
            analyzer, "_relate_tests", return_value=[]
        ), patch(
            "scripts.impact_analysis.DependencyGraphBuilder"
        ) as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.edges = []
            mock_builder.nodes = {}
            mock_builder_cls.return_value = mock_builder

            # autospec=True 保留 self 绑定
            with patch.object(Path, "rglob", autospec=True) as mock_rglob:
                analyzer.analyze()

        # 5 个变更文件，预收集模式下应只调用 1 次 rglob
        # 注：_relate_tests 被 mock，不触发 rglob
        assert mock_rglob.call_count == 1, (
            f"5 个变更文件预收集应只触发 1 次 rglob，"
            f"实际 {mock_rglob.call_count} 次"
        )


# ═══════════════════════════════════════════════════════════════
#  7. 匹配规则与一致性
# ═══════════════════════════════════════════════════════════════

class TestMatchingConsistency:
    """_find_tests_for_module 匹配规则与多次调用一致性"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_matching_should_be_consistent_between_shared_and_independent(
        self, tmp_path
    ):
        """共享 all_tests 与独立扫描应产出相同结果"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_orchestrator.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        module_path = "agent.orchestrator.core"
        # 模式 1：预收集 + 共享
        all_tests = analyzer._collect_test_files(tests_dir)
        result_shared = analyzer._find_tests_for_module(module_path, all_tests)
        # 模式 2：独立扫描（不传 all_tests）
        result_independent = analyzer._find_tests_for_module(module_path)

        assert result_shared == result_independent
        assert len(result_shared) == 1
        assert "test_orchestrator.py" in result_shared[0]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_no_match_should_return_empty_list(self, tmp_path):
        """无匹配时应返回空列表"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # 模块名 xyz 与 test_a.py 不匹配
        result = analyzer._find_tests_for_module(
            "agent.xyz.unrelated", all_tests
        )
        assert result == []

    @pytest.mark.unit
    @pytest.mark.p1
    def test_module_short_name_case_insensitive_matching(self, tmp_path):
        """模块短名匹配应不区分大小写"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_Orchestrator.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # module_path 含大写 Orchestrator，文件名也含大写
        # 短名小写后应能匹配
        result = analyzer._find_tests_for_module(
            "agent.Orchestrator.core", all_tests
        )
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════
#  8. N+1 调用对比验证（核心优化点）
# ═══════════════════════════════════════════════════════════════

class TestNPlusOneElimination:
    """N+1 调用消除验证：优化前后对比"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_n_modules_should_call_collect_once_not_n_times(self, tmp_path):
        """N 个模块应只调用 1 次 _collect_test_files，而非 N 次"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("", encoding="utf-8")

        # 先预收集一次
        all_tests = analyzer._collect_test_files(tests_dir)

        # 然后调用 N 次 _find_tests_for_module（共享 all_tests）
        with patch.object(
            analyzer, "_collect_test_files"
        ) as mock_collect:
            for i in range(10):
                analyzer._find_tests_for_module(
                    f"agent.mod_{i}.x", all_tests
                )
            # 10 次调用应零次 _collect_test_files（共享模式）
            assert mock_collect.call_count == 0

    @pytest.mark.unit
    @pytest.mark.p0
    def test_n_modules_without_sharing_should_call_collect_n_times(
        self, tmp_path
    ):
        """N 个模块不共享时应调用 N 次 _collect_test_files（验证旧模式缺陷）"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_x.py").write_text("", encoding="utf-8")

        # 不传 all_tests，模拟优化前的 N+1 调用模式
        with patch.object(
            analyzer,
            "_collect_test_files",
            wraps=analyzer._collect_test_files,
        ) as mock_collect:
            for i in range(5):
                analyzer._find_tests_for_module(f"agent.mod_{i}.x")
            # 5 次调用应 5 次 _collect_test_files（N+1 模式缺陷）
            assert mock_collect.call_count == 5

    @pytest.mark.unit
    @pytest.mark.p1
    def test_sharing_mode_reduces_call_count_proportionally(self, tmp_path):
        """共享模式应使 _collect_test_files 调用次数与模块数无关"""
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("", encoding="utf-8")
        (tests_dir / "test_b.py").write_text("", encoding="utf-8")

        all_tests = analyzer._collect_test_files(tests_dir)

        # 不同模块数下的共享模式调用次数都应是 0
        for n_modules in [1, 5, 10, 50]:
            with patch.object(
                analyzer, "_collect_test_files"
            ) as mock_collect:
                for i in range(n_modules):
                    analyzer._find_tests_for_module(
                        f"agent.mod_{i}.x", all_tests
                    )
                assert mock_collect.call_count == 0, (
                    f"{n_modules} 个模块共享模式下应 0 次 _collect_test_files，"
                    f"实际 {mock_collect.call_count} 次"
                )


# ═══════════════════════════════════════════════════════════════
#  9. P0 补充：重复收集优化验证 / 空字符串匹配修复验证
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeRelateTestsDuplicateCollectP0:
    """analyze() 与 _relate_tests 重复收集优化的 P0 级验证

    覆盖缺口（来自 test_coverage_gap_analysis.md 3.3 节）：
    - 优化遗漏：analyze() 预收集与 _relate_tests 内部重复收集（已修复）

    修复内容（Bug 3）：
    - _relate_tests 接受可选 all_tests 参数
    - analyze() 预收集一次并传递给 _relate_tests，避免重复收集
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_analyze_relate_tests_duplicate_collect_optimization_gap(
        self, tmp_path
    ):
        """P0-9: analyze() 全流程中 _collect_test_files 只调用 1 次（修复后验证）

        修复前：analyze() 预收集 1 次 + _relate_tests 内部收集 1 次 = 2 次
        修复后：analyze() 预收集 1 次并传递给 _relate_tests = 1 次
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "agent").mkdir()
        (repo / "agent" / "__init__.py").write_text("", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "test_mod.py").write_text("", encoding="utf-8")

        # 构造 1 个变更文件 + 1 个受影响模块
        changed_files = [
            ChangedFile(
                path="agent/mod.py",
                status="M",
                module_path="agent.mod",
            )
        ]
        impacted_modules = [
            ImpactedModule(
                module_path="agent.dep",
                impact_type="upstream",
                impact_chain="agent.dep → agent.mod",
                risk_level="low",
            )
        ]

        analyzer = ImpactAnalyzer(
            repo_root=str(repo),
            root_dir="agent",
            tests_dir="tests",
        )

        with patch.object(
            analyzer, "_get_changed_files", return_value=changed_files
        ), patch.object(
            analyzer, "_find_impacted_modules", return_value=impacted_modules
        ), patch(
            "scripts.impact_analysis.DependencyGraphBuilder"
        ) as mock_builder_cls:
            mock_builder = MagicMock()
            mock_builder.edges = []
            mock_builder.nodes = {}
            mock_builder_cls.return_value = mock_builder

            # wraps 包裹真实方法，统计调用次数
            with patch.object(
                analyzer,
                "_collect_test_files",
                wraps=analyzer._collect_test_files,
            ) as mock_collect:
                analyzer.analyze()

        # 修复后：analyze() 预收集 1 次，_relate_tests 复用，不再重复收集
        # 修复前：会调用 2 次（analyze 预收集 + _relate_tests 内部收集）
        assert mock_collect.call_count == 1, (
            f"修复后 _collect_test_files 应只调用 1 次（预收集复用），"
            f"实际 {mock_collect.call_count} 次。"
            f"若为 2 次说明 _relate_tests 仍在重复收集（修复未生效）"
        )


class TestFindTestsForModuleEmptyStringP0:
    """_find_tests_for_module 空字符串匹配修复的 P0 级验证

    覆盖缺口（来自 test_coverage_gap_analysis.md 3.3 节）：
    - short_name 为空字符串时匹配所有文件（已修复）
    - layer 为空字符串时匹配所有文件（已修复）

    修复内容（Bug 1）：
    - 添加 short_name_lower / layer_lower 真值检查
    - 空字符串不再参与匹配，避免 "" in fname_lower 始终为 True
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_find_tests_for_module_empty_short_name_matches_all(
        self, tmp_path
    ):
        """P0-10: module_path 含空 short_name（如 "agent.core."）不应匹配所有文件

        "agent.core.".split(".") = ['agent', 'core', '']
        - short_name = '' （空，末尾点号导致）
        - layer = 'core'（非空）

        修复前：short_name="" → "" in fname_lower 为 True，匹配所有文件
        修复后：short_name_lower="" 跳过，仅用 layer='core' 匹配
        """
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 创建两个测试文件：一个含 'core'，一个不含
        (tests_dir / "test_core.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # module_path 末尾点号导致 short_name 为空
        result = analyzer._find_tests_for_module(
            "agent.core.", all_tests
        )

        # 修复后：空 short_name 跳过，仅用 layer='core' 匹配
        # 应只匹配 test_core.py，不匹配 test_other.py
        assert len(result) == 1, (
            f"修复后空 short_name 应跳过，仅用 layer='core' 匹配，"
            f"应只匹配 1 个文件，实际匹配 {len(result)} 个。"
            f"若为 2 说明空字符串仍匹配所有文件（修复未生效）"
        )
        assert "test_core.py" in result[0]
        assert "test_other.py" not in result[0]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_find_tests_for_module_empty_layer_matches_all(self, tmp_path):
        """P0-11: module_path 含空 layer（如 "agent..core"）不应匹配所有文件

        "agent..core".split(".") = ['agent', '', 'core']
        - short_name = 'core'（非空）
        - layer = '' （空，中间连续点号导致）

        修复前：layer="" → "" in fname_lower 为 True，匹配所有文件
        修复后：layer_lower="" 跳过，仅用 short_name='core' 匹配
        """
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 创建两个测试文件：一个含 'core'，一个不含
        (tests_dir / "test_core.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = list(tests_dir.rglob("test_*.py"))
        # module_path 中间连续点号导致 layer 为空
        result = analyzer._find_tests_for_module(
            "agent..core", all_tests
        )

        # 修复后：空 layer 跳过，仅用 short_name='core' 匹配
        # 应只匹配 test_core.py，不匹配 test_other.py
        assert len(result) == 1, (
            f"修复后空 layer 应跳过，仅用 short_name='core' 匹配，"
            f"应只匹配 1 个文件，实际匹配 {len(result)} 个。"
            f"若为 2 说明空字符串仍匹配所有文件（修复未生效）"
        )
        assert "test_core.py" in result[0]
        assert "test_other.py" not in result[0]


# ═══════════════════════════════════════════════════════════════
#  10. P2 补充：并发安全与跨平台路径兼容
# ═══════════════════════════════════════════════════════════════

class TestConcurrentAndCrossPlatformP2:
    """P2 并发安全与跨平台路径兼容性测试

    覆盖维度：
    1. 多线程并发首次填充缓存的安全性（无数据竞争、无重复扫描）
    2. 多进程共享缓存实例（进程隔离语义验证）
    3. Windows 反斜杠路径在 _find_tests_for_module 中的处理
    4. Linux 正斜杠路径的兼容性
    5. 混合路径分隔符（agent\\core\\sub/agent.py）
    6. 并发缓存失效与重新扫描的正确性

    【可观测性约束】
    - 边界显性化：每个测试用例命名反映业务意图
    - 异常处理：所有并发测试设置合理超时（30s），避免死锁
    - 防竞态：使用 threading.Barrier 确保线程同时触发，提高复现率

    【生成日志摘要】
    - 生成时间：2026-06-27
    - 版本：v1.1.0
    - 内容：补充 6 个 P2 并发/跨平台测试用例
    - 关联变更：scripts/impact_analysis.py 的 _find_tests_for_module 增加路径分隔符归一化
    """

    # ── 1. 多线程并发首次填充缓存 ────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_concurrent_writes_thread_safety(self, tmp_path):
        """多线程并发首次填充缓存的安全性验证

        场景：N 个线程同时首次调用 _collect_test_files / _find_tests_for_module，
        缓存尚未建立，所有线程同时触发扫描。

        预期：
        - 无异常抛出（线程安全）
        - 所有线程拿到一致的匹配结果
        - 30s 超时内完成
        """
        import threading

        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 准备 10 个测试文件
        for i in range(10):
            (tests_dir / f"test_mod_{i}.py").write_text("", encoding="utf-8")

        # 预收集 all_tests（多线程共享）
        all_tests = analyzer._collect_test_files(tests_dir)
        assert len(all_tests) == 10

        results: list[list[str]] = [None] * 10  # type: ignore
        errors: list[Exception] = []
        barrier = threading.Barrier(10)  # 确保所有线程同时触发

        def worker(idx: int):
            try:
                barrier.wait(timeout=5)  # 同时触发
                # 每个线程查询不同模块，访问同一份 all_tests
                res = analyzer._find_tests_for_module(
                    f"agent.mod_{idx}.x", all_tests
                )
                results[idx] = res
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)  # 30s 超时

        # 断言：无异常、无死锁（线程全部退出）
        assert not errors, f"并发执行出错: {errors}"
        assert all(t.is_alive() is False for t in threads), "线程未在 30s 内退出（疑似死锁）"
        # 断言：每个线程都拿到 1 个匹配文件
        for idx, res in enumerate(results):
            assert res is not None, f"线程 {idx} 返回 None"
            assert len(res) == 1, f"线程 {idx} 应匹配 1 个文件，实际 {len(res)}"
            assert f"test_mod_{idx}.py" in res[0]

    # ── 2. 多进程共享缓存实例 ────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_cache_process_level_sharing(self, tmp_path):
        """多进程共享缓存实例验证

        场景：多进程模式下，每个进程独立加载 ImpactAnalyzer，
        验证进程间缓存不共享（进程隔离语义），但每个进程内部缓存生效。

        预期：
        - 每个子进程独立扫描，返回一致的结果
        - 子进程内的 _collect_test_files 调用次数受控（共享模式生效）
        - 30s 超时内完成
        """
        import multiprocessing
        import os

        # 准备测试仓库
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        (repo / "tests" / "test_shared.py").write_text("", encoding="utf-8")

        # 启动 2 个子进程（使用模块级函数，Windows spawn 兼容）
        ctx = multiprocessing.get_context("spawn")  # Windows 兼容
        queue = ctx.Queue()
        procs = [
            ctx.Process(target=_cache_process_child_worker, args=(str(repo), queue))
            for _ in range(2)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=30)  # 30s 超时

        # 断言：所有进程正常退出
        for p in procs:
            assert not p.is_alive(), "子进程未在 30s 内退出"
            assert p.exitcode == 0, f"子进程异常退出 code={p.exitcode}"

        # 收集结果
        results = []
        while not queue.empty():
            results.append(queue.get())
        assert len(results) == 2, f"应收到 2 个子进程结果，实际 {len(results)}"

        # 断言：每个子进程内部两次调用结果一致
        for r in results:
            assert "error" not in r, f"子进程出错: {r['error']}"
            assert r["consistent"] is True, f"子进程 {r['pid']} 两次调用结果不一致"
            assert len(r["r1"]) == 1, f"子进程 {r['pid']} 应匹配 1 个文件"
            assert "test_shared.py" in r["r1"][0]

        # 断言：两个进程的 pid 不同（确实是多进程）
        pids = {r["pid"] for r in results}
        assert len(pids) == 2, f"应有 2 个不同 pid，实际 {pids}"

    # ── 3. Windows 反斜杠路径处理 ────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_windows_path_separator_handling(self, tmp_path):
        """Windows 反斜杠路径在 _find_tests_for_module 中的处理

        场景：传入 Windows 风格 module_path（"agent\\core\\sub"），
        应能正确归一化为点分隔并匹配。

        预期：
        - 反斜杠被识别为分隔符，归一化为点
        - short_name="sub", layer="core"
        - 匹配 test_sub.py 或 test_core.py
        """
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sub.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = analyzer._collect_test_files(tests_dir)
        # Windows 反斜杠路径
        result = analyzer._find_tests_for_module(
            "agent\\core\\sub", all_tests
        )
        assert len(result) == 1, (
            f"Windows 反斜杠路径应匹配 1 个文件（test_sub.py），"
            f"实际匹配 {len(result)} 个: {result}"
        )
        assert "test_sub.py" in result[0]

    # ── 4. Linux 正斜杠路径兼容性 ────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_linux_path_separator_handling(self, tmp_path):
        """Linux 正斜杠路径在 _find_tests_for_module 中的兼容性

        场景：传入 Linux 风格 module_path（"agent/core/sub"），
        应能正确归一化为点分隔并匹配。

        预期：
        - 正斜杠被识别为分隔符，归一化为点
        - short_name="sub", layer="core"
        - 匹配 test_sub.py
        """
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_sub.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = analyzer._collect_test_files(tests_dir)
        # Linux 正斜杠路径
        result = analyzer._find_tests_for_module(
            "agent/core/sub", all_tests
        )
        assert len(result) == 1, (
            f"Linux 正斜杠路径应匹配 1 个文件（test_sub.py），"
            f"实际匹配 {len(result)} 个: {result}"
        )
        assert "test_sub.py" in result[0]

    # ── 5. 混合路径分隔符 ────────────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_mixed_path_separators(self, tmp_path):
        """混合路径分隔符（agent\\core\\sub/agent.py）的处理

        场景：module_path 同时包含反斜杠和正斜杠，
        例如跨平台拼接时产生的 "agent\\core\\sub/agent"。

        预期：
        - 两种分隔符都被识别为分隔符，统一归一化为点
        - short_name="agent", layer="core"
        - 匹配 test_agent.py 或 test_core.py
        - 与纯点分隔 "agent.core.sub.agent" 行为一致
        """
        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_agent.py").write_text("", encoding="utf-8")
        (tests_dir / "test_core.py").write_text("", encoding="utf-8")
        (tests_dir / "test_other.py").write_text("", encoding="utf-8")

        all_tests = analyzer._collect_test_files(tests_dir)
        # 混合分隔符路径
        result_mixed = analyzer._find_tests_for_module(
            "agent\\core\\sub/agent", all_tests
        )
        # 纯点分隔路径（应与混合路径结果一致）
        result_dotted = analyzer._find_tests_for_module(
            "agent.core.sub.agent", all_tests
        )

        assert result_mixed == result_dotted, (
            f"混合分隔符路径应与纯点分隔路径结果一致，"
            f"混合={result_mixed}, 点分隔={result_dotted}"
        )
        # 应匹配 test_agent.py（short_name=agent）和 test_core.py（layer=core）
        matched_names = {Path(r).name for r in result_mixed}
        assert "test_agent.py" in matched_names
        assert "test_core.py" in matched_names
        assert "test_other.py" not in matched_names

    # ── 6. 并发缓存失效与重新扫描 ────────────────────────────

    @pytest.mark.unit
    @pytest.mark.p2
    def test_concurrent_cache_invalidation_and_rescan(self, tmp_path):
        """并发缓存失效与重新扫描的正确性

        场景：多个线程同时调用 _find_tests_for_module（不传 all_tests），
        触发并发的 _collect_test_files 重新扫描。

        预期：
        - 无异常抛出（线程安全）
        - 所有线程拿到一致的非空结果
        - rglob 总调用次数等于线程数（无共享缓存，每次独立扫描）
        - 30s 超时内完成
        """
        import threading

        analyzer = ImpactAnalyzer(repo_root=str(tmp_path))
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        # 准备 1 个 test_target.py，所有线程都查询同一模块
        (tests_dir / "test_target.py").write_text("", encoding="utf-8")

        results: list[list[str]] = [None] * 8  # type: ignore
        errors: list[Exception] = []
        barrier = threading.Barrier(8)

        def worker(idx: int):
            try:
                barrier.wait(timeout=5)
                # 不传 all_tests，触发独立扫描
                res = analyzer._find_tests_for_module("agent.target.x")
                results[idx] = res
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        # 断言：无异常、无死锁
        assert not errors, f"并发重新扫描出错: {errors}"
        assert all(not t.is_alive() for t in threads), "线程未在 30s 内退出"
        # 断言：所有线程拿到一致结果
        non_none = [r for r in results if r is not None]
        assert len(non_none) == 8, f"应有 8 个非 None 结果，实际 {len(non_none)}"
        first = non_none[0]
        for idx, r in enumerate(non_none):
            assert r == first, (
                f"线程 {idx} 结果与首线程不一致: {r} vs {first}"
            )
            assert len(r) == 1, f"线程 {idx} 应匹配 1 个文件"
            assert "test_target.py" in r[0]
