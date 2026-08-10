# pre_commit_ci_guard 使用说明 — WARN 排查指南与常见误报处理

> 版本：v1.2（2026-08-10）— 新增一键安装脚本（install_guard.sh）与 pre-commit 框架链式集成
> 归档：`docs/troubleshooting/pre_commit_ci_guard_使用指南_20260810.md`
> 发布包：`release/pre_commit_ci_guard_20260810.zip`（同内容 README 见发布包内）
> 依据：《Singleton 与覆盖率并行测试_避坑指南_20260809》检查清单自动化

---

## 1. 背景与定位

把避坑指南 8 项检查清单固化为 **git commit 前自动执行**的护栏（guard），避免"测试先行、实现未同步"、覆盖率 artifact 丢失、模块顶层副作用等已发生过的缺陷再次进入仓库。

- **FAIL**：确定性缺陷 → 阻断提交（退出码 1）
- **WARN**：风险点。hook 默认 `--strict` 运行：**存量 WARN 按基线豁免，基线外新增 WARN 阻断提交**（增量阻断）
- **SKIP**：当前仓库不适用（如未启用 SingletonManager）
- 静态检查约 1 秒；`--run-serial` 追加串行复现（耗时，默认不跑）

---

## 2. 发布包与安装

### 2.1 发布包位置与内容

```
release/pre_commit_ci_guard_20260810.zip
├── pre_commit_ci_guard.py   # guard 主脚本（含 --strict / --update-baseline）
├── install.py               # 跨平台安装器（标准库，Win/Mac/Linux）
└── README.md                # 使用说明（本文档的发布包版）
```

### 2.2 安装（同事）

方式 A — 仓库内一键脚本（克隆了本仓库）：

```bash
bash scripts/install_guard.sh                     # Linux / macOS / Git Bash
& "C:\Program Files\Git\bin\bash.exe" scripts\install_guard.sh   # Windows PowerShell
```

方式 B — 发布包安装器（解压发布包）：

```bash
python install.py                     # 当前目录须是 git 仓库
python install.py --repo D:\work\repo # 或指定仓库
python install.py --check            # 校验部署状态
```

安装器自动完成：复制 guard 脚本到 `<repo>/scripts/` → 写入容错 + 增量阻断版 `.git/hooks/pre-commit` → 端到端校验（rc=0 表示就绪）。

### 2.3 链式集成 pre-commit 框架（.pre-commit-config.yaml）

仓库有被 git 跟踪的 `.pre-commit-config.yaml`（5 个框架 hook）。guard hook 链式调用框架：

- **guard 失败 → 硬阻断**；**框架失败 → 仅警告放行**（本次提交继续）；未安装 pre-commit 命令时自动跳过框架。
- 框架的 5 个检查此前从未真正注册生效（缓存无 db）；链式集成后，干净工作区下框架正常拦截，脏工作区恢复报 `patch does not apply` 时仅警告、不卡提交。

### 2.4 增量阻断与基线

- hook 调用 `--static-only --strict`：基线（`<仓库>/.guard_baseline.json`）外的 WARN 升级为 FAIL 阻断，基线内豁免。
- **首次提交自动生成基线并放行**，不卡历史债务；基线文件应提交到 git 共享。
- 存量清零后运行 `python scripts/pre_commit_ci_guard.py --update-baseline` 收紧豁免口径。

### 2.5 卸载 / 回滚

```bash
python install.py --uninstall   # 移除 hook（保留 guard 脚本；非本工具 hook 不误删）
```

覆盖非本工具 hook 前自动备份为 `pre-commit.bak`。

### 2.6 本次安装验证记录（2026-08-10）

| 验证项 | 结果 |
|---|---|
| guard 静态检查（主仓库） | `FAIL=0 WARN=3 PASS/SKIP=5`，rc=0 |
| strict 首次运行 | 自动生成基线（54 条），放行 rc=0 |
| strict 二次运行 | 存量豁免 54、新增阻断 0，rc=0 |
| strict 阻断模拟（部分基线） | 53 条基线外 WARN 全部阻断，rc=1 |
| hook 模拟执行 | `--strict` 生效，汇总含豁免信息 |
| 临时全新仓库完整安装 | 部署 + hook + 校验全通过 |
| 真实 commit 端到端（v1.0） | hook 触发，提交成功（c120bba） |
| **模拟新增 WARN 被阻断（--strict）** | 临时文件触发 import_degraded 降级 → `FAIL=1 新增阻断 1`，hook exit=1，提交被拦截；清理后 HEAD 还原 |
| **真实新增 WARN 被阻断** | 并行会话改动 `resource_monitor.py:889` 引入新静默降级 → guard 拦截（exit=1），机制对真实改动同样生效 |
| 一键脚本 install_guard.sh | 临时仓库验证：guard 缺失自动恢复 → 安装 hook → 验证 → 框架检测，exit=0 |
| 链式框架集成 | guard 失败硬阻断、pre-commit 框架失败警告放行；主仓库 hook 重装为链式版 |

---

## 3. 工作原理与并行会话注意（重要）

### 3.1 存在性容错 + 增量阻断 hook

```sh
#!/bin/sh
GUARD="$(git rev-parse --show-toplevel)/scripts/pre_commit_ci_guard.py"
if [ ! -f "$GUARD" ]; then
  echo "[pre-commit-guard] 未部署 $GUARD，本次跳过（如需启用请部署脚本）"
  exit 0
fi
python "$GUARD" --static-only --strict || exit 1
# 链式：guard 通过后调用 pre-commit 框架（未安装时跳过；失败仅警告放行）
if command -v pre-commit >/dev/null 2>&1; then
  pre-commit run --hook-stage commit || \
    echo "[pre-commit-guard] 注意：pre-commit 框架 hook 未全部通过，本次提交继续，请尽快处理。"
fi
```

脚本未部署到某 worktree 时**跳过且不阻断**——这是多 worktree/并行会话场景下的关键设计。

### 3.2 并行会话同步结论（已实证）

- git 的 `.git/hooks/` 目录对所有 worktree **共享**（worktree 的 `.git` 只是 gitdir 指针文件，无独立 hooks 目录）。
- 当前 3 个并行 worktree（`agent-b3` / `agent-lint` / `agent-wip-ti`）**均无 guard 脚本** → 它们提交时 hook 自动走容错分支跳过，**不会阻断**。
- **不需要也不应**主动往并行 worktree 部署脚本，除非对方明确要启用；否则 guard 的 git diff 检查（`check_test_impl_sync_git`）会基于各 worktree 的提交历史产生差异提示，干扰其提交流程。
- 若并行会话要启用：在其 worktree 内跑 `python install.py --repo <worktree路径>`（hooks 共享，只需把脚本复制进该 worktree）。
- **唯一风险**：并行会话若用旧版 `--install-hook` 重装 hook，会覆盖回非容错版。检测方法：`python install.py --check` 输出 `hook 非本工具版本` 即被覆盖，重装即可。

---

## 4. WARN 项排查指南（2026-08-10 当前基线）

当前仓库：`FAIL=0 WARN=3 PASS/SKIP=5`，`--strict` 下存量豁免 54 条、新增阻断 0。WARN 按影响从高到低：

### WARN-A：47 处 except ImportError 注册降级且无告警

- **判定**：`except ImportError:` 分支内含注册降级标志（`register_singleton = None` / `get_singleton = None` / `_SINGLETON_AVAILABLE = False`），且分支内无 `logging` / `warnings` / `logger.` / `warn`。
- **风险**：依赖缺失时静默降级 → `is_registered(...)` 为 False，正是 BUG-20260809-001 的根因模式（测试期望注册成功，实现静默跳过）。
- **排查**：`python scripts/pre_commit_ci_guard.py --static-only` 看前 5 个示例（如 `ab_testing.py:1283`、`api_gateway.py:488`、`async_executor.py:351`、`auto_tuner.py:972`、`failure_analysis.py:946`）；全量清单命令见发布包 README §6。
- **修复**：降级分支加显式告警，例如：
  ```python
  except ImportError:
      logger.warning("singleton 注册降级：可选依赖缺失，功能受限")
      register_singleton = None
  ```
  有意静默的冷启动路径需在 PR 中说明理由。

### WARN-B：6 处模块顶层副作用（`agent/tests/` 内）

- **判定**：`agent/` 下 `.py` 顶层（非 def/class/import）出现 `logging.disable` / `logging.basicConfig` / `os.environ[` / `os.setenv` / `os.chdir` / `sys.path.append` / `warnings.simplefilter`。
- **当前命中**：`agent/tests/test_behavior_controller.py:7`、`test_behavior_controller_debug.py:9`、`test_memory_manager.py:6`、`test_permission_system.py:7`、`test_planning.py:6` 等 6 处。
- **风险**：pytest collection 阶段 import 即执行 → 全局改日志/环境，污染其他测试（此前 Shard 4 串行段 10 failed 即 `logging.disable` 顶层调用所致）。
- **修复**：副作用移入 pytest fixture：
  ```python
  @pytest.fixture(autouse=True)
  def _silence_logs():
      logging.disable(logging.CRITICAL)
      yield
      logging.disable(logging.NOTSET)
  ```
  幂等全局配置（如 `os.environ.setdefault`）已在规则中排除，不告警。

### WARN-C：分片脚本未将 performance/stress 纳入串行段

- **判定**：`split_unit_tests.py` / `split_tests.py` 需同时含 `tests/performance` 与 `tests/stress`。
- **风险**：性能/压力测试混入 `-n 2` 并行矩阵 → 共享 runner 上微秒级断言 flake（如 `test_singleton_performance.py` 首次初始化对比）。
- **修复**：把 performance/stress 目录测试显式划入串行段（参考 `observability-ci.yml` L946-968），串行段 pytest 尾加 `|| [ $? -eq 5 ]` 容错；或接入性能 flaky 白名单过渡。

---

## 5. 常见误报处理

guard 已内置消噪（以下场景**不**告警）：

| 场景 | 排除规则 |
|---|---|
| 测试自行注册的桩名 | 集成断言期望名减去测试内 `register_singleton` 注册的桩名 |
| 方法调用 | `(?<!\.)is_registered` 负向断言，排除 `barrier.is_registered()` |
| 幂等配置初始化 | 顶层副作用排除 `os.environ.setdefault` / `os.getenv` |
| 未启用 SingletonManager | 相关检查项 SKIP（不计入 WARN/FAIL） |

**仍被误报时**：
1. 对照 §4 复核命中行，确认非真问题；
2. 临时跳过（仅本次）：`git commit --no-verify -m "..."`（会跳过所有 hook，慎用）；
3. 反馈维护者修规则（排除逻辑集中在 guard 各检查函数，注释已标注"排除"）。

---

## 6. 参数速查

| 命令 | 说明 |
|---|---|
| `python scripts/pre_commit_ci_guard.py --static-only` | 静态检查（WARN 只提示不阻断） |
| `python scripts/pre_commit_ci_guard.py --static-only --strict` | 增量阻断：基线外新增 WARN 升级 FAIL（hook 默认调用） |
| `python scripts/pre_commit_ci_guard.py --update-baseline` | 刷新基线文件（存量清零后收紧豁免口径） |
| `python scripts/pre_commit_ci_guard.py --baseline <path>` | 指定基线文件（默认 `<仓库>/.guard_baseline.json`） |
| `python scripts/pre_commit_ci_guard.py --run-serial` | + 串行复现 singleton（`-p no:xdist`） |
| `python scripts/pre_commit_ci_guard.py --install-hook` | 写入容错 + 增量阻断版 hook |
| `python release/pre_commit_ci_guard/install.py [--repo ...]` | 发布包安装 |
| `python release/pre_commit_ci_guard/install.py --check [--repo ...]` | 状态校验 |
| `python release/pre_commit_ci_guard/install.py --uninstall [--repo ...]` | 卸载 |

退出码：`0` 通过；`1` 有 FAIL 项或基线外新增 WARN（阻断）。WARN 存量豁免不影响退出码。

---

## 7. 关联文档

- 《Singleton 与覆盖率并行测试_避坑指南_20260809》（`docs/zh/知识库重构计划/`）— 检查清单来源
- [BUG_TRACKER_test_metrics_modules_registered_20260809.md](../zh/知识库重构计划/BUG_TRACKER_test_metrics_modules_registered_20260809.md) — 注册降级静默问题（BUG-20260809-001）
- [shard_coverage_artifact_and_omit_rootcause_20260809.md](shard_coverage_artifact_and_omit_rootcause_20260809.md) — omit/workflow 根因详查
- [pre_commit_ci_guard_部署操作手册_20260810.md](pre_commit_ci_guard_部署操作手册_20260810.md) — 同事部署手册
