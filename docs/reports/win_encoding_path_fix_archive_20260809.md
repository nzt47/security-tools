# Windows 编码与跨盘符路径问题修复归档（2026-08-09）

> **生成时间**: 2026-08-09
> **适用范围**: PR #499（release/v1.2.0 → master，已合并）配套的 Windows CI 稳定化修复
> **文档用途**: 归档编码问题（§7/§8）与跨盘符路径问题的根因、修复、验证记录，供后续维护参考

---

## 一、问题全景

PR #499 最终报告中，Windows CI 存在两类环境问题：

| 类别 | 报告章节 | 症状 | 状态 |
|------|----------|------|------|
| 编码问题 | §7（脚本）+ §8（单元测试） | `UnicodeEncodeError: 'charmap'/'locale' codec` | ✅ 已修复（2 commit） |
| 跨盘符路径问题 | §二（遗留，本批修复） | `ValueError: path is on mount 'C:', start on mount 'D:'` | ✅ 已修复（1 commit） |
| 集成测试编码/时序问题 | §10 | `UnicodeDecodeError` / `AttributeError` / `assert False` | ⏳ 排查计划已立项（另见排查计划文档） |

---

## 二、编码问题修复（§7/§8）

### 2.1 根因

Windows 控制台/CI runner 默认 locale 编码为 cp1252（或 GBK），无法表示中文：

1. **§7**: `scripts/parse_ci_l2_report.py` 打印中文（`方案: ...`）→ stdout 编码失败
2. **§8**: 单元测试写中文（`open()` 默认 locale 编码）+ pytest 输出中文 docstring → 编码失败
3. **§8 深层**: 修复 §8 表面用例后，`--maxfail=5` 掩盖的 5 个 `test_digital_life_comprehensive` 用例暴露——根因是 `strftime("%Y年%m月%d日")` 在 Windows C locale 下无法编码中文格式

### 2.2 修复 commit

#### commit `8ad88f9a` — 表面编码修复

| 文件 | 改动 |
|------|------|
| `scripts/parse_ci_l2_report.py` | `main()` 入口 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")` |
| `.github/workflows/test.yml` | 单元测试步骤设 `PYTHONUTF8=1`（同时覆盖 stdio 与 `open()` 默认编码） |
| `tests/unit/test_system_tools_core.py` | `test_write_file_backup` 的 `open()` 显式 `encoding="utf-8"` |

#### commit `b22de443` — strftime 中文格式根治（7 处）

| 文件 | 改动 |
|------|------|
| `agent/orchestrator/orchestrator.py` ×3 | `datetime.now().strftime("%Y年%m月%d日")` → f-string 数字拼接 |
| `agent/orchestrator/prompt_builder.py` ×1 | 同上 |
| `agent/server_routes/routes_chat.py` ×1 | `%Y年%m月%d日 %H:%M` → 年月日 f-string + `%H:%M` |
| `app_server.py` ×2 | 同上 |

**为什么用 f-string 而非 strftime**：`strftime` 的中文格式串在 Windows 下依赖 C locale 编码，不可控；f-string 拼接为纯 Python 层操作，任何平台行为一致。

### 2.3 验证

- 本地 `py_compile` 全部通过
- **CI 全矩阵验证**（head `b22de443`）:
  - 性能测试 Ubuntu + Windows × 3 版本：**6/6 全绿**
  - Ubuntu 单元/集成测试：全绿
  - Windows 单元测试：原 5 个 + 5 个 strftime 编码失败 → **编码失败清零**
  - Windows 剩余失败均为跨盘符路径问题（下一节）或 §10 既有问题

---

## 三、跨盘符路径问题修复

### 3.1 根因

Windows runner 上 pytest `tmp_path` 位于 `C:` 盘，而仓库 checkout 在 `D:` 盘。脚本内 `os.path.relpath()` 跨盘符时抛：

```
ValueError: path is on mount 'C:', start on mount 'D:'
```

触发点（均为被 `--maxfail=5` 掩盖的既有测试）：
- `test_check_circular_deps.py`（`extract()` 内 `os.path.relpath(filepath)`）
- `test_ci_guard_fix_regression.py`（`publish_fix_to_docs.py` 内 3 处 `os.path.relpath(index_path, ROOT)`）

### 3.2 修复 commit `2966105a`

| 文件 | 改动 |
|------|------|
| `scripts/check_circular_deps.py` | `extract()`: `relpath` 抛 `ValueError` 时降级为 `os.path.basename(filepath)` |
| `scripts/publish_fix_to_docs.py` | 新增 `_safe_relpath()` 辅助函数（跨盘符回退绝对路径），替换 3 处 `relpath` 调用 |

**降级策略说明**：
- `extract()` 的模块名仅作集合 key（测试不硬编码其值）→ 降级 basename 足够
- `publish_fix_to_docs.py` 的路径仅用于打印/`git add` → 跨盘符回退绝对路径（真实推送场景不会跨盘符，仅测试触发）

### 3.3 验证

- 本地模拟跨盘符：`_safe_relpath('C:\\...', 'D:\\repo')` → 正确回退绝对路径 ✅
- 本地模拟 `extract()` 跨盘符输入 → 不再抛 ValueError，正确解析导入 ✅
- 两个测试文件本地全量：**29 passed** ✅

---

## 四、修复后 CI 状态（head `2966105a` 前序 `b22de443`）

| 项 | 状态 |
|----|------|
| 性能测试（Ubuntu+Windows × 3 版本） | ✅ 全绿 |
| Ubuntu 单元/集成测试 | ✅ 全绿 |
| Windows 单元测试编码失败 | ✅ 清零 |
| Windows 单元测试跨盘符失败 | ✅ 修复（commit 2966105a，推送 release/v1.2.0） |
| 三项 CI 门禁（边界/漂移/Gitleaks） | ✅ 通过 |
| Windows 集成测试 §10 问题 | ⏳ 既有，排查计划已立项 |

---

## 五、后续待办

1. **跨盘符修复进入 master**：commit `2966105a` 已推送 release/v1.2.0，但 PR #499 已合并 → 需确认以新 PR 或 cherry-pick 方式并入 master。
2. **§10 集成测试**：按 [integration_s10_troubleshooting_plan_20260809.md](integration_s10_troubleshooting_plan_20260809.md) 执行排查（H1 编码对齐为最高优先）。
3. **防回归扫描**：按排查计划阶段 4，全仓库扫描 `subprocess`/`open` 无显式 UTF-8 的调用点。

---

## 六、关联文档

| 文档 | 说明 |
|------|------|
| [pr499_fix_summary_20260809.md](pr499_fix_summary_20260809.md) | PR #499 完整修复总结（§7/§8 编码、§10 待处理） |
| [integration_s10_troubleshooting_plan_20260809.md](integration_s10_troubleshooting_plan_20260809.md) | §10 集成测试排查计划 |
| `docs/troubleshooting/defect_tracking_20260809.md` | 单例污染缺陷跟踪（§9 参考） |
