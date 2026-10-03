# plugins/plugin_api.py
from __future__ import annotations
import functools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from flask import Blueprint

@dataclass
class Plugin:
    """插件声明。

    【2026-10-03 · 阶段 4 / R4（审计 H-1/H-6，指标 K2）—— routes 字段已**删除**】
    它原来是一份**手写**路由清单（由人工补齐到 199 条），与 blueprint 上真实的
    @bp.route 装饰器构成**两个事实源**。实测后果（审计原文）：**11 处**
    "插件真实注册了路由，但 manifest.routes 未声明" —— 前端插件面板少显示 11 条路由，
    而没有任何东西会变红。
    现改为 manifest() 从 **app.url_map** 派生（见 plugin_routes），手写清单全部删除：
    这一类漂移**在构造上不可能再发生**（不是"两份保持同步"，而是"只留一份"）。

    【为什么删字段而不是留着不用】留着它就会有人继续填，两个事实源立刻复活
    （本仓对"对齐而非共用"的教训有多次记录）。删除后误传 routes=[...] 会**当场
    TypeError**，是最快的反馈。由 tests/unit/test_plugin_manifest_derivation.py 的
    AST 守卫机械保证它不被加回来。
    """
    name: str
    version: str
    description: str = ""
    schema: Dict[str, Any] = field(default_factory=dict)
    blueprint: Optional[Blueprint] = None
    submit_url: str = ""  # 配置提交端点（T3.3）：空串表示「暂不支持在线修改」
    client_slot: Optional[Dict[str, str]] = None  # 前端动态装载（T4.2）：{slotId, module}

_REGISTRY: List[Plugin] = []

def _validate_schema(name: str, schema: Any) -> None:
    """校验 Plugin.schema（JSON Schema 子集协议，见 docs/yunshu-pluginization/PLAN-3-schema-ui.md §2）。

    - schema 必须为 dict 或 None；
    - 非空 dict 顶层必须声明 type == "object"；
    - 空 dict（默认占位）与 None 视为「未声明配置」，合法；
    - 非法时抛 ValueError（开发期早失败）。
    """
    if schema is None:
        return
    if not isinstance(schema, dict):
        raise ValueError(f"plugin {name}: invalid schema")
    if schema and schema.get("type") != "object":
        raise ValueError(f"plugin {name}: invalid schema")

def register_plugin(plugin: Plugin) -> Plugin:
    _validate_schema(plugin.name, plugin.schema)
    if not any(p.name == plugin.name for p in _REGISTRY):
        _REGISTRY.append(plugin)
    return plugin

def get_plugins() -> List[Plugin]:
    return list(_REGISTRY)


# ════════════════════════════════════════════════════════════
#  上下文窗口读数（**单一口径**，供各 plugin 共用）
# ════════════════════════════════════════════════════════════

def context_limit_info(yunshu) -> Dict[str, Any]:
    """读取**编排窗口上限**及其来源。

    单一事实源是 ``DigitalLife.context_limit_info()``（LifecycleManager 初始化时写入
    ``_memory_token_limit`` / ``_memory_token_limit_source``，见
    agent/orchestrator/lifecycle_manager.py:283-299）。

    【为什么放在这里】2026-10-03 复核发现本逻辑被**抄了三份**：
    plugins/chat.py、plugins/memory.py 各一份（同口径），plugins/status.py 干脆写成
    ``_cfg.get("memory", "token_limit", default=4096)`` —— 第三份会在取不到配置时
    冒出 4096 这个**假分母**，于是同一个占用在不同面板显示不同占比。
    同一口径写三遍必然漂移，故上收到 plugin_api（plugins 的公共叶子模块）。

    【不易】取不到时返回 ``limit_tokens=None`` + ``limit_source="unavailable"``，
    **绝不**回退到 4096 之类的硬编码值 —— 那比没有读数更坏：它看起来像个可信的数，
    然后所有"占比"结论都建在假分母上。
    """
    getter = getattr(yunshu, "context_limit_info", None)
    if callable(getter):
        try:
            info = getter()
        except Exception:  # noqa: BLE001 读数不得炸请求
            info = None
        if isinstance(info, dict):
            limit = info.get("limit_tokens")
            source = info.get("limit_source")
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                return {
                    "limit_tokens": int(limit),
                    "limit_source": (
                        source if isinstance(source, str) and source
                        else "unavailable"
                    ),
                }
    return {"limit_tokens": None, "limit_source": "unavailable"}

# ════════════════════════════════════════════════════════════
#  路由派生（**唯一事实源**：app.url_map）
# ════════════════════════════════════════════════════════════

#: 宿主 Flask app，由 app_server 装配完全部蓝图后绑定一次。
#:
#: 【为什么需要一个绑定点，而不是每次取 current_app】manifest() 有两条调用路径：
#:   ① GET /api/plugins（有请求上下文，current_app 可用）；
#:   ② loader.refresh_manifest()（**无请求上下文** —— reload 时被调用，测试/脚本也会）。
#: 没有绑定点时路径 ② 只能返回空 routes，等于让 reload 后的清单**静默少掉全部路由**。
#:
#: 【为什么派生必须发生在蓝图挂载之后】Flask 3.1.3 实测：Blueprint 在
#: register_blueprint 之前**不持有**规则列表 —— add_url_rule 只记录一个闭包 lambda，
#: 规则字符串在闭包里，没有公开读法。故 bind_app 的时机就是"蓝图装配完成"那一刻。
_BOUND_APP = None


def bind_app(app) -> None:
    """绑定宿主 app（app_server 在蓝图装配完成后调用一次）。"""
    global _BOUND_APP
    _BOUND_APP = app


def unbind_app() -> None:
    """解绑（仅供测试隔离；生产路径不需要）。"""
    global _BOUND_APP
    _BOUND_APP = None


def _resolve_app(app=None):
    """解析派生用的 app：显式入参 > 绑定值 > 当前应用上下文（取不到则 None）。"""
    if app is not None:
        return app
    if _BOUND_APP is not None:
        return _BOUND_APP
    try:
        from flask import current_app
        return current_app._get_current_object()  # 代理对象不能直接 iter_rules
    except Exception:  # noqa: BLE001 无应用/请求上下文不是错误，是"派生不可用"
        return None


def plugin_routes(plugin: Plugin, app) -> List[str]:
    """派生某插件蓝图在 app.url_map 中注册的**全部**路径（去重、排序）。

    app 为 None（无上下文且未绑定）时返回空列表 —— 调用方可用 manifest 里的
    routes_source 区分"确实没有路由"与"派生不可用"，**不要静默当成 0 条**。
    """
    bp = getattr(plugin, "blueprint", None)
    if bp is None or app is None:
        return []
    prefix = bp.name + "."
    paths = set()
    for rule in app.url_map.iter_rules():
        if str(rule.endpoint).startswith(prefix):
            paths.add(str(rule.rule))
    return sorted(paths)


def manifest(app=None) -> Dict[str, Any]:
    """插件清单（GET /api/plugins 的响应体）。

    【routes 是**派生值**】取自 app.url_map，不再读任何手写清单 —— 这是 K2
    （"插件 manifest routes 与真实规则双向一致率 → 100%"）的实现方式。
    """
    import sys, flask
    _app = _resolve_app(app)
    source = "url_map" if _app is not None else "unavailable"
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "schema": p.schema or {},  # 统一约定：无 schema 输出为空 dict
                "submit_url": p.submit_url,  # 配置提交端点（T3.3）；空串 = 不支持在线修改
                "client_slot": p.client_slot,  # 前端动态装载（T4.2）；None = 无客户端模块
                "routes": plugin_routes(p, _app),
                # 【为什么把来源写进契约】"routes 为空"有两种截然不同的原因：
                #   ① 该插件确实没有路由；② 当前拿不到 app（派生不可用）。
                #   不区分就会重演本仓反复出现的"看起来正常、实际没数据"。
                "routes_source": source,
            }
            for p in _REGISTRY
        ],
        "host": {"python": sys.version.split()[0], "flask": flask.__version__},
    }


# ════════════════════════════════════════════════════════════
#  插件统一鉴权装饰器（2026-10-03 · 审计 M-40 的收口点）
# ════════════════════════════════════════════════════════════
def _resolve_host_decorator(name):
    """解析宿主装饰器：优先 app_server，**回退 agent.server_auth**。

    【为什么必须回退 —— 2026-10-03 迁移时实测踩到】各插件原来的懒包装一律写
    `from app_server import require_token`，那**只在 app_server 已加载时才成立**。
    而 plugins/status.py 的原实现刻意 import `agent.server_auth`（其 docstring 写明
    「避免循环导入」）—— 因为 tests/test_plugin_submit_url.py 这类用例会**单独建 Flask app
    并注册 status 蓝图**，根本不导入 app_server；此时 `from app_server import ...` 抛
    ImportError: cannot import name 'require_token' from '<unknown module name>'，
    2 条用例当场变红。

    【为什么回退是等价安全的】`agent.server_auth` 里的 require_token / log_request 是
    **规范实现**；app_server.py:661 那份是历史副本，两者语义一致（都走运行期判定 +
    token_equal 按字节比较），已在迁移评估中逐行核对。
    """
    try:
        import app_server
        fn = getattr(app_server, name, None)
        if fn is not None:
            return fn
    except Exception:  # noqa: BLE001 app_server 未加载 / 导入期副作用失败都不致命
        pass
    from agent import server_auth
    return getattr(server_auth, name)


def _lazy_wrap(f, build):
    """占位包装器：每次调用时用延迟解析出的真实装饰器包装 f 后执行。"""
    @functools.wraps(f)
    def _wrapped(*args, **kwargs):
        return build(f)(*args, **kwargs)
    return _wrapped


def log_request(*args, **kwargs):
    """插件用**统一**的延迟版 @log_request(...)（app_server 共享装饰器）。

    【为什么也上收】与 require_auth 同因：chat/mcp_scheduler/safety/skills 各自抄了一份
    完全相同的实现（审计 M-40）。两者共用同一个 _lazy_wrap，分开留会立刻再分叉。
    """
    def _decorator(f):
        def _build(fn):
            return _resolve_host_decorator("log_request")(*args, **kwargs)(fn)
        return _lazy_wrap(f, _build)
    return _decorator


def require_auth(f):
    """插件用**统一**鉴权装饰器：延迟解析 app_server 的 require_token。

    【为什么上收到这里】审计 M-40 实测：plugins/{chat,mcp_scheduler,safety,skills,status}.py
    各自抄了一份 _lazy_wrap + _require_token（共 5 份定义、64 处使用）。改一处不会同步其余
    四处 —— 本仓已有同类前科（agent/logging_utils.py 与 utils/sensitive_data_filter.py 的
    两份掩码规则「对齐而非共用」；以及那次 require_token 空装饰器 fail-open）。
    plugin_api 是 plugins 的公共叶子模块，其自身注释早已记录过同一教训
    （context_limit_info 被抄三份导致「同一个占用在不同面板显示不同占比」），把鉴权也上收到此。

    【为什么延迟解析】PLAN-1 §4 红线：插件模块顶层不得 import app_server（循环导入）。
    故直到**首次请求**才从 app_server 取真实装饰器；导入期零副作用。

    【与既有 5 份的关系】本函数是**新代码与示范插件的标准写法**；既有 5 份属遗留，
    迁移应逐插件进行（它们各自有 4~23 处在用），不做一次性替换以免放大回归面。

    用法::

        from .plugin_api import require_auth

        @bp.route("/api/x", methods=["POST"])
        @require_auth
        def api_x():
            ...
    """
    def _build(fn):
        return _resolve_host_decorator("require_token")(fn)
    wrapped = _lazy_wrap(f, _build)
    # 【标记：本视图受令牌保护】口径见 agent.server_auth.REQUIRES_TOKEN_ATTR。
    #   【为什么必须在这里手工打标，不能靠 wraps 传递】本装饰器是**延迟**的：
    #   _lazy_wrap 返回的 _wrapped 才是挂在 app.url_map 上的视图函数，而真正的
    #   require_token 要到**首次请求**才在 _build 里解析出来。故 wraps 传播链
    #   在导入期根本不存在 —— 不打标则所有插件路由都会被判为"未受保护"，
    #   find_shadowed_exemptions 对 plugins/ 整片失明。
    #   由于标记是**声明性**的（这里声明"本路由受令牌保护"，与运行期解析结果一致，
    #   二者由 _resolve_host_decorator 的回退链保证同源），此处打标是准确的而非臆测。
    from agent.server_auth import REQUIRES_TOKEN_ATTR as _RTA
    setattr(wrapped, _RTA, True)
    return wrapped
