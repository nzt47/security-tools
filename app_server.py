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

# 修复 Windows 控制台编码，避免中文日志乱码
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

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
    init_workspace, list_workspace, write_workspace, delete_workspace,
    list_scheduled_tasks, create_scheduled_task, delete_scheduled_task,
    toggle_scheduled_task, browser_navigate, browser_screenshot, browser_close,
    start_process, list_processes, stop_process, get_clipboard, set_clipboard,
    read_file, write_file, list_directory, get_file_info, search_files,
    PROCESS_WHITELIST, get_whitelist_detail, add_whitelist_entry, remove_whitelist_entry, WORKSPACE_DIR,
)
from agent.web import HttpClient, Scraper, SearchEngine, DataProcessor, CrawlerController, BrowserAgent
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


def _trace_id():
    """生成 trace_id（结构化日志用）"""
    import uuid as _uuid
    return _uuid.uuid4().hex[:16]


def _log_struct(action: str, message: str, duration_ms: int = 0, **extra):
    """输出结构化 JSON 日志（符合可观测性约束：trace_id/module_name/action/duration_ms）"""
    import json as _json
    payload = {
        "trace_id": _trace_id(),
        "module_name": "app_server",
        "action": action,
        "duration_ms": duration_ms,
        "message": message,
    }
    payload.update(extra)
    logger.info(_json.dumps(payload, ensure_ascii=False))


app = Flask(__name__)
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')

# 注册日志系统蓝图（/logs/dashboard 页面 + REST API）
try:
    register_log_system(app)
    logger.info("[启动] 日志系统仪表盘与 API 路由已注册")
except Exception as e:
    logger.warning(f"[启动] 日志系统注册失败: {e}")

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
        f"🕒 时间：{now.strftime('%Y年%m月%d日 %H:%M')}\n" +
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

# 从网络配置文件加载 LLM 配置（修复 Web 界面配置 LLM 重启后不生效的问题）
print("[启动] 开始加载网络配置...")
try:
    from agent.network_config import NetworkConfigManager as _NCM
    print("[启动] 成功导入 NetworkConfigManager")
    try:
        from config import _get_secure_manager as _gsm
        _ncm = _NCM(secure_manager=_gsm())
        print("[启动] 使用安全管理器创建配置管理器")
    except Exception as _ex:
        print(f"[启动] 安全管理器不可用: {_ex}，使用默认配置管理器")
        _ncm = _NCM()
    
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
    """根据配置初始化窗口传感器（需用户同意，默认禁用）"""
    global _window_sensor
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

# ── 人格配置管理器 ──
_PERSONALITY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'personality.json')

class PersonalityManager:
    """管理云枢的人格配置数据"""

    def __init__(self):
        self._cache = None

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        try:
            with open(_PERSONALITY_FILE, 'r', encoding='utf-8') as f:
                self._cache = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._cache = self._default()
        return self._cache

    def _save(self, data: dict):
        self._cache = data
        with open(_PERSONALITY_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _default(self) -> dict:
        return {
            "current_profile": "gentle_helper",
            "custom_params": {"tone": 0.6, "emotion": 0.7, "conciseness": 0.4, "initiative": 0.5, "humor": 0.3, "empathy": 0.8},
            "profiles": {
                "gentle_helper": {"name": "温和助人型", "description": "温暖、耐心、富有同理心", "params": {"tone": 0.6, "emotion": 0.7, "conciseness": 0.4, "initiative": 0.5, "humor": 0.3, "empathy": 0.8}},
                "professional": {"name": "专业顾问型", "description": "严谨、客观、信息密度高", "params": {"tone": 0.3, "emotion": 0.2, "conciseness": 0.7, "initiative": 0.6, "humor": 0.1, "empathy": 0.4}},
                "humorous": {"name": "幽默风趣型", "description": "轻松、活泼、喜欢开玩笑", "params": {"tone": 0.8, "emotion": 0.9, "conciseness": 0.3, "initiative": 0.7, "humor": 0.9, "empathy": 0.6}},
            },
            "dimensions": [
                {"key": "tone", "label": "语气", "left": "正式", "right": "随意"},
                {"key": "emotion", "label": "情感", "left": "克制", "right": "丰富"},
                {"key": "conciseness", "label": "简练", "left": "详细", "right": "简洁"},
                {"key": "initiative", "label": "主动", "left": "被动", "right": "主动"},
                {"key": "humor", "label": "幽默", "left": "严肃", "right": "幽默"},
                {"key": "empathy", "label": "同理心", "left": "理性", "right": "感性"},
            ],
        }

    def get(self) -> dict:
        data = self._load()
        return {
            "current_profile": data["current_profile"],
            "custom_params": data["custom_params"],
            "profiles": data["profiles"],
            "dimensions": data["dimensions"],
        }

    def update_params(self, params: dict) -> dict:
        data = self._load()
        data["custom_params"].update(params)
        data["current_profile"] = "custom"
        self._save(data)
        return {"ok": True, "params": data["custom_params"]}

    def apply_profile(self, profile_key: str) -> dict:
        data = self._load()
        if profile_key not in data["profiles"]:
            return {"ok": False, "error": f"未知人格方案: {profile_key}"}
        profile = data["profiles"][profile_key]
        data["current_profile"] = profile_key
        data["custom_params"] = dict(profile["params"])
        self._save(data)
        return {"ok": True, "profile": profile_key, "params": data["custom_params"]}

    def reset(self) -> dict:
        return self.apply_profile("gentle_helper")

_personality_mgr = PersonalityManager()

# ── 工具状态持久化 ──
_TOOLS_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'data', 'tools_config.json')

def _load_tool_states() -> dict:
    """加载工具启用状态"""
    try:
        with open(_TOOLS_CONFIG_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"tool_states": {}}

def _save_tool_states(data: dict):
    """保存工具启用状态"""
    with open(_TOOLS_CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _get_tool_state(name: str) -> bool:
    """获取单个工具的启用状态，默认启用"""
    data = _load_tool_states()
    return data.get("tool_states", {}).get(name, True)

def _set_tool_state(name: str, enabled: bool):
    """设置单个工具的启用状态"""
    data = _load_tool_states()
    data.setdefault("tool_states", {})[name] = enabled
    _save_tool_states(data)

def _get_enabled_tool_names() -> list[str] | None:
    """获取所有已启用的工具名称列表，没有配置文件时返回 None（全部启用）"""
    data = _load_tool_states()
    states = data.get("tool_states", {})
    if not states:
        return None
    enabled = [name for name, e in states.items() if e]
    return enabled if enabled else []

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

# ── 语音输入 API ──
@app.route("/api/voice/listen", methods=["POST"])
@require_token
@log_request()
def api_voice_listen():
    """语音识别接口 - 从麦克风捕获语音并转换为文本"""
    try:
        data = request.get_json() or {}
        duration = min(data.get("duration", 5), 30)  # 最大30秒
        
        if not hasattr(_Yunshu, '_voice_manager') or _Yunshu._voice_manager is None:
            return jsonify({"ok": False, "error": "语音管理器未初始化"}), 500
        
        stt_available = _Yunshu._voice_manager.stt.available
        if not stt_available:
            return jsonify({"ok": False, "error": "语音识别引擎不可用，请检查SpeechRecognition库"}), 500
        
        logger.info(f"[VOICE] 开始语音识别，时长: {duration}秒")
        result = _Yunshu._voice_manager.listen(duration=duration)
        
        if result.success:
            logger.info(f"[VOICE] 语音识别成功: {result.text[:50]}...")
            return jsonify({
                "ok": True,
                "text": result.text,
                "duration": duration
            })
        else:
            logger.warning(f"[VOICE] 语音识别失败: {result.error}")
            return jsonify({"ok": False, "error": result.error}), 400
            
    except Exception as e:
        logger.error(f"[VOICE] 语音识别异常: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/voice/status")
@log_request(show_response=False)
def api_voice_status():
    """获取语音系统状态"""
    try:
        if not hasattr(_Yunshu, '_voice_manager') or _Yunshu._voice_manager is None:
            return jsonify({
                "tts_available": False,
                "stt_available": False,
                "engine": "none",
                "non_blocking": False
            })
        
        status = _Yunshu._voice_manager.get_status()
        return jsonify(status)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/health")
@log_request(show_response=False)
def api_health():
    readings = _Yunshu.body.collect_quick()
    return jsonify([r.to_dict() for r in readings])


@app.route("/api/sensors")
@log_request(show_response=False)
def api_sensors():
    return jsonify(_Yunshu.body.get_sensor_info())


@app.route("/api/status")
@log_request(show_response=False)
def api_status():
    status = _Yunshu.get_status()
    return jsonify(status)


@app.route("/api/mode")
@log_request(show_response=False)
def api_mode():
    mode = _Yunshu.get_behavior_mode()
    profile = _Yunshu._behavior.profile
    thinking = getattr(_Yunshu, '_thinking_mode', {})
    return jsonify({
        "mode": mode.value,
        "label": profile.label,
        "description": profile.description,
        "can_accept_tasks": profile.can_accept_tasks,
        "enable_reflection": profile.enable_reflection,
        "reasons": _Yunshu._behavior._reasons,
        "thinking_mode": thinking.get("label", ""),
    })


@app.route("/api/cognitive/status")
@log_request(show_response=False)
def api_cognitive_status():
    readings = _Yunshu.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    text = _Yunshu._injector.get_summary(reading_dicts)
    body_status = _Yunshu._build_body_status(readings)
    return jsonify({
        "summary": text,
        "full": body_status,
        "mode": _Yunshu._behavior.profile.label,
        "mode_description": _Yunshu._behavior.profile.description,
    })


@app.route("/api/chat", methods=["POST"])
def api_chat():
    global _CHAT_HISTORY
    import time
    start_time = time.time()
    
    data = request.get_json()
    user_input = (data or {}).get("message", "").strip()
    voice_mode = (data or {}).get("voice", False)
    
    logs = []
    logs.append(f"[START] 收到对话请求 - 时间: {datetime.datetime.now().isoformat()}")
    logs.append(f"[INPUT] 用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
    logs.append(f"[CONFIG] 语音模式: {voice_mode}")
    
    if not user_input:
        return jsonify({"error": "消息不能为空"}), 400

    # 获取会话 ID
    session_id = request.args.get("session") or _get_current_session_id()

    # 安全检查（受技能开关控制）
    safety_start = time.time()
    if not getattr(_Yunshu, '_is_skill_enabled', lambda x: True)("safety_guard"):
        safety_result = {"level": "safe", "matches": [], "safe": True}
        logs.append("[SAFETY] 安全守护技能已禁用，跳过检查")
    else:
        safety_result = _safety_guard.check(user_input)
    safety_time = (time.time() - safety_start) * 1000
    logs.append(f"[SAFETY] 安全检查完成 - 耗时: {safety_time:.2f}ms, 级别: {safety_result['level']}")

    if safety_result["level"] == "critical":
        match_lines = chr(10).join(
            f"• {m['description']} [{m['category']}]"
            for m in safety_result["matches"][:5]
        )
        blocked_msg = (
            f"⚠️ 安全警告：检测到危险操作！\n\n{match_lines}"
            f"\n\n此操作已被拦截。如需执行，请确认您了解相关风险。"
        )
        logs.append(f"[BLOCKED] 安全拦截触发")
        
        # 记录 Prometheus 指标
        if PROMETHEUS_AVAILABLE and SECURITY_BLOCKS:
            for match in safety_result["matches"]:
                SECURITY_BLOCKS.labels(
                    rule=match.get('description', 'unknown'),
                    level=match.get('level', 'unknown'),
                    category=match.get('category', 'unknown')
                ).inc()
        
        return jsonify({
            "response": blocked_msg,
            "mode": _Yunshu.get_behavior_mode().value,
            "mode_label": _Yunshu._behavior.profile.label,
            "blocked": True,
            "safety": safety_result,
            "logs": logs,
            "timing": {"total": (time.time() - start_time) * 1000},
        }), 403

    # 记录 LLM 状态便于诊断
    llm_state = _Yunshu.get_config()
    logs.append(f"[LLM] 配置状态 - 已配置: {llm_state['configured']}, 提供商: {llm_state['provider']}, API Key已设置: {llm_state['api_key_set']}")

    # 对话处理
    chat_start = time.time()
    try:
        logs.append(f"[CHAT] 开始调用 DigitalLife.chat()")
        response = _Yunshu.chat(user_input)
        chat_time = (time.time() - chat_start) * 1000
        logs.append(f"[CHAT] 对话响应生成完成 - 耗时: {chat_time:.2f}ms")
        logs.append(f"[CHAT] 响应长度: {len(response)} 字符")
    except Exception as e:
        import traceback
        chat_time = (time.time() - chat_start) * 1000
        logger.error(f"Chat error: {e}", exc_info=True)
        response = f"（处理出错: {e}）"
        logs.append(f"[ERROR] 对话处理失败 - 耗时: {chat_time:.2f}ms, 错误: {str(e)}")
        stack_trace = traceback.format_exc()
        logs.append(f"[STACK TRACE] {stack_trace[:500]}")

    # 语音合成（如果启用）
    voice_time = 0
    voice_result = None
    if voice_mode:
        voice_start = time.time()
        try:
            logs.append(f"[VOICE] 开始语音合成")
            voice_result = _Yunshu.speak(response)
            voice_time = (time.time() - voice_start) * 1000
            if voice_result.get("ok"):
                logs.append(f"[VOICE] 语音合成成功 - 耗时: {voice_time:.2f}ms")
            else:
                logs.append(f"[VOICE] 语音合成失败 - 耗时: {voice_time:.2f}ms, 错误: {voice_result.get('error')}")
        except Exception as e:
            import traceback
            voice_time = (time.time() - voice_start) * 1000
            logs.append(f"[ERROR] 语音合成异常 - 耗时: {voice_time:.2f}ms, 错误: {str(e)}")
            stack_trace = traceback.format_exc()
            logs.append(f"[STACK TRACE] {stack_trace[:500]}")

    entry = {
        "user": user_input,
        "Yunshu": response,
        "mode": _Yunshu.get_behavior_mode().value,
        "timestamp": datetime.datetime.now().isoformat(),
    }
    # 保存到会话（附带工具步骤和推理过程，用于页面刷新后恢复显示）
    _session_mgr.add_message(session_id, "user", user_input)
    _session_mgr.add_message(
        session_id, "assistant", response,
        tool_steps=getattr(_Yunshu, '_last_tool_steps', None),
        reasoning=getattr(_Yunshu, '_last_reasoning', None),
    )
    _CHAT_HISTORY.append(entry)

    # 自动保存到云枢记忆
    _save_conversation_record(
        user_input=user_input,
        response=response,
        mode=_Yunshu.get_behavior_mode().value,
        health_data=[r.to_dict() for r in _Yunshu.check_health()],
    )

    total_time = (time.time() - start_time) * 1000
    logs.append(f"[END] 请求处理完成 - 总耗时: {total_time:.2f}ms")
    
    # 打印详细日志到控制台
    print("\n" + "="*80)
    print(f"📊 对话请求日志 [{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print("-"*80)
    for log in logs:
        print(log)
    print("="*80 + "\n")

    # 计算本次消息的 token 用量
    _ctx_counter = _get_token_counter()
    _input_tokens = _ctx_counter.count(user_input)
    _output_tokens = _ctx_counter.count(response)

    # 计算会话累计 token（快速估算，仅统计 content 字段）
    _session_id_ctx = _get_current_session_id()
    _all_msgs = _session_mgr.get_messages(_session_id_ctx, limit=0)
    _session_total = sum(
        _ctx_counter.count((m.get("content") or ""))
        for m in _all_msgs
    )
    _token_limit = _cfg.get("memory", "token_limit", default=4096)

    return jsonify({
        "response": response,
        "mode": _Yunshu.get_behavior_mode().value,
        "mode_label": _Yunshu._behavior.profile.label,
        "health": [r.to_dict() for r in _Yunshu.check_health()],
        "llm_state": llm_state,
        "logs": logs,
        "tool_steps": getattr(_Yunshu, '_last_tool_steps', []),
        "reasoning": getattr(_Yunshu, '_last_reasoning', None),
        "timing": {
            "total": total_time,
            "safety_check": safety_time,
            "chat_processing": chat_time,
            "voice_synthesis": voice_time,
        },
        "voice_result": voice_result,
        "context": {
            "input_tokens": _input_tokens,
            "output_tokens": _output_tokens,
            "session_total_tokens": _session_total,
            "token_limit": _token_limit,
            "percentage": round(_session_total / _token_limit * 100, 1) if _token_limit > 0 else 0,
        },
    })


_DS_KEY = "sk-ddf2d09a74fc4c9fafb89a906f0f45a3"
_DS_URL = "https://api.deepseek.com/chat/completions"


@app.route("/api/news", methods=["GET"])
def api_news():
    """新闻直通接口 — 搜索+翻译+格式化，绕过 LLM"""
    import time as _time
    topic = request.args.get("topic", "")
    max_results = min(int(request.args.get("max", 8)), 15)

    try:
        _searcher = _Yunshu._get_web_search()
        if not _searcher:
            return jsonify({"ok": False, "error": "搜索引擎不可用"})

        queries = ["latest world news today", "international breaking news"]
        if topic:
            queries = [f"latest {topic} news", f"{topic} today"]

        all_results = []
        seen = set()
        # 通过 SearchEngine 搜索获取新闻标题和摘要
        for q in queries:
            try:
                res = _searcher.search(q, num_results=max_results, timeout=12)
                if res and isinstance(res, dict) and res.get("ok") and res.get("results"):
                    for item in res["results"]:
                        url = (item.get("url") or "").strip()
                        if url and url not in seen:
                            seen.add(url)
                            all_results.append({
                                "title": (item.get("title") or "").strip(),
                                "url": url,
                                "source": _guess_source(url),
                                "content": (item.get("content") or item.get("snippet", "") or "").strip(),
                            })
            except Exception:
                pass
            if len(all_results) >= max_results:
                break

        if not all_results:
            return jsonify({"ok": True, "result": f"已获取到以下信息：\n  - 当前暂无搜索结果\n  - 时间: {_time.strftime('%Y-%m-%d %H:%M UTC')}", "count": 0})

        # 排序：权威媒体优先
        _PREFERRED = ["bbc.com", "cnn.com", "reuters.com", "apnews.com",
                       "theguardian.com", "nytimes.com", "wsj.com"]
        all_results.sort(key=lambda x: next((i for i, d in enumerate(_PREFERRED) if d in x["url"].lower()), len(_PREFERRED)))
        all_results = all_results[:max_results]

        now = _time.strftime("%Y-%m-%d %H:%M UTC")
        lines = [f"已获取到以下信息：", f"  - 找到 {len(all_results)} 条结果:"]
        for i, item in enumerate(all_results, 1):
            _detail = item.get("content", "")[:600]
            lines.append(f"")
            lines.append(f"...{i}. **{item['title']}**")
            lines.append(f"   - 来源: {item['source']}")
            lines.append(f"   - 时间: {now}")
            lines.append(f"   - 详情: {_detail}")
            lines.append(f"   - 链接: {item['url']}")

        return jsonify({"ok": True, "result": "\n".join(lines), "count": len(all_results)})

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


def _guess_source(url):
    url = url.lower()
    sources = {"bbc.com":"BBC","cnn.com":"CNN","reuters.com":"Reuters","apnews.com":"AP News",
               "theguardian.com":"The Guardian","nytimes.com":"New York Times","wsj.com":"WSJ",
               "bloomberg.com":"Bloomberg","aljazeera.com":"Al Jazeera","npr.org":"NPR",
               "foxnews.com":"Fox News","economist.com":"The Economist","sohu.com":"搜狐",
               "sina.com":"新浪","163.com":"网易","thepaper.cn":"澎湃","xinhuanet.com":"新华网"}
    for k, v in sources.items():
        if k in url: return v
    return "新闻媒体"


# ════════════════════════════════════════════════════════════
#  多会话 API
# ════════════════════════════════════════════════════════════

@app.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    """获取会话列表"""
    sessions = _session_mgr.list_sessions()
    current_id = _session_mgr.get_current_id()
    return jsonify({
        "sessions": sessions,
        "current_id": current_id,
    })


@app.route("/api/sessions", methods=["POST"])
def api_sessions_create():
    """创建新会话"""
    data = request.get_json() or {}
    title = data.get("title", "")
    session = _session_mgr.create_session(title=title)
    logger.info("通过 Web 界面创建新会话: %s", session["id"])
    return jsonify(session), 201


@app.route("/api/sessions/<session_id>", methods=["DELETE"])
@require_token
def api_sessions_delete(session_id):
    """删除会话"""
    if _session_mgr.delete_session(session_id):
        # 如果删除的是当前会话，清空历史缓存
        global _CHAT_HISTORY
        if session_id == _session_mgr.get_current_id():
            _CHAT_HISTORY.clear()
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@app.route("/api/sessions/<session_id>/rename", methods=["PUT"])
@require_token
def api_sessions_rename(session_id):
    """重命名会话"""
    data = request.get_json() or {}
    title = data.get("title", "")
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if _session_mgr.rename_session(session_id, title):
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@app.route("/api/sessions/current", methods=["POST"])
@require_token
def api_sessions_set_current():
    """切换当前会话"""
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400
    if _session_mgr.set_current(session_id):
        # 切换会话时也更新 _CHAT_HISTORY 缓存
        global _CHAT_HISTORY
        messages = _session_mgr.get_messages(session_id, limit=50)
        _CHAT_HISTORY = []
        for i in range(0, len(messages), 2):
            user_msg = messages[i]
            assistant_msg = messages[i + 1] if i + 1 < len(messages) else {}
            if user_msg.get("role") == "user":
                _CHAT_HISTORY.append({
                    "user": user_msg.get("content", ""),
                    "Yunshu": assistant_msg.get("content", ""),
                    "mode": "normal",
                    "timestamp": user_msg.get("timestamp", ""),
                })
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@app.route("/api/sessions/<session_id>/messages", methods=["GET"])
def api_sessions_messages(session_id):
    """获取会话消息"""
    limit = request.args.get("limit", 50, type=int)
    messages = _session_mgr.get_messages(session_id, limit=limit)
    return jsonify(messages)


@app.route("/api/history")
@log_request(show_response=False)
def api_history():
    session_id = request.args.get("session") or _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=50)
    result = []
    for i in range(0, len(messages), 2):
        user_msg = messages[i]
        assistant_msg = messages[i + 1] if i + 1 < len(messages) else {}
        if user_msg.get("role") == "user":
            result.append({
                "user": user_msg.get("content", ""),
                "Yunshu": assistant_msg.get("content", ""),
                "mode": "normal",
                "timestamp": user_msg.get("timestamp", ""),
                "_real_index": i // 2,
            })
    return jsonify(result)


@app.route("/api/clear", methods=["POST"])
@require_token
@log_request()
def api_clear():
    global _CHAT_HISTORY
    session_id = request.args.get("session") or _get_current_session_id()
    _session_mgr.clear_messages(session_id)
    _CHAT_HISTORY.clear()
    return jsonify({"ok": True})


@app.route("/api/auth/token-check")
@log_request(show_response=False)
def api_auth_token_check():
    """检查令牌是否有效（前端用）"""
    return jsonify({"enabled": _API_TOKEN_ENABLED, "valid": True})


@app.route("/api/config", methods=["GET", "POST"])
@require_token
@log_request()
def api_config():
    """获取或设置 LLM 配置"""
    global _CHAT_HISTORY
    if request.method == "GET":
        return jsonify(_Yunshu.get_config())

    data = request.get_json() or {}
    provider = data.get("provider", "")

    # 检查依赖库
    if provider == "anthropic":
        try:
            import anthropic  # noqa
        except ImportError:
            return jsonify({"ok": False, "error": "缺少依赖库: anthropic。请执行: pip install anthropic"})
    elif provider in ("openai", "deepseek"):
        try:
            import openai  # noqa
        except ImportError:
            return jsonify({"ok": False, "error": "缺少依赖库: openai。请执行: pip install openai"})

    result = _Yunshu.configure_llm(
        provider=data.get("provider", ""),
        api_key=data.get("api_key", ""),
        model=data.get("model", ""),
    )
    if result.get("ok"):
        _session_mgr.clear_messages(_get_current_session_id())
        _CHAT_HISTORY.clear()
    return jsonify(result)


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


@app.route("/api/context/status")
@log_request(show_response=False)
def api_context_status():
    """获取当前上下文使用状态"""
    session_id = _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=0)  # 全部消息

    counter = _get_token_counter()
    send_tokens = 0
    recv_tokens = 0
    recent = []

    for msg in messages:
        content = msg.get("content", "") or ""
        tokens = counter.count(content)
        role = msg.get("role", "")
        if role == "user":
            send_tokens += tokens
        elif role == "assistant":
            recv_tokens += tokens
        recent.append({
            "role": role,
            "tokens": tokens,
            "content_preview": content[:60],
        })

    # 只保留最近 10 条用于展示
    recent = recent[-10:]

    total = send_tokens + recv_tokens
    limit = _cfg.get("memory", "token_limit", default=4096)
    pct = round(total / limit * 100, 1) if limit > 0 else 0

    # 压缩次数
    compress_rounds = 0
    try:
        compress_rounds = getattr(_Yunshu._memory, 'compress_rounds', 0)
        if callable(compress_rounds):
            compress_rounds = compress_rounds()
    except Exception:
        pass

    # 上下文状态级别
    compress_warn = compress_rounds >= 3
    compress_crit = compress_rounds >= 5
    pct_warn = pct >= 80 or pct >= 60
    pct_crit = pct >= 95
    if pct_crit or compress_crit:
        status_level = "critical"
    elif pct >= 80 or compress_warn:
        status_level = "warning"
    elif pct >= 60:
        status_level = "info"
    else:
        status_level = "ok"

    return jsonify({
        "current_tokens": total,
        "token_limit": limit,
        "percentage": pct,
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=2048),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=4096),
        "compress_threshold": _cfg.get("memory", "compress_threshold", default=0.8),
        "compress_rounds": compress_rounds,
        "status_level": status_level,
        "send_tokens": send_tokens,
        "recv_tokens": recv_tokens,
        "messages_count": len(messages),
        "recent_messages": recent,
    })


@app.route("/api/context/config", methods=["POST"])
@require_token
def api_context_config():
    """更新上下文控制参数"""
    data = request.get_json() or {}
    changed = []

    if "token_limit" in data:
        val = int(data["token_limit"])
        val = max(512, min(32768, val))
        _cfg.set(val, "memory", "token_limit")
        changed.append("token_limit")

    if "per_message_send_limit" in data:
        val = int(data["per_message_send_limit"])
        val = max(0, min(32768, val))
        _cfg.set(val, "memory", "per_message_send_limit")
        changed.append("per_message_send_limit")

    if "per_message_recv_limit" in data:
        val = int(data["per_message_recv_limit"])
        val = max(0, min(32768, val))
        _cfg.set(val, "memory", "per_message_recv_limit")
        changed.append("per_message_recv_limit")

    if changed:
        logger.info(f"上下文配置已更新: {', '.join(changed)}")

    return jsonify({
        "ok": True,
        "changed": changed,
        "token_limit": _cfg.get("memory", "token_limit", default=4096),
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=2048),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=4096),
    })


@app.route("/api/context/compress", methods=["POST"])
@require_token
def api_context_compress():
    """手动触发上下文压缩"""
    try:
        result = _Yunshu._memory.compress()
        return jsonify({
            "ok": True,
            "freed_tokens": result.get("freed", 0),
            "current_tokens": result.get("current", 0),
        })
    except Exception as e:
        logger.warning(f"手动压缩失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  全景 API
# ════════════════════════════════════════════════════════════

@app.route("/api/panorama")
@log_request(show_response=False)
def api_panorama():
    """获取全景页面所需的所有数据（单次调用）"""
    readings = _Yunshu.body.collect_quick()
    reading_dicts = [r.to_dict() for r in readings]
    mode = _Yunshu.get_behavior_mode()
    profile = _Yunshu._behavior.profile
    sensor_info = _Yunshu.body.get_sensor_info()
    summary = _Yunshu._memory.load_summary()
    config = _Yunshu.get_config()
    started_at = getattr(_Yunshu, '_started_at', None)

    # 认知状态
    cognitive_summary = _Yunshu._injector.get_summary(reading_dicts)

    # 记忆统计
    try:
        logs = _Yunshu._memory._black_box.analyze()
        log_count = sum(logs.values()) if isinstance(logs, dict) else 0
    except Exception:
        log_count = 0

    # 最近消息数（从 storage 加载）
    try:
        recent = _Yunshu._memory._storage.load_recent_messages(limit=1)
        total_msgs = len(recent) if recent else 0
        # 尝试获取实际总数
        try:
            with open(_Yunshu._memory._storage.messages_file, 'r', encoding='utf-8') as f:
                total_msgs = sum(1 for _ in f)
        except Exception:
            pass
    except Exception:
        total_msgs = 0

    # 构建交互追踪
    last_trace = []
    if _session_mgr.get_current_id():
        last_msgs = _session_mgr.get_messages(_session_mgr.get_current_id(), limit=1)
        if last_msgs:
            last = last_msgs[-1]
            mode_label = 'normal'
            last_trace = [
                {"phase": 1, "phase_label": "感知", "icon": "👁", "text": f"CPU {readings[0].value if readings else '?'}%, 内存 {readings[1].value if len(readings)>1 else '?'}%"},
                {"phase": 2, "phase_label": "认知", "icon": "🧠", "text": cognitive_summary[:60]},
                {"phase": 3, "phase_label": "记忆", "icon": "💾", "text": f"加载摘要·{total_msgs} 条历史"},
                {"phase": 4, "phase_label": "行动", "icon": "🤖", "text": f"模式: {mode_label} → 调用 LLM → 生成响应"},
            ]
        else:
            last_trace = []
    else:
        last_trace = []

    return jsonify({
        # 阶段一
        "health": [r.to_dict() for r in readings],
        "sensor_on": sum(1 for s in sensor_info if s.get("enabled")),
        "sensor_total": len(sensor_info),
        "sensor_categories": _get_sensor_categories(),
        "tag_dimensions": _get_tag_dimensions(),
        "sensor_list": sensor_info,
        # 阶段二
        "cognitive_summary": cognitive_summary,
        "can_accept": not _Yunshu._injector.should_reject_task(reading_dicts)[0],
        "translate_rules": _get_translate_rules(),
        "prompt_template": _get_prompt_template(),
        # 阶段三
        "summary_version": summary[1] if summary else None,
        "summary_text": summary[0][:500] if summary and summary[0] else None,
        "message_count": total_msgs,
        "log_count": log_count,
        "log_stats": logs if isinstance(logs, dict) else {},
        "compress_threshold": _cfg.get("memory", "compress_threshold", default=0.8),
        "token_limit": _cfg.get("memory", "token_limit", default=4096),
        # 阶段四
        "mode": mode.value,
        "mode_label": profile.label,
        "tool_count": len(list_tools()),
        "tool_list": list_tools(),
        "reflection_count": len(_Yunshu._reflection_history),
        "llm_configured": config.get("configured", False),
        "behavior_modes": _get_behavior_modes(),
        "permission_info": _get_permission_info(),
        # 系统
        "session_id": _Yunshu._session_id,
        "interaction_count": _Yunshu._interaction_count,
        "started_at": started_at,
        # 追踪
        "last_trace": last_trace,
    })


def _get_sensor_categories():
    """获取传感器五大分类（含数据来源）"""
    # 五大分类映射 + 数据来源
    CAT_CONFIG = {
        "硬件感知": {
            "icon": "💻", "sensors": ["cpu","gpu","memory","disk","battery","board","chassis","port","peripheral"],
            "source": "🔬 从硬件直接读取 （WMI/寄存器/传感器）",
        },
        "网络感知": {
            "icon": "🌐", "sensors": ["network"],
            "source": "🔬 从硬件直接读取 （网卡/协议栈）",
        },
        "进程与行为": {
            "icon": "⚙️", "sensors": ["process","activity","behavior"],
            "source": "⚡ 推测得来 （系统调用/性能计数器）",
        },
        "文件感知": {
            "icon": "📁", "sensors": ["file","change","hwfile"],
            "source": "🖥️ 从软件获得 （文件系统 API/快照对比）",
        },
        "系统与环境": {
            "icon": "🌿", "sensors": ["environment","system"],
            "source": "🖥️ 从软件获得 （OS 环境变量/系统 API）",
        },
    }
    # 反向映射: category → 分类名
    cat_reverse = {}
    for group_name, cfg in CAT_CONFIG.items():
        for sc in cfg["sensors"]:
            cat_reverse[sc] = group_name

    sensor_info = _Yunshu.body.get_sensor_info()
    grouped = {}
    for group_name in CAT_CONFIG:
        grouped[group_name] = {
            "name": f"{CAT_CONFIG[group_name]['icon']} {group_name}",
            "source": CAT_CONFIG[group_name]["source"],
            "count": 0,
            "sensors": [],
        }

    # 导入标签计算函数
    try:
        from sensor.tags import get_tags
    except Exception:
        get_tags = None

    for s in sensor_info:
        cat = s.get("category", "")
        group = cat_reverse.get(cat, "📡 其他")
        if group not in grouped:
            continue
        grouped[group]["count"] += 1
        sensor_tags = []
        if get_tags:
            try:
                sensor_tags = get_tags(cat, s.get("name", ""))
            except Exception:
                pass
        grouped[group]["sensors"].append({
            "name": s.get("label", s.get("name", "")),
            "key": s.get("name", ""),
            "enabled": s.get("enabled", True),
            "tags": sensor_tags,
        })

    return list(grouped.values())


def _get_tag_dimensions():
    """获取八大维度（硬编码，与 tags.py 同步）"""
    return [
        {"label": "目标域", "values": ["硬件感知", "软件感知", "行为感知", "环境感知"]},
        {"label": "内外方位", "values": ["内部感知", "外部感知", "边界感知"]},
        {"label": "动静属性", "values": ["静态配置", "动态运行", "增量变化"]},
        {"label": "采集方式", "values": ["主动探测", "被动监听", "系统查询", "对比检测"]},
        {"label": "感知层次", "values": ["物理层", "系统层", "应用层"]},
        {"label": "功能角色", "values": ["基础生存", "性能监控", "安全防护", "社交通信", "环境适应"]},
        {"label": "数据特征", "values": ["数值量", "状态量", "事件量", "配置量"]},
        {"label": "可干预性", "values": ["仅可观测", "可配置"]},
    ]


def _get_translate_rules():
    """获取翻译规则摘要"""
    try:
        rules = _Yunshu._injector.config.get_all_rules()
        result = []
        for name, rule in rules.items():
            thresholds = rule.get("thresholds", [])
            first = thresholds[0] if thresholds else {}
            result.append({
                "name": name,
                "message": first.get("message", rule.get("description", name)),
                "unit": rule.get("unit", ""),
            })
        return result[:8]
    except Exception:
        return []


def _get_prompt_template():
    """获取提示词模板"""
    try:
        from cognitive.templates import DEFAULT_TEMPLATE
        return DEFAULT_TEMPLATE[:500]
    except Exception:
        return ""


def _get_behavior_modes():
    """获取六种行为模式"""
    current_mode = _Yunshu.get_behavior_mode().value
    mode_info = {
        "normal": {"label": "正常模式", "desc": "全能力运行", "color": "#3fb950"},
        "safe": {"label": "安全模式", "desc": "CPU过热·拒绝高耗能", "color": "#f85149"},
        "power_save": {"label": "省电模式", "desc": "电量不足·降推理", "color": "#d29922"},
        "memory_compact": {"label": "整理模式", "desc": "内存紧张·触发压缩", "color": "#bc8cff"},
        "offline": {"label": "离线模式", "desc": "网络中断·本地逻辑", "color": "#8b949e"},
        "warning": {"label": "预警模式", "desc": "磁盘不足·提示清理", "color": "#db6d28"},
    }
    result = []
    for key, info in mode_info.items():
        active = key == current_mode
        result.append({
            "key": key,
            "label": info["label"],
            "desc": info["desc"],
            "color": info["color"] if active else "#30363d",
            "active": active,
        })
    return result


def _get_permission_info():
    """获取权限系统统计"""
    try:
        perm = _Yunshu._permission
        logs = perm.get_permission_log()
        import os
        backup_dir = getattr(perm, '_backup_dir', None)
        backup_count = 0
        if backup_dir and os.path.isdir(backup_dir):
            backup_count = len(os.listdir(backup_dir))
        return {
            "check_count": len(logs),
            "backup_count": backup_count,
            "backup_dir": str(backup_dir) if backup_dir else "-",
        }
    except Exception:
        return {}


# ════════════════════════════════════════════════════════════
#  人格配置 API
# ════════════════════════════════════════════════════════════

@app.route("/api/personality", methods=["GET"])
@log_request(show_response=False)
def api_personality_get():
    return jsonify(_personality_mgr.get())

@app.route("/api/personality/params", methods=["POST"])
@require_token
@log_request()
def api_personality_params():
    data = request.get_json() or {}
    params = data.get("params", {})
    result = _personality_mgr.update_params(params)
    return jsonify(result)

@app.route("/api/personality/profile", methods=["POST"])
@require_token
@log_request()
def api_personality_profile():
    data = request.get_json() or {}
    profile = data.get("profile", "")
    result = _personality_mgr.apply_profile(profile)
    return jsonify(result)

@app.route("/api/personality/reset", methods=["POST"])
@require_token
@log_request()
def api_personality_reset():
    result = _personality_mgr.reset()
    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  系统身份提示词 API
# ════════════════════════════════════════════════════════════

from agent.system_prompt_manager import get_template, save_template, reset_template, has_custom_template, get_placeholder_descriptions, DEFAULT_TEMPLATE

@app.route("/api/system-prompt", methods=["GET"])
@log_request(show_response=False)
def api_system_prompt_get():
    """获取系统提示词模板及预览信息"""
    template = get_template()
    placeholders = get_placeholder_descriptions()

    # 生成预览：尝试代入示例值
    try:
        preview = template.format(
            current_date=datetime.datetime.now().strftime("%Y年%m月%d日"),
            body_status="🟢 CPU: 32°C | 内存: 45% | 磁盘: 128G/512G | 电池: 充电中",
            mode_name="对话",
            mode_description="日常交流模式",
            memory_context="（暂无记忆内容）",
            tool_status="web_search: 启用 | file_read: 启用 | ...",
            skill_instructions="",
        )
    except KeyError:
        preview = "（模板包含未知占位符，请检查语法）"
    except Exception as e:
        preview = f"（渲染错误: {e}）"

    return jsonify({
        "template": template,
        "is_custom": has_custom_template(),
        "is_default": not has_custom_template(),
        "placeholders": placeholders,
        "preview": preview,
    })


@app.route("/api/system-prompt", methods=["POST"])
@require_token
@log_request()
def api_system_prompt_save():
    """保存自定义系统提示词模板"""
    data = request.get_json() or {}
    content = data.get("content", "")

    if not content or not content.strip():
        return jsonify({"ok": False, "error": "内容不能为空"}), 400

    # 验证占位符正确性
    try:
        content.format(
            current_date="测试",
            body_status="测试",
            mode_name="测试",
            mode_description="测试",
            memory_context="测试",
            tool_status="测试",
            skill_instructions="",
        )
    except KeyError as e:
        return jsonify({
            "ok": False,
            "error": f"模板中包含未知占位符: {e}。可用占位符: {', '.join(get_placeholder_descriptions().keys())}"
        }), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"模板语法错误: {e}"}), 400

    success = save_template(content)
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "保存失败"}), 500


@app.route("/api/system-prompt/reset", methods=["POST"])
@require_token
@log_request()
def api_system_prompt_reset():
    """重置系统提示词为默认"""
    success = reset_template()
    if success:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "重置失败"}), 500


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
#  技能配置 API
# ════════════════════════════════════════════════════════════

@app.route("/api/skills", methods=["GET"])
@log_request(show_response=False)
def api_skills_get():
    """获取技能列表（分类：已安装 + 可安装的内置技能）"""
    installed = _skills_mgr.get_all()
    installed_ids = {s["id"] for s in installed}

    # 从扩展存储获取额外已安装的扩展（通过 ext_install 安装的技能和 claude_skill）
    try:
        from agent.extensions.store import ExtensionStore
        from agent.extensions.base import ExtensionType
        ext_store = ExtensionStore()
        for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
            for ext in ext_store.list_all(ext_type):
                ext_id = ext.get("ext_id", "")
                if ext_id and ext_id not in installed_ids:
                    installed.append({
                        "id": ext_id,
                        "name": ext.get("name", ext_id),
                        "enabled": ext.get("status") in ("enabled", "installed"),
                        "description": ext.get("description", ""),
                        "params": ext.get("config", {}),
                        "source": "extension_store",
                    })
                    installed_ids.add(ext_id)
    except Exception:
        pass

    # 从内置注册表获取所有可用的技能
    try:
        from agent.extensions.base import BUILTIN_EXTENSIONS
        builtin_list = BUILTIN_EXTENSIONS.get("skill", [])
    except ImportError:
        builtin_list = []

    # 标记已安装状态
    available = []
    for s in builtin_list:
        available.append({
            "id": s["id"],
            "name": s["name"],
            "description": s.get("description", ""),
            "installed": s["id"] in installed_ids,
            "builtin": s.get("builtin", False),
        })

    return jsonify({
        "installed": installed,
        "available": available,
    })

@app.route("/api/skills/toggle", methods=["POST"])
@require_token
@log_request()
def api_skills_toggle():
    data = request.get_json() or {}
    skill_id = data.get("id", "")
    result = _skills_mgr.toggle(skill_id)
    new_enabled = result.get("enabled", True)
    # 直接写入全部三个文件，确保 ext_list 和 UI 数据一致
    try:
        import json
        all_skill_ids = ['self_reflection','memory_summary','emotion_expression',
                         'proactive_suggestion','context_aware','safety_guard','voice_interaction']
        # 重新读取完整状态
        skills_file = os.path.join(os.path.dirname(__file__), 'data', 'skills.json')
        if os.path.exists(skills_file):
            with open(skills_file, 'r', encoding='utf-8') as f:
                all_skills = json.load(f)
        else:
            all_skills = {"skills": []}
        skill_names = {s["id"]: s.get("name", s["id"]) for s in all_skills.get("skills", [])}
        skill_descs = {s["id"]: s.get("description", "") for s in all_skills.get("skills", [])}

        # 构建完整技能列表
        skills_list = []
        for sid in all_skill_ids:
            s_enabled = True
            for s in all_skills.get("skills", []):
                if s["id"] == sid:
                    s_enabled = s.get("enabled", True)
                    break
            skills_list.append({
                "id": sid, "name": skill_names.get(sid, sid),
                "enabled": s_enabled, "description": skill_descs.get(sid, ""),
                "params": {}
            })
        skills_data = {"skills": skills_list}

        # 1) 写入 root data/skills.json（UI 读取）
        with open(skills_file, 'w', encoding='utf-8') as f:
            json.dump(skills_data, f, ensure_ascii=False, indent=2)

        # 2) 写入 agent/data/skills.json
        os.makedirs('agent/data', exist_ok=True)
        with open('agent/data/skills.json', 'w', encoding='utf-8') as f:
            json.dump(skills_data, f, ensure_ascii=False, indent=2)

        # 3) 写入 agent/data/extensions.json（ext_list 读取）
        ext_skills = []
        for s in skills_list:
            ext_skills.append({
                "ext_id": s["id"], "ext_type": "skill",
                "name": s["name"], "description": s["description"],
                "status": "enabled" if s["enabled"] else "disabled",
                "source": "builtin",
            })
        with open('agent/data/extensions.json', 'w', encoding='utf-8') as f:
            json.dump({
                "skills": ext_skills, "claude_skills": [],
                "mcps": [], "channels": [], "plugins": [],
            }, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error("[SKILL_SYNC] 同步扩展注册表失败: %s", e)
    return jsonify(result)

@app.route("/api/skills/params", methods=["POST"])
@require_token
@log_request()
def api_skills_params():
    data = request.get_json() or {}
    return jsonify(_skills_mgr.update_params(data.get("id", ""), data.get("params", {})))

@app.route("/api/skills/add", methods=["POST"])
@require_token
@log_request()
def api_skills_add():
    return jsonify(_skills_mgr.add(request.get_json() or {}))

@app.route("/api/skills/delete", methods=["POST"])
@require_token
@log_request()
def api_skills_delete():
    data = request.get_json() or {}
    skill_id = data.get("id", "")

    # 内置技能不可删除
    try:
        from agent.extensions.base import BUILTIN_EXTENSIONS
        for s in BUILTIN_EXTENSIONS.get("skill", []):
            s_id = s.get("id", "")
            if s_id == skill_id and s.get("builtin", False):
                return jsonify({"ok": False, "error": "内置技能不可删除"})
    except Exception:
        pass

    # 从 skills.json 删除
    result = _skills_mgr.delete(skill_id)
    deleted = result.get("ok", False)

    # 尝试从扩展存储删除（覆盖 Claude Code 技能等）
    try:
        from agent.extensions.store import ExtensionStore
        from agent.extensions.base import ExtensionType
        ext_store = ExtensionStore()
        for ext_type in (ExtensionType.SKILL, ExtensionType.CLAUDE_SKILL):
            if ext_store.remove(ext_type, skill_id):
                deleted = True
                # 如果是 Claude Code 技能，清理磁盘文件
                if ext_type == ExtensionType.CLAUDE_SKILL:
                    import shutil
                    claude_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills", skill_id)
                    if os.path.exists(claude_dir):
                        shutil.rmtree(claude_dir, ignore_errors=True)
    except Exception:
        pass

    if deleted:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": f"未找到技能: {skill_id}"})


# ── 网络配置管理器 ──
from agent.network_config import NetworkConfigManager, _DEFAULT_SEARCH_INSTANCE

# 传入 secure_manager 以确保 API Key 加密存储和正确恢复
try:
    from config import _get_secure_manager
    _network_config_mgr = NetworkConfigManager(secure_manager=_get_secure_manager())
except Exception:
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
#  扩展系统 API
# ════════════════════════════════════════════════════════════

@app.route("/api/extensions/list", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_extensions_list():
    """列出所有已安装扩展"""
    try:
        ext_type = request.args.get("type")
        result = _extension_mgr.list_all(ext_type)
        return jsonify({"ok": True, "extensions": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/installed", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_extensions_installed():
    """按类型分组获取已安装扩展"""
    try:
        result = _extension_mgr.get_installed_by_type()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/install", methods=["POST"])
@require_token
@log_request()
def api_extensions_install():
    """安装扩展"""
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        source = data.get("source", data.get("id", ""))
        kwargs = data.get("params", {})

        if not ext_type or not source:
            return jsonify({"ok": False, "error": "缺少 type 或 source/id"}), 400

        result = _extension_mgr.install(ext_type, source, **kwargs)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/uninstall", methods=["POST"])
@require_token
@log_request()
def api_extensions_uninstall():
    """卸载扩展"""
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.uninstall(ext_type, ext_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/toggle", methods=["POST"])
@require_token
@log_request()
def api_extensions_toggle():
    """启用/禁用扩展"""
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")
        enabled = data.get("enabled")  # None 表示切换

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.toggle(ext_type, ext_id, enabled)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/configure", methods=["POST"])
@require_token
@log_request()
def api_extensions_configure():
    """配置扩展参数"""
    try:
        data = request.get_json() or {}
        ext_type = data.get("type", "")
        ext_id = data.get("id", "")
        config = data.get("config", {})

        if not ext_type or not ext_id:
            return jsonify({"ok": False, "error": "缺少 type 或 id"}), 400

        result = _extension_mgr.configure(ext_type, ext_id, config)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/discover", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_extensions_discover():
    """发现所有可用扩展"""
    try:
        result = _extension_mgr.discover_all()
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/market/search", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_extensions_market_search():
    """搜索扩展市场"""
    try:
        query = request.args.get("q", "")
        ext_type = request.args.get("type")
        include_github = request.args.get("github", "true").lower() == "true"

        result = _extension_market.search_all(query, ext_type, include_github)
        return jsonify({"ok": True, **result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/market/recommend", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_extensions_market_recommend():
    """获取推荐扩展"""
    try:
        ext_type = request.args.get("type")
        limit = request.args.get("limit", 5, type=int)
        result = _extension_market.get_recommendations(ext_type, limit)
        return jsonify({"ok": True, "recommendations": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/market/refresh", methods=["POST"])
@require_token
@log_request()
def api_extensions_market_refresh():
    """刷新社区扩展索引"""
    try:
        result = _extension_market.fetch_community_index()
        if result:
            return jsonify({"ok": True, "count": len(result)})
        return jsonify({"ok": False, "error": "获取索引失败"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/extensions/channels/send", methods=["POST"])
@require_token
@log_request()
def api_extensions_channel_send():
    """通过通道发送消息"""
    try:
        data = request.get_json() or {}
        channel_id = data.get("channel_id", "")
        message = data.get("message", "")
        kwargs = data.get("params", {})

        if not channel_id or not message:
            return jsonify({"ok": False, "error": "缺少 channel_id 或 message"}), 400

        result = _extension_mgr.send_channel_message(channel_id, message, **kwargs)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  网络配置 API
# ════════════════════════════════════════════════════════════

@app.route("/api/network-config", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_network_config_get():
    """获取网络配置"""
    return jsonify(_network_config_mgr.get_all())


@app.route("/api/network-config", methods=["POST"])
@require_token
@log_request()
def api_network_config_update():
    """更新网络配置"""
    import time as _time
    t0 = _time.time()
    data = request.get_json() or {}
    try:
        # 记录保存前的 priority（便于排查排序不生效问题）
        before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        result = _network_config_mgr.update(data)
        # 即时生效：将配置应用到应用实例
        _network_config_mgr.apply_to_app(_Yunshu)
        after = result.get('search', {}).get('engine_priority', [])
        _log_struct(
            'api_network_config_update.done',
            '网络配置已更新',
            duration_ms=int((_time.time() - t0) * 1000),
            priority_before=before,
            priority_after=after,
            priority_changed=before != after,
            default_engine=result.get('search', {}).get('default_engine', ''),
        )
        return jsonify({"ok": True, "config": result})
    except Exception as e:
        _log_struct(
            'api_network_config_update.failed',
            f'更新失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/network-config/reset", methods=["POST"])
@require_token
@log_request()
def api_network_config_reset():
    """重置网络配置为默认值"""
    result = _network_config_mgr.reset()
    return jsonify({"ok": True, "config": result})


@app.route("/api/network-config/export", methods=["GET"])
@require_token
@log_request()
def api_network_config_export():
    """导出网络配置（脱敏）"""
    try:
        json_str = _network_config_mgr.export_config()
        return jsonify({"ok": True, "config_json": json_str})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/network-config/import", methods=["POST"])
@require_token
@log_request()
def api_network_config_import():
    """导入网络配置"""
    data = request.get_json() or {}
    json_str = data.get("config_json", "")
    if not json_str:
        return jsonify({"ok": False, "error": "缺少 config_json"}), 400

    try:
        result = _network_config_mgr.import_config(json_str)
        # 即时生效
        _network_config_mgr.apply_to_app(_Yunshu)
        return jsonify({"ok": True, "config": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/apply-network-config", methods=["POST"])
@require_token
@log_request()
def api_apply_network_config():
    """应用网络配置到应用实例（即时生效）"""
    try:
        logger.info("[网络配置] 手动触发配置应用...")
        _network_config_mgr.apply_to_app(_Yunshu)
        
        # 同时应用到全局搜索引擎实例 _web_search
        config = _network_config_mgr.get_raw_config()
        search_config = config.get('search', {})
        search_api_keys = config.get('search_api_keys', {})
        
        update_config = {
            'engine_priority': search_config.get('engine_priority', ['duckduckgo', 'tavily']),
            'engine_enabled': search_config.get('engine_enabled', {}),
            'timeout': search_config.get('timeout', 30),
            'default_engine': search_config.get('default_engine', 'duckduckgo'),
        }
        
        # 添加 API Keys
        for key_name in ['tavily', 'bing', 'google', 'google_cx', 'brave']:
            if search_api_keys.get(key_name):
                update_config[f'{key_name}_api_key' if key_name != 'google_cx' else 'google_cx'] = search_api_keys[key_name]
        
        _web_search.update_config(update_config)

        # 注册搜索实例到全局引擎
        _network_config_mgr.apply_search_instances(_web_search)
        # 同步 DigitalLife 的搜索引擎实例
        _Yunshu._web_search = _web_search
        logger.info("[网络配置] 已同时应用到全局搜索引擎实例")
        
        # 返回搜索引擎配置状态供前端验证
        search_config_status = _network_config_mgr.get_search_engines()
        return jsonify({
            "ok": True,
            "message": "配置已即时生效",
            "search_config": search_config_status,
        })
    except Exception as e:
        logger.error("[网络配置] 应用配置失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  LLM 实例管理 API
# ════════════════════════════════════════════════════════════

@app.route("/api/llm/instances", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_llm_instances_get():
    """获取所有 LLM 实例"""
    try:
        instances = _network_config_mgr.get_llm_instances()
        return jsonify({"ok": True, "instances": instances})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances/<string:instance_id>", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_llm_instance_get(instance_id):
    """获取单个 LLM 实例"""
    try:
        instance = _network_config_mgr.get_llm_instance(instance_id)
        if instance:
            return jsonify({"ok": True, "instance": instance})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_add():
    """添加 LLM 实例"""
    try:
        data = request.get_json() or {}
        instance = data.get("instance", {})
        
        # 验证配置
        errors = _network_config_mgr.validate_llm_instance(instance)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        
        result = _network_config_mgr.add_llm_instance(instance)
        return jsonify({"ok": True, "instance": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances/<string:instance_id>", methods=["PUT"])
@require_token
@log_request()
def api_llm_instance_update(instance_id):
    """更新 LLM 实例"""
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        
        result = _network_config_mgr.update_llm_instance(instance_id, updates)
        if result:
            return jsonify({"ok": True, "instance": result})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances/<string:instance_id>", methods=["DELETE"])
@require_token
@log_request()
def api_llm_instance_delete(instance_id):
    """删除 LLM 实例"""
    try:
        success = _network_config_mgr.delete_llm_instance(instance_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances/<string:instance_id>/default", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_set_default(instance_id):
    """设置默认 LLM 实例"""
    try:
        success = _network_config_mgr.set_default_llm_instance(instance_id)
        if success:
            return jsonify({"ok": True, "message": "已设置为默认实例"})
        return jsonify({"ok": False, "error": "操作失败"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/llm/instances/<string:instance_id>/test", methods=["POST"])
@require_token
@log_request()
def api_llm_instance_test(instance_id):
    """测试 LLM 实例连接"""
    try:
        config = _network_config_mgr.get_raw_config()
        inst = next((i for i in config.get('llm_instances', []) if i.get('id') == instance_id), None)
        if not inst:
            return jsonify({"ok": False, "error": "实例不存在"}), 404

        provider = inst.get('provider', 'openai')
        api_key = inst.get('api_key', '')
        model = inst.get('model', 'gpt-4')
        base_url = inst.get('api_endpoint', '') or None
        timeout = inst.get('timeout', 30)

        if not api_key:
            return jsonify({"ok": False, "error": "API Key 未配置"})

        try:
            from memory.llm_service import LLMService
            llm = LLMService(
                provider=provider, api_key=api_key,
                model=model, base_url=base_url,
                timeout=timeout,
            )
            import time
            t0 = time.time()
            resp = llm.chat(
                messages=[{"role": "user", "content": "回复'OK'"}],
                max_tokens=10, temperature=0.1,
            )
            elapsed = round(time.time() - t0, 2)
            return jsonify({
                "ok": True,
                "elapsed": elapsed,
                "model": model,
                "provider": provider,
                "response": (resp or '')[:100],
            })
        except Exception as e:
            return jsonify({"ok": False, "error": f"连接失败: {e}"})
    except Exception as e:
        logger.error("[LLM 实例] 测试失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  搜索引擎实例管理 API
# ════════════════════════════════════════════════════════════

from agent.server_routes.routes_config import validate_search_instance as _validate_search_instance


@app.route("/api/search/instances", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_search_instances_get():
    try:
        config = _network_config_mgr.get_all()
        return jsonify({"ok": True, "instances": config.get('search_instances', [])})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search/instances", methods=["POST"])
@require_token
@log_request()
def api_search_instance_add():
    import time as _time
    t0 = _time.time()
    try:
        data = request.get_json() or {}
        instance = data.get("instance", {})
        errors = _validate_search_instance(instance)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400

        priority_before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        config = _network_config_mgr.get_raw_config()
        new_inst = dict(_DEFAULT_SEARCH_INSTANCE)
        new_inst.update(instance)
        new_inst['id'] = str(uuid.uuid4())
        new_inst['created_at'] = datetime.datetime.now().isoformat()
        new_inst['updated_at'] = new_inst['created_at']

        api_key = new_inst.get('api_key', '')
        if api_key and not api_key.startswith('***'):
            _network_config_mgr._save_secure(f'search_{new_inst["id"]}_api_key', api_key)

        config.setdefault('search_instances', []).append(new_inst)
        _network_config_mgr._save(config)
        _network_config_mgr._add_change_log('add', 'search_instance', {'id': new_inst['id'], 'name': new_inst['name']})
        if _web_search:
            _network_config_mgr._register_search_instance(new_inst, _web_search)
            _network_config_mgr.apply_search_instances(_web_search)

        priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        _log_struct(
            'api_search_instance_add.done',
            '搜索实例已新增',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=new_inst['id'],
            instance_name=new_inst.get('name', ''),
            engine_type=new_inst.get('engine_type', ''),
            priority_before=priority_before,
            priority_after=priority_after,
        )

        # 返回前脱敏 api_key（避免明文返回前端）
        resp_inst = dict(new_inst)
        if resp_inst.get('api_key'):
            resp_inst['api_key'] = '***' + resp_inst['api_key'][-4:] if len(resp_inst['api_key']) > 4 else '***'
        return jsonify({"ok": True, "instance": resp_inst})
    except Exception as e:
        _log_struct(
            'api_search_instance_add.failed',
            f'新增搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search/instances/<string:instance_id>", methods=["PUT"])
@require_token
@log_request()
def api_search_instance_update(instance_id):
    import time as _time
    t0 = _time.time()
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        priority_before = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
        config = _network_config_mgr.get_raw_config()
        for inst in config.get('search_instances', []):
            if inst.get('id') == instance_id:
                ak = updates.get('api_key', '')
                if ak and ak != '***' and not ak.startswith('***'):
                    _network_config_mgr._save_secure(f'search_{instance_id}_api_key', ak)
                # 移除 api_key 字段，避免脱敏值/明文写入缓存（_save 会再次剥离，这里防御性处理）
                updates_clean = {k: v for k, v in updates.items() if k != 'api_key'}
                inst.update(updates_clean)
                inst['updated_at'] = datetime.datetime.now().isoformat()
                _network_config_mgr._save(config)
                _network_config_mgr._add_change_log('update', 'search_instance', {'id': instance_id, 'name': inst.get('name')})
                if _web_search:
                    _network_config_mgr.apply_search_instances(_web_search)
                priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
                _log_struct(
                    'api_search_instance_update.done',
                    '搜索实例已更新',
                    duration_ms=int((_time.time() - t0) * 1000),
                    instance_id=instance_id,
                    instance_name=inst.get('name', ''),
                    updated_fields=list(updates_clean.keys()),
                    priority_before=priority_before,
                    priority_after=priority_after,
                    priority_changed=priority_before != priority_after,
                )
                # 返回前脱敏 api_key
                resp_inst = dict(inst)
                if resp_inst.get('api_key'):
                    resp_inst['api_key'] = '***' + resp_inst['api_key'][-4:] if len(resp_inst['api_key']) > 4 else '***'
                return jsonify({"ok": True, "instance": resp_inst})
        _log_struct(
            'api_search_instance_update.not_found',
            f'搜索实例不存在: {instance_id}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
        )
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        _log_struct(
            'api_search_instance_update.failed',
            f'更新搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search/instances/<string:instance_id>", methods=["DELETE"])
@require_token
@log_request()
def api_search_instance_delete(instance_id):
    import time as _time
    t0 = _time.time()
    try:
        config = _network_config_mgr.get_raw_config()
        before = len(config.get('search_instances', []))
        priority_before = config.get('search', {}).get('engine_priority', [])
        config['search_instances'] = [i for i in config.get('search_instances', []) if i.get('id') != instance_id]
        if len(config['search_instances']) < before:
            # 修复：从 engine_priority 中移除已删除实例的 id（避免残留 UUID 导致前端空行/报错）
            config.setdefault('search', {})['engine_priority'] = [
                p for p in priority_before if p != instance_id
            ]
            # 修复：如果删除的是默认引擎，清理 default_engine 字段（避免指向不存在的实例）
            default_before = config.get('search', {}).get('default_engine', '')
            default_changed = False
            if default_before == instance_id:
                config['search']['default_engine'] = ''
                default_changed = True
            _network_config_mgr._save(config)
            _network_config_mgr._save_secure(f'search_{instance_id}_api_key', '')
            _network_config_mgr._add_change_log('delete', 'search_instance', {'id': instance_id})
            if _web_search:
                _web_search.remove_engine(instance_id)
                # 同步更新 web_search 工具的 engine enum + 重建 priority
                from agent.tools import sync_web_search_engines
                sync_web_search_engines([], search_engine=_web_search)
                _network_config_mgr.apply_search_instances(_web_search)
            priority_after = _network_config_mgr.get_all().get('search', {}).get('engine_priority', [])
            _log_struct(
                'api_search_instance_delete.done',
                '搜索实例已删除',
                duration_ms=int((_time.time() - t0) * 1000),
                instance_id=instance_id,
                priority_before=priority_before,
                priority_after=priority_after,
                priority_changed=priority_before != priority_after,
                default_engine_cleared=default_changed,
            )
            return jsonify({"ok": True})
        _log_struct(
            'api_search_instance_delete.not_found',
            f'搜索实例不存在: {instance_id}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
        )
        return jsonify({"ok": False, "error": "实例不存在"}), 404
    except Exception as e:
        _log_struct(
            'api_search_instance_delete.failed',
            f'删除搜索实例失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search/instances/<string:instance_id>/default", methods=["POST"])
@require_token
@log_request()
def api_search_instance_set_default(instance_id):
    import time as _time
    t0 = _time.time()
    try:
        config = _network_config_mgr.get_raw_config()
        default_before = config.get('search', {}).get('default_engine', '')
        inst = next((i for i in config.get('search_instances', []) if i.get('id') == instance_id), None)
        if not inst:
            _log_struct(
                'api_search_instance_set_default.not_found',
                f'搜索实例不存在: {instance_id}',
                duration_ms=int((_time.time() - t0) * 1000),
                instance_id=instance_id,
            )
            return jsonify({"ok": False, "error": "实例不存在"}), 404
        if _web_search:
            _web_search.set_default_engine(instance_id if inst.get('engine_type') == 'custom' else inst['engine_type'])
        for i in config.get('search_instances', []):
            i['is_default'] = (i.get('id') == instance_id)
        # 同步 default_engine 字段（确保 search.default_engine 与 is_default 一致）
        config.setdefault('search', {})['default_engine'] = instance_id
        _network_config_mgr._save(config)
        _network_config_mgr._add_change_log('update', 'search_instance', {'id': instance_id, 'action': 'set_default'})
        _log_struct(
            'api_search_instance_set_default.done',
            '已设为默认搜索引擎',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            instance_name=inst.get('name', ''),
            default_before=default_before,
            default_after=instance_id,
        )
        return jsonify({"ok": True, "message": "已设为默认搜索引擎"})
    except Exception as e:
        _log_struct(
            'api_search_instance_set_default.failed',
            f'设置默认搜索引擎失败: {e}',
            duration_ms=int((_time.time() - t0) * 1000),
            instance_id=instance_id,
            error=str(e),
        )
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search/instances/<string:instance_id>/test", methods=["POST"])
@require_token
@log_request()
def api_search_instance_test(instance_id):
    try:
        config = _network_config_mgr.get_raw_config()
        inst = next((i for i in config.get('search_instances', []) if i.get('id') == instance_id), None)
        if not inst or not _web_search:
            return jsonify({"ok": False, "error": "实例不存在或搜索引擎未初始化"}), (404 if not inst else 503)
        if inst.get('engine_type') == 'custom':
            result = _web_search._search_custom(inst, "test", num_results=2)
        else:
            # 调用专用 handler（如 _search_duckduckgo）
            handler = getattr(_web_search, f'_search_{inst["engine_type"]}', None)
            if handler:
                result = handler("test", num_results=2)
            else:
                result = _web_search.search(query="test", engine=inst.get('engine_type', ''), num_results=2)
        return jsonify({
            "ok": result.get("ok", False),
            "results": (result.get("results") or [])[:2],
            "total": result.get("total_estimate", 0),
            "engine": result.get("engine", ""),
            "error": result.get("error", ""),
        })
    except Exception as e:
        logger.error("[搜索实例] 测试失败: %s", e, exc_info=True)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  MCP 服务管理 API
# ════════════════════════════════════════════════════════════

@app.route("/api/mcp/services", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_mcp_services_get():
    """获取所有 MCP 服务"""
    try:
        services = _network_config_mgr.get_mcp_services()
        return jsonify({"ok": True, "services": services})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mcp/services/<string:service_id>", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_mcp_service_get(service_id):
    """获取单个 MCP 服务"""
    try:
        service = _network_config_mgr.get_mcp_service(service_id)
        if service:
            return jsonify({"ok": True, "service": service})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mcp/services", methods=["POST"])
@require_token
@log_request()
def api_mcp_service_add():
    """添加 MCP 服务"""
    try:
        data = request.get_json() or {}
        service = data.get("service", {})
        
        # 验证配置
        errors = _network_config_mgr.validate_mcp_service(service)
        if errors:
            return jsonify({"ok": False, "errors": errors}), 400
        
        result = _network_config_mgr.add_mcp_service(service)
        return jsonify({"ok": True, "service": result})
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mcp/services/<string:service_id>", methods=["PUT"])
@require_token
@log_request()
def api_mcp_service_update(service_id):
    """更新 MCP 服务"""
    try:
        data = request.get_json() or {}
        updates = data.get("updates", {})
        
        result = _network_config_mgr.update_mcp_service(service_id, updates)
        if result:
            return jsonify({"ok": True, "service": result})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mcp/services/<string:service_id>", methods=["DELETE"])
@require_token
@log_request()
def api_mcp_service_delete(service_id):
    """删除 MCP 服务"""
    try:
        success = _network_config_mgr.delete_mcp_service(service_id)
        if success:
            return jsonify({"ok": True})
        return jsonify({"ok": False, "error": "服务不存在"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/mcp/enable", methods=["POST"])
@require_token
@log_request()
def api_mcp_enable():
    """启用/禁用 MCP 服务"""
    try:
        data = request.get_json() or {}
        enabled = data.get("enabled", False)
        
        config = _network_config_mgr.get_raw_config()
        config['mcp']['enabled'] = enabled
        _network_config_mgr.update(config)
        
        return jsonify({"ok": True, "enabled": enabled})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  配置变更日志 API
# ════════════════════════════════════════════════════════════

@app.route("/api/config/logs", methods=["GET"])
@require_token
@log_request(show_response=False)
def api_config_logs():
    """获取配置变更日志"""
    try:
        limit = request.args.get("limit", 20, type=int)
        logs = _network_config_mgr.get_change_log(limit)
        return jsonify({"ok": True, "logs": logs})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 工具配置 API ──
@app.route("/api/tools/config", methods=["GET"])
@log_request(show_response=False)
def api_tools_config():
    """获取工具列表及使用统计"""
    from agent.tools import list_tools
    tools = list_tools()
    try:
        perm_logs = _Yunshu._permission.get_permission_log()
    except Exception:
        perm_logs = []
    result = []
    for t in tools:
        tool_name = t["name"]
        call_count = sum(1 for log in perm_logs if log.get("tool") == tool_name)
        result.append({
            "name": tool_name,
            "description": t.get("description", ""),
            "enabled": _get_tool_state(tool_name),
            "call_count": call_count,
            "last_used": None,
        })
    return jsonify(result)

@app.route("/api/tools/toggle", methods=["POST"])
@require_token
@log_request()
def api_tools_toggle():
    """切换工具启用状态"""
    data = request.get_json() or {}
    tool_name = data.get("name", "")
    enabled = data.get("enabled", True)
    _set_tool_state(tool_name, enabled)
    return jsonify({"ok": True, "name": tool_name, "enabled": enabled})


# ════════════════════════════════════════════════════════════
#  工具分类 & 路由关键词 API
# ════════════════════════════════════════════════════════════

@app.route("/api/tools/categories", methods=["GET"])
@log_request(show_response=False)
def api_tools_categories():
    from agent.tool_router import get_categorized_tools, get_keywords
    return jsonify({
        "categories": get_categorized_tools(),
        "keywords": get_keywords(),
    })

@app.route("/api/tools/keywords", methods=["POST"])
@require_token
@log_request()
def api_tools_keywords_add():
    data = request.get_json() or {}
    category = data.get("category", "")
    keyword = data.get("keyword", "").strip()
    if not category or not keyword:
        return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
    from agent.tool_router import add_keyword
    ok = add_keyword(category, keyword)
    return jsonify({"ok": ok})

@app.route("/api/tools/keywords", methods=["DELETE"])
@require_token
@log_request()
def api_tools_keywords_remove():
    data = request.get_json() or {}
    category = data.get("category", "")
    keyword = data.get("keyword", "").strip()
    if not category or not keyword:
        return jsonify({"ok": False, "error": "缺少 category 或 keyword"}), 400
    from agent.tool_router import remove_keyword
    ok = remove_keyword(category, keyword)
    return jsonify({"ok": ok})

@app.route("/api/tools/keywords/update", methods=["POST"])
@require_token
@log_request()
def api_tools_keywords_update():
    data = request.get_json() or {}
    category = data.get("category", "")
    old_kw = data.get("old_keyword", "").strip()
    new_kw = data.get("new_keyword", "").strip()
    if not category or not old_kw or not new_kw:
        return jsonify({"ok": False, "error": "缺少必要参数"}), 400
    from agent.tool_router import update_keyword
    ok = update_keyword(category, old_kw, new_kw)
    return jsonify({"ok": ok})

@app.route("/api/tools/keywords/reset", methods=["POST"])
@require_token
@log_request()
def api_tools_keywords_reset():
    from agent.tool_router import reset_keywords
    ok = reset_keywords()
    return jsonify({"ok": ok})


@app.route("/api/tools/health")
@log_request(show_response=False)
def api_tools_health():
    """获取工具健康状态（追踪、成功率、评分）"""
    from agent.tools import get_health_status
    return jsonify(get_health_status())

@app.route("/api/tools/status-batch", methods=["GET"])
@log_request(show_response=False)
def api_tools_status_batch():
    """获取所有工具和技能的启用状态摘要（供快捷开关栏使用）"""
    from agent.tools import list_tools
    tools = list_tools()
    result = []
    for t in tools:
        result.append({
            "type": "tool",
            "name": t["name"],
            "description": t.get("description", ""),
            "enabled": _get_tool_state(t["name"]),
        })
    # 添加技能状态
    skills = _skills_mgr.get_all()
    for s in skills:
        result.append({
            "type": "skill",
            "id": s["id"],
            "name": s.get("name", s["id"]),
            "description": s.get("description", ""),
            "enabled": s.get("enabled", True),
        })
    return jsonify(result)

# ── 历史记录 API ──
@app.route("/api/history/search")
@log_request(show_response=False)
def api_history_search():
    """搜索历史记录"""
    q = request.args.get("q", "").strip().lower()
    session_id = request.args.get("session") or _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=500)
    if not q:
        return jsonify(messages[-50:])
    results = [
        {"index": i, **m}
        for i, m in enumerate(messages)
        if m.get("role") == "user" and q in m.get("content", "").lower()
        or m.get("role") == "assistant" and q in m.get("content", "").lower()
    ]
    return jsonify(results)

@app.route("/api/history/<int:index>", methods=["DELETE"])
@require_token
@log_request()
def api_history_delete(index):
    """删除指定索引的历史记录（同时删除用户消息和助手回复）"""
    session_id = request.args.get("session") or _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=1000)
    # index 是消息对索引（一条记录 = 用户消息 + 助手回复）
    msg_idx = index * 2
    if msg_idx >= len(messages):
        return jsonify({"ok": False, "error": "索引超出范围"}), 404
    # 先删助手回复（索引靠后），再删用户消息
    if msg_idx + 1 < len(messages):
        messages.pop(msg_idx + 1)
    messages.pop(msg_idx)
    # 通过 SessionManager 的清空 + 逐条添加（线程安全）
    _session_mgr.clear_messages(session_id)
    for msg in messages:
        _session_mgr.add_message(
            session_id,
            msg.get("role", "user"),
            msg.get("content", ""),
            tool_calls=msg.get("tool_calls"),
        )
    # 同步更新 _CHAT_HISTORY 缓存
    global _CHAT_HISTORY
    if session_id == _session_mgr.get_current_id():
        new_messages = _session_mgr.get_messages(session_id, limit=50)
        _CHAT_HISTORY = []
        for i in range(0, len(new_messages), 2):
            user_msg = new_messages[i]
            assistant_msg = new_messages[i + 1] if i + 1 < len(new_messages) else {}
            if user_msg.get("role") == "user":
                _CHAT_HISTORY.append({
                    "user": user_msg.get("content", ""),
                    "Yunshu": assistant_msg.get("content", ""),
                    "mode": "normal",
                    "timestamp": user_msg.get("timestamp", ""),
                })
    return jsonify({"ok": True})

# ── 记忆操作 API ──
@app.route("/api/memory/overview")
@log_request(show_response=False)
def api_memory_overview():
    """获取记忆概览"""
    try:
        summary = _Yunshu._memory.load_summary()
        recent = _Yunshu._memory._storage.load_recent_messages(limit=20)
        logs = _Yunshu._memory._black_box.analyze()
        log_stats = logs if isinstance(logs, dict) else {}
        return jsonify({
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:300] if summary and summary[0] else None,
            "recent_messages": [
                {"index": i, "role": m.get("role", "?"), "content": m.get("content", "")[:100]}
                for i, m in enumerate(recent)
            ] if recent else [],
            "message_count": len(recent) if recent else 0,
            "log_stats": log_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/memory/manual", methods=["POST"])
@require_token
@log_request()
def api_memory_manual():
    """手动添加记忆"""
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    priority = data.get("priority", "normal")
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    try:
        _Yunshu._memory.add_memory({
            "role": "user",
            "content": f"[手动记忆·优先级:{priority}] {content}"
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/memory/compress", methods=["POST"])
@require_token
@log_request()
def api_memory_compress():
    """触发记忆压缩"""
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_Yunshu._memory.compress())
        loop.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/memory/<int:index>", methods=["DELETE"])
@require_token
@log_request()
def api_memory_delete_index(index):
    """删除指定索引的记忆"""
    # 标记删除操作已接收（简化实现）
    return jsonify({"ok": True})


@app.route("/api/memory/clear-summary", methods=["POST"])
@require_token
@log_request()
def api_memory_clear_summary():
    """清空长期摘要"""
    try:
        _Yunshu._memory.clear_summary()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/memory/summary", methods=["PUT"])
@require_token
@log_request()
def api_memory_update_summary():
    """更新长期摘要内容"""
    data = request.get_json() or {}
    summary = data.get("summary", "").strip()
    try:
        old = _Yunshu._memory.load_summary()
        version = old[1] if old else 0
        _Yunshu._memory._storage.save_summary(summary, version + 1)
        _Yunshu._memory._black_box.log("summary_updated", {"version": version + 1, "length": len(summary)})
        return jsonify({"ok": True, "version": version + 1})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  向量记忆/语义搜索 API
# ════════════════════════════════════════════════════════════

@app.route("/api/vector/search", methods=["POST"])
@require_token
@log_request()
def api_vector_search():
    """语义搜索向量记忆"""
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    top_k = min(int(data.get("top_k", 5)), 50)
    if not query:
        return jsonify({"ok": False, "error": "查询内容不能为空"}), 400

    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"ok": True, "results": [], "count": 0, "available": False})

    try:
        results = vs.search(query, top_k)
        return jsonify({
            "ok": True,
            "results": [item.to_dict() for item in results],
            "count": len(results),
        })
    except Exception as e:
        logger.error("向量搜索失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  窗口监控 API
# ════════════════════════════════════════════════════════════

@app.route("/api/memory/windows/events")
@log_request(show_response=False)
def api_window_events():
    """获取窗口切换事件"""
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)
    try:
        events = _Yunshu._memory._black_box.query(
            event_type="window_event", limit=limit
        )
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})


@app.route("/api/memory/windows/stats")
@log_request(show_response=False)
def api_window_stats():
    """获取窗口使用统计"""
    try:
        events = _Yunshu._memory._black_box.query(
            event_type="window_event", limit=2000
        )
        # 按 to_process 聚合
        app_stats = {}
        total_duration = 0
        total_switches = 0
        for ev in events:
            data = ev.get("data", {})
            if data.get("action") != "switch":
                continue
            proc = data.get("to_process") or "unknown"
            title = data.get("to_title") or proc
            dur = data.get("duration_sec", 0)
            if proc not in app_stats:
                app_stats[proc] = {"process": proc, "title": title,
                                   "duration_sec": 0, "switch_count": 0}
            app_stats[proc]["duration_sec"] += dur
            app_stats[proc]["switch_count"] += 1
            total_duration += dur
            total_switches += 1

        apps = sorted(app_stats.values(), key=lambda a: a["duration_sec"], reverse=True)
        for a in apps:
            a["duration_sec"] = round(a["duration_sec"], 1)
            a["percentage"] = round(a["duration_sec"] / total_duration * 100, 1) if total_duration > 0 else 0

        return jsonify({
            "total_duration_sec": round(total_duration, 1),
            "total_switches": total_switches,
            "apps": apps[:20],
        })
    except Exception as e:
        return jsonify({"total_duration_sec": 0, "total_switches": 0, "apps": [], "error": str(e)})


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


@app.route("/api/memory/windows/current")
@log_request(show_response=False)
def api_window_current():
    """获取当前活跃窗口"""
    if _window_sensor:
        return jsonify(_window_sensor.get_current())
    return jsonify({"process": None, "title": None, "elapsed_sec": 0, "is_idle": False})


@app.route("/api/memory/windows/config", methods=["GET", "POST"])
@require_token
@log_request()
def api_window_config():
    """获取或更新窗口监控配置"""
    if not _window_sensor:
        return jsonify({"enabled": False, "error": "WindowSensor 未初始化"})
    if request.method == "POST":
        try:
            new_config = request.get_json()
            _window_sensor.save_config(new_config)
            return jsonify({"ok": True, "config": _window_sensor.get_config()})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400
    return jsonify(_window_sensor.get_config())


@app.route("/api/memory/windows/clear", methods=["POST"])
@require_token
@log_request()
def api_window_clear():
    """清空窗口事件记录"""
    try:
        return jsonify({"ok": True, "message": "窗口事件将在滚动日志中自然过期"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  心跳接口
# ════════════════════════════════════════════════════════════

@app.route("/api/heartbeat")
@log_request(show_response=False)
def api_heartbeat():
    """心跳检测接口 — 全维度健康检查"""
    try:
        # 执行完整心跳检查
        hb_result = perform_heartbeat_check(_Yunshu)
        # 同步保存到调度器
        scheduler = get_scheduler()
        scheduler._save_heartbeat(hb_result)
        return jsonify(hb_result)
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


@app.route("/api/heartbeat/history")
@log_request(show_response=False)
def api_heartbeat_history():
    """获取心跳历史"""
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    scheduler = get_scheduler()
    data = scheduler.get_heartbeat_status()
    history = data.get("history", [])
    total = len(history)
    history.reverse()
    paged = history[offset:offset + limit]
    return jsonify({
        "history": paged,
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@app.route("/api/heartbeat/status")
@log_request(show_response=False)
def api_heartbeat_status():
    """获取心跳概览"""
    scheduler = get_scheduler()
    data = scheduler.get_heartbeat_status()
    latest = data.get("latest", {})
    history = data.get("history", [])
    healthy_count = sum(1 for h in history if h.get("status") == "healthy")
    return jsonify({
        "status": latest.get("status", "unknown"),
        "timestamp": latest.get("timestamp"),
        "total_checks": len(history),
        "healthy_checks": healthy_count,
        "latest": latest,
    })


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
#  工作区接口
# ════════════════════════════════════════════════════════════

@app.route("/api/workspace")
@log_request(show_response=False)
def api_workspace_list():
    """列出工作区内容"""
    path = request.args.get("path", "")
    try:
        result = list_workspace(path)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"error": str(e)}), 403
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/workspace/write", methods=["POST"])
@require_token
@log_request()
def api_workspace_write():
    """写入工作区文件"""
    data = request.get_json() or {}
    path = data.get("path", "")
    content = data.get("content", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    # 安全检查
    safety = _safety_guard.check(content)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
    try:
        result = write_workspace(path, content)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 403


@app.route("/api/workspace/delete", methods=["POST"])
@require_token
@log_request()
def api_workspace_delete():
    """删除工作区文件"""
    data = request.get_json() or {}
    path = data.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    try:
        result = delete_workspace(path)
        return jsonify(result)
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 403


@app.route("/api/workspace/info")
@log_request(show_response=False)
def api_workspace_info():
    """工作区信息"""
    import os
    total_size = 0
    file_count = 0
    for root, dirs, files in os.walk(WORKSPACE_DIR):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total_size += os.path.getsize(fp)
                file_count += 1
            except OSError:
                pass
    return jsonify({
        "path": WORKSPACE_DIR,
        "file_count": file_count,
        "total_size_bytes": total_size,
    })


# ════════════════════════════════════════════════════════════
#  通用文件系统 API — 云枢读写本地文件的能力
# ════════════════════════════════════════════════════════════

@app.route("/api/filesystem/read", methods=["POST"])
@require_token
@log_request()
def api_filesystem_read():
    """读取本地文件内容"""
    data = request.get_json() or {}
    path = data.get("path", "")
    encoding = data.get("encoding", "utf-8")
    max_size_mb = min(data.get("max_size_mb", 5), 50)  # 最大 50MB
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400

    # 安全检查
    result = read_file(path, encoding=encoding, max_size_mb=max_size_mb)
    if result.get("binary"):
        # 对二进制内容返回截断警告
        content_len = len(result.get("content", ""))
        if content_len > 100000:
            result["truncated"] = True
            result["content"] = result["content"][:100000]
            result["note"] = "二进制内容已截断，完整内容过大"
    return jsonify(result)


@app.route("/api/filesystem/write", methods=["POST"])
@require_token
@log_request()
def api_filesystem_write():
    """写入本地文件"""
    data = request.get_json() or {}
    path = data.get("path", "")
    content = data.get("content", "")
    encoding = data.get("encoding", "utf-8")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400

    # 安全检查
    safety = _safety_guard.check(content)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403

    result = write_file(path, content, encoding=encoding)
    return jsonify(result)


@app.route("/api/filesystem/list", methods=["GET"])
@log_request(show_response=False)
def api_filesystem_list():
    """列出目录内容"""
    path = request.args.get("path", ".")
    show_hidden = request.args.get("show_hidden", "false").lower() == "true"
    result = list_directory(path, show_hidden=show_hidden)
    return jsonify(result)


@app.route("/api/filesystem/info", methods=["GET"])
@log_request(show_response=False)
def api_filesystem_info():
    """获取文件/目录信息"""
    path = request.args.get("path", "")
    if not path:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    return jsonify(get_file_info(path))


@app.route("/api/filesystem/search", methods=["GET"])
@log_request(show_response=False)
def api_filesystem_search():
    """搜索文件"""
    pattern = request.args.get("pattern", "")
    root_path = request.args.get("root_path", ".")
    if not pattern:
        return jsonify({"ok": False, "error": "缺少 pattern"}), 400
    return jsonify(search_files(pattern, root_path=root_path))


# ════════════════════════════════════════════════════════════
#  Python 沙盒接口
# ════════════════════════════════════════════════════════════

@app.route("/api/sandbox/run", methods=["POST"])
@require_token
@log_request()
def api_sandbox_run():
    """在受限沙盒中执行 Python 代码（受 features.sandbox 开关控制）"""
    # 读取沙盒功能开关（默认关闭）
    sandbox_enabled = os.getenv("YUNSHU_FEATURE_SANDBOX", "false").lower() == "true"

    if not sandbox_enabled:
        logger.warning("[沙盒] 访问被拒绝 - 沙盒功能已关闭 (YUNSHU_FEATURE_SANDBOX=%s)",
                       os.getenv("YUNSHU_FEATURE_SANDBOX", "未设置"))
        return jsonify({"blocked": True, "error": "沙盒功能已关闭，设置环境变量 YUNSHU_FEATURE_SANDBOX=true 可启用", "sandbox_disabled": True}), 503

    logger.info("[沙盒] 沙盒功能已启用，开始执行代码")

    try:
        from agent.system_tools import run_sandbox
    except ImportError as e:
        logger.error("[沙盒] 导入 run_sandbox 失败: %s", e, exc_info=True)
        return jsonify({"error": f"沙盒模块加载失败: {e}", "sandbox_init_error": True}), 500

    data = request.get_json() or {}
    code = data.get("code", "")
    timeout = min(data.get("timeout", 5), 30)  # 最大 30 秒

    # 安全检查
    try:
        safety = _safety_guard.check(code)
    except Exception as e:
        logger.error("[沙盒] 安全检查异常: %s", e, exc_info=True)
        safety = {"level": "warning", "matches": [], "safe": True, "check_error": str(e)}

    if safety["level"] == "critical":
        logger.warning("[沙盒] 代码被安全检查拦截: %s", safety)
        return jsonify({"blocked": True, "safety": safety}), 403

    try:
        result = run_sandbox(code, timeout)
    except Exception as e:
        logger.error("[沙盒] 代码执行引擎异常: %s", e, exc_info=True)
        return jsonify({"error": f"沙盒执行引擎异常: {e}", "engine_error": True}), 500

    result["safety"] = safety

    if result.get("error"):
        logger.warning("[沙盒] 代码执行出错: %s", result["error"][:200])
    elif result.get("timed_out"):
        logger.warning("[沙盒] 代码执行超时 (%ds)", timeout)
    else:
        logger.info("[沙盒] 代码执行成功，耗时 %.1fms", result.get("duration_ms", 0))

    return jsonify(result)


# ════════════════════════════════════════════════════════════
#  定时任务接口
# ════════════════════════════════════════════════════════════

@app.route("/api/scheduler/tasks")
@log_request(show_response=False)
def api_scheduler_list():
    """列出所有定时任务"""
    return jsonify(list_scheduled_tasks())


@app.route("/api/scheduler/create", methods=["POST"])
@require_token
@log_request()
def api_scheduler_create():
    """创建定时任务"""
    data = request.get_json() or {}
    name = data.get("name", "")
    command = data.get("command", "")
    interval_sec = data.get("interval_sec", 60)
    if not name or not command:
        return jsonify({"ok": False, "error": "缺少 name 或 command"}), 400
    # 安全检查
    safety = _safety_guard.check(command)
    if safety["level"] == "critical":
        return jsonify({"ok": False, "blocked": True, "safety": safety}), 403
    result = create_scheduled_task(name, command, interval_sec)
    return jsonify(result)


@app.route("/api/scheduler/delete", methods=["POST"])
@require_token
@log_request()
def api_scheduler_delete():
    """删除定时任务"""
    data = request.get_json() or {}
    task_id = data.get("id", "")
    return jsonify(delete_scheduled_task(task_id))


@app.route("/api/scheduler/toggle", methods=["POST"])
@require_token
@log_request()
def api_scheduler_toggle():
    """启用/禁用定时任务"""
    data = request.get_json() or {}
    task_id = data.get("id", "")
    enabled = data.get("enabled", True)
    return jsonify(toggle_scheduled_task(task_id, enabled))


@app.route("/api/scheduler/execute-now", methods=["POST"])
@require_token
@log_request()
def api_scheduler_execute_now():
    """立即执行指定任务"""
    data = request.get_json() or {}
    task_id = data.get("id", "")
    if not task_id:
        return jsonify({"ok": False, "error": "缺少任务ID"}), 400
    scheduler = get_scheduler()
    result = scheduler.execute_now(task_id)
    if result is None:
        return jsonify({"ok": False, "error": "任务不存在"}), 404
    return jsonify({"ok": True, "result": result})


@app.route("/api/scheduler/history")
@log_request(show_response=False)
def api_scheduler_history():
    """获取任务执行历史"""
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    task_type = request.args.get("type", "", type=str)
    scheduler = get_scheduler()
    history = scheduler.get_history(limit=limit, offset=offset, task_type=task_type)
    return jsonify({"history": history, "limit": limit, "offset": offset})


# ════════════════════════════════════════════════════════════
#  搜索引擎性能监控接口
# ════════════════════════════════════════════════════════════

@app.route("/api/search-performance/status")
@log_request()
def api_search_performance_status():
    """获取搜索引擎性能监控状态"""
    try:
        from agent.search_performance_monitor import get_performance_monitor_status
        status = get_performance_monitor_status()
        return jsonify({"ok": True, "status": status})
    except Exception as e:
        logger.error("[性能监控] 获取状态失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search-performance/start", methods=["POST"])
@require_token
@log_request()
def api_search_performance_start():
    """启动搜索引擎性能监控"""
    try:
        from agent.search_performance_monitor import start_performance_monitor
        data = request.get_json() or {}
        interval_sec = data.get("interval_sec", 300)  # 默认 5 分钟
        status = start_performance_monitor(interval_sec)
        return jsonify({"ok": True, "message": "性能监控已启动", "status": status})
    except Exception as e:
        logger.error("[性能监控] 启动失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search-performance/stop", methods=["POST"])
@require_token
@log_request()
def api_search_performance_stop():
    """停止搜索引擎性能监控"""
    try:
        from agent.search_performance_monitor import stop_performance_monitor
        status = stop_performance_monitor()
        return jsonify({"ok": True, "message": "性能监控已停止", "status": status})
    except Exception as e:
        logger.error("[性能监控] 停止失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search-performance/check", methods=["POST"])
@require_token
@log_request()
def api_search_performance_check():
    """手动执行一次性能检测"""
    try:
        from agent.search_performance_monitor import run_manual_performance_check
        result = run_manual_performance_check()
        return jsonify({"ok": True, "result": result})
    except Exception as e:
        logger.error("[性能监控] 手动检测失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search-performance/history")
@log_request()
def api_search_performance_history():
    """获取性能检测历史记录"""
    try:
        from agent.search_performance_monitor import get_performance_history
        limit = request.args.get("limit", 10, type=int)
        history = get_performance_history(limit)
        return jsonify({"ok": True, "history": history})
    except Exception as e:
        logger.error("[性能监控] 获取历史失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/search-performance/summary")
@log_request()
def api_search_performance_summary():
    """获取性能摘要"""
    try:
        from agent.search_performance_monitor import get_performance_summary
        summary = get_performance_summary()
        return jsonify({"ok": True, "summary": summary})
    except Exception as e:
        logger.error("[性能监控] 获取摘要失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  无头浏览器接口
# ════════════════════════════════════════════════════════════

@app.route("/api/browser/navigate", methods=["POST"])
@require_token
@log_request()
def api_browser_navigate():
    """浏览器导航到 URL"""
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400
    return jsonify(browser_navigate(url))


@app.route("/api/browser/screenshot")
@require_token
@log_request()
def api_browser_screenshot():
    """浏览器截图"""
    result = browser_screenshot()
    return jsonify(result)


@app.route("/api/browser/close", methods=["POST"])
@require_token
@log_request()
def api_browser_close():
    """关闭浏览器"""
    browser_close()
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════
#  进程管理接口
# ════════════════════════════════════════════════════════════

@app.route("/api/process/list")
@log_request(show_response=False)
def api_process_list():
    """列出白名单进程"""
    return jsonify({"processes": list_processes()})


@app.route("/api/process/whitelist")
@log_request(show_response=False)
def api_process_whitelist():
    """获取进程白名单详情"""
    return jsonify(get_whitelist_detail())


@app.route("/api/process/whitelist/add", methods=["POST"])
@require_token
@log_request()
def api_process_whitelist_add():
    """添加自定义白名单条目"""
    data = request.get_json() or {}
    program = data.get("program", "")
    return jsonify(add_whitelist_entry(program))


@app.route("/api/process/whitelist/remove", methods=["POST"])
@require_token
@log_request()
def api_process_whitelist_remove():
    """移除自定义白名单条目"""
    data = request.get_json() or {}
    program = data.get("program", "")
    return jsonify(remove_whitelist_entry(program))


@app.route("/api/process/start", methods=["POST"])
@require_token
@log_request()
def api_process_start():
    """启动白名单程序"""
    data = request.get_json() or {}
    program = data.get("program", "")
    args = data.get("args")
    if not program:
        return jsonify({"ok": False, "error": "缺少 program"}), 400
    return jsonify(start_process(program, args))


@app.route("/api/process/stop", methods=["POST"])
@require_token
@log_request()
def api_process_stop():
    """终止进程（仅限白名单）"""
    data = request.get_json() or {}
    pid = data.get("pid")
    if not pid:
        return jsonify({"ok": False, "error": "缺少 pid"}), 400
    return jsonify(stop_process(pid))


# ════════════════════════════════════════════════════════════
#  剪贴板接口
# ════════════════════════════════════════════════════════════

@app.route("/api/clipboard")
@require_token
@log_request(show_response=False)
def api_clipboard_get():
    """读取剪贴板"""
    return jsonify(get_clipboard())


@app.route("/api/clipboard", methods=["POST"])
@require_token
@log_request()
def api_clipboard_set():
    """写入剪贴板"""
    data = request.get_json() or {}
    text = data.get("text", "")
    return jsonify(set_clipboard(text))


# ════════════════════════════════════════════════════════════
#  互联网 API — 云枢获取网络信息的能力
# ════════════════════════════════════════════════════════════

@app.route("/api/web/get", methods=["POST"])
@require_token
@log_request()
def api_web_get():
    """HTTP GET 请求"""
    data = request.get_json() or {}
    url = data.get("url", "")
    timeout = data.get("timeout", 30)
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400

    result = _web_http.get(url, timeout=timeout)
    if result.get("ok") and result.get("text"):
        parsed = _web_scraper.parse(result["text"], url=result.get("url", url))
        result["parsed"] = {k: parsed.get(k) for k in ("title", "text", "links", "images", "meta", "headings") if k != "html"}
    return jsonify(result)


@app.route("/api/web/post", methods=["POST"])
@require_token
@log_request()
def api_web_post():
    """HTTP POST 请求"""
    data = request.get_json() or {}
    url = data.get("url", "")
    if not url:
        return jsonify({"ok": False, "error": "缺少 url"}), 400

    form_data = data.get("data", {})
    json_data = data.get("json_data", {})
    if json_data:
        result = _web_http.post(url, json_data=json_data)
    else:
        result = _web_http.post(url, data=form_data)
    return jsonify(result)


@app.route("/api/web/xpath", methods=["POST"])
@require_token
@log_request()
def api_web_xpath():
    """XPath 提取"""
    data = request.get_json() or {}
    url = data.get("url", "")
    expression = data.get("expression", "")
    html = data.get("html", "")

    if not expression:
        return jsonify({"ok": False, "error": "缺少 expression"}), 400

    if html:
        results = _web_scraper.xpath(expression, html=html)
        return jsonify({"ok": True, "results": results, "count": len(results)})

    if not url:
        return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400

    fetch = _web_http.get(url)
    if not fetch.get("ok"):
        return jsonify(fetch)
    results = _web_scraper.xpath(expression, html=fetch.get("text", ""))
    return jsonify({"ok": True, "results": results, "count": len(results)})


@app.route("/api/web/css", methods=["POST"])
@require_token
@log_request()
def api_web_css():
    """CSS 选择器提取"""
    data = request.get_json() or {}
    url = data.get("url", "")
    selector = data.get("selector", "")
    attr = data.get("attr", "")
    html = data.get("html", "")

    if not selector:
        return jsonify({"ok": False, "error": "缺少 selector"}), 400

    if html:
        results = _web_scraper.css(selector, html=html, attr=attr or None)
        return jsonify({"ok": True, "results": results, "count": len(results)})

    if not url:
        return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400

    fetch = _web_http.get(url)
    if not fetch.get("ok"):
        return jsonify(fetch)
    results = _web_scraper.css(selector, html=fetch.get("text", ""), attr=attr or None)
    return jsonify({"ok": True, "results": results, "count": len(results)})


@app.route("/api/web/search", methods=["GET"])
@log_request(show_response=False)
def api_web_search():
    """搜索互联网"""
    query = request.args.get("query", "")
    num = min(int(request.args.get("num_results", 10)), 50)
    engine = request.args.get("engine", "")

    if not query:
        return jsonify({"ok": False, "error": "缺少 query"}), 400

    result = _web_search.search(query, engine=engine, num_results=num)
    if result.get("ok") and result.get("results"):
        processed = _web_processor.process(result["results"])
        result["results"] = processed
        result["summary"] = DataProcessor.summarize_results(processed)
    return jsonify(result)


@app.route("/api/web/clean", methods=["POST"])
@require_token
@log_request()
def api_web_clean():
    """数据清洗"""
    data = request.get_json() or {}
    text = data.get("text", "")
    items = data.get("items", [])

    if text:
        return jsonify({"ok": True, "cleaned": DataProcessor.clean_text(text)})
    if items:
        processed = _web_processor.process(items)
        return jsonify({
            "ok": True,
            "original_count": len(items),
            "processed_count": len(processed),
            "results": processed,
        })
    return jsonify({"ok": False, "error": "请提供 text 或 items"}), 400


@app.route("/api/web/download", methods=["POST"])
@require_token
@log_request()
def api_web_download():
    """下载文件"""
    data = request.get_json() or {}
    url = data.get("url", "")
    filepath = data.get("filepath", "")
    if not url or not filepath:
        return jsonify({"ok": False, "error": "缺少 url 或 filepath"}), 400
    return jsonify(_web_http.download(url, filepath))


@app.route("/api/web/stats")
@log_request(show_response=False)
def api_web_stats():
    """Web 模块统计"""
    return jsonify({
        "http": _web_http.get_stats(),
        "search": _web_search.get_stats(),
        "processor": _web_processor.get_stats(),
        "crawler_control": _web_crawler.get_stats(),
    })


@app.route("/api/web/search/status")
@log_request(show_response=False)
def api_web_search_status():
    """获取当前搜索引擎状态和切换日志（用于前端显示）"""
    try:
        status = _web_search.get_current_status()
        return jsonify({
            "ok": True,
            "status": status,
        })
    except Exception as e:
        logger.error("[搜索引擎] 获取状态失败: %s", e, exc_info=True)
        return jsonify({
            "ok": False,
            "error": str(e)
        }), 500


# ════════════════════════════════════════════════════════════
#  定时调度 API
# ════════════════════════════════════════════════════════════

from agent.scheduling import get_schedule_scheduler, Scheduler as _SchedCls

_sched = get_schedule_scheduler()
_sched.start()
logger.info("定时调度系统已启动")

@app.route("/api/schedules", methods=["GET"])
@log_request(show_response=False)
def api_schedules_list():
    """获取所有定时任务"""
    return jsonify(_sched.get_tasks())

@app.route("/api/schedules", methods=["POST"])
@require_token
@log_request()
def api_schedules_create():
    """创建定时任务"""
    data = request.get_json() or {}
    name = data.get("name", "")
    action = data.get("action", "")
    params = data.get("params", {})
    interval_minutes = data.get("interval_minutes", 0)
    cron_expr = data.get("cron_expr", "")
    enabled = data.get("enabled", True)

    if not name.strip():
        return jsonify({"ok": False, "error": "任务名称不能为空"}), 400
    if interval_minutes <= 0 and not cron_expr.strip():
        return jsonify({"ok": False, "error": "必须提供 interval_minutes 或 cron_expr"}), 400
    if cron_expr.strip() and not _SchedCls.validate_cron_expr(cron_expr):
        return jsonify({"ok": False, "error": f"无效的 cron 表达式: {cron_expr}"}), 400
    result = _sched.add_task(
        name=name, action=action, params=params,
        interval_minutes=interval_minutes, cron_expr=cron_expr, enabled=enabled,
    )
    if result.get("ok"):
        return jsonify(result), 201
    return jsonify(result), 400

@app.route("/api/schedules/<task_id>", methods=["DELETE"])
@require_token
@log_request()
def api_schedules_delete(task_id):
    """删除定时任务"""
    result = _sched.remove_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404

@app.route("/api/schedules/<task_id>/pause", methods=["POST"])
@require_token
@log_request()
def api_schedules_pause(task_id):
    """暂停定时任务"""
    result = _sched.pause_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404

@app.route("/api/schedules/<task_id>/resume", methods=["POST"])
@require_token
@log_request()
def api_schedules_resume(task_id):
    """恢复定时任务"""
    result = _sched.resume_task(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 404

@app.route("/api/schedules/history", methods=["GET"])
@log_request(show_response=False)
def api_schedules_history():
    """获取执行历史"""
    limit = request.args.get("limit", 100, type=int)
    offset = request.args.get("offset", 0, type=int)
    return jsonify(_sched.get_history(limit=limit, offset=offset))


# ════════════════════════════════════════════════════════════
#  异步任务管理 API
# ════════════════════════════════════════════════════════════

from agent.async_executor import get_async_executor as _get_async_executor

@app.route("/api/tasks", methods=["GET"])
@log_request(show_response=False)
def api_tasks_list():
    """列出所有异步任务"""
    executor = _get_async_executor()
    return jsonify(executor.list_tasks())


@app.route("/api/tasks/<task_id>", methods=["GET"])
@log_request(show_response=False)
def api_task_status(task_id):
    """获取单个异步任务状态"""
    executor = _get_async_executor()
    return jsonify(executor.get_status(task_id))


@app.route("/api/tasks/<task_id>/cancel", methods=["POST"])
@require_token
@log_request()
def api_task_cancel(task_id):
    """取消异步任务"""
    executor = _get_async_executor()
    result = executor.cancel(task_id)
    if result.get("ok"):
        return jsonify(result)
    return jsonify(result), 400


# ════════════════════════════════════════════════════════════
#  HTML 界面
# ════════════════════════════════════════════════════════════

# HTML 模板已提取到 templates/index.html

@app.route("/")
def index():
    from flask import make_response, redirect
    # 用随机参数破坏浏览器缓存
    import time as t_mod, random
    ver = int(t_mod.time() * 1000)
    return redirect(f"/chat?v={ver}")

@app.route("/chat")
def chat_page():
    from flask import make_response
    html = render_template("index.html")
    resp = make_response(html)
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
    serve(app, host="127.0.0.1", port=5678, threads=8)
