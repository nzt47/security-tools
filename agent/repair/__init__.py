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
from agent.repair.guardrails import guard_patch, parse_unified_diff
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
from agent.repair.trace import RepairRunLogger, RunLoggerConfig, new_run_id
import importlib as _importlib

# ── 【S11-10 / R2】惰性再导出（PEP 562）────────────────────────────────────
# 为什么：下列子模块会（直接或间接）依赖回本包，构成"包 ↔ 子模块"环，
# 被 architecture-check 的 no_circular_dependency 规则阻断。急切再导出正是环的一条边；
# 改为按需解析可**真实消除运行期的急切耦合**（不是把 import 换个写法隐藏起来）：
#   `from agent.repair import X`、`agent.repair.X`、`hasattr(agent.repair, "X")` 语义均不变，只是解析推迟到首次访问。
# 类型层由同目录 `__init__.pyi` 声明——本仓依赖图只扫 `*.py`，故存根不产生依赖边。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "DEFAULT_TEST_TARGET": ("agent.repair.diagnose", "DEFAULT_TEST_TARGET"),
    "ProbeExecutor": ("agent.repair.diagnose", "ProbeExecutor"),
    "SubprocessExecutor": ("agent.repair.diagnose", "SubprocessExecutor"),
    "run_diagnosis": ("agent.repair.diagnose", "diagnose"),
    "locate_failure": ("agent.repair.locate", "locate"),
    "STATUS_DISCARDED": ("agent.repair.pipeline", "STATUS_DISCARDED"),
    "STATUS_ERROR": ("agent.repair.pipeline", "STATUS_ERROR"),
    "STATUS_NO_FAILURE": ("agent.repair.pipeline", "STATUS_NO_FAILURE"),
    "STATUS_PROPOSED": ("agent.repair.pipeline", "STATUS_PROPOSED"),
    "STATUS_REJECTED": ("agent.repair.pipeline", "STATUS_REJECTED"),
    "PipelineRequest": ("agent.repair.pipeline", "PipelineRequest"),
    "audit_completeness": ("agent.repair.pipeline", "audit_completeness"),
    "report_markdown": ("agent.repair.pipeline", "report_markdown"),
    "run_repair": ("agent.repair.pipeline", "run_repair"),
    "ProposalNotVerified": ("agent.repair.propose", "ProposalNotVerified"),
    "propose_fix": ("agent.repair.propose", "propose"),
    "verify_patch": ("agent.repair.verify", "verify"),
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
