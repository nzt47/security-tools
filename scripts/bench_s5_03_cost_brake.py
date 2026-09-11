"""TASK-S5-03 性能基线补充测量（为 PERF_BUDGET_REBASED.md 提供**本次实测**）

§11.2 的部分条目在仓库里**没有**既有实测（状态灯/首屏/路由/事件吞吐）。
本脚本只测**能在本机直接跑出**的那几项，产出真实数字；测不了的如实留空，
绝不臆造（写入 `PERF_BUDGET_REBASED.md` 的"未找到实测"栏）。

测量项：

1. `EventStore.emit()` 事件落盘吞吐（events/s）—— 对齐 P7.2-22「事件吞吐 ≤1000 events/s」
2. `ModelRouter.route()` 路由耗时 —— 对齐 §11.2「路由 <100ms」
3. `CostBrake.evaluate()` 判定耗时（含基线窗口 + 周聚合）
4. `CostBrake.allow_outbound()` 缓存命中路径耗时（热路径，必须廉价）
5. `utc.utc_daily()` 聚合耗时随事件条数的增长
6. `theta_limit()` 阶段阈值解析耗时

全部在临时目录内进行，不触碰仓库 `data/`。

用法::

    python scripts/bench_s5_03_cost_brake.py
    python scripts/bench_s5_03_cost_brake.py --events 2000 --repeat 30
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

TZ = datetime.now().astimezone().tzinfo
BASE = datetime(2026, 9, 14, 10, 0, tzinfo=TZ)


def percentile(values: list, q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


def summarize(name: str, samples_ms: list, *, note: str = "") -> dict:
    row = {
        "metric": name,
        "n": len(samples_ms),
        "avg_ms": round(statistics.fmean(samples_ms), 4),
        "p50_ms": round(percentile(samples_ms, 0.50), 4),
        "p95_ms": round(percentile(samples_ms, 0.95), 4),
        "p99_ms": round(percentile(samples_ms, 0.99), 4),
        "max_ms": round(max(samples_ms), 4),
        "note": note,
    }
    print(f"  {name:<44} avg={row['avg_ms']:>9.4f}  p50={row['p50_ms']:>9.4f}  "
          f"p95={row['p95_ms']:>9.4f}  p99={row['p99_ms']:>9.4f}  max={row['max_ms']:>9.4f}")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="S5-03 性能基线补充测量")
    parser.add_argument("--events", type=int, default=1000, help="事件吞吐样本条数")
    parser.add_argument("--repeat", type=int, default=20, help="各延迟项的重复次数")
    parser.add_argument("--json", default="", help="把结果写入该 JSON 路径")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    from agent.observability import events as ev
    from agent.observability import utc as U
    from agent.monitoring import cost_brake as CB

    workdir = Path(tempfile.mkdtemp(prefix="s5_03_bench_"))
    events_dir = str(workdir / "events")
    os.environ["CP_EVENTS_ENABLED"] = "1"
    os.environ[ev.ENV_DIR] = events_dir
    os.environ[U.ENV_ANCHOR_MODEL] = "gpt-4"
    for name in CB.__all__:
        if name.startswith("ENV_"):
            os.environ.pop(getattr(CB, name), None)
    U.reset_config_cache()
    ev.reset_event_stores()
    CB.reset_cost_brake()

    results: dict = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "machine": sys.platform,
        "python": sys.version.split()[0],
        "workdir": str(workdir),
        "items": [],
    }
    print("=" * 96)
    print("S5-03 性能基线补充测量（全部在临时目录内，不触碰仓库 data/）")
    print("=" * 96)

    store = ev.get_event_store()

    # ── 1. 事件吞吐 ────────────────────────────────────────────
    print("\n[1] 事件落盘吞吐（P7.2-22「事件吞吐 ≤1000 events/s」）")
    for chunk in (100, args.events):
        probe_dir = workdir / f"events_{chunk}"
        probe = ev.EventStore(str(probe_dir / "events.jsonl"))
        start = time.perf_counter()
        for index in range(chunk):
            probe.emit("metrics.delta", {"metric": "bench", "value": index},
                       actor="system", correlation_id="bench",
                       idempotency_key=f"bench:{chunk}:{index}")
        elapsed = time.perf_counter() - start
        probe.close()
        rate = chunk / elapsed if elapsed else 0.0
        print(f"  {chunk:>6} 条 → 用时 {elapsed * 1000:>8.2f} ms  "
              f"= {rate:>10.1f} events/s  "
              f"（单条 {elapsed / chunk * 1e6:>7.1f} µs）")
        results["items"].append({
            "metric": f"EventStore.emit throughput (batch={chunk})",
            "events_per_second": round(rate, 1),
            "elapsed_ms": round(elapsed * 1000, 4),
            "per_event_us": round(elapsed / chunk * 1e6, 2),
            "threshold_events_per_second": 1000,
            "meets_threshold": rate >= 1000,
        })

    # ── 2. 路由耗时 ────────────────────────────────────────────
    print("\n[2] ModelRouter.route() 路由耗时（§11.2「路由 <100ms」）")
    from agent.model_router.router import ModelRouter, ModelSelector
    router, selector = ModelRouter(), ModelSelector()
    texts = ["hello", "帮我分析这段代码的性能", "翻译这句话", "写一首诗",
             "帮我设计一个微服务架构并重构", "现在几点"]
    samples = []
    for _ in range(max(50, args.repeat * 10)):
        for text in texts:
            start = time.perf_counter()
            router.route("chat", text, 0)
            samples.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize("ModelRouter.route()", samples,
                                      note=f"{len(texts)} 种输入轮转，n={len(samples)}"))

    analyze = []
    for _ in range(max(20, args.repeat)):
        for text in texts:
            start = time.perf_counter()
            selector.select_model(selector.analyze_task(text))
            analyze.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize("ModelSelector.analyze_task+select",
                                      analyze, note="含意图分析"))

    # ── 3. 成本刹车判定耗时 ────────────────────────────────────
    print("\n[3] 成本刹车判定与热路径耗时（S5-03 自身预算）")
    day = BASE.date().isoformat()
    for offset in range(1, 8):
        past = (BASE - timedelta(days=offset)).date().isoformat()
        U.record_cost(model="gpt-4", tokens_in=5000, tokens_out=2000,
                      interaction_id=f"bench-base-{offset}", store=store,
                      ts=f"{past}T10:00:00.000+08:00")
    for index in range(50):
        U.record_cost(model="gpt-4", tokens_in=200, tokens_out=100,
                      interaction_id=f"bench-today-{index}", store=store,
                      ts=f"{day}T10:00:00.000+08:00")

    cfg = CB.load_config({})
    cfg.enabled, cfg.daily_cents, cfg.state_path = True, 1000.0, ""
    brake = CB.CostBrake(config=cfg, events_dir=events_dir, state_path="",
                         persist=False)
    evaluate_ms = []
    for _ in range(max(3, args.repeat // 2)):
        start = time.perf_counter()
        brake.evaluate(BASE)
        evaluate_ms.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize(
        "CostBrake.evaluate()（含 7 日基线窗口 + 周聚合）", evaluate_ms,
        note="读事件文件 9 次：当日 + 7 日基线 + 本周"))

    cached_ms = []
    for _ in range(max(20, args.repeat * 5)):
        start = time.perf_counter()
        brake.allow_outbound(kind="shadow", now=BASE)
        cached_ms.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize(
        "CostBrake.allow_outbound()（判定未过期，热路径）", cached_ms,
        note="不读盘、不加锁重算；应远低于 1ms"))

    theta_ms = []
    for _ in range(max(20, args.repeat * 5)):
        start = time.perf_counter()
        CB.theta_limit(BASE, config=cfg)
        theta_ms.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize("theta_limit()（阶段阈值解析）", theta_ms))

    # ── 4. utc_daily 随事件条数增长 ────────────────────────────
    print("\n[4] utc.utc_daily() 聚合耗时随事件条数增长")
    for count in (100, 500, 2000):
        probe_dir = workdir / f"agg_{count}"
        probe = ev.EventStore(str(probe_dir / "events.jsonl"))
        for index in range(count):
            U.record_cost(model="gpt-4", tokens_in=100, tokens_out=50,
                          interaction_id=f"agg-{count}-{index}", store=probe,
                          ts=f"{day}T11:00:00.000+08:00")
        probe.close()
        agg = []
        for _ in range(max(3, args.repeat // 4)):
            start = time.perf_counter()
            U.utc_daily(day, directory=str(probe_dir))
            agg.append((time.perf_counter() - start) * 1000)
        results["items"].append(summarize(f"utc.utc_daily()（{count} 条事件）", agg,
                                          note="单次全文件扫描"))
        # 清理，免得多份大文件叠加
        shutil.rmtree(probe_dir, ignore_errors=True)

    # ── 5. 快照落盘 ────────────────────────────────────────────
    print("\n[5] 归一化日成本视图落盘（data/cost_daily.json 的内容）")
    view_ms = []
    target = str(workdir / "cost_daily.json")
    for _ in range(max(3, args.repeat // 2)):
        start = time.perf_counter()
        CB.write_cost_daily(target, day=day, now=BASE, directory=events_dir,
                            config=cfg)
        view_ms.append((time.perf_counter() - start) * 1000)
    results["items"].append(summarize("write_cost_daily()", view_ms,
                                      note="含 utc_daily + 基线 + JSON 落盘"))

    print("\n" + "=" * 96)
    print(f"临时目录：{workdir}（即将清理）")
    shutil.rmtree(workdir, ignore_errors=True)
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"结果已写入：{args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
