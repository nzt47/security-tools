# GitHub 远程仓库配置指南

## 文档概述

本指南详细说明如何配置 GitHub 远程仓库并推送代码。

---

## 一、获取 GitHub 仓库 URL

### 1.1 在 GitHub 上创建仓库

1. 登录 GitHub 账号
2. 点击右上角 "+" 号，选择 "New repository"
3. 填写仓库信息：
   - Repository name: `yunshu-agent`（或其他名称）
   - Description: 云枢系统代理服务
   - Visibility: 选择 Public 或 Private
   - 点击 "Create repository"

### 1.2 获取仓库 URL

创建仓库后，在仓库页面点击 "Code" 按钮：

**HTTPS 方式**（推荐）：
```
https://github.com/<your-username>/<your-repo>.git
```

**SSH 方式**（需要配置 SSH key）：
```
git@github.com:<your-username>/<your-repo>.git
```

### 1.3 示例

假设用户名为 `yunshu-system`，仓库名为 `yunshu-agent`：
- HTTPS: `https://github.com/yunshu-system/yunshu-agent.git`
- SSH: `git@github.com:yunshu-system/yunshu-agent.git`

---

## 二、配置本地仓库

### 2.1 初始化 Git 仓库（首次）

```bash
# 初始化仓库
git init

# 配置用户信息
git config user.name "Your Name"
git config user.email "your.email@example.com"

# 可选：配置全局用户信息（仅首次）
git config --global user.name "Your Name"
git config --global user.email "your.email@example.com"
```

### 2.2 添加远程仓库

```bash
# 添加远程仓库
git remote add origin https://github.com/<your-username>/<your-repo>.git

# 验证配置
git remote -v
```

### 2.3 更新远程仓库 URL

```bash
# 查看当前远程仓库配置
git remote -v

# 更新 URL
git remote set-url origin https://github.com/<your-username>/<your-repo>.git
```

### 2.4 删除远程仓库

```bash
git remote remove origin
```

---

## 三、推送代码流程

### 3.1 完整推送流程

```bash
# 1. 查看状态
git status

# 2. 添加文件到暂存区
git add .

# 3. 提交更改
git commit -m "feat: 完成兼容性处理任务"

# 4. 推送代码
git push -u origin dev

# 后续推送（已设置上游）
git push
```

### 3.2 常见问题处理

**问题1: 远程仓库不存在**
```
fatal: 'origin' does not appear to be a git repository
```
解决：确认远程仓库 URL 正确，仓库已在 GitHub 创建

**问题2: 权限不足**
```
remote: Permission to <repo> denied
```
解决：使用 HTTPS URL，推送时输入用户名和 Personal Access Token

**问题3: 分支不存在于远程**
```
error: src refspec dev does not match any
```
解决：确保本地有 dev 分支，或使用 `-u origin HEAD:dev`

**问题4: 历史不匹配**
```
error: failed to push some refs to '...'
```
解决：先拉取远程分支，解决冲突后再推送
```bash
git pull origin dev --allow-unrelated-histories
```

---

## 四、GitHub Actions 触发流程

### 4.1 触发条件

当代码推送到 GitHub 后，`.github/workflows/test.yml` 中定义的工作流会自动触发：

```yaml
on:
  push:
    branches: [ main, dev ]
  pull_request:
    branches: [ main ]
```

### 4.2 完整流程

```
1. 推送代码到 GitHub
       ↓
2. GitHub 检测到 push 事件
       ↓
3. 触发工作流 "云枢系统测试流程"
       ↓
4. 初始化 Runner（Ubuntu/Windows）
       ↓
5. 检出代码
       ↓
6. 设置 Python 环境（3.8/3.9/3.10/3.11/3.12）
       ↓
7. 安装依赖
       ↓
8. 运行测试（单元/集成/性能/覆盖率）
       ↓
9. 生成报告
       ↓
10. 通知结果
```

### 4.3 测试矩阵展开

**40 个测试组合**：

```
平台: Ubuntu + Windows (2个)
Python版本: 3.8, 3.9, 3.10, 3.11, 3.12 (5个)
测试类型: 单元测试, 集成测试, 性能测试, 覆盖率检查 (4个)

总组合数 = 2 × 5 × 4 = 40
```

### 4.4 并行执行策略

```
┌─────────────────────────────────────────────────────────────────┐
│                     GitHub Actions                             │
├─────────────────────────────────────────────────────────────────┤
│  Ubuntu Runner 1    Ubuntu Runner 2    Windows Runner 1        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Python 3.8   │  │ Python 3.9   │  │ Python 3.8   │        │
│  │ 单元测试     │  │ 单元测试     │  │ 单元测试     │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Python 3.10  │  │ Python 3.11  │  │ Python 3.9   │        │
│  │ 集成测试     │  │ 集成测试     │  │ 集成测试     │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ Python 3.12  │  │              │  │ Python 3.10  │        │
│  │ 性能测试     │  │              │  │ 性能测试     │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│  ┌──────────────┐                    ┌──────────────┐        │
│  │ 覆盖率检查   │                    │ Python 3.11  │        │
│  │ Python 3.8   │                    │ 覆盖率检查   │        │
│  └──────────────┘                    └──────────────┘        │
└─────────────────────────────────────────────────────────────────┘
```

---

## 五、预期结果

### 5.1 成功场景

```
✅ 所有 40 个测试组合通过
✅ 覆盖率 >= 70%
✅ 运行时间 < 15 分钟
✅ 通知发送成功
```

### 5.2 失败场景处理

| 失败类型 | 处理策略 |
|---------|---------|
| 依赖安装失败 | 检查 pyproject.toml 版本约束 |
| 测试失败 | 查看日志定位问题，修复代码 |
| 超时 | 增加超时时间或优化测试 |
| 平台特定错误 | 添加平台兼容代码 |

---

## 六、验证步骤

### 6.1 配置验证

```bash
# 验证远程仓库配置
git remote -v

# 验证分支状态
git branch -a

# 验证提交状态
git log --oneline -5
```

### 6.2 CI 结果验证

1. 打开 GitHub Actions 页面
2. 确认工作流名称正确
3. 检查所有测试任务状态
4. 查看覆盖率报告
5. 确认无失败任务

---

**文档版本**: v1.0  
**生成时间**: 2026-06-03