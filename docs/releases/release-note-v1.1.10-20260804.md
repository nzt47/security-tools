# Release Note — v1.1.10（2026-08-04）

> 主题：reranker CI 守卫修复 + 同类 pytest 插件兼容性收尾 + ci-guard-runner 依赖补齐 + BOM/编码修复链
> 范围：v1.1.8（1dc2aaf0）→ HEAD（含 reranker 守卫修复链与后续 CI/编码修复）
> 状态：本地全量验证通过（verify 6/6、pytest 9/9、run_ci_guard 全流程、不变量 12/12）

---

## 一、概述

本次版本覆盖三块修复，核心是**让 reranker 相关 workflow 从"持续失败"到"全绿"**，并横向清理同类兼容性问题：

1. **reranker-timeout-guard / ci-guard-runner 依赖补齐**（失败形态三层演化：exit 2 → ImportError → exit 4）
2. **同类 pytest 插件兼容性横向排查**：p0-security / intent-layer-ratio-check / daily_regression 补装插件
3. **ci-guard-runner 入口依赖缺失修复**（本轮本地模拟 CI 新发现）：`simulate_pr_merge_guard` / `safe_git_revert` 从未入库，触发必挂

另含 BOM 编码修复链（42 个 PS 文件双 BOM）、WORKFLOW_SIM 预检集成、tlm-hook-failsafe 1.1.10 版本 bump。

---

## 二、涉及 Commit（按时间序）

| Commit | 说明 |
|--------|------|
| `5b41a582` | feat(reranker)：5 个 reranker 守卫依赖文件入库（reranker_utils / detect_reranker_changes / run_ci_guard / verify_reranker_timeout_health / test_reranker_utils） |
| `ff83ff4e` | fix(reranker)：verify 脚本对齐真实实现——import `SkillReranker`（非 `ToolReranker`）、默认值取类属性、env 为 `SKILL_RERANKER_RERANK_TIMEOUT` |
| `ef53187a` | fix(ci)：reranker-timeout-guard / ci-guard-runner 补装 `pytest-timeout`（修 `--timeout` exit 4） |
| `af1b8694` | fix(ci)：补装 `pytest-asyncio`（修 `asyncio_mode=auto` exit 4）；docs：移除失效链接引用 |
| `427f0daf` | fix(ci)：auto-tag 触发发布 + workflow_dispatch input 改 string |
| `8b47b614` | fix(ci)：p0-security ×3 处 / intent-layer-ratio-check / daily_regression 补装 `pytest-asyncio`（同类问题排查修复） |
| `aedbe39b` | docs(observability)：reranker CI 守卫修复最终总结报告（commit 链路 + 同类问题排查表） |
| `08aa5994` | feat(hooks)：pre-commit 集成工作流模拟校验段（WORKFLOW_SIM） |
| `8ffe05c2` | chore(hooks)：同步 WORKFLOW_SIM 段到发布包副本 |
| `7687bdd9` | fix(ci)：simulate_ci_failure_notify.py 退出码逻辑入库 |
| `44adaebc` | docs+fix：License/Release 根因备忘录 + action-gh-release@v3 升级 + 双重 BOM 修复 |
| `29d44803` | docs(ci)：工作流模拟预检使用指南（SKIP_WORKFLOW_SIM 豁免场景 + 失效 action 扫描结论） |
| `a9db49e2` | docs(ci)：新成员 Git Hook 上手指南 + Filebeat 非 JSON 行过滤示例 |
| `c17ecce9` | release(tlm-hook-failsafe)：bump 1.1.9 + sync 期望函数列表 15→16 |
| `d9530a77` | fix(ci)：补回 check_ps1_encoding.py + 新增 fix_ps_bom.py 批量修复 BOM + 避坑指南 |
| `117a7513` | fix(ci)：补全 42 个 PS 文件 BOM + hook 集成 fix_ps_bom.py BOM 修复预检段 |
| `90728a6e` | chore(ci)：同步 hook 模板副本至 packages（含 BOMFIX 预检段） |
| `3f975a99` | docs(ci)：BOM 修复总结报告 + 团队技术博客（典型错误案例） |
| `e3d4fc17` | release(tlm-hook-failsafe)：bump 1.1.10 + v1.1.9 修复链复盘文档 |
| `a95e2dce` | test(ci)：pre-commit hook BOM 拦截稳定性自动化测试脚本 |
| `6d1d8eae` | **fix(ci)：重建 ci-guard-runner 缺失依赖 simulate_pr_merge_guard / safe_git_revert**（本轮新增） |

---

## 三、关键修复点

### 3.1 pytest.ini 插件契约（exit 4 根因）

`pytest.ini` 全局 addopts/配置依赖两个插件，**任何**读取该 ini 的 pytest 调用缺一即 exit 4：

| 配置项 | 所需插件 | 缺省报错 |
|--------|---------|---------|
| `--timeout=60 --timeout-method=thread` | `pytest-timeout` | `unrecognized arguments: --timeout=60` |
| `asyncio_mode = auto`（+ `--strict-config`） | `pytest-asyncio` | `Unknown config option: asyncio_mode` |

标准安装行（已验证）：
```yaml
pip install pytest pytest-timeout pytest-asyncio --quiet
```

**同类问题横向排查结论**：p0-security（3 处，连续 3 次 schedule 失败实证）、intent-layer-ratio-check（`|| true` 静默吞错）、daily_regression（3 处）均已修复；其余 6 个 workflow（ci.yml / coverage-ci / ci-cd / observability-ci / extension-health-check / log-perf-guard）原已含双插件，无同类问题。

### 3.2 verify 脚本 Symbol 错位（ImportError 根因）

- 错误：`from agent.tool_router_reranker import ToolReranker, _DEFAULT_RERANK_TIMEOUT`
- 真相：真实实现在 `agent/skills_mgmt/reranker.py`，类名 `SkillReranker`，`_DEFAULT_RERANK_TIMEOUT` 是**类属性**（=3.0），env 为 `SKILL_RERANKER_RERANK_TIMEOUT`
- 已排查其余 100+ 处 import：全部正确使用 `SkillReranker`，无同类错误

### 3.3 ci-guard-runner 入口依赖缺失（本轮新发现）

- 现象：本地模拟 CI（`python scripts/run_ci_guard.py --json`）报 `ModuleNotFoundError: simulate_pr_merge_guard`
- 根因：`run_ci_guard.py` 依赖 `simulate_pr_merge_guard.py` / `safe_git_revert.py`，二者**从未入库且源文件丢失**（仅剩 `__pycache__` 编译缓存）；ci-guard-runner.yml 的 paths 虽引用，但因仅在 PR + 特定文件变更时触发，此前从未暴露
- 修复：按调用契约重建两模块——
  - `simulate_pr_merge_guard.run_guard(force_fail, pytest_args, verbose)` → `{decision, exit_code, checks, blocked_reasons}`，执行 verify 6 场景 + pytest 9 用例
  - `safe_git_revert.safe_revert(target, dry_run=True)` → `{affected_files, exit_code}`，守卫流程仅 dry-run，绝不执行破坏性操作
- 验证：全流程 JSON 输出 `overall=pass/exit 0`；`--force-fail` 注入正确 blocked/exit 1；dry-run 准确列出受影响文件

### 3.4 编码修复链（BOM）

- 双 BOM 污染（EFBBBF EFBBBF）导致 PS 5.1 解析失败 → 42 个 PS 文件批量修复为单 BOM
- 新增 `fix_ps_bom.py` 批量修复工具 + `check_ps1_encoding.py` 回归 + hook BOMFIX 预检段
- 生命周期中曾出现"无痕回滚"（修复未入库被还原），本轮已恢复并加不变量监控（12 项）

---

## 四、验证记录

| 验证项 | 结果 |
|--------|------|
| `verify_reranker_timeout_health.py` | ✅ 6/6（默认/6.0/10.5/0/非法/实例隔离） |
| `pytest tests/unit/test_reranker_utils.py` | ✅ 9/9 |
| `run_ci_guard.py --json` 全流程 | ✅ detect → rollback_sim → guard_verify 全 PASS，exit 0 |
| `run_ci_guard.py --force-fail` | ✅ 正确拦截（blocked/exit 1 + blocked_reasons） |
| `safe_git_revert` dry-run | ✅ 正确列出受影响文件，无写操作 |
| pre-commit hook | ✅ 链接 0 失效、锚点 4/4、不变量 12/12 |
| reranker-timeout-guard CI（run 30916564779） | ✅ 全绿（45s） |

---

## 五、注意事项

- **版本说明**：v1.1.10 为仓库修复版本标记；`tlm-hook-failsafe` 包 psd1 同步为 1.1.10，推送 tag 将触发 publish-psgallery 自动发布 + release-docs Pages 构建
- **遗留**：p0-security / intent-layer / daily_regression 修复后的下一次 schedule 运行待实证转绿（此前 p0-security 连续 3 次失败，修复在 `8b47b614`）
- **git 竞态提示**：本仓库存在并发 git 进程现象（历史多次出现提交被还原/HEAD 漂移），重要改动请及时 push
