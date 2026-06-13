# GitHub Actions 运行结果监控指南

## 文档概述

本指南详细说明如何监控 GitHub Actions 运行状态、查看测试日志、排查失败原因，并提供常见问题的解决方案。

---

## 一、访问 GitHub Actions

### 1.1 进入 Actions 页面

**方法1**: 通过仓库页面访问
```
1. 打开 GitHub 仓库页面
2. 点击顶部导航栏的 "Actions" 标签
3. 在左侧工作流列表中找到 "云枢系统测试流程"
4. 点击进入查看运行记录
```

**方法2**: 直接访问 URL
```
https://github.com/<your-username>/<your-repo>/actions
```

### 1.2 查看运行记录

| 状态标识 | 含义 |
|---------|------|
| ✅ 绿色对勾 | 所有任务通过 |
| ❌ 红色叉号 | 有任务失败 |
| ⚪ 灰色圆圈 | 运行中或等待中 |
| 🟡 黄色圆圈 | 部分通过 |

---

## 二、查看测试任务状态

### 2.1 任务列表概览

推送代码后，你会看到以下任务列表：

**Ubuntu 平台任务**:
- `代码质量检查` - 在 ubuntu-latest 上运行
- `单元测试 (Python 3.8 - ubuntu-latest)`
- `单元测试 (Python 3.9 - ubuntu-latest)`
- `单元测试 (Python 3.10 - ubuntu-latest)`
- `单元测试 (Python 3.11 - ubuntu-latest)`
- `单元测试 (Python 3.12 - ubuntu-latest)`
- `集成测试 (Python 3.8 - ubuntu-latest)`
- `集成测试 (Python 3.9 - ubuntu-latest)`
- `集成测试 (Python 3.10 - ubuntu-latest)`
- `集成测试 (Python 3.11 - ubuntu-latest)`
- `集成测试 (Python 3.12 - ubuntu-latest)`
- `性能测试 (Python 3.8 - ubuntu-latest)`
- `性能测试 (Python 3.9 - ubuntu-latest)`
- `性能测试 (Python 3.10 - ubuntu-latest)`
- `性能测试 (Python 3.11 - ubuntu-latest)`
- `性能测试 (Python 3.12 - ubuntu-latest)`
- `覆盖率检查 (Python 3.8 - ubuntu-latest)`
- `覆盖率检查 (Python 3.9 - ubuntu-latest)`
- `覆盖率检查 (Python 3.10 - ubuntu-latest)`
- `覆盖率检查 (Python 3.11 - ubuntu-latest)`
- `覆盖率检查 (Python 3.12 - ubuntu-latest)`

**Windows 平台任务**:
- `单元测试 (Python 3.8 - windows-latest)`
- `单元测试 (Python 3.9 - windows-latest)`
- `单元测试 (Python 3.10 - windows-latest)`
- `单元测试 (Python 3.11 - windows-latest)`
- `单元测试 (Python 3.12 - windows-latest)`
- `集成测试 (Python 3.8 - windows-latest)`
- `集成测试 (Python 3.9 - windows-latest)`
- `集成测试 (Python 3.10 - windows-latest)`
- `集成测试 (Python 3.11 - windows-latest)`
- `集成测试 (Python 3.12 - windows-latest)`
- `性能测试 (Python 3.8 - windows-latest)`
- `性能测试 (Python 3.9 - windows-latest)`
- `性能测试 (Python 3.10 - windows-latest)`
- `性能测试 (Python 3.11 - windows-latest)`
- `性能测试 (Python 3.12 - windows-latest)`
- `覆盖率检查 (Python 3.8 - windows-latest)`
- `覆盖率检查 (Python 3.9 - windows-latest)`
- `覆盖率检查 (Python 3.10 - windows-latest)`
- `覆盖率检查 (Python 3.11 - windows-latest)`
- `覆盖率检查 (Python 3.12 - windows-latest)`

**测试总结任务**:
- `测试总结` - 在所有测试完成后运行

### 2.2 任务依赖关系

```
代码质量检查
       ↓
单元测试 → 集成测试 → 性能测试
       ↓         ↓         ↓
    覆盖率检查 ←←←←←←←←←←
       ↓
    测试总结
```

---

## 三、查看详细日志

### 3.1 查看任务日志

1. **点击失败的任务名称**进入任务详情页
2. 在左侧步骤列表中找到**标红的失败步骤**
3. 点击步骤名称展开详细日志
4. 日志会显示每个命令的执行过程和输出

### 3.2 日志搜索技巧

| 搜索关键词 | 用途 |
|-----------|------|
| `ERROR` | 查找错误信息 |
| `FAILED` | 查找失败的测试用例 |
| `Traceback` | 查找异常堆栈 |
| `Timeout` | 查找超时问题 |
| `Could not find` | 查找依赖安装失败 |
| `Permission denied` | 查找权限问题 |
| `AssertionError` | 查找断言失败 |

### 3.3 常见日志模式

**测试失败日志示例**:
```
tests/unit/test_example.py::test_function FAILED
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/home/runner/work/repo/tests/unit/test_example.py", line 42, in test_function
    assert result == expected
AssertionError: assert 42 == 24
```

**依赖安装失败日志示例**:
```
Collecting pytest-benchmark>=4.0
  ERROR: Could not find a version that satisfies the requirement pytest-benchmark>=4.0
  ERROR: No matching distribution found for pytest-benchmark>=4.0
```

**超时日志示例**:
```
Timeout > 120 seconds
Error: The operation was canceled.
```

---

## 四、常见失败场景与排查

### 4.1 依赖安装失败

**错误特征**:
```
ERROR: Could not find a version that satisfies the requirement XXXX
ERROR: No matching distribution found for XXXX
```

**排查步骤**:
1. 确认 `pyproject.toml` 中的版本约束是否正确
2. 检查该依赖是否支持当前 Python 版本
3. 检查该依赖是否支持当前操作系统
4. 查看 CI 日志中使用的 Python 版本和平台

**解决方案**:
```toml
# 在 pyproject.toml 中添加条件依赖
[tool.poetry.dependencies]
pytest-benchmark = [
    { version = ">=4.0", python = ">=3.9" },
    { version = ">=3.4", python = "<3.9" }
]
```

### 4.2 测试用例失败

**错误特征**:
```
FAILED tests/unit/test_example.py::test_function
AssertionError: ...
```

**排查步骤**:
1. 查看失败测试的具体断言
2. 分析测试用例的期望结果和实际结果
3. 在本地运行相同测试复现问题
4. 修复代码逻辑或测试用例

**解决方案**:
```bash
# 在本地运行失败的测试
python -m pytest tests/unit/test_example.py::test_function -v
```

### 4.3 测试超时

**错误特征**:
```
Timeout > 300 seconds
Error: The operation was canceled.
```

**排查步骤**:
1. 确认测试用例是否需要大量计算或等待
2. 检查是否有死循环或无限等待
3. 考虑是否需要增加超时时间
4. 优化测试用例性能

**解决方案**:
```yaml
# 在 workflow 中增加超时时间
run: |
  pytest tests/performance/ \
    --timeout=600 \  # 增加到 600 秒
    --benchmark-only
```

### 4.4 平台特定错误

**错误特征**:
```
# Windows 特有错误
WinError 10038: ...
PermissionError: [WinError 5] 拒绝访问

# Linux 特有错误
PermissionError: [Errno 13] Permission denied
```

**排查步骤**:
1. 确认代码是否包含平台特定逻辑
2. 检查文件路径处理是否正确
3. 确认权限设置是否正确
4. 使用平台检测进行条件处理

**解决方案**:
```python
import os

if os.name == 'nt':
    # Windows 特定代码
    pass
else:
    # Linux 特定代码
    pass
```

### 4.5 覆盖率不达标

**错误特征**:
```
FAILED coverage: 68% < 70%
```

**排查步骤**:
1. 查看覆盖率报告中的未覆盖文件
2. 确认是否有新代码未添加测试
3. 检查测试用例是否覆盖了所有路径

**解决方案**:
```bash
# 查看详细覆盖率报告
coverage report -m
coverage html
```

---

## 五、下载测试报告

### 5.1 下载覆盖率报告

1. 在任务列表中找到 `覆盖率检查` 任务
2. 点击进入任务详情页
3. 点击右上角的 **Artifacts** 按钮
4. 下载 `full-coverage-report-<os>-py<version>` 文件

### 5.2 下载测试结果

1. 在任务列表中找到失败的测试任务
2. 点击进入任务详情页
3. 点击右上角的 **Artifacts** 按钮
4. 下载 `test-results-<type>-<os>-py<version>` 文件

### 5.3 下载性能报告

1. 在任务列表中找到 `性能测试` 任务
2. 点击进入任务详情页
3. 点击右上角的 **Artifacts** 按钮
4. 下载 `benchmark-report-<os>-py<version>` 文件

---

## 六、处理失败任务

### 6.1 失败处理流程

```
1. 查看失败任务的详细日志
2. 定位错误原因
3. 在本地复现问题
4. 修复代码或配置
5. 提交修复
6. 重新触发 CI
```

### 6.2 重新运行失败任务

**方法1**: 重新推送代码
```bash
git add .
git commit --amend  # 修改上次提交
git push -f origin dev
```

**方法2**: 在 GitHub 上重新运行
```
1. 进入失败的工作流运行页面
2. 点击右上角的 "Re-run all jobs" 按钮
3. 等待新的运行开始
```

**方法3**: 仅重新运行失败的任务
```
1. 进入失败的任务详情页
2. 点击右上角的 "Re-run" 按钮
3. 等待任务重新运行
```

---

## 七、性能监控

### 7.1 执行时间监控

| 测试类型 | 预期时间 | 警告阈值 | 错误阈值 |
|---------|---------|---------|---------|
| 单元测试 | 2-3分钟 | 5分钟 | 10分钟 |
| 集成测试 | 3-5分钟 | 8分钟 | 15分钟 |
| 性能测试 | 2-4分钟 | 6分钟 | 12分钟 |
| 覆盖率检查 | 1-2分钟 | 3分钟 | 5分钟 |

### 7.2 并行执行监控

GitHub Actions 会自动并行执行多个任务，但有一定的并发限制：
- 同一仓库默认最多 20 个并行任务
- 可以在 `strategy` 中设置 `max-parallel` 参数

---

## 八、通知与告警

### 8.1 设置通知

可以在 GitHub 上设置通知偏好：
1. 进入仓库设置页面
2. 点击左侧的 "Notifications"
3. 设置接收通知的方式和条件

### 8.2 添加 Slack/钉钉通知（可选）

```yaml
# 在 workflow 中添加通知步骤
- name: 发送通知到 Slack
  if: failure()
  uses: slackapi/slack-github-action@v1.24.0
  with:
    payload: |
      {
        "text": "云枢系统测试失败",
        "attachments": [
          {
            "title": "查看日志",
            "title_link": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
          }
        ]
      }
  env:
    SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

---

## 九、最佳实践

### 9.1 日志分析技巧

1. **从后往前看**: 错误信息通常在日志末尾
2. **搜索关键词**: 使用浏览器的搜索功能查找关键错误
3. **对比成功任务**: 如果同一测试在某个版本通过，另一个版本失败，对比日志差异
4. **查看环境信息**: 确认 Python 版本、操作系统、依赖版本

### 9.2 本地复现技巧

```bash
# 安装相同版本的依赖
pip install pytest pytest-cov pytest-mock
pip install -e .[dev]

# 运行特定测试
python -m pytest tests/unit/test_example.py::test_function -v

# 模拟 CI 环境变量
export COVERAGE_THRESHOLD=70
python -m pytest tests/unit/ --cov=agent --cov-fail-under=70
```

### 9.3 常见修复模式

| 问题类型 | 修复策略 |
|---------|---------|
| 版本兼容性 | 添加条件依赖或版本约束 |
| 平台兼容性 | 使用平台检测进行条件处理 |
| 测试失败 | 修复代码逻辑或测试用例 |
| 超时 | 优化测试或增加超时时间 |
| 覆盖率不足 | 添加测试用例 |

---

## 十、快速参考

### 10.1 常用命令

```bash
# 查看当前分支
git branch

# 查看远程配置
git remote -v

# 推送代码
git push origin dev

# 查看提交日志
git log --oneline -5

# 在本地运行测试
python -m pytest tests/unit/ -v
```

### 10.2 常用 URL

| 用途 | URL |
|------|-----|
| Actions 页面 | `https://github.com/<username>/<repo>/actions` |
| 工作流文件 | `https://github.com/<username>/<repo>/blob/main/.github/workflows/test.yml` |
| 运行记录 | `https://github.com/<username>/<repo>/actions/runs/<run_id>` |

### 10.3 常见错误码

| 错误码 | 含义 | 处理方向 |
|-------|------|---------|
| 1 | 一般错误 | 查看详细日志 |
| 127 | 命令未找到 | 检查依赖是否安装 |
| 128 | Git 错误 | 检查远程仓库配置 |
| 255 | 测试失败 | 检查测试用例 |

---

**文档版本**: v1.0  
**生成时间**: 2026-06-03