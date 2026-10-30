"""评估事件台账专用的未来时间戳清理（**兼容入口**，实现已泛化）。

历史：2026-10-30 先有本脚本，专门清 `data/skills_assessment_events.jsonl` 里的 190 条
2027 污染（动态中心最新动态钉在 2027）。随后 §11.26 U2 排查发现同样的「超前本机时钟」
行散落在 7 个台账（动态事件 / 治理写 / 策略决策 / 学习 novelty / 合并备份 / 归档），
于是把实现泛化成 `scripts/purge_future_rows.py`。

本文件保留为兼容入口（旧文档、CI 用例、运维习惯命令仍可用）：
只固定**默认文件**与**历史审计动作名** `skills.assess.events.purge_future`，
备份 / 丢未来行 / 原子替换 / 回读自证 / 审计留痕全部委托给泛化实现。

用法（不变）：
    python scripts/purge_future_assessment_events.py            # dry-run（默认）
    python scripts/purge_future_assessment_events.py --execute  # 真删（先备份）
    python scripts/purge_future_assessment_events.py --json     # 机器可读
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

DEFAULT_FILE = _ROOT / "data" / "skills_assessment_events.jsonl"
#: 本入口的历史审计动作名（泛化脚本用 `purge_future_rows`）。
LEGACY_AUDIT_ACTION = "skills.assess.events.purge_future"
LEGACY_AUDIT_SUBJECT = "data/skills_assessment_events.jsonl"


def _generic():
    """按文件路径加载同级泛化实现（scripts/ 不是包，避免污染 sys.path）。"""
    spec = importlib.util.spec_from_file_location(
        "purge_future_rows", Path(__file__).with_name("purge_future_rows.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def purge(path, *, execute: bool = False) -> dict:
    """清一个文件的未来行（默认 dry-run）。签名与历史版本一致。"""
    return _generic().purge(
        path, execute=execute,
        audit_action=LEGACY_AUDIT_ACTION, audit_subject=LEGACY_AUDIT_SUBJECT)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_FILE))
    ap.add_argument("--execute", action="store_true", help="真删（默认 dry-run）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()
    res = purge(Path(args.file), execute=args.execute)
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print("[%s] %s" % ("EXECUTE" if args.execute else "DRY-RUN", res["file"]))
        print("  总行数 %d / 未来时间戳 %d（允许时差 %ds）" % (
            res["total_rows"], res["future_rows"], res["skew_seconds"]))
        if res["future_dates"]:
            print("  按日期：%s" % res["future_dates"])
        if args.execute and res["future_rows"]:
            print("  备份：%s" % res.get("backup"))
            print("  保留 %d 行；回读未来行 %d" % (res.get("kept_rows", 0), res.get("future_after", -1)))
        elif not args.execute and res["future_rows"]:
            print("  （dry-run：加 --execute 才动文件）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
