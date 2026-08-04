# Reranker CI 守卫修复最终总结报告（2026-08-04）

> 范围：reranker-timeout-guard / ci-guard-runner workflow 从"持续失败"到"全绿"的完整修复链路，
> 及同类的 pytest 插件兼容性问题排查结果。
> 状态：✅ 全绿（reranker-timeout-guard run 30916564779：verify 6/6 + 单元测试 9/9）

---

## 一、问题背景

`reranker-timeout-guard` workflow 在 CI 中连续失败，失败形态随修复进度分三层演化：

| 阶段 | 失败形态 | 根因 |
|------|---------|------|
| 1 | exit 2 | 远端缺 `verify_reranker_timeout_health.py`（5 个 reranker 依赖文件未入库） |
| 2 | ImportError | verify 脚本从 `agent.tool_router_reranker` 导入 `_DEFAULT_RERANK_TIMEOUT`——该符号实际在 `agent/skills_mgmt/reranker.py`，且是**类属性**、类名是 `SkillReranker` |
| 3 | exit 4 ×2 | pytest.ini addopts 的 `--timeout` / `asyncio_mode=auto` 需要 `pytest-timeout` / `pytest-asyncio` 插件，CI 裸装 `pytest` 导致配置项无法识别 |

---

## 二、涉及 Commit（按时间序）

| Commit | 说明 |
|--------|------|
| `5b41a582` | feat(reranker)：5 个 reranker 守卫依赖文件入库（reranker_utils / detect_reranker_changes / run_ci_guard / verify_reranker_timeout_health / test_reranker_utils） |
| `ff83ff4e` | fix(reranker)：verify 脚本对齐真实实现——import `SkillReranker`（非 `ToolReranker`）、默认值取类属性、修正 env `SKILL_RERANKER_RERANK_TIMEOUT` |
| `ef53187a` | fix(ci)：reranker-timeout-guard / ci-guard-runner 补装 `pytest-timeout`（修复 `--timeout` 无法识别） |
| `af1b8694` | fix(ci)：同上补装 `pytest-asyncio`（修复 `asyncio_mode=auto` 无法识别）；docs：移除失效链接引用 |
| `057b2223` | fix(ci)：pytest-timeout 安装（rebase 后并入，含上轮待提交的 kwarg-docker-scan 修复——被 hook 自动暂存带入，内容合法） |
| `8b47b614` | fix(ci)：p0-security ×3 处 / intent-layer-ratio-check / daily_regression 补装 `pytest-asyncio`（同类问题排查修复） |

## 三、关键修复点

### 3.1 verify 脚本 import 错误（阶段 2）
- **错误**：`from agent.tool_router_reranker import ToolReranker, _DEFAULT_RERANK_TIMEOUT`
- **真相**：真实实现在 `agent/skills_mgmt/reranker.py`，类名 `SkillReranker`，`_DEFAULT_RERANK_TIMEOUT` 是**类属性**（`= 3.0`），env 为 `SKILL_RERANKER_RERANK_TIMEOUT`
- **修复**：`from agent.skills_mgmt.reranker import SkillReranker`；`_DEFAULT_RERANK_TIMEOUT = SkillReranker._DEFAULT_RERANK_TIMEOUT`
- 已排查其余 100+ 处 `from agent.skills_mgmt.reranker import`：全部正确使用 `SkillReranker`，无同类错误

### 3.2 pytest.ini 插件依赖（阶段 3）
`pytest.ini` 全局 addopts/配置依赖两个插件，**任何**读取该 ini 的 pytest 调用缺一即 exit 4：
- `--timeout=60 --timeout-method=thread` → 需 **pytest-timeout**
- `asyncio_mode = auto`（配合 `--strict-config`）→ 需 **pytest-asyncio**（报 `Unknown config option: asyncio_mode`）

标准安装行（已验证）：
```yaml
pip install pytest pytest-timeout pytest-asyncio --quiet
```

### 3.3 顺带修复的环境问题
- **双 BOM 批量污染**：16 个 ps1/psm1/psd1 首行被写成双 BOM（EFBBBF EFBBBF），PS 5.1 解析失败 → 字节级修复为单 BOM
- **死链**：`docs/reports/bom_fix_links_cleanup_summary_20260803.md` 引用已丢失的 `ci_failure_analysis_yunshu_test_20260803.md` → 移除失效引用
- **并发 git 进程**：会话中出现 HEAD 被 detach 到旧提交并还原工作区（reflog 可见 `reset` + `checkout c4594e97`）→ 已恢复 master 并重建改动，最终 `HEAD == master == origin/master`

---

## 四、同类问题排查结果（其他 workflow）

| Workflow | 安装行 | 状态 | 结论 |
|----------|--------|------|------|
| **p0-security.yml**（3 处） | `pytest pytest-cov pytest-mock pytest-timeout` / `pytest pytest-timeout` ×2 | 🔴 连续 3 次 schedule 失败 | 同 asyncio_mode 错误 → **已修复** `8b47b614` |
| **intent-layer-ratio-check.yml** | 仅 `pytest` | 🟡 步骤带 `\|\| true` 静默吞错 | 测试从未真正执行 → **已修复** `8b47b614` |
| **daily_regression.yml**（3 处） | `pytest pytest-cov` | 🟡 潜在（当前未触发该 job） | 缺 timeout+asyncio → **已修复** `8b47b614` |
| ci-guard-runner / reranker-timeout-guard | — | 🟢 | 已修复 |
| ci.yml / coverage-ci / ci-cd / observability-ci / extension-health-check / log-perf-guard | — | 🟢 | 均含双插件 |
| l3-docker-tests | 容器内 | 🟢 | 镜像经 requirements-dev.txt 含 pytest-asyncio |
| 本地 hook（git_precommit_check/precheck_docs） | — | 🟢 | 本地 venv 已含插件，无 CI 兼容问题 |

---

## 五、当前状态与验证

- reranker-timeout-guard ✅ **全绿**：run 30916564779，45s，verify 6/6 + pytest 9/9
- ci-guard-runner：已同步补插件，待 PR 场景验证
- master 分支与远端完全同步（`origin/master..master` 为空，1097 个提交），无未推送改动
