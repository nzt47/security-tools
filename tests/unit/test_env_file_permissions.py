"""P1 安全加固测试：.env 文件权限自动化设置

测试覆盖：
1. Unix 平台：os.chmod 设置 0o600
2. Windows 平台：icacls 限制 ACL
3. 失败降级：subprocess.run 失败时不抛异常，仅 warning
4. 文件不存在时跳过
5. set() / 原子写入后权限被显式重设
6. _ensure_file_exists 创建新文件时设置权限

设计原则（三义）:
- 【不易】.env 权限保护是核心安全边界，必须 100% 覆盖
- 【变易】跨平台分支独立测试
- 【简易】使用 mock 隔离，不依赖真实文件系统权限
"""
import os
import stat
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ─────────────────────────────────────────────────
# 测试夹具
# ─────────────────────────────────────────────────

@pytest.fixture
def temp_env_file(tmp_path):
    """临时 .env 文件路径（不存在，由 EnvConfigManager 创建）"""
    return tmp_path / ".env"


@pytest.fixture
def env_manager(temp_env_file):
    """独立 EnvConfigManager 实例（不使用全局单例）"""
    from agent.env_config_manager import EnvConfigManager
    return EnvConfigManager(env_file_path=str(temp_env_file))


# ─────────────────────────────────────────────────
# 1. Unix 平台权限设置
# ─────────────────────────────────────────────────

class TestUnixFilePermissions:
    """Unix 平台：os.chmod 设置 0o600"""

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason="Unix 权限测试仅在非 Windows 平台运行")
    def test_chmod_600_applied_on_init(self, temp_env_file):
        """Unix: 文件创建后权限应为 0o600"""
        from agent.env_config_manager import EnvConfigManager
        EnvConfigManager(env_file_path=str(temp_env_file))

        file_stat = os.stat(str(temp_env_file))
        actual_mode = stat.S_IMODE(file_stat.st_mode)
        assert actual_mode == 0o600, f"权限应为 0o600，实际为 {oct(actual_mode)}"

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason="Unix 权限测试仅在非 Windows 平台运行")
    def test_chmod_600_applied_after_set(self, env_manager, temp_env_file):
        """Unix: set() 写入后权限应重设为 0o600"""
        # 故意改为 0o644 模拟临时文件权限
        os.chmod(str(temp_env_file), 0o644)

        env_manager.set('TEST_KEY', 'test_value')

        file_stat = os.stat(str(temp_env_file))
        actual_mode = stat.S_IMODE(file_stat.st_mode)
        assert actual_mode == 0o600, f"set() 后权限应重设为 0o600，实际为 {oct(actual_mode)}"

    def test_unix_chmod_called_with_correct_mode(self, temp_env_file):
        """模拟 Unix 平台：验证 os.chmod 被调用且参数正确"""
        from agent.env_config_manager import EnvConfigManager

        # 临时创建文件，让 _secure_file_permissions 能进入权限设置分支
        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'linux'), \
             patch('agent.env_config_manager.os.chmod') as mock_chmod:
            manager = EnvConfigManager(env_file_path=str(temp_env_file))
            # 验证 chmod 至少被调用一次（init 时）
            assert mock_chmod.called, "Unix 平台应调用 os.chmod"
            # 验证最后一次调用参数为 0o600（owner 读写）
            args, kwargs = mock_chmod.call_args
            assert args[1] == stat.S_IRUSR | stat.S_IWUSR, \
                f"应使用 0o600，实际参数: {oct(args[1])}"


# ─────────────────────────────────────────────────
# 2. Windows 平台权限设置
# ─────────────────────────────────────────────────

class TestWindowsFilePermissions:
    """Windows 平台：icacls 限制 ACL"""

    def test_icacls_called_on_init(self, temp_env_file):
        """Windows: 初始化时调用 icacls"""
        from agent.env_config_manager import EnvConfigManager

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            EnvConfigManager(env_file_path=str(temp_env_file))

            assert mock_run.called, "Windows 平台应调用 icacls"
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == 'icacls', f"应调用 icacls，实际: {cmd[0]}"
            assert '/inheritance:r' in cmd, "应移除继承权限"
            assert any('/grant:r' in arg for arg in cmd), "应授权当前用户"
            assert 'SYSTEM:F' in cmd, "应授权 SYSTEM"

    def test_icacls_includes_username(self, temp_env_file):
        """Windows: icacls 命令应包含当前用户名"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.os.environ',
                   {**os.environ, 'USERNAME': 'test_user_123'}), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            EnvConfigManager(env_file_path=str(temp_env_file))

            cmd = mock_run.call_args[0][0]
            assert 'test_user_123:F' in cmd, \
                f"icacls 应授权当前用户 test_user_123，实际命令: {cmd}"

    def test_icacls_called_after_set(self, env_manager, temp_env_file):
        """Windows: set() 后再次调用 icacls"""
        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            env_manager.set('TEST_KEY', 'test_value')

            # set() 内部会调用 _atomic_write，进而调用 _secure_file_permissions
            assert mock_run.called, "set() 后应调用 icacls 重设权限"

    def test_icacls_uses_create_no_window(self, temp_env_file):
        """Windows: 应使用 CREATE_NO_WINDOW 避免弹出控制台窗口"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            EnvConfigManager(env_file_path=str(temp_env_file))

            kwargs = mock_run.call_args[1]
            assert 'creationflags' in kwargs, "应指定 creationflags"
            # CREATE_NO_WINDOW = 0x08000000
            assert kwargs['creationflags'] & 0x08000000, \
                "应使用 CREATE_NO_WINDOW 标志"


# ─────────────────────────────────────────────────
# 3. 失败降级（不抛异常）
# ─────────────────────────────────────────────────

class TestFailureDegradation:
    """权限设置失败时应降级为 warning，不破坏主流程"""

    def test_icacls_failure_does_not_raise(self, temp_env_file):
        """Windows: icacls 返回非零时不抛异常"""
        from agent.env_config_manager import EnvConfigManager

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr='Access denied', stdout=''
            )
            # 不应抛异常
            manager = EnvConfigManager(env_file_path=str(temp_env_file))

            # 文件仍应被创建
            assert temp_env_file.exists(), "权限失败时文件仍应创建"

    def test_chmod_failure_does_not_raise(self, temp_env_file):
        """Unix: os.chmod 抛异常时不影响主流程"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'linux'), \
             patch('agent.env_config_manager.os.chmod',
                   side_effect=PermissionError("chmod failed")):
            # 不应抛异常
            manager = EnvConfigManager(env_file_path=str(temp_env_file))

            # 文件仍存在
            assert temp_env_file.exists()

    def test_subprocess_timeout_does_not_raise(self, temp_env_file):
        """Windows: icacls 超时不抛异常"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run',
                   side_effect=subprocess.TimeoutExpired(cmd='icacls', timeout=5)):
            # 不应抛异常
            manager = EnvConfigManager(env_file_path=str(temp_env_file))

    def test_set_still_succeeds_when_permission_fails(self, env_manager, temp_env_file):
        """权限失败时 set() 仍应完成写入"""
        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run',
                   side_effect=PermissionError("blocked")):
            # set() 应成功完成（写入 .env + os.environ）
            env_manager.set('CRITICAL_KEY', 'critical_value')

            assert os.getenv('CRITICAL_KEY') == 'critical_value'
            content = temp_env_file.read_text(encoding='utf-8')
            assert 'CRITICAL_KEY=critical_value' in content


# ─────────────────────────────────────────────────
# 4. 文件不存在场景
# ─────────────────────────────────────────────────

class TestMissingFileHandling:
    """文件不存在时 _secure_file_permissions 应跳过"""

    def test_secure_permissions_skips_missing_file(self):
        """_secure_file_permissions 对不存在文件直接返回"""
        from agent.env_config_manager import EnvConfigManager

        manager = EnvConfigManager(env_file_path='non_existent_path.env')
        # 删除创建的文件
        Path('non_existent_path.env').unlink(missing_ok=True)

        with patch('agent.env_config_manager.os.chmod') as mock_chmod, \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            manager._secure_file_permissions()
            # 文件不存在时应直接返回，不调用 chmod / icacls
            mock_chmod.assert_not_called()
            mock_run.assert_not_called()


# ─────────────────────────────────────────────────
# 5. 端到端：完整流程权限验证
# ─────────────────────────────────────────────────

class TestEndToEndPermissionFlow:
    """端到端：创建 → 写入 → 删除流程中权限始终为 600"""

    @pytest.mark.skipif(sys.platform == 'win32',
                        reason="Unix 权限测试仅在非 Windows 平台运行")
    def test_permission_maintained_throughout_lifecycle(self, env_manager, temp_env_file):
        """Unix: 完整生命周期内权限保持 0o600"""
        # 创建后
        mode1 = stat.S_IMODE(os.stat(str(temp_env_file)).st_mode)
        assert mode1 == 0o600

        # 写入后
        env_manager.set('KEY1', 'val1')
        mode2 = stat.S_IMODE(os.stat(str(temp_env_file)).st_mode)
        assert mode2 == 0o600

        # 多次写入后
        env_manager.set('KEY2', 'val2')
        env_manager.set('KEY1', 'updated')
        mode3 = stat.S_IMODE(os.stat(str(temp_env_file)).st_mode)
        assert mode3 == 0o600

        # 删除后
        env_manager.delete('KEY1')
        mode4 = stat.S_IMODE(os.stat(str(temp_env_file)).st_mode)
        assert mode4 == 0o600

    def test_windows_full_lifecycle_calls_icacls(self, env_manager, temp_env_file):
        """Windows: 完整生命周期内 icacls 被多次调用"""
        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')

            env_manager.set('KEY1', 'val1')
            env_manager.set('KEY2', 'val2')
            env_manager.delete('KEY1')

            # 至少 3 次 icacls 调用（每次写入/删除后）
            assert mock_run.call_count >= 3, \
                f"应至少调用 3 次 icacls，实际: {mock_run.call_count}"


# ─────────────────────────────────────────────────
# 6. 跨平台命令构造验证
# ─────────────────────────────────────────────────

class TestCommandConstruction:
    """icacls 命令构造正确性"""

    def test_icacls_command_structure(self, temp_env_file):
        """icacls 命令应包含必需参数：路径 /inheritance:r /grant:r"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            EnvConfigManager(env_file_path=str(temp_env_file))

            cmd = mock_run.call_args[0][0]
            # 命令结构: ['icacls', '<path>', '/inheritance:r', '/grant:r', '<user>:F', '/grant:r', 'SYSTEM:F']
            assert cmd[0] == 'icacls'
            assert cmd[1] == str(temp_env_file)
            assert '/inheritance:r' in cmd
            # 至少有 2 个 /grant:r（用户 + SYSTEM）
            grant_count = sum(1 for arg in cmd if arg == '/grant:r')
            assert grant_count >= 2, f"应至少 2 个 /grant:r，实际: {grant_count}"
            assert 'SYSTEM:F' in cmd

    def test_icacls_has_timeout(self, temp_env_file):
        """icacls 应有超时设置避免卡死"""
        from agent.env_config_manager import EnvConfigManager

        temp_env_file.touch()

        with patch('agent.env_config_manager.sys.platform', 'win32'), \
             patch('agent.env_config_manager.subprocess.run') as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr='', stdout='')
            EnvConfigManager(env_file_path=str(temp_env_file))

            kwargs = mock_run.call_args[1]
            assert 'timeout' in kwargs, "应指定 timeout"
            assert kwargs['timeout'] <= 10, "超时不应过长（避免卡死）"
