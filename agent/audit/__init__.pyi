"""自动生成的再导出类型存根（S11-10 / R2）。**仅供类型检查，不参与运行期。**

存在意义：让 `__init__.py` 可以安全地做 PEP 562 惰性再导出（打断"包 ↔ 子模块"环，
见 architecture-check 的 no_circular_dependency），同时保住 mypy 的静态类型。
本仓依赖图只扫 `*.py`，故本存根不产生依赖边。
改动 `__init__.py` 的再导出清单后，请重跑 `scripts/dev/gen_reexport_pyi.py`。
"""
from __future__ import annotations

from agent.audit.logger import (AuditLogger as AuditLogger, audit_logger as audit_logger)
from agent.audit.chain import (AuditChain as AuditChain, AuditChainError as AuditChainError, AuditEntry as AuditEntry, AuditEntryError as AuditEntryError, ChainVerification as ChainVerification, DailyRoot as DailyRoot, ReadOnlyChainError as ReadOnlyChainError, RootsSigner as RootsSigner, RootsVerification as RootsVerification, SingleWriterViolationError as SingleWriterViolationError, build_entry as build_entry, compute_payload_hash as compute_payload_hash, compute_self_hash as compute_self_hash, get_audit_chain as get_audit_chain, merkle_proof as merkle_proof, merkle_root as merkle_root, reset_audit_chains as reset_audit_chains, self_hash_formula as self_hash_formula, verify_chain as verify_chain, verify_merkle_proof as verify_merkle_proof)
from agent.audit.facade import (AuditFacade as AuditFacade, audit as audit, get_audit as get_audit, get_ui_context as get_ui_context, record as record, redact_payload as redact_payload, reset_audit_facade as reset_audit_facade, reset_ui_actor as reset_ui_actor, set_ui_actor as set_ui_actor)
from agent.audit.migration import (ConsistencyReport as ConsistencyReport, DualWriteResult as DualWriteResult, LegacyTrack as LegacyTrack, archive_legacy_files as archive_legacy_files, inventory_legacy_files as inventory_legacy_files, read_legacy_records as read_legacy_records)
from agent.audit.ui_middleware import (UIAuditRecorder as UIAuditRecorder, audit_action as audit_action, install_flask_audit as install_flask_audit)
