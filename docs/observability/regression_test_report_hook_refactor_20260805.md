# hook 重构回归测试报告（P0 公共模块合并 + P1 函数化 + P2 诊断）

- **报告日期**: 2026-08-05
- **基准提交**: `ac93aa6b`（refactor(hooks): BOM 检查合并公共模块 ps_bom_contract + run_check 函数化 + WORKFLOW_SIM 失败诊断）
- **本次新增**: `scripts/guard_bom_pollution.py` + `maintenance_check.py` M8 巡检项
- **结论**: ✅ 全绿 —— 单元测试 **24/24**、集成巡检 **8/8**（7 基线 + 1 新增）、附加验证全部通过

---

## 1. 回归范围

本次重构（ac93aa6b + 本次监控脚本）改动/新增文件：

| 文件 | 变更 | 说明 |
|---|---|---|
| `scripts/ps_bom_contract.py` | 新增 | BOM 契约单一事实源（P0 合并） |
| `scripts/check_ps1_encoding.py` | 改造 | 重导出公共模块；`--quiet` 时 BLOCK 明细始终输出 |
| `scripts/fix_ps_bom.py` | 改造 | 重导出公共模块；扫描复用 `iter_ps_files` |
| `scripts/dev/hook_fail_safe.psm1` | 改造 | `run_check` 函数封装 5 段；ENCODING_CHECK 合并 BOMFIX；quiet 失败诊断 |
| `packages/tlm-hook-failsafe/tlm-hook-failsafe.psm1` | 同步 | sync-from-source.ps1 包快照（16 导出函数 + hash 一致） |
| `scripts/verify_bom_hook_stability.py` | 改造 | 归因标记对齐合并段；`--mode` 历史兼容 |
| `tests/unit/test_bom_encoding_hook.py` | 改造 | 移除 BOMFIX=/SKIP_BOM_FIX_CHECK；新增合并守卫测试 |
| `docs/observability/README_maintenance_check.md` | 改造 | 契约描述同步四段 run_check 链 |
| `scripts/guard_bom_pollution.py` | 新增 | 受保护文件 BOM 污染监控（本次） |
| `scripts/maintenance_check.py` | 改造 | 新增 M8 巡检项（本次） |

---

## 2. 单元测试（24/24 通过，2.09s）

> 运行: `python -m pytest tests/unit/test_bom_encoding_hook.py -v --tb=short`
> 环境: Python 3.12.0 / pytest 9.0.3 / Windows 10 / `--randomly-seed=2106331741`

### 组 1：BOM 契约基础函数（6 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_bom_constant_contract` | 两个检查脚本 BOM 常量一致（契约同源） | ✅ |
| `test_count_leading_bom_zero` | 无 BOM → 0 | ✅ |
| `test_count_leading_bom_one` | 恰好 1 个 BOM → 1 | ✅ |
| `test_count_leading_bom_stacked` | 叠加 BOM x3 → 3（事故场景） | ✅ |
| `test_is_utf8_valid_and_invalid` | 中文 UTF-8 合法 / 非法字节拒绝 | ✅ |
| `test_hex_head_format` | 文件头十六进制预览格式 | ✅ |

### 组 2：检测判定规则（4 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_stacked_bom_is_utf8_but_block` | 叠加 BOM 仍是合法 UTF-8，必须靠 `n_bom>1` 拦截（非解码错误） | ✅ |
| `test_invalid_utf8_is_block` | 非法 UTF-8 → 不可修复异常 | ✅ |
| `test_require_bom_default_contract` | 关键契约文件清单两脚本一致（单一事实源） | ✅ |
| `test_iter_ps_files_scans_scripts_and_packages` | 仅扫描 `.ps1/.psm1`，忽略其他后缀 | ✅ |

### 组 3：修复公式（2 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_fix_dedupe_stacked_bom` | 去叠加：保留恰好 1 个 BOM，内容无损 | ✅ |
| `test_fix_fill_missing_bom` | 补 BOM：关键契约文件前置 BOM，内容无损 | ✅ |

### 组 4：hook 模板完整性（5 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_hook_template_has_all_segments` | 四段标记 `ENCODING_CHECK=`/`CI_GUARD=`/`INVARIANT=`/`WORKFLOW_SIM=` 齐全 | ✅ |
| `test_hook_template_has_all_skip_switches` | 四个跳过开关齐全 | ✅ |
| `test_hook_template_prepush_invariant` | pre-push 含 INVARIANT 段 | ✅ |
| `test_hook_template_encoding_command` | ENCODING_CHECK 段调用 check_ps1_encoding.py + `--repo-root` | ✅ |
| `test_hook_template_encoding_merged_no_bomfix` | **合并守卫**：`BOMFIX=`/`SKIP_BOM_FIX_CHECK` 不得复活，四段必须走 `run_check` | ✅ |

### 组 5：verify_bom_hook_stability 契约（5 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_stability_block_markers_cover_attribution` | 归因标记覆盖合并段拦截文案 | ✅ |
| `test_write_stacked_bom_file_produces_double_bom` | 稳定性测试文件构造器产出叠加 BOM x2（事故复现） | ✅ |
| `test_analyze_commit_detects_encoding_block` | ENCODING_CHECK 段拦截 → 归因成功 | ✅ |
| `test_analyze_commit_detects_merged_encoding_block` | 合并段失败行 → 归因成功（首个标记命中） | ✅ |
| `test_analyze_commit_unattributed_is_false` | 无归因标记的失败 → 不误判为 BOM 拦截 | ✅ |

### 组 6：端到端回归（2 个）

| 用例 | 验证要点 | 结果 |
|---|---|---|
| `test_end_to_end_check_stacked_bom_exit_1` | 真实子进程：叠加 BOM → exit 1；修复单 BOM → exit 0 | ✅ |
| `test_end_to_end_detect_direct_attribute` | detect_direct 对叠加 BOM 文件归因检出 | ✅ |

---

## 3. 集成巡检（8/8 通过）

> 运行: `python scripts/maintenance_check.py`（M8 新增；`--with-hook-test` 已另跑验证真实拦截）
> 结果: PASS 8/8（BLOCK 0 / WARN 2，WARN 为 M2 工作区污染与 M6 Slack 待办——均为人工确认项）

| 巡检项 | 描述 | 状态 | 明细 |
|---|---|---|---|
| M1 | 环境体检（env_health_check.py 汇总） | ✅ PASS | env_health_check: PASS 7/7（含 C6 hook 真实拦截实测 3/3） |
| M2 | 提交前工作区核对 | ⚠️ WARN | 未跟踪/已修改若干（并行会话产物，人工核对项） |
| M3 | BOM 回归防护（check_ps1_encoding 全仓） | ✅ PASS | BLOCK 0，编码契约通过 |
| M4 | .gitignore 后台干扰产物防线段 | ✅ PASS | 防线段存在 |
| M5 | master 提交来源守卫 | ✅ PASS | 默认 dry-run 灰度期 |
| M6 | Slack 通知待办 | ⚠️ WARN | secret 需人工配置 |
| M7 | 已知残留文件核对 | ✅ PASS | 残留已清除 |
| M8 | **受保护 PS 文件 BOM 污染监控**（新增） | ✅ PASS | 受保护文件清单无叠加 BOM/缺 BOM |

> **hook 拦截稳定性实测**（M1 → C6）: `verify_bom_hook_stability.py --iterations 2 --mode both` 3/3 轮真实 git commit 均被稳定拦截（合并段归因成功）。

---

## 4. 附加验证（非 pytest 部分）

| 验证项 | 方法 | 结果 |
|---|---|---|
| hook bash 语法 | Git Bash `bash -n`（pre-commit + pre-push 双模板） | ✅ 语法 OK |
| 部署一致性 | 部署态 `.git/hooks/pre-commit` 与 `Get-HookContent` 生成内容逐字节比对 | ✅ 一致 |
| run_check 功能冒烟 | 5 场景：失败含 BLOCK 行 / 失败无匹配行（兜底尾部）/ 脚本缺失跳过 / SKIP 开关 / 成功放行 | ✅ 5/5 |
| WORKFLOW_SIM 诊断 | 失败时重跑 grep 过滤 `FAIL/BLOCK/叠加 BOM/返回码=[1-9]` 定位失败场景 | ✅ 已验证 |
| guard_bom_pollution 双态 | 正常态 exit 0；构造叠加 BOM x3 → exit 1 + `[BLOCK]` 行 | ✅ 双态符合 |
| 核心不变量 | `verify_core_invariants.py --repo-root .` | ✅ 12/12 |
| 包副本同步 | `sync-from-source.ps1`（16 导出函数 + SHA256 一致） | ✅ |

---

## 5. 过程中发现并修复的问题

### 5.1 `scripts/run_l3_regression_tests.ps1` 叠加 BOM 二次复发（重要）

回归过程中 `guard_bom_pollution.py` 首次正常态校验即检出该文件被叠加 BOM x3（工作区 `EF BB BF x3` + CRLF，HEAD 为 `1 BOM + LF`）——这是 ac93aa6b 提交后几分钟内第二次被并行会话/自动提交脚本污染。

- **处置**: `git restore` 恢复 HEAD 版本，编码契约恢复 BLOCK 0。
- **根因**: 并行会话使用 `--no-verify` 或绕过 hook 直接写盘，hook 层无法拦截；需巡检层防线。
- **防护**: 本次新增 `guard_bom_pollution.py` 将其列入受保护清单（`WATCH_DEFAULT`），并入 `maintenance_check M8`——每次巡检自动抓取此类污染，防止下次复发静默入库。

### 5.2 环境噪声（非回归失败）

- `CI=true` 环境变量注入（终端基础设施），不影响测试判定。
- 工作区存在并行会话改动（CHANGELOG、agent/memory 等 16 个已修改 + 多个未跟踪 wiki 文档），不属于本次回归范围，未触碰。

---

## 6. 结论

| 维度 | 结果 |
|---|---|
| 单元测试 | 24/24（6 组全覆盖，含合并守卫防 BOMFIX 复活） |
| 集成巡检 | 8/8（7 基线 + M8 污染监控新增） |
| hook 真实拦截 | 3/3 稳定拦截 |
| 语法/部署 | bash -n 双模板 OK、部署内容一致 |
| 监控防线 | guard_bom_pollution 双态验证通过，已接入巡检 |

合并后的公共模块（`ps_bom_contract`）与函数化 hook（`run_check`）协同工作正常，监控防线已就位。
