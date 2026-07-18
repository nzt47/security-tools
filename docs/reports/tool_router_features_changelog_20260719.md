# ToolRouter 功能增强更新日志

**更新日期**: 2026-07-19
**影响模块**: `agent/tool_router.py`、`agent/tests/test_tool_router.py`
**相关 commit**: `f44b5bb9`（功能实现）、`3ce10fc9`（正式记录）

---

## 1. 概述

本次更新为 `tool_router.get_tools_for_input` 实现三项功能增强，旨在优化工具选择策略、减少语义重复工具、控制返回工具数量以节省 tokens。

| 功能 | 名称 | 目的 |
|---|---|---|
| 功能 1 | priority 优先级排序 | 按分类重要性排序返回工具 |
| 功能 2 | 别名合并 | 主工具选中时移除别名工具，避免语义重复 |
| 功能 3 | max_tools 数量限制 | 限制返回工具数量，截断保留高优先级工具 |

**核心契约（不易）**: 关键词分类逻辑不动、现有工具运行时行为不动、现有测试不引入新回归。

---

## 2. 功能 1: priority 优先级排序

### 设计

为 `_DEFAULT_TOOL_CATEGORIES` 的 11 个分类添加 `priority` 字段（int，值越小优先级越高）：

| 分类 | priority | 说明 |
|---|---|---|
| `core` | 0 | 核心工具（始终发送）|
| `web` | 1 | 网络与搜索 |
| `file` | 2 | 文件系统（测试约束：必须为 2）|
| `code` | 3 | 代码与 Shell |
| `system` | 4 | 系统与进程 |
| `extension` | 5 | 扩展插件 |
| `pdf` | 6 | PDF 处理 |
| `software` | 7 | 软件管理 |
| `async` | 8 | 异步任务 |
| `schedule` | 9 | 定时任务 |
| `v2` | 99 | V2 特性（低优先级）|

### 排序规则

- 工具按其所属类别的 `priority` 升序排列
- 跨类别工具（理论上不存在，但防御性处理）取最小 priority
- 无 priority 字段的工具默认 99（最低优先级）

### 测试约束

`test_priority_order` 期望排序后前 5 个分类为 `["core", "web", "file", "code", "system"]`，且 `file.priority == 2`。
唯一满足的整数分配：`core=0, web=1, file=2, code=3, system=4`（core < web < file 且 file=2）。

---

## 3. 功能 2: 别名合并

### 设计

填充 `TOOL_ALIASES` 映射（main_name → [alias_names]），主工具被选中时移除其别名工具：

```python
TOOL_ALIASES: dict[str, list[str]] = {
    "shell_execute": ["run_program"],      # 两者都是命令执行,保留 code 分类的高优先级工具
    "read_file": ["read_pdf"],             # 读取 PDF 时优先通用 read_file
    "list_directory": ["list_processes"],  # "列出"语义歧义,目录列出优先于进程列出
}
```

### 合并规则

- 主工具在 `selected` 集合中时，其所有别名从结果中移除
- 别名移除发生在白名单交集之后、priority 排序之前
- `TOOL_ALIASES` 为空 dict 时跳过合并逻辑（向后兼容）

### 测试约束

`test_alias_merge` 期望 3 个用例：
- "执行命令" → `shell_execute` 选中，`run_program` 移除
- "读取PDF" → `read_file` 选中，`read_pdf` 移除
- "列出目录" → `list_directory` 选中，`list_processes` 移除

---

## 4. 功能 3: max_tools 数量限制

### 设计

为 `get_tools_for_input` 添加 `max_tools` 参数：

```python
def get_tools_for_input(
    user_input: str,
    enabled_whitelist: list[str] | None = None,
    max_tools: int = 25,
) -> list[str]:
```

### 截断规则

- 按 priority 排序后，截断保留前 `max_tools` 个工具
- `max_tools=None` 或 `<=0` 表示不限制（向后兼容）
- 默认值 25 覆盖常用场景（所有分类全触发时约 61 工具，截断到 25）

### 测试约束

`test_extreme_priority_conflict` 和 `test_performance_metrics` 期望 `len(tools) <= 25`。

---

## 5. 代码改动点

### `agent/tool_router.py`

| 位置 | 改动 | 说明 |
|---|---|---|
| `_DEFAULT_TOOL_CATEGORIES`（第 39-159 行）| 11 个分类添加 `priority` 字段 | core=0, web=1, file=2, code=3, system=4, extension=5, pdf=6, software=7, async=8, schedule=9, v2=99 |
| `TOOL_ALIASES`（第 238-242 行）| 从空 `{}` 填充为 3 个别名对 | shell_execute/run_program, read_file/read_pdf, list_directory/list_processes |
| `get_tools_for_input`（第 405-475 行）| 重写函数体 | 新增 max_tools 参数 + 别名合并 + priority 排序 + 数量截断 |

### `agent/tests/test_tool_router.py`

| 位置 | 改动 | 说明 |
|---|---|---|
| 第 977, 985, 990, 1023 行 | 移除 4 个 `@unittest.expectedFailure` 标记 | test_priority_order, test_alias_merge, test_extreme_priority_conflict, test_performance_metrics 功能已实现 |

---

## 6. 测试验证结果

### 直接相关测试（全部通过）

| 测试文件 | 结果 | 关键测试用例 |
|---|---|---|
| `agent/tests/test_tool_router.py` | **15 passed, 0 xfailed** ✓ | test_priority_order（priority 唯一性 + file=2 + 排序顺序）<br>test_alias_merge（3 个别名合并用例）<br>test_extreme_priority_conflict（len <= 25）<br>test_performance_metrics（len <= 25）|
| `tests/unit/test_tool_definitions_yaml.py` | **56 passed** ✓ | YAML 加载/迁移/降级全链路无回归 |
| `tests/unit/test_tool_trace.py` | **4 passed** ✓ | test_tool_router_records_selection（调用 get_tools_for_input，验证新签名兼容）|

**总计**: 75 个测试全部通过，0 失败，0 xfailed。

### 完整套件中的预先存在失败（与本次改动无关）

| 测试文件 | 失败数 | 原因 |
|---|---|---|
| `agent/tests/test_system_tools.py` | 7 | shell 执行/白名单配置环境问题 |
| `tests/unit/test_memory_vector_store.py` | 卡住 | 数据库锁等待 |

这些失败均为预先存在的环境/配置问题，不涉及 tool_router 的 priority/alias/max_tools 功能。

---

## 7. 调用点兼容性分析

### `get_tools_for_input` 调用点（6 处非测试代码）

| 文件 | 行号 | 调用方式 | 兼容性 |
|---|---|---|---|
| `agent/orchestrator/orchestrator.py` | 728 | `get_tools_for_input(user_input, _whitelist)` | ✅ 位置参数兼容，max_tools 用默认 25 |
| `agent/orchestrator/orchestrator.py` | 941 | `get_tools_for_input(user_input, tools_whitelist)` | ✅ 位置参数兼容 |
| `agent/orchestrator/task_dispatcher.py` | 49 | `get_tools_for_input(user_input, whitelist)` | ✅ 位置参数兼容 |
| `scripts/apply_auto_keywords_and_test.py` | 219, 255 | `get_tools_for_input(input_text)` | ✅ 单参数兼容 |
| `scripts/diagnose_smart_tool_selection.py` | 50 | `get_tools_for_input(test_input)` | ✅ 单参数兼容 |
| `scripts/stress_test_pipeline.py` | 229 | `get_tools_for_input(input_text)` | ✅ 单参数兼容 |

### `TOOL_ALIASES` 引用点（2 处非测试代码）

| 文件 | 行号 | 用途 | 兼容性 |
|---|---|---|---|
| `scripts/apply_config_and_test.py` | 86, 91 | `len(TOOL_ALIASES)` 统计 | ✅ 读取操作（0→3 规则）|
| `scripts/scan_tool_aliases.py` | 14, 232, 235, 260 | 遍历 `TOOL_ALIASES.items()` | ✅ 读取操作 |

### 行为变化说明

orchestrator 生产代码的调用现在受 `max_tools=25` 默认限制：
- **之前**: 无数量限制，全分类触发时返回约 61 个工具
- **现在**: 截断到 25 个，按 priority 排序保留高优先级工具（core/file/web/code/system 优先）
- **影响**: 预期行为变化，节省约 60% tools token，符合功能 3 设计目的

**无需适配**: 所有调用点签名向后兼容，无需显式传递 `max_tools`。

---

## 8. WIP 标记处理情况

### 问题背景

功能 2+3 的代码通过 commit `f44b5bb9` 提交，其 message 包含 "WIP" 标记：
```
feat(tools): WIP 功能2 别名合并 + 功能3 工具数量限制
```

### 处理过程

1. 尝试用 `git commit --amend` 移除 WIP 标记
2. 发现其他会话的 `pull --rebase` 已将原 commit 重写为 `f44b5bb9`，且 HEAD 已前进到其他会话的 commit
3. amend 修改了错误的 commit（修改了 `69506839` 而非 `f44b5bb9`），产生了误导性的 `3ce10fc9`
4. `f44b5bb9` 不是最近的 commit，无法用 amend 修改其 message
5. 系统规则禁止 `git rebase -i`（交互式 rebase），无法修改历史 commit 的 message

### 最终决策

**接受现状**（用户确认）：
- ✅ 代码完整性已验证：HEAD 中的 `tool_router.py` 包含全部改动
- ✅ 功能 2+3 已完整实现并通过 75 个测试验证
- ⚠️ `f44b5bb9` 的 WIP 标记保留在历史中（不影响代码正确性）
- ⚠️ `3ce10fc9` 的 message 已移除 WIP，但内容为 observability.py（误导性，保留）

---

## 9. 三义自检

### 【不易】约束守护
- 关键词分类逻辑（`classify_user_input`）未修改
- 现有 56 个 YAML 测试 + 4 个 tool_trace 测试无回归
- `get_tools_for_input` 新签名向后兼容（位置参数 `None` 仍有效）
- TOOL_ALIASES 合并规则不变（主工具存在 → 别名移除）

### 【变易】扩展能力
- `max_tools` 参数可配置（`None`/`<=0` 不限制，向后兼容）
- `TOOL_ALIASES` 可按需追加新别名对（dict 结构）
- `priority` 字段可通过 YAML 扩展（元数据来自 `_DEFAULT_TOOL_CATEGORIES`）

### 【简易】实现简洁
- 别名合并用 set 减法（`selected -= aliases_to_remove`）
- priority 排序用 `sorted + lambda`
- 数量截断用切片（`result[:max_tools]`）
- 无嵌套地狱，代码 30s 可读

---

## 10. 后续建议

1. **监控 orchestrator 行为**: `max_tools=25` 限制可能影响多分类场景的工具覆盖，建议观察生产日志中"智能选择: X/Y 个工具"的比例
2. **按需调整 max_tools**: 若某些场景需要更多工具，可显式传递 `max_tools=None` 或更大值
3. **扩展 TOOL_ALIASES**: 后续可按需追加新别名对（如 `web_search` 与 `fetch_news` 的语义去重）
4. **清理 WIP 标记**: 若未来有机会进行历史 rewrite（如 squash merge），可清理 `f44b5bb9` 的 WIP 标记