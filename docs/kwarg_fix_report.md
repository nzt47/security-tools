# 关键字参数冲突修复 — 变更清单报告

> **生成时间**: 2026-06-30 01:27:53
> **修复总数**: 12 处
> **涉及提交**: 3 个
> **涉及文件**: 6 个

## 1. 提交概览

| Commit | 日期 | 提交信息 |
|--------|------|----------|
| `edabf6bc` | 2026-06-30 | test: 新增 observability trackEvent 单元测试 + 修复保留键冲突 bug |
| `44eaccf6` | 2026-06-30 | fix: 扫描并修复全项目关键字参数冲突风险 |
| `4ec3f00c` | 2026-06-30 | fix: 修复 6 处 **kwargs 转发未过滤同名参数的冲突风险 |

## 2. 修复统计

### 按风险等级

| 风险等级 | 数量 | 说明 |
|----------|------|------|
| 🔴 HIGH | 6 | 显式 kwarg 与函数参数同名，**kwargs 展开可能冲突 |
| 🟡 MEDIUM | 6 | 外部函数签名已知，**kwargs 转发可能冲突 |

### 按修复类别

| 类别 | 数量 | 典型文件 |
|------|------|----------|
| 可观测性日志 | 5 | `observability.py` |
| LLM 适配器 | 4 | `adapters.py` |
| 失败收集器 | 1 | `failure_collector.py` |
| HTTP 客户端 | 1 | `http_client.py` |
| 子代理摘要 | 1 | `summarizer.py` |

### 保留键出现频率（Top 10）

| 保留键 | 出现次数 |
|--------|----------|
| `model` | 4 |
| `messages` | 4 |
| `trace_id` | 1 |
| `message` | 1 |
| `source` | 1 |
| `severity` | 1 |
| `output` | 1 |
| `subagent_id` | 1 |
| `strategy` | 1 |

## 3. 详细变更清单

### `agent/cognitive/failure_collector.py` (1 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 50 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `trace_id`, `message`, `source`, `severity` | 🔴 HIGH | `44eaccf6` |

### `agent/model_router/adapters.py` (4 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 13 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `model`, `messages` | 🔴 HIGH | `4ec3f00c` |
| 27 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `model`, `messages` | 🔴 HIGH | `4ec3f00c` |
| 41 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `model`, `messages` | 🔴 HIGH | `4ec3f00c` |
| 55 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `model`, `messages` | 🔴 HIGH | `4ec3f00c` |

### `agent/skills_mgmt/observability.py` (2 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 68 | `<module>` | `**payload` | `**safe` | `safe` |  | 🟡 MEDIUM | `44eaccf6` |
| 95 | `<module>` | `**safe_payload` | `**safe` | `safe` |  | 🟡 MEDIUM | `44eaccf6` |

### `agent/subagent/summarizer.py` (1 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 74 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` | `output`, `subagent_id`, `strategy` | 🔴 HIGH | `4ec3f00c` |

### `agent/web/http_client.py` (1 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 120 | `<module>` | `**kwargs` | `**safe_kwargs` | `safe_kwargs` |  | 🟡 MEDIUM | `44eaccf6` |

### `agent/workflow_learning/observability.py` (3 处)

| 行 | 函数 | 旧模式 | 新模式 | 过滤变量 | 保留键 | 风险 | Commit |
|----|------|--------|--------|----------|--------|------|--------|
| 152 | `<module>` | `**payload` | `**safe` | `safe` |  | 🟡 MEDIUM | `44eaccf6` |
| 157 | `<module>` | `**payload` | `**safe` | `safe` |  | 🟡 MEDIUM | `44eaccf6` |
| 166 | `<module>` | `**payload` | `**safe` | `safe` |  | 🟡 MEDIUM | `44eaccf6` |

## 4. 修复模式说明

### 统一修复模板

```python
# 1. 定义保留键集合（与显式参数同名）
_RESERVED = {"trace_id", "duration_ms", "level", "action", "module_name"}

# 2. 过滤 **kwargs 中的保留键
safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}

# 3. 使用过滤后的变量展开
func(explicit_kwarg=value, **safe_kwargs)
```

### 扫描器识别规则

扫描器 `scripts/scan_kwarg_conflicts.py` 通过以下规则识别已过滤变量，避免误报：

- 变量名含 `safe_`/`filtered_`/`clean_` 前缀 → 识别为已过滤
- 变量名含 `_safe`/`_filtered`/`_clean` 后缀 → 识别为已过滤
- 字典推导式含 `if k not in _RESERVED` 条件 → 识别为已过滤

## 5. 审查建议

1. **重点审查 HIGH 风险项**: 确认保留键集合是否完整覆盖了目标函数的所有显式参数名
2. **检查过滤变量命名**: 确保使用 `safe_` 前缀或 `_safe` 后缀，以便扫描器识别
3. **验证测试覆盖**: 运行 `pytest tests/unit/test_observability_track_event.py` 确认无回归
4. **CI 集成**: 提交前运行 `python scripts/scan_kwarg_conflicts.py --min-risk HIGH`，HIGH 风险会阻断提交

## 6. 附录

### 扫描命令

```bash
# 扫描高风险（CI 拦截用）
python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH

# 扫描中风险（代码审查用）
python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk MEDIUM

# 生成 JSON 报告
python scripts/scan_kwarg_conflicts.py --format json --output report.json
```
