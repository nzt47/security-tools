# `toolset_hash` 与裁剪保护（TASK-08 子工作流 E · E7 + E8）

> 对齐：v1.4 §11（`toolset_hash` / 会话重建）与 §7（工具集裁剪与职责划分）
> 交付：`agent/capregistry/toolset_hash.py`、`agent/capregistry/pruning.py`
> 测试：`tests/unit/test_toolset_hash.py`（38 例）、`tests/unit/test_tool_pruning.py`（38 例）
> 基线数据：2026-09-20，HEAD `ecdcafe4` + 本任务改动

---

## 一、E7 `toolset_hash`：实际 hash 范围（逐字段说明）

### 1.1 纳入（v1.4 §11 七项，逐项落地）

| # | 维度 | 取值 | 为什么必须纳入 |
|---|---|---|---|
| 1 | 工具名 | `CapabilityRecord.tool_name` | 工具体是**集合**，名字是主键；改名 = 换了一个工具 |
| 2 | 版本 | `CapabilityRecord.version` | 同名不同版是**不同契约**；不纳入就感知不到升级 |
| 3 | `input_schema` | `input_schema`（键排序规范化后） | 模型据它生成参数；变了必须让模型重新看到 |
| 4 | `description` | `description` | 模型据它**选择**工具；变了等于工具语义变了 |
| 5 | `llm_visible` | `¬internal ∧ reachable ∧ llm_callable ∧ callable_mode≠"manual"` | 见 §1.3（本仓库口径说明） |
| 6 | 权限 | `permission_level` + `needs_approval` + `risk` + `confirm_level` | "免确认 ↔ 逐次确认"是同名工具的两种世界 |
| 7 | 模型能力 | `agent/capregistry/modelcaps.py::model_capability(model)` | 不支持 tool calling 时工具集被清空（两个世界） |

hash 载荷形状：`{"model_capability": {...}, "tools": [entry, …]}`，
摘要 = `sha256(规范化 JSON)`。`entry` 的 6 个 per-tool 维度即上表 1–6，
第 7 维是"整批工具共享"的快照级维度。

### 1.2 🔴 排除：`health`（v1.4 §11 原文要求）

> **健康频繁变化不触发重建，仅过滤注入。**

写入 `EXCLUDED_FIELDS["health"]`（**排在排除表第一条**）与模块 docstring，理由三条：

1. **变化频率量级不同**：契约面只在发布/装配时变（一天 0 次），`health` 是运行时探针
   产物（一次 429 / 一个 5xx 就翻）。把 health 放进 hash ⇒ 每次健康抖动都"自动新建会话"
   ⇒ 用户会话被无端清空，而工具契约一个字没改。
2. **语义归属不同**：hash 回答"**模型看到的契约**变了吗"（会话生命周期问题）；
   health 回答"这个能力**此刻**能不能用"（单轮渲染问题）。混在一起 = 用会话重建
   去解决一个每轮都该解决的问题。
3. **可回滚性不同**：契约变了不可回退；health 下一轮可能自己好。

**health 的去处**：进 `ToolsetSnapshot.health`（快照但不参与 hash），
只通过 `RebuildDecision.health_changed` / `filtered_out` 影响**过滤与注入**。

### 1.3 其余显式排除项（如实列举，避免"看起来什么都算了"）

`impl_status` / `impl_status_reason`（运行时可用性事实，与 health 同级，只用于提示）、
`host_executor`（执行面接线细节）、`location*`（执行边界约束，不进模型可见契约；
扩展点：改 `HASHED_FIELDS` + `entry_of()` 一处即可）、`mark` / `main_line_status`
（装配期派生痕迹）、`spec_source` / `declared_in`（来源标记 —— 降级构建会改它，
但那不该重开会话）、`enabled`（由清单派生；可用性已由 `llm_visible` + health 表达，
避免同一事实两处判定）。

### 1.4 `llm_visible` 为什么不是 v1.4 的字面口径

v1.4 §5.1 把它拆成 `llm_visible`（可见性）与 `llm_invokable`（可调用性）。
本仓库的**实际**落点是 `agent/tools/__init__.py::_hidden_tool_names()`
= `internal` ∪ `non_callable_tool_names()`，而后者的判据是
`llm_callable == False` **或** `callable_mode == "manual"`。
若只按字面 `¬internal` 判定，则 `llm_callable: true → false` 这类**真实可见性变化**
不会改 hash ⇒ **该重建时不重建**。故取"实际可见集"，与 `get_tool_defs` 同一事实。

### 1.5 稳定性（与顺序无关）

`canonical()` 统一规范化：`dict` 按键排序、`set/frozenset` 排序、
`list/tuple` **保序**（`required` / `enum` 的顺序是 JSON Schema 语义）。
`build_entries()` 按 `(name, tenant_id)` 排序 ⇒ hash 与记录顺序无关。
测试用两路取证：① 同进程内反向/错位重排后 hash 相同；
② **两个不同 `PYTHONHASHSEED` 的子进程**算出同一 hash（跨进程可复现）。

### 1.6 会话重建语义

`SessionToolset.observe(source, *, model, health_provider)`：

| 情形 | `rebuild_required` | 动作 |
|---|---|---|
| 首次观测 | `False` | 建立基线（`generation=1`） |
| 契约未变、`health` 变了 | **`False`** | 只刷新 health 面 ⇒ `health_changed` / `filtered_out`；**换基但不换会话** |
| 契约变了 | `True` | `diff_entries` 逐字段说明 + `message`（含新会话键）⇒ **自动换基**（`generation+=1`，`session_id` 变 `s1#g2`） |

同 session 内缓存：键 `(源对象身份, model)`，**只缓存契约面**（hash+entries）——
health 每轮都可能变，缓存它等于缓存过期事实。

> 范围声明：**会话实体由宿主创建**。本模块不持有会话存储、不写任何数据文件，
> 只提供裁决（`rebuild_required` / `message`）与新会话键（`session_id`）。
> 仓库里没有可供挂钩的会话生命周期入口（`agent/session_manager.py` 不消费工具集），
> 故未做"真正新建会话"的接线 —— 这是**如实披露的范围边界**，不是遗漏。

---

## 二、E8 裁剪误伤率 = 0

### 2.1 判据（唯一实现：`agent/capregistry/pruning.py`）

```
risk >= high  **或**  confirm_level >= L2   ⇒  不参与裁剪
```

**为什么必须只有一处**：它被两条裁剪路径消费（工具级 token 预算裁剪、
主线装配结果裁剪）。两处各写一遍阈值 = 改阈值必漏一处
（TASK-06 的 `needs_approval` 曾在仓库里有三份手写副本）。
`tests/unit/test_tool_pruning.py::Test判据与规格一致` 在**全部 91 个真实工具**上与
"规格的直白写法"逐条对拍，任何不等价都会点名报出。

**未知风险 ⇒ 不裁**（刻意选择的失败方向）：名字不在 YAML 里（运行时注册的
MCP / 插件 / 生成工具）时不得假设可裁 —— 宁可少裁，不可误裁一个高危。
逃逸口只有"调用方显式声明可裁"（`prunable={...}`），**不新增 YAML 字段**
（避免给能力定义再开一个声明面，D1）。

### 2.2 裁剪路径盘点（**任务书描述与现状不符**，如实上报）

| 路径 | 落点 | 现状 |
|---|---|---|
| ① Schema 字段裁剪 | `agent/tool_schema_pruner.py::prune_schema` | 只截断描述 / 移除废弃**字段**，**不使工具消失** ⇒ 按设计不设保护 |
| ② 工具级移除（`deprecated: true`） | 同上 `prune_tool_defs` | 人工**显式淘汰声明**，实测 91/91 无 deprecated ⇒ 不属"裁剪误伤"，保留既有语义 |
| ③ **工具级 token 预算裁剪** | 新增：`pruning.prune_tool_defs_for_budget`，由 `prune_tool_defs(..., budget_tokens=)` 接线 | **此前不存在**（本任务补上），默认 `CP_TOOLSET_SCHEMA_TOKEN_BUDGET=0` = 不启用 |
| ④ 主线名额截断 | `agent/lines/assembler.py` 第 ⑤ 步 | ⚠️ **不可达**：第 ④ 步 `len(kept) >= cap` 即 `break` ⇒ 第 ⑤ 步拿不到可裁尾巴，`truncated` **恒为空**。本任务保留其保护接线（防御性），并新增**可达**的 ⑤′：`assemble(..., token_budget=N)`（默认 None = 不裁剪） |
| ⑤ 分页 `limit` | `capregistry/view.py::list_envelope` | 调用方显式分页参数，**不是**预算裁剪 ⇒ 不套用保护（套用会破坏 `returned ≤ limit` 的接口契约） |

### 2.3 实测规模（**两个口径都报**）

**口径一 · 全量**（`data/tool_definitions/*.yaml`，91 个工具）：

| 项 | 数量 |
|---|---|
| 工具 YAML 总数 | 91 |
| `risk >= high` | 16（13 high + 3 critical） |
| `confirm_level >= L2` | 20 |
| **受保护（并集）** | **20** |
| 其中仅由 `confirm_level` 命中（govern/extend 类） | 4 |

**口径二 · 生产实际（上限/截断）**，7 条内置主线全扫：

| 主线 | 工具数/上限 | 其中受保护 | 截断 |
|---|---|---|---|
| assistant | 18/18 | 1 | 0 |
| dev | 22/22 | 7 | 0 |
| digital_life | 18/18 | 1 | 0 |
| **engineering（激活）** | **26/26** | **8** | **0** |
| harness | 26/26 | 9 | 0 |
| knowledge | 20/20 | 1 | 0 |
| recon | 22/22 | 2 | 0 |

⇒ **生产上当前一次裁剪都没发生**（截断路径不可达 + token 预算默认关闭）。
"误伤率 = 0"因此在生产上是"零裁剪"的平凡结论；真正的验证必须在
**超预算场景**下做（下节），否则就是任务书点名的"假绿"。

### 2.4 超预算场景实测（含"裁剪确实发生"的证明）

真实 91 条 tool_defs 合计 **14053 token**（`estimate_def_tokens`，字符数 // 3，
与 `agent/context/assembler.py::estimate_tokens` 同一口径）。

**A. 工具级 token 预算裁剪**（`prune_tool_defs_for_budget`，预算 = 全量 60% = 8431）

| 指标 | 开启保护 | 关闭保护（反例对照） |
|---|---|---|
| 保留 / 裁掉 | 50 / **41**（裁剪确实发生） | 54 / 37 |
| 受保护**因判据而保留** | **7** | — |
| **受保护被裁（误伤）** | **0** | **7**（`run_program` / `run_sandbox` / `scan_mcp` / `schedule_task` / `shell_execute` / `workspace_delete` …） |
| **裁剪误伤率** | **0.0** | 规格集合口径下 7 个 |

**B. 装配层裁剪**（`assemble(engineering, token_budget=70%)`，预算 3973/5677）

| 指标 | 开启保护 | 关闭保护（反例对照） |
|---|---|---|
| 保留 / 裁掉 | 15 / **11** | 20 / 6 |
| 受保护被裁（误伤） | **0** | **3**（`fan_out` / `git` / `run_sandbox`） |

**C. 反向对照（防假绿）**：预算充足（`total+1000`）⇒ `applied=False`、
`dropped=[]`；元数据不可得（`meta={}`）⇒ **一个都不裁**；
预算极小（300）⇒ 只剩 20 个受保护项且 `over_budget=True`（**宁可超预算也不裁高危**）。

### 2.5 判据关闭两种"零误伤"必须可区分

保护关闭时 `BudgetPlan.protected_kept` 恒空、`misprune_rate()` 恒 0 ——
那是"**没有保护**"而不是"没误伤"。故账目同时披露 `applied` / `dropped` /
`reason`，并且测试里的误伤判定一律用**规格集合**（`risk`/`confirm_level` 直白写法）
而不是账目字段。这与任务书的告诫（"否则'零误伤'是假绿"）同源。

---

## 三、开关与回滚（D5：全部登记 `agent/settings/registry.py`）

| 开关 | 级别 | 默认 | 作用 | 回滚 |
|---|---|---|---|---|
| `CP_TOOLSET_PRUNE_PROTECT` | **B** | `1` | 裁剪保护总开关；置 0 ⇒ 退回无保护的旧裁剪行为 | 置 0（不写数据文件） |
| `CP_TOOLSET_SCHEMA_TOKEN_BUDGET` | A | `0` | 工具 schema 的 token 预算；≤0 = 不裁剪（既有行为零变化） | 置 0 |

`tests/unit/test_settings_registry.py`（零缺口硬守卫，27 例）**全绿**。

## 四、边界与未做项（如实登记）

1. **未改 `agent/tool_router.py`** 主路径（另一子工作流负责）。
2. **未创建 `docs/perf/` 下其它文件**（仅本文件）。
3. **未引新依赖**（仅 `hashlib` / `json` / `os`，全是标准库）。
4. **未动 `data/` 下 `.jsonl` / `.db`**；测试全部只读真实 YAML。
5. **未接"真正新建会话"**（见 §1.6 的范围声明）。
6. **未修第 ⑤ 步不可达**（改它会让既有断言
   `test_cap_is_respected_when_floors_fit` 变红 ⇒ 属行为变更，需另行裁定；
   现状已由 `test_名额截断路径当前不可达_如实记录` 钉住）。
