# GitHub Actions 运行监控指南

## 文档概述

本文档详细说明如何监控 GitHub Actions CI 工作流的运行状态，包括如何查看日志、排查失败和分析测试结果。

---

## 一、访问 GitHub Actions

### 1.1 导航到 Actions 页面

**方法1**: 通过仓库主页访问
```
1. 打开 GitHub 仓库主页
2. 点击顶部导航栏的 "Actions" 标签
```

**方法2**: 直接访问 URL
```
https://github.com/<your-username>/<your-repo>/actions
```

### 1.2 工作流列表说明

| 图标 | 状态 | 说明 |
|------|------|------|
| ✅ | 通过 | 所有任务都成功完成 |
| ❌ | 失败 | 至少有一个任务失败 |
| ⏳ | 运行中 | 工作流正在执行 |
| ⚠️ | 警告 | 部分任务成功，部分有警告 |
| 🚫 | 取消 | 工作流被手动取消 |

---

## 二、查看工作流运行详情

### 2.1 工作流运行页面

点击工作流名称 "云枢系统测试流程" 进入运行详情页面，你将看到：

1. **工作流信息**
   - 运行编号和状态
   - 触发方式（push/pull_request/schedule）
   - 提交信息和分支
   - 运行时间

2. **任务矩阵**
   - 所有测试组合列表
   - 每个组合的状态图标
   - 运行时间

### 2.2 预期的 40 个测试组合

**Ubuntu 平台**:
- 单元测试 (Python 3.8 - ubuntu-latest)
- 单元测试 (Python 3.9 - ubuntu-latest)
- 单元测试 (Python 3.10 - ubuntu-latest)
- 单元测试 (Python 3.11 - ubuntu-latest)
- 单元测试 (Python 3.12 - ubuntu-latest)
- 集成测试 (Python 3.8 - ubuntu-latest)
- 集成测试 (Python 3.9 - ubuntu-latest)
- 集成测试 (Python 3.10 - ubuntu-latest)
- 集成测试 (Python 3.11 - ubuntu-latest)
- 集成测试 (Python 3.12 - ubuntu-latest)
- 性能测试 (Python 3.8 - ubuntu-latest)
- 性能测试 (Python 3.9 - ubuntu-latest)
- 性能测试 (Python 3.10 - ubuntu-latest)
- 性能测试 (Python 3.11 - ubuntu-latest)
- 性能测试 (Python 3.12 - ubuntu-latest)
- 覆盖率检查 (Python 3.8 - ubuntu-latest)
- 覆盖率检查 (Python 3.9 - ubuntu-latest)
- 覆盖率检查 (Python 3.10 - ubuntu-latest)
- 覆盖率检查 (Python 3.11 - ubuntu-latest)
- 覆盖率检查 (Python 3.12 - ubuntu-latest)

**Windows 平台**:
- 单元测试 (Python 3.8 - windows-latest)
- 单元测试 (Python 3.9 - windows-latest)
- 单元测试 (Python 3.10 - windows-latest)
- 单元测试 (Python 3.11 - windows-latest)
- 单元测试 (Python 3.12 - windows-latest)
- 集成测试 (Python 3.8 - windows-latest)
- 集成测试 (Python 3.9 - windows-latest)
- 集成测试 (Python 3.10 - windows-latest)
- 集成测试 (Python 3.11 - windows-latest)
- 集成测试 (Python 3.12 - windows-latest)
- 性能测试 (Python 3.8 - windows-latest)
- 性能测试 (Python 3.9 - windows-latest)
- 性能测试 (Python 3.10 - windows-latest)
- 性能测试 (Python 3.11 - windows-latest)
- 性能测试 (Python 3.12 - windows-latest)
- 覆盖率检查 (Python 3.8 - windows-latest)
- 覆盖率检查 (Python 3.9 - windows-latest)
- 覆盖率检查 (Python 3.10 - windows-latest)
- 覆盖率检查 (Python 3.11 - windows-latest)
- 覆盖率检查 (Python 3.12 - windows-latest)

---

## 三、查看任务日志

### 3.1 打开任务日志

1. 在工作流运行页面，找到要查看的任务
2. 点击任务名称进入任务详情
3. 在左侧步骤列表中，点击具体步骤查看日志

### 3.2 日志结构说明

```
├── 检出代码 (actions/checkout@v4)
├── 设置Python环境 (actions/setup-python@v5)
├── 安装依赖 (Ubuntu/Windows)
├── 创建测试目录
├── 运行单元测试/集成测试/性能测试
└── 上传报告/结果
```

### 3.3 关键日志信息

| 步骤 | 关注内容 |
|------|---------|
| 设置Python环境 | Python版本是否正确 |
| 安装依赖 | 是否有依赖安装失败 |
| 运行测试 | 测试结果、失败信息 |
| 覆盖率检查 | 覆盖率百分比、是否达标 |

---

## 四、排查失败任务

### 4.1 常见失败原因

**1. 依赖安装失败**
```
错误特征:
- pip install 命令返回非零退出码
- 显示 "ERROR: Could not find a version that satisfies the requirement"

解决方案:
- 检查 pyproject.toml 中的依赖版本约束
- 确认 Python 版本与依赖版本兼容
- 对于 Python 3.8，可能需要降低某些依赖版本
```

**2. 测试失败**
```
错误特征:
- pytest 返回非零退出码
- 显示 "FAILED" 或 "Error"

解决方案:
- 查看具体的测试失败信息
- 检查是否有平台特定的代码问题
- 确认测试用例在该 Python 版本下是否兼容
```

**3. 平台特定问题**
```
Windows 特有问题:
- 文件路径分隔符差异
- 权限问题
- 缺少某些系统依赖

Ubuntu 特有问题:
- 缺少系统库
- 环境变量差异
```

**4. 超时**
```
错误特征:
- 显示 "The job has timed out"
- 运行时间超过 6 小时

解决方案:
- 优化测试用例执行时间
- 考虑拆分长时间运行的测试
```

### 4.2 失败排查流程

```
1. 点击失败的任务进入详情页
2. 查看步骤列表，找到标红的失败步骤
3. 展开失败步骤的日志
4. 定位错误信息（通常在日志末尾）
5. 分析错误原因：
   - 是否为平台特定问题？
   - 是否为 Python 版本兼容性问题？
   - 是否为依赖版本问题？
6. 在本地复现问题
7. 修复代码或配置
8. 提交修复并重新运行 CI
```

### 4.3 常用日志搜索关键词

| 关键词 | 用途 |
|--------|------|
| `ERROR` | 查找错误信息 |
| `FAILED` | 查找失败的测试 |
| `Traceback` | 查找异常堆栈 |
| `PermissionError` | 查找权限问题 |
| `ImportError` | 查找导入问题 |
| `TimeoutError` | 查找超时问题 |
| `Could not find` | 查找依赖安装问题 |

---

## 五、分析测试结果

### 5.1 覆盖率报告

**查看覆盖率**:
1. 在任务完成后，点击 "Summary" 标签
2. 找到上传的覆盖率报告 artifact
3. 下载并打开 `htmlcov/index.html`

**覆盖率指标**:
- **语句覆盖率**: 执行的代码行数占总代码行数的百分比
- **分支覆盖率**: 执行的代码分支占总分支数的百分比
- **函数覆盖率**: 执行的函数占总函数数的百分比

**阈值要求**:
- 项目最低覆盖率要求: 70%
- 低于阈值会导致覆盖率检查任务失败

### 5.2 性能测试结果

**查看性能报告**:
1. 在性能测试任务完成后，下载 `benchmark-report` artifact
2. 分析 `benchmark.json` 文件

**性能指标**:
- 平均执行时间
- 最小/最大执行时间
- 标准差
- 与基准的差异

### 5.3 测试结果对比

| Python版本 | 预期结果 | 关注重点 |
|-----------|---------|---------|
| 3.8 | 通过 | 旧版本兼容性 |
| 3.9 | 通过 | 过渡版本 |
| 3.10 | 通过 | 推荐版本 |
| 3.11 | 通过 | 新版本特性 |
| 3.12 | 通过 | 最新版本兼容性 |

---

## 六、监控最佳实践

### 6.1 设置通知

**开启 GitHub 通知**:
```
1. 进入仓库 Settings
2. 点击 "Notifications"
3. 开启 "Workflow runs" 通知
4. 选择通知方式（邮件/网页/短信）
```

**设置 Slack 通知**（可选）:
- 使用 GitHub Actions Slack 集成
- 在工作流完成时发送通知到指定频道

### 6.2 创建状态徽章

在 README.md 中添加 CI 状态徽章:
```markdown
[![云枢系统测试流程](https://github.com/<your-username>/<your-repo>/actions/workflows/test.yml/badge.svg)](https://github.com/<your-username>/<your-repo>/actions/workflows/test.yml)
```

### 6.3 定期检查

| 检查项 | 频率 | 说明 |
|--------|------|------|
| CI 运行状态 | 每次推送 | 确保所有测试通过 |
| 覆盖率趋势 | 每周 | 监控覆盖率变化 |
| 运行时间 | 每周 | 发现性能下降 |
| 失败历史 | 每月 | 分析常见失败原因 |

---

## 七、故障排除案例

### 案例1: Python 3.8 依赖安装失败

**问题**:
```
ERROR: Could not find a version that satisfies the requirement some-package>=2.0 (from versions: 1.0, 1.1)
```

**解决方案**:
在 `pyproject.toml` 中添加版本约束:
```toml
[tool.poetry.dependencies]
some-package = [
    { version = ">=2.0", python = ">=3.9" },
    { version = ">=1.0,<2.0", python = "<3.9" }
]
```

### 案例2: Windows 路径问题

**问题**:
```
FileNotFoundError: [WinError 3] 系统找不到指定的路径: '/home/user/project/file.txt'
```

**解决方案**:
使用 `os.path` 模块处理路径:
```python
import os
file_path = os.path.join(os.path.dirname(__file__), 'file.txt')
```

### 案例3: 覆盖率不达标

**问题**:
```
FAILED coverage: 68% < 70%
```

**解决方案**:
1. 查看覆盖率报告，找出未覆盖的代码
2. 添加单元测试覆盖这些代码
3. 或调整覆盖率阈值

---

## 八、总结

### 监控流程

```
1. 推送代码到 GitHub
2. 进入 Actions 页面查看工作流运行
3. 等待所有 40 个测试组合完成
4. 检查是否有失败任务
5. 如果失败，查看日志定位问题
6. 修复问题并重新提交
7. 验证所有测试通过
```

### 关键检查点

| 检查项 | 预期结果 |
|--------|---------|
| 总任务数 | 40 个 |
| 成功任务 | 40 个 |
| 失败任务 | 0 个 |
| 覆盖率 | >= 70% |
| 运行时间 | < 30 分钟 |

---

**文档版本**: v1.0  
**生成时间**: 2026-06-03  
**适用工作流**: `test.yml`