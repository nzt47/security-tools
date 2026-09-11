"""TASK-S4-02 策略模拟器单元测试（P7.2-19）

覆盖：窗口解析 / 变更分类 / deny→allow 主指标 / 高危清单 / 无样本判定 /
重放漂移 / 候选策略替换语义 / 报告渲染与落盘 / CLI 退出码。
"""
from __future__ import annotations

import json

import pytest

from policy_testkit import isolate_policy, make_policy, make_store

from agent.policy.decisions import DecisionLog, DecisionRecord
from agent.policy.engine import DecisionObserver, PolicyEngine
from agent.policy.models import EFFECT_ALLOW, EFFECT_ASK, EFFECT_DENY, PolicyContext
from agent.policy.simulator import (
    CHANGE_ALLOW_TO_DENY,
    CHANGE_ASK_TO_ALLOW,
    CHANGE_ASK_TO_DENY,
    CHANGE_DENY_TO_ALLOW,
    CHANGE_OTHER,
    CHANGE_TO_ASK,
    HIGH_RISK_CHANGES,
    SimulationChange,
    build_candidate_engine,
    classify_change,
    main,
    parse_since,
    render_markdown,
    simulate,
    write_report,
)


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    return isolate_policy(tmp_path, monkeypatch)


def _records(specs):
    """构造历史决策记录（specs: [(effect, data_class, external, action), ...]）"""
    out = []
    for index, (effect, data_class, external, action) in enumerate(specs):
        ctx = PolicyContext.build(
            capability_id="cp.test.src.act",
            capability={"trust": {"data_class": data_class}},
            tenant_id="t-alpha", actor=f"u{index}", action=action,
            target={"external": external, "host": "api.example.com"})
        out.append(DecisionRecord(
            ts=f"2026-09-{index + 1:02d}T10:00:00+08:00", input=ctx.input,
            effect=effect, policy_id="hist.policy", policy_version="1.0.0",
            actor=f"u{index}", capability_id="cp.test.src.act", action=action,
            tenant_id="t-alpha"))
    return out


def _engine(policies=()):
    return PolicyEngine(make_store(list(policies)), cache_size=0,
                        decision_log=False,
                        observer=DecisionObserver(enabled=False), inbox=False)


# ════════════════════════════════════════════════════════════
#  窗口解析
# ════════════════════════════════════════════════════════════


class TestParseSince:
    @pytest.mark.parametrize("raw,days", [
        ("7d", 7.0), ("1d", 1.0), ("7", 7.0), ("24h", 1.0),
        ("48h", 2.0), ("90m", 0.0625), ("0d", 0.0),
    ])
    def test_单位换算(self, raw, days):
        assert parse_since(raw)[0] == pytest.approx(days)

    @pytest.mark.parametrize("raw", ["", "abc", "7x", "-1d", None])
    def test_非法输入回退默认(self, raw):
        days, label = parse_since(raw)
        assert days == 7.0
        assert isinstance(label, str)

    def test_返回原文供报告展示(self):
        assert parse_since("3d")[1] == "3d"


# ════════════════════════════════════════════════════════════
#  变更分类
# ════════════════════════════════════════════════════════════


class TestClassify:
    @pytest.mark.parametrize("old,new,expected", [
        (EFFECT_DENY, EFFECT_ALLOW, CHANGE_DENY_TO_ALLOW),
        (EFFECT_ALLOW, EFFECT_DENY, CHANGE_ALLOW_TO_DENY),
        (EFFECT_ASK, EFFECT_ALLOW, CHANGE_ASK_TO_ALLOW),
        (EFFECT_ASK, EFFECT_DENY, CHANGE_ASK_TO_DENY),
        (EFFECT_ALLOW, EFFECT_ASK, CHANGE_TO_ASK),
        (EFFECT_DENY, EFFECT_ASK, CHANGE_TO_ASK),
        (EFFECT_ALLOW, EFFECT_ALLOW, ""),
        (EFFECT_DENY, EFFECT_DENY, ""),
    ])
    def test_分类(self, old, new, expected):
        assert classify_change(old, new) == expected

    def test_未知组合归_other(self):
        assert classify_change("weird", "allow") == CHANGE_OTHER

    def test_高危类别集合(self):
        assert CHANGE_DENY_TO_ALLOW in HIGH_RISK_CHANGES
        assert CHANGE_ASK_TO_ALLOW in HIGH_RISK_CHANGES
        assert CHANGE_ALLOW_TO_DENY not in HIGH_RISK_CHANGES


# ════════════════════════════════════════════════════════════
#  模拟主流程
# ════════════════════════════════════════════════════════════


class TestSimulate:
    def test_无样本时明示不可判定(self):
        report = simulate(make_policy(id="cand.a"), engine=_engine(), records=[])
        assert report.total == 0
        assert report.verdict == "no_sample"
        assert report.has_sample is False
        text = render_markdown(report)
        assert "模拟不可判定" in text
        assert "零变更" in text  # 明确点出「零变更不构成安全证据」

    def test_deny_to_allow_主指标与高危清单(self):
        """历史记录里 deny 的条目，若候选放宽为 allow ⇒ 计入高危清单。"""
        history = _records([
            (EFFECT_DENY, "secret", True, "http.post"),
            (EFFECT_DENY, "secret", True, "http.post"),
            (EFFECT_ALLOW, "internal", False, "read"),
        ])
        # 候选：把 secret 出域改成 allow
        candidate = make_policy(
            id="cand.relax", effect="allow",
            match={"all": [
                {"field": "capability.trust.data_class", "op": "eq", "value": "secret"},
                {"field": "target.external", "op": "eq", "value": True},
            ]},
            effective_range={"scopes": ["cp.test.*"]})
        report = simulate(candidate,
                          engine=_engine([make_policy(
                              id="hist.policy", effect="deny",
                              match={"field": "target.external", "op": "eq",
                                     "value": True})]),
                          records=history)
        assert report.total == 3
        # 内置不变量仍先命中 ⇒ 候选的 allow 无法放宽 secret 出域
        assert report.deny_to_allow == 0
        assert report.high_risk_hits == []
        assert report.verdict == "clean"

    def test_候选放宽普通外发时抓到_deny_to_allow(self):
        """候选删掉一条 deny 的效果：由候选把 effect 改为 allow。"""
        history = _records([
            (EFFECT_DENY, "internal", True, "http.post"),
            (EFFECT_DENY, "internal", True, "http.post"),
            (EFFECT_DENY, "internal", True, "http.post"),
        ])
        baseline = _engine([make_policy(
            id="hist.policy", effect="deny",
            match={"field": "target.external", "op": "eq", "value": True})])
        candidate = make_policy(
            id="hist.policy", version="2.0.0", effect="allow",
            match={"field": "target.external", "op": "eq", "value": True})
        report = simulate(candidate, engine=baseline, records=history)
        assert report.deny_to_allow == 3
        assert len(report.high_risk_hits) == 3
        assert report.verdict == "needs_ack"
        assert report.high_risk_hits[0].kind == CHANGE_DENY_TO_ALLOW
        assert report.high_risk_hits[0].old_effect == EFFECT_DENY
        assert report.high_risk_hits[0].new_effect == EFFECT_ALLOW
        assert report.by_capability["cp.test.src.act"] == 3

    def test_收紧策略产出_allow_to_deny(self):
        history = _records([(EFFECT_ALLOW, "internal", True, "http.post")] * 2)
        candidate = make_policy(id="tight.deny", effect="deny")
        report = simulate(candidate, engine=_engine(), records=history)
        assert report.allow_to_deny == 2
        assert report.deny_to_allow == 0
        assert report.verdict == "clean"  # 收紧不是高危

    def test_变更为_ask(self):
        history = _records([(EFFECT_ALLOW, "confidential", True, "http.post")])
        candidate = make_policy(id="ask.cand", effect="ask")
        report = simulate(candidate, engine=_engine(), records=history)
        assert report.to_ask == 1
        assert report.high_risk_hits == []

    def test_ask_转_allow_为高危(self):
        history = _records([(EFFECT_ASK, "confidential", True, "http.post")])
        baseline = _engine([make_policy(id="hist.policy", effect="ask",
                                        match={})])
        candidate = make_policy(id="hist.policy", version="2.0.0", effect="allow",
                                match={})
        report = simulate(candidate, engine=baseline, records=history)
        assert report.ask_to_allow == 1
        assert report.verdict == "needs_ack"

    def test_未变条目计入_unchanged(self):
        history = _records([(EFFECT_ALLOW, "internal", False, "read")] * 4)
        report = simulate(make_policy(id="noop.cand"), engine=_engine(),
                          records=history)
        assert report.unchanged == 4
        assert report.changes == []

    def test_重放漂移被计数(self):
        """历史记录写的 effect 与基线重算不一致 ⇒ 单列 replay_drift，不静默。"""
        history = _records([(EFFECT_DENY, "internal", False, "read")] * 2)
        report = simulate(make_policy(id="drift.cand"), engine=_engine(),
                          records=history)
        assert report.replay_drift == 2  # 基线无 deny 策略 ⇒ 重算为 allow

    def test_按策略分布(self):
        history = _records([(EFFECT_ALLOW, "internal", True, "http.post")] * 3)
        report = simulate(make_policy(id="dist.deny", effect="deny"),
                          engine=_engine(), records=history)
        assert report.by_policy.get("dist.deny") == 3

    def test_窗口标签进入报告(self):
        report = simulate(make_policy(id="w.cand"), engine=_engine(), records=[],
                          window_label="7d", since_days=7.0)
        assert report.window == "7d" and report.window_days == 7.0

    def test_从决策日志读取历史(self, tmp_path):
        log = DecisionLog(str(tmp_path / "decisions.jsonl"))
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                           observer=DecisionObserver(enabled=False), inbox=False)
        # actor 不同 ⇒ 两条独立的决策记录（决策日志按
        # (ts, input, policy_id, effect) 去重，同内容同毫秒会被视为重复 —— 这是
        # 幂等设计，不是缺陷）
        for actor in ("u-a", "u-b"):
            eng.check(PolicyContext.build(
                capability_id="cp.test.src.act", actor=actor,
                capability={"trust": {"data_class": "internal"}},
                target={"external": True, "host": "h"}))
        log.close()
        report = simulate(make_policy(id="fromlog.deny", effect="deny"),
                          engine=eng, log_path=str(tmp_path / "decisions.jsonl"))
        assert report.total == 2
        assert report.source.endswith("decisions.jsonl")

    def test_limit_只取最近_N_条(self, tmp_path):
        log = DecisionLog(str(tmp_path / "d.jsonl"))
        eng = PolicyEngine(make_store(), cache_size=0, decision_log=log,
                           observer=DecisionObserver(enabled=False), inbox=False)
        for index in range(5):
            eng.check(PolicyContext.build(capability_id="cp.x.y",
                                          action=f"a{index}",
                                          target={"external": True}))
        log.close()
        report = simulate(make_policy(id="lim.cand"), engine=eng,
                          log_path=str(tmp_path / "d.jsonl"), limit=2)
        assert report.total == 2

    def test_shadow_诊断进入报告(self):
        base = _engine([make_policy(id="s.allow", effect="allow")])
        candidate = make_policy(id="s.deny", effect="deny")
        report = simulate(candidate, engine=base, records=[])
        assert any(item["shadowing"] == "s.allow@1.0.0" for item in report.shadow)


class TestCandidateEngine:
    def test_候选替换同_id_同版本(self):
        base = make_store([make_policy(id="same.id", effect="deny")])
        engine, policy = build_candidate_engine(
            make_policy(id="same.id", effect="allow"), base_store=base)
        assert policy.effect.value == "allow"
        active = {p.id: p for p in engine.store.active()}
        assert active["same.id"].effect.value == "allow"
        assert len([p for p in engine.store.active() if p.id == "same.id"]) == 1

    def test_候选保留基线其它策略与内置不变量(self):
        base = make_store([make_policy(id="keep.me", effect="deny")])
        engine, _ = build_candidate_engine(make_policy(id="new.one"),
                                           base_store=base)
        ids = [p.id for p in engine.store.active()]
        assert "keep.me" in ids
        assert "builtin.invariant.secret-egress-deny" in ids

    def test_候选_engine_不落盘不埋点(self):
        engine, _ = build_candidate_engine(make_policy(id="iso.cand"),
                                           base_store=make_store())
        assert engine.decision_log is None
        assert engine.cache_size == 0

    def test_多策略候选文档被拒(self):
        from agent.policy.models import PolicyValidationError
        with pytest.raises(PolicyValidationError):
            build_candidate_engine({"policies": [make_policy(id="m.a"),
                                                 make_policy(id="m.b")]},
                                   base_store=make_store())

    def test_单策略文档被接受(self):
        _, policy = build_candidate_engine({"policies": [make_policy(id="doc.a")]},
                                           base_store=make_store())
        assert policy.id == "doc.a"


# ════════════════════════════════════════════════════════════
#  报告
# ════════════════════════════════════════════════════════════


class TestReport:
    def _report(self, history, candidate_id="rep.deny", effect="deny"):
        return simulate(make_policy(id=candidate_id, effect=effect),
                        engine=_engine(), records=_records(history))

    def test_to_dict_机读字段(self):
        body = self._report([(EFFECT_ALLOW, "internal", True, "a")]).to_dict()
        assert body["schema"] == "policy.simulation.v1"
        assert body["verdict"] in ("clean", "needs_ack", "no_sample")
        for key in ("candidate", "window", "totals", "high_risk_hits", "changes",
                    "by_capability", "by_policy", "shadow"):
            assert key in body

    def test_markdown_含主指标与高危清单(self):
        history = [(EFFECT_ALLOW, "internal", True, "http.post")] * 2
        base = _engine([make_policy(id="hist.policy", effect="allow", match={})])
        candidate = make_policy(id="hist.policy", version="2.0.0", effect="deny",
                                match={})
        report = simulate(candidate, engine=base, records=_records(history))
        # 用 deny→allow 反向构造：候选放宽
        candidate2 = make_policy(id="hist.policy", version="3.0.0", effect="allow",
                                 match={})
        relaxed = simulate(candidate2, engine=_engine([make_policy(
            id="hist.policy", effect="deny", match={})]), records=_records(history))
        assert relaxed.deny_to_allow == 2
        text = render_markdown(relaxed)
        assert "# 策略模拟报告（P7.2-19）" in text
        assert "deny → allow（主指标）" in text
        assert "## 三、高危命中清单" in text
        assert "## 五、合入检查项" in text
        assert report.total == 2  # 前一个报告也成立（收紧方向）

    def test_markdown_无高危时明示(self):
        report = self._report([(EFFECT_ALLOW, "internal", False, "a")],
                              candidate_id="plain.deny", effect="deny")
        text = render_markdown(report)
        assert "未发现高危变更" in text or "无历史决策样本" in text

    def test_write_report_落盘两个格式(self, tmp_path):
        report = self._report([(EFFECT_ALLOW, "internal", True, "a")])
        written = write_report(report, md_path=str(tmp_path / "r.md"),
                               json_path=str(tmp_path / "r.json"))
        assert set(written) == {"markdown", "json"}
        assert "# 策略模拟报告" in (tmp_path / "r.md").read_text(encoding="utf-8")
        body = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        assert body["totals"]["total"] == 1

    def test_verdict_三态(self):
        assert self._report([]).verdict == "no_sample"
        assert self._report([(EFFECT_ALLOW, "internal", False, "a")],
                            candidate_id="v.deny", effect="deny").verdict == "clean"
        base = _engine([make_policy(id="hist.policy", effect="deny", match={})])
        relaxed = simulate(make_policy(id="hist.policy", version="9.0.0",
                                       effect="allow", match={}),
                           engine=base,
                           records=_records([(EFFECT_DENY, "internal", True, "a")]))
        assert relaxed.verdict == "needs_ack"

    def test_SimulationChange_to_dict(self):
        change = SimulationChange(kind=CHANGE_DENY_TO_ALLOW, old_effect=EFFECT_DENY,
                                  new_effect=EFFECT_ALLOW, high_risk=True)
        body = change.to_dict()
        assert body["kind"] == CHANGE_DENY_TO_ALLOW and body["high_risk"] is True


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


class TestCli:
    def test_无样本时退出码零(self, tmp_path, capsys):
        cand = tmp_path / "cand.json"
        cand.write_text(json.dumps(make_policy(id="cli.cand")), encoding="utf-8")
        code = main(["--candidate", str(cand), "--since", "7d",
                     "--policy-file", str(tmp_path / "missing.json"),
                     "--log", str(tmp_path / "missing.jsonl")])
        out = capsys.readouterr().out
        assert code == 0
        assert "策略模拟报告" in out

    def test_fail_on_high_risk_退出码二(self, tmp_path):
        from policy_testkit import write_policy_file
        cand = tmp_path / "cand.json"
        cand.write_text(json.dumps(make_policy(
            id="hist.policy", version="2.0.0", effect="allow", match={})),
            encoding="utf-8")
        # 基线策略库必须含被放宽的那条 deny，否则基线重算与历史不一致（重放漂移）
        base_file = write_policy_file(str(tmp_path / "policies.json"),
                                      [make_policy(id="hist.policy", effect="deny",
                                                   match={})])
        log = tmp_path / "decisions.jsonl"
        engine = _engine([make_policy(id="hist.policy", effect="deny", match={})])
        from agent.policy.decisions import DecisionLog as _L
        writer = _L(str(log))
        for actor in ("u-a", "u-b"):
            ctx = PolicyContext.build(capability_id="cp.x.y", actor=actor,
                                      target={"external": True})
            writer.append(ctx, engine.check(ctx))
        writer.close()
        code = main(["--candidate", str(cand), "--log", str(log),
                     "--policy-file", base_file,
                     "--fail-on-high-risk"])
        assert code == 2

    def test_out_写出报告(self, tmp_path):
        cand = tmp_path / "cand.json"
        cand.write_text(json.dumps(make_policy(id="cli.out")), encoding="utf-8")
        out = tmp_path / "r.md"
        json_out = tmp_path / "r.json"
        main(["--candidate", str(cand), "--policy-file",
              str(tmp_path / "missing.json"), "--log", str(tmp_path / "m.jsonl"),
              "--out", str(out), "--json-out", str(json_out)])
        assert out.exists() and json_out.exists()
