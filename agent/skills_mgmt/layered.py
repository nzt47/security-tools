# -*- coding: utf-8 -*-
"""技能**分层加载**（L0/L1/L2/L3）—— 对应 docs/rfc/技能转化器_2026-11-01.md §3。

为什么需要：一条 40 KB 的技能一次全塞进上下文，会挤掉别的内容；而多数时候模型只需要
「什么时候用 + 怎么做」这一层。分层后按需逐层展开，并且**每一层都要能被显式展开**（不许藏内容）。

| 层 | 内容 | 预算 |
|---|---|---|
| L0 | name + description + tags（索引里已常驻） | 200 token |
| L1 | CARD.md；没有就**降级**成「标题骨架 + 前 N 行」 | 2k token |
| L2 | skill.md 全文 | 20k token |
| L3 | scripts/ 清单（真执行才取内容） | 单文件 8k |

【降级兼容】老技能没有 CARD.md 也能只加载 L1 —— 不要求先补文件。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional

from agent.skills_mgmt.capability_cards import build_card
from agent.skills_mgmt.skill_md_schema import _split_front_matter

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPO = ROOT / "data" / "skills_repo"

#: 各层字符预算（按 1 token ≈ 3 字符粗算；超了截断并置 truncated=True）
BUDGET_CHARS = {
    "L0": 600,
    "L1": 6000,
    "L2": 60000,
    "L3": 24000,
}

#: L1 降级时取正文前多少行（再有标题骨架兜底）
_DERIVED_HEAD_LINES = 40

# ──────────────────────────────────────────────────────────────
#  C-8 L1 装配灰度（默认 full = 逐字旧行为；两个开关都登记在
#  agent/settings/registry.py，否则「开关零缺口」必红）
# ──────────────────────────────────────────────────────────────

#: 灰度白名单的默认值 = Owner 2026-11-06 点头的 5 条试点（都真有 CARD.md）。
#: 备注：交接文档里曾写成 pdf / git / run_lint / notify / read_pdf —— 后四个是**工具名**、
#: 不是技能 id（skills_repo 里没有对应目录），照抄会灰出 4 个空转项，故这里改成真实技能。
DEFAULT_L1_IDS = ("pdf", "gh-cli", "code-refactor", "engineering-test-delivery", "handoff")


def canary_ids() -> List[str]:
    """当前 L1 灰度白名单（逗号分隔技能 id）。

    空串 / 只有空白 ⇒ **空名单** ⇒ 即便 CP_SKILL_LAYER_MODE=l1 也不灰任何技能
    （等价回滚，比改 MODE 更细的刹车）。
    """
    raw = os.environ.get("CP_SKILL_LAYER_L1_IDS")
    if raw is None:
        return list(DEFAULT_L1_IDS)
    return [s.strip() for s in str(raw).split(",") if s.strip()]


def l1_enabled_for(skill_id: str) -> bool:
    """这次要不要**只注入 L1**：MODE=l1 且该技能在灰名单内。

    [不易] 只认 full / l1；其余值（含拼错、空串）一律当 full ——
    灰度开关的失效方向必须是「回到旧行为」，不能悄悄改变模型看到的内容。
    """
    mode = str(os.environ.get("CP_SKILL_LAYER_MODE", "full") or "").strip().lower()
    if mode != "l1":
        return False
    return skill_id in canary_ids()


def _skill_dir(skill_id: str, repo: Optional[Path] = None) -> Optional[Path]:
    base = Path(repo) if repo else DEFAULT_REPO
    if not base.is_dir():
        return None
    for child in base.iterdir():
        if child.is_dir() and child.name == skill_id:
            return child
    return None


def _headings(body: str) -> List[str]:
    out = []
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            out.append(s.lstrip("#").strip())
    return out


def _clip(text: str, layer: str) -> Dict[str, object]:
    budget = BUDGET_CHARS.get(layer, 6000)
    if len(text) <= budget:
        return {"text": text, "truncated": False, "chars": len(text)}
    return {"text": text[:budget].rstrip() + "\n\n…（已截断，展开下一层看全文）",
            "truncated": True, "chars": len(text)}


def _render_card(card: Dict[str, object]) -> str:
    """把能力卡渲染成 L1 文本（与能力卡页**同一份派生**，只是换了排版）。"""
    lines = ["# %s" % (card.get("name") or card.get("skill_id") or "")]
    summary = str(card.get("summary") or "").strip()
    if summary:
        lines += ["", summary]
    for key, label in (("trigger", "何时使用"), ("steps", "步骤"),
                       ("params", "参数"), ("guard", "约束")):
        items = card.get(key) or []
        if items:
            lines += ["", "## %s" % label]
            lines += ["- %s" % str(i).strip() for i in items if str(i).strip()]
    example = str(card.get("example") or "").strip()
    if example:
        lines += ["", "## 示例", example]
    sections = card.get("sections") or []
    if sections:
        lines += ["", "## 目录", " › ".join(str(s) for s in sections)]
    return "\n".join(lines).strip()


def load_layer(skill_id: str, layer: str = "L1", repo: Optional[Path] = None) -> Dict[str, object]:
    """取某一层的文本。返回 {ok, skill_id, layer, source, text, truncated, chars}。

    source: card（有 CARD.md）/ derived（从 skill.md 降级）/ full / meta / scripts。
    """
    d = _skill_dir(skill_id, repo)
    if d is None:
        return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "技能不存在"}
    md = d / "skill.md"
    if not md.is_file():
        return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "缺少 skill.md"}
    raw = md.read_text(encoding="utf-8", errors="replace")
    meta, body, _warnings = _split_front_matter(raw)
    meta = meta or {}

    if layer == "L0":
        text = "%s\n%s" % (meta.get("name") or skill_id, (meta.get("description_zh")
                                    or meta.get("description") or ""))
        return {"ok": True, "skill_id": skill_id, "layer": "L0", "source": "meta",
                **_clip(text, "L0")}

    if layer == "L1":
        card = d / "CARD.md"
        if card.is_file():
            return {"ok": True, "skill_id": skill_id, "layer": "L1", "source": "card",
                    **_clip(card.read_text(encoding="utf-8", errors="replace"), "L1")}
        # 降级：与「技能中心 → 能力卡」**同一份派生**（五面卡片），不再用「前 40 行」——
        # 同样是 L1 预算，五面卡片保的是"怎么用"，前 40 行常常只保住了开头。
        card = build_card(d)
        if card:
            return {"ok": True, "skill_id": skill_id, "layer": "L1", "source": "derived",
                    **_clip(_render_card(card), "L1")}
        head = "\n".join(body.splitlines()[:_DERIVED_HEAD_LINES]).strip()
        skeleton = " › ".join(_headings(body)[:12])
        text = "%s\n\n[目录] %s\n\n[正文前 %d 行]\n%s" % (
            meta.get("name") or skill_id, skeleton, _DERIVED_HEAD_LINES, head)
        return {"ok": True, "skill_id": skill_id, "layer": "L1", "source": "derived",
                **_clip(text, "L1")}

    if layer == "L2":
        return {"ok": True, "skill_id": skill_id, "layer": "L2", "source": "full",
                **_clip(body, "L2")}

    if layer == "L3":
        scripts = []
        sdir = d / "scripts"
        if sdir.is_dir():
            scripts = sorted(p.name for p in sdir.iterdir() if p.is_file())
        text = "脚本：%s" % (", ".join(scripts) if scripts else "（无）")
        return {"ok": True, "skill_id": skill_id, "layer": "L3", "source": "scripts",
                "scripts": scripts, **_clip(text, "L3")}

    return {"ok": False, "skill_id": skill_id, "layer": layer, "error": "未知层：%s" % layer}
