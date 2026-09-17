"""工具审批闭环 —— 端到端验收（闸门 ⇄ 审批流 ⇄ 人工裁决 ⇄ 恢复执行）

这是"治理平面 = 审批边界"这条链路的**唯一**端到端证据。链路：

    模型调用工具
      → ``agent/tools/__init__.py::call()``
      → ``agent/tool_gate.py::check_tool_call()``
      → 命中审批边界 ⇒ ``agent/tool_approval.py`` 向既有审批流挂单
      → 返回 ``APPROVAL_REQUIRED`` + ``approval_id`` + ``guidance``
      → 人工在「治理 → 审批收件箱」批准（本测试直接调 ``ApprovalFlow.approve``，
        与路由同源同库）
      → 模型**原样重试**同一次调用（同一工具 + 同一参数）⇒ 消费批准后真的执行
      → 同一张批准**只能用一次**（再调即重新挂单）

为什么用**描述符**而不是 YAML 触发：YAML 是 ``data/tool_definitions/*.yaml``（91 个真实
工具），探针工具不在其中；而描述符的 ``trust.requires_approval`` 与 YAML 的
``needs_approval`` 在闸门里走的是**同一个** ``_tool_approval_outcome``，故这条用例覆盖的
正是同一条代码路径。真实边界工具（``shell_execute`` 等）另有一条轻量断言。
"""
from __future__ import annotations

import json

import pytest

from agent import tools as registry
import agent.tool_gate as G

PROBE_TOOL = "probe_approval_e2e_tool"
PROBE_CID = "cp.builtin." + PROBE_TOOL


def _descriptor_entry(cid: str, name: str, requires_approval: bool = True) -> dict:
    return {
        "meta": {"id": cid},
        "capability": {"name": name},
        "trust": {"requires_approval": requires_approval, "risk_level": "critical"},
    }


@pytest.fixture
def approval_env(tmp_path, monkeypatch):
    """把闸门的两份规则文件、审批库、消费台账**全部**指向 tmp_path

    审批库与消费台账必须隔离：本文件的用例会真的挂单/批准，若不隔离就会写进
    运行时的 ``data/approval_records.jsonl``（本仓已四次踩到"测试写生产数据"）。
    """
    import agent.tool_approval as TA

    policy_path = tmp_path / "permission_policies.json"
    desc_path = tmp_path / "descriptors.json"
    policy_path.write_text(json.dumps(
        {"version": 1, "default_role": "guest", "roles": {}}), encoding="utf-8")
    desc_path.write_text(json.dumps({
        "schema_version": 1,
        "descriptors": {PROBE_CID: _descriptor_entry(PROBE_CID, PROBE_TOOL)},
    }, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(G, "POLICY_POLICIES_PATH", str(policy_path))
    monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(desc_path))
    monkeypatch.delenv(G.GATE_ENABLED_ENV, raising=False)
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    monkeypatch.setenv("APPROVAL_RECORDS_PATH", str(tmp_path / "approval_records.jsonl"))
    monkeypatch.setenv("CP_TOOL_APPROVAL_USES_PATH", str(tmp_path / "uses.jsonl"))
    G._reset_cache()
    TA.reset_cache()
    yield {"records": tmp_path / "approval_records.jsonl",
           "uses": tmp_path / "uses.jsonl"}
    TA.reset_cache()
    G._reset_cache()


@pytest.fixture
def probe_tool():
    """计数探针工具（注册到真实注册表，用完精确还原）"""
    calls = {"n": 0}

    def _handler(**kwargs):
        calls["n"] += 1
        return {"ok": True, "result": "handler-ran", "echo": kwargs}

    saved_entry = registry._registry.get(PROBE_TOOL)
    saved_health = registry._tool_health.get(PROBE_TOOL)
    registry.register(PROBE_TOOL, "审批闭环端到端探针", handler=_handler)
    try:
        yield calls
    finally:
        if saved_entry is None:
            registry.unregister(PROBE_TOOL)
        else:
            registry._registry[PROBE_TOOL] = saved_entry
            registry._registry_version += 1
        if saved_health is None:
            registry._tool_health.pop(PROBE_TOOL, None)
        else:
            registry._tool_health[PROBE_TOOL] = saved_health


def _flow():
    """与审批路由同源同库的审批流（``ApprovalFlow()`` 读 ``APPROVAL_RECORDS_PATH``）"""
    from agent.skills_mgmt.approval import ApprovalFlow
    return ApprovalFlow()


def _call(**kwargs):
    return registry.call(PROBE_TOOL, **kwargs)


# ════════════════════════════════════════════════════════════
#  一、挂单
# ════════════════════════════════════════════════════════════


class TestRequest:

    def test_首次调用被拦并挂单(self, approval_env, probe_tool):
        result = _call(a=1)

        assert result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert result["approval_id"], "未返回审批单号，人工无从审批"
        assert "guidance" in result, "未告知模型怎么恢复（应提示原样重试）"
        assert probe_tool["n"] == 0, "被拦的调用绝不能真的执行 handler"

        # 单子确实进了审批收件箱（同一份存储，UI 读的就是它）
        pending = _flow().list({"state": "pending_review"}, limit=50)
        assert [r.record_id for r in pending] == [result["approval_id"]]
        rec = pending[0]
        assert rec.object_type == "tool_call"
        assert rec.object_id == PROBE_TOOL
        assert PROBE_TOOL in rec.description
        assert rec.payload["args_digest"]
        assert rec.payload["risk"], "payload 缺 risk，审批 UI 的风险列会显示为空"

    def test_重复调用复用同一张单不刷爆收件箱(self, approval_env, probe_tool):
        first = _call(a=1)
        second = _call(a=1)

        assert second["approval_id"] == first["approval_id"]
        assert len(_flow().list({"state": "pending_review"}, limit=50)) == 1


# ════════════════════════════════════════════════════════════
#  二、人工批准 → 恢复执行（单次有效）
# ════════════════════════════════════════════════════════════


class TestApproveAndResume:

    def test_批准后原样重试即放行且只放行一次(self, approval_env, probe_tool):
        blocked = _call(a=1)
        approval_id = blocked["approval_id"]

        # 人工批准（与审批路由同一条路：ApprovalFlow.approve，human 专属）
        _flow().approve(approval_id, actor="human", note="e2e 用例批准",
                        second_factor_ok=True)

        resumed = _call(a=1)
        assert resumed.get("ok") is True, f"批准后仍被拦: {resumed}"
        assert resumed["result"] == "handler-ran"
        assert probe_tool["n"] == 1

        # 单次有效：同一张批准不能反复放行
        again = _call(a=1)
        assert again["blocked"] is True
        assert again["error_code"] == "APPROVAL_REQUIRED"
        assert again["approval_id"] != approval_id, "复用了已消费的单子"
        assert probe_tool["n"] == 1, "已被消费的批准又放行了一次"

    def test_批准绑定参数不同参数不生效(self, approval_env, probe_tool):
        approval_id = _call(a=1)["approval_id"]
        _flow().approve(approval_id, actor="human", second_factor_ok=True)

        other = _call(a=2)                      # 参数不同 ⇒ 另一件事，须另行审批
        assert other["blocked"] is True
        assert other["error_code"] == "APPROVAL_REQUIRED"
        assert probe_tool["n"] == 0

    def test_批准跨进程重启仍有效(self, approval_env, probe_tool, monkeypatch):
        """批准落在 JSONL 里 ⇒ 清掉进程内缓存后（模拟重启）依然能恢复执行"""
        import agent.tool_approval as TA

        approval_id = _call(a=1)["approval_id"]
        _flow().approve(approval_id, actor="human", second_factor_ok=True)

        TA.reset_cache()                        # 模拟重启：内存缓存清空，文件还在
        G._reset_cache()

        resumed = _call(a=1)
        assert resumed.get("ok") is True, f"重启后批准丢失: {resumed}"
        assert probe_tool["n"] == 1


# ════════════════════════════════════════════════════════════
#  三、人工驳回
# ════════════════════════════════════════════════════════════


class TestReject:

    def test_驳回后不放行且把理由带给模型(self, approval_env, probe_tool):
        approval_id = _call(a=1)["approval_id"]
        _flow().reject(approval_id, actor="human", reason="该命令会删库，禁止")

        result = _call(a=1)
        assert result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REJECTED"
        assert "删库" in result["error"], "驳回理由没有传给模型，它只会反复重试"
        assert probe_tool["n"] == 0


# ════════════════════════════════════════════════════════════
#  四、真实边界工具 + 关闭开关即回滚
# ════════════════════════════════════════════════════════════


class TestRealBoundaryTools:

    def test_真实边界工具被拦且不执行(self, approval_env, probe_tool):
        """``shell_execute``（YAML risk=critical）走 YAML 侧的同一条路径"""
        result = G.check_tool_call("shell_execute", {"command": "echo should-not-run"})

        assert result is not None and result["blocked"] is True
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert result["approval_id"]
        assert probe_tool["n"] == 0

    def test_关闭开关即回滚为只告警(self, approval_env, probe_tool, monkeypatch):
        """``CP_TOOL_GATE_APPROVAL_ENFORCE=0`` ⇒ 一处环境变量完成回滚，不写任何文件"""
        monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "0")

        assert _call(a=1).get("ok") is True
        assert probe_tool["n"] == 1
        assert not approval_env["records"].exists() or \
            "pending" not in approval_env["records"].read_text(encoding="utf-8")


# ════════════════════════════════════════════════════════════
#  五、隐性安装路径也受同一道边界约束
# ════════════════════════════════════════════════════════════
#
# 背景：``ext_install``（plane=govern / effect=extend）是**自我改造**，显式调用要审批。
# 但工具发现服务有一条**隐性**安装路径：模型调了一个不存在的工具名 ⇒
# ``ToolDiscoveryService.on_tool_not_found`` ⇒ 市场搜索 ⇒ 直接 ``install_and_register``。
# 若那里不取闸门，模型打错一个工具名就能装进一个扩展，显式审批形同虚设。


class TestDiscoveryInstallIsGated:

    @staticmethod
    def _service(installs: list):
        from agent.tools.discovery_service import ToolDiscoveryService

        svc = ToolDiscoveryService(extension_manager=None, market=None)
        svc.search_market = lambda query, category=None: {
            "ok": True, "count": 1,
            "results": [{"ext_id": "ext-demo", "name": "Demo 扩展"}]}
        svc.install_and_register = lambda ext_id: installs.append(ext_id) or {"ok": True}
        return svc

    def test_自动安装未获批准时不安装且挂单(self, approval_env):
        installs: list = []
        svc = self._service(installs)

        result = svc.on_tool_not_found("some_missing_tool")

        assert installs == [], "隐式安装路径绕过了审批边界（模型打错工具名即可装扩展）"
        assert result["acquired"] is False
        assert result["error_code"] == "APPROVAL_REQUIRED"
        assert result["approval_id"], "未挂单 ⇒ 人工无从批准这条自动安装"

        pending = _flow().list({"state": "pending_review"}, limit=50)
        assert [r.object_id for r in pending] == ["ext_install"], \
            "自动安装的审批单没有落在 ext_install 上"

    def test_人工批准后自动安装才执行(self, approval_env):
        installs: list = []
        svc = self._service(installs)

        blocked = svc.on_tool_not_found("some_missing_tool")
        _flow().approve(blocked["approval_id"], actor="human",
                        second_factor_ok=True)

        again = svc.on_tool_not_found("some_missing_tool")

        assert installs == ["ext-demo"], f"批准后仍未安装: {again}"
        assert again["acquired"] is True


# ════════════════════════════════════════════════════════════
#  六、人在 UI 里**真的批得了**（收件箱投影）
# ════════════════════════════════════════════════════════════
#
# 背景：审批收件箱有一条后端硬规则「缺 undo_hint 不出现审批气泡（§7）」——
# ``agent/ui_panels/data.py::approval_inbox`` 用 ``governance_trace_fields()`` 的
# ``undo_hint``/``compensating_action`` 决定 ``bubble.visible``。工具审批单若不携带
# ``undo_hint``，就会**出现在数据里却不出气泡**，人在 UI 上根本点不到"批准" ⇒
# 闭环在 UI 侧断掉（API 侧却测得过，这正是最容易漏的一层）。


class TestApprovalVisibleInUiInbox:

    def test_工具审批项在收件箱里可见可批(self, approval_env, probe_tool):
        from agent.ui_panels.data import approval_inbox

        blocked = _call(a=1)
        view = approval_inbox(flow=_flow(), limit=50)

        items = [i for i in view["items"] if i["record_id"] == blocked["approval_id"]]
        assert len(items) == 1, f"审批单未出现在收件箱投影里: {view.get('count')}"
        item = items[0]

        assert item["object_type"] == "tool_call"
        assert item["risk"], "风险列为空，人工无法判断该不该批"
        assert item["bubble"]["visible"] is True, (
            "工具审批项没有 undo_hint ⇒ 收件箱不出气泡（§7 硬规则），人在 UI 里批不了")
        assert item["governance"]["undo_hint_status"] == "resolved"


# ════════════════════════════════════════════════════════════
#  七、两个"静默失效"陷阱
# ════════════════════════════════════════════════════════════


class TestSilentFailureTraps:

    def test_挂单很久之后批准仍然有效(self, approval_env, probe_tool):
        """TTL 从**裁决时刻**起算，不是创建时刻

        人工可能在夜里挂的单、第二天早上才批（排队/离线）。若按 ``created_at`` 计时，
        那张刚批的单会在批准瞬间就被判过期 ⇒ 人点了"批准"却什么也没发生。
        """
        import json as _json

        approval_id = _call(a=1)["approval_id"]

        # 把这张待办"改成 2 小时前挂的"（远超默认 TTL 900s）
        path = approval_env["records"]
        lines = path.read_text(encoding="utf-8").splitlines()
        patched = []
        for line in lines:
            rec = _json.loads(line)
            if rec.get("record_id") == approval_id:
                rec["created_at"] = "2026-01-01T00:00:00"
            patched.append(_json.dumps(rec, ensure_ascii=False))
        path.write_text("\n".join(patched) + "\n", encoding="utf-8")

        _flow().approve(approval_id, actor="human", second_factor_ok=True)

        resumed = _call(a=1)
        assert resumed.get("ok") is True, (
            f"挂单很久后批准被判过期（TTL 锚点错在创建时刻）: {resumed}")
        assert probe_tool["n"] == 1

    def test_审批流停用时给出可诊断的出路(self, approval_env, probe_tool, monkeypatch):
        """``APPROVAL_ENABLED=0`` × 审批边界开启 ⇒ 必须明说，而不是发一张等不到的单号

        审批流停用时 ``submit()`` 直接放行成 merged，收件箱不会有待办 ⇒ 若照常返回
        "已挂单、请人工确认"，模型会无限重试、人却看不到任何东西（死循环）。
        """
        monkeypatch.setenv("APPROVAL_ENABLED", "0")

        result = _call(a=1)

        assert result["blocked"] is True
        assert probe_tool["n"] == 0
        assert "审批流" in result["error"] and "停用" in result["error"] or \
            "未处于待审状态" in result["error"], \
            f"未诊断出审批流停用: {result['error']}"
        guidance = result.get("guidance", "")
        assert "APPROVAL_ENABLED" in guidance or "APPROVAL_ENABLED" in result["error"]
        assert "CP_TOOL_GATE_APPROVAL_ENFORCE=0" in guidance, \
            "没有告诉运维另一条出路（显式关闭审批边界）"


# ════════════════════════════════════════════════════════════
#  八、系统超时 ≠ 人工否决（陈旧待办的清理语义）
# ════════════════════════════════════════════════════════════
#
# 背景：没人理的待办必须能清出去（否则收件箱无限堆积），但"清出去"不能等于"被人否决"：
#   - 人工驳回  ⇒ 该请求被否决，模型**不应再重试**；
#   - 系统超时  ⇒ 只是没人处理，模型**应当重新发起**（重新挂单）。
# 二者都落在审批流的 ``rejected`` 状态（复用状态机与 §6.1「超时 Deny=2」计数），
# 靠 ``TIMEOUT_DENY_REASON_PREFIX`` 这一**结构标识**区分。


class TestSystemExpiryIsNotHumanRejection:

    @staticmethod
    def _expire_now():
        """在当前隔离库里把 ``tool_call`` 待办立即判为系统超时"""
        from agent.skills_mgmt.approval import ApprovalFlow
        return ApprovalFlow().expire_pending(
            older_than_seconds=0, object_type="tool_call", note="e2e 用例触发超时")

    def test_系统超时后重新挂单而不是报已否决(self, approval_env, probe_tool):
        first = _call(a=1)
        expired = self._expire_now()
        assert [r.record_id for r in expired] == [first["approval_id"]]

        second = _call(a=1)

        assert second["error_code"] == "APPROVAL_REQUIRED", (
            f"系统超时被当成了人工否决（模型会被误导为'别再重试'）: {second}")
        assert second["approval_id"], "系统超时后没有重新挂单，人工再也看不到待办"
        assert "别再重试" not in second["error"]
        assert probe_tool["n"] == 0
        # 收件箱里恰好一条待办（旧的已被清出）
        pending = _flow().list({"state": "pending_review"}, limit=50)
        assert [r.record_id for r in pending] == [second["approval_id"]]

    def test_人工驳回仍然阻断重试(self, approval_env, probe_tool):
        """回归：人工驳回的语义**不受本次改动影响**（该说"别再重试"就说）"""
        approval_id = _call(a=1)["approval_id"]
        _flow().reject(approval_id, actor="human", reason="越权命令")

        result = _call(a=1)
        assert result["error_code"] == "APPROVAL_REJECTED"
        assert "越权命令" in result["error"]

    def test_陈旧待办被惰性清理并换成新单(self, approval_env, probe_tool):
        """挂了超过 TTL 的待办：下次请求时被判系统超时清出，并挂一张新单"""
        import json as _json

        first = _call(a=1)["approval_id"]
        path = approval_env["records"]
        patched = []
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = _json.loads(line)
            if rec.get("record_id") == first:
                rec["created_at"] = "2026-01-01T00:00:00"      # 远超 TTL(900s)
            patched.append(_json.dumps(rec, ensure_ascii=False))
        path.write_text("\n".join(patched) + "\n", encoding="utf-8")

        second = _call(a=1)

        pending = _flow().list({"state": "pending_review"}, limit=50)
        assert [r.record_id for r in pending] == [second["approval_id"]], \
            "陈旧待办没有被清出，收件箱里会同时留着过期的旧单"
        old = [r for r in _flow().list({}, limit=200) if r.record_id == first]
        assert old and old[0].state == "rejected"
        from agent.skills_mgmt.approval import is_timeout_deny
        assert is_timeout_deny(old[0]), "陈旧待办的判定没有打上系统超时标识"

    def test_只清理工具待办不误伤其他对象类型(self, approval_env, probe_tool):
        """``expire_pending`` 的作用域：技能/提示词提案的待办时长不同，不能被工具 TTL 误伤"""
        import json as _json

        from agent.skills_mgmt.approval import ApprovalFlow

        other = ApprovalFlow().submit(
            "skill", "some_skill", action="submit", description="技能提案",
            payload={"note": "合理待办时长是 86400s"}, actor="auto", trigger="api")
        assert other.state == "pending_review"

        # 把这条技能待办也做成"很旧"
        path = approval_env["records"]
        patched = []
        for line in path.read_text(encoding="utf-8").splitlines():
            rec = _json.loads(line)
            if rec.get("record_id") == other.record_id:
                rec["created_at"] = "2026-01-01T00:00:00"
            patched.append(_json.dumps(rec, ensure_ascii=False))
        path.write_text("\n".join(patched) + "\n", encoding="utf-8")

        _call(a=1)      # 触发工具侧惰性清理

        skill_pending = [r for r in _flow().list({"state": "pending_review"}, limit=200)
                         if r.object_type == "skill"]
        assert skill_pending, "工具侧的清理把技能提案的待办一起误伤了"
