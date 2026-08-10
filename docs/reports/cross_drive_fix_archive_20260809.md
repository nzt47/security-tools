# Windows 跨盘符路径问题修复 — 最终归档总结（2026-08-09）

> **生成时间**: 2026-08-09
> **适用范围**: Windows CI 跨盘符 `os.path.relpath` ValueError 修复全流程（release/v1.2.0 → master）
> **文档用途**: 归档跨盘符修复的根因、变更点、验证结果与分支同步记录，作为本次修复的最终闭环文档

---

## 一、问题全景

| 项 | 内容 |
|----|------|
| 症状 | `ValueError: path is on mount 'C:', start on mount 'D:'` |
| 触发条件 | Windows runner 上 pytest `tmp_path` 位于 `C:` 盘，仓库 checkout 在 `D:` 盘，`os.path.relpath()` 跨盘符即抛异常 |
| 触发点 | `scripts/check_circular_deps.py` `extract()`；`scripts/publish_fix_to_docs.py` 内 3 处 `relpath` 调用 |
| 引入阶段 | PR #499 修复过程中被 `--maxfail=5` 掩盖的既有失败（修复编码问题后暴露） |
| 状态 | ✅ 已修复（1 commit × 2 分支同步） |

---

## 二、修复完整过程

```
[1] 根因确认
    Windows runner: tmp_path(C:) vs repo(D:) → relpath ValueError
        ↓
[2] 编码问题修复（commit 8ad88f9a / b22de443，详见编码归档）
    §7/§8 编码失败清零后，跨盘符失败暴露
        ↓
[3] 跨盘符修复（release/v1.2.0 commit 2966105a）
    2 文件、17+/4-，本地模拟 + 29 passed 验证
        ↓
[4] release/v1.2.0 CI 验证通过 → PR #499 合并 master
        ↓
[5] cherry-pick 2966105a → master d447bef8（内容一致，仅 SHA 不同）
    已推送 origin/master（7d128414..d447bef8）
        ↓
[6] master 全量 CI 复验（run 31323466698, head 23f9b64e）
    28/28 job 全部 success ← 本次闭环
```

---

## 三、关键变更点（commit `2966105a` / master `d447bef8`）

| 文件 | 改动 |
|------|------|
| `scripts/check_circular_deps.py` | `extract()` 内 `os.path.relpath(filepath)` 包 try/except ValueError，降级为 `os.path.basename(filepath)` |
| `scripts/publish_fix_to_docs.py` | 新增 `_safe_relpath()` 辅助函数（跨盘符回退绝对路径），替换 3 处 `os.path.relpath` 调用 |

### 降级策略（不易：不变量说明）

- **check_circular_deps.py**：模块名仅作集合 key，测试不硬编码其值 → 降级 basename 不影响语义，不改核心逻辑。
- **publish_fix_to_docs.py**：路径仅用于打印 / `git add` → 跨盘符回退绝对路径安全（真实推送场景仓库与文件同盘，仅测试触发跨盘符）。

```python
# check_circular_deps.py — extract() 内
try:
    rel = os.path.relpath(filepath).replace("\\", "/")
except ValueError:
    # Windows 跨盘符（如 pytest tmp_path 在 C: 而仓库在 D:）无法计算相对路径，
    # 降级为文件名（模块名仅作集合 key，测试不硬编码其值）。
    rel = os.path.basename(filepath)

# publish_fix_to_docs.py — 新增辅助函数
def _safe_relpath(path: str, start: str | None = None) -> str:
    """跨盘符安全的相对路径（Windows runner 上 tmp 在 C: 而仓库在 D: 时 relpath 抛 ValueError）。"""
    try:
        return os.path.relpath(path, start)
    except ValueError:
        return os.path.abspath(path)
```

---

## 四、测试结果

### 4.1 本地验证

| 验证项 | 结果 |
|--------|------|
| `_safe_relpath('C:\\...', 'D:\\repo')` 跨盘符模拟 | ✅ 回退绝对路径 |
| `extract()` 跨盘符输入模拟 | ✅ 不再抛 ValueError，正确解析导入 |
| 测试文件本地全量（test_check_circular_deps.py + test_ci_guard_fix_regression.py） | ✅ **29 passed** |

### 4.2 master 全量 CI 复验（run `31323466698`）

- head：`23f9b64e`（含跨盘符修复 `d447bef8` + 全部并行修复）
- **28/28 job 全部 success**，覆盖：
  - 单元测试 18 shard（Python 3.10/3.11/3.12 × 6，含跨盘符测试 `test_check_circular_deps.py` / `test_ci_guard_fix_regression.py`）
  - 集成测试、E2E、性能测试、安全扫描、代码质量、ChromaDB 预检、文档链接预检、覆盖率检查、测试总结

### 4.3 过程插曲

- 首次验证 run 被并行提交的 concurrency 抢占取消 → `gh run rerun 31323466698`（完整 rerun，非 `--failed`）解决。

---

## 五、分支同步确认

| 分支 | commit | 说明 |
|------|--------|------|
| release/v1.2.0 | `2966105a` | 原始修复 commit |
| master | `d447bef8` | cherry-pick 副本，内容与 `2966105a` 一致（仅 SHA 不同，预期） |

- master 当前 HEAD：`33136c19`（已与 origin/master 同步）
- 跨盘符修复 **已正确标记**：subject `fix(ci): Windows 跨盘符 relpath 降级（CHG-2026-0809）— 修复测试环境 ValueError`，body 含根因、变更点、验证记录，符合 commit 规范（conventional commit + 变更编号 CHG-2026-0809）

---

## 六、遗留事项

| 项 | 状态 | 说明 |
|----|------|------|
| §10 集成测试问题 | ⏳ 已立项 | 详见 [integration_s10_troubleshooting_plan_20260809.md](integration_s10_troubleshooting_plan_20260809.md)，H1（Windows locale 编码对齐）为最高优先 |
| 防回归扫描 | ⏳ 待执行 | 全仓库扫描 `subprocess`/`open` 无显式 UTF-8 调用点 |

---

## 七、关联文档

| 文档 | 说明 |
|------|------|
| [win_encoding_path_fix_archive_20260809.md](win_encoding_path_fix_archive_20260809.md) | 编码（§7/§8）+ 跨盘符修复过程归档 |
| [pr499_fix_summary_20260809.md](pr499_fix_summary_20260809.md) | PR #499 完整修复总结 |
| [integration_s10_troubleshooting_plan_20260809.md](integration_s10_troubleshooting_plan_20260809.md) | §10 集成测试排查计划 |
