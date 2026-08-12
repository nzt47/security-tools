"""规划模块集成验证：并行执行 + 崩溃恢复真实场景（阶段 2 / D5+D9 门禁）

不依赖 pytest，单脚本可独立运行（CI / 本地共用同一入口）：
  场景 1（并行执行）：真实 PlanExecutor + 真实 SQLite 审计，4 个互不依赖
    慢任务（各 sleep 0.5s）开启 parallel_execution 并发执行，断言：
    a) 全部完成且结果正确；b) 总耗时明显小于串行耗时（真实并发）；
    c) 4 个 start 事件全部先于第 1 个 end 事件（重叠执行证据）。
  场景 2（崩溃恢复）：subprocess 启动 worker 进程构造"执行中崩溃"的库
    （计划 EXECUTING + step_0 任务 RUNNING 残留），主进程 terminate 强杀
    后重启恢复，断言：
    a) EXECUTING 幂等放行，恢复计划可继续执行到 COMPLETED；
    b) RUNNING 残留被死锁消解重置为 PENDING 并重新调度；
    c) 依赖链（step_0 -> step_1）恢复后按依赖顺序完成；
    d) 再重启：已完成计划被排除（0 个未完成计划）。
  最后输出库状态分布 / 转换历史（与 planning_db_inspect.py 同口径），
  供 CI 独立 step 用 inspect 脚本复核产物。

用法:
    python scripts/planning_integration_verify.py [--db <path>]
    python scripts/planning_integration_verify.py --worker --db <path>  # 内部：模拟崩溃进程
"""

import argparse
import asyncio
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime

# 项目根入 sys.path（脚本位于 scripts/ 下，子进程 worker 也依赖此修正）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from planning.executor import PlanExecutor, ToolRegistry
from planning.models import Plan, PlanState, Task, TaskStatus
from planning.storage import PlanningStorage
from planning.core import PlanningCore

logger = logging.getLogger("planning_integration_verify")

# 每任务 sleep 时长（秒）：串行 4 任务约 4*0.5=2.0s，并行应 <1.0s
_TASK_SLEEP = 0.5
# 并行断言阈值：4 任务并行总耗时上限（留足调度抖动余量）
_PARALLEL_ELAPSED_LIMIT = 1.5


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


# ── 场景 1：并行执行 ──────────────────────────────────────────────
async def _scenario_parallel_execution(db_path: str) -> None:
    logger.info("=" * 70)
    logger.info(f"[场景1] 并行执行真实场景 @{_ts()} | db={db_path}")
    registry = ToolRegistry()
    events = []

    async def _mk_tool(name: str):
        async def _tool():
            events.append(f"start_{name}")
            await asyncio.sleep(_TASK_SLEEP)
            events.append(f"end_{name}")
            return f"{name}-ok"
        return _tool

    for i in range(4):
        registry.register(f"pt{i}", await _mk_tool(f"pt{i}"))

    executor = PlanExecutor(registry, config={"parallel_execution": True})
    # 注入持久化审计：并行执行记录真实落库（execution_log 4 条 + 计划终态）
    from planning.persistence import PlanDB
    audit_db = PlanDB(db_path)
    executor.persistence = audit_db
    plan = Plan(original_task="并行集成验证", state=PlanState.READY)
    for i in range(4):
        plan.add_task(Task(id=f"pt{i}", description=f"调用pt{i}", priority=3))

    t0 = time.monotonic()
    result = await executor.execute_plan(plan)
    elapsed = time.monotonic() - t0

    assert result.state == PlanState.COMPLETED, f"计划状态: {result.state}"
    for i in range(4):
        task = result.get_task(f"pt{i}")
        assert task.status == TaskStatus.COMPLETED, f"任务 pt{i} 未完成: {task.status}"
        assert str(task.result) == f"pt{i}-ok", f"任务 pt{i} 结果错误: {task.result}"

    # 真实并发证据：4 个 start 全部先于第 1 个 end
    first_end = next(i for i, e in enumerate(events) if e.startswith("end_"))
    assert first_end >= 4, f"任务未重叠执行: {events}"
    # 串行约 2.0s，并行须显著低于上限
    assert elapsed < _PARALLEL_ELAPSED_LIMIT, (
        f"并行耗时异常: {elapsed:.2f}s（4×0.5s 任务，并行应 <{_PARALLEL_ELAPSED_LIMIT}s）"
    )
    # 审计落库：4 条执行记录 + 计划终态（供崩溃恢复场景混合库验证）
    assert audit_db.count_execution_logs() == 4, "并行执行记录未全部落库"
    audit_db.close()
    logger.info(
        f"[场景1] PASS @{_ts()} | 耗时 {elapsed:.2f}s | 事件序: {events}"
        f" | 审计已落库: execution_log=4 条 + 计划终态"
    )


# ── 场景 2：崩溃恢复（worker 子进程）──────────────────────────────
def _worker_main(db_path: str) -> int:
    """worker 进程：构造"执行中崩溃"的库后挂起等待被 kill。

    崩溃现场 = 计划 EXECUTING + step_0 任务 RUNNING（to_dict 序列化状态），
    覆盖恢复路径的死锁消解（RUNNING -> PENDING 重置）与 EXECUTING 幂等放行。
    """
    storage = PlanningStorage(db_path)
    plan = Plan(original_task="崩溃恢复集成验证", state=PlanState.READY, max_steps=50)
    plan.add_task(Task(id="step_0", description="调用rt0", priority=3,
                       status=TaskStatus.RUNNING))  # 模拟执行中崩溃的残留 RUNNING
    plan.add_task(Task(id="step_1", description="调用rt1", priority=3,
                       dependencies=["step_0"]))
    plan.state = PlanState.EXECUTING
    plan.updated_at = datetime.now()
    storage.upsert_plan(plan)  # 计划 + 任务（含 RUNNING）落库
    storage.close()
    print("WORKER_READY", flush=True)
    time.sleep(600)  # 等待主进程 terminate 强杀
    return 0


def _wait_for_ready(proc: subprocess.Popen, timeout: float = 60.0) -> bool:
    """阻塞读取子进程 stdout 直到出现 WORKER_READY（或超时）"""
    deadline = time.monotonic() + timeout
    buf = ""
    assert proc.stdout is not None
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            if proc.poll() is not None:
                return False
            continue
        buf += line
        if "WORKER_READY" in line:
            return True
    # 超时：残留输出打日志便于排查
    logger.warning(f"[场景2] 等待 worker 就绪超时，已读输出:\n{buf}")
    return False


async def _scenario_crash_recovery(db_path: str, reflect_dir: str) -> None:
    logger.info("=" * 70)
    logger.info(f"[场景2] 崩溃恢复真实场景 @{_ts()} | db={db_path}")
    cfg = {
        "reflector": {"persist_dir": reflect_dir},
        "planning": {"persist_dir": reflect_dir, "storage": {"path": db_path}},
    }

    # 1) 启动 worker 构造崩溃现场，读到就绪信号后强杀（模拟进程崩溃）
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "--worker", "--db", db_path],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", env=env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    try:
        assert _wait_for_ready(proc), "worker 未在超时内就绪"
        proc.terminate()  # 模拟崩溃（Windows TerminateProcess / POSIX SIGTERM）
        proc.wait(timeout=15)
        logger.info(f"[场景2] worker 已强杀（exit={proc.returncode}），崩溃现场已构造")
    finally:
        if proc.poll() is None:
            proc.kill()

    # 2) 重启恢复：EXECUTING 计划 + RUNNING 残留任务
    core = PlanningCore(config=cfg)
    recovered_map = core._active_plans
    assert len(recovered_map) == 1, f"恢复数量异常: {len(recovered_map)}"
    plan = next(iter(recovered_map.values()))
    assert plan.state == PlanState.EXECUTING, f"恢复状态: {plan.state}"
    assert plan.get_task("step_0").status == TaskStatus.RUNNING, (
        "崩溃现场 step_0 应为 RUNNING 残留（验证死锁消解重置）"
    )
    logger.info(
        f"[场景2] 重启恢复 PASS @{_ts()} | 恢复计划 {plan.id}"
        f" | step_0={plan.get_task('step_0').status.value}"
    )

    # 3) 注册工具并继续执行到 COMPLETED（EXECUTING 幂等放行 + 依赖链正确）
    async def _mk_tool(name: str):
        async def _tool():
            await asyncio.sleep(0.2)
            return f"{name}-ok"
        return _tool

    core.tool_registry.register("rt0", await _mk_tool("rt0"))
    core.tool_registry.register("rt1", await _mk_tool("rt1"))
    await core.execute_plan(plan)
    assert plan.state == PlanState.COMPLETED, f"恢复执行未完成: {plan.state}"
    assert plan.get_task("step_0").status == TaskStatus.COMPLETED
    assert plan.get_task("step_1").status == TaskStatus.COMPLETED
    # 依赖顺序：execution_history 中 step_0 先于 step_1
    order = [r.task_id for r in core.executor.execution_history]
    assert order.index("step_0") < order.index("step_1"), f"依赖顺序错误: {order}"
    logger.info(
        f"[场景2] 恢复执行到 COMPLETED PASS @{_ts()}"
        f" | 执行顺序: {order} | 转换历史已落库"
    )

    # 4) 再重启：已完成计划必须被排除
    core3 = PlanningCore(config=cfg)
    assert len(core3._active_plans) == 0, (
        f"已完成计划未被排除: {list(core3._active_plans)}"
    )
    logger.info("[场景2] 再重启排除 PASS @%s | 未完成计划数=0", _ts())
    # 显式关闭连接：Windows 下 sqlite3 句柄延迟释放会导致库文件清理失败
    for c in (core, core3):
        if c.db is not None:
            c.db.close()


# ── 库产物校验（与 planning_db_inspect.py 同口径，供 CI 复核）─────
def _inspect_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    plans = conn.execute(
        "SELECT id, original_task, state FROM plans ORDER BY created_at"
    ).fetchall()
    dist = {}
    for p in plans:
        dist[p["state"]] = dist.get(p["state"], 0) + 1
    transitions = conn.execute(
        "SELECT plan_id, from_state, to_state, reason FROM transition_history"
        " ORDER BY id"
    ).fetchall()
    conn.close()
    logger.info(f"[库产物] 状态分布: {dist}")
    for t in transitions:
        logger.info(f"[库产物] 转换: {t['from_state']} -> {t['to_state']} | {t['reason']}")


async def _main(args) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    db_path = os.path.abspath(args.db)
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    reflect_dir = tempfile.mkdtemp(prefix="planning_reflect_")

    try:
        await _scenario_parallel_execution(db_path)
        await _scenario_crash_recovery(db_path, reflect_dir)
        _inspect_db(db_path)
    finally:
        # CI 需要保留库供独立 inspect 脚本复核（--keep-db）；本地默认清理
        if not args.keep_db:
            try:
                os.remove(db_path)
            except OSError as e:
                # Windows 下文件句柄延迟释放可能删除失败；失败不阻断，但必须告警，
                # 否则残留库会污染下次运行的计数断言（重复运行幂等性）。
                logger.warning(f"[集成验证] 清理验证库失败（保留供排查）: {e}")
        shutil.rmtree(reflect_dir, ignore_errors=True)

    logger.info("=" * 70)
    logger.info("✅ 规划模块集成验证全部通过（并行执行 + 崩溃恢复真实场景）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="规划模块集成验证（并行 + 崩溃恢复）")
    parser.add_argument("--db", default=os.path.join("data", "planning", "ci_verify.db"),
                        help="临时 SQLite 库路径（默认 data/planning/ci_verify.db，结束后删除）")
    parser.add_argument("--keep-db", action="store_true",
                        help="保留验证库（CI 供独立 inspect 脚本复核）")
    parser.add_argument("--worker", action="store_true",
                        help="内部：以 worker 模式运行（构造崩溃现场后挂起）")
    args, _ = parser.parse_known_args()

    if args.worker:
        return _worker_main(os.path.abspath(args.db))

    # 幂等起点：清理残留库（上次运行若删除失败，Windows 文件占用会残留并累加
    # 写入，导致 count 类断言被旧数据污染；集成验证必须可重复运行）
    db_path = os.path.abspath(args.db)
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError as e:
            logger.warning(f"[集成验证] 清理残留库失败（可能被占用）: {e}")

    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
