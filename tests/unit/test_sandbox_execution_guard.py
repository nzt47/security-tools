#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务 7：沙箱执行隔离单元测试

覆盖 sandbox.py 新增执行校验接口（进程级隔离，无容器依赖）：
- validate_command：危险命令识别（rm -rf / DROP TABLE os.system 等）+ 默认拒绝
- validate_network：外网写操作默认拒绝，白名单域名放行，file:// 拦截
- run_sandboxed：子进程 + 超时 kill + 输出截断

验收标准对应：
- #1  validate_command("rm -rf /") 返回拒绝且 reason 非空
- #2  run_sandboxed 超时被 kill 且输出截断
- #3  默认 network write 被拒，白名单域名放行
"""
import sys
from unittest.mock import patch

from agent.subagent.sandbox import (
    Sandbox,
    SandboxResourceLimits,
)


class TestValidateCommand:
    """危险命令识别（验收 #1）"""

    def test_rejects_rm_rf(self):
        """rm -rf / → 拒绝且 reason 非空（permission_system 黑名单命中）"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        verdict = sandbox.validate_command("rm -rf /")
        assert verdict.allowed is False
        assert verdict.reason
        assert verdict.matched_pattern

    def test_rejects_drop_table(self):
        """DROP TABLE → 拒绝（本模块补充 SQL 破坏模式）"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        verdict = sandbox.validate_command("DROP TABLE users;")
        assert verdict.allowed is False
        assert "DROP" in verdict.reason

    def test_rejects_os_system(self):
        """Python os.system → 拒绝（任意命令执行后门）"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        verdict = sandbox.validate_command("os.system('shutdown -s')")
        assert verdict.allowed is False
        assert "os.system" in verdict.reason

    def test_rejects_eval_exec(self):
        """Python eval/exec → 拒绝"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        assert sandbox.validate_command("eval(user_input)").allowed is False
        assert sandbox.validate_command("exec(code)").allowed is False

    def test_default_deny_without_execute_permission(self):
        """无 execute 权限 → 默认拒绝（未显式授权即拒绝）"""
        sandbox = Sandbox(allowed_permissions={"read"})
        verdict = sandbox.validate_command("python -c 'print(1)'")
        assert verdict.allowed is False
        assert "execute" in verdict.reason

    def test_allows_safe_command(self):
        """安全命令（execute 权限）→ 放行"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        verdict = sandbox.validate_command("python -c 'print(1)'")
        assert verdict.allowed is True

    def test_rejects_empty_command(self):
        """空/非法命令 → 拒绝"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        assert sandbox.validate_command("").allowed is False
        assert sandbox.validate_command("   ").allowed is False
        assert sandbox.validate_command(None).allowed is False


class TestValidateNetwork:
    """网络校验（验收 #3）"""

    def test_network_write_default_rejected(self):
        """默认外网写操作被拒"""
        sandbox = Sandbox()
        verdict = sandbox.validate_network("https://evil.com/api", "POST")
        assert verdict.allowed is False
        assert "拒绝" in verdict.reason

    def test_network_write_whitelisted_domain_allowed(self):
        """白名单域名写操作放行"""
        sandbox = Sandbox(allowed_network_domains=["trusted.com"])
        verdict = sandbox.validate_network("https://trusted.com/api", "PUT")
        assert verdict.allowed is True

    def test_network_write_other_methods_rejected(self):
        """DELETE/PATCH 同样默认拒绝"""
        sandbox = Sandbox()
        assert sandbox.validate_network("https://evil.com/x", "DELETE").allowed is False
        assert sandbox.validate_network("https://evil.com/x", "PATCH").allowed is False

    def test_network_read_default_allowed(self):
        """读请求默认放行"""
        sandbox = Sandbox()
        assert sandbox.validate_network("https://example.com/data", "GET").allowed is True
        assert sandbox.validate_network("https://example.com/data", "HEAD").allowed is True

    def test_network_file_protocol_rejected(self):
        """file:// 本地文件访问拦截"""
        sandbox = Sandbox()
        assert sandbox.validate_network("file:///etc/passwd", "GET").allowed is False

    def test_network_invalid_url_rejected(self):
        """无协议 URL 拒绝"""
        sandbox = Sandbox()
        assert sandbox.validate_network("not-a-url", "GET").allowed is False


class TestRunSandboxed:
    """沙箱执行器（验收 #2）"""

    def test_rejects_dangerous_command_before_spawn(self):
        """危险命令在启动子进程前被拒（returncode None = 未执行）"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        result = sandbox.run_sandboxed("rm -rf /")
        assert result.allowed is False
        assert result.reason
        assert result.returncode is None

    def test_simulated_execution_blocks_dangerous_command(self):
        """模拟执行 rm -rf：拦截逻辑生效，子进程 Popen 从未被启动（真正的"未执行"）

        即便分身拥有 execute 权限，run_sandboxed 也必须在 spawn 之前拦截危险命令。
        用 patch 替换 subprocess.Popen 验证其绝不被调用——比 returncode None
        更强的"未执行"证据。
        """
        sandbox = Sandbox(allowed_permissions={"execute"})
        with patch("agent.subagent.sandbox.subprocess.Popen") as m_popen:
            result = sandbox.run_sandboxed("rm -rf /important")
            # 拦截生效：子进程从未启动
            m_popen.assert_not_called()
        assert result.allowed is False
        assert "危险" in result.reason
        assert result.returncode is None
        assert result.stdout == ""
        assert result.stderr == ""

    def test_timeout_kills_process(self):
        """超时（0.1s 限制 + 1s sleep 命令）→ kill + timed_out（跨平台写法）"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        limits = SandboxResourceLimits(timeout_s=0.1, max_output_bytes=64)
        result = sandbox.run_sandboxed(
            [sys.executable, "-c", "import time; time.sleep(1)"],
            limits=limits,
        )
        assert result.timed_out is True
        # 已启动并 kill → returncode 非 None（Windows 上为 1 或负值）
        assert result.returncode is not None
        # 输出截断（无输出命令 + 上限 64B → 空串也满足 ≤ 上限）
        assert len(result.stdout) <= limits.max_output_bytes
        assert len(result.stderr) <= limits.max_output_bytes

    def test_output_truncated(self):
        """输出截断：1000 字符输出 + 100B 上限 → 截断"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        limits = SandboxResourceLimits(timeout_s=10, max_output_bytes=100)
        result = sandbox.run_sandboxed(
            [sys.executable, "-c", "print('A' * 1000)"],
            limits=limits,
        )
        assert result.allowed is True
        assert result.returncode == 0
        assert len(result.stdout) <= limits.max_output_bytes

    def test_success_result(self):
        """正常命令执行 → allowed + 退出码 0"""
        sandbox = Sandbox(allowed_permissions={"execute"})
        result = sandbox.run_sandboxed(
            [sys.executable, "-c", "print('ok')"],
            limits=SandboxResourceLimits(timeout_s=10),
        )
        assert result.allowed is True
        assert result.returncode == 0
        assert "ok" in result.stdout
