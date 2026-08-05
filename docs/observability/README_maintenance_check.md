# 环境维护交付物使用说明

> 2026-08-05 BOM 事故复盘后的两份自动化交付物：
> 1. **维护巡检脚本** `scripts/maintenance_check.py` — 将总结报告 §6 的 7 条维护建议固化为可定期运行的巡检
> 2. **BOM/hook 回归测试** `tests/unit/test_bom_encoding_hook.py` — 将 BOM 拦截逻辑与 hook 配置打包为重构回归护城河

相关背景文档：
- 总结报告：`docs/observability/bom_fix_env_hardening_summary_20260805.md`（§6 为 7 条建议源）
- 排查清单：`docs/GIT_OPERATION_SAFETY_GUIDE.md`（§8/§9）
- 提交来源核对：`docs/troubleshooting/commit_source_audit_20260805.md`

---

## 交付物 1：维护巡检脚本 `scripts/maintenance_check.py`

### 用途

把总结报告 §6 的 7 条维护建议自动化，定期运行即可体检环境状态，无需逐条人工核对。

| 检查项 | 内容 | 对应建议 |
|--------|------|---------|
| M1 | 环境体检（调用 `env_health_check.py` 汇总，可透传 `--with-hook-test`） | 建议 1 定期体检 |
| M2 | 提交前工作区核对（git status 未跟踪/暂存/已修改 + 最新提交） | 建议 2 提交前固定动作 |
| M3 | BOM 回归防护（调用 `check_ps1_encoding.py` 全仓扫描） | 建议 3 BOM 回归防护 |
| M4 | `.gitignore` 后台干扰产物防线段存在性 | 建议 4 后台干扰治理 |
| M5 | master 提交来源守卫（guard workflow 存在 + 灰度模式判定） | 建议 5 CI 守卫演进 |
| M6 | Slack 通知待办（`SLACK_WEBHOOK_URL` 引用状态核对） | 建议 6 Slack 配置 |
| M7 | 已知残留核对（`CI_FIX_INDEX.md` 未提交修改状态） | 建议 7 已知残留跟踪 |

### 状态分级与退出码

- 三态分级：`pass`（通过）/ `WARN`（需人工关注，不阻止）/ `BLOCK`（环境异常）
- 退出码：`0` = 无 BLOCK；`1` = 存在 BLOCK（适合接入 CI 或脚本告警）

### 用法

```powershell
# 常规巡检（几秒内完成）
python scripts/maintenance_check.py

# 完整巡检：M1 含 hook 拦截稳定性实测（会真实触发 git commit，约 2-3 分钟）
# 注意：必须先设置 TLM_HOOK_SOURCE_REPO，否则 hook 报错
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
python scripts/maintenance_check.py --with-hook-test

# 输出 JSON（供脚本/CI 解析）
python scripts/maintenance_check.py --json

# 仅输出汇总一行
python scripts/maintenance_check.py --quiet

# 指定仓库根目录
python scripts/maintenance_check.py --repo-root C:\path\to\repo
```

### 依赖脚本

| 脚本 | 作用 |
|------|------|
| `scripts/env_health_check.py` | M1 调用；C1-C7 环境体检（agitator 进程/计划任务/提交时间线/工作区污染/编码检查/hook 稳定性/核心不变量） |
| `scripts/check_ps1_encoding.py` | M3 调用；PS 文件编码契约检查（BLOCK/WARN 分级） |
| `scripts/verify_bom_hook_stability.py` | 经 env_health_check C6 调用；连续模拟真实提交验证 hook 拦截稳定性 |

---

## 交付物 2：BOM/hook 回归测试 `tests/unit/test_bom_encoding_hook.py`

### 用途

将修复后的 BOM 拦截逻辑（`check_ps1_encoding.py` / `fix_ps_bom.py`）与 hook 配置（`scripts/dev/hook_fail_safe.psm1`）打包为独立回归用例。**重构后一键验证拦截机制未被破坏**（测试 = 不易护城河）。

### 覆盖范围（6 组 23 个测试）

| 组 | 覆盖点 |
|----|--------|
| 1. BOM 契约基础 | `BOM` 常量 / `count_leading_bom` / `is_utf8` / `hex_head` |
| 2. 检测判定规则 | 叠加 BOM 是合法 UTF-8 但必须拦截；BLOCK/WARN 分级语义 |
| 3. 修复公式 | 去叠加 BOM（保留 1 个）/ 补 BOM（关键契约文件） |
| 4. hook 模板完整性 | 六段拦截链标记 + 5 个跳过开关 + pre-push INVARIANT + 编码检查命令 |
| 5. 稳定性测试契约 | 归因标记覆盖 / 叠加 BOM 文件构造 / analyze_commit 归因判定 |
| 6. 端到端回归 | 真实子进程检出叠加 BOM → exit 1；detect_direct 双脚本归因 |

### 用法

```powershell
# 运行全部
python -m pytest tests/unit/test_bom_encoding_hook.py -q

# 详细输出
python -m pytest tests/unit/test_bom_encoding_hook.py -v

# 只看某组（如端到端）
python -m pytest tests/unit/test_bom_encoding_hook.py -v -k "end_to_end"
```

### 与 hook 的契约（测试会校验，勿随意改动）

- hook 模板 `scripts/dev/hook_fail_safe.psm1` 必须包含五段标记：`ENCODING_CHECK=` / `BOMFIX=` / `CI_GUARD=` / `INVARIANT=` / `WORKFLOW_SIM=`（pre-commit 共六段，含文档链接预检）
- 五个跳过开关：`SKIP_ENCODING_CHECK` / `SKIP_BOM_FIX_CHECK` / `SKIP_CI_GUARD` / `SKIP_INVARIANT` / `SKIP_WORKFLOW_SIM`
- 两个检查脚本的 `BOM` 常量与 `REQUIRE_BOM_DEFAULT` 清单必须一致（单一事实源）
- `verify_bom_hook_stability.py` 的 `BOM_BLOCK_MARKERS` 归因顺序：编码检查未通过 → 叠加 BOM → BOM 修复预检未通过 → 待修复 → 临时文件前缀

---

## 团队日常协同工作流

```powershell
# 1. 定期巡检环境（建议每周一次，或大重构后）
python scripts/maintenance_check.py

# 2. 重构/改动 hook 或 BOM 相关脚本后，跑回归测试
python -m pytest tests/unit/test_bom_encoding_hook.py -q

# 3. 完整回归（含真实 hook 提交拦截实测，验证脚本+钩子协同）
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
python scripts/maintenance_check.py --with-hook-test

# 4. 提交前固定动作：确认工作区干净 → git add → commit（hook 自动执行六段拦截链）
```

## 注意事项

- **`TLM_HOOK_SOURCE_REPO` 必须设置**：本地 commit / push 前需 `$env:TLM_HOOK_SOURCE_REPO = "<仓库绝对路径>"`，否则 pre-commit/pre-push hook 报错。
- **`--with-hook-test` 会真实触发 git commit**：M1 内 env_health_check C6 会循环模拟"写入叠加 BOM 临时文件 → git commit"并断言被拦截。测试脚本自带清理，运行后工作区应恢复干净；如发现 `__bom_hook_stability_*.ps1` 残留，运行 `python scripts/fix_ps_bom.py --check` 定位。
- **WARN 项需人工处理**：M6（Slack webhook 未配置）为已知待办；M5 若切换为 enforce 模式会变 WARN（属灰度预期，需确认）。
- **退出码仅按 BLOCK 判定**：WARN 不阻止流程，适合接入 CI 时用 `--json` 解析。

## 修订记录

| 日期 | 变更 |
|------|------|
| 2026-08-05 | 创建：固化总结报告 §6 建议为巡检脚本 + BOM/hook 回归测试；提交 `a5894195`、`f2bae5da` |
