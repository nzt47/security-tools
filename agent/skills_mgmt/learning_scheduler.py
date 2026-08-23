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

    TASK-03（任务3 受控放行）追加：三类进化动作（feedback/evolution/lifecycle）
    的调度出口统一包装进 agent.learning.rollout_controller 四态放行框架
    （dry_run/observe/confirm/rollout）。默认总开关关闭 → 全部强制 dry_run，
    与既有行为完全一致；仅当显式开启放行模式时才叠加观察/审批/比例命中控制。

【不易】约束:
    - 不改各模块内部逻辑，只统一调用各自 schedule()/unschedule()
    - 每个任务是否注册由各自配置开关决定；注册失败不阻断主流程
    - 不启动 daemon（由主进程 start_daemon；CLI --start-daemon 可选）
    - 调度触发均默认 dry-run / 观察模式（feedback/evolver/lifecycle/sensor_learning
      各自配置），正式写操作需显式开启（安全底线）
    - 放行包装失败（import/配置异常）回退原 func，零行为变化
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger(__name__)

# 三类进化动作 → 调度任务名映射（放行框架只覆盖这三类；behavior_drift 为感知侧，不属进化动作）
_ROLLOUT_TASKS: Dict[str, str] = {
    "反馈建议执行": "feedback",
    "周期进化": "evolution",
    "生命周期检查": "lifecycle",
}


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

    results = {
        "feedback_agent": FeedbackAgent().schedule(),
        "evolution": EvolutionScheduler().schedule(),
        "lifecycle": LifecycleManager().schedule(),
        "behavior_drift": BehaviorDriftScheduler().schedule(),
    }
    # 任务3：调度出口接线——三类进化动作的已注册任务 func 包装进放行控制器
    _wire_rollout_controller()
    return results


def _wire_rollout_controller() -> None:
    """把三类进化动作的已注册调度任务 func 替换为放行控制器包装。

    默认（总开关关闭 → 全部 dry_run）下包装器以 dry_run=True 调用执行体，
    与既有"模块自身 dry_run=true"行为一致（零行为变化）；仅当显式开启
    observe/confirm/rollout 模式时才叠加放行控制。包装失败回退原 func。
    """
    try:
        from agent.task_scheduler import get_scheduler
        from agent.learning.rollout_controller import RolloutController, _executor_runners
    except Exception as e:  # noqa: BLE001 放行框架不可用 → 保持原调度行为
        logger.warning("[LearningScheduler] 放行框架接线失败（保持原调度行为）: %s", e)
        return
    try:
        sched = get_scheduler()
    except Exception as e:  # noqa: BLE001
        logger.error("[LearningScheduler] 调度器不可用: %s", e)
        return
    controller = RolloutController()
    by_name: Dict[str, Any] = {}
    for task in sched.tasks:
        if task.get("name") in _ROLLOUT_TASKS:
            by_name[task["name"]] = task
    for task_name, action in _ROLLOUT_TASKS.items():
        task = by_name.get(task_name)
        if task is None or "func" not in task:
            continue
        try:
            runners = _executor_runners(action)
        except Exception as e:  # noqa: BLE001 单个动作 runner 构建失败不阻断
            logger.warning("[LearningScheduler] 动作 %s runner 构建失败: %s",
                           action, e)
            continue
        task["func"] = _make_rollout_wrapper(
            controller, action, runners["dry_runner"], runners["run_real"])
        logger.info("[LearningScheduler] 调度出口已接入放行框架 action=%s "
                    "（默认 dry_run，零行为变化）", action)


def _make_rollout_wrapper(controller: Any, action: str,
                          dry_runner: Callable[[], Any],
                          run_real: Callable[[], Any]) -> Callable[[], None]:
    """构造放行包装器：调度触发 → controller.run_scheduled（按模式分派）。"""

    def wrapper() -> None:
        try:
            controller.run_scheduled(action, dry_runner=dry_runner,
                                     run_real=run_real)
        except Exception as e:  # noqa: BLE001 调度线程稳定性：异常不抛出
            logger.error("[LearningScheduler] 放行调度执行失败 action=%s: %s",
                         action, e)

    return wrapper


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
    "_wire_rollout_controller", "_make_rollout_wrapper", "_ROLLOUT_TASKS",
]
