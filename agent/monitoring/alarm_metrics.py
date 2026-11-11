# -*- coding: utf-8 -*-
"""报警器埋点（2026-11-11）：把 monitoring/alerts.yml 引用的指标族接到**真实执行点**。

【为什么有这个模块 —— "规则存在 ≠ 指标存在"】
`monitoring/alerts.yml` 的 16 条规则里，有 10 条引用的是本部署**从来没有自增过**的指标族
（`/api/alarms/coverage` 里逐条写着 "无序列: ..."）：

| 指标族 | 规则 | 真实执行点（本模块的调用方） |
|---|---|---|
| `yunshu_llm_calls_total{provider,model,status}` | LLMCallSpike | `agent/llm_monitor.py` 的两个 `_patched_do_*` 钩子（每次真实 LLM 调用） |
| `yunshu_tool_call_total{tool_name,tool_category,success}` | ToolFailureRate | `plugins/chat.py::_record_tool_usage`（工作台对话的真实工具执行点） |
| `yunshu_security_blocks_total{rule,level,category}` | SecurityBlockCritical | `agent/tool_gate.py::_deny`（集中式闸门硬拒绝）/ `agent/guardrails/{egress,ssrf}_guard.block_result`（出站与 SSRF 拦截） |
| `yunshu_skill_hallucination_total{skill_id}` | SkillHallucination* | 既有：`agent/skills_mgmt/observability.emit_eval_score_metric`（幻觉判定为真时 +1） |
| `yunshu_skill_eval_score{skill_id,task_success,quantile}` | SkillEvalScoreLow | 既有：同上（技能端到端评估分数直方图） |

后两个族**本来就有真实写入点**，只是写作业务指标层（`agent/monitoring/business_metrics.py`），
采样环读不到；`agent/alarms/samples.snapshot_business_metrics()` 负责把它们并进采样面。
前三个族本模块补写：**只在真实调用发生时 +1**，不伪造"有数据"。

【三条纪律】
1. **失败绝不影响主链路**：只用 try/except 包住，异常只记 debug（埋点坏掉不该让对话/工具失败）。
2. **不重复注册指标族**：`yunshu_llm_calls_total` / `yunshu_security_blocks_total` 由
   `app_server.py` 在启动时注册；这里只找**已有的**族（`REGISTRY._names_to_collectors`）再自增。
   若族不存在（纯单测 / 未装 prometheus_client）⇒ 什么都不做 —— 新建同族会让
   `app_server` 的 `Counter(...)` 抛 `Duplicated timeseries`，服务起不来。
3. **标签取值来自真实参数**，不做任何猜测性归一（provider/model 原样，空则空）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: LLM 调用结果标签（与规则 `status="error"` 的取值一致）
LLM_STATUS_OK = "success"
LLM_STATUS_ERROR = "error"

#: 工具调用成功标签（业务指标层口径：`success="True"` / `"False"`，首字母大写 —— 规则里就这么写死）
TOOL_SUCCESS_TRUE = "True"
TOOL_SUCCESS_FALSE = "False"

#: 安全拦截等级标签（与 alert_rules.yml 的 `level="critical"` / `level="warning"` 同字面量）
LEVEL_CRITICAL = "critical"
LEVEL_WARNING = "warning"

#: 工具类别未知时的占位（不编类别，如实留空；规则只按 success 分组）
CATEGORY_UNKNOWN = ""


def _collector(name: str) -> Optional[Any]:
    """取**已注册**的指标族（不新建）。

    `Counter("x_total", ...)` 在注册表里的键有两三个（`x` / `x_total` / `x_created`），
    这里两种写法都试一次；取不到返回 None（调用方据此跳过，绝不自建）。
    """
    try:
        from prometheus_client import REGISTRY  # noqa: PLC0415 延迟 import：只有埋点路径需要
    except Exception:  # noqa: BLE001 prometheus_client 不可用 ⇒ 不埋点
        return None
    table = getattr(REGISTRY, "_names_to_collectors", None)
    if not isinstance(table, dict):
        return None
    base = name[:-6] if name.endswith("_total") else name
    for key in (name, base):
        found = table.get(key)
        if found is not None:
            return found
    return None


def record_llm_call(provider: str, model: str, ok: bool) -> None:
    """记一次真实 LLM 调用（成功/失败）—— 供 YunshuLLMCallSpike 规则求值。"""
    try:
        family = _collector("yunshu_llm_calls_total")
        if family is None:
            return
        family.labels(
            provider=str(provider or ""),
            model=str(model or ""),
            status=LLM_STATUS_OK if ok else LLM_STATUS_ERROR,
        ).inc()
    except Exception as exc:  # noqa: BLE001 埋点失败不得影响 LLM 主链路
        logger.debug("埋点：LLM 调用计数失败（%s）", type(exc).__name__)


def record_tool_call(tool_name: str, ok: bool, category: str = CATEGORY_UNKNOWN,
                     duration: Optional[float] = None) -> None:
    """记一次真实工具调用 —— 走业务指标层（`yunshu_tool_call_total`，规则按 success 分组）。

    【为什么写业务指标层而不是再造一个原生 counter】该族在
    `agent/monitoring/business_metrics.py` 已有定义（labels = tool_name / tool_category / success），
    `/api/business/prometheus` 也已经在暴露它；报警器的采样环通过
    `samples.snapshot_business_metrics()` 读同一份投影。再建一个同名原生 counter
    会变成两套数字（同一指标两个真值）—— 那正是这个仓库反复清理的坑。
    """
    try:
        from agent.monitoring.business_metrics import (  # noqa: PLC0415 延迟 import：避免启动期环
            record_tool_call as _record,
        )
        _record(str(tool_name or ""), str(category or CATEGORY_UNKNOWN), bool(ok),
                None if duration is None else float(duration))
    except Exception as exc:  # noqa: BLE001 埋点失败不得影响工具主链路
        logger.debug("埋点：工具调用计数失败（%s）", type(exc).__name__)


def record_security_block(rule: str, level: str = LEVEL_WARNING,
                          category: str = "") -> None:
    """记一次安全拦截（集中式闸门硬拒绝 / 出站与 SSRF 护栏拦截）。

    Args:
        rule: 拦截规则名（闸门的拒因 / 护栏名），进标签 `rule`
        level: `critical`（真正的安全事件：出站/SSRF/注入）或
               `warning`（治理边界：权限不足、工具被停用）
        category: 粗分类（`permission` / `network` / `injection` …），可空
    """
    try:
        family = _collector("yunshu_security_blocks_total")
        if family is None:
            return
        family.labels(rule=str(rule or ""), level=str(level or LEVEL_WARNING),
                      category=str(category or "")).inc()
    except Exception as exc:  # noqa: BLE001 埋点失败不得影响拦截判定
        logger.debug("埋点：安全拦截计数失败（%s）", type(exc).__name__)


__all__ = [
    "LLM_STATUS_OK", "LLM_STATUS_ERROR", "TOOL_SUCCESS_TRUE", "TOOL_SUCCESS_FALSE",
    "LEVEL_CRITICAL", "LEVEL_WARNING", "CATEGORY_UNKNOWN",
    "record_llm_call", "record_tool_call", "record_security_block",
]
