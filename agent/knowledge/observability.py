"""knowledge 模块结构化日志埋点。

遵循项目生产日志链路（agent/logging_utils.py 事实标准）：
- emit_structured_log 以 **dict 作为 msg** 传给 logger，不做本地序列化——
  由生产过滤器接管：文件 handler 的 DictToJsonFilter 单次序列化为单行 JSON，
  控制台 handler 的 StructuredLogFormatter 美化显示，
  SensitiveDataFilter / EmojiFilter 负责脱敏与编码兼容。
- 输出行顶层含 trace_id / module_name / action / duration_ms + 业务字段，
  Filebeat / Promtail 的 json stage 可直接解析顶层字段接入 ELK
  （与 agent/orchestrator 的 log_dict 模式同一链路）。

【不易】耗时统计日志统一走此出口（字段名与项目先例一致，不做自定义格式）；
埋点失败不影响主流程（调用方异常由日志系统自行兜底，此处不额外捕获）。
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger("agent.knowledge")

# 当前知识操作链路的 trace_id（ContextVar，线程/异步隔离）。
# 【变易】同一链路（一次 kb_* 工具调用 / WorkflowRunner 操作）内的所有
# emit_structured_log 共享该 trace_id，分布式场景下可在 ELK 按 trace_id
# 聚合整条链路日志（对齐 orchestrator trace_id_ctx 的传播机制）。
_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "knowledge_trace_id", default=""
)


@contextmanager
def knowledge_trace(trace_id: Optional[str] = None) -> Iterator[str]:
    """进入一条知识操作链路：设置当前上下文的 trace_id，退出时恢复。

    - 同一链路内的 emit_structured_log 共享 trace_id（链路追踪完整）。
    - ContextVar 天然隔离并发请求/线程，分布式多节点各自独立生成。
    - 支持显式传入 trace_id（外部编排系统透传，跨工具关联链路）。

    异常恢复（【不易】不变量，勿改动）：
    - 实现为 try/finally + ContextVar.reset(token)：yield 体内抛任意异常
      （含嵌套链路内层中断）都会在 finally 中精确恢复进入前的值，
      不会把 trace_id 泄露到链路外部——reset(token) 是栈式复位，
      嵌套时内层异常只回退到内层入口前的链路段。
    - 已由测试锁定：test_knowledge_trace_no_leak_on_exception（异常中断恢复）、
      test_knowledge_trace_nested_restore_on_exception（嵌套精确复位）、
      scripts/dev/verify_knowledge_pipeline.py 场景5（1000 并发 +
      143 次异常中断注入，全部恢复无泄露）。

    Args:
        trace_id: 显式链路 id（缺省自动生成 128 bit UUID4）。

    Yields:
        当前链路 trace_id。
    """
    tid = trace_id or uuid.uuid4().hex
    token = _CTX.set(tid)
    try:
        yield tid
    finally:
        _CTX.reset(token)


def get_trace_id() -> str:
    """当前链路的 trace_id（未在链路内时为空串，表示单条日志级）。"""
    return _CTX.get()


def _trace_id() -> str:
    """生成 trace_id（分布式唯一，完整 UUID4 128 bit）。

    项目其他模块先例为 uuid4().hex[:16]（64 bit）；本模块面向分布式多节点
    日志聚合场景，改用完整 128 bit：生日悖论下 N=10^10 条时冲突概率约 1e-19
    （64 bit 同量级约 97%），工程上可视为不冲突。
    """
    return uuid.uuid4().hex


def emit_structured_log(action: str, *, trace_id: Optional[str] = None,
                        duration_ms: float = 0.0, level: str = "info",
                        **payload: Any) -> None:
    """输出一行结构化日志（dict msg，由生产过滤器序列化/美化）。

    Args:
        action: 事件名，形如 <module>.<action>（如 distill.llm_ok）。
        trace_id: 显式 trace_id（缺省取当前链路 knowledge_trace 的 id，
            均无则自动生成 128 bit UUID4）。
        duration_ms: 耗时（毫秒），自动 round 到 2 位小数。
        level: info / warning / error。
        payload: 业务字段（slug/source/reason/error 等）。
    """
    record = {
        "trace_id": trace_id or _CTX.get() or uuid.uuid4().hex,
        "module_name": "knowledge",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        **payload,
    }
    getattr(logger, level, logger.info)(record)
