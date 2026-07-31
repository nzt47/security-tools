# P0 安全验证 Workflow 模板

用于在其他项目快速复用「敏感数据脱敏防回归」的 CI 验证能力。

含 4 层防护（静态扫描 / 回归测试 / 补丁完整性 / 跨模块一致性），
内置 `[P0-CI]` 结构化日志体系，CI 失败时可一键 grep 定位问题。

## 快速上手（3 步上线）

```bash
# 1. 复制模板到目标仓库
cp .github/workflow-templates/p0-security.template.yml \
   /path/to/target-repo/.github/workflows/p0-security.yml

# 2. 替换全部 {{...}} 占位符（11 个，见下方清单）
#    用编辑器全局搜索 {{ 替换为项目实际值

# 3. 推送触发验证
cd /path/to/target-repo
git add .github/workflows/p0-security.yml
git commit -m "ci: 接入 P0 安全验证 workflow"
git push origin master
```

> 推送前建议本地验证：`python -m pytest <测试文件> --collect-only -q` 确认测试类名引用正确。

## 文件清单

| 文件 | 用途 |
|------|------|
| `p0-security.template.yml` | Workflow 模板主体（含 11 个占位符 + 4 个最佳实践警示） |
| `examples/flask-auth-p0-security.yml` | 完整复用示例（Flask 认证服务，含占位符替换对照表） |
| `README.md` | 本文档（复用指南 + 占位符清单 + 验证清单） |

> 单元测试：`tests/unit/test_p0_security_template.py` 验证模板占位符完整性、替换彻底性、YAML 有效性和结构完整性（16 项测试）。

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

## 复用示例：Flask 认证服务

完整示例见 `examples/flask-auth-p0-security.yml`，以下为占位符替换对照表：

| 占位符 | Flask 认证服务取值 | 说明 |
|--------|-------------------|------|
| `{{WORKFLOW_NAME}}` | `Flask认证服务P0安全验证` | 项目前缀 + 功能名 |
| `{{REGRESSION_TEST_FILE}}` | `tests/security/test_auth_redaction.py` | 认证脱敏回归测试 |
| `{{CROSS_MODULE_TEST_CLASSES}}` | `...::TestJWTTokenRedaction ...::TestPasswordHashRedaction` | JWT + 密码哈希两个脱敏模块 |
| `{{SCAN_SCRIPT}}` | `scripts/scan_auth_secrets.py` | 认证密钥扫描脚本 |
| `{{PATCH_DIR}}` | `patches/auth_security` | 认证安全补丁目录 |
| `{{EXPECTED_TEST_COUNT}}` | `42` | 该项目的回归测试数量 |

> **关键差异**：`push.paths` 中的敏感模块路径需替换为目标项目的实际文件（如 `app/auth/tokens.py` 而非 `agent/utils/sensitive_data_filter.py`）。

## 4 层防护说明

| Job | 防护目标 | 失败退出码 |
|-----|---------|-----------|
| 敏感数据正则静态扫描 | 检测贪婪正则模式（误匹配/漏匹配） | 脚本自定义 |
| P0 Security Regression Test | 68 个防复发测试用例全通过 | pytest 退出码 |
| 补丁完整性验证 | 补丁文件存在 + 测试数量达标 | exit 1 |
| 跨模块脱敏一致性验证 | 多个脱敏模块行为一致 | exit 4（类名找不到） |

## CI 日志体系

模板内置 `[P0-CI][job名]` 前缀的结构化日志，方便在 GitHub Actions 日志中一键 grep 定位：

```
[P0-CI][static-scan] START @ 2026-08-01T03:00:01Z
[P0-CI][p0-tests] pytest 退出码: 0 (耗时 145s)
[P0-CI][p0-tests] ✅ P0 回归测试全部通过
[P0-CI][cross-module] ❌ 退出码4=用法错误：测试类名拼写错误
[P0-CI][summary] ❌ P0 安全验证存在失败项
```

| 日志能力 | 说明 |
|---------|------|
| START/END 时间戳 | 每个 job 开始结束时输出 UTC 时间戳，便于追踪耗时 |
| 步骤计时 | 关键步骤（扫描/测试/收集）输出耗时秒数 |
| pytest 退出码含义 | 退出码非 0 时输出 0-5 各值含义（1=断言失败 4=用法错误） |
| JUnit XML 统计 | 从测试报告提取 总/通过/失败/错误/跳过 数量 |
| 失败排查指引 | summary job 在失败时输出逐项排查命令和常见原因 |

> CI 日志中搜索 `[P0-CI]` 可快速定位所有关键节点；搜索 `[P0-CI][p0-tests]` 可定位回归测试 job 的全部日志。

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

## 复用验证清单

复用模板后，逐项确认以下检查点：

- [ ] 全部 11 个 `{{...}}` 占位符已替换（全局搜索 `{{` 确认无残留）
- [ ] `push.paths` / `pull_request.paths` 已替换为目标项目的敏感模块路径
- [ ] `branches` 列表包含目标项目的默认分支（`master` 或 `main`）
- [ ] 回归测试文件存在且 `python -m pytest <文件> --collect-only -q` 能正常收集
- [ ] 跨模块测试类名与测试文件中的实际类名完全一致（`grep "^class Test" <文件>` 确认）
- [ ] 补丁目录 `{{PATCH_DIR}}/` 存在，含 `README.md` 和补丁文件
- [ ] 静态扫描脚本 `{{SCAN_SCRIPT}}` 存在且可执行
- [ ] pytest.ini 若配置 `--timeout`，模板中所有 job 的依赖安装均含 `pytest-timeout`
- [ ] actions 版本均为 v4+（`checkout@v4` / `setup-python@v5` / `upload-artifact@v4`）
- [ ] 推送后在 GitHub Actions 页面手动触发 `workflow_dispatch` 验证一次全流程
