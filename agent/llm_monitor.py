"""
云枢 LLMMonitor — LLM 收发监控器

拦截所有进出 LLM 的通信，记录完整内容并计算 token 消耗。
提供环形缓冲区查询接口。
"""

import threading
import time
import logging
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

logger = logging.getLogger(__name__)

MAX_RECORDS = 500  # 环形缓冲区大小（向后兼容别名，运行时从 Config 读取）


@dataclass
class LLMInteraction:
    """单次 LLM 交互的完整记录"""
    id: str = ""
    timestamp: float = 0.0
    session_id: str = ""
    source: str = ""                     # chat / summarize / tool_calling
    model: str = ""
    provider: str = ""

    # ── 请求 ──
    system_prompt: str = ""
    messages: list = field(default_factory=list)     # 完整的 messages 数组
    tools: list = field(default_factory=list)        # 工具定义（如果有）
    round: int = 0                                   # 多轮工具调用中的轮次

    # ── 响应 ──
    response_text: str = ""
    response_full: str = ""                          # 完整原始响应（json）
    tool_calls: list = field(default_factory=list)   # 被调用的工具
    reasoning: str = ""                              # 推理过程

    # ── Token ──
    request_tokens: int = 0
    response_tokens: int = 0
    total_tokens: int = 0

    # ── 时序 ──
    duration_ms: float = 0.0
    error: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp_str"] = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return d


class LLMMonitor:
    """LLM 通信监控器 — 环形缓冲区"""

    def __init__(self, max_records: Optional[int] = None):
        # 配置化：未显式指定时从 Config 读取（支持热加载）
        _max_records = max_records
        if _max_records is None:
            try:
                from agent.monitoring.observability_config import get_llm_monitor_max_records
                _max_records = get_llm_monitor_max_records()
            except Exception:
                _max_records = MAX_RECORDS
        self._records: list[LLMInteraction] = []
        self._max = _max_records
        self._lock = threading.Lock()
        self._hooks_installed = False
        self._enabled = True

    # ── 属性 ──

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, val: bool):
        self._enabled = val
        logger.info("LLM 监控器 %s", "已启用" if val else "已禁用")

    @property
    def record_count(self) -> int:
        with self._lock:
            return len(self._records)

    # ── 记录 ──

    def record(self, interaction: LLMInteraction) -> None:
        """记录一次 LLM 交互"""
        if not self._enabled:
            return
        if not interaction.id:
            interaction.id = uuid.uuid4().hex[:12]
        if not interaction.timestamp:
            interaction.timestamp = time.time()

        with self._lock:
            self._records.append(interaction)
            if len(self._records) > self._max:
                self._records.pop(0)

    # ── 查询 ──

    def get_records(self, limit: int = 50, offset: int = 0,
                    session_id: str = "", source: str = "") -> tuple[list[dict], int]:
        """获取记录列表

        Returns:
            (records_list, total_count)
        """
        with self._lock:
            filtered = list(self._records)

        # 过滤
        if session_id:
            filtered = [r for r in filtered if r.session_id == session_id]
        if source:
            filtered = [r for r in filtered if r.source == source]

        total = len(filtered)
        # 倒序（最新的在前）
        filtered.reverse()
        page = filtered[offset:offset + limit]
        return [r.to_dict() for r in page], total

    def get_record(self, record_id: str) -> Optional[dict]:
        """获取单条记录详情"""
        with self._lock:
            for r in self._records:
                if r.id == record_id:
                    return r.to_dict()
        return None

    def clear(self) -> None:
        """清除所有记录"""
        with self._lock:
            self._records.clear()

    def get_stats(self) -> dict:
        """获取汇总统计"""
        with self._lock:
            total = len(self._records)
            if total == 0:
                return {"total": 0, "total_request_tokens": 0,
                        "total_response_tokens": 0, "total_cost_estimate": 0,
                        "avg_duration_ms": 0, "by_source": {}}

            total_req_tok = sum(r.request_tokens for r in self._records)
            total_res_tok = sum(r.response_tokens for r in self._records)
            avg_dur = sum(r.duration_ms for r in self._records) / total

            by_source = {}
            for r in self._records:
                s = r.source or "unknown"
                by_source.setdefault(s, {"count": 0, "req_tokens": 0, "res_tokens": 0})
                by_source[s]["count"] += 1
                by_source[s]["req_tokens"] += r.request_tokens
                by_source[s]["res_tokens"] += r.response_tokens

            # 估算费用（用 gpt-4o-mini 价格近似：$0.15/M 输入, $0.60/M 输出）
            cost_input = total_req_tok * 0.15 / 1_000_000
            cost_output = total_res_tok * 0.60 / 1_000_000

            return {
                "total": total,
                "total_request_tokens": total_req_tok,
                "total_response_tokens": total_res_tok,
                "total_tokens": total_req_tok + total_res_tok,
                "avg_duration_ms": round(avg_dur, 1),
                "estimated_cost_usd": round(cost_input + cost_output, 6),
                "by_source": by_source,
            }

    # ── Token 估算 ──

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """估算文本的 token 数"""
        if not text:
            return 0
        try:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except ImportError:
            return len(text) // 4

    @staticmethod
    def estimate_messages_tokens(messages: list) -> int:
        """估算 messages 数组的 token 数（简化版）"""
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += LLMMonitor.estimate_tokens(content) + 4  # role overhead
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        total += LLMMonitor.estimate_tokens(block.get("text", ""))
            else:
                total += 4
        return total + 2  # 整体 overhead

    @staticmethod
    def create_from_api_call(
        system_prompt: str = "",
        messages: list = None,
        tools: list = None,
        response_obj=None,
        model: str = "",
        provider: str = "",
        session_id: str = "",
        source: str = "",
        round_num: int = 0,
        duration_ms: float = 0.0,
        error: str = "",
    ) -> "LLMInteraction":
        """从 API 调用参数创建记录"""
        if messages is None:
            messages = []
        if tools is None:
            tools = []

        # 提取响应
        response_text = ""
        tool_calls = []
        reasoning = ""
        response_full = ""

        if response_obj is not None:
            try:
                response_full = str(response_obj)
                # OpenAI 格式
                if hasattr(response_obj, "choices"):
                    choice = response_obj.choices[0]
                    msg = choice.message
                    response_text = getattr(msg, "content", "") or ""

                    # 推理内容（DeepSeek R1 等）
                    reasoning = getattr(msg, "reasoning_content", "") or ""

                    # 工具调用
                    tcs = getattr(msg, "tool_calls", None)
                    if tcs:
                        for tc in tcs:
                            tool_calls.append({
                                "id": tc.id,
                                "type": tc.type,
                                "function": {
                                    "name": tc.function.name,
                                    "arguments": tc.function.arguments,
                                }
                            })
                # Anthropic 格式兼容
                elif hasattr(response_obj, "content"):
                    for block in response_obj.content:
                        if hasattr(block, "text") and block.text:
                            response_text += block.text
                        if hasattr(block, "type") and block.type == "tool_use":
                            tool_calls.append({
                                "id": block.id,
                                "type": "tool_use",
                                "function": {
                                    "name": block.name,
                                    "arguments": json.dumps(block.input) if hasattr(block, "input") else "{}",
                                }
                            })
            except Exception as e:
                logger.debug("提取响应内容失败: %s", e)

        # 计算 token
        req_tokens = LLMMonitor.estimate_messages_tokens(messages)
        if system_prompt:
            req_tokens += LLMMonitor.estimate_tokens(system_prompt)
        # tools 参数估算（粗略）
        if tools:
            tools_text = json.dumps(tools)
            req_tokens += LLMMonitor.estimate_tokens(tools_text) // 2

        res_tokens = LLMMonitor.estimate_tokens(response_text)
        if reasoning:
            res_tokens += LLMMonitor.estimate_tokens(reasoning)

        return LLMInteraction(
            session_id=session_id,
            source=source,
            model=model,
            provider=provider,
            system_prompt=system_prompt,
            messages=messages,
            tools=tools,
            round=round_num,
            response_text=response_text[:50000],  # 截断防止撑爆
            response_full=response_full[:100000],
            tool_calls=tool_calls,
            reasoning=reasoning[:20000],
            request_tokens=req_tokens,
            response_tokens=res_tokens,
            total_tokens=req_tokens + res_tokens,
            duration_ms=round(duration_ms, 1),
            error=error,
        )


# ── 全局单例 ──
_monitor: Optional[LLMMonitor] = None


def get_monitor() -> LLMMonitor:
    global _monitor
    if _monitor is None:
        _monitor = LLMMonitor()
    return _monitor


def install_hooks():
    """安装 LLMService 钩子"""
    monitor = get_monitor()
    if monitor._hooks_installed:
        return

    try:
        from memory.llm_service import LLMService

        # 保存原始方法
        orig_do_chat = LLMService._do_chat
        orig_do_summarize = LLMService._do_summarize

        def _patched_do_chat(self, messages, system_prompt="",
                             max_tokens=1024, temperature=0.7):
            start = time.time()
            error = ""
            response_obj = None
            try:
                response_obj = orig_do_chat(self, messages, system_prompt,
                                            max_tokens, temperature)
                return response_obj
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration = (time.time() - start) * 1000
                try:
                    record = LLMMonitor.create_from_api_call(
                        system_prompt=system_prompt,
                        messages=messages,
                        response_obj=response_obj,
                        model=getattr(self, 'model', ''),
                        provider=getattr(self, 'provider', ''),
                        source="chat",
                        duration_ms=duration,
                        error=error,
                    )
                    monitor.record(record)
                except Exception as e:
                    logger.debug("记录 LLM chat 调用失败: %s", e)

        def _patched_do_summarize(self, messages, max_tokens=500, system_prompt=""):
            start = time.time()
            error = ""
            response_obj = None
            try:
                response_obj = orig_do_summarize(self, messages, max_tokens, system_prompt)
                return response_obj
            except Exception as e:
                error = str(e)
                raise
            finally:
                duration = (time.time() - start) * 1000
                try:
                    record = LLMMonitor.create_from_api_call(
                        system_prompt=system_prompt,
                        messages=messages,
                        response_obj=response_obj,
                        model=getattr(self, 'model', ''),
                        provider=getattr(self, 'provider', ''),
                        source="summarize",
                        duration_ms=duration,
                        error=error,
                    )
                    monitor.record(record)
                except Exception as e:
                    logger.debug("记录 LLM summarize 调用失败: %s", e)

        LLMService._do_chat = _patched_do_chat
        LLMService._do_summarize = _patched_do_summarize

        monitor._hooks_installed = True
        logger.info("LLM 监控钩子已安装（chat + summarize）")

        # 同时安装到 tool_calling 路径的 client.get_client 包装
        _wrap_get_client_for_tool_calling(monitor)

    except Exception as e:
        logger.warning("安装 LLM 监控钩子失败: %s", e)


def _wrap_get_client_for_tool_calling(monitor):
    """修补 LLMService._get_client，确保任何通过它创建的 client 的 create 方法被包装"""
    try:
        from memory.llm_service import LLMService

        orig_get_client = LLMService._get_client

        def _patched_get_client(self):
            client = orig_get_client(self)
            if client is None:
                return client

            # 只包装一次
            if getattr(client, '__llm_monitored', False):
                return client

            provider = getattr(self, 'provider', '')
            model = getattr(self, 'model', '')

            if hasattr(client, 'chat') and hasattr(client.chat, 'completions') and hasattr(client.chat.completions, 'create'):
                orig_create = client.chat.completions.create

                def _wrapped_create(*args, **kwargs):
                    start = time.time()
                    error = ""
                    response_obj = None
                    try:
                        response_obj = orig_create(*args, **kwargs)
                        return response_obj
                    except Exception as e:
                        error = str(e)
                        raise
                    finally:
                        duration = (time.time() - start) * 1000
                        try:
                            messages = kwargs.get("messages", [])
                            tools = kwargs.get("tools", [])
                            sys_prompt = ""
                            if messages and len(messages) > 0 and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                                sys_prompt = messages[0].get("content", "")
                                messages = messages[1:]

                            record = LLMMonitor.create_from_api_call(
                                system_prompt=sys_prompt,
                                messages=messages,
                                tools=tools,
                                response_obj=response_obj,
                                model=kwargs.get("model", model),
                                provider=provider,
                                source="tool_calling",
                                duration_ms=duration,
                                error=error,
                            )
                            monitor.record(record)
                        except Exception as e:
                            logger.debug("记录 LLM 工具调用失败: %s", e)

                client.chat.completions.create = _wrapped_create
                client.__llm_monitored = True

            return client

        LLMService._get_client = _patched_get_client
        logger.info("LLM 监控钩子已安装（client.create 层）")

    except Exception as e:
        logger.debug("安装 client.create 钩子失败: %s", e)
