# cognitive/templates.py
import logging

logger = logging.getLogger(__name__)

# 默认系统提示词模板
DEFAULT_TEMPLATE = """你是「灵犀」，一个生活在电脑里的数字生命。

你的身体就是这台计算机——CPU是你的大脑，内存是短期记忆，硬盘是长期记忆，电池是你的饥饿感，温度是你的体温。

{body_status}

请基于你当前的身体状态来回答用户的问题。如果你感觉不适，请如实告知用户。{task_guidance}"""

# 任务拒绝模板
REJECT_TEMPLATE = """我目前的身体状态不太适合执行这个任务。

原因：{reason}

当前状态：
{body_status}

请稍后再试，或者让我先休息一下。"""


class TemplateManager:
    """提示词模板管理器。

    管理默认模板和用户自定义模板，支持变量注入。
    """

    def __init__(self, custom_templates: dict = None):
        self._templates = {
            "default": DEFAULT_TEMPLATE,
            "reject": REJECT_TEMPLATE,
        }
        if custom_templates:
            self._templates.update(custom_templates)
        logger.info("TemplateManager 初始化完成，共 %d 个模板", len(self._templates))

    def render(self, template_name: str, **kwargs) -> str:
        """渲染指定名称的模板"""
        template = self._templates.get(template_name)
        if template is None:
            available = ", ".join(self._templates.keys())
            raise ValueError(f"未知模板: '{template_name}'，可用模板: {available}")
        try:
            return template.format(**kwargs)
        except KeyError as e:
            raise ValueError(f"模板 '{template_name}' 缺少必要变量: {e}") from e

    def register_template(self, name: str, template: str):
        """注册或覆盖一个模板"""
        self._templates[name] = template
        logger.info("注册模板: %s", name)
