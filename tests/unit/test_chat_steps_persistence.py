"""工作台 SSE · 思考/工具步骤落盘（刷新/切会话后仍可恢复）测试

对应线上反馈：「会话里面的思考与工具一会就没了」——除前端合并规则外，还有两条
**显示会真正丢失**的路径：刷新页面、切换会话（历史消息此前不含步骤）。
故 SSE 结束时把步骤随 assistant 消息写入会话，前端 loadSessionHistory 再恢复。

本文件锁死服务端口径：
  1. `merge_thinking_step` 与前端 `mergeStepDetail` 同规则（无 detail 事件不清空）；
  2. `finalized_steps` 有界（条数上限）+ 丢弃空步骤；
  3. `SessionManager.add_message(steps=...)` 落盘并可原样读回。
"""
from __future__ import annotations

import json

import pytest

from agent.session_manager import SessionManager
from plugins.chat import finalized_steps, merge_thinking_step


def _steps_after(events: list[dict]) -> list[dict]:
    steps: list[dict] = []
    for e in events:
        merge_thinking_step(steps, {"type": "thinking", **e})
    return steps


class TestMergeThinkingStep:
    def test_阶段事件_done_不清空已累积内容(self):
        """running(带 detail) → done(不带 detail) 后仍保留说明（缺陷根因）"""
        steps = _steps_after([
            {"id": "intent", "title": "意图识别", "detail": "解析输入：你好", "status": "running"},
            {"id": "intent", "title": "意图识别", "status": "done"},
        ])
        assert len(steps) == 1
        assert steps[0]["detail"] == "解析输入：你好"
        assert steps[0]["status"] == "done"

    def test_推理分片累加且完成时保留(self):
        steps = _steps_after([
            {"id": "reasoning", "title": "思考过程", "detail": "先寒暄。", "status": "running"},
            {"id": "reasoning", "title": "思考过程", "detail": "再自我介绍。", "status": "running"},
            {"id": "reasoning", "title": "思考过程", "status": "done"},
        ])
        assert steps[0]["detail"] == "先寒暄。再自我介绍。"
        assert steps[0]["status"] == "done"

    def test_工具调用_参数被结果覆盖(self):
        steps = _steps_after([
            {"id": "tool-real-search", "title": "工具调用：search", "detail": '参数: {"q":"x"}', "status": "running"},
            {"id": "tool-real-search", "title": "工具调用：search", "detail": "结果: ok", "status": "done"},
        ])
        assert steps[0]["detail"] == "结果: ok"
        assert steps[0]["status"] == "done"

    def test_无_id_事件被忽略_超长明细截断(self):
        steps = _steps_after([
            {"title": "无 id", "detail": "x", "status": "running"},
            {"id": "long", "title": "长文本", "detail": "A" * 9000, "status": "running"},
        ])
        assert [s["id"] for s in steps] == ["long"]
        assert len(steps[0]["detail"]) == 4000


class TestFinalizedSteps:
    def test_丢弃空步骤(self):
        assert finalized_steps([{"id": ""}, {"id": "a", "title": "A"}]) == [{"id": "a", "title": "A"}]

    def test_只保留最近_N_条(self):
        many = [{"id": f"s{i}"} for i in range(60)]
        out = finalized_steps(many)
        assert len(out) == 40
        assert out[0]["id"] == "s20"
        assert out[-1]["id"] == "s59"

    def test_空输入(self):
        assert finalized_steps(None) == []
        assert finalized_steps([]) == []


class TestSessionRoundTrip:
    @pytest.fixture
    def mgr(self, tmp_path):
        return SessionManager(sessions_dir=str(tmp_path / "sessions"))

    def test_步骤随_assistant_消息落盘并可读回(self, mgr):
        s = mgr.create_session(title="步骤落盘")
        sid = s["id"]
        mgr.add_message(sid, "user", "你好")
        steps = _steps_after([
            {"id": "intent", "title": "意图识别", "detail": "解析输入：你好", "status": "running"},
            {"id": "intent", "title": "意图识别", "status": "done"},
            {"id": "tool-real-search", "title": "工具调用：search", "detail": "结果: ok", "status": "done"},
        ])
        mgr.add_message(sid, "assistant", "我是云枢。", steps=finalized_steps(steps))

        rows = mgr.get_messages(sid)
        assert [r["role"] for r in rows] == ["user", "assistant"]
        assert rows[0].get("steps") is None
        loaded = rows[1]["steps"]
        assert [x["id"] for x in loaded] == ["intent", "tool-real-search"]
        assert loaded[0]["detail"] == "解析输入：你好"
        assert loaded[0]["status"] == "done"
        # 前端按 /api/sessions/<id>/messages 拿到的就是这些行（JSON 可序列化即可恢复渲染）
        round_tripped = json.loads(json.dumps(rows[1], ensure_ascii=False))
        assert round_tripped["steps"][1]["title"] == "工具调用：search"

    def test_不传_steps_时不写入该字段(self, mgr):
        s = mgr.create_session(title="无步骤")
        mgr.add_message(s["id"], "assistant", "普通回复")
        assert "steps" not in mgr.get_messages(s["id"])[0]
