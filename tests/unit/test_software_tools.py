"""受控软件安装通道（software_probe / software_install）的单元测试。

覆盖设计文档（docs/proposals/软件安装通道_2026-11-05.md）里的四道闸：
白名单精确匹配 / 显式 confirm / 退出码 / 事后探测，外加"没有安装器"与超时。

【不易】不碰真机注册表、不跑真 winget：
    · _iter_installed 用替身喂"卸载登记表"条目；
    · software_tools.subprocess 换成只带 run / TimeoutExpired 的命名空间
      （不改全局 subprocess，避免影响同进程其它用例）；
    · 审计写入用替身收集，保证用例不落真实审计库。
"""
from __future__ import annotations

import subprocess
import types

from agent.tools import software_tools as sw


# ────────────────────────────────────────────────────────────
#  替身
# ────────────────────────────────────────────────────────────

def _fake_registry(items):
    """把 _iter_installed 换成产出 items 的生成器函数。"""
    def _gen():
        for it in items:
            yield dict(it)
    return _gen


def _patch_env(monkeypatch, *, installed, which="winget", run=None, allowlist=None):
    """统一装配：注册表替身 + which 替身 + subprocess 替身 + 白名单替身。"""
    monkeypatch.setattr(sw, "_iter_installed", _fake_registry(installed), raising=True)
    monkeypatch.setattr(sw.shutil, "which", lambda name: which, raising=True)
    monkeypatch.setattr(sw, "_load_allowlist", lambda: allowlist or {}, raising=True)
    if run is not None:
        monkeypatch.setattr(
            sw, "subprocess",
            types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired),
            raising=True,
        )
    captured = {}

    def _audit(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
    monkeypatch.setattr(sw, "_audit_install", _audit, raising=True)
    return captured


_ENTRY = {"id": "Git.Git", "installer": "winget", "probe": ["Git"]}
_ALLOW = {"git.git": dict(_ENTRY)}


def _ok_run(*args, **kwargs):
    return types.SimpleNamespace(returncode=0, stdout="installed", stderr="")


def _boom_run(*args, **kwargs):  # pragma: no cover - 被调用即失败
    raise AssertionError("不应该启动安装器")


# ────────────────────────────────────────────────────────────
#  software_probe（只读）
# ────────────────────────────────────────────────────────────

class TestProbe:

    def test_精确匹配_大小写不敏感(self, monkeypatch):
        _patch_env(monkeypatch, installed=[
            {"name": "Git version 2.44", "version": "2.44",
             "path": r"C:\Program Files\Git", "source": "registry:HKLM"},
        ])
        out = sw.software_probe("git version 2.44")
        assert out["ok"] is True and out["installed"] is True
        assert out["version"] == "2.44"
        assert out["match"] == "exact"

    def test_精确优先于包含(self, monkeypatch):
        _patch_env(monkeypatch, installed=[
            {"name": "GitHub Desktop", "version": "3.4", "path": "", "source": "registry:HKLM"},
            {"name": "Git", "version": "2.44", "path": "", "source": "registry:HKLM"},
        ])
        out = sw.software_probe("Git")
        assert out["matched_display_name"] == "Git"
        assert out["version"] == "2.44"

    def test_未安装(self, monkeypatch):
        _patch_env(monkeypatch, installed=[
            {"name": "Python 3.12", "version": "3.12.0", "path": "", "source": "registry:HKLM"},
        ])
        out = sw.software_probe("Git")
        assert out["ok"] is True and out["installed"] is False

    def test_探测异常如实回失败(self, monkeypatch):
        def _boom():
            raise RuntimeError("registry locked")
        monkeypatch.setattr(sw, "_iter_installed", _boom, raising=True)
        out = sw.software_probe("Git")
        assert out["ok"] is False and out["installed"] is False
        assert out["reason"] == "probe-failed:RuntimeError"


# ────────────────────────────────────────────────────────────
#  software_install：四道闸
# ────────────────────────────────────────────────────────────

class TestInstallGates:

    def test_白名单为空_拒绝且不启动安装器(self, monkeypatch):
        _patch_env(monkeypatch, installed=[], allowlist={}, run=_boom_run)
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is False
        assert out["denied_reason"] == "not-in-allowlist"
        assert out["allowlist_size"] == 0

    def test_id_必须精确等值_不做模糊匹配(self, monkeypatch):
        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW, run=_boom_run)
        out = sw.software_install("Git", confirm=True)      # 少了 .Git
        assert out["denied_reason"] == "not-in-allowlist"

    def test_缺_confirm_只回计划(self, monkeypatch):
        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW, run=_boom_run)
        out = sw.software_install("Git.Git", confirm=False)
        assert out["ok"] is False
        assert out["denied_reason"] == "confirm-required"
        assert out["plan"]["package_id"] == "Git.Git"
        assert "--exact" in out["plan"]["command"]
        assert "winget" in out["plan"]["command"]

    def test_没有安装器_如实回失败(self, monkeypatch):
        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW, which=None, run=_boom_run)
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is False
        assert out["denied_reason"] == "installer-not-found"


class TestInstallOutcomes:

    def test_成功_事后探测通过(self, monkeypatch):
        state = {"installed": []}

        def _run(*args, **kwargs):
            state["installed"] = [
                {"name": "Git", "version": "2.44",
                 "path": r"C:\Program Files\Git", "source": "registry:HKLM"},
            ]
            return types.SimpleNamespace(returncode=0, stdout="done", stderr="")

        def _gen():
            for it in state["installed"]:
                yield dict(it)

        monkeypatch.setattr(sw, "_iter_installed", _gen, raising=True)
        monkeypatch.setattr(sw.shutil, "which", lambda name: "winget", raising=True)
        monkeypatch.setattr(sw, "_load_allowlist", lambda: _ALLOW, raising=True)
        monkeypatch.setattr(sw, "subprocess",
                            types.SimpleNamespace(run=_run,
                                                  TimeoutExpired=subprocess.TimeoutExpired),
                            raising=True)
        captured = {}
        monkeypatch.setattr(sw, "_audit_install",
                            lambda *a, **k: captured.update(k), raising=True)

        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is True, out
        assert out["exit_code"] == 0
        assert out["installed_before"] is False and out["installed_after"] is True
        assert out["version_after"] == "2.44"
        assert out["changed"] is True and out["verified"] is True
        # 审计必须带上"是否验证通过"，否则事后无从追溯
        assert captured["verified"] is True
        assert captured["installer"] == "winget"

    def test_退出码非零_判失败(self, monkeypatch):
        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW,
                   run=lambda *a, **kw: types.SimpleNamespace(returncode=1, stdout="",
                                                             stderr="boom"))
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is False
        assert out["exit_code"] == 1
        assert out["denied_reason"] == "install-failed"

    def test_退出码_0_但事后探测不到_仍判失败(self, monkeypatch):
        """这是旧空壳的病根：只能信"装完还查得到"，不能信退出码。"""
        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW, run=_ok_run)
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is False
        assert out["installed_after"] is False
        assert out["denied_reason"] == "not-installed-after"

    def test_超时(self, monkeypatch):
        def _slow(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="winget", timeout=sw._INSTALL_TIMEOUT)

        _patch_env(monkeypatch, installed=[], allowlist=_ALLOW, run=_slow)
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is False
        assert out["denied_reason"] == "timeout"

    def test_已安装且版本未变_不记为变更(self, monkeypatch):
        _patch_env(monkeypatch, installed=[
            {"name": "Git", "version": "2.44", "path": "", "source": "registry:HKLM"},
        ], allowlist=_ALLOW, run=_ok_run)
        out = sw.software_install("Git.Git", confirm=True)
        assert out["ok"] is True
        assert out["installed_before"] is True
        assert out["changed"] is False


# ────────────────────────────────────────────────────────────
#  声明一致性
# ────────────────────────────────────────────────────────────

def test_安装器白名单只有常量名():
    """argv[0] 必须来自常量表（用户串不进命令），崩了才是回归。"""
    assert set(sw._INSTALLERS) == {"winget", "choco"}
    assert all(v in ("winget", "choco") for v in sw._INSTALLERS.values())


def test_超时有上界():
    assert 0 < sw._INSTALL_TIMEOUT <= 1800
