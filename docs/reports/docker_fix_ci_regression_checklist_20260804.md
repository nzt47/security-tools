# Docker kwarg 误报修复 — CI 回归测试待办清单（2026-08-04）

> 关联：修复方案 [docker_false_positive_fix_plan_20260804.md](./docker_false_positive_fix_plan_20260804.md)、遗留待办 `todo_followup_20260804.md` §1
> 状态：代码修改已应用，**待执行 CI 回归验证**

---

## 一、修复完成状态

| 修改点 | 文件 | 状态 | 验证 |
|--------|------|------|------|
| 1. 输出路径迁移 + exit 分流 | [.github/workflows/kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml) | ✅ 已应用 | PyYAML 语法校验通过，4 job / 步骤结构完整 |
| 2. case 1) 加证据校验 | [packages/kwarg_scanner/docker-entrypoint.sh](../../packages/kwarg_scanner/docker-entrypoint.sh) | ✅ 已应用 | 逻辑等价模拟器 4/4 PASS |
| 3. 顺手修 MEDIUM 统计 YAML 缩进 bug | kwarg-docker-scan.yml L206-207 | ✅ 已应用 | 单行 python3 -c 行为等价 4/4 PASS |
| 4. 逻辑回归测试 | [packages/kwarg_scanner/tests/test_entrypoint_logic.py](../../packages/kwarg_scanner/tests/test_entrypoint_logic.py) | ✅ 新增 | 故障注入 4 场景全 PASS |

**未完成**：本地 Docker 真实构建验证（环境 Docker Desktop 未运行，待用户启动后执行）。

---

## 二、CI 回归测试任务清单

### 任务 1：启动 Docker Desktop 并执行本地真实构建验证 【P0】

**前置**：用户启动 Docker Desktop（当前环境 `docker version` 报 `failed to connect to the docker API`）。

**步骤**：

```powershell
# 1. 确认 Docker 可用
docker version --format '{{.Server.Version}}'

# 2. 构建修复后镜像
cd c:\Users\Administrator\agent
docker build -t kwarg-scanner:fix-test ./packages/kwarg_scanner

# 3. 复现修复前误报(确认根因) — 报告写挂载根目录,应 PermissionError + exit 1
docker run --rm `
  -v "${PWD}:/project" `
  -e MIN_RISK=HIGH -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/project/test-before.json `
  kwarg-scanner:fix-test --path /project/agent 2>&1
# 期望: exit 3 (修复后 entrypoint 不再误判 HIGH),日志含 E_SCAN_CRASHED

# 4. 验证修复后行为 — 报告写 /output,应 exit 0
mkdir -p scan-output; chmod 777 scan-output
docker run --rm `
  -v "${PWD}:/project:ro" `
  -v "${PWD}/scan-output:/output" `
  -e MIN_RISK=HIGH -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/output/kwarg-high-risk-report.json `
  -e ENABLE_LOGGING=true `
  kwarg-scanner:fix-test --path /project/agent 2>&1 | Tee-Object docker-scan-high.log
# 期望: exit 0, scan-output/kwarg-high-risk-report.json 存在, summary.HIGH=0

# 5. JSON 报告字段校验
python -c "import json; d=json.load(open('scan-output/kwarg-high-risk-report.json')); print('summary:', d.get('summary')); print('findings:', len(d.get('findings',[])))"
# 期望: summary.HIGH=0, findings=52 (全 LOW)
```

**通过标准**：
- 步骤 3 exit 3（不再 exit 1 误报 HIGH）
- 步骤 4 exit 0，报告生成，summary.HIGH=0
- 步骤 5 findings=52 且 summary.HIGH=0

**失败处理**：若步骤 4 仍 PermissionError，检查 `scan-output` 目录权限（需 777）；若 exit 1，查看 `docker-scan-high.log` 的 `scan_complete` 日志确认 reason。

---

### 任务 2：提交修改并推送触发 CI 【P0】

**步骤**：

```powershell
cd c:\Users\Administrator\agent
git status
git diff .github/workflows/kwarg-docker-scan.yml packages/kwarg_scanner/docker-entrypoint.sh
git add .github/workflows/kwarg-docker-scan.yml packages/kwarg_scanner/docker-entrypoint.sh packages/kwarg_scanner/tests/test_entrypoint_logic.py docs/reports/docker_false_positive_fix_plan_20260804.md docs/reports/docker_fix_ci_regression_checklist_20260804.md
git commit -m "fix(ci): 修复 Docker kwarg 扫描 PermissionError 误报 HIGH 风险

- kwarg-docker-scan.yml: OUTPUT_FILE 迁移到预创建 777 临时目录(/output),
  /project 改只读挂载;判断步骤区分 exit1(真HIGH)与 exit3(扫描器异常)
- docker-entrypoint.sh: case 1) 增加 OUTPUT_FILE 存在性 + summary.HIGH>0
  双校验,无证据时 die E_SCAN_CRASHED(exit3),不再误判 high_risk_detected
- 顺手修复 medium-risk-scan 统计步骤的 YAML 缩进致 IndentationError bug
- 新增 test_entrypoint_logic.py: case 1) 逻辑故障注入回归测试(4 场景)

Closes: develop run 30817413422 误报 52 HIGH(实为 0 HIGH)"
git push origin develop
```

**通过标准**：
- `git commit` 成功（pre-commit hook 不阻断；若 CI 守卫触发，用 `SKIP_CI_GUARD=1` 需谨慎，优先让守卫通过）
- `git push` 成功
- GitHub Actions 触发 `关键字参数冲突扫描 (Docker)` workflow

**注意**：提交前确认 `git diff` 输出符合预期（仅 4 个文件变更）。

---

### 任务 3：监控 Docker 扫描 workflow 运行结果 【P0】

**步骤**：

```powershell
# 等待 workflow 触发后,查看最新 run
gh run list --workflow="kwarg-docker-scan.yml" --branch=develop --limit=3

# 监控具体 run(替换 <run-id>)
gh run watch <run-id>

# 查看 high-risk-scan job 日志
gh run view <run-id> --log --job=<job-id> | Select-String "scan_complete|exit_code|HIGH|E_SCAN_CRASHED"
```

**通过标准**：
- `prepare-image` job → success
- `high-risk-scan` job → **success**（exit 0）
- `medium-risk-scan` job → success（warning 允许）
- 日志含 `scan_complete` + `result=success` + `high_risk_count=0`
- 日志**不含** `PermissionError` / `high_risk_detected` / `E_SCAN_CRASHED`
- 下载 artifact `kwarg-docker-high-risk-report`，确认 `kwarg-high-risk-report.json` 的 `summary.HIGH = 0`

**失败处理**：
- 若 `high-risk-scan` 仍失败：下载 `docker-scan-high.log`，检查 `scan-output` 目录是否创建成功、`chmod 777` 是否被 runner 策略限制
- 若 entrypoint 报 `E_SCAN_CRASHED`：说明报告未生成，检查容器内 `/output` 挂载是否成功

---

### 任务 4：真实 HIGH 风险阻断回归验证 【P1】

**目标**：确认修复后真实 HIGH 风险仍能被正确阻断（防止过度放行）。

**步骤**：

```powershell
# 1. 在测试分支构造 HIGH 风险样本
git checkout -b test/kwarg-high-regression
New-Item -ItemType Directory -Path tests/fixtures -Force
@'
def bad_caller(**kwargs):
    # 故意触发 HIGH: **kwargs 未过滤保留键直接展开到 requests.get
    import requests
    requests.get(**kwargs)
'@ | Out-File -FilePath tests/fixtures/kwarg_high_risk_sample.py -Encoding utf8

# 2. 本地扫描该 fixture(应 exit 1)
docker run --rm `
  -v "${PWD}:/project:ro" `
  -v "${PWD}/scan-output:/output" `
  -e MIN_RISK=HIGH -e OUTPUT_FORMAT=json `
  -e OUTPUT_FILE=/output/report.json `
  kwarg-scanner:fix-test --path /project/tests/fixtures 2>&1
# 期望: exit 1, 日志含 high_risk_detected, high_risk_count>=1

# 3. 验证报告
python -c "import json; d=json.load(open('scan-output/report.json')); print('HIGH:', d['summary']['HIGH'])"
# 期望: HIGH >= 1

# 4. 清理测试分支
git checkout develop
git branch -D test/kwarg-high-regression
Remove-Item tests/fixtures/kwarg_high_risk_sample.py -Force
```

**通过标准**：
- 步骤 2 exit 1（真实 HIGH 被阻断）
- 日志含 `high_risk_detected` + `high_risk_count >= 1`
- 步骤 3 报告 `summary.HIGH >= 1`

**失败处理**：若 exit 0 或 exit 3，说明 entrypoint 校验逻辑误判，检查 `HIGH_COUNT` 解析是否正确（参考 test_entrypoint_logic.py 的 real_high 场景）。

---

### 任务 5：关联文档状态更新 【P2】

**步骤**：

- [ ] 更新 `todo_followup_20260804.md` §1 表格状态列：「待执行」→「已修复（run xxxxx 验证通过）」
- [ ] 更新 `bom_fix_links_cleanup_summary_20260803.md` §四 CI 状态快照表「关键字参数冲突扫描（Docker）」行：failure → success
- [ ] 在 [docker_false_positive_fix_plan_20260804.md](./docker_false_positive_fix_plan_20260804.md) §七执行清单勾选已完成项

---

### 任务 6：监控后续 3 次 develop 推送的扫描稳定性 【P2】

**目标**：确认修复持续有效，非偶发通过。

**步骤**：
- [ ] 后续 3 次 develop push/PR 的 `关键字参数冲突扫描 (Docker)` workflow 均 success
- [ ] 任一次失败立即按任务 3 失败处理流程排查

**通过标准**：连续 3 次 success。

---

## 三、回滚指引

若任务 3 CI 验证失败且无法快速修复：

1. **快速回滚**：
   ```powershell
   git revert <修复 commit> --no-edit
   git push origin develop
   ```
2. **临时禁用 workflow**：在 `kwarg-docker-scan.yml` 顶部 `on:` 块加 `if: false`（仅禁用触发，保留文件供排查）
3. **保留输出路径修改、仅回滚 entrypoint**：若 entrypoint 校验逻辑有问题，可单独 revert docker-entrypoint.sh，保留 yml 的 scan-output 路径迁移（无副作用）

回滚后 CI 恢复「误报阻断」状态，需重新评审修复方案。

---

## 四、任务执行顺序与依赖

```
任务 1 (本地 Docker 验证)
   ↓ 通过
任务 2 (提交推送)
   ↓ 触发
任务 3 (CI 监控) ──失败──→ 回滚指引
   ↓ 通过
任务 4 (真实 HIGH 回归) ──可并行──
   ↓ 通过
任务 5 (文档更新)
   ↓
任务 6 (3 次稳定性监控, 持续)
```

**关键路径**：任务 1 → 2 → 3（P0，必须连续完成才能解除 develop 阻断）。
**可并行**：任务 4 可与任务 3 并行（本地 vs CI）。

---

## 五、附录：修改文件清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| [.github/workflows/kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml) | 修改 | 输出路径迁移 + exit 分流 + MEDIUM 统计 bug 修复 |
| [packages/kwarg_scanner/docker-entrypoint.sh](../../packages/kwarg_scanner/docker-entrypoint.sh) | 修改 | case 1) 加证据校验 |
| [packages/kwarg_scanner/tests/test_entrypoint_logic.py](../../packages/kwarg_scanner/tests/test_entrypoint_logic.py) | 新增 | case 1) 逻辑故障注入回归测试 |
| [docs/reports/docker_false_positive_fix_plan_20260804.md](./docker_false_positive_fix_plan_20260804.md) | 新增 | 修复方案文档 |
| [docs/reports/docker_fix_ci_regression_checklist_20260804.md](./docker_fix_ci_regression_checklist_20260804.md) | 新增 | 本清单 |
