# P0 安全验证 Workflow 模板

用于在其他项目快速复用「敏感数据脱敏防回归」的 CI 验证能力。

## 文件清单

| 文件 | 用途 |
|------|------|
| `p0-security.template.yml` | Workflow 模板主体（含占位符） |

## 使用步骤

1. **复制模板**：将 `p0-security.template.yml` 复制到目标仓库的 `.github/workflows/` 目录，重命名为 `<your-name>.yml`（例如 `p0-security.yml`）。
2. **替换占位符**：在复制后的文件中全局搜索 `{{...}}`，按下表替换为项目实际值。
3. **确认依赖产物**：确保以下文件/目录在目标仓库中存在：
   - 回归测试文件（`{{REGRESSION_TEST_FILE}}`）
   - 补丁目录及补丁文件（`{{PATCH_DIR}}` / `{{PATCH_FILE}}`）
   - 静态扫描脚本（`{{SCAN_SCRIPT}}`）
4. **本地验证**：先在本地运行 `python -m pytest <测试文件> --collect-only -q` 确认测试类名引用正确。
5. **推送触发**：推送到 `master`/`main` 分支，或在 GitHub Actions 页面手动触发 `workflow_dispatch` 验证。

## 占位符清单

| 占位符 | 含义 | 示例值 |
|--------|------|--------|
| `{{WORKFLOW_NAME}}` | Workflow 显示名称 | `P0 安全验证` |
| `{{WORKFLOW_FILE_NAME}}` | workflow 文件名（不含 .yml，用于 push.paths 自触发） | `p0-security` |
| `{{PYTHON_VERSION}}` | Python 版本 | `3.10` |
| `{{REGRESSION_TEST_FILE}}` | P0 回归测试文件路径 | `tests/regression/test_p0_security_fix.py` |
| `{{SCAN_SCRIPT}}` | 静态扫描脚本路径 | `scripts/scan_sensitive_regex.py` |
| `{{PATCH_DIR}}` | 补丁目录 | `patches/p0_security` |
| `{{PATCH_FILE}}` | 补丁文件名 | `p0_security_test_extension.patch` |
| `{{EXPECTED_TEST_COUNT}}` | 预期测试用例数量（数量验证阈值） | `68` |
| `{{CROSS_MODULE_TEST_CLASSES}}` | 跨模块一致性测试引用的类列表（空格分隔，每个用完整路径 `file::Class`） | `tests/.../test_x.py::TestCrossModuleConsistency tests/.../test_x.py::TestBearerRegression` |
| `{{PATCH_TEST_CLASS_1}}` | 补丁中应包含的测试类名 1（格式校验用） | `TestLoggingUtilsGreedyRegexRegression` |
| `{{PATCH_TEST_CLASS_2}}` | 补丁中应包含的测试类名 2（格式校验用） | `TestCrossModuleConsistency` |

> **关于 `paths` 中的敏感模块路径**：模板内 `push.paths` / `pull_request.paths` 列出了示例的敏感数据模块路径（如 `agent/utils/sensitive_data_filter.py`）。请根据目标项目的实际模块结构增删，只保留真正涉及脱敏逻辑的文件，避免无关变更频繁触发。

## 4 层防护说明

| Job | 防护目标 | 失败退出码 |
|-----|---------|-----------|
| 敏感数据正则静态扫描 | 检测贪婪正则模式（误匹配/漏匹配） | 脚本自定义 |
| P0 Security Regression Test | 68 个防复发测试用例全通过 | pytest 退出码 |
| 补丁完整性验证 | 补丁文件存在 + 测试数量达标 | exit 1 |
| 跨模块脱敏一致性验证 | 多个脱敏模块行为一致 | exit 4（类名找不到） |

## 最佳实践警示（本模板沉淀的 4 个曾踩的坑）

1. **actions 版本必须 v4+**
   - `checkout@v4` / `setup-python@v5` / `upload-artifact@v4`
   - v3 已被 GitHub 废弃，使用 v3 的 job 会在 setup 阶段被**自动失败**（不报业务错误，难排查）

2. **测试类名必须与测试文件完全一致**
   - 注意 `Bearer` vs `BearerToken` 等拼写差异
   - 引用前用 `grep "^class Test" <测试文件>` 确认实际类名
   - 类名找不到时 pytest 退出码为 4

3. **pytest.ini 配置 `--timeout` 时，依赖安装必须含 `pytest-timeout`**
   - 否则 `collect-only` 报 `unrecognized arguments: --timeout=60`
   - 在 `set -eo pipefail` 下，grep 无匹配会触发 `exit 1`（即使脚本逻辑想降级为警告）

4. **重复的安全扫描 job 应由独立 workflow 覆盖**
   - 例如 gitleaks 硬编码密码扫描应放在「全分支独立 workflow」
   - 避免在同一 workflow 内重复（既浪费资源，又因 v3 废弃放大失败面）

## 触发策略

模板默认配置 4 种触发方式：

- **push**：`main` / `master` / `develop` / `phase2-**` / `release/**` 分支，且改动涉及敏感模块路径
- **pull_request**：`main` / `master` / `develop` 分支
- **schedule**：每天 03:00 UTC 定时全量验证
- **workflow_dispatch**：手动触发

> 如默认分支非 `master`，请调整 `branches` 列表。`master` 已默认加入，确保推送到 master 也能自动触发验证。
