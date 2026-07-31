# CI 健康度看板更新失败排查清单

> **故障时间**：2026-07-29（首次触发 update-ci-dashboard job 后）
> **触发场景**：commit `a78324d8` 推送后，update-ci-dashboard job 未执行，看板趋势行未追加
> **影响范围**：`docs/dashboards/ci_health_dashboard.md` 趋势记录中断
> **来源**：v1.2.1-fix-secure-manager-return 后续看板自动化验证

---

## 一、诊断结论（分层根因）

### 第一层根因：上游 unit-tests job 卡住 16 小时

| 项 | 证据 |
|----|------|
| 卡住的 run | `30377427290`（commit `9c53ae88`，2026-07-28T16:16:06Z 创建） |
| 卡住的 job | `单元测试 (Python 3.10)` / `3.11` / `3.12` 三个 matrix job 全部 in_progress |
| 卡住的 step | "运行单元测试"（2026-07-28T16:18:30Z 开始，至今未结束） |
| 其他 job 状态 | 安全扫描/集成测试/性能测试/E2E/代码质量 全部成功 |
| 卡住时长 | ~16 小时（截至 2026-07-29 诊断时） |

**根因推断**：pytest 挂起，与 project_memory 经验吻合：
- `8661 个测试累积导致 'RuntimeError: can't start new thread' INTERNALERROR`
- `--timeout-method=signal 用 SIGALRM 信号做超时检测`，但 signal **无法中断 C 扩展调用 / join 阻塞**
- 某个测试可能卡在 C 扩展（sentence_transformers / chromadb）的阻塞调用上，signal 超时失效

### 第二层根因：a78324d8 的 CI run 未创建

| 项 | 证据 |
|----|------|
| 推送状态 | ✅ 成功（远程 master HEAD = `a78324d8`） |
| run 创建状态 | ❌ `gh api .../runs?head_sha=a78324d8` 返回 0 个 run |
| workflow 状态 | ✅ active（id 303282038 / 303453698） |
| 触发条件 | ✅ ci.yml `on.push.branches` 含 master |

**根因推断**：9c53ae88 的 in_progress run 长期占用，GitHub Actions 对同 workflow 同分支的 in_progress run 存在去重/阻塞机制，导致新 push 未创建 run（非标准行为，需 GitHub 侧确认；也可能是 runner 资源耗尽导致 run 创建延迟）。

### 第三层：update-ci-dashboard job 无机会执行

因 `needs: unit-tests` 且 unit-tests 永不完成，update-ci-dashboard 永远不会触发。即使 a78324d8 的 run 创建了，也会被卡住的 unit-tests 阻塞。

---

## 二、update-ci-dashboard job 自身排查清单（预防性）

> 即使上游 unit-tests 正常，本 job 仍可能因以下问题失败。修复卡住 run 后，按此清单逐项验证。

### A. GITHUB_TOKEN 权限（重点）

| 检查项 | 期望值 | 验证命令 / 位置 | 失败现象 |
|--------|--------|-----------------|----------|
| job 级 `permissions` | `contents: write` | [.github/workflows/ci.yml:260-262](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L260-L262) | `git push` 返回 403 FORBIDDEN `remote: Permission to nzt47/security-tools.git denied to github-actions[bot]` |
| repo 级 token 权限 | Settings → Actions → General → Workflow permissions = "Read and write" | GitHub UI | 同上 403 |
| checkout token | `token: ${{ secrets.GITHUB_TOKEN }}` | [ci.yml:269](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L269) | checkout 用默认只读 token，push 时凭证不足 |
| token 范围 | GITHUB_TOKEN 默认含 `contents: write` 仅对 job 显式声明后生效 | — | 隐式继承时可能被 repo 默认只读覆盖 |

**修复**：
1. 确认 ci.yml job 级 `permissions: contents: write`（已配置 ✅）
2. GitHub repo Settings → Actions → General → Workflow permissions → 勾选 "Read and write permissions"
3. 若仍 403，检查 org 级（若 repo 属于 org）限制

### B. Artifact 名称匹配（重点）

| 检查项 | 期望值 | 验证位置 | 失败现象 |
|--------|--------|----------|----------|
| 上传 artifact 名 | `test-results-unit-py${{ matrix.python-version }}` | [ci.yml:245](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L245) | — |
| 下载 artifact 名 | `test-results-unit-py3.10`（硬编码） | [ci.yml:274](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L274) | `Unable to find any artifact with name: test-results-unit-py3.10` |
| matrix 版本对齐 | unit-tests matrix 含 `'3.10'` | [ci.yml:131](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L131) | 若 matrix 改名（如 `'3.10.x'`），下载名不匹配 |
| artifact 路径还原 | `path: test-results` + `merge-multiple: true` | [ci.yml:277-278](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L277-L278) | 脚本读 `test-results/junit.xml` 找不到文件 |
| artifact 保留期 | `retention-days: 30`（足够） | [ci.yml:247](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L247) | 若过期，下载失败 |

**修复**：
1. 确认 matrix `python-version` 含 `'3.10'`（精确字符串匹配）
2. 下载名与上传名拼接逻辑一致：`test-results-unit-py3.10`
3. 若 matrix 调整，同步更新下载名（或用 `matrix.python-version` 变量，但下载 job 非 matrix，需硬编码）

### C. if 触发条件

| 检查项 | 期望值 | 验证位置 | 失败现象 |
|--------|--------|----------|----------|
| 分支条件 | `github.ref == 'refs/heads/main' \|\| github.ref == 'refs/heads/master'` | [ci.yml:260](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L260) | 推 master 不触发（已修正 ✅） |
| 事件条件 | `github.event_name == 'push'` | 同上 | PR / schedule 不触发（设计如此） |
| needs 依赖 | `needs: unit-tests` | [ci.yml:258](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L258) | unit-tests 失败时本 job 跳过（needs 默认 `success`，可改 `always()`） |

**潜在改进**：若希望 unit-tests 失败也更新看板（记录失败趋势），改 `needs: unit-tests` → `needs: unit-tests` + `if: always() && (...)`。当前设计 unit-tests 失败则不更新，符合"仅记录成功合入"语义。

### D. 脚本依赖与占位行匹配

| 检查项 | 期望值 | 验证方式 | 失败现象 |
|--------|--------|----------|----------|
| junit.xml 存在 | `test-results/junit.xml` | artifact 下载后 `ls test-results/` | 脚本输出 `[dashboard] junit.xml 不存在` |
| junit.xml schema | `<testsuites><testsuite tests=...>` | pytest --junitxml 标准 | 脚本输出 `[dashboard] 未找到 testsuite 元素` |
| 看板文件存在 | `docs/dashboards/ci_health_dashboard.md` | checkout 后 `ls docs/dashboards/` | 脚本输出 `[dashboard] 看板文件不存在` |
| 占位行正则 | `\| YYYY-MM-DD \| \`<sha7>\` \| — \| ...` | 看板第二节存在该行 | 脚本输出 `[dashboard] 未找到占位行` |
| Python 版本 | ≥ 3.10（用 `dict | None` 语法） | ci.yml setup-python 3.10 | `SyntaxError: invalid syntax` |

**修复**：
1. 本地验证脚本：`python scripts/update_ci_health_dashboard.py --junit <test.xml> --dashboard <test.md>`（已验证 ✅）
2. 占位行被误删时，看板第二节需保留模板行（见 [看板第二节](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md) 占位行）

### E. git 提交推送

| 检查项 | 期望值 | 验证位置 | 失败现象 |
|--------|--------|----------|----------|
| git config user | `github-actions[bot]` + noreply email | [ci.yml:306-307](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L306-L307) | commit author 为 runner 用户 |
| 提交跳过 CI | `[skip ci]` in message | [ci.yml:313](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L313) | 递归触发 CI（无限循环） |
| fetch-depth | `0`（完整历史，避免 push 落后） | [ci.yml:267](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L267) | `! [rejected] master -> master (non-fast-forward)` |
| 空提交保护 | `git diff --staged --quiet` 检查 | [ci.yml:310](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L310) | 无变更时 `nothing to commit` 报错 |
| 并发推送冲突 | 无 concurrency 配置 | ci.yml 顶部 | 多个 run 同时 push 看板导致冲突（低概率） |

---

## 三、解决方案（按优先级）

### 步骤 1：取消卡住的 9c53ae88 run（释放资源）

```bash
# 取消卡住 16 小时的 run
gh run cancel 30377427290

# 验证 run 状态变为 cancelled
gh run view 30377427290 --json status,conclusion
```

**风险**：丢失 9c53ae88 unit-tests 的部分日志（已通过 gh api 获取到 step 级状态，关键信息已留存）。其他已成功 job 的日志仍可在 GitHub UI 查看。

### 步骤 2：重新触发 a78324d8 的 CI

```bash
# 方法 A：手动 rerun（若 GitHub 已创建 run 但 queued）
gh run list --workflow=ci.yml --limit 3

# 方法 B：空推送触发（若 run 未创建）
git commit --allow-empty -m "ci: 触发 a78324d8 的 CI run" 
git push origin master

# 方法 C：workflow_dispatch（需 ci.yml 配置 workflow_dispatch 触发器）
gh workflow run ci.yml --ref master
```

### 步骤 3：定位 unit-tests 卡住的测试（治本）

```bash
# 在本地复现 CI 环境（SKILLS_OFFLINE=1 + Linux 行为模拟）
# 用 -x 在首个失败/挂起时停止 + 超时
SKILLS_OFFLINE=1 pytest tests/unit/ -x --timeout=60 --timeout-method=thread -v 2>&1 | tee pytest_run.log

# 重点排查 memory 中已知的 C 扩展挂起点：
#   - sentence_transformers 导入（SKILLS_OFFLINE 应已 patch）
#   - chromadb（Windows 不兼容，Linux 可能有其他问题）
#   - sqlite-vec 扩展加载
#   - multiprocessing.Process 子进程未 terminate
```

### 步骤 4：验证 update-ci-dashboard job

```bash
# a78324d8 的 CI 跑完后，检查 update-ci-dashboard job
gh run list --workflow=ci.yml --commit=a78324d8 --limit 1
gh run view <run-id> --json jobs --jq '.jobs[] | select(.name == "更新 CI 健康度看板") | {status, conclusion}'

# 查看看板是否追加趋势行
git pull origin master
git diff HEAD~1 docs/dashboards/ci_health_dashboard.md
```

---

## 四、预防措施

### 4.1 unit-tests 卡死预防

| 措施 | 实现位置 | 状态 |
|------|----------|------|
| `--timeout=60 --timeout-method=signal` | [ci.yml:169-170](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L169-L170) | ✅ 已配置（但 signal 无法中断 C 扩展） |
| job 级超时 | ci.yml unit-tests job 加 `timeout-minutes: 30` | ⚠️ 未配置（建议添加） |
| SKILLS_OFFLINE=1 patch 重量级模块 | [ci.yml:155](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml#L155) | ✅ 已配置 |
| 卡死检测 cron | 新增独立 workflow 检测 in_progress > 2h 的 run 并告警 | ⚠️ 未实现 |

**建议添加 job 级超时**（防止单个 job 卡死 16 小时）：
```yaml
unit-tests:
  name: 单元测试 (Python ${{ matrix.python-version }})
  runs-on: ubuntu-latest
  timeout-minutes: 45  # 【不易】硬超时兜底，防止 signal 无法中断的 C 扩展挂起
  strategy:
    ...
```

### 4.2 看板自动化健壮性

| 措施 | 状态 |
|------|------|
| 脚本失败不阻塞 CI（exit 0） | ✅ 已实现 |
| 占位行不存在时安全跳过 | ✅ 已实现 |
| 无 junit.xml 时跳过 | ✅ 已实现 |
| unit-tests 失败也更新看板（记录失败趋势） | ⚠️ 当前不更新（needs 默认 success），可改 `if: always()` |

---

## 五、诊断命令速查

```bash
# 1. 查最近 CI runs
gh run list --workflow=ci.yml --limit 5

# 2. 查指定 commit 的 run
gh api "repos/nzt47/security-tools/actions/runs?head_sha=<full-sha>" --jq '.workflow_runs | length'

# 3. 查 run 的 job 状态
gh run view <run-id> --json jobs --jq '.jobs[] | "\(.name) | \(.status) | \(.conclusion // "—")"'

# 4. 查 job 的 step 级状态
gh api "repos/nzt47/security-tools/actions/runs/<run-id>/jobs" --jq '.jobs[] | select(.name | contains("3.10")) | .steps[] | "\(.name) | \(.status) | \(.conclusion // "—")"'

# 5. 取消卡住的 run
gh run cancel <run-id>

# 6. 查看 job 日志（仅 completed job 可用）
gh api "repos/nzt47/security-tools/actions/jobs/<job-id>/logs"

# 7. 检查 workflow 启用状态
gh api "repos/nzt47/security-tools/actions/workflows" --jq '.workflows[] | {name, state}'
```

---

## 六、相关文档

- 看板模板：[`docs/dashboards/ci_health_dashboard.md`](file:///c:/Users/Administrator/agent/docs/dashboards/ci_health_dashboard.md)
- 自动更新脚本：[`scripts/update_ci_health_dashboard.py`](file:///c:/Users/Administrator/agent/scripts/update_ci_health_dashboard.py)
- CI 配置：[`.github/workflows/ci.yml`](file:///c:/Users/Administrator/agent/.github/workflows/ci.yml)
- 同类故障手册：[`docs/troubleshooting/ci_env_config_mock_runbook.md`](file:///c:/Users/Administrator/agent/docs/troubleshooting/ci_env_config_mock_runbook.md)
- 历史经验：project_memory 中"8661 测试累积 + signal 超时无法中断 C 扩展"条目

---

## 七、变更日志

| 日期 | 事件 | 操作人 |
|------|------|--------|
| 2026-07-29 | 首次诊断 a78324d8 看板未更新；定位 9c53ae88 卡住 16h 为根因；生成本排查清单 | Yi-Jing Coding Agent |
