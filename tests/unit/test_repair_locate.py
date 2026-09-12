"""TASK-S7-02 步骤 2 定位器 单元测试

覆盖（任务书 §三 步骤 2）：
- 源码切片：**带真实行号**、失败点 ±N 行、超长时如实标注 ``truncated``
- 由测试文件反推被测实现文件（import 扫描）并**剔除只读区与测试自身**
- 证据聚合：Trace 链（叶子视图）/ descriptor（关键词匹配）/ 最近改动（只读 git）
- **证据缺口如实披露**（无 Trace、无 descriptor、git 不可用都不是错误，是要说出来的事实）
- 工单生成：``locate()`` 端到端（真实仓库 + 真实 git）
"""

from __future__ import annotations

import os

import pytest

from repair_fixtures import make_failure

from agent.repair import locate as L
from agent.repair.models import DiagnosisReport, FailureItem, RepairTicket
from agent.repair.policy import RepairPolicy
from agent.repair.trace import RepairRunLogger, RunLoggerConfig

#: 真实仓库根（本测试文件位于 ``<repo>/tests/unit/``）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ════════════════════════════════════════════════════════════
#  切片
# ════════════════════════════════════════════════════════════


class TestSlices:
    def test_slice_has_real_line_numbers(self, tmp_path):
        target = tmp_path / "m.py"
        target.write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n",
                          encoding="utf-8")
        piece = L.make_slice(str(tmp_path), "m.py", 10, radius=3)
        assert piece is not None
        assert piece.start_line == 7 and piece.end_line == 13
        assert "    10| line10" in piece.content
        assert piece.truncated is False

    def test_slice_clamps_at_file_boundaries(self, tmp_path):
        (tmp_path / "m.py").write_text("a\nb\n", encoding="utf-8")
        piece = L.make_slice(str(tmp_path), "m.py", 1, radius=50)
        assert piece.start_line == 1 and piece.end_line == 2

    def test_slice_truncates_and_discloses(self, tmp_path):
        (tmp_path / "m.py").write_text("\n".join("x" * 200 for _ in range(50)) + "\n",
                                       encoding="utf-8")
        piece = L.make_slice(str(tmp_path), "m.py", 25, radius=25, max_chars=200)
        assert piece.truncated is True
        assert len(piece.content) <= 260

    def test_missing_file_returns_none(self, tmp_path):
        assert L.make_slice(str(tmp_path), "nope.py", 1, radius=5) is None

    def test_empty_file_slice(self, tmp_path):
        (tmp_path / "empty.py").write_text("", encoding="utf-8")
        piece = L.make_slice(str(tmp_path), "empty.py", 1, radius=5)
        assert piece.content == "(空文件)"

    def test_build_slices_orders_test_then_impl(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "agent").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("assert 1\n", encoding="utf-8")
        (tmp_path / "agent" / "x.py").write_text("v = 1\n", encoding="utf-8")
        failure = make_failure(file="tests/test_x.py", line=1)
        slices = L.build_slices(str(tmp_path), failure, policy=RepairPolicy(),
                                extra_files=["agent/x.py"])
        assert [s.path for s in slices] == ["tests/test_x.py", "agent/x.py"]

    def test_build_slices_dedups(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("assert 1\n", encoding="utf-8")
        failure = make_failure(file="tests/test_x.py", line=1)
        slices = L.build_slices(str(tmp_path), failure, policy=RepairPolicy(),
                                extra_files=["tests/test_x.py"])
        assert len(slices) == 1


# ════════════════════════════════════════════════════════════
#  实现文件反推
# ════════════════════════════════════════════════════════════


class TestInferImplFiles:
    @pytest.fixture()
    def repo(self, tmp_path):
        (tmp_path / "agent").mkdir()
        (tmp_path / "agent" / "core.py").write_text("def f():\n    return 1\n",
                                                    encoding="utf-8")
        (tmp_path / "agent" / "security").mkdir()
        (tmp_path / "agent" / "security" / "gate.py").write_text("x = 1\n",
                                                                 encoding="utf-8")
        (tmp_path / "tests").mkdir()
        return tmp_path

    def test_from_import_is_mapped(self, repo):
        (repo / "tests" / "test_a.py").write_text(
            "from agent.core import f\n\n\ndef test_f():\n    assert f() == 1\n",
            encoding="utf-8")
        assert L.infer_impl_files(str(repo), "tests/test_a.py") == ["agent/core.py"]

    def test_plain_import_is_mapped(self, repo):
        (repo / "tests" / "test_b.py").write_text("import agent.core\n", encoding="utf-8")
        assert L.infer_impl_files(str(repo), "tests/test_b.py") == ["agent/core.py"]

    def test_readonly_zone_is_excluded(self, repo):
        """命中只读区的实现文件**不进入**工单（否则产出必被护栏丢弃）"""
        (repo / "tests" / "test_c.py").write_text(
            "from agent.security.gate import x\n", encoding="utf-8")
        assert L.infer_impl_files(str(repo), "tests/test_c.py") == []

    def test_missing_test_file_returns_empty(self, repo):
        assert L.infer_impl_files(str(repo), "tests/nope.py") == []

    def test_module_files_variants(self, repo):
        (repo / "agent" / "pkg").mkdir()
        (repo / "agent" / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        assert L.module_files(str(repo), "agent.core") == ["agent/core.py"]
        assert L.module_files(str(repo), "agent.pkg") == ["agent/pkg/__init__.py"]
        assert L.module_files(str(repo), "agent.none") == []


# ════════════════════════════════════════════════════════════
#  证据聚合
# ════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def outside_repo():
    """**仓库之外**的临时目录（``tmp_path``/pytest basetemp 都在仓库内，git 会命中）"""
    import shutil
    import tempfile
    base = os.path.join(os.environ.get("SystemRoot", ""), "Temp") or None
    path = tempfile.mkdtemp(prefix="cp-repair-locate-outside-", dir=base)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def run_logger(tmp_path):
    from repair_fixtures import close_run_logger, make_run_logger
    logger, facade = make_run_logger(tmp_path)
    yield logger
    close_run_logger(logger, facade)


class TestEvidenceAggregation:
    def test_trace_gap_reported_when_store_missing(self):
        leaves, gaps = L.trace_chain_leaf(logger_=None)
        assert leaves == []
        assert gaps, "Trace 不可用时必须如实报缺口"

    def test_trace_leaves_from_real_store(self, run_logger):
        from agent.repair.models import FailureItem as _FI
        run_logger.record_step("diagnose", subject="x", status="ok")
        leaves, gaps = L.trace_chain_leaf(
            logger_=run_logger, capability="repair.diagnose")
        assert gaps == [], gaps
        assert leaves
        leaf = leaves[0]
        assert leaf["trace_id"]
        assert leaf["actor"] == "auto"
        assert "capability_id" in leaf

    def test_descriptor_gap_when_no_keyword(self):
        leaves, gaps = L.descriptors_leaf(keywords=[])
        assert leaves == [] and gaps == []

    def test_descriptor_lookup_is_graceful(self):
        """真实 registry 可用则匹配，不可用/无匹配则如实记缺口（都不抛）"""
        leaves, gaps = L.descriptors_leaf(keywords=["definitely_no_such_capability_xyz"])
        assert leaves == []
        assert gaps, "无匹配时应记缺口"

    def test_adjacent_tests_maps_impl_to_test(self, tmp_path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "tests" / "unit" / "test_core.py").write_text("", encoding="utf-8")
        assert L.adjacent_tests(str(tmp_path), ["agent/core.py"]) == \
            ["tests/unit/test_core.py"]

    def test_adjacent_tests_keeps_test_files_directly(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("", encoding="utf-8")
        assert L.adjacent_tests(str(tmp_path), ["tests/test_x.py"]) == ["tests/test_x.py"]

    def test_adjacent_tests_empty_when_no_mapping(self, tmp_path):
        assert L.adjacent_tests(str(tmp_path), ["agent/nowhere.py"]) == []


# ════════════════════════════════════════════════════════════
#  工单（端到端）
# ════════════════════════════════════════════════════════════


class TestLocate:
    def test_ticket_against_real_repo(self, run_logger):
        report = DiagnosisReport(run_id="rep-loc", repo_root=REPO_ROOT)
        ticket = L.locate(report, repo_root=REPO_ROOT, policy=RepairPolicy(),
                          failure=FailureItem(
                              node_id="tests.unit.test_repair_diagnose::test_x",
                              file="tests/unit/test_repair_diagnose.py", line=40,
                              message="assert False", stack_fingerprint="fp1"),
                          run_logger=run_logger)
        assert ticket.ticket_id.startswith("tkt-")
        assert ticket.slices, "应至少切出失败文件"
        assert ticket.repo_head, "真实 git 仓库应能取到 HEAD"
        assert ticket.impl_files, "应由 import 反推出实现文件"
        entries = [e for e in run_logger.trail() if e.step == "locate"]
        assert entries and entries[0].subject == ticket.ticket_id

    def test_ticket_without_failure_is_marked_error(self, run_logger, tmp_path):
        report = DiagnosisReport(run_id="rep-loc2", repo_root=str(tmp_path))
        ticket = L.locate(report, repo_root=str(tmp_path), run_logger=run_logger)
        assert ticket.failure is not None and ticket.failure.node_id == ""
        entry = [e for e in run_logger.trail() if e.step == "locate"][0]
        assert entry.status == "error"

    def test_evidence_gaps_are_disclosed(self, outside_repo):
        report = DiagnosisReport(run_id="rep-loc3", repo_root=outside_repo)
        ticket = L.locate(report, repo_root=outside_repo, policy=RepairPolicy(),
                          failure=FailureItem(node_id="t::x", file="missing.py", line=1))
        joined = " ".join(ticket.evidence_gaps)
        assert "切片为空" in joined
        assert "不是 git 工作区" in joined, joined
        assert "Trace" in joined

    def test_ticket_to_dict_is_serialisable(self, tmp_path):
        import json
        report = DiagnosisReport(run_id="rep-loc4", repo_root=str(tmp_path))
        ticket = L.locate(report, repo_root=str(tmp_path),
                          failure=FailureItem(node_id="t::x", file="m.py", line=1))
        json.dumps(ticket.to_dict(), ensure_ascii=False)

    def test_max_slices_bound(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text("assert 1\n", encoding="utf-8")
        extra = []
        for idx in range(10):
            rel = f"agent/m{idx}.py"
            os.makedirs(os.path.join(tmp_path, "agent"), exist_ok=True)
            (tmp_path / "agent" / f"m{idx}.py").write_text("v = 1\n", encoding="utf-8")
            extra.append(rel)
        failure = make_failure(file="tests/test_x.py", line=1)
        slices = L.build_slices(str(tmp_path), failure, policy=RepairPolicy(),
                                extra_files=extra)
        assert len(slices) <= L.MAX_SLICES
