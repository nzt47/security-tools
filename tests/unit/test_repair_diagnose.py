"""TASK-S7-02 步骤 1 体检器 单元测试

覆盖（任务书 §四 验收清单相关条目）：
- 人为造出的失败能被体检器发现，并产出含**失败指纹**与相关改动的 ``DiagnosisReport``
- **「无失败」也是合法结果**（ok=True、failures 为空、不编造问题）
- 失败项文本清洗与**堆栈指纹**的确定性（同根因恒同指纹；跨机器不稳定片段被抹平）
- junitxml 可用时以结构化结果为准；不可用时**降级不编造**
- L0 锚「未运行」不等于「通过」（fail-closed 语义）
- 审计快照默认关闭时 ``enabled=False``（不是"链没问题"）
- 最近改动只读 git；git 不可用 → 空列表 + 如实缺口
"""

from __future__ import annotations

import os
import tempfile

import pytest

from repair_fixtures import StubProbeExecutor, make_failure

from agent.repair import diagnose as D
from agent.repair.policy import RepairPolicy

#: 真实仓库根（本测试文件位于 ``<repo>/tests/unit/``）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
#: 真实 L0 锚目录（S5-02 冻结标尺；本任务只读）
REAL_ANCHOR_DIR = os.path.join(REPO_ROOT, "eval", "l0_anchor")


# ════════════════════════════════════════════════════════════
#  文本清洗与指纹
# ════════════════════════════════════════════════════════════


class TestFingerprint:
    def test_same_root_cause_same_fingerprint(self):
        """同一根因（不同绝对路径/地址/耗时）必须得同一指纹"""
        a = ("Traceback:\n  File \"C:\\build\\a\\tests\\test_x.py\", line 12\n"
             "  assert 0x1a2b3c4d == 5\nduration 12.5 ms\n")
        b = ("Traceback:\n  File \"/home/runner/work/b/tests/test_x.py\", line 12\n"
             "  assert 0x99ff00aa == 5\nduration 980.25 ms\n")
        assert D.fingerprint(a) == D.fingerprint(b)

    def test_different_root_cause_different_fingerprint(self):
        a = "assert add(1, 1) == 2"
        b = "assert add(1, 1) == 3"
        assert D.fingerprint(a) != D.fingerprint(b)

    def test_fingerprint_is_deterministic(self):
        assert D.fingerprint("x") == D.fingerprint("x")
        assert len(D.fingerprint("x")) == 16

    def test_line_number_is_preserved(self):
        """行号必须保留（抹掉会让不同根因撞指纹）"""
        assert D.fingerprint("line 12") != D.fingerprint("line 13")

    def test_clean_text_strips_ansi_and_truncates(self):
        text = "\x1b[31m红色\x1b[0m\n\n  多余空白  " + "x" * 500
        cleaned = D.clean_text(text, limit=50)
        assert "\x1b" not in cleaned
        assert len(cleaned) <= 50
        assert cleaned.endswith("…")


# ════════════════════════════════════════════════════════════
#  junitxml 解析
# ════════════════════════════════════════════════════════════

JUNIT_SAMPLE = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="1" tests="4">
    <testcase classname="tests.unit.test_demo" name="test_ok" file="tests/unit/test_demo.py"
              line="3" time="0.01" />
    <testcase classname="tests.unit.test_demo" name="test_bad" file="tests/unit/test_demo.py"
              line="12" time="0.02">
      <failure message="assert 2 == 3">def test_bad():
&gt;       assert add(1, 1) == 3
E       assert 2 == 3

tests/unit/test_demo.py:12: AssertionError</failure>
    </testcase>
    <testcase classname="tests.unit.test_other" name="test_boom"
              file="tests/unit/test_other.py" line="7" time="0.03">
      <error message="ImportError: no module">ImportError: no module
tests/unit/test_other.py:7</error>
    </testcase>
    <testcase classname="tests.unit.test_demo" name="test_skip" file="tests/unit/test_demo.py"
              line="20" time="0.00">
      <skipped message="no reason" />
    </testcase>
  </testsuite>
</testsuites>
"""


class TestJunitParsing:
    def test_counts(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text(JUNIT_SAMPLE, encoding="utf-8")
        summary = D.parse_junit_xml(str(path))
        assert summary.parse_error == ""
        assert (summary.passed, summary.failed, summary.errors, summary.skipped) == (1, 1, 1, 1)

    def test_failure_fields(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text(JUNIT_SAMPLE, encoding="utf-8")
        summary = D.parse_junit_xml(str(path))
        bad = [f for f in summary.failures if f.node_id.endswith("test_bad")][0]
        assert bad.kind == "failure"
        assert bad.file == "tests/unit/test_demo.py"
        assert bad.line == 12
        assert "assert 2 == 3" in bad.message
        assert bad.stack_fingerprint

    def test_error_kind_distinguished(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text(JUNIT_SAMPLE, encoding="utf-8")
        summary = D.parse_junit_xml(str(path))
        kinds = sorted(f.kind for f in summary.failures)
        assert kinds == ["error", "failure"]

    def test_missing_file_reports_parse_error(self, tmp_path):
        summary = D.parse_junit_xml(str(tmp_path / "nope.xml"))
        assert summary.failures == []
        assert "不存在" in summary.parse_error

    def test_corrupt_file_reports_parse_error(self, tmp_path):
        path = tmp_path / "junit.xml"
        path.write_text("<testsuites><oops", encoding="utf-8")
        summary = D.parse_junit_xml(str(path))
        assert summary.failures == []
        assert "解析失败" in summary.parse_error


# ════════════════════════════════════════════════════════════
#  diagnose 主入口
# ════════════════════════════════════════════════════════════


def _write_junit(tmp_path, body: str = JUNIT_SAMPLE) -> str:
    path = tmp_path / "junit.xml"
    path.write_text(body, encoding="utf-8")
    return str(path)


class TestDiagnose:
    def test_finds_injected_failure(self, tmp_path):
        """体检器必须能发现「人为注入的真实 bug」（验收清单第 1 条）"""
        junit = _write_junit(tmp_path)
        probe = StubProbeExecutor(script={"pytest": {"exit_code": 1, "stdout": "1 failed"}},
                                  default={"exit_code": 1, "stdout": "1 failed"})
        # 探针桩不写 junitxml → 预置一份（模拟 pytest 落盘）
        report = D.diagnose(repo_root=str(tmp_path), targets=("tests/unit/test_demo.py",),
                            executor=probe, junit_path=junit, with_anchor=False)
        assert report.ok is False
        assert report.failure_count == 2
        assert report.failures_by_fingerprint
        assert "tests/unit/test_demo.py" in report.failures[0].file
        assert report.test_command.startswith("python -m pytest")

    def test_no_failure_is_legal_result(self, tmp_path):
        """「无失败」是合法结果：ok=True 且 failures 为空（不编造问题）"""
        junit = tmp_path / "junit.xml"
        junit.write_text("""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1">
<testcase classname="tests.unit.test_demo" name="test_ok" file="tests/unit/test_demo.py"
 line="3" time="0.01" /></testsuite></testsuites>
""", encoding="utf-8")
        probe = StubProbeExecutor(default={"exit_code": 0, "stdout": "1 passed in 0.01s"})
        report = D.diagnose(repo_root=str(tmp_path), targets=("tests/unit/test_demo.py",),
                            executor=probe, junit_path=str(junit), with_anchor=False)
        assert report.ok is True
        assert report.failures == []
        assert report.failure_count == 0

    def test_junit_missing_does_not_fabricate(self, tmp_path):
        """junitxml 不可用 → 计数降级，但**不编造**失败项，且明确披露"""
        probe = StubProbeExecutor(default={"exit_code": 1,
                                           "stdout": "2 failed, 3 passed in 1.2s"})
        report = D.diagnose(repo_root=str(tmp_path), targets=("tests/unit/test_demo.py",),
                            executor=probe, junit_path=str(tmp_path / "missing.xml"),
                            with_anchor=False)
        assert report.failures == []
        assert report.tests_failed == 2
        assert report.tests_passed == 3
        assert any("junitxml 不可用" in d for d in report.disclosures)
        assert any("不代表无失败" in d for d in report.disclosures)

    def test_anchor_not_run_is_not_pass(self, tmp_path):
        """未跑 L0 锚 → anchor_ok=None（**不等于通过**），并如实披露"""
        probe = StubProbeExecutor(default={"exit_code": 0, "stdout": "1 passed"})
        junit = tmp_path / "junit.xml"
        junit.write_text("<testsuites><testsuite name='pytest' tests='1'>"
                         "<testcase classname='tests.unit.x' name='t' file='tests/unit/x.py'"
                         " line='1'/></testsuite></testsuites>", encoding="utf-8")
        report = D.diagnose(repo_root=str(tmp_path), targets=("tests/unit/x.py",),
                            executor=probe, junit_path=str(junit), with_anchor=False)
        assert report.anchor_ok is None
        assert any("未运行不等于通过" in d for d in report.disclosures)

    def test_anchor_runs_against_real_anchor_set(self):
        """真实跑 L0 锚（进程内、只读）：锚可用时必须全过并给出计数

        锚目录**显式**指向真实仓库的 ``eval/l0_anchor``：仓库存放在 ``data/`` 之外
        是锚的「位置独立性」不变量（S5-02），故不能用临时目录冒充锚目录。
        """
        report = D.diagnose(repo_root=REPO_ROOT, targets=("tests/unit/test_repair_diagnose.py",),
                            executor=StubProbeExecutor(default={"exit_code": 0,
                                                                "stdout": "1 passed"}),
                            junit_path=os.path.join(tempfile.gettempdir(),
                                                    "cp_repair_anchor_probe.xml"),
                            with_anchor=True, anchor_dir=REAL_ANCHOR_DIR)
        assert report.anchor_ok is True
        assert report.anchor_detail.get("total", 0) >= 1
        assert report.anchor_detail.get("failures") == 0

    def test_anchor_missing_is_not_pass(self, tmp_path):
        """锚目录不存在 → anchor_ok 非 True（**fail-closed**），且披露原因"""
        report = D.diagnose(repo_root=REPO_ROOT,
                            targets=("tests/unit/test_repair_diagnose.py",),
                            executor=StubProbeExecutor(default={"exit_code": 0,
                                                                "stdout": "1 passed"}),
                            junit_path=str(tmp_path / "j.xml"), with_anchor=True,
                            anchor_dir=str(tmp_path / "no_anchor_here"))
        assert report.anchor_ok is not True
        assert report.anchor_detail.get("error")

    def test_audit_snapshot_disabled_is_not_ok(self):
        """审计快照默认关闭 → enabled=False（不是"链没问题"）"""
        snapshot = D.audit_snapshot(enabled=False)
        assert snapshot.enabled is False
        assert snapshot.chain_ok is None

    def test_probe_unavailable_disclosed(self, tmp_path):
        report = D.diagnose(
            repo_root=str(tmp_path), targets=("tests/unit/x.py",),
            executor=StubProbeExecutor(default={"exit_code": -1, "stderr": "pytest 未安装",
                                                "available": False}),
            junit_path=str(tmp_path / "missing.xml"), with_anchor=False)
        assert any("探针不可用" in d for d in report.disclosures)

    def test_summary_markdown_lists_failures(self, tmp_path):
        junit = _write_junit(tmp_path)
        probe = StubProbeExecutor(default={"exit_code": 1, "stdout": "1 failed"})
        report = D.diagnose(repo_root=str(tmp_path), targets=("tests/unit/test_demo.py",),
                            executor=probe, junit_path=junit, with_anchor=False)
        md = report.summary_markdown()
        assert "失败项" in md
        assert "test_bad" in md


# ════════════════════════════════════════════════════════════
#  只读 git：最近改动
# ════════════════════════════════════════════════════════════


class TestRecentChanges:
    def test_git_failure_degrades_to_empty(self, monkeypatch):
        """git 不可用 → 空列表 + 如实缺口（**降级不编造**）"""
        from agent.repair import gitio

        def _boom(*args, **kwargs):
            raise gitio.GitUnavailableError("git 不可用（测试注入）")

        monkeypatch.setattr(gitio, "run_git_readonly", _boom)
        assert D.recent_changes(REPO_ROOT, policy=RepairPolicy()) == []

    def test_git_root_returns_commits_and_interest(self):
        changes = D.recent_changes(REPO_ROOT, policy=RepairPolicy(history_commits=3),
                                   interest=["agent/repair/policy.py"])
        assert changes, "真实仓库应能取到近期改动"
        commits = [c for c in changes if "subject" in c]
        assert commits, "应至少有提交记录"
        assert len(commits) <= 3
        interest_rows = [c for c in changes if c.get("source") == "interest"]
        assert interest_rows and interest_rows[0]["path"] == "agent/repair/policy.py"

    def test_head_files_are_readable_literals(self):
        """HEAD 改动文件必须是**可读字面路径**（core.quotepath 已关，无八进制转义）"""
        from agent.repair import gitio
        files = gitio.head_changed_files(REPO_ROOT)
        assert files, "HEAD 提交应有改动文件"
        assert not any("\\3" in f or f.startswith('"') for f in files)


class TestPytestArgv:
    def test_argv_is_readonly_and_structured(self):
        argv = D.pytest_argv(["tests/unit/x.py"], junit_path="j.xml")
        assert argv[:3] == ("python", "-m", "pytest")
        assert "--junitxml=j.xml" in argv
        assert "-p" in argv and "no:randomly" in argv
        assert argv[-1] == "tests/unit/x.py"

    def test_default_targets_when_empty(self):
        argv = D.pytest_argv([], junit_path="j.xml")
        assert list(D.DEFAULT_TEST_TARGET)[0] in argv


class TestFailureItem:
    def test_to_dict_excludes_heavy_text(self):
        item = make_failure(text="z" * 5000)
        payload = item.to_dict()
        assert "text" not in payload
        assert payload["node_id"].startswith("tests/unit/")
