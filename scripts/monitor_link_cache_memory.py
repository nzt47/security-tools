#!/usr/bin/env python3
"""LinkCache 内存监控与告警脚本（生产运维用）。

两种监控模式：
1. 进程内估算法（默认）：加载知识库 → 构建 LinkCache → 递归深度估算缓存
   独占内存 → 与阈值比较。精确衡量「LinkCache 自身内存使用率」。
2. 外部实测法（--pid）：周期采样目标服务进程 RSS 相对基线增量 → 与阈值
   比较（进程内包含 LinkCache + 索引等全部内存，阈值建议放宽）。

用法：
    # 进程内估算法（内置 mock 数据集演示，单次）
    python scripts/monitor_link_cache_memory.py --once
    # 进程内估算法（真实知识库，每 60s 采样）
    python scripts/monitor_link_cache_memory.py --cards-dir knowledge/wiki --interval 60
    # 外部实测法（监控服务进程 RSS 增量，阈值 256MB）
    python scripts/monitor_link_cache_memory.py --pid 12345 --threshold-mb 256 --interval 60
    # 结构化输出（供 Prometheus 文本采集 / 告警系统解析）
    python scripts/monitor_link_cache_memory.py --once --json

退出码：0 正常 / 1 内存超阈值（触发告警）/ 2 参数或运行错误。

【不易】阈值即告警契约：超阈值必须输出告警并返回非零退出码，不得静默吞掉。
【变易】双模式 + 参数化，支持单次/周期/外部进程监控三种运维场景。
【简易】独立进程，仅依赖标准库 + psutil（外部模式），不侵入服务代码。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# 仓库内直接运行时定位项目根与独立包源码（pip 安装后模块已在 sys.path，insert 幂等）。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
_PKG_SRC = _PROJECT_ROOT / "packages" / "yunshu_cache_tools" / "src"
if str(_PKG_SRC) not in sys.path:
    sys.path.insert(0, str(_PKG_SRC))

logger = logging.getLogger("monitor_link_cache_memory")
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

# 内存估算模型（64 位 CPython 保守上界，与包文档/部署手册一致）。
BYTES_PER_CARD = 120      # dict 条目 + list 头等结构开销
BYTES_PER_LINK = 120      # tuple(56B) + 目标字符串（保守含全量）


def estimate_deep_size(obj: Any, seen: Optional[set] = None) -> int:
    """递归估算对象深度内存占用（字节），共享引用不重复计数。

    覆盖 dict / list / tuple / set / frozenset 嵌套结构与字符串；
    其他对象回退到 sys.getsizeof。估算为保守上界：结构指针 + 对象头。
    """
    if seen is None:
        seen = set()
    oid = id(obj)
    if oid in seen:
        return 0
    seen.add(oid)
    total = sys.getsizeof(obj)
    if isinstance(obj, dict):
        total += sum(
            estimate_deep_size(k, seen) + estimate_deep_size(v, seen)
            for k, v in obj.items()
        )
    elif isinstance(obj, (list, tuple, set, frozenset)):
        total += sum(estimate_deep_size(i, seen) for i in obj)
    return total


def model_estimate(cache: Any) -> int:
    """按理论模型估算（不遍历对象，仅用 size 计数）。"""
    return cache.size * BYTES_PER_CARD + cache.total_links * BYTES_PER_LINK


def load_cards(cards_dir: Optional[str]) -> dict:
    """加载卡片快照：--cards-dir 指向知识库 wiki 根目录；缺省用内置 mock 数据集。"""
    if cards_dir:
        from agent.knowledge import CardStore

        store = CardStore(cards_dir)
        cards_list = store.list()
    else:
        from types import SimpleNamespace

        # mock 数据集：演示估算流程（正常链 / 断链 / 归档全覆盖）。
        cards_list = [
            SimpleNamespace(slug=f"卡{i}", links=[f"卡{(i + 1) % 8}", "ghost", "archives/old"] + [f"卡{(i + j) % 8}" for j in range(2, 5)])
            for i in range(8)
        ]
    return {c.slug: c for c in cards_list}


def in_process_check(cards_dir: Optional[str], threshold_mb: float) -> Dict[str, Any]:
    """进程内估算法：构建缓存 → 深估算 → 阈值判定。"""
    cards = load_cards(cards_dir)
    from yunshu_cache_tools import LinkCache

    cache = LinkCache(cards)
    deep_bytes = estimate_deep_size(cache._cache)
    model_bytes = model_estimate(cache)
    # 深估算更精确；模型估算作对照（二者应在同一数量级）。
    used_bytes = max(deep_bytes, model_bytes)
    used_mb = used_bytes / (1024 * 1024)
    threshold_bytes = threshold_mb * 1024 * 1024
    alarmed = used_bytes > threshold_bytes
    return {
        "mode": "in_process",
        "cards": cache.size,
        "total_links": cache.total_links,
        "used_mb": round(used_mb, 4),
        "deep_mb": round(deep_bytes / (1024 * 1024), 4),
        "model_mb": round(model_bytes / (1024 * 1024), 4),
        "threshold_mb": threshold_mb,
        "alarmed": alarmed,
    }


def external_check(pid: int, threshold_mb: float, interval: float, once: bool) -> int:
    """外部实测法：周期采样目标进程 RSS 增量。"""
    try:
        import psutil
    except ImportError as exc:
        logger.error("外部模式需要 psutil: %s", exc)
        return 2

    try:
        proc = psutil.Process(pid)
        baseline = proc.memory_info().rss
    except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
        logger.error("无法访问目标进程 pid=%d: %s", pid, exc)
        return 2

    threshold_bytes = threshold_mb * 1024 * 1024
    alarmed = False
    first = True
    try:
        while True:
            rss = proc.memory_info().rss
            delta_bytes = rss - baseline
            exceeded = delta_bytes > threshold_bytes
            alarmed = alarmed or exceeded
            record = {
                "mode": "external",
                "pid": pid,
                "rss_mb": round(rss / (1024 * 1024), 2),
                "baseline_mb": round(baseline / (1024 * 1024), 2),
                "delta_mb": round(delta_bytes / (1024 * 1024), 2),
                "threshold_mb": threshold_mb,
                "alarmed": exceeded,
            }
            if exceeded:
                logger.error("ALERT LinkCache/进程内存增量超阈值: %s", json.dumps(record, ensure_ascii=False))
            else:
                logger.info("ok: %s", json.dumps(record, ensure_ascii=False))
            if first:
                logger.info("监控基线已建立（RSS=%d MB），阈值=%.1f MB 增量", baseline // (1024 * 1024), threshold_mb)
                first = False
            if once:
                break
            time.sleep(interval)
    except (psutil.NoSuchProcess, KeyboardInterrupt):
        pass
    return 1 if alarmed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkCache 内存监控与告警")
    parser.add_argument("--cards-dir", default=None, help="知识库 wiki 根目录（进程内模式数据源）")
    parser.add_argument("--pid", type=int, default=None, help="目标服务进程 PID（外部实测模式）")
    parser.add_argument("--threshold-mb", type=float, default=256.0, help="告警阈值 MB（默认 256）")
    parser.add_argument("--interval", type=float, default=60.0, help="采样周期秒（默认 60）")
    parser.add_argument("--once", action="store_true", help="单次采样后退出（便于 CI/自测）")
    parser.add_argument("--json", action="store_true", help="最终结果输出 JSON 到 stdout（供采集器解析）")
    args = parser.parse_args()

    try:
        if args.pid is not None:
            rc = external_check(args.pid, args.threshold_mb, args.interval, args.once)
        elif args.once:
            result = in_process_check(args.cards_dir, args.threshold_mb)
            rc = 1 if result["alarmed"] else 0
            if args.json:
                print(json.dumps(result, ensure_ascii=False))
            elif result["alarmed"]:
                logger.error("ALERT LinkCache 内存超阈值: %s", json.dumps(result, ensure_ascii=False))
            else:
                logger.info(
                    "ok: 卡片=%d links=%d 缓存内存=%.2fMB 阈值=%.1fMB",
                    result["cards"], result["total_links"], result["used_mb"], result["threshold_mb"],
                )
        else:
            # 周期模式（进程内）：常驻采样，Ctrl+C 退出。
            result = None
            while True:
                result = in_process_check(args.cards_dir, args.threshold_mb)
                if result["alarmed"]:
                    rc = 1
                    logger.error("ALERT LinkCache 内存超阈值: %s", json.dumps(result, ensure_ascii=False))
                else:
                    logger.info(
                        "ok: 卡片=%d links=%d 缓存内存=%.2fMB 阈值=%.1fMB",
                        result["cards"], result["total_links"], result["used_mb"], result["threshold_mb"],
                    )
                time.sleep(args.interval)
            if args.json and result is not None:
                print(json.dumps(result, ensure_ascii=False))
    except KeyboardInterrupt:
        logger.info("监控已停止")
        return 0
    except Exception as exc:  # 参数/加载错误：退出码 2，不误报为超阈值
        logger.error("监控执行失败: %r", exc)
        return 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
