"""rerank 并发控制工具 —— 线程池容量调整建议的落地模块

依据: docs/observability/threadpool_capacity_adjustment.md 第 5 节

【背景】
当前 reranker._predict_with_timeout 每次调用新建 ThreadPoolExecutor(max_workers=1) 池，
无全局并发限制 —— 并发 N 个 rerank 请求 → N 个 predict 线程同时推理 →
GIL/CPU 竞争放大（真实 ONNX 实测: 并发 8 下 P99 由 258ms 放大至 546ms）。

【参数计算】
安全约束: P99_wall(C) ≤ 超时阈值 × 50% = 3.0s × 0.5 = 1500ms
保守 GIL 上界: C_max = ⌊1500 / 258⌋ ≈ 5.8 → 默认 5

【设计】
- concurrency_slot(timeout) 上下文管理器: 信号量 acquire(timeout) 成功 → True，
  失败 → False（不抛错，与软超时降级语义一致），退出时仅成功路径 release
- SKILL_RERANKER_MAX_CONCURRENCY env 可配（默认 5），支持运行时热重载

用法（接入 reranker.py 的 _predict_with_timeout 入口）:
    from agent.skills_mgmt.reranker_utils import concurrency_slot
    with concurrency_slot(self._rerank_timeout) as acquired:
        if not acquired:
            return [0.0] * len(pairs)   # 排队超时 → 调用方按降级语义处理
        # ... 原有 predict + soft timeout 逻辑
"""

import logging
import os
import threading
from contextlib import contextmanager
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

# 保守 GIL 上界（ONNX quantized 单请求 P99≈258ms, 阈值 3.0s, 裕度 50%）
_DEFAULT_MAX_CONCURRENCY = 5
_MAX_CONCURRENCY_ENV = "SKILL_RERANKER_MAX_CONCURRENCY"

# 模块级信号量（进程内共享，全局限制 rerank 并发）
_sem: Optional[threading.Semaphore] = None
_sem_capacity: int = -1


def get_max_concurrency() -> int:
    """读取并发上限：env > 默认 5（每次调用实时读，支持 env 热重载）"""
    raw = os.environ.get(_MAX_CONCURRENCY_ENV, "")
    if not raw:
        return _DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning("SKILL_RERANKER_MAX_CONCURRENCY 解析失败: %r，回退默认 %d",
                       raw, _DEFAULT_MAX_CONCURRENCY)
        return _DEFAULT_MAX_CONCURRENCY
    return max(1, value)  # 下限 1（至少允许串行执行）


def _ensure_semaphore() -> threading.Semaphore:
    """按当前 env 构建信号量（容量变化时重建，保证热重载生效）

    Why: threading.Semaphore 容量不可变，env 调整后需重建才能生效。
    """
    global _sem, _sem_capacity
    capacity = get_max_concurrency()
    if _sem is None or _sem_capacity != capacity:
        _sem = threading.Semaphore(capacity)
        _sem_capacity = capacity
        logger.debug("rerank 并发闸门重建: max_concurrency=%d", capacity)
    return _sem


def set_max_concurrency(value: int) -> None:
    """运行时调整并发上限（调试/压测用），0 以下重置为 env/默认"""
    if value <= 0:
        os.environ.pop(_MAX_CONCURRENCY_ENV, None)
    else:
        os.environ[_MAX_CONCURRENCY_ENV] = str(value)
    _ensure_semaphore()


@contextmanager
def concurrency_slot(timeout: float) -> Iterator[bool]:
    """rerank 并发闸门上下文管理器

    Args:
        timeout: acquire 等待上限（秒），建议传超时阈值本身

    Yields:
        acquired: True=已获得并发额度（退出时自动释放）；False=排队超时（调用方降级）

    简易说明:
        - 排队超时不抛错 → 调用方走降级分支（与软超时语义一致，不阻塞主流程）
        - 仅成功获得额度时 release，避免未持有就释放 ValueError
    """
    sem = _ensure_semaphore()
    acquired = sem.acquire(timeout=timeout)
    try:
        yield acquired
    finally:
        if acquired:
            sem.release()
