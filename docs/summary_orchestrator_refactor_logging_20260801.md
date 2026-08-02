# Orchestrator 拒识/兜底重构 + 日志增强 总结报告

> 日期：2026-08-01
> 分支：`feat/intent-layer-metrics-fix`
> 范围：orchestrator.py 重构提取 / semantic 埋点日志增强 / ratio 分母同步验证 / 监控同步

---

## 一、关键变更点

### 1. 重构提取（commit `01fb5330`）

**【不易】守常量与判定逻辑契约，测试侧禁止复制（漂移风险）**

| 提取项 | 类型 | 位置 | 说明 |
|---|---|---|---|
| `_REJECT_MSG` | 模块级常量 | orchestrator.py:99-102 | 拒识文案，含「转人工」引导 |
| `_FALLBACK_MSG` | 模块级常量 | orchestrator.py:104-107 | 兜底文案，含「转人工」引导 |
| `_LLM_ERROR_MARKERS` | 模块级常量（tuple） | orchestrator.py:110 | 4 个错误标记：抱歉处理/遇到了问题/无法完成/出错了 |
| `_judge_llm_confidence()` | 模块级函数 | orchestrator.py:113-134 | LLM 置信度启发式判定：空/过短→`empty_or_too_short`；错误标记→`error_marker_detected`；正常→`high` |

### 2. semantic 埋点日志增强（commit `58623a22` + `2c5ae897`）

**位置**：orchestrator.py:1086-1108

- `_record_intent_layer("semantic")` 从 L1011（load_instruction 前）移至 L1086（instruction 加载成功且非空后），**守 INV-2**（业务结果确定后才埋点）
- 新增结构化日志 `orchestrator.semantic.metric_total`，输出字段：

| 字段 | 类型 | 验证值 |
|---|---|---|
| `metric_total` | int | 3（分母总和） |
| `layer_counts` | dict | `{"llm":1,"llm_low_confidence_fallback":1,"semantic":1}` |
| `skill_id` | str | `verify_skill_001` |
| `top1_score` | float | 0.875 |
| `instruction_len` | int | 41 |
| `instruction_loaded` | bool | True（恒真，INV-2 显式标记） |

- try/except 包裹，异常静默降级不传播（埋点失败隔离）

### 3. 监控同步（本次新增）

- **docs/observability/intent_routing_logging.md**：新增字段采集规范表 + Promtail pipeline_stages 示例 + 方案 A/B PromQL + rate vs Gauge 选型说明
- **monitoring/grafana/dashboards/business_metrics.json**（v1.1.0 → v1.2.0）：
  - 面板 9 description 更新（四层 → 6 层含 fallback 子指标）
  - 新增面板 10「LLM 低置信度兜底率」（fallback/llm ratio）
  - 新增面板 11「semantic 埋点分母同步」（Loki 日志源，验证 total 单调递增）

---

## 二、验证结果

### 单元测试全绿（58 用例 = 35 + 23）

| 测试文件 | 用例数 | 验证重点 | 结果 |
|---|---|---|---|
| test_prometheus_ratio_regression.py | 15 | 分母同步核心不变量 | ✅ PASS |
| test_llm_low_confidence_dual_counting.py | 12 | 双重计数场景 ratio 守恒 | ✅ PASS |
| test_fallback_submetric_ratio_invariant.py | 8 | fallback 子指标设计 ratio = 1.0 | ✅ PASS |
| **test_judge_llm_confidence_edge_cases.py（新增）** | **23** | 判定输入边界 + 常量不变量 + 异常隔离 + 并发安全 | ✅ PASS |

### 验证脚本通过（11 项字段全 PASS）

```bash
python scripts/verify_semantic_metric_log.py
```

```
[✓ PASS] metric_total=3  skill_id='verify_skill_001'  top1_score=0.875
[✓ PASS] instruction_len=41  instruction_loaded=True
[✓ PASS] layer_counts={'llm':1,'llm_low_confidence_fallback':1,'semantic':1}
[✓ PASS] ratio 总和 = 1.0000000000
[✓ PASS] message 含 total=3 / instr_loaded=success / semantic 触发
```

**核心结论**：即使 fallback 层设计性双重计数（一次低置信度请求计 2 次），ratio 总和仍恒 = 1.0（分母同步机制守护）。

---

## 三、本次补充测试覆盖的边界情况

扫描 `scripts/scan_intent_layer_metric_calls.py` + 静态分析发现的未覆盖边界，已由 `test_judge_llm_confidence_edge_cases.py` 补齐：

| 边界情况 | 覆盖用例 | 说明 |
|---|---|---|
| `_judge_llm_confidence` 输入边界 | 12 用例 | None/空串/纯空白/4字符/5字符边界/各错误标记/多标记 |
| 常量不变量防漂移 | 6 用例 | REJECT/FALLBACK 文案、markers tuple 不可变、与判定逻辑一致性 |
| `_record_intent_layer` 异常隔离 | 2 用例 | prometheus 导入失败不传播、正常调用不抛异常 |
| `_intent_layer_counts` 并发安全 | 3 用例 | 多线程同层/多层写入 ratio 仍 = 1.0、reset+record 并发不抛异常 |

### 已识别但**未在本次修复**的边界（建议后续处理）

> 说明：以下为扫描发现的潜在风险点，均不影响当前 ratio 总和 = 1.0 不变量，故未越界修改代码（守【简易】最小变更）。列入后续建议。

1. **LLM 调用失败路径埋点缺失**：orchestrator.py:507 `_record_intent_layer("llm")` 在 LLM 调用前记录，若 LLM 调用本身抛异常（不进入低置信度判定分支），该请求只在 llm 层计 1 次，不触发 fallback 子指标 → 兜底率分母正常但无法区分"失败"与"低置信度"
2. **语义层顶层异常不埋点**：`_semantic_layer_match` 顶层 try/except（L1121-1145）异常时直接 return None，不记录任何埋点 → semantic 层失败请求完全不计入分母
3. **模块级 dict 无显式锁**：`_intent_layer_counts` 依赖 Python GIL 守护 dict 单操作原子性，高并发下 `+=` 非原子操作存在理论竞态（测试已验证 ratio 不变量守恒，但计数可能有微小偏差）

---

## 四、后续建议

### 短期（下一迭代）

1. **补齐 LLM 失败路径埋点**：在 LLM 调用异常分支（`orchestrator.process.fail` ERROR 日志处）补记 `_record_intent_layer("llm_error")` 或复用 reject 语义，使失败请求可见
2. **语义层异常可见性**：`_semantic_layer_match` 顶层 except 中增加 `_record_intent_layer("semantic_failed")` 或在 structured log 中输出异常摘要
3. **部署 Promtail pipeline**：按 intent_routing_logging.md 的 json 提取规则部署后，验证面板 11 是否正常展示（需 Loki datasource uid=`loki`）

### 中期（监控完善）

4. **`_intent_layer_counts` 加锁**：若未来并发写入量级显著上升，改为 `threading.Lock` 包裹 `+=` 操作，消除理论竞态
5. **低置信率告警**：面板 10 兜底率 > 0.5 时配置 Prometheus alert（P99 latency 已有关联规则，可扩展 LLM 质量维度）
6. **Gauge 快照持久化**：`yunshu_intent_layer_ratio` Gauge 是进程内快照，重启清零。若需长期趋势对比，建议以 Counter `rate()` 为主数据源（方案 A）

### 长期（架构演进）

7. **`_judge_llm_confidence` 增强**：当前为启发式（长度+标记），可扩展 LLM 自评 confidence 字段或工具调用成功率后验（已预留 `low_reason` 枚举扩展点）

---

## 五、相关文件清单

| 文件 | 变更 |
|---|---|
| agent/orchestrator/orchestrator.py | 重构提取 + 埋点后移 + 日志增强 |
| tests/unit/test_fallback_submetric_ratio_invariant.py | 新增 |
| tests/unit/test_prometheus_ratio_regression.py | 新增 |
| tests/unit/test_llm_low_confidence_dual_counting.py | 新增 |
| **tests/unit/test_judge_llm_confidence_edge_cases.py** | **本次新增** |
| scripts/verify_semantic_metric_log.py | 新增（行号同步 L1086） |
| scripts/scan_intent_layer_metric_calls.py | 新增（静态扫描工具） |
| docs/observability/intent_routing_logging.md | 本次更新（采集规范 + PromQL） |
| monitoring/grafana/dashboards/business_metrics.json | 本次更新（v1.2.0，3 面板变更） |
