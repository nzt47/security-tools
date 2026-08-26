"""
PermissionGateway 三层权限架构单元测试

覆盖:
- [层1] RBAC 角色拦截
- [层2] ABAC 属性校验(时间窗口/会话来源/IP 段)
- [层3] 正则黑名单兜底
- 三层叠加场景(RBAC 短路 / ABAC 先于正则)
- 降级模式(策略加载失败)
- ADMIN ABAC 约束(system_format/system_shutdown 仅内网 IP)
- 统一拒绝原因(reason="权限不足",不暴露规则细节)
- JSON trace 日志格式
"""
import json
import os
import pytest
import tempfile
import logging
from unittest.mock import patch

from agent.permission_system import (
    PermissionGateway,
    PermissionSystem,
    PermissionResult,
    Role,
    Permission,
    ABACContext,
)


# ────────────────────────────────────────────────────────────
# 工具:用临时策略文件构造 PermissionGateway(隔离真实配置)
# ────────────────────────────────────────────────────────────

DEFAULT_POLICY = {
    "version": 1,
    "default_role": "guest",
    "roles": {
        "admin": {
            "allowed_tools": ["*"],
            "denied_tools": []
        },
        "developer": {
            "allowed_tools": [
                "web_search", "file_read", "file_write",
                "shell_execute", "code_runner",
            ],
            "denied_tools": ["system_format", "system_shutdown"],
        },
        "guest": {
            "allowed_tools": ["web_search", "file_read"],
            "denied_tools": [],
        },
    },
    "abac_rules": [],
}


def _make_gateway(policy_dict, permission_system=None):
    """用临时策略文件构造 PermissionGateway,测试结束自动清理"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(policy_dict, f, ensure_ascii=False)
        path = f.name
    try:
        return PermissionGateway(
            policy_path=path, permission_system=permission_system
        )
    finally:
        os.unlink(path)


# ════════════════════════════════════════════════════════════
# 初始化与降级模式
# ════════════════════════════════════════════════════════════

class TestGatewayInit:
    """PermissionGateway 初始化测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_valid_policy(self):
        gw = _make_gateway(DEFAULT_POLICY)
        assert gw.is_degraded is False
        assert gw.default_role == Role.GUEST

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_missing_file_degrades(self):
        gw = PermissionGateway(policy_path="/nonexistent/path.json")
        assert gw.is_degraded is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_invalid_json_degrades(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("not valid json", encoding="utf-8")
        gw = PermissionGateway(policy_path=str(bad))
        assert gw.is_degraded is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_init_with_existing_permission_system(self):
        ps = PermissionSystem()
        gw = _make_gateway(DEFAULT_POLICY, permission_system=ps)
        assert gw.get_permission_system() is ps

    @pytest.mark.unit
    @pytest.mark.p0
    def test_independent_instance_no_llm_dependency(self):
        """PermissionGateway 可独立实例化,不依赖 LLM"""
        gw = _make_gateway(DEFAULT_POLICY)
        result = gw.check("web_search", {"q": "test"}, ABACContext(role=Role.GUEST))
        assert isinstance(result, PermissionResult)


# ════════════════════════════════════════════════════════════
# 层1: RBAC 角色拦截
# ════════════════════════════════════════════════════════════

class TestRBAC:
    """RBAC 角色拦截测试"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_all_tools_allowed(self):
        """ADMIN 通配 * → 全部工具通过 RBAC"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        for tool in ["web_search", "shell_execute", "system_format"]:
            result = gw.check(tool, {}, ctx)
            assert result.allowed is True, f"ADMIN 应可调用 {tool}"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_guest_blocked_for_shell(self):
        """GUEST 不在 shell_execute 的 allowed_tools 中 → 拦截"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("shell_execute", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_guest_allowed_for_query_tools(self):
        """GUEST 可调用 web_search/file_read"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("web_search", {"q": "hello"}, ctx)
        assert result.allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_developer_denied_system_tools(self):
        """DEVELOPER 的 denied_tools 含 system_format → 拦截"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_developer_allowed_shell(self):
        """DEVELOPER 的 allowed_tools 含 shell_execute → 通过"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "ls -la"}, ctx)
        assert result.allowed is True
        assert result.requires_confirmation is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_unknown_role_blocked(self):
        """策略中不存在的角色 → RBAC 直接拦截"""
        policy = {
            **DEFAULT_POLICY,
            "roles": {"admin": DEFAULT_POLICY["roles"]["admin"]},
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("web_search", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"


# ════════════════════════════════════════════════════════════
# 层2: ABAC 属性校验
# ════════════════════════════════════════════════════════════

class TestABACTimeWindow:
    """ABAC 时间窗口校验"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_time_window_inside_passes(self):
        """当前时间在窗口内 → 通过 ABAC"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "off-hours",
                "tool": "shell_execute",
                "deny_if": {"time_outside": ["09:00", "18:00"]},
            }],
        }
        gw = _make_gateway(policy)
        with patch.object(
            PermissionGateway, "_time_in_window", return_value=True
        ):
            ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
            result = gw.check("shell_execute", {"cmd": "ls"}, ctx)
        assert result.allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_time_window_outside_blocks(self):
        """当前时间在窗口外 → 拒绝"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "off-hours",
                "tool": "shell_execute",
                "deny_if": {"time_outside": ["09:00", "18:00"]},
            }],
        }
        gw = _make_gateway(policy)
        with patch.object(
            PermissionGateway, "_time_in_window", return_value=False
        ):
            ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
            result = gw.check("shell_execute", {"cmd": "ls"}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_time_window_only_targets_specific_tool(self):
        """时间窗口规则只针对 shell_execute, 不影响 file_read"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "off-hours",
                "tool": "shell_execute",
                "deny_if": {"time_outside": ["09:00", "18:00"]},
            }],
        }
        gw = _make_gateway(policy)
        with patch.object(
            PermissionGateway, "_time_in_window", return_value=False
        ):
            ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
            result = gw.check("file_read", {"path": "/tmp/x"}, ctx)
        assert result.allowed is True


class TestABACSessionSource:
    """ABAC 会话来源校验"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_scheduled_source_blocked_for_write(self):
        """scheduled 来源禁止 file_write"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "scheduled-no-write",
                "tool": "file_write",
                "deny_if": {"session_source_in": ["scheduled"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="scheduled")
        result = gw.check("file_write", {"path": "/tmp/x.txt"}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cli_source_allowed_for_write(self):
        """cli 来源不在拒绝列表 → 通过 ABAC"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "scheduled-no-write",
                "tool": "file_write",
                "deny_if": {"session_source_in": ["scheduled"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check(
            "file_write", {"path": "/tmp/x.txt", "content": "x"}, ctx
        )
        assert result.allowed is True


class TestABACIP:
    """ABAC IP 段校验"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_external_ip_blocked_for_format(self):
        """外网 IP 调用 system_format → 拦截"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "internal-only-format",
                "tool": "system_format",
                "deny_if": {
                    "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16"]
                },
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_internal_ip_allowed(self):
        """内网 IP 通过 CIDR 校验"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "internal-only-format",
                "tool": "system_format",
                "deny_if": {
                    "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16"]
                },
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="192.168.1.100")
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_no_ip_blocks(self):
        """未提供 IP 时 ip_not_in_cidr 视为不匹配 → 拒绝"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "internal-only-format",
                "tool": "system_format",
                "deny_if": {"ip_not_in_cidr": ["10.0.0.0/8"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip=None)
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_invalid_ip_treated_as_blocked(self):
        """非法 IP 字符串视为不在 CIDR 内 → 拒绝"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "internal-only-format",
                "tool": "system_format",
                "deny_if": {"ip_not_in_cidr": ["10.0.0.0/8"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="not-an-ip")
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is False


# ════════════════════════════════════════════════════════════
# ADMIN ABAC 约束(给 admin 加属性约束)
# ════════════════════════════════════════════════════════════

class TestAdminABACConstraint:
    """ADMIN 角色受 ABAC 约束测试

    admin 的 allowed_tools=["*"] 使其通过所有 RBAC 检查,
    但 ABAC 规则仍可拦截危险操作。
    """

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_external_ip_blocked_for_shutdown(self):
        """ADMIN 从外网 IP 调用 system_shutdown → ABAC 拦截"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "admin-shutdown-internal-only",
                "tool": "system_shutdown",
                "deny_if": {
                    "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16"]
                },
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        result = gw.check("system_shutdown", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_internal_ip_allowed_for_shutdown(self):
        """ADMIN 从内网 IP 调用 system_shutdown → 通过"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "admin-shutdown-internal-only",
                "tool": "system_shutdown",
                "deny_if": {
                    "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16"]
                },
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="10.0.0.1")
        result = gw.check("system_shutdown", {}, ctx)
        assert result.allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_external_ip_blocked_for_format(self):
        """ADMIN 从外网 IP 调用 system_format → ABAC 拦截"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "internal-only-format",
                "tool": "system_format",
                "deny_if": {
                    "ip_not_in_cidr": ["10.0.0.0/8", "192.168.0.0/16"]
                },
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, ip="203.0.113.1")
        result = gw.check("system_format", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_still_blocked_by_regex(self):
        """ADMIN 执行 rm -rf / → 正则兜底拦截(ABAC 不拦,正则拦)"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli", ip="10.0.0.1")
        result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert result.allowed is False
        assert "黑名单" in result.reason


# ════════════════════════════════════════════════════════════
# 层3: 正则黑名单兜底
# ════════════════════════════════════════════════════════════

class TestRegexFallback:
    """正则黑名单兜底测试(沿用 PermissionSystem.check_action)"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_blacklist_blocks_rm_rf_root(self):
        """rm -rf / 被 BLACKLIST 拦截,即使 ADMIN 也无法绕过"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert result.allowed is False
        assert "黑名单" in result.reason

    @pytest.mark.unit
    @pytest.mark.p0
    def test_blacklist_blocks_format_c(self):
        """format C: 被 BLACKLIST 拦截"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        result = gw.check(
            "shell_execute", {"cmd": "format C: /fs:ntfs"}, ctx
        )
        assert result.allowed is False

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dangerous_pattern_requires_confirmation(self):
        """危险模式(非黑名单)走正则兜底 → 二次确认"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check(
            "shell_execute", {"cmd": "rm -rf my_folder"}, ctx
        )
        assert result.allowed is True
        assert result.requires_confirmation is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_safe_action_passes_all_layers(self):
        """安全操作三层全通过"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "ls -la"}, ctx)
        assert result.allowed is True
        assert result.requires_confirmation is False


# ════════════════════════════════════════════════════════════
# 三层叠加
# ════════════════════════════════════════════════════════════

class TestThreeLayerStack:
    """三层叠加与短路语义"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rbac_short_circuits_before_abac(self):
        """RBAC 拦截时 ABAC 不应被调用"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "off-hours",
                "tool": "shell_execute",
                "deny_if": {"time_outside": ["09:00", "18:00"]},
            }],
        }
        gw = _make_gateway(policy)
        triggered = []
        original = PermissionGateway._check_abac

        def spy(self, tool_name, context, trace_id=""):
            triggered.append(tool_name)
            return original(self, tool_name, context, trace_id)

        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        with patch.object(PermissionGateway, "_check_abac", spy):
            result = gw.check("shell_execute", {}, ctx)

        assert result.allowed is False
        assert result.reason == "权限不足"
        assert triggered == [], "RBAC 拦截后 ABAC 不应被调用"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_admin_passes_rbac_but_regex_blocks(self):
        """ADMIN 通过 RBAC + ABAC, 最终被正则黑名单拦截"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert result.allowed is False
        assert "黑名单" in result.reason

    @pytest.mark.unit
    @pytest.mark.p0
    def test_abac_blocks_before_regex(self):
        """ABAC 拦截优先于正则黑名单,返回统一 reason"""
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "always-deny-shell",
                "tool": "shell_execute",
                "deny_if": {"session_source_in": ["cli"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        # 即使 cmd=rm -rf /(本应被正则黑名单拦截), ABAC 先拦截
        result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_full_chain_pass(self):
        """三层全部通过的黄金路径"""
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
        result = gw.check(
            "file_read", {"path": "/home/user/notes.md"}, ctx
        )
        assert result.allowed is True
        assert result.requires_confirmation is False


# ════════════════════════════════════════════════════════════
# 降级模式
# ════════════════════════════════════════════════════════════

class TestDegradedMode:
    """策略加载失败时降级到仅正则黑名单"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degraded_skips_rbac(self):
        """降级模式下 GUEST 也可调用 shell_execute(RBAC 跳过)"""
        gw = PermissionGateway(policy_path="/nonexistent/path.json")
        assert gw.is_degraded is True
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "ls -la"}, ctx)
        assert result.allowed is True

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degraded_still_blocks_blacklist(self):
        """降级模式下正则黑名单仍生效"""
        gw = PermissionGateway(policy_path="/nonexistent/path.json")
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("shell_execute", {"cmd": "rm -rf /"}, ctx)
        assert result.allowed is False
        assert "黑名单" in result.reason

    @pytest.mark.unit
    @pytest.mark.p0
    def test_degraded_still_requires_confirmation(self):
        """降级模式下危险模式仍需二次确认"""
        gw = PermissionGateway(policy_path="/nonexistent/path.json")
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check(
            "shell_execute", {"cmd": "rm -rf my_folder"}, ctx
        )
        assert result.allowed is True
        assert result.requires_confirmation is True


# ════════════════════════════════════════════════════════════
# 统一拒绝原因(不向 LLM 暴露规则细节)
# ════════════════════════════════════════════════════════════

class TestUnifiedReason:
    """RBAC/ABAC 拒绝时统一返回 reason="权限不足"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rbac_does_not_leak_role_name(self):
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("shell_execute", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"
        assert "guest" not in result.reason.lower()

    @pytest.mark.unit
    @pytest.mark.p0
    def test_rbac_does_not_leak_tool_name(self):
        gw = _make_gateway(DEFAULT_POLICY)
        ctx = ABACContext(role=Role.GUEST, session_source="cli")
        result = gw.check("shell_execute", {}, ctx)
        assert "shell_execute" not in result.reason

    @pytest.mark.unit
    @pytest.mark.p0
    def test_abac_does_not_leak_rule_name(self):
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "secret-rule-name",
                "tool": "shell_execute",
                "deny_if": {"session_source_in": ["cli"]},
            }],
        }
        gw = _make_gateway(policy)
        ctx = ABACContext(role=Role.ADMIN, session_source="cli")
        result = gw.check("shell_execute", {}, ctx)
        assert result.allowed is False
        assert result.reason == "权限不足"
        assert "secret" not in result.reason.lower()

    @pytest.mark.unit
    @pytest.mark.p1
    def test_abac_does_not_leak_time_window(self):
        policy = {
            **DEFAULT_POLICY,
            "abac_rules": [{
                "name": "off-hours",
                "tool": "shell_execute",
                "deny_if": {"time_outside": ["09:00", "18:00"]},
            }],
        }
        gw = _make_gateway(policy)
        with patch.object(
            PermissionGateway, "_time_in_window", return_value=False
        ):
            ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
            result = gw.check("shell_execute", {}, ctx)
        assert result.reason == "权限不足"
        assert "09:00" not in result.reason
        assert "18:00" not in result.reason


# ════════════════════════════════════════════════════════════
# JSON trace 日志格式
# ════════════════════════════════════════════════════════════

class TestJSONLogFormat:
    """trace 日志为标准 JSON 结构(ELK/Splunk 友好)"""

    def _capture_logs(self, func):
        """捕获 PermissionGateway 输出的 JSON 日志行

        PermissionSystem.__init__ 会输出非 JSON 中文日志(既有行为,不改动),
        只收集以 '{' 开头的行 —— 即 PermissionGateway 的 JSON trace 日志。
        """
        captured = []
        handler = logging.Handler()
        handler.emit = lambda record: captured.append(record.getMessage())
        logger = logging.getLogger("agent.permission_system")
        old_level = logger.level
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            func()
        finally:
            logger.removeHandler(handler)
            logger.setLevel(old_level)
        return [l for l in captured if l.lstrip().startswith("{")]

    @pytest.mark.unit
    @pytest.mark.p0
    def test_all_logs_are_valid_json(self):
        """所有日志行都能 json.loads 解析"""
        def run():
            gw = _make_gateway(DEFAULT_POLICY)
            ctx = ABACContext(role=Role.GUEST, session_source="cli")
            gw.check("shell_execute", {"cmd": "ls"}, ctx)
            gw.check("web_search", {"q": "hello"}, ctx)

        lines = self._capture_logs(run)
        assert len(lines) > 0
        for line in lines:
            record = json.loads(line)  # 解析失败会抛异常
            assert "ts" in record
            assert "event" in record
            assert "module" in record

    @pytest.mark.unit
    @pytest.mark.p0
    def test_decision_log_has_trace_and_layer(self):
        """出口决策日志包含 trace_id / layer / tool / allowed / duration_ms"""
        seen = {}

        def run():
            gw = _make_gateway(DEFAULT_POLICY)
            ctx = ABACContext(role=Role.GUEST, session_source="cli")
            gw.check("shell_execute", {"cmd": "ls"}, ctx)

        for line in self._capture_logs(run):
            record = json.loads(line)
            if record["event"] == "decision":
                seen = record

        assert seen, "应存在 decision 事件"
        assert seen["trace_id"]
        assert seen["layer"] == "RBAC"
        assert seen["tool"] == "shell_execute"
        assert seen["allowed"] is False
        assert "duration_ms" in seen

    @pytest.mark.unit
    @pytest.mark.p1
    def test_same_trace_id_throughout_check(self):
        """一次 check 的所有日志共享同一 trace_id"""
        trace_ids = set()

        def run():
            gw = _make_gateway(DEFAULT_POLICY)
            ctx = ABACContext(role=Role.GUEST, session_source="cli")
            gw.check("shell_execute", {"cmd": "ls"}, ctx)

        for line in self._capture_logs(run):
            record = json.loads(line)
            if "trace_id" in record:
                trace_ids.add(record["trace_id"])

        assert len(trace_ids) == 1, f"trace_id 应唯一, got {trace_ids}"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_params_snapshot_truncated(self):
        """超长参数值被截断到 50 字符"""
        long_val = "x" * 200

        def run():
            gw = _make_gateway(DEFAULT_POLICY)
            ctx = ABACContext(role=Role.DEVELOPER, session_source="cli")
            gw.check("shell_execute", {"cmd": long_val}, ctx)

        entry = None
        for line in self._capture_logs(run):
            record = json.loads(line)
            if record["event"] == "check_entry":
                entry = record

        assert entry is not None
        # 截断后 = 50 字符前缀 + "...(truncated)" 标记
        assert "...(truncated)" in entry["params"]["cmd"]
        assert len(entry["params"]["cmd"]) < 70, "参数应被截断"


# ════════════════════════════════════════════════════════════
# 数据类与枚举基础
# ════════════════════════════════════════════════════════════

class TestDataClasses:
    """Role / Permission / ABACContext 数据类基础测试"""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_role_enum_values(self):
        assert Role.ADMIN.value == "admin"
        assert Role.DEVELOPER.value == "developer"
        assert Role.GUEST.value == "guest"

    @pytest.mark.unit
    @pytest.mark.p1
    def test_permission_dataclass_defaults(self):
        p = Permission(tool_name="web_search")
        assert p.allowed is True
        assert p.requires_confirmation is False
        assert p.description == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_abac_context_defaults(self):
        ctx = ABACContext()
        assert ctx.role == Role.GUEST
        assert ctx.session_source == "cli"
        assert ctx.time_window is None
        assert ctx.ip is None


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
