# -*- coding: utf-8 -*-
"""L23 回归：启动期端口清理必须"先留痕、后 kill"，且可区分自杀式重启与他进程。

被断言的产品代码：agent/server_port_guard.py（app_server.py 启动期调用）。
背景：TASK-03 取证显示 15 个 restart 日志全部 Traceback=0 / MemoryError=0 /
**无 shutdown 行**，日志在正常流量中戛然而止 ⇒ 重启原因不可自证，因为旧实现
对占用 5678 的 PID 直接 taskkill /F 且零日志（整段包在 except Exception: pass）。

本文件用**受控桩**（注入 runner / cmdline_getter / audit）验证留痕与顺序，
**绝不真的 kill 任何进程**。
"""

import logging
import sys

from agent.server_port_guard import (
    DEFAULT_PORT, KIND_FOREIGN, KIND_SELF_APP, KIND_UNKNOWN,
    classify_target, cleanup_port_listeners, parse_listening_pids,
)

NETSTAT_SAMPLE = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:5678         0.0.0.0:0              LISTENING       4321
  TCP    [::]:5678              [::]:0                 LISTENING       4321
  TCP    127.0.0.1:56789        0.0.0.0:0              LISTENING       9999
  TCP    127.0.0.1:5678         127.0.0.1:51000        ESTABLISHED     5555
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       6789
"""


class _Completed:
    """subprocess.run 替身的最小返回体"""

    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _StubRunner:
    """受控桩：记录调用顺序，绝不真的执行任何命令"""

    def __init__(self, netstat_stdout=NETSTAT_SAMPLE):
        self.netstat_stdout = netstat_stdout
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[:2] == ["netstat", "-ano"]:
            return _Completed(self.netstat_stdout)
        return _Completed("")

    @property
    def killed_pids(self):
        return [c[0][-1] for c in self.calls if c[0][:1] == ["taskkill"]]


def test_parse_listening_pids_port_exact_and_dedup():
    """只认本地地址端口精确等于 5678 的 LISTENING 行（56789 不算，去重）"""
    assert parse_listening_pids(NETSTAT_SAMPLE, DEFAULT_PORT) == ["4321"]
    assert parse_listening_pids(NETSTAT_SAMPLE, 56789) == ["9999"]
    assert parse_listening_pids("", DEFAULT_PORT) == []


def test_classify_self_restart_vs_foreign():
    """命令行含 app_server.py ⇒ 自杀式重启；不含 ⇒ 他进程；读不到 ⇒ unknown"""
    assert classify_target("python app_server.py") == KIND_SELF_APP
    assert classify_target("C:/py/python.exe APP_SERVER.PY --port 1") == KIND_SELF_APP
    assert classify_target("python other_service.py") == KIND_FOREIGN
    assert classify_target("") == KIND_UNKNOWN
    assert classify_target(None) == KIND_UNKNOWN


def test_audit_written_before_kill_and_argv_unchanged():
    """留痕在 kill **之前**；kill 命令与旧实现逐字一致"""
    events = []
    runner = _StubRunner()

    def _audit(record):
        events.append(("log", record["target_pid"], record["action"]))

    def _runner(argv, **kwargs):
        if argv[:2] != ["netstat", "-ano"]:
            events.append(("kill", argv[-1], " ".join(argv)))
        return runner(argv, **kwargs)

    records = cleanup_port_listeners(
        5678, runner=_runner, cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, audit=_audit)

    assert [r["target_pid"] for r in records] == ["4321"]
    # 顺序：先 log 后 kill（kill 之前留痕，不可颠倒）
    assert [e[0] for e in events] == ["log", "kill"], events
    # kill 命令未改：仍是 taskkill /F /PID <pid>（win32）/ os.kill SIGTERM
    if sys.platform == "win32":
        assert events[1][2] == "taskkill /F /PID 4321"
    else:
        assert records[0]["kill_cmd"] == "os.kill(4321, SIGTERM)"
    assert records[0]["actor_pid"] == 777


def test_record_fields_cover_who_target_why_when():
    """留痕字段覆盖：谁 / 目标 PID / 为何 / 何时 / 自杀式重启判定"""
    collected = []
    cleanup_port_listeners(
        5678, runner=_StubRunner(),
        cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, audit=collected.append)

    rec = collected[0]
    assert rec["actor"] == "app_server.startup_port_cleanup"   # 谁
    assert rec["actor_pid"] == 777 and isinstance(rec["actor_ppid"], int)
    assert rec["target_pid"] == "4321"                          # 目标 PID
    assert rec["port"] == 5678 and rec["reason"] == "port_in_use"  # 为何
    assert rec["ts"] and rec["ts"][:2] == "20"                  # 何时（ISO）
    assert rec["target_kind"] == KIND_SELF_APP                  # 自杀式重启
    assert rec["is_self_app_restart"] is True
    assert rec["graceful_shutdown_possible"] is False
    assert "taskkill" in rec["kill_cmd"] or "os.kill" in rec["kill_cmd"]
    assert rec["action"] == "startup.port_cleanup.kill_before"


def test_foreign_process_is_distinguishable():
    """他进程：target_kind=foreign_process 且 is_self_app_restart=False"""
    collected = []
    cleanup_port_listeners(5678, runner=_StubRunner(),
                           cmdline_getter=lambda pid: "python legacy_backend.py",
                           self_pid=777, audit=collected.append)
    assert collected[0]["target_kind"] == KIND_FOREIGN
    assert collected[0]["is_self_app_restart"] is False


def test_unknown_cmdline_is_recorded_honestly():
    """命令行读不到 ⇒ unknown（不臆测为自杀式重启，也不臆测为他进程）"""
    collected = []
    cleanup_port_listeners(5678, runner=_StubRunner(),
                           cmdline_getter=lambda pid: "",
                           self_pid=777, audit=collected.append)
    assert collected[0]["target_kind"] == KIND_UNKNOWN
    assert collected[0]["is_self_app_restart"] is False


def test_default_audit_emits_structured_log(caplog):
    """默认留痕走结构化日志（logger.warning + log_dict），字段可直接检索"""
    with caplog.at_level(logging.WARNING, logger="agent.server_port_guard"):
        cleanup_port_listeners(5678, runner=_StubRunner(),
                               cmdline_getter=lambda pid: "python app_server.py",
                               self_pid=777)
    payloads = [r.msg for r in caplog.records if isinstance(r.msg, dict)]
    assert payloads, "默认留痕未写结构化日志"
    hit = [p for p in payloads
           if p.get("action") == "startup.port_cleanup.kill_before"]
    assert hit and hit[0]["target_pid"] == "4321"
    assert hit[0]["module_name"] == "server_port_guard"


def test_no_listener_no_kill():
    """端口没被占用 ⇒ 不 kill、不留痕（零副作用）"""
    runner = _StubRunner(netstat_stdout="  TCP    127.0.0.1:5173   0.0.0.0:0  LISTENING  6789")
    collected = []
    records = cleanup_port_listeners(5678, runner=runner,
                                     cmdline_getter=lambda pid: "",
                                     self_pid=777, audit=collected.append)
    assert records == [] and collected == []
    assert runner.killed_pids == []


def test_netstat_failure_is_swallowed():
    """netstat 起不来 ⇒ 返回空、不抛（与旧实现"不阻断启动"一致）"""
    def _boom(argv, **kwargs):
        raise OSError("netstat missing")
    assert cleanup_port_listeners(5678, runner=_boom, self_pid=777) == []