"""逐工具「需确认」豁免开关（agent/tool_exemptions.py）单元测试

【为什么必须有】这是"人在界面上给某个工具松绑"的唯一后端入口。它必须同时守住四条：
  1. 只列**本来就要人确认**的工具，且如实标注哪些**放不了**（描述符硬要求那批）；
  2. 增删真的改到 `CP_TOOL_CONFIRM_LEVEL_EXEMPT`，并被 `agent/tool_gate` 立刻看到（热生效）；
  3. **agent 自己不能松绑**：`settings.change` 在 Actor 矩阵里只有 human 放行；
  4. L0（本来就免确认）工具拒绝写入 —— 不制造"看着生效、其实什么都不做"的记录。

【环境隔离】覆盖层路径 monkeypatch 到 tmp（与 test_settings_*.py 同款），
绝不写生产 `data/ui_settings.json`。
"""
from __future__ import annotations

import json
import os

import pytest

import agent.tool_gate as G
from agent import tool_exemptions as TE
from agent.settings.overrides import reset_override_store
from agent.settings.service import reset_settings_service


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """覆盖层 / 事件 / env 快照全部隔离；单例复位（否则上个用例的 store 会串味）"""
@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """覆盖层 / 事件 / env 快照全部隔离；单例复位（否则上个用例的 store 会串味）"""
    snapshot = dict(os.environ)
    monkeypatch.setenv("CP_UI_SETTINGS_PATH", str(tmp_path / "ui_settings.json"))
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    # conftest 的会话基线把审批边界设为 0（避免无关用例被治理网拦）⇒
    # 本文件测的正是"要不要人确认"，必须显式打开它，否则断言全部退化为"本来就放行"。
    monkeypatch.setenv(G.APPROVAL_ENFORCE_ENV, "1")
    monkeypatch.delenv(TE.EXEMPT_SETTING_KEY, raising=False)   # 从"默认空"起步
    # 【为什么必须自备描述符台账】data/descriptors.json 是运行期台账（.gitignore），
    # CI 上不存在 ⇒ 直接依赖它会让"哪些工具放不了"这条用例在 CI 上恒失败，
    # 而开发者机恒通过。这里给一份**测试自备**的最小台账（与
    # test_confirm_level.py::real_descriptor_boundary 同一处置）。
    desc = tmp_path / "descriptors.json"
    desc.write_text(json.dumps({
        "schema_version": 1,
        "descriptors": {
            "cp.builtin.shell_execute": {
                "meta": {"id": "cp.builtin.shell_execute"},
                "capability": {"name": "shell_execute"},
                "trust": {"requires_approval": True, "risk_level": "critical"}},
            "cp.builtin.run_sandbox": {
                "meta": {"id": "cp.builtin.run_sandbox"},
                "capability": {"name": "run_sandbox"},
                "trust": {"requires_approval": True, "risk_level": "critical"}},
        },
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(G, "DESCRIPTORS_PATH", str(desc))
    reset_override_store()
    reset_settings_service()
    G._reset_cache()
    yield tmp_path
    os.environ.clear()
    os.environ.update(snapshot)
    reset_override_store()
    reset_settings_service()
    G._reset_cache()


DELEGATE_ARGS = {
    "goal": "计算 1..100 之和", "constraints": ["只读"], "prior_artifacts": [],
    "prohibitions": ["不得修改或删除任何文件"], "artifact_format": "纯文本一行",
    "budget_tokens": 1000, "timeout_seconds": 60, "callback_url": "internal://x",
}


class TestCandidates:
    def test_只列需要确认的工具_L0_不出现(self):
        items = {i["tool"]: i for i in TE.candidates()}

        assert "delegate" in items           # L1
        assert "write_file" in items         # L2
        assert "read_file" not in items      # L0 本来就免确认
        assert items["delegate"]["exempt"] is False

    def test_描述符硬要求的工具标成放不了并给出原因(self):
        items = {i["tool"]: i for i in TE.candidates()}

        assert items["shell_execute"]["exemptable"] is False
        assert "描述符" in items["shell_execute"]["blocked_reason"]
        assert items["run_sandbox"]["exemptable"] is False
        # 反面：普通工具必须可点，否则整个开关就是摆设
        assert items["delegate"]["exemptable"] is True
        assert items["delegate"]["blocked_reason"] == ""

    def test_默认来源是代码默认值且名单为空(self):
        view = TE.view()

        assert view["exempt"] == []
        assert view["items"]


class TestSetExempt:
    def test_加入豁免后门禁立刻放行(self):
        """热生效：改完不需要重启，`check_tool_call` 下一次就读到新名单"""
        blocked = G.check_tool_call("delegate", DELEGATE_ARGS)
        assert blocked is not None and blocked["blocked"] is True

        result = TE.set_exempt("delegate", True, actor="human-1",
                               actor_type="human", reason="编排动作高频")

        assert result["ok"] is True and result["changed"] is True
        assert result["exempt"] == ["delegate"]
        assert G.check_tool_call("delegate", DELEGATE_ARGS) is None

    def test_移出豁免后门禁重新要求确认(self):
        TE.set_exempt("delegate", True, actor="human-1", actor_type="human")
        assert G.check_tool_call("delegate", DELEGATE_ARGS) is None

        result = TE.set_exempt("delegate", False, actor="human-1", actor_type="human")

        assert result["ok"] is True and result["exempt"] == []
        assert G.check_tool_call("delegate", DELEGATE_ARGS) is not None

    def test_重复加入不重复写(self):
        TE.set_exempt("delegate", True, actor="human-1", actor_type="human")

        again = TE.set_exempt("delegate", True, actor="human-1", actor_type="human")

        assert again["ok"] is True and again["changed"] is False

    def test_描述符硬要求的工具拒绝写入(self):
        result = TE.set_exempt("shell_execute", True, actor="human-1", actor_type="human")

        assert result["ok"] is False and result["code"] == "descriptor_gated"
        assert TE.exempt_names() == []

    def test_L0_工具拒绝写入(self):
        result = TE.set_exempt("read_file", True, actor="human-1", actor_type="human")

        assert result["ok"] is False and result["code"] == "not_required"

    def test_未登记工具拒绝写入(self):
        result = TE.set_exempt("not_a_tool_xyz", True, actor="human-1", actor_type="human")

        assert result["ok"] is False and result["code"] == "unknown_tool"

    def test_agent_不能给自己松绑(self):
        """`settings.change` 在 Actor 矩阵里只有 human 放行（auto/sub_agent/SA 一律拒）

        这条是**安全边界**而不是流程细节：若 agent 能改，云枢就可以自己把审批全关掉，
        整套治理面形同虚设。
        """
        for actor_type in ("auto", "sub_agent", "service_account"):
            result = TE.set_exempt("delegate", True, actor="agent-x",
                                   actor_type=actor_type)
            assert result["ok"] is False, actor_type
            assert result["code"] == "settings_denied", (actor_type, result)
        assert TE.exempt_names() == []

class TestRoutes:
    """端点级：URL 真的挂在 Flask app 上、请求体校验与授权口径如实回执

    【为什么单测模块之外还要测端点】模块函数正确 ≠ 端点在线上可用。
    本仓库有过"手搓 Flask 全绿而线上 404"的教训（见 test_*_routes.py 的说明），
    故 URL / 方法 / 错误码这三件事必须在真实注册路径上验一次。
    """

    @pytest.fixture()
    def client(self):
        from flask import Flask
        from agent.server_routes import routes_settings as RS
        app = Flask(__name__)
        RS.register_routes(app, None)
        app.config["TESTING"] = True
        return app.test_client(), RS

    @staticmethod
    def _force_actor(monkeypatch, module, *, actor_type: str = "human") -> None:
        monkeypatch.setattr(module, "_identity_fields", lambda: {
            "actor": "unit-actor", "actor_type": actor_type,
            "identity_source": "unit", "session_id": "sess-1"})

    def test_读取候选清单(self, client):
        http, _ = client

        response = http.get("/api/cp/tool-exemptions")
        body = response.get_json()

        assert response.status_code == 200, body
        assert body["ok"] is True and body["editable"] is True
        tools = {i["tool"]: i for i in body["items"]}
        assert tools["delegate"]["exemptable"] is True
        assert tools["shell_execute"]["exemptable"] is False

    def test_exempt_必须是布尔(self, client, monkeypatch):
        http, _ = client
        self._force_actor(monkeypatch, _)

        response = http.post("/api/cp/tool-exemptions",
                             json={"tool": "delegate", "exempt": "yes"})

        assert response.status_code == 400
        assert response.get_json()["code"] == "invalid_exempt"

    def test_人可以开_关(self, client, monkeypatch):
        http, module = client
        self._force_actor(monkeypatch, module)

        on = http.post("/api/cp/tool-exemptions",
                       json={"tool": "delegate", "exempt": True, "reason": "编排高频"})
        assert on.status_code == 200, on.get_json()
        assert on.get_json()["exempt"] == ["delegate"]
        assert G.check_tool_call("delegate", DELEGATE_ARGS) is None

        off = http.post("/api/cp/tool-exemptions",
                        json={"tool": "delegate", "exempt": False})
        assert off.status_code == 200, off.get_json()
        assert off.get_json()["exempt"] == []
        assert G.check_tool_call("delegate", DELEGATE_ARGS) is not None

    def test_agent_身份被拒并如实回执(self, client, monkeypatch):
        """端点不做自己的授权判定，而是**如实转发**服务层的 settings_denied"""
        http, module = client
        self._force_actor(monkeypatch, module, actor_type="auto")

        response = http.post("/api/cp/tool-exemptions",
                             json={"tool": "delegate", "exempt": True})

        assert response.status_code == 403
        assert response.get_json()["code"] == "settings_denied"
        assert TE.exempt_names() == []

    def test_描述符工具回执_409_并给出原因(self, client, monkeypatch):
        http, module = client
        self._force_actor(monkeypatch, module)

        response = http.post("/api/cp/tool-exemptions",
                             json={"tool": "shell_execute", "exempt": True})

        assert response.status_code == 409
        body = response.get_json()
        assert body["code"] == "descriptor_gated"
        assert "描述符" in body["message"]

