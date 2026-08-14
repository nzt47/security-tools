"""Orchestrator — 云枢主编排器

职责:
- P12 统一对话链路（chat → process → 感知→认知→行动→反思）
- LLM 调用与工具调用协调（_call_llm / _call_llm_v2）
- 结果聚合与反思记录
- 状态查询与多模态功能入口
- 4 套旧实现已合并为统一 process() 方法

依赖:
- LifecycleManager: 提供 _memory、_llm、_behavior 等已初始化组件
- DigitalLifePersonaMixin: 提供 _build_body_status、_build_tool_status_text 等
- DigitalLifeStateMixin: 提供状态持久化方法
"""

import asyncio
import logging
import threading
import time
import json
import os
import re as _re
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

# digital_life 符号延迟到文件末尾导入，避免与 digital_life.py:369 形成模块级循环导入
# (orchestrator.py 顶层导入 → digital_life.py:369 → agent.orchestrator.Orchestrator → orchestrator.py 未完成)

from agent.guardrails.input_guard import InputGuard, GuardAction
from agent.guardrails.output_guard import OutputGuard
from agent.observability.subscriber import trace_store, TraceSpan
from agent.orchestrator.message_handler import MessageHandler
from agent.orchestrator.response_builder import ResponseBuilder, Response
import uuid
from agent.logging_utils import log_dict

# 任务6: 主业务链路路由可观测性埋点（各层耗时 / 流量分布 / 路由决策）
from agent.orchestrator.routing_observability import (
    log_layer_result,
    emit_route_decision,
    RouteContext,
    LAYER_INPUT_GUARD, LAYER_WORKFLOW, LAYER_TEMPLATE, LAYER_SEMANTIC,
    LAYER_WORKFLOW_LEARNING,
    LAYER_LLM, LAYER_OUTPUT_GUARD, LAYER_REJECT, LAYER_BEHAVIOR,
    DECISION_HIT, DECISION_MISS, DECISION_BLOCK, DECISION_PASS,
    DECISION_MODIFIED, DECISION_SUCCESS, DECISION_FALLBACK, DECISION_ERROR,
    DECISION_REJECT,
)

# tool_calling 和 tool_router 无循环依赖，直接模块级导入（替代原 5 处方法体内延迟导入）
from agent.tool_calling import (
    ToolCallingService,
    _summarize_tool_result,
    _clean_for_json,
)
from agent.tool_router import get_tools_for_input
from agent.tool_router_hybrid import hybrid_select_tools

logger = logging.getLogger(__name__)


# ── TASK-03 学习度量辅助（埋点异常绝不影响主链路） ──────────────
_LEARNING_SAVED_EST: Optional[int] = None


def _get_learning_saved_estimate() -> int:
    """命中 workflow/skill 时估算被跳过的 LLM 调用 token（读 config.yaml llm.max_tokens）"""
    global _LEARNING_SAVED_EST
    if _LEARNING_SAVED_EST is None:
        try:
            from pathlib import Path
            import yaml as _yaml
            _cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            with open(_cfg_path, "r", encoding="utf-8") as _f:
                _data = _yaml.safe_load(_f) or {}
            _LEARNING_SAVED_EST = int((_data.get("llm") or {}).get("max_tokens") or 2000)
        except Exception:
            _LEARNING_SAVED_EST = 2000
    return _LEARNING_SAVED_EST


def _emit_learning_metric(fn_name: str, *args, **kwargs) -> None:
    """安全包装：学习度量单例异常/缺失时静默降级，主链路零影响"""
    try:
        from agent.learning_metrics import get_learning_metrics
        _fn = getattr(get_learning_metrics(), fn_name, None)
        if _fn is not None:
            _fn(*args, **kwargs)
    except Exception:
        pass


def _trace_id():
    """获取 trace_id，优先复用上下文，无则生成临时 ID（结构化日志用）"""
    try:
        _tid = get_trace_id()
        if _tid:
            return _tid
    except Exception:
        pass
    return uuid.uuid4().hex[:16]


def _run_async_in_sync(coro, timeout: Optional[float] = None):
    """同步桥：在同步上下文（Orchestrator.process）运行规划引擎 async 调用（D7 接入）

    - 无运行中事件循环（典型同步部署）→ asyncio.run 直接执行；
    - 有运行中事件循环（async web 框架内）→ 独立线程运行新循环，避免嵌套运行冲突。
    - timeout 非 None 时经 asyncio.wait_for 硬超时（逃生通道超时依据）。

    Returns:
        coroutine 的返回值；异常/超时向上传播，由调用方决定回退路径。
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # 无运行中事件循环：直接 run
        # 【排查】同步桥模式记录：asyncio_run（无事件循环）——超时语义：wait_for 对
        #         同步快路径（协程无 await 挂起点）无法中途插入超时，仅对挂起协程生效
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.async_bridge.mode', 'message': '[异步桥] 无运行中事件循环 → asyncio.run（timeout=%s）' % (timeout,), 'mode': 'asyncio_run', 'timeout_seconds': timeout}))
        return asyncio.run(
            asyncio.wait_for(coro, timeout) if timeout is not None else coro
        )
    # 有运行中事件循环：线程池隔离新循环（wait_for 已在协程内控制超时）
    import concurrent.futures
    # 【排查】同步桥模式记录：thread_pool（async web 框架内）——超时由新循环 wait_for 生效
    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.async_bridge.mode', 'message': '[异步桥] 有运行中事件循环 → 线程池新循环（timeout=%s）' % (timeout,), 'mode': 'thread_pool', 'timeout_seconds': timeout}))
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as _pool:
        _fut = _pool.submit(
            lambda: asyncio.run(
                asyncio.wait_for(coro, timeout) if timeout is not None else coro
            )
        )
        return _fut.result()


def _record_intent_layer(layer: str) -> None:
    """记录意图识别各层命中（延迟导入 prometheus 避免循环依赖）

    【不易】统一入口：5 个命中点（rule/template/semantic/llm/reject）均经此函数，
           在此打印诊断日志可全覆盖埋点是否触发，避免逐点加日志的冗余
    【简易】埋点失败隔离：异常降级为 WARNING 日志，不向上传播

    Args:
        layer: rule / template / semantic / llm / reject
    """
    _tid = _trace_id()
    try:
        from agent.monitoring.prometheus import record_intent_layer as _rec
        _rec(layer)
        # [埋点诊断] 成功记录：layer + trace_id，便于日志检索"埋点是否触发"
        logger.info(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.intent_layer.metric_recorded',
            'trace_id_ctx': _tid,
            'message': '[埋点] intent_layer 指标已记录: layer=%s' % layer,
            'layer': layer,
            'metric': 'yunshu_intent_layer_total',
        }))
    except Exception as _e:
        # [埋点诊断] 失败：记录失败原因，便于排查埋点漏掉（指标丢失）
        logger.warning(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.intent_layer.metric_failed',
            'trace_id_ctx': _tid,
            'message': '[埋点] intent_layer 指标记录失败: layer=%s err=%s' % (layer, _e),
            'layer': layer,
            'error': str(_e),
        }))


# ════════════════════════════════════════════════════════════════════
#  拒识/兜底文案常量 + LLM 置信度判定（模块级，供测试 import 消除同源复制）
#  【不易】测试侧须 import 此处定义，禁止在测试中重新复制（漂移风险）
# ════════════════════════════════════════════════════════════════════

_REJECT_MSG = (
    "抱歉，我不太理解你的意思。能否详细描述一下你想做什么？"
    "如需人工帮助，请说「转人工」。"
)

_FALLBACK_MSG = (
    "抱歉，我暂时无法给出令人满意的回答。"
    "请尝试换种方式描述你的问题，或说「转人工」由人工协助处理。"
)

# 错误标记清单（LLM 响应含此标记 → 低置信度）
_LLM_ERROR_MARKERS = ("抱歉，处理", "遇到了问题", "无法完成", "出错了")


def _judge_llm_confidence(response: Optional[str]) -> Tuple[str, str]:
    """LLM 置信度启发式判定 — 基于响应质量

    【简易】空/过短/错误标记 → low；正常响应 → high
    【变易】未来可扩展为 LLM 自评 confidence 字段或工具调用成功率后验

    Args:
        response: LLM 响应文本（可能为 None 或空字符串）

    Returns:
        (confidence, low_reason): confidence="high"|"low",
        low_reason="normal"|"empty_or_too_short"|"error_marker_detected"
    """
    confidence = "high"
    low_reason = "normal"
    if not response or len(response.strip()) < 5:
        confidence = "low"
        low_reason = "empty_or_too_short"
    elif any(_marker in response for _marker in _LLM_ERROR_MARKERS):
        confidence = "low"
        low_reason = "error_marker_detected"
    return confidence, low_reason


# ════════════════════════════════════════════════════════════════════
#  TASK-01：规划接线（wire）复杂度分级 — 轻量关键词+动作词加权
#  【不易】评分逻辑复用 PlanningCore._needs_planning() 现成公式
#          （complex_count + 0.5×action_count），分级语义对齐 enhanced_planner
#          （TRIVIAL/SIMPLE/NORMAL/MODERATE/COMPLEX）；
#          wire_enabled=false（默认）时本分级不参与任何决策（接线分支整体 inert）。
#  【变易】后续可替换为 model_router.analyze_complexity / enhanced_planner 分级。
# ════════════════════════════════════════════════════════════════════
_WIRE_COMPLEXITY_LEVELS: Dict[str, int] = {
    "TRIVIAL": 0,
    "SIMPLE": 1,
    "NORMAL": 2,
    "MODERATE": 2,  # enhanced_planner 用 MODERATE，兼容别名
    "COMPLEX": 3,
}
_WIRE_COMPLEX_KEYWORDS: Tuple[str, ...] = (
    "架构", "系统", "平台", "重构", "迁移", "分布式",
    "设计一个", "帮我构建", "多步骤", "第一步", "第二步", "完整方案",
)
_WIRE_ACTION_KEYWORDS: Tuple[str, ...] = ("检查", "分析", "创建", "生成", "整理", "监控")


def _wire_complexity_detail(message: str) -> Tuple[float, list, list]:
    """复杂度判定明细（TASK-01 wire 排查用）：返回 (score, complex_matches, action_matches)

    【简易】与 _judge_wire_complexity 同源公式（complex_count + 0.5×action_count），
           仅附加命中关键词明细，供入口日志定位"为什么判为 X 级"；不改判定语义
    """
    complex_matches = [k for k in _WIRE_COMPLEX_KEYWORDS if k in message]
    action_matches = [k for k in _WIRE_ACTION_KEYWORDS if k in message]
    score = len(complex_matches) + len(action_matches) * 0.5
    return score, complex_matches, action_matches


def _judge_wire_complexity(message: str) -> str:
    """任务复杂度分级（TASK-01 wire 接线用）

    【简易】分数 = 复杂指示词数 + 0.5×动作词数（与 PlanningCore._needs_planning 同源）；
    分级：≥1.5 → COMPLEX / ≥1.0 → NORMAL / ≥0.5 → SIMPLE / 其余 TRIVIAL
    """
    score, _complex_matches, _action_matches = _wire_complexity_detail(message)
    if score >= 1.5:
        return "COMPLEX"
    if score >= 1.0:
        return "NORMAL"
    if score >= 0.5:
        return "SIMPLE"
    return "TRIVIAL"


def _wire_complexity_meets(message: str, min_complexity: str) -> bool:
    """任务复杂度 ≥ wire_min_complexity 判定

    【不易】未知级别按最高 COMPLEX（3）保守处理：配置非法时收严而非放行，守主链路稳定
    """
    min_level = _WIRE_COMPLEXITY_LEVELS.get(str(min_complexity).upper(), 3)
    return _WIRE_COMPLEXITY_LEVELS.get(_judge_wire_complexity(message), 0) >= min_level


class Orchestrator:
    """云枢主编排器

    协调完整的"感知 → 认知 → 行动 → 反思"闭环。
    依赖宿主类提供以下属性（由 LifecycleManager.__init__ 设置）:
    - _memory, _llm, _llm_pro, _behavior, _permission
    - _tool_calling_service, _model_router
    - _v2_lifetrace, _v2_persona, _v2_distillation
    - body, _injector

    Guardrails 安全护栏:
    - _input_guard: 输入护栏——检测提示词注入
    - _output_guard: 输出护栏——PII 遮盖
    """

    # ════════════════════════════════════════════════════════════════════
    #  Guardrails 安全护栏（懒加载属性）
    # ════════════════════════════════════════════════════════════════════

    @property
    def _input_guard(self) -> InputGuard:
        """输入护栏——懒加载"""
        attr = '_guardrails_input_guard'
        if not hasattr(self, attr):
            setattr(self, attr, InputGuard())
        return getattr(self, attr)

    @property
    def _output_guard(self) -> OutputGuard:
        """输出护栏——懒加载"""
        attr = '_guardrails_output_guard'
        if not hasattr(self, attr):
            setattr(self, attr, OutputGuard())
        return getattr(self, attr)

    @property
    def _output_validator(self):
        """输出验证门控（TASK-07）——懒加载；构建失败返回 None 主链路零影响"""
        attr = '_guardrails_output_validator'
        if hasattr(self, attr):
            return getattr(self, attr)
        try:
            from agent.verification.output_validator import (
                build_validator_from_config,
            )
            validator = build_validator_from_config()
        except Exception as e:  # noqa: BLE001 门控构建失败绝不阻断主链路
            logger.warning("[OutputValidator] 构建失败，门控降级跳过: %s", e)
            validator = None
        setattr(self, attr, validator)
        return validator

    # ════════════════════════════════════════════════════════════════════
    #  核心闭环：感知 → 认知 → 行动 → 反思
    # ════════════════════════════════════════════════════════════════════

    def chat(self, user_input: str, *, session_id: Optional[str] = None, session_mgr=None) -> str:
        """与云枢对话——P12 统一入口

        这是与云枢交互的唯一外部入口。
        内部统一由 process() 处理完整的感知-认知-行动-反思闭环。

        Args:
            user_input: 用户说给云枢的话
            session_id: 会话元数据所属的会话 ID（sess_*，与 SessionManager 对齐）。
                显式传入以根治多用户并发时的全局状态覆盖（不再依赖实例全局 _session_id）
            session_mgr: SessionManager 实例，用于读取会话元数据（时区/设备/语言）

        Returns:
            云枢的回复文本
        """
        # 【审计改进】参数传递埋点：记录 chat 入口收到的显式会话上下文，
        # 与 process.session_ctx / user_context.source 串联，便于排查参数传递是否正确
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.chat.session_ctx', 'session_id': session_id or getattr(self, '_session_id', 'default'), 'session_mgr_present': session_mgr is not None, 'message': '[chat] 显式传参: session_id=%s, session_mgr=%s' % (session_id or '(未传，回退实例)', '已传入' if session_mgr is not None else '未传入(回退实例)')}))
        _autonomy_level = resolve_autonomy_level(
            session_id=session_id or getattr(self, "_session_id", None))
        with AutonomyContext(_autonomy_level):
            result = self.process(
                user_input,
                session_id=session_id,
                session_mgr=session_mgr,
            )
        if isinstance(result, dict):
            text = result.get("response", "")
            if not text:
                data = result.get("data", "")
                if isinstance(data, str):
                    text = data
                elif isinstance(data, dict):
                    # workflow_result: data.output, llm_result: data.text
                    text = data.get("output", "") or data.get("text", "")
            return text
        return str(result)

    def process(self, user_input: str, **kwargs) -> dict:
        """P12 统一对话处理链路

        整合之前 4 套 chat 实现（_chat_impl / _chat_v2 / _chat_with_planning / _process_user_input）
        为一条统一链路：InputGuard → WorkflowEngine → 感知+行为能力 → 意图路由+模板 → LLM → OutputGuard → 反思 → 记忆

        Args:
            user_input: 用户输入
            **kwargs: 扩展参数（planning_mode, body_status 等）

        Returns:
            标准响应字典 {"success": bool, "response": str, "error": str, "metadata": dict}
        """
        trace_id = get_trace_id() if _MONITORING_AVAILABLE else None
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '=' * 70}))
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.receive', 'trace_id_ctx': trace_id, 'message': '[Orchestrator.process] 收到对话请求'}))
        input_preview = user_input[:100] + ("..." if len(user_input) > 100 else "")
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '   用户输入: %s' % (input_preview,)}))
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '   对话次数: %d' % (self._interaction_count + 1,)}))
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '=' * 70}))

        if not self._running:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '云枢未运行，返回提示'}))
            return ResponseBuilder.success(
                "我还没有被唤醒。请先调用 start() 让我醒来。"
            ).to_dict()

        # _interaction_count 为「读-改-写」序列（非原子），多线程并发 process()
        # 会丢更新（轮次计数失真 + trace interaction_id 重复）。锁内仅内存整数
        # 递增，无 I/O（持锁纪律）；锁由宿主 LifecycleManager/V2 optimized_init
        # 在 _interaction_count 初始化时同批创建（_interaction_lock）。
        with self._interaction_lock:
            self._interaction_count += 1

        # TASK-03: 学习度量——交互总数（埋点异常不影响主链路）
        _emit_learning_metric("record_interaction")

        # 会话 ID：kwargs 显式传参优先，回退实例全局 _session_id（并发安全）
        # 修复：重构时 _sid 定义行丢失，仅剩 8 处引用（get_dialog_state/_learn_workflow 等），
        #       导致 CI Shard 1/6、6/6 NameError: name '_sid' is not defined
        _sid = kwargs.get("session_id") or getattr(self, "_session_id", None)

        # ── 路由可观测性: 初始化单次请求上下文（累积各层中间结果）──
        # 任务6: 所有 log_layer_result / emit_route_decision 共享此上下文
        RouteContext.init(trace_id)

        # ── Trace: 开始记录 ──
        if trace_id:
            trace_store.start_trace(trace_id, user_input)

        if _MONITORING_AVAILABLE:
            collector = get_metrics_collector()
            collector.increment_counter("count.digital_life.chat.total")
            collector.increment_counter("count.digital_life.interaction.total")

        # 统一检查上下文使用率
        self._last_context_warning = self._check_context_usage()
        if self._last_context_warning and self._last_context_warning["level"] != "info":
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '[上下文] %s（%.1f%%）' % (self._last_context_warning['message'], self._last_context_warning['pct'])}))

        # ── 第零步：InputGuard 输入安全检查 ──
        _ts_guard = time.perf_counter()
        guard_result = self._input_guard.check(user_input)
        _dur_guard = (time.perf_counter() - _ts_guard) * 1000
        if guard_result.action == GuardAction.BLOCK:
            # 任务6: 统一层日志（layer/decision/duration_ms）+ 最终路由决策
            log_layer_result(
                LAYER_INPUT_GUARD, DECISION_BLOCK, trace_id,
                level=logging.WARNING,
                action='orchestrator.process.guard',
                message='[Guard] ⛔ 输入被 InputGuard 拦截: %s（匹配: %s）' % (
                    guard_result.reason, guard_result.matched_pattern),
                duration_ms=_dur_guard,
                reason=guard_result.reason,
                matched_pattern=guard_result.matched_pattern,
            )
            emit_route_decision(LAYER_INPUT_GUARD, DECISION_BLOCK, trace_id,
                                message='[输入护栏] 输入被拦截')
            if trace_id:
                trace_store.end_trace(trace_id, guard_result.reason, status="blocked")
            return ResponseBuilder.guard_blocked(
                guard_result.reason, guard_result.matched_pattern
            ).to_dict()
        else:
            # 未命中（中间结果）→ DEBUG
            log_layer_result(
                LAYER_INPUT_GUARD, DECISION_PASS, trace_id,
                level=logging.DEBUG,
                action='orchestrator.process.guard',
                message='[Guard] 输入检查通过',
                duration_ms=_dur_guard,
            )

        # ── 第一步：Workflow Engine 匹配（0 Token 消耗）──
        # 【变易】耗时用 perf_counter 配对计时; TraceSpan 时间戳单独用墙上时钟（time.time）
        _ts_wf_wall = time.time()            # span 绝对时间戳
        ts_wf = time.perf_counter()          # 耗时统计
        workflow_result = self._workflow_engine.try_match(user_input)
        _dur_wf = (time.perf_counter() - ts_wf) * 1000
        if workflow_result is not None and workflow_result.matched:
            # 任务6: 统一层日志（决策点 INFO）
            log_layer_result(
                LAYER_WORKFLOW, DECISION_HIT, trace_id,
                action='orchestrator.process.workflow',
                message='[Workflow] 命中规则: %s, 置信度=%.2f, 耗时=%.2fms' % (
                    workflow_result.intent, workflow_result.confidence,
                    workflow_result.execution_time_ms),
                duration_ms=_dur_wf,
                score=workflow_result.confidence,
                intent=workflow_result.intent,
                confidence=workflow_result.confidence,
                execution_time_ms=workflow_result.execution_time_ms,
            )
            emit_route_decision(LAYER_WORKFLOW, DECISION_HIT, trace_id,
                                message='[规则层] 命中 workflow 规则',
                                basis_extra={'intent': workflow_result.intent,
                                             'confidence': workflow_result.confidence})
            self._memory.score_and_save_message("user", user_input)
            self._memory.score_and_save_message("assistant", workflow_result.output)
            if trace_id:
                trace_store.end_trace(trace_id, workflow_result.output)
            _record_intent_layer("rule")
            return ResponseBuilder.workflow_result(
                output=workflow_result.output,
                intent=workflow_result.intent,
                confidence=workflow_result.confidence,
            ).to_dict()
        # 未命中（中间结果）→ DEBUG
        log_layer_result(
            LAYER_WORKFLOW, DECISION_MISS, trace_id,
            level=logging.DEBUG,
            action='orchestrator.process.workflow',
            message='[Workflow] 未命中规则',
            duration_ms=_dur_wf,
        )
        if trace_id:
            trace_store.add_span(trace_id, TraceSpan(
                span_id=f"{trace_id}_workflow",
                operation="workflow_match",
                start_time=_ts_wf_wall, end_time=time.time(),
                duration_ms=_dur_wf,
                status="no_match",
            ))

        # ── 第二步：感知 + 行为能力检查 ──
        readings = self.check_health()
        body_status = self._build_body_status(readings)

        # V2: LifeTrace 记录用户输入
        if self._v2_lifetrace and self._trace_recorder:
            timestamp = datetime.now(timezone.utc).isoformat()
            self._trace_recorder.record_chat(
                role="user", content=user_input,
                metadata={"interaction_id": self._interaction_count, "timestamp": timestamp},
            )

        # V2: 人格蒸馏增量更新
        if self._v2_distillation and self._persona_extractor:
            ts = datetime.now(timezone.utc).isoformat()
            self._persona_extractor.update_incremental({
                "role": "user", "content": user_input, "timestamp": ts,
            })

        # 行为能力 + Persona 双重拒绝检查
        can_execute, reject_reason = self._behavior.can_execute(user_input)
        if self._v2_persona and self._persona_injector:
            persona_reject, persona_reason = self._persona_injector.should_refuse_task(user_input)
            if persona_reject and not can_execute:
                reject_reason = f"{reject_reason}；{persona_reason}"
            elif persona_reject:
                can_execute, reject_reason = False, persona_reason

        if not can_execute:
            response = self._build_reject_response(reject_reason, readings)
            self._memory.save_log("task_rejected", {
                "reason": reject_reason,
                "mode": self._current_mode.value,
                "input_preview": user_input[:100],
            })
            if self._v2_lifetrace and self._trace_recorder:
                self._trace_recorder.record_chat(
                    role="assistant", content=response,
                    metadata={"rejected": True, "reason": reject_reason},
                )
            # 任务6: 最终路由决策（行为能力拒绝）
            emit_route_decision(LAYER_BEHAVIOR, DECISION_REJECT, trace_id,
                                message='[行为能力] 任务被拒绝',
                                basis_extra={'reason': (reject_reason or "")[:200]})
            if trace_id:
                trace_store.end_trace(trace_id, response)
            return ResponseBuilder.rejection(
                reject_reason, self._current_mode.value
            ).to_dict()

        # ── 第二步半：DST 指代消解（省略句补全）──
        # 【变易】从上一轮对话状态继承关键词/意图，将"那个呢"/"然后呢"等
        #         省略句补全为完整查询，使前序规则层和语义层能正确匹配。
        #         补全后的输入仅用于路由决策，LLM 仍用原始输入。
        # 【变易】向量置信度：若 SkillLoader 的 vector_adapter 已"热"（语义层跑过），
        #         注入 DST 做软门控；未热则走纯正则（不强制拉起模型，守性能）。
        routing_input = user_input
        try:
            from agent.orchestrator.dialog_state import get_dialog_state
            _dst = get_dialog_state(_sid)
            # 注入已初始化的 vector_adapter（仅当热，避免冷启动延迟）
            try:
                from agent.state_manager import get_skills_mgmt_service
                _svc = get_skills_mgmt_service()
                if _svc and _svc.loader and getattr(_svc.loader, "_vector_adapter", None) is not None:
                    _dst.vector_adapter = _svc.loader._vector_adapter
            except Exception:
                pass
            _augmented = _dst.resolve(user_input)
            _is_ellipsis = _dst.is_ellipsis_query(user_input)
            _sim = getattr(_dst, 'last_similarity', None)
            _sim_msg = ('%.4f' % _sim) if isinstance(_sim, float) else 'N/A'
            if _augmented:
                # 【不易】意图路由前详细日志：记录 DST 补全前后输入 + 向量相似度
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.dst', 'trace_id_ctx': trace_id,
                    'message': '[DST] 省略句补全: "%s" → "%s" (sim=%s, turn=%d)' % (user_input, _augmented, _sim_msg, _dst.turn_count),
                    'original_input': user_input,
                    'augmented_input': _augmented,
                    'similarity': _sim,
                    'turn': _dst.turn_count,
                    'result': 'augmented'}))
                routing_input = _augmented
            elif _is_ellipsis:
                # 省略句但被门控拒绝/无上下文 — 记录便于排查为何未补全
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.dst', 'trace_id_ctx': trace_id,
                    'message': '[DST] 省略句未补全: "%s" (sim=%s, turn=%d, 用原始输入路由)' % (user_input, _sim_msg, _dst.turn_count),
                    'original_input': user_input,
                    'augmented_input': None,
                    'similarity': _sim,
                    'turn': _dst.turn_count,
                    'result': 'rejected_or_no_context'}))
            # 状态回写（intent/skill/keywords/user_input）统一由 _update_dst_after_route
            # 在路由后处理，此处不再直接写 last_keywords（消除笨拙直写）
        except Exception as _dst_e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.dst.error', 'message': '[DST] 补全失败，用原始输入: %s' % (_dst_e,)}))

        # ── 第三步：意图路由 + 模板匹配（零 LLM 消耗）──
        # D7 前置：planning_mode（显式 planning_mode=True 或自动复杂任务判定）时，
        # 跳过模板直答/拒识提前 return，强制下沉规划引擎（模板命中会提前 return，
        # 否则显式规划入口在"你好"类低复杂度输入上不可达；拒识同理，复杂任务不拒识）
        _planning_mode = bool(kwargs.get("planning_mode", False)) or \
            (self._planning_enabled and self._planner and self._needs_planning(user_input))
        try:
            from agent.response_workflows import (
                IntentRouter, ResponseTemplates, Confidence,
            )
            intent, confidence = IntentRouter.classify(routing_input)
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '[路由] 意图=%s, 置信度=%s (routing_input="%s")' % (intent, confidence, routing_input[:40])}))

            # 【不易】路由后回写 DST 状态（intent/user_input/keywords），
            # 供下一轮指代消解继承；原注释承诺的 _update_dst_after_route 落地
            # 【变易】省略句（routing_input != user_input，即本轮已发生 DST 补全）
            #         仅回写 intent，keywords/user_input 传 None 保留上一轮真实查询，
            #         守"省略句不得覆盖上一轮真实查询关键词"（与 funnel 回归对齐，
            #         修复连续省略句互相污染：'那个呢'→'然后呢' 的级联问题）
            if routing_input != user_input:
                self._update_dst_after_route(intent, None, None, session_id=_sid)
            else:
                self._update_dst_after_route(intent, None, user_input, session_id=_sid)

            is_follow_up = MessageHandler.is_follow_up({
                'text': user_input,
                'last_was_template': getattr(self, '_last_was_template', False),
                'confidence': confidence,
                'session_id': _sid,
            })
            dissatisfaction = MessageHandler.detect_dissatisfaction(user_input)
            # 【排查】追问/不满判定依据（DEBUG 输出判定输入与结果，便于排查
            # "为什么没走模板层/为什么走了模板层"的中间结果异常）
            logger.debug(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator.process.template.decision',
                'trace_id_ctx': trace_id,
                'message': '[模板层] 判定依据: is_follow_up=%s dissatisfaction=%s last_was_template=%s confidence=%s' % (
                    is_follow_up, dissatisfaction, getattr(self, '_last_was_template', False),
                    getattr(confidence, 'name', str(confidence))),
                'is_follow_up': is_follow_up,
                'dissatisfaction': dissatisfaction,
                'last_was_template': getattr(self, '_last_was_template', False),
                'confidence': getattr(confidence, 'name', str(confidence)),
            }))
            if dissatisfaction:
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.llm', 'message': '[路由] 检测到用户不满/纠正，降级到 LLM'}))
                is_follow_up = True
            if is_follow_up:
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.llm', 'message': '[路由] 检测到模板后追问，降级到 LLM'}))
                self._last_was_template = False

            if not is_follow_up and not _planning_mode:
                template_response = ResponseTemplates.for_intent(
                    intent, confidence=confidence,
                    hour=datetime.now().hour,
                )
                if template_response:
                    # 任务6: 统一层日志（模板层命中, 决策点 INFO）
                    log_layer_result(
                        LAYER_TEMPLATE, DECISION_HIT, trace_id,
                        action='orchestrator.process.llm',
                        message='[路由] 使用本地模板，跳过 LLM 调用 (intent=%s, confidence=%s)' % (
                            intent, confidence.name if hasattr(confidence, 'name') else confidence),
                        duration_ms=0.0,
                        score=getattr(confidence, 'value', None),
                        intent=intent,
                        confidence=confidence.name if hasattr(confidence, 'name') else str(confidence),
                    )
                    emit_route_decision(
                        LAYER_TEMPLATE, DECISION_HIT, trace_id,
                        message='[模板层] 模板命中跳过 LLM',
                        basis_extra={
                            'intent': intent,
                            'confidence': confidence.name if hasattr(confidence, 'name') else str(confidence),
                        },
                    )
                    self._set_thinking_mode("instinct")
                    response = template_response
                    self._last_was_template = True
                    self._last_context_warning = None
                    self._memory.score_and_save_message("user", user_input)
                    self._memory.score_and_save_message("assistant", response)
                    try:
                        self._memory.infer_working_memory(user_input, response)
                    except Exception:
                        pass
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '[路由] 模板回复完成 (#%d)' % (self._interaction_count,)}))
                    if trace_id:
                        _ts_tpl_wall = time.time()  # 模板 span 时间戳（墙上时钟, 与耗时计时不混用）
                        trace_store.add_span(trace_id, TraceSpan(
                            span_id=f"{trace_id}_template",
                            operation="template_reply",
                            start_time=_ts_tpl_wall,
                            end_time=time.time(),
                            duration_ms=0.0,
                            status="success",
                            metadata={"intent": intent,
                                      "confidence": confidence.name},
                        ))
                        trace_store.end_trace(trace_id, response)
                    _record_intent_layer("template")
                    return ResponseBuilder.success(response).to_dict()
                # 【排查】模板层查表未命中原因（DEBUG 记录 intent 查表结果与下沉方向，
                # 便于排查"为何没走模板层"——是意图未知，还是模板库无对应意图）
                logger.debug(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator.process.template.miss',
                    'trace_id_ctx': trace_id,
                    'message': '[模板层] 未命中: intent=%s confidence=%s（for_intent 返回 None, 继续下沉 工作流学习层→语义层）' % (
                        intent, getattr(confidence, 'name', str(confidence))),
                    'intent': intent,
                    'confidence': getattr(confidence, 'name', str(confidence)),
                    'follow_up': is_follow_up,
                    'routing_input': (routing_input or '')[:80],
                }))
        except ImportError as _ie:
            # 【不易】不再静默吞错：response_workflows 缺失意味着模板语义层失效，
            # 所有意图将直落 LLM，违反三层漏斗架构。输出 WARNING 告警便于排查。
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.route.import_error', 'message': '[路由] response_workflows 导入失败，模板语义层失效，全部降级到 LLM: %s' % (_ie,)}))
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.llm', 'message': '[路由] 路由失败，降级到 LLM: %s' % (e,)}))
        self._last_was_template = False

        # ── 第三步零：工作流学习层匹配（自动闭环 v1，0 Token 短路）──
        # 【变易】本地优先：模板层未命中后、语义层之前，尝试命中并执行 learned workflow。
        #         命中且成功 → 短路返回（跳过 LLM，0 Token）；未命中/失败/异常 → 降级语义层。
        #         用 DST 补全后的 routing_input 匹配（省略句需补全才能命中 TF-IDF 索引）。
        #         【不易】工作流执行是真实工具调用（有副作用），置于行为能力检查之后，
        #         可被 persona/行为拒绝先行拦截；任何异常不影响主链路。
        _wfl_cfg_ = self._load_workflow_learning_layer_config()
        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.enter', 'trace_id_ctx': trace_id,
            'message': '[工作流层] 进入拦截: enabled=%s min_score=%.2f input=%r' % (
                _wfl_cfg_['enabled'], float(_wfl_cfg_['min_score']), (routing_input or '')[:60])}))
        wf_learning_result = self._workflow_learning_layer_match(routing_input, trace_id)

        # 任务6: 统一降级路径日志（命中与否，本层都要明确退出方向，便于链路追踪）
        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.exit', 'trace_id_ctx': trace_id,
            'message': '[工作流层] 退出: hit=%s wf=%s elapsed_ms=%s → 后续路径=%s' % (
                bool(wf_learning_result is not None),
                (wf_learning_result or {}).get('workflow_id', '-'),
                (wf_learning_result or {}).get('elapsed_ms', '-'),
                '短路返回(跳过LLM)' if wf_learning_result is not None else '降级→语义层(SkillLoader)' )}))

        if wf_learning_result is not None:
            output_text = wf_learning_result["output"]
            # 任务6: 统一路由决策（命中已在 _workflow_learning_layer_match 内记录层日志）
            emit_route_decision(
                LAYER_WORKFLOW_LEARNING, DECISION_HIT, trace_id,
                message='[工作流层] 命中短路返回: wf=%s score=%.3f' % (
                    wf_learning_result['workflow_id'], wf_learning_result['score']),
                basis_extra={
                    'workflow_id': wf_learning_result['workflow_id'],
                    'workflow_name': wf_learning_result['workflow_name'],
                    'score': wf_learning_result['score'],
                    'confidence': wf_learning_result['confidence'],
                    'steps_executed': wf_learning_result['steps_executed'],
                    'skipped_llm': wf_learning_result['skipped_llm'],
                },
            )

            # 记忆保存（与 WorkflowEngine/模板层/语义层命中分支保持一致）
            self._memory.score_and_save_message("user", user_input)
            self._memory.score_and_save_message("assistant", output_text)
            self._last_was_template = False
            if trace_id:
                trace_store.end_trace(trace_id, output_text)
            _record_intent_layer("workflow_learning")
            return ResponseBuilder.success(
                output_text, msg="handled_by_workflow_learning"
            ).to_dict()

        # ── 第三步半：语义层匹配（SkillLoader RRF 三路融合）──
        # 【变易】语义层接入：规则层(WorkflowEngine)+模板层(IntentRouter)未命中后，
        #         调用 SkillLoader.match 做向量+BM25+TF-IDF 三路 RRF 融合召回。
        #         命中高置信度技能时加载其 instruction（Layer 2）短路返回
        #         （与 WorkflowEngine.output 契约对称，无副作用）；
        #         未命中/异常降级到 LLM（守【不易】主链路稳定性）。
        semantic_result = self._semantic_layer_match(user_input, trace_id)

        if semantic_result is not None:
            # 语义层命中：短路返回技能 instruction
            output_text = semantic_result["output"]
            # 任务6: 统一层日志（语义层命中已在 _semantic_layer_match 内记录,
            #          此处仅输出最终路由决策 + 决策依据）
            emit_route_decision(
                LAYER_SEMANTIC, DECISION_HIT, trace_id,
                message='[语义层] 命中短路返回: skill=%s score=%.3f method=%s' % (
                    semantic_result['skill_id'], semantic_result['score'],
                    semantic_result['retrieval_method']),
                basis_extra={
                    'skill_id': semantic_result['skill_id'],
                    'score': semantic_result['score'],
                    'retrieval_method': semantic_result['retrieval_method'],
                    'reranked': semantic_result.get('reranked', False),
                    'fallback_used': semantic_result.get('fallback_used', False),
                },
            )

            # 【不易】语义层命中时回写 skill 到 DST（供下一轮"技能继承"分支）
            # 直接 set last_skill，避免再次调用 _update_dst_after_route(update) 导致
            # turn_count 重复递增；intent/user_input/keywords 已在路由后回写过
            try:
                from agent.orchestrator.dialog_state import get_dialog_state
                _dst_sk = get_dialog_state(_sid)
                _dst_sk.last_skill = semantic_result["skill_id"]
            except Exception:
                pass

            # 记忆保存（与 WorkflowEngine/模板层命中分支保持一致）
            self._memory.score_and_save_message("user", user_input)
            self._memory.score_and_save_message("assistant", output_text)
            self._last_was_template = False
            if trace_id:
                trace_store.end_trace(trace_id, output_text)
            return ResponseBuilder.success(
                output_text, msg="handled_by_semantic_layer"
            ).to_dict()

        # ── 第三步三：未知意图拒识检查（任务3）──
        # 【不易】拒识条件：(a) 输入过短 OR (b) 规则层+语义层双未命中且语义最高分 < 阈值
        # 【变易】阈值通过 ORCHESTRATOR_REJECT_THRESHOLD 配置（默认 0.3，与 min_score 解耦）
        # 【简易】软拒识——返回统一文案 + 转人工建议，不抛异常，记录 reject 指标
        import os as _os_reject
        _reject_cfg = self._load_reject_config()
        _reject_min_len = int(_os_reject.environ.get("ORCHESTRATOR_REJECT_MIN_LENGTH", "3"))
        _is_ellipsis = (routing_input != user_input)  # DST 补全过说明是指代句

        # (a) 长度拒识（保留现有逻辑：输入过短且非指代句）
        _len_reject = (not _is_ellipsis and len(user_input.strip()) < _reject_min_len)

        # (b) 语义+规则双未命中拒识（新增：基于 _should_reject 隐式判定）
        _semantic_reject, _reject_reason = self._should_reject(intent, confidence, semantic_result)
        # 指代句不拒识（DST 已补全，说明有上下文继承，不应判为未知意图）
        if _is_ellipsis:
            _semantic_reject = False

        # 【排查】拒识判定中间结果（DEBUG 记录判定输入/输出，便于排查
        # "为何没拒识/为何拒识"——intent、各层分数、阈值都在此处可见）
        logger.debug(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.process.reject.decision',
            'trace_id_ctx': trace_id,
            'message': '[拒识] 判定: len_reject=%s semantic_reject=%s reason=%r (intent=%s confidence=%s is_ellipsis=%s) → %s' % (
                _len_reject, _semantic_reject, (_reject_reason or '')[:80],
                intent, getattr(confidence, 'name', str(confidence)), _is_ellipsis,
                '拒识' if (_len_reject or _semantic_reject) else '放行→LLM'),
            'len_reject': _len_reject,
            'semantic_reject': _semantic_reject,
            'reject_reason': (_reject_reason or '')[:200],
            'intent': intent,
            'confidence': getattr(confidence, 'name', str(confidence)),
            'is_ellipsis': _is_ellipsis,
            'reject_threshold': _reject_cfg['threshold'],
            'final': 'reject' if (_len_reject or _semantic_reject) else 'pass_to_llm',
        }))

        if not _planning_mode and (_len_reject or _semantic_reject):
            _reject_type = "input_too_short" if _len_reject else "semantic_miss"
            _record_intent_layer("reject")
            # 【不易】拒识日志记录原因与各层分数（intent/confidence/semantic_result/threshold）
            logger.warning(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator.process.reject',
                'trace_id_ctx': trace_id,
                'message': '[拒识] %s: %s' % (_reject_type, _reject_reason),
                'reject_type': _reject_type,
                'intent': intent,
                'confidence': str(confidence),
                'semantic_result': semantic_result,
                'reject_threshold': _reject_cfg['threshold'],
                'input_length': len(user_input.strip()),
                'is_ellipsis': _is_ellipsis,
            }))
            # 任务6: 统一层日志（拒识层 WARNING）+ 最终路由决策（拒识）
            log_layer_result(
                LAYER_REJECT, DECISION_REJECT, trace_id,
                level=logging.WARNING,
                action='orchestrator.process.reject',
                message='[拒识] %s: %s' % (_reject_type, _reject_reason),
                duration_ms=0.0,
                reject_type=_reject_type,
                intent=intent,
                confidence=str(confidence),
                semantic_result=semantic_result,
                reject_threshold=_reject_cfg['threshold'],
            )
            emit_route_decision(
                LAYER_REJECT, DECISION_REJECT, trace_id,
                message='[拒识] 未知意图软拒识',
                basis_extra={
                    'reject_type': _reject_type,
                    'reason': (_reject_reason or "")[:200],
                    'intent': intent,
                    'confidence': str(confidence),
                    'reject_threshold': _reject_cfg['threshold'],
                },
            )
            _reject_msg = _REJECT_MSG  # 模块级常量，供测试 import 消除同源复制
            if trace_id:
                trace_store.end_trace(trace_id, _reject_msg, status="rejected")
            return ResponseBuilder.success(_reject_msg).to_dict()

        # ── 第三步三半：规划引擎接线（TASK-01 D7 接线，LLM 调用前）──
        # 【不易】wire_enabled=false（默认）时本分支整体 inert，生产行为与现状逐字节等价；
        #         仅当 wire_enabled=true 且任务复杂度 ≥ wire_min_complexity 才走规划；
        #         规划成功 → response 由 PlanningCore.chat() 生成并跳过 LLM 调用，
        #         同时抑制旧"第四步半"规划段（_planning_mode=False）防双规划；
        #         规划失败/超时/空响应 → 静默回退原 LLM 路径（守主链路稳定）。
        _wire_cfg = self._load_planning_wire_config()
        _wire_planning_used = False
        _wire_plan_result = None
        # 【排查】wire 链路追踪 ID：入口绑定一次（优先复用 process trace_id，无则 _trace_id() 兜底），
        #         出口所有日志（成功/三路回退）统一携带，grep wire_trace_id 即可还原完整 wire 调用链
        _wire_trace_id = trace_id or _trace_id()
        # 【排查】wire 入口判定日志：开关开启时 INFO（灰度期"为什么走/没走规划"全量可观测），
        #         开关关闭时 DEBUG（默认路径零噪音）——判定结果复用于后续分支，避免重复计算
        _wire_judged = _judge_wire_complexity(user_input) if _wire_cfg["enabled"] else None
        _wire_meets = _wire_complexity_meets(user_input, _wire_cfg["min_complexity"]) if _wire_cfg["enabled"] else False
        _wire_enter = bool(_wire_cfg["enabled"] and self._planner and _wire_meets)
        # 【排查】复杂度判定明细：命中的复杂/动作关键词与得分，回答"为什么判为 X 级"
        _wire_score, _wire_cx_matches, _wire_act_matches = _wire_complexity_detail(user_input) if _wire_cfg["enabled"] else (None, [], [])
        _wire_ingress_log = log_dict({
            'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.ingress',
            'trace_id_ctx': trace_id,
            'message': '[规划接线] 入口判定: wire_enabled=%s planner_available=%s '
                       'judged_complexity=%s min_complexity=%s meets=%s → %s' % (
                _wire_cfg["enabled"], self._planner is not None, _wire_judged,
                _wire_cfg["min_complexity"], _wire_meets,
                '进入规划引擎' if _wire_enter else '跳过规划（LLM 直答）'),
            'wire_enabled': _wire_cfg["enabled"],
            'planner_available': self._planner is not None,
            'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged,
            'complexity_score': _wire_score,
            'complex_matches': _wire_cx_matches,
            'action_matches': _wire_act_matches,
            'min_complexity': _wire_cfg["min_complexity"],
            'complexity_meets': _wire_meets,
            'enter_wire': _wire_enter,
            'timeout_seconds': float(_wire_cfg["timeout_seconds"]),
        })
        if _wire_cfg["enabled"]:
            logger.info(_wire_ingress_log)
        else:
            logger.debug(_wire_ingress_log)
        if _wire_cfg["enabled"] and self._planner and _wire_meets:
            _ts_wire_pf = time.perf_counter()
            _wire_ctx = {"session_id": kwargs.get("session_id"), "user_input": user_input}
            # 【排查】规划调用开始标记（时间轴起点）：回退日志的 wire_duration_ms 与此对照可还原完整调用链路
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.call.start', 'trace_id_ctx': trace_id, 'message': '[规划接线] 调用 PlanningCore.chat()（超时上限 %.1fs）' % float(_wire_cfg["timeout_seconds"]), 'input_length': len(user_input), 'timeout_seconds': float(_wire_cfg["timeout_seconds"]), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged}))
            try:
                _wire_plan_result = _run_async_in_sync(
                    self._planner.chat(user_input, _wire_ctx),
                    timeout=float(_wire_cfg["timeout_seconds"]),
                )
                if _wire_plan_result is not None and getattr(_wire_plan_result, "response", ""):
                    _wire_planning_used = True
                    _record_intent_layer("planning")
                    response = _wire_plan_result.response
                    _planning_mode = False  # 抑制旧"第四步半"段，避免双规划
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.planning', 'trace_id_ctx': trace_id, 'message': '[规划接线] wire 规划成功（%.1fms），跳过 LLM 调用' % ((time.perf_counter() - _ts_wire_pf) * 1000,), 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'response_length': len(response) if response else 0, 'timeout_seconds': float(_wire_cfg["timeout_seconds"]), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged}))
                else:
                    # 空响应回退：区分 None / 空字符串，info 级记录 PlanResult 结构与输入上下文（灰度期全量可排查），warning 保留为降级决策记录
                    _wire_plan_type = type(_wire_plan_result).__name__ if _wire_plan_result is not None else 'None'
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback.detail', 'trace_id_ctx': trace_id, 'message': '[规划接线] 空响应详情: result_type=%s response=%r 输入=%r' % (_wire_plan_type, getattr(_wire_plan_result, 'response', None), user_input[:200]), 'fallback_reason': 'plan_result_is_none' if _wire_plan_result is None else 'empty_response', 'result_type': _wire_plan_type, 'result_attrs': {k: str(getattr(_wire_plan_result, k, ''))[:200] for k in ('response', 'steps', 'plan', 'summary', 'reason') if hasattr(_wire_plan_result, k)}, 'input_preview': user_input[:200], 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))
                    logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback', 'trace_id_ctx': trace_id, 'message': '[规划接线] 规划结果为空，回退 LLM 直答（result_is_none=%s）' % (_wire_plan_result is None,), 'fallback_reason': 'plan_result_is_none' if _wire_plan_result is None else 'empty_response', 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))
            except asyncio.TimeoutError:
                # 逃生通道-超时：str(TimeoutError) 为空串，需显式超时文案与 is_timeout 标识（区别于一般异常）
                # info 级记录超时上下文（输入预览/会话上下文/实际耗时），warning 保留为降级决策记录
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback.detail', 'trace_id_ctx': trace_id, 'message': '[规划接线] 超时详情: 输入=%r 上下文=%s 实际耗时=%.1fms 上限=%.1fs' % (user_input[:200], {k: (str(v)[:200] if v else v) for k, v in _wire_ctx.items()}, (time.perf_counter() - _ts_wire_pf) * 1000, float(_wire_cfg["timeout_seconds"])), 'fallback_reason': 'timeout', 'is_timeout': True, 'input_preview': user_input[:200], 'wire_ctx': {k: (str(v)[:200] if v else v) for k, v in _wire_ctx.items()}, 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback', 'trace_id_ctx': trace_id, 'message': '[规划接线] 规划超时（上限 %.1fs），回退 LLM 直答' % float(_wire_cfg["timeout_seconds"]), 'fallback_reason': 'timeout', 'is_timeout': True, 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))
            except Exception as e:
                # 逃生通道-异常：完整记录原因链 + 调用耗时
                # info 级记录完整 traceback 尾部 + 输入/上下文（定位根因），warning 保留为降级决策记录
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback.detail', 'trace_id_ctx': trace_id, 'message': '[规划接线] 异常详情: error_type=%s error=%r 输入=%r' % (type(e).__name__, str(e)[:300], user_input[:200]), 'fallback_reason': 'exception', 'is_timeout': False, 'error': str(e)[:300], 'error_type': type(e).__name__, 'traceback_tail': __import__('traceback').format_exc()[-4000:], 'input_preview': user_input[:200], 'wire_ctx': {k: (str(v)[:200] if v else v) for k, v in _wire_ctx.items()}, 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wire.fallback', 'trace_id_ctx': trace_id, 'message': '[规划接线] 规划失败，回退 LLM 直答: %s' % (e,), 'fallback_reason': 'exception', 'is_timeout': False, 'wire_duration_ms': round((time.perf_counter() - _ts_wire_pf) * 1000, 1), 'error': str(e)[:200], 'error_type': type(e).__name__, 'wire_trace_id': _wire_trace_id, 'judged_complexity': _wire_judged, 'timeout_seconds': float(_wire_cfg["timeout_seconds"])}))

        # ── 第四步：LLM 调用（TASK-01 wire 规划成功时跳过，response 已由规划引擎生成）──
        if not _wire_planning_used:
            _record_intent_layer("llm")
            # 【变易】耗时用 perf_counter 配对计时; ts_llm（墙上时钟）仅供 TraceSpan 时间戳
            _ts_llm_pf = time.perf_counter()
            ts_llm = time.time()
            try:
                if self._v2_lifetrace and self._trace_recorder:
                    # V2 路径：Persona 系统 + ToolCallingService
                    # 会话元数据经 kwargs 显式传递，避免依赖实例全局 _session_id（并发安全）
                    response = self._call_llm_v2(
                        user_input, body_status,
                        session_id=kwargs.get("session_id"),
                        session_mgr=kwargs.get("session_mgr"),
                    )
                else:
                    # 标准路径
                    response = self._call_llm(user_input, body_status)
            except Exception as e:
                # 【TD-1】LLM 调用失败独立计层（llm_error 为 llm 的失败子指标）
                # llm（L507，INV-4 调用前埋点）计"尝试"；llm_error 计"失败"，
                # 成功路径不记 llm_error，面板 10 用 llm_error/llm 计算错误率
                _record_intent_layer("llm_error")
                _llm_err_ms = (time.perf_counter() - _ts_llm_pf) * 1000
                logger.error(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.fail', 'message': '[FAIL] 对话处理异常: %s' % (e,), 'error': str(e)}))
                tb_str = __import__('traceback').format_exc()
                logger.error(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '堆栈:\n%s' % (tb_str,), 'error': str(tb_str)}))
                # 任务6: 统一层日志（LLM 错误）+ 最终路由决策（错误）
                log_layer_result(
                    LAYER_LLM, DECISION_ERROR, trace_id,
                    level=logging.ERROR,
                    action='orchestrator.process.llm',
                    message='[LLM] 调用失败: %s' % (e,),
                    duration_ms=_llm_err_ms,
                    error=str(e)[:200],
                )
                emit_route_decision(
                    LAYER_LLM, DECISION_ERROR, trace_id,
                    message='[LLM] 调用异常，返回错误响应',
                    basis_extra={'error': str(e)[:200]},
                )
                if trace_id:
                    trace_store.end_trace(trace_id, str(e)[:200], status="error")
                if _MONITORING_AVAILABLE:
                    collector.increment_counter("count.digital_life.chat.error")
                    collector.increment_counter("count.digital_life.error.total")
                    if self._error_reporter:
                        try:
                            self._error_reporter.report_error(
                                error=e, level=AlertLevel.ERROR,
                                context={
                                    'user_input': user_input[:200],
                                    'trace_id': trace_id,
                                    'interaction_count': self._interaction_count,
                                    'session_id': _sid,
                                },
                            )
                            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.error_reported', 'trace_id_ctx': trace_id, 'message': '[OK] 错误已自动上报'}))
                        except Exception as report_error:
                            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.error_report_failed', 'trace_id_ctx': trace_id, 'error': str(report_error), 'message': '错误上报失败'}))
                return ResponseBuilder.error(
                    "抱歉，处理您的请求时遇到了问题：%s" % e
                ).to_dict()
            llm_duration_ms = (time.perf_counter() - _ts_llm_pf) * 1000
        else:
            # wire 规划成功：跳过 LLM 调用与置信度兜底（规划结果视为高置信度）
            ts_llm = time.time()
            llm_duration_ms = 0.0

        # ── LLM 置信度校验（任务3：基于响应质量的启发式校验 + 低置信度降级）──
        # 【简易】空/过短/错误标记 → 低置信度；正常响应 → high
        # 【变易】低置信度直接返回兜底文案（含转人工建议），不调用 HITL 异步审批
        #         未来可扩展为 LLM 自评 confidence 字段或工具调用成功率后验启发式
        _llm_confidence, _low_reason = _judge_llm_confidence(response)  # 模块级函数，供测试 import
        # 【日志】置信度判定过程（DEBUG 级别，记录触发 low 的具体原因便于排查）
        logger.debug(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.process.llm.confidence_judge',
            'trace_id_ctx': trace_id,
            'message': '[LLM] 置信度判定: %s (reason=%s, response_length=%d, llm_duration=%.2fms)' % (
                _llm_confidence, _low_reason, len(response) if response else 0, llm_duration_ms
            ),
            'llm_confidence': _llm_confidence,
            'low_reason': _low_reason,
            'response_length': len(response) if response else 0,
            'llm_duration_ms': round(llm_duration_ms, 2),
        }))
        logger.info(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.process.llm.confidence',
            'trace_id_ctx': trace_id,
            'message': '[LLM] 置信度=%s, 耗时=%.2fms, 响应长度=%d' % (
                _llm_confidence, llm_duration_ms, len(response) if response else 0
            ),
            'llm_confidence': _llm_confidence,
            'llm_duration_ms': round(llm_duration_ms, 2),
            'response_length': len(response) if response else 0,
        }))
        # 任务6: 统一层日志（LLM 层决策点, 含耗时 + 置信度作为决策依据）
        log_layer_result(
            LAYER_LLM, DECISION_SUCCESS, trace_id,
            message='[LLM] 调用完成, 置信度=%s' % _llm_confidence,
            duration_ms=llm_duration_ms,
            llm_confidence=_llm_confidence,
            low_reason=_low_reason,
            response_length=len(response) if response else 0,
        )

        # 【不易】低置信度触发兜底回复（任务3）：返回统一文案 + 转人工建议
        # 提前 return 跳过 OutputGuard/反思/向量记忆（低质量响应无需反思和持久化向量）
        # 但仍保存到对话记忆（便于后续分析低置信度场景）
        # TASK-01：wire 规划成功时跳过置信度兜底（规划结果视为高置信度，不降级）
        if not _wire_planning_used and _llm_confidence == "low":
            logger.warning(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator.process.llm.low_confidence_fallback',
                'trace_id_ctx': trace_id,
                'message': '[LLM] 低置信度响应，返回兜底文案 + 转人工建议',
                'original_response_preview': (response[:100] if response else ""),
                'llm_duration_ms': round(llm_duration_ms, 2),
            }))
            _record_intent_layer("llm_low_confidence_fallback")
            # 任务6: 最终路由决策（LLM 低置信度降级兜底）
            emit_route_decision(
                LAYER_LLM, DECISION_FALLBACK, trace_id,
                message='[LLM] 低置信度降级兜底',
                basis_extra={
                    'llm_confidence': _llm_confidence,
                    'low_reason': (_low_reason or "")[:200],
                    'llm_duration_ms': round(llm_duration_ms, 2),
                },
            )
            _fallback_msg = _FALLBACK_MSG  # 模块级常量，供测试 import 消除同源复制
            # 兜底响应仍走对话记忆保存（便于后续分析低置信度场景）
            self._memory.score_and_save_message("user", user_input)
            self._memory.score_and_save_message("assistant", _fallback_msg)
            if trace_id:
                trace_store.end_trace(trace_id, _fallback_msg, status="low_confidence_fallback")
            return ResponseBuilder.success(_fallback_msg).to_dict()

        # ── 第四步半：规划引擎策略增强（D7 接入，LLM 之上的策略层）──
        # 【不易】复用四层路由判定结果（语义层判定"复杂任务"或显式 planning_mode=True），
        #         不新增独立路由；规划响应仍进入既有 OutputGuard/记忆/反思/追踪链路。
        # 【变易】逃生通道：规划失败/超时/预算超限/空响应 → 回退原 LLM 直答（下方 response 保留）。
        # 【简易】get_stats() 仍暴露给状态面板（规划引擎能力不变），占位文本逻辑已移除。
        planning_mode = _planning_mode
        # 逃生通道依据：规划是否成功覆盖（供返回 metadata 携带 used_planning/plan_summary）
        _planning_used = False
        plan_result = None
        # 【排查】D7 规划入口判定日志（灰度开关/组件可用性/超时预算全量可观测，
        #         方便定位"为什么没走规划/为什么走了规划"）
        _planning_cfg = getattr(self, "_config", None) or getattr(self, "config", None) or {}
        _planning_cfg = _planning_cfg.get("planning") or {}
        _plan_timeout = float(_planning_cfg.get("timeout_seconds", 30))
        _enter_planning = bool(planning_mode and self._planner)
        logger.info(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator.process.planning.ingress',
            'trace_id_ctx': trace_id,
            'message': '[规划] 入口判定: planning_mode=%s planner_available=%s planning_enabled=%s → %s' % (
                planning_mode, self._planner is not None, getattr(self, '_planning_enabled', False),
                '进入规划引擎' if _enter_planning else '跳过规划（LLM 直答）'),
            'planning_mode': planning_mode,
            'planning_enabled': getattr(self, '_planning_enabled', False),
            'planner_available': self._planner is not None,
            'complexity_threshold': getattr(self, '_complexity_threshold', None),
            'timeout_seconds': _plan_timeout,
            'max_iterations': _planning_cfg.get('max_iterations', None),
            'enter_planning': _enter_planning,
        }))
        if planning_mode and self._planner:
            _ts_plan_pf = time.perf_counter()
            _plan_ctx = {"session_id": kwargs.get("session_id"), "user_input": user_input}
            try:
                plan_result = _run_async_in_sync(
                    self._planner.chat(user_input, _plan_ctx), timeout=_plan_timeout
                )
                _plan_dur_ms = (time.perf_counter() - _ts_plan_pf) * 1000
                if plan_result is not None and getattr(plan_result, "response", ""):
                    # 规划成功：其响应覆盖 LLM 直答结果，进入既有后链路
                    _record_intent_layer("planning")
                    _planning_used = True
                    response = plan_result.response
                    logger.info(log_dict({
                        'module_name': 'orchestrator',
                        'action': 'orchestrator.process.planning',
                        'trace_id_ctx': trace_id,
                        'message': '[规划] 复杂任务由规划引擎处理完成（used_planning=%s, %.2fms）' % (
                            bool(getattr(plan_result, 'used_planning', False)), _plan_dur_ms),
                        'used_planning': bool(getattr(plan_result, 'used_planning', False)),
                        'plan_id': (getattr(plan_result, 'plan', None).id
                                    if getattr(plan_result, 'plan', None) else None),
                        'response_length': len(response) if response else 0,
                        'planning_duration_ms': round(_plan_dur_ms, 2),
                    }))
                else:
                    # 空响应/无结果 → 回退原 LLM 直答
                    _fallback_reason = 'plan_result_is_none' if plan_result is None else 'empty_response'
                    logger.warning(log_dict({
                        'module_name': 'orchestrator',
                        'action': 'orchestrator.process.planning.fallback',
                        'trace_id_ctx': trace_id,
                        'message': '[规划] 规划结果为空，回退 LLM 直答 (reason=%s, %.2fms)' % (
                            _fallback_reason, _plan_dur_ms),
                        'fallback_reason': _fallback_reason,
                        'planning_duration_ms': round(_plan_dur_ms, 2),
                    }))
            except Exception as e:
                # 逃生通道：规划失败/超时/异常 → 回退原 LLM 直答响应
                _plan_dur_ms = (time.perf_counter() - _ts_plan_pf) * 1000
                logger.warning(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator.process.planning.fallback',
                    'trace_id_ctx': trace_id,
                    'message': '[规划] 规划调用失败/超时，回退 LLM 直答: %s (%.2fms)' % (e, _plan_dur_ms),
                    'error': str(e)[:200],
                    'error_type': type(e).__name__,
                    'timeout_seconds': _plan_timeout,
                    'planning_duration_ms': round(_plan_dur_ms, 2),
                }))

        # ── 第五步：OutputGuard 输出安全检查（PII 遮盖）──
        # 【变易】耗时用 perf_counter 配对计时
        _ts_og = time.perf_counter()
        output_result = self._output_guard.check(response)
        _dur_og = (time.perf_counter() - _ts_og) * 1000
        if output_result.modified:
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.guard', 'message': '[Guard] 🔒 输出已过滤，遮盖字段: %s' % (', '.join(output_result.redacted_fields),)}))
            # 任务6: 统一层日志（OutputGuard 修改决策点 INFO）
            log_layer_result(
                LAYER_OUTPUT_GUARD, DECISION_MODIFIED, trace_id,
                message='[Guard] 输出已过滤（PII 遮盖）',
                duration_ms=_dur_og,
                redacted_fields=list(output_result.redacted_fields),
            )
            response = output_result.filtered
        else:
            # 未修改（中间结果）→ DEBUG
            log_layer_result(
                LAYER_OUTPUT_GUARD, DECISION_PASS, trace_id,
                level=logging.DEBUG,
                action='orchestrator.process.guard',
                message='[Guard] 输出检查通过',
                duration_ms=_dur_og,
            )

        # ── 第五步半：输出验证门控（TASK-07，OutputGuard 之后追加）──
        # 【不易】不替换 OutputGuard；保守模式下响应零变化（仅记录 metrics/审计）
        _output_validator = self._output_validator
        if _output_validator is not None:
            _final_resp, _verdict = _output_validator.check_and_act(
                response, task_type="text_response")
            if _final_resp != response:
                response = _final_resp

        # Trace: 记录 LLM 调用 Span
        if trace_id:
            trace_store.add_span(trace_id, TraceSpan(
                span_id=f"{trace_id}_llm",
                operation="llm_call",
                start_time=ts_llm, end_time=time.time(),
                duration_ms=llm_duration_ms,
                status="success",
                metadata={"redacted_fields": list(output_result.redacted_fields)
                          if output_result.modified else []},
            ))

        # ── 第六步：认知循环——反思 ──
        if self._behavior.profile.enable_reflection:
            if self._is_skill_enabled("self_reflection"):
                self.self_reflect(user_input, response)
            else:
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.skillgate', 'message': '[SkillGate] self_reflection 已禁用，跳过'}))

        # ── 第七步：记忆保存 ──
        self._memory.score_and_save_message("user", user_input)
        self._memory.score_and_save_message("assistant", response)
        try:
            self._memory.infer_working_memory(user_input, response)
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.wm', 'message': '[WM] 工作记忆更新失败: %s' % (e,)}))

        # ── 第七步半：工作流自动学习（自动闭环 v1）──
        # 【变易】走到这里说明 LLM 成功且非低置信度（低置信度已在前面 return 兜底）。
        #         从 _last_tool_steps 提取成功的工具调用序列自动 learn_from_interaction，
        #         沉淀为本地工作流供下次 0 Token 命中。
        #         【不易】内部异常只记日志，不影响主链路；失败的工具步骤被过滤不学习。
        self._learn_workflow_from_interaction(user_input, session_id=_sid)

        # 向量记忆保存
        # 【不易】显式判 None（不依赖对象真值语义）：真实 VectorStore 无 __len__ 恒真值，
        #        但未来若有人添加 __len__（FakeVectorStore 曾因此误判为空而跳过写入），
        #        真值判断会静默失效——is not None 消除该隐式契约（TASK-02 同步加固）
        if self._vector_memory is not None:
            try:
                memory_content = f"用户: {user_input}\n云枢: {response}"
                item_id = self._vector_memory.add(
                    content=memory_content,
                    metadata={
                        "type": "conversation",
                        "interaction": self._interaction_count,
                    },
                )
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.log', 'message': '[记忆] 向量记忆已保存: %s' % (item_id,)}))
            except Exception as e:
                logger.error(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.process.fail', 'message': '[FAIL] 保存向量记忆失败: %s' % (e,), 'error': str(e)}))

        # V2: LifeTrace 记录响应
        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.record_chat(
                role="assistant", content=response,
                metadata={"interaction_id": self._interaction_count},
            )

        # V2: 人格蒸馏批量学习
        if self._v2_distillation and self._persona_extractor and \
           self._interaction_count % self._distillation_interval == 0:
            self._run_persona_distillation()

        # 兼容旧系统
        self._memory.add_message("user", user_input)
        self._memory.add_message("assistant", response)

        # 上下文快满时追加切换建议
        if self._last_context_warning and self._last_context_warning["level"] == "critical":
            carry_summary = ""
            try:
                summary_data = self._memory.load_summary()
                if summary_data:
                    carry_summary = summary_data[0][:2000]
            except Exception:
                pass
            if not carry_summary:
                carry_summary = (
                    f"本次对话共 {self._interaction_count} 轮，"
                    f"最新用户提问：{user_input[:200]}"
                )
            self._last_context_warning["summary"] = carry_summary
            response += (
                "\n\n---\n💡 **当前会话上下文即将耗尽**"
                f"（已使用 {self._last_context_warning['pct']:.0f}%）。"
                "\n点击下方「创建新会话」按钮，我会携带之前的记忆继续对话。"
            )

        # ── Trace: 结束记录 ──
        if trace_id:
            trace_store.end_trace(trace_id, response)

        # 任务6: 最终路由决策（LLM 正常完成, 含全链路各层耗时与中间结果）
        emit_route_decision(
            LAYER_LLM, DECISION_SUCCESS, trace_id,
            message='[LLM] 正常完成',
            basis_extra={
                'llm_duration_ms': round(llm_duration_ms, 2),
                'output_guard_modified': bool(output_result.modified),
                'redacted_fields': list(output_result.redacted_fields)
                if output_result.modified else [],
            },
        )

        if _MONITORING_AVAILABLE:
            collector.increment_counter("count.digital_life.chat.success")

        _resp = ResponseBuilder.success(response).to_dict()
        # 评估标准：E2E 断言响应含 used_planning/plan_summary（D7 规划覆盖标识）
        if _planning_used:
            _resp.setdefault("metadata", {})["used_planning"] = True
            if getattr(plan_result, "plan_summary", None) is not None:
                _resp["metadata"]["plan_summary"] = plan_result.plan_summary
        # TASK-01：wire 规划成功时标注 routed_by: planning（供 TASK-03 埋点）
        if _wire_planning_used:
            _resp.setdefault("metadata", {})["routed_by"] = "planning"
            if getattr(_wire_plan_result, "plan_summary", None) is not None:
                _resp["metadata"]["plan_summary"] = _wire_plan_result.plan_summary
        return _resp

    # (以下废弃方法已在 P12 统一链路中删除:
    #  _chat_v2, _chat_with_planning, _process_user_input)
    #  所有功能已合并到 process() 方法中

    # ════════════════════════════════════════════════════════════════════
    #  DST 状态回写 — 路由后写入 intent/skill/keywords/user_input
    # ════════════════════════════════════════════════════════════════════

    def _update_dst_after_route(self, intent: Optional[str],
                                skill: Optional[str] = None,
                                user_input: str = "",
                                session_id: Optional[str] = None) -> None:
        """路由后回写 DST 状态（供下一轮指代消解继承）

        架构层级：[TLM-L0] DST 状态回写 — 兜底原本只存于注释的承诺

        Args:
            session_id: 会话 ID（显式传参，并发安全）；None 时回退实例 _session_id

        【不易】每轮仅调用一次（turn_count 单次递增）；skill 由语义层命中后
               直接 set last_skill 单独写入（不重复调用本方法）
        【变易】keywords 为空时不覆盖（保留上一轮非省略句的关键词，避免"那个呢"
               这类省略句把 last_keywords 清空导致下一轮无法继承）
        【简易】任何异常降级为 DEBUG 日志，不阻断主链路
        """
        try:
            from agent.orchestrator.dialog_state import get_dialog_state
            dst = get_dialog_state(session_id or getattr(self, '_session_id', 'default'))
            kw = MessageHandler.extract_keywords(user_input) if user_input else []
            dst.update(
                intent=intent,
                skill=skill,
                keywords=(kw if kw else None),  # 空关键词不覆盖
                user_input=(user_input or None),
            )
        except Exception as e:
            logger.debug(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator.dst.update_after_route.error',
                'message': '[DST] 回写失败: %s' % (e,),
            }))

    # ════════════════════════════════════════════════════════════════════
    #  语义层 — SkillLoader RRF 三路融合召回（三层漏斗第 2 层）
    # ════════════════════════════════════════════════════════════════════

    # 语义层配置缓存（复用 loader.py:L1079-1177 的 mtime 缓存模式）
    # _SEM_CONFIG_CACHE: (mtime_timestamp, config_dict)；mtime 变化时自动失效
    _SEM_CONFIG_CACHE: Optional[Tuple[float, Dict[str, Any]]] = None
    _SEM_CONFIG_PATH: Optional[Any] = None  # Path 对象，延迟初始化

    # 语义层硬编码默认值（最终兜底，与 config.yaml orchestrator.semantic_layer 同源）
    _SEM_DEFAULTS: Dict[str, Any] = {
        "enabled": True,
        "min_score": 0.3,
        "top_k": 5,
        "use_vector": True,
        "use_bm25": True,
        "use_reranker": False,
        "fusion_mode": "rrf",
    }

    # Workflow Learning 拦截层配置默认值（自动闭环 v1）
    # 配置优先级: 环境变量 > config.yaml orchestrator.workflow_learning_layer > 此处硬编码
    # 【不易】硬编码默认值兜底，config.yaml 缺失/解析失败不影响主链路
    _WFL_DEFAULTS: Dict[str, Any] = {
        "enabled": True,
        "min_score": 0.25,
    }

    # 自动学习钩子开关默认值（LLM 成功交互后自动 learn_from_interaction）
    _WF_LEARN_ENABLED: bool = True

    # 懒加载缓存: 是否已完成 ToolExecutor 注入（避免重复注入）
    _WFL_TOOL_EXECUTOR_INJECTED: bool = False

    # 语义层 API 热更覆盖层（优先级最高，由 HTTP API /api/orchestrator/semantic-config 设置）
    # 【变易】运行时动态覆盖，重启后从 SQLite 恢复（_load_semantic_override_from_db）
    _SEM_API_OVERRIDE: Optional[Dict[str, Any]] = None

    # SQLite 持久化（语义层配置热更）— 复用 HolographicAdapter thread-local + busy_timeout 模式
    _SEM_DB_PATH: Optional[Any] = None
    _SEM_DB_CONN_LOCAL: Any = None  # 延迟初始化为 threading.local()
    _SEM_DB_LOADED: bool = False  # 启动加载标志（首次调用 _load_semantic_layer_config 时触发）

    @classmethod
    def _load_semantic_layer_config(cls) -> Dict[str, Any]:
        """读取语义层配置 — 优先级: 环境变量 > config.yaml > 硬编码默认值

        架构层级：[TLM-L2] 语义层配置加载

        分层配置架构（与 loader.py _get_default_weights 同源模式）:
            层0: 硬编码默认值（_SEM_DEFAULTS，最终兜底）
            层1: config.yaml orchestrator.semantic_layer（业务配置主源，mtime 缓存）
            层2: 环境变量（运维 hotfix 覆盖，优先级最高）

        config.yaml 路径: orchestrator.semantic_layer.{enabled,min_score,top_k,...}
        环境变量: ORCHESTRATOR_SEMANTIC_LAYER_ENABLED / ORCHESTRATOR_SEMANTIC_MIN_SCORE

        【不易】硬编码默认值作为最终兜底，config.yaml 缺失/解析失败不影响主链路
        【变易】config.yaml mtime 缓存避免每次调用都解析 YAML；env 允许运维临时覆盖
        【简易】逐层覆盖，每层失败静默降级；返回新 dict（线程安全）
        """
        from pathlib import Path

        # 首次调用时从 SQLite 加载持久化的热更配置（延迟加载，避免模块导入时 I/O）
        if not cls._SEM_DB_LOADED:
            cls._SEM_DB_LOADED = True
            cls._load_semantic_override_from_db()

        # 层0: 硬编码默认值（最终兜底）
        config = dict(cls._SEM_DEFAULTS)

        # 层1: config.yaml（带 mtime 缓存）
        try:
            if cls._SEM_CONFIG_PATH is None:
                cls._SEM_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            cfg_path = cls._SEM_CONFIG_PATH

            if cfg_path.exists():
                try:
                    current_mtime = cfg_path.stat().st_mtime
                except OSError:
                    current_mtime = 0.0

                yaml_cfg: Optional[Dict[str, Any]] = None
                # 缓存命中检查（mtime 未变 → 复用缓存）
                _cache_invalid_reason = None  # 监控用：记录缓存失效原因
                if cls._SEM_CONFIG_CACHE is not None:
                    cached_mtime, cached_cfg = cls._SEM_CONFIG_CACHE
                    if cached_mtime == current_mtime:
                        yaml_cfg = cached_cfg
                    else:
                        # 【变易】mtime 变化 → 缓存失效，记录监控日志便于线上排查
                        _cache_invalid_reason = "mtime_changed"
                        import datetime as _dt
                        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.cache_invalidated', 'message': '[语义层] config.yaml 缓存失效 (mtime 变化): old=%.3f new=%.3f invalidated_at=%s' % (cached_mtime, current_mtime, _dt.datetime.now().isoformat())}))
                        yaml_cfg = None  # mtime 变化，触发重建
                else:
                    _cache_invalid_reason = "first_load"
                    yaml_cfg = None

                # 缓存未命中或失效 → 重新解析
                if yaml_cfg is None:
                    import yaml as _yaml
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        data = _yaml.safe_load(f) or {}
                    yaml_cfg = (data.get("orchestrator", {}) or {}).get("semantic_layer", {}) or {}
                    cls._SEM_CONFIG_CACHE = (current_mtime, yaml_cfg)
                    # 【变易】记录配置加载日志（首次加载 + mtime 变化后重载）
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.loaded', 'message': '[语义层] config.yaml 已加载 (reason=%s): mtime=%.3f keys=%s' % (_cache_invalid_reason or "cache_hit_miss", current_mtime, list(yaml_cfg.keys()))}))

                # 用 config.yaml 值覆盖默认值（仅覆盖已知键，类型与默认值一致才接受）
                for key in cls._SEM_DEFAULTS:
                    if key in yaml_cfg and yaml_cfg[key] is not None:
                        config[key] = yaml_cfg[key]
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.fallback', 'message': '[语义层] config.yaml 读取失败，降级到默认值: %s' % (e,)}))

        # 层2: 环境变量覆盖（最高优先级，运维 hotfix）
        env_enabled = os.environ.get("ORCHESTRATOR_SEMANTIC_LAYER_ENABLED")
        if env_enabled is not None and env_enabled.strip():
            config["enabled"] = env_enabled.strip().lower() in ("true", "1", "yes")
        env_min_score = os.environ.get("ORCHESTRATOR_SEMANTIC_MIN_SCORE")
        if env_min_score is not None and env_min_score.strip():
            try:
                config["min_score"] = float(env_min_score.strip())
            except (ValueError, TypeError):
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.invalid_min_score', 'message': '[语义层] ORCHESTRATOR_SEMANTIC_MIN_SCORE 非法值已忽略: %s' % (env_min_score,)}))

        # 层3: API 热更覆盖（最高优先级，由 /api/orchestrator/semantic-config 设置）
        # 【变易】运行时动态覆盖，不持久化；None 值跳过（允许部分覆盖）
        if cls._SEM_API_OVERRIDE is not None:
            for key in cls._SEM_DEFAULTS:
                if key in cls._SEM_API_OVERRIDE and cls._SEM_API_OVERRIDE[key] is not None:
                    config[key] = cls._SEM_API_OVERRIDE[key]

        return config

    @classmethod
    def _clear_semantic_config_cache(cls) -> None:
        """手动清除语义层配置缓存（测试用 / config.yaml 修改后强制刷新）"""
        cls._SEM_CONFIG_CACHE = None

    # ═══════════════════════════════════════════════════════════════
    # Workflow Learning 拦截层（自动闭环 v1）
    # 位置：模板层(IntentRouter)未命中后、语义层(SkillLoader)之前
    # 命中并成功执行本地工作流 → 短路返回（跳过 LLM，0 Token）
    # 未命中/执行失败/异常 → 返回 None，调用方继续 LLM（守【不易】主链路稳定）
    # 模块入口：process() 第三步与第三步半之间
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def _load_workflow_learning_layer_config(cls) -> Dict[str, Any]:
        """读取工作流拦截层配置 — 优先级: 环境变量 > config.yaml > 硬编码默认值

        config.yaml 路径: orchestrator.workflow_learning_layer.{enabled,min_score}
        环境变量: ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED /
                 ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE

        【不易】硬编码默认值兜底，config.yaml 缺失/解析失败不影响主链路
        """
        from pathlib import Path

        config = dict(cls._WFL_DEFAULTS)
        try:
            cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if cfg_path.exists():
                import yaml as _yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                yaml_cfg = (data.get("orchestrator", {}) or {}).get("workflow_learning_layer", {}) or {}
                for key in cls._WFL_DEFAULTS:
                    if key in yaml_cfg and yaml_cfg[key] is not None:
                        config[key] = yaml_cfg[key]
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.config.fallback', 'message': '[工作流层] config.yaml 读取失败，降级到默认值: %s' % (e,)}))

        env_enabled = os.environ.get("ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED")
        if env_enabled is not None and env_enabled.strip():
            config["enabled"] = env_enabled.strip().lower() in ("true", "1", "yes")
        env_min = os.environ.get("ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE")
        if env_min is not None and env_min.strip():
            try:
                config["min_score"] = float(env_min.strip())
            except (ValueError, TypeError):
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.config.invalid_min_score', 'message': '[工作流层] ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE 非法值已忽略: %s' % (env_min,)}))
        return config

    def _workflow_learning_layer_match(self, routing_input: str,
                                       trace_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """工作流拦截层 — 尝试匹配并执行本地工作流（0 Token 短路）

        架构层级：三层漏斗第 2.5 层（工作流学习层）
        调用时机：模板层未命中后、语义层之前；使用 DST 补全后的 routing_input 匹配
                  （省略句"那个呢/然后呢"必须经 DST 补全才能命中 TF-IDF 索引）

        【不易】任何异常都返回 None，主链路降级到 LLM（不抛异常）
        【变易】配置来自 config.yaml orchestrator.workflow_learning_layer
                （env ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED/MIN_SCORE 可覆盖）
        【简易】命中且执行成功 → 返回结果 dict；未命中/失败/异常 → None

        Returns:
            命中时: {"output": str, "workflow_id": str, "workflow_name": str,
                     "score": float, "confidence": float, "steps_executed": int,
                     "elapsed_ms": float, "skipped_llm": bool}
            未命中/降级: None
        """
        cfg = self._load_workflow_learning_layer_config()
        if not cfg["enabled"]:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.disabled', 'trace_id_ctx': trace_id, 'message': '[工作流层] 已关闭(enabled=false)，继续语义层'}))
            return None

        _ts_pf = time.perf_counter()
        try:
            # 延迟导入避免循环依赖（state_manager 依赖较重）
            from agent.state_manager import get_workflow_learning_service
            svc = get_workflow_learning_service()
            if svc is None:
                return None

            # 懒注入 ToolExecutor（agent.tools.call 签名与 ToolExecutor 一致，仅注入一次）
            if not self._WFL_TOOL_EXECUTOR_INJECTED:
                try:
                    from agent.tools import call as _tool_call
                    svc.set_tool_executor(
                        lambda tool_name, params: _tool_call(tool_name, **params)
                    )
                    self._WFL_TOOL_EXECUTOR_INJECTED = True
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.tool_executor', 'trace_id_ctx': trace_id, 'message': '[工作流层] ToolExecutor 已注入（agent.tools.call）'}))
                except Exception as inj_e:
                    logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.tool_executor_failed', 'trace_id_ctx': trace_id, 'message': '[工作流层] ToolExecutor 注入失败，降级 LLM: %s' % (inj_e,)}))
                    return None

            result = svc.try_execute(routing_input, min_score=float(cfg["min_score"]))
            elapsed_ms = (time.perf_counter() - _ts_pf) * 1000

            if not result.matched:
                # 任务6: 统一层日志（未命中, 中间结果 → DEBUG）
                log_layer_result(
                    LAYER_WORKFLOW_LEARNING, DECISION_MISS, trace_id,
                    level=logging.DEBUG,
                    action='orchestrator.wfl.miss',
                    message='[工作流层] 未命中 (%.2fms, min_score=%.2f)' % (
                        elapsed_ms, float(cfg["min_score"])),
                    duration_ms=elapsed_ms,
                )
                # TASK-03: 学习度量——工作流未命中（埋点异常不影响主链路）
                _emit_learning_metric("record_workflow_match", hit=False)
                return None

            if not result.success:
                # 执行失败：executor 已更新 failure_count 并降低 confidence，降级 LLM
                log_layer_result(
                    LAYER_WORKFLOW_LEARNING, DECISION_ERROR, trace_id,
                    level=logging.WARNING,
                    action='orchestrator.wfl.exec_failed',
                    message='[工作流层] 执行失败，降级 LLM: wf=%s err=%s' % (
                        result.workflow_id, (result.error or "")[:200]),
                    duration_ms=elapsed_ms,
                    workflow_id=result.workflow_id,
                    error=(result.error or "")[:200],
                )
                return None

            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.hit', 'trace_id_ctx': trace_id,
                'message': '[工作流层] 命中 wf=%s name=%s score=%.3f conf=%.3f (%d 步, %.2fms, 跳过LLM=%s)' % (
                    result.workflow_id, result.workflow_name, result.similarity,
                    result.confidence, result.steps_executed, elapsed_ms, result.skipped_llm)}))
            # 任务6: 统一层日志（命中, 决策点 INFO）
            log_layer_result(
                LAYER_WORKFLOW_LEARNING, DECISION_HIT, trace_id,
                action='orchestrator.wfl.hit',
                message='[工作流层] 命中短路返回: wf=%s score=%.3f' % (
                    result.workflow_id, result.similarity),
                duration_ms=elapsed_ms,
                score=result.similarity,
                workflow_id=result.workflow_id,
                workflow_name=result.workflow_name,
                confidence=result.confidence,
                steps_executed=result.steps_executed,
                skipped_llm=result.skipped_llm,
            )
            # TASK-03: 学习度量——工作流命中 + 节省 token 估算（埋点异常不影响主链路）
            _emit_learning_metric(
                "record_workflow_match", hit=True,
                saved_tokens=_get_learning_saved_estimate(),
            )
            return {
                "output": str(result.output or ""),
                "workflow_id": result.workflow_id,
                "workflow_name": result.workflow_name,
                "score": result.similarity,
                "confidence": result.confidence,
                "steps_executed": result.steps_executed,
                "elapsed_ms": elapsed_ms,
                "skipped_llm": result.skipped_llm,
            }
        except Exception as e:
            # 【不易】异常降级 LLM，不中断主链路
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.error', 'trace_id_ctx': trace_id, 'message': '[工作流层] 异常，降级 LLM: %s' % (e,)}))
            log_layer_result(
                LAYER_WORKFLOW_LEARNING, DECISION_ERROR, trace_id,
                level=logging.DEBUG,
                action='orchestrator.wfl.error',
                message='[工作流层] 异常降级: %s' % (str(e)[:200],),
            )
            return None

    # ─── 自动学习钩子（自动闭环 v1）───

    @classmethod
    def _wf_learn_enabled(cls) -> bool:
        """自动学习开关 — 优先级: 环境变量 > config.yaml workflow_learning.learn_from_interaction.enabled > 默认"""
        env = os.environ.get("ORCHESTRATOR_WF_LEARN_ENABLED")
        if env is not None and env.strip():
            return env.strip().lower() in ("true", "1", "yes")
        try:
            from pathlib import Path
            cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if cfg_path.exists():
                import yaml as _yaml
                with open(cfg_path, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                learn_cfg = (data.get("workflow_learning", {}) or {}).get("learn_from_interaction", {}) or {}
                if "enabled" in learn_cfg and learn_cfg["enabled"] is not None:
                    return bool(learn_cfg["enabled"])
        except Exception:
            pass
        return cls._WF_LEARN_ENABLED

    @staticmethod
    def _extract_tool_calls_from_steps(steps: list) -> list:
        """把 tool_calling steps 转成 LearningRecord.tool_calls 格式

        steps 格式（tool_calling.py chat_with_steps 产出）:
            [{"type":"tool_call","tool":name,"args":{...},"status":"running"},
             {"type":"tool_result","tool":name,"status":"success"|"error","summary":...}]
        输出格式:
            [{"name":..., "params":..., "output":summary, "success":bool}]

        【简易】按出现顺序配对 tool_call→tool_result，同一工具多次调用分别保留；
                执行失败的工具调用被丢弃（守学习质量，失败流程不可学）
        """
        pending: Dict[str, list] = {}
        calls: list = []
        for s in steps or []:
            if not isinstance(s, dict):
                continue
            stype = s.get("type")
            if stype == "tool_call":
                tool = s.get("tool")
                if tool:
                    pending.setdefault(tool, []).append({
                        "name": tool,
                        "params": s.get("args") or {},
                    })
            elif stype == "tool_result":
                tool = s.get("tool")
                q = pending.get(tool)
                if q:
                    entry = q.pop(0)
                    if s.get("status") == "success":
                        calls.append({
                            "name": entry["name"],
                            "params": entry["params"],
                            "output": s.get("summary", ""),
                            "success": True,
                        })
        return calls

    def _learn_workflow_from_interaction(self, user_input: str,
                                         session_id: Optional[str] = None) -> bool:
        """从成功的 LLM 交互自动学习方法（自动闭环 v1）

        数据源: self._last_tool_steps（由 _call_llm/_call_llm_v2 填充）
        触发条件: 自动学习开关开启 + 工具调用序列非空（≥ learner.min_tool_calls）
        【不易】任何异常只记日志，不影响主链路

        Args:
            session_id: 会话 ID（显式传参，并发安全）；None 时回退实例 _session_id
        """
        try:
            if not self._wf_learn_enabled():
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.learn_skip',
                    'message': '[工作流] 自动学习跳过: 开关关闭(ORCHESTRATOR_WF_LEARN_ENABLED / config workflow_learning.learn_from_interaction.enabled)'}))
                return False
            steps = getattr(self, "_last_tool_steps", None) or []
            tool_calls = self._extract_tool_calls_from_steps(steps)
            if not tool_calls:
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.learn_skip',
                    'message': '[工作流] 自动学习跳过: 本次交互无成功工具调用(共 %d 个 steps)' % (len(steps),)}))
                return False

            from agent.state_manager import get_workflow_learning_service
            svc = get_workflow_learning_service()
            if svc is None:
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.learn_skip',
                    'message': '[工作流] 自动学习跳过: 服务未初始化(get_workflow_learning_service 返回 None)'}))
                return False
            from agent.workflow_learning.models import LearningRecord
            record = LearningRecord(
                session_id=session_id or getattr(self, "_session_id", "default"),
                user_input=user_input,
                tool_calls=tool_calls,
                success=True,
            )
            wf = svc.learn_from_interaction(record)
            # TASK-03: 学习度量——工作流沉淀（埋点异常不影响主链路）
            _emit_learning_metric("record_artifact", "workflow")
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.learned',
                'message': '[工作流] 自动学习成功: wf=%s 步骤=%d 触发词=%s' % (
                    wf.id, len(wf.steps), (wf.trigger_patterns or [])[:3])}))
            return True
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.wfl.learn_failed',
                'message': '[工作流] 自动学习失败（不影响主链路）: %s' % (e,)}))
            return False

    # ═══════════════════════════════════════════════════════════════
    # SQLite 持久化（语义层配置热更）
    # 复用 HolographicAdapter thread-local + busy_timeout 模式
    # 【不易】持久化失败不影响内存热更（降级到纯内存模式）
    # 【变易】启动时延迟加载，热更时 UPSERT 写入
    # 【简易】独立 db 文件（orchestrator_config.db），不污染 holographic.db
    # ═══════════════════════════════════════════════════════════════

    @classmethod
    def _get_semantic_db_conn(cls):
        """获取语义层配置持久化 SQLite 连接（thread-local + busy_timeout）

        架构层级：[TLM-L1] 配置持久化 - 复用 HolographicAdapter 连接模式
        """
        import sqlite3 as _sqlite3
        import threading as _threading

        if cls._SEM_DB_CONN_LOCAL is None:
            cls._SEM_DB_CONN_LOCAL = _threading.local()

        if hasattr(cls._SEM_DB_CONN_LOCAL, 'conn'):
            return cls._SEM_DB_CONN_LOCAL.conn

        if cls._SEM_DB_PATH is None:
            from pathlib import Path as _Path
            cls._SEM_DB_PATH = _Path(__file__).resolve().parent.parent.parent / "data" / "orchestrator_config.db"

        # 确保目录存在
        cls._SEM_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

        conn = _sqlite3.connect(str(cls._SEM_DB_PATH), check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")  # 【不易】处理 SQLITE_BUSY
        conn.execute("PRAGMA journal_mode=WAL")   # WAL 模式提升并发读写

        # 建表（幂等）
        conn.execute("""
            CREATE TABLE IF NOT EXISTS semantic_config_overrides (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()

        cls._SEM_DB_CONN_LOCAL.conn = conn
        return conn

    @classmethod
    def _load_semantic_override_from_db(cls) -> None:
        """启动时从 SQLite 加载热更配置到 _SEM_API_OVERRIDE

        【不易】加载失败降级到纯内存模式（_SEM_API_OVERRIDE 保持 None/现有值）
        """
        try:
            conn = cls._get_semantic_db_conn()
            rows = conn.execute("SELECT key, value FROM semantic_config_overrides").fetchall()
            if rows:
                import json as _json
                overrides = {}
                for key, value in rows:
                    try:
                        overrides[key] = _json.loads(value)
                    except (ValueError, TypeError):
                        pass  # 非法 JSON 跳过
                if overrides:
                    # 合并到现有 _SEM_API_OVERRIDE（不覆盖已设置的值）
                    if cls._SEM_API_OVERRIDE is None:
                        cls._SEM_API_OVERRIDE = {}
                    cls._SEM_API_OVERRIDE.update(overrides)
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.db_loaded', 'message': '[语义层] 从 SQLite 恢复热更配置: keys=%s' % list(overrides.keys())}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.db_load_failed', 'message': '[语义层] SQLite 加载热更配置失败（降级到内存模式）: %s' % (e,)}))

    @classmethod
    def _save_semantic_override_to_db(cls, overrides: Dict[str, Any]) -> None:
        """热更时将配置写入 SQLite（UPSERT）

        【不易】持久化失败不影响内存热更（已更新 _SEM_API_OVERRIDE）
        【变易】使用 INSERT ... ON CONFLICT DO UPDATE（HolographicAdapter 同款 UPSERT）
        """
        try:
            import json as _json
            import datetime as _dt
            conn = cls._get_semantic_db_conn()
            now = _dt.datetime.now().isoformat()
            for key, value in overrides.items():
                conn.execute(
                    "INSERT INTO semantic_config_overrides (key, value, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                    (key, _json.dumps(value), now)
                )
            conn.commit()
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.db_saved', 'message': '[语义层] 热更配置已持久化到 SQLite: keys=%s' % list(overrides.keys())}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.config.db_save_failed', 'message': '[语义层] SQLite 持久化失败（不影响内存热更）: %s' % (e,)}))

    def _semantic_layer_match(self, user_input: str,
                              trace_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """语义层匹配 — SkillLoader RRF 三路融合召回 + 技能 instruction 加载

        架构层级：三层漏斗第 2 层（语义层）
        调用时机：规则层(WorkflowEngine)+模板层(IntentRouter)未命中后，
                  LLM 调用之前。

        【不易】任何异常都返回 None，主链路降级到 LLM（不抛异常）
        【变易】配置来自 config.yaml orchestrator.semantic_layer
               （env ORCHESTRATOR_SEMANTIC_LAYER_ENABLED/MIN_SCORE 可覆盖）
        【简易】命中 top1 score ≥ 阈值 → 加载 instruction → 返回结果 dict；
               未命中/instruction 为空/异常 → 返回 None，调用方继续 LLM

        Args:
            user_input: 用户原始输入
            trace_id: 链路追踪 ID

        Returns:
            命中时: {"output": str, "skill_id": str, "score": float,
                     "retrieval_method": str, "reranked": bool,
                     "fallback_used": bool, "elapsed_ms": float}
            未命中/降级: None
        """
        cfg = self._load_semantic_layer_config()
        if not cfg["enabled"]:
            # 【排查】语义层关闭时明确记录降级原因，避免"为什么没走语义层"的困惑
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.disabled', 'trace_id_ctx': trace_id, 'message': '[语义层] 已关闭(enabled=false)，降级 LLM'}))
            return None

        min_score = float(cfg["min_score"])
        # 【变易】耗时用 perf_counter 配对计时; ts_sem（墙上时钟）仅供 TraceSpan 时间戳
        ts_sem = time.time()
        _ts_sem_pf = time.perf_counter()

        # 【排查】调用前打印配置摘要 + 入参摘要（截断 50 字符避免敏感信息泄漏）
        _input_preview = user_input[:50] + ("..." if len(user_input) > 50 else "")
        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.invoke', 'trace_id_ctx': trace_id, 'message': '[语义层] 调用 SkillLoader.match: input="%s" min_score=%.2f top_k=%d vector=%s bm25=%s reranker=%s fusion=%s' % (_input_preview, min_score, cfg["top_k"], cfg["use_vector"], cfg["use_bm25"], cfg["use_reranker"], cfg["fusion_mode"])}))

        try:
            # 延迟导入避免循环依赖（state_manager 依赖较重）
            from agent.state_manager import get_skills_mgmt_service
            svc = get_skills_mgmt_service()
            if svc is None or svc.loader is None:
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.skip', 'trace_id_ctx': trace_id, 'message': '[语义层] skills_mgmt 服务未初始化，跳过'}))
                return None

            # 调用 SkillLoader.match 启用 RRF 三路融合
            # 【变易】use_vector + use_bm25 触发 RRF 融合（tfidf+vector+bm25）
            #         向量模型不可用时 SkillLoader 内部降级到 TF-IDF+BM25
            result = svc.loader.match(
                user_input,
                top_k=int(cfg["top_k"]),
                enabled_only=True,
                min_score=min_score,
                use_vector=bool(cfg["use_vector"]),
                use_bm25=bool(cfg["use_bm25"]),
                use_reranker=bool(cfg["use_reranker"]),
                fusion_mode=str(cfg["fusion_mode"]),
            )

            elapsed_ms = (time.perf_counter() - _ts_sem_pf) * 1000

            # 【排查】打印候选列表详情（skill_id/score），便于诊断"为什么 top1 没过阈值"
            # 仅 DEBUG 级别输出，生产环境调高日志级别即可隐藏
            if result.matches:
                _candidates = ", ".join("%s=%.3f" % (m.skill_id, m.score) for m in result.matches[:5])
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.candidates', 'trace_id_ctx': trace_id, 'message': '[语义层] 候选列表 (top%d): %s | 阈值=%.2f' % (len(result.matches[:5]), _candidates, min_score)}))

            if not result.matches:
                # 任务6: 统一层日志（语义层未命中, 中间结果 → DEBUG）
                log_layer_result(
                    LAYER_SEMANTIC, DECISION_MISS, trace_id,
                    level=logging.DEBUG,
                    action='orchestrator.semantic.miss',
                    message='[语义层] 未命中 (min_score=%.2f, %.2fms, method=%s)' % (
                        min_score, elapsed_ms, result.retrieval_method),
                    duration_ms=elapsed_ms,
                    retrieval_method=result.retrieval_method,
                )
                # TASK-03: 语义层 miss 埋点（hit=False；埋点异常不影响主链路）
                _emit_learning_metric("record_semantic_query", hit=False)
                return None

            top1 = result.matches[0]
            # 【变易】二次校验阈值 — 防御 SkillLoader.match 未过滤的低分候选
            # 不依赖 SkillLoader.match 内部过滤行为，orchestrator 层面独立把控阈值
            if top1.score < min_score:
                # 任务6: 统一层日志（语义层未命中, 中间结果 → DEBUG, 含 top1 分数作为决策依据）
                log_layer_result(
                    LAYER_SEMANTIC, DECISION_MISS, trace_id,
                    level=logging.DEBUG,
                    action='orchestrator.semantic.miss',
                    message='[语义层] 未命中 (top1 score=%.3f < min_score=%.2f, %.2fms, method=%s)' % (
                        top1.score, min_score, elapsed_ms, result.retrieval_method),
                    duration_ms=elapsed_ms,
                    score=top1.score,
                    retrieval_method=result.retrieval_method,
                )
                # TASK-03: 语义层 miss 埋点（hit=False；埋点异常不影响主链路）
                _emit_learning_metric("record_semantic_query", hit=False)
                return None
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.hit', 'trace_id_ctx': trace_id, 'message': '[语义层] 命中 top1=%s score=%.3f (%d 命中, %.2fms, method=%s, reranked=%s, fallback=%s)' % (top1.skill_id, top1.score, len(result.matches), elapsed_ms, result.retrieval_method, result.reranked, result.fallback_used)}))
            # 【不易】埋点后移（P0 修复）：仅在 instruction 加载成功且非空后才记录 semantic，
            # 避免 load_instruction 失败 / 空 instruction 降级 LLM 时与 L418 llm 埋点双重计数。

            # 加载 top1 技能的 instruction（Layer 2）— 命中后短路返回的关键
            # 【不易】load_instruction 失败或返回空 → 降级 LLM（不抛异常）
            try:
                instr_data = svc.loader.load_instruction(top1.skill_id)
                if isinstance(instr_data, dict):
                    instruction = instr_data.get("instruction", "") or ""
                else:
                    instruction = str(instr_data) if instr_data else ""
            except Exception as instr_e:
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.instruction_failed', 'trace_id_ctx': trace_id, 'message': '[语义层] load_instruction 失败，降级 LLM: skill=%s err=%s' % (top1.skill_id, instr_e)}))
                # 任务6: 统一层日志（instruction 加载失败 → 降级算未命中, 中间结果 → DEBUG）
                log_layer_result(
                    LAYER_SEMANTIC, DECISION_MISS, trace_id,
                    level=logging.DEBUG,
                    action='orchestrator.semantic.instruction_failed',
                    message='[语义层] load_instruction 失败，降级 LLM: skill=%s' % (top1.skill_id,),
                    duration_ms=elapsed_ms,
                    score=top1.score,
                    skill_id=top1.skill_id,
                )
                # TASK-03: 语义层 miss 埋点（hit=False；埋点异常不影响主链路）
                _emit_learning_metric("record_semantic_query", hit=False)
                return None

            if not instruction.strip():
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.empty_instruction', 'trace_id_ctx': trace_id, 'message': '[语义层] instruction 为空，降级 LLM: skill=%s' % (top1.skill_id,)}))
                # 任务6: 统一层日志（检索命中但 instruction 为空 → 降级算未命中, 中间结果 → DEBUG）
                log_layer_result(
                    LAYER_SEMANTIC, DECISION_MISS, trace_id,
                    level=logging.DEBUG,
                    action='orchestrator.semantic.empty_instruction',
                    message='[语义层] instruction 为空，降级 LLM: skill=%s' % (top1.skill_id,),
                    duration_ms=elapsed_ms,
                    score=top1.score,
                    skill_id=top1.skill_id,
                )
                # TASK-03: 语义层 miss 埋点（hit=False；埋点异常不影响主链路）
                _emit_learning_metric("record_semantic_query", hit=False)
                return None

            # 记录 trace span
            if trace_id:
                try:
                    trace_store.add_span(trace_id, TraceSpan(
                        span_id=f"{trace_id}_semantic",
                        operation="semantic_match",
                        start_time=ts_sem, end_time=time.time(),
                        duration_ms=elapsed_ms,
                        status="hit",
                        metadata={
                            "top1_skill": top1.skill_id,
                            "top1_score": top1.score,
                            "match_count": len(result.matches),
                            "retrieval_method": result.retrieval_method,
                            "instruction_len": len(instruction),
                        },
                    ))
                except Exception:
                    pass

            # 缓存 skill_ids 供 _call_llm 增强上下文（向后兼容现有属性）
            self._semantic_matched_skills = [m.skill_id for m in result.matches]

            # 【不易】语义层埋点（P0 修复后移至此）：仅在确认命中（instruction 加载成功且非空）
            # 后记录，守 INV-2（业务结果已确定后才埋点）。
            # 上方 load_instruction 失败和空 instruction 的降级路径已 return None，不会执行本行。
            _record_intent_layer("semantic")
            # 任务6: 统一层日志（语义层命中, 决策点 INFO, 含 top1 score 作为决策依据）
            log_layer_result(
                LAYER_SEMANTIC, DECISION_HIT, trace_id,
                message='[语义层] 命中 top1=%s score=%.3f (%d 命中, %.2fms, method=%s)' % (
                    top1.skill_id, top1.score, len(result.matches), elapsed_ms,
                    result.retrieval_method),
                duration_ms=elapsed_ms,
                score=top1.score,
                skill_id=top1.skill_id,
                retrieval_method=result.retrieval_method,
                reranked=result.reranked,
                fallback_used=result.fallback_used,
            )
            # TASK-03: 语义层命中埋点（hit=True 计入 skill 命中率与 token 复用；
            # 埋点异常不影响主链路）
            _emit_learning_metric(
                "record_semantic_query", hit=True,
                saved_tokens=_get_learning_saved_estimate(),
            )
            # 【排查】打印 semantic 埋点触发时的 total 计数值 + instruction 加载状态
            # 验证两件事：(1) 分母同步——ratio 总和恒 = 1.0；(2) INV-2——instruction 已成功加载才埋点。
            # 注：能执行到此行说明 instruction 已加载成功且非空（上方失败/空路径已 return None），
            #     故 instruction_loaded 恒为 True，此字段用于 dashboard 显式过滤"埋点时机正确性"。
            try:
                from agent.monitoring.prometheus import _intent_layer_counts as _ilc
                _sem_total = sum(_ilc.values())
                logger.info(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator.semantic.metric_total',
                    'trace_id_ctx': trace_id,
                    'message': '[埋点] semantic 触发, total=%d, counts=%s, skill=%s, score=%.3f, instr_len=%d, instr_loaded=success' % (
                        _sem_total, dict(_ilc), top1.skill_id, top1.score, len(instruction)
                    ),
                    'metric_total': _sem_total,
                    'layer_counts': dict(_ilc),
                    'skill_id': top1.skill_id,
                    'top1_score': float(top1.score),
                    'instruction_len': len(instruction),
                    'instruction_loaded': True,
                }))
            except Exception:
                pass

            return {
                "output": instruction,
                "skill_id": top1.skill_id,
                "score": top1.score,
                "retrieval_method": result.retrieval_method,
                "reranked": result.reranked,
                "fallback_used": result.fallback_used,
                "elapsed_ms": elapsed_ms,
            }

        except Exception as e:
            elapsed_ms = (time.perf_counter() - _ts_sem_pf) * 1000
            # TD-2: 语义层异常独立计层（与 semantic 互斥；与后续 llm 埋点不同阶段，不双计）
            _record_intent_layer("semantic_failed")
            # 【不易】语义层任何异常都降级到 LLM，不阻断主链路
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.error', 'trace_id_ctx': trace_id, 'message': '[语义层] 异常降级到 LLM (%.2fms): %s' % (elapsed_ms, e)}))
            # 任务6: 统一层日志（语义层异常 → WARNING）
            log_layer_result(
                LAYER_SEMANTIC, DECISION_ERROR, trace_id,
                level=logging.WARNING,
                action='orchestrator.semantic.error',
                message='[语义层] 异常降级到 LLM',
                duration_ms=elapsed_ms,
                error=str(e)[:200],
            )
            # 【变易】发送告警到监控系统，便于线上排查（上报失败不影响主链路）
            try:
                if _MONITORING_AVAILABLE:
                    collector = get_metrics_collector()
                    collector.increment_counter("count.orchestrator.semantic.error")
                if self._error_reporter:
                    self._error_reporter.report_error(
                        error=e, level=AlertLevel.WARNING,
                        context={
                            'layer': 'semantic',
                            'user_input': user_input[:200],
                            'trace_id': trace_id,
                            'elapsed_ms': round(elapsed_ms, 2),
                            'fallback': 'llm',
                            'session_id': _sid,
                        },
                    )
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.error_reported', 'trace_id_ctx': trace_id, 'message': '[语义层] 异常已上报监控系统'}))
            except Exception as report_error:
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.semantic.report_failed', 'trace_id_ctx': trace_id, 'message': '[语义层] 告警上报失败（不影响主链路）: %s' % (report_error,)}))
            return None

    # ════════════════════════════════════════════════════════════════════
    #  主链路拒识 — 未知意图判定（三层漏斗第 3 层，LLM 调用之前）
    # ════════════════════════════════════════════════════════════════════

    # 拒识配置硬编码默认值（与 config.yaml orchestrator.reject 同源）
    _REJECT_DEFAULTS: Dict[str, Any] = {
        "enabled": True,
        "threshold": 0.3,
        "llm_min_confidence": 0.5,
    }

    @classmethod
    def _load_reject_config(cls) -> Dict[str, Any]:
        """读取拒识配置 — 优先级: 环境变量 > config.yaml > 硬编码默认值

        架构层级：[TLM-L2] 拒识配置加载

        分层配置架构（与 _load_semantic_layer_config 同源模式）:
            层0: 硬编码默认值（_REJECT_DEFAULTS，最终兜底）
            层1: config.yaml orchestrator.reject（业务配置主源）
            层2: 环境变量（运维 hotfix 覆盖，优先级最高）

        config.yaml 路径: orchestrator.reject.{enabled,threshold,llm_min_confidence}
        环境变量: ORCHESTRATOR_REJECT_ENABLED / ORCHESTRATOR_REJECT_THRESHOLD
                  / ORCHESTRATOR_LLM_MIN_CONFIDENCE

        【不易】硬编码默认值作为最终兜底，config.yaml 缺失/解析失败不影响主链路
        【变易】复用 _SEM_CONFIG_PATH（共享 config.yaml 路径，避免重复初始化）
        【简易】无 SQLite 持久化（拒识配置无需热更，简化实现）
        """
        config = dict(cls._REJECT_DEFAULTS)

        # 层1: config.yaml（复用 _SEM_CONFIG_PATH，避免重复路径初始化）
        try:
            if cls._SEM_CONFIG_PATH is None:
                from pathlib import Path
                cls._SEM_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if cls._SEM_CONFIG_PATH.exists():
                import yaml as _yaml
                with open(cls._SEM_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                reject_cfg = (data.get("orchestrator", {}) or {}).get("reject", {}) or {}
                # 仅覆盖已知键，类型与默认值一致才接受
                for key in cls._REJECT_DEFAULTS:
                    if key in reject_cfg and reject_cfg[key] is not None:
                        config[key] = reject_cfg[key]
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.reject.config.fallback', 'message': '[拒识] config.yaml 读取失败，降级到默认值: %s' % (e,)}))

        # 层2: 环境变量覆盖（最高优先级，运维 hotfix）
        env_enabled = os.environ.get("ORCHESTRATOR_REJECT_ENABLED")
        if env_enabled is not None and env_enabled.strip():
            config["enabled"] = env_enabled.strip().lower() in ("true", "1", "yes")

        env_threshold = os.environ.get("ORCHESTRATOR_REJECT_THRESHOLD")
        if env_threshold is not None and env_threshold.strip():
            try:
                config["threshold"] = float(env_threshold.strip())
            except (ValueError, TypeError):
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.reject.config.invalid_threshold', 'message': '[拒识] ORCHESTRATOR_REJECT_THRESHOLD 非法值已忽略: %s' % (env_threshold,)}))

        env_llm_conf = os.environ.get("ORCHESTRATOR_LLM_MIN_CONFIDENCE")
        if env_llm_conf is not None and env_llm_conf.strip():
            try:
                config["llm_min_confidence"] = float(env_llm_conf.strip())
            except (ValueError, TypeError):
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.reject.config.invalid_llm_confidence', 'message': '[拒识] ORCHESTRATOR_LLM_MIN_CONFIDENCE 非法值已忽略: %s' % (env_llm_conf,)}))

        return config

    # TASK-01：规划接线（wire）配置硬编码默认值（与 config.yaml planning.wire_* 同源）
    _WIRE_DEFAULTS: Dict[str, Any] = {
        "enabled": False,            # 灰度开关默认 false：未开启前生产行为与现状逐字节等价
        "min_complexity": "COMPLEX",  # 触发规划的最低复杂度（TRIVIAL/SIMPLE/NORMAL/COMPLEX）
        "timeout_seconds": 30,        # 规划调用超时（秒），超时回退 LLM
    }

    @classmethod
    def _load_planning_wire_config(cls) -> Dict[str, Any]:
        """读取规划接线配置（TASK-01）— 优先级: 环境变量 > config.yaml > 硬编码默认值

        架构层级：[TLM-L2] 规划接线配置加载（与 _load_reject_config 同源模式）

        config.yaml 路径: planning.wire_{enabled,min_complexity,timeout_seconds}
        环境变量: PLANNING_WIRE_ENABLED / PLANNING_WIRE_MIN_COMPLEXITY
                  / PLANNING_WIRE_TIMEOUT_SECONDS

        【不易】硬编码默认值作为最终兜底，config.yaml 缺失/解析失败不影响主链路；
               wire_enabled 默认 false（灰度开关），未开启前接线分支整体 inert。
        """
        config = dict(cls._WIRE_DEFAULTS)

        # 层1: config.yaml（复用 _SEM_CONFIG_PATH，避免重复路径初始化）
        try:
            if cls._SEM_CONFIG_PATH is None:
                from pathlib import Path
                cls._SEM_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if cls._SEM_CONFIG_PATH.exists():
                import yaml as _yaml
                with open(cls._SEM_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                plan_cfg = (data.get("planning", {}) or {})
                if plan_cfg.get("wire_enabled") is not None:
                    config["enabled"] = bool(plan_cfg.get("wire_enabled"))
                if plan_cfg.get("wire_min_complexity") is not None:
                    config["min_complexity"] = str(plan_cfg.get("wire_min_complexity"))
                if plan_cfg.get("wire_timeout_seconds") is not None:
                    config["timeout_seconds"] = float(plan_cfg.get("wire_timeout_seconds"))
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.planning_wire.config.fallback', 'message': '[规划接线] config.yaml 读取失败，降级到默认值: %s' % (e,)}))

        # 层2: 环境变量覆盖（最高优先级，运维 hotfix）
        env_enabled = os.environ.get("PLANNING_WIRE_ENABLED")
        if env_enabled is not None and env_enabled.strip():
            config["enabled"] = env_enabled.strip().lower() in ("true", "1", "yes")
        env_min = os.environ.get("PLANNING_WIRE_MIN_COMPLEXITY")
        if env_min is not None and env_min.strip():
            config["min_complexity"] = env_min.strip().upper()
        env_timeout = os.environ.get("PLANNING_WIRE_TIMEOUT_SECONDS")
        if env_timeout is not None and env_timeout.strip():
            try:
                config["timeout_seconds"] = float(env_timeout.strip())
            except (ValueError, TypeError):
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.planning_wire.config.invalid_timeout', 'message': '[规划接线] PLANNING_WIRE_TIMEOUT_SECONDS 非法值已忽略: %s' % (env_timeout,)}))

        return config

    # TASK-02：学习机制接线（反思持久化 + 认知规则评估）配置硬编码默认值
    _LEARNING_DEFAULTS: Dict[str, Any] = {
        # 反思产物写入检索面开关（观察模式）：false 保持现状仅写内存/日志
        "reflection_persist": False,
        # ReflectionEngine 规则评估开关（保守模式）：false 不评估；true 只记录不干预
        "critic_evaluation_enabled": False,
    }

    @staticmethod
    def _parse_bool_flag(value: Any) -> bool:
        """yaml 开关值安全解析（TASK-02）— 布尔原样返回；字符串按 true/1/yes 判 True，
        其余（含 "false"/"0"，运维误加引号场景）判 False。
        【不易】保护 config.yaml false 默认语义：bool("false")==True 会把关闭开关误读为开启。"""
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("true", "1", "yes")

    @classmethod
    def _load_learning_config(cls) -> Dict[str, Any]:
        """读取学习机制接线配置（TASK-02）— 优先级: 环境变量 > config.yaml > 硬编码默认值

        架构层级：[TLM-L2] 学习接线配置加载（与 _load_planning_wire_config 同源模式）

        config.yaml 路径: learning.reflection_persist / features.critic_evaluation_enabled
        环境变量: LEARNING_REFLECTION_PERSIST / CRITIC_EVALUATION_ENABLED

        【不易】硬编码默认值作为最终兜底，config.yaml 缺失/解析失败不影响主链路；
               两开关默认 false（观察/保守模式），未开启前接线分支整体 inert。
        """
        config = dict(cls._LEARNING_DEFAULTS)

        # 层1: config.yaml（复用 _SEM_CONFIG_PATH，避免重复路径初始化）
        try:
            if cls._SEM_CONFIG_PATH is None:
                from pathlib import Path
                cls._SEM_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config.yaml"
            if cls._SEM_CONFIG_PATH.exists():
                import yaml as _yaml
                with open(cls._SEM_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = _yaml.safe_load(f) or {}
                learning_cfg = data.get("learning", {}) or {}
                features_cfg = data.get("features", {}) or {}
                if learning_cfg.get("reflection_persist") is not None:
                    config["reflection_persist"] = cls._parse_bool_flag(learning_cfg.get("reflection_persist"))
                if features_cfg.get("critic_evaluation_enabled") is not None:
                    config["critic_evaluation_enabled"] = cls._parse_bool_flag(features_cfg.get("critic_evaluation_enabled"))
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.learning.config.fallback', 'message': '[学习接线] config.yaml 读取失败，降级到默认值: %s' % (e,)}))

        # 层2: 环境变量覆盖（最高优先级，运维 hotfix）
        env_reflect = os.environ.get("LEARNING_REFLECTION_PERSIST")
        if env_reflect is not None and env_reflect.strip():
            config["reflection_persist"] = env_reflect.strip().lower() in ("true", "1", "yes")
        env_critic = os.environ.get("CRITIC_EVALUATION_ENABLED")
        if env_critic is not None and env_critic.strip():
            config["critic_evaluation_enabled"] = env_critic.strip().lower() in ("true", "1", "yes")

        # 【排查】解析后最终生效值（含环境变量来源，一条日志看清三层优先级结果）
        logger.info(log_dict({'module_name': 'orchestrator',
                              'action': 'orchestrator.learning.config.loaded',
                              'message': '[学习接线] 配置生效: reflection_persist=%s critic_evaluation_enabled=%s (env_reflect=%r env_critic=%r)' % (
                                  config['reflection_persist'], config['critic_evaluation_enabled'],
                                  env_reflect, env_critic)}))

        return config

    def _should_reject(
        self,
        intent: Optional[str],
        confidence: Any,
        semantic_result: Optional[Dict[str, Any]],
    ) -> Tuple[bool, str]:
        """未知意图拒识判定 — 规则层+语义层双未命中 + 语义最高分 < 阈值

        架构层级：三层漏斗第 3 层（拒识层，LLM 调用之前）

        判定逻辑（隐式判定，守【简易】）:
        - 规则层未命中：执行到此处即 WorkflowEngine + 模板层均未命中（已隐含）
        - 语义层未命中：semantic_result is None
        - 语义最高分 < 阈值：semantic_result is None 隐含 top1.score < semantic_layer.min_score
          （_semantic_layer_match 已用 min_score 过滤低分候选）
        - 规则层置信度低：confidence 非 HIGH（IntentRouter 返回 Confidence 枚举）

        【不易】拒识返回统一文案 + 转人工建议，不抛异常
        【变易】阈值通过 ORCHESTRATOR_REJECT_THRESHOLD 配置，默认 0.3
        【简易】隐式判定，不修改 _semantic_layer_match 返回契约

        Args:
            intent: IntentRouter.classify 返回的意图字符串
            confidence: IntentRouter.classify 返回的 Confidence 枚举
            semantic_result: _semantic_layer_match 返回的 dict（含 score）或 None

        Returns:
            (should_reject, reason): should_reject=True 时 reason 含拒识原因（供日志记录）
        """
        cfg = self._load_reject_config()
        if not cfg["enabled"]:
            # 【日志】拒识总开关关闭，记录便于排查"为何未拒识"
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.should_reject.disabled', 'message': '[拒识判定] 拒识已禁用 (ORCHESTRATOR_REJECT_ENABLED=false)，放行到 LLM'}))
            return False, "reject_disabled"

        threshold = float(cfg["threshold"])

        # 条件1：语义层未命中（隐含语义最高分 < min_score，即 < 阈值默认 0.3）
        # semantic_result 非 None 表示语义层已命中，不应拒识
        if semantic_result is not None:
            _sem_score = semantic_result.get('score', 0.0) if isinstance(semantic_result, dict) else 0.0
            # 【日志】语义层命中，记录分数便于排查"为何放行"
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.should_reject.semantic_hit', 'message': '[拒识判定] 语义层已命中 (score=%.3f >= threshold=%.2f)，放行' % (_sem_score, threshold), 'semantic_score': _sem_score, 'reject_threshold': threshold}))
            return False, "semantic_hit"

        # 条件2：规则层置信度非 HIGH（confidence 为 Confidence 枚举）
        # Confidence 枚举名兼容多种实现（HIGH/HIGH_CONFIDENCE 等），用字符串包含判定
        _conf_str = str(confidence).upper() if confidence is not None else "UNKNOWN"
        _is_high = ("HIGH" in _conf_str)
        if _is_high:
            # 【日志】规则层高置信度，记录 confidence 值便于排查"为何放行"
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.should_reject.rule_high_confidence', 'message': '[拒识判定] 规则层高置信度 (%s)，放行到 LLM' % (_conf_str,), 'confidence': _conf_str}))
            return False, "rule_high_confidence"

        # 双未命中 + 低置信度 → 拒识
        # 语义层 None 时无法获取分数，按隐式判定（top1.score < min_score ≤ threshold 期望值）
        # 若 min_score 与 threshold 解耦调优，此处的隐式判定仍保守正确（语义层未命中即拒识候选）
        reason = "rule_and_semantic_both_miss: intent=%s confidence=%s semantic=None threshold=%.2f" % (
            intent, _conf_str, threshold
        )
        # 【日志】拒识触发，记录完整判定上下文（各层分数 + 阈值 + 意图）
        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.should_reject.rejected', 'message': '[拒识判定] 规则层+语义层双未命中 + 低置信度 → 拒识: %s' % (reason,), 'intent': intent, 'confidence': _conf_str, 'semantic_result': semantic_result, 'reject_threshold': threshold}))
        return True, reason

    # ════════════════════════════════════════════════════════════════════
    #  健康检查
    # ════════════════════════════════════════════════════════════════════

    def check_health(self) -> list:
        """检查我的身体状态（感知层）"""
        from sensor.sensor_reading import SensorReading
        readings = self.body.collect_quick()
        self._current_mode = self._behavior.evaluate(readings)
        self._last_health_check = time.time()

        if self._v2_lifetrace and self._trace_recorder:
            for reading in readings:
                self._trace_recorder.record_sensor(
                    sensor_type=reading.sensor_name,
                    data={
                        "value": reading.value,
                        "unit": reading.unit,
                        "severity": reading.severity,
                    },
                    metadata={"interaction_id": self._interaction_count},
                )

        return readings

    def get_behavior_mode(self):
        """获取我当前的行为模式"""
        return self._current_mode

    def _check_context_usage(self) -> Optional[dict]:
        """检查上下文使用率和压缩退化程度，返回警告信息

        Returns:
            {"level": "info"|"warning"|"critical", "pct": float, "message": str, ...}
        """
        if not self._memory:
            return None
        try:
            context = self._memory.get_context(token_limit=self._memory_token_limit)
            if not context:
                return None
            total_tokens = self._memory._token_counter.count_messages(context)
            limit = self._memory_token_limit
            pct = (total_tokens / limit) * 100
            compress_rounds = self._memory.compress_rounds

            if compress_rounds >= 5:
                return {
                    "level": "critical",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": (
                        f"已压缩 {compress_rounds} 次，摘要退化明显"
                        f"（当前使用 {pct:.0f}%），建议创建新会话继续对话"
                    ),
                }
            if compress_rounds >= 3:
                return {
                    "level": "warning",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": (
                        f"已压缩 {compress_rounds} 次，建议准备切换到新会话"
                    ),
                }

            if pct >= 95:
                return {
                    "level": "critical",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%，即将耗尽，建议创建新会话继续对话",
                }
            elif pct >= 80:
                return {
                    "level": "warning",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%，建议准备切换到新会话",
                }
            elif pct >= 60:
                return {
                    "level": "info",
                    "pct": round(pct, 1),
                    "compress_rounds": compress_rounds,
                    "message": f"上下文已使用 {pct:.0f}%",
                }
            return None
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._check_context_usage.log', 'message': '检查上下文使用率时出错: %s' % (e,)}))
            return None

    @staticmethod
    def _extract_keywords(text: str, max_keywords: int = 3) -> list:
        """从文本中提取关键词

        规则：
        - 空字符串/纯特殊字符 → []
        - 短句（≤4字）→ 整体作为一个关键词
        - 长句 → 按标点/空格分割，过滤停用词与过短片段，去重
        """
        if not text or not text.strip():
            return []

        # 纯特殊字符（无中文/字母数字）→ []
        if not _re.search(r'[\w\u4e00-\u9fff]', text):
            return []

        # 短句（≤4个字符）→ 整体作为关键词
        if len(text) <= 4:
            return [text]

        # 长句 → 按标点/空格分割
        segments = _re.split(r'[，。！？；：、,.\!?;:\s]+', text)
        stopwords = {'帮我', '一下', '这个', '的', '了', '是', '在', '我',
                     '你', '他', '她', '它', '和', '与', '及', '并', '而'}
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if not seg or len(seg) < 2:
                continue
            if seg in stopwords:
                continue
            if seg not in keywords:
                keywords.append(seg)
            if len(keywords) >= max_keywords:
                break

        # 无标点长句分割后只有一个片段 → 直接返回
        if not keywords:
            return [text]

        return keywords[:max_keywords]

    # ════════════════════════════════════════════════════════════════════
    #  LLM 调用
    # ════════════════════════════════════════════════════════════════════

    def _call_llm(self, user_input: str, body_status: str) -> str:
        """调用 LLM 生成响应（集成工作记忆 + Token 预算分配）"""
        mode = self._current_mode
        profile = self._behavior.profile

        self._set_thinking_mode()

        # ── 1. 构建 system prompt ──
        memory_context = ""
        try:
            summary_data = self._memory.load_summary()
            if summary_data and summary_data[0]:
                memory_context = summary_data[0][:300]
            else:
                context_messages = self._memory.get_context(token_limit=5000)
                if context_messages:
                    recent = context_messages[-2:]
                    lines = []
                    for m in recent:
                        if m.get('content'):
                            lines.append("%s: %s" % (m['role'], m['content'][:100]))
                    memory_context = " | ".join(lines)
        except Exception:
            pass
        if not memory_context:
            memory_context = "（暂无历史对话）"

        # 简短工作记忆
        wm_text = ""
        try:
            wm = self._memory.get_working_memory()
            if wm:
                items = []
                for k, v in wm.items():
                    if k == "interaction_count":
                        continue
                    if isinstance(v, list):
                        items.append("%s: %s" % (k, '; '.join(str(x)[:60] for x in v[-3:])))
                    else:
                        items.append("%s: %s" % (k, str(v)[:80]))
                if items:
                    combined = " | ".join(items)
                    if len(combined) > 200:
                        combined = combined[:200] + "..."
                    wm_text = "\n[工作中] " + combined
        except Exception:
            pass

        tool_status = self._build_tool_status_text()
        skill_instructions = self._build_skill_instructions()

        _sp_template = _get_template()
        system_prompt = _sp_template.format(
            current_date=f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日",
            body_status=body_status,
            mode_name=profile.label,
            mode_description=profile.description,
            memory_context=memory_context,
            tool_status=tool_status,
            skill_instructions=skill_instructions,
        )
        if wm_text:
            system_prompt += wm_text

        # ── ContextAssembler 旁路注入（CEL，观察模式；异常静默降级零影响）──
        # 标准路径（_call_llm）与 V2 路径（_call_llm_v2）双覆盖，
        # 保证无论 lifetrace/persona 开关状态，主链路均注入组装产物
        _ctx_extra = self._context_assembler_extra(user_input)
        if _ctx_extra:
            system_prompt = system_prompt + "\n\n" + _ctx_extra

        # ── System prompt Token 预算检查 ──
        try:
            _sp_tokens = self._memory._token_counter.count(system_prompt)
            _sp_budget = 10000
            if _sp_tokens > _sp_budget:
                logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.token', 'message': '[Token] system prompt %d tokens 超预算 %d，截断工具状态' % (_sp_tokens, _sp_budget)}))
                _brief_tools = (tool_status[:300] + "\n...（已截断）") if len(tool_status) > 300 else tool_status
                system_prompt = _sp_template.format(
                    current_date=f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日",
                    body_status=body_status,
                    mode_name=profile.label,
                    mode_description=profile.description,
                    memory_context=memory_context,
                    tool_status=_brief_tools,
                    skill_instructions="",
                )
                if wm_text:
                    system_prompt += wm_text
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.token', 'message': '[Token] system prompt: %d tokens (预算 %d)' % (_sp_tokens, _sp_budget)}))
        except Exception:
            pass

        # ── 2. 组装上下文消息 ──
        messages = []
        # 固定 system 消息前置（提升 LLM 前缀缓存命中率）
        if self._tool_calling_service:
            messages.append({
                "role": "system",
                "content": (
                    "⚡ 立即检查：用户这句话需要工具吗？如果需要，直接发起函数调用。"
                    "绝对禁止只发文字描述你将要做的操作。"
                    "没调用工具 = 没执行。立即行动。"
                ),
            })
        # 动态历史消息
        try:
            recent = self._memory._storage.load_recent_messages(limit=50)
            summary_data = self._memory.load_summary()
            summary_text = summary_data[0] if summary_data else None
            tool_results = getattr(self, '_last_tool_steps', [])

            budget_context = self._memory.get_budget_context(
                recent_messages=recent,
                summary_text=summary_text,
                tool_results=tool_results,
            )
            messages.extend(budget_context)
        except Exception as e:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.budget', 'message': 'Budget context assembly failed: %s, falling back' % (e,)}))
            try:
                context = self._memory.get_context(token_limit=self._memory_token_limit)
                if context:
                    messages.extend(context)
            except Exception:
                pass

        messages.append({"role": "user", "content": user_input})

        logger.debug(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator._call_llm.prompt_order',
            'message': '[PromptOrder] fixed=[tool_urge@idx0] dynamic=[budget_context@idx1-%d, user_input@idx%d]' % (
                len(messages) - 2, len(messages) - 1
            ),
            'messages_count': len(messages),
            'has_tool_urge': bool(self._tool_calling_service),
        }))

        if self._llm:
            try:
                self._last_tool_steps = []
                self._current_tool_steps = []

                from agent import tools as _tools
                _whitelist = self._get_enabled_tools_whitelist()
                if self._is_smart_tool_selection_enabled():
                    try:
                        _smart_tools = hybrid_select_tools(user_input, _whitelist) or get_tools_for_input(user_input, _whitelist)
                        if _smart_tools:
                            _whitelist = _smart_tools
                            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.log', 'message': '[工具路由] 智能选择: %d/%d 个工具' % (len(_smart_tools), len(_tools.list_tools()))}))
                    except Exception as _e:
                        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.log', 'message': '工具路由失败: %s' % (_e,)}))
                _tool_defs = _tools.get_tool_defs(whitelist=_whitelist)
                # 【Schema 裁剪】tool_router 选定后裁剪,守 [不易] required 不动、deprecated 移除
                try:
                    from agent.tool_schema_pruner import prune_tool_defs
                    _orig_tool_count = len(_tool_defs)
                    _tool_defs = prune_tool_defs(
                        _tool_defs,
                        intent_context={"selected_tools": list(_whitelist or [])},
                    ) or _tool_defs
                    _pruned_count = _orig_tool_count - len(_tool_defs)
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.schema_prune', 'message': '[SchemaPruner] 工具数 %d → %d (移除 %d 个工具级 deprecated)' % (_orig_tool_count, len(_tool_defs), _pruned_count)}))
                except Exception as _spe:
                    logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.schema_prune_failed', 'message': '[SchemaPruner] 裁剪失败降级原 tool_defs: %s' % (_spe,)}))
                _client = self._llm._get_client()

                # 智能调度：选择最合适的模型
                _selected_llm, _selected_model = self._select_model_for_request(user_input)
                _use_pro = _selected_model != self._llm.model
                if _use_pro and self._llm_pro:
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm._call_llm', 'message': '[_call_llm] 调度到深度模型: %s (主模型: %s)' % (_selected_model, self._llm.model)}))
                    _client = self._llm_pro._get_client()
                    _working_model = _selected_model
                else:
                    _working_model = self._llm.model
                    logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm._call_llm', 'message': '[_call_llm] 使用主模型: %s (pro可用=%s)' % (_working_model, self._llm_pro is not None)}))

                _working = list(messages)
                # 【Dynamic Few-shot 注入】从 tool_fewshot_store 采样脱敏样本,插入 user_input 之前
                # 【不易】 Few-shot 仅来自真实成功调用(由 ToolFewshotStore 保证);注入位置=user_input 前(动态区后置)
                try:
                    from agent.tool_fewshot_store import ToolFewshotStore
                    from agent.orchestrator.prompt_builder import build_fewshot_message
                    _fs_whitelist = list(_whitelist or [])
                    logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_sample_start', 'message': '[Fewshot] 采样开始: whitelist_size=%d whitelist=%s' % (len(_fs_whitelist), _fs_whitelist)}))
                    _fs_samples = ToolFewshotStore.instance().sample_for_tools(_fs_whitelist)
                    _fs_tools_with_samples = sum(1 for v in _fs_samples.values() if v)
                    _fs_total = sum(len(v) for v in _fs_samples.values())
                    logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_sample_done', 'message': '[Fewshot] 采样完成: tools_with_samples=%d total_samples=%d samples=%s' % (_fs_tools_with_samples, _fs_total, dict(_fs_samples))}))
                    _fmsg = build_fewshot_message(_fs_samples)
                    if _fmsg is None:
                        logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_build_none', 'message': '[Fewshot] build_fewshot_message 返回 None(无样本或降级),跳过注入'}))
                    else:
                        if _working:
                            _inject_pos = len(_working) - 1
                            _working.insert(_inject_pos, _fmsg)
                            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_injected', 'message': '[Fewshot] 注入成功: position=user_input_before(idx=%d) working_count=%d' % (_inject_pos, len(_working))}))
                        else:
                            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_no_user_input', 'message': '[Fewshot] _working 为空,无法注入(无 user_input)'}))
                except Exception as _fse:
                    logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'fewshot_inject_failed', 'message': '[Fewshot] 注入失败降级跳过: %s: %s traceback=%s' % (type(_fse).__name__, _fse, __import__('traceback').format_exc())}))
                _reasoning = None
                _max_rounds = 3
                response = ""

                # 根据模型类型自适应输出 token 限制
                _model_lower = (_working_model or "").lower()
                if any(k in _model_lower for k in ("pro", "ultra", "reasoner", "opus",
                                                   "claude-4", "gpt-4-turbo", "o1", "o3")):
                    _max_output = 16384
                else:
                    _max_output = 8192

                for _round_idx in range(_max_rounds):
                    _api_msgs = [{"role": "system", "content": system_prompt}] + _working
                    _kwargs = {
                        "model": _working_model,
                        "messages": _api_msgs,
                        "max_tokens": _max_output,
                        "temperature": 0.3,
                    }
                    if _tool_defs:
                        _kwargs["tools"] = _tool_defs
                    if _round_idx == _max_rounds - 1:
                        _kwargs.pop("tools", None)
                        _working.append({
                            "role": "system",
                            "content": "这是最后一轮，请根据之前获取到的信息给出完整总结。",
                        })
                        _api_msgs = [{"role": "system", "content": system_prompt}] + _working
                        _kwargs["messages"] = _api_msgs

                    _resp = _client.chat.completions.create(**_kwargs)
                    _msg = _resp.choices[0].message

                    _reasoning = _reasoning or getattr(_msg, "reasoning_content", None)
                    if _reasoning:
                        self._last_reasoning = _reasoning

                    if not (hasattr(_msg, 'tool_calls') and _msg.tool_calls):
                        # 检测 XML 格式的工具调用
                        _xml_tools = []
                        if _msg.content and _re.search(r'<[^>]*tool_calls[^>]*>', _msg.content):
                            try:
                                _xml_tools = ToolCallingService._extract_xml_tool_calls(_msg.content)
                            except Exception as _xml_e:
                                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm._call_llm', 'message': '[_call_llm] XML 工具提取失败: %s' % (_xml_e,)}))
                        if _xml_tools:
                            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm._call_llm', 'message': '[_call_llm] 检测到 XML 格式工具调用: %d 个' % (len(_xml_tools),)}))
                            _assistant_tc = []
                            _tool_results = []
                            for _xc in _xml_tools:
                                _fn_name = _xc["function"]["name"]
                                _fn_args = json.loads(_xc["function"]["arguments"])
                                _tc_id = _xc["id"]
                                _assistant_tc.append(_xc)
                                self._current_tool_steps.append({
                                    "type": "tool_call", "tool": _fn_name,
                                    "args": _fn_args, "id": _tc_id,
                                })
                                try:
                                    _tool_result_data = _tools.call(_fn_name, **_fn_args)
                                    _tool_summary = _summarize_tool_result(_fn_name, _tool_result_data)
                                    _status = "success"
                                except Exception as _te:
                                    _tool_summary = f"执行失败: {_te}"
                                    _status = "error"
                                self._current_tool_steps.append({
                                    "type": "tool_result", "tool": _fn_name, "id": _tc_id,
                                    "status": _status, "summary": _tool_summary[:200],
                                })
                                _tool_results.append({
                                    "role": "tool", "tool_call_id": _tc_id,
                                    "content": _tool_summary[:2000],
                                })
                            self._last_tool_steps = list(self._current_tool_steps)
                            _working.append({
                                "role": "assistant", "content": _msg.content,
                                "tool_calls": _assistant_tc,
                            })
                            _working.extend(_tool_results)
                            continue
                        response = _msg.content or _reasoning or ""
                        break

                    _assistant_tc = []
                    _tool_results = []
                    for _tc in _msg.tool_calls:
                        _fn_name = _tc.function.name
                        _fn_args = json.loads(_tc.function.arguments)
                        _tc_id = _tc.id
                        _assistant_tc.append({
                            "id": _tc_id, "type": "function",
                            "function": {"name": _fn_name, "arguments": _tc.function.arguments},
                        })
                        self._current_tool_steps.append({
                            "type": "tool_call", "tool": _fn_name, "args": _fn_args, "id": _tc_id,
                        })
                        try:
                            _tool_result_data = _tools.call(_fn_name, **_fn_args)
                            _tool_summary = _summarize_tool_result(_fn_name, _tool_result_data)
                            _status = "success"
                        except Exception as _te:
                            _tool_summary = f"执行失败: {_te}"
                            _status = "error"
                        self._current_tool_steps.append({
                            "type": "tool_result", "tool": _fn_name, "id": _tc_id,
                            "status": _status, "summary": _tool_summary[:200],
                        })
                        _tool_results.append({
                            "role": "tool", "tool_call_id": _tc_id,
                            "content": json.dumps(_clean_for_json(_tool_result_data),
                                                  ensure_ascii=False)[:2000],
                        })

                    self._last_tool_steps = list(self._current_tool_steps)
                    _working.append({
                        "role": "assistant", "content": _msg.content,
                        "tool_calls": _assistant_tc,
                    })
                    _working.extend(_tool_results)
                else:
                    if not response:
                        _last_summaries = [s.get("summary", "") for s in self._current_tool_steps
                                           if s["type"] == "tool_result"][-3:]
                        response = ("（已获取以下信息：）" + "\n" +
                                    "\n".join(_last_summaries)) if _last_summaries else "（已处理完毕）"

                if profile.response_prefix:
                    response = profile.response_prefix + "\n" + response

                # 兜底：检测 XML 工具调用残留
                if response and _re.search(r'<[^>]*tool_calls[^>]*>', response):
                    logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm._call_llm', 'message': '[_call_llm] 响应中包含 XML 工具调用，使用工具结果摘要替换'}))
                    _fb_summaries = [s.get("summary", "") for s in self._current_tool_steps
                                     if s["type"] == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"

                return response
            except Exception as _e:
                logger.error(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm.llm', 'message': 'LLM 调用失败: %s' % (_e,), 'error': str(_e)}))
                return "（抱歉，处理时遇到了问题: %s）" % str(_e)
        else:
            self._set_thinking_mode("instinct")
            return self._build_offline_response(user_input)

    def _get_user_context(self, session_id: Optional[str] = None,
                          session_mgr=None) -> Optional[str]:
        """获取会话的用户上下文（时区/设备/语言），用于调整说话风格与临场感

        显式参数优先，未传时回退实例全局 _session_id/_session_mgr
        （兼容 CLI 等未接入 SessionManager 的调用方）。

        防御性实现：
        - 无 session_mgr 时返回 None（不注入）
        - 会话不存在或元数据为空时返回 None（优雅降级，不影响对话链路）

        Args:
            session_id: 会话 ID（sess_*），显式传入以根治并发全局覆盖
            session_mgr: SessionManager 实例；None 时回退 getattr(self, '_session_mgr')

        Returns:
            形如 "用户时区: Asia/Shanghai；设备类型: mobile；语言环境: zh-CN" 的文本，或 None
        """
        # ── 来源判定：显式参数优先，未传才回退实例全局属性 ──
        # 显式传入 = 修复后链路（chat(session_id=..., session_mgr=...)），并发安全；
        # 实例回退 = CLI 等未接入 SessionManager 的调用方，仅单会话场景安全。
        _mgr_source = "显式参数"
        if session_mgr is None:
            session_mgr = getattr(self, '_session_mgr', None)
            _mgr_source = "实例回退"
        if session_mgr is None:
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.user_context.skip', 'message': '[user_context] 无 session_mgr（未接入会话元数据），跳过注入'}))
            return None

        _sid_source = "显式参数"
        if not session_id:
            session_id = getattr(self, '_session_id', None)
            _sid_source = "实例回退"
        if not session_id:
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.user_context.skip', 'message': '[user_context] 无 session_id，跳过注入'}))
            return None

        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.user_context.source', 'session_id': session_id, 'session_mgr_source': _mgr_source, 'session_id_source': _sid_source, 'message': '[user_context] 来源: session_mgr=%s, session_id=%s (id=%s)' % (_mgr_source, _sid_source, session_id)}))
        try:
            meta = session_mgr.get_session_metadata(session_id)
            if not meta:
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.user_context.empty', 'session_id': session_id, 'message': '[user_context] 会话无元数据（timezone/device/locale 均空），跳过注入'}))
                return None
            parts = []
            for _k, _label in (("timezone", "用户时区"), ("device_type", "设备类型"), ("locale", "语言环境")):
                if meta.get(_k):
                    parts.append(f"{_label}: {meta[_k]}")
            ctx = "；".join(parts) if parts else None
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.user_context.generated', 'session_id': session_id, 'user_context': ctx, 'message': '[user_context] 生成: %s' % (ctx,)}))
            return ctx
        except Exception as e:
            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._get_user_context.fail', 'message': '[user_context] 获取失败（忽略）: %s' % (e,)}))
            return None

    # ── ContextAssembler 集成（D2D3 替代方案 · CEL 框架；learning.context_assembler.enabled 默认 false）──

    def _load_context_assembler_config(self) -> Dict[str, Any]:
        """读取 learning.context_assembler 配置（config.yaml + 环境变量覆盖，默认关闭）"""
        cfg = {"enabled": False, "token_budget": 3000}
        try:
            import yaml
            with open("config.yaml", "r", encoding="utf-8") as _f:
                raw = yaml.safe_load(_f) or {}
            lc = (raw.get("learning") or {}).get("context_assembler") or {}
            cfg["enabled"] = bool(lc.get("enabled", False))
            cfg["token_budget"] = int(lc.get("token_budget", 3000))
        except Exception:
            pass
        env = os.environ.get("LEARNING_CONTEXT_ASSEMBLER_ENABLED", "").strip().lower()
        if env in ("1", "true", "yes"):
            cfg["enabled"] = True
        return cfg

    def _context_assembler_long_term(self, task: str) -> list:
        """长期检索记忆提供者 — 反思经验文件 data/reflection/{experiences,lessons}.json"""
        chunks = []
        try:
            for name in ("experiences.json", "lessons.json"):
                p = os.path.join("data", "reflection", name)
                if not os.path.exists(p):
                    continue
                with open(p, "r", encoding="utf-8") as _f:
                    data = json.load(_f)
                items = data if isinstance(data, list) else (data.get("items", []) if isinstance(data, dict) else [])
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    note = it.get("note") or it.get("content") or it.get("lesson") or it.get("experience")
                    if not note:
                        continue
                    chunks.append({
                        "layer": "反思经验",
                        "title": "%s·%s" % (name.split(".")[0], it.get("task_type", "general")),
                        "content": str(note),
                    })
        except Exception:
            pass
        return chunks

    def _context_assembler_procedural(self, task: str) -> Tuple[list, Optional[dict]]:
        """程序性记忆提供者 — SkillLoader 真实数据（懒初始化单例）"""
        try:
            if getattr(self, "_ctx_skills_loader", None) is None:
                from agent.skills_mgmt.loader import SkillLoader
                self._ctx_skills_loader = SkillLoader()
            result = self._ctx_skills_loader.match(task, top_k=2)
            skills = []
            for m in result.matches[:2]:
                text = None
                try:
                    instr = self._ctx_skills_loader.load_instruction(m.skill_id) or {}
                    text = instr.get("instruction") or instr.get("body") or instr.get("content")
                except Exception:
                    text = None
                if not text:
                    text = m.description
                skills.append({"skill_id": m.skill_id, "name": m.name, "instruction": text})
            return skills, None
        except Exception:
            return [], None

    def _emit_context_assembler_metric(self, action: str, **kwargs) -> None:
        """ContextAssembler 监控指标埋点（Prometheus，安全降级不影响主链路）

        action:
            - injected: 注入成功（耗时 + 注入 token）
            - empty:    三层全空跳过（仅耗时）
            - degraded: 组装异常降级（计数 + 耗时，告警源）
        """
        try:
            from agent.monitoring.prometheus import (
                record_context_assembler_injected,
                record_context_assembler_degraded,
                record_context_assembler_duration,
            )
            _ms = kwargs["duration_ms"]
            if action == "injected":
                record_context_assembler_injected(_ms, kwargs["tokens"])
            elif action == "degraded":
                record_context_assembler_degraded()
                record_context_assembler_duration(_ms)
            elif action == "empty":
                record_context_assembler_duration(_ms)
        except Exception:
            pass  # 埋点失败不影响主链路（prometheus_client 不可用等）

    def _context_assembler_extra(self, user_input: str, mode: str = "default") -> Optional[str]:
        """ContextAssembler 旁路注入 — 任何异常静默降级返回 None（主链路零影响）

        观察模式日志（结构化，INFO 级实时可见）:
            - injected:   注入成功（含耗时/token/各层命中统计）
            - empty:      三层全空，跳过注入
            - degraded:   组装异常，降级跳过（WARNING）
        """
        _t0 = time.time()
        try:
            cfg = self._load_context_assembler_config()
            if not cfg["enabled"]:
                logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.context_assembler.disabled',
                                       'trace_id_ctx': _trace_id(), 'message': '[ContextAssembler] 未启用，跳过'}))
                return None
            from agent.context.assembler import ContextAssembler
            assembler = ContextAssembler(
                token_budget=int(cfg["token_budget"]),
                working_memory_fn=(
                    (lambda: self._memory.get_context(token_limit=self._memory_token_limit))
                    if getattr(self, "_memory", None) else None
                ),
                long_term_fn=self._context_assembler_long_term,
                procedural_fn=self._context_assembler_procedural,
            )
            ctx = assembler.assemble(user_input, mode=mode)
            if not ctx.memory_sections and not ctx.skill_instructions and not ctx.workflow_hint:
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.context_assembler.empty',
                                      'trace_id_ctx': _trace_id(),
                                      'duration_ms': round((time.time() - _t0) * 1000, 2),
                                      'task_preview': user_input[:60],
                                      'message': '[ContextAssembler] 三层全空，跳过注入'}))
                self._emit_context_assembler_metric(
                    "empty", duration_ms=(time.time() - _t0) * 1000)
                return None
            text = assembler.render_text(ctx)
            logger.info(log_dict({
                'module_name': 'orchestrator', 'action': 'orchestrator.context_assembler.injected',
                'trace_id_ctx': _trace_id(),
                'duration_ms': round((time.time() - _t0) * 1000, 2),
                'task_preview': user_input[:60],
                'enabled': True,
                'injected_chars': len(text),
                'token_total': ctx.total_tokens,
                'token_budget': ctx.budget,
                'truncated': ctx.truncated,
                'layer_tokens': ctx.layer_tokens,
                'skills_hit': [s.get('skill_id') for s in ctx.skill_instructions],
                'workflow_hit': ctx.workflow_hint.get('wf_id') if ctx.workflow_hint else None,
                'reflections_hit': len(ctx.reflection_notes),
                'message': '[ContextAssembler] 旁路注入: %d 字符, token=%d/%d' % (len(text), ctx.total_tokens, ctx.budget),
            }))
            self._emit_context_assembler_metric(
                "injected", duration_ms=(time.time() - _t0) * 1000, tokens=ctx.total_tokens)
            return text
        except Exception as exc:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.context_assembler.degraded',
                                     'trace_id_ctx': _trace_id(),
                                     'duration_ms': round((time.time() - _t0) * 1000, 2),
                                     'error': str(exc),
                                     'message': '[ContextAssembler] 降级跳过（主链路零影响）: %s' % (exc,)}))
            self._emit_context_assembler_metric(
                "degraded", duration_ms=(time.time() - _t0) * 1000)
            return None

    def _call_llm_v2(self, user_input: str, body_status: str, *,
                     session_id: Optional[str] = None,
                     session_mgr=None) -> str:
        """V2 调用 LLM 生成响应（使用 Persona 系统）

        Args:
            user_input: 用户输入
            body_status: 身体状态描述
            session_id: 会话 ID（显式传入，并发安全；None 时回退实例全局）
            session_mgr: SessionManager 实例（显式传入，并发安全）
        """
        profile = self._behavior.profile
        self._set_thinking_mode()

        if self._v2_persona and self._persona_injector:
            memory_context = self._get_lifetrace_context(user_input)
            tool_status_text = self._build_tool_status_text()
            user_context = self._get_user_context(
                session_id=session_id,
                session_mgr=session_mgr,
            )
            system_prompt = self._persona_injector.build_system_prompt(
                body_status=body_status,
                memory_context=memory_context,
                tool_status=tool_status_text,
                user_context=user_context,
            )
        else:
            memory_context = self._get_lifetrace_context(user_input) if self._v2_lifetrace else ""
            tool_status = self._build_tool_status_text()
            skill_instructions = self._build_skill_instructions()
            _sp_template = _get_template()
            system_prompt = _sp_template.format(
                current_date=f"{datetime.now().year}年{datetime.now().month}月{datetime.now().day}日",
                body_status=body_status,
                mode_name=profile.label,
                mode_description=profile.description,
                memory_context=memory_context or "（暂无记忆内容）",
                tool_status=tool_status,
                skill_instructions=skill_instructions,
            )

        # ContextAssembler 旁路注入（learning.context_assembler.enabled 默认 false，观察模式；
        # 任何异常静默降级，主链路零影响）
        _ctx_extra = self._context_assembler_extra(user_input)
        if _ctx_extra:
            system_prompt = system_prompt + "\n\n" + _ctx_extra

        messages = []
        try:
            context = self._memory.get_context(token_limit=self._memory_token_limit)
            if context:
                messages.extend(context)
        except Exception:
            pass

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                if self._tool_calling_service:
                    tools_whitelist = self._get_enabled_tools_whitelist()
                    if self._is_smart_tool_selection_enabled():
                        try:
                            _smart = hybrid_select_tools(user_input, tools_whitelist) or get_tools_for_input(user_input, tools_whitelist)
                            if _smart:
                                tools_whitelist = _smart
                                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm_v2.log', 'message': '[工具路由V2] 智能选择: %d 个工具' % (len(_smart),)}))
                        except Exception as _e:
                            logger.debug(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm_v2.log', 'message': '工具路由V2失败: %s' % (_e,)}))

                    _selected_llm, _selected_model = self._select_model_for_request(user_input)
                    _use_pro = _selected_model != self._llm.model

                    if _use_pro and self._llm_pro:
                        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm_v2.log', 'message': '[调度] %s → 深度模型处理' % (user_input[:20],)}))
                        _tc_pro = ToolCallingService(
                            llm_service=self._llm_pro,
                            max_rounds=self._tool_calling_service._max_rounds,
                            tool_timeout=self._tool_calling_service._tool_timeout,
                        )
                        _result = _tc_pro.chat_with_steps(
                            messages=messages, system_prompt=system_prompt,
                            max_tokens=8192, temperature=0.3,
                            tools_whitelist=tools_whitelist,
                            on_step=lambda s: self._current_tool_steps.append(s),
                        )
                        response = _result["text"]
                        self._last_tool_steps = _result.get("steps", [])
                        self._last_reasoning = _result.get("reasoning") or self._last_reasoning
                    else:
                        _result = self._tool_calling_service.chat_with_steps(
                            messages=messages, system_prompt=system_prompt,
                            max_tokens=8192, temperature=0.3,
                            tools_whitelist=tools_whitelist,
                            on_step=lambda s: self._current_tool_steps.append(s),
                        )
                        response = _result["text"]
                        self._last_tool_steps = _result.get("steps", [])
                        self._last_reasoning = _result.get("reasoning") or self._last_reasoning
                else:
                    response = self._llm.chat(
                        messages=messages,
                        system_prompt=system_prompt,
                        max_tokens=8192,
                        temperature=0.3,
                    )
                if profile.response_prefix:
                    response = "%s\n%s" % (profile.response_prefix, response)

                # 兜底：检测 XML 工具调用
                if response and _re.search(r'<[^>]*tool_calls[^>]*>', response):
                    logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm_v2._call_llm_v2', 'message': '[_call_llm_v2] 响应中包含 XML 工具调用，使用摘要替换'}))
                    _fb_steps = self._last_tool_steps or []
                    _fb_summaries = [s.get("summary", "") for s in _fb_steps
                                     if s.get("type") == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"
                # [护栏集成] LLM 回复校验: 幻觉/PII/Prompt Injection
                # [链路追踪] guard_trace 贯穿护栏调用边界, 便于排查跨服务调用
                _gtrace = uuid.uuid4().hex[:16]
                _gt0 = time.time()
                logger.info(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator.guard_trace.start',
                    'message': '[链路追踪] 护栏调用开始 | guard_trace=%s | input_len=%d | intent=%s'
                               % (_gtrace, len(response), (user_input or '')[:40]),
                }))
                response = self._guard_llm_output(response, user_input, guard_trace=_gtrace)
                logger.info(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator.guard_trace.end',
                    'message': '[链路追踪] 护栏调用结束 | guard_trace=%s | duration_ms=%.1f | output_len=%d'
                               % (_gtrace, (time.time() - _gt0) * 1000, len(response)),
                }))
                # TASK-03: LLM token 消耗埋点（LLMMonitor.estimate_tokens 估算；
                # 埋点异常不影响主链路）
                try:
                    from agent.monitoring.llm_monitor import LLMMonitor
                    _est_tokens = LLMMonitor.estimate_tokens(response)
                    _emit_learning_metric("record_llm_tokens", tokens=_est_tokens)
                except Exception:
                    pass
                return response
            except LLMServiceError as e:
                error_msg = str(e)
                logger.error(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator._call_llm_v2.llm', 'message': 'LLM 调用失败: %s' % (error_msg,), 'error': str(error_msg)}))
                return (
                    "（LLM 调用失败）\n\n"
                    "我尝试调用 LLM 但遇到了问题：%s\n\n"
                    "请检查设置中的 API Key 和模型名称是否正确。" % error_msg
                )
        else:
            return self._build_offline_response(user_input)

    def _guard_llm_output(self, response: str, user_input: str, *,
                         guard_trace: str = "") -> str:
        """[护栏集成] LLM 输出护栏: 校验幻觉/PII/Prompt Injection

        [不易] 护栏异常不阻塞主流程, 失败时返回原 response
        [变易] severity=critical 时用脱敏输出或降级提示; warn/error 用脱敏输出
        [链路追踪] guard_trace 由调用方 (_call_llm_v2) 传入, 贯穿 start/end,
                  critical 决策日志携带该值, 便于跨服务排查降级路径
        """
        logger.info(log_dict({
            'module_name': 'orchestrator',
            'action': 'orchestrator._guard_llm_output.enter',
            'message': '[护栏] 入口 | response_len=%d | loaded_skills=%d | intent=%s'
                       % (len(response or ""), len(getattr(self, "_loaded_skill_ids", [])),
                          (user_input or "")[:40]),
        }))
        try:
            from agent.state_manager import get_skills_mgmt_service
            svc = get_skills_mgmt_service()
            if svc is None:
                logger.info(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator._guard_llm_output.skip_no_svc',
                    'message': '[护栏] skills_mgmt_svc 未初始化, 跳过校验',
                }))
                return response
            loaded = getattr(self, "_loaded_skill_ids", [])
            result = svc.validate_llm_output(
                response, loaded_skills=loaded, intent=user_input,
            )
            severity = result.get("severity", "info")
            sanitized = result.get("sanitized_output")
            findings_count = len(result.get("findings", []))
            logger.info(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator._guard_llm_output.result',
                'message': '[护栏] 校验完成 | severity=%s | findings=%d | has_sanitized=%s'
                           % (severity, findings_count, bool(sanitized)),
                'severity': severity,
            }))

            if severity == "critical":
                logger.warning(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator._guard_llm_output.critical',
                    'message': '[护栏] LLM 输出 critical 拦截 findings=%d' % findings_count,
                    'severity': severity,
                }))
                # [变易] critical 降级策略日志: 记录具体决策, 便于线上排查
                # 策略: has_sanitized=True → 返回脱敏输出 (保留可用信息)
                #       has_sanitized=False → 返回拦截提示 (建议触发重试或降级到兜底回复)
                logger.warning(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator._guard_llm_output.critical_strategy',
                    'message': '[护栏] critical 降级策略 | has_sanitized=%s | 决策=%s | 建议=%s'
                               % (bool(sanitized),
                                  '返回脱敏输出' if sanitized else '返回拦截提示',
                                  '保留脱敏信息继续流程' if sanitized
                                  else '触发重试或降级到兜底回复'),
                    'severity': severity,
                }))
                # [链路追踪] critical 决策点 — 串联 guard_trace, 跨服务排查降级路径
                # 与 guard_trace.start/end 同 trace_id, 在日志系统按 guard_trace 聚合
                # 可定位: start → (enter → result → critical_strategy) → critical_decision → end
                if guard_trace:
                    logger.warning(log_dict({
                        'module_name': 'orchestrator',
                        'action': 'orchestrator.guard_trace.critical_decision',
                        'message': '[链路追踪] critical 降级决策点 | guard_trace=%s | has_sanitized=%s | 决策=%s'
                                   % (guard_trace, bool(sanitized),
                                      '返回脱敏输出' if sanitized else '返回拦截提示'),
                        'guard_trace': guard_trace,
                        'severity': severity,
                    }))
                # critical: 优先用脱敏输出, 无则降级提示
                return sanitized if sanitized else "（输出校验未通过，已拦截）"
            if severity == "error":
                logger.info(log_dict({
                    'module_name': 'orchestrator',
                    'action': 'orchestrator._guard_llm_output.error',
                    'message': '[护栏] LLM 输出 error 警告 findings=%d' % findings_count,
                }))
                return sanitized if sanitized else response
            # warn/info: 用脱敏输出 (如有), 不阻塞
            logger.info(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator._guard_llm_output.pass',
                'message': '[护栏] 通过 (severity=%s), 返回%s'
                           % (severity, '脱敏输出' if sanitized else '原输出'),
            }))
            return sanitized if sanitized else response
        except Exception as e:  # noqa: BLE001 [不易] 护栏不阻塞
            logger.warning(log_dict({
                'module_name': 'orchestrator',
                'action': 'orchestrator._guard_llm_output.exception',
                'message': '[护栏] 异常, 跳过校验: %s' % str(e)[:100],
            }))
            return response

    # ════════════════════════════════════════════════════════════════════
    #  反思
    # ════════════════════════════════════════════════════════════════════

    def self_reflect(self, task: str, response: str) -> dict:
        """自我反思——纯本地实现，零 LLM 调用"""
        reflection_text = self._local_reflect(task[:500], response[:1000])

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction": self._interaction_count,
            "task": task[:200],
            "mode": self._current_mode.value,
            "reflection": reflection_text,
        }
        self._reflection_history.append(entry)

        if self._v2_lifetrace and self._trace_recorder:
            self._trace_recorder.add_to_topic(
                topic="reflection",
                content=reflection_text,
                tags=["reflection", f"interaction_{self._interaction_count}"],
            )

        self._memory.save_log("self_reflect", {
            "interaction": self._interaction_count,
            "mode": self._current_mode.value,
            "task_preview": task[:100],
            "reflection_preview": reflection_text[:200],
        })

        # TASK-02：认知规则评估（保守模式）→ 反思产物持久化（观察模式）
        # 【不易】两开关均默认 false：未开启前行为与现状逐字节等价（仅内存 + 日志）
        # 【变易】开关经 _load_learning_config 三层优先级（环境变量 > config.yaml > 默认值）
        # 【简易】只追加调用，不改 self_reflect 既有行为与返回格式
        learning_cfg = self._load_learning_config()
        # 【排查】接线入口一屏可见两开关状态——"为何未写检索面/未评估"先查此条
        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.learning_gate', 'message': '[学习接线] 反思接线开关（交互 #%d）: reflection_persist=%s critic_evaluation_enabled=%s' % (self._interaction_count, learning_cfg.get("reflection_persist"), learning_cfg.get("critic_evaluation_enabled"))}))
        eval_result = None
        if learning_cfg.get("critic_evaluation_enabled"):
            eval_result = self._run_rule_evaluation(task, response)
        if learning_cfg.get("reflection_persist"):
            self._persist_reflection(entry, task, eval_result)

        logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.log', 'message': '反思完成 (#%d): %s...' % (self._interaction_count, reflection_text[:100])}))
        # TASK-03: 反思产物沉淀埋点（reflection 类 artifact 增量；埋点异常不影响主链路）
        _emit_learning_metric("record_artifact", "reflection")
        return entry

    def _run_rule_evaluation(self, task: str, response: str) -> Optional[Any]:
        """TASK-02：ReflectionEngine 规则评估（保守模式，只记录不干预）

        features.critic_evaluation_enabled=true 时在 self_reflect 后调用；
        6 维规则零 Token。评估结果（score/passed）写入 learning.eval_* 指标族
        （TASK-03 度量体系消费）；不触发重试、不拦截响应（保守模式）。
        【变易】评估器异常仅记 WARNING 降级返回 None，不影响主链路。

        Returns:
            ReflectionResult 或 None（评估异常时）
        """
        try:
            from agent.cognitive.reflection import ReflectionEngine
            # 【排查】评估入口：确认评估被触发及入参规模（评估器异常时与下方 WARNING 配对定位）
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.eval.start', 'message': '[评估] 规则评估开始（交互 #%d）: input_len=%d output_len=%d' % (self._interaction_count, len(task[:500]), len(response[:1000]))}))
            engine = ReflectionEngine()
            result = engine.evaluate(
                task_id="interaction_%d" % self._interaction_count,
                input_text=task[:500],
                output=response[:1000],
            )
            if _MONITORING_AVAILABLE:
                collector = get_metrics_collector()
                collector.increment_counter("learning.eval.total")
                if result.passed:
                    collector.increment_counter("learning.eval.passed")
                else:
                    collector.increment_counter("learning.eval.failed")
                collector.record_latency("learning.eval.score", result.score)
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.eval', 'message': '[评估] 规则评估完成 (#%d): score=%.2f passed=%s' % (self._interaction_count, result.score, result.passed)}))
            return result
        except Exception as e:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.eval.fallback', 'message': '[评估] 规则评估异常，降级跳过: %s' % (e,)}))
            return None

    def _persist_reflection(self, entry: dict, task: str, eval_result: Optional[Any] = None) -> None:
        """TASK-02：反思产物写入检索面（观察模式）

        learning.reflection_persist=true 时在 self_reflect 后追加结构化写入
        VectorStore（type=reflection），供后续对话注入与 TASK-04 沉淀管道消费。
        【不易】反思产物 schema 写死（供 TASK-04 对接防漂移）：
                {type, interaction, task_id, input_hash, score, suggestions, created_at}
        【变易】写入失败静默降级（WARNING），不中断主链路。
        """
        try:
            # 【不易】显式判 None（不依赖对象真值语义）：真实 VectorStore 无 __len__ 恒真值，
            #        若未来有人添加 __len__（FakeVectorStore 曾因此把空存储误判为假而跳过写入），
            #        真值判断会静默失效——is None 消除该隐式契约（TASK-02 同步加固）
            if self._vector_memory is None:
                # 【排查】明确记录"检索面未初始化导致跳过"（避免误判写入逻辑失效）
                logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.persist.skipped', 'message': '[反思] 向量检索面未初始化，跳过持久化（交互 #%d）' % (entry["interaction"],)}))
                return
            import hashlib
            # 【排查】持久化入口：确认写入被触发（与下方 success/fallback 配对定位）
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.persist.start', 'message': '[反思] 反思产物写入检索面开始（交互 #%d, 反思长度=%d）' % (entry["interaction"], len(entry["reflection"]))}))
            score = eval_result.score if eval_result is not None else 0.0
            suggestions = list(eval_result.suggestions or []) if eval_result is not None else []
            item_id = self._vector_memory.add(
                content="反思(#%d): %s" % (entry["interaction"], entry["reflection"]),
                metadata={
                    "type": "reflection",
                    "interaction": entry["interaction"],
                    "task_id": str(entry["interaction"]),
                    "input_hash": hashlib.sha1(task.encode("utf-8")).hexdigest()[:12],
                    "score": score,
                    "suggestions": suggestions,
                    "created_at": entry["timestamp"],
                },
            )
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.persist', 'message': '[反思] 反思产物已写入检索面: %s' % (item_id,)}))
        except Exception as e:
            logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.self_reflect.persist.fallback', 'message': '[反思] 反思产物写入失败，静默降级: %s' % (e,)}))

    @staticmethod
    def _local_reflect(task: str, response: str) -> str:
        """基于规则的本地反思评估，零 LLM 调用"""
        if not task or not response:
            return "（任务或响应为空，跳过反思）"

        task_lower = task.lower()
        resp_lower = response.lower()
        lines = []

        # 维度 1：理解准确度
        key_terms = set(_re.findall(r'[a-zA-Z_]\w{3,}', task_lower))
        stop_words = {'this', 'that', 'with', 'from', 'have', 'been', 'what', 'which',
                      'there', 'their', 'about', 'would', 'could', 'should', 'your',
                      'will', 'them', 'then', 'than', 'when', 'where', 'more', 'also',
                      'some', 'into', 'other', 'only', 'over', 'such', 'very', 'just',
                      'well', 'make', 'like', 'take', 'know', 'think'}
        key_terms -= stop_words
        if key_terms:
            covered = sum(1 for t in key_terms if t in resp_lower)
            ratio = covered / max(len(key_terms), 1)
            if ratio >= 0.8:
                lines.append("✅ 准确理解了用户需求，覆盖了大部分关键点")
            elif ratio >= 0.5:
                lines.append("🟡 基本理解了需求，但部分细节可以更深入")
            else:
                lines.append("🔄 可能需要进一步确认用户需求中的关键点")
        else:
            lines.append("ℹ️ 任务以中文为主，基于上下文判断理解准确")

        # 维度 2：响应完整性
        resp_len = len(response)
        task_len = max(len(task), 1)
        ratio = resp_len / task_len
        has_code = bool(_re.search(r'```[\s\S]*?```', response))
        has_steps = bool(_re.search(r'(?:步骤|第一步|首先|其次|最后|\d+\.\s)', response))
        has_solution = bool(_re.search(r'(可以|建议|推荐|使用|采用|方案|方法|方式)', response))
        completeness_signals = sum([has_code, has_steps, has_solution])
        if ratio < 0.3:
            lines.append("📏 响应相对简洁，如需更详细可要求我展开")
        elif ratio > 5:
            lines.append("📏 响应较为详细，已提供充分信息")
        else:
            if completeness_signals >= 2:
                lines.append("✅ 响应完整，包含代码/步骤和具体建议")
            elif completeness_signals >= 1:
                lines.append("🟡 响应基本完整，可考虑补充更多细节")
            else:
                lines.append("📏 响应包含基础信息")

        # 维度 3：改进方向
        improvements = []
        if _re.search(r'(但是|不过|然而|缺点|局限|注意)', response):
            improvements.append("已指出局限性")
        if _re.search(r'(下一步|后续|进一步|可以试试|参考)', response):
            improvements.append("给出了后续方向")
        if _re.search(r'(欢迎|随时|继续|进一步|如果需要)', response):
            improvements.append("开放了追问空间")
        if improvements:
            lines.append("💡 改进: " + "；".join(improvements))
        else:
            lines.append("💡 可以补充后续建议或开放追问空间")

        # 维度 4：值得记住的经验
        if key_terms:
            term_list = sorted(key_terms)[:3]
            experience = f"本次交互涉及: {', '.join(term_list)}"
            lines.append(f"📝 {experience}")

        return "\n".join(lines)

    # ════════════════════════════════════════════════════════════════════
    #  权限与中止
    # ════════════════════════════════════════════════════════════════════

    def request_permission(self, action: str, context: str = ""):
        """申请执行危险操作的权限"""
        return self._permission.check_action(action, context)

    def check_action_with_autonomy(self, action: str, context: str = ""):
        """TASK-07：自主权聚合视图（可查询）

        【不易】内部仍调 _permission.check_action，既有判定语义零变化；
        仅叠加 L1-L5 等级视图（越级/确认/审计要求）供日志与审计使用。
        """
        base_result = self._permission.check_action(action, context)
        try:
            from agent.autonomy import AutonomyPolicy
            from agent.autonomy import get_autonomy_level

            return AutonomyPolicy.aggregate(base_result, action, level=get_autonomy_level())
        except Exception as e:  # noqa: BLE001 视图层异常不影响既有判定
            logger.warning("[自主权] 聚合视图构建失败，仅返回既有判定: %s", e)
            return base_result

    def abort_chat(self):
        """手动中止当前对话"""
        if self._tool_calling_service:
            self._tool_calling_service.abort()
            logger.info(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.abort_chat.orchestrator', 'message': '[Orchestrator] 对话中止请求已发送'}))
            return True
        logger.warning(log_dict({'module_name': 'orchestrator', 'action': 'orchestrator.abort_chat.orchestrator', 'message': '[Orchestrator] 工具调用引擎未启用，无法中止'}))
        return False

    @property
    def last_context_warning(self) -> Optional[dict]:
        """获取上一条回复的上下文使用警告"""
        return self._last_context_warning

    # ════════════════════════════════════════════════════════════════════
    #  子模块访问器（懒加载）
    # ════════════════════════════════════════════════════════════════════

    @property
    def subagent(self):
        """分身管理器——分身完整生命周期管理"""
        attr = '_subagent_mgr_proxy'
        if not hasattr(self, attr):
            from .subagent_manager import SubagentManager
            object.__setattr__(self, attr, SubagentManager(self))
        return getattr(self, attr)

    @property
    def voice(self):
        """语音/视觉多模态模块"""
        attr = '_voice_vision_proxy'
        if not hasattr(self, attr):
            from .voice_vision import VoiceVision
            object.__setattr__(self, attr, VoiceVision(self))
        return getattr(self, attr)

    @property
    def status(self):
        """状态报告模块"""
        attr = '_status_reporter_proxy'
        if not hasattr(self, attr):
            from .status_reporter import StatusReporter
            object.__setattr__(self, attr, StatusReporter(self))
        return getattr(self, attr)

    # ════════════════════════════════════════════════════════════════════
    #  代理方法（向后兼容）
    # ════════════════════════════════════════════════════════════════════

    # -- Subagent 代理 --

    def create_subagent(self, config):
        """创建一个新分身（代理至 SubagentManager）"""
        return self.subagent.create(config)

    def destroy_subagent(self, name: str):
        """销毁指定分身（代理至 SubagentManager）"""
        return self.subagent.destroy(name)

    def hot_reload_subagent(self, name: str, new_config: dict):
        """热更新分身配置（代理至 SubagentManager）"""
        return self.subagent.hot_reload(name, new_config)

    def list_subagents(self):
        """列出所有活跃分身（代理至 SubagentManager）"""
        return self.subagent.list()

    def get_subagent(self, name: str):
        """获取指定分身状态（代理至 SubagentManager）"""
        return self.subagent.get(name)

    def execute_subagent(self, name: str, task: str):
        """在分身中执行任务（代理至 SubagentManager）"""
        return self.subagent.execute(name, task)

    # -- 语音/视觉代理 --

    def speak(self, text: str, save_to_file: bool = False):
        """语音合成（代理至 VoiceVision）"""
        return self.voice.speak(text, save_to_file)

    def listen(self, duration: int = 5):
        """语音识别（代理至 VoiceVision）"""
        return self.voice.listen(duration)

    def voice_chat(self, duration: int = 5, speak_response: bool = True):
        """语音对话（代理至 VoiceVision）"""
        return self.voice.voice_chat(duration, speak_response)

    def look_at_screen(self, region=None):
        """观察屏幕（代理至 VoiceVision）"""
        return self.voice.look_at_screen(region)

    def get_voice_status(self):
        """获取语音功能状态（代理至 VoiceVision）"""
        return self.voice.get_voice_status()

    def get_multimodal_status(self):
        """获取多模态功能总状态（代理至 VoiceVision）"""
        return self.voice.get_multimodal_status()

    # -- 状态报告代理 --

    def get_status(self):
        """获取完整状态报告（代理至 StatusReporter）"""
        return self.status.get_status()

    def get_status_text(self):
        """获取人类可读状态描述（代理至 StatusReporter）"""
        return self.status.get_status_text()

    def check_health(self):
        """健康检查（代理至 StatusReporter）"""
        return self.status.check_health()


# 延迟导入: 放在 Orchestrator 类定义之后，避免与 digital_life 形成模块级循环导入.
# 不变量(不易): 这些符号仅在方法/函数内使用(运行时解析), 模块加载完成时已就绪.
# 循环链(修复前): orchestrator.py:24→digital_life.py:369→agent.orchestrator.Orchestrator→orchestrator.py(未完成)
# 修复后: Orchestrator 类先定义, digital_life.py:369 经 __getattr__ 获取 Orchestrator 时类已就绪.
from agent.digital_life import (
    _MONITORING_AVAILABLE, _PLANNING_AVAILABLE,
    TraceContext, get_metrics_collector, get_trace_id,
    get_error_reporter, AlertLevel,
    BehaviorMode,
    _get_template,
    LLMServiceError,
)
