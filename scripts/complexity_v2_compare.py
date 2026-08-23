"""复杂度判定源对比（wire vs wire_v2，可含 enhanced_planner）— 复查补充

任务7 对比报告已给出 wire vs enhanced_planner 的一致率（29.50%）与对人工
标注符合率（20%/32%）。本脚本补充 wire_v2（复查新增的增强特征判定）的量化
对比，供"复杂度判定质量提升"决策：

1. 分布对比：三源（wire / wire_v2 / enhanced_planner 可选）在抽样集上的
   TRIVIAL/SIMPLE/NORMAL/COMPLEX 分布；
2. 一致率：wire vs wire_v2（与任务7 报告口径一致），并列出分歧样例；
3. 符合率（--labeled）：对 data/complexity_labeled.jsonl 中已人工标注样本
   计算各源与 ground truth 的符合率（判定源最终选型的核心依据）。

用法:
    python scripts/complexity_v2_compare.py                      # wire vs v2 分布/一致率
    python scripts/complexity_v2_compare.py --include-enhanced   # 含 enhanced_planner
    python scripts/complexity_v2_compare.py --labeled            # 已标注后算符合率
    python scripts/complexity_v2_compare.py --out report.json

【不易】只读分析：不修改任何判定实现与配置；enhanced_planner 为延迟加载。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "complexity_samples.json"
LABELED = Path(__file__).resolve().parent.parent / "data" / "complexity_labeled.jsonl"
LEVELS = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")


def _load_samples() -> List[Dict[str, Any]]:
    raw = json.loads(SAMPLES.read_text(encoding="utf-8"))
    samples = raw.get("samples") if isinstance(raw, dict) else raw
    return [s for s in samples if str(s.get("message", "")).strip()]


def _load_labeled() -> Dict[str, tuple]:
    """已人工标注样本 {id: (expected_level, message)}——直接从标注资产读取，
    不依赖抽样集索引（兼容 eval_set 补充样本）。"""
    out: Dict[str, tuple] = {}
    if not LABELED.exists():
        return out
    for line in LABELED.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("label_status") == "labeled" and rec.get("expected_level"):
            out[str(rec["id"])] = (str(rec["expected_level"]),
                                   str(rec.get("message", "")))
    return out


def _build_sources(include_enhanced: bool) -> Dict[str, Any]:
    from agent.task_planner.complexity_classifier import (
        WireHeuristicClassifier, WireV2Classifier,
    )
    impls: Dict[str, Any] = {"wire": WireHeuristicClassifier(), "wire_v2": WireV2Classifier()}
    if include_enhanced:
        from agent.task_planner.complexity_classifier import EnhancedPlannerClassifier
        impls["enhanced_planner"] = EnhancedPlannerClassifier()
    return impls


def _dist_table(impls: Dict[str, Any], samples: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    from agent.task_planner.complexity_classifier import normalize_level
    dist: Dict[str, Dict[str, int]] = {name: {l: 0 for l in LEVELS}
                                       for name in impls}
    for s in samples:
        msg = str(s.get("message", ""))
        for name, impl in impls.items():
            lvl = normalize_level(impl.classify(msg))
            dist[name][lvl] = dist[name].get(lvl, 0) + 1
    return dist


def _consistency(impl_a: Any, impl_b: Any, samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    agree = 0
    conflicts: Dict[str, int] = {}
    examples: List[Dict[str, Any]] = []
    for s in samples:
        msg = str(s.get("message", ""))
        la = impl_a.classify(msg)
        lb = impl_b.classify(msg)
        if la == lb:
            agree += 1
        else:
            key = f"{la}->{lb}"
            conflicts[key] = conflicts.get(key, 0) + 1
            if len(examples) < 10:
                examples.append({"message": msg[:80], "a": la, "b": lb})
    total = len(samples)
    return {
        "agree": agree,
        "total": total,
        "consistency_rate": round(agree / total, 4) if total else 0.0,
        "conflicts": dict(sorted(conflicts.items(), key=lambda kv: -kv[1])),
        "examples": examples,
    }


def _accuracy(impl: Any, labeled: Dict[str, tuple]) -> Dict[str, Any]:
    correct = 0
    total = 0
    confusion: Dict[str, Dict[str, int]] = {}
    for sid, (truth, message) in labeled.items():
        if not message:
            continue
        pred = impl.classify(message)
        total += 1
        if pred == truth:
            correct += 1
        cell = confusion.setdefault(truth, {})
        cell[pred] = cell.get(pred, 0) + 1
    return {
        "correct": correct, "total": total,
        "accuracy": round(correct / total, 4) if total else None,
        "confusion": confusion,
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001 非 UTF-8 终端降级（报告落盘不受影响）
        pass
    parser = argparse.ArgumentParser(description="复杂度判定源对比（wire vs wire_v2）")
    parser.add_argument("--include-enhanced", action="store_true",
                        help="同时对比 enhanced_planner 分级")
    parser.add_argument("--labeled", action="store_true",
                        help="使用人工标注资产计算符合率")
    parser.add_argument("--out", default=None, help="报告输出 JSON 路径")
    args = parser.parse_args()

    samples = _load_samples()
    if not samples:
        print(f"错误：抽样集为空或不存在 {SAMPLES}")
        return 2
    impls = _build_sources(args.include_enhanced)

    report: Dict[str, Any] = {
        "samples_total": len(samples),
        "distribution": _dist_table(impls, samples),
    }
    names = list(impls)
    report["consistency"] = {
        f"{names[i]}_vs_{names[j]}": _consistency(impls[names[i]], impls[names[j]], samples)
        for i in range(len(names)) for j in range(i + 1, len(names))
    }
    if args.labeled:
        labeled = _load_labeled()
        if not labeled:
            print("警告：无已标注样本（先运行 scripts/apply_complexity_labels.py）")
        report["accuracy"] = {name: _accuracy(impl, labeled)
                              for name, impl in impls.items()}

    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
