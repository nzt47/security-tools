"""分身侧 CLI 对端与 container 挂载点守卫（scripts/subagent_peer.py + container_backend）

【为什么有这份守卫（不这样会怎样）】
    container 后端的"真跑"依赖两件缺一不可的事：
      ① 镜像里有可执行对端 —— scripts/subagent_peer.py，且必须与 §3.10 协议
         （channel.CLI_ARGV_TAIL / DEFAULT_*）、bundle.BUNDLE_PROTOCOL、
         delegation.EIGHT_ELEMENTS 逐字一致。对端是瘦镜像里的单文件、不能 import
         仓库（否则 python:3.12-slim 直接 ImportError），所以协议常量只能"重述"；
         这份守卫就是那次重述的对拍闸门。
      ② task_file 必须真的能被容器读到 —— **实测** task_mount 与 task_dir 同目标时
         Docker 用 tmpfs 盖住只读 bind，容器里 `cat /work/task.json` 直接
         "No such file or directory"。CI 不真跑 docker 时若不钉住 argv，这条
         "真跑必红"的缺陷会静默回归。

P1 协议对拍：PROTOCOL_TAIL / DEFAULT_* / PROTOCOL / EIGHT_ELEMENTS 与唯一权威逐字相等；
P2 真进程往返：subprocess 跑对端 ⇒ resolve_channel_output 得 tier=jsonl；
P3 零仓库导入：源码 AST 无 agent 导入 + 换 CWD/空 PYTHONPATH 仍能跑；
P4 反幻觉：默认 status=offline_receipt / inference=none，不冒充 done；
P5 handler：成功走 handler；失败 ⇒ stdout 空 + 退出码 4（不产出假成功）；
P6 错误码：非 json 输出格式 ⇒ 2；task_file 非对象/缺失 ⇒ 3；
C8 挂载点：bind 目标 != tmpfs 目标；task_file 映射到 task_mount；同目标 ⇒ ChannelError；
E2E docker 真跑（默认跳过）：CP_SUBAGENT_PEER_IMAGE 指到已构建镜像才跑。
E2E docker 离线真推理（默认跳过）：CP_SUBAGENT_OFFLINE_E2E=1 + 带模型镜像 + 1GiB 配额；
entrypoint 日志路径守卫（容器档 --read-only，/tmp 不可写）。

不 import app_server。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.subagent import bundle as bundle_mod
from agent.subagent import delegation as delegation_mod
from agent.subagent.channel import (CLI_ARGV_TAIL, DEFAULT_MAX_TURNS,
                                    DEFAULT_OUTPUT_FORMAT, TIER_JSONL,
                                    ChannelError, ChannelInvocation,
                                    SubprocessChannelExecutor, build_cli_argv,
                                    parse_json_lines, resolve_channel_output)
from agent.subagent.container_backend import (ContainerChannelExecutor,
                                              ContainerSpec)

_ROOT = Path(__file__).resolve().parents[2]
_PEER = _ROOT / "scripts" / "subagent_peer.py"
_IMAGE = os.environ.get("CP_SUBAGENT_PEER_IMAGE", "")
_OFFLINE_E2E = os.environ.get("CP_SUBAGENT_OFFLINE_E2E", "").strip() == "1"
_OFFLINE_IMAGE = os.environ.get("CP_SUBAGENT_OFFLINE_IMAGE",
                                "yunshu-subagent-peer-local:1")


def _load_peer():
    spec = importlib.util.spec_from_file_location("subagent_peer", _PEER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


peer = _load_peer()


def _task_file(tmp_path, **overrides):
    payload = {
        "schema_version": 1, "task_id": "task-1", "delegation_id": "dlg-1",
        "goal": "演示目标", "constraints": ["只读"], "prior_artifacts": [],
        "prohibitions": ["禁网"], "artifact_format": "json",
        "budget_tokens": 100, "timeout_seconds": 30, "callback_url": "http://x",
    }
    payload.update(overrides)
    path = tmp_path / "task_file_1.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_peer(task_file, *extra, env=None, cwd=None):
    argv = [sys.executable, str(_PEER), "-p", str(task_file)]
    argv.extend(extra)
    return subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env,
                          cwd=cwd, timeout=60)


class TestP1ProtocolParity:
    def test_协议尾巴与唯一权威一致(self):
        assert peer.PROTOCOL_TAIL == CLI_ARGV_TAIL

    def test_默认值与唯一权威一致(self):
        assert peer.DEFAULT_OUTPUT_FORMAT == DEFAULT_OUTPUT_FORMAT
        assert peer.DEFAULT_MAX_TURNS == DEFAULT_MAX_TURNS

    def test_协议名与八要素与唯一权威一致(self):
        assert peer.PROTOCOL == bundle_mod.BUNDLE_PROTOCOL
        assert list(peer.EIGHT_ELEMENTS) == list(delegation_mod.EIGHT_ELEMENTS)

    def test_真实_build_cli_argv_尾巴可被对端解析(self, tmp_path):
        tf = _task_file(tmp_path)
        argv = build_cli_argv("python /peer/subagent_peer.py", str(tf), max_turns=7)
        parsed = peer.build_parser().parse_args(list(argv[2:]))
        assert parsed.task_file == str(tf)
        assert parsed.output_format == DEFAULT_OUTPUT_FORMAT
        assert parsed.max_turns == 7


class TestP2RealProcessRoundTrip:
    def test_对端输出被母体解析为_jsonl(self, tmp_path):
        tf = _task_file(tmp_path)
        cli = (chr(34) + sys.executable + chr(34) + " "
               + chr(34) + str(_PEER) + chr(34))
        invocation = ChannelInvocation(
            argv=build_cli_argv(cli, str(tf), max_turns=3),
            task_file=str(tf), max_turns=3, timeout_seconds=60.0)
        out = resolve_channel_output(
            lambda: SubprocessChannelExecutor()(invocation), invocation=invocation)
        assert out.ok is True, out.error
        assert out.tier == TIER_JSONL
        assert out.payload["status"] == peer.RECEIPT_STATUS
        assert out.payload["task_id"] == "task-1"


class TestP3ZeroRepoImport:
    def test_源码不导入仓库(self):
        tree = ast.parse(_PEER.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "agent" not in imported, "对端必须是瘦镜像可跑的单文件，不得导入仓库"

    def test_换cwd空PYTHONPATH仍可跑(self, tmp_path):
        tf = _task_file(tmp_path)
        env = dict(os.environ)
        env["PYTHONPATH"] = ""
        proc = _run_peer(tf, "--output-format", "json", env=env, cwd=str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert json.loads(proc.stdout.splitlines()[0])["status"] == peer.RECEIPT_STATUS


class TestP4HonestReceipt:
    def test_默认回执不冒充done(self, tmp_path):
        tf = _task_file(tmp_path)
        proc = _run_peer(tf, "--output-format", "json", "--max-turns", "5")
        assert proc.returncode == 0, proc.stderr
        records = parse_json_lines(proc.stdout)
        assert len(records) == 1
        receipt = records[0]
        assert receipt["status"] == peer.RECEIPT_STATUS
        assert receipt["status"] != "done", "默认态不得冒充已完成"
        assert receipt["inference"] == "none"
        assert receipt["network_required"] is False
        assert receipt["task_id"] == "task-1"
        assert receipt["_max_turns"] == 5
        assert "prior_artifacts" in receipt["elements_missing"]


class TestP5Handler:
    def test_handler成功输出handler结果(self, tmp_path, capsys, monkeypatch):
        tf = _task_file(tmp_path)
        monkeypatch.setattr(
            peer, "load_handler",
            lambda spec: (lambda task_file, *, max_turns, output_format:
                          {"status": "done", "turns": max_turns}))
        rc = peer.main(["-p", str(tf), "--handler", "x:y", "--max-turns", "6"])
        assert rc == 0
        assert json.loads(capsys.readouterr().out.strip()) == {"status": "done",
                                                               "turns": 6}

    def test_handler失败_stdout空_退出4(self, tmp_path, capsys):
        tf = _task_file(tmp_path)
        rc = peer.main(["-p", str(tf), "--handler", "no_such_module_xyz:run"])
        assert rc == 4
        assert capsys.readouterr().out == ""


class TestP6ErrorCodes:
    def test_非json输出格式_退出2(self, tmp_path, capsys):
        tf = _task_file(tmp_path)
        assert peer.main(["-p", str(tf), "--output-format", "text"]) == 2
        capsys.readouterr()

    def test_task_file非对象_退出3(self, tmp_path, capsys):
        path = tmp_path / "list.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert peer.main(["-p", str(path)]) == 3
        capsys.readouterr()

    def test_task_file缺失_退出3(self, tmp_path, capsys):
        assert peer.main(["-p", str(tmp_path / "nope.json")]) == 3
        capsys.readouterr()


class TestC8MountSeparation:
    def test_默认挂载点与工作目录分离(self):
        spec = ContainerSpec(image="img")
        assert spec.task_mount != spec.task_dir

    def test_bind目标不与tmpfs同目标且映射到task_mount(self, tmp_path):
        tf = _task_file(tmp_path)
        ex = ContainerChannelExecutor(ContainerSpec(image="img"))
        invocation = ChannelInvocation(
            argv=build_cli_argv("peer", str(tf), max_turns=1), task_file=str(tf),
            max_turns=1, timeout_seconds=10.0)
        argv = ex.build_argv(invocation)
        tmpfs_targets = {argv[i + 1].split(":")[0]
                         for i, token in enumerate(argv) if token == "--tmpfs"}
        mount_specs = [argv[i + 1] for i, token in enumerate(argv)
                       if token == "--mount"]
        assert tmpfs_targets and mount_specs
        bind_targets = {spec.split("target=")[-1].split(",")[0]
                        for spec in mount_specs}
        assert not (bind_targets & tmpfs_targets), "bind 与 tmpfs 同目标会被盖住"
        assert "target=/task" in mount_specs[0]
        assert "/task/" + tf.name in argv, "task_file 必须映射到只读挂载点"

    def test_同目标_fail_closed(self, tmp_path):
        tf = _task_file(tmp_path)
        ex = ContainerChannelExecutor(ContainerSpec(image="img", task_mount="/work"))
        invocation = ChannelInvocation(
            argv=build_cli_argv("peer", str(tf), max_turns=1), task_file=str(tf),
            max_turns=1, timeout_seconds=10.0)
        with pytest.raises(ChannelError):
            ex.build_argv(invocation)


@pytest.mark.skipif(not _IMAGE, reason="CP_SUBAGENT_PEER_IMAGE 未设置（需先构建对端镜像）")
class TestE2EDocker:
    def test_容器真跑_协议往返(self, tmp_path):
        tf = _task_file(tmp_path)
        ex = ContainerChannelExecutor(
            ContainerSpec(image=_IMAGE),
            prober=lambda: SimpleNamespace(available=True, reasons=[]))
        invocation = ChannelInvocation(
            argv=build_cli_argv("python /peer/subagent_peer.py", str(tf), max_turns=3),
            task_file=str(tf), max_turns=3, timeout_seconds=120.0)
        out = resolve_channel_output(lambda: ex(invocation), invocation=invocation)
        assert out.ok is True, out.error
        assert out.tier == TIER_JSONL
        assert out.payload["status"] == peer.RECEIPT_STATUS
        assert out.payload["task_id"] == "task-1"


class TestEntrypointLocalLogPath:
    def test_本地对端日志不写只读_tmp(self):
        src = (_ROOT / "docker" / "subagent-peer"
               / "entrypoint.local.sh").read_text(encoding="utf-8")
        assert ">/tmp/" not in src, (
            "container 后端以 --read-only 运行、只有 tmpfs /work 可写：日志重定向到 /tmp "
            "会让 ollama serve 起不来，镜内全离线真推理直接失败")
        assert "${HOME" in src, "日志应落在可写的 HOME（镜像里 = /work）"


@pytest.mark.skipif(not _OFFLINE_E2E,
                    reason="CP_SUBAGENT_OFFLINE_E2E=1 且已构建带模型镜像才跑")
class TestOfflineLocalInferenceE2E:
    """镜内**全离线**真推理（镜像自带 Ollama+模型；--network none 真跑）。

    与 test_peer_local_handler_ollama_e2e.py 不同：那份跑宿主机 Ollama（容器里只有 handler），
    这份跑容器自带模型——不 build 镜像就跳过，不红、不假绿。
    0.5B 模型在容器档默认 256MiB cgroup 下会被 OOM kill（实测 llama-server signal: killed），
    故显式抬到 1GiB；这条同时钉住 run_prefix 必须走 IsolationQuota.from_env()。
    """

    def test_镜内真推理产出_llm_used(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_DIGESTION_ISOLATION_MEMORY_MB", "1024")
        task = tmp_path / "task.json"
        task.write_text(json.dumps({
            "task_id": "offline-e2e", "delegation_id": "offline-e2e",
            "goal": "只输出一行合法 JSON：{\"status\":\"done\",\"summary\":\"镜内就绪\"}。"
                    "禁止 markdown 围栏，禁止任何解释。",
            "constraints": ["只输出 JSON"], "artifact_format": "json",
        }, ensure_ascii=False), encoding="utf-8")
        ex = ContainerChannelExecutor(
            ContainerSpec(image=_OFFLINE_IMAGE),
            prober=lambda: SimpleNamespace(available=True, reasons=[]))
        invocation = ChannelInvocation(
            argv=build_cli_argv(
                "python3 /peer/subagent_peer.py "
                "--handler agent.subagent.peer_local_handler:run",
                str(task), max_turns=3),
            task_file=str(task), max_turns=3, timeout_seconds=180.0)
        out = resolve_channel_output(lambda: ex(invocation), invocation=invocation)
        assert out.ok is True, out.error or "容器无输出"
        assert out.tier == TIER_JSONL
        meta = out.payload.get("channel_meta") or {}
        assert meta.get("llm_used") is True, "必须真用本地模型，不得是离线回执"
        assert meta.get("model")
        assert str(out.payload.get("summary") or out.payload.get("status") or "").strip()
