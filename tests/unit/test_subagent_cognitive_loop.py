"""认知闭环守卫（agent/subagent/cognitive_loop.py）

【为什么有这份守卫（不这样会怎样）】
    反思环最容易出的两种事故：
      ① **本来没事却出事** —— 反思调用失败把一次正常委派拖死；
      ② **假装闭环** —— 模型乱答/非 JSON 也照样重跑，或第二轮更差还拿第二轮。
    本守卫逐条钉死：LLM 缺失⇒逐字透传；任一步失败⇒退回原产出；第二轮更差⇒保留第一轮；
    开启/关闭的决定性差别有显式断言（未开启 = 旧行为）。

不 import app_server；不联网（替身 LLM + 脚本化通道）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.subagent.channel import ChannelInvocation, RawOutput
from agent.subagent.cognitive_loop import (AUDIT_PLAN, AUDIT_REFLECT, AUDIT_REVISE,
                                           ReflectiveChannelExecutor, cognitive_loop_enabled,
                                           parse_critique, plan_prompt, reflect_prompt)


class _FakeLLM:
    model = "fake"
    provider = "internal"

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, messages, system_prompt=""):
        self.calls.append((messages, system_prompt))
        if not self.replies:
            raise RuntimeError("no more replies")
        out = self.replies.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


class _ScriptedInner:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.invocations = []

    def __call__(self, invocation):
        self.invocations.append(invocation)
        if not self.outputs:
            return RawOutput(stdout="", returncode=0)
        return self.outputs.pop(0)


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, action, *, actor="", subject="", payload=None, status=""):
        self.events.append({"action": action, "status": status, "payload": dict(payload or {})})


def _task_file(tmp_path: Path) -> str:
    path = tmp_path / "task.json"
    path.write_text(json.dumps({"goal": "写出结论", "constraints": ["只输出 JSON"]},
                               ensure_ascii=False), encoding="utf-8")
    return str(path)


def _invocation(task_file: str) -> ChannelInvocation:
    return ChannelInvocation(argv=("python", "peer.py", "-p", task_file), task_file=task_file)


class TestEnableAndParse:
    def test_默认关闭且只认显式真值(self):
        assert cognitive_loop_enabled({}) is False
        for raw in ("0", "false", "", "off", "no"):
            assert cognitive_loop_enabled({"CP_SUBAGENT_COGNITIVE_LOOP": raw}) is False
        for raw in ("1", "true", "TRUE", "Yes", "on"):
            assert cognitive_loop_enabled({"CP_SUBAGENT_COGNITIVE_LOOP": raw}) is True

    def test_解析自评(self):
        assert parse_critique('{"verdict":"pass"}') == {"verdict": "pass", "issues": [], "suggestion": ""}
        got = parse_critique('```json\n{"verdict":"REVISE","issues":["缺结论"],"suggestion":"补"}\n```')
        assert got["verdict"] == "revise" and got["issues"] == ["缺结论"]
        assert parse_critique("随便一段话") is None
        assert parse_critique('{"verdict":"maybe"}') is None
        assert parse_critique("[1,2]") is None

    def test_提示词把两边当数据(self):
        assert "写出结论" in plan_prompt({"goal": "写出结论"})
        assert "写出结论" in reflect_prompt({"goal": "写出结论"}, "产出")


class TestReflectiveLoop:
    def test_无LLM逐字透传_不产生额外调用(self, tmp_path):
        inner = _ScriptedInner([RawOutput(stdout="orig")])
        ex = ReflectiveChannelExecutor(inner, None)
        out = ex(_invocation(_task_file(tmp_path)))
        assert out.stdout == "orig"
        assert len(inner.invocations) == 1
        assert inner.invocations[0].task_file == _task_file(tmp_path).replace(_task_file(tmp_path), inner.invocations[0].task_file)

    def test_pass只跑一轮且注入plan(self, tmp_path):
        task = _task_file(tmp_path)
        llm = _FakeLLM(['{"steps":["一","二"]}', '{"verdict":"pass"}'])
        inner = _ScriptedInner([RawOutput(stdout="done-1")])
        audit = _Audit()
        out = ReflectiveChannelExecutor(inner, llm, audit=audit)(_invocation(task))
        assert out.stdout == "done-1"
        assert len(inner.invocations) == 1
        planned = inner.invocations[0]
        assert planned.task_file != task and planned.task_file.endswith(".planned.json")
        assert json.loads(Path(planned.task_file).read_text(encoding="utf-8"))["plan"].startswith("{")
        assert planned.argv[3] == planned.task_file
        assert [e["action"] for e in audit.events] == [AUDIT_PLAN, AUDIT_REFLECT]
        assert audit.events[1]["status"] == "ok"

    def test_revise注入self_review并采用第二轮(self, tmp_path):
        task = _task_file(tmp_path)
        llm = _FakeLLM(['{"steps":["一"]}', '{"verdict":"revise","issues":["缺结论"],"suggestion":"补结论"}'])
        inner = _ScriptedInner([RawOutput(stdout="weak"), RawOutput(stdout="strong")])
        audit = _Audit()
        out = ReflectiveChannelExecutor(inner, llm, audit=audit)(_invocation(task))
        assert out.stdout == "strong"
        assert len(inner.invocations) == 2
        second = inner.invocations[1]
        assert second.task_file.endswith(".revised.json")
        revised = json.loads(Path(second.task_file).read_text(encoding="utf-8"))
        assert revised["self_review"]["issues"] == ["缺结论"]
        assert revised["self_review"]["suggestion"] == "补结论"
        assert audit.events[-1]["action"] == AUDIT_REVISE and audit.events[-1]["status"] == "revised"

    def test_第二轮没产出则保留第一轮(self, tmp_path):
        llm = _FakeLLM(['{"steps":["一"]}', '{"verdict":"revise"}'])
        inner = _ScriptedInner([RawOutput(stdout="first"), RawOutput(stdout="")])
        audit = _Audit()
        out = ReflectiveChannelExecutor(inner, llm, audit=audit)(_invocation(_task_file(tmp_path)))
        assert out.stdout == "first"
        assert audit.events[-1]["status"] == "degraded"

    def test_自评解析失败不重跑(self, tmp_path):
        llm = _FakeLLM(['{"steps":["一"]}', "不是 JSON"])
        inner = _ScriptedInner([RawOutput(stdout="only")])
        audit = _Audit()
        out = ReflectiveChannelExecutor(inner, llm, audit=audit)(_invocation(_task_file(tmp_path)))
        assert out.stdout == "only" and len(inner.invocations) == 1
        assert audit.events[-1]["status"] == "degraded"

    def test_max_revisions为0不重跑(self, tmp_path):
        llm = _FakeLLM(['{"steps":["一"]}', '{"verdict":"revise"}'])
        inner = _ScriptedInner([RawOutput(stdout="only")])
        out = ReflectiveChannelExecutor(inner, llm, max_revisions=0)(_invocation(_task_file(tmp_path)))
        assert out.stdout == "only" and len(inner.invocations) == 1

    def test_规划失败退回原任务(self, tmp_path):
        task = _task_file(tmp_path)
        llm = _FakeLLM([RuntimeError("llm down"), '{"verdict":"pass"}'])
        inner = _ScriptedInner([RawOutput(stdout="raw")])
        out = ReflectiveChannelExecutor(inner, llm)(_invocation(task))
        assert out.stdout == "raw"
        assert inner.invocations[0].task_file == task

    def test_第一轮无产出不反思(self, tmp_path):
        llm = _FakeLLM([])
        inner = _ScriptedInner([RawOutput(stdout="", returncode=1)])
        out = ReflectiveChannelExecutor(inner, llm)(_invocation(_task_file(tmp_path)))
        assert out.stdout == ""
        assert len(inner.invocations) == 1
        assert len(llm.calls) == 1, "只应有规划那一次调用；无产出不得再反思"


class TestExecutorWiring:
    def test_开关关闭时不包装(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_COGNITIVE_LOOP", raising=False)
        from agent.subagent.executor import DelegationExecutor
        inner = _ScriptedInner([RawOutput(stdout="x")])
        ex = DelegationExecutor(channel=inner, llm=_FakeLLM([]))
        assert ex._channel is inner

    def test_开关开启时包装(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_COGNITIVE_LOOP", "1")
        from agent.subagent.executor import DelegationExecutor
        inner = _ScriptedInner([RawOutput(stdout="x")])
        ex = DelegationExecutor(channel=inner, llm=_FakeLLM([]))
        assert isinstance(ex._channel, ReflectiveChannelExecutor)



class TestLearning:
    def test_教训落盘且下一轮规划读到(self, tmp_path):
        task = _task_file(tmp_path)
        # 第一轮：verdict=revise ⇒ 写一条教训
        llm1 = _FakeLLM(['{"steps":["一"]}',
                         '{"verdict":"revise","issues":["缺结论"],"suggestion":"补结论"}'])
        inner1 = _ScriptedInner([RawOutput(stdout="weak"), RawOutput(stdout="strong")])
        ReflectiveChannelExecutor(inner1, llm1)(_invocation(task))
        lessons = tmp_path / "cognitive_lessons.jsonl"
        assert lessons.is_file(), "revise 后应落一条教训"
        row = json.loads(lessons.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert row["issues"] == ["缺结论"] and row["verdict"] == "revise"
        # 第二轮：规划提示里应带上一条教训（学习闭环的"用"）
        llm2 = _FakeLLM(['{"steps":["一"]}', '{"verdict":"pass"}'])
        inner2 = _ScriptedInner([RawOutput(stdout="ok")])
        ReflectiveChannelExecutor(inner2, llm2)(_invocation(task))
        plan_text = llm2.calls[0][0][0]["content"]
        assert "缺结论" in plan_text, "下一轮规划必须能读到既往教训"

    def test_pass不写教训(self, tmp_path):
        llm = _FakeLLM(['{"steps":["一"]}', '{"verdict":"pass"}'])
        inner = _ScriptedInner([RawOutput(stdout="ok")])
        ReflectiveChannelExecutor(inner, llm)(_invocation(_task_file(tmp_path)))
        assert not (tmp_path / "cognitive_lessons.jsonl").exists()

    def test_教训文件损坏不影响规划(self, tmp_path):
        task = _task_file(tmp_path)
        (tmp_path / "cognitive_lessons.jsonl").write_text("不是JSON\n", encoding="utf-8")
        llm = _FakeLLM(['{"steps":["一"]}', '{"verdict":"pass"}'])
        inner = _ScriptedInner([RawOutput(stdout="ok")])
        out = ReflectiveChannelExecutor(inner, llm)(_invocation(task))
        assert out.stdout == "ok"

