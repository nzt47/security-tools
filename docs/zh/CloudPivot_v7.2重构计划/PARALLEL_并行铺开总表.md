# PARALLEL 并行铺开总表（v7.2 重构 · S4/S5 批次）

> 用途：把本轮可并行的任务**一次性铺开**分发。每个任务配一份 `START-<ID>_*.md` 分发壳（§二 整段复制给对应新会话）。
> 命名约定：`TASK-*.md` = 任务规格（权威）；`START-*.md` = 该任务分发壳；本文件 = 批次与通用约定（所有会话共用）。
> 生成基线：`master` / `15eae00d`｜生成时**无其他会话 worktree 占用**、工作树干净、与 `origin/master` 同步。
> 生成日期：2026-09-11

---

## 一、批次划分（最大化并行为准）

| 波次 | 任务 | worktree id | 依赖 | 可并行性 |
|---|---|---|---|---|
| **第一波**（立即可开） | S4-01 审批矩阵与审批面安全 | `s401` | S2-02/S2-03 ✅ 已结案 | ✅ 无摩擦 |
| 第一波 | S4-02 策略即代码 | `s402` | S2-02/S2-03 ✅ | ✅ 无摩擦 |
| 第一波 | S5-01 记忆四层与租户隔离 | `s501` | S2-01 ✅ | ✅ 无摩擦 |
| 第一波 | S5-02 评测锚与基线 | `s502` | S2-03 + S3 ✅ | ✅ 无摩擦（关键路径枢纽） |
| 第一波 | S5-03 成本刹车与断食 | `s503` | S2-03 ✅ | ✅ 无摩擦 |
| **第二波**（待 S4-01 结案） | S4-03 熔断回滚与 Saga | `s403` | S2-02 + **S4-01** | ⚠ 依赖 S4-01 的审批落点 |
| 第二波 | S4-04 subagent 真实现 | `s404` | S2-01 + **S4-01** | ⚠ 依赖 S4-01 的 Actor 矩阵/授权清单 |
| **末波** | S6-01 六面板扩展 | `s601` | S2–S5 | ❌ 待 S5 全部结案（数据源齐备）后启动 |

**依赖图**
```
S2 ✅ ─┬─ S4-01 ─┬─ S4-03（第二波）
       │         └─ S4-04（第二波）
       ├─ S4-02（第一波）
       ├─ S5-01（第一波）
S3 ✅ ─┼─ S5-02（第一波，关键路径枢纽）
       └─ S5-03（第一波）
                    S5 全部 ✅ ─→ S6-01（末波）
```

## 二、文件落点与冲突提示（决定谁能真并行）

| 任务 | 主要落点 | 已知重叠风险 |
|---|---|---|
| S4-01 | `agent/skills_mgmt/approval.py`、`agent/human_in_the_loop/`、`agent/server_auth.py`、`agent/server_routes/`、前端审批区 | 与 S4-03 有**接口**耦合（审批落点），非文件重叠 |
| S4-02 | **新建** `agent/policy/`、`agent/guardrails/`（接线）、网络/权限网关 | ⚠ 与 S4-03 在 `agent/guardrails/` **重叠**（S4-02 策略决策接线 vs S4-03 注入防御六机制）→ 分属两波，天然错开 |
| S4-03 | `agent/self_healing/`（新增 saga.py）、`agent/monitoring/`、`agent/guardrails/` | ⚠ 与 S5-03 在 `agent/monitoring/` **重叠** → 若 S5-03 已结案再开 S4-03（推荐），或约定"S4-03 只改 self_healing/ 与 guardrails/，monitoring/ 只读" |
| S4-04 | `agent/subagent/`（container/lifecycle/sandbox/delegation） | 与第一波无重叠 |
| S5-01 | `agent/memory/`、`agent/knowledge/`、`agent/skills_mgmt/memory_abstractor.py` | 与第一波无重叠 |
| S5-02 | `tests/`（L0 锚用例新目录）、`scripts/report_slo_weekly.py` | 只新增，冲突风险最低 |
| S5-03 | `agent/monitoring/`（成本）、`data/cost_daily.json` | ⚠ 见 S4-03 行 |

> **并行的真正瓶颈不是 git（worktree 已隔离），而是"同一文件的语义冲突"。** 上表列为"无摩擦"的组合可放心同时开工；带 ⚠ 的组合已用波次错开。

## 三、通用执行约定（每个会话都适用）

**工作区隔离（强制）**
```powershell
cd C:\Users\Administrator\agent
python scripts/dev/new_session_worktree.py create --id <你的id，如 s401> --base master
# ⚠ --base 默认 develop，本项目在 master，必须显式指定
# ⚠ id 必须形如 s<数字>（s401 合法）；否则 cleanup 会拒绝执行
# 之后所有 git 操作都在 .worktrees/<id>/ 内
```
主工作区禁令：✗ `git checkout <branch>`　✗ `git reset --hard`　✗ `git add -A / add .`　✓ 只 `git add <具体文件>`
（规范全文：`docs/zh/并行会话worktree隔离规范_20260815.md`）

**提交流程**：worktree 内 add → commit → 回主工作区 `git merge <id>/main`（或 cherry-pick）→ 双远端推送同点 → `cleanup <id>`
```powershell
git push origin master && git push gitee master
# origin 若被 CI 自动提交（docs(architecture) 依赖图）顶住：git fetch → git pull --rebase origin master → 重推
```

**本地门禁（推送前必跑）**
- 本任务相关套件 + 邻接回归（见各 START 包指定）
- kwarg 扫描**两条都要**：`--path agent --min-risk HIGH` 与 `--path tests --min-risk HIGH`
- `mypy` 新增模块 + 既有阻塞模块（`agent/env_config_manager.py`、`network_config.py`）
- `importlinter lint --config .importlinter`（期望 kept / 0 broken）
- 真实提交场景验证 pre-commit（勿无说明 `--no-verify`）
- 跑完门禁后 `git status`：门禁脚本产物可能漂移，需还原

**通用硬约束（守不易）**
1. 不改既有公开接口签名与行为；新增机制失败不得阻断主流程
2. 一切自动化开关**默认关闭**，需显式阈值才开；destructive 必审批
3. 单测覆盖率 ≥80%；既有全量回归不得降级；可调参数走 `.env`，非法值回退默认
4. 术语纪律：v7.2「消化」= 内化流水线；云枢既有"评审"语义已在 S0-01 更名为 `review/assess`
5. **口径纪律**：真实流量未达"每能力 ≥20 条同类轨迹"，不得声称"已实现真实能力内化"
6. 涉及落盘存储的用例必须**显式传路径或加 autouse 会话级隔离**（S3-02/S3-03 两次踩坑）
7. 造数型用例（批量 create/write 30+）主动加 `@pytest.mark.timeout`（CI 分片高负载会误判）

**统一回报格式**（结案时给 Owner）
1) 交付物清单（文件路径 + 行数/用例数）2) 任务书 §四 验收清单逐条核验（含证据命令）3) 质量证据（新增单测数/覆盖率、邻接回归、CI 终态 SHA + 全绿 job 数）4) 遗留问题（逐条带归属与阻塞性判定）5) `00_总览` 状态行更新内容 6) 双远端推送终态 SHA

## 四、当前待 Owner 的人工动作（**已按 Owner 决定压后**，不阻塞任何技术任务）

| # | 事项 | 命令 | 影响 |
|---|---|---|---|
| 1 | S3-03 的 M5 人工抽检复核 | `python scripts/demo_s3_03_internalize.py --review-sheet` → `--record-review --verdict pass --reviewer <Owner> --review-note "…"` | 按口径"未复核不视为已验收"，不影响后续开发 |
| 2 | S1-02 的 NEEDS_REVIEW 7 条/6 资产逐条复核 | 手动 promote 通道复用同一 approval | 决定内化条件⑥（隐私闸门）；未分级/受限等级从严一票否决 |

> 两项均为**人工判断**，与 S4/S5 的技术任务无依赖关系，已按 Owner 指示压后。

## 五、分发清单

| 任务 | 分发壳 |
|---|---|
| S4-01 | [START-S4-01_审批矩阵与审批面安全.md](START-S4-01_审批矩阵与审批面安全.md) |
| S4-02 | [START-S4-02_策略即代码.md](START-S4-02_策略即代码.md) |
| S4-03 | [START-S4-03_熔断回滚与Saga.md](START-S4-03_熔断回滚与Saga.md) |
| S4-04 | [START-S4-04_subagent真实现.md](START-S4-04_subagent真实现.md) |
| S5-01 | [START-S5-01_记忆四层与租户隔离.md](START-S5-01_记忆四层与租户隔离.md) |
| S5-02 | [START-S5-02_评测锚与基线.md](START-S5-02_评测锚与基线.md) |
| S5-03 | [START-S5-03_成本刹车与断食.md](START-S5-03_成本刹车与断食.md) |
| S6-01 | 末波启动时另出（依赖 S5 全部结案） |
