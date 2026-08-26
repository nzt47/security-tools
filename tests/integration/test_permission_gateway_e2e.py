"""
PermissionGateway 三层权限架构端到端集成测试

模拟真实用户在不同角色、不同时间、不同 IP 下的完整调用流程,
验证三层架构(RBAC → ABAC → 正则黑名单)的端到端行为。

测试矩阵:
                    | GUEST      | DEVELOPER  | ADMIN
                    | (仅查询)   | (受限)     | (全工具+ABAC约束)
─────────────────────┼───────────┼────────────┼──────────────────
工作时间+CLI+内网IP  | 查询通过   | Shell通过  | 全工具通过
                    |           |            | system_format通过
非工作时间+CLI       | N/A(RBAC拦)| ABAC拦Shell| N/A(ABAC不拦admin的shell)
scheduled来源       | N/A       | ABAC拦写入  | N/A
外网IP              | N/A       | N/A        | ABAC拦format/shutdown
rm -rf /            | RBAC先拦   | 正则拦     | 正则拦
降级模式            | RBAC跳过   | RBAC跳过   | RBAC跳过,正则仍拦

运行:
    python -m pytest tests/integration/test_permission_gateway_e2e.py -v
"""
import json
import os
import pytest
import tempfile
from unittest.mock import patch

from agent.permission_system import (
    PermissionGateway,
    PermissionSystem,
    PermissionResult,
    Role,
    ABACContext,
)


# ────────────────────────────────────────────────────────────
# 策略: 与 data/permission_policies.json 同构,含 admin ABAC 约束
# ────────────────────────────────────────────────────────────

E2E_POLICY = {
    "version": 1,
    "default_role": "guest",
    "roles": {
        "admin": {
            "description": "管理员,全部工具可用(ABAC 约束危险操作)",
            "allowed_tools": ["*"],
            "denied_tools": []
        },
        "developer": {
            "description": "开发者",
            "allowed_tools": [
                "web_search", "file_read", "file_write",
                "shell_execute", "code_runner",
            ],
            "denied_tools": ["system_format", "system_shutdown"],
        },
        "guest": {
            "description": "访客,仅查询",
            "allowed_tools": ["web_search", "file_read"],
            "denied_tools": [],
        },
    },
    "abac_rules": [
        {
            "name": "off-hours-shell-restriction",
            "tool": "shell_execute",
            "deny_if": {"time_outside": ["09:00", "18:00"]},
        },
        {
            "name": "scheduled-no-write",
            "tool": "file_write",
            "deny_if": {"session_source_in": ["scheduled"]},
        },
        {
            "name": "internal-only-format",
            "tool": "system_format",
            "deny_if": {
                "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]
            },
        },
        {
            "name": "admin-shutdown-internal-only",
            "tool": "system_shutdown",
            "deny_if": {
                "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16", "172.16.0.0/12"]
            },
        },
    ],
}


@pytest.fixture
def gateway():
    """用临时策略文件构造 PermissionGateway"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(E2E_POLICY, f, ensure_ascii=False)
        path = f.name
    try:
        yield PermissionGateway(policy_path=path)
    finally:
        os.unlink(path)


@pytest.fixture
def degraded_gateway():
    """降级模式 PermissionGateway(策略文件不存在)"""
    return PermissionGateway(policy_path="/nonexistent/path.json")


# ════════════════════════════════════════════════════════════
# 端到端: GUEST 用户完整流程
# ════════════════════════════════════════════════════════════

class TestGuestE2E:
    """GUEST 用户端到端测试"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_guest_query_tools_allowed(self, gateway):
        """GUEST 可用查询工具(web_search/file_read)"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        assert gateway.check("web_search", {"q": "test"}, ctx).allowed
        assert gateway.check("file_read", {"path": "/tmp/x"}, ctx).allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_guest_shell_blocked_by_rbac(self, gateway):
        """GUEST 调 shell_execute 被 RBAC 拦截(短路, ABAC 不执行)"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gateway.check("shell_execute", {"cmd": "ls"}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_guest_rm_rf_blocked_by_rbac_not_regex(self, gateway):
        """GUEST 执行 rm -rf / 被 RBAC 先拦截(reason=权限不足,非黑名单)"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gateway.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"
        assert "黑名单" not in result.reason

    @pytest.mark.integration
    @pytest.mark.p1
    def test_guest_file_write_blocked(self, gateway):
        """GUEST 不可写文件"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gateway.check("file_write", {"path": "/tmp/x"}, ctx)
        assert not result.allowed


# ════════════════════════════════════════════════════════════
# 端到端: DEVELOPER 用户完整流程
# ════════════════════════════════════════════════════════════

class TestDeveloperE2E:
    """DEVELOPER 用户端到端测试"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_work_hours_shell_allowed(self, gateway):
        """DEVELOPER 工作时间执行安全 Shell → 通过"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            result = gateway.check("shell_execute", {"cmd": "ls -la"}, ctx)
        assert result.allowed
        assert not result.requires_confirmation

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_off_hours_shell_blocked(self, gateway):
        """DEVELOPER 非工作时间执行 Shell → ABAC 拦截"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        with patch.object(PermissionGateway, "_time_in_window", return_value=False):
            result = gateway.check("shell_execute", {"cmd": "ls"}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_scheduled_write_blocked(self, gateway):
        """DEVELOPER 定时任务来源写文件 → ABAC 拦截"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="scheduled")
        result = gateway.check("file_write", {"path": "/tmp/x"}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_cli_write_allowed(self, gateway):
        """DEVELOPER CLI 来源写文件 → 通过"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gateway.check("file_write", {"path": "/tmp/x", "content": "x"}, ctx)
        assert result.allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_rm_rf_blocked_by_regex(self, gateway):
        """DEVELOPER 执行 rm -rf / → RBAC+ABAC通过, 正则黑名单拦截"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            result = gateway.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert not result.allowed
        assert "黑名单" in result.reason

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_dangerous_needs_confirmation(self, gateway):
        """DEVELOPER 执行 rm -rf my_folder → 正则兜底, 需二次确认"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            result = gateway.check("shell_execute", {"cmd": "rm -rf my_folder"}, ctx)
        assert result.allowed
        assert result.requires_confirmation

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_system_format_blocked_by_rbac(self, gateway):
        """DEVELOPER 调 system_format → RBAC denied_tools 拦截"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gateway.check("system_format", {}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"


# ════════════════════════════════════════════════════════════
# 端到端: ADMIN 用户完整流程(含 ABAC 约束)
# ════════════════════════════════════════════════════════════

class TestAdminE2E:
    """ADMIN 用户端到端测试

    admin 的 allowed_tools=["*"] 使其通过所有 RBAC 检查,
    但 ABAC 规则仍约束危险操作(system_format/system_shutdown 仅内网 IP)。
    """

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_all_tools_pass_rbac(self, gateway):
        """ADMIN 通过 RBAC 检查所有工具"""
        ctx = ABACContext(role=Role.ADMIN, session_source="cli", ip="10.0.0.1")
        for tool in ["web_search", "file_read", "file_write", "shell_execute"]:
            with patch.object(PermissionGateway, "_time_in_window", return_value=True):
                result = gateway.check(tool, {"cmd": "ls"}, ctx)
            assert result.allowed, f"ADMIN 应可调用 {tool}"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_format_internal_ip_allowed(self, gateway):
        """ADMIN 内网 IP 调 system_format → 通过"""
        ctx = ABACContext(role=Role.ADMIN, ip="192.168.1.100")
        result = gateway.check("system_format", {}, ctx)
        assert result.allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_format_external_ip_blocked(self, gateway):
        """ADMIN 外网 IP 调 system_format → ABAC 拦截"""
        ctx = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        result = gateway.check("system_format", {}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_shutdown_internal_ip_allowed(self, gateway):
        """ADMIN 内网 IP 调 system_shutdown → 通过"""
        ctx = ABACContext(role=Role.ADMIN, ip="10.0.0.1")
        result = gateway.check("system_shutdown", {}, ctx)
        assert result.allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_shutdown_external_ip_blocked(self, gateway):
        """ADMIN 外网 IP 调 system_shutdown → ABAC 拦截(ADMIN 约束)"""
        ctx = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        result = gateway.check("system_shutdown", {}, ctx)
        assert not result.allowed
        assert result.reason == "权限不足"

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_rm_rf_blocked_by_regex(self, gateway):
        """ADMIN 执行 rm -rf / → RBAC+ABAC通过, 正则兜底拦截"""
        ctx = ABACContext(role=Role.ADMIN, session_source="cli", ip="10.0.0.1")
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            result = gateway.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert not result.allowed
        assert "黑名单" in result.reason

    @pytest.mark.integration
    @pytest.mark.p1
    def test_admin_no_ip_blocked_for_format(self, gateway):
        """ADMIN 未提供 IP 调 system_format → ABAC 拦截(ip_not_in_cidr)"""
        ctx = ABACContext(role=Role.ADMIN, ip=None)
        result = gateway.check("system_format", {}, ctx)
        assert not result.allowed


# ════════════════════════════════════════════════════════════
# 端到端: 降级模式
# ════════════════════════════════════════════════════════════

class TestDegradedE2E:
    """降级模式端到端测试"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_degraded_guest_shell_allowed(self, degraded_gateway):
        """降级模式: GUEST 可调 shell_execute(RBAC 跳过)"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = degraded_gateway.check("shell_execute", {"cmd": "ls"}, ctx)
        assert result.allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_degraded_rm_rf_still_blocked(self, degraded_gateway):
        """降级模式: rm -rf / 仍被正则黑名单拦截"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = degraded_gateway.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert not result.allowed
        assert "黑名单" in result.reason

    @pytest.mark.integration
    @pytest.mark.p0
    def test_degraded_dangerous_still_needs_confirmation(self, degraded_gateway):
        """降级模式: 危险操作仍需二次确认"""
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = degraded_gateway.check(
            "shell_execute", {"cmd": "rm -rf my_folder"}, ctx
        )
        assert result.allowed
        assert result.requires_confirmation


# ════════════════════════════════════════════════════════════
# 端到端: 跨角色对比矩阵
# ════════════════════════════════════════════════════════════

class TestCrossRoleMatrix:
    """同一操作在不同角色下的结果对比"""

    @pytest.mark.integration
    @pytest.mark.p0
    @pytest.mark.parametrize("role,expected_allowed", [
        (Role.GUEST, False),
        (Role.DEVELOPER, True),
        (Role.ADMIN, True),
    ])
    def test_shell_execute_access_matrix(self, gateway, role, expected_allowed):
        """shell_execute 在不同角色下的访问权限(RBAC 层)"""
        ctx = ABACContext(role=role, session_source="cli", ip="10.0.0.1")
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            result = gateway.check("shell_execute", {"cmd": "ls"}, ctx)
        assert result.allowed == expected_allowed

    @pytest.mark.integration
    @pytest.mark.p0
    @pytest.mark.parametrize("role,expected_allowed", [
        (Role.GUEST, False),
        (Role.DEVELOPER, False),
        (Role.ADMIN, True),
    ])
    def test_system_format_access_matrix(self, gateway, role, expected_allowed):
        """system_format 在不同角色下的访问权限

        GUEST: RBAC 拦截(不在 allowed_tools)
        DEVELOPER: RBAC 拦截(在 denied_tools)
        ADMIN: RBAC 通过(*), ABAC 需内网 IP(此测试用内网 IP)
        """
        ctx = ABACContext(role=role, ip="10.0.0.1")
        result = gateway.check("system_format", {}, ctx)
        assert result.allowed == expected_allowed

    @pytest.mark.integration
    @pytest.mark.p0
    @pytest.mark.parametrize("ip,expected_allowed", [
        ("10.0.0.1", True),       # 10.0.0.0/8
        ("192.168.1.100", True),  # 192.168.0.0/16
        ("172.16.0.1", True),     # 172.16.0.0/12
        ("203.0.113.1", False),   # 外网
        (None, False),            # 未提供
        ("invalid", False),       # 非法
    ])
    def test_admin_format_ip_matrix(self, gateway, ip, expected_allowed):
        """ADMIN 调 system_format 在不同 IP 下的 ABAC 校验"""
        ctx = ABACContext(role=Role.ADMIN, ip=ip)
        result = gateway.check("system_format", {}, ctx)
        assert result.allowed == expected_allowed


# ════════════════════════════════════════════════════════════
# 端到端: 多工具连续调用(会话模拟)
# ════════════════════════════════════════════════════════════

class TestSessionSimulation:
    """模拟一个完整会话中的多工具调用序列"""

    @pytest.mark.integration
    @pytest.mark.p0
    def test_developer_typical_session(self, gateway):
        """模拟 DEVELOPER 一次典型会话: 搜索→读文件→写文件→执行Shell"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")

        # 1. 搜索
        r = gateway.check("web_search", {"q": "python tutorial"}, ctx)
        assert r.allowed

        # 2. 读文件
        r = gateway.check("file_read", {"path": "/home/user/main.py"}, ctx)
        assert r.allowed

        # 3. 写文件(cli 来源)
        r = gateway.check("file_write", {"path": "/tmp/output.py", "content": "print(1)"}, ctx)
        assert r.allowed

        # 4. 执行 Shell(需在工作时间)
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            r = gateway.check("shell_execute", {"cmd": "python /tmp/output.py"}, ctx)
        assert r.allowed

    @pytest.mark.integration
    @pytest.mark.p0
    def test_admin_dangerous_session(self, gateway):
        """模拟 ADMIN 一次危险会话: 内网格式化→外网关机→rm -rf"""
        # 1. 内网 IP 格式化 → 通过
        ctx_internal = ABACContext(role=Role.ADMIN, ip="10.0.0.1")
        r = gateway.check("system_format", {}, ctx_internal)
        assert r.allowed

        # 2. 外网 IP 关机 → ABAC 拦截
        ctx_external = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        r = gateway.check("system_shutdown", {}, ctx_external)
        assert not r.allowed
        assert r.reason == "权限不足"

        # 3. rm -rf / → 正则兜底拦截(即使 ADMIN)
        with patch.object(PermissionGateway, "_time_in_window", return_value=True):
            r = gateway.check("shell_execute", {"cmd": "rm -rf /"}, ctx_internal)
        assert not r.allowed
        assert "黑名单" in r.reason

    @pytest.mark.integration
    @pytest.mark.p1
    def test_scheduled_session_blocked_write(self, gateway):
        """模拟定时任务会话: 读文件通过, 写文件被 ABAC 拦截"""
        ctx = ABACContext(role=Role.DEVELOPER, session_source="scheduled")

        # 读文件 → 通过(RBAC + ABAC 都不拦)
        r = gateway.check("file_read", {"path": "/data/config.json"}, ctx)
        assert r.allowed

        # 写文件 → ABAC 拦截(session_source_in: ["scheduled"])
        r = gateway.check("file_write", {"path": "/data/output.json"}, ctx)
        assert not r.allowed
        assert r.reason == "权限不足"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
