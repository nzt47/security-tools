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
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional

from agent.policy.models import (
    PolicyContext,
    PolicyDecision,
    canonical_json,
    now_iso,
)

logger = logging.getLogger("agent.policy.decisions")

#: 环境变量：决策日志路径
ENV_DECISION_LOG = "CP_POLICY_DECISION_LOG"
#: 环境变量：是否写入决策日志（"0" 关闭）
ENV_DECISION_LOG_ENABLED = "CP_POLICY_DECISION_LOG_ENABLED"

#: 默认决策日志（运行时产物）
DEFAULT_DECISION_LOG = "data/policies/decisions.jsonl"

#: 单行上限（与事件层同量级；超限行直接丢弃，避免被单条脏数据拖垮读取）
MAX_LINE_BYTES = 1 << 20


class DecisionLogError(Exception):
    """决策日志错误（仅在显式 strict 模式下抛出）"""


def _env_flag(name: str, default: str = "1") -> bool:
    return str(os.environ.get(name, default)).strip().lower() in ("1", "true", "yes", "on")


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
        replay_drift: 重放时置位的漂移标记（原记录中恒为 False）。
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
    """追加式决策日志（线程安全；best-effort）

    Args:
        path: 落盘路径；``None`` ⇒ ``CP_POLICY_DECISION_LOG`` 或默认路径。
        enabled: 是否写入；``None`` ⇒ ``CP_POLICY_DECISION_LOG_ENABLED``（默认开）。
        strict: 写失败时是否抛异常（默认 False：绝不阻断决策）。
        min_free_bytes: 低于该磁盘余量时自动停写（防止把盘写满）。
    """

    def __init__(
        self,
        path: Optional[str] = None,
        *,
        enabled: Optional[bool] = None,
        strict: bool = False,
        min_free_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self._path = str(path if path is not None
                         else os.environ.get(ENV_DECISION_LOG) or DEFAULT_DECISION_LOG)
        self._enabled = _env_flag(ENV_DECISION_LOG_ENABLED, "1") if enabled is None \
            else bool(enabled)
        self._strict = bool(strict)
        self._min_free_bytes = int(min_free_bytes)
        self._lock = threading.RLock()
        self._handle: Optional[Any] = None
        self._closed = False
        self._write_count = 0
        self._failure_count = 0
        self._last_error = ""
        self._skipped_low_disk = 0

    # ── 属性 ──

    @property
    def path(self) -> str:
        return self._path

    @property
    def enabled(self) -> bool:
        return self._enabled and not self._closed

    @property
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            return {"path": self._path, "enabled": self.enabled,
                    "write_count": self._write_count,
                    "failure_count": self._failure_count,
                    "last_error": self._last_error,
                    "skipped_low_disk": self._skipped_low_disk}

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
                handle = self._ensure_handle()
                if handle is None:
                    return False
                handle.write(line + "\n")
                handle.flush()
                self._write_count += 1
            return True
        except Exception as exc:  # noqa: BLE001 best-effort
            self._note_failure(f"{type(exc).__name__}: {exc}")
            return False

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

    def _ensure_handle(self) -> Optional[Any]:
        if self._handle is not None:
            return self._handle
        try:
            directory = os.path.dirname(self._path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            self._handle = open(self._path, "a", encoding="utf-8")
            return self._handle
        except OSError as exc:
            self._note_failure(f"无法打开决策日志: {exc}")
            return None

    def _note_failure(self, detail: str) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_error = detail
        logger.warning("决策日志写入失败（不影响决策）: %s", detail)
        if self._strict:
            raise DecisionLogError(detail)

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
        """读回决策记录（时间序）

        Args:
            since/until: ISO 前缀比较（与事件层 ``iter_events`` 同口径）。
            since_days: 相对「现在」的滚动窗口（天，可为小数）——P7.2-19 的 ``--since 7d``。
            limit: 只保留**最近** limit 条。
            path: 覆盖读取路径（不启动 writer 也能读）。
            dedupe: 按 ``(ts, input, policy_id, effect)`` 去重（重复 append 不重复计数）。
        """
        records = list(self.iter_records(since=since, until=until,
                                         since_days=since_days, path=path,
                                         dedupe=dedupe))
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
            except OSError:
                continue

    @staticmethod
    def _candidate_files(target: str) -> List[str]:
        """活动文件 + 同目录轮转分片（``*.jsonl``）"""
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
                    if stem and name.startswith(stem + "."):
                        files.append(os.path.join(directory, name))
            except OSError:
                pass
        return files

    # ── 生命周期 ──

    def flush(self) -> bool:
        with self._lock:
            if self._handle is None:
                return True
            try:
                self._handle.flush()
                return True
            except Exception as exc:  # noqa: BLE001
                self._note_failure(f"{type(exc).__name__}: {exc}")
                return False

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._handle is not None:
                try:
                    self._handle.flush()
                    self._handle.close()
                except Exception:  # noqa: BLE001
                    pass
                self._handle = None

    def __enter__(self) -> "DecisionLog":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


__all__ = [
    "ENV_DECISION_LOG", "ENV_DECISION_LOG_ENABLED", "DEFAULT_DECISION_LOG",
    "MAX_LINE_BYTES", "DecisionLogError", "DecisionRecord", "DecisionLog",
]
