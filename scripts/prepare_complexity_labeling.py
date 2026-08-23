"""复杂度人工标注样本资产准备（复查补充 · P0-1 数据基础）

任务7 对比报告结论：wire 与 enhanced_planner 判定一致率仅 29.50%，两源对
人工标注符合率均低（20%/32%）——判定质量是课程阶梯的数据瓶颈，需更大样本
人工标注后重选/校准判定实现。

本脚本把 `data/complexity_samples.json`（150 条真实/curated 语句，任务7 对比
抽样集）转换为人工标注资产 `data/complexity_labeled.jsonl`：
    {"id", "message", "source", "note", "expected_level": null,
     "label_status": "pending", "labeled_by": null}
- expected_level ∈ TRIVIAL/SIMPLE/NORMAL/COMPLEX（人工填写）；
- label_status: pending（待标注）/ labeled（已标注）；
- 幂等：已存在文件中的已标注记录保留，只追加未收录样本。

标注完成后可用脚本（scripts/complexity_v2_compare.py --labeled）计算 wire /
wire_v2 / enhanced_planner 三源对人工标注的符合率，作为判定源最终选型依据。

用法:
    python scripts/prepare_complexity_labeling.py              # 生成/更新资产
    python scripts/prepare_complexity_labeling.py --check      # 校验资产 schema
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SOURCE = Path(__file__).resolve().parent.parent / "data" / "complexity_samples.json"
TARGET = Path(__file__).resolve().parent.parent / "data" / "complexity_labeled.jsonl"

VALID_LEVELS = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")
VALID_STATUS = ("pending", "labeled")


def _load_existing() -> Dict[str, Dict[str, Any]]:
    """读取既有标注资产（按 id 索引）"""
    existing: Dict[str, Dict[str, Any]] = {}
    if TARGET.exists():
        for line in TARGET.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("id"):
                existing[str(rec["id"])] = rec
    return existing


def prepare() -> int:
    if not SOURCE.exists():
        print(f"错误：源样本集不存在 {SOURCE}（先运行任务7 对比脚本生成抽样集）")
        return 2
    raw = json.loads(SOURCE.read_text(encoding="utf-8"))
    samples = raw.get("samples") if isinstance(raw, dict) else raw
    if not isinstance(samples, list):
        print("错误：源样本集结构异常（期望数组或 {samples: [...]}）")
        return 2

    existing = _load_existing()
    added = 0
    lines: List[str] = []
    for s in samples:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        if sid in existing:
            lines.append(json.dumps(existing[sid], ensure_ascii=False))
            continue
        rec = {
            "id": sid,
            "message": str(s.get("message", "")),
            "source": str(s.get("source", "")),
            "note": str(s.get("note", "") or ""),
            "expected_level": None,
            "label_status": "pending",
            "labeled_by": None,
        }
        existing[sid] = rec
        lines.append(json.dumps(rec, ensure_ascii=False))
        added += 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"标注资产已更新：{TARGET}")
    print(f"  总样本 {len(existing)}（新增 {added}，保留既有 {len(existing) - added}）")
    return 0


def check() -> int:
    if not TARGET.exists():
        print(f"错误：标注资产不存在 {TARGET}（先运行 prepare）")
        return 2
    errors: List[str] = []
    total = 0
    labeled = 0
    for i, line in enumerate(TARGET.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"第 {i} 行 JSON 非法")
            continue
        if not rec.get("id") or not rec.get("message"):
            errors.append(f"第 {i} 行缺 id/message")
        lvl = rec.get("expected_level")
        status = rec.get("label_status")
        if status not in VALID_STATUS:
            errors.append(f"第 {i} 行 label_status 非法: {status!r}")
        if status == "labeled" and (lvl is None or lvl not in VALID_LEVELS):
            errors.append(f"第 {i} 行 labeled 但 expected_level 非法: {lvl!r}")
        if status == "labeled":
            labeled += 1
    print(f"校验：{total} 条，已标注 {labeled}，错误 {len(errors)}")
    for e in errors[:20]:
        print(f"  ✗ {e}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="复杂度人工标注资产准备/校验")
    parser.add_argument("--check", action="store_true", help="只校验资产 schema")
    args = parser.parse_args()
    return check() if args.check else prepare()


if __name__ == "__main__":
    raise SystemExit(main())
