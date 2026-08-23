# ChromaDB 构建问题 · 最终复盘总结

> 项目：云枢 · AI 智能体桌面工作台
> 关联：PR #754 / ci.yml chromadb-preflight
> 日期：2026-08-23
> 状态：✅ 已修复并复验转绿

---

## 1. 事件概述

`chromadb-preflight`（ChromaDB 导入降级预检，容器化）持续失败，阻断单元测试矩阵。症状：`docker build` 报 `"/tests/unit": not found` 与 `"/tests/conftest.py": not found`。

## 2. 根因

仓库根 `.dockerignore`（git 版本）第 13 行 `tests` 将整个 `tests/` 目录排除在 Docker 构建上下文之外，而 Dockerfile 的 `COPY tests/conftest.py`、`COPY tests/unit/` 依赖该目录——**配置自相矛盾**。

排查难点：本地工作区的 `.dockerignore` 是一个**未提交的临时简化版**（17 行 vs git 52 行，恰好不含 tests 排除），导致本地核对"正常"、CI 却失败的假象。

## 3. 排查历程（决策回顾）

| 阶段 | 动作 | 结果 |
|---|---|---|
| 静态核对 | .dockerignore / tests 存在性 / checkout / COPY 路径 | 本地视角全部"正常"（被未提交文件误导） |
| 深度核验 | merge commit 中 tests/ 存在、无整目录删除、无 working-directory 偏移 | 仓库代码层面无问题 |
| **诊断介入** | ci.yml 预检 job 加"构建上下文诊断"步骤（build 前 ls + cat） | **实锤**：runner 上 .dockerignore 含 `tests` 排除 |
| 修复 | `tests` → `tests/unit/temp` | CI 复验 chromadb-preflight **success** |

## 4. 教训

1. **排查 CI 问题必须以 git 版本为准**——CI checkout 的是 git 内容，本地工作区可能存在未提交差异，核对时须 `git show HEAD:文件` 而非仅看工作区
2. **诊断步骤的杠杆价值**——把猜测转化为 runner 实锤只需一个 `ls`/`cat`，成本极低、回报极高；遇到"本地正常 CI 失败"应第一时间加诊断
3. **配置一致性是隐性契约**——.dockerignore 排除集与 Dockerfile COPY 源必须一致，否则问题表现为"神秘失败"且与改动无直观关联

## 5. 预防措施（后续落地建议）

| # | 措施 | 级别 | 说明 |
|---|---|---|---|
| 1 | **.dockerignore↔Dockerfile 一致性检查** | 门禁 | CI 新增一步：解析 .dockerignore 排除集，校验 Dockerfile 所有 COPY 源未被排除，不匹配即失败 |
| 2 | **诊断步骤保留** | 常驻 | 预检 job 保留上下文诊断输出（`ls tests/` + `cat .dockerignore`），未来环境变化可直接定位 |
| 3 | **未提交改动审计** | 流程 | 排查类任务开始前 `git status --short`，确认工作区差异不影响判断 |
| 4 | **.dockerignore 变更评审** | 评审 | 涉及 .dockerignore 的 PR 需确认与 Dockerfile 的 COPY 清单兼容（可纳入 PR 模板检查项） |

## 6. 成果与遗留

- **修复已验证**：最新 CI run，`chromadb-preflight` job **success**，单元测试矩阵解除阻断 ✅
- **遗留基线项**（与本问题无关，独立跟踪）：
  - 单元测试 Shard 2/5：`RuntimeError: can't start new thread`（pytest 资源耗尽，既有问题，并行会话已在 CI 注释中备注）
  - 安全扫描：Gitleaks 全分支基线（M1/M2 已治理命中项，待重扫确认）
  - 文档链接预检 / 代码质量检查：既有失效链接基线
- **后续**：预防措施 #1（一致性门禁）建议排入下个迭代；如需可直接在 ci.yml 落地

## 7. 结语

该问题由"配置矛盾 + 本地未提交文件干扰判断"叠加导致，最终通过诊断步骤一步实锤。修复仅一行（`tests` → `tests/unit/temp`），但排查过程沉淀了流程教训与预防门禁设计，价值大于修复本身。
