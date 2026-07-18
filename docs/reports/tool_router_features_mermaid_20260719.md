# ToolRouter 功能增强 Mermaid 图表

**生成日期**: 2026-07-19
**对应 tag**: `v2.0.0-feature-tools-router`
**对应 commit**: `dc4b45e2`
**数据来源**: [tool_router_features_changelog_20260719.md](./tool_router_features_changelog_20260719.md)

---

## 图表索引

| 编号 | 图表类型 | 主题 | 用途 |
|---|---|---|---|
| 1 | `pie` | 测试用例分布 | 展示 75 个测试用例在 3 个文件中的分布 |
| 2 | `flowchart` | `get_tools_for_input` 处理流程 | 展示功能 1/2/3 在函数内部的执行顺序 |
| 3 | `flowchart` | `get_tools_for_input` 调用点兼容性 | 6 处非测试调用点全部向后兼容 |
| 4 | `flowchart` | `TOOL_ALIASES` 引用点兼容性 | 2 处非测试引用点全部读取兼容 |
| 5 | `gitgraph` | 发布历史 | 展示 commit 链与 tag 关系 |

---

## 1. 测试用例分布（Pie Chart）

**总计**: 75 passed, 0 failed, 0 xfailed

```mermaid
pie title "ToolRouter 功能验证测试分布（共 75 个用例）"
    "test_tool_router.py（功能 1/2/3 直接验证）" : 15
    "test_tool_definitions_yaml.py（YAML 加载/迁移/降级）" : 56
    "test_tool_trace.py（trace 集成 + 新签名兼容）" : 4
```

### 测试结果对比表

| 测试文件 | 用例数 | 状态 | 关键验证点 |
|---|---|---|---|
| `agent/tests/test_tool_router.py` | 15 | ✅ passed | `test_priority_order`（priority 唯一性 + file=2 + 排序）<br>`test_alias_merge`（3 个别名合并用例）<br>`test_extreme_priority_conflict`（len ≤ 25）<br>`test_performance_metrics`（len ≤ 25）|
| `tests/unit/test_tool_definitions_yaml.py` | 56 | ✅ passed | YAML 加载/字段完整性/索引同步/版本兼容/降级兜底 |
| `tests/unit/test_tool_trace.py` | 4 | ✅ passed | `test_tool_router_records_selection`（新签名兼容）|

---

## 2. `get_tools_for_input` 处理流程（Flowchart）

```mermaid
flowchart TD
    Start([用户输入 user_input]) --> Classify[classify_user_input<br/>关键词分类]
    Classify --> IterCat{遍历每个<br/>category}
    IterCat -->|cat_info 存在| AddTools[selected.update<br/>cat_info tools]
    IterCat -->|cat_info 不存在| IterCat
    AddTools --> IterCat

    IterCat --> Whitelist{enabled_whitelist<br/>is not None?}
    Whitelist -->|是| Intersect[selected &= whitelist_set<br/>白名单交集]
    Whitelist -->|否| Alias

    Intersect --> Alias{TOOL_ALIASES<br/>非空?}
    Alias -->|是| MergeAlias[遍历 TOOL_ALIASES<br/>主工具存在 → 收集别名]
    MergeAlias --> RemoveAlias[selected -= aliases_to_remove<br/>功能2 别名合并]
    Alias -->|否| Priority
    RemoveAlias --> Priority

    Priority[构建 tool_to_priority 映射<br/>取最小 priority]
    Priority --> Sort[result = sorted<br/>功能1 priority 排序]
    Sort --> Limit{max_tools 有效<br/>且 len > max_tools?}
    Limit -->|是| Truncate[result = result:max_tools<br/>功能3 数量截断]
    Limit -->|否| Trace
    Truncate --> Trace

    Trace{ToolTraceRecorder<br/>可用?}
    Trace -->|是| Record[record_tool_selection<br/>记录选择 trace]
    Trace -->|否| Return
    Record --> Return[返回 result<br/>默认上限 25]

    classDef feature fill:#e1f5ff,stroke:#0288d1,stroke-width:2px
    classDef guard fill:#fff4e1,stroke:#f57c00,stroke-width:2px
    class MergeAlias,RemoveAlias feature
    class Priority,Sort feature
    class Truncate feature
    class Trace,Record guard
```

### 功能层注释

| 步骤 | 功能 | 不易约束 |
|---|---|---|
| 分类匹配 + 白名单交集 | 基础逻辑 | `classify_user_input` 未修改 |
| 别名合并（功能 2） | set 减法移除别名 | 主工具存在才移除，规则不变 |
| priority 排序（功能 1） | 升序排列 | 跨类别取最小 priority |
| 数量截断（功能 3） | 切片保留前 N | `max_tools=None/<=0` 不限制（向后兼容）|

---

## 3. `get_tools_for_input` 调用点兼容性（Flowchart）

6 处非测试代码调用，全部向后兼容（位置参数 + 新参数使用默认值）。

```mermaid
flowchart LR
    subgraph Caller[调用点 - 6 处]
        O1[orchestrator.py:728<br/>get_tools_for_input<br/>user_input, _whitelist]
        O2[orchestrator.py:941<br/>get_tools_for_input<br/>user_input, tools_whitelist]
        TD[task_dispatcher.py:49<br/>get_tools_for_input<br/>user_input, whitelist]
        S1[apply_auto_keywords_and_test.py:219<br/>get_tools_for_input input_text]
        S2[apply_auto_keywords_and_test.py:255<br/>get_tools_for_input input_text]
        S3[diagnose_smart_tool_selection.py:50<br/>get_tools_for_input test_input]
        S4[stress_test_pipeline.py:229<br/>get_tools_for_input input_text]
    end

    subgraph Signature[新函数签名]
        Sig[get_tools_for_input<br/>user_input: str<br/>enabled_whitelist: list None<br/>max_tools: int = 25]
    end

    subgraph Result[兼容性结论]
        R1[位置参数兼容<br/>max_tools 默认 25]
        R2[行为变化: 生产代码<br/>受 max_tools=25 限制]
        R3[无需适配: 0 处需改]
    end

    O1 --> Sig
    O2 --> Sig
    TD --> Sig
    S1 --> Sig
    S2 --> Sig
    S3 --> Sig
    S4 --> Sig
    Sig --> R1
    Sig --> R2
    Sig --> R3

    classDef prod fill:#e8f5e9,stroke:#388e3c,stroke-width:2px
    classDef script fill:#fff9c4,stroke:#fbc02d,stroke-width:1px
    classDef ok fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    class O1,O2,TD prod
    class S1,S2,S3,S4 script
    class R1,R3 ok
    class R2 fill:#ffe0b2,stroke:#e65100,stroke-width:2px
```

### 调用点明细对比表

| 文件 | 行号 | 调用方式 | 类型 | 兼容性 | 备注 |
|---|---|---|---|---|---|
| `agent/orchestrator/orchestrator.py` | 728 | `get_tools_for_input(user_input, _whitelist)` | 生产 | ✅ | 位置参数兼容，受 max_tools=25 默认限制 |
| `agent/orchestrator/orchestrator.py` | 941 | `get_tools_for_input(user_input, tools_whitelist)` | 生产 | ✅ | 同上 |
| `agent/orchestrator/task_dispatcher.py` | 49 | `get_tools_for_input(user_input, whitelist)` | 生产 | ✅ | TaskDispatcher 统一入口 |
| `scripts/apply_auto_keywords_and_test.py` | 219 | `get_tools_for_input(input_text)` | 脚本 | ✅ | 单参数兼容 |
| `scripts/apply_auto_keywords_and_test.py` | 255 | `get_tools_for_input(input_text)` | 脚本 | ✅ | 单参数兼容 |
| `scripts/diagnose_smart_tool_selection.py` | 50 | `get_tools_for_input(test_input)` | 脚本 | ✅ | 诊断脚本兼容 |
| `scripts/stress_test_pipeline.py` | 229 | `get_tools_for_input(input_text)` | 脚本 | ✅ | 压测脚本兼容 |

---

## 4. `TOOL_ALIASES` 引用点兼容性（Flowchart）

2 处非测试代码引用，全部为读取操作（统计/遍历），与填充后的 dict 完全兼容。

```mermaid
flowchart TD
    subgraph Ref[引用点 - 2 处]
        A1[apply_config_and_test.py:86,91<br/>len TOOL_ALIASES 统计]
        A2[scan_tool_aliases.py:14,232,235,260<br/>遍历 TOOL_ALIASES.items]
    end

    TA[TOOL_ALIASES dict<br/>3 个别名对:<br/>shell_execute → run_program<br/>read_file → read_pdf<br/>list_directory → list_processes]

    A1 -->|读取 len| TA
    A2 -->|读取 items| TA

    TA --> R1[返回 3 之前返回 0]
    TA --> R2[遍历 3 项 之前为空]

    R1 --> C1[✅ 兼容<br/>原逻辑 len==0 判定变更<br/>行为符合新设计]
    R2 --> C2[✅ 兼容<br/>原遍历空 dict 跳过<br/>现遍历 3 项 输出报告]

    classDef ref fill:#e1f5ff,stroke:#0288d1,stroke-width:1px
    classDef data fill:#f3e5f5,stroke:#7b1fa2,stroke-width:2px
    classDef ok fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    class A1,A2 ref
    class TA data
    class C1,C2 ok
```

### 引用点行为变化对比

| 文件 | 行号 | 之前行为（TOOL_ALIASES={}）| 现在行为（3 个别名对）| 影响 |
|---|---|---|---|---|
| `apply_config_and_test.py` | 86, 91 | `len(TOOL_ALIASES) == 0` | `len(TOOL_ALIASES) == 3` | 报告中显示 3 个别名（符合新设计）|
| `scan_tool_aliases.py` | 14, 232, 235, 260 | 遍历空 dict，无输出 | 遍历 3 项，输出 3 个别名对 | 扫描报告含 3 条记录（符合新设计）|

---

## 5. 发布历史（GitGraph）

```mermaid
gitGraph
    commit id: "..."
    commit id: "..."
    commit id: "f44b5bb9" tag: "功能实现 WIP" msg: "feat(tools): WIP 功能2 别名合并 + 功能3 工具数量限制"
    commit id: "..." msg: "其他会话 commit observability"
    commit id: "dc4b45e2" tag: "v2.0.0-feature-tools-router" msg: "docs(tools): 功能2+3 完整实现记录"
```

### Tag 信息

| 字段 | 值 |
|---|---|
| Tag 名称 | `v2.0.0-feature-tools-router` |
| Tag 类型 | 附注 tag（`git tag -a`）|
| 目标 commit | `dc4b45e2` |
| 创建日期 | 2026-07-19 |
| 包含内容 | 3 项功能增强 + 75 测试通过 + 8 处调用点兼容 |
| 关联 commit | `f44b5bb9`（代码实现）+ `dc4b45e2`（文档记录）|

---

## 三义自检

### 【不易】数据来源可靠
- 测试结果来自实际 pytest 执行输出（75 passed, 0 failed, 0 xfailed）
- 调用点来自全代码库 grep（6 处 get_tools_for_input + 2 处 TOOL_ALIASES）
- Tag 数据来自 `git show v2.0.0-feature-tools-router` 实际输出

### 【变易】图表可扩展
- 新增测试文件 → pie chart 添加新切片
- 新增调用点 → flowchart 添加新节点
- 后续 release → gitgraph 添加新 commit + tag

### 【简易】单一文档自包含
- 5 类图表集中在 1 个 Markdown 文件
- 每个图表配对比表，便于复制到 PR 描述或发布说明
- Mermaid 语法可在 GitHub / GitLab / VS Code 直接渲染
