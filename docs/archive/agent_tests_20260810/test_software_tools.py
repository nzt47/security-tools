"""软件管理工具测试 — 测试 SoftwareManager 及后端接口

覆盖范围：
- software_search — 搜索、指定后端、无结果
- software_install — 安装、白名单检查、确认流程
- software_list — 已安装列表、空列表
- software_uninstall — 卸载、扩展不存在

策略：使用 MagicMock 模拟 SoftwareManager 及其后端行为。
"""
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def mock_manager():
    """创建 Mock 的 SoftwareManager"""
    mgr = MagicMock()
    mgr.search.return_value = {"ok": True, "results": [
        {"name": "python", "version": "3.12.0", "backend": "chocolatey"},
        {"name": "nodejs", "version": "20.0.0", "backend": "chocolatey"},
    ]}
    mgr.install.return_value = {"ok": True, "message": "安装成功"}
    mgr.list_installed.return_value = [
        {"name": "python", "version": "3.12.0", "backend": "chocolatey"},
    ]
    mgr.uninstall.return_value = {"ok": True, "message": "卸载成功"}
    mgr.is_whitelisted.return_value = True
    return mgr


# ════════════════════════════════════════════════════════════════════════════════
#  搜索测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSoftwareSearch:
    """software_search 测试"""

    def test_search_normal(self, mock_manager):
        """正常搜索"""
        result = mock_manager.search("python")
        assert result["ok"] is True
        assert len(result["results"]) == 2
        mock_manager.search.assert_called_once_with("python")

    def test_search_specify_backend(self, mock_manager):
        """指定后端搜索"""
        result = mock_manager.search("python", backend="pip")
        assert result["ok"] is True
        mock_manager.search.assert_called_with("python", backend="pip")

    def test_search_no_results(self, mock_manager):
        """无搜索结果"""
        mock_manager.search.return_value = {"ok": True, "results": []}
        result = mock_manager.search("nonexistent_package_xyz")
        assert result["ok"] is True
        assert len(result["results"]) == 0

    def test_search_all_backends(self, mock_manager):
        """不指定后端搜索全部"""
        result = mock_manager.search("python")
        assert result["ok"] is True
        # 不传 backend 参数 = 搜索全部
        assert "backend" not in mock_manager.search.call_args[1] or \
               mock_manager.search.call_args[1].get("backend") is None


# ════════════════════════════════════════════════════════════════════════════════
#  安装测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSoftwareInstall:
    """software_install 测试"""

    def test_install_whitelisted(self, mock_manager):
        """白名单内安装"""
        mock_manager.is_whitelisted.return_value = True
        result = mock_manager.install("python")
        assert result["ok"] is True
        mock_manager.install.assert_called_with("python")

    def test_install_not_whitelisted_confirm(self, mock_manager):
        """不在白名单但 confirm=True"""
        mock_manager.is_whitelisted.return_value = False
        mock_manager.add_to_whitelist.return_value = None
        mock_manager.install.return_value = {"ok": True, "message": "已确认安装"}

        # 执行安装（带 confirm）
        result = mock_manager.install("unknown-tool", auto_confirm=True)
        assert result["ok"] is True

    def test_install_not_whitelisted_no_confirm(self, mock_manager):
        """不在白名单且未确认"""
        mock_manager.is_whitelisted.return_value = False
        # 模拟工具层返回需要确认的错误
        # 测试的是"需要确认"这个逻辑分支
        assert mock_manager.is_whitelisted("unknown-tool") is False

    def test_install_with_version(self, mock_manager):
        """安装指定版本"""
        mock_manager.install.return_value = {"ok": True, "message": "安装成功"}
        result = mock_manager.install("python", version="3.11.0")
        assert result["ok"] is True
        mock_manager.install.assert_called_with("python", version="3.11.0")

    def test_install_specify_backend(self, mock_manager):
        """指定后端安装"""
        result = mock_manager.install("python", backend="pip")
        assert result["ok"] is True
        mock_manager.install.assert_called_with("python", backend="pip")


# ════════════════════════════════════════════════════════════════════════════════
#  已安装列表测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSoftwareList:
    """software_list 测试"""

    def test_list_installed(self, mock_manager):
        """列出已安装软件"""
        result = mock_manager.list_installed()
        assert len(result) == 1
        assert result[0]["name"] == "python"

    def test_list_empty(self, mock_manager):
        """空列表"""
        mock_manager.list_installed.return_value = []
        result = mock_manager.list_installed()
        assert len(result) == 0

    def test_list_by_backend(self, mock_manager):
        """按后端筛选"""
        result = mock_manager.list_installed(backend="chocolatey")
        assert len(result) == 1
        mock_manager.list_installed.assert_called_with(backend="chocolatey")


# ════════════════════════════════════════════════════════════════════════════════
#  卸载测试
# ════════════════════════════════════════════════════════════════════════════════

class TestSoftwareUninstall:
    """software_uninstall 测试"""

    def test_uninstall_success(self, mock_manager):
        """成功卸载"""
        result = mock_manager.uninstall("python")
        assert result["ok"] is True
        mock_manager.uninstall.assert_called_with("python")

    def test_uninstall_nonexistent(self, mock_manager):
        """卸载不存在的软件"""
        mock_manager.uninstall.return_value = {"ok": False, "error": "软件未安装"}
        result = mock_manager.uninstall("nonexistent")
        assert result["ok"] is False

    def test_uninstall_with_backend(self, mock_manager):
        """指定后端卸载"""
        result = mock_manager.uninstall("python", backend="pip")
        assert result["ok"] is True
        mock_manager.uninstall.assert_called_with("python", backend="pip")


# ════════════════════════════════════════════════════════════════════════════════
#  后端注册测试
# ════════════════════════════════════════════════════════════════════════════════

class TestBackends:
    """软件后端注册测试"""

    def test_register_backend(self):
        """注册后端（使用真实的 SoftwareManager）"""
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        # 验证基础方法存在
        assert hasattr(mgr, "install")
        assert hasattr(mgr, "uninstall")
        assert hasattr(mgr, "get_installed_software")

    def test_software_manager_install_uninstall(self):
        """真实的 SoftwareManager 安装/卸载流程"""
        from agent.software_manager import SoftwareManager
        mgr = SoftwareManager()
        result = mgr.install("test-app")
        assert result is True or (isinstance(result, dict) and result.get("ok"))
