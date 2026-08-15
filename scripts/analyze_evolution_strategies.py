#!/usr/bin/env python3
"""任务6 策略库膨胀统计：当前库中 active / deprecated 策略分布

运行:
  python scripts/analyze_evolution_strategies.py                  # 默认 data/evolution（auto 检测后端）
  python scripts/analyze_evolution_strategies.py --path <库目录>  # 指定库目录
  python scripts/analyze_evolution_strategies.py --backend sqlite --path <库目录>

背景（【不易】约束）: 策略库"只追加不删除"，旧策略以 deprecated 标记淘汰。
随着运行时间增长，deprecated 占比会持续上升 —— 本脚本监控"数据膨胀"，
给出总数 / active / deprecated / 占比 / scope 分布 / deprecated 明细。
"""

import argparse
import datetime
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.evolution.injector import BACKEND_JSON, BACKEND_SQLITE, StrategyInjector
from agent.evolution.selector import STATUS_ACTIVE, STATUS_DEPRECATED


def _fmt_ts(ts: float) -> str:
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, TypeError):
        return "-"


def analyze(path: str, backend: str) -> int:
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        print(f"[库不存在] {path}")
        return 1
    inj = StrategyInjector(storage_path=path, backend=backend)
    strategies = inj.list_strategies()
    cases = inj.list_cases()

    print("=" * 72)
    print(f"策略库膨胀统计: {path}")
    if inj.backend == BACKEND_SQLITE:
        print(f"存储后端: sqlite（{inj._db_path}）")
    else:
        print(f"存储后端: json（{inj._strategies_path}）")
    print("=" * 72)

    if not strategies:
        print("  策略库为空（0 条）—— 尚无失败案例回流或策略入库")
        print(f"  失败案例: {len(cases)} 条")
        return 0

    total = len(strategies)
    active = [s for s in strategies if s.status == STATUS_ACTIVE]
    deprecated = [s for s in strategies if s.status == STATUS_DEPRECATED]
    others = [s for s in strategies if s.status not in (STATUS_ACTIVE, STATUS_DEPRECATED)]

    print(f"  策略总数  : {total}")
    print(f"  active    : {len(active)}   ({len(active) / total * 100:.1f}%)")
    print(f"  deprecated: {len(deprecated)}   ({len(deprecated) / total * 100:.1f}%)")
    if others:
        print(f"  其他状态  : {len(others)}   ({[s.status for s in others][:5]})")

    dep_ratio = len(deprecated) / total
    print("\n  数据膨胀指标:")
    flag = "（偏高，建议人工评审清理）" if dep_ratio > 0.5 else "（正常范围内）"
    print(f"    deprecated 占比: {dep_ratio * 100:.1f}% {flag}")
    print(f"    失败案例数    : {len(cases)}")

    scope_stat = Counter(s.scope for s in strategies)
    print("\n  按 scope 分布:")
    for scope, n in sorted(scope_stat.items()):
        dep_n = len([s for s in strategies
                     if s.scope == scope and s.status == STATUS_DEPRECATED])
        print(f"    {scope:<32} {n:>3} 条（deprecated {dep_n}）")

    src_stat = Counter(s.source for s in strategies)
    print("\n  按 source 分布:")
    for src, n in sorted(src_stat.items()):
        print(f"    {src:<32} {n:>3} 条")

    print("\n  deprecated 策略明细:")
    if deprecated:
        for s in sorted(deprecated, key=lambda x: x.created_at):
            print(f"    {s.strategy_id}  scope={s.scope:<26} 创建={_fmt_ts(s.created_at)}")
    else:
        print("    （无）")

    return 0


def main() -> int:
    default_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data", "evolution")
    )
    parser = argparse.ArgumentParser(description="策略库膨胀统计（deprecated 占比）")
    parser.add_argument("--path", default=default_path, help="策略库目录（默认 data/evolution）")
    parser.add_argument("--backend", choices=["auto", BACKEND_JSON, BACKEND_SQLITE],
                        default="auto", help="存储后端（auto 按 evolution.db 是否存在自动检测）")
    args = parser.parse_args()

    backend = args.backend
    if backend == "auto":
        db = os.path.join(args.path, "evolution.db")
        backend = BACKEND_SQLITE if os.path.exists(db) else BACKEND_JSON
    return analyze(args.path, backend)


if __name__ == "__main__":
    sys.exit(main())
