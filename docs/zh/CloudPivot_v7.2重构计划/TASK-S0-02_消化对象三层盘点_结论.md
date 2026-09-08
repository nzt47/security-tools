# TASK-S0-02 消化对象三层盘点确认（结论与映射矩阵）

> 所属阶段：S0 对齐层｜依赖：TASK-S0-01（术语映射，联合评审见 §6）｜预估：2 人日
> 来源设计文档：`C:\Users\Administrator\Desktop\设计思路\CloudPivot_v7.2_final_合并归档(智谱审核版).md` §3.1/§3.3/§3.3.1/§3.7（审计缺陷 T8，见 01 号审计报告 §3 行 T8）
> 盘点基准：云枢（Yunshu）代码库 `C:\Users\Administrator\agent`（运行态截至 2026-09-09）
> 产出：`TASK-S0-02_验收报告.md`（同行归档）
> 文档状态：✅ 完成（2026-09-09）

---

## 0. 裁决速览（TL;DR）

| # | 裁决点 | 结论 |
|---|---|---|
| C1 | 三层边界 | L1 原子工具/Capability＝运行时工具注册项；L2 工作流序列＝LearnedWorkflow/DistilledProcess 中间形态；L3 SKILL.md 编排资产＝skills_mgmt 技能（JSON 主轨 + 文件轨 skill.md） |
| C2 | v7.2 七态主语 | 七态是**能力内化演进轨**，主语＝v7.2 `cp.<source>.<upstream>` capability（对应云枢**尚无**的 L1 能力级对象），演进终点产物落在 L3 SKILL.md（§3.3.1 internalized 验收物＝SKILL.md+tests+签名） |
| C3 | L2 是否入态 | **不入七态主轨**：L2 是 L1→L3 的"轨迹→候选模式"中间载体（＝v7.2 mirrored 产物的宿主与 internalized 前置），用自身质量阶梯（confidence/success_count/一次升格 converted_to_skill_id）管理；borrowed/shadow/permanent_borrowed 语义对无外部上游的 L2 不成立（理由见 §3.3） |
| C4 | L3 现有状态机映射 | 现有 SkillStatus（draft→pending_review→approved/rejected→published→deprecated→archived）是**发布/运营轨**（＝v7.2 §4.5 Skill 版本态的同构物），**不等于**七态；七态经新增 `evolution.stage` 字段与现有 status **正交叠加**，存量 21 资产状态零迁移零改名（§3.4） |
| C5 | 新增落点 | shadow/灰度：L1 演进轨落 shadow 判定集门；L3 部署轨在 published 前加灰度窗（不新增 status 枚举值，避免破坏存量语义/UI）；internalized/native 落 L3 evolution.stage 值；borrowed/permanent_borrowed 挂 L1 外部来源轨 |
| C6 | 转移条件可测性 | v7.2 全部转移条件（轨迹≥20/判定集/灰度 72h/30 天零回退）在云枢**现状均不可测**——无统一 Trace 计数、无判定集资产、metrics.usage 全 0、无灰度设施；需 S2（遥测）与 S3（判定集/灰度）前置（§3.5） |
| C7 | 消化对象范围（供 S1） | **L1 原子工具为主** + **L3 资产以轻量 Descriptor 视图承载**（与 TASK-S1-01 §一 裁定一致） |

---

## 1. 盘点方法

- **数据侧**：直读 `data/` 各存储文件并脚本统计（计数可复核，复核命令见 §8.A）；
- **代码侧**：核对 `agent/tools/`（运行时工具注册）、`agent/skills_mgmt/`（L3 资产 + 评审语义 + MCP 桥）、`agent/workflow_learning/`、`agent/process_distill/`（L2 链路）、`agent/orchestrator/lifecycle_manager.py`（内置工具启动注册）、`data/skills_repo/`（文件轨）；
- **文档侧**：对照 v7.2 设计文档 §2.3/§2.4/§3.1/§3.2/§3.3/§3.3.1/§3.7/§3.9/§4.5/§4.5.1 与 01 号审计报告 §3/§6；
- 三方交叉（设计文档 / 代码实现 / 运行数据）后给出每条结论，标注证据 file:line 或存储文件名。

> 术语纪律（与 TASK-S0-01 一致）：本文件将云枢现有 `digest`（＝review/评审语义，证据 `agent/skills_mgmt/service.py:614-620` "即 review() 的语义别名"）一律称**评审（Review）/评估（Assess）**；v7.2 七态机制一律称**内化流水线（Internalization）**，不称 digest。

---

## 2. 三层资产盘点表

### 2.1 L1 原子工具 / Capability 层

**载体**：进程内运行时工具注册表 `agent/tools/__init__.py::_registry`（register/register_dynamic/call/list_tools/get_tool_defs/get_tool_schema/unregister_by_source）。

| 维度 | 现状（证据） |
|---|---|
| 内置工具注册 | 启动时 `agent/orchestrator/lifecycle_manager.py:1019-1046 _register_builtin_tools()` 模块化加载 8 组 `register_all`：core/file_tools_reg/web/ext/pdf/software/system/code + 过程蒸馏工具（`process_distill/tools.py`，失败仅记日志不阻断）+ 规划工具（:1048-1053）；静态注册合计 **70**（web 9/file 8/core 9/system 5/software 4/ext 13/code 18/pdf 4）＋规划 5＋蒸馏 3 ≈ **80**；另有 **schema 双轨**：`data/tool_definitions/*.yaml`（tool_index tool_count=70）与 Python 注册表 schema 并存（漂移风险，S1-01 归一化对象） |
| 注册项结构 | `{name, description, handler, schema?}`；动态项另有 `{source, source_id, dynamic, registered_at}`（`tools/__init__.py:48-146`）——**静态内置项无 source 字段** |
| 来源枚举 | 运行时 `SOURCE_BUILTIN/PLUGIN/MCP/GENERATED/MARKET` 五类（`tools/__init__.py:33-37`）↔ 资产库 `SkillCategory` 六类 builtin/custom/claude/community/mcp/ai_generated（`skills_mgmt/models.py:21-28`）——**两套枚举并存且不同名不同义**（§5.3 记入术语表） |
| MCP 能力发现 | 双路径：① `skills_mgmt/mcp_adapter.py:87-149 discover_from_mcp_server` → `tools/list` → Skill 草稿（id=`mcp-<server>-<tool>`、category=mcp、status=PENDING_REVIEW、config_schema=工具 inputSchema、default_params 存 mcp_tool_name/server/transport，:296-329），过 `SecurityScanner(block_on_critical)` 安全门（:352-360）后才可 auto_register（走 create_manual）；② 运行时路径 `tools/mcp_connector.py:196 _register_mcp_tools`（register_dynamic, SOURCE_MCP）+ `tools/discovery_service.py`（call 未命中时 on_tool_not_found 自动发现获取）+ 根目录 `mcp_services/`（yunshu_mcp_bridge/mcp_client/multi_search_engine/mock）。**落地现状**：无启动自动连接；已配置/已安装 MCP=0（extensions.json mcps=0、network_config mcp.services 空）；mcp_adapter（含安全门）**当前无调用方（已建未用）**；mcp_executor 引用的 `TOOL_PROTOCOL_MAP/get_tool_protocol` 全库无定义（死路径）；动态工具持久化函数被调但无定义（lifecycle_manager.py:988-991 死路径） |
| 存量规模 | 内置工具 ≈80（静态 70＋规划 5＋蒸馏 3）；**MCP 类技能 0 条、MCP server 配置 0 个**（skills_mgmt.json category 全为 custom）；插件/市场/生成通道存在但无持久化统计（extensions.json skills=1、plugins=0） |
| 启停生命周期 | L1 无 enabled 字段：运行启停 = `tools_config.json`（仅 5 键）→ 与注册表 whitelist 差集过滤（`digital_life_persona.py:422-438`）；工具随进程注册/随 `unregister_by_source`（插件卸载/MCP 断连）注销；技能级启停走 `skills_mgmt/registry.py SkillRegistry`（L3 统一视图，主轨权威→文件轨回退） |
| trust/幂等/风险描述 | **全缺（能力级）**：注册项无 idempotent/risk_level/requires_approval/undo_hint/compensating_action/side_effects/evolution_stage 任一字段；全库 grep `evolution_stage|borrowed|mirrored|shadow` 零命中，`idempotent/requires_approval/undo_hint/compensating_action` 亦零命中于工具/资产声明处（§8.B）。仅有进程级 `timeout`（mcp 侧）/限流（rate_limiter）/健康计数（_tool_health）。**补充**：① 运行**操作级**风险分级存在于 `human_in_the_loop/hitl.py`（RiskLevel HIGH/CRITICAL→ConfirmationMode 需确认），但**不挂接**工具注册项/技能资产——风险非声明式、非能力级（S1-01 契约层要补的正是资产级 trust 字段）；② 安全实际靠 handler 内手写 `check_action("write_file:{path}",…)`（file_tools_reg.py:64、system_tools.py:57/82/112 等）+ `data/dangerous_commands.json` 在**调用点强制**，非契约声明 |

**L1 生命周期入口/出口**：入口＝进程启动 register_all / register_dynamic（MCP 接入、插件、自生成）；出口＝unregister_by_source / 进程退出。**无持久化的能力级对象**（无 descriptor、无 manifest、无轨迹台账）——这是 S1 契约层要新建的最小挂载面。

### 2.2 L2 工作流序列层（LearnedWorkflow + 蒸馏中间产物）

| 维度 | 现状（证据） |
|---|---|
| 学习链路 | `workflow_learning/learner.py`：从**成功的** LLM 交互 LearningRecord（session_id + tool_calls）抽取工具序列→参数模板化→生成 LearnedWorkflow（confidence 初值 0.4＝matcher 匹配门槛，:125-129；冷启动死锁已修）；失败/空工具序列拒绝学习（:89-99） |
| 匹配执行 | 精确阈值（子代理 B 复核）：`min_similarity=0.3`、`min_confidence=0.4`（matcher.py:131-132；service 构造 service.py:28-29）；综合分＝sim×conf×(0.5+priority/200)（matcher.py:204,213）；执行门槛 `executor.min_score=0.3`（构造默认）/orchestrator 覆盖 `0.25`（config.yaml:578）；success_count=0 冷启动豁免（conf 因子视为 1.0，matcher.py:211-213）；executor 按 mode 分发 DAG/Agent（分支>3 或步骤>10 触发 Agent 模式，mode_classifier.py:28-29）；toolchain 免 LLM 0-Token 短路（orchestrator.py:774-789） |
| 置信度演化 | `models.py:128-151 record_execution`：成功 +0.1 对数衰减（上限 0.99）、失败 ×0.9（下限 0.05），单调演进 |
| 存量规模 | `data/learned_workflows.json`：**3 条**，全 active / toolchain / enabled，均来自 session `demo-seed`（种子演示沉淀，见 docs/zh/过程蒸馏能力交付总结_20260905.md §7.7"seed_demo_workflows.py"）；success_count 1/3/5，confidence 0.5/0.599/0.749 |
| 蒸馏链路 | `process_distill/`：sources→distiller（**并行隔离 LLM worker，ThreadPoolExecutor**，替代占位 subagent——docs/zh/过程蒸馏能力交付总结_20260905.md 明示 "subagent/ 容器是占位实现（不调 LLM、无并行编排）"）→merge→solidify；llm/rule 双路，LLM 失败自动降级规则提取 |
| 蒸馏产物 | ① workflow：仅固化**映射到已注册工具**的步骤（工具白名单 `agent.tools.list_tools()`，solidify.py:51-84），纯指令产物 action=skipped 建议改 skill；② skill：双写 JSON 轨（create_manual）+ 文件轨（file_store.create），id 幂等派生 `pd-<slug>-<sha1:8>-{wf,skill}`（solidify.py:40-44）；当前 **15 条 pd-\* skill** 在库（见 L3），**pd-\*wf workflow 0 条**（蒸馏源为纯指令方法论→只能落 skill） |
| 生命周期 | WorkflowStatus 枚举 draft/active/deprecated/archived（models.py:11-15）；**四态机是空壳**：全仓无任何写侧转移（无 deprecate/archive API，routes 仅 delete/toggle/priority），仅 ACTIVE 可达——连续失败只把 confidence ×0.9（下限 0.05）使匹配自然失效，无"失败降级/归档"机制（子代理 B 复核） |
| 升格钩子 | `skill_converter.py`：MIN_SUCCESS_COUNT=5 / MIN_CONFIDENCE=0.7 / MIN_PRIORITY=50（:53-55），质量门不过抛 QUALITY_GATE_FAILED（:163-187）；转换幂等，回写 converted_to_skill_id；另有 convert_external_skill（外部素材→skill，LLM） |

**L2 生命周期入口/出口**：入口＝自动学习钩子（成功会话工具序列，orchestrator.py:1289-1294/1816-1862，失败调用在 :1807 剔除；开关 learn_from_interaction.enabled，config.yaml:730-732）或 process_distill solidify_to_workflow；出口＝skill_converter 定时升格（lifecycle_manager.py:776-808，300s 周期）或停用；**数据完整性缺口**：`zip-d2968c59` 的 converted_to_skill_id=`zip-d2968c59-skill` 指向的资产在 JSON 主轨 / 文件轨 / legacy 快照中**均不存在**（孤儿引用；删除触发点未定位，§4 缺口 Z2）。

**L2 主要缺口（子代理 B 复核，供 S3 输入）**：① 无"轨迹→模式"挖掘层（学习输入＝单次成功会话工具序列；无跨会话 LCS/决策树/支持度≥N 门控；distill merge 仅相邻步 Jaccard≥0.92 去重）；② 无轨迹清洗/负样本回灌（失败仅 ×0.9 置信度）；③ WorkflowStatus 空壳（见上）；④ workflow→Skill 门控为静态计数，无可执行性验证/freshness/失败上限；⑤ 产物落 draft 后无人工评审可长期滞留（自动升格不触发三审）；⑥ 中文**单字分词**（learner.py:37）→ 触发词全单字、同义改写无命中；⑦ workflow→记忆→skill 第三链死代码（memory_abstractor.py:405 调用不存在的 list_recent）；⑧ pd 固化 workflow（conf=0.5 直接可执行）与 converter 导出（"LLM 参考"副本）两套桥接语义不对称。

### 2.3 L3 SKILL.md 编排资产层（skills_mgmt）

**双轨存储架构**（读 `skills_mgmt/registry.py:1-14`、`store.py`、`file_store.py`、`solidify.py`）：

| 轨 | 文件 | 权威性 | 职责 |
|---|---|---|---|
| JSON 主轨 | `data/skills_mgmt.json`（dict id→Skill，21 条） | **权威**（管理/UI/审核/启停） | 全生命周期 Skill 模型（含 review/metrics/versions/config_schema） |
| 文件轨 | `data/skills_repo/<id>/skill.md`（23 个目录；**实际文件名小写 skill.md**，与 v7.2 SKILL.md 命名规范存在差异） | 从属（front matter 存 meta） | 语义层 SkillLoader 检索召回（蒸馏交付明确"只写 JSON 轨不会被检索"）；persona 内置技能启停落此轨 |
| legacy 快照 | `data/skills.json`（29 条，仅 id/name/enabled/description/params） | **只读兼容快照**（registry.py:11-14 "可最终废弃"） | 旧 SkillsManager/旧 UI 兼容（as_legacy_rows 合并视图） |

**规模与状态分布**（2026-09-09 运行态直读统计，§8.A）：

| 维度 | 数值 | 明细 |
|---|---|---|
| JSON 主轨 | 21 | status：approved 18 / published 3；**无 draft/pending_review/rejected/deprecated/archived 存量** |
| 主轨来源 | manual 2 / external_agent 4 / knowledge_distill 15 | author：workbench 2 / unknown 4 / process_distill 15 |
| category | 全 custom | content_type 全 markdown |
| config_schema | **21/21** 非空 | 含自动修复生成的 schema（QUAL_NO_SCHEMA→fix-auto） |
| output_schema | **21/21 有键但全部为空 `{}`（有效覆盖 0/21）** | executor/output_guard 的输出契约门禁实际未启用（schema 空即跳过） |
| review | 21/21 有 review，status 全 passed，digest_verdict 全 ok | 评审语义＝review（service.py:614-620 digest_skill=review() 别名）；放行真实判据＝无 critical 安全命中 ∧ 无 digest critical/error 阻断项（reviewer.py:398-463；ReviewThresholds 仅用于日志，不参与通过/拒绝） |
| metrics | 21/21 存在但 **usage_count/success_count 全 0** | 技能级运行指标无埋点消费（缺口） |
| 敏感隔离 | is_sensitive=2 | 独立上下文窗口隔离 |
| 文件轨 | 23 目录 | 15 pd-\*（主轨+文件轨双写）＋7 内置 persona 行为技能（self_reflection/memory_summary/emotion_expression/proactive_suggestion/context_aware/safety_guard/voice_interaction，种子在代码 `extensions/base.py BUILTIN_EXTENSIONS["skill"]`，文件轨 front matter source=legacy_migration，**仅文件轨**）＋1 scripted-selftest 三层带脚本示例 |
| 权威并集 | **29**（21 ＋ 8 文件轨独有） | 与 legacy 快照 29 行一致（并集口径） |
| 分类注册表 | `skills_classes.json` assignments 111（asset:/rt: 双生态命名空间） | 自动分类/同类折叠 |

**双轨不同步硬伤**：主轨↔文件轨**无通用双向同步**——除蒸馏固化双写（solidify）与删除（cleanup）外，主轨 publish/update/lifecycle 只 upsert 主轨、不回写文件轨，文件轨 front matter 的 status 会过期（发布后仍 approved）；front matter 白名单仅 16 字段（file_store.py:74-81），review/metrics/versions/config_schema/output_schema 无法入文件轨；legacy 快照仅创建/合并时重建（store.py:257/468），启停/删除后可能滞后。文件轨内容恒"薄于"主轨，双轨是"并集视图 + 少数显式同步点"（S1-02/S3 关注）。

**现有生命周期状态机**（发布/运营轨）：

- 人工/自动评审放行：新技能/外来安装默认 draft 或 PENDING_REVIEW（creator.py:112 AI 生成=DRAFT；creator.py:191 install=PENDING_REVIEW；solidify_to_skill 固化后自动走 review()：PASSED→approved、WARN→pending_review、FAILED→rejected，**永不自动 publish**，发布留人工——"AI 只产草稿、审核放行"，solidify.py:215-227）；
- 自动化降级：`skills_mgmt/lifecycle.py` PUBLISHED 闲置 >90 天（默认）→DEPRECATED；DEPRECATED 闲置 >180 天→ARCHIVED；仅状态迁移不删文件，审计写 `data/skill_lifecycle_audit.jsonl`（lifecycle.py:6-14,331-370）；**默认 dry_run=true**（config `learning.lifecycle.dry_run`）——实际只报告不迁移，存量因此无 deprecated/archived（另有 enhancer 直赋 PUBLISHED 弱门：success_rate≥0.99∧usage≥10 可绕过 publish 强审，enhancer.py:270-274）；
- 外来导入：`creator.py SkillInstaller.install(source)`，source 解析 github/url/local/registry（:234-327），默认 status=PENDING_REVIEW + installed_at；与云枢自身功能重叠按**增量吸收**策略保留（absorb_overlap 标记 absorbed/native-overlap，service.py:183-232；早期 NATIVE_DUPLICATE delete+400 硬闸门已演进为吸收优先）；
- 启停统一视图：`registry.py SkillRegistry`（主轨 Skill.enabled → 文件轨 front matter enabled；legacy 只读）。

**SKILL.md front matter 与 v7.2 §3.7 规范差距**（抽样 pd-* 与 persona skill.md）：现有 front matter 仅 id/name/description/content_type/category/tags/author/source/status/enabled/version；**缺** stage(七态)/scope/origin{derived_from_task,inspired_by,distilled_by}/preconditions/inputs/outputs(JSON Schema)/risk_level/requires_approval/undo_hint/compensating_action/tests/metrics/signature（§5.3 缺口行 G1-L3/G3-L3）。

### 2.4 汇总表（层 × 载体 × 规模/状态分布 × 生命周期入口/出口 × v7.2 对应）

| 层 | 云枢载体 | 规模/状态分布（2026-09-09） | 生命周期入口 | 生命周期出口 | v7.2 对应 |
|---|---|---|---|---|---|
| L1 原子工具/Capability | `agent/tools._registry` 运行时工具；MCP 双路径桥（skills_mgmt/mcp_adapter ↔ tools/mcp_connector）；mcp_services | 内置 ≈80（静态 70＋规划 5＋蒸馏 3）；MCP server 配置 0、MCP 类技能 0 条；schema 双轨（Python 注册表 vs tool_definitions/*.yaml=70）；无独立 capability 台账 | 进程启动 register_all / register_dynamic（MCP/插件/自生成） | unregister_by_source / tools_config.json whitelist 差集 / 进程退出（无持久化演进轨） | Tool/Capability（cp.<source>.<upstream>）+ §3.2 trust/runtime/evolution 字段（全缺） |
| L2 工作流序列 | `workflow_learning` LearnedWorkflow（data/learned_workflows.json）；process_distill 中间产物（DistilledProcess）→ pd-* 固化 | workflow 3 条（全 active/toolchain/demo-seed）；pd-*wf 0 条；蒸馏 skill 15 条（approved） | 成功会话工具序列学习 / solidify_to_workflow | skill_converter 升格（5/0.7/50 质量门）→ 孤儿引用风险 | mirrored 产物宿主（候选模式）＋§4.5 轨迹→模式→Skill 中间形态（入七态判定见 §3.3） |
| L3 SKILL.md 编排资产 | skills_mgmt JSON 主轨（skills_mgmt.json）＋文件轨（skills_repo/<id>/skill.md）＋legacy 只读快照（skills.json）＋SkillRegistry 统一启停 | 主轨 21（approved 18/published 3，全 custom）；文件轨 23；权威并集 29；config_schema 21/21、output_schema 21/21 有键全空；review 全 passed | create_manual(DRAFT)/install(PENDING_REVIEW)/AI 生成/freeze 蒸馏固化（draft→自动 review） | 人工放行 approved/published；lifecycle 自动 deprecated(90d)→archived(180d)（默认 dry-run）；rejected 删除 | SKILL.md 编排单元（§3.7）+ Skill 版本态（§4.5 draft→candidate→active→deprecated→archived） |

---

## 3. v7.2 七态作用域判定

### 3.1 判定框架

1. **两条正交轨**：v7.2 文档实际定义了两条对象轨——
   - **能力内化演进轨（七态）**：主语是 Tool/Capability（`cp.<source>.<upstream>`），描述"从外部借用→本地化→等价替换→原生固化"的**演化**（§3.2 evolution.stage、§3.3 转移表）；
   - **发布/运营轨**：Skill 版本态 draft→candidate→active→deprecated→archived（§4.5）与 Task 生命周期（§3.8），描述资产"能否被调度使用/何时退役"。
   - 两轨通过 §3.3.1 对照表衔接：internalized 态的**验收物**才是 SKILL.md+tests+签名——即内化产物是 L3 资产，而内化过程主语是 L1 能力。
2. **云枢现状只有第二条轨（L3 发布轨 + L2 质量阶梯），第一条轨零足迹**（§2.1 trust 覆盖表、§8.B grep 零命中）。因此七态落地 = 新建 L1 能力演进轨，而非改写任何现有状态机。
3. 转移条件语义须按层翻译：判定集/双跑 diff 属于"原生实现 vs 上游"（等价替换），灰度 72h/30 天零回退属于"部署/运营验证"。

### 3.2 状态机作用域矩阵（七态 × L1/L2/L3 × 适用判定）

| 七态 | L1 原子工具/Capability | L2 工作流序列 | L3 SKILL.md 资产 | 转移条件在哪层可测 | 现有替代机制 |
|---|---|---|---|---|---|
| borrowed | ✅ **适用（主轨）**：外部来源接入即借（MCP server tools/manifest、插件、市场、外来 skill 的来源记录） | ❌ 不适用：workflow 是**内生**提取（源＝云枢自己会话），无"外部上游"可言 | ◐ 部分适用：外来导入技能（source≠manual，如 external_agent/install/蒸馏固化）可视为 borrowed 的 L3 特例，但**不等义于现有 draft**（draft 也覆盖本土手工草稿） | L1：来源 manifest + 接入探针（云枢无 manifest，仅有 source 字符串）；L3：导入时点可标 | L3 install 默认 PENDING_REVIEW、draft（creator.py:191/437）；无 L1 manifest/探针 |
| mirrored | ✅ **适用**：本地镜像＝该能力的副作用画像 + 候选模式（§3.3.1） | ◐ **产物载体**：LearnedWorkflow（active + confidence 追踪）正是"轨迹→候选模式"的宿主，即 mirrored 的产物层 | ❌ 不直接适用：SKILL.md 是镜像之后的**产物/验收物**，不是镜像动作本身 | L1 画像（无）；L2 workflow 模式 + success/confidence 可测（现成） | workflow active + record_execution 置信度阶梯（近似"模式已镜像"） |
| shadow | ✅ **适用（需新建）**：等价判定集通过 + 原生实现单测通过 + 双跑 diff（对 L1 原生实现 vs 上游） | ❌ 主语义不适用（无上游等价物）；其"低流量试运行"思想可作为 matcher A/B 增强，不建独立态 | ◐ **需新增到部署轨**：native 技能发布前 shadow 灰度（5% 流量/72h）作为 published 前置流程 | **均不可测**：无 EquivalenceCase 判定集、无回放沙箱、无灰度设施（S3-02/S3-03 任务范围） | 无 |
| internalized | ✅ 适用（指向 L3）：内化动作的**终点产物**是 SKILL.md+tests+签名（§3.3.1）；原子工具内化同样以 skill 包装落地 | ❌ 不入态：skill_converter 升格＝一次内化动作的机械步骤，非独立态 | ✅ **主轨**：SKILL.md 验收门 + tests + 签名在此 | L3 评审门可测（review 三审）；验收门（回放/成本/签名）缺 | 云枢 review 三审 + approved（近似 internalized 验收的一部分） |
| native | ◐ 部分适用：若该能力有 L1 原生实现（非 skill 包装），native 挂 L1 descriptor | ❌ | ✅ **主轨**：native＝30 天零回退 + 每周探活（探活＝判定集子集重放 + 成本/成功率监控，T4 建议） | **不可测**：无 30 天台账、metrics.usage 全 0、无探活（S2/S3/S5 范围） | 无（published 无回退观察窗） |
| permanent_borrowed | ✅ **适用**：opaque/commodity/两次 ROI 未达标的外部能力（§6.9 永调类别） | ❌ | ◐ 适用：外来 skill 分级"只收录 manifest 不内化"（审计 §5.4 建议） | 豁免内化，仅需轨迹 + 季度探活（无） | L3 无"启用但不内化"标记（MCP 0 条、external_agent 4 条无演进字段） |
| deprecated | ✅ 适用：上游下线/契约漂移（能力级） | ◐ 现有 WorkflowStatus.deprecated 枚举（运营语义，可用） | ✅ **适用（复用现有）**：SkillStatus.DEPRECATED 已实现（lifecycle.py:331-370，闲置 90d 自动） | L1 漂移探针（无）；L2/L3 现成 | L3 lifecycle 自动 deprecated→archived |

> 矩阵无"都适用/都不适用"含糊格：每格均有判定与理由；部分适用格（◐）均注明边界条件。

### 3.3 特别裁决 A：L2（workflow 序列）不纳入七态主轨

**裁决**：L2 **不**作为七态的状态主体；其角色＝**L1→L3 的中间过渡载体（mirrored 产物的宿主 + internalized 的原料）**。

理由：
1. **语义不成立**：七态中的 borrowed/shadow/permanent_borrowed 都以"外部上游/等价替换对象"为前提；L2 由云枢自己会话的工具序列内生（learner.py source_session_id=demo-seed 或蒸馏），无上游可比、无"借来"语义；
2. **文档依据**：v7.2 §3.3.1 将 mirrored 态进入验收物定义为"副作用画像 + 候选模式"、产线环节为"模式挖掘"——LearnedWorkflow 正是该**候选模式的宿主**。即 L2 是 L1 演进到 mirrored 的产物载体，而非平行独立状态主体；v7.2 §3.1 ER 也无 workflow 实体（Skill(N)>─(1-N)Capability 之间无中间类）；
3. **防双状态机叠床架屋**：云枢 L2 已有成熟质量阶梯（confidence 演化 + 匹配门槛 0.4 + converter 升格 5/0.7/50 一次固化），若再套七态将出现"每层一套演进轨"的二象性，违背总计划"复用已有资产只补契约"的纪律；
4. 数据佐证：L2 现状样本极小（3 条全 demo-seed，success≤5）且 workflow 到 skill 是一跳升格——中间再设态无实践意义。

**保留的增强轨（不是七态）**：S3 可将 v7.2 shadow"低流量试运行"思想落到 L2 matcher A/B（workflow 命中 vs 默认 LLM 路径对照、命中样本回填 confidence），作为 workflow 置信度增强项，写入 S3 任务输入（§7）。

### 3.4 特别裁决 B：L3 现有状态机与 v7.2 七态的映射（不破坏现有语义）

**裁决**：现有 SkillStatus（draft→pending_review→approved/rejected→published→deprecated→archived）**保持原义不动**；它对应 v7.2 **§4.5 Skill 版本态**（draft→candidate→active→deprecated→archived 的同构物：candidate≈pending_review/approved，active≈published），而**不是**七态。七态以新增字段 `evolution.stage`（v7.2 §3.7 SKILL.md frontmatter stage）与现有 status **正交叠加**。

| 现有 L3 语义（不动） | v7.2 对应（发布/运营轨） | 与七态的关系（叠加，不替代） |
|---|---|---|
| draft（草稿/待放行；AI 只产草稿） | draft | **draft ≠ borrowed（有条件近似）**：本土手工草稿与"外来借用"不同义；仅当 `source≠manual 且 draft`（外来导入/蒸馏固化暂存）时可视为 borrowed 在 L3 的近似表现。borrowed 的规范落点是 L1 能力轨 + 外来导入标记，不改 draft 语义 |
| pending_review / approved / rejected（评审门） | candidate | 评审/放行轨保留；approve 后 approved 状态语义不变（internalized 是 evolution.stage 值，可与 approved/published 并存） |
| published（已发布可用） | active | **published ≈ internalized 有条件成立**：published 是"正式可用"的运营动作，internalized 是"验收通过 + 灰度 72h + 成本达标"的演化结论。映射规则：published 动作可作为 internalized 的触发近似，但 internalized 需满足 v7.2 验收门（§4.5 硬闸门）后才置位；存量 published 3 条不自动获得 internalized 标记（无验收证据），只标 stage=internalized_candidate 或留空待 S3 补验 |
| deprecated / archived | deprecated / archived | 同义复用（lifecycle.py 自动降级已实现，90d/180d 阈值，默认 dry-run 需显式关闭才实际迁移） |
| —（不存在） | — | **新增值 internalized/native/borrowed/permanent_borrowed/mirrored/shadow** 只出现在 evolution.stage 字段；native 需 30 天零回退台账后置位（存量 approved/published 21 条均无该证据，一律不迁移） |

**新增设施落点（不新增 L3 status 枚举值，避免破坏存量枚举/UI/迁移）**：
- shadow/灰度：L3 部署轨在 published 前插入灰度窗（复用 S3-03 shadow 设施，状态用 shadow_config 记录而非新 status）；L1 能力轨按判定集门进入 shadow（§3.2）；
- borrowed/permanent_borrowed：L1 能力轨 + L3 外来导入标记（evolution.stage 字段值），不触碰现有 status；
- 存量影响声明：21 条主轨 + 23 目录文件轨 + 29 条 legacy 快照**零状态迁移、零改名、零枚举变更**。

### 3.5 转移条件可测性结论

| v7.2 转移条件 | 云枢现状可测？ | 需要的设施（阶段） |
|---|---|---|
| 轨迹 ≥20（borrowed→mirrored） | ❌ 无统一 Trace 计数；workflow 学习不设样本下限（单次成功即学，learner.py:85-99）；技能 metrics.usage 全 0 | S2 Trace 台账 + S3 轨迹清洗/同类判定（T3） |
| 等价判定集通过（mirrored→shadow） | ❌ 无 EquivalenceCase 资产/回放沙箱 | S3-02（Seed Pack 12 技能×≥3 组起步，P7.2-23） |
| 原生实现单测通过 | ◐ 技能级 review 三审存在；无"原生实现 vs 上游"对照 | S3 验收门量化 |
| 灰度 72h 无异常 + 成本≤上游×0.8 | ❌ 无灰度/双跑/成本对照埋点 | S3-03 + S2 cost 埋点（UTC） |
| 30 天零回退 + 每周探活 | ❌ 无回退台账/探活 | S3-03/S5（T4 native 持续保障） |

**现状替代关口（L2/L3 已有的近似闸）**：workflow matcher 置信门槛 0.4、converter 升格 5/0.7/50、L3 review 三审 + 人工发布门禁、lifecycle 90d/180d 降级（默认 dry-run）、外来导入安全扫描 + 原生重叠增量吸收 + 外来预检（assessor）。

---

## 4. 层间转换器清单

### 4.1 现状钩子（转换方向 × 钩子 × 门控/质量门/审批 × v7.2 对照 × 缺口）

| 转换 | 现状钩子（文件:行） | 输入门控 / 质量门 / 审批点 | v7.2 对照（§2.4 三层组合拳 / §3.9） | 缺口 |
|---|---|---|---|---|
| L1→L2 工具序列→workflow | `workflow_learning/learner.py:85-99`（成功会话 tool_calls→LearningRecord→LearnedWorkflow）；process_distill solidify_to_workflow（`process_distill/solidify.py:64-139`，工具白名单过滤） | learner：仅成功交互 + 有工具调用才学；distill：仅已注册工具步骤可固化（否则 skipped） | §4.5 轨迹采集→模式挖掘（输入 **≥20 条同类轨迹**） | 无 ≥20 条样本门槛、无轨迹清洗/去噪/同类判定（T3）；轨迹源是 session 记录非统一 Trace（S2） |
| L2→L3 workflow→Skill 升格 | `workflow_learning/skill_converter.py:81-187`（convert_workflow_to_skill，定时自动升格在 `lifecycle_manager.py:776-808`，默认 300s 周期） | **success_count≥5 ∧ confidence≥0.7 ∧ priority≥50 ∧ status=ACTIVE ∧ enabled ∧ 未转换过**；不过抛 QUALITY_GATE_FAILED；幂等回写 converted_to_skill_id；产物＝"LLM 参考副本"叙述（不替代 workflow 本体，前端"导出为 LLM 参考"）；**本路径无强制评审**（create_manual 落主轨 draft + 咨询性评估，auto_review_after_workflow_convert 默认关） | §4.5 泛化→SKILL.md 生成→**确定性回放沙箱→验收门**→签名 | 无回放/验收门（仅质量阶梯）；无签名；**孤儿引用**（zip-d2968c59-skill 不存在于任何轨，§2.2）；正式评审仅 process_distill 固化路径（run_review=True） |
| L1→L3 外来能力导入（MCP/社区/URL/local/zip/Claude） | `skills_mgmt/mcp_adapter.py:87-149`（MCP discover→Skill 草稿）；`creator.py:171-327 SkillInstaller.install`（github/url/local/registry）；服务层 install_precheck + import_queue；`workflow_learning` convert_external_skill | 安全扫描硬门（SecurityScanner block_on_critical，仅在评审期/发现期执行，非 fetch 层）；外来预检 `assessor._assess_external_precheck`（高风险默认 error 阻断）；原生重复改为**增量吸收**（absorb_overlap 标记，非整包拒绝）；PENDING_REVIEW/草稿**人工逐个放行**；AI 产物只 draft | §2.3 六步探针 + manifest（provenance≥verified 才进自动化）；§2.2 Skill Import 验收门 + 许可证扫描强制 | 无六步探针（handshake/call_echo/side_effect/rate_limit/failure_modes）；无 manifest/provenance/license 字段（install fetch 层无 host 白名单/license 校验/签名，S1/S1-02 补）；无 30 天漂移重探 |
| L3→运行时消费 | SkillLoader 语义召回（文件轨）；context_injector 注入启用技能；SkillRegistry 统一启停（`registry.py`）；executor 执行带脚本技能 | enabled 标志（主轨权威） | §4.5.2 名片懒加载（frontmatter 常驻、正文按需） | 三层按需加载已落地（loader/file_store/context_injector）；签名前置/命中首步流式等对齐度 S6 微调；L3 依赖的工具名需对得上 L1 注册（solidify 白名单已校验） |
| L3→L1（技能引用 capability） | 技能正文引用工具步骤（toolchain workflow/skill 内嵌工具名）；蒸馏固化白名单校验 | solidify 白名单 = agent.tools.list_tools() | Skill 只引用 Capability schema（§3.7） | 无独立 capability 注册层可引用（S1 建） |
| 委派萃取（黑盒→解题模式，§3.9/§2.4 第 3 层） | `subagent/` 容器**占位**（不调 LLM、无并行编排——过程蒸馏交付总结明示）；process_distill distiller 以 ThreadPoolExecutor 隔离 worker 代偿 | 无委派上下文包八要素/回收三件套 | §3.9 委派契约 + 回收三件套（缺一不计成本） | **P7：真子代理未实现**（S4-04 独立前置）；回收三件套无埋点（S2） |

### 4.2 缺失转换器/设施清单（按层）

| 缺失项 | 作用层 | 落点任务 | 说明 |
|---|---|---|---|
| 能力级 Descriptor/Registry（含 ID 规则 cp.*） | L1 | S1-01 | 消化对象挂载面（本任务 C7 裁定） |
| provenance/trust/undo_hint 回填 API + 存量回填 | L1+L3 | S1-01/S1-02 | 摸底表见 §5.3（L3 全缺 risk/undo/provenance；L1 全缺） |
| 判定集 EquivalenceCase 资产 + 回放沙箱 | L1（原生实现） | S3-02 | Seed Pack 起步 |
| 轨迹台账（Trace 统一）+ 清洗/同类判定 | L2 原料 | S2/S3-01 | ≥20 条门槛的前提 |
| shadow 灰度 + 双跑 diff + 内化六条件引擎 | L1 演进轨 + L3 部署轨 | S3-03 | 灰度窗插 published 前 |
| SKILL.md 生成器（模式→正文） | L2→L3 | S3-01 | 现 skill_converter/蒸馏用模板拼装，非 LCS+决策树模式挖掘 |
| skill.md frontmatter 字段扩展（stage/origin/preconditions/inputs/outputs/risk/undo/tests/metrics/signature） | L3 | S1-02/S3-01 | 与 v7.2 §3.7 对齐，存量为空 |

---

## 5. 消化对象映射矩阵（含 01 号审计报告 §6 复核更新）

### 5.1 复核更新：01 号审计报告 §6 消化对象相关行

| 01 §6 行 | 原判定 | 本任务复核更新（2026-09-09 证据） | 处置不变 |
|---|---|---|---|
| ToolDescriptor v2.1（skills_mgmt models + MCP adapter，◐） | 补齐：Descriptor 模型/校验器/registry（S1） | **确认 ◐ 并给出缺口粒度**：L1 无 capability 注册对象（tools._registry 仅 name/desc/handler/schema）；MCP 桥只产出 L3 Skill 草稿非能力台账；全库无 evolution/trust 字段（§2.1） | 补齐（S1-01），对象范围＝L1 为主 + L3 轻量视图（C7） |
| 消化状态机七态（skills_mgmt 生命周期 + lineage，◐） | 补齐：映射旧状态 + 增 shadow/灰度（S3） | **上调缺口等级**：云枢**无任何七态运行时足迹**（grep 零命中）；现有 SkillStatus 是发布轨非演进轨（§3.1/§3.4）；七态需新建 L1 演进轨并正交叠加 L3（C2/C4/C5） | 补齐（S3），以本任务作用域矩阵为挂载图（§3.2） |
| EquivalenceCase 判定集（evaluator，◐） | 补齐（S3） | **确认 ❌（判定集方向）**：evaluator 是技能执行评测非等价判定集资产；无 30-100 组用例/回放沙箱（§3.5） | 补齐（S3-02） |
| SkillFactory 流水线（digest+process_distill+workflow_learning，✅） | 已有多轨固化，缺统一管道与验收门量化 | **确认 ✅ 并澄清三轨性质**：① skills_mgmt review（评审/放行轨）；② workflow_learning→skill_converter 升格轨（5/0.7/50）；③ process_distill→solidify 蒸馏固化轨（pd-*15 条）。三者均无七态语义、无判定集/灰度——是"半成品三轨"，需在 S3 统一到七态管道而非新建 | 补齐/统一（S3-01） |

### 5.2 新增缺口行（按"哪一层缺什么"补齐）

| 缺口 ID | 层 | 缺口 | 现状证据 | 落点 |
|---|---|---|---|---|
| G1-L1 | L1 | 缺 shadow/判定集门、缺镜像画像 | tools._registry 无演化/画像字段；MCP discover 无探针 | S3-02/S1-01 |
| G2-L1 | L1 | 缺能力级启停与持久化台账 | 工具随进程注册/注销，无持久化 lifecycle | S1-01（registry 持久化） |
| G1-L2 | L2 | 缺样本门槛与轨迹清洗（对应 T3） | 单次成功即学习（learner.py:85-99）；无 ≥20 同类轨迹判定 | S3-01 |
| G2-L2 | L2 | 缺灰度/A-B 与升格闭环校验 | converted_to_skill_id 孤儿引用（zip 例）；无回放 | S3-01/S3-03 |
| G1-L3 | L3 | 缺 evolution.stage（七态）/origin 溯源字段 | Skill 模型与 skill.md front matter 无此字段（§2.3） | S1-02/S3-01 |
| G2-L3 | L3 | 缺 shadow 灰度窗（published 前）与 native 探活 | published 无回退观察窗；metrics.usage 全 0 | S3-03/S2 |
| G3-L3 | L3 | output_schema 21/21 有键全空、risk/undo/provenance 全缺、无签名 | §2.3 统计 | S1-02/S1-01 |
| G0-全 | 三层 | 缺统一 Trace/事件埋点 → v7.2 全部转移条件不可测 | §3.5 | S2 |

### 5.3 v7.2 概念 ↔ 云枢实体映射总表（消化对象章节，供并入 S0-01 术语映射表）

| v7.2 概念 | 层 | 云枢实体/机制 | 差异与备注 |
|---|---|---|---|
| Capability（契约） | L1 | **无独立对象**（tools._registry 注册项近似；缺 schema 级 cp.hint 注记） | S1-01 新建；ID 规则 cp.<source>.<upstream> 待建 |
| Tool（实例） | L1 | 运行时注册工具（含 mcp-* 动态项） | 无 source 字段静态项需补标 |
| Skill（SKILL.md 编排单元） | L3 | skills_mgmt Skill + skills_repo/<id>/skill.md | front matter 字段子集（§2.3 差距） |
| source.manifest / 探针 | L1/L3 导入 | MCP discover / install / install_precheck（安全扫描） | 无六步探针/manifest/provenance（§4.1） |
| 七态 evolution.stage | L1 演进轨（L3 正交字段） | **无**（全库零足迹） | 按 §3.2 矩阵落位；不碰 L3 现有 status |
| Skill 版本态（§4.5） | L3 | SkillStatus 八值（draft…archived） | 同构；approved≈candidate 细分（云枢拆 pending_review/approved/rejected） |
| Trace | L2 原料 | observability/tool_trace.py + session 记录 | 无 trace_id 全链透传（S2） |
| EquivalenceCase 判定集 | L1 | evaluator 评测（非判定集） | S3-02 新建 |
| 内化六条件触发（§4.5.1） | L1→L3 | 无（metrics 全 0 无从计量） | S3-03 新建 + S2 埋点 |
| SKILL.md 名片懒加载（§4.5.2） | L3 | ✅ 已落地：`loader.py`/`file_store.py:29-39`/`context_injector.py` 三层按需（第一层只读 front matter 名片 → 第二层匹配后按需读正文 → 第三层执行时按需取脚本）；`index_cache` 懒加载元数据索引 | 与 v7.2 对齐度：签名校验前置/命中首步流式读取待 S6 微调 |
| Skill Import（社区） | L3 导入 | install(source=github/url/local/registry) + import_queue | 无许可证扫描/manifest（S1-02） |
| SkillCategory 六类 | L3 | models.py:21-28 | 与运行时 SOURCE_* 五类双枚举并存（记 S0-01 术语表：统一或显式映射） |
| digest（评审语义，云枢） | L3 | service.digest_skill=review() 别名（:614-620） | S0-01 更名 review/assess（本文件已遵守） |

---

## 6. 与 TASK-S0-01 术语映射表的联合评审记录（对实表评审）

> 评审对象：S0-01 交付物 [`术语映射表.md`](术语映射表.md)（Accepted，2026-09-08）＋[`RFC-宿主形态与范围.md`](RFC-宿主形态与范围.md)。
> 评审方式：逐行对照 §1（消化对象三层说明）、§2（术语映射核心表）、§3（digest 更名裁定）。

| 核对项 | S0-01 术语表结论 | 本文件一致性/仲裁 | 结论 |
|---|---|---|---|
| 三层定义（§1） | L1/L2/L3 载体与"L2 不入七态、作 L1→L3 过渡轨" | 与 §2.4 汇总表、C3 裁决**完全一致**（载体、生命周期、状态机适用逐格相同） | ✅ 无矛盾 |
| L1 与七态（§1） | "最接近七态主体；borrowed→mirrored 门槛（轨迹≥20）在此层可测" | 一致（§3.2 L1 列 ✅）；**可测性措辞仲裁**：轨迹≥20 的**测量维度归属 L1**，但云枢现状无统一 Trace 台账/技能 metrics.usage 全 0，**暂不可实际计量**（本文件 §3.5，S2 前置）——两表口径不冲突，S0-01 表不需改 | ✅ 无矛盾（可测性注记） |
| L3 与七态（§1 末列） | "映射七态后段（draft≈borrowed 前、published 之后的 shadow/灰度等由 S3 增补），不破坏现有语义" | **交叉点仲裁（措辞歧义）**：shadow/灰度按 v7.2 §3.3（shadow→internalized 转移条件）与 §4.5 流水线（shadow→灰度5%→转正）位于**转正（published）之前**的部署闸；本文件 §3.4 落点＝"published 前灰度窗 + evolution.stage 正交"，与术语表表述存在歧义 → **已对术语表 L3 行做最小修订并标注（S0-02 联合评审）**，正文语义统一为"灰度窗插 published 前" | ✅ 已消除（见术语表修订注） |
| digest 两义（§2/§3） | digest（评审）→ review/assess 系；v7.2 七态→内化流水线 | 全文遵守（§0）；本文件证据 service.py:614-620 与术语表 §3.2 #6 一致 | ✅ 无矛盾 |
| Skill / Capability-Tool / stage 七态 / Trace / Approval / EquivalenceCase（§2） | 术语与落位表 | §5.3 总表逐项覆盖、层归属一致（Skill=L3、Capability/Tool=L1、stage=S3、EquivalenceCase=S3-02） | ✅ 无矛盾 |
| 评审-消化流水线（§2 SkillFactory 行） | 评审-评估流水线＝assessor/reviewer/service；接 S3 统一管道 | 与 §2.3（review 21/21 passed）、§5.1（三轨澄清）一致 | ✅ 无矛盾 |
| 名片懒加载（§2 末行） | "已落地，按需对齐（index_cache/loader）" | **S0-01 正确，本文件初稿曾误列缺口** → 已修正 §5.3 行（loader/file_store/context_injector 三层按需加载实证，§8.B）。注：RFC §5 处置列将 §4.5.2 标"入 S3"——与术语表"已落地"口径有出入，属 S0-01 内部表述，建议其验收时统一（本文件以落地现状为准，对齐度微调归 S6） | ✅ 已修正（本文件修订）+ ⚠️ S0-01 内部统一待办 |
| events/存储更名（§2/§3.2 #11） | skills_digest_events.jsonl → skills_assessment_events.jsonl | 本文件 §2.3 引用旧档名仅为现状描述；不冲突 | ✅ 无矛盾 |
| 双来源枚举 | （S0-01 表未覆盖） | 本文件 §5.3 新发现：SOURCE_* 五类 vs SkillCategory 六类并存——已列为 S0-01 术语表待办（验收报告 §四） | ⚠️ 新增项（待 S0-01 纳入） |

**联合评审结论**：两表在消化对象三层、L2 不入七态、七态主语（L1 能力轨）、digest 更名方向等**核心裁决全部一致**；发现并消除 2 处交叉不一致（L3 行 shadow/灰度措辞——修订术语表；名片懒加载——修正本文件 §5.3），1 处新增待办（双来源枚举）；可测性措辞经仲裁无冲突。S0-01 验收报告应在最终验收中回引本文件 §3.2/§5.3 完成收口。

---

## 7. 对后续任务的输入（引用承诺）

- **TASK-S1-01（ToolDescriptor 契约层）**：依赖本文件 C7（消化对象范围＝L1 为主 + L3 轻量视图）与 §3.2 矩阵（evolution.stage 枚举取值、L3 映射方式）；其 §2 步骤 2"CLAUDE/community 类 SKILL.md 资产→轻量 descriptor 视图（evolution.stage 映射自 TASK-S0-02 的状态机作用域矩阵）"——即本文件 §3.2/§3.4。
- **TASK-S1-02（存量资产字段回填）**：摸底数据直接引用本文件 §2.3（21 条主轨/23 文件轨/29 并集、config_schema 21/21、output_schema 21/21 有键全空、risk/provenance/undo 全缺）与 §4.2 缺失清单。
- **TASK-S3-01/02/03（消化流水线）**：输入 §3.5 转移条件可测性缺口表、§4.2 缺失转换器清单、§5.2 新增缺口行（shadow/判定集/灰度落点按层）。
- **TASK-S0-01（术语映射）**：§5.3 映射总表（消化对象章节）已与其实表完成联合评审（§6），并入其验收；RFC 方案 A（机制吸收）与本文件 C2/C7 一致。

---

## 8. 证据附录

### A. 计数口径与复核命令

- 主轨统计：一次性只读统计脚本（已清理）——skills_mgmt.json（dict id→obj，21 条）status/category/source/config_schema/output_schema/review/metrics 分布；
- 文件轨：`data/skills_repo/` 23 个含 skill.md 的目录（15 pd-\* ＋ 7 内置 persona（种子在 extensions/base.py）＋ 1 scripted-selftest 示例）；
- legacy 快照：skills.json 29 行（仅 id/name/enabled/description/params）；
- L2：learned_workflows.json 3 条（active/toolchain）；
- L1 规模：8 个 register_all 模块静态注册 70（web 9/file 8/core 9/system 5/software 4/ext 13/code 18/pdf 4）＋规划 5＋蒸馏 3 ≈80；schema 轨 data/tool_definitions/*.yaml（tool_index tool_count=70）双轨并存；
- 七态足迹：`grep -r "evolution_stage|borrowed|mirrored|shadow" agent/**/*.py` 零命中；`idempotent/requires_approval/undo_hint/compensating_action` 亦无工具级声明（仅 HITL risk_level、permission_system requires_confirmation 调用点、knowledge ingest 文件级 idempotent）。

### B. 关键代码证据（file:line）

| 论断 | 证据 |
|---|---|
| digest=review 别名 | `agent/skills_mgmt/service.py:614-620` |
| 运行时工具注册结构（无 trust 字段） | `agent/tools/__init__.py:48-146,299-426` |
| 内置工具启动注册 8 模块组 | `agent/orchestrator/lifecycle_manager.py:1019-1046` |
| MCP 能力发现→Skill 草稿 + 安全门 | `agent/skills_mgmt/mcp_adapter.py:87-149,296-360` |
| SkillCategory/SkillStatus 枚举 | `agent/skills_mgmt/models.py:21-40` |
| 双轨统一启停视图 + legacy 只读 | `agent/skills_mgmt/registry.py:1-22,51-71,118-193` |
| L3 自动降级 90d/180d | `agent/skills_mgmt/lifecycle.py:107-131,331-370` |
| 外来安装默认 PENDING_REVIEW | `agent/skills_mgmt/creator.py:175-202,430-445` |
| workflow 学习仅成功+有工具调用；conf 初值 0.4 | `agent/workflow_learning/learner.py:85-133` |
| converter 质量门 5/0.7/50 | `agent/workflow_learning/skill_converter.py:53-55,163-187` |
| 蒸馏固化双写 + review 自动（不自动 publish） | `agent/process_distill/solidify.py:215-329` |
| 蒸馏 workflow 工具白名单 | `agent/process_distill/solidify.py:51-84` |
| L3 状态机八值（draft…archived） | `agent/skills_mgmt/models.py:31-39` |

### C. 子代理盘点佐证

3 个并行只读盘点子代理（L1/L2/L3）已全部落定，与本文档数据侧结论**核心一致**；本文档已按子代理精确化发现修订以下点（均在正文相应行更新）：
- L3（子代理 C）：output_schema 精确表述＝"21/21 有键但全空 {}"；文件轨 23 目录＝15 pd-* + 7 内置 persona（种子在 `extensions/base.py BUILTIN_EXTENSIONS["skill"]`）＋ scripted-selftest 示例；lifecycle 自动降级**默认 dry_run=true**；enhancer 直赋 PUBLISHED 弱门；review 放行真实判据＝无 critical 安全命中 ∧ 无 digest 阻断（阈值仅日志用）；双轨无通用双向同步（front matter 白名单 16 字段/legacy 快照重建触发面窄）；`skill.md` 小写命名；
- L2（子代理 B）：学习触发点（orchestrator.py:1289-1294/1816-1862，失败调用在 :1807 剔除）；定时自动升格（lifecycle_manager.py:776-808，300s）；**workflow→Skill 转换路径无强制评审**（create_manual 落 draft，auto_review 默认关；正式三审仅在 process_distill 固化 run_review=True）；WorkflowStatus 四态仅 ACTIVE 可达（全仓无写侧转移）；蒸馏为真实并行 LLM worker，占位仅 subagent/ 容器；
- L1（子代理 A）：结论与本文档第一手证据一致（注册表无 trust 字段/无独立 capability registry/MCP 三轨互不相通：connector 直入 L1、adapter 无调用方、executor 死路径；approval 仅存于 L3 内容变更与 HITL 调用点）。A 标注 3 处「未确认」不影响本文件结论：TOOL_PROTOCOL_MAP 是否外部注入、orchestrator.py:3075 注入前是否过滤 enabled、agent/ 顶层遗留 *_tools 模块是否仍被直接 import——S1-01 接线时复核。

---

## 附录 D：S1-01 回写注记（2026-09-09，TASK-S0-02 验收遗留 #2 收口）

> TASK-S1-01（ToolDescriptor 契约层）已按本文件 C7 与 §3.2/§3.4 落地
> `agent/descriptors/`（models/validator/registry/bridge，详见 TASK-S1-01 验收报告）。
> 本注记回写 S0-02 验收报告遗留 #2：**L3 轻量视图映射未调整矩阵，仅实现化**。

| 回写点 | S0-02 原文 | S1-01 实现（无矩阵调整） |
|---|---|---|
| L1 MCP 接入即 borrowed | §3.2 L1 列 ✅ | `bridge.descriptor_from_mcp_tool` 默认 stage=borrowed + trace_policy 默认调用侧策略（S2 台账前置声明） |
| 内置工具 native 证据缺 | §3.4 存量不自动置位 | `descriptor_from_builtin_tool` 默认 stage=None（30 天零回退台账前置，S3 补验） |
| L3 外来导入≈borrowed 特例 | §3.2 L3 列 ◐ / §3.4 | `map_skill_stage`：claude/community/mcp/ai_generated 或 github:/url:/http:/install/market/external_agent → borrowed（自动补 trace_policy） |
| L3 deprecated 同义复用 | §3.4 | status deprecated/archived → stage=deprecated |
| published 不自动 internalized | §3.4 | published（本土）→ stage=None，rationale 注明"无验收证据待 S3" |
| risk/data_class/stage None 语义 | §2.3 全缺摸底 | 模型层 None=未评估/未分级/未入轨（validator 出 warning 不出 error），S1-02 回填目标显式化 |
| 七态/trust/provenance 字段全缺 | §2.1/§5.2 | 九字段组模型 + 三不变量校验器 + ID 规则（cp.<source_id>.<upstream_id>）+ registry 合并/variant/alias 全部落地 |
