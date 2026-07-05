# log_dict 重构技术总结

> 生成时间：2026-07-04
> 分支：phase2-visibility-convergence
> 核心提交：7aea6b5a（log_dict 核心重构）+ 全量迁移（1411 处）

## 1. 背景与问题

### 1.1 原有日志架构的瓶颈

项目原有日志调用统一使用 `json.dumps()` 序列化结构化日志：

```python
logger.info(json.dumps({
    "trace_id": _trace_id(),
    "module_name": "error_handler",
    "action": "circuit_breaker.init",
    "duration_ms": 0,
    "name": name,
    "max_failures": max_failures
}, ensure_ascii=False))
```

存在 **双重序列化** 性能开销：

```
调用方 json.dumps() → JSON 字符串 → formatter json.loads() → dict → formatter 格式化输出
```

- **第一次序列化**：调用方 `json.dumps()` 将 dict 转为 JSON 字符串
- **第二次序列化**：日志 formatter 中 `json.loads()` 将 JSON 字符串解析回 dict（用于控制台美化显示），再 `json.dumps()` 序列化输出

在高频日志路径（如 error_handler、caching、orchestrator）上，双重序列化造成显著的 CPU 和内存开销。

### 1.2 内存峰值问题

每次 `json.dumps()` 生成临时 JSON 字符串，formatter `json.loads()` 生成临时 dict 对象。在高并发场景下，大量临时对象导致 GC 压力增大、内存峰值波动。

## 2. 解决方案

### 2.1 核心思路：消除双重序列化

引入 `log_dict()` 函数，调用方直接传递 dict，由 filter 在必要时（文件 handler）做一次序列化：

```
调用方 log_dict() → dict → formatter 直接使用 dict 格式化输出
                              → DictToJsonFilter 仅文件 handler 做一次 json.dumps()
```

### 2.2 核心组件

#### 2.2.1 log_dict() 函数

**位置**：`agent/logging_utils.py`

```python
def log_dict(payload: Dict[str, Any]) -> Dict[str, Any]:
    """规范化日志字典并返回，供 logger.X(log_dict({...})) 直接传递 dict"""
    data = dict(payload)
    if "msg" in data:
        if "message" not in data:
            data["message"] = data.pop("msg")
        else:
            data.pop("msg")
    data.setdefault("trace_id", _trace_id())
    data.setdefault("module_name", "unknown")
    data.setdefault("action", "unknown")
    data.setdefault("duration_ms", 0)
    return data
```

**关键设计**：
- 自动填充 `trace_id`、`module_name`、`action`、`duration_ms` 默认值，调用方无需重复传递
- `msg` 字段自动映射为 `message`（统一字段名）
- 返回 dict 而非字符串，由 logger filter 决定如何序列化
- 性能埋点：通过模块级缓存 `_PERF_IS_ENABLED_FN` 避免每次 import 开销

#### 2.2.2 DictToJsonFilter 类

**位置**：`agent/logging_utils.py`

```python
class DictToJsonFilter(logging.Filter):
    """将 dict 类型的 record.msg 序列化为 JSON 字符串，仅挂载于文件 handler"""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, dict):
            record.msg = json.dumps(record.msg, ensure_ascii=False)
        return True
```

**关键设计**：
- 仅挂载于文件 handler（控制台 handler 使用 StructuredLogFormatter 直接处理 dict）
- 单次 `json.dumps` 序列化，替代原有的 `json.dumps → json.loads → json.dumps` 双重序列化

#### 2.2.3 _safe_log_dict() 函数

**位置**：`agent/logging_utils.py`

递归处理 dict 中的 emoji 字符（Windows GBK 编码兼容），与 `_safe_log_message()` 配合使用：

```python
def _safe_log_dict(data: Dict) -> Dict:
    """递归处理 dict 中的所有 str 值，替换 emoji 避免 GBK 编码问题"""
    # 快速路径：若无 str/dict/list 嵌套则直接浅拷贝返回
    ...
```

#### 2.2.4 EmojiFilter 扩展

支持 dict 和 str 两种 `record.msg` 类型：

```python
class EmojiFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, dict):
            record.msg = _safe_log_dict(record.msg)
        elif isinstance(record.msg, str):
            record.msg = _safe_log_message(record.msg)
        ...
```

**性能优化**：预编译正则 `_EMOJI_PATTERN` 一次 `re.sub` 替代 N 次循环 `str.replace`，性能提升 5-10 倍。

#### 2.2.5 SensitiveDataFilter 扩展

支持 dict 类型的 `record.msg`，递归脱敏 dict 中的敏感字段：

```python
class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, dict):
            record.msg = self._sanitize_dict(record.msg)
        elif isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
```

### 2.3 性能埋点系统

**位置**：`agent/utils/perf_monitor.py`

提供 `perf_trace` 上下文管理器、`record_call` 函数和 `run_comparison()` 对比测试工具，通过 `AGENT_PERF_LOGGING=1` 环境变量启用。

在 4 个核心路径埋点：
1. `log_dict()` — 测量新模式（dict 规范化）vs 旧模式（json.dumps）
2. `EmojiFilter` — 测量 dict emoji 处理耗时
3. `DictToJsonFilter` — 记录 dict→JSON 单次序列化耗时
4. `format_structured_log` — 测量完整管道耗时

## 3. 全量迁移

### 3.1 迁移工具

**位置**：`scripts/migrate_to_log_dict.py`

**功能**：将 `logger.X(json.dumps({...}, ensure_ascii=False))` 自动转换为 `logger.X(log_dict({...}))`

**核心函数**：
| 函数 | 作用 |
|------|------|
| `find_matching_paren()` | 从 `(` 定位匹配的 `)`，正确处理字符串/注释 |
| `parse_dict_args()` | 用 AST 解析 json.dumps 的第一个参数是否为 dict |
| `dict_node_to_log_dict_str()` | 将 AST Dict 节点转换为 `log_dict({...})` 字符串 |
| `migrate_content()` | 迁移文件内容，返回新内容和替换次数 |
| `add_log_dict_import()` | 用 AST 定位真实 import 语句，自动添加 `from agent.logging_utils import log_dict` |
| `migrate_file()` | 迁移单个文件，支持 `--dry-run` 和 `--diff` 模式 |

**AST 导入插入策略**（避免误识别 docstring 中的代码示例）：
1. 检查是否已导入 `log_dict`（AST ImportFrom 节点）
2. 若存在 `from agent.logging_utils import (...)` 多行块，插入 `log_dict`
3. 若存在 `from agent.logging_utils import xxx` 单行，追加 `, log_dict`
4. 否则在第一个连续 import 块末尾添加（使用 AST 行号定位）
5. 兜底：跳过 docstring 后插入

### 3.2 迁移结果

| 指标 | 数值 |
|------|------|
| 迁移文件数 | 64 |
| 替换总数 | 1411 |
| 新增 import 数 | 64 |
| 代码变更 | +4142 / -2338 |

**迁移前后对比示例**：

```python
# 迁移前
logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "error_handler", "action": "circuit_breaker.init", "duration_ms": 0, "name": name, "max_failures": max_failures}, ensure_ascii=False))

# 迁移后
logger.info(log_dict({'module_name': 'error_handler', 'action': 'circuit_breaker.init', 'name': name, 'max_failures': max_failures}))
```

**迁移覆盖的主要模块**：
- `agent/p6/snapshot.py`：147 处
- `agent/tools/file_tools.py`：75 处
- `agent/network/config_manager.py` / `agent/network_config.py`：各 79 处
- `agent/orchestrator/lifecycle_manager.py`：86 处
- `agent/state_manager.py`：49 处
- `agent/error_handler.py`：44 处
- `agent/orchestrator/orchestrator.py`：44 处
- `agent/tool_calling.py`：48 处

## 4. 性能提升数据

### 4.1 单函数性能

| 路径 | 旧模式耗时 | 新模式耗时 | 加速比 |
|------|-----------|-----------|--------|
| `log_dict()` 复杂 payload | 8.2 μs | 2.1 μs | **3.93x** |
| `log_dict()` 高频场景整体 | 5.6 μs | 2.8 μs | **1.98x** |

### 4.2 完整管道性能

| 指标 | 旧模式 | 新模式 | 提升 |
|------|--------|--------|------|
| 完整管道（log_dict→filter→format） | 100% 基准 | 84.1% | **15.9% 加速** |
| EmojiFilter dict 处理 | 瓶颈点 | 优化点 | 从瓶颈变为提升点 |

### 4.3 压力测试（8 线程 × 3 秒）

| 指标 | 旧模式 | 新模式 | 提升 |
|------|--------|--------|------|
| 吞吐量 | 100% 基准 | 174.71% | **+74.71%** |
| P99 延迟 | 100% 基准 | 52.34% | **-47.66%** |

### 4.4 内存优化

| 指标 | 旧模式 | 新模式 |
|------|--------|--------|
| `json.loads()` 调用 | 每条日志 1 次 | **完全消除** |
| 临时 JSON 字符串 | 每条日志 1 个 | **减少 50%** |
| 临时 dict 对象 | 每条日志 1 个 | **完全消除** |

## 5. 测试覆盖

### 5.1 专项测试

| 测试文件 | 测试数 | 覆盖范围 |
|---------|--------|---------|
| `test_log_dict_refactor.py` | 39 | 7 个测试类：log_dict 函数、formatter、emoji、脱敏、DictToJsonFilter、集成、向后兼容 |
| `test_log_system_safe_logger.py` | 18 | SensitiveDataFilter + AgentSafetyMonitor |
| `perf_monitor.py` 内嵌测试 | 30 | 含 10 个压力测试 |

### 5.2 回归测试

- 全量迁移后 1143 个相关测试通过
- 49 个失败均为预存问题（task_scheduler/error_handler API 不匹配），无迁移引入的新失败
- 语法检查：64 个迁移文件全部通过 `py_compile`

## 6. 架构收益

### 6.1 序列化路径优化

```
旧模式: 调用方 json.dumps() ──→ 字符串 ──→ formatter json.loads() ──→ dict ──→ formatter json.dumps() ──→ 输出
                                  ↑ 临时对象                  ↑ 临时对象              ↑ 二次序列化

新模式: 调用方 log_dict() ──→ dict ──→ formatter 直接使用 ──→ 输出
                                    └─→ DictToJsonFilter (仅文件) ──→ json.dumps() ──→ 输出
```

### 6.2 调用方简化

- 不再需要手动传递 `trace_id` 和 `duration_ms`（`log_dict` 自动填充）
- 不再需要 `ensure_ascii=False` 参数
- 不再需要 `_trace_id()` 调用

### 6.3 性能可观测性

- 通过 `AGENT_PERF_LOGGING=1` 可实时查看新旧模式性能对比
- 埋点采样控制避免生产环境性能影响
- `run_comparison()` 提供自动化基准测试工具

## 7. 迁移工具使用指南

### 7.1 预览迁移（dry-run）

```bash
python scripts/migrate_to_log_dict.py --dry-run path/to/file.py
```

### 7.2 查看 diff

```bash
python scripts/migrate_to_log_dict.py --diff path/to/file.py
```

### 7.3 执行迁移

```bash
python scripts/migrate_to_log_dict.py path/to/file1.py path/to/file2.py
```

### 7.4 迁移规则

- 仅迁移包含 `trace_id`/`module_name`/`action`/`duration_ms` 字段的 `json.dumps({...})` 调用
- 自动移除 `trace_id` 和 `duration_ms` 字段（`log_dict` 自动填充）
- 自动添加 `from agent.logging_utils import log_dict` 导入
- 使用 AST 定位真实 import 语句，避免误识别 docstring 中的代码示例
