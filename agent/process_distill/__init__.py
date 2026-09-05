"""云枢过程蒸馏 (Process Distill) — 从知识库/素材蒸馏可复现步骤序列并固化。

核心能力（方向与 skills_mgmt.memory_abstractor 相反）：
    不是等交互经验"自己冒出来"，而是云枢主动把知识库中的外部过程素材
    （复盘文档 / SKILL.md / wiki 卡 / 任意 .md）蒸馏为结构化、可复现的
    步骤序列，再固化为两种可复用资产：
        - workflow：LearnedWorkflow（data/learned_workflows.json，可被执行，
          主循环工作流学习层 0-Token 命中）
        - skill：skills_repo/<id>/skill.md（文件轨，语义层 SkillLoader 长期召回）

编排：sources(素材) → distiller(并行子代理蒸馏) → merge(合并归一)
      → solidify(固化 workflow / skill)

设计原则：
    - 子代理即隔离上下文：每条素材 = 一个独立 LLM worker（线程池并行），
      各自产出步骤序列，主代理只消费合并结果（同 subagent 隔离思想）。
    - 降级铁律：LLM 不可用/解析失败 → 规则提取骨架，绝不抛异常；
      仅"素材不存在/参数错误"抛 ValueError。
    - 幂等：skill_id/workflow_id 由素材内容哈希派生，重复蒸馏不重复创建。
    - 边界显性化：固化为 workflow 仅当步骤能映射到云枢工具；
      纯指令步骤固化为 skill（markdown 步骤），不伪造工具调用。

公开入口：
    from agent.process_distill import ProcessDistillService
    svc = ProcessDistillService()
    result = svc.distill(query="git gc 维护复盘", artifact="both")
"""

from agent.process_distill.service import ProcessDistillService
from agent.process_distill.models import (
    DistilledProcess,
    DistilledStep,
    DistillMaterial,
)

__all__ = [
    "ProcessDistillService",
    "DistilledProcess",
    "DistilledStep",
    "DistillMaterial",
]
