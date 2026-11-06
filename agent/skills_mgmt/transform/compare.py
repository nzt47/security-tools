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


def containment(a: str, b: str) -> float:
    """a 的词有多少比例出现在 b 里（0–1）。

    【为什么重叠检测不用 Jaccard】工具说明往往比技能正文长得多，Jaccard 会被长边稀释：
    「技能讲的就是 read_file」这种明显重复也可能只有 0.07。判断「技能是不是在重复某个元生功能」，
    要看**技能这一侧的词被覆盖了多少**（containment），而不是两边并集。
    """
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / float(len(ta))


def facet_body(skill: Dict[str, object]) -> str:
    """技能侧被对比/被嵌入的正文：触发 + 步骤 + 参数 + 约束（**不含示例**）。

    【为什么放在这里】`native_functions`（字面口径）与 `overlap_vector`（向量口径）都要
    同一份正文；放在任一方都会让两个模块互相 import（arch_rules 的 no_circular_dependency
    实测会判违规），故下沉到这个两边都依赖的叶子模块。
    """
    parts = ((skill.get("trigger") or []) + (skill.get("steps") or [])
             + (skill.get("params") or []) + (skill.get("guard") or []))
    return "\n".join(str(x) for x in parts)


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


def delta_against(new_facets: Dict[str, object], old_text: str,
                  *, threshold: float = 0.8) -> Dict[str, object]:
    """新技能里哪些「步骤/要点」在既有能力里**找不到**（= 只该并入/重写的部分）。

    【为什么要这一步】verdict=incremental 只回答"有新东西"，回答不了"新在哪"。
    用户的目标是「只提取不重复的部分」，所以对比之后必须产出**增量清单**，
    否则重写时还是把整篇外来内容塞进自家技能里（等于又抄了一遍）。

    口径（确定性、不依赖向量）：
      · 逐条取 steps（没有 steps 就退回 trigger）；
      · 条目里的词有多少比例已被旧正文覆盖（containment 的反向用法）；
      · ≥ threshold ⇒ 判为「旧能力已讲过」，否则算新增。
    """
    items = [str(s).strip() for s in (new_facets.get("steps") or []) if str(s).strip()]
    source = "steps"
    if not items:
        items = [str(s).strip() for s in (new_facets.get("trigger") or []) if str(s).strip()]
        source = "trigger"
    fresh: List[str] = []
    repeated: List[str] = []
    for item in items:
        score = containment(item, old_text)
        (repeated if score >= threshold else fresh).append(item)
    total = len(items)
    return {
        "source": source,
        "new": fresh,
        "repeated": repeated,
        "coverage": round(len(repeated) / total, 3) if total else 0.0,
        "compared_points": total,
    }


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
        # 原文只在内存里用（算增量），**不随 candidates 序列化出去**（见 compare_with_repo）
        facets["_text"] = text
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
                      "verdict": verdict_for(scores, new_facets, old),
                      "_text": old.get("_text", "")})
    cands.sort(key=lambda c: max(c["scores"].values()), reverse=True)
    best = cands[0] if cands else None
    overall = best["verdict"] if best else "distinct"
    delta = delta_against(new_facets, str(best.get("_text") or "")) if best else None
    # 原文只在内存里参与算分/算增量：**不随响应出去**（plan/API 会整段序列化）
    for cand in cands:
        cand.pop("_text", None)
    if best is not None:
        best.pop("_text", None)
    return {"candidates": cands[:top], "best": best, "verdict": overall, "delta": delta}
