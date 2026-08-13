# Changelog: 五层健康探针采集与 L5 断线补全

> **改造主题**：为云枢（Yunshu）健康评估体系补齐「真实数据采集」环节——新增五层健康探针（probes）、JSONL 按日滚动持久化（storage）与 60s 幂等采集线程（collector），并修正边界断言：无数据时返回 `None` 而非假满分 `1.0`。
>
> **涉及 commit**：
> - `aa4b303d` feat(health): 五层健康探针采集与L5断线补全模块

---

## 一、背景

健康评估此前仅有 `HealthAssessor` 的评分逻辑，缺少真实数据源：
- 探针数据依赖手工注入，`assess_with_probes` 在无数据时会被迫「假满分」（`overall=1.0`），掩盖系统真实故障；
- 健康历史无持久化，Dashboard 断线后无法回补与趋势分析。

本次重构落地「存在即可见」：五层探针自动采集 → 加权归一化 → 落盘历史。

## 二、新增模块

### 2.1 `agent/health/probes.py` — 五层探针

| 层 | 标识 | 数据源 | 无数据语义 |
|---|---|---|---|
| L1 | `l1_process` | psutil 进程资源（内存/CPU/线程/磁盘） | psutil 缺失/采集失败 → `available=False, score=None` |
| L2 | `l2_dependency` | `configs/{app,paths}.yaml` 可读性 | 任一核心配置不可读 → `available=False` |
| L3 | `l3_llm_tool` | metrics 调用错误率 + 熔断器状态 | 无调用计数且未注册熔断器 → `available=False` |
| L4 | `l4_business` | metrics 业务请求成功率 | 窗口内无请求 → `available=False` |
| L5 | `l5_semantic` | 用户反馈满意度（近 200 条） | 无反馈记录 → `available=False` |

**核心契约（探针不变量）**：
- 每层返回 `ProbeResult(layer, score, detail, available)`；
- `available=False` 时 `score` 必须为 `None`——**禁止假满分**（无数据参与归一化即失真）；
- 单层探针失败不允许向上抛异常，降级为 `available=False` 并记录 detail；
- `run_all_probes()` 返回 `{layer: ProbeResult}`，键序固定（L1→L5），单层失败不影响其余层。

### 2.2 `agent/health/storage.py` — 历史持久化

- 文件命名 `data/health/history-YYYY-MM-DD.jsonl`，按日滚动；
- 单条记录含 `timestamp/overall/dimensions/issues/probe_details`；
- `query_history(days)` 跨日聚合、按时间升序，供趋势查询；
- 写入失败仅告警降级（不抛出），并发追加用文件级追加写避免串行锁；
- 全局单例 `health_storage`（Dashboard / collector 共用）。

### 2.3 `agent/health/collector.py` — 采集线程

- `start_collector(interval=60)` 启动后台 daemon 线程（`health_collector`）；
- 每轮流水线：`run_all_probes()` → `health_assessor.assess_with_probes()` → `health_storage.append()`；
- **幂等**：重复调用不重复启动；**单轮失败只告警**，不中断采集循环。

## 三、归一化算法与 L5 断线补全

五层权重（固定不动点）：

| 层 | 权重 |
|---|---|
| L1 进程资源 | 0.25 |
| L2 依赖服务 | 0.20 |
| L3 LLM/工具 | 0.25 |
| L4 业务接口 | 0.20 |
| L5 语义质量 | 0.10 |

**归一化规则**：
1. `available=False` 的层不参与分母（其权重不计入总权重）；
2. 其余层按各自权重**重归一化**（`score * weight / sum(available_weights)`）；
3. 全部五层均不可用 → `overall = None`（**禁假满分**）；
4. 结果保留完整精度，不 `round`。

L5 断线补全：L5 无反馈（如反馈表清空/7 天无记录）时降级为 `available=False`，overall 由 L1–L4 按 0.25/0.20/0.25/0.20 重归一化，避免「无反馈=满分」或「无反馈拖低整体」。

> 算法细节见 `docs/zh/自我修复机制重构计划/五层健康探针归一化算法与L5断线补全.md`。

## 四、测试与断言修正

### 4.1 新增 `tests/unit/test_health_probes_missing.py`（10 项）

- L1–L5 各层单独缺失 / 组合缺失 → `overall=None`；
- 全部缺失 → `overall=None`；
- L5 缺失 → L1–L4 重归一化（含手算验证 0.7944…）；
- 无数据层不参与分母、精度保留（`test_l5_missing_keeps_full_precision` 不 round）。

### 4.2 修正 `tests/boundary/test_health_boundary.py`（3 处断言）

| 用例 | 原断言 | 修正后 |
|---|---|---|
| `test_empty_metrics_assessor` | `overall == 1.0`（假满分） | `overall is None` + issues 含「无数据」 |
| `test_invalid_metrics_none_assessor` | `overall == 1.0` | `overall is None` |
| `test_null_metrics_assessor` | `overall == 1.0` | `overall is None` |

## 五、验证结果

- 定向回归：`test_health_probes_missing.py` + `test_health_boundary.py` → **42 passed**；
- 全量回归：**14153 passed / 21 failed / 8 errors**（39m30s）；
  - 21 个失败与本提交文件**零交集**：16 个为预存失败（orchestrator_reject 10、vector_store_sqlite_vec 4、optimized_storage 1、create_gitee_release_script 1），2 个由本地 `.git/hooks/pre-commit` wrapper 引起（非提交内容），3 个与 health 无关的独立失败（knowledge_workflow 单独跑通过、few_shot_injector、memory_optimized）；
  - 提交后 `aa4b303d` 经 pre-commit wrapper（stash 备份 → 4 项检查全过 → stash pop 恢复）放行。

---

## 附：影响面

- 新增文件：`agent/health/probes.py`、`agent/health/storage.py`、`agent/health/collector.py`、`tests/unit/test_health_probes_missing.py`、`docs/zh/自我修复机制重构计划/五层健康探针归一化算法与L5断线补全.md`
- 修改文件：`tests/boundary/test_health_boundary.py`
- 兼容性：`HealthAssessor.assess_with_probes` 接口签名未变（不破坏既有调用）；数据目录 `data/health/` 为运行时数据，不入版本控制。
