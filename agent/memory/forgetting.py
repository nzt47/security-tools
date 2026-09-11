"""遗忘与 TTL（v7.2 §4.3 遗忘三触发 / §8 被遗忘权 / §10 合规）

遗忘三触发（§4.3）
------------------
======  ============================================  ==================================
编号     触发条件                                       数据源
======  ============================================  ==================================
①       成功率 30 天 < 基线 × 0.7                      S2-01 统一台账 ``UnifiedTraceStore``
                                                      （基线取 descriptor.quality）
②       来源失效（descriptor ``deprecated`` / 来源摘除）  ``agent.descriptors.registry``
③       删除权（用户显式删除 / 被遗忘权）               显式 API 调用
补充     TTL 到期 → 自动降级为遗忘候选                   条目 ``ttl_expires_at``
======  ============================================  ==================================

执行纪律（hard constraint 4：先快照、后删除）
--------------------------------------------
1. **快照先于删除**：任何物理删除前必须先落快照；删除与快照一一对应可回溯。
2. **快照留 30 天**：①/② 类遗忘使用 ``full`` 快照（可用于回滚），到期自动清理。
3. **删除权例外**：③ 类（被遗忘权）使用 ``hash_only`` 墓碑快照 —— 只留
   ``content_hash`` 与主体伪引用，**不留内容、不留原始标识符**；否则"物理删除"
   会被 30 天快照抵消，与 §8「记忆物理删除」自相矛盾。
4. **删的是记忆不是证据**：审计链一个字节都不改写；主体标识符靠**销毁伪名盐**
   匿名化（见 ``agent.memory.identity``），链条仍可 ``verify()`` 验签通过。

环境变量
--------
- ``MEMORY_FORGET_WINDOW_DAYS``: 触发①统计窗口（默认 30）
- ``MEMORY_FORGET_SUCCESS_RATIO``: 触发①阈值比（默认 0.7）
- ``MEMORY_FORGET_MIN_SAMPLES``: 触发①最小样本数（默认 20，沿用项目「每能力 ≥20 条同类轨迹」口径纪律）
- ``MEMORY_SNAPSHOT_RETENTION_DAYS``: 快照保留天数（默认 30）
- ``MEMORY_SNAPSHOT_ROOT``: 快照根目录（默认 ``~/.cloudpivot/vault/snapshots``）
"""

import enum
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from agent.logging_utils import log_dict
from agent.memory.identity import (
    SubjectPseudonymizer,
    iter_audit_texts,
    record_memory_audit,
    scan_audit_for_identifiers,
    subject_ref,
)
from agent.memory.taxonomy import MemoryEntry

logger = logging.getLogger(__name__)

__all__ = [
    "FORGET_SUCCESS_RATE_RATIO",
    "FORGET_WINDOW_DAYS",
    "FORGET_MIN_SAMPLES",
    "SNAPSHOT_RETENTION_DAYS",
    "DEFAULT_SNAPSHOT_ROOT",
    "ForgetTrigger",
    "ForgetCandidate",
    "SuccessRateSample",
    "TraceQualitySource",
    "SourceStatus",
    "SourceValidityChecker",
    "SnapshotRecord",
    "MemorySnapshotStore",
    "ForgetBatchResult",
    "ErasureResult",
    "ForgettingReport",
    "ForgettingEngine",
]

#: §4.3 触发①阈值：成功率 30 天 < 基线 × 0.7
FORGET_SUCCESS_RATE_RATIO = 0.7

#: §4.3 触发①统计窗口
FORGET_WINDOW_DAYS = 30

#: 触发①最小样本数（沿用「每能力 ≥20 条同类轨迹」口径，避免小样本误杀）
FORGET_MIN_SAMPLES = 20

#: 快照保留天数（§4.3/§8）
SNAPSHOT_RETENTION_DAYS = 30

#: 快照默认根目录 —— **项目树之外**（§8 P7.2-18：备份/快照不得位于项目树内）
DEFAULT_SNAPSHOT_ROOT = os.path.join(
    os.path.expanduser("~"), ".cloudpivot", "vault", "snapshots")


class ForgetTrigger(str, enum.Enum):
    """遗忘触发来源"""

    SUCCESS_RATE = "success_rate"            # ① 成功率 30 天 < 基线×0.7
    SOURCE_INVALIDATED = "source_invalidated"  # ② 来源失效
    DELETION_RIGHT = "deletion_right"        # ③ 删除权 / 被遗忘权
    TTL_EXPIRED = "ttl_expired"              # 补充：TTL 到期降级为候选


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    """读取数值型环境变量；缺失/非法/越界 → 回退默认（批次总表 §三 硬约束 3）"""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        logger.warning(log_dict({
            "module_name": "memory.forgetting",
            "action": "env.invalid",
            "msg": "[forgetting] %s=%r 非法，回退默认 %s" % (name, raw, default),
        }))
        return default
    return value if value >= minimum else default


# ════════════════════════════════════════════════════════════
#  候选与证据
# ════════════════════════════════════════════════════════════


@dataclass
class ForgetCandidate:
    """遗忘候选（条目 + 触发原因 + 取证数据）"""

    entry: MemoryEntry
    trigger: ForgetTrigger
    reason: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def memory_id(self) -> str:
        return self.entry.id

    def as_dict(self) -> Dict[str, Any]:
        return {
            "memory_id": self.entry.id,
            "type": self.entry.type.value,
            "tenant_id": self.entry.tenant_id,
            "scope": self.entry.scope,
            "trigger": self.trigger.value,
            "reason": self.reason,
            "evidence": dict(self.evidence),
        }


@dataclass
class SuccessRateSample:
    """成功率样本（触发①的观测值）"""

    capability_id: str
    samples: int
    successes: int
    rate: Optional[float]
    since: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "samples": self.samples,
            "successes": self.successes,
            "rate": self.rate,
            "since": self.since,
        }


@dataclass
class SourceStatus:
    """来源（capability）有效性状态（触发②）"""

    capability_id: str
    exists: bool = False
    deprecated: bool = False
    stage: str = ""
    aliased_to: str = ""

    @property
    def invalid(self) -> bool:
        """来源失效：摘除（不存在）或 deprecated

        无来源指针（空 capability_id）时**不判定失效** —— 没有可判定的对象；
        若判为失效，会让"无来源记忆被静默清理"，与"宁可漏杀不可误删"相反。
        """
        if not str(self.capability_id or "").strip():
            return False
        return (not self.exists) or bool(self.deprecated)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "exists": self.exists,
            "deprecated": self.deprecated,
            "stage": self.stage,
            "aliased_to": self.aliased_to,
            "invalid": self.invalid,
        }


# ════════════════════════════════════════════════════════════
#  触发①数据源：S2-01 统一台账 quality
# ════════════════════════════════════════════════════════════


class TraceQualitySource:
    """30 天成功率数据源（包装 ``UnifiedTraceStore``，**惰性构造**）

    ``UnifiedTraceStore.__init__`` 会启动 daemon writer 线程并打开 SQLite，
    故此处只在首次真正查询时构造；测试请注入 ``trace_store`` 替身。
    """

    def __init__(self, trace_store: Any = None) -> None:
        self._store = trace_store
        self._owned = False

    @property
    def store(self) -> Any:
        if self._store is None:
            from agent.observability.trace_v2 import UnifiedTraceStore

            self._store = UnifiedTraceStore()
            self._owned = True
        return self._store

    def success_rate(
        self, capability_id: str, *, window_days: float = FORGET_WINDOW_DAYS,
        now: Optional[float] = None,
    ) -> SuccessRateSample:
        """统计窗口内该能力的成功率（无数据 → ``rate=None``）"""
        from agent.observability.trace_v2 import STATUS_SUCCESS

        ts = time.time() if now is None else float(now)
        since = ts - float(window_days) * 86400.0
        try:
            traces = self.store.query(capability_id=capability_id, since=since)
        except Exception as exc:  # noqa: BLE001 数据源不可用 → 视为无样本（不误杀）
            logger.warning(log_dict({
                "module_name": "memory.forgetting",
                "action": "quality.failed",
                "msg": "[forgetting] 成功率数据源不可用: %s" % exc,
            }))
            return SuccessRateSample(capability_id, 0, 0, None, since)
        steps = [t for t in (traces or []) if getattr(t, "capability_id", "") == capability_id]
        if not steps:
            return SuccessRateSample(capability_id, 0, 0, None, since)
        successes = [
            t for t in steps
            if str(getattr(getattr(t, "response", None), "status", "")) == STATUS_SUCCESS
        ]
        return SuccessRateSample(
            capability_id=capability_id,
            samples=len(steps),
            successes=len(successes),
            rate=len(successes) / float(len(steps)),
            since=since,
        )

    def close(self) -> None:
        """仅在内部构造过真实 store 时停止其后台线程"""
        if self._owned and self._store is not None:
            try:
                self._store.stop()
            except Exception:  # noqa: BLE001
                pass
            self._store = None
            self._owned = False


# ════════════════════════════════════════════════════════════
#  触发②数据源：descriptor 注册表
# ════════════════════════════════════════════════════════════


class SourceValidityChecker:
    """来源有效性检查（``DescriptorRegistry``：存在性 + deprecated 状态 + alias 归并）

    alias 归并（S1-01 三路投票去重）不算失效：先解析 canonical id 再判定，
    避免把"改名"误判为"摘除"。
    """

    def __init__(self, registry: Any = None) -> None:
        self._registry = registry
        self._owned = False

    @property
    def registry(self) -> Any:
        if self._registry is None:
            from agent.descriptors.registry import DescriptorRegistry

            self._registry = DescriptorRegistry()
            self._owned = True
        return self._registry

    def check(self, capability_id: str) -> SourceStatus:
        """检查来源是否失效（拿不到注册表 → 不判定失效，避免误删）"""
        cid = str(capability_id or "").strip()
        if not cid:
            return SourceStatus(capability_id="", exists=False)
        try:
            reg = self.registry
            canonical = cid
            aliased_to = ""
            resolve = getattr(reg, "resolve_alias", None)
            if callable(resolve):
                resolved = resolve(cid)
                if resolved and resolved != cid:
                    canonical = resolved
                    aliased_to = resolved
            descriptor = reg.get(canonical)
        except Exception as exc:  # noqa: BLE001 注册表不可用 → 不判定失效
            logger.warning(log_dict({
                "module_name": "memory.forgetting",
                "action": "source.failed",
                "msg": "[forgetting] descriptor 注册表不可用: %s" % exc,
            }))
            return SourceStatus(capability_id=cid, exists=True)
        if descriptor is None:
            return SourceStatus(capability_id=cid, exists=False, aliased_to=aliased_to)
        stage = ""
        try:
            raw_stage = getattr(getattr(descriptor, "evolution", None), "stage", None)
            stage = str(getattr(raw_stage, "value", raw_stage) or "")
        except Exception:  # noqa: BLE001
            stage = ""
        return SourceStatus(
            capability_id=cid,
            exists=True,
            deprecated=(stage == "deprecated"),
            stage=stage,
            aliased_to=aliased_to,
        )


# ════════════════════════════════════════════════════════════
#  快照（先快照后删除）
# ════════════════════════════════════════════════════════════


def _atomic_write_json(path: str, payload: Any) -> None:
    """原子写 JSON（临时文件 + replace），避免半写快照污染证据"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-%s" % uuid.uuid4().hex[:8]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, sort_keys=True, default=str)
    os.replace(tmp, path)


def _entries_digest(entries: Sequence[Dict[str, Any]]) -> str:
    """条目集合摘要（验签快照完整性；与审计链同族的 sha256 口径）"""
    blob = json.dumps(list(entries), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class SnapshotRecord:
    """一条快照记录（索引项）"""

    snapshot_id: str
    path: str
    created_at: float
    expires_at: float
    trigger: str
    reason: str
    mode: str
    count: int
    entries_sha256: str
    tenant_ids: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "path": self.path,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "trigger": self.trigger,
            "reason": self.reason,
            "mode": self.mode,
            "count": self.count,
            "entries_sha256": self.entries_sha256,
            "tenant_ids": list(self.tenant_ids),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SnapshotRecord":
        return cls(
            snapshot_id=str(data.get("snapshot_id") or ""),
            path=str(data.get("path") or ""),
            created_at=float(data.get("created_at") or 0.0),
            expires_at=float(data.get("expires_at") or 0.0),
            trigger=str(data.get("trigger") or ""),
            reason=str(data.get("reason") or ""),
            mode=str(data.get("mode") or "full"),
            count=int(data.get("count") or 0),
            entries_sha256=str(data.get("entries_sha256") or ""),
            tenant_ids=list(data.get("tenant_ids") or []),
        )


class MemorySnapshotStore:
    """遗忘快照存储（§4.3 快照留 30 天；§8 快照先于删除）

    两种模式：
    - ``full``：保留脱敏内容，供 ①/② 类遗忘回滚；
    - ``hash_only``：**墓碑模式**，内容置空、``subject_id`` 换伪引用，只留
      ``content_hash`` 供取证 —— 用于被遗忘权（③），确保"物理删除"不被快照抵消。
    """

    ENV_ROOT = "MEMORY_SNAPSHOT_ROOT"
    ENV_RETENTION = "MEMORY_SNAPSHOT_RETENTION_DAYS"

    def __init__(
        self,
        root: Optional[str] = None,
        *,
        retention_days: Optional[float] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.root = os.path.abspath(
            root or os.environ.get(self.ENV_ROOT) or DEFAULT_SNAPSHOT_ROOT)
        self.retention_days = (
            _env_float(self.ENV_RETENTION, float(SNAPSHOT_RETENTION_DAYS), minimum=0.0)
            if retention_days is None else float(retention_days)
        )
        self._clock: Callable[[], float] = clock or time.time

    def now(self) -> float:
        try:
            return float(self._clock())
        except Exception:  # noqa: BLE001
            return time.time()

    @property
    def index_path(self) -> str:
        return os.path.join(self.root, "index.json")

    # ── 索引 ──

    def _load_index(self) -> List[SnapshotRecord]:
        path = self.index_path
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            return [SnapshotRecord.from_dict(x) for x in (raw.get("snapshots") or [])]
        except Exception as exc:  # noqa: BLE001 索引损坏 → 空索引（不阻断遗忘）
            logger.warning(log_dict({
                "module_name": "memory.forgetting",
                "action": "snapshot.index.failed",
                "msg": "[forgetting] 快照索引读取失败（按空索引处理）: %s" % exc,
            }))
            return []

    def _save_index(self, records: Sequence[SnapshotRecord]) -> None:
        _atomic_write_json(self.index_path, {
            "version": 1,
            "retention_days": self.retention_days,
            "snapshots": [r.as_dict() for r in records],
        })

    # ── 快照 ──

    def capture(
        self,
        entries: Sequence[MemoryEntry],
        *,
        trigger: Any,
        reason: str = "",
        mode: str = "full",
        now: Optional[float] = None,
    ) -> SnapshotRecord:
        """先快照：把待遗忘条目落成一份带摘要的快照（**必须在删除之前调用**）"""
        ts = self.now() if now is None else float(now)
        trigger_value = str(getattr(trigger, "value", trigger) or "")
        snapshot_id = "snap_%d_%s" % (int(ts), uuid.uuid4().hex[:8])
        day = time.strftime("%Y%m%d", time.gmtime(ts))
        rel = os.path.join(day, snapshot_id + ".json")
        path = os.path.join(self.root, rel)
        payload_entries = [self._snapshot_payload(e, mode) for e in entries]
        record = SnapshotRecord(
            snapshot_id=snapshot_id,
            path=path,
            created_at=ts,
            expires_at=ts + self.retention_days * 86400.0,
            trigger=trigger_value,
            reason=str(reason or ""),
            mode=str(mode),
            count=len(payload_entries),
            entries_sha256=_entries_digest(payload_entries),
            tenant_ids=sorted({e.tenant_id for e in entries if e.tenant_id}),
        )
        _atomic_write_json(path, {
            "version": 1,
            "snapshot_id": record.snapshot_id,
            "created_at": record.created_at,
            "expires_at": record.expires_at,
            "trigger": record.trigger,
            "reason": record.reason,
            "mode": record.mode,
            "count": record.count,
            "tenant_ids": record.tenant_ids,
            "entries_sha256": record.entries_sha256,
            "entries": payload_entries,
        })
        records = self._load_index()
        records.append(record)
        self._save_index(records)
        logger.info(log_dict({
            "module_name": "memory.forgetting",
            "action": "snapshot.capture",
            "msg": "[forgetting] 快照已落: id=%s count=%d mode=%s trigger=%s"
                   % (record.snapshot_id, record.count, record.mode, record.trigger),
        }))
        return record

    @staticmethod
    def _snapshot_payload(entry: MemoryEntry, mode: str) -> Dict[str, Any]:
        """快照条目载荷；``hash_only`` 走墓碑化（不留内容与原始标识符）"""
        data = entry.to_dict()
        if str(mode) != "hash_only":
            return data
        data["content_redacted"] = ""
        data["subject_id"] = subject_ref(entry.subject_id) if entry.subject_id else ""
        data["tombstoned"] = True
        return data

    # ── 查询 / 校验 / 清理 ──

    def list_snapshots(self) -> List[SnapshotRecord]:
        return self._load_index()

    def get(self, snapshot_id: str) -> Optional[SnapshotRecord]:
        for record in self._load_index():
            if record.snapshot_id == snapshot_id:
                return record
        return None

    def read(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        record = self.get(snapshot_id)
        if record is None or not os.path.exists(record.path):
            return None
        with open(record.path, "r", encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            return None
        return loaded

    def verify(self, snapshot_id: str) -> bool:
        """校验快照摘要（防止快照被改写而"证据"失真）"""
        data = self.read(snapshot_id)
        if data is None:
            return False
        return _entries_digest(data.get("entries") or []) == str(data.get("entries_sha256") or "")

    def prune(self, *, now: Optional[float] = None) -> int:
        """清理到期快照（保留期 = ``retention_days``，默认 30 天）"""
        ts = self.now() if now is None else float(now)
        records = self._load_index()
        keep: List[SnapshotRecord] = []
        removed = 0
        for record in records:
            if record.expires_at and record.expires_at <= ts:
                try:
                    if os.path.exists(record.path):
                        os.unlink(record.path)
                        removed += 1
                except OSError as exc:
                    logger.warning(log_dict({
                        "module_name": "memory.forgetting",
                        "action": "snapshot.prune.failed",
                        "msg": "[forgetting] 快照清理失败 %s: %s" % (record.path, exc),
                    }))
                    keep.append(record)
                    continue
                continue
            keep.append(record)
        if removed or len(keep) != len(records):
            self._save_index(keep)
        return removed

    def redact_subject(self, subject_id: str) -> int:
        """把既有快照中该主体的条目**就地墓碑化**（删除权配套动作）

        否则：①/② 类遗忘留下的 ``full`` 快照（保留 30 天）会让"被遗忘权下的物理
        删除"被快照抵消。本方法把命中条目的内容清空、``subject_id`` 换伪引用，
        并重算摘要，使删除权在全量快照面同样成立。

        Returns:
            被墓碑化的快照条目数
        """
        sid = str(subject_id or "").strip()
        if not sid:
            return 0
        ref = subject_ref(sid)
        touched = 0
        records = self._load_index()
        for record in records:
            if not os.path.exists(record.path):
                continue
            try:
                with open(record.path, "r", encoding="utf-8") as fh:
                    data = json.load(fh) or {}
            except Exception:  # noqa: BLE001
                continue
            changed = False
            for item in data.get("entries") or []:
                if str(item.get("subject_id") or "") not in (sid, ref):
                    continue
                if item.get("tombstoned"):
                    continue
                item["content_redacted"] = ""
                item["subject_id"] = ref
                item["tombstoned"] = True
                touched += 1
                changed = True
            if changed:
                data["entries_sha256"] = _entries_digest(data.get("entries") or [])
                data.setdefault("redacted_subject_refs", [])
                if ref not in data["redacted_subject_refs"]:
                    data["redacted_subject_refs"].append(ref)
                record.entries_sha256 = data["entries_sha256"]
                _atomic_write_json(record.path, data)
        if touched:
            self._save_index(records)
        return touched


# ════════════════════════════════════════════════════════════
#  执行结果
# ════════════════════════════════════════════════════════════


@dataclass
class ForgetBatchResult:
    """一次遗忘批次的结果（标记 / 快照 / 删除）"""

    trigger: str
    reason: str = ""
    snapshot_id: str = ""
    snapshot_mode: str = "full"
    marked_ids: List[str] = field(default_factory=list)
    deleted_ids: List[str] = field(default_factory=list)
    failed_ids: List[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_ids)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trigger": self.trigger,
            "reason": self.reason,
            "snapshot_id": self.snapshot_id,
            "snapshot_mode": self.snapshot_mode,
            "marked_count": len(self.marked_ids),
            "deleted_count": self.deleted_count,
            "failed_count": len(self.failed_ids),
            "dry_run": self.dry_run,
            "deleted_ids": list(self.deleted_ids),
        }


@dataclass
class ErasureResult:
    """被遗忘权执行结果（§8：记忆物理删除、审计标识符匿名化、链保留）"""

    subject_ref: str = ""
    pseudonym_before: str = ""
    pseudonym_after: str = ""
    snapshot_id: str = ""
    snapshot_mode: str = "hash_only"
    deleted_ids: List[str] = field(default_factory=list)
    salts_shredded: bool = False
    snapshots_redacted: int = 0
    audit_recorded: bool = False
    chain_verified: bool = False
    chain_checked: int = 0
    chain_detail: str = ""
    chain_pseudonym_present: bool = False
    residual_identifier_hits: List[Tuple[str, str]] = field(default_factory=list)
    dry_run: bool = False

    @property
    def deleted_count(self) -> int:
        return len(self.deleted_ids)

    @property
    def anonymized(self) -> bool:
        """审计标识符匿名化达成：伪名已不可关联且链内无原始标识符残留"""
        return bool(self.salts_shredded) and not self.residual_identifier_hits

    def as_dict(self) -> Dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "pseudonym_before": self.pseudonym_before,
            "pseudonym_after": self.pseudonym_after,
            "snapshot_id": self.snapshot_id,
            "snapshot_mode": self.snapshot_mode,
            "deleted_count": self.deleted_count,
            "deleted_ids": list(self.deleted_ids),
            "salts_shredded": self.salts_shredded,
            "snapshots_redacted": self.snapshots_redacted,
            "audit_recorded": self.audit_recorded,
            "chain_verified": self.chain_verified,
            "chain_checked": self.chain_checked,
            "chain_detail": self.chain_detail,
            "chain_pseudonym_present": self.chain_pseudonym_present,
            "residual_identifier_hits": [list(h) for h in self.residual_identifier_hits],
            "anonymized": self.anonymized,
            "dry_run": self.dry_run,
        }


@dataclass
class ForgettingReport:
    """一次遗忘巡检的报告（scan + 可选执行）"""

    scanned: int = 0
    candidates: List[ForgetCandidate] = field(default_factory=list)
    marked: List[str] = field(default_factory=list)
    batches: List[ForgetBatchResult] = field(default_factory=list)
    pruned_snapshots: int = 0
    dry_run: bool = True

    def candidates_by_trigger(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for candidate in self.candidates:
            key = candidate.trigger.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def as_dict(self) -> Dict[str, Any]:
        return {
            "scanned": self.scanned,
            "candidate_count": len(self.candidates),
            "candidates_by_trigger": self.candidates_by_trigger(),
            "marked_count": len(self.marked),
            "batches": [b.as_dict() for b in self.batches],
            "pruned_snapshots": self.pruned_snapshots,
            "dry_run": self.dry_run,
            "candidates": [c.as_dict() for c in self.candidates],
        }


# ════════════════════════════════════════════════════════════
#  遗忘引擎
# ════════════════════════════════════════════════════════════


class ForgettingEngine:
    """遗忘三触发引擎（扫描 → 标记候选 → 先快照后删除；+ TTL 降级 + 被遗忘权）

    参数（全部可注入，便于单测与报告复现）:
        store: ``LayeredMemoryStore``
        snapshots: ``MemorySnapshotStore``
        quality_source: 触发①数据源（默认包装 ``UnifiedTraceStore``）
        source_checker: 触发②数据源（默认 ``DescriptorRegistry``）
        baseline_provider: 基线提供者（默认取 descriptor.quality.success_rate）
        clock: 注入时钟（避免真实时钟边界）
    """

    def __init__(
        self,
        store: Any,
        *,
        snapshots: Optional[MemorySnapshotStore] = None,
        quality_source: Optional[TraceQualitySource] = None,
        source_checker: Optional[SourceValidityChecker] = None,
        baseline_provider: Optional[Callable[[str], Optional[float]]] = None,
        pseudonymizer: Optional[SubjectPseudonymizer] = None,
        clock: Optional[Callable[[], float]] = None,
        window_days: Optional[float] = None,
        success_ratio: Optional[float] = None,
        min_samples: Optional[int] = None,
        audit: bool = True,
    ) -> None:
        self.store = store
        self._clock: Callable[[], float] = clock or getattr(store, "_clock", None) or time.time
        self.snapshots = snapshots or MemorySnapshotStore(clock=self._clock)
        self.quality_source = quality_source or TraceQualitySource()
        self.source_checker = source_checker or SourceValidityChecker()
        self.baseline_provider = baseline_provider
        self.pseudonymizer = pseudonymizer or getattr(store, "pseudonymizer", None) or (
            SubjectPseudonymizer())
        self.window_days = (
            _env_float("MEMORY_FORGET_WINDOW_DAYS", float(FORGET_WINDOW_DAYS), minimum=0.0)
            if window_days is None else float(window_days)
        )
        self.success_ratio = (
            _env_float("MEMORY_FORGET_SUCCESS_RATIO", FORGET_SUCCESS_RATE_RATIO, minimum=0.0)
            if success_ratio is None else float(success_ratio)
        )
        self.min_samples = (
            int(_env_float("MEMORY_FORGET_MIN_SAMPLES", float(FORGET_MIN_SAMPLES), minimum=1.0))
            if min_samples is None else int(min_samples)
        )
        self.audit = bool(audit)

    # ── 时钟 ──

    def now(self) -> float:
        try:
            return float(self._clock())
        except Exception:  # noqa: BLE001
            return time.time()

    # ── TTL：到期自动降级为遗忘候选 ──

    async def apply_ttl(
        self, entries: Optional[Sequence[MemoryEntry]] = None, *, now: Optional[float] = None
    ) -> List[ForgetCandidate]:
        """TTL 到期 → 标记 ``forget_candidate`` 并返回候选（工作记忆短 TTL，事实/策略长 TTL）"""
        ts = self.now() if now is None else float(now)
        pool = list(entries) if entries is not None else await self.store.all_entries()
        candidates: List[ForgetCandidate] = []
        for entry in pool:
            if not entry.is_expired(ts):
                continue
            candidates.append(ForgetCandidate(
                entry=entry,
                trigger=ForgetTrigger.TTL_EXPIRED,
                reason="ttl_expired",
                evidence={
                    "ttl_expires_at": entry.ttl_expires_at,
                    "now": ts,
                    "expired_seconds": ts - float(entry.ttl_expires_at or ts),
                    "layer": entry.type.value,
                },
            ))
            if not entry.forget_candidate:
                await self.store.mark_forget_candidate(entry, "ttl_expired")
        return candidates

    # ── 触发①：成功率 30 天 < 基线 × 0.7 ──

    def baseline_for(self, capability_id: str) -> Optional[float]:
        """基线：显式 provider > descriptor.quality.success_rate（无基线 → None）

        「真实流量未达每能力 ≥20 条同类轨迹，不得声称已实现真实能力内化」口径同样
        约束此处：``sample_count`` 为 0 的 descriptor 不提供基线，触发①不生效。
        """
        if self.baseline_provider is not None:
            try:
                return self.baseline_provider(capability_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning(log_dict({
                    "module_name": "memory.forgetting",
                    "action": "baseline.failed",
                    "msg": "[forgetting] 基线提供者异常: %s" % exc,
                }))
                return None
        try:
            descriptor = self.source_checker.registry.get(capability_id)
        except Exception:  # noqa: BLE001
            return None
        if descriptor is None:
            return None
        quality = getattr(descriptor, "quality", None)
        if quality is None:
            return None
        sample_count = float(getattr(quality, "sample_count", 0) or 0)
        baseline = float(getattr(quality, "success_rate", 0.0) or 0.0)
        if sample_count <= 0 or baseline <= 0:
            return None
        return baseline

    def evaluate_success_rate(
        self,
        entry: MemoryEntry,
        *,
        now: Optional[float] = None,
        sample: Optional[SuccessRateSample] = None,
    ) -> Optional[ForgetCandidate]:
        """触发①判定：观测成功率 < 基线 × ratio（且样本数达门槛）"""
        capability_id = str(entry.source_capability_id or "").strip()
        if not capability_id:
            return None
        baseline = self.baseline_for(capability_id)
        if baseline is None:
            return None
        obs = sample or self.quality_source.success_rate(
            capability_id, window_days=self.window_days, now=now)
        if obs.rate is None or obs.samples < self.min_samples:
            return None
        threshold = baseline * self.success_ratio
        if obs.rate >= threshold:
            return None
        return ForgetCandidate(
            entry=entry,
            trigger=ForgetTrigger.SUCCESS_RATE,
            reason="success_rate_below_baseline",
            evidence={
                "capability_id": capability_id,
                "observed_rate": obs.rate,
                "baseline": baseline,
                "threshold": threshold,
                "ratio": self.success_ratio,
                "samples": obs.samples,
                "window_days": self.window_days,
            },
        )

    # ── 触发②：来源失效 ──

    def evaluate_source(
        self, entry: MemoryEntry, *, status: Optional[SourceStatus] = None
    ) -> Optional[ForgetCandidate]:
        """触发②判定：descriptor deprecated 或来源摘除"""
        capability_id = str(entry.source_capability_id or "").strip()
        if not capability_id:
            return None
        st = status or self.source_checker.check(capability_id)
        if not st.invalid:
            return None
        return ForgetCandidate(
            entry=entry,
            trigger=ForgetTrigger.SOURCE_INVALIDATED,
            reason=("source_deprecated" if st.deprecated else "source_removed"),
            evidence=st.as_dict(),
        )

    # ── 扫描（TTL + ①②）──

    async def scan(
        self,
        entries: Optional[Sequence[MemoryEntry]] = None,
        *,
        triggers: Optional[Sequence[Any]] = None,
        now: Optional[float] = None,
        apply_ttl: bool = True,
    ) -> List[ForgetCandidate]:
        """执行遗忘巡检：TTL 降级 + 触发①②（触发③走显式 ``erase_subject``）"""
        ts = self.now() if now is None else float(now)
        wanted = {str(getattr(t, "value", t)) for t in triggers} if triggers else None
        pool = list(entries) if entries is not None else await self.store.all_entries()
        candidates: List[ForgetCandidate] = []
        seen: set = set()

        if apply_ttl and (wanted is None or ForgetTrigger.TTL_EXPIRED.value in wanted):
            for ttl_candidate in await self.apply_ttl(pool, now=ts):
                if ttl_candidate.memory_id not in seen:
                    seen.add(ttl_candidate.memory_id)
                    candidates.append(ttl_candidate)

        # 触发①/② 只对带来源指针的条目有意义（偏好记忆无来源能力 → 跳过）
        sampled: Dict[str, Optional[SuccessRateSample]] = {}
        statuses: Dict[str, SourceStatus] = {}
        for entry in pool:
            capability_id = str(entry.source_capability_id or "").strip()
            if not capability_id:
                continue
            if entry.id in seen:
                continue
            if wanted is None or ForgetTrigger.SUCCESS_RATE.value in wanted:
                if capability_id not in sampled:
                    sampled[capability_id] = self.quality_source.success_rate(
                        capability_id, window_days=self.window_days, now=ts)
                rate_candidate = self.evaluate_success_rate(
                    entry, now=ts, sample=sampled.get(capability_id))
                if rate_candidate is not None:
                    seen.add(entry.id)
                    candidates.append(rate_candidate)
                    continue
            if wanted is None or ForgetTrigger.SOURCE_INVALIDATED.value in wanted:
                if capability_id not in statuses:
                    statuses[capability_id] = self.source_checker.check(capability_id)
                source_candidate = self.evaluate_source(
                    entry, status=statuses.get(capability_id))
                if source_candidate is not None:
                    seen.add(entry.id)
                    candidates.append(source_candidate)
        return candidates

    # ── 标记候选 ──

    async def mark_candidates(
        self, candidates: Sequence[ForgetCandidate], *, persist: bool = True
    ) -> List[str]:
        """把候选写入 ``forget_candidate`` 标记（§3.11；不删除）"""
        marked: List[str] = []
        for candidate in candidates:
            try:
                await self.store.mark_forget_candidate(
                    candidate.entry, candidate.reason, persist=persist)
                marked.append(candidate.entry.id)
            except Exception as exc:  # noqa: BLE001 单条失败不阻断批次
                logger.warning(log_dict({
                    "module_name": "memory.forgetting",
                    "action": "candidate.mark.failed",
                    "msg": "[forgetting] 候选标记失败 id=%s: %s"
                           % (candidate.entry.id, exc),
                }))
        return marked

    # ── 执行遗忘（先快照、后删除）──

    async def forget(
        self,
        candidates: Sequence[ForgetCandidate],
        *,
        trigger: Any = None,
        reason: str = "",
        mode: str = "full",
        dry_run: bool = False,
        now: Optional[float] = None,
    ) -> ForgetBatchResult:
        """执行遗忘：**先快照 → 再物理删除**（hard constraint 4）

        Args:
            mode: ``full``（①/② 类可回滚快照）或 ``hash_only``（③ 类墓碑快照）
        """
        cands = [c for c in candidates or [] if c is not None]
        trigger_value = str(
            getattr(trigger, "value", trigger)
            or (cands[0].trigger.value if cands else "manual"))
        result = ForgetBatchResult(
            trigger=trigger_value, reason=reason, snapshot_mode=mode, dry_run=bool(dry_run))
        if not cands:
            return result
        entries = [c.entry for c in cands]
        marked = await self.mark_candidates(cands, persist=not dry_run)
        result.marked_ids = [m for m in marked]
        if dry_run:
            return result

        # ① 先快照
        record = self.snapshots.capture(
            entries, trigger=trigger_value, reason=reason, mode=mode, now=now)
        result.snapshot_id = record.snapshot_id

        # ② 后删除（物理删除）
        for entry in entries:
            try:
                ok = await self.store.delete_entry(entry)
            except Exception as exc:  # noqa: BLE001 单条失败记入失败清单
                logger.warning(log_dict({
                    "module_name": "memory.forgetting",
                    "action": "delete.failed",
                    "msg": "[forgetting] 物理删除失败 id=%s: %s" % (entry.id, exc),
                }))
                ok = False
            if ok:
                result.deleted_ids.append(entry.id)
            else:
                result.failed_ids.append(entry.id)

        if self.audit:
            record_memory_audit(
                "memory.forget",
                subject_id="",
                payload={
                    "trigger": trigger_value,
                    "reason": reason,
                    "snapshot_id": record.snapshot_id,
                    "snapshot_mode": mode,
                    "deleted_count": len(result.deleted_ids),
                    "failed_count": len(result.failed_ids),
                },
                pseudonymizer=self.pseudonymizer,
            )
        logger.info(log_dict({
            "module_name": "memory.forgetting",
            "action": "forget.done",
            "msg": "[forgetting] 遗忘完成: trigger=%s snapshot=%s deleted=%d failed=%d"
                   % (trigger_value, record.snapshot_id,
                      len(result.deleted_ids), len(result.failed_ids)),
        }))
        return result

    # ── 触发③：删除权 / 被遗忘权 ──

    async def erase_subject(
        self,
        subject_id: str,
        *,
        reason: str = "deletion_right",
        dry_run: bool = False,
        include_degraded: bool = True,
        now: Optional[float] = None,
    ) -> ErasureResult:
        """被遗忘权执行（§8）：**记忆物理删除 + 审计标识符匿名化 + 链保留**

        步骤（顺序即纪律）:

        1. 跨租户收集该主体的全部记忆（偏好随 subject 携带，事实/策略也记录了 subject_id）；
        2. **先落墓碑快照**（``hash_only``：只留 content_hash 与主体伪引用）；
        3. **物理删除**全部命中的记忆条目；
        4. **销毁伪名盐**（crypto-shredding）⇒ 既有审计伪名不可再关联；
        5. 追加一条链记录（``memory.erase``）作为**留存证据**（删的是记忆不是证据）；
        6. 校验审计链仍可通过 ``verify()``，并扫描链内**无原始标识符残留**。
        """
        sid = str(subject_id or "").strip()
        ref = subject_ref(sid) if sid else ""
        prior_pseudonym = self.pseudonymizer.pseudonym(sid) if sid and self.pseudonymizer.has_salt(sid) else ""
        result = ErasureResult(
            subject_ref=ref,
            pseudonym_before=prior_pseudonym,
            snapshot_mode="hash_only",
            dry_run=bool(dry_run),
        )
        if not sid:
            result.chain_detail = "subject_id 为空：未执行任何删除"
            return result

        pool = await self.store.all_entries()
        targets = [
            e for e in pool
            if str(e.subject_id or "") == sid
            or (str(e.subject_id or "") == ref)  # 兼容已墓碑化条目
        ]
        if not dry_run and targets:
            record = self.snapshots.capture(
                targets, trigger=ForgetTrigger.DELETION_RIGHT.value,
                reason=reason, mode="hash_only", now=now)
            result.snapshot_id = record.snapshot_id
            for entry in targets:
                try:
                    if await self.store.delete_entry(entry):
                        result.deleted_ids.append(entry.id)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(log_dict({
                        "module_name": "memory.forgetting",
                        "action": "erase.delete.failed",
                        "msg": "[forgetting] 删除权删除失败 id=%s: %s" % (entry.id, exc),
                    }))
        elif dry_run:
            result.deleted_ids = [e.id for e in targets]

        if not dry_run:
            # ④ 销毁盐 ⇒ 既有伪名不可再关联（匿名化不可逆，不再签发新盐）
            result.salts_shredded = bool(self.pseudonymizer.shred(sid))
            result.pseudonym_after = self.pseudonymizer.pseudonym(sid, create=False)
            # ④b 既有 full 快照（①/② 类遗留）中的该主体条目一并墓碑化，
            #     否则 30 天快照会抵消"物理删除"
            try:
                result.snapshots_redacted = self.snapshots.redact_subject(sid)
            except Exception as exc:  # noqa: BLE001 快照墓碑化失败不阻断删除
                logger.warning(log_dict({
                    "module_name": "memory.forgetting",
                    "action": "snapshot.redact.failed",
                    "msg": "[forgetting] 快照墓碑化失败: %s" % exc,
                }))
            # ⑤ 追加链记录（证据保留）
            if self.audit:
                entry_audit = record_memory_audit(
                    "memory.erase",
                    subject_id=sid,
                    payload={
                        "reason": reason,
                        "snapshot_id": result.snapshot_id,
                        "snapshot_mode": "hash_only",
                        "deleted_count": result.deleted_count,
                        "salts_shredded": result.salts_shredded,
                        "snapshots_redacted": result.snapshots_redacted,
                    },
                    pseudonymizer=self.pseudonymizer,
                )
                result.audit_recorded = entry_audit is not None
            # ⑥ 链校验 + 原始标识符残留扫描
            self._verify_chain(result, identifiers=[sid], prior_pseudonym=prior_pseudonym)
        return result

    def _verify_chain(
        self, result: ErasureResult, *, identifiers: Sequence[str], prior_pseudonym: str = ""
    ) -> None:
        """校验审计链完整、无原始标识符残留，且旧伪名仍可证"证据未删"（删除权取证）"""
        try:
            from agent.audit import get_audit

            facade = get_audit()
            verification = facade.verify()
            checked = int(getattr(verification, "checked", 0) or 0)
            # 审计未启用时 verify() 返回 ok=True/checked=0；必须如实标注为"未验签"
            result.chain_verified = bool(getattr(verification, "ok", False)) and checked > 0
            result.chain_checked = checked
            result.chain_detail = str(getattr(verification, "summary", lambda: "")() or "")
            try:
                entries = facade.recent(limit=5000)
            except Exception:  # noqa: BLE001
                entries = []
            needles = [x for x in identifiers if str(x or "").strip()]
            # 只扫"原始标识符"：伪名残留是**预期且必要**的（证明链条未被改写）
            result.residual_identifier_hits = scan_audit_for_identifiers(entries, needles)
            if prior_pseudonym:
                texts = iter_audit_texts(entries)
                result.chain_pseudonym_present = any(
                    prior_pseudonym in text for text in texts)
        except Exception as exc:  # noqa: BLE001 取证失败不阻断删除
            result.chain_verified = False
            result.chain_detail = "审计链校验失败: %s: %s" % (type(exc).__name__, exc)

    # ── 一键巡检 ──

    async def run(
        self,
        *,
        entries: Optional[Sequence[MemoryEntry]] = None,
        triggers: Optional[Sequence[Any]] = None,
        execute: bool = False,
        now: Optional[float] = None,
        prune_snapshots: bool = True,
    ) -> ForgettingReport:
        """巡检（并按需执行）：扫描 → 标记 → 分组遗忘 → 清理到期快照

        Args:
            execute: **默认 False（dry-run）** —— 遗忘为破坏性动作，须显式开启
        """
        ts = self.now() if now is None else float(now)
        pool = list(entries) if entries is not None else await self.store.all_entries()
        report = ForgettingReport(scanned=len(pool), dry_run=not execute)
        report.candidates = await self.scan(pool, triggers=triggers, now=ts)
        if not report.candidates:
            # dry-run 不删任何东西（快照清理亦是删除动作）
            if prune_snapshots and execute:
                report.pruned_snapshots = self.snapshots.prune(now=ts)
            return report

        # 标记候选（TTL 候选已在 apply_ttl 中标记）
        to_mark = [c for c in report.candidates if c.trigger is not ForgetTrigger.TTL_EXPIRED]
        report.marked = await self.mark_candidates(to_mark, persist=execute)

        grouped: Dict[str, List[ForgetCandidate]] = {}
        for candidate in report.candidates:
            grouped.setdefault(candidate.trigger.value, []).append(candidate)
        for trigger_value, group in grouped.items():
            # 删除权类（若被显式纳入巡检）走墓碑快照
            mode = "hash_only" if trigger_value == ForgetTrigger.DELETION_RIGHT.value else "full"
            report.batches.append(await self.forget(
                group, trigger=trigger_value, reason="forgetting_scan",
                mode=mode, dry_run=not execute, now=ts))
        if prune_snapshots and execute:
            report.pruned_snapshots = self.snapshots.prune(now=ts)
        return report
