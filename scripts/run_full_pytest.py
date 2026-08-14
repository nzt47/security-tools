"""全量 pytest 分块并行执行脚本

背景：TraeCode 后台任务有约 20 分钟超时，而云枢全量测试套件运行需 25+ 分钟。
本脚本将收集到的测试文件按需切分为 N 块，用 N 个 worker 进程并行执行，
每块独立日志（pytest_chunks/chunk_i.log），规避单任务超时。

用法：
    python scripts/run_full_pytest.py [chunks] [workers] [mode]
    chunks  切分数（默认 4）
    workers 并行进程数（默认 4）
    mode    fast | slow | all（默认 fast）
            fast: 排除 @pytest.mark.slow（D 类环境性慢测试），分块稳定执行（推荐回归入口）
            slow: 仅跑 @pytest.mark.slow（单块，容忍慢路径，作为 D 类监控）
            all:  不过滤（与旧行为一致）

【P1 A3】D 类 slow 分流背景（2026-08-14 实测）：
- generate_weekly_report → pydantic_settings/importlib 慢扫描、task_scheduler 系列、e2e 热更
  t.join() 在分块进程中 >60s，thread 超时无法中断 → 进程被 pytest-timeout 强制终止（rc=1 无汇总）。
- fast 模式排除后 chunk 可稳定完成；slow 模式单独运行容忍慢路径。

退出码：任一 chunk 失败则返回 1，全部通过返回 0（rc=5 no-tests-ran 视为通过）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

# 与 pytest.ini addopts 的 --ignore 保持一致（保持 --continue-on-collection-errors 语义）
IGNORES = [
    "tests/benchmark",
    "memory/tests",
    "cognitive/test_cognitive",
    "tests/performance",
    "tests/stress",
    "tests/e2e",
    "tests/integration/check_targets.py",
    "tests/integration/check_baseline.py",
    "tests/integration/check_5xx_source.py",
    "tests/unit/temp",
    "tests/test_digital_life.py",
    "tests/unit/test_utils_index_manager.py",
]

# 【P1 A3】mode → pytest -m 过滤参数（None = 不过滤）
MODE_MARKER = {"fast": "not slow", "slow": "slow", "all": None}
# slow 模式附加 --runslow + 更长超时（300s）：激活 --runslow 门控用例并容忍
# D 类 os.stat/importlib 慢路径（实测 >60s，2026-08-14），超时标记但不过早强杀
MODE_EXTRA = {"fast": [], "slow": ["--runslow", "--timeout=300"], "all": []}


def collect() -> list[str]:
    """收集测试文件（与 pytest.ini 忽略规则对齐）"""
    ignore_prefixes = []
    for ig in IGNORES:
        p = Path(ig)
        ignore_prefixes.append(str(p).replace("\\", "/") + "/")
        ignore_prefixes.append(str(p).replace("\\", "/"))
    files = []
    for path in sorted(Path("tests").rglob("test_*.py")):
        rel = str(path).replace("\\", "/")
        if any(rel.startswith(prefix) for prefix in ignore_prefixes if prefix.endswith("/")):
            continue
        if any(rel == prefix for prefix in ignore_prefixes if not prefix.endswith("/")):
            continue
        files.append(rel)
    return files


def run_chunk(files: list[str], idx: int, out: str, marker: str | None,
              extra: list[str] | None = None) -> tuple[int, int, str]:
    """执行单块测试，输出到独立日志文件"""
    cmd = [
        sys.executable, "-m", "pytest", *files,
        "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line",
    ]
    if marker:
        cmd += ["-m", marker]
    cmd += (extra or [])
    with open(out, "w", encoding="utf-8") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
    # rc=5 = "no tests ran"（分块后该 chunk 恰好无匹配用例），不视为失败
    if rc == 5:
        rc = 0
    return idx, rc, out


def main() -> int:
    chunks_n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    mode = sys.argv[3] if len(sys.argv) > 3 else "fast"
    if mode not in MODE_MARKER:
        print(f"[run_full_pytest] 非法 mode={mode!r}，可选 fast/slow/all", file=sys.stderr)
        return 1
    marker = MODE_MARKER[mode]
    extra = MODE_EXTRA[mode]

    # 【P0 T-11】回归前工作区检查（T-18 落地）：
    # 默认提示模式（并行会话常态脏工作区下仍可回归）；REGRESSION_REQUIRE_CLEAN=1
    # 或 --strict 时非空即阻断，防止未提交/未跟踪改动污染回归判定。
    # 豁免 pytest_chunks/（本脚本自身产物目录）。
    guard = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "guard_workspace_clean.py"),
         "--repo-root", str(ROOT), "--allow", "pytest_chunks/**"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60,
    )
    out = (guard.stdout + guard.stderr).strip()
    if out:
        print(out)
    if guard.returncode != 0:
        print("[run_full_pytest] 脏工作区阻断：请先提交改动或隔离到独立 worktree", file=sys.stderr)
        return 1

    files = collect()
    if not files:
        print("未收集到任何测试文件", file=sys.stderr)
        return 1
    print(f"共收集 {len(files)} 个测试文件（mode={mode}, marker={marker or '无'}）")
    if mode == "slow":
        # 【P1 A3】slow 模式单块直跑全部文件（-m slow 过滤），不分块（分布不均无意义）
        chunks = [files]
        print(f"slow 模式：仅跑 @pytest.mark.slow（单块）")
    else:
        chunks = [files[i::chunks_n] for i in range(chunks_n)]
        chunks = [c for c in chunks if c]
        print(f"切分为 {len(chunks)} 块，{workers} 个 worker")

    logdir = ROOT / "pytest_chunks"
    logdir.mkdir(exist_ok=True)

    results: list[tuple[int, int, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(run_chunk, chunk, i, str(logdir / f"chunk_{i}.log"), marker, extra)
            for i, chunk in enumerate(chunks)
        ]
        for fu in futures:
            results.append(fu.result())

    overall_rc = 0
    for idx, rc, out in sorted(results):
        tail = ""
        try:
            tail = "\n".join(
                open(out, encoding="utf-8", errors="replace").read().strip().splitlines()[-3:]
            )
        except OSError:
            pass
        print(f"[chunk {idx}] rc={rc}")
        if tail:
            print(f"  {tail}")
        if rc != 0:
            overall_rc = 1
    print(f"== 总体结果: {'PASS' if overall_rc == 0 else 'FAIL'} ==")
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
