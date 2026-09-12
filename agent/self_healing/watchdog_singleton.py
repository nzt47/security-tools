"""单机 Watchdog 单例守卫 —— 分裂脑防护（TASK-S4-03 步骤 4 / v7.2 §4.4 P7.2-16）

【规则原文（§4.4 〔P7.2-16〕）】
    分裂脑防护：Watchdog 集群化（企业侧）= 单领导者租约 + 奇数节点仲裁切主；
    **单机模式仅允许一个 Watchdog 实例（lockfile 强制）**。集群化入 P5。

【本模块解决什么】
    "两个 Watchdog 同时活着"在单机上不是性能问题而是**正确性问题**：两边都会
    判定主进程失联、都会尝试拉起、都会写自己的账——即分裂脑。云枢此前**没有任何
    机制**阻止第二个实例启动（`agent/monitoring/lock_watchdog.py` 管的是"持锁超时
    监测"，`SingletonManager` 管的是"同进程内单例"——两者都不跨进程）。本模块补的
    正是**跨进程**那一段。

【与既有设施的关系（勿另建第二套锁 / TASK-S8-02 步骤 3）】
    - **同进程**：走 `agent.utils.singleton_manager` 的既有单例登记（`_INPROC_KEY`），
      不另造进程内单例模式。
    - **跨进程**：调用 `agent.utils.cross_process_lock` 的**唯一**跨进程锁原语
      （`CrossProcessLock.try_lock()`，非阻塞语义），与
      `agent/env_config_manager.py::_acquire_process_lock`、
      `agent/knowledge/ingest.py::_FileLock` 共用同一份实现。
      【为什么必须改写（不易）】改写前本模块**自带**一份 OS 文件锁平台分支
      （`_open_lockfile`/`_try_lock`/`_unlock`，Windows 字节区间锁 / POSIX flock），
      与另外两处是**手抄关系**而非调用关系：三份实现各自长出了不同的坑（本文件的"身份槽定长""探测不得创建
      文件"两条结论就是只有这一份踩到并修好的）。现统一由原语承载，本模块只保留
      **语义包装**（非阻塞 → `SplitBrainError`；锁文件不可用 → `WatchdogLockError`）。
    - **与 lock_watchdog 的边界**：`lock_watchdog` = 持锁时长监测（阈值告警）；
      本模块 = 实例互斥（身份唯一性）。两者关注的量不同，无重叠、无冲突。
    - **P5 预留**：集群化（单领导者租约 + 奇数节点仲裁）**不在本任务范围**；
      `LEASE_BACKEND_SINGLE_HOST` 常量与 `backend` 字段留出接口形状，避免将来改数据形态。

【唯一性判定的层次（从便宜到昂贵）】
    1. 进程内单例（`SingletonManager`）——同进程重复 `acquire()` 直接返回既有句柄；
    2. **OS 文件锁**（非阻塞）——跨进程唯一性的**权威**判定；
    3. 锁文件内容（pid/host/role/started_at）——**仅用于诊断与陈旧锁回收**，
       不作为授权依据（内容可被外部改写，锁才是权威）。

【不易】非阻塞获取（第二个实例**被拒**而不是排队等待）；释放幂等；进程退出不依赖
       锁文件内容即可被判定为陈旧（靠 OS 锁随进程消亡自动释放）。
【变易】`lock_path` 可显式注入（用例用临时目录，绝不碰真实运行目录）。
【简易】只依赖标准库 + 唯一锁原语 `agent.utils.cross_process_lock`（自身不再有平台分支）；
       无后台线程；无自动回收定时器（陈旧判定只在显式调用时发生）。
"""

from __future__ import annotations

import enum
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# 【为什么在模块级导入（TASK-S8-02 步骤 3）】统一原语只依赖标准库，无反向依赖，
# 故可直接模块级导入；本模块**不再**出现任何平台分支（唯一的平台分支已在原语里）。
from agent.utils.cross_process_lock import (
    HOLDER_SLOT_BYTES as _CP_HOLDER_SLOT_BYTES,
    HOLDER_SLOT_OFFSET as _CP_HOLDER_SLOT_OFFSET,
    LOCK_REGION_BYTES as _CP_LOCK_REGION_BYTES,
    CrossProcessLock,
    LockFileError,
)

logger = logging.getLogger("agent.self_healing.watchdog_singleton")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: 默认锁文件（`CP_WATCHDOG_LOCK_PATH` 可覆盖；用例必须显式传路径）
DEFAULT_LOCK_PATH = os.path.join("data", "state", "watchdog.lock")
ENV_LOCK_PATH = "CP_WATCHDOG_LOCK_PATH"

#: 锁文件角色：单机唯一 Watchdog
ROLE_WATCHDOG = "watchdog"

#: 集群化后端（P5；本任务只声明接口形状，不实现）
LEASE_BACKEND_SINGLE_HOST = "single_host_lockfile"
LEASE_BACKEND_CLUSTER = "leader_lease"        # P5：单领导者租约 + 奇数节点仲裁

#: 同进程单例键（复用 SingletonManager）
_INPROC_KEY = "self_healing.watchdog_singleton"

# ── 锁文件布局常量（**别名，唯一权威在统一原语**）──
#
# 【为什么改成别名而不是本地定义（TASK-S8-02 步骤 3）】布局（字节 0 哨兵 + 字节 1
# 起的定长身份槽）是与"谁去读写这个锁文件"绑定的：现在读写的是统一原语，布局必须
# 由它定义。本地再写一遍 `= 1` / `= 512` 就是第四份"手抄的布局常量"，一旦原语调整
# （例如载荷变长）本地不会跟着动，read_holder() 会静默读到错位的字节。
# 保留这两个名字是因为本模块的既有调用方/文档/用例（`ws_mod.HOLDER_SLOT_*`）在引用。
#
# 【为什么定长且不 truncate（实现期实测踩到的真实缺陷，已写进原语）】
# Windows 的字节区间锁在持锁期间 `truncate()` 会让已持有的区间失效（实测解锁时报
# `[Errno 13] Permission denied`）——那意味着**互斥可能在持有期内被破坏**。
# 故：字节 0 是锁专用哨兵，永不 truncate；身份 JSON 从字节 1 起**定长**写入
# （不足补空格、超出截断），既不需要 truncate，也不会残留上一次更长的内容。
# 身份仅用于**诊断**，截断不影响唯一性判定。
_LOCK_REGION_BYTES = _CP_LOCK_REGION_BYTES
HOLDER_SLOT_OFFSET = _CP_HOLDER_SLOT_OFFSET
HOLDER_SLOT_BYTES = _CP_HOLDER_SLOT_BYTES


class SplitBrainError(RuntimeError):
    """检测到第二个 Watchdog 实例（分裂脑）——**拒绝启动**

    Attributes:
        holder: 当前持有者的诊断信息（pid/host/role/started_at）。
        lock_path: 锁文件路径。
    """

    def __init__(self, message: str, *, holder: Optional[Dict[str, Any]] = None,
                 lock_path: str = "") -> None:
        self.holder = dict(holder or {})
        self.lock_path = str(lock_path or "")
        super().__init__(message)


class WatchdogLockError(RuntimeError):
    """锁文件不可用（路径不可写/平台不支持）"""


# ════════════════════════════════════════════════════════════
#  锁文件路径
# ════════════════════════════════════════════════════════════


def default_lock_path() -> str:
    """锁文件路径（显式 > 环境变量 > 默认）"""
    return os.environ.get(ENV_LOCK_PATH) or DEFAULT_LOCK_PATH


# ════════════════════════════════════════════════════════════
#  持有者信息
# ════════════════════════════════════════════════════════════


@dataclass
class LockHolder:
    """锁持有者身份（**诊断用**，非授权依据）"""

    pid: int
    host: str = ""
    role: str = ROLE_WATCHDOG
    started_at: float = field(default_factory=time.time)
    backend: str = LEASE_BACKEND_SINGLE_HOST
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pid": int(self.pid),
            "host": self.host,
            "role": self.role,
            "started_at": self.started_at,
            "started_iso": datetime.fromtimestamp(self.started_at).isoformat(),
            "backend": self.backend,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LockHolder":
        return cls(
            pid=int(data.get("pid") or -1),
            host=str(data.get("host") or ""),
            role=str(data.get("role") or ROLE_WATCHDOG),
            started_at=float(data.get("started_at") or 0.0),
            backend=str(data.get("backend") or LEASE_BACKEND_SINGLE_HOST),
            note=str(data.get("note") or ""),
        )

    @classmethod
    def current(cls, *, role: str = ROLE_WATCHDOG, note: str = "") -> "LockHolder":
        """本进程身份"""
        try:
            import socket
            host = socket.gethostname()
        except Exception:  # noqa: BLE001 主机名取不到不影响唯一性判定
            host = ""
        return cls(pid=os.getpid(), host=host, role=role, note=note)


def _pid_alive(pid: int) -> bool:
    """pid 是否仍存活（**仅用于诊断**；唯一性由 OS 锁保证）

    Windows：`OpenProcess` 语义不便，改用 `tasklist` 之外的最轻做法——
    用 `os.kill(pid, 0)` 在 POSIX 有效、Windows 会抛；故 Windows 走
    `ctypes` 之外的最简路径：交给 OS 锁判定，这里返回 True（保守，
    即"假设还活着"，避免误判陈旧而放行第二个实例）。
    """
    if pid <= 0:
        return False
    if sys.platform == "win32":
        # 保守：Windows 上不做 pid 探活（易误判），由 OS 锁的持有状态作权威
        return True
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:  # noqa: BLE001
        return True


# ════════════════════════════════════════════════════════════
#  OS 级非阻塞文件锁 → 已收敛到统一原语（TASK-S8-02 步骤 3）
# ════════════════════════════════════════════════════════════
#
# 【本段删掉了什么、为什么（不易）】改写前这里有三个模块级函数
# `_open_lockfile` / `_try_lock` / `_unlock`——它们与
# `agent/env_config_manager.py`、`agent/knowledge/ingest.py` 里的同名逻辑是**手抄**
# 关系（同一套 OS 锁平台分支各写了一到两遍）。现在这些职责全部由
# `agent.utils.cross_process_lock` 承担：
#
#   旧函数              现调用点
#   ------------------  ------------------------------------------------------
#   _open_lockfile()    CrossProcessLock 内部 open_lockfile()（不再由本模块开文件）
#   _try_lock(handle)   CrossProcessLock.try_lock() → 原语 os_try_lock()
#   _unlock(handle)     CrossProcessLock.release() → 原语 os_unlock()
#
# 【为什么删掉而不是留一层转发（简易）】留着 `def _try_lock(h): return os_try_lock(h)`
# 这类纯转发，会让"平台分支到底在哪"这件事继续有两个答案（读代码的人仍要先看本文件
# 的转发层）。本模块对外只保留**语义包装**：非阻塞 → `SplitBrainError`。
# 若将来有外部引用 `watchdog_singleton._open_lockfile`（本仓库内已确认为零引用），
# 直接改用 `agent.utils.cross_process_lock.open_lockfile` 即可。


# ════════════════════════════════════════════════════════════
#  单例守卫
# ════════════════════════════════════════════════════════════

#: 进程内已持有的守卫（同进程重复 acquire 幂等返回同一实例）
_INPROC_GUARD: Dict[str, "WatchdogSingleton"] = {}
_INPROC_LOCK = threading.RLock()


class WatchdogSingleton:
    """单机唯一 Watchdog 守卫（lockfile 强制）

    Usage:
        guard = WatchdogSingleton(lock_path=tmp / "watchdog.lock")
        guard.acquire()             # 第二个实例在此抛 SplitBrainError
        ...
        guard.release()             # 幂等
    """

    def __init__(self, lock_path: Optional[str] = None, *,
                 role: str = ROLE_WATCHDOG,
                 holder: Optional[LockHolder] = None,
                 register_singleton: bool = True) -> None:
        self.lock_path = str(lock_path or default_lock_path())
        self.role = str(role or ROLE_WATCHDOG)
        self._holder = holder
        self._held = False
        self._register_singleton = register_singleton
        self._lock = threading.RLock()
        # 【为什么在 __init__ 就建锁对象（简易）】CrossProcessLock 构造**无副作用**
        # （只是 abspath 一个路径，不建文件、不加锁），真正的文件创建发生在 acquire()
        # 里；提前建好可以让 read_holder()/is_stale() 这些只读诊断路径直接复用同一实例，
        # 且"同一实例"正是原语的重入判定口径（不同实例在本进程内会互斥，见原语注释）。
        #
        # 【为什么把身份作为 holder_info 传进去（不易）】改写前身份是本模块自己
        # `_write_holder()` 写进定长槽的；现在写槽由原语负责，身份必须**交出去**。
        # 这里传 `holder().to_dict()`（含 pid/host/role/backend/started_at/started_iso/
        # note）而不是只传 pid：诊断槽的使用方（被拒的第二个实例、面板、运维）读的是
        # 这些字段，少一个就是行为回退。字段总长实测 ~394B < 512B 槽位，原语的
        # `_fit_payload()` 在超长时会先降级 path 再兜底，不会写出非法 JSON。
        self._cp_lock = CrossProcessLock(
            self.lock_path, name="watchdog_singleton",
            holder_info=self.holder().to_dict(),
        )

    # ── 身份 ──

    @property
    def held(self) -> bool:
        """本实例是否持有锁"""
        return self._held

    def holder(self) -> LockHolder:
        """本实例的持有者身份（惰性构造）"""
        if self._holder is None:
            self._holder = LockHolder.current(role=self.role)
        return self._holder

    # ── 获取 / 释放 ──

    def acquire(self, *, timeout_s: float = 0.0) -> "WatchdogSingleton":
        """获取单例锁（**非阻塞**；已被占用则抛 `SplitBrainError`）

        Args:
            timeout_s: 保留参数（留给 P5 的租约等待）；当前仅接受 0（非阻塞）。
                传 >0 会显式告警——本任务不实现排队语义（单机不应排队，
                第二个 Watchdog 启动本身就是需要人看的异常）。

        Returns:
            self（链式）。

        Raises:
            SplitBrainError: 已有实例持有（**第二个实例被拒**）。
            WatchdogLockError: 锁文件不可用。
        """
        with self._lock:
            if self._held:
                return self  # 同实例重复 acquire 幂等
            if timeout_s and timeout_s > 0:
                logger.warning(
                    "timeout_s=%.1f 被忽略：单机单例守卫为非阻塞语义（第二个实例应被拒，"
                    "而不是排队）", timeout_s,
                )
            # 1) 进程内先查（同进程的第二个 guard 对象 → 复用既有登记）
            if self._register_singleton:
                existing = _INPROC_GUARD.get(self.lock_path)
                if existing is not None and existing is not self and existing.held:
                    raise SplitBrainError(
                        f"同进程已有 Watchdog 守卫持有 {self.lock_path}（pid="
                        f"{existing.holder().pid}）——单机仅允许一个 Watchdog 实例",
                        holder=existing.holder().to_dict(), lock_path=self.lock_path,
                    )
            # 2) 跨进程权威判定：统一原语的**非阻塞**获取
            #
            # 【为什么这里不能用 acquire(timeout>0)（不易）】单机单例守卫要的是
            # "第二个实例被拒"，不是排队——排队会让两个 Watchdog 都"活着"，
            # 正是分裂脑本身。故只用 try_lock()，失败一律翻译成 SplitBrainError。
            path = Path(self.lock_path)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                raise WatchdogLockError(
                    f"锁文件目录不可用 {path.parent}: {type(exc).__name__}: {exc}"
                ) from exc
            try:
                acquired = self._cp_lock.try_lock()
            except LockFileError as exc:
                # 原语的 LockFileError ≡ 本模块的 WatchdogLockError 语义
                # （锁文件不可用）；异常类型必须保持 WatchdogLockError，
                # 故在此翻译而不是把原语异常透出去。
                raise WatchdogLockError(f"锁文件不可用 {path}（{exc}）") from exc
            if not acquired:
                holder = self.read_holder()
                raise SplitBrainError(
                    f"单机已有 Watchdog 实例持有 {self.lock_path}"
                    f"（pid={holder.get('pid') if holder else '未知'}）——"
                    f"分裂脑防护拒绝第二个实例（§4.4 P7.2-16）",
                    holder=holder, lock_path=self.lock_path,
                )
            # 3) 登记进程内单例（身份槽已由原语在加锁成功时写入）
            #
            # 【为什么 _INPROC_GUARD 必须留（不易）】原语的进程内登记是**按路径**的，
            # 它足以拒掉"同进程第二个 guard 对象"；但本模块需要的是更早、更明确的一条
            # 诊断（"单机仅允许一个 Watchdog 实例" + 已有守卫的 holder），且 release()
            # 要能被 reset_watchdog_singleton() 统一清空。故保留这层登记：
            # 它**不是**第二套锁，是最上层的一句"同进程语义说明"。
            self._held = True
            if self._register_singleton:
                _INPROC_GUARD[self.lock_path] = self
            logger.info("Watchdog 单例锁已获取: %s (pid=%d role=%s)",
                        self.lock_path, self.holder().pid, self.role)
            return self

    def release(self) -> bool:
        """释放单例锁（幂等；返回是否真的释放了）"""
        with self._lock:
            if not self._held:
                self._held = False
                return False
            # 原语 release() 幂等且线程亲和（只有属主线程能释放）；
            # 句柄关闭、OS 解锁都在原语内部完成——本模块不再自持句柄。
            self._cp_lock.release()
            self._held = False
            if self._register_singleton and _INPROC_GUARD.get(self.lock_path) is self:
                _INPROC_GUARD.pop(self.lock_path, None)
            logger.info("Watchdog 单例锁已释放: %s", self.lock_path)
            return True

    def __enter__(self) -> "WatchdogSingleton":
        return self.acquire()

    def __exit__(self, *exc: Any) -> None:
        self.release()

    # ── 锁文件内容（诊断） ──

    def read_holder(self) -> Dict[str, Any]:
        """读锁文件里的持有者信息（**仅诊断**；损坏/为空返回 {}）

        【实现期实测的关键点（Windows）】字节区间锁锁住的是**字节**，
        而对已被锁的字节做**读取**同样会被拒（`PermissionError: [Errno 13]`）。
        整文件 `read_bytes()` 会读到字节 0 → 在本进程之外读持有者信息时必然失败
        ——而那正是最需要它的时候（报 `SplitBrainError` 时要说清"谁持有"）。
        故：**先 `seek(HOLDER_SLOT_OFFSET)` 跳过锁字节再读**，绕开被锁区间。

        兼容历史上"整文件就是 JSON"的形态（无哨兵时回退整文件解析）。

        【为什么整段委托给原语（TASK-S8-02 步骤 3，不易）】本方法此前是**第二份**
        "跳锁字节读定长槽 + 回退整文件 JSON"实现（`_read_slot`/`_read_whole`）。
        统一原语里的 `read_holder()` 就是照这套语义写的（哨兵在字节 0、槽在字节 1 起、
        容忍历史整文件 JSON、读不到一律 `{}`、**绝不创建文件**），故这里只做转发；
        两份实现只要有一处漂移（比如一边截断了一边没截断），"谁持有"这句诊断就会
        在最需要它的时候两边说法不一致。
        """
        return self._cp_lock.read_holder()

    # ── 陈旧判定（**显式调用**，不自动回收） ──

    def is_stale(self) -> bool:
        """锁文件是否"陈旧"（存在残留锁文件，但**已无活跃持有者**）

        【判定口径（实现期修正：以 OS 锁为权威）】
        `is_stale()` = "锁文件存在" ∧ "本进程能立刻拿到该文件的 OS 锁"。
        能拿到锁 ⇒ 原持有者进程已消亡（OS 随进程释放锁）⇒ 残留锁文件可安全清理。

        第一版把"pid 已消亡"当**门槛**，导致 Windows 上永远判不出陈旧
        （Windows 不做 pid 探活，`_pid_alive` 保守返回 True）。pid 探活现降级为
        `stale_evidence()` 里的**补充证据**，不再能否决结论——这条判定因此跨平台一致。

        注意语义边界：**本进程自己释放锁之后**，锁文件仍在（带着本进程的 pid），
        且锁可获取 ⇒ 返回 True。这是正确的："残留锁文件、无活跃持有者"。

        【无副作用】本方法及其调用的 `_os_lock_acquirable()` **绝不创建**锁文件
        （`create=False`）——诊断不应改变被诊断对象。

        Returns:
            True = 残留锁文件可安全清理；False = 无锁文件，或仍有活跃持有者。
        """
        if self._held:
            return False
        try:
            if not Path(self.lock_path).exists():
                return False
            return self._os_lock_acquirable()
        except Exception as exc:  # noqa: BLE001 判定失败按"非陈旧"处理（保守）
            logger.warning("陈旧锁判定失败: %s", exc)
            return False

    def stale_evidence(self) -> Dict[str, Any]:
        """陈旧判定的证据明细（诊断用：陈旧的**理由**，不只看结论）

        【先判后探（实现期修正）】第一版把 `lockfile_exists` 先取值、再调
        `is_stale()`，而后者（经 `_os_lock_acquirable()`）会**创建**锁文件——
        于是无锁文件时返回 `{lockfile_exists: false, stale: true}` 自相矛盾。
        现在：① 观测与探测都不创建文件；② `stale` 先算，其余字段随后取值。
        """
        stale = self.is_stale()
        holder = self.read_holder()
        pid = int(holder.get("pid") or -1)
        return {
            "lock_path": self.lock_path,
            "lockfile_exists": Path(self.lock_path).exists(),
            "lockfile_holder": holder,
            "recorded_pid": pid,
            "recorded_pid_alive": _pid_alive(pid) if pid > 0 else False,
            "os_lock_acquirable": (False if self._held else self._os_lock_acquirable()),
            "stale": stale,
            "note": ("Windows 不做 pid 探活（保守 True）；陈旧以「能否拿到 OS 锁」为权威"
                     if sys.platform == "win32" else "POSIX 以 pid 探活 + OS 锁双重证据"),
        }

    def _os_lock_acquirable(self) -> bool:
        """能否立刻拿到 OS 锁（**不创建文件**；探测后立即释放）

        【为什么可以直接用原语的 is_stale()（TASK-S8-02 步骤 3，不易）】
        原语 `is_stale()` 的判定式与这里的旧实现**逐字等价**：
        `未持锁 ∧ 锁文件存在 ∧ 能立刻拿到 OS 锁`（原语内部即 `_os_lock_acquirable()`，
        且同样用 `create=False` 打开、探测后立刻释放，**绝不产生副作用**）。
        改写前这里是本模块自己的一份 open→try→unlock→close 探针，与另外两处是手抄
        关系；现在直接复用公开入口，本模块不再持有任何探测逻辑。
        外层 `self._held` 判断保留：本模块的持有标记比原语实例标记更早生效
        （acquire 里先置位再由原语登记），两层都判不会误报"可获取"。
        """
        if self._held:
            return False
        return self._cp_lock.is_stale()

    def status(self) -> Dict[str, Any]:
        """守卫状态快照（诊断/面板用）"""
        holder = self.read_holder()
        return {
            "lock_path": self.lock_path,
            "role": self.role,
            "held_by_self": self._held,
            "self_pid": self.holder().pid,
            "lockfile_holder": holder,
            "stale": self.is_stale(),
            "backend": LEASE_BACKEND_SINGLE_HOST,
            "cluster_backend_reserved": LEASE_BACKEND_CLUSTER,
        }


# ════════════════════════════════════════════════════════════
#  便捷入口
# ════════════════════════════════════════════════════════════


def acquire_watchdog_singleton(lock_path: Optional[str] = None, *,
                               role: str = ROLE_WATCHDOG) -> WatchdogSingleton:
    """获取（并持有）单机唯一 Watchdog 守卫

    Raises:
        SplitBrainError: 已有实例。
        WatchdogLockError: 锁文件不可用。
    """
    return WatchdogSingleton(lock_path=lock_path, role=role).acquire()


def watchdog_singleton_status(lock_path: Optional[str] = None) -> Dict[str, Any]:
    """只读查询单例状态（**不获取锁**——供面板/运维查看）"""
    guard = WatchdogSingleton(lock_path=lock_path, register_singleton=False)
    status = guard.status()
    status["held_by_inprocess"] = any(
        g.held for g in _INPROC_GUARD.values() if g.lock_path == guard.lock_path)
    return status


def reset_watchdog_singleton() -> None:
    """释放并清空进程内登记（用例隔离）"""
    with _INPROC_LOCK:
        for guard in list(_INPROC_GUARD.values()):
            try:
                guard.release()
            except Exception:  # noqa: BLE001
                pass
        _INPROC_GUARD.clear()


def active_guards() -> Dict[str, "WatchdogSingleton"]:
    """当前进程内登记的守卫快照（诊断用）"""
    with _INPROC_LOCK:
        return dict(_INPROC_GUARD)


__all__ = [
    "DEFAULT_LOCK_PATH", "ENV_LOCK_PATH", "ROLE_WATCHDOG",
    "LEASE_BACKEND_SINGLE_HOST", "LEASE_BACKEND_CLUSTER",
    "SplitBrainError", "WatchdogLockError",
    "LockHolder", "WatchdogSingleton", "default_lock_path",
    "acquire_watchdog_singleton", "watchdog_singleton_status",
    "reset_watchdog_singleton", "active_guards",
]
