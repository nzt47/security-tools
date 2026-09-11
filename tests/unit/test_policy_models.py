"""TASK-S4-02 策略 schema 单元测试（§3.11 / §5.6）

覆盖：Policy 九字段校验 / Effect / effective_range 判定 / message_template 安全渲染 /
PolicyContext（含 descriptor 裁剪）/ PolicyDecision 叶子投影 / P7.1-20 禁用 token。
"""
from __future__ import annotations

import pytest

from policy_testkit import make_policy

from agent.policy import matcher, models
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    POLICY_SCHEMA,
    Effect,
    EffectiveRange,
    Policy,
    PolicyContext,
    PolicyDecision,
    PolicyValidationError,
    canonical_json,
    render_message,
    template_fields,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    from policy_testkit import isolate_policy
    isolate_policy(tmp_path, monkeypatch)


# ────────────────────────────────────────────────────────────
#  Effect
# ────────────────────────────────────────────────────────────


class TestEffect:
    def test_schema_标识与三态(self):
        assert POLICY_SCHEMA == "policy.v1"
        assert {e.value for e in Effect} == {"allow", "deny", "ask"}

    @pytest.mark.parametrize("raw,expected", [
        ("allow", EFFECT_ALLOW), ("DENY", EFFECT_DENY),
        (" Ask ", EFFECT_ASK), (Effect.DENY, EFFECT_DENY),
    ])
    def test_parse_归一化(self, raw, expected):
        assert Effect.parse(raw).value == expected

    @pytest.mark.parametrize("raw", ["", None, "permit", "拒绝", 1])
    def test_parse_非法取值报错(self, raw):
        with pytest.raises(PolicyValidationError) as exc:
            Effect.parse(raw)
        assert exc.value.code == "INVALID_EFFECT"


# ────────────────────────────────────────────────────────────
#  Policy（§3.11 九个字段）
# ────────────────────────────────────────────────────────────


class TestPolicySchema:
    def test_九字段全部落地(self):
        policy = Policy.parse(make_policy())
        body = policy.to_dict()
        assert set(body) == {
            "id", "version", "owner", "effect", "match", "message_template",
            "effective_range", "break_glass_ttl_min", "signature",
        }

    def test_齐全策略解析成功(self):
        policy = Policy.parse(make_policy(
            id="sec.a-b", version="2.3.4", owner="security", effect="ask",
            effective_range={"tenants": ["t1"], "scopes": ["cp.*.read"]},
            break_glass_ttl_min=15, signature="ed25519:00ff"))
        assert policy.key() == "sec.a-b@2.3.4"
        assert policy.effect is Effect.ASK
        assert policy.is_break_glassable
        assert policy.effective_range.tenants == ("t1",)

    @pytest.mark.parametrize("field,value,fragment", [
        ("id", "", "缺 id"),
        ("id", "BadId", "id 形态非法"),
        ("id", "9ok", "id 形态非法"),
        ("version", "", "缺 version"),
        ("version", "1.0", "version 非法"),
        ("owner", "", "缺 owner"),
    ])
    def test_必填字段缺失或非法报错(self, field, value, fragment):
        raw = make_policy()
        raw[field] = value
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(raw)
        assert any(fragment in e for e in exc.value.errors)

    def test_缺_effect_报错(self):
        raw = make_policy()
        raw.pop("effect")
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(raw)
        assert any("缺 effect" in e for e in exc.value.errors)

    def test_缺_match_报错(self):
        raw = make_policy()
        raw.pop("match")
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(raw)
        assert any("缺 match" in e for e in exc.value.errors)

    def test_match_非_dict_报错(self):
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(make_policy(match=[{"field": "a", "value": 1}]))
        assert any("match 应为 dict" in e for e in exc.value.errors)

    @pytest.mark.parametrize("ttl,fragment", [
        (0, "必须为正整数"), (-5, "必须为正整数"), ("abc", "应为整数分钟"),
    ])
    def test_break_glass_ttl_非法(self, ttl, fragment):
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(make_policy(break_glass_ttl_min=ttl))
        assert any(fragment in e for e in exc.value.errors)

    def test_break_glass_ttl_缺省为_None_且不可例外(self):
        policy = Policy.parse(make_policy(break_glass_ttl_min=None))
        assert policy.break_glass_ttl_min is None
        assert policy.is_break_glassable is False

    def test_match_hash_稳定(self):
        a = Policy.parse(make_policy())
        b = Policy.parse(make_policy())
        assert a.match_signature() == b.match_signature()
        c = Policy.parse(make_policy(match={"field": "tenant.id", "value": "x"}))
        assert c.match_signature() != a.match_signature()

    def test_source_ref_不进签名材料(self):
        a = Policy.parse(make_policy(), source_ref="data/policies/a.json")
        b = Policy.parse(make_policy(), source_ref="elsewhere/b.json")
        assert a.signing_payload() == b.signing_payload()


class TestForbiddenTokens:
    """P7.1-20：策略里出现网络/执行副作用 ⇒ 装载期拒绝（不是运行期忽略）"""

    @pytest.mark.parametrize("token", [
        "http.send", "socket", "subprocess", "os.system", "requests.", "eval(",
    ])
    def test_禁用_token_被拒(self, token):
        with pytest.raises(PolicyValidationError) as exc:
            Policy.parse(make_policy(
                match={"field": "attributes.note", "op": "eq", "value": token}))
        assert any("P7.1-20" in e for e in exc.value.errors)

    def test_设计文档的_Rego_伪代码写法不被接受(self):
        """§5.6 的例子含 ``http.send(_)``，P7.1-20 已更正为伪代码；抄进来必须被拦。"""
        with pytest.raises(PolicyValidationError):
            Policy.parse(make_policy(
                match={"all": [{"field": "attributes.x", "op": "eq",
                                "value": "http.send(_)"}]}))

    def test_禁用_token_清单非空且覆盖网络与执行两类(self):
        assert "http.send" in models.FORBIDDEN_MATCH_TOKENS
        assert "subprocess" in models.FORBIDDEN_MATCH_TOKENS


# ────────────────────────────────────────────────────────────
#  effective_range（云枢裁定 #2）
# ────────────────────────────────────────────────────────────


class TestEffectiveRange:
    def test_缺省即全局长期(self):
        rng = EffectiveRange.parse(None)
        assert rng.is_active(tenant_id="t", capability_id="cp.x.y")
        assert rng.to_dict() == {}
        assert rng.specificity() == 0

    def test_未知键报错(self):
        with pytest.raises(PolicyValidationError) as exc:
            EffectiveRange.parse({"from": "2026-01-01"})
        assert any("未知键" in e for e in exc.value.errors)

    def test_非_dict_报错(self):
        with pytest.raises(PolicyValidationError):
            EffectiveRange.parse(["2026-01-01"])

    def test_时间窗口(self):
        rng = EffectiveRange.parse({"not_before": "2000-01-01T00:00:00+08:00",
                                    "not_after": "2000-12-31T00:00:00+08:00"})
        assert rng.covers_time() is False
        future = EffectiveRange.parse({"not_before": "2999-01-01T00:00:00+08:00"})
        assert future.covers_time() is False

    def test_租户白名单(self):
        rng = EffectiveRange.parse({"tenants": ["t-alpha"]})
        assert rng.covers_tenant("t-alpha") is True
        assert rng.covers_tenant("t-beta") is False

    def test_范围通配(self):
        rng = EffectiveRange.parse({"scopes": ["cp.filesystem.*"]})
        assert rng.covers_scope("cp.filesystem.local.read") is True
        assert rng.covers_scope("cp.web.search") is False

    def test_specificity_随约束增加(self):
        assert EffectiveRange.parse({"tenants": ["a"], "scopes": ["b"]}).specificity() == 2

    def test_策略级_is_active_组合判定(self):
        policy = Policy.parse(make_policy(
            effective_range={"tenants": ["t1"], "scopes": ["cp.a.*"]}))
        assert policy.is_active(tenant_id="t1", capability_id="cp.a.b") is True
        assert policy.is_active(tenant_id="t2", capability_id="cp.a.b") is False
        assert policy.is_active(tenant_id="t1", capability_id="cp.z.b") is False


# ────────────────────────────────────────────────────────────
#  message_template 安全渲染
# ────────────────────────────────────────────────────────────


class TestRenderMessage:
    def test_替换已知占位符(self):
        assert render_message("拒绝 {capability_id}", {"capability_id": "cp.a"}) \
            == "拒绝 cp.a"

    def test_未提供占位符原样保留(self):
        assert render_message("{a}-{b}", {"a": 1}) == "1-{b}"

    def test_不做属性访问(self):
        """``str.format`` 会经属性访问读到类型对象——这是被刻意回避的面。"""
        text = render_message("{obj.__class__}", {"obj": object()})
        assert text == "{obj.__class__}"

    def test_不做格式化规格(self):
        assert render_message("{x:>10}", {"x": "a"}) == "{x:>10}"

    def test_空模板返回空串(self):
        assert render_message("", {"a": 1}) == ""
        assert render_message(None, {}) == ""

    def test_template_fields_提取占位符名(self):
        assert template_fields("a {x} b {y} c") == ["x", "y"]


# ────────────────────────────────────────────────────────────
#  PolicyContext
# ────────────────────────────────────────────────────────────


class TestPolicyContext:
    def test_build_规范骨架(self):
        ctx = PolicyContext.build(capability_id="cp.a.b", actor="u1",
                                  actor_role="admin", action="read")
        assert set(ctx.input) == {"capability", "tenant", "actor", "action",
                                  "target", "attributes"}
        assert ctx.capability_id == "cp.a.b"
        assert ctx.tenant_id == "default"
        assert ctx.actor == "u1"
        assert ctx.action == "read"
        assert ctx.target_external is False

    def test_get_点分路径与缺失(self):
        ctx = PolicyContext.build(capability={"trust": {"data_class": "secret"}})
        assert ctx.get("capability.trust.data_class") == "secret"
        assert ctx.get("capability.trust.nope", "d") == "d"
        assert ctx.get("capability.trust.data_class.deep") is None

    def test_from_input_非_dict_退化空(self):
        assert PolicyContext.from_input(None).input == {}
        assert PolicyContext.from_input("x").input == {}
        same = PolicyContext.build(capability_id="a")
        assert PolicyContext.from_input(same) is same

    def test_cache_material_稳定(self):
        a = PolicyContext.build(capability_id="a", tenant_id="t")
        b = PolicyContext.build(capability_id="a", tenant_id="t")
        assert a.cache_material() == b.cache_material()
        assert a.cache_material() == canonical_json(a.input)

    def test_template_values_扁平叶子(self):
        ctx = PolicyContext.build(capability_id="cp.a", target={"host": "h"})
        values = ctx.template_values()
        assert values["capability_id"] == "cp.a"
        assert values["host"] == "h"
        assert values["target_host"] == "h"


class _Leaf:
    def __init__(self, value):
        self.value = value


class TestPolicyContextFromDescriptor:
    """§3.2 descriptor 裁剪：只读叶子，不搬运 live 对象"""

    class _Descriptor:
        def __init__(self, **kwargs):
            self.capability_id = kwargs.get("capability_id", "cp.test.src.act")
            self.trust = type("T", (), {
                "data_class": kwargs.get("data_class"),
                "risk_level": kwargs.get("risk_level"),
                "requires_approval": kwargs.get("requires_approval", False)})()
            self.origin = type("O", (), {
                "source_type": kwargs.get("source_type", "builtin"),
                "external_endpoint": kwargs.get("external_endpoint", False),
                "provenance": kwargs.get("provenance", "unknown")})()
            self.evolution = type("E", (), {"stage": kwargs.get("stage")})()
            self.tenancy = type("N", (), {"tenant_id": kwargs.get("tenant_id", "default")})()

    def test_裁剪出判定叶子(self):
        ctx = PolicyContext.from_descriptor(self._Descriptor(
            data_class="secret", risk_level="destructive",
            external_endpoint=True, tenant_id="t9"))
        assert ctx.capability_id == "cp.test.src.act"
        assert ctx.get("capability.trust.data_class") == "secret"
        assert ctx.get("capability.trust.risk_level") == "destructive"
        assert ctx.get("capability.origin.external_endpoint") is True
        assert ctx.tenant_id == "t9"

    def test_未分级保持_None(self):
        ctx = PolicyContext.from_descriptor(self._Descriptor())
        assert ctx.get("capability.trust.data_class") is None
        assert ctx.get("capability.trust.risk_level") is None

    def test_opaque_由_provenance_推导(self):
        assert PolicyContext.from_descriptor(
            self._Descriptor(provenance="unknown")).get("capability.origin.opaque") is True
        assert PolicyContext.from_descriptor(
            self._Descriptor(provenance="verified")).get("capability.origin.opaque") is False

    def test_input_是纯_JSON_可序列化(self):
        ctx = PolicyContext.from_descriptor(self._Descriptor(data_class="internal"))
        assert canonical_json(ctx.input) != ""  # 可序列化 ⇒ 可缓存/可重放

    def test_overrides_生效(self):
        ctx = PolicyContext.from_descriptor(self._Descriptor(), tenant_id="override")
        assert ctx.tenant_id == "override"


# ────────────────────────────────────────────────────────────
#  PolicyDecision
# ────────────────────────────────────────────────────────────


class TestPolicyDecision:
    def test_谓词语义(self):
        assert PolicyDecision(effect=EFFECT_ALLOW).allowed is True
        assert PolicyDecision(effect=EFFECT_DENY).denied is True
        assert PolicyDecision(effect=EFFECT_DENY).allowed is False
        assert PolicyDecision(effect=EFFECT_ASK).needs_human is True
        assert PolicyDecision(effect=EFFECT_ASK).allowed is False
        assert PolicyDecision(effect=EFFECT_ALLOW,
                              break_glass=True).needs_human is True

    def test_audit_leaves_只有_6_6_清单字段(self):
        decision = PolicyDecision(
            effect=EFFECT_DENY, policy_id="p", policy_version="1.0.0",
            actor="u", capability_id="cp.a", action="a", latency_ms=1.234,
            cache_hit=True, reason_code="policy_deny")
        leaves = decision.audit_leaves()
        assert leaves["policy_version"] == "1.0.0"
        assert leaves["actor"] == "u"
        assert leaves["scope"] == "cp.a"
        assert leaves["result"] == EFFECT_DENY
        assert leaves["latency_ms"] == 1.234
        assert "match" not in leaves and "input" not in leaves  # 不写匹配值

    def test_scope_退化为_action(self):
        leaves = PolicyDecision(effect=EFFECT_ALLOW, action="http.get").audit_leaves()
        assert leaves["scope"] == "http.get"

    def test_to_dict_全字段(self):
        body = PolicyDecision(effect=EFFECT_DENY, policy_id="p").to_dict()
        for key in ("effect", "policy_id", "matched", "policy_version",
                    "reason_code", "message", "break_glass", "cache_hit",
                    "latency_ms", "tenant_id", "capability_id", "action", "actor"):
            assert key in body


# ────────────────────────────────────────────────────────────
#  工具函数
# ────────────────────────────────────────────────────────────


class TestHelpers:
    def test_canonical_json_键序无关(self):
        assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})

    def test_canonical_json_不可序列化返回空串(self):
        assert canonical_json(object()) == ""

    def test_matcher_支持矩阵与算子非空(self):
        matrix = matcher.support_matrix()
        assert matrix["supported_ops"] == list(matcher.OPS)
        assert matrix["unsupported_constructs"]
