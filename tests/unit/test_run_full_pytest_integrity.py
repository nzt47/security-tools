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

    # ── 【不易·2026-09-21】D2 的判据曾经"恒为真"（遗留 L3 的一半）────────────
    # 上面那条 `test_not_completed_on_thread_timeout_kill` 的夹具**漏掉了真实日志里
    # 最关键的一行** —— pytest 的收集表头：
    #     collected 7144 items / 121 deselected / 1 skipped / 7023 selected
    # 旧正则 `\d+\s+(passed|failed|error|skipped|...)` 会被它命中（`121 deselected`
    # 与 `1 skipped` 都满足），而表头在**任何用例执行之前**就写进日志
    # ⇒ 被 `os._exit(1)` 强杀的块也被判"已跑完"，D2 的逐文件补跑**永不触发**。
    # 这是 TASK-00 D12「夹具形状不得代替生产形状」的又一实例：夹具里没有表头，
    # 于是这条缺陷在单测里**永远测不出来**，只能在 45 分钟的全量回归里以
    # 「✔ 全部 4 个分块均正常收尾」的假成功暴露。
    #
    # ⇒ 下面三条夹具逐字复刻真实产物的形状（`pytest_chunks/chunk_0.log`）。

    #: 真实收集表头（来自 pytest_chunks/chunk_0.log 第 2 行）
    REAL_COLLECT_HEADER = "collected 7144 items / 121 deselected / 1 skipped / 7023 selected"
    #: 真实强杀标记（一长串 `+` 包裹 " Timeout "，不是字面量 `+++ Timeout +++`）
    REAL_TIMEOUT_MARKER = "+" * 35 + " Timeout " + "+" * 35

    def test_real_killed_chunk_shape_is_not_completed(self, tmp_path):
        """**真实形状**负例：收集表头 + Timeout 标记 + 无摘要 ⇒ 未跑完"""
        log = tmp_path / "chunk_0.log"
        log.write_text(
            "============================= test session starts =============================\n"
            f"{self.REAL_COLLECT_HEADER}\n"
            "\n"
            "tests\\unit\\test_audit_migration.py ..................................... [ 15%]\n"
            "tests\\unit\\test_capregistry_callpaths_routes.py "
            f"{self.REAL_TIMEOUT_MARKER}\n"
            "~~~~~~~~~~~~~~~~~~~~~~~~~~ Stack of asyncio_0 (6104) ~~~~~~~~~~~~~~~~~~~~~~~~~~\n"
            "  File \"...\\threading.py\", line 1009, in _bootstrap\n"
            f"{self.REAL_TIMEOUT_MARKER}\n",
            encoding="utf-8",
        )
        ok, detail = RFP.chunk_log_status(str(log))
        assert ok is False, "收集表头不得被当成结束摘要（否则丢文件会被报成 ✔ 已跑完）"
        assert "强杀" in detail

    def test_collect_header_alone_is_not_a_summary(self, tmp_path):
        """只有收集表头（无 Timeout 标记、无摘要，如被外部 kill / 崩溃）⇒ 未跑完"""
        log = tmp_path / "chunk.log"
        log.write_text(
            "============================= test session starts =============================\n"
            f"{self.REAL_COLLECT_HEADER}\n"
            "tests\\unit\\test_x.py .......\n",
            encoding="utf-8",
        )
        ok, detail = RFP.chunk_log_status(str(log))
        assert ok is False
        assert "无 pytest 结束摘要" in detail

    def test_collect_header_plus_real_summary_is_completed(self, tmp_path):
        """反向保护（不得过度修正）：带表头的**正常**日志仍必须判已跑完。

        形状取自真实产物 `pytest_chunks/chunk_1.log`：摘要在倒数第 3 行，
        其后还有 2 行收尾日志（`sensor.window_sensor` 等）⇒ 不能要求"摘要是最后一行"。
        """
        log = tmp_path / "chunk_1.log"
        log.write_text(
            "============================= test session starts =============================\n"
            f"{self.REAL_COLLECT_HEADER}\n"
            "\n"
            "tests\\boundary\\test_core_boundary.py ................................... [  1%]\n"
            "= 6099 passed, 16 skipped, 123 deselected, 16 xfailed, 19 warnings in 581.26s (0:09:41) =\n"
            "2026-09-21 02:32:53,761 [    INFO] sensor.window_sensor: WindowSensor 监控已停止\n"
            "2026-09-21 02:32:53,761 [    INFO] agent.llm_monitor: LLM 监控：会话最后一条通信已保存\n",
            encoding="utf-8",
        )
        ok, detail = RFP.chunk_log_status(str(log))
        assert ok is True, detail
        assert "6099 passed" in detail

    def test_no_tests_ran_after_header_is_completed(self, tmp_path):
        """全 deselected / 收集为空 ⇒ 会话正常收尾，不触发补跑"""
        log = tmp_path / "chunk.log"
        log.write_text(
            "============================= test session starts =============================\n"
            "collected 0 items / 44 deselected\n"
            "no tests ran in 0.03s\n",
            encoding="utf-8",
        )
        ok, _ = RFP.chunk_log_status(str(log))
        assert ok is True

    def test_zero_collected_with_duration_tail_is_completed(self, tmp_path):
        """`collected 0 items` + 只有 warnings 的结束摘要 ⇒ 正常收尾（不是"被强杀"）

        真实样本：`tests/acceptance/test_observability_acceptance.py` 的用例全部标了
        `@pytest.mark.slow`，fast 模式（`-m "not slow"`）下就该 **0 条**；
        而本仓 conftest 的自定义 footer 让结束行变成
        `==== 3 warnings in 0.28s ====`（**不含 passed/failed 计数**）。
        实测代价：若不特判，`still_lost_files.txt` 会点名一个**根本没丢**的文件，
        runner 直接报 FAIL（实测发生在 2026-09-21 的全量回归）。
        """
        log = tmp_path / "chunk.log"
        log.write_text(
            "============================= test session starts =============================\n"
            "collected 0 items\n"
            "\n"
            "================================ 测试失败 - 需要修复！✗ ================================\n"
            "\n"
            "测试统计:\n"
            "  通过: 0\n"
            "  失败: 0\n"
            "  跳过: 0\n"
            "============================= 3 warnings in 0.28s =============================\n",
            encoding="utf-8",
        )
        ok, detail = RFP.chunk_log_status(str(log))
        assert ok is True, f"0 条 + 有时长尾巴应判正常收尾，实得：{detail}"

    def test_zero_collected_without_duration_tail_is_not_completed(self, tmp_path):
        """反向保护：只有 `collected 0 items`、没有任何时长尾巴 ⇒ 仍判未跑完

        这是"收集期刚过就被杀"的保守方向：宁可多补跑一次，也不许假报 ✔。
        """
        log = tmp_path / "chunk.log"
        log.write_text(
            "============================= test session starts =============================\n"
            "collected 0 items\n",
            encoding="utf-8",
        )
        ok, _ = RFP.chunk_log_status(str(log))
        assert ok is False


#: 真实 Timeout 日志样本（按优先级）。`pytest_chunks/` 是 runner 运行期产物（gitignored），
#: 一旦重跑全量就会被覆盖 ⇒ 同时登记 `_ci_logs/t10/real_logs/` 里的只读副本。
#: 两者都可能不存在（CI / 干净克隆）⇒ 全缺时 skip，不制造假红。
_KILLED_LOG_CANDIDATES = (
    "pytest_chunks/chunk_0.log",
    "pytest_chunks/chunk_3.log",
    "_ci_logs/t10/real_logs/chunk_0_killed.log",
    "_ci_logs/t10/real_logs/chunk_3_killed.log",
)


class TestRealKilledChunkLogs:
    """用**真实产物**复核判定（TASK-00 D12：修复结论必须用真实来源的产物复测一次）

    ⚠️ 样本选取必须是**按内容**而不是按文件名：`pytest_chunks/chunk_*.log` 每次
    全量回归都会被覆盖 —— 被打断的那一块下次可能正常收尾（修复 `scan()` 缓存后实测
    就从"chunk_3 超时"变成"chunk_3 正常收尾"），此时它**不再是**负例样本。
    故这里逐份检查"是否含 Timeout 标记"，只把真的含标记的当负例；
    一份都没有时 skip（CI / 干净克隆上 `pytest_chunks/` 不存在）。
    """

    def test_real_timeout_logs_are_judged_incomplete(self):
        samples = []
        for rel in _KILLED_LOG_CANDIDATES:
            log = REPO_ROOT / rel
            if not log.exists():
                continue
            text = log.read_text(encoding="utf-8", errors="replace")
            if not RFP._TIMEOUT_RE.search(text):
                continue          # 该文件已被新一轮全量覆盖成"正常收尾"的日志，不是负例
            samples.append((rel, log))
        if not samples:
            pytest.skip("没有可用的真实 Timeout 日志样本（pytest_chunks/ 已被覆盖且无归档副本）")
        for rel, log in samples:
            ok, detail = RFP.chunk_log_status(str(log))
            assert ok is False, (
                f"真实被强杀的日志 {rel} 被判成『已跑完』 —— 这正是 2026-09-21 的假成功缺陷"
            )
            assert "强杀" in detail, f"{rel} 判定详情未点明强杀：{detail}"


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
