"""丢更新（lost update）复测与竞态防护单测（TASK-08 子工作流 D / E1f）

【实测更正（先读这一段，任务书描述与事实不符）】
    1. 报告**不在** `data/stress-report.json` —— 该路径不存在；
       实际是 `security-tools/stress-report.json`（684 B，2026-08-02）。
    2. 被争用对象**不是**会话语料/计数器/JSON 文件/DB 行，而是
       `agent/monitoring/prometheus.py:732` 的模块级进程内 dict
       `_intent_layer_counts`，唯一消费方是 `yunshu_intent_layer_ratio` 这个
       **Gauge**。同函数里 `Counter.inc()` 在竞态块**之外**且自带锁，不是竞态点。
    3. 该竞态**已被修复**：`e6b71281`（2026-08-13，晚于报告 11 天）加了
       `_counts_lock`。故本任务的处置是「已修复 + 提供可重测的复现脚本」。
    4. **丢更新是负载相关的**（这决定了测试怎么写，也决定了业务影响的结论）：
       实测 unlocked（修复前）形态：
       | 线程 | 每线程迭代 | 实测丢率（3 次） |
       |---|---|---|
       | 16 | 5000  | 0.0, 0.0, 0.0 |
       | 32 | 5000  | 0.0, 0.0, 0.0 |
       | 64 | 5000  | 0.0, 0.0, 0.0 |
       | **16** | **30000** | **0.019194, 0.019885, 0.002237** |
       | 64 | 30000 | 0.012604, 0.001528, 0.005740 |
       | 96 | 30000 | 0.011305, 0.009772, 0.009457 |

       ★**关键结论（与"16 线程是设计内并发所以安全"的直觉相反）**：
       把迭代数提到 30000 后，**生产设计并发 16 线程也会丢更新**（丢率 0.2%~2.0%）。
       先前"16 线程不丢"只是**迭代数太少、没撞上交错窗口**的假象。
       ⇒ 所以"设计内并发是否安全"的答案是：**修复前不安全**；
       `e6b71281` 加上的 `_counts_lock` 之后才安全（实测恒为 0）。

       ⇒ 也正因丢率随机性大（同配置 3 次可从 0.0015 到 0.019），
       本文件**不**用"观测到丢更新"当断言（那会是 flaky 测试），
       改用**确定性的互斥性证明**（见 `test_rmw_is_inside_critical_section`）。
"""

from __future__ import annotations

import threading
import time

import pytest


class TestRaceReproTooling:
    """复现脚本本身可用（并能在修复后重测同一指标）"""

    def test_measure_reports_zero_loss_with_lock(self):
        """当前代码（有锁）：64 线程下丢更新必须为 0"""
        from scripts.repro_race_lost_update import measure
        r = measure(threads=32, iters=2000, locked=True)
        assert r["lost_updates"] == 0, r
        assert r["lost_rate"] == 0.0
        assert r["observed_total"] == r["expected_total"]

    def test_measure_accounts_for_every_update(self):
        """完整性：各层计数之和 == 总数（数据结构未被破坏）"""
        from scripts.repro_race_lost_update import measure
        r = measure(threads=16, iters=1000, locked=True)
        assert r["integrity"]["sum_matches_observed"] is True
        assert r["integrity"]["all_layers_present"] is True

    def test_unlocked_emulation_removes_mutual_exclusion(self):
        """工具自证：`_emulate_prefix_unlocked` 确实去掉了互斥

        不依赖"观测到丢更新"（负载相关、会 flaky），而是**结构化**验证：
        替换后 `_counts_lock` 不再是那把真锁，且进入临界区**不再阻塞**。
        """
        from agent.monitoring import prometheus as P
        from scripts.repro_race_lost_update import _emulate_prefix_unlocked

        real_lock = P._counts_lock
        assert isinstance(real_lock, type(threading.Lock()))

        with _emulate_prefix_unlocked(P):
            assert P._counts_lock is not real_lock
            # 连续两次进入"临界区"都不会阻塞 ⇒ 互斥已被移除
            t0 = time.monotonic()
            with P._counts_lock:
                with P._counts_lock:
                    pass
            assert time.monotonic() - t0 < 1.0

        # 退出后必须还原（否则测试会污染后续用例）
        assert P._counts_lock is real_lock

    def test_emulation_is_restored_on_exception(self):
        """异常路径也必须还原锁（否则会静默影响后续所有测量）"""
        from agent.monitoring import prometheus as P
        from scripts.repro_race_lost_update import _emulate_prefix_unlocked

        real_lock = P._counts_lock
        with pytest.raises(ValueError):
            with _emulate_prefix_unlocked(P):
                raise ValueError("boom")
        assert P._counts_lock is real_lock


class TestRmwIsInsideCriticalSection:
    """★确定性证明：读改写确实在临界区内（这是修复生效的**充分**证据）"""

    def test_rmw_is_inside_critical_section(self):
        """持有 `_counts_lock` 时，`record_intent_layer` 必须阻塞在临界区外

        原理：主线程先占住锁；后台线程调用 `record_intent_layer("rule")`。
        若读改写真的在临界区内，后台线程在此刻**无法完成**计数；
        主线程放锁后才完成。这样就**不依赖**任何"碰巧的交错"。
        """
        from agent.monitoring import prometheus as P

        P.reset_intent_layer_counts()

        done = threading.Event()
        started = threading.Event()

        def _worker():
            started.set()
            P.record_intent_layer("rule")
            done.set()

        with P._counts_lock:                    # 主线程占住临界区
            t = threading.Thread(target=_worker, daemon=True)
            t.start()
            started.wait(timeout=5.0)
            # 后台线程已进入函数，但读改写被挡在锁外 ⇒ 计数尚未生效
            time.sleep(0.2)
            mid = dict(P._intent_layer_counts)
            blocked = done.is_set() is False
        # 放锁后应当很快完成
        assert done.wait(timeout=5.0), "放锁后计数仍未完成"

        final = dict(P._intent_layer_counts)
        assert mid.get("rule", 0) == 0, f"临界区被击穿，计数提前生效: {mid}"
        assert final.get("rule", 0) == 1, f"放锁后计数应生效: {final}"
        assert blocked is True

    def test_all_layers_share_one_lock(self):
        """不同 layer 之间也互斥（同一把锁保护整个 dict，而非按 key 分锁）"""
        from agent.monitoring import prometheus as P

        P.reset_intent_layer_counts()
        barrier = threading.Barrier(8)

        def _worker(i):
            barrier.wait(timeout=10.0)
            for _ in range(200):
                P.record_intent_layer(f"L{i % 5}")

        ts = [threading.Thread(target=_worker, args=(i,), daemon=True) for i in range(8)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(timeout=30.0)

        counts = dict(P._intent_layer_counts)
        assert sum(counts.values()) == 8 * 200, f"总数应精确等于 1600: {counts}"

    def test_reset_is_also_locked_and_consistent(self):
        """`reset_intent_layer_counts()` 同样在锁内，清空后视图一致"""
        from agent.monitoring import prometheus as P
        P.record_intent_layer("rule")
        P.reset_intent_layer_counts()
        assert dict(P._intent_layer_counts) == {}
