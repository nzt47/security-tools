"""委派真执行器（v7.2 §3.9 + §3.10 + §4.2 + §5.7 + §5.9）

【任务定位】
    把 ``agent/subagent/`` 从占位骨架升级为**真实委派执行器**。本模块是编排中枢，
    串起以下已交付件而**不重复实现**它们：

      ``delegation.py``   八要素契约 + task_file 物化（准入闸门）
      ``channel.py``      §3.10 CLI 通道物理协议 + 输出解析三级降级 + taint
      ``toolset.py``      §5.7 机制 3 工具裁剪子集（对齐 S4-01 Actor 矩阵）
      ``credentials.py``  §5.9 临时凭据（TTL ≤ 任务时长 + finally 销毁）
      ``sandbox.py``      §5.9 第三方执行默认隔离（无宿主网络/无 SSH agent/无 $HOME）
      ``barrier.py``      §4.2 并发上限 + 回压
      ``collection.py``   §3.9 回收三件套 + 计费 + stage 闸门
      ``trace_v2``        §3.4 统一 Trace（**S2-01 ``TraceContext.child()``**）

【一次委派的完整时序（命中验收清单的每一步）】
    1. **八要素校验前置**（``require_delegation``）——不合格**立即拒绝**，不落盘、
       不签发凭据、不写 Trace（拒绝本身也留痕：审计 ``subagent.delegation.rejected``）。
    2. 物化 task_file（§3.10）。
    3. 计算裁剪工具集（申请 ∩ 授权子集 − 矩阵拒绝项）。
    4. **子 Trace**：``sub_ctx = parent_ctx.child()``（**复用 S2-01，不自建上下文
       传递**），``enter()`` 到 ContextVar；跨线程由 ``execute_many`` 在**worker 内**
       显式重建上下文（ContextVar 不跨线程自动继承——上游已知坑 #4）。
    5. **临时凭据**：``credential_scope`` 进入时签发、``finally`` 无条件销毁；
       TTL 由契约⑦的任务时长封顶。
    6. 叠加隔离环境（第三方默认隔离）→ 调 CLI 通道 → 三级降级解析。
    7. **工具裁剪闸门**：上游**声称**调用过的工具逐个过闸（含别名/间接路径），
       越界即判本次委派失败（``E_TOOL_NOT_AUTHORIZED``）。
    8. 记录 Trace（``actor=sub_agent`` + ``parent_trace_id``）。
    9. 回收三件套 → 成本记账 → 回调。

【回调（契约⑧）】
    ``callback_dispatcher`` 可注入（真实投递）；缺省把「回调地址 + 结果摘要」写审计，
    即 §3.9 的「记录结果/触发后续」。回调失败 best-effort，**不**改变委派结果。

【依赖纪律】
    仅依赖 ``agent.subagent.*`` 与 ``agent.security.actor_matrix``（纯表），
    以及惰性使用的 ``agent.observability.trace_v2``（由调用方注入 facade 时零导入）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from agent.security.actor_matrix import ACTOR_SUB_AGENT

from agent.subagent.barrier import BackpressureTimeout, ConcurrencyBarrier
from agent.subagent.channel import (
    DEFAULT_MAX_TURNS,
    DEFAULT_OUTPUT_FORMAT,
    ENV_REPLACE,
    E_UPSTREAM_FORMAT,
    ChannelExecutor,
    ChannelInvocation,
    ChannelOutput,
    RawOutput,
    build_cli_argv,
    default_agent_cli,
    default_max_turns,
    make_executor,
    resolve_channel_output,
)
from agent.subagent.collection import (
    CollectedTriad,
    CloudReviewer,
    CostLedger,
    CostRecord,
    TriadCollector,
)
from agent.subagent.credentials import (
    CredentialError,
    TemporaryCredential,
    TemporaryCredentialManager,
    credential_scope,
)
from agent.subagent.delegation import (
    DelegationContext,
    DelegationRejected,
    write_task_file,
)
from agent.subagent.sandbox import apply_isolation_env, isolation_policy_report
from agent.subagent.scoped_memory_gate import ScopedMemoryGate
from agent.subagent.toolset import (
    E_TOOL_NOT_AUTHORIZED,
    SubAgentToolset,
)

logger = logging.getLogger(__name__)

#: 能力标识（Trace 行）
CAPABILITY_DELEGATE = "subagent.delegate"
CAPABILITY_DELEGATE_TOOL = "subagent.delegate.tool"

#: 错误码
E_DELEGATION_FAILED = "E_DELEGATION_FAILED"

#: 缺省并发上限（§4.2 并发上限 N）
DEFAULT_MAX_CONCURRENCY = 4

#: 委派工作区（task_file 落盘根；``.env`` 可调 ``CP_SUBAGENT_WORKSPACE``）
WORKSPACE_ENV = "CP_SUBAGENT_WORKSPACE"

#: 上游载荷里承载工具调用声明的候选键
_TOOL_CALL_KEYS: Tuple[str, ...] = ("tool_calls", "tools_used", "tool_invocations")


# ════════════════════════════════════════════════════════════
#  执行结果
# ════════════════════════════════════════════════════════════


@dataclass
class ExecutionOutcome:
    """一次委派执行的结果（可直接入审计/验收报告）

    Attributes:
        delegation_id: 委派标识。
        ok: 是否成功产出结构化结果**且**通过全部闸门（含工具裁剪闸门）。
        tier: 通道解析命中的降级层级（见 ``channel.TIERS``）。
        output_text: 上游原文——**外来文本，不可信**（§5.7 机制 1）。只可用于复评
            与归档；**不得**拼接进工具参数或 system prompt。
        payload: 结构化载荷（来自 JSON Lines / LLM 抽取）。
        artifacts: 产物条目。
        trace_id / trace: 委派子 Trace（``actor=sub_agent`` + ``parent_trace_id``）。
        triad: 回收三件套。
        cost: 成本记账记录。
        error_code / error: 失败原因。
        error_detail: 失败的**机器可读**明细（如八要素拒绝的 ``missing`` 列表）——
            调用方据此决定补哪一项，无需解析中文原因串。
        tool_calls / tool_violations: 上游声称的工具调用与越界明细。
        toolset: 裁剪工具集清单（白名单 + 拒绝计数）。
        credentials / credentials_destroyed: 凭据审计视图与销毁证据。
        isolation: 隔离策略声明。
        callback: 回调投递结果。
        task_file: task_file 落盘路径（复现用）。
        role_audit: 角色档位的审计留痕证据（`{action, tier, recorded, ...}`）；
            默认档（`template`）为空 dict，非默认档必有该键（`recorded=False` 表示
            审计写入失败 —— **如实报告**，不伪装成"已留痕"）。
        memory: brokered 记忆档的解析结果（`{mode, source, constraint_chars,
            degraded, memory_count, recorded}`）；**不含记忆正文**。
            scoped 档另含 `write`（结构化事实写回）与 `ops`（子代理声明的记忆读写
            执行结果：计数 + 错误码）；none 档为空 dict（默认路径不新增该键，
            与 role_audit 同款"有才回"）。
    """

    delegation_id: str = ""
    ok: bool = False
    tier: str = ""
    output_text: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    artifacts: Tuple[Dict[str, Any], ...] = ()
    trace_id: str = ""
    trace: Optional[Mapping[str, Any]] = None
    triad: Optional[CollectedTriad] = None
    cost: Optional[CostRecord] = None
    error_code: str = ""
    error: str = ""
    error_detail: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    attempts: int = 0
    tool_calls: Tuple[Dict[str, Any], ...] = ()
    tool_violations: Tuple[Dict[str, Any], ...] = ()
    toolset: Dict[str, Any] = field(default_factory=dict)
    credentials: Tuple[Dict[str, Any], ...] = ()
    credentials_destroyed: bool = False
    isolation: Dict[str, Any] = field(default_factory=dict)
    callback: Dict[str, Any] = field(default_factory=dict)
    task_file: str = ""
    sub_reason: str = ""
    invocation: Optional[Dict[str, Any]] = None
    role_audit: Dict[str, Any] = field(default_factory=dict)
    memory: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_credentials(self) -> bool:
        return bool(self.credentials)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "delegation_id": self.delegation_id,
            "ok": bool(self.ok),
            "tier": self.tier,
            "error_code": self.error_code,
            "error": self.error,
            "error_detail": dict(self.error_detail),
            "sub_reason": self.sub_reason,
            "duration_ms": round(float(self.duration_ms), 2),
            "attempts": int(self.attempts),
            "artifact_count": len(self.artifacts),
            "trace_id": self.trace_id,
            "tool_call_count": len(self.tool_calls),
            "tool_violation_count": len(self.tool_violations),
            "toolset": dict(self.toolset),
            "credentials": [dict(c) for c in self.credentials],
            "credentials_destroyed": bool(self.credentials_destroyed),
            "isolation": dict(self.isolation),
            "callback": dict(self.callback),
            "task_file": self.task_file,
            "triad": (self.triad.to_dict() if self.triad else None),
            "cost": (self.cost.to_dict() if self.cost else None),
            "invocation": self.invocation,
            "role_audit": dict(self.role_audit),
            "memory": dict(self.memory),
        }


# ════════════════════════════════════════════════════════════
#  内部 LLM 执行器（§3.10 的「内部 executor 等价实现」）
# ════════════════════════════════════════════════════════════

#: 委派执行的 system prompt——**云枢自有文本**，绝不拼接外来内容（§5.7 机制 1/2）
DELEGATE_SYSTEM_PROMPT = (
    "你是云枢委派出去的子代理执行体。你会收到一份 task_file（八要素上下文包）。"
    "严格在「约束」与「禁止事项」内完成「目标」，并按要求产出「产物格式」。"
    "最后一轮必须只输出**单个 JSON 对象**，键包含：status(\"done\")、summary、"
    "artifacts(数组)、self_eval(对象：verdict/score/summary/issues)、tool_calls(数组)。"
    "不要输出 markdown 围栏或任何解释性前后缀。"
)

#: 继续轮次的 user 消息（同样是云枢自有文本）
DELEGATE_CONTINUE_PROMPT = "继续。若已完成，请按约定输出最终 JSON 对象。"


def _is_final_turn(text: str) -> bool:
    """本轮输出是否可视为最终结果（含 ``status`` 与 JSON 对象特征）"""
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            doc = json.loads(stripped)
            return isinstance(doc, dict) and bool(doc.get("status") or doc.get("summary")
                                                 or doc.get("artifacts"))
        except (ValueError, TypeError):
            return False
    return False


class LlmChannelExecutor(ChannelExecutor):
    """内部 LLM 执行器（**等价实现**：不依赖外部 agent CLI）

    真实多轮 LLM 循环：逐轮调用 ``llm.chat``，直到某轮产出最终 JSON 对象或达到
    ``--max-turns``；随后按 §3.10 的约定**输出 JSON Lines**（单行 JSON 对象）。

    与外部 CLI 的差别只在「谁来执行」；协议侧（task_file 输入 / JSON Lines 输出 /
    超时 / 环境注入）完全一致，故两者可互换注入。
    """

    def __init__(self, llm: Any, *, system_prompt: str = DELEGATE_SYSTEM_PROMPT) -> None:
        self._llm = llm
        self._system_prompt = system_prompt

    @property
    def llm(self) -> Any:
        return self._llm

    def __call__(self, invocation: ChannelInvocation) -> RawOutput:
        start = time.time()
        if self._llm is None:
            return RawOutput(returncode=-5, error="内部 LLM 执行器未注入 llm",
                             duration_ms=0.0)
        try:
            with open(invocation.task_file, "r", encoding="utf-8") as fh:
                task_file = json.load(fh)
        except (OSError, ValueError) as e:
            return RawOutput(returncode=-6, error=f"task_file 不可读: {e}",
                             duration_ms=(time.time() - start) * 1000)
        if not isinstance(task_file, dict):
            return RawOutput(returncode=-6, error="task_file 不是 JSON 对象",
                             duration_ms=(time.time() - start) * 1000)

        max_turns = max(1, int(invocation.max_turns or DEFAULT_MAX_TURNS))
        messages: List[Dict[str, str]] = [
            {"role": "user",
             "content": ("task_file（八要素上下文包）：\n"
                         + json.dumps(task_file, ensure_ascii=False, indent=2))},
        ]
        # ── system prompt：云枢自有基座 + 本轮角色片段（受控模板）──
        # 【顺序】基座在前、角色在后：基座的"最后一轮只输出单个 JSON 对象"是协议硬要求，
        # 不该被角色叙述埋在后面；角色片段只补充"以什么身份做"。
        # 【它是受控的】override 只承载 role_templates 词表里的正文（template/full-system
        # 档），或模板+显式开启的自由文本（full-system）；template+text 档的自由文本
        # 走 ②约束，**不会**出现在这里。回归：tests/unit/test_subagent_role_wiring.py。
        override = str(getattr(invocation, "system_prompt", "") or "").strip()
        base_prompt = self._system_prompt
        if override:
            base_prompt = f"{base_prompt}\n\n{override}"
        # 把**实际运行模型**告诉子代理：否则被问及"你由什么提供推理"时会臆测厂商
        # （实测：跑在 deepseek 上却自称"由 Anthropic 的 Claude 提供"）。这属于对外
        # 输出的可信度问题，而非人格设定，故在 system prompt 末尾如实声明。
        system_prompt = base_prompt
        _model = str(getattr(self._llm, "model", "") or "").strip()
        if _model:
            _provider = str(getattr(self._llm, "provider", "") or "").strip()
            system_prompt = (
                f"{base_prompt}\n"
                f"你的实际运行模型：{_provider + '/' if _provider else ''}{_model}"
                "（以此为准；不得臆测为其它厂商或模型）。"
            )
        final_text = ""
        turns_used = 0
        for turn in range(max_turns):
            turns_used = turn + 1
            try:
                raw = self._llm.chat(messages, system_prompt=system_prompt)
            except Exception as e:  # noqa: BLE001  执行器异常 → 通道层按失败处理
                return RawOutput(returncode=-7, error=f"LLM 调用异常: {e}",
                                 duration_ms=(time.time() - start) * 1000)
            raw = str(raw or "").strip()
            if not raw:
                continue
            final_text = raw
            if _is_final_turn(raw):
                break
            # 未终结：把上一轮结果作为**上下文**续跑（system prompt 仍为云枢自有文本）
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": DELEGATE_CONTINUE_PROMPT})

        if not final_text:
            return RawOutput(returncode=-8, error="LLM 多轮执行未产出任何内容",
                             stdout="", duration_ms=(time.time() - start) * 1000)

        record = self._build_record(final_text, turns_used, max_turns)
        return RawOutput(stdout=json.dumps(record, ensure_ascii=False),
                         returncode=0,
                         duration_ms=(time.time() - start) * 1000)

    def _build_record(self, final_text: str, turns_used: int, max_turns: int) -> Dict[str, Any]:
        """把最终轮输出规整为 JSON Lines 的一行（不合格则如实标注 status）"""
        try:
            doc = json.loads(final_text)
            if isinstance(doc, dict):
                record = dict(doc)
                record.setdefault("status", "done")
                record["_turns_used"] = turns_used
                record["_max_turns"] = max_turns
                return record
        except (ValueError, TypeError):
            pass
        # 非 JSON 的收尾：归一为 raw 文本（通道层会按需走第 3 级抽取）
        return {"status": "unstructured", "summary": final_text[:4000],
                "_turns_used": turns_used, "_max_turns": max_turns}


# ════════════════════════════════════════════════════════════
#  执行器
# ════════════════════════════════════════════════════════════


class DelegationExecutor:
    """委派真执行器（真实 LLM/CLI 执行 + 并行编排 + 全部治理闸门）

    典型用法::

        executor = DelegationExecutor(agent_cli="my-agent", audit=audit, trace=facade)
        outcome = executor.execute(ctx, tools=["read_file"], credentials=[...])
        outcomes = executor.execute_many([ctx1, ctx2, ctx3], max_concurrency=2)
    """

    def __init__(
        self,
        *,
        channel: Optional[ChannelExecutor] = None,
        local_channel: Optional[ChannelExecutor] = None,
        agent_cli: str = "",
        llm: Any = None,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        queue_timeout: Optional[float] = None,
        barrier: Optional[ConcurrencyBarrier] = None,
        credential_manager: Optional[TemporaryCredentialManager] = None,
        trace: Any = None,
        audit: Any = None,
        collector: Optional[TriadCollector] = None,
        cost_ledger: Optional[CostLedger] = None,
        reviewer: Optional[CloudReviewer] = None,
        callback_dispatcher: Optional[Callable[[str, Mapping[str, Any]], Any]] = None,
        workspace: str = "",
        max_turns: Optional[int] = None,
        output_format: str = DEFAULT_OUTPUT_FORMAT,
        trusted: bool = False,
        container_root: str = "",
        memory_broker: Any = None,
        scoped_memory_domain: Any = None,
    ) -> None:
        """
        Args:
            channel: CLI 通道执行器（**测试用桩 / 内部实现注入点**）。缺省时：有
                ``agent_cli`` → 子进程执行器；有 ``local_channel`` → 本地推理执行器；
                有 ``llm`` → 内部 LLM 执行器；都没有 → 未配置执行器（调用即显式失败，
                **不静默返回假成功**）。
            local_channel: 本地推理通道（缺省 None = 不介入；也可由
                ``CP_SUBAGENT_LOCAL_ENABLED`` 显式开启后自动构造，见
                ``agent/subagent/local_inference.py``）。
            agent_cli: 外部 agent CLI（§3.10 的 ``<agent_cli>``）。
            llm: 内部 LLM 执行器所用的 LLM（鸭子类型 ``chat(messages, system_prompt=)``），
                同时用于输出解析第 3 级的 LLM 抽取。
            max_concurrency: 并发上限 N（§4.2）。
            queue_timeout: 回压排队上限（秒）；None = 纯回压不失败。
            barrier: 注入并发屏障（缺省按 max_concurrency/queue_timeout 新建）。
            credential_manager: 临时凭据管理器（缺省新建）。
            trace: ``TraceFacade``（缺省 None → 不写 Trace，但不影响其余闸门）。
            audit: ``agent.audit.facade.audit``（缺省 None → 不写审计）。
            collector: 三件套收集器。
            cost_ledger: 成本台账。
            reviewer: 云枢复评器（透传给 ``TriadCollector``）。
            callback_dispatcher: 回调投递器 ``(callback_url, payload) -> Any``。
            workspace: task_file 落盘根。
            max_turns: CLI ``--max-turns``（缺省读 ``.env`` / 常量）。
            output_format: ``--output-format``。
            trusted: 是否可信执行（**默认 False** = 第三方隔离，§5.9）。
            container_root: 隔离容器工作根（用作 HOME 指向）。
            memory_broker: brokered 记忆取用器（**注入点**；签名见
                `agent.memory.broker.assemble_brokered_context`）。缺省 None = 用默认实现
                （**只在 `ctx.metadata['memory_mode']=='brokered'` 时才会被调用**；
                默认档 none 绝不触达 ⇒ 逐字旧行为）。
            scoped_memory_domain: scoped 私人记忆域（**注入点**；见
                `agent.memory.scoped_store.ScopedMemoryDomain`）。缺省 None = 按
                ctx.metadata 现场构造（只在 `memory_mode=='scoped'` 时触达；
                none/brokered 绝不触达 ⇒ 逐字旧行为）。容器注入同一实例可跨委派
                复用配额/熔断状态。
        """
        self._agent_cli = str(agent_cli or "")
        self._llm = llm
        self._local_channel = local_channel
        self._channel: ChannelExecutor = channel or self._default_channel()
        self._barrier = barrier or ConcurrencyBarrier(
            max_concurrency=max_concurrency, queue_timeout=queue_timeout,
            name="delegation")
        self._credentials = credential_manager or TemporaryCredentialManager()
        self._trace = trace
        self._audit = audit
        self._collector = collector or TriadCollector(reviewer=reviewer)
        self._ledger = cost_ledger or CostLedger(audit=audit)
        self._callback_dispatcher = callback_dispatcher
        self._workspace = str(workspace or os.environ.get(WORKSPACE_ENV) or "")
        self._max_turns = int(max_turns) if max_turns is not None else default_max_turns()
        self._output_format = str(output_format or DEFAULT_OUTPUT_FORMAT)
        self._trusted = bool(trusted)
        self._container_root = str(container_root or "")
        self._memory_broker = memory_broker
        self._scoped_memory_domain = scoped_memory_domain
        self._lock = threading.Lock()
        self._workspace_ready = False

    def _default_channel(self) -> ChannelExecutor:
        """缺省通道：CLI > 本地（显式开启）> 内部 LLM > 未配置（显式失败）

        【优先级写死在这里，不隐式抢】外部 CLI 仍在最前（既有行为不动）；本地档只有
        在**显式开启**（``CP_SUBAGENT_LOCAL_ENABLED``）时才参与，关着时逐字旧行为。
        """
        if self._agent_cli or default_agent_cli():
            return make_executor(agent_cli=self._agent_cli)
        if self._local_channel is not None:
            return self._local_channel
        if self._llm is not None:
            return LlmChannelExecutor(self._llm)
        try:
            from agent.subagent.local_inference import (build_local_channel,
                                                        local_backend_enabled)

            if local_backend_enabled():
                local_channel = build_local_channel()
                # 本地 adapter 同时作为第 3 级 LLM 抽取的 llm（否则抽取在本地档不可用）
                self._llm = local_channel.llm
                return local_channel
        except Exception as e:  # noqa: BLE001 本地档启用失败 ⇒ 按无通道处理，不假成功
            logger.warning("[Executor] 本地推理档启用失败（按无通道处理）: %s", e)
        return make_executor(agent_cli="")

    # ── 属性 ──

    @property
    def barrier(self) -> ConcurrencyBarrier:
        return self._barrier

    @property
    def credential_manager(self) -> TemporaryCredentialManager:
        return self._credentials

    @property
    def cost_ledger(self) -> CostLedger:
        return self._ledger

    @property
    def workspace(self) -> str:
        if not self._workspace:
            with self._lock:
                if not self._workspace:
                    self._workspace = tempfile.mkdtemp(prefix="cp-subagent-")
            return self._workspace
        with self._lock:
            if not self._workspace_ready:
                os.makedirs(self._workspace, exist_ok=True)
                self._workspace_ready = True
        return self._workspace

    # ── 单次委派 ──

    def execute(self, ctx: DelegationContext, *,
                tools: Iterable[str] = (),
                authorized_capabilities: Optional[Iterable[str]] = None,
                credentials: Sequence[Mapping[str, Any]] = (),
                parent_trace: Any = None,
                input_text: str = "",
                system_prompt: str = "",
                role_tier: str = "",
                ) -> ExecutionOutcome:
        """执行一次委派（全闸门）

        Args:
            ctx: 委派上下文（八要素）。
            tools: 申请暴露给子代理的工具。
            authorized_capabilities: 授权子集（S4-01 口径）；缺省取
                ``ctx.metadata["authorized_capabilities"]``。
            credentials: 临时凭据规格（``name``/``value``/``source``/可选 ``ttl_seconds``）。
            parent_trace: 父 ``TraceContext``（编排任务）；缺省由 ``ctx`` 的 trace 字段重建。
            input_text: 委派目标文本（供复评维度使用；缺省 ``ctx.goal``）。
            system_prompt: **本轮角色片段**（受控模板正文；空 = 不加，system prompt 与
                改动前逐字相同）。它由 `agent/subagent/role_templates.py` 的词表产出，
                执行器把它拼在云枢自有基座**之后**。**自由文本只在 `full-system` 档
                才会出现在这里**（默认与 `template+text` 档绝不）。
            role_tier: 角色档位（`template` / `template+text` / `full-system`）。
                非默认档 ⇒ 本方法写一条 `subagent.role.tier` 审计，并把结果放进
                `outcome.role_audit`（`recorded` 如实反映是否真的写入）。

        Returns:
            ``ExecutionOutcome``（``ok=False`` 时 ``error_code`` 可读）。
        """
        start = time.time()
        delegation_id = ctx.delegation_id

        # ── 步骤 1：八要素校验前置（不合格 → 拒绝，零副作用）──
        try:
            ctx.require_valid()
        except DelegationRejected as e:
            self._audit_event("subagent.delegation.rejected", ctx, e.to_dict(),
                              status="rejected")
            return ExecutionOutcome(
                delegation_id=delegation_id, ok=False, tier="",
                error_code=e.code, error=str(e), duration_ms=(time.time() - start) * 1000,
                sub_reason="incomplete_contract", error_detail=e.to_dict())

        subset = self._resolve_subset(ctx, authorized_capabilities)
        # 【P3 scoped 档】把生效记忆档位与自身域传给裁剪器：默认档 scoped=False
        # ⇒ 与改动前逐字一致；scoped 时记忆类工具才不被硬禁，且矩阵要求 target_scope
        # 等于自身域（越域即拒）。
        mem_meta = getattr(ctx, "metadata", None) or {}
        mem_scope = mem_meta.get("memory_scope") if isinstance(mem_meta, Mapping) else None
        own_scope = ""
        if isinstance(mem_scope, Mapping):
            own_scope = str(mem_scope.get("workspace_id", "") or "")
        toolset = SubAgentToolset.build(
            tools, subset, actor=ctx.delegate_actor, tenant_id=ctx.tenant_id,
            scope=own_scope,
            scoped_memory=(str(mem_meta.get("memory_mode", "") or "").strip().lower()
                           == "scoped"))

        # ── 步骤 5 前置：并发闸门（§4.2 回压）──
        try:
            self._barrier.acquire()
        except BackpressureTimeout as e:
            self._audit_event("subagent.delegation.backpressure", ctx, e.__dict__,
                              status="rejected")
            return ExecutionOutcome(
                delegation_id=delegation_id, ok=False, tier="",
                error_code=e.code, error=str(e),
                duration_ms=(time.time() - start) * 1000, sub_reason="backpressure",
                toolset=toolset.as_manifest())
        try:
            # ── 步骤 1.5：brokered 记忆注入（**在 _execute_locked 之前**，逐字影响 task_file）──
            ctx, memory = self._apply_memory_broker(ctx)
            outcome = self._execute_locked(ctx, toolset=toolset, credentials=credentials,
                                           parent_trace=parent_trace,
                                           input_text=input_text or ctx.goal,
                                           system_prompt=system_prompt, role_tier=role_tier,
                                           start=start)
            if memory:
                # 【P-F】_execute_locked 在通道输出后已算好声明式记忆操作结果（仅 scoped）；
                # 换 memory 段时把它一并带过来（只含计数/错误码，不含正文）。
                memory_ops = dict((outcome.memory or {}).get("ops") or {})
                # none 档 memory 为空 ⇒ 不设（outcome.memory 保持空 dict，逐字旧行为）
                outcome.memory = dict(memory)
                if memory_ops:
                    outcome.memory["ops"] = memory_ops
                if str(memory.get("mode", "") or "") == "scoped":
                    # scoped 成功委派 → 把结构化事实写回分身私人域（fail-soft）
                    outcome.memory["write"] = self._write_back_scoped_memory(ctx, outcome)
            return outcome
        finally:
            self._barrier.release()

    def _apply_memory_broker(self, ctx: DelegationContext) -> Tuple[DelegationContext, Dict[str, Any]]:
        """按 `ctx.metadata['memory_mode']` 调 brokered 记忆取用器（**fail-soft**）

        ``none``（默认）/ 任何非 brokered 档 ⇒ 原样返回（逐字旧行为）。
        brokered 且取到非空正文 ⇒ 追加**一条**带来源/域标注的 ②约束，并把
        memory_ids / tenancy（标识，非正文）写进 metadata；取不到 ⇒ ctx 原样不动，
        但把降级原因放进返回的 memory 段（如实报告，不静默）。

        Returns:
            `(ctx_or_new_ctx, memory_segment)`；memory_segment 为空 dict = 未启用。
        """
        meta = getattr(ctx, "metadata", None) or {}
        mode = str(meta.get("memory_mode", "") or "").strip().lower()
        if mode == "scoped":
            # 【P3 scoped 档】分身自带私人记忆域：不向 ②约束注入任何正文，
            # 只做"开启留痕 + 标识回显"（配额/熔断的准入由 memory_quota 单独承担）。
            return ctx, self._scoped_memory_segment(ctx, meta)
        if mode != "brokered":
            return ctx, {}

        scope = meta.get("memory_scope") or {}
        broker = self._memory_broker
        if broker is None:
            from agent.memory.broker import assemble_brokered_context as broker  # type: ignore[assignment]

        base: Dict[str, Any] = {"mode": "brokered", "source": "", "constraint_chars": 0,
                                "degraded": "", "memory_count": 0}
        try:
            bc = broker(ctx.goal, scope=scope,
                        expected_tenant_id=ctx.tenant_id,
                        expected_workspace_id=str(meta.get("workspace_id", "") or ""),
                        expected_subject_id=ctx.subject_id)
        except Exception as e:  # noqa: BLE001 取用器异常绝不阻断委派
            logger.warning("[Executor] brokered 记忆取用异常（降级，不阻断委派）: %s", e)
            base["degraded"] = "broker_failed:%s" % type(e).__name__
            base["recorded"] = self._audit_memory_broker(ctx, base)
            return ctx, base

        try:
            from agent.memory.broker import render_constraint
        except Exception as e:  # noqa: BLE001 渲染依赖不可用 ⇒ 降级
            logger.warning("[Executor] brokered 记忆渲染不可用（降级）: %s", e)
            base["degraded"] = "render_unavailable:%s" % type(e).__name__
            base["recorded"] = self._audit_memory_broker(ctx, base)
            return ctx, base

        base["source"] = str(getattr(bc, "provider", "") or "")
        base["degraded"] = str(getattr(bc, "degraded", "") or "")
        constraint = render_constraint(bc)
        if not constraint:
            # 没取到正文：**ctx 原样不动**（不注入空行），但如实报告降级
            base["degraded"] = base["degraded"] or "empty_recall"
            base["recorded"] = self._audit_memory_broker(ctx, base)
            return ctx, base

        new_meta: Dict[str, Any] = dict(meta)
        new_meta["memory_ids"] = list(getattr(bc, "memory_ids", ()) or ())
        new_meta["memory_tenancy"] = dict(getattr(bc, "tenancy", None) or {})
        new_meta["memory_degraded"] = base["degraded"]
        new_ctx = replace(ctx, constraints=tuple(ctx.constraints) + (constraint,),
                          metadata=new_meta)
        base["constraint_chars"] = len(constraint)
        base["memory_count"] = len(new_meta["memory_ids"])
        base["recorded"] = self._audit_memory_broker(ctx, base)
        return new_ctx, base

    def _scoped_memory_segment(self, ctx: DelegationContext,
                               meta: Mapping[str, Any]) -> Dict[str, Any]:
        """scoped 档：开启留痕 + 标识回显（**不注入任何记忆正文**）

        与 brokered 的区别：scoped 的读写由分身自身的私人记忆域承担，本执行器
        **不**向 ②约束追加记忆行；这里只写一条 subagent.memory.scoped.enabled 审计，
        并把 provider / 域键 / 配额**标识**回投 outcome.memory（不含正文）。
        """
        scope = meta.get("memory_scope") or {}
        quota = meta.get("memory_quota") or {}
        segment: Dict[str, Any] = {
            "mode": "scoped",
            "provider": str(meta.get("memory_provider", "") or ""),
            "scope_keys": sorted(str(k) for k in (scope or {}) if str(k)),
            "quota": dict(quota) if isinstance(quota, Mapping) else {},
            "degraded": "",
        }
        segment["recorded"] = self._audit_event_recorded(
            "subagent.memory.scoped.enabled", ctx,
            {"mode": "scoped", "provider": segment["provider"],
             "scope_keys": segment["scope_keys"], "quota": segment["quota"]},
            status="enabled")
        return segment

    # ── scoped 写回（委派成功后把结构化事实落回分身私人域）──

    def _write_back_scoped_memory(self, ctx: DelegationContext,
                                  outcome: ExecutionOutcome) -> Dict[str, Any]:
        """scoped 委派成功后写回结构化事实（**fail-soft，绝不阻断委派**）

        只取 payload 的 summary / facts / result（**不搬运 output_text 原文**）并按
        scoped_store.MAX_SCOPED_WRITE_CHARS 截断；写回经 ScopedMemoryDomain.write，
        严格遵循 admit → 域校验 → 真实存储 → record_write → 审计。任何失败/降级都
        如实放进 outcome.memory['write']（**不含正文**），委派结果不受影响。
        """
        meta = getattr(ctx, "metadata", None) or {}
        if str(meta.get("memory_mode", "") or "").strip().lower() != "scoped":
            return {}
        if not bool(getattr(outcome, "ok", False)):
            return {"ok": False, "error_code": "", "degraded": "delegation_not_ok",
                    "attempted": False}
        facts = self._scoped_facts_from_payload(getattr(outcome, "payload", None) or {})
        if not facts:
            return {"ok": False, "error_code": "", "degraded": "no_structured_facts",
                    "attempted": False}
        try:
            from agent.memory.scoped_store import ScopedMemoryEntry
        except Exception as e:  # noqa: BLE001 依赖缺失 ⇒ 如实降级，不阻断委派
            return {"ok": False, "error_code": "E_MEMORY_DEGRADED",
                    "degraded": "import_failed:%s" % type(e).__name__,
                    "attempted": False}
        entry = ScopedMemoryEntry(
            content=facts, memory_type="fact",
            source_task_id=str(getattr(ctx, "task_id", "") or ""),
            extra={"source": "delegation"})
        try:
            domain = self._resolve_scoped_domain(meta)
        except Exception as e:  # noqa: BLE001 域构造失败（如未知 provider）⇒ 如实降级
            return {"ok": False,
                    "error_code": str(getattr(e, "code", "") or "E_MEMORY_DEGRADED"),
                    "degraded": "domain_unavailable:%s" % type(e).__name__,
                    "attempted": False}
        if domain is None:
            return {"ok": False, "error_code": "E_MEMORY_DEGRADED",
                    "degraded": "domain_unavailable", "attempted": False}
        try:
            result = domain.write(
                entry, task_id=str(getattr(ctx, "task_id", "") or ""),
                trace_id=str(getattr(outcome, "trace_id", "") or ""))
        except Exception as e:  # noqa: BLE001 写回异常绝不阻断委派
            logger.warning("[Executor] scoped 记忆写回异常（降级，不阻断委派）: %s", e)
            segment: Dict[str, Any] = {
                "ok": False, "error_code": "E_MEMORY_DEGRADED",
                "degraded": "write_failed:%s" % type(e).__name__}
        else:
            segment = (result.to_dict() if hasattr(result, "to_dict")
                       else {"ok": bool(getattr(result, "ok", False)),
                             "error_code": str(getattr(result, "error_code", "") or ""),
                             "degraded": str(getattr(result, "degraded", "") or "")})
        segment["attempted"] = True
        segment["recorded"] = self._audit_event_recorded(
            "subagent.memory.scoped.writeback", ctx,
            {"ok": bool(segment.get("ok")),
             "error_code": str(segment.get("error_code", "") or ""),
             "degraded": str(segment.get("degraded", "") or ""),
             "bytes": int(segment.get("bytes", 0) or 0)},
            status="written" if segment.get("ok") else "degraded")
        return segment

    def _run_scoped_memory_gate(self, ctx: DelegationContext, *,
                                toolset: SubAgentToolset, channel_output: ChannelOutput,
                                ok: bool) -> Dict[str, Any]:
        """【P-F】执行子代理在载荷里**声明**的 scoped 记忆读写（母体执行 + 守域）

        只在 memory_mode == scoped 时触达；none/brokered 原样返回空 dict（不新增键）。
        无论读写成败都**不**改变委派结果（fail-soft），结果只投影计数与错误码
        （outcome.memory['ops']，**不含正文**）。
        """
        meta = getattr(ctx, "metadata", None) or {}
        if str(meta.get("memory_mode", "") or "").strip().lower() != "scoped":
            return {}
        try:
            domain = self._resolve_scoped_domain(meta)
        except Exception as e:  # noqa: BLE001 域构造失败（如未知 provider）=> 如实降级
            return {"ok": False,
                    "error_code": str(getattr(e, "code", "") or "E_MEMORY_DEGRADED"),
                    "degraded": "domain_unavailable:%s" % type(e).__name__,
                    "attempted": False, "recorded": False}
        gate = ScopedMemoryGate.from_context(meta, domain=domain, toolset=toolset,
                                             audit=self._audit)
        if gate is None:
            return {"ok": False, "error_code": "E_MEMORY_DEGRADED",
                    "degraded": "gate_disabled", "attempted": False, "recorded": False}
        reads = tuple(getattr(channel_output, "memory_reads", ()) or ())
        writes = tuple(getattr(channel_output, "memory_writes", ()) or ())
        declared = {"reads": len(reads), "writes": len(writes),
                    "degraded": str(getattr(channel_output,
                                            "memory_declaration_degraded", "") or "")}
        if not reads and not writes:
            return {"ok": False, "error_code": "", "degraded": "no_declared_ops",
                    "attempted": False, "recorded": False, "declared": declared}
        if not ok:
            return {"ok": False, "error_code": "", "degraded": "delegation_not_ok",
                    "attempted": False, "recorded": False, "declared": declared}
        result: Dict[str, Any] = {
            "ok": True, "error_code": "", "degraded": "", "attempted": True,
            "declared": declared, "reads": [], "writes": {},
        }
        try:
            for query in reads:
                result["reads"].append(gate.read(query))
            if writes:
                result["writes"] = gate.write_declared(writes)
            read_ok = all(bool(item.get("ok")) for item in result["reads"])
            write_ok = (not writes) or bool(result["writes"].get("ok"))
            result["ok"] = bool(read_ok and write_ok)
            if result["reads"] and not read_ok:
                result["degraded"] = "read_degraded"
            elif writes and not write_ok:
                result["degraded"] = "write_degraded"
            result["recorded"] = bool(result["writes"].get("recorded")) or any(
                bool(item.get("recorded")) for item in result["reads"])
            return result
        except Exception as e:  # noqa: BLE001 守门器异常绝不阻断委派
            logger.warning("[Executor] scoped 声明式记忆操作异常（降级）: %s", e)
            result.update(ok=False, error_code="E_MEMORY_DEGRADED",
                          degraded="gate_failed:%s" % type(e).__name__)
            return result

    def _resolve_scoped_domain(self, meta: Mapping[str, Any]) -> Any:
        """取 scoped 记忆域：注入实例优先，否则按 metadata 现场构造（含审计 sink）"""
        if self._scoped_memory_domain is not None:
            return self._scoped_memory_domain
        from agent.memory.scoped_store import scoped_domain_from_scope

        return scoped_domain_from_scope(
            meta.get("memory_provider", ""), meta.get("memory_scope") or {},
            quota=meta.get("memory_quota") or {}, audit=self._audit)

    @staticmethod
    def _scoped_facts_from_payload(payload: Mapping[str, Any]) -> str:
        """从上游结构化载荷提取可写回的事实摘要（**不含 output_text 原文**）

        来源按键固定：summary（字符串）+ facts（字符串数组 / 字符串）+ result。
        长度受限（scoped_store.MAX_SCOPED_WRITE_CHARS）；空则返回空串（不写回）。
        """
        if not isinstance(payload, Mapping):
            return ""
        try:
            from agent.memory.scoped_store import MAX_SCOPED_WRITE_CHARS as limit
        except Exception:  # noqa: BLE001
            limit = 800
        parts: List[str] = []
        summary = payload.get("summary")
        if isinstance(summary, str) and summary.strip():
            parts.append(summary.strip())
        facts = payload.get("facts")
        if isinstance(facts, (list, tuple)):
            for item in facts:
                text = str(item or "").strip()
                if text:
                    parts.append(text)
        elif isinstance(facts, str) and facts.strip():
            parts.append(facts.strip())
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            parts.append(result.strip())
        return "；".join(parts)[:int(limit)]

    def _audit_memory_broker(self, ctx: DelegationContext,
                             memory: Mapping[str, Any]) -> bool:
        """写一条 `subagent.memory.brokered` 审计（返回是否真的写入）

        与角色档位同款：显式启用记忆注入是**安全姿态变更**，必须在实跑这一刻留痕。
        默认档 none 绝不触达本方法（噪声不是留痕）。**payload 不含记忆正文**。
        """
        payload = {"mode": str(memory.get("mode", "") or ""),
                   "source": str(memory.get("source", "") or ""),
                   "constraint_chars": int(memory.get("constraint_chars", 0) or 0),
                   "memory_count": int(memory.get("memory_count", 0) or 0),
                   "degraded": str(memory.get("degraded", "") or "")}
        return self._audit_event_recorded("subagent.memory.brokered", ctx, payload,
                                          status="enabled")

    def _execute_locked(self, ctx: DelegationContext, *, toolset: SubAgentToolset,
                        credentials: Sequence[Mapping[str, Any]], parent_trace: Any,
                        input_text: str, system_prompt: str = "", role_tier: str = "",
                        start: float) -> ExecutionOutcome:
        """持并发槽位的执行主体（授权后续全部步骤）"""
        delegation_id = ctx.delegation_id
        base = ExecutionOutcome(delegation_id=delegation_id, ok=False,
                                toolset=toolset.as_manifest(),
                                isolation=isolation_policy_report(
                                    container_root=self._container_root,
                                    trusted=self._trusted))
        # ── 角色档位留痕（非默认档 = 一次显式的权限抬升，必须在**实跑**这一刻留下一笔）──
        # 放在最前面：只要真的带着该档位进入了执行主体，就留痕（后续 task_file/凭据
        # 失败也不影响"这一次确实用了这个档"这个事实）。
        base.role_audit = self._audit_role_tier(ctx, role_tier, system_prompt)

        # ── 步骤 2：物化 task_file（§3.10）──
        try:
            task_file_path = self._write_task_file(ctx)
        except (OSError, DelegationRejected) as e:
            base.error_code = getattr(e, "code", E_DELEGATION_FAILED)
            base.error = f"task_file 物化失败: {e}"
            base.sub_reason = "task_file"
            base.duration_ms = (time.time() - start) * 1000
            return base
        base.task_file = task_file_path

        # ── 步骤 4：子 Trace（S2-01 TraceContext.child()）──
        sub_ctx, token = self._enter_child_trace(ctx, parent_trace)
        try:
            # ── 步骤 5/6：临时凭据（finally 销毁）+ 隔离环境 + 通道调用 ──
            try:
                creds, channel_output = self._run_with_credentials(
                    ctx, task_file_path, credentials, system_prompt=system_prompt)
            except CredentialError as e:
                base.error_code = getattr(e, "code", "E_CREDENTIAL")
                base.error = str(e)
                base.sub_reason = "credentials"
                base.duration_ms = (time.time() - start) * 1000
                self._record_trace(ctx, sub_ctx, base, status="error")
                return base

            base.credentials = tuple(c.to_dict() for c in creds)
            base.credentials_destroyed = all(
                (self._credentials.get(c.credential_id) is not None
                 and self._credentials.get(c.credential_id).destroyed)  # type: ignore[union-attr]
                for c in creds) if creds else True
            base.tier = channel_output.tier
            base.attempts = channel_output.attempts
            base.payload = dict(channel_output.payload)
            base.artifacts = tuple(channel_output.artifacts)
            base.output_text = channel_output.text.for_sandbox_slot()
            base.sub_reason = channel_output.sub_reason
            base.invocation = (channel_output.invocation.to_dict()
                               if channel_output.invocation else None)

            # ── 步骤 7：工具裁剪闸门（含上游声称的间接调用）──
            tool_calls, violations = self._gate_tool_calls(toolset,
                                                           channel_output.payload)
            base.tool_calls = tuple(tool_calls)
            base.tool_violations = tuple(violations)

            if not channel_output.ok:
                base.ok = False
                base.error_code = channel_output.error_code or E_UPSTREAM_FORMAT
                base.error = channel_output.error
            elif violations:
                # 越界调用 → 本次委派整体失败（输出不可信）
                base.ok = False
                base.error_code = E_TOOL_NOT_AUTHORIZED
                first = violations[0]
                base.error = (f"子代理调用了裁剪集外的工具：{first.get('tool')}"
                              f"（{first.get('reason')}）")
                base.sub_reason = "tool_trimmed"
            else:
                base.ok = True

            # ── 步骤 7.5：声明式记忆操作（仅 scoped；母体执行 + 守域，fail-soft）──
            # 子代理只能在载荷里"声明"读写意图，真正执行由母体守门器完成；默认档
            # none/brokered 与未开启 scoped 的情况绝不在 outcome.memory 里出现该键。
            ops = self._run_scoped_memory_gate(
                ctx, toolset=toolset, channel_output=channel_output, ok=base.ok)
            if ops:
                base.memory["ops"] = ops

            # ── 步骤 8：Trace ──
            self._record_trace(ctx, sub_ctx, base,
                               status="success" if base.ok else "error")

            # ── 步骤 9：回收三件套 → 成本 → 回调 ──
            base.triad = self._collector.collect_from_outcome(
                base, upstream_payload=base.payload, input_text=input_text,
                # R4：把委派契约（八要素⑤产物格式）交给收集器 ⇒ 信号①产物结构
                # 在真实链路里自动取到**可机验**的格式声明（无需调用方额外接线）
                context=ctx)
            base.cost = self._ledger.account(
                base.triad,
                input_tokens=int(self._tokens_from_payload(base.payload, "input")),
                output_tokens=int(self._tokens_from_payload(base.payload, "output")),
                cost_usd=float(base.payload.get("cost_usd") or 0.0)
                if isinstance(base.payload.get("cost_usd"), (int, float)) else 0.0)
            base.callback = self._dispatch_callback(ctx, base)
            base.duration_ms = (time.time() - start) * 1000
            return base
        finally:
            if token is not None:
                self._exit_child_trace(token)

    # ── 子 Trace（S2-01 child()）──

    def _enter_child_trace(self, ctx: DelegationContext, parent_trace: Any,
                           ) -> Tuple[Any, Any]:
        """构造并进入委派子 Trace 上下文；返回 ``(sub_ctx, token)``

        **S2-01 遗留 #5 的消费点**：子 Trace **必须**经 ``TraceContext.child()``
        产生（不复用、不自建上下文传递）。父上下文来源优先级：
          1. 显式传入的 ``parent_trace``（编排任务的 ``TraceContext``）；
          2. 由契约字段重建的编排上下文（``ctx.trace_id``/``task_id``/tenant/subject）。
        两者都走同一条 ``child()`` 路径，故子 Trace **恒有父链**。
        """
        try:
            from agent.observability.trace_v2 import TraceContext  # 惰性：无 Trace 依赖也可导入
        except Exception as e:  # noqa: BLE001  Trace 不可用不得阻断委派
            logger.warning("[Executor] trace_v2 不可用（%s）——本次委派不写 Trace", e)
            return None, None
        parent = parent_trace
        if parent is None:
            parent = TraceContext(
                trace_id=ctx.trace_id or "",
                task_id=ctx.task_id or ctx.delegation_id,
                tenant_id=ctx.tenant_id,
                workspace_id=str(ctx.metadata.get("workspace_id") or ""),
                subject_id=ctx.subject_id,
                policy_version=ctx.policy_version,
            )
        sub_ctx = parent.child()
        token = sub_ctx.enter()
        return sub_ctx, token

    @staticmethod
    def _exit_child_trace(token: Any) -> None:
        try:
            from agent.observability.trace_v2 import TraceContext
            TraceContext.exit(token)
        except Exception:  # noqa: BLE001  上下文恢复失败不得影响委派结果
            pass

    def _record_trace(self, ctx: DelegationContext, sub_ctx: Any,
                      outcome: ExecutionOutcome, *, status: str) -> None:
        """写委派子 Trace（``actor=sub_agent``；父链由 ``child()`` 串联）

        ``facade.record`` 未显式传 ``trace_id`` 时，落库行的
        ``parent_trace_id`` 自动指向 ``task_ctx.trace_id`` —— 即 ``child()`` 产出的
        子 Trace id。这正是我们要的可断言事实。
        """
        if self._trace is None:
            return
        try:
            from agent.observability.trace_v2 import STATUS_ERROR, STATUS_SUCCESS
        except Exception:  # noqa: BLE001
            STATUS_SUCCESS, STATUS_ERROR = "success", "error"  # type: ignore[assignment]
        try:
            trace = self._trace.record(
                CAPABILITY_DELEGATE,
                args={"delegation_id": ctx.delegation_id, "goal": ctx.goal[:200],
                      "budget_tokens": ctx.budget_tokens,
                      "tools": list(outcome.toolset.get("tools") or [])},
                output={"ok": bool(outcome.ok), "tier": outcome.tier,
                        "error_code": outcome.error_code,
                        "artifact_count": len(outcome.artifacts),
                        "tool_violation_count": len(outcome.tool_violations),
                        "credentials_destroyed": outcome.credentials_destroyed},
                actor=ACTOR_SUB_AGENT,
                status=STATUS_SUCCESS if status == "success" else STATUS_ERROR,
                error_code=outcome.error_code,
                duration_ms=outcome.duration_ms,
                task_ctx=sub_ctx,
            )
            if trace is not None:
                row = trace.to_dict() if hasattr(trace, "to_dict") else None
                if isinstance(row, Mapping):
                    outcome.trace = {str(k): row[k] for k in row}
                    outcome.trace_id = str(getattr(trace, "trace_id", "") or "")
        except Exception as e:  # noqa: BLE001  Trace 写入失败不得阻断委派主路径
            logger.warning("[Executor] 委派 Trace 写入失败: %s", e)

    # ── 凭据 + 隔离 + 通道 ──

    def _run_with_credentials(self, ctx: DelegationContext, task_file_path: str,
                              credentials: Sequence[Mapping[str, Any]],
                              *, system_prompt: str = "",
                              ) -> Tuple[List[TemporaryCredential], ChannelOutput]:
        """在凭据作用域内执行通道调用（``finally`` 无条件销毁凭据）

        任务时长取契约⑦（``timeout_seconds``）——§5.9 的「TTL ≤ 任务时长」上界来源。
        """
        with credential_scope(self._credentials, list(credentials),
                              task_timeout_seconds=ctx.timeout_seconds) as creds:
            env = self._build_env(creds)
            invocation = ChannelInvocation(
                argv=build_cli_argv(self._agent_cli or default_agent_cli() or "internal-llm",
                                    task_file_path, max_turns=self._max_turns,
                                    output_format=self._output_format),
                task_file=task_file_path,
                max_turns=self._max_turns,
                output_format=self._output_format,
                timeout_seconds=float(ctx.timeout_seconds),
                env=env,
                cwd=self._container_root,
                env_mode=ENV_REPLACE,   # 已自建完整环境（隔离 + 凭据），不再叠宿主环境
                # 本轮角色片段（受控模板）；LlmChannelExecutor 拼在云枢自有基座之后，
                # 外部 CLI 通道忽略它（system prompt 由对端进程决定）
                system_prompt=str(system_prompt or ""),
            )
            output = resolve_channel_output(
                lambda: self._channel(invocation), llm=self._llm, invocation=invocation)
        # 退出 with 即已销毁；此处断言销毁证据（隐藏失败会长期留存凭据）
        for cred in creds:
            record = self._credentials.get(cred.credential_id)
            if record is not None and not record.destroyed:
                logger.error("[Executor] 凭据 %s 未销毁（§5.9 违规）", cred.credential_id)
        return creds, output

    def _build_env(self, creds: Sequence[TemporaryCredential]) -> Dict[str, str]:
        """构造子进程环境：宿主环境 → 隔离覆盖 → 临时凭据（§5.9）"""
        env = apply_isolation_env(dict(os.environ), container_root=self._container_root,
                                  trusted=self._trusted)
        for cred in creds:
            env[cred.env_var] = cred.value
        return env

    # ── 工具闸门 ──

    @staticmethod
    def _gate_tool_calls(toolset: SubAgentToolset,
                         payload: Mapping[str, Any],
                         ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """对上游**声称**的每一次工具调用过闸（含别名/间接路径）

        为什么必须做：子代理是独立进程，它「说自己调用了什么」是**外来声明**。
        不做闸门就等于把裁剪集当成君子协定（§5.7 机制 3 要求的是能力最小暴露，
        不是「请勿越界」）。
        """
        raw_calls: Any = None
        for key in _TOOL_CALL_KEYS:
            if isinstance(payload, Mapping) and payload.get(key):
                raw_calls = payload.get(key)
                break
        if raw_calls is None:
            return [], []
        if isinstance(raw_calls, Mapping):
            raw_calls = [raw_calls]
        if not isinstance(raw_calls, (list, tuple)):
            raw_calls = [raw_calls]

        gated: List[Dict[str, Any]] = []
        violations: List[Dict[str, Any]] = []
        for item in raw_calls:
            spec: Any = item
            if isinstance(item, str):
                spec = {"name": item}
            decision = toolset.check_spec(spec)
            entry = {"call": spec if isinstance(spec, Mapping) else {"name": str(spec)},
                     **decision.to_dict()}
            gated.append(entry)
            if not decision.allowed:
                violations.append(entry)
        return gated, violations

    # ── 辅助 ──

    @staticmethod
    def _resolve_subset(ctx: DelegationContext,
                        explicit: Optional[Iterable[str]]) -> FrozenSet[str]:
        """授权子集（显式参数 > 契约 metadata；两者皆无 → 空集 = 无工具可用，fail-closed）"""
        if explicit is not None:
            return frozenset(str(c).strip() for c in explicit if str(c).strip())
        meta = ctx.metadata.get("authorized_capabilities") if ctx.metadata else None
        if isinstance(meta, (list, tuple, set, frozenset)):
            return frozenset(str(c).strip() for c in meta if str(c).strip())
        return frozenset()

    def _write_task_file(self, ctx: DelegationContext) -> str:
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_"
                       for ch in str(ctx.delegation_id))[:64] or "delegation"
        path = os.path.join(self.workspace, f"task_file_{safe}.json")
        return write_task_file(ctx, path)

    @staticmethod
    def _tokens_from_payload(payload: Mapping[str, Any], kind: str) -> int:
        if not isinstance(payload, Mapping):
            return 0
        for key in (f"{kind}_tokens", f"{kind}Tokens"):
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                return max(0, value)
        usage = payload.get("usage")
        if isinstance(usage, Mapping):
            value = usage.get(f"{kind}_tokens")
            if isinstance(value, int) and not isinstance(value, bool):
                return max(0, value)
        return 0

    def _dispatch_callback(self, ctx: DelegationContext,
                           outcome: ExecutionOutcome) -> Dict[str, Any]:
        """投递回调（契约⑧）：记录结果 / 触发后续

        best-effort：回调失败**不**改变委派结果（通用硬约束「新增机制失败不得阻断
        主流程」），但结果如实记录在 ``outcome.callback``。
        """
        payload = {
            "delegation_id": ctx.delegation_id,
            "task_id": ctx.task_id or ctx.delegation_id,
            "ok": bool(outcome.ok),
            "status": "success" if outcome.ok else "error",
            "error_code": outcome.error_code,
            "tier": outcome.tier,
            "trace_id": outcome.trace_id,
            "artifact_count": len(outcome.artifacts),
        }
        result: Dict[str, Any] = {"callback_url": ctx.callback_url,
                                  "delivered": False, "error": ""}
        if self._callback_dispatcher is not None:
            try:
                returned = self._callback_dispatcher(ctx.callback_url, payload)
                # 投递器可回 Mapping（见 callback_channel.HttpCallbackDispatcher）：如实
                # 并入 delivered/status/error/mode，使「被策略拒绝 / 传输失败」不被误报成
                # delivered=True。返回 None 的既有投递器语义不变（视为已投递）。
                if isinstance(returned, Mapping):
                    result["delivered"] = bool(returned.get("delivered", False))
                    if returned.get("status") is not None:
                        result["status"] = returned.get("status")
                    result["error"] = str(returned.get("error") or "")
                    if returned.get("mode"):
                        result["mode"] = str(returned["mode"])
                else:
                    result["delivered"] = True
            except Exception as e:  # noqa: BLE001  回调失败不阻断
                result["error"] = f"{type(e).__name__}: {e}"
                logger.warning("[Executor] 回调投递失败 %s: %s", ctx.callback_url, e)
        else:
            # 缺省「记录结果/触发后续」：把地址与结果摘要写审计
            result["delivered"] = True
            result["mode"] = "audit_record"
        self._audit_event("subagent.delegation.callback", ctx,
                          {"callback": result, "result": payload},
                          status=str(payload["status"]))
        return result

    def _audit_event_recorded(self, action: str, ctx: DelegationContext,
                              payload: Mapping[str, Any], *, status: str = "") -> bool:
        """写审计并**返回是否真的写入**（best-effort，但结果可查）

        与 :meth:`_audit_event` 的唯一差别是返回值：需要"留痕证据"的调用方
        （角色档位）据此把"记上了没有"如实投影出去，而不是把"尝试过"说成"留痕了"。
        """
        if self._audit is None:
            return False
        try:
            self._audit.record(
                action, actor=ctx.delegate_actor or ACTOR_SUB_AGENT,
                subject=f"delegation:{ctx.delegation_id}",
                payload=dict(payload), status=status)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[Executor] 审计写入失败 %s: %s", action, e)
            return False

    def _audit_event(self, action: str, ctx: DelegationContext,
                     payload: Mapping[str, Any], *, status: str = "") -> None:
        """审计（best-effort；审计失败不得阻断委派主路径）"""
        self._audit_event_recorded(action, ctx, payload, status=status)

    def _audit_role_tier(self, ctx: DelegationContext, role_tier: str,
                         system_prompt: str) -> Dict[str, Any]:
        """角色档位留痕（非默认档 ⇒ 写一条 `subagent.role.tier`）

        【为什么在**执行器**留痕，而不是只在创建端点】创建时记一次，答的是"配置写成什么"；
        这里记的是"这一次真的带着该档位跑了"。两者不同，而权限抬升要的是后者。
        【默认档不记】每个分身都记一条会把审计淹掉 —— 噪声不是留痕。
        【只有判定不可用时按需审计】`role_templates` 不可导入属异常形态，此时宁可多记。

        Returns:
            `{}`（默认档/未指定）或 `{"action", "tier", "system_prompt_chars",
            "explicit", "recorded"}`。**不含 system prompt 正文**。
        """
        tier = str(role_tier or "").strip()
        if not tier:
            return {}
        try:
            from agent.subagent.role_templates import requires_audit

            elevated = bool(requires_audit(tier))
        except Exception as e:  # noqa: BLE001 档位词表不可用 ⇒ 按"需审计"处理（不静默跳过）
            logger.warning("[Executor] 角色档位判定不可用（按需审计处理）: %s", e)
            elevated = True
        if not elevated:
            return {}
        payload = {"tier": tier, "system_prompt_chars": len(str(system_prompt or "")),
                   "explicit": True}
        recorded = self._audit_event_recorded("subagent.role.tier", ctx, payload,
                                              status="enabled")
        return {"action": "subagent.role.tier", **payload, "recorded": bool(recorded)}

    # ── 并行编排 ──

    def execute_many(self, delegations: Sequence[DelegationContext], *,
                     max_concurrency: Optional[int] = None,
                     tools: Iterable[str] = (),
                     authorized_capabilities: Optional[Iterable[str]] = None,
                     tools_for: Optional[Callable[[DelegationContext],
                                                 Iterable[str]]] = None,
                     authorized_for: Optional[Callable[[DelegationContext],
                                                       Iterable[str]]] = None,
                     credentials_for: Optional[Callable[[DelegationContext],
                                                        Sequence[Mapping[str, Any]]]] = None,
                     parent_trace: Any = None,
                     ) -> List[ExecutionOutcome]:
        """并行执行多个委派（ThreadPoolExecutor + 并发屏障）

        结果**按输入顺序**返回（``as_completed`` 的完成顺序不是调用方要的语义）。
        单个委派的异常不影响其余（就地收敛为 ``ok=False`` 的结果）。

        **逐条委派参数（工厂）**：``tools`` / ``authorized_capabilities`` 是**整批共用**的
        单值；当一批任务各自持有不同的工具集（例如"每个子代理按自己的主线档案装配"）时，
        改传 ``tools_for`` / ``authorized_for``——它们是**按 ctx 现算**的工厂，语义与既有的
        ``credentials_for`` 一致（缺省 None 时退回整批单值，故既有调用点行为不变）。

        **跨线程上下文**：``TraceContext`` 是 ContextVar 语义，**不跨线程自动继承**
        （上游已知坑 #4）。故父 Trace 以参数显式传入，子上下文在 **worker 内部**由
        ``child()`` 现场生成——不做任何隐式继承假设。
        """
        items = list(delegations)
        if not items:
            return []
        workers = int(max_concurrency) if max_concurrency is not None else self._barrier.max_concurrency
        workers = max(1, min(int(workers), len(items)))
        results: List[Optional[ExecutionOutcome]] = [None] * len(items)

        def _run(index: int, ctx: DelegationContext) -> Tuple[int, ExecutionOutcome]:
            creds = credentials_for(ctx) if credentials_for is not None else ()
            try:
                # 工厂在 try 内求值：工厂自身抛异常同样只影响本条委派（与执行异常同口径）
                task_tools = tools_for(ctx) if tools_for is not None else tools
                task_authorized = (authorized_for(ctx) if authorized_for is not None
                                   else authorized_capabilities)
                return index, self.execute(
                    ctx, tools=task_tools, authorized_capabilities=task_authorized,
                    credentials=creds, parent_trace=parent_trace)
            except Exception as e:  # noqa: BLE001  线程级兜底：单点异常不拖垮批次
                logger.error("[Executor] 委派 %s 执行异常: %s", ctx.delegation_id, e)
                return index, ExecutionOutcome(
                    delegation_id=ctx.delegation_id, ok=False,
                    error_code=E_DELEGATION_FAILED,
                    error=f"{type(e).__name__}: {e}", sub_reason="executor_exception")

        if workers == 1:
            for idx, ctx in enumerate(items):
                i, outcome = _run(idx, ctx)
                results[i] = outcome
        else:
            with ThreadPoolExecutor(max_workers=workers,
                                    thread_name_prefix="cp-delegate") as pool:
                futures = [pool.submit(_run, idx, ctx) for idx, ctx in enumerate(items)]
                for fut in as_completed(futures):
                    try:
                        i, outcome = fut.result()
                    except Exception as e:  # noqa: BLE001  线程级兜底
                        logger.error("[Executor] 委派任务异常: %s", e)
                        continue
                    results[i] = outcome
        return [r for r in results if r is not None]

    # ── 统计 ──

    def stats(self) -> Dict[str, Any]:
        """执行器统计（并发/回压/凭据/成本）——验收证据来源"""
        return {
            "barrier": self._barrier.stats().to_dict(),
            "credentials": self._credentials.snapshot(),
            "cost": self._ledger.totals(),
            "isolation": isolation_policy_report(container_root=self._container_root,
                                                 trusted=self._trusted),
        }


# ════════════════════════════════════════════════════════════
#  便捷构造
# ════════════════════════════════════════════════════════════


def build_executor(*, llm: Any = None, agent_cli: str = "",
                   channel: Optional[ChannelExecutor] = None,
                   local_channel: Optional[ChannelExecutor] = None,
                   callback_dispatcher: Any = None,
                   **kwargs: Any) -> DelegationExecutor:
    """按可用条件构造执行器（LLM 优先于 CLI 的**内部等价实现**路径）

    任务书「上游已知坑 #1」：环境可能无外部 agent CLI/凭证，故真实调用是可选的；
    无 CLI 时用内部 LLM 执行器（协议完全一致），无 LLM 时如实标注为未配置。

    【S5 回调反向通道】未显式注入 callback_dispatcher 时走工厂：未开启 ⇒ None
    （生产默认仍只写审计，行为逐字不变）；开启且配置齐全 ⇒ HttpCallbackDispatcher。
    """
    if callback_dispatcher is None:
        from agent.subagent.callback_channel import default_callback_dispatcher

        callback_dispatcher = default_callback_dispatcher()
    kwargs["callback_dispatcher"] = callback_dispatcher
    if channel is not None:
        return DelegationExecutor(channel=channel, llm=llm,
                                  local_channel=local_channel, **kwargs)
    if agent_cli or default_agent_cli():
        return DelegationExecutor(agent_cli=agent_cli, llm=llm,
                                  local_channel=local_channel, **kwargs)
    return DelegationExecutor(llm=llm, local_channel=local_channel, **kwargs)


__all__ = [
    "CAPABILITY_DELEGATE", "CAPABILITY_DELEGATE_TOOL",
    "E_DELEGATION_FAILED", "DEFAULT_MAX_CONCURRENCY",
    "DELEGATE_SYSTEM_PROMPT", "DELEGATE_CONTINUE_PROMPT",
    "ExecutionOutcome", "LlmChannelExecutor", "DelegationExecutor", "build_executor",
]
