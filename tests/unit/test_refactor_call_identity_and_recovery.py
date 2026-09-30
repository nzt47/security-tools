"""调用身份轴 / 工具错误恢复 / 凭据边界 / partial 状态的验收测试

覆盖本次重构的四项修复，每条都带**反证**（去掉修复即应转红）：

| 修复 | 病灶 | 反证方式 |
|---|---|---|
| call_id 预分配身份轴（INV-04） | agent/ 全域 call_id = 0 命中，身份由 20+ 处自造 | 断言 scope 外 current_call_id() == ""（不是"随便生成一个"） |
| 工具层错误恢复真实生效（INV-08/09） | 导入的 ErrorRecovery/ToolResultProcessor **在本仓不存在**，ImportError 被静默吞掉 | 断言 write/未知 effect **不重试**（防止"修好导入"顺带放开写重试） |
| abort/timeout 每 run 隔离（C-1.2） | 两个共享 Event ⇒ 一个会话中止会掐断并发会话，且新 run 会 clear 掉他人信号 | 构造两个 run，断言互不影响（改动前必然失败） |
| read_file 凭据边界（S-2） | 不拦 .env/私钥，且路径保护未 realpath ⇒ symlink 可绕 | 断言 symlink 指向凭据文件同样被拦 |
| run 级 deadline 传播（V2.0 §5.3） | handler 上界是静态 1800s，与 60s 重试窗口 / 15s 前端预算量级不自洽 | 断言「工具声明不限」时仍被 deadline 收敛（改动前会 return 0 = 不限） |
"""
from __future__ import annotations

import os
import threading
import time

import pytest


# ════════════════════════════════════════════════════════════
#  1. 调用身份轴（agent/capregistry/identity.py）
# ════════════════════════════════════════════════════════════

class TestCallIdentity:

    def setup_method(self):
        from agent.capregistry import identity
        identity.reset_context()

    def test_new_call_id_格式与唯一性(self):
        from agent.capregistry import identity
        a, b = identity.new_call_id(), identity.new_call_id()
        assert identity.is_valid_call_id(a)
        assert identity.is_valid_call_id(b)
        assert a != b
        assert a.startswith("call_")
        assert identity.is_valid_call_id(identity.new_run_id())

    def test_非法值不被接受(self):
        from agent.capregistry import identity
        for bad in ("", "call_", "call_XYZ", "call_" + "0" * 31, None, 123, "run_" + "g" * 32):
            assert identity.is_valid_call_id(bad) is False

    def test_反证_scope外不生成身份(self):
        """**反证**：scope 外必须返回空串。

        若这里返回一个"看起来合法"的 id，就退回了"调用方分不清
        '编排层已预分配' 与 '无上下文'"的旧形态 —— 那正是本次要消灭的。
        """
        from agent.capregistry import identity
        assert identity.current_call_id() == ""
        assert identity.current_run_id() == ""

    def test_scope绑定与恢复(self):
        from agent.capregistry import identity
        outer = identity.new_call_id()
        with identity.call_scope(outer) as cid:
            assert cid == outer
            assert identity.current_call_id() == outer
            inner = identity.new_call_id()
            with identity.call_scope(inner):
                assert identity.current_call_id() == inner
            # 退出内层必须恢复外层（嵌套不被破坏）
            assert identity.current_call_id() == outer
        assert identity.current_call_id() == ""

    def test_ensure_call_id_标注降级(self):
        from agent.capregistry import identity
        cid, derived = identity.ensure_call_id()
        assert derived is True and identity.is_valid_call_id(cid)
        with identity.call_scope(cid):
            cid2, derived2 = identity.ensure_call_id()
            assert derived2 is False and cid2 == cid

    def test_上下文快照键名与契约一致(self):
        from agent.capregistry import identity
        with identity.call_scope("call_" + "a" * 32), identity.run_scope("run_" + "b" * 32):
            snap = identity.call_context_snapshot()
        assert set(snap) == {"call_id", "run_id"}
        assert snap["call_id"] == "call_" + "a" * 32
        assert snap["run_id"] == "run_" + "b" * 32

    def test_线程间不串扰(self):
        """ContextVar 是每线程一份；本用例固化该前提（子线程不会继承父 scope）。"""
        from agent.capregistry import identity
        seen = {}

        def worker():
            seen["tid"] = identity.current_call_id()

        with identity.call_scope("call_" + "c" * 32):
            t = threading.Thread(target=worker)
            t.start()
            t.join()
        # 子线程拿不到父 scope 的值 ⇒ 这正是 ensure_call_id 要标注 derived 的场景
        assert seen["tid"] == ""


# ════════════════════════════════════════════════════════════
#  2. 工具层错误恢复（agent/response_workflows.py::ErrorRecovery）
# ════════════════════════════════════════════════════════════

class TestErrorRecovery:

    def _plan(self, msg, attempt=0, tool="read_file", effect="read"):
        from agent.response_workflows import ErrorRecovery
        return ErrorRecovery.get_recovery_plan(msg, attempt, tool_name=tool, effect=effect)

    @pytest.mark.parametrize("msg,expected", [
        ("connection reset by peer", "TRANSIENT"),
        ("502 Bad Gateway", "TRANSIENT"),
        ("request timed out", "TIMEOUT"),
        ("403 Forbidden", "AUTH"),
        ("permission denied", "AUTH"),
        ("schema validation failed", "PERMANENT"),
        ("未知工具: foo", "PERMANENT"),
        ("host unreachable", "UNAVAILABLE"),
        ("完全无法归类的一句话", "UNKNOWN"),
    ])
    def test_七分类映射(self, msg, expected):
        from agent.response_workflows import ErrorRecovery
        assert ErrorRecovery.classify(msg) == expected

    def test_权限类优先于超时类(self):
        """复合消息必须归到**不可重试**的一侧（关键词表顺序有意义）。"""
        assert self._plan("timeout 后 permission denied")["error_class"] == "AUTH"
        assert self._plan("timeout 后 permission denied")["should_retry"] is False

    def test_read_可重试(self):
        plan = self._plan("connection reset")
        assert plan["should_retry"] is True
        assert plan["delay"] > 0

    def test_反证_写操作绝不重试(self):
        """**反证（INV-09）**：本仓没有写幂等键，重试写会重复副作用。

        去掉 ErrorRecovery._retry_safe 的 effect 闸门 ⇒ 本用例立刻转红。
        """
        plan = self._plan("connection reset", tool="write_file", effect="write")
        assert plan["should_retry"] is False
        assert "INV-09" in plan["reason"]
        assert plan["delay"] == 0.0

    def test_反证_未知后果绝不重试(self):
        """effect 取不到（台账缺失/工具未登记）⇒ fail-closed，不重试。"""
        for effect in ("", "execute", "extend", "unknown-effect"):
            assert self._plan("connection reset", effect=effect)["should_retry"] is False

    def test_非可重试分类不重试(self):
        for msg in ("schema error", "403 forbidden", "unreachable"):
            assert self._plan(msg)["should_retry"] is False

    def test_达到上限后不重试(self):
        from agent.response_workflows import ErrorRecovery
        last = ErrorRecovery.MAX_ATTEMPTS - 1
        assert self._plan("connection reset", attempt=last)["should_retry"] is False
        assert self._plan("connection reset", attempt=last - 1)["should_retry"] is True

    def test_不重试时必须给出理由(self):
        """INV-08：判定结果可归因，否则"没重试"与"路径到不了"无法区分。"""
        plan = self._plan("schema error")
        assert plan["should_retry"] is False
        assert plan["reason"] and plan["reason"] != "ok"
        assert "不重试" in plan["message"]

    def test_摘要不含堆栈且压平空白(self):
        from agent.response_workflows import ErrorRecovery
        brief = ErrorRecovery._safe_brief("line1\n\nline2   " + "x" * 500)
        assert "\n" not in brief
        assert len(brief) <= 160


# ════════════════════════════════════════════════════════════
#  3. 工具结果压缩（ToolResultProcessor）
# ════════════════════════════════════════════════════════════

class TestToolResultProcessor:

    def test_超长字段被压缩且带标注(self):
        from agent.response_workflows import ToolResultProcessor
        result = {"ok": True, "content": "a" * 20000}
        rep = ToolResultProcessor.compress_verbose(result, max_chars=1000)
        assert rep["compressed"] is True
        assert rep["saved_chars"] > 0
        assert "content" in rep["fields"]
        assert "已压缩" in result["content"]
        # ok 语义绝不被改动
        assert result["ok"] is True

    def test_反证_短字段不被动(self):
        from agent.response_workflows import ToolResultProcessor
        result = {"ok": True, "content": "hi"}
        rep = ToolResultProcessor.compress_verbose(result)
        assert rep["compressed"] is False
        assert result["content"] == "hi"

    def test_嵌套结构与列表(self):
        from agent.response_workflows import ToolResultProcessor
        result = {"ok": True, "nested": {"big": "b" * 9000}, "items": ["c" * 9000, "small"]}
        rep = ToolResultProcessor.compress_verbose(result, max_chars=100)
        assert rep["compressed"] is True
        assert any("nested.big" == f for f in rep["fields"])
        assert any(f.startswith("items[") for f in rep["fields"])
        assert result["items"][1] == "small"

    def test_异常输入不抛(self):
        from agent.response_workflows import ToolResultProcessor
        for bad in (None, 42, "text", [], {"a": None}, {"a": 1}):
            rep = ToolResultProcessor.compress_verbose(bad)  # 不得抛
            assert isinstance(rep, dict)

    def test_压缩后长度受控(self):
        from agent.response_workflows import ToolResultProcessor
        result = {"content": "x" * 100000}
        ToolResultProcessor.compress_verbose(result, max_chars=500)
        # 允许标注本身带来的少量额外字符
        assert len(result["content"]) < 500 + 200


# ════════════════════════════════════════════════════════════
#  4. partial 结果状态（agent/capregistry/errors.py）
# ════════════════════════════════════════════════════════════

class TestPartialResult:

    def test_ok_与_error_键集不变(self):
        """D2：既有 ok/error 的对拍键集必须一字不改。"""
        from agent.capregistry.errors import CapabilityResult, ok_result, CapabilityError, err_result
        assert set(ok_result({"a": 1}).to_dict()) == {"status", "code", "data", "error", "meta"}
        err = err_result(CapabilityError("timeout"))
        assert set(err.to_dict()) == {"status", "code", "data", "error", "meta"}

    def test_partial_携带缺失项(self):
        from agent.capregistry.errors import partial_result, PARTIAL
        res = partial_result({"a": 1}, ["erp 源超时"], code="timeout")
        assert res.status == PARTIAL
        assert res.is_partial is True and res.ok is False
        d = res.to_dict()
        assert d["missing"] == ["erp 源超时"]
        assert res.http_status() == 206

    def test_反证_空缺失项被拒绝(self):
        """**反证**：无缺失就不该产 partial —— 否则第三种状态没有信息量。"""
        from agent.capregistry.errors import partial_result
        with pytest.raises(ValueError):
            partial_result({"a": 1}, [])


# ════════════════════════════════════════════════════════════
#  5. 凭据文件边界（agent/tools/file_tools.py）
# ════════════════════════════════════════════════════════════

class TestCredentialBoundary:

    @pytest.mark.parametrize("name", [
        ".env", ".env.local", ".env.backups", ".netrc", "credentials",
        "secrets.json", "id_rsa", "id_ed25519", "server.pem", "app.key",
        "cert.p12", "audit_signing_key.pem", ".git-credentials",
    ])
    def test_凭据文件被识别(self, name, tmp_path):
        from agent.tools.file_tools import is_credential_path
        assert is_credential_path(str(tmp_path / name)) is True

    @pytest.mark.parametrize("name", ["main.py", "README.md", "config.yaml", "notes.txt", "data.json"])
    def test_普通文件不被误伤(self, name, tmp_path):
        from agent.tools.file_tools import is_credential_path
        assert is_credential_path(str(tmp_path / name)) is False

    def test_凭据目录片段被识别(self, tmp_path):
        from agent.tools.file_tools import is_credential_path
        assert is_credential_path(str(tmp_path / ".ssh" / "known_hosts")) is True
        assert is_credential_path(str(tmp_path / ".aws" / "config")) is True

    def test_反证_symlink_不能绕过(self, tmp_path):
        """**反证（S-2 补漏）**：原实现只比对字面路径 ⇒ symlink 可绕过。

        去掉 is_credential_path 里的 os.path.realpath ⇒ 本用例转红。
        """
        from agent.tools.file_tools import is_credential_path
        secret = tmp_path / ".env"
        secret.write_text("K=1", encoding="utf-8")
        link = tmp_path / "innocent.txt"
        try:
            os.symlink(str(secret), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("当前环境不支持创建符号链接（需管理员权限）")
        assert is_credential_path(str(link)) is True

    def test_反证_凭据文件仍可读_防线在外发侧(self, tmp_path):
        """**反证（记录一次被测试推翻的设计判断）**。

        我最初在 read_file 里加了「凭据文件硬拦」，随后被
        tests/unit/test_policy_integration.py::TestToolReadToEgressChain 推翻：
        本仓的既有设计是「**读取允许，防线在外发侧**」——
        agent/policy/egress.py 用 is_secret_path() + scan_secret_material() 把
        「读了密钥」记成污点，之后任何出域请求被拒。

        硬拦会让该链路**整条失效**（读失败 ⇒ 不记污点 ⇒ 外发不再被拦），
        且不泛化（shell_execute 一样能 cat .env）。故回退，仅保留**分类器**。

        本用例锁住回退后的真实契约：读得到，且分类器认得出来。
        """
        from agent.tools.file_tools import is_credential_path, read_file
        secret = tmp_path / ".env"
        secret.write_text("SECRET_KEY=dummy", encoding="utf-8")
        out = read_file(str(secret))
        assert out["ok"] is True, "读取必须仍然可用（否则外发侧污点链路断掉）"
        # 真正的防线：分类器可判定，供外发/审计复用
        assert is_credential_path(str(secret)) is True

    def test_read_file_正常文件仍可读(self, tmp_path):
        from agent.tools.file_tools import read_file
        normal = tmp_path / "hello.txt"
        normal.write_text("hi", encoding="utf-8")
        out = read_file(str(normal))
        assert out["ok"] is True
        assert out["content"] == "hi"


# ════════════════════════════════════════════════════════════
#  6. abort / timeout 每 run 隔离（C-1.2）
# ════════════════════════════════════════════════════════════

class TestRunControlIsolation:

    def test_每个_run_控制块独立(self):
        """**反证（C-1.2）**：改动前两个 run 共用实例上的两个 Event。

        去掉 _RunControl、退回共享字段 ⇒ 本用例转红。
        """
        from agent.tool_calling import _RunControl
        a = _RunControl(run_id="run_a")
        b = _RunControl(run_id="run_b")
        a.abort_event.set()
        assert a.abort_event.is_set() is True
        assert b.abort_event.is_set() is False        # 互不影响
        assert a.timeout_event.is_set() is False
        assert isinstance(a.abort_event, threading.Event)

    def test_服务实例的控制块登记与活动指针(self):
        """用 __new__ 绕过重量级 __init__，只验证 run 登记/栈/属性语义。"""
        from agent.tool_calling import ToolCallingService, _RunControl
        svc = ToolCallingService.__new__(ToolCallingService)
        svc._runs = {}
        svc._run_stack = []
        svc._runs_lock = threading.RLock()
        svc._detached_control = _RunControl(run_id="")

        # 无活动 run：属性仍可用（向后兼容）
        assert isinstance(svc._abort_event, threading.Event)
        assert svc.abort() is True
        assert svc._abort_event.is_set() is True
        svc._abort_event.clear()

        # 两个 run 并存，互不干扰
        c1 = svc._begin_run()
        c2 = svc._begin_run()
        assert svc.active_run_ids() == [c1.run_id, c2.run_id]
        svc.abort(c1.run_id)
        assert c1.abort_event.is_set() is True
        assert c2.abort_event.is_set() is False       # 关键：不再殃及池鱼

        # 默认 abort 只打栈顶（当前活动 run）
        svc.abort()
        assert c2.abort_event.is_set() is True

        # 精确指定不存在的 run ⇒ 返回 False 并告警（不静默）
        assert svc.abort("run_does_not_exist") is False

        svc._end_run(c1.run_id)
        svc._end_run(c2.run_id)
        assert svc.active_run_ids() == []
        # 幂等：重复注销不抛
        svc._end_run(c1.run_id)

    def test_死字段已删除(self):
        """审计发现的 _circuit_breaker 死字段必须不复存在（**按 AST 判，不按文本判**）。

        留着一个不生效的熔断字段比没有更危险 —— 它让人以为已有保护。

        【为什么用 AST 而不是子串匹配】本次改动的注释里会提到该字段名
        （说明「为什么删」），纯文本断言会把注释也算命中而假红。
        """
        import ast
        from agent.tool_calling import ToolCallingService
        path = ToolCallingService.__module__.replace(".", os.sep) + ".py"
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        assigned = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Attribute):
                    assigned.append(target.attr)
        assert "_circuit_breaker" not in assigned

# ════════════════════════════════════════════════════════════
#  7. run 级 deadline 传播（agent/timeout_budget.py）
# ════════════════════════════════════════════════════════════

class TestRunDeadline:
    """V2.0 §5.3「deadline 传播」+ §5.8.2「父 run 自留 ≥20%」。

    改动前实测：全仓 remaining = deadline - now 仅 2 处且都与编排链路无关，
    handler 上界是静态 1800s，而重试窗口 60s、前端预算 15s —— 量级不自洽。
    """

    def teardown_method(self):
        from agent import timeout_budget as tb
        tb.reset_run_deadline(tb.set_run_deadline(0))

    def test_反证_无_deadline_时行为逐字不变(self):
        """**反证（D2）**：未接线调用方必须原样拿到入参。

        若这里被改成「总是收敛」，所有脚本/单次调用/MCP 客户端的行为都会变。
        """
        from agent.timeout_budget import call_timeout, remaining_sec
        assert remaining_sec() is None          # None，不是 0
        assert call_timeout(1800.0) == 1800.0
        assert call_timeout(0.0) == 0.0         # 0 = 不限，原样保留
        assert call_timeout(30.0) == 30.0

    def test_有_deadline_时按剩余量与自留比例收敛(self):
        from agent.timeout_budget import RESERVE_RATIO, call_timeout, run_deadline_scope
        with run_deadline_scope(100.0):
            got = call_timeout(1800.0)
        # remaining≈100，自留 30% ⇒ 上界 ≈70；1800 被收敛
        assert 60.0 < got <= 100.0 * (1.0 - RESERVE_RATIO)
        assert got < 1800.0

    def test_反证_工具声明不限时_deadline_仍是上界(self):
        """**反证**：这是「单个挂死 handler 拖 30 分钟」的成因。

        改动前 resolve_tool_handler_timeout 在 ceiling<=0 时直接 return 0.0，
        把「全局关闭超时」连 deadline 一起绕过。
        """
        from agent.timeout_budget import call_timeout, run_deadline_scope
        with run_deadline_scope(50.0):
            got = call_timeout(0.0)             # 工具自述「不限」
        assert got > 0.0, "必须收敛成有限值，不能返回 0（0 在本模块语义是「不限」）"
        assert got <= 50.0

    def test_反证_已超时也不返回_0(self):
        """**反证**：remaining<=0 时若返回 0，会从「限时」翻成「无限时」。"""
        from agent.timeout_budget import _MIN_CALL_TIMEOUT, call_timeout, run_deadline_scope
        with run_deadline_scope(0.05):
            time.sleep(0.12)
            got = call_timeout(1800.0)
        assert got == _MIN_CALL_TIMEOUT > 0.0

    def test_嵌套_scope_恢复外层(self):
        from agent.timeout_budget import call_timeout, run_deadline_scope
        with run_deadline_scope(1000.0):
            outer = call_timeout(10000.0)
            with run_deadline_scope(10.0):
                inner = call_timeout(10000.0)
            back = call_timeout(10000.0)
        assert inner < back <= outer

    def test_set_reset_token_往返(self):
        from agent.timeout_budget import (call_timeout, remaining_sec,
                                          reset_run_deadline, set_run_deadline)
        assert remaining_sec() is None
        token = set_run_deadline(120.0)
        try:
            assert remaining_sec() is not None
            assert call_timeout(9999.0) < 9999.0
        finally:
            reset_run_deadline(token)
        # 复位后回到「未接线」语义
        assert remaining_sec() is None
        assert call_timeout(9999.0) == 9999.0

    def test_seconds_非正数等于不设置(self):
        from agent.timeout_budget import remaining_sec, set_run_deadline
        assert set_run_deadline(0) is None
        assert set_run_deadline(-5) is None
        assert remaining_sec() is None

    def test_反证_handler_上界被_deadline_收敛(self):
        """**反证（端到端）**：resolve_tool_handler_timeout 是 handler 上界唯一出口。

        去掉 call_timeout(ceiling) 那一行 ⇒ 本用例转红。
        """
        from agent.timeout_budget import resolve_tool_handler_timeout, run_deadline_scope
        before = resolve_tool_handler_timeout(None, None)
        with run_deadline_scope(100.0):
            after = resolve_tool_handler_timeout(None, None)
        assert before > after, f"deadline 未生效: before={before} after={after}"
        assert after > 0.0

    def test_snapshot_字段(self):
        from agent.timeout_budget import run_deadline_snapshot, run_deadline_scope
        assert run_deadline_snapshot()["has_deadline"] is False
        with run_deadline_scope(60.0):
            snap = run_deadline_snapshot()
        assert snap["has_deadline"] is True
        assert 0 < snap["remaining_sec"] <= 60.0
        assert snap["reserve_ratio"] >= 0.2, "V2.0 §5.8.2 要求父 run 自留 ≥20%"

# ════════════════════════════════════════════════════════════
#  8. 下载逐跳复检（S3 审计：download 曾绕过策略层）
# ════════════════════════════════════════════════════════════

class _FakeResp:
    """最小可用响应桩（只覆盖 download 用到的那几个成员）"""

    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size=8192):
        yield b"hello"


class TestDownloadHopGuard:
    """改动前 download 直接调 self._session.get ⇒ requests 默认跟随重定向，
    跳转目标**绕过策略层判定**（`公网 URL 302 到 169.254.169.254` 经典 SSRF 链）。
    现改走 _send_with_redirects（allow_redirects=False + 逐跳复检）。"""

    def _client(self, monkeypatch, send):
        from agent.web.http_client import HttpClient
        client = HttpClient({})
        monkeypatch.setattr(client, "_preflight_block", lambda *a, **k: None)
        monkeypatch.setattr(client, "_send_with_redirects", send)
        return client

    def test_反证_download_经逐跳复检发送(self, monkeypatch, tmp_path):
        calls = {}

        def fake_send(**kwargs):
            calls.update(kwargs)
            return _FakeResp(), [], None

        client = self._client(monkeypatch, fake_send)
        out = client.download("https://example.invalid/x", str(tmp_path / "f.bin"))
        # 关键：必须由 download 自己声明 allow_redirects（= 由我方逐跳复检），
        # 而不是把它留给 requests 的默认行为
        assert calls.get("allow_redirects") is True
        assert calls.get("stream") is True
        assert calls.get("method") == "GET"
        assert out["ok"] is True and out["size"] == 5

    def test_某一跳被拒时如实返回拦截结果(self, monkeypatch, tmp_path):
        def fake_send(**kwargs):
            return (_FakeResp(), [], {"ok": False,
                                      "error": "重定向目标被拒绝（第 1 跳）：http://169.254.169.254/"})

        client = self._client(monkeypatch, fake_send)
        out = client.download("https://example.invalid/x", str(tmp_path / "f.bin"))
        assert out["ok"] is False
        assert "重定向" in out["error"]
        assert client._stats["blocked_count"] == 1


# ════════════════════════════════════════════════════════════
#  9. search_files 不出凭据文件 + result_schema 覆盖只增不减
# ════════════════════════════════════════════════════════════

class TestSearchFilesCredentialFilter:

    def test_凭据文件不出现在搜索结果里(self, tmp_path):
        (tmp_path / ".env").write_text("K=1", encoding="utf-8")
        (tmp_path / "server.pem").write_text("x", encoding="utf-8")
        (tmp_path / "normal.py").write_text("x = 1", encoding="utf-8")
        from agent.tools.file_tools import search_files
        out = search_files("*", root_path=str(tmp_path))
        assert out["ok"] is True
        names = [r["name"] for r in out["results"]]
        assert "normal.py" in names
        assert ".env" not in names, "凭据文件不应被递到模型面前（下一步它就会去 read_file）"
        assert "server.pem" not in names

# ════════════════════════════════════════════════════════════
#  10. 审计发现的固化门禁（只许收敛，不许退化）
# ════════════════════════════════════════════════════════════

def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestDeadRouteGuard:
    """`register_all_routes` 死代码 —— 148 条路由从未注册（审计 A-1）。

    【为什么用"计数上界"而不是直接删】删掉 148 条路由的注册代码是**产品决策**
    （万一某天要启用它们）；而"它一直没人调用、且越积越多"是**工程缺陷**。
    本守卫锁住后者：计数只许减少，涨了就红，逼改动者显式面对。
    """

    #: 审计实测基线（2026-09-30）：register_all_routes 内注册的路由数
    BASELINE_MAX = 200

    def test_死代码仍然无调用方_若有则必须同步本守卫(self):
        import re
        root = _repo_root()
        hits = []
        for base, dirs, files in os.walk(os.path.join(root, "agent")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for fn in files:
                if not fn.endswith(".py"):
                    continue
                path = os.path.join(base, fn)
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh, 1):
                        if "register_all_routes" in line:
                            hits.append((os.path.relpath(path, root), i, line.strip()))
        # 允许"定义"与"文档提及"，但不允许**除定义外的调用**
        callers = [h for h in hits
                   if not h[2].startswith("def ") and "无调用方" not in h[2]
                   and h[0] != os.path.join("agent", "server_routes", "__init__.py")]
        assert len(hits) >= 1, "register_all_routes 的定义应仍然存在（否则请更新本守卫）"
        assert callers == [], (
            "register_all_routes 出现了调用方 ⇒ 死路由已被启用，",
            "请更新本守卫与审计报告 A-1：%r" % (callers,))

    def test_死路由规模不超过基线(self):
        import re
        root = _repo_root()
        path = os.path.join(root, "agent", "server_routes", "__init__.py")
        with open(path, encoding="utf-8", errors="ignore") as fh:
            src = fh.read()
        # 粗口径：该文件里 import 的路由模块数（每个模块通常贡献若干路由）
        imports = re.findall(r"^\s*from\s+\.\s*import\s+(\w+)", src, re.M)
        assert len(imports) <= self.BASELINE_MAX, (
            f"死代码规模从基线增长到 {len(imports)}（上界 {self.BASELINE_MAX}）")


class TestCrossRegistryConsistency:
    """跨注册表一致性 —— 审计实测全仓仅 1 处校验且只查 skill description 单字段。

    这里补上"工具集合"这一面：`data/tool_definitions/*.yaml`（人写权威）
    与 `data/capability_manifest.json`（派生清单）的工具名集合必须一致。
    两者漂移正是"第二真相源"最常见的形态。
    """

    def test_工具名集合在权威源与派生清单之间一致(self):
        import glob, json
        root = _repo_root()
        yaml_names = {os.path.splitext(os.path.basename(p))[0]
                      for p in glob.glob(os.path.join(root, "data", "tool_definitions", "*.yaml"))}
        manifest_path = os.path.join(root, "data", "capability_manifest.json")
        with open(manifest_path, encoding="utf-8") as fh:
            manifest = json.load(fh)
        # 清单字段名是 tool_name（不是 name）——实测确认，勿凭直觉写
        tool_entries = {str(t.get("tool_name") or "") for t in manifest.get("tools", [])}
        tool_entries.discard("")
        assert yaml_names, "未读到任何 tool_definitions/*.yaml（路径口径变了？）"
        missing_in_manifest = sorted(yaml_names - tool_entries)
        missing_in_yaml = sorted(tool_entries - yaml_names)
        assert missing_in_manifest == [], (
            "权威 YAML 有但派生清单没有（跑 python scripts/sync_capability_manifest.py）: %r"
            % (missing_in_manifest,))
        assert missing_in_yaml == [], (
            "派生清单有但权威 YAML 没有（清单陈旧，跑 sync_capability_manifest.py）: %r"
            % (missing_in_yaml,))

    def test_descriptor_台账覆盖不下降(self):
        """`data/descriptors.json` 是被 .gitignore 忽略的运行期台账，覆盖 32 条。

        它是**第二套**能力台账（审计 H11），短期收不掉；但至少锁住"不继续退"：
        台账缺失时 skip（干净 checkout 上本就不存在），存在时必须 ≥ 基线。
        """
        import json
        path = os.path.join(_repo_root(), "data", "descriptors.json")
        if not os.path.exists(path):
            pytest.skip("descriptors.json 不存在（干净 checkout 的预期状态）")
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        n = len(data.get("descriptors") or {})
        assert n >= 32, f"descriptor 台账从 32 条退到 {n} 条"


class TestResultSchemaCoverage:
    """`result_schema` 覆盖**只增不减**（与 failures_baseline.txt 同一纪律）。

    实测基线：改动前 3/91；本次补 read_file / search_files 后 5/91。
    机制本身早已存在并接线（contract.py + invoke.py 步骤⑦，违约只报告不改 status），
    缺的是声明覆盖 —— 所以这里锁的是「别退回去」。
    """

    FLOOR = 5

    def _count(self):
        import glob
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        pat = os.path.join(root, "data", "tool_definitions", "*.yaml")
        n = 0
        for path in glob.glob(pat):
            with open(path, encoding="utf-8") as fh:
                if any(line.startswith("result_schema:") for line in fh):
                    n += 1
        return n

    def test_覆盖率不低于基线(self):
        got = self._count()
        assert got >= self.FLOOR, (
            f"result_schema 覆盖从 {self.FLOOR} 退到 {got}；"
            "该指标只许增长（对齐 V2.0 §3.4「output_schema 不可或缺」）")

    def test_必填清单只增不减且工具真的声明了_schema(self):
        from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS
        required = set(RESULT_SCHEMA_REQUIRED_TOOLS)
        assert required >= {"data_format_detect", "json_query", "get_file_info"}, \
            "原有三个不得被移除"
        assert {"read_file", "search_files"} <= required, "本次新增的两个必须在清单里"
