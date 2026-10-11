"""本地推理引擎——Ollama / vLLM

原则5「本地可用性」的实现。

═══════════════════════════════════════════════════════════════════════════
状态（2026-10-12 更新）：**已接线**，两条引擎分支都已有真实实现。
  · 调用方：`agent/subagent/local_inference.py`（分身「本地推理执行档 local」）——
    `LocalLLMAdapter` 经 `run_sync` 调本模块的 `generate()`；不再是"0 处引用"。
  · Ollama：`POST /api/generate`（探活 `GET /api/tags`）。
  · **vLLM：`POST /v1/completions`（OpenAI 兼容；探活 `GET /v1/models`）**——
    此前 `ENGINES` 声明了 vllm 却只有 ollama 分支（声明与实现不一致）；本次补齐。

设计原文：「在 `core/local_llm.py` 中实现调用本地推理引擎（Ollama 或 vLLM）的接口，
确保在**完全断网环境**下依然能加载本地模型权重进行推理。」

维护提示：
  · 超时目前硬编码（探活 5s、生成 60s）；若未来按 `D5` 登记到 agent/settings/registry.py，
    改为从 settings 解析，否则零缺口守卫测试会红。
  · 单例 `local_llm = LocalLLM()` 在**模块 import 期**构造 ⇒ 不得在 import 期做网络探活
    （本模块当前没做，勿改坏）。
═══════════════════════════════════════════════════════════════════════════
"""
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)


class LocalLLM:
    #: 引擎端点表。`probe` = 探活路径（**必须与引擎同源**：Ollama 与 vLLM 的探活
    #: 端点不同，此前统一打 `/api/tags` 会让 vLLM 永远判为不可用）。
    ENGINES = {
        "ollama": {"api_base": "http://localhost:11434", "endpoint": "/api/generate",
                   "probe": "/api/tags"},
        "vllm": {"api_base": "http://localhost:8000", "endpoint": "/v1/completions",
                 "probe": "/v1/models"},
    }

    def __init__(self, engine: str = "ollama", model: str = "qwen2.5:7b", api_base: str = None):
        self._engine = engine
        self._model = model
        self._api_base = api_base or self.ENGINES.get(engine, {}).get("api_base", "")
        self._available = False

    async def check_available(self) -> bool:
        """探活：按**当前引擎自己的**探活路径 GET（失败/无端点 ⇒ False，不抛）"""
        probe = str(self.ENGINES.get(self._engine, {}).get("probe", "") or "")
        if not self._api_base or not probe:
            self._available = False
            return False
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._api_base}{probe}", timeout=5):
                    self._available = True
                    return True
        except Exception:
            self._available = False
            logger.warning(f"本地推理引擎不可用: {self._engine}")
            return False

    async def generate(self, prompt: str, max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:
        """生成：未知引擎 fail-closed 返回 None（不假装支持）"""
        if self._engine not in self.ENGINES:
            logger.warning("本地推理引擎未实现: %s", self._engine)
            return None
        if not self._available and not await self.check_available():
            return None
        if self._engine == "ollama":
            return await self._ollama_generate(prompt, max_tokens, temperature)
        if self._engine == "vllm":
            return await self._vllm_generate(prompt, max_tokens, temperature)
        return None

    async def _ollama_generate(self, prompt: str, max_tokens: int, temperature: float) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False,
                   "options": {"num_predict": max_tokens, "temperature": temperature}}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._api_base}/api/generate", json=payload, timeout=60) as resp:
                data = await resp.json()
                return data.get("response", "")

    async def _vllm_generate(self, prompt: str, max_tokens: int, temperature: float) -> str:
        """vLLM（OpenAI 兼容 `/v1/completions`）—— 解析 `choices[0].text`（兼容 message.content）"""
        payload = {"model": self._model, "prompt": prompt, "max_tokens": max_tokens,
                   "temperature": temperature, "stream": False}
        endpoint = self.ENGINES["vllm"]["endpoint"]
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._api_base}{endpoint}", json=payload, timeout=60) as resp:
                data = await resp.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        text = first.get("text")
        if isinstance(text, str):
            return text
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return ""


local_llm = LocalLLM()
