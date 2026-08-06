"""预检 runner 整体回归 — 复用 agent.preflight.runner 单事实源

Why（不易）：原 scripts/demo_memory_optimized_import.py 的 12 条路径检查已迁移至
agent/preflight/runner.py。本文件通过 run_preflight() 复用同一实现做整体断言
（取代 demo 脚本，消除 demo/pytest 两套断言），并验证 CLI 退出码契约：
0=全过 / 1=故障演练。分支级深度验证见 test_memory_optimized_import.py（14 用例）。
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from agent.preflight import CheckResult, run_preflight


def test_run_preflight_all_paths_pass():
    """12 条路径全部通过（demo 逻辑的 pytest 化回归）。"""
    results = run_preflight()
    assert len(results) == 12, f"路径数变化：{len(results)}"
    failed = [r for r in results if not r.ok]
    assert failed == [], f"失败路径: {[str(r) for r in failed]}"


def test_run_preflight_never_raises_and_returns_checkresults():
    """run_preflight 绝不抛异常，且返回结构化 CheckResult。"""
    results = run_preflight()
    assert all(isinstance(r, CheckResult) for r in results)
    assert all(1 <= r.index <= 12 for r in results)


def test_run_preflight_restores_probe_cache():
    """预检结束后模块级缓存必须复位（不污染同进程其他代码）。"""
    from agent import memory_optimized as mo

    run_preflight()
    assert mo._CHROMADB_IMPORT_OK is None


_FAKE_FAIL_ENV = "PREFLIGHT_FAKE_FAIL"


def _run_cli(env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    # 显式剔除故障演练开关：避免宿留环境变量污染子进程（本地调试设过后忘清会假失败）
    env = {k: v for k, v in os.environ.items() if k != _FAKE_FAIL_ENV}
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-m", "agent.preflight"],
        capture_output=True,
        text=True,
        env=env,
        timeout=90,
    )


def test_cli_exit_zero_on_success():
    """CLI 全过 → exit 0，输出包含编号与通过标记。"""
    proc = _run_cli()
    assert proc.returncode == 0, f"stderr={proc.stderr}"
    assert "全部验证通过" in proc.stdout
    assert "[1]" in proc.stdout and "[12]" in proc.stdout


def test_cli_fake_fail_env_exit_one():
    """PREFLIGHT_FAKE_FAIL 非空 → 立即以 exit 1 结束（CI 阻断演练开关）。"""
    proc = _run_cli({_FAKE_FAIL_ENV: "1"})
    assert proc.returncode == 1
    assert "故障演练" in proc.stderr
    # 故障注入应在任何路径检查前生效
    assert "全部验证通过" not in proc.stdout


def test_cli_fake_fail_env_empty_means_normal():
    """PREFLIGHT_FAKE_FAIL 置空（非空才触发）→ 正常执行。"""
    proc = _run_cli({_FAKE_FAIL_ENV: ""})
    assert proc.returncode == 0
    assert "全部验证通过" in proc.stdout


def test_main_function_direct_success():
    """直接调用 main()（不走 subprocess，供覆盖率统计）。"""
    from agent.preflight.__main__ import main

    assert main([]) == 0


def test_main_function_direct_fake_fail(monkeypatch):
    """直接调用 main() + 故障演练 env → exit 1。"""
    from agent.preflight.__main__ import main

    monkeypatch.setenv(_FAKE_FAIL_ENV, "1")
    assert main([]) == 1


def test_main_verbose_branch(capsys):
    """--verbose 触发 logging.basicConfig(INFO)（__main__ 41 行）。"""
    from agent.preflight.__main__ import main

    assert main(["--verbose"]) == 0
    captured = capsys.readouterr()
    assert "全部验证通过" in captured.out


def test_main_failure_output_branch():
    """预检含失败项 → exit 1 并输出失败明细（__main__ 49-52 行）。"""
    from unittest import mock

    from agent.preflight.__main__ import main
    from agent.preflight.runner import CheckResult

    fake_results = [
        CheckResult(index=1, name="通过路径", ok=True),
        CheckResult(index=2, name="失败路径", ok=False, detail="模拟断言失败"),
    ]
    with mock.patch(
        "agent.preflight.__main__.run_preflight", return_value=fake_results
    ):
        assert main([]) == 1


def test_main_module_entrypoint():
    """python -m 直接入口（__main__ 59 行 SystemExit 契约）。"""
    import runpy
    import sys
    from unittest import mock

    with mock.patch.object(sys, "argv", ["agent.preflight"]):
        with pytest.raises(SystemExit) as excinfo:
            runpy.run_module("agent.preflight", run_name="__main__")
    assert excinfo.value.code == 0


def test_run_preflight_handles_assertion_failure():
    """run_preflight 捕获断言失败 → CheckResult(ok=False)（runner 298-299 行）。"""
    from unittest import mock

    from agent.preflight import runner as preflight_runner

    def _boom():
        raise AssertionError("模拟断言失败")

    with mock.patch.object(preflight_runner, "CASES", [(1, "失败路径", _boom)]):
        results = run_preflight()
    assert len(results) == 1
    assert results[0].ok is False
    assert "模拟断言失败" in results[0].detail


def test_run_preflight_handles_unexpected_exception():
    """run_preflight 防御捕获意外异常 → 转失败且不中断（runner 300-303 行）。"""
    from unittest import mock

    from agent.preflight import runner as preflight_runner

    def _boom():
        raise RuntimeError("模拟意外异常")

    with mock.patch.object(preflight_runner, "CASES", [(1, "失败路径", _boom)]):
        results = run_preflight()
    assert len(results) == 1
    assert results[0].ok is False
    assert "RuntimeError: 模拟意外异常" in results[0].detail
