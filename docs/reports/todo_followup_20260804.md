# 遗留问题待办清单（2026-08-04）

> 基于 2026-08-03 总结报告 [bom_fix_links_cleanup_summary_20260803.md](./bom_fix_links_cleanup_summary_20260803.md) 的遗留问题，
> 以及 2026-08-04 对 Docker kwarg 扫描 52 HIGH 的复核结论。
> 状态：待执行

---

## 一、待办项总览

| # | 待办项 | 优先级 | 风险影响 | 状态 |
|---|--------|--------|----------|------|
| 1 | 修复 Docker kwarg 扫描误报机制（PermissionError → 误判 HIGH） | **高** | CI 永远红灯，阻断 develop 提交 | **已修复**（run 30882099762 success） |
| 2 | post-commit sync-from-source WARN（exit 1）排查 | **高** | hook 同步链路异常，契约漂移隐患 | 待执行 |
| 3 | 73 个无 BOM .ps1 编码统一决策 | 中 | PS 5.1 中文注释按 ANSI 解析，跨平台隐患 | 待决策 |
| 4 | master 3 处失效链接修复提交 | 低 | 链接预检在 master 仍会阻断 | 待提交 |
| 5 | develop 4 处「待补档」链接补全 | 低 | 文档引用缺失 | 待补档 |

---

## 二、详细说明与处理建议

### 1. Docker kwarg 扫描误报（run 30817413422，develop @ 4db85572）

**复核结论（2026-08-04）**：
- 用本地 `kwarg_scanner` 包在 develop 4db85572 的 `agent/` 子目录复现：**375 文件、52 findings，全部为 LOW**（0 HIGH / 0 MEDIUM）
- 52 项构成：
  - `safe_payload`/`safe_kwargs`/`safe`/`safe_merged` 已过滤变量标记：39 项（扫描器明确降级为 LOW）
  - 外部函数签名未知（CircuitBreaker、RateLimiter、get/post/put/delete、cls、handler 等）：12 项
  - 字典推导式含条件过滤：2 项
- **Docker 报「52 HIGH 阻断」的根因是误报链**：
  1. `docker-entrypoint.sh` 中 `OUTPUT_FILE=/project/kwarg-high-risk-report.json` 指向容器挂载点根目录，容器以非 root `scanner` 用户运行，宿主 workspace 根目录不可写 → `PermissionError: [Errno 13]`
  2. PermissionError 使 CLI 进程以 exit 1 崩溃
  3. entrypoint 的 `case $SCAN_EXIT_CODE in 1)` 分支把 exit 1 一律映射为 `reason: high_risk_detected`，导致 CI 误判阻断

**处理建议**（按优先级排序）：
- [ ] **P0** 修改 [kwarg-docker-scan.yml](../../.github/workflows/kwarg-docker-scan.yml)：`OUTPUT_FILE` 改为容器内可写路径（如 `/tmp/kwarg-high-risk-report.json`），随后在宿主读取；或挂载可写目录
- [ ] **P1** 修改 [docker-entrypoint.sh](../../packages/kwarg_scanner/docker-entrypoint.sh)：区分「exit 1 = 真有 HIGH」与「进程崩溃」——捕获 traceback 时输出 `result: error` 而非 `high_risk_detected`
- [ ] **P2** entrypoint 的扫描结果判断增加 stdout 报告解析校验：`findings_count: 52` 与 `high_risk_count` 需与报告内容一致才阻断
- [ ] **P3** 复核 `agent/` 子目录 52 项 LOW 是否值得清理（均属安全模式，非真实风险，可视情况批量加 `safe_` 前缀或忽略）

**验证方式**：修复后重新触发 Docker 扫描 run，应显示 success；若真有 HIGH 才阻断。

**修复完成记录（2026-08-04）**：
- commit `0055a3f8` 已合入 develop（8 文件：yml 输出路径迁移 + entrypoint 证据校验 + Dockerfile sed 去 CRLF + .gitattributes 强制 LF + ci-failure-notify 监听扩展 + 逻辑回归测试 + 2 文档）
- CI run 30882099762 **success**（对比前两次 run 30837696244/30837604853 均 failure，误报阻断已解除）
- 本地真实 HIGH 回归验证通过（fixture 触发 3 个 HIGH：字典字面量同名键 + 本地函数签名 **kwargs 同名参数，exit 1 阻断，high_risk_count=3）
- 三层 CRLF 防御：Dockerfile sed（镜像层）+ .gitattributes `*.sh eol=lf`（仓库层）+ 磁盘 LF（工作区层）

### 2. post-commit sync-from-source WARN（exit 1）

**现象**：worktree 环境提交时 post-commit hook 调 sync-from-source.ps1 报 WARN 且 exit 1，提交不受影响。
**可能根因**：
- worktree 路径与主工作区不同，hook 内路径硬编码（`$PSScriptRoot` 之外的相对路径）
- `TLM_HOOK_SOURCE_REPO` 环境变量在 post-commit 上下文未继承
- sync 目标文件被 git 锁占用或只读

**处理建议**：
- [ ] 复现：在干净 worktree 执行一次提交，抓取 post-commit 完整 stderr 日志
- [ ] 检查 [sync-precommit-hook.ps1 / sync-from-source.ps1](../../scripts/dev/sync_precommit_hook.ps1) 的路径解析逻辑，确认是否依赖主工作区绝对路径
- [ ] 若为环境变量未继承：在 hook 中显式加载 `.env` 或 User 级环境变量
- [ ] 长期：将 sync 目标路径改为 `git rev-parse --show-toplevel` 动态解析，消除 worktree 差异

### 3. 73 个无 BOM .ps1 编码决策

**现状**：UTF-8 无 BOM 属既有编码设计（非多 BOM 污染），未纳入修复范围。但 PS 5.1 在中文系统上按 ANSI/GBK 解析无 BOM 文件的中文注释，存在乱码/解析隐患。

**处理建议**：
- [ ] 决策：统一为「UTF-8 带 BOM」（推荐，PS 5.1 兼容）还是「保留无 BOM + 去中文注释」（改动大）
- [ ] 若统一带 BOM：编写批量脚本，用 `Get-Content -Encoding UTF8` + `[System.IO.File]::WriteAllText` 方式统一写入单 BOM，防止重复 BOM
- [ ] 更新 BOM 契约测试：将「0 BOM」文件也纳入契约基线，防止新文件随意引入多 BOM
- [ ] 在 CI 增加 PowerShell 扩展名全量扫描（.ps1/.psm1/.psd1），吸取首轮只扫 .ps1 的教训

### 4. master 3 处失效链接修复提交

**现状**：master 工作区已修复但未提交（随 a16fa4fb 保留）：
- `docs/observability/semantic_monitoring_runbook.md`（锚点改纯文件链接）
- `docs/wiki/incident_report_template.md`（占位符改反引号）
- 待提交文件清单见 [issue-template-broken-links-20260803.md](../issues/issue-template-broken-links-20260803.md)

**处理建议**：
- [ ] 下次 master 提交时合入，避免链接预检在 master 触发时再次阻断
- [ ] 提交前先本地跑 fix_broken_links.ps1 确认 diff 归零

### 5. develop 4 处「待补档」链接补全

**现状**：develop commit 950a41c5 将 4 处指向不存在文件的链接改为反引号「待补档」标注：
- `docs/DEVELOPMENT_STANDARDS_K8S_SCRIPTS.md` ×2
- `docs/HPA_CHANGELOG.md`
- `docs/MIGRATION_PORT_FORWARD_TO_IN_CLUSTER.md`

**处理建议**：
- [ ] 补全目标文档（`scripts/mock_alert_webhook.py` 用法说明、`HPA_PATROL_TEST_REPORT.md`、`RESOURCE_COST_COMPARISON.md` 对比数据）
- [ ] 或明确删除引用，避免「待补档」长期悬挂

---

## 三、CI 状态快照（2026-08-04 复核）

| Workflow | Run | 结论 | 说明 |
|----------|-----|------|------|
| tlm-hook-failsafe E2E（BOM 契约） | 30837604966（81778b0a） | ✅ success | **BOM 契约测试转绿**（PS 5.1/7 单测 + E2E 全过，日志含 `[OK] verified 15 exported functions`） |
| 云枢系统测试流程（ci.yml） | 30837696318（9c208b9a） | ❌ cancelled | 集成/安全/性能/代码质量/E2E 全绿；**3 个单测 job 60min 硬超时被取消**（`Terminate orphan process: pytest`，pytest 仍在运行） |
| 云枢系统测试流程（test.yml） | 30837696291（9c208b9a） | ❌ failure | 代码质量 job 缺 pytest-timeout（unrecognized args）；单测/集成/性能 18 个 job 超时或崩溃 |
| 关键字参数冲突扫描（Docker） | 30882099762（0055a3f8） | ✅ success | **误报已修复**（exit 0, high_risk_count=0）；真实 HIGH 回归验证通过（exit 1, HIGH=3） |

> 注：develop 最新 HEAD 为 9c208b9a（pythoncom 同步删除），云枢两个 workflow 均针对该提交。

### 云枢失败根因分析（2026-08-04 复核）

**两个云枢 run 的失败均与 pythoncom/BOM 变更无关**，是既有 CI 环境问题：

1. **test.yml「代码质量检查」job**：`pip install --timeout=120 flake8 black isort mypy pytest pytest-cov || true` + `pip install -e .[dev] --timeout=120 || true`，后者被 `|| true` 吞掉失败 → 环境缺 `pytest-timeout` → pytest.ini 的 `--timeout=60 --timeout-method=thread` 报 `unrecognized arguments` → exit 4
2. **单测/集成/性能 job 超时被取消**：单测卡在 `test_planning_executor.py::test_execute_plan_failure`（18:11:24 起 12 分钟无输出，30min job 超时取消）；ci.yml 单测 job 60min 超时被 kill，日志末尾 `Terminate orphan process: pytest`
3. **develop 与 master 的 test.yml 差异**：master 有 `--cov-fail-under=30`（缺陷#3 修复），develop 缺失，需随下次同步合入

**处理建议**：
- [ ] 将 test.yml 代码质量 job 的依赖安装改为显式安装 pytest-timeout（或去掉 `|| true`，让安装失败直接暴露）
- [ ] 排查 `test_execute_plan_failure` 卡住根因（疑似 plan 执行路径 IO/锁等待），补 pytest-timeout=30 兜底
- [ ] 将 master 的 test.yml `--cov-fail-under=30` 同步到 develop
