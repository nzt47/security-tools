"""云枢综合技能管理系统 (Skills Management)

灵感来源: Claude Skills、Code Skills、Trae Skills、OpenClaw、Hermes 等。

四大核心能力:
    1. 技能创建 (creator): AI 辅助生成 / 手动开发 / 多格式安装
    2. 技能发现与管理 (searcher + reviewer): 高级搜索 + 三重审核
       (重复检测 / 安全扫描 / 质量评估)
    3. 技能集成与增强 (enhancer): 版本管理 / 参数优化 / 性能追踪
    4. (与 agent/workflow_learning 协同) 智能工作流学习

公开入口:
    from agent.skills_mgmt import SkillsMgmtService
    svc = SkillsMgmtService()
"""

# PEP 562 模块级懒加载: 仅在访问具体符号时才导入重依赖.
# 不变量(不易): skills_mgmt/__init__.py 顶层不再拉入 service→models→pydantic、
#   service→creator/reviewer/... 等重依赖链, 让 CI 脚本
#   (compare_skills_legacy_vs_repo.py 只需 file_store,
#    verify_migrated_skills.py 只需 skill_manager + exceptions)
#   可独立导入轻量子模块, 不应被 pydantic 等重依赖绑架.
# 向后兼容: `from agent.skills_mgmt import SkillsMgmtService, Skill, ...` 仍可用,
#   生产环境依赖齐全时正常懒加载.
# 缓存: __getattr__ 首次解析后写入 globals(), 后续访问走正常属性查找, 零额外开销.
_PKG = __name__  # "agent.skills_mgmt"

# 符号名 → (来源模块路径, 符号名)
_LAZY_IMPORTS = {
    # 服务层 (重依赖入口: service→models→pydantic + creator/reviewer/searcher/...)
    "SkillsMgmtService": (f"{_PKG}.service", "SkillsMgmtService"),
    # 数据模型 (pydantic BaseModel)
    "Skill": (f"{_PKG}.models", "Skill"),
    "SkillVersion": (f"{_PKG}.models", "SkillVersion"),
    "SkillCategory": (f"{_PKG}.models", "SkillCategory"),
    "SkillStatus": (f"{_PKG}.models", "SkillStatus"),
    "ReviewResult": (f"{_PKG}.models", "ReviewResult"),
    "ReviewStatus": (f"{_PKG}.models", "ReviewStatus"),
    "SkillMetrics": (f"{_PKG}.models", "SkillMetrics"),
    "SkillSearchParams": (f"{_PKG}.models", "SkillSearchParams"),
    "SkillSearchResult": (f"{_PKG}.models", "SkillSearchResult"),
    # 异常 (轻量, 但统一走懒加载保持模式一致)
    "SkillMgmtError": (f"{_PKG}.exceptions", "SkillMgmtError"),
    "SkillNotFoundError": (f"{_PKG}.exceptions", "SkillNotFoundError"),
    "SkillAlreadyExistsError": (f"{_PKG}.exceptions", "SkillAlreadyExistsError"),
    "SkillValidationError": (f"{_PKG}.exceptions", "SkillValidationError"),
    "SkillReviewError": (f"{_PKG}.exceptions", "SkillReviewError"),
    "SkillInstallError": (f"{_PKG}.exceptions", "SkillInstallError"),
    "SkillSecurityError": (f"{_PKG}.exceptions", "SkillSecurityError"),
    "ErrorCode": (f"{_PKG}.exceptions", "ErrorCode"),
}


def __getattr__(name):
    """PEP 562: 仅在访问时才导入重依赖, 避免 import agent.skills_mgmt 触发整包重依赖加载."""
    if name in _LAZY_IMPORTS:
        import importlib
        module_path, attr_name = _LAZY_IMPORTS[name]
        attr = getattr(importlib.import_module(module_path), attr_name)
        globals()[name] = attr  # 缓存到全局, 后续访问零开销
        return attr
    raise AttributeError(f"module {_PKG!r} has no attribute {name!r}")


def __dir__():
    """补全 dir(agent.skills_mgmt), 让懒加载符号可被发现 (REPL/IDE 自动补全兼容)."""
    return sorted(set(globals()) | set(_LAZY_IMPORTS))


__all__ = [
    "SkillsMgmtService",
    "Skill",
    "SkillVersion",
    "SkillCategory",
    "SkillStatus",
    "ReviewResult",
    "ReviewStatus",
    "SkillMetrics",
    "SkillSearchParams",
    "SkillSearchResult",
    "SkillMgmtError",
    "SkillNotFoundError",
    "SkillAlreadyExistsError",
    "SkillValidationError",
    "SkillReviewError",
    "SkillInstallError",
    "SkillSecurityError",
    "ErrorCode",
]

__version__ = "1.0.0"
