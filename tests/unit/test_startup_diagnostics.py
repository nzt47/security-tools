"""启动期降级可见性单测（TASK-08 子工作流 D / E1g）

【本文件证明什么】
    1. 启动期模块加载失败会**被登记**且**结构化上报**（不再静默）；
    2. 上报**不抛异常**（D4：降级启动不得被诊断拖垮）；
    3. `audit_expected_modules()` 能机械发现"被引用但不存在的模块"；
    4. 当前仓库的真实结论：`app_server.py` 引用但不可导入的模块**恰好只有 1 个**
       （`agent.api_gateway_flask`）—— 与任务书"5 个模块静默失败"的描述不符，
       见下。

【实测更正：任务书 E1g 的框架有误】
    · 任务书依据 `server_health.log` 的行号 573/657/658/659/707 称"5 个路由模块
      静默加载失败"。行号确实对得上，但**该日志是陈旧产物**：
      最后写入 2026-08-28，而 `app_server.py` 最后修改于 2026-09-20
      —— 日志记录的是一个**旧版本**的启动过程。
    · 按**当前源码**，那 5 个里 4 个已不复现（模块被删除/改名/改家），
      唯一仍缺失的是 `agent/api_gateway_flask`。本文件的
      `test_current_repo_has_exactly_one_missing_module` 把这一事实**锁死**。
    · 任务书称失败"静默"。对**日志里的**那 5 条而言并不准确（都是 ERROR 级）。
      真正静默的是**今天**的 `api_gateway_flask` 分支：它被刻意降级为 `debug`
      （`app_server.py:1522`），生产日志里一行都没有 —— 这才是要修的那一处。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from agent import startup_diagnostics as sd

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean_registry():
    """每个用例前后清空降级表（它是模块级状态，会跨用例泄漏）"""
    sd.reset_degradations()
    yield
    sd.reset_degradations()


class TestDegradationRecording:
    def test_record_and_read_back(self):
        sd.record_degradation("agent.some_missing_mod", kind="module_missing",
                              purpose="某可选能力", impact="该能力不可用",
                              error="No module named 'agent.some_missing_mod'")
        items = sd.degradations()
        assert len(items) == 1
        assert items[0]["module"] == "agent.some_missing_mod"
        assert items[0]["impact"] == "该能力不可用"
        assert sd.is_degraded() is True

    def test_same_module_recorded_once_with_count(self):
        """同一模块重复登记只保留一条并累加次数（避免刷屏）"""
        for _ in range(3):
            sd.record_degradation("agent.dup", kind="module_missing")
        items = sd.degradations()
        assert len(items) == 1
        assert items[0]["count"] == 3

    def test_degradations_returns_copies_not_live_state(self):
        """返回副本：调用方改不坏内部登记表（不外泄可变内部对象）"""
        sd.record_degradation("agent.x", kind="module_missing")
        snapshot = sd.degradations()
        snapshot[0]["module"] = "tampered"          # type: ignore[index]
        assert sd.degradations()[0]["module"] == "agent.x"

    def test_summary_is_json_serializable(self):
        """摘要必须可安全序列化（只含本模块自建的普通数据）"""
        import json
        sd.record_degradation("agent.y", kind="module_missing",
                              purpose="p", impact="i", error="e")
        json.dumps(sd.summary())                    # 不抛即通过


class TestEmitNeverBlocksStartup:
    """D4 的核心断言：诊断层出任何问题都不得阻断启动"""

    def test_emit_with_no_degradation_logs_info(self):
        calls = []

        class _L:
            def info(self, *a, **k):
                calls.append(("info", a))
            def error(self, *a, **k):
                calls.append(("error", a))

        report = sd.emit_startup_report(_L())
        assert report["degraded"] is False
        assert calls and calls[0][0] == "info"

    def test_emit_with_degradation_logs_error_and_details(self):
        calls = []

        class _L:
            def info(self, *a, **k):
                calls.append(("info", a))
            def error(self, *a, **k):
                calls.append(("error", a))

        sd.record_degradation("agent.api_gateway_flask", kind="module_missing",
                              purpose="API 网关", impact="/api/open/* 不可用",
                              error="No module named 'agent.api_gateway_flask'")
        report = sd.emit_startup_report(_L())

        assert report["degraded"] is True
        assert report["count"] == 1
        errors = [c for c in calls if c[0] == "error"]
        assert len(errors) >= 2, "至少一条汇总 + 一条明细"
        joined = " ".join(str(c) for c in errors)
        assert "agent.api_gateway_flask" in joined
        assert "/api/open/*" in joined

    def test_logger_that_raises_does_not_break_startup(self):
        """★D4：连 logger 都坏掉时，`emit_startup_report` 也**不得抛异常**"""
        class _BrokenLogger:
            def info(self, *a, **k):
                raise RuntimeError("logger exploded")
            def error(self, *a, **k):
                raise RuntimeError("logger exploded")

        sd.record_degradation("agent.z", kind="module_missing")
        report = sd.emit_startup_report(_BrokenLogger())    # 不应抛
        assert report["degraded"] is True

    def test_record_never_raises_on_bad_input(self):
        """`record_degradation` 自身健壮（None 也不炸）"""
        sd.record_degradation(None)                          # type: ignore[arg-type]
        sd.record_degradation("ok", kind=None)               # type: ignore[arg-type]
        assert isinstance(sd.degradations(), tuple)


class TestAuditExpectedModules:
    def test_detects_missing_module(self, tmp_path):
        """审计能发现被引用但不存在的模块（负例，确定性）"""
        fake = tmp_path / "fake_app.py"
        fake.write_text(
            "import os\n"
            "from agent.definitely_not_a_real_module_xyz import thing\n"
            "import json\n",
            encoding="utf-8",
        )
        missing = sd.audit_expected_modules(fake)
        names = [m["module"] for m in missing]
        assert "agent.definitely_not_a_real_module_xyz" in names
        # stdlib 模块不得误报
        assert "os" not in names and "json" not in names

    def test_import_style_detected(self, tmp_path):
        """`import agent.X` 形态也要被发现"""
        fake = tmp_path / "fake_app2.py"
        fake.write_text("import agent.also_not_real_zyx\n", encoding="utf-8")
        names = [m["module"] for m in sd.audit_expected_modules(fake)]
        assert "agent.also_not_real_zyx" in names

    def test_unparseable_file_returns_empty_not_crash(self, tmp_path):
        """解析失败时返回空表而不是抛（审计失败不得阻断启动）"""
        bad = tmp_path / "broken.py"
        bad.write_text("def (:::\n", encoding="utf-8")
        assert sd.audit_expected_modules(bad) == []
        assert sd.audit_expected_modules(tmp_path / "nope.py") == []

    def test_audit_and_record_registers_findings(self, tmp_path):
        fake = tmp_path / "fake_app3.py"
        fake.write_text("from agent.never_real_abc import x\n", encoding="utf-8")
        missing = sd.audit_and_record(fake)
        assert missing
        assert sd.is_degraded() is True


class TestCurrentRepoGroundTruth:
    """把"当前仓库的真实情况"锁死 —— 防止任务书的错误描述被当成事实沿用"""

    def test_current_repo_has_exactly_one_missing_module(self):
        """★实测：`app_server.py` 引用但不可导入的模块**恰好 1 个**

        即 `agent.api_gateway_flask`。这直接反驳任务书"5 个模块静默加载失败"
        （那是 2026-08-28 的旧日志记录的状态）。
        """
        missing = sd.audit_expected_modules(REPO_ROOT / "app_server.py")
        names = sorted(m["module"] for m in missing)
        assert names == ["agent.api_gateway_flask"], (
            f"当前应恰好只有 api_gateway_flask 缺失，实际: {names}")

    def test_api_gateway_flask_file_really_absent(self):
        """该文件确实不存在（判定"删引用还是补文件"的事实前提）"""
        assert not (REPO_ROOT / "agent" / "api_gateway_flask.py").exists()
        assert (REPO_ROOT / "agent" / "api_gateway.py").exists()

    def test_api_gateway_flask_import_is_not_debug_level(self):
        """★E1g 修复点：该降级不再压到 debug（否则生产日志里看不见）"""
        src = (REPO_ROOT / "app_server.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        # 找到 except ImportError 分支里对该模块的日志调用级别
        found_levels = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            handlers = node.handlers
            for h in handlers:
                if not (isinstance(h.type, ast.Name) and h.type.id == "ImportError"):
                    continue
                # 该 try 体里是否 import 了 api_gateway_flask
                body_src = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                if "api_gateway_flask" not in body_src:
                    continue
                for sub in ast.walk(h):
                    if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                        if isinstance(sub.func.value, ast.Name) and sub.func.value.id == "logger":
                            found_levels.append(sub.func.attr)
        assert found_levels, "应能找到 api_gateway_flask 的 ImportError 处理分支"
        assert "debug" not in found_levels, (
            f"E1g 回归：api_gateway_flask 降级又被打回 debug（不可见）: {found_levels}")
        assert "error" in found_levels, f"应为 error 级: {found_levels}"

    def test_startup_report_is_wired_into_app_server(self):
        """汇总出报确实接线进了 app_server.py（不是只写了个没人调的模块）"""
        src = (REPO_ROOT / "app_server.py").read_text(encoding="utf-8")
        assert "emit_startup_report" in src
        assert "audit_and_record" in src
        # 且必须包在 try/except 里（D4：不得阻断启动）
        assert "启动诊断不可用" in src

    def test_server_health_log_is_stale_relative_to_app_server(self):
        """如实记录：`server_health.log` 比 `app_server.py` 旧 ⇒ 其结论不可直接用

        这条断言的意义是**防止误用**：若将来两者时间关系反转（日志变新），
        说明确实重新启动过，届时该重新核对日志结论。
        """
        log = REPO_ROOT / "server_health.log"
        app = REPO_ROOT / "app_server.py"
        if not log.exists():
            pytest.skip("server_health.log 不存在")
        assert app.stat().st_mtime > log.stat().st_mtime, (
            "app_server.py 现在比 server_health.log 新 —— 若反转，请重新核对日志结论")
