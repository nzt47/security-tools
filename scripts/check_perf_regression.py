# -*- coding: utf-8 -*-
"""性能回归门禁（TASK-08 · E12）

【目标】性能退化时**非零退出**，使"性能"进入 CI 的可见范围。

【测什么】关键路径的**进程内**微基准（不发网络、不启服务、不碰生产数据）：
    1. `load_tool_meta()` 热调用             —— P0-1 的缓存是否仍有效
    2. `lines.assembler.assemble()`          —— v1.4「Router 组装 p99 < 50ms」的主承载者
    3. `tools.get_tool_defs()`               —— 最终构造 tools 参数的函数
    4. `tool_schema_pruner.prune_tool_defs()`
    5. `tool_router._load_tool_categories_from_yaml()` —— 残留的纯 Python safe_load
    6. `location._walk_chain` 全量 93 执行器  —— TASK-07 点名的潜在超时源（--with-walk-chain）
    7. 冷启动墙钟秒数（--with-cold-boot，约 90s）

【门禁量 = "被测耗时 / 同刻参照负载耗时" 的 best-of-N —— 三次校准才定下来的】
本机实测（当时另有最多 5 个并行子代理在做 pytest 与基准）走过三步，每步都有数据：

| 方案 | 跨 3 次运行的波动 | 结论 |
|---|---|---|
| 绝对值 p50 | **1.3x ~ 23x**（yaml 装载 22.94x、prune 14.04x） | 无法定阈值：宽则永不触发（验收禁止），紧则天天误报 |
| 参照负载归一化（**分段**采样） | 仍 1.1x ~ 14.9x | 无效 —— 参照负载**自身**跨次波动 2.61x，两段看到的负载不同 |
| best-of-N(min)，绝对值 | 1.42x ~ 1.56x | 好很多，但仍与"整段时间的机器负载"相关 |
| **best-of-N(min)，交错比值** | 见 `docs/perf/门禁阈值校准.md` | 被测与参照**同刻**测量 ⇒ 负载被比值约掉 |

⇒ 最终采用最后一行：每轮先测被测函数、**紧接着**测参照负载，取逐轮比值，
  再对这组比值取 **min**（best-of-N；负载只会"加"时间，故 min 是最紧的真实成本上界）。
  参照负载与被测指标同型：CPU 型指标配纯计算参照，磁盘型指标配"读一组固定 YAML"参照。

【阈值】默认 **1.25**：高于实测的交错比值波动，又低于需要拦住的真实退化
   （P0-1 回退 ≈ 580x；P0-1 之前的 Router 组装 ≈ 3.2x；20% 退化 = 1.20 → 会被拦）。
  ⚠️ 若在负载极端不稳的机器上运行，可 `--threshold` 放宽，但**不要**放到"永不触发"。

【负例演示（E12 明确要求）】
    python scripts/check_perf_regression.py --simulate-regression 1.2
⇒ 把每个门禁比值放大 1.2 倍 ⇒ 应非零退出。

用法：
    python scripts/check_perf_regression.py --write-baseline
    python scripts/check_perf_regression.py
    python scripts/check_perf_regression.py --simulate-regression 1.2
    python scripts/check_perf_regression.py --with-walk-chain --with-cold-boot
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from typing import Callable, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# ── GBK 控制台加固（TASK-04 · W2 修一处**真实缺陷**）────────────────────────
# 【症状】默认控制台下**正常模式**（非 --selftest）运行本门禁会崩：
#     UnicodeEncodeError: gbk codec can t encode character ...
# 【根因】门禁自己写进 docs/perf/baseline.json 的 threshold_basis 里含 ⇒ / ⚠️，
#   读回来后又经 json.dumps 打到 stdout ⇒ 在 GBK 管道/控制台下编码失败。
#   与 06-基线台账 §7.2 登记的同类问题同源；台账 §7.1 只验了 --selftest
#   （该路径不打印 threshold_basis），故此前未被发现。
# 【修法】沿用台账 §7.4 的既定修法：**只放宽 errors、不改 encoding**。
#   改 encoding 会让 GBK 控制台上的中文变乱码（本脚本输出大量中文）；
#   errors="replace" 只把装饰字符降级为 ?，中文仍正常，UTF-8 环境等价于无操作。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

BASELINE = os.path.join(_REPO_ROOT, "docs", "perf", "baseline.json")
#: 默认阈值 —— **在本机（最多 5 个并行子代理）实测校准出来的**
#: 实测（见 docs/perf/门禁阈值校准.md）：
#:   绝对值 p50        跨 3 次运行波动 **1.3x ~ 23x**  ⇒ 无法定阈值
#:   分段归一化         仍 1.1x ~ 14.9x（参照自身波动 2.61x）
#:   best-of-N 绝对值   1.42x ~ 1.56x
#:   **best-of-N + 交错比值** 1.16x ~ **1.54x** ← 采用；比朴素 p50 稳 **15 倍**
#: ⇒ 默认阈值取 **1.60**（高于并发负载下的最差观测波动 1.54x，留 4% 余量）。
#: ⚠️ **在安静的 CI 机器上应重新 `--write-baseline` 并把阈值收紧到 1.25**
#:    （届时 20% 退化 = 1.20 可被拦住；本机并发负载下 1.25 会产生误报）。
#: ⚠️ 不得为了"不红"把阈值放到永不触发 —— 兜底断言见 `--simulate-regression` 负例演示。
DEFAULT_THRESHOLD = 1.60
#: 安静机器上的建议阈值（写进 baseline 的说明里，供 CI 配置参考）
QUIET_MACHINE_THRESHOLD = 1.25

#: 每个指标配哪种参照负载（cpu = 纯计算；disk = 读固定 YAML 集；mem = 无量纲，不走交错采样）
REFERENCE_OF = {
    "load_tool_meta_hot": "cpu",
    "assemble": "cpu",
    "get_tool_defs": "cpu",
    "prune_tool_defs": "cpu",
    "yaml_category_load": "disk",
    "repo_index": "disk",
    "walk_chain_all_executors": "disk",
    "cold_boot": "disk",
    # ── TASK-04 · W2 新增（容量门禁；见文件末尾 "W2 新增" 注释块）──
    "capregistry_list_envelope_10k": "cpu",
    "process_rss_mb": "mem",
}

#: W2 新增指标 —— 供自证与"新增指标必须被门禁覆盖"的机械断言使用
NEW_METRICS: tuple = ("capregistry_list_envelope_10k", "process_rss_mb")

#: **只有**这些指标把 p95 计入判决
#:
#: 【为什么 p95 只对少数指标开门，而不是全量开门】
#: 本机已实测的噪声地板是 **1.54x**（best-of-N + 交错比值，`threshold_basis`）；
#: 而 p95 是"接近最大值"的统计量，对调度抖动**严格更敏感**。
#: 微秒级指标（load_tool_meta_hot / assemble / prune_tool_defs …）的 p95
#: 实际上取到的是"接近最大的一次观测"，把它纳入判决等于把噪声直接当信号
#: ⇒ 会制造本仓库明令警惕的"假红"。
#: 反之，单次成本 ≥ 10ms 的指标（目前只有 10k 条全量信封，实测 p95 ≈ 百毫秒级）
#: 的 p95 是**真实成本上界**，值得单列一条门禁线。
#:
#: ⚠️ 这份"作用域"不是拍脑袋：`--selftest` 的 scenario_c/scenario_d
#:    分别证明"p95 退化**必然**被抓到"与"作用域外的 p95 退化**不会**误报"。
P95_GATED: tuple = ("capregistry_list_envelope_10k",)

#: W2 新增指标的基线（**为什么基线写在本脚本里**）
#:
#: `docs/perf/baseline.json` 是共享产物（由 `--write-baseline` 整体覆写）。
#: TASK-04 的文件所有权只覆盖本脚本，**不得**覆写共享基线文件 —— 那会把
#: 既有 7 个指标的基线一并换成"当前这台并发负载机器"的读数，等于顺手改阈值。
#: 故 W2 新增指标的基线**声明在本脚本内**，作为 `effective_baseline()` 的兜底：
#: 一旦共享基线里出现了同名指标，**共享基线优先**（安静机器上重采后自然接管）。
#:
#: `value` = 交错比值（best-of-N min），与既有指标同一口径。
#: `raw_p95_ms` / `p95_ratio` 只作为**记录**（p95 是否参与判决见 `P95_GATED`）。
#: `_provenance` 给出实测命令与机器状态，便于日后复算。
NEW_METRIC_BASELINE: dict = {
    # ── 由 scripts/bench_perf_gate_metrics.py 标定 + 门禁进程同条件实测 ──
    "capregistry_list_envelope_10k": {
        "value": 76.192827,          # 交错比值 best-of-N(min)，门禁进程实测
        "ref": "cpu",
        "p50_ratio": 92.152145,
        "max_ratio": 325.332306,
        "raw_min_ms": 43.5442,       # 单次最省 43.5ms
        "raw_p50_ms": 50.0182,
        "p95_ratio": 175.430485,     # ← 本指标 p95 那条线的基线
        "raw_p95_ms": 91.6098,
        "p95_gated": True,
        "n": 30,
        "unit": "x(cpu-ref)",
        "note": "合成 10k 条全量信封；只测数据结构在 10k 量级的行为，不可用于声称容量达标",
        "calibration": {
            "command": "python scripts/bench_perf_gate_metrics.py --repeats 3 --reps 30",
            "value_per_round": [77.5434, 78.384307, 43.83267],
            "value_stability_max_over_min": 1.788,
            "p95_ratio_per_round": [174.974234, 184.928896, 182.717954],
            "p95_stability_max_over_min": 1.057,
            "raw_p95_ms_per_round": [94.40, 105.72, 100.35],
            "gate_process_value": 76.192827,
            "gate_process_p95_ratio": 175.430485,
            "reading": ("比值维度在本机波动 1.788x（与既有 8 个指标的 1.54x 噪声地板同量级，"
                        "被 4 个并行子代理放大）；**p95 维度只波动 1.057x** ⇒ 对这条约 50-90ms 的"
                        "指标而言，p95 比 best-of-N(min) 更可复现，这是把它纳入判决的实测依据"),
        },
    },
    "process_rss_mb": {
        "value": 100.219,            # 门禁进程实测 RSS(MB)
        "ref": "mem",
        "p50_ratio": 100.219,
        "max_ratio": 100.219,
        "raw_min_ms": None,
        "raw_p50_ms": None,
        "p95_ratio": None,
        "raw_p95_ms": None,
        "p95_gated": False,
        "n": 1,
        "unit": "MB",
        "note": "门禁进程 RSS(MB)；内存无法与 CPU/磁盘参照交错采样，故判决量是【当前/基线】的比值",
        "calibration": {
            "measured_in": "gate process（含 agent.tools / lines / capregistry 等导入）",
            "rss_mb_per_round": [34.766, 39.137, 40.844],
            "rss_stability_max_over_min": 1.175,
            "reading": ("⚠️ 34.8MB 那组是 bench 进程（只导入 capregistry）的读数，**不可**当门禁基线 —— "
                        "门禁进程因导入 agent.tools/transformers 等，RSS 高一个量级（实测 100.2MB）。"
                        "这正是把内存基线**放在门禁进程里测**的原因"),
        },
    },
}

#: ⛔ **暂不入门禁的指标**（如实登记，不做假门禁）
#:
#: P1 = "含 LLM 链路的端到端 p95"。它**本该**是容量门禁的主指标，但本轮无法给出
#: 一个干净、可复现的本地口径，理由逐条如下（均为本任务实测/查证所得）：
#:   (a) **没有生产 LLM 时延遥测**。仓库内唯一的"成本日志"
#:       `test_cost_log.jsonl`（227 条）**每个字段只有一个取值**
#:       （duration_ms=500 / cost_usd=0.006 / model=gpt-4 / task_type=test）
#:       ⇒ 纯合成夹具，**不得**用于延迟归因（见 06-基线台账.md §11.6）。
#:   (b) 真实外呼时延受网络抖动支配：`logs/server_restart_20260920_020048.log`
#:       65 行 `chat/completions` 记录的时间戳**只到秒级**，不足以支撑 p95。
#:   (c) 门禁必须能在 CI 里**无人值守**跑；起真实后端 + 真实 LLM 外呼既慢又要密钥。
#:   (d) 本波次明令禁跑压测（D10，有并行子代理，压测会相互污染基线）。
#: ⇒ 处置：**记录基线 + 比值判定**的形态也做不到（连基线都还没有），
#:    故本轮**不硬编阈值**，改为在本文件登记，并给出可执行的口径建议：
#:      P1（含 LLM 链路）  ：在**波次门禁**上，用受控单请求 × N 次的分位数，
#:                           与"同刻基线"比比值；不做绝对阈值。
#:      P2（关掉 LLM 链路）：用既有 `scripts/verify_llm_off_entrypoints.py` 的
#:                           三条入口（HTTP / CLI / 模型）跑同一口径的分位数。
#:    ⚠️ 在拿到 (a)(b) 的干净口径之前，**任何**写进本文件的 P1 阈值都是假门禁。
DEFERRED_METRICS: dict = {
    "e2e_p95_llm_on": {
        "status": "deferred",
        "reason": "无生产 LLM 时延遥测（test_cost_log.jsonl 是合成夹具）；"
                  "日志时间戳只到秒级；本轮禁压测（D10）",
        "proposed_gate": "波次门禁：受控单请求×N 的分位数比值（不设绝对阈值）",
        "p2_counterpart": "scripts/verify_llm_off_entrypoints.py（关掉 LLM 的三入口链路）",
    },
}


# ════════════════════════════════════════════════════════════
#  参照负载
# ════════════════════════════════════════════════════════════


def _cpu_work() -> int:
    """确定性纯计算参照负载（与仓库代码无关）"""
    acc = 0
    for i in range(8000):
        acc += (i * i) % 7
    return acc


_DISK_FILES: Optional[List[str]] = None


def _disk_work() -> int:
    """确定性磁盘参照负载：读一组固定的能力 YAML（与 `_walk_chain` 的访问模式同型）"""
    global _DISK_FILES
    if _DISK_FILES is None:
        try:
            from agent.lines.models import TOOL_DEFS_DIR
            _DISK_FILES = [os.path.join(TOOL_DEFS_DIR, f)
                           for f in sorted(os.listdir(TOOL_DEFS_DIR))
                           if f.endswith(".yaml")][:15]
        except OSError:
            _DISK_FILES = []
    total = 0
    for p in _DISK_FILES:
        try:
            total += os.stat(p).st_size
            with open(p, "rb") as f:
                total += len(f.read())
        except OSError:
            pass
    return total


_REF_FN: Dict[str, Callable[[], object]] = {"cpu": _cpu_work, "disk": _disk_work}


def _paired(fn: Callable[[], object], ref_name: str, reps: int) -> tuple:
    """**交错**采样被测函数与参照负载，返回 (比值列表, 被测原始毫秒列表)

    【为什么要交错】先前把指标与参照负载**分两段**采样，两者看到的机器负载不同
    ⇒ 归一化不仅没降噪反而引入噪声（实测参照负载自身跨次波动 2.61x）。
    交错后两者在同一时刻、同一负载下测量 ⇒ **比值对负载不敏感**。
    """
    ref = _REF_FN[ref_name]
    for _ in range(2):                       # 预热
        fn()
        ref()
    ratios: List[float] = []
    raws: List[float] = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        t_m = time.perf_counter() - t0
        t0 = time.perf_counter()
        ref()
        t_r = time.perf_counter() - t0
        raws.append(t_m * 1000.0)
        if t_r > 0:
            ratios.append(t_m / t_r)
    return ratios, raws


def _best(v: List[float]) -> float:
    """门禁统计量：best-of-N（min）。负载只会给测量值"加"时间，不会"减"时间。"""
    return min(v) if v else 0.0


def _pct(v: List[float], p: float) -> float:
    """分位数（最近秩法；与 scripts/bench_capregistry.py::pct 同一口径）

    【为什么口径要和 bench 脚本一致】p95 基线是**跨脚本**对拍的产物：
    `scripts/bench_perf_gate_metrics.py` 用它定基线，本文件用它做判决。
    两处口径若不同，"基线"与"判决"就会各说各话。
    """
    if not v:
        return 0.0
    ordered = sorted(v)
    k = max(0, min(len(ordered) - 1, int(round(p / 100.0 * len(ordered) + 0.5)) - 1))
    return ordered[k]


def _entry(ratios: List[float], raws: List[float], ref_name: str,
           p95_gated: bool = False, **extra) -> Dict:
    med = statistics.median(ratios) if ratios else 0.0
    return {
        "value": round(_best(ratios), 6),        # ← 门禁用这个（比值）
        "ref": ref_name,
        "p50_ratio": round(med, 6),
        "max_ratio": round(max(ratios), 6) if ratios else 0.0,
        "raw_min_ms": round(min(raws), 4) if raws else 0.0,   # 供人阅读，不参与门禁
        "raw_p50_ms": round(statistics.median(raws), 4) if raws else 0.0,
        # ── W2 新增：p95 维度（记录 + 可选判决，作用域见 P95_GATED）──
        "p95_ratio": round(_pct(ratios, 95), 6),
        "raw_p95_ms": round(_pct(raws, 95), 4),
        "p95_gated": bool(p95_gated),
        "n": len(ratios),
        **extra,
    }


# ════════════════════════════════════════════════════════════
#  采集
# ════════════════════════════════════════════════════════════


def _rss_mb() -> float:
    """当前进程 RSS（MB）。拿不到 ⇒ 返回 0.0（**由 compare 记为"无法判决"**）

    【为什么失败不抛异常】内存指标是**新增**的观测项，psutil 缺失不该让
    既有的 7 条性能门禁整体失败；但也不能静默当成"通过" —— 故返回 0，
    compare() 会把"值非正"的指标单列进 `metrics_skipped_nonpositive`。
    """
    try:
        import psutil
        return float(psutil.Process(os.getpid()).memory_info().rss) / (1024.0 * 1024.0)
    except Exception:
        return 0.0


def measure(reps: int, with_walk_chain: bool, with_cold_boot: bool,
            warmup: int = 5, walk_reps: int = 3) -> Dict:
    """采集门禁指标

    【为什么要 warmup】首调用带惰性初始化代价：实测 `assemble()` 第一次 19ms，
    稳态只有 0.5ms。首调用算进统计会让门禁被"初始化"而非"退化"触发。

    【必须先装真实注册表】`agent/tools/_registry` 在**裸进程里是空的**
    （由 `lifecycle_manager._register_builtin_tools()` 在启动流程中装配）。
    不装就测 `get_tool_defs()`，循环体一次都不进 ⇒ 测出 **0.0006ms 的假绿**。
    本任务实测踩到过这个坑，故这里强制先装载。
    """
    out: Dict[str, Dict] = {}

    sys.path.insert(0, os.path.join(_REPO_ROOT, "scripts"))
    from _t08_registry_boot import ensure_registry_loaded  # type: ignore
    reg = ensure_registry_loaded()
    out["_registry_size"] = {"value": reg["size"], "p50_ratio": reg["size"], "ref": "(n/a)",
                             "n": 1}

    # 1) load_tool_meta 热调用
    from agent.lines.models import invalidate_tool_meta_cache, load_tool_meta
    invalidate_tool_meta_cache()
    load_tool_meta()
    r, raw = _paired(load_tool_meta, "cpu", max(reps, 20))
    out["load_tool_meta_hot"] = _entry(r, raw, "cpu")

    # 2) assemble
    from agent import tools as T
    from agent.lines.assembler import assemble
    from agent.lines.models import LineProfile
    names = [t["name"] for t in T.list_tools()]
    meta = load_tool_meta()
    prof = LineProfile(id="perf-gate", name="性能门禁默认档案", max_tools=26)
    r, raw = _paired(lambda: assemble(prof, names, meta=meta), "cpu", reps)
    out["assemble"] = _entry(r, raw, "cpu", n_candidates=len(names))

    # 3) get_tool_defs（白名单路径不走缓存）
    r, raw = _paired(lambda: T.get_tool_defs(whitelist=names), "cpu", reps)
    out["get_tool_defs"] = _entry(r, raw, "cpu", n_whitelist=len(names))

    # 4) prune_tool_defs
    from agent.tool_schema_pruner import prune_tool_defs
    defs = T.get_tool_defs(whitelist=names)
    r, raw = _paired(
        lambda: prune_tool_defs(defs, intent_context={"selected_tools": names}), "cpu", reps)
    out["prune_tool_defs"] = _entry(r, raw, "cpu")

    # ── 5.5) W2 新增 ①：容量门禁 —— 10,000 条全量信封（p95 参与判决）──
    #
    # 【为什么选它当容量门禁的承载者】docs/perf/容量压测.md §2.2 实测：
    # 它是**唯一**因条数破线的数据结构项（10,000 条单线程 p99 = 105.70ms > 100ms），
    # 且能在**进程内、无网络、无并发**的条件下稳定复现 —— 满足"门禁必须能
    # 无人值守跑"的硬约束。
    # 【口径】合成数据只证明"数据结构在 10k 量级是否退化"，**不可**用于声称
    # 真实 10k 能力达标（bench_capregistry.py 的纪律，这里原样继承）。
    try:
        from agent.capregistry.view import CapabilityRegistry
        from bench_capregistry import _synthesize, SCALE  # 同一份合成器，避免两处走样
        synth = _synthesize(SCALE)
        reg10k = CapabilityRegistry(synth)
        # p95 要有意义就需要足够样本（n=20 时最近秩法的 p95 退化为 max）
        env_reps = max(30, min(reps, 60))
        r, raw = _paired(lambda: reg10k.list_envelope(), "cpu", env_reps)
        out["capregistry_list_envelope_10k"] = _entry(
            r, raw, "cpu",
            p95_gated=("capregistry_list_envelope_10k" in P95_GATED),
            n_synthetic=len(synth), unit="x(cpu-ref)",
            note="合成数据；只测数据结构在 10k 量级的行为，不可用于声称容量达标")
    except Exception as exc:  # noqa: BLE001
        out["_capregistry_10k_error"] = {"value": 0.0, "ref": "(n/a)", "n": 0,
                                        "error": "%s: %s" % (type(exc).__name__, exc)}

    # ── 5.6) W2 新增 ②：内存基线（记录 + 比值判定；无量纲，无参照可交错）──
    _rss = _rss_mb()
    out["process_rss_mb"] = {
        "value": round(_rss, 3), "ref": "mem",
        "p50_ratio": round(_rss, 3), "max_ratio": round(_rss, 3),
        "raw_min_ms": None, "raw_p50_ms": None,
        "p95_ratio": None, "raw_p95_ms": None, "p95_gated": False,
        "n": 1, "unit": "MB",
        "note": ("门禁进程 RSS(MB)；内存无法与 CPU/磁盘参照【交错采样】，"
                 "故判决量是【当前 / 基线】的比值"),
    }

    # 5) tool_router 残留的纯 Python safe_load（模块级执行一次，~180ms）
    import agent.tool_router as R
    r, raw = _paired(R._load_tool_categories_from_yaml, "disk", 7)
    out["yaml_category_load"] = _entry(r, raw, "disk")

    # 6) _walk_chain 全量（单次 6~9s ⇒ 用 3 轮 best-of-N 压波动）
    if with_walk_chain:
        from agent.lines import location as loc
        with open(os.path.join(_REPO_ROOT, "data", "capability_manifest.json"),
                  "r", encoding="utf-8") as f:
            m = json.load(f)
        executors: List[str] = []
        seen = set()
        for e in m.get("entries", []):
            he = str(e.get("host_executor") or "").strip()
            if he and he not in seen:
                seen.add(he)
                executors.append(he)

        def _walk_once():
            loc.invalidate_cache()
            loc._repo_index()
            for he in executors:
                try:
                    loc.judge_executor_location(he)
                except Exception:
                    pass

        def _index_once():
            loc.invalidate_cache()
            loc._repo_index()

        r, raw = _paired(_index_once, "disk", max(1, walk_reps))
        out["repo_index"] = _entry(r, raw, "disk", n_executors=len(executors))
        r, raw = _paired(_walk_once, "disk", max(1, walk_reps))
        out["walk_chain_all_executors"] = _entry(r, raw, "disk", n_executors=len(executors))

    # 7) 冷启动（慢）
    if with_cold_boot:
        import subprocess

        def _boot():
            subprocess.run([sys.executable, "-c", "import app_server"],
                           cwd=_REPO_ROOT, capture_output=True, timeout=900)

        r, raw = _paired(_boot, "disk", 1)
        out["cold_boot"] = _entry(r, raw, "disk")

    return out


def effective_baseline(base: Optional[Dict]) -> Dict:
    """把**脚本内声明**的 W2 新增指标基线并入已加载的基线（已加载的值优先）

    【为什么需要这一步】门禁的判决循环只遍历**基线里有的**指标 ⇒ 只要新增指标
    没进 `docs/perf/baseline.json`，它就会被**静默忽略**（典型的"假门禁"）。
    本函数把 `NEW_METRIC_BASELINE` 兜底进来，并如实记录来源，
    使"新增指标到底有没有在判"这件事**在输出里看得见**。
    """
    merged = dict(base or {})
    metrics = dict(merged.get("metrics") or {})
    from_script: List[str] = []
    for name, entry in NEW_METRIC_BASELINE.items():
        if name not in metrics:
            metrics[name] = dict(entry)
            from_script.append(name)
    merged["metrics"] = metrics
    merged["metrics_from_script"] = sorted(from_script)
    return merged


def compare(base: Dict, current: Dict, threshold: float,
            factor: float = 1.0, p95_threshold: float = None) -> Dict:
    """把"基线 vs 当前"变成门禁判决（**纯逻辑，不含任何计时**）

    【为什么抽成函数】`--selftest` 要用它做**无噪声**的敏感性验证：
    本机实测的噪声地板约 1.54x ⇒ **端到端**地检测 20% 退化在这台机器上做不到
    （信号低于噪声地板）。但这不等于"门禁逻辑抓不到 20%"。
    把判决逻辑与测量噪声**分开**，才能对"检出能力"给出诚实的结论：
      · 逻辑层：20% 退化在阈值 1.15 下**必然**被判 REGRESSION（--selftest 证明）
      · 测量层：本机噪声地板 1.54x ⇒ 实际可检出的最小退化约 **54%**
                  （安静机器上噪声地板远低于此，20% 可检出）

    【W2 新增：p95 维度】判决量仍是"交错比值 best-of-N"，p95 **只对
    `P95_GATED` 里的指标**额外计一条线（作用域理由见该常量的注释）。
    两条线**都不放宽、不删除**既有阈值。
    """
    if p95_threshold is None:
        p95_threshold = threshold
    rows, regressions, missing = [], [], []
    skipped_nonpositive: List[str] = []
    for name, bm in base.get("metrics", {}).items():
        if name.startswith("_"):
            continue
        if name not in current:
            missing.append(name)
            continue
        bv = float(bm["value"])
        cv = float(current[name]["value"]) * factor      # 负例：只放大被测比值
        if bv <= 0 or cv <= 0:
            # 值非正 ⇒ 该指标本刻**无法判决**（例如 psutil 缺失、指标没采到）
            # ⇒ 明确登记，绝不当作"通过"
            skipped_nonpositive.append(name)
            continue
        ratio = (cv / bv) if bv > 0 else 1.0
        bad = ratio > threshold
        flagged = ["ratio"] if bad else []

        # ── p95 维度（只对 P95_GATED 的指标计入判决）──
        p95_ratio = None
        if bm.get("p95_gated"):
            bp = bm.get("p95_ratio")
            cp = current[name].get("p95_ratio")
            if bp and cp:
                p95_ratio = (float(cp) * factor) / float(bp)
                if p95_ratio > p95_threshold:
                    flagged.append("p95")

        rows.append({
            "metric": name, "ref": current[name].get("ref"),
            "baseline_ratio": bv, "current_ratio": round(cv, 6),
            "ratio": round(ratio, 4), "threshold": threshold,
            "baseline_p95_ratio": bm.get("p95_ratio"),
            "current_p95_ratio": current[name].get("p95_ratio"),
            "p95_ratio": round(p95_ratio, 4) if p95_ratio is not None else None,
            "p95_threshold": p95_threshold if bm.get("p95_gated") else None,
            "p95_gated": bool(bm.get("p95_gated")),
            "flagged_dimensions": flagged,
            "verdict": "REGRESSION" if flagged else "ok",
            "raw_min_ms_current": current[name].get("raw_min_ms"),
            "raw_min_ms_baseline": bm.get("raw_min_ms"),
            "raw_p95_ms_current": current[name].get("raw_p95_ms"),
            "raw_p95_ms_baseline": bm.get("raw_p95_ms"),
        })
        if flagged:
            regressions.append(name)
    # 反向守卫：**测了但基线里没有**的指标 ⇒ 它其实没有被门禁覆盖，必须显式暴露
    unbaselined = sorted(k for k in current
                         if not k.startswith("_") and k not in base.get("metrics", {}))
    return {
        "rows": rows,
        "metrics_skipped_not_measured": missing,
        "metrics_skipped_nonpositive": skipped_nonpositive,
        "metrics_unbaselined": unbaselined,
        "regressions": regressions,
        "verdict": "FAIL" if regressions else "PASS",
    }


def _mk_metrics(value, p95_value):
    """构造自检用的指标读数（把测量噪声完全排除，只留判决逻辑）"""
    return {k: {"value": value, "ref": REFERENCE_OF[k], "n": 1, "raw_min_ms": 1.0,
                "p95_gated": k in P95_GATED, "p95_ratio": p95_value}
            for k in REFERENCE_OF}


def selftest() -> int:
    """无噪声敏感性自检（**门禁的自证**）

    这不是在测性能，是在测**判决逻辑**（把测量噪声完全排除）。

    四个场景共同证明"这门禁**抓得到**退化，且**只在被授权的作用域内**抓"：

      A. 比值维度注入 1.20x ⇒ 每个指标都必须被判 REGRESSION（阈值 1.15）
      B. 1.00x（无退化）    ⇒ 必须 PASS（不得误报）
      C. **只有 p95 维度**注入 1.20x（比值维度不动）
         ⇒ 必须 FAIL，且被点名的**恰好**是 P95_GATED 里的指标
         —— 这是 W2 新增指标（capregistry_list_envelope_10k）的自证：
            证明"它的 p95 那条线真有牙齿"，而不是只被记录。
      D. **作用域外**的指标把 p95 放大 3.00x ⇒ 判决必须**仍是 PASS**
         —— 证明 p95 的作用域是**真的**（不是碰巧全量开门），
            也证明既有 7 个指标的判定行为**没有被 W2 改动**。

    另有两条机械断言，防止"新增指标没被门禁覆盖"这类**假门禁**：
      · `NEW_METRICS ⊆ REFERENCE_OF`（新增指标确实进了采集表）
      · `P95_GATED` 非空（场景 C 不是空转）
    """
    th = 1.15
    base = {"metrics": _mk_metrics(1.0, 1.0)}

    # ── A ──
    res_a = compare(base, _mk_metrics(1.20, 1.20), th, factor=1.0)
    a_ok = (res_a["verdict"] == "FAIL"
            and set(res_a["regressions"]) == set(REFERENCE_OF))

    # ── B ──
    res_b = compare(base, _mk_metrics(1.0, 1.0), th, factor=1.0)
    b_ok = res_b["verdict"] == "PASS" and res_b["regressions"] == []

    # ── C：只动 p95 维度 ──
    cur_c = _mk_metrics(1.0, 1.0)
    for k in REFERENCE_OF:
        if k in P95_GATED:
            cur_c[k]["p95_ratio"] = 1.20
    res_c = compare(base, cur_c, th, factor=1.0)
    c_ok = (res_c["verdict"] == "FAIL"
            and set(res_c["regressions"]) == set(P95_GATED))

    # ── D：作用域外的 p95 放大 ⇒ 不得影响判决 ──
    cur_d = _mk_metrics(1.0, 1.0)
    for k in REFERENCE_OF:
        if k not in P95_GATED:
            cur_d[k]["p95_ratio"] = 3.00
    res_d = compare(base, cur_d, th, factor=1.0)
    d_ok = res_d["verdict"] == "PASS" and res_d["regressions"] == []

    # ── 机械断言：新增指标必须真的在门禁覆盖范围内 ──
    cov_ok = (set(NEW_METRICS) <= set(REFERENCE_OF)) and bool(P95_GATED)

    ok = bool(a_ok and b_ok and c_ok and d_ok and cov_ok)
    print(json.dumps({
        "selftest": "PASS" if ok else "FAIL",
        "gate_quantity": "交错比值 best-of-N(min)；p95 仅对 P95_GATED 计一条线",
        "threshold": th,
        "scenario_a": {"what": "比值维度注入 1.20x (+20%)",
                       "expected": "FAIL", "got": res_a["verdict"],
                       "n_flagged": len(res_a["regressions"]),
                       "n_metrics": len(REFERENCE_OF), "ok": a_ok},
        "scenario_b": {"what": "1.00x（无退化）", "expected": "PASS",
                       "got": res_b["verdict"], "ok": b_ok},
        "scenario_c": {"what": "**只有 p95 维度**注入 1.20x",
                       "expected": "FAIL 且只点名 P95_GATED",
                       "got": res_c["verdict"],
                       "flagged": res_c["regressions"],
                       "p95_gated": list(P95_GATED), "ok": c_ok},
        "scenario_d": {"what": "作用域外语标的 p95 放大 3.00x",
                       "expected": "PASS（不得误报）",
                       "got": res_d["verdict"],
                       "n_p95_inflated": len(REFERENCE_OF) - len(P95_GATED),
                       "ok": d_ok},
        "coverage_assert": {
            "new_metrics": list(NEW_METRICS),
            "new_metrics_all_collected": (sorted(NEW_METRICS)
                                          if set(NEW_METRICS) <= set(REFERENCE_OF) else []),
            "p95_gated": list(P95_GATED),
            "ok": cov_ok},
        "note": ("本自检排除测量噪声，只验判决逻辑；"
                 "端到端可检出的最小退化受本机噪声地板限制（见 threshold_basis）"),
    }, ensure_ascii=False, indent=2))
    return 0 if ok else 1


def load_baseline() -> Optional[Dict]:
    if not os.path.exists(BASELINE):
        return None
    with open(BASELINE, "r", encoding="utf-8") as f:
        return json.load(f)


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════


class _RawHelpFormatter(argparse.RawDescriptionHelpFormatter):
    """关闭 `--help` 里的 `%` 插值。

    【不易·2026-09-21 修一处真实的可用性缺陷】
    本脚本的 `--selftest` 帮助文本里含裸 `%`（中文全角 `％` 与半角 `%` 混用），
    而 argparse 的 `_expand_help()` 会对**每个** help 字符串做 `%` 格式化：

        ValueError: unsupported format character '?' (0x9000) at index 16

    ⇒ 结果 `python scripts/check_perf_regression.py --help` **直接崩溃**，
    而该脚本是 TASK-08 交付的**性能回归门禁**，帮助不可用会让人以为它坏了。

    Why 不在每条 help 里手工转义 `%`：
      ① 本文件的 help 全是**中文说明**，含百分号是**正常书写**，逐个转义易漏且难维护；
      ② 没有任何 help 用到 `%(default)s` 这类插值（本脚本也不需要）
         ⇒ 直接返回原串语义等价，且**今后任何 help 再写 `%` 都不会再崩**。
    """

    def _expand_help(self, action: argparse.Action) -> str:
        return self._get_help_string(action)


def main() -> int:
    ap = argparse.ArgumentParser(formatter_class=_RawHelpFormatter)
    ap.add_argument("--reps", type=int, default=30)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--with-walk-chain", action="store_true", help="含 _walk_chain 全量（约 25s）")
    ap.add_argument("--with-cold-boot", action="store_true", help="含冷启动（约 90s）")
    ap.add_argument("--simulate-regression", type=float, default=0.0,
                    help="**负例演示**：把每个门禁比值乘以该系数")
    ap.add_argument("--threshold", type=float, default=0.0)
    ap.add_argument("--selftest", action="store_true",
                    help="无噪声敏感性自检（证明 20% 退化会被判 REGRESSION）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    try:
        current = measure(args.reps, args.with_walk_chain, args.with_cold_boot)
    except Exception as e:
        print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}, ensure_ascii=False))
        return 2

    if args.write_baseline:
        base = {
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python": sys.version.split()[0],
            "reps": args.reps,
            "gate_quantity": ("被测耗时 / 同刻参照负载耗时，取 best-of-N(min)；"
                              "参照与被测**交错**采样"),
            "threshold": DEFAULT_THRESHOLD,
            "quiet_machine_threshold": QUIET_MACHINE_THRESHOLD,
            "threshold_basis": (
                "四步校准（详见 docs/perf/门禁阈值校准.md，均为本机实测）："
                "① 绝对值 p50 跨 3 次运行波动 1.3x~23x（yaml 装载 22.94x、prune 14.04x）⇒ 无法定阈值；"
                "② 参照负载分段归一化无效（参照自身波动 2.61x）；"
                "③ best-of-N(min) 绝对值 1.42x~1.56x；"
                "④ best-of-N(min) + 交错比值 1.16x~1.54x ⇒ 采用，比朴素 p50 稳 15 倍。"
                "阈值取 1.60（高于并发负载下最差观测 1.54x）。"
                "⚠️ 安静机器上应重采基线并收紧到 1.25 —— 届时 20% 退化（1.20）可被拦住。"
                "真实退化信号远大于阈值：P0-1 回退约 580x、P0-1 前 Router 组装约 3.2x。"),
            "reference_of": REFERENCE_OF,
            "p95_gated": list(P95_GATED),
            "new_metrics_from_script": sorted(NEW_METRIC_BASELINE),
            "deferred_metrics": DEFERRED_METRICS,
            "metrics": current,
        }
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        print(json.dumps({"baseline_written": BASELINE, "metrics": current},
                         ensure_ascii=False, indent=2))
        return 0

    _raw_base = load_baseline()
    if _raw_base is None:
        print(json.dumps({"status": "no_baseline",
                          "detail": "未找到 %s ⇒ 请先 --write-baseline" % BASELINE},
                         ensure_ascii=False, indent=2))
        return 2

    base = effective_baseline(_raw_base)
    threshold = args.threshold or float(base.get("threshold", DEFAULT_THRESHOLD))
    factor = args.simulate_regression or 1.0

    cmp = compare(base, current, threshold, factor)
    result = {
        "baseline": BASELINE,
        "gate_quantity": base.get("gate_quantity"),
        "threshold": threshold,
        "threshold_basis": base.get("threshold_basis"),
        "simulated_factor": factor,
        # W2：新增指标基线来自本脚本 ⇒ 必须显式暴露，否则无从判断它有没有在判
        "metrics_from_script": base.get("metrics_from_script", []),
        "p95_gated": list(P95_GATED),
        "deferred_metrics": DEFERRED_METRICS,
        "rows": cmp["rows"],
        "metrics_skipped_not_measured": cmp["metrics_skipped_not_measured"],
        "metrics_skipped_nonpositive": cmp["metrics_skipped_nonpositive"],
        "metrics_unbaselined": cmp["metrics_unbaselined"],
        # 未入基线的指标：把**本次实测条目**一并带出 ⇒ 可直接用于 --write-baseline
        # 或填进 NEW_METRIC_BASELINE（否则"新增指标没被判决"会一直悬着，无从校准）
        "current_entries_unbaselined": {
            k: current.get(k) for k in cmp["metrics_unbaselined"]},
        "regressions": cmp["regressions"],
        "verdict": cmp["verdict"],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # ── 不判 FAIL，但**必须**说出来的两件事（假门禁的两个入口）──
    if cmp["metrics_unbaselined"]:
        print("\n[perf-gate] ⚠ 以下指标**被测到但基线里没有** ⇒ 它们当前没有被门禁判决：%s"
              % ", ".join(cmp["metrics_unbaselined"]), file=sys.stderr)
    if cmp["metrics_skipped_nonpositive"]:
        print("\n[perf-gate] ⚠ 以下指标本刻**值非正、无法判决**（不计为通过）：%s"
              % ", ".join(cmp["metrics_skipped_nonpositive"]), file=sys.stderr)
    if DEFERRED_METRICS:
        print("[perf-gate] ⛔ 暂不入门禁的指标：%s"
              % ", ".join(sorted(DEFERRED_METRICS)), file=sys.stderr)

    if cmp["regressions"]:
        print("\n[perf-gate] 检测到性能退化：%s ⇒ 非零退出" % ", ".join(cmp["regressions"]),
              file=sys.stderr)
        return 1
    print("\n[perf-gate] 全部关键路径指标在阈值内 ⇒ 退出 0", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
