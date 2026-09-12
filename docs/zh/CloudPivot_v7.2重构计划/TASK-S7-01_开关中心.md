# TASK-S7-01 开关中心（把全部布尔开关与关键阈值呈现在 UI，含三级权限与审计）

> 所属阶段：**S7 稳态运营补完批次**｜依赖：S6-01（治理面板）、S4-01（Actor 矩阵/审批面安全）、S2-02（链式审计）、S2-03（events）｜预估：5–8 人日
> 来源：Owner 决策 2026-09-12「将所有开关在 UI 呈现出来，并作说明」+ 收官审计 §九（面板不得说谎）
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

把云枢散落在**环境变量（73 个，其中布尔开关约 51 个）**与 **`config.yaml`（17 个配置段）**中的开关，统一呈现在工作台 UI 中，并给出**可读说明、当前值、默认值、真实生效来源、风险级与是否需要重启**。核心不是"做一张表单"，而是解决三件事：

1. **可发现**：任何开关都能在 UI 找到，且知道它属于哪个子系统、控制什么行为。
2. **不说谎**：显示**真实生效来源**（`env > ui_override > config > default`）；被环境变量锁定的开关必须**置灰 + 注明"被 X 覆盖，UI 不可改"**——禁止出现"UI 改了但没生效"。
3. **可治理**：切换开关是**治理动作**——按风险三级处理，全部入链式审计与 `policy.decision`，高危项需二次认证（复用 S4-01 矩阵）。

## 二、执行步骤

### 步骤 1：开关注册表（唯一事实源）
- 新增 `agent/settings/` 包：`registry.py` 定义 `SettingSpec`：
  `key / category / type(bool|int|float|str|path) / default / env_name / config_path / risk(A|B|C) / needs_restart / description / validator(range|enum|regex) / owner_module`。
- **完整性来源（防手写清单漂移）**：
  - 从代码机械提取现有开关读取点（`_env_flag(` / `_env_flag_bool(` / `os.environ.get("X")` / `config.get(...)`）——建议写 `scripts/scan_settings.py` 生成候选清单；
  - 与 `agent/monitoring/observability_config.py` 既有配置校验注册表**合并**（该表已有 path/范围/校验/说明，勿重复造）；
  - 机械提取结果必须 100% 被注册表覆盖（**缺口即 CI 失败**，见验收项）。
- **分类**：`自愈与安全 / 学习与进化 / 编排与规划 / 技能与检索 / 可观测与阈值 / 外部依赖与密钥`。
- **风险分级**：
  - **A 可直接切**：可观测采样、日志级别、非关键行为开关（如 `AGENT_PERF_SAMPLE`、`SENSOR_LEARNING_ENABLED` 的**只读观测**类）；
  - **B 需二次认证 + 双人确认**：自愈自动执行、熔断/回滚、关闭沙箱、审批豁免、自动合入类、成本刹车阈值；
  - **C 只读脱敏**：`LLM_API_KEY` / `DEEPSEEK_API_KEY` / `OPENAI_API_KEY` / `SMTP_PASSWORD` / `*_URL` / 绝对路径等——**永不返回明文**（沿用 S4-01 裁定 B 的掩码口径）。

### 步骤 2：生效来源与覆盖层（本任务的技术核心）
- 新增覆盖层 `data/ui_settings.json`（gitignore），解析优先级 **`env > ui_override > config.yaml > 代码默认`**；
- 提供 `resolve(key) -> {value, source, shadowed_by}`：当存在更高级来源覆盖时，`source` 如实返回被谁覆盖；
- 写入 API 只写覆盖层，**绝不修改 `.env` 与 `config.yaml`**（守不易）；
- 生效方式标注：`hot`（下次读取即生效）/ `needs_restart` / `next_task`；无法热生效的必须在 UI 明示。

### 步骤 3：后端 API
- `GET /api/cp/settings`：返回全部条目（元数据 + 当前值 + 生效来源 + 是否被覆盖）；
- `POST /api/cp/settings/<key>`：改值 → 按风险级分流（A 直接、B 二次认证 + 双人确认、C 拒绝 403）；
- `POST /api/cp/settings/<key>/reset`：清除覆盖层（回落到 config/default）；
- 全部路由 `@require_token` + §7.0 矩阵鉴权（**改开关 = human 专属**，auto/sub_agent 一律拒绝）；
- 每次变更：`audit.record(action="settings.change", actor, subject=key, payload={old,new,source})` + `policy.decision` + 事件回执。

### 步骤 4：前端（复用既有）
- 在 `yunshu-ui/src/pages/hub/governance/` 内新增「**开关中心**」栏目（`settings.tsx` + 组件），复用现成 `SwitchField.tsx`；
- 交互要求：分类折叠 + 搜索 + 风险徽章（A/B/C）+ **生效来源标签** + 说明 tooltip + `needs_restart` 标记 + 被 env 锁定时置灰并给原因；
- 只读项（C 级）以掩码显示（如 `sk-****…****`），并提供"仅显示是否已配置"的布尔状态；
- **改动前二次确认**（B 级）：展示"影响面 + 回滚方式 + 生效方式"再提交；
- 复用 S6-01 的口径纪律：**不出现不可追溯的数字**；开关变更历史可从审计链跳转查看。

### 步骤 5：测试与回归
- 新增单测：注册表完整性（机械提取项全覆盖）、优先级解析（四来源组合）、覆盖层写读、B 级二次认证、C 级拒绝、审计留痕、掩码不泄明文；
- 前端：vitest 组件用例 + `tsc`/`eslint` 零告警 + `npm run build:flask` 产物同步；
- 后端邻接回归：`ui_panels`、`security`、`approval`、`audit` 相关套件零回归。

## 三、预期成果

1. `agent/settings/`（registry + resolver + override store）+ `scripts/scan_settings.py`（开关提取与缺口检测）。
2. `GET/POST /api/cp/settings*` 三组路由（含三级权限与审计）。
3. `hub/governance` 内「开关中心」页（分类/搜索/风险徽章/生效来源/置灰说明/只读掩码）。
4. 覆盖层 `data/ui_settings.json` + 优先级解析与"不谎报"文案。
5. `TASK-S7-01_验收报告.md`（含开关清单统计与一张真实生效来源截图说明）。

## 四、评估标准（验收清单）

- [ ] 注册表覆盖**全部**布尔开关与关键阈值；`scripts/scan_settings.py` 的机械提取项**零缺口**（缺口即测试失败）
- [ ] 每个条目都有：说明、默认值、类型、风险级、生效方式；B/C 级不可被 UI 静默修改
- [ ] `resolve()` 如实返回生效来源；**被 env 锁定的项在 UI 置灰并注明原因**（附用例）
- [ ] 写覆盖层**不改** `.env` / `config.yaml`（用例断言文件未被修改）
- [ ] A 级直接生效；B 级无二次认证不可通过；C 级返回 403 且响应体**不含明文**
- [ ] 每次变更入链式审计（含 old/new/source）+ `policy.decision`；审计链 `verify_chain` 仍通过
- [ ] auto / sub_agent 调用改开关一律被拒（Actor 矩阵）
- [ ] 前端 `tsc`/`eslint` 零告警；vitest 新增用例全绿；`build:flask` 产物已同步
- [ ] 既有 `ui_panels`/`security`/`approval`/`audit` 套件零回归；覆盖率 ≥80%
