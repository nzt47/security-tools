"""合并归一 — 把多路子代理产物合并为一份可复现步骤序列。

职责：
    1. 拼接各素材的 steps（保序：素材顺序 × 步骤内 seq）；
    2. 相邻/近似去重（同动作文本重复只保留首条，标注多来源）；
    3. 生成任务签名（关键词字典序拼接，复用 learner 同款分词）与触发词；
    4. 产出 DistilledProcess（method 取优先级 llm > rule）。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Dict, List

from agent.process_distill.models import DistilledProcess, DistilledStep

_STOP_WORDS = {
    "的", "了", "在", "是", "我", "你", "他", "她", "它", "们",
    "和", "与", "或", "及", "但", "而", "请", "帮", "给", "把",
    "这", "那", "一", "二", "三", "个", "中", "上", "下",
    "the", "a", "an", "is", "are", "was", "were", "be", "been",
    "to", "of", "in", "on", "at", "for", "with", "by",
    "and", "or", "but", "if", "then", "so", "do", "does", "did",
    "i", "you", "he", "she", "it", "we", "they",
    "please", "help", "me", "my", "your",
}

_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9_]+|[\u4e00-\u9fff]")

# 相邻步骤判定为重复的相似度阈值
_DUP_SIM_THRESHOLD = 0.92


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").lower())


def _similar(a: str, b: str) -> float:
    """字符级 Jaccard（用于相邻步骤去重；中文素材较可靠）。"""
    na, nb = set(_norm(a)), set(_norm(b))
    if not na or not nb:
        return 0.0
    inter = len(na & nb)
    return inter / (len(na | nb) or 1)


def _extract_keywords(text: str, top_k: int = 5) -> List[str]:
    tokens = [t for t in _WORD_RE.findall((text or "").lower())
              if t not in _STOP_WORDS]
    freq: Counter = Counter(tokens)
    return [t for t, _ in freq.most_common(top_k)]


def make_task_signature(name: str, triggers: List[str]) -> str:
    """任务签名：触发词+名称关键词 字典序 | 拼接（与 workflow learner 对齐）。"""
    kws = set(triggers or [])
    kws.update(_extract_keywords(name or "", top_k=10))
    kws = {k for k in kws if k and k not in _STOP_WORDS}
    return "|".join(sorted(kws)) or "general"


def merge_results(results: List[Dict[str, Any]],
                  default_name: str = "蒸馏流程") -> DistilledProcess:
    """多路蒸馏结果 → 合并后的 DistilledProcess。

    results: distill_parallel() 返回的 results 列表
             [{material_id, method, payload: {name, description, steps, ...}}]
    """
    all_steps: List[Dict[str, Any]] = []
    sources: List[str] = []
    name_hints: List[str] = []
    desc_hints: List[str] = []
    trigger_counter: Counter = Counter()
    method_rank = {"rule": 0, "llm": 1}
    best_method = "rule"
    expected_outputs: List[str] = []

    for r in results or []:
        payload = r.get("payload") or {}
        mid = r.get("material_id", "")
        if mid and mid not in sources:
            sources.append(mid)
        if r.get("method") == "llm":
            best_method = "llm"
        # 收集名称/描述候选（取非空且不含"降级"字样）
        nm = str(payload.get("name") or "").strip()
        if nm and "降级" not in nm:
            name_hints.append(nm)
        desc = str(payload.get("description") or "").strip()
        if desc and "规则提取降级" not in desc and len(desc) >= 4:
            desc_hints.append(desc)
        for t in (payload.get("trigger_patterns") or []):
            t = str(t).strip()
            if t:
                trigger_counter[t] += 1
        eo = str(payload.get("expected_output") or "").strip()
        if eo:
            expected_outputs.append(eo)
        for st in (payload.get("steps") or []):
            if isinstance(st, dict) and st.get("action"):
                st = dict(st)
                st.setdefault("source", mid)
                all_steps.append(st)

    # 相邻去重（近似文本只留首条；工具步骤不相邻则保留）
    merged: List[Dict[str, Any]] = []
    for st in all_steps:
        action = str(st.get("action") or "").strip()
        if not action:
            continue
        prev = merged[-1] if merged else None
        if prev and _similar(prev.get("action", ""), action) >= _DUP_SIM_THRESHOLD:
            if st.get("tool") and not prev.get("tool"):
                prev["tool"] = st["tool"]
                prev["params"] = st.get("params", {})
            continue
        merged.append(st)

    # 重新编号
    for i, st in enumerate(merged, start=1):
        st["seq"] = i

    # 名称：优先出现最多的名称提示，否则默认名
    name = default_name
    if name_hints:
        name = Counter(name_hints).most_common(1)[0][0][:200]
    triggers = [t for t, _ in trigger_counter.most_common(4)]
    if not triggers:
        triggers = _extract_keywords(name, top_k=3)
    # 描述：优先素材描述（多素材取信息量最长者），再补来源与预期产出
    desc_parts: List[str] = []
    if desc_hints:
        best_desc = max(desc_hints, key=len)
        desc_parts.append(best_desc[:300])
    if sources:
        desc_parts.append(f"由 {len(sources)} 份素材蒸馏生成")
    else:
        desc_parts.append("蒸馏生成")
    if expected_outputs:
        desc_parts.append("预期产出: " + expected_outputs[0][:120])
    description = "。".join(desc_parts)

    steps = [DistilledStep(**{k: st[k] for k in (
        "seq", "action", "tool", "params", "condition", "note",
        "source", "confidence") if k in st})
        for st in merged]

    return DistilledProcess(
        name=name,
        description=description,
        task_signature=make_task_signature(name, triggers),
        trigger_patterns=triggers,
        steps=steps,
        expected_output=expected_outputs[0][:500] if expected_outputs else "",
        sources=sources,
        method=best_method,
        tags=list({*triggers[:3], "distilled", "from_knowledge"}),
    )
