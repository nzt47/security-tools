#!/usr/bin/env python3
"""
增强 routes_chat.py 和 routes_dashboard.py 的路由日志

目标：在关键路由入口处添加更详细的日志打印，
包含具体的参数值和 trace_id 变化过程，方便排查链路追踪问题。

修改点：
1. routes_chat.py  - api_voice_listen  入口日志增强
2. routes_chat.py  - api_chat          入口日志增强
3. routes_dashboard.py - api_dashboard_quality 入口日志增强
4. routes_dashboard.py - api_dashboard_traces  入口日志增强
"""
import os
import sys
import re

# ── 工具函数 ──────────────────────────────────────────────

def read_file_safe(path):
    """尝试多种编码读取文件"""
    for enc in ('utf-8', 'utf-8-sig', 'gbk', 'latin-1'):
        try:
            with open(path, 'r', encoding=enc) as f:
                content = f.read()
            print(f"  [OK] 读取成功: {path} (encoding={enc}, len={len(content)})")
            return content, enc
        except (UnicodeDecodeError, OSError) as e:
            print(f"  [FAIL] {enc}: {e}")
    raise RuntimeError(f"无法读取文件: {path}")


def write_file_safe(path, content, enc):
    """写回文件"""
    with open(path, 'w', encoding=enc, newline='') as f:
        f.write(content)
    print(f"  [OK] 写入成功: {path} (encoding={enc}, len={len(content)})")


def replace_once(content, old, new, label):
    """精确替换一次，失败时报错"""
    count = content.count(old)
    if count == 0:
        print(f"  [FAIL] 未找到: {label}")
        print(f"         old (前60字符): {repr(old[:60])}")
        return content, False
    if count > 1:
        print(f"  [WARN] 匹配多处({count}): {label}，仅替换第一处")
    content = content.replace(old, new, 1)
    print(f"  [OK] 替换成功: {label}")
    return content, True


# ── 增强日志模板 ──────────────────────────────────────────

# 1. routes_chat.py - api_voice_listen
# 原始日志：
#             _tid = get_trace_id()
#             logger.info(
#                 '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.start", "duration_ms": 0, "duration": %d}',
#                 _tid, duration
#             )
VOICE_LISTEN_OLD = '''            _tid = get_trace_id()
            logger.info(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.start", "duration_ms": 0, "duration": %d}',
                _tid, duration
            )'''

VOICE_LISTEN_NEW = '''            # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
            # @trace_route 已创建 TraceContext，此处读取的 trace_id 为本次请求的唯一标识
            # 记录 trace_id_entry 作为基准，后续节点对比 trace_id_changed 以排查链路断裂
            _tid_entry = get_trace_id()
            _vl_start = time.time()
            logger.info(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.entry", '
                '"duration_ms": 0, "phase": "entry", "params": {"duration": %d, "max": 30, "raw_duration": %s}, '
                '"trace_id_phase": "entry"}',
                _tid_entry, duration, data.get("duration", 5)
            )'''

# 在 voice_manager 检查失败处添加 trace_id 变化日志
VOICE_MGR_CHECK_OLD = '''            if not hasattr(Yunshu, '_voice_manager') or Yunshu._voice_manager is None:
                return jsonify({"ok": False, "error": "语音管理器未初始化"}), 500

            stt_available = Yunshu._voice_manager.stt.available
            if not stt_available:
                return jsonify({"ok": False, "error": "语音识别引擎不可用，请检查SpeechRecognition库"}), 500

            logger.info(f"[VOICE] 开始语音识别，时长: {duration}秒")
            result = Yunshu._voice_manager.listen(duration=duration)

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
            return jsonify({"ok": False, "error": str(e)}), 500'''

VOICE_MGR_CHECK_NEW = '''            if not hasattr(Yunshu, '_voice_manager') or Yunshu._voice_manager is None:
                _tid_chk = get_trace_id()
                logger.warning(
                    '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.fail", '
                    '"duration_ms": %.2f, "phase": "pre_check", "reason": "voice_manager_not_init", '
                    '"trace_id_entry": "%s", "trace_id_changed": %s}',
                    _tid_chk, (time.time() - _vl_start) * 1000, _tid_entry, str(_tid_chk != _tid_entry)
                )
                return jsonify({"ok": False, "error": "语音管理器未初始化"}), 500

            stt_available = Yunshu._voice_manager.stt.available
            if not stt_available:
                _tid_stt = get_trace_id()
                logger.warning(
                    '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.fail", '
                    '"duration_ms": %.2f, "phase": "stt_check", "reason": "stt_unavailable", '
                    '"trace_id_entry": "%s", "trace_id_changed": %s}',
                    _tid_stt, (time.time() - _vl_start) * 1000, _tid_entry, str(_tid_stt != _tid_entry)
                )
                return jsonify({"ok": False, "error": "语音识别引擎不可用，请检查SpeechRecognition库"}), 500

            # ── 链路追踪：调用前日志（记录 trace_id 是否在检查过程中变化） ──
            _tid_pre = get_trace_id()
            logger.info(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.pre_listen", '
                '"duration_ms": %.2f, "phase": "pre_listen", "duration_param": %d, '
                '"trace_id_entry": "%s", "trace_id_changed": %s}',
                _tid_pre, (time.time() - _vl_start) * 1000, duration,
                _tid_entry, str(_tid_pre != _tid_entry)
            )

            result = Yunshu._voice_manager.listen(duration=duration)

            # ── 链路追踪：调用后日志（记录结果摘要 + trace_id 一致性） ──
            _tid_post = get_trace_id()
            logger.info(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.post_listen", '
                '"duration_ms": %.2f, "phase": "post_listen", "success": %s, "text_len": %d, '
                '"trace_id_entry": "%s", "trace_id_changed": %s}',
                _tid_post, (time.time() - _vl_start) * 1000, str(result.success),
                len(result.text) if result.success and result.text else 0,
                _tid_entry, str(_tid_post != _tid_entry)
            )

            if result.success:
                return jsonify({
                    "ok": True,
                    "text": result.text,
                    "duration": duration
                })
            else:
                return jsonify({"ok": False, "error": result.error}), 400
        except Exception as e:
            _tid_err = get_trace_id()
            logger.error(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "voice_listen.error", '
                '"duration_ms": %.2f, "phase": "exception", "error": "%s", '
                '"trace_id_entry": "%s", "trace_id_changed": %s}',
                _tid_err, (time.time() - _vl_start) * 1000, str(e),
                _tid_entry, str(_tid_err != _tid_entry)
            )
            return jsonify({"ok": False, "error": str(e)}), 500'''

# 2. routes_chat.py - api_chat
CHAT_OLD = '''        # trace_id 链路追踪验证日志（手动创建 TraceContext 以保证 trace_id 可用）
        _tid = get_trace_id() or "no-trace"
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_chat", "action": "chat.start", "duration_ms": 0, "input_len": %d, "voice_mode": %s}',
            _tid, len(user_input), voice_mode
        )'''

CHAT_NEW = '''        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # 记录 trace_id_entry 作为基准；后续在安全检查、LLM 调用、语音合成等节点
        # 对比 trace_id_changed 字段，可快速定位链路断裂点
        _tid_entry = get_trace_id() or "no-trace"
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_chat", "action": "chat.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"input_len": %d, "input_preview": "%s", "voice_mode": %s, "has_session_arg": %s, "has_session_body": %s}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, len(user_input), user_input[:50].replace('"', "'"),
            voice_mode, str(bool(request.args.get("session"))), str(bool(data.get("session")))
        )'''

# 在 api_chat 的安全检查后添加 trace_id 变化日志
CHAT_SAFETY_OLD = '''        safety_time = (time.time() - safety_start) * 1000
        logs.append(f"[SAFETY] 安全检查完成 - 耗时: {safety_time:.2f}ms, 级别: {safety_result['level']}")'''

CHAT_SAFETY_NEW = '''        safety_time = (time.time() - safety_start) * 1000
        logs.append(f"[SAFETY] 安全检查完成 - 耗时: {safety_time:.2f}ms, 级别: {safety_result['level']}")
        # ── 链路追踪：安全检查后日志（记录 trace_id 变化） ──
        _tid_safety = get_trace_id() or "no-trace"
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_chat", "action": "chat.post_safety", '
            '"duration_ms": %.2f, "phase": "post_safety", "safety_level": "%s", "safety_safe": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_safety, safety_time, safety_result['level'], str(safety_result.get('safe', True)),
            _tid_entry, str(_tid_safety != _tid_entry)
        )'''

# 在 api_chat 的 LLM 调用后添加 trace_id 变化日志
CHAT_LLM_OLD = '''        chat_start = time.time()
        try:
            response = Yunshu.chat(user_input)
            chat_time = (time.time() - chat_start) * 1000
            logs.append(f"[CHAT] 对话响应生成完成 - 耗时: {chat_time:.2f}ms")'''

CHAT_LLM_NEW = '''        chat_start = time.time()
        try:
            response = Yunshu.chat(user_input)
            chat_time = (time.time() - chat_start) * 1000
            logs.append(f"[CHAT] 对话响应生成完成 - 耗时: {chat_time:.2f}ms")
            # ── 链路追踪：LLM 调用后日志（记录 trace_id 变化 + 响应摘要） ──
            _tid_llm = get_trace_id() or "no-trace"
            logger.info(
                '{"trace_id": "%s", "module_name": "routes_chat", "action": "chat.post_llm", '
                '"duration_ms": %.2f, "phase": "post_llm", "response_len": %d, "response_preview": "%s", '
                '"trace_id_entry": "%s", "trace_id_changed": %s}',
                _tid_llm, chat_time, len(response), response[:50].replace('"', "'").replace('\\n', ' '),
                _tid_entry, str(_tid_llm != _tid_entry)
            )'''

# 3. routes_dashboard.py - api_dashboard_quality
QUALITY_OLD = '''        # trace_id 链路追踪验证日志（@trace_route 已创建 TraceContext，此处可读取）
        _tid = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.start", "duration_ms": 0, "time_range": "%s"}',
            _tid, time_range
        )'''

QUALITY_NEW = '''        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # @trace_route 已创建 TraceContext，此处读取的 trace_id 为本次请求的唯一标识
        # 记录 trace_id_entry 作为基准，后续节点对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _q_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"time_range": "%s", "start_time": %s, "end_time": %s}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, time_range,
            str(start_time) if start_time else "null",
            str(end_time) if end_time else "null"
        )'''

# 在 quality 的结果返回前添加 trace_id 变化日志
QUALITY_END_OLD = '''        result = _get_quality_metrics(start_time, end_time)
        return jsonify(result)'''

QUALITY_END_NEW = '''        result = _get_quality_metrics(start_time, end_time)
        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "quality.exit", '
            '"duration_ms": %.2f, "phase": "exit", "schema_pass_rate": %s, "critic_avg": %s, "total_failures": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _q_start) * 1000,
            str(result.get("schema_validation", {}).get("pass_rate", "null")),
            str(result.get("critic_scores", {}).get("average_score", "null")),
            str(result.get("failure_distribution", {}).get("total_failures", "null")),
            _tid_entry, str(_tid_exit != _tid_entry)
        )
        return jsonify(result)'''

# 4. routes_dashboard.py - api_dashboard_traces
TRACES_OLD = '''        # trace_id 链路追踪验证日志
        _tid = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.list", "duration_ms": 0, "limit": %d, "filter": "%s"}',
            _tid, limit, trace_id_filter
        )'''

TRACES_NEW = '''        # ── 链路追踪：入口日志（含完整参数值 + 初始 trace_id） ──
        # 记录 trace_id_entry 作为基准，出口处对比 trace_id_changed 以排查链路断裂
        _tid_entry = get_trace_id()
        _t_start = time.time()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.entry", '
            '"duration_ms": 0, "phase": "entry", "params": {"limit": %d, "trace_id_filter": "%s", "filter_len": %d}, '
            '"trace_id_phase": "entry"}',
            _tid_entry, limit, trace_id_filter, len(trace_id_filter)
        )'''

# 在 traces 的结果返回前添加 trace_id 变化日志
TRACES_END_OLD = '''        traces = _get_trace_list(limit, trace_id_filter)
        
        return jsonify({
            "traces": traces,
            "total": len(traces),
            "limit": limit,
            "timestamp": time.time()
        })'''

TRACES_END_NEW = '''        traces = _get_trace_list(limit, trace_id_filter)

        # ── 链路追踪：出口日志（记录 trace_id 变化 + 结果摘要） ──
        _tid_exit = get_trace_id()
        logger.info(
            '{"trace_id": "%s", "module_name": "routes_dashboard", "action": "traces.exit", '
            '"duration_ms": %.2f, "phase": "exit", "traces_count": %d, "limit": %d, "has_filter": %s, '
            '"trace_id_entry": "%s", "trace_id_changed": %s}',
            _tid_exit, (time.time() - _t_start) * 1000, len(traces), limit,
            str(bool(trace_id_filter)), _tid_entry, str(_tid_exit != _tid_entry)
        )

        return jsonify({
            "traces": traces,
            "total": len(traces),
            "limit": limit,
            "timestamp": time.time()
        })'''


# ── 主流程 ──────────────────────────────────────────────

def enhance_routes_chat():
    """增强 routes_chat.py 的路由日志"""
    path = os.path.join('agent', 'server_routes', 'routes_chat.py')
    print(f"\n[1/2] 增强 {path}")
    content, enc = read_file_safe(path)

    # 替换 api_voice_listen 的入口日志
    content, ok1 = replace_once(content, VOICE_LISTEN_OLD, VOICE_LISTEN_NEW, "voice_listen 入口日志")
    # 替换 api_voice_listen 的检查和调用部分
    content, ok2 = replace_once(content, VOICE_MGR_CHECK_OLD, VOICE_MGR_CHECK_NEW, "voice_listen 检查/调用日志")
    # 替换 api_chat 的入口日志
    content, ok3 = replace_once(content, CHAT_OLD, CHAT_NEW, "chat 入口日志")
    # 替换 api_chat 安全检查后日志
    content, ok4 = replace_once(content, CHAT_SAFETY_OLD, CHAT_SAFETY_NEW, "chat 安全检查后日志")
    # 替换 api_chat LLM 调用后日志
    content, ok5 = replace_once(content, CHAT_LLM_OLD, CHAT_LLM_NEW, "chat LLM 调用后日志")

    if ok1 and ok2 and ok3 and ok4 and ok5:
        write_file_safe(path, content, enc)
        print(f"  [DONE] routes_chat.py 全部 5 处替换完成")
    else:
        print(f"  [SKIP] routes_chat.py 有未匹配项，未写入文件（避免部分修改）")
        return False
    return True


def enhance_routes_dashboard():
    """增强 routes_dashboard.py 的路由日志"""
    path = os.path.join('agent', 'server_routes', 'routes_dashboard.py')
    print(f"\n[2/2] 增强 {path}")
    content, enc = read_file_safe(path)

    # 替换 api_dashboard_quality 入口日志
    content, ok1 = replace_once(content, QUALITY_OLD, QUALITY_NEW, "quality 入口日志")
    # 替换 api_dashboard_quality 出口日志
    content, ok2 = replace_once(content, QUALITY_END_OLD, QUALITY_END_NEW, "quality 出口日志")
    # 替换 api_dashboard_traces 入口日志
    content, ok3 = replace_once(content, TRACES_OLD, TRACES_NEW, "traces 入口日志")
    # 替换 api_dashboard_traces 出口日志
    content, ok4 = replace_once(content, TRACES_END_OLD, TRACES_END_NEW, "traces 出口日志")

    if ok1 and ok2 and ok3 and ok4:
        write_file_safe(path, content, enc)
        print(f"  [DONE] routes_dashboard.py 全部 4 处替换完成")
    else:
        print(f"  [SKIP] routes_dashboard.py 有未匹配项，未写入文件（避免部分修改）")
        return False
    return True


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(project_root)
    print(f"工作目录: {project_root}")

    ok1 = enhance_routes_chat()
    ok2 = enhance_routes_dashboard()

    print("\n" + "=" * 60)
    print("增强结果汇总:")
    print(f"  routes_chat.py:      {'成功' if ok1 else '失败'}")
    print(f"  routes_dashboard.py: {'成功' if ok2 else '失败'}")
    print("=" * 60)

    if ok1 and ok2:
        print("\n所有路由日志增强完成！")
        print("新增日志节点：")
        print("  - api_voice_listen: entry / pre_check / stt_check / pre_listen / post_listen / error")
        print("  - api_chat:         entry / post_safety / post_llm")
        print("  - api_dashboard_quality: entry / exit")
        print("  - api_dashboard_traces:  entry / exit")
        print("\n每个日志节点都包含:")
        print("  - trace_id:        当前节点的 trace_id")
        print("  - trace_id_entry:  入口基准 trace_id")
        print("  - trace_id_changed: trace_id 是否发生变化（true 表示链路可能断裂）")
        print("  - phase:           当前阶段标识")
        print("  - params:          具体参数值")
        return 0
    else:
        print("\n部分替换失败，请检查上述 [FAIL] 信息")
        return 1


if __name__ == '__main__':
    sys.exit(main())
