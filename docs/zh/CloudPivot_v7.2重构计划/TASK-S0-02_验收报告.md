# TASK-S0-02 验收报告（消化对象三层盘点确认）

> 归档日期：2026-09-09
> 所属计划：CloudPivot v7.2 重构计划（S0 对齐层）
> 任务：[TASK-S0-02_消化对象三层盘点.md](TASK-S0-02_消化对象三层盘点.md)｜依赖：TASK-S0-01（术语映射，联合评审见 §5）
> 主产出：[TASK-S0-02_消化对象三层盘点_结论.md](TASK-S0-02_消化对象三层盘点_结论.md)（完整盘点 + 映射矩阵）
> 审计缺陷：T8（01 号审计报告 §3）；联动 §6 差距矩阵消化对象行
> 状态：✅ 验收通过

---

## 一、总览

| 预期成果 | 交付 | 验收 | 备注 |
|---|---|---|---|
| 1. 三层资产盘点表（含规模与状态分布） | 结论 §2（§2.1/2.2/2.3 分层 + §2.4 汇总表） | ✅ | 规模数据 2026-09-09 运行态直读，可复核（复核命令见结论 §8.A） |
| 2. 状态机作用域矩阵（七态 × L1/L2/L3） | 结论 §3.2 + 特别裁决 §3.3/§3.4 + 可测性 §3.5 | ✅ | 逐格判定无"都/都不适用"含糊格 |
| 3. 层间转换器清单（现状钩子 + 门控/审批 + 缺口） | 结论 §4.1/§4.2 | ✅ | 每个转换点标注门控/质量门/审批 |
| 4. 消化对象映射矩阵章节（复核 01 §6 + 新缺口行 + 术语表输入） | 结论 §5.1/§5.2/§5.3 | ✅ | 供并入 S0-01 术语表 |
| 5. 本验收报告 | 本文件 | ✅ | — |

核心裁决：C1 三层边界 / C2 七态主语＝L1 能力演进轨 / **C3 L2 不入七态主轨（中间过渡载体）** / **C4 L3 现有状态机＝发布轨，与七态正交叠加（evolution.stage 新字段），存量零迁移** / C5 shadow/灰度新增落点 / C6 转移条件现状均不可测（S2/S3 前置）/ C7 消化对象范围供 S1（L1 为主 + L3 轻量视图）。

---

## 二、验收清单逐条核验

### ✅ 1. 三层资产盘点表覆盖 L1/L2/L3 全部现有载体，规模数据可复核

| 层 | 盘点到的全部现有载体 | 规模（2026-09-09） |
|---|---|---|
| L1 | 运行时工具注册表 `agent/tools._registry`（register/register_dynamic）、8 组 register_all 内置模块（core/file/web/ext/pdf/software/system/code）、规划工具、蒸馏工具、MCP 双路径（skills_mgmt/mcp_adapter → Skill 草稿 + tools/mcp_connector 动态注册）、mcp_services、工具发现服务（discovery_service）、schema 双轨（tool_definitions/*.yaml） | 内置 ≈80（静态 70＋规划 5＋蒸馏 3）；MCP server 配置 0、MCP 类技能 0 条；注册项仅 name/desc/handler/schema，trust/evolution 字段 0 覆盖（操作级风险仅 HITL/check_action 调用点强制） |
| L2 | workflow_learning 全链路（learner/generator/matcher/executor/skill_converter/repository）、process_distill 全链路（sources/distiller/merge/solidify）、data/learned_workflows.json、蒸馏产物（pd-*） | workflow 3 条（全 active/toolchain，demo-seed）；蒸馏 workflow 0 条；蒸馏 skill 15 条；质量门 5/0.7/50；孤儿引用 1 条（zip-d2968c59-skill） |
| L3 | skills_mgmt JSON 主轨、文件轨 skills_repo/<id>/skill.md、legacy 快照 skills.json、SkillRegistry 统一启停、分类注册表（asset:/rt:） | 主轨 21（approved 18/published 3）；文件轨 23 目录；权威并集 29；config_schema 21/21、output_schema 21/21 有键全空；review 21/21 passed；metrics.usage 全 0 |

> 复核：计数均来自直读 `data/` 存储 + 代码注册点静态统计，非估计值；命令见结论 §8.A。

### ✅ 2. 七态 × 三层的适用判定逐格给出且理由充分，无"都适用/都不适用"含糊格

作用域矩阵（完整版含理由列见结论 §3.2）：

| 七态 | L1 | L2 | L3 | 判定要点 |
|---|---|---|---|---|
| borrowed | ✅ | ❌ | ◐（外来导入特例） | L2 内生无外部上游；L3 draft≠borrowed（有条件近似） |
| mirrored | ✅ | ◐（产物载体） | ❌ | workflow＝候选模式宿主（§3.3.1） |
| shadow | ✅（需新建） | ❌ | ◐（部署轨灰度窗） | 判定集门 + 双跑 diff 属 L1；灰度属 L3 发布前置 |
| internalized | ✅（指向 L3） | ❌ | ✅（主轨） | 产物 SKILL.md+tests+签名（§3.3.1） |
| native | ◐ | ❌ | ✅（主轨） | 30 天零回退 + 每周探活 |
| permanent_borrowed | ✅ | ❌ | ◐ | opaque/commodity 豁免内化 |
| deprecated | ✅ | ◐（现有枚举） | ✅（复用现有） | L3 已实现 90d/180d 自动降级 |

### ✅ 3. L3 现有状态机与 v7.2 七态映射不破坏现有语义（存量资产状态不受影响）

- 现有 SkillStatus（draft→pending_review→approved/rejected→published→deprecated→archived）＝**发布/运营轨**（对应 v7.2 §4.5 Skill 版本态），语义原义保留；
- 七态经新增 `evolution.stage` 字段（v7.2 §3.7）**正交叠加**，不新增/不改任何现有 status 枚举值；
- 存量 21 条主轨 + 23 目录文件轨 + 29 条 legacy 快照**零状态迁移、零改名、零枚举变更**；
- "published≈internalized"仅作为触发近似（published 动作可作为 internalized 置位候选），存量 published 3 条**不自动获得** internalized/native 标记（无 30 天零回退/验收证据），全部留空待 S3 补验——保证现有语义不被污染。

### ✅ 4. 层间转换器清单标注了每个转换点的门控/质量门/审批点

| 转换 | 钩子 | 门控/质量门/审批 |
|---|---|---|
| L1→L2 | learner.py:85-99 / solidify.py:64-139 | 仅成功交互 + 有工具调用；蒸馏仅已注册工具步骤（白名单） |
| L2→L3 | skill_converter.py:81-187 | success_count≥5 ∧ confidence≥0.7 ∧ priority≥50 ∧ ACTIVE/enabled/未转换（QUALITY_GATE_FAILED）；幂等回写；**本路径无强制评审**（create_manual 落 draft；正式三审仅 process_distill 固化 run_review=True） |
| L1→L3 导入 | mcp_adapter.py:87-149 / creator.py install | SecurityScanner block_on_critical 硬门；外来预检 assessor（高风险 error 阻断）；原生重叠增量吸收（absorb_overlap）；PENDING_REVIEW/草稿人工放行；不自动 publish |
| 委派萃取 | subagent/ 占位 + distill 隔离 worker | 无八要素/回收三件套（P7 缺口，S4-04） |

### ✅ 5. 与 S0-01 术语映射表无交叉矛盾（联合评审通过，对实表）

- S0-01 交付物 [`术语映射表.md`](术语映射表.md)（Accepted）+ [`RFC-宿主形态与范围.md`](RFC-宿主形态与范围.md) 已于本任务期间产出，联合评审**对实表进行**（记录见结论 §6）；
- 核心裁决全部一致：三层定义、L2 不入七态（作 L1→L3 过渡轨）、七态主语（L1 能力轨）、digest 更名方向（评审→review/assess、七态→内化流水线）；
- **已消除 2 处交叉不一致**：① S0-01 术语表 L3 行 shadow/灰度措辞歧义 → 已做最小修订（灰度窗插 published 前，标注 S0-02 联合评审）；② 本文件初稿将"名片懒加载"误列缺口 → 已修正（loader/file_store/context_injector 三层按需实证）；
- 新增 1 处 S0-01 术语表待办：运行时 SOURCE_* 五类 vs SkillCategory 六类双来源枚举（验收报告 §四 #4）。

### ✅ 6. 盘点结论被 S1（契约层）与 S3（消化流水线）任务作为输入引用

- **TASK-S1-01**：其 §一 明确"对象范围为 TASK-S0-02 裁定的消化对象（L1 原子工具为主，L3 轻量 Descriptor 视图）"——即本任务 C7；其步骤 2 要求 "evolution.stage 映射自 TASK-S0-02 的状态机作用域矩阵"——即结论 §3.2/§3.4；
- **TASK-S1-02**：摸底输入＝结论 §2.3 统计（config_schema 21/21、output_schema 21/21 有键全空、risk/provenance/undo 全缺）与 §4.2 缺失清单；
- **TASK-S3-01/02/03**：输入＝结论 §3.5（转移条件可测性缺口）、§4.2（缺失转换器）、§5.2（分层缺口行：L1 缺 shadow/判定集门、L3 缺灰度窗/native 探活/evolution.stage、L2 缺样本门槛/轨迹清洗）。

---

## 三、关键新证据（本任务盘点相对 01 号审计报告 §6 的增量）

1. **七态零足迹实证**：`agent/**/*.py` grep `evolution_stage|borrowed|mirrored|shadow` 零命中——v7.2 状态机在云枢无任何运行时实现（审计 §6 原判 ◐ 实为 ❌ 起步）；
2. **digest=评审别名实证**：service.py:614-620；
3. **双轨存储权威结构**：JSON 主轨（21）权威 ＋ 文件轨独有（8，persona/示例）＝权威并集 29；legacy skills.json（29 行）＝并集的只读派生态快照（SkillRegistry 统一启停视图，registry.py:1-22）；
4. **L2→L3 孤儿引用实证**：zip-d2968c59 的 converted_to_skill_id 指向不存在资产；
5. **指标空转实证**：21 条技能 metrics.usage/success 全 0——v7.2 六条件触发（§4.5.1 样本≥50/月≥200）在云枢现状**无计量基础**（对 T2 门槛问题的数据侧证实）；
6. **双来源枚举并存**：运行时 SOURCE_* 五类 vs SkillCategory 六类（builtin 同义、其余不同名不同义）——记入 S0-01 术语表待办。

---

## 四、遗留清单（不阻塞本任务验收）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | S0-01 验收报告须回引本结论 §5.3/§3.2 收口联合评审（术语表已产出，评审已完成） | S0-01 |
| 2 | S1-01 若调整 L3 轻量视图映射，按 TASK-S1-01 §四 回写本结论 | S1-01 |
| 3 | 孤儿引用 zip-d2968c59-skill 的处置（清理或补资产） | S3-01（或临时数据修复） |
| 4 | 双来源枚举（SOURCE_* / SkillCategory）统一或显式映射 | S0-01 术语表 + S1-01 |

---

## 五、文件清单

| 文件 | 内容 |
|---|---|
| `TASK-S0-02_消化对象三层盘点_结论.md` | 主产出：三层盘点表 / 状态机作用域矩阵与裁决 / 转换器清单 / 映射矩阵 / 联合评审 / 证据附录 |
| `TASK-S0-02_验收报告.md` | 本文件：验收清单核验 + 增量证据 + 遗留清单 |
