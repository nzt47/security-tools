"""
M1 里程碑验收测试 —— 管理后台密码治理（Gitleaks 硬编码扫描归零前置）

覆盖 load_admin_credentials（agent/server_auth.py）的三种行为：
  1. 生产环境（YUNSHU_ENV=production）未设置 YUNSHU_ADMIN_PASSWORD → 拒绝启动（RuntimeError）
  2. 本地联调（development/未设置 YUNSHU_ENV）无密码 → 默认 admin/admin123（行为不变）
  3. 显式注入密码 → 任何环境均生效（生产优先注入）
另含源码级回归防线：app_server.py 不应再含硬编码默认密码赋值模式。
"""
import re
from pathlib import Path

import pytest

from agent.server_auth import load_admin_credentials

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_production_without_password_raises(monkeypatch):
    """M1 核心：生产环境缺密码必须拒绝启动，禁止静默使用默认弱口令"""
    monkeypatch.delenv("YUNSHU_ADMIN_PASSWORD", raising=False)
    monkeypatch.setenv("YUNSHU_ENV", "production")
    with pytest.raises(RuntimeError, match="YUNSHU_ADMIN_PASSWORD"):
        load_admin_credentials()


def test_production_with_password_ok(monkeypatch):
    """生产环境显式注入密码 → 正常返回，不再触发拒绝"""
    monkeypatch.setenv("YUNSHU_ENV", "production")
    monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "Str0ng-P@ss-2026")
    username, password = load_admin_credentials()
    assert username == "admin"
    assert password == "Str0ng-P@ss-2026"


def test_development_default_password(monkeypatch):
    """本地联调（无 YUNSHU_ENV）：无密码时保留默认 admin/admin123，行为不变"""
    monkeypatch.delenv("YUNSHU_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("YUNSHU_ENV", raising=False)
    assert load_admin_credentials() == ("admin", "admin123")


def test_env_injected_password_any_env(monkeypatch):
    """显式注入密码在任何环境都优先于默认值"""
    monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "Env-Only-Pass")
    monkeypatch.delenv("YUNSHU_ENV", raising=False)
    assert load_admin_credentials()[1] == "Env-Only-Pass"


def test_custom_username(monkeypatch):
    """用户名支持环境变量定制"""
    monkeypatch.setenv("YUNSHU_ADMIN_USERNAME", "root")
    monkeypatch.setenv("YUNSHU_ADMIN_PASSWORD", "x")
    assert load_admin_credentials()[0] == "root"


def test_no_hardcoded_default_password_in_app_server():
    """源码级回归防线：app_server.py 不应再含 os.environ.get(..., 'admin123') 硬编码默认密码。

    若未来有人回退 M1 改动，此用例立即失败，保证 Gitleaks 命中不回归。
    """
    src = (REPO_ROOT / "app_server.py").read_text(encoding="utf-8")
    pattern = r'os\.environ\.get\(\s*["\']YUNSHU_ADMIN_PASSWORD["\']\s*,\s*["\']admin123["\']\s*\)'
    assert not re.search(pattern, src), "app_server.py 仍存在硬编码默认密码赋值，M1 未完成"
