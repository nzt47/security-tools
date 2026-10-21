"""内置适配器清单（P0 通用 + P1 生态/包格式）

**新增适配器只需要实现 `sniff()` + `extract()` 并在 `builtin_adapters()` 里登记**，
不需要改 registry/service/HTTP 层。生态名只作为"默认扫描目录"的提示，选择仍按
内容特征打分（见 `registry.AdapterRegistry.select`）；没有生态证据时由通用
`skill_md` / `docs` 兜底。
"""

from __future__ import annotations

from typing import List

from .base import Adapter, ExtractedAsset
from .claude import ClaudeAdapter
from .clawhub import OpenClawAdapter, TraeAdapter
from .docs import DocsAdapter
from .js_plugin import JsPluginAdapter
from .skill_json import SkillJsonAdapter
from .skill_md import SkillMarkdownAdapter
from .zip import ZipAdapter


def builtin_adapters() -> List[Adapter]:
    """返回**新实例**列表（registry 持有自己的注册表，测试可独立构造）。"""
    return [
        SkillJsonAdapter(),
        SkillMarkdownAdapter(),
        DocsAdapter(),
        ZipAdapter(),
        JsPluginAdapter(),
        ClaudeAdapter(),
        OpenClawAdapter(),
        TraeAdapter(),
    ]


__all__ = [
    "Adapter",
    "ExtractedAsset",
    "ClaudeAdapter",
    "DocsAdapter",
    "JsPluginAdapter",
    "OpenClawAdapter",
    "SkillJsonAdapter",
    "SkillMarkdownAdapter",
    "TraeAdapter",
    "ZipAdapter",
    "builtin_adapters",
]
