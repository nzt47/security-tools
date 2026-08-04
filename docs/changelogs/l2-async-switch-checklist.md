# 操作检查清单：L2 异步方案切换

**适用场景**：未来需要将 L2 冷数据加载从「同步串行 + 路径缓存」切换回「异步 IO (asyncio.to_thread) + 路径缓存」时使用。
**前置条件**：当前 master 分支处于同步串行方案（`L2_SCHEME=sync-serial-path-cache`）。
**验证工具**：`scripts/simulate_l2_async_switch.py`（dry-run 一致性校验）。

---

## 🎯 三义约束（必读）

| 约束 | 说明 |
|------|------|
| **【不易】不变量** | L2 冷数据懒加载必须从 `.md` 归档读取，绝不查 SQLite 主表；路径缓存 `key→filepath` 契约不变；`read_fragment` 接口签名向后兼容 |
| **【变易】扩展点** | 异步化仅改 `read_fragment` 内部 IO 与 `_build_l2` 调用方式，不破坏对外接口 |
| **【简易】顺序** | **先改实现 → 再改标记**。标记（L2_SCHEME）必须永远跟随实现，禁止标记撒谎 |

---

## 📋 Phase 1：切换前准备

- [ ] **1.1 确认当前状态一致**
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```
  预期输出：`[✓] 一致：L2_SCHEME=sync-serial-path-cache, 实现=sync`，退出码 0

- [ ] **1.2 创建实验分支**（详见 `scripts/l2_async_experiment_branch.ps1`）
  ```powershell
  git checkout -b feature/l2-async-io-experiment
  ```

- [ ] **1.3 备份当前同步方案的关键文件**（防御性，便于回滚）
  ```powershell
  git tag l2-sync-baseline-$(Get-Date -Format yyyyMMdd)
  ```

- [ ] **1.4 重新跑一次同步方案压测基线**（用于切换后对照）
  ```bash
  python scripts/bench_l2_stress.py > bench_sync_baseline.log 2>&1
  ```
  记录场景 A/B/C 的 P50/P99 作为对照基线

---

## 📋 Phase 2：代码修改（实现层，必改 2 处）

### 2.1 异步化 `read_fragment`

- [ ] **2.1.1** 在 [markdown_syncer.py:435](file:///c:/Users/Administrator/agent/agent/memory/markdown_syncer.py#L435) 修改方法签名与 IO 调用

  **方案 A（推荐，最小侵入）**：保持 `def read_fragment` 同步，在 `_build_l2` 侧用 `asyncio.to_thread` 包装
  - ✅ 接口签名不变，向后兼容
  - ✅ 单元测试无需改

  **方案 B（彻底异步化）**：改为 `async def read_fragment`，内部用 `await asyncio.to_thread(...)`
  - ⚠️ 接口签名变更，所有调用方需同步修改
  - ⚠️ 单元测试需改 `await`

- [ ] **2.1.2** 验证 `read_fragment` 异步特征已落地
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```
  预期：`read_fragment: async`（若用方案 A，此项仍为 sync，需结合 2.2 判断）

### 2.2 并发化 `_build_l2`

- [ ] **2.2.1** 在 [context_assembler.py:476-483](file:///c:/Users/Administrator/agent/agent/memory/context_assembler.py#L476-L483) 改串行 for 循环为并发

  **修改前**（串行）：
  ```python
  for r in vec_results:
      ...
      fragment = self.syncer.read_fragment(key, max_chars=self.l2_max_chars)
  ```

  **修改后**（并发，方案 A 配套）：
  ```python
  import asyncio
  async def _read_one(r):
      meta = getattr(r, "metadata", None) or {}
      key = meta.get("key") if isinstance(meta, dict) else None
      if not key:
          return None
      try:
          # 同步 read_fragment 放线程池，避免阻塞事件循环
          fragment = await asyncio.to_thread(
              self.syncer.read_fragment, key, max_chars=self.l2_max_chars
          )
      except Exception as e:
          logger.debug(...)
          fragment = ""
      return {"key": key, "fragment": fragment} if fragment else None

  results = await asyncio.gather(*[_read_one(r) for r in vec_results])
  fragments.extend([r for r in results if r])
  ```

- [ ] **2.2.2** 验证 `_build_l2` 并发特征已落地
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```
  预期：`_build_l2: concurrent`

---

## 📋 Phase 3：本地性能验证（决策门槛）

- [ ] **3.1 跑异步方案压测**
  ```bash
  python scripts/bench_l2_stress.py > bench_async.log 2>&1
  ```

- [ ] **3.2 对照基线，确认性能改善**（否则**停止切换**）
  ```bash
  python scripts/parse_ci_l2_report.py --bench-log bench_async.log --output bench_async_report.png
  ```
  **决策门槛**：异步方案 P50 必须 **优于或持平** 同步方案（场景 C P50 ≤ 16.81ms）
  - 若 P50 变慢 → **回滚，保持同步方案**，记录失败原因到 CHANGELOG
  - 若 P50 改善 → 继续 Phase 4

- [ ] **3.3 跑 L2 性能回归测试**（护栏验证）
  ```bash
  python -m pytest tests/performance/test_l2_perf_regression.py -v -m performance
  ```
  预期：4 个测试全部 PASSED

- [ ] **3.4 跑全量单元测试**（回归保护）
  ```bash
  $env:PYTHONIOENCODING="utf-8"
  python -m pytest tests/unit/test_tlm_markdown_sync.py tests/unit/test_tool_trace.py -v
  ```

---

## 📋 Phase 4：CI 标记同步（标记层，必改 2 处）

> ⚠️ **顺序约束**：必须在 Phase 2、3 完成且性能验证通过后，才改 CI 标记。

- [ ] **4.1** 修改 [test.yml:304](file:///c:/Users/Administrator/agent/.github/workflows/test.yml#L304)
  ```yaml
  L2_SCHEME: sync-serial-path-cache    # 改前
  L2_SCHEME: async-io-to-thread        # 改后
  ```

- [ ] **4.2** 修改 [test.yml:309](file:///c:/Users/Administrator/agent/.github/workflows/test.yml) 的 echo 方案描述
  ```yaml
  echo "方案: 同步串行 read_fragment + 路径缓存（最优方案）"                    # 改前
  echo "方案: 异步 IO (asyncio.to_thread) + 路径缓存"                           # 改后
  ```

- [ ] **4.3** 验证标记与实现一致性
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```
  预期：`[✓] 一致：L2_SCHEME=async-io-to-thread, 实现=async`，退出码 0

---

## 📋 Phase 5：CI 远程验证

- [ ] **5.1** 提交并推送实验分支
  ```powershell
  git add agent/memory/markdown_syncer.py agent/memory/context_assembler.py .github/workflows/test.yml
  git commit -m "perf(l2): 切换 read_fragment 到 asyncio.to_thread 异步方案"
  git push origin feature/l2-async-io-experiment
  ```

- [ ] **5.2** 在 GitHub Actions 上确认 L2 性能回归测试 Job 通过
  - 检查 CI 日志中 `SCHEME=async-io-to-thread` 正确输出
  - 检查 artifact 中 `l2_perf_report.png` 已生成

- [ ] **5.3** 下载 artifact，确认图表中场景 C/E 数据符合本地压测结果

---

## 📋 Phase 6：文档更新

- [ ] **6.1** 新增切换决策 CHANGELOG（详细版）
  路径：`CHANGELOG_L2_ASYNC_SWITCH_<YYYYMMDD>.md`
  内容：背景、实测数据对照、决策依据、影响文件

- [ ] **6.2** 更新简短变更说明
  路径：[docs/changelogs/2026-07-26-l2-async-io-revert-brief.md](file:///c:/Users/Administrator/agent/docs/changelogs/2026-07-26-l2-async-io-revert-brief.md)
  - 状态：`已完成` → `已切换至异步方案`
  - 更新实测数据表

- [ ] **6.3** 更新团队简报
  路径：[docs/briefings/2026-07-26-l2-perf-briefing.md](file:///c:/Users/Administrator/agent/docs/briefings/2026-07-26-l2-perf-briefing.md)
  - 追加"切换更新"章节

- [ ] **6.4** 更新根 README（如有 L2 性能章节）

---

## 📋 Phase 7：合并与清理

- [ ] **7.1** 创建 PR 并请求代码评审
  ```powershell
  gh pr create --title "perf(l2): 切换 read_fragment 到异步 IO 方案" --body "详见 CHANGELOG"
  ```

- [ ] **7.2** 评审通过后合并到 master
  ```powershell
  git checkout master
  git pull origin master
  git merge --no-ff feature/l2-async-io-experiment
  git push origin master
  ```

- [ ] **7.3** 删除实验分支
  ```powershell
  git branch -d feature/l2-async-io-experiment
  git push origin --delete feature/l2-async-io-experiment
  ```

- [ ] **7.4** 最终一致性校验
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```
  预期：`[✓] 一致：L2_SCHEME=async-io-to-thread, 实现=async`

---

## 🔄 回滚预案

若 Phase 3 性能验证失败或 Phase 5 CI 不通过，执行回滚：

- [ ] **R.1** 丢弃实验分支所有变更
  ```powershell
  git checkout master
  git branch -D feature/l2-async-io-experiment
  ```

- [ ] **R.2** 回滚到同步基线 tag
  ```powershell
  git reset --hard l2-sync-baseline-<YYYYMMDD>
  ```

- [ ] **R.3** 记录失败原因到 CHANGELOG（供后续决策参考）
  - 哪个场景性能恶化？恶化倍数？
  - 根因分析（线程池开销？GIL？缓存竞争？）

- [ ] **R.4** 跑一次一致性校验确认回到同步方案
  ```bash
  python scripts/simulate_l2_async_switch.py --check
  ```

---

## ✅ 验收标准（Definition of Done）

切换完成的充要条件（全部满足）：

| # | 验收项 | 验证方法 |
|---|--------|---------|
| 1 | `read_fragment` 异步化 | `simulate_l2_async_switch.py --check` 显示 `read_fragment: async` |
| 2 | `_build_l2` 并发化 | 同上显示 `_build_l2: concurrent` |
| 3 | `L2_SCHEME=async-io-to-thread` | 同上显示 CI 标记一致 |
| 4 | 标记与实现一致 | 同上退出码 0 |
| 5 | 异步方案 P50 ≤ 同步基线 | `bench_l2_stress.py` 对照 |
| 6 | CI 性能护栏 4 测试全过 | GitHub Actions 绿灯 |
| 7 | 全量单元测试通过 | `pytest tests/unit/` 无新增失败 |
| 8 | 文档已更新 | CHANGELOG + brief + briefing |
| 9 | 实验分支已合并并清理 | `git branch` 无残留 |

---

## 📚 相关文档

- [CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md](../../CHANGELOG_L2_ASYNC_IO_REVERT_20260726.md) — 原回退决策
- [简短变更说明](./2026-07-26-l2-async-io-revert-brief.md)
- [团队简报](../briefings/2026-07-26-l2-perf-briefing.md)
- [代码评审摘要](../reviews/2026-07-26-l2-perf-diff-summary.md)
- [L2 性能测试最佳实践](../../tests/performance/README.md)
- [模拟切换脚本](../../scripts/simulate_l2_async_switch.py)
- [临时分支 git 操作指令](../../scripts/l2_async_experiment_branch.ps1)

---

*本清单基于 2026-07-26 的回退决策经验编制，覆盖代码/CI/文档/验证四维，确保未来切换可追溯、可回滚、可验证。*
