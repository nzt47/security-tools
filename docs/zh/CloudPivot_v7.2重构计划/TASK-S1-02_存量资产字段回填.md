# TASK-S1-02 provenance / trust / undo_hint 存量资产字段回填

> 所属阶段：S1 契约层｜依赖：TASK-S1-01（Descriptor 模型与写入 API）｜预估：3–5 人日
> 来源设计文档：`CloudPivot_v7.2_final_合并归档(智谱审核版).md` §2.3（manifest/provenance）/§3.2（trust 字段）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

对云枢**存量技能/工具资产**执行 provenance 四级、trust 风险/数据分级与 undo_hint/compensating_action 的回填与迁移，使资产逐步满足 v7.2 契约要求，同时**保持既有行为与接口不变**（不破坏存量状态机与发布门禁）。

核心口径（v7.2）：
- provenance 四级：`unknown → declared → verified → signed`；仅 `verified+` 可进自动化流水线，`unknown` 只能人工单步执行。
- data_class 四级：`public / internal / confidential / secret`。
- risk_level 四级（映射自 v7.2：低/中/高/destructive——以文档 risk 四级为准；destructive ⇒ requires_approval ∧ undo_hint ∧ compensating_action 全必填）。
- manifest 思想（§2.3）：外来技能安装时应带 provenance 与 license 声明（云枢 `install_precheck`/`extensions.security_checker` 已有安全预检，需与之合并而非另起炉灶）。

## 二、执行步骤

### 步骤 1：存量资产字段现状摸底
- 对 `data/skills.json`（及 skills_repo 文件轨）资产抽样全量盘点：哪些已有 `is_sensitive`（models.py 已有）、哪些有 config_schema/output_schema、哪些无任何风险/来源标记。
- 检查外来技能（install/install_from_zip/import_queue）路径现有 provenance 信息（来源 scheme：github/url/local/registry——见 `service.py` install_precheck），确认能否自动归类 provenance。
- 输出 `存量资产字段摸底表`：资产数 × 来源分布 × 已有标记覆盖率（is_sensitive/config_schema/来源记录）。

### 步骤 2：回填规则设计（确定性规则优先，禁默认全高/全低）
- provenance：按来源 scheme 自动映射（builtin=verified、custom=declared、claude/community/mcp/ai_generated=按安装来源声明，本地无签名证据一律 unknown 或 declared 并给出升级路径）；保留人工提升通道（reviewer 标记 + 审计留痕）。
- data_class：基于 `is_sensitive` + 技能内容特征（是否收集数据、是否外发——可复用 assessor 的 DATA_COLLECT_SENSITIVE 维度）自动判定，confidential/secret 需人工复核确认。
- risk：基于已装评估维度（权限/攻击面/数据合规/兼容性——`assessor.py` digest 扩展评估）映射；`risk=destructive` 类（危险写/外发/删除类操作，参考现有脚本全维度审查）必须人工复核。
- undo_hint/compensating_action：对 risk≥high 的资产**必须**提供真实可执行的补偿动作描述（可引用技能自身回滚/rollback_version 机制），缺失则打 `NEEDS_UNDO_HINT` 标记并进入待补队列（不阻断既有发布，但发布门禁提示）。

### 步骤 3：回填实施（分批复填 + 幂等）
- 使用 TASK-S1-01 提供的写入 API（`update_trust`/`mark_provenance`）批量回填；逐条写审计（对齐"自动化不可做不可见之事"与审计平权）。
- 分批处理（如 200 条/批）并支持 dry-run 报告；每批校验：destructive 三件套是否齐、secret 是否禁外部端点、provenance=verified+ 是否才允许进自动化。
- 存量资产**不回填失败即回滚该批**，主流程零影响。

### 步骤 4：校验与回归
- 运行 TASK-S1-01 校验器对回填后 descriptor 全量重校验，未达标清单化（如遗留 NEEDS_UNDO_HINT 资产清单）。
- 回归：技能中心全链路（列表/检索/审核/发布/安装）+ 前端相关页面（技能详情展示新字段徽章——如无 UI 改动则仅数据层）。
- 撰写 `TASK-S1-02_验收报告.md`（回填统计 + 未达标清单 + 兼容验证）。

## 三、预期成果

1. 存量资产 provenance/trust/undo_hint 字段回填（覆盖率报告 + dry-run 记录）。
2. `NEEDS_UNDO_HINT`/`NEEDS_REVIEW` 待补清单（供人工复核与后续阶段消费）。
3. 外来技能安装路径的 provenance 合并预检（与 install_precheck 集成）。
4. 全量 descriptor 重校验通过（或清单化残留）。
5. `TASK-S1-02_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 摸底表与实际资产规模一致（可复核计数）
- [ ] 回填规则确定性可复现（dry-run 两次结果一致）；无"一刀切全 high/全 low"
- [ ] destructive 类资产三件套（approval+undo_hint+compensating_action）100% 齐备或进待补清单且未静默放行
- [ ] secret 类资产无外部端点配置（校验器通过）
- [ ] 仅 provenance ≥ verified 的资产标记为可进自动化（其余标记人工单步）
- [ ] 回填过程逐条审计留痕；批量失败回滚不残留半批状态
- [ ] 既有技能中心/前端行为无回归（测试全绿）
- [ ] 遗留待补清单明确到资产级且带处置路径
