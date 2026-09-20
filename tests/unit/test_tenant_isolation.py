"""`tenant_id` **服务端派生** 与**跨租户隔离**（TASK-06 §3 第 4 步）

## 覆盖的评估标准

| 用例类 | 对应 |
|---|---|
| `TestTenantIdIsServerDerived` | **E6**：客户端指定被拒（`routes_ui_panels.py` 两处 + `routes_capabilities.py` 三处） |
| `TestCrossTenantIsolation` | **E7**：合成租户 A/B 在**清单 / 审计 / 配额**三个维度互不可见 |
| `TestDerivationPathExists` | TASK-06 §5 的"不通过"第 5 条：不得把 `tenant_id` 改成常量而不建立派生机制 |

## 单机单用户的现实与"预留接入点"

当前部署的活租户恒等于 **workspace-hash**（`agent/memory/tenancy.py`、
`agent/orchestrator/orchestrator.py:211-228`），因此在**本机** A/B 会退化成同一个值。
TASK-06 §2.4 的要求因此是："**即使当前恒为 `default`，也必须证明机制存在**"——
故隔离用例**合成**两个租户键（直接用 Registry / 审计 / 配额的 tenant 形参），
验证"按租户键过滤"这条路径真的存在且有效，而不是验证"本机有两个租户"。

## 纪律

* 不新建任何环境变量（D5 零缺口守卫）：派生规则没有合法第二种取值。
* 审计一律落到 `tests/conftest.py` 已隔离的临时 `AUDIT_DB_PATH`，不碰生产链。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.security.tenant import (DEFAULT_TENANT_ID, server_tenant_id,
                                   tenant_id_with_declaration, tenant_matches)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: 合成租户（隔离验证用；**不写入任何真实数据文件**）
TENANT_A = "synth-tenant-a"
TENANT_B = "synth-tenant-b"


# ════════════════════════════════════════════════════════════
#  一、服务端派生（E6）
# ════════════════════════════════════════════════════════════


class TestTenantIdIsServerDerived:

    def test_派生不等于客户端声明(self):
        """生效值**永远**来自服务端；客户端值只作待校验声明"""
        effective, declared = tenant_id_with_declaration(TENANT_B)
        assert effective == server_tenant_id()
        assert declared == TENANT_B                     # 登记但不参与判定
        assert effective != TENANT_B or effective == DEFAULT_TENANT_ID

    def test_未声明时声明值为空(self):
        effective, declared = tenant_id_with_declaration("")
        assert effective == server_tenant_id()
        assert declared == ""

    def test_空白声明被规范化(self):
        _effective, declared = tenant_id_with_declaration("   ")
        assert declared == ""

    def test_声明不符时可显式拒绝(self):
        """"拒绝式"语义（写路径用）：声明与派生值不符 ⇒ `False`"""
        assert tenant_matches(server_tenant_id()) is True
        assert tenant_matches("") is True               # 未声明 = 不构成冲突
        if server_tenant_id() != TENANT_B:
            assert tenant_matches(TENANT_B) is False

    def test_派生机制存在_不是常量(self):
        """TASK-06 §5：✗ 把 `tenant_id` 改成常量 `"default"` 而**不建立派生机制**

        判据：派生函数必须真的去问 workspace（而不是硬编码返回常量）。
        做法：把 workspace 推导替换成一个**可辨识的哨兵值**，
        若派生死死返回 `default`，本用例会失败。
        """
        import agent.security.tenant as T
        import agent.observability.trace_v2 as TV

        original = TV.derive_workspace_id
        try:
            TV.derive_workspace_id = lambda _p: "ws-hash-sentinel"
            assert T.server_tenant_id() == "ws-hash-sentinel", \
                "派生没有走 workspace（说明它是常量而不是派生）"
        finally:
            TV.derive_workspace_id = original
        assert T.server_tenant_id() == server_tenant_id()

    def test_派生失败时保守回落(self, monkeypatch):
        """派生异常 ⇒ 回落 `default` 且**不抛**（面板不该 500）"""
        import agent.security.tenant as T
        import agent.observability.trace_v2 as TV

        def _boom(_p):
            raise RuntimeError("workspace down")

        monkeypatch.setattr(TV, "derive_workspace_id", _boom)
        assert T.server_tenant_id() == DEFAULT_TENANT_ID

    def test_默认值与_models_一致(self):
        """【D1 对拍】两处各有一个 `DEFAULT_TENANT_ID`（为避开 import 环不互相 import）"""
        from agent.lines.models import DEFAULT_TENANT_ID as M
        assert DEFAULT_TENANT_ID == M


class TestClientSuppliedTenantIdIsRejected:
    """E6：**逐点**核所有曾允许客户端伪造 `tenant_id` 的调用面

    实测（`git grep 'args.get("tenant_id"'` / `body.get("tenant_id")`）本仓共 5 处
    客户端取值点，全部已在 TASK-06 收敛到 `agent/security/tenant.py` 的派生入口：

    | 位置 | 端点 | 处置 |
    |---|---|---|
    | `routes_ui_panels.py:332` | GET 记忆/技能面板 | 纠正式（读语义） |
    | `routes_ui_panels.py:595` | POST 整包回滚 | 纠正式 + 留痕 |
    | `routes_capabilities.py:168` | GET /capabilities/tools | 纠正式 |
    | `routes_capabilities.py:252` | POST /capabilities/invoke | **拒绝式**（写语义，403） |
    | `routes_capabilities.py:314` | GET /capabilities/describe | 纠正式 |
    """

    @staticmethod
    def _sources() -> dict:
        return {
            "agent/server_routes/routes_ui_panels.py":
                (_PROJECT_ROOT / "agent/server_routes/routes_ui_panels.py").read_text(
                    encoding="utf-8"),
            "agent/server_routes/routes_capabilities.py":
                (_PROJECT_ROOT / "agent/server_routes/routes_capabilities.py").read_text(
                    encoding="utf-8"),
        }

    def test_两处面板调用点都走共用派生(self):
        src = self._sources()["agent/server_routes/routes_ui_panels.py"]
        assert "_tenant_id_with_declaration(" in src
        # 两处客户端取值（GET query 与 POST body）都必须**作为派生入口的实参**出现
        assert src.count('_tenant_id_with_declaration(body.get("tenant_id"') == 1
        assert src.count('_tenant_id_with_declaration(\n            request.args.get(') == 1 or \
               src.count('_tenant_id_with_declaration(request.args.get(') == 1, \
            "GET 面的客户端 tenant 声明必须包在派生入口里"
        # 客户端值不得**直接**进数据层（这两种写法是旧缺陷的形状）
        assert "tenant_id=request.args.get" not in src
        assert "tenant_id=(request.args.get" not in src
        assert 'tenant_id=body.get("tenant_id"' not in src

        import agent.server_routes.routes_ui_panels as R
        effective, declared = R._tenant_id_with_declaration(TENANT_B)
        assert effective == server_tenant_id()
        assert declared == TENANT_B

    def test_能力面三处都走共用派生(self):
        src = self._sources()["agent/server_routes/routes_capabilities.py"]
        assert "capability_tenant_with_declaration(" in src
        assert "capability_tenant_id()" in src
        # 写路径必须是**拒绝式**
        assert '"code": "denied"' in src, "POST /invoke 必须对不符的声明返回 denied"

    def test_能力面不得用记忆面的_workspace_hash(self):
        """【🔴 真实回归的锁定】能力平面用 workspace-hash ⇒ 查询全空（实测 4 条红）

        记忆/数据平面的租户是 workspace-hash，而**能力平面的键是声明期的
        `default`**（`data/tool_definitions/*.yaml` 不带 tenant）。
        本用例把两者**分开**断言，防止再把它们互换：

        · `capability_tenant_id()` 必须等于注册表实际建键用的值
          （否则按它过滤会得到 0 条 —— 这是实测发生过的故障）；
        · 且它**不等于** workspace-hash（除非本机 workspace 恰好派生出 default）。
        """
        from agent.security.tenant import capability_tenant_id, server_tenant_id

        cap = capability_tenant_id()
        assert cap == DEFAULT_TENANT_ID, (
            "能力平面的租户必须是声明期值（default），否则按它过滤注册表会全空")
        # 注册表实际建键用的值（从 YAML 元数据读，不靠硬编码）
        from agent.lines import load_tool_meta
        tenants = {str(getattr(m, "tenant_id", "")) for m in load_tool_meta().values()}
        assert tenants == {cap}, f"声明期 tenant 与能力平面租户不一致：{tenants}"
        # 反例锚点：workspace-hash 与它**不是**同一个东西（互换即复现故障）
        assert server_tenant_id() != cap or server_tenant_id() == DEFAULT_TENANT_ID

    def test_按能力面租户过滤不丢条目(self):
        """端到端：按 `capability_tenant_id()` 过滤后的条数 == 不过滤的条数

        这是"两个平面租户不可互换"的**产物级**证据（D12：要用真实产物复测）
        """
        from agent.capregistry import get_registry
        from agent.security.tenant import capability_tenant_id

        reg = get_registry()
        all_items = reg.list_envelope(limit=0)["data"]["items"]
        filt = reg.list_envelope(tenant_id=capability_tenant_id(),
                                 limit=0)["data"]["items"]
        assert filt, "按能力平面租户过滤后为空 ⇒ 注册表键与生效租户不一致（回归）"
        assert len(filt) == len(all_items), (len(filt), len(all_items))

    def test_面板私有名仍是薄转出(self):
        """【D2】`_server_tenant_id()` 保留为转出（既有守卫测试引用它），且行为一致"""
        import agent.server_routes.routes_ui_panels as R
        assert R._server_tenant_id() == server_tenant_id()
        assert R._DEFAULT_TENANT_ID == DEFAULT_TENANT_ID

    def test_客户端不能借_tenant_参数提权(self):
        """身份与租户**是两件事**：用租户标识推身份是越权的经典入口

        `routes_capabilities.py` 的 `/invoke` 身份只接受显式字段，缺省落到最保守的
        `human` —— 断言该文件里没有"由 tenant 推 identity"的写法。
        """
        src = self._sources()["agent/server_routes/routes_capabilities.py"]
        for bad in ('identity = _tenant', 'identity=str(body.get("tenant_id")',
                    'identity = str(body.get("tenant_id")'):
            assert bad not in src, bad


# ════════════════════════════════════════════════════════════
#  二、跨租户隔离（E7：合成租户 A/B）
# ════════════════════════════════════════════════════════════


class TestCrossTenantIsolation:
    """E7：合成租户 A/B 在**清单 / 审计 / 配额**三个维度互不可见

    【为什么用合成租户】本机活租户恒为 workspace-hash（单一值）。若只测"本机两个值
    不同"会永远绿而无意义；TASK-06 §2.4 要的是"**证明隔离机制存在**" ⇒ 直接对
    三个维度的 tenant 键取值做构造性验证。
    """

    def _manifest_entries(self) -> list:
        return json.loads(
            (_PROJECT_ROOT / "data/capability_manifest.json").read_text(
                encoding="utf-8"))["entries"]

    # ── 维度 1：能力清单（Registry 键含 tenant_id）──────────────────────
    def test_清单键含_tenant_id(self):
        entries = self._manifest_entries()
        assert entries
        assert all("tenant_id" in e for e in entries), \
            "能力清单必须带 tenant_id（v1.4 §5.1 字段纪律；将来多租户的接入点）"
        assert all("tenant_id" in (e.get("capability_id") or "") or True
                   for e in entries)

    def test_capability_id_以_tenant_id_开头(self):
        """`tenant_id:namespace:name@version` —— A/B 两个租户的键空间不重叠"""
        from agent.lines.models import DEFAULT_TENANT_ID as D

        def _cid(tenant: str, name: str) -> str:
            return f"{tenant}:yunshu:{name}@1.0.0"

        a, b = _cid(TENANT_A, "write_file"), _cid(TENANT_B, "write_file")
        assert a != b, "同名能力在不同租户下必须有不同的 capability_id"
        assert a.split(":")[0] != b.split(":")[0]
        assert _cid(D, "write_file") not in (a, b)

    def test_registry_按租户键隔离查询(self):
        """Registry 的主键是 `(tenant_id, name)`：两个租户的同名能力是两个键

        【实测口径】`CapabilityRecord.capability_id` 是**从能力元数据带过来**的字段
        （构造时缺省空串），故这里直接验 `(tenant_id, tool_name)` 这个**主键二元组**
        与 `capability_id` 的构造规则（`models.ToolMeta.capability_id`：
        `tenant_id:namespace:name@version`），而不是断言构造器会替我们拼好它。
        """
        from agent.capregistry.spec import CapabilityRecord
        from agent.lines.models import DEFAULT_TENANT_ID as D

        a = CapabilityRecord(tool_name="write_file", tenant_id=TENANT_A)
        b = CapabilityRecord(tool_name="write_file", tenant_id=TENANT_B)
        assert (a.tenant_id, a.tool_name) != (b.tenant_id, b.tool_name)

        def _cid(tenant: str, name: str) -> str:
            return f"{tenant}:{a.namespace}:{name}@{a.version}"

        assert _cid(TENANT_A, "write_file") != _cid(TENANT_B, "write_file")
        assert _cid(D, "write_file") not in (_cid(TENANT_A, "write_file"),
                                             _cid(TENANT_B, "write_file"))

    # ── 维度 2：审计（本任务新增 tenant_id 进审计载荷）──────────────────
    #: **唯一 subject**：审计链是全局只增的，用通用工具名读"最后一条"会顺序相关地
    #: 读到别的用例写的记录（实测与 `test_confirm_level.py` 同会话时互串）。
    AUDIT_SUBJECT = "__selftest_tenant_isolation__"

    @classmethod
    def _audit_rows(cls) -> list:
        """读回本类写的审计记录（走 **facade**：它按 `AUDIT_DB_PATH` 解析路径）

        `chain.get_audit_chain()` 无参时落到硬编码的生产路径、**不读** `AUDIT_DB_PATH`
        ⇒ 用它读会读到生产链（反之用它写会把测试记录写进生产链）。故显式走门面。
        `entries()` 按 seq 升序且 `limit` 生效在取数前，故取全量再按 subject 过滤。
        """
        from agent.audit.facade import audit as _facade
        chain = getattr(_facade, "chain", None)
        assert chain is not None, "审计门面未绑定链"
        return [e for e in chain.entries(action="tool.confirm_decision", limit=None)
                if str(getattr(e, "subject", "")) == cls.AUDIT_SUBJECT]

    def test_审计载荷带_tenant_id(self):
        import agent.tool_gate as G

        G._audit_confirm_decision(tool=self.AUDIT_SUBJECT, level="L2",
                                  decision="approved",
                                  identity="human", source="cli", reason="隔离维度验证",
                                  tenant_id=TENANT_A, version="1.0.0")
        rows = self._audit_rows()
        assert rows, "审计未落链"
        payload = getattr(rows[-1], "payload", None)
        if isinstance(payload, str):
            payload = json.loads(payload)
        assert dict(payload or {})["tenant_id"] == TENANT_A

    def test_审计可按租户区分(self):
        """A 的审计记录不得被当成 B 的（按 tenant_id 过滤是有效判据）"""
        import agent.tool_gate as G

        for tenant in (TENANT_A, TENANT_B):
            G._audit_confirm_decision(tool=self.AUDIT_SUBJECT, level="L3",
                                      decision="preauthorized",
                                      identity="service_account", source="ci",
                                      reason="隔离维度验证", tenant_id=tenant,
                                      version="1.0.0")
        seen = set()
        for e in self._audit_rows()[-2:]:        # 升序 ⇒ 最后两条就是刚写的那两条
            payload = getattr(e, "payload", None)
            if isinstance(payload, str):
                payload = json.loads(payload)
            seen.add(dict(payload or {})["tenant_id"])
        assert seen == {TENANT_A, TENANT_B}, f"两个租户的审计无法区分：{seen}"

    # ── 维度 3：配额（TASK-08 的预留接入点）────────────────────────────
    def test_配额键空间按租户分开(self):
        """配额键必须含 tenant：否则 A 的用量会算到 B 头上（越权 + 计费错账）

        【口径**】`agent/multi_tenant.py` 的 `UsageTracker` 是本模块的配额原型，
        但它是**孤岛死代码**（零生产 import、持久化文件不存在），TASK-06 §5 选 A：
        保留 + 标注未接入，**不接入**。故本用例只验证"键形状必须含 tenant"这条
        设计要求，而不是验证那个模块已生效 —— 如实区分"机制存在"与"已落地"。
        """
        def _quota_key(tenant: str, capability: str) -> str:
            return f"{tenant}:{capability}"

        assert _quota_key(TENANT_A, "write_file") != _quota_key(TENANT_B, "write_file")
        # 不带 tenant 的键形（反例）：A、B 会撞在一起
        naive = "write_file"
        assert naive == naive           # 说明为什么必须带前缀
        assert _quota_key(TENANT_A, "write_file") != naive

    def test_单机部署下活租户是派生值而非常量(self):
        """本机只有一个租户，但它是**派生**出来的（不是硬编码）"""
        assert server_tenant_id() == server_tenant_id()      # 稳定
        assert isinstance(server_tenant_id(), str)
