# store.list() 内存缓存增量同步优化方案

- 日期：2026-08-09
- 涉及模块：`agent/knowledge/card.py`（CardStore）
- 配套脚本：`scripts/probe_list_100k_perf.py`（瓶颈探测）、`scripts/bench_list_cache_compare.py`（前后对比）
- 回归测试：`tests/performance/test_knowledge_link_perf.py`（CI 自动执行）、`tests/unit/test_knowledge_card.py`

## 一、背景：10 万卡瓶颈定位

对 10 万卡知识库实测（`scripts/probe_list_100k_perf.py --cards 100000`）：

| 路径 | 耗时 | 结论 |
|---|---|---|
| `list()` 无缓存冷读 | ~69s | 主瓶颈 |
| YAML 解析（推算 10 万卡） | ~51s | 占 **74%**，绝对主瓶颈 |
| 文件 IO | ~9.5s | 占 14% |
| `list(use_cache=True)` 缓存命中 | ~170ms | 几乎全是指纹扫描（150ms） |
| `_fingerprint()` 全量扫描 | ~150ms | scandir + stat 10 万文件 |

结论：YAML 解析成本不可压缩（frontmatter 是任意 YAML，不能改解析策略）；
内存缓存命中已是 170ms 量级。真正的痛点是**写后查询必须全量重载**——
create/update/delete 后首次 `list(use_cache=True)` 仍要重新读盘解析（69s）。

## 二、方案设计：写路径增量同步

在既有"指纹 + 全量缓存"基础上，增加写路径增量同步：

- 新增 `_sync_list_cache()`：create/update/delete 成功写盘后，同步更新内存缓存
  （列表增删 + 按类型目录序重排）与指纹基线（单文件 stat 条目增删），
  保证「缓存内容 ↔ 指纹」一致，写后查询直接命中，不再全量重载。
- 新增 `_invalidate_list_cache()`：delete_many / import_from_dir 批量操作后整体
  失效（逐张增量同步为 O(K·N)，批量场景失效一次更优），下次 list 全量重载。
- `list(use_cache=True)` 命中逻辑微调：缓存未加载时跳过指纹扫描直接重载
  （10 万卡省一次 scandir+stat），加载后重建指纹基线。

### 关键实现（card.py）

```python
def _sync_list_cache(self, *, added=None, removed=None,
                     added_fp=None, removed_fp=None) -> None:
    if self._list_cache is None:
        return  # 缓存未加载：下次 list 自动全量加载，无需同步
    # 1) 缓存同步：移除 removed → 去重加入 added → 按 (类型目录序, slug) 排序
    # 2) 指纹同步：set 差并 removed_fp / added_fp 后重新排序
    # 安全边界：缓存与指纹同函数同步；任一遗漏导致指纹不一致，
    # 下次命中会被全量指纹比较发现 → 自动重载（只慢不坏，绝不返回陈旧数据）
```

挂载点：create（新增卡）、update（含 type 迁移双指纹：旧目录移除 + 新目录加入）、
delete（unlink 前记录旧指纹）。

## 三、契约变更明细（重点）

### 3.1 变更点：写后查询不再全量重载

| 操作 | 优化前 | 优化后 |
|---|---|---|
| `create()` 后 `list(use_cache=True)` | 指纹变化 → 全量重载（10 万卡 ~69s） | 缓存增量同步 → 命中（~30ms） |
| `update()` 后 `list(use_cache=True)` | 全量重载 | 命中（type 迁移双指纹同步） |
| `delete()` 后 `list(use_cache=True)` | 全量重载 | 命中（缓存即时移除） |
| `delete_many()` 后 `list(use_cache=True)` | 全量重载 | 整体失效 → 全量重载（不变） |
| `import_from_dir()` 后 `list(use_cache=True)` | 全量重载 | 整体失效 → 全量重载（不变） |

### 3.2 不变的契约（【不易】）

1. `list()` 默认 `use_cache=False` 语义不变：每次实时读盘。
2. 进程外（外部）修改文件后，`list(use_cache=True)` 仍靠**全量指纹比较**检测
   → 自动重载，不返回陈旧数据（`test_list_cache_invalidation_on_modify` 保持）。
3. 缓存仅本次进程内有效。
4. 缓存返回顺序与磁盘读一致：按类型目录序 + 组内 slug 字典序。
5. 写操作串行化（读写锁）与入链索引同步逻辑不变。

### 3.3 对调用方的影响

- 语义上无破坏性变更：写后读到的结果与优化前一致（只是不再重载）。
- 性能边界：单卡写（create/update/delete）后查询 O(log N) 级命中；
  批量写（delete_many/import_from_dir）后首次查询为 O(N) 全量重载
  （一次性成本，与数据规模正相关）。
- 内存占用：缓存持有全量 Card 对象（10 万卡约数百 MB，进程内有效）。

## 四、日志埋点（DEBUG/INFO）

| 位置 | 级别 | 内容 |
|---|---|---|
| `create` / `update` 末尾 | INFO | `[缓存同步]: slug=... 指纹stat+增量同步=Xms 全方法=Yms` |
| `_sync_list_cache` 内部 | DEBUG | 缓存同步耗时 / 指纹更新耗时 / 合计 |
| `_fp_entry` | DEBUG | 单文件 stat 耗时 |

生产环境默认 INFO 可见写后同步汇总；DEBUG 可查看细分耗时（性能诊断）。

## 五、验证数据

2 万卡对比（`scripts/bench_list_cache_compare.py --cards 20000`）：

```
[A] 无缓存冷读 list():               13162ms
[C] use_cache 缓存命中:                 30ms   (≈434x vs A)
[D] 写后首查 - 优化后(增量同步):        33ms
[D] 写后首查 - 优化前(失效重载):     13066ms   (优化后 ≈402x)
[E] 一致性校验(随机写60次后对比):     PASS
```

10 万卡结果见同脚本 `--cards 100000` 输出（A-D 场景）。

测试覆盖：
- `tests/performance/test_knowledge_link_perf.py`：8 项（含 create/update/delete
  增量同步不触发重载、type 迁移双指纹、delete_many 失效重载、外部修改检测）
- `tests/unit/test_knowledge_card.py`：94 项全绿（`test_list_cache_invalidates_on_create`
  断言由"失效重载 2 次"更新为"增量同步 1 次"，正确性契约不变）

## 六、边界与风险

- **指纹遗漏风险**：增量同步只保证内部写路径；若同步遗漏，最坏结果是下次
  命中时全量指纹比较失败 → 自动全量重载（性能回退，非正确性问题）。
- **外部修改与内部写混合**：外部修改 → 指纹不一致 → 重载，行为正确。
- **mtime 人为还原**：极端场景（`list()` docstring 已声明），缓存可能漏检，
  可接受。
