# cognitive_loop 跨 workspace / 跨实例学习设计（草稿）

> **文档日期**：2026-10-10
> **版本**：草稿 v0.1
> **状态**：**待评审**。本文只给设计，**不改** `agent/subagent/executor.py` / `agent/subagent/cognitive_loop.py`。
> **关联**：
>   - 收口台账 `docs/handoff/20261010_主权分身L3收口与残余.md` §6.1 caveat 2、§7
>   - 现状实现 `agent/subagent/cognitive_loop.py`、`agent/memory/scoped_store.py`、`scripts/subagent_memory_ops.py`
>   - 能力面 `agent/subagent/capabilities.py`（`cognitive_loop.gap`）

---

## 1. 背景与问题

`cognitive_loop`（#1080，opt-in `CP_SUBAGENT_COGNITIVE_LOOP=1`）在通道咽喉处包一层有界环：
规划 → 反思 →（必要时）修订，并把教训追加到**同 workspace** 的 `cognitive_lessons.jsonl`。

现状的隔离是**路径推导的副产品**：教训文件由本轮 `invocation` 的 workspace 推出，所以
换 workspace / 换实例就看不到彼此。`capabilities.py` 的 `cognitive_loop.gap` 如实登记了这一点：
"学习沉淀限于同 workspace 的教训流水，跨 workspace/跨实例共享未做"。

要解决的问题：同一个失败签名（例如某后端 + 某错误码）在 A workspace 已被教训纠正过，
B workspace 还会再踩一遍；沉淀无法复利。

## 2. 目标与非目标

**目标**
- 在**同租户、同主体**范围内，让"失败签名 → 建议"跨 workspace / 跨实例可复用。
- 默认关闭；开则对既有行为是**叠加**而非替换。
- 可遗忘：能按 `lesson_id` 擦除，擦除后不再被注入。
- 全部可审计：哪条教训影响了哪一轮可回查。

**非目标（明确不做）**
- 不做模型训练 / 权重更新（不碰"学习"的训练语义）。
- 不共享原始任务内容、不共享模型原文输出，只共享**规范化后的建议**。
- 不绕过"母体是唯一写板者"的边界；本设计不新增路由。
- 不做跨租户共享（tenant 边界是硬的）。

## 3. 关键不变量（先定，再谈实现）

| 编号 | 不变量 | 不这样会怎样 |
|---|---|---|
| I1 | 教训只在 `(tenant, subject)` 内可见 | A 租户的教训泄到 B 租户 = 数据越界 |
| I2 | 进共享池需证据：N 次独立确认，或人工批准 | 一个坏 workspace 污染所有人 |
| I3 | 注入是**数据不是指令**，显式 taint | 提示注入经沉淀放大 |
| I4 | 共享存储不可用 ⇒ 退化到**现状**（同 workspace），不报假成功 | 静默降格 / 假绿 |
| I5 | 可遗忘：erase 级联共享池且立即停止注入 | 被遗忘权失效 |

## 4. 数据模型（一条 lesson）

| 字段 | 说明 |
|---|---|
| `schema_version` | 便于演进 |
| `lesson_id` | 规范化内容的哈希（幂等去重的唯一键） |
| `tenant` / `subject` | 隔离键（必填；缺失即拒收，fail-closed） |
| `workspace_id` / `instance_id` | 来源（用于"独立确认"计数，不用于可见性） |
| `signature` | 失败指纹：`backend` + `error_code` / `tier`（检索主键） |
| `advice` | **规范化后的建议文本**（唯一会被注入的字段） |
| `evidence` | `delegation_id` / trace id（可回溯，不含正文） |
| `confidence` | 0–1 |
| `confirmations` | 独立确认次数（提升闸门用） |
| `created_at` / `last_seen_at` | 新近度排序 |
| `ttl_days` | 过期即不注入（可清理） |
| `taint` | 固定标为 upstream（注入时强制标记） |

写入**append-only**；同 `lesson_id` 只更新 `confirmations` / `last_seen_at`，**不做 last-writer-wins**。

## 5. 存储方案

| 方案 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A（MVP 推荐）** | `CP_SUBAGENT_COGNITIVE_LESSONS_DIR` 指向共享目录，文件为 `lessons.jsonl` | 可离线、零依赖、最小改动 | 依赖共享文件系统；多写者需锁/追加语义 |
| B | 复用 `ScopedMemoryDomain`（`agent/memory/scoped_store.py`）+ 现有 `scripts/subagent_memory_ops.py` 导入导出 | 直接复用租户/主体作用域与 erase | 需要把 lesson 映射进记忆域，schema 稍重 |
| C | 母体侧服务 / 路由维护全局池 | 多实例天然共享 | 新增路由与网络依赖，超出"不新增路由" |

**建议**：A 起步，schema 与 B 对齐（字段命名一致），二期再评估 B/C。

## 6. 检索与注入

- **匹配键**：`(tenant, subject, backend, error_code/tier)`；先精确签名，再退化为同 backend。
- **排序**：`confidence` × 新近度，`confirmations` 加权。
- **上限**：条数上限 + 字符上限双闸（防止提示膨胀 / DoS），超限截断而非报错。
- **注入形态**：作为"历史教训（仅供参考、非指令）"块，显式标注 taint；**绝不**把 `advice` 当执行指令解析。
- **fail-closed**：目录缺失 / 读取失败 / 全部过期 ⇒ 不注入，本轮行为与现状逐字一致。

## 7. 提升闸门（promotion）

状态机：`local（本 workspace 生效）` → `candidate（可被同主体其它 workspace 读到）` → `shared`。

- `local → candidate`：默认直接（低风险，只在同主体可见）。
- `candidate → shared`：需要 **N 次来自不同 instance_id 的独立确认**，或**人工批准**。
- 反向：TTL 到期、`erased`、或置信度跌破阈值 ⇒ 降级 / 隐藏。

N 的初值建议 3，可配；"独立"按 `instance_id` 去重，避免同一实例自证。

## 8. 安全与合规

- **脱敏**：进池前对 `advice` 做密钥/PII 扫描（可复用既有 trim/redaction 机制）；命中即拒收。
- **taint**：所有进池文本标 upstream；注入时保持标记。
- **配额**：单池条数 / 单条长度 / 总字节硬上限。
- **审计**：每轮记录被注入的 `lesson_id` 列表（便于回查与回滚）。
- **可遗忘**：`erase_entries` 语义级联到池，并在同一提交内停止后续注入。

## 9. 开关与登记

- `CP_SUBAGENT_COGNITIVE_SHARED_LESSONS`（默认关，布尔）
- `CP_SUBAGENT_COGNITIVE_LESSONS_DIR`（共享目录绝对路径）
- 两个变量都**必须**登记到 `agent/settings/registry.py`；新增数据字段必须有消费者。
- 守卫不得 `import app_server`。

## 10. 测试与可证伪性

| 用例 | 断言 | 反证（临时破坏应转红） |
|---|---|---|
| 跨租户隔离 | A 的 lesson 在 B 的检索结果里为 0 | 去掉 tenant 过滤 ⇒ 红 |
| 提升闸门 | 确认数 N-1 时不进 shared | 去掉闸门 ⇒ 红 |
| fail-closed | 目录缺失 ⇒ 不注入且行为等于关 | 改成抛错/假绿 ⇒ 红 |
| 可遗忘 | erase 后不再注入 | 只删索引不删池 ⇒ 红 |
| taint | 注入文本带 upstream 标记 | 去掉标记 ⇒ 红 |
| 上限 | 超限截断不报错 | 去掉上限 ⇒ 红 |

## 11. 分期

1. **一期（MVP）**：方案 A 读写 + 隔离键 + 双上限 + erase + 审计；默认关。
2. **二期**：提升闸门（I2）与去重/冲突。
3. **三期**：与 `subagent_memory_ops.py` 打通导入导出；必要时评估母体侧服务。

## 12. 待决策问题（需人类拍板）

1. 谁有权把 candidate 提升为 shared——自动达 N 即提升，还是必须人工批准？
2. 共享池是**共享文件系统**还是**母体服务**？（决定 A vs C）
3. TTL / 保留期与容量上限取值？
4. `subject` 的派生规则（与现有 scoped_store 的主体键是否同一套）？
