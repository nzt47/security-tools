# 待审核草稿目录（data/evals/_pending/）

本目录存放**扩充管道产出的 DRAFT 评估样本**（`agent/skills_mgmt/eval_sample_ingest.py`）。

## 目录结构

```
data/evals/_pending/
└── <category>/           # 与正式类别同名（search/code/chat/tool/planning）
    └── <draft_id>.json   # DRAFT 样本
```

## 草稿格式

每条草稿为 JSON，含：

| 字段 | 说明 |
| --- | --- |
| `draft_status` | 固定 `"DRAFT"`（未审核前绝不入评估集） |
| `id` | 草稿 ID（`draft-<uuid8>`，与正式样本 id 不冲突） |
| `category` | 目标类别 |
| `task` / `expected_output` / `created_at` / `metadata` | 与正式样本同构；`metadata.source` 标记素材来源（reflection/feedback/novelty/manual） |
| `metadata.input_hash` | 去重哈希（与正式样本同算法） |
| `review` | 审核结果（`{"status": "pending"|"passed"|"rejected", "findings": [...]}`） |

## 红线（不易）

- DRAFT 态**零副作用**：管道只写 `_pending/`，绝不直接写入正式类别 JSON。
- 审核通过（`review.status == passed`，复用 `reviewer.SecurityScanner`）后才可
  `approve_draft()` 入正式类别；`EvalSamplePool` 只扫描各类别目录，**不读取本目录**。
- 涉及真实交互轨迹的素材（reflection/feedback/novelty）入库前必须走
  `agent.security_utils.DataSanitizer` 脱敏管道（`EVAL_SAMPLE_INGEST_ENABLED=true` 时强制）。
