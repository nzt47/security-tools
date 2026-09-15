"""审计日志系统 - Append-only 结构化审计日志 + 链式防篡改审计（v7.2 §3.5）

【两层结构（S2-02 交付）】
    - 兼容层（既有）：`AuditLogger` / `audit_logger`——按日 JSONL 追加（旧轨，
      现为**双写过渡**：同一条记录同时写旧 JSONL 与新链式轨）。
    - 链式层（新增）：`agent.audit.chain`——`AuditEntry` / `AuditChain` /
      `verify_chain()` / `daily_merkle_root()`，含 prev_hash / self_hash 链、
      单写者纪律、每日 Merkle 根与验签。
    - 统一门面：`agent.audit.facade.audit.record(...)`——**UI 与 Agent 同表**
      （P7.2-24 审计平权）。
    - UI 写路由包装：`agent.audit.ui_middleware.install_flask_audit(app)`。
    - 存量迁移：`agent.audit.migration`——只读归档 + 双写一致性校验。
"""

__all__ = [
    # 兼容层（既有符号，零移除）
    "AuditLogger", "audit_logger",
    # 链式层
    "AuditChain", "AuditChainError", "AuditEntry", "AuditEntryError",
    "ChainVerification", "DailyRoot", "ReadOnlyChainError", "RootsSigner",
    "RootsVerification", "SingleWriterViolationError", "build_entry",
    "compute_payload_hash", "compute_self_hash", "get_audit_chain", "merkle_proof",
    "merkle_root", "reset_audit_chains", "self_hash_formula", "verify_chain",
    "verify_merkle_proof",
    # 统一门面（P7.2-24）
    "AuditFacade", "audit", "get_audit", "get_ui_context", "record", "redact_payload",
    "reset_audit_facade", "reset_ui_actor", "set_ui_actor",
    # UI 写路由包装
    "UIAuditRecorder", "audit_action", "install_flask_audit",
    # 存量迁移
    "ConsistencyReport", "DualWriteResult", "LegacyTrack", "archive_legacy_files",
    "inventory_legacy_files", "read_legacy_records",
]
import importlib as _importlib

# ── 【S11-10 / R2】惰性再导出（PEP 562）────────────────────────────────────
# 为什么：下列子模块会（直接或间接）依赖回本包，构成"包 ↔ 子模块"环，
# 被 architecture-check 的 no_circular_dependency 规则阻断。急切再导出正是环的一条边；
# 改为按需解析可**真实消除运行期的急切耦合**（不是把 import 换个写法隐藏起来）：
#   `from agent.audit import X`、`agent.audit.X`、`hasattr(agent.audit, "X")` 语义均不变，只是解析推迟到首次访问。
# 类型层由同目录 `__init__.pyi` 声明——本仓依赖图只扫 `*.py`，故存根不产生依赖边。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AuditLogger": ("agent.audit.logger", "AuditLogger"),
    "audit_logger": ("agent.audit.logger", "audit_logger"),
    "AuditChain": ("agent.audit.chain", "AuditChain"),
    "AuditChainError": ("agent.audit.chain", "AuditChainError"),
    "AuditEntry": ("agent.audit.chain", "AuditEntry"),
    "AuditEntryError": ("agent.audit.chain", "AuditEntryError"),
    "ChainVerification": ("agent.audit.chain", "ChainVerification"),
    "DailyRoot": ("agent.audit.chain", "DailyRoot"),
    "ReadOnlyChainError": ("agent.audit.chain", "ReadOnlyChainError"),
    "RootsSigner": ("agent.audit.chain", "RootsSigner"),
    "RootsVerification": ("agent.audit.chain", "RootsVerification"),
    "SingleWriterViolationError": ("agent.audit.chain", "SingleWriterViolationError"),
    "build_entry": ("agent.audit.chain", "build_entry"),
    "compute_payload_hash": ("agent.audit.chain", "compute_payload_hash"),
    "compute_self_hash": ("agent.audit.chain", "compute_self_hash"),
    "get_audit_chain": ("agent.audit.chain", "get_audit_chain"),
    "merkle_proof": ("agent.audit.chain", "merkle_proof"),
    "merkle_root": ("agent.audit.chain", "merkle_root"),
    "reset_audit_chains": ("agent.audit.chain", "reset_audit_chains"),
    "self_hash_formula": ("agent.audit.chain", "self_hash_formula"),
    "verify_chain": ("agent.audit.chain", "verify_chain"),
    "verify_merkle_proof": ("agent.audit.chain", "verify_merkle_proof"),
    "AuditFacade": ("agent.audit.facade", "AuditFacade"),
    "audit": ("agent.audit.facade", "audit"),
    "get_audit": ("agent.audit.facade", "get_audit"),
    "get_ui_context": ("agent.audit.facade", "get_ui_context"),
    "record": ("agent.audit.facade", "record"),
    "redact_payload": ("agent.audit.facade", "redact_payload"),
    "reset_audit_facade": ("agent.audit.facade", "reset_audit_facade"),
    "reset_ui_actor": ("agent.audit.facade", "reset_ui_actor"),
    "set_ui_actor": ("agent.audit.facade", "set_ui_actor"),
    "ConsistencyReport": ("agent.audit.migration", "ConsistencyReport"),
    "DualWriteResult": ("agent.audit.migration", "DualWriteResult"),
    "LegacyTrack": ("agent.audit.migration", "LegacyTrack"),
    "archive_legacy_files": ("agent.audit.migration", "archive_legacy_files"),
    "inventory_legacy_files": ("agent.audit.migration", "inventory_legacy_files"),
    "read_legacy_records": ("agent.audit.migration", "read_legacy_records"),
    "UIAuditRecorder": ("agent.audit.ui_middleware", "UIAuditRecorder"),
    "audit_action": ("agent.audit.ui_middleware", "audit_action"),
    "install_flask_audit": ("agent.audit.ui_middleware", "install_flask_audit"),
}


def __getattr__(name: str) -> object:
    """PEP 562：按需解析本包的再导出名。"""
    entry = _LAZY_EXPORTS.get(name)
    if entry is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = entry
    return getattr(_importlib.import_module(mod_name), attr)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
