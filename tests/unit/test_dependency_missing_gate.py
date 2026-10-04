"""依赖锁定文件「缺件」门禁守卫（2026-10-04 · H-8 的机制化补丁）。

【解决什么】`scripts/audit_dependency_drift.py` 早已能算出
`ONLY_IN_PYPROJECT`（[2b]：pyproject 声明了，锁定文件里没有），
但 CI 那一步只跑 `--fail-on-conflict`，而它**只判 CONFLICT**。
于是这一类**可以全绿地合入**。

【这不是假想 —— 实测复现过一次】
2026-10-04：在 `python:3.12-slim` 容器里按文件头写的规范命令
`pip-compile --output-file=requirements.txt pyproject.toml` 重新生成锁文件，
结果 pyproject 里 4 个带 `sys_platform == 'win32'` 标记的直接依赖
（comtypes / pypiwin32 / pywin32 / wmi）**全部消失** ——
pip-compile 按**当前平台**求值环境标记，在 Linux 上就把它们解析掉了。

用那份锁替换后实测：
    [2b] 仅 pyproject 有 = 4
    audit_dependency_drift.py --fail-on-conflict   -> exit 0   ← 漏报
    audit_dependency_drift.py --fail-on-missing    -> exit 1   ← 本提交补的开关

而**生产运行环境是 Windows**（本机 `pip list` 实测 4 个包都在装），
丢掉它们意味着 WMI 感知能力在生产上消失 —— 而 CI 全绿。
本仓对这类门禁的定性：**会漏报的门禁比会误报的更危险，因为它看起来很绿**。

【本文件守什么】
  ① 开关确实存在且**有分辨力**（构造"缺件"的合成输入 ⇒ 非零退出；
     同一输入补齐后 ⇒ 零退出）。合成夹具走 `--pyproject` / `--requirements`
     命令行参数，不依赖仓库当前的实际锁文件内容 —— 即**不把今天的数值写成契约**
     （这条纪律是本会话踩过坑之后立的：见 test_contract_diff_matcher 里
     那条把 callability.ts 的 stray 数写死的用例）。
  ② CI 那一步**确实带上了**该开关（否则开关写了也没人跑）。
  ③ 真实锁文件当前**不缺件**（[2b] == 0），保证②不会让 CI 变红。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_dependency_drift.py"


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT),
    )


@pytest.fixture()
def synth(tmp_path):
    """合成一对声明/锁定文件：让用例只依赖"机制"，不依赖仓库当前内容。"""
    def _make(*, win_dep_in_lock: bool):
        py = tmp_path / "pyproject.toml"
        py.write_text(
            "[project]\n"
            'name = "synth"\n'
            'version = "0.1.0"\n'
            'requires-python = ">=3.11"\n'
            "dependencies = [\n"
            '  "requests>=2.0.0",\n'
            "  \"wmi>=1.5.0,<1.6.0; sys_platform == 'win32'\",\n"
            "]\n",
            encoding="utf-8",
        )
        req = tmp_path / "requirements.txt"
        lines = ["requests==2.32.3", "    # via synth (pyproject.toml)"]
        if win_dep_in_lock:
            lines += ["wmi==1.5.1; sys_platform == 'win32'",
                      "    # via synth (pyproject.toml)"]
        req.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return py, req
    return _make


class Test开关存在且有分辨力:
    def test_缺件时非零退出(self, synth):
        py, req = synth(win_dep_in_lock=False)
        cp = _run("--pyproject", str(py), "--requirements", str(req), "--fail-on-missing")
        assert cp.returncode != 0, (
            "pyproject 声明了 wmi 而锁定文件里没有，--fail-on-missing 却没有拦住 —— "
            "门禁会漏报（这正是 H-8 实测复现过的形态）"
        )

    def test_补齐后零退出(self, synth):
        """反证：同一条命令、同一份声明，只把缺的那个包补进锁文件 ⇒ 放行。

        【为什么必须有这条】没有它，一个"永远非零退出"的坏开关也会让上一条通过。
        """
        py, req = synth(win_dep_in_lock=True)
        cp = _run("--pyproject", str(py), "--requirements", str(req), "--fail-on-missing")
        assert cp.returncode == 0, cp.stdout[-600:] + cp.stderr[-400:]

    def test_不带开关时不拦(self, synth):
        """保持既有语义：不带 --fail-on-missing 时不因缺件而失败（避免影响其它调用点）。"""
        py, req = synth(win_dep_in_lock=False)
        cp = _run("--pyproject", str(py), "--requirements", str(req))
        assert cp.returncode == 0


class TestCI_确实带上了开关:
    def test_dependency_consistency_job_含_fail_on_missing(self):
        src = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "audit_dependency_drift.py --fail-on-conflict --fail-on-missing" in src, (
            "ci.yml 的声明层审计没有带上 --fail-on-missing —— "
            "开关写了却没人跑，等于补了个摆设"
        )


class Test真实锁文件当前不缺件:
    def test_2b_为零(self):
        """保证上一条不会把 CI 直接变红（真锁确实完整）。"""
        cp = _run("--json")
        assert cp.returncode == 0
        import json

        payload = json.loads(cp.stdout)
        assert payload["counts"]["ONLY_IN_PYPROJECT"] == 0, (
            "真实锁文件缺件了："
            + str(payload["counts"]["ONLY_IN_PYPROJECT"])
            + " 个（见报告 [2b]）"
        )

    def test_四个_win32_直接依赖都在锁里(self):
        """本仓生产环境是 Windows：这 4 个带 win32 标记的直接依赖必须留在锁里。

        【锚在"这 4 个名字"而不是"标记行数"】名字来自 pyproject 的显式声明；
        标记行数会随平台/工具版本变化，是一个会漂的量。
        """
        req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        missing = [p for p in ("comtypes", "pypiwin32", "pywin32", "wmi")
                   if not any(line.lower().startswith(p + "==")
                              for line in req.splitlines())]
        assert not missing, (
            "requirements.txt 缺少 Windows 专属直接依赖：" + str(missing)
            + " —— 生产（Windows）会丢 WMI 感知能力。"
            "注意：在 Linux 容器里 pip-compile 会把带 sys_platform=='win32' 的依赖解析掉。"
        )
