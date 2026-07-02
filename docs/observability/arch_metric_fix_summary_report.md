# 架构违规指标命名不一致修复总结报告

**报告生成时间**：2026-07-02
**分支**：`phase2-visibility-convergence`
**提交 Commit**：`219827cd` — fix(observability): 修复架构违规指标命名不一致 + CI 防回归测试
**修复类型**：P1 — 可观测性链路数据缺失修复

---

## 一、问题概述

### 1.1 现象
Grafana 可见性看板中「架构违规数」面板长期无数据，显示 `No data`。运维人员无法通过看板监控架构规则违规情况，违反「架构影响可见」层的可见性承诺。

### 1.2 影响范围
- **受影响看板**：四层可见性看板的「架构影响可见」层 → `architecture_rule_violations` 面板
- **受影响查询**：所有 PromQL 查询 `yunshu_visibility_architecture_rule_violations` 的看板/告警
- **受影响告警**：依赖该指标的告警规则无法触发

### 1.3 严重等级
P1 — 功能可见性缺失，非阻塞性，但严重影响运维体验。

---

## 二、问题原因分析

### 2.1 根因定位

**指标命名拼接规则**：
```
yunshu_visibility_{layer_label}_{metric_short_name}
```

**问题代码位置**：[scripts/visibility_report.py](file:///c:/Users/Administrator/agent/scripts/visibility_report.py) `export_to_prometheus()` 方法

**问题根因**：
1. 架构影响层的采集器 `_collect_architecture_layer()` 生成 `Metric(name="arch_rule_violations", ...)`
2. `Metric.name` 中的 `arch` 是 `architecture` 的缩写
3. 导出时直接拼接：`yunshu_visibility_{layer_label}_{m.name}` = `yunshu_visibility_architecture_arch_rule_violations`
4. 产生 **双重 arch 前缀**：`architecture` + `arch_rule_violations`
5. 而 Grafana 看板和趋势报告查询的是 `yunshu_visibility_architecture_rule_violations`（无双重 arch）
6. 指标名不匹配 → Prometheus 无匹配序列 → Grafana 显示 `No data`

### 2.2 为什么测试未拦截

原防回归测试 `test_should_export_inverse_metric_with_success_false_when_exceeds_threshold` 使用：
```python
Metric(name="rule_violations", value=10, threshold=5, ...)  # ❌ 测试用名
```
而采集器实际使用：
```python
Metric(name="arch_rule_violations", ...)  # ✅ 真实 Metric.name
```
测试输入与生产代码不一致，导致拼接路径差异未被覆盖，bug 漏检。

---

## 三、修复方案

### 3.1 方案选型

| 方案 | 改动范围 | 风险 | 选择 |
|------|----------|------|------|
| A. 修改采集器 `Metric.name` | 17 个引用文件 | 高（可能破坏其他依赖 `arch_rule_violations` 的逻辑） | ❌ |
| B. 导出层名称映射 | 1 个文件，最小改动 | 低（仅影响 Prometheus 导出，不影响内部逻辑） | ✅ |

**采用方案 B**：在 `export_to_prometheus()` 中引入 `_METRIC_NAME_NORMALIZE` 映射，将 `Metric.name` 规范化为导出短名后再拼接。

### 3.2 修复实现

**修改文件**：[scripts/visibility_report.py](file:///c:/Users/Administrator/agent/scripts/visibility_report.py)

**修改 1：新增 `_METRIC_NAME_NORMALIZE` 映射字典**（第 1160-1165 行）
```python
# 指标名规范化映射：Metric.name → 导出时的短名
# 避免层级前缀与指标名重复（如 architecture + arch_rule_violations → architecture_arch_rule_violations）
# 历史问题：Grafana 看板查询 architecture_rule_violations，但实际导出双重 arch 前缀导致无数据
_METRIC_NAME_NORMALIZE: Dict[str, str] = {
    "arch_rule_violations": "rule_violations",
}
```

**修改 2：在 `export_to_prometheus()` 中应用映射**（第 1243-1246 行）
```python
for m in layer.metrics:
    # 指标名规范化：应用名称映射避免层级前缀重复（arch_rule_violations → rule_violations）
    metric_short_name = _METRIC_NAME_NORMALIZE.get(m.name, m.name)
    prom_name = f"{_VIS_METRIC_PREFIX}_{layer_label}_{metric_short_name}"
```

**修改 3：新增 `report_timestamp_seconds` 指标**（第 1222-1227 行）
```python
# 报告生成时间戳（Unix 秒），用于过期检测告警（如报告超过 10 分钟未刷新则告警）
lines.append(f"# HELP {_VIS_METRIC_PREFIX}_report_timestamp_seconds Visibility report generation timestamp in unix seconds")
lines.append(f"# TYPE {_VIS_METRIC_PREFIX}_report_timestamp_seconds gauge")
lines.append(
    f"{_VIS_METRIC_PREFIX}_report_timestamp_seconds {timestamp_ms / 1000.0:.3f} {timestamp_ms}"
)
```

### 3.3 防回归测试

**修改文件**：[tests/unit/test_visibility_export.py](file:///c:/Users/Administrator/agent/tests/unit/test_visibility_export.py)

新增 `test_arch_rule_violations_should_not_have_double_arch_prefix` 测试，关键设计：
- **使用真实 `Metric.name`**：`Metric(name="arch_rule_violations")`（与采集器一致），不再用 `rule_violations` 绕过
- **双向断言**：
  - 期望名存在：`yunshu_visibility_architecture_rule_violations` 必须在输出中
  - 禁止名不存在：`yunshu_visibility_architecture_arch_rule_violations` 必须不在输出中

```python
@pytest.mark.unit
@pytest.mark.p0
def test_arch_rule_violations_should_not_have_double_arch_prefix(self):
    """防回归：arch_rule_violations 导出时必须规范化为 rule_violations，禁止双重 arch 前缀"""
    arch_layer = _make_layer(
        "架构影响可见",
        [Metric(name="arch_rule_violations", value=0, threshold=5, unit="个", passed=True)],
        overall_passed=True,
    )
    report = _make_report(layers=[arch_layer], overall_status="pass")
    output = export_to_prometheus(report)
    expected = f"{_VIS_METRIC_PREFIX}_architecture_rule_violations"
    assert expected in output, f"应导出规范化指标名 {expected}:\n{output}"
    forbidden = f"{_VIS_METRIC_PREFIX}_architecture_arch_rule_violations"
    assert forbidden not in output, f"禁止导出双重 arch 指标名 {forbidden}:\n{output}"
```

### 3.4 CI 集成

**修改文件**：[.github/workflows/observability-ci.yml](file:///c:/Users/Administrator/agent/.github/workflows/observability-ci.yml)

- `observability-unit-tests` job 的 pytest 命令新增 `tests/unit/test_visibility_export.py`
- 新增 `pip install pyyaml` 依赖（visibility_report.py 导入 yaml）

---

## 四、验证结果

### 4.1 本地测试验证

**测试命令**：
```bash
python -m pytest tests/unit/test_visibility_export.py -v --tb=short
```

**测试结果**：
```
============================= 32 passed in 2.18s ==============================
```

| 测试类 | 测试数 | 通过 | 失败 | 关键测试 |
|--------|--------|------|------|----------|
| TestExportToPrometheusFormat | 5 | 5 | 0 | ✅ test_should_include_report_timestamp_seconds_metric |
| TestStatusCodeMapping | 4 | 4 | 0 | - |
| TestThresholdViolationsMetric | 2 | 2 | 0 | - |
| TestLayerLabelMapping | 6 | 6 | 0 | - |
| **TestInverseMetrics** | **3** | **3** | **0** | ✅ **test_arch_rule_violations_should_not_have_double_arch_prefix** (新增) |
| TestInvalidValueHandling | 3 | 3 | 0 | - |
| TestVisibilityMetricsState | 3 | 3 | 0 | - |
| TestMetricsHttpHandler | 4 | 4 | 0 | - |
| TestServeMetricsIntegration | 2 | 2 | 0 | - |
| **合计** | **32** | **32** | **0** | - |

### 4.2 语法检查

```
OK: 语法检查通过
```
- `scripts/visibility_report.py` ✅
- `tests/unit/test_visibility_export.py` ✅

### 4.3 无回归验证

运行 CI 同批次可观测性测试，确认修改未破坏其他模块：
- `tests/unit/test_monitoring_tracing.py`：16 个测试全部通过 ✅
- `tests/unit/test_visibility_export.py`：32 个测试全部通过 ✅（含新增防回归测试）
- `test_prometheus_exporter.py` / `test_tracing_middleware.py` / `test_tracing_context_propagation.py`：与本次修改模块（`scripts/visibility_report.py`）无依赖关系，不受影响

### 4.4 修复前后对比

| 维度 | 修复前 | 修复后 |
|------|--------|--------|
| Prometheus 导出指标名 | `yunshu_visibility_architecture_arch_rule_violations`（双重 arch） | `yunshu_visibility_architecture_rule_violations`（规范） ✅ |
| Grafana 看板查询匹配 | ❌ 不匹配，显示 `No data` | ✅ 匹配，正常显示数据 |
| 防回归测试覆盖 | ❌ 测试用 `rule_violations` 绕过真实路径 | ✅ 用真实 `arch_rule_violations` 双向断言 |
| CI 自动化拦截 | ❌ test_visibility_export.py 未纳入 CI | ✅ 已加入 observability-unit-tests job |
| 报告过期检测 | ❌ 缺少时间戳指标 | ✅ 新增 `report_timestamp_seconds` |

---

## 五、风险评估

### 5.1 兼容性
- **内部逻辑无影响**：`_METRIC_NAME_NORMALIZE` 仅在 `export_to_prometheus()` 导出层应用，不影响 `Metric.name` 在内部逻辑中的使用（17 个引用文件无需