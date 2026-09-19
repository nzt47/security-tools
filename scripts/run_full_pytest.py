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

【D2 · 2026-09-19 批次完整性校验 + 丢文件自动补跑】
    每块跑完后**检查日志里是否有 pytest 结束摘要**，而不是只看 rc：
    被 `pytest-timeout` 的 thread 法强杀（`os._exit(1)`）与"有测试失败"都是 rc=1，
    但前者会让该块排在挂起测试之后的文件**全部从未执行**（TASK-03 实测：第 8 块
    63 个文件里 14 个从未执行），且日志无摘要 ⇒ 调用方不知道自己丢了文件。
    检测到未跑完的块后，脚本会：
      ① 落盘 `pytest_chunks/incomplete_files.txt`；② 逐个文件用独立进程**自动补跑**；
      ③ 补跑后仍无摘要的文件 = 真正元凶，写 `pytest_chunks/still_lost_files.txt` 并点名报出。
    设 `RUN_FULL_PYTEST_NO_RESUME=1` 可跳过自动补跑。

【P1 A3】D 类 slow 分流背景（2026-08-14 实测）：
- generate_weekly_report → pydantic_settings/importlib 慢扫描、task_scheduler 系列、e2e 热更
  t.join() 在分块进程中 >60s，thread 超时无法中断 → 进程被 pytest-timeout 强制终止（rc=1 无汇总）。
- fast 模式排除后 chunk 可稳定完成；slow 模式单独运行容忍慢路径。
  （注：正是本脚本 D2 要兜底的那种"无汇总"事故。）

退出码：任一 chunk 失败、或**有文件补跑后仍未正常收尾**则返回 1；全部通过返回 0
        （rc=5 no-tests-ran 视为通过）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)

# Windows GBK 代码页无法编码 emoji 等非 BMP 字符，logging.StreamHandler.emit 会抛
# UnicodeEncodeError 并丢失日志行（chunk_0/2 实测：memory_manager 的 🔁、sqlite_vec_backend 的 ✅）。
# 与项目其他 47 处脚本保持一致；pytest chunk 运行在 ProcessPoolExecutor 子进程，
# spawn 继承本环境变量 → 子进程 stdio 使用 UTF-8，emoji 日志不再丢失。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# 【2026-08-31 污染治理 P1】PYTHONUTF8=1：解释器级 UTF-8 模式。修复 pytest capture
# 读取回放 tmpfile 时把 GBK 字节当 UTF-8 解码的 UnicodeDecodeError（test_extensions /
# test_routes_config 批量下 1 failed + 1 error 根因，调用时设置才生效——conftest 内
# setdefault 对当前进程无效）。子进程 spawn 继承后 pytest 自身也在 UTF-8 模式运行。
os.environ.setdefault("PYTHONUTF8", "1")

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

# 【K9 规避】离线模式环境变量：chromadb→pydantic_settings 导入、sentence_transformers→
# huggingface 网络下载在无网环境会阻塞卡死。注入后改为快速失败/走本地缓存，
# 配合 slow 模式（--runslow --timeout=300）+ 分块实现 K9 "离线变量 + 分块 + 高 timeout" 规避。
K9_OFFLINE_ENV = {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}

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


# ══════════════════════════════════════════════════════════════════════════════
# 【D2 · 2026-09-19 批次完整性校验 + 丢文件自动补跑】
#
# 问题（实测复现见 scripts/repro_timeout_batch_loss.py）：
#   pytest.ini 用 `--timeout-method=thread`。该超时法在超时时走
#   pytest_timeout.py:505 `timeout_timer()` → `finally: os._exit(1)`
#   ⇒ 整个 pytest 进程被杀，同块里**排在挂起测试之后的测试文件一个都不执行**，
#     并且 pytest 来不及写结束摘要 ⇒ 只看 rc 会把"丢了一批文件"误判成"有一批失败"。
#   实测代价（TASK-03，2026-09-19）：第 8 块 63 个文件里 14 个从未执行。
#
# 为什么不能靠 `--timeout-method=signal` 解决：SIGALRM 在 Windows 上不存在，
#   显式传入会 `AttributeError: module 'signal' has no attribute 'SIGALRM'`，
#   pytest 直接 INTERNALERROR、**0 个测试运行**（比 thread 更糟）。
#   本平台（Windows 单机部署）只能用 thread ⇒ 必须在**调用层**兜底。
#
# 处置：① 每块跑完检查日志里有没有 pytest 结束摘要；② 没有 ⇒ 该块文件标记为"从未执行"，
#       落盘清单并**逐文件独立进程补跑**；③ 补跑后仍无摘要的文件 = 真正的元凶，点名报出。
# ══════════════════════════════════════════════════════════════════════════════

#: pytest 结束摘要行特征。例：`1 failed, 1695 passed, 7 skipped in 313.71s`
_SUMMARY_RE = re.compile(r"\d+\s+(passed|failed|error|skipped|xfailed|xpassed|deselected)")
_NO_TESTS_RE = re.compile(r"no tests ran")


def chunk_log_status(log_path: str) -> tuple[bool, str]:
    """判断某块的 pytest 会话是否**正常收尾**（而不是被 os._exit 强杀）。

    Why 不看 rc：被强杀与"有测试失败"都会给出 rc=1，但前者会让该块剩余文件
    全部丢失。只有"日志里有没有结束摘要"能区分这两者。
    """
    p = Path(log_path)
    if not p.exists():
        return False, "日志文件不存在"
    text = p.read_text(encoding="utf-8", errors="replace")
    if _NO_TESTS_RE.search(text):
        return True, "no tests ran（收集为空，会话正常收尾）"
    for line in reversed(text.splitlines()):
        if _SUMMARY_RE.search(line):
            return True, line.strip()
    if "+++ Timeout +++" in text:
        return False, "无结束摘要且含 Timeout 标记 ⇒ 被 pytest-timeout 强杀（os._exit）"
    return False, "无 pytest 结束摘要 ⇒ 疑似被强杀/崩溃"


def run_chunk(files: list[str], idx: int, out: str, marker: str | None,
              extra: list[str] | None = None) -> tuple[int, int, str, bool, str]:
    """执行单块测试，输出到独立日志文件；返回 (idx, rc, out, 是否跑完, 证据)"""
    cmd = [
        sys.executable, "-m", "pytest", *files,
        "-q", "--no-header", "-p", "no:cacheprovider", "--tb=line",
    ]
    if marker:
        cmd += ["-m", marker]
    cmd += (extra or [])
    with open(out, "w", encoding="utf-8") as f:
        # 【K9 规避】合并离线 env（保留既有环境变量，仅新增/覆盖 K9 变量）
        # 【P1】PYTHONUTF8 已在脚本顶部 setdefault，随 os.environ 一并继承
        env = {**os.environ, **K9_OFFLINE_ENV}
        rc = subprocess.call(cmd, stdout=f, stderr=subprocess.STDOUT, env=env)
    # rc=5 = "no tests ran"（分块后该 chunk 恰好无匹配用例），不视为失败
    if rc == 5:
        rc = 0
    completed, detail = chunk_log_status(out)
    return idx, rc, out, completed, detail


def resume_lost_files(files: list[str], logdir: Path, marker: str | None,
                      extra: list[str] | None) -> tuple[list[str], list[tuple[str, int]]]:
    """把"从未执行"的文件逐个用**独立进程**补跑（有界）。

    返回 (仍然跑不完的文件, [(文件, rc)])。逐文件而不是整块重跑的理由：
      - 一个进程只装一个文件 ⇒ 单个文件爆预算只会影响它自己；
      - 能精确定位元凶（否则永远只知道"这块丢了 14 个文件"）。
    """
    still_lost: list[str] = []
    done: list[tuple[str, int]] = []
    rdir = logdir / "resume"
    rdir.mkdir(exist_ok=True)
    for i, f in enumerate(files):
        out = str(rdir / f"resume_{i:03d}_{Path(f).stem}.log")
        cmd = [sys.executable, "-m", "pytest", f, "-q", "--no-header",
               "-p", "no:cacheprovider", "--tb=short"]
        if marker:
            cmd += ["-m", marker]
        cmd += (extra or [])
        with open(out, "w", encoding="utf-8") as fh:
            env = {**os.environ, **K9_OFFLINE_ENV}
            rc = subprocess.call(cmd, stdout=fh, stderr=subprocess.STDOUT, env=env)
        completed, detail = chunk_log_status(out)
        print(f"    [补跑 {i + 1}/{len(files)}] {f} rc={rc} "
              f"{'✔' if completed else '✗ ' + detail}")
        # 文件跑不完，或文件本身有测试失败（rc!=0 且非 5）都保留在结果里；
        # 但只有"跑不完"才进 still_lost —— 失败是正常结果，交给既有基线体系。
        if not completed:
            still_lost.append(f)
        done.append((f, rc))
    return still_lost, done



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

    results: list[tuple[int, int, str, bool, str]] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(run_chunk, chunk, i, str(logdir / f"chunk_{i}.log"), marker, extra)
            for i, chunk in enumerate(chunks)
        ]
        for fu in futures:
            results.append(fu.result())

    overall_rc = 0
    for idx, rc, out, completed, detail in sorted(results):
        tail = ""
        try:
            tail = "\n".join(
                open(out, encoding="utf-8", errors="replace").read().strip().splitlines()[-3:]
            )
        except OSError:
            pass
        print(f"[chunk {idx}] rc={rc} {'✔ 已跑完' if completed else '✗ **未跑完**'} :: {detail}")
        if tail:
            print(f"  {tail}")
        if rc != 0:
            overall_rc = 1

    # ── D2：批次完整性校验 + 丢文件自动补跑 ──────────────────────────────────
    incomplete = [r for r in sorted(results) if not r[3]]
    if incomplete:
        lost: list[str] = []
        for idx, _rc, _out, _ok, _detail in incomplete:
            lost.extend(chunks[idx])
        print()
        print("=" * 78)
        print(f"⚠ 检测到 {len(incomplete)} 个分块**没有正常收尾**（缺 pytest 结束摘要）")
        print("  这通常意味着 pytest-timeout 的 thread 法超时后 os._exit 掉了整个进程，")
        print(f"  ⇒ 这些块里排在挂起测试之后的文件**从未执行**，共 {len(lost)} 个文件。")
        print("=" * 78)
        manifest = logdir / "incomplete_files.txt"
        manifest.write_text("\n".join(lost) + "\n", encoding="utf-8")
        print(f"清单已落盘: {manifest}")
        if os.environ.get("RUN_FULL_PYTEST_NO_RESUME") == "1":
            print("已按 RUN_FULL_PYTEST_NO_RESUME=1 跳过自动补跑。")
            overall_rc = 1
        else:
            print(f"\n开始逐文件补跑 {len(lost)} 个文件（各自独立进程，有界）...")
            still_lost, _done = resume_lost_files(lost, logdir, marker, extra)
            if still_lost:
                print("\n✗ 以下文件**补跑后仍未正常收尾**（即真正的元凶，需要单独处置）：")
                for f in still_lost:
                    print(f"    {f}")
                (logdir / "still_lost_files.txt").write_text(
                    "\n".join(still_lost) + "\n", encoding="utf-8"
                )
                overall_rc = 1
            else:
                print(f"\n✔ 已把 {len(lost)} 个从未执行的文件全部补跑完毕，**没有文件被丢弃**。")
    else:
        print("\n✔ 全部 %d 个分块均正常收尾，无文件丢失。" % len(results))

    print(f"== 总体结果: {'PASS' if overall_rc == 0 else 'FAIL'} ==")
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
