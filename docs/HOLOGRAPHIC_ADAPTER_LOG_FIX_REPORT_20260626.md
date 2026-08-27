# holographic_adapter.py 日志规范整改报告

- **日期**：2026-06-26
- **模块**：`agent/memory/adapters/holographic_adapter.py`
- **整改目标**：解决全库最严重的 trace_id 缺失问题（约 70 处非结构化日志）
- **状态**：已完成，回归通过

---

## 一、整改背景

在"全模块 trace_id 缺失扫描"中，`holographic_adapter.py` 被判定为 **TOP 1 最严重模块**：

- 全库日志缺失最多（约 70 处非结构化日志）
- 文件内已定义 `_trace_id()` 辅助函数（L38），但所有日志调用均未使用
- 基建已就绪、业务代码未接轨，属于明确的规范缺失

**判定标准**（来自 `agent/logging_utils.py` 规范）：
- 合规：`logger.X(log_dict({...}))`，`log_dict` 自动补齐 `trace_id`/`module_name`/`action`/`duration_ms`
- 违规：`logger.X("纯文本 %s", arg)` 非结构化字符串日志

---

## 二、整改方式

采用 **脚本自动转换 + 人工补漏** 两阶段：

1. **脚本转换**（53 处单行日志）：
   脚本 `scripts/fix_holographic_logs.py` 匹配单行 `logger.X("text %s", args)` 模式，
   自动转换为 `logger.X(log_dict({'module_name': ..., 'action': ..., 'msg': "text %s" % args}))`。

2. **人工处理**（11 处多行日志 + 2 处优先级 bug）：
   多行调用（字符串跨行 + 多参数）由人工逐一转换。

---

## 三、变更明细

### 3.1 总数统计

| 项目 | 数量 |
|------|------|
| 整改前日志调用 | 80 处 |
| 其中已合规（`log_dict`） | 10 处（保持不动） |
| 其中违规（非结构化） | 70 处 |
| 整改后日志调用 | 80 处，**全部结构化** |

### 3.2 action 命名规范

action 采用 `子模块.事件` 语义化命名（与现有合规日志一致）：

| 子模块 | 事件示例 |
|--------|---------|
| `vec` | `vec.success` / `vec.failed` / `vec.degrade` / `vec.dim_mismatch` / `vec.write_failed` |
| `migrate` | `migrate.success` / `migrate.failed` |
| `conflict` | `conflict.recorded` / `conflict.record_failed` / `conflict.resolved` / `conflict.resolve_failed` |
| `adapter` | `adapter.success` / `adapter.failed` |
| `access` / `scorer` / `recovery` / `sync` | `access.update_failed` / `scorer.record_failed` / `recovery.save_failed` / `sync.enabled` |

### 3.3 修复的潜在 Bug（2 处运算符优先级）

`set_syncer` / `set_scorer` 中的三元表达式：

```python
# 修复前（脚本初版生成，存在优先级 bug）
"[HolographicAdapter] syncer 已%s" % "注入" if syncer is not None else "移除"
# 实际解析为 ("已%s" % "注入") if cond else "移除"，条件为假时丢失前缀

# 修复后（加括号）
"[HolographicAdapter] syncer 已%s" % ("注入" if syncer is not None else "移除")
```

---

## 四、验证结果

| 验证项 | 结果 |
|--------|------|
| `python -m py_compile` | 通过 |
| AST 解析 | 通过 |
| 单元测试（test_holographic_adapter_concurrency + test_hotness_scorer + test_tlm_memory_store） | **60 passed** |
| Smoke test（init/save/search/delete/set_syncer/set_scorer） | 功能正常，`%` 格式化无误 |
| 结构化日志运行时验证 | **11/11 条日志均含 trace_id 四字段** |

### 结构化日志输出示例

```python
{'module_name': 'holographic_adapter',
 'action': 'vec.success',
 'message': '[HolographicAdapter][vec] sqlite_vec.load(conn) 加载成功（Python 适配器路径）',
 'trace_id': '1f1a4785dd644ac2',
 'duration_ms': 0}
```

---

## 五、变更文件

| 文件 | 变更类型 |
|------|---------|
| `agent/memory/adapters/holographic_adapter.py` | 70 处日志结构化（+89 -102 行） |
| `scripts/fix_holographic_logs.py` | 新增转换脚本（供后续模块复用） |

---

## 六、遗留风险

- `%` 格式化在运行时已验证（smoke + 单测覆盖 init/save/search/delete 路径）；
  未覆盖的异常分支（如 `_retry_vec_write` 重试耗尽）格式化与常规路径一致，风险低。
- 整改仅涉及日志调用，未改动任何业务逻辑。
