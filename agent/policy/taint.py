"""SecretTaint — 出域链路监测的「读密钥」半边（§5.7 机制 4）

【机制 4 原文】
    「出域链路监测：检测'读本地密钥→外发 HTTP'链路，命中即熔断+事故卡。」

    这条链路有两个端点，缺一不可：
        - **读端点**：本地敏感文件被读进上下文（``mark_secret_read``）；
        - **写端点**：出站 HTTP 把凭据类内容送出去（``egress.decide_egress``）。
    本模块提供两个端点的共用判定：**什么算敏感文件**、**什么算凭据内容**、
    以及「当前作用域是否已被标记」。策略引擎本身不碰这两件事（它只判定），
    判定输入由 ``egress.py`` 组装。

【两条独立的证据线（都指向 data_class=secret）】
    1. **内容证据**：出站载荷里**当下就有**凭据形态的值（``scan_secret_material``）
       ——不依赖任何历史状态，单次请求即可判定。
    2. **链路证据**：同一作用域内**先读过**敏感文件（taint 台账），随后外发
       ——这是「读密钥→外发」这条**跨调用链路**的形式化。

    两条线任一命中即把 ``capability.trust.data_class`` 置为 ``secret``，命中
    §2.5/§3.2 的契约级不变量（secret 且目标外部 ⇒ deny）。

【脱敏纪律（沿用既有口径）】
    ``scan_secret_material`` **只返回类别与计数，绝不返回匹配到的值**；
    事件/审计里只写 ``kinds`` 与 ``count``。台账在内存里保留来源路径（追责需要），
    但写入审计时只写文件名（``basename``）——路径本身可能包含凭据材料的目录习惯
    （如 ``~/.ssh/id_rsa``）。

【为什么要有 TTL】
    无 TTL 的进程级污点在长驻服务里会变成「一次读密钥 ⇒ 之后所有外发都被拒」，
    那是把安全机制做成了可用性事故。默认 15 分钟窗口足够覆盖「读→外发」的实际
    调用跨度，又不会永久粘住。
"""

from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from agent.policy.models import now_iso

#: 环境变量：是否启用污点台账（"0" 关闭）
ENV_TAINT_ENABLED = "CP_POLICY_TAINT_ENABLED"
#: 环境变量：污点有效期（秒）
ENV_TAINT_TTL = "CP_POLICY_TAINT_TTL_SECONDS"
#: 环境变量：是否并入既有 PII 检测器的 CRITICAL 命中（"1" 开启；默认 0，见 _deep_scan）
ENV_TAINT_DEEP_SCAN = "CP_POLICY_TAINT_DEEP_SCAN"

#: 默认 TTL（秒）——覆盖「读密钥 → 外发」的实际调用跨度
DEFAULT_TTL_SECONDS = 900

#: 无 trace/subject 上下文时使用的作用域键
PROCESS_SCOPE = "__process__"

# ── 敏感文件判定（**文件名**级，窄口径） ──
#:
#: 只认「几乎必然是凭据载体」的名字/后缀，不做宽泛的 ``*secret*`` 匹配：
#: 宽口径会把 ``docs/secret_rotation_plan.md`` 也算进来，进而让一次普通的
#: 文档阅读污染整条链路（误报的代价是外发被拦，比漏报更打断业务）。
SECRET_FILE_PATTERNS: Tuple[str, ...] = (
    ".env", ".env.local", ".env.production", ".env.development",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519",
    "credentials", "credentials.json", ".credentials",
    ".netrc", ".pgpass", ".htpasswd",
    "secrets.json", "secrets.yaml", "secrets.yml", "secret.json",
    "api_keys.json", "api_key.txt", "apikeys.json",
    "service_account.json", "service-account.json",
    "keystore.jks", ".keystore",
    "network_config.json",       # 云枢既有：含代理/上游凭据
    "role_assignments.json",     # 云枢既有：权限指派
)
SECRET_FILE_SUFFIXES: Tuple[str, ...] = (
    ".pem", ".key", ".pfx", ".p12", ".jks", ".keystore", ".ppk",
)

# ── 凭据内容判定（**值**级；只出类别，不出值） ──
#: (类别, 正则)。正则只用于**判定**，匹配文本永不外传。
SECRET_VALUE_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("openssh_private_key", re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b")),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[abopr]-[A-Za-z0-9\-]{10,}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("bearer_header", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}")),
    ("basic_auth_header", re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{20,}")),
    # ``api_key = "<长值>"`` 一类赋值：要求值 ≥16 位且不含空白，避免误伤文档示例
    ("assigned_credential",
     re.compile(r"(?i)\b(?:api[_\-]?key|secret[_\-]?key|access[_\-]?token|"
                r"client[_\-]?secret|private[_\-]?key|password|passwd|auth[_\-]?token)"
                r"\s*[:=]\s*[\"']?([A-Za-z0-9/+_\-\.]{16,})")),
)

#: 扫描时最多处理的文本长度（防止把上百 MB 响应全量正则；足够覆盖请求体）
MAX_SCAN_CHARS = 256 * 1024


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _ttl_seconds() -> int:
    raw = str(os.environ.get(ENV_TAINT_TTL, "")).strip()
    if not raw:
        return DEFAULT_TTL_SECONDS
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        return DEFAULT_TTL_SECONDS  # 非法值回退默认（通用硬约束 3）
    return value if value > 0 else DEFAULT_TTL_SECONDS


# ════════════════════════════════════════════════════════════
#  判定基元
# ════════════════════════════════════════════════════════════


def is_secret_path(path: Any) -> bool:
    """路径是否属于「几乎必然是凭据载体」（窄口径，见 ``SECRET_FILE_PATTERNS``）"""
    text = str(path or "").strip()
    if not text:
        return False
    name = os.path.basename(text.replace("\\", "/")).lower()
    if not name:
        return False
    if name in SECRET_FILE_PATTERNS:
        return True
    if name.startswith(".env"):
        return True
    return any(name.endswith(suffix) for suffix in SECRET_FILE_SUFFIXES)


def _deep_scan(text: Any) -> List[str]:
    """可选深扫：复用既有 ``agent/utils/sensitive_data_filter.py``（**默认关闭**）

    该模块是云枢已有的成熟检测器（``SensitiveDataFilter.detect() -> FilterResult``，
    带 ``SensitiveLevel`` 五级与 ``allowed``/``action_taken``），但它的口径**比凭据宽**
    ——还覆盖邮箱、手机号、身份证等 PII。把它并进默认判定，会让「给合作方 API 传一个
    邮箱」变成出域拦截，那是把 §5.7-4 的「密钥外泄」机制误扩成「任何个人信息不得出境」。

    因此裁定：默认只用本模块的**凭据形态**口径；需要更严的部署用
    ``CP_POLICY_TAINT_DEEP_SCAN=1`` 打开深扫，且**只取 CRITICAL 级**（既有检测器里
    明确标注「立即阻止+告警」的那一档），把误报面压到最小。

    Returns:
        类别名清单（形如 ``pii:aws_access_key``）；未开启或既有检测器不可用时为空。
    """
    if not _env_flag(ENV_TAINT_DEEP_SCAN, "0"):
        return []
    blob = str(text or "")
    if not blob:
        return []
    try:
        from agent.utils.sensitive_data_filter import (  # type: ignore[import-not-found]
            SensitiveLevel,
            get_default_filter,
        )
    except Exception:  # noqa: BLE001 既有检测器不可用 ⇒ 退回本模块口径
        return []
    try:
        result = get_default_filter().detect(blob[:MAX_SCAN_CHARS])
    except Exception:  # noqa: BLE001
        return []
    kinds: List[str] = []
    for violation in (getattr(result, "violations", None) or []):
        if getattr(violation, "level", None) == SensitiveLevel.CRITICAL:
            name = str(getattr(violation, "pattern_name", "") or "critical")
            kinds.append("pii:" + name)
    return sorted(set(kinds))


def scan_secret_material(text: Any) -> List[str]:
    """扫描文本中的凭据材料；**只返回类别清单（去重排序），绝不返回值**

    Args:
        text: 待扫描文本（超长自动截断到 ``MAX_SCAN_CHARS``）。

    Returns:
        命中的类别名（如 ``["openai_key", "private_key_block"]``）；空＝未命中。
        开启 ``CP_POLICY_TAINT_DEEP_SCAN=1`` 时并入既有 PII 检测器的 CRITICAL 命中。
    """
    blob = str(text or "")
    if not blob:
        return []
    if len(blob) > MAX_SCAN_CHARS:
        blob = blob[:MAX_SCAN_CHARS]
    kinds: List[str] = []
    for kind, pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(blob):
            kinds.append(kind)
    kinds.extend(_deep_scan(blob))
    return sorted(set(kinds))


def flatten_for_scan(value: Any, *, _depth: int = 0, _limit: int = 4096) -> str:
    """把出站载荷（dict/list/str/bytes）拍平成一段文本供扫描

    只用于**判定**：不返回给调用方做日志，调用方也拿不到原始片段
    （``scan_secret_material`` 只回类别）。
    """
    if _depth > 8:
        return ""
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="ignore")[:_limit]
        except Exception:  # noqa: BLE001
            return ""
    if isinstance(value, str):
        return value[:_limit]
    if isinstance(value, dict):
        parts: List[str] = []
        for key, item in list(value.items())[:256]:
            parts.append(str(key))
            parts.append(flatten_for_scan(item, _depth=_depth + 1, _limit=_limit))
            if sum(len(p) for p in parts) > _limit:
                break
        return "\n".join(parts)[:_limit]
    if isinstance(value, (list, tuple, set)):
        parts = [flatten_for_scan(item, _depth=_depth + 1, _limit=_limit)
                 for item in list(value)[:256]]
        return "\n".join(parts)[:_limit]
    return str(value)[:_limit]


def scan_payload(value: Any) -> List[str]:
    """出站载荷凭据扫描门面（拍平 + 判定；只回类别）"""
    return scan_secret_material(flatten_for_scan(value))


def describe_kinds(kinds: Iterable[str]) -> Dict[str, Any]:
    """类别清单 → 可入审计/事件的**脱敏描述**（只有类别与计数）"""
    unique = sorted(set(str(k) for k in (kinds or ())))
    return {"kinds": unique, "count": len(unique), "values_recorded": False}


# ════════════════════════════════════════════════════════════
#  污点台账
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class TaintMark:
    """一条「读敏感数据」标记

    Attributes:
        mark_id: 标记 id（入审计的关联键）。
        kind: 类别（``file`` / ``env`` / ``explicit``）。
        source_ref: 来源（内存里存全路径；入审计时只出 basename）。
        scope: 作用域键（trace_id / subject_id / ``__process__``）。
        kinds: 命中的凭据类别（**不含值**）。
        at / expires_at: ISO-8601。
    """

    mark_id: str
    kind: str
    source_ref: str
    scope: str
    kinds: Tuple[str, ...] = ()
    at: str = ""
    expires_at: str = ""

    def is_expired(self, ts: Optional[str] = None) -> bool:
        return bool(self.expires_at) and (ts or now_iso()) > self.expires_at

    def audit_leaves(self) -> Dict[str, Any]:
        """入审计的叶子投影（**只有文件名 + 类别，无值、无完整路径**）"""
        return {
            "mark_id": self.mark_id,
            "kind": self.kind,
            "source_name": os.path.basename(str(self.source_ref).replace("\\", "/")),
            "scope": self.scope,
            **describe_kinds(self.kinds),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mark_id": self.mark_id, "kind": self.kind,
            "source_ref": self.source_ref, "scope": self.scope,
            "kinds": list(self.kinds), "at": self.at, "expires_at": self.expires_at,
        }


class SecretTaintLedger:
    """进程内「已读过敏感数据」台账（线程安全，带 TTL）

    **作用域纪律**：查询时按 ``trace_id`` → ``subject_id`` → ``__process__`` 逐级
    回退。这样同一次任务链内的「读→外发」能被串起来，而不同任务之间不会互相污染
    ——除非两边都没有上下文（此时退化为进程级，安全优先）。
    """

    def __init__(self, *, enabled: Optional[bool] = None,
                 ttl_seconds: Optional[int] = None) -> None:
        self._enabled = _env_flag(ENV_TAINT_ENABLED, "1") if enabled is None \
            else bool(enabled)
        self._ttl = int(ttl_seconds) if ttl_seconds else _ttl_seconds()
        self._marks: Dict[str, List[TaintMark]] = {}
        self._lock = threading.RLock()
        self._marked_count = 0
        self._hit_count = 0

    # ── 属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"enabled": self._enabled, "ttl_seconds": self._ttl,
                    "scopes": len(self._marks),
                    "marks": sum(len(v) for v in self._marks.values()),
                    "marked_count": self._marked_count, "hit_count": self._hit_count}

    # ── 写 ──

    def mark(self, *, kind: str = "explicit", source_ref: str = "",
             scope: str = "", kinds: Iterable[str] = ()) -> Optional[TaintMark]:
        """登记一条污点；返回标记（未启用 ⇒ None）"""
        if not self._enabled:
            return None
        key = str(scope or PROCESS_SCOPE)
        from datetime import datetime, timedelta
        at = now_iso()
        expires = (datetime.now().astimezone()
                   + timedelta(seconds=self._ttl)).isoformat(timespec="milliseconds")
        mark = TaintMark(mark_id="taint_" + uuid.uuid4().hex[:12], kind=str(kind),
                         source_ref=str(source_ref), scope=key,
                         kinds=tuple(sorted(set(str(k) for k in (kinds or ())))),
                         at=at, expires_at=expires)
        with self._lock:
            self._marks.setdefault(key, []).append(mark)
            self._marked_count += 1
        return mark

    def clear(self, *, scope: str = "") -> int:
        """清理某作用域（``scope=""`` ⇒ 全清，含进程级）"""
        with self._lock:
            if not scope:
                removed = sum(len(v) for v in self._marks.values())
                self._marks.clear()
                return removed
            return len(self._marks.pop(str(scope), []) or [])

    # ── 读 ──

    def scope_for(self, *, trace_id: str = "", subject_id: str = "") -> str:
        """解析查询作用域（trace → subject → process）"""
        if str(trace_id or "").strip():
            return str(trace_id).strip()
        if str(subject_id or "").strip():
            return str(subject_id).strip()
        return PROCESS_SCOPE

    def marks_for(self, *, trace_id: str = "", subject_id: str = "",
                  include_process: bool = True, prune: bool = True) -> List[TaintMark]:
        """取该作用域（含进程级回退）的有效标记"""
        with self._lock:
            if prune:
                self._prune_locked()
            out: List[TaintMark] = []
            scope = self.scope_for(trace_id=trace_id, subject_id=subject_id)
            for key in ([scope] + ([PROCESS_SCOPE] if include_process
                                   and scope != PROCESS_SCOPE else [])):
                out.extend(self._marks.get(key, []) or [])
            return out

    def is_tainted(self, *, trace_id: str = "", subject_id: str = "",
                   include_process: bool = True) -> bool:
        """当前作用域是否已被标记为「读过敏感数据」"""
        if not self._enabled:
            return False
        found = bool(self.marks_for(trace_id=trace_id, subject_id=subject_id,
                                    include_process=include_process))
        if found:
            with self._lock:
                self._hit_count += 1
        return found

    def taint_kinds(self, *, trace_id: str = "", subject_id: str = "",
                    include_process: bool = True) -> List[str]:
        """当前作用域命中的类别（去重；**无值**）"""
        kinds: List[str] = []
        for mark in self.marks_for(trace_id=trace_id, subject_id=subject_id,
                                   include_process=include_process):
            kinds.extend(mark.kinds)
        return sorted(set(kinds))

    def _prune_locked(self) -> int:
        removed = 0
        for scope in list(self._marks):
            kept = [m for m in self._marks[scope] if not m.is_expired()]
            removed += len(self._marks[scope]) - len(kept)
            if kept:
                self._marks[scope] = kept
            else:
                self._marks.pop(scope, None)
        return removed


# ════════════════════════════════════════════════════════════
#  进程级台账 + 门面
# ════════════════════════════════════════════════════════════

_LEDGER: Optional[SecretTaintLedger] = None
_LEDGER_LOCK = threading.Lock()


def get_secret_taint() -> SecretTaintLedger:
    """进程级污点台账（懒加载）"""
    global _LEDGER
    if _LEDGER is None:
        with _LEDGER_LOCK:
            if _LEDGER is None:
                _LEDGER = SecretTaintLedger()
    return _LEDGER


def reset_secret_taint() -> None:
    """丢弃进程级台账（**用例必须显式隔离**：默认路径会跨用例泄漏污点）"""
    global _LEDGER
    with _LEDGER_LOCK:
        _LEDGER = None


def _current_scope() -> str:
    """从 TraceContext 解析当前作用域（无上下文 ⇒ 进程级）"""
    try:
        from agent.observability.events import trace_fields
        fields = trace_fields()
        return get_secret_taint().scope_for(
            trace_id=str(fields.get("trace_id") or ""),
            subject_id=str(fields.get("subject_id") or ""))
    except Exception:  # noqa: BLE001
        return PROCESS_SCOPE


def mark_secret_read(
    source_ref: str = "",
    *,
    kind: str = "file",
    content: Any = None,
    scope: str = "",
    content_kinds: Optional[Iterable[str]] = None,
) -> Optional[TaintMark]:
    """登记「读到了敏感数据」（**只在真的判定为敏感时登记**）

    判定口径（二者之一）：
      1. ``content_kinds`` 显式给出命中的凭据类别；或
      2. ``content`` 经 ``scan_secret_material`` 命中。

    ``is_secret_path`` 只作为**调用方**的入口条件（工具层用它决定要不要调本函数），
    本函数不再看路径——因为「路径敏感但内容是普通文本」不该污染链路。

    Returns:
        :class:`TaintMark`（登记成功）或 ``None``（未命中/未启用）。
    """
    kinds = sorted(set(str(k) for k in (content_kinds or ())))
    if not kinds and content is not None:
        kinds = scan_secret_material(content)
    if not kinds:
        return None
    ledger = get_secret_taint()
    mark = ledger.mark(kind=kind, source_ref=source_ref,
                       scope=scope or _current_scope(), kinds=kinds)
    if mark is not None:
        _audit_taint(mark)
    return mark


def _audit_taint(mark: TaintMark) -> None:
    """污点登记入审计（best-effort；**只写文件名 + 类别**）"""
    try:
        from agent.audit.facade import audit
        audit.record("policy.taint.marked", actor="policy.taint",
                     subject=f"taint:{mark.mark_id}",
                     payload=mark.audit_leaves(), source="agent")
    except Exception:  # noqa: BLE001
        pass


def observe_file_read(source_ref: str, content: Any, *,
                      scope: str = "") -> Optional[TaintMark]:
    """**执行点辅助**：文件读取后的污点登记（§5.7 机制 4 的「读端点」）

    封装工具层需要的三步，让 ``agent/tools/file_tools_reg.py::_read_file`` 的改动
    只有一行：

        1. 路径是否属敏感载体（``is_secret_path``）——**窄口径闸门**，普通文档
           阅读不登记污点（误报的代价是后续外发被拦）；
        2. 内容里是否真的出现凭据材料（``scan_secret_material``）——路径敏感但
           内容是普通文本时不登记；
        3. 两者都成立才 ``mark_secret_read``。

    Returns:
        :class:`TaintMark` 或 ``None``（未命中/未启用）。**永远不抛异常**，
        调用方不需要 try。
    """
    try:
        if not is_secret_path(source_ref):
            return None
        kinds = scan_secret_material(content if content is not None
                                     else flatten_for_scan(content))
        if not kinds:
            return None
        return mark_secret_read(source_ref, kind="file", content_kinds=kinds,
                                scope=scope)
    except Exception:  # noqa: BLE001 监测失败绝不影响读取
        return None


def taint_state(*, trace_id: str = "", subject_id: str = "") -> Dict[str, Any]:
    """当前污点状态的**脱敏摘要**（可入报告/审计；无路径无值）"""
    ledger = get_secret_taint()
    marks = ledger.marks_for(trace_id=trace_id, subject_id=subject_id)
    return {
        "enabled": ledger.enabled,
        "tainted": bool(marks),
        "kinds": ledger.taint_kinds(trace_id=trace_id, subject_id=subject_id),
        "mark_count": len(marks),
    }


__all__ = [
    "ENV_TAINT_ENABLED", "ENV_TAINT_TTL", "ENV_TAINT_DEEP_SCAN",
    "DEFAULT_TTL_SECONDS", "PROCESS_SCOPE",
    "SECRET_FILE_PATTERNS", "SECRET_FILE_SUFFIXES", "SECRET_VALUE_PATTERNS",
    "MAX_SCAN_CHARS",
    "TaintMark", "SecretTaintLedger",
    "is_secret_path", "scan_secret_material", "scan_payload", "flatten_for_scan",
    "describe_kinds", "get_secret_taint", "reset_secret_taint",
    "mark_secret_read", "observe_file_read", "taint_state",
]
