#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""TASK-S7-04 子项 A —— 性能预算补测（清掉 ``PERF_BUDGET_REBASED.md`` 的 ❓）

本脚本只做一件事：把该文件 §五 中**仍标 ❓ 的两项**（S6-01 已补测状态灯与首屏）
在**受控环境**里实测出**原始毫秒级采样**，并把结果落成 JSON（供回填文档与验收报告）。

| 项 | 预算 | 本脚本的测量口径 |
|---|---|---|
| Watchdog | `< 10 s`（硬性） | W-1 持锁超时感知（§五 指定方法：注入持锁 3 s，测"持锁开始 → 告警发出"）<br>W-2 主进程失联感知（**桩子进程**持有 watchdog 单例锁 → 终止该桩 → 轮询 `is_stale()` 判陈旧） |
| 熔断「达阈值 → 生效」 | `< 3 s` | C-1 第 N 次失败 → `state == OPEN`；C-2 阈值达成 → **下一次 outbound 被实际阻断**（`call()` 抛 `CircuitBreakerError`） |

【安全底线（硬约束，写进代码而不是只写在文档里）】

1. **绝不 kill 真实生产进程**：W-2 只用本脚本自己 ``subprocess`` 起来的**桩子进程**
   （命令行含 ``--stub-holder``），且在 ``finally`` 里确保回收；脚本退出前显式
   ``terminate()/kill()``。绝不按 pid 去杀别的进程。
2. **受控落点**：watchdog 锁文件、熔断器实例全部在 ``tempfile.mkdtemp()`` 下创建，
   不触碰 ``data/state/watchdog.lock`` 与任何运行时台账。
3. **不编造数字**：测不出的项在 JSON 里以 ``"status": "unmeasured"`` + ``method`` +
   ``env_requirement`` 如实输出，**不填估算值**。
4. **标明时钟口径**：全部耗时用 ``time.perf_counter()``（单调墙钟，单位毫秒）；
   JSON 的 ``clock`` 字段显式声明，避免与 S3-02 回放沙箱的「模型时钟」混淆。

【为什么这两项此前测不出】`PERF_BUDGET_REBASED.md` §五 只给了复测方案而无人执行；
本脚本把方案落成可复跑代码，故「可复现」不再是口头承诺。

用法：

    python scripts/measure_perf_budget.py                    # 全部项，默认 20 次采样
    python scripts/measure_perf_budget.py --items watchdog   # 只测 Watchdog
    python scripts/measure_perf_budget.py --repeat 50 --out reports/s7_04/perf_budget_probe.json
    python scripts/measure_perf_budget.py --json             # 机器可读输出（stdout）
"""

from __future__ import annotations

import argparse
import io
import json
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from contextlib import redirect_stdout
from typing import Any, Callable, Dict, List, Optional, Sequence

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

#: 时钟口径（**必须随数字一起引用**）
CLOCK_NOTE = "wall_clock(monotonic perf_counter; 单次采样单位 ms)"

#: §五 指定的受控注入量：持锁 3 s（> 默认 LOCK_WATCHDOG_HOLD_MS=2000）
DEFAULT_HOLD_SECONDS = 3.0
#: 持锁超时阈值（对齐 `LOCK_WATCHDOG_HOLD_MS` 生产默认值 2000）
DEFAULT_HOLD_MS = 2000
#: W-2 轮询周期（模拟"感知"侧的最短探测间隔）
DEFAULT_POLL_INTERVAL_MS = 100.0
#: 预算线（§11.2 / PERF_BUDGET_REBASED.md）
BUDGET_WATCHDOG_MS = 10_000.0
BUDGET_CIRCUIT_MS = 3_000.0


# ════════════════════════════════════════════════════════════
#  统计工具（原始采样 + 分位；不虚报小样本分位）
# ════════════════════════════════════════════════════════════


def percentile(values: Sequence[float], p: float) -> float:
    """最近秩分位（与 ``scripts/perf_compare_circuit_breaker.py`` 同口径）"""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0
    idx = int(len(ordered) * p / 100.0)
    return round(ordered[min(idx, len(ordered) - 1)], 4)


def summarize(samples: Sequence[float]) -> Dict[str, Any]:
    """原始采样 → 汇总（**同时保留 samples 原文**，便于第三方复核）"""
    values = [float(v) for v in samples]
    if not values:
        return {"n": 0, "samples_ms": [], "min": None, "p50": None,
                "p95": None, "max": None, "mean": None}
    return {
        "n": len(values),
        "samples_ms": [round(v, 4) for v in values],
        "min": round(min(values), 4),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
    }


def environment_note() -> Dict[str, Any]:
    """环境说明（复现的前提；与数字一起引用）"""
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "cpu_count": os.cpu_count(),
        "clock": CLOCK_NOTE,
        "isolation": ("进程内受控环境：临时目录 + 桩实现；不 kill 生产进程、"
                      "不读写运行时台账"),
    }


# ════════════════════════════════════════════════════════════
#  W-1：持锁超时感知（§五 指定方法）
# ════════════════════════════════════════════════════════════


def _measure_watchdog_hold_once(*, hold_seconds: float, hold_ms: int,
                                name: str) -> Dict[str, Any]:
    """一次「持锁开始 → 告警发出」端到端采样

    [BOUNDARY] 本仓库 `LockWatchdog` 的**实现在释放点**检测持锁超时
    （`WatchedLock.release()` 调 `record_hold_timeout`）。故本函数如实分两段记录：

    - ``hold_to_alert_ms``：持锁开始 → 告警回调触发（= 真实持锁时长 + 判定开销）；
    - ``release_to_alert_ms``：释放锁 → 告警回调触发（**纯判定开销**）。

    两段都进 JSON：只报前者会把「3 s 持锁」误读成「看门狗用了 3 s 才发现」。
    """
    from agent.monitoring.lock_watchdog import LockWatchdog, WatchedLock

    watchdog = LockWatchdog(hold_ms=int(hold_ms), wait_ms=int(hold_ms) * 2)
    fired: List[float] = []

    original_record = watchdog.record_hold_timeout

    def _spy(lock_name: str, ms: float, stack: str) -> None:
        fired.append(time.perf_counter())
        original_record(lock_name, ms, stack)

    watchdog.record_hold_timeout = _spy  # type: ignore[assignment]

    lock = WatchedLock(name=name, watchdog=watchdog)
    # 既有 `record_hold_timeout()` 会把栈打到 stdout（生产告警留给 Prometheus 采集）。
    # 本脚本要输出 JSON，故只在**测量窗口内**把 stdout 静音，不动被测量代码本身。
    sink = io.StringIO()
    with redirect_stdout(sink):
        t_hold_start = time.perf_counter()
        lock.acquire()
        time.sleep(float(hold_seconds))
        t_release = time.perf_counter()
        lock.release()
    t_alert = fired[0] if fired else None

    return {
        "hold_to_alert_ms": (None if t_alert is None
                             else round((t_alert - t_hold_start) * 1000.0, 4)),
        "release_to_alert_ms": (None if t_alert is None
                                else round((t_alert - t_release) * 1000.0, 4)),
        "observed_hold_ms": round((t_release - t_hold_start) * 1000.0, 4),
        "alert_fired": t_alert is not None,
    }


def measure_watchdog_hold(*, repeat: int, hold_seconds: float = DEFAULT_HOLD_SECONDS,
                          hold_ms: int = DEFAULT_HOLD_MS) -> Dict[str, Any]:
    """W-1 汇总（逐次采样 + 分位 + 与预算线的比较）"""
    runs: List[Dict[str, Any]] = []
    for index in range(max(1, int(repeat))):
        runs.append(_measure_watchdog_hold_once(
            hold_seconds=hold_seconds, hold_ms=hold_ms,
            name=f"perf_probe_lock_{index}"))
    end_to_end = [r["hold_to_alert_ms"] for r in runs
                  if r["hold_to_alert_ms"] is not None]
    overhead = [r["release_to_alert_ms"] for r in runs
                if r["release_to_alert_ms"] is not None]
    return {
        "status": "measured" if end_to_end else "unmeasured",
        "item": "Watchdog 持锁超时感知（PERF_BUDGET_REBASED §二 #9 / §五 指定方法）",
        "budget_ms": BUDGET_WATCHDOG_MS,
        "clock": CLOCK_NOTE,
        "injected": {"hold_seconds": hold_seconds, "hold_threshold_ms": hold_ms},
        "hold_to_alert": summarize(end_to_end),
        "release_to_alert_overhead": summarize(overhead),
        "runs": runs,
        "passed": bool(end_to_end) and max(end_to_end) < BUDGET_WATCHDOG_MS,
        "boundary": ("本仓库看门狗在**释放点**判定持锁超时 ⇒ 该口径测的是"
                     "「已释放的超长持锁」；**永不释放**的持锁不会触发告警"
                     "（此项由 W-2 的陈旧锁探测覆盖，如实并列，不合并成一个数字）"),
    }


# ════════════════════════════════════════════════════════════
#  W-2：主进程失联感知（陈旧锁探测）
# ════════════════════════════════════════════════════════════


def _stub_holder(lock_path: str, ready_path: str) -> int:
    """桩进程体：持有 watchdog 单例锁直到被杀（**只用于受控实测**）"""
    from agent.self_healing.watchdog_singleton import WatchdogSingleton

    guard = WatchdogSingleton(lock_path=lock_path, role="perf_probe_stub")
    guard.acquire()
    with open(ready_path, "w", encoding="utf-8") as fh:
        fh.write(str(os.getpid()))
    try:
        while True:
            time.sleep(0.2)
    finally:
        guard.release()
    return 0


def _measure_liveness_detection_once(*, poll_interval_ms: float,
                                     timeout_seconds: float,
                                     workdir: str) -> Dict[str, Any]:
    """一次「桩进程失联 → 判陈旧」采样

    [SAFETY] 被终止的是本脚本自己 ``subprocess.Popen`` 起来的**桩进程**：
    命令行带 ``--stub-holder``，pid 只来自 ``Popen.pid``。**绝不**枚举/终止其他进程。
    """
    from agent.self_healing.watchdog_singleton import WatchdogSingleton

    lock_path = os.path.join(workdir, "watchdog_probe.lock")
    ready_path = os.path.join(workdir, "stub_ready.txt")
    if os.path.exists(ready_path):
        os.remove(ready_path)

    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__),
         "--stub-holder", "--lock-path", lock_path, "--ready-path", ready_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=_PROJECT_ROOT)
    proc_pid = proc.pid
    try:
        deadline = time.perf_counter() + 30.0
        while not os.path.exists(ready_path):
            if time.perf_counter() > deadline:
                return {"detect_ms": None, "status": "stub_not_ready",
                        "stub_pid": proc_pid}
            if proc.poll() is not None:
                return {"detect_ms": None, "status": "stub_exited_early",
                        "stub_pid": proc_pid}
            time.sleep(0.02)

        holder_probe = WatchdogSingleton(lock_path=lock_path)
        held_before = not holder_probe.is_stale()

        t_kill = time.perf_counter()
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:  # pragma: no cover - 平台差异兜底
            proc.kill()
            proc.wait(timeout=10.0)

        detect_ms: Optional[float] = None
        deadline = t_kill + float(timeout_seconds)
        while time.perf_counter() < deadline:
            if WatchdogSingleton(lock_path=lock_path).is_stale():
                detect_ms = round((time.perf_counter() - t_kill) * 1000.0, 4)
                break
            time.sleep(max(0.001, float(poll_interval_ms) / 1000.0))
        return {
            "status": "measured" if detect_ms is not None else "timeout",
            "detect_ms": detect_ms,
            "stub_pid": proc_pid,
            "holder_alive_before_kill": held_before,
            "poll_interval_ms": poll_interval_ms,
        }
    finally:
        if proc.poll() is None:  # 兜底：任何路径都不留孤儿桩进程
            proc.kill()
            proc.wait(timeout=10.0)


def measure_watchdog_liveness(*, repeat: int,
                              poll_interval_ms: float = DEFAULT_POLL_INTERVAL_MS,
                              timeout_seconds: float = 15.0) -> Dict[str, Any]:
    """W-2 汇总：主进程（桩）失联 → 陈旧可判 → 与预算线比较"""
    runs: List[Dict[str, Any]] = []
    workdir = tempfile.mkdtemp(prefix="cp_s704_watchdog_")
    for _ in range(max(1, int(repeat))):
        runs.append(_measure_liveness_detection_once(
            poll_interval_ms=poll_interval_ms, timeout_seconds=timeout_seconds,
            workdir=workdir))
    detected = [r["detect_ms"] for r in runs if r.get("detect_ms") is not None]
    return {
        "status": "measured" if detected else "unmeasured",
        "item": "Watchdog 主进程失联感知（PERF_BUDGET_REBASED §二 #9「主进程失联 → 感知」）",
        "budget_ms": BUDGET_WATCHDOG_MS,
        "clock": CLOCK_NOTE,
        "injected": {"poll_interval_ms": poll_interval_ms,
                     "stub": "本脚本自起的桩子进程（terminate），非生产进程"},
        "detect": summarize(detected),
        "runs": runs,
        "passed": bool(detected) and max(detected) < BUDGET_WATCHDOG_MS,
        "boundary": ("`is_stale()` 是**显式调用**（不自动回收）：端到端「感知时间」"
                     "= 探测周期 + 单次判定耗时；本口径按 poll_interval 固定披露，"
                     "不把它说成「看门狗自动发现」"),
    }


# ════════════════════════════════════════════════════════════
#  C-1 / C-2：熔断「达到阈值 → 生效」
# ════════════════════════════════════════════════════════════


def _measure_circuit_once(*, name: str, failure_threshold: float,
                          min_requests: int) -> Dict[str, Any]:
    """一次「连续失败达阈值 → 实际阻断 outbound」采样

    路径（刻意走**真实调用入口** ``CircuitBreaker.call()``，而非直接改状态）：

    ① 用桩函数连续抛错，经 ``call()`` 记录失败 —— 与生产路径同一入口；
    ② 第 ``min_requests`` 次失败后错误率必然达阈值 ⇒ 记 ``t_threshold``；
    ③ 立刻再经 ``call()`` 发起一次 outbound ⇒ 期望抛 ``CircuitBreakerError``
       （**这就是"实际阻断"**）⇒ 记 ``t_blocked``；
    ④ ``outbound_block_latency_ms = t_blocked - t_threshold`` 即本项实测值。

    额外记 ``threshold_to_open_ms``（失败那次调用返回到读到 state==OPEN），
    以便把「状态跃迁」与「对下一次调用的阻断」两种语义分开披露。
    """
    from agent.circuit_breaker import CircuitBreaker, CircuitBreakerError, CircuitState

    breaker = CircuitBreaker(name=name, failure_threshold=failure_threshold,
                             min_calls=min_requests, cooldown_seconds=3600.0,
                             window_seconds=3600.0)

    class _StubFailure(RuntimeError):
        pass

    def _failing() -> None:
        raise _StubFailure("受控故障注入（桩实现，无外部依赖）")

    failures_until_open = 0
    t_threshold_before = 0.0
    t_threshold_after = 0.0
    for _ in range(int(min_requests)):
        t_call = time.perf_counter()
        try:
            breaker.call(_failing)
        except _StubFailure:
            pass
        failures_until_open += 1
        t_threshold_before = t_call
        t_threshold_after = time.perf_counter()
        if breaker.state == CircuitState.OPEN:
            break

    t_open_read = time.perf_counter()
    threshold_to_open_ms = round((t_open_read - t_threshold_after) * 1000.0, 4)

    blocked = False
    t_blocked: Optional[float] = None
    try:
        breaker.call(lambda: None)
    except CircuitBreakerError:
        blocked = True
        t_blocked = time.perf_counter()

    return {
        "status": "measured" if (blocked and t_blocked is not None) else "unmeasured",
        "failures_until_open": failures_until_open,
        "state_after_threshold": breaker.state.value,
        "threshold_call_span_ms": round((t_threshold_after - t_threshold_before) * 1000.0, 4),
        "threshold_to_open_ms": threshold_to_open_ms,
        "outbound_block_latency_ms": (None if t_blocked is None
                                      else round((t_blocked - t_threshold_after) * 1000.0, 4)),
        "outbound_actually_blocked": blocked,
    }


def measure_circuit_breaker(*, repeat: int, failure_threshold: float = 0.3,
                            min_requests: int = 5) -> Dict[str, Any]:
    """C-1/C-2 汇总"""
    runs: List[Dict[str, Any]] = []
    for index in range(max(1, int(repeat))):
        runs.append(_measure_circuit_once(
            name=f"perf_probe_breaker_{index}",
            failure_threshold=failure_threshold, min_requests=min_requests))
    block_latency = [r["outbound_block_latency_ms"] for r in runs
                     if r.get("outbound_block_latency_ms") is not None]
    open_latency = [r["threshold_to_open_ms"] for r in runs
                    if r.get("threshold_to_open_ms") is not None]
    all_blocked = all(r.get("outbound_actually_blocked") for r in runs)
    return {
        "status": "measured" if block_latency and all_blocked else "unmeasured",
        "item": "熔断「达到阈值 → 生效」（PERF_BUDGET_REBASED §二 #7b / §五 指定方法）",
        "budget_ms": BUDGET_CIRCUIT_MS,
        "clock": CLOCK_NOTE,
        "injected": {"failure_threshold": failure_threshold,
                     "min_requests": min_requests,
                     "stub": "进程内抛错桩（无外部依赖、无真实 outbound）"},
        "threshold_to_open": summarize(open_latency),
        "outbound_block_latency": summarize(block_latency),
        "runs": runs,
        "passed": bool(block_latency) and all_blocked
                  and max(block_latency) < BUDGET_CIRCUIT_MS,
        "boundary": ("本口径测的是熔断器**自身**的「阈值 → 阻断下一次调用」时延；"
                     "跨进程/跨节点的配置下发时延（若部署侧另有开关下发）不在此列，"
                     "需集群环境，单机测不出 ⇒ 如实标注，不并入本数字"),
    }


# ════════════════════════════════════════════════════════════
#  编队与输出
# ════════════════════════════════════════════════════════════

ITEMS: Dict[str, Callable[..., Dict[str, Any]]] = {
    "watchdog-hold": measure_watchdog_hold,
    "watchdog-liveness": measure_watchdog_liveness,
    "circuit-breaker": measure_circuit_breaker,
}
#: ``--items watchdog`` / ``all`` 的展开
GROUPS: Dict[str, Sequence[str]] = {
    "watchdog": ("watchdog-hold", "watchdog-liveness"),
    "circuit": ("circuit-breaker",),
    "circuit-breaker": ("circuit-breaker",),
    "all": tuple(ITEMS),
}


def resolve_items(raw: str) -> List[str]:
    """``--items`` 归一（未知项**显式报错**，不静默忽略）"""
    picked: List[str] = []
    for token in str(raw or "all").split(","):
        name = token.strip()
        if not name:
            continue
        if name in GROUPS:
            picked.extend(GROUPS[name])
        elif name in ITEMS:
            picked.append(name)
        else:
            raise SystemExit(f"[measure_perf_budget] 未知项 {name!r}；"
                             f"可选：{sorted(ITEMS)} / {sorted(GROUPS)}")
    seen: List[str] = []
    for name in picked:
        if name not in seen:
            seen.append(name)
    return seen


def run_measurement(items: Sequence[str], *, repeat: int,
                    hold_seconds: float, poll_interval_ms: float,
                    timeout_seconds: float, hold_ms: int = DEFAULT_HOLD_MS
                    ) -> Dict[str, Any]:
    """执行所选测量项 → 报告 dict（**不含任何估算值**）"""
    results: Dict[str, Any] = {}
    for name in items:
        if name == "watchdog-hold":
            results[name] = measure_watchdog_hold(repeat=repeat,
                                                  hold_seconds=hold_seconds,
                                                  hold_ms=hold_ms)
        elif name == "watchdog-liveness":
            results[name] = measure_watchdog_liveness(
                repeat=repeat, poll_interval_ms=poll_interval_ms,
                timeout_seconds=timeout_seconds)
        elif name == "circuit-breaker":
            results[name] = measure_circuit_breaker(repeat=repeat)
        else:  # pragma: no cover - resolve_items 已拦
            raise SystemExit(f"未知项 {name!r}")
    return {
        "task": "TASK-S7-04",
        "subtask": "A 性能预算补测",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "clock": CLOCK_NOTE,
        "environment": environment_note(),
        "repeat": repeat,
        "results": results,
    }


def render_text(report: Dict[str, Any]) -> str:
    """人类可读表（终端用；JSON 才是权威产物）"""
    lines: List[str] = []
    lines.append("=" * 78)
    lines.append("TASK-S7-04 子项 A：性能预算补测（受控环境实测，原始采样见 JSON）")
    lines.append("=" * 78)
    env = report.get("environment") or {}
    lines.append(f"环境：{env.get('platform')}｜Python {env.get('python')}"
                 f"｜CPU {env.get('cpu_count')}")
    lines.append(f"时钟：{report.get('clock')}")
    lines.append(f"采样次数：{report.get('repeat')}")
    lines.append("")
    for name, block in (report.get("results") or {}).items():
        lines.append(f"── {name} " + "─" * max(0, 70 - len(name)))
        lines.append(f"   状态：{block.get('status')}｜预算：{block.get('budget_ms')} ms"
                     f"｜判定：{'✅ 达标' if block.get('passed') else '❌ 未达标/未测出'}")
        for key, label in (("hold_to_alert", "持锁开始→告警"),
                           ("release_to_alert_overhead", "释放→告警（纯开销）"),
                           ("detect", "失联→判陈旧"),
                           ("threshold_to_open", "阈值达标→state=OPEN"),
                           ("outbound_block_latency", "阈值达标→outbound 实际阻断")):
            summary = block.get(key)
            if not isinstance(summary, dict) or not summary.get("n"):
                continue
            lines.append(f"   {label}：n={summary['n']} "
                         f"min={summary['min']} p50={summary['p50']} "
                         f"p95={summary['p95']} max={summary['max']} ms")
        if block.get("boundary"):
            lines.append(f"   [口径边界] {block['boundary']}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="TASK-S7-04 子项 A：性能预算补测（受控环境，输出原始毫秒级采样）")
    parser.add_argument("--items", default="all",
                        help="watchdog / circuit-breaker / all / 具体项名（逗号分隔）")
    parser.add_argument("--repeat", type=int, default=20, help="每项采样次数（默认 20）")
    parser.add_argument("--hold-seconds", type=float, default=DEFAULT_HOLD_SECONDS,
                        help="W-1 受控持锁时长（默认 3.0s）")
    parser.add_argument("--hold-ms", type=int, default=DEFAULT_HOLD_MS,
                        help="W-1 持锁超时阈值（默认 2000，即 LOCK_WATCHDOG_HOLD_MS）")
    parser.add_argument("--poll-interval-ms", type=float,
                        default=DEFAULT_POLL_INTERVAL_MS,
                        help="W-2 陈旧探测周期（默认 100ms）")
    parser.add_argument("--timeout-seconds", type=float, default=15.0,
                        help="W-2 单次探测上限（默认 15s）")
    parser.add_argument("--out", default=os.path.join("reports", "s7_04",
                                                      "perf_budget_probe.json"),
                        help="JSON 落点（默认 reports/s7_04/perf_budget_probe.json）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON（stdout）")

    # 桩进程模式（仅本脚本内部使用；不对外暴露为常规用法）
    parser.add_argument("--stub-holder", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--lock-path", default="", help=argparse.SUPPRESS)
    parser.add_argument("--ready-path", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.stub_holder:
        return _stub_holder(args.lock_path, args.ready_path)

    items = resolve_items(args.items)
    report = run_measurement(items, repeat=args.repeat,
                             hold_seconds=args.hold_seconds,
                             poll_interval_ms=args.poll_interval_ms,
                             timeout_seconds=args.timeout_seconds,
                             hold_ms=args.hold_ms)

    if args.out:
        out_path = args.out if os.path.isabs(args.out) \
            else os.path.join(_PROJECT_ROOT, args.out)
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
        report["output_path"] = out_path

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
        if report.get("output_path"):
            print(f"原始采样 JSON：{report['output_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
