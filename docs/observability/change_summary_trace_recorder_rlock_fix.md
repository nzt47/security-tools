# 变更摘要：TraceRecorder RLock 死锁修复

## 修复日期
2026-07-08

## 概述
本次修复解决了 CI Run 28845908380 中「全项目测试覆盖率」job 失败的问题。
根因是 `tests/unit/test_lifetrace.py:620` 的 `test_concurrent_recording` 测试
在 5 线程并发调用 `record_chat` 和 `record_sensor` 时触发线程死锁，
导致 pytest-timeout 超时。

## CI Run 验证历史

| Run ID | 覆盖率 job 结果 | 失败原因 |
|--------|----------------|---------|
| 28845908380 | failure | test_lifetrace.py:620 线程死锁超时 |
| **28883791833** | **待验证** | trace_recorder.py RLock 修复后 |

### 关键里程碑
- Run 28845908380：E2E 验证通过（Prometheus 修复生效），但覆盖率 job 因
  `test_concurrent_recording` 线程死锁失败
- Run 28883791833：trace_recorder.py RLock 修复推送后触发，待验证

## 根因分析

### 问题链
1. `lifetrace/trace_recorder.py` 第 40 行使用 `threading.Lock()`（不可重入锁）
2. `record_chat` 方法在持锁状态下：
   - 触发 `self.callbacks["chat"]` 回调
   - 调用 `self._auto_classify_topic(content)`
3. `_auto_classify_topic` 在持锁状态下调用 `self.topic_tree.add_to_topic()`
4. 5 个线程并发调用 `record_chat` + `record_sensor`
5. CI 环境性能不足，锁竞争激烈
6. pytest-timeout 超时（默认 30 秒）

### CI 失败堆栈信息
```
File "tests/unit/test_lifetrace.py", line 620, in test_concurrent_recording
    t.join()
  File "/opt/hostedtoolcache/Python/3.11.15/x64/lib/python3.11/threading.py", line 1119, in join
    self._wait_for_tstate_lock()
  File "/opt/hostedtoolcache/Python/3.11.15/x64/lib/python3.11/threading.py", line 1139, in _wait_for_tstate_lock
    if lock.acquire(block, timeout):
+++++++++++++++++++++++++++++++++++ Timeout ++++++++++++++++++++++++++++++++++++
```

### 死锁机制
```
Thread A                          Thread B
─────────                         ─────────
record_chat()                     record_sensor()
  with self._lock:  ← 获取锁        with self._lock:  ← 阻塞等待
    source_tree.record_chat()         (等待 Thread A 释放锁)
    callbacks(node)                   ...
    _auto_classify_topic()            ...
      topic_tree.add_to_topic()       ...
        (文件 IO 操作)                ...
    (锁未释放，GIL 切换)              (仍然等待)
  (继续执行)                       (仍然等待)
                                  ← pytest-timeout 触发
```

## 修复详情

### 修改文件
[lifetrace/trace_recorder.py](file:///c:/Users/Administrator/agent/lifetrace/trace_recorder.py) 第 38-44 行

### 修改内容

#### 修改前
```python
# 状态
self.is_recording = False
self._lock = threading.Lock()

logger.info("TraceRecorder 初始化完成")
```

#### 修改后
```python
# 状态
self.is_recording = False
# 使用 RLock（可重入锁）避免回调重入死锁
# 历史问题：record_chat 持锁时触发 callbacks，若回调中再次调用 record_* 会死锁
# 同时 _auto_classify_topic 在持锁时调用 topic_tree.add_to_topic，
# 5 线程并发竞争 + CI 环境性能不足时易触发 pytest-timeout
self._lock = threading.RLock()

logger.info("TraceRecorder 初始化完成")
```

### 修复原理
- `threading.Lock`：不可重入锁，同线程二次获取会死锁
- `threading.RLock`：可重入锁，同线程可多次获取（需相应次数 release）
- 改为 RLock 后：
  1. 回调中重入 `record_*` 方法不再死锁
  2. `_auto_classify_topic` 持锁调用时减少竞争
  3. 5 线程并发场景下锁等待时间缩短

### Commit
- `01244521` - fix(lifetrace): 使用 RLock 消除 TraceRecorder 并发死锁

## 验证步骤

### 本地验证
```bash
# 1. 验证 py_compile
python -c "import py_compile; py_compile.compile('lifetrace/trace_recorder.py', doraise=True)"

# 2. 运行死锁测试
pytest tests/unit/test_lifetrace.py::test_concurrent_recording -v --timeout=30

# 3. 运行完整 lifetrace 测试
pytest tests/unit/test_lifetrace.py -v --timeout=60

# 4. 验证锁类型
python -c "
from lifetrace.trace_recorder import TraceRecorder
r = TraceRecorder()
assert hasattr(r._lock, '_is_owned'), 'RLock should have _is_owned method'
print('✓ RLock 验证通过')
"
```

### CI 验证
- 推送 `lifetrace/trace_recorder.py` 后触发「云枢系统测试流程」workflow
- 推送 `docs/observability/change_summary_trace_recorder_rlock_fix.md` 触发「可观测性质量保障」workflow
- 监控覆盖率 job 是否通过

## 与 metrics.py 修复的一致性

本次修复与之前的 `metrics.py` RLock 修复（commit `0710b56a`）采用**完全相同的模式**：

| 修复 | 文件 | 锁类型 | 根因 | Commit |
|------|------|--------|------|--------|
| metrics.py | agent/monitoring/metrics.py:79 | Lock → RLock | get_all_metrics → get_stats 重入死锁 | `0710b56a` |
| trace_recorder.py | lifetrace/trace_recorder.py:40 | Lock → RLock | record_chat → callbacks/_auto_classify_topic 重入 | `01244521` |

两处修复都遵循相同的工程模式：
1. 将 `threading.Lock()` 改为 `threading.RLock()`
2. 添加注释说明历史问题和修复原因
3. 不重构其他逻辑（最小化修改）
4. 通过 py_compile 验证

## 遗留问题

### observability-ci.yml paths 过滤器
`lifetrace/**` 不在 observability-ci.yml 的 paths 过滤器中，
单独推送 `lifetrace/trace_recorder.py` 不会触发可观测性 CI。
本次通过同时推送 `docs/observability/` 文档来触发 CI。

建议后续在 observability-ci.yml 的 paths 中添加：
```yaml
- 'lifetrace/**'
```

## 相关文件
- [lifetrace/trace_recorder.py](file:///c:/Users/Administrator/agent/lifetrace/trace_recorder.py) - 修复文件
- [tests/unit/test_lifetrace.py](file:///c:/Users/Administrator/agent/tests/unit/test_lifetrace.py) - 测试文件
- [agent/monitoring/metrics.py](file:///c:/Users/Administrator/agent/monitoring/metrics.py) - 同模式修复参考
- [docs/observability/change_summary_fstring_rlock_fix.md](file:///c:/Users/Administrator/agent/docs/observability/change_summary_fstring_rlock_fix.md) - metrics.py 修复摘要
