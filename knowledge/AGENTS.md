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

## 7. 生命周期状态

卡片状态由 frontmatter `status` 字段表达（`draft` / `current` / `archive` / `unknown`），
不得通过移动文件表达（除 Archive 归档）。合法迁移以 `agent/knowledge/lifecycle.py::TRANSITIONS`
为唯一事实源：`draft → current|unknown`、`current → archive|draft`、`unknown → draft|current`、
`archive` 为终态（拒绝 `draft → archive` 直跳，须先经 `current`）。
