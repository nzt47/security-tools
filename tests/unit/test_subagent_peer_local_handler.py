"""镜内真推理处理器守卫（agent/subagent/peer_local_handler.py）

【为什么有这份守卫（不这样会怎样）】
    这个 handler 是"container 内跑 local"的真实产出通道。最危险的两种退化：
      ① 本地服务没起来时**编一段 summary** 冒充推理结果（污染回收三件套与成本账）；
      ② 模型输出不是 JSON 时抛异常，把"非结构化但有效"的产出整条丢掉。
    本守卫钉死：不可用 ⇒ **抛错**（对端非零退出，母体记 E_UPSTREAM_FORMAT）；
    非 JSON ⇒ 如实标 unstructured + 截断保留；JSON ⇒ 保留字段并注入 channel_meta。

不 import app_server；用替身 LLM（不联网、不起 Ollama）。
"""
from __future__ import annotations

import pytest

from agent.subagent import peer_local_handler as h


class _FakeLLM:
    provider = "local"
    model = "qwen2.5:0.5b"

    def __init__(self, out="", *, boom=False):
        self._out = out
        self._boom = boom
        self.seen = None

    def chat(self, messages, system_prompt=""):
        self.seen = (messages, system_prompt)
        if self._boom:
            raise RuntimeError("connect refused")
        return self._out


class TestBuildRecord:
    def test_json对象保留字段并注入channel_meta(self):
        rec = h.build_record('{"summary": "s", "artifacts": [1]}', _FakeLLM())
        assert rec["status"] == "done"
        assert rec["summary"] == "s"
        assert rec["channel_meta"] == {"llm_used": True, "provider": "local",
                                       "model": "qwen2.5:0.5b"}

    def test_显式status不被覆盖(self):
        assert h.build_record('{"status": "partial"}', _FakeLLM())["status"] == "partial"

    def test_非JSON如实标unstructured且截断(self):
        rec = h.build_record("x" * 9000, _FakeLLM())
        assert rec["status"] == "unstructured"
        assert len(rec["summary"]) == 4000
        assert rec["channel_meta"]["llm_used"] is True

    def test_顶层非对象也算unstructured(self):
        assert h.build_record("[1,2]", _FakeLLM())["status"] == "unstructured"


class TestTaskPrompt:
    def test_转写task_file且不执行指令(self):
        prompt = h.task_prompt({"goal": "演示目标", "constraints": ["只读"]})
        assert "演示目标" in prompt and "只读" in prompt
        assert "待处理内容" in prompt

    def test_非对象不抛(self):
        assert isinstance(h.task_prompt(None), str)


class TestRun:
    def test_真推理返回记录(self, monkeypatch):
        monkeypatch.setattr(h, "_resolve_llm",
                            lambda: _FakeLLM('{"status": "done", "summary": "real"}'))
        rec = h.run({"goal": "g"})
        assert rec["status"] == "done" and rec["summary"] == "real"
        assert rec["channel_meta"]["model"] == "qwen2.5:0.5b"

    def test_空产出抛错不假成功(self, monkeypatch):
        monkeypatch.setattr(h, "_resolve_llm", lambda: _FakeLLM(""))
        with pytest.raises(RuntimeError):
            h.run({"goal": "g"})

    def test_本地服务异常抛错(self, monkeypatch):
        monkeypatch.setattr(h, "_resolve_llm", lambda: _FakeLLM(boom=True))
        with pytest.raises(RuntimeError) as ei:
            h.run({"goal": "g"})
        assert "本地推理不可用" in str(ei.value)

    def test_构造失败也抛错(self, monkeypatch):
        def _boom():
            raise RuntimeError("no engine")

        monkeypatch.setattr(h, "_resolve_llm", _boom)
        with pytest.raises(RuntimeError):
            h.run({"goal": "g"})

    def test_task_file非对象拒绝(self):
        with pytest.raises(RuntimeError):
            h.run(["not", "a", "dict"])

    def test_契约签名可被对端调用(self, monkeypatch):
        monkeypatch.setattr(h, "_resolve_llm", lambda: _FakeLLM('{"status":"done"}'))
        # 对端以 handler(task_file, max_turns=..., output_format=...) 调用
        assert h.run({"goal": "g"}, max_turns=3, output_format="json")["status"] == "done"

