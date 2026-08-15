"""TASK-05/06 学习类定时任务统一注册入口

背景（Why）:
    TASK-05 交付三个带自调度能力的模块，各自内部按 enabled 开关独立决定
    注册或 disabled（默认关闭 = 安全底线）:
        feedback_agent      每日反馈建议执行（config learning.feedback_agent）
        evolution_scheduler 周级 offline_evolver 进化（config learning.evolver）
        lifecycle           每日零使用淘汰判定（config learning.lifecycle）
    TASK-06 追加感知侧学习:
        behavior_drift      周级行为漂移检测（config learning.sensor_learning）
    本模块把它们收口到一处，供应用启动时一次注册
    （app_server __main__ 挂载 / CLI python -m agent.skills_mgmt.learning_scheduler），
    避免分散注册（任务书"三处定时调度注册（统一收口）"）。

【不易】约束:
    - 不改各模块内部逻辑，只统一调用各自 schedule()/unschedule()
    - 每个任务是否注册由各自配置开关决定；注册失败不阻断主流程
    - 不启动 daemon（由主进程 start_daemon；CLI --start-daemon 可选）
    - 调度触发均默认 dry-run / 观察模式（feedback/evolver/lifecycle/sensor_learning
      各自配置），正式写操作需显式开启（安全底线）
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def register_learning_schedulers() -> Dict[str, Any]:
    """统一注册 TASK-05/06 定时任务（各按 enabled 开关独立决定）。

    Returns:
        {"feedback_agent": {...}, "evolution": {...}, "lifecycle": {...},
         "behavior_drift": {...}}
        每项为对应 schedule() 的返回值（status: scheduled|disabled|error）。
    """
    from agent.skills_mgmt.feedback_agent import FeedbackAgent
    from agent.skills_mgmt.evolution_scheduler import EvolutionScheduler
    from agent.skills_mgmt.lifecycle import LifecycleManager
    from agent.learning.behavior_drift import BehaviorDriftScheduler

    return {
        "feedback_agent": FeedbackAgent().schedule(),
        "evolution": EvolutionScheduler().schedule(),
        "lifecycle": LifecycleManager().schedule(),
        "behavior_drift": BehaviorDriftScheduler().schedule(),
    }


def unregister_learning_schedulers() -> Dict[str, bool]:
    """注销 TASK-05/06 定时任务（按固定任务名定位，可跨实例）。"""
    from agent.skills_mgmt.feedback_agent import FeedbackAgent
    from agent.skills_mgmt.evolution_scheduler import EvolutionScheduler
    from agent.skills_mgmt.lifecycle import LifecycleManager
    from agent.learning.behavior_drift import BehaviorDriftScheduler

    return {
        "feedback_agent": FeedbackAgent().unschedule(),
        "evolution": EvolutionScheduler().unschedule(),
        "lifecycle": LifecycleManager().unschedule(),
        "behavior_drift": BehaviorDriftScheduler().unschedule(),
    }


def _print_results(results: Dict[str, Any]) -> None:
    for name, res in results.items():
        status = res.get("status", "unknown")
        print(f"  {name:<16} status={status}")
        if status == "scheduled":
            print(f"    task_id={res.get('task_id')}")
            print(f"    note  ={res.get('note', '')}")
        elif status == "disabled":
            print(f"    note  ={res.get('note', '')}")
        elif status == "error":
            print(f"    error ={res.get('error', '')}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="TASK-05 学习类定时任务统一注册入口")
    parser.add_argument("--start-daemon", action="store_true",
                        help="注册后启动 task_scheduler daemon（真实定时运行）")
    parser.add_argument("--unregister", action="store_true",
                        help="注销全部学习类定时任务")
    args = parser.parse_args()

    if args.unregister:
        results = unregister_learning_schedulers()
        print("=== TASK-05 学习类定时任务注销 ===")
        for name, ok in results.items():
            print(f"  {name:<16} removed={ok}")
        return

    print("=== TASK-05 学习类定时任务注册 ===")
    results = register_learning_schedulers()
    _print_results(results)
    print(f"\n完整摘要: {json.dumps(results, ensure_ascii=False)}")

    if args.start_daemon:
        from agent.task_scheduler import get_scheduler
        get_scheduler().start_daemon(check_interval=10)
        print("daemon 已启动（check_interval=10s；到期的 interval 任务将在首次 tick 执行）")


if __name__ == "__main__":
    main()


__all__: List[str] = [
    "register_learning_schedulers", "unregister_learning_schedulers",
]
