#!/usr/bin/env python3
"""simulate_ci_run.py — 模拟 CI 已累积 5 次成功运行，验证 gen_perf_trend.py auto→ci 自动切换

模拟策略:
  在 Python 级 patch 两个函数（不修改 gen_perf_trend.py 源码）:
  1. _query_ci_runs → 返回 5 次成功运行（模拟 gh run list）
  2. _download_run_artifact → 返回 perf_history/run_N.json 数据（模拟 gh api artifact 下载）

验证点:
  - auto 模式探测到 5 次成功运行 → 切换到 ci
  - ci 模式"下载" artifact → 解析 → 生成图表
  - 图表数据来自真实 perf_history 文件（非编造）

运行:
  python scripts/perf_history/simulate_ci_run.py
  python scripts/perf_history/simulate_ci_run.py --runs 5
"""
import os
import sys
import json
from pathlib import Path
from unittest.mock import patch

# 加入项目根
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

HISTORY_DIR = Path(__file__).resolve().parent


def _mock_query_ci_runs(limit, repo):
    """模拟 gh run list: 返回 5 次成功运行"""
    print("  [mock] _query_ci_runs(limit=%d, repo=%s) → 5 次成功运行" % (limit, repo))
    runs = []
    for i in range(1, 6):
        runs.append({
            "databaseId": 10000 + i,
            "status": "completed",
            "conclusion": "success",
        })
    return runs


def _mock_download_artifact(run_id, repo):
    """模拟 gh api artifact 下载: 返回 perf_history/run_N.json 数据

    将 databaseId (10001~10005) 映射到 run_1.json~run_5.json
    """
    index = run_id - 10000 if run_id > 10000 else 1
    history_file = HISTORY_DIR / ("run_%d.json" % index)
    if not history_file.exists():
        print("  [mock] ⚠️ %s 不存在，回退 run_1.json" % history_file.name)
        history_file = HISTORY_DIR / "run_1.json"

    with open(history_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    print("  [mock] _download_run_artifact(run_id=%d) → %s (SQLite P99=%.3fms, etcd P99=%.3fms)" % (
        run_id, history_file.name,
        data.get("sqlite", {}).get("p99", 0),
        data.get("etcd", {}).get("p99", 0)))
    return data


def main():
    # 解析简单参数
    runs = 5
    if "--runs" in sys.argv:
        idx = sys.argv.index("--runs")
        if idx + 1 < len(sys.argv):
            runs = int(sys.argv[idx + 1])

    print("=" * 60)
    print(" 模拟 CI 环境验证: gen_perf_trend.py auto → ci 自动切换")
    print("=" * 60)
    print(" 模拟数据: %d 次成功 CI 运行（数据来自 perf_history/run_*.json）" % runs)
    print()

    # 注入 mock 并调用 gen_perf_trend.py main()
    from scripts import gen_perf_trend

    # 模拟 CLI 参数
    sys.argv = ["gen_perf_trend.py", "--source", "auto", "--runs", str(runs)]

    with patch("scripts.gen_perf_trend._query_ci_runs", side_effect=_mock_query_ci_runs), \
         patch("scripts.gen_perf_trend._download_run_artifact", side_effect=_mock_download_artifact):
        exit_code = gen_perf_trend.main()

    print()
    if exit_code == 0:
        print("=" * 60)
        print(" ✅ 验证通过: auto 模式成功切换到 ci 并生成趋势图")
        print("=" * 60)
    else:
        print("=" * 60)
        print(" ❌ 验证失败: exit_code=%d" % exit_code)
        print("=" * 60)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
