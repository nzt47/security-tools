# light_loader 独立 pip 包安装说明

- 生成时间：2026-08-11
- 包位置：`packages/light_loader/`
- 发行名：`light-loader`（模块名：`light_loader`）

## 一、背景

`agent/knowledge/light_loader.py` 的轻量检测视图加载逻辑（P0 内存优化：
只解析检测六字段，丢弃正文/insight，单卡内存降 5~10 倍）已提取为独立
pip 包，供其他项目零依赖复用。

## 二、目录结构

```
packages/light_loader/
├── pyproject.toml            # 构建配置（setuptools，requires-python>=3.10）
├── README.md                 # 包内说明
└── src/light_loader/
    ├── __init__.py           # 公开 API 导出（__version__ = 0.1.0）
    └── core.py               # 核心逻辑（与 agent/knowledge/light_loader.py 同源）
```

## 三、安装

```bash
# 方式一：直接安装（生产）
pip install ./packages/light_loader

# 方式二：可编辑安装（开发，改动即时生效）
pip install -e ./packages/light_loader
```

依赖：PyYAML>=6.0（自动安装；libyaml C 扩展为可选加速，缺失时自动回退
纯 Python SafeLoader）。

## 四、验证安装

```bash
python -c "import light_loader; print(light_loader.__version__)"
# 期望输出: 0.1.0
```

## 五、用法

```python
from light_loader import scan_light_cards, parse_light, CardLight

# 全量扫描（parallel=True 线程池并发，结果与串行顺序完全一致）
cards = scan_light_cards("path/to/wiki", parallel=True)
for c in cards:                 # type: CardLight
    print(c.slug, c.status, c.links)

# 单文件解析（损坏卡抛 ValueError，调用方按需捕获跳过）
light = parse_light(open("a.md", encoding="utf-8").read())

# 自定义类型目录顺序
cards = scan_light_cards("path/to/wiki", type_dirs=("concepts", "insights"))
```

公开 API：

| 符号 | 说明 |
|---|---|
| `CardLight` | 检测六字段视图（slug/status/type/date/links/contradictions） |
| `DEFAULT_TYPE_DIRS` | 默认类型目录（concepts/entities/insights） |
| `parse_light(md_text)` | 单文件 frontmatter → CardLight；解析失败抛 ValueError |
| `scan_light_cards(root, *, type_dirs, parallel)` | 全量扫描（损坏卡跳过、并行保序） |

## 六、行为契约（不易）

- 只读扫描，不修改任何文件；
- 排序：按 type_dirs 顺序 + 组内 slug 字典序（与 CardStore.list 一致）；
- 损坏卡（无 frontmatter / YAML 语法错误）跳过，不阻断全库列举；
- `parallel=True` 时线程池并发（IO 密集提速），`ex.map` 按提交顺序收集，
  结果与串行完全一致——该保序契约已在 5000 卡（损坏率 60%）极端场景下
  验证（见 `scripts/dev/stress_light_loader_parallel.py`）。

## 七、与仓库内模块的关系

| 入口 | 用途 | 同步要求 |
|---|---|---|
| `agent/knowledge/light_loader.py` | agent 内部使用（card.py/list_light 依赖） | 与包同源 |
| `packages/light_loader/src/light_loader/core.py` | 独立发布 / 其他项目复用 | 与 vendored 同源 |

两者为同一份源码的双入口：修改任一侧后**必须同步另一侧**。agent 内部
保持零依赖（不因打包引入外部安装要求）；外部项目通过 `pip install light-loader`
获得同款能力。

## 八、相关文档

- [CLI 日志参数文档](knowledge_cli_logging_modes_20260811.md)
- [性能分析报告](knowledge_audit_perf_scale_20260811.md)
- [架构优化建议](knowledge_audit_architecture_optimization_20260811.md)
