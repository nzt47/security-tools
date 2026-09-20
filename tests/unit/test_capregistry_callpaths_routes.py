"""调用路径普查 + 非 LLM 入口路由（TASK-05 E1b / E3 / E6 / E7）

`E1b` 的硬要求（`TASK-05` §5）：

> - 存在 `scripts/audit_call_paths.py`，输出**全部**能触发能力执行的路径及其
>   "是否过闸门 / 是否带身份"**以及"当前是否可达"**
> - **4 处已定位的直调全部处置** …
> - `--check` 模式在发现**未登记的直调**时**非零退出**（**必须实测这个负例**）

本文件把"负例"做成**可自动执行的用例**：直接调 `audit_call_paths.main()`，
不用起子进程（避免 `TASK-00` §0.2d 第 4 类的管道坑）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agent.capregistry.call_sites import (  # noqa: E402
    DEAD_MODULES, EXEMPT_CALL_SITES, VIOLATION_SCOPE_PREFIXES, anchor_key,
    reachability_of)


def _load_audit_module():
    """按路径加载 `scripts/audit_call_paths.py`（它不在包内，不能直接 import）"""
    path = _ROOT / "scripts" / "audit_call_paths.py"
    spec = importlib.util.spec_from_file_location("_audit_call_paths_under_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def audit():
    return _load_audit_module()


@pytest.fixture(scope="module")
def findings(audit):
    return audit.scan()


# ════════════════════════════════════════════════════════════
#  清单覆盖与分类
# ════════════════════════════════════════════════════════════


class TestCallPathInventory:
    def test_清单非空且四类都在(self, findings):
        kinds = {f.path_kind for f in findings}
        assert "funnel" in kinds, "必须能看见收口路径"
        assert "direct" in kinds or "remote_primitive" in kinds, \
            "必须能看见直调路径"
        assert len(findings) > 0

    def test_每条都带四要素(self, findings, audit):
        """`TASK-05` E1b 要求每条输出：是否过闸门 / 是否带身份 / 是否可达 / 是否直调"""
        for f in findings:
            d = f.to_dict()
            for key in ("via_registry", "via_gate", "has_identity", "reachable",
                        "reachability", "path_kind", "trigger", "file", "symbol",
                        "anchor"):
                assert key in d, f"{f.file}:{f.lineno} 缺少字段 {key}"
            assert str(f.reachability).strip(), "可达性必须给出依据而不是空串"

    def test_四处已知缺口全部被看见(self, findings):
        """★ E1b：`TASK-05` §2.3c 的 4 处已定位直调**必须在清单里**

        锚点用 `路径::符号名`（**不用行号** —— 行号会漂移，`ci.yml:562` 就是前车之鉴）。
        """
        anchors = {f.anchor for f in findings}
        required = [
            # ① CI 面的知识库审计 CLI（`ci.yml` 的 knowledge-audit-smoke job 触发）
            "agent/knowledge/__main__.py::cmd_audit",
            # ② 后台任务执行器（**实测更正：过闸门但缺身份**）
            "agent/async_executor.py::_run_task",
            # ③ MCP 服务端 tools/call（**实测更正：过闸门，协议层无身份**）
            "mcp_services/yunshu_mcp_server.py::_handle_tools_call",
            # ④ 技能 MCP 适配器的远程直调（潜在风险，当前不可达）
            "agent/skills_mgmt/mcp_adapter.py::_call_tool",
        ]
        missing = [a for a in required if a not in anchors]
        assert missing == [], f"清单漏掉了已知缺口：{missing}"

    def test_当前不可达的直调与可达的直调被区分(self, findings):
        """★ `TASK-05` §2.3c 明令：**不要把"当前不可达"算作现实缺口**

        可达性判据是**实测**的（`mcp` SDK 是否可导入 / 死模块是否有 importer），
        不是硬编码 —— 硬编码会在 SDK 装上后静默过期。
        """
        p3 = [f for f in findings if f.path_kind == "remote_primitive"]
        mcp_adapter = [f for f in p3
                       if f.file == "agent/skills_mgmt/mcp_adapter.py"]
        assert mcp_adapter, "mcp_adapter 的远程直调必须在清单里"
        assert mcp_adapter[0].reachable is False, \
            "官方 mcp SDK 未安装 ⇒ 该路径当前不可达（实测判据）"
        assert "装上" in mcp_adapter[0].reachability, \
            "可达性说明必须写明'一旦装上即生效'"

    def test_死模块被标为不可达(self, findings):
        """死模块的判据是"**生产范围内零调用方**"，不是字面上的"零 importer"

        （`agent/mcp_executor.py` 实测被 `scripts/check_mcp_log_level.py` import，
        故"零 importer"会得出错误结论 —— 见 `_reachability` 的口径修正注释。）
        """
        seen = False
        for f in findings:
            if f.file in DEAD_MODULES:
                seen = True
                assert f.reachable is False, f"{f.file} 是死模块，应判不可达"
                assert "零调用方" in f.reachability, \
                    f"可达性依据必须写明'生产代码零调用方'，实际：{f.reachability}"
        assert seen, "死模块的调用点应当在清单里被看见"


# ════════════════════════════════════════════════════════════
#  `--check`：**必须实测负例**
# ════════════════════════════════════════════════════════════


class TestCheckMode:
    def test_正例_当前仓库通过_check(self, audit):
        rc = audit.main(["--check"])
        assert rc == 0, "当前仓库应当没有未登记的直调（否则就是本任务没做完）"

    def test_负例_未登记的直调_非零退出(self, audit, monkeypatch):
        """★★ E1b 的硬要求：**实测这个负例**

        做法：从 `EXEMPT_CALL_SITES` 里删掉**一条真实的直调登记**
        （`agent/skills_mgmt/mcp_adapter.py::_build` —— 它确实被 AST 扫描器
        以 `remote_primitive` 命中且在硬失败范围内），等价于"这条直调没登记"。
        `--check` 必须**非零退出**。

        【为什么不删 `cmd_audit` 那条】`cmd_audit` 是**经符号存在性**兜底登记的
        （AST 启发式抓不到"经 CLI 间接触发"），删掉它只会让清单少一条、
        **不会**产生"未登记直调" ⇒ 那样测不到退出码逻辑。
        负例必须打在**真的会被扫出来的**直调上。

        【2026-09-21 修一处锚点漂移 —— 本负例曾**静默失效**】
        原受害条目是 `agent/skills_mgmt/mcp_adapter.py::_call_tool`。
        TASK-08 给 MCP 调用加超时/重试预算时，把 SDK 调用路径重构成了
        `_build()` 内层闭包（`mcp_adapter.py:372`）⇒ **`_call_tool` 这个符号不再存在**。
        于是本用例变成"删掉一个**本就不存在的**条目" ⇒ 未登记数仍为 0 ⇒
        **负例测不出非零退出，静默失效**（实测 `assert 0 == 1`）。
        改用当前**真实存在且会被扫到**的 `_build` 作为受害条目。
        ⇒ **教训：负例必须锚在"扫描器当前真能命中的符号"上；重构会让负例静默失效。**
        """
        victim = "agent/skills_mgmt/mcp_adapter.py::_build"
        assert victim in EXEMPT_CALL_SITES, "测试前提：该例外必须存在"
        patched = {k: v for k, v in EXEMPT_CALL_SITES.items() if k != victim}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        rc = audit.main(["--check"])
        assert rc == 1, "删掉例外登记后 --check 必须非零退出"

    def test_负例_白名单腐化_非零退出(self, audit, monkeypatch):
        """★ 防"什么都放行"：登记了但**代码里找不到对应符号** ⇒ 非零退出

        这条不变量是 `EXEMPT_CALL_SITES` 不腐化成空转声明的结构保证。
        """
        patched = dict(EXEMPT_CALL_SITES)
        patched["agent/knowledge/__main__.py::definitely_no_such_symbol"] = {
            "reason": "故意造一个腐化条目", "identity": "x", "audit": "x"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        rc = audit.main(["--check"])
        assert rc == 1, "白名单腐化必须非零退出"

    def test_负例_虚假登记不产生额外例外(self, audit, monkeypatch):
        """把**真实存在**的直调登记成例外 ⇒ `--check` 通过（证明例外机制真的在生效）"""
        patched = dict(EXEMPT_CALL_SITES)
        patched["agent/knowledge/tools.py::kb_lint"] = {
            "reason": "测试用：显式登记", "identity": "llm", "audit": "有"}
        monkeypatch.setattr(audit, "EXEMPT_CALL_SITES", patched)
        assert audit.main(["--check"]) == 0


# ════════════════════════════════════════════════════════════
#  例外表本身的纪律
# ════════════════════════════════════════════════════════════


class TestExemptTableDiscipline:
    def test_每条例外都有理由_身份_审计说明(self):
        """`TASK-05` §5：例外必须**逐点、带理由、带身份**，不得宽泛放行"""
        for anchor, meta in EXEMPT_CALL_SITES.items():
            assert "::" in anchor, f"{anchor} 不是 `路径::符号名` 形态"
            for field in ("reason", "identity", "audit"):
                val = str(meta.get(field) or "").strip()
                assert val, f"{anchor} 的 {field} 为空（例外必须逐点说明）"
            assert not anchor.endswith("/*"), f"{anchor} 是宽泛模式（禁止）"
            assert "*" not in anchor, f"{anchor} 含通配符（禁止宽泛放行）"

    def test_理由不是敷衍话术(self):
        """理由必须**具体**：含"为什么"，而不是"暂不处理"这类空话"""
        vague = ("暂时", "以后再", "TODO", "先这样", "无所谓", "都行")
        for anchor, meta in EXEMPT_CALL_SITES.items():
            reason = str(meta.get("reason") or "")
            assert len(reason) >= 30, f"{anchor} 的理由太短（{len(reason)} 字）"
            assert not any(v in reason for v in vague), \
                f"{anchor} 的理由含敷衍话术：{reason[:60]}"

    def test_硬失败范围是被显式声明的(self):
        assert VIOLATION_SCOPE_PREFIXES, "违规范围必须显式声明，不能含糊"
        assert all(p.endswith("/") for p in VIOLATION_SCOPE_PREFIXES)
        assert "agent/" in VIOLATION_SCOPE_PREFIXES

    def test_anchor_key_归一化(self):
        assert anchor_key("agent\\x.py", "A::b") == "agent/x.py::b"
        assert anchor_key("./agent/x.py", "b") == "agent/x.py::b"
        assert anchor_key("agent/x.py", "") == "agent/x.py::<module>"

    def test_reachability_of_对远程原语实测_sdk(self):
        reach, why = reachability_of("x.py", "y", "session.call_tool",
                                    "remote_primitive")
        try:
            import mcp  # noqa: F401
            assert reach is True
        except ImportError:
            assert reach is False
        assert why.strip()


# ════════════════════════════════════════════════════════════
#  非 LLM 入口路由（E1 / E6 / E7 的 HTTP 侧）
# ════════════════════════════════════════════════════════════


@pytest.fixture
def cap_client(monkeypatch):
    """只挂能力层路由的 Flask 测试客户端（真实 WSGI 请求）"""
    from flask import Flask
    from agent.server_routes.routes_capabilities import register_routes
    app = Flask("caproutes_test")
    register_routes(app, lambda: None)
    return app.test_client()


class TestCapabilityRoutes:
    def test_开关关闭时端点不存在(self, monkeypatch, audit):
        """回滚方案：`CP_CAPABILITY_API_ENABLED=0` ⇒ `/capabilities/*` 全部 404"""
        from flask import Flask
        from agent.server_routes.routes_capabilities import register_routes
        monkeypatch.setenv("CP_CAPABILITY_API_ENABLED", "0")
        app = Flask("caproutes_off")
        register_routes(app, lambda: None)
        c = app.test_client()
        assert c.get("/capabilities/tools").status_code == 404
        assert c.post("/capabilities/invoke", json={"name": "x"}).status_code == 404
        assert c.get("/capabilities/health").status_code == 404

    def test_get_tools_返回全量清单与裁剪字段(self, cap_client):
        r = cap_client.get("/capabilities/tools?limit=2000")
        assert r.status_code == 200
        body = r.get_json()
        assert body["status"] == "ok"
        assert body["data"]["total"] == 114
        assert body["data"]["returned"] == 114
        item = body["data"]["items"][0]
        for key in ("name", "kind", "location", "owner", "callable_by",
                    "impl_status", "capability_id", "tenant_id"):
            assert key in item

    def test_默认分页不静默截断(self, cap_client):
        """默认分页 + `truncated` 明示（10k 压测逼出来的设计）"""
        body = cap_client.get("/capabilities/tools").get_json()
        assert body["data"]["limit"] == 500
        assert body["data"]["limit_source"] == "default_page"
        assert "truncated" in body["data"]

    def test_模型能力裁剪(self, cap_client):
        body = cap_client.get("/capabilities/tools?model=none").get_json()
        assert body["data"]["model_capability"]["supports_tool_calling"] is False
        assert body["data"]["returned"] == 0
        assert body["data"]["total"] == 114
        body2 = cap_client.get("/capabilities/tools?model=deepseek-chat").get_json()
        assert body2["data"]["returned"] > 0

    def test_调度双工具面可区分(self, cap_client):
        """★ 交付物 #15：`/capabilities/tools` 能看出哪个 `schedule_task` 未实现"""
        body = cap_client.get("/capabilities/tools?q=schedule_task&limit=2000").get_json()
        items = {i["name"]: i for i in body["data"]["items"]}
        assert "schedule_task" in items
        assert items["schedule_task"]["impl_status"] == "not_implemented"
        assert items["schedule_task"]["impl_status_reason"], "必须附上证据"

    def test_invoke_缺参数归_validation_error(self, cap_client):
        assert cap_client.post("/capabilities/invoke", json={}).status_code == 400
        assert cap_client.post("/capabilities/invoke",
                               json={"name": "x", "args": []}).status_code == 400

    def test_invoke_未知能力归_not_found(self, cap_client):
        r = cap_client.post("/capabilities/invoke", json={"name": "__nope__"})
        assert r.status_code == 404
        assert r.get_json()["code"] == "not_found"

    def test_describe_与_health(self, cap_client):
        r = cap_client.get("/capabilities/json_query")
        assert r.status_code == 200
        assert r.get_json()["data"]["name"] == "json_query"
        assert cap_client.get("/capabilities/__nope__").status_code == 404
        h = cap_client.get("/capabilities/health")
        assert h.status_code == 200
        assert h.get_json()["data"]["registry"]["total"] == 114

    def test_异常不外泄为_html_或原文(self, cap_client, monkeypatch):
        """★ 铁律：任何异常都不能把 HTML / 原文喂出去

        做法：让真实 Registry 的 `list_envelope` 抛异常 —— 路由的 catch-all
        必须把它折叠成 `internal_error` + 固定描述，且**不得**回显原文。
        （不能 patch 模块级名字：`_registry()` 里的导入在 `register_routes`
        时就绑成了闭包，patch 模块属性无效 —— 实测踩到 AttributeError。）
        """
        from agent.capregistry.view import CapabilityRegistry

        def _boom(*_a: Any, **_kw: Any) -> Any:
            raise RuntimeError("C:\\Users\\secret\\path 内部错误 <html>502</html>")

        monkeypatch.setattr(CapabilityRegistry, "list_envelope", _boom)
        r = cap_client.get("/capabilities/tools?limit=2000")
        assert r.status_code == 500
        body = r.get_json()
        blob = json.dumps(body, ensure_ascii=False)
        assert "C:\\Users" not in blob
        assert "secret" not in blob
        assert "<html>" not in blob
        assert body["code"] == "internal_error"
        assert body["error"]["message"] == \
            "内部错误，已脱敏；请查看服务端日志定位"
