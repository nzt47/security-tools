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

import logging
import time
import json
import os
import re as _re
from datetime import datetime, timezone
from typing import Optional

from agent.digital_life import (
    _MONITORING_AVAILABLE, _PLANNING_AVAILABLE,
    TraceContext, get_metrics_collector, get_trace_id,
    get_error_reporter, AlertLevel,
    BehaviorMode,
    _get_template,
    LLMServiceError,
)

from agent.guardrails.input_guard import InputGuard, GuardAction
from agent.guardrails.output_guard import OutputGuard
from agent.observability.subscriber import trace_store, TraceSpan
from agent.orchestrator.message_handler import MessageHandler
from agent.orchestrator.response_builder import ResponseBuilder, Response

logger = logging.getLogger(__name__)


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

    # ════════════════════════════════════════════════════════════════════
    #  核心闭环：感知 → 认知 → 行动 → 反思
    # ════════════════════════════════════════════════════════════════════

    def chat(self, user_input: str) -> str:
        """与云枢对话——P12 统一入口

        这是与云枢交互的唯一外部入口。
        内部统一由 process() 处理完整的感知-认知-行动-反思闭环。

        Args:
            user_input: 用户说给云枢的话

        Returns:
            云枢的回复文本
        """
        result = self.process(user_input)
        if isinstance(result, dict):
            return result.get("response", "") or result.get("data", "") or ""
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
        logger.info("=" * 70)
        logger.info("[%s] [Orchestrator.process] 收到对话请求", trace_id)
        input_preview = user_input[:100] + ("..." if len(user_input) > 100 else "")
        logger.info("   用户输入: %s", input_preview)
        logger.info("   对话次数: %d", self._interaction_count + 1)
        logger.info("=" * 70)

        if not self._running:
            logger.warning("云枢未运行，返回提示")
            return ResponseBuilder.success(
                "我还没有被唤醒。请先调用 start() 让我醒来。"
            ).to_dict()

        self._interaction_count += 1

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
            logger.info("[上下文] %s（%.1f%%）", self._last_context_warning["message"],
                        self._last_context_warning["pct"])

        # ── 第零步：InputGuard 输入安全检查 ──
        guard_result = self._input_guard.check(user_input)
        if guard_result.action == GuardAction.BLOCK:
            logger.warning(
                "[Guard] ⛔ 输入被 InputGuard 拦截: %s（匹配: %s）",
                guard_result.reason, guard_result.matched_pattern,
            )
            if trace_id:
                trace_store.end_trace(trace_id, guard_result.reason, status="blocked")
            return ResponseBuilder.guard_blocked(
                guard_result.reason, guard_result.matched_pattern
            ).to_dict()

        # ── 第一步：Workflow Engine 匹配（0 Token 消耗）──
        ts_wf = time.time()
        workflow_result = self._workflow_engine.try_match(user_input)
        if workflow_result is not None and workflow_result.matched:
            logger.info("[Workflow] 命中规则: %s, 置信度=%.2f, 耗时=%.2fms",
                        workflow_result.intent, workflow_result.confidence,
                        workflow_result.execution_time_ms)
            self._memory.score_and_save_message("user", user_input)
            self._memory.score_and_save_message("assistant", workflow_result.output)
            if trace_id:
                trace_store.end_trace(trace_id, workflow_result.output)
            return ResponseBuilder.workflow_result(workflow_result.output).to_dict()
        if trace_id:
            trace_store.add_span(trace_id, TraceSpan(
                span_id=f"{trace_id}_workflow",
                operation="workflow_match",
                start_time=ts_wf, end_time=time.time(),
                duration_ms=(time.time() - ts_wf) * 1000,
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
            if trace_id:
                trace_store.end_trace(trace_id, response)
            return ResponseBuilder.rejection(
                reject_reason, self._current_mode.value
            ).to_dict()

        # ── 第三步：意图路由 + 模板匹配（零 LLM 消耗）──
        try:
            from agent.response_workflows import (
                IntentRouter, ResponseTemplates, Confidence,
            )
            intent, confidence = IntentRouter.classify(user_input)
            logger.info("[路由] 意图=%s, 置信度=%s", intent, confidence)

            is_follow_up = MessageHandler.is_follow_up({
                'last_was_template': getattr(self, '_last_was_template', False),
                'confidence': confidence,
            })
            dissatisfaction = MessageHandler.detect_dissatisfaction(user_input)
            if dissatisfaction:
                logger.info("[路由] 检测到用户不满/纠正，降级到 LLM")
                is_follow_up = True
            if is_follow_up:
                logger.info("[路由] 检测到模板后追问，降级到 LLM")
                self._last_was_template = False

            if not is_follow_up:
                template_response = ResponseTemplates.for_intent(
                    intent, confidence=confidence,
                    hour=datetime.now().hour,
                )
                if template_response:
                    logger.info("[路由] 使用本地模板，跳过 LLM 调用")
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
                    logger.info("[路由] 模板回复完成 (#%d)", self._interaction_count)
                    if trace_id:
                        trace_store.add_span(trace_id, TraceSpan(
                            span_id=f"{trace_id}_template",
                            operation="template_reply",
                            status="success",
                            metadata={"intent": intent,
                                      "confidence": confidence.name},
                        ))
                        trace_store.end_trace(trace_id, response)
                    return ResponseBuilder.success(response).to_dict()
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[路由] 路由失败，降级到 LLM: %s", e)
        self._last_was_template = False

        # ── 第四步：LLM 调用 ──
        ts_llm = time.time()
        try:
            if self._v2_lifetrace and self._trace_recorder:
                # V2 路径：Persona 系统 + ToolCallingService
                response = self._call_llm_v2(user_input, body_status)
            else:
                # 标准路径
                response = self._call_llm(user_input, body_status)
        except Exception as e:
            logger.error("[FAIL] 对话处理异常: %s", e)
            tb_str = __import__('traceback').format_exc()
            logger.error("堆栈:\n%s", tb_str)
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
                                'session_id': getattr(self, '_session_id', 'unknown'),
                            },
                        )
                        logger.info("[%s] [OK] 错误已自动上报", trace_id)
                    except Exception as report_error:
                        logger.warning("[%s] 错误上报失败: %s", trace_id, report_error)
            return ResponseBuilder.error(
                "抱歉，处理您的请求时遇到了问题：%s" % e
            ).to_dict()
        llm_duration_ms = (time.time() - ts_llm) * 1000

        # 规划模式：追加 Planner 状态信息
        planning_mode = kwargs.get("planning_mode", False) or \
            (self._planning_enabled and self._planner and self._needs_planning(user_input))
        if planning_mode and self._planner:
            try:
                stats = self._planner.get_stats()
                if stats and stats.get("registered_tools"):
                    registered_tools = stats["registered_tools"]
                    response += "\n\n（规划引擎已就绪，可用工具: %s）" % registered_tools
            except Exception:
                pass

        # ── 第五步：OutputGuard 输出安全检查（PII 遮盖）──
        output_result = self._output_guard.check(response)
        if output_result.modified:
            logger.info(
                "[Guard] 🔒 输出已过滤，遮盖字段: %s",
                ", ".join(output_result.redacted_fields),
            )
            response = output_result.filtered

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
                logger.debug("[SkillGate] self_reflection 已禁用，跳过")

        # ── 第七步：记忆保存 ──
        self._memory.score_and_save_message("user", user_input)
        self._memory.score_and_save_message("assistant", response)
        try:
            self._memory.infer_working_memory(user_input, response)
        except Exception as e:
            logger.debug("[WM] 工作记忆更新失败: %s", e)

        # 向量记忆保存
        if self._vector_memory:
            try:
                memory_content = f"用户: {user_input}\n云枢: {response}"
                item_id = self._vector_memory.add(
                    content=memory_content,
                    metadata={
                        "type": "conversation",
                        "interaction": self._interaction_count,
                    },
                )
                logger.info("[记忆] 向量记忆已保存: %s", item_id)
            except Exception as e:
                logger.error("[FAIL] 保存向量记忆失败: %s", e)

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

        if _MONITORING_AVAILABLE:
            collector.increment_counter("count.digital_life.chat.success")

        return ResponseBuilder.success(response).to_dict()

    # (以下废弃方法已在 P12 统一链路中删除:
    #  _chat_v2, _chat_with_planning, _process_user_input)
    #  所有功能已合并到 process() 方法中

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
            logger.debug("检查上下文使用率时出错: %s", e)
            return None

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
            current_date=datetime.now().strftime("%Y年%m月%d日"),
            body_status=body_status,
            mode_name=profile.label,
            mode_description=profile.description,
            memory_context=memory_context,
            tool_status=tool_status,
            skill_instructions=skill_instructions,
        )
        if wm_text:
            system_prompt += wm_text

        # ── System prompt Token 预算检查 ──
        try:
            _sp_tokens = self._memory._token_counter.count(system_prompt)
            _sp_budget = 10000
            if _sp_tokens > _sp_budget:
                logger.warning("[Token] system prompt %d tokens 超预算 %d，截断工具状态",
                               _sp_tokens, _sp_budget)
                _brief_tools = (tool_status[:300] + "\n...（已截断）") if len(tool_status) > 300 else tool_status
                system_prompt = _sp_template.format(
                    current_date=datetime.now().strftime("%Y年%m月%d日"),
                    body_status=body_status,
                    mode_name=profile.label,
                    mode_description=profile.description,
                    memory_context=memory_context,
                    tool_status=_brief_tools,
                    skill_instructions="",
                )
                if wm_text:
                    system_prompt += wm_text
            logger.info("[Token] system prompt: %d tokens (预算 %d)", _sp_tokens, _sp_budget)
        except Exception:
            pass

        # ── 2. 组装上下文消息 ──
        messages = []
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
            logger.warning("Budget context assembly failed: %s, falling back", e)
            try:
                context = self._memory.get_context(token_limit=self._memory_token_limit)
                if context:
                    messages.extend(context)
            except Exception:
                pass

        if self._tool_calling_service:
            messages.append({
                "role": "system",
                "content": (
                    "⚡ 立即检查：用户这句话需要工具吗？如果需要，直接发起函数调用。"
                    "绝对禁止只发文字描述你将要做的操作。"
                    "没调用工具 = 没执行。立即行动。"
                ),
            })

        messages.append({"role": "user", "content": user_input})

        if self._llm:
            try:
                self._last_tool_steps = []
                self._current_tool_steps = []

                from agent import tools as _tools
                from agent.tool_calling import _summarize_tool_result, _clean_for_json
                _whitelist = self._get_enabled_tools_whitelist()
                if self._is_smart_tool_selection_enabled():
                    try:
                        from agent.tool_router import get_tools_for_input
                        _smart_tools = get_tools_for_input(user_input, _whitelist)
                        if _smart_tools:
                            _whitelist = _smart_tools
                            logger.info("[工具路由] 智能选择: %d/%d 个工具",
                                        len(_smart_tools), len(_tools.list_tools()))
                    except Exception as _e:
                        logger.debug("工具路由失败: %s", _e)
                _tool_defs = _tools.get_tool_defs(whitelist=_whitelist)
                _client = self._llm._get_client()

                # 智能调度：选择最合适的模型
                _selected_llm, _selected_model = self._select_model_for_request(user_input)
                _use_pro = _selected_model != self._llm.model
                if _use_pro and self._llm_pro:
                    logger.info("[_call_llm] 调度到深度模型: %s (主模型: %s)",
                                _selected_model, self._llm.model)
                    _client = self._llm_pro._get_client()
                    _working_model = _selected_model
                else:
                    _working_model = self._llm.model
                    logger.info("[_call_llm] 使用主模型: %s (pro可用=%s)",
                                _working_model, self._llm_pro is not None)

                _working = list(messages)
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
                                from agent.tool_calling import ToolCallingService as _TCSvc
                                _xml_tools = _TCSvc._extract_xml_tool_calls(_msg.content)
                            except Exception as _xml_e:
                                logger.debug("[_call_llm] XML 工具提取失败: %s", _xml_e)
                        if _xml_tools:
                            logger.info("[_call_llm] 检测到 XML 格式工具调用: %d 个", len(_xml_tools))
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
                    logger.warning("[_call_llm] 响应中包含 XML 工具调用，使用工具结果摘要替换")
                    _fb_summaries = [s.get("summary", "") for s in self._current_tool_steps
                                     if s["type"] == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"

                return response
            except Exception as _e:
                logger.error("LLM 调用失败: %s", _e)
                return "（抱歉，处理时遇到了问题: %s）" % str(_e)
        else:
            self._set_thinking_mode("instinct")
            return self._build_offline_response(user_input)

    def _call_llm_v2(self, user_input: str, body_status: str) -> str:
        """V2 调用 LLM 生成响应（使用 Persona 系统）"""
        profile = self._behavior.profile
        self._set_thinking_mode()

        if self._v2_persona and self._persona_injector:
            memory_context = self._get_lifetrace_context(user_input)
            tool_status_text = "## 当前工具与技能状态\n" + self._build_tool_status_text()
            system_prompt = self._persona_injector.build_system_prompt(
                body_status=body_status,
                memory_context=memory_context,
            ) + "\n\n" + tool_status_text
        else:
            memory_context = self._get_lifetrace_context(user_input) if self._v2_lifetrace else ""
            tool_status = self._build_tool_status_text()
            skill_instructions = self._build_skill_instructions()
            _sp_template = _get_template()
            system_prompt = _sp_template.format(
                current_date=datetime.now().strftime("%Y年%m月%d日"),
                body_status=body_status,
                mode_name=profile.label,
                mode_description=profile.description,
                memory_context=memory_context or "（暂无记忆内容）",
                tool_status=tool_status,
                skill_instructions=skill_instructions,
            )

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
                            from agent.tool_router import get_tools_for_input
                            _smart = get_tools_for_input(user_input, tools_whitelist)
                            if _smart:
                                tools_whitelist = _smart
                                logger.info("[工具路由V2] 智能选择: %d 个工具", len(_smart))
                        except Exception as _e:
                            logger.debug("工具路由V2失败: %s", _e)

                    _selected_llm, _selected_model = self._select_model_for_request(user_input)
                    _use_pro = _selected_model != self._llm.model

                    if _use_pro and self._llm_pro:
                        logger.info("[调度] %s → 深度模型处理", user_input[:20])
                        from agent.tool_calling import ToolCallingService
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
                    logger.warning("[_call_llm_v2] 响应中包含 XML 工具调用，使用摘要替换")
                    _fb_steps = self._last_tool_steps or []
                    _fb_summaries = [s.get("summary", "") for s in _fb_steps
                                     if s.get("type") == "tool_result"][-5:]
                    if _fb_summaries:
                        response = "已获取到以下信息：\n" + "\n".join(f"  - {s}" for s in _fb_summaries)
                    else:
                        response = "（已处理完毕）"
                return response
            except LLMServiceError as e:
                error_msg = str(e)
                logger.error("LLM 调用失败: %s", error_msg)
                return (
                    "（LLM 调用失败）\n\n"
                    "我尝试调用 LLM 但遇到了问题：%s\n\n"
                    "请检查设置中的 API Key 和模型名称是否正确。" % error_msg
                )
        else:
            return self._build_offline_response(user_input)

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

        logger.info("反思完成 (#%d): %s...", self._interaction_count, reflection_text[:100])
        return entry

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

    def abort_chat(self):
        """手动中止当前对话"""
        if self._tool_calling_service:
            self._tool_calling_service.abort()
            logger.info("[Orchestrator] 对话中止请求已发送")
            return True
        logger.warning("[Orchestrator] 工具调用引擎未启用，无法中止")
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
