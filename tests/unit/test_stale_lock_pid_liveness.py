"""陈旧锁判定：持有者 pid 已不存在 ⇒ 视为失效锁且可安全获取（W1 / TASK-01）

现场证据：生产 .env.lock 内容为
  {"pid": 14512, "host": "DESKTOP-CN00D5I", "name": "env_config.lock", ...}
而 pid 14512 在本机不存在（Get-Process -Id 14512 计数为 0）=> 陈旧锁。

本文件覆盖两件事：
1. _pid_alive() 的三态语义（存活 / 确定不存在 / 无法判定）；
2. CrossProcessLock.holder_pid_alive() 与 is_stale() 的陈旧锁分支——
   **包括 OS 锁探测不可用时**仅凭 pid 证据仍能判陈旧（用 monkeypatch 造该分支）。
"""

from __future__ import annotations

import json
import os
import socket

import pytest

from agent.utils.cross_process_lock import (
    HOLDER_SLOT_BYTES,
    LOCK_REGION_BYTES,
    CrossProcessLock,
    _pid_alive,
)

#: 一个在本机几乎不可能存在的 pid（远大于 Windows/Linux 常规 pid 上限）
_DEAD_PID = 999_999


def _write_holder_slot(path, payload: dict) -> None:
    """按原语的槽位布局写一个诊断槽（哨兵 + 定长 JSON）——仅供测试构造现场"""
    data = json.dumps(payload).encode("utf-8")
    assert len(data) <= HOLDER_SLOT_BYTES, "测试载荷过长"
    blob = (b"\0" * LOCK_REGION_BYTES) + data + b" " * (HOLDER_SLOT_BYTES - len(data))
    path.write_bytes(blob)


# ── 1. _pid_alive 三态 ─────────────────────────────────────────────

def test_pid_alive_true_for_self():
    assert _pid_alive(os.getpid()) is True


def test_pid_alive_false_for_nonexistent_pid():
    assert _pid_alive(_DEAD_PID) is False


@pytest.mark.parametrize("bad", [0, -1, None, "not-a-pid", 3.5j])
def test_pid_alive_none_for_unusable_input(bad):
    """不可用输入 => None（**不得**据此判陈旧）"""
    assert _pid_alive(bad) is None


# ── 2. holder_pid_alive() ──────────────────────────────────────────

def test_holder_pid_alive_false_for_dead_pid_record(tmp_path):
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.holder_pid_alive() is False


def test_holder_pid_alive_true_for_own_pid_record(tmp_path):
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": os.getpid(), "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.holder_pid_alive() is True


def test_holder_pid_alive_none_for_other_host(tmp_path):
    """跨主机记录：本机查同号 pid 无意义 => 必须弃权（None），防止误判陈旧"""
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": "SOME-OTHER-HOST",
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.holder_pid_alive() is None


def test_holder_pid_alive_none_when_no_record(tmp_path):
    lk_path = tmp_path / ".env.lock"
    lk_path.write_bytes(b"\0")
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.holder_pid_alive() is None


# ── 3. is_stale() 陈旧锁分支 ────────────────────────────────────────

def test_stale_true_for_dead_pid_lockfile(tmp_path):
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.is_stale() is True


def test_stale_false_when_os_lock_probe_ok_and_pid_alive(tmp_path):
    """持有者记录是本进程（存活）=> 即使 OS 锁探测被 stub 成"拿不到"，也不得判陈旧

    【这是有牙齿的断言】若把"pid 已死 => 陈旧"写成"记录里只要有 pid 就陈旧"，
    或用 pid 存活误判成死亡，本用例会变红。
    """
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": os.getpid(), "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.holder_pid_alive() is True
    lk._os_lock_acquirable = lambda: False          # 模拟"OS 锁探测不可用"
    assert lk.is_stale() is False


def test_stale_true_by_dead_pid_even_when_os_probe_unavailable(tmp_path, monkeypatch):
    """**核心新增分支**：OS 锁探测不可用时，仅凭"持有者 pid 不存在"即判陈旧

    这就是任务要求的"PID 不存在 ⇒ 视为失效锁并可安全获取"。
    """
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    lk._os_lock_acquirable = lambda: False          # 唯一可用证据只剩 pid
    assert lk.holder_pid_alive() is False
    assert lk.is_stale() is True


def test_stale_true_requires_existing_lockfile(tmp_path):
    lk = CrossProcessLock(tmp_path / "absent.lock", name="env_config.lock")
    assert lk.is_stale() is False


def test_status_exposes_holder_pid_alive(tmp_path):
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    st = lk.status()
    assert st["holder_pid_alive"] is False
    assert st["stale"] is True


def test_stale_lock_is_safely_acquirable(tmp_path):
    """端到端：陈旧锁文件存在时，获取锁必须成功（"可安全获取"）"""
    lk_path = tmp_path / ".env.lock"
    _write_holder_slot(lk_path, {"pid": _DEAD_PID, "host": socket.gethostname(),
                                 "name": "env_config.lock"})
    lk = CrossProcessLock(lk_path, name="env_config.lock")
    assert lk.is_stale() is True
    assert lk.try_lock() is True, "陈旧锁存在时竟然拿不到锁"
    lk.release()
