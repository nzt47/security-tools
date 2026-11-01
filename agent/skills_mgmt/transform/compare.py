# -*- coding: utf-8 -*-
"""逐面对比：新技能 vs **自身已有能力**，算出「新在哪」。

五个面各自独立打分（0–1），再按设计文档 §2.1 归到四种结论：
duplicate（整体重复）/ incremental（部分新增）/ conflict（同面冲突）/ distinct（不相关）。

【为什么不用向量库】P1 是**只读、可复现**的：分词 + Jaccard 足够把 pd-* 那 15 条同源技能聚起来，
而且换个环境跑结果一致（embedding 版本漂移会让分数每天不一样）。向量留给 P3/P5。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from .facets import extract_facets, load_text

_LATIN = re.compile(r"[a-z0-9_\-]{2,}")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def tokens(text: str) -> set:
    """拉丁词 + 中文二元组（中文按 bigram 切，避免依赖分词器）。"""
    low = (text or "").lower()
    out = set(_LATIN.findall(low))
    for seg in _CJK.findall(low):
        out.update(seg[i:i + 2] for i in range(max(0, len(seg) - 1)))
    return out


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta | tb))


def _flatten(value) -> str:
    if isinstance(value, list):
        return "\n".join(str(x) for x in value)
    return str(value or "")


def facet_scores(new: Dict[str, object], old: Dict[str, object]) -> Dict[str, float]:
    scores = {}
    for facet in ("trigger", "steps", "params", "example", "guard"):
        scores[facet] = round(jaccard(_flatten(new.get(facet)), _flatten(old.get(facet))), 3)
    return scores


#: 「相反」的信号词。步骤面里两边各占一边 ⇒ 同面冲突，必须人拍板。
_NEG = ("不要", "禁止", "避免", "切勿", "never", "don't", "do not")
_POS = ("必须", "务必", "一定要", "always", "must")


def _has_conflict_signal(a: str, b: str) -> bool:
    la, lb = a.lower(), b.lower()
    a_neg = any(w in la for w in _NEG)
    b_pos = any(w in lb for w in _POS)
    b_neg = any(w in lb for w in _NEG)
    a_pos = any(w in la for w in _POS)
    return (a_neg and b_pos) or (a_pos and b_neg)


def _present(value) -> bool:
    return bool(_flatten(value).strip())


def verdict_for(scores: Dict[str, float], new: Dict[str, object], old: Dict[str, object]) -> str:
    """四选一。**空面不参与判定**：两边都没有「步骤」不等于步骤不同（都缺就是不可比）。"""
    common = {f: s for f, s in scores.items() if _present(new.get(f)) or _present(old.get(f))}
    if not common:
        return "distinct"
    trig, steps = scores["trigger"], scores["steps"]
    best = max(common.values())
    # 出现的面全部高度相似 + 触发面也像 ⇒ 整体重复（重复导入同一条时就是这条）
    if trig >= 0.85 and all(s >= 0.85 for s in common.values()):
        return "duplicate"
    if trig >= 0.6 and _has_conflict_signal(_flatten(new.get("steps")), _flatten(old.get("steps"))):
        return "conflict"
    if best >= 0.5:
        return "incremental"
    return "distinct"


def load_repo_facets(repo_path: Optional[Path] = None) -> List[Dict[str, object]]:
    """把自身已有能力全部切一遍（每次跑都现算，不落缓存 —— P1 要的是可复现）。"""
    from agent.skills_mgmt.capability_cards import DEFAULT_REPO
    repo = Path(repo_path) if repo_path else DEFAULT_REPO
    if not repo.is_dir():
        return []
    out: List[Dict[str, object]] = []
    for child in sorted(repo.iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        try:
            text = load_text(child)
        except FileNotFoundError:
            continue
        facets = extract_facets(text)
        facets["skill_id"] = child.name
        out.append(facets)
    return out


def compare_with_repo(new_facets: Dict[str, object], repo_path: Optional[Path] = None,
                      top: int = 5) -> Dict[str, object]:
    """返回 {candidates:[{skill_id, scores, verdict}], best, verdict}（按最高分排序取 top）。"""
    cands = []
    for old in load_repo_facets(repo_path):
        # 【不易】不跳过同名 id：重复导入同一条技能时，与"自己"的全等面分正是 duplicate 的证据；
        # 跳过会让「又导了一遍」显示成 distinct，进而被重写成第二条技能。
        scores = facet_scores(new_facets, old)
        cands.append({"skill_id": old["skill_id"], "scores": scores,
                      "verdict": verdict_for(scores, new_facets, old)})
    cands.sort(key=lambda c: max(c["scores"].values()), reverse=True)
    best = cands[0] if cands else None
    overall = best["verdict"] if best else "distinct"
    return {"candidates": cands[:top], "best": best, "verdict": overall}
