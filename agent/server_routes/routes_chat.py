"""对话 & 语音 & Web 工具 API 路由"""
import os
import json
import uuid
import time
import datetime as dt
import logging
from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route
from agent.logging_utils import log_dict
# 业务埋点（TE-001）：trackEvent 失败不影响主流程
try:
    from agent.server_routes.observability import trackEvent
except Exception:
    def trackEvent(event_name, payload=None):
        """埋点降级：仅日志记录"""
        logger.debug(f"[trackEvent degraded] {event_name}: {payload}")

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]



def _save_conversation_record(user_input, response, Yunshu, mode="normal", health_data=None):
    """自动保存对话记录到云枢记忆目录"""
    from agent.system_tools import WORKSPACE_DIR
    memory_dir = os.path.join(WORKSPACE_DIR, "云枢记忆")
    os.makedirs(memory_dir, exist_ok=True)

    now = dt.datetime.now()
    date_str = now.strftime("%Y%m%d")

    prefix = os.path.join(memory_dir, f"会话记录_{date_str}")
    seq = 0
    try:
        for f in os.listdir(memory_dir):
            if f.startswith(f"会话记录_{date_str}") and f.endswith(".txt"):
                seq += 1
    except OSError:
        pass
    seq += 1

    filename = f"会话记录_{date_str}_{seq:03d}.txt"
    filepath = os.path.join(memory_dir, filename)

    health_lines = []
    if health_data:
        for h in health_data[:6]:
            name = h.get("description", h.get("sensor_name", "?"))
            value = h.get("severity", "normal")
            icon = "🟢" if value == "normal" else "🟡" if value == "warning" else "🔴"
            health_lines.append(f"🔹 {name}：{icon} {value}")

    record = (
        "=" * 45 + "\n"
        f"  会话记录 #{seq}\n"
        "=" * 45 + "\n\n"
        f"🕒 时间：{now.strftime('%Y年%m月%d日 %H:%M')}\n"
        f"📋 模式：{mode}\n\n"
        "---\n\n"
        "💬 【对话内容】\n\n"
        f"👤 用户：\n{user_input.strip()}\n\n"
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


def _get_current_session_id(session_mgr):
    """获取当前会话 ID，如无则创建新会话"""
    session_id = session_mgr.get_current_id()
    if not session_id:
        session = session_mgr.create_session("新会话")
        session_id = session["id"]
    return session_id


# ── 尝试导入 Prometheus 指标 ──
try:
    from prometheus_flask_exporter import Counter as _Counter
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

if PROMETHEUS_AVAILABLE:
    try:
        from prometheus_client import REGISTRY as _DEFAULT_REGISTRY
        SECURITY_BLOCKS = _Counter(
            'yunshu_security_blocks_total',
            'Total number of security blocks',
            ['rule', 'level', 'category']
        )
        LLM_CALLS = _Counter(
            'yunshu_llm_calls_total',
            'Total number of LLM calls',
            ['provider', 'model', 'status']
        )
    except Exception:
        SECURITY_BLOCKS = None
        LLM_CALLS = None
else:
    SECURITY_BLOCKS = None
    LLM_CALLS = None


def register_routes(app, state):
    """注册所有对话 & 语音 & Web 路由"""

    Yunshu = state.Yunshu
    safety_guard = state.safety_guard
    session_mgr = state.session_mgr
    chat_history = state.chat_history

    web_http = state.http_client
    web_scraper = state.scraper
    web_search = state.search_engine
    web_processor = state.processor

    # ═══════════════════════════════════════════════════
    #  语音 API
    # ═══════════════════════════════════════════════════

    @app.route("/api/voice/listen", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_voice_listen():
        try:
            data = request.get_json() or {}
            duration = min(data.get("duration", 5), 30)

            if not hasattr(Yunshu, '_voice_manager') or Yunshu._voice_manager is None:
                return jsonify({"ok": False, "error": "语音管理器未初始化"}), 500

            stt_available = Yunshu._voice_manager.stt.available
            if not stt_available:
                return jsonify({"ok": False, "error": "语音识别引擎不可用，请检查SpeechRecognition库"}), 500

            logger.info(log_dict({'module_name': 'routes_chat', 'action': 'duration', 'msg': f'[VOICE] 开始语音识别，时长: {duration}秒'}))
            result = Yunshu._voice_manager.listen(duration=duration)

            if result.success:
                logger.info(log_dict({'module_name': 'routes_chat', 'action': 'result.text', 'msg': f'[VOICE] 语音识别成功: {result.text[:50]}...'}))
                # 埋点：语音识别成功
                trackEvent('voice_listen_success', {
                    'duration': duration,
                    'text_length': len(result.text or ''),
                })
                return jsonify({
                    "ok": True,
                    "text": result.text,
                    "duration": duration
                })
            else:
                logger.warning(log_dict({'module_name': 'routes_chat', 'action': 'result.error', 'msg': f'[VOICE] 语音识别失败: {result.error}'}))
                # 埋点：语音识别失败
                trackEvent('voice_listen_failed', {
                    'duration': duration,
                    'error': result.error,
                })
                return jsonify({"ok": False, "error": result.error}), 400
        except Exception as e:
            logger.error(log_dict({'module_name': 'routes_chat', 'action': 'log', 'msg': f'[VOICE] 语音识别异常: {e}'}))
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/voice/status")
    @trace_route("Chat")
    @log_request(show_response=False)
    def api_voice_status():
        try:
            if not hasattr(Yunshu, '_voice_manager') or Yunshu._voice_manager is None:
                return jsonify({
                    "tts_available": False,
                    "stt_available": False,
                    "engine": "none",
                    "non_blocking": False
                })
            status = Yunshu._voice_manager.get_status()
            return jsonify(status)
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  对话 API
    # ═══════════════════════════════════════════════════

    @app.route("/api/chat", methods=["POST"])
    @trace_route("Chat")
    def api_chat():
        start_time = time.time()

        data = request.get_json(silent=True) or {}
        user_input = (data or {}).get("message", "").strip()
        voice_mode = (data or {}).get("voice", False)

        if request.args.get("session"):
            session_id = request.args["session"]
        elif data.get("session"):
            session_id = data["session"]
        else:
            session_id = _get_current_session_id(session_mgr)

        logs = []
        logs.append(f"[START] 收到对话请求 - 时间: {dt.datetime.now().isoformat()}")
        logs.append(f"[INPUT] 用户输入: {user_input[:100]}{'...' if len(user_input) > 100 else ''}")
        logs.append(f"[CONFIG] 语音模式: {voice_mode}")

        # 埋点：用户提交对话消息（关键交互点）
        trackEvent('chat_submit', {
            'session_id': session_id,
            'message_length': len(user_input),
            'voice_mode': voice_mode,
            'mode': Yunshu.get_behavior_mode().value,
        })

        if not user_input:
            return jsonify({"error": "消息不能为空"}), 400

        # 安全检查
        safety_start = time.time()
        if not getattr(Yunshu, '_is_skill_enabled', lambda x: True)("safety_guard"):
            safety_result = {"level": "safe", "matches": [], "safe": True}
            logs.append("[SAFETY] 安全守护技能已禁用，跳过检查")
        else:
            safety_result = safety_guard.check(user_input)
        safety_time = (time.time() - safety_start) * 1000
        logs.append(f"[SAFETY] 安全检查完成 - 耗时: {safety_time:.2f}ms, 级别: {safety_result['level']}")

        if safety_result["level"] == "critical":
            match_lines = "\n".join(
                f"• {m['description']} [{m['category']}]"
                for m in safety_result["matches"][:5]
            )
            blocked_msg = (
                f"⚠️ 安全警告：检测到危险操作！\n\n{match_lines}"
                f"\n\n此操作已被拦截。如需执行，请确认您了解相关风险。"
            )
            logs.append(f"[BLOCKED] 安全拦截触发")

            # 埋点：安全拦截触发（关键决策点）
            trackEvent('safety_block', {
                'session_id': session_id,
                'level': safety_result['level'],
                'match_count': len(safety_result.get('matches', [])),
                'categories': [m.get('category', 'unknown') for m in safety_result.get('matches', [])[:5]],
            })

            if PROMETHEUS_AVAILABLE and SECURITY_BLOCKS:
                for match in safety_result["matches"]:
                    SECURITY_BLOCKS.labels(
                        rule=match.get('description', 'unknown'),
                        level=match.get('level', 'unknown'),
                        category=match.get('category', 'unknown')
                    ).inc()

            return jsonify({
                "response": blocked_msg,
                "mode": Yunshu.get_behavior_mode().value,
                "mode_label": Yunshu._behavior.profile.label,
                "blocked": True,
                "safety": safety_result,
                "logs": logs,
                "timing": {"total": (time.time() - start_time) * 1000},
            }), 403

        llm_state = Yunshu.get_config()
        logs.append(f"[LLM] 配置状态 - 已配置: {llm_state['configured']}, 提供商: {llm_state['provider']}, API Key已设置: {llm_state['api_key_set']}")

        chat_start = time.time()
        try:
            response = Yunshu.chat(user_input)
            chat_time = (time.time() - chat_start) * 1000
            logs.append(f"[CHAT] 对话响应生成完成 - 耗时: {chat_time:.2f}ms")
        except Exception as e:
            import traceback
            chat_time = (time.time() - chat_start) * 1000
            logger.error(f"Chat error: {e}", exc_info=True)
            response = f"（处理出错: {e}）"
            logs.append(f"[ERROR] 对话处理失败 - 耗时: {chat_time:.2f}ms, 错误: {str(e)}")
            stack_trace = traceback.format_exc()
            logs.append(f"[STACK TRACE] {stack_trace[:500]}")

        # 语音合成
        voice_time = 0
        voice_result = None
        if voice_mode:
            voice_start = time.time()
            try:
                voice_result = Yunshu.speak(response)
                voice_time = (time.time() - voice_start) * 1000
                if voice_result.get("ok"):
                    logs.append(f"[VOICE] 语音合成成功 - 耗时: {voice_time:.2f}ms")
                else:
                    logs.append(f"[VOICE] 语音合成失败 - 耗时: {voice_time:.2f}ms, 错误: {voice_result.get('error')}")
            except Exception as e:
                voice_time = (time.time() - voice_start) * 1000
                logs.append(f"[ERROR] 语音合成异常 - 耗时: {voice_time:.2f}ms, 错误: {str(e)}")

        entry = {
            "user": user_input,
            "Yunshu": response,
            "mode": Yunshu.get_behavior_mode().value,
            "timestamp": dt.datetime.now().isoformat(),
        }
        session_mgr.add_message(session_id, "user", user_input)
        session_mgr.add_message(
            session_id, "assistant", response,
            tool_steps=getattr(Yunshu, '_last_tool_steps', None),
            reasoning=getattr(Yunshu, '_last_reasoning', None),
        )
        chat_history.append(entry)

        _save_conversation_record(
            user_input=user_input,
            response=response,
            Yunshu=Yunshu,
            mode=Yunshu.get_behavior_mode().value,
            health_data=[r.to_dict() for r in Yunshu.check_health()],
        )

        total_time = (time.time() - start_time) * 1000
        logs.append(f"[END] 请求处理完成 - 总耗时: {total_time:.2f}ms")

        # 埋点：对话响应完成（关键交互点）
        trackEvent('chat_complete', {
            'session_id': session_id,
            'safety_level': safety_result['level'],
            'response_length': len(response),
            'total_duration_ms': round(total_time, 2),
            'chat_duration_ms': round(chat_time, 2),
            'voice_synthesis': voice_mode,
            'mode': Yunshu.get_behavior_mode().value,
        })

        print("\n" + "=" * 80)
        print(f"📊 对话请求日志 [{dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
        print("-" * 80)
        for log_line in logs:
            print(log_line)
        print("=" * 80 + "\n")

        return jsonify({
            "response": response,
            "mode": Yunshu.get_behavior_mode().value,
            "mode_label": Yunshu._behavior.profile.label,
            "thinking_mode": getattr(Yunshu, '_thinking_mode', {"mode": "idle", "label": ""}),
            "health": [r.to_dict() for r in Yunshu.check_health()],
            "llm_state": llm_state,
            "logs": logs,
            "tool_steps": getattr(Yunshu, '_last_tool_steps', []),
            "reasoning": getattr(Yunshu, '_last_reasoning', None),
            "timing": {
                "total": total_time,
                "safety_check": safety_time,
                "chat_processing": chat_time,
                "voice_synthesis": voice_time,
            },
            "voice_result": voice_result,
        })

    # ═══════════════════════════════════════════════════
    #  Web HTTP API
    # ═══════════════════════════════════════════════════

    @app.route("/api/web/get", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_get():
        data = request.get_json() or {}
        url = data.get("url", "")
        timeout = data.get("timeout", 30)
        if not url:
            return jsonify({"ok": False, "error": "缺少 url"}), 400
        # 埋点：网页抓取请求（关键交互点）
        trackEvent('web_scrape_request', {
            'url_length': len(url),
            'timeout': timeout,
        })
        scrape_start = time.time()
        result = web_http.get(url, timeout=timeout)
        scrape_duration_ms = (time.time() - scrape_start) * 1000
        if result.get("ok") and result.get("text"):
            parsed = web_scraper.parse(result["text"], url=result.get("url", url))
            result["parsed"] = {k: parsed.get(k) for k in ("title", "text", "links", "images", "meta", "headings") if k != "html"}
        # 埋点：网页抓取结果
        trackEvent('web_scrape_result', {
            'url_length': len(url),
            'success': result.get("ok", False),
            'text_length': len(result.get("text", "")),
            'duration_ms': round(scrape_duration_ms, 2),
        })
        return jsonify(result)

    @app.route("/api/web/post", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_post():
        data = request.get_json() or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"ok": False, "error": "缺少 url"}), 400
        form_data = data.get("data", {})
        json_data = data.get("json_data", {})
        if json_data:
            result = web_http.post(url, json_data=json_data)
        else:
            result = web_http.post(url, data=form_data)
        return jsonify(result)

    @app.route("/api/web/xpath", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_xpath():
        data = request.get_json() or {}
        url = data.get("url", "")
        expression = data.get("expression", "")
        html = data.get("html", "")
        if not expression:
            return jsonify({"ok": False, "error": "缺少 expression"}), 400
        if html:
            results = web_scraper.xpath(expression, html=html)
            return jsonify({"ok": True, "results": results, "count": len(results)})
        if not url:
            return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400
        fetch = web_http.get(url)
        if not fetch.get("ok"):
            return jsonify(fetch)
        results = web_scraper.xpath(expression, html=fetch.get("text", ""))
        return jsonify({"ok": True, "results": results, "count": len(results)})

    @app.route("/api/web/css", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_css():
        data = request.get_json() or {}
        url = data.get("url", "")
        selector = data.get("selector", "")
        attr = data.get("attr", "")
        html = data.get("html", "")
        if not selector:
            return jsonify({"ok": False, "error": "缺少 selector"}), 400
        if html:
            results = web_scraper.css(selector, html=html, attr=attr or None)
            return jsonify({"ok": True, "results": results, "count": len(results)})
        if not url:
            return jsonify({"ok": False, "error": "缺少 url 或 html"}), 400
        fetch = web_http.get(url)
        if not fetch.get("ok"):
            return jsonify(fetch)
        results = web_scraper.css(selector, html=fetch.get("text", ""), attr=attr or None)
        return jsonify({"ok": True, "results": results, "count": len(results)})

    @app.route("/api/web/search", methods=["GET"])
    @trace_route("Chat")
    @log_request(show_response=False)
    def api_web_search():
        query = request.args.get("query", "")
        num = min(int(request.args.get("num_results", 10)), 50)
        engine = request.args.get("engine", "")
        if not query:
            return jsonify({"ok": False, "error": "缺少 query"}), 400
        # 埋点：搜索请求提交（关键交互点）
        trackEvent('web_search_submit', {
            'query_length': len(query),
            'num_results': num,
            'engine': engine or 'default',
        })
        search_start = time.time()
        result = web_search.search(query, engine=engine, num_results=num)
        search_duration_ms = (time.time() - search_start) * 1000
        if result.get("ok") and result.get("results"):
            processed = web_processor.process(result["results"])
            result["results"] = processed
            from agent.web import DataProcessor
            result["summary"] = DataProcessor.summarize_results(processed)
        # 埋点：搜索结果返回
        trackEvent('web_search_result', {
            'query_length': len(query),
            'result_count': len(result.get("results", [])),
            'duration_ms': round(search_duration_ms, 2),
            'success': result.get("ok", False),
        })
        return jsonify(result)

    @app.route("/api/web/clean", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_clean():
        data = request.get_json() or {}
        text = data.get("text", "")
        items = data.get("items", [])
        if text:
            from agent.web import DataProcessor
            return jsonify({"ok": True, "cleaned": DataProcessor.clean_text(text)})
        if items:
            processed = web_processor.process(items)
            return jsonify({
                "ok": True,
                "original_count": len(items),
                "processed_count": len(processed),
                "results": processed,
            })
        return jsonify({"ok": False, "error": "请提供 text 或 items"}), 400

    @app.route("/api/web/download", methods=["POST"])
    @trace_route("Chat")
    @require_token
    @log_request()
    def api_web_download():
        data = request.get_json() or {}
        url = data.get("url", "")
        filepath = data.get("filepath", "")
        if not url or not filepath:
            return jsonify({"ok": False, "error": "缺少 url 或 filepath"}), 400
        return jsonify(web_http.download(url, filepath))

    @app.route("/api/web/stats")
    @trace_route("Chat")
    @log_request(show_response=False)
    def api_web_stats():
        return jsonify({
            "http": web_http.get_stats(),
            "search": web_search.get_stats(),
            "processor": web_processor.get_stats(),
            "crawler_control": state.crawler_controller.get_stats() if state.crawler_controller else {},
        })

    @app.route("/api/web/search/status")
    @trace_route("Chat")
    @log_request(show_response=False)
    def api_web_search_status():
        try:
            status = web_search.get_current_status()
            return jsonify({"ok": True, "status": status})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
