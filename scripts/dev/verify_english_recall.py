"""真实 tool_router_hybrid 检索召回验证脚本

覆盖 4 组验证:
1. 英文查询(真实索引 = 中文描述 + 英文别名) — BM25 路 / 融合路(alpha 可配)
2. 中英混合查询(极端混合场景,真实索引)
3. 非英文工具模拟(日文/法文描述 ± 英文别名) — 验证别名方案通用性
4. 中文查询回归(真实索引) — 别名不伤害中文召回

用法:
    python scripts/dev/verify_english_recall.py                 # 全部组(BM25 路,快速)
    python scripts/dev/verify_english_recall.py --hybrid        # 额外跑融合路(可能加载模型)
    python scripts/dev/verify_english_recall.py --alpha 0.5 --hybrid
    python scripts/dev/verify_english_recall.py --bm25-only     # 只跑真实索引 BM25 组
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

# ────────────────────────────────────────────────────────────
#  真实索引英文查询:query -> 期望命中的工具
# ────────────────────────────────────────────────────────────
ENGLISH_QUERIES: list[tuple[str, str]] = [
    ("extract text from pdf", "read_pdf"),
    ("merge two pdf files", "merge_pdf"),
    ("split pdf into pages", "split_pdf"),
    ("get pdf metadata and page count", "get_pdf_info"),
    ("search the web for news", "web_search"),
    ("fetch this url page", "web_get"),
    ("run shell command", "shell_execute"),
    ("read a local file", "read_file"),
    ("find files by pattern", "search_files"),
    ("get weather in beijing", "get_weather"),
]

# ────────────────────────────────────────────────────────────
#  中英混合查询(极端混合场景):query 内中英混排
# ────────────────────────────────────────────────────────────
MIXED_QUERIES: list[tuple[str, str]] = [
    ("extract pdf 里的文本", "read_pdf"),
    ("把多个 pdf merge 成一个文件", "merge_pdf"),
    ("split 这个 pdf 成多页", "split_pdf"),
    ("查 pdf 的 metadata 信息", "get_pdf_info"),
    ("在 web 上 search 信息", "web_search"),
    ("fetch 这个 url", "web_get"),
    ("跑个 shell command", "shell_execute"),
    ("读取本地 file 内容", "read_file"),
    ("按 pattern 找文件", "search_files"),
    ("get 北京的 weather", "get_weather"),
]

# ────────────────────────────────────────────────────────────
#  中文查询回归(别名不伤害中文召回)
# ────────────────────────────────────────────────────────────
CHINESE_REGRESSION_QUERIES: list[tuple[str, str]] = [
    ("解析pdf", "read_pdf"),
    ("合并pdf", "merge_pdf"),
    ("拆分pdf", "split_pdf"),
    ("查询天气", "get_weather"),
    ("搜索网页", "web_search"),
]

# ────────────────────────────────────────────────────────────
#  非英文工具模拟数据(验证别名方案通用性)
#  描述语言:日文(ja) / 法文(fr);± 英文别名
# ────────────────────────────────────────────────────────────
MULTILINGUAL_TOOLS: list[dict] = [
    {
        "name": "ja_pdf",
        "description": "PDFファイルからテキストを抽出します。Extract text from PDF files, parse pdf documents",
        "lang": "ja",
        "has_alias": True,
    },
    {
        "name": "fr_pdf",
        "description": "Extraire le texte de fichiers PDF. Extract text from PDF files, parse pdf documents",
        "lang": "fr",
        "has_alias": True,
    },
    {
        "name": "ja_weather",
        "description": "都市の天気と気温を取得します",
        "lang": "ja",
        "has_alias": False,
    },
    {
        "name": "fr_weather",
        "description": "Obtenir la météo et la température d'une ville",
        "lang": "fr",
        "has_alias": False,
    },
]

MULTILINGUAL_QUERIES: list[tuple[str, str, set[str]]] = [
    # (query, kind, allowed) kind: alias=英文查询命中带别名工具 / native=原语言匹配 / negative=负向
    ("extract text from pdf", "alias", {"ja_pdf", "fr_pdf"}),
    ("parse a pdf document", "alias", {"ja_pdf", "fr_pdf"}),
    ("météo de paris", "native", {"fr_weather"}),
    ("get weather in tokyo", "negative", set()),
]


# ────────────────────────────────────────────────────────────
#  工具加载与索引构建
# ────────────────────────────────────────────────────────────
def _load_tools(index_path: str) -> list[dict]:
    with open(index_path, "r", encoding="utf-8") as f:
        return json.load(f).get("tools", [])


def _build_bm25(tools: list[dict]):
    from agent.tool_router_hybrid import BM25Index

    idx = BM25Index()
    for t in tools:
        name = t.get("name")
        if not name:  # 防御:缺 name 的条目无法作为 doc_id,跳过而非崩溃
            continue
        content = (
            f"{name} "
            f"{' '.join(t.get('parameter_names', []) or [])} "
            f"{t.get('description', '')}"
        )
        idx.add_document(name, content)
    return idx


def _check(idx, queries: list[tuple[str, str]], top_k: int = 5, mode: str = "top1") -> dict:
    """对查询组执行命中检查,返回结构化统计(含耗时)

    mode="top1":   top1 精确命中期望工具(强断言,用于英文/混合查询)
    mode="recall": 期望工具出现在 top5 内即命中(召回断言,用于中文回归)
    """
    cases = []
    hits = 0
    total_ms = 0.0
    for query, expected in queries:
        t0 = time.perf_counter()
        results = idx.search(query, top_k=top_k)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        total_ms += elapsed_ms
        top_names = [d for d, _ in results]
        if mode == "recall":
            hit = expected in top_names
        else:
            hit = bool(top_names) and top_names[0] == expected
        hits += int(hit)
        cases.append(
            {
                "query": query,
                "expected": expected,
                "top1": top_names[0] if top_names else None,
                "top3": top_names[:3],
                "hit": hit,
                "elapsed_ms": round(elapsed_ms, 3),
            }
        )
    return {
        "hits": hits,
        "total": len(queries),
        "cases": cases,
        "total_ms": round(total_ms, 3),
        "avg_ms": round(total_ms / max(len(queries), 1), 3),
    }


# ────────────────────────────────────────────────────────────
#  验证组
# ────────────────────────────────────────────────────────────
def _run_english_bm25(index_path: str) -> dict:
    """组1:英文查询 BM25 路(真实索引)"""
    tools = _load_tools(index_path)
    idx = _build_bm25(tools)
    stats = _check(idx, ENGLISH_QUERIES)
    print(f"=== [1] BM25 英文查询(工具数={len(tools)}) ===")
    for c in stats["cases"]:
        mark = "+" if c["hit"] else " "
        extra = "" if c["hit"] else f" (期望={c['expected']}, top1={c['top1']})"
        print(f"  [{mark}] {c['query']!r} -> {c['top3']}{extra}")
    print(f"  命中率: {stats['hits']}/{stats['total']}; "
          f"总耗时 {stats['total_ms']}ms, 平均 {stats['avg_ms']}ms/查询")
    return {"group": "bm25_english", **stats}


def _run_mixed_bm25(index_path: str) -> dict:
    """组2:中英混合查询 BM25 路(真实索引,极端混合场景)"""
    tools = _load_tools(index_path)
    idx = _build_bm25(tools)
    stats = _check(idx, MIXED_QUERIES)
    print(f"\n=== [2] BM25 中英混合查询(极端混合场景) ===")
    for c in stats["cases"]:
        mark = "+" if c["hit"] else " "
        extra = "" if c["hit"] else f" (期望={c['expected']}, top1={c['top1']})"
        print(f"  [{mark}] {c['query']!r} -> {c['top3']}{extra}")
    print(f"  命中率: {stats['hits']}/{stats['total']}; "
          f"总耗时 {stats['total_ms']}ms, 平均 {stats['avg_ms']}ms/查询")
    return {"group": "bm25_mixed", **stats}


def _run_chinese_regression(index_path: str) -> dict:
    """组3:中文查询回归(别名不伤害中文召回,top5 召回断言)"""
    tools = _load_tools(index_path)
    idx = _build_bm25(tools)
    stats = _check(idx, CHINESE_REGRESSION_QUERIES, mode="recall")
    print(f"\n=== [3] BM25 中文查询回归(top5 召回,别名不伤害中文召回) ===")
    for c in stats["cases"]:
        mark = "+" if c["hit"] else " "
        extra = "" if c["hit"] else f" (期望={c['expected']}, top1={c['top1']})"
        print(f"  [{mark}] {c['query']!r} -> {c['top3']}{extra}")
    print(f"  召回率: {stats['hits']}/{stats['total']}; "
          f"总耗时 {stats['total_ms']}ms, 平均 {stats['avg_ms']}ms/查询")
    return {"group": "bm25_chinese_regression", **stats}


def _run_multilingual_mock() -> dict:
    """组4:非英文工具(日/法描述)模拟 — 别名方案通用性

    对照设计:
      - alias 正向:日/法描述 + 英文别名 → 英文查询应召回(top1 命中带别名工具)
      - native 正向:法文查询 → 命中法文描述(同语言匹配,不依赖别名,能力不丢失)
      - negative 负向:英文查询 → 无英文别名的非英文描述不应被召回(零字面失效)
    """
    idx = _build_bm25(MULTILINGUAL_TOOLS)
    print("\n=== [4] 非英文工具模拟(日文/法文描述,验证别名通用性) ===")
    cases = []
    alias_hits = 0
    alias_total = 0
    native_hits = 0
    native_total = 0
    neg_hits = 0
    neg_total = 0
    for query, kind, allowed in MULTILINGUAL_QUERIES:
        results = idx.search(query, top_k=5)
        top_names = [d for d, _ in results]
        top1 = top_names[0] if top_names else None
        hit = False
        if kind == "alias":
            alias_total += 1
            hit = top1 in allowed
            alias_hits += int(hit)
            mark = "+" if hit else " "
            print(f"  [{mark}] {query!r} -> top1={top1} (别名召回,期望含 {sorted(allowed)})")
        elif kind == "native":
            native_total += 1
            hit = top1 in allowed
            native_hits += int(hit)
            mark = "+" if hit else " "
            print(f"  [{mark}] {query!r} -> top1={top1} (原语言匹配,期望含 {sorted(allowed)})")
        else:  # negative
            neg_total += 1
            hit = not any(n in {"ja_weather", "fr_weather"} for n in top_names)
            neg_hits += int(hit)
            mark = "+" if hit else " "
            print(f"  [{mark}] {query!r} -> top5={top_names} (无别名非英文描述不应被英文召回)")
        cases.append({"query": query, "kind": kind, "top3": top_names, "hit": hit})
    print(f"  别名召回命中率: {alias_hits}/{alias_total}; 原语言匹配: {native_hits}/{native_total}; "
          f"负向守位: {neg_hits}/{neg_total}")
    return {
        "group": "multilingual_mock",
        "hits": alias_hits,
        "total": alias_total,
        "native_hits": native_hits,
        "native_total": native_total,
        "negative_hits": neg_hits,
        "negative_total": neg_total,
        "cases": cases,
    }


def _run_hybrid_check(
    index_path: str,
    alpha: float,
    label: str,
    queries: list[tuple[str, str]],
    mode: str = "top1",
) -> dict:
    """融合路:HybridRetriever.query(top_k=5)(alpha 可配)

    mode="top1":   top1 精确命中(英文/混合查询)
    mode="recall": top5 召回断言(中文回归)
    """
    from agent.tool_router_hybrid import HybridRetriever, reset_hybrid_retriever

    reset_hybrid_retriever()  # 每组独立构造,避免单例缓存串组
    retriever = HybridRetriever(alpha=alpha, index_path=index_path)
    degraded = retriever.degraded
    print(f"\n=== [融合路] {label}(alpha={alpha}, degraded={degraded}) ===")
    if not retriever.available:
        print("  retriever 不可用(索引加载失败)")
        return {"group": f"hybrid_{label}", "alpha": alpha, "degraded": degraded,
                "hits": 0, "total": 0, "cases": [], "total_ms": 0.0, "avg_ms": 0.0}
    cases = []
    hits = 0
    total_ms = 0.0
    for query, expected in queries:
        t0 = time.perf_counter()
        results = retriever.query(query, top_k=5) or []
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        total_ms += elapsed_ms
        top_names = [d for d, _ in results]
        if mode == "recall":
            hit = expected in top_names
        else:
            hit = bool(top_names) and top_names[0] == expected
        hits += int(hit)
        cases.append(
            {
                "query": query,
                "expected": expected,
                "top1": top_names[0] if top_names else None,
                "hit": hit,
                "elapsed_ms": round(elapsed_ms, 3),
                "scores": {d: round(s, 3) for d, s in results[:3]},
            }
        )
        mark = "+" if hit else " "
        print(f"  [{mark}] {query!r} -> {', '.join(f'{d}:{s:.3f}' for d, s in results[:3])}"
              f" ({elapsed_ms:.2f}ms)")
    total_ms = round(total_ms, 3)
    avg_ms = round(total_ms / max(len(queries), 1), 3)
    print(f"  命中率: {hits}/{len(queries)}; "
          f"总耗时 {total_ms}ms, 平均 {avg_ms}ms/查询")
    return {"group": f"hybrid_{label}", "alpha": alpha, "degraded": degraded, "hits": hits,
            "total": len(queries), "cases": cases,
            "total_ms": total_ms, "avg_ms": avg_ms}


def _positive_alpha(value: str) -> float:
    """--alpha 参数解析:非法/越界值直接报错(与 _resolve_alpha_from_env 的 env 回退策略区分)

    Why: CLI 显式传参优先级最高,传错应 fail-fast,而不是静默回退掩盖误配。
    """
    try:
        val = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"alpha 必须为数字,收到 {value!r}")
    if not (0.0 <= val <= 1.0):
        raise argparse.ArgumentTypeError(f"alpha 必须在 [0,1] 内,收到 {val!r}")
    return val


def main() -> int:
    parser = argparse.ArgumentParser(description="工具混合检索召回验证")
    parser.add_argument("--index", default=os.path.join(_PROJECT_ROOT, "data", "tool_index.json"))
    parser.add_argument("--hybrid", action="store_true", help="跑融合路(可能加载模型)")
    parser.add_argument("--alpha", type=_positive_alpha, default=0.5, help="融合权重(默认 0.5)")
    parser.add_argument("--bm25-only", action="store_true", help="只跑真实索引 BM25 组(1/2/3),跳过模拟组")
    args = parser.parse_args()

    if not os.path.exists(args.index):
        print(f"索引文件不存在: {args.index}")
        return 1

    if args.bm25_only:
        # 只跑真实索引 BM25 组(1/2/3),不跑模拟组/融合路
        _run_english_bm25(args.index)
        _run_mixed_bm25(args.index)
        _run_chinese_regression(args.index)
        return 0

    _run_english_bm25(args.index)
    _run_mixed_bm25(args.index)
    _run_chinese_regression(args.index)
    _run_multilingual_mock()
    if args.hybrid:
        _run_hybrid_check(args.index, args.alpha, "英文查询", ENGLISH_QUERIES)
        _run_hybrid_check(args.index, args.alpha, "中英混合查询", MIXED_QUERIES)
        _run_hybrid_check(args.index, args.alpha, "中文回归", CHINESE_REGRESSION_QUERIES, mode="recall")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
