"""统一路由注册入口

将所有按业务域拆分的路由模块集中注册到 Flask 应用。
每个模块的 register_routes(app, state) 接受 Flask app 和 ServerState 实例。

⚠️ 重要：**本函数当前没有调用方（死代码）**
------------------------------------------------------------------
真实的路由注册发生在 `app_server.py`，那里**逐个模块显式注册**（大量
`from agent.server_routes.routes_x import register_routes as reg_x` + try/except 块）。

历史教训：`routes_approval` 与 `routes_agent_lines` 都曾**只在本文件登记**，
结果生产环境从未注册，前端表现为 HTTP 404。而单测里手工 `Flask(__name__)`
再调 `register_routes` 会通过 ⇒ **测试绿、线上 404**，非常难查。

⇒ 新增路由模块的正确做法：在 `app_server.py` 里照抄一个既有 try/except 块显式注册。
   只改本文件不会生效。
"""


def register_all_routes(app, state):
    """注册所有业务域路由到 Flask 应用

    Args:
        app: Flask 应用实例
        state: ServerState 全局状态容器（含 Yunshu, session_mgr, safety_guard 等）
    """
    # 对话 & 语音 & Web 工具
    from .routes_chat import register_routes as reg_chat
    reg_chat(app, state)

    # 会话管理
    from .routes_sessions import register_routes as reg_sessions
    reg_sessions(app, state)

    # 全景 & 健康 & 状态
    from .routes_panorama import register_routes as reg_panorama
    reg_panorama(app, state)

    # 配置 & 网络 & LLM & MCP
    from .routes_config import register_routes as reg_config
    reg_config(app, state)

    # 技能 & 工具
    from .routes_skills import register_routes as reg_skills
    reg_skills(app, state)

    # 人格配置
    from .routes_personality import register_routes as reg_personality
    reg_personality(app, state)

    # 权限 & 安全
    from .routes_permission import register_routes as reg_permission
    reg_permission(app, state)

    # 记忆 & 窗口 & 隐私
    from .routes_memory import register_routes as reg_memory
    reg_memory(app, state)

    # 知识库检索（任务4：融合检索 + 双链扩展 + rerank）
    from .routes_knowledge import register_routes as reg_knowledge
    reg_knowledge(app, state)

    # 工作区 & 系统工具
    from .routes_workspace import register_routes as reg_workspace
    reg_workspace(app, state)

    # 心跳 & 调度器 & 性能监控 & 测试
    from .routes_monitoring import register_routes as reg_monitoring
    reg_monitoring(app, state)

    # 扩展系统（原有，保持向后兼容）
    from .extensions import register_routes as reg_extensions
    reg_extensions(app, state)

    # 系统身份提示词配置（组件级开关 + 参数配置）
    from .routes_system_prompt import register_routes as reg_system_prompt
    reg_system_prompt(app, state)

    # 分身管理（P4 Subagent Lifecycle）
    from .routes_subagent import register_routes as reg_subagent
    reg_subagent(app, state)

    # 综合技能管理系统 (skills_mgmt v1)
    from .routes_skills_mgmt import register_routes as reg_skills_mgmt
    reg_skills_mgmt(app, state)

    # 智能工作流学习系统 (workflow_learning v1)
    from .routes_workflow_learning import register_routes as reg_workflow_learning
    reg_workflow_learning(app, state)

    # 审批 HTTP 面（TASK-S4-01：§5.7⑦ 审批面安全 + §7.0 Actor 矩阵）
    from .routes_approval import register_routes as reg_approval
    reg_approval(app, state)

    # 治理可观测六面板 HTTP 面（TASK-S6-01：§7 六面板 + 七动作；只读为主）
    from .routes_ui_panels import register_routes as reg_ui_panels
    reg_ui_panels(app, state)

    # 主线管理（能力平面档案 CRUD + 装配预览；数据源 data/agent_lines/*.yaml）
    from .routes_agent_lines import register_routes as reg_agent_lines
    reg_agent_lines(app, state)
