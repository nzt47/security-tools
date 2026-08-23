"""评估集回归基线建立 CLI（复查补充 · P0-2）

把"评估集回归门禁"从"代码就绪"推进到"基线就绪"：对技能存储中已发布
（PUBLISHED/APPROVED 且 enabled）的技能，在评估集 v1 上执行一次真实评估并
记录 baseline（BaselineStore → data/evals/baselines.json）。

基线建立后，回归门禁（RegressionGate / rollout regression_gate=enforce）
才有"不退化"的参照物；warn_only 告警与 enforce 拦截在基线缺失时按
NO_SAMPLES/缺基线显式处理（绝不伪造指标）。

用法:
    python scripts/establish_regression_baselines.py                 # dry-run 预演
    python scripts/establish_regression_baselines.py --apply         # 实际建立基线
    python scripts/establish_regression_baselines.py --skill <id>    # 单技能
    python scripts/establish_regression_baselines.py --out report.json

【不易】:
    - 只调用既有 RegressionGate.evaluate(record_baseline=True)，不修改任何
      评估/进化算法；基线条目写入既有 BaselineStore（幂等覆盖）。
    - 默认 dry-run：不执行任何评估、不写任何文件（安全底线）。
    - 技能缺失/样本缺失/预算熔断 → 显式跳过并说明，绝不伪造基线分数。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("establish_baselines")


def _load_skills(skill_ids: Optional[List[str]] = None) -> List[Any]:
    """从 SkillStore 加载候选技能（已发布 + enabled）"""
    from agent.skills_mgmt.store import SkillStore
    from agent.skills_mgmt.models import SkillStatus

    store = SkillStore()
    skills = store.list_all()
    published = [s for s in skills
                 if getattr(s, "status", None) in (SkillStatus.PUBLISHED,
                                                   SkillStatus.APPROVED)]
    candidates = [s for s in published if getattr(s, "enabled", True)]
    if skill_ids:
        wanted = set(skill_ids)
        candidates = [s for s in candidates if s.id in wanted]
    return candidates


def _dry_run_report(candidates: List[Any]) -> Dict[str, Any]:
    from agent.skills_mgmt.eval_regression import RegressionGate

    gate = RegressionGate()
    rows = []
    for s in candidates:
        category = "unknown"
        try:
            ev = gate._build_evaluator(s)
            category = ev.resolve_category(s)
        except Exception:  # noqa: BLE001
            category = "unknown"
        rows.append({
            "skill_id": s.id, "name": s.name, "category": category,
            "version": str(getattr(s, "version", "?")),
            "has_baseline": gate.has_baseline(s.id),
        })
    return {"mode": "dry_run", "candidates": rows, "count": len(rows)}


def _apply_baselines(candidates: List[Any], out: Optional[Path]) -> Dict[str, Any]:
    from agent.skills_mgmt.eval_regression import RegressionGate

    gate = RegressionGate()
    rows: List[Dict[str, Any]] = []
    for s in candidates:
        try:
            result = gate.evaluate(s, record_baseline=True)
            rows.append({
                "skill_id": s.id,
                "status": result.status,
                "score": result.score,
                "sample_count": result.sample_count,
                "used_tokens": result.used_tokens,
                "baseline": gate.baseline_score(s.id),
                "notes": result.notes[:5],
            })
            logger.info("[基线] %s → status=%s score=%s samples=%d",
                        s.id, result.status, result.score, result.sample_count)
        except Exception as exc:  # noqa: BLE001 单技能失败不中断批量
            rows.append({"skill_id": s.id, "status": "error", "error": str(exc)})
            logger.warning("[基线] %s 建立失败（跳过）: %s", s.id, exc)
    report = {
        "mode": "apply",
        "established": sum(1 for r in rows if r.get("status") in ("pass", "fail")),
        "skipped": sum(1 for r in rows if r.get("status") in
                       ("no_samples", "budget_exceeded", "error")),
        "rows": rows,
    }
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="评估集回归基线建立（dry-run 默认）")
    parser.add_argument("--apply", action="store_true",
                        help="实际执行评估并记录基线（默认仅 dry-run 预演）")
    parser.add_argument("--skill", action="append", default=None,
                        help="限定技能 id（可多次）；缺省=全部已发布技能")
    parser.add_argument("--out", default=None, help="报告输出 JSON 路径")
    args = parser.parse_args()

    candidates = _load_skills(args.skill)
    if not candidates:
        logger.info("技能存储中无已发布技能（data/skills_mgmt.json 为空或无可评估项）；"
                    "基线建立流程待首个技能入库后执行。")
        report = {"mode": "dry_run" if not args.apply else "apply",
                  "candidates": [], "count": 0}
    elif not args.apply:
        report = _dry_run_report(candidates)
        logger.info("dry-run：%d 个候选技能（加 --apply 实际建立基线）", report["count"])
    else:
        report = _apply_baselines(candidates, Path(args.out) if args.out else None)
        logger.info("基线建立完成：established=%d skipped=%d",
                    report.get("established", 0), report.get("skipped", 0))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
