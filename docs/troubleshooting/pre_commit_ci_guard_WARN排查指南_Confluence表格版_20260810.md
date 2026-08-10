# pre_commit_ci_guard WARN 排查指南（Confluence 表格版）

> 版本：v1.0（2026-08-10）
> 用途：可直接复制到 Confluence（插入 → Markdown）。完整背景见《WARN 修复案例与排查指南》。

---

## 1. WARN 项总览

| 编号 | 检查项 | 判定逻辑 | 风险 | 默认行为 | 当前存量 | 修复方向 |
|---|---|---|---|---|---|---|
| A | import_degraded | `except ImportError:` 分支含注册降级标志（`register_singleton = None` / `get_singleton = None` / `_SINGLETON_AVAILABLE = False`）且分支内无 `logging`/`warnings`/`logger.`/`warn` | 依赖缺失时**静默降级**，`is_registered` 返回 False，复现 BUG-20260809-001 根因模式（测试先行、实现静默跳过注册） | WARN（--strict 下新增阻断） | 47 处（agent/ 下） | 分支内补显式告警（logger.warning） |
| B | top_side_effect | `agent/` 下 `.py` 顶层（非 def/class/import）出现 `logging.disable` / `logging.basicConfig` / `os.environ[` / `os.setenv` / `os.chdir` / `sys.path.append` / `warnings.simplefilter` | pytest **collection 阶段 import 即执行** → 全局改日志/环境，污染其他测试（历史 Shard 4 串行段 10 failed） | WARN（--strict 下新增阻断） | 6 处（agent/tests/ 内） | 副作用移入 pytest fixture（autouse） |
| C | serial_dirs | 分片脚本 `split_unit_tests.py` / `split_tests.py` 需同时含 `tests/performance` 与 `tests/stress`（串行段） | 性能/压力测试混入 `-n 2` 并行矩阵 → 共享 runner 上微秒级断言 flake | WARN（--strict 下新增阻断） | 0（并行会话已修复，基线含历史签名） | 显式划入串行段 + 串行段 pytest 尾加 `\|\| [ $? -eq 5 ]` 容错 |

## 2. 修复案例：resource_monitor.py:889（import_degraded）

| 项 | 内容 |
|---|---|
| 拦截信息 | `[FAIL] 新增 WARN（基线外）: import_degraded:resource_monitor.py:889` |
| 修复前 | `except ImportError:` 分支仅置降级标志，无任何告警输出 |
| 修复后 | 分支首行补 `logger.warning("singleton 注册降级：可选依赖缺失，功能受限")` |
| 补丁 | `scripts/dev/resource_monitor_889_fix.patch`（`git apply` 即用） |
| 验证 | 应用后 `--static-only --strict` → FAIL=0 新增阻断 0，阻断解除 |
| 应用后建议 | `--update-baseline` 刷新基线，清除已失效签名 |

## 3. 常见误报处理（已内置消噪）

| 场景 | 排除规则 | 说明 |
|---|---|---|
| 测试自行注册的桩名 | 集成断言期望名 − 测试内 `register_singleton` 注册的桩名 | 避免把测试桩当"实现缺失" |
| 方法调用 | `(?<!\.)is_registered` 负向断言 | 排除 `barrier.is_registered(...)` 类调用，只统计模块级 |
| 幂等配置初始化 | 顶层副作用排除 `os.environ.setdefault` / `os.getenv` | 环境初始化属常规且幂等 |
| 未启用 SingletonManager | 相关检查项 SKIP | 不计入 WARN/FAIL |

## 4. 仍被误报时的处理流程

| 步骤 | 操作 | 说明 |
|---|---|---|
| 1 | 对照 §1 判定逻辑复核命中行 | 确认是否真问题，先假定真、再怀疑误报 |
| 2 | 临时放行（仅本次） | `git commit --no-verify`（跳过**所有** hook，慎用） |
| 3 | 反馈维护者修规则 | 提供命中示例（文件:行号 + 代码片段），排除逻辑集中在 guard 各检查函数 |

## 5. 参数速查

| 命令 | 说明 |
|---|---|
| `python scripts/pre_commit_ci_guard.py --static-only` | 静态检查（WARN 只提示） |
| `python scripts/pre_commit_ci_guard.py --static-only --strict` | 增量阻断（hook 默认调用） |
| `python scripts/pre_commit_ci_guard.py --update-baseline` | 刷新基线文件 |
| `python scripts/pre_commit_ci_guard.py --run-serial` | + 串行复现 singleton 测试 |
| `python scripts/pre_commit_ci_guard.py --install-hook` | 安装 pre-commit + pre-push hook |
| `python release/pre_commit_ci_guard/install.py [--repo ...]` | 发布包安装 / `--check` 校验 / `--uninstall` 卸载 |

## 6. 验证记录（2026-08-10）

| 验证项 | 结果 |
|---|---|
| 889 修复前（--strict） | FAIL=1 新增阻断 1（resource_monitor.py:889），拦截 |
| 889 修复后（--strict） | FAIL=0 新增阻断 0，exit=0，放行 |
| 模拟提交流程（修复后） | hook 通过，提交成功 |
| 真实提交 ×3 | 链式框架 commit 阶段 4 hook 全部 Passed |
| 真实 push ×2 | pre-push（kwarg MEDIUM）Passed，exit=0 |
| 远端确认 | 58b0a615 / a3c95f12 / 42a0422d 均在 origin/develop |
