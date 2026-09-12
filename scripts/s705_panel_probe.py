#!/usr/bin/env python
"""治理面板三列读数（TASK-S7-05 步骤 4：面板出真数取证）

【两种读法，都保留】

1. **显式口径**（默认）：`--runtime-root <根>` 由本脚本推导 `events / shadow /
   promote_pr` 三个目录并**显式传入** `pipeline_view()`，`registry` 取该根的
   `data/descriptors.json`。用于在 worktree 里复核部署根的真实数字。
2. **面板默认口径**（`--use-env-defaults`）：不带任何目录/台账参数调用
   `pipeline_view()`，完全走面板自己的默认解析（env → 代码根）。
   在**部署根**执行时，这就是"人工打开面板看到的东西"，是防"脚本自证"的独立复核。

【口径纪律（S5-02 / S6-01）】

- 每个数字都带 `source` + `formula`（面板自己给的，不二次计算）；
- 缺数据源记 `None`（**不以 0 冒充**）；样本 <20 只披露不考核；
- 本脚本**只读**：不写事件、不推进 stage、不落任何运行时文件（除非 `--out`）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CODE_ROOT = os.path.dirname(SCRIPT_DIR)
if CODE_ROOT not in sys.path:
    sys.path.insert(0, CODE_ROOT)


def _git_common_root() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--git-common-dir"],
                             cwd=CODE_ROOT, capture_output=True, text=True,
                             timeout=15)
        if out.returncode == 0:
            common = str(out.stdout or "").strip()
            if common:
                if not os.path.isabs(common):
                    common = os.path.join(CODE_ROOT, common)
                root = os.path.dirname(os.path.abspath(common))
                if os.path.isdir(root):
                    return root
    except Exception:  # noqa: BLE001
        pass
    return CODE_ROOT


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="治理面板三列真实数字取证")
    parser.add_argument("--runtime-root", default="",
                        help="运行时根（默认：由 git common dir 推导的部署根）")
    parser.add_argument("--code-root", default="",
                        help=("从哪个代码根 import `agent.*`（默认本脚本所在仓库）。"
                              "配合 --use-env-defaults 指向**部署根**时，得到的才是"
                              "『人工打开面板所见』的读数 —— 面板默认目录按**代码根**"
                              "解析，代码根不同则读到不同的运行时区"))
    parser.add_argument("--days", type=int, default=30, help="事件窗口天数")
    parser.add_argument("--limit", type=int, default=50, help="每列明细上限")
    parser.add_argument("--use-env-defaults", action="store_true",
                        help="完全走面板默认解析（在部署根执行时=人工打开面板所见）")
    parser.add_argument("--out", default="", help="把读数写成 JSON（可选）")
    return parser.parse_args(argv)


def probe(root: str, *, days: int, limit: int,
          use_env_defaults: bool) -> Dict[str, Any]:
    from agent.ui_panels.data import pipeline_view

    if use_env_defaults:
        return pipeline_view(days=int(days), limit=int(limit))
    from agent.descriptors.registry import DescriptorRegistry
    base = os.path.abspath(root)
    reg = DescriptorRegistry(path=os.path.join(base, "data", "descriptors.json"),
                             autosave=False)
    return pipeline_view(days=int(days), limit=int(limit),
                         events_dir=os.path.join(base, "data", "events"),
                         shadow_dir=os.path.join(base, "data", "digestion", "shadow"),
                         promote_dir=os.path.join(base, "data", "digestion",
                                                  "promote_pr"),
                         registry=reg)


def render(payload: Dict[str, Any], *, root: str, mode: str) -> str:
    lines: List[str] = []
    lines.append(f"# 治理面板三列读数（{mode}）")
    lines.append("")
    lines.append(f"- 运行时根：`{root}`")
    lines.append(f"- 生成时间：{payload.get('generated_at')}")
    lines.append("")
    lines.append("## 一、泳道（含「验收」列）")
    lines.append("")
    lines.append("| 泳道 | 事件数 | 数据源 | 公式 |")
    lines.append("|---|---|---|---|")
    for lane in payload.get("lanes", []):
        metric = lane.get("event_count") or {}
        lines.append(f"| {lane.get('title')} | {metric.get('value')} | "
                     f"{metric.get('source')} | {metric.get('formula')} |")
    summary = payload.get("summary") or {}
    lines.append("")
    lines.append("## 二、关键指标（灰度列 / 内化列）")
    lines.append("")
    lines.append("| 指标 | 值 | 数据源 | 公式 |")
    lines.append("|---|---|---|---|")
    for key in ("digest_stage_events", "applied_migrations", "shadow_runs",
                "internalize_decisions", "internalize_rate",
                "capabilities_touched"):
        item = summary.get(key) or {}
        lines.append(f"| `{key}` | {item.get('value')} | {item.get('source')} | "
                     f"{item.get('formula')} |")
    lines.append("")
    lines.append("## 三、stage 分布（真实台账）")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(summary.get("stage_distribution") or {},
                            ensure_ascii=False, indent=1))
    lines.append("```")
    lines.append("")
    lines.append("## 四、灰度 / 内化卡片")
    lines.append("")
    lines.append(f"- 灰度卡片：{len(payload.get('shadow') or [])} 条")
    for card in (payload.get("shadow") or [])[:5]:
        lines.append(f"  - `{card.get('capability_id')}`｜samples={card.get('sampled')}"
                     f"｜pass_rate={(card.get('pass_rate') or {}).get('value')}"
                     f"｜judge_kind={card.get('judge_kind')}"
                     f"｜p99_wall候选="
                     f"{(card.get('p99_wall_candidate_ms') or {}).get('value')}ms")
    lines.append(f"- 内化决策卡片：{len(payload.get('internalize') or [])} 条")
    for card in (payload.get("internalize") or [])[:5]:
        lines.append(f"  - `{card.get('capability_id')}`｜pr_id={card.get('pr_id')}"
                     f"｜verdict={card.get('verdict')}"
                     f"｜promotable={card.get('promotable')}")
    manual = payload.get("manual_review") or {}
    lines.append(f"- 人工抽检队列：{json.dumps(manual, ensure_ascii=False)[:300]}")
    lines.append("")
    lines.append("## 五、口径")
    lines.append("")
    lines.append(json.dumps(payload.get("clock") or {}, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    root = os.path.abspath(args.runtime_root or _git_common_root())
    code_root = os.path.abspath(args.code_root) if args.code_root else CODE_ROOT
    if code_root != CODE_ROOT:
        # 面板默认目录按**代码根**解析：先让目标代码根占据 sys.path 首位，
        # 并把已导入的本仓库 `agent.*` 清掉，确保读到的是目标代码根的那一份
        for name in [n for n in sys.modules if n == "agent" or
                     n.startswith("agent.")]:
            sys.modules.pop(name, None)
        while code_root in sys.path:
            sys.path.remove(code_root)
        sys.path.insert(0, code_root)
    mode = ("面板默认解析口径（env/代码根）" if args.use_env_defaults
            else "显式目录口径（--runtime-root 推导）")
    payload = probe(root, days=int(args.days), limit=int(args.limit),
                    use_env_defaults=bool(args.use_env_defaults))
    payload["_probe"] = {"runtime_root": root, "code_root": code_root, "mode": mode}
    print(render(payload, root=root, mode=f"{mode}｜代码根={code_root}"))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
        print(f"（读数已写出：{args.out}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
