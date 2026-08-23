# -*- coding: utf-8 -*-
"""任务7 复杂度判定源对比脚本 — wire 启发式 vs enhanced_planner 分级

抽样集（200 条）:
    1) 任务1 技能评估集 data/evals/（50 条，含人工标注难度 difficulty 作参考）；
    2) 生产对话日志风格抽样 data/complexity_samples.json（150 条，curated_prod）。

输出:
    - stdout: 一致性报告摘要（一致率 / 分档分布 / 交叉表 / 与人工标注符合率 / 误差样例）
    - data/complexity_compare_result.json: 全量明细（供报告复现与单测消费）

用法:
    python scripts/complexity_source_compare.py
"""

import io
import json
import sys
from collections import Counter
from pathlib import Path

# 直接执行时把仓库根加入 sys.path（与 scripts/ 下既有脚本同款引导）
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Windows 控制台 UTF-8 输出
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agent.task_planner.complexity_classifier import (
    CANONICAL_LEVELS,
    EnhancedPlannerClassifier,
    WireHeuristicClassifier,
)

_EVALS_DIR = _REPO_ROOT / "data" / "evals"
_CURATED_PATH = _REPO_ROOT / "data" / "complexity_samples.json"
_OUTPUT_PATH = _REPO_ROOT / "data" / "complexity_compare_result.json"


def load_eval_samples() -> list:
    """加载任务1 评估集样本（含人工难度标注；跳过 manifest/baselines/_pending）"""
    samples = []
    for f in sorted(_EVALS_DIR.glob("*/")):
        for jf in sorted(f.glob("*.json")):
            if jf.name in ("manifest.json", "baselines.json"):
                continue
            try:
                data = json.loads(jf.read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                print(f"[warn] 跳过评估集文件 {jf}: {e}")
                continue
            items = data if isinstance(data, list) else list(data.values())
            for it in items:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                difficulty = ((it.get("metadata") or {}).get("difficulty") or "").upper()
                samples.append({
                    "id": it["id"],
                    "category": it.get("category", ""),
                    "message": it.get("task", ""),
                    "reference": difficulty if difficulty in CANONICAL_LEVELS else "",
                })
    return samples


def load_curated_samples() -> list:
    """加载生产对话风格抽样（150 条，无参考标注）"""
    data = json.loads(_CURATED_PATH.read_text(encoding="utf-8"))
    return [
        {"id": it["id"], "category": "curated", "message": it["message"], "reference": ""}
        for it in data if isinstance(it, dict) and it.get("id")
    ]


def main() -> None:
    wire = WireHeuristicClassifier()
    enhanced = EnhancedPlannerClassifier()

    eval_samples = load_eval_samples()
    curated = load_curated_samples()
    all_samples = eval_samples + curated
    print(f"抽样集构成: 评估集 {len(eval_samples)} 条 + 生产风格 {len(curated)} 条 = {len(all_samples)} 条\n")

    rows = []
    agree = 0
    for s in all_samples:
        msg = s["message"]
        w = wire.classify(msg)
        e = enhanced.classify(msg)
        rows.append({**s, "wire": w, "enhanced": e, "agree": w == e})
        if w == e:
            agree += 1

    total = len(rows)
    rate = agree / total
    print(f"一致率: {agree}/{total} = {rate:.2%}\n")

    # 分档分布
    w_dist = Counter(r["wire"] for r in rows)
    e_dist = Counter(r["enhanced"] for r in rows)
    print("分档分布（wire → enhanced）:")
    for lvl in CANONICAL_LEVELS:
        print(f"  {lvl:8s}: wire={w_dist.get(lvl, 0):3d}  enhanced={e_dist.get(lvl, 0):3d}")
    print()

    # 交叉表
    print("交叉表（行=wire, 列=enhanced）:")
    header = "      " + "".join(f"{lvl[:5]:>7s}" for lvl in CANONICAL_LEVELS)
    print(header)
    for wl in CANONICAL_LEVELS:
        line = f"{wl[:5]:>5s} "
        for el in CANONICAL_LEVELS:
            n = sum(1 for r in rows if r["wire"] == wl and r["enhanced"] == el)
            line += f"{n:7d}"
        print(line)
    print()

    # 与人工标注符合率（仅评估集）
    ref_rows = [r for r in rows if r["reference"]]
    if ref_rows:
        w_ref = sum(1 for r in ref_rows if r["wire"] == r["reference"])
        e_ref = sum(1 for r in ref_rows if r["enhanced"] == r["reference"])
        print(f"与人工难度标注符合率（评估集 {len(ref_rows)} 条）:")
        print(f"  wire      = {w_ref}/{len(ref_rows)} = {w_ref / len(ref_rows):.2%}")
        print(f"  enhanced  = {e_ref}/{len(ref_rows)} = {e_ref / len(ref_rows):.2%}")
        print()

    # 误差样例（分歧样本，按参考/类别分组展示）
    div = [r for r in rows if not r["agree"]]
    print(f"分歧样本数: {len(div)}/{total}\n")
    print("分歧样例（前 15 条）:")
    for r in div[:15]:
        ref = f" 参考={r['reference']}" if r["reference"] else ""
        print(f"  [{r['id']}] wire={r['wire']:<7s} enhanced={r['enhanced']:<7s}{ref}  msg={r['message'][:36]}")
    print()

    # 落盘明细（供报告复现与单测消费）
    result = {
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "sample_composition": {
            "eval": len(eval_samples),
            "curated": len(curated),
            "total": total,
        },
        "consistency_rate": rate,
        "agree_count": agree,
        "wire_distribution": dict(w_dist),
        "enhanced_distribution": dict(e_dist),
        "cross_table": {
            wl: {el: sum(1 for r in rows if r["wire"] == wl and r["enhanced"] == el)
                 for el in CANONICAL_LEVELS}
            for wl in CANONICAL_LEVELS
        },
        "reference_agreement": {
            "n": len(ref_rows),
            "wire": round(sum(1 for r in ref_rows if r["wire"] == r["reference"]) / len(ref_rows), 4) if ref_rows else None,
            "enhanced": round(sum(1 for r in ref_rows if r["enhanced"] == r["reference"]) / len(ref_rows), 4) if ref_rows else None,
        } if ref_rows else {"n": 0, "wire": None, "enhanced": None},
        "divergent_count": len(div),
        "divergent_samples": [
            {"id": r["id"], "wire": r["wire"], "enhanced": r["enhanced"],
             "reference": r["reference"], "message": r["message"]}
            for r in div
        ],
    }
    _OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写入: {_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
