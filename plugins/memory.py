# plugins/memory.py
"""记忆与上下文管理插件（任务 T1.3）。

迁移自 app_server.py 的记忆/上下文域路由（路径与行为 100% 不变）：
  - 上下文监视器：/api/context/*
  - 记忆操作：/api/memory/*
  - 记忆审查：/api/memory/review（legacy，见下）
  - 向量记忆：/api/vector/*（legacy 端点 stats/add/batch_add/item/recent/clear 见下）
  - 知识库写入：/api/knowledge/add（legacy，见下）
  - 窗口监控：/api/memory/windows/*

legacy 端点接线（2026 修复 404）：
  `/legacy` 页面（app_server.py 把 templates/index.html 挂在 /legacy）仍在服务，
  其 static/js/sidebar/memory.js 仍在调用上述 8 条端点。它们原先只声明在
  未接线的 agent/server_routes/routes_memory.py 中（生产 404），现按本插件既有
  风格原样搬入（handler 实现逐字保留，未改语义）；该旧模块随后退役。

共享依赖约定（PLAN-1 §4）：
  - 插件模块顶层只 import flask / plugin_api，绝不顶层 import app_server（循环导入红线）。
  - require_token / log_request / _Yunshu / _cfg / _session_mgr / _window_sensor 等
    共享装饰器与全局保留在 app_server.py，视图函数内延迟 import。
"""
import functools
import json
import logging
import os
import re
from flask import Blueprint, request, jsonify
from .plugin_api import Plugin, context_limit_info, register_plugin

# 【P1-front 第六批 · 2026-10-05】统一响应信封（阶段 2 / R3）。
# 只依赖 stdlib + flask，不会把 app_server 拉进本模块的导入期。
from agent.api_envelope import ok as _ok
from agent.api_envelope import problem as _problem

# 【2026-10-03 审计 P2-5】值域与默认值不再在本文件里抄一份：改 config.py 常量时这里跟着变，
#   否则"面板能设的范围/展示的默认值"会与实际生效的口径悄悄分叉。
from config import (  # noqa: E402
    MEMORY_TOKEN_LIMIT_DEFAULT,
    MEMORY_TOKEN_LIMIT_MIN,
    MEMORY_TOKEN_LIMIT_MAX,
    PER_MESSAGE_SEND_LIMIT_DEFAULT,
    PER_MESSAGE_SEND_LIMIT_MAX,
    PER_MESSAGE_RECV_LIMIT_DEFAULT,
    PER_MESSAGE_RECV_LIMIT_MAX,
)

logger = logging.getLogger(__name__)

bp = Blueprint("memory", __name__)

#: 面板/接口保存的"配置值"重启后会回落到哪里 —— **用户可见文案，必须是事实**。
#: 【2026-10-03 更正】原文案写"重启后回落到 config.yaml:memory.token_limit"，
#:   但运行时配置合成为 DEFAULT → 环境变量 → overrides，**从不读 config.yaml**
#:   （复核实验：谎报 config.yaml 为 65536，Config 仍返回 131072；见 docs/closeout/交付报告_20261003.md §12）。
#:   真实回落目标是**代码默认值**（可被环境变量覆盖）。指向 config.yaml 会让用户改一个无效文件。
RESTART_FALLBACK_NOTE = (
    "面板/接口保存的值是**运行时覆盖**（本进程内生效），重启后回落到**代码默认值**"
    "（环境变量可覆盖）；**config.yaml 不参与运行时配置合成**，改它对本项无效。"
)


def _view(*, auth=False, log=None):
    """延迟应用 app_server 的共享装饰器（require_token / log_request）。

    app_server 在本插件模块被导入时尚在初始化（require_token / log_request 尚未定义），
    因此路由函数先以裸函数注册到 blueprint；首次请求时惰性从 app_server 取回装饰器
    并一次性包装，之后直接命中包装版，行为与迁移前完全一致。

    Args:
        auth: True 时套用 require_token（最外层，与原 @require_token 位置一致）。
        log: 不为 None 时套用 log_request(show_response=log)，与原装饰器参数一致。
    """
    def decorator(f):
        state = {"wrapped": None}

        @functools.wraps(f)
        def proxy(*args, **kwargs):
            if state["wrapped"] is None:
                from app_server import require_token, log_request
                wrapped = f
                if log is not None:
                    wrapped = log_request(show_response=log)(wrapped)
                if auth:
                    wrapped = require_token(wrapped)
                state["wrapped"] = wrapped
            return state["wrapped"](*args, **kwargs)

        return proxy
    return decorator


# ════════════════════════════════════════════════════════════
#  上下文口径：窗口上限的单一事实源
# ════════════════════════════════════════════════════════════

#: 上下文窗口读数 —— 单一实现在 plugins/plugin_api.py（2026-10-03 上收，原先本文件
#: 与 plugins/chat.py 各抄了一份；同一口径写两遍必然漂移）。
_context_limit_info = context_limit_info


def _push_runtime_window(value: int) -> bool:
    """把新窗口值推给**正在跑**的编排器（否则滑块只改了个没人读的副本）

    【为什么必须有这一步】``_memory_token_limit`` 是**启动时**由 config 常量决定的
    （``config.py`` 的取值链是 DEFAULT → 环境变量 → overrides，**从不读 config.yaml**，
    2026-10-03 复核：把 config.yaml 的 memory.token_limit 谎报成 65536，Config 仍返回 131072）；
    此前 ``POST /api/context/config`` 只改 ``_cfg`` 这个运行时副本 ⇒ 面板上的数字变了、
    真正"超限丢弃最旧消息"的边界一动不动（同一个旋钮，两套账）。
    这里同时推两处，让三者回到同一个数：
      1. ``_Yunshu._memory_token_limit``（组装窗口，get_context 用它）；
      2. ``_Yunshu._memory._token_limit``（压缩触发阈值 = 窗口 × compress_threshold）。

    Returns:
        True = 已推给运行中的编排器；False = 编排器不可用（值只落在配置副本里）。
    """
    try:
        from app_server import _Yunshu
    except Exception:  # noqa: BLE001 拿不到主对象时如实返回未生效
        return False
    if _Yunshu is None or not hasattr(_Yunshu, "_memory_token_limit"):
        return False
    try:
        _Yunshu._memory_token_limit = int(value)
        _Yunshu._memory_token_limit_source = "runtime_override(api)"
        memory = getattr(_Yunshu, "_memory", None)
        if memory is not None and hasattr(memory, "_token_limit"):
            memory._token_limit = int(value)
        return True
    except Exception:  # noqa: BLE001 推送失败不得让接口 500（值仍会写进配置副本）
        return False


# ════════════════════════════════════════════════════════════
#  上下文监视器 API
# ════════════════════════════════════════════════════════════

# ── 「当前窗口压缩次数」的会话基线 ──────────────────────────────────
#: 【为什么需要】memory_manager.compress_rounds 是**全局累计**的摘要版本号
#: （单一 MemoryManager 跨会话共享），后端注释也写明它"不是当前会话压缩了几次"。
#: 面板要显示"当前窗口压缩了几次"，就必须减去该会话开始时的基线。
#: 基线落 data/ 下的运行时文件（不入库），刷新页面不丢；同会话跨进程也保留。
_COMPRESS_BASE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "context_session_compress_base.json")
_COMPRESS_BASE_CACHE: dict | None = None


def _compress_base(session_id: str, current: int) -> int:
    """取（必要时登记）该会话的压缩基线；拿不到会话 ID 时返回当前值（窗口数恒 0）"""
    global _COMPRESS_BASE_CACHE
    key = str(session_id or "")
    if not key:
        return int(current)
    if _COMPRESS_BASE_CACHE is None:
        try:
            with open(_COMPRESS_BASE_PATH, "r", encoding="utf-8") as f:
                _COMPRESS_BASE_CACHE = {str(k): int(v)
                                        for k, v in (json.load(f) or {}).items()}
        except Exception:  # noqa: BLE001 文件缺失/损坏 ⇒ 从空表开始
            _COMPRESS_BASE_CACHE = {}
    if key not in _COMPRESS_BASE_CACHE:
        _COMPRESS_BASE_CACHE[key] = int(current)
        try:
            os.makedirs(os.path.dirname(_COMPRESS_BASE_PATH), exist_ok=True)
            with open(_COMPRESS_BASE_PATH, "w", encoding="utf-8") as f:
                json.dump(_COMPRESS_BASE_CACHE, f, ensure_ascii=False, indent=2)
        except Exception as e:  # noqa: BLE001 写基线失败不影响读数（只是窗口数退化）
            import logging
            logging.getLogger(__name__).debug("写压缩基线失败: %s", e)
    return _COMPRESS_BASE_CACHE[key]


@bp.route("/api/context/status")
@_view(log=False)
def api_context_status():
    """获取当前上下文使用状态（**已迁移到统一信封**）。

    【P1-front 第七批 · 2026-10-05】载荷**一字未动**（实测 19 个键），只是装进 ok() 的 data。
    【本端点有**两个**消费方，且它们用的是两套客户端】
      · components/workbench/panels/ContextManagerBar.tsx —— hubGet + pickObj（本批改 getEnvelope）；
      · lib/contextMonitorApi.ts —— 走 lib/apiClient.request()，它**不拆信封**（返回原始体）。
    两个都同批改，否则 contextMonitorApi 会在 {code,data,message} 上读 percentage 而**静默拿到 undefined**。
    """
    from app_server import _get_current_session_id, _session_mgr, _get_token_counter, _cfg, _Yunshu
    session_id = _get_current_session_id()
    messages = _session_mgr.get_messages(session_id, limit=0)  # 全部消息

    counter = _get_token_counter()
    send_tokens = 0
    recv_tokens = 0
    turns = 0          # 轮数 = 用户发言条数（一次发言 = 一轮）
    recent = []

    for msg in messages:
        content = msg.get("content", "") or ""
        tokens = counter.count(content)
        role = msg.get("role", "")
        if role == "user":
            turns += 1
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

    # ── 分母：**编排窗口**（单一事实源），不是 UI 存的另一个数 ──
    # 【为什么必须改】本面板原先除以 `memory.token_limit`，而真正决定"能记住多少历史"的是
    #   `orchestrator._memory_token_limit`（启动时取自 config 常量 / 环境变量，
    #   见 lifecycle_manager.py:283-299 与 config.py 的 DEFAULT→env→overrides）。
    #   【2026-10-03 更正】原注释写"启动时由 config.yaml:memory.token_limit 写入"并称
    #   存在"代码默认 / UI 存值 / config.yaml 值"三份数 —— 第三份**不存在**：
    #   config.yaml 从未参与运行时配置合成（实测见同文件 _push_runtime_window 的注释）。
    #   真实存在的两份是"代码默认（或 env）"与"UI 运行时覆盖"，取错任一份都会与
    #   /api/chat 的 context 块差出倍数（编排层 TASK-S10-03 已修过，本面板当时没跟上）。
    #   取不到就如实报 None，**不拿硬编码值冒充分母**（那比没有读数更坏：它像真的）。
    # 【2026-10-14 对齐 DSH】会话统计与上下文装配明细（由 plugins/chat.py 的
    # 流式主循环每轮实测后写入 agent/context_stats.py）
    session_stats = {}
    try:
        from agent.context_stats import get_session_stats as _get_session_stats
        session_stats = _get_session_stats(session_id) or {}
    except Exception as _ss_e:  # noqa: BLE001 读数不可用 ⇒ 如实返回空对象
        import logging as _logging
        _logging.getLogger(__name__).debug("会话读数读取失败: %s", _ss_e)
        session_stats = {}

    limit_info = _context_limit_info(_Yunshu)
    limit = limit_info["limit_tokens"]
    configured_limit = _cfg.get("memory", "token_limit", default=MEMORY_TOKEN_LIMIT_DEFAULT)
    pct = round(total / limit * 100, 1) if limit else None

    # 压缩次数（= 摘要版本号，**累计值**，不是"当前会话压缩了几次"）
    compress_rounds = 0
    try:
        compress_rounds = getattr(_Yunshu._memory, 'compress_rounds', 0)
        if callable(compress_rounds):
            compress_rounds = compress_rounds()
    except Exception:
        pass

    # 【2026-10-14 对齐 DSH】「当前窗口的压缩次数」= 本会话基线之上的增量。
    # 面板此前显示的是全局累计版本号（换会话不归零、与实际窗口脱节）。
    try:
        compress_rounds_window = max(0, int(compress_rounds)
                                     - int(_compress_base(session_id, compress_rounds)))
    except Exception:  # noqa: BLE001 基线不可用 ⇒ 如实退回累计值，不伪造 0
        compress_rounds_window = int(compress_rounds)

    # ── 档位：**按占用**判定；"摘要退化"作为独立成因单列 ──
    # 【为什么拆开】原先 `compress_rounds >= 5` 直接判 critical ⇒ 占用 22% 也常年报红，
    #   用户看到的是"没几下就用满了"。累计压缩次数与"当前窗口占用"是两件事，
    #   编排层已在 `_check_context_usage`（TASK-S10-03）明确区分 summary_degraded / usage_high，
    #   本面板与之对齐。顺带删掉 `pct_warn = pct >= 80 or pct >= 60` 那句死代码（写等于没写）。
    compress_degraded = compress_rounds >= 5
    compress_warn = compress_rounds >= 3
    if pct is None:
        status_level = "unknown"
    elif pct >= 95:
        status_level = "critical"
    elif pct >= 80:
        status_level = "warning"
    elif pct >= 60:
        status_level = "info"
    else:
        status_level = "ok"
    status_reasons = []
    if pct is not None and pct >= 95:
        status_reasons.append("usage_critical")
    elif pct is not None and pct >= 80:
        status_reasons.append("usage_high")
    if compress_degraded:
        status_reasons.append("summary_degraded")
    elif compress_warn:
        status_reasons.append("summary_warn")

    return _ok({
        "current_tokens": total,
        # 分母 = 编排窗口（真正生效的那个）+ 来源披露；百分比语义写明白
        "token_limit": limit,
        "token_limit_source": limit_info["limit_source"],
        "configured_token_limit": configured_limit,
        "configured_token_limit_note": RESTART_FALLBACK_NOTE,
        "percentage": pct,
        "percentage_semantics": "current_window_usage",
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=PER_MESSAGE_SEND_LIMIT_DEFAULT),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=PER_MESSAGE_RECV_LIMIT_DEFAULT),
        # 语义声明：发送侧**只告警不截断**（绝不静默丢弃用户粘进来的原文）
        "send_limit_semantics": "warn_only",
        "compress_threshold": _cfg.get("memory", "compress_threshold", default=0.8),
        "compress_rounds": compress_rounds,
        # 当前窗口（= 本会话基线之上）的压缩次数；面板展示用这个
        "compress_rounds_window": compress_rounds_window,
        "compress_degraded": compress_degraded,
        "status_level": status_level,
        "status_reasons": status_reasons,
        "send_tokens": send_tokens,
        "recv_tokens": recv_tokens,
        "messages_count": len(messages),
        # 轮数（用户发言条数）；与 LLM 调用频数（/api/llm-monitor/stats.total）分列：
        # 一轮对话可能触发多次 LLM 调用（工具循环/摘要），两者不应混同。
        "turns": turns,
        # 会话统计：模型用时/工具调用用时/首 token 平均（毫秒）+ 上下文装配明细
        "session_stats": session_stats,
        "context_breakdown": session_stats.get("breakdown") or {},
        "recent_messages": recent,
    })


@bp.route("/api/context/config", methods=["POST"])
@_view(auth=True)
def api_context_config():
    """更新上下文控制参数（**已迁移到统一信封**）。

    【P1-front 第九批 · 2026-10-07】业务载荷一字未动（仍是 ok/changed/runtime_applied/...），
    只是装进 ok() 的 data 并带 X-Envelope: v2。消费方两个：ContextManagerBar.tsx（hubPost）
    与 lib/contextMonitorApi.ts（apiClient，返回原始体 ⇒ 需 unwrapEnvelopeBody）。
    """
    # _Yunshu 用于读取"真实窗口"（回显 token_limit 时必须与状态接口同一口径）
    from app_server import _cfg, logger, _Yunshu
    data = request.get_json() or {}
    changed = []

    # 【2026-10-02 值域对齐真实模型能力】原上限一律 32768，而实测本机模型
    #   （deepseek-flash = DeepSeek-V4.1-Flash，GET /v1/models）context_window=1048576、
    #   max_output_tokens=393216 ⇒ 想调大也调不动。
    applied = False
    if "token_limit" in data:
        val = int(data["token_limit"])
        val = max(MEMORY_TOKEN_LIMIT_MIN, min(MEMORY_TOKEN_LIMIT_MAX, val))
        _cfg.set(val, "memory", "token_limit")
        changed.append("token_limit")
        # ★ 关键：把新值推给**正在跑**的编排器。只写 _cfg 的话，
        #   面板数字变了、真正"超限丢最旧消息"的边界却没动（同一个旋钮两套账）。
        applied = _push_runtime_window(val)

    if "per_message_send_limit" in data:
        val = int(data["per_message_send_limit"])
        val = max(0, min(PER_MESSAGE_SEND_LIMIT_MAX, val))
        _cfg.set(val, "memory", "per_message_send_limit")
        changed.append("per_message_send_limit")

    if "per_message_recv_limit" in data:
        val = int(data["per_message_recv_limit"])
        val = max(0, min(PER_MESSAGE_RECV_LIMIT_MAX, val))
        _cfg.set(val, "memory", "per_message_recv_limit")
        changed.append("per_message_recv_limit")

    if changed:
        logger.info(f"上下文配置已更新: {', '.join(changed)}")

    limit_info = _context_limit_info(_Yunshu)
    return _ok({
        "ok": True,
        "changed": changed,
        # 窗口值是否**当场生效**（False = 只落进配置副本，重启后才可能生效）
        "runtime_applied": applied,
        "apply_note": (
            "token_limit 已推给运行中的编排器（组装窗口 + 压缩阈值同步）；"
            "发送/回复上限即时生效，但同样是运行时覆盖，重启后回落**代码默认值**"
            "（环境变量可覆盖；config.yaml 不参与运行时配置合成）"),
        "token_limit": limit_info["limit_tokens"] or _cfg.get(
            "memory", "token_limit", default=MEMORY_TOKEN_LIMIT_DEFAULT),
        "token_limit_source": limit_info["limit_source"],
        "per_message_send_limit": _cfg.get("memory", "per_message_send_limit", default=PER_MESSAGE_SEND_LIMIT_DEFAULT),
        "per_message_recv_limit": _cfg.get("memory", "per_message_recv_limit", default=PER_MESSAGE_RECV_LIMIT_DEFAULT),
        "send_limit_semantics": "warn_only",
    })


@bp.route("/api/context/compress", methods=["POST"])
@_view(auth=True)
def api_context_compress():
    """手动触发上下文压缩（**已迁移到统一信封**）。

    【P1-front 第九批 · 2026-10-07】成功路径改走 ok()；**错误路径（500）按纪律本次不动**。
    """
    from app_server import _Yunshu, logger
    try:
        # 【2026-10-14 修 500】原实现调 compress()（无参）+ 按 dict 取 freed/current：
        #   MemoryManager.compress(old_messages) 缺参直接 TypeError，面板恒 500。
        #   现走 compress_now()（真正的压缩入口），并**如实**回报结果 ——
        #   "释放多少 token"在当前实现下不可核实（压缩不删消息），故不编数字。
        result = _Yunshu._memory.compress_now()
        return _ok({
            "ok": bool(result.get("ok")),
            "summary_version": result.get("summary_version"),
            "version_before": result.get("version_before"),
            "messages_compressed": result.get("messages_compressed"),
        })
    except Exception as e:
        logger.warning(f"手动压缩失败: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── 记忆操作 API ──

@bp.route("/api/memory/overview")
@_view(log=False)
def api_memory_overview():
    """获取记忆概览"""
    from app_server import _Yunshu
    try:
        summary = _Yunshu._memory.load_summary()
        storage = getattr(_Yunshu._memory, "_storage", None)
        recent = storage.load_recent_messages(limit=20) if storage else []
        try:
            # 「消息数」= 真实总条数（recent 只是最近 20 条预览）。
            # 原实现是 len(recent) ⇒ 恒 ≤20、与真实记忆量对不上，也让「资产管理·记忆数据」
            # 与「记忆管理」显示不一致（2026-10-13 同源核对时暴露）。
            total = len(storage.load_recent_messages(limit=1000000)) if storage else len(recent)
        except Exception:  # noqa: BLE001 统计失败退回 recent 长度，不把概览打成 500
            total = len(recent)
        logs = _Yunshu._memory._black_box.analyze()
        log_stats = logs if isinstance(logs, dict) else {}
        return _ok({
            "summary_version": summary[1] if summary else None,
            "summary_text": summary[0][:300] if summary and summary[0] else None,
            "recent_messages": [
                {"index": i, "role": m.get("role", "?"), "content": m.get("content", "")[:100]}
                for i, m in enumerate(recent)
            ] if recent else [],
            "message_count": total,
            "log_stats": log_stats,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@bp.route("/api/memory/manual", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_manual():
    """手动添加记忆"""
    from app_server import _Yunshu
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    priority = data.get("priority", "normal")
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400
    try:
        # 【2026-10-24 修复】MemoryManager 从来没有 add_memory 这个方法：本路由
        #   在生成环境稳定 500（'MemoryManager' object has no attribute
        #   'add_memory'），只是被 test_envelope_batch6 里同名 stub 掩盖了。
        #   真实写路径是 score_and_save_message（先评分再落盘，压缩时按分数保留）
        #   或 add_message（不评分）。保留最后一条分支仅为兼容测试/旧实现。
        memory = _Yunshu._memory
        text = f"[手动记忆·优先级:{priority}] {content}"
        # 【2026-10-25】把 msg_id 回给调用方：没有它就**没有任何办法**定位并删除
        # 这条记忆（交接单 §9 T3 的根因）。msg_id 就是落盘的 timestamp。
        msg_id = ""
        if hasattr(memory, "score_and_save_message"):
            msg_id = memory.score_and_save_message("user", text)
        elif hasattr(memory, "add_message"):
            msg_id = memory.add_message("user", text)
        else:
            memory.add_memory({"role": "user", "content": text})
        return _ok({"ok": True, "msg_id": str(msg_id or "")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/memory/compress", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_compress():
    """触发记忆压缩"""
    from app_server import _Yunshu
    try:
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_Yunshu._memory.compress())
        loop.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


#: msg_id 形态：落盘 timestamp 是 ISO-8601（UTC，含微秒）。
#: 【为什么只做形态校验】msg_id 会进**响应**与**审计**,不进任何文件路径；
#: 这里拒绝超长/控制字符即可（不做白名单式过度约束，免得将来换 id 形态就废）。
_MSG_ID_RE = re.compile(r"^[0-9A-Za-z:.+-]{16,64}$")


@bp.route("/api/memory/<int:index>", methods=["DELETE"])
@_view(auth=True, log=True)
def api_memory_delete_index(index):
    """【2026-10-25 修正】按序号删除**未实现**，此前恒返回 ok:true 却没删任何东西。

    【为什么不再"假装成功"】消息文件是追加写 JSONL，序号会随每次写入漂移：
    界面上"第 3 条"下一次请求可能已是另一条。按序号删 = 删错数据的概率极高。
    与其返回一个看起来成功的空操作，不如明确拒绝并指出正确入口
    （本项目对"假绿/假成功"的既有口径：会漏报的门禁比会误报的更危险）。
    """
    logger.info("[memory] 拒绝按序号删除 index=%s（该接口未实现）", index)
    return _problem(
        501,
        title="按序号删除未实现",
        detail=("消息文件是追加写 JSONL，序号会随每次写入漂移，按序号删会删错数据。"
                "请改用 DELETE /api/memory/messages/<msg_id>"
                "（msg_id 由 POST /api/memory/manual 的返回体给出）"),
        type_slug="delete-by-index-unsupported",
    )


@bp.route("/api/memory/messages/<msg_id>", methods=["DELETE"])
@_view(auth=True, log=True)
def api_memory_delete_message(msg_id):
    """按 id 删除一条长期记忆：**默认 dry-run**，真删必须 confirm=true。

    判据在后端（与外部记忆导入的 commit 同一口径），前端勾选只是二次确认的界面表达。
    """
    from app_server import _Yunshu
    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run", True))
    confirm = body.get("confirm") is True
    target = str(msg_id or "").strip()
    if not _MSG_ID_RE.match(target):
        return _problem(400, title="msg_id 形态非法",
                        detail="msg_id 形如 2026-10-25T00:12:33.123456+00:00"
                               "（由 POST /api/memory/manual 的返回体给出）",
                        type_slug="invalid-msg-id")
    memory = _Yunshu._memory
    try:
        found = memory.find_message(target)
    except Exception:  # noqa: BLE001 异常原文只进日志（py/stack-trace-exposure）
        logger.exception("[memory] 查询记忆失败")
        return _problem(500, title="查询记忆失败",
                        detail="详见服务日志（本次未删除任何内容）",
                        type_slug="memory-lookup-failed")
    if not found:
        return _problem(404, title="找不到该记忆",
                        detail="可能已被删除，或 msg_id 抄错（先 dry_run=true 查一次）",
                        type_slug="message-not-found")
    preview = str(found.get("content") or "")
    chars = len(preview)
    result = {
        "ok": True,
        "dry_run": dry_run,
        "msg_id": target,
        "found": True,
        "role": str(found.get("role") or ""),
        "chars": chars,
        # 只回前 80 字：够人确认"是不是这一条"，又不把整段记忆再吐一遍
        "preview": preview[:80] + ("…" if chars > 80 else ""),
        "note": ("只删除 messages.jsonl 里的这一行；滚动摘要里可能仍留有压缩过的痕迹，"
                 "如需彻底清除请再清空摘要（POST /api/memory/clear-summary）"),
    }
    if dry_run:
        return _ok(result)
    if not confirm:
        return _problem(400, title="需要显式确认",
                        detail="物理删除不可恢复：先 dry_run=true 看 preview，再传 confirm=true",
                        type_slug="confirmation-required")
    try:
        deleted = memory.delete_message(target)
    except Exception:  # noqa: BLE001 同上：错误文本不进响应
        logger.exception("[memory] 删除记忆失败")
        return _problem(500, title="删除记忆失败",
                        detail="详见服务日志（删除可能未生效，可重试）",
                        type_slug="memory-delete-failed")
    audit_ref = {}
    try:
        from agent.audit.facade import audit
        entry = audit("memory.delete_message", actor="agent.plugins.memory",
                      subject=f"msg:{target}",
                      payload={"chars": chars, "role": str(found.get("role") or ""),
                               "deleted": bool(deleted)},
                      source="agent")
        if entry is not None:
            audit_ref = {"seq": int(getattr(entry, "seq", 0) or 0)}
    except Exception:  # noqa: BLE001 审计失败不掩盖业务结果
        audit_ref = {}
    if not deleted:
        return _problem(404, title="删除时该记忆已不存在",
                        detail="可能已被并发删除（本次未写入任何内容）",
                        type_slug="message-not-found")
    result.update({"deleted": True, "audit": audit_ref})
    return _ok(result)


@bp.route("/api/memory/clear-summary", methods=["POST"])
@_view(auth=True, log=True)
def api_memory_clear_summary():
    """清空长期摘要"""
    from app_server import _Yunshu
    try:
        _Yunshu._memory.clear_summary()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/memory/summary", methods=["PUT"])
@_view(auth=True, log=True)
def api_memory_update_summary():
    """更新长期摘要内容"""
    from app_server import _Yunshu
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


@bp.route("/api/memory/review", methods=["GET", "POST"])
@_view(auth=True, log=True)
def api_memory_review():
    """记忆库审查接口（legacy：原 agent/server_routes/routes_memory.py）

    GET: 返回上次审查结果 + LTM 统计
    POST: 触发快速审查 (review_quick)
    """
    from app_server import _Yunshu
    try:
        reviewer = _Yunshu._memory_reviewer
        if reviewer is None:
            return jsonify({"ok": False, "error": "记忆审查器未启用"}), 503

        if request.method == "GET":
            last_review = reviewer.get_last_review()
            return jsonify({
                "last_review": vars(last_review) if last_review else None,
                "stats": _Yunshu._long_term_memory.get_stats() if _Yunshu._long_term_memory else {},
            })
        else:  # POST
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(reviewer.review_quick())
            finally:
                loop.close()
            return jsonify({"ok": True, "result": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  向量记忆/语义搜索 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/vector/search", methods=["POST"])
@_view(auth=True, log=True)
def api_vector_search():
    """语义搜索向量记忆"""
    from app_server import _Yunshu, logger
    data = request.get_json() or {}
    query = data.get("query", "").strip()
    top_k = min(int(data.get("top_k", 5)), 50)
    if not query:
        return jsonify({"ok": False, "error": "查询内容不能为空"}), 400

    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return _ok({"ok": True, "results": [], "count": 0, "available": False})

    try:
        results = vs.search(query, top_k)
        return _ok({
            "ok": True,
            "results": [item.to_dict() for item in results],
            "count": len(results),
        })
    except Exception as e:
        logger.error("向量搜索失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/vector/stats")
@_view(log=False)
def api_vector_stats():
    """获取向量记忆统计（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu
    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"available": False})
    stats = vs.get_stats()
    stats["available"] = True
    stats["total_memories"] = vs.count
    return jsonify(stats)


@bp.route("/api/vector/add", methods=["POST"])
@_view(auth=True, log=True)
def api_vector_add():
    """添加单条向量记忆（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu, logger
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400

    metadata = data.get("metadata", {})
    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"ok": False, "error": "向量系统未初始化"}), 503

    try:
        item_id = vs.add(content, metadata)
        return jsonify({"ok": True, "item_id": item_id})
    except Exception as e:
        logger.error("添加向量记忆失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/vector/batch_add", methods=["POST"])
@_view(auth=True, log=True)
def api_vector_batch_add():
    """批量添加向量记忆（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu, logger
    data = request.get_json() or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"ok": False, "error": "items 不能为空"}), 400

    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"ok": False, "error": "向量系统未初始化"}), 503

    try:
        item_ids = vs.batch_add(items)
        return jsonify({"ok": True, "item_ids": item_ids, "count": len(item_ids)})
    except Exception as e:
        logger.error("批量添加向量记忆失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/vector/item/<item_id>")
@_view(log=False)
def api_vector_get_item(item_id):
    """按 ID 获取记忆项（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu
    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"available": False}), 503

    item = vs.get_by_id(item_id)
    if not item:
        return jsonify({"error": "未找到该记忆项"}), 404
    return jsonify(item.to_dict())


@bp.route("/api/vector/recent")
@_view(log=False)
def api_vector_recent():
    """获取最近的向量记忆（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu, logger
    limit = min(int(request.args.get("limit", 20)), 100)
    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"available": False, "items": []}), 503

    try:
        items = vs.get_recent(limit=limit)
        return jsonify({
            "items": [item.to_dict() for item in items],
            "count": len(items),
        })
    except Exception as e:
        logger.error("获取最近向量记忆失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/vector/clear", methods=["DELETE"])
@_view(auth=True, log=True)
def api_vector_clear():
    """清空向量记忆（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu, logger
    vs = getattr(_Yunshu, '_vector_memory', None)
    if not vs:
        return jsonify({"ok": False, "error": "向量系统未初始化"}), 503

    try:
        vs.clear()
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("清空向量记忆失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  知识库写入 API（legacy）
#  【为什么不放别处】本仓无 plugins/knowledge.py；/api/knowledge/* 的活体是
#  agent/server_routes/routes_knowledge.py（CardStore 卡片/图谱），与本端点语义
#  不同（本端点写的是 Yunshu._knowledge_base 向量知识库），且该 server_routes
#  模块在本任务禁改清单内。故与原实现一致，仍留在记忆/向量域插件。
# ════════════════════════════════════════════════════════════

@bp.route("/api/knowledge/add", methods=["POST"])
@_view(auth=True, log=True)
def api_knowledge_add():
    """添加知识文档（legacy：原 agent/server_routes/routes_memory.py）"""
    from app_server import _Yunshu, logger
    data = request.get_json() or {}
    content = data.get("content", "").strip()
    source = data.get("source", "manual")
    tags = data.get("tags", [])

    if not content:
        return jsonify({"ok": False, "error": "内容不能为空"}), 400

    kb = getattr(_Yunshu, '_knowledge_base', None)
    if not kb:
        return jsonify({"ok": False, "error": "知识库未初始化"}), 503

    try:
        kb.add_document(content, source=source, tags=tags)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("添加知识文档失败: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


# ════════════════════════════════════════════════════════════
#  窗口监控 API
# ════════════════════════════════════════════════════════════

@bp.route("/api/memory/windows/events")
@_view(log=False)
def api_window_events():
    """获取窗口切换事件"""
    from app_server import _Yunshu
    limit = request.args.get("limit", 50, type=int)
    limit = min(limit, 500)
    try:
        events = _Yunshu._memory._black_box.query(
            event_type="window_event", limit=limit
        )
        return jsonify({"events": events})
    except Exception as e:
        return jsonify({"events": [], "error": str(e)})


@bp.route("/api/memory/windows/stats")
@_view(log=False)
def api_window_stats():
    """获取窗口使用统计"""
    from app_server import _Yunshu
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


@bp.route("/api/memory/windows/current")
@_view(log=False)
def api_window_current():
    """获取当前活跃窗口"""
    from app_server import _window_sensor
    if _window_sensor:
        return jsonify(_window_sensor.get_current())
    return jsonify({"process": None, "title": None, "elapsed_sec": 0, "is_idle": False})


@bp.route("/api/memory/windows/config", methods=["GET", "POST"])
@_view(auth=True, log=True)
def api_window_config():
    """获取或更新窗口监控配置"""
    from app_server import _window_sensor
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


@bp.route("/api/memory/windows/clear", methods=["POST"])
@_view(auth=True, log=True)
def api_window_clear():
    """清空窗口事件记录"""
    try:
        return jsonify({"ok": True, "message": "窗口事件将在滚动日志中自然过期"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


PLUGIN = register_plugin(Plugin(
    name="memory",
    version="1.0.0",
    description="记忆与上下文管理",
    blueprint=bp,
))
