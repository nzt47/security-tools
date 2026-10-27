r"""Windows 上调用 bash 脚本的适配层（agent/tools/win_bash.py）

回归对象：2026-10-28 实测的**真实缺陷** —— 吞噬生成的工具把 Windows 绝对路径交给
`bash`，而 Windows 的 bash 是 WSL，看不到 `C:\...`：

    bash "C:\\Users\\...\\scripts\\manage.sh" list
    → /bin/bash: C:UsersAdministrator...scriptsmanage.sh: No such file or directory (exit=127)

表现为"工具登记成功、一调就失败"，且报错像"脚本不存在"，最容易误判成"没装 CLI"。
本文件只测**纯函数**（不发子进程、不依赖 WSL 在跑），两条平台分支都用 monkeypatch 覆盖。
"""
from __future__ import annotations

import pytest

from agent.tools import win_bash as W


class TestToWslPath:
    @pytest.mark.parametrize("raw,want", [
        ("C:\\Users\\x\\a.sh", "/mnt/c/Users/x/a.sh"),
        ("C:/Users/x/a.sh", "/mnt/c/Users/x/a.sh"),
        ("D:\\dist", "/mnt/d/dist"),
        ("c:\\lower", "/mnt/c/lower"),
        ("C:\\", "/mnt/c"),
        ("C:", "C:"),                       # 没有分隔符 ⇒ 不是路径，不猜
        ("/mnt/c/Users/x", "/mnt/c/Users/x"),
        ("relative/a.sh", "relative/a.sh"),
        ("a.sh", "a.sh"),
        ("", ""),
        ("https://example.com/a", "https://example.com/a"),   # URL 不得被改
    ])
    def test_翻或不翻(self, raw, want):
        assert W.to_wsl_path(raw) == want

    def test_None_当空串(self):
        assert W.to_wsl_path(None) == ""   # type: ignore[arg-type]


class TestBashArgv:
    def test_非_windows_逐字返回_且是新列表(self, monkeypatch):
        monkeypatch.setattr(W, "is_windows", lambda: False)
        src = ["bash", "C:\\a\\b.sh", "list"]
        out = W.bash_argv(src)
        assert out == src
        assert out is not src, "必须返回新列表（调用方可能就地改）"
        out.append("x")
        assert src == ["bash", "C:\\a\\b.sh", "list"], "不得修改入参"

    def test_windows_翻译路径_其余原样(self, monkeypatch):
        monkeypatch.setattr(W, "is_windows", lambda: True)
        argv = W.bash_argv([
            "bash", "C:\\Users\\Administrator\\a b\\manage.sh",
            "list", "--pages", "p-123", "--domain", "www.example.com",
        ])
        assert argv[0] == "bash"
        assert argv[1] == "/mnt/c/Users/Administrator/a b/manage.sh"
        assert argv[2:] == ["list", "--pages", "p-123", "--domain", "www.example.com"], (
            "非路径参数（含域名，形似 a.b 但无盘符）必须逐字保留")

    def test_windows_相对路径不翻(self, monkeypatch):
        monkeypatch.setattr(W, "is_windows", lambda: True)
        assert W.bash_argv(["bash", "scripts/manage.sh", "list"]) == [
            "bash", "scripts/manage.sh", "list"]

    def test_空入参安全(self):
        assert W.bash_argv([]) == []
        assert W.bash_argv(None) == []   # type: ignore[arg-type]


class TestLooksLikeShell:
    @pytest.mark.parametrize("arg,want", [
        ("bash", True), ("sh", True), ("bash.exe", True),
        ("C:\\Windows\\System32\\bash.exe", True),
        ("python", False), ("", False),
    ])
    def test_判定(self, arg, want):
        assert W.looks_like_shell(arg) is want


def test_生成器必须提醒这条_否则新工具会重犯():
    """提示词里必须保留 win_bash 指引（否则下次 AI 生成又会写裸 `bash <C:\\...>`）"""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "agent" / "ingestion" / "toolbuild.py"
           ).read_text(encoding="utf-8")
    assert "agent.tools.win_bash.bash_argv" in src, (
        "toolbuild 的实现要求里丢了 win_bash 指引：Windows 上 bash 是 WSL，"
        "裸传 C:\\ 路径会 exit=127")
