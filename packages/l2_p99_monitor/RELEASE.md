# l2_p99_monitor PyPI 发布指南

本文档说明如何将 `l2_p99_monitor` 包发布到 PyPI（Python Package Index）。

## 前置条件

1. **PyPI 账号**：在 [pypi.org](https://pypi.org/account/register/) 注册
2. **API Token**：在 [PyPI Account Settings](https://pypi.org/manage/account/token/) 创建 API token
3. **twine**：用于上传包到 PyPI

```bash
pip install build twine
```

## 发布步骤

### 1. 配置 PyPI 认证

创建 `~/.pypirc` 文件（Linux/macOS）或 `%USERPROFILE%\.pypirc`（Windows）：

```ini
[distutils]
index-servers = pypi

[pypi]
repository = https://upload.pypi.org/legacy/
username = __token__
password = pypi-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

> **安全**：不要将 `.pypirc` 提交到版本控制系统（已加入 .gitignore）

### 2. 更新版本号

编辑 `pyproject.toml`，更新 `version` 字段：

```toml
[project]
version = "1.0.1"  # 从 1.0.0 升级到 1.0.1
```

遵循 [语义化版本](https://semver.org/lang/zh-CN/)：
- `MAJOR.MINOR.PATCH`
- 不兼容 API 修改：MAJOR +1
- 向下兼容功能新增：MINOR +1
- 向下兼容问题修复：PATCH +1

### 3. 构建分发包

```bash
cd packages/l2_p99_monitor

# 清理旧构建产物
Remove-Item -Recurse -Force dist, build, *.egg-info -ErrorAction SilentlyContinue

# 构建 sdist（源码包）+ wheel（二进制包）
python -m build
```

构建产物在 `dist/` 目录：
- `l2_p99_monitor-1.0.0-py3-none-any.whl`（wheel 包，推荐）
- `l2_p99_monitor-1.0.0.tar.gz`（源码包）

### 4. 本地验证

```bash
# 检查包元数据
twine check dist/*

# 本地安装测试
pip install dist/l2_p99_monitor-1.0.0-py3-none-any.whl

# 验证 CLI 可用
l2-p99-monitor --help

# 验证库导入
python -c "from l2_p99_monitor import P99Monitor; print('OK')"
```

### 5. 上传到 TestPyPI（可选，推荐首次发布时测试）

```bash
# 上传到 TestPyPI
twine upload --repository testpypi dist/*

# 从 TestPyPI 安装测试
pip install --index-url https://test.pypi.org/simple/ l2-p99-monitor
```

### 6. 上传到 PyPI

```bash
twine upload dist/*
```

### 7. 验证发布

```bash
# 从 PyPI 安装
pip install l2-p99-monitor

# 验证版本
pip show l2-p99-monitor

# 验证 CLI
l2-p99-monitor --input bench_ci.log --threshold 1000
```

## 一键发布脚本

### Windows（PowerShell）

```powershell
# .\scripts\release.ps1 -Version 1.0.1
param([string]$Version)

# 更新版本号
(pyproject.toml 中的 version 字段)

# 构建 + 检查 + 上传
python -m build
twine check dist/*
twine upload dist/*
```

### Linux/macOS

```bash
# ./scripts/release.sh 1.0.1
#!/bin/bash
VERSION=$1

# 构建 + 检查 + 上传
python -m build
twine check dist/*
twine upload dist/*
```

## 安装命令（发布后）

### 用户安装

```bash
# 从 PyPI 安装
pip install l2-p99-monitor

# 或指定版本
pip install l2-p99-monitor==1.0.0

# 升级
pip install --upgrade l2-p99-monitor
```

### 使用示例

```bash
# CLI 使用
l2-p99-monitor --input bench_ci.log --threshold 1000

# 库导入
python -c "
from l2_p99_monitor import P99Monitor, create_parser, ConsoleChannel
monitor = P99Monitor(parser=create_parser(), threshold=1000, channels=[ConsoleChannel()])
result = monitor.check_file('bench_ci.log')
print(f'P99: {result.data.p99}ms, 状态: {\"告警\" if result.alerted else \"正常\"}')
"
```

## 版本发布检查清单

- [ ] 更新 `pyproject.toml` 中的 `version`
- [ ] 更新 `l2_p99_monitor/__init__.py` 中的 `__version__`
- [ ] 运行单元测试：`python tests/test_monitor.py`
- [ ] 更新 `CHANGELOG.md`（如有）
- [ ] 构建：`python -m build`
- [ ] 检查：`twine check dist/*`
- [ ] TestPyPI 验证（可选）
- [ ] 上传 PyPI：`twine upload dist/*`
- [ ] 验证安装：`pip install l2-p99-monitor`
- [ ] 创建 Git tag：`git tag v1.0.1 && git push origin v1.0.1`
- [ ] 创建 GitHub Release

## 回滚说明

PyPI 不允许重新上传相同版本号的包。如果发布有问题：

1. **修复后发布新版本**：升级版本号（如 1.0.0 → 1.0.1），重新发布
2. **yank 旧版本**（不删除，但不在默认搜索结果中）：
   ```bash
   pip install pypi-command
   pypi yank l2-p99-monitor==1.0.0
   ```

## CI 自动发布（可选）

可在 GitHub Actions 中配置自动发布到 PyPI：

```yaml
- name: Publish to PyPI
  if: startsWith(github.ref, 'refs/tags/v')
  env:
    TWINE_USERNAME: __token__
    TWINE_PASSWORD: ${{ secrets.PYPI_API_TOKEN }}
  run: |
    cd packages/l2_p99_monitor
    python -m build
    twine upload dist/*
```

创建 tag 触发发布：
```bash
git tag v1.0.0
git push origin v1.0.0
```
