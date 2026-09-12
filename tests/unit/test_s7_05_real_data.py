# -*- coding: utf-8 -*-
"""TASK-S7-05 真实数据打通 —— 真实采集 / 开关快照 / 面板取证 单元测试

【测试目标】覆盖本任务新增的三个落点：

| 落点 | 覆盖点 |
|---|---|
| `agent/digestion/real_capture.py` | 受控工作区构造、**真实工具调用的三类结果**（成功 / 真实失败 / 工具异常）→ 能力级 Trace 的 capability_id、status、intent 通道、副作用路径口径、同类门槛统计 |
| `agent/digestion/switch_snapshot.py` | 未设置 vs 空串的区分、**引擎生效值**、前后对比（新增/删除/变更/生效值变更）、Markdown 渲染 |
| `scripts/s705_real_digestion_chain.py` / `scripts/s705_panel_probe.py` | 运行时目录推导、演示开关的开启与**复位**、stage 迁移视图、面板读数与渲染 |

【测试纪律（与任务书 §四 对齐）】
- 真实工具在本套件里**全部打桩**（不真起子进程）：单测必须快且与机器环境无关；
  "真跑 pytest 的真实执行"由 `scripts/s705_real_digestion_chain.py` 的实测记录承接；
- 面板读数的断言遵守口径纪律：**缺数据源记 None，不以 0 冒充**；
- 开关用例显式断言"结束后复位"（任务书 §五"开关忘记复位"的预案落地）。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from agent.digestion import real_capture as rc  # noqa: E402
from agent.digestion import switch_snapshot as ss  # noqa: E402
from agent.observability.trace_v2 import TraceFacade, UnifiedTraceStore  # noqa: E402


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> str:
    ws = tmp_path / "ws"
    rc.build_workspace(str(ws), modules=4, bug_every=2)
    return str(ws)


@pytest.fixture()
def trace_facade(tmp_path: Path):
    facade = TraceFacade(str(tmp_path / "trace.db"))
    try:
        yield facade
    finally:
        facade.flush()
        facade._store.stop(timeout=1.0)


def _stub_runner(facade: Any, workspace: str, **overrides: Any) -> Any:
    """真实工具全部打桩的执行器（结果仍是"工具真实返回值"的形状）"""
    runner = rc.RealTaskRunner(facade=facade, workspace=workspace,
                               workspace_id="ws_unit")

    def read_file(path: str, **kwargs: Any) -> Dict[str, Any]:
        with open(path, "r", encoding="utf-8") as fh:
            content = fh.read()
        return {"ok": True, "path": path, "content": content,
                "bytes": len(content.encode("utf-8")),
                "lines": content.count("\n") + 1}

    def shell_ok(command: str, **kwargs: Any) -> Dict[str, Any]:
        return {"ok": True, "stdout": "4 passed in 0.02s\n", "stderr": "",
                "exit_code": 0, "shell": kwargs.get("shell"), "cwd": kwargs.get("cwd")}

    def write_file(path: str, content: str, **kwargs: Any) -> Dict[str, Any]:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return {"ok": True, "path": path, "bytes": len(content.encode("utf-8"))}

    runner._tool_read_file = overrides.get("read_file", read_file)
    runner._tool_execute_shell = overrides.get("shell", shell_ok)
    runner._tool_write_file = overrides.get("write_file", write_file)
    return runner


# ════════════════════════════════════════════════════════════
#  §A 受控真实工作区
# ════════════════════════════════════════════════════════════


class TestBuildWorkspace:
    def test_creates_real_runnable_project(self, tmp_path: Path) -> None:
        specs = rc.build_workspace(str(tmp_path / "ws"), modules=5, bug_every=10)
        assert len(specs) == 5
        for spec in specs:
            module = tmp_path / "ws" / spec.module_rel
            test = tmp_path / "ws" / spec.test_rel
            assert module.exists() and test.exists()
            compile(module.read_text(encoding="utf-8"), str(module), "exec")
            compile(test.read_text(encoding="utf-8"), str(test), "exec")
        assert (tmp_path / "ws" / "pytest.ini").exists()

    def test_bug_injection_is_deterministic_and_disclosable(self, tmp_path: Path) -> None:
        specs = rc.build_workspace(str(tmp_path / "ws"), modules=10, bug_every=5)
        buggy = [s.task_id for s in specs if s.buggy]
        assert buggy == ["s705-real-0004", "s705-real-0009"]
        # 缺陷是**被测代码**的缺陷（返回缺偏置修正），不是轨迹的修饰
        source = (tmp_path / "ws" / specs[4].module_rel).read_text(encoding="utf-8")
        assert "BIAS_CORRECTION" in source and "return total\n" in source

    def test_no_injection_when_bug_every_zero(self, tmp_path: Path) -> None:
        specs = rc.build_workspace(str(tmp_path / "ws"), modules=3, bug_every=0)
        assert not any(s.buggy for s in specs)


class TestPublicSymbols:
    def test_extracts_public_names_only(self) -> None:
        source = ("PUBLIC = 1\n_HIDDEN = 2\n\n\ndef pub():\n    pass\n\n\n"
                  "def _priv():\n    pass\n\n\nclass Cls:\n    pass\n")
        assert rc._public_symbols(source) == ["Cls", "PUBLIC", "pub"]

    def test_syntax_error_returns_empty_not_invented(self) -> None:
        assert rc._public_symbols("def broken(:\n") == []


# ════════════════════════════════════════════════════════════
#  §B 真实任务执行 → 能力级 Trace
# ════════════════════════════════════════════════════════════


class TestRealTaskRunner:
    def test_success_path_records_three_capability_traces(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        runner = _stub_runner(trace_facade, tmp_workspace)
        spec = rc.RealTaskSpec(task_id="t-ok", module_rel="src/mod_0000.py",
                               test_rel="tests/test_mod_0000.py",
                               report_rel="out/audit_0000.md")
        result = runner.run_task(spec)
        trace_facade.flush()
        assert result.status == "success"
        assert result.labels == [rc.LABEL_READ_FILE, rc.LABEL_SHELL_EXECUTE,
                                 rc.LABEL_WRITE_FILE]
        assert result.test_exit_code == 0
        assert result.report_written is True
        assert result.module_lines and result.module_lines > 0

        store = trace_facade._store
        rows = store.query(task_id="t-ok")
        by_cap = {}
        for row in rows:
            by_cap.setdefault(row.capability_id, []).append(row)
        assert set(by_cap) == {rc.CAP_READ_FILE, rc.CAP_SHELL_EXECUTE,
                               rc.CAP_WRITE_FILE, ""}
        assert all(r.response.status == "success"
                   for group in by_cap.values() for r in group)

    def test_intent_channel_carries_real_instruction(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        runner = _stub_runner(trace_facade, tmp_workspace)
        spec = rc.RealTaskSpec(task_id="t-intent", module_rel="src/mod_0000.py",
                               test_rel="tests/test_mod_0000.py",
                               report_rel="out/audit_0000.md")
        runner.run_task(spec)
        trace_facade.flush()
        store = trace_facade._store
        seed = [r for r in store.query(task_id="t-intent")
                if r.capability_id == rc.CAP_READ_FILE][0]
        assert any(str(n).startswith("intent:") for n in seed.side_effects.notes)
        from agent.digestion.cleaning import same_task_key
        key = same_task_key(seed, capability_id=rc.CAP_READ_FILE)
        assert key.outcome == "success"
        # 归一意图键非空，且**不含目标特定的数字**（"同任务换目标仍同键"）
        #
        # 【2026-09-13 修正（S8-05 × S7-05 接缝）】原断言是
        #     `not any(ch.isdigit() for ch in key.intent_key)`
        # 即"键里一个数字都不许有"。S8-05 的 D3 给归组键加了**结构版本前缀**
        # （`agent/digestion/cleaning.py:191`：`v2|cap=<集合>|steps=<档位>|out=<结果>`），
        # `v2` 自身含数字 ⇒ 该断言按**字面**失效，但**意图未失效**：
        # 断言真正要守的是"不得把目标里的 `0000` 之类编进键"，版本前缀不影响该性质。
        # 故按**意图**改断言，并补一条**正向**断言把新结构钉住（收紧而非放宽）。
        assert key.intent_key
        assert key.intent_key.startswith("v2|"), \
            "结构版本前缀应显式存在（S8-05 D3 的归组键结构维度）"
        assert "0000" not in key.intent_key, \
            "目标特定的序号不得进入归组键（否则换目标即换键，归组失效）"
        # 文本归一（分隔符 ‖ 之后）部分不得含数字：CJK 按字切分不会产生数字，
        # 一旦出现说明归一化退化成原样拼接
        _text_part = key.intent_key.split("‖", 1)[-1]
        assert not any(ch.isdigit() for ch in _text_part), \
            f"意图文本段不应含数字：{_text_part!r}"

    def test_real_test_failure_skips_report_and_records_error(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        def shell_fail(command: str, **kwargs: Any) -> Dict[str, Any]:
            return {"ok": False, "stdout": "1 failed\n", "stderr": "",
                    "exit_code": 1}

        runner = _stub_runner(trace_facade, tmp_workspace, shell=shell_fail)
        spec = rc.RealTaskSpec(task_id="t-fail", module_rel="src/mod_0001.py",
                               test_rel="tests/test_mod_0001.py",
                               report_rel="out/audit_0001.md")
        result = runner.run_task(spec)
        trace_facade.flush()
        assert result.status == "error"
        assert result.error_code.startswith("REAL_TEST_FAILED")
        assert result.labels == [rc.LABEL_READ_FILE, rc.LABEL_SHELL_EXECUTE]
        assert result.report_written is False
        store = trace_facade._store
        task_rows = [r for r in store.query(task_id="t-fail")
                     if not r.capability_id]
        assert task_rows and task_rows[0].response.status == "error"

    def test_tool_exception_is_recorded_not_swallowed(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        def read_boom(path: str, **kwargs: Any) -> Dict[str, Any]:
            raise OSError("disk gone")

        runner = _stub_runner(trace_facade, tmp_workspace, read_file=read_boom)
        spec = rc.RealTaskSpec(task_id="t-boom", module_rel="src/mod_0002.py",
                               test_rel="tests/test_mod_0002.py",
                               report_rel="out/audit_0002.md")
        result = runner.run_task(spec)
        trace_facade.flush()
        store = trace_facade._store
        seed = [r for r in store.query(task_id="t-boom")
                if r.capability_id == rc.CAP_READ_FILE][0]
        assert seed.response.status == "error"
        assert "OSError" in str(seed.response.error_code)
        assert result.status in ("success", "error")

    def test_side_effect_paths_use_forward_slashes(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        """副作用路径与回放沙箱 `_norm_path` 同口径（正斜杠）

        回归：Windows 下 `os.path.join` 与相对段混用会产生 `C:\\ws\\out/x.md`，
        回放侧归一为 `C:/ws/out/x.md` ⇒ 副作用层误判"不一致"（实测缺陷）。
        """
        runner = _stub_runner(trace_facade, tmp_workspace)
        spec = rc.RealTaskSpec(task_id="t-slash", module_rel="src/mod_0000.py",
                               test_rel="tests/test_mod_0000.py",
                               report_rel="out/audit_0000.md")
        runner.run_task(spec)
        trace_facade.flush()
        store = trace_facade._store
        written = [p for r in store.query(task_id="t-slash")
                   for p in (r.side_effects.files_written or [])]
        assert written, "写文件副作用必须落账"
        assert all("\\" not in str(p) for p in written)
        assert written[0].endswith("/out/audit_0000.md")

    def test_report_content_derives_from_real_outputs(
            self, trace_facade: Any, tmp_workspace: str) -> None:
        runner = _stub_runner(trace_facade, tmp_workspace)
        spec = rc.RealTaskSpec(task_id="t-report", module_rel="src/mod_0000.py",
                               test_rel="tests/test_mod_0000.py",
                               report_rel="out/audit_0000.md")
        runner.run_task(spec)
        report = Path(tmp_workspace, "out", "audit_0000.md").read_text(
            encoding="utf-8")
        assert "真实测试" in report and "exit 0" in report
        assert "summarize" in report          # 来自真实 AST 解析
        assert "4 passed" in report           # 来自真实 stdout


# ════════════════════════════════════════════════════════════
#  §C 同类轨迹门槛统计
# ════════════════════════════════════════════════════════════


def _seed_ledger(tmp_path: Path, *, tasks: int, intent: str,
                 fail_every: int = 0) -> UnifiedTraceStore:
    """真实形状的最小台账（成功 3 步 / 失败 2 步；intent 走 notes 通道）

    复用 facade 自己的 store（不再开第二个 writer 连同一个库 —— 避免同库双写）。
    """
    from agent.observability.trace_v2 import SideEffects

    facade = TraceFacade(str(tmp_path / "ledger.db"))
    for i in range(tasks):
        failing = bool(fail_every) and (i + 1) % fail_every == 0
        facade.start(task_id=f"t{i:03d}", workspace_id="ws_unit")
        facade.record(rc.CAP_READ_FILE, args={"path": f"C:/ws/src/m{i}.py"},
                      output={"ok": True}, status="success",
                      side_effects=SideEffects(notes=[f"intent:{intent}"]))
        facade.record(rc.CAP_SHELL_EXECUTE, args={"cmd": f"pytest t{i}"},
                      output={"ok": not failing}, status=("error" if failing
                                                          else "success"),
                      side_effects=SideEffects(external_calls=["shell_execute"]))
        if not failing:
            facade.record(rc.CAP_WRITE_FILE, args={"path": f"C:/ws/out/r{i}.md"},
                          output={"ok": True}, status="success",
                          side_effects=SideEffects(
                              files_written=[f"C:/ws/out/r{i}.md"]))
        facade.finish(status="error" if failing else "success")
    facade.flush()
    return facade._store


class TestSameKindSummary:
    def test_groups_by_same_task_key_and_reports_success_bucket(
            self, tmp_path: Path) -> None:
        store = _seed_ledger(tmp_path, tasks=7, intent=rc.TASK_INSTRUCTION,
                             fail_every=3)
        summary = rc.same_kind_summary(store, rc.CAP_READ_FILE, threshold=5)
        assert summary["collect"]["matched"] == 7
        assert summary["max_success_size"] == 5      # 7 条里 2 条真实失败
        assert summary["meets_threshold"] is True
        outcomes = {g["outcome"]: g["size"] for g in summary["groups"]}
        assert outcomes["success"] == 5 and outcomes["failure"] == 2
        success = [g for g in summary["groups"] if g["outcome"] == "success"][0]
        assert success["negative"] == 0 and success["trace_ids"]

    def test_below_threshold_list_kept_without_padding(self, tmp_path: Path) -> None:
        store = _seed_ledger(tmp_path, tasks=3, intent=rc.TASK_INSTRUCTION)
        summary = rc.same_kind_summary(store, rc.CAP_READ_FILE, threshold=20)
        assert summary["meets_threshold"] is False
        assert summary["below_threshold"], "未达门槛的同类组必须如实保留"
        assert summary["max_success_size"] == 3


class TestRegistryCoverage:
    def test_lists_untouched_capabilities_without_padding(self, tmp_path: Path) -> None:
        from tests.unit.digestion_util import descriptor_registry

        store = _seed_ledger(tmp_path, tasks=6, intent=rc.TASK_INSTRUCTION)
        reg = descriptor_registry(tmp_path, ["read_file", "write_file",
                                            "shell_execute", "browser_navigate",
                                            "web_search"])
        coverage = rc.registry_coverage(store, reg, threshold=5)
        assert coverage["available"] is True
        assert coverage["threshold_kind"].startswith("正式门槛")
        by_id = {row["capability_id"]: row for row in coverage["capabilities"]}
        # 链内三步都被真实覆盖（同一任务链同时用到三者）
        for cid in ("cp.builtin.read_file", "cp.builtin.write_file",
                    "cp.builtin.shell_execute"):
            assert by_id[cid]["meets_threshold"] is True
        # 链外能力一条真实同类轨迹都没有 —— 必须如实列出，不硬凑
        assert by_id["cp.builtin.browser_navigate"]["max_success_size"] == 0
        below = {row["capability_id"] for row in coverage["below_threshold"]}
        assert below == {"cp.builtin.browser_navigate", "cp.builtin.web_search"}
        assert coverage["below_threshold_count"] == 2

    def test_unavailable_registry_is_reported_not_guessed(self, tmp_path: Path) -> None:
        class Broken:
            def list(self) -> List[Any]:
                raise RuntimeError("registry down")

        store = _seed_ledger(tmp_path, tasks=1, intent=rc.TASK_INSTRUCTION)
        coverage = rc.registry_coverage(store, Broken(), threshold=5)
        assert coverage["available"] is False
        assert "registry down" in coverage["reason"]


class TestSummarizeResults:
    def test_counts_exit_codes_and_traces(self) -> None:
        rows = [
            rc.RealTaskResult(task_id="a", status="success", test_exit_code=0,
                              trace_ids={"read_file": "1", "write_file": "3"},
                              labels=["read_file", "write_file"]),
            rc.RealTaskResult(task_id="b", status="error", test_exit_code=1,
                              trace_ids={"read_file": "2"},
                              labels=["read_file"]),
        ]
        summary = rc.summarize_results(rows)
        assert summary["tasks"] == 2 and summary["success"] == 1
        assert summary["failure"] == 1
        assert summary["test_exit_codes"] == {"0": 1, "1": 1}
        assert summary["capability_traces"] == 3

    def test_write_evidence_round_trip(self, tmp_path: Path) -> None:
        path = rc.write_evidence({"a": 1}, str(tmp_path / "n" / "e.json"))
        assert json.loads(Path(path).read_text(encoding="utf-8")) == {"a": 1}


# ════════════════════════════════════════════════════════════
#  §D 开关快照与复位
# ════════════════════════════════════════════════════════════


class TestSwitchSnapshot:
    def test_unset_is_none_not_empty_string(self) -> None:
        snap = ss.switch_snapshot(env={}, label="empty")
        assert snap["raw"]["CP_DIGESTION_SHADOW_ENABLED"] is None
        assert "CP_DIGESTION_SHADOW_ENABLED" in snap["unset"]
        assert (snap["effective"]["shadow_enabled"]["value"]) is False

    def test_empty_string_differs_from_unset(self) -> None:
        snap = ss.switch_snapshot(env={"CP_DIGESTION_SHADOW_ENABLED": ""},
                                  label="blank")
        assert snap["raw"]["CP_DIGESTION_SHADOW_ENABLED"] == ""

    def test_effective_value_comes_from_engine(self) -> None:
        snap = ss.switch_snapshot(
            env={"CP_DIGESTION_SHADOW_ENABLED": "true",
                 "CP_DIGESTION_SHADOW_BUDGET_CAP": "7",
                 "CP_DIGESTION_SHADOW_BUDGET_RATIO": "0.5"}, label="on")
        assert snap["effective"]["shadow_enabled"]["value"] is True
        assert snap["effective"]["shadow_budget"]["value"]["cap"] == 7
        assert snap["effective"]["shadow_budget"]["value"]["ratio"] == 0.5
        thresholds = snap["effective"]["digest_thresholds"]["value"]
        assert thresholds["digest_count_min"] == 50
        assert thresholds["monthly_samples_min"] == 200
        assert thresholds["veto_conditions"] == ["p99", "privacy_gate"]

    def test_illegal_value_falls_back_and_is_visible(self) -> None:
        snap = ss.switch_snapshot(
            env={"CP_DIGESTION_SHADOW_BUDGET_RATIO": "9.9"}, label="illegal")
        # 越界 → 回退默认 0.15（生效值可见，不静默）
        assert snap["effective"]["shadow_budget"]["value"]["ratio"] == 0.15

    def test_diff_detects_added_removed_changed(self) -> None:
        before = ss.switch_snapshot(env={"A": "1"}, label="b")
        before["raw"] = {"A": "1", "B": "2"}
        after = ss.switch_snapshot(env={}, label="a")
        after["raw"] = {"B": "9", "C": "3"}
        diff = ss.diff_snapshots(before, after)
        assert diff["added"] == ["C"] and diff["removed"] == ["A"]
        assert diff["changed"] == [{"key": "B", "before": "2", "after": "9"}]
        assert diff["identical"] is False

    def test_self_diff_is_identical(self) -> None:
        snap = ss.switch_snapshot(env={}, label="x")
        assert ss.diff_snapshots(snap, snap)["identical"] is True

    def test_markdown_lists_every_switch_with_owner(self) -> None:
        text = ss.format_markdown(ss.switch_snapshot(env={}, label="md"))
        for key in ss.SWITCH_KEYS:
            assert key in text
        assert "shadow_enabled" in text
        assert "未设置" in text


# ════════════════════════════════════════════════════════════
#  §E 链脚本的运行时目录 / 演示开关 / 迁移视图
# ════════════════════════════════════════════════════════════


class TestChainScriptHelpers:
    def test_runtime_dirs_layout(self, tmp_path: Path) -> None:
        import s705_real_digestion_chain as chain

        paths = {k: str(v).replace("\\", "/")
                 for k, v in chain.runtime_dirs(str(tmp_path)).items()}
        assert paths["runtime_root"] == str(tmp_path).replace("\\", "/")
        assert paths["trace_db"].endswith("agent/data/tool_trace.db")
        assert paths["events_dir"].endswith("data/events")
        assert paths["case_dir"].endswith("data/digestion/cases")
        assert paths["shadow_dir"].endswith("data/digestion/shadow")
        assert paths["promote_dir"].endswith("data/digestion/promote_pr")
        assert paths["audit_db"].endswith("data/audit/audit_chain.db")

    def test_apply_runtime_env_sets_explicit_paths(self, tmp_path: Path) -> None:
        import s705_real_digestion_chain as chain

        paths = chain.runtime_dirs(str(tmp_path))
        saved = {k: os.environ.get(k) for k in
                 ("CP_EVENTS_DIR", "CP_DIGESTION_CASE_DIR",
                  "CP_DIGESTION_SHADOW_DIR", "CP_DIGESTION_PROMOTE_DIR",
                  "AUDIT_DB_PATH", "CP_DIGESTION_SANDBOX_DENY_EXTERNAL")}
        try:
            chain.apply_runtime_env(paths)
            assert os.environ["CP_EVENTS_DIR"] == paths["events_dir"]
            assert os.environ["AUDIT_DB_PATH"] == paths["audit_db"]
            assert "CP_DIGESTION_SANDBOX_DENY_EXTERNAL" not in os.environ
            for key in ("demo_dir", "workspace", "events_dir", "case_dir",
                        "shadow_dir", "promote_dir"):
                assert os.path.isdir(paths[key])
        finally:
            for key, value in saved.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_demo_switch_enable_and_reset(self) -> None:
        import s705_real_digestion_chain as chain

        key = "CP_DIGESTION_SHADOW_ENABLED"
        original = os.environ.get(key)
        try:
            os.environ.pop(key, None)
            before = ss.switch_snapshot(label="before")
            assert before["effective"]["shadow_enabled"]["value"] is False
            saved = chain.enable_demo_switches()
            during = ss.switch_snapshot(label="during")
            assert during["effective"]["shadow_enabled"]["value"] is True
            chain.restore_switches(saved)
            after = ss.switch_snapshot(label="after")
            assert after["effective"]["shadow_enabled"]["value"] is False
            assert ss.diff_snapshots(before, after)["identical"] is True
            assert key not in os.environ       # 原本未设置 ⇒ 复位为"未设置"
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

    def test_restore_puts_back_original_value(self) -> None:
        import s705_real_digestion_chain as chain

        key = "CP_DIGESTION_SHADOW_BUDGET_CAP"
        original = os.environ.get(key)
        try:
            os.environ[key] = "12"
            saved = chain.enable_demo_switches({"CP_DIGESTION_SHADOW_ENABLED": "true"})
            assert os.environ["CP_DIGESTION_SHADOW_ENABLED"] == "true"
            chain.restore_switches(saved)
            assert os.environ[key] == "12"
            assert "CP_DIGESTION_SHADOW_ENABLED" not in os.environ
        finally:
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original

    def test_migration_view_handles_none_and_dataclass(self) -> None:
        import s705_real_digestion_chain as chain
        from agent.digestion.models import StageMigration

        assert chain._migration_view(None) is None
        view = chain._migration_view(StageMigration(
            capability_id="cp.builtin.read_file", from_stage="borrowed",
            to_stage="mirrored", applied=True, verdict="applied"))
        assert view["capability_id"] == "cp.builtin.read_file"
        assert view["applied"] is True and view["ok"] is True

    def test_print_panel_smoke(self, capsys: Any) -> None:
        import s705_real_digestion_chain as chain

        chain._print_panel({
            "lanes": [{"title": "验收", "event_count": {"value": 3, "source": "s"}}],
            "summary": {"shadow_runs": {"value": 2, "source": "ledger"},
                        "internalize_decisions": {"value": 1, "source": "pr"},
                        "stage_distribution": {"shadow": 1}},
            "shadow": [], "internalize": []})
        out = capsys.readouterr().out
        assert "验收" in out and "shadow_runs" in out and "internalize_decisions" in out


# ════════════════════════════════════════════════════════════
#  §F 面板取证脚本
# ════════════════════════════════════════════════════════════


class TestPanelProbe:
    def test_reads_explicit_runtime_root_without_fabrication(
            self, tmp_path: Path) -> None:
        import s705_panel_probe as probe

        (tmp_path / "data").mkdir(parents=True, exist_ok=True)
        payload = probe.probe(str(tmp_path), days=7, limit=10,
                              use_env_defaults=False)
        assert payload["ok"] is True
        for lane in payload["lanes"]:
            metric = lane["event_count"]
            assert metric["value"] == 0 and metric["source"]
            assert metric["formula"]
        summary = payload["summary"]
        # 缺数据源记 None / 0 并带来源，不编造
        assert summary["shadow_runs"]["value"] == 0
        assert summary["shadow_runs"]["source"]
        assert summary["internalize_rate"]["value"] is None
        assert summary["stage_distribution"] == {}

    def test_render_contains_three_columns(self, tmp_path: Path) -> None:
        import s705_panel_probe as probe

        payload = probe.probe(str(tmp_path), days=7, limit=10,
                              use_env_defaults=False)
        text = probe.render(payload, root=str(tmp_path), mode="单测")
        for title in ("轨迹采集", "模式挖掘", "Skill 生成", "验收", "灰度"):
            assert title in text
        assert "shadow_runs" in text and "internalize_decisions" in text


class TestRuntimeRootResolution:
    def test_git_common_root_returns_existing_dir(self) -> None:
        import s705_panel_probe as probe

        root = probe._git_common_root()
        assert os.path.isdir(root)
        # 部署根应含 agent/ 与 data/（worktree 里跑也指向主仓库根）
        assert os.path.isdir(os.path.join(root, "agent"))
