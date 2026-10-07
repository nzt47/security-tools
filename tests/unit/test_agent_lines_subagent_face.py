"""主线档案「第 4 面 · 分身面」的 HTTP 投影守卫

【背景】`agent/subagent/assembly.py::resolve_subagent_assembly` 此前**没有任何读端点**：
唯一消费方是派发路径 `agent/tools/fan_out_tools.py`（把装配单塞进
`DelegationContext.metadata`）。后果是"这条线派出去的分身到底拿到什么"在 UI 上
无从查证；而只要有人在前端或路由层"顺手再算一遍"，就会出现**第二份分身授权口径**
（本仓的"十三处工具真相"都是这么长出来的）。

【本文件锁死的不变量（每条都对应一个可证伪的断言）】
  1. **同源**：`GET /api/agent-lines/<id>` 与 `POST /api/agent-lines/preview` 的
     `subagent_assembly` 必须来自 `resolve_subagent_assembly` **这一处实现** ——
     用替身计数钉死：路由里若自己拼一份（第二口径），替身一次都不会被调到 ⇒ 立刻红。
     派发路径那一侧的同款计数断言在 `tests/unit/test_subagent_assembly.py::
     test_同一主线只算一次装配`（`monkeypatch.setattr(_assembly, ...)`），
     两条合起来才是"HTTP 面与派发面共用同一份判定"。
  2. **两条相反失败语义**（唯一权威：assembly 模块 docstring）
     ① 未点名（`line_id` 为空）⇒ **只读默认集**（`mode=default-readonly`，不是降级）；
     ② 点名了但不存在 / 已停用 / 损坏 ⇒ `LineUnavailable` ⇒ `available=false` +
        人读原因，且**响应其余部分照常返回**（不把整个响应带崩）。
  3. **投影不重算**：装配单字段逐项相等（少一个就是静默丢信息），投影只多出
     `available` / `reason` / `semantics` / `semantics_note` 四个**说明性**字段。

【不易】不起 `app_server`（全站启动 80–100s，与本面无关）：最小 Flask app 只注册
        主线路由。工具候选池经 `_runtime_tool_names` 钉成"已声明的全量工具名"
        （与 `tests/unit/test_subagent_assembly.py` 同款），使本文件不因宿主注册表
        是否已被别的用例加载而变。档案目录经 `YUNSHU_AGENT_LINES_DIR` 隔离到 tmp_path。
【不易】不 mock 被测逻辑：装配、技能包、硬禁判定全部走真实实现（只有"档案目录"
        与"工具候选池"这两个外部输入被钉死）。
"""

from __future__ import annotations

from typing import Any, List

import pytest

from agent.lines import LineProfile, LineRegistry, get_line_registry, load_tool_meta
from agent.subagent.assembly import resolve_subagent_assembly

#: 装配单里必须逐项透传的字段（服务端投影只许**加**说明字段，不许丢装配单字段）
ASSEMBLY_FIELDS = (
    "line_id",
    "mode",
    "tools",
    "needs_approval",
    "note",
    "skills",
    "skills_mode",
    "skills_note",
    "prompt_note",
    "prompt_source",
)

#: 投影额外加的说明性字段（全部是"人读/机器可读的语义标注"，不参与装配判定）
PROJECTION_FIELDS = ("available", "reason", "semantics", "semantics_note")


def _pool() -> List[str]:
    """候选池 = 已声明的全量工具名（与装配单单测同一个口径）"""
    return sorted(load_tool_meta().keys())


def _expected(line_id: str) -> Any:
    """同一份输入下装配单的期望值（**直接调唯一入口**，不重写算法）"""
    return resolve_subagent_assembly(line_id, get_line_registry(), load_tool_meta(), _pool())


@pytest.fixture()
def client(monkeypatch):
    """只注册主线路由的最小 app；候选池钉成"已声明工具"（确定性，见模块 docstring）"""
    flask = pytest.importorskip("flask")
    from agent.server_routes import routes_agent_lines as routes

    monkeypatch.setattr(routes, "_runtime_tool_names", _pool)
    app = flask.Flask("agent_lines_subagent_face_test")
    routes.register_routes(app)
    return app.test_client()


@pytest.fixture()
def temp_lines(tmp_path, monkeypatch):
    """档案目录隔离到 tmp_path（真实 LineRegistry，全局单例也读同一份）"""
    monkeypatch.setenv("YUNSHU_AGENT_LINES_DIR", str(tmp_path))
    return tmp_path


def _save_line(**fields) -> LineProfile:
    fields.setdefault("plane_weights", {"perceive": 1.0, "act": 1.0})
    fields.setdefault("max_tools", 6)
    profile = LineProfile(**fields)
    LineRegistry().save(profile)
    return profile


# ════════════════════════════════════════════════════════════
#  1. 同源：两个端点都只经唯一装配单入口
# ════════════════════════════════════════════════════════════


class TestSingleSource:
    def test_两个端点都调唯一入口且字段逐项相等(self, client, monkeypatch):
        """路由里若自己拼一份装配算法（第二口径）⇒ 替身不会被调用 ⇒ 立刻红"""
        import agent.subagent.assembly as asm_mod

        real = asm_mod.resolve_subagent_assembly
        calls: List[str] = []

        def _counting(line_id, registry, meta, available):
            calls.append(str(line_id))
            return real(line_id, registry, meta, available)

        monkeypatch.setattr(asm_mod, "resolve_subagent_assembly", _counting)

        det = client.get("/api/agent-lines/engineering")
        prev = client.post("/api/agent-lines/preview", json={"id": "engineering"})
        assert det.status_code == 200 and prev.status_code == 200, (det.status_code, prev.status_code)
        assert calls == ["engineering", "engineering"], (
            "两个端点的分身面必须调 resolve_subagent_assembly 本尊" f"（实际调用记录：{calls}）"
        )

        expected = _expected("engineering").to_dict()
        for name, resp in (("详情", det), ("预览", prev)):
            sa = resp.get_json()["subagent_assembly"]
            for key in ASSEMBLY_FIELDS:
                assert sa[key] == expected[key], f"{name}端点 {key} 与装配单不同源"
            assert sa["available"] is True
            assert sa["semantics"] == "named-resolved"
            assert sa["mode"] == expected["mode"] == "line"
            assert set(sa) == set(expected) | set(PROJECTION_FIELDS)

    def test_两个端点的分身面逐字段相等(self, client):
        """同一条已保存线：详情端点与预览端点必须给出同一个分身面（同源同算）"""
        det = client.get("/api/agent-lines/engineering").get_json()["subagent_assembly"]
        prev = client.post("/api/agent-lines/preview", json={"id": "engineering"}).get_json()["subagent_assembly"]
        assert det == prev

    def test_分身面是真的收紧过的_不是主智能体面的副本(self, client):
        """口径自检：同一份档案，分身面 ⊂ 主智能体面，且差额**逐项可解释**

        差额只允许两种来源：① §5.7 机制 3 硬禁（记忆读写）；② govern 平面被剔。
        若将来有人把收紧逻辑删掉，本用例会红；若有人把收紧逻辑改成第三种口径，
        这里也会红（差额里出现既非硬禁也非 govern 的工具）。
        """
        body = client.get("/api/agent-lines/engineering").get_json()
        sa = body["subagent_assembly"]
        main = list(body["preview"]["tools"])
        assert sa["tools"], "engineering 是真实装配线，分身面不该为空"
        assert set(sa["tools"]) <= set(main), "分身面不是主智能体面的子集 ⇒ 装配口径分叉"

        from agent.subagent.toolset import SubAgentToolset

        hard = set(SubAgentToolset.hard_denied(main))
        meta = load_tool_meta()
        dropped = [t for t in main if t not in set(sa["tools"])]
        assert dropped, "分身面与主智能体面完全相同 ⇒ 分身专属收紧没有生效"
        for name in dropped:
            assert (
                name in hard or meta[name].plane == "govern"
            ), f"{name} 既不是 §5.7 机制 3 硬禁、也不在 govern 平面，却被剔出了分身面"
        assert (
            SubAgentToolset.hard_denied(sa["tools"]) == []
        ), "分身面里混进了 §5.7 机制 3 硬禁工具（授权了也永远调不动）"
        assert sa["note"], "被拒 / 硬禁项必须有人读说明（前端直接上屏这一段）"


# ════════════════════════════════════════════════════════════
#  2. 失败语义①：未点名 ⇒ 只读默认集（不是降级）
# ════════════════════════════════════════════════════════════


class TestUnnamedReadonlyDefault:
    def test_未点名_只读默认集且明确写着不是降级(self, temp_lines):
        """【为什么打在投影函数上而不是 HTTP 上】两个端点的 line_id 必然非空：

        详情端点的路径段不会为空；`/preview` 的档案经 `LineProfile.from_dict`
        校验，缺 id / 空 id 一律 400（实测 `{"id": ""}` → 400「主线档案缺少 id」，
        见本类最后一条用例）。故"未点名"这一档由投影函数承担，HTTP 面到不了它 ——
        如实记在这里，而不是编一个到不了的 HTTP 用例假装覆盖了。
        """
        from agent.server_routes.routes_agent_lines import _subagent_assembly_payload

        sa = _subagent_assembly_payload("", get_line_registry(), load_tool_meta(), _pool())
        expected = _expected("").to_dict()
        assert sa["available"] is True
        assert sa["mode"] == expected["mode"] == "default-readonly"
        assert sa["semantics"] == "unnamed-default-readonly"
        for key in ASSEMBLY_FIELDS:
            assert sa[key] == expected[key], key
        assert "不是降级" in sa["semantics_note"]

    def test_只读默认集里没有写文件与_Shell(self, temp_lines):
        """语义不是"降级"的证据：**没点名**时按最小集给（写/执行不在内）"""
        from agent.server_routes.routes_agent_lines import _subagent_assembly_payload
        from agent.tools.subagent_tools import _default_subagent_tools

        sa = _subagent_assembly_payload("", get_line_registry(), load_tool_meta(), _pool())
        assert set(sa["tools"]) <= set(_default_subagent_tools())
        assert "write_file" not in sa["tools"]
        assert "shell_execute" not in sa["tools"]
        assert "只读默认集" in sa["note"]

    def test_preview_对空_id_仍是既有_400(self, client, temp_lines):
        """HTTP 面到不了"未点名"的原因（既有口径，本面不改变它）"""
        for body in ({}, {"id": ""}, {"id": "   "}):
            resp = client.post("/api/agent-lines/preview", json=body)
            assert resp.status_code == 400, body
            assert "缺少 id" in resp.get_json()["error"]


# ════════════════════════════════════════════════════════════
#  3. 失败语义②：点名了却装不上 ⇒ fail-closed（不回退全量授权）
# ════════════════════════════════════════════════════════════


class TestNamedButUnavailable:
    def test_点名但不存在_预览照常返回且如实报装不上(self, client, temp_lines):
        body = client.post("/api/agent-lines/preview", json={"id": "no_such_line"}).get_json()
        sa = body["subagent_assembly"]
        assert sa["available"] is False
        assert sa["mode"] == "unavailable"
        assert sa["semantics"] == "named-but-unavailable"
        assert "不存在" in sa["reason"]
        assert "未回退成全量授权" in sa["reason"]
        assert "E_FAN_OUT_LINE_UNAVAILABLE" in sa["semantics_note"]
        assert sa["tools"] == [] and sa["needs_approval"] == []
        # 关键：错误路径不许把整个响应带崩（预览本体与其余三面照常）
        assert body["ok"] is True
        assert body["preview"]["line_id"] == "no_such_line"
        assert body["skills"]["mode"]
        assert "prompt_fragments" in body

    def test_已停用线_详情照常_200_但分身面报装不上(self, client, temp_lines):
        """档案存在 ⇒ 详情仍是既有 200；分身面才是"派发会失败"的那个事实"""
        _save_line(id="probe_disabled", enabled=False, prompt_note="停用线不该被装配")
        resp = client.get("/api/agent-lines/probe_disabled")
        assert resp.status_code == 200
        sa = resp.get_json()["subagent_assembly"]
        assert sa["available"] is False
        assert "已停用" in sa["reason"]
        assert "未回退成全量授权" in sa["reason"]

    def test_损坏档案_预览照常返回且按不可用处理(self, client, temp_lines):
        """损坏档案（YAML 合法但不是字典）⇒ 装配单报"不可用"，**不静默降级**"""
        raw = "- 这是一个列表\n- 不是字典\n"
        (temp_lines / "probe_broken.yaml").write_text(raw, encoding="utf-8")
        resp = client.post("/api/agent-lines/preview", json={"id": "probe_broken"})
        assert resp.status_code == 200
        body = resp.get_json()
        sa = body["subagent_assembly"]
        assert sa["available"] is False
        assert (
            sa["semantics"] == "named-but-unavailable"
        ), "损坏档案必须走 fail-closed 那一档，而不是被投影层当成'降级'吞掉"
        assert "不可用" in sa["reason"]

    def test_装配单入口异常_降级成说明字段且预览照常(self, client, monkeypatch):
        """投影层自身的降级路径：`degraded` 只影响本块，预览本体必须照常 200

        【为什么与"点名线装不上"分开】那是**产品语义**（fail-closed，工具有意留空）；
        这是**故障降级**（异常，非 LineUnavailable）—— 两者的 `semantics` 不同，
        前端对它们的呈现也不同，不能被一句"出错了"糊成同一档。
        """
        import agent.subagent.assembly as asm_mod

        def _boom(*a: Any, **kw: Any):
            raise RuntimeError("boom")

        monkeypatch.setattr(asm_mod, "resolve_subagent_assembly", _boom)
        resp = client.post("/api/agent-lines/preview", json={"id": "engineering"})
        assert resp.status_code == 200, resp.status_code
        body = resp.get_json()
        sa = body["subagent_assembly"]
        assert sa["available"] is False
        assert sa["semantics"] == "degraded"
        assert "降级" in sa["reason"] and "boom" in sa["reason"]
        assert body["preview"]["line_id"] == "engineering"
