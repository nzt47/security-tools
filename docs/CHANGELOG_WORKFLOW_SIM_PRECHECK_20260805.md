# 变更报告：工作流模拟预检豁免场景 + 失效 Action 全仓扫描

**日期**: 2026-08-05
**变更类型**: 可观测性增强（本地预检护栏 + 依赖健康检查）
**关联提交**:
- `29d44803` docs(ci): 新增工作流模拟预检使用指南（含 SKIP_WORKFLOW_SIM 豁免场景与失效 action 扫描结论）
- `7687bdd9` fix(ci): simulate_ci_failure_notify.py 退出码逻辑入库（消除拦截形同虚设的无痕回滚风险）

---

## 一、SKIP_WORKFLOW_SIM 豁免场景确认

pre-commit 的 WORKFLOW_SIM 段（`simulate_ci_failure_notify.py --all`）失败即阻止提交。
豁免变量 `SKIP_WORKFLOW_SIM=1` 的**适用场景**：

1. 仅改文档/配置，且确定不影响 ci-failure-notify.yml 判定逻辑（减少提交耗时）
2. 本地环境缺脚本依赖（如 Python 版本不兼容）且急需提交
3. 正在调试 simulate 脚本本身（避免 hook 递归校验自身）

**不适用场景**（豁免会掩盖问题）：

- 修改了 yml 判定逻辑但未同步更新脚本场景
- 引入新的第三方 action 引用（应先核验仓库与 tag 有效）
- 修复不完整（yml 仍残留失效 action）时绕过

## 二、失效 Action 全仓扫描结论

对 `.github/workflows/*.yml` 全部 `uses:` 引用逐一核验 GitHub API（仓库 + tag）：

- **19 个唯一引用全部有效**：官方 `actions/*` 12 个（cache/checkout/configure-pages/deploy-pages/download-artifact/github-script/setup-node/setup-python/upload-artifact/upload-pages-artifact）+ 第三方 7 个（docker/build-push-action、docker/login-action、docker/setup-buildx-action、slackapi/slack-github-action、softprops/action-gh-release、dawidd6/action-send-mail、codecov/codecov-action）
- 此前的 `visiblelabs/dingtalk-action@v1`（仓库不存在）为唯一失效引用，已在 ci-failure-notify.yml 中移除并替换为自维护脚本

## 三、连带发现与修复

- **退出码逻辑未入库**：`da309690`(现 `daaa3f6e`) 提交的 simulate 脚本缺失 `sys.exit(1)`，导致 WORKFLOW_SIM 段永远放行。已由 `7687bdd9` 固化入库，并生成修复对比报告 `docs/observability/simulate_version_fix_report.md`
- **旧副本隐患**：`agent\security-tools\`（gitignore 忽略的独立 clone）中 ci-failure-notify.yml 残留 8 项隐患（失效 action / secret 拼错 / 无手动触发 / 无 null 兜底 / job if 不兼容 / 缺恢复通知 / 监控列表不全 / markdown 硬编码），详见 `docs/observability/stale_copy_ci_notify_audit.md`

## 四、影响与后续

- 本地 pre-commit 现在具备真实拦截能力（构造错误 yml → exit 1 阻止提交，已验证）
- 后续改动 simulate 脚本务必保持 `--all` 退出码语义（失败必须 exit 1）
- 建议定期对 `security-tools\` 副本执行 `git pull` 或删除，避免静默漂移
