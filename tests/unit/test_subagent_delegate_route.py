"""分身「真委派」HTTP 面测试（/api/subagent/<name>/delegate）

线上反馈：「子代理没跑通」——根因是前端（以及既有 /execute 端点）走的是
``SubagentContainer.execute()`` 占位骨架（设计上不调 LLM、只回占位文案）。
本文件锁死修好后的契约：

  1. /delegate 走 **run_delegation**（真执行器），并把八要素透传下去；
  2. 缺执行通道（无 LLM/CLI）→ 409 + E_DELEGATION_NO_CHANNEL（明确拒绝，不跑空执行）；
  3. 分身不存在 → 404；目标过短 → 400；
  4. 结果形状与模型侧 delegate 工具一致（复用同一份字段映射）；
  5. /execute 保持原样（占位骨架），避免破坏既有调用方。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

import pytest
from flask import Flask

from agent.server_routes.routes_subagent import register_routes


@dataclass
class FakeOutcome:
    ok: bool = True
    delegation_id: str = "dlg-ui-12345678"
    tier: str = "tier1"
    duration_ms: float = 42.5
    trace_id: str = "trace-abc"
    output_text: str = "子代理的真实产出"
    error_code: str = ""
    error: str = ""
    sub_reason: str = ""
    artifacts: tuple = field(default_factory=tuple)


class FakeContainer:
    """记录 run_delegation 的入参，返回预设 outcome（真执行器不在单测里跑）"""

    def __init__(self, outcome: FakeOutcome | None = None, raise_exc: Exception | None = None):
        self.outcome = outcome or FakeOutcome()
        self.raise_exc = raise_exc
        self.calls: list[Dict[str, Any]] = []

    def run_delegation(self, ctx, **kw):
        self.calls.append({"ctx": ctx, **kw})
        if self.raise_exc:
            raise self.raise_exc
        return self.outcome

    def execute(self, task):
        raise AssertionError("不应走占位骨架 execute()")


class FakeLifecycleManager:
    def __init__(self, container: FakeContainer | None):
        self.container = container

    def get(self, name):
        if self.container is None:
            return None
        return self.container if name == "sa-1" else None

    def list(self):
        return []


class FakeYunshu:
    def __init__(self, container: FakeContainer | None = None, llm: Any = "fake-llm",
                 manager: Any = "auto"):
        # manager="auto" → 正常管理器；显式传 None → 模拟分身系统未启用
        self._subagent_mgr = FakeLifecycleManager(container) if manager == "auto" else manager
        self._llm = llm

    def list_subagents(self):
        return []

    def execute_subagent(self, name, task):
        """仅供 /execute（占位骨架）路径使用"""
        container = self._subagent_mgr.get(name)
        return {"output": container.execute(task).output, "trace_id": "t", "error": None,
                "duration_ms": 0.0, "timestamp": ""}


@pytest.fixture
def make_client(monkeypatch):
    """构造 client；monkeypatch 掉 require_token 鉴权装饰器不生效（register_routes 内绑定）"""
    def _make(container: FakeContainer | None = None, llm: Any = "fake-llm"):
        state = type("S", (), {"Yunshu": FakeYunshu(container, llm)})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        return app.test_client(), state
    return _make


class TestDelegateHappyPath:
    def test_真委派_透传八要素并返回结果形状(self, make_client):
        container = FakeContainer()
        client, _ = make_client(container)

        r = client.post("/api/subagent/sa-1/delegate", json={"task": "把 docs 下的设计稿抽取为步骤序列"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True
        assert body["name"] == "sa-1"
        # 工具侧映射会附"完成度校验"说明（同一份 _result_from_outcome），故用包含断言
        assert "子代理的真实产出" in body["result"]
        assert body["tier"] == "tier1"
        assert body["trace_id"] == "trace-abc"

        # 走的是 run_delegation（不是占位 execute）
        assert len(container.calls) == 1
        call = container.calls[0]
        ctx = call["ctx"]
        assert ctx.goal == "把 docs 下的设计稿抽取为步骤序列"
        # 缺省补齐的八要素（②不得为空列表；④默认给出禁止事项）
        assert list(ctx.constraints) == ["只读为主，不得修改仓库文件"]
        assert list(ctx.prohibitions) == ["不得对外发送数据"]
        assert list(ctx.prior_artifacts) == []
        assert ctx.artifact_format == "文本要点"
        assert ctx.budget_tokens == 4000
        assert ctx.timeout_seconds == 120
        # ⑧回调地址：空串会被八要素判不合格（结果无处投递），故 UI 简化入口给本地占位标识
        assert ctx.callback_url == "ui://workbench/sync"
        assert ctx.delegation_id.startswith("dlg-ui-")
        # 执行通道：LLM 有 → llm 传入；工具子集与模型侧同源（元组）
        assert call["llm"] == "fake-llm"
        assert isinstance(call["tools"], tuple)
        assert call["authorized_capabilities"] == call["tools"]
        # 响应回显八要素（便于 UI 展示与排障）
        assert body["elements"]["budget_tokens"] == 4000

    def test_传入的可选项覆盖缺省(self, make_client):
        container = FakeContainer()
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate", json={
            "task": "统计仓库里 TODO 的数量并给出清单",
            "constraints": ["只读", "不联网"],
            "prohibitions": [],
            "artifact_format": "markdown 表格",
            "budget_tokens": 1000,
            "timeout_seconds": 30,
        })
        assert r.status_code == 200
        ctx = container.calls[0]["ctx"]
        assert list(ctx.constraints) == ["只读", "不联网"]
        # 显式传空列表 → 使用缺省（UI 语义：留空=用缺省）
        assert list(ctx.prohibitions) == ["不得对外发送数据"]
        assert ctx.artifact_format == "markdown 表格"
        assert ctx.budget_tokens == 1000
        assert ctx.timeout_seconds == 30

    def test_委派失败_返回失败画面而非异常(self, make_client):
        container = FakeContainer(FakeOutcome(ok=False, error_code="E_DELEGATION_TIMEOUT",
                                              error="执行超时", sub_reason="timeout"))
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is False
        assert body["error_code"] == "E_DELEGATION_TIMEOUT"
        assert body["error"] == "执行超时"
        assert body["sub_reason"] == "timeout"


class TestDelegateGuards:
    def test_无执行通道_明确拒绝(self, make_client):
        container = FakeContainer()
        client, _ = make_client(container, llm=None)
        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 409
        body = r.get_json()
        assert body["error_code"] == "E_DELEGATION_NO_CHANNEL"
        assert body["channel"]["ok"] is False
        assert container.calls == []  # 不跑注定失败的空执行

    def test_分身不存在_404(self, make_client):
        client, _ = make_client(FakeContainer())
        r = client.post("/api/subagent/not-exist/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 404

    def test_目标过短_400(self, make_client):
        container = FakeContainer()
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate", json={"task": "太短"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == "E_DELEGATION_INCOMPLETE"
        assert container.calls == []

    def test_分身系统未启用_409(self, make_client):
        state = type("S", (), {"Yunshu": FakeYunshu(container=None, manager=None)})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        client = app.test_client()
        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 409
        assert r.get_json()["error_code"] == "E_SUBAGENT_UNAVAILABLE"

    def test_预算非整数_400(self, make_client):
        container = FakeContainer()
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate",
                        json={"task": "一个足够长的目标任务", "budget_tokens": "abc"})
        assert r.status_code == 400
        assert container.calls == []

    def test_执行器异常_500且不裸奔(self, make_client):
        container = FakeContainer(raise_exc=RuntimeError("boom"))
        client, _ = make_client(container)
        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 500
        assert r.get_json()["error_code"] == "E_DELEGATION_FAILED"


class TestListChannelInfo:
    def test_列表返回执行通道可用性(self, make_client):
        client, _ = make_client(FakeContainer(), llm=None)
        body = client.get("/api/subagent/list").get_json()
        assert body["ok"] is True
        assert body["channel"]["llm"] is False
        assert body["channel"]["ok"] is False

    def test_有_LLM_时通道可用(self, make_client):
        client, _ = make_client(FakeContainer(), llm="llm-obj")
        body = client.get("/api/subagent/list").get_json()
        assert body["channel"]["llm"] is True
        assert body["channel"]["ok"] is True


class TestExecuteStaysSkeleton:
    def test_execute_仍走占位骨架_不误用真执行器(self, make_client):
        """既有 /execute 的语义不变（占位骨架）：容器只实现 execute，且 run_delegation 被调用即失败"""

        class SkeletonContainer:
            def execute(self, task):
                return type("R", (), {"output": "占位", "trace_id": "t", "error": None,
                                      "duration_ms": 0.0, "timestamp": ""})()

            def run_delegation(self, *a, **k):
                raise AssertionError("/execute 不应调用 run_delegation")

        yunshu = FakeYunshu(container=SkeletonContainer(), llm=None)
        state = type("S", (), {"Yunshu": yunshu})()
        app = Flask(__name__)
        app.config.update(TESTING=True)
        register_routes(app, state)
        client = app.test_client()
        r = client.post("/api/subagent/sa-1/execute", json={"task": "任意任务"})
        assert r.status_code == 200
        assert r.get_json()["result"]["output"] == "占位"
