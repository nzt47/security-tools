# -*- coding: utf-8 -*-
"""云枢**元生功能**清单 —— 那些可能与技能造成重叠的内置能力。

【为什么单独一份】Owner 2026-11-01 澄清：「我要的是云枢自身功能，那些可能与技能造成重叠的元生功能」；
「平台能力（服务/审计/调度/监控）可以单独开一个叶子」。两者不是一回事：

  · 平台能力 = 运维对象（服务在不在、审计断没断、调度跑没跑）；
  · **元生功能 = 智能体天生就会做的事**（内置工具 + 常驻机制）—— 它是技能重复的**来源**，
    转化器做完内涵切分后必须也跟它对一遍，否则会出现「写了一条 read_file 技能」这种重复。

【本模块只读】给界面列清单，并算一份「技能 × 元生功能」的重叠提示（同一套分词 Jaccard，可复现）。

【2026-11-06 C-7「诚实版」】字面口径下清单**恒为空**（全语料字面覆盖率上限 0.133 < 阈值 0.25），
过去界面只显示「0 条」，看起来像 bug。现在把口径本身也吐出去（stats）：比了多少对、上限分多大、
最高的一对是谁、阈值为什么是 0.25，以及「要发现语义重复得靠向量召回」。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

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

#: 重叠提示的阈值：低于它不报（避免满屏噪音）。
#: 【为什么是 0.25 而不是 0.1】0.1 会把「请 / 文件 / 使用」这类泛词撞出来的对全放进来 ——
#: 2026-11-01 上线实测过，3 条重叠全是假阳性。宁可空清单 + 把口径说清楚，也不放噪音。
OVERLAP_THRESHOLD = 0.25
#: 每条技能最多报几个重叠对象
_TOP_PER_SKILL = 3
#: 技能正文**点名**带 _ 或 - 的工具名时给的强信号分（可解释、可复核，比调阈值靠谱）
_EXPLICIT_BOOST = 0.6


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


def _body_of(skill: Dict[str, object]) -> str:
    """技能侧被比的正文（触发 + 步骤 + 参数 + 约束，不含示例）。

    实现在 transform.compare.facet_body —— 向量口径要同一份正文，放这里会与
    overlap_vector 形成循环依赖（arch_rules 实测会判违规）。
    """
    from agent.skills_mgmt.transform.compare import facet_body
    return facet_body(skill)


def _explicit_mentioned(tool_id: str, body_low: str) -> bool:
    """技能正文里**点名**了某个元生工具（只认有辨识度的复合名 + 整词匹配）。"""
    if not tool_id or ("_" not in tool_id and "-" not in tool_id):
        return False
    return bool(re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(tool_id.lower()),
                          body_low))


def _empty_reason(stats: Dict[str, object]) -> str:
    """清单为空时把口径讲清楚 —— C-7 诚实版的核心：空 ≠ bug。"""
    if stats["hinted_skills"]:
        return ("命中的都是字面覆盖率 / 点名信号；语义重复仍可能漏（阈值保持 %.2f，未下调）"
                % OVERLAP_THRESHOLD)
    if not stats["pairs"]:
        return "没有可比对的对象（工具说明为空或技能正文全空），不能据此说「没有重叠」"
    pair = stats.get("max_text_pair") or {}
    return ("清单为空是**口径**所致，不是 bug：比了 %d 对（%d 条技能 × %d 个元生功能），"
            "字面覆盖率最高只有 %.3f（%s → %s），低于阈值 %.2f；"
            "只有技能正文点名带 _ 或 - 的工具名才会被抬到 %.1f。"
            "要发现语义级重复得靠向量召回（CP_SKILL_OVERLAP_VECTOR=1，默认关，C-7 第二步）"
            % (stats["pairs"], stats["skills_compared"], stats["tools_compared"],
               stats["max_text_score"], pair.get("skill_id") or "-",
               pair.get("native_id") or "-", OVERLAP_THRESHOLD, _EXPLICIT_BOOST))


def _compute(skills: List[Dict[str, object]],
             tools: List[Dict[str, object]]) -> Tuple[List[Dict[str, object]], Dict[str, object]]:
    """一次遍历同时算出「重叠清单」与「口径统计」（界面要的是后者解释前者）。"""
    from agent.skills_mgmt.transform.compare import containment

    tool_text = [(t["id"], str(t.get("summary") or "")) for t in tools]
    hints: List[Dict[str, object]] = []
    compared = 0
    pairs = 0
    max_text = {"score": 0.0, "skill_id": "", "native_id": ""}
    max_eff = {"score": 0.0, "skill_id": "", "native_id": ""}
    for sk in skills:
        skill_id = sk.get("skill_id")
        body = _body_of(sk)
        if not body.strip():
            continue
        compared += 1
        body_low = body.lower()
        scored = []
        for tool_id, text in tool_text:
            if not str(text).strip():
                continue
            pairs += 1
            score = containment(body, text)
            if score > max_text["score"] or not max_text["native_id"]:
                max_text = {"score": score, "skill_id": skill_id, "native_id": tool_id}
            reason = "containment"
            # 【不易】技能正文里**点名**了某个元生工具 ⇒ 强信号：它十有八九在教「怎么用这个工具」
            # 或把工具行为又写了一遍。只认**有辨识度**的工具名（含 _ 或 - 的复合名）+ 整词匹配：
            # 否则 git / edit / remember 这类普通英文词会把整份文档都判成「在讲这个工具」
            # （2026-11-01 上线实测：3 条重叠全是这种假阳性）。
            if _explicit_mentioned(tool_id, body_low) and _EXPLICIT_BOOST > score:
                score = _EXPLICIT_BOOST
                reason = "explicit_mention"
            if score > max_eff["score"] or not max_eff["native_id"]:
                max_eff = {"score": score, "skill_id": skill_id, "native_id": tool_id}
            if score >= OVERLAP_THRESHOLD:
                scored.append({"native_id": tool_id, "kind": "tool",
                               "score": round(score, 3), "reason": reason})
        scored.sort(key=lambda x: x["score"], reverse=True)
        if scored:
            hints.append({"skill_id": skill_id, "name": sk.get("name"),
                          "matches": scored[:_TOP_PER_SKILL]})
    hints.sort(key=lambda h: h["matches"][0]["score"], reverse=True)

    stats: Dict[str, object] = {
        "threshold": OVERLAP_THRESHOLD,
        "skills_total": len(skills),
        "skills_compared": compared,
        "tools_compared": len(tool_text),
        "pairs": pairs,
        "max_text_score": round(max_text["score"], 3),
        "max_text_pair": dict(max_text),
        "max_effective_score": round(max_eff["score"], 3),
        "max_effective_pair": dict(max_eff),
        "hinted_skills": len(hints),
        "explicit_boost": _EXPLICIT_BOOST,
    }
    stats["why_empty"] = _empty_reason(stats)
    return hints, stats


def overlap_hints(skills: Optional[List[Dict[str, object]]] = None,
                  tools: Optional[List[Dict[str, object]]] = None) -> List[Dict[str, object]]:
    """技能 × 元生功能的重叠提示（containment + 点名工具强信号；只读、可复现）。

    只比「技能正文（触发+步骤+参数+约束） × 工具说明」——工具说明是**元生功能的自然语言口径**，
    拿它当对比面比拿工具名更准（名字常常是缩写）。
    """
    skills = skills if skills is not None else _skill_facets()
    tools = tools if tools is not None else list_tools()
    return _compute(skills, tools)[0]


def overlap_stats(skills: Optional[List[Dict[str, object]]] = None,
                  tools: Optional[List[Dict[str, object]]] = None) -> Dict[str, object]:
    """重叠检测的**口径统计**（界面用它解释「为什么清单是空的」）。"""
    skills = skills if skills is not None else _skill_facets()
    tools = tools if tools is not None else list_tools()
    return _compute(skills, tools)[1]


def native_functions() -> Dict[str, object]:
    """界面用：分组清单 + 重叠提示 + 口径统计 + 向量候选（默认关）。"""
    tools = list_tools()
    skills = _skill_facets()
    hints, stats = _compute(skills, tools)
    from agent.skills_mgmt.overlap_vector import vector_candidates  # 只有开启时才真加载模型
    vector = vector_candidates(skills, tools)
    return {
        "groups": [
            {"kind": "tool", "title": "内置工具",
             "desc": "模型可直接调用；技能若把同一件事再写一遍就是重复",
             "count": len(tools), "items": tools},
            {"kind": "resident", "title": "常驻机制",
             "desc": "不建文件轨但对模型始终生效 / 按需注入的机制",
             "count": len(RESIDENT_MECHANISMS), "items": list(RESIDENT_MECHANISMS)},
        ],
        "overlaps": hints,
        "threshold": OVERLAP_THRESHOLD,
        "stats": stats,
        # C-7 第二步：向量候选（默认关；与字面候选**并列**呈现，不合并计数）
        "vector_overlaps": vector["items"],
        "vector": vector["stats"],
    }
