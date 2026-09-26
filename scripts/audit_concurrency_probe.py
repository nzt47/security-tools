#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""审计链并发写压测探针（CONC 卡）——多进程 append 丢不丢记录 / 什么时候降级

【本探针回答什么】`docs/audit_skill_governance/VERIFICATION_LOG.md` 的
「D3 上报的 `lock.degraded`」一节把结论钉在「**这次没丢**」，并把
「并发写审计链（≥4 进程 × 大记录数）下 seq 是否出现缺口 / `lock.degraded` 频率」
登记为待验证面。本探针就是来补这个留白的**独立可跑**版本（CI 轻量版见
`tests/unit/test_audit_chain_concurrency.py`）。

【用法】
    python scripts/audit_concurrency_probe.py --processes 2 4 8 --per-process 200
    python scripts/audit_concurrency_probe.py --processes 4 --per-process 500 --mode startup
    python scripts/audit_concurrency_probe.py --processes 2 4 8 --per-process 200 --mode warm

    --mode startup : 所有子进程**同时启动**（模拟"多个实例同时开机"，会撞上
                     启动期的预留日志收敛竞争）
    --mode warm    : 子进程**错峰启动**，各自先完成启动收敛并报"就绪"后，
                     父进程才放发令枪（模拟"生产稳态：多个常驻进程并发追加"）
    --mode both    : 两种都跑（默认）

【安全（本卡铁律：只写临时目录）】
    1. 进程**启动最早期**就把 `AUDIT_DB_PATH` / `AUDIT_ROOTS_PATH` 指向本探针
       自己 `mkdtemp` 出来的临时目录——必须在 import `agent.*` 之前，
       否则审计链降级留痕（`notify_degraded` → `agent.audit.facade.record`，
       口径是 `db_path or os.getenv("AUDIT_DB_PATH") or DEFAULT_DB_PATH`）
       会默认落到**生产审计库**；
    2. 子进程打开库之前硬断言"库在临时根之下、且不在仓库 `data/` 之下"
       （见 `assert_tmp_db_path`，父进程/子进程各断言一次）；
    3. 每个子进程用 `set_notify_hook` 把降级留痕换成**纯计数**，既精确计数，
       又杜绝"留痕自己去取锁写库"给被测对象加噪声；
    4. 全程对生产库做**只读**前后对照：`sha256 + size + mtime_ns + 行数`
       （刻意不用 `git status -- data/`：`data/audit/` 被 gitignore，改了也是空），
       结尾打印对照结论。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unicodedata
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 仓库根（scripts/<本文件> → parents[1]）
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _REPO_ROOT not in sys.path:              # 直接 `python scripts/...` 跑时也能 import agent
    sys.path.insert(0, _REPO_ROOT)

#: 生产审计库：**只读**对照对象
PROD_DB = os.path.join(_REPO_ROOT, "data", "audit", "audit_chain.db")

#: 探针自己的临时根：**必须在 import agent.* 之前建好并写进环境变量**（见模块 docstring）。
#: 用环境变量回读而不是无条件 `mkdtemp`：spawn 的子进程会以 `__mp_main__` 重新
#: 执行本文件顶部，若每进程各建一个临时根就会留下一堆空目录（虽然无害）。
_PROBE_TMP = os.environ.get("CONC_PROBE_TMP") or tempfile.mkdtemp(prefix="audit_conc_probe_")
os.environ["CONC_PROBE_TMP"] = _PROBE_TMP
os.environ["AUDIT_DB_PATH"] = os.path.join(_PROBE_TMP, "notify_sink", "audit_chain.db")
os.environ["AUDIT_ROOTS_PATH"] = os.path.join(_PROBE_TMP, "notify_sink", "daily_roots.jsonl")

#: 子进程退出上界
_CHILD_DEADLINE_S = 300.0
#: 子进程内 `flush()` 上界
_FLUSH_TIMEOUT_S = 120.0
#: 发令枪等待上界
_GATE_TIMEOUT_S = 240.0


# ════════════════════════════════════════════════════════════
#  安全护栏
# ════════════════════════════════════════════════════════════


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([_norm(child), _norm(parent)]) == _norm(parent)
    except ValueError:
        return False


def temp_roots(extra: Sequence[str] = ()) -> List[str]:
    """可接受的临时根（环境变量 TEMP/TMP/TMPDIR + `tempfile.gettempdir()` + 显式传入）"""
    roots: List[str] = []
    for cand in list(extra) + [tempfile.gettempdir(),
                               os.environ.get("TEMP", ""),
                               os.environ.get("TMP", ""),
                               os.environ.get("TMPDIR", "")]:
        if cand:
            roots.append(os.path.abspath(os.fspath(cand)))
    uniq: List[str] = []
    for cand in roots:
        if not any(_inside(cand, kept) for kept in uniq):
            uniq.append(cand)
    return uniq


def assert_tmp_db_path(path: str, *, allowed_roots: Sequence[str] = ()) -> str:
    """硬断言：库在临时根之下，且**绝不在**生产审计目录 / 仓库 `data/` 之下"""
    ap = os.path.abspath(os.fspath(path))
    prod_dir = os.path.dirname(PROD_DB)
    data_dir = os.path.join(_REPO_ROOT, "data")
    assert _norm(ap) != _norm(PROD_DB), f"拒绝对生产审计库压测：{ap!r}"
    assert not _inside(ap, prod_dir), f"拒在生产审计目录下压测：{ap!r}"
    assert not _inside(ap, data_dir), f"拒在仓库 data/ 下压测：{ap!r}"
    roots = temp_roots(allowed_roots)
    assert any(_inside(ap, root) for root in roots), (
        f"审计库必须位于临时根之下：{ap!r} 不在 {roots!r} 任何一个之内")
    return ap


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def prod_snapshot() -> Dict[str, Any]:
    """生产库的只读快照：`sha256 + size + mtime_ns + 行数`（不存在则 available=False）"""
    snap: Dict[str, Any] = {"path": PROD_DB, "available": False}
    try:
        st = os.stat(PROD_DB)
    except OSError as exc:
        snap["error"] = f"{type(exc).__name__}: {exc}"
        return snap
    snap.update(available=True, size=int(st.st_size), mtime_ns=int(st.st_mtime_ns))
    try:
        snap["sha256"] = _sha256_file(PROD_DB)
    except OSError as exc:
        snap["sha256"] = f"<hash 失败: {type(exc).__name__}>"
    try:
        conn = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True, timeout=10.0)
        try:
            snap["rows"] = int(conn.execute("SELECT COUNT(*) FROM audit_chain").fetchone()[0])
            snap["max_seq"] = int(conn.execute(
                "SELECT COALESCE(MAX(seq),0) FROM audit_chain").fetchone()[0])
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 只读统计失败不影响探针
        snap["rows_error"] = f"{type(exc).__name__}: {exc}"
    return snap


# ════════════════════════════════════════════════════════════
#  子进程 worker（spawn 要求模块级可导入；本文件有 __main__ 守卫）
# ════════════════════════════════════════════════════════════


def probe_worker(job: Dict[str, Any], start_gate: Any) -> None:
    """子进程：构造链 →（warm 模式）先完成启动收敛并报就绪 → 等发令枪 → 并发追加

    结果写 `job["out"]` 这个**独占文件**（一进程一文件）。刻意不用 Queue/pipe：
    管道在受限沙箱下可能被拒，文件通道没有这个不确定性。
    """
    result: Dict[str, Any] = {
        "tag": job["tag"], "pid": os.getpid(), "seqs": [], "degrade": [],
        "notify": [], "lat_ms": [], "flush_ok": False, "error": "",
        "batch_start": 0.0, "batch_end": 0.0, "startup_drained": None,
    }
    try:
        assert_tmp_db_path(job["db"], allowed_roots=job["temp_roots"])   # 子进程侧纵深防御
        from agent.audit.chain import AuditChain
        from agent.utils import cross_process_lock as cpl

        cpl.set_notify_hook(
            lambda action, detail: result["notify"].append(
                {"action": str(action), "reason": str(detail.get("reason", ""))}))

        chain = AuditChain(job["db"], roots_path=job["roots"],
                           signing_enabled=False, auto_seal=False,
                           enforce_single_writer=False)
        orig_note = chain._note_degraded  # noqa: SLF001 运行期插桩，不改生产代码

        def _counting_note(reason: str, *, key: str = "") -> None:
            # 逐次计数：`_note_degraded` 的**留痕**有 30s 节流，只看留痕会低报
            result["degrade"].append({"reason": str(reason), "key": str(key)})
            return orig_note(reason, key=key)

        chain._note_degraded = _counting_note  # type: ignore[method-assign]

        if job.get("warm"):
            # 错峰启动下，这里会把"启动期预留日志收敛"**在别人开始追加之前**做完，
            # 从而把启动期竞争与稳态竞争分开测。
            result["startup_drained"] = bool(chain.flush(timeout=float(job["flush_timeout"])))
            with open(job["out"] + ".ready", "w", encoding="utf-8") as fh:
                fh.write("ready")

        if not start_gate.wait(timeout=float(job["gate_timeout"])):
            raise TimeoutError("未等到发令枪（父进程未启动？）")

        lat: List[float] = []
        result["batch_start"] = time.time()
        for i in range(int(job["count"])):
            t0 = time.perf_counter()
            entry = chain.append("conc.probe", actor=f"actor-{job['tag']}",
                                 subject=f"subject-{job['tag']}",
                                 payload={"tag": job["tag"], "i": i})
            lat.append((time.perf_counter() - t0) * 1000.0)
            result["seqs"].append(int(entry.seq))
        result["batch_end"] = time.time()
        result["lat_ms"] = lat
        result["flush_ok"] = bool(chain.flush(timeout=float(job["flush_timeout"])))
        chain.close(timeout=float(job["flush_timeout"]))
    except Exception as exc:  # noqa: BLE001 子进程异常必须回传，否则父进程只能看到超时
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            with open(job["out"], "w", encoding="utf-8") as fh:
                json.dump(result, fh, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass


# ════════════════════════════════════════════════════════════
#  统计
# ════════════════════════════════════════════════════════════


def percentile(values: Sequence[float], q: float) -> float:
    """线性插值分位数（values 为空返回 0.0）"""
    if not values:
        return 0.0
    data = sorted(values)
    if len(data) == 1:
        return float(data[0])
    pos = (len(data) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(data) - 1)
    frac = pos - lo
    return float(data[lo] * (1.0 - frac) + data[hi] * frac)


def db_seqs(db_path: str) -> List[int]:
    """直读库取全部 seq（升序）——判据用原始行，不用被测模块的自述"""
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        rows = conn.execute("SELECT seq FROM audit_chain ORDER BY seq").fetchall()
    finally:
        conn.close()
    return [int(r[0]) for r in rows]


def gaps_of(seqs: Sequence[int]) -> List[Tuple[int, int]]:
    return [(a, b) for a, b in zip(seqs, seqs[1:]) if b != a + 1]


def duplicates_of(seqs: Sequence[int]) -> List[int]:
    seen: set = set()
    dup: set = set()
    for s in seqs:
        if s in seen:
            dup.add(s)
        seen.add(s)
    return sorted(dup)


def degrade_summary(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """按 reason 归类全部降级事件（逐次计数）"""
    out: Dict[str, int] = {}
    for r in results:
        for ev in r.get("degrade") or []:
            key = str(ev.get("reason", "")).split("（")[0].strip()[:70]
            out[key] = out.get(key, 0) + 1
    return out


def notify_summary(results: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """按 action 归类 `notify_degraded` 上报数（**节流后**的留痕口径）"""
    out: Dict[str, int] = {}
    for r in results:
        for ev in r.get("notify") or []:
            key = str(ev.get("action", ""))
            out[key] = out.get(key, 0) + 1
    return out


# ════════════════════════════════════════════════════════════
#  一轮压测
# ════════════════════════════════════════════════════════════


def run_one(processes: int, per_process: int, mode: str, work_root: str,
            stagger_s: float = 1.0) -> Dict[str, Any]:
    """跑一轮 N 进程 × M 条（mode 决定子进程是同时启动还是错峰启动）"""
    ctx = multiprocessing.get_context("spawn")
    gate = ctx.Event()
    run_dir = os.path.join(work_root, f"{mode}_p{processes}_n{per_process}_"
                                      f"{time.time_ns() % 10**12}")
    os.makedirs(run_dir, exist_ok=True)
    db = os.path.join(run_dir, "audit_chain.db")
    roots = os.path.join(run_dir, "daily_roots.jsonl")
    assert_tmp_db_path(db, allowed_roots=[work_root])      # 每轮都断言

    warm = (mode == "warm")
    join = (mode == "join")
    jobs: List[Dict[str, Any]] = [{
        "db": db, "roots": roots, "tag": f"p{i}", "count": per_process,
        "warm": warm, "temp_roots": temp_roots([work_root]),
        "out": os.path.join(run_dir, f"result_p{i}.json"),
        "gate_timeout": _GATE_TIMEOUT_S, "flush_timeout": _FLUSH_TIMEOUT_S,
    } for i in range(processes)]

    if join:
        # join 模式：先把发令枪放掉，于是每个子进程**一构造完就开始追加**。
        # 父进程再错峰启动它们 ⇒ 后面的子进程是在前面的子进程"正在写"的时候
        # 构造自己的链 ⇒ 精确复现"新增实例/重启实例撞上在途记录"这一生产场景。
        gate.set()

    procs: List[Any] = []
    t_spawn = time.time()
    for idx, job in enumerate(jobs):
        procs.append(ctx.Process(target=probe_worker, args=(job, gate)))
        procs[-1].start()
        if (warm or join) and idx + 1 < len(jobs):
            time.sleep(stagger_s)          # 错峰：warm 等收敛，join 让前一个先写起来

    if warm:
        # 等所有子进程报"就绪"（各自已完成启动收敛），再放发令枪
        deadline = time.time() + _CHILD_DEADLINE_S
        while time.time() < deadline:
            if all(os.path.exists(j["out"] + ".ready") for j in jobs):
                break
            if any(not p.is_alive() for p in procs):
                break
            time.sleep(0.05)
    elif not join:
        time.sleep(0.3)                    # 同时启动：让各进程尽量"撞"在一起

    gate.set()                             # join 模式下这是幂等的第二次 set

    deadline = time.time() + _CHILD_DEADLINE_S
    for p in procs:
        p.join(timeout=max(1.0, deadline - time.time()))
    alive = [p for p in procs if p.is_alive()]
    for p in alive:
        p.terminate()
        p.join(timeout=30.0)
    wall = time.time() - t_spawn

    results: List[Dict[str, Any]] = []
    for job in jobs:
        try:
            with open(job["out"], "r", encoding="utf-8") as fh:
                results.append(json.load(fh))
        except Exception as exc:  # noqa: BLE001 缺文件 = 子进程没走到收尾
            results.append({"tag": job["tag"], "seqs": [], "degrade": [],
                            "notify": [], "lat_ms": [], "flush_ok": False,
                            "error": f"结果文件不可读: {type(exc).__name__}: {exc}",
                            "batch_start": 0.0, "batch_end": 0.0})

    seqs = db_seqs(db) if os.path.exists(db) else []
    lat = [x for r in results for x in (r.get("lat_ms") or [])]
    starts = [r["batch_start"] for r in results if r.get("batch_start")]
    ends = [r["batch_end"] for r in results if r.get("batch_end")]
    batch_s = (max(ends) - min(starts)) if (starts and ends) else 0.0

    return {
        "mode": mode, "processes": processes, "per_process": per_process,
        "expected": processes * per_process, "db": db,
        "actual": len(seqs), "gaps": gaps_of(seqs), "duplicates": duplicates_of(seqs),
        "seq_min": seqs[0] if seqs else 0, "seq_max": seqs[-1] if seqs else 0,
        "alloc_duplicates": duplicates_of(
            [s for r in results for s in r["seqs"]]),
        "degrade": degrade_summary(results), "notify": notify_summary(results),
        "errors": [f"{r.get('tag')}: {r['error']}" for r in results if r.get("error")],
        "flush_ok": all(r.get("flush_ok") for r in results),
        "exitcodes": [p.exitcode for p in procs], "alive": len(alive),
        "p50_ms": percentile(lat, 0.50), "p95_ms": percentile(lat, 0.95),
        "p99_ms": percentile(lat, 0.99), "lat_n": len(lat),
        "batch_s": batch_s, "wall_s": wall,
        "throughput": (len(seqs) / batch_s) if batch_s > 0 else 0.0,
    }


# ════════════════════════════════════════════════════════════
#  输出
# ════════════════════════════════════════════════════════════


def _w(text: str) -> int:
    """显示宽度（中日韩全角算 2 列）"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _w(text))


def print_table(rows: Sequence[Dict[str, Any]]) -> None:
    """打印压测矩阵表：并发度 / 预期 / 实际 / 缺口 / degraded / 耗时"""
    headers = ["模式", "进程", "每进程", "预期", "实际", "缺口", "重复",
               "seq冲突", "其它降级", "留痕", "p50(ms)", "p95(ms)", "吞吐(条/s)", "耗时(s)"]
    table: List[List[str]] = []
    for r in rows:
        conflict = sum(v for k, v in r["degrade"].items() if k.startswith("seq_conflict"))
        other = sum(v for k, v in r["degrade"].items() if not k.startswith("seq_conflict"))
        table.append([
            r["mode"], str(r["processes"]), str(r["per_process"]),
            str(r["expected"]), str(r["actual"]),
            str(len(r["gaps"])), str(len(r["duplicates"])),
            str(conflict), str(other), str(sum(r["notify"].values())),
            f"{r['p50_ms']:.3f}", f"{r['p95_ms']:.3f}",
            f"{r['throughput']:.0f}", f"{r['wall_s']:.2f}",
        ])
    widths = [max(_w(headers[i]), *(_w(row[i]) for row in table)) if table
              else _w(headers[i]) for i in range(len(headers))]
    print("  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)))
    print("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in table:
        print("  ".join(_pad(c, widths[i]) for i, c in enumerate(row)))


def print_details(rows: Sequence[Dict[str, Any]]) -> None:
    """逐轮细节：降级原因分布 / 留痕分布 / 缺口原文 / 子进程健康"""
    for r in rows:
        head = f"[{r['mode']} {r['processes']}×{r['per_process']}]"
        print(f"{head} seq范围=({r['seq_min']}..{r['seq_max']}) "
              f"分配重复={r['alloc_duplicates'][:10]} "
              f"退出码={r['exitcodes']} 未退出={r['alive']} flush={r['flush_ok']} "
              f"批次并发窗口={r['batch_s']:.2f}s 延迟样本={r['lat_n']}")
        if r["gaps"]:
            print(f"    !! seq 缺口 {len(r['gaps'])} 处，前 10：{r['gaps'][:10]}")
        if r["degrade"]:
            for reason, cnt in sorted(r["degrade"].items(), key=lambda kv: -kv[1]):
                print(f"    降级 ×{cnt}: {reason}")
        if r["notify"]:
            print(f"    留痕（节流后）: {r['notify']}")
        for err in r["errors"]:
            print(f"    !! 子进程错误: {err}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="审计链并发写压测探针（只在临时目录建假库，绝不碰生产库）")
    parser.add_argument("--processes", type=int, nargs="+", default=[2, 4, 8],
                        help="并发度列表（默认 2 4 8）")
    parser.add_argument("--per-process", type=int, default=200,
                        help="每个进程追加的条数（默认 200）")
    parser.add_argument("--mode", choices=["startup", "warm", "join", "both"], default="both",
                        help="startup=同时启动；warm=错峰启动且各自先完成启动收敛；"
                             "join=错峰启动但**立刻开始追加**（模拟「第二个实例在第一个"
                             "实例正在写时启动」）；both=startup+warm")
    parser.add_argument("--stagger", type=float, default=1.0,
                        help="warm/join 模式下子进程之间的启动间隔秒数（默认 1.0）。"
                             "join 模式要复现竞争必须比「单进程追加一撮记录的耗时」更短，"
                             "否则前一个进程已经写完并落盘了")
    parser.add_argument("--keep", action="store_true", help="保留临时目录（默认跑完删除）")
    args = parser.parse_args(argv)

    modes = ["startup", "warm"] if args.mode == "both" else [args.mode]
    work_root = os.path.join(_PROBE_TMP, "runs")
    os.makedirs(work_root, exist_ok=True)

    print("=" * 100)
    print("审计链并发写压测探针（CONC）")
    print(f"  临时工作根 : {work_root}")
    print(f"  生产库     : {PROD_DB}")
    print(f"  模式       : {modes}   并发度: {args.processes}   每进程: {args.per_process}")
    print("=" * 100)

    before = prod_snapshot()
    print(f"[只读基线] 生产库 sha256={str(before.get('sha256'))[:16]} "
          f"size={before.get('size')} mtime_ns={before.get('mtime_ns')} "
          f"rows={before.get('rows')} max_seq={before.get('max_seq')}")
    print()

    rows: List[Dict[str, Any]] = []
    for mode in modes:
        for n in args.processes:
            print(f"--- 运行 {mode} / {n} 进程 × {args.per_process} 条 ...")
            row = run_one(n, args.per_process, mode, work_root,
                          stagger_s=args.stagger)
            rows.append(row)
            print(f"    完成：实际={row['actual']}/{row['expected']} "
                  f"缺口={len(row['gaps'])} 重复={len(row['duplicates'])} "
                  f"seq冲突={sum(v for k, v in row['degrade'].items() if k.startswith('seq_conflict'))} "
                  f"耗时={row['wall_s']:.2f}s")
    print()

    print("=" * 100)
    print("压测矩阵（并发度 / 预期 / 实际 / 缺口 / degraded / 耗时）")
    print("=" * 100)
    print_table(rows)
    print()
    print("=" * 100)
    print("逐轮细节")
    print("=" * 100)
    print_details(rows)
    print()

    after = prod_snapshot()
    print("=" * 100)
    print("只读断言：生产库前后对照（sha256 + size + mtime_ns + 行数）")
    print("=" * 100)
    same = (before.get("sha256") == after.get("sha256")
            and before.get("size") == after.get("size")
            and before.get("mtime_ns") == after.get("mtime_ns")
            and before.get("rows") == after.get("rows"))
    print(f"  before: sha256={str(before.get('sha256'))[:16]} size={before.get('size')} "
          f"mtime_ns={before.get('mtime_ns')} rows={before.get('rows')} "
          f"max_seq={before.get('max_seq')}")
    print(f"  after : sha256={str(after.get('sha256'))[:16]} size={after.get('size')} "
          f"mtime_ns={after.get('mtime_ns')} rows={after.get('rows')} "
          f"max_seq={after.get('max_seq')}")
    print(f"  结论  : {'未变化（探针全程只写了临时目录）' if same else '发生变化'}")
    if not same:
        print("  !! 注意：生产链可能正被**本机常驻服务**正常写入（外部写入），")
        print("     或本探针发生了污染。请对照 rows/max_seq 增量与是否只增不改，")
        print("     并用 tests/unit/test_audit_chain_concurrency.py 的只读守卫交叉验证。")
    print()

    total_gaps = sum(len(r["gaps"]) for r in rows)
    total_dup = sum(len(r["duplicates"]) for r in rows)
    total_loss = sum(max(r["expected"] - r["actual"], 0) for r in rows)
    total_conflict = sum(sum(v for k, v in r["degrade"].items()
                             if k.startswith("seq_conflict")) for r in rows)
    print("=" * 100)
    print("结论")
    print("=" * 100)
    print(f"  总轮次={len(rows)} 合计预期={sum(r['expected'] for r in rows)} "
          f"合计实际={sum(r['actual'] for r in rows)}")
    print(f"  seq 缺口总数={total_gaps}  重复 seq 总数={total_dup}  "
          f"缺失（预期-实际）={total_loss}  seq_conflict 降级总数={total_conflict}")
    if total_gaps or total_dup or total_loss:
        print("  ⇒ **发现丢行/重号**：见上面逐轮细节，最小复现命令见报告 CONC.md。")
    else:
        print("  ⇒ 本轮矩阵内**未发现丢行**：seq 全部连续、无重复、总数守恒。")
    print()

    if args.keep:
        print(f"[--keep] 临时目录保留在：{_PROBE_TMP}")
    else:
        shutil.rmtree(_PROBE_TMP, ignore_errors=True)
    # 退出码语义：**发现丢行/重号即失败**（便于接 CI）；降级本身不算失败
    return 1 if (total_gaps or total_dup or total_loss) else 0


if __name__ == "__main__":
    raise SystemExit(main())
