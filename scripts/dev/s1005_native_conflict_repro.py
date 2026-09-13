#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S10-05 最小复现 / 归因探针：`tests/integration` 单进程跑不完的原生冲突

背景（Why）:
    单进程跑 `pytest tests/integration` 全量时，进程跑到 20% 就被系统直接终止
    （退出码 -1073741819 = 0xC0000005 ACCESS_VIOLATION），**没有 pytest 汇总行**，
    因此拿不到 FAILED 列表 —— 这就是「约 80% 用例未验证」的真面目。

最小复现（2026-09-13 实测，未打 S10-05 导入顺序修复时 3/3 必崩）:
    python -m pytest tests/integration/test_adapters_integration.py \\
                     tests/integration/test_agent_integration.py \\
                     tests/integration/test_digital_life_integration.py \\
        -q -p no:randomly --timeout=900
    → rc = 3221225477 / -1073741819（0xC0000005），约 23s 内复现。

    对照组（同一台机、同一时刻）:
        {adapters, digital_life}  → 通过（23.9s）
        {agent,    digital_life}  → 通过（26.5s）
    即 **两个前置文件必须同时存在** 才会崩：触发条件是「两个文件联合建立的进程级
    原生状态」，而不是某一个文件或某一个可导入模块（另有 15 组纯导入顺序组合实测
    均未复现，见结案报告 §4）。

崩溃点（代码行级，来自 faulthandler 当前线程栈 + Windows 应用程序错误日志）:
    agent/orchestrator/lifecycle_manager.py:118  import sentence_transformers
      → sentence_transformers/util/__init__.py:26
      → sentence_transformers/util/retrieval.py:14
      → sentence_transformers/util/similarity.py:9   import sklearn
      → sklearn/utils/fixes.py:19                    import pandas
      → pandas/compat/__init__.py:28
      → pandas/compat/pyarrow.py:12                  import pyarrow
      → pyarrow/__init__.py:71                       from pyarrow.lib import ...
      → 加载 lib.cp312-win_amd64.pyd → arrow.dll 原生初始化 ⇒ 0xC0000005
    事件日志：错误模块 = site-packages\\pyarrow\\arrow.dll，异常代码 = 0xc0000005。

用法:
    python scripts/dev/s1005_native_conflict_repro.py            # 跑最小复现 + 两个对照组
    python scripts/dev/s1005_native_conflict_repro.py --quiet     # 只跑最小复现

判定约定（重要）:
    rc ∈ {3221225477, -1073741819} → 原生崩溃；rc == 0 → 通过。
    本脚本**不**修改任何测试语义，只负责发起子进程并对比退出码。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CRASH_RCS = {3221225477, -1073741819}  # 0xC0000005 的无符号 / 有符号表示
I = "tests/integration"
ADAPTERS = f"{I}/test_adapters_integration.py"
AGENT = f"{I}/test_agent_integration.py"
DIGITAL_LIFE = f"{I}/test_digital_life_integration.py"

CASES: list[tuple[str, list[str]]] = [
    ("最小复现: adapters + agent + digital_life", [ADAPTERS, AGENT, DIGITAL_LIFE]),
    ("对照组: adapters + digital_life", [ADAPTERS, DIGITAL_LIFE]),
    ("对照组: agent + digital_life", [AGENT, DIGITAL_LIFE]),
]


def run_case(files: list[str], timeout: int) -> tuple[int, float]:
    cmd = [sys.executable, "-m", "pytest", *files, "-q", "--tb=line", "-rf",
           "-p", "no:randomly", "-p", "no:cacheprovider", f"--timeout={timeout}"]
    t0 = time.time()
    p = subprocess.run(cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return p.returncode, time.time() - t0


def main() -> int:
    ap = argparse.ArgumentParser(description="S10-05 原生冲突最小复现探针")
    ap.add_argument("--quiet", action="store_true", help="只跑最小复现，跳过对照组")
    ap.add_argument("--timeout", type=int, default=900, help="pytest 单用例超时（秒）")
    args = ap.parse_args()

    cases = CASES[:1] if args.quiet else CASES
    print(f"[S10-05] 仓库根: {REPO_ROOT}")
    print("[S10-05] 判定: rc∈{3221225477,-1073741819} = 原生崩溃(0xC0000005)，rc=0 = 通过\n")
    crashed = 0
    for label, files in cases:
        rc, el = run_case(files, args.timeout)
        if rc in CRASH_RCS:
            verdict, crashed = "CRASH(0xC0000005)", crashed + 1
        elif rc == 0:
            verdict = "PASS"
        else:
            verdict = f"rc={rc}"
        print(f"  [{verdict:16s}] {el:7.1f}s  {label}")
    print()
    if crashed:
        print(f"[结论] 复现 {crashed}/{len(cases)} 例原生崩溃 —— 与 S10-05 结案报告一致。")
        print("[提示] 若已应用 S10-05 修复（tests/integration/conftest.py 的")
        print("       _pin_native_import_order），最小复现应转为 PASS；")
        print("       要复现原始缺陷请先临时撤下该函数。")
    else:
        print("[结论] 未复现原生崩溃。可能原因：已应用 S10-05 导入顺序修复，")
        print("       或环境/依赖版本已变化（记录 python/pyarrow/pandas 版本后再判断）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
