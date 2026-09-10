"""结构化审计日志——Append-only 追加写入（**双写过渡版，S2-02**）

每条审计记录包含：时间戳、Trace_ID、操作类型、输入输出哈希、调用栈深度

【S2-02 变更（additive，既有语义逐字保留）】
    1. 旧轨（本模块的按日 JSONL）**格式与写入路径不变**，仅新增一个关联键
       `audit_ref`（用于 `LegacyTrack.verify_consistency()` 对齐新旧两轨）；
    2. 新增链式轨：同一条审计同时写入 `agent/audit/chain.py` 的防篡改链
       （`source="agent"`，含 actor/action/subject/prev_hash/self_hash）；
    3. 双写由 `AUDIT_DUAL_WRITE`（默认 1）控制；置 0 即**回滚为仅旧轨**，
       行为与 S2-02 之前逐字一致（`AUDIT_LEGACY_WRITE` 控制旧轨本身是否停写）；
    4. 链式写入 best-effort：链不可用时自动退化为直接写旧 JSONL，审计不丢。

【S2-02 盘点结论（诚实口径）】
    本模块的 `log()` 在仓库内**没有生产写入调用方**（唯一引用是 plugins/admin.py
    的只读查询）；因此把「链式轨」接在这里的收益是「兼容升级」，真正产生审计面的
    是 skills_mgmt 审批/评审、descriptors 审计、review_gate 与 UI 写路由
    （见 `agent/audit/facade.py` 与 `agent/audit/ui_middleware.py` 的接线）。
"""
import hashlib
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.observability.tracer import get_trace_id
from agent.logging_utils import log_dict
from agent.audit.chain import AuditEntry

logger = logging.getLogger(__name__)

_ENV_DUAL_WRITE = "AUDIT_DUAL_WRITE"


def _env_flag(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in ("0", "false", "no", "off")


class AuditLogger:
    """审计日志记录器——Append-only（旧 JSONL 轨 + 新链式轨双写过渡）

    Args:
        log_dir: 旧轨目录（默认 `./data/audit/`），按日分片 `audit_YYYYMMDD.jsonl`。
        dual_write: 是否同时写链式轨（None → 环境变量 AUDIT_DUAL_WRITE，默认 True）。
        chain_db_path: 链式台账路径（默认 `<log_dir>/audit_chain.db`）。
        roots_path: 每日 Merkle 根路径（默认 `<log_dir>/daily_roots.jsonl`）。
        actor: 缺省操作者（调用方可用 `actor=` 覆盖）。
    """

    def __init__(self, log_dir: str = "./data/audit/", *, dual_write: Optional[bool] = None,
                 chain_db_path: Optional[str] = None, roots_path: Optional[str] = None,
                 actor: str = "system", flush_on_write: bool = True):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = self._log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
        self._dual_write = _env_flag(_ENV_DUAL_WRITE) if dual_write is None else bool(dual_write)
        self._chain_db_path = chain_db_path or str(self._log_dir / "audit_chain.db")
        self._roots_path = roots_path or str(self._log_dir / "daily_roots.jsonl")
        self._actor = str(actor or "system")
        #: 双写后等待链式轨落盘（默认 True：写入即耐久；避免后台 writer 短暂占用台账
        #: 文件导致外部「删除/替换台账」在 Windows 上失败）
        self._flush_on_write = bool(flush_on_write)
        self._facade: Any = None
        self._track: Any = None

    # ── 链式轨（懒加载，构造期零副作用） ────────────────────

    @property
    def facade(self) -> Any:
        """链式审计门面（懒加载：首次写链时才创建台账文件与 writer 线程）"""
        if self._facade is None:
            from agent.audit.facade import AuditFacade
            self._facade = AuditFacade(
                db_path=self._chain_db_path, roots_path=self._roots_path,
                enabled=self._dual_write)
        return self._facade

    @property
    def track(self) -> Any:
        """双写过渡轨（旧 JSONL + 新链）；跨日自动跟随新的分片文件"""
        expected = os.path.abspath(str(self._current_file))
        if self._track is None or os.path.abspath(str(self._track.path)) != expected:
            from agent.audit.migration import LegacyTrack
            self._track = LegacyTrack(str(self._current_file), facade=self.facade)
        return self._track

    @property
    def chain(self) -> Any:
        """链式台账（只读/验签用；未启用返回 None）"""
        facade = self.facade
        return facade.chain if facade.enabled else None

    def verify_chain(self, **kwargs: Any) -> Any:
        """链式验签（转发 AuditChain.verify_chain；未启用返回 None）"""
        chain = self.chain
        return None if chain is None else chain.verify_chain(**kwargs)

    # ── 写入 ────────────────────────────────────────────────

    def log(self, action: str, input_data: str = "", output_data: str = "",
            status: str = "success", metadata: Optional[dict] = None, *,
            actor: Optional[str] = None, subject: str = "") -> None:
        """记录一条审计日志（旧 JSONL 轨 + 链式轨双写；返回 None 保持既有契约）

        Args:
            action: 操作类型。
            input_data / output_data: 原文（仅存 sha256 前 16 位摘要，不落原文）。
            status: 结果状态（success/error/...）。
            metadata: 附加元数据（入链前自动脱敏）。
            actor: 操作者（None → 本实例缺省 actor）。
            subject: 受影响资源（None/"" → 退化为 action）。
        """
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": get_trace_id() or "",
            "action": action,
            "input_hash": self._hash(input_data) if input_data else "",
            "output_hash": self._hash(output_data) if output_data else "",
            "stack_depth": len(traceback.extract_stack()),
            "status": status,
            "metadata": metadata or {},
        }
        legacy_enabled = True
        if self._dual_write:
            try:
                track = self.track
                legacy_enabled = bool(track.legacy_enabled)
                result = track.emit(
                    action, actor=actor or self._actor, subject=subject or action,
                    payload={
                        "status": status,
                        "input_hash": record["input_hash"],
                        "output_hash": record["output_hash"],
                        "stack_depth": record["stack_depth"],
                        "metadata": record["metadata"],
                        "trace_id": record["trace_id"],
                        "legacy": "audit_jsonl",
                    },
                    legacy_record=record, source="agent", ts=record["timestamp"],
                    trace_id=record["trace_id"])
                if legacy_enabled and not result.legacy_written:
                    # 旧轨本应写入却失败 → 走下方兜底补写（审计不丢）
                    raise RuntimeError("; ".join(result.errors) or "legacy 轨未写入")
                if self._flush_on_write and result.chain_written:
                    self.flush()
                logger.debug(f"[Audit] {action}: status={status} "
                             f"(chain_seq={result.chain_seq}, legacy={result.legacy_written})")
                return
            except Exception as e:  # noqa: BLE001 双写失败 → 退化为旧轨直写
                logger.debug("审计双写失败，退化为旧轨直写: %s", e)
                try:
                    legacy_enabled = bool(self.track.legacy_enabled)
                except Exception:  # noqa: BLE001 轨不可用 → 按旧行为补写
                    legacy_enabled = True
        if legacy_enabled or not self._dual_write:
            self._append_legacy(record)
        logger.debug(f"[Audit] {action}: status={status}")

    def _append_legacy(self, record: Dict[str, Any]) -> None:
        """旧轨直写（S2-02 之前的原始行为，逐字保留；双写关闭/失败时的路径）"""
        with open(self._current_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _hash(self, data: str) -> str:
        """sha256 摘要前 16 位（不落原文）"""
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    # ── 读取 ────────────────────────────────────────────────

    def query(self, trace_id: str = "", action: str = "",
              limit: int = 100) -> List[dict]:
        """查询旧轨审计日志（按日分片倒序；行为与 S2-02 之前一致）"""
        results: List[Dict[str, Any]] = []
        for log_file in sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True):
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line.strip())
                    if trace_id and record.get("trace_id") != trace_id:
                        continue
                    if action and record.get("action") != action:
                        continue
                    results.append(record)
                    if len(results) >= limit:
                        return results
        return results

    def query_chain(self, limit: int = 100, **filters: Any) -> List[AuditEntry]:
        """查询链式轨（同表可查：UI 与 Agent 记录都在此表）"""
        chain = self.chain
        if chain is None:
            return []
        entries: List[AuditEntry] = list(chain.entries(**filters))
        return entries[-int(limit):] if limit else entries

    def flush(self, timeout: float = 5.0) -> bool:
        """等待链式轨落盘（测试/收尾用；未启用链式轨返回 True）"""
        chain = self.chain
        return True if chain is None else chain.flush(timeout=timeout)

    def close(self) -> None:
        """关闭链式轨（释放单写者登记；幂等）"""
        if self._facade is not None:
            self._facade.close()


audit_logger = AuditLogger()


def _safe_call(func, *args, action="safe_call", **kwargs):
    """安全调用包装器——捕获异常并记录结构化日志后重新抛出

    用于边界显性化：可能失败的操作应通过此包装器调用，
    确保异常被记录后再向上传播，而非静默吞掉。
    """
    try:
        return func(*args, **kwargs)
    except Exception as e:
        logger.error(log_dict({'module_name': 'logger', 'action': action + '.failed', 'error': f'{type(e).__name__}: {e}'}))
        raise
