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
    """生命周期管理器替身：具名端点用 get()，临时分身端点用 delegate()"""

    def __init__(self, container: FakeContainer | None, outcome: FakeOutcome | None = None):
        self.container = container
        self.delegate_outcome = outcome or FakeOutcome()
        self.delegate_calls: list[Dict[str, Any]] = []

    def get(self, name):
        if self.container is None:
            return None
        return self.container if name == "sa-1" else None

    def list(self):
        return []

    def delegate(self, config, ctx, **kw):
        """临时分身委派：真件会"建容器 → 执行 → 回收"，这里只记录入参并返回预设结果"""
        self.delegate_calls.append({"config": config, "ctx": ctx, **kw})
        return self.delegate_outcome


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
    def _make(container: FakeContainer | None = None, llm: Any = "fake-llm",
              manager: Any = "auto"):
        state = type("S", (), {"Yunshu": FakeYunshu(container, llm, manager)})()
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
        # 委派来源标注：界面发起必须标成 ui，委派记录里才能与模型工具/fan_out 区分开
        assert call["source"] == "ui"
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
    """通道可用性提示（UI 据此提前告知"委托执行必然失败"）。

    【2026-10-05 随 P1-front 第三批更新】`GET /api/subagent/list` 已迁移到统一响应信封，
    业务载荷从**顶层**移到了 `data` 下（键名与结构一字未动）。
    本文件原先是 `body["ok"]` / `body["channel"]`，迁移后要读 `data` ——
    加一个 `_data()` 把这件事集中一处，并**顺带断言信封存在**，
    免得将来有人把信封摘掉时这些用例仍然"绿着"。
    """

    @staticmethod
    def _data(client):
        resp = client.get("/api/subagent/list")
        assert resp.headers.get("X-Envelope") == "v2", (
            "该端点应带 X-Envelope: v2（P1-front 第三批已迁移）；实测 "
            + repr(resp.headers.get("X-Envelope"))
        )
        return resp.get_json()["data"]

    def test_列表返回执行通道可用性(self, make_client):
        client, _ = make_client(FakeContainer(), llm=None)
        data = self._data(client)
        assert data["ok"] is True
        assert data["channel"]["llm"] is False
        assert data["channel"]["ok"] is False

    def test_有_LLM_时通道可用(self, make_client):
        client, _ = make_client(FakeContainer(), llm="llm-obj")
        data = self._data(client)
        assert data["channel"]["llm"] is True
        assert data["channel"]["ok"] is True


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


class TestEphemeralDelegate:
    """临时分身委派（POST /api/subagent/delegate）：**不要求先存在分身**

    线上反馈的正面修法：具名端点要求 `mgr.get(name)` 命中存活容器，而容器跑完即回收，
    于是"列表为空 ⇒ 无法委托 ⇒ 只能先去装配车间手工造一个"。本端点走
    `SubagentLifecycleManager.delegate`：现建临时分身 → 执行 → 立即回收。
    """

    def test_一个分身都没有也能真委派_且强制回收(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(manager=mgr)

        r = client.post("/api/subagent/delegate",
                        json={"task": "把 docs 下的设计稿抽取为可复现步骤序列"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["ok"] is True
        assert body["ephemeral"] is True, "响应要能让界面区分「临时分身」与「用户选的分身」"
        assert "子代理的真实产出" in body["result"], "结果形状与模型侧 delegate 工具同源"

        assert len(mgr.delegate_calls) == 1
        call = mgr.delegate_calls[0]
        assert call["destroy_after"] is True, "临时分身必须用完即回收（否则会撞 max_subagents）"
        assert call["source"] == "ui", "委派记录里要能区分界面发起"
        assert call["config"].name, "要给生命周期管理器一个非空名字（消歧与 TTL 由它负责）"
        assert call["ctx"].goal == "把 docs 下的设计稿抽取为可复现步骤序列"
        # 八要素缺省补齐与具名端点同一份规则
        assert list(call["ctx"].constraints) == ["只读为主，不得修改仓库文件"]
        assert list(call["ctx"].prohibitions) == ["不得对外发送数据"]
        assert body["elements"]["budget_tokens"] == 4000
        assert body["elements"]["timeout_seconds"] == 120
        # 工具子集与模型侧同源（只读白名单），不是空集也不是全量
        assert call["tools"] == call["authorized_capabilities"]

    def test_八要素可由请求覆盖(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(manager=mgr)
        client.post("/api/subagent/delegate", json={
            "task": "把 docs 下的设计稿抽取为可复现步骤序列",
            "constraints": ["只读", "不联网"], "prohibitions": ["不得删除任何文件"],
            "artifact_format": "markdown 表格", "budget_tokens": 1500, "timeout_seconds": 45,
        })
        ctx = mgr.delegate_calls[0]["ctx"]
        assert list(ctx.constraints) == ["只读", "不联网"]
        assert list(ctx.prohibitions) == ["不得删除任何文件"]
        assert ctx.artifact_format == "markdown 表格"
        assert (ctx.budget_tokens, ctx.timeout_seconds) == (1500, 45)

    def test_分身系统未启用_409(self, make_client):
        client, _ = make_client(manager=None)  # subagent.enabled=False 的等价形态
        r = client.post("/api/subagent/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 409
        assert r.get_json()["error_code"] == "E_SUBAGENT_UNAVAILABLE"

    def test_无执行通道_409_且不跑空执行(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(llm=None, manager=mgr)
        r = client.post("/api/subagent/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 409
        assert r.get_json()["error_code"] == "E_DELEGATION_NO_CHANNEL"
        assert mgr.delegate_calls == [], "没有执行通道时不该发起委派"

    def test_目标过短_400(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(manager=mgr)
        r = client.post("/api/subagent/delegate", json={"task": "太短"})
        assert r.status_code == 400
        assert r.get_json()["error_code"] == "E_DELEGATION_INCOMPLETE"
        assert mgr.delegate_calls == []

    def test_预算或超时非整数_400(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(manager=mgr)
        r = client.post("/api/subagent/delegate",
                        json={"task": "一个足够长的目标任务", "budget_tokens": "很多"})
        assert r.status_code == 400
        assert "整数" in r.get_json()["error"]
        assert mgr.delegate_calls == []

    def test_两条委派路由互不遮蔽(self, make_client):
        """静态 /api/subagent/delegate 与 /api/subagent/<name>/delegate 必须各自可达"""
        container = FakeContainer()
        mgr = FakeLifecycleManager(container)
        client, _ = make_client(container, manager=mgr)
        rules = {str(x.rule) for x in client.application.url_map.iter_rules()}
        assert "/api/subagent/delegate" in rules
        assert "/api/subagent/<name>/delegate" in rules

        named = client.post("/api/subagent/sa-1/delegate", json={"task": "具名分身的任务目标"})
        temp = client.post("/api/subagent/delegate", json={"task": "临时分身的任务目标"})
        assert named.status_code == 200 and named.get_json().get("ephemeral") is None
        assert temp.status_code == 200 and temp.get_json()["ephemeral"] is True
        assert [c["ctx"].goal for c in container.calls] == ["具名分身的任务目标"]
        assert [c["ctx"].goal for c in mgr.delegate_calls] == ["临时分身的任务目标"]

    def test_具名端点分身不存在时给出临时分身去处(self, make_client):
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(container=None, manager=mgr)
        r = client.post("/api/subagent/sa-gone/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 404
        body = r.get_json()
        assert "分身不存在" in body["error"]
        # 404 不该是死路：告诉调用方还有临时分身入口可用
        assert "/api/subagent/delegate" in body["hint"]


# ══════════════════════════════════════════════════════════════════════
#  ⑥ 分身独立 LLM：具名委派按**这个分身自己的** model_id 派生模型
#     （`agent/subagent/llm_factory.py`；空 = 跟随母体）
# ══════════════════════════════════════════════════════════════════════


class SpyParentLLM:
    """母体 LLM 替身：记录 with_model 调用；派生出的子实例带自己的 model 名"""

    def __init__(self, model: str = "deepseek-flash"):
        self.model = model
        self.provider = "deepseek"
        self.calls: list = []

    def with_model(self, model: str):
        self.calls.append(model)
        child = SpyParentLLM(model)
        child.provider = self.provider
        return child


def _container_with_model(model_id: str, outcome: FakeOutcome | None = None) -> FakeContainer:
    """带上 `config.model_id` 的物质（真容器有 config；替身此前没有）"""
    container = FakeContainer(outcome)
    container.config = type("C", (), {"model_id": model_id})()
    return container


class TestPerSubagentLlm:
    def test_具名委派_按分身自己的模型派生并回显来源(self, make_client):
        parent = SpyParentLLM("deepseek-flash")
        container = _container_with_model("deepseek-v4-pro")
        client, _ = make_client(container, llm=parent)

        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()

        # ① 真委派用的是**派生出来的**实例，不是母体实例
        assert parent.calls == ["deepseek-v4-pro"], "必须经 with_model 派生，且只派生一次"
        used = container.calls[0]["llm"]
        assert used is not parent
        assert used.model == "deepseek-v4-pro"
        # ② 生效模型与来源随响应回显（界面据此显示"这个分身实际跑哪个模型"）
        assert body["llm"] == {"requested": "deepseek-v4-pro",
                               "model": "deepseek-v4-pro",
                               "source": "explicit", "error": ""}

    def test_未声明模型_跟随母体且不派生(self, make_client):
        parent = SpyParentLLM("deepseek-flash")
        container = _container_with_model("")
        client, _ = make_client(container, llm=parent)

        body = client.post("/api/subagent/sa-1/delegate",
                           json={"task": "一个足够长的目标任务"}).get_json()
        assert container.calls[0]["llm"] is parent
        assert parent.calls == []
        assert body["llm"]["source"] == "inherit"
        assert body["llm"]["model"] == "deepseek-flash"

    def test_派生失败_回退母体但响应用户可见(self, make_client):
        """不静默换模型：bad 模型名不该让委派失败，但**换了必须看得见**"""
        class BoomParent(SpyParentLLM):
            def with_model(self, model):
                self.calls.append(model)
                raise RuntimeError("模型名不被接受")

        parent = BoomParent("deepseek-flash")
        container = _container_with_model("gpt-4")
        client, _ = make_client(container, llm=parent)

        r = client.post("/api/subagent/sa-1/delegate", json={"task": "一个足够长的目标任务"})
        assert r.status_code == 200, "回退是刻意的：坏模型名不该让整次委派失败"
        body = r.get_json()
        assert container.calls[0]["llm"] is parent, "回退用的是母体实例"
        llm = body["llm"]
        assert llm["source"] == "fallback-after-error"
        assert llm["requested"] == "gpt-4"
        assert llm["model"] == "deepseek-flash"
        assert "模型名不被接受" in llm["error"], "原因必须原样带回，便于界面点名"

    def test_临时分身_抄的是母体模型_判为跟随(self, make_client):
        """临时分身没有"用户选的模型"：config.model_id 抄母体 ⇒ 不得算成 explicit"""
        parent = SpyParentLLM("deepseek-flash")
        mgr = FakeLifecycleManager(FakeContainer())
        client, _ = make_client(container=None, llm=parent, manager=mgr)

        body = client.post("/api/subagent/delegate",
                           json={"task": "一个足够长的目标任务"}).get_json()
        assert mgr.delegate_calls[0]["llm"] is parent
        assert parent.calls == [], "抄母体模型名不该触发派生（否则白造影子实例）"
        assert body["llm"]["source"] == "inherit"

    def test_列表端点_逐分身给出生效模型与可选清单(self, monkeypatch):
        """列表页要回答"这个分身实际用哪个模型"与"我能填哪些模型名" —— 都在这一份载荷里"""
        import types

        from flask import Flask as _Flask

        from agent.server_routes.routes_subagent import register_routes as _register

        parent = SpyParentLLM("deepseek-flash")

        class RowYunshu:
            _llm = parent

            def list_subagents(self):
                return [{"name": "alpha", "model_id": "deepseek-v4-pro", "status": "running"},
                        {"name": "beta", "model_id": "", "status": "idle"}]

        app = _Flask(__name__)
        app.config.update(TESTING=True)
        _register(app, types.SimpleNamespace(Yunshu=RowYunshu()))
        resp = app.test_client().get("/api/subagent/list")
        # 列表端点走统一信封（{code,data}）：业务载荷在 data 里
        body = resp.get_json()["data"]

        rows = {r["name"]: r for r in body["subagents"]}
        assert rows["alpha"]["llm"]["source"] == "explicit"
        assert rows["alpha"]["llm"]["model"] == "deepseek-v4-pro"
        assert rows["beta"]["llm"]["source"] == "inherit"
        assert rows["beta"]["llm"]["model"] == "deepseek-flash"
        # 部署级事实 + 可选清单（部署模型在前，已声明去重在后；**不编造模型目录**）
        assert body["llm"]["model"] == "deepseek-flash"
        assert body["llm"]["provider"] == "deepseek"
        assert body["llm"]["options"] == [
            {"model": "deepseek-flash", "source": "deployment"},
            {"model": "deepseek-v4-pro", "source": "declared"},
        ]
        # 载荷里不得出现密钥形态
        assert "sk-" not in str(body)

    def test_列表端点_单个分身解析失败不影响整表(self, monkeypatch):
        """逐行兜底：解析器对某个分身抛异常 ⇒ 该行如实记 `fallback-after-error`，整表照常"""
        import types

        from flask import Flask as _Flask

        from agent.server_routes import routes_subagent as routes_mod
        from agent.subagent import llm_factory as factory_mod

        class RowYunshu:
            _llm = SpyParentLLM("deepseek-flash")

            def list_subagents(self):
                return [{"name": "bad", "model_id": "boom"}, {"name": "ok", "model_id": ""}]

        real = factory_mod.resolve_subagent_llm

        def flaky(model_id, parent_llm=None):
            if str(model_id) == "boom":
                raise RuntimeError("解析器炸了")
            return real(model_id, parent_llm=parent_llm)

        monkeypatch.setattr(factory_mod, "resolve_subagent_llm", flaky)
        app = _Flask(__name__)
        app.config.update(TESTING=True)
        routes_mod.register_routes(app, types.SimpleNamespace(Yunshu=RowYunshu()))
        resp = app.test_client().get("/api/subagent/list")
        assert resp.status_code == 200
        body = resp.get_json()["data"]
        rows = {r["name"]: r for r in body["subagents"]}
        assert rows["bad"]["llm"]["source"] == "fallback-after-error"
        assert "解析器炸了" in rows["bad"]["llm"]["error"]
        assert rows["ok"]["llm"]["source"] == "inherit", "同表另一行不受影响"
