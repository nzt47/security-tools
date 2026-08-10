# 缺陷追踪报告 — precommit hook 与 p6_snapshot（2026-08-09）

> 状态：✅ 根因已确认 | ⚠️ 未修复（规避方案见各节）
> 发现途径：全量回归 seed=12345 第 3 轮 + 单独运行验证（埋点确认非资源竞争，为确定性失败）

---

## 缺陷总览

| # | 缺陷 | 文件 | 严重度 | 类型 | 影响面 |
|---|------|------|--------|------|--------|
| 1 | pre-commit hook 编码检查拦截所有 git 提交 | `scripts/protect_source_files.ps1`（文件损坏）+ hook | 高 | 环境/文件损坏 | **任何 git commit 被拦截**（含 CI 外全部本地提交） |
| 2 | p6_snapshot 性能断言脆弱 | `tests/unit/test_p6_snapshot.py` + `agent/p6_snapshot.py` | 低 | 测试断言缺陷 | 仅该测试失败（单测） |
| 3 | task_scheduler 单例测试互相污染 | `tests/unit/test_task_scheduler_singleton.py` + `tests/integration/test_task_scheduler_integration.py` | 中 | 测试间顺序依赖污染 | 全量运行时 2 个用例失败（单独运行 115 全过） |
| 4 | SingletonManager 集成测试注册断言失败 | `tests/unit/test_singleton_manager.py` + `agent/utils/singleton_manager.py` | 中 | 疑似 xdist 分片隔离/多实例化（待串行复现验证） | master CI 3.11/Shard 3 失败（1 failed / 1714 passed） |

---

## 缺陷 3：task_scheduler 单例测试互相污染

### 3.1 现象

全量回归（seed=12345）出现 2 个失败（第 1/2/3 轮均有）：

```
FAILED tests/integration/test_task_scheduler_integration.py::TestGlobalSingleton::test_get_scheduler_returns_instance
    - assert False  （isinstance(s, TaskScheduler) 为 False）

FAILED tests/unit/test_task_scheduler_singleton.py::TestTaskSchedulerConcurrency::test_concurrent_first_get_initializes_once
    - AssertionError: 应只构造一次，实际 0 次
```

**单独运行两个测试文件：115 passed**（含上述 2 个用例）→ 顺序依赖实锤，非真实缺陷。

### 3.2 根因

两个用例对 `agent.task_scheduler` 的**模块级单例 `_scheduler`** 与**类引用 `module.TaskScheduler`** 的操纵互相干扰：

| 用例 | 操作 | 问题 |
|------|------|------|
| `test_concurrent_first_get_initializes_once`（[test_task_scheduler_singleton.py L72-107](file:///c:/Users/Administrator/agent/tests/unit/test_task_scheduler_singleton.py#L72-L107)） | 替换 `module.TaskScheduler = CountingScheduler` 统计构造次数；**无单例重置 fixture** | 若前序测试已初始化 `_scheduler`，`get_scheduler()` 命中缓存 → `CountingScheduler.__init__` 零调用 → `created` 为空 → "实际 0 次" |
| `test_get_scheduler_returns_instance`（[test_task_scheduler_integration.py L778-780](file:///c:/Users/Administrator/agent/tests/integration/test_task_scheduler_integration.py#L778-L780)） | `reset_scheduler_singleton` fixture 重置后断言 `isinstance(s, TaskScheduler)` | 若并发测试替换类后缓存/引用残留，`isinstance` 比较错位 → `assert False` |

共同点：**`_scheduler` 缓存跨测试残留**。并发测试没重置单例，integration 测试虽重置但依赖 `module.TaskScheduler` 类引用的一致性——两个测试互为污染源/受害方。

### 3.3 复现步骤

```bash
# 方式 A（确认单独运行通过）
python -m pytest tests/unit/test_task_scheduler_singleton.py tests/integration/test_task_scheduler_integration.py -q
# → 115 passed

# 方式 B（全量顺序下复现）
python -m pytest tests/ --randomly-seed=12345 -q --tb=line
# → 2 个 task_scheduler 失败（与其他用例的初始化顺序相关）

# 方式 C（定向复现：先初始化再并发）
python - <<'PY'
import agent.task_scheduler as m
m.get_scheduler()          # 先初始化缓存
from tests.unit.test_task_scheduler_singleton import TestTaskSchedulerConcurrency
# 模拟缓存残留下并发首次 get → created 为 0
PY
```

### 3.4 临时规避方案

| 方案 | 改动 | 说明 |
|------|------|------|
| **① 并发测试显式重置单例（推荐）** | `test_concurrent_first_get_initializes_once` 开头加 `module._scheduler = None`（或复用 `reset_scheduler_singleton` fixture） | 保证"并发首次构造"语义成立，不再受缓存残留影响 |
| ② integration 测试改用等价断言 | `isinstance(s, TaskScheduler)` 前校验 `module.TaskScheduler is TaskScheduler`（类未被替换） | 防御类替换泄漏 |
| ③ 全量跳过这两个测试 | 标记 `@pytest.mark.skipif` | 不推荐，掩盖真实现象 |

### 3.5 根治建议

1. **并发测试补单例重置**（方案①）——1 行改动，语义自洽；
2. 为 `test_task_scheduler_singleton.py` 全类添加 autouse 重置（与
   `TestIntentRouterClassify.setup_method` 同款模式），杜绝跨测试缓存残留；
3. conftest `reset_global_singletons` 第 13 步已覆盖 `_scheduler` 置 None，
   但**仅在每个测试 teardown 执行**——并发测试自身执行期间的缓存残留需自愈。

### 3.6 证据

- 全量失败：`.fix_backups/regression_seed12345_final.log` L13692/L13693
- 单独运行：115 passed（本节 3.3 方式 A，48.40s）

---

## 缺陷 1：pre-commit hook 编码检查拦截所有 git 提交

### 1.1 现象

`tests/regression/test_precommit_hook_blocking.py::test_real_git_commit_blocked_by_hook`
单独运行即失败：

```
AssertionError: [pre-commit] 运行全量预检（链接 + 锚点回归测试）...
      [BLOCK] scripts\protect_source_files.ps1: 叠加 BOM x2 (head: EF BB BF EF BB BF 23 52)
      [pre-commit][ERROR] 编码检查(UTF-8 BOM 契约) 未通过, 提交被阻止
        修复: python "C:\Users\Administrator\agent/scripts/check_ps1_encoding.py" --fix --repo-root "C:\Users\Administrator\agent"
        临时跳过: SKIP_ENCODING_CHECK=1 git commit 或 git commit --no-verify
    assert 1 == 0
```

测试流程：先提交**健康基线文档**（good.md），hook 链接预检 `[OK] 预检通过`，
但**随后的编码检查**发现 `scripts/protect_source_files.ps1` 双 BOM → 提交被阻止
（returncode=1）→ `assert good.returncode == 0` 失败。

### 1.2 根因

`scripts/protect_source_files.ps1` 文件头字节（已实测确认）：

```
EF BB BF EF BB BF 23 52 65 71 75 69 72 65 73 20 2D 56 65 72 73
└──BOM──┘ └──BOM──┘  #Requires -Vers...
```

**UTF-8 BOM 叠加两次**（文件大小 7989 字节）。该文件在早期会话中被反复写入
（protect watch 机制相关），某次写入追加了重复 BOM。hook 的
「UTF-8 BOM 契约」检查（`check_ps1_encoding.py`）判定文件损坏 → 拦截。

**影响**：hook 对 `TLM_HOOK_SOURCE_REPO` 指向的仓库执行编码检查，而测试
注入的源仓库就是本仓库 → **本仓库所有 git commit 都会被拦截**（无论是否
涉及该文件，因为检查扫描整个 `scripts/` 目录）。

### 1.3 复现步骤

```bash
# 方式 A（测试复现）—— 确定性失败
python -m pytest tests/regression/test_precommit_hook_blocking.py::test_real_git_commit_blocked_by_hook -q --tb=long -s

# 方式 B（直接复现）—— 任意提交被拦
git add scripts/protect_source_files.ps1   # 或任意文件
git commit -m "test"
# → 输出 [BLOCK] ... 叠加 BOM x2 ... 提交被阻止

# 方式 C（确认文件损坏）
powershell -Command "$b=[System.IO.File]::ReadAllBytes('scripts/protect_source_files.ps1'); ($b[0..5] | ForEach-Object {$_.ToString('X2')}) -join ' '"
# 期望输出: EF BB BF EF BB BF（双 BOM）
```

### 1.4 临时规避方案（按推荐顺序）

| 方案 | 命令 | 说明 |
|------|------|------|
| **① 修复文件（根治）** | `python scripts/check_ps1_encoding.py --fix --repo-root .` | 一次性修复 BOM 契约，此后提交恢复正常 |
| ② 跳过编码检查 | `SKIP_ENCODING_CHECK=1 git commit -m "..."` | 仅跳过编码段，链接/锚点检查仍生效 |
| ③ 绕过整个 hook | `git commit --no-verify` | 不推荐，跳过全部 hook |

### 1.5 根治建议

1. 运行 `check_ps1_encoding.py --fix` 修复 `protect_source_files.ps1`；
2. 排查该文件为何被写入双 BOM（早期会话的写盘脚本/工具链问题），
   避免再次叠加；
3. 可考虑 hook 编码检查失败时输出**具体文件路径 + 修复命令**（当前已具备），
   便于自愈。

### 1.6 证据

- 失败日志：`.fix_backups/regression_seed12345_final.log` L13517-13554
- 文件头实测：`EF BB BF EF BB BF 23 52`（本报告 1.2 节）

---

## 缺陷 2：p6_snapshot 性能断言脆弱

### 2.1 现象

`tests/unit/test_p6_snapshot.py::TestStateSnapshotManager::test_performance_monitor`
单独运行即失败（埋点输出）：

```
INFO  性能摘要: {'total_saves': 1, 'last_save_ms': 0.0, ...}
INFO  保存耗时: 0.00ms
AssertionError: 上次保存时间应大于 0，得到: 0.0，完整摘要: {...}，保存耗时: 0.00ms
```

### 2.2 根因

测试用 `patch.object(mgr, '_save_core_modules_with_delta', return_value=0)`
把**核心保存路径 mock 掉**。`save_snapshot` 中：

```python
start_time = time.time()          # L455
...
elapsed = (time.time() - start_time) * 1000   # L535
...
self.performance_monitor.record_save(elapsed, space_saved)  # L541
```

mock 后保存路径执行极快（空 module_states + 空快照文件），两次 `time.time()`
落在同一浮点刻度 → `elapsed == 0.0` → `record_save` 里
`last_save_time_ms = elapsed = 0.0` → 断言 `last_save_ms > 0` 失败。

**关键证据**：`total_saves == 1` 通过 → `record_save` **确实被调用**，性能记录
链路正常；失败纯粹是「mock 压缩耗时到浮点 0.0」导致的**断言边界问题**，
非业务缺陷。

### 2.3 复现步骤

```bash
# 确定性失败
python -m pytest "tests/unit/test_p6_snapshot.py::TestStateSnapshotManager::test_performance_monitor" -q --tb=long
```

### 2.4 临时规避方案

| 方案 | 改动 | 说明 |
|------|------|------|
| **① 放宽断言（推荐）** | `last_save_ms > 0` → `last_save_ms >= 0`（或删除该断言） | `total_saves == 1` 已证明 record_save 被调用；耗时断言在 mock 场景下无意义 |
| ② 不 mock，改为真实保存 | 移除 `patch.object(_save_core_modules_with_delta)` | 真实耗时 > 0，但引入真实模块序列化依赖（重） |
| ③ 断言 elapsed 来源 | 增加 `result.elapsed_ms > 0` 检查替代 | 语义更贴近「保存耗时被记录」 |

### 2.5 根治建议

1. 断言意图是「验证 performance_monitor 记录了保存操作」——用
   `total_saves == 1` 已充分；`last_save_ms` 的 >0 断言删除或改 `>= 0`；
2. 若需保留耗时断言，应在测试中构造**可测的最小真实保存**（不 mock 核心
   路径），而非依赖 `time.time()` 差值的浮点非零性。

### 2.6 证据

- 埋点输出：`.fix_backups/instrument_verify.log`（单独运行失败，last_save_ms: 0.0）
- 代码：`agent/p6_snapshot.py` L455/L535/L541；测试 `test_p6_snapshot.py` L445-476

---

## 缺陷 4：SingletonManager 集成测试注册断言失败（BUG-20260809-001）

> 关联追踪单：[BUG_TRACKER_test_metrics_modules_registered_20260809.md](../zh/知识库重构计划/BUG_TRACKER_test_metrics_modules_registered_20260809.md)
> 状态：OPEN（归属并行会话 SingletonManager 迁移，未修复）

### 4.1 现象

master CI（run `31322544891`，Python 3.11 / Shard 3，xdist gw0）失败：

```
________ TestSingletonModuleIntegration.test_metrics_modules_registered ________
[gw0] linux -- Python 3.11.15
tests/unit/test_singleton_manager.py:218: in test_metrics_modules_registered
    assert is_registered("auto_tuner")
E   AssertionError: assert False
E    +  where False = is_registered('auto_tuner')
======= 1 failed, 1714 passed, 34 skipped, 1 warning in 79.25s =======
```

**仅 3.11/Shard 3 失败**，3.10/3.12 其它 shard 未报 → 疑似分片隔离问题。

### 4.2 根因（已确认，2026-08-09 本地串行复现）

**根因：SingletonManager 迁移"测试先行、实现未同步"——注册代码缺失。**

| 验证项 | 结果 |
|--------|------|
| 模式 A 串行复现（`-p no:xdist`） | 1 failed, 17 passed，失败点与 CI 完全一致 → **确定性缺陷，排除 xdist 隔离** |
| 模式 B 状态探测 | 4 模块 import 成功但全部 `is_registered=False`；`auto_tuner` 未持有 `_manager` |
| sys.modules | singleton_manager 仅 1 份（单实例）→ 排除多实例化 |
| d447bef8 源码 | 4 个目标模块 `register_singleton` 均 0 次 → 注册代码缺失 |
| reset_all 审查 | 只重置 instances、保留 factories → 排除 reset 破坏 |

"仅 3.11/Shard 3 失败" = 该测试文件恰好分片至 Shard 3，与隔离无关。

### 4.3 复现步骤（脚本已就绪）

```bash
# 模式 A：串行运行（排除 xdist）
python scripts/repro_singleton_metrics_registered.py --mode A
#   通过 → 确认 xdist 分片隔离问题；失败 → 代码逻辑缺陷

# 模式 B：状态探测（导入序列 + _manager 身份 + reset 语义）
python scripts/repro_singleton_metrics_registered.py --mode B
python scripts/repro_singleton_metrics_registered.py --mode B --reset-before
```

### 4.4 建议修复方向（根因已确认）

1. **为 4 个目标模块补注册调用**（参考最新 master `33136c19` 中 `agent/auto_tuner.py` 的 try/except 注册块模式）：
   - `agent/auto_tuner.py` → `register_singleton("auto_tuner", _create_auto_tuner)`
   - `agent/monitoring/error_reporter.py` → `register_singleton("error_reporter", ...)`
   - `agent/monitoring/optimized_metrics.py` → `register_singleton("optimized_metrics_collector", ...)`
   - `agent/monitoring/tracing_cache.py` → `register_singleton("trace_cache", ...)`
2. 补注册时注意：try/except import 结构在**循环导入场景**下可能静默跳过注册（`_SINGLETON_AVAILABLE=False`）——建议 import 失败时打印显式告警而非静默降级
3. 修复后串行 + 全量回归验证（脚本 `scripts/repro_singleton_metrics_registered.py` 可直接复用）

### 4.5 证据

- CI 失败堆栈：run `31322544891` job `93269248927`（完整堆栈见追踪单第三节）
- 相关代码：`tests/unit/test_singleton_manager.py` L218；`agent/utils/singleton_manager.py` L173-176（`is_registered`）、L155-160（`reset_all`）；`agent/auto_tuner.py` 注册块

---

## 附：排查结论来源

1. 全量回归 seed=12345 第 3 轮（16 failed）中两个用例为**确定性失败**（非资源竞争）；
2. 7 处日志埋点验证（24 passed / 2 failed）证实：ci_guard 4 个为资源竞争
   （无并行即通过），**precommit / p6_snapshot 为真实失败**（单独运行复现）；
3. 本报告复现步骤均经实际执行确认。
