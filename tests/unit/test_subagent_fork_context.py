"""E2-3 fork 上下文：轮次截取 / 字符上限 / 摘要 / 失败降级"""
from __future__ import annotations

import pytest

from agent.subagent import fork_context as fc


def _msgs(rounds: int, size: int = 20):
    out = []
    for i in range(1, rounds + 1):
        out.append({"role": "user", "content": ("问%d " % i) + "x" * size})
        out.append({"role": "assistant", "content": ("答%d " % i) + "y" * size})
    return out


class FakeMgr:
    def __init__(self, messages, raise_exc=None):
        self.messages = messages
        self.raise_exc = raise_exc
        self.calls = []

    def get_messages(self, sid, limit=0):
        self.calls.append((sid, limit))
        if self.raise_exc:
            raise self.raise_exc
        return self.messages


def test_默认取最近六轮且标注来源():
    mgr = FakeMgr(_msgs(8))
    data = fc.collect_parent_context("s-1", manager=mgr)
    assert data["ok"] is True
    assert data["from_session"] == "s-1"
    assert data["rounds"] == 6
    assert data["chars"] > 0 and data["chars"] <= fc.MAX_FORK_CHARS
    assert data["note"] == fc.FORK_NOTE
    assert data["messages"][0]["role"] == "user"
    # 取的是**最近** 6 轮：第一条应来自第 3 轮（8-6+1）
    assert data["messages"][0]["content"].startswith("问3")


def test_fork_messages_收敛到_1_20():
    mgr = FakeMgr(_msgs(30))
    assert fc.collect_parent_context("s", rounds=0, manager=mgr)["rounds"] == 1
    assert fc.collect_parent_context("s", rounds=999, manager=mgr)["rounds"] == fc.MAX_FORK_ROUNDS


def test_字符上限优先保留新内容():
    mgr = FakeMgr(_msgs(6, size=500))
    data = fc.collect_parent_context("s", rounds=6, max_chars=300, manager=mgr)
    assert data["chars"] <= 300
    assert data["truncated"] is True
    # 新近优先：最后一条是最后一轮的助手答复
    assert data["messages"][-1]["role"] == "assistant"
    assert "答6" in data["messages"][-1]["content"]


def test_摘要模式只留助手前_160_字():
    mgr = FakeMgr(_msgs(1, size=600))
    data = fc.collect_parent_context("s", rounds=1, summarize=True, manager=mgr)
    assistant = [m for m in data["messages"] if m["role"] == "assistant"][0]
    assert len(assistant["content"]) <= fc.SUMMARY_CHARS
    assert data["summarized"] is True


def test_空会话_无管理器_读失败都有明确错误码(monkeypatch):
    assert fc.collect_parent_context("")["code"] == "no_session"
    monkeypatch.setattr(fc, "_default_manager", lambda: None)
    assert fc.collect_parent_context("s")["code"] == "no_session_manager"
    empty = fc.collect_parent_context("s", manager=FakeMgr([]))
    assert empty["ok"] is False and empty["code"] == "not_found"
    boom = fc.collect_parent_context("s", manager=FakeMgr(_msgs(2), raise_exc=RuntimeError("x")))
    assert boom["ok"] is False and boom["code"] == "read_failed"


def test_空片段不占位():
    msgs = [{"role": "user", "content": "问题"}, {"role": "assistant", "content": "  "}]
    data = fc.collect_parent_context("s", rounds=1, manager=FakeMgr(msgs))
    assert [m["role"] for m in data["messages"]] == ["user"]

def test_fork_上下文真的进了子代理第一轮(tmp_path):
    """端到端：metadata.fork → task_file → 内部 LLM 执行器的首条 user 消息"""
    import json
    import tempfile

    from agent.subagent.delegation import DelegationContext
    from agent.subagent.executor import DelegationExecutor

    class FakeLlm:
        model = "fake"
        provider = "local"

        def __init__(self):
            self.seen = []

        def chat(self, messages, system_prompt=""):
            self.seen.append([m["content"] for m in messages])
            return json.dumps({"status": "done", "summary": "ok", "artifacts": [],
                               "self_eval": {"verdict": "pass", "score": 1,
                                             "summary": "", "issues": []},
                               "tool_calls": []})

    llm = FakeLlm()
    ctx = DelegationContext(
        goal="接着上一轮把交接文档补完", constraints=["只读"], prior_artifacts=[],
        prohibitions=["不要联网"], artifact_format="json", budget_tokens=1000,
        timeout_seconds=30, callback_url="http://127.0.0.1:1/cb",
        metadata={"fork": {"from_session": "s-1", "rounds": 1, "chars": 9,
                           "messages": [{"role": "user", "content": "上一轮说的是 A 方案"}],
                           "note": "参考"}})
    ex = DelegationExecutor(llm=llm, workspace=tempfile.mkdtemp(prefix="fork-"), max_turns=2)
    out = ex.execute(ctx)
    assert out.ok, out.error
    first = "\n".join(llm.seen[0])
    assert "上一轮说的是 A 方案" in first, "fork 上下文没有进子代理首轮上下文"
    assert "fork" in first and "s-1" in first
