#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""D2 复现：`--timeout-method=thread` 超时会**丢掉整批尚未执行的测试文件**（2026-09-19）

机制（本脚本用最小用例证明，源码依据见 pytest_timeout.py）
---------------------------------------------------------
pytest-timeout 有两种超时法，超时后的**收尾方式完全不同**：

* `thread`（Windows 上的默认值，也是本仓 `pytest.ini` 显式配置的值）
  `pytest_timeout.py:505 timeout_timer()` → 打印所有线程栈 →
  **`finally: os._exit(1)`**（`pytest_timeout.py:542`）
  ⇒ 整个 pytest 进程立刻消失。**排在后面的测试与测试文件一个都不会执行**，
    而且 pytest 来不及打印结束摘要 ⇒ 拿不到"哪些文件没跑"的信息。

* `signal`（仅 POSIX）
  `pytest_timeout.py:485 timeout_sigarlm()` → 打印栈 → `pytest.fail(...)`（`:502`）
  ⇒ 只是**当前测试失败**，会话继续，后面的文件照跑。

Windows 上 `signal` 法不可用：`pytest_timeout.py:26 HAVE_SIGALRM = hasattr(signal, "SIGALRM")`
为 False，且显式传 `--timeout-method=signal` 会在 `:324 signal.signal(signal.SIGALRM, ...)`
抛 `AttributeError`（本脚本会实测这一条，而不是照抄文档）。

用法
----
    python scripts/repro_timeout_batch_loss.py            # 三个场景全跑
    python scripts/repro_timeout_batch_loss.py --timeout 3

退出码
------
0 = 复现成功（即：thread 法确实丢了后续文件，且 signal 法在本平台不可用）
      —— 这是**预期**结果，脚本的用途是留证据，不是当门禁。
1 = 未能复现（说明 pytest-timeout 行为已变，本文档结论需要重测）
2 = 环境错误
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "_ci_logs" / "d2" / "repro"

SUMMARY_RE = re.compile(r"\d+\s+(passed|failed|error|skipped)")

#: 3 个测试文件：第 1 个必挂，后两个是"被牵连的整批文件"。
FILES = {
    # Why 用 sleep 而不是死循环：thread 法下两者的结局一样（都是 os._exit 掉进程），
    # 但 sleep 让脚本在"没能复现"时仍能自己退出，不会把调试者挂在这一步。
    "test_01_hang.py": "import time\n\ndef test_will_block():\n    time.sleep(600)\n",
    "test_02_after.py": "def test_after_one():\n    assert True\n",
    "test_03_after.py": "def test_after_two():\n    assert True\n",
}


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def run_scenario(name: str, extra_args: list[str], tmp: Path, timeout: int) -> dict:
    """跑一个场景，返回"跑了哪些测试 / 退出码 / 有没有结束摘要"。"""
    log = OUT / f"{name}.log"
    cmd = [
        sys.executable, "-m", "pytest", ".", "-v", "--no-header",
        "-p", "no:cacheprovider", "-p", "no:randomly",
        "--timeout", str(timeout), *extra_args,
    ]
    t0 = time.time()
    # 必须重定向到文件：本沙箱受限模式下管道会假挂死。
    with log.open("w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run(cmd, cwd=str(tmp), stdout=fh, stderr=subprocess.STDOUT)
    dt = time.time() - t0
    text = log.read_text(encoding="utf-8", errors="replace")

    started = sorted(set(re.findall(r"(test_0\d_\w+?\.py)::", text)))
    # Why 用 "PASSED/FAILED 行" 而不是 "::" 出现：`-v` 会先打 nodeid 再打结果，
    # 被强杀的测试只留下 nodeid 没有结果行。两者都要分开记。
    finished = sorted(set(re.findall(r"(test_0\d_\w+?\.py)::\S+\s+(?:PASSED|FAILED|ERROR|SKIPPED)", text)))
    has_summary = any(SUMMARY_RE.search(line) for line in text.splitlines())
    timed_out = "Timeout" in text

    return {
        "scenario": name,
        "args": extra_args or ["(默认，Windows 上即 thread)"],
        "exit_code": proc.returncode,
        "seconds": round(dt, 1),
        "files_started": started,
        "files_finished": finished,
        "files_never_run": sorted(set(FILES) - set(started)),
        "pytest_summary_line_present": has_summary,
        "timeout_marker_in_log": timed_out,
        "log": str(log.relative_to(ROOT)),
    }


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description="复现 thread 超时法丢整批文件")
    ap.add_argument("--timeout", type=int, default=5, help="单测试超时秒数（默认 5，让脚本快速跑完）")
    ap.add_argument("--keep", action="store_true", help="保留临时测试目录（默认删除）")
    args = ap.parse_args(argv)

    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="d2_repro_", dir=str(OUT)))
    for fname, body in FILES.items():
        (tmp / fname).write_text(body, encoding="utf-8")

    print("=" * 92)
    print("D2 复现：pytest-timeout 超时法对「整批文件」的影响")
    print("=" * 92)
    print(f"临时测试目录 : {tmp}")
    print(f"测试文件     : {sorted(FILES)}")
    print(f"挂起用例     : test_01_hang.py::test_will_block（sleep 600s，超时设 {args.timeout}s）")
    print("-" * 92)

    scenarios = [
        ("thread", ["--timeout-method=thread"]),
        ("signal", ["--timeout-method=signal"]),
        ("default", []),
    ]
    results = [run_scenario(n, a, tmp, args.timeout) for n, a in scenarios]

    header = f"{'场景':<10}{'参数':<26}{'退出码':>7}{'耗时(s)':>9}{'结束摘要':>9}{'从未执行的测试文件':>20}"
    print(header)
    print("-" * 92)
    for r in results:
        print(
            f"{r['scenario']:<10}{str(r['args'][0])[:25]:<26}{r['exit_code']:>7}"
            f"{r['seconds']:>9}{('有' if r['pytest_summary_line_present'] else '**无**'):>9}"
            f"{len(r['files_never_run']):>20}"
        )

    print("-" * 92)
    for r in results:
        print(f"\n[{r['scenario']}] 从未执行: {r['files_never_run'] or '（无）'}")
        print(f"         实际跑完  : {r['files_finished'] or '（无）'}")
        print(f"         日志      : {r['log']}")

    thread_res = next(r for r in results if r["scenario"] == "thread")
    reproduced = bool(thread_res["files_never_run"]) and not thread_res["pytest_summary_line_present"]

    report = {
        "reproduced_batch_loss_with_thread_method": reproduced,
        "results": results,
    }
    (OUT / "repro.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 92)
    if reproduced:
        print("✔ 已复现：`--timeout-method=thread` 超时后，后面的测试文件**一个都没跑**，")
        print("  且日志里没有 pytest 结束摘要 ⇒ 调用方无法知道自己丢了文件。")
    else:
        print("✗ 未复现预期行为：pytest-timeout 的行为可能已变，请重新核实本文档结论。")
    print(f"证据 JSON: {(OUT / 'repro.json').relative_to(ROOT)}")

    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if reproduced else 1


if __name__ == "__main__":
    raise SystemExit(main())
