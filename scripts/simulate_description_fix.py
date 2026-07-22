"""模拟补全 self_reflection / memory_summary 的 description 后的 Precision@3

实验目的：
    验证假设 —— "7 个 0 分用例的根因是 description 字段为空"
    方法：用 MonkeyPatch 在内存层修改 SkillFileStore.load_metadata_index 返回值，
         给两个空 description 技能补充符合 front matter 的描述，再跑评估。

【不易】不修改任何磁盘文件（不动 data/skills_repo/）
【简易】单文件 + 标准库，monkey-patch 内存层即可
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from agent.skills_mgmt.loader import SkillLoader  # noqa: E402

# 复用评估脚本的指标计算
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))
import eval_skill_retrieval as ev  # noqa: E402


# ════════════════════════════════════════════════════════════
#  模拟补全的 description（人工撰写，贴合 skill.md body 的语义）
# ════════════════════════════════════════════════════════════

# 从 data/skills_repo/self_reflection/skill.md body 提炼的描述
# body 原文："自我反思技能 — 让模型回顾自身推理与回答过程，识别可能的疏漏并改进"
SIMULATED_DESCRIPTIONS = {
    "self_reflection": (
        "自我反思技能 — 让模型回顾自身推理与回答过程，"
        "识别可能的疏漏并改进。在多步推理完成后复查关键步骤、"
        "长回答输出前自检事实准确性与逻辑一致性、用户质疑时复盘定位偏差"
    ),
    # 从 data/skills_repo/memory_summary/skill.md body 提炼
    # body 原文："记忆摘要技能 — 对长对话或历史记忆做结构化压缩，保留关键事实与决策"
    "memory_summary": (
        "记忆摘要技能 — 对长对话或历史记忆做结构化压缩，"
        "保留关键事实与决策。在上下文超阈值时压缩前文、"
        "跨会话恢复时加载历史摘要、长期记忆检索前按主题归并相似条目"
    ),
}


def _patch_loader_with_descriptions(loader: SkillLoader) -> None:
    """内存层 patch SkillLoader.fs，给空 description 技能补充模拟描述

    策略：用 wrapper 包裹 load_metadata_index，返回的 metadata 中
    替换 description 字段。
    """
    original_load = loader.fs.load_metadata_index

    def _patched_load(*args, **kwargs):
        index = original_load(*args, **kwargs)
        # 复制一份避免污染缓存
        patched: Dict[str, Dict[str, Any]] = {}
        for sid, meta in index.items():
            new_meta = dict(meta)
            if sid in SIMULATED_DESCRIPTIONS and not new_meta.get("description"):
                new_meta["description"] = SIMULATED_DESCRIPTIONS[sid]
            patched[sid] = new_meta
        return patched

    loader.fs.load_metadata_index = _patched_load  # type: ignore


def _run_eval(label: str, top_k: int = 3) -> Dict[str, Any]:
    """跑一次评估并打印对比"""
    print(f"\n{'═' * 80}")
    print(f"  {label} (K={top_k})")
    print(f"{'═' * 80}")

    # 用独立 loader 实例，避免缓存污染
    loader = SkillLoader()
    report = ev.evaluate(
        golden_set_path=ev.DEFAULT_GOLDEN_SET,
        top_k=top_k,
        enabled_only=True,
        loader=loader,
    )

    o = report["overall"]
    print(f"  Precision@{top_k} : {o['precision']:.4f}")
    print(f"  Recall@{top_k}    : {o['recall']:.4f}")
    print(f"  MRR              : {o['mrr']:.4f}")

    # 按 0 分用例数对比
    zero_cases = [c for c in report["cases"] if c["precision"] == 0.0]
    print(f"  0 分用例数       : {len(zero_cases)}/{report['total_cases']}")
    if zero_cases:
        print(f"  剩余 0 分用例:")
        for c in zero_cases:
            print(f"    {c['case_id']}: expected={c['expected']} actual={c['actual']}")

    return report


def main() -> int:
    print("=" * 80)
    print("模拟实验：补全 description 后 Precision@3 提升预测")
    print("=" * 80)
    print()
    print("假设：7 个 0 分用例的根因是 self_reflection 与 memory_summary 的")
    print("      skill.md front matter description 字段为空。")
    print()
    print("实验方法：用 monkey-patch 在内存层补充 description（不修改磁盘文件），")
    print("         再跑评估脚本对比 Precision@3。")
    print()
    print("模拟补全的 description：")
    for sid, desc in SIMULATED_DESCRIPTIONS.items():
        print(f"  [{sid}]")
        print(f"    {desc}")
    print()

    # ── Baseline：不修改 ──
    baseline = _run_eval("Baseline (TF-IDF, description 空)", top_k=3)

    # ── Patched：补全 description ──
    print()
    print(">>> 应用 monkey-patch：内存层补全 description...")
    patched_loader = SkillLoader()
    _patch_loader_with_descriptions(patched_loader)
    patched = ev.evaluate(
        golden_set_path=ev.DEFAULT_GOLDEN_SET,
        top_k=3,
        enabled_only=True,
        loader=patched_loader,
    )

    print()
    print(f"{'═' * 80}")
    print(f"  Patched (description 补全) (K=3)")
    print(f"{'═' * 80}")
    o = patched["overall"]
    print(f"  Precision@3 : {o['precision']:.4f}")
    print(f"  Recall@3    : {o['recall']:.4f}")
    print(f"  MRR         : {o['mrr']:.4f}")
    zero_patched = [c for c in patched["cases"] if c["precision"] == 0.0]
    print(f"  0 分用例数   : {len(zero_patched)}/{patched['total_cases']}")
    if zero_patched:
        print(f"  剩余 0 分用例:")
        for c in zero_patched:
            print(f"    {c['case_id']}: expected={c['expected']} actual={c['actual']}")

    # ── 对比 ──
    print()
    print("=" * 80)
    print("【对比结论】")
    print("=" * 80)
    delta_p = patched["overall"]["precision"] - baseline["overall"]["precision"]
    delta_r = patched["overall"]["recall"] - baseline["overall"]["recall"]
    delta_m = patched["overall"]["mrr"] - baseline["overall"]["mrr"]
    print(f"  Precision@3 : {baseline['overall']['precision']:.4f} → {patched['overall']['precision']:.4f}  (Δ={delta_p:+.4f}, {(delta_p/baseline['overall']['precision']*100):+.1f}%)")
    print(f"  Recall@3    : {baseline['overall']['recall']:.4f} → {patched['overall']['recall']:.4f}  (Δ={delta_r:+.4f})")
    print(f"  MRR         : {baseline['overall']['mrr']:.4f} → {patched['overall']['mrr']:.4f}  (Δ={delta_m:+.4f})")
    print(f"  0 分用例    : {len([c for c in baseline['cases'] if c['precision']==0.0])} → {len(zero_patched)}")

    # ── 逐用例对比 ──
    print()
    print("【逐用例对比（仅显示有变化的用例）】")
    print(f"  {'case_id':<10} {'难度':<8} {'Baseline P':>12} {'Patched P':>12} {'变化':>10}  query")
    changed = 0
    for b, p in zip(baseline["cases"], patched["cases"]):
        if b["precision"] != p["precision"] or b["actual"] != p["actual"]:
            changed += 1
            delta = p["precision"] - b["precision"]
            query_preview = b["query"][:35] + ("..." if len(b["query"]) > 35 else "")
            print(f"  {b['case_id']:<10} {b['difficulty']:<8} {b['precision']:>12.2f} {p['precision']:>12.2f} {delta:>+10.2f}  {query_preview}")
            print(f"    baseline actual : {b['actual']}")
            print(f"    patched  actual : {p['actual']}")
    if changed == 0:
        print("  (无变化)")
    else:
        print(f"\n  共 {changed} 个用例发生变化")

    # ── 保存对比报告 ──
    output_path = _PROJECT_ROOT / "tests" / "eval" / "simulate_description_fix_report.json"
    comparison = {
        "experiment": "simulate_description_fix",
        "method": "monkey-patch SkillFileStore.load_metadata_index in memory",
        "simulated_descriptions": SIMULATED_DESCRIPTIONS,
        "baseline": {
            "precision": baseline["overall"]["precision"],
            "recall": baseline["overall"]["recall"],
            "mrr": baseline["overall"]["mrr"],
            "zero_cases": len([c for c in baseline["cases"] if c["precision"] == 0.0]),
        },
        "patched": {
            "precision": patched["overall"]["precision"],
            "recall": patched["overall"]["recall"],
            "mrr": patched["overall"]["mrr"],
            "zero_cases": len(zero_patched),
        },
        "delta": {
            "precision": delta_p,
            "recall": delta_r,
            "mrr": delta_m,
        },
        "changed_cases": changed,
    }
    output_path.write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"\n对比报告已保存: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
