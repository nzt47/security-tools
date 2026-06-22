"""本地推理引擎——Ollama / vLLM / llama.cpp

原则5「本地可用性」的实现。
"""
import logging
import aiohttp
from typing import Optional

logger = logging.getLogger(__name__)

class LocalLLM:
    ENGINES = {
        "ollama": {"api_base": "http://localhost:11434", "endpoint": "/api/generate"},
        "vllm": {"api_base": "http://localhost:8000", "endpoint": "/v1/completions"},
    }

    def __init__(self, engine: str = "ollama", model: str = "qwen2.5:7b", api_base: str = None):
        self._engine = engine
        self._model = model
        self._api_base = api_base or self.ENGINES.get(engine, {}).get("api_base", "")
        self._available = False

    async def check_available(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self._api_base}/api/tags", timeout=5):
                    self._available = True
                    return True
        except Exception:
            self._available = False
            logger.warning(f"本地推理引擎不可用: {self._engine}")
            return False

    async def generate(self, prompt: str, max_tokens: int = 2048, temperature: float = 0.7) -> Optional[str]:
        if not self._available and not await self.check_available():
            return None
        if self._engine == "ollama":
            return await self._ollama_generate(prompt, max_tokens, temperature)
        return None

    async def _ollama_generate(self, prompt: str, max_tokens: int, temperature: float) -> str:
        payload = {"model": self._model, "prompt": prompt, "stream": False,
                   "options": {"num_predict": max_tokens, "temperature": temperature}}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{self._api_base}/api/generate", json=payload, timeout=60) as resp:
                data = await resp.json()
                return data.get("response", "")

local_llm = LocalLLM()
