# ChromaDB 构建失败排查报告

> 项目：云枢 · AI 智能体桌面工作台
> 目标读者：CI 基础设施团队 / 评审
> 日期：2026-08-23
> 状态：根因已定位 ✅，修复已提交，待 CI 复验

---

## 1. 现象

`chromadb-preflight` job（ci.yml）的「构建预检镜像」步骤持续失败：

```
#9 ERROR: failed to calculate checksum ... "/tests/unit": not found
#10 ERROR: ... "/tests/conftest.py": not found
ERROR: failed to build: ... "/tests/unit": not found
```

该 job 为单元测试矩阵的前置预检（`needs` 依赖），失败即阻断后续矩阵。

## 2. 排查过程与排除项

| # | 假设 | 结论 | 依据 |
|---|---|---|---|
| 1 | `.dockerignore` 忽略 tests | ❌ 本地工作区文件无 tests 排除 | 本地文件核对 |
| 2 | 分支/merge commit 中 tests/ 被误删 | ❌ tests/ 完整存在 | `git ls-tree` merge commit 775a9cce：`tests/unit`、`tests/conftest.py` IN-MERGE |
| 3 | checkout 特殊参数（sparse 等） | ❌ 标准 `actions/checkout@v6` | ci.yml 核对 |
| 4 | 全局 `working-directory` 偏移 | ❌ 无 `defaults`/`working-directory` 配置 | ci.yml 核对 |
| 5 | Dockerfile COPY 上下文路径错误 | ❌ 路径相对仓库根，与仓库结构一致 | Dockerfile 逐行核对 |

> ⚠️ 期间发现本地 `.dockerignore` 为**未提交的临时简化版**（17 行 vs git 52 行），导致假设 1 误判——本地检查的文件状态 ≠ CI 使用的 git 版本。

## 3. 根因（诊断步骤实锤）

在 ci.yml 预检 job 新增**构建上下文诊断步骤**（build 前打印 workspace / tests / git 跟踪 / .dockerignore），runner 实际输出：

```
=== workspace 根目录 ===
...（.dockerignore、Dockerfile 等正常）
=== tests/ 目录 ===
...（tests/ 完整：unit、conftest.py 等均在）
=== .dockerignore ===
# 预检镜像构建上下文排除...
# 测试代码不进镜像（docker-compose 运行时挂载 ./tests）
tests          ← 根因！git 版本 .dockerignore 排除了 tests
```

**根因**：仓库根 `.dockerignore`（git 版本）第 13 行 `tests` 将整个 `tests/` 目录排除在 Docker 构建上下文之外，而 [Dockerfile](file:///c:/Users/Administrator/agent/Dockerfile#L22-L23) 的 `COPY tests/conftest.py`、`COPY tests/unit/` 依赖该目录——**配置自相矛盾**，导致构建上下文无 tests，COPY 源缺失。

## 4. 修复

[.dockerignore](file:///c:/Users/Administrator/agent/.dockerignore#L12-L13)：

```
# 修改前
# 测试代码不进镜像（docker-compose 运行时挂载 ./tests）
tests
# 修改后
# 预检镜像需 COPY tests/（pytest 用例），仅排除大体积运行时产物 tests/unit/temp
tests/unit/temp
```

- **移除** `tests` 整目录排除（Dockerfile 需要 COPY conftest.py 与 unit 测试）
- **保留** `tests/unit/temp` 排除（876 个运行时产物目录，保持镜像轻量）

## 5. 验证与后续

| 项 | 状态 |
|---|---|
| 根因定位 | ✅ 诊断步骤输出实锤 |
| 修复提交 | ✅ 已推送 PR #754 |
| CI 复验 | ⏳ 待下一轮 run 确认 chromadb-preflight 转绿 |
| 遗留 | 本地 `.dockerignore` 临时简化版已恢复为 git 版本（丢弃未提交改动），后续如有本地构建需求请在 git 版本上调整 |

## 6. 经验教训

1. **排查 CI 问题须以 git 版本为准**（checkout 内容），本地工作区可能存在未提交差异导致误判
2. **诊断步骤价值**：构建前打印上下文关键路径（tests/ 与 .dockerignore），将猜测转化为实锤
3. .dockerignore 与 Dockerfile COPY 之间的一致性应纳入变更检查（如 PR 评论/CI 门禁）
