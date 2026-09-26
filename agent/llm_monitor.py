"""
云枢 LLMMonitor — LLM 收发监控器

拦截所有进出 LLM 的通信，记录完整内容并计算 token 消耗。
提供环形缓冲区查询接口。
"""

import threading
import time
import logging
import json
import os
import atexit
import uuid
from dataclasses import dataclass, field, asdict, fields
from typing import Optional

logger = logging.getLogger(__name__)

# B3/F2（P0）：usage 真值 → 六指标（定义见 agent/monitoring/prometheus.py）
# 【不易】prometheus 不可用（未装 prometheus_client / 导入链异常）不得阻断 LLM 监控：
# 取不到就退化为 None，记录与主链路照常。
try:
    from agent.monitoring.prometheus import (
        record_llm_usage as _record_llm_usage,
        record_tool_selected as _record_tool_selected,
    )
except Exception:  # noqa: BLE001
    _record_llm_usage = None
    _record_tool_selected = None

try:
    from agent.logging_utils import log_dict as _log_dict
except Exception:  # noqa: BLE001
    _log_dict = None

# SingletonManager 统一收口（保留 fallback 变量 _monitor 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None

MAX_RECORDS = 500  # 环形缓冲区大小（向后兼容别名，运行时从 Config 读取）

# 会话最后一条通信的落盘位置：服务关闭后重开仍可在「LLM 通信监控」回看
# （写盘时机：每记录一条即写 + 进程退出 atexit 兜底；见 LLMMonitor._persist_last）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PERSIST_FILE = os.path.join(_PROJECT_ROOT, "data", "llm_monitor_last.json")

# 写盘节流间隔（秒）：两次快照写盘的最小间隔，避免拖慢 LLM 主路径
PERSIST_MIN_INTERVAL_S = 5.0


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

    # ── TASK-S2-03 UTC 成本埋点（§6.2/§6.6 cost；**additive**，默认值保持既有行为） ──
    task_id: str = ""                 # 来自 S2-01 TraceContext
    workspace_id: str = ""            # 来自 S2-01 TraceContext（P7.1-19）
    subject_id: str = ""              # 来自 S2-01 TraceContext
    retries: int = 0                  # 本次交互内的重试次数
    shadow_overhead_ms: float = 0.0   # 影子/附加开销（§6.6 shadow_overhead）
    cache_hit: bool = False           # P7.1-18：命中缓存 → 不计 token 成本
    cost_normalized_cents: float = 0.0  # 归一成本（锚价 × 系数表）

    # ── B3/F2：usage 真值（**additive**：API 未上报时 available=False，既有估算字段不受影响） ──
    usage_available: bool = False        # 响应里是否真的带 usage
    usage_prompt_tokens: int = 0         # 输入 token 真值（含命中缓存的部分）
    usage_completion_tokens: int = 0     # 输出 token 真值
    prompt_cache_hit_tokens: int = 0     # 其中命中服务端前缀缓存的部分
    prompt_cache_miss_tokens: int = 0    # 其中未命中的部分
    cache_reported: bool = False         # API 是否上报缓存字段（决定是否计入命中率分母）
    reasoning_tokens: int = 0            # 思维链 token（DeepSeek reasoner / o 系列）
    usage_cost_usd: float = 0.0          # usage 真值 × 价目表（USD）
    call_cache_hit_ratio: float = 0.0    # 本次调用的前缀缓存命中率（未上报 → 0）

    # ── 会话持久化（重启后回填「上次会话最后一条通信」；additive，默认 False） ──
    restored: bool = False            # True = 由磁盘回填的上次会话遗留记录

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp_str"] = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        d["timestamp_full"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        return d


class LLMMonitor:
    """LLM 通信监控器 — 环形缓冲区（+ 会话最后一条通信落盘）"""

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
        # 会话持久化：启动即回填上次会话最后一条通信（服务关闭时已落盘）
        self._persist_file = PERSIST_FILE
        self._last_persist_ts = 0.0
        self.restored_from_disk = False
        self._restore_last()

    # ── 会话持久化（关闭时保存最后一条通信；重启后回填） ──

    def _restore_last(self) -> bool:
        """启动时回填上次会话落盘的最后一条通信（幂等，失败静默）。"""
        try:
            if not os.path.exists(self._persist_file):
                return False
            with open(self._persist_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return False
            allowed = {fld.name for fld in fields(LLMInteraction)}
            payload = {k: v for k, v in data.items() if k in allowed}
            payload["restored"] = True
            interaction = LLMInteraction(**payload)
            if not interaction.id:
                interaction.id = uuid.uuid4().hex[:12]
            with self._lock:
                self._records.append(interaction)
            self.restored_from_disk = True
            logger.info("LLM 监控：已回填上次会话最后一条通信记录（%s）",
                        interaction.timestamp_str if hasattr(interaction, "timestamp_str")
                        else interaction.id)
            return True
        except Exception as e:  # noqa: BLE001 回填失败不影响监控主流程
            logger.debug("LLM 监控记录回填失败: %s", e)
            return False

    def persist_last(self, interaction: Optional[LLMInteraction] = None) -> bool:
        """把最后一条通信内容落盘（供服务关闭 / 手动调用）。

        Args:
            interaction: 指定要落盘的记录；缺省取缓冲区最后一条

        Returns:
            是否成功写盘
        """
        try:
            if interaction is None:
                with self._lock:
                    interaction = self._records[-1] if self._records else None
            if interaction is None:
                return False
            payload = (interaction.to_dict() if hasattr(interaction, "to_dict")
                       else dict(interaction))
            payload["_persisted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            os.makedirs(os.path.dirname(self._persist_file), exist_ok=True)
            tmp = f"{self._persist_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, default=str)
            os.replace(tmp, self._persist_file)
            self._last_persist_ts = time.time()
            return True
        except Exception as e:  # noqa: BLE001 落盘失败不影响监控主流程
            logger.debug("LLM 监控记录落盘失败: %s", e)
            return False

    def persisted_info(self) -> dict:
        """已落盘的最后一条通信摘要（供前端标注「上次会话」来源）"""
        try:
            if not os.path.exists(self._persist_file):
                return {}
            with open(self._persist_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                "file": os.path.basename(self._persist_file),
                "persisted_at": data.get("_persisted_at", ""),
                "record_id": data.get("id", ""),
                "timestamp_full": data.get("timestamp_full", ""),
            }
        except Exception:  # noqa: BLE001 读取失败返回空
            return {}

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

        # 会话持久化：每条通信即写盘（服务被关闭/崩溃后重开仍能回看最后一条）
        # 节流：两次写盘间隔不足 PERSIST_MIN_INTERVAL_S 时跳过（避免每次调用都
        # 序列化整个请求体拖慢 LLM 主路径）；服务关闭时由 atexit 保证最终落盘。
        _now = time.time()
        if _now - self._last_persist_ts >= PERSIST_MIN_INTERVAL_S:
            self.persist_last(interaction)

        # TASK-S2-03：UTC 成本埋点 + 模型降级事件（best-effort，绝不阻断监控主路径）
        self._emit_observability(interaction)

    def _emit_observability(self, interaction: LLMInteraction) -> None:
        """把一次 LLM 交互接入 events.v1（§6.6 cost / P7.1-18 model.degraded）

        - **cost**：task_id/workspace_id/subject_id（S2-01 TraceContext 注入）/
          model/tokens_in/out/retries/shadow_overhead/cents/cache_hit；
          缓存命中不计 token（P7.1-18）；同时算出归一成本并回填到 interaction。
        - **model.degraded**：本次调用带 error → 主模型失败收口 emit
          `model.degraded {from, to, reason}`（§11.6.0 `E_MODEL_DEGRADED`）；
          是否真的切换由 `CP_MODEL_FALLBACK_ENABLED`（默认 0）决定，事件如实标注。
        """
        try:
            from agent.observability import events as _events
            from agent.observability import utc as _utc
            fields = _events.trace_fields()
            task_id = interaction.task_id or fields.get("task_id") or ""
            workspace_id = interaction.workspace_id or fields.get("workspace_id") or ""
            subject_id = interaction.subject_id or fields.get("subject_id") or ""
            envelope = _utc.record_cost(
                model=interaction.model, provider=interaction.provider,
                source=interaction.source, tokens_in=interaction.request_tokens,
                tokens_out=interaction.response_tokens,
                cache_hit=interaction.cache_hit, retries=interaction.retries,
                shadow_overhead_ms=interaction.shadow_overhead_ms,
                task_id=task_id, interaction_id=interaction.id,
                duration_ms=interaction.duration_ms, error=interaction.error)
            if envelope is not None:
                interaction.cost_normalized_cents = float(
                    envelope.payload.get("cost_normalized_cents") or 0.0)
            interaction.task_id = task_id
            interaction.workspace_id = workspace_id
            interaction.subject_id = subject_id
        except Exception as e:  # noqa: BLE001 成本埋点 best-effort
            logger.debug("LLM 成本埋点失败: %s", e)
        if interaction.error:
            try:
                from agent.observability import model_degrade as _degrade
                _degrade.report_model_degraded(
                    from_model=interaction.model,
                    reason=f"{interaction.source or 'llm'}: {interaction.error}"[:400],
                    provider=interaction.provider)
            except Exception as e:  # noqa: BLE001 降级埋点 best-effort
                logger.debug("model.degraded 埋点失败: %s", e)

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
        """清除所有记录（含已落盘的会话快照，避免重启后又回来）"""
        with self._lock:
            self._records.clear()
        try:
            if os.path.exists(self._persist_file):
                os.remove(self._persist_file)
        except Exception as e:  # noqa: BLE001 删除快照失败不影响清空内存
            logger.debug("删除 LLM 监控快照失败: %s", e)

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

    # ── B3/F2：usage 真值解析（只读；缺失 → available=False） ──

    @staticmethod
    def extract_usage(response_obj) -> dict:
        """从 LLM 响应对象解析 usage 真值

        兼容三种形态:
        - OpenAI / DeepSeek 兼容: usage.prompt_tokens / completion_tokens /
          prompt_cache_hit_tokens / prompt_cache_miss_tokens（DeepSeek 前缀缓存字段）/
          prompt_tokens_details.cached_tokens / completion_tokens_details.reasoning_tokens
        - Anthropic Messages API: usage.input_tokens / output_tokens / cache_read_input_tokens
        - 流式响应对象（Stream）或被包装层转成字符串的响应: 无 usage → available=False

        Returns:
            dict(available, prompt_tokens, completion_tokens, total_tokens,
                 prompt_cache_hit_tokens, prompt_cache_miss_tokens,
                 cache_reported, reasoning_tokens)

        【不易】只读: 不修改响应对象; 任何异常都退化为 available=False, 绝不上抛。
        【关键】cache_reported 只在 API **明确给出**缓存字段时为 True ——
            否则无法区分「本次 0 命中」与「该 provider 压根不报缓存」,
            会把"没上报"错算成"未命中"而污染 cache_hit_ratio 的分母。
        """
        out = {
            "available": False, "prompt_tokens": 0, "completion_tokens": 0,
            "total_tokens": 0, "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 0, "cache_reported": False,
            "reasoning_tokens": 0,
        }

        def _raw(container, key):
            """同时支持 pydantic 对象与 dict 两种 usage 载体"""
            if container is None:
                return None
            if isinstance(container, dict):
                return container.get(key)
            return getattr(container, key, None)

        def _int(value):
            try:
                return max(int(value), 0)
            except Exception:
                return 0

        try:
            usage = _raw(response_obj, "usage")
            if usage is None:
                return out

            prompt = _int(_raw(usage, "prompt_tokens"))
            completion = _int(_raw(usage, "completion_tokens"))
            if prompt == 0 and completion == 0:
                # Anthropic Messages API 形态
                prompt = _int(_raw(usage, "input_tokens"))
                completion = _int(_raw(usage, "output_tokens"))
            total = _int(_raw(usage, "total_tokens")) or (prompt + completion)
            if prompt == 0 and completion == 0 and total == 0:
                # usage 字段存在但全 0 ⇒ 视为未上报（不猜、不编）
                return out

            hit = miss = 0
            cache_reported = False
            raw_hit = _raw(usage, "prompt_cache_hit_tokens")
            raw_miss = _raw(usage, "prompt_cache_miss_tokens")
            if raw_hit is not None or raw_miss is not None:
                hit = _int(raw_hit)
                miss = _int(raw_miss)
                if raw_miss is None:
                    miss = max(prompt - hit, 0)
                if raw_hit is None:
                    hit = max(prompt - miss, 0)
                cache_reported = True
            else:
                details = _raw(usage, "prompt_tokens_details")
                cached_tokens = (_raw(details, "cached_tokens")
                                 if details is not None else None)
                if cached_tokens is None:
                    cached_tokens = _raw(usage, "cache_read_input_tokens")
                if cached_tokens is not None:
                    hit = _int(cached_tokens)
                    miss = max(prompt - hit, 0)
                    cache_reported = True

            c_details = _raw(usage, "completion_tokens_details")
            reasoning = _int(_raw(c_details, "reasoning_tokens")) if c_details is not None else 0
            if not reasoning:
                reasoning = _int(_raw(usage, "reasoning_tokens"))

            out.update({
                "available": True,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "prompt_cache_hit_tokens": hit,
                "prompt_cache_miss_tokens": miss,
                "cache_reported": cache_reported,
                "reasoning_tokens": reasoning,
            })
            return out
        except Exception as e:  # noqa: BLE001 usage 解析失败不得影响记录
            logger.debug("解析 LLM usage 失败: %s", e)
            return out

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
        retries: int = 0,
        shadow_overhead_ms: float = 0.0,
        cache_hit: bool = False,
        task_id: str = "",
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

        # ── B3/F2：usage 真值 → 六指标 ──────────────────────────────────
        # 修复的历史缺陷：成本此前只用本地 tiktoken 估算（见本文件 get_stats），
        # 而本函数的调用方（_wrapped_create）**已拿到 response_obj 却不读 usage**。
        # 【不易】计量失败绝不影响记录与主链路。
        usage = LLMMonitor.extract_usage(response_obj)
        usage_metrics = {}
        if usage.get("available") and _record_llm_usage is not None:
            try:
                usage_metrics = _record_llm_usage(usage, model=model, source=source) or {}
                if _log_dict is not None:
                    logger.info(_log_dict({
                        "module_name": "llm_monitor",
                        "action": "llm.usage.parsed",
                        "message": "LLM usage 真值: prompt=%d completion=%d cached=%d miss=%d" % (
                            usage["prompt_tokens"], usage["completion_tokens"],
                            usage["prompt_cache_hit_tokens"],
                            usage["prompt_cache_miss_tokens"]),
                        "model": model,
                        "source": source,
                        "prompt_tokens": usage["prompt_tokens"],
                        "completion_tokens": usage["completion_tokens"],
                        "prompt_cache_hit_tokens": usage["prompt_cache_hit_tokens"],
                        "prompt_cache_miss_tokens": usage["prompt_cache_miss_tokens"],
                        "reasoning_tokens": usage["reasoning_tokens"],
                        "cache_reported": usage["cache_reported"],
                        "call_cache_hit_ratio": usage_metrics.get("call_cache_hit_ratio"),
                        "cumulative_cache_hit_ratio": usage_metrics.get("cumulative_cache_hit_ratio"),
                        "cost_usd": usage_metrics.get("cost_usd"),
                    }))
            except Exception as e:  # noqa: BLE001 指标记录失败不得影响 LLM 监控记录
                logger.debug("记录 LLM usage 指标失败: %s", e)

        # 工具选中计数：tools 即**本轮路由选中的工具集**（下发给模型的 tools 字段）
        if tools and _record_tool_selected is not None:
            try:
                _record_tool_selected(tools)
            except Exception as e:  # noqa: BLE001
                logger.debug("记录 tool_selected 指标失败: %s", e)

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
            retries=max(0, int(retries or 0)),
            shadow_overhead_ms=round(float(shadow_overhead_ms or 0.0), 3),
            cache_hit=bool(cache_hit),
            task_id=task_id or _current_task_id(),
            # B3/F2：usage 真值（API 未上报时保持默认值，既有估算字段不变）
            usage_available=bool(usage.get("available")),
            usage_prompt_tokens=usage.get("prompt_tokens", 0),
            usage_completion_tokens=usage.get("completion_tokens", 0),
            prompt_cache_hit_tokens=usage.get("prompt_cache_hit_tokens", 0),
            prompt_cache_miss_tokens=usage.get("prompt_cache_miss_tokens", 0),
            cache_reported=bool(usage.get("cache_reported")),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            usage_cost_usd=float(usage_metrics.get("cost_usd") or 0.0),
            call_cache_hit_ratio=float(usage_metrics.get("call_cache_hit_ratio") or 0.0),
            workspace_id=_current_trace_field("workspace_id"),
            subject_id=_current_trace_field("subject_id"),
        )


def _current_trace_field(name: str) -> str:
    """读取当前 S2-01 TraceContext 的叶子字段（无上下文 → ""）"""
    try:
        from agent.observability.events import trace_fields
        return str(trace_fields().get(name) or "")
    except Exception:  # noqa: BLE001 无上下文不是错误
        return ""


def _current_task_id() -> str:
    return _current_trace_field("task_id")


# ── 全局单例 ──
_monitor: Optional[LLMMonitor] = None  # 保留作为 fallback

# 安装钩子时备份的原始方法（uninstall_hooks 恢复用，避免闭包悬空引用旧实例）
_orig_do_chat = None
_orig_do_summarize = None
_orig_get_client = None


def _create_llm_monitor(config=None):
    """LLMMonitor 工厂（供 SingletonManager 使用）"""
    return LLMMonitor()


def _cleanup_llm_monitor(monitor):
    """清理钩子：卸载 LLMService 钩子（仅测试重置时调用，幂等）"""
    if monitor is not None:
        uninstall_hooks()
        monitor._hooks_installed = False


def get_monitor() -> LLMMonitor:
    """获取全局 LLM 监控器单例

    Returns:
        LLMMonitor 实例
    """
    if _SINGLETON_AVAILABLE:
        return get_singleton("llm_monitor")
    global _monitor
    if _monitor is None:
        _monitor = _create_llm_monitor()
    return _monitor


def reset_llm_monitor():
    """重置全局 LLM 监控器单例（仅用于测试）

    注意：reset 会触发 cleanup 钩子卸载 LLMService 钩子，避免闭包悬空引用。
    """
    global _monitor
    if _SINGLETON_AVAILABLE:
        reset_singleton("llm_monitor")
    _monitor = None


def uninstall_hooks():
    """卸载 LLMService 钩子，恢复原始方法（幂等）

    供 reset 清理钩子调用；仅恢复本次安装的补丁，
    未安装过钩子时安全跳过。
    """
    global _orig_do_chat, _orig_do_summarize, _orig_get_client
    try:
        from memory.llm_service import LLMService
    except Exception:
        return
    if _orig_do_chat is not None:
        LLMService._do_chat = _orig_do_chat
    if _orig_do_summarize is not None:
        LLMService._do_summarize = _orig_do_summarize
    if _orig_get_client is not None:
        LLMService._get_client = _orig_get_client
    _orig_do_chat = _orig_do_summarize = _orig_get_client = None


def install_hooks():
    """安装 LLMService 钩子"""
    global _orig_do_chat, _orig_do_summarize
    monitor = get_monitor()
    if monitor._hooks_installed:
        return

    try:
        from memory.llm_service import LLMService

        # 保存原始方法（模块级，供 uninstall_hooks 恢复）
        _orig_do_chat = LLMService._do_chat
        _orig_do_summarize = LLMService._do_summarize

        orig_do_chat = _orig_do_chat
        orig_do_summarize = _orig_do_summarize

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


# ── F3-2：流式响应的计量代理 ──────────────────────────────────────────────
# 病灶（实测见 docs/audit_skill_governance/F3-2.md §1）：client.chat.completions
# .create(stream=True) 返回的是**惰性** Stream 对象；_wrapped_create 在它被消费
# **之前**就 return 了 ⇒ 那一刻 extract_usage(stream) 必然是 available=False。
# 于是工作台（plugins/chat.py 的 SSE 链路）每条记录都是 prompt=0/hit=0/miss=0，
# 成本与**前缀缓存命中率**在两条链路里一半可见、一半不可见。
#
# 做法：把 Stream 包一层**透明**代理，惰性转发 chunk，在流结束时（正常迭代完 /
# 显式 close / 迭代被中断）才落**一条**记录，并把沿途看到的最后一个 chunk.usage
# 交给既有的 LLMMonitor.create_from_api_call ⇒ 记录仍然落在**同一个**监控环形
# 缓冲区、同一张表、同一个 source，只是这次带上了 usage 真值。


def _usage_payload(usage):
    """把 chunk.usage 规整成 extract_usage 能吃的形态（pydantic / dict 都兼容）

    为什么先 model_dump()：OpenAI SDK 的 CompletionUsage 用 extra="allow" 承载
    DeepSeek 的 prompt_cache_hit_tokens / prompt_cache_miss_tokens；转成 dict
    后既保留这些额外字段，也让落库的 response_full 是人可读的原始计量。
    """
    if usage is None:
        return None
    for attr in ("model_dump", "to_dict", "dict"):
        fn = getattr(usage, attr, None)
        if callable(fn):
            try:
                dumped = fn()
                if isinstance(dumped, dict):
                    return dumped
            except Exception:  # noqa: BLE001 取不到就退回原对象
                pass
    return usage


class _MonitoredStream:
    """流式响应代理：透明转发 chunk，并在流结束时补一条带 usage 的监控记录

    【不易】三条硬约束：
      1. **不改变用户可见内容与事件顺序** —— 本类只做透传，不增删改任何 chunk；
      2. **服务端不报 usage 时安全降级** —— usage 保持 None ⇒ 记录照落，
         usage_available=False（既有语义），不抛异常、不丢内容；
      3. **客户端中断（关页面）不丢记录** —— 上游只在生成完成后才发 usage，
         中断时 usage 客观上不存在；此时仍落一条记录（usage 未上报），
         并记一条日志，绝不因计量失败反过来打断/污染主链路。
    """

    def __init__(self, stream, monitor, meta, start_ts=None):
        self._ys_stream = stream
        self._ys_monitor = monitor
        self._ys_meta = meta or {}
        self._ys_start = start_ts if start_ts is not None else time.time()
        self._ys_usage = None
        self._ys_finish_reason = ""
        self._ys_chunks = 0
        self._ys_recorded = False
        self._ys_aborted = False
        self._ys_error = ""

    # ── 透明转发：任何本类没定义的属性都交回真正的 Stream ──
    def __getattr__(self, name):
        try:
            stream = self.__dict__["_ys_stream"]
        except KeyError:  # 初始化中途的属性探测
            raise AttributeError(name)
        return getattr(stream, name)

    def __iter__(self):
        try:
            for chunk in self._ys_stream:
                self._ys_chunks += 1
                # usage 在**最后一个 chunk** 上；OpenAI 规范里该 chunk 的
                # choices 可能是空数组（本端点实测非空，见 F3-2.md §4），
                # 所以这里必须先取 usage、再由消费端决定怎么处理 choices。
                _u = getattr(chunk, "usage", None)
                if _u is not None:
                    self._ys_usage = _u
                _choices = getattr(chunk, "choices", None)
                if _choices:
                    _fr = getattr(_choices[0], "finish_reason", None)
                    if _fr:
                        self._ys_finish_reason = _fr
                yield chunk
        except GeneratorExit:
            # 消费端被关闭（典型：用户关页面 → SSE 生成器关闭 → 本迭代器关闭）
            self._ys_aborted = True
            raise
        except BaseException as e:  # noqa: BLE001 记录后原样抛出，不吞异常
            self._ys_error = "%s: %s" % (type(e).__name__, e)
            raise
        finally:
            self._finalize()

    def close(self):
        try:
            _c = getattr(self._ys_stream, "close", None)
            if _c is not None:
                _c()
        except Exception as e:  # noqa: BLE001 关闭失败不影响计量
            logger.debug("关闭流式响应失败: %s", e)
        finally:
            self._finalize()

    def __enter__(self):
        _e = getattr(self._ys_stream, "__enter__", None)
        if _e is not None:
            _e()
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            _x = getattr(self._ys_stream, "__exit__", None)
            if _x is not None:
                return _x(exc_type, exc, tb)
        finally:
            self._finalize()
        return False

    def __del__(self):
        # 兜底：消费方既没迭代完也没 close（被丢弃）时仍尽量落一条记录
        try:
            self._finalize()
        except Exception:  # noqa: BLE001 析构期绝不抛
            pass

    def _finalize(self):
        if self._ys_recorded:
            return
        self._ys_recorded = True
        _usage = _usage_payload(self._ys_usage)
        if self._ys_aborted:
            logger.warning(
                "event=llm_stream_aborted source=%s chunks=%d usage_reported=%s "
                "note=客户端中断，上游不会补 usage；记录仍落库（usage_available=False）",
                self._ys_meta.get("source", "tool_calling"), self._ys_chunks,
                _usage is not None)
            # 中断时主动放掉底层 HTTP 连接（否则要等 GC 才回收）；失败不影响计量。
            try:
                _c = getattr(self._ys_stream, "close", None)
                if _c is not None:
                    _c()
            except Exception as e:  # noqa: BLE001
                logger.debug("中断后关闭流式响应失败: %s", e)
        try:
            record = LLMMonitor.create_from_api_call(
                system_prompt=self._ys_meta.get("system_prompt", ""),
                messages=self._ys_meta.get("messages", []),
                tools=self._ys_meta.get("tools", []),
                # 只把 usage 当"响应载荷"传进去：extract_usage 同时支持 dict 载体，
                # 于是流式记录与主线的记录**走同一套解析/同一张表**。
                response_obj=({"usage": _usage} if _usage is not None else None),
                model=self._ys_meta.get("model", ""),
                provider=self._ys_meta.get("provider", ""),
                source=self._ys_meta.get("source", "tool_calling"),
                duration_ms=(time.time() - self._ys_start) * 1000.0,
                error=self._ys_error,
            )
            self._ys_monitor.record(record)
        except Exception as e:  # noqa: BLE001 计量失败绝不影响主链路
            logger.debug("记录流式 LLM 调用失败: %s", e)


def _wrap_get_client_for_tool_calling(monitor):
    """修补 LLMService._get_client，确保任何通过它创建的 client 的 create 方法被包装"""
    global _orig_get_client
    try:
        from memory.llm_service import LLMService

        # 保存原始方法（模块级，供 uninstall_hooks 恢复）
        _orig_get_client = LLMService._get_client

        orig_get_client = _orig_get_client

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

                def _request_meta(kwargs):
                    """从 create() 入参提取建记录所需的**请求侧**字段（流式/非流式共用）"""
                    messages = kwargs.get("messages", [])
                    tools = kwargs.get("tools", [])
                    sys_prompt = ""
                    if messages and len(messages) > 0 and isinstance(messages[0], dict) and messages[0].get("role") == "system":
                        sys_prompt = messages[0].get("content", "")
                        messages = messages[1:]
                    return {
                        "system_prompt": sys_prompt,
                        "messages": messages,
                        "tools": tools,
                        "model": kwargs.get("model", model),
                        "provider": provider,
                        "source": "tool_calling",
                    }

                def _record_call(meta, response_obj, error, duration_ms):
                    """把一次 create 调用落进监控环形缓冲区（非流式/失败路径用）"""
                    try:
                        record = LLMMonitor.create_from_api_call(
                            system_prompt=meta.get("system_prompt", ""),
                            messages=meta.get("messages", []),
                            tools=meta.get("tools", []),
                            response_obj=response_obj,
                            model=meta.get("model", ""),
                            provider=meta.get("provider", ""),
                            source=meta.get("source", "tool_calling"),
                            duration_ms=duration_ms,
                            error=error,
                        )
                        monitor.record(record)
                    except Exception as e:
                        logger.debug("记录 LLM 工具调用失败: %s", e)

                def _wrapped_create(*args, **kwargs):
                    start = time.time()
                    try:
                        response_obj = orig_create(*args, **kwargs)
                    except Exception as e:
                        _record_call(_request_meta(kwargs), None, str(e),
                                     (time.time() - start) * 1000)
                        raise
                    # ── F3-2：流式分支的 usage 只能等流结束才拿得到 ──
                    # create(stream=True) 返回的是**惰性** Stream，此刻一个 chunk 都还
                    # 没到；在这里直接建记录 ⇒ 永远是一条 usage 未上报的空记录
                    # （这正是工作台缓存命中率测不到的根因）。改成交给
                    # _MonitoredStream：流消费完（或中断/关闭）时落**一条**记录，
                    # usage 取沿途最后一个 chunk。非流式分支逐字保持原行为。
                    if kwargs.get("stream"):
                        return _MonitoredStream(response_obj, monitor,
                                                _request_meta(kwargs), start)
                    _record_call(_request_meta(kwargs), response_obj, "",
                                 (time.time() - start) * 1000)
                    return response_obj

                client.chat.completions.create = _wrapped_create
                client.__llm_monitored = True

            return client

        LLMService._get_client = _patched_get_client
        logger.info("LLM 监控钩子已安装（client.create 层）")

    except Exception as e:
        logger.debug("安装 client.create 钩子失败: %s", e)


# 注册单例工厂（置于文件末尾，确保 get_monitor / install_hooks 均已定义）
if _SINGLETON_AVAILABLE:
    register_singleton("llm_monitor", _create_llm_monitor,
                       cleanup_fn=_cleanup_llm_monitor)


def persist_session_last() -> bool:
    """服务关闭时保存「会话最后一条 LLM 通信内容」（atexit / 显式关机调用）。

    已初始化的监控器才落盘：进程退出路径不得为了持久化而**新建**监控器。
    """
    try:
        if _SINGLETON_AVAILABLE:
            from agent.utils.singleton_manager import is_initialized
            if not is_initialized("llm_monitor"):
                return False
            monitor = get_singleton("llm_monitor")
        else:
            monitor = _monitor
        if monitor is None:
            return False
        ok = monitor.persist_last()
        if ok:
            logger.info("LLM 监控：会话最后一条通信已保存（服务关闭）")
        return ok
    except Exception as e:  # noqa: BLE001 退出路径绝不抛异常
        logger.debug("关闭时保存 LLM 会话快照失败: %s", e)
        return False


# 服务（进程）关闭时自动保存会话最后一条通信内容
atexit.register(persist_session_last)
