"""precheck_docs.ps1 锚点链接预检回归测试

覆盖场景：提交前预检对带 #锚点 的相对 Markdown 链接的处理——
  1. 目标文件存在 → 通过（退出码 0，不阻塞提交）
  2. 目标文件缺失 → 阻塞（退出码 1）
  3. 纯锚点 / 外部链接 → 跳过，不误报
  4. 跨目录（../）带锚点链接 → 通过

通过 -TargetRepo 参数指向临时伪仓库（tmp_path），
避免污染真实 docs/ 目录。退出码为唯一可靠的行为信号
（PS 5.x 重定向输出编码可能非 UTF-8，故断言只用 ASCII 片段）。
"""
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PRECHECK_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "dev" / "precheck_docs.ps1"
)

# 【简易】precheck_docs.ps1 是 PowerShell 脚本，仅 Windows runner 有 powershell 命令。
# Linux 上的 CI 单元测试 job 无法执行 → 平台不满足时跳过（文档链接预检已有独立
# windows-latest job 覆盖该逻辑）。
pytestmark = pytest.mark.skipif(
    not shutil.which("powershell") or sys.platform.startswith("linux"),
    reason="需要 PowerShell（仅 Windows 环境可执行 precheck_docs.ps1）",
)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _run_precheck(repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PRECHECK_SCRIPT),
            "-SkipChart",
            "-BlockMode",
            "-AllowBroken",
            "0",
            "-TargetRepo",
            str(repo_root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def test_anchor_link_to_existing_file_passes(tmp_path):
    """带中文锚点的相对链接指向存在的文件 → 预检通过（退出码 0）。"""
    _write(tmp_path / "docs" / "target.md", "# 目标文档\n\n## 四、告警规则\n")
    _write(
        tmp_path / "docs" / "runbook.md",
        "[四、告警规则](./target.md#四、告警规则)\n",
    )
    result = _run_precheck(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_anchor_link_to_missing_file_blocks(tmp_path):
    """带锚点的相对链接指向不存在的文件 → 预检阻塞（退出码 1）。"""
    _write(
        tmp_path / "docs" / "runbook.md",
        "[缺失目标](./ghost.md#四、告警规则)\n",
    )
    result = _run_precheck(tmp_path)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "[BROKEN]" in result.stdout
    assert "[BLOCK]" in result.stdout


def test_pure_anchor_and_external_links_skipped(tmp_path):
    """纯锚点（#本地）与外部链接（https://）不应被误判为失效。"""
    _write(
        tmp_path / "docs" / "runbook.md",
        "[本地锚点](#目标章节)\n[外链](https://example.com/x)\n",
    )
    result = _run_precheck(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout


def test_anchor_link_with_parent_dir_passes(tmp_path):
    """跨目录（../）带锚点链接指向存在的文件 → 预检通过。"""
    _write(tmp_path / "docs" / "target.md", "# 目标文档\n")
    _write(
        tmp_path / "docs" / "sub" / "runbook.md",
        "[目标](../target.md#目标文档)\n",
    )
    result = _run_precheck(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "[OK]" in result.stdout
