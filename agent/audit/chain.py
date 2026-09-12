"""链式审计核心（CloudPivot v7.2 §3.5：AuditLog 链式哈希 + 每日 Merkle 根）

【任务定位】
    把云枢原有的「可追加 JSONL 审计」（`agent/audit/logger.py`，仅 sha256 摘要、
    无 prev_hash 链）升级为**防篡改链式审计**：每条记录含
    ``seq / ts / actor / action / subject / payload_hash / prev_hash / self_hash``，
    追加即入链，改中间任意一条即破坏后续全部 self_hash。

【哈希公式（与 §3.5 逐字一致）】
    self_hash = sha256(seq + ts + actor + action + subject + payload_hash + prev_hash)

    规范化签名字符串（`self_hash_formula`）::

        f"{seq}|{ts}|{actor}|{action}|{subject}|{payload_hash}|{prev_hash}"

    payload_hash = sha256(规范化记录 JSON)（`canonical_record_json`），待签载荷为
    **整条记录的规范化表示**（含 source / trace_id / workspace_id / schema_version /
    payload），故除 §3.5 七字段链式校验外，改 payload / 元数据列同样会被检出——见
    `verify_chain` 的「两级校验」。这是对 §3.5 的**加强**，不改变 self_hash 公式本身。

【单写者纪律（§5.5）】
    - 进程内每个 DB 路径**唯一 Ledger writer**：`AuditChain` 构造时登记路径占用，
      重复构造（未 close）抛 `SingleWriterViolationError`；同一路径请用
      `get_audit_chain()` 复用进程单例。
    - seq 分配在 `append()` 的互斥锁内完成（调用线程串行获得），因此链序 = seq 序；
      持久化由后台 writer 线程**批量**执行，`append()` 只做哈希 + 入队（<5ms）。
    - Scheduler / Watchdog / 后台线程走 `role="reader"` 只读实例，或向唯一 writer
      实例 `append()`（进程内 IPC 提交），不得自行开第二个 writer。

【存储】
    SQLite（默认 `data/audit/audit_chain.db`，独立 audit 库；WAL + synchronous=FULL，
    审计优先耐久而非吞吐）。表 `audit_chain` **只 INSERT**（append-only）；
    `clear()` 为测试专用显式动作（全模块唯一 DELETE）。

【每日 Merkle 根 + 单机降级】
    `AuditChain.daily_merkle_root(date)`：当日 entries 建 Merkle 树，根哈希写入
    `data/audit/daily_roots.jsonl`（受保护：追加即 chmod 只读，追加前临时恢复写权限），
    记录含 date / root_hash / leaf_count / 首尾 self_hash / 签名 / 降级说明。
    签名优先 ed25519（`cryptography` 可用且有/可生成密钥）；无密钥或库缺失时降级为
    sha256 自签占位并**显式记录降级**（对齐 P4 分级实施：外部只追加存储入 P5 Backlog）。

【验签】
    `verify_chain()`（模块函数 / `AuditChain.verify_chain()`）：从任一锚点重算全部
    self_hash + payload_hash + prev_hash 链接 + seq 连续性，报告**首个**篡改位置，
    供混沌演练（§11.10「向审计链注入一条篡改」）与 `scripts/verify_audit_chain.py` 使用。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue as queue_module
import sqlite3
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import (Any, Deque, Dict, Iterable, Iterator, List, Optional, Sequence,
                    Tuple)

from agent.audit.seq_journal import (
    DEFAULT_RETAIN_RECORDS,
    SeqJournal,
    SeqJournalError,
)
from agent.utils.cross_process_lock import (
    PROCESS_TOKEN,
    CrossProcessLock,
    LockUnavailable,
    lock_path_for,
)

logger = logging.getLogger("agent.audit.chain")

# ════════════════════════════════════════════════════════════
#  路径与常量
# ════════════════════════════════════════════════════════════

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 链式审计台账（独立 audit 库；与 tool_trace.db 分开，审计链不与轨迹争锁）
DEFAULT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "audit", "audit_chain.db")
#: 每日 Merkle 根（受保护追加文件）
DEFAULT_ROOTS_PATH = os.path.join(_PROJECT_ROOT, "data", "audit", "daily_roots.jsonl")
#: ed25519 签名私钥（PKCS8 PEM；首次使用自动生成）
DEFAULT_KEY_PATH = os.path.join(_PROJECT_ROOT, "data", "audit", "audit_signing_key.pem")

SCHEMA_VERSION = 1
HASH_ALGO = "sha256"
#: 创世前驱哈希（首条记录的 prev_hash）
GENESIS_PREV_HASH = "0" * 64

WRITER_BATCH_SIZE = 100
WRITER_POLL_INTERVAL = 0.5
FLUSH_TIMEOUT = 5.0
RING_BUFFER_MAXLEN = 2000
#: 每轮后台自动封存最多处理的天数（防「首个记录 ts 很旧 → 数千空日」无限封存）
AUTO_SEAL_MAX_DAYS = 8

# ── S8-02：跨进程 seq 分配（见 `agent/audit/seq_journal.py`）────────────────
#: 预留日志后缀（``audit_chain.db`` → ``audit_chain.db.seqjournal``）
JOURNAL_SUFFIX = ".seqjournal"
#: 跨进程锁后缀（**锁独立文件**：锁被保护文件本体时，rename 原子写会让锁落到旧 inode）
LOCK_SUFFIX = ".lock"
#: 取锁的有限等待上限（秒）。超出即**显式降级**（回落 DB 事务分配 + 计数留痕）。
#:
#: 【为什么是 5s 而不是 2s（实测缺陷）】初版取 2s，而当时锁内还夹着 DB 工作
#: （``synchronous=FULL`` 提交在 4 进程争用下可被 ``busy_timeout`` 拖到数秒），
#: 于是正常并发就会被误判为"锁不可得"⇒ 走降级 ⇒ 重复 seq（实测
#: ``duplicates=[80]``）。现在锁内**不再做 DB I/O**，临界区只有内存计算 +
#: 一次日志写（无 fsync），远低于 5s；保留 5s 是为了容忍调度抖动与
#: ``os.replace``/杀毒软件等外部停顿。
DEFAULT_SEQ_LOCK_TIMEOUT = 5.0
#: 入库队列上限。**有界**才谈得上"队列满"有明确行为（见 `_note_queue_full`）：
#: 无界队列把"内存被写爆"伪装成"永不失败"，是更坏的静默。
DEFAULT_QUEUE_MAXSIZE = 20000
#: 预留日志压缩的最小行数闸（同时受 ``max(2*retain, 本值)`` 约束，见 chain 注释）
DEFAULT_COMPACT_MIN_ROWS = 1024
#: 预留日志压缩检查的最小时间间隔（秒）：避免每轮都去打 DB 的 ``MAX(seq)``
COMPACT_CHECK_INTERVAL_S = 5.0

#: 审计来源（P7.2-24 审计平权：UI 与 Agent 同表）
SOURCE_AGENT = "agent"
SOURCE_UI = "ui"
SOURCE_SYSTEM = "system"
SOURCE_MIGRATION = "migration"
SOURCES = frozenset({SOURCE_AGENT, SOURCE_UI, SOURCE_SYSTEM, SOURCE_MIGRATION})

#: Merkle 算法标识
MERKLE_ALGO = "sha256-merkle-v1"
#: 空日根（无可审计记录时，仍留根以证明「当日无新增」）
EMPTY_MERKLE_ROOT = hashlib.sha256(b"").hexdigest()

_SIGN_SCHEME_ED25519 = "ed25519"
_SIGN_SCHEME_SHA256_SELF = "sha256-self"
_DEGRADED_NO_KEY = "no_signing_key（无外部 Keychain/密钥，P4 分级实施降级为 sha256 自签占位）"


class AuditChainError(Exception):
    """链式审计异常基类"""


class SingleWriterViolationError(AuditChainError):
    """同一 DB 路径已存在活跃 writer（§5.5 单写者纪律）"""


class ReadOnlyChainError(AuditChainError):
    """只读实例调用了写入接口"""


class AuditEntryError(AuditChainError):
    """记录字段非法（如 seq 非正、action 为空）"""


# ════════════════════════════════════════════════════════════
#  哈希原语（§3.5）
# ════════════════════════════════════════════════════════════


def sha256_hex(data: str) -> str:
    """sha256(utf-8) → 64 位十六进制小写"""
    return hashlib.sha256(str(data).encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    """当前 UTC 时刻（ISO-8601，微秒精度，+00:00 后缀）——ts 的规范化文本形式"""
    return datetime.now(timezone.utc).isoformat()


def normalize_ts(ts: Any) -> str:
    """把 ts 规范化为可哈希的 ISO-8601 UTC 字符串

    - None → 当前 UTC；
    - datetime（naive 视为 UTC）→ ISO-8601 UTC；
    - 数值 → 视为 epoch 秒；
    - 字符串 → 原样（假定调用方给的是规范形式）。
    """
    if ts is None:
        return utc_now_iso()
    if isinstance(ts, datetime):
        dt = ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    return str(ts)


def day_of_ts(ts: str) -> str:
    """从规范化 ts 取 UTC 日期（YYYY-MM-DD）"""
    return str(ts)[:10]


def canonical_json(data: Any) -> str:
    """规范化 JSON：sort_keys + 紧凑分隔符 + 不转义非 ASCII（同构对象恒同串）"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def canonical_record_json(*, seq: int, ts: str, actor: str, action: str, subject: str,
                          source: str, trace_id: str, workspace_id: str,
                          schema_version: int, payload: Any) -> str:
    """待签载荷的规范化表示（payload_hash 的输入）

    含 §3.5 七字段中的六个（prev_hash 由 self_hash 承载，payload_hash 自身除外），
    并额外绑定 source / trace_id / workspace_id / schema_version / payload，
    使元数据与载荷一并受链保护。
    """
    return canonical_json({
        "seq": int(seq),
        "ts": str(ts),
        "actor": str(actor),
        "action": str(action),
        "subject": str(subject),
        "source": str(source),
        "trace_id": str(trace_id),
        "workspace_id": str(workspace_id),
        "schema_version": int(schema_version),
        "payload": payload if payload is not None else {},
    })


def compute_payload_hash(*, seq: int, ts: str, actor: str, action: str, subject: str,
                         source: str = SOURCE_AGENT, trace_id: str = "",
                         workspace_id: str = "", schema_version: int = SCHEMA_VERSION,
                         payload: Any = None) -> str:
    """payload_hash = sha256(规范化记录 JSON)"""
    return sha256_hex(canonical_record_json(
        seq=seq, ts=ts, actor=actor, action=action, subject=subject, source=source,
        trace_id=trace_id, workspace_id=workspace_id, schema_version=schema_version,
        payload=payload))


def self_hash_formula(*, seq: int, ts: str, actor: str, action: str, subject: str,
                      payload_hash: str, prev_hash: str) -> str:
    """§3.5 self_hash 待签串：seq+ts+actor+action+subject+payload_hash+prev_hash

    分隔符 `|` 固定；字段中若含 `|` 不会造成歧义（仅影响可读性，不影响抗篡改强度，
    因为各字段同时受 payload_hash 的规范化 JSON 绑定约束）。
    """
    return "|".join([str(int(seq)), str(ts), str(actor), str(action),
                     str(subject), str(payload_hash), str(prev_hash)])


def compute_self_hash(*, seq: int, ts: str, actor: str, action: str, subject: str,
                      payload_hash: str, prev_hash: str) -> str:
    """self_hash = sha256(seq+ts+actor+action+subject+payload_hash+prev_hash)"""
    return sha256_hex(self_hash_formula(
        seq=seq, ts=ts, actor=actor, action=action, subject=subject,
        payload_hash=payload_hash, prev_hash=prev_hash))


# ════════════════════════════════════════════════════════════
#  AuditEntry（§3.5 记录模型）
# ════════════════════════════════════════════════════════════

#: DB 列（顺序即 INSERT 顺序）
_COLUMNS: Tuple[str, ...] = (
    "seq", "ts", "actor", "action", "subject", "payload_hash", "prev_hash",
    "self_hash", "source", "trace_id", "workspace_id", "schema_version", "payload",
)


@dataclass
class AuditEntry:
    """一条链式审计记录（§3.5）

    链式字段（参与哈希）：seq / ts / actor / action / subject / payload_hash /
    prev_hash / self_hash；元数据列（受 payload_hash 绑定）：source / trace_id /
    workspace_id / schema_version / payload。
    """

    seq: int
    ts: str
    actor: str
    action: str
    subject: str = ""
    payload_hash: str = ""
    prev_hash: str = GENESIS_PREV_HASH
    self_hash: str = ""
    source: str = SOURCE_AGENT
    trace_id: str = ""
    workspace_id: str = ""
    schema_version: int = SCHEMA_VERSION
    #: 脱敏后的载荷（已 JSON 规范化；原文不入链）
    payload: Dict[str, Any] = field(default_factory=dict)
    #: DB 行号（仅读路径填充；不参与哈希）
    id: int = 0

    # ── 构造/校验 ────────────────────────────────────────────

    def __post_init__(self) -> None:
        self.seq = int(self.seq)
        self.ts = str(self.ts)
        self.actor = str(self.actor or SOURCE_SYSTEM)
        self.action = str(self.action or "")
        self.subject = str(self.subject or "")
        self.source = str(self.source or SOURCE_AGENT)
        self.trace_id = str(self.trace_id or "")
        self.workspace_id = str(self.workspace_id or "")
        self.schema_version = int(self.schema_version or SCHEMA_VERSION)
        if self.payload is None:
            self.payload = {}
        if not isinstance(self.payload, dict):
            # 载荷统一为 dict（保证规范化 JSON 稳定；非 dict 走 {"value": ...}）
            self.payload = {"value": self.payload}

    def validate(self) -> List[str]:
        """域级校验 → 问题清单（空 = 合法）"""
        problems: List[str] = []
        if self.seq <= 0:
            problems.append("seq 必须为正整数")
        if not self.action:
            problems.append("action 不能为空")
        if not self.ts:
            problems.append("ts 不能为空")
        return problems

    # ── 哈希 ────────────────────────────────────────────────

    def canonical_record(self) -> str:
        return canonical_record_json(
            seq=self.seq, ts=self.ts, actor=self.actor, action=self.action,
            subject=self.subject, source=self.source, trace_id=self.trace_id,
            workspace_id=self.workspace_id, schema_version=self.schema_version,
            payload=self.payload)

    def recompute_payload_hash(self) -> str:
        return sha256_hex(self.canonical_record())

    def recompute_self_hash(self) -> str:
        return compute_self_hash(
            seq=self.seq, ts=self.ts, actor=self.actor, action=self.action,
            subject=self.subject, payload_hash=self.payload_hash,
            prev_hash=self.prev_hash)

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """扁平字典（含 id，便于展示/JSON 输出）"""
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditEntry":
        payload = data.get("payload") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload) if payload else {}
            except (ValueError, TypeError):
                payload = {"_raw": payload}
        return cls(
            seq=int(data.get("seq") or 0),
            ts=str(data.get("ts") or ""),
            actor=str(data.get("actor") or ""),
            action=str(data.get("action") or ""),
            subject=str(data.get("subject") or ""),
            payload_hash=str(data.get("payload_hash") or ""),
            prev_hash=str(data.get("prev_hash") or GENESIS_PREV_HASH),
            self_hash=str(data.get("self_hash") or ""),
            source=str(data.get("source") or SOURCE_AGENT),
            trace_id=str(data.get("trace_id") or ""),
            workspace_id=str(data.get("workspace_id") or ""),
            schema_version=int(data.get("schema_version") or SCHEMA_VERSION),
            payload=payload if isinstance(payload, dict) else {"value": payload},
            id=int(data.get("id") or 0),
        )

    def to_public_dict(self) -> Dict[str, Any]:
        """对外摘要（不含完整 payload，供 CLI 默认输出/日志）"""
        d = self.to_dict()
        d.pop("payload", None)
        return d

    # ── DB 行 ───────────────────────────────────────────────

    def row_values(self) -> Tuple[Any, ...]:
        """DB 行值：payload 以规范化 JSON 文本落库，读回重新规范化恒等（哈希稳定）"""
        return (
            self.seq, self.ts, self.actor, self.action, self.subject,
            self.payload_hash, self.prev_hash, self.self_hash, self.source,
            self.trace_id, self.workspace_id, self.schema_version,
            canonical_json(self.payload),
        )

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuditEntry":
        raw = row["payload"]
        try:
            payload = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            payload = {"_raw": raw}
        return cls(
            seq=int(row["seq"]), ts=row["ts"], actor=row["actor"],
            action=row["action"], subject=row["subject"],
            payload_hash=row["payload_hash"], prev_hash=row["prev_hash"],
            self_hash=row["self_hash"], source=row["source"],
            trace_id=row["trace_id"], workspace_id=row["workspace_id"],
            schema_version=int(row["schema_version"]),
            payload=payload if isinstance(payload, dict) else {"value": payload},
            id=int(row["id"]),
        )


def build_entry(*, seq: int, ts: str, actor: str, action: str, subject: str = "",
                payload: Optional[Dict[str, Any]] = None, prev_hash: str,
                source: str = SOURCE_AGENT, trace_id: str = "",
                workspace_id: str = "",
                schema_version: int = SCHEMA_VERSION) -> AuditEntry:
    """按 §3.5 公式构造一条完整（含 payload_hash/self_hash）的记录"""
    entry = AuditEntry(
        seq=seq, ts=ts, actor=actor, action=action, subject=subject,
        prev_hash=prev_hash, source=source, trace_id=trace_id,
        workspace_id=workspace_id, schema_version=schema_version,
        payload=payload or {},
    )
    entry.payload_hash = entry.recompute_payload_hash()
    entry.self_hash = entry.recompute_self_hash()
    return entry


# ════════════════════════════════════════════════════════════
#  验签（verify_chain）
# ════════════════════════════════════════════════════════════

#: 篡改/异常原因码
REASON_OK = "ok"
REASON_EMPTY = "empty"
REASON_SEQ_GAP = "seq_not_monotonic"
REASON_PREV_HASH = "prev_hash_mismatch"
REASON_SELF_HASH = "self_hash_mismatch"
REASON_PAYLOAD_HASH = "payload_hash_mismatch"
REASON_ANCHOR = "anchor_not_found"
REASON_INVALID_ENTRY = "invalid_entry"

_REASON_TEXT = {
    REASON_OK: "链完整",
    REASON_EMPTY: "无记录（空链）",
    REASON_SEQ_GAP: "seq 不连续（记录被删除或插入）",
    REASON_PREV_HASH: "prev_hash 与上一条 self_hash 不一致（链断裂）",
    REASON_SELF_HASH: "self_hash 重算不一致（本条记录字段被篡改）",
    REASON_PAYLOAD_HASH: "payload_hash 重算不一致（载荷/元数据被篡改）",
    REASON_ANCHOR: "锚点 seq 不存在",
    REASON_INVALID_ENTRY: "记录字段非法",
}


@dataclass
class ChainVerification:
    """`verify_chain` 结果：OK 或首个篡改位置（外带全部失败位置）"""

    ok: bool
    checked: int = 0
    first_bad_seq: Optional[int] = None
    reason: str = REASON_OK
    detail: str = ""
    head_seq: int = 0
    head_self_hash: str = ""
    anchor_seq: Optional[int] = None
    anchor_prev_hash: str = ""
    #: 全部异常位置（[{seq, reason, detail}]，首条即注入点；篡改点之后因哈希前向传播
    #: 而「后续全部失败」，本列表即该证据）
    bad_seqs: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """人类可读单行结论（CLI 使用）"""
        if self.ok:
            return (f"OK — 链完整，已校验 {self.checked} 条"
                    f"（seq 1..{self.head_seq}，链头 self_hash={self.head_self_hash[:16]}…）")
        return (f"TAMPERED — 首个异常 seq={self.first_bad_seq}"
                f"（{_REASON_TEXT.get(self.reason, self.reason)}）: {self.detail}"
                f"；异常共 {len(self.bad_seqs)} 处"
                f"（注入点之后因哈希前向传播全部失败）")


#: 单条记录的最大异常数（防极端情况下结果膨胀）
_MAX_BAD_RECORDS = 10000


def verify_chain(entries: Sequence[AuditEntry], *, anchor_seq: Optional[int] = None,
                 anchor_prev_hash: str = GENESIS_PREV_HASH,
                 expect_first_seq: Optional[int] = None) -> ChainVerification:
    """从任一锚点重算全部 self_hash 并比对，报告首个篡改位置（§3.5/§11.10）

    每条记录四道校验：
      1. 字段合法性（seq>0、action/ts 非空）；
      2. seq 连续性（首条等于锚点 seq，其后逐条 +1）；
      3. prev_hash == **前一条的重算 self_hash**；
      4. **两级哈希**：payload_hash 重算（载荷/元数据级）+ self_hash 重算（链级）。

    关键：链前驱取「重算值」而非「存储值」前向传播——因此改中间任意一条，
    **该条及其后续全部记录**都会校验失败（`bad_seqs` 逐条列出），首条即注入点。

    Note:
        链前驱使用**重算值**（而非存储值）前向传播，故「改中间任意一条 → 该条与
        其后全部记录校验失败」（`bad_seqs` 逐条列出），首条即注入点。

    Args:
        entries: 按 seq 升序排列的记录（DB 读取顺序）。
        anchor_seq: 锚点 seq；给定时首条记录 seq 必须等于它。
        anchor_prev_hash: 锚点处期望的前驱哈希（默认创世哈希）。
        expect_first_seq: 期望首条 seq（覆盖 anchor_seq 的默认推断）。

    Returns:
        ChainVerification：ok=True 或 first_bad_seq=首个篡改位置 + bad_seqs 全量异常。
    """
    if not entries:
        return ChainVerification(ok=True, checked=0, reason=REASON_EMPTY,
                                 detail="空链：无记录可校验",
                                 anchor_seq=anchor_seq,
                                 anchor_prev_hash=anchor_prev_hash)

    first_seq_expected = expect_first_seq
    if first_seq_expected is None:
        first_seq_expected = anchor_seq if anchor_seq is not None else entries[0].seq

    running_prev = anchor_prev_hash
    bad: List[Dict[str, Any]] = []
    first_bad_seq: Optional[int] = None
    first_reason = REASON_OK
    first_detail = ""
    checked = 0

    for idx, entry in enumerate(entries):
        expect_seq = first_seq_expected + idx
        recomputed_payload = entry.recompute_payload_hash()
        # 链前驱用「重算的 self_hash」前向传播（链一致重算）：一旦某条被改，
        # 该条之后的**全部**记录 self_hash 重算值都与存储值不符 → 后续全部失败
        recomputed_self = compute_self_hash(
            seq=entry.seq, ts=entry.ts, actor=entry.actor, action=entry.action,
            subject=entry.subject, payload_hash=entry.payload_hash,
            prev_hash=running_prev)

        reason, detail = "", ""
        if entry.seq != expect_seq:
            reason = REASON_SEQ_GAP
            detail = (f"期望 seq={expect_seq}，实际 seq={entry.seq}"
                      f"（缺 {expect_seq} 或多出 {entry.seq}）")
        else:
            problems = entry.validate()
            if problems:
                reason = REASON_INVALID_ENTRY
                detail = "; ".join(problems)
            elif entry.prev_hash != running_prev:
                reason = REASON_PREV_HASH
                detail = (f"prev_hash={entry.prev_hash[:16]}… ≠ 上一条重算 self_hash="
                          f"{running_prev[:16]}…（链断裂）")
            elif recomputed_payload != entry.payload_hash:
                reason = REASON_PAYLOAD_HASH
                detail = (f"payload_hash 重算={recomputed_payload[:16]}… ≠ 存储="
                          f"{entry.payload_hash[:16]}…（载荷或元数据被改）")
            elif recomputed_self != entry.self_hash:
                reason = REASON_SELF_HASH
                detail = (f"self_hash 重算={recomputed_self[:16]}… ≠ 存储="
                          f"{entry.self_hash[:16]}…（链式篡改：本条字段被改）")

        if reason:
            if first_bad_seq is None:
                first_bad_seq = entry.seq
                first_reason = reason
                first_detail = detail
            if len(bad) < _MAX_BAD_RECORDS:
                bad.append({"seq": entry.seq, "reason": reason, "detail": detail})
        else:
            checked += 1

        running_prev = recomputed_self

    if first_bad_seq is None:
        return ChainVerification(ok=True, checked=len(entries),
                                 head_seq=entries[-1].seq,
                                 head_self_hash=entries[-1].self_hash,
                                 anchor_seq=first_seq_expected,
                                 anchor_prev_hash=anchor_prev_hash,
                                 bad_seqs=[])

    return ChainVerification(
        ok=False, checked=checked, first_bad_seq=first_bad_seq, reason=first_reason,
        detail=first_detail, head_seq=entries[-1].seq,
        head_self_hash=entries[-1].self_hash, anchor_seq=first_seq_expected,
        anchor_prev_hash=anchor_prev_hash, bad_seqs=bad)


# ════════════════════════════════════════════════════════════
#  Merkle 树（每日根 + 成员证明）
# ════════════════════════════════════════════════════════════


def merkle_root(leaves: Sequence[str]) -> str:
    """对叶子哈希序列建 Merkle 树 → 根哈希

    规则（确定性，跨实现可重放）：
      - 空序列 → `EMPTY_MERKLE_ROOT`（sha256("")）；
      - 相邻两叶 `sha256(left_hex + right_hex)`；
      - 奇数个节点时末节点**直接上提**（不复制），避免与偶数情形歧义。
    """
    level: List[str] = [str(h) for h in leaves]
    if not level:
        return EMPTY_MERKLE_ROOT
    while len(level) > 1:
        nxt: List[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(sha256_hex(level[i] + level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0]


def merkle_proof(leaves: Sequence[str], index: int) -> List[Dict[str, str]]:
    """生成第 index 个叶子的成员证明（路径），含方向标注

    返回 [{"position": "left"|"right", "hash": ...}, ...]：position 表示该兄弟
    节点在拼接中的位置（left = 兄弟在左）。末节点上提层不产生兄弟，跳过。
    """
    level: List[str] = [str(h) for h in leaves]
    if not (0 <= index < len(level)):
        raise IndexError(f"index 越界: {index} / {len(level)}")
    proof: List[Dict[str, str]] = []
    idx = index
    while len(level) > 1:
        if idx % 2 == 0:
            if idx + 1 < len(level):
                proof.append({"position": "right", "hash": level[idx + 1]})
            # 末节点上提：无兄弟，不记录
        else:
            proof.append({"position": "left", "hash": level[idx - 1]})
        nxt: List[str] = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(sha256_hex(level[i] + level[i + 1]))
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
        idx //= 2
    return proof


def verify_merkle_proof(leaf: str, proof: Sequence[Dict[str, str]], root: str) -> bool:
    """用成员证明重算根并与给定根比对（可重放验证）"""
    cur = str(leaf)
    for step in proof:
        sib = str(step.get("hash") or "")
        if step.get("position") == "left":
            cur = sha256_hex(sib + cur)
        else:
            cur = sha256_hex(cur + sib)
    return cur == str(root)


# ════════════════════════════════════════════════════════════
#  RootsSigner（ed25519 优先，缺失降级 sha256 自签）
# ════════════════════════════════════════════════════════════


class RootsSigner:
    """每日根签名器

    - ed25519：`cryptography` 可用且密钥文件存在/可生成 → 真签名；
    - 降级：库缺失或密钥不可用 → sha256 自签占位，`degraded=True` 且记录原因。
    """

    def __init__(self, key_path: Optional[str] = None, *, enabled: bool = True,
                 allow_generate: bool = True):
        self._key_path = key_path or DEFAULT_KEY_PATH
        self._enabled = bool(enabled)
        self._allow_generate = bool(allow_generate)
        self._private: Optional[Any] = None
        self._public_hex = ""
        self._scheme = _SIGN_SCHEME_SHA256_SELF
        self._degraded = True
        self._degraded_reason = _DEGRADED_NO_KEY
        self._key_ready = False
        # 已有密钥 → 立即加载（廉价）；**不存在时延迟到首次签名再生成**：
        # 绝大多数进程只写审计、不封存每日根，没必要为每个台账做一次 OpenSSL 密钥生成
        # （native 开销 + 密钥文件落盘），故 keygen 延后到真正签名时。
        if self._enabled and os.path.exists(self._key_path):
            self._ensure_key()

    def _ensure_key(self) -> None:
        """确保密钥就绪（幂等；首次调用时按需生成）

        `enabled=False`（调用方显式关闭签名）时保持降级占位，不加载/生成密钥。
        """
        if self._key_ready:
            return
        self._key_ready = True
        if not self._enabled:
            return
        self._init_key()

    def _init_key(self) -> None:
        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ed25519
        except Exception as e:  # noqa: BLE001 无 cryptography → 降级（非致命）
            self._degraded_reason = f"{_DEGRADED_NO_KEY}｜cryptography 不可用: {e}"
            return
        try:
            if os.path.exists(self._key_path):
                with open(self._key_path, "rb") as f:
                    self._private = serialization.load_pem_private_key(
                        f.read(), password=None)
            elif self._allow_generate:
                self._private = ed25519.Ed25519PrivateKey.generate()
                os.makedirs(os.path.dirname(self._key_path) or ".", exist_ok=True)
                pem = self._private.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.PKCS8,
                    encryption_algorithm=serialization.NoEncryption())
                with open(self._key_path, "wb") as f:
                    f.write(pem)
                _protect_readonly(self._key_path)
            else:
                self._degraded_reason = f"{_DEGRADED_NO_KEY}｜密钥不存在且禁止生成"
                return
            pub = self._private.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw)
            self._public_hex = pub.hex()
            self._scheme = _SIGN_SCHEME_ED25519
            self._degraded = False
            self._degraded_reason = ""
        except Exception as e:  # noqa: BLE001 密钥损坏/权限问题 → 降级
            self._private = None
            self._degraded_reason = f"{_DEGRADED_NO_KEY}｜密钥不可用: {e}"

    @property
    def scheme(self) -> str:
        self._ensure_key()
        return self._scheme

    @property
    def degraded(self) -> bool:
        self._ensure_key()
        return self._degraded

    @property
    def degraded_reason(self) -> str:
        self._ensure_key()
        return self._degraded_reason

    @property
    def public_key_hex(self) -> str:
        self._ensure_key()
        return self._public_hex

    @property
    def key_path(self) -> str:
        return self._key_path

    def sign(self, message: str) -> str:
        """签名（hex）

        无 ed25519 私钥（库缺失 / 未启用 / 密钥不可用）时返回 **sha256 自签占位**，
        并已在 `degraded` / `degraded_reason` 显式记录降级（§3.5 单机降级路径）。
        """
        self._ensure_key()
        if self._private is not None:
            sig: bytes = self._private.sign(str(message).encode("utf-8"))
            return sig.hex()
        return sha256_hex(_SIGN_SCHEME_SHA256_SELF + "|" + str(message))

    @staticmethod
    def verify(message: str, signature: str, *, scheme: str,
               public_key_hex: str = "") -> bool:
        """校验签名（ed25519 需公钥；sha256-self 仅作一致性重算，非真签名）"""
        if scheme == _SIGN_SCHEME_ED25519:
            if not public_key_hex:
                return False
            try:
                from cryptography.hazmat.primitives.asymmetric import ed25519
            except Exception:  # noqa: BLE001
                return False
            try:
                pub = ed25519.Ed25519PublicKey.from_public_bytes(
                    bytes.fromhex(public_key_hex))
                pub.verify(bytes.fromhex(signature), str(message).encode("utf-8"))
                return True
            except Exception:  # noqa: BLE001 签名不合法/格式错误
                return False
        if scheme == _SIGN_SCHEME_SHA256_SELF:
            return signature == sha256_hex(_SIGN_SCHEME_SHA256_SELF + "|" + str(message))
        return False


def _protect_readonly(path: str) -> bool:
    """把文件置为只读（best-effort；Windows/Linux 语义不同，仅作单机降级保护）"""
    try:
        os.chmod(path, 0o444)
        return True
    except Exception:  # noqa: BLE001 平台/权限差异，非致命
        return False


@contextmanager
def _appending(path: str) -> Iterator[Any]:
    """受保护文件的临时可写上下文：进入前恢复写权限，退出后置回只读"""
    existed = os.path.exists(path)
    if existed:
        try:
            os.chmod(path, 0o644)
        except Exception:  # noqa: BLE001
            pass
    fh = open(path, "a", encoding="utf-8")
    try:
        yield fh
    finally:
        try:
            fh.close()
        finally:
            if existed:
                _protect_readonly(path)


# ════════════════════════════════════════════════════════════
#  DailyRoot（每日根记录）
# ════════════════════════════════════════════════════════════


@dataclass
class DailyRoot:
    """每日 Merkle 根记录（`data/audit/daily_roots.jsonl` 一行）"""

    date: str
    root_hash: str
    leaf_count: int
    first_seq: int = 0
    last_seq: int = 0
    first_self_hash: str = ""
    last_self_hash: str = ""
    algorithm: str = MERKLE_ALGO
    signature: str = ""
    signature_scheme: str = _SIGN_SCHEME_SHA256_SELF
    signer_public_key: str = ""
    degraded: bool = True
    degraded_reason: str = _DEGRADED_NO_KEY
    protected: bool = False
    created_at: str = ""
    #: 外层链：前一条根记录的 entry_hash（防整日根被删除）
    prev_entry_hash: str = ""
    entry_hash: str = ""
    schema_version: int = SCHEMA_VERSION

    def signed_message(self) -> str:
        """待签串（不含签名字段本身）"""
        return "|".join([str(self.date), str(self.root_hash), str(self.leaf_count),
                         str(self.first_seq), str(self.last_seq),
                         str(self.first_self_hash), str(self.last_self_hash)])

    def core_dict(self) -> Dict[str, Any]:
        """参与 entry_hash 的字段（排除 entry_hash 自身）"""
        d = asdict(self)
        d.pop("entry_hash", None)
        return d

    def compute_entry_hash(self, prev_entry_hash: str) -> str:
        """外层链哈希 = sha256(prev_entry_hash + 规范化核心字段 JSON)"""
        return sha256_hex(str(prev_entry_hash) + canonical_json(self.core_dict()))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DailyRoot":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in (data or {}).items() if k in known})


@dataclass
class RootsVerification:
    """每日根重放校验结果"""

    ok: bool
    date: str = ""
    reason: str = ""
    detail: str = ""
    recomputed_root: str = ""
    recorded_root: str = ""
    signature_ok: bool = False
    #: 记录中的签名方案（ed25519 / sha256-self 降级）与降级标记
    signature_scheme: str = ""
    signing_degraded: bool = False
    chains_ok: bool = True
    entries_verified: int = 0
    root_chain_checked: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        if self.ok:
            return (f"OK — 每日根可重放：date={self.date} root={self.recorded_root[:16]}…"
                    f"（{self.entries_verified} 条叶子，签名="
                    f"{self.signature_scheme or 'n/a'}"
                    f"{'（降级占位）' if self.signing_degraded else '，有效'}）")
        return f"FAILED — date={self.date} {self.reason}: {self.detail}"


# ════════════════════════════════════════════════════════════
#  单写者登记表（进程内，§5.5）
# ════════════════════════════════════════════════════════════

_WRITER_REGISTRY: Dict[str, str] = {}
_REGISTRY_LOCK = threading.RLock()
_CHAIN_SINGLETONS: Dict[str, "AuditChain"] = {}


def _resolve_path(db_path: Optional[str]) -> str:
    return os.path.abspath(db_path or DEFAULT_DB_PATH)


def active_writers() -> Dict[str, str]:
    """当前进程内活跃 writer（路径 → 属主 token），供诊断/测试"""
    with _REGISTRY_LOCK:
        return dict(_WRITER_REGISTRY)


def reset_audit_chains() -> None:
    """关闭并清除本进程全部审计链单例（**测试专用**）

    顺序关键：**先关闭、后清登记**，且全程持登记锁——否则「已清登记的旧 writer
    仍在 flush」与「新 writer 已按旧 max(seq) 起链」会并发写同一路径，产生
    seq 撞车（实测 UNIQUE constraint failed）。
    """
    with _REGISTRY_LOCK:
        chains = list(_CHAIN_SINGLETONS.values())
        _CHAIN_SINGLETONS.clear()
        for c in chains:
            try:
                c.close(timeout=2.0)
            except Exception:  # noqa: BLE001 清理阶段不抛
                pass
        _WRITER_REGISTRY.clear()


def get_audit_chain(db_path: Optional[str] = None, **kwargs: Any) -> "AuditChain":
    """取得某 DB 路径的**进程唯一 writer** 实例（不存在则创建）

    这是「主进程唯一 Ledger writer」的默认入口：Scheduler / 路由 / 后台线程
    统一通过它 `append()`（进程内 IPC 提交），而非各自 new 一个 writer。
    """
    key = _resolve_path(db_path)
    with _REGISTRY_LOCK:
        chain = _CHAIN_SINGLETONS.get(key)
        if chain is not None and not chain.closed:
            return chain
        chain = AuditChain(key, **kwargs)
        _CHAIN_SINGLETONS[key] = chain
        return chain


# ════════════════════════════════════════════════════════════
#  AuditChain（单写者 + 批量异步持久化 + 读取 + 验签 + 每日根）
# ════════════════════════════════════════════════════════════


class AuditChain:
    """链式审计台账（§3.5）

    写入路径：`append()` 在互斥锁内完成 seq 分配 + 两级哈希 + 入队，由后台 writer
    线程批量 INSERT（WAL + synchronous=FULL）。读路径：`entries()/get()/count()`
    先 `flush()` 追平再查库，并合并降级 ring buffer，保证「读得到刚写的」。

    单写者：构造时登记路径占用（`enforce_single_writer=True`），同路径第二实例抛
    `SingleWriterViolationError`；只读消费方用 ``role="reader"``。
    """

    def __init__(self, db_path: Optional[str] = None, *, roots_path: Optional[str] = None,
                 role: str = "writer", enforce_single_writer: bool = True,
                 daily_root_protect: bool = True, auto_seal: bool = True,
                 signing_enabled: bool = True, signing_key_path: Optional[str] = None,
                 ring_buffer_maxlen: int = RING_BUFFER_MAXLEN,
                 auto_start_writer: bool = True,
                 journal_path: Optional[str] = None, journal_enabled: bool = True,
                 lock_path: Optional[str] = None, lock_enabled: bool = True,
                 seq_lock_timeout: float = DEFAULT_SEQ_LOCK_TIMEOUT,
                 queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE):
        if role not in ("writer", "reader"):
            raise AuditChainError(f"非法 role: {role}（允许 writer / reader）")
        self._db_path = _resolve_path(db_path)
        self._roots_path = os.path.abspath(roots_path or DEFAULT_ROOTS_PATH)
        self._role = role
        self._daily_root_protect = bool(daily_root_protect)
        self._auto_seal = bool(auto_seal) and role == "writer"
        self._ring_buffer_maxlen = int(ring_buffer_maxlen)

        self._append_lock = threading.Lock()
        self._write_lock = threading.RLock()
        self._local = threading.local()
        self._queue: "queue_module.Queue[Optional[AuditEntry]]" = queue_module.Queue(
            maxsize=max(int(queue_maxsize), 1))
        self._failed_buffer: Deque[AuditEntry] = deque(maxlen=self._ring_buffer_maxlen)
        #: 保护 ``_failed_buffer`` 的锁：**writer 线程写、任意读线程遍历**
        #: （见 `_failed_snapshot` 的说明——无锁遍历会撞
        #: `RuntimeError: deque mutated during iteration`）
        self._buffer_lock = threading.Lock()
        self._degraded = False
        self._degraded_reason = ""
        #: DB 是否可用（初始化失败 → False：读路径不再碰库，全部走 ring buffer）
        self._db_available = True
        self._closed = False
        self._owner_token = f"{os.getpid()}-{id(self)}-{time.time():.6f}"

        self._enqueue_count = 0
        self._commit_count = 0
        self._count_lock = threading.Lock()
        self._next_seq = 1
        self._last_hash = GENESIS_PREV_HASH
        self._last_appended_ts = ""
        #: 本进程观察到「有记录」的 UTC 日（后台自动封存只处理这些日，避免空日风暴）
        self._observed_days: set = set()
        self._sealed_days: set = set()
        #: 后台 writer 线程（reader 角色恒为 None）
        self._writer_thread: Optional[threading.Thread] = None

        # ── S8-02：跨进程 seq 权威（预留日志）+ 互斥（独立锁文件）──
        self._role_writer = role == "writer"
        self._journal_enabled = bool(journal_enabled) and role == "writer"
        self._journal = SeqJournal(
            journal_path or (self._db_path + JOURNAL_SUFFIX),
            enabled=self._journal_enabled)
        #: 压缩时保留的最近已入库记录条数（降级读路径的"近期记录缓存"；
        #: 0 = 全清。见 `SeqJournal.compact` 的 retain 说明）
        self._journal_retain = DEFAULT_RETAIN_RECORDS
        #: 压缩节流参数（见 `_maybe_compact_journal` 的性能说明）
        self._journal_compact_min_rows = max(2 * DEFAULT_RETAIN_RECORDS,
                                             DEFAULT_COMPACT_MIN_ROWS)
        self._last_compact_check = 0.0
        self._lock_enabled = bool(lock_enabled) and role == "writer"
        self._lock_path = os.path.abspath(
            lock_path or lock_path_for(self._db_path, suffix=LOCK_SUFFIX))
        self._cp_lock = CrossProcessLock(
            self._lock_path, name="audit_chain",
            holder_info={"db_path": self._db_path})
        self._seq_lock_timeout = max(float(seq_lock_timeout), 0.0)
        #: 观测：seq 降级分配 / 队列满 / ring buffer 溢出 / 日志重放 / 压缩
        self._seq_degraded_count = 0
        self._queue_full_count = 0
        self._buffer_dropped_count = 0
        self._journal_replay_count = 0
        self._journal_compact_count = 0
        self._journal_write_failures = 0
        #: 降级留痕节流（避免退避循环把审计链自己冲垮）
        self._degrade_notify_at = 0.0
        #: 本进程已确认入库的最大 seq（预留日志压缩的水位参考）
        self._committed_max_seq = 0
        #: **在途 seq**：已入队但尚未提交 DB 的 seq。
        #:
        #: 【为什么必须有（实现期实测缺陷）】writer 每轮既消费队列、又按
        #: "DB 水位"收敛预留日志；而队列里那些**尚未提交**的记录同样满足
        #: `seq > db_max` ⇒ 同一条被**插两次** ⇒ 实测
        #: `UNIQUE constraint failed: audit_chain.seq`。
        #: 用这张表把"在途"从"滞留"里排除掉，两条路径才不会互相重复。
        self._inflight_seq: set = set()
        #: 是否需要收敛预留日志（**按需**触发，不做无谓的每轮全量重放）
        #:
        #: 【为什么不无条件收敛（实现期实测缺陷）】审计链的防篡改用例会**故意
        #: 删除 DB 中间行**再断言"能检出"；若无条件重放，日志会立刻把被删的行
        #: 补回去，用例反而看不到篡改——功能被自己的恢复机制掩盖。
        #: 正确口径：只在**确知有滞留**时收敛（启动发现日志超前 / 队满 / 入队失败）。
        self._journal_needs_drain = False

        if role == "writer":
            if enforce_single_writer:
                self._acquire_writer_slot()

        try:
            self._init_db()
        except Exception as e:  # noqa: BLE001 初始化失败 → 降级 ring buffer（不阻断主路径）
            logger.warning("链式审计 SQLite 初始化失败，降级到 ring buffer: %s", e)
            self._db_available = False
            self._degraded = True
            self._degraded_reason = f"db_init_failed: {e}"

        self._load_state()
        self._sealed_days = self._load_sealed_days()

        self._signer = RootsSigner(signing_key_path, enabled=signing_enabled)

        if role == "writer" and auto_start_writer:
            self._writer_thread = threading.Thread(
                target=self._writer_loop, name="audit-chain-writer", daemon=True)
            self._writer_thread.start()

    # ── 生命周期 ────────────────────────────────────────────

    @property
    def db_path(self) -> str:
        return self._db_path

    @property
    def roots_path(self) -> str:
        return self._roots_path

    @property
    def role(self) -> str:
        return self._role

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def degraded(self) -> bool:
        return self._degraded

    @property
    def degraded_reason(self) -> str:
        return self._degraded_reason

    @property
    def signer(self) -> RootsSigner:
        return self._signer

    @property
    def next_seq(self) -> int:
        with self._append_lock:
            return self._next_seq

    @property
    def last_hash(self) -> str:
        with self._append_lock:
            return self._last_hash

    def _acquire_writer_slot(self) -> None:
        with _REGISTRY_LOCK:
            holder = _WRITER_REGISTRY.get(self._db_path)
            if holder is not None:
                raise SingleWriterViolationError(
                    f"路径 {self._db_path} 已有活跃 writer（{holder}）——§5.5 单写者纪律："
                    f"请用 get_audit_chain() 复用进程单例，或后台线程使用 role='reader'")
            _WRITER_REGISTRY[self._db_path] = self._owner_token

    def _release_writer_slot(self) -> None:
        with _REGISTRY_LOCK:
            if _WRITER_REGISTRY.get(self._db_path) == self._owner_token:
                _WRITER_REGISTRY.pop(self._db_path, None)
            if _CHAIN_SINGLETONS.get(self._db_path) is self:
                _CHAIN_SINGLETONS.pop(self._db_path, None)

    def _get_conn(self) -> sqlite3.Connection:
        """常驻连接（仅 `:memory:` 共享缓存库需要；文件库请用 `_connect()`）"""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                "file:audit_chain_mem?mode=memory&cache=shared",
                uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return conn

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """连接上下文：文件库**用完即关**（WAL 模式随库持久化）

        Why 不常驻：常驻连接会一直占住 `audit_chain.db`（Windows 上目录/库文件
        无法删除或替换），且让「删库重建」场景出现 seq 撞车。审计写入本就是
        批量低并发，开连接开销可忽略。
        """
        if self._db_path == ":memory:":
            yield self._get_conn()
            return
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self._db_path, check_same_thread=False, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            if self._role == "writer":
                # 审计优先耐久：WAL + FULL（崩溃不丢已提交记录）
                conn.execute("PRAGMA synchronous = FULL")
            yield conn
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _init_db(self) -> None:
        with self._connect() as conn:
            with self._write_lock:
                if self._db_path != ":memory:":
                    try:
                        conn.execute("PRAGMA journal_mode = WAL")
                    except Exception:  # noqa: BLE001 平台差异，非致命
                        pass
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS audit_chain (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        seq INTEGER NOT NULL UNIQUE,
                        ts TEXT NOT NULL,
                        actor TEXT NOT NULL,
                        action TEXT NOT NULL,
                        subject TEXT NOT NULL DEFAULT '',
                        payload_hash TEXT NOT NULL,
                        prev_hash TEXT NOT NULL,
                        self_hash TEXT NOT NULL,
                        source TEXT NOT NULL DEFAULT 'agent',
                        trace_id TEXT NOT NULL DEFAULT '',
                        workspace_id TEXT NOT NULL DEFAULT '',
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        payload TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                for ddl in (
                    "CREATE INDEX IF NOT EXISTS idx_ac_ts ON audit_chain(ts)",
                    "CREATE INDEX IF NOT EXISTS idx_ac_action_ts ON audit_chain(action, ts)",
                    "CREATE INDEX IF NOT EXISTS idx_ac_actor ON audit_chain(actor)",
                    "CREATE INDEX IF NOT EXISTS idx_ac_source ON audit_chain(source, ts)",
                    "CREATE INDEX IF NOT EXISTS idx_ac_trace ON audit_chain(trace_id)",
                ):
                    conn.execute(ddl)
                conn.commit()

    def _load_state(self) -> None:
        """重启后从持久化最大 seq / 链头 self_hash 继续（seq 单调不重用）

        【S8-02 扩展（跨进程）】链头不能只看 DB：进程被杀时可能"已分配并落日志、
        未入库"，那些 seq 也必须算进链头，否则重启会**从空洞处重号**
        （重复 seq → UNIQUE 冲突 → 整批进 ring buffer → 丢数据）。
        故这里同时读预留日志，取 seq 较大者；并在读之前清掉日志末尾的**半行**
        （进程被杀的残行，留着会让后续 append 与之粘连成非法 JSON）。
        """
        # 先清残行（best-effort；拿不到锁也做——残行清理是幂等的本地修复）
        if self._journal_enabled:
            try:
                if self._lock_enabled:
                    with self._cp_lock.locked(0.2, on_timeout="degrade") as ctx:
                        if ctx.acquired:
                            self._journal.discard_torn_tail()
                else:
                    self._journal.discard_torn_tail()
            except Exception as exc:  # noqa: BLE001 清理失败不影响启动
                logger.debug("预留日志残行清理跳过: %s", exc)

        db_seq, db_hash = 0, ""
        if self._db_available:
            try:
                with self._connect() as conn:
                    row = conn.execute(
                        "SELECT seq, self_hash FROM audit_chain ORDER BY seq DESC LIMIT 1"
                    ).fetchone()
                if row is not None:
                    db_seq, db_hash = int(row["seq"]), str(row["self_hash"])
            except Exception as e:  # noqa: BLE001 读不到 → 从 1 开始（空链），不阻断
                logger.warning("链式审计恢复状态失败（将从 seq=1 起）: %s", e)

        head_seq, head_hash = db_seq, db_hash
        if self._journal_enabled:
            try:
                j_seq, j_hash = self._journal.head()
                if j_seq > head_seq:
                    head_seq, head_hash = j_seq, j_hash
                    # 日志超前于 DB ⇒ 上次有"已分配未入库"的记录，必须收敛
                    self._journal_needs_drain = True
                    logger.info("链式审计从预留日志恢复链头: seq=%d（DB=%d，"
                                "说明上次有已分配未入库的记录，将由后台收敛补写）",
                                j_seq, db_seq)
            except Exception as exc:  # noqa: BLE001
                logger.warning("预留日志链头读取失败（回退 DB 链头）: %s", exc)

        if head_seq > 0:
            self._next_seq = head_seq + 1
            self._last_hash = head_hash or GENESIS_PREV_HASH

    # ── S8-02：跨进程 seq 分配 / 锁 / 留存 ────────────────────

    def _db_head(self) -> Tuple[int, str]:
        """DB 内的 ``(max(seq), 该行 self_hash)``（读不到返回 ``(0, "")``）"""
        if not self._db_available:
            return 0, ""
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT seq, self_hash FROM audit_chain ORDER BY seq DESC LIMIT 1"
                ).fetchone()
        except Exception as exc:  # noqa: BLE001 读不到按"无"处理（保守：以内存链头为准）
            logger.debug("读取 DB 链头失败: %s", exc)
            return 0, ""
        if row is None:
            return 0, ""
        return int(row["seq"]), str(row["self_hash"])

    def _committed_watermark(self) -> Tuple[int, bool]:
        """DB 的**连续**已入库水位 ``(watermark, contiguous)``

        返回 ``watermark`` = 最大的 W 使得 ``1..W`` **全部**在库里；
        ``contiguous`` = 该口径是否成立（库为空时返回 ``(0, True)``）。

        【为什么需要"连续"而不是 MAX(seq)】崩溃恢复窗口里 DB 可能暂时只有
        较大的 seq（例如先补写了 6 而 1..5 还在日志里）。此时若拿 ``MAX(seq)=6``
        当水位去压缩日志，就会把**尚未入库**的 1..5 当作"已入库副本"删掉
        ——静默丢数据。连续水位由"行数 == MAX(seq) 且 MIN(seq) == 1"判定。
        """
        if not self._db_available:
            return 0, False
        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n, MIN(seq) AS lo, MAX(seq) AS hi "
                    "FROM audit_chain").fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.debug("读取 DB 水位失败: %s", exc)
            return 0, False
        count = int(row["n"] or 0)
        if count == 0:
            return 0, True
        lo, hi = int(row["lo"]), int(row["hi"])
        if lo == 1 and count == hi:
            return hi, True
        return 0, False

    #: 锁文件元数据槽的键（记"最后完成分配的进程 + 其链头"）。
    #: 仅供 `_resolve_head` 的快路径使用；缺失只会退化为慢路径，不影响正确性。
    _META_KEY_TOKEN = "tok"
    _META_KEY_SEQ = "seq"
    _META_KEY_HASH = "self_hash"

    def _resolve_head(self) -> Tuple[int, str]:
        """解析**权威链头**（须在跨进程锁内调用）

        【快路径：读锁文件的元数据槽，免 stat、免查库】
        任何写入者都必须**先取锁**，所以"我不持锁期间有没有别人写过"等价于
        "有没有别人取过锁"。每个持有者完成分配后，把自己的 ``PROCESS_TOKEN``
        与链头写进锁文件元数据槽；下次取锁读到 token 仍是自己 ⇒ 无人动过
        ⇒ 内存链头即权威，可跳过对日志的 ``os.stat``（Windows 实测 0.11ms/次）
        与 DB 查询（实测 **3.56ms/次**）。槽的定位读只走**已打开的句柄**，
        实测约 15µs。

        【慢路径：三源取大】快路径不成立时（首次分配 / 别的进程取过锁 /
        重启后 pid 复用被 token 挡住）才真正解析：
        ``max(内存链头, 预留日志末行, DB 最大行)``。
        - **预留日志**是"已分配、可能尚未入库"的权威（丢它会重号），
          且非空时优先于 DB（否则每条 append 都要付 3.56ms 的连接开销）；
        - **DB** 是对外可查询的既成事实；
        - **内存** 是本进程已分配但日志被禁用时唯一的依据（降级路径）。
        """
        if self._lock_enabled:
            meta: Optional[Dict[str, Any]] = None
            try:
                meta = self._cp_lock.read_meta()
            except Exception:  # noqa: BLE001 槽读失败 → 走慢路径
                meta = None
            if isinstance(meta, dict) and \
                    meta.get(self._META_KEY_TOKEN) == PROCESS_TOKEN:
                with self._append_lock:
                    mem_seq = self._next_seq - 1
                    mem_hash = self._last_hash
                if int(meta.get(self._META_KEY_SEQ) or 0) <= mem_seq:
                    # 我们是最后一个完成分配的进程 ⇒ 无人能超过内存链头
                    return max(mem_seq, 0), (mem_hash or GENESIS_PREV_HASH)
            else:
                # 槽里不是我们自己（或槽损坏）⇒ 先声明占用，避免并发持锁者互信
                self._publish_head_meta(self._next_seq - 1, self._last_hash)

        cand_seq, cand_hash = self._next_seq - 1, self._last_hash

        j_seq, j_hash = 0, ""
        if self._journal_enabled:
            try:
                j_seq, j_hash = self._journal.head()
            except Exception as exc:  # noqa: BLE001
                logger.debug("预留日志链头不可用: %s", exc)
        if j_seq > cand_seq:
            cand_seq, cand_hash = j_seq, j_hash

        # 仅当日志给不出链头时才查 DB（冷启动/刚压缩完/日志被清）
        if cand_seq <= 0 or j_seq <= 0:
            d_seq, d_hash = self._db_head()
            if d_seq > cand_seq:
                cand_seq, cand_hash = d_seq, d_hash

        return max(cand_seq, 0), (cand_hash or GENESIS_PREV_HASH)

    def _publish_head_meta(self, seq: int, self_hash: str) -> bool:
        """把"我已完成 seq 分配、链头到此"写进锁文件元数据槽（须持锁）

        【写入顺序：先日志、后元数据槽（不可颠倒）】若在两者之间崩溃，槽里仍是
        上一次的记录 ⇒ 下一个进程读到 token 不是自己 ⇒ 走慢路径读日志，
        从而**发现**那条已分配的记录。反过来先写槽再写日志，会让别人从槽取到
        "日志里根本没有"的链头，造成 seq **空洞**。
        """
        if not self._lock_enabled:
            return False
        try:
            return self._cp_lock.write_meta({
                self._META_KEY_TOKEN: PROCESS_TOKEN,
                self._META_KEY_SEQ: int(seq),
                self._META_KEY_HASH: str(self_hash or ""),
                "pid": os.getpid(),
            })
        except Exception as exc:  # noqa: BLE001 槽写失败只影响性能（退化为慢路径）
            logger.debug("锁元数据槽写入失败（退化为慢路径）: %s", exc)
            return False

    def _journal_row(self, entry: "AuditEntry") -> Dict[str, Any]:
        """把记录转成预留日志的一行（扁平字典；payload 存规范化 JSON 文本）"""
        return {
            "seq": int(entry.seq), "ts": entry.ts, "actor": entry.actor,
            "action": entry.action, "subject": entry.subject,
            "payload_hash": entry.payload_hash, "prev_hash": entry.prev_hash,
            "self_hash": entry.self_hash, "source": entry.source,
            "trace_id": entry.trace_id, "workspace_id": entry.workspace_id,
            "schema_version": int(entry.schema_version),
            "payload": canonical_json(entry.payload),
        }

    @staticmethod
    def _entry_from_journal_row(row: Dict[str, Any]) -> "AuditEntry":
        """预留日志一行 → AuditEntry（payload 文本还原为字典）"""
        raw = row.get("payload")
        payload: Any = raw
        if isinstance(raw, str):
            try:
                payload = json.loads(raw) if raw else {}
            except (ValueError, TypeError):
                payload = {"_raw": raw}
        return AuditEntry(
            seq=int(row.get("seq") or 0), ts=str(row.get("ts") or ""),
            actor=str(row.get("actor") or ""), action=str(row.get("action") or ""),
            subject=str(row.get("subject") or ""),
            payload_hash=str(row.get("payload_hash") or ""),
            prev_hash=str(row.get("prev_hash") or GENESIS_PREV_HASH),
            self_hash=str(row.get("self_hash") or ""),
            source=str(row.get("source") or SOURCE_AGENT),
            trace_id=str(row.get("trace_id") or ""),
            workspace_id=str(row.get("workspace_id") or ""),
            schema_version=int(row.get("schema_version") or SCHEMA_VERSION),
            payload=payload if isinstance(payload, dict) else {"value": payload},
        )

    def _note_degraded(self, reason: str, *, key: str = "") -> None:
        """登记一次写入/分配降级（**计数 + 节流留痕**，绝不静默）

        为什么节流：降级常发生在**高频重试**场景（锁冲突、DB 不可用），逐条写审计
        会让"审计链自己"成为放大器。约定：计数**每次都记**（可观测性不打折），
        留痕每 30s 最多一条（保证"不静默"且可被外部看到）。
        """
        self._degraded = True
        self._degraded_reason = reason
        now = time.time()
        if now - self._degrade_notify_at < 30.0:
            return
        self._degrade_notify_at = now
        try:
            from agent.utils.cross_process_lock import notify_degraded
            notify_degraded("lock.degraded", {
                "lock_name": "audit_chain",
                "lock_path": self._lock_path,
                "db_path": self._db_path,
                "reason": reason,
                "detail": key,
                "pid": os.getpid(),
            })
        except Exception as exc:  # noqa: BLE001 留痕失败不改写降级事实
            logger.debug("审计链降级留痕失败: %s", exc)

    def _failed_snapshot(self) -> List[AuditEntry]:
        """``_failed_buffer`` 的**一致快照**（持锁拷贝）

        【为什么必须有它（实测指出的线程安全缺口）】
        ``_failed_buffer`` 被 **writer 线程**写（``_buffer_failed`` / ``clear``），
        又被**任意读线程**遍历（``entries`` / ``verify_chain`` / ``seq_range`` /
        ``stats`` ...）。CPython 的 ``deque`` 在"一边 append 一边迭代"时会抛
        ``RuntimeError: deque mutated during iteration`` —— 那会让**审计读路径**
        （含验签、面板）随机失败。原实现全程无锁遍历，属真实的崩溃级竞态。
        改用"持锁拷贝后遍历"：读路径拿到一致视图，且不再与写者共享迭代状态。
        """
        with self._buffer_lock:
            return list(self._failed_buffer)

    def _failed_len(self) -> int:
        """``_failed_buffer`` 当前长度（持锁读；避免读到撕裂状态）"""
        with self._buffer_lock:
            return len(self._failed_buffer)

    def _buffer_failed(self, entry: "AuditEntry") -> None:
        """降级缓存一条记录（**溢出必须可见**）

        【为什么不能直接用 ``deque(maxlen=N).append``（实现期指出的静默点）】
        ``deque`` 到上限后 append 会**静默丢弃最旧一条**——审计记录就这样消失了，
        只有内存里的长度能说明问题。故此处显式比较长度、计数并留痕。

        【计数与 append 必须在同一临界区】否则"判长 → 计数 → append"之间
        可能被另一个写者插入，导致丢弃数被少计、或计数与实际丢弃数不符。
        """
        with self._buffer_lock:
            if len(self._failed_buffer) >= self._ring_buffer_maxlen > 0:
                self._buffer_dropped_count += 1
                dropped = self._buffer_dropped_count
                overflow = True
            else:
                dropped = self._buffer_dropped_count
                overflow = False
            self._failed_buffer.append(entry)
        if overflow:
            self._note_degraded(
                f"ring_buffer_overflow（容量 {self._ring_buffer_maxlen}，"
                f"已丢弃最旧一条；丢弃累计 {dropped}）",
                key=f"seq={entry.seq}")

    def _note_queue_full(self, entry: "AuditEntry") -> None:
        """队满：**不丢数据**（记录已在预留日志里），显式计数 + 留痕

        这是"队列满"场景的明确行为：seq 分配与持久化已在锁内完成，
        入库队列只是进程内**交接通道**；通道打满时记录仍在日志中，
        由 writer 的**日志收敛**（``_drain_journal``）在后续轮次补齐。
        """
        self._queue_full_count += 1
        self._note_degraded(
            f"queue_full（上限 {self._queue.maxsize}；记录已持久化于预留日志，"
            f"待后台收敛；累计 {self._queue_full_count}）",
            key=f"seq={entry.seq}")

    def _resync_seq(self) -> int:
        """按库内最大 seq 重同步计数（单写者纪律被破坏时的自愈 + 告警）"""
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT MAX(seq) AS m FROM audit_chain").fetchone()
            db_max = int(row["m"] or 0)
        except Exception:  # noqa: BLE001
            return self._next_seq
        with self._append_lock:
            if db_max + 1 > self._next_seq:
                self._next_seq = db_max + 1
            return self._next_seq

    def _load_sealed_days(self) -> set:
        try:
            return {r.date for r in self.read_daily_roots() if r.date}
        except Exception:  # noqa: BLE001
            return set()

    # ── 写入路径 ────────────────────────────────────────────

    def append(self, action: str, actor: str, subject: str = "",
               payload: Optional[Dict[str, Any]] = None, *,
               source: str = SOURCE_AGENT, trace_id: str = "",
               workspace_id: str = "", ts: Any = None,
               schema_version: int = SCHEMA_VERSION) -> AuditEntry:
        """追加一条审计记录（链式；**跨进程** seq 单调递增）

        为满足「单条 append <5ms」，本方法只做：**跨进程锁内**解析链头 +
        分配 seq + 两级 sha256 + **写预留日志（flush，不 fsync）** + 入队；
        实际 SQLite 提交由后台 writer 线程批量完成。需要落盘确认时调用 `flush()`。

        【S8-02：为什么 seq 分配要跨进程锁 + 预留日志】
        原实现在**进程内**锁里读 `self._next_seq`，而该值只在构造时从 DB 恢复一次
        ⇒ 两个进程从同一 max(seq) 起分配 ⇒ **重复 seq** ⇒ 撞 UNIQUE ⇒ 整批进
        ring buffer ⇒ 进程退出即丢。现在：

        1. 取跨进程锁（非阻塞优先，失败则**有限等待**至 `seq_lock_timeout`）；
        2. 锁内解析权威链头 = max(内存, 预留日志末行, DB 最大行)，故任一进程
           分配过的 seq 立刻对其它进程可见；
        3. 整条记录（含哈希）写进预留日志并 ``flush()`` ⇒ **崩溃不丢**：
           进程被杀后由启动重放补写进 DB，seq **无空洞**、链式哈希连续，
           ``verify_chain`` 的连续性断言仍然成立；
        4. 释放锁，交接给后台批量入库。

        【取舍（实测数据）】"锁内同步 SQLite 提交"本可更简单，但一次
        ``synchronous=FULL`` 提交实测 17–22ms，而 append 的 p99 基线是 0.14ms
        （约 150×），会违反「单进程写入 p99 不退化」。预留日志把持久化与批量入库
        解耦，代价是**整机掉电**可能丢掉最后几条尚未 fsync 的记录——它们同样
        没进 DB，因此日志与 DB 只一起缺尾巴，仍然一致（无空洞、无重复）。

        【降级（不静默）】取锁失败 ⇒ 按内存链头分配并**计数 + 留痕**（此时
        seq 唯一性退化为由 DB 的 UNIQUE 约束兜底，冲突会触发 `_resync_seq`
        与日志重放，记录不会消失）。

        Raises:
            ReadOnlyChainError: 只读实例调用；
            AuditEntryError: action 为空 / source 非法 / 链已关闭。
        """
        if self._role != "writer":
            raise ReadOnlyChainError("只读实例（role='reader'）不可写入审计链")
        if self._closed:
            raise AuditEntryError("审计链已关闭，拒绝追加（请重新 get_audit_chain）")
        if not action:
            raise AuditEntryError("action 不能为空")
        src = str(source or SOURCE_AGENT)
        if src not in SOURCES:
            raise AuditEntryError(f"非法 source: {src}（允许 {sorted(SOURCES)}）")

        norm_ts = normalize_ts(ts)

        if not self._lock_enabled:
            # 显式关闭跨进程保护（用例/单进程部署）：等价旧语义
            entry = self._allocate_and_journal(
                prev_seq=self._next_seq - 1, prev_hash=self._last_hash,
                ts=norm_ts, actor=actor or SOURCE_SYSTEM, action=action,
                subject=subject, payload=payload, src=src, trace_id=trace_id,
                workspace_id=workspace_id, schema_version=schema_version,
                under_lock=False)
            return entry

        acquired = False
        try:
            with self._cp_lock.locked(self._seq_lock_timeout,
                                      on_timeout="degrade") as ctx:
                acquired = bool(ctx.acquired)
                if acquired:
                    head_seq, head_hash = self._resolve_head()
                else:
                    # 【锁不可得 ⇒ 不再用"内存链头"兜底（实测会重复 seq）】
                    # 旧口径按 `self._next_seq - 1` 分配，而该值是**本进程上次分配
                    # 的位置**：别的进程可能早已推进到更后面 ⇒ 两个进程同时返回
                    # 同一个 seq（实测：4 进程用例出现 `duplicates=[80]`，
                    # 一条 `under_lock=0` 的分配与前一条 `prev_seq` 完全相同）。
                    # 改为回落**DB 事务分配**（正确但慢），见 `_allocate_via_db`。
                    entry = self._allocate_via_db(
                        ts=norm_ts, actor=actor or SOURCE_SYSTEM, action=action,
                        subject=subject, payload=payload, src=src,
                        trace_id=trace_id, workspace_id=workspace_id,
                        schema_version=schema_version,
                        reason="seq_lock_timeout")
                    self._seq_degraded_count += 1
                    self._note_degraded(
                        f"seq_lock_timeout（{self._seq_lock_timeout}s 内未取得跨进程锁；"
                        f"本次回落到 DB 事务分配 seq={entry.seq}；"
                        f"累计 {self._seq_degraded_count}）",
                        key=f"lock={self._lock_path}")
                    return entry
                entry = self._allocate_and_journal(
                    prev_seq=head_seq, prev_hash=head_hash, ts=norm_ts,
                    actor=actor or SOURCE_SYSTEM, action=action, subject=subject,
                    payload=payload, src=src, trace_id=trace_id,
                    workspace_id=workspace_id, schema_version=schema_version,
                    under_lock=acquired)
                # 【顺序关键】日志已落 ⇒ 才把链头发布到锁槽（见该方法注释）
                self._publish_head_meta(entry.seq, entry.self_hash)
        except LockUnavailable:
            # 防御性兜底：locked(on_timeout="degrade") 已吞掉 LockUnavailable，
            # 但若未来语义变动/异常路径漏出，也不能让审计写入直接失败。
            self._seq_degraded_count += 1
            self._note_degraded("seq_lock_unavailable", key=f"lock={self._lock_path}")
            entry = self._allocate_via_db(
                ts=norm_ts, actor=actor or SOURCE_SYSTEM, action=action,
                subject=subject, payload=payload, src=src, trace_id=trace_id,
                workspace_id=workspace_id, schema_version=schema_version,
                reason="seq_lock_unavailable")
            return entry
        return entry

    def _allocate_via_db(self, *, ts: str, actor: str, action: str, subject: str,
                         payload: Optional[Dict[str, Any]], src: str,
                         trace_id: str, workspace_id: str, schema_version: int,
                         reason: str) -> AuditEntry:
        """**锁不可得时的兜底分配**：DB 事务内读链头 + 直接入库

        【为什么这是正确的那条兜底（实测教训）】任务书给的三条 seq 分配路线是
        "锁内分配 / 预留区间 / **DB 事务**"。主路径用"锁内分配 + 预留日志"换取
        低延迟；取不到锁时若继续按**本进程内存链头**分配，就会与其它进程撞号
        （实测 4 进程用例复现）。而 ``BEGIN IMMEDIATE`` 会立刻取到 SQLite 的写锁，
        跨进程串行化 ⇒ 链头读到的一定是最新值 ⇒ **无重复**、且记录当场入库
        （不依赖后台线程，崩溃也不丢）。

        【语义与代价】崩溃语义：事务未提交 ⇒ 无记录、无 seq 消耗（**无空洞**）；
        已提交 ⇒ 已入库。代价是一次 ``synchronous=FULL`` 提交（实测 17–22ms），
        比主路径慢约两个数量级——但它只在"跨进程锁长期不可得"这种异常态发生，
        且**显式计数 + 留痕**，不会静默。

        【双故障兜底】若 DB 事务同样失败（锁与 DB 同时不可用），退回"隐式失败"
        路线：按内存链头分配并置 ``seq_alloc_reliable=False``、计入降级，
        唯一性由 DB 的 UNIQUE 约束兜底——这是本模块唯一可能重号的路径，
        且必须两个独立设施同时失效才会走到。
        """
        if not self._db_available:
            entry = self._allocate_and_journal(
                prev_seq=self._next_seq - 1, prev_hash=self._last_hash, ts=ts,
                actor=actor, action=action, subject=subject, payload=payload,
                src=src, trace_id=trace_id, workspace_id=workspace_id,
                schema_version=schema_version, under_lock=False)
            self._note_degraded(f"db_unavailable_for_fallback ({reason})",
                                key=f"seq={entry.seq}")
            return entry
        try:
            with self._connect() as conn:
                with self._write_lock:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT seq, self_hash FROM audit_chain "
                        "ORDER BY seq DESC LIMIT 1").fetchone()
                    seq = (int(row["seq"]) if row is not None else 0) + 1
                    prev_hash = (str(row["self_hash"]) if row is not None
                                 else GENESIS_PREV_HASH)
                    entry = build_entry(
                        seq=seq, ts=ts, actor=actor, action=action,
                        subject=subject, payload=payload, prev_hash=prev_hash,
                        source=src, trace_id=trace_id,
                        workspace_id=workspace_id, schema_version=schema_version)
                    placeholders = ",".join(["?"] * len(_COLUMNS))
                    conn.execute(
                        f"INSERT INTO audit_chain ({','.join(_COLUMNS)}) "
                        f"VALUES ({placeholders})", entry.row_values())
                    conn.commit()
        except Exception as exc:  # noqa: BLE001 双故障 → 最后兜底（显式标记）
            logger.error("兜底 DB 事务分配失败（跨进程锁与 DB 同时不可用）: %s", exc)
            entry = self._allocate_and_journal(
                prev_seq=self._next_seq - 1, prev_hash=self._last_hash, ts=ts,
                actor=actor, action=action, subject=subject, payload=payload,
                src=src, trace_id=trace_id, workspace_id=workspace_id,
                schema_version=schema_version, under_lock=False)
            self._note_degraded(
                f"fallback_db_alloc_failed ({reason}): {exc}", key=f"seq={entry.seq}")
            return entry

        # 与主路径一致地推进内存链头 / 写日志（日志是崩溃恢复的来源）
        with self._append_lock:
            if entry.seq >= self._next_seq:
                self._next_seq = int(entry.seq) + 1
                self._last_hash = entry.self_hash
            self._last_appended_ts = ts
            self._observed_days.add(day_of_ts(ts))
        self._committed_max_seq = max(self._committed_max_seq, int(entry.seq))
        if self._journal_enabled:
            try:
                self._journal.append_row(self._journal_row(entry))
            except Exception as exc:  # noqa: BLE001 日志写失败：记录已在 DB，不丢
                self._journal_write_failures += 1
                self._note_degraded(f"seq_journal_write_failed: {exc}",
                                    key=f"seq={entry.seq}")
        logger.warning("审计 seq 兜底分配成功（DB 事务）：seq=%s，原因=%s",
                       entry.seq, reason)
        return entry

    def _allocate_and_journal(self, *, prev_seq: int, prev_hash: str, ts: str,
                              actor: str, action: str, subject: str,
                              payload: Optional[Dict[str, Any]], src: str,
                              trace_id: str, workspace_id: str,
                              schema_version: int,
                              under_lock: bool) -> AuditEntry:
        """分配 seq → 算两级哈希 → 写预留日志 → 入队（唯一分配点）

        ``under_lock`` 仅用于观测口径（是否走了降级分配），不改变分配逻辑。
        """
        with self._append_lock:
            seq = int(prev_seq) + 1
            entry = build_entry(
                seq=seq, ts=ts, actor=actor, action=action, subject=subject,
                payload=payload, prev_hash=prev_hash or GENESIS_PREV_HASH,
                source=src, trace_id=trace_id, workspace_id=workspace_id,
                schema_version=schema_version)
            # seq 与链头在本锁内推进：并发的 append 拿到互不相同的 seq，
            # 且每条的前驱 = 上一条 self_hash（链序 = seq 序）
            self._next_seq = seq + 1
            self._last_hash = entry.self_hash
            self._last_appended_ts = ts
            self._observed_days.add(day_of_ts(ts))

            # ① 持久化到预留日志（**崩溃恢复的关键**：先落日志，再入队）
            if self._journal_enabled:
                try:
                    self._journal.append_row(self._journal_row(entry))
                except SeqJournalError as exc:
                    self._journal_write_failures += 1
                    self._journal_needs_drain = True
                    self._note_degraded(f"seq_journal_write_failed: {exc}",
                                        key=f"seq={seq}")
                except Exception as exc:  # noqa: BLE001
                    self._journal_write_failures += 1
                    self._journal_needs_drain = True
                    self._note_degraded(
                        f"seq_journal_write_failed: {type(exc).__name__}: {exc}",
                        key=f"seq={seq}")

            # ② 交接给后台批量入库（队满 ≠ 丢数据：记录已在日志里）
            try:
                self._queue.put_nowait(entry)
                with self._count_lock:
                    self._enqueue_count += 1
                    self._inflight_seq.add(int(seq))
            except queue_module.Full:
                self._note_queue_full(entry)
                self._journal_needs_drain = True
            except Exception as e:  # noqa: BLE001 其它异常 → ring buffer（不丢记录）
                self._buffer_failed(entry)
                self._journal_needs_drain = True
                logger.warning("审计记录入队失败，已降级 ring buffer（seq=%s）", seq)
        return entry

    def _write_to_db(self, records: List[AuditEntry]) -> None:
        if not records:
            return
        try:
            self._write_to_db_inner(records)
        finally:
            self._clear_inflight(records)

    def _write_to_db_inner(self, records: List[AuditEntry]) -> None:
        if not self._db_available:
            for r in records:
                self._buffer_failed(r)
            self._mark_committed(len(records))
            return
        try:
            with self._connect() as conn:
                placeholders = ",".join(["?"] * len(_COLUMNS))
                sql = (f"INSERT INTO audit_chain ({','.join(_COLUMNS)}) "
                       f"VALUES ({placeholders})")
                with self._write_lock:
                    conn.executemany(sql, [r.row_values() for r in records])
                    conn.commit()
            self._mark_committed(len(records))
            # 入库成功 → 记录已进入 DB 权威；预留日志可被压缩（见 _maybe_compact_journal）
            self._committed_max_seq = max(self._committed_max_seq,
                                          max((r.seq for r in records), default=0))
        except sqlite3.IntegrityError as e:  # noqa: BLE001 seq 冲突 → 单写者纪律被破坏
            logger.error("审计链 seq 冲突（跨进程分配未生效？）: %s；已重同步 seq、"
                         "重放预留日志，并保留记录于 ring buffer（不静默丢弃）", e)
            self._note_degraded(f"seq_conflict: {e}")
            self._resync_seq()
            for r in records:
                self._buffer_failed(r)
            self._mark_committed(len(records))
        except Exception as e:  # noqa: BLE001 写失败 → ring buffer（审计绝不静默丢弃）
            logger.warning("审计链 SQLite 批量写入失败，降级 ring buffer: %s", e)
            self._note_degraded(f"db_write_failed: {e}")
            for r in records:
                self._buffer_failed(r)
            self._mark_committed(len(records))

    def _seqs_in_db(self, seqs: List[int]) -> set:
        """查询给定 seq 中**已入库**的那些（一次 IN 查询）"""
        if not seqs or not self._db_available:
            return set()
        present: set = set()
        chunk = 400
        try:
            with self._connect() as conn:
                for i in range(0, len(seqs), chunk):
                    part = [int(s) for s in seqs[i:i + chunk]]
                    marks = ",".join(["?"] * len(part))
                    rows = conn.execute(
                        f"SELECT seq FROM audit_chain WHERE seq IN ({marks})",
                        part).fetchall()
                    present.update(int(r["seq"]) for r in rows)
        except Exception as exc:  # noqa: BLE001 查不到按"全缺"处理（保守，宁重不丢）
            logger.debug("查询已入库 seq 失败（按全缺处理）: %s", exc)
            return set()
        return present

    def _drain_journal(self) -> List[AuditEntry]:
        """取回预留日志中**尚未入库、且不在途**的记录（**收敛，防丢**）

        【为什么水位不能只用 ``MAX(seq)``（实现期实测缺陷）】第一版用 DB 的
        ``max(seq)`` 作水位（``seq > db_max``）。这在"崩溃后先写了较大的 seq"
        时会漏：
          1. 崩溃前已分配 1..5（只在日志）；
          2. 新进程从链头恢复，``_next_seq=6``，先追加 seq 6 并由后台**先入库**；
          3. 此时 ``db_max=6`` ⇒ ``seq > 6`` 取不到任何行 ⇒ 1..5 **永远不补**
             ⇒ 空洞 + 静默丢失（实测用例 ``test_killed_process_does_not_deadlock_...``
             复现：恢复后只剩 1 条）。
        正确口径是**精确判定"哪些 seq 不在库里"**：取日志里出现过的 seq 集合，
        一次 ``IN`` 查询问 DB 谁已存在，缺的才补。代价是一条查询，而收敛是
        **按需触发**（见 ``_journal_needs_drain``），不在写路径上。
        """
        if not self._journal_enabled or self._closed:
            return []
        if not self._journal_needs_drain:
            return []
        try:
            rows = self._journal.read_since(0, limit=5000)
        except Exception as exc:  # noqa: BLE001
            logger.debug("预留日志收敛读取失败: %s", exc)
            return []
        if not rows:
            self._journal_needs_drain = False
            return []

        with self._count_lock:
            inflight = set(self._inflight_seq)
        candidates: List[AuditEntry] = []
        for row in rows:
            try:
                entry = self._entry_from_journal_row(row)
            except Exception as exc:  # noqa: BLE001 单行脏数据不拖垮整批
                logger.warning("预留日志单行还原失败（跳过并计数）: %s", exc)
                continue
            if int(entry.seq) in inflight:
                continue            # 在途：交给队列路径，避免重复插入
            candidates.append(entry)
        if not candidates:
            self._journal_needs_drain = False
            return []

        present = self._seqs_in_db([int(e.seq) for e in candidates])
        out = [e for e in candidates if int(e.seq) not in present]
        if not out:
            self._journal_needs_drain = False
            return []
        out.sort(key=lambda e: int(e.seq))
        self._journal_replay_count += len(out)
        self._observed_days.update(day_of_ts(e.ts) for e in out)
        with self._count_lock:
            self._inflight_seq.update(int(e.seq) for e in out)
        return out

    def _recover_backlog(self) -> int:
        """把预留日志的滞留记录同步补写进 DB（**仅 writer 线程调用**）

        【为什么不能在 ``append()`` 里（锁内）调用（实测缺陷）】那会把
        ``synchronous=FULL`` 提交放进跨进程临界区，4 进程争用时临界区可达数秒
        ⇒ 其它进程取锁超时 ⇒ 降级分配 ⇒ **重复 seq**。DB I/O 只属于 writer 线程；
        ``append()`` 的临界区必须保持"只有内存计算 + 一次日志写"。
        """
        if not self._journal_needs_drain or self._closed:
            return 0
        pending = self._drain_journal()
        if not pending:
            return 0
        before = self._failed_len()
        self._write_to_db(pending)
        written = max(self._failed_len() - before, 0)
        logger.info("审计链预留日志同步收敛：补写 %d 条（崩溃恢复/降级滞留）",
                    len(pending))
        return len(pending) - written

    def _maybe_compact_journal(self) -> bool:
        """预留日志全部入库后压缩它（释放磁盘；**保留最近 retain 条**）

        【必须节流（实测性能缺陷）】第一版在 writer 每轮都检查并压缩，而
        "每轮"在低延迟写入下等价于"每次 append"——于是每次 append 都触发一次
        **512 行日志的重写**，实测把单条 append 的 p50 从 **0.035ms 拖到 2.4ms**
        （约 70×），直接违反「单进程写入 p99 不退化」。现在两道闸：

        - **行数闸**：自上次压缩以来新增行数超过 ``max(2*retain, 512)`` 才考虑；
        - **时间闸**：两次"考虑压缩"之间至少隔 ``COMPACT_CHECK_INTERVAL_S``，
          避免连 `SELECT MAX(seq)`（实测 3.5ms）都被每轮调用。
        """
        if not self._journal_enabled or self._closed:
            return False
        if self._journal.rows_since_compact < self._journal_compact_min_rows:
            return False
        now = time.monotonic()
        if now - self._last_compact_check < COMPACT_CHECK_INTERVAL_S:
            return False
        self._last_compact_check = now
        try:
            if not self._journal.exists():
                return False
            # 【水位必须是"**连续**已入库"的水位】压缩只允许丢弃"DB 里确定还有"的行。
            # 若 DB 因崩溃恢复窗口而暂时非连续（如只有 seq 6），用 MAX(seq) 当水位
            # 会把尚未入库的 1..5 一并丢掉 —— 静默丢数据。故先算连续前缀水位。
            db_seq, contiguous = self._committed_watermark()
            if not contiguous:
                return False
            if self._journal.max_seq() <= 0:
                return False
            if self._lock_enabled:
                with self._cp_lock.locked(0.2, on_timeout="degrade") as ctx:
                    if not ctx.acquired:
                        return False
                    # 锁内复查：压缩期间别人可能又追加了
                    db_seq, contiguous = self._committed_watermark()
                    if not contiguous:
                        return False
                    done = self._journal.compact(upto_seq=db_seq,
                                                 retain=self._journal_retain)
            else:
                done = self._journal.compact(upto_seq=db_seq,
                                             retain=self._journal_retain)
            if done:
                self._journal_compact_count += 1
            return done
        except Exception as exc:  # noqa: BLE001 压缩失败不影响写入
            logger.debug("预留日志压缩跳过: %s", exc)
            return False

    def _mark_committed(self, n: int) -> None:
        with self._count_lock:
            self._commit_count += n

    def _clear_inflight(self, records: List[AuditEntry]) -> None:
        """把一批记录移出"在途"（无论成功入库还是落到 ring buffer）

        语义：在途 = "还在队列里等着被这轮 writer 处理"。一旦本轮处理完
        （成功 / 冲突 / 失败转 ring buffer），它就不再占用队列通道，
        可以（在需要时）被预留日志收敛路径接手。
        """
        with self._count_lock:
            for r in records:
                self._inflight_seq.discard(int(r.seq))

    def _writer_loop(self) -> None:
        while not self._closed:
            # 【每轮先收敛滞留，再消费队列（顺序不可颠倒）】崩溃/降级遗留的旧
            # 记录必须**先于**新记录入库，否则 DB 会出现临时空洞，``verify_chain``
            # 在窗口期会误报"链断"。
            #
            # 【为什么收敛必须在这里、而不是在 append() 内（实测缺陷）】第一版把它
            # 放在 `append()` 的跨进程锁内，于是锁内要跑 DB 查询 + ``synchronous=FULL``
            # 提交（实测 17–22ms，4 进程争用时受 ``busy_timeout`` 影响可达数秒）
            # ⇒ 其它进程 2s 取锁超时 ⇒ 走进降级分配 ⇒ **重复 seq**（实测复现
            # ``duplicates=[80]``）。DB 工作只属于 writer 线程，锁内不做 DB I/O。
            if self._journal_needs_drain:
                try:
                    stranded = self._drain_journal()
                    if stranded:
                        self._write_to_db(stranded)
                except Exception as e:  # noqa: BLE001 收敛失败不影响正常写入
                    logger.debug("审计 writer 收敛滞留失败: %s", e)
            batch: List[AuditEntry] = []
            try:
                first = self._queue.get(timeout=WRITER_POLL_INTERVAL)
                if first is None:
                    continue
                batch.append(first)
                while len(batch) < WRITER_BATCH_SIZE:
                    try:
                        item = self._queue.get_nowait()
                    except queue_module.Empty:
                        break
                    if item is None:
                        continue
                    batch.append(item)
            except queue_module.Empty:
                self._maybe_compact_journal()
                self._maybe_auto_seal()
                continue
            except Exception as e:  # noqa: BLE001
                logger.debug("审计 writer 取队异常: %s", e)
                continue
            if batch:
                self._write_to_db(batch)
                self._maybe_compact_journal()
                self._maybe_auto_seal()

    def _maybe_auto_seal(self) -> None:
        """自动封存「已过完的 UTC 日」的 Merkle 根（后台线程内执行，不占 append 路径）

        只封**确实有记录**的日（`_observed_days`），且每轮最多 `AUTO_SEAL_MAX_DAYS` 天：
        否则「首条记录 ts 很旧」（如回填/导入 2020 年数据）会触发数千个空日封存，
        后台线程长时间不退出（`close()` join 超时 → writer 线程泄漏）。
        判定用**内存内的最后一条 ts**，避免每批都开库查询。
        """
        if not self._auto_seal or self._closed:
            return
        try:
            today = datetime.now(timezone.utc).date().isoformat()
            with self._append_lock:
                pending = sorted(d for d in self._observed_days
                                 if d < today and d not in self._sealed_days)
            if not pending:
                return
            for day in pending[:AUTO_SEAL_MAX_DAYS]:
                if self._closed:
                    return
                self.daily_merkle_root(day)
            if len(pending) > AUTO_SEAL_MAX_DAYS:
                logger.debug("自动封存仍有 %d 天待处理（下一轮继续）",
                             len(pending) - AUTO_SEAL_MAX_DAYS)
        except Exception as e:  # noqa: BLE001 自动封存 best-effort，不影响写入
            logger.debug("自动封存每日根失败: %s", e)

    @staticmethod
    def _days_between(first_day: str, end_day: str) -> List[str]:
        from datetime import date as _date, timedelta
        try:
            start = _date.fromisoformat(first_day)
            end = _date.fromisoformat(end_day)
        except ValueError:
            return []
        out: List[str] = []
        cur = start
        while cur < end:
            out.append(cur.isoformat())
            cur += timedelta(days=1)
        return out

    def _peek_last_ts(self) -> str:
        """取已持久化（或缓冲）的最后一条 ts，无则空串（writer 线程内不自锁）"""
        try:
            rows = self._query_rows(limit=1, order_desc=True, refresh=False)
            if rows:
                return str(rows[-1]["ts"])
        except Exception:  # noqa: BLE001
            pass
        if self._failed_len():
            buffered = self._failed_snapshot()
            if buffered:
                return str(buffered[-1].ts)
        return ""

    def flush(self, timeout: float = FLUSH_TIMEOUT) -> bool:
        """等待已 append 的记录全部提交 SQLite（测试/收尾用）

        【S8-02：把"滞留收敛"纳入 flush 语义（实测缺陷）】崩溃/降级留在预留日志
        里的记录也必须在这里被补写——否则用例（以及运维脚本）在
        ``flush()`` 之后仍可能读到"少了一批"的库：实测
        ``test_killed_process_records_recovered_from_journal`` 读到
        ``recovered=1/25``，原因是 writer 线程把收敛放在每轮循环**开头**，
        之后阻塞在 ``queue.get(0.5s)``，调用方等不到那一次收敛。
        ``flush()`` 是**持久化屏障**，必须包含它。

        注意：writer 线程自身调用时立即返回（否则等待自己提交会自锁）。
        """
        if self._in_writer_thread():
            return True
        if self._journal_needs_drain:
            try:
                self._recover_backlog()
            except Exception as exc:  # noqa: BLE001 收敛失败仍继续等待队列提交
                logger.warning("flush 期间收敛预留日志失败（已计数）: %s", exc)
        with self._count_lock:
            target = self._enqueue_count
        deadline = time.time() + max(0.0, timeout)
        while time.time() < deadline:
            with self._count_lock:
                if self._commit_count >= target:
                    return True
            time.sleep(0.005)
        with self._count_lock:
            return self._commit_count >= target

    def _in_writer_thread(self) -> bool:
        """当前线程是否就是本实例的 writer 线程"""
        return (self._writer_thread is not None
                and threading.current_thread() is self._writer_thread)

    def close(self, timeout: float = FLUSH_TIMEOUT) -> bool:
        """优雅关闭：flush 残留 → 停 writer → 释放单写者登记（幂等）"""
        if self._closed:
            return True
        self.flush(timeout=timeout)
        self._closed = True
        if self._writer_thread is not None:
            try:
                self._queue.put(None, timeout=1.0)
            except Exception:  # noqa: BLE001
                pass
            if self._writer_thread.is_alive():
                self._writer_thread.join(timeout=timeout)
            if self._writer_thread.is_alive():   # 显式暴露：后台线程未能及时退出
                logger.warning("审计 writer 线程未在 %.1fs 内退出（可能仍在封存每日根）",
                               timeout)
        residual: List[AuditEntry] = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue_module.Empty:
                break
            if item is not None:
                residual.append(item)
        if residual:
            if self._degraded:
                # 【S8-02：这里是原来的静默丢弃点】旧实现"降级时直接丢弃 residual"，
                # 而这些记录**已经持久化在预留日志里**，丢弃的是内存副本而非数据；
                # 但仍必须**显式说明**，否则看上去就是"关一次链丢一批审计"。
                logger.warning(
                    "审计链关闭时处于降级态：%d 条待入库记录未提交，已保留在预留日志 %s"
                    "（下次启动自动重放，不丢失）", len(residual), self._journal.path)
                self._note_degraded(
                    f"close_with_pending（{len(residual)} 条待入库；"
                    f"已保留于预留日志，下次启动重放）")
            else:
                self._write_to_db(residual)
        # 尽力清空预留日志中已入库的部分（关链后不再有 writer 帮它收敛）
        if not self._degraded:
            self._maybe_compact_journal()
        self._journal.close()
        if self._role == "writer":
            self._release_writer_slot()
        return True

    def __enter__(self) -> "AuditChain":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def clear(self) -> None:
        """清空台账（**测试专用**显式动作）"""
        while True:
            try:
                self._queue.get_nowait()
            except queue_module.Empty:
                break
        with self._buffer_lock:
            self._failed_buffer.clear()
        # 【S8-02】此 DELETE 是"全模块唯一 DELETE"（既有不变量测试按
        # "DELETE 前 400 字符内出现 def clear" 校验，故本行须紧贴函数头）。
        # 必须**同步清预留日志**：否则只清了 DB，日志里的旧记录会被
        # `_drain_journal` 重放回库，与新分配的 seq 正面撞车
        # （实测 `UNIQUE constraint failed: audit_chain.seq`）。
        # 语义上这也对：clear() 的意图是"这条链回到空"，日志是链的一部分。
        try:
            with self._connect() as conn:
                with self._write_lock:
                    conn.execute("DELETE FROM audit_chain")
                    conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.warning("清空 audit_chain 失败: %s", e)
        if self._journal_enabled:
            try:
                # 取锁只是"礼貌"：清库是显式的破坏性测试动作，拿不到锁也必须执行
                with self._cp_lock.locked(1.0, on_timeout="degrade"):
                    pass
                self._journal.compact(upto_seq=10 ** 18)
            except Exception as exc:  # noqa: BLE001 清日志失败不影响清库结果
                logger.warning("清空预留日志失败（DB 已清空，日志残留会重放）: %s", exc)
        with self._append_lock:
            self._next_seq = 1
            self._last_hash = GENESIS_PREV_HASH
        with self._count_lock:
            self._enqueue_count = 0
            self._commit_count = 0
        self._committed_max_seq = 0
        self._journal_replay_count = 0
        self._buffer_dropped_count = 0

    # ── 读取路径 ────────────────────────────────────────────

    def _query_rows(self, *, start_seq: Optional[int] = None,
                    end_seq: Optional[int] = None, source: Optional[str] = None,
                    action: Optional[str] = None, actor: Optional[str] = None,
                    day: Optional[str] = None, trace_id: Optional[str] = None,
                    limit: Optional[int] = None, offset: int = 0,
                    order_desc: bool = False,
                    refresh: bool = True) -> List[sqlite3.Row]:
        if not self._db_available:
            return []
        if refresh and self._role == "writer":
            self.flush(timeout=FLUSH_TIMEOUT)
        where: List[str] = []
        params: List[Any] = []
        if start_seq is not None:
            where.append("seq >= ?")
            params.append(int(start_seq))
        if end_seq is not None:
            where.append("seq <= ?")
            params.append(int(end_seq))
        if source:
            where.append("source = ?")
            params.append(str(source))
        if action:
            where.append("action = ?")
            params.append(str(action))
        if actor:
            where.append("actor = ?")
            params.append(str(actor))
        if day:
            where.append("ts LIKE ?")
            params.append(f"{day}%")
        if trace_id:
            where.append("trace_id = ?")
            params.append(str(trace_id))
        sql = "SELECT * FROM audit_chain"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY seq DESC" if order_desc else " ORDER BY seq ASC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
            if offset:
                sql += " OFFSET ?"
                params.append(int(offset))
        with self._connect() as conn:
            rows = list(conn.execute(sql, params).fetchall())
        if order_desc:
            rows.reverse()
        return rows

    def _buffered_extra(self, seen: set) -> List[AuditEntry]:
        """降级 ring buffer + 预留日志中尚未落库的记录（按 seq 升序，去重）

        【S8-02：为什么要把预留日志也算进来】seq 分配后**先写日志再入队**，
        因此"刚 append 但还没入库"的记录可能在日志里而不在 ring buffer 里
        （典型是队满时）。读路径承诺"读得到刚写的"，故两者都要合并。
        """
        extra = [e for e in self._failed_snapshot() if e.seq not in seen]
        if self._journal_enabled:
            try:
                db_seq, _ = self._db_head()
                for row in self._journal.read_since(db_seq, limit=2000):
                    try:
                        entry = self._entry_from_journal_row(row)
                    except Exception:  # noqa: BLE001 单行脏数据跳过
                        continue
                    if entry.seq not in seen:
                        extra.append(entry)
            except Exception as exc:  # noqa: BLE001 读不到日志不影响 DB 查询结果
                logger.debug("预留日志待入库记录合并失败: %s", exc)
        # 去重（同一 seq 可能既在 ring buffer 又在日志里），再按 seq 排序
        deduped: Dict[int, AuditEntry] = {}
        for e in extra:
            deduped.setdefault(int(e.seq), e)
        return sorted(deduped.values(), key=lambda e: e.seq)

    def entries(self, *, start_seq: Optional[int] = None,
                end_seq: Optional[int] = None, source: Optional[str] = None,
                action: Optional[str] = None, actor: Optional[str] = None,
                day: Optional[str] = None, trace_id: Optional[str] = None,
                limit: Optional[int] = None) -> List[AuditEntry]:
        """按条件读取记录（seq 升序；合并降级缓冲，读得到刚写的）"""
        rows = self._query_rows(start_seq=start_seq, end_seq=end_seq, source=source,
                               action=action, actor=actor, day=day,
                               trace_id=trace_id, limit=limit)
        out = [AuditEntry.from_row(r) for r in rows]
        seen = {e.seq for e in out}
        # 【S8-02 修正的短路条件（实测缺陷）】原条件只看 `_failed_buffer`（进程内
        # ring buffer），是"ring buffer 是唯一额外来源"时代的优化。现在
        # **预留日志也是额外来源**（先落日志再入队），于是出现空窗：
        # "记录已提交入库 → DB 随后被判不可用（读返回空）→ ring buffer 也是空"
        # ⇒ 刚写的记录读不到。实测该窗口使
        # `test_failed_write_keeps_records_visible` 偶发绿灯（12 次复现 1–3 次）。
        # 故只要**日志可用**就必须走合并路径。
        if self._failed_len() or self._journal_enabled:
            extra = self._buffered_extra(seen)
            if start_seq is not None:
                extra = [e for e in extra if e.seq >= start_seq]
            if end_seq is not None:
                extra = [e for e in extra if e.seq <= end_seq]
            if source:
                extra = [e for e in extra if e.source == source]
            if action:
                extra = [e for e in extra if e.action == action]
            if actor:
                extra = [e for e in extra if e.actor == actor]
            if day:
                extra = [e for e in extra if day_of_ts(e.ts) == day]
            out.extend(extra)
            out.sort(key=lambda e: e.seq)
            if limit is not None:
                out = out[:limit]
        return out

    def iter_entries(self, *, batch: int = 500, **kwargs: Any) -> Iterator[AuditEntry]:
        """流式迭代（全链重算用，避免一次性载入内存）"""
        cur = kwargs.pop("start_seq", None) or 1
        end_seq = kwargs.pop("end_seq", None)
        kwargs.pop("limit", None)
        while True:
            chunk = self.entries(start_seq=cur, end_seq=end_seq, limit=batch, **kwargs)
            if not chunk:
                return
            for e in chunk:
                yield e
            if len(chunk) < batch:
                return
            cur = chunk[-1].seq + 1

    def get(self, seq: int) -> Optional[AuditEntry]:
        """按 seq 取单条"""
        rows = self._query_rows(start_seq=int(seq), end_seq=int(seq))
        if rows:
            return AuditEntry.from_row(rows[0])
        for e in self._failed_snapshot():
            if e.seq == int(seq):
                return e
        return None

    def last_entry(self) -> Optional[AuditEntry]:
        rows = self._query_rows(limit=1, order_desc=True)
        if rows:
            return AuditEntry.from_row(rows[-1])
        buffered = self._failed_snapshot()
        if buffered:
            return buffered[-1]
        return None

    def count(self, **kwargs: Any) -> int:
        """记录数（默认全表；支持与 entries 相同的过滤条件）"""
        rows = self._query_rows(limit=None, **kwargs)
        seen = {int(r["seq"]) for r in rows}
        extra = self._buffered_extra(seen)
        return len(rows) + len(extra)

    def entries_of_day(self, day: str) -> List[AuditEntry]:
        """某 UTC 日的全部记录（seq 升序）"""
        return self.entries(day=day)

    def seq_range(self) -> Tuple[int, int]:
        """(最小 seq, 最大 seq)；空链返回 (0, 0)"""
        buffered = self._failed_snapshot()
        rows = self._query_rows(limit=None)
        if not rows:
            buffered_seqs = [e.seq for e in buffered]
            if buffered_seqs:
                return (min(buffered_seqs), max(buffered_seqs))
            return (0, 0)
        lo, hi = int(rows[0]["seq"]), int(rows[-1]["seq"])
        for e in buffered:
            lo, hi = min(lo, e.seq), max(hi, e.seq)
        return (lo, hi)

    # ── 验签 ────────────────────────────────────────────────

    def verify_chain(self, *, start_seq: Optional[int] = None,
                     end_seq: Optional[int] = None,
                     anchor_prev_hash: Optional[str] = None) -> ChainVerification:
        """重算全链（或指定区间）并比对，报告首个篡改位置

        Args:
            start_seq: 锚点 seq；None → 从最小已存 seq 起。
            end_seq: 终点 seq（含）；None → 链头。
            anchor_prev_hash: 锚点处期望的前驱哈希；None → 若 start_seq 为链首则用创世
                哈希，否则取上一条记录的 self_hash（局部锚点重算）。
        """
        rows = self._query_rows(start_seq=start_seq, end_seq=end_seq)
        entries = [AuditEntry.from_row(r) for r in rows]
        seen = {e.seq for e in entries}
        if self._failed_len():
            extra = self._buffered_extra(seen)
            if start_seq is not None:
                extra = [e for e in extra if e.seq >= start_seq]
            if end_seq is not None:
                extra = [e for e in extra if e.seq <= end_seq]
            entries.extend(extra)
            entries.sort(key=lambda e: e.seq)
        if not entries:
            return ChainVerification(ok=True, checked=0, reason=REASON_EMPTY,
                                     detail="空链：无记录可校验",
                                     anchor_seq=start_seq)
        if anchor_prev_hash is None:
            if start_seq is not None and entries[0].seq > 1:
                prev = self.get(entries[0].seq - 1)
                # 锚点前的记录可见 → 用其 self_hash 作为锚；不可见则只从锚点自洽校验
                anchor_prev_hash = (prev.self_hash if prev is not None
                                    else entries[0].prev_hash)
            else:
                anchor_prev_hash = GENESIS_PREV_HASH
        return verify_chain(entries, anchor_seq=entries[0].seq,
                            anchor_prev_hash=anchor_prev_hash)

    def chain_head(self) -> Dict[str, Any]:
        """链头信息（供校验器/面板输出）"""
        last = self.last_entry()
        lo, hi = self.seq_range()
        return {
            "db_path": self._db_path,
            "count": self.count(),
            "first_seq": lo,
            "last_seq": hi,
            "head_self_hash": last.self_hash if last else "",
            "last_ts": last.ts if last else "",
            "degraded": self._degraded,
        }

    # ── 每日 Merkle 根 ──────────────────────────────────────

    def daily_merkle_root(self, date: Any = None, *, write: bool = True,
                          sign: bool = True, protect: Optional[bool] = None,
                          force: bool = False) -> DailyRoot:
        """生成某 UTC 日的 Merkle 根并写入受保护文件 `daily_roots.jsonl`

        Args:
            date: "YYYY-MM-DD" / date / datetime；None → 今日 UTC。
            write: 是否落盘（False 仅计算，供重放校验比较）。
            sign: 是否签名（False → 无签名字段，仅根哈希）。
            protect: 覆盖实例默认的只读保护开关。
            force: 同日已有根时是否重写一条新根（默认幂等：已有则直接返回）。
        """
        day = _normalize_day(date)
        day_entries = self.entries(day=day)
        leaves = [e.self_hash for e in day_entries]
        root = merkle_root(leaves)
        obj = DailyRoot(
            date=day,
            root_hash=root,
            leaf_count=len(leaves),
            first_seq=day_entries[0].seq if day_entries else 0,
            last_seq=day_entries[-1].seq if day_entries else 0,
            first_self_hash=day_entries[0].self_hash if day_entries else "",
            last_self_hash=day_entries[-1].self_hash if day_entries else "",
            created_at=utc_now_iso(),
        )
        if sign:
            obj.signature_scheme = self._signer.scheme
            obj.degraded = self._signer.degraded
            obj.degraded_reason = self._signer.degraded_reason
            obj.signer_public_key = self._signer.public_key_hex
            obj.signature = self._signer.sign(obj.signed_message())
        else:
            obj.degraded = True
            obj.degraded_reason = "sign=False（调用方显式关闭签名）"
        if write:
            existing = self.get_daily_root(day)
            if existing is not None and not force:
                return existing
            obj = self._append_daily_root(obj, protect=protect)
            self._sealed_days.add(day)
        return obj

    def _append_daily_root(self, obj: DailyRoot, *,
                           protect: Optional[bool] = None) -> DailyRoot:
        """受保护追加：进入前恢复写权限、追加后置只读（单机降级保护）"""
        do_protect = self._daily_root_protect if protect is None else bool(protect)
        path = self._roots_path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        prev_hash = self._last_root_entry_hash()
        obj.prev_entry_hash = prev_hash
        obj.entry_hash = obj.compute_entry_hash(prev_hash)
        with self._write_lock:
            with _appending(path) as fh:
                fh.write(canonical_json(obj.to_dict()) + "\n")
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except Exception:  # noqa: BLE001 平台差异
                    pass
                obj.protected = False
            if do_protect:
                obj.protected = _protect_readonly(path)
        return obj

    def _read_root_records(self) -> List[Dict[str, Any]]:
        path = self._roots_path
        if not os.path.exists(path):
            return []
        out: List[Dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except (ValueError, TypeError):
                        out.append({"_corrupt": line})
        except PermissionError:
            # 只读文件在 Windows 上仍可读；真无权限则显式返回空并告警
            logger.warning("每日根文件不可读: %s", path)
        return out

    def _last_root_entry_hash(self) -> str:
        recs = self._read_root_records()
        for rec in reversed(recs):
            if rec.get("entry_hash"):
                return str(rec["entry_hash"])
        return GENESIS_PREV_HASH

    def read_daily_roots(self) -> List[DailyRoot]:
        """读取全部每日根（跳过损坏行）"""
        out: List[DailyRoot] = []
        for rec in self._read_root_records():
            if "_corrupt" in rec:
                continue
            try:
                out.append(DailyRoot.from_dict(rec))
            except Exception:  # noqa: BLE001 字段异常行跳过
                continue
        return out

    def get_daily_root(self, date: Any) -> Optional[DailyRoot]:
        day = _normalize_day(date)
        for r in self.read_daily_roots():
            if r.date == day:
                return r
        return None

    def verify_daily_root(self, date: Any = None) -> RootsVerification:
        """重放校验：从当日 entries 重算 Merkle 根 + 校验签名 + 外层根链

        - **封印区间语义**：根记录含 `first_seq`/`last_seq`，重放只覆盖该 seq 区间
          （封印是对当时链头的前缀快照）；因此「封印后又追加当日新记录」不会造成
          误报，而区间内被删/被改则必然检出；
        - 根哈希重算比对（可重放）；
        - 签名校验（ed25519 用记录中的公钥；sha256-self 降级路径标注为占位）；
        - 外层链（prev_entry_hash → entry_hash）连续性，检出整日根被删/被改。
        """
        day = _normalize_day(date)
        recorded = self.get_daily_root(day)
        if recorded is None:
            return RootsVerification(ok=False, date=day, reason="root_not_found",
                                     detail=f"未找到 {day} 的每日根记录")
        if recorded.last_seq and recorded.last_seq >= recorded.first_seq:
            sealed = self.entries(start_seq=recorded.first_seq,
                                  end_seq=recorded.last_seq)
        else:                       # 空日根（leaf_count=0）或无区间信息 → 回退按日取
            sealed = self.entries(day=day)
        day_entries = sealed
        leaves = [e.self_hash for e in day_entries]
        recomputed = merkle_root(leaves)
        # 叶子自洽性：每日根封印的是 self_hash，若某条载荷/字段被改（self_hash 列未变，
        # 或列被改），根重算仍可能通过，故此处补一层逐条两级哈希校验。
        bad_leaf = next((e for e in day_entries
                         if e.recompute_payload_hash() != e.payload_hash
                         or e.recompute_self_hash() != e.self_hash), None)
        sig_ok = False
        if recorded.signature:
            sig_ok = RootsSigner.verify(
                recorded.signed_message(), recorded.signature,
                scheme=recorded.signature_scheme,
                public_key_hex=recorded.signer_public_key)
        chain_ok, chain_checked, chain_detail = self._verify_root_chain()
        count_ok = (recorded.leaf_count == len(leaves))
        ok = (recomputed == recorded.root_hash and chain_ok and bad_leaf is None
              and count_ok
              and (sig_ok or recorded.signature_scheme == _SIGN_SCHEME_SHA256_SELF
                   or not recorded.signature))
        reason = ""
        detail = chain_detail
        if recomputed != recorded.root_hash:
            reason = "root_hash_mismatch"
            detail = (f"重算={recomputed[:16]}… ≠ 记录={recorded.root_hash[:16]}…"
                      f"（封印区间 seq {recorded.first_seq}..{recorded.last_seq}，"
                      f"{len(leaves)} 条叶子）")
        elif not count_ok:
            reason = "leaf_count_mismatch"
            detail = (f"封印区间实际 {len(leaves)} 条 ≠ 记录 {recorded.leaf_count} 条"
                      f"（区间内记录被删除或插入）")
        elif bad_leaf is not None:
            reason = "entry_hash_mismatch"
            detail = (f"当日记录 seq={bad_leaf.seq} 的两级哈希重算不一致"
                      f"（封印后条目被篡改）")
        elif not chain_ok:
            reason = "root_chain_broken"
        elif not sig_ok and recorded.signature_scheme == _SIGN_SCHEME_ED25519:
            reason = "signature_invalid"
            detail = "ed25519 签名校验失败"
        return RootsVerification(ok=ok, date=day, reason=reason, detail=detail,
                                 recomputed_root=recomputed,
                                 recorded_root=recorded.root_hash,
                                 signature_ok=sig_ok,
                                 signature_scheme=recorded.signature_scheme,
                                 signing_degraded=recorded.degraded,
                                 chains_ok=chain_ok,
                                 entries_verified=len(leaves),
                                 root_chain_checked=chain_checked)

    def _verify_root_chain(self) -> Tuple[bool, int, str]:
        """外层根链连续性校验（每日根文件自身不可被删改）"""
        recs = self._read_root_records()
        prev = GENESIS_PREV_HASH
        checked = 0
        for i, rec in enumerate(recs):
            if "_corrupt" in rec:
                return (False, checked, f"第 {i + 1} 行每日根记录损坏")
            try:
                obj = DailyRoot.from_dict(rec)
            except Exception as e:  # noqa: BLE001
                return (False, checked, f"第 {i + 1} 行每日根字段异常: {e}")
            if obj.prev_entry_hash != prev:
                return (False, checked,
                        f"每日根 {obj.date} 的 prev_entry_hash 与前一条 entry_hash 不一致")
            expect = obj.compute_entry_hash(obj.prev_entry_hash)
            if expect != obj.entry_hash:
                return (False, checked,
                        f"每日根 {obj.date} 的 entry_hash 重算不一致（根记录被改）")
            prev = obj.entry_hash
            checked += 1
        return (True, checked, "")

    def verify_daily_roots_all(self) -> List[RootsVerification]:
        """全部每日根逐个重放校验"""
        return [self.verify_daily_root(r.date) for r in self.read_daily_roots()]

    # ── 统计（面板/报告） ───────────────────────────────────

    def stats(self, *, verify: bool = True) -> Dict[str, Any]:
        """台账摘要：条数/来源分布/seq 区间/链头/校验结论/降级信息"""
        by_source: Dict[str, int] = {}
        by_actor: Dict[str, int] = {}
        for e in self.entries():
            by_source[e.source] = by_source.get(e.source, 0) + 1
            by_actor[e.actor] = by_actor.get(e.actor, 0) + 1
        last = self.last_entry()
        first = self.entries(limit=1)
        out: Dict[str, Any] = {
            "db_path": self._db_path,
            "roots_path": self._roots_path,
            "total": self.count(),
            "first_seq": first[0].seq if first else 0,
            "last_seq": last.seq if last else 0,
            "head_self_hash": last.self_hash if last else "",
            "last_ts": last.ts if last else "",
            "by_source": by_source,
            "by_actor": by_actor,
            "append_only": True,
            "schema_version": SCHEMA_VERSION,
            "degraded": self._degraded,
            "degraded_reason": self._degraded_reason,
            "failed_buffer": self._failed_len(),
            "daily_roots": len(self.read_daily_roots()),
            "signing_scheme": self._signer.scheme,
            "signing_degraded": self._signer.degraded,
            # ── S8-02：跨进程 seq / 锁 / 降级可观测 ──
            "seq_journal": self._journal.stats(),
            "seq_lock_path": self._lock_path,
            "seq_lock_held_by_self": self._cp_lock.held,
            "seq_alloc_reliable": (not self._lock_enabled)
                                 or self._cp_lock.held or self._seq_degraded_count == 0,
            "seq_degraded_count": self._seq_degraded_count,
            "queue_full_count": self._queue_full_count,
            "queue_maxsize": int(self._queue.maxsize),
            "buffer_dropped_count": self._buffer_dropped_count,
            "journal_replay_count": self._journal_replay_count,
            "journal_compact_count": self._journal_compact_count,
            "journal_write_failures": self._journal_write_failures,
            "committed_max_seq": self._committed_max_seq,
        }
        if verify:
            v = self.verify_chain()
            out["chain_ok"] = v.ok
            out["chain_reason"] = v.reason
            out["verified"] = v.checked
        return out

    # ── 只读视图 ────────────────────────────────────────────

    @classmethod
    def reader(cls, db_path: Optional[str] = None, **kwargs: Any) -> "AuditChain":
        """只读实例（Scheduler/Watchdog/巡检用；不占单写者登记，不启 writer 线程）"""
        kwargs.pop("role", None)
        kwargs.setdefault("auto_start_writer", False)
        kwargs.setdefault("auto_seal", False)
        kwargs.setdefault("enforce_single_writer", False)
        return cls(db_path, role="reader", **kwargs)


def _normalize_day(date: Any) -> str:
    """date → "YYYY-MM-DD"（UTC）"""
    if date is None:
        return datetime.now(timezone.utc).date().isoformat()
    if isinstance(date, datetime):
        dt = date if date.tzinfo is not None else date.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).date().isoformat()
    if hasattr(date, "isoformat") and not isinstance(date, str):
        return str(date.isoformat())[:10]
    return str(date)[:10]


__all__ = [
    "AuditChain", "AuditChainError", "AuditEntry", "AuditEntryError",
    "ChainVerification", "DailyRoot", "ReadOnlyChainError", "RootsSigner",
    "RootsVerification", "SingleWriterViolationError",
    "DEFAULT_DB_PATH", "DEFAULT_KEY_PATH", "DEFAULT_ROOTS_PATH",
    "EMPTY_MERKLE_ROOT", "GENESIS_PREV_HASH", "MERKLE_ALGO", "SCHEMA_VERSION",
    "SOURCE_AGENT", "SOURCE_MIGRATION", "SOURCE_SYSTEM", "SOURCE_UI", "SOURCES",
    "build_entry", "canonical_json", "canonical_record_json", "compute_payload_hash",
    "compute_self_hash", "day_of_ts", "get_audit_chain", "merkle_proof",
    "merkle_root", "normalize_ts", "reset_audit_chains", "self_hash_formula",
    "sha256_hex", "utc_now_iso", "verify_chain", "verify_merkle_proof",
]
