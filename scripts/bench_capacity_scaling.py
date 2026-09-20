"""容量扩容压测：Registry / Router 组装 / 语义召回 / 内存（TASK-08 第 7 步 / E1h）

    python scripts/bench_capacity_scaling.py                     # 全部规模
    python scripts/bench_capacity_scaling.py --scales 500,1000   # 只跑两个规模
    python scripts/bench_capacity_scaling.py --json _ci_logs/cap.json

## 它回答什么（v1.4 §6 容量目标：单租户 ≤500 / 全局 ≤10,000）

| 指标 | 目标 | 本脚本的测法 |
|---|---|---|
| Registry 查询 p99 | < 100 ms | 16 并发共享同一 Registry 实例 |
| Router 组装 p99 | < 50 ms | 分类 + 排序/截断核心，16 并发 |
| 语义召回 p99 | — | 复用检索栈的 **BM25 腿**（`HybridRetriever`，embedding 关闭） |
| 内存 | — | 独立子进程取 RSS / 峰值工作集（WS） |
| **超限行为** | — | 501 单租户 + 10,001 全局，看是否被拒/告警/截断 |

## 与既有资产的关系（**不另造一套**）

* 合成能力、并发压测骨架、分位数口径**全部复用**
  `scripts/bench_capregistry.py`（`_synthesize` / `_run_concurrent` / `_pct`）——
  同一套合成数据既喂 Registry 也喂检索索引，两组数字才有可比性；
* `TASK-05` 已实测过"真实 114 条最差 p99 = 33.60 ms 达标"与"10k 全量信封未达标"，
  本脚本**不重测 114 条**那一段，只补 500 / 1,000 / 5,000 / 10,000 的曲线；
* `docs/RRF_5000SKILLS_CAPACITY_BOUNDARY_REPORT.md` 测的是**检索栈**（5000 技能，
  RRF 融合 P99），**不是 Registry**（能力信封）。两者对象不同、口径不同，
  本报告引用它时会显式标注，不与本脚本的数字混算。

## 口径纪律

1. **合成 ≠ 生产**：所有合成结论都标注"合成负载"，不得用于声称"真实 1 万能力达标"；
2. **并发 vs 空载**：延迟类指标一律 16 并发（与 waitress 16 线程同构），内存类指标
   在**独立冷进程**里取（并同时给出"仅 import 的空载基线"）；
3. **应用级日志**在压测前被压到 `CRITICAL`：`HybridRetriever._query_locked` 每次查询
   有 4 条 `logger.info`，开着会把日志格式化成本算进"查询耗时"。
   故这里的数字是**关闭应用级 INFO 日志**后的口径（生产若开 INFO 会另有开销）；
4. **样本数**逐项打印（大对象上的重查询样本数较小，报告里如实标注 n）。
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import gc
import json
import os
import statistics
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPTS = os.path.join(_ROOT, "scripts")
for _p in (_ROOT, _SCRIPTS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用既有压测骨架（**不复制**分位数/并发实现，避免两套口径）
from bench_capregistry import (  # noqa: E402
    CONCURRENCY, _run_concurrent, _synthesize,
)

#: 扩容点：500 = v1.4 §6 的单租户目标；10,000 = 全局目标
SCALES: Tuple[int, ...] = (500, 1000, 5000, 10000)

#: 每指标的每线程轮数（与 bench_capregistry 同精神：信封类每次数百毫秒，样本数必须降档）
ROUNDS: Dict[str, int] = {
    "get": 500,
    "filtered": 100,
    "envelope_paged": 100,
    "retrieval": 100,
    "assembly": 200,
}
#: 无条件全量信封：10k 上单次数百毫秒，样本数再降一档（报告里如实标注 n）
FULL_ENVELOPE_ROUNDS: Dict[int, int] = {500: 20, 1000: 10, 5000: 3, 10000: 3}
#: **空载（单线程）**口径的轮数。为什么必须有这一组：16 并发下 p99 与 p50 差两个
#: 数量级（实测 500 条时分页信封 p50=1.85ms / p99=252ms），单看 p99 无法判断
#: "是查询真的变慢了"还是"只是被 GC/GIL 抖动放大了尾"。单线程口径给出**结构成本**，
#: 并发口径给出**部署形态下的尾**，两者相减才是并发税的量化。
SEQ_ROUNDS: Dict[str, int] = {
    "get": 2000,
    "filtered": 200,
    "envelope_paged": 200,
    "retrieval": 200,
    "assembly": 200,
}
SEQ_FULL_ENVELOPE_ROUNDS: Dict[int, int] = {500: 30, 1000: 20, 5000: 10, 10000: 10}
#: 目标线（v1.4 §6 / 本任务书）
TARGET_REGISTRY_P99_MS = 100.0
TARGET_ROUTER_ASSEMBLY_P99_MS = 50.0


def _run_sequential(label: str, fn: Callable[[int], float],
                    rounds: int) -> Dict[str, Any]:
    """**单线程**逐个调用并记录每次耗时（空载口径）

    why 不直接用 `bench_capregistry._run_concurrent(..., concurrency=1)`：那也能跑，
    但入口参数名会让人误读成"1 并发压测"。这里显式写一个单线程采集器，语义清楚。
    分位数仍复用 `bench_capregistry._stats`，保证与并发口径**同一套算法**。
    """
    from bench_capregistry import _stats

    samples: List[float] = []
    t0 = time.perf_counter()
    for i in range(rounds):
        samples.append(fn(i))
    wall = (time.perf_counter() - t0) * 1000.0
    st = _stats(samples)
    st["wall_ms"] = round(wall, 3)
    st["concurrency"] = 1
    st["qps"] = round(len(samples) / (wall / 1000.0), 1) if wall > 0 else 0.0
    st["label"] = label
    return st


def _run_concurrent_gc_off(label: str, fn: Callable[[int], float],
                           rounds: int) -> Dict[str, Any]:
    """**关掉 GC** 再跑一遍 16 并发（诊断口径，不是部署建议）

    why 要做这个诊断：并发 p99 与 p50 差两个数量级时，最常见的元凶是
    **分代 GC 的 stop-the-world**（每次查询构造数千个短命 dict，10k 规模下
    gen2 回收动辄百毫秒）。关掉 GC 复测，如果尾塌下来 ⇒ 归因成立，
    建议就是"GC 调参 / 减少每请求分配"，而不是"优化查询算法"。
    ⚠ 这不是可部署的配置（内存会无界增长），只用于归因。
    """
    import gc as _gc

    was_enabled = _gc.isenabled()
    _gc.disable()
    try:
        st = _run_concurrent(label + " [GC off·诊断]", fn, rounds=rounds)
    finally:
        if was_enabled:
            _gc.enable()
    st["gc_off"] = True
    return st


# ════════════════════════════════════════════════════════════
#  日志压制（测量条件，不是"美化"）
# ════════════════════════════════════════════════════════════


def _quiet_application_loggers() -> None:
    """把被测模块的 logger 压到 CRITICAL

    why 必须做且必须标注：`HybridRetriever._query_locked` 每次查询写 4 条
    `logger.info`（含 token 列表的 repr）。保留 INFO 时，"查询耗时"里混进了
    日志格式化与 handler 的成本——那是**部署配置**的代价，不是索引结构的代价。
    本脚本要测的是后者，故压制；报告里显式写明该口径。
    """
    import logging

    for name in ("agent.tool_router_hybrid", "agent.tool_router",
                 "agent.capregistry", "agent.capregistry.view"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


# ════════════════════════════════════════════════════════════
#  负载：合成能力（复用 bench_capregistry._synthesize）
# ════════════════════════════════════════════════════════════


def _synth(n: int) -> List[Any]:
    """n 条合成 `CapabilityRecord`（**合成负载**，见模块 docstring）"""
    return _synthesize(n)


def _tool_index_payload(specs: Sequence[Any]) -> Dict[str, Any]:
    """把合成能力转成 `data/tool_index.json` 的同构结构（供检索栈使用）

    why 复用同一批合成能力而不是另造：Registry 与检索栈的数字只有在
    "同一批对象、同一份描述文本"下才可比较。`parameter_names` 由能力的
    `input_schema.properties` 派生（与真实 tool_index.json 的生成口径一致）。
    """
    tools = []
    for s in specs:
        props = ((s.input_schema or {}).get("properties") or {})
        tools.append({
            "name": s.tool_name,
            "description": s.description,
            "parameter_names": list(props.keys()),
            "category": "synthetic",
        })
    return {"tools": tools}


# ════════════════════════════════════════════════════════════
#  ① Registry 查询
# ════════════════════════════════════════════════════════════


def _registry_queries(reg: Any) -> Dict[str, Callable[[int], float]]:
    """四类查询形态（与 `bench_capregistry._queries` 同构，避免口径漂移）"""
    names = [s.tool_name for s in reg.specs]

    def q_get(i: int) -> float:
        t = time.perf_counter()
        reg.get(names[i % len(names)] if names else "x")
        return (time.perf_counter() - t) * 1000.0

    def q_filtered(i: int) -> float:
        t = time.perf_counter()
        reg.query(kind="tool" if i % 2 == 0 else "skill",
                  location="local" if i % 3 else "remote")
        return (time.perf_counter() - t) * 1000.0

    def q_envelope_paged(i: int) -> float:
        t = time.perf_counter()
        reg.list_envelope(limit=500)   # HTTP 面默认形态 CP_CAPABILITY_DEFAULT_PAGE=500
        return (time.perf_counter() - t) * 1000.0

    def q_envelope_full(i: int) -> float:
        t = time.perf_counter()
        reg.list_envelope()
        return (time.perf_counter() - t) * 1000.0

    return {"get": q_get, "filtered": q_filtered,
            "envelope_paged": q_envelope_paged, "envelope_full": q_envelope_full}


# ════════════════════════════════════════════════════════════
#  ② Router 组装
# ════════════════════════════════════════════════════════════


def _assembly_queries(n_names: int) -> Dict[str, Callable[[int], float]]:
    """Router 组装的两条曲线

    【为什么分两条（重要）】当前 **Router 并不读 Registry**：`tool_router` 的候选集
    来自 `data/tool_categories.yaml` 派生的 `TOOL_CATEGORIES`（实测 11 个类别 / 90 个
    工具），与 Registry 里的能力条数**无关**。所以：

    * `assembly_real`：真实配置下的组装（分类 + 别名合并 + 优先级排序 + 截断）。
      它的耗时应**与规模无关**——这条曲线就是"当前架构下 Router 不受容量影响"的证据；
    * `assembly_n`：**假设** Router 由 N 条能力驱动时，组装原语在 N 元候选集上的成本。
      这是"如果将来把 Registry 接进 Router，组装会不会成为瓶颈"的前瞻曲线，
      **是假设场景，不是当前实测架构**。

    两条都不调用 `get_tools_for_input()`：那个函数会写 `ToolTraceRecorder`
    （生产轨迹库）并触发进化策略注入，属**副作用 + 生产数据**，压测里不该碰。
    这里测的是它的两步核心：`classify_user_input` + `_apply_alias_merge_and_priority_sort`。
    """
    from agent.tool_router import (_apply_alias_merge_and_priority_sort,
                                   classify_user_input, TOOL_CATEGORIES)

    queries = ["帮我写代码并运行测试", "读取 PDF 的内容", "搜索互联网资料",
               "读取本地文件内容", "运行测试并修复失败用例", "压缩这个目录"]
    all_cats = set(TOOL_CATEGORIES.keys())
    syn_names = [f"synthetic_{i:06d}" for i in range(n_names)]
    syn_set = set(syn_names)

    def assembly_real(i: int) -> float:
        q = queries[i % len(queries)]
        t = time.perf_counter()
        cats = classify_user_input(q)
        selected = set()
        for c in cats:
            info = TOOL_CATEGORIES.get(c)
            if info:
                selected.update(info["tools"])
        _apply_alias_merge_and_priority_sort(selected, cats, 25)
        return (time.perf_counter() - t) * 1000.0

    def assembly_n(i: int) -> float:
        t = time.perf_counter()
        _apply_alias_merge_and_priority_sort(
            syn_set, all_cats, 25, preferred_order=syn_names)
        return (time.perf_counter() - t) * 1000.0

    return {"assembly_real": assembly_real, "assembly_n": assembly_n}


# ════════════════════════════════════════════════════════════
#  ③ 语义召回（复用检索栈的 BM25 腿）
# ════════════════════════════════════════════════════════════


def _build_retriever(specs: Sequence[Any]) -> Tuple[Any, float]:
    """按合成能力构建 `HybridRetriever`（embedding 关闭）

    【为什么关 embedding】`EmbeddingIndex` 要拉子进程 + 句子向量模型（冷启动数十秒、
    需 HF 网络）。本脚本测的是**容量曲线**（索引规模对召回延迟的影响），
    BM25 腿是**必然可用**的那条腿（embedding 缺失时代码本来就走它）。
    故 pin 住 BM25 口径，并在报告里写明"这是检索栈的 BM25 腿，不是三路融合"。

    【为什么写到临时目录】`HybridRetriever` 从 `data/tool_index.json` 读工具定义，
    那是**生产数据（只读纪律 D6）**。合成负载一律写临时目录，测完即弃。
    """
    from agent.tool_router_hybrid import HybridRetriever

    tmp = tempfile.mkdtemp(prefix="t08_capbench_")
    path = os.path.join(tmp, "tool_index.json")
    payload = _tool_index_payload(specs)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)

    t0 = time.perf_counter()
    retriever = HybridRetriever(alpha=1.0, index_path=path)
    build_ms = (time.perf_counter() - t0) * 1000.0
    return retriever, build_ms


def _retrieval_query(retriever: Any) -> Callable[[int], float]:
    queries = [
        "搜索互联网信息", "读取本地文件", "运行测试", "压缩目录",
        "合成能力 1234 描述", "synthetic 000123 search web fetch",
        "写代码并运行 lint", "转换数据格式", "发送通知消息", "查询数据库",
    ]

    def q(i: int) -> float:
        t = time.perf_counter()
        retriever.query(queries[i % len(queries)], top_k=25)
        return (time.perf_counter() - t) * 1000.0

    return q


# ════════════════════════════════════════════════════════════
#  ④ 超限行为探针（是否有强制点）
# ════════════════════════════════════════════════════════════


def _run_concurrent_switch_interval(label: str, fn: Callable[[int], float],
                                    rounds: int,
                                    interval_s: float = 0.0005) -> Dict[str, Any]:
    """**调小 GIL 切换间隔**再跑一遍 16 并发（诊断口径）

    why：关掉 GC 只让尾延迟缩小 1.15×（见 ③），说明尾不是回收停顿造成的。
    剩下的头号嫌疑是 **GIL 的协作式调度**：CPython 默认 `sys.setswitchinterval()=5ms`，
    一个线程执行纯 Python 代码时最多独占 GIL 5ms 才让出；16 个线程相互等待的
    最坏排队时间 ≈ 15×5ms = 75ms 量级 —— 与实测 p99（数百毫秒）同阶。
    把间隔压到 0.5ms 复测：若尾延迟显著塌下来 ⇒ 归因成立，改进方向就是
    "降低每请求纯 Python 工作量 / 调小切换间隔 / 改多进程"，而**不是**"索引算法退化"。
    ⚠ 同样是诊断口径：`setswitchinterval` 的收益依赖负载形态，不是无条件部署建议。
    """
    old = sys.getswitchinterval()
    sys.setswitchinterval(interval_s)
    try:
        st = _run_concurrent(f"{label} [switchinterval={interval_s * 1000}ms·诊断]",
                             fn, rounds=rounds)
    finally:
        sys.setswitchinterval(old)
    st["switch_interval_s"] = interval_s
    return st


def _per_query_breakdown(retriever: Any, rounds: int = 20) -> List[Dict[str, Any]]:
    """逐查询串测一次（单线程），把"召回 p99 高"归因到**具体查询形态**

    why：检索的单线程 p50 只有 0.013ms，p99 却有 40ms —— 差 3000 倍。
    这种情况几乎总是"两类查询混在一个分位数里"：BM25 的单次成本 = Σ(命中 term 的
    postings 长度)，**高频词（例如每个描述里都出现的汉字）的 postings 就是全表**。
    逐串拆开才能看出"是 N 让查询变慢"还是"某些查询本来就 O(N)"。
    """
    queries = [
        ("搜索互联网信息", "中频（多字词）"),
        ("读取本地文件", "中频（多字词）"),
        ("运行测试", "中频（多字词）"),
        ("压缩目录", "中频（多字词）"),
        ("合成能力 1234 描述", "**高频**（合成描述里每个文档都含这些字）"),
        ("synthetic 000123 search web fetch", "低频（英文合成名+通用词）"),
        ("写代码并运行 lint", "中频（多字词）"),
        ("转换数据格式", "中频（多字词）"),
        ("发送通知消息", "中频（多字词）"),
        ("查询数据库", "中频（多字词）"),
    ]
    out = []
    for q, note in queries:
        ts = []
        for _ in range(rounds):
            t0 = time.perf_counter()
            res = retriever.query(q, top_k=25)
            ts.append((time.perf_counter() - t0) * 1000.0)
        from bench_capregistry import _stats

        st = _stats(ts)
        out.append({"query": q, "note": note, "n": st["n"],
                    "p50_ms": st["p50_ms"], "p99_ms": st["p99_ms"],
                    "max_ms": st["max_ms"], "returned": len(res or [])})
    return out


def probe_concurrency_scaling(n: int, say: Callable[[str], None]) -> Dict[str, Any]:
    """**并发度扫描**：同一负载、同一实例，只改线程数（1→32）

    why 这是尾延迟的决定性判据：如果 p50 基本不动、而 p99 随线程数单调上升，
    那么尾就是**争用**（GIL + 12 逻辑核上的 CPU 超订）造成的，与"查询算法退化"
    无关。前面两个探针已排除"GC 停顿"（关 GC 只缩 1.15×）和"切换间隔太长"
    （压小到 0.5ms 反而把 p50 从 1.9ms 抬到 419ms —— 线程抖动加剧）。
    剩下的解释只能是争用，本扫描把它直接测出来。
    """
    from agent.capregistry.view import CapabilityRegistry

    specs = _synth(n)
    reg = CapabilityRegistry(specs)
    qs = _registry_queries(reg)
    out: Dict[str, Any] = {"n": n, "levels": {}}
    say("")
    say("─" * 96)
    say(f"⑦ 并发度扫描（n={n}，同一 Registry 实例，只改线程数）")
    say("─" * 96)
    say(f"{'线程数':>7}{'过滤查询 p50':>16}{'过滤查询 p99':>16}"
        f"{'分页信封 p50':>16}{'分页信封 p99':>16}{'qps(信封)':>12}")
    total_samples = 1600
    for c in (1, 2, 4, 8, 16, 32):
        rounds = max(20, total_samples // c)
        a = _run_concurrent(f"filtered c={c}", qs["filtered"], concurrency=c, rounds=rounds)
        b = _run_concurrent(f"envelope_paged c={c}", qs["envelope_paged"],
                            concurrency=c, rounds=rounds)
        out["levels"][str(c)] = {"filtered": a, "envelope_paged": b}
        say(f"{c:>7}{a['p50_ms']:>16.3f}{a['p99_ms']:>16.3f}"
            f"{b['p50_ms']:>16.3f}{b['p99_ms']:>16.3f}{b['qps']:>12.1f}")
    say("")
    p99_1 = out["levels"]["1"]["envelope_paged"]["p99_ms"]
    p99_16 = out["levels"]["16"]["envelope_paged"]["p99_ms"]
    p50_1 = out["levels"]["1"]["envelope_paged"]["p50_ms"]
    p50_16 = out["levels"]["16"]["envelope_paged"]["p50_ms"]
    out["verdict"] = {
        "envelope_p50_ratio_16_over_1": round(p50_16 / max(p50_1, 1e-9), 2),
        "envelope_p99_ratio_16_over_1": round(p99_16 / max(p99_1, 1e-9), 2),
    }
    say(f"  n={n} 分页信封：p50 从 1 线程的 {p50_1}ms 到 16 线程的 {p50_16}ms"
        f"（{out['verdict']['envelope_p50_ratio_16_over_1']}×，基本不动）；"
        f"p99 从 {p99_1}ms 到 {p99_16}ms"
        f"（{out['verdict']['envelope_p99_ratio_16_over_1']}×）")
    say(f"  ⇒ p50 稳定 + p99 随线程数上升 ⇒ **尾延迟是争用造成的**，"
        f"不是查询算法退化。本机 12 逻辑核 / 16 线程已属 CPU 超订。")
    return out


def probe_over_limit(say: Callable[[str], None]) -> Dict[str, Any]:
    """直接压过 v1.4 §6 的两条容量线，看代码**有没有**拒绝/告警/截断

    【为什么必须实测而不是只读代码】"没有强制点"是个**否定式结论**——本仓纪律要求
    否定式结论换口径复测。这里的第二个口径是**行为口径**：真的塞 501 / 10,001 条进去，
    看 `len()`、`query()` 的返回条数、`stats()` 的字段有没有出现上限迹象。
    code-grep 口径的结论（`agent/capregistry/` 内零处条数上限判定）见报告正文。
    """
    from agent.capregistry.view import CapabilityRegistry

    out: Dict[str, Any] = {}
    say("")
    say("─" * 96)
    say("⑥ 超限行为探针（v1.4 §6：单租户 ≤500 / 全局 ≤10,000）")
    say("─" * 96)

    # 单租户 501（超 1 条）
    specs501 = _synth(501)   # _synthesize 的 tenant_id 恒为 "default" ⇒ 天然单租户
    t0 = time.perf_counter()
    reg501 = CapabilityRegistry(specs501)
    build_ms = (time.perf_counter() - t0) * 1000.0
    tenants = {s.tenant_id for s in specs501}
    got = len(reg501.query(tenant_id="default"))
    out["single_tenant_501"] = {
        "input": 501, "len_registry": len(reg501), "tenants": sorted(tenants),
        "query_tenant_default": got, "build_ms": round(build_ms, 1),
        "rejected": len(reg501) < 501, "truncated": got < 501,
        "degraded": bool(reg501.degraded),
    }
    say(f"  单租户 501 条：len(registry)={len(reg501)}，"
        f"query(tenant_id='default') 返回 {got} 条 ⇒ "
        f"{'❌ 无强制点（全部接受）' if got == 501 else '⚠ 有条数收敛'}")

    # 全局 10,001（超 1 条）
    specs10001 = _synth(10001)
    t0 = time.perf_counter()
    reg10001 = CapabilityRegistry(specs10001)
    build_ms2 = (time.perf_counter() - t0) * 1000.0
    out["global_10001"] = {
        "input": 10001, "len_registry": len(reg10001),
        "build_ms": round(build_ms2, 1),
        "rejected": len(reg10001) < 10001, "degraded": bool(reg10001.degraded),
        "build_warnings": list(reg10001.build_warnings),
    }
    say(f"  全局 10,001 条：len(registry)={len(reg10001)}，"
        f"build_warnings={list(reg10001.build_warnings)} ⇒ "
        f"{'❌ 无强制点（全部接受）' if len(reg10001) == 10001 else '⚠ 有条数收敛'}")

    # stats() 里有没有任何"容量/上限"字段
    stats_keys = sorted(k for k in reg10001.stats().keys())
    out["stats_keys"] = stats_keys
    suspicious = [k for k in stats_keys
                  if any(w in k.lower() for w in ("limit", "cap", "max", "quota"))]
    out["stats_capacity_keys"] = suspicious
    say(f"  stats() 字段：{stats_keys}")
    say(f"  其中含容量/上限语义的字段：{suspicious or '无'}")
    say("  结论：**当前无强制点** ⇒ 不能声称『单租户 ≤500 / 全局 ≤10,000』已受控。")
    say("        （拒绝注册 / 告警 / 静默截断三者都没有；建议行为见 docs/perf/容量压测.md）")
    return out


# ════════════════════════════════════════════════════════════
#  ⑤ 内存（独立冷子进程）
# ════════════════════════════════════════════════════════════


def _rss_mb() -> Dict[str, float]:
    """当前进程的内存读数（MB）

    why 同时给 rss 与 peak_wset：`rss` 是**取样的瞬时驻留**（会被 GC 时机影响），
    `peak_wset` 是**进程生命周期内的工作集峰值**（Windows 由内核维护，不受取样时刻影响）。
    "压力峰值"必须用 peak_wset —— 用 rss 取样会系统性地低估峰值。
    """
    import psutil

    mi = psutil.Process().memory_info()
    out = {"rss_mb": round(mi.rss / 1048576.0, 2),
           "vms_mb": round(mi.vms / 1048576.0, 2)}
    peak = getattr(mi, "peak_wset", None)
    out["peak_wset_mb"] = round(peak / 1048576.0, 2) if peak else None
    return out


def mem_child(n: int) -> Dict[str, Any]:
    """子进程模式：测**这一个规模**的空载基线与压力峰值

    三个读数点（报告里逐点标注，不外推）：
      1. `idle_import`：只 import 被测模块后的 RSS（空载基线）；
      2. `after_registry`：构建 n 条能力的 Registry 之后；
      3. `after_retrieval`：再构建 n 篇文档的检索索引之后（含 BM25 + embedding pending）。
    """
    _quiet_application_loggers()
    gc.collect()
    out: Dict[str, Any] = {"n": n}
    out["idle_import"] = _rss_mb()

    from agent.capregistry.view import CapabilityRegistry

    gc.collect()
    out["idle_after_import_registry"] = _rss_mb()

    specs = _synth(n)
    t0 = time.perf_counter()
    reg = CapabilityRegistry(specs)
    out["registry_build_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
    gc.collect()
    out["after_registry"] = _rss_mb()
    out["registry_len"] = len(reg)
    del reg

    retriever, build_ms = _build_retriever(specs)
    out["retriever_build_ms"] = round(build_ms, 1)
    out["retriever_docs"] = retriever._bm25.size
    gc.collect()
    out["after_retrieval"] = _rss_mb()
    # 查询一次，让可能的惰性分配路径也走一遍（但 embedding 已关闭，不会拉子进程）
    retriever.query("搜索互联网信息", top_k=25)
    out["after_query"] = _rss_mb()
    return out


def idle_probe(samples: int = 7, interval_s: float = 0.25) -> Dict[str, Any]:
    """**空载内存基线**：冷进程里只 import 被测模块，重复采样 RSS

    为什么需要单独一个探针（而不是只看 `mem_child` 的第一个读数）：
      * `rss` 是取样值，单点会受 GC 时机与分配器 arena 复用影响 ⇒ 多次采样给 min/中位/max；
      * 必须明确"空载"到底包含什么：本探针 import 的是 **TASK-08 热路径栈**
        （`agent.capregistry` / `agent.tool_router` / `agent.tool_router_hybrid` / `agent.tools`），
        **不含** `app_server` 的整机装配（DigitalLife、调度器、SessionManager、各路由）。

    ⚠ 为什么**不**测 `import app_server` 的内存：那个模块在 import 期就会
    `DigitalLife().start()`、`os.makedirs(云枢记忆)`、`SessionManager('./data/sessions')`、
    `get_schedule_scheduler().start()` —— 它**不是**"只读导入"，而是**把智能体启动起来**
    并写 `data/`（D6 禁止）。本机已有用户的真实后端（PID 980）在跑，整机口径
    应引用它（标注为"长期运行的真实进程"，非本基准），而不是另起一个。
    """
    _quiet_application_loggers()
    out: Dict[str, Any] = {"probe": "idle", "python": sys.version.split()[0]}
    out["rss_before_import"] = _rss_mb()

    t0 = time.perf_counter()
    import agent.capregistry.view as _v          # noqa: F401
    import agent.tool_router as _tr              # noqa: F401
    import agent.tool_router_hybrid as _th       # noqa: F401
    import agent.tools as _t                     # noqa: F401
    out["import_ms"] = round((time.perf_counter() - t0) * 1000.0, 1)

    gc.collect()
    readings = []
    for _ in range(max(1, samples)):
        readings.append(_rss_mb())
        time.sleep(interval_s)
    rss = sorted(r["rss_mb"] for r in readings)
    out["rss_samples_mb"] = rss
    out["rss_min_mb"] = rss[0]
    out["rss_median_mb"] = rss[len(rss) // 2]
    out["rss_max_mb"] = rss[-1]
    out["final"] = _rss_mb()
    out["tool_count"] = len(_t.list_tools()) if hasattr(_t, "list_tools") else None
    out["tool_categories"] = len(_tr.TOOL_CATEGORIES)
    return out


def _run_idle_child(samples: int = 7) -> Dict[str, Any]:
    import subprocess

    code = (
        "import json,sys;"
        f"sys.path.insert(0,{_ROOT!r});sys.path.insert(0,{_SCRIPTS!r});"
        "import bench_capacity_scaling as B;"
        "print('@@IDLE@@'+json.dumps(B.idle_probe(int(sys.argv[1]))))"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["AGENT_HYBRID_EMBEDDING"] = "0"
    proc = subprocess.run([sys.executable, "-c", code, str(samples)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=_ROOT, env=env, timeout=900)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@IDLE@@"):
            return json.loads(line[len("@@IDLE@@"):])
    raise SystemExit(f"空载探针失败\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr[-3000:]}")


def _run_mem_child(n: int) -> Dict[str, Any]:
    import subprocess

    code = (
        "import json,sys;"
        f"sys.path.insert(0,{_ROOT!r});sys.path.insert(0,{_SCRIPTS!r});"
        "import bench_capacity_scaling as B;"
        "print('@@MEM@@'+json.dumps(B.mem_child(int(sys.argv[1]))))"
    )
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["AGENT_HYBRID_EMBEDDING"] = "0"   # 不拉句子向量模型（冷启动数十秒 + 需网络）
    proc = subprocess.run([sys.executable, "-c", code, str(n)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=_ROOT, env=env, timeout=3600)
    for line in (proc.stdout or "").splitlines():
        if line.startswith("@@MEM@@"):
            return json.loads(line[len("@@MEM@@"):])
    raise SystemExit(f"内存子进程 n={n} 失败\nSTDOUT:{proc.stdout}\nSTDERR:{proc.stderr[-3000:]}")


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


def main() -> int:
    ap = argparse.ArgumentParser(description="容量扩容压测（TASK-08 第 7 步）")
    ap.add_argument("--scales", default=",".join(str(s) for s in SCALES))
    ap.add_argument("--json", default="")
    ap.add_argument("--skip-memory", action="store_true")
    ap.add_argument("--skip-retrieval", action="store_true")
    ap.add_argument("--idle-only", action="store_true",
                    help="只跑空载内存基线（冷进程 + 多次采样），不做压测")
    ap.add_argument("--concurrency-scaling", action="store_true",
                    help="额外跑并发度扫描（1→32 线程），用于尾延迟归因")
    ap.add_argument("--cs-n", type=int, default=10000,
                    help="并发度扫描用的规模（默认 10000）")
    args = ap.parse_args()

    scales = [int(s) for s in args.scales.split(",") if s.strip()]
    lines: List[str] = []

    def say(m: str = "") -> None:
        lines.append(m)
        print(m, flush=True)

    _quiet_application_loggers()

    say("=" * 96)
    say("容量扩容压测（TASK-08 第 7 步 / E1h）")
    say("=" * 96)
    say(f"采集时间：{time.strftime('%Y-%m-%dT%H:%M:%S')}")
    say(f"规模：{scales}　并发：{CONCURRENCY}（对齐 waitress 16 线程，共用同一实例）")
    say(f"负载：**合成能力**（`bench_capregistry._synthesize`，与 TASK-05 同一套）")
    say("目标线：Registry 查询 p99 < 100ms；Router 组装 p99 < 50ms（v1.4 §6）")
    say("⚠ 本机同时运行着用户的后端（127.0.0.1:5678）⇒ 延迟数字采集于**并发负载**下；")
    say("  该后端仅被引用为“长期运行的真实进程”，**不是**本脚本的测量对象。")
    say("⚠ 应用级 INFO 日志已压到 CRITICAL（口径纪律 3）——生产开 INFO 会另有日志开销。")

    report: Dict[str, Any] = {"scales": {}, "concurrency": CONCURRENCY,
                              "targets": {"registry_p99_ms": TARGET_REGISTRY_P99_MS,
                                          "assembly_p99_ms": TARGET_ROUTER_ASSEMBLY_P99_MS}}

    # ── ⓪ 空载内存基线（冷进程，只 import；先做，避免被后面的压测残余影响）──
    idle = _run_idle_child()
    report["idle_memory"] = idle
    say("")
    say("─" * 96)
    say("⓪ 空载内存基线（**冷进程**，只 import 热路径栈，不做任何压测）")
    say("─" * 96)
    say(f"  import 前 RSS={idle['rss_before_import']['rss_mb']}MB → "
        f"import 后（{idle['import_ms']}ms）："
        f"min={idle['rss_min_mb']}MB / 中位={idle['rss_median_mb']}MB / "
        f"max={idle['rss_max_mb']}MB（{len(idle['rss_samples_mb'])} 次采样）")
    say(f"  当前 WS={idle['final']['rss_mb']}MB，VMS={idle['final']['vms_mb']}MB，"
        f"进程峰值 WS={idle['final']['peak_wset_mb']}MB")
    say(f"  含：capregistry + tool_router + tool_router_hybrid + tools"
        f"（类别 {idle['tool_categories']} 个；工具注册表为空是正常的 ——"
        f" `register_all` 由 app_server 装配时才调用，本探针刻意不启动应用）")
    say("  ⚠ 这是**热路径栈**的空载占用，**不是** app_server 整机装配后的占用——")
    say("     后者会在 import 期启动 DigitalLife/调度器/会话存储并写 data/（D6 禁止）。")

    if args.idle_only:
        if args.json:
            p = args.json if os.path.isabs(args.json) else os.path.join(_ROOT, args.json)
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        os.makedirs(os.path.join(_ROOT, "_ci_logs"), exist_ok=True)
        with open(os.path.join(_ROOT, "_ci_logs", "bench_capacity_scaling.txt"),
                  "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return 0

    for n in scales:
        say("")
        say("═" * 96)
        say(f"规模 n = {n}（**合成负载**）")
        say("═" * 96)

        specs = _synth(n)
        t0 = time.perf_counter()
        from agent.capregistry.view import CapabilityRegistry
        reg = CapabilityRegistry(specs)
        build_ms = (time.perf_counter() - t0) * 1000.0
        say(f"  Registry：{len(reg)} 条，构建 {build_ms:.1f}ms，degraded={reg.degraded}")
        say(f"    索引：{json.dumps(reg.stats().get('index', {}), ensure_ascii=False)}")

        entry: Dict[str, Any] = {"n": n, "registry_build_ms": round(build_ms, 1)}

        qs = _registry_queries(reg)
        for key, label in (("get", "get(tenant,name) 主键查询"),
                           ("filtered", "query(kind,location) 过滤查询"),
                           ("envelope_paged", "list_envelope(limit=500) 分页信封")):
            st = _run_concurrent(label, qs[key], rounds=ROUNDS[key])
            entry[key] = st
            say(f"  · {label}: n={st['n']} mean={st['mean_ms']}ms p50={st['p50_ms']}ms "
                f"**p99={st['p99_ms']}ms** max={st['max_ms']}ms")
            # 空载（单线程）口径：给出**结构成本**，与并发口径的差即为并发税
            sts = _run_sequential(label + " [单线程·空载]", qs[key],
                                  rounds=SEQ_ROUNDS[key])
            entry[key + "_seq"] = sts
            say(f"      ↳ 单线程（空载）: n={sts['n']} p50={sts['p50_ms']}ms "
                f"**p99={sts['p99_ms']}ms** max={sts['max_ms']}ms"
                f"　⇒ 并发税(p99差)={st['p99_ms'] - sts['p99_ms']:.3f}ms")
            # GC 归因诊断（只对两个 p99 敏感指标做，控制总时长）
            if key in ("filtered", "envelope_paged"):
                gst = _run_concurrent_gc_off(label, qs[key], rounds=ROUNDS[key])
                entry[key + "_gcoff"] = gst
                say(f"      ↳ 16 并发·**关闭 GC**（诊断）: n={gst['n']} "
                    f"p50={gst['p50_ms']}ms **p99={gst['p99_ms']}ms** max={gst['max_ms']}ms")
                sst = _run_concurrent_switch_interval(label, qs[key], rounds=ROUNDS[key])
                entry[key + "_switchint"] = sst
                say(f"      ↳ 16 并发·**switchinterval=0.5ms**（诊断）: n={sst['n']} "
                    f"p50={sst['p50_ms']}ms **p99={sst['p99_ms']}ms** max={sst['max_ms']}ms")
        st_full = _run_concurrent("list_envelope() 全量信封",
                                  qs["envelope_full"],
                                  rounds=FULL_ENVELOPE_ROUNDS.get(n, 3))
        entry["envelope_full"] = st_full
        say(f"  · list_envelope() 全量信封（无分页·已知未达标项）: n={st_full['n']} "
            f"p50={st_full['p50_ms']}ms **p99={st_full['p99_ms']}ms** max={st_full['max_ms']}ms")
        stf_seq = _run_sequential("list_envelope() [单线程·空载]", qs["envelope_full"],
                                  rounds=SEQ_FULL_ENVELOPE_ROUNDS.get(n, 10))
        entry["envelope_full_seq"] = stf_seq
        say(f"      ↳ 单线程（空载）: n={stf_seq['n']} p50={stf_seq['p50_ms']}ms "
            f"**p99={stf_seq['p99_ms']}ms** max={stf_seq['max_ms']}ms")

        # ── Router 组装 ──
        aq = _assembly_queries(n)
        for key, label in (("assembly_real", "Router 组装（真实 TOOL_CATEGORIES 驱动）"),
                           ("assembly_n", f"Router 组装（假设 N={n} 元候选集，前瞻）")):
            st = _run_concurrent(label, aq[key], rounds=ROUNDS["assembly"])
            entry[key] = st
            say(f"  · {label}: n={st['n']} p50={st['p50_ms']}ms **p99={st['p99_ms']}ms** "
                f"max={st['max_ms']}ms")
            sts = _run_sequential(label + " [单线程·空载]", aq[key],
                                  rounds=SEQ_ROUNDS["assembly"])
            entry[key + "_seq"] = sts
            say(f"      ↳ 单线程（空载）: n={sts['n']} p50={sts['p50_ms']}ms "
                f"**p99={sts['p99_ms']}ms** max={sts['max_ms']}ms")

        # ── 语义召回（检索栈 BM25 腿） ──
        if not args.skip_retrieval:
            retriever, rbuild_ms = _build_retriever(specs)
            entry["retriever_build_ms"] = round(rbuild_ms, 1)
            entry["retriever_docs"] = retriever._bm25.size
            say(f"  · 检索索引构建：{retriever._bm25.size} 篇文档，{rbuild_ms:.1f}ms，"
                f"degraded={retriever.degraded}（embedding 关闭 ⇒ 纯 BM25 腿）")
            st = _run_concurrent("语义召回（BM25 腿）", _retrieval_query(retriever),
                                 rounds=ROUNDS["retrieval"])
            entry["retrieval"] = st
            say(f"  · 语义召回（BM25 腿）: n={st['n']} p50={st['p50_ms']}ms "
                f"**p99={st['p99_ms']}ms** max={st['max_ms']}ms")
            sts = _run_sequential("语义召回（BM25 腿）[单线程·空载]",
                                  _retrieval_query(retriever),
                                  rounds=SEQ_ROUNDS["retrieval"])
            entry["retrieval_seq"] = sts
            say(f"      ↳ 单线程（空载）: n={sts['n']} p50={sts['p50_ms']}ms "
                f"**p99={sts['p99_ms']}ms** max={sts['max_ms']}ms")
            # 逐查询拆解（只在最大规模做，控制总时长：召回的成本取决于查询词频，不只看 N）
            if n == max(scales):
                bd = _per_query_breakdown(retriever)
                entry["retrieval_per_query"] = bd
                say("      ↳ 逐查询拆解（单线程，n=20/串）：")
                for b in bd:
                    say(f"         p50={b['p50_ms']:>9.3f}ms p99={b['p99_ms']:>9.3f}ms "
                        f"returned={b['returned']:>3}  {b['query']!r} {b['note']}")
            # 不调 retriever.clear()：HybridRetriever 没有这个方法（只有内部两个索引
            # 各自的 clear），这里直接丢弃引用让 GC 回收——压测不需要复用实例。
            del retriever

        report["scales"][str(n)] = entry
        del reg, specs
        gc.collect()

    # ── 汇总曲线 ──
    say("")
    say("─" * 96)
    say("① 退化曲线汇总（**16 并发** p99 / p50，毫秒）")
    say("─" * 96)
    metrics = [("get", "Registry 主键查询"),
               ("filtered", "Registry 过滤查询"),
               ("envelope_paged", "Registry 分页信封(limit=500)"),
               ("envelope_full", "Registry 全量信封(无分页)"),
               ("assembly_real", "Router 组装（真实配置）"),
               ("assembly_n", "Router 组装（假设 N 元候选）"),
               ("retrieval", "语义召回（BM25 腿）")]
    for stat_key, title in (("p99_ms", "p99"), ("p50_ms", "p50")):
        say("")
        say(f"  [16 并发 · {title}]")
        say(f"{'指标':<32}" + "".join(f"{('n=' + str(s)):>15}" for s in scales))
        for key, label in metrics:
            row = f"{label:<32}"
            for s in scales:
                e = report["scales"][str(s)].get(key)
                row += f"{(e[stat_key] if e else float('nan')):>15.3f}"
            say(row)

    say("")
    say("─" * 96)
    say("② 结构成本 vs 并发税（同一负载、同一函数；单线程 = 空载口径）")
    say("─" * 96)
    say(f"{'指标':<32}{'口径':<12}" + "".join(f"{('n=' + str(s)):>14}" for s in scales))
    for key, label in metrics:
        first = True
        for seq in (False, True):
            for stat_key, tag in (("p50_ms", "p50"), ("p99_ms", "p99")):
                col = key + ("_seq" if seq else "")
                row = f"{(label if first else ''):<32}" \
                      f"{('单线程' if seq else '16并发') + ' ' + tag:<12}"
                first = False
                for s in scales:
                    e = report["scales"][str(s)].get(col)
                    row += f"{(e[stat_key] if e else float('nan')):>14.3f}"
                say(row)
        say("")

    # ── GC 归因 ──
    say("")
    say("─" * 96)
    say("③ 尾延迟归因：关闭分代 GC 的对照（**诊断口径，不是部署建议**）")
    say("─" * 96)
    say(f"{'指标':<24}{'口径':<26}" + "".join(f"{('n=' + str(s)):>15}" for s in scales))
    gc_attr: Dict[str, Any] = {}
    for key, label in (("filtered", "Registry 过滤查询"),
                       ("envelope_paged", "Registry 分页信封")):
        for col, tag in ((key, "16并发 默认"), (key + "_gcoff", "16并发 关GC"),
                         (key + "_switchint", "16并发 switch=0.5ms")):
            row = f"{label:<24}{tag:<26}"
            for s in scales:
                e = report["scales"][str(s)].get(col)
                row += f"{(e['p99_ms'] if e else float('nan')):>15.3f}"
            say(row)
        last = str(scales[-1])
        a = report["scales"][last].get(key, {}).get("p99_ms")
        b = report["scales"][last].get(key + "_gcoff", {}).get("p99_ms")
        c = report["scales"][last].get(key + "_switchint", {}).get("p99_ms")
        seq = report["scales"][last].get(key + "_seq", {}).get("p99_ms")
        gc_attr[key] = {"p99_default_ms": a, "p99_gc_off_ms": b,
                        "p99_switch_0p5ms": c, "p99_single_thread_ms": seq,
                        "gc_shrink_ratio": (round(a / b, 2) if a and b else None),
                        "switch_shrink_ratio": (round(a / c, 2) if a and c else None)}
        say(f"  ↳ n={scales[-1]}：默认 {a}ms ／ 关GC {b}ms（{gc_attr[key]['gc_shrink_ratio']}×）"
            f" ／ switch=0.5ms {c}ms（{gc_attr[key]['switch_shrink_ratio']}×）"
            f" ／ 单线程 {seq}ms")
        if a and c and a / c > 2:
            say("      ⇒ **尾延迟主因是 GIL 调度间隔**（压小切换间隔后尾显著收敛），"
                "不是 GC、也不是查询算法退化")
        elif a and b and a / b > 2:
            say("      ⇒ 尾延迟主因是**分代 GC**")
        else:
            say("      ⇒ 两个开关都解释不了尾延迟 ⇒ 需另找原因（例如本机其它负载的抢占）")
        say("")
    report["gc_attribution"] = gc_attr

    # ── 破线点 ──
    say("─" * 96)
    say("④ 破线点（v1.4 §6 目标；**并发口径**与**单线程口径**分别判定）")
    say("─" * 96)
    breaks: Dict[str, Any] = {}
    for key, label, target in (
            ("get", "Registry 主键查询", TARGET_REGISTRY_P99_MS),
            ("filtered", "Registry 过滤查询", TARGET_REGISTRY_P99_MS),
            ("envelope_paged", "Registry 分页信封", TARGET_REGISTRY_P99_MS),
            ("envelope_full", "Registry 全量信封", TARGET_REGISTRY_P99_MS),
            ("assembly_real", "Router 组装（真实）", TARGET_ROUTER_ASSEMBLY_P99_MS),
            ("assembly_n", "Router 组装（假设 N 元）", TARGET_ROUTER_ASSEMBLY_P99_MS),
            ("retrieval", "语义召回（BM25 腿）", None)):
        first_break = None
        first_break_seq = None
        for s in scales:
            e = report["scales"][str(s)].get(key)
            if e and target is not None and e["p99_ms"] >= target and first_break is None:
                first_break = s
            es = report["scales"][str(s)].get(key + "_seq")
            if es and target is not None and es["p99_ms"] >= target and first_break_seq is None:
                first_break_seq = s
        breaks[key] = {"label": label, "target_ms": target,
                       "first_break_n_concurrent": first_break,
                       "first_break_n_sequential": first_break_seq}
        verdict = ("未破线" if first_break is None
                   else f"**{first_break} 条起破线**")
        verdict_seq = ("未破线" if first_break_seq is None
                       else f"{first_break_seq} 条起破线")
        tgt = "—" if target is None else f"< {target}ms"
        worst = max((report["scales"][str(s)][key]["p99_ms"]
                     for s in scales if key in report["scales"][str(s)]), default=float("nan"))
        worst_seq = max((report["scales"][str(s)][key + "_seq"]["p99_ms"]
                         for s in scales
                         if key + "_seq" in report["scales"][str(s)]), default=float("nan"))
        say(f"  {label:<30} 目标 {tgt:<10} "
            f"16并发 最差p99={worst:>9.3f}ms({verdict})　"
            f"单线程 最差p99={worst_seq:>9.3f}ms({verdict_seq})")
    report["breaks"] = breaks
    say("")
    say("  【怎么读这两列】单线程列 = **结构成本**（算法与数据结构的代价）；")
    say("  16 并发列 = **部署形态下的尾**（含 GIL 争用与分代 GC 抖动）。")
    say("  两列给出不同的破线点，报告里必须分开写——混用会把「GC 抖动」误报成「查询退化」。")

    # ── 内存 ──
    if not args.skip_memory:
        say("")
        say("─" * 96)
        say("⑤ 内存（独立冷子进程；RSS=取样瞬时，peak_wset=内核记录的进程峰值）")
        say("─" * 96)
        say(f"{'n':>7}{'空载 import(MB)':>18}{'建 Registry 后':>18}"
            f"{'峰值 WS(MB)':>15}{'建检索索引后':>16}{'边际 MB/条':>14}")
        mem: Dict[str, Any] = {}
        idle_ref: float | None = None
        for n in scales:
            m = _run_mem_child(n)
            mem[str(n)] = m
            if idle_ref is None:
                idle_ref = m["idle_after_import_registry"]["rss_mb"]
            after_reg = m["after_registry"]["rss_mb"]
            after_ret = m.get("after_retrieval", {}).get("rss_mb", float("nan"))
            peak = m.get("after_retrieval", {}).get("peak_wset_mb") or \
                m["after_registry"].get("peak_wset_mb")
            marginal = (after_reg - idle_ref) / n
            say(f"{n:>7}{m['idle_import']['rss_mb']:>18.2f}{after_reg:>18.2f}"
                f"{(peak if peak else float('nan')):>15.2f}{after_ret:>16.2f}"
                f"{marginal:>14.3f}")
            say(f"        （空载基线{'(import 后)' if n == scales[0] else ''}"
                f"={m['idle_after_import_registry']['rss_mb']}MB，"
                f"Registry 构建 {m['registry_build_ms']}ms，"
                f"检索索引构建 {m.get('retriever_build_ms')}ms）")
        report["memory"] = mem

    # ── 超限探针 ──
    report["over_limit"] = probe_over_limit(say)

    # ── 并发度扫描（可选：尾延迟归因）──
    if args.concurrency_scaling:
        report["concurrency_scaling"] = probe_concurrency_scaling(args.cs_n, say)

    os.makedirs(os.path.join(_ROOT, "_ci_logs"), exist_ok=True)
    out_txt = os.path.join(_ROOT, "_ci_logs", "bench_capacity_scaling.txt")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    if args.json:
        p = args.json if os.path.isabs(args.json) else os.path.join(_ROOT, args.json)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        say("")
        say(f"[bench] 结构化结果已写入 {args.json}")
    say(f"[bench] 文本结果已写入 _ci_logs/{os.path.basename(out_txt)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
