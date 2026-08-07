# Release 流程知识库（Wiki）

> 团队内部 Release 发布流程的**导航入口**。本文档汇集发布工具集 v1.0.0 的归档、手册、避坑指南与关联 PR，供新成员一站直达。

---

## 目录

1. [概述](#概述)
2. [核心产物导航](#核心产物导航)
3. [发布流程架构](#发布流程架构)
4. [文档地图](#文档地图)
5. [关联 PR 与变更记录](#关联-pr-与变更记录)

---

## 概述

Release 发布流程已于 v1.0.0 完成工具化沉淀并全量验证（2026-08-07）：

- **三个自动验证闭环**：pip 包 15 项单测、curl 网络失败映射、WinForms GUI 冒烟
- **发布架构**：`guard → auto-release → alert-on-failure` 三 job 工作流
- **工具三件套**：Shell 函数库 + WinForms 引导脚本 + Docker 模拟镜像

## 核心产物导航

| 产物 | 路径 / 链接 | 说明 |
|---|---|---|
| **归档包** | [release_workflow_v1.0.0.zip](../../releases/release_workflow_v1.0.0.zip) | 发布流程工具集 v1.0.0 完整快照（20 文件） |
| 归档说明 | [MANIFEST.txt](../../releases/release_workflow_v1.0.0/MANIFEST.txt) | 内容清单 + 验证方法 |
| **新成员操作手册** | [release_v1.0.0_operations_manual.md](../release_v1.0.0_operations_manual.md) | 归档解压 + GUI 五步实操 |
| **避坑指南** | [release_testing_guide.md](../release_testing_guide.md) | 自动化测试 8 大坑位与解法 |
| 合并 PR | [PR #399](https://github.com/nzt47/security-tools/pull/399) | v1.0.0 归档发布（squash 合并） |

## 发布流程架构

```
push v* tag / workflow_dispatch
        │
        ▼
   [guard]           权限与 tag 判定（子包 tag 放行/拦截）
        │
        ▼
 [auto-release]      update_changelog → GitHub Release（重试 3 次）→ Gitee 同步
        │
        ▼
 [alert-on-failure]  任一 job 失败 → 创建告警 Issue
```

关键机制：
- curl 网络失败映射 HTTP 500 进入重试，不触发 `set -e` 静默中止
- 写操作全部弹确认框（GUI 版），检查项自动执行
- 失败告警走 GitHub Issue（`GITHUB_TOKEN` 零依赖）

## 文档地图

| 文档 | 定位 |
|---|---|
| [发布操作手册](../release_v1.0.0_operations_manual.md) | 新成员实操入口（解压 + GUI） |
| [新手引导](../release_quickstart.md) | 首次发布五步走 |
| [检查清单](../release_checklist.md) | 发布前/中/后勾选式检查 |
| [排障手册](../release_workflow_manual.md) | 全链路故障排查总纲 |
| [工作流模板](../release_workflow_template.md) | 可复用工作流模板 |
| [避坑指南](../release_testing_guide.md) | 自动化测试坑位沉淀 |
| [复盘文章](../release_workflow_retrospective.md) | 团队分享技术文章 |
| [总结](../release_workflow_summary.md) | 演进历程 + 18 项优化点 |

## 关联 PR 与变更记录

| 变更 | PR / 提交 | 说明 |
|---|---|---|
| v1.0.0 归档包 + 避坑指南 | [PR #399](https://github.com/nzt47/security-tools/pull/399)（111062f2） | 归档 zip / 避坑指南 / MANIFEST 入主分支 |
| WinForms 修复 | e42b1ab0 | UIA Name + Point 参数拆包 |
| pip 包 + GUI 版 | 5449eb68 | release-shell-lib 0.1.0 + GUI 脚本 |
| 发布工作流 | 84800618 等 | guard/auto-release/alert-on-failure 演进 |

---

> 维护约定：产物或流程有变更时，同步更新本文档的「核心产物导航」与「关联 PR 记录」两张表。
