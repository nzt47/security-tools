"""统一跨进程锁原语（TASK-S8-02 步骤 2）

【为什么需要"统一"，而不是再写一份】
    云枢此前**同一套原语被手抄了三份**，各自长出了不同的语义与坑：

    ==============================  ============  ==============================
    位置                            锁语义        已知坑
    ==============================  ============  ==============================
    ``agent/env_config_manager.py``  ``LK_LOCK``   阻塞式，**无超时**（进程互等可
                                     （阻塞）      以致死不返回）；``open('a+')``
                                                   下 ``seek`` 对写无效
    ``agent/knowledge/ingest.py``    ``LK_LOCK``   自行拼了 50ms 轮询超时；**没有
                                     （轮询超时）   进程内线程串行化登记**，靠一个
                                                   模块级全局 ``_THREAD_LOCK`` 兜
    ``agent/self_healing/watchdog_   ``LK_NBLCK``  只有"被拒"语义；与上面两者
     singleton.py``                  （非阻塞）    **不可互换**
    ==============================  ==============  =============================

    三份实现共享同一个**底层 OS 原语**（Windows ``msvcrt.locking`` 字节锁 /
    POSIX ``fcntl.flock``），却各自复制了开文件、补哨兵、平台分支、解锁。
    TASK-S8-02 硬约束「严禁复制第 N 份实现」要求**提取为唯一共享工具**，
    再把上述三处**改写为调用本模块**（代码级证据见验收报告 §"不新建第二套锁"）。

【为什么是"非阻塞 + 有限等待"，而不是直接阻塞式 flock】
    阻塞式 ``flock``/``LK_LOCK`` 在**持锁进程被杀**后的表现依赖实现细节；
    更重要的是它让调用方无法表达"等不到就降级"。本模块把两种语义都做成**显式**的：

    - ``try_lock()``     非阻塞：拿不到立刻返回 False（调用方自行退避/降级）；
    - ``locked(t)``      有限等待：轮询至多 t 秒，超时抛 ``LockTimeout``。
    - ``acquire(t)``     ``t<=0`` 等价 ``try_lock``（超时抛 ``LockUnavailable``）。

    底层**始终**使用非阻塞 OS 锁（``LK_NBLCK`` / ``LOCK_EX|LOCK_NB``）做轮询，
    因此"有限等待"是我们自己的循环，不是 OS 的阻塞等待——超时边界完全可预测。

【跨进程 + 跨线程双层（不易）】
    Windows 的 ``msvcrt.locking`` 是**按进程**判定字节区间的：同进程内两个线程
    用**两个不同 fd** 去锁同一区域，第二个**不会**被拒（实测）。因此仅靠 OS 锁
    无法串行化同进程多线程。本模块叠加一层**按路径登记的进程内 RLock**，
    并处理**同线程重入**（RLock 允许，重入时不再重复要 OS 锁，避免自我死锁）。

【崩溃恢复（硬约束 2：持锁进程被杀不得死锁）】
    OS 文件锁随**进程消亡自动释放**，因此"持锁进程被杀"⇒ 下一个等待者立刻拿到锁，
    不存在需要人工清理的残留状态。锁文件本身**永不删除**（删除会引入
    "锁文件 inode 被换掉、旧持有者仍持旧 inode 锁"的静默互斥失效），
    其内容仅作**诊断**（谁持有的、何时），**不作授权依据**。

【可观测（硬约束：等待/冲突/超时/降级计数）】
    ``lock_metrics()`` 返回进程内快照；同时向 ``agent.monitoring.metrics``
    递增计数器（若可用）。计数口径见 ``LockMetrics`` 字段注释——
    没有计数就无法回答"加固了但有没有生效"。

【依赖方向（不破坏 importlinter）】
    本模块**只依赖标准库**（+可选的 metrics 惰性导入）。审计/事件留痕走
    ``notify_hook``，由调用方注入或惰性导入，**避免 utils → audit/observability
    的模块级反向依赖**。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("agent.utils.cross_process_lock")

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: 锁区间字节数：Windows ``msvcrt.locking`` 要求锁定区域 ≥1 字节
LOCK_REGION_BYTES = 1

#: 默认轮询间隔（秒）。5ms 是"进程间握手"的常见量级：足够细以免白等，
#: 又足够粗以免在长等待时把 CPU 打满。
DEFAULT_POLL_INTERVAL = 0.005

#: 默认有限等待上限（秒）
DEFAULT_TIMEOUT = 5.0

#: 锁文件里的**持有者诊断槽**（字节 1 起，定长）。
#:
#: 【为什么定长且不 truncate（沿用 watchdog_singleton 实测结论）】
#: Windows 的 ``msvcrt.locking`` 锁的是**字节区间**；持锁期间 ``truncate()``
#: 会让已持有的区间失效（实测解锁报 ``[Errno 13] Permission denied``），
#: 即**互斥可能在持有期内被破坏**。故：字节 0 是锁专用哨兵，永不 truncate；
#: 身份以定长文本从字节 1 起覆写（不足补空格、超出截断），无需 truncate
#: 也不会残留上一次更长的内容。
#:
#: 【为什么是 512 而不是 256（实现期实测缺陷）】诊断载荷里含**绝对路径**；
#: Windows 上 pytest ``tmp_path`` 形如
#: ``C:\Users\<u>\AppData\Local\Temp\pytest-of-<u>\pytest-<n>\<test>0\guarded.lock``，
#: 加上 json 转义后轻易超过 256 字节 ⇒ 载荷被**从中间截断** ⇒ ``json.loads``
#: 失败 ⇒ ``read_holder()`` 恒返回 ``{}``（诊断在最需要它的时候失效）。
#: 两处修正：槽位放宽到 512，且 ``_fit_payload()`` **保证**截断后仍是合法 JSON。
HOLDER_SLOT_OFFSET = LOCK_REGION_BYTES
HOLDER_SLOT_BYTES = 512

#: **通用元数据槽**（持有者诊断槽之后，定长 512 字节）
#:
#: 【为什么需要一个"给调用方用"的槽（性能，实测）】审计链要判断"我不持锁期间
#: 有没有别的进程写过"——最直接的判据是查被保护文件的大小（``os.stat``，
#: Windows 实测 **0.11ms/次**，占单条 append 总延迟的四成以上）。
#: 但"有没有别的进程动过"其实等价于"有没有别的进程取过这把锁"：**任何写入者都
#: 必须先取锁**。于是让每个持有者在取到锁后把自己的**进程身份 + 链头**写进这个
#: 槽（定位读写、全部落在**已打开的句柄**上，实测约 15µs/次，比 stat 便宜约 7 倍）；
#: 下一次取锁时读到"token 还是我自己的"即可**跳过 stat**，直接信任内存链头。
#:
#: 槽内容由调用方自定义（本模块只负责定长、可覆写、可读回），因此不违反
#: "本模块是唯一锁实现"的约束——它是锁文件的**通用协作槽**，不是第二套锁。
HEAD_SLOT_OFFSET = HOLDER_SLOT_OFFSET + HOLDER_SLOT_BYTES
HEAD_SLOT_BYTES = 512

#: 锁文件诊断区总长度（截断点不得越过它）
_SLOTS_END = HEAD_SLOT_OFFSET + HEAD_SLOT_BYTES
#: 持有者诊断槽末尾（**不写元数据槽时**锁文件的规整长度 = 1 哨兵 + 512 槽）
_HOLDER_SLOT_END = HOLDER_SLOT_OFFSET + HOLDER_SLOT_BYTES

#: 本进程的一次性标识（pid + 启动时刻）。
#:
#: 【为什么不能只用 pid（不易）】操作系统会**复用 pid**：重启后的新进程可能拿到
#: 与上一个持有者相同的 pid，若元数据槽里只记 pid，就会把**陈旧链头**当成自己的
#: 而跳过校验 ⇒ 重复 seq。故用"pid + 进程启动时刻"做 token，复用概率可忽略。
PROCESS_TOKEN = f"{os.getpid()}-{time.time():.6f}"


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class LockError(RuntimeError):
    """跨进程锁异常基类"""


class LockUnavailable(LockError):
    """**非阻塞**获取失败：锁正被其它持有者占用（调用方应退避/降级）

    Attributes:
        path: 锁文件路径。
        holder: 当前持有者的诊断信息（可能为空——读不到不代表没被占）。
        waited_ms: 已等待毫秒数（非阻塞路径恒为 0）。
    """

    def __init__(self, message: str, *, path: str = "",
                 holder: Optional[Dict[str, Any]] = None,
                 waited_ms: float = 0.0) -> None:
        self.path = str(path or "")
        self.holder = dict(holder or {})
        self.waited_ms = float(waited_ms)
        super().__init__(message)


class LockTimeout(LockUnavailable):
    """**有限等待**超时：在 timeout 内未取得锁（显式失败，须留痕）

    继承 ``LockUnavailable`` 是**有意的**：调用方若只关心"没拿到锁"，
    一个 ``except LockUnavailable`` 即可覆盖两种语义，不会漏掉超时分支。
    """


class LockFileError(LockError):
    """锁文件不可用（路径不可写 / 平台不支持 / 打开失败）"""


# ════════════════════════════════════════════════════════════
#  可观测计数
# ════════════════════════════════════════════════════════════


@dataclass
class LockMetrics:
    """锁可观测计数（**进程内**快照）

    Attributes:
        acquired: 成功获取次数（含重入）。
        conflicts: **非阻塞**尝试被拒次数（``try_lock()`` 返回 False）。
        timeouts: ``locked()/acquire(timeout>0)`` 等待超时次数。
        degraded: 调用方在取锁失败后**走了降级路径**的次数（显式上报）。
        waits: 发生过真实等待（等待 > poll_interval）的次数。
        wait_ms_total: 等待毫秒累计（算平均等待）。
        wait_ms_max: 单次最长等待毫秒。
        os_lock_acquires: 真正拿到 **OS 锁**的次数（不含同线程重入）。
        reentrant: 同线程重入次数（未重复要 OS 锁）。
        failures: 锁文件不可用/异常次数。
    """

    acquired: int = 0
    conflicts: int = 0
    timeouts: int = 0
    degraded: int = 0
    waits: int = 0
    wait_ms_total: float = 0.0
    wait_ms_max: float = 0.0
    os_lock_acquires: int = 0
    reentrant: int = 0
    failures: int = 0

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "acquired": int(self.acquired),
            "conflicts": int(self.conflicts),
            "timeouts": int(self.timeouts),
            "degraded": int(self.degraded),
            "waits": int(self.waits),
            "wait_ms_total": round(float(self.wait_ms_total), 4),
            "wait_ms_max": round(float(self.wait_ms_max), 4),
            "os_lock_acquires": int(self.os_lock_acquires),
            "reentrant": int(self.reentrant),
            "failures": int(self.failures),
        }
        data["wait_ms_avg"] = (round(self.wait_ms_total / self.waits, 4)
                              if self.waits else 0.0)
        return data


_METRICS = LockMetrics()
_METRICS_LOCK = threading.Lock()


def _bump(field_name: str, value: float = 1.0) -> None:
    """递增计数（绝不抛：观测失败不能影响加锁路径）"""
    try:
        with _METRICS_LOCK:
            setattr(_METRICS, field_name, getattr(_METRICS, field_name) + value)
    except Exception:  # noqa: BLE001
        pass


def _observe_wait(wait_ms: float, *, poll_interval_ms: float) -> None:
    """记录一次等待（只在真的等过时计入 ``waits``，避免污染平均值）

    【为什么不在热路径上打 metrics 计数器（实测性能缺陷）】第一版每次**成功**
    加锁都调 ``agent.monitoring.metrics.increment_counter``，而该函数内部
    无条件执行 ``logger.debug(f"...")`` —— f-string 是**先求值再丢弃**的，
    即便日志级别关掉也要付格式化代价。审计链每条 append 都要加锁，两处
    metrics 调用（``lock.acquire`` + ``lock.wait_ms``）实测占掉单条延迟的
    **约 0.4ms（近 80%）**。改为：热路径只累加**进程内**整数计数（一次
    ""锁 + 加法），对外指标由 ``publish_lock_metrics()`` **显式**推送。
    """
    try:
        with _METRICS_LOCK:
            if wait_ms > poll_interval_ms:
                _METRICS.waits += 1
                _METRICS.wait_ms_total += float(wait_ms)
                if wait_ms > _METRICS.wait_ms_max:
                    _METRICS.wait_ms_max = float(wait_ms)
    except Exception:  # noqa: BLE001
        pass


def _metric_counter(name: str, value: int = 1) -> None:
    """向 ``agent.monitoring.metrics`` 递增计数器（**惰性导入**，不可用则跳过）

    ⚠️ 仅用于**失败/降级**等低频路径；**不要**放进每次加锁的热路径
    （理由见 ``_observe_wait``）。
    """
    try:
        from agent.monitoring.metrics import increment_counter
        increment_counter(name, int(value))
    except Exception:  # noqa: BLE001 观测不可用绝不影响加锁
        pass


def lock_metrics() -> Dict[str, Any]:
    """当前进程的锁计数快照（可观测入口；运维/面板可查）"""
    with _METRICS_LOCK:
        return _METRICS.to_dict()


#: 上次向 metrics 采集器推送过的计数（用于算增量，避免重复累加）
_PUBLISHED = LockMetrics()
_PUBLISHED_LOCK = threading.Lock()


def publish_lock_metrics() -> Dict[str, Any]:
    """把锁计数的**增量**推送到 ``agent.monitoring.metrics``（显式调用）

    为什么是显式而非自动：见 ``_observe_wait`` 的性能说明。调用点建议是
    健康巡检 / 面板刷新 / 用例断言——都是低频，不在写路径上。
    """
    snapshot = lock_metrics()
    deltas: Dict[str, int] = {}
    with _PUBLISHED_LOCK:
        for field_name in ("acquired", "conflicts", "timeouts", "degraded",
                           "waits", "os_lock_acquires", "reentrant", "failures"):
            current = int(snapshot.get(field_name) or 0)
            previous = int(getattr(_PUBLISHED, field_name, 0) or 0)
            if current > previous:
                deltas[field_name] = current - previous
                setattr(_PUBLISHED, field_name, current)
    for field_name, delta in deltas.items():
        _metric_counter(f"lock.{field_name}", delta)
    return {**snapshot, "published_deltas": deltas}


def reset_lock_metrics() -> None:
    """清零锁计数（**测试专用**：让用例能断言增量而非绝对值）"""
    global _METRICS
    with _METRICS_LOCK:
        _METRICS = LockMetrics()


# ════════════════════════════════════════════════════════════
#  降级留痕（审计/事件）
# ════════════════════════════════════════════════════════════

#: 降级留痕钩子：``(action, detail_dict) -> None``。
#: 默认实现惰性导入审计门面 + 事件层（best-effort）；调用方可覆盖（用例注入探针）。
_NOTIFY_HOOK: Optional[Callable[[str, Dict[str, Any]], None]] = None

#: 留痕在途标记（线程局部）：切断"取锁失败 → 写审计 → 又取锁失败"的递归
_NOTIFY_INFLIGHT = threading.local()

#: 降级留痕的审计 action 前缀（审计链里搜 ``lock.`` 即可查到全部锁事件）
AUDIT_ACTION_LOCK_DEGRADED = "lock.degraded"
AUDIT_ACTION_LOCK_TIMEOUT = "lock.timeout"
AUDIT_ACTION_LOCK_CONFLICT = "lock.conflict"

#: 事件类型：``lock.contention``（复用 events 的 `payload` 承载细节）
EVENT_LOCK_CONTENTION = "lock.contention"


def set_notify_hook(hook: Optional[Callable[[str, Dict[str, Any]], None]]) -> None:
    """设置降级留痕钩子（None = 恢复默认；**测试专用**）"""
    global _NOTIFY_HOOK
    _NOTIFY_HOOK = hook


def _default_notify(action: str, detail: Dict[str, Any]) -> None:
    """默认留痕：审计链 + 事件层（**两条都 best-effort，绝不抛出**）

    为什么两个通道都要走（硬约束「锁超时须显式失败 + 留痕」）：
    - **审计链**是防篡改台账，回答"谁在什么时候因为锁拿不到而降级了"；
    - **事件层**是结构化治理事件流，供面板/告警按 ``type`` 聚合。
    两者互为交叉证据：任一通道不可用时另一条仍在，不出现"无痕"。
    """
    payload = {k: v for k, v in dict(detail or {}).items() if v is not None}
    try:
        # 【实测缺陷：模块级入口名写错会被 except 吞掉】初版写
        # `get_audit_facade()`——**该函数不存在**（真实入口是模块级 `record()`
        # 或 `get_audit()`）。因为整块被 `except Exception` 包着，错误只在 mypy
        # 里露头，运行时表现为"锁降级**从不留痕**"，恰好违反本任务
        # 「锁超时须显式失败 + 审计/事件留痕」的硬约束。教训：留痕路径必须有
        # **直接断言真实落痕**的用例（见 test_degradation_is_actually_audited）。
        from agent.audit import facade as _audit_facade
        _audit_facade.record(action, actor="system",
                             subject=str(payload.get("lock_path") or ""),
                             payload=payload, source="system")
    except Exception as exc:  # noqa: BLE001 审计不可用不阻断
        logger.warning("锁降级留痕（审计）失败: %s", exc)
    try:
        from agent.observability.events import emit
        emit(EVENT_LOCK_CONTENTION,
             {"action": action, **payload}, actor="auto")
    except Exception as exc:  # noqa: BLE001 事件不可用不阻断
        logger.warning("锁降级留痕（事件）失败: %s", exc)


def notify_degraded(action: str, detail: Dict[str, Any]) -> None:
    """上报一次锁降级（**显式留痕**；不允许静默）

    【防递归（实现期必须处理的一环）】默认留痕会写**审计链**，而审计链本身也要
    取跨进程锁。于是可能出现"取锁失败 → 留痕 → 写审计 → 又取锁失败 → 再留痕"
    的回路。用一个**线程局部**的在途标记切断：留痕过程中再次触发的留痕只计数、
    不再向外写（审计链自己那条失败会由审计链的 best-effort 计数兜住），
    从而既不静默、也不递归。
    """
    _bump("degraded")
    _metric_counter("lock.degraded")
    if getattr(_NOTIFY_INFLIGHT, "active", False):
        logger.debug("锁留痕在途，跳过嵌套留痕（防递归）: %s", action)
        return
    hook = _NOTIFY_HOOK or _default_notify
    _NOTIFY_INFLIGHT.active = True
    try:
        hook(action, dict(detail or {}))
    except Exception as exc:  # noqa: BLE001
        logger.warning("锁降级留痕钩子异常: %s", exc)
    finally:
        _NOTIFY_INFLIGHT.active = False


#: 冲突留痕的**冷却窗口**（秒）。
#:
#: 【为什么冲突要冷却而超时不要（不易）】超时是**终止性显式失败**——每个超时都
#: 意味着一次"这次真没写成"，必须逐条留痕。而冲突是**退避重试循环**的正常中间态：
#: 50ms 退避重试 5s 就是 100 次冲突，逐条写审计会把审计链冲垮，反而掩盖真实问题。
#: 故冲突按 ``(路径, 进程)`` 去重、每窗口最多留痕一条——**不静默**（首次一定留痕），
#: 也不放大。计数（``lock_metrics()``）则**不做**冷却，每一次冲突都如实计入。
CONFLICT_NOTIFY_COOLDOWN_S = 60.0

_CONFLICT_NOTIFIED: Dict[str, float] = {}
_CONFLICT_NOTIFIED_LOCK = threading.Lock()


def _should_notify_conflict(key: str) -> bool:
    """冲突留痕冷却判定（True = 本窗口内首次，应当留痕）"""
    now = time.monotonic()
    with _CONFLICT_NOTIFIED_LOCK:
        last = _CONFLICT_NOTIFIED.get(key)
        if last is not None and (now - last) < CONFLICT_NOTIFY_COOLDOWN_S:
            return False
        _CONFLICT_NOTIFIED[key] = now
        # 防无界增长（锁路径数量有限，这里只是兜底）
        if len(_CONFLICT_NOTIFIED) > 4096:  # pragma: no cover - 极端兜底
            for stale in sorted(_CONFLICT_NOTIFIED,
                                key=lambda k: _CONFLICT_NOTIFIED[k])[:2048]:
                _CONFLICT_NOTIFIED.pop(stale, None)
        return True


def reset_conflict_cooldown() -> None:
    """清空冲突留痕冷却表（**测试专用**）"""
    with _CONFLICT_NOTIFIED_LOCK:
        _CONFLICT_NOTIFIED.clear()


# ════════════════════════════════════════════════════════════
#  底层 OS 锁（★ 唯一实现，三处既有调用方均改用本模块）
# ════════════════════════════════════════════════════════════


def open_lockfile(path: Any, *, create: bool) -> Optional[Any]:
    """以**可定位覆写**的方式打开锁文件（返回二进制句柄；失败返回 None）

    【为什么不是 ``open(path, "a+")``（env_config_manager 的历史写法）】
    追加模式下**所有写入都被强制落到文件末尾**，``seek()`` 对写无效：
    ``seek(1)`` 会被忽略 ⇒ 身份被**追加**到 EOF，文件每次 acquire 增长，
    重新获取后读到的是**上一个**持有者。改用 ``os.open(O_RDWR[|O_CREAT])`` +
    ``os.fdopen(fd, "r+b")``：``O_CREAT`` **不截断**（并发创建不会互相清空），
    ``r+b`` 允许任意位置覆写 ⇒ 诊断槽被真正重写。

    Args:
        create: True = 不存在则创建（获取路径）；
            False = 只读探测（``read_holder()``/``is_stale()`` 等诊断路径，
            **绝不产生副作用**——诊断不应改变被诊断对象）。
    """
    flags = os.O_RDWR | (os.O_CREAT if create else 0)
    try:
        fd = os.open(os.fspath(path), flags, 0o600)
    except OSError:
        return None
    try:
        handle = os.fdopen(fd, "r+b", buffering=0)
    except OSError:
        os.close(fd)
        return None
    if create:
        # 锁区间要求 ≥1 字节：仅对**空文件**补哨兵（不触碰已有内容）
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.seek(0)
                handle.write(b"\0" * LOCK_REGION_BYTES)
        except OSError:  # noqa: BLE001 补哨兵失败留给后续锁调用报错
            pass
    return handle


def os_try_lock(handle: Any) -> bool:
    """尝试**非阻塞**获取 OS 文件锁（成功 True / 已被占用 False）

    平台差异（**唯一分支点**，其余代码平台无关）：
    - Windows：``msvcrt.locking(LK_NBLCK, 1)`` —— 锁**字节区间**，按进程判定；
    - POSIX：``fcntl.flock(LOCK_EX | LOCK_NB)`` —— 锁**整个文件**，按打开文件描述。
    """
    try:
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, LOCK_REGION_BYTES)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def os_unlock(handle: Any) -> None:
    """释放 OS 文件锁（失败只告警——句柄关闭时 OS 也会释放）"""
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, LOCK_REGION_BYTES)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except Exception as exc:  # noqa: BLE001
        logger.warning("锁文件解锁失败（句柄关闭时 OS 会释放）: %s", exc)


# ════════════════════════════════════════════════════════════
#  进程内登记（跨线程串行化 + 同线程重入）
# ════════════════════════════════════════════════════════════


class _InProcessSlot:
    """某锁路径的进程内状态：一个 RLock + 持有深度 + 属主线程

    Why 用 RLock（不易）：``append()`` 这类路径常在**同一线程**上嵌套调用
    （如审计链在锁内调用写入工具，写入工具又要同一把锁）。若用普通 ``Lock``
    会**自我死锁**；RLock 允许重入，配合深度计数即可在重入时**跳过 OS 锁**
    （本进程已持有，再去要一次会被自己的 OS 锁拒掉）。
    """

    __slots__ = ("rlock", "depth", "owner", "handle", "instance", "idle_since",
                 "dir_ready", "handle_ino", "verified_at")

    def __init__(self) -> None:
        self.rlock = threading.RLock()
        #: **本路径**的获取深度（跨实例累计）；归零时才真正释放 OS 锁
        self.depth = 0
        self.owner: Optional[int] = None
        self.handle: Optional[Any] = None
        #: 真正持有 OS 锁的那个 ``CrossProcessLock`` 实例（重入判定用）
        self.instance: Optional[Any] = None
        #: 空闲起始时刻（用于**有界**回收空闲句柄，见 ``trim_idle_handles``）
        self.idle_since: float = 0.0
        #: 父目录已确认存在（省掉每次加锁的 ``os.makedirs`` 探测）
        self.dir_ready: bool = False
        #: 句柄对应的 inode/文件索引（复用前校验"锁文件没被换掉"）
        self.handle_ino: int = 0
        #: 上次做"句柄↔路径"校验的时刻（节流，见 ``_handle_ok``）
        self.verified_at: float = 0.0


_INPROC: Dict[str, _InProcessSlot] = {}
_INPROC_GUARD = threading.Lock()

#: 进程内**保留**的空闲锁文件句柄上限。
#:
#: 【为什么保留句柄（性能，实测）】每次加锁都 ``os.open``+``os.fdopen``+
#: ``close`` 实测要 **0.26ms**（其中 ``fdopen`` 建 Python 文件对象占 ~0.2ms），
#: 而纯 ``msvcrt.locking`` + ``LKU NLCK`` 只要 **0.012ms**——相差 20 倍。
#: 审计链每次 append 都要加锁，这个差价直接把单条 append 的 p50 从 0.035ms
#: 顶到 1.7ms。故**复用**句柄，只在真正需要时关闭。
#:
#: 【为什么要上限（不易）】Windows 上打开着的句柄会阻止文件删除；若无上限，
#: 用例里成千上万个临时锁文件会让句柄耗尽、并让临时目录清理失败。
#: 故只保留最近使用的 ``MAX_IDLE_HANDLES`` 个，其余按 LRU 关闭。
MAX_IDLE_HANDLES = 128

#: 空闲句柄的 LRU 顺序（最近使用在末尾）
_IDLE_ORDER: List[str] = []

#: "句柄↔路径"一致性校验的最小间隔（秒）。见 ``CrossProcessLock._handle_ok``：
#: 逐次校验要两次 stat（Windows 各 ~0.13ms），会拖垮每次加锁都在的写路径。
HANDLE_VERIFY_INTERVAL_S = 5.0


def _slot_for(key: str) -> _InProcessSlot:
    with _INPROC_GUARD:
        slot = _INPROC.get(key)
        if slot is None:
            slot = _InProcessSlot()
            _INPROC[key] = slot
        return slot


def _mark_active(key: str) -> None:
    """把某路径标记为"刚被使用"（LRU 队尾）"""
    with _INPROC_GUARD:
        if key in _IDLE_ORDER:
            _IDLE_ORDER.remove(key)
        _IDLE_ORDER.append(key)


def _flush_handle(slot: _InProcessSlot) -> None:
    """关闭并清空某槽的锁文件句柄（幂等）"""
    handle = slot.handle
    slot.handle = None
    slot.handle_ino = 0
    if handle is not None:
        try:
            handle.close()
        except Exception:  # noqa: BLE001
            pass


def trim_idle_handles(keep: int = MAX_IDLE_HANDLES) -> int:
    """关闭最久未使用的空闲句柄，使保留量不超过 ``keep``；返回关闭数

    【只关空闲的】正在持有 OS 锁的槽（``depth > 0``）**绝不**关闭——
    关掉句柄等于释放锁。
    """
    closed = 0
    with _INPROC_GUARD:
        candidates = [k for k in list(_IDLE_ORDER)
                      if (k in _INPROC and _INPROC[k].depth <= 0
                          and _INPROC[k].handle is not None)]
        excess = len(candidates) - max(int(keep), 0)
        for key in candidates[:max(excess, 0)]:
            _flush_handle(_INPROC[key])
            if key in _IDLE_ORDER:
                _IDLE_ORDER.remove(key)
            closed += 1
    return closed


def close_idle_handles(path: Optional[str] = None) -> int:
    """关闭空闲锁句柄（``path`` 给定时只关该路径）；返回关闭数

    用例收尾/运维需要释放 Windows 文件占用时调用。**不**影响任何正在持有的锁。
    """
    closed = 0
    target = os.path.abspath(os.fspath(path)) if path else None
    with _INPROC_GUARD:
        keys = [target] if target is not None else list(_INPROC)
        for key in keys:
            slot = _INPROC.get(key)
            if slot is None or slot.depth > 0 or slot.handle is None:
                continue
            _flush_handle(slot)
            if key in _IDLE_ORDER:
                _IDLE_ORDER.remove(key)
            closed += 1
    return closed


# ════════════════════════════════════════════════════════════
#  CrossProcessLock
# ════════════════════════════════════════════════════════════


class CrossProcessLock:
    """**唯一**的跨进程锁（文件锁 + 进程内线程串行化）

    Usage::

        lock = CrossProcessLock("data/audit/audit_chain.db.lock")

        if lock.try_lock():                 # 非阻塞
            try:
                ...
            finally:
                lock.release()

        with lock.locked(timeout=2.0):      # 有限等待（超时抛 LockTimeout）
            ...

        with lock.acquire(timeout=0):       # 同 try，超时抛 LockUnavailable
            ...

    Args:
        path: 锁文件路径（**必须显式传或由调用方从配置解析**；用例用临时目录）。
        name: 诊断名（出现在留痕与日志里，便于在审计链中定位是哪把锁）。
        poll_interval: 轮询间隔秒（仅有限等待路径使用）。
        reentrant: 是否允许同线程重入（默认 True）。设为 False 时同线程二次获取
            会被进程内 RLock 自身拒绝……故实际仍用 RLock，此参数仅控制**是否
            复用已有 OS 锁句柄**，False 表示重入也要求一次真实 OS 锁
            （用于"重入即 bug"的场景）。
        holder_info: 额外写入诊断槽的字段（如 ``{"db_path": ...}``）。
    """

    def __init__(self, path: Any, *, name: str = "",
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 reentrant: bool = True,
                 holder_info: Optional[Dict[str, Any]] = None) -> None:
        self.path = os.path.abspath(os.fspath(path))
        self.name = str(name or os.path.basename(self.path))
        self.poll_interval = max(float(poll_interval), 0.0005)
        self.reentrant = bool(reentrant)
        self.holder_info: Dict[str, Any] = dict(holder_info or {})
        self._slot_key = self.path
        self._held = False
        self._os_held = False
        #: **本实例**的获取深度（与 ``slot.depth`` 区分：slot 是路径级累计，
        #: 实例级深度保证"谁加几次谁放几次"，避免用 reentrant 的返回值
        #: 误判本实例是否仍持有 ⇒ 提前 unlock 造成**锁泄漏**
        #: （实现期实测：v1 用 `slot.depth > 1` 判定重入并立即置
        #: `_held=False`，外层随后 `release()` 直接短路返回，
        #: OS 锁永不释放 —— 本进程内互斥失效、句柄泄漏）。
        self._depth = 0
        #: 上次写诊断槽的时刻（**节流**：诊断槽写入含 json 编码 + truncate，
        #: 实测让单次加锁从 ~35µs 涨到 ~1ms；诊断只需"最近"，不必每次刷新）
        self._holder_written_at = 0.0

    #: 诊断槽刷新间隔（秒）。取 1s：运维看到的信息最多滞后 1s，而高频加锁路径
    #: 只在窗口外付出一次写槽成本。
    HOLDER_REFRESH_INTERVAL_S = 1.0

    # ── 诊断 ──

    def __repr__(self) -> str:  # pragma: no cover - 诊断用
        return f"<CrossProcessLock {self.name} path={self.path} held={self._held}>"

    @property
    def held(self) -> bool:
        """本实例当前是否持有锁"""
        return self._held

    def read_holder(self) -> Dict[str, Any]:
        """读诊断槽里的持有者信息（**仅诊断**；损坏/为空返回 ``{}``）

        兼容历史上"整文件就是 JSON"的形态（无哨兵时回退整文件解析）。
        """
        for reader in (self._read_slot, self._read_whole):
            try:
                parsed = reader()
            except Exception:  # noqa: BLE001 读不到不是错误路径
                continue
            if parsed:
                return parsed
        return {}

    def _read_slot(self) -> Dict[str, Any]:
        """只读锁字节之后的定长诊断槽（**不创建文件**、不触碰被锁区间）

        Windows 上对已被锁的字节做**读取**同样会被拒（``PermissionError``），
        故必须 ``seek(HOLDER_SLOT_OFFSET)`` 跳过锁字节。
        """
        import json
        handle = open_lockfile(self.path, create=False)
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
        """整文件解析（锁已释放 / 历史形态兜底）

        【为什么要容忍 offset=0 失败】Windows 上对**已锁字节**做读取会被拒
        （``PermissionError``），而字节 0 在**本进程持有锁期间**恰恰是被锁的。
        故先试 offset=0（历史"整文件即 JSON"形态），失败再退到 offset=1
        （带哨兵的现行形态）——两段都在 try 里，任一段失败只影响该段。
        """
        import json
        for offset in (0, HOLDER_SLOT_OFFSET):
            try:
                with open(self.path, "rb") as fh:
                    fh.seek(offset)
                    raw = fh.read(HOLDER_SLOT_BYTES + HOLDER_SLOT_OFFSET)
            except OSError:
                continue
            text = raw.decode("utf-8", errors="ignore").strip("\0 \r\n\t")
            if not text:
                continue
            start = text.find("{")
            if start > 0:
                text = text[start:]
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                continue
            if isinstance(parsed, dict):
                return parsed
        return {}

    def is_stale(self) -> bool:
        """锁文件是否"陈旧"（存在残留锁文件，但**已无活跃持有者**）

        判定口径：``锁文件存在`` ∧ ``本进程能立刻拿到该文件的 OS 锁``。
        能拿到锁 ⇒ 原持有者进程已消亡（OS 随进程释放锁）⇒ 残留文件可安全清理。

        **无副作用**：本方法绝不创建锁文件——诊断不应改变被诊断对象。
        """
        if self._held:
            return False
        try:
            if not os.path.exists(self.path):
                return False
            return self._os_lock_acquirable()
        except Exception as exc:  # noqa: BLE001 判定失败按"非陈旧"（保守）
            logger.warning("陈旧锁判定失败: %s", exc)
            return False

    def _os_lock_acquirable(self) -> bool:
        """能否立刻拿到 OS 锁（**不创建文件**；探测后立即释放）"""
        if self._held:
            return False
        handle = open_lockfile(self.path, create=False)
        if handle is None:
            return False
        try:
            acquired = os_try_lock(handle)
            if acquired:
                os_unlock(handle)
            return acquired
        finally:
            handle.close()

    def status(self) -> Dict[str, Any]:
        """锁状态快照（诊断/面板）"""
        return {
            "name": self.name,
            "path": self.path,
            "held_by_self": self._held,
            "os_lock_held": self._os_held,
            "holder": self.read_holder(),
            "stale": self.is_stale(),
            "pid": os.getpid(),
        }

    # ── 获取 / 释放 ──

    def try_lock(self) -> bool:
        """**非阻塞**获取；拿不到立刻返回 False

        失败时**计数**（``lock_metrics()['conflicts']``）并按冷却窗口留痕一条
        ``lock.conflict`` —— 返回 False 必须是**可观测**的，否则调用方的降级
        路径就成了新的静默点。
        """
        acquired, waited_ms = self._acquire(0.0, blocking=False)
        if acquired:
            return True
        self._note_failure(kind="conflict", timeout=0.0, waited_ms=waited_ms)
        return False

    def _note_failure(self, *, kind: str, timeout: float, waited_ms: float) -> None:
        """统一的失败处理：计数 + 留痕（``kind`` = ``conflict`` / ``timeout``）"""
        holder = self.read_holder()
        detail = {"lock_name": self.name, "lock_path": self.path,
                  "holder": holder, "waited_ms": round(waited_ms, 3),
                  "timeout_s": float(timeout), "pid": os.getpid(),
                  **self.holder_info}
        if kind == "timeout":
            _bump("timeouts")
            _metric_counter("lock.timeout")
            # 超时是终止性失败：**逐条**留痕（见 CONFLICT_NOTIFY_COOLDOWN_S 注释）
            notify_degraded(AUDIT_ACTION_LOCK_TIMEOUT, detail)
            return
        _bump("conflicts")
        _metric_counter("lock.conflict")
        if _should_notify_conflict(f"{self.path}|{os.getpid()}"):
            notify_degraded(AUDIT_ACTION_LOCK_CONFLICT, detail)

    def acquire(self, timeout: float = 0.0) -> "CrossProcessLock":
        """获取锁；``timeout <= 0`` 等价 ``try_lock``，否则**有限等待**

        Returns:
            self（链式）。

        Raises:
            LockTimeout: 有限等待超时（**已留痕**）。
            LockUnavailable: 非阻塞尝试被拒（**已留痕**，受冷却窗口约束）。
            LockFileError: 锁文件不可用。
        """
        acquired, waited_ms = self._acquire(float(timeout), blocking=True)
        if acquired:
            return self
        holder = self.read_holder()
        if float(timeout) > 0:
            self._note_failure(kind="timeout", timeout=float(timeout),
                               waited_ms=waited_ms)
            raise LockTimeout(
                f"跨进程锁等待超时（{timeout}s）: {self.name} @ {self.path}"
                f"（持有者 pid={holder.get('pid', '未知')}）",
                path=self.path, holder=holder, waited_ms=waited_ms)
        self._note_failure(kind="conflict", timeout=0.0, waited_ms=waited_ms)
        raise LockUnavailable(
            f"跨进程锁被占用（非阻塞）: {self.name} @ {self.path}"
            f"（持有者 pid={holder.get('pid', '未知')}）",
            path=self.path, holder=holder, waited_ms=waited_ms)

    def _acquire(self, timeout: float, *, blocking: bool) -> Any:
        """统一获取实现（返回 ``(是否成功, 已等待毫秒)``）

        顺序（**先进程内、后跨进程**，与 watchdog_singleton 的层次一致，从便宜到昂贵）：
        1. 进程内 RLock（跨线程串行化；同线程重入直接返回，**不重复要 OS 锁**）；
        2. OS 非阻塞文件锁（跨进程唯一权威判定）。
        """
        slot = _slot_for(self._slot_key)
        deadline = time.monotonic() + max(float(timeout), 0.0)
        t0 = time.monotonic()

        # ── 1) 进程内 RLock ──
        if blocking and timeout > 0:
            remaining = max(deadline - time.monotonic(), 0.0)
            got_thread = slot.rlock.acquire(timeout=remaining)
        else:
            got_thread = slot.rlock.acquire(blocking=False)
        if not got_thread:
            return False, (time.monotonic() - t0) * 1000.0

        try:
            # ── 重入：**同一个实例**在同一线程再次获取 ⇒ 只加深度 ──
            #
            # 【为什么只认"同实例"，而不是"同线程"（实现期修正，安全性关键）】
            # 第一版把"同线程"当作重入 ⇒ 同线程里**另一个** ``CrossProcessLock``
            # 实例（哪怕 name 不同、保护的对象不同）也会"拿到锁"。这会让
            # "单写者/单例"型守卫在**本进程内**静默失效：
            #   A = CrossProcessLock(p); A.try_lock()      # True
            #   B = CrossProcessLock(p); B.try_lock()      # 也 True ← 互斥被绕过
            # 语义上锁的持有者是**实例**（与 ``threading.RLock`` 一致：不同 RLock
            # 之间不互通）。改为按实例判定后，B 会被正确拒掉，而"同一实例嵌套
            # 加锁"（``append()`` 内再调同样持锁的工具）仍然不会自我死锁。
            same_owner = (slot.depth > 0 and slot.owner == threading.get_ident()
                          and slot.instance is self)
            if slot.depth > 0 and slot.owner == threading.get_ident() \
                    and not same_owner:
                slot.rlock.release()
                return False, (time.monotonic() - t0) * 1000.0
            if same_owner and not self.reentrant:
                slot.rlock.release()
                return False, (time.monotonic() - t0) * 1000.0
            if same_owner:
                slot.depth += 1
                self._depth += 1
                self._held = True
                self._os_held = False
                _bump("acquired")
                _bump("reentrant")
                return True, (time.monotonic() - t0) * 1000.0
            # ── 2) OS 非阻塞锁（轮询至 deadline） ──
            while True:
                handle = self._open_and_lock(slot)
                if handle is not None:
                    slot.handle = handle
                    slot.depth = 1
                    slot.owner = threading.get_ident()
                    slot.instance = self
                    self._depth = 1
                    self._held = True
                    self._os_held = True
                    waited_ms = (time.monotonic() - t0) * 1000.0
                    _bump("acquired")
                    _bump("os_lock_acquires")
                    _observe_wait(waited_ms,
                                  poll_interval_ms=self.poll_interval * 1000.0)
                    return True, waited_ms
                if not blocking or time.monotonic() >= deadline:
                    break
                time.sleep(min(self.poll_interval,
                               max(deadline - time.monotonic(), 0.0005)))
        except LockFileError:
            slot.rlock.release()
            raise
        except Exception as exc:  # noqa: BLE001
            slot.rlock.release()
            _bump("failures")
            raise LockFileError(f"获取跨进程锁失败: {self.name} @ {self.path}: {exc}") from exc

        slot.rlock.release()
        waited_ms = (time.monotonic() - t0) * 1000.0
        _observe_wait(waited_ms, poll_interval_ms=self.poll_interval * 1000.0)
        return False, waited_ms

    def _open_and_lock(self, slot: _InProcessSlot) -> Optional[Any]:
        """打开（必要时创建）锁文件并尝试非阻塞 OS 锁；成功返回句柄

        【句柄复用（性能）】优先复用该路径上已保留的句柄（见
        ``MAX_IDLE_HANDLES`` 的实测说明），仅在"没有句柄"或"锁文件被换掉"
        时重新打开。换掉检测用 ``st_ino``（Python 在 Windows 上也填充文件索引）：
        若锁文件被外部删除重建，复用旧句柄会锁在**已废弃的 inode** 上，
        互斥会静默失效——这是必须防的。
        """
        if not slot.dir_ready:
            try:
                parent = os.path.dirname(self.path)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                slot.dir_ready = True
            except Exception as exc:  # noqa: BLE001
                raise LockFileError(
                    f"锁文件目录不可用 {os.path.dirname(self.path)}: "
                    f"{type(exc).__name__}: {exc}") from exc

        handle = slot.handle
        if handle is not None and not self._handle_ok(slot, handle):
            _flush_handle(slot)          # 锁文件被换掉 → 丢弃旧句柄
            handle = None
        if handle is None:
            handle = open_lockfile(self.path, create=True)
            if handle is None:
                raise LockFileError(f"锁文件不可用 {self.path}（无法创建/打开）")
            slot.handle = handle
            slot.handle_ino = self._path_ino()
            _mark_active(self._slot_key)
            trim_idle_handles()

        if os_try_lock(handle):
            now = time.monotonic()
            if (now - self._holder_written_at) >= self.HOLDER_REFRESH_INTERVAL_S:
                self._write_holder(handle)
                self._holder_written_at = now
            return handle
        return None

    def _path_ino(self) -> int:
        """锁文件当前的文件索引（取不到返回 0 ⇒ 放弃"换掉检测"而非误判）"""
        try:
            return int(os.stat(self.path).st_ino)
        except OSError:
            return 0

    def _handle_ok(self, slot: _InProcessSlot, handle: Any) -> bool:
        """复用句柄前校验"锁文件没被换掉"（**节流**：见下）

        【为什么节流（实测性能）】校验要 ``os.stat`` 路径 + ``os.fstat`` 句柄，
        在 Windows 上各约 **0.13ms**；而本路径在审计链上**每次 append** 都走。
        第一版逐次校验，直接把单条 append 的 p50 抬到 ~0.47ms。
        改为**每 ``HANDLE_VERIFY_INTERVAL_S`` 秒最多校验一次**：
        被替换的风险是"运维手工删锁文件"这类低频事件，秒级检出足够；
        真正高频的并发场景由 OS 锁本身保证互斥，不依赖本校验。
        """
        now = time.monotonic()
        if (now - slot.verified_at) < HANDLE_VERIFY_INTERVAL_S:
            return True
        slot.verified_at = now
        if not slot.handle_ino:
            return True
        current = self._path_ino()
        if not current:
            return True          # 路径已不存在 ⇒ 交给后续加锁路径报错
        try:
            return int(os.fstat(handle.fileno()).st_ino) == current
        except OSError:
            return False

    def release(self) -> bool:
        """释放锁（幂等；返回是否真的释放了**本层**）

        【非属主释放（实现期修正）】只有**持有锁的那个线程**才能释放：跨线程
        ``release()`` 会绕过 RLock 的属主检查去动 OS 锁与 ``slot.handle``，
        破坏外层持有者的状态（实测可复现"外层仍以为持有、实际已解锁"）。
        故此处以 ``slot.owner == threading.get_ident()`` 为门槛，非属主一律不动作。
        """
        slot = _slot_for(self._slot_key)
        if self._depth <= 0:
            self._held = False
            return False
        if slot.owner is not None and slot.owner != threading.get_ident():
            logger.warning("非属主线程尝试释放跨进程锁（已忽略）: %s", self.name)
            return False
        try:
            # 本实例放一层；**只有路径级深度归零**才真正动 OS 锁与句柄
            self._depth -= 1
            slot.depth = max(int(slot.depth) - 1, 0)
            self._held = self._depth > 0
            if slot.depth > 0:
                return True
            handle = slot.handle
            if handle is not None:
                os_unlock(handle)
            # 【不解锁即弃】句柄**保留**在该路径的槽里供下次复用（见
            # MAX_IDLE_HANDLES 的实测说明：开/关句柄 0.26ms vs 纯锁操作 0.012ms）。
            # 淘汰由 trim_idle_handles()/close_idle_handles() 负责，且**只关空闲**槽。
            slot.depth = 0
            slot.owner = None
            slot.instance = None
            slot.idle_since = time.monotonic()
            self._os_held = False
            trim_idle_handles()
            return True
        finally:
            try:
                slot.rlock.release()
            except RuntimeError:  # 非本线程释放/已释放：幂等吞掉
                pass

    def _fit_payload(self, payload: Dict[str, Any]) -> bytes:
        """把诊断载荷压进定长槽，且**保证截断后仍是合法 JSON**

        【为什么不能直接按字节切片（实现期实测缺陷）】``json.dumps(...)[:512]``
        会在**字符串中间**切断——留下 ``{"path": "C:\\Users\\Admi`` 这种
        非法 JSON，``json.loads`` 必败 ⇒ ``read_holder()`` 恒为 ``{}``。
        正确做法是**先按字段降级**，直到编码后能装下：

        1. 原样；
        2. ``path`` 省略为 ``…\\<basename>``（路径是诊断里最长的字段）；
        3. 丢掉调用方附加的 ``holder_info``；
        4. 只留最小集合 ``{pid, name, truncated}``。

        任一级装得下就返回；四级都装不下（理论不可能，最小集合 <64B）时
        退化为最小集合的**强制**编码。解码端因此**永远**拿到 dict。
        """
        import json

        def encode(data: Dict[str, Any]) -> bytes:
            return json.dumps(data, ensure_ascii=False).encode("utf-8")

        candidates: List[Dict[str, Any]] = [
            payload,
            {**payload, "path": os.path.basename(self.path), "path_full_in": "name"},
            {k: v for k, v in payload.items()
             if k not in ("path",) and k not in self.holder_info},
            {"pid": payload.get("pid"), "name": payload.get("name"),
             "truncated": True},
        ]
        for candidate in candidates:
            encoded = encode(candidate)
            if len(encoded) <= HOLDER_SLOT_BYTES:
                return encoded
        return encode({"pid": payload.get("pid"), "truncated": True})

    def _write_holder(self, handle: Any) -> None:
        """把本进程身份写入**定长诊断槽**（真正的位置覆写，非追加）

        字节 0 的锁哨兵保持不动；诊断 JSON 从字节 1 起定长写入（补空格），
        末尾 ``truncate`` 清掉历史残留（截断点远在锁区间之上，不影响字节 0 的
        锁有效性——这是**不能用** ``truncate(0)`` 的原因）。
        """
        try:
            import socket
            host = socket.gethostname()
        except Exception:  # noqa: BLE001
            host = ""
        payload = {
            "pid": os.getpid(),
            "host": host,
            "name": self.name,
            "path": self.path,
            "acquired_at": time.time(),
            "thread": threading.get_ident(),
            **self.holder_info,
        }
        encoded = self._fit_payload(payload)
        if len(encoded) < HOLDER_SLOT_BYTES:
            encoded = encoded + b" " * (HOLDER_SLOT_BYTES - len(encoded))
        handle.seek(HOLDER_SLOT_OFFSET)
        handle.write(encoded)
        try:
            # 【截断点：**保住**已存在的元数据槽（实测缺陷）】早期版本硬截断到
            # `_SLOTS_END`(1025)，于是"没人用元数据槽"的场景（如 watchdog 单例）
            # 也会得到 1025 字节的文件，破坏了既有 `1 + 512` 布局约定
            # （`test_watchdog_singleton` 的 3 条断言实测失败：1025 != 513）。
            # 反之若永远截断到 513，又会**削掉** `write_meta` 写过协作槽。
            # 正确口径：只保留"文件当前已有的深度"，但不短于持有者槽末尾。
            try:
                current = os.fstat(handle.fileno()).st_size
            except OSError:
                current = _HOLDER_SLOT_END
            handle.truncate(max(_HOLDER_SLOT_END, min(current, _SLOTS_END)))
        except OSError:  # noqa: BLE001 截断失败不影响诊断可读性
            pass

    # ── 通用元数据槽（调用方协作；须在持锁期间使用） ──

    def _held_handle(self) -> Optional[Any]:
        """当前**被本线程持有**的锁文件句柄（未持有返回 None）"""
        if self._depth <= 0:
            return None
        slot = _INPROC.get(self._slot_key)
        return None if slot is None else slot.handle

    def read_meta(self) -> Optional[Dict[str, Any]]:
        """读回元数据槽（未持有锁 / 未写过 / 损坏 → None）

        定位读**只走已打开的句柄**，不做任何 stat/查库——这是它在写路径上
        可用的原因（见 ``HEAD_SLOT_OFFSET`` 的实测说明）。
        """
        import json
        handle = self._held_handle()
        if handle is None:
            return None
        try:
            handle.seek(HEAD_SLOT_OFFSET)
            raw = handle.read(HEAD_SLOT_BYTES)
        except OSError:
            return None
        text = raw.decode("utf-8", errors="ignore").strip("\0 \r\n\t")
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def write_meta(self, data: Dict[str, Any]) -> bool:
        """把元数据写入槽（**须持锁**；定长补空格，不 truncate 锁字节区）"""
        import json
        handle = self._held_handle()
        if handle is None:
            return False
        try:
            encoded = json.dumps(data, ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8")
            if len(encoded) > HEAD_SLOT_BYTES:
                return False
            encoded = encoded + b" " * (HEAD_SLOT_BYTES - len(encoded))
            handle.seek(HEAD_SLOT_OFFSET)
            handle.write(encoded)
            return True
        except OSError:
            return False

    # ── 上下文管理 ──

    def locked(self, timeout: float = DEFAULT_TIMEOUT,
               *, on_timeout: str = "raise") -> Any:
        """有限等待上下文管理器

        Args:
            timeout: 最长等待秒数（0 等价非阻塞）。
            on_timeout: 超时行为——
                ``"raise"``（默认）抛 ``LockTimeout``（**显式失败**）；
                ``"degrade"`` 返回一个 ``acquired=False`` 的上下文
                （调用方在 ``as`` 变量上判 ``.acquired`` 走降级路径），
                仍然**留痕**（转由调用方决定记什么）。

        Returns:
            上下文管理器；``with ... as handle`` 的 ``handle`` 有
            ``acquired`` / ``lock`` 两个属性。
        """
        return _LockContext(self, float(timeout), on_timeout=on_timeout)

    def __enter__(self) -> "CrossProcessLock":
        self.acquire(0.0)
        return self

    def __exit__(self, *exc: Any) -> None:
        self.release()


class _LockContext:
    """``CrossProcessLock.locked()`` 的上下文实现（支持 ``degrade`` 语义）"""

    def __init__(self, lock: CrossProcessLock, timeout: float,
                 *, on_timeout: str = "raise") -> None:
        if on_timeout not in ("raise", "degrade"):
            raise ValueError(f"非法 on_timeout: {on_timeout}（raise / degrade）")
        self.lock = lock
        self.timeout = float(timeout)
        self.on_timeout = on_timeout
        self.acquired = False

    def __enter__(self) -> "_LockContext":
        if self.on_timeout == "raise":
            self.lock.acquire(self.timeout)
            self.acquired = True
            return self
        try:
            self.lock.acquire(self.timeout)
            self.acquired = True
        except LockUnavailable:
            self.acquired = False
        return self

    def __exit__(self, *exc: Any) -> None:
        # 显式返回 None：``locked()`` **不吞异常**（吞掉会让调用方以为成功）
        if self.acquired:
            self.lock.release()


# ════════════════════════════════════════════════════════════
#  便捷入口
# ════════════════════════════════════════════════════════════


def cross_process_lock(path: Any, *, name: str = "",
                       **kwargs: Any) -> CrossProcessLock:
    """构造跨进程锁（工厂：便于调用方统一打点）"""
    return CrossProcessLock(path, name=name, **kwargs)


def lock_path_for(target: Any, *, suffix: str = ".lock") -> str:
    """由"被保护的资源路径"派生锁文件路径

    【为什么锁文件必须与被保护文件**不同**（env_config_manager 的既有结论）】
    对文件本体加锁时，若持有者用 ``rename`` 替换该文件（原子写惯用法），
    锁就落到了**被淘汰的 inode** 上——新文件立刻可以被别人锁走，互斥静默失效。
    故**始终**锁一个独立的 ``<target>.lock``。
    """
    return str(os.fspath(target)) + suffix


__all__ = [
    "LOCK_REGION_BYTES", "DEFAULT_POLL_INTERVAL", "DEFAULT_TIMEOUT",
    "HOLDER_SLOT_OFFSET", "HOLDER_SLOT_BYTES",
    "HEAD_SLOT_OFFSET", "HEAD_SLOT_BYTES", "PROCESS_TOKEN",
    "LockError", "LockUnavailable", "LockTimeout", "LockFileError",
    "LockMetrics", "lock_metrics", "reset_lock_metrics", "publish_lock_metrics",
    "CONFLICT_NOTIFY_COOLDOWN_S", "reset_conflict_cooldown",
    "AUDIT_ACTION_LOCK_DEGRADED", "AUDIT_ACTION_LOCK_TIMEOUT",
    "AUDIT_ACTION_LOCK_CONFLICT", "EVENT_LOCK_CONTENTION",
    "set_notify_hook", "notify_degraded",
    "open_lockfile", "os_try_lock", "os_unlock",
    "MAX_IDLE_HANDLES", "trim_idle_handles", "close_idle_handles",
    "CrossProcessLock", "cross_process_lock", "lock_path_for",
]
