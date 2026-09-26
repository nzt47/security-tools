# -*- coding: utf-8 -*-
"""A1 回归：**新实例启动失败 ⇒ 旧实例未被杀**（零服务窗口不变量）+ 子进程不遗留。

被断言的产品代码：
    agent/server_port_guard.py 的 guarded_startup / install_child_process_reaper /
    cleanup_port_listeners(reap_children=True)，以及 app_server.py 的调用点。

背景（审计主报告 §1.4）：
    改动前 app_server.py 在**启动早期**无条件 taskkill /F 掉 5678 上的旧实例，
    之后才构造引擎并 serve ⇒ 新实例在后续任何一步失败，结果是"旧实例已死、
    新实例没起来" = **完全无服务**，且没有任何人会发现（2026-09-22 那次静默退出后
    直到 09-25 审计才被查出来）。

与 tests/unit/test_server_port_guard.py 的分工：
    那份是 L23 的**逐字回归锁**（kill argv 必须与旧实现逐字相同），本文件是 A1 的
    **时序不变量锁**（什么条件下才允许杀）。两者共用受控桩纪律：默认不真的杀进程。

唯一的例外见本文件末尾的 test_job_object_kills_children_on_parent_force_kill：
它确实会 taskkill /F 一个进程 —— 但那个进程是**本测试自己刚刚 spawn 的**父进程，
只用于验证 Job Object 机制；它绝不触碰任何既有服务或第三方进程。
"""

import json
import os
import subprocess
import sys
import time

import pytest

from agent import server_port_guard as spg
from agent.server_port_guard import (
    EXIT_PREFLIGHT_FAILED,
    EXIT_SERVE_FAILED,
    guarded_startup,
    install_child_process_reaper,
)

#: 5678 上有旧实例在监听（PID 4321）+ 一个无关端口，用来证明"只认端口精确匹配"
NETSTAT_OLD_INSTANCE = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:5678         0.0.0.0:0              LISTENING       4321
  TCP    127.0.0.1:56789        0.0.0.0:0              LISTENING       9999
"""

#: 5678 上没有任何监听（只有无关的 5173 前端）
NETSTAT_PORT_FREE = """
  Proto  Local Address          Foreign Address        State           PID
  TCP    127.0.0.1:5173         0.0.0.0:0              LISTENING       6789
"""


class _Completed:
    """subprocess.run 替身的最小返回体"""

    def __init__(self, stdout=""):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _StubRunner:
    """受控桩：记录每次调用，绝不真的执行任何命令。"""

    def __init__(self, netstat_stdout=NETSTAT_OLD_INSTANCE):
        self.netstat_stdout = netstat_stdout
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        if argv[:2] == ["netstat", "-ano"]:
            return _Completed(self.netstat_stdout)
        return _Completed("")

    @property
    def argv_list(self):
        return [c[0] for c in self.calls]

    @property
    def taskkill_argv(self):
        return [c[0] for c in self.calls if c[0][:1] == ["taskkill"]]


def _make_events_runner(netstat_stdout):
    """返回 (runner, events)：runner 是受控桩，events 按发生顺序记下关键动作。"""
    events = []
    stub = _StubRunner(netstat_stdout)

    def _runner(argv, **kwargs):
        if argv[:2] == ["netstat", "-ano"]:
            events.append(("netstat",))
        else:
            events.append(("kill", " ".join(argv)))
        return stub(argv, **kwargs)

    return _runner, events, stub


def _install_os_kill_probe(monkeypatch, events):
    """非 win32 下 cleanup 的 kill 走 os.kill（不经 runner）⇒ 单独接探针。

    与 tests/unit/test_server_port_guard.py 同一套平台适配纪律：否则 Linux CI 上
    既看不到 kill 事件，还会**真的**对桩 PID 发 SIGTERM。
    """
    if sys.platform != "win32":
        def _fake_kill(pid, sig):
            events.append(("kill", "os.kill(%s)" % pid))
        monkeypatch.setattr(os, "kill", _fake_kill)


def _kills(events):
    return [e for e in events if e[0] == "kill"]


# ════════════════════════════════════════════════════════════════════════════
# 核心不变量：新实例启动失败 ⇒ 旧实例未被杀
# ════════════════════════════════════════════════════════════════════════════

def test_startup_failure_leaves_old_instance_alive(monkeypatch):
    """就绪门判定"未就绪" ⇒ 一个进程都不杀，且不进入服务。"""
    runner, events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    _install_os_kill_probe(monkeypatch, events)
    served = []

    def _preflight():
        events.append(("preflight",))
        return False, "waitress 导入失败"

    result = guarded_startup(lambda: served.append(True), 5678,
                             preflight=_preflight, runner=runner, self_pid=777)

    assert result["served"] is False
    assert result["exit_code"] == EXIT_PREFLIGHT_FAILED
    assert result["targets"] == ["4321"], "应识别出旧实例（且不把 56789 算进来）"
    assert _kills(events) == [], "新实例未就绪时**绝不允许**出现任何 kill：%s" % events
    assert stub.taskkill_argv == [], "受控桩不应收到任何 taskkill 调用"
    assert served == [], "未就绪不得进入 serve"
    assert result["preflight"] == {"ran": True, "ok": False,
                                   "reason": "waitress 导入失败"}


def test_preflight_exception_is_fail_closed(monkeypatch):
    """就绪门自身抛异常 ⇒ 按"未就绪"处理（失败关闭），同样不杀旧实例。"""
    runner, events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    _install_os_kill_probe(monkeypatch, events)
    served = []

    def _boom():
        raise RuntimeError("探针自身炸了")

    result = guarded_startup(lambda: served.append(True), 5678,
                             preflight=_boom, runner=runner, self_pid=777)

    assert result["served"] is False
    assert result["exit_code"] == EXIT_PREFLIGHT_FAILED
    assert _kills(events) == []
    assert stub.taskkill_argv == []
    assert served == []
    assert "preflight_exception" in result["preflight"]["reason"]


def test_gate_abort_is_traced_with_killed_any_false(monkeypatch, caplog):
    """放弃启动必须留痕（否则"为什么没起来"又变成不可自证）。"""
    import logging
    runner, events, _stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    _install_os_kill_probe(monkeypatch, events)
    with caplog.at_level(logging.ERROR, logger="agent.server_port_guard"):
        guarded_startup(lambda: None, 5678,
                        preflight=lambda: (False, "boom"),
                        runner=runner, self_pid=777)
    payloads = [r.msg for r in caplog.records if isinstance(r.msg, dict)]
    hit = [p for p in payloads
           if p.get("action") == "startup.gate.abort_before_cleanup"]
    assert hit, "放弃启动没有写结构化留痕"
    assert hit[0]["killed_any"] is False
    assert hit[0]["old_instance_pids"] == ["4321"]
    assert hit[0]["reason"] == "new_instance_not_ready"


# ════════════════════════════════════════════════════════════════════════════
# 时序：清理只能发生在就绪门**通过之后**
# ════════════════════════════════════════════════════════════════════════════

def test_old_instance_killed_only_after_preflight_passes(monkeypatch):
    """就绪门通过 ⇒ 才清理旧实例；且顺序固定为 preflight → kill → serve。"""
    runner, events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    _install_os_kill_probe(monkeypatch, events)
    served = []

    def _preflight():
        events.append(("preflight",))
        return True, "self_health_probe_status=200"

    def _serve():
        events.append(("serve",))
        served.append(True)

    result = guarded_startup(_serve, 5678,
                             preflight=_preflight, runner=runner, self_pid=777,
                             children_getter=lambda pid: [])

    assert result["served"] is True
    assert result["exit_code"] == 0
    assert [e[0] for e in events] == ["netstat", "preflight", "netstat", "kill", "serve"], \
        "顺序必须是：读端口 → 就绪门 → 清理旧实例 → serve；实际 %s" % events
    if sys.platform == "win32":
        assert stub.taskkill_argv == [["taskkill", "/F", "/PID", "4321"]], \
            "kill argv 必须与旧实现逐字一致（L23 回归锁同款约束）"


def test_port_free_never_runs_preflight_and_serves():
    """端口空闲 ⇒ 没有旧实例可杀 ⇒ 不跑探针、零额外开销，直接起。"""
    runner, events, stub = _make_events_runner(NETSTAT_PORT_FREE)
    served = []
    result = guarded_startup(lambda: served.append(True), 5678,
                             preflight=lambda: (_ for _ in ()).throw(
                                 AssertionError("端口空闲时不应调用探针")),
                             runner=runner, self_pid=777)
    assert result["served"] is True
    assert result["targets"] == []
    assert result["preflight"] == {"ran": False, "ok": True,
                                   "reason": "port_free_no_old_instance"}
    assert stub.taskkill_argv == []
    assert served == [True]


def test_serve_failure_is_reported_not_swallowed():
    """清理之后 serve 失败 ⇒ 如实回传退出码（交给看门狗），不假装成功。"""
    runner, _events, _stub = _make_events_runner(NETSTAT_OLD_INSTANCE)

    def _serve():
        raise OSError("bind failed: 10048")

    result = guarded_startup(_serve, 5678, preflight=lambda: True,
                             runner=runner, self_pid=777,
                             children_getter=lambda pid: [])
    assert result["served"] is False
    assert result["exit_code"] == EXIT_SERVE_FAILED
    assert "10048" in result["serve_error"]


def test_cleanup_exception_does_not_block_startup(monkeypatch, caplog):
    """【独立复核 A1-R1 回归】清理期抛异常**不得**阻断启动（既有契约不得收紧）。

    背景：早期版本的 guarded_startup 直接做 `result[...] = cleanup_port_listeners(...)`，
    没有兜底。实测确实炸过一次 —— records 里混着 kill_before 与 reap_child 两类记录、
    却对全体取 r["target_kind"]，抛 KeyError。异常会在"旧实例已被杀、新实例还没 bind"
    之间穿出去 ⇒ **正好制造本卡要消灭的零服务窗口**。本用例把这条契约钉死。
    """
    import logging
    runner, _events, _stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    served = []

    def _boom(*a, **kw):
        raise KeyError("target_kind")

    monkeypatch.setattr(spg, "cleanup_port_listeners", _boom)
    with caplog.at_level(logging.ERROR, logger="agent.server_port_guard"):
        result = guarded_startup(lambda: served.append(True), 5678,
                                 preflight=lambda: True, runner=runner,
                                 self_pid=777, children_getter=lambda pid: [])

    assert result["served"] is True, "清理抛异常不得阻断启动（既有契约）"
    assert result["exit_code"] == 0
    assert "KeyError" in result["cleanup_error"]
    assert served == [True]
    payloads = [r.msg for r in caplog.records if isinstance(r.msg, dict)]
    hit = [p for p in payloads if p.get("action") == "startup.port_cleanup.failed"]
    assert hit, "清理失败必须留痕（否则又变成不可自证）"
    assert hit[0]["reason"] == "cleanup_raised"
    assert hit[0]["old_instance_pids"] == ["4321"]


def test_cleanup_mixed_records_never_raise_keyerror():
    """【独立复核 A1-R1 回归】混合记录（父进程 + 收割子进程）不得让汇总日志炸掉。

    这是上一条的真实触发源：`cleanup_port_listeners` 的汇总日志对 records 全量取
    r["target_kind"]，而 reap_child 记录没有该键。本用例把因果链直接钉在
    cleanup_port_listeners 上（不走 guarded_startup），并断言两类记录同时存在。
    """
    records = spg.cleanup_port_listeners(
        5678, runner=_StubRunner(), cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, reap_children=True,
        children_getter=lambda pid: ["5001"], child_guard=lambda c, p: True)
    actions = {r["action"] for r in records}
    assert actions == {"startup.port_cleanup.kill_before",
                       "startup.port_cleanup.reap_child"}
    # 关键：混合记录下 kill_before 有 target_kind、reap_child 没有 —— 汇总必须能处理
    kinds = [r.get("target_kind") for r in records
             if r["action"] == "startup.port_cleanup.kill_before"]
    assert kinds == ["self_app_restart"]


def test_no_preflight_configured_keeps_legacy_behaviour():
    """**有意保留**：不传 preflight ⇒ 维持旧行为（有旧实例就清理）。

    app_server.py 的调用点**总是**传 preflight（见该文件末尾 _startup_preflight），
    本用例只是把"默认值不是失败关闭"这条事实固定下来，避免被误当成已修复。
    """
    runner, _events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    result = guarded_startup(lambda: None, 5678, runner=runner, self_pid=777,
                             children_getter=lambda pid: [])
    assert result["served"] is True
    assert result["preflight"] == {"ran": False, "ok": True,
                                   "reason": "no_preflight_configured"}
    if sys.platform == "win32":
        assert stub.taskkill_argv == [["taskkill", "/F", "/PID", "4321"]]


# ════════════════════════════════════════════════════════════════════════════
# 子进程不遗留：reap_children（默认关闭，默认路径零变化）
# ════════════════════════════════════════════════════════════════════════════

def test_reap_children_is_off_by_default():
    """默认 reap_children=False ⇒ 不取后代、不补杀（保证默认路径逐字不变）。"""
    runner, _events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    called = []
    records = spg.cleanup_port_listeners(
        5678, runner=runner, cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, audit=lambda r: None,
        children_getter=lambda pid: called.append(pid) or ["5001"])
    assert called == [], "默认不应调用 children_getter"
    assert len(stub.taskkill_argv) == 1
    assert [r["target_pid"] for r in records] == ["4321"]


def test_reap_children_kills_orphans_after_parent():
    """reap_children=True ⇒ 先记后代、后强杀父、再把仍匹配的后代补杀掉。"""
    runner, events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    collected = []
    spg.cleanup_port_listeners(
        5678, runner=runner, cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, audit=collected.append, reap_children=True,
        children_getter=lambda pid: ["5001", "5002"],
        child_guard=lambda child, parent: child == "5001")
    if sys.platform == "win32":
        assert stub.taskkill_argv == [["taskkill", "/F", "/PID", "4321"],
                                      ["taskkill", "/F", "/PID", "5001"]], \
            "只应补杀通过 PPID 闸的后代（5002 被闸挡住）：%s" % stub.taskkill_argv
    reaped = [r for r in collected
              if r["action"] == "startup.port_cleanup.reap_child"]
    assert [r["target_pid"] for r in reaped] == ["5001"]
    assert reaped[0]["parent_pid"] == "4321"
    assert reaped[0]["reason"] == "orphan_child_of_killed_listener"


def test_reap_children_never_touches_self_or_targets():
    """后代集合必须先剔掉目标自身与本进程 —— 否则会自杀/重复杀。"""
    runner, _events, stub = _make_events_runner(NETSTAT_OLD_INSTANCE)
    seen = []
    spg.cleanup_port_listeners(
        5678, runner=runner, cmdline_getter=lambda pid: "",
        self_pid=777, audit=lambda r: None, reap_children=True,
        # 4321 = 目标自身；777 = 本进程（self_pid）；只有 5003 是真后代
        children_getter=lambda pid: ["4321", "777", "5003"],
        child_guard=lambda child, parent: seen.append((child, parent)) or True)
    assert seen == [("5003", "4321")], "目标自身与本进程都被剔掉，只剩 5003"
    if sys.platform == "win32":
        assert stub.taskkill_argv[-1] == ["taskkill", "/F", "/PID", "5003"]


def test_reap_records_use_own_action_name_for_audit():
    """补杀也必须留痕（与父进程的 kill_before 记录一样可自证）。"""
    collected = []
    spg.cleanup_port_listeners(
        5678, runner=_StubRunner(), cmdline_getter=lambda pid: "python app_server.py",
        self_pid=777, audit=collected.append, reap_children=True,
        children_getter=lambda pid: ["5001"], child_guard=lambda c, p: True)
    actions = [r["action"] for r in collected]
    assert actions[0] == "startup.port_cleanup.kill_before"
    assert actions[1] == "startup.port_cleanup.reap_child"


# ════════════════════════════════════════════════════════════════════════════
# Job Object 装载：失败必须降级而不是抛
# ════════════════════════════════════════════════════════════════════════════

def test_reaper_is_noop_off_windows():
    status = install_child_process_reaper(platform="linux")
    assert status["installed"] is False
    assert status["reason"] == "platform_not_win32"


def test_reaper_degrades_when_assign_process_fails(monkeypatch):
    """AssignProcessToJobObject 失败（例如已被外部 Job 限制）⇒ 降级，不抛。"""
    monkeypatch.setattr(spg, "_REAPER_JOB_HANDLE", None)

    class _K:
        def CreateJobObjectW(self, *a): return 111
        def SetInformationJobObject(self, *a): return 1
        def OpenProcess(self, *a): return 222
        def AssignProcessToJobObject(self, *a): return 0
        def CloseHandle(self, *a): return 1

    class _C:
        @staticmethod
        def get_last_error(): return 5
        @staticmethod
        def byref(x): return x
        @staticmethod
        def sizeof(x): return 8

    class _Limit:
        def __init__(self):
            self.BasicLimitInformation = type("B", (), {"LimitFlags": 0})()

    api = {"ctypes": _C, "kernel32": _K(), "ExtendedLimit": _Limit}
    status = install_child_process_reaper(platform="win32",
                                          api_factory=lambda: api)
    assert status["installed"] is False
    assert status["reason"] == "AssignProcessToJobObject_failed:err=5"
    assert spg._REAPER_JOB_HANDLE is None, "失败时不得留下半成品句柄"


def test_reaper_degrades_when_create_job_fails(monkeypatch):
    monkeypatch.setattr(spg, "_REAPER_JOB_HANDLE", None)

    class _K:
        def CreateJobObjectW(self, *a): return 0

    class _C:
        @staticmethod
        def get_last_error(): return 87
        @staticmethod
        def byref(x): return x
        @staticmethod
        def sizeof(x): return 8

    api = {"ctypes": _C, "kernel32": _K(),
           "ExtendedLimit": lambda: type("L", (), {"BasicLimitInformation": None})()}
    status = install_child_process_reaper(platform="win32",
                                          api_factory=lambda: api)
    assert status["installed"] is False
    assert "CreateJobObject_failed" in status["reason"]


# ════════════════════════════════════════════════════════════════════════════
# 唯一的"真进程"用例：证明 Job Object 真的把子进程带走了
# ════════════════════════════════════════════════════════════════════════════

_PARENT_SRC = '''
import subprocess, sys, time
sys.path.insert(0, {repo!r})
from agent.server_port_guard import install_child_process_reaper
status = install_child_process_reaper()
print("INSTALLED %s" % status.get("installed"), flush=True)
if not status.get("installed"):
    print("REASON %s" % status.get("reason"), flush=True)
    sys.exit(7)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
print("CHILD %d" % child.pid, flush=True)
time.sleep(600)
'''


def _is_alive(pid):
    """只用 tasklist/psutil 判活；两者都不可用则按"活着"处理（保守）。"""
    try:
        import psutil
        return psutil.pid_exists(int(pid))
    except Exception:  # noqa: BLE001
        pass
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                             capture_output=True, text=True, timeout=10).stdout or ""
        return str(int(pid)) in out
    except Exception:  # noqa: BLE001
        return True


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object 仅 Windows 存在")
def test_job_object_kills_children_on_parent_force_kill(tmp_path):
    """**实测**：加入 KILL_ON_JOB_CLOSE 的父进程被 taskkill /F 后，子进程自动消失。

    这条用例会真的 taskkill /F —— 但对象是**本用例自己刚 spawn 的**父进程
    （parent_script，一个只 slept 的临时脚本），以及它自己 spawn 的子进程。
    它不触碰任何既有服务、不触碰任何第三方进程，且 finally 里无条件兜底清理。
    """
    parent_script = tmp_path / "a1_job_parent.py"
    parent_script.write_text(_PARENT_SRC.format(repo=os.path.dirname(
        os.path.dirname(os.path.abspath(spg.__file__)))), encoding="utf-8")

    parent = subprocess.Popen([sys.executable, str(parent_script)],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace")
    child_pid = None
    try:
        line = parent.stdout.readline().strip()
        if line != "INSTALLED True":
            reason = parent.stdout.readline().strip()
            pytest.skip("本机无法加入 Job Object（%s / %s）⇒ 无法验证该机制"
                        % (line, reason))

        line = parent.stdout.readline().strip()
        assert line.startswith("CHILD "), "父进程未报告子进程 PID：%r" % line
        child_pid = int(line.split()[1])

        assert _is_alive(parent.pid), "父进程应仍在运行"
        assert _is_alive(child_pid), "子进程应仍在运行"

        subprocess.run(["taskkill", "/F", "/PID", str(parent.pid)],
                       capture_output=True, timeout=15)
        deadline = time.time() + 15
        while time.time() < deadline and _is_alive(child_pid):
            time.sleep(0.3)
        assert not _is_alive(child_pid), \
            "父进程被 taskkill /F 后子进程 %d 仍存活 ⇒ 子进程会被孤儿化" % child_pid
    finally:
        for pid in (parent.pid, child_pid):
            if pid is None:
                continue
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                               capture_output=True, timeout=10)
            except Exception:  # noqa: BLE001
                pass
        try:
            parent.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            parent.wait(timeout=10)
        except Exception:  # noqa: BLE001
            pass
