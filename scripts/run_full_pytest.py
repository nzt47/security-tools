"""全量 pytest 分块并行执行脚本

背景：TraeCode 后台任务有约 20 分钟超时，而云枢全量测试套件运行需 25+ 分钟。
本脚本将收集到的测试文件按需切分为 N 块，用 N 个 worker 进程并行执行，
每块独立日志（pytest_chunks/chunk_i.log），规避单任务超时。

用法：
    python scripts/run_full_pytest.py [chunks] [workers]
    chunks  切分数（默认 4）
    workers 并行进程数（默认 4）

退出码：任一 chunk 失败则返回 1，全部通过返回 0。
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


def run_chunk(files: list[str], idx: int, out: str) -> tuple[int, int, str]:
    """执行单块测试，输出到独立日志文件"""
    cmd = [
        sys.executable, "-m", "pytest", *files,
        "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line",
    ]
    with open(out, "w", encoding="utf-8") as f:
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT)
    return idx, rc, out


def main() -> int:
    chunks_n = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    workers = int(sys.argv[2]) if len(sys.argv) > 2 else 4

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
    print(f"共收集 {len(files)} 个测试文件，切分为 {chunks_n} 块，{workers} 个 worker")
    chunks = [files[i::chunks_n] for i in range(chunks_n)]
    chunks = [c for c in chunks if c]

    logdir = ROOT / "pytest_chunks"
    logdir.mkdir(exist_ok=True)

    results: list[tuple[int, int, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(run_chunk, chunk, i, str(logdir / f"chunk_{i}.log"))
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
