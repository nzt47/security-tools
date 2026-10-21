"""适配器注册表（P0）

选择规则：**按内容特征打分，不按生态名硬编码**。所有适配器的 `sniff()` 都跑一遍，
取最高分；同分先看 `priority`（生态专用适配器可调高），再按注册顺序（先注册者优先）。
单个 `sniff()` 抛异常只记 0 分并留证，绝不阻断其它适配器 —— 一个坏适配器不该让
整个「吞噬」入口失效。

注册表本身是**可实例化**的：生产用 `get_registry()` 的单例，测试用
`AdapterRegistry([...])` 建独立实例，互不污染。这也是为什么本模块不把
"注册状态"藏进全局可变单例里做隐式共享。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .adapters import Adapter, builtin_adapters
from .models import IngestSource, UnsupportedSource

logger = logging.getLogger("agent.ingestion.registry")


@dataclass(frozen=True)
class Selection:
    """一次选择的结果（含完整打分表，供计划/排障展示）。"""

    adapter: Adapter
    score: int
    scores: Dict[str, int] = field(default_factory=dict)
    errors: Dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.adapter.name


class AdapterRegistry:
    """适配器注册表（顺序即同分时的次序）。"""

    def __init__(self, adapters: Optional[Iterable[Adapter]] = None) -> None:
        self._adapters: List[Adapter] = []
        for adapter in adapters or ():
            self.register(adapter)

    # ── 注册 ──────────────────────────────────────────────

    def register(self, adapter: Adapter) -> Adapter:
        name = str(getattr(adapter, "name", "") or "").strip()
        if not name:
            raise ValueError("适配器必须有非空 name")
        if not callable(getattr(adapter, "sniff", None)):
            raise ValueError(f"适配器 {name} 缺少 sniff()")
        if not callable(getattr(adapter, "extract", None)):
            raise ValueError(f"适配器 {name} 缺少 extract()")
        if any(existing.name == name for existing in self._adapters):
            raise ValueError(f"适配器名字重复: {name}")
        self._adapters.append(adapter)
        return adapter

    def unregister(self, name: str) -> bool:
        before = len(self._adapters)
        self._adapters = [a for a in self._adapters if a.name != name]
        return len(self._adapters) != before

    def adapters(self) -> List[Adapter]:
        """返回副本 —— 调用方不能绕过 register 改内部列表。"""
        return list(self._adapters)

    def names(self) -> List[str]:
        return [a.name for a in self._adapters]

    # ── 打分 / 选择 ───────────────────────────────────────

    def score(self, source: IngestSource) -> Dict[str, int]:
        return dict(self.evaluate(source)[0])

    def evaluate(self, source: IngestSource) -> tuple[Dict[str, int], Dict[str, str]]:
        """跑全部 sniff；返回 (分数表, 异常表)。"""
        scores: Dict[str, int] = {}
        errors: Dict[str, str] = {}
        for adapter in self._adapters:
            try:
                raw = adapter.sniff(source)
                value = int(raw or 0)
            except Exception as exc:  # noqa: BLE001 单适配器失败隔离
                errors[adapter.name] = f"{type(exc).__name__}: {exc}"
                value = 0
                logger.warning("[ingestion] adapter sniff failed name=%s: %s", adapter.name, exc)
            scores[adapter.name] = max(0, min(100, value))
        return scores, errors

    def select(self, source: IngestSource) -> Selection:
        scores, errors = self.evaluate(source)
        best: Optional[Adapter] = None
        best_score = 0
        for adapter in self._adapters:
            value = scores.get(adapter.name, 0)
            if value <= 0:
                continue
            # 严格大于才替换：同分时保留先注册者；priority 只在"更高分"之外做次判据
            if best is None or value > best_score or (
                value == best_score and int(getattr(adapter, "priority", 0)) > int(getattr(best, "priority", 0))
            ):
                best, best_score = adapter, value
        if best is None:
            detail = "没有适配器能识别该来源"
            if errors:
                detail += "；适配器异常：" + "、".join(f"{k}({v})" for k, v in sorted(errors.items()))
            raise UnsupportedSource(detail, detail=detail, scores=scores)
        return Selection(adapter=best, score=best_score, scores=scores, errors=errors)


_DEFAULT: Optional[AdapterRegistry] = None


def get_registry() -> AdapterRegistry:
    """生产默认注册表（进程内单例；首次调用时才构造内置适配器）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = AdapterRegistry(builtin_adapters())
    return _DEFAULT


def register(adapter: Adapter) -> Adapter:
    return get_registry().register(adapter)


def select(source: IngestSource) -> Selection:
    return get_registry().select(source)


def reset_registry(adapters: Optional[Iterable[Adapter]] = None) -> AdapterRegistry:
    """替换默认注册表（测试/热插拔用；生产启动阶段也可用）。"""
    global _DEFAULT
    _DEFAULT = AdapterRegistry(builtin_adapters() if adapters is None else adapters)
    return _DEFAULT


__all__ = [
    "AdapterRegistry",
    "Selection",
    "get_registry",
    "register",
    "select",
    "reset_registry",
]
