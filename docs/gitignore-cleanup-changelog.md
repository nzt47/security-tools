# Git 清理工作变更日志

**日期**：2026-07-14
**范围**：.gitignore 规则补全与构建产物清理

---

## 概述

本次清理工作修复了 .gitignore 中缺失的忽略规则，将 Git 仓库中未跟踪文件从 **160+ 个** 降至 **6 个**（全部为源代码文件），消除了所有构建产物、运行时数据和临时文件对版本控制的干扰。

---

## 修复的问题

### Python 虚拟环境规则缺失（核心问题）

**原因**：`.gitignore` 第 6 行只有 `.venv/`（带点前缀），但项目实际使用 `venv/`（不带点），导致整个 Python 虚拟环境目录（含数百个依赖包文件）被 Git 跟踪为未跟踪文件。

**修复**：在 `.venv/` 下方添加 `venv/` 规则。

---

## 新增忽略规则清单

### 1. Python 虚拟环境
| 规则 | 行号 | 说明 |
|------|------|------|
| `venv/` | 7 | Python 虚拟环境目录（修复缺失） |

### 2. 运行时数据
| 规则 | 行号 | 说明 |
|------|------|------|
| `data/memory/` | 84 | 内存数据库文件（long_term.db, memory_vec.db 等） |
| `data/backups/` | 85 | 运行时备份目录 |
| `data/sessions/` | 86 | 会话数据目录 |
| `data/assets/` | 87 | 资产数据目录 |
| `data/permission_policies.json` | 88 | 权限策略运行时数据 |
| `data/test_workflows.json` | 89 | 测试工作流运行时数据 |

### 3. 测试产物
| 规则 | 行号 | 说明 |
|------|------|------|
| `coverage.json` | 95 | 根目录覆盖率报告 |
| `yunshu-ui/coverage/` | 98 | 前端测试覆盖率目录 |
| `yunshu-ui/coverage_output.txt` | 99 | 前端覆盖率输出文本 |

### 4. 前端构建产物
| 规则 | 行号 | 说明 |
|------|------|------|
| `static/assets/` | 114 | Vite 构建产物（JS/CSS/SourceMap） |
| `yunshu-ui/dist/` | 115 | 前端 dist 目录 |

### 5. 部署包
| 规则 | 行号 | 说明 |
|------|------|------|
| `deploy_package/` | 118 | 部署打包目录 |
| `linux_deploy_package.zip` | 119 | Linux 部署压缩包 |

---

## 清理效果

### 修改前
- 未跟踪文件：**160+ 个**
- 包含：venv/ 下数百个 Python 依赖包、static/assets/ 下数十个构建产物、data/memory/ 下数据库文件、coverage 报告、部署包等

### 修改后
- 未跟踪文件：**6 个**（全部为源代码）
- 已忽略的非代码文件类型：
  - Python 虚拟环境（venv/）
  - 前端构建产物（static/assets/, yunshu-ui/dist/）
  - 数据库文件（data/memory/*.db）
  - 测试覆盖率（coverage.json, yunshu-ui/coverage/）
  - 部署包（deploy_package/, linux_deploy_package.zip）
  - 运行时数据（data/backups/, data/sessions/, data/assets/）

---

## 影响范围

- **不影响**：已有的源代码跟踪、已提交的文件
- **不影响**：开发流程（venv/ 仍在本地使用，只是不提交到仓库）
- **改善**：`git status` 输出从 160+ 行降至 6 行，大幅提升可读性
- **改善**：防止构建产物和数据库文件被误提交
