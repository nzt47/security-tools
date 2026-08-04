# 技术债处理计划：orchestrator 埋点 3 项未修复边界

> 日期：2026-08-01
> 关联报告：docs/summary_orchestrator_refactor_logging_20260801.md（第三节「已识别但未修复的边界」）
> 分支：`feat/intent-layer-metrics-fix`
> 状态：TD-1 已实施（2026-08-02），TD-2/TD-3 待排期

---

## 总览

| # | 边界 | 风险等级 | 代码位置 | 当前影响 | 修复优先级 |
|---|---|---|---|---|---|
| TD-1 | LLM 调用失败路径无独立埋点 | P2 | orchestrator.py:507,516-521 | ~~失败请求不可见，兜底率口径含混~~ **已修复（2026-08-02）** | 高（数据质量） |
| TD-2 | 语义层顶层异常无独立埋点 | P2 | orchestrator.py:1121-1145 | 语义层故障请求完全不计入分母 | 中（需守 INV-2） |
| TD-3 | `_intent_layer_counts` 无显式锁 | P3 | prometheus.py:732,754 | 高并发下理论竞态（GIL 守护下概率极低） | 低（防御性） |

**关键约束（【不易】）**：
1. 任何修复**不得破坏** ratio 总和 = 1.0 分母同步不变量
2. 任何修复**不得引入**新的双重计数（守 INV-1：每分支有且仅有一次埋点）
3. 修复后必须通过现有 58 个 ratio 测试 + 新增回归用例

---

## TD-1：LLM 调用失败路径无独立埋点 【已实施 2026-08-02】

### 现状

[orchestrator.py:507](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py#L507) `_record_intent_layer("llm")` 在 LLM 调用前记录。

- **成功且高置信**：llm × 1
- **成功但低置信**：llm × 1 + llm_low_confidence_fallback × 1（L585）
- **调用失败**（L516 except 分支）：仅 llm × 1 → 失败与正常无法区分

### 实施记录（2026-08-02）

- **代码**：[orchestrator.py:517-520](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py#L517-L520) except 分支补记 `_record_intent_layer("llm_error")`
- **设计决策**：`llm_error` 为 `llm` 的**失败子指标**（与 `llm_low_confidence_fallback` 同模式）。
  守 INV-4（调用前埋点），`llm` 计"尝试"、`llm_error` 计"失败"——成功路径不记 llm_error。
  面板 10 用 `llm_error / llm` 计算 LLM 错误率（llm 分母含全部尝试，纯失败时段不为 0）。
  与计划原文"互斥（llm=0）"表述的差异：若失败时 llm=0，错误率折线在纯失败时段除零，
  故按 INV-4 语义实施（三义优先级 > 文档措辞）。
- **测试**：`tests/unit/test_llm_error_path_recorded.py` 9 用例全绿
  （5 机制级 + 4 wiring 级走真实 `process()` except 分支）
- **回归**：58 既有 ratio 测试 + 9 新用例 = 67 全绿；`verify_semantic_metric_log.py` 通过
- **文档**：intent_routing_logging.md 第四步补充 llm_error

### 验收标准

- [x] LLM 异常时 `_intent_layer_counts["llm_error"]` 正确 +1（wiring 测试验证）
- [x] 成功路径不记 llm_error（不双计）
- [x] ratio 总和恒 = 1.0（含 3 层：llm + llm_error + 历史层）
- [x] 新增 ≥2 个回归用例全绿（实际 9 个）

---

## TD-2：语义层顶层异常无独立埋点

### 现状

[orchestrator.py:1121-1145](file:///c:/Users/Administrator/agent/agent/orchestrator/orchestrator.py#L1121-L1145) 顶层 try/except 捕获语义层所有异常后 `return None`（降级 LLM），不记录任何埋点。

**注意与 INV-2 的张力**：
- L1041-1042 已将 semantic 埋点后移至「instruction 加载成功且非空」之后（P0 修复），就是为了避免与后续 llm 埋点（L506）双重计数
- 因此**不能**在异常路径补记 `semantic`（会与 llm 双计），而应补记独立的 `semantic_failed`

### 影响分析

1. 语义层 `svc.loader.match` 抛异常 → 请求直接降级 LLM，语义层故障完全不可见
2. 现有 `count.orchestrator.semantic.error` Counter 未接入意图层 ratio 体系
3. 无法从意图层分布中发现语义层健康度退化（如 reranker 加载失败）

### 修复方案

新增独立 layer 值 `semantic_failed`，在 L1121 except 分支补记：

```python
except Exception as e:
    elapsed_ms = (time.time() - ts_sem) * 1000
    _record_intent_layer("semantic_failed")   # ← 新增：语义层异常独立计层
    logger.warning(log_dict({
        'module_name': 'orchestrator',
        'action': 'orchestrator.semantic.error',
        ...
    }))
```

**不变量校验**：`semantic_failed` 与 `semantic` 互斥（成功命中记 semantic，异常记 semantic_failed），降级到 LLM 后 llm 埋点（L506）是**后续独立请求阶段**的埋点，非同一语义层路径双计 → ratio 总和仍 = 1.0。

> 同类路径：L1053-1054（load_instruction 失败）、L1057-1058（instruction 为空）也是「语义层尝试但未命中」——**维持现状不补记**（有意降级，非异常），避免分母语义膨胀。如需观测可加 `semantic_degraded`（可选，后续再议）。

### 伴随变更

| 项 | 内容 |
|---|---|
| 测试 | 新增 `test_semantic_exception_recorded`：mock loader.match 抛异常 → semantic_failed=1, semantic=0 |
| 测试 | 新增 `test_semantic_failed_not_dual_count_llm`：semantic_failed + llm 不双计，ratio=1.0 |
| dashboard | 面板 9 饼图可选包含 semantic_failed（归入"其他"或独立扇区） |
| 文档 | 语义层 section 补充 `record_intent_layer("semantic_failed")` |

### 验收标准

- [ ] `svc.loader.match` 抛异常时 `semantic_failed` +1
- [ ] 与 `semantic`、`llm` 均不双计
- [ ] ratio 总和恒 = 1.0
- [ ] 新增 ≥2 个回归用例全绿

---

## TD-3：`_intent_layer_counts` 无显式锁

### 现状

[prometheus.py:732](file:///c:/Users/Administrator/agent/agent/monitoring/prometheus.py#L732) `_intent_layer_counts: dict = {}` 为模块级 dict，[prometheus.py:754](file:///c:/Users/Administrator/agent/agent/monitoring/prometheus.py#L754) `+= 1` 非原子操作。

### 影响分析

1. CPython 3.12 下 GIL 守护 dict 单操作原子性，但 `d[k] = d.get(k, 0) + 1` 是 **LOAD + ADD + STORE** 三步，线程切换点可能丢失更新
2. 已测试验证：多线程下 ratio 总和仍 = 1.0（因为丢失更新只影响绝对计数，不影响"总和"数学恒等），但**绝对计数可能偏低**
3. 未来若改 PyPy / 多进程共享（multiprocessing），竞态概率显著上升

### 修复方案

加 `threading.Lock` 包裹计数操作（最小侵入）：

```python
import threading

_intent_layer_counts: dict = {}
_counts_lock = threading.Lock()   # ← 新增

def record_intent_layer(layer: str):
    ...
    with _counts_lock:            # ← 新增：保护 读改写
        _intent_layer_counts[layer] = _intent_layer_counts.get(layer, 0) + 1
    total = sum(_intent_layer_counts.values())
    ...
```

**不变量校验**：锁仅保护内存状态变更，无 I/O/外部回调（守项目硬约束「持锁操作严禁包含 I/O」）。

### 伴随变更

| 项 | 内容 |
|---|---|
| 测试 | 现有 `TestIntentLayerCountsConcurrency` 3 用例升级断言：`_intent_layer_counts["semantic"]` 精确 == N_THREADS × N_PER（加锁后无丢失） |
| 文档 | prometheus.py 模块注释补充锁设计说明 |

### 验收标准

- [ ] 10 线程 × 100 次写入后计数精确 = 1000（无丢失）
- [ ] reset 与 record 并发无异常（现有用例保持通过）
- [ ] ratio 总和仍 = 1.0
- [ ] 持锁段仅内存操作（代码审查确认无 I/O）

---

## 排期建议

| 阶段 | 任务 | 前置条件 | 预估变更量 |
|---|---|---|---|
| 阶段 1（高优先） | TD-1 llm_error 埋点 | 无 | orchestrator.py +4 行，测试 +2 用例 |
| 阶段 2（中优先） | TD-2 semantic_failed 埋点 | 无 | orchestrator.py +1 行，测试 +2 用例 |
| 阶段 3（低优先） | TD-3 计数锁 | 无 | prometheus.py +4 行，测试断言增强 |

> 每个阶段独立可提交、可回滚（守【变易】大变更拆小步）。阶段 1+2 完成后统一跑
> `pytest tests/unit/` 全量回归 + `python scripts/verify_semantic_metric_log.py`。

---

## 回归防护清单

实施任一 TD 后必须验证（守【不易】测试护城河）：

```bash
# ratio 分母同步 + 双重计数 + 边界用例（现有 58 + 新增）
python -m pytest tests/unit/test_prometheus_ratio_regression.py \
  tests/unit/test_llm_low_confidence_dual_counting.py \
  tests/unit/test_fallback_submetric_ratio_invariant.py \
  tests/unit/test_judge_llm_confidence_edge_cases.py -v

# 日志字段验证脚本
python scripts/verify_semantic_metric_log.py
```

**不变量红线**（任一违反即回滚）：
1. `sum(_intent_layer_counts.values())` 后各层 ratio 总和偏离 1.0 > 1e-9
2. 同一请求同一路径被计 2 次（新增 layer 与既有 layer 非互斥）
3. 持锁段出现 I/O / 外部回调 / 嵌套锁
