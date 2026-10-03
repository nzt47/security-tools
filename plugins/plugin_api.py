# plugins/plugin_api.py
from __future__ import annotations
import functools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from flask import Blueprint

@dataclass
class Plugin:
    name: str
    version: str
    description: str = ""
    schema: Dict[str, Any] = field(default_factory=dict)
    blueprint: Optional[Blueprint] = None
    routes: List[str] = field(default_factory=list)
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

def manifest() -> Dict[str, Any]:
    import sys, flask
    return {
        "plugins": [
            {
                "name": p.name,
                "version": p.version,
                "description": p.description,
                "schema": p.schema or {},  # 统一约定：无 schema 输出为空 dict
                "submit_url": p.submit_url,  # 配置提交端点（T3.3）；空串 = 不支持在线修改
                "client_slot": p.client_slot,  # 前端动态装载（T4.2）；None = 无客户端模块
                "routes": sorted(p.routes),
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
