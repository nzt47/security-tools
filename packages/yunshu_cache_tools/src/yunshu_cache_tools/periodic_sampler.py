"""周期采样器：按调用次数确定性抽样（多线程安全）。

本文件是独立 PyPI 包 yunshu-cache-tools 的实现，与 agent 内部实现保持
行为等价（由 tests/unit/test_cache_tools_package_parity.py 锁一致），
零第三方依赖。

与 trace_id 哈希概率采样（agent/monitoring/tracing_sampling.py）定位不同：
本采样器按「调用次数」周期抽样，无随机性、结果可复现，适合耗时日志/埋点等
需要稳定样本比例的生产降噪场景。
"""

from __future__ import annotations

import itertools


class PeriodicSampler:
    """周期计数采样器。

    rate=1.0 全量输出；0<rate<1 时每 round(1/rate) 次调用输出 1 条
    （rate=0.1 → 每 10 次 1 条；rate=0.3 → 每 3 次 1 条 ≈ 33%）。
    should_sample 由 itertools.count 驱动，next 为 C 原子操作，
    多线程并发调用无竞态（无需额外加锁）。
    """

    def __init__(self, rate: float = 0.1) -> None:
        self.rate = max(1e-6, min(1.0, float(rate)))
        self._period = max(1, round(1.0 / self.rate))
        self._counter = itertools.count()

    def should_sample(self) -> bool:
        """本轮调用是否采样（线程安全）。"""
        return (next(self._counter) % self._period) == 0

    @property
    def period(self) -> int:
        """采样周期（每 period 次调用输出 1 条）。"""
        return self._period


__all__ = ["PeriodicSampler"]
