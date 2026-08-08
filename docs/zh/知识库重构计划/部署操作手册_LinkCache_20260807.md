# LinkCache 生产部署操作手册

> 适用范围：知识检索整合（任务4）双链扩展性能优化组件在生产环境的部署、监控与运维。
> 关联文档：[双链扩展性能压测报告_20260807.md](./双链扩展性能压测报告_20260807.md)

## 1. 组件总览

| 组件 | 位置（agent 内） | 独立 PyPI 包 | 职责 |
|---|---|---|---|
| `LinkCache` | `agent/knowledge/link_cache.py` | `yunshu-cache-tools` | 预计算卡片双链目标为内存 slug，热路径零文件 I/O |
| `PeriodicSampler` | `agent/utils/periodic_sampler.py` | `yunshu-cache-tools` | 按调用次数周期确定性采样（耗时日志降噪） |

**关键契约（【不易】）**：

- `LinkCache` 是**快照式缓存**：构造期一次性解析全部卡的 links；断链 / `archives/` 归档 / 损坏 / 快照外新增目标一律解析为 `None`，与实时 `resolve_link + CardStore.get` 容错语义完全等价。
- 快照语义与 `KnowledgeSearch` 的 `_cards` / `_bm25` 索引**同生命周期**：写库后重建 searcher（`_build_index`）即整体刷新，无增量失效通知。
- `PeriodicSampler`：`rate=1.0` 全量；`0<rate<1` 每 `round(1/rate)` 次调用输出 1 条；越界自动钳制到 `[1e-6, 1.0]`；`itertools.count` C 原子计数，多线程安全。

## 2. LinkCache 初始化时机（部署重点）

### 2.1 生命周期接线

```
KnowledgeSearch.__init__()
  ├─ self._timing_sampler = PeriodicSampler(timing_sample_rate)
  ├─ self._link_cache = LinkCache({})      # 空占位，防未构建即访问
  └─ self._build_index()
       ├─ 加载 _cards / _bm25 索引（快照）
       └─ self._link_cache = LinkCache(self._cards)   # 与索引同快照重建
```

### 2.2 初始化时机规则

| 场景 | 时机 | 说明 |
|---|---|---|
| 服务启动 | 构造 `KnowledgeSearch` 时（`_build_index` 内） | 一次性预计算，10 卡 <1ms，万卡级亚秒 |
| 写路径之后 | **必须重建 searcher**（重新 `_build_index` / 重新构造 `KnowledgeSearch`） | 否则新增/修改卡的 links 不入缓存（快照语义，与索引一致） |
| 热加载/热更新 | 复用同一套快照刷新入口 | `LinkCache` 与 `_cards`/`_bm25` 同源同生命周期，禁止单独局部刷新缓存 |

> 【易踩坑】只更新 `_link_cache` 而不重建 `_cards`/`_bm25` 会导致三路索引不一致。
> 所有索引必须通过同一个 `_build_index()` 入口整体刷新。

### 2.3 采样率配置

- 环境变量：`KNOWLEDGE_TIMING_SAMPLE_RATE`（默认 `0.1`，即每 10 次 search 输出 1 条 `search_stage_timing` 日志）。
- 构造参数：`KnowledgeSearch(..., timing_sample_rate=...)` 优先于环境变量。
- 生产建议：默认 `0.1` 足够定位瓶颈；故障排查时可临时设 `1.0` 全量采集（压测/诊断脚本已按此实践）。

## 3. 内存占用估算

### 3.1 估算模型（64 位 CPython 保守上界）

```
bytes ≈ 卡片数 × 120 + links 总数 × 120
```

分量说明：

| 分量 | 字节/条 | 覆盖内容 |
|---|---|---|
| 每卡固定开销 | ~120 B | dict 条目 + list 头（56B）+ 结构指针 |
| 每条链接 | ~120 B | 2 元 tuple（56B）+ 目标字符串（保守含全量） |

### 3.2 估算表

| 卡片数 | 平均 links/卡 | links 总数 | 估算内存 | 占用 512MB 堆比例 |
|---|---|---|---|---|
| 1,000 | 3 | 3,000 | ~0.5 MB | 0.1% |
| 10,000 | 3 | 30,000 | ~4.8 MB | 0.9% |
| 100,000 | 5 | 500,000 | ~72 MB | 14% |
| 1,000,000 | 5 | 5,000,000 | ~720 MB | 141%（不可接受） |

### 3.3 实测口径

监控脚本采用**递归深估算**（`estimate_deep_size`，共享引用不重复计数）与模型估算取**较大值**作为告警依据（宁可多算不少算）。两者应同数量级；若深估算显著低于模型值，多为目标字符串与输入卡共享引用（属正常，模型为保守上界）。

## 4. 监控与告警

监控脚本：[scripts/monitor_link_cache_memory.py](../../../scripts/monitor_link_cache_memory.py)

```bash
# 单次估算（内置 mock 数据演示，可用于 smoke test）
python scripts/monitor_link_cache_memory.py --once --json

# 周期监控真实知识库（每 60s 采样，阈值 256MB）
python scripts/monitor_link_cache_memory.py --cards-dir knowledge/wiki --interval 60 --threshold-mb 256

# 外部实测模式（监控服务进程 RSS 增量，适合已部署服务）
python scripts/monitor_link_cache_memory.py --pid <服务PID> --threshold-mb 512 --interval 60

# 结构化输出对接 Prometheus 文本采集 / 告警平台
python scripts/monitor_link_cache_memory.py --once --json
```

**阈值建议**：

| 场景 | 建议阈值 | 说明 |
|---|---|---|
| LinkCache 自身内存（进程内估算法） | ≤ 256 MB | 对应约 200 万条链接，远超当前数据规模 |
| 进程 RSS 增量（外部实测法） | ≤ 512 MB | 进程内还含 BM25/向量等索引，需放宽 |
| 告警触发 | 退出码 1 + stderr `ALERT` 行 | 可被 cron/supervisor/告警网关消费 |

## 5. 部署检查清单

- [ ] 确认采样率配置（`KNOWLEDGE_TIMING_SAMPLE_RATE` 或构造参数）已设置，默认 `0.1`
- [ ] 写库操作后触发 searcher 重建（`_build_index`），而非局部刷新缓存
- [ ] 上线前用监控脚本 `--once --json` 实测缓存内存，确认低于阈值
- [ ] 周期监控任务已接入（cron / systemd timer），告警出口已验证
- [ ] 压测报告中的基线（QPS 5811 / P99 0.489ms）可复测对比

## 6. 回滚方案

- 代码回滚：`LinkCache` 依赖全部收敛在 `KnowledgeSearch` 内部，回滚 search 模块即回退到逐条 `resolve_link` 实时解析（性能劣化但语义等价，检索结果不变）。
- 缓存异常兜底：`expanded_links(seed)` 对未知 seed 返回 `[]`（等价无扩展），不会抛异常；即使缓存损坏，检索仍走 BM25 + 向量两路，不中断服务。

## 7. 常见问题（FAQ）

| 现象 | 原因 | 处理 |
|---|---|---|
| 写库后查询结果未含新卡 | 快照语义：未重建索引 | 写后调用 `_build_index`（或重建 searcher） |
| 缓存内存估算超预期 | 模型为保守上界；或数据规模增长 | 用 `--json` 看 deep_mb 实测值，按实测调整阈值 |
| 采样日志过密/过疏 | 采样率配置不当 | 调 `KNOWLEDGE_TIMING_SAMPLE_RATE`，诊断期可设 1.0 |
| 监控退出码 2 | 参数错误或数据加载失败 | 检查 `--cards-dir` / `--pid` / psutil 依赖 |

## 8. 独立 PyPI 包（其他项目引用）

`LinkCache` / `PeriodicSampler` 已提取为独立包 `yunshu-cache-tools`（`packages/yunshu_cache_tools/`），零第三方依赖，其他项目可直接：

```bash
pip install yunshu-cache-tools
```

行为等价由 `tests/unit/test_cache_tools_package_parity.py` 与 agent 内部实现锁一致（漂移即 CI 失败）。
