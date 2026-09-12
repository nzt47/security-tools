"""隔离执行器：执行体协议 + 子进程路径 + 容器路径（TASK-S8-03 步骤 2/3）

**两条路径的用例分开写、分开 gate**：
- 子进程路径在**任何**装有 Python 的环境都必须真跑（这是 Windows 开发机的唯一路径）；
- 容器路径 `skipif` Docker 不可用 —— 且跳过理由**如实写明**，不允许把子进程的
  结果拿来充当容器的证据（那正是任务书 §五 禁止的"冒充容器"）。

真实边界（env/网络/资源/文件）的**机器可读证据**由 `scripts/verify_isolation.py`
产出；本文件保证的是"执行器本身按声明的参数执行、且失败如实上报"。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from agent.digestion import isolation as ISO
from agent.digestion import isolation_worker as W

from isolation_util import fake_prober

#: 容器用例的 gate（CI 无 Docker ⇒ **跳过并如实标注**，不得伪通过）
DOCKER_REQUIRED = pytest.mark.skipif(
    not ISO.probe_docker().available,
    reason=("容器路径需要可用的 Docker CLI 与 daemon；本机探测结果："
            + "；".join(ISO.probe_docker().reasons or ["（可用）"])))


def _run_worker(job: dict, *, env: dict | None = None, timeout: float = 30.0,
                cwd: str | None = None):
    """直接驱动执行体（不经执行器）：返回 (rc, stdout, stderr)"""
    argv = [sys.executable, "-I",
            os.path.join(ISO.REPO_ROOT, ISO.WORKER_REL_PATH), "--job", "-"]
    proc = subprocess.run(argv, input=json.dumps(job, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout, env=env, cwd=cwd)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ════════════════════════════════════════════════════════════
#  执行体协议（stdlib-only；可在容器内裸跑）
# ════════════════════════════════════════════════════════════


class TestWorkerProtocol:
    def test_worker_is_stdlib_only(self):
        """执行体**不得**导入 agent.*：容器里只挂源码树，导入主仓依赖就等于
        把宿主那一整套东西搬进边界内（隔离的意义随之消失）"""
        source = open(os.path.join(ISO.REPO_ROOT, ISO.WORKER_REL_PATH),
                      encoding="utf-8").read()
        assert "import agent" not in source
        assert "from agent" not in source

    def test_result_markers_are_emitted(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        rc, stdout, _ = _run_worker({"job_id": "j1", "work_root": str(work),
                                     "steps": [{"op": "env_dump", "params": {}}]})
        assert rc == 0
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        assert parsed["job_id"] == "j1" and parsed["status"] == "success"
        assert "env_raw" in parsed and "platform" in parsed

    def test_write_outside_work_root_is_denied(self, tmp_path):
        """非探针作业：唯一可写根之外的写入一律 `escape_blocked`（**不静默**）"""
        work = tmp_path / "work"
        work.mkdir()
        outside = tmp_path / "outside.txt"
        rc, stdout, _ = _run_worker(
            {"job_id": "j2", "work_root": str(work), "probe_mode": False,
             "steps": [{"op": "write_file",
                        "params": {"path": str(outside), "content": "x"}}]})
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        assert parsed["status"] == W.STATUS_ESCAPE_BLOCKED
        assert parsed["error_code"] == W.ERR_ESCAPE
        assert not outside.exists(), "被拒的写入不得真的落盘"

    @pytest.mark.parametrize("op", ["escape_write", "net_probe_raw"])
    def test_probe_only_ops_are_gated(self, tmp_path, op):
        """`probe_mode=false` 的作业拿不到越界/直连探针 —— 否则探针就是逃逸后门"""
        work = tmp_path / "work"
        work.mkdir()
        params = ({"path": str(tmp_path / "x.txt"), "content": "x"}
                  if op == "escape_write" else {"host": "1.1.1.1", "port": 53})
        _, stdout, _ = _run_worker(
            {"job_id": "j3", "work_root": str(work), "probe_mode": False,
             "steps": [{"op": op, "params": params}]})
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        assert parsed["status"] == W.STATUS_ESCAPE_BLOCKED
        assert "probe_mode" in parsed["error"]

    def test_quota_exceeded_is_not_silent(self, tmp_path):
        work = tmp_path / "work"
        work.mkdir()
        _, stdout, _ = _run_worker(
            {"job_id": "j4", "work_root": str(work),
             "quota": {"max_files_written": 1},
             "steps": [{"op": "write_file", "params": {"path": "a.txt", "content": "1"}},
                       {"op": "write_file", "params": {"path": "b.txt", "content": "2"}}]})
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        assert parsed["status"] == W.STATUS_QUOTA_EXCEEDED
        assert parsed["error_code"] == W.ERR_QUOTA_FILES

    def test_bad_job_reports_error_and_exit_code(self):
        argv = [sys.executable, "-I",
                os.path.join(ISO.REPO_ROOT, ISO.WORKER_REL_PATH), "--job", "-"]
        proc = subprocess.run(argv, input="{not json", capture_output=True,
                              text=True, encoding="utf-8", timeout=30)
        assert proc.returncode == 2
        parsed = ISO.parse_worker_result(proc.stdout or "")
        assert parsed is not None
        assert parsed["status"] == W.STATUS_ERROR
        assert parsed["error_code"] == W.ERR_BAD_JOB

    def test_env_derived_credential_path_underivable_when_home_empty(self, tmp_path):
        """`HOME`/`USERPROFILE` 为空 ⇒ 凭据路径**推导不出来**（清空环境买到的边界）"""
        work = tmp_path / "work"
        work.mkdir()
        env = {"PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get(
            "SystemRoot", ""), "HOME": "", "USERPROFILE": ""}
        _, stdout, _ = _run_worker(
            {"job_id": "j5", "work_root": str(work), "probe_mode": True,
             "steps": [{"op": "credential_scan",
                        "params": {"paths": [{"derivation": "env_derived",
                                              "suffix": ".ssh/id_rsa"}]}}]},
            env=env)
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        entry = parsed["outputs"][0]["entries"][0]
        assert entry["visible"] is False
        assert "不可推导" in entry["reason"]

    def test_escape_write_probe_reports_both_outcomes(self, tmp_path):
        """越界探针**如实**报告能不能越出去（这是对比表的关键一格）"""
        work = tmp_path / "work"
        work.mkdir()
        _, stdout, _ = _run_worker(
            {"job_id": "j6", "work_root": str(work), "probe_mode": True,
             "steps": [{"op": "escape_write",
                        "params": {"path": str(tmp_path / "escaped.txt"),
                                   "content": "x"}}]})
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        out = parsed["outputs"][0]
        assert out["escaped"] is True  # 执行体这一侧没有内核约束 ⇒ 如实记 True
        assert (tmp_path / "escaped.txt").exists()
        (tmp_path / "escaped.txt").unlink()

    def test_env_scrub_records_raw_platform_values(self, tmp_path):
        """先留证再清空：`env_raw` 记平台原始值，`values` 记生效值"""
        work = tmp_path / "work"
        work.mkdir()
        env = dict(os.environ, HOME="/somewhere-raw", SSH_AUTH_SOCK="/tmp/agent.sock")
        _, stdout, _ = _run_worker(
            {"job_id": "j7", "work_root": str(work), "scrub_env": True,
             "steps": [{"op": "env_dump", "params": {"names": ["HOME",
                                                              "SSH_AUTH_SOCK"]}}]},
            env=env)
        parsed = ISO.parse_worker_result(stdout)
        assert parsed is not None
        assert parsed["env_raw"]["HOME"] == "/somewhere-raw"
        assert parsed["env_raw"]["SSH_AUTH_SOCK"] == "/tmp/agent.sock"
        assert parsed["outputs"][0]["values"]["HOME"] == ""
        assert parsed["outputs"][0]["values"]["SSH_AUTH_SOCK"] == ""


# ════════════════════════════════════════════════════════════
#  执行体（**进程内直测**：op 语义 / 守卫 / 配额 / 入口）
# ════════════════════════════════════════════════════════════
#
# 为什么还要进程内直测：执行体平时跑在子进程/容器里，`coverage` 追踪不到它——
# 于是"它在容器里跑过"就等于"它没被测过"。这里把 op 语义单独测一遍，
# 既补上可追踪的覆盖，也让失败定位落在**具体 op**上而不是"容器里没输出"。


class TestWorkerOpsInProcess:
    def _env(self, tmp_path, **job):
        payload = {"work_root": str(tmp_path / "work"), "scrub_env": False}
        payload.update(job)
        (tmp_path / "work").mkdir(exist_ok=True)
        return W.WorkerEnv(payload)

    def test_norm_and_abspath_and_under(self, tmp_path):
        assert W._norm("a\\b") == "a/b"
        assert W._norm(None) == ""
        assert W._abspath("rel.txt", base=str(tmp_path)).endswith("rel.txt")
        assert W._abspath(str(tmp_path / "abs.txt"), base="/x") == \
            str(tmp_path / "abs.txt").replace("\\", "/")
        with pytest.raises(ValueError):
            W._abspath("", base=str(tmp_path))
        # 两侧都归一分隔符：Windows 上 "C:/a/b" 与 "C:\\a\\b" 必须是同一把尺子
        assert W._under(f"{tmp_path}/a", str(tmp_path)) is True
        assert W._under(str(tmp_path), str(tmp_path)) is True
        assert W._under(str(tmp_path) + "x", str(tmp_path)) is False
        assert W._under("anything", "") is False

    def test_file_ops_round_trip(self, tmp_path):
        env = self._env(tmp_path)
        written = W._op_write_file(env, {"path": "d/a.txt", "content": "hello"})
        assert written["ok"] and written["bytes"] == 5
        assert W._op_append_file(env, {"path": "d/a.txt", "content": "!"})["ok"]
        assert W._op_read_file(env, {"path": "d/a.txt"})["content"] == "hello!"
        assert W._op_stat(env, {"path": "d/a.txt"})["size"] == 6
        assert "a.txt" in W._op_list_dir(env, {"path": "d"})["names"]
        assert W._op_grep(env, {"pattern": "hello", "path": "d/a.txt"})["hits"]
        assert W._op_create_dir(env, {"path": "d/sub"})["ok"]
        assert W._op_delete_file(env, {"path": "d/a.txt"})["existed"] is True
        assert W._op_stat(env, {"path": "d/a.txt"})["ok"] is False
        assert set(env.side_effects["files_written"]) == {
            W._norm(str(tmp_path / "work" / "d" / "a.txt")),
            W._norm(str(tmp_path / "work" / "d" / "a.txt")),
            W._norm(str(tmp_path / "work" / "d" / "sub"))}
        assert env.side_effects["files_deleted"]

    def test_writable_guard_rejects_outside_paths(self, tmp_path):
        env = self._env(tmp_path)
        with pytest.raises(W.EscapeBlocked):
            env.writable(str(tmp_path / "outside.txt"))
        assert W._op_write_file.__name__  # 保持引用（避免 lint 误删导入语义）

    def test_readable_allows_source_root_only(self, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "s.txt").write_text("s", encoding="utf-8")
        env = self._env(tmp_path, source_root=str(source))
        assert W._op_read_file(env, {"path": str(source / "s.txt")})["ok"]
        with pytest.raises(W.EscapeBlocked):
            env.readable(str(tmp_path / "elsewhere.txt"))
        probe = self._env(tmp_path, probe_mode=True)
        assert probe.readable(str(tmp_path / "elsewhere.txt"))

    def test_byte_quota_charges(self, tmp_path):
        env = self._env(tmp_path, quota={"max_bytes": 4})
        with pytest.raises(W.QuotaExceeded) as err:
            env._charge("bytes", 5)
        assert err.value.code == W.ERR_QUOTA_BYTES

    def test_step_quota_counts_independently(self, tmp_path):
        """步数是**独立计数**：不依赖调用方有没有往 `env.steps` 里追加"""
        env = self._env(tmp_path, quota={"max_steps": 1})
        env._charge("steps")
        with pytest.raises(W.QuotaExceeded):
            env._charge("steps")

    def test_file_and_external_quotas_come_from_recorded_effects(self, tmp_path):
        """写/删/外呼的配额由**已记录副作用**驱动（不是空转的计数器）"""
        env = self._env(tmp_path, quota={"max_files_written": 1, "max_deleted": 1,
                                         "max_external_calls": 1})
        W._op_write_file(env, {"path": "a.txt", "content": "1"})
        with pytest.raises(W.QuotaExceeded):
            W._op_write_file(env, {"path": "b.txt", "content": "2"})
        W._op_delete_file(env, {"path": "a.txt"})
        with pytest.raises(W.QuotaExceeded):
            W._op_delete_file(env, {"path": "b.txt"})
        W._op_external_call(env, {"label": "one"})
        with pytest.raises(W.QuotaExceeded):
            W._op_external_call(env, {"label": "two"})

    def test_qint_falls_back_on_non_numeric_quota(self, tmp_path):
        env = self._env(tmp_path, quota={"max_steps": "many"})
        assert env._qint("max_steps") == W.DEFAULT_QUOTA["max_steps"]

    def test_unknown_open_is_simulated_not_executed(self, tmp_path):
        env = self._env(tmp_path)
        out = W.OPS.get("shell_execute")
        assert out is None, "未知操作不得有真实执行通道"
        result = W.run_job({"job_root": str(tmp_path), "scrub_env": False,
                            "work_root": str(tmp_path / "work"),
                            "steps": [{"op": "shell_execute",
                                       "params": {"cmd": "echo hi"}}]})
        assert result["outputs"][0] == {"ok": True, "simulated": "shell_execute"}

    def test_external_call_is_recorded_and_never_sent(self, tmp_path):
        env = self._env(tmp_path, network="none")
        denied = W._op_external_call(env, {"label": "deploy"})
        assert denied["ok"] is False and denied["error_code"] == W.ERR_DENIED
        assert denied["simulated"] == "deploy"
        assert env.side_effects["external_calls"] == ["deploy"]
        allow = self._env(tmp_path, network="whitelist",
                          network_allow=["api.internal"])
        granted = W._op_external_call(allow, {"label": "http_get",
                                             "host": "api.internal"})
        assert granted["ok"] is True and granted["recorded_only"] is True

    def test_env_dump_and_home_expand(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", "/fake-home")
        monkeypatch.setenv("SSH_AUTH_SOCK", "/fake.sock")
        env = self._env(tmp_path)
        out = W._op_env_dump(env, {"names": ["HOME", "SSH_AUTH_SOCK", "NOPE"]})
        assert out["values"]["HOME"] == "/fake-home"
        assert "NOPE" not in out["present"]
        assert set(out["present"]) == {"HOME", "SSH_AUTH_SOCK"}

    def test_credential_scan_env_derived_and_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", "")
        monkeypatch.setenv("USERPROFILE", "")
        env = self._env(tmp_path, probe_mode=True)
        out = W._op_credential_scan(env, {"paths": [
            {"derivation": "env_derived", "suffix": ".ssh/id_rsa"},
            {"derivation": "absolute", "path": str(tmp_path / "nope.txt")}]})
        assert out["visible_count"] == 0
        assert "不可推导" in out["entries"][0]["reason"]

    def test_credential_scan_reads_when_home_present(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / ".ssh").mkdir(parents=True)
        (home / ".ssh" / "id_rsa").write_text("KEY", encoding="utf-8")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", "")
        env = self._env(tmp_path, probe_mode=True)
        out = W._op_credential_scan(env, {"paths": [
            {"derivation": "env_derived", "suffix": ".ssh/id_rsa"}]})
        assert out["visible_count"] == 1

    def test_net_probe_respects_policy(self, tmp_path):
        none_env = self._env(tmp_path, network="none")
        out = W._op_net_probe(none_env, {"host": "1.1.1.1", "port": 53})
        assert out["reachable"] is False and out["enforced_by"] == "policy"
        wl_env = self._env(tmp_path, network="whitelist",
                           network_allow=["example.com:443"])
        blocked = W._op_net_probe(wl_env, {"host": "1.1.1.1", "port": 53})
        assert blocked["reachable"] is False and "白名单" in blocked["reason"]

    def test_net_probe_raw_requires_probe_mode(self, tmp_path):
        env = self._env(tmp_path, network="none")
        with pytest.raises(W.EscapeBlocked):
            W._op_net_probe_raw(env, {"host": "1.1.1.1", "port": 53})
        probe = self._env(tmp_path, network="none", probe_mode=True)
        out = W._op_net_probe_raw(probe, {"host": "127.0.0.1", "port": 1,
                                          "timeout_s": 0.5})
        assert out["ok"] is True and out["enforced_by"] == "none"

    def test_escape_write_requires_probe_mode(self, tmp_path):
        env = self._env(tmp_path)
        with pytest.raises(W.EscapeBlocked):
            W._op_escape_write(env, {"path": str(tmp_path / "x.txt"),
                                     "content": "x"})
        probe = self._env(tmp_path, probe_mode=True)
        out = W._op_escape_write(probe, {"path": str(tmp_path / "x.txt"),
                                        "content": "x"})
        assert out["escaped"] is True
        (tmp_path / "x.txt").unlink()

    def test_mem_alloc_and_cpu_spin_and_fork(self, tmp_path):
        env = self._env(tmp_path)
        mem = W._op_mem_alloc(env, {"mb": 8})
        assert mem["allocated_mb"] == 8 and mem["memory_error"] is False
        spin = W._op_cpu_spin(env, {"seconds": 0.05})
        assert spin["spun_s"] >= 0.0
        forked = W._op_fork_procs(env, {"count": 1, "hold_s": 0.1})
        assert forked["requested"] == 1 and forked["spawned"] == 1

    def test_run_job_collects_steps_outputs_and_platform(self, tmp_path):
        result = W.run_job({"job_id": "inproc", "scrub_env": False,
                            "work_root": str(tmp_path / "work"),
                            "steps": [{"op": "write_file",
                                       "params": {"path": "a.txt", "content": "1"}},
                                      {"op": "read_file",
                                       "params": {"path": "a.txt"}}]})
        assert result["status"] == W.STATUS_SUCCESS
        assert result["steps"] == ["write_file", "read_file"]
        assert result["platform"]["python"]
        assert result["boundaries"]["work_root"].endswith("work")
        assert result["env_raw"] and "HOME" in result["env_raw"]

    def test_run_job_reports_missing_op_and_bad_quota(self, tmp_path):
        bad = W.run_job({"work_root": str(tmp_path / "work"), "scrub_env": False,
                         "steps": [{"params": {}}]})
        assert bad["status"] == W.STATUS_ERROR
        assert bad["error_code"] == W.ERR_BAD_JOB
        tolerant = W.run_job({"work_root": str(tmp_path / "work"),
                              "scrub_env": False, "quota": "not-a-dict",
                              "steps": [{"op": "env_dump", "params": {}}]})
        assert tolerant["status"] == W.STATUS_SUCCESS
        assert any("quota 字段非法" in n for n in tolerant["notes"])

    def test_fixtures_are_seeded_into_the_work_root_only(self, tmp_path):
        """夹具播种：写进可写根，越界夹具**拒绝并留痕**（不静默丢）"""
        work = tmp_path / "work"
        result = W.run_job({
            "work_root": str(work), "scrub_env": False, "probe_mode": False,
            "fixtures": {"cand/a.txt": "seed",
                         str(tmp_path / "outside.txt"): "nope"},
            "steps": [{"op": "read_file", "params": {"path": "cand/a.txt"}}]})
        assert result["status"] == W.STATUS_SUCCESS
        assert result["outputs"][0]["content"] == "seed"
        assert not (tmp_path / "outside.txt").exists()
        assert any("夹具越界未播种" in n for n in result["notes"])
        bad = W.run_job({"work_root": str(work), "scrub_env": False,
                         "fixtures": "not-a-dict", "steps": []})
        assert any("fixtures 字段非法" in n for n in bad["notes"])

    def test_run_job_records_step_exception_without_aborting(self, tmp_path):
        result = W.run_job({"work_root": str(tmp_path / "work"),
                            "scrub_env": False,
                            "steps": [{"op": "read_file",
                                       "params": {"path": "missing.txt"}},
                                      {"op": "env_dump", "params": {}}]})
        assert result["outputs"][0]["ok"] is False
        assert result["outputs"][1]["ok"] is True
        assert result["status"] == W.STATUS_SUCCESS

    def test_worker_scrub_is_opt_in(self, monkeypatch, tmp_path):
        """清空环境是**破坏性**动作 ⇒ 必须显式要求（默认不清）"""
        monkeypatch.setenv("HOME", "/should-survive")
        W.run_job({"work_root": str(tmp_path), "steps": []})
        import os as _os
        assert _os.environ["HOME"] == "/should-survive"
        W.run_job({"work_root": str(tmp_path), "scrub_env": True, "steps": []})
        assert _os.environ["HOME"] == ""

    def test_main_reads_and_writes_files(self, tmp_path, capsys):
        job_path = tmp_path / "job.json"
        out_path = tmp_path / "out.json"
        job_path.write_text(json.dumps({
            "job_id": "file-io", "scrub_env": False,
            "work_root": str(tmp_path / "work"),
            "steps": [{"op": "env_dump", "params": {"names": ["HOME"]}}]}),
            encoding="utf-8")
        rc = W.main(["--job", str(job_path), "--out", str(out_path)])
        assert rc == 0
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        assert payload["job_id"] == "file-io"
        assert W.RESULT_BEGIN in capsys.readouterr().out

    def test_main_reports_bad_job_file(self, tmp_path, capsys):
        bad = tmp_path / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        rc = W.main(["--job", str(bad), "--out", "-"])
        assert rc == 2
        assert W.RESULT_BEGIN in capsys.readouterr().out


# ════════════════════════════════════════════════════════════
#  强隔离子进程路径（Windows 无 Docker 时的唯一路径；必须真跑）
# ════════════════════════════════════════════════════════════


class TestSubprocessHardenedExecutor:
    def test_runs_and_clears_environment(self):
        executor = ISO.SubprocessHardenedExecutor()
        result = executor.run({"job_id": "sub-env",
                               "steps": [{"op": "env_dump", "params": {}}]})
        assert result.ran and result.ok, result.error
        values = result.outputs[0]["values"]
        for name in ("HOME", "USERPROFILE", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
                     "AWS_ACCESS_KEY_ID", "GITHUB_TOKEN", "KUBECONFIG",
                     "HTTP_PROXY", "HTTPS_PROXY"):
            assert values[name] == "", f"{name} 未被清空（隔离声明不成立）"
        assert result.outputs[0]["present"] == []

    def test_env_matches_s4_04_isolation_paradigm(self, monkeypatch):
        """复用 S4-04 已验证范式：本等级的 env 与 `apply_isolation_env` 同口径"""
        from agent.subagent.sandbox import apply_isolation_env
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "SHOULD-BE-DROPPED")
        monkeypatch.setenv("SSH_AUTH_SOCK", "/tmp/agent.sock")
        executor = ISO.SubprocessHardenedExecutor()
        env = executor._hardened_env(tempfile.gettempdir())
        reference = apply_isolation_env(
            {k: v for k, v in os.environ.items() if not k.startswith("CP_")},
            container_root="", trusted=False)
        for name in ("HOME", "USERPROFILE", "SSH_AUTH_SOCK", "SSH_AGENT_PID",
                     "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
            assert env.get(name) == reference.get(name) == ""
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert env["TEMP"] == tempfile.gettempdir()
        assert env["PYTHONPATH"] == ""

    def test_work_dir_isolation_and_cleanup(self):
        """候选在一次性临时目录内跑，且该目录用后即删（不往宿主留垃圾）"""
        executor = ISO.SubprocessHardenedExecutor()
        result = executor.run({"job_id": "sub-work",
                               "steps": [{"op": "write_file",
                                          "params": {"path": "in/note.txt",
                                                     "content": "hi"}},
                                         {"op": "read_file",
                                          "params": {"path": "in/note.txt"}}]})
        assert result.ok, result.error
        written = result.side_effect_set["files_written"]
        assert written and "note.txt" in written[0]
        assert not os.path.exists(os.path.dirname(written[0])), \
            "临时工作目录应当已被清理"

    def test_keep_work_dir_option(self, tmp_path):
        work = tmp_path / "keepme"
        executor = ISO.SubprocessHardenedExecutor(work_dir=str(work))
        result = executor.run({"job_id": "sub-keep",
                               "steps": [{"op": "write_file",
                                          "params": {"path": "x.txt",
                                                     "content": "1"}}]})
        assert result.ok and (work / "x.txt").exists()

    def test_source_tree_write_is_blocked_by_the_guard(self):
        """源码树受**协作式**路径守卫保护（其局限由探针如实暴露）"""
        repo_file = os.path.join(ISO.REPO_ROOT, "agent", "digestion", "shadow.py")
        executor = ISO.SubprocessHardenedExecutor()
        result = executor.run({"job_id": "sub-guard",
                               "steps": [{"op": "write_file",
                                          "params": {"path": repo_file,
                                                     "content": "tampered"}}]})
        assert result.status == ISO.STATUS_ESCAPE_BLOCKED
        assert os.path.getsize(repo_file) > 1000, "源码不得被改动"
        with open(repo_file, encoding="utf-8") as fh:
            assert "tampered" not in fh.readline()

    def test_timeout_kills_and_is_marked_quota_exceeded(self):
        executor = ISO.SubprocessHardenedExecutor()
        executor.quota.timeout_s = 1.0
        result = executor.run({"job_id": "sub-timeout", "timeout_s": 1.0,
                               "steps": [{"op": "cpu_spin",
                                          "params": {"seconds": 20.0}}]})
        assert result.status == ISO.STATUS_TIMEOUT
        assert result.quota_exceeded is True and result.killed is True
        assert result.error_code == ISO.ERR_TIMEOUT

    def test_missing_worker_is_reported_as_spawn_error(self, tmp_path):
        executor = ISO.SubprocessHardenedExecutor(source_root=str(tmp_path))
        result = executor.run({"job_id": "sub-missing", "steps": []})
        assert result.ran is False
        assert result.error_code == ISO.ERR_SPAWN
        assert "不存在" in result.error

    def test_no_result_from_child_is_not_success(self, monkeypatch):
        """解析不到标记 ⇒ **失败**（绝不把"无输出"当成功）"""
        executor = ISO.SubprocessHardenedExecutor()
        monkeypatch.setattr(ISO, "parse_worker_result", lambda _stdout: None)
        result = executor.run({"job_id": "sub-noresult",
                               "steps": [{"op": "env_dump", "params": {}}]})
        assert result.ok is False
        assert result.status in (ISO.STATUS_ERROR, ISO.STATUS_KILLED)
        assert any("无结果 ≠ 成功" in note for note in result.honest_notes)

    def test_worker_receives_stdin_job_with_placeholders_resolved(self):
        executor = ISO.SubprocessHardenedExecutor()
        result = executor.run({"job_id": "sub-placeholder",
                               "steps": [{"op": "write_file",
                                          "params": {"path": "${work_root}/p.txt",
                                                     "content": "1"}}]})
        assert result.ok
        assert "${work_root}" not in json.dumps(result.side_effect_set)

    @pytest.mark.skipif(sys.platform == "win32",
                        reason="Windows 无 resource 模块 ⇒ 无 rlimit（如实差距，非缺陷）")
    def test_posix_resource_limits_are_installed(self):
        assert ISO._posix_limits(ISO.IsolationQuota()) is not None

    @pytest.mark.skipif(sys.platform != "win32",
                        reason="仅在 Windows 上验证「如实标注无 rlimit」")
    def test_windows_has_no_rlimit_and_says_so(self):
        assert ISO._posix_limits(ISO.IsolationQuota()) is None
        gaps = "；".join(ISO.isolation_boundaries(
            "subprocess_hardened").not_guaranteed)
        assert "Windows 上没有 rlimit 等价物" in gaps


# ════════════════════════════════════════════════════════════
#  容器路径（argv 断言恒可测；真实执行由 Docker gate）
# ════════════════════════════════════════════════════════════


class TestContainerExecutorArgv:
    def test_argv_carries_every_declared_isolation_flag(self):
        executor = ISO.ContainerExecutor()
        argv = executor.build_argv()
        joined = " ".join(argv)
        assert argv[0] == ISO.DEFAULT_DOCKER_CLI and argv[1] == "run"
        assert "--network none" in joined
        assert "--read-only" in argv
        assert f"--user {ISO.DEFAULT_CONTAINER_USER}" in joined
        assert "--cap-drop ALL" in joined
        assert "no-new-privileges" in joined
        assert f"--memory {executor.quota.memory_mb}m" in joined
        assert f"--cpus {executor.quota.cpus}" in joined
        assert f"--pids-limit {executor.quota.pids_limit}" in joined
        assert "mode=1777" in joined, "非 root 要能写 tmpfs ⇒ 必须给足权限位"
        assert "target=/src,readonly" in joined
        assert "--privileged" not in argv

    def test_argv_forbidden_flags_absent(self):
        executor = ISO.ContainerExecutor()
        argv = executor.build_argv()
        for flag in executor.forbidden_flags():
            assert flag not in argv, f"禁止参数 {flag} 出现在容器命令行"

    def test_no_host_home_or_credential_mounts(self):
        executor = ISO.ContainerExecutor()
        argv = executor.build_argv()
        mounts = [a for a in argv if a.startswith("type=bind")]
        assert len(mounts) == 1, "只允许一个挂载：只读源码树"
        joined = " ".join(mounts)
        for needle in (".ssh", ".aws", ".docker", "id_rsa", "credentials"):
            assert needle not in joined
        assert "readonly" in mounts[0]

    def test_empty_env_is_explicitly_passed(self):
        executor = ISO.ContainerExecutor()
        argv = executor.build_argv()
        for pair in ("HOME=", "USERPROFILE=", "SSH_AUTH_SOCK=", "ALL_PROXY="):
            index = argv.index(pair)
            assert argv[index - 1] == "-e"
        assert executor.outside_root == ISO.CONTAINER_OUTSIDE_ROOT

    def test_network_whitelist_mode_uses_bridge_and_says_it_is_not_kernel(self):
        executor = ISO.ContainerExecutor(network=ISO.NETWORK_WHITELIST,
                                         network_allow=["api.internal:443"])
        argv = executor.build_argv()
        assert "bridge" in argv and "none" not in argv
        gaps = "；".join(ISO.isolation_boundaries("container").not_guaranteed)
        assert "whitelist" in gaps, "白名单是应用层策略 ⇒ 必须写进不保证清单"

    def test_invalid_network_mode_falls_back_to_none(self):
        executor = ISO.ContainerExecutor(network="chaos-mesh")
        assert executor.network == ISO.NETWORK_NONE
        assert "none" in executor.build_argv()

    def test_custom_image_and_user_are_honoured(self):
        executor = ISO.ContainerExecutor(image="registry.internal/py:3.12",
                                        user="10001:10001")
        argv = executor.build_argv()
        assert "registry.internal/py:3.12" in argv
        assert "10001:10001" in argv
        assert "python:3.12-slim" not in argv


@DOCKER_REQUIRED
class TestContainerExecutorIntegration:
    """需要真实 Docker 的用例（无 Docker ⇒ **skip 并如实标注**，不得伪通过）"""

    def test_container_runs_as_non_root_with_cleared_env(self):
        executor = ISO.ContainerExecutor()
        result = executor.run({"job_id": "ctr-env",
                               "steps": [{"op": "env_dump", "params": {}}]})
        assert result.ran and result.ok, f"{result.status}: {result.error}"
        assert result.platform.get("uid") == 65534, "必须非 root 运行"
        assert result.platform.get("py_platform", "linux") in ("linux", None)
        assert result.platform["sys_platform"] == "linux"
        values = result.outputs[0]["values"]
        assert values["HOME"] == "" and values["SSH_AUTH_SOCK"] == ""
        # 平台原始 HOME 由 Docker/runc 覆写（`-e HOME=` 挡不住，实测）⇒ 单独留档
        assert result.env_raw.get("HOME") == "/nonexistent"

    def test_container_cannot_write_source_or_egress(self):
        executor = ISO.ContainerExecutor()
        result = executor.run({
            "job_id": "ctr-boundaries", "probe_mode": True,
            "steps": [
                {"op": "write_file", "params": {"path": "${work_root}/ok.txt",
                                                "content": "ok"}},
                {"op": "escape_write",
                 "params": {"path": "${source_root}/iso-ctr-must-not-land.txt",
                            "content": "x"}},
                {"op": "escape_write",
                 "params": {"path": f"{ISO.CONTAINER_OUTSIDE_ROOT}/x.txt",
                            "content": "x"}},
                {"op": "net_probe_raw", "params": {"host": "1.1.1.1", "port": 53}},
            ]})
        assert result.ok, f"{result.status}: {result.error}"
        assert result.outputs[0]["ok"] is True, "tmpfs 临时目录必须可写"
        assert result.outputs[1]["escaped"] is False, "源码只读挂载必须挡住写入"
        assert result.outputs[2]["escaped"] is False, "根只读必须挡住越界写"
        assert result.outputs[3]["reachable"] is False, "network=none 必须挡住直连"
        assert not os.path.exists(os.path.join(
            ISO.REPO_ROOT, "iso-ctr-must-not-land.txt"))

    def test_container_memory_limit_kills_over_allocation(self):
        executor = ISO.ContainerExecutor()
        executor.quota.memory_mb = 64
        result = executor.run({"job_id": "ctr-mem", "timeout_s": 30.0,
                               "steps": [{"op": "mem_alloc", "params": {"mb": 512}}]})
        assert result.ran
        assert result.status in (ISO.STATUS_KILLED, ISO.STATUS_TIMEOUT) \
            or result.quota_exceeded, f"64MB 配额下分配 512MB 必须被终止：{result.status}"

    def test_container_pids_limit_blocks_fork_bomb(self):
        executor = ISO.ContainerExecutor()
        executor.quota.pids_limit = 16
        result = executor.run({"job_id": "ctr-pids", "timeout_s": 20.0,
                               "steps": [{"op": "fork_procs",
                                          "params": {"count": 48, "hold_s": 5.0}}]})
        assert result.ok, f"{result.status}: {result.error}"
        assert result.outputs[0]["blocked"] is True, \
            "pids-limit 生效时必须拒绝继续 spawn"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([os.path.abspath(__file__), "-q"]))
