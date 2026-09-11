"""主体标识匿名化与审计桥（v7.2 §8/§10 被遗忘权：删的是记忆不是证据）

问题
----
§3.5 的链式审计把标识符写进了哈希公式::

    self_hash = sha256(seq + ts + actor + action + subject + payload_hash + prev_hash)

因此**事后改写审计里的标识符必然破坏链**（重算 self_hash 与存储值不符）。
"审计标识符匿名化" 只能靠**写入时就伪名化 + 事后销毁密钥**（crypto-shredding）实现：

1. 每个 subject_id 对应一把**随机盐**；审计只落 ``HMAC(盐, subject_id)`` 的伪名；
2. 擦除时销毁该盐 ⇒ 既有伪名**不可再与任何自然人关联**，而链条一个字节都没动；
3. 擦除动作本身以**新的一条链记录**留痕（``memory.forget`` / ``subject.anonymize``）——
   删的是记忆，证据（链）保留。

盐表以 ``sha256(subject_id)`` 的短摘要为键（盐表本身不落原始标识符）；键表被删后，
伪名不可由 subject_id 重算（盐为随机值，非派生物）。

本模块无状态计算 + 一个小文件存储，被 ``layered_store``（写入审计）与
``forgetting``（擦除）共用；不依赖两者，避免环形导入。
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

__all__ = [
    "ERASED_MARKER",
    "PSEUDONYM_PREFIX",
    "subject_ref",
    "SubjectPseudonymizer",
    "record_memory_audit",
    "iter_audit_texts",
    "scan_audit_for_identifiers",
]

#: 盐被销毁后使用的常量伪名（不可与任何自然人关联，且不泄漏"是谁"）
ERASED_MARKER = "anon-erased"

#: 伪名前缀
PSEUDONYM_PREFIX = "anon-"

#: 盐表文件名
SALTS_FILENAME = "subject_salts.json"


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def subject_ref(subject_id: str) -> str:
    """主体引用（盐表索引键）：``subj-<sha256 前 16 位>``

    盐表因此**不含原始标识符**；该引用本身无法在无原始标识符的情况下被反查
    （短摘要 + 随机盐双重保护）。
    """
    return "subj-" + _sha256_hex(str(subject_id or ""))[:16]


@dataclass
class SubjectPseudonymizer:
    """主体伪名化器（写入即伪名 + 擦除即销毁盐）

    Attributes:
        root: 盐表存放目录（默认取环境变量 ``MEMORY_IDENTITY_ROOT``，否则 ``./data/memory/identity``）
    """

    root: str = ""
    _salts: Dict[str, str] = field(default_factory=dict, repr=False)
    _shredded: set = field(default_factory=set, repr=False)
    _loaded: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if not self.root:
            self.root = os.environ.get("MEMORY_IDENTITY_ROOT") or os.path.join(
                ".", "data", "memory", "identity")

    # ── 路径 ──

    @property
    def salts_path(self) -> str:
        return os.path.join(self.root, SALTS_FILENAME)

    # ── 读写盐表 ──

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        path = self.salts_path
        if not os.path.exists(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh) or {}
            self._salts = {str(k): str(v) for k, v in (raw.get("salts") or {}).items()}
            self._shredded = {str(k) for k in (raw.get("shredded") or [])}
        except Exception as exc:  # noqa: BLE001 盐表损坏 → 空表（伪名化退化为 ERASED_MARKER）
            logger.warning(log_dict({
                "module_name": "memory.identity",
                "action": "salts.load.failed",
                "msg": "[identity] 盐表读取失败（按空表处理）: %s" % exc,
            }))
            self._salts = {}
            self._shredded = set()

    def _flush(self) -> None:
        os.makedirs(self.root, exist_ok=True)
        payload = {"salts": self._salts, "shredded": sorted(self._shredded), "version": 1}
        # 原子写：临时文件 + 替换（避免半写状态污染盐表）
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".salts-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, sort_keys=True)
            os.replace(tmp, self.salts_path)
        except Exception:  # noqa: BLE001 清理临时文件后上抛由调用方兜底
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── 伪名化 ──

    def salt_for(self, subject_id: str, *, create: bool = True) -> Optional[str]:
        """取该主体的盐（``create=True`` 时按需生成并持久化）

        一旦该主体被擦除（盐已销毁），**不再签发新盐** —— 匿名化不可逆，
        避免"擦除后又生成新盐"把被遗忘权变成可逆操作。
        """
        ref = subject_ref(subject_id)
        if not ref or ref == "subj-":
            return None
        self._ensure_loaded()
        salt = self._salts.get(ref)
        if salt:
            return salt
        if ref in self._shredded:
            return None  # 已匿名化：不再签发
        if not create:
            return None
        salt = secrets.token_hex(16)
        self._salts[ref] = salt
        self._flush()
        return salt

    def pseudonym(self, subject_id: str, *, create: bool = True) -> str:
        """伪名：``anon-<HMAC(盐, subject_id) 前 16 位>``

        盐已销毁（或不可创建）时返回常量 :data:`ERASED_MARKER`——此后写入的审计
        记录与前擦除记录**不可互相关联**（这正是匿名化要达到的效果）。
        """
        if not str(subject_id or "").strip():
            return ""
        salt = self.salt_for(subject_id, create=create)
        if not salt:
            return ERASED_MARKER
        digest = hmac.new(
            salt.encode("utf-8"), str(subject_id).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return PSEUDONYM_PREFIX + digest[:16]

    def has_salt(self, subject_id: str) -> bool:
        """该主体是否仍有可关联的盐（未匿名化）"""
        return self.salt_for(subject_id, create=False) is not None

    def is_shredded(self, subject_id: str) -> bool:
        """该主体是否已被匿名化（盐已销毁）"""
        self._ensure_loaded()
        return subject_ref(subject_id) in self._shredded

    # ── 匿名化（擦除）──

    def shred(self, subject_id: str) -> bool:
        """销毁该主体的盐 ⇒ 既有伪名不可再关联（返回是否确有盐被销毁）

        **不触碰审计链**：链上记录的伪名仍在，但已无任何途径可还原为自然人。
        """
        self._ensure_loaded()
        ref = subject_ref(subject_id)
        had = ref in self._salts
        self._salts.pop(ref, None)
        self._shredded.add(ref)
        self._flush()
        logger.info(log_dict({
            "module_name": "memory.identity",
            "action": "subject.shred",
            "msg": "[identity] 主体盐已销毁（匿名化）: ref=%s, had_salt=%s" % (ref, had),
        }))
        return had

    def shred_many(self, subject_ids: Iterable[str]) -> int:
        """批量销毁盐，返回确有盐被销毁的主体数"""
        return sum(1 for sid in subject_ids if self.shred(sid))

    def stats(self) -> Dict[str, Any]:
        """盐表统计（不含任何原始标识符）"""
        self._ensure_loaded()
        return {
            "salt_count": len(self._salts),
            "shredded_count": len(self._shredded),
            "path": self.salts_path,
        }


# ════════════════════════════════════════════════════════════
#  审计桥（best-effort，绝不阻断主路径）
# ════════════════════════════════════════════════════════════


def record_memory_audit(
    action: str,
    *,
    memory_id: str = "",
    subject_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
    pseudonymizer: Optional[SubjectPseudonymizer] = None,
    actor: str = "",
    extra: Optional[Dict[str, Any]] = None,
    strict: bool = False,
) -> Any:
    """把一条记忆事件写入链式审计（标识符一律伪名化）

    Args:
        action: 动作名（``memory.write`` / ``memory.forget`` / ``memory.erase`` …）
        memory_id: 受影响记忆条目 id（落 ``subject`` 字段，是"资源"不是"人"）
        subject_id: 主体标识；**只以伪名形式入链**（§8：审计标识符匿名化）
        payload: 载荷叶子字段（自动脱敏；原文不入链）
        pseudonymizer: 伪名化器；None 时用进程默认（``MEMORY_IDENTITY_ROOT``）
        actor: 操作者；缺省用主体伪名
        strict: True 时审计失败上抛（默认 best-effort 不阻断主路径）

    Returns:
        ``AuditEntry`` 或 None（审计关闭/失败）
    """
    pz = pseudonymizer or SubjectPseudonymizer()
    try:
        from agent.audit import record as _record

        pseudo = pz.pseudonym(subject_id) if subject_id else ""
        body: Dict[str, Any] = {
            "memory_id": str(memory_id or ""),
            "subject_pseudonym": pseudo,
        }
        if payload:
            body.update(payload)
        return _record(
            str(action),
            actor or (pseudo or "system"),
            "memory:%s" % memory_id if memory_id else "memory",
            body,
            extra=dict(extra or {}) or None,
        )
    except Exception as exc:  # noqa: BLE001 审计 best-effort
        logger.warning(log_dict({
            "module_name": "memory.identity",
            "action": "audit.failed",
            "msg": "[identity] 记忆审计写入失败（不阻断主路径）: %s: %s"
                   % (type(exc).__name__, exc),
        }))
        if strict:
            raise
        return None


# ════════════════════════════════════════════════════════════
#  审计扫描（"删除后无原始标识符残留"的取证工具）
# ════════════════════════════════════════════════════════════


def iter_audit_texts(entries: Sequence[Any]) -> List[str]:
    """把审计条目压成可搜文本（只读叶子字段，不序列化 live 对象）"""
    texts: List[str] = []
    for entry in entries or []:
        try:
            texts.append(str(getattr(entry, "actor", "") or ""))
            texts.append(str(getattr(entry, "subject", "") or ""))
            payload = getattr(entry, "payload", None)
            if payload is not None:
                texts.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        except Exception:  # noqa: BLE001 live 对象不可读 → 跳过该条
            continue
    return texts


def scan_audit_for_identifiers(
    entries: Sequence[Any],
    identifiers: Sequence[str],
) -> List[Tuple[str, str]]:
    """扫描审计文本中是否残留原始标识符

    Returns:
        ``[(identifier, 命中文本片段), ...]``；空列表 = 无残留（匿名化达成）

    Note:
        这是**取证**用途：单测用它断言"擦除后审计链内不含原始 subject_id"。
    """
    needles = [str(x) for x in (identifiers or []) if str(x or "").strip()]
    if not needles:
        return []
    hits: List[Tuple[str, str]] = []
    for text in iter_audit_texts(entries):
        for needle in needles:
            if needle in text:
                idx = text.find(needle)
                hits.append((needle, text[max(0, idx - 20): idx + len(needle) + 20]))
    return hits
