"""消化流水线（TASK-S3-01 / v7.2 §4.5 SkillFactory 流水线）

把 S2 数据地基（统一 Trace 台账 / 链式审计 / events.v1 事件流）接到**唯一的**
固化通道上：``轨迹台账 → 清洗 → 模式挖掘 → SKILL.md 草稿 → stage 迁移``。

模块清单：

| 模块 | 职责 |
|---|---|
| `models` | 数据模型与门槛常量（同类判定键 / 候选模式 / 草稿 / 迁移结果 / 报告） |
| `capability` | 入口侧能力键归一（L1：工具名 ↔ canonical capability_id，含历史行） |
| `cleaning` | 轨迹清洗四规则 + 同类轨迹判定键 |
| `generalize` | 参数泛化（形态占位符 + 具名参数槽，沿用 `${name}` 既有方言） |
| `mining` | LCS 步骤骨架 + 决策树分支条件 + 副作用画像 |
| `generation` | 候选模式 → draft 态 SKILL.md（桥接 `process_distill`/`skills_mgmt`） |
| `stage` | stage 迁移门与执行（descriptor + 审计 + `digest.stage` 三处联动） |
| `service` | `DigestionService.pipeline()` 端到端门面 |

**import 纪律**：本包是**叶子**——既有模块不得反向导入 `agent.digestion`
（否则与 `descriptors.bridge → skills_mgmt` 诸链构成环）。重依赖（`agent.tools`、
`agent.skills_mgmt`、`agent.workflow_learning`、`agent.process_distill.solidify`）
一律在**函数体内**懒加载，故 `import agent.digestion` 无文件/DB/网络副作用。

**范围边界**（不越权）：确定性回放沙箱与验收门 = S3-02；shadow/灰度/内化 = S3-03；
发布签名与人工评审 = 既有 `skills_mgmt` 轨。本包产物一律停在 **draft**。
"""

from __future__ import annotations

from .models import (  # noqa: F401
    MIN_PATTERN_STEPS,
    MIN_SAME_KIND_TRACES,
    MIN_STEP_SUPPORT,
    BranchCondition,
    CandidatePattern,
    DigestionReport,
    SameTaskKey,
    SkillDraft,
    StageMigration,
    TraceSet,
    Trajectory,
    TrajectoryStep,
)
from .service import DigestionService  # noqa: F401

__all__ = [
    # 门面
    "DigestionService",
    # 模型
    "SameTaskKey", "Trajectory", "TrajectoryStep", "TraceSet",
    "CandidatePattern", "BranchCondition", "SkillDraft", "StageMigration",
    "DigestionReport",
    # 门槛常量
    "MIN_SAME_KIND_TRACES", "MIN_PATTERN_STEPS", "MIN_STEP_SUPPORT",
]
