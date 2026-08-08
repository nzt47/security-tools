# 知识模块优化 · Pending 待办清单（外部凭据阻塞项）

> 结案日期：2026-08-08 ｜ 状态：**主线已结案，以下 2 项待外部凭据就绪后恢复**
> 关联：知识模块性能优化第二批（develop/release/v1.2.0 均已同步 origin）

---

## 概览

| # | 待办项 | 阻塞原因 | 凭据需求 | 恢复优先级 |
|---|---|---|---|---|
| 1 | Jira 任务创建（KN-101~105） | 无 Jira API 凭据 | `JIRA_BASE_URL` / `JIRA_USER` / `JIRA_TOKEN` | P3（不影响主线） |
| 2 | Confluence 文档推送 | 无 Confluence 凭据 | `CONFLUENCE_BASE_URL` / `CONFLUENCE_USER` / `CONFLUENCE_TOKEN` | P3（备选已覆盖） |

---

## 1. Jira 任务创建（KN-101 ~ KN-105）

**背景**：按 Release Notes 后续计划创建 5 个任务，脚本已就绪。

| 任务 | 标题 | 优先级 |
|---|---|---|
| KN-101 | 流式扫描落地（iter_cards） | High |
| KN-102 | 容量监控埋点落地 | High |
| KN-103 | 分片架构前置设计 | Medium |
| KN-104 | 存储层二级目录/对象存储评估 | Low |
| KN-105 | Confluence 推送（缺凭据） | Low |

**恢复步骤**：
1. `.env` 追加：`JIRA_BASE_URL` / `JIRA_USER` / `JIRA_TOKEN`（可选 `JIRA_PROJECT`，默认 `KN`）
2. `python -B scripts/create_jira_tasks.py --dry-run` —— 预览 5 任务（不发请求）
3. `python -B scripts/create_jira_tasks.py` —— 实际创建（**幂等**：summary 匹配到已存在任务自动跳过，可重复运行）
4. 核对输出 `[created] KN-101 → <Jira key>` 逐条

**失败排查**：[docs/troubleshooting/jira_task_creation_diagnosis_20260808.md](file:///c:/Users/Administrator/agent/docs/troubleshooting/jira_task_creation_diagnosis_20260808.md)（已实测 fail-fast 正确拦截：缺凭据 exit 1、无半创建；含 8 种潜在失败模式表）。

## 2. Confluence 文档推送

**背景**：将《知识模块性能优化第二批对比报告》和《知识模块性能优化第二批变更说明》推送团队 Confluence。

**恢复步骤**：
1. `.env` 配置三个凭据
2. 先查询 Space 列表确认 Key（`sync_to_confluence.py` 内建查询）
3. 运行 `scripts/sync_to_confluence.py` 创建两个页面
4. 页面链接回填文档索引

**备选（已落地）**：团队知识库实际约定为 `docs/wiki/` 目录（GitHub Wiki 仓库不存在），两份文档已有等价内容：
- `docs/wiki/knowledge_optimization_phase2_evolution_wiki.md`（架构演进总结终版）
- 容量评估系列 11 份报告 + 复盘 + 邮件 + PDF 归档均已入库

---

## 恢复后操作检查表

- [ ] `.env` 已配置 Jira 三凭据
- [ ] `create_jira_tasks.py --dry-run` 输出 5 任务预览正确
- [ ] 实际创建成功，Jira UI 可见 KN-101~105
- [ ] `.env` 已配置 Confluence 三凭据
- [ ] Confluence Space 查询确认 Key
- [ ] 两个页面创建成功并回填链接

## 备注

- 两条待办均已记入项目记忆（`project_memory.md` ⚠️ 待办区），跨会话可恢复；
- 凭据属敏感信息，一律只放 `.env`（已 gitignore），勿入提交；
- 除以上 2 项外，本轮知识模块优化**全部交付物已归档**（代码/测试/文档/脚本），无需其他处理。
