"""云枢 Web 应用 — 感知底座 + 数字生命对话

整合 BodySensor 仪表盘和 DigitalLife 聊天界面，
提供完整的可视化交互体验。

启动:
    python app_server.py
    访问 http://127.0.0.1:5678
    
Prometheus 监控:
    访问 http://127.0.0.1:5678/metrics 获取监控指标
"""

import os
import json
import logging
import platform
import webbrowser
import datetime
import uuid
import functools
import secrets
import concurrent.futures
import time
import sys
import urllib.request as _ur
import urllib.parse as _up
import json as _js
import requests as _http  # 注意：Flask 的 request 对象会覆盖 requests 模块，用 _http 别名

# 插件机制（T1.1）：协议层 + 示例插件（注册表见 plugins/plugin_api.py）
from plugins.plugin_api import get_plugins, manifest as plugin_manifest
from plugins import example  # noqa: F401

# 修复 Windows 控制台编码，避免中文日志乱码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 加载 .env 到 os.environ（守 user_rules「配置走 .env」单一数据源）
# Why: main.py 已按此模式加载；app_server 若不加载，LLM 密钥 / HF_HUB_OFFLINE /
#      LOG_LEVEL / CONTEXT_ASSEMBLER_LOG_LEVEL 等 .env 配置全部失效。
#      必须在读取环境变量的模块级代码之前执行（reload 覆盖同名变量为 .env 值）。
try:
    from agent.env_config_manager import get_env_config_manager
    get_env_config_manager().reload()
except Exception as _e:
    logging.getLogger(__name__).warning(f".env 加载失败（继续使用系统环境变量）: {_e}")

from flask import Flask, jsonify, render_template, request, g

# 导入 Prometheus 监控（使用 prometheus_flask_exporter）
try:
    from prometheus_flask_exporter import PrometheusMetrics, Counter, Histogram, Gauge
    from prometheus_flask_exporter.multiprocess import GunicornPrometheusMetrics
    
    # 自定义指标
    PROMETHEUS_AVAILABLE = True
    try:
        print("[OK] Prometheus Flask Exporter import success")
    except:
        pass
except ImportError:
    print("[WARN] Prometheus Flask Exporter not installed")
    PROMETHEUS_AVAILABLE = False

# 安全守护 + 系统工具
from agent.safety_guard import SafetyGuard, register_alert_callback
from agent.task_scheduler import (
    get_scheduler,
    perform_heartbeat_check,
)
from agent.tools import list_tools
from agent.system_tools import (
    init_workspace,
    WORKSPACE_DIR,
)
from agent.web import HttpClient, Scraper, SearchEngine, DataProcessor, CrawlerController
from agent.session_manager import SessionManager
from agent.log_system.dashboard import register_log_system

logging.basicConfig(level=logging.INFO, encoding="utf-8", force=True)
logger = logging.getLogger(__name__)

# 启用结构化日志易读格式（控制台显示优化，不影响 JSON 原始内容）
try:
    from scripts.struct_log_formatter import setup_readable_logging
    setup_readable_logging()
except Exception as _e:
    logger.debug(f"结构化日志格式化器加载失败（不影响功能）: {_e}")


app = Flask(__name__, static_url_path='/static-assets')
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')

# 注册日志系统蓝图（/logs/dashboard 页面 + REST API）
try:
    register_log_system(app)
    logger.info("[启动] 日志系统仪表盘与 API 路由已注册")
except Exception as e:
    logger.warning(f"[启动] 日志系统注册失败: {e}")

# 注册健康看板蓝图（/api/health/dashboard、/api/health/probe-trend）
try:
    from agent.health.dashboard import health_bp
    app.register_blueprint(health_bp)
    logger.info("[启动] 健康看板 API 路由已注册 (/api/health/*)")
except Exception as e:
    logger.warning(f"[启动] 健康看板注册失败: {e}")

# 注册学习度量蓝图（TASK-03: /api/learning/metrics 只读 KPI 查询）
try:
    from agent.learning_metrics_api import learning_metrics_bp
    app.register_blueprint(learning_metrics_bp)
    logger.info("[启动] 学习度量 API 路由已注册 (/api/learning/metrics)")
except Exception as e:
    logger.warning(f"[启动] 学习度量注册失败: {e}")

# 注册全部插件 blueprint（插件化机制 T1.1：装配器骨架）
for _p in get_plugins():
    if _p.blueprint is not None:
        app.register_blueprint(_p.blueprint)
logger.info(f"[启动] 插件蓝图注册完成（共 {len(get_plugins())} 个插件）")

# 注册模块聚合蓝图（S2: /api/modules/topology + <id>/detail + <id>/actions）
# 说明: provider 用模块级 def 延迟解析 _Yunshu（_Yunshu 在文件后部初始化），
#       注册动作本身不执行采集，运行时才调用，避免注册时序依赖。
try:
    from agent.modules_api import register_modules_api, register_status_provider

    def _provider_sensors():
        try:
            return _Yunshu.body.get_sensor_info()
        except Exception as _e:  # noqa: BLE001 - 采集失败降级为离线
            return None

    def _provider_status():
        try:
            return _Yunshu.get_status()
        except Exception as _e:  # noqa: BLE001 - 采集失败降级为离线
            return None

    def _provider_panorama():
        """全景指标（CPU/内存/电池/sensor_on 等），补全拓扑节点指标 chip"""
        try:
            from plugins.status import api_panorama  # T1.4：全景路由已迁移至 status 插件
            resp = api_panorama()
            data = resp.get_json() or {}
            out = {"sensor_on": data.get("sensor_on"), "sensor_total": data.get("sensor_total")}
            for reading in data.get("health", []) or []:
                name = reading.get("sensor_name")
                if name and reading.get("value") is not None:
                    out[name] = reading["value"]
            return out
        except Exception as _e:  # noqa: BLE001 - 采集失败降级为离线
            return None

    register_status_provider("/api/sensors", _provider_sensors)
    register_status_provider("/api/status", _provider_status)
    register_status_provider("/api/panorama", _provider_panorama)
    register_modules_api(app, api_token_provider=lambda: _API_TOKEN if _API_TOKEN_ENABLED else None)
    logger.info("[启动] 模块聚合 API 路由已注册 (/api/modules/*)")
except Exception as e:
    logger.warning(f"[启动] 模块聚合 API 注册失败: {e}")

# ════════════════════════════════════════════════════════════
# Prometheus 监控初始化
# ════════════════════════════════════════════════════════════

if PROMETHEUS_AVAILABLE:
    # 初始化 Prometheus 监控
    metrics = PrometheusMetrics(
        app,
        defaults_prefix='yunshu',
        group_by='endpoint'  # 按端点分组统计
    )
    
    # 获取默认 REGISTRY（用于 generate_latest）
    from prometheus_client import REGISTRY as DEFAULT_REGISTRY
    
    # 注册自定义指标
    # 安全拦截计数器
    SECURITY_BLOCKS = Counter(
        'yunshu_security_blocks_total',
        'Total number of security blocks',
        ['rule', 'level', 'category']
    )
    
    # LLM 调用计数器
    LLM_CALLS = Counter(
        'yunshu_llm_calls_total',
        'Total number of LLM calls',
        ['provider', 'model', 'status']
    )
    
    # 用户登录次数
    USER_LOGINS = Counter(
        'yunshu_user_logins_total',
        'Total number of user logins',
        ['user_id', 'auth_method']
    )
    
    # API 调用频率
    API_CALLS = Counter(
        'yunshu_api_calls_total',
        'Total number of API calls by endpoint',
        ['endpoint', 'method', 'user_id']
    )
    
    # 对话次数
    CONVERSATIONS = Counter(
        'yunshu_conversations_total',
        'Total number of conversations',
        ['status']
    )
    
    # 工具调用次数
    TOOL_CALLS = Counter(
        'yunshu_tool_calls_total',
        'Total number of tool calls',
        ['tool_name', 'status']
    )
    
    # 系统资源指标
    CPU_USAGE = Gauge(
        'yunshu_cpu_usage_percent',
        'CPU usage percentage'
    )
    
    MEMORY_USAGE = Gauge(
        'yunshu_memory_usage_percent',
        'Memory usage percentage'
    )
    
    # 活跃连接数
    ACTIVE_CONNECTIONS = Gauge(
        'yunshu_active_connections',
        'Number of active connections'
    )
    
    print("[OK] Prometheus monitoring initialized")
    print("   Metrics endpoint: http://127.0.0.1:5678/metrics")
else:
    SECURITY_BLOCKS = None
    LLM_CALLS = None
    USER_LOGINS = None
    API_CALLS = None
    CONVERSATIONS = None
    TOOL_CALLS = None
    CPU_USAGE = None
    MEMORY_USAGE = None
    ACTIVE_CONNECTIONS = None

# 禁用浏览器缓存（确保 HTML/CSS/JS 始终最新）
@app.after_request
def _no_cache(response):
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate, max-age=0'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

# ── API 认证令牌 ──
# 从环境变量 FLASK_API_TOKEN 加载，若未设置则自动生成一个随机令牌
# 所有危险操作 API 需要携带 Authorization: Bearer <token> 或 X-API-Token: <token>
_API_TOKEN = os.environ.get("FLASK_API_TOKEN", "")
_API_TOKEN_ENABLED = bool(_API_TOKEN)
if _API_TOKEN_ENABLED:
    logger.info("API 令牌认证已启用")
else:
    logger.info("API 令牌认证未启用（设置 FLASK_API_TOKEN 环境变量以启用）")

def require_token(f):
    """需要 API 令牌认证的装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _API_TOKEN_ENABLED:
            return f(*args, **kwargs)
        # 从请求头中提取令牌
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.headers.get("X-API-Token", "")
        if not token or not secrets.compare_digest(token, _API_TOKEN):
            return jsonify({"error": "未授权：缺少或无效的 API 令牌"}), 401
        return f(*args, **kwargs)
    return decorated

def log_request(show_body=True, show_response=True):
    """接口日志装饰器 - 记录请求和响应的详细信息
    
    Args:
        show_body: 是否显示请求体
        show_response: 是否显示响应内容（大型响应可设为False）
    """
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            import time
            start_time = time.time()
            endpoint = f.__name__
            
            logs = []
            logs.append(f"[REQUEST] 接口: {endpoint}")
            logs.append(f"[REQUEST] 方法: {request.method}")
            logs.append(f"[REQUEST] 路径: {request.path}")
            logs.append(f"[REQUEST] 查询参数: {dict(request.args)}")
            
            if show_body and request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = request.get_json() if request.is_json else request.form.to_dict()
                    body_str = str(body)[:200] + ('...' if len(str(body)) > 200 else '')
                    logs.append(f"[REQUEST] 请求体: {body_str}")
                except Exception:
                    logs.append(f"[REQUEST] 请求体: 无法解析")
            
            # 执行原始函数
            try:
                response = f(*args, **kwargs)
                response_time = (time.time() - start_time) * 1000
                
                logs.append(f"[RESPONSE] 状态码: {response[1] if isinstance(response, tuple) else 200}")
                logs.append(f"[RESPONSE] 耗时: {response_time:.2f}ms")
                
                if show_response:
                    if isinstance(response, tuple) and len(response) > 0:
                        resp_data = response[0].get_json() if hasattr(response[0], 'get_json') else str(response[0])[:200]
                    else:
                        resp_data = response.get_json() if hasattr(response, 'get_json') else str(response)[:200]
                    logs.append(f"[RESPONSE] 内容: {resp_data}")
                
                success = True
                
            except Exception as e:
                import traceback as tb
                response_time = (time.time() - start_time) * 1000
                logs.append(f"[ERROR] 异常: {type(e).__name__} - {str(e)[:200]}")
                logs.append(f"[ERROR] 耗时: {response_time:.2f}ms")
                
                # 捕获堆栈信息到日志
                stack_trace = tb.format_exc()
                logs.append(f"[STACK TRACE] {stack_trace[:500]}")
                
                success = False
                
                # 打印异常日志到控制台
                print("\n" + "="*60)
                print(f"❌ API 请求异常 [{endpoint}]")
                print("-"*60)
                for log in logs:
                    print(log)
                print("="*60 + "\n")
                
                raise
            
            finally:
                # 打印成功日志到控制台
                if success:
                    print("\n" + "="*60)
                    print(f"📡 API 请求日志 [{endpoint}]")
                    print("-"*60)
                    for log in logs:
                        print(log)
                    print("="*60 + "\n")
            
            return response
        return decorated
    return decorator


# ── 多会话管理器（保留 _CHAT_HISTORY 作为向后兼容的缓存） ──
_session_mgr = SessionManager(sessions_dir="./data/sessions")

# 用于全景视图等旧功能的向后兼容缓存
_CHAT_HISTORY = []


def _ensure_default_session():
    """确保至少有一个会话存在（启动时自动创建默认会话）"""
    sessions = _session_mgr.list_sessions()
    if not sessions:
        default = _session_mgr.create_session("默认会话")
        logger.info("✅ 已创建默认会话: %s", default["id"])
    else:
        _session_mgr.set_current(sessions[0]["id"])
        logger.info("✅ 当前会话: %s (%s)", sessions[0]["id"], sessions[0]["title"])


def _get_current_session_id():
    """获取当前会话 ID，如无则创建新会话"""
    session_id = _session_mgr.get_current_id()
    if not session_id:
        session = _session_mgr.create_session("新会话")
        session_id = session["id"]
    return session_id


MEMORY_DIR = os.path.join(WORKSPACE_DIR, "云枢记忆")
os.makedirs(MEMORY_DIR, exist_ok=True)


def _save_conversation_record(user_input, response, mode="normal", health_data=None):
    """自动保存对话记录到云枢记忆目录"""
    import datetime as dt
    now = dt.datetime.now()
    date_str = now.strftime("%Y%m%d")

    # 查找当日已有记录数
    prefix = os.path.join(MEMORY_DIR, f"会话记录_{date_str}")
    seq = 0
    try:
        for f in os.listdir(MEMORY_DIR):
            if f.startswith(f"会话记录_{date_str}") and f.endswith(".txt"):
                seq += 1
    except OSError:
        pass
    seq += 1

    filename = f"会话记录_{date_str}_{seq:03d}.txt"
    filepath = os.path.join(MEMORY_DIR, filename)

    health_lines = []
    if health_data:
        for h in health_data[:6]:
            name = h.get("description", h.get("sensor_name", "?"))
            value = h.get("severity", "normal")
            icon = "🟢" if value == "normal" else "🟡" if value == "warning" else "🔴"
            health_lines.append(f"🔹 {name}：{icon} {value}")

    record = (
        "=" * 45 + "\n" +
        f"  会话记录 #{seq}\n" +
        "=" * 45 + "\n\n" +
        f"🕒 时间：{now.year}年{now.month}月{now.day}日 {now.strftime('%H:%M')}\n" +
        f"📋 模式：{mode}\n\n" +
        "---\n\n" +
        "💬 【对话内容】\n\n" +
        f"👤 用户：\n{user_input.strip()}\n\n" +
        f"🤖 云枢：\n{response.strip()}\n\n"
    )
    if health_lines:
        record += "---\n\n📊 【身体状态】\n\n" + "\n".join(health_lines) + "\n\n"

    record += "— 云枢 🤖 于 " + now.strftime("%Y.%m.%d %H:%M") + "\n"
    record += "=" * 45 + "\n\n"

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(record)
        logger.info("📝 对话记录已保存: %s", filename)
    except OSError as e:
        logger.error("❌ 保存对话记录失败: %s", e)


# ── 初始化 DigitalLife ──
from config import Config
from agent import DigitalLife

_cfg = Config()
_Yunshu = DigitalLife(_cfg.merged)
_Yunshu.start()

# 知识库卡片存储接线（任务6）：知识库 API 路由的 CardStore 事实源。
# 默认布局 knowledge/wiki（AGENTS.md 契约）；wiki 目录缺失时 CardStore 写入自动建目录。
try:
    from agent.knowledge.card import CardStore
    _Yunshu._card_store = CardStore("knowledge/wiki")
    print("[启动] 知识库 CardStore 已接线: knowledge/wiki")
except Exception as _kb_e:
    print(f"[启动] 知识库 CardStore 接线失败: {_kb_e}")
    _Yunshu._card_store = None

# 知识库 API 路由注册（任务6）：/api/knowledge/*（CRUD + index + lint + graph + query）
try:
    from types import SimpleNamespace
    from agent.server_routes.routes_knowledge import register_routes as reg_knowledge
    _kb_state = SimpleNamespace(Yunshu=_Yunshu)
    reg_knowledge(app, _kb_state)
    print("[启动] 知识库 API 路由已注册: /api/knowledge/*")
except Exception as _kb_r:
    print(f"[启动] 知识库 API 路由注册失败: {_kb_r}")
    import traceback
    traceback.print_exc()

# 从网络配置文件加载 LLM 配置（修复 Web 界面配置 LLM 重启后不生效的问题）
print("[启动] 开始加载网络配置...")
try:
    from agent.network_config import NetworkConfigManager as _NCM
    print("[启动] 成功导入 NetworkConfigManager")
    # 【P2 已清理】SecureConfigManager 已移除，敏感数据统一由 .env 单一数据源管理
    _ncm = _NCM()
    print("[启动] 已创建配置管理器（纯 .env 架构）")

    print("[启动] 调用 apply_to_app...")
    _ncm.apply_to_app(_Yunshu)
    print("[启动] 网络配置应用完成")
except Exception as _e:
    print(f"[启动] 加载网络配置失败: {_e}")
    import traceback
    traceback.print_exc()

# 确保默认会话存在
_ensure_default_session()

# 验证工具注册
from agent import tools as _agent_tools
_agent_tools_count = len(_agent_tools.list_tools())
logger.info("云枢工具系统初始化完成: %d 个工具已就绪", _agent_tools_count)

# 初始化窗口传感器（默认禁用，需要用户同意）
_window_sensor = None
_window_sensor_consented = False  # 用户是否同意过

def _init_window_sensor():
    """根据配置初始化窗口传感器（需用户同意，默认禁用）

    YUNSHU_DISABLE_WINDOW_SENSOR=1/true 时跳过导入（开发/沙箱环境屏蔽开关，
    规避受限环境访问系统语音词库等路径的噪音；屏蔽后窗口监控功能不可用）。
    """
    global _window_sensor
    if os.environ.get("YUNSHU_DISABLE_WINDOW_SENSOR", "").strip().lower() in ("1", "true", "yes"):
        _window_sensor = None
        logger.info("窗口监控传感器已跳过（YUNSHU_DISABLE_WINDOW_SENSOR 屏蔽）")
        return
    try:
        from sensor.window_sensor import WindowSensor
        ws = WindowSensor(
            config_path="data/window_config.json",
            save_callback=lambda event_type, data: _Yunshu._memory.save_log(event_type, data)
        )
        # 强制禁用 —— 必须通过 /api/window/consent 端点经用户同意才能启用
        config = ws.get_config()
        config["enabled"] = False
        ws.save_config(config)
        _window_sensor = ws
        logger.info("窗口监控传感器已初始化（默认禁用，需用户同意后启用）")
    except Exception as e:
        logger.warning(f"窗口监控传感器初始化失败: {e}")
        _window_sensor = None

_init_window_sensor()

# 初始化安全守护
_safety_guard = SafetyGuard()
logger.info("安全守护模块已加载")

# ── 初始化 Web 工具模块 ──
_web_http = HttpClient({"timeout": 30, "max_retries": 3, "backoff_factor": 0.5})
_web_scraper = Scraper(_web_http)
_web_search = SearchEngine()
_web_search.set_http_client(_web_http)
_web_processor = DataProcessor()
_web_crawler = CrawlerController({"default_delay": 1.0})
logger.info("Web 工具模块已初始化")

# 让 DigitalLife 复用全局搜索引擎（避免延迟初始化后缺少搜索实例注册）
_Yunshu._web_search = _web_search

# 告警通知回调：将告警存入内存队列供前端轮询
_alert_queue = []  # 最多保留 100 条
_MAX_ALERT_QUEUE = 100

def _on_safety_alert(alert):
    _alert_queue.append(alert)
    if len(_alert_queue) > _MAX_ALERT_QUEUE:
        _alert_queue.pop(0)

register_alert_callback(_on_safety_alert)

# 初始化工作区
_workspace_path = init_workspace()
logger.info(f"受保护工作区: {_workspace_path}")

# ── 人格配置管理器（已迁移至 plugins/status.py，任务 T1.4；仅该域使用）──

# ── 工具状态持久化（已迁移至 plugins/skills.py，任务 T1.5；仅本域使用）──

# ── 技能配置管理器 ──
_SKILLS_FILE = os.path.join(os.path.dirname(__file__), 'data', 'skills.json')

class SkillsManager:
    """管理云枢的技能配置"""

    def _load(self) -> dict:
        try:
            with open(_SKILLS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {"skills": []}

    def _save(self, data: dict):
        with open(_SKILLS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def get_all(self) -> list:
        return self._load().get("skills", [])

    def toggle(self, skill_id: str) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["enabled"] = not s.get("enabled", True)
                self._save(data)
                return {"ok": True, "id": skill_id, "enabled": s["enabled"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def update_params(self, skill_id: str, params: dict) -> dict:
        data = self._load()
        for s in data["skills"]:
            if s["id"] == skill_id:
                s["params"].update(params)
                self._save(data)
                return {"ok": True, "id": skill_id, "params": s["params"]}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

    def add(self, skill: dict) -> dict:
        data = self._load()
        skill_id = skill.get("id", "")
        if any(s["id"] == skill_id for s in data["skills"]):
            return {"ok": False, "error": f"技能已存在: {skill_id}"}
        data["skills"].append({
            "id": skill_id,
            "name": skill.get("name", skill_id),
            "enabled": skill.get("enabled", True),
            "description": skill.get("description", ""),
            "params": skill.get("params", {}),
        })
        self._save(data)
        return {"ok": True, "id": skill_id}

    def delete(self, skill_id: str) -> dict:
        data = self._load()
        before = len(data["skills"])
        data["skills"] = [s for s in data["skills"] if s["id"] != skill_id]
        if len(data["skills"]) < before:
            self._save(data)
            return {"ok": True}
        return {"ok": False, "error": f"未知技能: {skill_id}"}

_skills_mgr = SkillsManager()


# ════════════════════════════════════════════════════════════
#  API 路由
# ════════════════════════════════════════════════════════════
# 健康/传感器/状态/模式/规划/认知/全景/人格/心跳 路由已迁移至 plugins/status.py（任务 T1.4）

@app.route("/api/plugins", methods=["GET"])
def api_plugins():
    """插件元信息 manifest（插件化机制 T1.1）"""
    return jsonify(plugin_manifest())


# [security] DeepSeek API key 改为从环境变量读取，避免硬编码泄露
# 本地配置：在 .env 文件中设置 DEEPSEEK_API_KEY=sk-xxx
# 缺省时 _DS_KEY 为空字符串，/api/news 的 DeepSeek 翻译功能将不可用
_DS_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
_DS_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions")
if not _DS_KEY:
    logger.warning("DEEPSEEK_API_KEY 未设置，/api/news 接口的 DeepSeek 翻译功能不可用")


# ════════════════════════════════════════════════════════════
#  上下文监视器 API
# ════════════════════════════════════════════════════════════

_token_counter_imported = None
def _get_token_counter():
    global _token_counter_imported
    if _token_counter_imported is None:
        from memory.token_counter import TokenCounter
        _token_counter_imported = TokenCounter()
    return _token_counter_imported


# ════════════════════════════════════════════════════════════
#  全景 API / 人格配置 API（已迁移至 plugins/status.py，任务 T1.4）
# ════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════
#  系统提示词配置管理（组件级开关 + 参数配置）
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_system_prompt import register_routes as reg_system_prompt_config
    reg_system_prompt_config(app, lambda: None)  # state 不需要，用 lambda 代替
except Exception as e:
    logger.error("加载系统提示词配置路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  LLM 通信监控（收发看板）
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_llm_monitor import register_routes as reg_llm_monitor
    reg_llm_monitor(app, lambda: None)
    # 安装 LLM 调用拦截钩子
    from agent.llm_monitor import install_hooks
    install_hooks()
    logger.info("LLM 通信监控已启动")
except Exception as e:
    logger.error("加载 LLM 监控路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  技能管理系统 v1 路由（/api/skills-mgmt/*）
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_skills_mgmt import register_routes as reg_skills_mgmt
    reg_skills_mgmt(app, lambda: None)
    logger.info("技能管理系统路由已注册 (/api/skills-mgmt/*)")
except Exception as e:
    logger.error("加载技能管理路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  工作流学习系统路由（/api/workflow-learning/*）
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_workflow_learning import register_routes as reg_workflow_learning
    reg_workflow_learning(app, lambda: None)
    logger.info("工作流学习系统路由已注册 (/api/workflow-learning/*)")
except Exception as e:
    logger.error("加载工作流学习路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  运行时诊断路由（可观测性 E2E 测试所需的 7 个诊断端点）
#  包含：/api/diagnostics/health、/api/diagnostics/trace、
#        /api/diagnostics/trace/inject、/api/diagnostics/metrics、
#        /api/diagnostics/logs、/api/observability/state、
#        /api/diagnostics/tools
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_logging import register_routes as reg_logging

    # 注意：不要移除 PrometheusMetrics 已注册的 /metrics 规则（endpoint: prometheus_metrics）。
    # routes_logging 也会注册 /metrics（endpoint: api_prometheus_metrics），但 werkzeug 按
    # 规则添加顺序匹配，先注册的 PrometheusMetrics 规则会优先匹配，使用默认 REGISTRY，
    # 返回 200。routes_logging 的 /metrics 规则不会被命中，仅作为备用存在。
    reg_logging(app, lambda: None)
    logger.info("运行时诊断路由注册成功 (/api/diagnostics/*, /api/observability/*)")
except Exception as e:
    logger.error("加载运行时诊断路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  分身管理 / 资产管理路由
# ════════════════════════════════════════════════════════════

try:
    from types import SimpleNamespace
    from agent.server_routes.routes_subagent import register_routes as reg_subagent
    _subagent_state = SimpleNamespace(Yunshu=_Yunshu)
    reg_subagent(app, _subagent_state)
    logger.info("分身管理路由已注册 (/api/subagent/*)")
except Exception as e:
    logger.error("加载分身管理路由失败: %s", e)

try:
    from types import SimpleNamespace
    from agent.server_routes.routes_assets import register_routes as reg_assets
    _assets_state = SimpleNamespace(
        session_mgr=None,
        vector_store=None,
        skills_mgr=_skills_mgr,
    )
    reg_assets(app, _assets_state)
    logger.info("资产管理路由已注册 (/api/assets/*)")
except Exception as e:
    logger.error("加载资产管理路由失败: %s", e)

# ════════════════════════════════════════════════════════════
#  用户行为回放路由（/api/replay/*）
#  Why 接线：前端 yunshu-ui replayRecorder.ts / sessionReplay.ts 上传录制数据，
#  此前 app_server 仅有 /replay-viewer 页面、无 API，上传即 404。
# ════════════════════════════════════════════════════════════
try:
    from agent.server_routes.routes_replay import register_routes as reg_replay
    reg_replay(app, lambda: None)
    logger.info("用户行为回放路由已注册 (/api/replay/*)")
except Exception as e:
    logger.error("加载回放路由失败: %s", e)

# ════════════════════════════════════════════════════════════
#  向量记忆路由（/api/vector/* + /api/knowledge/add）
#  Why 接线：前端 static/js/sidebar/memory.js 调用 /api/vector/stats|add、
#  /api/knowledge/add，此前未注册即 404；/api/vector/search 已迁移至
#  plugins/memory.py（任务 T1.3），register_vector_routes 不含 search 以避免路由冲突。
# ════════════════════════════════════════════════════════════
try:
    from agent.server_routes.routes_memory import register_vector_routes as reg_vector
    reg_vector(app, _Yunshu)
    logger.info("向量记忆路由已注册 (/api/vector/*, /api/knowledge/add)")
except Exception as e:
    logger.error("加载向量记忆路由失败: %s", e)

# ════════════════════════════════════════════════════════════
#  遗留重构接线 T2-T5（见 docs/zh/架构收口遗留重构任务清单_20260816.md）
#  均为 state 未使用的独立路由模块，整体注册不会与既有路由冲突。
# ════════════════════════════════════════════════════════════

# T3：业务仪表盘（Prometheus 告警依赖 /api/business/prometheus）
try:
    from agent.server_routes.routes_business_dashboard import register_routes as reg_business
    reg_business(app, lambda: None)
    logger.info("业务仪表盘路由已注册 (/api/business/*)")
except Exception as e:
    logger.error("加载业务仪表盘路由失败: %s", e)

# T2：用户反馈（后端 get_feedback_manager 已多模块使用，补 HTTP 暴露）
try:
    from agent.server_routes.routes_feedback import register_routes as reg_feedback
    reg_feedback(app, lambda: None)
    logger.info("反馈路由已注册 (/api/feedback/*)")
except Exception as e:
    logger.error("加载反馈路由失败: %s", e)

# T4：健康评分（与 health_bp 的 /api/health/dashboard 等路径不冲突）
try:
    from agent.server_routes.routes_health import register_routes as reg_health
    reg_health(app, lambda: None)
    logger.info("健康评分路由已注册 (/api/health/score 等)")
except Exception as e:
    logger.error("加载健康评分路由失败: %s", e)

# T5：监控仪表盘（质量/链路追踪，契约测试已定义）
try:
    from agent.server_routes.routes_dashboard import register_routes as reg_dashboard
    reg_dashboard(app, lambda: None)
    logger.info("监控仪表盘路由已注册 (/api/dashboard/*)")
except Exception as e:
    logger.error("加载监控仪表盘路由失败: %s", e)

# T6：orchestrator 语义层配置热更（独立子函数，无需 state，仅用 Orchestrator 类）
try:
    from agent.server_routes.routes_config import register_semantic_config_routes as reg_sem_cfg
    reg_sem_cfg(app)
    logger.info("语义层配置热更路由已注册 (/api/orchestrator/semantic-config)")
except Exception as e:
    logger.error("加载语义层配置路由失败: %s", e)

# T7：会话交接（独立子函数，需 session_mgr + Yunshu）
try:
    from agent.server_routes.routes_sessions import register_handoff_routes as reg_handoff
    from types import SimpleNamespace as _SN
    reg_handoff(app, _SN(session_mgr=_session_mgr, Yunshu=_Yunshu))
    logger.info("会话交接路由已注册 (/api/handoff)")
except Exception as e:
    logger.error("加载会话交接路由失败: %s", e)

# T8.1：多租户管理 API（TenantManager HTTP 化，管理端点 require_token）
try:
    from agent.server_routes.routes_tenants import register_routes as reg_tenants
    reg_tenants(app, lambda: None)
    logger.info("多租户管理路由已注册 (/api/open/tenants/*)")
except Exception as e:
    logger.error("加载多租户管理路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  技能配置 API（已迁移至 plugins/skills.py，任务 T1.5）
# ════════════════════════════════════════════════════════════


# ── 网络配置管理器 ──
from agent.network_config import NetworkConfigManager

# 【P2 已清理】SecureConfigManager 已移除，敏感数据统一由 .env 单一数据源管理
_network_config_mgr = NetworkConfigManager()

# ── 启动时自动将搜索实例注册到全局搜索引擎 ──
try:
    _network_config_mgr.apply_search_instances(_web_search)
    _Yunshu._web_search = _web_search
    logger.info("[启动] 搜索实例已自动注册到全局搜索引擎")
except Exception as e:
    logger.warning("[启动] 搜索实例注册失败（可在网络配置面板手动应用）: %s", e)


# ════════════════════════════════════════════════════════════
#  扩展系统管理器（Skills / MCP / Channels / Plugins）
# ════════════════════════════════════════════════════════════

from agent.extensions.manager import ExtensionManager
from agent.extensions.market import ExtensionMarket

_extension_mgr = ExtensionManager(network_config_mgr=_network_config_mgr)
_extension_market = ExtensionMarket()


# ════════════════════════════════════════════════════════════
#  扩展系统 API（已迁移至 plugins/skills.py，任务 T1.5）
# ════════════════════════════════════════════════════════════


# ── 工具配置 API（已迁移至 plugins/skills.py，任务 T1.5）──


@app.route("/api/privacy/info")
@log_request(show_response=False)
def api_privacy_info():
    """返回数据采集透明度信息"""
    from sensor.window_sensor import HAS_WIN32
    return jsonify({
        "version": 1,
        "采集说明": "云枢为了感知自己的身体状态，会采集以下系统信息：",
        "categories": [
            {
                "name": "硬件状态",
                "items": ["CPU 使用率和温度", "内存使用率", "磁盘空间", "电池电量"],
                "purpose": "感知身体状态，调整行为模式",
            },
            {
                "name": "系统信息",
                "items": ["操作系统版本", "Python 版本", "主机名"],
                "purpose": "了解运行环境",
            },
            {
                "name": "窗口活动",
                "items": ["当前活跃窗口标题", "当前进程名称", "窗口切换频率"],
                "purpose": "了解用户注意力焦点（**需用户明确同意**）",
                "requires_consent": True,
                "currently_active": _window_sensor is not None and hasattr(_window_sensor, 'is_running') and bool(_window_sensor.is_running),
            },
        ],
        "不采集的信息": ["键盘输入内容", "鼠标点击位置", "文件内容", "浏览器历史"],
        "数据存储": {
            "location": "本地 memory_data/ 目录",
            "format": "JSONL 文件",
            "retention": "日志文件按大小滚动保留",
        },
    })


@app.route("/api/window/consent", methods=["POST"])
@log_request()
def api_window_consent():
    """用户同意或拒绝窗口监控"""
    global _window_sensor_consented, _window_sensor
    data = request.get_json() or {}
    consent = data.get("consent", False)
    _window_sensor_consented = consent

    if _window_sensor:
        config = _window_sensor.get_config()
        if consent:
            config["enabled"] = True
            _window_sensor.save_config(config)
            if not _window_sensor.is_running:
                _window_sensor.start()
            logger.info("用户已同意窗口监控")
        else:
            config["enabled"] = False
            _window_sensor.save_config(config)
            if _window_sensor.is_running:
                _window_sensor.stop()
            logger.info("用户已拒绝窗口监控")
        return jsonify({"ok": True, "consent": consent, "enabled": consent})

    return jsonify({"ok": False, "error": "窗口传感器未初始化"})


# ════════════════════════════════════════════════════════════
#  心跳接口（已迁移至 plugins/status.py，任务 T1.4）
# ════════════════════════════════════════════════════════════


# ════════════════════════════════════════════════════════════
#  安全守护接口
# ════════════════════════════════════════════════════════════

@app.route("/api/safety/check", methods=["POST"])
@require_token
@log_request()
def api_safety_check():
    """检查文本是否包含危险操作"""
    data = request.get_json() or {}
    text = data.get("text", "")
    result = _safety_guard.check(text)
    return jsonify(result)


@app.route("/api/safety/alerts")
@log_request(show_response=False)
def api_safety_alerts():
    """获取最近的告警通知"""
    limit = request.args.get("limit", 20, type=int)
    alerts = _alert_queue[-limit:]
    return jsonify({"alerts": alerts, "stats": _safety_guard.get_stats()})


@app.route("/api/safety/keywords", methods=["GET", "POST"])
@require_token
@log_request()
def api_safety_keywords():
    """获取或添加危险关键词"""
    if request.method == "POST":
        data = request.get_json() or {}
        pattern = data.get("pattern", "")
        description = data.get("description", "")
        level = data.get("level", "warning")
        category = data.get("category", "")
        if not pattern:
            return jsonify({"ok": False, "error": "缺少 pattern"}), 400
        _safety_guard.add_keyword(pattern, description, level, category)
        _safety_guard.reload()
        return jsonify({"ok": True})
    return jsonify({"keywords": _safety_guard._keywords, "stats": _safety_guard.get_stats()})


# ════════════════════════════════════════════════════════════
#  权限控制面板 — ActionTracker + API 端点
# ════════════════════════════════════════════════════════════

import threading as _threading
import time as _time

class ActionTracker:
    """实时操作追踪器 — 记录智能体正在做什么、做过什么"""

    def __init__(self, max_history=100):
        self._current_action = None  # {tool, params, target, start_time, status, auth}
        self._action_history = []    # 已完成的操作历史
        self._access_log = []        # 数据访问记录
        self._emergency_state = {    # 紧急状态
            "paused": False,
            "stopped": False,
            "network_blocked": False,
        }
        self._max_history = max_history
        self._lock = _threading.Lock()

    def start_action(self, tool: str, params: dict = None, target: str = ""):
        """开始追踪一个操作（自动完成前一个未完成的操作）"""
        with self._lock:
            # 如果已有正在运行的操作，先自动完成它
            if self._current_action and self._current_action["status"] == "running":
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                self._current_action["status"] = "interrupted"
                self._current_action["elapsed"] = round(elapsed, 2)
                self._current_action["result"] = "被新操作中断"
                self._action_history.append(dict(self._current_action))
                if len(self._action_history) > self._max_history:
                    self._action_history = self._action_history[-self._max_history:]

            self._current_action = {
                "tool": tool,
                "params": params or {},
                "target": target,
                "start_time": datetime.datetime.now().isoformat(),
                "status": "running",
                "elapsed": 0,
            }
        return self._current_action

    def finish_action(self, status="completed", result: str = ""):
        """完成当前操作"""
        with self._lock:
            if self._current_action:
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                self._current_action["status"] = status
                self._current_action["elapsed"] = round(elapsed, 2)
                self._current_action["result"] = result[:200]
                self._action_history.append(dict(self._current_action))
                if len(self._action_history) > self._max_history:
                    self._action_history = self._action_history[-self._max_history:]
                old = self._current_action
                self._current_action = None
                return old
        return None

    def log_access(self, access_type: str, target: str, detail: str = "",
                   permission: str = "allowed", duration: float = 0):
        """记录一次数据访问"""
        entry = {
            "time": datetime.datetime.now().isoformat(),
            "type": access_type,       # file | window | sensor | network
            "target": target,
            "detail": detail,
            "permission": permission,  # allowed | requires_consent | blocked
            "duration": round(duration, 2),
        }
        with self._lock:
            self._access_log.append(entry)
            if len(self._access_log) > self._max_history * 2:
                self._access_log = self._access_log[-self._max_history * 2:]
        return entry

    def get_status(self) -> dict:
        """获取当前状态（供前端轮询）"""
        with self._lock:
            current = None
            if self._current_action:
                start = datetime.datetime.fromisoformat(self._current_action["start_time"])
                elapsed = (datetime.datetime.now() - start).total_seconds()
                current = dict(self._current_action)
                current["elapsed"] = round(elapsed, 2)

            return {
                "current_action": current,
                "emergency": dict(self._emergency_state),
                "action_count": len(self._action_history),
                "access_count": len(self._access_log),
            }

    def get_access_log(self, limit=20, type_filter=None) -> list:
        """获取数据访问记录"""
        with self._lock:
            logs = list(self._access_log)
        if type_filter:
            logs = [l for l in logs if l["type"] == type_filter]
        return logs[-limit:]

    def get_action_history(self, limit=20) -> list:
        """获取操作历史"""
        with self._lock:
            return list(self._action_history[-limit:])

    def emergency_stop(self):
        """紧急停止"""
        with self._lock:
            self._emergency_state["stopped"] = True
            self._current_action = None
        logger.warning("🚨 紧急停止已触发")
        return True

    def emergency_pause(self):
        """暂停智能体"""
        with self._lock:
            self._emergency_state["paused"] = not self._emergency_state["paused"]
        state = "已暂停" if self._emergency_state["paused"] else "已恢复"
        logger.info(f"⏸ 智能体{state}")
        return self._emergency_state["paused"]

    def toggle_network_block(self):
        """切换网络封锁"""
        with self._lock:
            self._emergency_state["network_blocked"] = not self._emergency_state["network_blocked"]
        state = "已封锁" if self._emergency_state["network_blocked"] else "已解除"
        logger.info(f"🔌 网络{state}")
        return self._emergency_state["network_blocked"]

    def reset(self):
        """重置所有状态"""
        with self._lock:
            self._current_action = None
            self._emergency_state = {"paused": False, "stopped": False, "network_blocked": False}
        logger.info("🔄 操作追踪器已重置")
        return True


# 全局操作追踪器实例
_action_tracker = ActionTracker()

# 自动包装工具调用以追踪操作
_original_tool_call = _agent_tools.call
def _tracked_tool_call(*args, **params):
    """带追踪的工具调用包装

    部分工具（如 ext_install）的参数中也包含 'name' 字段，
    因此必须使用 *args/**params 的签名，与原 tools.call 保持一致，
    避免 Python 的参数冲突。
    """
    # 从位置参数或关键字参数中提取工具名
    name = args[0] if args else params.pop("name", None)
    target = str(params.get("path", params.get("url", params.get("target", ""))))
    _action_tracker.start_action(name, params, target)
    try:
        result = _original_tool_call(name, **params)
        _action_tracker.finish_action("completed", str(result)[:200])
        # 自动记录数据访问日志
        if any(k in name for k in ["http", "fetch", "search", "api", "browse"]):
            access_type = "network"
        elif any(k in name for k in ["read", "write", "list", "delete", "rename", "copy"]):
            access_type = "file"
        else:
            access_type = "sensor"
        _action_tracker.log_access(access_type, target or name, name, "allowed")
        return result
    except Exception as e:
        _action_tracker.finish_action("failed", str(e)[:200])
        raise
_agent_tools.call = _tracked_tool_call

# 权限开关状态（允许用户快速切换）
_permission_toggles = {
    "window_monitor": True,
    "sensor": True,
    "network_access": True,
    "file_write": True,
    "dangerous_ops": False,  # 默认关闭危险操作授权
}


@app.route("/api/permission/status")
@log_request(show_response=False)
def api_permission_status():
    """获取权限控制面板状态 — 当前操作 + 总览统计"""
    tracker_status = _action_tracker.get_status()

    # 统计信息
    perm_stats = _safety_guard.get_stats()
    try:
        perm_logs = _Yunshu._permission.get_permission_log()
        perm_check_count = len(perm_logs)
    except Exception:
        perm_check_count = 0

    # 工具数量
    from agent.tools import list_tools as _list_tools
    tools = _list_tools()
    tool_count = len(tools)

    # 告警数量
    alert_count = len(_alert_queue)

    return jsonify({
        "current_action": tracker_status["current_action"],
        "emergency": tracker_status["emergency"],
        "stats": {
            "blocked": perm_stats.get("blocked_count", 0),
            "warned": perm_stats.get("warned_count", 0),
            "total_alerts": alert_count,
            "perm_checks": perm_check_count,
            "tools": tool_count,
            "actions_tracked": tracker_status["action_count"],
            "access_tracked": tracker_status["access_count"],
        },
        "toggles": dict(_permission_toggles),
    })


@app.route("/api/permission/log")
@log_request(show_response=False)
def api_permission_log():
    """获取权限操作日志"""
    limit = request.args.get("limit", 20, type=int)
    logs = _action_tracker.get_action_history(limit)

    # 也包含 PermissionSystem 的日志
    try:
        perm_logs = _Yunshu._permission.get_permission_log(limit)
    except Exception:
        perm_logs = []

    return jsonify({
        "action_logs": logs,
        "perm_logs": perm_logs,
    })


@app.route("/api/permission/stats")
@log_request(show_response=False)
def api_permission_stats():
    """获取聚合统计"""
    guard_stats = _safety_guard.get_stats()
    try:
        perm = _Yunshu._permission
        perm_logs = perm.get_permission_log()
        perm_stats = {
            "total_checks": len(perm_logs),
            "backup_count": getattr(perm, '_backup_count', 0),
            "pending_confirm": sum(1 for l in perm_logs if l.get("requires_confirmation") and not l.get("confirmed")),
        }
    except Exception:
        perm_stats = {"total_checks": 0, "backup_count": 0, "pending_confirm": 0}

    # 所有注册工具及其权限等级
    from agent.tools import list_tools as _list_tools
    tools = _list_tools()
    tool_perms = []
    for t in tools:
        name = t["name"]
        # 简单权限分类：根据名称推断
        dangerous_keywords = ["delete", "remove", "format", "stop", "shutdown", "exec", "write"]
        sensitive_keywords = ["write", "modify", "config", "setting"]
        is_dangerous = any(k in name.lower() for k in dangerous_keywords)
        is_sensitive = any(k in name.lower() for k in sensitive_keywords)
        if is_dangerous:
            level = "dangerous"
        elif is_sensitive:
            level = "requires_confirm"
        else:
            level = "allowed"
        tool_perms.append({"name": name, "description": t.get("description", ""), "level": level})

    return jsonify({
        "guard_stats": {
            "blocked": guard_stats.get("blocked_count", 0),
            "warned": guard_stats.get("warned_count", 0),
            "total_alerts": guard_stats.get("total_alerts", 0),
            "keywords": guard_stats.get("keywords_loaded", {}),
        },
        "perm_stats": perm_stats,
        "tools": tool_perms,
        "toggles": dict(_permission_toggles),
    })


@app.route("/api/permission/access-log")
@log_request(show_response=False)
def api_permission_access_log():
    """获取数据访问记录"""
    limit = request.args.get("limit", 20, type=int)
    type_filter = request.args.get("type", None)
    logs = _action_tracker.get_access_log(limit, type_filter)
    return jsonify({"access_logs": logs})


@app.route("/api/permission/emergency", methods=["POST"])
@require_token
@log_request()
def api_permission_emergency():
    """紧急控制 — 暂停/停止/重置"""
    data = request.get_json() or {}
    action = data.get("action", "")

    if action == "stop":
        result = _action_tracker.emergency_stop()
        return jsonify({"ok": True, "action": "stop", "message": "🚨 已触发紧急停止"})
    elif action == "pause":
        paused = _action_tracker.emergency_pause()
        msg = "⏸ 智能体已暂停" if paused else "▶ 智能体已恢复"
        return jsonify({"ok": True, "action": "pause", "paused": paused, "message": msg})
    elif action == "network_block":
        blocked = _action_tracker.toggle_network_block()
        msg = "🔌 网络访问已封锁" if blocked else "🌐 网络访问已恢复"
        return jsonify({"ok": True, "action": "network_block", "blocked": blocked, "message": msg})
    elif action == "reset":
        _action_tracker.reset()
        return jsonify({"ok": True, "action": "reset", "message": "🔄 操作追踪器已重置"})
    elif action == "cancel":
        _action_tracker.finish_action("cancelled", "用户手动取消")
        # 真正中止正在进行的聊天
        try:
            _Yunshu.abort_chat()
        except Exception as e:
            logger.warning("中止聊天时出错: %s", e)
        return jsonify({"ok": True, "action": "cancel", "message": "⏹ 当前操作已取消"})

    return jsonify({"ok": False, "error": f"未知操作: {action}"}), 400


@app.route("/api/permission/toggle", methods=["POST"])
@require_token
@log_request()
def api_permission_toggle():
    """切换权限开关"""
    data = request.get_json() or {}
    key = data.get("key", "")
    enabled = data.get("enabled")

    if key not in _permission_toggles:
        return jsonify({"ok": False, "error": f"未知开关: {key}"}), 400

    if enabled is not None:
        _permission_toggles[key] = bool(enabled)
    else:
        _permission_toggles[key] = not _permission_toggles[key]

    # 特殊处理：窗口监控开关联动
    if key == "window_monitor":
        global _window_sensor_consented
        _window_sensor_consented = _permission_toggles[key]
        if _window_sensor:
            config = _window_sensor.get_config()
            config["enabled"] = _permission_toggles[key]
            _window_sensor.save_config(config)
            if _permission_toggles[key] and not _window_sensor.is_running:
                _window_sensor.start()
            elif not _permission_toggles[key] and _window_sensor.is_running:
                _window_sensor.stop()

    logger.info(f"权限开关 {key} → {'开' if _permission_toggles[key] else '关'}")
    return jsonify({"ok": True, "key": key, "enabled": _permission_toggles[key]})


# ════════════════════════════════════════════════════════════
#  定时调度系统启动
# ════════════════════════════════════════════════════════════
# 调度域路由（/api/scheduler/*、/api/schedules*、/api/tasks*）已迁移至
# plugins/mcp_scheduler.py；此处仅保留调度器后台线程的启动副作用
# （app_server 导入即启动，与迁移前行为一致；插件视图函数内经
# agent.scheduling.get_schedule_scheduler 取同一单例）。

from agent.scheduling import get_schedule_scheduler

get_schedule_scheduler().start()
logger.info("定时调度系统已启动")


# ════════════════════════════════════════════════════════════
#  HTML 界面
# ════════════════════════════════════════════════════════════

# HTML 模板已提取到 templates/index.html

@app.route("/")
def index():
    """[简易] 新首页：系统健康度仪表盘（综合监控中心）"""
    from flask import Response
    response = render_template("health_dashboard.html")
    return Response(response, mimetype='text/html; charset=utf-8')

@app.route("/chat")
def chat_page():
    from flask import redirect
    return redirect("/static/chat")

@app.route("/legacy")
def legacy_ui():
    """旧版界面入口（云枢·数字生命体）"""
    from flask import Response
    response = render_template("index.html")
    return Response(response, mimetype='text/html; charset=utf-8')

@app.route("/static/<path:subpath>")
def spa_fallback(subpath):
    from flask import make_response, send_from_directory, abort
    full_path = os.path.join(app.static_folder, subpath)
    # [不易] spa.html 已删除，静态资源未命中时返回 404，不再回退到已删除的模板
    if os.path.isfile(full_path):
        resp = make_response(send_from_directory(app.static_folder, subpath))
    else:
        abort(404)
    resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    resp.headers['Vary'] = '*'
    return resp


@app.route("/mascot-test")
def mascot_test():
    """Mascot 功能测试页面"""
    return render_template("mascot-test.html")

@app.route("/network-test")
def network_test():
    """网络配置功能测试页面"""
    response = render_template("test_network.html")
    from flask import Response
    return Response(response, mimetype='text/html; charset=utf-8')


@app.route("/search-status")
def search_status_page():
    """搜索引擎状态监控页面"""
    response = render_template("search-status.html")
    from flask import Response
    return Response(response, mimetype='text/html; charset=utf-8')


@app.route("/network-config-debug")
def network_config_debug():
    """网络配置调试面板"""
    response = render_template("network_config_debug.html")
    from flask import Response
    return Response(response, mimetype='text/html; charset=utf-8')


@app.route("/replay-viewer")
def replay_viewer():
    """[简易] 用户行为回放页面"""
    response = render_template("replay_viewer.html")
    from flask import Response
    return Response(response, mimetype='text/html; charset=utf-8')


# ════════════════════════════════════════════════════════════
#  Prometheus 监控端点
# ════════════════════════════════════════════════════════════
# /metrics 路由由 PrometheusMetrics(app, ...) 自动注册（endpoint: prometheus_metrics），
# 使用 prometheus_client 默认 REGISTRY。无需在此重复注册。
# routes_logging 也会注册 /metrics（endpoint: api_prometheus_metrics），但 werkzeug
# 按规则添加顺序匹配，PrometheusMetrics 的规则先注册，会被优先命中。


# ════════════════════════════════════════════════════════════
#  测试端点 - 用于验证日志装饰器异常处理
# ════════════════════════════════════════════════════════════

@app.route("/api/test/error")
@log_request()
def api_test_error():
    """
    测试端点：触发除零错误以验证堆栈捕获
    
    用于验证日志装饰器是否正确捕获并输出异常堆栈信息
    """
    # 触发除零错误
    x = 1 / 0
    return jsonify({"ok": True, "result": x})


@app.route("/api/test/null")
@log_request()
def api_test_null():
    """
    测试端点：触发空指针错误以验证堆栈捕获
    """
    obj = None
    # 触发 AttributeError
    return jsonify({"ok": True, "result": obj.some_method()})


@app.route("/api/test/division")
@log_request()
def api_test_division():
    """
    测试端点：测试除法运算（正常情况）
    """
    a = request.args.get("a", 10, type=float)
    b = request.args.get("b", 2, type=float)
    
    try:
        result = a / b
        return jsonify({"ok": True, "result": result})
    except ZeroDivisionError as e:
        # 这个异常会被日志装饰器捕获
        raise


# ════════════════════════════════════════════════════════════
#  API 网关适配层（/api/open/* + /api/docs）
#  Why 置于文件末尾：_scan_internal_routes 在 register_gateway 时遍历
#  app.url_map 生成全量 Swagger 文档——必须等全部 /api/* 路由（含 T2-T7
#  接线与后续内联路由）注册完成后再挂载，否则新接口缺失于文档。
#  适配层采用中间层模式：仅拦截 /api/open/* 前缀，内部 API 认证不变。
# ════════════════════════════════════════════════════════════
try:
    from agent.api_gateway_flask import register_gateway as reg_gateway
    reg_gateway(app)
    logger.info("API 网关适配层已挂载 (/api/open/*, /api/docs)")
except Exception as e:
    logger.error("加载 API 网关适配层失败: %s", e)

# 程序退出时停止窗口传感器
import atexit

@atexit.register
def _cleanup_window_sensor():
    global _window_sensor
    if _window_sensor:
        _window_sensor.stop()

if __name__ == "__main__":
    # 记录沙盒功能状态
    sandbox_enabled = os.getenv("YUNSHU_FEATURE_SANDBOX", "false").lower() == "true"
    sandbox_status = "已启用" if sandbox_enabled else "已关闭"
    logger.info("[沙盒] 功能状态: %s (YUNSHU_FEATURE_SANDBOX=%s)",
                sandbox_status, os.getenv("YUNSHU_FEATURE_SANDBOX", "未设置(默认false)"))

    print("=" * 56)
    print("  云枢 · 数字生命体 Web 界面")
    print("  http://127.0.0.1:5678")
    print("=" * 56)
    print("  顶部：实时健康指标 + 状态栏")
    print("  下方：与云枢对话")
    print(f"  沙盒：{sandbox_status}")
    print("=" * 56)
    
    # 启动定时任务：每 60 秒更新系统资源指标
    if PROMETHEUS_AVAILABLE:
        def update_system_metrics():
            """更新系统资源指标"""
            try:
                import psutil
                CPU_USAGE.set(psutil.cpu_percent(interval=1))
                MEMORY_USAGE.set(psutil.virtual_memory().percent)
            except Exception as e:
                logger.error(f"更新系统指标失败：{e}")
        
        def start_metrics_thread():
            import threading
            def _update():
                while True:
                    update_system_metrics()
                    time.sleep(60)
            thread = threading.Thread(target=_update, daemon=True)
            thread.start()
            print("✅ 系统资源监控线程已启动")
        
        start_metrics_thread()

    # 启动健康采集线程：五层探针 → 加权评分 → 落盘 data/health/history-*.jsonl
    try:
        from agent.health.collector import start_collector
        start_collector()
        logger.info("[健康] 五层健康探针采集线程已启动")
    except Exception as e:
        logger.error(f"[健康] 健康采集线程启动失败：{e}")

    # 启动前先清理 5678 端口的旧进程
    try:
        import subprocess, signal
        result = subprocess.run(
            ['netstat', '-ano'], capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if ':5678' in line and 'LISTENING' in line:
                parts = line.strip().split()
                if parts:
                    pid = parts[-1]
                    try:
                        if sys.platform == 'win32':
                            subprocess.run(['taskkill', '/F', '/PID', pid],
                                         capture_output=True, timeout=3)
                        else:
                            os.kill(int(pid), signal.SIGTERM)
                    except Exception:
                        pass
    except Exception:
        pass

    # 启动增强型定时任务调度器
    try:
        scheduler = get_scheduler()
        # 从 JSON 加载 API 创建的任务
        loaded = scheduler.load_from_json()
        if loaded:
            print(f"✅ 已加载 {loaded} 个预设定时任务")
        # 为调度器注入心跳函数和 Yunshu 引用
        scheduler._heartbeat_func = perform_heartbeat_check
        scheduler._yunshu_ref = _Yunshu
        # 注册内置 heartbeat 任务
        scheduler.add_interval_task(
            name="系统心跳",
            func=lambda: None,  # 占位，实际由 _heartbeat_func 处理
            interval_seconds=60,
        )
        # TASK-05 学习类定时任务统一注册（feedback_agent 每日 / 周级进化 / 生命周期检查）
        # 各任务按 config learning.*.enabled 独立开关（默认关闭，安全底线；调度触发默认 dry-run）
        try:
            from agent.skills_mgmt.learning_scheduler import register_learning_schedulers
            learning_tasks = register_learning_schedulers()
            print(f"✅ TASK-05 学习类定时任务注册: {learning_tasks}")
        except Exception as e:
            print(f"⚠️ TASK-05 学习类定时任务注册失败（不阻断主流程）: {e}")
        scheduler.start_daemon(check_interval=10)
        print("✅ 定时任务调度器已启动 (daemon)")
    except Exception as e:
        print(f"⚠️ 定时任务调度器启动失败: {e}")

    # 启动搜索引擎性能监控（可选，默认不启动）
    try:
        # 从配置文件读取是否启动性能监控
        network_config_file = os.path.join(os.path.dirname(__file__), "agent", "data", "network_config.json")
        if os.path.exists(network_config_file):
            with open(network_config_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
                search_config = config.get('search', {})
                if search_config.get('performance_monitor_enabled', False):
                    interval = search_config.get('performance_monitor_interval', 300)
                    from agent.search_performance_monitor import start_performance_monitor
                    start_performance_monitor(interval)
                    print(f"✅ 搜索引擎性能监控已启动 (间隔: {interval} 秒)")
    except Exception as e:
        print(f"[启动] 搜索引擎性能监控启动失败: {e}")
    
    webbrowser.open("http://127.0.0.1:5678")
    # 使用 Waitress 生产级 WSGI 服务器（替代 Flask 内置开发服务器）
    # 多线程 + 纯 Python，Windows 原生兼容
    from waitress import serve
    # threads 8→16: 高并发压测发现 LLM 长耗时请求占满线程导致排队（Task queue 高发），
    # 提升线程容量缓解排队；LLM 外呼另有 60s 看门狗兜底（orchestrator._run_llm_bounded）
    serve(app, host="127.0.0.1", port=5678, threads=16)
