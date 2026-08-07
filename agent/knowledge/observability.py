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

import logging
import uuid
from typing import Any, Optional

logger = logging.getLogger("agent.knowledge")


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
        trace_id: 显式 trace_id（缺省自动生成 128 bit UUID4）。
        duration_ms: 耗时（毫秒），自动 round 到 2 位小数。
        level: info / warning / error。
        payload: 业务字段（slug/source/reason/error 等）。
    """
    record = {
        "trace_id": trace_id or _trace_id(),
        "module_name": "knowledge",
        "action": action,
        "duration_ms": round(duration_ms, 2),
        **payload,
    }
    getattr(logger, level, logger.info)(record)
