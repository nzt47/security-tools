# light-loader

知识库轻量检测视图加载器（P0 内存优化）——零依赖 frontmatter 扫描，支持保序并行读取。

审计类检测通常只需要每张卡六个字段（slug / status / type / date / links / contradictions）。
本包只解析这六字段、丢弃正文与 insight 等大字段（单卡内存降 5~10 倍），
并提供串行 / 线程池并行两种扫描模式，并行结果与串行顺序完全一致。

## 特性

- 零项目依赖（仅标准库 + PyYAML），不 import 任何宿主项目模块；
- 优先 libyaml C 扩展解析（约 7.6x 提速），无则自动回退纯 Python SafeLoader；
- 损坏卡（无 frontmatter / YAML 语法错误）跳过不阻断，数千张并存时保序；
- 排序契约：类型目录序 + 组内 slug 字典序（与 CardStore.list 一致）。

## 安装

```bash
pip install ./packages/light_loader     # 或 pip install -e ./packages/light_loader
```

## 用法

```python
from light_loader import CardLight, parse_light, scan_light_cards

# 全量扫描（并行保序）
cards = scan_light_cards("path/to/wiki", parallel=True)
for c in cards:                      # type: CardLight
    print(c.slug, c.status, c.links)

# 单文件解析
light = parse_light(open("a.md", encoding="utf-8").read())
```

## 公开 API

| 符号 | 说明 |
|---|---|
| `CardLight` | 检测六字段视图（dataclass） |
| `DEFAULT_TYPE_DIRS` | 默认类型目录（concepts/entities/insights） |
| `parse_light(md_text)` | 单文件 frontmatter → CardLight；损坏抛 ValueError |
| `scan_light_cards(root, *, type_dirs, parallel)` | 全量扫描列表 |

## 与仓库内模块的关系

本包与 `agent/knowledge/light_loader.py` 同源（仓库内 vendored 副本）。
修改任一侧后须同步另一侧。
