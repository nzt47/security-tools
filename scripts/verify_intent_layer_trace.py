#!/usr/bin/env python3
"""验证 orchestrator _record_intent_layer 埋点日志的 trace_id 上下文传递

确认埋点按预期触发 trace_id 上下文：在 set_trace_id / TraceContext 作用域内
调用 _record_intent_layer，日志中 trace_id_ctx 应与上下文一致。

用法: python scripts/verify_intent_layer_trace.py
"""

import ast
import logging
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.monitoring.tracing import set_trace_id, get_trace_id, TraceContext
from agent.orchestrator.orchestrator import _record_intent_layer

# [简易] 自定义 handler 捕获日志消息，便于解析 trace_id_ctx 字段
captured_logs: list = []


class _CaptureHandler(logging.Handler):
    def emit(self, record):
        captured_logs.append(record.getMessage())


_handler = _CaptureHandler()
logging.getLogger().addHandler(_handler)
logging.getLogger().setLevel(logging.INFO)

_TRACE_CTX_RE = re.compile(r"'trace_id_ctx':\s*'([^']*)'")


def _last_log_trace_ctx() -> str:
    """从最近一条日志解析 trace_id_ctx 字段值"""
    if not captured_logs:
        return None
    m = _TRACE_CTX_RE.search(captured_logs[-1])
    return m.group(1) if m else None


def main():
    passed = 0
    failed = 0

    def _check(name, ok, detail=""):
        nonlocal passed, failed
        print("  [%s] %s%s" % ("OK" if ok else "FAIL", name,
                               (" - " + detail) if detail else ""))
        if ok:
            passed += 1
        else:
            failed += 1

    print("=" * 64)
    print("trace_id 上下文传递验证")
    print("=" * 64)

    # 测试1: set_trace_id 注入固定 ID，日志 trace_id_ctx 应一致
    print("\n[测试1] set_trace_id 注入固定 ID")
    set_trace_id("FIXED_TRACE_ABC123")
    captured_logs.clear()
    _record_intent_layer("rule")
    ctx_in_log = _last_log_trace_ctx()
    _check("get_trace_id() 返回固定 ID",
           get_trace_id() == "FIXED_TRACE_ABC123",
           "actual=%s" % get_trace_id())
    _check("日志 trace_id_ctx 与上下文一致",
           ctx_in_log == "FIXED_TRACE_ABC123",
           "log=%s" % ctx_in_log)

    # 测试2: TraceContext 上下文管理器生成新 trace_id
    print("\n[测试2] TraceContext 生成并复用 trace_id")
    set_trace_id(None)  # 清空，确保 TraceContext 生成新 ID
    with TraceContext("VerifySvc", "trace_check") as ctx:
        captured_logs.clear()
        _record_intent_layer("semantic")
        ctx_in_log = _last_log_trace_ctx()
        _check("with 块内 get_trace_id() == ctx.trace_id",
               get_trace_id() == ctx.trace_id,
               "ctx=%s get=%s" % (ctx.trace_id, get_trace_id()))
        _check("日志 trace_id_ctx 复用 ctx.trace_id",
               ctx_in_log == ctx.trace_id,
               "log=%s ctx=%s" % (ctx_in_log, ctx.trace_id))

    # 测试3: 嵌套上下文复用外层 trace_id
    print("\n[测试3] 嵌套上下文复用外层 trace_id")
    set_trace_id("OUTER_TRACE_XYZ")
    with TraceContext("Outer", "op") as outer:
        captured_logs.clear()
        _record_intent_layer("llm")
        ctx_in_log = _last_log_trace_ctx()
        _check("外层日志 trace_id_ctx = OUTER_TRACE_XYZ",
               ctx_in_log == "OUTER_TRACE_XYZ",
               "log=%s" % ctx_in_log)
        with TraceContext("Inner", "sub"):
            captured_logs.clear()
            _record_intent_layer("reject")
            inner_ctx = _last_log_trace_ctx()
            _check("内层复用外层 trace_id",
                   inner_ctx == "OUTER_TRACE_XYZ",
                   "log=%s" % inner_ctx)

    # 测试4: 退出 TraceContext 后恢复进入前的值
    print("\n[测试4] 退出后上下文恢复")
    after = get_trace_id()
    _check("退出后 get_trace_id() 恢复为 OUTER_TRACE_XYZ",
           after == "OUTER_TRACE_XYZ",
           "actual=%s" % after)

    # 测试5: 无上下文时不崩（_trace_id 兜底生成临时 ID）
    print("\n[测试5] 无上下文时埋点不崩（兜底生成临时 trace_id）")
    set_trace_id(None)
    try:
        captured_logs.clear()
        _record_intent_layer("llm")
        ctx_in_log = _last_log_trace_ctx()
        _check("无上下文仍输出 trace_id_ctx（兜底临时 ID）",
               ctx_in_log is not None and len(ctx_in_log) > 0,
               "log=%s" % ctx_in_log)
    except Exception as e:
        _check("无上下文不抛异常", False, "err=%s" % e)

    print("\n" + "=" * 64)
    print("结果: %d passed, %d failed" % (passed, failed))
    print("=" * 64)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
