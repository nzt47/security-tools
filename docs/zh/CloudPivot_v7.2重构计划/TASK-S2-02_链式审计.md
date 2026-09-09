# TASK-S2-02 链式审计（AuditLog prev_hash / self_hash / 验签 / 审计平权）

> 所属阶段：S2 数据与可观测层｜依赖：S2-01（Trace/事件源）｜预估：5–8 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.5（AuditLog 链式哈希 + 每日 Merkle 根）/P7.2-24（审计平权：UI 与 Agent 同表）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把云枢现有的**可追加 JSONL 审计**（`agent/audit/logger.py`——仅 sha256 摘要无 prev_hash 链；skills 评审 JSONL、`data/approval_records.jsonl` 等分散事件文件）升级为 v7.2 §3.5 的**防篡改链式审计**：

1. **链式哈希**：每条记录含 `seq（单调递增）/ ts / actor / action / subject / payload_hash / prev_hash / self_hash`，self_hash = sha256(seq+ts+actor+action+subject+payload_hash+prev_hash)；追加即链，改中间任意一条即破坏后续全部 self_hash。
2. **审计平权（P7.2-24）**：UI 操作日志与 Agent 操作日志**同一张审计表**——否则攻击者走管理后台即绕过治理。需盘点现有 UI 写操作（技能中心/审批/设置类路由）并接入统一审计入口。
3. **每日 Merkle 根 + 外部只追加（单机降级）**：按 v7.2 §3.5 生成每日根哈希；单机 Local-First 无外部存储时，降级为"每日根哈希写入受保护文件 + 校验器"（对齐 P4 分级实施：外部只追加存储入 P5 Backlog），并提供验签 CLI。
4. **验签工具**：`verify_chain()`——从任一锚点重算全部 self_hash 并比对，报告篡改位置；供混沌演练（§11.10"向审计链注入一条篡改"）使用。
5. **兼容迁移**：存量 JSONL 审计只读归档（不删除、不追溯），新增链式轨；按 §3.2 迁移策略"旧库只读 + 双写过渡 ≥1 版本 + 回滚窗口"。

## 二、执行步骤

### 步骤 1：盘点现状审计写入面
- 全量盘点现有审计/事件写入点：`agent/audit/logger.py`（AuditLogger.log）、skills_mgmt 评审/审批 JSONL（`data/skills_assessment_events.jsonl`、`approval_records.jsonl`）、EVO/进化谱系（`evolution_archive.jsonl`）、以及**UI 操作**（前端调用的写路由：技能 CRUD/发布/删除、审批 approve/reject、设置修改——参考 `server_routes/*` 各写接口）。
- 输出 `审计写入面清单`（写入方 × 载体 × 事件类型 × 是否含 actor/action/subject × UI/Agent 归属）。

### 步骤 2：链式审计模型与单写者
- 新增 `agent/audit/chain.py`：`AuditEntry`（含 §3.5 全部字段）、`AuditChain`（append 单写者 + 读取 + verify_chain + daily_merkle_root）。
- 存储：沿用 SQLite（与 tool_trace 同库或独立 audit.db，遵循 WAL + 单写者纪律 §5.5）；**主进程唯一 Ledger writer**，Scheduler/Watchdog/后台线程只读或 IPC 提交。
- seq 单调：单写者内递增；进程重启后从持久化最大 seq 继续。
- 性能目标：单条 append <5ms（对齐 §11.2 轨迹/审计预算的量级），批量异步写入。

### 步骤 3：审计平权接入
- 统一审计门面 `audit.record(action, actor, subject, payload, source="agent|ui")`：
  - Agent 侧：S2-01 的 Trace 关键事件、审批 submit/approve/reject、状态机 stage 变更、策略变更、回填（S1-02 backfill 已有审计，改接到新门面或双写过渡）；
  - UI 侧：写路由统一包一层（decorator 或 middleware），把当前登录用户/会话作为 actor 记录——UI 与 Agent 同表同格式。
- 存量 JSONL 只读归档：新增链式轨后，旧 JSONL 停止追加（或双写 ≤1 minor），旧文件仅读。

### 步骤 4：每日 Merkle 根与验签 CLI
- `daily_merkle_root(date)`：对当日 entries 建 Merkle 树，根哈希写入 `data/audit/daily_roots.jsonl`（含 date/root_hash/自签或 ed25519 签名如环境可用；无 Keychain 时先 sha256 自签占位并记录降级）。
- CLI/脚本 `scripts/verify_audit_chain.py`：全链重算比对，输出 OK 或首个篡改 seq；纳入混沌演练清单项。
- 演练：向链中间注入一条篡改 → verify 报告具体位置（验收演示）。

### 步骤 5：回归与归档
- 回归：audit 相关既有套件 + skills 审批/评审套件零回归；新增单测（链式不变量、篡改检测、seq 单调、UI/Agent 同表、每日根、单写者并发）≥40 例、覆盖率 ≥80%。
- 撰写 `TASK-S2-02_验收报告.md`。

## 三、预期成果

1. `agent/audit/chain.py`：AuditEntry/AuditChain/verify_chain/daily_merkle_root。
2. 统一审计门面（agent+ui 同表）与写路由接入。
3. 存量 JSONL 只读归档 + 双写过渡兼容。
4. `scripts/verify_audit_chain.py` 验签 CLI。
5. 篡改注入演练通过记录 + `TASK-S2-02_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] self_hash 公式与 §3.5 一致；篡改中间任意一条，后续全部校验失败且定位到注入 seq
- [ ] 单写者约束生效（并发写测试无重复 seq/竞态）
- [ ] UI 操作（至少审批/技能删除/设置写）与 Agent 操作同表可查（平权演示）
- [ ] 每日 Merkle 根生成并可重放验证；无外部存储时降级路径明确记录
- [ ] 存量 JSONL 只读归档，无追溯性丢失；双写过渡期内新旧轨一致
- [ ] 混沌演练"注入篡改"项通过（verify CLI 报告）
- [ ] 既有 audit/审批/评审套件零回归；新增单测全绿、覆盖率 ≥80%
- [ ] 单条 append 性能达标（≥§11.2 量级内，实测记录）
