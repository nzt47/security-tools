"""TASK-S2-02 集成测试：审计平权（UI × Agent 同表）+ 校验 CLI + 每日根覆盖

覆盖真实调用路径（非桩）：
    - ApprovalFlow 提交/审批/驳回/归档 → 链上留痕
    - review_gate 豁免发布 / descriptor 台账变更 / lineage 谱系写入 → 链上留痕
    - S2-01 Trace 收尾、失败能力、脱敏动作 → 链上留痕（含遗留 #3）
    - **真实 Flask 写路由**（技能删除 / 设置写）经全局包装落进**同一张表**
    - 每日 Merkle 根覆盖 UI+Agent 全量记录，可重放
    - 验签 CLI 退出码与篡改定位（混沌演练 §11.10 的自动化形态）
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from flask import Flask

from agent.audit import facade as facade_mod
from agent.audit.chain import AuditChain, reset_audit_chains
from agent.audit.facade import AuditFacade
from agent.audit.ui_middleware import UIAuditRecorder, reset_ui_recorders


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture
def chain(tmp_path):
    reset_audit_chains()
    reset_ui_recorders()
    c = AuditChain(str(tmp_path / "audit_chain.db"),
                   roots_path=str(tmp_path / "daily_roots.jsonl"),
                   signing_key_path=str(tmp_path / "k.pem"), auto_seal=False)
    yield c
    c.close(timeout=2.0)
    reset_audit_chains()
    reset_ui_recorders()


@pytest.fixture
def bound(chain):
    """进程级门面绑定测试台账：被测模块内部的 audit.record 走同一条链"""
    previous = facade_mod.audit.bind(chain)
    old_enabled = facade_mod.audit.enabled
    facade_mod.audit.enabled = True
    facade_mod.audit.reset_counters()
    yield facade_mod.audit
    facade_mod.audit.bind(previous)
    facade_mod.audit.enabled = old_enabled


def _actions(chain):
    chain.flush()
    return [e.action for e in chain.entries()]


# ════════════════════════════════════════════════════════════
#  1. Agent 侧治理动作入链（真实调用路径）
# ════════════════════════════════════════════════════════════


class TestAgentSideHooks:
    def test_approval_lifecycle_recorded(self, bound, chain, tmp_path):
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "approval_records.jsonl"))
        rec = flow.submit("skill", "s-1", action="params_submit", actor="human",
                          description="参数调整")
        flow.approve(rec.record_id, actor="reviewer", note="ok")
        rec2 = flow.submit("prompt", "p-2", action="prompt_apply", actor="agent")
        flow.reject(rec2.record_id, actor="reviewer", reason="风险未评估")

        actions = _actions(chain)
        assert actions == ["approval.submit", "approval.approved",
                           "approval.submit", "approval.rejected"]
        rows = chain.entries()
        assert rows[0].actor == "human" and rows[0].subject == "skill:s-1"
        assert rows[1].actor == "reviewer"
        assert rows[3].payload["payload"]["reason"] == "风险未评估"
        for r in rows:
            assert r.source == "agent"

    def test_approval_merged_and_archived_recorded(self, bound, chain, tmp_path):
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "approval_records.jsonl"))
        rec = flow.submit("skill", "s-2", action="params_submit", actor="human",
                          applier=lambda: None)
        flow.approve(rec.record_id, actor="reviewer")
        flow.merge(rec.record_id, actor="reviewer")
        actions = _actions(chain)
        assert "approval.merged" in actions

    def test_review_waiver_publish_recorded(self, bound, chain, tmp_path,
                                            monkeypatch):
        from agent.skills_mgmt import review_gate
        monkeypatch.setattr(review_gate, "_audit_file",
                            lambda: str(tmp_path / "waiver.jsonl"))
        review_gate.audit_exemption("skill-x", actor="human", reason="紧急发布")
        chain.flush()
        rows = chain.entries()
        assert rows[0].action == "skill.review_waiver_publish"
        assert rows[0].subject == "skill:skill-x"
        assert rows[0].actor == "human"

    def test_descriptor_registry_changes_recorded(self, bound, chain, tmp_path):
        from agent.descriptors.registry import DescriptorRegistry
        reg = DescriptorRegistry(path=str(tmp_path / "desc.json"))
        reg.register({"meta": {"id": "cp.demo.probe"},
                      "origin": {"source_type": "builtin", "source_id": "demo",
                                 "provenance": "declared"},
                      "capability": {"name": "probe", "description": "d",
                                     "input_schema": {"type": "object"}}},
                     actor="agent", reason="集成测试")
        reg.unregister("cp.demo.probe", actor="human")
        actions = _actions(chain)
        assert actions == ["descriptor.register", "descriptor.unregister"]
        assert chain.entries()[0].subject == "capability:cp.demo.probe"

    def test_lineage_append_recorded(self, bound, chain, tmp_path):
        from agent.skills_mgmt.lineage import EvolutionArchive, EvolutionRecord
        archive = EvolutionArchive(
            active_path=str(tmp_path / "evolution_archive.jsonl"))
        rec = EvolutionRecord(object_id="skill:y", parent_version="1.0.0",
                              new_version="1.1.0", strategy="meta_edit",
                              decision="committed")
        archive.append(rec)
        chain.flush()
        row = chain.entries()[0]
        assert row.action == "lineage.append"
        assert row.subject == "skill:y"
        assert row.payload["payload"]["new_version"] == "1.1.0"

    def test_trace_close_and_failure_recorded(self, bound, chain, tmp_path):
        from agent.observability.trace_v2 import TraceFacade
        tf = TraceFacade(str(tmp_path / "trace.db"))
        tid = tf.start(task_id="t-1", workspace_id="ws-1", subject_id="op-1")
        tf.record("cp.builtin.read_file", args={"path": "a.py"},
                  output={"ok": False, "error": "boom"})
        tf.finish(status="success", output={"ok": True})
        tf._store.stop(timeout=2.0)
        actions = _actions(chain)
        assert "trace.tool.error" in actions
        assert "trace.closed" in actions
        closed = [e for e in chain.entries() if e.action == "trace.closed"][0]
        assert closed.trace_id == tid
        assert closed.workspace_id == "ws-1"

    def test_trace_redact_event_recorded_only_when_changed(self, bound, chain,
                                                           tmp_path):
        from agent.observability.trace_v2 import TraceFacade
        tf = TraceFacade(str(tmp_path / "trace.db"))
        tf.start(task_id="t-2")
        tf.record("cp.builtin.read_file", args={"api_key": "sk-test-REDACT-LEFTOVER"})
        tf.finish(status="success")
        tf._store.stop(timeout=2.0)
        redacts = [e for e in chain.entries() if e.action == "trace.redact"]
        assert redacts, "脱敏动作应入链（S2-01 遗留 #3）"
        raw = json.dumps([e.payload for e in redacts], ensure_ascii=False)
        assert "sk-test-REDACT-LEFTOVER" not in raw
        assert redacts[0].payload["payload"]["values_recorded"] is False

    def test_no_redact_event_when_nothing_to_redact(self, bound, chain, tmp_path):
        from agent.observability.trace_v2 import TraceFacade
        tf = TraceFacade(str(tmp_path / "trace.db"))
        tf.start(task_id="t-3")
        tf.record("cp.builtin.read_file", args={"path": "plain.py"})
        tf.finish(status="success")
        tf._store.stop(timeout=2.0)
        assert not [e for e in chain.entries() if e.action == "trace.redact"]

    def test_logging_utils_sensitive_operations_recorded(self, bound, chain):
        """配置访问/修改、权限变更、认证、密钥访问 → 链上留痕（策略变更面）"""
        from agent.logging_utils import AuditLogger as SensitiveAuditLogger
        lg = SensitiveAuditLogger()
        lg.log_config_access("llm_api_key", user="admin")
        lg.log_config_modification("llm_model", user="admin")
        lg.log_secure_config_access("llm_api_key", success=False, user="admin")
        lg.log_encryption_key_access(success=True, user="admin")
        lg.log_permission_change("grant", "shell_execute", user="admin")
        lg.log_authentication("admin", success=False, ip_address="10.0.0.7")
        actions = _actions(chain)
        assert "config.access" in actions
        assert "config.modify" in actions
        assert "config.secure_access" in actions
        assert "config.encryption_key_access" in actions
        assert "permission.change" in actions
        auth = [e for e in chain.entries() if e.action == "auth.attempt"][0]
        assert auth.actor == "admin"
        assert auth.payload["status"] == "failed"
        # 客户端 IP 沿用仓库既有脱敏口径（掩码保留网段，原文不入链）
        assert auth.payload["payload"]["ip"] == "10.0.xxx.xxx"
        assert "10.0.0.7" not in json.dumps(auth.payload, ensure_ascii=False)

    def test_logging_utils_sensitive_operation_records_keys_not_values(self, bound,
                                                                      chain):
        from agent.logging_utils import AuditLogger as SensitiveAuditLogger
        secret = "sk-test-SENSITIVE-OP-LEAK"
        lg = SensitiveAuditLogger()
        lg.log_sensitive_operation("rotate_key", details={"api_key": secret,
                                                          "scope": "llm"},
                                   user="admin")
        chain.flush()
        rows = chain.entries()
        assert rows[0].action == "sensitive.operation"
        raw = json.dumps([e.payload for e in rows], ensure_ascii=False)
        assert secret not in raw
        assert rows[0].payload["payload"]["detail_keys"] == ["api_key", "scope"]

    def test_env_config_change_recorded(self, bound, chain, tmp_path, monkeypatch):
        """设置写（.env 配置变更）→ 链上留痕（与 UI config.write 同表）"""
        from agent import env_config_manager as ecm
        monkeypatch.setattr(ecm.EnvConfigManager, "_get_audit_log_path",
                            lambda self: tmp_path / "config_audit.jsonl")
        mgr = ecm.EnvConfigManager()
        mgr._audit_log("set", "llm_model", "old", "new")
        chain.flush()
        rows = chain.entries()
        assert rows[0].action == "config.env_set"
        assert rows[0].subject == "env:llm_model"
        assert rows[0].source == "agent"


# ════════════════════════════════════════════════════════════
#  2. 真实 UI 写路由 → 同一张表（P7.2-24 审计平权）
# ════════════════════════════════════════════════════════════


class _FakeSvc:
    def delete(self, skill_id):
        return True

    def set_enabled(self, skill_id, enabled):
        return SimpleNamespace(id=skill_id,
                               model_dump=lambda: {"id": skill_id, "enabled": enabled})


def _build_real_ui_app(chain, facade=None):
    """真实路由（skills_mgmt DELETE + config POST）+ 全局写路由审计包装

    说明：`@audit_action` 显式审计走**进程级门面**（与全局包装同一台账），
    故测试里必须先绑定进程门面（`bound` 夹具），两条路径才落同一条链。
    """
    from agent.server_routes import routes_config, routes_skills_mgmt

    app = Flask("s2_02_real_ui")
    app.config["PROPAGATE_EXCEPTIONS"] = False
    facade = facade or AuditFacade(chain=chain, enabled=True, db_path=chain.db_path)
    UIAuditRecorder(facade=facade, skip_prefixes=()).register(app)
    return app, facade, routes_config, routes_skills_mgmt


class _FakeYunshu:
    @staticmethod
    def get_config():
        return {"provider": "deepseek"}

    @staticmethod
    def configure_llm(**kwargs):
        return {"ok": True, "provider": kwargs.get("provider", "")}


class _FakeNCM:
    @staticmethod
    def _save_secure(key, value):
        return True


class _FakeSessionMgr:
    @staticmethod
    def get_current_id():
        return "s-1"

    @staticmethod
    def create_session(name):
        return {"id": "s-1"}

    @staticmethod
    def clear_messages(sid):
        return None


class _FakeHistory:
    @staticmethod
    def clear():
        return None


class TestUIAuditEquality:
    def test_skill_delete_route_audited_with_semantic_action(self, bound, chain,
                                                             monkeypatch):
        app, _facade, _cfg, skills_mod = _build_real_ui_app(chain, bound)
        monkeypatch.setattr(skills_mod, "_svc", lambda: _FakeSvc())
        skills_mod.register_routes(app, SimpleNamespace())
        r = app.test_client().delete("/api/skills-mgmt/skill-abc",
                                     headers={"X-Audit-Actor": "admin@yunshu"})
        assert r.status_code == 200
        chain.flush()
        rows = chain.entries()
        assert len(rows) == 1
        assert rows[0].action == "skill.delete"
        assert rows[0].subject == "skill-abc"
        assert rows[0].source == "ui"
        assert rows[0].actor == "admin@yunshu"

    def test_config_write_route_audited_and_key_not_stored(self, bound, chain):
        app, _facade, cfg_mod, _skills = _build_real_ui_app(chain, bound)
        state = SimpleNamespace(Yunshu=_FakeYunshu, session_mgr=_FakeSessionMgr,
                                network_config_mgr=_FakeNCM, search_engine=None,
                                chat_history=_FakeHistory())
        cfg_mod.register_routes(app, state)
        secret = "sk-test-CONFIG-LEAK-CHECK"
        r = app.test_client().post("/api/config", json={"provider": "deepseek",
                                                        "api_key": secret})
        assert r.status_code == 200
        chain.flush()
        rows = chain.entries()
        assert rows and rows[0].action == "config.write"
        raw = json.dumps([e.payload for e in rows], ensure_ascii=False)
        assert secret not in raw

    def test_ui_and_agent_share_one_table_and_chain(self, bound, chain, tmp_path,
                                                    monkeypatch):
        """验收核心：UI 操作与 Agent 操作同表可查、同链可验"""
        # Agent 侧
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "approval_records.jsonl"))
        rec = flow.submit("skill", "agent-skill", action="params_submit", actor="agent")
        flow.approve(rec.record_id, actor="reviewer")

        # UI 侧（真实路由）
        app, _facade, _cfg, skills_mod = _build_real_ui_app(chain, bound)
        monkeypatch.setattr(skills_mod, "_svc", lambda: _FakeSvc())
        skills_mod.register_routes(app, SimpleNamespace())
        app.test_client().delete("/api/skills-mgmt/ui-skill")
        app.test_client().post("/api/skills-mgmt/ui-skill/toggle",
                               json={"enabled": True})

        chain.flush()
        rows = chain.entries()
        sources = [r.source for r in rows]
        assert "agent" in sources and "ui" in sources
        # 同表：一张 audit_chain 表、一条 seq 链
        assert [r.seq for r in rows] == list(range(1, len(rows) + 1))
        assert chain.verify_chain().ok is True
        # 同表可查：一条 SQL 同时取回两类来源
        conn = sqlite3.connect(chain.db_path)
        try:
            agent_n = conn.execute(
                "SELECT COUNT(*) FROM audit_chain WHERE source='agent'").fetchone()[0]
            ui_n = conn.execute(
                "SELECT COUNT(*) FROM audit_chain WHERE source='ui'").fetchone()[0]
        finally:
            conn.close()
        assert agent_n >= 2 and ui_n >= 2
        # 链上相邻（同一条链）：UI 记录的前驱 = Agent 记录的 self_hash
        ui_rows = [r for r in rows if r.source == "ui"]
        agent_rows = [r for r in rows if r.source == "agent"]
        assert ui_rows[0].seq == agent_rows[-1].seq + 1
        assert ui_rows[0].prev_hash == agent_rows[-1].self_hash

    def test_daily_root_covers_both_sources(self, bound, chain, tmp_path, monkeypatch):
        from agent.skills_mgmt.approval import ApprovalFlow
        flow = ApprovalFlow(records_path=str(tmp_path / "approval_records.jsonl"))
        flow.submit("skill", "s", action="params_submit", actor="agent")
        app, _f, _c, skills_mod = _build_real_ui_app(chain, bound)
        monkeypatch.setattr(skills_mod, "_svc", lambda: _FakeSvc())
        skills_mod.register_routes(app, SimpleNamespace())
        app.test_client().delete("/api/skills-mgmt/x")
        chain.flush()

        root = chain.daily_merkle_root()
        assert root.leaf_count == len(chain.entries()) >= 2
        rep = chain.verify_daily_root(root.date)
        assert rep.ok and rep.entries_verified == root.leaf_count

    def test_ui_write_route_failure_still_audited(self, bound, chain, monkeypatch):
        app, _facade, _cfg, skills_mod = _build_real_ui_app(chain, bound)

        class _Boom:
            def delete(self, skill_id):
                raise RuntimeError("svc down")

        monkeypatch.setattr(skills_mod, "_svc", lambda: _Boom())
        skills_mod.register_routes(app, SimpleNamespace())
        r = app.test_client().delete("/api/skills-mgmt/boom")
        assert r.status_code == 500
        chain.flush()
        rows = chain.entries()
        assert rows, "写路由失败也必须留痕"
        # 视图捕获异常后返回 500 → 显式审计以 status=error + status_code=500 落账
        assert rows[0].action == "skill.delete"
        assert rows[0].payload["status"] == "error"
        assert rows[0].payload["status_code"] == 500


# ════════════════════════════════════════════════════════════
#  3. 验签 CLI（含混沌演练自动化形态）
# ════════════════════════════════════════════════════════════


class TestVerifyCli:
    @staticmethod
    def _cli():
        from scripts.verify_audit_chain import main as cli_main
        return cli_main

    def _seed(self, chain):
        for i in range(6):
            chain.append(f"act{i}", "alice", f"skill:{i}",
                         source="ui" if i % 2 else "agent")
        chain.flush()
        return chain.db_path

    def test_clean_chain_returns_zero(self, chain, capsys):
        db = self._seed(chain)
        code = self._cli()(["--db", db, "--roots", chain.roots_path, "--stats"])
        out = capsys.readouterr().out
        assert code == 0
        assert "OK" in out and "链式校验" in out

    def test_tampered_chain_returns_one_and_reports_seq(self, chain, capsys):
        db = self._seed(chain)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_chain SET actor='mallory' WHERE seq=4")
        conn.commit()
        conn.close()
        code = self._cli()(["--db", db, "--roots", chain.roots_path])
        out = capsys.readouterr().out
        assert code == 1
        assert "TAMPERED" in out and "seq=4" in out

    def test_json_output_parsable(self, chain, capsys):
        db = self._seed(chain)
        code = self._cli()(["--db", db, "--json", "--stats"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 0
        assert payload["chain"]["ok"] is True
        assert payload["stats"]["total"] == 6
        assert payload["stats"]["by_source"] == {"agent": 3, "ui": 3}

    def test_missing_db_is_usage_error(self, tmp_path, capsys):
        code = self._cli()(["--db", str(tmp_path / "nope.db")])
        assert code == 2
        assert "不存在" in capsys.readouterr().out

    def test_missing_db_allowed_as_empty(self, tmp_path, capsys):
        code = self._cli()(["--db", str(tmp_path / "nope.db"), "--allow-missing"])
        assert code == 0

    def test_anchor_seq_verification(self, chain, capsys):
        db = self._seed(chain)
        code = self._cli()(["--db", db, "--seq", "5"])
        assert code == 0
        assert "已校验 2 条" in capsys.readouterr().out

    def test_roots_check_all_ok(self, chain, capsys):
        db = self._seed(chain)
        chain.daily_merkle_root()
        code = self._cli()(["--db", db, "--roots", chain.roots_path,
                            "--roots-check", "all"])
        out = capsys.readouterr().out
        assert code == 0 and "每日根" in out

    def test_tampered_root_reported(self, chain, capsys):
        db = self._seed(chain)
        root = chain.daily_merkle_root()
        os.chmod(chain.roots_path, 0o644)
        with open(chain.roots_path, "w", encoding="utf-8") as f:
            rec = root.to_dict()
            rec["root_hash"] = "0" * 64
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        code = self._cli()(["--db", db, "--roots", chain.roots_path,
                            "--roots-check", root.date])
        assert code == 1

    def test_tamper_detected_from_cli_marks_position(self, chain, capsys):
        """§11.10 混沌演练：向链中间注入一条篡改 → CLI 报告注入 seq 及后续失败"""
        db = self._seed(chain)
        conn = sqlite3.connect(db)
        conn.execute("UPDATE audit_chain SET action='forged.action' WHERE seq=3")
        conn.commit()
        conn.close()
        code = self._cli()(["--db", db, "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert code == 1
        assert payload["chain"]["first_bad_seq"] == 3
        bad = [b["seq"] for b in payload["chain"]["bad_seqs"]]
        assert bad == [3, 4, 5, 6]
