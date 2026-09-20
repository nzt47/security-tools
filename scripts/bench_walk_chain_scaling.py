# -*- coding: utf-8 -*-
"""`_walk_chain` 规模退化曲线基准（TASK-08）

【为什么需要这个脚本】
TASK-07 实测：`location.py::_walk_chain` 在**全量规模**下（93 个执行器走链）
冷进程耗时 6.7 秒，最慢单工具 558ms。而它被 `callability.build_manifest` 调用，
`backfill_tool_callability.py --check` 已进 CI ⇒ **工具数增长时这里可能成为超时源**。
TASK-08 要求量化它，并给出「工具数 → 走链耗时」的退化曲线（哪怕用合成数据）。

本脚本给**两条曲线**，并**明确区分口径**（引用纪律：不得混用两种口径）：

  曲线 A（真实）—— 用 `data/capability_manifest.json` 里 93 个**真实 host_executor**，
      在**冷进程**（缓存全空）下逐个 `judge_executor_location`，记录累计耗时与最慢单工具。
      这条曲线是"真数据、真模块、真解析"，**但不是"更多工具"**：它只能到 93。

  曲线 B（合成扩容）—— 在临时目录里生成 N 个**形似真实工具模块**的合成模块
      （同样的 `register_all(dl)` 内嵌工具函数形态、同样的本地辅助函数链、
      同样的 `dl._x = Cls()` 属性赋值 + `requests.get` 跨边界调用），
      把 `location` 的仓库根临时指向该目录，测 N = 10 … 2000 的累计耗时。
      这条曲线回答"工具数增长时会怎样"，但**是合成负载**，不是生产。

【口径纪律】
- 两条曲线的数字**不可互比**（不同模块集），本脚本分别标注。
- 冷/热进程分开测：冷 = 新建进程且不预热缓存；热 = 同进程第二次调用
  （`_repo_index` / `_TREE_CACHE` 已填充）。
- 所有数字都带 n（样本数）与是否含 TASK-06/07 新层（本脚本只调 `location`，
  不含 TASK-07 的 `check_injection_guard`，故标注"不含安全层"）。

用法：
    python scripts/bench_walk_chain_scaling.py            # 两条曲线都跑
    python scripts/bench_walk_chain_scaling.py --real     # 只跑真实曲线
    python scripts/bench_walk_chain_scaling.py --synth    # 只跑合成曲线
    python scripts/bench_walk_chain_scaling.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import tempfile
import time
from typing import Dict, List, Tuple

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def _manifest_executors() -> List[str]:
    """从能力清单取全部唯一 `host_executor`（**真实数据**，不是合成）"""
    path = os.path.join(_REPO_ROOT, "data", "capability_manifest.json")
    with open(path, "r", encoding="utf-8") as f:
        m = json.load(f)
    out: List[str] = []
    seen = set()
    for e in m.get("entries", []):
        he = str(e.get("host_executor") or "").strip()
        if he and he not in seen:
            seen.add(he)
            out.append(he)
    return out


# ════════════════════════════════════════════════════════════
#  曲线 A：真实执行器
# ════════════════════════════════════════════════════════════


def run_real(points: Tuple[int, ...] = (10, 30, 60, 93)) -> Dict:
    """真实执行器曲线（**必须在冷进程里第一次调用本函数**才有冷启动数字）"""
    from agent.lines import location as loc

    executors = _manifest_executors()
    total = len(executors)

    # ① 仓库索引构建耗时（一次性成本，与工具数无关但会被"第一个工具"承担）
    t0 = time.perf_counter()
    loc._repo_index()
    index_ms = (time.perf_counter() - t0) * 1000.0

    # ② 逐个走链；把每个工具的耗时单独记下来
    per_tool: List[Tuple[str, float]] = []
    for i, he in enumerate(executors):
        t = time.perf_counter()
        try:
            loc.judge_executor_location(he)
        except Exception:  # 单个工具失败不应中断基准
            pass
        per_tool.append((he, (time.perf_counter() - t) * 1000.0))

    cum: List[float] = []
    acc = 0.0
    for _, ms in per_tool:
        acc += ms
        cum.append(acc)

    rows = []
    for n in points:
        if n > total:
            continue
        rows.append({
            "n": n,
            "cumulative_ms": round(cum[n - 1], 1),
            "mean_per_tool_ms": round(cum[n - 1] / n, 2),
        })

    slowest = sorted(per_tool, key=lambda x: -x[1])[:5]
    return {
        "mode": "real",
        "source": "data/capability_manifest.json 的 host_executor（真实模块，真实 ast 解析）",
        "n_total": total,
        "repo_index_ms": round(index_ms, 1),
        "repo_index_note": "一次性成本（_repo_index 有缓存）；与工具数无关，但由第一个工具承担",
        "curve": rows,
        "all_93_total_ms": round(cum[-1], 1),
        "per_tool_p50_ms": round(statistics.median([m for _, m in per_tool]), 2),
        "per_tool_max_ms": round(slowest[0][1], 1),
        "slowest_5": [{"executor": e, "ms": round(m, 1)} for e, m in slowest],
        "not_included": ["TASK-06 confirm_level 判定", "TASK-07 安全层(check_injection_guard)"],
    }


# ════════════════════════════════════════════════════════════
#  曲线 B：合成扩容
# ════════════════════════════════════════════════════════════

#: 合成模块模板 —— 刻意模仿真实工具模块的**形态**：
#:   ① 工具函数嵌在 `register_all(dl)` 内部（本仓库工具几乎都这样，见 `_find_function` 的注释）
#:   ② 有本地辅助函数链（真实工具也有），使走链**真的走若干跳**而不是立刻命中
#:   ③ 有 `dl._web_http = _SynthClient()` 属性赋值（触发 `_ATTR_CLASS_RE` 索引）
#:   ④ 有 `requests.get(...)` 跨边界原语（使判定有真实结论）
#: `_PAD` 用于把模块撑到接近真实工具模块的体量。
_SYNTH_TMPL = '''# -*- coding: utf-8 -*-
"""合成工具模块 #{idx}（TASK-08 扩容基准用；非生产代码）"""
import requests


class _SynthClient:
    def post(self, url, json=None, timeout=10):
        return requests.post(url, json=json, timeout=timeout)

    def get(self, url, timeout=10):
        return requests.get(url, timeout=timeout)


def _aux_{idx}_0(x):
    return x


def _aux_{idx}_1(x):
    return _aux_{idx}_0(x)


def _aux_{idx}_2(x):
    return _aux_{idx}_1(x)


def register_all(dl):
    dl._web_http_{idx} = _SynthClient()

    def tool_{idx}(params):
        a = _aux_{idx}_2(params)
        b = dl._web_http_{idx}.post("https://example.invalid/{idx}", json=a)
        return b

    return tool_{idx}

# {pad}
'''

_PAD_LINE = "# 填充行：模拟真实工具模块的注释与说明体量（不影响 AST 走链成本量级）\n"


def _write_synth_tree(root: str, count: int, pad_lines: int) -> List[str]:
    base = os.path.join(root, "synth")
    os.makedirs(base, exist_ok=True)
    names = []
    pad = _PAD_LINE * pad_lines
    for i in range(count):
        mod = f"t{i:05d}"
        path = os.path.join(base, mod + ".py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(_SYNTH_TMPL.format(idx=i, pad=pad))
        names.append(f"synth.{mod}")
    return names


def run_synth(points: Tuple[int, ...] = (10, 50, 100, 200, 500, 1000, 2000),
              pad_lines: int = 20) -> Dict:
    """合成扩容曲线：**在临时目录里**建合成仓库根，再把 location 指过去

    为什么用 monkeypatch 而不是往真仓库写文件：D15 禁止在共享工作区制造/清理文件，
    且写进 `agent/` 会污染真实索引与其它并行子代理的检索口径。
    """
    from agent.lines import location as loc

    rows = []
    detail = {}
    with tempfile.TemporaryDirectory(prefix="t08_walkchain_") as tmp:
        # 一次性生成最大规模的模块集，各测点只取前 N 个 ⇒ 避免重复建文件干扰计时
        max_n = max(points)
        names = _write_synth_tree(tmp, max_n, pad_lines)

        # 备份并改写扫描根（基准结束必须还原，否则污染同进程后续调用）
        saved = (loc._REPO_ROOT, loc._SCAN_ROOTS, loc._REPO_INDEX)
        try:
            loc._REPO_ROOT = tmp
            loc._SCAN_ROOTS = ("synth",)
            for n in points:
                loc.invalidate_cache()          # 每个测点都从"冷索引"开始
                t0 = time.perf_counter()
                loc._repo_index()               # 一次性索引（该 N 规模下）
                idx_ms = (time.perf_counter() - t0) * 1000.0

                per_tool = []
                for name in names[:n]:
                    # 模块名 `synth.t00007` → 内嵌工具函数名 `tool_7`（对齐 `_write_synth_tree`
                    # 里 `_SYNTH_TMPL.format(idx=i)` 的 i，**不是**零填充的那个串）
                    idx = int(name.rsplit(".", 1)[-1][1:])
                    t = time.perf_counter()
                    try:
                        loc.judge_executor_location(f"{name}:tool_{idx}")
                    except Exception:
                        pass
                    per_tool.append((time.perf_counter() - t) * 1000.0)
                total_ms = sum(per_tool)
                rows.append({
                    "n": n,
                    "repo_index_ms": round(idx_ms, 1),
                    "walk_total_ms": round(total_ms, 1),
                    "walk_mean_per_tool_ms": round(total_ms / n, 3),
                })
                detail[n] = {
                    "max_single_ms": round(max(per_tool), 2),
                    "p50_single_ms": round(statistics.median(per_tool), 3),
                }
        finally:
            loc._REPO_ROOT, loc._SCAN_ROOTS, loc._REPO_INDEX = saved
            loc.invalidate_cache()

    return {
        "mode": "synth",
        "source": f"临时目录合成模块（每模块 {pad_lines} 行填充 + 4 个函数 + 1 个跨边界调用）",
        "caveat": ("**合成负载**：模块形态模仿真实工具模块，但不是生产数据；"
                   "合成模块的 import 指向临时根 ⇒ 部分 callee 解析不到，"
                   "因而本曲线对'解析成本'是**偏乐观**的（AST 解析成本则如实）"),
        "curve": rows,
        "single_tool_detail": detail,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true")
    ap.add_argument("--synth", action="store_true")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    do_real = args.real or not (args.real or args.synth)
    do_synth = args.synth or not (args.real or args.synth)

    out: Dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                 "cwd": os.getcwd(), "python": sys.version.split()[0]}
    if do_real:
        out["real"] = run_real()
    if do_synth:
        out["synth"] = run_synth()

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
