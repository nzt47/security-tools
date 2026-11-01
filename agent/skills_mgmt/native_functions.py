# -*- coding: utf-8 -*-
"""云枢**元生功能**清单 —— 那些可能与技能造成重叠的内置能力。

【为什么单独一份】Owner 2026-11-01 澄清：「我要的是云枢自身功能，那些可能与技能造成重叠的元生功能」；
「平台能力（服务/审计/调度/监控）可以单独开一个叶子」。两者不是一回事：

  · 平台能力 = 运维对象（服务在不在、审计断没断、调度跑没跑）；
  · **元生功能 = 智能体天生就会做的事**（内置工具 + 常驻机制）—— 它是技能重复的**来源**，
    转化器做完内涵切分后必须也跟它对一遍，否则会出现「写了一条 read_file 技能」这种重复。

【本模块只读】给界面列清单，并算一份「技能 × 元生功能」的重叠提示（同一套分词 Jaccard，可复现）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

#: 常驻机制（不是工具，但对模型始终生效/按需注入）—— 手工维护的短清单。
#: 判据：**不建文件轨、不在 tools 注册表里、但确实改变模型行为**。
RESIDENT_MECHANISMS = (
    {"id": "global-core-principles", "name": "常驻准则",
     "summary": "主轨独有、始终生效的行为准则（H-3：不建文件轨，走 prompt 装配）"},
    {"id": "skill", "name": "易之三义",
     "summary": "注入型指令（编码前输出 san_yi_analysis），不参与语义检索"},
    {"id": "memory", "name": "记忆",
     "summary": "短期消息 + 长期记忆 + 向量检索（技能若教模型「怎么记」就是重复）"},
    {"id": "knowledge", "name": "知识库",
     "summary": "入库/提炼/产卡/检索（kb_* 系列工具背后的机制）"},
    {"id": "workflow", "name": "工作流引擎",
     "summary": "确定性 DAG 执行（与「技能里写步骤」是两条路，重叠时优先工作流）"},
    {"id": "scheduler", "name": "调度器",
     "summary": "定时任务（技能若教「每天跑一次」应改走调度）"},
    {"id": "audit", "name": "审计链",
     "summary": "哈希链审计（技能若自带「记录日志」要注意与审计重复）"},
)

#: 重叠提示的阈值：低于它不报（避免满屏噪音）
OVERLAP_THRESHOLD = 0.25
#: 每条技能最多报几个重叠对象
_TOP_PER_SKILL = 3


def list_tools() -> List[Dict[str, object]]:
    """内置工具（模型可直接调用，不需要技能注入）。"""
    from agent.tools import list_tools as _list, declared_disabled_tool_names
    disabled = declared_disabled_tool_names()
    out = []
    for t in _list():
        name = t.get("name")
        desc = (t.get("description") or "").strip().replace("\n", " ")
        out.append({"id": name, "name": name, "summary": desc[:120],
                    "status": "off" if name in disabled else "ok"})
    return out


def _skill_facets() -> List[Dict[str, object]]:
    from agent.skills_mgmt.capability_cards import build_cards
    return build_cards()


def overlap_hints(skills: Optional[List[Dict[str, object]]] = None,
                  tools: Optional[List[Dict[str, object]]] = None) -> List[Dict[str, object]]:
    """技能 × 元生功能的重叠提示（containment + 点名工具强信号；只读、可复现）。

    只比「技能正文（触发+步骤+参数+约束） × 工具说明」——工具说明是**元生功能的自然语言口径**，
    拿它当对比面比拿工具名更准（名字常常是缩写）。
    """
    from agent.skills_mgmt.transform.compare import containment

    skills = skills if skills is not None else _skill_facets()
    tools = tools if tools is not None else list_tools()
    tool_text = [(t["id"], str(t.get("summary") or "")) for t in tools]

    hints: List[Dict[str, object]] = []
    for sk in skills:
        parts = ((sk.get("trigger") or []) + (sk.get("steps") or [])
                 + (sk.get("params") or []) + (sk.get("guard") or []))
        body = "\n".join(str(x) for x in parts)
        if not body.strip():
            continue
        body_low = body.lower()
        scored = []
        for tool_id, text in tool_text:
            if not text.strip():
                continue
            score = containment(body, text)
            reason = "containment"
            # 【不易】技能正文里**点名**了某个元生工具 ⇒ 强信号：它十有八九在教「怎么用这个工具」
            # 或把工具行为又写了一遍。这条规则可解释、可复核，比调阈值更靠谱
            # （2026-11-01 实测：Jaccard/containment 在长短文本之间会被稀释到 0.3 以下）。
            if tool_id and tool_id.lower() in body_low:
                score = max(score, 0.6)
                reason = "explicit_mention"
            if score >= OVERLAP_THRESHOLD:
                scored.append({"native_id": tool_id, "kind": "tool",
                               "score": round(score, 3), "reason": reason})
        scored.sort(key=lambda x: x["score"], reverse=True)
        if scored:
            hints.append({"skill_id": sk["skill_id"], "name": sk.get("name"),
                          "matches": scored[:_TOP_PER_SKILL]})
    hints.sort(key=lambda h: h["matches"][0]["score"], reverse=True)
    return hints


def native_functions() -> Dict[str, object]:
    """界面用：分组清单 + 重叠提示。"""
    tools = list_tools()
    skills = _skill_facets()
    return {
        "groups": [
            {"kind": "tool", "title": "内置工具",
             "desc": "模型可直接调用；技能若把同一件事再写一遍就是重复",
             "count": len(tools), "items": tools},
            {"kind": "resident", "title": "常驻机制",
             "desc": "不建文件轨但对模型始终生效 / 按需注入的机制",
             "count": len(RESIDENT_MECHANISMS), "items": list(RESIDENT_MECHANISMS)},
        ],
        "overlaps": overlap_hints(skills, tools),
        "threshold": OVERLAP_THRESHOLD,
    }
