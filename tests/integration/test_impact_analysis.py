"""
变更影响分析集成测试
覆盖：端到端分析流程、git diff 解析、受影响模块反查、测试用例关联、报告生成
"""
import json
import os
import subprocess
from pathlib import Path
from textwrap import dedent

import pytest

from scripts.impact_analysis import (
    ImpactAnalyzer,
    ImpactAnalysisError,
    ImpactReport,
    ChangedFile,
    ImpactedModule,
)


# ── 测试夹具 ──────────────────────────────────────────────────


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """构造一个真实的 git 仓库用于集成测试

    仓库结构：
        repo/
        ├── agent/
        │   ├── __init__.py
        │   ├── orchestrator/
        │   │   ├── __init__.py
        │   │   └── core.py        # 依赖 agent.tools.helper
        │   └── tools/
        │       ├── __init__.py
        │       └── helper.py
        └── tests/
            └── unit/
                └── test_tools.py
    """
    repo = tmp_path / "repo"
    repo.mkdir()

    # git init
    subprocess.run(
        ["git", "init"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, capture_output=True, check=True,
    )

    agent = repo / "agent"
    (agent / "orchestrator").mkdir(parents=True)
    (agent / "tools").mkdir(parents=True)
    tests = repo / "tests" / "unit"
    tests.mkdir(parents=True)

    for sub in ["orchestrator", "tools"]:
        (agent / sub / "__init__.py").write_text("", encoding="utf-8")
    (agent / "__init__.py").write_text("", encoding="utf-8")

    # orchestrator/core.py 依赖 tools/helper
    (agent / "orchestrator" / "core.py").write_text(
        "from agent.tools.helper import do_work\n"
        "def run(): return do_work()\n",
        encoding="utf-8",
    )
    (agent / "tools" / "helper.py").write_text(
        "def do_work(): return 'ok'\n",
        encoding="utf-8",
    )

    # 测试文件
    (tests / "test_tools.py").write_text(
        "def test_helper(): assert True\n",
        encoding="utf-8",
    )

    # 初始提交
    subprocess.run(
        ["git", "add", "-A"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo, capture_output=True, check=True,
    )

    # 修改 tools/helper.py（下游变更）
    (agent / "tools" / "helper.py").write_text(
        "def do_work(): return 'changed'\n"
        "def new_func(): pass\n",
        encoding="utf-8",
    )
    # 新增文件
    (agent / "tools" / "new_module.py").write_text(
        "x = 1\n",
        encoding="utf-8",
    )

    subprocess.run(
        ["git", "add", "-A"], cwd=repo, capture_output=True, check=True
    )
    subprocess.run(
        ["git", "commit", "-m", "modify helper + add new_module"],
        cwd=repo, capture_output=True, check=True,
    )

    return repo


# ── 集成测试 ──────────────────────────────────────────────────


class TestEndToEndAnalysis:
    """端到端变更影响分析"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_analyze_returns_report(self, git_repo: Path):
        """端到端分析应返回 ImpactReport"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        assert isinstance(report, ImpactReport)
        assert report.trace_id is not None

    @pytest.mark.integration
    @pytest.mark.p0
    def test_analyze_detects_changed_files(self, git_repo: Path):
        """应检测到变更文件"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        # 应检测到 helper.py 修改 + new_module.py 新增
        paths = {f.path for f in report.changed_files}
        assert "agent/tools/helper.py" in paths
        assert "agent/tools/new_module.py" in paths

    @pytest.mark.integration
    @pytest.mark.p0
    def test_analyze_detects_impacted_upstream(self, git_repo: Path):
        """应反查到上游受影响模块（orchestrator/core 依赖 tools/helper）"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        # orchestrator.core 依赖了变更的 tools.helper，应作为上游受影响
        upstream = [
            m for m in report.impacted_modules
            if m.impact_type == "upstream"
        ]
        upstream_paths = {m.module_path for m in upstream}
        assert "agent.orchestrator.core" in upstream_paths

    @pytest.mark.integration
    @pytest.mark.p0
    def test_analyze_recommends_tests(self, git_repo: Path):
        """应推荐关联测试用例"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        # tools 模块关联 test_tools.py
        assert len(report.recommended_tests) > 0
        assert any("test_tools" in t for t in report.recommended_tests)

    @pytest.mark.integration
    @pytest.mark.p0
    def test_report_to_markdown(self, git_repo: Path):
        """报告应可生成 Markdown"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        md = report.to_markdown()
        assert "变更影响分析报告" in md
        assert "变更文件清单" in md


class TestEmptyDiff:
    """测试空 diff 场景"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_analyze_empty_diff(self, git_repo: Path):
        """无变更时应返回空报告"""
        analyzer = ImpactAnalyzer(
            base_ref="HEAD",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        assert report.changed_files == []
        assert report.impacted_modules == []
        assert not report.has_impact

    @pytest.mark.integration
    @pytest.mark.p1
    def test_analyze_unknown_ref_fallback(self, git_repo: Path):
        """基准 ref 不存在应降级为空变更列表"""
        analyzer = ImpactAnalyzer(
            base_ref="origin/nonexistent-branch",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(git_repo),
        )
        report = analyzer.analyze()
        # 应不抛异常，返回空报告
        assert report.changed_files == []


class TestErrorHandling:
    """测试异常处理"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_git_not_available(self, tmp_path: Path, monkeypatch):
        """git 不可用应抛出 ImpactAnalysisError"""
        # 模拟 git 不在 PATH
        monkeypatch.setenv("PATH", "")
        analyzer = ImpactAnalyzer(
            base_ref="HEAD~1",
            head_ref="HEAD",
            root_dir="agent",
            tests_dir="tests",
            repo_root=str(tmp_path),
        )
        with pytest.raises(ImpactAnalysisError) as exc_info:
            analyzer.analyze()
        assert exc_info.value.error_code == "IMPACT_GIT_NOT_FOUND"


class TestDataclasses:
    """测试数据类"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_changed_file_to_dict(self):
        """ChangedFile.to_dict 应返回完整字段"""
        f = ChangedFile(
            path="a/b.py",
            status="M",
            module_path="a.b",
            insertions=10,
            deletions=5,
        )
        d = f.to_dict()
        assert d["path"] == "a/b.py"
        assert d["status"] == "M"
        assert d["insertions"] == 10

    @pytest.mark.unit
    @pytest.mark.p0
    def test_impacted_module_to_dict(self):
        """ImpactedModule.to_dict 应返回完整字段"""
        m = ImpactedModule(
            module_path="a.b",
            impact_type="upstream",
            impact_chain="a.b → c.d",
            risk_level="high",
            related_tests=["test_a.py"],
            reason="依赖了变更模块",
        )
        d = m.to_dict()
        assert d["module_path"] == "a.b"
        assert d["risk_level"] == "high"
        assert "test_a.py" in d["related_tests"]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_impact_report_to_dict(self):
        """ImpactReport.to_dict 应可序列化"""
        report = ImpactReport(
            trace_id="test",
            base_ref="main",
            head_ref="HEAD",
            changed_files=[],
            impacted_modules=[],
            recommended_tests=[],
            risk_summary={"high": 0, "medium": 0, "low": 0},
            duration_ms=100.0,
        )
        d = report.to_dict()
        json_str = json.dumps(d, ensure_ascii=False)
        assert len(json_str) > 0
        assert d["trace_id"] == "test"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_impact_report_has_impact_property(self):
        """has_impact 属性应正确"""
        report = ImpactReport(
            trace_id="t",
            base_ref="b",
            head_ref="h",
            changed_files=[],
            impacted_modules=[],
            recommended_tests=[],
            risk_summary={"high": 0, "medium": 0, "low": 0},
            duration_ms=0.0,
        )
        assert report.has_impact is False
