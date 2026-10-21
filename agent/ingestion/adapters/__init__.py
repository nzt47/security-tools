"""内置适配器清单（P0）

P1 会在这里追加 claude / openclaw / trae / zip / JS 插件包适配器；**新增适配器
只需要实现 `sniff()` + `extract()` 并在 `builtin_adapters()` 里登记**，不需要
改 registry/service/HTTP 层。生态名只作为"默认扫描目录"的提示，选择仍按内容
特征打分（见 `registry.AdapterRegistry.select`）。
"""

from __future__ import annotations

from typing import List

from .base import Adapter, ExtractedAsset
from .docs import DocsAdapter
from .skill_json import SkillJsonAdapter
from .skill_md import SkillMarkdownAdapter


def builtin_adapters() -> List[Adapter]:
    """返回**新实例**列表（registry 持有自己的注册表，测试可独立构造）。"""
    return [SkillJsonAdapter(), SkillMarkdownAdapter(), DocsAdapter()]


__all__ = [
    "Adapter",
    "ExtractedAsset",
    "DocsAdapter",
    "SkillJsonAdapter",
    "SkillMarkdownAdapter",
    "builtin_adapters",
]
