"""agent.extensions.sandbox 单元测试

覆盖 SandboxPermission / ResourceLimits / SandboxContext / ExecutionResult
数据结构、PluginSandbox（沙箱创建、权限校验、函数执行、子进程执行、文件
读写、路径安全、销毁、审计日志、状态查询）以及 SandboxManager 单例管理。
外部子进程调用通过 mock 隔离，其余业务逻辑均为真实执行。
"""

import logging
import os
from types import SimpleNamespace
from unittest import mock

import pytest

from agent.extensions import sandbox as mod
from agent.extensions.sandbox import (
    ExecutionResult,
    PluginSandbox,
    ResourceLimits,
    SandboxContext,
    SandboxManager,
    SandboxPermission,
    get_sandbox_manager,
)


@pytest.fixture
def sb():
    """构造 PluginSandbox，并在测试后销毁所有残留沙箱（清理临时目录）。"""
    s = PluginSandbox()
    yield s
    for pid in list(s._active_sandboxes.keys()):
        s.destroy_sandbox(pid)


# ── 数据结构 ────────────────────────────────────────────────────────────


def test_sandbox_permission_values():
    """SandboxPermission 枚举包含 7 个权限级别且值正确。"""
    assert len(SandboxPermission) == 7
    assert SandboxPermission.NONE.value == "none"
    assert SandboxPermission.READ_FILES.value == "read_files"
    assert SandboxPermission.WRITE_FILES.value == "write_files"
    assert SandboxPermission.EXECUTE_CODE.value == "execute_code"
    assert SandboxPermission.NETWORK_ACCESS.value == "network"
    assert SandboxPermission.SYSTEM_COMMANDS.value == "system"
    assert SandboxPermission.ADMIN.value == "admin"


def test_resource_limits_defaults():
    """ResourceLimits 默认资源限制值。"""
    rl = ResourceLimits()
    assert rl.max_cpu_percent == 50.0
    assert rl.max_memory_mb == 256
    assert rl.max_disk_mb == 100
    assert rl.max_execution_time == 30
    assert rl.max_network_requests == 100


def test_sandbox_context_defaults():
    """SandboxContext 默认字段（work_dir/env_vars 等）。"""
    ctx = SandboxContext(plugin_id="p1", permissions=[], resource_limits=ResourceLimits())
    assert ctx.work_dir == ""
    assert ctx.env_vars == {}
    assert ctx.start_time == ""
    assert ctx.cpu_usage == 0.0
    assert ctx.network_requests == 0


def test_execution_result_defaults():
    """ExecutionResult 默认字段。"""
    res = ExecutionResult(success=False)
    assert res.output == ""
    assert res.error == ""
    assert res.duration_ms == 0
    assert res.resource_usage == {}


# ── PluginSandbox：创建与权限 ───────────────────────────────────────────


def test_create_sandbox(sb):
    """create_sandbox 应创建隔离工作目录、注入环境变量并记录审计。"""
    ctx = sb.create_sandbox("p1", ["read_files"])
    assert ctx.plugin_id == "p1"
    assert ctx.permissions == ["read_files"]
    assert isinstance(ctx.resource_limits, ResourceLimits)
    assert os.path.basename(ctx.work_dir).startswith("plugin_p1_")
    assert os.path.isdir(ctx.work_dir)
    assert ctx.env_vars["PLUGIN_SANDBOXED"] == "true"
    assert ctx.env_vars["PYTHONPATH"] == ""
    assert ctx.start_time != ""
    assert sb._active_sandboxes["p1"] is ctx
    assert any(e["action"] == "sandbox_created" for e in sb.get_audit_log())


def test_create_sandbox_duplicate_returns_same(sb):
    """重复创建同一插件沙箱应返回既有实例，不新建目录。"""
    c1 = sb.create_sandbox("p1", ["read_files"])
    c2 = sb.create_sandbox("p1", ["write_files"])
    assert c1 is c2
    assert c1.permissions == ["read_files"]  # 不覆盖
    assert len(sb._active_sandboxes) == 1


def test_create_sandbox_custom_limits(sb):
    """传入自定义 ResourceLimits 时应原样使用。"""
    limits = ResourceLimits(max_cpu_percent=10, max_memory_mb=64)
    ctx = sb.create_sandbox("p1", [], resource_limits=limits)
    assert ctx.resource_limits is limits


def test_check_permission(sb):
    """check_permission 对已授权/未授权/不存在插件返回正确结果。"""
    sb.create_sandbox("p1", ["read_files"])
    assert sb.check_permission("p1", SandboxPermission.READ_FILES) is True
    assert sb.check_permission("p1", SandboxPermission.WRITE_FILES) is False
    assert sb.check_permission("ghost", SandboxPermission.READ_FILES) is False


# ── PluginSandbox：函数执行 ─────────────────────────────────────────────


def test_execute_in_sandbox_success(sb):
    """execute_in_sandbox 正常执行应返回输出、成功标志与审计记录。"""
    sb.create_sandbox("p1", ["execute_code"])

    def _double(x):
        return x * 2

    res = sb.execute_in_sandbox("p1", _double, 21)
    assert res.success is True
    assert res.output == 42
    assert res.duration_ms >= 0
    assert any(e["action"] == "execution_success" for e in sb.get_audit_log("p1"))


def test_execute_in_sandbox_function_error(sb):
    """函数抛出异常时结果标记失败并记录 execution_error 审计。"""
    sb.create_sandbox("p1", ["execute_code"])

    def _bad():
        raise ValueError("oops")

    res = sb.execute_in_sandbox("p1", _bad)
    assert res.success is False
    assert "oops" in res.error
    assert any(e["action"] == "execution_error" for e in sb.get_audit_log("p1"))


def test_execute_in_sandbox_not_found(sb):
    """插件未创建沙箱时返回 Sandbox not found。"""
    res = sb.execute_in_sandbox("ghost", lambda: 1)
    assert res.success is False
    assert "Sandbox not found for plugin: ghost" in res.error


def test_execute_in_sandbox_already_running(sb):
    """插件正在运行（_running_plugins 已含）时拒绝重复执行。"""
    sb.create_sandbox("p1", ["execute_code"])
    sb._running_plugins.add("p1")
    res = sb.execute_in_sandbox("p1", lambda: 1)
    assert res.success is False
    assert "already running" in res.error


# ── PluginSandbox：子进程执行 ───────────────────────────────────────────


def test_execute_subprocess_success(sb):
    """execute_subprocess 成功（returncode=0）应返回 stdout 并记录审计。"""
    sb.create_sandbox("p1", ["execute_code"])
    fake = SimpleNamespace(returncode=0, stdout="hello", stderr="")
    with mock.patch.object(mod.subprocess, "run", return_value=fake) as mrun:
        res = sb.execute_subprocess("p1", ["echo", "hello"])
    assert res.success is True
    assert res.output == "hello"
    mrun.assert_called_once()
    # 传入 cwd 与执行超时
    _, kwargs = mrun.call_args
    assert kwargs["timeout"] == 30
    assert any(e["action"] == "subprocess_executed" for e in sb.get_audit_log("p1"))


def test_execute_subprocess_nonzero_exit(sb):
    """子进程非零退出码时 success=False 且 error 取 stderr。"""
    sb.create_sandbox("p1", ["execute_code"])
    fake = SimpleNamespace(returncode=1, stdout="", stderr="boom")
    with mock.patch.object(mod.subprocess, "run", return_value=fake):
        res = sb.execute_subprocess("p1", ["false"])
    assert res.success is False
    assert res.error == "boom"


def test_execute_subprocess_not_found(sb):
    """插件未创建沙箱时返回 Sandbox not found。"""
    res = sb.execute_subprocess("ghost", ["echo"])
    assert res.success is False
    assert "Sandbox not found for plugin: ghost" in res.error


def test_execute_subprocess_permission_denied(sb):
    """缺少 execute_code 权限时拒绝执行子进程。"""
    sb.create_sandbox("p1", ["read_files"])  # 无 execute_code
    res = sb.execute_subprocess("p1", ["echo", "x"])
    assert res.success is False
    assert "Permission denied: execute_code" in res.error


def test_execute_subprocess_timeout(sb):
    """子进程超时返回 Execution timeout。"""
    sb.create_sandbox("p1", ["execute_code"])
    with mock.patch.object(
        mod.subprocess, "run", side_effect=mod.subprocess.TimeoutExpired("cmd", 30)
    ):
        res = sb.execute_subprocess("p1", ["sleep", "60"])
    assert res.success is False
    assert res.error == "Execution timeout"


def test_execute_subprocess_other_error(sb):
    """子进程执行抛其他异常时返回其错误信息。"""
    sb.create_sandbox("p1", ["execute_code"])
    with mock.patch.object(mod.subprocess, "run", side_effect=OSError("no such file")):
        res = sb.execute_subprocess("p1", ["nope"])
    assert res.success is False
    assert "no such file" in res.error


# ── PluginSandbox：文件读写与路径安全 ───────────────────────────────────


def test_read_file_success(sb):
    """具备 read_files 权限时可读取沙箱内文件。"""
    sb.create_sandbox("p1", ["read_files"])
    ctx = sb._active_sandboxes["p1"]
    fp = os.path.join(ctx.work_dir, "a.txt")
    with open(fp, "w", encoding="utf-8") as f:
        f.write("hello sandbox")
    res = sb.read_file("p1", fp)
    assert res.success is True
    assert res.output == "hello sandbox"
    assert any(e["action"] == "file_read" for e in sb.get_audit_log("p1"))


def test_read_file_permission_denied(sb):
    """缺少 read_files 权限时拒绝读取。"""
    sb.create_sandbox("p1", [])
    res = sb.read_file("p1", "whatever.txt")
    assert res.success is False
    assert "Permission denied: read_files" in res.error


def test_read_file_unsafe_path(sb):
    """读取系统危险路径应被拒绝（路径遍历防护）。"""
    sb.create_sandbox("p1", ["read_files"])
    res = sb.read_file("p1", "C:\\Windows\\System32\\drivers\\etc\\hosts")
    assert res.success is False
    assert "Access denied" in res.error


def test_read_file_not_found(sb):
    """读取不存在的文件返回失败而非抛出异常。"""
    sb.create_sandbox("p1", ["read_files"])
    ctx = sb._active_sandboxes["p1"]
    res = sb.read_file("p1", os.path.join(ctx.work_dir, "missing.txt"))
    assert res.success is False
    assert "No such file" in res.error


def test_write_file_success(sb):
    """具备 write_files 权限时可写入沙箱内文件。"""
    sb.create_sandbox("p1", ["write_files"])
    ctx = sb._active_sandboxes["p1"]
    fp = os.path.join(ctx.work_dir, "out.txt")
    res = sb.write_file("p1", fp, "content-123")
    assert res.success is True
    with open(fp, encoding="utf-8") as f:
        assert f.read() == "content-123"
    assert any(e["action"] == "file_written" for e in sb.get_audit_log("p1"))


def test_write_file_permission_denied(sb):
    """缺少 write_files 权限时拒绝写入。"""
    sb.create_sandbox("p1", [])
    res = sb.write_file("p1", "x.txt", "x")
    assert res.success is False
    assert "Permission denied: write_files" in res.error


def test_write_file_unsafe_path(sb):
    """写入系统危险路径应被拒绝。"""
    sb.create_sandbox("p1", ["write_files"])
    # Windows 下 /etc 经 abspath 归一化后不命中危险路径表，需用 Windows 危险路径
    res = sb.write_file("p1", "C:\\Windows\\System32\\config\\SAM", "x")
    assert res.success is False
    assert "Access denied" in res.error


def test_write_file_io_error(sb):
    """写入不可写目标（目录）时返回失败而非抛出异常。"""
    sb.create_sandbox("p1", ["write_files"])
    ctx = sb._active_sandboxes["p1"]
    res = sb.write_file("p1", ctx.work_dir, "x")  # work_dir 是目录
    assert res.success is False


def test_is_path_safe(sb):
    """_is_path_safe 识别危险路径，放行安全路径。"""
    assert sb._is_path_safe("/etc/passwd") is False
    assert sb._is_path_safe("/usr/bin/x") is False
    assert sb._is_path_safe("C:\\Windows\\System32\\x") is False
    assert sb._is_path_safe("C:\\Program Files\\x") is False
    assert sb._is_path_safe("C:\\safe\\data\\file.txt") is True
    assert sb._is_path_safe("/home/user/data.txt") is True


# ── PluginSandbox：销毁 ─────────────────────────────────────────────────


def test_destroy_sandbox(sb):
    """销毁沙箱应删除工作目录、移出活动列表并记录审计。"""
    sb.create_sandbox("p1", [])
    ctx = sb._active_sandboxes["p1"]
    assert os.path.isdir(ctx.work_dir)
    sb.destroy_sandbox("p1")
    assert not os.path.isdir(ctx.work_dir)
    assert "p1" not in sb._active_sandboxes
    assert any(e["action"] == "sandbox_destroyed" for e in sb.get_audit_log())


def test_destroy_sandbox_not_exist(sb):
    """销毁不存在的沙箱无副作用。"""
    sb.destroy_sandbox("ghost")  # 不应抛异常
    assert sb._active_sandboxes == {}


def test_destroy_sandbox_cleanup_error(sb, caplog):
    """rmtree 失败时应记录 warning 且不影响后续销毁流程。"""
    sb.create_sandbox("p1", [])
    with caplog.at_level(logging.WARNING, logger="agent.extensions.sandbox"):
        with mock.patch("shutil.rmtree", side_effect=OSError("denied")):
            sb.destroy_sandbox("p1")
    assert "Failed to cleanup sandbox dir" in caplog.text
    assert "p1" not in sb._active_sandboxes
    assert any(e["action"] == "sandbox_destroyed" for e in sb.get_audit_log())


# ── PluginSandbox：审计与状态 ───────────────────────────────────────────


def test_audit_log_truncation(sb):
    """审计日志超过 1000 条时保留最近 1000 条。"""
    for i in range(1001):
        sb._log_audit("action", "p1", {"i": i})
    assert len(sb._audit_log) == 1000
    assert sb._audit_log[-1]["details"]["i"] == 1000
    assert sb._audit_log[0]["details"]["i"] == 1  # 最旧一条被截掉


def test_get_audit_log_filter_and_limit(sb):
    """get_audit_log 支持按插件过滤与 limit 截断。"""
    sb.create_sandbox("p1", [])
    sb.create_sandbox("p2", [])
    sb.execute_in_sandbox("p1", lambda: 1)
    p1_logs = sb.get_audit_log("p1")
    assert p1_logs and all(l["plugin_id"] == "p1" for l in p1_logs)
    limited = sb.get_audit_log(limit=2)
    assert len(limited) == 2
    assert len(sb.get_audit_log()) <= 100


def test_get_sandbox_status(sb):
    """get_sandbox_status 返回状态字典；不存在的插件返回 None。"""
    sb.create_sandbox("p1", ["read_files"])
    status = sb.get_sandbox_status("p1")
    assert status["plugin_id"] == "p1"
    assert status["permissions"] == ["read_files"]
    assert os.path.basename(status["work_dir"]).startswith("plugin_p1_")
    assert status["is_running"] is False
    assert status["resource_limits"]["max_memory_mb"] == 256
    sb._running_plugins.add("p1")
    assert sb.get_sandbox_status("p1")["is_running"] is True
    assert sb.get_sandbox_status("ghost") is None


def test_list_sandboxes(sb):
    """list_sandboxes 返回所有活动沙箱的状态。"""
    sb.create_sandbox("p1", [])
    sb.create_sandbox("p2", [])
    statuses = sb.list_sandboxes()
    assert len(statuses) == 2
    assert {s["plugin_id"] for s in statuses} == {"p1", "p2"}


# ── SandboxManager ──────────────────────────────────────────────────────


def test_sandbox_manager_singleton(monkeypatch):
    """SandboxManager 是单例，get_sandbox_manager 返回同一实例。"""
    monkeypatch.setattr(SandboxManager, "_instance", None)
    m1 = SandboxManager()
    m2 = SandboxManager()
    assert m1 is m2
    assert get_sandbox_manager() is m1


def test_sandbox_manager_get_sandbox_reuse():
    """get_sandbox 同一插件返回同一 PluginSandbox，不同插件相互独立。"""
    mgr = SandboxManager()
    mgr.destroy_all()  # 清理残留
    s1 = mgr.get_sandbox("plugin-a")
    assert isinstance(s1, PluginSandbox)
    assert mgr.get_sandbox("plugin-a") is s1
    assert mgr.get_sandbox("plugin-b") is not s1
    mgr.destroy_all()


def test_sandbox_manager_destroy_all():
    """destroy_all 销毁并移除所有沙箱。"""
    mgr = SandboxManager()
    mgr.destroy_all()
    s1 = mgr.get_sandbox("plugin-a")
    s2 = mgr.get_sandbox("plugin-b")
    assert s1 is not s2
    mgr.destroy_all()
    assert mgr._sandboxes == {}
