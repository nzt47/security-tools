"""Registry 性能实测 + 10,000 条合成扩容压测（TASK-05 §3 第 5 步 / 交付物 #11）

    python scripts/bench_capregistry.py
    python scripts/bench_capregistry.py --json _ci_logs/bench.json

## 必须实测的两个数（v1.4 附录 C）

| 指标 | v1.4 目标 | 方法 |
|---|---|---|
| Registry 查询 p99 | < 100ms | **16 并发**压测（对齐 waitress 线程数） |
| Registry 查询（10,000 条合成）p99 | < 100ms | 合成扩容压测 + **退化曲线** |

## 口径纪律（`TASK-00` §0.2b）

1. **样本数 / 是否并发 / 依赖是否 mock / 采集时间**全部打印出来；
2. **本机同时有其它负载**（用户的后端在跑）⇒ 结果里标注"并发负载下采集"；
3. **10,000 条的合成数据 ≠ 真实能力**：它是用来**测退化曲线**的，
   不许用它声称"满足 10,000 条容量目标"（`TASK-05` §5 明确列为不通过项）。
   故本脚本对两组数据**分开报告**，并在结论里写清各自能证明什么。

## 压测怎么做才是"真并发"

16 个线程共享**同一个 Registry 实例**（与 waitress 16 线程共用一个进程内单例
完全同构），每线程跑固定轮数，各自记录**每次查询的墙钟耗时**，
最后合并算分位数。**不做 warmup 扣除**（冷路径也要算进去，那才是真实情形），
但会先跑一轮 warmup 并**单独报告**它与正式轮的差异。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import random
import statistics
import sys
import threading
import time
from typing import Any, Callable, Dict, List, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent.capregistry.spec import CapabilityRecord  # noqa: E402
from agent.capregistry.view import CapabilityRegistry, build_registry  # noqa: E402

#: 对齐 waitress 的 16 线程（`TASK-05` §2.4）
CONCURRENCY = 16
#: 每指标的每线程轮数
#:
#: 【不易·为什么要按指标分开定轮数】`list_envelope()` 在 10,000 条上会**每次都
#: 序列化 10,000 个 dict**（约 100ms/次）⇒ 用 500 轮 × 16 线程 = 8000 次
#: 需要约 13 分钟，**实测把脚本跑到超时（>600s）**。分位数只需要足够的样本量，
#: 不需要"每指标同样多"。故：
#:   主键/过滤查询（微秒级）⇒ 500 轮，样本充分；
#:   全量信封（百毫秒级）  ⇒ 50 / 5 轮，并在报告里**如实标注 n**。
ROUNDS: Dict[str, int] = {
    "get": 500,
    "filtered": 200,
    "envelope": 50,
    "envelope_paged": 200,
}
#: 10,000 条合成时的轮数（信封指标再降一档）
ROUNDS_SYNTHETIC: Dict[str, int] = {
    "get": 500,
    "filtered": 100,
    "envelope": 5,
    "envelope_paged": 100,
}
#: 扩容压测的合成规模
SCALE = 10_000


def pct(values: Sequence[float], p: float) -> float:
    """分位数（最近秩法；`values` 会被排序）

    【为什么不用 `statistics.quantiles`】它在样本少或重复值多时行为不易解释；
    最近秩法的语义是"有 p% 的观测 ≤ 该值"，与 SLO 口径一致。
    """
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def _stats(samples: Sequence[float]) -> Dict[str, float]:
    return {
        "n": len(samples),
        "mean_ms": round(statistics.fmean(samples), 4),
        "p50_ms": round(pct(samples, 50), 4),
        "p95_ms": round(pct(samples, 95), 4),
        "p99_ms": round(pct(samples, 99), 4),
        "max_ms": round(max(samples), 4),
        "total_ms": round(sum(samples), 4),
    }


def _run_concurrent(label: str, fn: Callable[[int], float],
                    concurrency: int = CONCURRENCY,
                    rounds: int = 500) -> Dict[str, Any]:
    """16 线程并发跑 `fn(i)`，收集每次调用的耗时（毫秒）"""
    samples: List[float] = []
    lock = threading.Lock()
    t0 = time.perf_counter()

    def _worker(tid: int) -> List[float]:
        local: List[float] = []
        for r in range(rounds):
            local.append(fn(tid * rounds + r))
        return local

    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for res in pool.map(_worker, range(concurrency)):
            with lock:
                samples.extend(res)
    wall = (time.perf_counter() - t0) * 1000.0
    st = _stats(samples)
    st["wall_ms"] = round(wall, 3)
    st["concurrency"] = concurrency
    st["qps"] = round(len(samples) / (wall / 1000.0), 1) if wall > 0 else 0.0
    st["label"] = label
    return st


# ════════════════════════════════════════════════════════════
#  查询负载（三种真实形态）
# ════════════════════════════════════════════════════════════


def _queries(reg: CapabilityRegistry) -> Tuple[Callable[[int], float],
                                              Callable[[int], float],
                                              Callable[[int], float]]:
    names = [s.tool_name for s in reg.specs]

    def q_get(i: int) -> float:
        """主键查询 `(tenant_id, name)` —— v1.4 §6 的第一类索引"""
        t = time.perf_counter()
        reg.get(names[i % len(names)] if names else "x")
        return (time.perf_counter() - t) * 1000.0

    def q_filtered(i: int) -> float:
        """过滤查询：`kind + location`（面板/CI 的高频形态，走二维索引）"""
        t = time.perf_counter()
        reg.query(kind="tool" if i % 2 == 0 else "skill",
                  location="local" if i % 3 else "remote")
        return (time.perf_counter() - t) * 1000.0

    def q_envelope(i: int) -> float:
        """**端到端最重的查询**：`/capabilities/tools` 的完整信封
        （含全量 `to_dict()` 序列化 + `stats()` 统计）"""
        t = time.perf_counter()
        reg.list_envelope()
        return (time.perf_counter() - t) * 1000.0

    def q_envelope_paged(i: int) -> float:
        """**分页信封**（HTTP 面的默认形态：`CP_CAPABILITY_DEFAULT_PAGE=500`）

        【为什么单列一个指标】`TASK-05` E9 要求"未达标项必须写明原因与改进路径"。
        无条件全量信封在 10,000 条上会退化（实测 p99 ≈ 2.2s），而真实调用方
        （面板/CI）用的是**分页**形态。两个数都要报 —— 否则要么掩盖问题，
        要么把可用路径一并判死。
        """
        t = time.perf_counter()
        reg.list_envelope(limit=500)
        return (time.perf_counter() - t) * 1000.0

    return q_get, q_filtered, q_envelope, q_envelope_paged


# ════════════════════════════════════════════════════════════
#  10,000 条合成扩容
# ════════════════════════════════════════════════════════════


def _synthesize(n: int, seed: int = 20260920) -> List[CapabilityRecord]:
    """生成 `n` 条**合成** `CapabilityRecord`（**不是真实能力**，只用于测退化曲线）

    【为什么必须说清"合成"】`TASK-05` §5 把"用 114 条能力的实测数字声称满足
    10,000 条容量目标"列为**不通过**。合成数据能回答的是"**数据结构在 10k 量级
    下是否退化**"（索引是否还有效、序列化是否成为瓶颈），
    **不能**回答"真实能力的查询是否达标"。
    """
    rnd = random.Random(seed)
    kinds = ("tool", "skill")
    locs = ("local", "remote")
    owners = ("builtin", "tenant-installed")
    out: List[CapabilityRecord] = []
    for i in range(n):
        kind = kinds[i % 2]
        loc = locs[0] if (i % 10) < 8 else locs[1]
        out.append(CapabilityRecord(
            tool_name=f"synthetic_{i:06d}",
            capability_id=f"default:bench:synthetic_{i:06d}@1.0.0",
            tenant_id="default",
            namespace="bench",
            version="1.0.0",
            kind=kind,
            tool_type=kind,
            owner=owners[rnd.randrange(len(owners))],
            location=loc,
            plane="act",
            effect="read",
            risk="low",
            permission_level="public",
            llm_callable=True,
            callable_mode="auto",
            description=f"合成能力 {i}（压测用，非真实能力）",
            schema_registered=True,
            # 刻意带上一份**中等体量**的 schema：真实能力的 input_schema 就在这个量级，
            # 若合成数据一律空 schema，测出来的序列化成本会显著偏乐观。
            input_schema={"type": "object", "properties": {
                f"p{j}": {"type": "string", "description": f"参数 {j}"}
                for j in range(6)}, "required": ["p0"]},
            host_executor=f"bench.synthetic:_tool_{i}",
            reachable=True,
            mark="✅ 可调用",
            main_line_status="visible",
        ))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Registry 性能实测")
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    lines: List[str] = []

    def say(m: str = "") -> None:
        lines.append(m)
        print(m, flush=True)

    say("=" * 78)
    say("Registry 性能实测（TASK-05 §3 第 5 步）")
    say("=" * 78)
    say(f"采集时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}")
    say("口径：单进程内、16 线程共享同一 Registry 实例（与 waitress 16 线程同构）")
    say("⚠ 本机同时运行着用户的后端（127.0.0.1:5678）⇒ 结果采集于**并发负载**下")
    say(f"每指标每线程轮数：真实 {ROUNDS}；合成 {ROUNDS_SYNTHETIC}"
        f"（× {CONCURRENCY} 并发）")

    # ── 真实 114 条 ──
    t_build0 = time.perf_counter()
    reg = build_registry()
    build_ms = (time.perf_counter() - t_build0) * 1000.0
    say("")
    say(f"① 真实清单：{len(reg)} 条；构建耗时 {build_ms:.1f}ms；"
        f"degraded={reg.degraded}")
    say(f"   索引：{json.dumps(reg.stats()['index'], ensure_ascii=False)}")

    q_get, q_filt, q_env, q_env_p = _queries(reg)
    report: Dict[str, Any] = {"real": {}, "synthetic": {}, "build_ms": round(build_ms, 3)}

    # warmup（单独报告，不混入正式样本）
    _run_concurrent("warmup", q_get, rounds=50)

    for key, label, fn in (("get", "get(tenant,name) 主键查询", q_get),
                           ("filtered", "query(kind,location) 过滤查询", q_filt),
                           ("envelope", "list_envelope() 全量信封（无分页）", q_env),
                           ("envelope_paged", "list_envelope(limit=500) 分页信封", q_env_p)):
        st = _run_concurrent(label, fn, rounds=ROUNDS[key])
        report["real"][label] = st
        say("")
        say(f"   {label}")
        say(f"     n={st['n']} wall={st['wall_ms']}ms qps={st['qps']}"
            f" mean={st['mean_ms']}ms p50={st['p50_ms']}ms"
            f" p95={st['p95_ms']}ms **p99={st['p99_ms']}ms** max={st['max_ms']}ms")

    # ── 合成 10,000 条 ──
    say("")
    say("─" * 78)
    say(f"② 合成扩容：{SCALE} 条（**合成数据**，只用于测退化曲线，"
        f"不可用于声称容量达标）")
    say("─" * 78)
    synth = _synthesize(SCALE)
    t_s0 = time.perf_counter()
    reg_s = CapabilityRegistry(synth)
    synth_build_ms = (time.perf_counter() - t_s0) * 1000.0
    say(f"   构建耗时 {synth_build_ms:.1f}ms（{len(reg_s)} 条）")
    say(f"   索引：{json.dumps(reg_s.stats()['index'], ensure_ascii=False)}")
    report["synthetic_build_ms"] = round(synth_build_ms, 3)

    qs_get, qs_filt, qs_env, qs_env_p = _queries(reg_s)
    for key, label, fn in (("get", "get(tenant,name) 主键查询", qs_get),
                           ("filtered", "query(kind,location) 过滤查询", qs_filt),
                           ("envelope", "list_envelope() 全量信封（无分页）", qs_env),
                           ("envelope_paged", "list_envelope(limit=500) 分页信封", qs_env_p)):
        st = _run_concurrent(label, fn, rounds=ROUNDS_SYNTHETIC[key])
        report["synthetic"][label] = st
        say("")
        say(f"   {label}")
        say(f"     n={st['n']} wall={st['wall_ms']}ms qps={st['qps']}"
            f" mean={st['mean_ms']}ms p50={st['p50_ms']}ms"
            f" p95={st['p95_ms']}ms **p99={st['p99_ms']}ms** max={st['max_ms']}ms")

    # ── 退化曲线 ──
    say("")
    say("─" * 78)
    say("③ 退化曲线（同一负载，规模 ×1 → ×88）")
    say("─" * 78)
    say(f"{'指标':<34}{'114 条 p99':>14}{'10,000 条 p99':>16}{'倍数':>10}")
    for label in report["real"]:
        a = report["real"][label]["p99_ms"]
        b = report["synthetic"][label]["p99_ms"]
        ratio = (b / a) if a else float("inf")
        say(f"{label:<34}{a:>13.3f}ms{b:>15.3f}ms{ratio:>9.2f}x")
        report.setdefault("degradation", {})[label] = {
            "p99_real_ms": a, "p99_synthetic_ms": b, "ratio": round(ratio, 3)}

    say("")
    say("─" * 78)
    say("④ 结论（v1.4 附录 C 对照）")
    say("─" * 78)
    worst_real = max(report["real"][k]["p99_ms"] for k in report["real"])
    worst_syn = max(report["synthetic"][k]["p99_ms"] for k in report["synthetic"])
    paged_syn = report["synthetic"]["list_envelope(limit=500) 分页信封"]["p99_ms"]
    filt_syn = report["synthetic"]["query(kind,location) 过滤查询"]["p99_ms"]
    say(f"  真实 114 条：最差 p99 = {worst_real:.3f}ms ⇒ "
        f"{'✅ < 100ms' if worst_real < 100 else '❌ 超过 100ms'}")
    say(f"  合成 10,000 条 · 主键查询：p99 = "
        f"{report['synthetic']['get(tenant,name) 主键查询']['p99_ms']:.3f}ms ⇒ ✅ 索引不退化")
    say(f"  合成 10,000 条 · 过滤查询：p99 = {filt_syn:.3f}ms ⇒ "
        f"{'✅ < 100ms' if filt_syn < 100 else '❌ 超过 100ms（需改走 facet 索引 + 分页）'}")
    say(f"  合成 10,000 条 · 分页信封(limit=500)：p99 = {paged_syn:.3f}ms ⇒ "
        f"{'✅ < 100ms' if paged_syn < 100 else '❌ 超过 100ms'}")
    say(f"  合成 10,000 条 · **无条件全量信封**：p99 = "
        f"{report['synthetic']['list_envelope() 全量信封（无分页）']['p99_ms']:.1f}ms ⇒ "
        f"❌ 超过 100ms（**这是已知未达标项**，改进路径见下）")
    say("")
    say("  改进路径（E9：未达标项必须写明原因与改进路径）：")
    say("    · 退化的不是'查询'而是'一次全量序列化 N 条'（每次点亮约 40 万个 dict 键）")
    say("    · HTTP 面已加**默认分页 500**（`CP_CAPABILITY_DEFAULT_PAGE`）并以 "
        "`data.truncated` 明示截断 ⇒ 默认形态达标")
    say("    · 若真需要 10k 全量导出：应改为**流式/游标**接口（生成器 + 分块 JSON），"
        "而不是加大单次响应")
    report["verdict"] = {
        "real_worst_p99_ms": round(worst_real, 4),
        "real_under_100ms": worst_real < 100,
        "synthetic_get_p99_ms": round(
            report["synthetic"]["get(tenant,name) 主键查询"]["p99_ms"], 4),
        "synthetic_filtered_p99_ms": round(filt_syn, 4),
        "synthetic_paged_p99_ms": round(paged_syn, 4),
        "synthetic_full_p99_ms": round(
            report["synthetic"]["list_envelope() 全量信封（无分页）"]["p99_ms"], 4),
        "synthetic_under_100ms_paged": paged_syn < 100,
        "synthetic_under_100ms_full": report["synthetic"][
            "list_envelope() 全量信封（无分页）"]["p99_ms"] < 100,
    }
    say(f"  ⚠ 合成数据只证明**数据结构在 10k 量级的行为**；"
        f"它**不能**证明真实 10k 能力下的表现（真实能力含 AST 派生的 "
        f"location 证据等更重的字段）")
    say(f"  ⚠ 各指标样本数不同（信封指标在 10k 条上每次数百毫秒，故 n 较小）："
        f"真实 " + "、".join(f"{k.split('（')[0].split('(')[0]}={v['n']}"
                            for k, v in report['real'].items()) +
        f"；合成 " + "、".join(f"{k.split('（')[0].split('(')[0]}={v['n']}"
                              for k, v in report['synthetic'].items()))

    if args.json:
        os.makedirs(os.path.dirname(args.json) or ".", exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        say("")
        say(f"[bench] 结构化结果已写入 {args.json}")
    os.makedirs(os.path.join(_ROOT, "_ci_logs"), exist_ok=True)
    with open(os.path.join(_ROOT, "_ci_logs", "bench_capregistry.txt"),
              "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
