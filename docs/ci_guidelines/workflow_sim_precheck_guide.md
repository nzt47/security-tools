# 工作流模拟预检指南（simulate_ci_failure_notify.py）

## 背景

GitHub 免费 runner 排队严重时，CI 修复无法及时在远端验证。本脚本在本地逐条镜像
`.github/workflows/ci-failure-notify.yml` 的判定表达式，替代排队中的 GitHub Action，
作为 pre-commit 本地预检步骤运行。

2026-08-04 根因：`visiblelabs/dingtalk-action@v1` 仓库在 GitHub 上不存在，导致
job 在 Set up job 阶段直接失败。替换为项目自维护的 `scripts/observability_dingtalk_notify.py` 后，
用本脚本在本地验证通知链路判定逻辑的正确性。

## 脚本功能

- **6 个场景判定**：真实失败 / 手动模拟 / 手动+webhook / Docker 恢复(上次失败) /
  恢复+webhook / 成功无变化
- **8 项边界检查（B1-B8）**：webhook 空跳过不中断、prep 兜底、不误建 Issue、
  手动触发 recover 不运行、无历史不误报、空 webhook 不触发调用、布尔判定兼容等
- **yml 预检**：检查 `ci-failure-notify.yml` 是否残留**代码级**
  `uses: visiblelabs/dingtalk-action` 引用（注释提及不算，不会误拦截）
- **退出码语义**：`--all` 模式下任一边界失败或预检发现残留引用 → `exit 1`

## 使用方法

```bash
# 运行全部场景 + 边界检查（pre-commit hook 内使用此模式）
python scripts/simulate_ci_failure_notify.py --all

# 运行单个场景
python scripts/simulate_ci_failure_notify.py --scenario wf_failure
python scripts/simulate_ci_failure_notify.py --scenario manual_simulate
python scripts/simulate_ci_failure_notify.py --scenario manual_with_webhook --webhook https://oapi.dingtalk.com/robot/send?access_token=DEMO
python scripts/simulate_ci_failure_notify.py --scenario docker_recover
python scripts/simulate_ci_failure_notify.py --scenario docker_recover_webhook --webhook https://oapi.dingtalk.com/robot/send?access_token=DEMO

# 真实调用通知脚本（--live，需要脚本与 webhook 均可用）
python scripts/simulate_ci_failure_notify.py --scenario manual_with_webhook --live
```

## pre-commit 集成

hook 模板（`scripts/dev/hook_fail_safe.psm1` 的 `Get-HookContent`）在
INVARIANT 段之后、`exit 0` 之前新增 WORKFLOW_SIM 段：

- 脚本存在时：`python "$WORKFLOW_SIM" --all` 失败（exit 1）→ 提交被阻止
- 脚本缺失时：静默跳过（跨仓库安全，不因缺脚本误阻塞）
- 豁免：`SKIP_WORKFLOW_SIM=1` 显式跳过

```bash
# 临时豁免（本地快速提交）
SKIP_WORKFLOW_SIM=1 git commit
# 或完全绕过所有 hook
git commit --no-verify
```

## SKIP_WORKFLOW_SIM 适用场景

仅在以下情况使用豁免，其他情况应修复脚本/yml 后正常提交：

1. **临时验证与模拟脚本本身无关的改动**：例如只改文档、配置，且确定不影响
   ci-failure-notify.yml 判定逻辑时，可临时跳过以减少提交耗时
2. **本地环境缺少脚本依赖**：脚本运行环境异常（如 Python 版本不兼容）且急需提交时
3. **调试脚本本身**：正在修改 simulate 脚本时，避免 hook 递归校验自身

**不适用场景**：

- 修改了 ci-failure-notify.yml 的判定逻辑但未同步更新脚本场景
- 引入新的第三方 action 引用（应先用预检确认仓库与 tag 有效）
- 修复不完整（yml 仍残留失效 action）时用豁免绕过会掩盖问题

## 验证记录（2026-08-05）

- 构造含 `uses: visiblelabs/dingtalk-action@v1` 的临时 yml → 预检 `[BLOCK]` → exit 1，
  真实 `git commit` 被 hook 拦截（`[pre-commit][ERROR] 工作流模拟校验未通过, 提交被阻止`）
- 正常 yml → 6 场景判定符合预期，边界检查 8/8 PASS → exit 0
- 真实 git commit 全链路：预检通过 → 不变量 12/12 → 工作流模拟通过 → exit 0

## 失效 action 扫描结论（2026-08-05）

全仓 `.github/workflows/*.yml` 共 19 个唯一 action 引用（官方 `actions/*` 12 个 +
第三方 7 个），已逐一对 GitHub API 核验仓库与 tag，**全部有效**：

- `actions/*`：cache@v4、checkout@v4/v5、configure-pages@v5、deploy-pages@v4、
  download-artifact@v4、github-script@v7、setup-node@v4、setup-python@v5、
  upload-artifact@v4/v6、upload-pages-artifact@v3
- 第三方：docker/build-push-action@v5、docker/login-action@v3、
  docker/setup-buildx-action@v3、slackapi/slack-github-action@v1.24.0、
  softprops/action-gh-release@v3、dawidd6/action-send-mail@v3、codecov/codecov-action@v4

注意：`agent\security-tools\` 嵌套副本（gitignore 已忽略，独立 clone）中
ci-failure-notify.yml 仍是修复前旧版，含代码级失效引用，不参与当前 CI 流程。
