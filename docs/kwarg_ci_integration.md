# 关键字参数冲突扫描 — CI 集成说明

## 概述

本项目使用 `scripts/scan_kwarg_conflicts.py` 静态扫描 `func(explicit_kwarg=x, **dict)` 模式中
dict 含同名键的冲突风险。扫描器已集成到三层防护：

1. **本地 pre-commit hook** — 提交前拦截 HIGH 风险
2. **GitHub Actions CI** — push/PR 时自动扫描并生成报告
3. **变更清单报告** — PR 时自动生成代码审查报告

## 安装

### 方式 1: 使用 pre-commit 框架（推荐）

```bash
pip install pre-commit
pre-commit install
pre-commit install --hook-type pre-push
```

配置文件 `.pre-commit-config.yaml` 已就位：
- `commit` 阶段: HIGH 风险扫描（阻断提交）
- `push` 阶段: MEDIUM 风险扫描（仅提醒）

### 方式 2: 直接安装 git hook

```bash
# Linux/macOS
cp scripts/hooks/pre-commit-kwarg-scan.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit

# Windows (Git Bash)
cp scripts/hooks/pre-commit-kwarg-scan.sh .git/hooks/pre-commit
```

### 方式 3: 手动运行

```bash
# 提交前手动检查
python scripts/scan_kwarg_conflicts.py --path agent/ --min-risk HIGH

# 生成 JSON 报告
python scripts/scan_kwarg_conflicts.py --format json --output report.json

# 生成变更清单报告
python scripts/generate_fix_report.py --output docs/kwarg_fix_report.md
```

## CI 工作流

文件: `.github/workflows/kwarg-conflict-check.yml`

| Job | 触发时机 | 风险等级 | 行为 |
|-----|----------|----------|------|
| `kwarg-high-risk-scan` | push / PR | HIGH | 阻断 CI（exit 1） |
| `kwarg-medium-risk-scan` | push / PR | MEDIUM | 提醒不阻断 |
| `fix-report` | PR only | — | 生成变更清单并评论到 PR |

## 风险等级说明

| 等级 | 条件 | 处理 |
|------|------|------|
| 🔴 HIGH | 同文件函数，显式 kwarg 与函数参数同名，`**variable` 展开 | 必须修复 |
| 🟡 MEDIUM | 外部函数签名已知，`**kwargs` 转发可能冲突 | 建议审查 |
| 🟢 LOW | 已过滤变量（`safe_` 前缀）或字典推导式含条件 | 无需处理 |

## 修复模板

```python
# 1. 定义保留键集合（与显式参数同名）
_RESERVED = {"trace_id", "duration_ms", "level", "action", "module_name"}

# 2. 过滤 **kwargs 中的保留键
safe_kwargs = {k: v for k, v in kwargs.items() if k not in _RESERVED}

# 3. 使用过滤后的变量展开
func(explicit_kwarg=value, **safe_kwargs)
```

扫描器通过变量名 `safe_`/`filtered_`/`clean_` 前缀识别已过滤变量，避免误报。

## 相关文件

| 文件 | 用途 |
|------|------|
| `scripts/scan_kwarg_conflicts.py` | AST 级别静态扫描器 |
| `scripts/generate_fix_report.py` | 变更清单报告生成器 |
| `scripts/hooks/pre-commit-kwarg-scan.sh` | 轻量级 git hook 脚本 |
| `.pre-commit-config.yaml` | pre-commit 框架配置 |
| `.github/workflows/kwarg-conflict-check.yml` | GitHub Actions CI 工作流 |
| `docs/kwarg_fix_report.md` | 最新变更清单报告 |
