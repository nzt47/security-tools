#!/usr/bin/env python3
"""模拟 LinkCache 在极端高并发下的引用泄漏，验证监控能否及时发现。

【名词澄清】LinkCache 是纯内存缓存（无连接池），此处「连接泄漏」指与连接池
泄漏同症状的对象引用泄漏：高并发下不断创建新 LinkCache 实例并持有引用
（防 GC 回收），导致进程 RSS 持续单调上升——正是外部模式监控的检测对象。

双模式:
  --leak   泄漏进程模式：持续构建新 LinkCache 并累积持有，RSS 单调上涨。
           （每轮 --leak-cards 张卡 × --links-per-card 条链，默认 ≈1.2MB/轮）
  --verify 端到端验证模式：
           1. 启动 --leak 子进程（模拟高并发泄漏源头）
           2. 等待泄漏发生（RSS 上涨）
           3. 调用监控脚本外部模式（--pid <子进程> --interval 0.2，跑循环
              直到子进程结束触发 NoSuchProcess）
           4. 断言: 退出码 1 + stderr 含 ALERT（监控及时发现）
           5. 清理子进程

用法:
  # 手动观察泄漏:
  python scripts/simulate_link_cache_leak.py --leak --rounds 20 --interval 0.1
  # 端到端验证监控:
  python scripts/simulate_link_cache_leak.py --verify [--leak-cards 2000]

退出码: 0 监控及时发现泄漏 / 1 验证失败

【不易】验证走监控脚本真实外部入口（--pid + psutil RSS 采样），
不绕过退出码契约：监控未告警即判失败。
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (PROJECT_ROOT / "scripts", PROJECT_ROOT / "packages" / "yunshu_cache_tools" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

logger = logging.getLogger("simulate_link_cache_leak")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)

# 泄漏进程全局持有引用（防 GC）：模拟泄漏的"不释放"本质
_LEAKED: List[object] = []


def _rss_mb() -> float:
    """当前进程 RSS（MB），零依赖跨平台（psutil 兜底 / 标准库）。"""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        pass
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        pmc = PROCESS_MEMORY_COUNTERS()
        pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return pmc.WorkingSetSize / 1024 / 1024
        return 0.0
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux: KB→MB


def run_leak(rounds: int, interval: float, leak_cards: int, links_per_card: int) -> int:
    """泄漏进程主体：每轮构建新 LinkCache 并持有，RSS 单调上涨。"""
    from types import SimpleNamespace
    from yunshu_cache_tools import LinkCache

    rss0 = _rss_mb()
    logger.info("leak: 基线 RSS=%.1fMB，每轮 %d 卡×%d 链，共 %d 轮", rss0, leak_cards, links_per_card, rounds)
    for i in range(1, rounds + 1):
        store = {}
        for c in range(leak_cards):
            store[f"card{c}"] = SimpleNamespace(
                slug=f"card{c}",
                links=[f"card{(c + j) % leak_cards}" for j in range(1, links_per_card + 1)],
            )
        # 构建后持有引用（泄漏点）：缓存对象不释放，等效连接池句柄泄漏
        _LEAKED.append(LinkCache(store))
        if i % 5 == 0 or i == rounds:
            logger.info("leak: 第 %d 轮 RSS=%.1fMB 增量=%.1fMB", i, _rss_mb(), _rss_mb() - rss0)
        time.sleep(interval)
    logger.info("leak: 泄漏模拟完成，累计持有实例=%d 总增量=%.1fMB", len(_LEAKED), _rss_mb() - rss0)
    return 0


def run_verify(leak_cards: int, links_per_card: int, rounds: int, interval: float,
               threshold_mb: float, monitor_interval: float) -> int:
    """端到端验证：泄漏子进程 + 监控脚本外部模式，断言监控及时发现。"""
    child = subprocess.Popen(
        [sys.executable, str(__file__), "--leak",
         "--rounds", str(rounds), "--interval", str(interval),
         "--leak-cards", str(leak_cards), "--links-per-card", str(links_per_card)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    logger.info("verify: 泄漏子进程 pid=%d（约 %.1fMB/%.2fs 增速）",
                child.pid, leak_cards * links_per_card * 0.00012, interval)

    # 让泄漏先发生，确保 RSS 已上涨（监控基线建立后再持续增长）
    time.sleep(1.0)
    if child.poll() is not None:
        out, err = child.communicate()
        logger.error("verify: 泄漏子进程提前退出 rc=%d\n%s", child.returncode, err[-2000:])
        return 1

    proc = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "monitor_link_cache_memory.py"),
         "--pid", str(child.pid), "--interval", str(monitor_interval),
         "--threshold-mb", str(threshold_mb)],
        capture_output=True, text=True, timeout=120,
    )
    child.communicate()  # 回收子进程输出（子进程应已自行结束）
    rc, stderr = proc.returncode, proc.stderr

    issues: list[str] = []
    if rc != 1:
        issues.append(f"监控未告警: 退出码={rc}（期望 1，说明泄漏未被及时发现）")
    if "ALERT" not in stderr:
        issues.append("监控日志缺少 ALERT 标记")
    logger.info("verify: 监控退出码=%d（1=发现泄漏）", rc)

    report = {
        "verdict": "监控及时发现泄漏" if not issues else "验证失败",
        "leak_pid": child.pid,
        "leak_rounds": rounds,
        "leak_cards_per_round": leak_cards,
        "threshold_mb": threshold_mb,
        "monitor_rc": rc,
        "alerted": rc == 1 and "ALERT" in stderr,
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    for issue in issues:
        logger.error(" - %s", issue)
    return 0 if not issues else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkCache 引用泄漏模拟与监控验证")
    parser.add_argument("--leak", action="store_true", help="泄漏进程模式")
    parser.add_argument("--verify", action="store_true", help="端到端验证模式")
    parser.add_argument("--rounds", type=int, default=40, help="泄漏轮数")
    parser.add_argument("--interval", type=float, default=0.05, help="泄漏轮间隔秒")
    parser.add_argument("--leak-cards", type=int, default=2000, help="每轮卡数（≈1.2MB/轮）")
    parser.add_argument("--links-per-card", type=int, default=5, help="每卡链接数")
    parser.add_argument("--threshold-mb", type=float, default=2.0, help="监控 RSS 增量阈值 MB")
    parser.add_argument("--monitor-interval", type=float, default=0.2, help="监控采样间隔秒")
    args = parser.parse_args()

    if args.leak:
        return run_leak(args.rounds, args.interval, args.leak_cards, args.links_per_card)
    if args.verify:
        return run_verify(args.leak_cards, args.links_per_card, args.rounds, args.interval,
                          args.threshold_mb, args.monitor_interval)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
