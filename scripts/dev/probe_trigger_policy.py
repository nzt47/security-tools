# -*- coding: utf-8 -*-
"""触发词政策探针（TASK-S11-02）—— 政策变更的改前/改后对比证据

用途（只读，不写任何运行期数据）：
  1) 打印匹配器**真实语义**读数：同一批查询在存量仓库上的候选与相似度
     （改前/改后各跑一次，命令与输入完全相同 ⇒ 可直接对比）
  2) 打印"旧规则 vs 新规则"在同一批存量条目上的准入判定对照表
     （两条规则在本脚本内**显式实现**，不依赖被测代码，故改代码后仍可复算）

用法：
    python scripts/dev/probe_trigger_policy.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from agent.workflow_learning.admission import (  # noqa: E402
    effective_trigger_patterns, single_char_triggers,
)
from agent.workflow_learning.matcher import WorkflowMatcher  # noqa: E402
from agent.workflow_learning.models import LearnedWorkflow  # noqa: E402
from agent.workflow_learning.retirement import WORKFLOW_REPO_PATH  # noqa: E402

# 固定查询集（改前/改后必须完全相同；含"单字触发词误触发"假设的直接检验项）
QUERIES = [
    "读取 JSON 配置文件并转换为 YAML 格式",   # 来源请求（应命中）
    "读取配置",                                # S10-01 报告记录的跨任务命中
    "读",                                      # 单字触发词假设检验
    "取",
    "配置",
    "帮我列出当前工作目录下的文件",
    "把代码仓库打包成 zip 压缩包",
    "统计项目里所有 Python 文件的行数并保存报告",
]


def _old_rule_admits(wf: LearnedWorkflow) -> bool:
    """旧规则（S10-01）：只要**存在**≥1 个有区分度触发词即通过。

    与 `admission.check_structure` 改前语义一致（本脚本独立实现，便于复算）。
    """
    return len(effective_trigger_patterns(wf.trigger_patterns)) >= 1


def _new_rule_admits(wf: LearnedWorkflow) -> bool:
    """新规则（S11-02）：触发词列表必须**纯净**（无任何单字/无有效字符项）。"""
    return len(single_char_triggers(wf.trigger_patterns)) == 0 and \
        len(effective_trigger_patterns(wf.trigger_patterns)) >= 1


def main() -> int:
    data = json.loads(WORKFLOW_REPO_PATH.read_text(encoding="utf-8"))
    wfs = [LearnedWorkflow(**raw) for _, raw in sorted(data.items())]

    print("=" * 78)
    print("A. 政策判定对照（同一批存量条目，两条规则显式求值）")
    print("=" * 78)
    print(f"仓库: {WORKFLOW_REPO_PATH}")
    print(f"{'workflow_id':>18} | {'steps':>5} | {'单字触发词':<22} | "
          f"{'旧规则':<6} | {'新规则':<6} | 触发词")
    for wf in wfs:
        bad = single_char_triggers(wf.trigger_patterns)
        print(f"{wf.id:>18} | {len(wf.steps or []):>5} | "
              f"{str(bad):<22} | "
              f"{'通过' if _old_rule_admits(wf) else '否决':<6} | "
              f"{'通过' if _new_rule_admits(wf) else '否决':<6} | "
              f"{list(wf.trigger_patterns)}")

    print()
    print("=" * 78)
    print("B. 匹配器实测候选（真实代码路径，min_similarity=0.3 / "
          "min_confidence=0.4）")
    print("=" * 78)
    m = WorkflowMatcher()
    admitted: list[str] = []
    rejected: list[str] = []
    for wf in wfs:
        (admitted if m.register(wf) else rejected).append(wf.id)
    print(f"注册入候选池: {admitted}")
    print(f"被准入否决  : {rejected}")
    print("-" * 78)
    for q in QUERIES:
        hits = m.match(q, top_k=5)
        if not hits:
            print(f"  {q!r:<36} -> (无候选)")
            continue
        detail = ", ".join(f"{w.id}:{s:.4f}" for w, s in hits)
        print(f"  {q!r:<36} -> {detail}")
    print()
    print("=" * 78)
    print("C. 残留缺口检验：**政策合规**（触发词纯净）的条目是否仍会被单字查询命中")
    print("   构造与 json-30a189b6 同形但触发词纯净的合成条目（只改 trigger_patterns）")
    print("=" * 78)
    from agent.workflow_learning.models import WorkflowStep  # noqa: E402

    src = next((w for w in wfs if w.id == "json-30a189b6"), None)
    if src is None:
        print("  (存量里没有 json-30a189b6，跳过)")
    else:
        pure = LearnedWorkflow(
            **{**src.model_dump(),
               "id": "wf-pure-probe",
               "trigger_patterns": ["json"],
               # 强制 active：本节的目的是隔离"触发词纯净度"这一个变量，
               # 不能被存量仓库退役后的 NOT_ACTIVE 混淆（否则 --apply 后本节失义）
               "status": "active",
               "tags": ["learned", "json"]})
        m2 = WorkflowMatcher()
        print(f"  合成条目准入: {m2.register(pure)}（触发词={pure.trigger_patterns}，"
              f"步骤数={len(pure.steps)}）")
        for q in ["读", "取", "配置", "读取", "读取配置"]:
            hits = m2.match(q, top_k=5)
            txt = ", ".join(f"{w.id}:{s:.4f}" for w, s in hits) or "(无候选)"
            print(f"    {q!r:<12} -> {txt}")
        print("  说明：若此处仍有命中，说明'单字误触发'的**根因在索引文本构成"
              "（source_user_input/task_signature 按字切分）**，"
              "而非触发词列表纯净度；触发词政策只能消除其中一条通道。")
    print()
    print("=" * 78)
    print("D. 污染触发词的**可量化代价**：generator._compute_priority 按"
          " len(trigger_patterns)>=3 给 +10")
    print("=" * 78)
    from agent.workflow_learning.generator import WorkflowGenerator  # noqa: E402
    _f = WorkflowGenerator._compute_priority
    src = next((w for w in wfs if w.id == "json-30a189b6"), None)
    if src is None:
        print("  (存量里没有 json-30a189b6，跳过)")
    else:
        before = src.priority
        stripped = LearnedWorkflow(**{**src.model_dump(),
                                      "trigger_patterns": ["json"]})
        after = _f(stripped)
        rendered_before = _f(src)
        print(f"  {src.id} 触发词={list(src.trigger_patterns)}"
              f"（有效 1 个 / 混入单字 4 个）")
        print(f"    _compute_priority(原样)   = {rendered_before}"
              f"   ← 5 个触发词命中 '>=3' 加成")
        print(f"    _compute_priority(纯净后) = {after}"
              f"   ← 仅 1 个有效触发词")
        print(f"    仓库中实测 priority 字段  = {before}"
              f"（与 _compute_priority(原样) 一致: {before == rendered_before}）")
        print(f"    污染代价 = +{rendered_before - after} 优先级"
              f"（priority_factor {0.5 + rendered_before / 200:.3f} → "
              f"{0.5 + after / 200:.3f}）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
