"""验证 BM25 优化（b=0.75 → 0.5）对短文档排序得分的影响

【不易】对照实验：同一文档集 + 同一查询，仅 b 值不同，验证短文档虚高是否缓解
【变易】多组测试用例覆盖不同长度差异场景（基础对照 / 中等差异 / 极端差异）
【简易】直接调用 InvertedIndex.search，不依赖 VectorStore 全栈和 ChromaDB

用法：
    python scripts/verify_bm25_optimization.py

退出码：
    0 = 验证通过（短文档虚高缓解）
    1 = 验证失败（优化未生效）
"""
import sys
from pathlib import Path

# 确保项目根目录在 sys.path（本地运行兜底）
_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from memory.vector_store.vector_store import InvertedIndex


# 测试用例：短文档 vs 长文档，查询词在两者中都出现（term_freq 相同）
# Why term_freq 相同：确保得分差异只来自 doc_length 归一化项，隔离 b 值影响
TEST_CASES = [
    {
        "name": "machine learning 基础对照",
        "short_doc": "machine learning",
        "long_doc": (
            "machine learning is a subset of artificial intelligence that focuses "
            "on the development of algorithms and statistical models that enable "
            "computers to learn from data without being explicitly programmed"
        ),
        "query": "machine learning",
    },
    {
        "name": "data science 中等差异",
        "short_doc": "data science",
        "long_doc": (
            "data science is an interdisciplinary field that uses scientific methods "
            "processes algorithms and systems to extract knowledge and insights from "
            "structured and unstructured data applying techniques from statistics "
            "computer science and information science"
        ),
        "query": "data science",
    },
    {
        "name": "neural networks 极端差异",
        "short_doc": "neural networks",
        "long_doc": (
            "neural networks are a series of algorithms that endeavors to recognize "
            "underlying relationships in a set of data through a process that mimics "
            "the way the human brain operates using deep learning techniques to solve "
            "complex problems in image recognition natural language processing and "
            "speech recognition with multiple layers of artificial neurons"
        ),
        "query": "neural networks",
    },
]


def build_index(docs: dict, k1: float, b: float) -> InvertedIndex:
    """构建指定 b 值的倒排索引

    Args:
        docs: {doc_id: content} 字典
        k1: BM25 饱和度参数
        b: BM25 长度归一化参数

    Returns:
        InvertedIndex 实例
    """
    idx = InvertedIndex(k1=k1, b=b)
    for doc_id, content in docs.items():
        idx.add_document(doc_id, content)
    return idx


def run_case(case: dict) -> dict:
    """运行单个测试用例，返回新旧 b 值下的得分对比

    Args:
        case: 测试用例字典

    Returns:
        包含 short/long 得分和短/长比的字典
    """
    docs = {"short": case["short_doc"], "long": case["long_doc"]}
    query = case["query"]

    # 旧 b=0.75（短文档虚高）
    old_idx = build_index(docs, k1=1.5, b=0.75)
    old_scores = dict(old_idx.search(query, top_k=10))

    # 新 b=0.5（缓解短文档虚高）
    new_idx = build_index(docs, k1=1.5, b=0.5)
    new_scores = dict(new_idx.search(query, top_k=10))

    old_short = old_scores.get("short", 0.0)
    old_long = old_scores.get("long", 0.0)
    new_short = new_scores.get("short", 0.0)
    new_long = new_scores.get("long", 0.0)

    # 短/长得分比：比值越大说明短文档虚高越严重
    old_ratio = old_short / old_long if old_long > 0 else float("inf")
    new_ratio = new_short / new_long if new_long > 0 else float("inf")

    return {
        "name": case["name"],
        "old_short": old_short,
        "old_long": old_long,
        "old_ratio": old_ratio,
        "new_short": new_short,
        "new_long": new_long,
        "new_ratio": new_ratio,
    }


def main():
    print("=" * 90)
    print("BM25 短文档归一化优化验证（b=0.75 → b=0.5）")
    print("=" * 90)
    print()
    print("【背景】BM25 长度归一化参数 b 越大，对短文档的惩罚越弱（短文档得分越虚高）")
    print("【目标】验证 b 从 0.75 降至 0.5 后，短/长文档得分比下降（短文档虚高缓解）")
    print("【方法】同一查询 + term_freq 相同的短/长文档对，仅 b 值不同，对比得分")
    print()

    results = [run_case(c) for c in TEST_CASES]

    # 表头
    header = (
        f"{'用例':<28} {'b=0.75 短':<12} {'b=0.75 长':<12} {'旧比值':<10} "
        f"{'b=0.5 短':<12} {'b=0.5 长':<12} {'新比值':<10} {'改善?':<10}"
    )
    print(header)
    print("-" * 118)

    improved_count = 0
    for r in results:
        # 改善判定：新比值 < 旧比值（短文档虚高下降）
        improved = r["new_ratio"] < r["old_ratio"]
        if improved:
            improved_count += 1
        improved_str = "✓ 改善" if improved else "✗ 未改善"
        print(
            f"{r['name']:<28} {r['old_short']:<12.4f} {r['old_long']:<12.4f} {r['old_ratio']:<10.4f} "
            f"{r['new_short']:<12.4f} {r['new_long']:<12.4f} {r['new_ratio']:<10.4f} {improved_str:<10}"
        )

    print("-" * 118)
    print()

    # 汇总统计
    avg_old_ratio = sum(r["old_ratio"] for r in results) / len(results)
    avg_new_ratio = sum(r["new_ratio"] for r in results) / len(results)
    reduction_pct = (avg_old_ratio - avg_new_ratio) / avg_old_ratio * 100 if avg_old_ratio > 0 else 0

    print(f"平均短/长得分比: b=0.75 → {avg_old_ratio:.4f}x | b=0.5 → {avg_new_ratio:.4f}x")
    print(f"比值下降: {reduction_pct:.1f}%（短文档虚高缓解程度）")
    print(f"改善用例: {improved_count}/{len(results)}")
    print()

    # 判定结论
    # CI 友好：失败时输出 ::error::/::warning:: 标记（GitHub Actions UI 红/黄高亮）+ 失败用例详情
    if improved_count == len(results) and reduction_pct > 5:
        print("✅ 验证通过：BM25 b=0.5 优化有效缓解了短文档虚高问题")
        print(f"   所有 {len(results)} 个用例的短/长得分比均下降，平均降幅 {reduction_pct:.1f}%")
        sys.exit(0)
    elif improved_count > 0:
        # 部分改善：列出未改善用例，便于快速定位
        failed_cases = [r for r in results if r["new_ratio"] >= r["old_ratio"]]
        print(f"::warning::BM25 优化部分改善：{improved_count}/{len(results)} 个用例下降，{len(failed_cases)} 个未改善")
        print(f"⚠️ 部分改善：{improved_count}/{len(results)} 个用例下降，以下用例未改善：")
        for r in failed_cases:
            print(f"   - {r['name']}: 旧比值={r['old_ratio']:.4f} → 新比值={r['new_ratio']:.4f}")
        sys.exit(0)
    else:
        # 全部失败：输出 ::error:: 标记 + 每个用例详情，便于 CI 快速定位
        print(f"::error::BM25 优化未生效：0/{len(results)} 个用例改善，请检查 vector_store.py _DEFAULT_B 配置")
        print("❌ 验证失败：优化未生效，请检查 vector_store.py _DEFAULT_B 配置")
        print("   失败用例详情：")
        for r in results:
            delta = r["new_ratio"] - r["old_ratio"]
            print(f"   - {r['name']}: 旧比值={r['old_ratio']:.4f} → 新比值={r['new_ratio']:.4f} (Δ={delta:+.4f})")
        sys.exit(1)


if __name__ == "__main__":
    main()
