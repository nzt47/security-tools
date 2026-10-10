"""真实本地推理端到端（默认跳过；需 `CP_SUBAGENT_LOCAL_E2E=1` + 可用 Ollama）

【为什么有这份守卫（不这样会怎样）】
    `test_subagent_peer_local_handler.py` 用替身、`test_peer_local_handler_loopback.py` 用回环桩，
    都不证明"真模型真的产出"。本文件在**有 Ollama 的机器**上跑真模型，把"镜内 handler →
    真实本地推理 → 有真实产出"钉成可复算证据；CI 无 Ollama 时自动跳过（不红、不假绿）。

【启用方式（本机实测 2026-10-10）】
    docker run -d --name ys-ollama -p 11434:11434 ollama/ollama
    docker exec ys-ollama ollama pull qwen2.5:0.5b
    $env:CP_SUBAGENT_LOCAL_E2E="1"; $env:CP_SUBAGENT_LOCAL_ENABLED="1";
    $env:CP_SUBAGENT_LOCAL_MODEL="qwen2.5:0.5b"; python -m pytest tests/unit/test_peer_local_handler_ollama_e2e.py -q

不 import app_server。
"""
from __future__ import annotations

import os

import pytest

from agent.subagent import peer_local_handler as h

_E2E = os.environ.get("CP_SUBAGENT_LOCAL_E2E", "").strip() == "1"

pytestmark = pytest.mark.skipif(
    not _E2E, reason="需 CP_SUBAGENT_LOCAL_E2E=1 且本机有可用 Ollama + 已拉模型")


class TestRealLocalInference:
    def test_真模型产出非空记录(self):
        rec = h.run({"goal": "只输出一个 JSON 对象：{\"status\":\"done\",\"summary\":\"你是本地模型\"}，不要任何其它文字。",
                     "constraints": ["只输出 JSON"], "artifact_format": "json"})
        meta = rec.get("channel_meta") or {}
        assert meta.get("llm_used") is True
        assert meta.get("provider") == "local"
        assert str(meta.get("model") or ""), "真实推理记录必须带模型名"
        text = str(rec.get("summary") or "")
        assert text.strip(), "真实推理不得产出空记录（否则等于假成功）"

