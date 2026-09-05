"""素材读取 — 从知识库 wiki / 指定路径 / 目录收集蒸馏输入素材。

读取策略：
    1. query 模式：走 KnowledgeSearch（BM25 混合检索）召回 wiki 成品卡；
    2. paths 模式：直接读指定的 .md/.txt 文件或目录（递归），
       支持 .superpowers/skills/**/SKILL.md 等外部语料；
    两种模式可同时使用，结果合并去重（按 source_ref）。

约束：
    - 只读，绝不修改任何素材文件；
    - 单条素材正文截断 MAX_SOURCE_CHARS（防 prompt 爆炸，与 knowledge/distill 对齐）；
    - wiki 检索无命中不抛异常，返回空列表（由上层降级提示）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Optional

from agent.knowledge.search import KnowledgeSearch
from agent.process_distill.models import DistillMaterial
from agent.process_distill.prompts import MAX_SOURCE_CHARS

logger = logging.getLogger(__name__)

_KB_ROOT = Path(__file__).resolve().parents[2] / "knowledge"

# 支持直接读取的扩展名（.md / SKILL.md / 纯文本）
_TEXT_EXTS = {".md", ".markdown", ".txt", ".text"}


# ═══════════════════════════════════════════════════════════════
#  单条素材读取（路径模式）
# ═══════════════════════════════════════════════════════════════

def _front_matter_meta(content: str) -> Dict[str, str]:
    """解析 markdown front matter 的 name/title/description（SKILL.md 等）。

    只解析首块 --- ... --- 内的 key: value 行；无 front matter 返回空 dict。
    返回键小写。
    """
    out: Dict[str, str] = {}
    if not content.startswith("---"):
        return out
    end = content.find("\n---", 3)
    if end < 0:
        return out
    fm = content[3:end]
    for line in fm.splitlines():
        line = line.strip()
        if not line or ":" not in line or line.startswith("#"):
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        if key in ("name", "title", "description"):
            v = val.strip().strip("'\"")
            if v and not v.startswith("{"):  # 排除复杂值
                out[key] = v[:300]
    return out


def _material_from_file(path: Path, root: Optional[Path] = None) -> Optional[DistillMaterial]:
    """读一个文本文件为素材；非文本/空文件返回 None。"""
    try:
        if path.suffix.lower() not in _TEXT_EXTS:
            return None
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.warning("[PD] 素材读取失败 %s: %s", path, e)
        return None
    content = content.strip()
    if not content:
        return None
    if len(content) > MAX_SOURCE_CHARS:
        content = content[:MAX_SOURCE_CHARS] + "\n…[截断]"
    rel = path.relative_to(root) if root and path.is_relative_to(root) else path
    meta = _front_matter_meta(content)
    title = meta.get("name") or meta.get("title") or path.stem
    # id 用目录名（SKILL.md 场景取父目录名，避免全是 "skill"）
    base_id = (path.parent.name if path.name.lower() == "skill.md"
               else path.stem)
    return DistillMaterial(
        id=base_id[:80],
        title=title,
        content=content,
        source_ref=str(rel).replace("\\", "/"),
        kind="markdown" if path.suffix.lower() in (".md", ".markdown") else "text",
        description=meta.get("description", ""),
    )


def collect_from_paths(paths: Iterable[str]) -> List[DistillMaterial]:
    """按文件/目录路径收集素材（目录递归；去重按 source_ref）。"""
    out: List[DistillMaterial] = []
    seen: set[str] = set()
    for raw in paths or []:
        p = Path(raw)
        files: List[Path] = []
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files = sorted(
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in _TEXT_EXTS
            )
        else:
            logger.warning("[PD] 路径不存在，跳过: %s", raw)
            continue
        for f in files:
            m = _material_from_file(f, root=p if p.is_dir() else None)
            if m and m.source_ref not in seen:
                seen.add(m.source_ref)
                out.append(m)
    return out


# ═══════════════════════════════════════════════════════════════
#  知识库 wiki 检索（query 模式）
# ═══════════════════════════════════════════════════════════════

def collect_from_wiki(query: str, top_k: int = 5,
                      card_store=None, search: Optional[KnowledgeSearch] = None,
                      ) -> List[DistillMaterial]:
    """按关键词从知识库 wiki 召回成品卡为素材。

    card_store/search 可注入（测试用临时知识库）；默认从仓库 knowledge/wiki 构造。
    """
    if not query:
        return []
    try:
        if search is None:
            if card_store is None:
                from agent.knowledge.card import CardStore
                card_store = CardStore(str(_KB_ROOT / "wiki"))
            search = KnowledgeSearch(card_store)
        hits = search.search(query, top_k=max(1, int(top_k)))
    except Exception as e:  # noqa: BLE001  检索失败降级空结果，不阻断
        logger.warning("[PD] wiki 检索失败，返回空: %s", e)
        return []

    out: List[DistillMaterial] = []
    for hit in hits or []:
        slug = getattr(hit, "slug", "") or ""
        ref = getattr(hit, "source_ref", "") or ""
        # 优先取全文（CardStore.get），检索 hit 只带 snippet
        content = getattr(hit, "snippet", "") or ""
        try:
            card = card_store.get(slug)
            if card is not None and getattr(card, "content", ""):
                content = card.content
        except Exception:  # noqa: BLE001  取全文失败退回 snippet
            pass
        if not content:
            continue
        out.append(DistillMaterial(
            id=slug[:80],
            title=getattr(hit, "title", "") or slug,
            content=content[:MAX_SOURCE_CHARS],
            source_ref=ref or f"wiki/{slug}",
            kind="markdown",
        ))
    return out


# ═══════════════════════════════════════════════════════════════
#  组合入口
# ═══════════════════════════════════════════════════════════════

def collect_materials(*, query: str = "", paths: Optional[Iterable[str]] = None,
                      top_k: int = 5, card_store=None,
                      search=None) -> List[DistillMaterial]:
    """组合收集：query（wiki 检索）+ paths（文件/目录）→ 去重素材列表。"""
    mats: List[DistillMaterial] = []
    seen: set[str] = set()

    def _add(ms: List[DistillMaterial]) -> None:
        for m in ms or []:
            key = m.source_ref or m.id
            if key not in seen:
                seen.add(key)
                mats.append(m)

    if query:
        _add(collect_from_wiki(query, top_k=top_k,
                               card_store=card_store, search=search))
    if paths:
        _add(collect_from_paths(paths))
    return mats
