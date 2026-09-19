"""本地推理引擎——Ollama / vLLM / llama.cpp

原则5「本地可用性」的实现。

═══════════════════════════════════════════════════════════════════════════
⚠️ 状态：**未接线（未兑现的设计需求）** —— 2026-09-20 核实并显式标注

本模块**没有任何调用方**，是"实现了但从未接上"的资产，不是意外遗留的垃圾代码。
核实依据（2026-09-20 重新检索，非沿用旧快照）：
  · **代码引用：0 处**。全部命中仅在本文件自身；`core/__init__.py` 只再导出 `registry`。
  · **它对应的是一条明确的设计需求**，见这些文档（均早于本标注）：
      - `docs/superpowers/design/P1_核心调度与本地推理.md:24`
      - `docs/superpowers/design/原：主权AI多Agent系统 .txt:308`
      - `docs/superpowers/design/00_首条消息_全局规范与启动.md:206`
      - `docs/superpowers/design/设计多Agent系统架构方案-20260622094717.txt:705`
    原文要求：「在 `core/local_llm.py` 中实现调用本地推理引擎（Ollama 或 vLLM）的接口，
    确保在**完全断网环境**下依然能加载本地模型权重进行推理。」
  · `docs/superpowers/specs/2026-06-22-架构合规性审计报告.md:13` 也点过它的状态：
    「`core/local_llm.py` 存在但无离线 E2E 验证」。

## 为什么选择"标注"而不是"删除"

审计给出的处置是「删除/标注」二选一。选择标注的理由：
1. **它不是垃圾，是一条未兑现的需求的物证**。删掉后，"原则5 本地可用性"这条
   设计承诺就**没有任何可追溯的痕迹**了，下一个人会重新从零设计。
2. 本模块**是可用的完整实现**（`LocalLLM` 类约 25 条语句：引擎端点表、
   `check_available()` 探活、`generate()` 分派、`_ollama_generate()` 真实 POST），
   不是占位 stub ⇒ 接线成本远低于重写。
3. 覆盖率为 0% 是因为**没人调用它**，不是因为代码分支复杂 —— 显式标注后，
   这个数字不再会被误读成"该补测"。

## 若要接线（给后续执行者的最短路径）

1. 判定入口：`check_available()` 应在一个**降级链**里被调用（上游 LLM 不可达时兜底），
   当前 `memory/llm_service.py` 只有远端 provider，无本地回退分支。
2. `generate()` 目前**只实现了 `ollama` 分支**（`:37-39`：非 ollama 直接返回 None）
   ⇒ `ENGINES` 里声明的 `vllm` **声明了但未实现**，接线时要么补实现、要么从 `ENGINES` 移除，
   **不要让声明与实现不一致**（本仓已有此类缺陷先例，见 `TOOL-` 系列审计）。
3. 超时是硬编码的（探活 5s、生成 60s）⇒ 若接线，应按 `D5` 登记到
   `agent/settings/registry.py`，否则零缺口守卫测试会红。
4. 单例 `local_llm = LocalLLM()`（`:49`）在**模块 import 期**构造 ⇒ 若接线，
   注意它不应在 import 期做网络探活（本模块当前没做，符合要求，勿改坏）。

## 与本标注的关系（维护提示）

上表"代码引用 0 处"是标注当时的实测。**若你接线了本模块**，请删除本段并
同步更新 `docs/closeout/COVERAGE_GAP_PLAN_20260919.md` 的 L-11 条目状态。
═══════════════════════════════════════════════════════════════════════════
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
