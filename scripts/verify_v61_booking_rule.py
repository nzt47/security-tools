"""v6.1 negative_booking 规则自动化验证脚本

用途:
    在测试环境中模拟 3 个 negative_booking 负样本 + 5 个 voice_interaction 正样本，
    验证候选 booking 规则的命中情况与误伤情况。

设计原则:
    【不易】不依赖 v6.1 已实现代码（booking 规则尚未加入 loader.py）
            独立定义候选正则，直接测试
    【变易】支持多候选方案对比（方案 A/B/C）
    【简易】单文件可运行，无第三方依赖（仅 re + json）

用法:
    # 完整验证（默认方案 A）
    python scripts/verify_v61_booking_rule.py

    # 指定方案
    python scripts/verify_v61_booking_rule.py --scheme A

    # 详细输出
    python scripts/verify_v61_booking_rule.py --verbose

    # 仅检查正样本冲突
    python scripts/verify_v61_booking_rule.py --positives-only

输出:
    ✅ 全部通过: 3 负样本命中 + 5 正样本不误伤
    ❌ 失败: 列出失败项 + 误伤的 query

退出码:
    0: 全部通过
    1: 有负样本未命中
    2: 有正样本被误伤（违【不易】）
    3: 配置错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

# 项目根目录
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ════════════════════════════════════════════════════════════
#  候选 booking 规则（3 个方案）
# ════════════════════════════════════════════════════════════

@dataclass
class BookingScheme:
    """booking 规则候选方案"""
    name: str
    pattern: re.Pattern
    description: str
    risk_note: str


# 方案 A: 精确宾语白名单（推荐）
# 只匹配明确的"下单/预订"宾语，避免动词过宽
SCHEME_A = BookingScheme(
    name="A",
    pattern=re.compile(
        r"(帮我|请|我想).{0,2}(点|订|买|叫|购).{0,3}(外卖|机票|酒店|火车票|电影票|商品|礼物)"
    ),
    description="精确宾语白名单：动词 + 明确购物对象",
    risk_note="低风险：白名单宾语都是明确的购物对象，'帮我点歌'不匹配",
)

# 方案 B: 动词+任意宾语（不推荐）
# 覆盖面广但易误伤
SCHEME_B = BookingScheme(
    name="B",
    pattern=re.compile(r"(帮我|请).{0,2}(点|订|买).{0,3}(.+?)"),
    description="动词 + 任意宾语（过宽）",
    risk_note="高风险：'帮我点歌'/'帮我买本书'会被误伤",
)

# 方案 C: 动词 + 数字量词模式
# "订一张"/"买两张" 等带量词的预订模式
SCHEME_C = BookingScheme(
    name="C",
    pattern=re.compile(
        r"(帮我|请|我想).{0,2}(订|买|购).{0,2}(一张|两张|三张|几张|个|件)"
    ),
    description="动词 + 数字量词模式",
    risk_note="低风险，但 '帮我点外卖' 无量词不匹配（case_105 无法解决）",
)

_SCHEMES = {"A": SCHEME_A, "B": SCHEME_B, "C": SCHEME_C}


# ════════════════════════════════════════════════════════════
#  测试数据
# ════════════════════════════════════════════════════════════

# 3 个 negative_booking 负样本（应命中）
NEGATIVE_BOOKING_SAMPLES = [
    ("case_103", "帮我订一张机票", "negative_booking"),
    ("case_104", "我想订酒店", "negative_booking"),
    ("case_105", "帮我点外卖", "negative_booking"),
]

# 5 个 voice_interaction 正样本（不应误伤）
VOICE_INTERACTION_POSITIVES = [
    ("case_025", "语音", "voice"),
    ("case_026", "我想用语音跟你说话", "voice"),
    ("case_027", "请识别语音指令", "voice"),
    ("case_028", "请进行 TTS 合成", "voice"),
    ("case_035", "请识别语音指令并保障安全", "multi_skill"),
]

# 高风险潜在正样本（若出现在正样本集会被误伤，需重点验证）
HIGH_RISK_POTENTIAL_POSITIVES = [
    ("risk_001", "帮我点歌", "voice_interaction 合理场景"),
    ("risk_002", "帮我播放音乐", "播放控制"),
    ("risk_003", "帮我买东西", "通用购物（含'东西'）"),
    ("risk_004", "请帮我订会议室", "办公预订（可能误伤）"),
    ("risk_005", "帮我买本书", "购物（非技能但常见）"),
]


# ════════════════════════════════════════════════════════════
#  验证逻辑
# ════════════════════════════════════════════════════════════

def _load_golden_queries() -> List[Tuple[str, str, bool]]:
    """加载 45 个正样本黄金集 query（用于完整冲突检查）

    黄金集含正样本（expected_skill_ids 非空）和负样本（expected_skill_ids 为空）。
    本函数只返回正样本用于冲突检查（负样本不在冲突检查范围）。

    Returns:
        [(case_id, query, is_positive), ...]
        is_positive=True 表示 expected 非空（真技能意图，不应被规则命中）
    """
    golden_path = _PROJECT_ROOT / "tests" / "eval" / "skill_retrieval_golden_set.json"
    if not golden_path.exists():
        print(f"⚠️  黄金集不存在: {golden_path}", file=sys.stderr)
        return []
    with open(golden_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    result = []
    for c in data["test_cases"]:
        expected = c.get("expected_skill_ids") or []
        is_positive = len(expected) > 0  # 只有 expected 非空才是正样本
        result.append((c["case_id"], c["query"], is_positive))
    return result


def verify_scheme(
    scheme: BookingScheme,
    *,
    check_full_golden: bool = True,
    verbose: bool = False,
) -> Tuple[bool, List[str]]:
    """验证单个方案的命中与误伤情况

    Args:
        scheme: 候选方案
        check_full_golden: 是否检查完整 45 正样本黄金集
        verbose: 详细输出

    Returns:
        (all_passed, failure_messages)
    """
    failures: List[str] = []
    print(f"\n{'='*70}")
    print(f"  验证方案 {scheme.name}: {scheme.description}")
    print(f"  风险说明: {scheme.risk_note}")
    print(f"  正则: {scheme.pattern.pattern}")
    print(f"{'='*70}")

    # ── Step 1: 3 个 negative_booking 负样本应全部命中 ──
    print(f"\n[1/4] 负样本命中验证（3 个 negative_booking）:")
    neg_passed = 0
    for case_id, query, category in NEGATIVE_BOOKING_SAMPLES:
        hit = bool(scheme.pattern.search(query))
        marker = "✅" if hit else "❌"
        print(f"  {marker} {case_id} [{category}] {query}")
        if hit:
            neg_passed += 1
        else:
            failures.append(f"负样本未命中: {case_id} {query}")
    print(f"  结果: {neg_passed}/{len(NEGATIVE_BOOKING_SAMPLES)} 命中")

    # ── Step 2: 5 个 voice_interaction 正样本不应误伤 ──
    print(f"\n[2/4] 正样本不误伤验证（5 个 voice_interaction）:")
    voice_passed = 0
    for case_id, query, category in VOICE_INTERACTION_POSITIVES:
        hit = bool(scheme.pattern.search(query))
        marker = "✅" if not hit else "❌ 误伤"
        print(f"  {marker} {case_id} [{category}] {query}")
        if not hit:
            voice_passed += 1
        else:
            failures.append(f"正样本误伤: {case_id} {query}（违【不易】）")
    print(f"  结果: {voice_passed}/{len(VOICE_INTERACTION_POSITIVES)} 不误伤")

    # ── Step 3: 高风险潜在正样本检查 ──
    print(f"\n[3/4] 高风险潜在正样本检查（5 个 risk_*）:")
    risk_passed = 0
    for case_id, query, note in HIGH_RISK_POTENTIAL_POSITIVES:
        hit = bool(scheme.pattern.search(query))
        marker = "✅" if not hit else "⚠️  命中"
        print(f"  {marker} {case_id} [{note}] {query}")
        if not hit:
            risk_passed += 1
        else:
            # 高风险样本命中是 warning（非 failure），需人工判断
            print(f"      ⚠️  需人工判断: {query} 是否为真技能意图")
    print(f"  结果: {risk_passed}/{len(HIGH_RISK_POTENTIAL_POSITIVES)} 不命中")

    # ── Step 4: 完整 45 正样本黄金集冲突检查 ──
    if check_full_golden:
        print(f"\n[4/4] 完整黄金集正样本冲突检查:")
        golden_queries = _load_golden_queries()
        if not golden_queries:
            print("  ⚠️  跳过（黄金集不存在）")
        else:
            # 仅检查正样本（expected_skill_ids 非空），负样本不在冲突范围
            positives = [(cid, q) for cid, q, is_pos in golden_queries if is_pos]
            negatives = [(cid, q) for cid, q, is_pos in golden_queries if not is_pos]
            print(f"  黄金集: {len(positives)} 正样本 + {len(negatives)} 负样本")
            print(f"  仅检查 {len(positives)} 个正样本是否被 booking 规则误伤")

            conflict_count = 0
            for case_id, query in positives:
                hit = bool(scheme.pattern.search(query))
                if hit:
                    conflict_count += 1
                    print(f"  ❌ 冲突: {case_id} {query}")
                    failures.append(f"黄金集正样本冲突: {case_id} {query}（违【不易】）")
                elif verbose:
                    print(f"  ✅ {case_id} {query[:40]}")
            if conflict_count == 0:
                print(f"  ✅ 无冲突: {len(positives)} 个正样本全部不命中")
            else:
                print(f"  ❌ 发现 {conflict_count} 个正样本冲突")
    else:
        print(f"\n[4/4] 完整黄金集检查已跳过（--positives-only 模式）")

    # ── 汇总 ──
    all_passed = len(failures) == 0
    print(f"\n{'='*70}")
    if all_passed:
        print(f"  ✅ 方案 {scheme.name} 全部通过")
    else:
        print(f"  ❌ 方案 {scheme.name} 失败 ({len(failures)} 项):")
        for msg in failures:
            print(f"     - {msg}")
    print(f"{'='*70}")

    return all_passed, failures


# ════════════════════════════════════════════════════════════
#  端到端集成验证（可选：调用 loader._match_query_pattern）
# ════════════════════════════════════════════════════════════

def verify_via_loader_integration(scheme: BookingScheme) -> bool:
    """通过实际 loader._match_query_pattern 验证（需 v6.1 booking 规则已实施）

    本函数用于 v6.1 代码实施后的集成验证。
    若 booking 规则尚未加入 _QUERY_PATTERNS，此函数会跳过。

    Args:
        scheme: 候选方案（仅用于打印参考）

    Returns:
        True: 集成验证通过；False: 失败或跳过
    """
    print(f"\n{'='*70}")
    print(f"  集成验证（通过 loader._match_query_pattern）")
    print(f"{'='*70}")

    try:
        # 延迟导入，避免无 v6 实现时崩溃
        from agent.skills_mgmt.loader import SkillLoader, _QUERY_PATTERNS
    except ImportError as e:
        print(f"  ⚠️  跳过集成验证: 无法导入 SkillLoader ({e})")
        return False

    # 检查 booking 规则是否已加入 _QUERY_PATTERNS
    has_booking = any(
        cat == "booking" for _, cat, _ in _QUERY_PATTERNS
    )
    if not has_booking:
        print("  ⚠️  跳过集成验证: booking 规则尚未加入 _QUERY_PATTERNS")
        print("      请先在 loader.py _QUERY_PATTERNS 中添加 booking 规则，")
        print("      再重新运行此脚本进行集成验证。")
        return False

    loader = SkillLoader()
    import os
    os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)

    print("\n  [集成] 3 个 negative_booking 负样本（应被 _match_query_pattern 拒绝）:")
    neg_passed = 0
    for case_id, query, category in NEGATIVE_BOOKING_SAMPLES:
        result = loader._match_query_pattern(query, tid="test", t0=0.0)
        rejected = result is not None and len(result.matches) == 0
        marker = "✅" if rejected else "❌"
        print(f"    {marker} {case_id} {query}")
        if rejected:
            neg_passed += 1
    print(f"  结果: {neg_passed}/{len(NEGATIVE_BOOKING_SAMPLES)} 被拒绝")

    print("\n  [集成] 5 个 voice_interaction 正样本（应返回 None）:")
    voice_passed = 0
    for case_id, query, category in VOICE_INTERACTION_POSITIVES:
        result = loader._match_query_pattern(query, tid="test", t0=0.0)
        not_rejected = result is None
        marker = "✅" if not_rejected else "❌ 误伤"
        print(f"    {marker} {case_id} {query}")
        if not_rejected:
            voice_passed += 1
    print(f"  结果: {voice_passed}/{len(VOICE_INTERACTION_POSITIVES)} 不误伤")

    return neg_passed == len(NEGATIVE_BOOKING_SAMPLES) and \
           voice_passed == len(VOICE_INTERACTION_POSITIVES)


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="v6.1 negative_booking 规则自动化验证"
    )
    parser.add_argument(
        "--scheme", choices=["A", "B", "C", "all"], default="A",
        help="候选方案: A=精确白名单(推荐), B=动词+任意宾语, C=量词模式, all=全部对比"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="详细输出（打印每个正样本的检查结果）"
    )
    parser.add_argument(
        "--positives-only", action="store_true",
        help="仅检查正样本冲突（跳过集成验证）"
    )
    parser.add_argument(
        "--integration", action="store_true",
        help="运行集成验证（需 v6.1 booking 规则已实施）"
    )
    args = parser.parse_args()

    print("=" * 70)
    print("  v6.1 negative_booking 规则自动化验证")
    print(f"  方案: {args.scheme}")
    print(f"  时间: {__import__('datetime').datetime.now().isoformat()}")
    print("=" * 70)

    # 选定要验证的方案列表
    if args.scheme == "all":
        schemes = list(_SCHEMES.values())
    else:
        schemes = [_SCHEMES[args.scheme]]

    # 验证每个方案
    results = {}
    for scheme in schemes:
        passed, failures = verify_scheme(
            scheme,
            check_full_golden=not args.positives_only,
            verbose=args.verbose,
        )
        results[scheme.name] = (passed, failures)

    # 可选: 集成验证
    if args.integration:
        for scheme in schemes:
            verify_via_loader_integration(scheme)

    # 汇总
    print(f"\n{'='*70}")
    print("  汇总")
    print(f"{'='*70}")
    all_passed = True
    for name, (passed, failures) in results.items():
        marker = "✅" if passed else "❌"
        print(f"  {marker} 方案 {name}: {'通过' if passed else f'失败 ({len(failures)} 项)'}")
        if not passed:
            all_passed = False

    # 退出码
    if not all_passed:
        # 区分失败类型
        for name, (passed, failures) in results.items():
            for f in failures:
                if "误伤" in f or "冲突" in f:
                    print(f"\n❌ 退出码 2: 正样本被误伤（违【不易】），方案不可用")
                    sys.exit(2)
        print(f"\n❌ 退出码 1: 负样本未命中")
        sys.exit(1)

    print(f"\n✅ 退出码 0: 全部通过")
    sys.exit(0)


if __name__ == "__main__":
    main()
