# Singleton 与覆盖率并行测试 — 避坑指南

> 来源：2026-08-09 两次真实 CI 事故复盘（BUG-20260809-001 Singleton 注册缺失 / BUG-20260809-002 覆盖率 artifact 链路）
> 适用：pytest-xdist 分片 CI、coverage 并行收集、全局单例（SingletonManager）注册测试

---

## 第一部分：Singleton 并行测试避坑

### 坑 1：测试先行、实现未同步（本次真因）

**现象**：`test_singleton_manager.py` 断言 `is_registered("auto_tuner")`，CI 首断言即失败。
**真因**：测试文件与 SingletonManager 框架已合入，但目标模块（auto_tuner 等 4 个）的 `register_singleton` 调用**尚未落地**——实现滞后于测试，失败是确定性的。

**规避**：
- 合入"测试 + 实现"必须同 commit（或实现先行）；
- 验收前 grep 注册调用完整性：
  ```powershell
  git show <sha>:agent/auto_tuner.py | Select-String "register_singleton"
  ```

### 坑 2：把"仅单 shard 失败"误判为 xdist 隔离问题

**现象**：仅 3.11 / Shard 3 失败，3.10 / 3.12 全过 → 直觉归因"并行分片污染"。
**真相**：该测试文件恰好被分片到 Shard 3——失败与并行**无关**，是分片分布巧合。

**规避**：先做**串行复现**，再下结论：
```powershell
pytest tests/unit/test_singleton_manager.py -p no:xdist -x
```
- 串行仍失败 → 确定性代码缺陷（非隔离问题）
- 串行通过、并行失败 → 才需要排查分片污染

### 坑 3：多实例化误判

**现象**：注册"丢失"，怀疑模块双份加载（reload / sys.path 差异）。
**验证方法**（探测脚本模式 B）：
```python
import sys
# 1. 模块只加载一次？
print([k for k in sys.modules if k.endswith("singleton_manager")])
# 2. 注册侧与查询侧是同一 _manager 实例？
import agent.auto_tuner as at
mgr = getattr(at, "_manager", None)          # 注册侧
import agent.utils.singleton_manager as sm
print(id(mgr) == id(sm._manager))            # True=单实例
```
本次结论：`sys.modules` 单条目 + `_manager` 身份一致 → 排除多实例化。

### 坑 4：try/except 导入静默跳过注册

**模式**（本次参考的注册块）：
```python
try:
    from agent.utils.singleton_manager import register_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
```
**隐患**：循环导入场景下 `ImportError` 静默 → 注册被跳过且无告警 → 表现为"模块能 import 但未注册"。

**规避**：`except` 分支**必须显式告警**（`logging.error` / `warnings.warn`），禁止静默降级。

### 坑 5：reset 语义误解

**事实**：`reset_all_singletons()` **只重置 `_instances`（已初始化实例），保留 `_factories`（注册表）** → reset 不会导致"注册丢失"。
**教训**：排查注册丢失时，先读 reset 实现（看是否动 `_factories`），再怀疑 reset 破坏，避免冤枉无辜。

### Singleton 检查清单

- [ ] 测试与实现同 commit / 实现先行
- [ ] 串行复现先行（`-p no:xdist`）再判定是否隔离问题
- [ ] 探测 `sys.modules` 单实例 + `_manager` 身份一致
- [ ] `except ImportError` 分支显式告警
- [ ] reset 语义确认（不动 `_factories`）

---

## 第二部分：覆盖率并行测试避坑

### 坑 6：`set -e` 中止导致 artifact 未生成/未上传（本次核心链路）

**链路**：shard 中任一步非零退出 → `set -e` 立即中止 → `mv .coverage` 跳过 → 上传空跑（`No files were found with the provided path`）→ combine/分析阶段下载 artifact 失败（`Artifact not found`）→ 覆盖率 job 失败。

**规避**：
- 上传步骤加 `if: always()`，保证即使前置失败也执行；
- `mv` / 改名步骤做容错（存在才移动）；
- 失败现场保留 artifact（`upload-artifact` 加 `if-no-files-found: warn` 而非 error）。

### 坑 7：omit 路径模式与 coverage 存储路径不匹配

**现象**：`tests/*` 前缀模式不生效，测试代码仍计入覆盖率分母。
**真相**：coverage `.data` 存的是**运行时刻完整路径**（CI 下为绝对路径 `/home/runner/work/...`），`tests/*`（仅前缀）匹配不上。
**修正**：用 `*/tests/*`（fnmatch 中 `*` 可跨目录）。

### 坑 8：无测试收集的 shard 导致 exit 5

**现象**：某 shard 串行段收集 0 个测试 → pytest exit 5（无测试）→ 被 `set -e` 当失败。
**规避**：
- 串行段加容错（如 `|| true` + 记录，或 `--no-header --exitfirst` 语义调整）；
- 或分片脚本保证每 shard 至少 1 个测试。

### 坑 9：性能测试 flake 混入覆盖率矩阵

**现象**：`test_singleton_performance.py` 断言 206us > 200us（环境波动）→ shard 失败。
**规避**：性能断言预留裕量（如阈值 × 1.5）或标 `@pytest.mark.serial` 串行段隔离；修复后确认无回归。

### 坑 10：模块顶层副作用在 collection 阶段被 import

**现象**：模块顶层 `logging.disable(CRITICAL)`，collection 阶段 import 即全局禁用日志 → 同进程所有 `assertLogs`/`caplog` 断言静默失败（本次 Shard 4 串行段 10 failed）。
**规避**：副作用（disable/环境变量/全局状态）移入 **autouse fixture + try/finally 恢复**，禁止模块顶层执行。

### 覆盖率检查清单

- [ ] 上传/合并步骤 `if: always()`
- [ ] `mv` 容错（存在才移动）
- [ ] omit 用 `*/tests/*`（跨目录）而非 `tests/*`
- [ ] 无测试 shard 的 exit 5 已容错
- [ ] 性能断言留裕量 / serial 隔离
- [ ] 模块顶层无日志/环境副作用

---

## 第三部分：CI 验证环境经验

### 坑 11：CI 队列拥塞下的"全绿确认"

**现象**：并行会话频繁 push → runner 队列 30-100 个 job 排队 → 新 push 的 CI 迟迟不跑 / 旧 run 被取消 → "全绿确认"窗口不断漂移。

**规避**：
- 确认某 commit 的 CI：用 `commits/<sha>/check-runs` 按**固定 sha** 查询（不随 master 前进漂移）；
- 区分 `cancelled`（队列拥塞取消，非代码失败）与 `failure`（代码失败）；
- 分页拉全量（per_page=100 上限，注意 `--paginate` 与 jq 组合可能输出碎片，优先循环分页 `page=N`）。

### 坑 12：分页截断

**现象**：check-runs 超过 100 条时，单页查询"看不到"部分 job（如单元测试矩阵）。
**规避**：
```powershell
$all = @(); for ($p = 1; $p -le 2; $p++) { $r = gh api ".../check-runs?per_page=100&page=$p" | ConvertFrom-Json; $all += $r.check_runs }
```

---

## 附：验证工具（本次沉淀）

| 脚本 | 用途 |
|------|------|
| `scripts/repro_singleton_metrics_registered.py --mode A` | 串行复现（`-p no:xdist`），排除/确认隔离问题 |
| `scripts/repro_singleton_metrics_registered.py --mode B` | 状态探测（注册状态 + `_manager` 身份 + `sys.modules` 单实例） |
| `--mode B --reset-before` | 验证 reset 对注册表的影响 |

## 总结

- **Singleton 类问题**：先串行复现 → 再查注册代码存在性 → 再查实例/模块加载 → reset 语义靠读代码确认。
- **覆盖率类问题**：顺着 `set -e → mv → artifact → combine` 链路定位；omit/exit 5/flake/顶层副作用是四大高频根因。
- **CI 验证**：锚定固定 sha，分页拉全量，区分 cancelled 与 failure。

> 关联文档：`docs/zh/知识库重构计划/PR77_结项总结报告_20260809.md`、`docs/troubleshooting/shard_coverage_artifact_and_omit_rootcause_20260809.md`
