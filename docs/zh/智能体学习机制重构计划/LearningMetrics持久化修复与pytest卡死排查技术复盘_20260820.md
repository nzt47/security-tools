# LearningMetrics SQLite 持久化修复与 pytest 卡死排查技术复盘

> 关联模块：`agent/learning_metrics.py`（TASK-03 学习 KPI 聚合）
> 关联测试：`tests/unit/test_learning_metrics_persistence.py`
> 复盘日期：2026-08-20
> 相关提交：`3f37612d`（持久化实现，含根因备注）

---

## 1. 背景

TASK-08 报告披露 LearningMetrics 为**进程内内存聚合**（重启清零），"连续 4 周"触发条件需跨重启累积。据此实现可选 SQLite 持久化：内存聚合为主 + 事件行阈值批量 flush + 重启 `_load_from_db` 回填；默认关闭，不改变既有行为。

## 2. 问题一：AttributeError（初始化顺序 bug）

### 现象

构造带 `persistence` 的实例**必降级**（`_persistence=None`），重启恢复失效（`get_snapshot` 全 0）。

### 根因

`__init__` 中持久化启用块位于**内存聚合字段初始化之前**：

```python
self._lock = threading.RLock()
# ── SQLite 持久化 ──
if persistence and persistence.get("enabled"):
    ...
    self._init_db()   # ← 此处 _load_from_db 回填
# ── token 复用率 ──
self._tokens_saved = 0          # ← 字段在此之后才定义
self._total_interactions = 0
```

`_load_from_db()` 回填 `self._total_interactions += cnt` 时字段未定义 → `AttributeError` → 被 `__init__` 的 `except` 吞掉 → **静默降级为纯内存**（且无日志定位线索，因 warning 只显示异常名）。

### 修复

持久化启用块移至**全部内存字段初始化之后**（字段就绪后再建表 + 回填），I/O 仍全在锁外。注：属性声明（`_persistence/_pending/_db_lock`）保留在开头。

### 验证

- 构造无降级、无 AttributeError
- 写 3 交互 + flush → 新实例同路径重启 → interactions/hits/saved/qa 与写前**完全一致**
- python 直连 5 场景（批量/事务/双降级/重启恢复）全过

## 3. 问题二：pytest 测试"卡死"

### 现象

`pytest tests/unit/test_learning_metrics_persistence.py` 在 `collected N items` 后无输出，`--timeout=120` 无法中断，疑似无限卡死。

### 排查过程

| 步骤 | 实验 | 结果 | 结论 |
|---|---|---|---|
| 1 | 与 test_learning_metrics.py 一起跑（18 items） | 13/13 过，持久化 5 个卡 | 既有逻辑无回归；问题在持久化测试 |
| 2 | 单测 T2（事务性）`--timeout=30` | 仍卡 | 非特定用例 |
| 3 | 最小 probe（纯 `sqlite3` + `tmp_path`，无 LearningMetrics） | 2 passed in **70.02s** | **不是卡死，是极慢**（~35s/测试） |
| 4 | 无并行终端时跑持久化 5 测试（`--timeout=600`） | **5 passed in 2.31s** | 无并行干扰时正常且快 |
| 5 | 合并回归 18 items | **18 passed in 3.45s** | 全部通过 |

### 根因分析

1. **"卡死"实为"极慢 + 超时无法中断"**：本环境 `pytest-timeout` 用 thread 模式，对阻塞在系统调用/锁等待的主线程**无法中断**（watchdog 触发前无响应即挂起），`--timeout` 形同虚设 → 表现为"卡死"。
2. **慢的源头与环境相关**：probe（纯 sqlite+tmp_path）需 70s，而同样测试在无并行终端时仅数秒。指向**并行会话进程（app_server/fake redis/前端 dev 等）与本机 IO/锁竞争** + `tests/conftest.py` 的 session 级 `_safe_tmp_directory`（tempdir 重定向 `.pytest_tmp` + `_RetryTemporaryDirectory` 清理重试）放大了耗时。
3. **判定铁律**（沿用项目既有教训）：pytest 结果必须以**汇总行**（`=+ N passed`）为准，而非进程退出码或超时表现；`--timeout` 线程模式不可依赖。

### 结论

- 卡死**非代码缺陷**：持久化 5 测试在干净环境 2.31s 全过，合并 18/18 全过
- 建议：并行会话结束后再跑 pytest；CI 环境（无并行干扰）不受影响
- 复盘价值：后续遇到"pytest 卡死"先跑最小 probe 区分"环境极慢" vs "代码阻塞"，勿直接怀疑被测代码

## 4. 交付与验证汇总

| 项 | 结果 |
|---|---|
| 持久化实现 | `3f37612d`（9 个 `record_*` 接口不变；默认关闭；flush 单事务批量；双降级；重启恢复） |
| 测试 | `test_learning_metrics_persistence.py` 5 用例（批量阈值/事务性/初始化降级/落库降级/重启恢复） |
| pytest 回归 | 18 passed in 3.45s（既有 13 + 新增 5） |
| 远程 | origin/develop 与本地同步（0 ahead / 0 behind），3 个提交已推送 |

## 5. 遗留事项

- 持久化默认关闭（`persistence` 参数 / `LEARNING_METRICS_PERSIST_*` env），生产启用需显式配置
- `config.yaml` 未加 `learning.metrics.persistence` 段（代码默认值可用）；如需配置驱动可后续补充
- pytest 在本机并行活跃时耗时异常（非代码问题），CI 为可靠验证通道
