"""pre-commit hook 拦截回归测试 — 模拟含失效锚点/失效链接的提交

目的：验证「文档链接预检 + 锚点链接回归测试」在坏文档提交时能否正确拦截。
被测入口统一为 scripts/dev/git_precommit_check.ps1 —— 它同时被
  1. 本地 pre-commit hook 调用（经 sync_precommit_hook.ps1 部署）
  2. CI docs-precheck-tests job 调用（.github/workflows/ci.yml）
因此「入口返回非零」即代表「CI 会拦截、hook 会阻止提交」，两者行为一致。

覆盖场景：
  A. 失效链接（无锚点，目标文件不存在）→ 入口返回 1（拦截）
  B. 失效锚点（带 #锚点，目标文件不存在）→ 入口返回 1（拦截）
  C. 混合场景（好链接 + 坏链接）→ 入口返回 1（拦截）
  D. 健康文档（链接全部有效）→ 入口返回 0（放行）
  E. E2E：临时 git 仓库部署 hook 后，git commit 含坏文档 → 提交被拦截

依赖与平台：
  - Windows（PowerShell 5.1 + Git for Windows）；非 Windows 直接跳过
  - CI ubuntu unit-tests job 只扫 tests/unit/，不会收集本文件；
    windows-latest 的 docs-precheck-tests job 显式运行本文件
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECK_SCRIPT = REPO_ROOT / "scripts" / "dev" / "git_precommit_check.ps1"
# 复用本仓库已部署的 hook 内容（marker 含 source_repo，运行时经
# TLM_HOOK_SOURCE_REPO 环境变量重定向，可复制到任意仓库）
LOCAL_HOOK = REPO_ROOT / ".git" / "hooks" / "pre-commit"

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="依赖 PowerShell + Git for Windows，仅 Windows 可运行",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_check(repo_root: Path, timeout: int = 180) -> subprocess.CompletedProcess:
    """调用 CI / hook 共用的检查入口 git_precommit_check.ps1。

    埋点（仅失败时输出）：记录耗时 + returncode（含十六进制，Windows 崩溃码
    0xC0000005/0xC0000409 可区分「被杀/崩溃」与「正常业务失败」）+ 输出尾部，
    用于定位完整套件下的资源竞争偶发失败。
    """
    cmd = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(CHECK_SCRIPT),
        "-TargetRepo",
        str(repo_root),
    ]
    t0 = time.monotonic()
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    dt = time.monotonic() - t0
    if p.returncode != 0:
        print(f"[diag-hook] returncode={p.returncode} "
              f"(0x{p.returncode & 0xFFFFFFFF:08X}) elapsed={dt:.2f}s "
              f"stdout_tail={p.stdout[-500:]!r} stderr_tail={p.stderr[-500:]!r}",
              file=sys.stderr, flush=True)
    return p


def _run_git(repo_root: Path, *args: str, timeout: int = 180) -> subprocess.CompletedProcess:
    """在临时仓库执行 git 命令；始终注入 TLM_HOOK_SOURCE_REPO 供 hook 寻址。

    【不易】SKIP_WORKFLOW_SIM=1：跳过 hook 的「工作流模拟校验」段。
    本测试意图验证 hook 对失效链接的拦截行为，不验证 ci-failure-notify
    通知链路（那由 simulate_ci_failure_notify.py 的独立测试覆盖）。
    simulate_ci_failure_notify.py 被 git 跟踪，CI checkout 后存在于 runner，
    若不跳过会因 Linux 环境差异误失败（输出被 hook >/dev/null 吞掉，难排查）。
    """
    env = {**os.environ, "TLM_HOOK_SOURCE_REPO": str(REPO_ROOT), "SKIP_WORKFLOW_SIM": "1"}
    t0 = time.monotonic()
    p = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=timeout,
    )
    dt = time.monotonic() - t0
    if p.returncode != 0:
        print(f"[diag-git] {args[0]} returncode={p.returncode} "
              f"(0x{p.returncode & 0xFFFFFFFF:08X}) elapsed={dt:.2f}s "
              f"stdout_tail={p.stdout[-500:]!r} stderr_tail={p.stderr[-500:]!r}",
              file=sys.stderr, flush=True)
    return p


def _setup_git_repo(tmp_path: Path) -> Path:
    """git init 临时仓库（master 默认分支 + 免签名配置），返回仓库根。"""
    init = _run_git(tmp_path, "init", "-b", "master")
    assert init.returncode == 0, init.stdout + init.stderr
    # 仅本仓库生效的配置，避免污染全局 gitconfig
    for key, value in [
        ("user.name", "hook-test"),
        ("user.email", "hook-test@example.com"),
        ("commit.gpgsign", "false"),
    ]:
        _run_git(tmp_path, "config", key, value)
    return tmp_path


def _install_local_hook(repo_root: Path) -> None:
    """把本仓库部署的 pre-commit hook 复制到临时仓库（模拟 sync 部署结果）。

    【变易】hook 内容优先取本仓库已部署的 .git/hooks/pre-commit（本地已 sync）；
    缺失时（CI 全新 checkout，.git/hooks 不随 git 跟踪）回退用跟踪的
    hook_fail_safe.psm1 模块生成同一内容，保证 E2E 拦截测试不依赖本地部署状态。
    【不易】本地 hook 即使存在，也须校验为 TLM 体系（含 TLM_HOOK_SOURCE_REPO
    marker）；旧版 pre-commit 框架 hook（报 "No .pre-commit-config.yaml file
    was found"，2026-08-14 A-2 实测）同样回退 psm1 生成，测试不依赖本地部署新旧。
    """
    hook_content = None
    if LOCAL_HOOK.exists():
        hook_content = LOCAL_HOOK.read_bytes()
        if b"TLM_HOOK_SOURCE_REPO" not in hook_content:
            print("[diag-hook] 本地 hook 缺 TLM marker，回退 psm1 生成",
                  file=sys.stderr, flush=True)
            hook_content = None
    if hook_content is None:
        module = REPO_ROOT / "scripts" / "dev" / "hook_fail_safe.psm1"
        hooks_dir = repo_root / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        # 用 PowerShell 调用模块直接写文件，避免 stdout 中文编码不一致
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                f"Import-Module '{module}'; "
                f"$c = Get-HookContent -SourceRepo '{REPO_ROOT}'; "
                f"Write-HookNoBom -Path '{hooks_dir / 'pre-commit'}' -Content $c",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return
    hooks_dir = repo_root / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / "pre-commit").write_bytes(hook_content)


# ────────────────────────────────────────────────────────────
# A/B/C/D：检查入口对坏/好文档的拦截行为
# ────────────────────────────────────────────────────────────

def test_broken_link_blocks(tmp_path):
    """失效链接（目标文件不存在）→ 入口返回 1 并输出 [BROKEN]。"""
    _write(tmp_path / "docs" / "good.md", "# 健康文档\n")
    _write(tmp_path / "docs" / "bad.md", "[缺失目标](./ghost.md)\n")
    result = _run_check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BROKEN]" in result.stdout
    assert "[BLOCK]" in result.stdout


def test_broken_anchor_link_blocks(tmp_path):
    """失效锚点（带 #锚点 且目标文件不存在）→ 入口返回 1。"""
    _write(tmp_path / "docs" / "runbook.md", "[缺失锚点](./ghost.md#四、告警规则)\n")
    result = _run_check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BROKEN]" in result.stdout
    assert "[BLOCK]" in result.stdout


def test_mixed_good_and_broken_blocks(tmp_path):
    """混合场景（1 个有效 + 1 个失效）→ 入口返回 1（存在即拦截）。"""
    _write(tmp_path / "docs" / "target.md", "# 目标\n")
    _write(
        tmp_path / "docs" / "runbook.md",
        "[有效](./target.md)\n[失效](./ghost.md)\n",
    )
    result = _run_check(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BROKEN]" in result.stdout


def test_healthy_docs_passes(tmp_path):
    """健康文档（有效链接 + 纯锚点 + 外链）→ 入口返回 0，不误报。"""
    _write(tmp_path / "docs" / "target.md", "# 目标\n")
    _write(
        tmp_path / "docs" / "runbook.md",
        "[有效](./target.md)\n[本地锚点](#目标)\n[外链](https://example.com/x)\n",
    )
    result = _run_check(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


# ────────────────────────────────────────────────────────────
# E：E2E —— 真实 git commit 被 hook 拦截
# ────────────────────────────────────────────────────────────

def test_real_git_commit_blocked_by_hook(tmp_path):
    """部署 hook 后，git commit 含失效链接的文档 → 提交被拦截。"""
    repo = _setup_git_repo(tmp_path)
    _install_local_hook(repo)

    # 先提交一个健康基线（hook 放行，验证 hook 本身可用）
    _write(repo / "docs" / "good.md", "# 健康文档\n")
    add = _run_git(repo, "add", "docs/good.md")
    assert add.returncode == 0, add.stdout + add.stderr
    good = _run_git(repo, "commit", "-m", "healthy baseline")
    assert good.returncode == 0, good.stdout + good.stderr
    # hook 输出经 git 透传，可能落在 stderr（Git for Windows 行为），两个流都查
    assert "预检通过" in good.stdout + good.stderr

    # 再提交含失效链接的文档 → hook 必须拦截
    _write(repo / "docs" / "bad.md", "[缺失目标](./ghost.md)\n")
    add = _run_git(repo, "add", "docs/bad.md")
    assert add.returncode == 0, add.stdout + add.stderr
    bad = _run_git(repo, "commit", "-m", "bad commit")

    assert bad.returncode != 0, "坏文档提交应被 hook 拦截，但提交成功"
    output = bad.stdout + bad.stderr
    assert "预检失败" in output or "[BLOCK]" in output, output
    # 提交确实未生成：日志应只有基线提交，不含 bad commit
    log = _run_git(repo, "log", "--oneline")
    assert "bad commit" not in log.stdout, f"坏提交不应出现在日志:\n{log.stdout}"
    assert "healthy baseline" in log.stdout, log.stdout
