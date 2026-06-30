# PT3 — 补充单元测试

> **目标：** 补齐剩余模块的单测，从96到120个测试文件
> **项目路径：** `c:\Users\Administrator\agent`
> **分支建议：** `refactor/PT3-unit-tests`

## 一、当前覆盖盲区扫描

```bash
# 找一下哪些模块测试文件偏少
for f in agent/model_router agent/task_planner agent/human_in_the_loop agent/health agent/audit agent/subagent; do
  name=$(basename $f)
  count=$(ls tests/unit/test_${name}*.py 2>/dev/null | wc -l)
  echo "$name: $count 个测试文件"
done
```

## 二、新增测试

### Step 1: 补全 AuditLogger 测试

追加到 `tests/unit/test_audit.py`：

```python
class TestAuditQuery:
    def test_query_by_trace_id(self):
        ...

    def test_query_by_action(self):
        ...

    def test_query_limit(self):
        ...

    def test_empty_query_returns_empty(self):
        ...

class TestAuditAppendOnly:
    def test_log_is_appended_not_overwritten(self):
        ...

    def test_log_file_rotation(self):
        ...
```

### Step 2: 补全 Subagent 测试

追加到 `tests/unit/test_subagent.py`：

```python
class TestSubagentLifecycle:
    def test_create_and_destroy(self):
        ...

    def test_double_create_fails(self):
        ...

    def test_list_empty_initially(self):
        ...

    def test_execute_nonexistent_raises(self):
        ...

class TestSubagentSandbox:
    def test_docker_not_available(self):
        ...
```

### Step 3: 补全 Cognitive Loop 边界测试

追加到 `tests/unit/test_cognitive_loop.py`：

```python
class TestCognitiveEdgeCases:
    def test_max_retries_exceeded(self):
        ...

    def test_empty_input_handling(self):
        ...

    def test_very_long_input_truncation(self):
        ...

    def test_memory_persistence(self):
        ...
```

## 三、运行

```bash
python -m pytest tests/unit/ -q --tb=short 2>&1 | tail -3
# 预期: 全部通过，测试数≥120
```
