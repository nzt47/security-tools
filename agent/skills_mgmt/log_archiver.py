"""评审-评估日志按日归档 + 跨进程安全的 JSONL 追加原语（TASK-S8-02 加固）

把评估事件 / 人工复核审计这类 JSONL 按 ts 的日期分档：
- 当日记录保留在原文件；
- 历史记录（ts 早于今天）移入 `<stem>-YYYYMMDD<ext>` 归档文件（追加模式）。
幂等：进程内按"路径→日期"记忆已归档，重复调用/轮询廉价；
文件缺失/无历史行时零操作。

术语纪律（TASK-S0-01）：云枢旧 digest（评审语义）已更名 review/assess——
事件文件新名 data/skills_assessment_events.jsonl，旧名只读兼容（见下）。

【TASK-S8-02 加固：为什么"整读 → 整写"必须改掉】
    原实现是 `p.read_text()` … `p.write_text()`，**既无跨进程锁也无原子性**：

    ====================================  ==========================================
    并发场景                                原实现的后果
    ====================================  ==========================================
    两个进程同时归档                        后写者用**自己读到的旧快照**覆盖前者结果
                                            ⇒ 前者的归档行**从此不存在**（静默丢失）
    归档的同时另一进程在追加                追加落在**即将被替换掉的旧 inode** 上
                                            ⇒ `os.replace` 之后这行**静默消失**
    进程内 `_ARCHIVED` 记忆                 只覆盖"本进程当天已归档"，对上述场景
                                            **毫无保护**，反而提供假安全感
    ====================================  ==========================================

    本模块现在的纪律（五条，缺一不可）：

    1. **归档的 read-modify-write 全程持跨进程锁**：锁 `<path>.lock`（
       `agent.utils.cross_process_lock` 的统一原语），绝不对被归档文件本体加锁
       ——本体会在原子写里被 `os.replace` 替换，锁会落到**旧 inode** 上，
       互斥随即静默失效（`lock_path_for()` 的既有结论）。
    2. **活动文件的重写原子化**：同目录临时文件 + `os.replace`（同盘才原子）。
    3. **先写分片、后替换活动文件**：崩在中间只可能产生**重复**（归档不删语义下
       的良性偏差：读取端还能看到记录），绝不产生**丢失**。原顺序是"先把活动文件
       截断成当日行、再写分片"，崩在中间 = 那批历史记录**永久消失**。
    4. **拿不到锁 ⇒ 本轮跳过归档**（返回零结果 + 计数 + 留痕），绝不无锁改写；
       也不写 `_ARCHIVED`（让下一次调用还有机会补做）。
    5. `append_jsonl_locked()` 提供"持锁 + **单次 `os.write`**"的 JSONL 追加原语，
       专供各分片写入方（评估事件 / 复核审计）使用，消除多进程撕行。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from agent.utils.cross_process_lock import (
    CrossProcessLock,
    LockError,
    lock_path_for,
)

logger = logging.getLogger(__name__)

# 进程内归档记忆：{str(resolved_path): "YYYY-MM-DD"}
_ARCHIVED: Dict[str, str] = {}

# 评估事件文件名（新名为主；旧名 skills_digest_events.jsonl 为评审语义旧名，
# ≤1 minor 只读兼容：首用迁移 copy + 读取兜底，不删除旧档）
ASSESSMENT_EVENTS_BASENAME = "skills_assessment_events.jsonl"
LEGACY_DIGEST_EVENTS_BASENAME = "skills_digest_events.jsonl"

# ════════════════════════════════════════════════════════════
#  配置开关（环境变量直读，沿用既有 CP_* 风格）
#  【为什么直读 env 而不进 observability_config】该模块由别的任务拥有；
#  且本模块被 skills_mgmt / observability 两侧共用，直读可避免新的耦合边。
# ════════════════════════════════════════════════════════════

#: 归档锁总开关（"0" 关闭 ⇒ 退回无锁归档：**仅供测试/应急**，会丢数据）
ENV_ARCHIVE_LOCK_ENABLED = "CP_ARCHIVE_LOCK_ENABLED"
#: 归档锁有限等待上限（秒）。归档是 RMW，持锁时间天然比"追加一行"长，
#: 因此默认给得比事件写入（2s）宽；仍**有限**，不会无限阻塞调用方。
ENV_ARCHIVE_LOCK_TIMEOUT_SEC = "CP_ARCHIVE_LOCK_TIMEOUT_SEC"

DEFAULT_ARCHIVE_LOCK_TIMEOUT_SEC = 5.0

_FALSEY = ("0", "false", "no", "off", "")


def _env_flag(name: str, default: str = "1") -> bool:
    """环境变量布尔（沿用 events.py 的口径：0/false/no/off/空 = 关）"""
    return str(os.getenv(name, default)).strip().lower() not in _FALSEY


def _env_float(name: str, default: float) -> float:
    """环境变量浮点（非法值回落默认，**不抛**：配置坏值不该打断归档）"""
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return float(default)
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning("%s 非法（%r），回落默认 %s", name, raw, default)
        return float(default)


def lock_enabled() -> bool:
    """跨进程锁是否启用（`CP_ARCHIVE_LOCK_ENABLED`，默认开）"""
    return _env_flag(ENV_ARCHIVE_LOCK_ENABLED, "1")


def lock_timeout_sec() -> float:
    """归档锁有限等待上限（`CP_ARCHIVE_LOCK_TIMEOUT_SEC`，默认 5.0s）"""
    return max(0.0, _env_float(ENV_ARCHIVE_LOCK_TIMEOUT_SEC,
                               DEFAULT_ARCHIVE_LOCK_TIMEOUT_SEC))


# ════════════════════════════════════════════════════════════
#  可观测计数（加固了但不知道有没有生效 = 没加固）
# ════════════════════════════════════════════════════════════

_ARCHIVE_STATS: Dict[str, int] = {
    "archive_runs": 0,          # 真正执行了 read-modify-write 的次数
    "archive_lock_skips": 0,    # 拿不到锁 ⇒ 本轮跳过归档（已留痕）
    "archive_write_failures": 0,  # 写分片/替换活动文件失败（未写记忆，可重试）
    "append_locked": 0,         # 持锁追加成功
    "append_unlocked": 0,       # 降级：无锁追加（已计数 + 告警）
    "append_failures": 0,       # 追加本身失败（OS 错误）
}
_ARCHIVE_STATS_LOCK = threading.Lock()


def _bump(name: str, value: int = 1) -> None:
    """递增计数（绝不抛：观测不能成为新的故障点）"""
    try:
        with _ARCHIVE_STATS_LOCK:
            _ARCHIVE_STATS[name] = int(_ARCHIVE_STATS.get(name, 0)) + int(value)
    except Exception:  # noqa: BLE001
        pass


def archive_stats() -> Dict[str, int]:
    """JSONL 归档/追加的进程内计数快照（运维可查）"""
    with _ARCHIVE_STATS_LOCK:
        return dict(_ARCHIVE_STATS)


def reset_archive_stats() -> None:
    """清零计数（**测试专用**：让用例断言增量而非绝对值）"""
    with _ARCHIVE_STATS_LOCK:
        for key in _ARCHIVE_STATS:
            _ARCHIVE_STATS[key] = 0


# ════════════════════════════════════════════════════════════
#  锁路径 + 底层原语（★ 与 events.py 必须同规则派生）
# ════════════════════════════════════════════════════════════


def jsonl_lock_path(target: Path | str) -> str:
    """由 JSONL 目标路径派生的**锁文件**路径（`<resolved>.lock`）

    【为什么必须与 events.py 用同一规则】追加方与归档方要互斥，就必须落在
    **同一个锁文件**上；两侧各写一遍派生逻辑 ⇒ 一旦有一侧改了 resolve/拼接
    方式，互斥会**静默失效**（表现为"加了锁还是撕行"）。故：
    `tests/unit/test_events_write_hardening.py::test_lock_path_rule_matches_archiver`
    以用例锁死两侧同值。
    """
    return lock_path_for(os.fspath(Path(target).resolve()))


def _raw_append_bytes(path: Path, data: bytes) -> None:
    """**单次** `os.write` 追加（`O_WRONLY|O_CREAT|O_APPEND`）

    【为什么不用 `open(path, "a").write()`】文本层带缓冲，一行可能被拆成多次
    系统调用，撕行窗口更大；显式 fd + `os.write` 是"一次调用一次写"，
    配合跨进程锁后多进程也不会交错。

    【为什么不用 `os.makedirs`】沿用 events.py 的既有教训：`os.makedirs` 是
    模块级全局函数，会被既有单测 `patch('…os.makedirs')` 命中并污染其调用计数
    断言。统一用 `Path.mkdir`。
    """
    parent = path.parent
    if str(parent):
        parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(os.fspath(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        _write_all(fd, data)
    finally:
        os.close(fd)


def _write_all(fd: int, data: bytes) -> None:
    """把整块数据写进 fd（`os.write` 允许短写，必须循环到写完）"""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:  # pragma: no cover - 理论不可达（磁盘满会抛 OSError）
            raise OSError(f"os.write 返回 {written}（未写入任何字节）")
        view = view[written:]


def _atomic_write_lines(path: Path, lines: Sequence[str]) -> None:
    """原子替换 `path` 的内容（同目录临时文件 + `os.replace`）

    【为什么临时文件必须同目录】`os.replace` 只在**同一文件系统/卷**内原子；
    跨卷会退化成 copy + delete（非原子），恰好丢掉了原子性这个目的。

    【为什么要 fsync】`os.replace` 保证"读者要么看到旧文件、要么看到新文件"，
    不保证数据已落盘。审计类数据宁可慢一点（归档每天一次，成本可忽略），
    也不接受"替换看起来成功了、断电后内容为空"。
    """
    data = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        fd = os.open(os.fspath(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            _write_all(fd, data)
            try:
                os.fsync(fd)
            except OSError as e:  # noqa: BLE001 某些文件系统/CI 不支持 fsync
                logger.debug("[LogArchive] fsync 失败（忽略）%s: %s", tmp.name, e)
        finally:
            os.close(fd)
        os.replace(os.fspath(tmp), os.fspath(path))
    except BaseException:
        # 失败必须清掉半成品：残留 tmp 会被误当数据文件，也会污染目录扫描
        try:
            os.unlink(os.fspath(tmp))
        except OSError:
            pass
        raise


# ════════════════════════════════════════════════════════════
#  跨进程安全的 JSONL 追加（供评估事件 / 复核审计分片使用）
# ════════════════════════════════════════════════════════════


def append_jsonl_locked(path: Path | str, line: str, *,
                        timeout: Optional[float] = None,
                        lock: Optional[bool] = None) -> bool:
    """跨进程安全地追加**一行** JSONL（整行一次 `os.write`，不撕行）

    【降级设计：拿不到锁时"照样写"，而不是"丢弃"（TASK-S8-02 硬约束
    「降级路径不静默丢数据」）】
        本函数服务的是**低频审计分片**（评估事件 / 复核豁免），没有常驻对象
        可以在"下一次成功取锁"时排空缓冲（见 `events.EventStore` 的排队式降级），
        因此取锁失败时选择**无锁追加**：

        - 数据**不丢**（最坏情况是极小概率地与其它进程交错，或与并发归档竞争）；
        - 失败**不静默**：`archive_stats()['append_unlocked']` 计数 +
          限流告警日志 + 锁原语自身的冲突/超时留痕（审计链 + 事件流）；
        - 全程**不阻塞调用方**超过 `timeout`（默认 5s），审计写入绝不拖死发布流程。

    Args:
        path: 目标 JSONL 路径。
        line: 单行内容（自动补换行；已带换行则不重复补）。
        timeout: 有限等待上限秒（None ⇒ `lock_timeout_sec()`）。
        lock: True/False 强制开关（None ⇒ `lock_enabled()`）。

    Returns:
        是否**在持锁状态下**写入（False = 走了降级无锁路径，已计数留痕）。
    """
    target = Path(path)
    data = _encode_line(line)
    use_lock = lock_enabled() if lock is None else bool(lock)
    wait = lock_timeout_sec() if timeout is None else max(0.0, float(timeout))
    held = False
    if use_lock:
        guard = CrossProcessLock(jsonl_lock_path(target),
                                 name=f"jsonl:{target.name}",
                                 holder_info={"who": "append_jsonl_locked"})
        try:
            guard.acquire(wait)
            held = True
        except LockError as exc:
            _note_lock_degraded("append", target, exc)
    try:
        _raw_append_bytes(target, data)
    except OSError:
        _bump("append_failures")
        raise
    finally:
        # 【必须在 finally 里释放】写成"紧随其后 release()"会在 `_raw_append_bytes`
        # 抛错时**泄漏锁**（实现期实测：一个线程泄漏后，其余线程只能等到 5s 超时，
        # 240 次追加退化成 ~20 分钟——"看似卡死"）。
        if held:
            guard.release()
    _bump("append_locked" if held else "append_unlocked")
    return held


def _encode_line(line: str) -> bytes:
    """行 → 待写字节（保证恰有一个结尾换行）"""
    text = str(line)
    if not text.endswith("\n"):
        text += "\n"
    return text.encode("utf-8")


def _note_lock_degraded(where: str, target: Path, exc: BaseException) -> None:
    """锁不可用 → 告警（**绝不静默**；计数由各调用点显式 bump，避免重复计）

    锁原语自身已向审计链与事件流留痕（`notify_degraded`，含冷却去重）；
    这里补的是"本模块视角"的可读上下文与日志，两边互为交叉证据。
    """
    logger.warning("[LogArchive] 跨进程锁不可用（%s），走降级路径 %s: %s",
                   where, target.name, exc)


# ════════════════════════════════════════════════════════════
#  路径
# ════════════════════════════════════════════════════════════


def repo_data_dir() -> Path:
    """仓库根 data/ 目录（与 store/service 的落盘位置一致，绝对路径）。"""
    return Path(__file__).resolve().parent.parent.parent / "data"


def active_events_file() -> Path:
    """评估事件活动文件（新名为主）。

    兼容策略：新文件不存在而旧名 live 文件存在时，一次性 copy 旧内容到新文件
    （旧文件保留只读兼容，供仍读旧名的旧版本使用；≤1 minor 后随旧 API 移除）。
    """
    new_p = repo_data_dir() / ASSESSMENT_EVENTS_BASENAME
    old_p = repo_data_dir() / LEGACY_DIGEST_EVENTS_BASENAME
    if not new_p.exists() and old_p.exists():
        try:
            repo_data_dir().mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old_p, new_p)
            logger.info("[LogArchive] 评估事件文件迁移 %s → %s（旧名保留只读兼容）",
                        old_p.name, new_p.name)
        except OSError as e:  # noqa: BLE001 迁移失败不阻断：读取端会兜底旧名
            logger.warning("[LogArchive] 事件文件迁移失败 %s: %s", old_p.name, e)
    return new_p


def events_files() -> Dict[str, Path]:
    """当前可用的评估事件 live 文件（primary 新名 + legacy 旧名，供读取/清理）。"""
    return {
        "primary": active_events_file(),
        "legacy": repo_data_dir() / LEGACY_DIGEST_EVENTS_BASENAME,
    }


def _ts_day(line: str) -> Optional[str]:
    """从 JSONL 行的 ts 字段取 YYYY-MM-DD；无法解析返回 None。"""
    try:
        rec = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    ts = str(rec.get("ts", "") or "")
    return ts[:10] if len(ts) >= 10 else None


# ════════════════════════════════════════════════════════════
#  按日归档（TASK-S8-02：加锁 + 原子 + 先分片后替换）
# ════════════════════════════════════════════════════════════


def archive_daily_file(path: Path | str) -> dict:
    """把 path（JSONL）中的历史行按日归档到同目录 `<stem>-YYYYMMDD<ext>`。

    Args:
        path: 待归档的 JSONL 活动文件（**显式传入**；生产调用方传
            `EventStore.path` / `active_events_file()` / `review_gate._audit_file()`）。

    Returns:
        `{"archived": 当天归档行总数, "files": [归档文件列表], "today": 今日行数}`；
        拿不到跨进程锁时**本轮跳过**并返回全零（已计数 + 已留痕，且**不写记忆**，
        下一次调用仍可补做）。

    【不变式（调用方依赖，勿改）】
        - 公开签名与返回形状不变（`EventStore._maybe_archive` / skills 侧同上）；
        - 进程内 `_ARCHIVED` 记忆语义不变（同一天同路径只做一次）；
        - **归档不删**：历史行只被"搬家"到分片，任何情况下都不被丢弃；
        - 分片命名固定 `<stem>-YYYYMMDD<ext>`（**不是** `_candidate_files` 需要的
          点号形态——那是 `DecisionLog` 自家的事，不在本函数职责内）。
    """
    p = Path(path).resolve()
    if not p.exists():
        return {"archived": 0, "files": [], "today": 0}
    today = date.today().isoformat()
    if _ARCHIVED.get(str(p)) == today:
        return {"archived": 0, "files": [], "today": 0}

    guard: Optional[CrossProcessLock] = None
    held = False
    if lock_enabled():
        guard = CrossProcessLock(jsonl_lock_path(p), name=f"jsonl:{p.name}",
                                 holder_info={"who": "archive_daily_file"})
        try:
            guard.acquire(lock_timeout_sec())
            held = True
        except LockError as exc:
            _note_lock_degraded("archive", p, exc)
            _bump("archive_lock_skips")
            # 记忆**不写**：让下一次调用还有机会补做本轮归档
            return {"archived": 0, "files": [], "today": 0}
    try:
        return _archive_under_lock(p, today)
    finally:
        if held and guard is not None:
            guard.release()


def _archive_under_lock(p: Path, today: str) -> dict:
    """持锁执行 read-modify-write（调用方保证已持 `<p>.lock`）"""
    try:
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        logger.warning("[LogArchive] 读取失败 %s: %s", p, e)
        return {"archived": 0, "files": [], "today": 0}

    today_lines: List[str] = []
    buckets: Dict[str, List[str]] = {}
    moved = 0
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        day = _ts_day(line)
        if day is None or day >= today:
            today_lines.append(line)
        else:
            buckets.setdefault(day, []).append(line)
            moved += 1

    if not buckets:
        _ARCHIVED[str(p)] = today
        return {"archived": 0, "files": [], "today": len(today_lines)}

    files: List[str] = []
    _bump("archive_runs")
    try:
        # ① **先追加分片**：此刻活动文件尚未被改动 ⇒ 崩在这里只是"分片多了一份"，
        #    历史行仍在活动文件里（下一轮重新归档 ⇒ 重复而非丢失）。
        for day, day_lines in sorted(buckets.items()):
            arch = p.with_name(f"{p.stem}-{day}{p.suffix}")
            _raw_append_bytes(arch, "".join(l + "\n" for l in day_lines).encode("utf-8"))
            files.append(str(arch))
        # ② **再原子替换活动文件**（当日行 / 无当日行则删除活动文件，语义同旧实现）
        if today_lines:
            _atomic_write_lines(p, today_lines)
        else:
            p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001 归档失败不阻断调用方（best-effort）
        _bump("archive_write_failures")
        logger.warning("[LogArchive] 归档写入失败（未写记忆，可重试）%s: %s", p, e)
        return {"archived": 0, "files": files, "today": len(today_lines)}

    _ARCHIVED[str(p)] = today
    logger.info("[LogArchive] %s → 归档 %d 行 → %s（今日保留 %d 行）",
                p.name, moved, ",".join(files) or "-", len(today_lines))
    return {"archived": moved, "files": files, "today": len(today_lines)}


__all__ = [
    "ASSESSMENT_EVENTS_BASENAME", "LEGACY_DIGEST_EVENTS_BASENAME",
    "ENV_ARCHIVE_LOCK_ENABLED", "ENV_ARCHIVE_LOCK_TIMEOUT_SEC",
    "DEFAULT_ARCHIVE_LOCK_TIMEOUT_SEC",
    "lock_enabled", "lock_timeout_sec",
    "archive_stats", "reset_archive_stats",
    "jsonl_lock_path", "append_jsonl_locked",
    "repo_data_dir", "active_events_file", "events_files", "archive_daily_file",
]
