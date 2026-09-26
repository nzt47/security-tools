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
import signal  # 优雅关闭：SIGTERM/SIGINT/SIGBREAK 处理器（见 _install_graceful_shutdown_hooks）
import urllib.request as _ur
import urllib.parse as _up
import json as _js

# 修复 Windows 控制台编码，避免中文日志乱码
# 【S11-01 为何上移】原生扩展预导入要打印一行 `[S11-01]` 启动记录（见下），
# 该行必须在 stdout 已切成 UTF-8 之后才可靠（中文摘要 + 中文 Windows GBK 下会乱码）。
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 加载 .env 到 os.environ（守 user_rules「配置走 .env」单一数据源）
# Why: main.py 已按此模式加载；app_server 若不加载，LLM 密钥 / HF_HUB_OFFLINE /
#      LOG_LEVEL / CONTEXT_ASSEMBLER_LOG_LEVEL 等 .env 配置全部失效。
#      必须在读取环境变量的模块级代码之前执行（reload 覆盖同名变量为 .env 值）。
# 【S11-01 为何上移到此处】紧随其后的原生扩展预导入有一个 env 开关
#      （`CP_NATIVE_PREIMPORT_ENABLED`）。若 .env 加载晚于它，写在 `.env` 里的
#      开关值就不生效（只有真实 OS 环境变量才生效）—— 与「配置走 .env」
#      单一数据源相悖。故把 .env 加载提前到「任何 env 读取点」之前。
try:
    from agent.env_config_manager import get_env_config_manager
    get_env_config_manager().reload()
except Exception as _e:
    logging.getLogger(__name__).warning(f".env 加载失败（继续使用系统环境变量）: {_e}")

# ════════════════════════════════════════════════════════════════════════════
# [S11-01] 进程入口：原生扩展导入顺序固化（防 arrow.dll 打崩长寿命进程）
# ════════════════════════════════════════════════════════════════════════════
# 【为什么必须在最前（Why here）】本进程是**长寿命生产进程**。若 `pyarrow` 的
#   原生初始化（`pyarrow\arrow.dll`）被推迟到进程已加载 torch / onnxruntime 等
#   重型原生库之后才发生，Windows 上会触发 `0xC0000005 ACCESS_VIOLATION`，
#   进程被系统**直接终止**（没有 traceback、没有日志）。此前该保护只在
#   `agent/orchestrator/lifecycle_manager.py:118`（DigitalLife 构造时）才生效，
#   **生产进程（app_server）没有这道保护** —— 这就是 S10-05 遗留 #1。
# 【位置纪律（不易）】必须早于**任何其它 agent.\* / plugins.\* 导入**：
#   `plugins/__init__.py` 会连带导入 memory/admin/skills/chat 等重模块，
#   它们一旦先加载，pyarrow 的"干净窗口"就没了。故此处紧跟 .env 加载，
#   位于 `requests` / `plugins.plugin_api` / `flask` 等一切非 stdlib 导入之前。
# 【实现唯一（不易）】复用 `agent/utils/native_preimport.py` —— 与
#   `tests/conftest.py`、`tests/integration/conftest.py`、以及 S10-05 的原实现
#   **同一份代码**（不是复制）。现象/根因/顺序依据/失败姿态见该模块 docstring。
# 【失败姿态（不易）】装载失败只打印一行降级告警并继续启动：本保护是为了
#   "更不容易崩"，绝不能反过来变成"装不上就起不来"（守「规避逻辑不得引入新的
#   硬依赖」）。开关 `CP_NATIVE_PREIMPORT_ENABLED=0` 可整支关闭（已登记注册表）。
# 【为什么用 print 而不是 logger】此处 `logging.basicConfig` 尚未执行
#   （第 83 行才配置），logger 只有 lastResort 兜底；而这一行是"保护有没有装上"
#   的唯一启动期证据，必须无条件可见。启动后可用 grep `[S11-01]` 复核。
_NATIVE_PREIMPORT_RESULT: dict = {}
try:
    from agent.utils.native_preimport import (
        pin_native_import_order as _pin_native_import_order,
        report_line as _native_preimport_report_line,
    )

    _NATIVE_PREIMPORT_RESULT = _pin_native_import_order()
    print(_native_preimport_report_line(), flush=True)
except Exception as _native_e:  # noqa: BLE001 保护装不上不阻断启动
    print(
        "[S11-01] 原生扩展导入顺序固化装载失败（降级，不阻断启动）: %s" % _native_e,
        flush=True,
    )

import requests as _http  # 注意：Flask 的 request 对象会覆盖 requests 模块，用 _http 别名

# 插件机制（T1.1–T1.10）：协议层 + 装配器（注册表见 plugins/plugin_api.py）
from plugins.plugin_api import get_plugins, manifest as plugin_manifest

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
from agent.session_manager import SessionManager, SessionGroupStore, WorkspaceRegistry
from agent.log_system.dashboard import register_log_system

logging.basicConfig(level=logging.INFO, encoding="utf-8", force=True)
logger = logging.getLogger(__name__)

# 启用结构化日志易读格式（控制台显示优化，不影响 JSON 原始内容）
try:
    from scripts.struct_log_formatter import setup_readable_logging
    setup_readable_logging()
except Exception as _e:
    logger.debug(f"结构化日志格式化器加载失败（不影响功能）: {_e}")


# ════════════════════════════════════════════════════════════════════════════
# [B2] 路由决策事件持久化 sink —— agent.orchestrator → data/logs/<date>.jsonl
# ════════════════════════════════════════════════════════════════════════════
# [B2-ROUTE-SINK-BEGIN]
#   ↑ tests/unit/test_route_log_sink.py 按此标记从本文件源码抽取本段做单测。
#   为什么不直接 import app_server：模块级会真的构造 DigitalLife 并 start()
#   （下方 _Yunshu = DigitalLife(...)），实测 80–100s 且启动 embedding/reranker
#   子进程（GB 级内存）—— 一个 sink 单测不得付出这个代价。
#
# 【为什么要它（Q5_funnel_baseline.md §3 / §8.2 实测）】
#   路由打点本身齐全：routing_observability.py:222-266 log_layer_result、
#   :269-299 emit_route_decision、orchestrator.py:368 _record_intent_layer
#   （→ prometheus.py:718/724）。但 web 服务**没有持久化 sink**：
#   本文件第 128 行的 logging.basicConfig 只写 stderr；带轮转文件的
#   setup_agent_logging(enable_file=True) 只被 CLI main.py:28,95 调用，
#   且默认 enable_file=False（agent/logging_utils.py:441,500-513）。
#   实测 data/logs/*.jsonl 中 route_decision=0 / intent_layer=0 ⇒ 服务一重启，
#   路由决策永久丢失，「路由准确率/误召率/平均路由深度/P95 延迟」一个都算不出。
#
# 【复用而不是另造（不易）】data/logs/<date>.jsonl 这个 sink 早就存在：
#   agent/monitoring/loki.py:66-76 LokiClient._save_local_log 以
#   {"timestamp":..,"labels":{..},"message":"<json 字符串>"} 逐行追加到同一目录的
#   <date>.jsonl（配置变更事件走的就是它，见 config_observability.py:230-234）。
#   本段**只把路由类事件接进这个既有通道**：不新增文件格式、不新增 writer、
#   不改写 logging.basicConfig、不建第二套日志框架；读侧的
#   LokiClient.query_logs/_get_local_labels 无需任何改动即可检索这些行。
#
# 【范围界定：只放行路由类事件，不无脑开 DEBUG（不易）】双重门 + 一个级别约束：
#   ① logger 门：handler 只挂在 _ROUTE_EVENT_SINK_LOGGERS 这两个 logger 上（不挂
#      root）。agent.orchestrator 覆盖 routing_observability.py:33 与
#      orchestrator.py:70（其 "agent.orchestrator.orchestrator" 向上传播到这里）；
#      agent.observability.tool_trace（tool_trace.py:41）覆盖工具漏斗检索事件。
#      其它模块的记录根本不进入本 handler ⇒ 连过滤开销都不付。
#   ② action 门：记录 msg 是 log_dict 产出的 dict，只有 action 命中
#      _ROUTE_EVENT_SINK_ACTIONS / _ROUTE_EVENT_SINK_ACTION_PREFIXES 才落盘；
#      同一 logger 上的非路由日志（如 "[LLM] 正常完成"）被丢弃。
#   ③ 级别：handler 级别固定 INFO，全程不调 setLevel(DEBUG)。层未命中
#      （log_layer_result level=DEBUG，orchestrator.py:758 等）因此**不落盘** ——
#      这是有意的：漏斗的「尝试/命中」分母已由 route_decision 整包携带
#      （routing_observability.py:261 ctx.add_layer 不区分级别，:291 全量写出
#      layer_results），1 条 route_decision 即可还原该请求的完整漏斗，
#      无需为分母开 DEBUG 洪泛。
#
# 【可关闭 / 可回滚】CP_ROUTE_EVENT_SINK_ENABLED=0（亦接受 false/no/off）⇒ 不装配
#   handler，且会摘掉已装的 handler，行为回到改动前（路由日志只进 stderr）。
#   回滚方式见 docs/audit_skill_governance/B2.md。
#
# 【失败姿态】装配失败（loki 模块缺失等）只 warning，绝不阻断启动；单条写盘失败
#   只计数，绝不向业务线程抛出（与埋点「不阻断主链路」同一姿态）。
#
# 【不拖慢请求路径】同步追加写，不用 QueueHandler/后台线程：端到端实测每请求只写
#   2 行（2 次真实对话 → 4 行，见 docs/audit_skill_governance/B2.md §4），而进程被
#   taskkill /F 时（本仓常见退出方式）异步队列里未刷盘的事件会丢 ——「重启即丢」正是
#   本卡要消灭的问题。单次写盘 mean 241–267us / p95 289–340us（同一份生产代码的
#   微基准，2000 次写盘 ×2 轮），相对实测请求耗时（0.5s 起）可忽略。

_ROUTE_EVENT_SINK_ENV = "CP_ROUTE_EVENT_SINK_ENABLED"
_ROUTE_EVENT_SINK_DIR_ENV = "CP_ROUTE_EVENT_SINK_DIR"
_ROUTE_EVENT_SINK_LOGGERS = ("agent.orchestrator", "agent.observability.tool_trace")
_ROUTE_EVENT_SINK_ACTIONS = frozenset({
    "orchestrator.process.route_decision",  # routing_observability.py:286（每请求 1 条）
    "orchestrator.traffic.summary",         # routing_observability.py:124（每 N 次请求 1 条）
    "tool_retrieval",                       # tool_trace.py:559（工具漏斗检索事件）
})
_ROUTE_EVENT_SINK_ACTION_PREFIXES = (
    "orchestrator.layer.",                  # routing_observability.py:240 log_layer_result
    "orchestrator.intent_layer.",           # orchestrator.py:385/395 埋点诊断
)
_ROUTE_EVENT_SINK_HANDLERS = []  # [(logger_name, handler)]，供幂等摘除
_ROUTE_EVENT_SINK_STATE = {}


def _route_event_payload(record):
    """把一条 LogRecord 还原成路由事件 dict；非白名单事件返回 None

    两种形态都吃：log_dict 产出的 dict（当前实现），以及历史调用点可能传的
    JSON 字符串 —— 判据始终是 action 门，不因形态不同而放行。
    """
    msg = getattr(record, "msg", None)
    if isinstance(msg, dict):
        payload = msg
    elif isinstance(msg, str):
        try:
            payload = json.loads(msg)
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
    else:
        return None

    action = payload.get("action")
    if not isinstance(action, str) or not action:
        return None
    if action in _ROUTE_EVENT_SINK_ACTIONS:
        return payload
    if action.startswith(_ROUTE_EVENT_SINK_ACTION_PREFIXES):
        return payload
    return None


class RouteEventJsonlHandler(logging.Handler):
    """把路由类日志记录追加写入既有 JSONL sink（data/logs/<date>.jsonl）

    写盘通道 = LokiClient(enabled=False).push_log()，即 loki.py:66-76 的本地回退
    写文件逻辑（enabled=False ⇒ 直接落盘，不做任何网络请求，也不会等连接超时）。
    """

    def __init__(self, client, level=logging.INFO):
        super().__init__(level=level)
        self._client = client
        self.written = 0
        self.skipped = 0
        self.failed = 0
        self.last_error = ""

    def emit(self, record):
        """同步写一条（本 handler 自己 json.dumps 载荷，不依赖 formatter）"""
        try:
            payload = _route_event_payload(record)
            if payload is None:
                self.skipped += 1
                return
            self._client.push_log(
                labels={
                    "app": "yunshu-route",
                    "event": payload["action"],
                    "level": record.levelname,
                    "logger": record.name,
                },
                message=json.dumps(payload, ensure_ascii=False, default=str),
                timestamp=record.created,
            )
            self.written += 1
        except Exception as exc:  # noqa: BLE001 写盘失败绝不冒泡到业务线程
            self.failed += 1
            self.last_error = "%s: %s" % (type(exc).__name__, exc)

    def stats(self):
        """写盘计数（诊断/测试用）"""
        return {
            "written": self.written,
            "skipped": self.skipped,
            "failed": self.failed,
            "last_error": self.last_error,
        }


def route_event_sink_enabled(explicit=None):
    """开关解析：显式入参优先，其次 CP_ROUTE_EVENT_SINK_ENABLED（缺省开启）

    关闭值：0 / false / no / off（大小写与空白不敏感）。
    """
    if explicit is not None:
        return bool(explicit)
    raw = os.environ.get(_ROUTE_EVENT_SINK_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _detach_route_event_sink():
    """摘掉已装配的 handler（幂等安装 + 关闭开关时清理），返回摘除条数"""
    global _ROUTE_EVENT_SINK_HANDLERS
    removed = 0
    for name, handler in _ROUTE_EVENT_SINK_HANDLERS:
        try:
            logging.getLogger(name).removeHandler(handler)
            removed += 1
        except Exception:
            pass
    _ROUTE_EVENT_SINK_HANDLERS = []
    return removed


def install_route_event_sink(enabled=None, log_dir=None):
    """装配路由事件 sink（幂等；返回状态 dict 供启动日志与测试断言）

    Args:
        enabled: 显式开关；None ⇒ 读 CP_ROUTE_EVENT_SINK_ENABLED
        log_dir: 落盘目录覆盖；None ⇒ 读 CP_ROUTE_EVENT_SINK_DIR，仍为空则用
                 LokiClient 自带的 <repo>/data/logs（测试借它指向 tmp_path）

    Returns:
        {"installed": bool, "reason": str, "log_dir": str, "target_file": str,
         "loggers": [...], "actions": [...], "handler": RouteEventJsonlHandler|None}
    """
    global _ROUTE_EVENT_SINK_HANDLERS, _ROUTE_EVENT_SINK_STATE
    _detach_route_event_sink()  # 重复调用不叠加 handler（叠加 ⇒ 同一事件写多行）

    status = {
        "installed": False,
        "reason": "",
        "log_dir": "",
        "target_file": "",
        "loggers": list(_ROUTE_EVENT_SINK_LOGGERS),
        "actions": sorted(_ROUTE_EVENT_SINK_ACTIONS),
        "handler": None,
    }

    if not route_event_sink_enabled(enabled):
        status["reason"] = "disabled_by_switch:%s" % _ROUTE_EVENT_SINK_ENV
        _ROUTE_EVENT_SINK_STATE = status
        return status

    override_dir = log_dir
    if override_dir is None:
        override_dir = (os.environ.get(_ROUTE_EVENT_SINK_DIR_ENV) or "").strip() or None

    try:
        from agent.monitoring.loki import LokiClient

        # enabled=False ⇒ 复用 loki.py 的本地 JSONL 通道，不做网络推送/超时等待
        client = LokiClient(enabled=False)
        if override_dir:
            os.makedirs(override_dir, exist_ok=True)
            client._local_log_dir = str(override_dir)  # 仅测试/运维覆盖目录时使用

        handler = RouteEventJsonlHandler(client)
        attached = []
        for name in _ROUTE_EVENT_SINK_LOGGERS:
            target = logging.getLogger(name)
            target.addHandler(handler)
            # 【不改既有语义】propagate 保持原值（默认 True）：
            # logging.basicConfig 的控制台输出、LOG_REQUEST_PRINT 等语义均不受影响。
            attached.append(name)
        _ROUTE_EVENT_SINK_HANDLERS = [(name, handler) for name in attached]

        sink_dir = os.path.abspath(str(client._local_log_dir))
        status.update({
            "installed": True,
            "reason": "ok",
            "log_dir": sink_dir,
            "target_file": os.path.join(
                sink_dir, datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"),
            "handler": handler,
        })
    except Exception as exc:  # noqa: BLE001 装配失败降级，不阻断启动
        status["reason"] = "install_failed:%s: %s" % (type(exc).__name__, exc)

    _ROUTE_EVENT_SINK_STATE = status
    return status


def route_event_sink_stats():
    """当前 sink 的写盘计数（诊断用；未装配返回 None）"""
    handler = (_ROUTE_EVENT_SINK_STATE or {}).get("handler")
    return handler.stats() if handler is not None else None


# [B2-ROUTE-SINK-END]


app = Flask(__name__, static_url_path='/static-assets')
app.static_folder = os.path.join(os.path.dirname(__file__), 'static')
app.template_folder = os.path.join(os.path.dirname(__file__), 'templates')

# ════════════════════════════════════════════════════════════════════════════
# [C2] 请求入口并发闸门（背压：并发硬上限 + 有界排队超时）
# ════════════════════════════════════════════════════════════════════════════
# 【解决什么】waitress threads=16 之上没有任何**准入**控制，而 waitress 的
#   channel_timeout=120 **不是排队超时**：请求解析完成就已进 channel.requests，
#   maintenance() 只回收空闲通道（server.py:342-351）⇒ 第 17~100 个请求在服务端
#   无限期排队，只能靠客户端自己超时（Q8 第 1/3.4 节、主报告 P3/P4）。
# 【怎么做】WSGI 中间件包住 app（**请求入口**，不碰 Flask 内部钩子、不碰 serve()）：
#   acquire → 执行 → 响应迭代结束或 close 时 release，严格成对（异常路径也归还，
#   否则额度泄漏会把闸门永久堵死）。超过并发上限的请求在闸门处**有界等待**，
#   等满 CP_HTTP_QUEUE_TIMEOUT（默认 20s）仍拿不到额度 ⇒ 429 +
#   error_code=SERVER_BUSY_TIMEOUT（**可识别拒绝，不是静默排队**）。
# 【豁免】/api/health、/api/heartbeat、/metrics、/static、/favicon.ico 不进闸门 ——
#   否则 A1 的就绪门自证、看门狗与前端状态栏高频探针会被并发抖动假性打红。
# 【正交】threads=16 决定"能同时跑多少"，本闸门决定"允许多少个开始跑"；
#   上限 8 = 线程数的一半，给健康采集/后台定时任务留余量（实测见 C2.md 压测节）。
# 【回滚】CP_HTTP_CONCURRENCY_GATE=0 ⇒ 不装中间件，行为回到现状。
# 【失败姿态】装配失败只 warning：规避逻辑不得引入新的启动硬依赖。
try:
    from agent.rate_limiter import (
        ConcurrencyGateMiddleware as _ConcurrencyGateMiddleware,
        build_http_gate_from_env as _build_http_gate_from_env,
    )
    _http_gate = _build_http_gate_from_env()
    if _http_gate is not None:
        app.wsgi_app = _ConcurrencyGateMiddleware(app.wsgi_app, _http_gate)
except Exception as _http_gate_err:  # noqa: BLE001 闸门装不上不得阻断启动（降级=现状）
    _http_gate = None
    logger.warning("[背压] HTTP 并发闸门装载失败（降级为无闸门，不阻断启动）: %s",
                   _http_gate_err)

# ── S2-02 审计平权（P7.2-24）：UI 写路由统一入链 ──
# 位置：紧跟 app 构造之后注册 before/after/teardown 钩子，凡 POST/PUT/PATCH/DELETE
# 一律落进与 Agent 相同的链式审计表（source="ui"）；新增写路由无需逐个改造。
# 开关：AUDIT_UI_ENABLED=0 可关闭；审计异常绝不阻断业务请求（best-effort）。
try:
    from agent.audit.ui_middleware import install_flask_audit
    _ui_audit_recorder = install_flask_audit(app)
    logger.info("[启动] UI 写路由审计已安装（source=ui，P7.2-24 审计平权）")
except Exception as _audit_e:  # noqa: BLE001 审计不可用不阻断启动
    _ui_audit_recorder = None
    logger.warning(f"[启动] UI 写路由审计安装失败（不阻断启动）: {_audit_e}")

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

# 注册全部插件 blueprint（插件化机制 T1.1 装配器 + T4.1 目录扫描动态装载）
# plugins/__init__.py 显式清单 = 「内置插件」；loader.load_all() 补扫目录中
# 显式清单之外的插件（新插件丢进 plugins/ 即生效，无需改任何代码）。
try:
    from plugins import loader
    _loader_new = loader.load_all()  # 单插件损坏只记日志，不阻断启动
    _loader_registered = loader.register_blueprints(app)
    logger.info(
        f"[启动] 插件装配完成：目录扫描新发现 {_loader_new} 个插件，"
        f"蓝图注册 {_loader_registered} 个（共 {len(get_plugins())} 个插件）"
    )
except Exception as _e:
    # 装配器自身异常时回退显式清单路径，保证内置插件不丢
    logger.warning(f"[启动] 插件动态装配失败（回退显式清单路径）: {_e}")
    for _p in get_plugins():
        if _p.blueprint is not None:
            app.register_blueprint(_p.blueprint)

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
    
    环境变量 LOG_REQUEST_PRINT=0 时降级为静默（仅 logger.debug 记录），
    用于生产环境控制台降噪；默认开启打印（向后兼容）。
    """
    _print_enabled = os.environ.get("LOG_REQUEST_PRINT", "1").strip().lower() not in ("0", "false", "no")
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
                # 打印成功日志到控制台（受 LOG_REQUEST_PRINT 控制，默认开启）
                if success and _print_enabled:
                    print("\n" + "="*60)
                    print(f"📡 API 请求日志 [{endpoint}]")
                    print("-"*60)
                    for log in logs:
                        print(log)
                    print("="*60 + "\n")
                # 降级模式：保留 DEBUG 级结构化记录（不刷屏但可查）
                if _print_enabled is False:
                    logger.debug("[api] %s %s %s", request.method, request.path, logs[-1] if logs else "")
            
            return response
        return decorated
    return decorator


# ── 多会话管理器（保留 _CHAT_HISTORY 作为向后兼容的缓存） ──
_session_mgr = SessionManager(sessions_dir="./data/sessions")
# 会话分组（项目/用途归类；groups.json 独立持久化，见 SessionGroupStore）
_session_groups = SessionGroupStore(sessions_dir="./data/sessions")
# 「已添加的工作区」记忆（仿 DSH 添加工作区；workspaces.json）
_workspace_registry = WorkspaceRegistry(sessions_dir="./data/sessions")

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
# 注：_window_sensor_consented 已随 /api/window/consent 与 /api/permission/toggle
#     迁入 plugins/safety.py（任务 T1.7），此处不再持有。

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

# ── 【TASK-06】把 SA 预授权钩子装进工具闸门（**预授权不是旁路**）──────────────
# 为什么必须在这里显式调用：`agent/security/service_account.py::install_gate_hook()`
#   刻意**不**在 import 期自动安装（导入期副作用会让"只想读个常量"的调用方也改全局
#   状态）。而不装它的后果是**静默**的：SA 无法凭 scope 通过 L2/L3，非交互场景只能
#   拿到拒绝 —— 方向更严，但"SA 预授权"这项交付物等于没接线（名义有、实际无）。
# 为什么按 D4 只告警不抛：钩子装不上的后果是"更严"，不该阻断 5678 启动。
def _install_service_account_hook():
    try:
        from agent.security.service_account import install_gate_hook
        if install_gate_hook():
            logger.info("SA 预授权钩子已装入工具闸门（TASK-06）")
        else:
            logger.warning("SA 预授权钩子安装失败（SA 将无法凭 scope 通过 L2/L3，属更严的一侧）")
    except Exception as _e:  # noqa: BLE001 不阻断启动（D4）
        logger.warning(f"SA 预授权钩子加载失败（不影响启动，SA 预授权不可用）: {_e}")


_install_service_account_hook()


# ── 【TASK-06】鉴权配置状态：启动告警（**不阻断**）────────────────────────────
# TASK-06 §3 第 5 步第 2 项第 ① 步：本部署实测**未配置任何令牌**（只有"未配就不校验"
# 的 fail-open）。一步改成 fail-closed 会当场 401 掉本机 UI 与全部脚本 ⇒ 先做
# "让它可见"：启动时明确告警，并把状态暴露在健康/状态面（见 routes_panorama）。
# 迁移的 ②③④ 步与回退路径见 `docs/rfc/鉴权迁移.md`。
def _warn_if_auth_unconfigured():
    try:
        from agent.server_auth import auth_status
        st = auth_status()
        if not st.get("configured"):
            logger.warning(
                "【鉴权】未配置任何 API 令牌（FLASK_API_TOKEN / CP_UI_TOKENS 皆为空）"
                "⇒ 所有端点**不做令牌校验**（fail-open）。这是刻意的迁移第 ① 步，"
                "不是缺陷；收口路径与回退见 docs/rfc/鉴权迁移.md。"
                "如需立即启用：生成令牌写入 .env（scripts/gen_api_token.py）。")
        else:
            logger.info("【鉴权】已配置令牌（source=%s，映射条目=%s）",
                        st.get("source"), st.get("token_map_size"))
    except Exception as _e:  # noqa: BLE001 状态探测失败不得影响启动（D4）
        logger.warning(f"鉴权状态探测失败（不影响启动）: {_e}")


_warn_if_auth_unconfigured()

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

# ── 技能配置管理器 ──
# 【legacy 迁移】SkillsManager 数据源从 data/skills.json 切换到统一技能
# 注册表（SkillRegistry：主轨 JSON + 文件轨 skill.md），data/skills.json 不再
# 是权威/写入目标。保留 get_all/toggle/add/delete 旧接口兼容 /api/assets 与
# 旧 /api/skills 路由，行为不变但状态落到主轨/文件轨。

class SkillsManager:
    """管理云枢的技能配置（基于 SkillRegistry，legacy 迁移后）"""

    def _reg(self):
        from agent.skills_mgmt.registry import SkillRegistry
        return SkillRegistry()

    def _load(self) -> dict:
        return {"skills": self._reg().as_legacy_rows()}

    def _save(self, data: dict):
        # 迁移后：skills.json 不再是权威写入目标。行内 enabled 变更由
        # toggle/add/delete 直接落主轨/文件轨；此处保留为 no-op 兼容。
        pass

    def get_all(self) -> list:
        return self._reg().as_legacy_rows()

    def toggle(self, skill_id: str) -> dict:
        return self._reg().toggle(skill_id)

    def update_params(self, skill_id: str, params: dict) -> dict:
        # params 仅主轨技能有（default_params）；文件轨技能无 params 概念
        from agent.skills_mgmt.registry import SkillRegistry
        return SkillRegistry()._svc().update(
            skill_id, {"default_params": params})

    def add(self, skill: dict) -> dict:
        from agent.skills_mgmt.registry import SkillRegistry
        svc = SkillRegistry()._svc()
        try:
            svc.create_manual({
                "id": skill.get("id", ""),
                "name": skill.get("name", skill.get("id", "")),
                "content": skill.get("content", "# " + skill.get("name", "")),
                "content_type": skill.get("content_type", "markdown"),
                "description": skill.get("description", ""),
                "enabled": skill.get("enabled", True),
            })
            return {"ok": True, "id": skill.get("id")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

    def _has_extension_record(self, skill_id: str) -> bool:
        """该技能在扩展存储中是否有记录（**仅用于留痕**，不参与门禁判定）

        取不到（扩展存储不可用）时报 False 并继续 —— 门禁只认「主轨有无记录」，
        留痕字段的成败绝不改变删除与否。
        """
        try:
            from agent.extensions.base import ExtensionType
            from agent.extensions.store import ExtensionStore
            return ExtensionStore().get(ExtensionType.SKILL, skill_id) is not None
        except Exception:  # noqa: BLE001
            return False

    def _warn_structured(self, payload: dict, fallback: str) -> None:
        """结构化 warning 留痕；log_dict 不可用时退化为纯文本

        【不易】留痕是**观测**不是**门禁**：无论留痕成功与否，删除路径行为不变
        （故此处吞异常；该 import 也绝不放在 delete 的既有 try 之外，免得留痕
        失败反过来改变删除结果）。
        """
        try:
            from agent.logging_utils import log_dict
            logger.warning(log_dict(payload))
        except Exception:  # noqa: BLE001
            logger.warning(fallback)

    def delete(self, skill_id: str, force: bool = False) -> dict:
        """删除技能（主轨有记录 → 多轨删除；文件轨独占 → 须显式 force=True）

        【加固 · 与 agent/extensions/skills_installer.py::remove_skill 同型】
        可达条件（实测）：主轨 svc.store.get(skill_id) 为 None **且** 文件轨
        svc.file_store.get_metadata(skill_id) 非 None ⇒ 原实现直接走
        svc.file_store.delete() → agent/skills_mgmt/file_store.py::delete 的
        shutil.rmtree，把 data/skills_repo/<id>/ 整棵树（含 scripts/ 与 temp/）
        **不可逆**删除。

        这类「文件轨独占」技能是**技能仓库/内置 persona 技能**（front matter 提供），
        不是扩展安装产物。而其 HTTP 入口 POST /api/skills/delete
        （plugins/skills.py:539）**可由网络直达**，原来的唯一守卫是「内置技能白名单」，
        实测只覆盖 data/skills_repo 下 25 个技能中的 7 个 ⇒ 其余 18 个真实技能可被
        不可逆删除。
        ⇒ 本方法对「文件轨独占」技能默认**拒绝删除**（写结构化 warning 留痕 +
        返回可读原因）；确需删除必须显式 force=True。

        Args:
            skill_id: 技能 ID
            force: True 时允许删除「文件轨独占」技能（默认 False）；
                主轨存在的既有删除路径与 force 无关，**一字未改**

        Returns:
            dict：成功为 {"ok": True}；拒绝为
            {"ok": False, "refused": True, "error": <可读原因>}
            （refused 供路由区分「显式拒绝」与「未找到」，以免路由下方的扩展存储
            清理把一次拒绝翻成 ok=True 的假成功）
        """
        from agent.skills_mgmt.registry import SkillRegistry
        svc = SkillRegistry()._svc()
        try:
            # 主轨有→删主轨；否则文件轨有→删文件轨；否则报未知
            if svc.store.get(skill_id) is not None:
                # ── 既有删除路径（多轨同步）：与 force 无关，一字未改 ──
                svc.delete(skill_id)
                return {"ok": True}
            meta = svc.file_store.get_metadata(skill_id)
            if meta is None:
                return {"ok": False, "error": f"未知技能: {skill_id}"}
            # ── 加固：文件轨独占 ⇒ 默认拒绝（除非显式 force=True）──
            has_ext = self._has_extension_record(skill_id)
            if not force:
                self._warn_structured({
                    'module_name': 'app_server',
                    'action': 'skills_manager.delete.refused_file_track_only',
                    'skill_id': skill_id,
                    'has_extension_record': has_ext,
                    'msg': ('[技能管理器] 拒绝删除文件轨独占技能 %s：'
                            '主轨无记录 ⇒ 该目录不是扩展安装产物'
                            '（确认删除请显式 force=True）' % skill_id),
                }, f'[技能管理器] 拒绝删除文件轨独占技能: {skill_id}')
                return {
                    "ok": False,
                    "refused": True,
                    "error": (
                        f"拒绝删除: 技能 {skill_id} 仅存在于文件轨（主轨无记录），"
                        f"其目录由技能仓库/内置技能提供，删除不可逆；"
                        f"如确需删除请显式 force=True"
                    ),
                }
            self._warn_structured({
                'module_name': 'app_server',
                'action': 'skills_manager.delete.force_file_track_only',
                'skill_id': skill_id,
                'has_extension_record': has_ext,
                'msg': '[技能管理器] 显式 force=True 删除文件轨独占技能: %s' % skill_id,
            }, f'[技能管理器] 显式 force=True 删除文件轨独占技能: {skill_id}')
            svc.file_store.delete(skill_id)
            return {"ok": True}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}

_skills_mgr = SkillsManager()


# ════════════════════════════════════════════════════════════
#  API 路由
# ════════════════════════════════════════════════════════════
# 健康/传感器/状态/模式/规划/认知/全景/人格/心跳 路由已迁移至 plugins/status.py（任务 T1.4）

@app.route("/api/plugins", methods=["GET"])
def api_plugins():
    """插件元信息 manifest（插件化机制 T1.1）"""
    return jsonify(plugin_manifest())


@app.route("/api/plugins/reload", methods=["POST"])
@require_token
def api_plugins_reload():
    """刷新插件清单（动态装载 T4.1）：扫描 plugins/ 目录重建注册表，无需重启进程。

    - 成功：返回最新 manifest（新插件丢进 plugins/ 无需改任何代码即可被发现）；
    - 失败：保留旧注册表（先构建临时注册表，成功才替换），返回 500 + 错误摘要；
    - 说明：Flask 已注册 blueprint 不可注销/不可在首个请求后追加，路由在启动时
      统一挂载——新增/删除插件的路由生效/失效需重启进程（manifest 即时刷新）。
    """
    from plugins import loader
    try:
        new_manifest = loader.refresh_manifest()
        return jsonify({"ok": True, **new_manifest})
    except Exception as exc:
        logger.error(f"[plugins] 刷新插件清单失败（旧注册表已保留）: {exc}")
        return jsonify({"ok": False, "error": f"刷新插件清单失败（旧注册表已保留）: {exc}"}), 500


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
#  后台任务（AsyncExecutor）HTTP 面（/api/background/tasks*）
#  供「会话任务 → 后台任务」下拉查看/取消系统后台运行的任务
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_background import register_routes as reg_background
    reg_background(app, lambda: None)
    logger.info("后台任务路由已注册 (/api/background/tasks*)")
except Exception as e:
    logger.error("加载后台任务路由失败: %s", e)


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
#  过程蒸馏路由（/api/process-distill/*）
#  知识库/素材 → 子代理蒸馏 → workflow/skill 固化
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_process_distill import register_routes as reg_process_distill
    reg_process_distill(app, lambda: None)
    logger.info("过程蒸馏路由已注册 (/api/process-distill/*)")
except Exception as e:
    logger.error("加载过程蒸馏路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  可视化编辑器工作流草稿路由（/api/visual-workflows/*）
#  工作台"可视化编辑"页保存/加载手工编排的 workflow 图；
#  与 workflow-learning 的学习工作流存储完全隔离（不触碰 matcher/executor）。
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_visual_workflows import register_routes as reg_visual_workflows
    reg_visual_workflows(app, lambda: None)
    logger.info("可视化工作流草稿路由已注册 (/api/visual-workflows/*)")
except Exception as e:
    logger.error("加载可视化工作流路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  审批 HTTP 面（/api/approval/*，TASK-S4-01）
#  §5.7⑦ 审批面安全（会话绑定 / CSRF / 链接 ≤900s / 二次认证 / DOM 隔离）
#  ⚠ 此前只在 `agent/server_routes/__init__.py::register_all_routes` 中登记，
#    而该函数**无调用方**（S2-02 盘点已记为死代码）⇒ 审批路由从未真正注册。
#    本任务（S6-01）补上显式注册，使审批收件箱与批量裁决真正可达。
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_approval import register_routes as reg_approval
    reg_approval(app, lambda: None)
    logger.info("审批 HTTP 面已注册 (/api/approval/*)")
except Exception as e:
    logger.error("加载审批 HTTP 面失败: %s", e)


# ════════════════════════════════════════════════════════════
#  治理可观测六面板（/api/cp/*，TASK-S6-01）
#  §7 六面板 + 七动作 + 审计导出 + 安全渲染常量；只读为主，写动作走既有审批
# ════════════════════════════════════════════════════════════

try:
    from agent.server_routes.routes_ui_panels import register_routes as reg_ui_panels
    reg_ui_panels(app, lambda: None)
    logger.info("治理可观测面板路由已注册 (/api/cp/*)")
except Exception as e:
    logger.error("加载治理可观测面板路由失败: %s", e)


# ════════════════════════════════════════════════════════════
#  开关中心（/api/cp/settings*，TASK-S7-01）
#  ① 覆盖层启动应用：`needs_restart` 类开关靠这一步才真正生效；
#     无覆盖层文件时**不做任何事**（对既有行为零影响）。
#  ② 路由注册：GET 全量条目 / POST 改值 / POST reset / POST confirm（双人确认）。
# ════════════════════════════════════════════════════════════

try:
    from agent.settings.bootstrap import apply_overrides as _apply_ui_overrides
    _override_result = _apply_ui_overrides()
    if _override_result.get("existed"):
        logger.info("开关覆盖层已应用: applied=%d skipped=%d (%s)",
                    len(_override_result.get("applied", [])),
                    len(_override_result.get("skipped", [])),
                    _override_result.get("overlay"))
except Exception as e:
    logger.error("开关覆盖层启动应用失败（不影响启动）: %s", e)

try:
    from agent.server_routes.routes_settings import register_routes as reg_settings
    reg_settings(app, lambda: None)
    logger.info("开关中心路由已注册 (/api/cp/settings*)")
except Exception as e:
    logger.error("加载开关中心路由失败: %s", e)


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
#  向量记忆路由
#  /api/vector/* 与 /api/knowledge/add 全部由 plugins/memory.py 提供
#  （任务 T1.3 迁移 /api/vector/search；2026-09 补齐 legacy 8 条）。
#
#  【事实更正】此处原注释称"旧版 templates/index.html 已归档，不再调用"，
#  该前提为假：app_server 的 legacy_ui 仍把 templates/index.html 挂在 /legacy
#  （见本文件 legacy_ui 视图），该页面加载 static/js/sidebar/memory.js，其中
#  确实在调用 /api/vector/stats|recent|add|batch_add|clear、/api/knowledge/add。
#  于是这些端点在生产 404（前端调了不存在的后端），而 agent/server_routes/
#  routes_memory.py 虽声明了它们却**从未被本文件注册**（整模块未接线）。
#  现已在 plugins/memory.py 按插件既有风格补齐这 8 条（含 /api/memory/review），
#  routes_memory.py 随之退役删除（其 22 条路径均已确认有活体，未重复注册）。
#  守门测试：tests/unit/test_legacy_memory_routes.py（用真实入口 import app_server
#  枚举 url_map + 校验 memory.js 每个 /api fetch 路径都能命中）。
# ════════════════════════════════════════════════════════════

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

# ── W5/TASK-08 ⑤：检索降级可见化（embedding_health → 健康面）──────────────
# 【契约（已冻结，不改签名）】agent/tool_router_hybrid.py:1387 `embedding_health()`
#   经 agent/tool_router_hybrid.py:1407 `get_hybrid_retriever()` 取单例；
#   字段：mode / init_failed / worker_alive / available / failure_total /
#   restart_attempts / max_restart_attempts / restarting / retry_exhausted /
#   next_restart_in_sec / last_failure（+ retriever_degraded）。
# 【为什么单开 /api/health/retrieval，而不塞进 /api/health 的响应体】
#   实测 `GET /api/health` 的响应体是**数组**（传感器读数列表），前端
#   `static/js/sidebar/status-panel.js:30` 直接 `data.forEach(m => ...)`；
#   且 tests/unit/test_auth_migration_step1.py:114-123（"不改 /api/health 的响应形状"）
#   与 tests/contract/contract_definitions.py:200-221（_root=array）都把该形状**钉死为契约**。
#   ⇒ 按"新增只读端点"落地，与 TASK-06 的 /api/health/auth 同一先例
#   （见 agent/server_routes/routes_panorama.py:182-188 的同类论证）：
#   **降级可见（不静默）+ 零破坏既有消费者**。
# 【回滚开关】常量 `_WORKER_MAX_RESTARTS`（tool_router_hybrid.py:86）置 0 即退回旧行为
#   ⇒ 本接线**不新增任何 env 开关**（D5）。
# 【同族要求】06-基线台账 §3.5 D1「ChromaDB 静默降级可见化」：降级必须可见，不得静默。
@app.route("/api/health/retrieval", methods=["GET"])
def api_health_retrieval():
    """混合检索器（embedding worker）健康状态（**只读**；永不 500）。"""
    try:
        from agent.tool_router_hybrid import get_hybrid_retriever
        retriever = get_hybrid_retriever()
    except Exception as e:  # noqa: BLE001 探针故障不得让端点 500
        return jsonify({"status": "error", "degraded": None, "mode": "unknown",
                        "note": f"检索器不可得: {type(e).__name__}"}), 200

    if retriever is None:
        # 未启用/初始化失败：**明确"不可观测"**，而不是静默当成健康
        return jsonify({"status": "unknown", "degraded": None, "mode": "unknown",
                        "retriever": None,
                        "note": "HybridRetriever 不可用（未初始化或初始化失败）"}), 200

    try:
        health = dict(retriever.embedding_health() or {})
    except Exception as e:  # noqa: BLE001
        return jsonify({"status": "error", "degraded": None, "mode": "unknown",
                        "note": f"embedding_health() 失败: {type(e).__name__}"}), 200

    mode = str(health.get("mode") or "unknown")
    # 降级口径取值域并集：mode==bm25_only（worker 起不来）或 retriever_degraded（融合层判降级）
    degraded = bool(mode == "bm25_only" or health.get("retriever_degraded")
                    or health.get("retry_exhausted"))
    body = dict(health)                     # 冻结契约字段逐字透传（不改名、不丢字段）
    body["status"] = "degraded" if degraded else "ok"
    body["degraded"] = degraded
    body["degrade_to"] = "bm25_only" if degraded else None
    return jsonify(body), 200


# T5：监控仪表盘（质量/链路追踪，契约测试已定义）
try:
    from agent.server_routes.routes_dashboard import register_routes as reg_dashboard
    reg_dashboard(app, lambda: None)
    logger.info("监控仪表盘路由已注册 (/api/dashboard/*)")
except Exception as e:
    logger.error("加载监控仪表盘路由失败: %s", e)

# ── 主线管理（/api/agent-lines/*）──
#  ⚠ 必须在此显式注册：`agent/server_routes/__init__.py::register_all_routes` 是
#    **无调用方的死代码**（同 routes_approval 当年的前科，见上方 857 行注释）。
#    只把它加进 register_all_routes 不会生效 —— 表现是前端拿到 HTTP 404。
try:
    from agent.server_routes.routes_agent_lines import register_routes as reg_agent_lines
    reg_agent_lines(app, lambda: None)
    logger.info("主线管理路由已注册 (/api/agent-lines/*)")
except Exception as e:
    logger.error("加载主线管理路由失败: %s", e)

# T6：orchestrator 语义层配置热更（/api/orchestrator/semantic-config）
#  【2026-09-18 接线】原注释写"路由由 agent/api_gateway.py 与 orchestrator 提供，
#    此处不再接线"——**实测不成立**：`api_gateway.py` 里根本没有这个路径，
#    `url_map` 里也没有（线上 404）。而 `agent/orchestrator/orchestrator.py` 明确在读
#    这一层覆盖（`_SEM_API_OVERRIDE` 为最高优先级）⇒ 写入侧不存在 = 顶层永远读不到值。
#    这两个 handler 原住在 `routes_config.py`（整模块**从未注册**，29 条路径里 27 条由
#    别处提供）⇒ 只把缺失的这对摘到 `routes_semantic_config.py` 单独接线，
#    避免整模块注册造成的同路径重复。
try:
    from agent.server_routes.routes_semantic_config import register_routes as reg_sem_cfg
    reg_sem_cfg(app, lambda: None)
    logger.info("语义层配置路由已注册 (/api/orchestrator/semantic-config)")
except Exception as e:
    logger.error("加载语义层配置路由失败: %s", e)

# ════════════════════════════════════════════════════════════
#  能力层非 LLM 入口（TASK-05 §3 第 4 步）
#  /capabilities/tools · /capabilities/invoke · /capabilities/skills/search
#  · /capabilities/<name> · /capabilities/health
#
#  【为什么必须在此显式注册】`agent/server_routes/__init__.py::register_all_routes`
#    是**无调用方的死代码**（历史教训见本文件上方 routes_approval 的注释与
#    `agent/server_routes/__init__.py` 的模块 docstring）——只把它加进那里不会生效，
#    表现是前端/CI 拿到 HTTP 404。
#  【D4】整段包在 try/except 里：能力层路由注册失败**不得**阻塞平台启动。
#    开关 `CP_CAPABILITY_API_ENABLED=0` 时 `register_routes` 直接返回，
#    `/capabilities/*` 全部不存在（回滚方式，见 TASK-05 §6）。
# ════════════════════════════════════════════════════════════
try:
    from agent.server_routes.routes_capabilities import register_routes as reg_capabilities
    reg_capabilities(app, lambda: None)
except Exception as e:
    logger.error("加载能力层路由失败（/capabilities/* 将不可用，平台照常启动）: %s", e)

# T7：会话交接（原 routes_sessions.register_handoff_routes 已移除，
# 会话 API 由 plugins/chat.py 提供，此处不再接线）

# T8.1：多租户管理 API（原 routes_tenants 已移除，多租户由 agent/multi_tenant.py
# 提供，管理端点走 plugins/admin.py，此处不再接线）


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


# ════════════════════════════════════════════════════════════
#  权限控制面板 API（/api/permission/* 已迁移至 plugins/safety.py，
#  任务 T1.7；_permission_toggles 仅被该域使用，一并迁入）
# ════════════════════════════════════════════════════════════


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
    """云枢 React SPA 入口（build:flask 同步自 yunshu-ui/dist → templates/yunshu.html）。

    修复 2026-08-31：原实现 redirect("/static/chat") 指向不存在的路径（404 死链），
    React SPA（templates/yunshu.html，引用 /static/assets/*）无任何路由可达。
    现改为直接渲染 SPA 入口；前端以 base=/static/ 构建，资源经 /static/<path> 路由服务。

    修复 2026-09-19（**每次请求从磁盘读取**）：非 debug 模式下 Jinja 会**缓存**已编译
    模板，`npm run build:flask` 重新构建后进程仍返回旧 HTML；而构建会先清空
    static/assets，旧 HTML 引用的 chunk 已不存在 ⇒ 刷新页面白屏（实测
    /static/assets/index-<旧hash>.js 404）。故这里直接读文件（dist HTML 无 Jinja
    占位符，原文即可），并显式 no-store，保证重新构建后刷新即生效、无需重启服务。
    """
    import os as _os
    from flask import Response
    tpl_path = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)),
                             "templates", "yunshu.html")
    try:
        with open(tpl_path, "r", encoding="utf-8") as f:
            html = f.read()
    except OSError:
        # 构建产物缺失（未跑过 build:flask）→ 回退 Jinja 渲染，保持既有行为
        html = render_template("yunshu.html")
    response = Response(html, mimetype='text/html; charset=utf-8')
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

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
#  注：agent/api_gateway_flask.py 为可选组件（当前未提供，缺失时跳过不阻断），
#      /api/open/* 与 /api/docs 由恢复该模块后自动生效。
# ════════════════════════════════════════════════════════════
try:
    from agent.api_gateway_flask import register_gateway as reg_gateway
    reg_gateway(app)
    logger.info("API 网关适配层已挂载 (/api/open/*, /api/docs)")
except ImportError:
    # 【TASK-08 子工作流 D / E1g】原为 `logger.debug` ⇒ 生产日志（INFO 及以上）
    # **一行都没有**，缺网关的机器与正常机器从外部不可区分。降级本身是**对的**
    # （可选组件不该阻断启动，D4 要求继续降级），缺的是「可见性」：
    # 现改为结构化 ERROR 并登记进启动诊断表，由文件末尾的汇总一次性出报。
    logger.error("API 网关适配层未安装（agent/api_gateway_flask.py 缺失）——"
                 "/api/open/* 与 /api/docs 端点本次启动**不可用**")
    try:
        from agent.startup_diagnostics import record_degradation
        record_degradation(
            "agent.api_gateway_flask", kind="module_missing",
            purpose="API 网关适配层：/api/open/* 开放端点 + 限流 + 配额 + /api/docs",
            impact="开放 API 网关整体不可用：/api/open/* 与 /api/docs 线上 404；"
                   "前端若依赖 /api/docs 则文档页不可用",
            error="No module named 'agent.api_gateway_flask'")
    except Exception:  # noqa: BLE001  诊断登记失败不得阻断启动
        pass
except Exception as e:
    logger.warning("加载 API 网关适配层失败: %s", e)
    try:
        from agent.startup_diagnostics import record_degradation
        record_degradation(
            "agent.api_gateway_flask", kind="register_error",
            purpose="API 网关适配层：/api/open/* 开放端点 + /api/docs",
            impact="开放 API 网关未挂载，相关端点 404",
            error=str(e))
    except Exception:  # noqa: BLE001
        pass

# ── 启动期降级汇总（TASK-08 D / E1g）──────────────────────────────
# 位置：**所有路由/适配层注册之后**（本文件末尾），才能看到完整失败集合。
# 结构：① AST 机械审计"本应可导入的模块"是否真的在（覆盖 30+ 处显式 try/except
#        注册块——它们没有循环遍历，只有机械提取才不会漏）；
#       ② 汇总成一条结构化告警。
# D4：两层都包在 try/except 里，任何诊断故障都不得阻断启动。
try:
    from agent.startup_diagnostics import audit_and_record, emit_startup_report
    _missing = audit_and_record(__file__)
    if _missing:
        logger.error("[启动诊断] AST 审计发现 %d 个 app_server.py 引用但不可导入的模块: %s",
                     len(_missing), ", ".join(m["module"] for m in _missing))
    emit_startup_report(logger)
except Exception as _diag_err:  # noqa: BLE001  诊断层故障绝不阻断启动
    logger.debug("启动诊断不可用（忽略）: %s", _diag_err)

# 程序退出时停止窗口传感器
import atexit

@atexit.register
def _cleanup_window_sensor():
    global _window_sensor
    if _window_sensor:
        _window_sensor.stop()


# ── 优雅关闭：收到信号即显式落盘「会话最后一条 LLM 通信」（W5/TASK-08 · R2）──
# 【为什么必须补这一条】事实依据：
#   ① `atexit` 对 **SIGTERM/SIGKILL 不触发**（Python 官方语义：被信号杀死不进退出钩子）；
#   ② 因此"关闭时必存最后一条"此前实际只由 agent/llm_monitor.py:218-223 的
#      「每条即写 + 5s 节流」（PERSIST_MIN_INTERVAL_S=5.0）保证
#      ⇒ **最坏暴露窗口 ≤5s**：节流跳过的最后 N 条若恰在此窗口内进程被 SIGTERM，
#      则该条不落盘（虽有 llm_monitor 的 atexit 兜底，但 atexit 同样不触发）。
# 【本钩子只补这一条】"收到信号 ⇒ 立即调用 persist_session_last()"。
#   **不改** llm_monitor 既有的 5s 节流逻辑，也**不改**其「每条即写 / atexit 兜底 /
#   启动回填」三重机制（只做一次显式调用，不新增开关）。
# 【Windows 事实】`os.kill(pid, SIGTERM)` 在本平台走 TerminateProcess **不跑处理器**
#   （见 scripts/dev/graceful_shutdown_persist_probe.py 的 --win-term-demo 探针）；
#   本平台真正可投递的终止信号是 CTRL_C_EVENT / CTRL_BREAK_EVENT（SIGINT/SIGBREAK）。
#   故三个信号都注册：SIGTERM（POSIX 语义正确）+ SIGINT/SIGBREAK（Windows 可投递）。
_GRACEFUL_SHUTDOWN_DONE = False


def _graceful_shutdown_persist(signum=None, _frame=None):
    """信号处理器：先把「会话最后一条 LLM 通信」显式落盘，再按原信号语义退出。

    幂等（重入直接返回）；退出路径绝不抛异常、绝不阻塞。
    """
    global _GRACEFUL_SHUTDOWN_DONE
    if _GRACEFUL_SHUTDOWN_DONE:
        return
    _GRACEFUL_SHUTDOWN_DONE = True

    ok = False
    try:
        # 延迟导入：退出路径不得为持久化而新建监控器（persist_session_last 自身已守卫）
        from agent.llm_monitor import persist_session_last
        ok = bool(persist_session_last())
    except Exception as e:  # noqa: BLE001 退出路径故障绝不阻断退出
        logger.debug("[关闭] LLM 会话快照落盘失败（忽略）: %s", e)

    try:
        logger.info("[关闭] 收到信号 %s：会话最后一条 LLM 通信显式落盘=%s", signum, ok)
    except Exception:  # noqa: BLE001
        pass

    # 保持"以该信号退出"的既有语义：恢复默认处理后重新投递（不吞信号）
    try:
        if signum is not None and hasattr(signal, "SIG_DFL"):
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)
    except Exception:  # noqa: BLE001 平台不支持重投递时走下面的硬退出
        pass
    os._exit(0 if signum == getattr(signal, "SIGINT", None) else 128 + int(signum or 0))


def _install_graceful_shutdown_hooks():
    """在服务进程内注册优雅关闭信号处理器（SIGTERM / SIGINT / SIGBREAK）。

    Returns:
        已成功注册的信号名列表（供启动日志与探针断言；无可用信号时为空列表）
    """
    installed = []
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _graceful_shutdown_persist)
            installed.append(name)
        except (ValueError, OSError, RuntimeError) as e:
            # 非主线程 / 平台不支持：跳过该信号，不影响其余信号
            logger.debug("[关闭] 信号 %s 注册失败（忽略）: %s", name, e)
    logger.info("[关闭] 优雅关闭钩子已注册: %s（收到信号即落盘会话最后一条 LLM 通信）",
                ", ".join(installed) or "无")
    return installed


if __name__ == "__main__":
    # 脚本直跑（python app_server.py）时本模块名为 __main__；插件视图函数内的
    # 延迟导入 `from app_server import _Yunshu`（PLAN-1 §4）会把 app_server.py
    # 重新导入一份，导致模块级代码重跑（Prometheus Counter 重复注册 → ValueError）。
    # 把 __main__ 注册为 app_server，使延迟导入解析到运行中的本模块（须在 serve 前）。
    import sys as _sys
    _sys.modules.setdefault("app_server", _sys.modules["__main__"])

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

    # ── [A1] 子进程预防：把本进程放进 KILL_ON_JOB_CLOSE 的 Job Object ──
    # 旧问题：embedding worker（agent/tool_router_hybrid.py:991 subprocess.Popen）
    # 与 reranker（agent/skills_mgmt/reranker.py）是**独立 python 进程**，实测各占
    # 0.5–0.9 GB。而本进程的退出路径（含 taskkill /F == TerminateProcess）既不投递
    # 信号也不跑 atexit ⇒ 父进程一死子进程即被孤儿化，继续占内存、继续读盘。
    # 加入本 Job 后，父进程以**任何**方式消失（/F、崩溃、正常退出）都由内核连带
    # 终止整条进程树，无需逐个登记 PID。
    # 【时序】必须早于任何可能 spawn 子进程的初始化：本行以上只有模块导入与本
    #   函数之上的横幅打印，不 spawn 任何子进程。
    # 【失败姿态】装载失败只降级告警，绝不阻断启动（守"规避逻辑不引入新硬依赖"）。
    try:
        from agent.server_port_guard import install_child_process_reaper
        _reaper_status = install_child_process_reaper()
        if _reaper_status.get("installed"):
            logger.info("[启动] 子进程回收 Job Object 已装载：%s", _reaper_status)
        else:
            logger.warning(
                "[启动] 子进程回收 Job Object 未装载（降级：强杀父进程仍可能遗留"
                " embedding/reranker 子进程）：%s", _reaper_status)
    except Exception as e:  # noqa: BLE001 降级不得阻断启动
        logger.warning("[启动] 子进程回收 Job Object 装载异常（降级，不阻断启动）: %s", e)

    # 【A1 时序变更】改动前紧接此处（旧第 1843-1847 行）的
    # `cleanup_port_listeners(5678)` **已移走**：它在"引擎还没起来"的启动早期就
    # taskkill /F 掉旧实例，旧实例一死而新实例若在后续任何一步失败 ⇒ 完全无服务
    # （审计 §1.4）。现在该动作只作为受"就绪门"保护的下游步骤执行，
    # 见下方 guarded_startup 的调用处。

    # ── [B2] 路由决策事件落盘：装配位置有意放在 Job Object 装载之后 ──
    # ① 时序下界：必须在 serve() 之前 —— 路由事件只在请求期产生，装在这里即为
    #    "任何请求可到达之前"。
    # ② 时序上界：必须在上方 install_child_process_reaper（A1 的 Job Object）之后 ——
    #    那一段写明了"本行以上只有模块导入与横幅打印"的不变量；本段会
    #    os.makedirs + 打开文件（全程不 spawn 任何子进程），放到其后可让该不变量
    #    逐字仍然成立。
    # ③ 与 A1 的就绪门/看门狗零耦合：本段不调用 guarded_startup，也不调用
    #    cleanup_port_listeners，更不改动二者的先后与调用关系
    #    （静态核对见 docs/audit_skill_governance/B2.md）。
    try:
        _route_sink = install_route_event_sink()
        if _route_sink.get("installed"):
            logger.info(
                "[启动] 路由事件 sink 已装配：logger=%s → %s（%s=0 可关闭）",
                ",".join(_route_sink["loggers"]),
                _route_sink["target_file"],
                _ROUTE_EVENT_SINK_ENV,
            )
        else:
            logger.warning(
                "[启动] 路由事件 sink 未装配（%s）：路由事件仍只进 stderr",
                _route_sink.get("reason"))
    except Exception as _sink_e:  # noqa: BLE001 装配失败不得阻断启动
        logger.warning("[启动] 路由事件 sink 装配异常（不阻断启动）: %s", _sink_e)

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
        # 技能清理周期任务（孤儿扫描 / 无用淘汰；默认关闭 + dry-run，见
        # skills_mgmt.cleanup_scheduler 配置说明）
        try:
            from agent.skills_mgmt.cleanup_scheduler import register_cleanup_schedulers
            cleanup_tasks = register_cleanup_schedulers()
            print(f"✅ 技能清理定时任务注册: {cleanup_tasks}")
        except Exception as e:
            print(f"⚠️ 技能清理定时任务注册失败（不阻断主流程）: {e}")
        # SLO 指标周报定时生成（§6.7；默认关闭，需 CP_SLO_SCHEDULE_ENABLED=true
        # 或 config.yaml slo_report.enabled=true；默认周一 09:00，存档 docs/zh/周报存档/）
        try:
            from agent.monitoring.slo_report_scheduler import register_slo_report_scheduler
            slo_task = register_slo_report_scheduler(scheduler)
            if slo_task.get("registered"):
                print(f"✅ SLO 周报定时任务注册: {slo_task}")
            else:
                print(f"ℹ️ SLO 周报定时任务未注册: {slo_task.get('reason')}")
        except Exception as e:
            print(f"⚠️ SLO 周报定时任务注册失败（不阻断主流程）: {e}")
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
    
    # 优雅关闭：注册信号处理器 ⇒ 收到 SIGTERM/SIGINT/SIGBREAK 立即显式落盘
    # 「会话最后一条 LLM 通信」（atexit 对信号退出不触发，见上方 R2 注释）
    try:
        _install_graceful_shutdown_hooks()
    except Exception as e:  # noqa: BLE001 钩子注册失败不得阻断启动
        print(f"⚠️ 优雅关闭钩子注册失败（不阻断主流程）: {e}")

    # 打开工作台。**位置有意不变**：若下面的就绪门判定新实例未就绪而放弃启动，
    # 旧实例仍在服务，此时打开浏览器指向的正是仍然健康的旧实例。
    webbrowser.open("http://127.0.0.1:5678")

    # 使用 Waitress 生产级 WSGI 服务器（替代 Flask 内置开发服务器）
    # 多线程 + 纯 Python，Windows 原生兼容
    # 【A1】waitress 的导入放在就绪门**之前**：它是最容易失败的一步，必须归入
    # "清理旧实例之前的可失败初始化"，否则就变成"先杀旧实例、再发现连 waitress
    # 都导入不了"。
    from waitress import serve

    def _startup_preflight():
        """新实例可服务性自证 —— 只有它为真才会去杀旧实例。

        做法：用 Flask 自带的 test_client 在**进程内**打一次 /api/health。
        它覆盖 WSGI 应用装配、路由注册、插件装配等真实链路，但不 bind 端口
        （端口此刻还被旧实例占着，无法 bind）。实测 /api/health 冷态 0.05 s。
        """
        try:
            resp = app.test_client().get("/api/health")
        except Exception as e:  # noqa: BLE001 探针自身出错 = 未就绪（失败关闭）
            return False, "self_health_probe_exception:%s: %s" % (type(e).__name__, e)
        code = getattr(resp, "status_code", 0)
        if code != 200:
            return False, "self_health_probe_status=%s" % code
        return True, "self_health_probe_status=200"

    # ── [A1 就绪门] 清理旧实例 → bind，顺序不可调换 ──
    # 旧行为：启动早期无条件 taskkill /F 掉 5678 旧实例，然后才开始构造引擎；
    #         新实例后续任何一步失败 ⇒ 旧实例已死、新实例没起来 = 完全无服务。
    # 新行为：只在此处、且仅在 ①端口上确实有旧实例 ②新实例进程内自证 /api/health=200
    #         两个条件同时满足时才清理旧实例；任一条不满足 ⇒ 一个进程都不杀，
    #         旧实例继续服务，本进程以退出码 3 退出（不会悄悄退化成"无服务"）。
    # threads 8→16: 高并发压测发现 LLM 长耗时请求占满线程导致排队（Task queue 高发），
    # 提升线程容量缓解排队；LLM 外呼另有 60s 看门狗兜底（orchestrator._run_llm_bounded）
    from agent.server_port_guard import guarded_startup
    _startup = guarded_startup(
        lambda: serve(app, host="127.0.0.1", port=5678, threads=16),
        5678,
        preflight=_startup_preflight,
    )
    if not _startup.get("served"):
        logger.error(
            "[启动] 未进入服务状态（exit_code=%s）：%s",
            _startup.get("exit_code"),
            _startup.get("preflight") or _startup.get("serve_error"))
        sys.exit(int(_startup.get("exit_code") or 1))
