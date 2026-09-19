"""子代理执行体「实际运行模型」声明测试

线上实测：委派返回的自述写着"底层推理由 Anthropic 的 Claude 模型提供"，而实际跑在
deepseek 上 —— 因为 system prompt 从未告知子代理真实模型，模型只能臆测厂商。
这属于**对外输出的可信度**问题（用户会据此误判部署），故在 system prompt 末尾如实声明：
  - 有 model 时追加"你的实际运行模型：provider/model（以此为准；不得臆测…）"；
  - 无 model 时不追加（不制造空声明），system prompt 与既有行为完全一致。
"""
from __future__ import annotations

import json

import pytest

from agent.subagent.executor import LlmChannelExecutor


class _RecordingLLM:
    """记录每次 chat 的 system_prompt 与 messages"""

    def __init__(self, model="deepseek-v4-flash", provider="deepseek", reply=None):
        self.model = model
        self.provider = provider
        self.reply = reply or json.dumps({"status": "done", "summary": "ok"}, ensure_ascii=False)
        self.calls = []

    def chat(self, messages, system_prompt=""):
        self.calls.append({"messages": list(messages), "system_prompt": system_prompt})
        return self.reply


class _Invocation:
    def __init__(self, task_file: str, max_turns: int = 2):
        self.task_file = task_file
        self.max_turns = max_turns


@pytest.fixture
def task_file(tmp_path):
    p = tmp_path / "task_file.json"
    p.write_text(json.dumps({"goal": "用一句话说明你由什么模型驱动", "constraints": ["只读"]},
                            ensure_ascii=False), encoding="utf-8")
    return str(p)


class TestModelDeclaration:
    def test_声明实际模型_且不臆测其它厂商(self, task_file):
        llm = _RecordingLLM(model="deepseek-v4-flash", provider="deepseek")
        out = LlmChannelExecutor(llm)(_Invocation(task_file))
        assert out.returncode == 0
        sp = llm.calls[0]["system_prompt"]
        assert "你的实际运行模型：deepseek/deepseek-v4-flash" in sp
        assert "不得臆测" in sp
        # 原有委派契约仍在（只在末尾追加，不改既有内容）
        assert "单个 JSON 对象" in sp

    def test_无_provider_时只写模型名(self, task_file):
        llm = _RecordingLLM(model="deepseek-chat", provider="")
        LlmChannelExecutor(llm)(_Invocation(task_file))
        assert "你的实际运行模型：deepseek-chat" in llm.calls[0]["system_prompt"]

    def test_无_model_时不追加声明(self, task_file):
        llm = _RecordingLLM(model="", provider="deepseek")
        LlmChannelExecutor(llm)(_Invocation(task_file))
        sp = llm.calls[0]["system_prompt"]
        assert "你的实际运行模型" not in sp

    def test_多轮时每轮都带声明(self, task_file):
        llm = _RecordingLLM(reply="还需要一轮")  # 非最终 JSON → 触发续跑
        LlmChannelExecutor(llm)(_Invocation(task_file, max_turns=2))
        assert len(llm.calls) >= 2
        assert all("你的实际运行模型" in c["system_prompt"] for c in llm.calls)
