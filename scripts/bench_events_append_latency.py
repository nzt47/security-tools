"""事件追加写延迟基准（TASK-S8-02「单进程写入 p99 不退化」的实证工具）

计量口径
    - 时钟源：``time.perf_counter()``（单调、纳秒分辨率，不受系统时钟调整影响）；
    - 单位：**微秒/次**；每次 ``store.emit()`` 计时一次（含幂等派生 + 本地写盘）；
    - 规模：默认 N=3000（任务书要求 N≥2000），每个变体独立文件 + 独立临时目录；
    - **绝不写仓库 data/**：全部落在 ``tempfile.mkdtemp()``；
    - **轮转交错采样**：三个变体在同一轮里依次各跑一次、且每轮轮换顺序。
      本机实测单次系统调用本身就有 ~150-500us 且偶发上百毫秒的尖峰
      （Windows 实时防护/慢盘），"先跑完 A 再跑完 B"的顺序采样会把机器漂移
      误记成变体差异；交错采样让三个变体共享同一时段的噪声，差值才可信。

三个变体（同一台机、同一次运行、同一 payload 形状）
    1. ``legacy``      —— 变更前的 ``_write_line``（``open(path,"a")`` + ``write`` + close，
                          含"每次追加都 Path.mkdir"的原行为），用实例属性覆盖回来，
                          其余代码路径完全相同；
    2. ``lock_off``    —— 新实现（单次 ``os.write`` + 目录就绪缓存），``CP_EVENTS_LOCK_ENABLED=0``；
    3. ``lock_on``     —— 生产路径：跨进程锁（有限等待 2s）+ 单次 ``os.write``。

用法::

    python scripts/bench_events_append_latency.py            # 默认 N=3000
    python scripts/bench_events_append_latency.py --n 5000
"""

from __future__ import annotations

import argparse
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List

# 【为什么手动加 sys.path】`python scripts/xxx.py` 时 sys.path[0] 是 scripts/，
# 仓库根不在搜索路径上 ⇒ `import agent.*` 失败。显式把仓库根插到最前，
# 使脚本既能 `python scripts/…` 直接跑，也能被别的入口 import。
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_N_DEFAULT = 3000


def _pct(sorted_values: List[float], q: float) -> float:
    """分位数（最近秩法；空集返回 0）"""
    if not sorted_values:
        return 0.0
    idx = min(len(sorted_values) - 1, int(round(q * (len(sorted_values) - 1))))
    return sorted_values[idx]


def _report(name: str, samples_us: List[float]) -> Dict[str, float]:
    ordered = sorted(samples_us)
    out = {
        "n": float(len(ordered)),
        "mean_us": round(statistics.fmean(ordered), 2) if ordered else 0.0,
        "p50_us": round(_pct(ordered, 0.50), 2),
        "p90_us": round(_pct(ordered, 0.90), 2),
        "p99_us": round(_pct(ordered, 0.99), 2),
        "max_us": round(ordered[-1], 2) if ordered else 0.0,
        "total_ms": round(sum(ordered) / 1000.0, 1),
    }
    print(f"{name:>10} | n={int(out['n']):<6} mean={out['mean_us']:>9.2f}us "
          f"p50={out['p50_us']:>9.2f}us p90={out['p90_us']:>9.2f}us "
          f"p99={out['p99_us']:>9.2f}us max={out['max_us']:>10.2f}us "
          f"total={out['total_ms']:>8.1f}ms")
    return out


def _measure(n: int, path: str, write_one: Callable[[int], None]) -> List[float]:
    """跑 n 次写入并逐次计时（返回微秒样本）"""
    samples: List[float] = []
    for i in range(n):
        start = time.perf_counter()
        write_one(i)
        samples.append((time.perf_counter() - start) * 1_000_000.0)
    return samples


def _new_store(path: str):
    from agent.observability.events import EventStore
    return EventStore(path, archive=False)


def _legacy_write_line(store, line: str) -> None:
    """变更前的 ``_write_line`` 原文（``open(...,"a")`` + 文本写 + close）"""
    parent = os.path.dirname(store.path)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    with open(store.path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def bench_legacy(n: int) -> List[float]:
    base = tempfile.mkdtemp(prefix="bench_legacy_")
    store = _new_store(str(Path(base) / "events.jsonl"))
    store._write_line = lambda line: _legacy_write_line(store, line)  # type: ignore[method-assign]
    return _measure(n, store.path, lambda i: store.emit(
        "cost", {"n": i}, correlation_id="bench", idempotency_key=f"legacy:{i}"))


def bench_lock(n: int, *, enabled: bool, label: str) -> List[float]:
    os.environ["CP_EVENTS_LOCK_ENABLED"] = "1" if enabled else "0"
    try:
        base = tempfile.mkdtemp(prefix=f"bench_{label}_")
        store = _new_store(str(Path(base) / "events.jsonl"))
        assert store.stats()["lock_enabled"] is enabled
        return _measure(n, store.path, lambda i: store.emit(
            "cost", {"n": i}, correlation_id="bench", idempotency_key=f"{label}:{i}"))
    finally:
        os.environ.pop("CP_EVENTS_LOCK_ENABLED", None)


def _make_variants() -> Dict[str, Callable[[int], None]]:
    """构造三个变体的写入闭包（各自独立文件；锁开关在**构造时**读取）"""
    variants: Dict[str, Callable[[int], None]] = {}

    base = tempfile.mkdtemp(prefix="bench_legacy_")
    legacy = _new_store(str(Path(base) / "events.jsonl"))
    legacy._write_line = lambda line: _legacy_write_line(legacy, line)  # type: ignore[method-assign]
    variants["legacy"] = lambda i: legacy.emit(
        "cost", {"n": i}, correlation_id="bench", idempotency_key=f"legacy:{i}")

    for label, enabled in (("lock_off", False), ("lock_on", True)):
        os.environ["CP_EVENTS_LOCK_ENABLED"] = "1" if enabled else "0"
        try:
            d = tempfile.mkdtemp(prefix=f"bench_{label}_")
            store = _new_store(str(Path(d) / "events.jsonl"))
            assert store.stats()["lock_enabled"] is enabled
        finally:
            os.environ.pop("CP_EVENTS_LOCK_ENABLED", None)
        variants[label] = (lambda s, tag: (lambda i: s.emit(
            "cost", {"n": i}, correlation_id="bench",
            idempotency_key=f"{tag}:{i}")))(store, label)
    return variants


def bench_interleaved(n: int, variants: Dict[str, Callable[[int], None]]
                      ) -> Dict[str, List[float]]:
    """轮转交错采样：每轮把各变体各跑一次，并逐轮轮换顺序（抵消机器漂移）"""
    names = list(variants)
    samples: Dict[str, List[float]] = {name: [] for name in names}
    # 预热：剔除"建目录/建锁文件/首次导入"等一次性成本
    for name in names:
        for i in range(5):
            variants[name](-1 - i)
    for i in range(n):
        order = names[i % len(names):] + names[:i % len(names)]
        for name in order:
            start = time.perf_counter()
            variants[name](i)
            samples[name].append((time.perf_counter() - start) * 1_000_000.0)
    return samples


def bench_lock_only(n: int) -> List[float]:
    """只测锁原语的 acquire+release（用于把新增成本归因到"锁"本身）"""
    from agent.utils.cross_process_lock import CrossProcessLock
    base = tempfile.mkdtemp(prefix="bench_lockonly_")
    lock = CrossProcessLock(str(Path(base) / "x.lock"), name="bench")

    def one(_i: int) -> None:
        lock.acquire(2.0)
        lock.release()

    return _measure(n, str(Path(base) / "x.lock"), one)


def main() -> int:
    parser = argparse.ArgumentParser(description="事件追加写延迟基准（TASK-S8-02）")
    parser.add_argument("--n", type=int, default=_N_DEFAULT)
    args = parser.parse_args()
    n = max(1, int(args.n))

    print("=" * 118)
    print(f"事件追加写延迟基准（时钟源: time.perf_counter，单位: 微秒/次，N={n}，轮转交错采样）")
    print("=" * 118)
    results = {name: _report(name, s)
               for name, s in bench_interleaved(n, _make_variants()).items()}
    results["lock_only"] = _report("lock_only", bench_lock_only(n))

    base = results["legacy"]
    print("-" * 118)
    for name in ("lock_off", "lock_on"):
        row = results[name]
        print(f"{name} vs legacy: "
              f"p50 {base['p50_us']:.2f}→{row['p50_us']:.2f}us "
              f"(×{row['p50_us'] / max(base['p50_us'], 1e-9):.2f}, "
              f"Δ{row['p50_us'] - base['p50_us']:+.2f}us) | "
              f"p99 {base['p99_us']:.2f}→{row['p99_us']:.2f}us "
              f"(×{row['p99_us'] / max(base['p99_us'], 1e-9):.2f}, "
              f"Δ{row['p99_us'] - base['p99_us']:+.2f}us)")
    print(f"锁原语本体（acquire+release，进程内无竞争）p50={results['lock_only']['p50_us']:.2f}us "
          f"p99={results['lock_only']['p99_us']:.2f}us —— 这就是「锁」在追加路径上的理论增量上界")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
