"""自动生成的再导出类型存根（S11-10 / R2）。**仅供类型检查，不参与运行期。**

存在意义：让 `__init__.py` 可以安全地做 PEP 562 惰性再导出（打断"包 ↔ 子模块"环，
见 architecture-check 的 no_circular_dependency），同时保住 mypy 的静态类型。
本仓依赖图只扫 `*.py`，故本存根不产生依赖边。
改动 `__init__.py` 的再导出清单后，请重跑 `scripts/dev/gen_reexport_pyi.py`。
"""
from __future__ import annotations

from agent.repair.budget import (BudgetExceeded as BudgetExceeded, RepairBudget as RepairBudget)
from agent.repair.delegate import (DelegationOutcome as DelegationOutcome, DelegationRequest as DelegationRequest, delegate as delegate_patch)
from agent.repair.diagnose import (DEFAULT_TEST_TARGET as DEFAULT_TEST_TARGET, ProbeExecutor as ProbeExecutor, SubprocessExecutor as SubprocessExecutor, diagnose as run_diagnosis)
from agent.repair.guardrails import (guard_patch as guard_patch, parse_unified_diff as parse_unified_diff)
from agent.repair.locate import (locate as locate_failure)
from agent.repair.models import (AUDIT_ACTIONS as AUDIT_ACTIONS, REPAIR_STEPS as REPAIR_STEPS, DiagnosisReport as DiagnosisReport, FailureItem as FailureItem, PatchGuardReport as PatchGuardReport, RepairPatch as RepairPatch, RepairProposal as RepairProposal, RepairReport as RepairReport, RepairTicket as RepairTicket, VerificationReport as VerificationReport)
from agent.repair.pipeline import (STATUS_DISCARDED as STATUS_DISCARDED, STATUS_ERROR as STATUS_ERROR, STATUS_NO_FAILURE as STATUS_NO_FAILURE, STATUS_PROPOSED as STATUS_PROPOSED, STATUS_REJECTED as STATUS_REJECTED, PipelineRequest as PipelineRequest, audit_completeness as audit_completeness, report_markdown as report_markdown, run_repair as run_repair)
from agent.repair.policy import (READONLY_ZONE_FILES as READONLY_ZONE_FILES, READONLY_ZONE_PREFIXES as READONLY_ZONE_PREFIXES, REPAIR_FORBIDDEN_TOOLS as REPAIR_FORBIDDEN_TOOLS, REPAIR_SUBAGENT_TOOLS as REPAIR_SUBAGENT_TOOLS, RepairPolicy as RepairPolicy, is_readonly_path as is_readonly_path, policy_from_env as policy_from_env, readonly_reason as readonly_reason)
from agent.repair.propose import (ProposalNotVerified as ProposalNotVerified, propose as propose_fix)
from agent.repair.trace import (RepairRunLogger as RepairRunLogger, RunLoggerConfig as RunLoggerConfig, new_run_id as new_run_id)
from agent.repair.verify import (verify as verify_patch)
