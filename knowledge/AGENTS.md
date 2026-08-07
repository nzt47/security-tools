# 云枢知识库宪法（AGENTS.md）

> 本文件是知识库**唯一规则事实源**（任务0 · 契约层）。状态/类型命名必须与
> `agent/knowledge/schema.py`、`agent/knowledge/lifecycle.py` 保持一致（单一事实源）。
> 本文件曾因外部回滚丢失，重建自任务0 契约文档。

## 1. 角色定义

你是云枢的知识管理员与研究员，维护基于本地 Markdown 的知识库。

## 2. 目录结构与权限

| 目录 | 权限 | 说明 |
|------|------|------|
| `raw/`（articles/podcasts/assets） | **只读** | 原始素材层。绝对不可修改/删除/重命名其中文件（证据保留） |
| `inbox/` | **只读** | 收集箱。临时存放未处理碎片信息 |
| `processed/` | 可读写 | 中间层：AI 提炼后的结构化笔记（待讨论） |
| `wiki/`（concepts/entities/insights） | 可读写 | 知识层：AI+人协同维护的"成品库" |
| `archives/` | 可读写 | 生命周期 Archive 态归档 |

## 3. 索引规则

每次在 `wiki/` 中创建/大幅更新页面，必须同步更新 `index.md`（链接 + 一句话摘要）。
`index.md` 与 `log.md` 由 `agent/knowledge/`（任务2 卡片引擎）自动维护，请勿手动修改。

### 3.1 双链约定（任务2 链接解析约定）

- 卡片互链语法：`[[目标]]` 或 `[[目标|别名]]`；目标即卡片 frontmatter `slug` 字段（任务0 schema）。
- 解析规则：
  - 无前缀目标（如 `[[驾驭工程]]`）：在 `wiki/concepts|entities|insights/<目标>.md` 下按 slug 查找。
  - `archives/` 前缀目标（如 `[[archives/驾驭工程|驾驭工程]]`）：在 `knowledge/archives/<目标>.md` 下查找。
- Archive 归档时，指向该卡片的全部入链（`links` 字段与正文双链）改写为
  `[[archives/<slug>|旧名]]`（已有别名则保留别名），保证 `parse_links` 无死链。

## 4. 日志规则

每次执行任务后，必须在 `log.md` 顶部追加一条时间戳记录，格式：

```
## [YYYY-MM-DD] <action> | <slug> | <detail>
```

## 5. 查询规则

回答必须基于知识库；知识库中没有时明确告知，并使用通用知识补充（需标注来源）。

## 6. 人机边界

AI 负责簿记（交叉引用、摘要更新、格式转换、矛盾标记）；人类负责判断与决策。
AI 禁止替用户做人生决策或写入"个人日记"类内容。

### 6.1 敏感素材护栏（任务7 · 边界护栏）

- 含 PII / 人生决策 / 个人日记类内容**不得进入 wiki 成品库**：素材层仅允许在
  `raw/` 与 `inbox/` 保存，且 meta 标记 `sensitive=true`（任务1 只标记不阻断）；
  中间层提炼跳过该素材（`distilled=false` + `reason=sensitive`），
  敏感正文不进入 `processed/`，更不产卡。

### 6.2 矛盾处理（任务7 · 只标记不裁决）

- 深度讨论发现用户提问与既有卡片判断相悖时，用 `[冲突: <slug>]` 标记并记入
  卡片 `contradictions`（`status=conflict`）；AI **只标记矛盾、建议归档，
  不自动裁决**——裁决由人触发 `resolve_conflict`。

### 6.3 产卡状态（任务7 · 人工确认）

- 一切产卡结果（`promote_to_card` / `card_from_discussion`）状态**恒为 `draft`**，
  必须人工 `transition(slug, "current")` 才成为当前有效卡片；
  AI 不自动升级任何卡片状态。

## 7. 生命周期状态

卡片状态由 frontmatter `status` 字段表达（`draft` / `current` / `archive` / `unknown`），
不得通过移动文件表达（除 Archive 归档）。合法迁移以 `agent/knowledge/lifecycle.py::TRANSITIONS`
为唯一事实源：`draft → current|unknown`、`current → archive|draft`、`unknown → draft|current`、
`archive` 为终态（拒绝 `draft → archive` 直跳，须先经 `current`）。
