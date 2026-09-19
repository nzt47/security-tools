#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scripts/run_full_pytest.py` 批次完整性校验的单元测试（D2 交付物 · 2026-09-19）

## 被守护的不变量

`pytest.ini` 用 `--timeout-method=thread`，超时后走
`pytest_timeout.py:505 timeout_timer()` → `finally: os._exit(1)`，**整个 pytest 进程被杀**。
后果不是"一个测试失败"，而是"同一批里排在后面的测试文件全部从未执行"，
而且**没有 pytest 结束摘要** ⇒ 只看 rc 无法区分"被强杀"与"有测试失败"。

本测试锁死"如何判定一块是否真的跑完"这一条判据。判错的代价：
丢文件被当成普通失败 ⇒ 永远不会有人去补跑那 14 个文件（TASK-03 真实事故）。
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load():
    """加载被测脚本。

    ⚠️ Why 必须显式保存并恢复 cwd：`scripts/run_full_pytest.py` 在**模块顶层**执行
    `os.chdir(ROOT)`（它本来的用法是当命令行入口）。测试若直接 import 它，
    就会把**整个 pytest 进程**的工作目录改掉，进而污染同批次其它用例
    （本仓历史上已有多起"顺序依赖 flaky"，不能再加一条）。
    """
    cwd = os.getcwd()
    try:
        spec = importlib.util.spec_from_file_location(
            "run_full_pytest_under_test", REPO_ROOT / "scripts" / "run_full_pytest.py"
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["run_full_pytest_under_test"] = mod
        spec.loader.exec_module(mod)
    finally:
        os.chdir(cwd)
    return mod


RFP = _load()


class TestChunkLogStatus:
    """判定"这块是否正常收尾"——本判据错一次就丢一批文件"""

    @pytest.mark.parametrize(
        "tail,expected",
        [
            ("===== 1 failed, 1695 passed, 7 skipped, 10 warnings in 313.71s =====", True),
            ("===== 1428 passed, 53 skipped in 194.22s =====", True),
            ("========== 3 errors in 12.00s ==========", True),
            ("no tests ran in 0.01s", True),
        ],
    )
    def test_completed_when_summary_present(self, tmp_path, tail, expected):
        log = tmp_path / "chunk.log"
        log.write_text(f"collecting ...\n....\n{tail}\n", encoding="utf-8")
        ok, _ = RFP.chunk_log_status(str(log))
        assert ok is expected

    def test_not_completed_on_thread_timeout_kill(self, tmp_path):
        """核心负例：TASK-03 事故的真实日志形状（无摘要 + Timeout 标记）。"""
        log = tmp_path / "chunk.log"
        log.write_text(
            "tests\\unit\\test_x.py .......+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~ Stack of MainThread (5712) ~~~~~~~~~~~~~~~~~~~~~~~~~\n"
            "  File \"...\", line 1, in <module>\n"
            "+++++++++++++++++++++++++++++++++++ Timeout +++++++++++++++++++++++++++++++++++\n",
            encoding="utf-8",
        )
        ok, detail = RFP.chunk_log_status(str(log))
        assert ok is False
        assert "强杀" in detail

    def test_not_completed_without_summary_and_without_timeout_marker(self, tmp_path):
        """被外部 kill / 崩溃（无 Timeout 标记也无摘要）同样判未跑完。"""
        log = tmp_path / "chunk.log"
        log.write_text("collecting ...\n", encoding="utf-8")
        ok, _ = RFP.chunk_log_status(str(log))
        assert ok is False

    def test_not_completed_when_log_missing(self, tmp_path):
        ok, detail = RFP.chunk_log_status(str(tmp_path / "nope.log"))
        assert ok is False and "不存在" in detail

    def test_empty_log_is_not_completed(self, tmp_path):
        log = tmp_path / "empty.log"
        log.write_text("", encoding="utf-8")
        ok, _ = RFP.chunk_log_status(str(log))
        assert ok is False


class TestChunkingIsLossless:
    """分块本身不得丢文件（丢文件会让完整性校验失去意义）"""

    def test_round_robin_covers_every_file_exactly_once(self):
        files = [f"tests/unit/test_{i:03d}.py" for i in range(37)]
        chunks = [files[i::5] for i in range(5)]
        chunks = [c for c in chunks if c]
        flat = sorted(f for c in chunks for f in c)
        assert flat == sorted(files)
        assert len(flat) == len(set(flat))


class TestResumeWiring:
    """补跑函数存在且可调用（真正的补跑行为由 scripts/repro_timeout_batch_loss.py 做端到端复现）"""

    def test_resume_function_exists(self):
        assert callable(RFP.resume_lost_files)

    def test_import_does_not_leak_cwd_change(self):
        """import 该脚本不得改变进程 cwd（见 _load() 的 why）。"""
        before = os.getcwd()
        _load()
        assert os.getcwd() == before, "导入 run_full_pytest.py 泄漏了 os.chdir 副作用"

    def test_ignores_list_is_unchanged_size(self):
        # 与 pytest.ini 的 --ignore 保持一致（11 条 ignore + 1 条 pytest.ini 里的 temp）
        assert len(RFP.IGNORES) == 12, (
            "IGNORES 与 pytest.ini 的 --ignore 列表必须同步；"
            f"当前 {len(RFP.IGNORES)} 条：{RFP.IGNORES}"
        )
