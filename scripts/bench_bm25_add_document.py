"""BM25 索引构建 O(n²) 退化曲线实测（TASK-08 子工作流 C / E1p）

    python scripts/bench_bm25_add_document.py                # 当前实现
    python scripts/bench_bm25_add_document.py --mode legacy  # 复现修复前的实现
    python scripts/bench_bm25_add_document.py --mode both --json _ci_logs/bm25.json

## 这个脚本回答什么

`agent/tool_router_hybrid.py::BM25Index.add_document()` 在**修复前**每次插入都执行
`total_length = sum(self._doc_lengths.values())` 重算全表总长 ⇒ 单次插入 O(n)、
构建 n 篇文档 O(n²)。这个脚本测的就是这条曲线，以及修复后是否回到近似线性。

## 口径纪律

1. **合成负载**：文档由 `data/tool_index.json` 的**真实 90 条工具定义循环扩增**而来。
   为什么循环扩增而不是随机造词：真实工具的 `description` 长度分布、中英文混排比例
   决定了分词后的 token 数，而 token 数是 BM25 构建成本的主因。随机造词会把这个
   分布带偏，测出来的斜率就不能代表真实负载。**但它仍然是合成负载**——语义内容
   高度重复，且不含真实工具的 AST/参数结构；结论只说"构建复杂度"，不说"真实 1 万
   个工具会怎样"。
2. **冷进程**：每个规模在**独立子进程**里测（`--in-process` 可关闭）。
   为什么必须这样：CPython 的 `dict` 在插入过程中会 rehash，且前一个规模的
   分配器状态（arena 复用）会显著影响下一个规模的耗时——同进程串行测 4 个规模
   会把"规模效应"和"分配器预热"混在一起。子进程隔离后每个点都是干净起点。
3. **不做 warmup 扣除**：索引构建是一次性动作，本来就没有"稳态"可谈。
4. **样本数**：每个规模 n 的"单次插入均摊"是**确定性推导值**（总耗时 / n），
   同时额外单独采样 n=90 的逐次插入耗时以给出分布（p50/p99）。

## 与修复前的对照怎么做到可复现

`--mode legacy` 会用 `_legacy_add_document` 覆盖 `BM25Index.add_document`
（普通属性赋值，不用 `importlib.reload`——本仓纪律 F13）。该函数是**逐字复制**
修复前 HEAD 的实现，故两条曲线可以在同一次运行里对照，不依赖 git 回退。
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 扩容点。90 = 真实 `data/tool_index.json` 的工具条数（也是 v1.4 §6 单租户目标的
#: 同一量级）；1000/5000/10000 用于暴露二次项。
SCALES: Tuple[int, ...] = (90, 1000, 5000, 10000)


def _pct(values: Sequence[float], p: float) -> float:
    """最近秩法分位数（与 `scripts/bench_capregistry.py` 同口径）"""
    if not values:
        return float("nan")
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(p / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def _load_real_tools() -> List[Dict[str, Any]]:
    """读真实工具定义（**只读**；`data/` 下的文件一律不写）"""
    path = os.path.join(_ROOT, "data", "tool_index.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tools = [t for t in (data.get("tools") or []) if t.get("name")]
    if not tools:
        raise SystemExit("data/tool_index.json 无工具定义，无法构造负载")
    return tools


def _make_docs(tools: List[Dict[str, Any]], n: int) -> List[Tuple[str, str]]:
    """生成 n 篇合成文档（doc_id, content）

    内容与 `HybridRetriever.rebuild` 完全同构：`name + " " + parameter_names + " " + description`。
    为什么 doc_id 要加后缀：真实工具名必须唯一（`add_document` 对同名走**覆盖**路径，
    那会走 `_remove_document_locked` 的全表扫描，是另一条更慢的路径）。本脚本测的是
    **纯追加构建**，故用 `name#k` 保证每次都是新文档。
    """
    out: List[Tuple[str, str]] = []
    for i in range(n):
        t = tools[i % len(tools)]
        params = t.get("parameter_names") or []
        if not isinstance(params, list):
            params = []
        content = f"{t['name']} " + " ".join(str(p) for p in params) + " " + str(t.get("description", ""))
        out.append((f"{t['name']}#{i}", content))
    return out


def _legacy_add_document(self: Any, doc_id: str, content: str) -> None:
    """**修复前**的 `BM25Index.add_document`（逐字复制 HEAD 版本，用于对照）

    唯一差别就是第 3 段：`total_length = sum(self._doc_lengths.values())` 每次重算全表。
    """
    from agent.tool_router_hybrid import _tokenize  # noqa: PLC0415

    tokens = _tokenize(content)
    term_counts: Dict[str, int] = {}
    for token in tokens:
        term_counts[token] = term_counts.get(token, 0) + 1

    with self._lock:
        if doc_id in self._doc_lengths:
            self._remove_document_locked(doc_id)

        for term, freq in term_counts.items():
            if term not in self._index:
                self._index[term] = []
            self._index[term].append((doc_id, freq))

        self._doc_lengths[doc_id] = len(tokens)
        self._total_docs += 1
        total_length = sum(self._doc_lengths.values())  # ← O(n)：这就是被测的二次项
        self._avg_doc_length = total_length / self._total_docs if self._total_docs > 0 else 0.0


def _apply_mode(mode: str) -> None:
    """把 `BM25Index.add_document` 换成指定实现

    why 不用 `importlib.reload`：reload 会重建模块对象，其它已 import 该模块的
    对象（例如已构造的 `HybridRetriever`）仍指向旧类 ⇒ 测的到底是哪一份实现会变得
    不可知。直接替换方法属性则**只影响本进程内此后构造的实例**，语义明确。
    """
    from agent.tool_router_hybrid import BM25Index  # noqa: PLC0415

    if mode == "legacy":
        BM25Index.add_document = _legacy_add_document
    elif mode == "current":
        # 显式记录：不替换。若修复被回退，`add_document` 里会重新出现 sum(...)，
        # 此时本脚本会如实把它测成二次曲线，而不是"因为没打补丁所以看不见"。
        pass
    else:
        raise SystemExit(f"未知 mode: {mode}")


def _measure_once(n: int, docs: List[Tuple[str, str]]) -> Dict[str, Any]:
    """单次构建（不做重复；重复由调用方做）"""
    from agent.tool_router_hybrid import BM25Index  # noqa: PLC0415

    idx = BM25Index()
    per_add: List[float] = []
    t0 = time.perf_counter()
    for doc_id, content in docs:
        # 逐次采样：`perf_counter()` 约 0.1µs，而单次插入 30–200µs ⇒ 计时开销 < 1%
        a = time.perf_counter()
        idx.add_document(doc_id, content)
        per_add.append((time.perf_counter() - a) * 1000.0)
    total_ms = (time.perf_counter() - t0) * 1000.0

    # 不变量：索引自身的总长字段（若存在）必须等于重算值 —— 修复后这条必须成立
    recomputed = sum(idx._doc_lengths.values())
    internal = getattr(idx, "_total_doc_len", None)
    return {
        "total_ms": total_ms,
        "per_add": per_add,
        "indexed": idx.size,
        "avg_doc_length": idx._avg_doc_length,
        "recomputed_total_len": recomputed,
        "internal_total_len": internal,
        "invariant_ok": (internal is None) or (internal == recomputed),
    }


def _measure_one(n: int, mode: str, repeat: int) -> Dict[str, Any]:
    """在**当前进程**内测一个规模：重复 `repeat` 次，取总耗时的中位数

    why 取中位数而不是最小值：本机有用户后端在跑（并发负载），最小值会系统性
    低估真实成本；中位数对单次 GC 抖动稳健，又不像均值那样被离群点拖走。
    """
    docs = _make_docs(_load_real_tools(), n)
    runs = [_measure_once(n, docs) for _ in range(max(1, repeat))]
    totals = sorted(r["total_ms"] for r in runs)
    med = totals[len(totals) // 2]
    rep = runs[len(totals) // 2] if len(totals) % 2 else runs[0]
    per_add = rep["per_add"]

    # 【为什么 legacy 模式要单独说明】`--mode legacy` 是**函数级**替换：它换掉了
    # add_document 的实现，但没有（也不该）动 `__init__` —— 故 `_total_doc_len`
    # 会一直停在 0，不变量检查必然"违反"。那是**模拟本身**的性质，不是被测代码的
    # 缺陷。故这里把它标成"不适用"，避免读者误读为修复引入了不一致。
    invariant_applicable = (mode != "legacy")
    out: Dict[str, Any] = {
        "mode": mode,
        "n": n,
        "docs": len(docs),
        "indexed": rep["indexed"],
        "repeat": max(1, repeat),
        "total_ms": round(med, 3),
        "total_ms_all_runs": [round(t, 3) for t in totals],
        "per_add_ms": round(med / max(len(docs), 1), 6),
        "avg_doc_length": round(rep["avg_doc_length"], 3),
        "recomputed_total_len": rep["recomputed_total_len"],
        "internal_total_len": rep["internal_total_len"],
        "invariant_applicable": invariant_applicable,
        "invariant_ok": (not invariant_applicable) or rep["invariant_ok"],
    }
    k = max(1, len(per_add) // 10)
    # 用**分位数**而非均值：dict 扩容/首次分配会让极少数早期插入特别慢，
    # 均值会被它们带偏，从而掩盖"末段变慢"这个二次项信号。
    out["add_p50_ms"] = round(_pct(per_add, 50), 6)
    out["add_p99_ms"] = round(_pct(per_add, 99), 6)
    out["add_max_ms"] = round(max(per_add), 6)
    out["add_head_p50_ms"] = round(_pct(per_add[:k], 50), 6)
    out["add_tail_p50_ms"] = round(_pct(per_add[-k:], 50), 6)
    out["add_head_tail_ratio"] = round(
        out["add_tail_p50_ms"] / max(out["add_head_p50_ms"], 1e-9), 3)
    return out


def _run_child(n: int, mode: str, repeat: int) -> Dict[str, Any]:
    """在**独立子进程**里测一个规模（冷进程，分配器状态干净）"""
    code = (
        "import json,sys;"
        f"sys.path.insert(0,{_ROOT!r});"
        "sys.path.insert(0,"
        + repr(os.path.join(_ROOT, "scripts"))
        + ");"
        "import bench_bm25_add_document as B;"
        "B._apply_mode(sys.argv[1]);"
        "print('@@RESULT@@'+json.dumps(B._measure_one(int(sys.argv[2]), sys.argv[1], int(sys.argv[3]))))"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", code, mode, str(n), str(repeat)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=_ROOT, env=env, timeout=3600,
    )
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@RESULT@@"):
            return json.loads(line[len("@@RESULT@@"):])
    raise SystemExit(
        f"子进程 n={n} mode={mode} 未产出结果\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr[-4000:]}"
    )


def _fit_linear_quadratic(points: Sequence[Tuple[int, float]]) -> Dict[str, float]:
    """最小二乘拟合 `t(n) = a·n + b·n²`，并给出线性项与二次项的交叉点

    why 要拟合而不是只看指数：指数把两项混成一个数，读不出"哪一项在什么规模上
    开始主导"。拆成 a/b 之后可以直接算出 `n* = a/b`——即**二次项超过线性项的规模**，
    这才是"退化开始咬人"的位置，也是报告里"破线点"的依据之一。
    """
    pts = [(float(n), t / 1000.0) for n, t in points if t > 0]  # ms → s
    if len(pts) < 2:
        return {"a_s_per_doc": float("nan"), "b_s_per_doc2": float("nan"),
                "crossover_n": float("nan")}
    s11 = sum(n * n for n, _ in pts)
    s12 = sum(n * n * n for n, _ in pts)
    s22 = sum(n ** 4 for n, _ in pts)
    y1 = sum(t * n for n, t in pts)
    y2 = sum(t * n * n for n, t in pts)
    det = s11 * s22 - s12 * s12
    if abs(det) < 1e-12:
        return {"a_s_per_doc": float("nan"), "b_s_per_doc2": float("nan"),
                "crossover_n": float("nan")}
    a = (y1 * s22 - y2 * s12) / det
    b = (s11 * y2 - s12 * y1) / det
    return {
        "a_s_per_doc": a,
        "b_s_per_doc2": b,
        "crossover_n": (a / b) if b > 0 else float("inf"),
    }


def _fit_exponent(points: Sequence[Tuple[int, float]]) -> float:
    """用 log-log 最小二乘拟合总耗时对 n 的幂次

    why：单看"倍数"很难区分 O(n log n) 与 O(n²)。拟合出的指数直接可读：
    1.0 附近＝线性，2.0 附近＝二次。
    """
    import math

    pts = [(math.log(n), math.log(max(t, 1e-9))) for n, t in points if t > 0]
    if len(pts) < 2:
        return float("nan")
    mx = statistics.fmean(p[0] for p in pts)
    my = statistics.fmean(p[1] for p in pts)
    num = sum((x - mx) * (y - my) for x, y in pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return num / den if den else float("nan")


def _summarize(mode: str, rows: List[Dict[str, Any]],
               say: Callable[[str], None]) -> Dict[str, Any]:
    """打印一种实现的曲线表并返回结构化结果（打印与计算分离，便于交错测量复用）"""
    label = ("修复前（每次 sum(_doc_lengths.values()) 重算全表）"
             if mode == "legacy" else "当前工作区实现")
    say("")
    say("─" * 92)
    say(f"实现：{label}   [--mode {mode}]")
    say("─" * 92)
    say(f"{'n':>7}{'总耗时(ms)':>14}{'单次均摊(ms)':>16}{'相对 n=90 倍率':>18}"
        f"{'token 均值':>13}{'首10% p50':>12}{'末10% p50':>12}{'末/首':>9}")

    pts: List[Tuple[int, float]] = []
    base: float | None = None
    for r in rows:
        pts.append((r["n"], r["total_ms"]))
        if base is None:
            base = r["total_ms"]
        say(f"{r['n']:>7}{r['total_ms']:>14.2f}{r['per_add_ms']:>16.5f}"
            f"{r['total_ms'] / base:>17.2f}x{r['avg_doc_length']:>13.2f}"
            f"{r['add_head_p50_ms']:>12.4f}{r['add_tail_p50_ms']:>12.4f}"
            f"{r['add_head_tail_ratio']:>8.2f}x")

    exp = _fit_exponent(pts)
    fit = _fit_linear_quadratic(pts)
    say("")
    say(f"  log-log 拟合指数 = **{exp:.3f}**（1.0≈线性 / 2.0≈二次）")
    say(f"  两项拟合 t(n)=a·n+b·n²：a={fit['a_s_per_doc'] * 1e6:.3f}µs/条，"
        f"b={fit['b_s_per_doc2'] * 1e9:.6f}ns/条²")
    say(f"    ⇒ 二次项等于线性项的规模 n* ≈ {fit['crossover_n']:.0f} 条"
        f"（>n* 之后退化由二次项主导；b≤0 ⇒ 无二次项，记 inf）")
    worst = rows[-1]
    say(f"  n={worst['n']} 逐次插入：p50={worst['add_p50_ms']}ms "
        f"p99={worst['add_p99_ms']}ms max={worst['add_max_ms']}ms")
    say(f"  n={worst['n']} 末/首 10% 的 p50 比值 = {worst['add_head_tail_ratio']:.2f}x"
        f"（≈1 ⇒ 单次插入成本与已插入条数无关；≫1 ⇒ 存在 O(n) 单次插入）")
    bad = [r for r in rows if not r["invariant_ok"]]
    if rows[0]["invariant_applicable"]:
        verdict = "✅ 全部成立" if not bad else f"❌ 违反 {len(bad)} 处"
    else:
        verdict = "— 不适用（legacy 是函数级替换，不会维护 _total_doc_len）"
    say(f"  不变量（_total_doc_len == sum(_doc_lengths.values())）：{verdict}")
    return {"rows": rows, "loglog_exponent": round(exp, 4), "fit": fit}


def main() -> int:
    ap = argparse.ArgumentParser(description="BM25 索引构建退化曲线")
    ap.add_argument("--mode", choices=("current", "legacy", "both"), default="current")
    ap.add_argument("--scales", default=",".join(str(s) for s in SCALES))
    ap.add_argument("--repeat", type=int, default=3,
                    help="每个规模重复次数，取总耗时中位数（默认 3）")
    ap.add_argument("--json", default="")
    ap.add_argument("--in-process", action="store_true",
                    help="不 fork 子进程（仅用于快速冒烟；正式数据请用默认的冷进程模式）")
    args = ap.parse_args()

    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    modes = ("current", "legacy") if args.mode == "both" else (args.mode,)

    lines: List[str] = []

    def say(m: str = "") -> None:
        lines.append(m)
        print(m, flush=True)

    say("=" * 92)
    say("BM25 索引构建 O(n²) 退化曲线（TASK-08 / E1p）")
    say("=" * 92)
    say(f"采集时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}")
    say(f"被测代码：agent/tool_router_hybrid.py::BM25Index.add_document")
    say(f"负载：**合成**（由 data/tool_index.json 的 90 条真实工具定义循环扩增）")
    say(f"进程模型：{'同进程' if args.in_process else '每规模一个冷子进程'}"
        f"（冷进程=分配器状态干净，避免把分配器预热当成规模效应）")
    say(f"重复：每规模 {args.repeat} 次，报总耗时**中位数**"
        f"（本机有并发负载，最小值会系统性低估）")
    say("⚠ 本机同时运行着用户的后端（127.0.0.1:5678）⇒ 采集于并发负载下；")
    say("  本脚本只用单线程，故影响主要体现在绝对量级而非曲线形状。")

    results: Dict[str, Any] = {"scales": scales, "modes": {}}

    def _one(mode: str, n: int) -> Dict[str, Any]:
        if args.in_process:
            _apply_mode(mode)
            return _measure_one(n, mode, args.repeat)
        return _run_child(n, mode, args.repeat)

    # 【测量设计】两种实现**按规模交错**测量，而不是"先跑完 A 再跑完 B"。
    # Why：本机有用户后端在跑（并发负载会随时间漂移）。串行分块测量会把
    #      "机器负载变了"混进"A 与 B 的差"——首轮实测就出现了后测的那一组
    #      在小规模上系统性慢约 14%，那是环境漂移，不是代码差异。
    #      交错之后同一规模的两组只隔几秒，漂移对两组的影响基本同向、可抵消。
    if len(modes) == 2:
        rows_by_mode: Dict[str, List[Dict[str, Any]]] = {m: [] for m in modes}
        for n in scales:
            # 每个规模**交替先后顺序**：否则"总是后测的那一组"仍会系统性受影响
            order = modes if (scales.index(n) % 2 == 0) else tuple(reversed(modes))
            for m in order:
                rows_by_mode[m].append(_one(m, n))
        for m in modes:
            results["modes"][m] = _summarize(m, rows_by_mode[m], say)
    else:
        m = modes[0]
        results["modes"][m] = _summarize(m, [_one(m, n) for n in scales], say)

    if len(modes) == 2:
        cur = results["modes"]["current"]["rows"]
        leg = results["modes"]["legacy"]["rows"]
        say("")
        say("─" * 92)
        say("修复前 / 修复后 对照（同负载、同进程模型、同重复次数、**按规模交错测量**）")
        say("─" * 92)
        say(f"{'n':>7}{'修复前(ms)':>16}{'修复后(ms)':>16}{'差值(ms)':>14}"
            f"{'占修复前':>12}{'加速比':>11}")
        for a, b in zip(leg, cur):
            d = a["total_ms"] - b["total_ms"]
            say(f"{a['n']:>7}{a['total_ms']:>16.2f}{b['total_ms']:>16.2f}{d:>14.2f}"
                f"{(d / max(a['total_ms'], 1e-9) * 100):>11.1f}%"
                f"{a['total_ms'] / max(b['total_ms'], 1e-9):>10.2f}x")
        say("")
        say(f"  修复前拟合指数 = {results['modes']['legacy']['loglog_exponent']:.3f}"
            f" / 交叉点 n* ≈ {results['modes']['legacy']['fit']['crossover_n']:.0f}")
        say(f"  修复后拟合指数 = {results['modes']['current']['loglog_exponent']:.3f}"
            f" / 交叉点 n* ≈ {results['modes']['current']['fit']['crossover_n']:.0f}")
        say("")
        say("  ⚠ 读数说明：小规模（≤5000）的差值仍在噪声带内——二次项此时只占总量的一小部分，")
        say("     **不要**把十几毫秒的差读成「修复的收益」。可信的结论是两条曲线形状不同：")
        say("     修复前末/首十分位比随 n 上升（单次插入随索引变长而变贵），修复后稳定在 ~1。")

    os.makedirs(os.path.join(_ROOT, "_ci_logs"), exist_ok=True)
    out_txt = os.path.join(_ROOT, "_ci_logs",
                           f"bench_bm25_add_document_{args.mode}.txt")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if args.json:
        p = args.json if os.path.isabs(args.json) else os.path.join(_ROOT, args.json)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        say("")
        say(f"[bench] 结构化结果已写入 {args.json}")
    say(f"[bench] 文本结果已写入 _ci_logs/{os.path.basename(out_txt)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
