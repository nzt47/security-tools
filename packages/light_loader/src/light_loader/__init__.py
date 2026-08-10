"""light_loader — 知识库轻量检测视图加载器（P0 内存优化 · 独立 pip 包）。

审计五类检测（孤儿/断链/index 漂移/过期/未裁决矛盾）只需要每张卡六个字段：
slug / status / type / date / links / contradictions。本包只解析这六字段，
丢弃正文/insight 等大字段（单卡内存降 5~10 倍），并提供：
- 串行 / 线程池并行（parallel=True）两种扫描模式；
- 并行结果与串行**顺序完全一致**（类型目录序 + 组内 slug 字典序，
  数千张损坏卡并存时保序契约已由压力脚本验证）。

零依赖（仅标准库 + PyYAML），不 import 任何项目模块，可直接在
其他项目中复用。

公开 API：
    CardLight            # 检测六字段视图（dataclass）
    DEFAULT_TYPE_DIRS    # 默认 wiki 类型目录（concepts/entities/insights）
    parse_light(text)    # 单文件 frontmatter 文本 → CardLight（损坏抛 ValueError）
    scan_light_cards(wiki_root, *, type_dirs=..., parallel=False)  # 全量扫描
"""

from .core import CardLight, DEFAULT_TYPE_DIRS, parse_light, scan_light_cards

__version__ = "0.1.0"
__all__ = ["CardLight", "DEFAULT_TYPE_DIRS", "parse_light", "scan_light_cards"]
