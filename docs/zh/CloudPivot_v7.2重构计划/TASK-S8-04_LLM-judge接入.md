# TASK-S8-04 LLM-judge 凭证接入与成本护栏

> 所属阶段：**S8 生产化加固批次**｜依赖：S3-02（三层比对/判定集）、S3-03（灰度 judge 注入通道）、S5-03（成本口径/UTC）、S2-03（成本事件）｜预估：3–4 人日
> 来源：S7-05 未完成项 #1（`judge_kind = deterministic_local(llm_unavailable)`，21/21 次灰度均未跑真实 judge）、S3-02 遗留 M1、S3-03 灰度期 judge 注入通道
> 验收报告归档于本目录 `docs/zh/CloudPivot_v7.2重构计划/`

---

## 一、目标描述

S3-02 的三层比对里，**层③（软性判定）默认用的是本地确定性打分器**（token Jaccard + 序列相似），真实 LLM-judge 虽已留注入通道（`judge=` 参数）与 `judge_kind` 如实标注，但**因为缺凭证从未真实跑过**。本任务把这条通道接通，并给它配上**成本护栏**（否则 judge 自己会把预算吃光）：

1. **凭证接入**：`CP_DIGESTION_JUDGE_PROVIDER` / `CP_DIGESTION_JUDGE_MODEL` + 凭证来源（SecretStore / `.env`）+ 启动自检；
2. **真实 judge**：结构化输出（判定 + 置信度 + 理由）+ **≥0.85 软性阈值**；`judge_kind` 如实标注（如 `llm:<provider>:<model>` / `deterministic_local(llm_unavailable)` / `deterministic_local(budget_exceeded)`）；
3. **成本护栏**：judge 调用**计入 UTC**（复用 `utc.record_cost()`）+ 每日 judge 预算上限 + 超限自动回落；
4. **降级一致**：无凭证 / 超预算 / 调用失败 → 回落确定性打分器并**标注具体原因**；同一批样本内 `judge_kind` 可解释；
5. **人工校准联动**：10% 人工抽检队列（M1/M5 口径）与 judge 结果对照，用于评估 judge 一致性。

## 二、执行步骤

### 步骤 1：配置与凭证自检
- 定义配置项（`.env` / config，非法值回退默认）：`provider` / `model` / `daily_budget_cents` / `enabled`（**默认关闭**，显式开启才用真实 judge）；
- 凭证来源：优先 SecretStore，回落 `.env`；**启动自检**输出"judge 可用性三态"（`available` / `no_credentials` / `disabled`），供面板与日志读取；
- 凭据安全：不得在日志/审计/事件里输出密钥（沿用既有脱敏口径）。

### 步骤 2：真实 judge 实现
- 在既有注入通道上实现 `LLMJudge`：输入（上游结果 / 候选结果 / 判定问题）→ 结构化输出 `{verdict, confidence, reason}`；
- **阈值**：`confidence ≥ 0.85` 视为软性通过（与 §4.5 一致）；低于阈值 → 层③不通过（触发人工抽检或负例处理）；
- **`judge_kind` 如实标注**（这是本任务诚信底线）：真实 judge 标注 provider+model；回落时标注**具体回落原因**；
- 提示词与输出解析要**结构化校验**（解析失败 → 按 `E_UPSTREAM_FORMAT` 语义处理，不猜）。

### 步骤 3：成本护栏（关键）
- 每次 judge 调用：`utc.record_cost(...)` 计入 UTC（**标注来源为 judge**），使 judge 开销进入日/周成本与断食判定；
- **每日 judge 预算**：`daily_budget_cents` 超限 → 自动停用真实 judge、回落确定性打分器、标注原因、发事件（不得静默）；
- 与 S5-03 的日熔断/周断食联动：断食期可自动停用 judge（策略可配，默认跟随）；
- 报告口径：区分"业务成本"与"judge 成本"两栏（**不得混算**，沿用 S5-02 两列纪律）。

### 步骤 4：抽检联动与一致性
- 10% 人工抽检队列：把 judge 判定与人工判定并列存档，输出**一致性统计**（一致率、分歧样本清单）；
- 分歧样本进入待复核队列（复用 `ManualReviewQueue`），并作为后续 judge 提示词/阈值调整的依据；
- 样本 <20 → 只披露不结论（S5-02 口径）。

### 步骤 5：测试与回归
- 用**桩 judge**（无凭证环境可测）：注入通道、阈值边界（0.84/0.85/0.86）、`judge_kind` 三态、成本计入、超预算回落、解析失败处理；
- 有凭证时（若环境具备）真跑一次小样本并留证；
- 回归：`digestion`（sandbox/gate/shadow/internalize）、`observability`（utc）、`eval` 邻接套件零回归；
- 撰写 `TASK-S8-04_验收报告.md`。

## 三、预期成果

1. judge 配置项 + 凭证自检（三态）+ 面板/日志可读的可用性状态。
2. `LLMJudge`（结构化输出 + 0.85 阈值 + 解析失败处理）。
3. **成本护栏**：judge 计入 UTC + 每日预算 + 超限回落 + 事件。
4. 两栏成本口径（业务 / judge）与一致性统计 + 分歧样本入队。
5. `TASK-S8-04_验收报告.md`。

## 四、评估标准（验收清单）

- [ ] 配置项与凭证自检落地；三态可读（`available` / `no_credentials` / `disabled`）
- [ ] 真实 judge 经既有注入通道生效；阈值 0.85 边界有用例（0.84 拒 / 0.85 过）
- [ ] **`judge_kind` 如实标注**（含具体回落原因）；无凭证时不得冒充 llm
- [ ] judge 调用**计入 UTC**（成本可查）；**每日预算超限自动回落** + 事件（用例）
- [ ] 业务成本与 judge 成本**两栏不混算**（用例断言）
- [ ] 解析失败按 `E_UPSTREAM_FORMAT` 语义处理（不猜、不静默）
- [ ] 抽检一致率统计产出；分歧样本入复核队列；样本 <20 只披露
- [ ] 日志/审计/事件中**无密钥明文**
- [ ] 既有 `digestion`/`observability`/`eval` 套件零回归；新增单测全绿、覆盖率 ≥80%

## 五、说明与风险

- **默认关闭**：真实 judge 会产生额外成本，`enabled` 默认 false；开启后仍受每日预算约束。
- 若环境中始终无凭证：本任务交付"**具备即用**"能力（配置好即生效），并在报告中如实写明"**未在真实凭证下验证**"——不编造。
- judge 的用途限"软性第三层"，**不得**用于替代结构 schema 与副作用硬性比对（前两层永远机械）。
