# 真实任务样本池（data/evals/）

替代 mock 的 `data/benchmark/`，为进化评估提供**真实任务样本**。本目录为**技能评估集一等资产**：
schema 由 `scripts/eval_samples_validate.py` 强制校验，样本集版本由 `manifest.json` 登记，
回归门禁（`agent/skills_mgmt/eval_regression.py`）以"版本 + 类别"解析样本集。

## 目录结构

```
data/evals/
├── README.md             # 本文档（schema v1）
├── manifest.json         # 样本集版本登记（版本 → 类别 → 样本 id 清单）
├── baselines.json        # 回归基线存储（门禁运行时生成；skill_id → 版本 → 基线分）
├── _pending/             # 扩充管道 DRAFT 草稿（见 _pending/README.md，不入评估集）
├── search/               # 搜索类样本（精确/关键词校验）
│   └── qa_pairs.json
├── code/                 # 代码类样本（validator 表达式校验）
│   └── code_tasks.json
├── chat/                 # 对话类样本（开放域，走自一致性+反馈）
│   └── dialog_flows.json
├── tool/                 # 工具调用类样本（计算器/时间/搜索/文件等工具）
│   └── tool_tasks.json
└── planning/             # 规划类样本（任务拆解/学习计划等）
    └── planning_tasks.json
```

**目录即类别（Category）**：`<类别目录>/` 下的所有 `.json` 合并为该类别的样本集。新增类别只需新建目录放 JSON 文件，**无需改代码**。

## 类别（schema v1）

| 类别 | 说明 | 评估器（EvaluatorRegistry） |
| --- | --- | --- |
| `search` | 检索/问答/知识查询 | `SkillExecutorEvaluator`（exact/contains/json） |
| `code` | 代码生成/算法实现 | `SkillExecutorEvaluator`（validator，允许表达式） |
| `chat` | 开放域对话/建议 | `SkillExecutorEvaluator`（自一致性 + 反馈信号） |
| `tool` | 工具调用（计算/时间/文件等） | 未注册 → 分阶段 LLM 评估 |
| `planning` | 规划/拆解/计划 | 未注册 → 分阶段 LLM 评估 |
| `general` | 兜底类别（代码内保留） | `LlmEvaluator`（无 LLM 时降级跳过） |

> 类别目录只放**正式样本**；`_pending/`、`manifest.json`、`baselines.json` 不是类别。
> 门禁只按 `manifest.json` 解析样本集，绝不扫描 `_pending/`（DRAFT 不入评估集）。

## 样本格式

单文件可以是 JSON 数组，或 `{"id": {样本}}` 字典。每条样本字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 样本唯一 ID（字符串，全池去重） |
| `category` | 是 | 类别（须与所在目录一致，schema v1 强制） |
| `task` | 是 | 任务描述（会作为执行参数 `task` 传入技能脚本） |
| `expected_output` | 否 | 期望输出校验器。**缺省 = 开放域任务**，走替代验证 |
| `created_at` | 否 | 创建时间（ISO 格式） |
| `metadata` | 是 | 附加信息（见下）；`metadata.input` 字典会并入执行参数 |

### metadata 必填字段（schema v1）

| 字段 | 必填 | 允许值 | 说明 |
| --- | --- | --- | --- |
| `input` | 是 | dict | 执行参数（并入 run_params） |
| `difficulty` | 是 | `TRIVIAL` / `SIMPLE` / `NORMAL` / `COMPLEX` | 任务难度（复用 `enhanced_planner` 复杂度语义，为任务7 提供元数据） |
| `source` | 是 | `manual` / `reflection` / `feedback` / `novelty` | 样本来源（人工标注 / 反思产物 / 反馈失败案例 / 新颖事件） |
| `input_hash` | 是 | 16 位 hex | 去重哈希：`sha256(canonical_json({category, task, input}))[:16]`（`eval_sample_ingest.compute_input_hash`） |
| `note` | 否 | str | 备注 |

### expected_output 四种校验类型（OutputChecker）

| type | 结构 | 判定 |
| --- | --- | --- |
| `exact` | `{"value": v}` | 输出与 `v` 完全相等 |
| `contains` | `{"values": [...]}` | 输出文本包含全部子串 |
| `json` | `{"key": k, "value": v}` | 输出 JSON 中 `result[k] == v` |
| `validator` | `{"expression": "..."}` | 受限 `eval`，表达式内 `result` 即执行输出 |

> **安全（守不易）**：`validator` 表达式在受限环境执行（禁 `import`/`__`/`;`/`open(`/`eval(`/`exec(`）。
> 类别 `code` 注册评估器时显式开启 `allow_validator=True`；其余类别禁用（命中 → 样本 skipped）。

## 样本集版本（manifest.json）

`manifest.json` 登记样本集版本，门禁按 `(版本, 类别)` 解析样本 id：

```json
{
  "schema_version": 1,
  "current": "v1",
  "versions": {
    "v1": {
      "description": "...",
      "created_at": "...",
      "categories": {"search": ["search-001", ...], ...}
    }
  }
}
```

- 新增/删除样本后必须**同步更新 manifest**（校验脚本强制：manifest 中 id 必须存在、池中样本必须入册）。
- 样本集版本变化 → 旧基线仍按旧版本记录，互不干扰（回归门禁按版本查基线）。

## 如何扩充样本

1. 人工扩充：在对应类别目录追加样本条目（保持数组格式），同步更新 `manifest.json`，运行校验：
   ```bash
   python scripts/eval_samples_validate.py            # 全量校验（0 非法 → 通过）
   ```
2. 自动回流（DRAFT → 审核 → 入评估集）：见 `agent/skills_mgmt/eval_sample_ingest.py`
   （默认关闭 `EVAL_SAMPLE_INGEST_ENABLED=false`；开启后只产 `_pending/` DRAFT，
   审核通过才入正式类别）。
3. 命令行一键初始化（幂等，不覆盖已有样本）：
   ```bash
   python scripts/dev/init_eval_samples.py
   ```

## 回归门禁

```bash
# 门禁 CLI：评估某技能在 v1 样本集上的回归结果（首次评估自动记录基线）
python -m agent.skills_mgmt.eval_regression --skill <skill_id> --set v1 --budget 500k
# 输出 RegressionResult: status=PASS/FAIL/NO_SAMPLES/budget_exceeded, score, delta_vs_baseline, used_tokens
```

- 基线语义：首次评估记录基线分（`baselines.json`，谱系可查）；后续进化产物 `delta < -阈值（默认 0.05）` → FAIL。
- 接线：`offline_evolver` 提交判定钩子（`EVOLUTION_REGRESSION_GATE=warn_only` 默认只读告警）；TASK-04 发布审核链只读查询。

## 样本来源红线（守不易）

- 样本**禁止来自用户真实敏感数据**；自动回流素材（reflection/feedback/novelty）必须经
  `agent.security_utils.DataSanitizer` 脱敏后才可入 DRAFT（`_pending/README.md` 详述）。
- 若某类别无样本/版本未登记，评估器返回 `status="no_samples"`，**绝不伪造指标**。
- 校验脚本保证：id 全池唯一 + `input_hash` 全池唯一 + 字段合法性 100%。

## 变更记录

| 日期 | 版本 | 说明 |
| --- | --- | --- |
| 2026-08-22 | v1 | 初始人工审核评估集：5 类 50 条（search 12 / code 12 / chat 12 / tool 7 / planning 7）；schema v1（difficulty/source/input_hash）；引入 manifest 版本登记与回归门禁 |
