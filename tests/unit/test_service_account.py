"""服务账号（`agent/security/service_account.py`）单元测试

## 覆盖 TASK-06 §5 的两条硬指标

| 用例类 | 对应评估标准 |
|---|---|
| `TestTokenShape` | 交付物 #2：Token 结构（`sub` 前缀 / `jti` / `exp` / `scope` / `aud` / `iss`） |
| `TestRevocationIsImmediate` | **E4**：吊销**秒级**生效（附实测时序） |
| `TestNoPermissionInheritance` | **E3**：SA **不继承**创建者权限（负例证明） |
| `TestScopeSemantics` | scope 的两条判定（级别上限 + 能力集合），含 L3 不接受通配 |
| `TestGateWiring` | 预授权**是闸门内的一条判定**，不是旁路（TASK-06 §5 的"不通过"第 3 条） |
| `TestPersistence` | 落盘与重载（加密退化时如实标记，不静默） |

## 纪律

* 凭据库一律指向 `tmp_path`（`CP_SERVICE_ACCOUNTS_PATH`）—— **绝不碰
  `data/service_accounts.json`**（D6）。
* 时间敏感断言（过期、吊销时序）一律**注入 `now`** 或断言"单调时钟差 < 1 秒"，
  不依赖真实 wall clock 的巧合（D9 与"时间类断言"纪律）。
"""
from __future__ import annotations

import json
import time

import pytest

from agent.security import service_account as SA


@pytest.fixture
def sa(tmp_path, monkeypatch):
    """隔离的 SA 注册表（每个用例一张空的凭据库）"""
    monkeypatch.setenv(SA.ACCOUNTS_PATH_ENV, str(tmp_path / "sa.json"))
    monkeypatch.delenv(SA.KEY_ENV, raising=False)
    SA.reset_registry()
    yield SA
    SA.reset_registry()
    from agent.tool_gate import set_preauthorization_hook
    set_preauthorization_hook(None)


def _scope(caps=(), level="L0", **kw):
    return SA.SAScope(capabilities=frozenset(caps), max_confirm_level=level, **kw)


# ════════════════════════════════════════════════════════════
#  一、Token 结构（交付物 #2）
# ════════════════════════════════════════════════════════════


class TestTokenShape:

    def test_sub_带前缀(self, sa):
        """v1.4 §10.1：`sub` 必须带前缀（`sa:`），避免与用户 id 混淆"""
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        claims = sa.verify_token(sa.issue_token("ci-bot"))
        assert claims.sub == "sa:ci-bot"
        assert claims.sub.startswith(SA.SA_SUB_PREFIX)

    def test_字段齐全(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"),
                                  tenant_id="default")
        token = sa.issue_token("ci-bot")
        payload = json.loads(SA._b64d(token.split(".")[1]).decode("utf-8"))
        for field in ("sub", "tenant_id", "scope", "resource_filter", "jti",
                      "exp", "iat", "aud", "iss"):
            assert field in payload, f"Token 缺字段 {field}（v1.4 §10.1）"
        assert payload["aud"] == SA.SA_AUDIENCE
        assert payload["iss"] == SA.SA_ISSUER
        assert len(payload["jti"]) >= 16, "jti 太短：碰撞风险"
        assert token.split(".")[0] == SA.SA_TOKEN_PREFIX

    def test_jti_逐次唯一(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        jtis = {sa.verify_token(sa.issue_token("ci-bot")).jti for _ in range(5)}
        assert len(jtis) == 5, "同一账号多次签发的 jti 必须不同（否则吊销会连带误伤）"

    def test_默认_TTL_是_90_天且上有上限(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        claims = sa.verify_token(sa.issue_token("ci-bot"))
        assert abs((claims.exp - claims.iat) - SA.SA_DEFAULT_TTL_SEC) < 2
        with pytest.raises(SA.SATokenError):
            sa.issue_token("ci-bot", ttl_sec=SA.SA_MAX_TTL_SEC + 1)

    def test_篡改载荷导致签名不符(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L3"))
        token = sa.issue_token("ci-bot")
        head, body, sig = token.split(".")
        payload = json.loads(SA._b64d(body).decode("utf-8"))
        payload["scope"]["max_confirm_level"] = "L3"
        payload["scope"]["capabilities"] = ["*"]      # 提权尝试
        forged = ".".join([head, SA._b64e(json.dumps(payload).encode("utf-8")), sig])
        with pytest.raises(SA.SATokenError):
            sa.verify_token(forged)

    def test_过期令牌被拒(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        token = sa.issue_token("ci-bot", now=1000.0, ttl_sec=60)
        with pytest.raises(SA.SATokenError):
            sa.verify_token(token, now=1000.0 + 61)

    def test_受众不符被拒(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        with pytest.raises(SA.SATokenError):
            sa.verify_token(sa.issue_token("ci-bot"), audience="other-service")

    @pytest.mark.parametrize("bad", ["", "not-a-token", "sa1.abc", "xx.yy.zz"])
    def test_格式非法一律抛错(self, sa, bad):
        """**不返回 None**：让调用方无法漏判（fail-closed 的接口设计）"""
        with pytest.raises(SA.SATokenError):
            sa.verify_token(bad)


# ════════════════════════════════════════════════════════════
#  二、E4：吊销秒级生效
# ════════════════════════════════════════════════════════════


class TestRevocationIsImmediate:

    def test_吊销后立即失效(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        token = sa.issue_token("ci-bot")
        jti = sa.verify_token(token).jti
        assert sa.is_revoked(jti) is False

        assert sa.revoke(jti) == 1

        with pytest.raises(SA.SARevokedError):
            sa.verify_token(token)

    def test_吊销是秒级_实测时序(self, sa):
        """E4 要求"**秒级**，附实测时序"

        实测口径：`revoke()` 返回到下一次校验抛错之间的**单调时钟差**。
        进程内 `jti` 黑名单先于任何文件 IO 判定 ⇒ 期望值应在**毫秒**级。
        """
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        token = sa.issue_token("ci-bot")
        jti = sa.verify_token(token).jti

        t0 = time.monotonic()
        sa.revoke(jti)
        with pytest.raises(SA.SARevokedError):
            sa.verify_token(token)
        elapsed = time.monotonic() - t0

        assert elapsed < 1.0, f"吊销生效耗时 {elapsed:.4f}s，超过秒级要求"
        print(f"\n[实测] 吊销 → 失效 耗时 {elapsed * 1000:.3f} ms")

    def test_吊销不受落盘影响(self, sa, monkeypatch):
        """黑名单在**进程内**先判：即使落盘失败/不可用，吊销仍然生效

        （否则"磁盘写不进去"就成了"吊销不了"的借口 —— 那是拿可用性换安全性。）
        """
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        token = sa.issue_token("ci-bot")
        jti = sa.verify_token(token).jti
        monkeypatch.setattr(sa, "_save_accounts", lambda: None)
        sa.revoke(jti)
        with pytest.raises(SA.SARevokedError):
            sa.verify_token(token)

    def test_按账号名批量吊销(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        t1 = sa.issue_token("ci-bot")
        t2 = sa.issue_token("ci-bot")
        assert sa.revoke(name="ci-bot") == 2
        for t in (t1, t2):
            with pytest.raises(SA.SARevokedError):
                sa.verify_token(t)

    def test_吊销只影响该_jti(self, sa):
        """最小爆炸半径：吊销一个令牌不得让同账号的其它令牌失效"""
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        t1 = sa.issue_token("ci-bot")
        t2 = sa.issue_token("ci-bot")
        sa.revoke(sa.verify_token(t1).jti)
        with pytest.raises(SA.SARevokedError):
            sa.verify_token(t1)
        assert sa.verify_token(t2).sub == "sa:ci-bot"


# ════════════════════════════════════════════════════════════
#  三、E3：SA 不继承创建者权限（负例）
# ════════════════════════════════════════════════════════════


class TestNoPermissionInheritance:
    """v1.4 §10.1 铁律：**SA 不得继承创建者权限**

    TASK-06 §3 第 1 步第 4 项要求"必须有一个**测试**证明：
    以 admin 身份创建的 SA，用该 SA 调用 admin 专属能力时**被拒绝**"。
    """

    def test_admin_创建的_SA_调_admin_能力被拒(self, sa, monkeypatch):
        monkeypatch.setenv("CP_TOOL_GATE_APPROVAL_ENFORCE", "1")
        import agent.tool_gate as G

        # 以 admin 身份创建，但 scope 为空（**什么都不允许**）
        acct = sa.create_service_account("ci-from-admin", created_by="admin",
                                        scope=SA.SAScope())
        assert acct.created_by == "admin"          # 只进审计
        assert acct.scope.capabilities == frozenset()
        assert acct.scope.max_confirm_level == "L0"

        assert sa.install_gate_hook() is True
        token = sa.issue_token("ci-from-admin")
        handle = sa.enter_service_account(token)
        try:
            # admin 专属的高危能力（`shell_execute` 是 critical ⇒ L3）
            result = G.check_tool_call("shell_execute", {}, session_source="cli")
            assert result is not None and result["blocked"] is True, \
                "SA 竟然继承了创建者（admin）的权限 —— 违反 v1.4 §10.1 铁律"
            # 一个普通的 L2 能力同样调不动（scope 为空）
            result2 = G.check_tool_call("write_file", {}, session_source="cli")
            assert result2 is not None and result2["blocked"] is True
        finally:
            handle.reset()

    def test_创建者字段不参与任何判定(self, sa):
        """把 `created_by` 从 "admin" 换成 "root"／空串，授权结果必须**逐字一致**"""
        results = []
        for creator in ("admin", "root", ""):
            sa.reset_registry()
            sa.create_service_account("bot", created_by=creator,
                                      scope=_scope(["read_file"], "L1"))
            acct = sa.get_service_account("bot")
            results.append((tuple(sorted(acct.scope.capabilities)),
                            acct.scope.max_confirm_level))
        assert len(set(results)) == 1, f"创建者影响了授权：{results}"

    def test_scope_是唯一权限来源(self, sa):
        """同一创建者、不同 scope ⇒ 授权随 scope 变（而**不**随创建者变）"""
        sa.create_service_account("a", created_by="admin", scope=_scope(["read_file"], "L1"))
        sa.create_service_account("b", created_by="admin", scope=_scope(["write_file"], "L2"))
        sa_a = sa.get_service_account("a").scope
        sa_b = sa.get_service_account("b").scope
        assert sa.scope_allows(sa_a, "read_file", "L1") is True
        assert sa.scope_allows(sa_a, "write_file", "L2") is False
        assert sa.scope_allows(sa_b, "write_file", "L2") is True
        assert sa.scope_allows(sa_b, "read_file", "L0") is False


# ════════════════════════════════════════════════════════════
#  四、scope 语义
# ════════════════════════════════════════════════════════════


class TestScopeSemantics:

    def test_级别上限是硬约束(self, sa):
        s = _scope(["write_file"], "L1")
        assert sa.scope_allows(s, "write_file", "L0") is True
        assert sa.scope_allows(s, "write_file", "L1") is True
        assert sa.scope_allows(s, "write_file", "L2") is False, "超出上限必须拒绝"

    def test_能力集合逐条点名(self, sa):
        s = _scope(["write_file"], "L2")
        assert sa.scope_allows(s, "write_file", "L2") is True
        assert sa.scope_allows(s, "edit", "L2") is False

    def test_命名空间通配对_L0_L2_有效(self, sa):
        s = _scope(["yunshu:*"], "L2", namespaces=frozenset({"yunshu"}))
        assert sa.scope_allows(s, "write_file", "L2") is True

    def test_L3_不接受通配(self, sa):
        """L3 = 须**显式**预授权 ⇒ `*` 不得成为"一把万能钥匙"（爆炸半径可控）"""
        star = _scope(["*"], "L3")
        assert sa.scope_allows(star, "shell_execute", "L3") is False
        named = _scope(["shell_execute"], "L3")
        assert sa.scope_allows(named, "shell_execute", "L3") is True

    def test_非法级别构造期即抛错(self):
        """**不静默回落最宽**（写错一个字就变成"预授权全部高危动作"是最坏的方向）"""
        with pytest.raises(SA.SAScopeError):
            SA.SAScope(max_confirm_level="L9")
        with pytest.raises(SA.SAScopeError):
            SA.SAScope(max_confirm_level="")

    def test_空能力名与非法级别一律拒(self, sa):
        s = _scope(["read_file"], "L1")
        assert sa.scope_allows(s, "", "L1") is False
        assert sa.scope_allows(s, "read_file", "L9") is False


# ════════════════════════════════════════════════════════════
#  五、闸门接线（不是旁路）
# ════════════════════════════════════════════════════════════


class TestGateWiring:

    def test_钩子安装幂等(self, sa):
        assert sa.install_gate_hook() is True
        from agent.tool_gate import preauthorization_hook
        assert preauthorization_hook() is sa.preauthorize

    def test_preauthorize_要求身份一致(self, sa):
        """上下文里有 SA、但显式声明的身份不是 SA ⇒ **不放行**

        只信上下文会让"忘了设 identity 却设了 SA 上下文"的调用点意外获得预授权；
        两条都核 = 上下文与显式声明**一致**才放行。
        """
        sa.create_service_account("ci-bot", scope=_scope(["write_file"], "L2"))
        token = sa.issue_token("ci-bot")
        handle = sa.enter_service_account(token)
        try:
            assert sa.preauthorize("write_file", "service_account", {}, "L2") is True
            assert sa.preauthorize("write_file", "human", {}, "L2") is False
            assert sa.preauthorize("write_file", "", {}, "L2") is False
        finally:
            handle.reset()

    def test_无上下文时不预授权(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["write_file"], "L2"))
        assert sa.preauthorize("write_file", "service_account", {}, "L2") is False

    def test_进入上下文同时标身份(self, sa):
        """两层都要写：SA 上下文（scope）+ 闸门身份（无身份/非交互判定读它）"""
        import agent.tool_gate as G
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        handle = sa.enter_service_account(sa.issue_token("ci-bot"))
        try:
            assert sa.current_service_account() is not None
            assert G.current_execution_identity() == "service_account"
        finally:
            handle.reset()
        assert G.current_execution_identity() == ""
        assert sa.current_service_account() is None

    def test_非法令牌不进入任何上下文(self, sa):
        import agent.tool_gate as G
        with pytest.raises(SA.SATokenError):
            sa.enter_service_account("sa1.bogus.bogus")
        assert G.current_execution_identity() == ""
        assert sa.current_service_account() is None

    def test_停用账号不能签发(self, sa):
        acct = sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        acct.enabled = False
        with pytest.raises(SA.ServiceAccountError):
            sa.issue_token("ci-bot")


# ════════════════════════════════════════════════════════════
#  六、落盘与重载
# ════════════════════════════════════════════════════════════


class TestPersistence:

    def test_落盘与重载(self, sa, tmp_path):
        path = tmp_path / "sa.json"
        sa.create_service_account("ci-bot", scope=_scope(["write_file"], "L2"),
                                  created_by="admin", description="CI 用")
        assert path.exists(), "凭据未落盘 ⇒ 进程重启后 SA 全部消失"
        sa.reset_registry()
        acct = sa.get_service_account("ci-bot")
        assert acct is not None and acct.created_by == "admin"
        assert acct.scope.max_confirm_level == "L2"
        assert "write_file" in acct.scope.capabilities

    def test_未配密钥时明文落盘但如实标记(self, sa, tmp_path, monkeypatch):
        """密钥缺失 ⇒ 退化而不是拒绝；但**绝不静默**（信封里 `encrypted=false`）"""
        monkeypatch.delenv(SA.KEY_ENV, raising=False)
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        raw = json.loads((tmp_path / "sa.json").read_text(encoding="utf-8"))
        assert raw.get("encrypted") is False, raw.keys()

    def test_清单不泄漏令牌原文(self, sa):
        sa.create_service_account("ci-bot", scope=_scope(["read_file"], "L1"))
        token = sa.issue_token("ci-bot")
        blob = json.dumps(sa.list_service_accounts(), ensure_ascii=False)
        assert token not in blob, "盘点输出里出现了令牌原文"
        assert "jti" not in blob or token.split(".")[1][:20] not in blob

    def test_空名拒绝(self, sa):
        with pytest.raises(SA.ServiceAccountError):
            sa.create_service_account("  ")

    def test_签发不存在的账号抛错(self, sa):
        with pytest.raises(SA.ServiceAccountError):
            sa.issue_token("nobody")


# ════════════════════════════════════════════════════════════
#  七、与 actormatrix 的第四类主体衔接
# ════════════════════════════════════════════════════════════


class TestActorMatrixIntegration:

    def test_第四类主体已登记(self):
        from agent.security.actor_matrix import (ACTOR_SERVICE_ACCOUNT,
                                                 ACTOR_TYPES)
        assert ACTOR_SERVICE_ACCOUNT == "service_account"
        assert ACTOR_SERVICE_ACCOUNT in ACTOR_TYPES
        assert len(ACTOR_TYPES) == 4, "第四类主体必须真的加进值域"

    @pytest.mark.parametrize("name,want", [
        ("sa:ci-bot", "service_account"),
        ("service_account:bot", "service_account"),
        ("ci:nightly", "service_account"),
        ("cron:job", "service_account"),
        ("webhook:github", "service_account"),
        ("auto:skill", "auto"),                 # 前缀不得互相抢
        ("sub_agent:x", "sub_agent"),
        ("human", "human"),
    ])
    def test_actor_名可反推类型(self, name, want):
        from agent.security.actor_matrix import infer_actor_type
        assert infer_actor_type(name, default="human") == want

    @pytest.mark.parametrize("alias", ["sa", "ci", "cron", "webhook",
                                      "service_account", "service-account"])
    def test_别名可规范化(self, alias):
        """场景名（ci/cron/webhook）必须能被接住，否则 `normalize_actor_type` 抛错"""
        from agent.security.actor_matrix import ACTOR_SERVICE_ACCOUNT, normalize_actor_type
        assert normalize_actor_type(alias) == ACTOR_SERVICE_ACCOUNT
