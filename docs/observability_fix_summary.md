# 可观测性修复总结：BusinessMetricsCollector 实例隔离 + 方法名不匹配

**修复日期**: 2026-07-21
**修复范围**: `agent/skills_mgmt/observability.py`, `agent/monitoring/business_metrics.py`
**关联分支**: `feature/tlm-step3-vectorstore-sqlite-vec`
**关联 HEAD**: `37266f77`

---

## 一、问题现象

### 现象 1：emit_metric 调用后内部存储为空

调用链 `emit_eval_score_metric → emit_metric → _metrics.inc_counter/observe_histogram` 执行完毕后，
`BusinessMetricsCollector._counters` 与 `_histograms` 仍为空 dict，`export_prometheus()`
只输出 `HELP/TYPE` 头部而无任何值行，Prometheus 抓取到的 `yunshu_skill_*` 指标缺失。

### 现象 2：多个 BusinessMetricsCollector 实例并存

每次 `observability.py` 模块加载时通过 `BusinessMetricsCollector()` 直接实例化一个新的 collector，
而 `app_server` / `mock_metrics_server` 通过 `get_business_metrics_collector()` 获取另一个全局单例。
两个实例的 `_counters` 互不共享，导致埋点写入实例 A、`/metrics` 端点导出实例 B，数据流断裂。

### 现象 3：try/except 静默吞异常

`emit_metric` 函数末尾的 `except Exception` 只 `logger.debug(...)`，默认 INFO 级别日志不可见，
掩盖了方法名不匹配问题，导致排查路径被延长。

---

## 二、根因分析（按三义原则分类）

### 【不易】契约不变量违反

`observability.emit_metric` 在设计上约定通过 `hasattr(_metrics, "inc_counter")` 探测 collector
是否提供通用 API。但 `BusinessMetricsCollector` 类只暴露了业务专属方法
（`record_interaction` / `record_tool_call` 等）和私有的 `_increment_counter` / `_set_gauge` /
`_observe_histogram`，从未提供 `inc_counter` / `observe_histogram` / `set_gauge` 这 3 个公开方法名。

→ `hasattr` 永远返回 False，3 个 `if/elif` 分支全部静默跳过，**没有任何指标被写入**。

这是契约层的不变量违反：调用方期望的公开 API 在被调方不存在。

### 【变易】实例隔离 vs. 全局共享的演进偏差

`business_metrics.py` 早已在 line 1298/1301 提供了 `_global_business_collector` 单例和
`get_business_metrics_collector()` 工厂函数，并要求所有业务方法（`record_interaction` 等）
通过工厂函数访问单例。但 `observability.py` 在初始化时仍使用 `BusinessMetricsCollector()`
直接实例化，形成"双实例"格局。

→ 即使后续补齐了公开方法名，埋点写入与 `/metrics` 导出仍会落在不同实例上。

### 【简易】防御性编程的反面教材

`emit_metric` 的 `try/except + logger.debug` 模式本意是"埋点失败不影响主流程"（守不易），
但因为 `hasattr` 失败属于**正常控制流**而非异常，try/except 根本不会触发，错误被彻底沉默化。
正确的"埋点失败隔离"应是：方法存在时调用，方法不存在时显式记录 warning。

---

## 三、修复方案

### 修复 1：observability.py 改用全局单例（守"变易"统一实例）

```python
# 旧代码（line 22-23）
from agent.monitoring.business_metrics import BusinessMetricsCollector
_metrics = BusinessMetricsCollector()

# 新代码
from agent.monitoring.business_metrics import get_business_metrics_collector
_metrics = get_business_metrics_collector()
```

**影响范围**: 2 行，无 API 破坏，调用方代码（`emit_metric` 等）零改动。

### 修复 2：business_metrics.py 补齐 3 个公开包装方法（守"不易"契约对齐）

在 `# ── 内部方法 ──` 之前插入：

```python
# ── 通用对外 API（供 observability.emit_metric 等通用埋点入口调用）──

def inc_counter(self, metric_name: str,
                labels: Optional[Dict[str, str]] = None,
                value: float = 1.0) -> None:
    """[TLM-L1] 通用计数器埋点 — 内部循环 _increment_counter 以支持 value>1"""
    if value <= 0:
        return
    labels = labels or {}
    n = int(value)
    for _ in range(n):
        self._increment_counter(metric_name, labels)

def observe_histogram(self, metric_name: str,
                      value: float,
                      labels: Optional[Dict[str, str]] = None) -> None:
    """[TLM-L1] 通用直方图埋点 — 委托 _observe_histogram"""
    self._observe_histogram(metric_name, labels or {}, float(value))

def set_gauge(self, metric_name: str,
              value: float,
              labels: Optional[Dict[str, str]] = None) -> None:
    """[TLM-L1] 通用仪表盘埋点 — 委托 _set_gauge"""
    self._set_gauge(metric_name, labels or {}, float(value))
```

**设计要点**:
- 公开方法仅做参数归一化，核心逻辑仍走私有 `_increment_counter` 等，复用既有的
  `try/except` 埋点失败隔离与 `self._lock` 锁保护（守不易：不破坏现有 save/search 接口）。
- `inc_counter` 支持 `value > 1` 的批量计数，通过循环 `_increment_counter` 实现，
  避免改动 `_increment_counter` 签名（最小变更原则）。
- `observe_histogram` / `set_gauge` 直接转发，签名与 `emit_metric` 调用约定一致。

---

## 四、验证结果

### 4.1 磁盘文件验证

```
[OK] observability.py: get_business_metrics_collector import
[OK] observability.py: 单例调用
[OK] business_metrics.py: inc_counter 方法
[OK] business_metrics.py: observe_histogram 方法
[OK] business_metrics.py: set_gauge 方法
```

### 4.2 git diff 验证

```
agent/monitoring/business_metrics.py | 25 +++++++++++++++++++++++++
agent/skills_mgmt/observability.py   |  4 ++--
2 files changed, 27 insertions(+), 2 deletions(-)
```

### 4.3 端到端数据流验证（通过 mock_metrics_server.py）

调用 `emit_eval_score_metric` 发射 5 条评估指标后，`/metrics` 端点返回：

```
# HELP yunshu_skill_eval_score ...
# TYPE yunshu_skill_eval_score histogram
yunshu_skill_eval_score{quantile="0.5",skill_id="prom-verify-normal",task_success="true"} 0.92
yunshu_skill_eval_score{quantile="0.5",skill_id="prom-verify-hallucination",task_success="false"} 0.3
...

# HELP yunshu_skill_hallucination_total ...
# TYPE yunshu_skill_hallucination_total counter
yunshu_skill_hallucination_total{skill_id="prom-verify-hallucination"} 4
```

数据流：`emit_eval_score_metric → emit_metric → inc_counter/observe_histogram →
singleton collector → export_prometheus → /metrics` 全链路通畅。

### 4.4 Grafana 仪表盘

新增 panel id=7：`技能幻觉实时趋势（5m rate，按 skill_id 分组 + 合计）`
- PromQL A: `sum by (skill_id) (rate(yunshu_skill_hallucination_total[5m]))`
- PromQL B: `sum(rate(yunshu_skill_hallucination_total[5m]))`

---

## 五、风险与回滚

### 风险评估

| 风险项 | 等级 | 缓解措施 |
|--------|------|----------|
| 公开方法 inc_counter 循环调用私有方法，value>1 时性能下降 | 低 | 业务埋点 value 默认 1.0，批量场景极少 |
| 单例修复影响其他 import BusinessMetricsCollector 的代码 | 低 | 全仓库 grep 确认仅 observability.py 使用直接实例化 |
| `_observe_histogram` / `_set_gauge` 签名变化 | 无 | 公开方法仅转发，私有方法签名未改 |

### 回滚方案

```bash
git revert <commit-hash>
```

由于修复仅涉及 2 个文件、27 行新增 + 2 行修改，回滚影响面极小。

---

## 六、教训（Lessons Learned）

1. **hasattr + try/except 双重静默是反模式**：`hasattr` 失败属于正常控制流，try/except 抓不到，
   应在 `hasattr` 失败时显式 `logger.warning`。
2. **实例隔离 vs. 全局单例的契约必须文档化**：`get_business_metrics_collector()` 已存在但未在
   `observability.py` 中强制使用，导致双实例问题潜伏数月未被发现。
3. **`.pyc` 缓存可能掩盖磁盘文件未持久化问题**：Edit 工具修改未写入 `.py` 但 Python 从 `.pyc`
   加载新代码，运行时验证通过但 git 看不到改动，需用 `git diff` 或 `Select-String` 直接验证磁盘。
4. **`core.autocrlf=true` 会误导 git diff 调查**：CRLF 归一化使 `git hash-object` 与 HEAD blob
   hash 匹配，即使内容不同。需用 `git diff --no-index` 跨文件对比才能看到真实差异。

---

## 七、关联文件

- 修复脚本: `scripts/_apply_singleton_fix.py`（被 `.gitignore` 屏蔽，不入库）
- 修复文件 1: `agent/skills_mgmt/observability.py`
- 修复文件 2: `agent/monitoring/business_metrics.py`
- 验证脚本: `scripts/mock_metrics_server.py`
- Docker 启动文档: `docs/docker_startup_after_singleton_fix.md`
- Grafana 仪表盘: `monitoring/grafana/dashboards/yunshu-skill-quality.json`
