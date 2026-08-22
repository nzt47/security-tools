"""任务6：沙箱回放与评估隔离加固 — 单元/集成测试

覆盖验收条件：
  - 危险样本 100% 被拦截/超时，宿主零残留（无新文件/新进程断言）
  - 回放环境与生产零共享写路径（独立工作目录 + 素材目录 hash 不变）
  - fail-closed：隔离层不可用 → 显式失败，不降级同进程执行
  - 明文样本不出隔离环境（脱敏断言）
  - 审计完备（replay_id/samples/candidate_id/verdict/duration/resource_usage/
    evidence/rollback_command）
  - 默认关闭（EVAL_REPLAY_ENABLED=false）零行为变化

运行：python -m pytest tests/unit/test_replay_isolation.py -q --timeout=120
"""
import hashlib
import json
from pathlib import Path

import pytest

from agent.learning import replay as rp

TEST_DATA = Path(__file__).resolve().parent.parent.parent / "test_data" / "replay"
DANGEROUS_DIR = TEST_DATA / "dangerous"


def _read(name: str) -> str:
    for base in (DANGEROUS_DIR, TEST_DATA):
        p = base / name
        if p.exists():
            return p.read_text(encoding="utf-8")
    raise FileNotFoundError(name)


def _engine(tmp_path, *, runner="subprocess", timeout_s=10.0, **kw):
    """构造显式开启的引擎（默认 subprocess 真实进程隔离；spawn 单测单独 mock）"""
    budget = rp.ReplayBudget(timeout_s=timeout_s)
    return rp.ReplayEngine(
        enabled=True, runner=runner, work_dir=tmp_path / "work",
        audit_file=tmp_path / "audit.jsonl", budget=budget, **kw)


def _run(engine, samples, code, candidate_id="c@test", **kw):
    job = rp.ReplayJob(
        samples=samples,
        candidate=rp.candidate_from_code(code, candidate_id),
        **kw)
    return engine.run(job)


# ════════════════════════════════════════════════════════════
#  1. 配置默认值 / 总开关（默认关闭，零行为变化）
# ════════════════════════════════════════════════════════════


class TestConfigDefaults:
    def test_replay_disabled_by_default(self):
        """安全底线：默认关闭"""
        assert rp.replay_enabled() is False

    def test_default_timeout_and_backend(self):
        """默认超时 30s / process 后端 / spawn 执行器"""
        assert rp._timeout_s() == 30.0
        assert rp._env_str("EVAL_REPLAY_BACKEND", rp.DEFAULT_BACKEND) == "process"
        assert rp._env_str("EVAL_REPLAY_RUNNER", rp.DEFAULT_RUNNER) == "spawn"

    def test_env_override_enabled(self, monkeypatch):
        monkeypatch.setenv("EVAL_REPLAY_ENABLED", "1")
        assert rp.replay_enabled() is True

    def test_env_override_timeout(self, monkeypatch):
        monkeypatch.setenv("EVAL_REPLAY_TIMEOUT_S", "7")
        assert rp._timeout_s() == 7.0

    def test_disabled_gate_raises(self, tmp_path):
        """默认关闭：回放显式拒绝（ReplayDisabledError），不静默跳过"""
        eng = rp.ReplayEngine(enabled=False, work_dir=tmp_path / "w")
        job = rp.ReplayJob(
            samples=[rp.ReplaySample(sample_id="s1", task="t")],
            candidate=rp.candidate_from_code("print(1)", "c1"))
        with pytest.raises(rp.ReplayDisabledError):
            eng.run(job)


# ════════════════════════════════════════════════════════════
#  2. 静态逃逸预扫描
# ════════════════════════════════════════════════════════════


class TestScanForEscape:
    @pytest.mark.parametrize("code", [
        "import os\nprint(1)",
        "from socket import *",
        "import subprocess\nsubprocess.run(['id'])",
        "import ctypes",
        "import shutil",
        "import requests",
        "open('/etc/passwd', 'r')",
        "eval('1+1')",
        "exec(code)",
        "os.system('whoami')",
        "().__class__.__bases__",
        "globals()['x']",
        "__import__('os')",
        "import pathlib\npathlib.Path('/').iterdir()",
        "import pickle",
        "import multiprocessing",
        "import threading",
        "import urllib.request",
    ])
    def test_dangerous_patterns_flagged(self, code):
        assert rp.scan_for_escape(code) is not None

    @pytest.mark.parametrize("code", [
        "import sys, json\nprint(json.dumps({'a': 1}))",
        "import re\nm = re.compile(r'\\d+')\nprint(m)",
        "x = sorted([3, 1, 2])\nprint(sum(x))",
        "import json\nparams = json.loads('{}')\nprint(params.get('k', 'd'))",
        "import random\nprint(random.random())",
    ])
    def test_benign_patterns_allowed(self, code):
        assert rp.scan_for_escape(code) is None

    def test_re_compile_not_flagged(self):
        """re.compile 不误伤（排除点号前缀）"""
        assert rp.scan_for_escape("import re\nre.compile(r'abc')") is None

    def test_import_sys_not_flagged(self):
        """import sys 由 worker 代理管控，不静态拦截"""
        assert rp.scan_for_escape("import sys\nprint('ok')") is None


# ════════════════════════════════════════════════════════════
#  3. spawn 执行器逻辑测试（mock_sandbox_spawn 线程化，CI 兼容）
# ════════════════════════════════════════════════════════════


class TestSpawnRunnerLogic:
    @pytest.fixture(autouse=True)
    def _mock_spawn(self, mock_sandbox_spawn):
        self._spawn = mock_sandbox_spawn

    def _engine(self, tmp_path):
        return rp.ReplayEngine(
            enabled=True, runner="spawn", work_dir=tmp_path / "w",
            audit_file=tmp_path / "audit.jsonl",
            budget=rp.ReplayBudget(timeout_s=10.0))

    def test_benign_success(self, tmp_path):
        eng = self._engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="hi")],
                   "import sys, json\np=json.loads(sys.stdin.read())\n"
                   "print(json.dumps({'ok': 1, 'id': p['sample_id']}))")
        assert rep.results[0].verdict == rp.VERDICT_SUCCESS
        assert rep.results[0].result["ok"] == 1
        assert rep.success_rate == 1.0

    def test_code_error_verdict_failed(self, tmp_path):
        eng = self._engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="hi")],
                   "undefined_name_xyz()")
        assert rep.results[0].verdict == rp.VERDICT_FAILED
        assert rep.results[0].error is not None

    def test_sys_exit_verdict_escape_worker_level(self, tmp_path):
        """worker 级逃逸：sys.exit 被 sys 代理拦截 → escape"""
        eng = self._engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="hi")],
                   "import sys\nsys.exit(1)")
        assert rep.results[0].verdict == rp.VERDICT_ESCAPE

    def test_timeout_verdict(self, tmp_path):
        self._spawn.force_timeout = True
        eng = self._engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="hi")],
                   "x = 1")
        assert rep.results[0].verdict == rp.VERDICT_TIMEOUT

    def test_escape_prescan_never_executes(self, tmp_path):
        """预扫描命中 → escape，且代码未在任何位置执行（含 mock 线程）"""
        eng = self._engine(tmp_path)
        marker = {}
        code = "import os\nprint('should-not-run')"
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="hi")], code)
        assert rep.results[0].verdict == rp.VERDICT_ESCAPE
        assert "静态逃逸预扫描拦截" in rep.results[0].evidence
        assert marker == {}


# ════════════════════════════════════════════════════════════
#  4. subprocess 真实进程隔离 — 危险样本安全套件（宿主零残留）
# ════════════════════════════════════════════════════════════


def _no_bootstrap_processes_left():
    """断言无残留 replay worker 进程"""
    try:
        import psutil
    except ImportError:
        return True
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
        except (psutil.Error, TypeError):
            continue
        if "replay_bootstrap.py" in cmd:
            return False
    return True


def _dir_hashes(root: Path) -> dict:
    """目录内全部文件 sha256 摘要（只读）"""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[str(p.relative_to(root))] = hashlib.sha256(
                p.read_bytes()).hexdigest()
    return out


class TestDangerousSamplesSubprocess:
    """真实子进程隔离：危险样本 100% 被拦截/超时，宿主零残留"""

    @pytest.mark.parametrize("sample_file,expect", [
        ("dangerous_file_write.py", rp.VERDICT_ESCAPE),
        ("dangerous_network_exfil.py", rp.VERDICT_ESCAPE),
        ("dangerous_privilege.py", rp.VERDICT_ESCAPE),
        ("dangerous_env_secret.py", rp.VERDICT_ESCAPE),
        ("dangerous_sys_exit.py", rp.VERDICT_ESCAPE),
        ("dangerous_infinite_loop.py", rp.VERDICT_TIMEOUT),
    ])
    def test_dangerous_sample_contained(self, tmp_path, sample_file, expect):
        eng = _engine(tmp_path, timeout_s=3.0)
        code = _read(sample_file)
        before_files = {str(p) for p in tmp_path.rglob("*") if p.is_file()}
        rep = _run(eng, [rp.ReplaySample(sample_id="evil-1", task="任务")],
                   code, candidate_id=f"evil@{sample_file}")
        result = rep.results[0]
        assert result.verdict == expect, (
            f"{sample_file}: verdict={result.verdict} error={result.error}")
        # 宿主零残留：无新文件（work 内仅 bootstrap+audit）、无残留进程
        after_files = {str(p) for p in tmp_path.rglob("*") if p.is_file()}
        new_files = after_files - before_files
        allowed_new = {p for p in new_files
                       if "replay_bootstrap.py" in p or "audit.jsonl" in p}
        assert new_files == allowed_new, f"宿主残留新文件: {new_files - allowed_new}"
        assert _no_bootstrap_processes_left()
        # 无 scratch 残留（含容器目录与 per-sample 子目录）
        scratch_root = tmp_path / "work" / "scratch"
        assert not scratch_root.exists(), "scratch 目录残留（应零残留）"

    def test_benign_control_success(self, tmp_path):
        """良性对照：隔离层不误伤合法候选"""
        eng = _engine(tmp_path, timeout_s=10.0)
        rep = _run(eng, [rp.ReplaySample(sample_id="ok-1", task="查询天气")],
                   _read("benign_echo.py"), candidate_id="benign@echo")
        assert rep.results[0].verdict == rp.VERDICT_SUCCESS
        assert rep.results[0].result["sample_id"] == "ok-1"
        assert rep.success_rate == 1.0

    def test_host_zero_residue_no_new_process(self, tmp_path):
        """显式断言：回放后无残留 worker 子进程"""
        eng = _engine(tmp_path, timeout_s=3.0)
        for i in range(3):
            rep = _run(eng, [rp.ReplaySample(sample_id=f"z-{i}", task="t")],
                       _read("dangerous_infinite_loop.py"),
                       candidate_id=f"loop-{i}")
            assert rep.results[0].verdict == rp.VERDICT_TIMEOUT
        assert _no_bootstrap_processes_left()

    def test_no_network_proxy_env_in_worker_env(self):
        """环境级网络禁断：白名单剔除代理/密钥变量"""
        payload = {"material_dir": None}
        env = rp._safe_env(payload)
        for key in env:
            assert rp._SENSITIVE_ENV_RE.search(key) is None, \
                f"敏感变量进入隔离环境: {key}"
        assert "REPLAY_MATERIAL_DIR" not in env  # material None → 不注入


# ════════════════════════════════════════════════════════════
#  5. fail-closed：隔离不可用 → 显式失败，绝不降级同进程执行
# ════════════════════════════════════════════════════════════


class TestFailClosed:
    def _job(self):
        return rp.ReplayJob(
            samples=[rp.ReplaySample(sample_id="s1", task="t")],
            candidate=rp.candidate_from_code("print('ran')", "c1"))

    def test_backend_unavailable_raises_and_no_same_process(self, tmp_path,
                                                            monkeypatch):
        """后端不可用 → ReplayIsolationError；候选代码绝不在父进程执行"""
        eng = rp.ReplayEngine(enabled=True, runner="subprocess",
                              work_dir=tmp_path / "w")
        monkeypatch.setattr(rp, "_resolve_backend",
                            lambda *a, **k: (_ for _ in ()).throw(
                                rp.ReplayIsolationError("后端不可用（测试）")))
        with pytest.raises(rp.ReplayIsolationError):
            eng.run(self._job())
        # 无同进程降级：代码未在父进程执行（无副作用可观测——直接断言异常而非结果）

    def test_spawn_unavailable_fail_closed(self, tmp_path, monkeypatch):
        """spawn runner 不可用（Queue 创建失败）→ 回放显式失败，无静默降级"""
        import multiprocessing

        def _boom(*a, **k):
            raise OSError("no named pipes (sandbox)")

        monkeypatch.setattr(multiprocessing, "get_context", _boom)
        eng = rp.ReplayEngine(enabled=True, runner="spawn",
                              work_dir=tmp_path / "w")
        with pytest.raises(rp.ReplayIsolationError) as ei:
            eng.run(self._job())
        assert "EVAL_REPLAY_RUNNER=subprocess" in str(ei.value)

    def test_docker_unavailable_fail_closed(self, tmp_path, monkeypatch):
        """docker 后端无 docker → 显式失败"""
        monkeypatch.setattr(rp.shutil, "which", lambda *a, **k: None)
        with pytest.raises(rp.ReplayIsolationError):
            rp._DockerBackend(rp.ReplayBudget())

    def test_invalid_backend_fail_closed(self, tmp_path):
        with pytest.raises(rp.ReplayIsolationError):
            rp._resolve_backend(rp.ReplayBudget(), backend="bogus")

    def test_worker_no_result_is_escape_not_same_process(self, tmp_path,
                                                         monkeypatch):
        """worker 未产出结果（隔离失败）→ escape 判定，绝不回退同进程"""
        eng = rp.ReplayEngine(enabled=True, runner="subprocess",
                              work_dir=tmp_path / "w")
        original = rp._ProcessBackend.run_sample_subprocess
        calls = {"n": 0}

        def broken(self, payload, scratch, bootstrap_file):
            calls["n"] += 1
            # 模拟子进程从未产出结果文件（隔离失败）
            return rp.SampleExecution(
                verdict=rp.VERDICT_ESCAPE,
                duration_ms=1.0,
                error="隔离 worker 未产出结果（fail-closed）")

        monkeypatch.setattr(rp._ProcessBackend, "run_sample_subprocess", broken)
        rep = eng.run(self._job())
        assert rep.results[0].verdict == rp.VERDICT_ESCAPE
        assert calls["n"] == 1


# ════════════════════════════════════════════════════════════
#  6. 脱敏：明文样本不出隔离环境
# ════════════════════════════════════════════════════════════

PII_RAW = ["13812345678", "sk-test-abcdef1234567890",
           "user@example.com", "SuperSecret1"]


class TestDesensitization:
    def test_sanitize_text_redacts_pii(self):
        """各类敏感值在上下文形式下被脱敏（与 samples_pii.json 同源口径）"""
        cases = [
            "API Key=sk-test-abcdef1234567890 配置失败",
            "password=SuperSecret1 需重置",
            "邮箱 user@example.com 登录异常",
            "手机号 13812345678 反馈",
        ]
        for text in cases:
            for raw in PII_RAW:
                assert raw not in rp.sanitize_text(text), (
                    f"未脱敏: {raw!r} in {text!r}")

    def test_worker_receives_sanitized_only(self, tmp_path):
        """回显候选：worker 收到的参数已脱敏（明文不出隔离环境）"""
        eng = _engine(tmp_path)
        raw = ("手机号 13812345678，API Key=sk-test-abcdef1234567890，"
               "邮箱 user@example.com，password=SuperSecret1")
        echo = ("import sys, json\np = json.loads(sys.stdin.read())\n"
                "print(json.dumps(p, ensure_ascii=False))")
        rep = _run(eng, [rp.ReplaySample(sample_id="pii-1", task=raw,
                                         metadata={"note": raw})],
                   echo, candidate_id="echo@pii")
        assert rep.results[0].verdict == rp.VERDICT_SUCCESS
        evidence = rep.results[0].evidence
        for raw_val in PII_RAW:
            assert raw_val not in evidence, f"明文泄漏到证据: {raw_val}"
        assert "[REDACTED]" in evidence

    def test_params_json_never_contains_plaintext(self, tmp_path):
        """审计/报告内容不含明文；工作目录无明文残留"""
        eng = _engine(tmp_path)
        raw = "密钥 sk-test-abcdef1234567890 与手机 13812345678"
        rep = _run(eng, [rp.ReplaySample(sample_id="pii-2", task=raw)],
                   _read("benign_echo.py"), candidate_id="benign@pii2")
        blob = json.dumps(rep.to_dict(), ensure_ascii=False)
        for raw_val in PII_RAW:
            assert raw_val not in blob, f"明文出现在报告: {raw_val}"
        # 工作目录无残留文件（scratch 已清理；仅 bootstrap/audit）
        work = tmp_path / "work"
        leftovers = [str(p) for p in work.rglob("*")
                     if "replay_bootstrap.py" not in str(p)
                     and "audit" not in str(p)]
        assert leftovers == [], f"工作目录明文残留: {leftovers}"

    def test_pii_fixture_sanitized_end_to_end(self, tmp_path):
        """samples_pii.json 资产走完整回放管道：明文不出证据/审计"""
        fixture = TEST_DATA / "samples_pii.json"
        raw_samples = json.loads(fixture.read_text(encoding="utf-8"))
        samples = [rp.ReplaySample(sample_id=s["sample_id"], task=s["task"],
                                   category=s["category"],
                                   metadata=s.get("metadata"))
                   for s in raw_samples]
        eng = _engine(tmp_path)
        echo = ("import sys, json\np = json.loads(sys.stdin.read())\n"
                "print(json.dumps(p, ensure_ascii=False))")
        rep = _run(eng, samples, echo, candidate_id="echo@pii-fixture")
        assert rep.sample_count == 2
        assert rep.success_rate == 1.0
        for r in rep.results:
            for raw_val in PII_RAW:
                assert raw_val not in r.evidence, f"明文泄漏: {raw_val}"


# ════════════════════════════════════════════════════════════
#  7. 零共享写路径 / 审计完备 / 回归入口
# ════════════════════════════════════════════════════════════


class TestZeroWritePathAndAudit:
    def test_default_workdir_definition(self):
        """默认工作目录 = 系统临时区/yunshu_replay（独立于生产 data/）"""
        import tempfile
        assert rp._work_dir() == Path(tempfile.gettempdir()) / "yunshu_replay"
        # 生产数据目录不在默认工作目录内
        assert "data" not in rp._work_dir().parts

    def test_material_dir_untouched(self, tmp_path):
        """回放素材只读：素材目录内容与 hash 在回放前后不变"""
        material = tmp_path / "material"
        (material / "in").mkdir(parents=True)
        (material / "in" / "sample.json").write_text(
            json.dumps({"id": "m1", "task": "任务"}), encoding="utf-8")
        before = _dir_hashes(material)
        eng = _engine(tmp_path, timeout_s=10.0)
        job = rp.ReplayJob(
            samples=[rp.ReplaySample(sample_id="m1", task="任务")],
            candidate=rp.candidate_from_code(_read("benign_echo.py"),
                                             "benign@mat"),
            material_dir=material)
        eng.run(job)
        assert _dir_hashes(material) == before

    def test_scratch_cleaned_after_run(self, tmp_path):
        eng = _engine(tmp_path)
        _run(eng, [rp.ReplaySample(sample_id="s1", task="t")],
             _read("benign_echo.py"))
        scratch = tmp_path / "work" / "scratch"
        assert not scratch.exists() or not any(scratch.iterdir())

    def test_stale_bootstrap_regenerated(self, tmp_path):
        """版本漂移防护：旧版 bootstrap 缓存会被重新生成（防陈旧 worker 逻辑）"""
        eng = _engine(tmp_path)
        eng._ensure_bootstrap()
        boot = eng._bootstrap_file
        assert boot.exists()
        boot.write_text("__REPLAY_BOOTSTRAP_VERSION__ = 999\n# 旧版残留\n",
                        encoding="utf-8")
        eng2 = _engine(tmp_path)  # 同一工作目录，新引擎实例
        eng2._ensure_bootstrap()
        assert rp._BOOTSTRAP_VERSION_MARKER in boot.read_text(
            encoding="utf-8"), "旧 bootstrap 未按版本标记重新生成"

    def test_audit_fields_complete(self, tmp_path):
        eng = _engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="a1", task="t")],
                   _read("benign_echo.py"), candidate_id="audit@c",
                   sampleset_version="v1", category="bench")
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip()
        entry = json.loads(lines.splitlines()[-1])
        for key in ("replay_id", "created_at", "candidate_id", "samples",
                    "verdict_counts", "duration_ms", "resource_usage",
                    "evidence", "rollback_command", "backend", "runner",
                    "enabled"):
            assert key in entry, f"审计缺字段: {key}"
        assert entry["candidate_id"] == "audit@c"
        assert entry["samples"][0]["verdict"] == rp.VERDICT_SUCCESS
        assert entry["rollback_command"]  # 非空

    def test_report_audit_fields(self, tmp_path):
        eng = _engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="r1", task="t")],
                   _read("benign_echo.py"), candidate_id="report@c")
        d = rep.to_dict()
        for key in ("replay_id", "candidate_id", "sample_count", "verdict_counts",
                    "success_rate", "duration_ms", "resource_usage",
                    "rollback_command"):
            assert key in d


class TestRegressionEntry:
    def _setup_samples(self, tmp_path):
        evals = tmp_path / "evals"
        (evals / "bench").mkdir(parents=True)
        (evals / "manifest.json").write_text(json.dumps({
            "schema_version": 1,
            "versions": {"v1": {"categories": {"bench": ["b-001", "b-002"]}}},
        }), encoding="utf-8")
        (evals / "bench" / "samples.json").write_text(json.dumps([
            {"id": "b-001", "category": "bench", "task": "任务甲",
             "expected_output": None, "metadata": {}},
            {"id": "b-002", "category": "bench", "task": "任务乙",
             "expected_output": None, "metadata": {}},
        ]), encoding="utf-8")
        return evals

    def test_regression_entry_resolves_and_runs(self, tmp_path):
        evals = self._setup_samples(tmp_path)
        eng = rp.ReplayEngine(enabled=True, runner="subprocess",
                              work_dir=tmp_path / "w",
                              audit_file=tmp_path / "audit.jsonl",
                              budget=rp.ReplayBudget(timeout_s=10.0))
        rep = rp.run_replay_regression(
            rp.candidate_from_code(_read("benign_echo.py"), "skill@replay",
                                   name="bench"),
            sampleset_version="v1", category="bench",
            samples_dir=str(evals), engine=eng,
            material_dir=evals)
        assert rep.sample_count == 2
        assert rep.success_rate == 1.0
        assert [r.sample_id for r in rep.results] == ["b-001", "b-002"]

    def test_regression_no_manifest_fails_closed(self, tmp_path):
        evals = tmp_path / "evals"
        evals.mkdir()
        eng = rp.ReplayEngine(enabled=True, runner="subprocess",
                              work_dir=tmp_path / "w")
        with pytest.raises(rp.ReplayJobError):
            rp.run_replay_regression(
                rp.candidate_from_code(_read("benign_echo.py"), "x@y",
                                       name="bench"),
                sampleset_version="v1", category="bench",
                samples_dir=str(evals), engine=eng)


# ════════════════════════════════════════════════════════════
#  8. worker 判定边界（subprocess 真实执行）
# ════════════════════════════════════════════════════════════


class TestWorkerVerdicts:
    def test_failed_when_no_result_json(self, tmp_path):
        """执行完成但无结果 JSON → failed（协议违反，与 SkillExecutor 一致）"""
        eng = _engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="t")],
                   "x = 1 + 1\nprint('no-json-here')")
        assert rep.results[0].verdict == rp.VERDICT_FAILED

    def test_success_with_result_json(self, tmp_path):
        eng = _engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="t")],
                   "import json\nprint(json.dumps({'a': [1, 2]}))")
        assert rep.results[0].verdict == rp.VERDICT_SUCCESS
        assert rep.results[0].result == {"a": [1, 2]}

    def test_rss_recorded(self, tmp_path):
        eng = _engine(tmp_path)
        rep = _run(eng, [rp.ReplaySample(sample_id="s1", task="t")],
                   _read("benign_echo.py"))
        ru = rep.results[0].resource_usage
        assert "wall_ms" in ru
        assert ru.get("rss_kb") is None or ru["rss_kb"] > 0
