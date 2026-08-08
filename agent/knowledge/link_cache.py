"""链接解析缓存：预计算卡片双链目标为内存 slug，查询零文件 I/O（任务4 性能优化）。

Why 存在：检索热路径（双链扩展）此前每次逐条 resolve_link→CardStore.get
（文件 I/O + 读锁等待），实测占总耗时 99%+；本缓存构造期一次性把每卡 links
解析为内存 slug，查询纯内存，零 I/O、零锁等待。快照式语义（与 KnowledgeSearch
的 _cards/_bm25 索引同待遇）：构造后写入的卡不入缓存，重建 searcher 即刷新。

语义契约（守【不易】）：断链 / archives 归档 / 损坏 / 快照外新增卡 → 解析为
None，与实时 resolve_link + CardStore.get 的容错语义完全等价（检索契约：
仅可索引目标纳入，其余一律跳过）。
"""

from __future__ import annotations

from typing import Mapping, Optional

from agent.knowledge.links import resolve_link
from agent.knowledge.schema import Card

ARCHIVES_PREFIX = "archives/"


class _MemoryStore:
    """内存卡片库（鸭子类型 CardStore.get，供 resolve_link 零文件 I/O 解析）。

    - 纯 slug：命中内存 wiki 卡（快照覆盖全库，与 store.get 的 wiki 查找等价）；
    - `archives/...` / 断链 / 损坏 / 构造后新增的卡：不在快照 → None，
      与真实 store 的容错语义一致。
    """

    def __init__(self, cards: Mapping[str, Card]) -> None:
        self._cards = cards

    def get(self, slug: str) -> Optional[Card]:
        return self._cards.get(slug)


class LinkCache:
    """预计算链接解析缓存。

    Usage:
        cache = LinkCache(cards_by_slug)
        for target, slug in cache.expanded_links(seed):
            if slug is None:   # 断链/归档目标，跳过
                ...
            if slug in seen:   # 已见（种子/已纳入），重复跳过
                ...
    """

    def __init__(self, cards: Mapping[str, Card]) -> None:
        mem = _MemoryStore(cards)
        self._cache: dict[str, list[tuple[str, Optional[str]]]] = {}
        for card in cards.values():
            resolved: list[tuple[str, Optional[str]]] = []
            for target in card.links:
                hit = resolve_link(target, mem)
                resolved.append((target, hit.slug if hit else None))
            self._cache[card.slug] = resolved

    def expanded_links(self, seed: str) -> list[tuple[str, Optional[str]]]:
        """返回 seed 卡链接的预解析结果 [(原始 target, 解析后 slug | None)]。"""
        return self._cache.get(seed, [])

    @property
    def size(self) -> int:
        """已缓存的卡片数。"""
        return len(self._cache)


__all__ = ["LinkCache", "ARCHIVES_PREFIX"]
