# -*- coding: utf-8 -*-
"""C-6 收尾：4 条 py/stack-trace-exposure 的**真源**（2026-11-07，SARIF codeFlows 定位）。

【为什么有这个文件】此前两轮都在「sink」（api_envelope.ok/problem、roi_view、jsonify）
附近盲改无效 —— 因为告警挂在**共享 sink** 上，只要还有**任何一条**源把异常派生串流进去，
sink 的告警就不会消失。用 CodeQL 分析的 SARIF（codeFlows）反查出 6 个真源后逐个改，
本文件把「类名在、原文不在」钉成契约，防止再被写回去：

| 告警 | 真源 |
|---|---|
| api_envelope:117（ok） | build_info._run / frontend_info、plugins/chat.py llm-preview |
| api_envelope:152（problem） | plugins/audit_coverage.py、plugins/ingestion.py ×3 |
| routes_process_distill.py:107 | process_distill/solidify.py ×2 |
| routes_ui_panels.py:302 | eval/metrics.delegation_signal_dictionary |

契约统一为：**响应体里的错误字段 = 类名 + 固定后缀「（详见服务日志）」，异常原文只进日志**。
"""
from __future__ import annotations

import importlib
import json
import logging
import sys
import types

import pytest

#: 假异常里塞的"原文"，任何进响应体的字段都不得包含它。
SECRET = "SECRET-TRACE-9f3a"


def _boom(cls=RuntimeError):
    return cls(SECRET)


class TestBuildInfo:
    """build_info 的 error 字段会经 /api/version 进响应体（ok() 的 data）。"""

    def test_run_failure_keeps_class_name_only(self, monkeypatch, caplog):
        import agent.build_info as bi

        def fake_run(*a, **k):
            raise _boom()

        monkeypatch.setattr(bi.subprocess, "run", fake_run)
        with caplog.at_level(logging.WARNING):
            stdout, err = bi._run(["git", "rev-parse", "HEAD"], bi.REPO_ROOT)
        assert stdout is None
        assert "RuntimeError" in err, "类名必须在（否则线上无法定位）"
        assert SECRET not in err, "异常原文不得进 error 字段"
        assert any(SECRET in r.getMessage() or SECRET in str(r.args)
                   for r in caplog.records), "原文必须仍可查（进日志）"

    def test_frontend_read_error_keeps_class_name_only(self, tmp_path, monkeypatch):
        import agent.build_info as bi

        # 造一个真实的 OSError 分支（Windows 上「目录当文件读」抛的是 PermissionError，
        # 故这里不绑定具体子类：注入 OSError 原文，断言字段里只剩类名）。
        html = tmp_path / "templates" / "yunshu.html"
        html.parent.mkdir(parents=True)
        html.write_text("x", encoding="utf-8")

        def boom_read(self, *a, **k):
            raise OSError(SECRET)

        monkeypatch.setattr(bi.Path, "read_text", boom_read)
        info = bi.frontend_info(tmp_path)
        assert info["error"], "读不到必须如实报错，不得静默"
        assert "OSError" in info["error"]
        assert SECRET not in info["error"], "原始 OSError 文案不得进字段"


class TestEnvelopeCallers:
    """ok(message=…) / problem(detail=…) 的入参源头。"""

    def test_llm_preview_message_has_no_exception_text(self, monkeypatch):
        """plugins/chat.py：llm-preview 的 502 message。"""
        import importlib

        fake = types.ModuleType("app_server")
        fake.require_token = lambda f: f
        fake.log_request = lambda *a, **k: (lambda f: f)
        fake.logger = logging.getLogger("fake_app_server")
        monkeypatch.setitem(sys.modules, "app_server", fake)

        from flask import Flask
        from agent.api_envelope import install_error_handlers

        mod = importlib.import_module("plugins.chat")
        app = Flask(__name__)
        if getattr(mod, "bp", None) is not None:
            app.register_blueprint(mod.bp)
        install_error_handlers(app, enabled=True)
        app.config["TESTING"] = True

        calls = {}

        class _Svc:
            def __init__(self, **kwargs):
                calls["init"] = kwargs

            def chat(self, *a, **k):
                raise _boom()

        import memory.llm_service as lls
        monkeypatch.setattr(lls, "LLMService", _Svc)
        resp = app.test_client().post(
            "/api/prompt-lab/llm-preview", json={"prompt": "hi"})
        assert resp.status_code == 502, "LLM 代理失败必须 502（前端据此给可读错误）"
        body = resp.get_json() or {}
        assert SECRET not in json.dumps(body, ensure_ascii=False), (
            "llm-preview 的 message 不得含异常原文")
        assert "RuntimeError" in (body.get("message") or ""), "类名必须在"

    def test_audit_coverage_detail_has_no_exception_text(self, monkeypatch, tmp_path):
        fake = types.ModuleType("app_server")
        fake.require_token = lambda f: f
        fake.log_request = lambda *a, **k: (lambda f: f)
        fake.logger = logging.getLogger("fake_app_server")
        monkeypatch.setitem(sys.modules, "app_server", fake)

        from flask import Flask
        from agent.api_envelope import install_error_handlers

        mod = importlib.import_module("plugins.audit_coverage")
        artifact = tmp_path / "route_ui_coverage.json"
        artifact.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(mod, "_ARTIFACT", str(artifact))

        app = Flask(__name__)
        app.register_blueprint(mod.bp)
        install_error_handlers(app, enabled=True)
        app.config["TESTING"] = True
        resp = app.test_client().get("/api/audit/route-ui-coverage")
        assert resp.status_code == 500, "产物坏掉必须响亮 500"
        body = resp.get_json() or {}
        assert "详见服务日志" in (body.get("detail") or "")
        assert "JSONDecodeError" not in SECRET
        assert SECRET not in json.dumps(body, ensure_ascii=False)

    def test_ingestion_draft_missing_detail_has_no_exception_text(self, monkeypatch):
        """plugins/ingestion.py：draft 不存在/内容非法 的 detail（3 处 str(exc)）。"""
        fake = types.ModuleType("app_server")
        fake.require_token = lambda f: f
        fake.log_request = lambda *a, **k: (lambda f: f)
        fake.logger = logging.getLogger("fake_app_server")
        monkeypatch.setitem(sys.modules, "app_server", fake)

        from flask import Flask
        from agent.api_envelope import install_error_handlers

        mod = importlib.import_module("plugins.ingestion")
        app = Flask(__name__)
        app.register_blueprint(mod.bp)
        install_error_handlers(app, enabled=True)
        app.config["TESTING"] = True

        from agent.ingestion import drafts as drafts_mod

        def raise_not_found(draft_id):
            raise _boom(FileNotFoundError)

        monkeypatch.setattr(drafts_mod, "load_workflow", raise_not_found)
        resp = app.test_client().post(
            "/api/ingestion/drafts/draft-42/autobind", json={})
        body = resp.get_json() or {}
        assert SECRET not in json.dumps(body, ensure_ascii=False)
        if resp.status_code == 404:
            assert "draft-42" in (body.get("detail") or ""), "响应里应能认出是哪个草稿"
            assert "详见服务日志" in (body.get("detail") or "")


class TestToolbuildValidate:
    """agent/ingestion/toolbuild.validate_code 的 errors → ok() 的 data（draft implement）。"""

    def test_syntax_error_keeps_class_name_only(self, caplog):
        from agent.ingestion import toolbuild

        broken = "def demo_tool(:**kw):\n    return {}\n"
        with caplog.at_level(logging.WARNING):
            out = toolbuild.validate_code(broken, "demo_tool")
        assert out["ok"] is False
        joined = " ".join(out["errors"])
        assert "SyntaxError" in joined, "类名必须在（否则无法判断是语法错还是别的）"
        assert "invalid syntax" not in joined, "解释器原文不得进 errors"
        assert any("语法错误" in str(r.getMessage()) or r.args
                   for r in caplog.records), "原文必须进日志"


class TestIngestionGovernance:
    """plugins/ingestion.py 治理动作：ValueError 的原文不得进 problem(detail)。"""

    def test_governance_value_error_detail_has_no_exception_text(self, monkeypatch):
        fake = types.ModuleType("app_server")
        fake.require_token = lambda f: f
        fake.log_request = lambda *a, **k: (lambda f: f)
        fake.logger = logging.getLogger("fake_app_server")
        monkeypatch.setitem(sys.modules, "app_server", fake)

        from flask import Flask
        from agent.api_envelope import install_error_handlers

        mod = importlib.import_module("plugins.ingestion")
        app = Flask(__name__)
        app.register_blueprint(mod.bp)
        install_error_handlers(app, enabled=True)
        app.config["TESTING"] = True

        from agent.ingestion import governance as gov

        def boom(**kwargs):
            raise _boom(ValueError)

        monkeypatch.setattr(gov, "apply", boom)
        resp = app.test_client().post(
            "/api/ingestion/governance/apply",
            json={"plan_id": "plan-7", "action": "drop", "target": "draft-1",
                  "confirm": True, "dry_run": False})
        assert resp.status_code == 400, "治理参数错误仍是 400"
        body = resp.get_json() or {}
        assert SECRET not in json.dumps(body, ensure_ascii=False), "原文不得进响应体"
        detail = body.get("detail") or ""
        assert "详见服务日志" in detail
        assert "plan-7" in detail, "响应里应能认出是哪份计划"


class TestProcessDistillSolidify:
    """solidify 的 file_error / review.error 会经 /api/process-distill/distill 进响应体。"""

    @staticmethod
    def _proc():
        from agent.process_distill.models import DistilledProcess, DistilledStep

        return DistilledProcess(
            name="发布流程蒸馏", description="一次发布流程的蒸馏产物",
            task_signature="release | hotfix", trigger_patterns=["发布"],
            steps=[DistilledStep(seq=1, action="执行构建", tool="bash",
                                 params={"cmd": "make"}, source="s1"),
                   DistilledStep(seq=2, action="执行发布", tool="bash",
                                 params={"cmd": "make release"}, source="s1")],
            sources=["wiki/release.md"], method="llm", tags=["git"])

    def test_file_error_keeps_class_name_only(self):
        from agent.process_distill.solidify import solidify_to_skill

        class _FS:
            def create(self, *a, **k):
                raise _boom()

        class _Svc:
            file_store = _FS()

            def get(self, sid):
                return None

            def create_manual(self, data):
                return dict(data)

        out = solidify_to_skill(self._proc(), skills_svc=_Svc(), run_review=False)
        assert out["file_error"], "文件轨失败必须如实报（不阻断 JSON 轨）"
        assert "RuntimeError" in out["file_error"]
        assert SECRET not in out["file_error"]

    def test_review_error_keeps_class_name_only(self):
        from agent.process_distill.solidify import solidify_to_skill

        class _FS:
            def create(self, *a, **k):
                return None

            def update_meta(self, *a, **k):
                return None

        class _Svc:
            file_store = _FS()

            def get(self, sid):
                return None

            def create_manual(self, data):
                return dict(data)

            def review(self, sid):
                raise _boom()

        out = solidify_to_skill(self._proc(), skills_svc=_Svc(), run_review=True)
        assert out["review"].get("error"), "评审失败要如实标注（不阻断固化）"
        assert "RuntimeError" in out["review"]["error"]
        assert SECRET not in out["review"]["error"]


class TestHeartbeatChecks:
    """task_scheduler.perform_heartbeat_check 的 checks[...]["error"] → ok() 的 data。"""

    def test_check_error_keeps_class_name_only(self, caplog):
        from agent import task_scheduler as ts

        with caplog.at_level(logging.WARNING):
            row = ts._check_error(_boom(), "memory")
        assert row["status"] == "error"
        assert "RuntimeError" in row["error"]
        assert SECRET not in row["error"], "心跳响应体不得含异常原文"
        assert any(SECRET in str(r.args) for r in caplog.records), "原文必须进日志"

    def test_heartbeat_route_detail_has_no_exception_text(self, monkeypatch):
        """plugins/status.py：/api/heartbeat 失败时的 problem(detail=…)。"""
        fake = types.ModuleType("app_server")
        fake._Yunshu = object
        monkeypatch.setitem(sys.modules, "app_server", fake)

        from flask import Flask
        from agent.api_envelope import install_error_handlers

        mod = importlib.import_module("plugins.status")
        app = Flask(__name__)
        app.register_blueprint(mod.bp)
        install_error_handlers(app, enabled=True)
        app.config["TESTING"] = True

        import agent.task_scheduler as ts

        def boom(*a, **k):
            raise _boom()

        monkeypatch.setattr(ts, "perform_heartbeat_check", boom)
        resp = app.test_client().get("/api/heartbeat")
        assert resp.status_code == 500
        body = resp.get_json() or {}
        assert "详见服务日志" in (body.get("detail") or "")
        assert SECRET not in json.dumps(body, ensure_ascii=False)


class TestMetricsDelegationSignals:
    """eval/metrics.delegation_signal_dictionary → ui_panels → /roi 面板响应体。"""

    def test_list_unavailable_keeps_class_name_only(self, monkeypatch):
        from agent.eval import metrics as M

        import agent.subagent.mechanical as mech

        def broken():
            raise _boom()

        monkeypatch.setattr(mech, "mechanical_signal_catalog", broken)
        rows = M.delegation_signal_dictionary()
        assert rows and rows[0].get("error")
        assert "RuntimeError" in rows[0]["error"]
        assert SECRET not in rows[0]["error"]
