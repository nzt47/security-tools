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
from agent.audit.logger import AuditLogger, audit_logger
from agent.audit.chain import (
    AuditChain,
    AuditChainError,
    AuditEntry,
    AuditEntryError,
    ChainVerification,
    DailyRoot,
    ReadOnlyChainError,
    RootsSigner,
    RootsVerification,
    SingleWriterViolationError,
    build_entry,
    compute_payload_hash,
    compute_self_hash,
    get_audit_chain,
    merkle_proof,
    merkle_root,
    reset_audit_chains,
    self_hash_formula,
    verify_chain,
    verify_merkle_proof,
)
from agent.audit.facade import (
    AuditFacade,
    audit,
    get_audit,
    get_ui_context,
    record,
    redact_payload,
    reset_audit_facade,
    reset_ui_actor,
    set_ui_actor,
)
from agent.audit.migration import (
    ConsistencyReport,
    DualWriteResult,
    LegacyTrack,
    archive_legacy_files,
    inventory_legacy_files,
    read_legacy_records,
)
from agent.audit.ui_middleware import UIAuditRecorder, audit_action, install_flask_audit

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
