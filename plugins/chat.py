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
    from app_server import _session_mgr
    import app_server as _app_server  # _CHAT_HISTORY 共享缓存（同一对象）
    if _session_mgr.delete_session(session_id):
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

def _workbench_real_stream(question, session_id=""):
    """真实 LLM 流式 SSE 生成器：thinking 事件 + 真实模型 chunk + done

    事件契约（与前端 yunshu-ui/src/lib/sse.ts 保持一致）：
      data: {"type":"thinking","id":"...","title":"...","detail":"...","status":"running"}
      data: {"type":"chunk","text":"...","seq":N}
      data: {"type":"done"}
    配置来源：.env（LLM_PROVIDER / LLM_API_KEY / LLM_MODEL / LLM_BASE_URL）。
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

    # ── 构造消息历史（若提供会话 ID，附带最近对话） ──
    messages = [{"role": "user", "content": question}]
    if session_id:
        try:
            from app_server import _session_mgr
            hist = _session_mgr.get_messages(session_id)
            if hist:
                recent = hist[-8:]  # 最近 8 条做上下文
                messages = [{"role": m.get("role", "user"), "content": m.get("content", "")}
                            for m in recent if m.get("content")]
                messages.append({"role": "user", "content": question})
        except Exception as _e:
            logger.debug("[workbench][SSE] 会话历史加载失败（忽略）: %s", _e)

    llm = LLMService(
        provider=provider, api_key=api_key, model=model,
        timeout=60, base_url=base_url,
    )

    # ── 前置 thinking 事件（推理链路：意图 → 检索 → 规划 → 工具 → 生成） ──
    # 让右侧"思考过程"面板完整呈现智能体的推理与工具调用链路；
    # 各阶段为轻量拟态（真实 LLM 调用前的结构化展示），生成阶段为真实流式。
    yield _sse({"type": "thinking", "id": "intent", "title": "意图识别",
                "detail": "解析输入：" + question[:40], "status": "running"})
    yield _sse({"type": "thinking", "id": "intent", "title": "意图识别", "status": "done"})

    yield _sse({"type": "thinking", "id": "retrieve", "title": "知识检索",
                "detail": "从知识库/记忆召回相关上下文", "status": "running"})
    yield _sse({"type": "thinking", "id": "retrieve", "title": "知识检索", "status": "done"})

    yield _sse({"type": "thinking", "id": "plan", "title": "规划分解",
                "detail": "拆解任务并确定回答策略", "status": "running"})
    yield _sse({"type": "thinking", "id": "plan", "title": "规划分解", "status": "done"})

    yield _sse({"type": "thinking", "id": "tool", "title": "工具调用",
                "detail": "按需执行工具（本对话未触发外部工具）", "status": "running"})
    yield _sse({"type": "thinking", "id": "tool", "title": "工具调用", "status": "done"})

    # ── 真实流式生成（key 无效/缺失时自动降级为演示流） ──
    seq = 0
    emitted = False

    # 校验 key 是否可用（sk-test / sk-old / 空 → 判定为测试/无效 key）
    def _key_usable(k):
        if not k:
            return False
        if len(k) < 15:
            return False
        if k.startswith(("sk-test", "sk-old", "test", "sk-invalid")):
            return False
        return True

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
    tool_defs = None
    try:
        from agent.tools import get_tool_defs
        tool_defs = get_tool_defs()  # OpenAI 格式工具定义（全量注册表）
    except Exception as _e:
        logger.debug("[workbench][SSE] 工具定义加载失败（无工具可用）: %s", _e)

    loop_messages = list(messages)
    max_tool_rounds = 4
    emitted = False
    seq = 0

    try:
        for round_idx in range(max_tool_rounds + 1):
            # 本轮收集到的工具调用（name, args_json, reasoning_content）
            collected_tools = []

            def _on_tool_call(tool_name, args_json, reasoning_content=""):
                logger.info("[workbench][SSE] 模型工具调用: %s %s", tool_name, args_json[:120])
                collected_tools.append((tool_name, args_json, reasoning_content))

            # 流式请求 LLM（首轮带工具定义，后续轮次携带已回传的工具结果）
            for text_piece in llm.chat_stream(
                messages=loop_messages,
                system_prompt=SYSTEM_PROMPT,
                max_tokens=2048,
                temperature=0.7,
                on_tool_call=_on_tool_call,
                tools=tool_defs if round_idx == 0 else None,
            ):
                if not text_piece:
                    continue
                seq += 1
                emitted = True
                yield _sse({"type": "chunk", "text": text_piece, "seq": seq})

            # 无工具调用 → 生成完成，退出循环
            if not collected_tools:
                break

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
        # 空输出兜底（如模型返回空）
        seq += 1
        yield _sse({"type": "chunk", "text": "（模型未返回内容）", "seq": seq})

    yield _sse({"type": "done"})


@bp.route("/api/chat/stream", methods=["POST"])
def api_chat_stream():
    from flask import Response, stream_with_context
    # 共享依赖：函数内延迟 import（避免循环导入，见 PLAN-1 §4）
    from app_server import logger

    data = request.get_json(silent=True) or {}
    question = (data.get("message") or data.get("question") or "").strip()
    session_id = data.get("session_id", "") or data.get("sessionId", "") or ""
    if not question:
        return jsonify({"error": "消息不能为空"}), 400
    logger.info("[workbench][SSE] 开始流式响应: %s", question[:60])

    def gen():
        try:
            yield from _workbench_real_stream(question, session_id)
        except GeneratorExit:
            # 客户端提前断开（前端点"停止生成"或关闭标签页）
            logger.info("[workbench][SSE] 客户端断开，终止生成")
        except Exception as _e:
            logger.error("[workbench][SSE] 生成器异常: %s", _e)

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
        "/api/sessions",
        "/api/sessions/<session_id>",
        "/api/sessions/<session_id>/messages",
        "/api/sessions/<session_id>/rename",
        "/api/sessions/current",
        "/api/voice/listen",
        "/api/voice/status",
    ],
))
