"""自修复 L1（自动诊断与补丁 PR）：能修，但不自动落地（TASK-S7-02）

【这一层是什么】
    v7.2 §4.4 的自愈分级里，L1 = **自动诊断 + 自动产出补丁 PR，合入仍人工**。
    本包把云枢已有但彼此孤立的零件（S4-04 真子代理委派、S5-02 L0 锚标尺、S2-01/02
    Trace 与链式审计、只读 git 局部历史）串成一条**受控闭环**：

        体检 diagnose → 定位 locate → 派工 delegate → 隔离验证 verify → 产出 propose
                                                                    （→ **人工合入**）

【四条宪法式边界（违反即本任务失败；每一条都有代码级落点）】
    1. **绝不自动 push / 绝不自动合并** → ``gitio`` 只读白名单 + 唯一写操作
       ``create_local_branch()``；``RepairProposal.pushed/merged`` 恒为 ``False``。
    2. **绝不触碰只读区**（``core/auth/`` / ``core/audit/`` / ``schema/`` /
       ``agent/audit/chain.py`` / ``agent/security/`` / ``eval/l0_anchor/``）→
       ``guardrails.guard_patch()`` 命中即整包丢弃。
    3. **验证不过就不产出**（目标用例 + L0 锚 + 邻接回归三关）→
       ``propose.propose()`` 在 ``verification.ok=False`` 时抛
       ``ProposalNotVerified``（结构性拒绝，无法绕过）。
    4. **全程可审计**（体检/定位/派工/验证/产出五步各一条 Trace + 链式审计）→
       ``trace.RepairRunLogger.run_step()`` 在成功/失败/异常三条路径上都写；
       ``pipeline.audit_completeness()`` 给出机械可查的完整性判定。

【本包明确不做】
    - 自动触发（定时/告警驱动）——只提供**手动**入口 ``scripts/self_repair.py``；
    - 自动 push / 自动合并 / 远端 PR；
    - 核心自改写（v7.2 §5.2 熔炉 Forge/Vault）——属 L3，未实现；
    - 修改只读区与审计/签名相关代码。

【公开入口】
    常用：``run_repair``（一条命令跑完全流程）、``run_diagnosis``、``guard_patch``、
    ``verify_patch``、``propose_fix``、``policy_from_env``。
    完整清单见 ``__all__``。

【命名约定（为什么不把五步函数叫 diagnose/verify/propose）】
    五个步骤各有同名子模块（``agent/repair/diagnose.py`` 等）。若包级再导出同名
    函数，``from agent.repair import diagnose`` 拿到的是**函数**而不是模块——这会
    让 ``import agent.repair.diagnose as D`` 这类调用点在不同导入顺序下行为不一致
    （子模块绑定会被包级导出覆盖）。故五步函数统一加动作后缀：``run_diagnosis`` /
    ``locate_failure`` / ``delegate_patch`` / ``verify_patch`` / ``propose_fix``；
    子模块名保持与步骤同名，二者互不遮蔽。
"""

from __future__ import annotations

from agent.repair.budget import BudgetExceeded, RepairBudget
from agent.repair.delegate import (
    DelegationOutcome,
    DelegationRequest,
    delegate as delegate_patch,
)
from agent.repair.diagnose import (
    DEFAULT_TEST_TARGET,
    ProbeExecutor,
    SubprocessExecutor,
    diagnose as run_diagnosis,
)
from agent.repair.guardrails import guard_patch, parse_unified_diff
from agent.repair.locate import locate as locate_failure
from agent.repair.models import (
    AUDIT_ACTIONS,
    REPAIR_STEPS,
    DiagnosisReport,
    FailureItem,
    PatchGuardReport,
    RepairPatch,
    RepairProposal,
    RepairReport,
    RepairTicket,
    VerificationReport,
)
from agent.repair.pipeline import (
    STATUS_DISCARDED,
    STATUS_ERROR,
    STATUS_NO_FAILURE,
    STATUS_PROPOSED,
    STATUS_REJECTED,
    PipelineRequest,
    audit_completeness,
    report_markdown,
    run_repair,
)
from agent.repair.policy import (
    READONLY_ZONE_FILES,
    READONLY_ZONE_PREFIXES,
    REPAIR_FORBIDDEN_TOOLS,
    REPAIR_SUBAGENT_TOOLS,
    RepairPolicy,
    is_readonly_path,
    policy_from_env,
    readonly_reason,
)
from agent.repair.propose import ProposalNotVerified, propose as propose_fix
from agent.repair.trace import RepairRunLogger, RunLoggerConfig, new_run_id
from agent.repair.verify import verify as verify_patch

__all__ = [
    # 策略与护栏
    "RepairPolicy", "policy_from_env", "is_readonly_path", "readonly_reason",
    "READONLY_ZONE_PREFIXES", "READONLY_ZONE_FILES",
    "REPAIR_SUBAGENT_TOOLS", "REPAIR_FORBIDDEN_TOOLS",
    "guard_patch", "parse_unified_diff", "PatchGuardReport",
    # 数据模型
    "DiagnosisReport", "FailureItem", "RepairTicket", "RepairPatch",
    "VerificationReport", "RepairProposal", "RepairReport", "REPAIR_STEPS",
    "AUDIT_ACTIONS",
    # 五步（函数名带动作后缀，避免遮蔽同名子模块；见模块 docstring 命名约定）
    "run_diagnosis", "DEFAULT_TEST_TARGET", "locate_failure", "delegate_patch",
    "DelegationRequest", "DelegationOutcome", "verify_patch", "propose_fix",
    "ProposalNotVerified",
    # 预算与留痕
    "RepairBudget", "BudgetExceeded", "RepairRunLogger", "RunLoggerConfig",
    "new_run_id",
    # 流水线
    "PipelineRequest", "run_repair", "report_markdown", "audit_completeness",
    "STATUS_NO_FAILURE", "STATUS_PROPOSED", "STATUS_DISCARDED", "STATUS_REJECTED",
    "STATUS_ERROR",
    # 执行器
    "ProbeExecutor", "SubprocessExecutor",
]
