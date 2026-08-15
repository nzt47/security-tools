#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wait_index_retry.py 单元测试：index 干净判据 + 等待超时 + cherry-pick 自动重试。

覆盖目标：
  - index_is_clean 必须用 `git diff --cached --quiet HEAD`（rc=0 干净），防止回归到
    `git diff-index HEAD`（工作区 vs HEAD，并行会话未暂存改动会误判为占用导致轮询空转）
  - wait_index_clean 超时返回 False / 恢复干净返回 True
  - cherry_pick_with_retry 失败重试成功后清理 sequencer / 重试耗尽返回 False

Why: 2026-08-15 并行会话共享 index 阻塞入库事故的经验固化，守护轮询脚本不被回归破坏。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import MagicMock, patch

import pytest

# 让测试能 import scripts/dev/wait_index_retry.py（非包模块）
SCRIPTS_DEV_DIR = Path(__file__).resolve().parents[2] / "scripts" / "dev"
if str(SCRIPTS_DEV_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DEV_DIR))

import wait_index_retry as wtr  # noqa: E402


# ── index_is_clean：正确判据 ─────────────────────────────────────────
class TestIndexIsClean:
    @pytest.mark.unit
    def test_uses_cached_quiet_correct_command(self):
        """必须调用 git diff --cached --quiet HEAD，禁止 diff-index HEAD。"""
        with patch.object(wtr, "_git", return_value=CompletedProcess([], 0, "", "")) as m:
            result = wtr.index_is_clean("/repo")
        assert result is True
        args = m.call_args.args
        assert args[1:4] == ("diff", "--cached", "--quiet")
        assert "HEAD" in args

    @pytest.mark.unit
    def test_rc0_means_clean(self):
        with patch.object(wtr, "_git", return_value=CompletedProcess([], 0, "", "")):
            assert wtr.index_is_clean("/repo") is True

    @pytest.mark.unit
    def test_rc1_means_occupied(self):
        with patch.object(wtr, "_git", return_value=CompletedProcess([], 1, "", "")):
            assert wtr.index_is_clean("/repo") is False


# ── wait_index_clean：等待与超时 ─────────────────────────────────────
class TestWaitIndexClean:
    @pytest.mark.unit
    def test_timeout_returns_false_when_never_clean(self):
        """index 一直不干净时，超时返回 False 且持续轮询。"""
        calls = {"n": 0}

        def always_occupied(repo):
            calls["n"] += 1
            return False

        with patch.object(wtr, "index_is_clean", side_effect=always_occupied), \
             patch.object(wtr.time, "sleep") as m_sleep:
            result = wtr.wait_index_clean("/repo", timeout=0.2, poll=0.05)

        assert result is False
        assert calls["n"] >= 2  # 至少轮询 2 次才超时
        assert m_sleep.call_count >= 1

    @pytest.mark.unit
    def test_recovers_when_index_clean_becomes_true(self):
        """前 2 次被占用、第 3 次干净 → 返回 True，停止轮询。"""
        seq = iter([False, False, True])

        def eventually_clean(repo):
            return next(seq)

        with patch.object(wtr, "index_is_clean", side_effect=eventually_clean), \
             patch.object(wtr.time, "sleep"):
            result = wtr.wait_index_clean("/repo", timeout=10.0, poll=0.05)

        assert result is True

    @pytest.mark.unit
    def test_immediately_clean_no_sleep(self):
        """入口 index 已干净时不等待直接返回 True。"""
        with patch.object(wtr, "index_is_clean", return_value=True), \
             patch.object(wtr.time, "sleep") as m_sleep:
            result = wtr.wait_index_clean("/repo", timeout=10.0, poll=0.05)

        assert result is True
        m_sleep.assert_not_called()


# ── cherry_pick_with_retry：自动重试 ─────────────────────────────────
class TestCherryPickWithRetry:
    def _completed(self, rc: int, out: str = "") -> CompletedProcess:
        return CompletedProcess([], rc, out, "")

    @pytest.mark.unit
    def test_success_on_first_try(self):
        with patch.object(wtr, "_cherry_pick") as m_cp, \
             patch.object(wtr, "_cleanup_sequencer") as m_clean:
            ok = wtr.cherry_pick_with_retry("/repo", ["abc1234"], max_retries=3)

        assert ok is True
        m_cp.assert_called_once_with("/repo", "abc1234")
        m_clean.assert_not_called()

    @pytest.mark.unit
    def test_retry_after_failure_then_success(self):
        """首次失败（清理 sequencer + 退避重试），第二次成功。"""
        fail_once = MagicMock(
            side_effect=[RuntimeError("cherry-pick abc1234 失败"), None]
        )
        with patch.object(wtr, "_cherry_pick", fail_once), \
             patch.object(wtr, "_cleanup_sequencer") as m_clean, \
             patch.object(wtr.time, "sleep"):
            ok = wtr.cherry_pick_with_retry("/repo", ["abc1234"], max_retries=3)

        assert ok is True
        assert fail_once.call_count == 2
        m_clean.assert_called_once_with("/repo")  # 失败后清理 sequencer

    @pytest.mark.unit
    def test_retries_exhausted_returns_false(self):
        """持续失败且超过 max_retries → 返回 False，不再重试。"""
        always_fail = MagicMock(side_effect=RuntimeError("持续失败"))
        with patch.object(wtr, "_cherry_pick", always_fail), \
             patch.object(wtr, "_cleanup_sequencer"), \
             patch.object(wtr.time, "sleep"):
            ok = wtr.cherry_pick_with_retry("/repo", ["abc1234"], max_retries=2)

        assert ok is False
        # 第 1 次调用 + 2 次重试
        assert always_fail.call_count == 3

    @pytest.mark.unit
    def test_multiple_commits_stops_at_failure(self):
        """多 commit 时，第一个失败重试耗尽即整体失败，不继续后续 commit。"""
        always_fail = MagicMock(side_effect=RuntimeError("失败"))
        with patch.object(wtr, "_cherry_pick", always_fail), \
             patch.object(wtr, "_cleanup_sequencer"), \
             patch.object(wtr.time, "sleep"):
            ok = wtr.cherry_pick_with_retry("/repo", ["abc1234", "def5678"], max_retries=1)

        assert ok is False
        # 只尝试了第一个 commit（1 次 + 1 次重试），第二个从未尝试
        assert always_fail.call_count == 2
        for call in always_fail.call_args_list:
            assert call.args[1] == "abc1234"
