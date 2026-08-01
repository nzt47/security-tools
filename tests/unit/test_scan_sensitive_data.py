#!/usr/bin/env python3
"""scan_sensitive_data.py 单元测试

覆盖 2026-08-01 修复的 PASSWORD 正则误报（变量名匹配语法被误判），
确保修复不被改回 buggy 版本。

测试维度:
  - 真实硬编码密码仍被检测（不漏检）
  - PowerShell 变量名匹配语法不再误报（误报消除）
  - is_code_context 函数契约
  - 白名单值/路径/敏感文件名检测不受影响

运行:
    pytest tests/unit/test_scan_sensitive_data.py -v
"""
import sys
from pathlib import Path

# 将 scripts/ 加入 import 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))

import scan_sensitive_data as ssd


def _scan_text(content: str, tmp_path: Path, filename: str = 'sample.ps1'):
    """辅助：将内容写入临时文件并扫描

    【不易】filename 必须避开白名单前缀（test_/test.），否则触发
    WHITELIST_FILENAME_PREFIXES 导致扫描被跳过，真实密码检测测试失效
    """
    f = tmp_path / filename
    f.write_text(content, encoding='utf-8')
    return ssd.scan_file(f)


# ─── is_code_context 函数契约 ──────────────────────────────────

class TestIsCodeContext:
    """验证代码上下文检测函数"""

    def test_含_cmdlet_判定为代码上下文(self):
        assert ssd.is_code_context('PASSWORD=" } | Select-Object -First 1)') is True

    def test_含_replace_运算符判定为代码上下文(self):
        assert ssd.is_code_context('PASSWORD=" -replace "') is True

    def test_含_match_运算符判定为代码上下文(self):
        assert ssd.is_code_context('PASSWORD="^VAR=" -match "') is True

    def test_含_dollar_underscore_判定为代码上下文(self):
        assert ssd.is_code_context('PASSWORD="$_"') is True

    def test_纯密码值_非代码上下文(self):
        assert ssd.is_code_context('password="actual_secret_123"') is False

    def test_空字符串_非代码上下文(self):
        assert ssd.is_code_context('') is False


# ─── scan_file 误报消除（核心 Bug 回归守卫）────────────────────

class TestPasswordFalsePositiveFix:
    """Bug 回归：PowerShell 变量名匹配语法不再误报为硬编码密码"""

    def test_glitchtip变量名匹配语法不误报(self, tmp_path):
        # 误报根因：$_ -match "^GLITCHTIP_ADMIN_PASSWORD=" 中 PASSWORD=" 被误判
        content = (
            '$glitchtipPwd = ($envContent | Where-Object '
            '{ $_ -match "^GLITCHTIP_ADMIN_PASSWORD=" } '
            '| Select-Object -First 1) -replace "^GLITCHTIP_ADMIN_PASSWORD=", ""'
        )
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 0, '变量名匹配语法不应被误报为硬编码密码'

    def test_grafana变量名匹配语法不误报(self, tmp_path):
        content = (
            '$grafanaPwd = ($envContent | Where-Object '
            '{ $_ -match "^GRAFANA_ADMIN_PASSWORD=" } '
            '| Select-Object -First 1) -replace "^GRAFANA_ADMIN_PASSWORD=", ""'
        )
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 0


# ─── 真实密码不漏检（安全护城河）──────────────────────────────

class TestRealPasswordStillDetected:
    """修复不能引入漏检：真实硬编码密码必须仍被检测"""

    def test_行首password硬编码被检测(self, tmp_path):
        content = 'password = "actual_secret_123"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 1

    def test_缩进password硬编码被检测(self, tmp_path):
        content = '    password = "actual_secret_123"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 1

    def test_大写PASSWORD等号无空格被检测(self, tmp_path):
        content = 'PASSWORD="Hardcoded_Pwd_998"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 1

    def test_变量名后缀admin_password硬编码被检测(self, tmp_path):
        # 关键：(?<![\w]) 方案会漏检此场景，代码特征过滤方案不会
        content = 'admin_password = "actual_secret_123"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 1, 'admin_password 硬编码必须被检测（不能漏检）'

    def test_passwd硬编码被检测(self, tmp_path):
        content = 'passwd = "actual_secret_123"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        assert len(password_findings) == 1


# ─── 其他类别不受影响 ──────────────────────────────────────────

class TestOtherCategoriesUnaffected:
    """代码特征过滤仅对 PASSWORD 生效，其他类别不受影响"""

    def test_API_key仍被检测(self, tmp_path):
        # 【不易】测试值必须避开 WHITELIST_VALUES（sk-abcdefghijklmnopqrstuvwxyz 等已被白名单）
        content = 'api_key = "sk-ThisIsARealApiKey1234567890XY"'
        findings = _scan_text(content, tmp_path)
        api_findings = [f for f in findings if f[1] == 'API_KEY']
        assert len(api_findings) == 1

    def test_数据库连接串仍被检测(self, tmp_path):
        content = 'url = "postgres://user:passwordlong@host:5432/db"'
        findings = _scan_text(content, tmp_path)
        db_findings = [f for f in findings if f[1] == 'DB_URL']
        assert len(db_findings) == 1


# ─── 白名单与敏感文件名 ────────────────────────────────────────

class TestWhitelistAndSensitiveFile:
    """白名单值/路径/敏感文件名检测保持原有行为"""

    def test_白名单值不报(self, tmp_path):
        # admin123 在 WHITELIST_VALUES 中
        content = 'password = "admin12345678"'
        findings = _scan_text(content, tmp_path)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        # admin12345678 含 admin123 → 白名单 → 不报
        assert len(password_findings) == 0

    def test_tests目录路径白名单不报(self, tmp_path):
        # tests/ 在 WHITELIST_PATHS 中
        f = tmp_path / 'test_secret.ps1'
        # 放在 tests/ 模拟路径（通过文件名前缀 test_ 也白名单）
        f.write_text('password = "actual_secret_123"', encoding='utf-8')
        findings = ssd.scan_file(f)
        password_findings = [f for f in findings if f[1] == 'PASSWORD']
        # test_ 前缀白名单 → 不报
        assert len(password_findings) == 0

    def test_敏感文件名被检测(self, tmp_path):
        f = tmp_path / '.secure_config.json'
        f.write_text('{}', encoding='utf-8')
        findings = ssd.scan_file(f)
        # .secure_config.json 在 SENSITIVE_FILES 中 → 文件名级拦截
        sensitive_file_findings = [f for f in findings if f[1] == 'SENSITIVE_FILE']
        assert len(sensitive_file_findings) == 1


# ─── 真实文件回归守卫 ──────────────────────────────────────────

class TestRealFileRegression:
    """对真实 verify_monitoring_setup.ps1 的回归守卫"""

    def test_真实verify_monitoring文件无PASSWORD误报(self):
        # 真实文件路径（相对项目根）
        f = PROJECT_ROOT / 'scripts' / 'verify_monitoring_setup.ps1'
        if not f.exists():
            import pytest
            pytest.skip('verify_monitoring_setup.ps1 不存在')
        findings = ssd.scan_file(f)
        password_findings = [fd for fd in findings if fd[1] == 'PASSWORD']
        assert len(password_findings) == 0, (
            f'verify_monitoring_setup.ps1 不应有 PASSWORD 误报，'
            f'实际发现: {password_findings}'
        )
