"""统一路由注册入口

将所有按业务域拆分的路由模块集中注册到 Flask 应用。
每个模块的 register_routes(app, state) 接受 Flask app 和 ServerState 实例。
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
