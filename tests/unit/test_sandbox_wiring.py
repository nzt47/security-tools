"""TASK-07 负例 3：沙箱统一收口（E7 / E8 / E10）

验收覆盖（TASK-07 §5 E7 逐条）：
    · `shell_execute` **走 `validate_command`**（有 spy 证明，不是"看代码像走了"）；
    · **`run_sandboxed` 接进真实执行路径**（改动前是零生产调用方）；
    · `sandbox_allowed: false` 在**受限会话**内被拒；
    · 三类路径逃逸**全部被拒**：symlink / Windows 8.3 短名 / 前缀逃逸
      （`C:\\work\\ab` vs `C:\\work\\a`）。

外加 E8（诚实标注隔离级别：本仓只有进程级，**没有**容器/WASM）与
E10（正常 shell 用法不被误拦）。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent.subagent import sandbox as SB


# ════════════════════════════════════════════════════════════
#  夹具
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _isolated_audit(tmp_path):
    from agent.audit import facade as fac
    chain = fac.get_audit_chain(
        str(tmp_path / "audit_chain.db"),
        roots_path=str(tmp_path / "daily_roots.jsonl"),
        signing_key_path=str(tmp_path / "key.pem"))
    previous = fac.audit.bind(chain)
    try:
        yield chain
    finally:
        fac.audit.bind(previous)


@pytest.fixture(autouse=True)
def _reset_env(monkeypatch):
    monkeypatch.delenv("CP_SANDBOX_SHELL_MODE", raising=False)
    monkeypatch.delenv("CP_TOOL_SANDBOX_ALLOWED_ENFORCE", raising=False)
    monkeypatch.setenv("CP_TOOL_GATE_APPROVAL_ENFORCE", "0")
    monkeypatch.delenv("CP_TOOL_GATE_ENABLED", raising=False)
    SB.reset_tool_sandbox()


#: 会命中 `SANDBOX_DANGEROUS_PATTERNS` 但**对宿主无害**的命令
#: （`die`/`drop table` 在 shell 里都不存在 ⇒ 即使真的执行了也只是"命令未找到"）
DANGEROUS_BUT_HARMLESS = "drop table users"


# ════════════════════════════════════════════════════════════
#  一、E7：`shell_execute` 走 `validate_command` + `run_sandboxed`
# ════════════════════════════════════════════════════════════


class TestShellWiring:
    def test_validate_command_is_called(self, monkeypatch):
        """spy：每次 `execute_shell` 都必须先过 `Sandbox.validate_command`"""
        seen = []
        real = SB.Sandbox.validate_command

        def _spy(self, cmd):
            seen.append(cmd)
            return real(self, cmd)

        monkeypatch.setattr(SB.Sandbox, "validate_command", _spy)
        from agent.tools.shell_tools import execute_shell
        execute_shell("echo hello-wiring", shell="bash")
        assert seen, "shell_execute 必须先过 validate_command"
        assert any("echo hello-wiring" in c for c in seen)

    def test_run_sandboxed_is_the_executor(self, monkeypatch):
        """spy：执行**真的**落在 `Sandbox.run_sandboxed` 上（改动前它零生产调用方）"""
        seen = []
        real = SB.Sandbox.run_sandboxed

        def _spy(self, cmd, **kwargs):
            seen.append(cmd)
            return real(self, cmd, **kwargs)

        monkeypatch.setattr(SB.Sandbox, "run_sandboxed", _spy)
        from agent.tools.shell_tools import execute_shell
        result = execute_shell("echo hello-executor", shell="bash")
        assert seen, "执行必须经 run_sandboxed"
        assert result.get("sandboxed") is True
        assert "hello-executor" in (result.get("stdout") or "")

    def test_enforce_denies_dangerous_command(self, monkeypatch):
        """`enforce` 档：命中危险模式 ⇒ 拒绝，且 `subprocess.run`/`Popen` 都没被调用"""
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", "enforce")
        launched = []
        monkeypatch.setattr(subprocess, "run",
                            lambda *a, **k: launched.append(a) or None)
        monkeypatch.setattr(subprocess, "Popen",
                            lambda *a, **k: launched.append(a) or None)
        from agent.tools.shell_tools import execute_shell
        result = execute_shell(DANGEROUS_BUT_HARMLESS, shell="bash")
        assert result["ok"] is False
        assert result.get("blocked") is True
        assert result.get("blocked_by") == "subagent.sandbox"
        assert launched == [], "被拒的命令绝不能启动子进程"

    def test_shadow_mode_warns_but_executes(self, monkeypatch):
        """`shadow` 档（**默认**）：只告警不拦 —— 命令照旧执行（TASK-07 §6 灰度要求）"""
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", "shadow")
        seen = []
        real = SB.Sandbox.run_sandboxed

        def _spy(self, cmd, **kwargs):
            seen.append(cmd)
            return real(self, cmd, **kwargs)

        monkeypatch.setattr(SB.Sandbox, "run_sandboxed", _spy)
        from agent.tools.shell_tools import execute_shell
        execute_shell(DANGEROUS_BUT_HARMLESS, shell="bash")
        assert seen == [], "影子模式命中后应走既有路径，不得进沙箱执行器"

    def test_off_mode_is_legacy(self, monkeypatch):
        """`off` 档：完全不接线（等价改动前 —— 回滚口）"""
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", "off")
        called = []
        monkeypatch.setattr(SB.Sandbox, "validate_command",
                            lambda self, cmd: called.append(cmd) or SB.CommandVerdict(True))
        from agent.tools import shell_tools
        result = shell_tools.execute_shell("echo hello-off", shell="bash")
        assert called == []
        assert result.get("ok") is True
        assert "sandboxed" not in result

    def test_default_mode_is_shadow(self):
        from agent.tools.shell_tools import sandbox_mode
        assert sandbox_mode() == "shadow"

    @pytest.mark.parametrize("raw,expected", [
        ("off", "off"), ("shadow", "shadow"), ("enforce", "enforce"),
        ("1", "enforce"), ("0", "off"), ("SHADOW", "shadow"),
        ("nonsense", "shadow"),
    ])
    def test_mode_parsing(self, monkeypatch, raw, expected):
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", raw)
        from agent.tools.shell_tools import sandbox_mode
        assert sandbox_mode() == expected

    def test_timeout_kill_and_truncation_preserved(self):
        """既有能力不退化：超时 kill 与输出截断仍由 `run_sandboxed` 保证"""
        import shutil
        if not shutil.which("bash"):
            pytest.skip("本机无 bash（run_sandboxed 用 argv 形态，不经 shell）")
        from agent.subagent.sandbox import SandboxResourceLimits, get_tool_sandbox
        sb = get_tool_sandbox()
        killed = sb.run_sandboxed(["bash", "-c", "sleep 5"],
                                  limits=SandboxResourceLimits(timeout_s=0.5))
        assert killed.timed_out is True and killed.allowed is True
        limited = sb.run_sandboxed(
            ["bash", "-c", "echo 0123456789012345678901234567890123456789"],
            limits=SandboxResourceLimits(max_output_bytes=10))
        assert len(limited.stdout) <= 10

    def test_normal_shell_usage_not_blocked(self):
        """E10：常规 shell 用法（管道/变量/重定向）不被误拦"""
        from agent.tools.shell_tools import execute_shell
        result = execute_shell("echo a b c | wc -w", shell="bash")
        assert result.get("ok") is True, result
        assert result.get("stdout", "").strip().startswith("3")


# ════════════════════════════════════════════════════════════
#  二、E11：沙箱拦截落审计，且**不含命令原文**
# ════════════════════════════════════════════════════════════


class TestSandboxAudit:
    def test_denial_audited_without_plaintext(self, monkeypatch, _isolated_audit):
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", "enforce")
        from agent.tools.shell_tools import execute_shell
        execute_shell("drop table customers_secret", shell="bash")
        rows = _isolated_audit.entries(action="sandbox_denied")
        assert rows, "沙箱拒绝必须落审计"
        import json
        blob = json.dumps([r.payload for r in rows], ensure_ascii=False)
        assert "customers_secret" not in blob, "审计里不得出现命令原文"
        assert "command_digest" in blob
        assert "command_chars" in blob

    def test_shadow_mode_audits_would_deny(self, monkeypatch, _isolated_audit):
        monkeypatch.setenv("CP_SANDBOX_SHELL_MODE", "shadow")
        from agent.tools.shell_tools import execute_shell
        execute_shell(DANGEROUS_BUT_HARMLESS, shell="bash")
        rows = _isolated_audit.entries(action="sandbox_would_deny")
        assert rows, "影子模式必须留下「若开启会拦什么」的记录"


# ════════════════════════════════════════════════════════════
#  三、E7：`sandbox_allowed` 在受限会话内真的被消费
# ════════════════════════════════════════════════════════════


class TestSandboxAllowedConsumption:
    def test_field_is_read_from_authoritative_yaml(self):
        """`web_get` 的 YAML 声明是 `sandbox_allowed: false`；`read_file` 是 true"""
        assert SB.sandbox_allowed_for("web_get") is False
        assert SB.sandbox_allowed_for("read_file") is True

    def test_not_enforced_outside_restricted_session(self):
        """**零影响保证**：不在受限会话内 ⇒ 该字段不参与判定"""
        from agent.tool_gate import check_tool_call
        assert check_tool_call("web_get", {}) is None

    def test_rejected_inside_restricted_session(self):
        from agent.tool_gate import check_tool_call
        with SB.restricted_session("unit"):
            denied = check_tool_call("web_get", {})
        assert denied is not None
        assert denied.get("error_code") == "PERMISSION_DENIED"
        assert "sandbox_allowed=false" in denied.get("error", "")

    def test_allowed_tool_still_passes_inside_session(self):
        from agent.tool_gate import check_tool_call
        with SB.restricted_session("unit"):
            assert check_tool_call("read_file", {}) is None

    def test_switch_can_be_turned_off(self, monkeypatch):
        monkeypatch.setenv("CP_TOOL_SANDBOX_ALLOWED_ENFORCE", "0")
        from agent.tool_gate import check_tool_call
        with SB.restricted_session("unit"):
            assert check_tool_call("web_get", {}) is None

    def test_context_is_thread_local_and_restored(self):
        assert SB.in_restricted_session() is False
        with SB.restricted_session("unit"):
            assert SB.in_restricted_session() is True
            assert SB.current_restriction()["name"] == "unit"
        assert SB.in_restricted_session() is False

    def test_subagent_toolset_enters_restricted_session(self):
        """子代理的工具调用**就是**受限会话（执行那一瞬进入作用域）"""
        from agent.subagent.toolset import SubAgentToolset
        toolset = SubAgentToolset.build(["read_file"], ["read_file"],
                                        actor="sub_agent:unit")
        observed = []
        toolset.invoke("read_file", lambda: observed.append(SB.in_restricted_session()))
        assert observed == [True]
        assert SB.in_restricted_session() is False


# ════════════════════════════════════════════════════════════
#  四、E7：三类路径逃逸全部被拒
# ════════════════════════════════════════════════════════════


class TestPathEscapes:
    def test_prefix_escape_sibling_directory(self, tmp_path):
        """`C:\\work\\a` 放行 **不等于** 放行 `C:\\work\\ab`（裸 startswith 的经典缺陷）"""
        root = tmp_path / "work" / "a"
        sibling = tmp_path / "work" / "ab"
        root.mkdir(parents=True)
        sibling.mkdir(parents=True)
        sb = SB.Sandbox(allowed_permissions={"read"}, allowed_paths=[str(root)])

        assert sb.check_path(str(root / "inside.txt")) is True
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(sibling / "escape.txt"))

    def test_run_sandboxed_cwd_uses_same_boundary(self, tmp_path):
        """`run_sandboxed` 的工作目录判定与 `check_path` **同一口径**（改动前是裸 startswith）"""
        root = tmp_path / "work" / "a"
        sibling = tmp_path / "work" / "ab"
        root.mkdir(parents=True)
        sibling.mkdir(parents=True)
        sb = SB.Sandbox(allowed_permissions={"read", "execute"})
        result = sb.run_sandboxed("echo x", allowed_paths=[str(root)],
                                  cwd=str(sibling))
        assert result.allowed is False
        assert "工作目录不在允许范围内" in result.reason

    def test_symlink_escape_is_rejected(self, tmp_path):
        """符号链接逃逸：`root/link` → `outside` 必须被拒（abspath 解不了链接）"""
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        link = root / "link"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:  # pragma: no cover - 平台限制
            pytest.skip(f"本机不允许创建符号链接：{exc}")

        sb = SB.Sandbox(allowed_permissions={"read"}, allowed_paths=[str(root)])
        # 【为什么连"链接自身"也要拒】`root/link` 这个字符串在 root 内，但**访问它
        # 就是访问 root 之外**（内核解析到 `outside`）。放行它等于放行逃逸，
        # 故 realpath 归一后它同样出界 ⇒ 拒。这正是"解链接"相对 abspath 的价值。
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(link))
        # 经链接出去的路径必须被拒
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(link / "secret.txt"))
        # **目标文件还不存在**时同样要被拒（否则"探测不存在的路径"就成了绕过面）
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(link / "not-created-yet.txt"))
        # 建立真实文件后再验一次
        (outside / "secret.txt").write_text("x", encoding="utf-8")
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(link / "secret.txt"))
        # 对照：root 内的真实文件照常放行（守卫不是"全拒"）
        (root / "ok.txt").write_text("x", encoding="utf-8")
        assert sb.check_path(str(root / "ok.txt")) is True

    @pytest.mark.skipif(os.name != "nt", reason="8.3 短名是 Windows 语义（D7）")
    def test_windows_short_name_escape_is_rejected(self, tmp_path):
        """Windows 8.3 短名：`PROGRA~1` 形态的路径不得绕过前缀比较"""
        import ctypes
        long_dir = tmp_path / "VeryLongDirectoryNameForShortNameTest"
        long_dir.mkdir()
        buf = ctypes.create_unicode_buffer(1024)
        written = ctypes.windll.kernel32.GetShortPathNameW(
            str(long_dir), buf, len(buf))
        if not written or buf.value == str(long_dir):
            pytest.skip("本机未启用 8.3 短名生成（NtfsDisable8dot3NameCreation=1）")
        short = buf.value

        sb = SB.Sandbox(allowed_permissions={"read"},
                        allowed_paths=[str(long_dir)])
        # 长短名指向同一目录 ⇒ 都应放行（归一后相等）
        assert sb.check_path(short) is True
        # 而一个**只在长名下才是兄弟目录**的路径不得因短名写法被放行
        sibling = tmp_path / "VeryLongDirectoryNameForShortNameTestX"
        sibling.mkdir()
        with pytest.raises(SB.PermissionDenied):
            sb.check_path(str(sibling))

    def test_normalize_path_resolves_traversal(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        escape = str(root / ".." / "root_evil")
        assert not SB.path_within(escape, str(root))
        assert SB.path_within(str(root / "." / "a.txt"), str(root))


# ════════════════════════════════════════════════════════════
#  五、E8：诚实的隔离级别标注（不得声称有容器隔离）
# ════════════════════════════════════════════════════════════


class TestIsolationHonesty:
    def test_isolation_level_is_process(self):
        assert SB.isolation_level() == "process"

    def test_isolation_level_has_one_source_of_truth(self):
        """三处 `isolation_level` 常量必须一致（D1：不得各写各的）

        三处：`agent/subagent/sandbox.py`（运行期事实）/
        `agent/lines/models.py`（字段默认值）/ `agent/lines/callability.py`（清单取值域）。
        `callability` 与 `models` 各列一次是**刻意**的（避免新增依赖边，与 `LOCATIONS`
        同款取舍）⇒ 一致性必须由测试锁死，否则三处会各自漂移。
        """
        from agent.lines import callability as C
        from agent.lines import models as M
        assert SB.isolation_level() == M.DEFAULT_ISOLATION_LEVEL == C.DEFAULT_ISOLATION_LEVEL
        assert M.ISOLATION_LEVELS == C.ISOLATION_LEVELS
        assert "container" in M.ISOLATION_LEVELS

    def test_manifest_marks_no_container_isolation(self):
        """清单里能看见"当前没有容器隔离"（E8 / §4 第 12 项）"""
        from agent.lines.callability import build_manifest
        manifest = build_manifest(include_runtime_catalog=True)
        entries = manifest["entries"]
        assert entries
        assert all(e.get("isolation_level") == "process" for e in entries)
        counts = manifest["counts"]["by_isolation_level"]
        assert counts["container"] == 0, "不得声称存在容器级隔离"
        assert counts["none"] == 0
        assert counts["process"] == len(entries)

    def test_container_adapters_are_not_implemented(self):
        sb = SB.Sandbox()
        assert sb.get_docker_sandbox() is None
        assert sb.get_wasm_sandbox() is None
        status = sb.get_status()
        assert status["docker_available"] is False
        assert status["wasm_available"] is False

    def test_no_container_runtime_dependency(self):
        """E8：不得引入 Docker/gVisor/Firecracker/WASM 运行时依赖（D3）"""
        import agent.subagent.sandbox as mod
        source = open(mod.__file__, encoding="utf-8").read()
        for banned in ("import docker", "from docker", "import gvisor",
                       "import firecracker", "import wasmtime", "import wasmer"):
            assert banned not in source, f"不得引入重依赖：{banned}"
