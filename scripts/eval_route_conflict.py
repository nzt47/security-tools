# -*- coding: utf-8 -*-
"""E1 · 路由冲突回归集评测 + τ（检索分差）标定 —— 离线、零 LLM 调用

【这张脚本为什么长这样】
  审计主报告 §2.2 判定「方案 3.4 主通道 logprob」**物理不可行**
  （tool_calls 位置没有逐 token 概率），3.4 只剩**备通道 = 检索分差**这一条路。
  备通道的阈值 τ 不能拍脑袋，必须用真实检索在边界句集合上的分布标定
  ⇒ 本脚本就是那把尺子：跑真实生产检索入口、出混淆矩阵与分差分布、给 τ。

【测的是哪一层（两个层次必须分开看，否则结论会错）】
  · 决策层（decision）= 融合排序的 **top1**。这正是「分差 ≥ τ 才执行」要门控的对象。
  · 下发层（payload）  = hybrid_select_tools() 的返回值，即随请求发给模型的
    tools[] 成员（默认上限 25）。生产实现是「检索命中 ∪ 关键词类别命中」再截断，
    core 类 5 个工具**恒在**集合内 ⇒ 下发层的 expected 命中对核心工具恒成立，
    对 forbidden 的「误扩」也只是关键词表撞词的读数。
  两者结论**不可互相替代**：本脚本两个矩阵都给，并显式标注哪一个是主判据。

  诚实声明：本脚本度量的是**检索通道**，不是端到端答案质量 ——
  真正的最终选择者仍是模型（L3 终审未落地），检索层选对 ≠ 模型一定调对。

【不调 LLM、不启动服务、零费用】
  只用 get_hybrid_retriever().query()（融合排序/分数）+ hybrid_select_tools()（下发集）。
  检索入口在无服务时可跑：直接 import agent.tool_router_hybrid 即可自建
  BM25 + Embedding 双索引（索引源 data/tool_index.json，由 YAML 生成），
  不需要 app_server 引导、不需要注册表装配。见报告 §3.1。

【退出码】0 = 达标；1 = 未达标（用例集自检失败，或决策层通过数低于 --min-pass）

【E1-B 增补（门的另一侧）】
  · 用例集版本选择：--cases-version auto（默认）= data/eval 下**版本号最大**的
    route_conflict_cases.vN.jsonl（今天 = v2，含 111 正样本 + 10 负/澄清样本）；
    v1 是 E1 的冻结集，只能显式 --cases-version 1 跑，**不会被新集覆盖或改写**。
  · expect_tools 允许为空，但**只在 should_clarify=true 时**（负/澄清样本：
    真值是「不执行任何工具」，用 expect 表达不了）。
  · 标定把负样本一并算进去：执行一条本该澄清的句子记 **假阳（误执行）**。
  · 新增 --cross-holdout：按 group-parity 双向「一半标定 τ → 另一半验证」。

用法：
  python scripts/eval_route_conflict.py                      # 生产默认（可能带向量腿）
  python scripts/eval_route_conflict.py --no-embedding       # 强制纯 BM25，CI 可复现
  python scripts/eval_route_conflict.py --cases-version 1    # 跑 E1 的 50 条冻结集
  python scripts/eval_route_conflict.py --cross-holdout      # 留出集跨集一致性
  python scripts/eval_route_conflict.py --list-failures      # 只列失败用例
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ── 仓库根（脚本可被 python scripts/xxx.py 直接跑，此时 sys.path[0] 是 scripts/）──
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CASES_DIR_REL = os.path.join("data", "eval")
_CASES_TMPL = "route_conflict_cases.v%d.jsonl"
_DEFAULT_CASES_REL = os.path.join(_CASES_DIR_REL, "route_conflict_cases.v1.jsonl")
_TOOL_INDEX_REL = os.path.join("data", "tool_index.json")
_MIN_CASES = 50          # 默认条数守卫（< 50 一律判 FAIL，见报告 §5.3）

# ── 用例集版本选择（E1-B 新增）────────────────────────────────────────────────
# 规则：--cases 显式给路径 ⇒ 用该文件（版本号从文件名解析，解析不到按 v1 处理）；
#       否则 --cases-version=auto ⇒ 取 data/eval 下**版本号最大**的 route_conflict_cases.vN.jsonl；
#       两者都没有 ⇒ 回落到 v1（E1 原始集）。
# Why 用「最高版本」而不是「新集直接覆盖旧文件」：v1 的 50 条是 E1 的冻结基线，
#   必须保持逐字不变（报告 §6 的可追溯性要求），新样本只能以新文件追加。
MIN_CASES_BY_VERSION: Dict[str, int] = {"v1": 50, "v2": 100}

# ── 本机实测基线（只许升不许降；数值来源 = 报告 §3.4 的原始输出）──────────────
# 键 = 用例集版本 → 检索模式；None = 该模式尚未标定（不得当作 0 用）。
BASELINE_BY_VERSION: Dict[str, Dict[str, Optional[Dict[str, int]]]] = {
    "v1": {
        "bm25_only": {"cases": 50, "decision_pass": 27, "payload_expect_pass": 40},
        "hybrid": None,
    },
    "v2": {
        "bm25_only": {"cases": 121, "positives": 111, "decision_pass": 48,
                      "payload_expect_pass": 88},
        "hybrid": None,
    },
}
# 兼容 E1 / E1-C 的旧引用：BASELINE 恒指 **v1（冻结集）** 基线。
BASELINE: Dict[str, Optional[Dict[str, int]]] = BASELINE_BY_VERSION["v1"]


# ════════════════════════════════════════════════════════════
#  用例集：读取与自检
# ════════════════════════════════════════════════════════════

_REQUIRED_FIELDS = ("id", "query", "expect_tools", "forbid_tools", "note")


def _version_of_path(path: str) -> str:
    """从文件名解析版本号（route_conflict_cases.vN.jsonl → "vN"）；解析不到按 v1 处理"""
    import re as _re
    m = _re.search(r"route_conflict_cases\.v(\d+)\.jsonl$", os.path.basename(path))
    return ("v" + m.group(1)) if m else "v1"


def discover_cases(root: str, version: str = "auto") -> Tuple[str, str, str]:
    """选出要评测的用例集文件，返回 (路径, 版本, 选择理由)

    version="auto" ⇒ 取 data/eval 下**版本号最大**的 route_conflict_cases.vN.jsonl；
    显式 "1"/"2" ⇒ 取对应文件（不存在则抛，不静默降级 —— 静默降级会让
    「跑了新集」这句话变成假话）。
    """
    import re as _re
    if version and version not in ("auto", "1", "2"):
        raise ValueError("--cases-version 只接受 auto/1/2，收到 %r" % version)
    if version in ("1", "2"):
        want = "v" + version
        path = os.path.join(root, _CASES_DIR_REL, _CASES_TMPL % int(version))
        if not os.path.isfile(path):
            raise FileNotFoundError("指定版本 %s 的用例集不存在: %s" % (want, path))
        return path, want, "显式 --cases-version=%s" % version
    cdir = os.path.join(root, _CASES_DIR_REL)
    found: List[Tuple[int, str]] = []
    if os.path.isdir(cdir):
        for fn in os.listdir(cdir):
            m = _re.match(r"route_conflict_cases\.v(\d+)\.jsonl$", fn)
            if m:
                found.append((int(m.group(1)), os.path.join(cdir, fn)))
    if not found:
        path = os.path.join(root, _DEFAULT_CASES_REL)
        return path, "v1", "data/eval 下没有 vN 用例集，回落 v1 默认路径"
    found.sort()
    n, path = found[-1]
    return path, "v%d" % n, "auto：data/eval 下版本号最大（发现 %s）" % (
        ", ".join("v%d" % v for v, _ in found))


def baseline_for(mode: str, version: str) -> Tuple[Optional[Dict[str, int]], str]:
    """取该（版本, 检索模式）下的基线；返回 (基线或 None, 说明)"""
    base = (BASELINE_BY_VERSION.get(version) or {}).get(mode)
    if base:
        return base, "BASELINE_BY_VERSION[%s][%s]" % (version, mode)
    if (BASELINE_BY_VERSION.get(version) or {}).get(mode) is None and mode in (
            BASELINE_BY_VERSION.get(version) or {}):
        return None, "该模式未标定（%s/%s）" % (version, mode)
    return None, "无基线（%s/%s 未登记）" % (version, mode)


def load_cases(path: str) -> List[Dict[str, Any]]:
    """读 JSONL 用例集（坏行直接抛，不静默跳过——静默跳过等于偷偷放行）"""
    cases: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                cases.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise ValueError("用例集第 %d 行不是合法 JSON: %s" % (lineno, e)) from e
    return cases


def validate_cases(cases: Sequence[Dict[str, Any]],
                   known_tools: Optional[set] = None,
                   min_cases: int = _MIN_CASES) -> List[str]:
    """用例集自检，返回错误列表（空列表 = 通过）。与 tests 共用同一份判据。

    【E1-B 新增：门的另一侧】负/澄清样本（should_clarify=true）**允许 expect_tools 为空**
    —— 它们的真值就是「不该执行任何工具」，用 expect 表达不了。开关不是全局放开：
      · should_clarify=true  ⇒ expect_tools 必须为空、forbid_tools 必须非空；
      · 缺省/False（= 正样本）⇒ expect_tools 必须非空（E1 原有契约不变）。
    """
    errors: List[str] = []
    if len(cases) < min_cases:
        errors.append("用例集条数 %d < 守卫下限 %d（禁止删条缩集）" % (len(cases), min_cases))

    seen_ids: set = set()
    for idx, c in enumerate(cases, 1):
        where = "第 %d 条(id=%s)" % (idx, c.get("id"))
        for field in _REQUIRED_FIELDS:
            if field not in c:
                errors.append("%s 缺字段 %s" % (where, field))
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            errors.append("%s id 必须是非空字符串" % where)
        elif cid in seen_ids:
            errors.append("%s id 重复" % where)
        else:
            seen_ids.add(cid)

        query = c.get("query")
        if not isinstance(query, str) or not query.strip():
            errors.append("%s query 为空" % where)
        note = c.get("note")
        if not isinstance(note, str) or len(note.strip()) < 12:
            errors.append("%s note 缺失或过短（必须说明「为什么是边界」）" % where)

        clarify = bool(c.get("should_clarify"))
        for field in ("expect_tools", "forbid_tools"):
            v = c.get(field)
            if v is None or not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
                errors.append("%s %s 必须是字符串数组" % (where, field))
            elif not v and not (field == "expect_tools" and clarify):
                errors.append("%s %s 必须是非空字符串数组%s"
                              % (where, field,
                                 "（只有 should_clarify=true 的负样本允许 expect_tools 为空）"))
        if clarify:
            if c.get("expect_tools"):
                errors.append("%s should_clarify=true 时 expect_tools 必须为空"
                              "（真值是「不执行任何工具」，写了期望工具就自相矛盾）" % where)
            if not c.get("forbid_tools"):
                errors.append("%s should_clarify=true 时 forbid_tools 必须给出"
                              "（列出最容易被误召的工具，供下发层诊断）" % where)
        exp, forb = c.get("expect_tools") or [], c.get("forbid_tools") or []
        if isinstance(exp, list) and isinstance(forb, list):
            both = sorted(set(exp) & set(forb))
            if both:
                errors.append("%s expect_tools 与 forbid_tools 冲突: %s" % (where, both))
            if known_tools is not None:
                unknown = sorted({t for t in list(exp) + list(forb) if t not in known_tools})
                if unknown:
                    errors.append("%s 引用了不存在的工具名: %s" % (where, unknown))
        groups = c.get("source_groups")
        if not isinstance(groups, list) or not groups or \
                not all(isinstance(g, int) and 1 <= g <= 21 for g in groups):
            errors.append("%s source_groups 必须是 1..21 的整数数组（锚定 Q2 §3 的 21 组重叠样本）" % where)
    return errors


def known_tool_names(root: str) -> set:
    """生产可路由工具名集合（索引文件即检索侧的候选全集）"""
    with open(os.path.join(root, _TOOL_INDEX_REL), "r", encoding="utf-8") as f:
        data = json.load(f)
    return {t.get("name") for t in data.get("tools", []) if t.get("name")}


# ════════════════════════════════════════════════════════════
#  检索入口（生产路径；无服务可用）
# ════════════════════════════════════════════════════════════

def build_retriever(root: str, no_embedding: bool = False, worker_ready_timeout: float = 0.0):
    """构造生产检索器

    Args:
        no_embedding: True ⇒ 关掉向量腿（等价于生产里 Embedding 不可用时的降级态），
            使 CI 结果可复现（本机实测：向量 worker 冷启动需约 97s，而
            _WORKER_READY_TIMEOUT 硬编码 30s ⇒ 生产默认态恒为 bm25_only）。
        worker_ready_timeout: >0 时覆盖 EmbeddingIndex._WORKER_STARTUP_TIMEOUT
            （**仅供标定用**，会明确打印为「非生产默认」）。
    """
    if root not in sys.path:
        sys.path.insert(0, root)
    if no_embedding:
        os.environ["AGENT_HYBRID_EMBEDDING"] = "0"

    import agent.tool_router_hybrid as trh  # 延迟导入：确保 env 在本模块导入前已设好

    if worker_ready_timeout and worker_ready_timeout > 0:
        trh.EmbeddingIndex._WORKER_STARTUP_TIMEOUT = float(worker_ready_timeout)

    trh.reset_hybrid_retriever()          # 隔离上一次的单例状态
    retriever = trh.get_hybrid_retriever()
    if retriever is None or not retriever.available:
        raise RuntimeError("检索器不可用：data/tool_index.json 缺失或 BM25 索引构建失败")
    return trh, retriever


def _pool_size(top_k: int, max_tools: int) -> int:
    """候选池大小 —— 与 hybrid_select_tools 内部口径逐字一致

    production: pool = top_k；若 pool < max_tools 则放大到 max_tools
    （agent/tool_router_hybrid.py:1690-1695）。不镜像这段，读到的排序就不是
    生产入口读到的那一份。
    """
    pool = int(top_k) if top_k and top_k > 0 else 40
    if max_tools and max_tools > 0 and pool < max_tools:
        pool = int(max_tools)
    return pool


def wait_embedding(retriever, timeout: float) -> bool:
    """等到向量腿真的可用（或超时）。返回是否可用。"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not retriever.degraded:
            return True
        time.sleep(2.0)
    return not retriever.degraded


# ════════════════════════════════════════════════════════════
#  评测
# ════════════════════════════════════════════════════════════

def evaluate_case(trh, retriever, case: Dict[str, Any], *,
                  max_tools: int = 25, top_k: int = 40,
                  alpha: Optional[float] = None) -> Dict[str, Any]:
    """跑一条用例：真实检索 + 真实下发集，逐项记录判据与分差"""
    query = case["query"]
    expect = list(case["expect_tools"])
    forbid = list(case["forbid_tools"])

    # 【E1-C 装置修正】alpha 必须在 query() **之前**落到检索器实例上。
    #   旧实现的顺序是「先 query()（读实例 alpha）→ 再 hybrid_select_tools(alpha=…)」，
    #   而 hybrid_select_tools 内部才写 retriever._alpha（agent/tool_router_hybrid.py:2168）。
    #   后果：第 1 条用例的**决策层**读到的永远是构造时的默认 alpha（hybrid_select_tools
    #   是条末才写的），同一轮里两条腿用的不是同一个 alpha。改后显式 alpha 对两侧同时、
    #   且从第 1 条起生效；alpha=None 时行为不变（沿用实例默认 = 环境变量/0.5）。
    if alpha is not None:
        retriever._alpha = float(alpha)

    # ① 融合排序（决策层）：与生产入口同池
    ranked = retriever.query(query, top_k=_pool_size(top_k, max_tools))
    if ranked is None:
        ranked = []
    ranked_names = [n for n, _ in ranked]

    # ② 下发集（下发层）：生产入口本体，**不重写一份截断逻辑**
    selected = trh.hybrid_select_tools(query, None, max_tools=max_tools, top_k=top_k,
                                       alpha=alpha)
    selected = list(selected) if selected else []

    top1 = ranked_names[0] if ranked_names else None
    top2 = ranked_names[1] if len(ranked_names) > 1 else None
    top1_score = float(ranked[0][1]) if ranked else 0.0
    # 只有 1 个候选（或无候选）时，第 2 名分数按 0.0 记 ⇒ 分差 = top1 分。
    # 这是**口径选择**而不是测量值，报告 §3.5 已显式标注。
    top2_score = float(ranked[1][1]) if len(ranked) > 1 else 0.0
    gap = round(top1_score - top2_score, 6)

    expect_hit_decision = top1 in expect
    forbid_hit_decision = top1 in forbid
    expect_hit_payload = set(expect) <= set(selected)
    forbid_in_payload = sorted(set(forbid) & set(selected))
    expect_miss_rank = sorted(set(expect) - set(ranked_names))

    should_clarify = bool(case.get("should_clarify"))
    return {
        "id": case["id"],
        "query": query,
        "expect_tools": expect,
        "forbid_tools": forbid,
        "should_clarify": should_clarify,
        "category": case.get("category"),
        "shape": case.get("shape"),
        "top1": top1,
        "top2": top2,
        "top1_score": round(top1_score, 6),
        "top2_score": round(top2_score, 6),
        "gap": gap,
        "n_ranked": len(ranked_names),
        "n_selected": len(selected),
        "selected": selected,
        "expect_hit_decision": expect_hit_decision,
        "forbid_hit_decision": forbid_hit_decision,
        "expect_hit_payload": expect_hit_payload,
        "forbid_in_payload": forbid_in_payload,
        "expect_miss_rank": expect_miss_rank,
        "top1_in_selected": (top1 in selected) if top1 else False,
        # 主判据（决策层）：选对了才算过；选到 forbidden 直接不过。
        # 负/澄清样本没有「选对」这回事 ⇒ 判据改成「门有没有拦住」（gap < τ），
        # 由 apply_clarify_gate(rows, tau) 在标定出 τ 之后回填；这里先给 None，
        # 避免出现「没经过门就算通过」的假绿。
        "passed": (None if should_clarify
                   else bool(expect_hit_decision and not forbid_hit_decision)),
        # 负样本的检索侧诊断：top1（无论是什么）都不该被执行 ⇒ 恒记 False，
        # 由门控那一层给分。这一栏只用于统计「最容易被哪一类工具吸走」。
        "expect_hit_decision": (False if should_clarify else expect_hit_decision),
    }


def run_eval(cases: Sequence[Dict[str, Any]], trh, retriever, *,
             max_tools: int = 25, top_k: int = 40,
             alpha: Optional[float] = None) -> List[Dict[str, Any]]:
    return [evaluate_case(trh, retriever, c, max_tools=max_tools, top_k=top_k, alpha=alpha)
            for c in cases]


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """混淆矩阵（两个层次分别给）

    【E1-B 口径变更（必须点名）】决策层/下发层的通过判据只在**正样本**上定义
    （负样本没有 expect，"命中率"对它没有意义）。因此：
      · n_cases = 全部条数；n_positive / n_negative 分别给出；
      · decision/payload 两块的 n 字段 = **正样本数**（v1 全是正样本 ⇒ n == n_cases，
        与 E1 的口径逐字一致，旧读数不受影响）；
      · 负样本另走 clarify 块（门控语义，见 clarify_gate_eval）。
    """
    pos = [r for r in rows if not r.get("should_clarify")]
    neg = [r for r in rows if r.get("should_clarify")]
    n = len(pos)
    dec_hit = sum(1 for r in pos if r["expect_hit_decision"])
    dec_forbid = sum(1 for r in pos if r["forbid_hit_decision"])
    pay_hit = sum(1 for r in pos if r["expect_hit_payload"])
    pay_forbid_cases = sum(1 for r in pos if r["forbid_in_payload"])
    pay_forbid_members = sum(len(r["forbid_in_payload"]) for r in pos)
    passed = sum(1 for r in pos if r["passed"])
    top1_absent = sum(1 for r in pos if not r["top1_in_selected"])
    return {
        "n_cases": len(rows),
        "n_positive": n,
        "n_negative": len(neg),
        "decision": {
            "n": n,
            "expect_hit": dec_hit, "expect_miss": n - dec_hit,
            "forbid_recalled": dec_forbid, "forbid_not_recalled": n - dec_forbid,
            "passed": passed, "failed": n - passed,
            "pass_rate": round(passed / n, 4) if n else 0.0,
        },
        "payload": {
            "n": n,
            "expect_hit": pay_hit, "expect_miss": n - pay_hit,
            "forbid_recalled_cases": pay_forbid_cases,
            "forbid_not_recalled_cases": n - pay_forbid_cases,
            "forbid_recalled_members": pay_forbid_members,
            "top1_hit_but_not_in_payload": top1_absent,
        },
        "clarify": _clarify_retrieval_stats(neg),
    }


def _clarify_retrieval_stats(neg: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """负/澄清样本在**检索侧**的读数（不含门控）

    关键一条：负样本的 top1 无论是什么都不该被执行 ⇒ 它的 "expected 命中" 恒为 0，
    唯一有信息量的检索侧读数是「最容易被哪一类工具吸走」与「有没有零召回」。
    """
    n = len(neg)
    zero_recall = [r["id"] for r in neg if r["n_ranked"] == 0]
    forbid_hit = sum(1 for r in neg if r["forbid_hit_decision"])
    forbid_payload = sum(1 for r in neg if r["forbid_in_payload"])
    top1_counter: Dict[str, int] = {}
    for r in neg:
        key = r["top1"] or "(零召回)"
        top1_counter[key] = top1_counter.get(key, 0) + 1
    by_cat: Dict[str, int] = {}
    for r in neg:
        cat = r.get("category") or "未分类"
        by_cat[cat] = by_cat.get(cat, 0) + 1
    return {
        "n": n,
        "zero_recall_cases": zero_recall,
        "top1_distribution": dict(sorted(top1_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
        "forbid_recalled_in_payload_cases": forbid_payload,
        "forbid_hit_top1_cases": forbid_hit,
        "by_category": dict(sorted(by_cat.items())),
    }


# ════════════════════════════════════════════════════════════
#  τ 标定（唯一可用通道 = 检索分差）
# ════════════════════════════════════════════════════════════

def _execute_is_correct(r: Dict[str, Any]) -> bool:
    """「这条用例该不该被执行」的真值（门控的地面真值）

    · 正样本：top1 正确（命中 expect 且不在 forbid 里）⇒ 该执行；
    · 负/澄清样本（should_clarify）：**任何** top1 都不该被执行 ⇒ 恒 False。

    E1-B 的作用点：把负样本并入标定后，「执行了一条本该澄清的句子」会被记成
    **假阳（误执行）**，这正是 E1 那 50 条全正样本时**结构上无法出现**的那类错误。
    """
    if r.get("should_clarify"):
        return False
    return bool(r.get("expect_hit_decision"))


def apply_clarify_gate(rows: Sequence[Dict[str, Any]], tau: float) -> int:
    """把 τ 回填到负/澄清样本的 passed 字段（门控判据：gap < τ ⇒ 正确澄清）

    返回正确澄清的条数。正样本不受影响（它们的 passed 在 evaluate_case 里已定）。
    """
    ok = 0
    for r in rows:
        if not r.get("should_clarify"):
            continue
        r["passed"] = bool(r["gap"] < tau)
        if r["passed"]:
            ok += 1
    return ok


def clarify_gate_eval(rows: Sequence[Dict[str, Any]], tau: float) -> Dict[str, Any]:
    """★ 门的另一侧：在给定 τ 下，「该澄清时澄清」的准确率（E1 首次有数）

    判据：负样本 gap < τ ⇒ 门会去澄清（正确）；gap >= τ ⇒ 门会直接执行 top1（**误执行**）。
    两层读数都给：
      · all      = 全部负样本（含零召回 —— 零召回时没有可执行对象，必然澄清）；
      · scored   = 只算**有候选**的负样本（零召回不算"门的功劳"，单独报，避免虚高）。
    """
    neg = [r for r in rows if r.get("should_clarify")]
    scored = [r for r in neg if r.get("n_ranked", 0) > 0]
    def _clarified(r) -> bool:
        # 零召回 = 没有可执行对象 ⇒ 必然澄清（否则 τ=0 时会把"无候选"错记成"误执行"）
        if r.get("n_ranked", 0) == 0:
            return True
        return r.get("gap", 0.0) < tau

    def _acc(sub):
        if not sub:
            return {"n": 0, "clarified": 0, "executed_wrong": 0, "accuracy": None}
        ok = sum(1 for r in sub if _clarified(r))
        return {"n": len(sub), "clarified": ok, "executed_wrong": len(sub) - ok,
                "accuracy": round(ok / len(sub), 4),
                "wrong_ids": [r["id"] for r in sub if not _clarified(r)]}
    return {"tau": tau, "all": _acc(neg), "scored": _acc(scored),
            "zero_recall": [r["id"] for r in neg if r.get("n_ranked", 0) == 0],
            "top1_of_wrong": {r["id"]: r.get("top1") for r in neg if not _clarified(r)}}


def calibrate_tau(rows: Sequence[Dict[str, Any]],
                  include_negatives: bool = True) -> Dict[str, Any]:
    """在分差分布上扫 τ，给出**数据驱动**的 τ 建议与假阳/假阴

    门控语义（方案 3.4 备通道）：分差 >= τ ⇒ 执行（采用决策层 top1）；否则澄清。
    以「这条用例该不该被执行」为真值（见 _execute_is_correct）：
      · 执行 且 该执行 = TP；  执行 且 不该执行 = FP（**假阳 = 误执行**）
      · 澄清 且 该执行 = FN（**假阴 = 该执行却被要求澄清**）；澄清 且 不该执行 = TN

    include_negatives=True（E1-B 默认）：负/澄清样本一并进标定 —— 执行它们一律记 FP。
    include_negatives=False：回到 E1 的原始口径（只用正样本），用于与 E1 的 τ 对照。

    选 τ 的判据：**最大化 F1**（同时惩罚误执行与不必要澄清）；
    并列时取 τ 更大者（更保守）。任一 τ 的 F1 都为 0 ⇒ 该通道**无判别力**，
    此时**不给经验值**，直接报「不可标定」。
    """
    all_rows = list(rows)          # 门的另一侧读数始终用**全集**（不受 include_negatives 影响）
    if not include_negatives:
        rows = [r for r in rows if not r.get("should_clarify")]
    usable = [r for r in rows if r["n_ranked"] > 0]
    gaps = sorted({r["gap"] for r in usable})
    if not gaps:
        return {"tau_candidates": [], "best": None, "note": "无可用于标定的候选（全部零召回）"}

    cand = sorted(set([0.0] + gaps + [round(gaps[-1] + 1e-6, 6)]))
    table: List[Dict[str, Any]] = []
    for tau in cand:
        tp = fp = fn = tn = 0
        for r in usable:
            execute = r["gap"] >= tau
            ok = _execute_is_correct(r)
            if execute and ok:
                tp += 1
            elif execute and not ok:
                fp += 1
            elif not execute and ok:
                fn += 1
            else:
                tn += 1
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        table.append({
            "tau": tau, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "execute_rate": round((tp + fp) / len(usable), 4),
        })

    best = max(table, key=lambda t: (t["f1"], t["tau"]))
    degenerate = best["f1"] <= 0.0
    zero_fn = [t for t in table if t["FN"] == 0]
    # 零误执行（FP=0）且仍有产出的**最小** τ = 保守操作点（执行率最高的一档）
    zero_fp = [t for t in table if t["FP"] == 0 and t["TP"] > 0]
    # 分差对「top1 正确与否」的区分度：给两组的分差统计，而不是只报相关系数
    pos_rows = [r for r in usable if not r.get("should_clarify")]
    ok_gaps = [r["gap"] for r in pos_rows if r["expect_hit_decision"]]
    bad_gaps = [r["gap"] for r in pos_rows if not r["expect_hit_decision"]]
    return {
        "n_scored": len(usable),
        "n_unscored": len(rows) - len(usable),
        "include_negatives": bool(include_negatives),
        "n_positive_scored": len(pos_rows),
        "n_negative_scored": len(usable) - len(pos_rows),
        # ★ 门的另一侧：该 τ 下负样本会不会被误执行（E1 首次有数）
        "clarify_at_best": clarify_gate_eval(all_rows, best["tau"]),
        "gap_min": min(gaps), "gap_max": max(gaps),
        "n_distinct_gaps": len(gaps),
        "tau_candidates": cand,
        "table": table,
        "best": best,
        "degenerate": degenerate,
        # FN=0 的最小 τ（"宁可澄清也不误执行"的保守点），供方案做取舍时参考
        "conservative_zero_fn_tau": (min(t["tau"] for t in zero_fn) if zero_fn else None),
        "zero_fp_min_tau": (min(t["tau"] for t in zero_fp) if zero_fp else None),
        "gap_stats": {
            "correct_top1": {"n": len(ok_gaps),
                             "median": round(statistics.median(ok_gaps), 4) if ok_gaps else None,
                             "mean": round(statistics.mean(ok_gaps), 4) if ok_gaps else None},
            "wrong_top1": {"n": len(bad_gaps),
                           "median": round(statistics.median(bad_gaps), 4) if bad_gaps else None,
                           "mean": round(statistics.mean(bad_gaps), 4) if bad_gaps else None},
        },
    }


def _histogram(gaps: Sequence[float], width: float = 0.05) -> List[Tuple[str, int]]:
    buckets: Dict[int, int] = {}
    for g in gaps:
        k = int(g // width)
        buckets[k] = buckets.get(k, 0) + 1
    out = []
    for k in sorted(buckets):
        lo = round(k * width, 4)
        out.append(("[%.2f, %.2f)" % (lo, lo + width), buckets[k]))
    return out


# ════════════════════════════════════════════════════════════
#  α 扫描（E1-C）：同进程扫一整条 α 轴
# ════════════════════════════════════════════════════════════

def parse_alpha_list(raw: str) -> List[float]:
    """解析 --sweep 的 α 列表（逗号分隔）。非法值直接抛，不静默跳过。

    Why 不静默跳过：扫描点被悄悄丢掉会让「轴上看不出拐点」变成一个假结论，
    而这正是本卡要回答的问题。
    """
    out: List[float] = []
    for part in str(raw).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            val = float(part)
        except ValueError as exc:
            raise ValueError("--sweep 中 %r 不是数字" % part) from exc
        if not (0.0 <= val <= 1.0):
            raise ValueError("--sweep 中 %r 超出 [0,1]（融合权重定义域）" % part)
        out.append(val)
    if not out:
        raise ValueError("--sweep 为空")
    return out


def filter_cases_by_group_parity(cases: Sequence[Dict[str, Any]],
                                 parity: Optional[str]) -> List[Dict[str, Any]]:
    """按 source_groups 的奇偶把用例切开（E1-C 留出集：跨集一致性）

    归属判据 = **case.source_groups 里的最小值**的奇偶（确定性、可复现；
    交叉组用例 rc-004 这类 [2,3] 一律归入 min=2 的偶数组，不重复计入两侧，
    使两侧并集 = 全集、交集 = 空 —— 否则同一条用例既标定又验证 = 自证）。

    Why 在**自检之后**才切：validate_cases 的 50 条条数守卫必须仍然作用在全集上，
    否则"留出集"会顺手把「禁止删条缩集」这条守卫也一起绕过去。
    """
    if not parity or parity == "all":
        return list(cases)
    if parity not in ("odd", "even"):
        raise ValueError("group-parity 只接受 odd/even/all，收到 %r" % parity)
    want_odd = (parity == "odd")
    out = []
    for c in cases:
        groups = [g for g in (c.get("source_groups") or []) if isinstance(g, int)]
        if not groups:
            continue
        if (min(groups) % 2 == 1) == want_odd:
            out.append(c)
    return out


def _count_at_tau(rows: Sequence[Dict[str, Any]], tau: float) -> Dict[str, Any]:
    """把给定 τ 应用到一批 row 上，给出混淆计数与派生指标（正/负样本分开算）"""
    usable = [r for r in rows if r["n_ranked"] > 0]
    tp = fp = fn = tn = 0
    for r in usable:
        execute = r["gap"] >= tau
        ok = _execute_is_correct(r)
        if execute and ok:
            tp += 1
        elif execute and not ok:
            fp += 1
        elif not execute and ok:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    pos = [r for r in usable if not r.get("should_clarify")]
    return {
        "tau": tau, "n_scored": len(usable), "n_positive_scored": len(pos),
        "n_negative_scored": len(usable) - len(pos),
        "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
        "execute_rate": round((tp + fp) / len(usable), 4) if usable else 0.0,
        "positive_pass": sum(1 for r in pos if r["passed"]),
        "positive_pass_rate": round(sum(1 for r in pos if r["passed"]) / len(pos), 4) if pos else 0.0,
        "clarify": clarify_gate_eval(rows, tau),
    }


def cross_holdout(rows: Sequence[Dict[str, Any]],
                  cases: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """★ 留出集跨集一致性（E1 缺的过拟合量化）

    切法沿用 E1-C 的 --group-parity：归属 = case.source_groups 最小值的奇偶，
    两侧并集 = 全集、交集 = 空。做法是**双向**的：
      · 在奇数组上标定 τ（最大化 F1），把这个 τ 搬到偶数组上评估；
      · 反之亦然。
    报告的判据不是「两半的最优 τ 一不一样」（样本量小，必然抖），而是：
      **用一半选出的 τ，在另一半上的 F1 / precision / 误执行数，与它在本半上的差距有多大**
      —— 差距大 = 标定过拟合；差距小 = 该通道的标定可跨集复制。
    """
    odd = {c["id"] for c in filter_cases_by_group_parity(cases, "odd")}
    even = {c["id"] for c in filter_cases_by_group_parity(cases, "even")}
    rows_odd = [r for r in rows if r["id"] in odd]
    rows_even = [r for r in rows if r["id"] in even]
    covered = len(rows_odd) + len(rows_even)
    out: Dict[str, Any] = {
        "n_odd": len(rows_odd), "n_even": len(rows_even), "n_rows": len(rows),
        "split_covers_all": covered == len(rows),
        "split_disjoint": len(odd & even) == 0,
        "directions": {},
    }
    for name, tr_rows, va_rows in (("odd->even", rows_odd, rows_even),
                                   ("even->odd", rows_even, rows_odd)):
        cal = calibrate_tau(tr_rows)
        tau = cal["best"]["tau"] if cal.get("best") else None
        on_train = _count_at_tau(tr_rows, tau) if tau is not None else None
        on_valid = _count_at_tau(va_rows, tau) if tau is not None else None
        # 另一半自己的最优 τ（对照：两边 argmax 是否同一个区间）
        cal_other = calibrate_tau(va_rows)
        out["directions"][name] = {
            "tau_from_train": tau,
            "tau_own_optimum_of_valid": (cal_other["best"]["tau"] if cal_other.get("best") else None),
            "train": on_train, "valid": on_valid,
            "f1_drop": (round(on_train["f1"] - on_valid["f1"], 4)
                        if on_train and on_valid else None),
        }
    # 全集 τ 在两个半集上的表现（第三条参照线）
    cal_all = calibrate_tau(rows)
    tau_all = cal_all["best"]["tau"] if cal_all.get("best") else None
    out["tau_all"] = tau_all
    if tau_all is not None:
        out["tau_all_on_odd"] = _count_at_tau(rows_odd, tau_all)
        out["tau_all_on_even"] = _count_at_tau(rows_even, tau_all)
    return out


def format_cross_holdout(res: Dict[str, Any], cases_path: str, n_cases: int) -> str:
    lines: List[str] = []
    add = lines.append
    add("=" * 78)
    add("留出集跨集一致性（group-parity 双向标定→验证；E1 缺的过拟合量化）")
    add("=" * 78)
    add("切法    : 归属 = source_groups 最小值的奇偶（E1-C 原判据，未改）")
    add("规模    : 奇数组 %d 条 / 偶数组 %d 条 / 合计 %d 条；并集=全集 %s，交集=空 %s"
        % (res["n_odd"], res["n_even"], res["n_rows"],
           res["split_covers_all"], res["split_disjoint"]))
    add("")
    for name in ("odd->even", "even->odd"):
        d = res["directions"][name]
        tr, va = d["train"], d["valid"]
        add("【%s】在训练半集上标定的 τ = %s（该半集自己的最优 τ = %s）"
            % (name, ("%.4f" % d["tau_from_train"]) if d["tau_from_train"] is not None else "-",
               ("%.4f" % d["tau_own_optimum_of_valid"]) if d["tau_own_optimum_of_valid"] is not None else "-"))
        if tr and va:
            add("    训练半集 : n=%d  TP=%d FP=%d FN=%d TN=%d  P=%.4f R=%.4f F1=%.4f 执行率=%.2f 澄清正确率=%s"
                % (tr["n_scored"], tr["TP"], tr["FP"], tr["FN"], tr["TN"],
                   tr["precision"], tr["recall"], tr["f1"], tr["execute_rate"],
                   _acc_str(tr["clarify"]["scored"])))
            add("    验证半集 : n=%d  TP=%d FP=%d FN=%d TN=%d  P=%.4f R=%.4f F1=%.4f 执行率=%.2f 澄清正确率=%s"
                % (va["n_scored"], va["TP"], va["FP"], va["FN"], va["TN"],
                   va["precision"], va["recall"], va["f1"], va["execute_rate"],
                   _acc_str(va["clarify"]["scored"])))
            add("    ⇒ 跨集落差：F1 %+.4f、precision %+.4f、误执行(FP) %+d 条、澄清正确率 %s"
                % (d["f1_drop"], round(tr["precision"] - va["precision"], 4),
                   va["FP"] - tr["FP"], _acc_delta_str(tr["clarify"]["scored"], va["clarify"]["scored"])))
        add("")
    if res.get("tau_all") is not None:
        for half in ("odd", "even"):
            c = res["tau_all_on_%s" % half]
            add("全集 τ=%.4f 在 %s 半集：n=%d TP=%d FP=%d FN=%d TN=%d F1=%.4f 澄清正确率=%s"
                % (res["tau_all"], "奇数组" if half == "odd" else "偶数组",
                   c["n_scored"], c["TP"], c["FP"], c["FN"], c["TN"], c["f1"],
                   _acc_str(c["clarify"]["scored"])))
    add("")
    return "\n".join(lines)


def _acc_str(a: Dict[str, Any]) -> str:
    if not a or a.get("accuracy") is None:
        return "-"
    return "%d/%d=%.2f%%" % (a["clarified"], a["n"], a["accuracy"] * 100)


def _acc_delta_str(a: Dict[str, Any], b: Dict[str, Any]) -> str:
    if not a or not b or a.get("accuracy") is None or b.get("accuracy") is None:
        return "-"
    return "%+.2fpp" % ((b["accuracy"] - a["accuracy"]) * 100)


def run_sweep(cases: Sequence[Dict[str, Any]], trh, retriever,
              alphas: Sequence[float], *, max_tools: int = 25,
              top_k: int = 40) -> List[Dict[str, Any]]:
    """在**同一个检索器实例**上扫 α 轴（模型只加载一次）

    每个点独立跑全量用例，并各自重标 τ；同时记录该点检索器是否真的处于 hybrid 态
    （向量腿掉回 bm25_only 的点按"作废"处理，由调用方判定）。
    """
    points: List[Dict[str, Any]] = []
    for a in alphas:
        rows = run_eval(cases, trh, retriever, max_tools=max_tools, top_k=top_k, alpha=a)
        summ = summarize(rows)
        cal = calibrate_tau(rows)
        best = cal.get("best") or {}
        # 负/澄清样本的 passed 由门控回填（与主路径口径一致；否则扫描 JSON 里
        # 负样本的 passed 恒为 None，"通过数"会与主路径对不上）
        if best.get("tau") is not None:
            apply_clarify_gate(rows, best["tau"])
        points.append({
            "alpha": float(a),
            "mode": _mode_name(retriever),
            "vector_leg_available": (not retriever.degraded),
            "n_positive": summ["n_positive"],
            "n_negative": summ["n_negative"],
            # ★ E1-B：每个 α 点也自带「门的另一侧」读数（该点自己标定的 τ* 下）
            "clarify_at_best": cal.get("clarify_at_best"),
            "tau_positive_only": (calibrate_tau(rows, include_negatives=False).get("best") or {}).get("tau"),
            "decision_pass": summ["decision"]["passed"],
            "decision_pass_rate": summ["decision"]["pass_rate"],
            "decision_expect_hit": summ["decision"]["expect_hit"],
            "decision_forbid_hit": summ["decision"]["forbid_recalled"],
            "payload_expect_hit": summ["payload"]["expect_hit"],
            "payload_forbid_cases": summ["payload"]["forbid_recalled_cases"],
            "payload_forbid_members": summ["payload"]["forbid_recalled_members"],
            "tau_best": best.get("tau"),
            "tau_tp": best.get("TP"), "tau_fp": best.get("FP"),
            "tau_fn": best.get("FN"), "tau_tn": best.get("TN"),
            "tau_precision": best.get("precision"),
            "tau_recall": best.get("recall"),
            "tau_f1": best.get("f1"),
            "tau_execute_rate": best.get("execute_rate"),
            "tau_degenerate": bool(cal.get("degenerate")),
            "summary": summ,
            "tau": cal,
            "rows": rows,
        })
    return points


def format_sweep_table(points: Sequence[Dict[str, Any]], cases_path: str,
                       n_cases: int, *, max_tools: int, top_k: int) -> str:
    """α 扫描总表（每行 = 一个实测 α 点）"""
    lines: List[str] = []
    add = lines.append
    with open(cases_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]
    # 决策层/下发层的分母 = **正样本**（E1-B：负样本没有 expect，不进通过率）
    n_pos = points[0].get("n_positive", n_cases) if points else n_cases
    n_neg = points[0].get("n_negative", 0) if points else 0
    add("=" * 118)
    add("E1-C · α 扫描（离线，零 LLM；同一进程内每个 α 点全量重跑）")
    add("=" * 118)
    add("用例集    : %s   sha256(16)=%s   条数=%d（正样本 %d / 负·澄清 %d）"
        % (cases_path, digest, n_cases, n_pos, n_neg))
    add("检索参数  : max_tools=%d  实际候选池=%d" % (max_tools, _pool_size(top_k, max_tools)))
    add("")
    add("%-6s %-9s %-9s %-9s %-7s %-6s %-8s %-8s %-8s %-5s %-5s %-6s %-9s"
        % ("α", "模式", "决策层", "下发层", "forbid下", "forbid", "τ*", "τ F1",
           "precision", "FP", "FN", "执行率", "门的另一侧"))
    add("%-6s %-9s %-9s %-9s %-7s %-6s %-8s %-8s %-8s %-5s %-5s %-6s %-9s"
        % ("", "", "通过/%d" % n_pos, "exp/%d" % n_pos, "发(条)", "成员",
           "", "", "", "", "", "", "澄清/%d" % n_neg))
    add("-" * 132)
    for p in points:
        cg = (p.get("clarify_at_best") or {}).get("all") or {}
        cl = ("%d/%d" % (cg.get("clarified", 0), cg.get("n", 0))) if cg.get("n") else "-"
        add("%-6.2f %-9s %-9s %-9d %-7d %-6d %-8s %-8s %-8s %-5s %-5s %-6s %-9s"
            % (p["alpha"], p["mode"],
               "%d/%d" % (p["decision_pass"], n_pos),
               p["payload_expect_hit"],
               p["payload_forbid_cases"],
               p["payload_forbid_members"],
               ("%.4f" % p["tau_best"]) if p["tau_best"] is not None else "-",
               ("%.4f" % p["tau_f1"]) if p["tau_f1"] is not None else "-",
               ("%.4f" % p["tau_precision"]) if p["tau_precision"] is not None else "-",
               p["tau_fp"] if p["tau_fp"] is not None else "-",
               p["tau_fn"] if p["tau_fn"] is not None else "-",
               ("%.2f" % p["tau_execute_rate"]) if p["tau_execute_rate"] is not None else "-",
               cl))
    add("-" * 118)
    degraded = [p["alpha"] for p in points if p["mode"] != "hybrid"]
    if degraded:
        add("⚠ 以下 α 点的向量腿**不可用**（模式=bm25_only）⇒ 该点作废：%s"
            % ", ".join("%.2f" % a for a in degraded))
    else:
        add("全部 α 点均实测于 hybrid 态（向量腿可用）")
    add("")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  报告输出
# ════════════════════════════════════════════════════════════

def _mode_name(retriever) -> str:
    return "bm25_only" if retriever.degraded else "hybrid"


def format_report(cases_path: str, cases: Sequence[Dict[str, Any]],
                  rows: Sequence[Dict[str, Any]], mode: str,
                  retriever, *, max_tools: int, top_k: int,
                  alpha: float, non_production: bool,
                  version: str = "v1", selection: str = "") -> str:
    lines: List[str] = []
    add = lines.append
    with open(cases_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()[:16]

    n_pos = sum(1 for r in rows if not r.get("should_clarify"))
    n_neg = len(rows) - n_pos
    add("=" * 78)
    add("E1 · 路由冲突回归集评测（离线，零 LLM 调用）")
    add("=" * 78)
    add("用例集      : %s" % cases_path)
    add("用例集版本  : %s（选择规则：%s）" % (version, selection or "显式 --cases"))
    add("用例集 sha256(16): %s   条数=%d（正样本 %d / 负·澄清样本 %d）"
        % (digest, len(cases), n_pos, n_neg))
    add("检索模式    : %s%s" % (mode, "   <- 覆盖了 worker 就绪超时（非生产默认，仅标定用）"
                                if non_production else ""))
    add("检索参数    : alpha=%.2f  max_tools=%d  实际候选池=%d  向量腿可用=%s"
        % (alpha, max_tools, _pool_size(top_k, max_tools), not retriever.degraded))
    add("")

    # ── 逐条 ──
    add("-" * 78)
    add("逐条结果（decision=融合 top1；payload=下发集）")
    add("-" * 78)
    add("%-8s %-4s %-18s %-8s %-6s %-5s %-9s %s"
        % ("id", "判定", "top1", "分差", "候选", "下发", "exp/pay", "forbidden 在下发集"))
    for r in rows:
        add("%-8s %-4s %-18s %-8.4f %-6d %-5d %-9s %s"
            % (r["id"], "OK" if r["passed"] else "FAIL",
               (r["top1"] or "-")[:18], r["gap"], r["n_ranked"], r["n_selected"],
               ("Y" if r["expect_hit_payload"] else "N") + "/" + ("Y" if not r["forbid_in_payload"] else "N"),
               ",".join(r["forbid_in_payload"]) or "-"))
    add("")

    summ = summarize(rows)
    add("=" * 78)
    add("混淆矩阵")
    add("=" * 78)
    d, p = summ["decision"], summ["payload"]
    add("【决策层 · 主判据】top1 属于 expect_tools 且 top1 不属于 forbid_tools")
    add("    expected  命中 / 未命中 : %d / %d" % (d["expect_hit"], d["expect_miss"]))
    add("    forbidden 误召 / 未误召 : %d / %d" % (d["forbid_recalled"], d["forbid_not_recalled"]))
    add("    通过 / 失败             : %d / %d   （通过率 %.2f%%）"
        % (d["passed"], d["failed"], d["pass_rate"] * 100))
    add("【下发层 · 诊断】expect_tools 属于 下发集；forbid 交集 下发集")
    add("    expected  命中 / 未命中 : %d / %d" % (p["expect_hit"], p["expect_miss"]))
    add("    forbidden 误召 / 未误召 : %d / %d （按条）；共 %d 个 forbidden 成员被下发"
        % (p["forbid_recalled_cases"], p["forbid_not_recalled_cases"], p["forbid_recalled_members"]))
    add("    top1 命中却未进下发集  : %d （检索排到了却拿不到）"
        % p["top1_hit_but_not_in_payload"])
    if summ["n_negative"]:
        cl = summ["clarify"]
        add("【负/澄清样本 · 检索侧诊断】共 %d 条，**不使用通过率**（无 expect 可命中）"
            % cl["n"])
        add("    零召回（检索什么都没给）: %d 条 %s"
            % (len(cl["zero_recall_cases"]), cl["zero_recall_cases"] or ""))
        add("    forbid 被排成 top1      : %d 条（检索侧误召，与门控无关）"
            % cl["forbid_hit_top1_cases"])
        add("    forbid 落入下发集      : %d 条" % cl["forbid_recalled_in_payload_cases"])
        add("    类目分布                : %s" % cl["by_category"])
    add("")

    gaps = [r["gap"] for r in rows if r["n_ranked"] > 0]
    add("=" * 78)
    add("分差分布（决策层 top1 分 − top2 分；无第 2 名时 top2 记 0.0）")
    add("=" * 78)
    if gaps:
        q = statistics.quantiles(gaps, n=4) if len(gaps) > 3 else [min(gaps), min(gaps), max(gaps)]
        add("    样本 n=%d    min=%.4f  p25=%.4f  中位=%.4f  p75=%.4f  max=%.4f"
            % (len(gaps), min(gaps), q[0], statistics.median(gaps), q[2], max(gaps)))
        add("    均值=%.4f  标准差=%.4f  不同取值数=%d"
            % (statistics.mean(gaps), statistics.pstdev(gaps), len(set(gaps))))
        for label, cnt in _histogram(gaps):
            add("    %-14s %s %d" % (label, "#" * cnt, cnt))
    else:
        add("    （无样本）")
    add("")

    cal = calibrate_tau(rows)
    add("=" * 78)
    add("τ 标定（通道 = 检索分差；主通道 logprob 物理不可用 —— 审计 §2.2 / T1）")
    add("=" * 78)
    add("    可标定样本 %d 条（零召回 %d 条不计入）"
        % (cal.get("n_scored", 0), cal.get("n_unscored", 0)))
    if cal.get("table"):
        add("    %-8s %-4s %-4s %-4s %-4s %-9s %-8s %-6s %s"
            % ("τ", "TP", "FP", "FN", "TN", "precision", "recall", "F1", "执行率"))
        for t in cal["table"]:
            add("    %-8.4f %-4d %-4d %-4d %-4d %-9.4f %-8.4f %-6.4f %.2f"
                % (t["tau"], t["TP"], t["FP"], t["FN"], t["TN"],
                   t["precision"], t["recall"], t["f1"], t["execute_rate"]))
        b = cal["best"]
        add("")
        if cal["degenerate"]:
            add("    >>> τ 标定结论：**不可标定**（所有候选 τ 的 F1 = 0）")
            add("        数据依据：决策层 top1 正确率 %.2f%%，分差不同取值数 %d ——"
                % (summ["decision"]["expect_hit"] / max(1, summ["decision"]["n_cases"]) * 100,
                   cal["n_distinct_gaps"]))
            add("        在「执行 = 采用 top1」的语义下，任何 τ 都只能二选一：")
            add("          · 全放行 ⇒ 误执行(假阳) %d 条；" % b["FP"])
            add("          · 全澄清 ⇒ 把 %d 条本该直接执行的路由也拦下(假阴)。" % b["FN"])
            add("        ⇒ 不给经验值。该通道当前无判别力，处置建议见报告 §4.3。")
        else:
            add("    >>> τ 建议值 = **%.4f**（判据：F1 最大，并列取更保守者）" % b["tau"])
            add("        该 τ 下：误执行(假阳)=%d 条，不必要澄清(假阴)=%d 条，"
                % (b["FP"], b["FN"]))
            add("        正确执行 TP=%d，正确澄清 TN=%d，执行率=%.2f%%"
                % (b["TP"], b["TN"], b["execute_rate"] * 100))
        if cal.get("conservative_zero_fn_tau") is not None:
            add("        参考点 A（FN=0 的最小 τ，宁可澄清不误执行）: %.4f"
                % cal["conservative_zero_fn_tau"])
        if cal.get("zero_fp_min_tau") is not None:
            zt = [t for t in cal["table"] if t["tau"] == cal["zero_fp_min_tau"]][0]
            add("        参考点 B（FP=0 的最小 τ，零误执行操作点）: %.4f"
                % cal["zero_fp_min_tau"])
            add("            该点：误执行=0，不必要澄清(假阴)=%d，正确执行 TP=%d，执行率=%.2f%%"
                % (zt["FN"], zt["TP"], zt["execute_rate"] * 100))
        gs = cal.get("gap_stats") or {}
        if gs.get("correct_top1", {}).get("n"):
            add("        区分度证据：top1 正确的样本分差 中位=%.4f 均值=%.4f（n=%d）；"
                % (gs["correct_top1"]["median"], gs["correct_top1"]["mean"],
                   gs["correct_top1"]["n"]))
            add("                    top1 错误的样本分差 中位=%.4f 均值=%.4f（n=%d）"
                % (gs["wrong_top1"]["median"], gs["wrong_top1"]["mean"],
                   gs["wrong_top1"]["n"]))
    add("")

    # ── E1-B：门的另一侧（负/澄清样本）——「该澄清时澄清」第一次有数 ──
    best_tau = cal["best"]["tau"] if cal.get("best") else None
    if best_tau is not None:
        apply_clarify_gate(rows, best_tau)
    neg_rows = [r for r in rows if r.get("should_clarify")]
    if neg_rows:
        add("=" * 78)
        add("★ 门的另一侧 · 负/澄清样本（E1 的 50 条全正样本 ⇒ 这一栏当时无数据）")
        add("=" * 78)
        cg = cal.get("clarify_at_best") or {}
        a, s = cg.get("all") or {}, cg.get("scored") or {}
        add("    判据：负样本 gap < τ ⇒ 门会澄清（正确）；gap >= τ ⇒ 会直接执行 top1（**误执行**）")
        add("    τ = %s（= 本次标定建议值）" % ("%.4f" % best_tau if best_tau is not None else "-"))
        add("    全部负样本     : %d/%d 正确澄清（准确率 %s）；误执行 %d 条"
            % (a.get("clarified", 0), a.get("n", 0), _acc_str(a), a.get("executed_wrong", 0)))
        add("    有候选的负样本 : %d/%d 正确澄清（准确率 %s）；误执行 %d 条"
            % (s.get("clarified", 0), s.get("n", 0), _acc_str(s), s.get("executed_wrong", 0)))
        if cg.get("zero_recall"):
            add("    （零召回 %d 条 %s 不计入「有候选」一栏：检索什么都没给，必然澄清，"
                % (len(cg["zero_recall"]), cg["zero_recall"]))
            add("      把它们算进「准确率」会把门的功劳记在检索的退化上 —— 口径必须写清）")
        add("")
        add("    %-8s %-9s %-8s %-6s %-8s %s" % ("id", "类目", "gap", "候选", "τ判定", "top1（本句最易被谁吸走）"))
        for r in neg_rows:
            verdict = ("澄清" if (r["n_ranked"] == 0 or
                                  (best_tau is not None and r["gap"] < best_tau))
                       else "**执行(误)**")
            add("    %-8s %-9s %-8.4f %-6d %-8s %s"
                % (r["id"], (r.get("category") or "-")[:9], r["gap"], r["n_ranked"],
                   verdict, r["top1"] or "(零召回)"))
        add("")
        # E1 口径对照：只用正样本标定出来的 τ，放到负样本上会怎样
        cal_pos = calibrate_tau(rows, include_negatives=False)
        if cal_pos.get("best"):
            tau_pos = cal_pos["best"]["tau"]
            gate_pos = clarify_gate_eval(rows, tau_pos)
            gp = gate_pos["scored"]
            add("    【E1 口径对照】只用**正样本**标定的 τ = %.4f（= E1 的标定方式）" % tau_pos)
            add("        把它放到负样本上：有候选的 %d 条里只有 %d 条会被澄清（%s），"
                % (gp["n"], gp["clarified"], _acc_str(gp)))
            add("        另 %d 条（%s）会被直接执行 ⇒ **这就是 E1 结构性看不见的那类错误**"
                % (gp["executed_wrong"], ",".join(gp.get("wrong_ids") or [])))
    add("")

    fails = [r for r in rows if not r["passed"]]
    add("=" * 78)
    add("失败用例（%d 条）" % len(fails))
    add("=" * 78)
    for r in fails:
        why = []
        if r.get("should_clarify"):
            why.append("负/澄清样本：门没拦住（gap=%.4f >= τ=%s），会直接执行 top1=%s"
                       % (r["gap"], ("%.4f" % best_tau) if best_tau is not None else "-",
                          r["top1"]))
        else:
            if not r["expect_hit_decision"]:
                why.append("top1=%s 不在 expect=%s 中" % (r["top1"], r["expect_tools"]))
            if r["forbid_hit_decision"]:
                why.append("top1 命中 forbidden=%s" % r["forbid_tools"])
        add("  %-8s %s" % (r["id"], "；".join(why)))
        add("           query=%s" % r["query"])
        add("           分差=%.4f top1_score=%.4f top2=%s(%.4f) 候选=%d expect在下发集=%s"
            % (r["gap"], r["top1_score"], r["top2"], r["top2_score"], r["n_ranked"],
               "Y" if r["expect_hit_payload"] else "N"))
    add("")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  main
# ════════════════════════════════════════════════════════════

def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser(description="E1 路由冲突回归集评测 + τ 标定（离线，零 LLM）")
    ap.add_argument("--root", default=_REPO_ROOT, help="仓库根（默认脚本上级目录）")
    ap.add_argument("--cases", default=None,
                    help="用例集路径（默认按 --cases-version 自动选择）")
    ap.add_argument("--cases-version", default="auto", choices=("auto", "1", "2"),
                    help="用例集版本选择：auto=取 data/eval 下版本号最大的 vN（E1-B 起为 v2）；"
                         "1=v1（E1 冻结集，50 条全正样本）；2=v2（含负/澄清样本）")
    ap.add_argument("--max-tools", type=int, default=25, help="下发集上限（生产默认 25）")
    ap.add_argument("--top-k", type=int, default=40, help="检索候选池（生产默认 40）")
    ap.add_argument("--alpha", type=float, default=None, help="BM25/Embedding 融合权重（默认取环境变量/0.5）")
    ap.add_argument("--no-embedding", action="store_true",
                    help="关掉向量腿（= 生产降级态），使 CI 结果可复现")
    ap.add_argument("--worker-ready-timeout", type=float, default=0.0,
                    help="覆盖 EmbeddingIndex worker 就绪超时（秒）。>30 即偏离生产默认，报告中会标注")
    ap.add_argument("--wait-embedding", type=float, default=0.0,
                    help="构造后最多等待向量腿就绪的秒数（默认 0 = 不等待）")
    ap.add_argument("--min-pass", type=int, default=None,
                    help="决策层通过数下限（默认取本机基线 BASELINE_BY_VERSION[版本][mode]）")
    ap.add_argument("--cross-holdout", action="store_true",
                    help="留出集跨集一致性：按 group-parity 双向「一半标定 τ → 另一半验证」（E1-B）")
    ap.add_argument("--json", default=None, help="把完整结果另存为 JSON（不给则只打印）")
    ap.add_argument("--list-failures", action="store_true", help="只打印失败用例")
    # ── E1-C 新增：α 扫描 + 留出集切分（不改任何既有默认行为）──
    ap.add_argument("--sweep", default=None,
                    help="α 扫描：逗号分隔的 α 列表（如 0,0.1,...,1.0）。"
                         "同一进程内每点全量重跑，模型只加载一次")
    ap.add_argument("--sweep-json", default=None,
                    help="α 扫描结果另存 JSON（含每点 summary/tau/rows）")
    ap.add_argument("--group-parity", default="all", choices=("all", "odd", "even"),
                    help="按 source_groups 最小值奇偶切分用例集（留出集用）。"
                         "注意：自检始终跑在**全集**上，切片只影响评测")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.root)
    if args.cases:
        cases_path = os.path.abspath(args.cases)
        version = _version_of_path(cases_path)
        selection = "显式 --cases"
    else:
        cases_path, version, selection = discover_cases(root, args.cases_version)

    cases = load_cases(cases_path)
    min_cases = MIN_CASES_BY_VERSION.get(version, _MIN_CASES)
    errors = validate_cases(cases, known_tools=known_tool_names(root),
                            min_cases=min_cases)
    if errors:
        print("[用例集自检 FAIL]")
        for e in errors:
            print("  - " + e)
        print("")
        print("退出码   : 1（用例集不合法）")
        return 1

    # 留出集切片：**自检已跑在全集上**，这里才切（守卫不被绕开）
    eval_cases = filter_cases_by_group_parity(cases, args.group_parity)

    trh, retriever = build_retriever(root, no_embedding=args.no_embedding,
                                     worker_ready_timeout=args.worker_ready_timeout)
    if args.wait_embedding and args.wait_embedding > 0:
        wait_embedding(retriever, args.wait_embedding)

    # ── α 扫描分支（E1-C 主路径）──
    if args.sweep:
        try:
            alphas = parse_alpha_list(args.sweep)
        except ValueError as exc:
            print("[--sweep 参数错误] %s" % exc)
            return 2
        points = run_sweep(eval_cases, trh, retriever, alphas,
                           max_tools=args.max_tools, top_k=args.top_k)
        print(format_sweep_table(points, cases_path, len(eval_cases),
                                 max_tools=args.max_tools, top_k=args.top_k))
        expected_mode = "bm25_only" if args.no_embedding else "hybrid"
        bad = [p["alpha"] for p in points if p["mode"] != expected_mode]
        print("-" * 118)
        print("每一 α 点的模式必须 = %s（--no-embedding=%s）；不符者作废：%s"
              % (expected_mode, args.no_embedding,
                 ", ".join("%.2f" % a for a in bad) if bad else "无"))
        print("用例切片 : group-parity=%s  评测条数=%d（自检仍在全集 %d 条上）"
              % (args.group_parity, len(eval_cases), len(cases)))
        if args.sweep_json:
            with open(args.sweep_json, "w", encoding="utf-8") as f:
                json.dump({
                    "cases_path": cases_path,
                    "group_parity": args.group_parity,
                    "n_cases": len(eval_cases),
                    "no_embedding": bool(args.no_embedding),
                    "batch_alpha": float(getattr(retriever, "_alpha", 0.5)),
                    "points": points,
                }, f, ensure_ascii=False, indent=1)
            print("JSON     : %s" % args.sweep_json)
        print("退出码   : %d" % (0 if not bad else 1))
        print("-" * 118)
        return 0 if not bad else 1

    rows = run_eval(eval_cases, trh, retriever, max_tools=args.max_tools,
                    top_k=args.top_k, alpha=args.alpha)
    mode = _mode_name(retriever)
    effective_alpha = float(getattr(retriever, "_alpha", 0.5))
    non_production = bool(args.worker_ready_timeout and args.worker_ready_timeout > 30.0)

    # 负/澄清样本的判据要先有 τ 才能定 ⇒ 先标定、回填，再打印
    cal_pre = calibrate_tau(rows)
    if cal_pre.get("best"):
        apply_clarify_gate(rows, cal_pre["best"]["tau"])

    if args.list_failures:
        for r in rows:
            if not r["passed"]:
                print("%s  top1=%s  expect=%s  forbid=%s  分差=%.4f  | %s"
                      % (r["id"], r["top1"], r["expect_tools"], r["forbid_tools"],
                         r["gap"], r["query"]))
    else:
        if args.group_parity != "all":
            print("用例切片  : group-parity=%s  评测条数=%d（自检仍在全集 %d 条上）"
                  % (args.group_parity, len(eval_cases), len(cases)))
        print(format_report(cases_path, eval_cases, rows, mode, retriever,
                            max_tools=args.max_tools, top_k=args.top_k,
                            alpha=effective_alpha, non_production=non_production,
                            version=version, selection=selection))
        if args.cross_holdout:
            print(format_cross_holdout(cross_holdout(rows, eval_cases), cases_path,
                                       len(eval_cases)))

    summ = summarize(rows)
    cal = calibrate_tau(rows)
    passed = summ["decision"]["passed"]

    if args.min_pass is not None:
        min_pass = args.min_pass
        baseline_src = "--min-pass 显式指定"
    elif args.group_parity != "all":
        # 基线是按**全集**标定的；切片后条数变了，拿全集基线卡半集必然假红
        # ⇒ 切片模式下不设下限（并在输出里写明），要卡门槛请显式传 --min-pass
        min_pass = 0
        baseline_src = "切片模式（group-parity=%s）不适用全集基线，下限按 0" % args.group_parity
    else:
        base, baseline_src = baseline_for(mode, version)
        min_pass = base["decision_pass"] if base else 0

    print("-" * 78)
    print("用例集   : %s（版本 %s）  正样本 %d / 负·澄清 %d"
          % (cases_path, version, summ["n_positive"], summ["n_negative"]))
    print("达标判据 : 用例集自检通过 且 决策层通过数 >= 下限")
    print("下限     : %d（%s）" % (min_pass, baseline_src))
    print("实测     : 决策层通过 %d/%d（%.2f%%）；下发层 expected 命中 %d/%d"
          % (passed, summ["n_positive"], summ["decision"]["pass_rate"] * 100,
             summ["payload"]["expect_hit"], summ["n_positive"]))
    print("τ 建议   : %s" % ("不可标定（F1 全 0，见上）" if cal.get("degenerate")
                             else ("%.4f" % cal["best"]["tau"] if cal.get("best") else "无数据")))
    cg = cal.get("clarify_at_best") or {}
    if (cg.get("all") or {}).get("n"):
        print("门的另一侧: 负/澄清样本 %d/%d 正确澄清（有候选的 %d/%d），误执行 %d 条"
              % (cg["all"]["clarified"], cg["all"]["n"],
                 (cg.get("scored") or {}).get("clarified", 0), (cg.get("scored") or {}).get("n", 0),
                 cg["all"]["executed_wrong"]))
    rc = 0 if passed >= min_pass else 1
    print("退出码   : %d" % rc)
    print("-" * 78)

    if args.json:
        payload = {"mode": mode, "cases_path": cases_path, "cases_version": version,
                   "summary": summ, "tau": cal,
                   # 只用正样本标定的 τ（E1 口径），供对照
                   "tau_positive_only": calibrate_tau(rows, include_negatives=False),
                   "rows": rows}
        if args.cross_holdout:
            payload["cross_holdout"] = cross_holdout(rows, eval_cases)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
