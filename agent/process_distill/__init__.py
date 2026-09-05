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

# PEP 562 模块级懒加载（与 agent.skills_mgmt 同款惯例）：
#   __init__ 顶层不再 from ...service import ... —— 否则 service 反向
#   `from agent.process_distill import sources` 会构成
#   process_distill → process_distill.service → process_distill 的包级循环，
#   被架构规则 no_circular_dependency 拦截（CI 架构规则校验）。
#   副作用收益：仅 import 包名（如 CI 脚本）时不拉入 service → knowledge.search
#   等重依赖链，导入更快、更稳。
# 向后兼容：`from agent.process_distill import ProcessDistillService` 等用法不变，
#   首次访问时经 __getattr__ 导入并缓存到 globals()，后续零额外开销。
_PKG = __name__  # "agent.process_distill"

# 符号名 → (来源模块路径, 符号名)
_LAZY_IMPORTS = {
    # 服务层（重依赖入口：service → knowledge.search / workflow_learning / skills_mgmt）
    "ProcessDistillService": (f"{_PKG}.service", "ProcessDistillService"),
    # 数据模型（轻量 dataclass）
    "DistilledProcess": (f"{_PKG}.models", "DistilledProcess"),
    "DistilledStep": (f"{_PKG}.models", "DistilledStep"),
    "DistillMaterial": (f"{_PKG}.models", "DistillMaterial"),
}

__all__ = [
    "ProcessDistillService",
    "DistilledProcess",
    "DistilledStep",
    "DistillMaterial",
]


def __getattr__(name):
    """PEP 562: 仅在访问时才导入子模块，避免包级循环依赖与重依赖提前加载."""
    if name in _LAZY_IMPORTS:
        import importlib
        module_path, attr_name = _LAZY_IMPORTS[name]
        attr = getattr(importlib.import_module(module_path), attr_name)
        globals()[name] = attr  # 缓存到全局，后续访问零开销
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY_IMPORTS))
