"""TASK-S4-01 审批面安全单测（v7.2 §5.7⑦）

验收对应：
- **会话绑定**：审批链接 token 与会话强绑定 ⇒ 分享式链接（换会话）失效；
- **链接时效 ≤15 分钟**（配置写大也钳制到 900s）；过期 → 提示重新发起；
- 链接**一次性**（兑换即作废，防重放）；
- **CSRF 保护**（双重提交：会话 Cookie + 请求头）；
- **destructive 二次认证**：一次性确认码/口令，未通过不得执行；
- 前端审批按钮区 **DOM 隔离 + 固定 zIndex + CSRF 头**（静态资产断言）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent.security import approval_session as S


class _Clock:
    """可控时钟（时效类断言不依赖真实等待）"""

    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def store():
    return S.ApprovalSessionStore(clock=_Clock())


def _session(store, **kwargs):
    params = {"actor": "alice", "actor_type": "human",
              "identity_source": "token_map", "actor_ip": "10.0.0.7"}
    params.update(kwargs)
    return store.open_session(**params)


# ════════════════════════════════════════════════════════════════
#  会话
# ════════════════════════════════════════════════════════════════


class TestSession:
    def test_open_session_issues_csrf_and_expiry(self, store):
        session = _session(store)
        assert session.session_id and len(session.session_id) == 32
        assert session.csrf_token and len(session.csrf_token) > 16
        assert session.expires_at > session.created_at

    def test_public_view_hides_secrets(self, store):
        session = _session(store)
        public = session.to_public()
        assert "csrf_token" not in public
        assert "actor_ip" not in public
        assert public["identity_source"] == "token_map"

    def test_session_expires(self, store):
        clock = store._clock
        session = _session(store, ttl_seconds=60)
        assert store.get_session(session.session_id) is not None
        clock.advance(61)
        assert store.get_session(session.session_id) is None

    def test_close_session_invalidates_its_links(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        assert store.check_link(link.token, session_id=session.session_id).ok is True
        store.close_session(session.session_id)
        assert store.check_link(link.token,
                                session_id=session.session_id).code == S.CHECK_UNKNOWN

    def test_unknown_session_not_returned(self, store):
        assert store.get_session("nope") is None

    def test_stats_reports_ttl_bounds(self, store):
        _session(store)
        stats = store.stats()
        assert stats["sessions"] == 1
        assert stats["max_link_ttl_seconds"] == S.MAX_LINK_TTL_SECONDS == 900.0
        assert stats["csrf_enabled"] is True


# ════════════════════════════════════════════════════════════════
#  链接：会话绑定 / 时效 / 一次性
# ════════════════════════════════════════════════════════════════


class TestLinkBinding:
    def test_link_ok_for_same_session(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        check = store.check_link(link.token, session_id=session.session_id,
                                 record_id="r-1")
        assert check.ok is True and check.code == S.CHECK_OK

    def test_shared_link_fails_in_another_session(self, store):
        """**分享式链接**：token 有效但会话不符 ⇒ 明确拒绝"""
        owner = _session(store, actor="alice")
        stranger = _session(store, actor="bob")
        link = store.issue_link(session_id=owner.session_id, record_id="r-1")
        check = store.check_link(link.token, session_id=stranger.session_id,
                                 record_id="r-1")
        assert check.ok is False
        assert check.code == S.CHECK_SESSION_MISMATCH
        assert "分享" in check.message

    def test_link_without_session_fails(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        assert store.check_link(link.token, session_id="").code == \
            S.CHECK_SESSION_MISMATCH

    def test_record_mismatch_rejected(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        check = store.check_link(link.token, session_id=session.session_id,
                                 record_id="r-2")
        assert check.code == S.CHECK_RECORD_MISMATCH

    def test_unknown_token_rejected(self, store):
        session = _session(store)
        assert store.check_link("bogus", session_id=session.session_id).code == \
            S.CHECK_UNKNOWN

    def test_link_expires_within_15_minutes(self, store):
        clock = store._clock
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        assert link.expires_at - link.created_at == 900.0
        clock.advance(899)
        assert store.check_link(link.token, session_id=session.session_id).ok is True
        clock.advance(2)
        check = store.check_link(link.token, session_id=session.session_id)
        assert check.ok is False and check.code == S.CHECK_EXPIRED
        assert "重新发起" in check.message

    def test_session_expiry_invalidates_link(self, store):
        clock = store._clock
        session = _session(store, ttl_seconds=30)
        link = store.issue_link(session_id=session.session_id, record_id="r-1",
                                ttl_seconds=900)
        clock.advance(31)
        assert store.check_link(link.token,
                                session_id=session.session_id).code == \
            S.CHECK_SESSION_EXPIRED

    def test_redeem_is_one_time(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1")
        assert store.redeem_link(link.token, session_id=session.session_id).ok is True
        second = store.redeem_link(link.token, session_id=session.session_id)
        assert second.ok is False and second.code == S.CHECK_ALREADY_USED

    def test_redeem_failure_does_not_consume(self, store):
        owner = _session(store)
        stranger = _session(store, actor="bob")
        link = store.issue_link(session_id=owner.session_id, record_id="r-1")
        assert store.redeem_link(link.token, session_id=stranger.session_id).ok is False
        # 越权尝试不得消耗真正持有者的链接
        assert store.redeem_link(link.token, session_id=owner.session_id).ok is True


# ════════════════════════════════════════════════════════════════
#  时效配置钳制
# ════════════════════════════════════════════════════════════════


class TestTtlConfig:
    def test_default_is_900(self, monkeypatch):
        monkeypatch.delenv("CP_APPROVAL_LINK_TTL_SECONDS", raising=False)
        assert S.link_ttl_seconds() == 900.0

    def test_larger_value_is_clamped_to_900(self, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_LINK_TTL_SECONDS", "7200")
        assert S.link_ttl_seconds() == 900.0

    def test_smaller_value_respected(self, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_LINK_TTL_SECONDS", "120")
        assert S.link_ttl_seconds() == 120.0

    def test_invalid_value_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_LINK_TTL_SECONDS", "abc")
        assert S.link_ttl_seconds() == 900.0

    def test_non_positive_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_LINK_TTL_SECONDS", "-5")
        assert S.link_ttl_seconds() == 900.0

    def test_issue_link_clamps_explicit_ttl(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1",
                                ttl_seconds=99999)
        assert link.expires_at - link.created_at == 900.0


# ════════════════════════════════════════════════════════════════
#  CSRF
# ════════════════════════════════════════════════════════════════


class TestCsrf:
    def test_matching_token_passes(self, store):
        session = _session(store)
        assert store.verify_csrf(session.session_id, session.csrf_token).ok is True

    def test_missing_or_wrong_token_fails(self, store):
        session = _session(store)
        for token in ("", "wrong", session.csrf_token[:-1]):
            check = store.verify_csrf(session.session_id, token)
            assert check.ok is False
            assert check.code == S.CHECK_CSRF_MISMATCH

    def test_unknown_session_fails(self, store):
        assert store.verify_csrf("nope", "x").code == S.CHECK_SESSION_UNKNOWN

    def test_disabled_allows(self, store, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_CSRF_ENABLED", "0")
        assert S.csrf_enabled() is False
        assert store.verify_csrf("nope", "").ok is True

    def test_csrf_tokens_are_unique_per_session(self, store):
        a = _session(store, actor="alice")
        b = _session(store, actor="bob")
        assert a.csrf_token != b.csrf_token


# ════════════════════════════════════════════════════════════════
#  二次认证（destructive 强制）
# ════════════════════════════════════════════════════════════════


class TestSecondFactor:
    def test_destructive_link_requires_second_factor(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1",
                                risk="destructive")
        check = store.check_link(link.token, session_id=session.session_id)
        assert check.ok is True and check.requires_second_factor is True
        assert link.destructive is True

    def test_non_destructive_link_does_not_require(self, store):
        session = _session(store)
        link = store.issue_link(session_id=session.session_id, record_id="r-1",
                                risk="high")
        assert store.check_link(link.token,
                                session_id=session.session_id).requires_second_factor is False

    def test_one_time_code_flow(self, store):
        session = _session(store)
        code = store.issue_second_factor(session_id=session.session_id,
                                        record_id="r-1")
        assert len(code) == 6 and code.isdigit()
        assert store.verify_second_factor(session_id=session.session_id,
                                          record_id="r-1", code=code).ok is True
        # 一次性：再次使用同码失败
        assert store.verify_second_factor(session_id=session.session_id,
                                          record_id="r-1", code=code).ok is False

    def test_empty_code_reports_required(self, store):
        session = _session(store)
        check = store.verify_second_factor(session_id=session.session_id,
                                          record_id="r-1", code="")
        assert check.code == S.CHECK_SECOND_FACTOR_REQUIRED
        assert check.requires_second_factor is True

    def test_wrong_code_rejected(self, store):
        session = _session(store)
        store.issue_second_factor(session_id=session.session_id, record_id="r-1")
        check = store.verify_second_factor(session_id=session.session_id,
                                           record_id="r-1", code="000000")
        assert check.ok is False

    def test_code_is_bound_to_session_and_record(self, store):
        alice = _session(store, actor="alice")
        bob = _session(store, actor="bob")
        code = store.issue_second_factor(session_id=alice.session_id,
                                        record_id="r-1")
        # 换会话用同一码 ⇒ 无效（分享式链接拿不到这一步）
        assert store.verify_second_factor(session_id=bob.session_id,
                                          record_id="r-1", code=code).ok is False
        # 换记录同样无效
        assert store.verify_second_factor(session_id=alice.session_id,
                                          record_id="r-2", code=code).ok is False

    def test_code_expires(self, store):
        clock = store._clock
        session = _session(store)
        code = store.issue_second_factor(session_id=session.session_id,
                                        record_id="r-1")
        clock.advance(901)
        check = store.verify_second_factor(session_id=session.session_id,
                                           record_id="r-1", code=code)
        assert check.ok is False and check.code == S.CHECK_EXPIRED

    def test_passphrase_configuration(self, store, monkeypatch):
        monkeypatch.setenv("CP_APPROVAL_SECOND_FACTOR_CODE", "let-me-in-42")
        session = _session(store)
        assert S.second_factor_passphrase() == "let-me-in-42"
        assert store.verify_second_factor(session_id=session.session_id,
                                          record_id="r-1",
                                          code="let-me-in-42").ok is True
        assert store.verify_second_factor(session_id=session.session_id,
                                          record_id="r-1", code="nope").ok is False


# ════════════════════════════════════════════════════════════════
#  进程级单例与 PII
# ════════════════════════════════════════════════════════════════


class TestStoreSingletonAndPii:
    def test_singleton_is_reused_and_resettable(self):
        first = S.get_session_store()
        assert S.get_session_store() is first
        S.reset_approval_sessions()
        assert S.get_session_store() is not first

    def test_ip_fields_masked_not_raw(self, store, s401_ip_key):
        session = _session(store, actor_ip="10.0.0.7")
        fields = session.ip_fields()
        assert fields["actor_ip_masked"] == "10.0.xxx.xxx"
        assert fields["actor_ip_hash"]
        assert "10.0.0.7" not in str(fields)

    def test_no_ip_no_fields(self, store):
        session = _session(store, actor_ip="")
        assert session.ip_fields() == {}

    def test_reset_clears_state(self, store):
        _session(store)
        store.reset()
        assert store.stats()["sessions"] == 0


# ════════════════════════════════════════════════════════════════
#  前端审批按钮区：DOM 隔离 + CSRF 头（静态资产断言）
# ════════════════════════════════════════════════════════════════


class TestFrontendIsolation:
    """前端资产断言（无浏览器依赖；锁死 §5.7⑦ 的前端两条硬要求）"""

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).resolve().parents[2]

    def test_console_assets_exist(self, repo_root):
        assert (repo_root / "templates" / "approval_console.html").exists()
        assert (repo_root / "static" / "js" / "approval_console.js").exists()
        assert (repo_root / "static" / "css" / "approval_console.css").exists()

    def test_css_fixes_zindex_and_isolates_stacking(self, repo_root):
        css = (repo_root / "static" / "css" / "approval_console.css").read_text(
            encoding="utf-8")
        assert "#cp-approval-console-root" in css
        assert "z-index: 2147483000" in css        # 固定 zIndex（分发壳 §八-4）
        assert "isolation: isolate" in css          # 独立层叠上下文
        assert "contain: layout style paint" in css  # 布局/绘制不外溢

    def test_js_mounts_in_shadow_root(self, repo_root):
        js = (repo_root / "static" / "js" / "approval_console.js").read_text(
            encoding="utf-8")
        assert "attachShadow" in js
        assert "cp-approval-console-root" in js

    def test_js_sends_csrf_header(self, repo_root):
        js = (repo_root / "static" / "js" / "approval_console.js").read_text(
            encoding="utf-8")
        assert "X-CSRF-Token" in js
        assert "cp_approval_csrf" in js             # 双重提交：从 Cookie 读取
        assert "credentials: 'same-origin'" in js   # 会话 Cookie 随请求

    def test_js_does_not_claim_actor_type(self, repo_root):
        """前端不得声明执行体身份（后端单表校验是唯一权威）"""
        js = (repo_root / "static" / "js" / "approval_console.js").read_text(
            encoding="utf-8")
        assert "actor_type:" not in js
        assert "'actor'" not in js
