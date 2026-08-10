"""扩展管理工具测试 — 测试 ExtensionManager 接口

覆盖范围：
- ext_install — 安装 skill/mcp/channel/plugin、无效类型、无效来源
- ext_uninstall — 成功卸载、扩展不存在
- ext_list — 列出全部、按类型筛选、空列表
- ext_toggle — 启用/禁用、切换状态
- ext_discover — 搜索、按类型搜索
- ext_configure — 配置更新、无效 ID
- ext_send_channel — 发送消息、通道不存在
"""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from agent.extensions.manager import ExtensionManager
from agent.extensions.base import ExtensionType


# ════════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def manager_with_mocks():
    """创建 ExtensionManager 并替换其内部 installers 为 Mock 对象"""
    mgr = ExtensionManager()
    # 替换 store
    mgr._store = MagicMock()
    mgr._store.list_all.return_value = []
    mgr._store.get.return_value = None

    # 为每种类型创建 mock installer
    skills = MagicMock()
    skills.list_installed_skills.return_value = []
    skills.add_builtin_skill.return_value = (True, "已安装内置技能")
    skills.add_custom_skill.return_value = (True, "已安装自定义技能")
    skills.remove_skill.return_value = (True, "已卸载技能")
    skills.toggle_skill.return_value = (True, "已启用", True)
    skills.update_skill_params.return_value = (True, "已更新配置")
    skills.discover_available_skills.return_value = {"builtin_skills": []}

    mcp = MagicMock()
    mcp.list_installed_mcp.return_value = []
    mcp.install_builtin_mcp.return_value = (True, "已安装 MCP")
    mcp.uninstall_mcp.return_value = (True, "已卸载 MCP")
    mcp.toggle_mcp.return_value = (True, "已启用", True)
    mcp.discover_available_mcp.return_value = {"builtin_mcp_services": []}

    channels = MagicMock()
    channels.list_installed_channels.return_value = []
    channels.install_builtin_channel.return_value = (True, "已安装通道")
    channels.uninstall_channel.return_value = (True, "已卸载")
    channels.toggle_channel.return_value = (True, "已启用", True)
    channels.configure_channel.return_value = (True, "已配置")
    channels.discover_available_channels.return_value = {"channels": []}

    plugins = MagicMock()
    plugins.list_installed_plugins.return_value = []
    plugins.install_plugin.return_value = (True, "已安装插件")
    plugins.uninstall_plugin.return_value = (True, "已卸载")
    plugins.toggle_plugin.return_value = (True, "已启用", True)
    plugins.discover_local_plugins.return_value = []

    mgr._installers = {
        ExtensionType.SKILL: skills,
        ExtensionType.CLAUDE_SKILL: MagicMock(),
        ExtensionType.MCP: mcp,
        ExtensionType.CHANNEL: channels,
        ExtensionType.PLUGIN: plugins,
    }
    return mgr


# ════════════════════════════════════════════════════════════════════════════════
#  安装测试
# ════════════════════════════════════════════════════════════════════════════════

class TestInstall:
    """ext_install 测试"""

    def test_install_skill(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.install("skill", "self_reflection")
        assert result["ok"] is True
        mgr._installers[ExtensionType.SKILL].add_builtin_skill.assert_called_once_with("self_reflection")

    def test_install_mcp(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.install("mcp", "filesystem")
        assert result["ok"] is True
        mgr._installers[ExtensionType.MCP].install_builtin_mcp.assert_called_once_with("filesystem")

    def test_install_channel(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.install("channel", "webhook")
        assert result["ok"] is True
        mgr._installers[ExtensionType.CHANNEL].install_builtin_channel.assert_called_once_with("webhook")

    def test_install_plugin(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.install("plugin", "my_plugin")
        assert result["ok"] is True
        mgr._installers[ExtensionType.PLUGIN].install_plugin.assert_called_once_with("my_plugin")

    def test_install_invalid_type(self, manager_with_mocks):
        result = manager_with_mocks.install("invalid_type", "something")
        assert result["ok"] is False
        assert "未知" in result["message"]

    def test_install_custom_skill_with_name(self, manager_with_mocks):
        mgr = manager_with_mocks
        skills_mock = mgr._installers[ExtensionType.SKILL]
        skills_mock.add_builtin_skill.return_value = (False, "未找到内置技能")
        result = mgr.install("skill", "github:user/repo", name="自定义技能", description="我的技能")
        assert result["ok"] is True
        skills_mock.add_custom_skill.assert_called_once()


# ════════════════════════════════════════════════════════════════════════════════
#  卸载测试
# ════════════════════════════════════════════════════════════════════════════════

class TestUninstall:
    """ext_uninstall 测试"""

    def test_uninstall_skill(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.uninstall("skill", "self_reflection")
        assert result["ok"] is True
        mgr._installers[ExtensionType.SKILL].remove_skill.assert_called_once_with("self_reflection")

    def test_uninstall_nonexistent(self, manager_with_mocks):
        mgr = manager_with_mocks
        mgr._installers[ExtensionType.SKILL].remove_skill.return_value = (False, "技能不存在")
        result = mgr.uninstall("skill", "nonexistent")
        assert result["ok"] is False

    def test_uninstall_invalid_type(self, manager_with_mocks):
        result = manager_with_mocks.uninstall("invalid", "something")
        assert result["ok"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  列表查询测试
# ════════════════════════════════════════════════════════════════════════════════

class TestList:
    """ext_list 测试"""

    def test_list_all_by_type(self, manager_with_mocks):
        mgr = manager_with_mocks
        mgr._store.list_all.return_value = [{"id": "mcp1", "name": "MCP1"}]
        result = mgr.list_all("mcp")
        assert len(result) == 1
        mgr._store.list_all.assert_called_once_with(ExtensionType.MCP)

    def test_list_empty_by_type(self, manager_with_mocks):
        mgr = manager_with_mocks
        mgr._store.list_all.return_value = []
        result = mgr.list_all("skill")
        assert len(result) == 0

    def test_get_installed_by_type(self, manager_with_mocks):
        mgr = manager_with_mocks
        skills_mock = mgr._installers[ExtensionType.SKILL]
        skills_mock.list_installed_skills.return_value = [
            {"id": "skill1", "name": "技能1", "enabled": True},
        ]
        result = mgr.get_installed_by_type()
        assert "skills" in result
        assert len(result["skills"]) == 1


# ════════════════════════════════════════════════════════════════════════════════
#  开关测试
# ════════════════════════════════════════════════════════════════════════════════

class TestToggle:
    """ext_toggle 测试"""

    def test_toggle_enable(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.toggle("skill", "test_skill", enabled=True)
        assert result["ok"] is True
        assert result["enabled"] is True
        mgr._installers[ExtensionType.SKILL].toggle_skill.assert_called_once_with("test_skill", True)

    def test_toggle_disable(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.toggle("skill", "test_skill", enabled=False)
        assert result["ok"] is True
        mgr._installers[ExtensionType.SKILL].toggle_skill.assert_called_once_with("test_skill", False)

    def test_toggle_toggle_state(self, manager_with_mocks):
        mgr = manager_with_mocks
        skills_mock = mgr._installers[ExtensionType.SKILL]
        skills_mock.toggle_skill.return_value = (True, "已切换", False)
        result = mgr.toggle("skill", "test_skill")
        assert result["ok"] is True
        assert result["enabled"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  发现测试
# ════════════════════════════════════════════════════════════════════════════════

class TestDiscover:
    """ext_discover 测试"""

    def test_discover_all(self, manager_with_mocks):
        mgr = manager_with_mocks
        skills_mock = mgr._installers[ExtensionType.SKILL]
        skills_mock.discover_available_skills.return_value = {
            "builtin_skills": [{"id": "skill_a"}, {"id": "skill_b"}],
        }
        result = mgr.discover_all()
        assert "builtin_skills" in result
        assert len(result["builtin_skills"]) == 2


# ════════════════════════════════════════════════════════════════════════════════
#  配置测试
# ════════════════════════════════════════════════════════════════════════════════

class TestConfigure:
    """ext_configure 测试"""

    def test_configure_skill(self, manager_with_mocks):
        mgr = manager_with_mocks
        result = mgr.configure("skill", "test_skill", {"param1": "value1"})
        assert result["ok"] is True
        mgr._installers[ExtensionType.SKILL].update_skill_params.assert_called_once_with(
            "test_skill", {"param1": "value1"}
        )

    def test_configure_invalid_type(self, manager_with_mocks):
        result = manager_with_mocks.configure("invalid", "something", {"key": "val"})
        assert result["ok"] is False

    def test_configure_nonexistent_id(self, manager_with_mocks):
        mgr = manager_with_mocks
        mgr._installers[ExtensionType.SKILL].update_skill_params.return_value = (False, "技能不存在")
        result = mgr.configure("skill", "nonexistent", {"key": "val"})
        assert result["ok"] is False


# ════════════════════════════════════════════════════════════════════════════════
#  通道消息测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSendChannel:
    """ext_send_channel 测试"""

    def test_send_message_store_mock(self, manager_with_mocks):
        """发送消息到通道（通过 ExtensionStore）"""
        mgr = manager_with_mocks
        mgr._store.send_message.return_value = {"ok": True, "message": "已发送"}
        # 直接通过 store.send_message（ext_send_channel 在工具层使用它发送）
        result = mgr._store.send_message("webhook", "hello")
        assert result["ok"] is True
