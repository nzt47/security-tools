"""隔离等级模型 / 探测 / 边界声明 / 探针与证据工具（TASK-S8-03 步骤 1+5）

覆盖：
- 等级词表与归一（非法值**不猜**）；
- 选择策略：auto 择优、显式请求、**不可用即如实降级**、非法值回退；
- 边界声明的"保证 / **不保证**"清单（诚实底线）；
- 探针作业构造、结果解析、对比表生成；
- "未双写"前后快照比对工具。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

from agent.digestion import isolation as ISO

from isolation_util import fake_prober


# ════════════════════════════════════════════════════════════
#  等级词表
# ════════════════════════════════════════════════════════════


class TestLevelVocabulary:
    def test_three_levels_with_intended_ordering(self):
        assert ISO.ISOLATION_LEVELS == ("in_process", "subprocess_hardened",
                                        "container")
        assert (ISO.ISOLATION_RANK["in_process"]
                < ISO.ISOLATION_RANK["subprocess_hardened"]
                < ISO.ISOLATION_RANK["container"])

    @pytest.mark.parametrize("raw,expected", [
        ("in_process", "in_process"), ("in-process", "in_process"),
        ("off", "in_process"), ("none", "in_process"), ("process", "in_process"),
        ("subprocess", "subprocess_hardened"),
        ("subprocess_hardened", "subprocess_hardened"),
        ("hardened", "subprocess_hardened"),
        ("container", "container"), ("docker", "container"),
        ("  CONTAINER  ", "container"),
    ])
    def test_aliases_normalize(self, raw, expected):
        assert ISO.normalize_level(raw) == expected

    @pytest.mark.parametrize("raw", ["auto", "", None, "  ", "k8s",
                                     "subprocess_hardenedx", 42])
    def test_auto_and_unknown_are_not_levels(self, raw):
        """`auto` 与非法值都**不是**等级 —— 不猜（猜错就是"冒充容器"的开端）"""
        assert ISO.normalize_level(raw) is None

    def test_request_off_is_an_alias_not_a_level(self):
        assert ISO.ISOLATION_REQUEST_OFF not in ISO.ISOLATION_LEVELS
        assert ISO.ISOLATION_REQUEST_ALIASES["off"] == "in_process"


# ════════════════════════════════════════════════════════════
#  选择与降级
# ════════════════════════════════════════════════════════════


class TestResolveIsolationLevel:
    def test_auto_prefers_container_when_docker_available(self):
        plan = ISO.resolve_isolation_level(env={}, prober=fake_prober(available=True))
        assert plan.level == "container"
        assert plan.capable and plan.container_isolated
        assert not plan.downgraded
        assert "container" in "；".join(plan.reasons)

    def test_auto_falls_back_to_subprocess_without_docker(self):
        plan = ISO.resolve_isolation_level(env={}, prober=fake_prober(available=False))
        assert plan.level == "subprocess_hardened"
        assert plan.capable and not plan.container_isolated
        assert "非内核级隔离" in "；".join(plan.reasons)

    def test_auto_falls_back_to_in_process_when_subprocess_unavailable(
            self, monkeypatch):
        monkeypatch.setattr(ISO, "subprocess_available", lambda: False)
        plan = ISO.resolve_isolation_level(env={}, prober=fake_prober(available=False))
        assert plan.level == "in_process"
        assert not plan.capable
        assert "拒绝" in "；".join(plan.reasons)

    def test_explicit_container_downgrades_honestly(self):
        """显式请求容器但 daemon 不在 ⇒ **如实降级**，绝不冒称容器"""
        plan = ISO.resolve_isolation_level(requested="container", env={},
                                           prober=fake_prober(available=False))
        assert plan.level == "subprocess_hardened"
        assert plan.downgraded is True
        assert plan.source == ISO.LEVEL_SOURCE_FALLBACK
        assert plan.container_isolated is False
        joined = "；".join(plan.reasons)
        assert "如实降级" in joined and "不冒称容器" in joined

    def test_explicit_container_without_subprocess_downgrades_to_in_process(
            self, monkeypatch):
        monkeypatch.setattr(ISO, "subprocess_available", lambda: False)
        plan = ISO.resolve_isolation_level(requested="container", env={},
                                           prober=fake_prober(available=False))
        assert plan.level == "in_process"
        assert plan.downgraded is True
        assert not plan.capable

    def test_explicit_subprocess_downgrades_when_unavailable(self, monkeypatch):
        monkeypatch.setattr(ISO, "subprocess_available", lambda: False)
        plan = ISO.resolve_isolation_level(requested="subprocess_hardened", env={},
                                           prober=fake_prober())
        assert plan.level == "in_process" and plan.downgraded is True

    def test_explicit_in_process_needs_no_docker_probe(self):
        """显式等级与 Docker 可用性无关 ⇒ **不探测**（且如实标注未探测）"""
        calls: list = []
        plan = ISO.resolve_isolation_level(
            requested="in_process", env={},
            prober=fake_prober(available=False, calls=calls))
        assert calls == []
        assert plan.level == "in_process"
        assert plan.docker.get("probed") is False
        assert "未探测" in "；".join(plan.docker.get("reasons") or [])

    def test_env_level_is_read_when_no_argument(self):
        plan = ISO.resolve_isolation_level(
            env={ISO.ENV_LEVEL: "in_process"}, prober=fake_prober(available=False))
        assert plan.level == "in_process"
        assert plan.source == ISO.LEVEL_SOURCE_ENV

    def test_argument_beats_env(self):
        plan = ISO.resolve_isolation_level(
            requested="in_process", env={ISO.ENV_LEVEL: "container"},
            prober=fake_prober(available=True))
        assert plan.level == "in_process"
        assert plan.source == ISO.LEVEL_SOURCE_ARG

    def test_invalid_env_value_falls_back_to_auto(self):
        plan = ISO.resolve_isolation_level(
            env={ISO.ENV_LEVEL: "k8s-gpu"}, prober=fake_prober(available=True))
        assert plan.level == "container"
        assert any("非法" in r for r in plan.reasons)

    def test_plan_dict_carries_honest_gap_list(self):
        plan = ISO.resolve_isolation_level(env={}, prober=fake_prober(available=False))
        payload = plan.to_dict()
        assert payload["not_guaranteed"], "不保证边界清单不得为空"
        assert payload["kernel_isolation"] is False
        assert payload["display"]


# ════════════════════════════════════════════════════════════
#  边界声明（诚实清单）
# ════════════════════════════════════════════════════════════


class TestBoundaries:
    def test_container_claims_kernel_isolation_and_lists_gaps(self):
        spec = ISO.isolation_boundaries("container")
        assert spec.kernel_isolation is True and spec.container_isolated is True
        joined = "；".join(spec.guarantees)
        for token in ("只读", "--network none", "非 root", "内存"):
            assert token in joined or token in "；".join(spec.enforcement.values())
        gaps = "；".join(spec.not_guaranteed)
        assert "不是虚拟机" in gaps, "容器必须承认它共享宿主内核"
        assert "whitelist" in gaps, "白名单是应用层策略⇒必须承认"

    def test_subprocess_never_claims_kernel_isolation(self):
        spec = ISO.isolation_boundaries("subprocess_hardened")
        assert spec.kernel_isolation is False
        assert spec.container_isolated is False
        gaps = "；".join(spec.not_guaranteed)
        assert "没有内核级隔离" in gaps
        assert "Windows" in gaps, "Windows 无 rlimit ⇒ 必须写清"
        assert "协作式" in gaps, "路径守卫是协作式的 ⇒ 必须写清"
        joined = "；".join(spec.guarantees)
        assert "env_mode=replace" in joined or "环境整体替换" in joined

    def test_in_process_declares_refusal_rationale(self):
        spec = ISO.isolation_boundaries("in_process")
        assert spec.container_isolated is False
        gaps = "；".join(spec.not_guaranteed)
        assert "拒绝" in gaps and "real_takeover" in gaps

    def test_unknown_level_is_treated_as_in_process(self):
        assert ISO.isolation_boundaries("k8s").level == "in_process"
        assert ISO.isolation_boundaries(None).level == "in_process"

    def test_boundary_table_covers_all_levels(self):
        table = ISO.boundary_table()
        assert [row["level"] for row in table] == list(ISO.ISOLATION_LEVELS)
        for row in table:
            assert row["guarantees"] and row["not_guaranteed"]

    def test_capability_report_shape(self):
        report = ISO.isolation_capability_report(
            env={}, requested="in_process")
        assert set(report) >= {"plan", "boundaries", "levels", "platform"}
        assert report["plan"]["level"] == "in_process"


# ════════════════════════════════════════════════════════════
#  Docker 探测（真实：不假装）
# ════════════════════════════════════════════════════════════


class TestDockerProbe:
    def test_missing_cli_is_reported_not_raised(self):
        probe = ISO.probe_docker(cli="definitely-not-a-real-cli-xyz",
                                 refresh=True, timeout_s=5.0)
        assert probe.available is False
        assert probe.cli_available is False
        assert probe.reasons and "不冒充" in "；".join(probe.reasons)
        assert probe.to_dict()["available"] is False

    def test_probe_never_raises_on_broken_runner(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("boom")
        monkeypatch.setattr(ISO.subprocess, "run", _boom)
        ISO.reset_isolation_probe_cache()
        probe = ISO.probe_docker(refresh=True)
        assert probe.available is False
        assert probe.reasons
        ISO.reset_isolation_probe_cache()

    def test_cli_and_daemon_are_probed_separately(self):
        """「装了 CLI 但 daemon 没起」必须能被区分 —— 那是开发机最常见状态"""
        probe = ISO.probe_docker(cli="definitely-not-a-real-cli-xyz", refresh=True,
                                 timeout_s=5.0)
        assert probe.cli_available is False
        assert probe.daemon_available is False
        assert "detail" in probe.to_dict()

    def test_cache_avoids_repeated_probing(self):
        calls: list = []
        prober = fake_prober(available=True, calls=calls)
        ISO.reset_isolation_probe_cache()
        try:
            first = ISO.resolve_isolation_level(env={}, prober=prober)
            second = ISO.resolve_isolation_level(env={}, prober=prober)
            assert first.level == second.level == "container"
        finally:
            ISO.reset_isolation_probe_cache()

    def test_subprocess_available_is_true_in_this_interpreter(self):
        assert ISO.subprocess_available() is True


# ════════════════════════════════════════════════════════════
#  配额与作业构造
# ════════════════════════════════════════════════════════════


class TestQuotaAndJobs:
    def test_quota_defaults_and_env_fallback(self):
        quota = ISO.IsolationQuota.from_env({})
        assert quota.memory_mb == ISO.DEFAULT_MEMORY_MB
        assert quota.pids_limit == ISO.DEFAULT_PIDS_LIMIT
        bad = ISO.IsolationQuota.from_env({ISO.ENV_MEMORY_MB: "not-a-number",
                                          ISO.ENV_CPUS: "-3"})
        assert bad.memory_mb == ISO.DEFAULT_MEMORY_MB
        assert bad.cpus == ISO.DEFAULT_CPUS
        good = ISO.IsolationQuota.from_env({ISO.ENV_MEMORY_MB: "512",
                                            ISO.ENV_CPUS: "2.5"})
        assert good.memory_mb == 512 and good.cpus == 2.5
        assert good.worker_quota()["max_bytes"] == ISO.IsolationQuota().max_bytes

    def test_worker_op_vocabulary_matches_isolation_module(self):
        """op 词表两侧**逐字对齐**：不一致会被这条用例当场抓住"""
        from agent.digestion import isolation_worker as W
        assert set(W.OPS) == set(ISO.EXEC_OPS)
        assert set(ISO.PROBE_ONLY_OPS) <= set(ISO.EXEC_OPS)

    def test_worker_status_vocabulary_matches(self):
        from agent.digestion import isolation_worker as W
        assert W.STATUS_SUCCESS == ISO.STATUS_SUCCESS
        assert W.STATUS_ESCAPE_BLOCKED == ISO.STATUS_ESCAPE_BLOCKED
        assert W.STATUS_QUOTA_EXCEEDED == ISO.STATUS_QUOTA_EXCEEDED
        assert W.RESULT_BEGIN == ISO.RESULT_BEGIN
        assert W.RESULT_END == ISO.RESULT_END

    def test_probe_jobs_use_placeholders_and_probe_mode(self):
        jobs = ISO.build_probe_jobs(level="container", outside_root="/host-secrets")
        kinds = [ISO.probe_kind_of(job) for job in jobs]
        assert set(kinds) == set(ISO.PROBE_KINDS)
        for job in jobs:
            assert job["probe_mode"] is True
        files = next(j for j in jobs if ISO.probe_kind_of(j) == ISO.PROBE_FILES)
        rendered = json.dumps(files, ensure_ascii=False)
        assert "${work_root}" in rendered and "${source_root}" in rendered
        assert ISO.SOURCE_PROBE_DIRNAME in rendered

    def test_probe_jobs_scale_with_quota(self):
        """量级必须**由配额推导**：写死会让"没超限"与"限得太松"分不清"""
        executor = ISO.SubprocessHardenedExecutor()
        executor.quota.memory_mb = 128
        executor.quota.pids_limit = 16
        jobs = ISO.build_probe_jobs(
            level="subprocess_hardened", work_root="", source_root="",
            outside_root="", memory_mb=max(64, executor.quota.memory_mb * 2),
            pids_count=executor.quota.pids_limit + 16,
            cpu_seconds=20.0, timeout_s=4.0)
        mem = next(j for j in jobs if ISO.probe_kind_of(j) == ISO.PROBE_MEMORY)
        pids = next(j for j in jobs if ISO.probe_kind_of(j) == ISO.PROBE_PIDS)
        assert mem["steps"][0]["params"]["mb"] == 256 > executor.quota.memory_mb
        assert pids["steps"][0]["params"]["count"] == 32 > executor.quota.pids_limit
        assert pids["steps"][0]["params"]["hold_s"] >= 1.0

    def test_substitute_roots_is_deep_and_typed(self):
        payload = {"a": "${work_root}/x", "b": ["${source_root}"],
                   "c": {"d": "${outside_root}"}, "e": 3}
        out = ISO._substitute_roots(payload, work_root="/w", source_root="/s",
                                    outside_root="/o")
        assert out == {"a": "/w/x", "b": ["/s"], "c": {"d": "/o"}, "e": 3}

    def test_env_derived_credentials_are_derivation_marked(self):
        entries = ISO.env_derived_credentials()
        assert entries and all(e["derivation"] == "env_derived" for e in entries)
        assert any(".ssh/id_rsa" in e["suffix"] for e in entries)


# ════════════════════════════════════════════════════════════
#  结果解析与对比表
# ════════════════════════════════════════════════════════════


class TestResultParsingAndComparison:
    def test_parse_worker_result_requires_both_markers(self):
        body = json.dumps({"job_id": "j", "status": "success"})
        assert ISO.parse_worker_result(f"noise\n{ISO.RESULT_BEGIN}\n{body}\n"
                                       f"{ISO.RESULT_END}\n") == {"job_id": "j",
                                                                  "status": "success"}
        assert ISO.parse_worker_result(body) is None
        assert ISO.parse_worker_result(f"{ISO.RESULT_BEGIN}not json{ISO.RESULT_END}") is None
        assert ISO.parse_worker_result(f"{ISO.RESULT_BEGIN}[1,2]{ISO.RESULT_END}") is None

    def test_isolation_result_helpers(self):
        result = ISO.IsolationResult(level="container", ran=True,
                                     status=ISO.STATUS_SUCCESS,
                                     side_effects={"files_written": ["b", "a", "a"]})
        assert result.ok is True
        assert result.side_effect_set["files_written"] == ["a", "b"]
        assert result.to_dict()["ok"] is True

    def test_comparison_rows_without_runs_marks_untested(self):
        rows = ISO.comparison_rows({})
        assert rows
        for row in rows:
            for level in ISO.ISOLATION_LEVELS:
                assert row[level] == "未测"

    def test_render_comparison_markdown_lists_all_levels(self):
        markdown = ISO.render_comparison_markdown(ISO.comparison_rows({}))
        assert "| 边界 |" in markdown
        for level in ISO.ISOLATION_LEVELS:
            assert level in markdown
        assert "不以后验推断补写" in markdown

    def test_summarize_probe_never_runs_are_visible(self):
        result = ISO.IsolationResult(level="container", ran=False,
                                     status=ISO.STATUS_REFUSED,
                                     error="Docker 不可用", refused_reason="daemon 未运行")
        findings = ISO.summarize_probe(ISO.PROBE_ENV, result)
        assert findings["ran"] is False
        assert findings["status"] == ISO.STATUS_REFUSED

    def test_cell_marks_unrun_levels_instead_of_guessing(self):
        rows = ISO.comparison_rows({
            "container": [ISO.ProbeRun(probe_kind=ISO.PROBE_ENV,
                                       result=ISO.IsolationResult(
                                           level="container", ran=False,
                                           status=ISO.STATUS_REFUSED))]})
        row = next(r for r in rows if r["aspect"] == "env_home")
        assert "未运行" in row["container"]
        assert row["subprocess_hardened"] == "未测"


class TestProbeSuitePlumbing:
    """探针套件的**管线**（结果的归纳/单元格判定/未运行不外推）

    真实边界值由 `scripts/verify_isolation.py` 实测；这里保证的是"管线本身
    不会把没跑成的东西写成跑成了"。
    """

    def test_suite_without_isolation_reports_refusal_per_probe(self):
        runs = ISO.run_probe_suite(ISO.InProcessExecutor())
        assert len(runs) == len(ISO.PROBE_KINDS)
        for run in runs:
            assert run.result.ran is False
            assert run.result.status == ISO.STATUS_REFUSED
            assert run.to_dict()["result"]["status"] == ISO.STATUS_REFUSED

    def test_suite_derives_probe_magnitudes_from_quota(self):
        """量级由配额推导：内存要两倍、进程数要超额、CPU 要活过墙钟"""
        executor = ISO.SubprocessHardenedExecutor()
        executor.quota.memory_mb = 512
        executor.quota.pids_limit = 8
        executor.quota.timeout_s = 4.0
        job = {"job_id": "x", "steps": [{"op": "env_dump", "params": {}}]}
        prepared = ISO.build_probe_jobs(
            level="subprocess_hardened", work_root="", source_root="",
            outside_root="", memory_mb=executor.quota.memory_mb * 2,
            pids_count=executor.quota.pids_limit + 16, cpu_seconds=24.0,
            timeout_s=4.0)
        mem = next(j for j in prepared if ISO.probe_kind_of(j) == ISO.PROBE_MEMORY)
        cpu = next(j for j in prepared if ISO.probe_kind_of(j) == ISO.PROBE_CPU)
        assert mem["steps"][0]["params"]["mb"] == 1024
        assert cpu["steps"][0]["params"]["seconds"] > cpu["timeout_s"]
        assert job  # 保持引用（job 只是给读者的形状示意）

    def test_run_probe_suite_accepts_explicit_jobs(self, tmp_path):
        jobs = [{"job_id": "j", "meta": {"probe_kind": ISO.PROBE_ENV},
                 "steps": [{"op": "env_dump", "params": {}}]}]
        runs = ISO.run_probe_suite(ISO.SubprocessHardenedExecutor(), jobs=jobs)
        assert len(runs) == 1
        assert runs[0].probe_kind == ISO.PROBE_ENV
        assert runs[0].result.ran is True
        findings = runs[0].findings["env"]
        assert findings["HOME"] == "" and findings["SSH_AUTH_SOCK"] == ""

    def test_cell_helpers(self):
        assert "被终止" in ISO._limit_cell({"killed": True})
        assert "软限" in ISO._limit_cell({"memory_error": True})
        assert "未终止" in ISO._limit_cell({"survived": True, "allocated_mb": 8})
        assert ISO._limit_cell({}) == "未测"
        assert "非宿主 HOME" in ISO._raw_home_cell("/nonexistent")
        assert ISO._raw_home_cell("") == "`''`"
        assert "uid=0" in ISO._user_cell({"uid": 0}) and "root" in \
            ISO._user_cell({"uid": 0})
        assert "非 root" in ISO._user_cell({"uid": 65534})
        assert "Windows" in ISO._user_cell({})

    def test_cell_reports_parse_failure_instead_of_crashing(self):
        def _boom(_findings):
            raise RuntimeError("坏的 findings")
        run = ISO.ProbeRun(probe_kind=ISO.PROBE_ENV,
                           result=ISO.IsolationResult(level="container", ran=True,
                                                      status=ISO.STATUS_SUCCESS))
        assert "解析失败" in ISO._cell([run], ISO.PROBE_ENV, _boom)

    def test_executor_job_defaults_and_describe(self):
        executor = ISO.SubprocessHardenedExecutor()
        job = executor._job_for({"steps": []}, work_root="/w", source_root="/s")
        assert job["work_root"] == "/w" and job["source_root"] == "/s"
        assert job["network"] == ISO.NETWORK_NONE
        assert job["scrub_env"] is True
        assert job["quota"]["max_steps"] == ISO.IsolationQuota().max_steps
        described = executor.describe()
        assert described["level"] == "subprocess_hardened"
        assert described["worker"].endswith(ISO.WORKER_REL_PATH)

    def test_work_dir_creation_and_cleanup(self, tmp_path):
        executor = ISO.SubprocessHardenedExecutor(work_dir=str(tmp_path / "w"))
        work = executor._make_work_dir()
        assert os.path.isdir(work)
        executor._cleanup(work)
        assert os.path.isdir(work), "显式指定的工作目录不得被自动删除"

    def test_fallback_isolation_env_matches_s4_04_rules(self):
        env = ISO._fallback_isolation_env()
        for name in ("HOME", "USERPROFILE", "SSH_AUTH_SOCK", "HTTP_PROXY"):
            assert env[name] == ""
        assert env["CP_SANDBOX_HOME"] == "0"

    def test_hardened_env_survives_isolation_helper_failure(self, monkeypatch,
                                                            tmp_path):
        """复用失败也不得放弃清空：兜底实现必须仍然清掉敏感项"""
        import builtins
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "agent.subagent.sandbox":
                raise ImportError("模拟不可用")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        executor = ISO.SubprocessHardenedExecutor()
        env = executor._hardened_env(str(tmp_path))
        assert env["HOME"] == "" and env["SSH_AUTH_SOCK"] == ""
        assert env["PYTHONPATH"] == ""


# ════════════════════════════════════════════════════════════
#  "未双写"实测工具
# ════════════════════════════════════════════════════════════


class TestSnapshotAndDiff:
    def test_unchanged_when_nothing_touched(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        (target / "a.txt").write_text("a", encoding="utf-8")
        before = ISO.snapshot_paths([str(target)])
        after = ISO.snapshot_paths([str(target)])
        diff = ISO.diff_snapshot(before, after)
        assert diff["unchanged"] is True
        assert not diff["created"] and not diff["changed"] and not diff["removed"]

    def test_detects_created_changed_removed(self, tmp_path):
        target = tmp_path / "real"
        target.mkdir()
        (target / "keep.txt").write_text("keep", encoding="utf-8")
        (target / "gone.txt").write_text("gone", encoding="utf-8")
        before = ISO.snapshot_paths([str(target)])
        (target / "keep.txt").write_text("MODIFIED", encoding="utf-8")
        (target / "gone.txt").unlink()
        (target / "new.txt").write_text("new", encoding="utf-8")
        diff = ISO.diff_snapshot(before, ISO.snapshot_paths([str(target)]))
        assert diff["unchanged"] is False
        # 「内容被改写」必须说成 changed，而不是"删了一个又建了一个"——
        # 后者会把"被改写"这件严重的事说轻（口径问题，不是措辞问题）
        assert any("keep.txt" in p for p in diff["changed"]), diff
        assert any("gone.txt" in p for p in diff["removed"]), diff
        assert any("new.txt" in p for p in diff["created"]), diff

    def test_missing_paths_are_reported(self, tmp_path):
        snapshot = ISO.snapshot_paths([str(tmp_path / "nope.txt")])
        assert snapshot["missing"] == [str(tmp_path / "nope.txt")]
        assert snapshot["files"] == {}


# ════════════════════════════════════════════════════════════
#  执行器选择
# ════════════════════════════════════════════════════════════


class TestExecutorFor:
    @pytest.mark.parametrize("level,cls_name", [
        ("container", "ContainerExecutor"),
        ("subprocess_hardened", "SubprocessHardenedExecutor"),
        ("in_process", "InProcessExecutor"),
    ])
    def test_level_maps_to_matching_executor(self, level, cls_name):
        plan = ISO.IsolationPlan(level=level)
        executor = ISO.executor_for(plan, env={})
        assert type(executor).__name__ == cls_name
        assert executor.level == level
        assert executor.describe()["boundaries"]["level"] == level

    def test_in_process_executor_refuses_to_run(self):
        result = ISO.InProcessExecutor().run({"job_id": "x", "steps": []})
        assert result.ran is False
        assert result.status == ISO.STATUS_REFUSED
        assert result.error_code == ISO.ERR_REFUSED
        assert result.refused_reason and "拒绝" in result.refused_reason
        assert result.ok is False

    def test_container_executor_refuses_without_docker(self):
        executor = ISO.ContainerExecutor(prober=fake_prober(available=False))
        result = executor.run({"job_id": "x", "steps": []})
        assert result.ran is False
        assert result.status == ISO.STATUS_REFUSED
        assert result.error_code == ISO.ERR_DOCKER
        assert "不降格冒充" in "；".join(result.honest_notes)
        assert executor.build_argv()[0] == ISO.DEFAULT_DOCKER_CLI


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
