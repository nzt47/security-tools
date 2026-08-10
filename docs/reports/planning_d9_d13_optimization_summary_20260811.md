# D9 持久化落地 + D13 预算优化总结报告

> 生成日期: 2026-08-11
> 范围: planning 模块 D9（SQLite 持久化）与 D13（token/cost 预算 + 硬超时）实现总结
> 关联: [persistence.py](../../planning/persistence.py) · [react.py](../../planning/react.py) · [react.py 模型](../../planning/models/react.py)

## 1. D9：SQLite 持久化落地

### 1.1 实现

| 项 | 内容 |
|---|---|
| 新增 [persistence.py](../../planning/persistence.py) | `PlanDB` 访问层：plans / plan_tasks / execution_log 三表；单连接 + 线程锁互斥（asyncio 并发写安全） |
| 存储介质 | `sqlite3` 标准库（零第三方依赖）；默认 `data/plans/plans.db` |
| 迁移 | `migrate_from_json`：旧 JSON 检查点目录首次启动幂等导入（库非空则跳过） |
| 后端切换 | `core.save_plan_checkpoint` / `_load_plans_from_disk` 接口语义不变，内部切 SQLite |
| 审计埋点 | `executor._record_execution` 追加 execution_log（action_type/tool/success/output/error），失败仅告警 |
| 配置 | `config.yaml` 新增 `planning.persist_db` / `persist_dir` |

### 1.2 表结构

- `plans`：计划主表（状态/进度/结果/错误/context JSON）
- `plan_tasks`：子任务表（payload JSON，模型扩展不迁移表）
- `execution_log`：执行记录审计（时间线可追溯）
- 索引：`plans(state)`、`execution_log(plan_id, created_at)`

### 1.3 验证

- d9 复现测试：SQLite 落库文件存在 + 重启恢复未完成计划 ✅
- capability 持久化恢复规格：启用（5/5 能力基线全过）✅

## 2. D13：token/cost 预算优化 + 硬超时

### 2.1 实现

| 项 | 内容 |
|---|---|
| deadline 预算 | 既有（迭代级检查），本批保留 |
| token 预算 | 新增 `token_budget`：`_think` 后累计估算 token，迭代级检查超限终止 |
| cost 预算 | 新增 `cost_budget`：`token_used / 1000 × token_price_per_1k`（默认 $0.002/1k） |
| 硬超时 | 新增 `tool_timeout_seconds`：异步工具调用 `asyncio.wait_for` 包裹，超时中断返回失败 |
| 可观测 | `ReActResult` 新增 `token_used` / `cost` 字段，`to_dict` / `summary` 透出 |
| 配置 | `token_budget` / `cost_budget` / `token_price_per_1k` / `tool_timeout_seconds` 四个 env 化配置 |

### 2.2 token 估算方法（已知限制）

- 方法：`len(text) // 3`（中英混合近似），累计 prompt + 响应
- 原因：`llm.chat` 返回纯文本（无 usage 结构），估算为通用零依赖方案
- 限制：估算精度低于真实 usage；后续若 LLM 层透出 usage 可切换精确统计（接口已预留）

### 2.3 已知限制

- **同步工具无法硬超时**：同步调用阻塞事件循环，`wait_for` 无法中断；由迭代级 deadline 兜底（已注释说明）

### 2.4 验证

- d13 复现测试扩展：deadline / token / cost / 硬超时 4 个用例全过（4 passed）
  - token 预算超限 → `超出token预算(N/M)` 终止
  - cost 预算超限 → `超出成本预算($x/y)` 终止
  - 10s 慢工具 + 0.05s 硬超时 → 工具调用被中断，总时长 < 10s

## 3. 测试数据

| 套件 | 结果 |
|---|---|
| planning 全量（tests/unit -k planning） | **191 passed, 1 xfailed, 1 skipped**（0 failed） |
| d13 专项（预算 + 硬超时） | 4 passed |
| capability_baseline | 5/5 能力基线全部启用 |

## 4. 结论

- D9：SQLite 落库闭环（计划/任务/执行记录），JSON 检查点平滑迁移，接口向后兼容。
- D13：三层预算（deadline/token/cost）+ 异步工具硬超时 + 预算可观测透出，ReAct 循环的资源防护完整。
- 遗留：token 估算精度、同步工具硬超时不可达（均记录为已知限制，非缺陷）。
