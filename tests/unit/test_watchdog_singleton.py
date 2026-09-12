#!/usr/bin/env python3
"""单机 Watchdog 单例守卫单元测试（TASK-S4-03 步骤 4 / v7.2 §4.4 P7.2-16）

覆盖验收项：
    1. **分裂脑防护**：单机模式仅允许一个 Watchdog 实例（lockfile 强制）——
       第二个实例 `acquire()` 必须抛 `SplitBrainError`（而不是排队等待）；
    2. 释放幂等、同实例重复 acquire 幂等、上下文管理器可用；
    3. 锁文件内容**仅用于诊断**（pid/host/role/backend），唯一性判定归 OS 锁；
    4. `backend == single_host_lockfile`，并预留 `cluster_backend_reserved == leader_lease`（P5）。

【路径纪律】锁文件一律落在 `tmp_path`，绝不碰真实运行目录 `data/state/`。
【平台说明】Windows `msvcrt` 的字节区间锁会阻塞**其他句柄**读取被锁区间，
    故持锁期间经独立句柄读锁文件会 `PermissionError` → `read_holder()` 返回 {}。
    这是平台语义（本模块的锁内容本就只是诊断），相关断言按此口径分流。
"""
import json
import logging
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.self_healing import watchdog_singleton as ws_mod
from agent.self_healing.watchdog_singleton import (
    DEFAULT_LOCK_PATH,
    ENV_LOCK_PATH,
    LEASE_BACKEND_CLUSTER,
    LEASE_BACKEND_SINGLE_HOST,
    ROLE_WATCHDOG,
    LockHolder,
    SplitBrainError,
    WatchdogLockError,
    WatchdogSingleton,
    acquire_watchdog_singleton,
    active_guards,
    default_lock_path,
    reset_watchdog_singleton,
    watchdog_singleton_status,
)

#: status() 必须提供的键（面板/运维契约）
STATUS_KEYS = {
    "lock_path", "role", "held_by_self", "self_pid", "lockfile_holder",
    "stale", "backend", "cluster_backend_reserved",
}

#: stale_evidence() 必须提供的键（陈旧判定的证据明细契约）
STALE_EVIDENCE_KEYS = {
    "lock_path", "lockfile_exists", "lockfile_holder", "recorded_pid",
    "recorded_pid_alive", "os_lock_acquirable", "stale", "note",
}


@pytest.fixture(autouse=True)
def _isolate_watchdog_singleton():
    """逐用例隔离：先释放并清空进程内守卫登记（否则锁被上个用例占着）"""
    reset_watchdog_singleton()
    yield
    reset_watchdog_singleton()


@pytest.fixture
def lock_path(tmp_path):
    """tmp 下的锁文件路径（父目录已存在）"""
    return tmp_path / "watchdog.lock"


# ════════════════════════════════════════════════════════════
#  1. 获取 / 分裂脑拒绝
# ════════════════════════════════════════════════════════════


class TestAcquireAndSplitBrain:
    """第一个实例拿到，第二个实例被拒"""

    def test_first_acquire_succeeds(self, lock_path):
        """首次 acquire 成功：held True、锁文件落盘、登记进进程内守卫表"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        assert guard.held is False
        assert guard.acquire() is guard
        assert guard.held is True
        assert lock_path.exists()
        assert active_guards() == {str(lock_path): guard}
        assert guard.holder().pid == os.getpid()
        assert guard.holder().role == ROLE_WATCHDOG

    def test_second_instance_same_lock_path_is_refused(self, lock_path):
        """【headline】第二个实例被拒：SplitBrainError 且携带 holder/lock_path"""
        first = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        with pytest.raises(SplitBrainError) as exc:
            WatchdogSingleton(lock_path=str(lock_path)).acquire()
        err = exc.value
        assert err.lock_path == str(lock_path)
        assert err.holder.get("pid") == os.getpid()
        assert err.holder.get("role") == ROLE_WATCHDOG
        assert err.holder.get("backend") == LEASE_BACKEND_SINGLE_HOST
        assert "单机仅允许一个 Watchdog 实例" in str(err)
        assert first.held is True                    # 原实例不受影响
        assert active_guards()[str(lock_path)] is first

    def test_second_guard_object_is_never_held_after_refusal(self, lock_path):
        """被拒的守卫不得残留半持有状态（否则会出现"两个都活着"的假象）"""
        WatchdogSingleton(lock_path=str(lock_path)).acquire()
        intruder = WatchdogSingleton(lock_path=str(lock_path))
        with pytest.raises(SplitBrainError):
            intruder.acquire()
        assert intruder.held is False
        assert intruder.release() is False

    def test_os_lock_rejects_second_handle_without_inproc_registry(self, lock_path):
        """绕过进程内登记（register_singleton=False）仍被 OS 锁拒绝——跨进程权威判定

        并且此时报错必须带**真实**持有者身份（身份槽在字节 1 起、锁只覆盖字节 0，
        故读取不再被字节区间锁拦住）。
        """
        first = WatchdogSingleton(lock_path=str(lock_path), register_singleton=False)
        first.acquire()
        second = WatchdogSingleton(lock_path=str(lock_path), register_singleton=False)
        with pytest.raises(SplitBrainError) as exc:
            second.acquire()
        err = exc.value
        assert err.lock_path == str(lock_path)
        assert err.holder != {}
        assert isinstance(err.holder["pid"], int)
        assert err.holder["pid"] == os.getpid()
        assert err.holder["role"] == ROLE_WATCHDOG
        assert err.holder["backend"] == LEASE_BACKEND_SINGLE_HOST
        assert first.held is True and second.held is False
        assert active_guards() == {}

    def test_different_lock_paths_are_independent(self, tmp_path):
        """不同锁文件互不影响（唯一性按路径，不是全局单例）"""
        first = WatchdogSingleton(lock_path=str(tmp_path / "a.lock")).acquire()
        second = WatchdogSingleton(lock_path=str(tmp_path / "b.lock")).acquire()
        assert first.held is True and second.held is True
        assert len(active_guards()) == 2
        assert second.lock_path.endswith("b.lock")

    def test_acquire_creates_parent_directory(self, tmp_path):
        """父目录不存在时自动创建（首次启动可用）"""
        nested = tmp_path / "state" / "deep" / "watchdog.lock"
        WatchdogSingleton(lock_path=str(nested)).acquire()
        assert nested.parent.is_dir()
        assert nested.exists()

    def test_unusable_lock_path_raises_lock_error(self, tmp_path):
        """锁文件路径不可用（指向目录）→ WatchdogLockError（不是 SplitBrainError）"""
        with pytest.raises(WatchdogLockError) as exc:
            WatchdogSingleton(lock_path=str(tmp_path)).acquire()
        assert "锁文件不可用" in str(exc.value)

    def test_timeout_argument_is_non_blocking_and_warns(self, lock_path, caplog):
        """timeout_s>0 仅告警并忽略：单机守卫是"被拒"语义，不排队"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        with caplog.at_level(logging.WARNING, logger="agent.self_healing.watchdog_singleton"):
            guard.acquire(timeout_s=5.0)
        assert guard.held is True
        assert any("非阻塞" in rec.getMessage() for rec in caplog.records)


# ════════════════════════════════════════════════════════════
#  2. 释放与幂等
# ════════════════════════════════════════════════════════════


class TestReleaseAndIdempotency:
    """release / 重复 acquire / 上下文管理器"""

    def test_release_frees_lock_for_new_instance(self, lock_path):
        """release 后新实例可重新获取（OS 锁与登记同时释放）"""
        first = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        assert first.release() is True
        assert first.held is False
        assert active_guards() == {}
        second = WatchdogSingleton(lock_path=str(lock_path))
        assert second.acquire().held is True
        assert second.held is True

    def test_release_is_idempotent(self, lock_path):
        """重复 release：第二次返回 False 且不抛"""
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        assert guard.release() is True
        assert guard.release() is False
        assert guard.release() is False
        assert guard.held is False

    def test_release_without_acquire_returns_false(self, lock_path):
        """从未 acquire 的实例 release 返回 False（不抛、不建文件）"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        assert guard.release() is False
        assert guard.held is False
        assert not lock_path.exists()

    def test_same_instance_double_acquire_is_idempotent(self, lock_path):
        """同实例重复 acquire 幂等：返回 self，仍持有，不新建句柄"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        assert guard.acquire() is guard
        holder_before = guard.holder()
        assert guard.acquire() is guard
        assert guard.acquire() is guard
        assert guard.held is True
        assert guard.holder() is holder_before
        assert active_guards() == {str(lock_path): guard}

    def test_reacquire_after_release_keeps_working(self, lock_path):
        """释放→再获取→再释放 循环稳定（不因句柄泄漏卡死）"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        for _ in range(3):
            assert guard.acquire().held is True
            assert guard.release() is True
            assert guard.held is False

    def test_context_manager_acquires_and_releases(self, lock_path):
        """with 形态：进入即持有，退出即释放"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        with guard as entered:
            assert entered is guard
            assert guard.held is True
            assert lock_path.exists()
        assert guard.held is False
        assert active_guards() == {}
        # 退出后可被新实例获取
        assert WatchdogSingleton(lock_path=str(lock_path)).acquire().held is True

    def test_context_manager_releases_on_exception(self, lock_path):
        """with 体内抛异常也必须释放（否则崩溃后残留单例）"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        with pytest.raises(RuntimeError):
            with guard:
                raise RuntimeError("业务异常")
        assert guard.held is False
        assert WatchdogSingleton(lock_path=str(lock_path)).acquire().held is True

    def test_acquire_helper_returns_held_guard(self, lock_path):
        """便捷入口 acquire_watchdog_singleton 返回已持有守卫"""
        guard = acquire_watchdog_singleton(str(lock_path), role="watchdog-nightly")
        assert guard.held is True
        assert guard.role == "watchdog-nightly"
        assert guard.holder().role == "watchdog-nightly"
        with pytest.raises(SplitBrainError):
            acquire_watchdog_singleton(str(lock_path))
        assert guard.release() is True


# ════════════════════════════════════════════════════════════
#  3. 锁文件内容（诊断）与陈旧判定
# ════════════════════════════════════════════════════════════


class TestHolderFileAndStaleness:
    """read_holder / is_stale / 身份写入"""

    def test_read_holder_returns_written_identity(self, lock_path):
        """**持锁期间**即可读到本进程写入的身份（pid/host/role/backend/started_at）

        【实现期修正】修正前整文件读取会碰到字节 0 → Windows 上必然
        `PermissionError`（正是最需要"谁持有"的时候读不到）。现在身份放在字节 1 起的
        定长槽，`read_holder()` 先 `seek(HOLDER_SLOT_OFFSET)` 跳过锁字节再读。
        """
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        holder = guard.read_holder()
        assert holder != {}
        assert holder["pid"] == os.getpid()
        assert holder["role"] == ROLE_WATCHDOG
        assert holder["backend"] == LEASE_BACKEND_SINGLE_HOST
        assert holder["host"] == LockHolder.current().host
        assert holder["started_at"] > 0
        assert holder["started_iso"]
        assert guard.status()["lockfile_holder"] == holder

    def test_read_holder_visible_from_other_instance_while_held(self, lock_path):
        """另一实例（register_singleton=False）在**持锁期间**也能读到真实身份

        这是跨进程场景的等价形态：被拒的第二个实例必须能说清"谁持有"。
        """
        holder_guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        reader = WatchdogSingleton(lock_path=str(lock_path), register_singleton=False)
        holder = reader.read_holder()
        assert holder != {}
        assert holder["pid"] == os.getpid()
        assert holder["role"] == ROLE_WATCHDOG
        assert holder["backend"] == LEASE_BACKEND_SINGLE_HOST
        assert holder["pid"] == holder_guard.holder().pid
        assert reader.status()["lockfile_holder"] == holder
        assert reader.status()["held_by_self"] is False

    def test_lock_file_layout_sentinel_byte_and_holder_slot(self, lock_path):
        """锁文件布局：字节 0 是锁专用哨兵，身份 JSON 在字节 1 起的定长槽

        持锁期间整文件读取仍会被拒（字节 0 被锁），故字节 0/长度的断言在释放后做
        ——哨兵不因释放而消失，`_write_holder` 也从不 truncate（truncate 会让已持有
        的区间失效）。
        """
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        assert ws_mod.HOLDER_SLOT_OFFSET == 1
        assert os.path.getsize(lock_path) > 1                 # 至少哨兵 + 身份槽
        assert os.path.getsize(lock_path) == (
            ws_mod.HOLDER_SLOT_OFFSET + ws_mod.HOLDER_SLOT_BYTES)
        guard.release()

        raw = lock_path.read_bytes()
        assert raw[:1] == b"\x00"                              # 字节 0 = 哨兵
        assert len(raw) > 1
        slot = raw[ws_mod.HOLDER_SLOT_OFFSET:
                   ws_mod.HOLDER_SLOT_OFFSET + ws_mod.HOLDER_SLOT_BYTES]
        assert len(slot) == ws_mod.HOLDER_SLOT_BYTES           # 定长槽（不足补空格）
        payload = json.loads(slot.decode("utf-8").strip("\0 \r\n\t"))
        assert payload["pid"] == os.getpid()
        assert payload["backend"] == LEASE_BACKEND_SINGLE_HOST
        # 槽被空格补齐 ⇒ 读回文本以空格结尾（保证不残留上一次更长的内容）
        assert slot.rstrip(b" ").endswith(b"}")

    def test_reacquire_overwrites_holder_slot_in_place(self, lock_path):
        """重复获取**原地覆写**身份槽：读到的是新持有者，文件长度恒为 513

        【实现期修正】第一版用 `open(path, "a+")`：追加模式下 `seek()` 对写无效，
        新身份被**追加**到文件尾 ⇒ `read_holder()` 读到**上一个**持有者，且文件每次
        acquire 涨 513 字节。现改用 `os.open(O_RDWR|O_CREAT)` + `fdopen(fd, "r+b")`
        （`O_CREAT` 不截断、`r+b` 可定位写），写后再 `truncate(513)` 清历史尾巴。
        """
        slot_total = ws_mod.HOLDER_SLOT_OFFSET + ws_mod.HOLDER_SLOT_BYTES   # 513
        first_holder = LockHolder(pid=os.getpid(), host="host-a", role=ROLE_WATCHDOG,
                                  note="A")
        first = WatchdogSingleton(lock_path=str(lock_path), holder=first_holder)
        first.acquire()
        # 注入的 LockHolder 生效（不是 LockHolder.current()）
        assert first.holder() is first_holder
        assert first.read_holder()["pid"] == os.getpid()
        assert first.read_holder()["note"] == "A"
        assert os.path.getsize(lock_path) == slot_total          # 哨兵 + 定长槽
        first.release()
        assert lock_path.read_bytes()[:1] == b"\x00"             # 释放后可读字节 0

        second_pid = 424242                                      # 刻意不同于本进程
        second = WatchdogSingleton(
            lock_path=str(lock_path),
            holder=LockHolder(pid=second_pid, host="host-b", role=ROLE_WATCHDOG,
                              note="B"))
        second.acquire()
        assert second.holder().pid == second_pid
        assert second.read_holder()["pid"] == second_pid         # 新身份真正覆盖槽 1
        assert second.read_holder()["pid"] != os.getpid()
        assert second.read_holder()["host"] == "host-b"
        assert second.read_holder()["note"] == "B"
        assert os.path.getsize(lock_path) == slot_total          # 无增长
        second.release()

        raw = lock_path.read_bytes()
        assert len(raw) == slot_total                            # 长度仍恒为 513
        assert raw[:1] == b"\x00"                                # 哨兵未被破坏
        payload = json.loads(raw[ws_mod.HOLDER_SLOT_OFFSET:slot_total]
                             .decode("utf-8").strip("\0 \r\n\t"))
        assert payload["pid"] == second_pid                      # 槽里是 B 的身份
        assert payload["note"] == "B"

    def test_reacquire_cycles_do_not_grow_lockfile(self, lock_path):
        """反复 acquire/release 不得让锁文件增长（长跑驻留的稳定性）"""
        slot_total = ws_mod.HOLDER_SLOT_OFFSET + ws_mod.HOLDER_SLOT_BYTES
        guard = WatchdogSingleton(lock_path=str(lock_path))
        for round_no in range(5):
            guard.acquire()
            assert os.path.getsize(lock_path) == slot_total, round_no
            assert guard.read_holder()["pid"] == os.getpid()
            guard.release()
            assert os.path.getsize(lock_path) == slot_total, round_no

    def test_read_holder_missing_or_corrupt_returns_empty(self, lock_path):
        """锁文件不存在/内容损坏/为空 → {}（不抛）"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        assert guard.read_holder() == {}
        lock_path.write_text("{这不是 JSON", encoding="utf-8")
        assert guard.read_holder() == {}
        lock_path.write_text("\0\0  \r\n", encoding="utf-8")
        assert guard.read_holder() == {}

    def test_read_holder_supports_legacy_whole_file_json(self, lock_path):
        """历史形态兜底：整文件就是 JSON（无哨兵、无定长槽）仍可解析"""
        guard = WatchdogSingleton(lock_path=str(lock_path))
        lock_path.write_text(json.dumps({"pid": 4242, "role": ROLE_WATCHDOG}),
                             encoding="utf-8")
        holder = guard.read_holder()
        assert holder["pid"] == 4242
        assert holder["role"] == ROLE_WATCHDOG

    def test_is_stale_false_while_held(self, lock_path):
        """本实例持锁时 is_stale() False（锁在手上，绝不自判陈旧）"""
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        assert guard.is_stale() is False
        assert guard.status()["stale"] is False

    def test_is_stale_false_when_no_lock_file(self, tmp_path):
        """锁文件不存在 → False（无残留可清）"""
        guard = WatchdogSingleton(lock_path=str(tmp_path / "absent.lock"))
        assert guard.is_stale() is False

    def test_is_stale_false_when_other_guard_holds(self, lock_path):
        """他人持锁时 is_stale() False（拿不到锁即证明有活跃持有者）"""
        WatchdogSingleton(lock_path=str(lock_path)).acquire()
        observer = WatchdogSingleton(lock_path=str(lock_path), register_singleton=False)
        assert observer.is_stale() is False
        assert observer._os_lock_acquirable() is False

    def test_is_stale_true_for_lockfile_without_usable_holder(self, lock_path):
        """残留锁文件（身份不可读）+ 无人持锁 → 判为陈旧残留"""
        lock_path.write_text("{损坏的锁内容", encoding="utf-8")
        guard = WatchdogSingleton(lock_path=str(lock_path))
        assert guard.read_holder() == {}
        assert guard.is_stale() is True      # 本模块**不自动**回收，只如实报告

    def test_is_stale_true_for_simulated_dead_holder(self, lock_path):
        """模拟"持有者进程已消亡"：哨兵 + 不可能存活的 pid → is_stale() True

        判定已改为「锁文件存在（挂 OS 锁成功）⇒ 原持有者已消亡」，**不再**用 pid
        探活否决结论（修正前 Windows 上 `_pid_alive` 保守返回 True → 永远判不出陈旧）。
        pid 只作为 `stale_evidence()` 的证据字段出现。
        """
        for dead_pid in (-1, 999999999):
            lock_path.write_bytes(
                b"\x00" + json.dumps({"pid": dead_pid, "host": "gone", "role": ROLE_WATCHDOG}
                                     ).encode("utf-8").ljust(ws_mod.HOLDER_SLOT_BYTES, b" "))
            guard = WatchdogSingleton(lock_path=str(lock_path))
            assert guard.read_holder()["pid"] == dead_pid
            assert guard.is_stale() is True
            evidence = guard.stale_evidence()
            assert evidence["recorded_pid"] == dead_pid
            assert evidence["os_lock_acquirable"] is True
            assert evidence["stale"] is True

    def test_is_stale_after_release_reports_residue(self, lock_path):
        """本进程 release() 之后：无活跃持有者 ⇒ is_stale() True（残留可清理）

        判定口径是「锁文件存在 ∧ 本进程能立刻拿到 OS 锁」，**pid 不否决**结论
        （模块 docstring 明示：自己释放之后返回 True 是正确的——"残留锁文件、
        无活跃持有者"）。`_pid_alive` 只作为 `stale_evidence()["recorded_pid_alive"]`
        的证据字段出现。
        """
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        assert guard.is_stale() is False
        guard.release()
        evidence = guard.stale_evidence()
        assert evidence["lockfile_exists"] is True
        assert evidence["recorded_pid"] == os.getpid()
        assert evidence["recorded_pid_alive"] is True       # 本进程活着（证据，不否决）
        assert evidence["os_lock_acquirable"] is True       # 权威信号：能拿到锁
        assert guard.is_stale() is True

    def test_stale_evidence_reports_documented_keys(self, tmp_path, lock_path):
        """stale_evidence() 给出判定明细（结论 + 理由字段齐）"""
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        evidence = guard.stale_evidence()
        assert STALE_EVIDENCE_KEYS <= set(evidence)
        assert evidence["lock_path"] == str(lock_path)
        assert evidence["lockfile_exists"] is True
        assert evidence["lockfile_holder"]["pid"] == os.getpid()
        assert evidence["recorded_pid"] == os.getpid()
        assert evidence["recorded_pid_alive"] is True
        assert evidence["os_lock_acquirable"] is False       # 本实例正持锁
        assert evidence["stale"] is False
        assert isinstance(evidence["note"], str) and evidence["note"]

    def test_stale_evidence_on_missing_lockfile_has_no_side_effect(self, tmp_path):
        """无锁文件时 `stale_evidence()` 三项全 False，且**绝不创建**锁文件

        【实现期修正】第一版 `_os_lock_acquirable()` 用 `open(path, "a+")` 探测 →
        创建了文件，而 `lockfile_exists` 先于它求值、`stale` 后于它求值，于是返回
        「lockfile_exists=False + stale=True」这种无意义结论。现在：观测与探测都用
        `_open_lockfile(create=False)`（缺失即返回 None），且 `stale` 先算。
        """
        path = tmp_path / "absent.lock"
        guard = WatchdogSingleton(lock_path=str(path))
        evidence = guard.stale_evidence()
        assert STALE_EVIDENCE_KEYS <= set(evidence)
        assert evidence["lockfile_exists"] is False
        assert evidence["os_lock_acquirable"] is False
        assert evidence["stale"] is False
        assert evidence["lockfile_holder"] == {}
        assert evidence["recorded_pid"] == -1
        assert evidence["recorded_pid_alive"] is False
        assert path.exists() is False          # 只读诊断不得产生副作用

    def test_is_stale_on_missing_lockfile_does_not_create_it(self, tmp_path):
        """无锁文件时 `is_stale()` False，且探测本身不创建文件（可反复调用）"""
        path = tmp_path / "absent.lock"
        guard = WatchdogSingleton(lock_path=str(path))
        for _ in range(3):
            assert guard.is_stale() is False
            assert guard._os_lock_acquirable() is False
            assert path.exists() is False

    def test_stale_evidence_records_deterministic_dead_holder(self, lock_path):
        """伪造"已消亡持有者"（pid=999999）→ 判定为陈旧，证据字段如实记录

        Windows 上 `_pid_alive` 保守返回 True（不做 pid 探活），故 **不否决**结论：
        `stale` 由「锁文件存在 ∧ 能拿到 OS 锁」决定，跨平台一致。
        """
        dead_pid = 999999
        lock_path.write_bytes(
            b"\x00" + json.dumps({"pid": dead_pid, "host": "gone",
                                  "role": ROLE_WATCHDOG}).encode("utf-8")
            .ljust(ws_mod.HOLDER_SLOT_BYTES, b" "))
        guard = WatchdogSingleton(lock_path=str(lock_path))
        evidence = guard.stale_evidence()
        assert evidence["lockfile_exists"] is True
        assert evidence["lockfile_holder"]["pid"] == dead_pid
        assert evidence["recorded_pid"] == dead_pid
        assert isinstance(evidence["recorded_pid_alive"], bool)   # 平台相关证据
        assert evidence["os_lock_acquirable"] is True
        assert evidence["stale"] is True
        assert guard.is_stale() is True
        assert guard.read_holder()["pid"] == dead_pid


# ════════════════════════════════════════════════════════════
#  4. status / 面板契约 / 全局入口
# ════════════════════════════════════════════════════════════


class TestStatusAndModuleEntry:
    """status() / watchdog_singleton_status() / reset / 默认路径"""

    def test_status_keys_and_backend_declaration(self, tmp_path):
        """status 键齐；单机后端 + P5 集群后端形状留出"""
        guard = WatchdogSingleton(lock_path=str(tmp_path / "w.lock"), role="watchdog")
        status = guard.status()
        assert STATUS_KEYS <= set(status)
        assert status["backend"] == "single_host_lockfile"
        assert status["cluster_backend_reserved"] == "leader_lease"
        assert status["role"] == ROLE_WATCHDOG
        assert status["lock_path"] == str(tmp_path / "w.lock")
        assert status["self_pid"] == os.getpid()
        assert status["held_by_self"] is False
        assert status["lockfile_holder"] == {}
        assert status["stale"] is False

    def test_status_held_by_self_true_while_held(self, lock_path):
        """持锁时 held_by_self True 且能报出持有者身份；释放后回到 False"""
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        status = guard.status()
        assert status["held_by_self"] is True
        assert status["stale"] is False
        assert status["lockfile_holder"] == guard.read_holder()
        assert status["lockfile_holder"]["pid"] == os.getpid()
        guard.release()
        assert guard.status()["held_by_self"] is False

    def test_watchdog_singleton_status_without_holder(self, tmp_path):
        """只读状态查询：无持有者时报 held_by_self False 且不抛、不建锁"""
        path = tmp_path / "no-holder.lock"
        status = watchdog_singleton_status(str(path))
        assert status["held_by_self"] is False
        assert status["held_by_inprocess"] is False
        assert status["backend"] == LEASE_BACKEND_SINGLE_HOST
        assert status["lock_path"] == str(path)
        assert path.exists() is False          # 只读查询不得创建锁文件

    def test_watchdog_singleton_status_sees_inprocess_holder(self, lock_path):
        """进程内已有持有者时，只读查询能报告 held_by_inprocess + 真实持有者身份"""
        guard = WatchdogSingleton(lock_path=str(lock_path)).acquire()
        status = watchdog_singleton_status(str(lock_path))
        assert status["held_by_self"] is False        # 查询者不是持有者
        assert status["held_by_inprocess"] is True
        assert status["role"] == guard.role
        assert status["lockfile_holder"]["pid"] == os.getpid()

    def test_reset_clears_active_guards(self, tmp_path):
        """reset_watchdog_singleton 释放并清空登记（用例隔离入口）"""
        first = WatchdogSingleton(lock_path=str(tmp_path / "a.lock")).acquire()
        WatchdogSingleton(lock_path=str(tmp_path / "b.lock")).acquire()
        assert len(active_guards()) == 2
        reset_watchdog_singleton()
        assert active_guards() == {}
        assert first.held is False
        # 复位后同一路径可再次获取
        assert WatchdogSingleton(lock_path=str(tmp_path / "a.lock")).acquire().held is True
        reset_watchdog_singleton()
        assert active_guards() == {}

    def test_default_lock_path_honours_env(self, tmp_path, monkeypatch):
        """默认路径：环境变量优先，缺省回落 DEFAULT_LOCK_PATH"""
        monkeypatch.setenv(ENV_LOCK_PATH, str(tmp_path / "env.lock"))
        assert default_lock_path() == str(tmp_path / "env.lock")
        guard = WatchdogSingleton()
        assert guard.lock_path == str(tmp_path / "env.lock")
        assert guard.acquire().held is True
        monkeypatch.delenv(ENV_LOCK_PATH, raising=False)
        assert default_lock_path() == DEFAULT_LOCK_PATH
        assert DEFAULT_LOCK_PATH.replace("\\", "/").startswith("data/")

    def test_lock_holder_serialization(self):
        """LockHolder：current()/to_dict/from_dict 往返；非法 pid 回退 -1"""
        holder = LockHolder.current(role="watchdog", note="首启")
        payload = holder.to_dict()
        assert payload["pid"] == os.getpid()
        assert payload["role"] == "watchdog" and payload["note"] == "首启"
        assert payload["backend"] == LEASE_BACKEND_SINGLE_HOST
        restored = LockHolder.from_dict(payload)
        assert restored.pid == holder.pid and restored.role == holder.role
        assert LockHolder.from_dict({}).pid == -1
        assert LockHolder.from_dict({}).role == ROLE_WATCHDOG

    def test_module_constants_and_exports(self):
        """常量与 `__all__` 自洽（P5 集群后端只留形状，不实现）"""
        assert ws_mod.ROLE_WATCHDOG == "watchdog"
        assert LEASE_BACKEND_SINGLE_HOST == "single_host_lockfile"
        assert LEASE_BACKEND_CLUSTER == "leader_lease"
        for name in ws_mod.__all__:
            assert hasattr(ws_mod, name), name
