"""验证 b=0.5 在极端短文档场景下的表现

【不易】诚实报告：BM25 的 b 参数只能缓解短文档虚高，数学上无法彻底消除
        （b=1.0 才完全归一化，但会损害长文档区分度）。本脚本验证"缓解程度"
        而非"彻底消除"，避免给出虚假承诺。
【变易】覆盖 5 类极端场景：1-token 文档 / term_freq 极端差异 / 多短文档排序 /
        空文档边界 / b 值敏感性扫描
【简易】直接调用 InvertedIndex，输出对照表 + 诚实结论

用法：
    python scripts/verify_bm25_extreme_cases.py

退出码：
    0 = b=0.5 在极端场景下表现合理（虚高在可接受范围）
    1 = b=0.5 在极端场景下虚高超出阈值（需重新调参）
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from memory.vector_store.vector_store import InvertedIndex


# ============================================================================
# 场景 1：1-token 最短短文档 vs 50-token 长文档（极端长度差异）
# ============================================================================
SCENARIO_1 = {
    "name": "1-token 短文档 vs 50-token 长文档",
    "docs": {
        "tiny": "machine",  # 1 token
        "long": " ".join(["machine"] + ["data"] * 49),  # 50 tokens, machine 出现 1 次
    },
    "query": "machine",
    "expectation": "短文档 term 密度 100%，长文档 2%，短文档应适度偏高但不应极端虚高",
}

# ============================================================================
# 场景 2：term_freq 极端差异（短文档重复 term vs 长文档单次 term）
# ============================================================================
SCENARIO_2 = {
    "name": "term_freq 极端差异（短文档重复 5 次 vs 长文档 1 次）",
    "docs": {
        "dense_short": "machine machine machine machine machine",  # 5 tokens, 全是 machine
        "sparse_long": " ".join(["machine"] + ["algorithm"] * 49),  # 50 tokens, machine 1 次
    },
    "query": "machine",
    "expectation": "短文档 term_freq=5 饱和，长文档 term_freq=1，差异主要来自 tf 饱和项而非长度",
}

# ============================================================================
# 场景 3：多短文档间相对排序（1/2/3-token 文档）
# ============================================================================
SCENARIO_3 = {
    "name": "多短文档排序（1/2/3-token 递增）",
    "docs": {
        "d1": "machine",  # 1 token
        "d2": "machine learning",  # 2 tokens
        "d3": "machine learning algorithms",  # 3 tokens
        "d50": " ".join(["machine"] + ["data"] * 49),  # 50 tokens
    },
    "query": "machine",
    "expectation": "短文档得分应随长度递减（d1 > d2 > d3 > d50），但梯度应平缓而非悬崖",
}

# ============================================================================
# 场景 4：空文档边界（无有效 token）
# ============================================================================
SCENARIO_4 = {
    "name": "空文档边界（无 >=3 字符 token）",
    "docs": {
        "empty": "a b c d",  # 无有效 token（全 <3 字符）
        "valid": "machine learning",
    },
    "query": "machine",
    "expectation": "空文档不应进入索引，得分为 0，不影响其他文档排序",
}

# ============================================================================
# 场景 5：b 值敏感性扫描（b=0.0 / 0.25 / 0.5 / 0.75 / 1.0）
# ============================================================================
SCENARIO_5 = {
    "name": "b 值敏感性扫描",
    "docs": {
        "short": "machine learning",
        "long": " ".join(["machine", "learning"] + ["data"] * 48),
    },
    "query": "machine learning",
    "b_values": [0.0, 0.25, 0.5, 0.75, 1.0],
    "expectation": "短/长得分比应随 b 增大而下降，b=0.5 应处于合理折中点",
}


def build_index(docs: dict, k1: float, b: float) -> InvertedIndex:
    """构建指定 b 值的倒排索引"""
    idx = InvertedIndex(k1=k1, b=b)
    for doc_id, content in docs.items():
        idx.add_document(doc_id, content)
    return idx


def get_scores(docs: dict, query: str, k1: float, b: float) -> dict:
    """返回 {doc_id: score}"""
    idx = build_index(docs, k1=k1, b=b)
    return dict(idx.search(query, top_k=100))


def run_scenario_1():
    """场景 1：1-token vs 50-token"""
    print("\n【场景 1】" + SCENARIO_1["name"])
    print(f"   预期：{SCENARIO_1['expectation']}")
    docs = SCENARIO_1["docs"]

    old_scores = get_scores(docs, SCENARIO_1["query"], k1=1.5, b=0.75)
    new_scores = get_scores(docs, SCENARIO_1["query"], k1=1.5, b=0.5)

    old_ratio = old_scores["tiny"] / old_scores["long"] if old_scores.get("long", 0) > 0 else float("inf")
    new_ratio = new_scores["tiny"] / new_scores["long"] if new_scores.get("long", 0) > 0 else float("inf")

    print(f"   b=0.75: tiny={old_scores.get('tiny', 0):.4f}, long={old_scores.get('long', 0):.4f}, 比值={old_ratio:.2f}x")
    print(f"   b=0.5 : tiny={new_scores.get('tiny', 0):.4f}, long={new_scores.get('long', 0):.4f}, 比值={new_ratio:.2f}x")
    print(f"   缓解：比值 {old_ratio:.2f}x → {new_ratio:.2f}x（{(old_ratio-new_ratio)/old_ratio*100:.1f}%）")

    # 诚实判定：极端场景下短文档仍会偏高（BM25 设计如此），但应低于阈值
    # Why 阈值 3.0x: 短文档 term 密度 100% vs 2%，理论上短文档得分更高是合理的
    #    但超过 3x 说明长度归一化不足，虚高明显
    return new_ratio < 3.0, new_ratio


def run_scenario_2():
    """场景 2：term_freq 极端差异"""
    print("\n【场景 2】" + SCENARIO_2["name"])
    print(f"   预期：{SCENARIO_2['expectation']}")
    docs = SCENARIO_2["docs"]

    old_scores = get_scores(docs, SCENARIO_2["query"], k1=1.5, b=0.75)
    new_scores = get_scores(docs, SCENARIO_2["query"], k1=1.5, b=0.5)

    old_ratio = old_scores["dense_short"] / old_scores["sparse_long"] if old_scores.get("sparse_long", 0) > 0 else float("inf")
    new_ratio = new_scores["dense_short"] / new_scores["sparse_long"] if new_scores.get("sparse_long", 0) > 0 else float("inf")

    print(f"   b=0.75: dense_short={old_scores.get('dense_short', 0):.4f}, sparse_long={old_scores.get('sparse_long', 0):.4f}, 比值={old_ratio:.2f}x")
    print(f"   b=0.5 : dense_short={new_scores.get('dense_short', 0):.4f}, sparse_long={new_scores.get('sparse_long', 0):.4f}, 比值={new_ratio:.2f}x")
    print(f"   缓解：比值 {old_ratio:.2f}x → {new_ratio:.2f}x（{(old_ratio-new_ratio)/old_ratio*100:.1f}%）")

    # term_freq=5 已接近 k1=1.5 饱和，比值主要来自 tf 饱和而非长度
    # 阈值 5.0x: tf 饱和项贡献上限约 (k1+1)/1 = 2.5x，加上长度差异允许 2x
    return new_ratio < 5.0, new_ratio


def run_scenario_3():
    """场景 3：多短文档排序"""
    print("\n【场景 3】" + SCENARIO_3["name"])
    print(f"   预期：{SCENARIO_3['expectation']}")
    docs = SCENARIO_3["docs"]

    new_scores = get_scores(docs, SCENARIO_3["query"], k1=1.5, b=0.5)

    print(f"   b=0.5 得分:")
    for doc_id in ["d1", "d2", "d3", "d50"]:
        score = new_scores.get(doc_id, 0.0)
        print(f"      {doc_id:>4}: {score:.4f}")

    # 验证排序：d1 >= d2 >= d3 >= d50（短文档得分递减）
    s1, s2, s3, s50 = [new_scores.get(k, 0.0) for k in ["d1", "d2", "d3", "d50"]]
    ordered = s1 >= s2 >= s3 >= s50

    # 验证梯度平缓：d1/d50 比值不应过大（< 5x 表示梯度合理）
    ratio = s1 / s50 if s50 > 0 else float("inf")
    print(f"   排序正确（d1≥d2≥d3≥d50）: {'✓' if ordered else '✗'}")
    print(f"   d1/d50 比值: {ratio:.2f}x（梯度{'平缓' if ratio < 5 else '陡峭'}）")

    return ordered and ratio < 5.0, ratio


def run_scenario_4():
    """场景 4：空文档边界"""
    print("\n【场景 4】" + SCENARIO_4["name"])
    print(f"   预期：{SCENARIO_4['expectation']}")
    docs = SCENARIO_4["docs"]

    new_scores = get_scores(docs, SCENARIO_4["query"], k1=1.5, b=0.5)

    empty_score = new_scores.get("empty", 0.0)
    valid_score = new_scores.get("valid", 0.0)

    print(f"   b=0.5: empty={empty_score:.4f}, valid={valid_score:.4f}")

    # 空文档不应出现在搜索结果中（得分为 0 或不存在）
    passed = empty_score == 0.0
    print(f"   空文档得分为 0: {'✓' if passed else '✗'}")

    return passed, empty_score


def run_scenario_5():
    """场景 5：b 值敏感性扫描"""
    print("\n【场景 5】" + SCENARIO_5["name"])
    print(f"   预期：{SCENARIO_5['expectation']}")
    docs = SCENARIO_5["docs"]
    query = SCENARIO_5["query"]

    print(f"   {'b 值':<8} {'短文档':<12} {'长文档':<12} {'短/长比':<10} {'虚高程度':<10}")
    print("   " + "-" * 55)

    ratios = []
    for b in SCENARIO_5["b_values"]:
        scores = get_scores(docs, query, k1=1.5, b=b)
        short_s = scores.get("short", 0.0)
        long_s = scores.get("long", 0.0)
        ratio = short_s / long_s if long_s > 0 else float("inf")
        ratios.append(ratio)

        # 虚高程度描述
        if ratio < 1.2:
            level = "无虚高"
        elif ratio < 1.5:
            level = "轻微"
        elif ratio < 2.0:
            level = "中等"
        elif ratio < 3.0:
            level = "偏高"
        else:
            level = "严重"

        print(f"   {b:<8.2f} {short_s:<12.4f} {long_s:<12.4f} {ratio:<10.2f} {level:<10}")

    # 验证：比值随 b 增大而增大（b 越大，短文档虚高越严重）
    # Why: BM25 中 b 控制长度归一化强度，b 增大 → 短文档 denominator 减小（得分升高）
    #      + 长文档 denominator 增大（得分降低）→ 短/长比增大
    b025_ratio = ratios[1]  # b=0.25
    b05_ratio = ratios[2]   # b=0.5
    b075_ratio = ratios[3]  # b=0.75

    monotonic = b025_ratio < b05_ratio < b075_ratio
    print(f"\n   单调性（比值随 b 增大而增大）: {'✓' if monotonic else '✗'}")
    print(f"   b=0.5 处于折中位置: b=0.25→{b025_ratio:.2f}x < b=0.5→{b05_ratio:.2f}x < b=0.75→{b075_ratio:.2f}x")

    return monotonic and b05_ratio < 2.0, b05_ratio


def main():
    print("=" * 80)
    print("BM25 b=0.5 极端短文档场景验证")
    print("=" * 80)
    print()
    print("【背景】b=0.5 是缓解短文档虚高与保留长文档区分度的折中")
    print("【目标】验证极端场景下虚高是否在可接受范围（非彻底消除）")
    print("【诚实声明】BM25 数学上无法彻底消除短文档虚高（b=0 时无虚高但无区分度，")
    print("           b=1 时虚高最严重）。b=0.5 是折中，本脚本验证'缓解程度'。")

    results = []
    results.append(("场景1-1token", *run_scenario_1()))
    results.append(("场景2-term_freq", *run_scenario_2()))
    results.append(("场景3-多短文档", *run_scenario_3()))
    results.append(("场景4-空文档", *run_scenario_4()))
    results.append(("场景5-b扫描", *run_scenario_5()))

    print("\n" + "=" * 80)
    print("汇总")
    print("=" * 80)
    print(f"\n{'场景':<20} {'通过?':<8} {'关键值':<12}")
    print("-" * 40)
    all_passed = True
    for name, passed, value in results:
        status = "✓ 通过" if passed else "✗ 失败"
        if not passed:
            all_passed = False
        print(f"{name:<20} {status:<8} {value:<12.4f}")

    print("-" * 40)
    passed_count = sum(1 for _, p, _ in results if p)
    print(f"\n通过用例: {passed_count}/{len(results)}")

    if all_passed:
        print("\n✅ 验证通过：b=0.5 在极端场景下虚高均在可接受范围")
        print("   诚实结论：短文档仍适度偏高（BM25 设计如此），但未极端虚高")
        sys.exit(0)
    else:
        failed = [n for n, p, _ in results if not p]
        print(f"\n::error::BM25 极端场景验证失败：{', '.join(failed)}")
        print("❌ 验证失败：部分极端场景虚高超出阈值，建议重新调参")
        sys.exit(1)


if __name__ == "__main__":
    main()
