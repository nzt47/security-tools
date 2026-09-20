# -*- coding: utf-8 -*-
"""Tool Router 复杂度基准（TASK-08）

【目标】把 v1.4 的目标 —— 「Router 组装 p99 < 50ms」「复杂度 O(n log n)」——
变成可实测的曲线，并判定是否存在 O(n²)。

【被测对象（**已判定的活路径**，见报告 §Router 权威路径）】
    ① `agent/lines/assembler.py::assemble()`        —— 主线装配（首选）
    ② `agent/tool_router.py::get_tools_for_input()` —— 回退路由
    ③ `agent/tools/__init__.py::get_tool_defs()`    —— **最终构造 tools 参数的函数**
    ④ `agent/tool_schema_pruner.py::prune_tool_defs()` —— 末道裁剪

【口径】
- n = 10 / 50 / 114 / 500 / 1000。
  · n ≤ 真实规模（工具 91 / 能力 114）时用**真实** registry 与真实 `ToolMeta`；
  · n > 真实规模时必须**合成注入** ⇒ 该段是**合成负载**，本脚本逐点标注 `synthetic`。
- 冷进程 / 热进程分开：`--cold` 表示每个 n 都在新进程里跑（默认同进程，
  因为本基准关心的是**随 n 的斜率**，同进程更省时且更稳定）。
- 每点重复 `--reps` 次取 **p50/p95/p99/max**（性能门禁要用分位数，不能用单次值）。
- **不含** LLM 网络耗时（本基准只测本地组装成本）。
- 标注是否含 TASK-06/07 新层：`assemble()` 走的是 `ToolMeta.confirm_level`（TASK-06 已进），
  但**不含** TASK-07 的 `check_injection_guard`（那只在 `tools.call()` 里）。

用法：
    python scripts/bench_router_complexity.py --json _t08_logs/router_complexity.json
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Callable, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

NS = (10, 50, 114, 500, 1000)


def _percentiles(samples: List[float]) -> Dict[str, float]:
    s = sorted(samples)

    def q(p: float) -> float:
        if not s:
            return 0.0
        idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
        return s[idx]

    return {
        "n_samples": len(s),
        "p50_ms": round(statistics.median(s), 3) if s else 0.0,
        "p95_ms": round(q(0.95), 3),
        "p99_ms": round(q(0.99), 3),
        "max_ms": round(max(s), 3) if s else 0.0,
        "mean_ms": round(statistics.mean(s), 3) if s else 0.0,
    }


def _timed(fn: Callable[[], object], reps: int) -> List[float]:
    out = []
    for _ in range(reps):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000.0)
    return out


# ════════════════════════════════════════════════════════════
#  真实基座
# ════════════════════════════════════════════════════════════


def _instrument_registry(n_extra: int) -> int:
    """按需向 `agent.tools._registry` 注入合成工具（**仅在本进程内**，不落盘）

    【为什么必须为 n>91 合成】真实注册表只有 ~90 条。要回答"n=1000 时组装多久"，
    唯一办法是临时扩容。注入只发生在本基准进程的内存里，进程退出即消失。
    """
    if n_extra <= 0:
        return 0
    from agent import tools as T
    reg = T._registry
    added = 0
    for i in range(n_extra):
        name = f"_synth_tool_{i:05d}"
        if name in reg:
            continue
        reg[name] = {
            "name": name,
            "description": "合成工具（TASK-08 复杂度基准用，非生产）："
                           "用于把候选集扩到 n>91 以观测组装复杂度",
            "handler": lambda **kw: None,
            "schema": {"type": "object",
                       "properties": {"q": {"type": "string", "description": "查询"}},
                       "required": []},
            "source": "t08_bench",
        }
        added += 1
    # 让 `get_tool_defs()` 的无白名单缓存失效（走白名单时不缓存，但保持一致性）
    try:
        T._registry_version += 1
    except Exception:
        pass
    return added


def _load_real_registry() -> Dict:
    """**必须先装真实注册表**，否则测到的是空注册表的假绿（p50 会低到 0.0006ms）"""
    sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
    from _t08_registry_boot import ensure_registry_loaded  # type: ignore
    return ensure_registry_loaded()


def _real_names() -> List[str]:
    from agent import tools as T
    return [t["name"] for t in T.list_tools()]


def _synth_meta(names: List[str]):
    """为合成名字造 `ToolMeta`（真实名字用真实元数据）

    【为什么可以合成 ToolMeta】`assemble()` 只读 plane/effect/risk/tags/description 四个字段，
    形态与真实一致即可让打分路径真实执行；合成对象**不落盘、不入 YAML**（D1 单一真相源）。
    """
    from agent.lines.models import ToolMeta, load_tool_meta
    real = load_tool_meta()
    out = dict(real)
    planes = ("resident", "perceive", "act")
    for i, n in enumerate(names):
        if n in out:
            continue
        out[n] = ToolMeta(
            name=n, category="synth", plane=planes[i % 3],
            effect="read", risk="low",
            tags=("synth",), description="合成工具描述" * 5,
        )
    return out


def _profile():
    """取**当前激活主线**的真实档案（拿不到就用默认档案，保证基准可跑）"""
    from agent.lines.models import LineProfile
    try:
        from agent.lines.registry import get_line_registry, resolve_line_id  # type: ignore
        lid = resolve_line_id()
        if lid:
            p = get_line_registry().load(lid)
            if p is not None:
                return p, lid
    except Exception:
        pass
    return LineProfile(id="t08-bench", name="基准默认档案", max_tools=26), "(default)"


# ════════════════════════════════════════════════════════════
#  四个被测组件
# ════════════════════════════════════════════════════════════


def bench_assemble(ns, reps: int) -> Dict:
    from agent.lines.assembler import assemble
    prof, lid = _profile()
    names = _real_names()
    n_real = len(names)
    extra = max(0, max(ns) - n_real)
    added = _instrument_registry(extra)
    if added:
        names = _real_names()
    meta = _synth_meta(names)

    rows = []
    for n in ns:
        subset = names[:n]
        samples = _timed(lambda: assemble(prof, subset, meta=meta), reps)
        rows.append({"n": n, "synthetic": n > n_real, **_percentiles(samples)})
    return {
        "component": "lines.assembler.assemble(profile, available, meta=<预取>)",
        "line_id": lid,
        "n_real_registered": n_real,
        "note": ("meta 已预取（P0-1 后 `load_tool_meta()` 热调用 0.29ms，可忽略）；"
                 "n > n_real_registered 为合成注入"),
        "rows": rows,
    }


def bench_get_tool_defs(ns, reps: int) -> Dict:
    from agent import tools as T
    names = _real_names()
    n_real = len(names)
    extra = max(0, max(ns) - n_real)
    if extra:
        _instrument_registry(extra)
        names = _real_names()

    rows = []
    for n in ns:
        subset = names[:n]
        samples = _timed(lambda: T.get_tool_defs(whitelist=subset), reps)
        rows.append({"n": n, "synthetic": n > n_real, **_percentiles(samples)})
    return {
        "component": "tools.get_tool_defs(whitelist=[n 个])  ← 最终构造 tools 参数的函数",
        "n_real_registered": n_real,
        "note": "白名单路径**不走缓存**（源码注释：'有白名单时不使用缓存，实时计算'）",
        "rows": rows,
    }


def bench_router(ns, reps: int) -> Dict:
    import agent.tool_router as R
    names = _real_names()
    n_real = len(names)
    extra = max(0, max(ns) - n_real)
    if extra:
        _instrument_registry(extra)
        names = _real_names()

    rows = []
    for n in ns:
        subset = names[:n]
        samples = _timed(
            lambda: R.get_tools_for_input("帮我搜索并写入文件", subset, max_tools=25), reps)
        rows.append({"n": n, "synthetic": n > n_real, **_percentiles(samples)})
    return {
        "component": "tool_router.get_tools_for_input(user_input, whitelist[n], max_tools=25)",
        "n_real_registered": n_real,
        "note": ("候选集由 `TOOL_CATEGORIES` 决定、再与 whitelist 取交集 ⇒ "
                 "**本函数耗时对 n 不敏感**（这正是它比 assembler 快的原因，也是它召回更粗的原因）"),
        "rows": rows,
    }


def bench_pruner(ns, reps: int) -> Dict:
    from agent import tools as T
    from agent.tool_schema_pruner import prune_tool_defs
    names = _real_names()
    n_real = len(names)
    extra = max(0, max(ns) - n_real)
    if extra:
        _instrument_registry(extra)
        names = _real_names()

    rows = []
    for n in ns:
        defs = T.get_tool_defs(whitelist=names[:n])
        samples = _timed(
            lambda: prune_tool_defs(defs, intent_context={"selected_tools": list(names[:n])}),
            reps)
        rows.append({"n": n, "synthetic": n > n_real, "n_defs_in": len(defs), **_percentiles(samples)})
    return {"component": "tool_schema_pruner.prune_tool_defs(defs[n])",
            "n_real_registered": n_real, "rows": rows}


def bench_yaml_category_load() -> Dict:
    """`tool_router.py` 里**残留的纯 Python safe_load**（P0-1 的同型问题，另一处）

    【为什么单列】P0-1 把 `agent/lines/models.py::load_tool_meta()` 改成 `CSafeLoader`
    并加了进程级缓存（热调用 0.29ms）。但 `agent/tool_router.py:266` 对**同一批 91 个 YAML**
    仍用 `yaml.safe_load`，且 `:308` 在**模块级**执行一次 ⇒ 它不再影响每请求耗时，
    但仍是一次**冷启动成本**。本项把它量化出来。
    """
    import agent.tool_router as R
    samples = _timed(R._load_tool_categories_from_yaml, 3)
    return {
        "component": "tool_router._load_tool_categories_from_yaml()（模块级执行一次）",
        "loader": "yaml.safe_load（**未用 CSafeLoader**，与 P0-1 修的那处同型）",
        "rows": [{"n": 91, "synthetic": False, **_percentiles(samples)}],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--json", default="")
    args = ap.parse_args()

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "python": sys.version.split()[0],
        "reps": args.reps,
        "cold_or_warm": "同进程热态（关注**随 n 的斜率**，不是绝对值）",
        "includes_task06": True,
        "includes_task07_security_layer": False,
        "ns": list(NS),
    }
    # 【必须先装真实注册表】否则测到空注册表 ⇒ 假绿（详见 _t08_registry_boot 的说明）
    out["registry"] = _load_real_registry()
    out["assemble"] = bench_assemble(NS, args.reps)
    out["get_tool_defs"] = bench_get_tool_defs(NS, args.reps)
    out["get_tools_for_input"] = bench_router(NS, args.reps)
    out["prune_tool_defs"] = bench_pruner(NS, args.reps)
    out["yaml_category_load"] = bench_yaml_category_load()

    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
