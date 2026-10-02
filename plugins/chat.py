# -*- coding: utf-8 -*-
"""云枢对话域插件（T1.2）：对话 / 会话 / 历史记录 / 语音 / 新闻 / 清空。

从 app_server.py 迁移而来，路由路径与行为 100% 不变。
约定（PLAN-1 §4）：
  - Blueprint 不设 url_prefix，路由保持 /api/... 原样；
  - 插件模块顶层只 import flask / plugin_api / 标准库；
  - 共享依赖（require_token、log_request、_Yunshu、_session_mgr、_CHAT_HISTORY 等）
    保留在 app_server.py，视图函数内部延迟 import，规避循环导入。
"""
import datetime
import functools
import json
import logging
import os
import time

from flask import Blueprint, request, jsonify

from .plugin_api import Plugin, register_plugin

bp = Blueprint("chat", __name__)


# ════════════════════════════════════════════════════════════════════════════
#  共享装饰器（延迟包装）
# ----------------------------------------------------------------------------
# require_token / log_request 保留在 app_server.py；插件顶层不得 import app_server
# （循环导入红线，PLAN-1 §4），故在请求时取用真实装饰器再调用。
# 包装顺序与语义和迁移前一致：日志装饰器在内、令牌校验装饰器在外。
# ════════════════════════════════════════════════════════════════════════════

def _lazy_wrap(f, build):
    """占位包装器：每次调用时用 app_server 的真实装饰器包装 f 后执行。"""
    @functools.wraps(f)
    def _wrapped(*args, **kwargs):
        return build(f)(*args, **kwargs)
    return _wrapped


def _require_token(f):
    """延迟版 @require_token（app_server 共享装饰器）"""
    def _build(fn):
        from app_server import require_token as _real
        return _real(fn)
    return _lazy_wrap(f, _build)


def _log_request(*args, **kwargs):
    """延迟版 @log_request(...)（app_server 共享装饰器）"""
    def _decorator(f):
        def _build(fn):
            from app_server import log_request as _real
            return _real(*args, **kwargs)(fn)
        return _lazy_wrap(f, _build)
    return _decorator


def _json_safe_metadata(metadata):
    """仅保留可 JSON 序列化的元数据键（TASK-S11-03 R5）。

    Why: ``/api/chat`` 的响应由 ``jsonify`` 序列化，而 ``metadata`` 由编排层装配
    （``context_notice`` / ``plan_summary`` / ``used_planning`` …）。一旦混入
    不可序列化对象，``jsonify`` 会抛 ``TypeError`` ⇒ **整个对话接口 500**。
    读数通道不得拖垮主链路，故在边界上过滤。

    被丢弃的键**显式披露**在返回值里（由调用方放进 ``metadata_omitted_keys``），
    不静默吞掉 —— 否则"键不见了"会被误读成"编排层没产出这个键"。

    Returns:
        ``(safe: dict, dropped_keys: list[str])``
    """
    if not isinstance(metadata, dict):
        return {}, []
    safe = {}
    dropped = []
    for key, value in metadata.items():
        try:
            json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            dropped.append(str(key))
        else:
            safe[key] = value
    return safe, dropped


def _context_limit_info(yunshu):
    """读取编排窗口上限及其来源（TASK-S11-03 R3）。

    单一事实源是 ``DigitalLife.context_limit_info()``（由 LifecycleManager 在初始化时
    写入 ``_memory_token_limit`` / ``_memory_token_limit_source``，
    见 agent/orchestrator/lifecycle_manager.py:283-289）。

    【不易】取不到时返回 ``limit_tokens=None`` + ``limit_source="unavailable"``，
    **绝不**回退到 4096 之类的硬编码值——那样比没有读数更坏：它看起来像个可信的数。
    ``getattr`` 兜底是为了兼容 mock / 旧对象（缺方法时不炸请求）。
    """
    getter = getattr(yunshu, "context_limit_info", None)
    if callable(getter):
        try:
            info = getter()
        except Exception:
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


# ── 语音输入 API ──
@bp.route("/api/voice/listen", methods=["POST"])
@_require_token
@_log_request()
def api_voice_listen():
    """语音识别接口 - 从麦克风捕获语音并转换为文本"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _Yunshu, logger
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


@bp.route("/api/voice/status")
@_log_request(show_response=False)
def api_voice_status():
    """获取语音系统状态"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _Yunshu
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


@bp.route("/api/chat", methods=["POST"])
def api_chat():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    # _CHAT_HISTORY 是 app_server 的模块级共享缓存（api_config 等仍在原文件使用），
    # 经模块属性读写保证同一对象、重绑定语义与迁移前一致。
    import app_server as _app_server
    from app_server import (
        _Yunshu, _session_mgr, _get_current_session_id, _safety_guard,
        _save_conversation_record, _get_token_counter, _cfg, logger,
        PROMETHEUS_AVAILABLE, SECURITY_BLOCKS,
    )
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

    # 获取会话 ID（优先级：请求体 session_id > 查询参数 session > 全局默认）
    # [2026-08-15 并发修复] 压测/外部调用方在 JSON body 传 session_id，
    # 原实现只读 query 参数导致 12 并发请求全部收敛到同一默认会话
    # （会话级串行根因之一，见 会话级上下文检查串行阻塞技术备忘录_20260815.md）。
    # body 优先实现真正的请求级会话隔离；query 回退保持 Web 前端兼容。
    body_session_id = (data or {}).get("session_id") or ""
    session_id = body_session_id or request.args.get("session") or _get_current_session_id()
    # [2026-08-15 并发修复] 显式会话不存在时自动创建：
    # SessionManager.add_message 对不存在会话抛 SessionNotFoundError → 500，
    # 外部调用方传入任意 session_id 时无法工作。自动创建即可实现真正会话隔离。
    if body_session_id or request.args.get("session"):
        try:
            if not _session_mgr.get_session(session_id):
                _session_mgr.create_session(
                    session_id=session_id,
                    title=f"会话 {session_id[:24]}",
                )
                logger.info("已自动创建会话: %s", session_id)
        except ValueError as _e:
            # 非法会话 ID（含路径穿越字符）回退默认会话
            logger.warning("会话 ID 非法，回退默认会话: %s", _e)
            session_id = _get_current_session_id()
        except OSError as _e:
            # [2026-08-15 边界修复] Windows 路径超长等 mkdir 抛 OSError → 500，
            # 与非法 ID 同策略回退默认会话（不因外部参数崩掉请求）
            logger.warning("会话 ID 创建失败（OSError），回退默认会话: %s", _e)
            session_id = _get_current_session_id()
    logs.append(f"[SESSION] 会话 ID: {session_id}")

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
    # 【2026-10-02】供下方业务指标埋点区分成功/失败（失败路径在 except 里置 False）
    _chat_ok = True
    try:
        logs.append(f"[CHAT] 开始调用 DigitalLife.chat()")
        # 会话元数据显式传入（并发安全），避免全局 _session_id 被并发覆盖
        response = _Yunshu.chat(
            user_input,
            session_id=session_id,
            session_mgr=_session_mgr,
        )
        chat_time = (time.time() - chat_start) * 1000
        logs.append(f"[CHAT] 对话响应生成完成 - 耗时: {chat_time:.2f}ms")
        logs.append(f"[CHAT] 响应长度: {len(response)} 字符")
    except Exception as e:
        import traceback
        chat_time = (time.time() - chat_start) * 1000
        _chat_ok = False
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
    # TASK-S9-01: 按会话读取本轮状态（修复前读全局实例属性，本轮没写就返回上一轮值）
    _turn_state = _Yunshu.last_turn_state(session_id)
    _session_mgr.add_message(session_id, "user", user_input)
    _session_mgr.add_message(
        session_id, "assistant", response,
        tool_steps=_turn_state["tool_steps"],
        reasoning=_turn_state["reasoning"],
    )
    _app_server._CHAT_HISTORY.append(entry)

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

    # ── 本次读数（TASK-S11-03 R3：口径统一到「本请求会话 + 真实窗口上限」）──
    # 修复前（本文件 :294-330）三处口径缺陷：
    #   ① `_session_id_ctx = _get_current_session_id()` 取的是**全局当前会话**，
    #      而同一 handler 在 :149 是按**请求**解析 session_id 的
    #      ⇒ A 会话聊天可能报 B 会话的数字（读数串台）；
    #   ② `_token_limit = _cfg.get("memory", "token_limit", default=4096)`：
    #      config.yaml 的 memory 段**没有** token_limit 键
    #      （agent/orchestrator/lifecycle_manager.py:283-289），分母恒为硬编码 4096，
    #      与真正用于组装上下文的编排窗口（内置默认 131072）差 32 倍；
    #   ③ 于是 `percentage = 累计/4096` 几轮必然 >100%，
    #      与 metadata.context_notice.pct（÷131072）在同一响应里自相矛盾。
    # 现：会话取**本请求**解析出的 `session_id`；分母取编排窗口的单一事实源
    #     `_Yunshu.context_limit_info()` 并**披露来源**；分母不可得时记 None
    #     （不以 4096 之类硬编码冒充，见 _context_limit_info）。
    _ctx_counter = _get_token_counter()
    _input_tokens = _ctx_counter.count(user_input)
    _output_tokens = _ctx_counter.count(response)

    # ── 单次发送**告警**（2026-10-02）：只告警，**不截断** ──
    # 【为什么不是截断】`memory.per_message_send_limit` 的界面文案写着"超限截断"，
    #   但它全仓**没有任何强制点**。本次把它变成**有意义的告警阈值**（产品决定）：
    #   静默丢弃用户粘进来的原文，比"提示一句"危险得多 —— 用户会以为发出去的就是全文。
    try:
        _send_limit = int(_cfg.get("memory", "per_message_send_limit", default=8192) or 0)
    except (TypeError, ValueError):
        _send_limit = 0
    _send_exceeded = bool(_send_limit and _input_tokens > _send_limit)
    if _send_exceeded:
        logger.warning(
            "[context] 单条消息超过「单次发送」告警阈值: %d > %d tokens"
            "（仅告警，未截断原文；阈值见 POST /api/context/config）",
            _input_tokens, _send_limit)

    # 会话累计 token（快速估算，仅统计 content 字段）——**本请求会话**，非全局会话。
    # limit=0 = 不截断，取该会话全部消息（含刚写入的本轮）。
    _all_msgs = _session_mgr.get_messages(session_id, limit=0)
    _session_total = sum(
        _ctx_counter.count((m.get("content") or ""))
        for m in _all_msgs
    )
    _limit_info = _context_limit_info(_Yunshu)
    _token_limit = _limit_info["limit_tokens"]
    _token_limit_source = _limit_info["limit_source"]
    if _token_limit is None:
        # 读数缺位必须**可见**：宁可是一个显式的 None + 告警，也不要一个像样的假分母
        logger.warning(
            "[context] 编排窗口上限不可得（_Yunshu.context_limit_info 缺位），"
            "context.token_limit 记 None —— 不以硬编码分母冒充"
        )

    # R5：读回本轮结构化元数据（含 S10-03 的 metadata.context_notice）并转发进
    # HTTP 响应。修复前本文件根本不转发 metadata ⇒ 新口径的告警在 Web 端看不到。
    # 只转发**可 JSON 序列化**的键：元数据由编排层装配，一旦混入不可序列化对象，
    # jsonify 会抛 TypeError 让整个 /api/chat 500 —— 读数通道不得拖垮主链路。
    _response_metadata, _metadata_dropped = _json_safe_metadata(
        _Yunshu.last_response_metadata(session_id))

    # 【2026-10-02 新增】对话业务指标 —— 供 /api/business/prometheus 及依赖它的告警使用。
    #   【为什么必须在这里补】此前唯一会记业务指标的聊天路径是 `agent/server_routes/routes_chat.py`，
    #   而那个模块**从未接线**（见 test_server_routes_registration_inventory 的 KNOWN_UNREGISTERED）⇒
    #   线上聊天**零埋点**，业务指标端点恒为空（实测样本行 0）。本插件是会话域的**活路径**。
    #   【单位】指标名是 yunshu_interaction_duration_seconds ⇒ 传**秒**，故把 ms 除以 1000。
    #   【失败隔离】埋点异常绝不影响对话响应（与本文件既有纪律一致）。
    try:
        from agent.monitoring.business_metrics import record_interaction
        record_interaction(
            "chat",
            str((llm_state or {}).get("provider") or "unknown"),
            _chat_ok,
            chat_time / 1000.0,
        )
    except Exception:  # noqa: BLE001 埋点失败绝不阻断主路径
        pass

    return jsonify({
        "response": response,
        "mode": _Yunshu.get_behavior_mode().value,
        "mode_label": _Yunshu._behavior.profile.label,
        "health": [r.to_dict() for r in _Yunshu.check_health()],
        "llm_state": llm_state,
        "logs": logs,
        "tool_steps": _turn_state["tool_steps"],
        "reasoning": _turn_state["reasoning"],
        "timing": {
            "total": total_time,
            "safety_check": safety_time,
            "chat_processing": chat_time,
            "voice_synthesis": voice_time,
        },
        "voice_result": voice_result,
        # 结构化系统提示（如 context_notice）随响应外发，**不**混进 response 正文
        # （TASK-S10-03 硬要求；TASK-S11-03 R5 接通到 HTTP）
        "metadata": _response_metadata,
        **({"metadata_omitted_keys": _metadata_dropped} if _metadata_dropped else {}),
        "context": {
            # ── 归属：本次读数属于哪个会话（= 本请求解析出的 session_id，可自查）
            "session_id": session_id,
            "session_message_count": len(_all_msgs),
            # ── 分子：来源分别为「本请求输入/输出」与「该会话全部消息的 content 估算」
            "input_tokens": _input_tokens,
            "output_tokens": _output_tokens,
            "session_total_tokens": _session_total,
            "session_total_source": (
                "session_manager:messages.jsonl 全量消息（仅 content 字段估算）"
            ),
            # ── 分母：编排窗口上限（单一事实源）+ 来源披露
            "token_limit": _token_limit,
            "token_limit_source": _token_limit_source,
            # ── percentage 的分母语义（**口径变更**：修复前是硬编码 4096）
            # 这是「会话累计占编排窗口的比例」，**不是**当前窗口占用率。
            "percentage": (round(_session_total / _token_limit * 100, 1)
                           if _token_limit else None),
            "percentage_semantics": "session_cumulative_share_of_window",
            # ── 单次发送告警（阈值 = memory.per_message_send_limit；**只告警不截断**）
            "send_limit": {
                "limit": _send_limit or None,
                "input_tokens": _input_tokens,
                "exceeded": _send_exceeded,
                "semantics": "warn_only",
                "note": "超限仅告警，绝不截断原文",
            },
            "percentage_note": (
                "= 会话累计 token ÷ token_limit（session_total_tokens / token_limit），"
                "**非**当前窗口占用率；窗口占用率见 metadata.context_notice 的 "
                "used_tokens / pct（其分母同为 token_limit，来自对话记忆装配结果）"
            ),
        },
    })


@bp.route("/api/news", methods=["GET"])
def api_news():
    """新闻直通接口 — 搜索+翻译+格式化，绕过 LLM"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _Yunshu
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

@bp.route("/api/sessions", methods=["GET"])
def api_sessions_list():
    """获取会话列表"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    sessions = _session_mgr.list_sessions()
    current_id = _session_mgr.get_current_id()
    return jsonify({
        "sessions": sessions,
        "current_id": current_id,
    })


@bp.route("/api/sessions", methods=["POST"])
def api_sessions_create():
    """创建新会话"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, logger
    data = request.get_json() or {}
    title = data.get("title", "")
    session = _session_mgr.create_session(title=title)
    logger.info("通过 Web 界面创建新会话: %s", session["id"])
    return jsonify(session), 201


@bp.route("/api/sessions/<session_id>", methods=["DELETE"])
@_require_token
def api_sessions_delete(session_id):
    """删除会话"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _session_groups, logger
    import app_server as _app_server  # _CHAT_HISTORY 共享缓存（同一对象）
    if _session_mgr.delete_session(session_id):
        # 清理分组归属（groups.json membership，幂等）
        try:
            _session_groups.remove_member(session_id)
        except Exception as _e:
            logger.warning("清理会话分组归属失败（忽略）: %s", _e)
        # 如果删除的是当前会话，清空历史缓存
        if session_id == _session_mgr.get_current_id():
            _app_server._CHAT_HISTORY.clear()
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@bp.route("/api/sessions/<session_id>/rename", methods=["PUT"])
@_require_token
def api_sessions_rename(session_id):
    """重命名会话"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    data = request.get_json() or {}
    title = data.get("title", "")
    if not title:
        return jsonify({"error": "标题不能为空"}), 400
    if _session_mgr.rename_session(session_id, title):
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@bp.route("/api/sessions/current", methods=["POST"])
@_require_token
def api_sessions_set_current():
    """切换当前会话"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    import app_server as _app_server  # _CHAT_HISTORY 共享缓存（同一对象）
    data = request.get_json() or {}
    session_id = data.get("session_id", "")
    if not session_id:
        return jsonify({"error": "session_id 不能为空"}), 400
    if _session_mgr.set_current(session_id):
        # 切换会话时也更新 _CHAT_HISTORY 缓存
        messages = _session_mgr.get_messages(session_id, limit=50)
        _app_server._CHAT_HISTORY = []
        for i in range(0, len(messages), 2):
            user_msg = messages[i]
            assistant_msg = messages[i + 1] if i + 1 < len(messages) else {}
            if user_msg.get("role") == "user":
                _app_server._CHAT_HISTORY.append({
                    "user": user_msg.get("content", ""),
                    "Yunshu": assistant_msg.get("content", ""),
                    "mode": "normal",
                    "timestamp": user_msg.get("timestamp", ""),
                })
        return jsonify({"ok": True})
    return jsonify({"error": "会话不存在"}), 404


@bp.route("/api/sessions/<session_id>/messages", methods=["GET"])
def api_sessions_messages(session_id):
    """获取会话消息"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    limit = request.args.get("limit", 50, type=int)
    messages = _session_mgr.get_messages(session_id, limit=limit)
    return jsonify(messages)


@bp.route("/api/sessions/<session_id>/messages", methods=["DELETE"])
@_require_token
def api_sessions_messages_clear(session_id):
    """清空单个会话的消息（不删除会话本身；历史缓存由切换/刷新时重建）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    if not _session_mgr.get_session(session_id):
        return jsonify({"error": "会话不存在"}), 404
    _session_mgr.clear_messages(session_id)
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════
#  会话交接文档（救回孤儿路由，2026-10-01）
# ════════════════════════════════════════════════════════════

class _HandoffStateAdapter:
    """把 app_server 的两个模块级全局适配成 generate_handoff 期望的 state 形状。

    【为什么需要这个适配器】generate_handoff(state, ...) 的签名要求
     state.session_mgr 与 state.Yunshu（见 agent/handoff/handoff_generator.py:44-68），
    而它诞生时是从 routes_sessions.register_routes(app, state) 里调用的 —— 那里
    恰好握有 ServerState。迁到插件后没有 ServerState 可用，但 app_server 的两个
    模块级全局（_session_mgr / _Yunshu）正是同一批对象。
    【为什么不在插件顶层 import】循环导入红线（PLAN-1 §4）：本模块在 app_server
    导入中途被加载；故属性在这里**延迟**取（property），请求期 app_server 已完全加载。
    """

    @property
    def session_mgr(self):
        from app_server import _session_mgr
        return _session_mgr

    @property
    def Yunshu(self):
        from app_server import _Yunshu
        return _Yunshu


@bp.route("/api/handoff", methods=["POST"])
@_require_token
def api_handoff():
    """生成会话交接文档 —— LLM 把当前会话压缩成 Markdown，落盘到 OS 临时目录。

    【为什么这条曾经是 404】它原定义在 agent/server_routes/routes_sessions.py:247，
    而该模块**从未在 app_server 接线**（见 tests/unit/test_server_routes_registration_inventory.py
    的 KNOWN_UNREGISTERED：「会话 API 由 plugins/chat.py 提供」）。
    【为什么要救而不是删】generate_handoff 是**完整且有单测**的能力
    （tests/unit/test_handoff_generator.py，含三处脱敏 + llm.chat→llm.summarize→规则提取
    的降级链），而这条路由是它**唯一**的 HTTP 入口 —— 不接线就等于整条能力不可达。
    本插件是会话域的正式归属（/api/sessions/* 全在此），故迁入此处。
    【为什么不能直接接线 routes_sessions】该模块的 9 条会话路径与 plugins/chat.py
    重复，整体接线会造成同名路径重复注册（语义歧义），已被 test_陈旧模块集合未被接线 拦住。

    请求体（均可选）：
        session_id: 目标会话 ID，缺省取当前会话
        intent:     下一 session 用途描述，用于 skill 推荐

    【安全】保留原路由的 @require_token；会话内容会送 LLM，故必须认证。
    """
    from agent.handoff.handoff_generator import generate_handoff
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    try:
        result = generate_handoff(
            _HandoffStateAdapter(),
            session_id=data.get("session_id"),
            intent=data.get("intent"),
        )
        return jsonify(result)
    except ValueError as e:
        # 会话不存在 / 无消息 —— 调用方输入问题，非服务故障
        return jsonify({"error": str(e)}), 400
    except Exception as e:  # noqa: BLE001 交接生成失败不得把端点打成未处理异常
        return jsonify({"error": f"handoff 生成失败: {e}"}), 500


# ════════════════════════════════════════════════════════════
#  会话工作空间（任务工作目录）API
#  默认：data/sessions/{id}/workspace；可绑定自定义本地目录（仿 DSH 添加工作区）
# ════════════════════════════════════════════════════════════

@bp.route("/api/sessions/<session_id>/workspace")
def api_sessions_workspace(session_id):
    """列出会话工作空间文件树（会话不存在 404）

    默认工作空间目录缺失时惰性补齐；绑定目录缺失时如实返回 exists=False
    （前端提示修复/恢复默认）。
    """
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    if not _session_mgr.get_session(session_id):
        return jsonify({"error": "会话不存在"}), 404
    meta = _session_mgr.get_session_metadata(session_id) or {}
    if not meta.get("workspace_root"):
        # 默认工作空间惰性补齐：历史会话升级前无 workspace 目录，首次查看即创建
        try:
            _session_mgr.ensure_workspace_path(session_id)
        except Exception as _e:
            return jsonify({"error": f"工作空间初始化失败: {_e}"}), 500
    result = _session_mgr.list_session_workspace(session_id)
    result["session_id"] = session_id
    return jsonify(result)


@bp.route("/api/sessions/<session_id>/workspace-root", methods=["PUT"])
@_require_token
def api_sessions_workspace_bind(session_id):
    """绑定/切换会话工作空间到本地绝对路径（仿 DSH 添加工作区）

    body: {"path": "C:\\...", "create": bool}
    path 为空 = 恢复默认工作空间。绑定成功自动登记到「已添加的工作区」。
    """
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _workspace_registry, logger as _log
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    create = bool(data.get("create"))
    ok, payload = _session_mgr.bind_workspace_root(session_id, path, create=create)
    if not ok:
        return jsonify({"error": payload}), 400
    root = payload["root"]
    if "changed" not in payload:
        # 绑定了自定义目录 → 登记记忆，方便其它会话快速选择
        try:
            _workspace_registry.add_workspace(root)
        except Exception as _e:
            _log.warning("登记工作区记忆失败（忽略）: %s", _e)
    return jsonify({"ok": True, **payload})


@bp.route("/api/sessions/<session_id>/workspace-root", methods=["DELETE"])
@_require_token
def api_sessions_workspace_unbind(session_id):
    """恢复会话默认工作空间（移除自定义绑定）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr
    ok, payload = _session_mgr.clear_workspace_root(session_id)
    if not ok:
        return jsonify({"error": payload}), 404
    return jsonify({"ok": True, **payload})


@bp.route("/api/sessions/<session_id>/workspace/reveal", methods=["POST"])
@_require_token
def api_sessions_workspace_reveal(session_id):
    """在系统文件管理器中打开该会话的工作空间目录（本机桌面应用场景）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, logger as _log
    if not _session_mgr.get_session(session_id):
        return jsonify({"error": "会话不存在"}), 404
    meta = _session_mgr.get_session_metadata(session_id) or {}
    if meta.get("workspace_root"):
        # 绑定目录缺失时如实报错，不静默重建用户目录
        root = _session_mgr.workspace_path(session_id)
        if root is None:
            return jsonify({"error": "绑定的工作区目录不存在，请先修正路径或恢复默认"}), 400
    else:
        try:
            root = _session_mgr.ensure_workspace_path(session_id)
        except Exception as _e:
            return jsonify({"error": f"工作空间初始化失败: {_e}"}), 500
    import subprocess
    import sys as _sys
    try:
        if _sys.platform.startswith("win"):
            os.startfile(str(root))  # type: ignore[attr-defined]  # Windows only
        elif _sys.platform == "darwin":
            subprocess.Popen(["open", str(root)])
        else:
            subprocess.Popen(["xdg-open", str(root)])
        _log.info("[workspace] 已在文件管理器打开会话工作空间: %s", root)
        return jsonify({"ok": True, "root": str(root)})
    except Exception as _e:
        return jsonify({"ok": False, "error": str(_e), "root": str(root)}), 500


# ════════════════════════════════════════════════════════════
#  「已添加的工作区」记忆 API（跨会话复用；workspaces.json）
# ════════════════════════════════════════════════════════════

@bp.route("/api/workspaces")
def api_workspaces_list():
    """列出已添加的工作区（路径/名称/添加时间）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _workspace_registry
    return jsonify({"workspaces": _workspace_registry.list_workspaces()})


@bp.route("/api/workspaces", methods=["POST"])
@_require_token
def api_workspaces_add():
    """登记一个工作区目录：body {path, create?}；路径须为本地绝对路径"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _workspace_registry
    import os as _os
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip().strip('"').strip("'")
    if not path:
        return jsonify({"error": "路径不能为空"}), 400
    if not _os.path.isabs(path):
        return jsonify({"error": "请输入本地绝对路径（例如 C:\\Users\\you\\myproject）"}), 400
    resolved = _os.path.normpath(path)
    if not _os.path.exists(resolved):
        if not data.get("create"):
            return jsonify({"error": f"目录不存在：{resolved}（勾选“自动创建”可新建）"}), 400
        try:
            _os.makedirs(resolved, exist_ok=True)
        except OSError as _e:
            return jsonify({"error": f"创建目录失败：{_e}"}), 400
    if not _os.path.isdir(resolved):
        return jsonify({"error": f"路径不是文件夹：{resolved}"}), 400
    entry = _workspace_registry.add_workspace(resolved)
    if entry is None:
        return jsonify({"error": "登记失败"}), 400
    return jsonify(entry), 201


@bp.route("/api/workspaces", methods=["DELETE"])
@_require_token
def api_workspaces_remove():
    """从记忆列表移除一个工作区（不影响目录本身）：body {path}"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _workspace_registry
    data = request.get_json(silent=True) or {}
    path = data.get("path") or ""
    removed = _workspace_registry.remove_workspace(path)
    return jsonify({"ok": removed})


# ════════════════════════════════════════════════════════════
#  会话分组 API（按项目/用途归类会话；存储 groups.json）
# ════════════════════════════════════════════════════════════

@bp.route("/api/session-groups")
def api_session_groups_list():
    """列出全部分组及 会话→分组 归属表"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_groups
    return jsonify(_session_groups.list_groups())


@bp.route("/api/session-groups", methods=["POST"])
@_require_token
def api_session_groups_create():
    """新建分组（name 缺省为「新分组」）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_groups, logger
    data = request.get_json(silent=True) or {}
    group = _session_groups.create_group(name=data.get("name", ""))
    logger.info("创建会话分组: %s — %s", group["id"], group["name"])
    return jsonify(group), 201


@bp.route("/api/session-groups/<group_id>", methods=["PUT"])
@_require_token
def api_session_groups_rename(group_id):
    """重命名分组"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_groups
    data = request.get_json(silent=True) or {}
    title = data.get("name", "")
    if not title:
        return jsonify({"error": "分组名不能为空"}), 400
    if _session_groups.rename_group(group_id, title):
        return jsonify({"ok": True})
    return jsonify({"error": "分组不存在"}), 404


@bp.route("/api/session-groups/<group_id>", methods=["DELETE"])
@_require_token
def api_session_groups_delete(group_id):
    """删除分组（组内会话自动变为未分组，不删除会话）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_groups
    if _session_groups.delete_group(group_id):
        return jsonify({"ok": True})
    return jsonify({"error": "分组不存在"}), 404


@bp.route("/api/sessions/<session_id>/group", methods=["PUT"])
@_require_token
def api_sessions_set_group(session_id):
    """把会话移入/移出分组：body {group_id: string|null}；null = 未分组"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _session_groups
    if not _session_mgr.get_session(session_id):
        return jsonify({"error": "会话不存在"}), 404
    data = request.get_json(silent=True) or {}
    group_id = data.get("group_id") or None
    if group_id is not None and not _session_groups.get_group(group_id):
        return jsonify({"error": "分组不存在"}), 404
    _session_groups.assign(session_id, group_id)
    return jsonify({"ok": True})


@bp.route("/api/history")
@_log_request(show_response=False)
def api_history():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _get_current_session_id
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


@bp.route("/api/clear", methods=["POST"])
@_require_token
@_log_request()
def api_clear():
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _get_current_session_id
    import app_server as _app_server  # _CHAT_HISTORY 共享缓存（同一对象）
    session_id = request.args.get("session") or _get_current_session_id()
    _session_mgr.clear_messages(session_id)
    _app_server._CHAT_HISTORY.clear()
    return jsonify({"ok": True})


# ── 历史记录 API ──
@bp.route("/api/history/search")
@_log_request(show_response=False)
def api_history_search():
    """搜索历史记录"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _get_current_session_id
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


@bp.route("/api/history/<int:index>", methods=["DELETE"])
@_require_token
@_log_request()
def api_history_delete(index):
    """删除指定索引的历史记录（同时删除用户消息和助手回复）"""
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import _session_mgr, _get_current_session_id
    import app_server as _app_server  # _CHAT_HISTORY 共享缓存（同一对象）
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
    if session_id == _session_mgr.get_current_id():
        new_messages = _session_mgr.get_messages(session_id, limit=50)
        _app_server._CHAT_HISTORY = []
        for i in range(0, len(new_messages), 2):
            user_msg = new_messages[i]
            assistant_msg = new_messages[i + 1] if i + 1 < len(new_messages) else {}
            if user_msg.get("role") == "user":
                _app_server._CHAT_HISTORY.append({
                    "user": user_msg.get("content", ""),
                    "Yunshu": assistant_msg.get("content", ""),
                    "mode": "normal",
                    "timestamp": user_msg.get("timestamp", ""),
                })
    return jsonify({"ok": True})


# ════════════════════════════════════════════════════════════════════════════
# [workbench] 云枢工作台 SSE 流式接口示例
# ----------------------------------------------------------------------------
# 路径: POST /api/chat/stream
# 事件契约（与前端 yunshu-ui/src/workbench/lib/sse.ts 保持一致）:
#   data: {"type":"thinking","id":"intent","title":"...","detail":"...","status":"running"}
#   data: {"type":"chunk","text":"...","seq":N}     # seq=分片序号，供前端乱序检测
#   data: {"type":"done"}
# 说明: 演示 SSE 协议本身（Content-Type / 事件节奏 / 客户端断开）。
#       接入真实 LLM 时仅需把 _workbench_demo_stream 替换为真实生成器，
#       事件结构保持不变（契约即【不易】约束）。
# ════════════════════════════════════════════════════════════════════════════

#: 步骤明细长度上限 / 步骤条数上限：推理文本可能很长，落盘需有界
_STEP_DETAIL_LIMIT = 4000
_STEP_MAX = 40


def merge_thinking_step(steps, evt):
    """把一条 SSE thinking 事件合并进「步骤列表」（与前端 mergeStepDetail 同规则）

    规则（前端 `useLayoutStore.mergeStepDetail` 的服务端镜像，两端口径必须一致）：
      - 同一 id 的事件合并为一条：阶段事件是 `running`（带 detail）→ `done`（不带 detail）；
      - **不带 detail 的后续事件不清空已累积内容** —— 这是"思考/工具先出现、
        回复完成后凭空消失"的根因，落盘口径同样不能踩；
      - 两次 `running` 视为流式增量，detail 拼接；
      - `running → done`（如工具调用 参数 → 结果）用新 detail 覆盖。

    Args:
        steps: 就地修改的步骤列表（[{id,title,detail,status,at}]）
        evt: SSE 事件字典（type=thinking）
    """
    sid = str(evt.get("id") or "")
    if not sid:
        return
    detail = evt.get("detail")
    detail = detail if isinstance(detail, str) else ""
    status = str(evt.get("status") or "")
    title = str(evt.get("title") or sid)

    existing = next((s for s in steps if s.get("id") == sid), None)
    if existing is None:
        steps.append({
            "id": sid,
            "title": title,
            "detail": detail[:_STEP_DETAIL_LIMIT],
            "status": status or "done",
            "at": time.time(),
        })
        return

    if detail:
        if str(existing.get("status") or "") == "running" and status == "running":
            existing["detail"] = (
                (existing.get("detail") or "") + detail
            )[:_STEP_DETAIL_LIMIT]
        else:
            existing["detail"] = detail[:_STEP_DETAIL_LIMIT]
    existing["status"] = status or existing.get("status") or "done"
    existing["title"] = title
    existing["at"] = time.time()


def finalized_steps(steps):
    """落盘前收尾：丢弃空步骤、只保留最近 _STEP_MAX 条（超长会话不无限增长）"""
    kept = [s for s in (steps or []) if s.get("id")]
    return kept[-_STEP_MAX:]


def key_usable(k) -> bool:
    """LLM key 是否"看起来可用"（工作台据此决定真实流式 vs 演示模式）

    【单一来源】实现位于 `agent/llm_key.py`（下层模块，可被 agent 侧诊断端点与
    plugins 侧对话流共用）。此处保留同名转发，避免历史调用点/测试漂移。
    """
    from agent.llm_key import key_usable as _key_usable

    return _key_usable(k)


#: 本会话历史的**软上限**（条数）。真正的裁剪按 token 预算做，这个数字只为防内存失控。
_HISTORY_SOFT_CAP = 400


def _app_server_or_none():
    """取**已加载**的 app_server 模块；没加载就返回 None

    【不易·为什么用 sys.modules 而不是 `import app_server`】后者会执行整套 app_server 装配
    （注册全部内建工具、拉起单例等）。生产里它**早已加载**，读个数是免费的；而在测试进程里
    那是一次真实副作用（本仓已为"import app_server 污染进程级工具注册表"写过专门夹具）。
    这里只是"顺便读个配置/窗口"，不该为此触发重量级导入。
    """
    import sys
    return sys.modules.get("app_server")


def _get_counter_or_none():
    """取 token 计数器；取不到返回 None（**不拿字符数换算冒充 token**）"""
    try:
        mod = _app_server_or_none()
        return mod._get_token_counter() if mod is not None else None
    except Exception:  # noqa: BLE001 计数不可用 ⇒ 调用方保持原行为
        return None


def _select_within_budget(messages, budget, counter, *, min_messages: int = 2):
    """按 **token 预算**从后往前保留消息（替代写死的"最近 8 条"）

    【为什么这样选】对话的"当前话题"永远在尾部：从最新往前累计，直到预算用尽。
    至少保留 ``min_messages`` 条 —— 否则连"接上上一句话"都做不到，预算再紧也不该退化到 1 条。

    Args:
        messages: 完整消息列表（末条是本轮用户输入）。
        budget: 历史预算（<= 0 或计数不可用 ⇒ **原样返回**，保持调用方原行为）。
        counter: token 计数器（需有 ``count(text) -> int``）。
        min_messages: 无论如何保留的条数。

    Returns:
        ``(kept, dropped_count, used_tokens_or_None)``。计数不可用时 used 为 None（"未测"）。
    """
    if not messages or budget <= 0 or counter is None:
        return list(messages), 0, None
    kept: list = []
    used = 0
    for msg in reversed(messages):
        try:
            cost = int(counter.count(str(msg.get("content") or "")))
        except Exception:  # noqa: BLE001 单条计数失败按 0 处理，不影响整体
            cost = 0
        if kept and len(kept) >= min_messages and used + cost > budget:
            break
        kept.append(msg)
        used += cost
    kept.reverse()
    return kept, len(messages) - len(kept), used


def _cfg_get(section, key, default=None):
    """读 app_server 的运行时配置（取不到就返回 default，绝不抛）"""
    try:
        mod = _app_server_or_none()
        if mod is None:
            return default
        return mod._cfg.get(section, key, default=default)
    except Exception:  # noqa: BLE001 配置读取失败不得打断对话
        return default


def _resolve_context_window() -> int:
    """真实编排窗口（``_memory_token_limit``）；不可得返回 0（调用方保持原行为）

    【不易】不可得时返回 0 而不是 4096/131072 之类的"看起来合理"的值 ——
    调用方据此**不做预算裁剪**（保持原行为），而不是拿着假窗口去砍用户的历史。
    """
    try:
        mod = _app_server_or_none()
        yunshu = getattr(mod, "_Yunshu", None) if mod is not None else None
        getter = getattr(yunshu, "context_limit_info", None)
        if callable(getter):
            info = getter() or {}
            limit = info.get("limit_tokens")
            if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                return int(limit)
        raw = getattr(yunshu, "_memory_token_limit", 0)
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            return int(raw)
    except Exception:  # noqa: BLE001
        pass
    return 0


def _resolve_stream_max_tokens(model: str) -> int:
    """工作台流式路径的单次回复上限：与编排层**同一份口径**

    【为什么优先问编排器】``Orchestrator._resolve_max_output_tokens`` 是这条口径的既有实现，
    直接复用就不会出现"两个入口两个上限"（2026-10-02 实测工作台写死 2048 就是这么来的）。
    编排器不可用（桩/未初始化）时按同一规则自己算一遍 —— 规则本体在 agent/chat_limits.py。
    """
    from agent.chat_limits import resolve_max_output_tokens

    configured = _cfg_get("memory", "per_message_recv_limit", 0)
    try:
        mod = _app_server_or_none()
        resolver = getattr(getattr(mod, "_Yunshu", None), "_resolve_max_output_tokens", None)
        if callable(resolver):
            return int(resolver(model))
    except Exception:  # noqa: BLE001 拿不到编排器 ⇒ 用共享规则兜底
        pass
    return resolve_max_output_tokens(configured, model)


#: 支持的对话模式（与前端 useChatPrefsStore.CHAT_MODES 同名契约）
#:   plain     轻量：只用本会话历史（默认；1 次模型调用、真流式）
#:   retrieval 检索：轻量之上注入检索到的记忆/知识片段（仍是真流式）
#:   full      完整：委托编排器全链路（意图分层 + 检索 + 规划 + 工具）——**非流式**、可能多轮调用
CHAT_STREAM_MODES = ("plain", "retrieval", "full")


def _sse_event(evt: dict) -> str:
    """把一个事件编码成 SSE 帧（与 _workbench_real_stream 内部 _sse 同格式）"""
    return "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"


def _retrieve_context_extra(question: str) -> str:
    """「检索模式」要注入的上下文文本 —— 复用**编排器的同一份实现**；失败返回空串

    【为什么必须复用】编排器的 `_context_assembler_extra()` 已经装配了
    工作记忆 / 长期检索 / 程序性（技能+工作流）/ 经验库四层，并带注入防御、预算截断与
    指标埋点。在工作台另写一份检索 = 两套命中结果、两套预算 —— 正是本仓反复栽的坑。
    """
    try:
        mod = _app_server_or_none()
        yunshu = getattr(mod, "_Yunshu", None) if mod is not None else None
        getter = getattr(yunshu, "_context_assembler_extra", None)
        if not callable(getter):
            return ""
        return str(getter(question, mode="workbench") or "")
    except Exception as e:  # noqa: BLE001 检索失败 ⇒ 按轻量继续，绝不打断对话
        logging.getLogger("plugins.chat.stream").warning(
            "[workbench][SSE] 检索模式取上下文失败（按轻量继续）: %s", e)
        return ""


def _full_pipeline_events(question: str, session_id: str):
    """「完整模式」：委托编排器全链路，把结果按 chunk 事件外发（**非流式**）

    【为什么委托而不是在流式里复刻】编排器链路是 async 且步骤多（意图分层 → 检索 →
    规划 → 工具 → 回答），复刻就是第二套实现；直接委托只有一套。
    代价是**非流式**（回答整段到达、再分段外发）——这一点已写在 UI 的选项说明里，不隐瞒。
    【记忆不重复写】编排器 `process()` 自己会写 `_memory`；本函数不写，
    由 `_persist_turn_side_effects(..., write_memory=False)` 保证工作台侧不重复写。
    """
    log = logging.getLogger("plugins.chat.stream")
    yield _sse_event({"type": "thinking", "id": "full-pipeline", "title": "完整链路",
                      "detail": "走编排器：意图分层 → 检索 → 规划 → 工具 → 回答",
                      "status": "running"})
    mod = _app_server_or_none()
    yunshu = getattr(mod, "_Yunshu", None) if mod is not None else None
    if yunshu is None or not callable(getattr(yunshu, "chat", None)):
        log.warning("[workbench][SSE] 完整模式不可用：编排器未就绪")
        yield _sse_event({"type": "thinking", "id": "full-pipeline", "title": "完整链路",
                          "detail": "编排器未就绪，未执行", "status": "error"})
        yield _sse_event({"type": "chunk", "seq": 1,
                          "text": "（完整模式不可用：编排器未就绪，请改用「轻量」或「检索」）"})
        yield _sse_event({"type": "done"})
        return

    session_mgr = getattr(mod, "_session_mgr", None)
    try:
        answer = str(yunshu.chat(question, session_id=session_id or None,
                                 session_mgr=session_mgr) or "")
    except Exception as e:  # noqa: BLE001 编排器异常 ⇒ 明确告知，不静默返回空
        log.error("[workbench][SSE] 完整模式执行失败: %s", e)
        yield _sse_event({"type": "thinking", "id": "full-pipeline", "title": "完整链路",
                          "detail": f"执行失败：{e}", "status": "error"})
        yield _sse_event({"type": "chunk", "seq": 1,
                          "text": f"（完整模式执行失败：{e}）"})
        yield _sse_event({"type": "done"})
        return

    # 把编排层留存的**真实**上下文告警转成阶段事件（此前只在 /api/chat 的响应里可见）
    try:
        md = yunshu.last_response_metadata(session_id) or {}
        notice = md.get("context_notice") if isinstance(md, dict) else None
        if isinstance(notice, dict) and notice.get("level"):
            yield _sse_event({"type": "thinking", "id": "context-notice",
                              "title": "上下文提示",
                              "detail": str(notice.get("message") or notice.get("level")),
                              "status": "done"})
    except Exception as e:  # noqa: BLE001 元数据读不到不影响回答
        log.debug("[workbench][SSE] 读编排层元数据失败: %s", e)

    yield _sse_event({"type": "thinking", "id": "full-pipeline", "title": "完整链路",
                      "detail": "编排器已完成（结果整段返回，再分段外发）", "status": "done"})
    seq = 0
    for i in range(0, len(answer), 12):
        seq += 1
        yield _sse_event({"type": "chunk", "text": answer[i:i + 12], "seq": seq})
    yield _sse_event({"type": "done"})


def _persist_turn_side_effects(session_id: str, question: str, answer: str,
                                 steps: list, *, write_memory: bool = True) -> None:
    """把工作台这一轮写进**全局记忆**与**本轮状态**（与编排器路径同口径；fail-soft）

    【为什么独立成函数】它不该影响对话本身：任何一步失败都只记日志。
      · 记忆写入 = 与 /api/chat 对齐（编排器在 orchestrator.py:1661-1662 写 user/assistant）；
      · turn_state 写入 = 与 /api/chat 的 _set_turn_state 对齐（tool_steps / reasoning）。
    """
    try:
        log = logging.getLogger("plugins.chat.stream")
        mod = _app_server_or_none()
        yunshu = getattr(mod, "_Yunshu", None) if mod is not None else None
        if yunshu is None:
            return
        memory = getattr(yunshu, "_memory", None)
        # 【完整模式必须跳过】那条路是编排器自己写的记忆（orchestrator.process 末尾），
        # 这里再写一次就是同一条对话在记忆里出现两遍。
        if write_memory and memory is not None and callable(getattr(memory, "add_message", None)):
            memory.add_message("user", question)
            memory.add_message("assistant", answer)
            log.info("[workbench][SSE] 已写入全局记忆（会话 %s）", session_id)
        setter = getattr(yunshu, "_set_turn_state", None)
        if callable(setter):
            tool_steps = [s for s in (steps or [])
                          if str(s.get("id") or "").startswith("tool-real-")]
            reasoning = next((str(s.get("detail") or "") for s in (steps or [])
                              if s.get("id") == "reasoning"), "")
            # 显式传 None 也照写（该接口规定不得用 value-or-old 回退，见其 docstring）
            setter(session_id, tool_steps=tool_steps, reasoning=reasoning or None)
    except Exception as e:  # noqa: BLE001 记忆/状态写入失败不得影响已生成的回复
        log.warning("[workbench][SSE] 记忆/本轮状态写入失败（不影响回复）: %s", e)


def _workbench_real_stream(question, session_id="", mode="plain"):
    """真实 LLM 流式 SSE 生成器：thinking 事件 + 真实模型 chunk + done

    事件契约（与前端 yunshu-ui/src/lib/sse.ts 保持一致）：
      data: {"type":"thinking","id":"...","title":"...","detail":"...","status":"running"}
      data: {"type":"chunk","text":"...","seq":N}
      data: {"type":"done"}
    配置来源：.env（LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL）。

    Args:
        mode: 对话模式（见 :data:`CHAT_STREAM_MODES`）。
            ``plain``（默认，与引入该开关之前逐字一致）/ ``retrieval``（+检索注入）/
            ``full``（委托编排器全链路，非流式）。未知值一律按 ``plain`` 处理并记日志。
    """
    import logging
    logger = logging.getLogger("plugins.chat.stream")

    def _sse(evt):
        return "data: " + json.dumps(evt, ensure_ascii=False) + "\n\n"

    # ── 从配置构建 LLMService ──
    provider = os.environ.get("LLM_PROVIDER", "deepseek").strip().lower()
    api_key = os.environ.get("LLM_API_KEY", "") or os.environ.get("DEEPSEEK_API_KEY", "")
    model = os.environ.get("LLM_MODEL", "deepseek-v4-flash")
    base_url = os.environ.get("LLM_BASE_URL", "") or os.environ.get("DEEPSEEK_BASE_URL", "")

    from memory.llm_service import LLMService

    # ── 构造消息历史（若提供会话 ID，附带**本会话全部**历史） ──
    # 【2026-10-02 改：不再写死"最近 8 条"】原实现 `hist[-8:]` 与「上下文最大 Token」
    #   这个旋钮**完全无关** ⇒ 把窗口从 32768 调到 131072 对工作台毫无影响；反过来，
    #   8 条里的长工具输出又会把窗口吃光而无人过问。现在取本会话全部历史（软上限
    #   `_HISTORY_SOFT_CAP` 条只为防内存失控），真正的裁剪交给下面按 **token 预算**
    #   做的 `_select_within_budget()` —— 预算必须在"工具集/系统提示已知之后"才算得准。
    messages = [{"role": "user", "content": question}]
    if session_id:
        try:
            from app_server import _session_mgr
            # limit=0 与 /api/chat 同口径：不截断，取该会话全部消息
            hist = _session_mgr.get_messages(session_id, limit=0) or []
            if hist:
                recent = hist[-_HISTORY_SOFT_CAP:]
                messages = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                            for m in recent if m.get("content")]
                messages.append({"role": "user", "content": question})
        except Exception as _e:
            logger.debug("[workbench][SSE] 会话历史加载失败（忽略）: %s", _e)

    # ── 会话持久化（2026-09-07 修复：工作台 SSE 此前不落盘，刷新即丢）──
    # 用户消息在历史快照之后落盘（避免自身混入上下文）；首条消息自动命名会话。
    # 助手回复由路由层 gen() 在流结束时统一落盘（见 api_chat_stream）。
    if session_id:
        try:
            from app_server import _session_mgr as _sm
            _meta = _sm.get_session(session_id)
            if _meta and not _meta.get("message_count") and question:
                try:
                    _sm.rename_session(session_id, question[:28] or "新会话")
                except Exception:
                    pass
            _sm.add_message(session_id, "user", question)
        except Exception as _e:
            logger.debug("[workbench][SSE] 用户消息落盘失败（忽略）: %s", _e)

    # ── 完整模式：委托编排器全链路（意图分层 / 检索 / 规划 / 工具）──
    # 【2026-10-02】由用户在工具栏「对话模式」里选择。委托而非复刻：编排器那条路是 async
    #   且步骤多，复刻就是第二套实现。代价是**非流式**（已在 UI 选项说明里写明）。
    #   【为什么放在这里】用户消息刚落盘、还没构造本路径的 LLMService、更没向模型发请求 ⇒
    #   完整模式既不白建客户端，也不会走工作台自己的工具选择与预算（那些都是另一条链路的活）。
    if mode == "full":
        yield from _full_pipeline_events(question, session_id)
        return

    llm = LLMService(
        provider=provider, api_key=api_key, model=model,
        timeout=60, base_url=base_url,
    )

    # ── 前置 thinking 事件 ──
    # 【2026-10-02：删掉四条**拟态**阶段】原实现依次外发"意图识别 / 知识检索 / 规划分解 /
    #   工具调用"四条**总是显示完成**的事件，而本路径**并没有**跑这些引擎（源码自称"轻量拟态"）。
    #   后果是用户看到一个"知识检索 ✓"却什么都没检索 —— 与"假分母""死旋钮"是同一类病：
    #   界面声称的与实际执行的对不上。真正的阶段（上下文装配 / 工具调用：<name> / 思考过程 /
    #   生成回复）本来就是**真实**事件，不需要用假的来"凑满"时间线；
    #   意图/检索/规划由编排器路径（/api/chat）负责，这里不假装跑过。
    yield _sse({"type": "thinking", "id": "prepare", "title": "对话准备",
                "detail": "装配本会话上下文与工具集（意图/检索/规划由编排器路径负责，此处不跑）",
                "status": "done"})

    # ── 真实流式生成（key 无效/缺失时自动降级为演示流） ──
    seq = 0
    emitted = False

    # 校验 key 是否可用（sk-test / sk-old / 空 → 判定为测试/无效 key）
    _key_usable = key_usable

    if not _key_usable(api_key):
        logger.warning("[workbench][SSE] LLM_API_KEY 为测试/无效 key（%s...），降级为演示流", api_key[:8] if api_key else "空")
        yield _sse({"type": "thinking", "id": "degrade", "title": "演示模式",
                    "detail": "未配置有效 LLM_API_KEY，当前为演示输出。在 .env 配置真实 key 后自动切换真实 LLM。",
                    "status": "running"})
        # 演示回复块
        blocks = [
            "## 已收到你的问题\n\n> **" + question + "**\n",
            "当前运行在**演示模式**（.env 未配置有效 `LLM_API_KEY`）。\n",
            "### 接入真实 LLM\n\n在 `.env` 中设置：\n",
            "```bash\nLLM_PROVIDER=deepseek\nLLM_API_KEY=sk-你的真实key\nLLM_MODEL=deepseek-chat\nLLM_BASE_URL=https://api.deepseek.com/v1\n```\n",
            "重启服务后，本接口将自动切换为真实模型流式输出（事件结构不变）。\n",
        ]
        for block in blocks:
            for i in range(0, len(block), 8):
                seq += 1
                yield _sse({"type": "chunk", "text": block[i:i + 8], "seq": seq})
        yield _sse({"type": "thinking", "id": "degrade", "title": "演示模式", "status": "done"})
        yield _sse({"type": "done"})
        return

    # ── 真实流式生成 + 工具执行循环（Agent Loop） ──
    # 循环：模型请求工具 → 后端执行 → 结果回传 → 模型继续，直到模型不再请求工具。
    # 上限 4 轮工具循环（防死循环）；工具定义从 agent.tools 注册表生成。
    yield _sse({"type": "thinking", "id": "generate", "title": "生成回复",
                "detail": "模型流式输出中…", "status": "running"})

    SYSTEM_PROMPT = "你是云枢（Yunshu），一个拥有完整感知-认知-行动闭环的数字生命体。请以简洁、自然的语言回答用户。需要时可以使用提供的工具获取实时信息或执行操作。"

    # ── 检索模式：注入检索到的记忆/知识片段（2026-10-02，用户在工具栏「对话模式」里选）──
    # 口径只有一份：直接调编排器的 `_context_assembler_extra()`（见其 docstring）。
    _retrieval_text = ""
    _retrieval_tokens = 0
    if mode == "retrieval":
        _retrieval_text = _retrieve_context_extra(question)
        if _retrieval_text:
            SYSTEM_PROMPT = SYSTEM_PROMPT + "\n\n" + _retrieval_text
            try:
                _cnt = _get_counter_or_none()
                _retrieval_tokens = int(_cnt.count(_retrieval_text)) if _cnt else 0
            except Exception:  # noqa: BLE001 计不上就记 0（预算略保守）
                _retrieval_tokens = 0
        logger.info("[workbench][SSE] 检索模式：注入 %d 字符 / %d token（命中=%s）",
                    len(_retrieval_text), _retrieval_tokens, bool(_retrieval_text))
        yield _sse_event({
            "type": "thinking", "id": "retrieval", "title": "记忆/知识检索",
            "detail": ("命中并注入 %d token 的检索片段（工作记忆 / 长期检索 / 技能与工作流 / 经验库）"
                       % _retrieval_tokens) if _retrieval_text else
                      "本次未命中任何片段（或该能力未启用）——按轻量继续",
            "status": "done"})
    # 🔴 DSML 根因防线（见 `agent/tools_prompt_guard.py` 模块 docstring 的实测对照）：
    #   本函数上面那句 SYSTEM_PROMPT 里的「可以使用提供的工具」是**无条件的**工具宣传，
    #   而下方的工具加载 `except` 分支（工具定义加载失败）会让 `tool_defs` 保持 None。
    #   两者同时成立 ⇒ 提示词宣传了工具、请求却不带 tools ⇒ 上游退回 DSML 文本协议。
    #   实测：清空提示词里的工具宣传段后，同一 prompt 上游改为纯文本推辞，不再吐标记。
    #   ⇒ 提示词必须在**知道本轮是否有工具之后**再定稿；下方工具加载完会调用
    #     `align_system_prompt_with_tools` 做这一次定稿。
    # ── 工具选择（P5 统一入口，2026-09-17）─────────────────────────────
    # 【原先的问题】这里直接 `get_tool_defs()`（**无白名单**）⇒ 把注册表里**全部**工具
    #   schema 发给模型（与用户说什么无关），并且**完全绕过**编排器路径的路由、
    #   Schema 裁剪与工具闸门/审批 —— 同一次对话"换个入口就换一套工具集与一套治理"。
    #   这也是评估报告 §4.2 的头号口径分裂。
    #
    # 【口径更正 · B1】本段注释原先写"实测 91 个 ≈ 13k token/轮"，两个数都不成立，
    #   已按实测改写（复算脚本见 docs/audit_skill_governance/Q2_tool_skill_pool.md §7.1）：
    #     · "91"= `data/tool_definitions/*.yaml` 的**文件数**，不是下发数。运行实例上
    #       主线 `engineering` 白名单实际下发 **26** 个（本文件下方日志逐轮打印）。
    #     · "13k" 只对「字符÷3」粗口径成立（13,416）；对真实 BPE 是低估：
    #       全量 91 个未裁剪 = 43,997 字符 / **18,311** token(cl100k)，
    #       按 .env 的 SCHEMA_DESC_MAX_LEN=100 / SCHEMA_PROP_DESC_MAX_LEN=80 裁剪后
    #       = 40,250 字符 / **16,261** token（低估 20%；未裁剪低估 29%）。
    #   ⇒ 工具集的 token 数一律以 `count_tool_defs_tokens()`（tiktoken cl100k_base）
    #     实测值打印，不再用字符数换算。
    #
    # 【现在的做法】复用与编排器**同一条**选择链：
    #   ① 有激活主线 → 主线装配器（身份层做 effect/mute/平面减法）；
    #   ② 否则 → hybrid/关键词智能选择（若开启）；
    #   ③ 都不可用 → 退回全量（保持向后兼容，不让本改动把工作台弄坏）。
    #   再统一做 Schema 裁剪，最后交给 get_tool_defs(whitelist=...)。
    #
    # 【不易】整段失败必须**退回全量**而不是"零工具"：工作台是主 UI 路径，
    #   选择链出故障时"多给工具"远比"用户发不出任何工具调用"可接受。
    tool_defs = None
    _sel_note = "全量(回退)"
    try:
        from agent.tools import get_tool_defs as _get_defs
        _whitelist = None
        try:
            from agent.lines import line_whitelist as _line_wl
            _line_tools, _line_res = _line_wl(None)
            if _line_tools:
                _whitelist = _line_tools
                _sel_note = f"主线:{_line_res.line_id}({len(_line_tools)})"
        except Exception as _le:  # noqa: BLE001 主线不可用不影响后续选择
            logger.debug("[workbench][SSE] 主线装配不可用: %s", _le)

        if _whitelist is None:
            try:
                from agent.orchestrator.orchestrator import Orchestrator  # noqa: F401
            except Exception:
                pass
            try:
                from agent.tool_router_hybrid import hybrid_select_tools
                from agent.tool_router import get_tools_for_input
                _smart = hybrid_select_tools(question) or get_tools_for_input(question)
                if _smart:
                    _whitelist = _smart
                    _sel_note = f"智能选择({len(_smart)})"
            except Exception as _se:  # noqa: BLE001
                logger.debug("[workbench][SSE] 智能工具选择不可用: %s", _se)

        tool_defs = _get_defs(whitelist=_whitelist)
        try:
            from agent.tool_schema_pruner import prune_tool_defs
            tool_defs = prune_tool_defs(tool_defs) or tool_defs
        except Exception:  # noqa: BLE001 裁剪失败用未裁剪版
            pass
        _chars = sum(len(str(d)) for d in (tool_defs or []))
        # token 数**实测**（tiktoken cl100k_base，与 `LLMMonitor.estimate_tokens`、
        # 与审计报告同一口径）：不再用「字符÷3」估算 —— 那个口径会把主线 26 个工具
        # 的真实 6,757 token 说成 ~5.2k。测不了（缺 tiktoken）就明说"未测"，
        # 绝不退回另一种口径冒充同一个数字。
        _tokens = None
        try:
            from agent.tools_prompt_guard import count_tool_defs_tokens as _count_tok
            _tokens = _count_tok(tool_defs)
        except Exception as _te:  # noqa: BLE001 计量失败不影响工具集本身
            logger.debug("[workbench][SSE] 工具集 token 实测不可用: %s", _te)
        logger.info("[workbench][SSE] 工具集: %s -> %d 个, %d 字符, %s",
                    _sel_note, len(tool_defs or []), _chars,
                    ("%d token(cl100k)" % _tokens) if _tokens is not None
                    else "token 未实测（缺 tiktoken）")
    except Exception as _e:
        logger.debug("[workbench][SSE] 工具定义加载失败（无工具可用）: %s", _e)

    # ── DSML 根因防线：提示词宣传 与 tools 下发 对齐（唯一收口）──
    # 走到这里 `tool_defs` 才最终确定（可能是 None：加载失败 / 主线返回空白名单）。
    # `tools_prompt_guard` 会把"宣传了工具但本轮不发 tools"就地中和，并记
    # `event=tools_prompt_mismatch`；两侧口径从此由同一个函数保证。
    try:
        from agent.tools_prompt_guard import align_system_prompt_with_tools as _align_sp
        SYSTEM_PROMPT, _ = _align_sp(
            SYSTEM_PROMPT, bool(tool_defs),
            site="plugins.chat.workbench_sse",
            tools_count=len(tool_defs or []),
        )
    except Exception as _ge:  # noqa: BLE001 守卫异常不得弄坏工作台
        logger.debug("[workbench][SSE] 工具一致性守卫异常（按原样继续）: %s", _ge)

    # ══════════════════════════════════════════════════════════════════════
    #  上下文预算与单次回复上限（2026-10-02：让工作台真正吃到面板上的旋钮）
    # ══════════════════════════════════════════════════════════════════════
    # 【此前的问题】本路径把 `max_tokens=2048` **写死**在请求里、历史写死"最近 8 条"：
    #   面板上把「单次回复」调到 16384、「上下文最大 Token」调到 131072，对**主 UI 的
    #   对话链路**毫无作用（那条路才是用户天天在用的）。实测：回复被卡在模型的 1/192。
    # 【现在】两个数字都从同一份口径推出来（agent/chat_limits.py）：
    #   max_tokens = 配置优先 + 模型档位下限 + 硬上限收敛；
    #   历史预算   = 窗口 − 系统提示 − 工具 schema − max_tokens − 余量。
    # 【为什么在这里算】工具 schema 是窗口里最大的一块（实测主线 26 个 ≈ 6.7k token），
    #   必须等它确定后再算预算，否则预算是假的。
    from agent.chat_limits import resolve_context_budget as _resolve_ctx_budget

    _stream_max_tokens = _resolve_stream_max_tokens(model)
    _window_tokens = _resolve_context_window()
    _system_tokens = 0
    _counter_for_budget = None
    try:
        _counter_for_budget = _get_counter_or_none()
        if _counter_for_budget is not None:
            _system_tokens = int(_counter_for_budget.count(SYSTEM_PROMPT))
    except Exception:  # noqa: BLE001 系统提示计量失败按 0 计（预算略保守）
        _system_tokens = 0
    # 固定开销 = 工具 schema + 系统提示 + **检索注入**（检索模式下它同样占窗口）
    _overhead = int(_tokens or 0) + _system_tokens + int(_retrieval_tokens or 0)
    _budget = _resolve_ctx_budget(
        _window_tokens, overhead_tokens=_overhead, max_output_tokens=_stream_max_tokens)
    loop_messages, _dropped, _used = _select_within_budget(
        messages, _budget, _get_counter_or_none())
    logger.info(
        "[workbench][SSE] 上下文预算: 窗口=%s 系统提示=%d 工具=%s 回复上限=%d ⇒ 历史预算=%d；"
        "保留 %d/%d 条（丢弃 %d 条，%s token）",
        _window_tokens or "不可得", _system_tokens,
        ("%d token" % _tokens) if _tokens is not None else "未测",
        _stream_max_tokens, _budget, len(loop_messages), len(messages), _dropped,
        ("%d" % _used) if _used is not None else "未测")
    # 把"这次到底带了多少上下文"变成**可见的**一步（此前完全不可观测，
    # 用户只能靠猜"为什么它忘了我刚说的话"）
    yield _sse({
        "type": "thinking", "id": "context-budget", "title": "上下文装配",
        "detail": ("窗口 %s · 系统提示 %d tok · 工具 %s · 回复上限 %d ⇒ 历史预算 %d；"
                   "带入 %d 条（丢弃 %d 条%s）") % (
            _window_tokens or "不可得", _system_tokens,
            ("%d tok" % _tokens) if _tokens is not None else "未测",
            _stream_max_tokens, _budget, len(loop_messages), _dropped,
            ("，%d tok" % _used) if _used is not None else ""),
        "status": "done"})

    # ── 单次发送**告警**（只告警不截断，与 /api/chat 同一产品决定）──
    try:
        _send_limit = int(_cfg_get("memory", "per_message_send_limit", 8192) or 0)
    except Exception:  # noqa: BLE001 配置读不到 ⇒ 不告警（不是错误）
        _send_limit = 0
    _input_tokens_est = None
    if _send_limit and _counter_for_budget is not None:
        try:
            _input_tokens_est = int(_counter_for_budget.count(question))
        except Exception:  # noqa: BLE001
            _input_tokens_est = None
    if _input_tokens_est is not None and _input_tokens_est > _send_limit:
        logger.warning(
            "[workbench][SSE] 单条消息超过「单次发送」告警阈值: %d > %d tokens"
            "（仅告警，未截断原文）", _input_tokens_est, _send_limit)
        yield _sse({
            "type": "thinking", "id": "send-limit", "title": "消息超过告警阈值",
            "detail": ("本条 %d tok > 阈值 %d tok —— **未截断**，原文已完整发出；"
                       "阈值可在「上下文」面板调整") % (_input_tokens_est, _send_limit),
            "status": "done"})

    max_tool_rounds = 4
    emitted = False
    seq = 0
    # ── 异常 B 证据链（TASK-01 §3 修复 B）：本轮流式的可诊断状态 ──
    # 修复前这些信息**一个都没留**，只有一句"（模型未返回内容）"文案。
    _stream_started = time.time()
    _last_finish_reason = ""
    _saw_tool_calls = False
    _saw_any_payload = ""

    # on_reasoning 是 chat_stream 的**新增可选回调**（思考过程外发）。老实现 / 测试替身
    # 可能没有该形参，直接传会 TypeError 打断对话主链路 → 先探测签名，不支持就退化为
    # 「只发工具/阶段事件」（与本次改动前行为一致）。
    def _supports_on_reasoning(fn) -> bool:
        try:
            import inspect
            params = inspect.signature(fn).parameters
            if "on_reasoning" in params:
                return True
            return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
        except (TypeError, ValueError):
            return False

    _reasoning_supported = _supports_on_reasoning(getattr(llm, "chat_stream", None))

    try:
        for round_idx in range(max_tool_rounds + 1):
            # 本轮收集到的工具调用（name, args_json, reasoning_content）
            collected_tools = []

            def _on_tool_call(tool_name, args_json, reasoning_content=""):
                logger.info("[workbench][SSE] 模型工具调用: %s %s", tool_name, args_json[:120])
                collected_tools.append((tool_name, args_json, reasoning_content))

            # 思考过程（DeepSeek reasoning_content）实时外发：累积增量按分片
            # 推成 thinking 事件，前端「思考过程」开关打开时即可逐段看到推理。
            reasoning_parts: list = []
            reasoning_sent = 0

            def _on_reasoning(piece):
                if piece:
                    reasoning_parts.append(piece)

            # 流式请求 LLM（每轮都带工具定义，工具结果已回传到 loop_messages）
            #
            # 🔴 DSML 根因防线：原实现是 `tools=tool_defs if round_idx == 0 else None`
            #   —— 第 1 轮起就不再下发 tools，但提示词（SYSTEM_PROMPT）从第 1 轮起
            #   完全没变，仍然向模型宣告"有工具"。实测该不一致会让上游退回 DSML
            #   文本协议（标记被当正文返回）。而且这在语义上也是缺陷：本循环声明
            #   "上限 4 轮工具循环（防死循环）"，只首轮带 tools 会让第 2~4 轮
            #   **永远不可能**发起工具调用，那个循环等于废掉。
            #   现在每轮都带：既恢复多轮工具能力，也让"提示词宣传与 tools 下发"
            #   在每个轮次都一致。轮数上限仍由 max_tool_rounds 兜住，不会死循环。
            stream_kwargs = dict(
                messages=loop_messages,
                system_prompt=SYSTEM_PROMPT,
                # 【2026-10-02】原先写死 2048：面板上的「单次回复」对主 UI 无效，
                # 回复被卡在模型的 1/192。现与编排层同一份口径（配置驱动）。
                max_tokens=_stream_max_tokens,
                temperature=0.7,
                on_tool_call=_on_tool_call,
                tools=tool_defs,
            )
            if _reasoning_supported:
                stream_kwargs["on_reasoning"] = _on_reasoning
            for text_piece in llm.chat_stream(**stream_kwargs):
                # 先把新增的推理增量作为 thinking 事件推给前端（思考过程可见）
                if len(reasoning_parts) > reasoning_sent:
                    delta_reasoning = "".join(reasoning_parts[reasoning_sent:])
                    reasoning_sent = len(reasoning_parts)
                    yield _sse({"type": "thinking", "id": "reasoning",
                                "title": "思考过程", "detail": delta_reasoning,
                                "status": "running"})
                if not text_piece:
                    continue
                # ── 兜底消毒闸（防御性第二道防线，不是主要修法）──
                # 主要修法在解析层（agent/dsml_adapter，把标记变成结构化 tool_calls）；
                # 这里只保证"万一还有残留"，用户可见出口不留标记。
                # 注意局限：本闸按 chunk 判定，**跨 chunk 被切开的标记**由
                # memory/llm_service.py::chat_stream 里的 DSMLStreamGuard 负责。
                if len(_saw_any_payload) < 200:
                    _saw_any_payload += text_piece[: 200 - len(_saw_any_payload)]
                try:
                    from agent.dsml_adapter import sanitize_visible_text as _sanitize
                    text_piece, _n_leak = _sanitize(text_piece)
                    if _n_leak:
                        logger.warning(
                            "event=dsml_leak_blocked source=workbench_sse stripped=%d", _n_leak)
                except Exception as _san_e:  # noqa: BLE001 消毒闸异常不得打断流
                    logger.debug("[workbench][SSE] 兜底消毒闸异常: %s", _san_e)
                if not text_piece:
                    continue
                seq += 1
                emitted = True
                yield _sse({"type": "chunk", "text": text_piece, "seq": seq})

            # 收尾：本轮推理已全部外发 → 标记思考过程完成
            if reasoning_sent:
                yield _sse({"type": "thinking", "id": "reasoning",
                            "title": "思考过程", "detail": "", "status": "done"})
                reasoning_parts, reasoning_sent = [], 0

            # 无工具调用 → 生成完成，退出循环
            if not collected_tools:
                break
            _saw_tool_calls = True

            # ── 执行工具并回传结果 ──
            from agent.tools import call as _tool_call
            for tname, targs_json, reasoning in collected_tools:
                seq += 1
                emitted = True
                yield _sse({"type": "thinking", "id": f"tool-real-{tname}", "title": f"工具调用：{tname}",
                            "detail": f"参数: {targs_json[:120]}", "status": "running"})
                # 解析参数并执行
                tool_result = ""
                try:
                    import json as _json
                    targs = _json.loads(targs_json) if targs_json else {}
                    if not isinstance(targs, dict):
                        targs = {"args": targs}
                    tool_result = _tool_call(tname, **targs)
                    # 统一序列化结果（dict → json 字符串）
                    if isinstance(tool_result, (dict, list)):
                        tool_result = _json.dumps(tool_result, ensure_ascii=False, default=str)
                    else:
                        tool_result = str(tool_result)
                except Exception as _te:
                    logger.error("[workbench][SSE] 工具执行失败 %s: %s", tname, _te)
                    tool_result = f"工具执行失败: {_te}"
                # 回传结果给模型（assistant 消息含 tool_calls + reasoning_content
                # [DeepSeek thinking 模式要求]；tool 消息含执行结果）
                assistant_msg = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": f"call_{tname}",
                        "type": "function",
                        "function": {"name": tname, "arguments": targs_json},
                    }],
                }
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                loop_messages.append(assistant_msg)
                loop_messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{tname}",
                    "content": tool_result[:4000],
                })
                # 展示工具完成状态（含结果摘要）
                yield _sse({"type": "thinking", "id": f"tool-real-{tname}", "title": f"工具调用：{tname}",
                            "detail": f"结果: {tool_result[:100]}", "status": "done"})
    except Exception as _e:
        logger.error("[workbench][SSE] LLM 流式生成异常: %s", _e)
        err_text = f"\n\n> ⚠️ LLM 调用失败：{_e}\n> 请检查 .env 中 LLM_API_KEY / LLM_MODEL 配置。"
        seq += 1
        emitted = True
        yield _sse({"type": "chunk", "text": err_text, "seq": seq})

    yield _sse({"type": "thinking", "id": "generate", "title": "生成回复", "status": "done"})

    if not emitted:
        # 空输出兜底（异常 B）。
        # 修复前这里只有文案、**没有任何结构化证据**，线上无法判断是上游空
        # choices、finish_reason=tool_calls 却没带 tool_calls，还是超时降级。
        # 现在：统一有效性判定口径 + event=llm_empty_response 结构化日志 + 明确降级文案。
        try:
            from agent.llm_response_guard import (
                is_valid_response, log_empty_response, degraded_text)
            if not is_valid_response(None, None, None):
                log_empty_response(
                    source="plugins/chat.py::_chat_stream_generator",
                    provider=str(getattr(llm, "provider", "") or ""),
                    model=str(getattr(llm, "model", "") or ""),
                    finish_reason=str(
                        getattr(llm, "_last_stream_finish_reason", "")
                        or _last_finish_reason or ""),
                    has_tool_calls=bool(_saw_tool_calls),
                    elapsed_ms=(time.time() - _stream_started) * 1000.0,
                    request_id=str(locals().get("session_id", "") or ""),
                    raw_prefix=str(_saw_any_payload),
                )
            _empty_text = degraded_text(finish_reason=str(_last_finish_reason or ""))
        except Exception as _guard_e:  # noqa: BLE001 守卫异常不得吞掉兜底文案
            logger.warning("[workbench][SSE] 空返回判定失败（使用默认文案）: %s", _guard_e)
            _empty_text = "（模型未返回内容）"
        seq += 1
        yield _sse({"type": "chunk", "text": _empty_text, "seq": seq})

    yield _sse({"type": "done"})


@bp.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    from flask import Response, stream_with_context
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import logger

    data = request.get_json(silent=True) or {}
    question = (data.get("message") or data.get("question") or "").strip()
    raw_session_id = data.get("session_id", "") or data.get("sessionId", "") or ""
    if not question:
        return jsonify({"error": "消息不能为空"}), 400

    # ── 对话模式（2026-10-02）──────────────────────────────────────────
    # 非法/缺失一律按默认 plain 处理（与引入该开关之前逐字一致），并把非法值记进日志 ——
    # 前端传错不该让对话失败，但也不该静默变成别的模式。
    raw_mode = str(data.get("mode") or "").strip().lower()
    if raw_mode and raw_mode not in CHAT_STREAM_MODES:
        logger.warning("[workbench][SSE] 未知对话模式 %r，按 plain 处理（可选：%s）",
                       raw_mode, "/".join(CHAT_STREAM_MODES))
    mode = raw_mode if raw_mode in CHAT_STREAM_MODES else "plain"

    # ── 会话解析（2026-09-07 修复） ──
    # 与 /api/chat 对齐：请求显式传了会话 ID 但后端不存在时自动创建
    # （外部调用方/旧前端可能持有过期 ID）；否则回退全局当前会话。
    # 非法 ID（路径穿越/保留名/超长）回退默认会话，不因外部参数崩掉请求。
    session_id = raw_session_id
    if session_id:
        try:
            from app_server import _session_mgr as _sm_resolve
            if not _sm_resolve.get_session(session_id):
                try:
                    created = _sm_resolve.create_session(
                        session_id=session_id,
                        title=f"会话 {session_id[:24]}",
                    )
                    session_id = created["id"]
                except (ValueError, OSError):
                    logger.warning("[workbench][SSE] 会话 ID 非法，回退默认会话: %s", session_id)
                    session_id = ""
        except Exception as _e:
            logger.warning("[workbench][SSE] 会话解析失败，回退默认会话: %s", _e)
            session_id = ""
    if not session_id:
        from app_server import _get_current_session_id as _gcid
        session_id = _gcid()
    logger.info("[workbench][SSE] 开始流式响应（会话 %s）: %s", session_id, question[:60])

    def gen():
        # 累积流式 chunk 文本，流结束时落盘为 assistant 消息（会话持久化；
        # 客户端中途断开时 finally 仍会保存已生成的部分回复）。
        # 同时累积 thinking 步骤（思考过程 / 工具调用）：随同一条 assistant 消息落盘，
        # 使**刷新页面 / 切换会话后仍能恢复内联显示**（否则用户看到的是
        # "思考与工具先出现、刷新或切会话后就没了"）。
        acc_parts: list = []
        acc_steps: list = []
        #: 流是否**正常收尾**（区别于客户端中途断开）。只有正常收尾才写长期记忆，
        #: 见下方 finally 里的说明。
        completed = False
        try:
            for _evt in _workbench_real_stream(question, session_id, mode):
                _payload = _evt[len("data:"):].strip() if _evt.startswith("data:") else ""
                if _payload:
                    try:
                        _obj = json.loads(_payload)
                        if isinstance(_obj, dict) and _obj.get("type") == "chunk":
                            acc_parts.append(str(_obj.get("text") or ""))
                        elif isinstance(_obj, dict) and _obj.get("type") == "thinking":
                            merge_thinking_step(acc_steps, _obj)
                    except Exception:
                        pass
                yield _evt
            completed = True
        except GeneratorExit:
            # 客户端提前断开（前端点"停止生成"或关闭标签页）
            logger.info("[workbench][SSE] 客户端断开，终止生成")
        except Exception as _e:
            logger.error("[workbench][SSE] 生成器异常: %s", _e)
        finally:
            if session_id and (acc_parts or acc_steps):
                try:
                    from app_server import _session_mgr as _sm_persist
                    _sm_persist.add_message(
                        session_id, "assistant", "".join(acc_parts),
                        steps=finalized_steps(acc_steps),
                    )
                except Exception as _e2:
                    logger.warning("[workbench][SSE] 回复落盘失败: %s", _e2)
                # ── 与编排器路径（/api/chat）对齐：全局记忆 + 本轮状态（2026-10-02）──
                # 【为什么必须补】工作台是**主 UI**，此前却既不写记忆、也不写 turn_state：
                #   ① 记忆：编排器把 user/assistant 写进 _memory（长期记忆 / 压缩 / 召回的数据源），
                #      而工作台只写会话存储 ⇒ "面板量到的"与"被记住的"只反映另一半对话；
                #   ② turn_state：/api/chat 会记本轮 tool_steps / reasoning 供 /api/status 等消费，
                #      工作台只在会话消息里留 steps ⇒ 同一个会话换个入口读到的东西不一样。
                # 【只在正常收尾时写记忆】客户端中途断开时回复是**残缺**的，
                #   写进长期记忆会污染后续召回；会话存储仍保留部分回复（既有行为，便于用户查看）。
                if completed and "".join(acc_parts):
                    # 完整模式下记忆由编排器自己写（避免同一条对话在记忆里出现两遍）
                    _persist_turn_side_effects(
                        session_id, question, "".join(acc_parts), acc_steps,
                        write_memory=(mode != "full"))

    resp = Response(stream_with_context(gen()), mimetype="text/event-stream")
    # SSE 关键响应头；after_request 会再补 no-store，对 SSE 无碍
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


PLUGIN = register_plugin(Plugin(
    name="chat",
    version="1.0.0",
    description="对话、会话、历史记录",
    blueprint=bp,
    routes=[
        "/api/chat",
        "/api/chat/stream",
        "/api/clear",
        "/api/history",
        "/api/history/<int:index>",
        "/api/history/search",
        "/api/news",
        "/api/handoff",  # 会话交接文档（原 routes_sessions 未接线，2026-10-01 迁入）
        "/api/sessions",
        "/api/sessions/<session_id>",
        "/api/sessions/<session_id>/messages",
        "/api/sessions/<session_id>/rename",
        "/api/sessions/<session_id>/workspace",
        "/api/sessions/<session_id>/workspace-root",
        "/api/sessions/<session_id>/workspace/reveal",
        "/api/sessions/<session_id>/group",
        "/api/sessions/current",
        "/api/session-groups",
        "/api/session-groups/<group_id>",
        "/api/workspaces",
        "/api/voice/listen",
        "/api/voice/status",
    ],
))
