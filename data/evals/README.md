# 真实任务样本池（data/evals/）

替代 mock 的 `data/benchmark/`，为进化评估提供**真实任务样本**。

## 目录结构

```
data/evals/
├── README.md             # 本文档
├── search/               # 搜索类样本（精确/关键词校验）
│   └── qa_pairs.json
├── code/                 # 代码类样本（validator 表达式校验）
│   └── code_tasks.json
└── chat/                 # 对话类样本（开放域，走自一致性+反馈）
    └── dialog_flows.json
```

**目录即类别（Category）**：`<类别目录>/` 下的所有 `.json` 合并为该类别的样本集。新增类别只需新建目录放 JSON 文件，**无需改代码**。

## 样本格式

单文件可以是 JSON 数组，或 `{"id": {样本}}` 字典。每条样本字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 样本唯一 ID（字符串） |
| `category` | 否 | 类别（缺省取所在目录名） |
| `task` | 是 | 任务描述（会作为执行参数 `task` 传入技能脚本） |
| `expected_output` | 否 | 期望输出校验器。**缺省 = 开放域任务**，走替代验证 |
| `created_at` | 否 | 创建时间（ISO 格式） |
| `metadata` | 否 | 附加信息；`metadata.input` 字典会并入执行参数 |

### expected_output 四种校验类型（OutputChecker）

| type | 结构 | 判定 |
| --- | --- | --- |
| `exact` | `{"value": v}` | 输出与 `v` 完全相等 |
| `contains` | `{"values": [...]}` | 输出文本包含全部子串 |
| `json` | `{"key": k, "value": v}` | 输出 JSON 中 `result[k] == v` |
| `validator` | `{"expression": "..."}` | 受限 `eval`，表达式内 `result` 即执行输出 |

> **安全（守不易）**：`validator` 表达式在受限环境执行（禁 `import`/`__`/`;`/`open(`/`eval(`/`exec(`）。
> 样本池为人工维护的评测数据（非用户输入），且禁用校验器按 `allow_validator=False` 走跳过分支。
> 类别 `code` 注册评估器时显式开启 `allow_validator=True`。

## 如何扩充样本

1. 在对应类别目录追加样本条目（保持数组格式）。
2. 新类别：`mkdir data/evals/<category>`，放入 `<category>/xxx.json`，然后：
   - 若该类别有客观校验标准 → 用 `expected_output`（exact/contains/json/validator）；
   - 若为开放域 → 不写 `expected_output`，评估器自动走**自一致性 + 反馈信号**替代验证；
   - 可选：在 `EvaluatorRegistry` 注册专属评估器（未注册类别默认走分阶段 LLM 评估）。
3. 命令行一键初始化（幂等，不覆盖已有样本）：

```bash
python scripts/dev/init_eval_samples.py
```

## 类别 ↔ 评估器注册表

| 类别 | 评估器 | 校验方式 |
| --- | --- | --- |
| `search` | `SkillExecutorEvaluator` | 精确匹配（exact/contains/json） |
| `code` | `SkillExecutorEvaluator` | 测试用例（validator，允许表达式） |
| `chat` | `SkillExecutorEvaluator` | 自一致性 + 反馈信号 |
| 其他 | `LlmEvaluator`（分阶段包装） | LLM 判定；无 LLM 时降级为跳过（`degraded`） |

## 样本来源红线（守不易）

- 样本**禁止来自用户真实敏感数据**；必须人工脱敏后录入。
- 若某类别无样本，评估器返回 `status="no_samples"`，**绝不伪造指标**。
