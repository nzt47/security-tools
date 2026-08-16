"""云枢功能模块注册表（S1：模块拓扑数据源）

【不易】契约：
- 纯数据声明 + 纯函数辅助，不 import 任何业务重依赖（可被轻量测试/CI 独立导入）
- status_source 只读声明，聚合器（S2 modules_api）按其取值，不在此处采集
- ACTION_ROUTES 仅描述"动作 → 既有 API"映射，**不新增业务逻辑、不绕过安全边界**
- 危险动作（danger=high）必须二次确认 + reason 必填，由聚合层强制

【变易】新增模块只需向 DOMAINS 追加节点声明，前端拓扑自动上图。
【简易】dataclass 声明式，30s 可读。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__version__ = "0.1.0"


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class ModuleNode:
    """单个功能模块节点

    Attributes:
        module_id: 唯一 ID（域.子域.名称），供状态聚合与干预路由定位
        name: 展示名
        path: 代码路径（供详情面板跳转/定位）
        status_source: 状态来源声明，形如 "api:/api/sensors" 或
                       "config:learning.evolver.enabled"（S2 聚合器解析）
        metrics: 该节点要展示的关键指标键（透传状态源响应的字段名）
        actions: 允许的干预动作 key（必须在 ACTION_ROUTES 中已声明）
        danger: 干预风险等级 low / medium / high
        description: 一句话核心功能
    """
    module_id: str
    name: str
    path: str = ""
    type: str = "module"            # module / sensor / service / task / config
    status_source: str = ""         # api:xxx | config:xxx
    metrics: List[str] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    danger: str = "low"             # low / medium / high
    description: str = ""


@dataclass
class Domain:
    """功能域（六域分层）"""
    domain_id: str
    domain_name: str
    icon: str
    nodes: List[ModuleNode] = field(default_factory=list)


# ════════════════════════════════════════════════════════════
#  动作 → 既有 API 映射表（已核对 app_server.py 路由签名）
#  参数模板 {param: "字段名"} 表示前端透传字段；固定值直接写常量
# ════════════════════════════════════════════════════════════

@dataclass
class ActionRoute:
    """干预动作 → 既有 API 的映射描述

    params 语义：{"接口字段": "透传字段"}
      - "name": 表示从前端入参取 name 字段
      - "const:stop": 固定值，忽略前端入参（防篡改，如紧急动作）
    """
    method: str
    url: str
    params: Dict[str, str]          # {"接口字段": "透传字段" | "const:固定值"}
    danger: str = "low"
    note: str = ""


ACTION_ROUTES: Dict[str, ActionRoute] = {
    # ── 工具 / 技能 / 扩展 / 定时任务（toggle 类）──
    "toggle_tool":      ActionRoute("POST", "/api/tools/toggle",      {"name": "name", "enabled": "enabled"}, "medium"),
    "toggle_skill":     ActionRoute("POST", "/api/skills/toggle",     {"id": "id"}, "medium"),
    "toggle_extension": ActionRoute("POST", "/api/extensions/toggle", {"type": "type", "id": "id", "enabled": "enabled"}, "medium"),
    "toggle_scheduler": ActionRoute("POST", "/api/scheduler/toggle",  {"id": "id", "enabled": "enabled"}, "medium"),
    "execute_scheduler": ActionRoute("POST", "/api/scheduler/execute-now", {"id": "id"}, "medium"),
    "delete_scheduler": ActionRoute("POST", "/api/scheduler/delete",  {"id": "id"}, "high", "删除定时任务不可恢复"),
    # ── 权限 / 紧急控制（高危，二次确认 + reason；action 固定值防篡改）──
    "emergency_stop":   ActionRoute("POST", "/api/permission/emergency", {"action": "const:stop"}, "high"),
    "emergency_pause":  ActionRoute("POST", "/api/permission/emergency", {"action": "const:pause"}, "high"),
    "block_network":    ActionRoute("POST", "/api/permission/emergency", {"action": "const:network_block"}, "high"),
    "toggle_permission": ActionRoute("POST", "/api/permission/toggle", {"key": "key", "enabled": "enabled"}, "medium"),
    # ── 记忆 / 上下文 / 人格 / 提示词 ──
    "compress_memory":  ActionRoute("POST", "/api/memory/compress",  {}, "low"),
    "compress_context": ActionRoute("POST", "/api/context/compress", {}, "low"),
    "add_memory":       ActionRoute("POST", "/api/memory/manual",    {"content": "content", "priority": "priority"}, "low"),
    "set_personality":  ActionRoute("POST", "/api/personality/profile", {"profile": "profile"}, "medium"),
    "reset_personality": ActionRoute("POST", "/api/personality/reset", {}, "medium"),
    "save_prompt":      ActionRoute("POST", "/api/system-prompt",    {"content": "content"}, "medium"),
    # ── 配置 / 网络 / LLM ──
    "update_network":   ActionRoute("POST", "/api/network-config",   {"config": "config"}, "medium"),
    "reconfigure_llm":  ActionRoute("POST", "/api/config",           {"provider": "provider", "api_key": "api_key", "model": "model"}, "high", "切换 LLM 会清空当前会话上下文"),
    # ── 进程 / 安全策略 / 性能监控 ──
    "start_process":    ActionRoute("POST", "/api/process/start",    {"program": "program", "args": "args"}, "medium"),
    "stop_process":     ActionRoute("POST", "/api/process/stop",     {"pid": "pid"}, "high"),
    "add_keyword":      ActionRoute("POST", "/api/safety/keywords",  {"pattern": "pattern", "description": "description",
                                                                     "level": "level", "category": "category"}, "medium"),
    "start_search_perf": ActionRoute("POST", "/api/search-performance/start", {"interval_sec": "interval_sec"}, "low"),
    "stop_search_perf":  ActionRoute("POST", "/api/search-performance/stop",  {}, "low"),
    # ── 规划引擎运行开关（POST /api/planning/toggle，热生效）──
    "toggle_planning":   ActionRoute("POST", "/api/planning/toggle", {"enabled": "enabled"}, "medium",
                                     "运行中热切换 _planning_enabled；持久化需改 config.yaml planning.enabled"),
}


# ════════════════════════════════════════════════════════════
#  六域模块树（与《云枢系统功能模块审计报告》层级一致）
# ════════════════════════════════════════════════════════════

DOMAINS: List[Domain] = [
    Domain("perception", "感知层", "👁", [
        ModuleNode("sensor.body", "感知聚合器 BodySensor", "sensor/body_sensor.py",
                   type="sensor", status_source="api:/api/sensors",
                   metrics=["sensor_on", "sensor_total"],
                   description="聚合全部传感器采集与回调分发"),
        ModuleNode("sensor.registry", "传感器注册表", "sensor/registry.py",
                   type="service", status_source="api:/api/sensors",
                   metrics=["sensor_on", "sensor_total"],
                   description="自发现扫描并接入符合规范的传感器"),
        ModuleNode("sensor.hw", "硬件传感器组", "sensor/cpu_sensor.py 等 10 个",
                   type="sensor", status_source="api:/api/panorama",
                   metrics=["cpu_usage", "memory_usage", "battery"],
                   description="CPU/GPU/内存/电池/磁盘/网络/主板/机箱/端口/外设"),
        ModuleNode("sensor.env", "环境与系统传感器组", "sensor/environment_sensor.py 等 4 个",
                   type="sensor", status_source="api:/api/panorama",
                   metrics=["cpu_usage", "memory_usage"],
                   description="环境/行为活动/系统状态/进程感知"),
        ModuleNode("sensor.file", "文件与事件监控", "sensor/file_watcher.py",
                   type="service", status_source="config:sensor_learning.enabled",
                   description="文件变动监听、变更检测、事件监控"),
        ModuleNode("sensor.ext", "扩展感知", "sensor/ocr_sensor.py 等 3 个",
                   type="sensor", status_source="api:/api/panorama",
                   description="屏幕 OCR / 语音 / 窗口活动感知"),
        ModuleNode("learning.novelty", "感知侧学习", "agent/learning/",
                   type="module", status_source="config:learning.sensor_learning.enabled",
                   description="事件分类→分级→记忆/建议草稿；行为漂移周级检测"),
    ]),
    Domain("cognitive", "认知层", "🧠", [
        ModuleNode("planning.engine", "规划引擎", "planning/core.py",
                   type="service", status_source="config:planning.enabled",
                   metrics=["wire_enabled"],
                   actions=["toggle_planning"],  # 预留：规划开关（S2 需新增接口）
                   description="PlanningCore 任务规划主入口，异常自动回退 LLM"),
        ModuleNode("cognitive.templates", "提示词模板", "cognitive/templates.py",
                   type="module", status_source="api:/api/system-prompt",
                   actions=["save_prompt"],
                   description="系统提示词模板管理"),
        ModuleNode("planning.decomposer", "任务分解/执行", "planning/decomposer.py",
                   type="module", status_source="api:/api/status",
                   description="复杂任务拆解与执行闭环"),
        ModuleNode("planning.persist", "规划持久化", "planning/persistence.py",
                   type="module", status_source="api:/api/status",
                   description="计划 SQLite 持久化"),
    ]),
    Domain("memory", "记忆层", "💾", [
        ModuleNode("memory.manager", "记忆管理器", "memory/memory_manager.py",
                   type="service", status_source="api:/api/memory/overview",
                   metrics=["message_count", "summary_version"],
                   actions=["compress_memory", "add_memory"],
                   description="对话记忆管理、摘要触发、上下文压缩"),
        ModuleNode("memory.context", "上下文组装器", "agent/context/assembler.py",
                   type="module", status_source="api:/api/context/status",
                   actions=["compress_context"],
                   description="三层记忆组装旁路注入，异常静默降级"),
        ModuleNode("knowledge.base", "知识库", "agent/knowledge/",
                   type="service", status_source="api:/api/knowledge/*",
                   description="知识卡片、向量索引、语义搜索、工作流"),
        ModuleNode("lifetrace.tree", "生命周期轨迹", "lifetrace/memory_tree.py",
                   type="module", status_source="api:/api/status",
                   description="长期记忆树与检索"),
    ]),
    Domain("action", "行动层", "🤖", [
        ModuleNode("action.digital_life", "主循环 DigitalLife", "agent/digital_life.py",
                   type="service", status_source="api:/api/status",
                   metrics=["interaction_count", "mode"],
                   actions=["emergency_stop", "emergency_pause", "block_network"],
                   danger="high",
                   description="对话主循环与行动中枢"),
        ModuleNode("action.permission", "权限系统", "agent/permission_system.py",
                   type="service", status_source="api:/api/permission/status",
                   actions=["toggle_permission"],
                   description="黑白名单、危险操作拦截、紧急控制"),
        ModuleNode("action.tools", "工具集", "agent/tools/",
                   type="service", status_source="api:/api/tools/health",
                   metrics=["tool_count"],
                   actions=["toggle_tool"],
                   description="MCP 风格工具注册表（五来源）"),
        ModuleNode("action.skills", "技能管理", "agent/skills_mgmt/",
                   type="service", status_source="api:/api/skills",
                   actions=["toggle_skill"],
                   description="技能加载/启停/参数"),
        ModuleNode("action.extensions", "扩展系统", "agent/extensions/",
                   type="service", status_source="api:/api/extensions/list",
                   actions=["toggle_extension"],
                   description="插件/扩展市场/渠道"),
        ModuleNode("action.scheduler", "定时任务", "agent/task_scheduler.py",
                   type="service", status_source="api:/api/scheduler/tasks",
                   actions=["toggle_scheduler", "execute_scheduler", "delete_scheduler"],
                   description="定时任务调度与心跳"),
        ModuleNode("action.process", "进程管理", "agent/system_tools.py",
                   type="service", status_source="api:/api/process/list",
                   actions=["start_process", "stop_process"],
                   description="白名单进程启动/停止"),
        ModuleNode("action.llm", "LLM 实例", "app_server.py /api/llm/instances",
                   type="service", status_source="api:/api/llm/instances",
                   actions=["reconfigure_llm"],
                   danger="high",
                   description="多 LLM 实例管理，重配会清空会话"),
        ModuleNode("action.mcp", "MCP 服务", "mcp_services/",
                   type="service", status_source="api:/api/mcp/services",
                   description="MCP 服务注册与启用"),
    ]),
    Domain("service", "服务层", "🌐", [
        ModuleNode("service.app", "Web 主服务", "app_server.py",
                   type="service", status_source="api:/api/health",
                   metrics=["overall_health"],
                   description="Flask 主服务 127.0.0.1:5678，287 API + 11 页面"),
        ModuleNode("service.gateway", "API 网关", "agent/api_gateway_flask.py",
                   type="service", status_source="api:/api/health",
                   description="/api/open/* 开放端点、限流、配额、/api/docs"),
        ModuleNode("service.network", "网络配置", "app_server.py /api/network-config",
                   type="config", status_source="api:/api/network-config",
                   actions=["update_network"],
                   description="搜索/网络实例配置与即时生效"),
    ]),
    Domain("ops", "运维层", "🛡", [
        ModuleNode("ops.health", "健康系统", "agent/health/",
                   type="service", status_source="api:/api/health/dashboard",
                   metrics=["overall_health"],
                   description="五层探针→加权评分→趋势"),
        ModuleNode("ops.logs", "日志系统", "agent/log_system/",
                   type="service", status_source="api:/logs/api/stats",
                   metrics=["log_count"],
                   description="多维度采集、分析、内省、Web 看板"),
        ModuleNode("ops.safety", "安全守护", "agent/safety_guard.py",
                   type="service", status_source="api:/api/safety/alerts",
                   actions=["add_keyword"],
                   description="安全关键词检查、告警"),
        ModuleNode("ops.personality", "人格配置", "app_server.py /api/personality",
                   type="config", status_source="api:/api/personality",
                   actions=["set_personality", "reset_personality"],
                   description="人格参数/档案管理"),
        ModuleNode("ops.search_perf", "搜索性能监控", "app_server.py /api/search-performance",
                   type="service", status_source="api:/api/search-performance/status",
                   actions=["start_search_perf", "stop_search_perf"],
                   description="搜索实例性能监控"),
    ]),
]


# ════════════════════════════════════════════════════════════
#  查询辅助（纯函数）
# ════════════════════════════════════════════════════════════

def get_domain(domain_id: str) -> Optional[Domain]:
    """按 domain_id 取域"""
    for d in DOMAINS:
        if d.domain_id == domain_id:
            return d
    return None


def get_node(module_id: str) -> Optional[ModuleNode]:
    """按 module_id 取节点（全树查找）"""
    for d in DOMAINS:
        for n in d.nodes:
            if n.module_id == module_id:
                return n
    return None


def get_action(action: str) -> Optional[ActionRoute]:
    """按动作 key 取映射"""
    return ACTION_ROUTES.get(action)


def node_actions(node: ModuleNode) -> List[Dict]:
    """展开节点可执行动作的完整映射（供前端渲染按钮）"""
    result = []
    for key in node.actions:
        route = ACTION_ROUTES.get(key)
        if route:
            result.append({
                "action": key,
                "method": route.method,
                "url": route.url,
                "params": route.params,
                "danger": route.danger,
                "note": route.note,
            })
    return result


def summary() -> dict:
    """注册表概览（调试/诊断用）"""
    return {
        "domains": len(DOMAINS),
        "nodes": sum(len(d.nodes) for d in DOMAINS),
        "actions": len(ACTION_ROUTES),
    }


if __name__ == "__main__":
    import json
    print(json.dumps({
        "summary": summary(),
        "domains": [
            {"domain_id": d.domain_id, "domain_name": d.domain_name,
             "nodes": [n.module_id for n in d.nodes]}
            for d in DOMAINS
        ],
    }, ensure_ascii=False, indent=2))
