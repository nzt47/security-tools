# §10 集成测试既有问题排查计划（2026-08-09）

> **来源**: PR #499 最终报告 [pr499_fix_summary_20260809.md](pr499_fix_summary_20260809.md) §二-10
> **性质**: 全仓库既有问题，非 PR #499 引入；Windows CI 环境特定，本地无法稳定复现
> **状态**: 待立项排查（本文件为执行计划）

---

## 一、问题清单与现状

| # | 用例 | 失败信息 | CI 平台 | 本地复现 |
|---|------|----------|---------|----------|
| S10-1 | `test_digital_life_integration.py::TestToolCallingIntegration::test_tool_calling_chat_flow` | `assert False is True` | Windows 3.10/3.11 | ✅ 通过 |
| S10-2 | `test_git_sync_e2e.py::TestGitSyncConflictE2E::test_unresolvable_conflict_returns_false` | `AttributeError: 'NoneType' object has no attribute 'strip'` | Windows 3.10/3.11 | ✅ 通过 |
| S10-3 | `test_verification_flow.py::TestVerificationFlow::test_tool_calling_schema_config` | `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x95` | Windows 3.10/3.11 | ✅ 通过 |

**已确认的关键事实**（2026-08-09 本地实测）:
- 3 个用例在本地 Windows（UTF-8 locale）**全部通过**（`3 passed`）。
- CI 上 Windows 3.12 本次通过 → 具偶发性/环境相关性。
- 失败仅出现在 Windows runner，Ubuntu 全矩阵通过。

**推断**: 失败根因大概率是 Windows 环境的 locale 编码差异（cp1252/GBK vs UTF-8）或 CI 并发干扰，而非业务逻辑缺陷。

---

## 二、根因假设（按优先级排序）

### H1（最高优先）: Windows 控制台/文件 locale 编码差异
- 证据: S10-3 明确为 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x95`（0x95 = cp1252 的 `•`），S10-2 的 `'NoneType' .strip()` 也符合 git 输出被 locale 编码污染的形态。
- 机制: Windows runner 默认 stdout/stderr 编码 cp1252；当脚本以 `text=True` 读子进程输出或读文件时，若内容为 GBK/cp1252 字节流按 utf-8 解码即报错，或返回含异常字符的字符串。
- 验证: 在 CI 侧给相关 job 加 `PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（与 §8 同款修复），观察是否转绿。

### H2: git 子进程输出解析健壮性不足
- 证据: S10-2 失败点 `git_sync.py` 内多处 `result.stdout.strip()` / `_parse_conflicts`。
- 机制: 冲突场景下 git 输出带非 ASCII（中文文件名/commit 信息），Windows locale 下 `subprocess.run(text=True)` 默认按 locale 解码，产生乱码或 None。
- 验证: 在 `GitSync._run_git` 显式 `encoding="utf-8", errors="replace"`；本地构造中文文件名冲突场景回归。

### H3: 测试 mock 依赖时序/并发干扰
- 证据: S10-1 `assert False is True`（`result["success"]` 为 False），本地同代码通过。
- 机制: `DigitalLife.process()` 在 mock 环境下偶发走未预期分支（如模板匹配/语义层短路），可能与测试执行顺序或共享单例状态有关（同 `defect_tracking_20260809.md` 缺陷 3）。
- 验证: 隔离运行 + 加 `--forked` / 重跑多轮；检查 `_v2_lifetrace` 与 `_call_llm_v2` 分支条件。

### H4: 环境变量/配置文件缺失
- 证据: S10-3 涉及 `config.Config`（`agent/tool_calling.py:136` `from config import Config`）。
- 机制: Windows job 若缺少某 env（如 `.env` 相关变量），`Config.get()` 抛异常被 except 吞掉走默认值，导致 `hasattr` 断言失败或配置读取路径差异。
- 验证: 对比 CI 日志中 Config 初始化告警；本地 `PYTHONUTF8=1` 模拟。

---

## 三、排查步骤（执行顺序）

### 阶段 1: 编码环境对齐（低成本，预计可解决 H1/H4 大部分）
1. 在 `test.yml` 集成测试 job（Windows 分支）加环境变量:
   ```yaml
   PYTHONUTF8: "1"
   PYTHONIOENCODING: "utf-8"
   ```
2. 重新触发 CI，观察 S10-2/S10-3 是否转绿。
3. 若转绿 → 判定 H1 成立，收尾：为 `GitSync._run_git` 与 `Config` 文件读取补显式 UTF-8（防回归）。

### 阶段 2: git 子进程输出显式编码（针对 S10-2）
1. 定位 `agent/skills_mgmt/git_sync.py` 所有 `subprocess` 调用点（`_run_git` 及直接调用）。
2. 统一改为:
   ```python
   subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace")
   ```
3. 本地构造中文文件名冲突 e2e 场景回归（`git_env` fixture 已支持中文名）。

### 阶段 3: 工具调用流程稳定性（针对 S10-1）
1. 隔离运行 S10-1 连续 20 次，统计偶发率（本地 + CI）。
2. 若 CI 偶发而本地稳定 → 检查测试执行顺序依赖（`--order` / 共享 fixture）。
3. 审查 `DigitalLife.process()` 中 `_v2_lifetrace`、模板匹配、语义层匹配的短路条件，确认 mock 环境确定性。
4. 若为时序问题 → 参考 `defect_tracking_20260809.md` 缺陷 3 的隔离方案（模块级注册清理 fixture）。

### 阶段 4: 编码边界统一（防回归，收尾）
1. 全仓库搜索 `subprocess.run` + `text=True` 但无 `encoding=` 的调用（S10-2 同源风险面）。
2. 全仓库搜索 `open(..., "r")` 读文本未指定 `encoding="utf-8"` 的调用（S10-3 同源风险面）。
3. 统一在 CI workflow 顶层加 `PYTHONUTF8=1`（覆盖所有 job，与 §8 方案一致）。

---

## 四、验收标准

- [ ] S10-2/S10-3 在 Windows 3.10/3.11 连续 3 次 CI 全绿
- [ ] S10-1 隔离重跑 20 次无偶发失败（或已定位根因并修复）
- [ ] 全仓库 `subprocess`/`open` 编码显式化扫描结果为 0 处遗漏
- [ ] 无新引入的回归（Ubuntu 全矩阵保持绿）

---

## 五、相关文件索引

| 用途 | 路径 |
|------|------|
| 失败用例 1 | `tests/integration/test_digital_life_integration.py`（`TestToolCallingIntegration::test_tool_calling_chat_flow`） |
| 失败用例 2 | `tests/integration/test_git_sync_e2e.py`（`TestGitSyncConflictE2E::test_unresolvable_conflict_returns_false`） |
| 失败用例 3 | `tests/integration/test_verification_flow.py`（`TestVerificationFlow::test_tool_calling_schema_config`） |
| 疑似根因 1 | `agent/skills_mgmt/git_sync.py`（`_run_git`/`pull`/`_parse_conflicts`） |
| 疑似根因 2 | `agent/tool_calling.py`（`ToolCallingService.__init__` 读 Config） |
| 疑似根因 3 | `config.py`（全局配置加载） |
| 同源缺陷参考 | `docs/troubleshooting/defect_tracking_20260809.md`（缺陷 3 单例污染） |
