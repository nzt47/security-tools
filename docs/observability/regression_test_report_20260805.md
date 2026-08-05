# 自动化回归测试报告：BOM/hook 拦截机制

> 归档日期：2026-08-05
> 测试对象：维护交付物（巡检脚本 + BOM/hook 回归测试）
> 测试范围：**23 个单元测试用例 + 7 个集成巡检项**
> 执行环境：Windows / Python 3.12.0 / pytest 9.0.3 / git 2.x（本地仓库 `C:\Users\Administrator\agent`）

## 一、结论摘要

| 维度 | 结果 |
|------|------|
| 单元回归（pytest） | **23/23 通过**（0 失败 / 0 跳过，耗时 1.68s） |
| 集成巡检（maintenance_check --with-hook-test） | **7/7 PASS**（BLOCK 0 / WARN 1） |
| 快速巡检（maintenance_check --json） | **pass**（BLOCK 0 / WARN 1） |
| 唯一 WARN | M6（SLACK_WEBHOOK_URL 未配置，见 `docs/observability/slack_webhook_setup_todo.md`） |
| 工作区状态 | 干净（hook 稳定性测试无残留临时文件） |

**判定：通过。** 拦截机制（ENCODING_CHECK + BOMFIX 双段）稳定，hook 六段链配置与脚本契约一致，无回归。

## 二、执行命令

```powershell
# 1. 单元回归（23 用例）
python -m pytest tests/unit/test_bom_encoding_hook.py -v

# 2. 完整集成巡检（含真实 git commit 触发六段 hook 拦截实测）
$env:TLM_HOOK_SOURCE_REPO = "C:\Users\Administrator\agent"
python scripts/maintenance_check.py --with-hook-test --quiet

# 3. 结构化结果输出（JSON）
python scripts/maintenance_check.py --json
```

## 三、Part A：单元回归 —— 23 个测试用例

测试文件：`tests/unit/test_bom_encoding_hook.py`（6 组）

### A1. BOM 契约基础（6 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_bom_constant_contract` | 两检查脚本 BOM 常量一致（EF BB BF） |
| `test_count_leading_bom_zero` | 无 BOM → 计数 0 |
| `test_count_leading_bom_one` | 单 BOM → 计数 1 |
| `test_count_leading_bom_stacked` | 叠加 BOM x3 → 计数 3（事故场景） |
| `test_is_utf8_valid_and_invalid` | UTF-8 合法性判定 |
| `test_hex_head_format` | 十六进制头部格式化 |

### A2. 检测判定规则（4 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_stacked_bom_is_utf8_but_block` | 叠加 BOM 合法 UTF-8 但必须契约拦截 |
| `test_invalid_utf8_is_block` | 非法 UTF-8 判定 |
| `test_require_bom_default_contract` | 关键契约文件清单两脚本一致（单一事实源） |
| `test_iter_ps_files_scans_scripts_and_packages` | 仅扫 .ps1/.psm1，忽略其他后缀 |

### A3. 修复公式（2 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_fix_dedupe_stacked_bom` | 去叠加 BOM：保留恰好 1 个，内容无损 |
| `test_fix_fill_missing_bom` | 补 BOM：关键契约文件缺 BOM 时前置 |

### A4. hook 模板完整性（4 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_hook_template_has_all_segments` | 五段标记（ENCODING_CHECK/BOMFIX/CI_GUARD/INVARIANT/WORKFLOW_SIM） |
| `test_hook_template_has_all_skip_switches` | 五个跳过开关 |
| `test_hook_template_prepush_invariant` | pre-push 含 INVARIANT 段 |
| `test_hook_template_encoding_command` | ENCODING_CHECK 段调用 check_ps1_encoding.py + --repo-root |

### A5. 稳定性测试契约（5 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_stability_block_markers_cover_attribution` | 归因标记覆盖两段拦截文案 |
| `test_write_stacked_bom_file_produces_double_bom` | 文件构造器产出叠加 BOM x2（事故复现） |
| `test_analyze_commit_detects_encoding_block` | ENCODING_CHECK 段拦截归因成功 |
| `test_analyze_commit_detects_bomfix_block` | BOMFIX 段拦截归因成功（标记顺序取首个） |
| `test_analyze_commit_unattributed_is_false` | 无归因标记的失败不误报为 BOM 拦截 |

### A6. 端到端回归（2 用例）— 全部 PASS

| 用例 | 覆盖点 |
|------|--------|
| `test_end_to_end_check_stacked_bom_exit_1` | 真实子进程：叠加 BOM → exit 1；修复后 → exit 0 |
| `test_end_to_end_detect_direct_attribute` | detect_direct：双检查脚本同时归因检出 |

## 四、Part B：集成巡检 —— 7 个巡检项

脚本：`scripts/maintenance_check.py`（数据源：`bom_fix_env_hardening_summary_20260805.md` §6）

| 项 | 检查内容 | 状态 | 说明 |
|----|----------|------|------|
| M1 | 环境体检（env_health_check 汇总） | **PASS** | 7 项，BLOCK 0 / WARN 1 |
| M2 | 提交前工作区核对 | **PASS** | 工作区干净 |
| M3 | BOM 回归防护（check_ps1_encoding 全仓） | **PASS** | BLOCK 0 |
| M4 | .gitignore 后台干扰产物防线段 | **PASS** | 防线段存在 |
| M5 | master 提交来源守卫 | **PASS** | 守卫存在且默认 dry-run |
| M6 | Slack 通知（SLACK_WEBHOOK_URL） | **WARN** | 步骤就绪但 secret 未配置（待办，不阻断） |
| M7 | 已知残留核对（CI_FIX_INDEX.md） | **PASS** | 无未提交修改 |

**完整模式（--with-hook-test）附加验证**：M1 内 env_health_check C6 通过 `verify_bom_hook_stability.py --iterations 2 --mode both` 真实触发 2 次 git commit，断言叠加 BOM 提交每次均被六段 hook 链稳定拦截且 HEAD 不变 → **拦截机制稳定**。

## 五、回归判定标准

- [x] 单元测试 0 失败（23/23）
- [x] 巡检 0 BLOCK（7/7，WARN 1 为已知待办）
- [x] hook 稳定性实测拦截成功（C6 / 完整模式）
- [x] 工作区无测试残留（`__bom_hook_stability_*.ps1` 未出现）

## 六、相关交付物

| 文件 | 说明 |
|------|------|
| `scripts/maintenance_check.py` | 巡检脚本（M1-M7） |
| `tests/unit/test_bom_encoding_hook.py` | 回归测试（23 用例） |
| `scripts/env_health_check.py` | 环境体检（M1 依赖） |
| `docs/observability/README_maintenance_check.md` | 交付物使用说明 |
| `docs/observability/slack_webhook_setup_todo.md` | M6 待办跟进清单 |

## 七、后续待办

- **M6**：配置 `SLACK_WEBHOOK_URL` secret → 见 `docs/observability/slack_webhook_setup_todo.md`
- 全量 tests/unit（200+ 文件）存在已知环境相关失败（chromadb/numpy 兼容、E2E 超时，CI 已分片处理），与本次交付无关，不在本报告范围

---

*报告由实际运行输出生成，数据采集时间 2026-08-05 22:33。*
