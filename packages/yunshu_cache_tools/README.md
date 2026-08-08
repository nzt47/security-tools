# yunshu-cache-tools

云枢通用缓存与采样工具集：**LinkCache**（预计算链接解析缓存）+ **PeriodicSampler**（周期确定性采样器）。

零第三方依赖，Python >= 3.10。

## 安装

```bash
pip install yunshu-cache-tools
# 或从源码
pip install -e packages/yunshu_cache_tools
```

## LinkCache：预计算链接解析缓存

把卡片 `links` 字段中的 `[[双链]]` 目标在构造期一次性解析为内存 slug，
热路径查询零文件 I/O。断链 / 归档 / 损坏目标一律解析为 `None`（容错语义）。

```python
from yunshu_cache_tools import LinkCache

cards = {card.slug: card for card in all_cards}   # 任意含 slug/links 的对象
cache = LinkCache(cards)

for target, resolved_slug in cache.expanded_links("seed-slug"):
    if resolved_slug is None:      # 断链/归档目标，跳过
        continue
    print(target, "->", resolved_slug)
```

内存占用估算（64 位 CPython，保守上界）：

    bytes ≈ 卡片数 × 120 + links 总数 × 120

## PeriodicSampler：周期确定性采样

按调用次数周期性采样，无随机性、结果可复现，多线程安全（`itertools.count` C 原子计数）。

```python
from yunshu_cache_tools import PeriodicSampler

sampler = PeriodicSampler(rate=0.1)   # 每 10 次调用输出 1 条
for ... in workload:
    if sampler.should_sample():
        log_timing(...)
```

`rate=1.0` 全量输出；`rate` 越界自动钳制到 `[1e-6, 1.0]`。

## License

MIT
