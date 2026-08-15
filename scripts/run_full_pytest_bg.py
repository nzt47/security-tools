"""全量 pytest 后台静默运行 + 汇总报告生成脚本

背景：全量套件约 14000 用例 / 1 小时，前台阻塞占用终端且结果仅散落在各 chunk 日志。
本脚本复用 run_full_pytest.py 的分块/并行/离线规避逻辑（不重复造轮子），
增加：后台静默运行（--bg）、进度状态文件（state.json）、聚合报告（JSON + Markdown）。

用法：
    python scripts/run_full_pytest_bg.py [--bg] [--chunks N] [--workers N] [--mode fast|slow|all]
    前台（默认）：阻塞执行并打印报告路径（便于调试/验证脚本本身）
    --bg：Popen 启动子进程静默后台运行，立即返回 PID（报告路径打印到 stdout）

产物（pytest_bg/ 目录）：
    chunk_{i}.log              每块 pytest 原始日志
    report_{ts}.md / .json     聚合报告（每块 passed/failed/error/skipped + 总体结论）
    state.json                 运行状态：pending→running→done/failed，含每块进度

判定约定（【2026-08-14 T-4 教训】）：
- 每块是否完整执行以日志中的 "=+ \\d+ passed" 汇总行为准，而非仅看 rc
  （pytest-timeout 强杀时 rc=1 且无汇总，不能把"无 passed 汇总"误判为成功）
- chunk rc=5（no tests ran）视为通过（分块后该块恰好无匹配用例）
- 默认 fast 模式（P1 A3：排除 @pytest.mark.slow 的 D 类环境性慢测试，分块稳定执行）
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# 复用 run_full_pytest.py 的收集/分块/离线规避/模式参数（不重复造轮子）
import run_full_pytest as rfp  # noqa: E402

BG_DIR = ROOT / "pytest_bg"
SUMMARY_RE = re.compile(
    r"^=+ (\d+) passed(?:, (\d+) failed)?(?:, (\d+) error)?"
    r"(?:, (\d+) skipped)?(?:, (\d+) xfailed)?(?:, (\d+) xpassed)?"
    r" in ([\d.]+)s =+$"
)


def parse_summary(log_path: Path) -> dict:
    """解析 chunk 日志中的 pytest 汇总行（T-4 教训(4)：须看汇总行而非仅 rc）"""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"summary": None, "has_passed_line": False}
    matches = [m for m in SUMMARY_RE.finditer(text) if "passed" in m.group(0)]
    if not matches:
        return {"summary": None, "has_passed_line": False}
    m = matches[-1]
    return {
        "summary": {
            "passed": int(m.group(1) or 0),
            "failed": int(m.group(2) or 0),
            "error": int(m.group(3) or 0),
            "skipped": int(m.group(4) or 0),
            "xfailed": int(m.group(5) or 0),
            "xpassed": int(m.group(6) or 0),
            "duration_s": float(m.group(7) or 0.0),
        },
        "has_passed_line": True,
    }


def write_state(state: dict) -> None:
    BG_DIR.mkdir(parents=True, exist_ok=True)
    (BG_DIR / "state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_and_report(chunks_n: int, workers: int, mode: str) -> int:
    BG_DIR.mkdir(parents=True, exist_ok=True)
    marker = rfp.MODE_MARKER[mode]
    extra = rfp.MODE_EXTRA[mode]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    state = {
        "pid": os.getpid(),
        "status": "running",
        "mode": mode,
        "chunks": chunks_n,
        "workers": workers,
        "started_at": datetime.now().isoformat(),
        "chunk_results": [],
    }
    write_state(state)

    files = rfp.collect()
    chunks = [files[i::chunks_n] for i in range(chunks_n)]
    chunks = [c for c in chunks if c]

    results: list[dict] = []
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futures = [
            ex.submit(rfp.run_chunk, chunk, i, str(BG_DIR / f"chunk_{i}.log"), marker, extra)
            for i, chunk in enumerate(chunks)
        ]
        for fu in futures:
            idx, rc, out = fu.result()
            parsed = parse_summary(BG_DIR / f"chunk_{idx}.log")
            results.append({
                "idx": idx,
                "rc": rc,
                "log": out,
                **parsed,
            })
            state["chunk_results"] = results
            write_state(state)  # 每块完成即落盘，便于查询进度

    results.sort(key=lambda r: r["idx"])
    ok_chunks = [r for r in results if r["rc"] == 0]
    has_summary = [r for r in ok_chunks if r["has_passed_line"]]
    no_summary = [r for r in ok_chunks if not r["has_passed_line"]]
    # rc=0 但无汇总行 = 被 pytest-timeout 强杀过（rc 被吞）或 0 用例，按 T-4 教训不能视为成功
    all_complete = (len(no_summary) == 0) and (len(has_summary) == len(chunks))
    all_pass = all_complete and all(
        r["summary"]["failed"] == 0 and r["summary"]["error"] == 0 for r in has_summary
    )
    overall_rc = 0 if all_pass else 1

    report = {
        "ts": ts,
        "mode": mode,
        "total_files": len(files),
        "chunk_count": len(chunks),
        "overall": "PASS" if all_pass else "FAIL",
        "all_chunks_completed": all_complete,
        "results": results,
    }
    (BG_DIR / f"report_{ts}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (BG_DIR / f"report_{ts}.md").write_text(
        render_markdown(report), encoding="utf-8"
    )

    state["status"] = "done" if all_complete else "degraded"
    state["overall"] = report["overall"]
    state["finished_at"] = datetime.now().isoformat()
    state["report"] = f"report_{ts}.md"
    write_state(state)

    print(f"[run_full_pytest_bg] 报告: {BG_DIR / ('report_' + ts + '.md')}")
    print(f"[run_full_pytest_bg] 总体: {report['overall']} "
          f"(chunks={len(chunks)} 全部完成={all_complete})")
    for r in results:
        s = r["summary"] or {}
        detail = (f"passed={s.get('passed')} failed={s.get('failed')} "
                  f"error={s.get('error')}") if s else "无汇总(疑似被强杀/0用例)"
        print(f"  [chunk {r['idx']}] rc={r['rc']} {detail}")
    return overall_rc


def render_markdown(report: dict) -> str:
    lines = [
        f"# 全量 pytest 后台运行报告",
        f"",
        f"- 时间: {report['ts']}",
        f"- 模式: {report['mode']}",
        f"- 测试文件数: {report['total_files']}",
        f"- 总体结论: **{report['overall']}**",
        f"- 全部 chunk 完整执行: {report['all_chunks_completed']}",
        f"",
        f"## 分块结果",
        f"",
        f"| chunk | rc | passed | failed | error | skipped | duration_s | 汇总 |",
        f"|---|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        s = r["summary"]
        if s:
            lines.append(
                f"| {r['idx']} | {r['rc']} | {s['passed']} | {s['failed']} | "
                f"{s['error']} | {s['skipped']} | {s['duration_s']} | 有 |"
            )
        else:
            lines.append(f"| {r['idx']} | {r['rc']} | - | - | - | - | - | 无(疑似强杀) |")
    lines.append("")
    lines.append("## 失败块日志尾部")
    for r in report["results"]:
        if r["rc"] != 0 or not r["has_passed_line"]:
            try:
                tail = "\n".join(
                    (BG_DIR / f"chunk_{r['idx']}.log").read_text(
                        encoding="utf-8", errors="replace"
                    ).strip().splitlines()[-6:]
                )
            except OSError:
                tail = "(日志读取失败)"
            lines += ["", f"### chunk {r['idx']}", "", "```", tail, "```"]
    return "\n".join(lines)


def main() -> int:
    argv = sys.argv[1:]
    bg = "--bg" in argv
    chunks_n = int(argv[argv.index("--chunks") + 1]) if "--chunks" in argv else 4
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 4
    mode = argv[argv.index("--mode") + 1] if "--mode" in argv else "fast"
    if mode not in rfp.MODE_MARKER:
        print(f"[run_full_pytest_bg] 非法 mode={mode!r}，可选 fast/slow/all", file=sys.stderr)
        return 1

    if bg and os.environ.get("RFBP_CHILD") != "1":
        # 后台模式：Popen 启动自身子进程，静默运行（CREATE_NO_WINDOW + DETACHED_PROCESS）
        BG_DIR.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, **rfp.K9_OFFLINE_ENV, "RFBP_CHILD": "1"}
        log = open(BG_DIR / "bg_stdout.log", "a", encoding="utf-8", buffering=1)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--mode", mode,
             "--chunks", str(chunks_n), "--workers", str(workers)],
            cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
            creationflags=flags, close_fds=True,
        )
        write_state({
            "pid": child.pid, "status": "pending", "mode": mode,
            "started_at": datetime.now().isoformat(), "chunk_results": [],
            "log": str(BG_DIR / "bg_stdout.log"),
        })
        print(f"[run_full_pytest_bg] 后台已启动 PID={child.pid} "
              f"(状态: {BG_DIR / 'state.json'})")
        return 0

    # 前台 / 子进程：真正执行
    try:
        return run_and_report(chunks_n, workers, mode)
    finally:
        if os.environ.get("RFBP_CHILD") == "1":
            pass  # 子进程退出时 state 已由 run_and_report 落盘


if __name__ == "__main__":
    sys.exit(main())
