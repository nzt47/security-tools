"""container 执行后端守卫（agent/subagent/container_backend.py + bundle/config 接线）

C1 词表与映射：container 在后端词表；未知后端仍 fail-closed；
C2 argv 包裹：inner 逐 token 等于 build_cli_argv（task_file 映射到只读 /task，
   与可写 tmpfs /work 分离）；隔离参数齐备；禁止参数缺席；
   task_file 必须恰好 1 次（否则 ChannelError）；
C3 不可用即拒绝：probe 不可用 ⇒ runner 零调用、错误非空；
C4 可用即执行：runner 收到 docker argv，RawOutput 正确映射；
C5 构造期零网络：构造不触发 prober/runner；
C6 镜像缺失 fail-closed：ContainerBackendError / resolve_backend ⇒ BundleError（不回落）；
C7 声明式选路消费者：execution_backend=container ⇒ _resolve_execution_channel 返回容器通道；
   bundle 导入携带容器后端。

不 import app_server；不真跑 docker。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.subagent.bundle import (BACKEND_CONTAINER, SUPPORTED_BACKENDS, BundleError,
                                   UnsupportedBackend, build_bundle, get_backend,
                                   import_config, resolve_backend)
from agent.subagent.channel import ChannelError, ChannelInvocation, build_cli_argv
from agent.subagent.container import SubagentConfig, SubagentContainer
from agent.subagent.container_backend import (ContainerBackendError,
                                              ContainerChannelExecutor, ContainerSpec,
                                              build_container_channel,
                                              container_backend_enabled)


def _invocation(tmp_path, *, max_turns: int = 2) -> ChannelInvocation:
    task = tmp_path / "task.json"
    task.write_text('{"goal": "g", "constraints": ["只读"]}', encoding="utf-8")
    argv = build_cli_argv("my-agent", str(task), max_turns=max_turns)
    return ChannelInvocation(argv=argv, task_file=str(task), max_turns=max_turns,
                             timeout_seconds=30.0)


class _Proc:
    def __init__(self, stdout: str = '{"status":"done"}', returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class TestC1Wordlist:
    def test_container_在词表(self):
        assert BACKEND_CONTAINER == "container"
        assert BACKEND_CONTAINER in SUPPORTED_BACKENDS
        assert get_backend({"runtime": {"backend": "container"}}) == "container"

    def test_未知后端仍_fail_closed(self):
        with pytest.raises(UnsupportedBackend):
            get_backend({"runtime": {"backend": "docker"}})


class TestC2Argv:
    def test_inner_等于协议且隔离参数齐备(self, tmp_path):
        inv = _invocation(tmp_path)
        ex = ContainerChannelExecutor(ContainerSpec(image="yunshu-subagent:1"))
        argv = ex.build_argv(inv)
        assert argv[:3] == ["docker", "run", "--rm"]
        assert "--network" in argv and "none" in argv
        assert "--read-only" in argv
        assert "--cap-drop" in argv and "ALL" in argv
        assert "no-new-privileges" in argv
        assert "yunshu-subagent:1" in argv
        idx = argv.index("yunshu-subagent:1")
        inner = argv[idx + 1:]
        # task_file 只读挂在 /task、可写 tmpfs 在 /work：同目标时 tmpfs 会盖住 bind，
        # 容器读不到 task_file（实测真跑 100% 失败）。回归守卫另见
        # tests/unit/test_subagent_peer_entrypoint.py::TestC8MountSeparation。
        container_task = "/task/" + tmp_path.joinpath("task.json").name
        expected = [container_task if t == str(inv.task_file) else t for t in inv.argv]
        assert inner == expected, "inner 必须逐 token 等于 build_cli_argv（换后端不改协议）"
        for flag in ex.forbidden_flags():
            assert flag not in argv, "禁止参数出现在容器 argv：%s" % flag
        assert argv.count(container_task) == 1

    def test_配额受_cp_digestion_环境变量约束(self, monkeypatch):
        monkeypatch.setenv("CP_DIGESTION_ISOLATION_MEMORY_MB", "1024")
        argv = list(ContainerSpec(image="img").run_prefix())
        assert argv[argv.index("--memory") + 1] == "1024m", (
            "容器内存必须可经 CP_DIGESTION_ISOLATION_MEMORY_MB 配置；"
            "裸 IsolationQuota() 只取类默认值(256MiB)，会让镜内本地推理被 OOM kill")

    def test_task_file_缺失_抛_ChannelError(self, tmp_path):
        ex = ContainerChannelExecutor(ContainerSpec(image="img"))
        bad = ChannelInvocation(argv=("my-agent", "-p", "/nope.json"),
                                task_file=str(tmp_path / "task.json"), max_turns=1,
                                timeout_seconds=10.0)
        with pytest.raises(ChannelError):
            ex.build_argv(bad)


class TestC3C4ProbeAndRun:
    def test_不可用即拒绝_runner_零调用(self, tmp_path):
        calls = []
        ex = ContainerChannelExecutor(
            ContainerSpec(image="img"),
            prober=lambda: SimpleNamespace(available=False, reasons=["daemon 未运行"]),
            runner=lambda *a, **k: calls.append(a) or _Proc())
        out = ex(_invocation(tmp_path))
        assert out.ok is False
        assert "Docker 不可用" in out.error
        assert calls == [], "Docker 不可用却启动了容器"

    def test_可用即执行_映射_RawOutput(self, tmp_path):
        seen = []
        ex = ContainerChannelExecutor(
            ContainerSpec(image="img"),
            prober=lambda: SimpleNamespace(available=True, reasons=[]),
            runner=lambda argv, **k: seen.append(argv) or _Proc(stdout='{"status":"done"}'))
        out = ex(_invocation(tmp_path))
        assert out.returncode == 0 and out.stdout == '{"status":"done"}'
        assert seen and seen[0][0] == "docker"

    def test_runner_显式_utf8_解码(self, tmp_path):
        seen = {}

        def _run(argv, **kwargs):
            seen.update(kwargs)
            return _Proc()

        ex = ContainerChannelExecutor(
            ContainerSpec(image="img"),
            prober=lambda: SimpleNamespace(available=True, reasons=[]),
            runner=_run)
        ex(_invocation(tmp_path))
        assert seen.get("text") is True
        assert seen.get("encoding") == "utf-8", (
            "不显式 utf-8 时 Windows(GBK) 读取容器 UTF-8 输出会在读取线程抛 "
            "UnicodeDecodeError、stdout 变空 ⇒ 假 E_UPSTREAM_FORMAT/empty")
        assert seen.get("errors") == "replace"


class TestC5ConstructionOffline:
    def test_构造不触发_prober_runner(self):
        calls = []

        def _probe():
            calls.append("probe")
            return SimpleNamespace(available=True, reasons=[])

        def _run(*a, **k):
            calls.append("run")
            return _Proc()

        ch = build_container_channel(image="img", prober=_probe, runner=_run)
        assert isinstance(ch, ContainerChannelExecutor)
        assert calls == [], "构造期不得探测/启动"


class TestC6FailClosed:
    def test_镜像缺失_抛(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_CONTAINER_IMAGE", raising=False)
        with pytest.raises(ContainerBackendError):
            build_container_channel()

    def test_resolve_backend_container_无镜像_抛_BundleError_不回落(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_CONTAINER_IMAGE", raising=False)
        with pytest.raises(BundleError):
            resolve_backend({"runtime": {"backend": "container"}})


class TestC7Consumer:
    def test_显式_container_选容器通道(self, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_CONTAINER_IMAGE", "img")
        mc = SubagentContainer(SubagentConfig(name="sa-c", model_id="m",
                                              execution_backend="container"))
        assert isinstance(mc._resolve_execution_channel(), ContainerChannelExecutor)

    def test_缺镜像_选路抛(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_CONTAINER_IMAGE", raising=False)
        mc = SubagentContainer(SubagentConfig(name="sa-c2", model_id="m",
                                              execution_backend="container"))
        with pytest.raises(ContainerBackendError):
            mc._resolve_execution_channel()

    def test_默认不选容器(self, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_CONTAINER_ENABLED", raising=False)
        mc = SubagentContainer(SubagentConfig(name="sa-d", model_id="m"))
        assert mc._resolve_execution_channel() is None

    def test_import_config_携带后端(self):
        cfg = SubagentConfig(name="sa-e", model_id="m")
        # schema v2 起 environment 为必需段（#1062）：这里给最小合法形态，本用例只测后端携带
        bundle = build_bundle(cfg, backend="container",
                              generated_at="2026-10-09T00:00:00+00:00",
                              bundle_id="bnd-c",
                              environment={"source_status": "ok", "items": [],
                                           "offline_ready": False})
        assert bundle["runtime"]["backend"] == "container"
        assert import_config(bundle).execution_backend == "container"

    def test_env_gate_解析(self):
        assert container_backend_enabled({"CP_SUBAGENT_CONTAINER_ENABLED": "1"}) is True
        assert container_backend_enabled({}) is False
