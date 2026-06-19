# cognitive/prompt_injector.py
import logging

from cognitive.config import PromptConfig
from cognitive.translator import Translator
from cognitive.templates import TemplateManager

logger = logging.getLogger(__name__)


class PromptInjector:
    """元认知引擎核心——编排翻译、模板注入和任务决策。

    将传感器数值数据转化为拟人化自然语言描述，
    注入到 LLM 系统提示词中，使 AI 具有"身体感知"。
    """

    def __init__(self, config: PromptConfig = None, templates: dict = None):
        self.config = config or PromptConfig()
        self.translator = Translator(self.config)
        self.template_mgr = TemplateManager(templates)
        logger.info("PromptInjector 初始化完成")

    def inject(self, sensor_data: list[dict]) -> str:
        """接收传感器数据，返回注入身体状态后的完整系统提示词"""
        if sensor_data is None or not isinstance(sensor_data, list):
            return self.template_mgr.render(
                "default",
                body_status="身体状态正常。",
                task_guidance="状态良好，可以正常执行任务。",
            )
        
        valid_data = [r for r in sensor_data if isinstance(r, dict)]
        status_lines = self.translator.translate_all(valid_data)
        body_status = "\n".join(status_lines) if status_lines else "身体状态正常。"
        alerts = self._get_alerts(valid_data)
        task_guidance = self._generate_guidance(alerts)
        return self.template_mgr.render(
            "default",
            body_status=body_status,
            task_guidance=task_guidance,
        )

    def translate(self, reading: dict) -> str:
        """将单条传感器数据翻译为拟人化描述"""
        return self.translator.translate(reading)

    def get_summary(self, sensor_data: list[dict]) -> str:
        """获取所有传感器的综合状态摘要"""
        return self.translator.get_status_line(sensor_data)

    def should_reject_task(self, sensor_data: list[dict]) -> tuple:
        """判断是否应该拒绝当前任务。

        Returns:
            tuple[bool, str]: (是否拒绝, 原因描述)
        """
        criticals = [r for r in sensor_data if r.get("severity") == "critical"]
        warnings = [r for r in sensor_data if r.get("severity") == "warning"]

        if criticals:
            reasons = [self.translator.translate(r) for r in criticals]
            return (True, f"身体出现严重不适：{'；'.join(reasons)}")

        if len(warnings) >= 3:
            return (False, "虽然还能工作，但状态不太好，建议简化任务")

        return (False, "一切正常，随时待命")

    def _get_alerts(self, sensor_data: list[dict]) -> list[dict]:
        """筛选出 warning 和 critical 级别的告警"""
        return [r for r in sensor_data if r.get("severity") in ("warning", "critical")]

    def _generate_guidance(self, alerts: list[dict]) -> str:
        """根据告警生成任务执行建议"""
        if not alerts:
            return "状态良好，可以正常执行任务。"
        critical_count = sum(1 for a in alerts if a.get("severity") == "critical")
        if critical_count > 0:
            return "请注意，我当前身体不适，可能影响任务执行效率。"
        return "我有点疲惫，但还能坚持完成任务。"
