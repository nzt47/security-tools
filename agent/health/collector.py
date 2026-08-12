"""健康采集线程：五层探针 → 加权评分 → 落盘

【不易】契约：
- start_collector() 启动后台 daemon 线程，间隔可配（默认 60s）
- 每轮：run_all_probes() → health_assessor.assess_with_probes() → health_storage.append()
- 单轮失败只告警，不允许中断采集循环
- 幂等：重复调用 start_collector 不会重复启动（_thread 已存在则跳过）

【变易】interval 可注入（测试用）；线程名固定便于日志过滤。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

from agent.health.assessor import health_assessor
from agent.health.probes import run_all_probes
from agent.health.storage import health_storage

COLLECT_INTERVAL = 60  # 默认采集间隔（秒）
_thread: Optional[threading.Thread] = None
_lock = threading.Lock()


def _collect_once() -> dict:
    """单轮采集：探针 → 评分 → 落盘，返回落盘记录"""
    probes = run_all_probes()
    score = health_assessor.assess_with_probes(probes)
    record = {
        "timestamp": score.timestamp,
        "overall": score.overall,
        "dimensions": score.dimensions,
        "issues": score.issues,
        "probe_details": {
            layer: {
                "score": detail["score"],
                "available": detail["available"],
                "detail": detail["detail"],
            }
            for layer, detail in score.probe_details.items()
        },
    }
    health_storage.append(record)
    return record


def _loop(interval: int) -> None:
    while True:
        try:
            _collect_once()
        except Exception as e:  # noqa: BLE001 - 采集异常不中断循环
            logger.error("健康采集异常: %s", e)
        time.sleep(interval)


def start_collector(interval: int = COLLECT_INTERVAL) -> threading.Thread:
    """启动健康采集 daemon 线程（幂等，重复调用返回已存在的线程）"""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(
            target=_loop,
            args=(interval,),
            name="health_collector",
            daemon=True,
        )
        _thread.start()
        logger.info("健康采集线程已启动 (interval=%ss)", interval)
        return _thread
