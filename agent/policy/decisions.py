"""DecisionLog — 决策日志（策略模拟器的重放数据源，P7.2-19）

【为什么需要它，而事件不够】
    §6.6 的 ``policy.decision`` 埋点只有
    ``{policy_version, actor, scope, result, latency_ms}``——**没有决策输入**。
    只靠这五个字段无法回答模拟器的问题：「把候选新策略放进去，这条历史决策会变成
    什么」。因此本模块额外持久化**脱敏后的决策输入**（``PolicyContext.input``），
    这是 P7.2-19「对历史 PolicyDecision 重放候选新策略」唯一可行的数据基础。

【脱敏口径（沿用 S2-03，不自建一套）】
    写入前对 ctx 走 ``agent.observability.events.sanitize_payload``——键名黑名单
    （密钥/凭据类精确与分段匹配）+ 长度上限 + JSON 可序列化，**不做值形态掩码**
    （S2-02 裁定 D6：掩码会误伤内部生成的 hex 关联键）。这一口径与事件层完全一致，
    因此「决策日志里有什么」与「事件里有什么」不需要分别审计。

    代价必须写明：**策略不应匹配凭据类字段**（那本身也是反模式）。若某条策略真的
    匹配了被脱敏丢弃的键，重放会因输入缺字段而产生分歧——模拟器把这类条目单列为
    ``replay_drift``（重放漂移）并计入报告，而不是把它藏起来。

【落盘纪律】
    - 默认路径 ``data/policies/decisions.jsonl``（运行时产物，已入 .gitignore）。
    - **用例必须显式传路径**（``decision_log_path=tmp_path/...``）或依赖本模块的
      autouse 隔离 fixture——这是 S3-02/S3-03 两次踩过的坑，不能靠默认路径兜。
    - best-effort：写失败只计数 + 告警，**绝不阻断决策路径**（与 S2 事件层同纪律）。

【轮转纪律（TASK-S8-02 步骤 4）】
    决策日志此前**只增不减**：``data/policies/decisions.jsonl`` 会随流量无界增长，
    而 ``read()``/模拟器每次都整读它。轮转把「旧记录」搬进分片，四条硬纪律：

    1. **归档而非删除**（S8-01 一致）：轮转只把记录**搬进**分片，永不丢弃。
       崩溃窗口的代价也必须落在「多算（可去重）」一侧，因此顺序恒为
       「先写分片 → 再原子替换活动文件」。
    2. **分片名必须与 ``_candidate_files()`` 的既有口径一致**：点号形态
       ``decisions.<后缀>.jsonl``。**不要**用连字符形态 ``decisions-YYYYMMDD.jsonl``
       ——它不满足 ``name.startswith(stem + ".")``，对读取端**完全不可见**
       （``agent/skills_mgmt/log_archiver.archive_daily_file`` 用的就是连字符，
       直接复用会产出"读不到的分片"，即静默丢数据）。后缀本身可以含连字符。
    3. **默认关闭**（S8 批次原则：默认保守）：``CP_POLICY_DECISION_LOG_MAX_BYTES=0``
       / ``CP_POLICY_DECISION_LOG_ROTATE_DAILY=0`` 时 ``maybe_rotate()`` 零 syscall 返回。
    4. **不改变统计语义**：``read()`` 恒按 ``ts`` 时间序返回，同一份数据在轮转前后
       的**记录多重集完全一致**——``simulator.simulate()`` 的 totals 因此逐字段不变
       （证据见 ``tests/unit/test_decision_log_rotation.py``）。

    轮转是**跨进程**的读-改-写，故走 ``agent/utils/cross_process_lock.py`` 的统一
    原语锁 ``<path>.lock``（**独立锁文件**，绝不锁 JSONL 本体）。锁拿不到就
    **跳过本轮并计数**——轮转只许"没做"，不许"做坏"决策路径。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Tuple

from agent.policy.models import (
    PolicyContext,
    PolicyDecision,
    canonical_json,
    now_iso,
)
# 跨进程锁（TASK-S8-02 步骤 2 的统一原语；**模块级导入**：utils 只依赖标准库，
# 不构成 agent.policy → agent.monitoring 的反向依赖，见 test_policy_support 的 AST 守卫）
from agent.utils.cross_process_lock import (
    LockUnavailable,
    cross_process_lock,
    lock_path_for,
)

logger = logging.getLogger("agent.policy.decisions")

#: 环境变量：决策日志路径
ENV_DECISION_LOG = "CP_POLICY_DECISION_LOG"
#: 环境变量：是否写入决策日志（"0" 关闭）
ENV_DECISION_LOG_ENABLED = "CP_POLICY_DECISION_LOG_ENABLED"
#: 环境变量：活动文件大小阈值（字节）；``0`` = **关闭**（默认关闭 = 保守默认）
ENV_DECISION_LOG_MAX_BYTES = "CP_POLICY_DECISION_LOG_MAX_BYTES"
#: 环境变量：是否按日轮转（"0" 关闭；默认关闭）
ENV_DECISION_LOG_ROTATE_DAILY = "CP_POLICY_DECISION_LOG_ROTATE_DAILY"
#: 环境变量：轮转后活动文件保留的目标字节数（``0`` ⇒ 由阈值推导为 ``max_bytes // 2``）
ENV_DECISION_LOG_KEEP_BYTES = "CP_POLICY_DECISION_LOG_KEEP_BYTES"
#: 环境变量：append 是否取跨进程锁（**三态**：未设置 = auto，即"轮转开启时才取"；
#: "1" 强制取；"0" 强制不取）。auto 的理由见 ``DecisionLog.lock_appends_effective``。
ENV_DECISION_LOG_LOCK_APPENDS = "CP_POLICY_DECISION_LOG_LOCK_APPENDS"
#: 环境变量：按日检查的节流间隔（秒）；``0`` ⇒ 每次 append 都检查
ENV_DECISION_LOG_DAILY_CHECK_SECONDS = "CP_POLICY_DECISION_LOG_DAILY_CHECK_SECONDS"

#: 默认决策日志（运行时产物）
DEFAULT_DECISION_LOG = "data/policies/decisions.jsonl"

#: 单行上限（与事件层同量级；超限行直接丢弃，避免被单条脏数据拖垮读取）
MAX_LINE_BYTES = 1 << 20

#: 默认大小阈值：**0 = 关闭**（默认保守；S8 批次原则）
DEFAULT_MAX_BYTES = 0
#: 默认保留目标：0 ⇒ 推导为 ``max_bytes // 2``（留一半余量做滞回，避免刚轮转就再触发）
DEFAULT_KEEP_BYTES = 0
#: 默认按日轮转：关闭
DEFAULT_ROTATE_DAILY = "0"
#: append 取锁的有限等待上限（秒）。**很短**：轮转窗口是毫秒级，
#: 而决策路径不许被 IO 长时间拖住；等不到即降级写（计数 + 告警）。
DEFAULT_APPEND_LOCK_TIMEOUT = 0.5
#: 轮转取锁的有限等待上限（秒）。轮转是低频操作，可以多等一会儿。
DEFAULT_ROTATE_LOCK_TIMEOUT = 2.0
#: 按日检查的默认节流间隔（秒）——避免每次 append 都整读活动文件
DEFAULT_DAILY_CHECK_SECONDS = 60.0
#: 并发追加的对账重试上限（每轮最多把"快照之后新到的完整行"并入多少次）
MAX_ROTATE_REREAD = 3

#: 长期句柄的**身份复核间隔**（每 N 次写入用 fstat/stat 比一次 inode）。
#:
#: 【为什么需要它（不易）】配置关闭轮转 ⇒ **本进程**不会执行 ``os.replace``，但
#: 滚动发布/多配置部署下**别的进程**可能开着轮转。POSIX 上它的 ``os.replace`` 会
#: 把我们的长期句柄指向被淘汰的 inode，之后的 append 会**静默写进读不到的文件**
#: （Windows 上对方 replace 会因共享冲突失败 ⇒ 只会"轮转被跳过"，不会丢）。
#: 每 N 次比一次 ``(st_dev, st_ino)`` 即可把这个窗口收敛到 N 次写入以内；
#: 摊销成本 ≈ 2 次 syscall / N（本机 ≈ 0.1 µs/条），对 p50/p99 无可见影响。
HANDLE_RECHECK_EVERY = 256

#: 无日历日记录的归档后缀（``decisions.undated.jsonl``）
SHARD_SUFFIX_UNDATED = "undated"
#: 临时文件后缀（**刻意不以 ``.jsonl`` 结尾**：否则会被 ``_candidate_files`` 当成分片读进来）
TEMP_SUFFIX = ".tmp"

# ── 轮转触发因 ──
TRIGGER_SIZE = "size"
TRIGGER_DAY = "day"
# ── 轮转结果因（**全部要能出现在摘要与日志里，不允许静默**）──
ROTATION_OK = "ok"
ROTATION_DISABLED = "disabled"
ROTATION_CLOSED = "closed"
ROTATION_BELOW_THRESHOLD = "below_threshold"
ROTATION_LOCK_UNAVAILABLE = "lock_unavailable"
ROTATION_NO_ACTIVE_FILE = "no_active_file"
ROTATION_UNSUPPORTED_PATH = "unsupported_path"
ROTATION_NOTHING_TO_MOVE = "nothing_to_move"
ROTATION_CONCURRENT_WRITE = "concurrent_write"
ROTATION_ERROR = "error"


class DecisionLogError(Exception):
    """决策日志错误（仅在显式 strict 模式下抛出）"""


def _is_daily_hyphen_shard(name: str, stem: str, ext: str) -> bool:
    """``name`` 是否是 ``<stem>-<YYYY-MM-DD><ext>``（log_archiver / S8-01 温层的产物）

    【实测口径：是 ISO 带连字符的日期，不是 ``YYYYMMDD``（实现期踩坑）】
    ``log_archiver.archive_daily_file`` 实际用
    ``f"{p.stem}-{day}{p.suffix}"``、``day`` 取自 ``ts[:10]``
    ⇒ 真实名字形如 ``decisions-2026-09-10.jsonl``。
    但该模块**自己的 docstring 写的是 ``<stem>-YYYYMMDD<ext>``**（不准确），
    照着它写匹配会得到"永远匹配不上"的死代码——本方法第一版即因此失效
    （用例 ``test_s801_warm_archive_shards_stay_visible`` 实测抓到）。
    故此处**同时接受** ISO 形态与紧凑 8 位形态，两种都不放过。

    只认**恰好**这两种日期形态：``decisions-backup.jsonl`` / ``decisions-old.jsonl`` /
    ``decisions-2026.jsonl`` / ``decisions-2026-09.jsonl`` 一律不收。
    理由见 ``DecisionLog._candidate_files`` 的 docstring（宽进会**多**读数据，
    同样是口径漂移）。
    """
    prefix = stem + "-"
    suffix = "." + ext
    if not name.startswith(prefix) or not name.endswith(suffix):
        return False
    middle = name[len(prefix):len(name) - len(suffix)]
    if len(middle) == 10 and middle[4] == "-" and middle[7] == "-":
        # ISO 形态：2026-09-10
        return (middle[:4].isdigit() and middle[5:7].isdigit()
                and middle[8:].isdigit())
    if len(middle) == 8:
        # 紧凑形态：20260910（历史/其它调用方可能使用）
        return middle.isdigit()
    return False


class _ConcurrentWrite(RuntimeError):
    """活动文件在轮转期间被并发写入/替换（**放弃本轮，不动任何文件**）"""


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int = 0) -> int:
    """读整型环境变量（**脏值一律回退默认**：配置错误不该让决策路径抛异常）"""
    return _safe_int(os.environ.get(name), default)


def _env_float(name: str, default: float = 0.0) -> float:
    return _safe_float(os.environ.get(name), default)


def _env_optional_flag(name: str) -> Optional[bool]:
    """三态环境变量（**未设置/空 ⇒ None**；"1"/"0" ⇒ True/False）

    【为什么需要三态】``CP_POLICY_DECISION_LOG_LOCK_APPENDS`` 的"未设置"必须与
    "显式 0"区分开：未设置 = **auto**（轮转开启时才取锁），显式 1/0 = 强制开/关。
    两态会让"auto"无法表达。
    """
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize(payload: Any) -> Any:
    """走 S2-03 事件层脱敏（**延迟导入**：决策热路径不应被审计栈拖慢）"""
    try:
        from agent.observability.events import sanitize_payload
        return sanitize_payload(payload)
    except Exception:  # noqa: BLE001 脱敏不可用 ⇒ 保守起见只留结构，不留值
        if isinstance(payload, dict):
            return {str(k): "<redacted:sanitizer-unavailable>" for k in payload}
        return {"value": "<redacted:sanitizer-unavailable>"}


# ════════════════════════════════════════════════════════════
#  轮转：分片命名 / 时间序（纯函数，便于单测直接钉住口径）
# ════════════════════════════════════════════════════════════


def valid_day(day: str) -> bool:
    """``YYYY-MM-DD`` 形态且是**真实存在**的日历日"""
    text = str(day or "")
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return False
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def shard_affixes(target: str) -> Optional[Tuple[str, str, str]]:
    """拆出分片命名所需的 ``(目录, 词干, 扩展名)``；**不可轮转的路径返回 None**

    【为什么无扩展名/无词干就拒绝轮转（实现期发现的静默丢数据陷阱）】
    ``_candidate_files()`` 只认 ``name.startswith(stem + ".") and name.endswith("." + ext)``
    的分片。若目标形如 ``data/decisions``（无扩展名），``stem`` 为空 ⇒ 任何分片名都
    不可能被读取端枚举到 ⇒ 轮转等于**把记录搬进读不到的文件**。故宁可不轮转。
    """
    base = os.path.basename(str(target or ""))
    stem, _dot, ext = base.rpartition(".")
    if not stem or not ext:
        return None
    return os.path.dirname(str(target)) or ".", stem, ext


def shard_name(target: str, day: str = "") -> str:
    """由活动文件路径与日历日派生分片文件名（**点号形态**）

    口径（与既有 ``test_按日分片与目录路径`` 一致）：

        data/policies/decisions.jsonl  +  2026-09-11  →  decisions.20260911.jsonl
        data/policies/decisions.jsonl  +  ""（无日）  →  decisions.undated.jsonl

    后缀本身**确定性**（同一天恒定映射同一文件）且**无碰撞**（同名即同一归档，
    一律 append，永不 truncate）。刻意避免连字符形态——它对读取端不可见。
    """
    affixes = shard_affixes(target)
    if affixes is None:
        return ""
    directory, stem, ext = affixes
    suffix = day.replace("-", "") if valid_day(day) else SHARD_SUFFIX_UNDATED
    return os.path.join(directory, f"{stem}.{suffix}.{ext}")


def shard_day(name: str) -> str:
    """从分片名反解日历日（``decisions.20260911.jsonl`` → ``2026-09-11``）；无则 ``""``"""
    base = os.path.basename(str(name or ""))
    stem, _dot, ext = base.rpartition(".")
    if not stem or not ext:
        return ""
    suffix = stem.rpartition(".")[2]
    if len(suffix) != 8 or not suffix.isdigit():
        return ""
    day = f"{suffix[:4]}-{suffix[4:6]}-{suffix[6:]}"
    return day if valid_day(day) else ""


def ts_order_key(ts: Any) -> Optional[datetime]:
    """``ts`` → 可比较的排序键；**无法解析返回 None**（表示"无位置"）

    【为什么无时区的 ts 按 UTC 解释】仅用于**排序键**，不改写记录本身：
    ``now_iso()`` 恒写带本机偏移的 ISO-8601，naive 只可能来自外部注入的脏数据；
    若把 naive 当本地时区解析，混排时会与 aware 记录比较而抛 ``TypeError``，
    等于让一条脏记录炸掉整次 ``read()``。统一按 UTC 解释即可保证全序可比。
    """
    text = str(ts or "").strip()
    if len(text) < 10:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def chronological(records: List[DecisionRecord]) -> List[DecisionRecord]:
    """把记录排成**时间序**（稳定；无 ``ts``/``ts`` 脏的记录**原地不动**）

    【为什么排序只放在 ``read()``，而不动 ``iter_records``】
        ``iter_records`` 是**流式**契约（逐行 yield，超大日志可用内存换时间），
        排序必须物化整个结果集，塞进去就等于放弃流式。故时间序是 ``read()`` 的
        保证；``iter_records`` 保持"按文件顺序流式"，而文件顺序是
        ``[活动文件, 分片...]``——**最新数据在最前**。这正是未排序时
        ``read(limit=N)`` 会取到**最旧分片**的根因。

    【为什么不是朴素的 ``sort(key=ts)``（关键，且是轮转会放大的坑）】
        脏记录（半行、外部注入、``ts=""``）没有可比位置：朴素排序把它们的键
        当成空值排到**最前**，于是"最近 N 条"里会塞满脏记录。这里用**槽位回填**：
        只对"ts 可解析"的记录排序，再放回它们原本占据的那些**位置槽**，
        脏记录原地不动。

    【平局规则（tie-break）】
        ``sorted`` 稳定 ⇒ 同 ``ts`` 的记录保持**文件内相对顺序**；而
        ``iter_records`` 的文件顺序是 ``[活动文件, 分片（名升序）]``，
        因此同 ``ts`` 时活动文件（更新）优先于分片。这是**确定性**口径，
        不依赖字典/集合顺序。
    """
    if len(records) < 2:
        return records
    slots: List[Tuple[int, datetime]] = []
    for index, record in enumerate(records):
        key = ts_order_key(record.ts)
        if key is not None:
            slots.append((index, key))
    if len(slots) < 2:
        return records
    ordered = sorted(slots, key=lambda item: item[1])          # 稳定排序
    result = list(records)
    for (slot, _key), (_index, _key2) in zip(slots, ordered):
        result[slot] = records[_index]
    return result


def line_day(line: Any) -> str:
    """从一行 JSONL 原文取日历日（用于按日归档）；解析不出返回 ``""``"""
    text = line.decode("utf-8", errors="ignore") if isinstance(line, bytes) else str(line or "")
    text = text.strip()
    if not text:
        return ""
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    day = str(payload.get("ts") or "")[:10]
    return day if valid_day(day) else ""


# ════════════════════════════════════════════════════════════
#  记录
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DecisionRecord:
    """一条可重放的决策记录

    Attributes:
        ts: 决策时刻（本地带偏移 ISO-8601，与事件层同口径）。
        input: 脱敏后的决策输入（``PolicyContext.input``）。
        effect / policy_id / policy_version / reason_code: 原决策结果。
        cache_hit: 原决策是否命中缓存（重放时无意义，仅供统计）。
        latency_ms: 原决策耗时。
        fingerprint: 原决策所用策略库指纹（定位「哪一版策略库做出的这个决策」）。
        revision: 原决策时的策略库修订号。
        tenant_id / capability_id / action / actor: 定位叶子（与事件层同口径）。
        extra: 附加字段（可选；不参与去重键）。

    【重放漂移不在本记录上（TASK-S8-02 步骤 4 的文档缺陷修正）】
        本类此前在文档里写了一个 ``replay_drift`` 字段，但 dataclass 里**并没有**
        它——文档与实现不一致。之所以选择**改文档而不是加字段**：
        漂移是「基线引擎重算 ≠ 历史 effect」的**派生结论**，只在重放那一刻才知道，
        原记录里它恒为 False，加字段等于给每条历史记录塞一个永远为假的常量，
        还会改变 ``to_json_line()`` 的落盘 schema（``policy.decision.v1``）。
        真实承载点见 ``simulator.SimulationChange.drift`` 与
        ``SimulationReport.replay_drift``（报告 totals 里的同名项）。
    """

    ts: str = ""
    input: Dict[str, Any] = field(default_factory=dict)
    effect: str = ""
    policy_id: str = ""
    policy_version: str = ""
    reason_code: str = ""
    cache_hit: bool = False
    latency_ms: float = 0.0
    fingerprint: str = ""
    revision: int = 0
    tenant_id: str = "default"
    capability_id: str = ""
    action: str = ""
    actor: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_json_line(self) -> str:
        body = {
            "ts": self.ts,
            "schema": "policy.decision.v1",
            "input": self.input,
            "effect": self.effect,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
            "cache_hit": bool(self.cache_hit),
            "latency_ms": round(float(self.latency_ms), 4),
            "fingerprint": self.fingerprint,
            "revision": int(self.revision),
            "tenant_id": self.tenant_id,
            "capability_id": self.capability_id,
            "action": self.action,
            "actor": self.actor,
        }
        if self.extra:
            body["extra"] = self.extra
        line = canonical_json(body)
        if not line:  # 载荷夹带了不可序列化对象 ⇒ 退回 default=str（仍保证是一行 JSON）
            line = json.dumps(body, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), default=str)
        return line

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionRecord":
        """从一行 JSON 还原（**容错**：单条脏记录不能拖垮模拟器）

        决策日志是 append-only 的运行时产物，可能因磁盘满/进程被杀而留下半行或
        字段类型异常。这里的容错口径与事件层一致：**能救的字段救回来，救不回来的
        用中性默认**，绝不因为一条脏记录让整次模拟（乃至 CI 门禁）失败。
        """
        payload = data or {}
        raw_input = payload.get("input")
        raw_extra = payload.get("extra")
        return cls(
            ts=str(payload.get("ts") or ""),
            input=raw_input if isinstance(raw_input, dict) else {},
            effect=str(payload.get("effect") or ""),
            policy_id=str(payload.get("policy_id") or ""),
            policy_version=str(payload.get("policy_version") or ""),
            reason_code=str(payload.get("reason_code") or ""),
            cache_hit=bool(payload.get("cache_hit")),
            latency_ms=_safe_float(payload.get("latency_ms")),
            fingerprint=str(payload.get("fingerprint") or ""),
            revision=_safe_int(payload.get("revision")),
            tenant_id=str(payload.get("tenant_id") or "default"),
            capability_id=str(payload.get("capability_id") or ""),
            action=str(payload.get("action") or ""),
            actor=str(payload.get("actor") or ""),
            extra=dict(raw_extra) if isinstance(raw_extra, dict) else {},
        )

    def day(self) -> str:
        return self.ts[:10] if len(self.ts) >= 10 else ""

    def ctx(self) -> PolicyContext:
        """还原决策输入（模拟器重放用）"""
        return PolicyContext.from_input(self.input)


# ════════════════════════════════════════════════════════════
#  DecisionLog
# ════════════════════════════════════════════════════════════


class DecisionLog:
    """追加式决策日志（线程安全；best-effort；支持跨进程安全的轮转）

    Args:
        path: 落盘路径；``None`` ⇒ ``CP_POLICY_DECISION_LOG`` 或默认路径。
        enabled: 是否写入；``None`` ⇒ ``CP_POLICY_DECISION_LOG_ENABLED``（默认开）。
        strict: 写失败时是否抛异常（默认 False：绝不阻断决策）。
            **对轮转无效**——轮转失败恒不抛（轮转只许"没做"，不许"做坏"决策路径）。
        min_free_bytes: 低于该磁盘余量时自动停写（防止把盘写满）。
        max_bytes: 活动文件大小阈值（字节）；``0`` = **关闭**（默认；保守默认）。
            ``None`` ⇒ ``CP_POLICY_DECISION_LOG_MAX_BYTES``。
        rotate_daily: 是否按日轮转（把"早于今天"的记录搬进当日分片）；
            ``None`` ⇒ ``CP_POLICY_DECISION_LOG_ROTATE_DAILY``（默认关）。
        keep_bytes: 轮转后活动文件保留的目标字节数；``0`` ⇒ ``max_bytes // 2``。
        lock_appends: append 是否取跨进程锁。``None`` = **auto**（推荐）：
            轮转开启时取（本进程随时可能 ``rotate``，append 必须让开读-改-写窗口），
            轮转关闭时不取（**单进程下没有互斥对象，它保护的是一个微秒级窗口，
            却要每条付实测约 40 µs p50 / 100 µs p99**）。``True`` = 无条件取
            （多进程且各自轮转配置不一致的部署可显式要求协作）；
            ``False`` = 无条件不取。``None`` ⇒ ``CP_POLICY_DECISION_LOG_LOCK_APPENDS``
            （未设置 = auto；"1"/"0" = 强制）。
        append_lock_timeout: append 取锁的有限等待上限（秒；等不到就降级写并计数）。
        daily_check_seconds: 按日检查的节流间隔（秒；``0`` ⇒ 每次 append 都检查）。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        enabled: Optional[bool] = None,
        strict: bool = False,
        min_free_bytes: int = 32 * 1024 * 1024,
        max_bytes: Optional[int] = None,
        rotate_daily: Optional[bool] = None,
        keep_bytes: Optional[int] = None,
        lock_appends: Optional[bool] = None,
        append_lock_timeout: float = DEFAULT_APPEND_LOCK_TIMEOUT,
        rotate_lock_timeout: float = DEFAULT_ROTATE_LOCK_TIMEOUT,
        daily_check_seconds: Optional[float] = None,
    ) -> None:
        self._path = str(path if path is not None
                         else os.environ.get(ENV_DECISION_LOG) or DEFAULT_DECISION_LOG)
        self._enabled = _env_flag(ENV_DECISION_LOG_ENABLED, "1") if enabled is None \
            else bool(enabled)
        self._strict = bool(strict)
        self._min_free_bytes = int(min_free_bytes)
        # ── 轮转配置（默认全关 = 保守默认；开启后才产生 syscall/锁文件）──
        self._max_bytes = max(_env_int(ENV_DECISION_LOG_MAX_BYTES, DEFAULT_MAX_BYTES)
                              if max_bytes is None else _safe_int(max_bytes), 0)
        self._rotate_daily = (_env_flag(ENV_DECISION_LOG_ROTATE_DAILY, DEFAULT_ROTATE_DAILY)
                              if rotate_daily is None else bool(rotate_daily))
        self._keep_bytes = max(_env_int(ENV_DECISION_LOG_KEEP_BYTES, DEFAULT_KEEP_BYTES)
                               if keep_bytes is None else _safe_int(keep_bytes), 0)
        self._lock_appends = (_env_optional_flag(ENV_DECISION_LOG_LOCK_APPENDS)
                              if lock_appends is None else bool(lock_appends))
        self._append_lock_timeout = max(_safe_float(append_lock_timeout,
                                                    DEFAULT_APPEND_LOCK_TIMEOUT), 0.0)
        self._rotate_lock_timeout = max(_safe_float(rotate_lock_timeout,
                                                    DEFAULT_ROTATE_LOCK_TIMEOUT), 0.0)
        self._daily_check_seconds = max(
            _env_float(ENV_DECISION_LOG_DAILY_CHECK_SECONDS, DEFAULT_DAILY_CHECK_SECONDS)
            if daily_check_seconds is None else _safe_float(daily_check_seconds),
            0.0)
        self._lock = threading.RLock()
        #: 跨进程锁（**惰性构造**：未启用的日志不该在磁盘上留下锁文件）
        self._xlock: Optional[Any] = None
        #: 活动文件所在目录已确认存在（省掉每次 append 的 makedirs）
        self._dir_ready = False
        # ── 长期句柄（**仅轮转关闭时的快路径**；安全性见 _append_line） ──
        #: 复用的写入句柄（``None`` = 无；轮转开启时恒为 ``None``）
        self._handle: Optional[Any] = None
        #: 该句柄打开时的 ``_rotation_generation``（不等 ⇒ 句柄已作废，必须重开）
        self._handle_generation = -1
        #: 轮转代际：**每次成功 os.replace 后 +1**（快路径据此判定句柄失效）
        self._rotation_generation = 0
        #: 长期句柄的写入/重开/复用计数（可观测：确认快路径真的生效）
        self._handle_writes = 0
        self._handle_reopens = 0
        self._handle_reuse = 0
        self._closed = False
        self._write_count = 0
        self._failure_count = 0
        self._last_error = ""
        self._skipped_low_disk = 0
        # ── 轮转计数（**全部进 stats**：加固了却没有计数 = 无法回答"有没有生效"）──
        self._rotation_count = 0
        self._rotation_records = 0
        self._rotation_shards_created = 0
        self._rotation_bytes_reclaimed = 0
        self._rotation_skipped_locked = 0
        self._rotation_skipped_concurrent = 0
        self._rotation_failures = 0
        self._rotation_last: Dict[str, Any] = {}
        self._rotation_last_error = ""
        #: 日轮转节流的时间戳（``None`` = 还没检查过）
        self._daily_checked_at: Optional[float] = None
        #: 读不回的分片数（B 项：**静默跳过整片 = 静默篡改统计**）
        self._unreadable_shards = 0
        #: 拿不到锁仍写入的次数（降级留痕；轮转安全性的观测量）
        self._degraded_appends = 0

    # ── 属性 ──

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._closed

    @property
    def lock_appends_effective(self) -> bool:
        """append 实际是否取跨进程锁（**auto = 轮转开启时才取**）

        【为什么默认 auto，而不是"永远取"（性能验收口径：单进程写入 p50/p99 不退化）】
            每条 append 取一次跨进程锁在本机实测约 **40 µs p50 / 100 µs p99**
            （锁文件 open + OS 锁 + 写 512B 诊断槽 + unlock + close）。它换来的是
            与"**别的进程**正在做轮转读-改-写"的互斥。而当本日志**轮转关闭**时：

            - 单进程部署：没有任何并发的读-改-写，锁是**纯开销**；
            - 多进程但配置一致（都关）：同上；
            - 多进程且**配置不一致**（别的进程开着轮转）：轮转侧的
              ``_merge_concurrent_appends`` + 替换前字节数校验已经把"快照之后新到的
              行"并入新内容，锁只再收窄"最后一次校验 → os.replace"这个**微秒级**
              窗口；而该残余窗口对**降级写者**本来就不受锁保护（见 ``_append_line``）。

            需要无条件协作加锁的部署（例如跨机器脚本各自轮转）可显式
            ``CP_POLICY_DECISION_LOG_LOCK_APPENDS=1`` 恢复严格行为。
        """
        if self._lock_appends is None:
            return self.rotation_enabled
        return bool(self._lock_appends)

    @property
    def lock_path(self) -> str:
        """跨进程锁文件路径（``<path>.lock``；**绝不锁 JSONL 本体**，见锁模块文档）"""
        return lock_path_for(self._path)

    @property
    def rotation_enabled(self) -> bool:
        """是否配置了任何轮转触发（默认 False = 保守默认）"""
        return self._max_bytes > 0 or self._rotate_daily

    @property
    def rotation_config(self) -> Dict[str, Any]:
        return {"max_bytes": self._max_bytes,
                "keep_bytes": self.effective_keep_bytes,
                "rotate_daily": bool(self._rotate_daily),
                "lock_appends": self._lock_appends,
                "lock_appends_effective": self.lock_appends_effective,
                "lock_path": self.lock_path}

    @property
    def effective_keep_bytes(self) -> int:
        """实际保留字节数（``keep_bytes<=0`` ⇒ ``max_bytes // 2``，留滞回余量）"""
        if self._keep_bytes > 0:
            return self._keep_bytes
        return max(self._max_bytes // 2, 0)

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"path": self._path, "enabled": self.enabled,
                    "write_count": self._write_count,
                    "failure_count": self._failure_count,
                    "last_error": self._last_error,
                    "skipped_low_disk": self._skipped_low_disk,
                    # 读取侧诚实性：整片读不回必须可见
                    "unreadable_shards": self._unreadable_shards,
                    # 轮转
                    "rotation_enabled": self.rotation_enabled,
                    "rotation_max_bytes": self._max_bytes,
                    "rotation_keep_bytes": self.effective_keep_bytes,
                    "rotation_daily": bool(self._rotate_daily),
                    "rotation_count": self._rotation_count,
                    "rotation_records": self._rotation_records,
                    "rotation_shards_created": self._rotation_shards_created,
                    "rotation_bytes_reclaimed": self._rotation_bytes_reclaimed,
                    "rotation_skipped_locked": self._rotation_skipped_locked,
                    "rotation_skipped_concurrent": self._rotation_skipped_concurrent,
                    "rotation_failures": self._rotation_failures,
                    "rotation_last_error": self._rotation_last_error,
                    "rotation_last": dict(self._rotation_last),
                    "rotation_generation": self._rotation_generation,
                    "degraded_appends": self._degraded_appends,
                    "lock_appends": self._lock_appends,
                    "lock_appends_effective": self.lock_appends_effective,
                    # 长期句柄（轮转关闭时走快路径；计数用于确认它真的生效）
                    "handle_reuse": self._handle_reuse,
                    "handle_reopens": self._handle_reopens,
                    "handle_open": self._handle is not None}

    # ── 写入 ──

    def append(self, ctx: Any, decision: PolicyDecision, *,
               fingerprint: str = "", revision: int = 0,
               ts: Optional[str] = None,
               extra: Optional[Dict[str, Any]] = None) -> bool:
        """写入一条决策记录；返回是否真正落盘（关闭/失败 → False）"""
        if not self.enabled:
            return False
        context = ctx if isinstance(ctx, PolicyContext) else PolicyContext.from_input(ctx)
        record = DecisionRecord(
            ts=ts or now_iso(),
            input=_sanitize(context.input) if isinstance(context.input, dict) else {},
            effect=decision.effect,
            policy_id=decision.policy_id,
            policy_version=decision.policy_version,
            reason_code=decision.reason_code,
            cache_hit=decision.cache_hit,
            latency_ms=decision.latency_ms,
            fingerprint=fingerprint,
            revision=revision,
            tenant_id=context.tenant_id,
            capability_id=context.capability_id,
            action=context.action,
            actor=context.actor,
            extra=dict(extra or {}),
        )
        line = record.to_json_line()
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            self._note_failure("records too large; dropped")
            return False
        try:
            with self._lock:
                if not self.enabled:
                    return False
                if self._min_free_bytes and not self._has_free_space():
                    self._skipped_low_disk += 1
                    return False
                self._append_line(line)
                self._write_count += 1
        except Exception as exc:  # noqa: BLE001 best-effort
            self._note_failure(f"{type(exc).__name__}: {exc}")
            return False
        # 【为什么轮转在锁**外**调用】append 已把记录刷到盘上，返回值不再依赖轮转：
        # 轮转即使整体失败，也只允许"没做"，绝不允许"做坏"决策路径（best-effort）。
        try:
            self.maybe_rotate()
        except Exception as exc:  # noqa: BLE001 兜底：轮转异常**不得**影响 append 结果
            self._note_rotation_failure(f"maybe_rotate: {type(exc).__name__}: {exc}")
        return True

    def _ensure_directory(self) -> None:
        """确保活动文件所在目录存在（**只做一次**：不在热路径上反复 makedirs）"""
        if self._dir_ready:
            return
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self._dir_ready = True

    def _append_line(self, line: str) -> None:
        """在跨进程锁保护下追加一行（**两条路径；失败向上抛，由 append 统一计数**）

        路径选择只看**有效轮转配置**（``rotation_enabled``）：

        - **轮转关闭（默认）⇒ 复用长期句柄**（``_durable_handle()``，开-写-关一次之后
          一直复用）：本进程不可能执行 ``os.replace``，也就没有下面那两条风险，
          为"不可能发生的操作"付每次 open/close 的钱是**纯退化**。
        - **轮转开启 ⇒ 每次开-关句柄**。

        【为什么轮转开启时必须开-关句柄，而不是留长期句柄（实现期实测，关键）】
            轮转必须**原子替换**活动文件（``os.replace``）。留长期句柄会两处致命：

            1. **Windows 上替换直接失败**：实测对"仍被打开"的目标文件做
               ``os.replace`` 抛 ``PermissionError [WinError 5]``——本进程自己的
               轮转会永远做不成（句柄是同一个进程持有的也一样会拒绝）。
            2. **POSIX 上更糟：静默丢记录**。句柄指向被替换掉的旧 inode，之后的
               ``append`` 全部写进一个**再也不会被读取**的文件——没有异常、没有
               计数、没有告警，正是本任务明令禁止的静默丢失。

            这条路径的代价（Windows 实测）：长期句柄 55 µs/条 vs 每次开-关
            307 µs/条 ⇒ 净增约 **250 µs/条**（整条 append 约 640 µs）。开启轮转的
            部署可以接受；**关闭轮转时不该付**——所以快路径按配置开关分流。

        【安全性不依赖那个开关（不易）】配置关闭只决定**走哪条快路径**，不当作安全
        依据：显式 ``rotate(force=True)``（运维随时可能调）照样会 ``os.replace``。
        真正兜住它的是三重机制：

        1. 替换前 ``_close_handle()``（本进程句柄绝不在替换期间存活）；
        2. ``_rotation_generation`` 代际比对（成功替换后 +1，快路径命中句柄时比对）；
        3. 周期性**身份复核**（``HANDLE_RECHECK_EVERY``）：比对 fstat/stat 的
           ``(st_dev, st_ino)``，兜住"**别的进程**开着轮转、把我们句柄指向的 inode
           换掉"这一本进程配置看不见的跨进程场景。

        【为什么 append 也（可能要）取锁】轮转是活动文件的读-改-写。若 append 不持锁，
        它可能正好落在"轮转快照之后、替换之前"的窗口里而被覆盖掉。故**轮转开启时**
        一律取锁，把这个窗口压缩到"仅降级写者"（见 ``_degraded_appends``）；
        轮转关闭时默认不取（auto，见 ``lock_appends_effective`` 的【为什么】）。
        """
        held = False
        if self.lock_appends_effective:
            held = self._acquire_xlock(self._append_lock_timeout)
            if not held:
                # 锁不可用**不能**丢决策记录：仍写，但计数 + 告警（降级留痕）
                self._degraded_appends += 1
                logger.warning(
                    "决策日志 append 未取得跨进程锁，降级写入（锁不可用/被占用）: %s",
                    self.lock_path)
        try:
            handle = self._durable_handle()
            if handle is not None:
                handle.write(line + "\n")
                handle.flush()
                self._handle_reuse += 1
                return
            self._ensure_directory()
            with open(self._path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
        finally:
            if held:
                self._release_xlock()

    # ── 长期句柄（仅"轮转不可能发生"时的快路径；安全性见 _append_line） ──

    def _durable_handle(self) -> Optional[Any]:
        """取可复用的长期句柄；**当前配置下不允许复用时返回 ``None``**（走开-关慢路径）

        【为什么不是"配置说了算"】配置只决定快路径选择；句柄是否**仍然有效**由
        代际比对与身份复核共同保证：

        - 代际不等（本进程刚成功轮转过）⇒ 关闭重开；
        - 每 ``HANDLE_RECHECK_EVERY`` 次写入复核 ``(st_dev, st_ino)``，与路径上的
          文件不一致 ⇒ 关闭重开（跨进程替换，本进程配置看不见的那一类）。

        Returns:
            可写入的句柄；``None`` 表示调用方应走"每次开-关"的慢路径。
        """
        if self.rotation_enabled:
            # 轮转开启：本进程随时可能 os.replace ⇒ 不许有长期句柄存活
            self._close_handle()
            return None
        if self._handle is not None and self._handle_generation != self._rotation_generation:
            self._close_handle()          # 代际已变：旧句柄指向被替换掉的文件
        if self._handle is None:
            self._ensure_directory()
            self._handle = open(self._path, "a", encoding="utf-8")
            self._handle_generation = self._rotation_generation
        self._handle_writes += 1
        if HANDLE_RECHECK_EVERY > 0 and \
                self._handle_writes % HANDLE_RECHECK_EVERY == 0 and \
                not self._handle_identity_ok():
            self._handle_reopens += 1
            logger.warning("决策日志长期句柄指向的文件已被替换，重开句柄: %s", self._path)
            self._close_handle()
            self._handle = open(self._path, "a", encoding="utf-8")
            self._handle_generation = self._rotation_generation
        return self._handle

    def _close_handle(self) -> None:
        """关闭并作废长期句柄（幂等）"""
        handle = self._handle
        self._handle = None
        self._handle_generation = -1
        if handle is None:
            return
        try:
            handle.flush()
            handle.close()
        except Exception:  # noqa: BLE001 关闭失败不影响调用方
            pass

    def _handle_identity_ok(self) -> bool:
        """长期句柄指向的**仍是当前路径上那个文件**吗（廉价身份复核）

        【为什么用 ``(st_dev, st_ino)`` 而不是 size/mtime】活动文件每次 append 都在
        长（size 必变），拿 size 比会把"正常追加"误判成"被替换" ⇒ 每次都重开，
        等于退回慢路径。inode 才是"是不是同一个文件"的判据。

        【为什么 inode 取不到就返回 True】部分文件系统/平台 ``st_ino`` 为 0，
        此时**无法判定**；按"仍是同一文件"处理（不重开），避免退化成"每条都重开"
        的性能悬崖——这类平台上的跨进程替换由锁 + 轮转侧的放弃逻辑兜。
        """
        handle = self._handle
        if handle is None:
            return False
        try:
            mine = os.fstat(handle.fileno())
            current = os.stat(self._path)
        except OSError:
            return False
        if mine.st_ino and current.st_ino:
            return (mine.st_dev, mine.st_ino) == (current.st_dev, current.st_ino)
        return True

    def _has_free_space(self) -> bool:
        try:
            usage = os.statvfs(self._path) if hasattr(os, "statvfs") else None
        except OSError:
            usage = None
        if usage is not None:
            return bool(usage.f_bavail * usage.f_frsize >= self._min_free_bytes)
        try:  # Windows：用 shutil.disk_usage
            import shutil
            directory = os.path.dirname(self._path) or "."
            return bool(shutil.disk_usage(directory).free >= self._min_free_bytes)
        except Exception:  # noqa: BLE001 无法判定 ⇒ 视为有空间（不因探测失败停写）
            return True

    # ── 跨进程锁（统一原语；**锁独立文件**，绝不锁 JSONL 本体） ──

    def _x_locker(self) -> Any:
        """惰性构造本路径的跨进程锁（同名同路径在进程内复用同一实例 ⇒ 可重入）"""
        if self._xlock is None:
            self._xlock = cross_process_lock(
                lock_path_for(self._path), name="policy.decision_log",
                holder_info={"decision_log": self._path})
        return self._xlock

    def _acquire_xlock(self, timeout: float) -> bool:
        """取跨进程锁（有限等待；**拿不到返回 False，绝不抛**）"""
        try:
            lock = self._x_locker()
        except Exception as exc:  # noqa: BLE001 锁不可构造 ⇒ 降级
            logger.warning("决策日志跨进程锁不可构造（降级）: %s", exc)
            return False
        wait = max(_safe_float(timeout), 0.0)
        try:
            if wait > 0:
                lock.acquire(wait)      # 超时抛 LockTimeout（LockUnavailable 子类）
                return bool(lock.held)
            return bool(lock.try_lock())
        except LockUnavailable:
            return False
        except Exception as exc:  # noqa: BLE001 LockFileError 等 ⇒ 降级
            logger.warning("决策日志取跨进程锁失败（降级）: %s", exc)
            return False

    def _release_xlock(self) -> None:
        lock = self._xlock
        if lock is None:
            return
        try:
            lock.release()
        except Exception as exc:  # noqa: BLE001 释放失败不影响调用方
            logger.warning("决策日志释放跨进程锁失败: %s", exc)

    def _note_failure(self, detail: str) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_error = detail
        logger.warning("决策日志写入失败（不影响决策）: %s", detail)
        if self._strict:
            raise DecisionLogError(detail)

    # ── 轮转（TASK-S8-02 步骤 4；默认关闭、归档而非删除、跨进程安全） ──

    def _note_rotation_failure(self, detail: str) -> None:
        """轮转失败计数 + 告警

        **刻意不抛**（即使 ``strict=True``）：轮转是后台卫生工作，而 strict 的语义
        是"写入失败要显式失败"。让轮转失败炸掉决策路径，就等于用运维问题换业务
        中断——本任务明令禁止。
        """
        with self._lock:
            self._rotation_failures += 1
            self._rotation_last_error = detail
        logger.warning("决策日志轮转失败（不影响决策；本轮不做）: %s", detail)

    def _rotation_summary(self, reason: str, **extra: Any) -> Dict[str, Any]:
        """轮转摘要骨架（**每次轮转/跳过都有摘要**，便于计数与留痕）"""
        summary: Dict[str, Any] = {
            "path": self._path,
            "rotated": reason == ROTATION_OK,
            "reason": reason,
            "trigger": "",
            "records_moved": 0,
            "records_kept": 0,
            "shards": [],
            "shards_created": [],
            # 【术语】bytes_reclaimed = **活动文件缩小**的字节数，**不是磁盘回收**：
            # 记录被搬进分片（归档而非删除），磁盘占用只会增加。
            "bytes_reclaimed": 0,
            "bytes_archived": 0,
            "active_bytes_before": 0,
            "active_bytes_replaced": 0,
            "active_bytes_after": 0,
            "error": "",
        }
        summary.update(extra)
        return summary

    def _log_rotation(self, summary: Dict[str, Any]) -> None:
        """轮转必须留结构化日志（**不许静默**：跳过也是一种结果）

        分级：【为什么】``maybe_rotate()`` 在每次 append 上都会跑，但其
        "未启用/未达阈值"的早退**不是一次轮转尝试**（每次都留痕会把日志刷爆）；
        而任何走完 ``rotate()`` 的**尝试**——成功、跳过、失败——都必须留痕。
        故本方法只由 ``rotate()`` 的统一出口调用。
        """
        try:
            body = json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 摘要全是原始类型，兜底不该发生
            body = str(summary)
        reason = summary.get("reason")
        if reason == ROTATION_OK:
            logger.info("决策日志轮转 %s", body)
        elif reason == ROTATION_BELOW_THRESHOLD:
            logger.debug("决策日志轮转未达阈值 %s", body)
        else:
            logger.warning("决策日志轮转未执行 %s", body)

    def _finish_rotation(self, summary: Dict[str, Any]) -> Dict[str, Any]:
        """``rotate()`` 的**唯一出口**：记最近一次摘要 + 留痕 + 返回"""
        self._rotation_last = summary
        self._log_rotation(summary)
        return summary

    def _active_size(self) -> int:
        return self._path_size(self._path)

    @staticmethod
    def _path_size(path: str) -> int:
        try:
            return int(os.path.getsize(path))
        except OSError:
            return 0

    def _daily_check_due(self, now: Optional[float] = None) -> bool:
        """按日检查是否到期（**节流**：整读活动文件不该每次 append 都做）"""
        if self._daily_check_seconds <= 0:
            return True
        current = time.monotonic() if now is None else float(now)
        checked = self._daily_checked_at
        return checked is None or (current - checked) >= self._daily_check_seconds

    def _has_old_day_records(self) -> bool:
        """活动文件里是否存在"早于今天"的记录（**整读**；仅按日轮转开启时调用）"""
        today = date.today().isoformat()
        try:
            with open(self._path, "rb") as handle:
                for raw in handle:
                    day = line_day(raw)
                    if day and day < today:
                        return True
        except OSError:
            return False
        return False

    def _pending_trigger(self) -> str:
        """当前应轮转的触发因（``""`` = 无需轮转）；**含一次 stat**"""
        if self._max_bytes > 0 and self._active_size() >= self._max_bytes:
            return TRIGGER_SIZE
        if self._rotate_daily and self._has_old_day_records():
            return TRIGGER_DAY
        return ""

    def _configured_rule(self) -> str:
        """``force`` 且未指定触发因时用哪条规则

        两者都没配（默认配置）时返回 ``SIZE``：``force=True`` 是**显式运维动作**
        （"现在就整理一遍"），此时按大小规则把活动文件收缩到"只剩最后一条"是唯一
        确定、不含猜测的语义。**自动路径（``maybe_rotate``）永远走不到这里**
        ——没有触发因就直接返回，所以默认关闭的保守性不受影响。
        """
        if self._max_bytes > 0:
            return TRIGGER_SIZE
        if self._rotate_daily:
            return TRIGGER_DAY
        return TRIGGER_SIZE

    def maybe_rotate(self, *, now: Optional[float] = None) -> Dict[str, Any]:
        """按需轮转（**廉价**；由 ``append()`` 调用；绝不抛异常）

        检查顺序（从便宜到昂贵）：

            1. 未配置任何轮转 ⇒ 直接返回（默认配置下**零 syscall、零锁文件**）；
            2. 大小：一次 ``os.stat``，到阈值才真正轮转；
            3. 按日：**节流** ``daily_check_seconds`` 秒（要整读活动文件），
               窗口内不重复整读——否则每次 append 都读一遍会拖垮决策路径。

        Returns:
            轮转摘要（未轮转时 ``rotated=False`` + ``reason``）。
        """
        if not self.rotation_enabled:
            return self._rotation_summary(ROTATION_DISABLED)
        if self._max_bytes > 0 and self._active_size() >= self._max_bytes:
            return self.rotate(trigger=TRIGGER_SIZE)
        if self._rotate_daily and self._daily_check_due(now):
            # 先落节流时间戳：本轮无论有没有可归档记录，窗口内都不再整读
            self._daily_checked_at = time.monotonic() if now is None else float(now)
            return self.rotate(trigger=TRIGGER_DAY)
        return self._rotation_summary(ROTATION_BELOW_THRESHOLD)

    def rotate(self, *, force: bool = False, trigger: str = "") -> Dict[str, Any]:
        """执行一次轮转并返回摘要（**绝不抛异常**；运维/测试可显式调用）

        Args:
            force: True = 跳过阈值判定直接轮转（**运维显式动作**）。"哪些算旧记录"
                的规则：``max_bytes>0`` 走大小规则；否则 ``rotate_daily`` 走按日规则；
                两者都没配 ⇒ 按大小规则收缩到只剩最后一条（确定性语义，见
                ``_configured_rule``）。
            trigger: 触发因（``"size"`` / ``"day"``）；``""`` ⇒ 自行判定。

        Returns:
            摘要：``rotated`` / ``reason`` / ``records_moved`` / ``records_kept`` /
            ``shards_created`` / ``bytes_reclaimed`` / ``bytes_archived`` /
            ``active_bytes_*`` / ``error``。``reason`` 取值见 ``ROTATION_*``。
        """
        if self._closed:
            return self._finish_rotation(self._rotation_summary(ROTATION_CLOSED))
        if not self._enabled:
            return self._finish_rotation(self._rotation_summary(ROTATION_DISABLED))
        target = self._path
        if not target or shard_affixes(target) is None or os.path.isdir(target):
            # 分片名对读取端不可见 ⇒ 宁可不轮转（见 shard_affixes 的说明）
            return self._finish_rotation(
                self._rotation_summary(ROTATION_UNSUPPORTED_PATH))
        if not force and not trigger:
            trigger = self._pending_trigger()
            if not trigger:
                return self._finish_rotation(
                    self._rotation_summary(ROTATION_BELOW_THRESHOLD))
        if not trigger:
            trigger = self._configured_rule()
        if not os.path.exists(target):
            return self._finish_rotation(
                self._rotation_summary(ROTATION_NO_ACTIVE_FILE, trigger=trigger))
        if not self._acquire_xlock(self._rotate_lock_timeout):
            # **无锁即跳过**：没有锁的读-改-写会覆盖并发写入 ⇒ 宁可本轮不做
            self._rotation_skipped_locked += 1
            return self._finish_rotation(self._rotation_summary(
                ROTATION_LOCK_UNAVAILABLE, trigger=trigger,
                error="跨进程锁不可用（被占用或不可用）"))
        try:
            # 【为什么整个读-改-写都在 self._lock 内（线程安全，必须）】
            # 替换点会 ``_close_handle()``。若另一个线程此刻正通过同一个长期句柄
            # 写入，句柄会在它脚下被关掉 ⇒ 那条 append 抛 ValueError/OSError ⇒
            # **丢一条决策记录**。用同一把进程内可重入锁把轮转与 append 串行化，
            # 就不会出现"关掉别人正在用的句柄"。
            # 锁序恒为 self._lock → 跨进程锁（append 路径同序），不存在死锁；
            # 代价是轮转期间同进程 append 会等这次读-改-写结束（轮转低频，可接受）。
            with self._lock:
                summary = self._rotate_locked(trigger=trigger)
        except Exception as exc:  # noqa: BLE001 轮转**绝不**上抛
            self._note_rotation_failure(f"{type(exc).__name__}: {exc}")
            summary = self._rotation_summary(ROTATION_ERROR, trigger=trigger,
                                             error=f"{type(exc).__name__}: {exc}")
        finally:
            self._release_xlock()
        # 留痕/记录摘要在锁外做（日志处理器不该被卷进这把锁）
        return self._finish_rotation(summary)

    def _rotate_locked(self, *, trigger: str) -> Dict[str, Any]:
        """在**已持有跨进程锁**的前提下执行轮转

        顺序（**先归档、后截断**：崩溃只可能"多算"、不可能"少算"）：

            1. 快照整读活动文件（记下字节数 size_before）；
            2. 切出「搬走的旧行」与「留在活动文件的行」；
            3. 新内容写进**同目录临时文件**（``os.replace`` 要求同一文件系统）；
            4. 并发对账：把快照之后新到的**完整行**并入临时文件；
            5. 字节数校验：对不上 ⇒ 放弃（**活动文件一个字节都不动**）；
            6. 搬走的行按日 append 进分片（归档，永不 truncate）；
            7. 分片写入后再校验一次：变了 ⇒ 放弃替换（宁可下轮再来，也不覆盖并发写入）；
            8. ``os.replace`` 原子替换活动文件（temp 与目标同目录 ⇒ 同文件系统）。

        【残余风险（写明白，不藏）】第 7 步校验与第 8 步替换之间还有一个
        **微秒级**窗口；只有"取不到锁仍降级写入"的并发进程才可能落进去
        （持锁写入者被彻底串行化在外）。这种降级写已被计数
        （``stats['degraded_appends']``）并逐条告警，不是静默路径。
        """
        target = self._path
        size_before, lines = self._read_snapshot(target)
        moved, kept = self._split_lines(lines, trigger)
        if not moved:
            return self._rotation_summary(ROTATION_NOTHING_TO_MOVE, trigger=trigger,
                                          records_kept=len(kept),
                                          active_bytes_before=size_before,
                                          active_bytes_replaced=size_before,
                                          active_bytes_after=size_before)
        temp = ""
        try:
            temp = self._write_temp(target, kept)
            try:
                replaced_size = self._merge_concurrent_appends(target, temp, size_before)
            except _ConcurrentWrite as exc:
                self._rotation_skipped_concurrent += 1
                return self._rotation_summary(
                    ROTATION_CONCURRENT_WRITE, trigger=trigger,
                    records_kept=len(kept),
                    active_bytes_before=size_before,
                    active_bytes_replaced=size_before,
                    active_bytes_after=self._path_size(target),
                    error=str(exc))
            shards, created = self._archive_shards(target, moved)
            if self._path_size(target) != replaced_size:
                # 已归档、未替换：活动文件保持原样 ⇒ **一条不丢**；
                # 最坏结果是"分片里也有一份"（read 默认去重兜住），下轮再轮转。
                self._rotation_skipped_concurrent += 1
                return self._rotation_summary(
                    ROTATION_CONCURRENT_WRITE, trigger=trigger,
                    records_moved=len(moved), records_kept=len(kept),
                    shards=shards, shards_created=created,
                    bytes_archived=self._lines_bytes(moved),
                    active_bytes_before=size_before,
                    active_bytes_replaced=replaced_size,
                    active_bytes_after=self._path_size(target),
                    error="分片写入期间活动文件被并发追加；本轮放弃替换（不覆盖并发写入）")
            # 【必须在此刻作废长期句柄（不易，安全关键）】
            # Windows：目标文件仍被打开时 os.replace 直接失败（实测 WinError 5），
            #          不先关闭 ⇒ 本进程的轮转永远做不成；
            # POSIX：替换会成功，而长期句柄随后指向**被淘汰的 inode** ⇒ 之后的
            #        append 静默写进读不到的文件（正是本任务禁止的静默丢失）。
            # 替换成功后代际 +1：快路径下次命中句柄时必然发现代际不符而重开。
            # 代际只在**真正替换成功**后递增，故"代际 == 成功轮转次数"。
            self._close_handle()
            os.replace(temp, target)
            temp = ""
            self._rotation_generation += 1
            size_after = self._path_size(target)
            summary = self._rotation_summary(
                ROTATION_OK, trigger=trigger,
                records_moved=len(moved), records_kept=len(kept),
                shards=shards, shards_created=created,
                bytes_reclaimed=max(replaced_size - size_after, 0),
                bytes_archived=self._lines_bytes(moved),
                active_bytes_before=size_before,
                active_bytes_replaced=replaced_size,
                active_bytes_after=size_after)
            with self._lock:
                self._rotation_count += 1
                self._rotation_records += len(moved)
                self._rotation_shards_created += len(created)
                self._rotation_bytes_reclaimed += int(summary["bytes_reclaimed"])
            return summary
        finally:
            # 临时文件要么已被 os.replace 消费，要么在此清除（**不留半成品**）
            self._unlink(temp)

    @staticmethod
    def _lines_bytes(lines: List[bytes]) -> int:
        return sum(len(line) + 1 for line in lines)

    @staticmethod
    def _unlink(path: str) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    def _read_snapshot(self, target: str) -> Tuple[int, List[bytes]]:
        """整读活动文件：返回 ``(字节数, 行列表)``（**按字节读**，保证字节数可对账）"""
        with open(target, "rb") as handle:
            raw = handle.read()
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()          # 末行换行 ⇒ 去掉 split 出来的空元素
        return len(raw), lines

    def _split_lines(self, lines: List[bytes], trigger: str) -> Tuple[List[bytes], List[bytes]]:
        """切出 ``(搬走的旧行, 留在活动文件的行)``

        - ``day`` 规则：**ts 能定日**且早于今天的搬走；定不出日的一律留在活动文件
          （无法定日的记录不该被塞进某个"日期分片"里冒充历史）。
        - ``size`` 规则：从**文件末尾往回**累计到 ``effective_keep_bytes``，
          尾部留在活动文件，头部搬走。活动文件**至少保留最后一条**——
          空的活动文件会让下一次 append 立刻再顶穿阈值，形成抖动。
        """
        if trigger == TRIGGER_DAY:
            today = date.today().isoformat()
            moved: List[bytes] = []
            kept: List[bytes] = []
            for line in lines:
                day = line_day(line)
                (moved if (day and day < today) else kept).append(line)
            return moved, kept
        keep_bytes = self.effective_keep_bytes
        kept_count = 0
        kept_bytes = 0
        for line in reversed(lines):
            cost = len(line) + 1
            if kept_count > 0 and kept_bytes + cost > keep_bytes:
                break
            kept_bytes += cost
            kept_count += 1
        split = len(lines) - kept_count
        return lines[:split], lines[split:]

    def _write_temp(self, target: str, lines: List[bytes]) -> str:
        """把轮转后的活动文件内容写进**同目录**临时文件；返回临时文件路径

        【为什么 temp 名字不能以 ``.jsonl`` 结尾】``_candidate_files()`` 会把
        目录里一切 ``decisions.*.jsonl`` 当分片读——临时文件若命中该形态，
        读端就会读到"半成品"。这里用 ``decisions.jsonl.<随机>.tmp``，天然不匹配。
        """
        self._ensure_directory()
        directory = os.path.dirname(target) or "."
        fd, temp = tempfile.mkstemp(dir=directory,
                                    prefix=os.path.basename(target) + ".",
                                    suffix=TEMP_SUFFIX)
        try:
            with os.fdopen(fd, "wb") as handle:
                if lines:
                    handle.write(b"\n".join(lines) + b"\n")
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except OSError as exc:  # 某些文件系统不支持 fsync：不致命
                    logger.debug("决策日志轮转 fsync 失败（不致命）: %s", exc)
        except Exception:
            self._unlink(temp)
            raise
        return temp

    def _merge_concurrent_appends(self, target: str, temp: str, size0: int) -> int:
        """把快照之后新到的**完整行**并入临时文件；返回"已对账到的字节数"

        【为什么必须对账（否则轮转就是"静默丢记录"）】只认自己那份快照的读-改-写，
        会把任何落在"快照之后、替换之前"的并发 append 用 ``os.replace`` 覆盖掉。
        活动文件是 append-only，故按**字节偏移增量读**是安全的；只取到**最后一个
        换行**为止——半行（另一个进程正在写）绝不搬走，留它继续待在活动文件里，
        否则会把一条记录劈成两条脏记录。

        Raises:
            _ConcurrentWrite: 活动文件**缩小**（疑似被别的轮转者替换/截断，本进程不猜），
                增量里没有完整行（只有半行），或持续有并发写入导致对账超限。
        """
        for _attempt in range(MAX_ROTATE_REREAD):
            current = self._path_size(target)
            if current == size0:
                return size0
            if current < size0:
                raise _ConcurrentWrite(f"活动文件缩小（{size0}→{current}），疑似并发轮转")
            with open(target, "rb") as handle:
                handle.seek(size0)
                extra = handle.read()
            cut = extra.rfind(b"\n") + 1
            if cut <= 0:
                raise _ConcurrentWrite("并发写入只到半行为止；本轮放弃（不搬半行）")
            with open(temp, "ab") as handle:
                handle.write(extra[:cut])
            size0 += cut
        raise _ConcurrentWrite("并发写入持续存在，对账重试超限")

    def _archive_shards(self, target: str, moved: List[bytes]) -> Tuple[List[str], List[str]]:
        """按日把搬走的行 append 进分片；返回 ``(写出的分片, 新建的分片)``

        **归档而非删除**：一律 ``ab`` 追加，永不 truncate、永不 unlink；
        同名分片重复归档只是多一份副本（``read`` 默认去重），不会丢数据。
        """
        groups: Dict[str, List[bytes]] = {}
        for line in moved:
            groups.setdefault(shard_name(target, line_day(line)), []).append(line)
        written: List[str] = []
        created: List[str] = []
        for shard in sorted(groups):
            if not os.path.exists(shard):
                created.append(shard)
            with open(shard, "ab") as handle:
                handle.write(b"\n".join(groups[shard]) + b"\n")
                handle.flush()
                try:
                    # 分片必须**先于**活动文件替换落稳：否则崩溃会同时丢掉两边
                    os.fsync(handle.fileno())
                except OSError as exc:  # noqa: BLE001
                    logger.debug("分片 fsync 失败（不致命）: %s", exc)
            written.append(shard)
        return written, created

    def rotated_shards(self, path: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出已归档分片（运维可见性；**按名字升序 = 日期升序**）

        只看文件名 + ``stat``，**不读内容**（为了看一眼清单而整读全部历史是浪费）；
        要按条数审计就用 ``read()``——它会把活动文件与全部分片一并读回。
        """
        target = str(path if path is not None else self._path)
        if not target or os.path.isdir(target) or shard_affixes(target) is None:
            return []
        shards: List[Dict[str, Any]] = []
        for full in self._candidate_files(target):
            if os.path.abspath(full) == os.path.abspath(target):
                continue
            try:
                if not os.path.isfile(full):
                    continue
                info = os.stat(full)
            except OSError:
                continue
            shards.append({"path": full, "name": os.path.basename(full),
                           "day": shard_day(full), "bytes": int(info.st_size),
                           "mtime": float(info.st_mtime)})
        return sorted(shards, key=lambda item: item["name"])

    # ── 读取（模拟器） ──

    def read(
        self,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        since_days: Optional[float] = None,
        limit: Optional[int] = None,
        path: Optional[str] = None,
        dedupe: bool = True,
    ) -> List[DecisionRecord]:
        """读回决策记录（**时间序**）

        Args:
            since/until: ISO 前缀比较（与事件层 ``iter_events`` 同口径）。
            since_days: 相对「现在」的滚动窗口（天，可为小数）——P7.2-19 的 ``--since 7d``。
            limit: 只保留**最近** limit 条（按 ``ts`` 时间序取尾部；同 ``ts`` 时
                活动文件的记录先于分片，见 ``chronological`` 的平局规则）。
            path: 覆盖读取路径（不启动 writer 也能读）。
            dedupe: 按 ``(ts, input, policy_id, effect)`` 去重（重复 append 不重复计数）。

        【为什么这里必须排序（TASK-S8-02 步骤 4 修掉的排序缺陷）】
            ``iter_records`` 的**文件顺序**是 ``[活动文件, 分片...]``——最新数据在最前。
            只有活动文件时它恰好等于时间序；一旦轮转出分片，``records[-N:]`` 取的
            就是**最旧分片**的记录，与"最近 limit 条"（以及本方法承诺的时间序）相反。
            故在 ``read()`` 内对物化结果做一次**稳定排序**（``chronological``），
            ``iter_records`` 保持流式契约不变（它不承诺时间序，只承诺"逐行不丢"）。
        """
        records = list(self.iter_records(since=since, until=until,
                                         since_days=since_days, path=path,
                                         dedupe=dedupe))
        records = chronological(records)
        if limit is not None and limit >= 0:
            records = records[-int(limit):] if limit else []
        return records

    def iter_records(
        self,
        *,
        since: Optional[str] = None,
        until: Optional[str] = None,
        since_days: Optional[float] = None,
        path: Optional[str] = None,
        dedupe: bool = True,
    ) -> Iterator[DecisionRecord]:
        """**流式**逐条 yield 记录（文件顺序：``[活动文件, 分片...]``；**不承诺时间序**）

        时间序是 ``read()`` 的保证（见其文档）；本方法刻意保持流式——超大日志
        可以边读边处理而不物化整个结果集。

        【整片读不回必须可见（TASK-S8-02 步骤 4 的 B 项）】此前这里
        ``except OSError: continue``——一个分片不可读（权限/被杀/被锁）就**静默
        跳过整片记录**：统计会在无人知晓的情况下少算一大截。现在改为
        **告警 + 计数**（``stats['unreadable_shards']``）：缺数据是事实，
        但绝不允许是"没人知道的事实"。
        """
        target = str(path if path is not None else self._path)
        if since_days is not None:
            cutoff = (datetime.now().astimezone()
                      - timedelta(days=float(since_days))).isoformat(
                          timespec="milliseconds")
            if not since or cutoff > since:
                since = cutoff
        files = self._candidate_files(target)
        seen: set = set()
        for full in files:
            if not os.path.exists(full):
                continue
            try:
                with open(full, "r", encoding="utf-8", errors="ignore") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line or len(line) > MAX_LINE_BYTES:
                            continue
                        try:
                            data = json.loads(line)
                        except (json.JSONDecodeError, ValueError):
                            continue
                        if not isinstance(data, dict):
                            continue
                        record = DecisionRecord.from_dict(data)
                        if since and record.ts and record.ts < str(since):
                            continue
                        if until and record.ts and record.ts > str(until):
                            continue
                        if dedupe:
                            key = (record.ts, canonical_json(record.input),
                                   record.policy_id, record.effect)
                            if key in seen:
                                continue
                            seen.add(key)
                        yield record
            except OSError as exc:
                self._unreadable_shards += 1
                logger.warning(
                    "决策日志分片不可读，**整片记录将缺失（统计因此不完整）**: %s: %s",
                    full, exc)
                continue

    @staticmethod
    def _candidate_files(target: str) -> List[str]:
        """活动文件 + 同目录轮转分片（**同时识别点号与连字符两种命名**）

        【为什么必须两种都认（跨任务实测缺陷，S8-01 × S8-02 缝合处）】
        本模块自己的轮转产出**点号**分片 ``decisions.<YYYYMMDD>.jsonl``，
        于是读侧的门槛一度被写成"必须 ``startswith(stem + ".")``"。
        但**归档不止本模块一处**：S8-01 数据生命周期治理把
        ``policy_decisions`` 声明为 ``reader_shard_aware=True`` +
        ``ARCHIVE_WARM_DAILY``，而温层**复用既有的**
        ``agent/skills_mgmt/log_archiver.archive_daily_file``——该函数产出的是
        **连字符**形态 ``decisions-YYYYMMDD.jsonl``。

        两者一旦相遇，后果是**静默的口径漂移**：实测把 3 天决策（2 条历史日 +
        1 条今日）交给 ``archive_daily_file`` 后，
        ``read()`` 从 **3 条变成 1 条**（``_candidate_files`` 只返回活动文件），
        即"归档成功、统计凭空少 2/3，而没有任何报错"。
        S8-01 侧的指标复算校验的是 ``utc.weekly`` / ``digestion.throughput`` /
        ``audit.chain``，**不覆盖** ``policy.decision_audit``，故那条路查不出来；
        本任务验收项「读分片仍可用 / 轮转前后口径一致」正是该处的守卫。

        【口径】点号形态（本模块轮转）**任意后缀**都收，因为它是"同一族的兄弟文件"；
        连字符形态**只收 ``<stem>-<8位数字><ext>``**（``log_archiver`` 的既定格式），
        以免把 ``decisions-backup.jsonl``、``decisions-old.jsonl`` 这类人工副本
        误当分片读进来（那会**多**读数据，同样是口径漂移）。
        """
        if not target:
            return []
        if os.path.isdir(target):
            return [os.path.join(target, name)
                    for name in sorted(os.listdir(target)) if name.endswith(".jsonl")]
        directory = os.path.dirname(target) or "."
        base = os.path.basename(target)
        files = [target]
        stem, _, ext = base.rpartition(".")
        if os.path.isdir(directory):
            try:
                for name in sorted(os.listdir(directory)):
                    if name == base or not name.endswith("." + ext):
                        continue
                    if not stem:
                        continue
                    if name.startswith(stem + "."):
                        # 本模块轮转的点号分片（如 decisions.20260912.jsonl）
                        files.append(os.path.join(directory, name))
                    elif _is_daily_hyphen_shard(name, stem, ext):
                        # log_archiver / S8-01 温层的连字符按日分片
                        files.append(os.path.join(directory, name))
            except OSError:
                pass
        return files

    # ── 生命周期 ──

    def flush(self) -> bool:
        """刷盘（每次 append 都已 ``flush``；长期句柄存在时再刷一次）

        语义与改动前一致：没有句柄（未写过 / 关闭后）返回 ``True``——因为
        "每条都已 flush" 是**确定事实**，不是"没句柄所以假装成功"。
        """
        with self._lock:
            handle = self._handle
            if handle is None:
                return True
            try:
                handle.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                self._note_failure(f"{type(exc).__name__}: {exc}")
                return False

    def close(self) -> None:
        """关闭日志（幂等；此后 append 一律返回 False）；同时关闭长期句柄

        轮转**不在 close 时触发**：close 只做"停写"这一件确定的事，
        不做可能失败/耗时的磁盘整理（整理交给 ``maybe_rotate``/``rotate``）。
        """
        with self._lock:
            self._closed = True
            self._close_handle()

    def __enter__(self) -> "DecisionLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = [
    "ENV_DECISION_LOG", "ENV_DECISION_LOG_ENABLED", "DEFAULT_DECISION_LOG",
    # 轮转配置（TASK-S8-02 步骤 4）
    "ENV_DECISION_LOG_MAX_BYTES", "ENV_DECISION_LOG_ROTATE_DAILY",
    "ENV_DECISION_LOG_KEEP_BYTES", "ENV_DECISION_LOG_LOCK_APPENDS",
    "ENV_DECISION_LOG_DAILY_CHECK_SECONDS",
    "DEFAULT_MAX_BYTES", "DEFAULT_KEEP_BYTES", "DEFAULT_ROTATE_DAILY",
    "DEFAULT_APPEND_LOCK_TIMEOUT", "DEFAULT_ROTATE_LOCK_TIMEOUT",
    "DEFAULT_DAILY_CHECK_SECONDS", "MAX_ROTATE_REREAD", "HANDLE_RECHECK_EVERY",
    "SHARD_SUFFIX_UNDATED", "TEMP_SUFFIX",
    "TRIGGER_SIZE", "TRIGGER_DAY",
    "ROTATION_OK", "ROTATION_DISABLED", "ROTATION_CLOSED",
    "ROTATION_BELOW_THRESHOLD", "ROTATION_LOCK_UNAVAILABLE",
    "ROTATION_NO_ACTIVE_FILE", "ROTATION_UNSUPPORTED_PATH",
    "ROTATION_NOTHING_TO_MOVE", "ROTATION_CONCURRENT_WRITE", "ROTATION_ERROR",
    # 轮转纯函数（分片命名 / 时间序；用例直接钉口径）
    "valid_day", "shard_affixes", "shard_name", "shard_day",
    "ts_order_key", "chronological", "line_day",
    "MAX_LINE_BYTES", "DecisionLogError", "DecisionRecord", "DecisionLog",
]
