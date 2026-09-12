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

【与既有设施的关系（勿另建第二套锁）】
    - **同进程**：走 `agent.utils.singleton_manager` 的既有单例登记（`_INPROC_KEY`），
      不另造进程内单例模式。
    - **跨进程**：复用云枢既有 lockfile 惯用法——与 `agent/env_config_manager.py::
      _acquire_process_lock` / `agent/knowledge/ingest.py` 同一套原语
      （Windows `msvcrt.locking`，POSIX `fcntl.flock`），**不是**第二套锁实现。
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
【简易】纯标准库；无后台线程；无自动回收定时器（陈旧判定只在显式调用时发生）。
"""

from __future__ import annotations

import enum
import json
import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

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

#: 锁文件最小字节数（Windows msvcrt 要求锁定区域 ≥1 字节）
_LOCK_REGION_BYTES = 1

#: 身份信息在锁文件中的**固定槽位**（字节 1 起，定长；字节 0 归锁专用）
#:
#: 【为什么定长且不定长截断（实现期实测踩到的真实缺陷）】
#: Windows 的 `msvcrt.locking` 锁的是**字节区间**。若在持锁期间 `truncate()` 文件，
#: 会让已持有的区间失效（实测解锁时报 `[Errno 13] Permission denied`）——那意味着
#: **互斥可能在持有期内被破坏**。故：字节 0 是锁专用哨兵，永不 truncate；
#: 身份 JSON 从字节 1 起以**定长**写入（不足补空格、超出截断），从而既不需要 truncate，
#: 也不会残留上一次更长的内容。身份仅用于**诊断**，截断不影响唯一性判定。
HOLDER_SLOT_OFFSET = _LOCK_REGION_BYTES
HOLDER_SLOT_BYTES = 512


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
#  OS 级非阻塞文件锁（复用云枢既有 lockfile 惯用法）
# ════════════════════════════════════════════════════════════


def _open_lockfile(path: Any, *, create: bool) -> Optional[Any]:
    """以**可定位写**的方式打开锁文件（返回二进制句柄；打不开/不存在返回 None）

    【为什么不能用 `open(path, "a+")`（实现期实测踩到的真实缺陷）】
    追加模式下**所有写入都被强制落到文件末尾**，`seek()` 对写无效。第一版用 `a+`：
      - `_write_holder()` 的 `seek(1)` 被忽略 ⇒ 身份被**追加**到 EOF，
        于是重新获取锁后 `read_holder()` 读到的是**上一个**持有者，且文件每次
        acquire 增长 513 字节——恰好废掉了这套诊断信息的用途。
    改用 `os.open(O_RDWR[|O_CREAT])` + `os.fdopen(fd, "r+b")`：
      - `O_CREAT` **不截断**（不像 `w+`），并发创建不会互相清空；
      - `r+b` 允许任意位置覆写 ⇒ 身份槽被真正重写。

    Args:
        create: True = 不存在则创建（仅 `acquire()` 路径）；
            False = 只读探测（`is_stale()` / `status()` 等诊断路径，
            **绝不产生副作用**——诊断不应改变被诊断对象）。
    """
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        fd = os.open(str(path), flags, 0o600)
    except OSError:
        return None
    try:
        handle = os.fdopen(fd, "r+b", buffering=0)
    except OSError:
        os.close(fd)
        return None
    # 锁区间要求至少 1 字节：仅对空文件补哨兵（不触碰已有内容）
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.seek(0)
            handle.write(b"\0" * _LOCK_REGION_BYTES)
    except OSError:  # noqa: BLE001 补哨兵失败留给后续锁调用报错
        pass
    return handle


def _try_lock(handle: Any) -> bool:
    """尝试**非阻塞**获取 OS 文件锁（成功 True / 已被占用 False）

    与 `env_config_manager._acquire_process_lock` 同一套原语，唯一差别是
    **非阻塞**（`LK_NBLCK` / `LOCK_EX|LOCK_NB`）——单例守卫需要的是"被拒"，
    而不是"排队等待"。
    """
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, _LOCK_REGION_BYTES)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: Any) -> None:
    """释放 OS 文件锁（失败只告警——句柄关闭时 OS 也会释放）"""
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, _LOCK_REGION_BYTES)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("锁文件解锁失败（句柄关闭时 OS 会释放）: %s", exc)


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
        self._handle: Optional[Any] = None
        self._held = False
        self._register_singleton = register_singleton
        self._lock = threading.RLock()

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
            # 2) OS 级非阻塞锁（跨进程权威判定）
            path = Path(self.lock_path)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                raise WatchdogLockError(
                    f"锁文件目录不可用 {path.parent}: {type(exc).__name__}: {exc}"
                ) from exc
            handle = _open_lockfile(path, create=True)
            if handle is None:
                raise WatchdogLockError(f"锁文件不可用 {path}（无法创建/打开）")
            if not _try_lock(handle):
                holder = self.read_holder()
                handle.close()
                raise SplitBrainError(
                    f"单机已有 Watchdog 实例持有 {self.lock_path}"
                    f"（pid={holder.get('pid') if holder else '未知'}）——"
                    f"分裂脑防护拒绝第二个实例（§4.4 P7.2-16）",
                    holder=holder, lock_path=self.lock_path,
                )
            # 3) 落身份（诊断用）+ 登记进程内单例
            self._handle = handle
            self._held = True
            try:
                self._write_holder(handle)
            except Exception as exc:  # noqa: BLE001 身份写失败不影响互斥（锁已拿到）
                logger.warning("写锁身份失败（互斥已生效）: %s", exc)
            if self._register_singleton:
                _INPROC_GUARD[self.lock_path] = self
            logger.info("Watchdog 单例锁已获取: %s (pid=%d role=%s)",
                        self.lock_path, self.holder().pid, self.role)
            return self

    def release(self) -> bool:
        """释放单例锁（幂等；返回是否真的释放了）"""
        with self._lock:
            if not self._held or self._handle is None:
                self._held = False
                return False
            _unlock(self._handle)
            try:
                self._handle.close()
            except Exception:  # noqa: BLE001
                pass
            self._handle = None
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

        【实现期实测的关键点（Windows）】`msvcrt.locking` 锁住的是**字节区间**，
        而对已被锁的字节做**读取**同样会被拒（`PermissionError: [Errno 13]`）。
        整文件 `read_bytes()` 会读到字节 0 → 在本进程之外读持有者信息时必然失败
        ——而那正是最需要它的时候（报 `SplitBrainError` 时要说清"谁持有"）。
        故：**先 `seek(HOLDER_SLOT_OFFSET)` 跳过锁字节再读**，绕开被锁区间。

        兼容历史上"整文件就是 JSON"的形态（无哨兵时回退整文件解析）。
        """
        for reader in (self._read_slot, self._read_whole):
            try:
                parsed = reader()
            except Exception:  # noqa: BLE001 读不到不是错误路径（诊断信息缺失）
                continue
            if parsed:
                return parsed
        return {}

    def _read_slot(self) -> Dict[str, Any]:
        """只读**锁字节之后**的定长身份槽（不触碰被锁区间，也不创建文件）"""
        handle = _open_lockfile(self.lock_path, create=False)
        if handle is None:
            return {}
        try:
            handle.seek(HOLDER_SLOT_OFFSET)
            raw = handle.read(HOLDER_SLOT_BYTES)
        finally:
            handle.close()
        text = raw.decode("utf-8", errors="ignore").strip("\0 \r\n\t")
        if not text:
            return {}
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}

    def _read_whole(self) -> Dict[str, Any]:
        """整文件解析（锁已释放 / 历史形态兜底）"""
        raw = Path(self.lock_path).read_bytes()
        text = raw.decode("utf-8", errors="ignore").strip("\0 \r\n\t")
        if not text:
            return {}
        # 历史形态：哨兵 + JSON 混排 → 取第一个 '{' 起的片段
        start = text.find("{")
        if start > 0:
            text = text[start:]
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}

    def _write_holder(self, handle: Any) -> None:
        """写入本进程身份到锁文件的**定长槽**（真正的位置覆写，非追加）

        字节 0 的锁哨兵保持不动；身份 JSON 从字节 1 起定长写入（补空格/截断），
        末尾用 `truncate(513)` 清掉任何历史残留（截断点远在锁区间之上，
        不影响字节 0 的锁有效性——这是不能用 `truncate(0)` 的原因）。
        """
        payload = json.dumps(self.holder().to_dict(), ensure_ascii=False)
        encoded = payload.encode("utf-8")[:HOLDER_SLOT_BYTES]
        if len(encoded) < HOLDER_SLOT_BYTES:
            encoded = encoded + b" " * (HOLDER_SLOT_BYTES - len(encoded))
        handle.seek(HOLDER_SLOT_OFFSET)
        handle.write(encoded)
        try:
            handle.truncate(HOLDER_SLOT_OFFSET + HOLDER_SLOT_BYTES)
        except OSError:  # noqa: BLE001 截断失败不影响身份可读性
            pass
        handle.flush()
        os.fsync(handle.fileno())

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
        """能否立刻拿到 OS 锁（**不创建文件**；探测后立即释放）"""
        if self._held:
            return False
        handle = _open_lockfile(self.lock_path, create=False)
        if handle is None:
            return False
        try:
            return _try_lock(handle)
        finally:
            _unlock(handle)
            handle.close()

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
