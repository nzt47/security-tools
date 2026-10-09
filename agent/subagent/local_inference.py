"""分身本地推理执行档（P5 / portability：断网可跑）

【解决什么（不这样会怎样）】
    分身此前只有两档执行后端：`inproc`（部署 LLM，同进程）与 `subprocess`（外部
    agent CLI）。两者都**依赖外部可达的推理服务**：断网 / 换机时，"可带走 bundle"
    只是一份配置，跑不起来。本模块把 `core/local_llm.py`（本地 Ollama 推理，此前
    **全仓零调用方**）接成第三档执行后端 `local`，与既有两档**平级**。

【为什么是"执行后端档"而不是"模型名"】
    `agent/subagent/llm_factory.py::resolve_subagent_llm` 回答的是"同一 provider 下
    换哪个模型名"，唯一构造口 `LLMService.with_model()` 复用**远端 SDK 客户端**
    （provider / api_key / base_url 恒取部署配置）。把一个本地模型名传进去，请求仍会
    打到远端 —— 那是静默说谎，不是断网可跑。执行后端是"谁来执行"，与模型名是两个面。

【协议零改动】
    本档实现的是既有的 `ChannelExecutor.__call__(invocation) -> RawOutput`
    （见 `agent/subagent/channel.py`），并**复用** `LlmChannelExecutor` 的多轮文本循环
    与 JSONL 收尾：task_file 输入、JSON Lines 输出、三级降级、taint、工具裁剪闸门、
    Trace、成本记账全部不动。本地与远端只差"谁来回答"。

【如实边界（不能假装）】
    · `core/local_llm.py` 的 `vllm` 分支**声明了但未实现**（非 ollama 的 generate 恒
      返回 None）⇒ 本模块对未知引擎 fail-closed（`SUPPORTED_LOCAL_ENGINES` 只有
      ollama），**不假装支持 vLLM / llama.cpp**。
    · `LocalLLM.generate` 只接受**一个字符串 prompt**，没有 system 角色、没有多轮；
      `LocalLLMAdapter.chat` 会把 messages + system **压平成一个 prompt** —— 这是
      语义损失，如实写在 adapter docstring 里，不声称角色保真。
    · 服务是否在跑是**环境事实**：本档接线 ≠ "本机 Ollama 已启动"。能力面如实标注。
    · 默认关闭（`CP_SUBAGENT_LOCAL_ENABLED`）：关着时通道选择与改动前逐字相同。

【依赖纪律】
    顶层仅标准库 + `agent.subagent.channel`；`core.local_llm`（会拉 aiohttp）与
    `agent.subagent.executor`（较重）都是**函数内惰性导入**，不给 channel 增加导入负担。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any, Dict, Optional

from agent.subagent.channel import ChannelExecutor, ChannelInvocation, RawOutput

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_LOCAL_ENGINES",
    "ENV_LOCAL_ENABLED",
    "ENV_LOCAL_ENGINE",
    "ENV_LOCAL_MODEL",
    "ENV_LOCAL_API_BASE",
    "LocalInferenceError",
    "local_backend_enabled",
    "local_status",
    "LocalLLMAdapter",
    "LocalInferenceChannelExecutor",
    "build_local_channel",
]

#: 有真实实现的本地引擎（只列实现了的；core/local_llm 的 vllm 分支未实现，不列）
SUPPORTED_LOCAL_ENGINES = ("ollama",)

#: 环境变量（全部登记进 agent/settings/registry.py，否则 scan_settings --check 红）
ENV_LOCAL_ENABLED = "CP_SUBAGENT_LOCAL_ENABLED"
ENV_LOCAL_ENGINE = "CP_SUBAGENT_LOCAL_ENGINE"
ENV_LOCAL_MODEL = "CP_SUBAGENT_LOCAL_MODEL"
ENV_LOCAL_API_BASE = "CP_SUBAGENT_LOCAL_API_BASE"

#: 真值写法（与仓库其它 _b 类开关同款）
_TRUTHY = ("1", "true", "yes", "on")


class LocalInferenceError(Exception):
    """本地推理档构造失败（fail-closed，不回落远端）"""

    code = "E_LOCAL_INFERENCE"

    def __init__(self, message: str, *, code: str = "E_LOCAL_INFERENCE") -> None:
        super().__init__(message)
        self.code = str(code or "E_LOCAL_INFERENCE")


def local_backend_enabled(environ: Optional[Mapping[str, str]] = None) -> bool:
    """本地推理档总开关（默认关；关着时一切行为与改动前逐字相同）"""
    if environ is None:
        raw = os.environ.get(ENV_LOCAL_ENABLED, "")
    else:
        raw = environ.get(ENV_LOCAL_ENABLED, "")
    return str(raw or "").strip().lower() in _TRUTHY


def local_status(environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """本地档配置投影（**不含端点值**，供 /api/subagent/list 的 channel 段回显）

    只回答"开没开、用哪个引擎、模型名是什么"。endpoint 属脱敏项，不回显。
    """
    env = environ if environ is not None else os.environ
    engine = str(env.get(ENV_LOCAL_ENGINE, "") or "").strip() or "ollama"
    model = str(env.get(ENV_LOCAL_MODEL, "") or "").strip()
    return {
        "enabled": local_backend_enabled(env),
        "engine": engine,
        "model": model,
        "engine_supported": engine in SUPPORTED_LOCAL_ENGINES,
    }


def _flatten_messages(messages: Any, system_prompt: str) -> str:
    """把 messages + system 压平成单个 prompt（LocalLLM 只接受字符串）"""
    parts = []
    sys_text = str(system_prompt or "").strip()
    if sys_text:
        parts.append("[system]\n" + sys_text)
    for msg in messages or []:
        if not isinstance(msg, Mapping):
            continue
        role = str(msg.get("role") or "user")
        content = str(msg.get("content") or "")
        parts.append("[" + role + "]\n" + content)
    return "\n\n".join(parts)


class LocalLLMAdapter:
    """`core.local_llm.LocalLLM` → `LlmChannelExecutor` 需要的 `chat()` 鸭子接口

    【语义损失（如实）】LocalLLM 只有"单字符串 prompt → 单字符串回答"，没有 system
    角色与多轮消息。`chat()` 把 messages + system 压平：**角色/多轮保真度降低**，
    这是本地档的已知差异，不是隐藏行为。

    【凭据】本地档不读 .env、不碰 api_key；`provider` 恒回 `"local"`，使执行器写给
    子代理的"你的实际运行模型"如实标为 local/<model>，不冒充部署 provider。
    """

    def __init__(self, local: Any, *, model: str = "") -> None:
        self._local = local
        self._model = str(model or getattr(local, "_model", "") or "")

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "local"

    @property
    def local(self) -> Any:
        return self._local

    def chat(self, messages: Any, system_prompt: str = "") -> str:
        """同步 `chat`：压平 prompt → 跑 async `LocalLLM.generate`（经唯一同步包装）

        当前线程已有运行中的事件循环时，`run_sync` 显式抛错；由 `LlmChannelExecutor`
        的调用点按通道失败处理（不静默换循环、不返回假成功）。
        """
        from agent.memory.broker import run_sync

        prompt = _flatten_messages(messages, system_prompt)
        text = run_sync(lambda: self._local.generate(prompt), need="本地推理")
        return str(text or "")

    def chat_stream(self, messages: Any, system_prompt: str = "") -> Any:
        """单块流式视图（LlmChannelExecutor 不消费它，仅为鸭子接口完整）"""
        yield self.chat(messages, system_prompt=system_prompt)


class LocalInferenceChannelExecutor(ChannelExecutor):
    """第三档执行器：把 `LlmChannelExecutor` 的多轮文本循环跑在本地 adapter 上

    不重写协议：内部委托 `LlmChannelExecutor`，因此 task_file / JSON Lines /
    三级降级 / max_turns 行为与内部 LLM 档逐字一致。
    """

    def __init__(self, adapter: LocalLLMAdapter, *, system_prompt: str = "") -> None:
        from agent.subagent.executor import DELEGATE_SYSTEM_PROMPT, LlmChannelExecutor

        self._adapter = adapter
        self._inner = LlmChannelExecutor(
            adapter, system_prompt=(system_prompt or DELEGATE_SYSTEM_PROMPT))

    @property
    def llm(self) -> LocalLLMAdapter:
        """本地 adapter：执行器把它注入通道后，第 3 级 LLM 抽取在本地档也可用"""
        return self._adapter

    @property
    def provider(self) -> str:
        return "local"

    def __call__(self, invocation: ChannelInvocation) -> RawOutput:
        return self._inner(invocation)


def build_local_channel(*, engine: str = "", model: str = "", api_base: str = "",
                        local: Any = None, system_prompt: str = ""
                        ) -> LocalInferenceChannelExecutor:
    """构造本地通道（**构造期零网络**：不探活、不下载，探活只在真正调用时发生）

    Args:
        engine: 引擎名；缺省读 `CP_SUBAGENT_LOCAL_ENGINE`，再缺省 ollama。
        model: 模型名；缺省读 `CP_SUBAGENT_LOCAL_MODEL`，空则用 core 的内置默认。
        api_base: 端点；缺省读 `CP_SUBAGENT_LOCAL_API_BASE`，空则用 core 的内置默认。
        local: 注入的 `LocalLLM` 替身（测试用；不给就惰性导入 core.local_llm 构造）。
        system_prompt: 覆盖基座 prompt（空 = 用执行器的 DELEGATE_SYSTEM_PROMPT）。

    Raises:
        LocalInferenceError: 引擎无真实实现（fail-closed，不假装支持）。
    """
    eng = str(engine or os.environ.get(ENV_LOCAL_ENGINE, "") or "ollama").strip() or "ollama"
    if eng not in SUPPORTED_LOCAL_ENGINES:
        raise LocalInferenceError(
            "本地引擎未实现: %r（当前仅 %s 有真实实现；vLLM 分支未实现，不假装支持）"
            % (eng, list(SUPPORTED_LOCAL_ENGINES)),
            code="E_LOCAL_ENGINE_UNSUPPORTED")
    if local is None:
        from core.local_llm import LocalLLM

        mdl = str(model or os.environ.get(ENV_LOCAL_MODEL, "") or "").strip()
        base = str(api_base or os.environ.get(ENV_LOCAL_API_BASE, "") or "").strip()
        if mdl:
            local = LocalLLM(engine=eng, model=mdl, api_base=(base or None))
        else:
            # 不抄 core 的默认模型名（避免"硬编码模型名"在第二处出现）
            local = LocalLLM(engine=eng, api_base=(base or None))
    adapter = LocalLLMAdapter(local, model=str(getattr(local, "_model", "") or ""))
    return LocalInferenceChannelExecutor(adapter, system_prompt=system_prompt)
