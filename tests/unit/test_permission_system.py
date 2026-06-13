"""
PermissionSystem 权限边界系统测试
"""
import pytest
import os
import sys
import tempfile
import json
from unittest.mock import patch, MagicMock

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.permission_system import PermissionSystem, PermissionResult


class TestPermissionSystemInitialization:
    """测试权限系统初始化"""
    
    def test_init_default(self):
        """测试默认初始化"""
        ps = PermissionSystem()
        assert ps._backup_dir is not None
        assert ps._loaded_keywords is not None
    
    def test_init_custom_backup_dir(self):
        """测试自定义备份目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ps = PermissionSystem(backup_dir=tmpdir)
            assert str(ps._backup_dir) == tmpdir
    
    def test_init_with_keywords_path(self):
        """测试使用自定义关键词路径"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"critical": [], "warning": []}, f)
            custom_path = f.name
        
        try:
            ps = PermissionSystem(keywords_path=custom_path)
            assert ps._loaded_keywords == {"critical": [], "warning": []}
        finally:
            os.unlink(custom_path)


class TestPermissionCheckAction:
    """测试权限检查操作"""
    
    def test_check_action_allowed(self):
        """测试允许的操作"""
        ps = PermissionSystem()
        result = ps.check_action("正常操作")
        
        assert result.allowed is True
        assert result.requires_confirmation is False
    
    def test_check_action_blacklist(self):
        """测试黑名单操作"""
        ps = PermissionSystem()
        result = ps.check_action("rm -rf /")
        
        assert result.allowed is False
        assert "黑名单" in result.reason
    
    def test_check_action_fork_bomb(self):
        """测试Fork炸弹"""
        ps = PermissionSystem()
        result = ps.check_action(":(){ :|:& };")
        
        assert result.allowed is True
    
    def test_check_action_dangerous_pattern(self):
        """测试危险模式操作"""
        ps = PermissionSystem()
        result = ps.check_action("rm -rf my_folder")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
        assert "需要二次确认" in result.reason
    
    def test_check_action_format(self):
        """测试格式化操作"""
        ps = PermissionSystem()
        result = ps.check_action("format D:")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
    
    def test_check_action_shutdown(self):
        """测试关机操作"""
        ps = PermissionSystem()
        result = ps.check_action("shutdown -s -t 0")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
    
    def test_check_action_sensitive_dir(self):
        """测试敏感目录操作"""
        ps = PermissionSystem()
        result = ps.check_action("操作 C:\\Windows\\system32")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
        assert "敏感路径" in result.reason
    
    def test_check_action_unix_sensitive_dir(self):
        """测试Unix敏感目录"""
        ps = PermissionSystem()
        result = ps.check_action("操作 /etc")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
    
    def test_check_action_sensitive_extension(self):
        """测试敏感文件扩展名"""
        ps = PermissionSystem()
        result = ps.check_action("修改 config.exe")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
        assert "敏感文件类型" in result.reason
    
    def test_check_action_registry_modification(self):
        """测试注册表修改"""
        ps = PermissionSystem()
        result = ps.check_action("reg delete HKLM\\Software")
        
        assert result.allowed is True
        assert result.requires_confirmation is True
    
    def test_check_action_chmod_777(self):
        """测试chmod 777"""
        ps = PermissionSystem()
        result = ps.check_action("chmod 777 /var/www")
        
        assert result.allowed is True
        assert result.requires_confirmation is True


class TestPermissionConfirmAction:
    """测试操作确认"""
    
    def test_confirm_action_success(self):
        """测试确认操作成功"""
        ps = PermissionSystem()
        
        result = ps.check_action("rm -rf my_folder")
        action_id = ps._permission_log[-1]["id"]
        
        confirm_result = ps.confirm_action(action_id)
        
        assert confirm_result is True
        assert ps._permission_log[-1]["confirmed"] is True
    
    def test_confirm_action_not_found(self):
        """测试确认不存在的操作"""
        ps = PermissionSystem()
        
        confirm_result = ps.confirm_action("perm_9999")
        
        assert confirm_result is False
    
    def test_confirm_action_already_confirmed(self):
        """测试确认已确认的操作"""
        ps = PermissionSystem()
        
        result = ps.check_action("format D:")
        action_id = ps._permission_log[-1]["id"]
        
        ps.confirm_action(action_id)
        confirm_result = ps.confirm_action(action_id)
        
        assert confirm_result is True


class TestPermissionBackupFile:
    """测试文件备份"""
    
    def test_backup_file_success(self):
        """测试备份文件成功"""
        with tempfile.TemporaryDirectory() as tmpdir:
            ps = PermissionSystem(backup_dir=os.path.join(tmpdir, "backups"))
            
            test_file = os.path.join(tmpdir, "test.txt")
            with open(test_file, 'w') as f:
                f.write("test content")
            
            backup_path = ps.backup_file(test_file)
            
            assert backup_path is not None
            assert os.path.exists(backup_path)
    
    def test_backup_file_not_exists(self):
        """测试备份不存在的文件"""
        ps = PermissionSystem()
        backup_path = ps.backup_file("/nonexistent/file.txt")
        
        assert backup_path is None
    
    def test_backup_file_failure(self):
        """测试备份失败"""
        ps = PermissionSystem()
        backup_path = ps.backup_file("/nonexistent/directory/file.txt")
        
        assert backup_path is None


class TestPermissionSensitivePath:
    """测试敏感路径检查"""
    
    def test_is_sensitive_path_windows(self):
        """测试Windows敏感路径"""
        ps = PermissionSystem()
        
        assert ps.is_sensitive_path("C:\\Windows\\system32") is True
        assert ps.is_sensitive_path("C:\\Program Files\\test") is True
        assert ps.is_sensitive_path("D:\\My Documents") is False
    
    def test_is_sensitive_path_unix(self):
        """测试Unix敏感路径"""
        ps = PermissionSystem()
        
        assert ps.is_sensitive_path("/etc/passwd") is True
        assert ps.is_sensitive_path("/usr/lib/libc.so") is True
        assert ps.is_sensitive_path("/home/user") is False


class TestPermissionCheckText:
    """测试文本检查功能（SafetyGuard整合）"""
    
    def test_check_text_empty(self):
        """测试检查空文本"""
        ps = PermissionSystem()
        result = ps.check_text("")
        
        assert result["safe"] is True
        assert result["level"] == "safe"
    
    def test_check_text_safe(self):
        """测试检查安全文本"""
        ps = PermissionSystem()
        result = ps.check_text("这是安全的文本")
        
        assert result["safe"] is True
        assert result["level"] == "safe"
    
    def test_check_text_critical(self):
        """测试检查包含危险关键词的文本"""
        ps = PermissionSystem()
        result = ps.check_text("rm -rf /")
        
        assert result["safe"] is False
        assert result["level"] == "critical"
    
    def test_check_text_warning(self):
        """测试检查包含警告关键词的文本"""
        ps = PermissionSystem()
        result = ps.check_text("执行 reboot")
        
        assert result["safe"] is False
        assert result["level"] == "warning"
    
    def test_check_text_multiple_matches(self):
        """测试检查多个匹配"""
        ps = PermissionSystem()
        result = ps.check_text("rm -rf / 和 format")
        
        assert result["safe"] is False
        assert result["level"] == "critical"
        assert len(result["matches"]) >= 1


class TestPermissionLogging:
    """测试权限日志"""
    
    def test_get_permission_log(self):
        """测试获取权限日志"""
        ps = PermissionSystem()
        
        ps.check_action("rm -rf folder1")
        ps.check_action("format D:")
        
        logs = ps.get_permission_log()
        assert len(logs) == 2
    
    def test_get_permission_log_limit(self):
        """测试获取权限日志限制"""
        ps = PermissionSystem()
        
        for i in range(10):
            ps.check_action(f"rm -rf folder{i}")
        
        logs = ps.get_permission_log(limit=5)
        assert len(logs) == 5


if __name__ == '__main__':
    pytest.main([__file__, '-v', '-s'])
