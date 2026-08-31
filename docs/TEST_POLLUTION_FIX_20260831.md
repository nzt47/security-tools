# 测试套件污染治理报告（P0/P1/P2，2026-08-31）

> 背景：全量 pytest（15097 用例）随机序长跑出现 **4486 passed / 22072 errors**——
> 错误数超过用例总数，teardown 级联双报；单文件/双文件复跑全部通过，
> 判定为「顺序/规模依赖的环境污染」（与结案报告记载的 0xC0000005 / 编码 / 顺序污染同源）。
> 本报告记录 P0/P1/P2 治理过程与结果。

---

## 1. 问题证据链

| 证据 | 结论 |
|---|---|
| 单文件/双文件复跑全过（9/9、741/741、157/157、30/30） | 非静态代码缺陷 |
| 全量 22072 errors > 用例数 15097 | teardown 级联双报 |
| 失败分布全仓库（lastfailed 11051 条目） | 横切状态（线程/单例/编码）问题 |
| 钉种子（993162150）复现：1833 passed 后首个失败是 `test_precommit_hook_blocking` | 已知环境限制测试挡路（git hook 类，CI 通过） |
| 排除环境限制后：4765 passed 后首个真实失败 `test_plugin_loader::test_refresh_manifest_discovers_new_plugin`（单独跑 30/30 通过） | **真实顺序污染源头之一** |

## 2. 根因机制（按影响力排序）

1. **pytest capture 解码错误（首要根因，2026-08-31 实证）**：中文 Windows 默认 GBK
   代码页下，测试向 stdout/stderr 写入 GBK 字节；pytest 的 capture 在 teardown 用
   `_pytest/capture.py snap() → tmpfile.read()` 以 **UTF-8** 读回回放文件 →
   `UnicodeDecodeError`（`0xc0/0xcd` = GBK 首字节）→ teardown ERROR + 双报。
   实测触发：`test_extensions.py::test_search_all`（HTTP 404 日志含中文）。
   **修复**：解释器级 `PYTHONUTF8=1`（调用时设置才生效，conftest 内 setdefault 无效）。
2. **线程持有型单例泄漏**：`async_executor` / `resource_monitor` / `self_healer` /
   `task_scheduler` / `lazy_loader` / `async_lazy_loader` 注册于 SingletonManager，
   部分带 cleanup 钩子、部分没有；测试初始化后未清理 → ThreadPoolExecutor 非
   daemon 线程累积 → 长跑资源耗竭 → 后续测试 setup 级联失败。
3. **`--timeout=60 --timeout-method=thread`**：thread 方式超时杀测试但**线程不回收**，
   放大泄漏；模型加载/重 IO 测试在资源竞争下偶超 60s 被误杀。
4. **已有隔离缺口**：`reset_global_singletons` 覆盖 15 类模块级状态，但未覆盖
   SingletonManager 注册的线程持有型单例。

## 3. 已实施修复

### P0：钉种子复现 + 单例定向重置

- **复现协议**（复用项目已有 T-4 协议）：
  `python -m pytest tests/ -x -q --tb=long --randomly-seed=<seed> -p no:cacheprovider`
- **`tests/conftest.py`** `reset_global_singletons` 追加：
  - 第 16 项：定向重置 4 个带 cleanup 钩子的单例（async_executor / resource_monitor /
    self_healer / task_scheduler）——cleanup 钩子真正释放线程池；
  - 第 17 项：lazy_loader / async_lazy_loader（无 cleanup 钩子）——先 `shutdown()`
    线程池（幂等）再重置实例，避免「实例被丢但线程不退出」；
  - 原则：**不**用 `reset_all_singletons()`（其余单例无钩子，直接删实例不释放
    资源且会让 session 级 fixture 旧引用失效），沿用「定向重置」策略。

### P1：环境变量固化 + timeout 治理 + capture 解码修复

- **`tests/conftest.py` 顶部**（setdefault，不覆盖用户配置，供子进程继承）：
  - `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`；
  - `OMP_NUM_THREADS=4` / `MKL_NUM_THREADS=4`：限制 torch/numpy 线程，规避
    Windows C 扩展线程竞争 0xC0000005（与 README Docker 配置一致）。
- **`pytest.ini`**：`--timeout=60` → `--timeout=120`（减少 thread 方式误杀与泄漏放大；
  极慢测试应显式 `@pytest.mark.timeout(N)`）；固定 seed 协议区补充 Windows
  `PYTHONUTF8=1` 调用要求说明。
- **`scripts/run_full_pytest.py`**：补 `PYTHONUTF8=1`（原本只有 PYTHONIOENCODING），
  全量入口子进程进入解释器 UTF-8 模式（`run_full_pytest_bg.py` 复用同一逻辑）。
- **验证**（3 文件批量，修复前 1 failed + 1 error → 修复后 120 passed / 10 xfailed）：
  根因确认 pytest capture 以 UTF-8 读回 GBK 回放文件，调用级 `PYTHONUTF8=1` 根治。

### P2：多种子回归验证（待填结果）

- 协议：固定 seed `--randomly-seed=20260813` 全量回归 + 抽样 seed 1/2/3 +
  `-p no:randomly` 对照基线（`failures_baseline.txt`，2026-08-13 共 78 行）。
- 判定：固定 seed 下 0 failed / 0 errors（除基线与环境限制项）即收敛。

## 4. 验证结果（待填）

| 运行 | 结果 |
|---|---|
| 钉种子 993162150（修复前，排除环境限制） | 4765 passed / 1 failed（test_plugin_loader） |
| 钉种子 993162150（修复后） | （待填） |
| 规范 seed 20260813（修复后） | （待填） |
| 抽样 seed ×2（修复后） | （待填） |

## 5. 环境限制排除清单（与结案报告一致，非代码缺陷）

| 测试 | 原因 |
|---|---|
| `test_precommit_hook_blocking.py` | 依赖仓库安装 pre-commit hook（本机 BOM 检查会阻断真实 commit） |
| `test_ci_l3_context_preflight.py` / `test_preflight_runner.py` | 依赖本机 ChromaDB 可用性 |
| `test_mcp_executor*.py` | 沙箱子进程 PIPE 捕获限制 |
| `test_impact_analysis.py::test_git_not_available` | 沙箱 git 进程拉起限制（WinError 5） |

> 上述均为执行环境限制，正常机器/CI（Linux runner）通过；**不在代码里 skip**，
> 按仓库既有「排除项记录」纪律文档化。
