"""create_gitee_release.ps1 的 PowerShell 变量拼接回归测试

覆盖 Bug（2026-08-05 真实事故）:
    PowerShell 变量名允许包含 '?' 字符。双引号字符串 "/repos/$Owner/$Repo?access_token=$token"
    中 $Repo?access_token 被解析为单个变量名（未定义 → 空），
    URL 退化为 "/repos/nzt47/=d428..." → Gitee API 返回 404 "Not Found Project"。
    修复: 用 $($Repo) / $($token) 显式变量边界。

CI 说明: 需要 PowerShell 7+ (pwsh)，Linux/无 pwsh 环境自动跳过。
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    platform.system() != "Windows" or shutil.which("pwsh") is None,
    reason="需要 PowerShell 7+ (pwsh)",
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "create_gitee_release.ps1"

# 复现探针：同一变量拼接的旧/新写法
PROBE = r"""
$Owner = "nzt47"; $Repo = "security-tools"; $token = "dummy"
$old = "/repos/$Owner/$Repo?access_token=$token"
$new = "/repos/$Owner/$($Repo)?access_token=$($token)"
Write-Output "OLD=$old"
Write-Output "NEW=$new"
"""


def _run_pwsh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["pwsh", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )


def test_powershell_dollar_question_mark_trap():
    """复现 Bug 与验证修复：'$Repo?access_token' 被当单个变量名，URL 退化"""
    r = _run_pwsh(PROBE)
    assert r.returncode == 0, r.stderr
    out = dict(line.split("=", 1) for line in r.stdout.strip().splitlines())
    # 旧写法（修复前）→ 变量名吸收 '?access_token'，URL 退化为 /repos/nzt47/=dummy
    assert out["OLD"] == "/repos/nzt47/=dummy"
    # 新写法（修复后）→ $() 显式边界，URL 完整
    assert out["NEW"] == "/repos/nzt47/security-tools?access_token=dummy"


def test_script_loads_and_blocks_on_missing_token():
    """脚本可被 pwsh 解析（UTF-8/语法正确），无 token 时输出 BLOCK 并退出 1"""
    assert SCRIPT.exists(), f"脚本缺失: {SCRIPT}"
    r = subprocess.run(
        ["pwsh", "-NoProfile", "-File", str(SCRIPT)],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 1
    assert "GITEE_TOKEN" in r.stdout, f"应提示 GITEE_TOKEN 未设置, 实际输出: {r.stdout[-200:]}"
