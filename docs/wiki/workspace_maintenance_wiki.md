# 工作区维护规范 Wiki

> 最后更新：2026-08-13
> 维护者：团队共享
> 关联提交：`e4924848`（阶段5 完成确认）、`5724e191`（TD-4 闭环）、`50fe6d6c`（复盘归档）

---

## 概述

本页记录工作区 untracked/未提交产物的**清理判定规则**与 **2026-08-13 清理结论**，
防止误删并行会话产出与无跟踪运行时数据（本项目已有 `data/reflection` 误删事故先例）。

## 一、清理判定规则（硬约束）

| 规则 | 说明 |
|---|---|
| R1 清理前必核对 | 任何 untracked 文件/目录删除前，先 `git ls-files <path>` 确认无 HEAD 跟踪（避免过滤词不匹配误判） |
| R2 并行会话产物禁动 | `git worktree list` 存在多 worktree 时，并行会话的 M/?? 文件保持本地状态，除非用户明确确认清理 |
| R3 运行时数据严禁删 | `data/reflection/`、`data/sandbox/` 等 untracked 运行时目录**禁止删除**——无跟踪、无 `.gitignore` 保护、无备份 |
| R4 禁 `git clean` | `git clean -fd` 会删除全部 untracked（含并行会话产出与运行时数据），**本项目一律禁用**，只能精确删除确认过的文件 |
| R5 删除前备份 | 确需删除的 untracked 目录，先整体备份（移动/压缩）再操作，操作后核实目标状态（`Test-Path`/`Get-ChildItem`） |

## 二、2026-08-13 清理结论（未删除原因）

### 背景
阶段5 记账修复 + TD-4 闭环后检查工作区：untracked/modified 约 60 项。本会话产物已全部提交，
剩余均为并行会话产物或运行时数据，**结论：不执行清理**。

### 分类与原因

| 类别 | 数量 | 未删除原因 | 后续处置 |
|---|---|---|---|
| 并行会话产物（进化机制重构/自我修复/合约/P0 文档等） | ~50 | R2：并行会话仍活跃，产物为任务线交付物（`lineage.py`、`evolution-ci.yml`、`parent_selection.py`、`进化机制重构计划/*`、`tests/contract/*` 等） | 并行会话结束后由对应会话提交归档 |
| 运行时数据（`data/reflection/`、`data/sandbox/`、`data/health/`、`data/knowledge/reports/`、`data/lifetrace/topics/`） | ~10 | R3：无跟踪无备份，删除不可恢复（`reflection/`、`sandbox/` 已用 `git ls-files` + `git check-ignore` 双向核实） | 永久保留 |
| 本会话评测输出 `data/eval_planning_stage5_final_20260813.json` | 1 | 灰度放量前对照证据，非临时文件 | 保留至 T-1/T-2 完成后归档 |

### 判定依据（2026-08-13 实测）
- `git ls-files data/reflection` → 空；`git ls-files data/sandbox` → 空
- `git check-ignore data/reflection data/sandbox` → 无匹配（不在 `.gitignore`）
- `.gitignore` data 规则仅覆盖 state/reports/audit/failures/auto_tuning/blackbox 等，**不含 reflection/sandbox**
- 两目录递归 5 个文件逐一核对均 untracked

## 三、交接指引

并行会话产物归档清单见 `docs/zh/并行会话产物归档清单_20260813.md`（按任务线分类、
标注状态与建议处置），供后续会话/成员交接使用。
