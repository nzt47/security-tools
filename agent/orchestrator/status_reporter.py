"""StatusReporter — 从 Orchestrator 提取的状态报告模块

职责:
- 构建云枢完整状态报告（get_status）
- 生成人类可读状态文本（get_status_text）
- 健康检查（check_health）

依赖:
- orchestrator 上的 body、_behavior、_memory、_subagent_mgr 等组件
"""

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class StatusReporter:
    """状态报告模块——构建状态报告与健康检查

    通过持有 Orchestrator 引用来访问其属性和组件，
    提供状态查询的统一接口。
    """

    def __init__(self, orchestrator: Any):
        """绑定到 Orchestrator 实例

        Args:
            orchestrator: Orchestrator 实例（或其子类 DigitalLife）
        """
        self._o = orchestrator

    # ── 健康检查 ──

    def check_health(self) -> list:
        """检查身体状态（感知层）

        Returns:
            SensorReading 列表
        """
        readings = self._o.body.collect_quick()
        self._o._current_mode = self._o._behavior.evaluate(readings)
        self._o._last_health_check = time.time()

        if getattr(self._o, '_v2_lifetrace', False) and getattr(self._o, '_trace_recorder', None):
            interaction_id = getattr(self._o, '_interaction_count', 0)
            for reading in readings:
                try:
                    self._o._trace_recorder.record_sensor(
                        sensor_type=reading.sensor_name,
                        data={
                            "value": reading.value,
                            "unit": reading.unit,
                            "severity": reading.severity,
                        },
                        metadata={"interaction_id": interaction_id},
                    )
                except Exception:
                    pass

        return readings

    # ── 详细状态报告 ──

    def get_status(self) -> dict:
        """获取云枢的完整状态报告"""
        from agent import tools

        readings = self._o.body.collect_quick()
        profile = self._o._behavior.profile

        status = {
            "云枢": {
                "版本": "2.0" if getattr(self._o, '_v2_lifetrace', False) else "1.0",
                "会话": getattr(self._o, '_session_id', ''),
                "运行中": getattr(self._o, '_running', False),
                "交互次数": getattr(self._o, '_interaction_count', 0),
            },
            "行为模式": {
                "当前模式": str(getattr(self._o, '_current_mode', '')),
                "模式名称": profile.label,
                "模式描述": profile.description,
                "可接受任务": profile.can_accept_tasks,
                "启用反思": profile.enable_reflection,
            },
            "身体状态": {
                str(r.sensor_name): {
                    "值": f"{r.value}{r.unit}",
                    "严重程度": r.severity,
                    "描述": r.description,
                }
                for r in readings
            },
            "系统": {
                "工具数量": len([t for t in tools.list_tools()]),
                "已启用工具数": (
                    len(self._o._get_enabled_tools_whitelist())
                    if hasattr(self._o, '_get_enabled_tools_whitelist')
                    and self._o._get_enabled_tools_whitelist() is not None
                    else len(tools.list_tools())
                ),
                "智能工具选择": self._o._is_smart_tool_selection_enabled() 
                              if hasattr(self._o, '_is_smart_tool_selection_enabled') 
                              else "未知",
                "记忆摘要": (
                    self._o._memory.load_summary()[0][:100]
                    if self._o._memory and self._o._memory.load_summary() else "无"
                ),
                "反思记录数": len(getattr(self._o, '_reflection_history', [])),
            },
        }

        # 搜索引擎健康状态（来自 LifecycleManager mixin）
        try:
            status["搜索引擎"] = self._o._get_engine_health_status()
        except Exception:
            status["搜索引擎"] = {"available": False, "details": "查询失败"}

        # 分身状态
        status["分身"] = (
            self._o._subagent_mgr.get_stats()
            if getattr(self._o, '_subagent_mgr', None) else {"active_count": 0}
        )

        # V2 增强功能
        if getattr(self._o, '_v2_lifetrace', False) and getattr(self._o, '_trace_recorder', None):
            try:
                ls = self._o._trace_recorder.get_statistics()
                status["LifeTrace"] = {
                    "源节点数": ls.get("source_nodes", 0),
                    "主题节点数": ls.get("topic_nodes", 0),
                    "主题列表": ls.get("topics", []),
                }
            except Exception:
                pass

        if getattr(self._o, '_v2_persona', False) and getattr(self._o, '_persona_model', None):
            try:
                status["Persona"] = {
                    "人格ID": self._o._persona_model.persona.get("persona_id"),
                    "版本": self._o._persona_model.persona.get("version"),
                }
            except Exception:
                pass

        if getattr(self._o, '_v2_distillation', False) and getattr(self._o, '_persona_extractor', None):
            try:
                rpt = self._o._persona_extractor.export_preferences()
                prefs = rpt.get("preferences", {})
                status["人格蒸馏"] = {
                    "启用": True,
                    "学习间隔": getattr(self._o, '_distillation_interval', 5),
                    "话题兴趣": list(prefs.get("topic_interest", {}).keys())[:5],
                    "最后更新": prefs.get("last_updated", "未知"),
                }
            except Exception:
                pass

        return status

    # ── 人类可读状态 ──

    def get_status_text(self) -> str:
        """获取人类可读的状态描述"""
        profile = self._o._behavior.profile
        health = self._o.body.get_health_report()

        v2_info = " (V2增强版)" if getattr(self._o, '_v2_lifetrace', False) else ""

        return (
            f"* 云枢{v2_info}状态\n"
            f"━━━━━━━━━━━━━━━\n"
            f"会话: {getattr(self._o, '_session_id', '')}\n"
            f"运行中: {'是' if getattr(self._o, '_running', False) else '否'}\n"
            f"交互次数: {getattr(self._o, '_interaction_count', 0)}\n"
            f"行为模式: {profile.label}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"{health}"
        )
