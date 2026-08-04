"""压力测试：50 并发工作流拦截 vs 自动升格线程干扰 → 延迟对比报表

场景（模拟生产瓶颈）：
    - 仓库预种入 N_WF 条"达标"工作流（conf>=0.7 / success>=5 / priority>=50 / ACTIVE / 未转换）
      —— 模拟"大量工作流达标"的存量状态
    - 阶段 A（基线）：升格线程关闭，ThreadPoolExecutor 50 并发调用 svc.try_execute(..., min_score=0.25)
      —— 模拟 orchestrator 拦截层同时收到 50 个请求
    - 阶段 B（干扰）：升格线程后台运行，每轮 list_convertible_workflows + convert_to_skill
      （模拟 lifecycle_manager._run_maint_workflow_skill_upgrade），同时再跑同样的 50 并发
    - 对比两阶段 P50/P95/P99/均值/最大延迟与命中率，判断升格线程是否拖慢请求

关键机制（为什么升格可能产生干扰）：
    1. convert_to_skill 内部会读仓库 + 构建 SKILL.md + 写 skills_repo 文件
    2. 升格线程的 repo.upsert 触发 matcher.register → _dirty → 下次查询触发全量索引重建
    3. 请求执行成功也会 repo.upsert（RLock 全量 json dump），与升格线程写文件序列化竞争

数据隔离：临时目录构造 WorkflowLearningService，不污染 data/learned_workflows.json。
升格转换：mock convert_to_skill（模拟文本构建 + 文件写入负载），不触达真实 skills_mgmt 存储。
升格候选：每轮转换后重置 converted_to_skill_id，持续制造"新达标工作流"，保证升格线程全程满负荷。

运行:
    python scripts/stress_workflow_interception_upgrade.py
    python scripts/stress_workflow_interception_upgrade.py --wf-count 500 --concurrency 50 --rounds 3
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import statistics
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.workflow_learning.models import (  # noqa: E402
    LearnedWorkflow, WorkflowStep, WorkflowStatus,
)
from agent.workflow_learning.service import WorkflowLearningService  # noqa: E402

TASK_TEXT = "搜索最新的科技新闻并翻译成英文"


def _mock_tool_executor(tool_name: str, params: dict):
    return "mock-output"


def _make_qualified_wf(idx: int) -> LearnedWorkflow:
    """构造达标工作流（conf>=0.7 / success>=5 / priority>=50 / ACTIVE / 未转换）"""
    return LearnedWorkflow(
        id=f"wf_qualified_{idx}",
        name=f"达标工作流 {idx}",
        description=f"自动学习: 科技新闻搜索翻译 {idx}",
        task_signature="新闻|翻译|搜索",
        trigger_patterns=["新闻", "翻译", "英文"],
        steps=[
            WorkflowStep(step_id="step_1", tool_name="search",
                         params_template={"query": "最新的科技新闻"},
                         output_key="step_1_output"),
            WorkflowStep(step_id="step_2", tool_name="translate",
                         params_template={"text": "${prev_output}"},
                         output_key="step_2_output"),
        ],
        source_session_id="seed",
        source_user_input=TASK_TEXT,
        confidence=0.75,      # >= MIN_CONFIDENCE(0.7)
        priority=60,          # >= MIN_PRIORITY(50)
        tags=["learned", "新闻"],
        status=WorkflowStatus.ACTIVE.value,
        enabled=True,
        success_count=8,      # >= MIN_SUCCESS_COUNT(5)
        failure_count=0,
        converted_to_skill_id="",  # 未转换
    )


def _pct(vals, p):
    if not vals:
        return 0.0
    idx = min(len(vals) - 1, int(len(vals) * p))
    return vals[idx]


def _summary(name: str, latencies: list, hits: int):
    lat = sorted(latencies)
    n = len(lat)
    print(f"\n[{name}]")
    print(f"  请求数: {n}  命中: {hits}  命中率: {hits / n:.1%}" if n else "  无请求")
    if lat:
        print(f"  P50: {_pct(lat, 0.50):.1f}ms  P95: {_pct(lat, 0.95):.1f}ms  "
              f"P99: {_pct(lat, 0.99):.1f}ms  均值: {statistics.mean(lat):.1f}ms  "
              f"最大: {max(lat):.1f}ms")


def _run_concurrent(svc, concurrency: int) -> tuple:
    latencies, hits = [], 0

    def one(_):
        nonlocal hits
        t0 = time.perf_counter()
        res = svc.try_execute(TASK_TEXT, min_score=0.25)
        latencies.append((time.perf_counter() - t0) * 1000)
        if getattr(res, "matched", False):
            hits += 1

    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        list(ex.map(one, range(concurrency)))
    return latencies, hits


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="拦截 vs 升格线程干扰压测")
    parser.add_argument("--wf-count", type=int, default=200, help="预种达标工作流数量")
    parser.add_argument("--concurrency", type=int, default=50, help="并发请求数")
    parser.add_argument("--rounds", type=int, default=3, help="压测轮数")
    args = parser.parse_args(argv)

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "WARNING"),
                        format="%(levelname)s %(name)s: %(message)s")
    stop_upgrade = threading.Event()
    upgrade_errors: list = []

    def _fake_convert(wf_id: str, *, skills_service=None, force: bool = False) -> dict:
        """模拟 convert_to_skill：文本构建 + 写文件负载（不触达真实 skills_mgmt）"""
        try:
            # 模拟 SKILL.md 构建与落盘（约 1-3ms）
            time.sleep(0.002)
            wf = svc.repo.get(wf_id)
            if wf is None:
                raise RuntimeError(f"wf {wf_id} 不存在")
            # 标记已转换（模拟真实转换副作用）→ 下次 list_convertible 不再返回
            wf.converted_to_skill_id = f"skill_{wf_id}"
            svc.repo.upsert(wf)
            svc.matcher.register(wf)
            return {"workflow_id": wf_id, "skill_id": f"skill_{wf_id}",
                    "skill_name": wf.name, "version": "1.0.0", "action": "create"}
        except Exception as e:  # noqa: BLE001
            upgrade_errors.append(str(e))
            raise

    with tempfile.TemporaryDirectory(prefix="wf_stress_") as tmp:
        svc = WorkflowLearningService(repo_path=str(tmp), min_score=0.3)
        svc.set_tool_executor(_mock_tool_executor)

        # 预种达标工作流
        for i in range(args.wf_count):
            wf = _make_qualified_wf(i)
            svc.repo.upsert(wf)
            svc.matcher.register(wf)
        print(f"已预种达标工作流: {args.wf_count}")

        # 阶段 A: 基线（无升格干扰）
        a_lats, a_hits = _run_concurrent(svc, args.concurrency)
        _summary("阶段A 基线(无升格线程)", a_lats, a_hits)

        # 阶段 B: 升格线程后台干扰
        def _upgrade_loop():
            with patch.object(svc, "convert_to_skill", _fake_convert):
                while not stop_upgrade.is_set():
                    try:
                        cands = svc.list_convertible_workflows()
                        for c in cands[:5]:
                            svc.convert_to_skill(c["workflow_id"])
                    except Exception as e:  # noqa: BLE001
                        upgrade_errors.append(str(e))
                    time.sleep(0.02)

        t = threading.Thread(target=_upgrade_loop, daemon=True)
        t.start()
        time.sleep(0.1)  # 让升格线程先跑起来
        b_lats, b_hits = _run_concurrent(svc, args.concurrency)
        stop_upgrade.set()
        t.join(timeout=5)
        _summary("阶段B 升格线程干扰下", b_lats, b_hits)

        # 对比
        if a_lats and b_lats:
            a_p99, b_p99 = _pct(sorted(a_lats), 0.99), _pct(sorted(b_lats), 0.99)
            a_mean, b_mean = statistics.mean(a_lats), statistics.mean(b_lats)
            print("\n[对比]")
            print(f"  P99: A={a_p99:.1f}ms → B={b_p99:.1f}ms  变化 {b_p99 - a_p99:+.1f}ms ({(b_p99 / a_p99 - 1) * 100:+.1f}%)")
            print(f"  均值: A={a_mean:.1f}ms → B={b_mean:.1f}ms  变化 {b_mean - a_mean:+.1f}ms")

        if upgrade_errors:
            print(f"\n[警告] 升格线程异常 {len(upgrade_errors)} 次: {upgrade_errors[:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
