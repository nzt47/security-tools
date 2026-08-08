"""链接解析缓存：预计算卡片双链目标为内存 slug，查询零文件 I/O。

Why 存在：检索热路径（双链扩展）此前每次逐条 resolve_link→CardStore.get
（文件 I/O + 读锁等待），实测占总耗时 99%+；本缓存构造期一次性把每卡 links
解析为内存 slug，查询纯内存，零 I/O、零锁等待。快照式语义：构造后写入的卡
不入缓存，重建缓存（重新构造实例）即刷新。

本文件是独立 PyPI 包 yunshu-cache-tools 的实现，与 agent 内部实现保持
行为等价（由 tests/unit/test_cache_tools_package_parity.py 锁一致），
但零外部依赖：卡片采用 CardLike 鸭子类型，解析器 resolve_slug 内聚实现，
不引用 agent.* 任何模块。

语义契约（守【不易】）：断链 / archives 归档 / 损坏 / 快照外新增卡 → 解析为
None，与实时 resolve_link + CardStore.get 的容错语义完全等价。
"""

from __future__ import annotations

from typing import Callable, List, Mapping, Optional, Protocol, Tuple

ARCHIVES_PREFIX = "archives/"


class CardLike(Protocol):
    """卡片最小结构（鸭子类型）。

    兼容 agent.knowledge.schema.Card 以及任何含 slug/links 的对象，
    使本包可被其他项目直接复用而无需感知云枢知识层实现。
    """

    slug: str
    links: List[str]


def resolve_slug(
    slug: str,
    getter: Callable[[str], Optional[CardLike]],
) -> Optional[CardLike]:
    """解析链接；目标不存在或异常返回 None（断链容错），不抛异常。

    `getter` 鸭子类型（仅调用 getter(slug)），语义与 agent.knowledge.links
    的 resolve_link 对内存存储的行为一致。
    """
    try:
        return getter(slug)
    except Exception:
        return None


class _MemoryStore:
    """内存卡片库（鸭子类型存储，供 resolve_slug 零文件 I/O 解析）。

    - 纯 slug：命中内存卡（快照覆盖全库）；
    - `archives/...` / 断链 / 构造后新增的卡：不在快照 → None，
      与真实存储的容错语义一致。
    """

    def __init__(self, cards: Mapping[str, CardLike]) -> None:
        self._cards = cards

    def get(self, slug: str) -> Optional[CardLike]:
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

    memory 估算（64 位 CPython，保守上界）:
        bytes ≈ n_cards * 120 + total_links * 120
        其中 n_cards 为快照卡片数，total_links 为全部卡片 links 总数。
    """

    def __init__(self, cards: Mapping[str, CardLike]) -> None:
        mem = _MemoryStore(cards)
        self._cache: dict[str, list[Tuple[str, Optional[str]]]] = {}
        for card in cards.values():
            resolved: list[Tuple[str, Optional[str]]] = []
            for target in card.links:
                hit = resolve_slug(target, mem.get)
                resolved.append((target, hit.slug if hit else None))
            self._cache[card.slug] = resolved

    def expanded_links(self, seed: str) -> List[Tuple[str, Optional[str]]]:
        """返回 seed 卡链接的预解析结果 [(原始 target, 解析后 slug | None)]。"""
        return self._cache.get(seed, [])

    @property
    def size(self) -> int:
        """已缓存的卡片数。"""
        return len(self._cache)

    @property
    def total_links(self) -> int:
        """缓存的链接条数（全部卡的 links 总数）。"""
        return sum(len(v) for v in self._cache.values())


__all__ = ["LinkCache", "resolve_slug", "CardLike", "ARCHIVES_PREFIX"]
