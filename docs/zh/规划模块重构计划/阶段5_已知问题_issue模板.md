# 阶段 5 已知问题 · GitHub Issues 模板

> 来源：`阶段5_已知问题任务卡片.md`（P1/P2/P3）
> 用法：每个模板块可直接复制到 GitHub 新建 Issue（标题 / labels / 正文均已填好）；
> labels 需在仓库中预先创建：`planning`、`stage-5`、`tech-debt`。

---

## Issue 1 · P1

```markdown
---
title: "[P1] 接入 token 计费，回填成本口径"
labels: ["planning", "stage-5", "tech-debt"]
assignees: []
---

### 背景与根因

`cost_total` 恒 0.0 的根因是 Plan 路径 `planning/executor.py` 只 `record_step`、
从不 `record_text`/`record_tokens`，budget 快照 `cost=0.0`（ReAct 路径
`react.py:_think` 已有记账）。评测集恰好全部走 Plan 路径，故聚合成本为 0。

### 预演输入

`scripts/pilot_cost_forecast.py` 产出 `cost_pilot_forecast.json`
（估算口径：token=字符数/3，单价 0.002 USD/1k）：
13 用例全量 ≈ $0.0032，其中 parallel 类主导（$0.003162，占 ~98%，6 次 LLM 调用），
multi_step 与 failure_injection 规则分解 0 成本，simple 直答 $0.000052。

### 实施要点

1. executor 任务执行处（LLM 调用/工具结果聚合点）调用 `budget_manager.record_text` 记账；
2. `token_price_per_1k` 从配置透传：`config.yaml planning.token_price_per_1k`，
   默认 0.002 与 `budget.py` 一致（core 已有透传路径，直接复用）；
3. 成本超限 `EXCEEDED_COST` 降级路径补单测。

### 验收标准

- [ ] `scripts/eval_planning.py` 复跑后 `cost_total` 非 0，且与预演量级（$0.001~0.01/13 用例）吻合
- [ ] 评测基线表成本行回填（`规划评测基线.md`）
- [ ] `EXCEEDED_COST` 降级路径单测覆盖通过
- [ ] `token_price_per_1k` 配置透传生效：改 `config.yaml planning.token_price_per_1k` 后
      budget 快照/埋点 cost 按新单价变化（单测断言）
- [ ] ReAct 与 Plan 两路径计价口径一致（同一 price 与同一 token 估算方式，防双轨漂移）
- [ ] executor 新增记账后既有规划单测全绿（无行为回归）

### 关联

- 任务卡片：`阶段5_已知问题任务卡片.md` #P1
- 预演脚本：`scripts/pilot_cost_forecast.py` / `docs/zh/规划模块重构计划/cost_pilot_forecast.json`
```

---

## Issue 2 · P2

```markdown
---
title: "[P2] 单测隔离 data/reflection 环境耦合"
labels: ["planning", "stage-5", "tech-debt"]
assignees: []
---

### 背景与根因

`Reflector` 默认 `persist_dir=./data/reflection` 会加载系统运行积累的历史教训
（当前 29 条），依赖"空经验库"的用例/验证脚本断言可能失效
（如 `test_decompose_prompt_no_experience_no_injection`）。

### 实施要点

1. 规划模块单测统一改用不可变 fixture 目录（`tmp_path` 派生），
   每个用例显式清空 `lessons_db`/`experiences`；
2. 参考 `scripts/verify_lesson_guidance.py` 已示范的清空写法。

### 验收标准

- [ ] 清空本地 `data/reflection` 后重跑规划单测，结果与清理前一致（不依赖宿主环境残留数据）
- [ ] "空库静默"断言用例化：单测显式断言空经验库时 `_next_hint` 不注入下轮提示词
      （对齐 `verify_lesson_guidance.py` 场景 B 口径）

### 关联

- 任务卡片：`阶段5_已知问题任务卡片.md` #P2
- 验证脚本：`scripts/verify_lesson_guidance.py`
```

---

## Issue 3 · P3

```markdown
---
title: "[P3] 规则分解检索经验（默认不实施，可选增强）"
labels: ["planning", "stage-5", "tech-debt"]
assignees: []
---

### 背景

经验命中率仅统计 LLM 分解路径，规则分解不查询经验库（不改变行为）。

### 默认决策（不实施）

规则分解输出确定性高、行为可预期，不受 LLM 幻觉影响；注入经验收益低且会
改变既有确定性行为（违【不易】）。命中率口径保持"仅 LLM 分解路径"。

### 触发条件（满足其一再启用）

- [ ] 线上出现规则分解类任务的系统性失败模式，且可被历史教训覆盖
- [ ] 业务方明确要求规则分解输出受经验引导

### 启用步骤（若触发）

1. `RuleStrategy.decompose` 分解后追加 `get_advice_for_task` 注入点，
   并计入命中率埋点（`record_experience_lookup`）；
2. 重跑评测集核对成功率不下降；
3. 同步更新命中率口径文档（`规划评测基线.md`）。

### 验收标准

- [ ] 默认不实施：决策与理由记录在案；`规划评测基线.md` 命中率定义注明
      "仅 LLM 分解路径"（可直接检索验证）
- [ ] 启用时：按启用步骤全量验证并更新基线，成功率 ≥ 原基线，且
      `record_experience_lookup` 的 task_type 标签计数与检索实际次数一致

### 关联

- 任务卡片：`阶段5_已知问题任务卡片.md` #P3
```
