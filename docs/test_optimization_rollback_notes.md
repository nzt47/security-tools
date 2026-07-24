# 测试优化 Commit 回滚备注

> commit: `ee14e4fd`
> 分支: `feature/tlm-step3-vectorstore-sqlite-vec`
> 文件: `tests/unit/test_task_scheduler_comprehensive.py` (+26 -6)
> 日期: 2026-07-15

## 变更详情（按回滚优先级排序）

### 变更 1: check_interval 缩短（回滚风险: 低）

| 项目 | 内容 |
|------|------|
| 位置 | L693, L701, L703, L711（4 处） |
| 原始 | `scheduler.start_daemon(check_interval=1)` |
| 修改 | `scheduler.start_daemon(check_interval=0.05)` |
| 原因 | daemon 线程 `time.sleep(check_interval)` 无法被 `stop()` 立即唤醒，1 秒间隔导致每个测试至少阻塞 1s |
| 影响 | 3.00s → 0.03s（3 个测试各 1.00s → 各 0.01s） |
| 回滚 | 全局替换 `check_interval=0.05` → `check_interval=1` |

### 变更 2: mock psutil.cpu_percent（回滚风险: 低）

| 项目 | 内容 |
|------|------|
| 位置 | L824-828（`test_perform_heartbeat_check_basic`） |
| 原始 | `result = perform_heartbeat_check(None)` |
| 修改 | `with mock.patch('psutil.cpu_percent', return_value=42.0):`<br>`    result = perform_heartbeat_check(None)` |
| 原因 | `psutil.cpu_percent(interval=1)` 阻塞 1 秒采样真实 CPU，测试只需验证返回结构 |
| 影响 | 1.01s → <0.01s |
| 回滚 | 删除 `with mock.patch(...)` 缩进层，恢复单行直接调用 |

### 变更 3: cProfile 诊断代码（回滚风险: 无）

| 项目 | 内容 |
|------|------|
| 位置 | L789-809（`test_generate_weekly_report_no_exception`） |
| 原始 | `generate_weekly_report()`（单行） |
| 修改 | `cProfile.Profile()` + `enable/disable` + `print` top 30 |
| 原因 | 诊断 14s 瓶颈根因，定位到 `sentence_transformers` 导入链 |
| 影响 | 无性能影响（cProfile 开销可忽略） |
| 回滚 | 替换为原始单行 `generate_weekly_report()` |

## cache 部分说明

`cache_size=100` 优化位于 `tests/performance/test_vector_store_performance.py`，已在更早的 commit 中提交，不在本 commit 范围内。

| 项目 | 内容 |
|------|------|
| 位置 | `tests/performance/test_vector_store_performance.py` L75-80 |
| 变更 | VectorStore 构造时添加 `cache_size=100` |
| 原因 | 启用 LRU 查询缓存，反映生产环境真实配置 |
| 影响 | `test_search_performance`: 12.14s → 3.68s（-70%） |
| 回滚 | 删除 `cache_size=100` 参数 |

## 回滚命令

```bash
# 整个 commit 回滚
git revert ee14e4fd

# 单文件回滚
git checkout ee14e4fd~1 -- tests/unit/test_task_scheduler_comprehensive.py

# cache 优化回滚（需找到对应 commit）
git checkout <cache_commit>~1 -- tests/performance/test_vector_store_performance.py
```

## 回归测试结果

| 指标 | 优化前 | 优化后 | 改善 |
|------|--------|--------|------|
| 测试总数 | 231 | 240 | +9 |
| 通过率 | 100% | 100% | — |
| 总耗时 | 82.35s | 35.26s | **-57%** |
| 失败数 | 0 | 0 | — |
