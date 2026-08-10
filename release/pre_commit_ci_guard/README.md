# pre_commit_ci_guard 发布包 — 使用说明

> 版本：v1.2（2026-08-10）— 链式集成 pre-commit 框架（失败警告放行）
> 适用：任何成员克隆本仓库后一键启用提交前 CI 护栏；也支持任意 git 仓库（`--repo`）。
> 依据：《Singleton 与覆盖率并行测试_避坑指南_20260809》检查清单自动化。

---

## 1. 这是什么

`pre_commit_ci_guard` 在 **git commit 前自动运行 8 项静态检查**（约 1 秒），把避坑指南里的检查清单固化为护栏：

| 分类 | 检查项 | 默认行为 | 增量阻断（--strict） |
|---|---|---|---|
| Singleton | ① 注册覆盖：测试期望的注册名必须存在于实现 | FAIL 阻断 | FAIL 阻断 |
| Singleton | ② except ImportError 注册降级必须显式告警 | WARN 提示 | 新增阻断 / 存量豁免 |
| Singleton | ③ reset 只重置实例、保留注册表 | WARN 提示 | 新增阻断 / 存量豁免 |
| 覆盖率 | ④ omit 必须用 `*/tests/*` 跨目录模式 | WARN 提示 | 新增阻断 / 存量豁免 |
| 覆盖率 | ⑤ workflow 必须含 if: always() + coverage-data + exit5 容错 | WARN 提示 | 新增阻断 / 存量豁免 |
| 覆盖率 | ⑥ performance/stress 应纳入串行段 | WARN 提示 | 新增阻断 / 存量豁免 |
| 覆盖率 | ⑦ 模块顶层禁止强副作用 | WARN 提示 | 新增阻断 / 存量豁免 |
| git | ⑧ 本次改动测试/实现是否同步 | WARN 提示 | WARN 提示（随提交变化，无基线意义） |

- **FAIL** = 确定性缺陷，**阻断提交**（退出码 1）。
- **WARN** = 风险点。hook 默认以 `--strict` 运行：**存量 WARN 按基线豁免，新增 WARN 阻断提交**（增量阻断）。
- 支持 `--run-serial` 追加串行复现 singleton 测试（耗时，默认不跑）。

---

## 2. 发布包内容

```
pre_commit_ci_guard_20260810.zip
├── pre_commit_ci_guard.py   # guard 主脚本（检查项实现 + --install-hook）
├── install.py               # 跨平台安装器（部署脚本 + 注册 hook + 校验）
└── README.md                # 本说明文档
```

---

## 3. 安装

### 3.1 前提

- git（Windows 用户请使用 Git for Windows，自带 `sh.exe`）
- Python ≥ 3.8（guard 脚本仅用标准库）
- 目标目录必须是 git 仓库

### 3.2 一键安装

**方式 A — 已克隆本仓库的同事（一条命令）：**

```bash
# 仓库根目录执行：
bash scripts/install_guard.sh                  # Linux / macOS / Git Bash
& "C:\Program Files\Git\bin\bash.exe" scripts\install_guard.sh   # Windows PowerShell
```

脚本自动完成「部署 guard（缺失时从 release/ 恢复）→ 安装 hook → 端到端验证」，并提示链式框架集成状态。

**方式 B — 发布包安装器（解压发布包后）：**

```bash
# 解压发布包到任意位置，进入目录后执行：
python install.py                    # 默认安装到当前目录（当前目录须是 git 仓库）

# 安装到其他仓库：
python install.py --repo D:\work\security-tools
```

安装器会完成三件事：
1. 把 `pre_commit_ci_guard.py` 复制到 `<仓库>/scripts/pre_commit_ci_guard.py`
2. 写入 `<仓库>/.git/hooks/pre-commit`（存在性容错 + 增量阻断版）
3. 端到端校验（`--check` 逻辑），打印 `rc=0` 表示就绪

### 3.3 增量阻断与基线（重要）

- hook 以 `--strict` 运行：**基线外新增的 WARN 阻断提交，基线内存量 WARN 豁免**。
- **首次提交时**自动生成基线文件 `<仓库>/.guard_baseline.json`（记录当前全部 WARN 签名：`文件:行号`），本次放行。
- 之后新增的 WARN（新文件/新行/新检查项）不在基线中 → 被阻断，须修复后才能提交。
- 基线文件**应提交到 git**（团队共享豁免范围），同事 pull 后豁免口径一致。
- 存量 WARN 清零后，建议 `python scripts/pre_commit_ci_guard.py --update-baseline` 刷新基线（旧的豁免签名被移除，防止同类问题死灰复燃）。

### 3.4 验证

```bash
python install.py --check [--repo ...]
# 期望输出：
#   [PASS] guard 脚本已部署：...
#   [PASS] hook 为容错版：...
#   [INFO] guard 静态检查：rc=0  === 汇总：FAIL=0 WARN=3（基线内豁免 54，新增阻断 0） PASS/SKIP=5 ===
```

此后每次 `git commit` 都会先跑检查：

```
$ git commit -m "feat: xxx"
=== 提交前 CI 护栏（避坑指南检查清单）===
  [PASS] ...
  [WARN] ...
=== 汇总：FAIL=0 WARN=3（基线内豁免 54，新增阻断 0） PASS/SKIP=5 ===
```

---

## 4. 参数速查

### guard 主脚本（`scripts/pre_commit_ci_guard.py`）

| 参数 | 说明 |
|---|---|
| （无参） | 静态检查（WARN 只提示不阻断） |
| `--static-only` | 仅静态检查（hook 默认参数，秒级） |
| `--strict` | 增量阻断：基线外新增 WARN 升级为 FAIL（hook 默认调用） |
| `--update-baseline` | 刷新基线文件为当前全部 WARN（存量清零后收紧豁免口径） |
| `--baseline <path>` | 指定基线文件（默认 `<仓库>/.guard_baseline.json`） |
| `--run-serial` | 追加串行复现 singleton 测试（`-p no:xdist`，耗时，供 CI 失败排查用） |
| `--install-hook` | 写入 `.git/hooks/pre-commit`（容错 + 增量阻断版），随后可用 `--static-only` 自检 |

### 安装器（`install.py`）

| 参数 | 说明 |
|---|---|
| `--repo <path>` | 目标仓库根目录（默认当前目录） |
| `--check` | 只检查部署状态与可运行性，不安装 |
| `--uninstall` | 移除 hook（保留 guard 脚本；非本工具安装的 hook 不会误删） |

---

## 5. 工作原理与注意事项

### 5.1 hook 是"存在性容错 + 增量阻断 + 链式框架"版

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

- 脚本未部署到某分支/worktree 时，hook **跳过且不阻断**（输出提示，exit 0）。
- `--strict` 增量阻断：首次运行自动生成基线并放行；此后基线外新增 WARN 阻断提交。
- 链式集成 pre-commit 框架（`.pre-commit-config.yaml`）：guard 失败硬阻断；框架失败仅警告放行（脏工作区常见 `patch does not apply`，不卡提交）。
- 这保证：**hook 对全仓库 worktree 生效，但 guard 脚本按 worktree 是否部署决定是否执行**。

### 5.2 多 worktree / 并行会话

- git 的 `.git/hooks/` 目录对**所有 worktree 共享**。
- 因此主工作区安装的 hook 会自动作用于并行会话的 worktree；若并行 worktree 未部署 guard 脚本，hook 自动跳过，**不会**阻断其提交。
- 若并行会话也希望启用护栏：在其 worktree 内运行一次 `python install.py`（hooks 目录已共享，主要是把 guard 脚本复制到该 worktree 的 `scripts/`）。

### 5.3 覆盖与回滚

- 安装器在覆盖非本工具安装的 hook 前会备份为 `pre-commit.bak`。
- 回滚：`python install.py --uninstall` 移除 hook；若需恢复原 hook，把 `pre-commit.bak` 改回 `pre-commit`。

---

## 6. WARN 项排查指南（2026-08-10 当前状态）

当前仓库基线：`FAIL=0 WARN=3 PASS/SKIP=5`。在 `--strict` 下，**存量 WARN 按基线豁免、新增阻断**；下列 3 项按影响从高到低排列：

### WARN-A：47 处 except ImportError 注册降级且无告警

- **判定逻辑**：`except ImportError:` 分支内出现注册降级标志（`register_singleton = None` / `get_singleton = None` / `_SINGLETON_AVAILABLE = False`），且分支内无 `logging` / `warnings` / `logger.` / `warn` 输出。
- **风险**：依赖缺失时**静默降级**，`is_registered(...)` 返回 False，与"测试期望注册成功"冲突 → 复现 BUG-20260809-001 的根因模式（测试先行、实现静默跳过注册）。
- **排查步骤**：
  1. 运行 `python scripts/pre_commit_ci_guard.py --static-only`，读取 WARN 列表前 5 个示例（如 `ab_testing.py:1283`、`api_gateway.py:488`、`auto_tuner.py:972`）。
  2. 逐处检查降级分支：缺依赖是**预期内**（如可选特性）还是**配置错误**。
  3. 修复：分支内加显式告警，例如：
     ```python
     except ImportError:
         logger.warning("singleton 注册降级：...（可选依赖缺失，功能受限）")
         register_singleton = None
     ```
  4. 全部修复后该 WARN 归零；若部分为**有意静默**（如冷启动路径），在 PR 描述中说明理由。
- **完整清单获取**：guard 脚本只打印前 5 个；如需全量，运行以下命令：
  ```bash
  python -c "import re,pathlib
  for f in pathlib.Path('agent').rglob('*.py'):
      ls=f.read_text(encoding='utf-8',errors='replace').splitlines()
      for i,l in enumerate(ls):
          if re.match(r'\s*except ImportError\s*:',l):
              b=ls[i+1:i+8]
              d=any(re.search(r'register_singleton\s*=\s*None|get_singleton\s*=\s*None|_SINGLETON_AVAILABLE\s*=\s*False',x) for x in b)
              if d and not any(re.search(r'logging|warnings|logger\.|warn',x) for x in b):
                  print(f'{f}:{i+1}')"
  ```

### WARN-B：6 处模块顶层副作用（`agent/tests/` 内）

- **判定逻辑**：`agent/` 下 `.py` 文件顶层（非 def/class/import 缩进）出现 `logging.disable` / `logging.basicConfig` / `os.environ[` / `os.setenv` / `os.chdir` / `sys.path.append` / `warnings.simplefilter`。
- **当前命中**：`agent/tests/test_behavior_controller.py:7`、`test_behavior_controller_debug.py:9`、`test_memory_manager.py:6`、`test_permission_system.py:7`、`test_planning.py:6` 等 6 处。
- **风险**：pytest **collection 阶段 import 即执行**顶层代码 → 全局改日志/环境，影响其他测试（此前 Shard 4 串行段 10 failed 即 `logging.disable` 顶层调用所致）。
- **排查步骤**：
  1. 打开命中文件对应行，确认副作用类型。
  2. 将副作用移入 pytest fixture（`conftest.py` 或测试类内）：
     ```python
     @pytest.fixture(autouse=True)
     def _silence_logs():
         logging.disable(logging.CRITICAL)
         yield
         logging.disable(logging.NOTSET)
     ```
  3. 若确需进程级全局配置（如 `os.environ.setdefault` 幂等初始化），在 PR 中说明并确认不与其他测试冲突。

### WARN-C：分片脚本未将 performance/stress 纳入串行段

- **判定逻辑**：仓库中 `split_unit_tests.py` / `split_tests.py` 需同时出现 `tests/performance` 与 `tests/stress`（串行段）。
- **风险**：性能/压力测试混入 `-n 2` 并行矩阵 → 共享 runner 上微秒级断言 flake（如 `test_singleton_performance.py` 首次初始化对比）。
- **排查步骤**：
  1. 打开分片脚本，确认是否已有串行段（`-m "... and serial"` 单进程段）。
  2. 若没有，把 performance/stress 目录的测试显式划入串行段（参考 `observability-ci.yml` L946-968 模式），并在串行段 pytest 尾加 `|| [ $? -eq 5 ]` 容错。
  3. 若团队已有性能 flake 白名单机制，可标记 flaky 作为过渡。

---

## 7. 常见误报处理

guard 的检查项经过**消噪处理**，以下场景**不会**触发告警：

| 场景 | 排除规则 | 说明 |
|---|---|---|
| 测试自行注册的桩名 | 集成断言仅取 `is_registered` 期望名，减去测试内 `register_singleton` 注册的桩名 | 避免把测试桩当"实现缺失" |
| 方法调用 | `(?<!\.)is_registered` 负向断言，排除 `barrier.is_registered(...)` 类方法调用 | 只统计模块级调用 |
| 幂等配置初始化 | 顶层副作用排除 `os.environ.setdefault` / `os.getenv` 读 | 环境初始化属常规且幂等 |
| 未启用 SingletonManager | 检查项 SKIP（`SKIP` 不算 WARN/FAIL） | `singleton_manager.py` 不存在时跳过 |

### 仍被误报时怎么办

1. **确认不是真问题**：对照第 6 节排查步骤复核命中行。
2. **临时跳过（仅本次提交）**：
   ```bash
   git commit --no-verify -m "wip: ..."
   ```
   > ⚠️ 仅限临时使用；`--no-verify` 会跳过**所有** hook。
3. **反馈修规则**：向维护者提交问题，说明命中示例与为何是误报；guard 的排除规则集中在各检查函数的正则与过滤逻辑（`pre_commit_ci_guard.py` 内注释已标注"排除"）。

---

## 8. FAQ

**Q1：Windows 下 hook 报 `sh: 未找到命令`？**
hook 由 git 调用其自带的 `sh.exe`（`C:\Program Files\Git\bin`），无需配置。若用非 Git-for-Windows 的 git，请安装 Git for Windows 后重装 hook。

**Q2：提交时输出 `[pre-commit-guard] 未部署 ... 本次跳过`？**
当前 worktree/分支没有 `scripts/pre_commit_ci_guard.py`。运行 `python install.py --repo <该仓库>` 部署即可；这是容错设计，不会阻断其他会话。

**Q3：hook 被其他会话/工具覆盖了？**
git hooks 目录对所有 worktree 共享，并行会话可能重装旧版 hook。处理：重跑 `python install.py`，并检查是否只有一份 hook 维护入口。

**Q4：`--run-serial` 很慢？**
它等价于 `pytest tests/unit/test_singleton_manager.py -p no:xdist`，仅 CI 出现 singleton 分片失败时用于区分"隔离问题 vs 确定性缺陷"，提交前不必要。

**Q5：退出码怎么理解？**
`0` = 通过（可提交）；`1` = 存在 FAIL 项或基线外新增 WARN（阻断）。WARN 存量豁免不影响退出码。

**Q6：提交被 `[FAIL] 新增 WARN（基线外）` 阻断怎么办？**
新增 WARN = 本次代码新引入了避坑指南禁止的模式（新文件/新行/新检查项不在基线中）。这是**真实新增风险**，应修复对应位置；修复后若基线需要收紧，运行 `python scripts/pre_commit_ci_guard.py --update-baseline`。仅当确认为误报时走第 7 节流程。

**Q7：`[info] 首次运行：已自动生成基线` 是什么意思？**
hook 首次执行时自动记录当前全部存量 WARN 到 `.guard_baseline.json` 并放行，避免刚部署就被历史债务卡住。基线文件应提交到 git，让团队豁免口径一致。

**Q8：基线文件（.guard_baseline.json）要维护吗？**
建议。存量 WARN 清零后运行 `--update-baseline` 移除旧的豁免签名，防止"存量永存"。基线随代码一起 review。

---

## 9. 关联文档

- 《Singleton 与覆盖率并行测试_避坑指南_20260809》：检查清单来源（`docs/zh/知识库重构计划/`）
- BUG-20260809-001 追踪单：注册降级静默问题（`docs/zh/知识库重构计划/BUG_TRACKER_test_metrics_modules_registered_20260809.md`）
- 归档副本：`docs/troubleshooting/pre_commit_ci_guard_使用指南_20260810.md`
- 部署操作手册：`docs/troubleshooting/pre_commit_ci_guard_部署操作手册_20260810.md`
