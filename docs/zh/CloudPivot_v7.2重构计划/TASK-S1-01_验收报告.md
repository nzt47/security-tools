# TASK-S1-01 验收报告 — ToolDescriptor v2.1 契约层（模型 + 校验器 + Registry）

> 归档日期：2026-09-09
> 所属计划：CloudPivot v7.2 重构计划（S1 契约层）
> 任务：[TASK-S1-01_ToolDescriptor契约层.md](TASK-S1-01_ToolDescriptor契约层.md)
> 依赖输入：TASK-S0-01（RFC 方案 A + 术语映射表）、TASK-S0-02（C7 对象范围 +
> §3.2/§3.4 状态机作用域矩阵）、01 号审计报告 §6（G1/S1 行）
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.6/§3.2/§3.3.1（P7.2-24/P7.2-05）
> 交付记录：commit `fec4c4ec`（feat(descriptors)）＋ `9bf0988a`（CI Shard6 轮询预算修复，
> 详见 [`S1-01_交付结案报告_20260909.md`](S1-01_交付结案报告_20260909.md) §4.3）已推送
> origin(github) + gitee；push 门禁 ci.yml 全 job success
> 状态：✅ 验收通过

---

## 一、执行摘要

1. **独立包落地**（S0-01 RFC 方案 A 与 S0-02 C7 裁定）：新增 `agent/descriptors/`
   （models/validator/registry/bridge），模块级零依赖 skills_mgmt/agent.tools，
   可被 skills_mgmt / mcp / workflow_learning 复用，未触碰任何存量模块主轨。
2. **契约模型**：`ToolDescriptor`（Pydantic v2）覆盖 v7.2 §3.2 全部九字段组
   （meta/origin/tenancy/capability/trust/runtime/evolution/quality/governance），
   枚举值域与文档一致（provenance/data_class/risk 各四级、七态、scope、cp.hint 注记）；
   存量缺字段以 **None=未评估/未分级/未入轨** 显式建模（云枢裁定，见报告 §四）。
3. **校验器强制三不变量**：destructive 三件套必填 / secret 禁外部端点 /
   borrowed 必记轨迹策略；ID 规则 `cp.<source_id>.<upstream_id>`；cp.hint 注记类型。
4. **Registry**：注册/查询/列表 + schema 同门三路投票 ≥0.9 **去重合并留 alias** +
   schema 不同 **variant 分裂**（记录 diff）+ 跨租户禁合并；JSON 原子持久化（损坏
   备份 + 非法条目 advisory 跳过）；回填写入 API（update_trust/mark_provenance/
   set_stage/set_governance/update_fields）+ `list_with_trust()` 能力清单导出 + 审计轨。
5. **读侧桥接（advisory）**：MCP list_tools / 内置工具 / SKILL.md → descriptor 视图；
   `map_skill_stage` 按 S0-02 §3.2/§3.4 决策表映射 L3 stage；`bridge_to_skill` 只读
   单向映射，绝不写回 Skill；单条失败隔离不阻断主流程。
6. **测试与回归**：新增 124 例（≥40 要求），包覆盖率 **91%**（models 96 / validator
   93 / registry 90 / bridge 89，≥80% 要求）；技能中心 + MCP 套件 403 passed / 1 既有
   xfail（基线，与本任务无关），邻接抽样 201 passed，零新增失败。

---

## 二、预期成果对照（任务 §三）

| # | 预期成果 | 交付 | 位置 | 验收 |
|---|---|---|---|---|
| 1 | `agent/descriptors/` 新包（models/validator/registry + 单测 ≥40 例，覆盖率 ≥80%） | ✅ | `agent/descriptors/{models,validator,registry,bridge,__init__}.py`；单测 **124 例**（models 26 / validator 26 / registry 42 / bridge 30），包覆盖率 **91%** | ✅ |
| 2 | MCP/内置工具/外部技能的 Descriptor 视图桥接（读侧） | ✅ | `bridge.py`：descriptor_from_mcp_tool / descriptor_from_builtin_tool / skill_to_descriptor / bridge_to_skill / register_bridge_view（advisory） | ✅ |
| 3 | trust/provenance 回填写入 API（供 S1-02） | ✅ | `registry.py`：update_trust / mark_provenance / set_stage / set_governance / update_fields（写前校验不变量 + 审计留痕） | ✅ |
| 4 | Descriptor 能力清单导出（供能力地图） | ✅ | `registry.list_with_trust()`（含 aliases/variant_count/undo 标记/trust 摘要） | ✅ |
| 5 | `TASK-S1-01_验收报告.md` | ✅ | 本文件 | ✅ |

---

## 三、验收清单逐条核验（任务 §四）

### ✅ 1. ToolDescriptor 覆盖 §3.2 全部字段组，枚举值域与文档一致

| 字段组 | 落地模型 | 值域/约束 |
|---|---|---|
| meta | `MetaInfo` | id（`cp.<source_id>.<upstream_id>` 格式校验）/ version（SemVer）/ created_at / updated_at |
| origin | `OriginInfo` | source_type（SOURCE_* 五类 ∪ 六类适配器 ∪ skill/manual 归一）/ source_id / provenance 四级（unknown/decilared/…见下） |
| tenancy | `TenancyInfo` | tenant_id / scope（local/project/org） |
| capability | `CapabilityInfo` | name / description / input_schema（字段级 `cp.hint` 注记，P7.2-24，`extract_cp_hints()`）/ output_schema |
| trust | `TrustInfo` | risk_level 四级（low/medium/high/destructive）/ data_class 四级（public/internal/confidential/secret）/ requires_approval |
| runtime | `RuntimeInfo` | timeout_ms / retry_policy（mode: none/fixed/exponential + max_retries/backoff）/ idempotent |
| evolution | `EvolutionInfo` | stage 七态（borrowed/mirrored/shadow/internalized/native/permanent_borrowed/deprecated）/ internalize_attempts / shadow_config / trace_policy |
| quality | `QualityInfo` | success_rate / p99_latency_ms / sample_count / **regression_baseline_id（P7.2-24）** |
| governance | `GovernanceInfo` | policy_ref / audit_level（none/summary/full）/ undo_hint / compensating_action |

枚举值域断言（test_descriptors_models.py::TestEnumDomains，5 例）+ 各字段数值域
（quality/timeout 越界拒绝）+ 序列化往返（枚举→JSON 字符串→模型）均有单测。

### ✅ 2. 校验器强制三不变量

`validator.py::validate_descriptor()` / `assert_valid()`：

| 不变量 | 规则 | 单测（test_descriptors_validator.py） |
|---|---|---|
| I1 destructive 三件套 | risk=destructive ⇒ requires_approval=True ∧ undo_hint 非空 ∧ compensating_action 非空（任一缺失 errors 拒绝） | TestDestructiveInvariant 7 例 |
| I2 secret 禁外部端点 | data_class=secret ∧ origin.external_endpoint=True ⇒ 拒绝 | TestSecretInvariant 4 例 |
| I3 borrowed 必记轨迹 | stage=borrowed ∧ trace_policy 空 ⇒ 拒绝；其余六态不要求 | TestBorrowedInvariant 5 例 |
| I4 ID 规则 | meta.id 必须匹配 `cp.<source_id>.<upstream_id>`（模型字段校验 + 校验器复核） | TestIdFormat + I4 3 例 |
| I5 cp.hint 注记 | input_schema 属性级 `cp.hint` 键值必须是 dict（P7.2-24） | TestIdAndCpHintChecks 3 例 |

分级设计：`validate_descriptor()` 永不抛异常（批量/load/UI 用）；`assert_valid()` 写前
强制抛 `DescriptorValidationError`（携带 errors/warnings/descriptor_id/code），
registry 与写入 API 均走 assert_valid 兜底。未回填项（risk/data_class/stage None、
provenance=unknown）进 **warnings**（S1-02 回填目标），不出 error（S0-02 存量零迁移裁定）。

### ✅ 3. ID 规则生效；schema 相同合并（三路 ≥0.9）与不同 variant 分裂均有用例

`registry.py`（test_descriptors_registry.py）：

- **ID 规则**：`cp.<source_id>.<upstream_id>` 在 MetaInfo 字段校验强制；桥接层按
  server/tool 名与 skill id 自动派生（sanitize_id_part）。
- **去重合并**：同 id 重复 → updated/unchanged；**跨来源同名近名 + schema 同门
  （input/output 结构相似度 ≥0.9）→ 三路投票（input_schema/output_schema/
  name_or_description 结构签名 F1 相似度，均值 ≥0.9）→ 合并，旧 id 进 **alias 表**
  （reason=dedupe-merge，记 votes/时间/操作者）。用例：TestMergeDedupe 5 例
  （含保守合并字段断言：risk 取更严格、requires_approval OR、provenance 取更高、
  quality 样本加权）。**跨租户/跨 scope 禁合并**（不变量 #7 数据不出域）。
- **variant 分裂**：同名但 schema 不同 → 各自登记 + `variants_of(canonical)` 记录
  reason=schema_diff 与 input/output diff keys。用例：TestVariantSplit 3 例。
- **宁冗余勿误合**（P7.2-05）：schema 同门但名称明显不同 → 独立登记不合并。
- **alias 往返**：resolve_alias/aliases_of/alias_records + 持久化后重载仍可解析
  （TestPersistence）。

### ✅ 4. MCP list_tools → descriptor 自动生成演示通过；Skill↔Descriptor 只读桥接不写回

- 演示（`bridge.demo_pipeline()`，纯样例数据，测试驱动验证 + 实跑日志见 §五.3）：
  ① MCP list_tools 2 tool → 2 descriptor（mcp/borrowed/declared）；
  ② 内置工具 2 → 2 descriptor（builtin/verified/stage=None）；
  ③ SKILL.md 2 条（published 本土 + claude 外来导入）→ 轻量视图（stage=None/borrowed）；
  ④ 第二 MCP server 同 schema read_file → **去重合并留别名**（votes mean=1.0）；
  ⑤ `list_with_trust()` 导出 6 条能力清单。
- **Skill↔Descriptor 只读**：`skill_to_descriptor`/`bridge_to_skill` 只做字段映射
  （id/name/description/config_schema→input_schema/output_schema/metrics→quality），
  测试 `test_read_only_no_write_back` 断言原 skill 字典逐键不变；模块无任何写回路径。
- `map_skill_stage` 决策表对齐 S0-02 §3.2/§3.4：外来导入（claude/community/mcp/
  ai_generated 或 github:/url:/http:/install/market/external_agent）→ borrowed；
  deprecated/archived → deprecated（同义复用）；published 本土 → None（无验收证据
  不置位，rationale 注明"待 S3 补验"）。用例 7 例。

### ✅ 5. 桥接失败不阻断技能中心主流程（advisory 语义用例）

- `BridgeBatchResult`/`BridgeRunSummary`/`bridge_to_skill(error_sink)` 全部 item 级
  失败隔离；`register_bridge_view` 单条畸形 tool（名字清洗为空 → ID 非法）仅记 error，
  其余条目正常入库（`test_invalid_item_does_not_block`）；skill 无 id 条目记 error
  不抛（`test_skill_items_errors_collected`）；loader 异常/资产不存在返回 None 记
  error_sink（3 例）。Registry.load() 对非法条目 advisory 跳过并记 invalid_entries
  （`test_load_invalid_entries_advisory`）。

### ✅ 6. 新增单测全绿且覆盖率 ≥80%；技能中心/MCP 既有套件无回归

| 项 | 结果 |
|---|---|
| 新增单测 | **124 passed / 0 failed**（models 26 / validator 26 / registry 42 / bridge 30；5.15s） |
| 包覆盖率 | **91%**：models 96% / validator 93% / registry 90% / bridge 89% / `__init__` 100% |
| 技能中心 + MCP 套件 | **403 passed / 1 xfailed**（52.4s）——xfail 为既有基线
  `test_skills_mgmt.py::TestRetrievalEvaluation::test_skill_retrieval_precision_above_threshold`
  （TF-IDF 基线，与本任务无关，S0-01 报告同款基线） |
| 邻接抽样 | **201 passed**（tool_calling_comprehensive / tool_definitions_yaml /
  skill_bridge / workflow_engine / agentskills_io_compat，11.8s） |
| 语法检查 | `py_compile` 5 个新后端文件全部通过 |

套件明细（技能中心 + MCP）：test_skills_mgmt / test_skills_digest_assessor /
test_skills_classifier / test_skill_lifecycle / test_reviewer / test_review_enforcement /
test_routes_workflow_learning / test_mcp_adapter / test_mcp_executor / test_skill_registry /
test_skill_merge / test_process_distill / test_workflow_to_skill / test_workflow_learning。

### ✅ 7. doc-drift/schema 快照联动登记完成（若项目启用）

- 复核结论：**项目未启用 doc-drift/schema snapshot/ALLOWLIST 机制**——全仓 JSON 配置与
  `agent/**` grep `ALLOWLIST|allowlist|schema_snapshot|doc.drift` 零命中；
  `.guard_baseline.json` 是 import-degradation 守卫清单，与 schema 快照无关。
  因此按任务"如项目启用"条件登记为 **N/A（机制不存在）**，不新建半吊子快照轨。
- 替代联动已落地：术语映射表/消化对象矩阵回写（见 §五.2）——即本任务对
  §13.3 联动表"ToolDescriptor schema ⇒ 校验器 +（后续）渲染"的文档侧登记：
  校验器已在本包交付；渲染联动（UI 能力地图消费 `list_with_trust()`）登记给 S6。

---

## 四、关键设计裁定（对 v7.2 未闭合点的显式化，均已在代码模块 docstring 标注）

| # | 裁定点 | 结论 | 理由 |
|---|---|---|---|
| D1 | risk/data_class/stage 的"未评估"表示 | **None=未评估/未分级/未入轨**（模型允许 None，validator 出 warning） | v7.2 无 unknown 取值；存量资产全缺（S0-02 摸底），默认低风险/低敏感会造成"假安全"；S1-02 回填后收敛 |
| D2 | "secret 禁外部端点"的落点字段 | `origin.external_endpoint: bool`（sse 远端 MCP 默认 True） | §3.2 无独立端点字段；§2.5 Router 规则同源 |
| D3 | "borrowed 必记轨迹"的落点字段 | `evolution.trace_policy: str`（borrowed 必填；MCP/外来 skill 桥接默认调用侧策略 `trace:<src>:call-side(S2-ledger-pending)`） | 统一 Trace 台账未建成（S0-02 §3.5，S2 前置），先声明策略引用 |
| D4 | source_type 归一 | builtin/plugin/mcp/generated/market ∪ claude/community/…→skill 视图 ∪ cli/subagent/sdk/rest/manual | 双来源枚举（S0-01 术语表待办）在 descriptor 层收敛，细节保留 origin.source_id/evidence |
| D5 | 合并规则确定性 | 三路投票＝input/output 结构签名 F1 相似度 + name_or_description；schema 同门（双双 ≥0.9）且均值 ≥0.9 且名称 ≥0.55 才合并；否则同名不同 schema→variant、不同名→独立 | 宁冗余勿误合（P7.2-05）；跨租户（不变量 #7）禁合并 |
| D6 | 保守合并 | risk/data_class 取更严格、requires_approval OR、provenance 取更高+evidence 并集、idempotent AND、quality 样本加权、stage 既有非 None 优先 | 合并声明不优于任何单方声明 |
| D7 | 内置工具 stage | None（native 需 30 天零回退台账，S0-02 §3.4 不置位，S3 补验）；MCP 工具 default borrowed | 桥接读侧视图不伪造演进证据 |
| D8 | 桥接 provenance 初始值 | builtin=verified（代码即证据）；MCP discover=declared（仅 list_tools 声明，六步探针未跑）；外来 skill=unknown（无签名） | §2.3 证据纪律；verified/signed 需 evidence（mark_provenance 强制） |

---

## 五、联动与文件清单

### 5.1 代码改动（新增，零存量主轨修改）

| 文件 | 内容 |
|---|---|
| `agent/descriptors/__init__.py` | 公共 API 导出（__all__） |
| `agent/descriptors/models.py` | 九字段组模型 + 7 枚举 + DescriptorValidationError + extract_cp_hints |
| `agent/descriptors/validator.py` | DescriptorValidationResult + validate_descriptor/assert_valid（三不变量/I4/I5） |
| `agent/descriptors/registry.py` | DescriptorRegistry（注册/合并/分裂/alias/持久化/写入 API/list_with_trust/审计）+ 相似度原语 + 三路投票 |
| `agent/descriptors/bridge.py` | MCP/内置/SKILL 桥接 + map_skill_stage + bridge_to_skill + register_bridge_view + demo_pipeline |
| `tests/unit/descriptors_util.py` | 测试共享构造器 |
| `tests/unit/test_descriptors_models.py` | 26 例 |
| `tests/unit/test_descriptors_validator.py` | 26 例 |
| `tests/unit/test_descriptors_registry.py` | 42 例 |
| `tests/unit/test_descriptors_bridge.py` | 30 例 |
| `.gitignore` | + `data/descriptors.json`、`data/descriptors.corrupted.json`（运行时台账不入库，同 skills_mgmt.json 惯例） |

### 5.2 文档联动

| 文件 | 动作 |
|---|---|
| `docs/zh/CloudPivot_v7.2重构计划/术语映射表.md` | 增补 4 行（ToolDescriptor 九字段组、capability_id/alias/variant、双来源枚举归一〔S1-01 部分收口〕），更新 provenance/data_class 行与 Capability/Tool 行（S1-01 已补） |
| `docs/zh/CloudPivot_v7.2重构计划/TASK-S0-02_消化对象三层盘点_结论.md` | 附录 D：S1-01 回写注记（S0-02 验收遗留 #2 收口：L3 轻量视图映射未调整矩阵、仅实现化） |
| 本报告 | 验收核验 + 遗留清单 |

### 5.3 演示实跑证据（demo_pipeline，2026-09-09）

```
mcp_discover : {registered: 2, errors: []}          # list_tools 2 tool → borrowed/declared
builtin      : {registered: 2, errors: []}          # builtin/verified/stage=None
skills       : {registered: 2, errors: []}          # published 本土 → None；claude 外来 → borrowed
dedupe_merge : {merged: 1, errors: []}              # fs-dup/read_file 并入 filesystem-mcp/read_file
capability_count: 6 ；aliases: {cp.fs-dup.read_file: {canonical_id: cp.filesystem-mcp.read_file,
                reason: dedupe-merge, votes: {sim_input: 1.0, sim_output: 1.0, sim_name: 1.0, mean: 1.0}}}
capability_map_sample: list_with_trust() 首 3 条（含 risk_level/data_class/stage/undo 标记）
```

---

## 六、遗留清单（不阻塞本任务验收，供下游消费）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | 存量资产 trust/provenance/undo_hint **字段回填**（本包已交付模型 + 校验器 + 写入 API） | S1-02（update_trust/mark_provenance/set_governance 消费） |
| 2 | MCP provenance 升 verified/signed 需六步探针 evidence（§2.3）；探针设施 | S1-02/S2 |
| 3 | 七态转移条件（判定集/灰度 72h/30 天零回退）不设状态机门，set_stage 自由写 | S3-02/03 |
| 4 | 内置工具/MCP/技能 → Registry 的**运行时接线**（demo 用样例数据；真实 list_tools 拉取 + registry 装载入口待 routes/生命周期挂接） | S1-02（接线）+ S6（能力地图 UI 消费 list_with_trust） |
| 5 | 双来源枚举（SOURCE_* / SkillCategory）descriptor 层已归一；L1/L3 双轨口径的完整统一 | S0-01 术语表闭环（本任务部分收口） |
| 6 | data/descriptors.json 为运行时台账（.gitignore 已加）；多进程并发写（单机锁内串行，未做跨进程锁） | S2/生产化评估 |
| 7 | doc-drift/schema snapshot/ALLOWLIST 机制项目未启用 → N/A 登记 | —（如后续启用，先补 ToolDescriptor schema 快照） |

---

*补记：回归仅新增模块与文档，未修改任何存量后端代码；如后续全量 tests/ 出现与本任务
相关的失败，以本报告 §三.6 定向 + 邻接 + 抽样套件为准复核。*
