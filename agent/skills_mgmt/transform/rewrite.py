# -*- coding: utf-8 -*-
"""P5：LLM 参与重写 —— **每条产出都必须能指回原文**（严格引用校验）。

【为什么不能只"让模型写两节"】外来技能是要进自己的库、进每次提示装配的：
模型顺手编一个不存在的 flag / 场景，这条技能就变成负资产，而人很难在 39 KB 正文里
逐句核对。所以这里的规则是硬的：

  1. 模型**只能引用**原文（给 `quote`，逐字片段），不能自己造句子；
  2. `quote` 必须能在原文里逐字找到（归一化后 substring 匹配），找不到**整条丢弃**；
  3. 行号由**我们自己**按 quote 反查 —— 不信模型给的行号；
  4. 一条都验不过 ⇒ 明确回「没有可提取的内涵」，而不是给一份看起来像样的编造；
  5. 落盘只写 `CARD.md`（L1 卡），**不改 skill.md 正文**；卡片头部标注
     `generated_by: llm` 与 `verified: N/N`，人一眼能看出哪些是模型整理、依据在哪。

【可测性】LLM 调用是**注入的**（`chat(prompt, system) -> str`），单测不联网。
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

#: 卡片里允许出现的面（与 capability_cards.FACETS 同口径，只取这几面）
SECTION_KEYS = ("when_to_use", "steps", "params", "guard", "example")
SECTION_TITLES = {
    "when_to_use": "何时使用",
    "steps": "步骤",
    "params": "参数",
    "guard": "约束 / 反例",
    "example": "示例",
}

#: 送进模型的正文上限（字符）。超了宁可截断 + 明说，也不偷偷分段重排。
MAX_INPUT_CHARS = 12000
#: 单条 quote 的上限（过长的一律视为"整段搬运"，不是引用）
MAX_QUOTE_CHARS = 240
#: 一次最多要多少条（防止模型刷屏）
MAX_ITEMS = 40


def _norm(text: str) -> str:
    """归一化：折叠空白、去零宽字符、统一全半角引号 —— 只影响比对，不改原文。"""
    s = str(text or "")
    s = s.replace("\u200b", "").replace("\ufeff", "")
    s = s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _norm_lines(text: str) -> List[str]:
    return [_norm(line) for line in str(text or "").splitlines()]


def locate(text: str, quote: str) -> Optional[Tuple[int, str]]:
    """把 quote 在原文里定位：返回 (1-based 行号, 该行原文)；找不到返回 None。

    判据是**归一化后的逐字包含**（跨行 quote 会先拼成一行再找），不做模糊匹配 ——
    模糊匹配等于给模型留了"差不多就行"的口子。
    """
    q = _norm(quote)
    if not q or len(q) > MAX_QUOTE_CHARS:
        return None
    lines = str(text or "").splitlines()
    normed = _norm_lines(text)
    for idx, line in enumerate(normed, start=1):
        if q in line:
            return idx, lines[idx - 1].strip()
    joined = " ".join(normed)
    pos = joined.find(q)
    if pos < 0:
        return None
    # 跨行：按累计长度反推行号
    acc = 0
    for idx, line in enumerate(normed, start=1):
        acc += len(line) + 1
        if acc > pos:
            return idx, lines[idx - 1].strip()
    return None


def _extract_json(raw: str) -> Dict[str, Any]:
    """从模型输出里抠 JSON（容忍 ```json 围栏与前后废话）。"""
    s = str(raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"```\s*$", "", s)
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 模型常带说明文字
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return {}
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}


SYSTEM_PROMPT = (
    "你是技能整理助手。任务：只从给定正文中**摘录**内容，整理成能力卡的分节。\n"
    "硬性规则：\n"
    "1. 每一条都必须给出 quote —— **从正文逐字复制**的片段（≤120 字，可跨行但不要改写）；\n"
    "2. 禁止改写 quote，禁止合并多处、禁止补充正文里没有的信息；\n"
    "3. text 可以用你自己的话概括，但必须**严格由该 quote 支撑**；\n"
    "4. 正文里没有的面向就留空数组（不要编）。\n"
    '只输出 JSON：{"items":[{"section":"when_to_use|steps|params|guard|example",'
    '"text":"...","quote":"..."}]}'
)


def _build_user_prompt(title: str, text: str, limit: int) -> str:
    clipped = text[:limit]
    tail = "" if len(text) <= limit else "\n\n（正文后 %d 字未提供）" % (len(text) - limit)
    return ("技能名：%s\n\n===== 正文开始 =====\n%s\n===== 正文结束 =====%s\n\n"
            "请按 JSON 输出，最多 %d 条。" % (title or "(未命名)", clipped, tail, MAX_ITEMS))


def llm_configured() -> bool:
    """只查配置（不建客户端、不发请求）：界面据此决定要不要显示「LLM 补两节」按钮。"""
    import os
    key = (os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return True
    try:
        from agent.env_config_manager import get_env_config_manager
        get_env_config_manager().reload()
    except Exception:  # noqa: BLE001 .env 加载失败 ⇒ 当没配
        return False
    return bool((os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY") or "").strip())


def default_chat() -> Optional[Callable[[str, str], str]]:
    """按 .env 构造 LLM 调用（不可用返回 None，调用方据此降级/报错）。"""
    try:
        from agent.skills_mgmt.service import _build_default_llm
        svc = _build_default_llm()
        if svc is None:
            return None

        def _call(prompt: str, system: str) -> str:
            return svc.chat([{"role": "user", "content": prompt}],
                            system_prompt=system, max_tokens=4000, temperature=0.1)

        return _call
    except Exception:  # noqa: BLE001 构建失败就当没配 LLM
        return None


def verify_items(text: str, items: Any) -> Dict[str, Any]:
    """严格引用校验：逐条定位 quote；不通过的直接丢弃（并记录原因）。"""
    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    if not isinstance(items, list):
        return {"kept": kept, "dropped": dropped}
    for raw in items[:MAX_ITEMS]:
        if not isinstance(raw, dict):
            continue
        section = str(raw.get("section") or "").strip()
        body = str(raw.get("text") or "").strip()
        quote = str(raw.get("quote") or "").strip()
        if section not in SECTION_KEYS:
            dropped.append({"reason": "section 不在白名单", "text": body[:80], "quote": quote[:80]})
            continue
        if not body:
            dropped.append({"reason": "text 为空", "text": "", "quote": quote[:80]})
            continue
        hit = locate(text, quote)
        if hit is None:
            dropped.append({"reason": "quote 在原文里找不到（可能是模型改写或编造）",
                            "section": section, "text": body[:120], "quote": quote[:120]})
            continue
        line, source = hit
        kept.append({"section": section, "text": body, "line": line, "source": source})
    return {"kept": kept, "dropped": dropped}


def group_sections(kept: List[Dict[str, Any]]) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {k: [] for k in SECTION_KEYS}
    for item in kept:
        out.setdefault(item["section"], []).append(item["text"])
    return out


def render_card_md(name: str, sections: Dict[str, List[str]], stats: Dict[str, Any],
                   dropped: Optional[List[Dict[str, Any]]] = None) -> str:
    """把经验证的分节渲染成 CARD.md（带来源标注；未通过的不进正文，只计数）。"""
    lines = ["# %s" % (name or ""), ""]
    lines.append("> LLM 整理，逐条经**原文引用校验**：verified %d / %d（未通过的已丢弃）。"
                 % (stats.get("verified", 0), stats.get("total", 0)))
    lines.append("")
    for key in ("when_to_use", "steps", "params", "guard", "example"):
        items = sections.get(key) or []
        lines.append("## %s" % SECTION_TITLES[key])
        lines.append("")
        if items:
            for text in items:
                lines.append("- %s" % text)
        else:
            lines.append("（原文没有这一节，或没有能通过引用校验的条目）")
        lines.append("")
    if dropped:
        lines.append("## 未通过校验（已丢弃）")
        lines.append("")
        for d in dropped[:10]:
            lines.append("- %s：%s" % (d.get("reason"), str(d.get("text") or d.get("quote") or "")[:80]))
        lines.append("")
    return "\n".join(lines)


def draft_sections(text: str, *, title: str = "", chat: Optional[Callable[[str, str], str]] = None,
                   limit: int = MAX_INPUT_CHARS) -> Dict[str, Any]:
    """让 LLM 摘录分节，并对每条做严格引用校验。返回可直接给 UI 的结构。"""
    body = str(text or "")
    if not body.strip():
        return {"ok": False, "reason": "正文为空", "sections": {}, "kept": [], "dropped": []}
    call = chat or default_chat()
    if call is None:
        return {"ok": False, "reason": "没有可用的 LLM（.env 缺 LLM_API_KEY/DEEPSEEK_API_KEY）",
                "sections": {}, "kept": [], "dropped": []}
    try:
        raw = call(_build_user_prompt(title, body, limit), SYSTEM_PROMPT)
    except Exception as e:  # noqa: BLE001 网络/超时都当"这条走不了 LLM"
        return {"ok": False, "reason": "LLM 调用失败：%s" % type(e).__name__,
                "sections": {}, "kept": [], "dropped": []}

    data = _extract_json(raw)
    items = data.get("items") if isinstance(data, dict) else None
    result = verify_items(body, items)
    kept, dropped = result["kept"], result["dropped"]
    stats = {"total": len(items or []), "verified": len(kept)}
    if not kept:
        return {"ok": False, "reason": "没有一条能对上原文（严格引用校验未通过）",
                "sections": {}, "kept": [], "dropped": dropped, "stats": stats}
    sections = group_sections(kept)
    card = render_card_md(title, sections, stats, dropped)
    return {"ok": True, "reason": "", "sections": sections, "kept": kept, "dropped": dropped,
            "stats": stats, "card_md": card}
