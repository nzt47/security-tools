"""评估日志生成器 — 汇总 K=1/3/5 报告并生成详细分析日志

【不易】只读已有 JSON 报告，不重新跑评估
【简易】单文件脚本，标准库依赖
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "tests" / "eval"

# 加载三份报告
reports = {}
for k in [1, 3, 5]:
    p = EVAL_DIR / f"report_k{k}.json"
    reports[k] = json.loads(p.read_text(encoding="utf-8"))

lines = []
lines.append("=" * 100)
lines.append("技能检索评估详细日志 — TF-IDF 基线")
lines.append("=" * 100)
lines.append("")
lines.append("【一、整体指标对比（不同 K 值）】")
lines.append("-" * 60)
header = f"  {'K':<4} {'Precision':>12} {'Recall':>12} {'MRR':>10} {'用例数':>8}"
lines.append(header)
for k in [1, 3, 5]:
    o = reports[k]["overall"]
    line = f"  {k:<4} {o['precision']:>12.4f} {o['recall']:>12.4f} {o['mrr']:>10.4f} {reports[k]['total_cases']:>8}"
    lines.append(line)
lines.append("")
lines.append("观察：")
lines.append(f"  - Precision@1 = 0.7556：Top-1 命中率较高（34/45 用例 Top-1 正确）")
lines.append(f"  - Precision@3 = 0.3926 < Precision@1：K 增大后 Top-2/3 多为干扰项，拉低 Precision")
lines.append(f"  - Precision@5 = 0.2800：K=5 时负样本误召回与干扰项进一步增加")
lines.append(f"  - Recall@1=0.7000 → Recall@3=0.8444：K=3 比 K=1 多召回 6 个期望技能")
lines.append(f"  - Recall@3 = Recall@5 = 0.8444：K>3 后召回饱和，剩余 7 个 0 分用例是 description 空技能导致，无法靠增大 K 解决")
lines.append("")

# 按用例分数升序排序
lines.append("【二、用例得分升序（K=3 视角，贡献最低分的用例）】")
lines.append("-" * 100)
cases = reports[3]["cases"]
sorted_cases = sorted(cases, key=lambda c: (c["precision"], c["mrr"], c["recall"]))
header = f"  {'case_id':<10} {'难度':<8} {'类别':<16} {'P@3':>6} {'R@3':>6} {'MRR':>6}  {'expected':<35} {'actual':<40}"
lines.append(header)
for c in sorted_cases:
    exp = ",".join(c["expected"]) if c["expected"] else "(空)"
    act = ",".join(c["actual"]) if c["actual"] else "(空)"
    line = (f"  {c['case_id']:<10} {c['difficulty']:<8} {c['category']:<16} "
            f"{c['precision']:>6.2f} {c['recall']:>6.2f} {c['mrr']:>6.2f}  "
            f"{exp:<35} {act:<40}")
    lines.append(line)
lines.append("")

# 单独列出 0 分用例
zero_cases = [c for c in sorted_cases if c["precision"] == 0.0]
lines.append(f"【三、0 分用例深度分析（共 {len(zero_cases)} 个，是拉低 Precision@3 的核心原因）】")
lines.append("-" * 100)
fixed_precision = (sum(c["precision"] for c in cases) + len(zero_cases) * 1.0) / len(cases)
lines.append(f"  0 分用例对 Precision@3 的拖累：每例贡献 0/3=0，相比满分 1.0 共损失 {len(zero_cases)}（{len(zero_cases)}/{reports[3]['total_cases']} 例）")
lines.append(f"  若 {len(zero_cases)} 个 0 分用例全部修复，Precision@3 将从 0.3926 提升至 {fixed_precision:.4f}")
lines.append("")
for c in zero_cases:
    lines.append(f"  ■ {c['case_id']} [{c['difficulty']}/{c['category']}]")
    lines.append(f"    query    : {c['query']}")
    lines.append(f"    expected : {c['expected']}")
    lines.append(f"    actual   : {c['actual']}")
    lines.append(f"    根因分析 :")
    if c["category"] == "self_reflection":
        lines.append(f"      - self_reflection 的 skill.md front matter 中 description 为空，name 是英文 ID")
        lines.append(f"      - TF-IDF 第一层只读 front matter，不读 body（body 中含'反思/复查'等关键词）")
        lines.append(f"      - 中文查询词'反思/复查/检查'与英文 ID self_reflection 无字面交集")
    elif c["category"] == "memory_summary":
        lines.append(f"      - memory_summary 的 skill.md front matter 中 description 为空，name 是英文 ID")
        lines.append(f"      - 中文查询词'总结/梳理/记忆/压缩'与英文 ID 无字面交集")
        lines.append(f"      - actual 中 context_aware 排第一，因为其 description 含'记忆检索''对话'等词被误命中")
    elif c["category"] == "discrimination":
        lines.append(f"      - 语义近似区分用例：期望命中 self_reflection/memory_summary")
        lines.append(f"      - 这两个技能 description 空，TF-IDF 完全无法召回")
        if c["actual"]:
            lines.append(f"      - actual 召回的都是 description 含'上下文/对话/回应'的技能（误命中）")
        else:
            lines.append(f"      - actual 完全为空（query 词都不在任何技能 front matter 中）")
    lines.append("")

# 按类别聚合
lines.append("【四、按类别聚合 — 识别最差的类别】")
lines.append("-" * 100)
header = f"  {'类别':<16} {'Precision':>12} {'Recall':>12} {'MRR':>10} {'用例数':>8}  问题诊断"
lines.append(header)
cat_sorted = sorted(reports[3]["by_category"].items(), key=lambda x: x[1]["precision"])
for cat, g in cat_sorted:
    diag = ""
    if g["precision"] == 0.0:
        diag = "description 空技能，TF-IDF 完全失效"
    elif g["precision"] < 0.5:
        diag = "召回有干扰，Top-K 排序待优化"
    elif g["precision"] < 1.0:
        diag = "部分命中，排序需提升"
    else:
        diag = "满分通过"
    line = (f"  {cat:<16} {g['precision']:>12.4f} {g['recall']:>12.4f} "
            f"{g['mrr']:>10.4f} {g['count']:>8}  {diag}")
    lines.append(line)
lines.append("")

# 按难度聚合
lines.append("【五、按难度聚合 — 识别难度拐点】")
lines.append("-" * 80)
header = f"  {'难度':<10} {'Precision':>12} {'Recall':>12} {'MRR':>10} {'用例数':>8}"
lines.append(header)
diff_order = ["easy", "medium", "hard", "tricky"]
for diff in diff_order:
    if diff in reports[3]["by_difficulty"]:
        g = reports[3]["by_difficulty"][diff]
        line = f"  {diff:<10} {g['precision']:>12.4f} {g['recall']:>12.4f} {g['mrr']:>10.4f} {g['count']:>8}"
        lines.append(line)
lines.append("")
lines.append("  拐点分析：")
lines.append("    - easy/medium 0.3333：Top-3 只有 1 个期望命中（其余 2 个为干扰），1/3=0.3333")
lines.append("    - hard 0.2821：除多技能用例外，多为 description 空技能的语义查询，0 分拖累")
lines.append("    - tricky 1.0：负样本全部正确拒绝（无技能命中即正确）")
lines.append("")

# 根因与升级建议
lines.append("【六、根因诊断与升级建议】")
lines.append("-" * 100)
lines.append("  1. 核心根因：self_reflection 与 memory_summary 的 skill.md front matter description 字段为空")
lines.append("     → TF-IDF 第一层只读 front matter（name + description + tags + category）")
lines.append("     → 这两个技能 name 是英文 ID，description 为空，tags 为空，category=custom")
lines.append("     → 中文语义查询无任何字面命中点")
lines.append("")
lines.append("  2. 次要根因：TF-IDF 排序缺陷")
lines.append("     → case_026 '我想用语音跟你说话'：voice_interaction 与 proactive_suggestion 同分 0.3333")
lines.append("       因 proactive_suggestion 字典序靠前被排到前面（实际算法用 stable sort）")
lines.append("     → case_012 '请调整回应语气加点感情'：context_aware 排第一（0.4545）")
lines.append("       因 '回应' 同时出现在 context_aware 与 emotion_expression 的 description 中")
lines.append("")
lines.append("  3. 升级向量检索的预期收益")
lines.append(f"     → {len(zero_cases)} 个 0 分用例全部修复：Precision@3 从 0.3926 → {fixed_precision:.4f}（+{fixed_precision - 0.3926:.4f}）")
lines.append("     → 加上排序优化（case_012、case_026 等恢复 Top-1 命中）：Precision@3 → ~0.6+")
lines.append("     → MRR 从 0.8000 → ~0.95（7 个用例 MRR 从 0 提升到 1.0）")
lines.append("")
lines.append("  4. 短期缓解（不改算法）：补全 self_reflection / memory_summary 的 description 字段")
lines.append("     → 但【不易】约束禁止改技能定义，所以必须走升级向量检索路径")
lines.append("")
lines.append("=" * 100)
lines.append("报告生成时间：2026-07-18")
lines.append("基线版本：TF-IDF (loader.py 当前实现)")
lines.append("黄金集版本：1.0 (45 cases)")
lines.append("黄金集路径：tests/eval/skill_retrieval_golden_set.json")
lines.append("基线文件：tests/eval/baseline_tfidf.json")
lines.append("K=1/3/5 JSON 报告：tests/eval/report_k{1,3,5}.json")
lines.append("=" * 100)

output = "\n".join(lines)
out_path = EVAL_DIR / "eval_detail_log.txt"
out_path.write_text(output, encoding="utf-8")
print(output)
print()
print(f"详细日志已保存: {out_path}")
