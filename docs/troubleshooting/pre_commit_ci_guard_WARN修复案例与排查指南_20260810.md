# pre_commit_ci_guard WARN 修复案例与排查指南（resource_monitor.py:889）

> 版本：v1.0（2026-08-10）
> 归档：`docs/troubleshooting/`
> 场景：guard（`--strict` 增量阻断）拦截了并行会话改动引入的 `import_degraded:resource_monitor.py:889`，本文档记录修复补丁、验证过程与通用排查方法，供其他同事参考。

---

## 1. 案例回顾：--strict 拦截了什么

guard 以 `--strict` 运行：**基线（`.guard_baseline.json`）外的新增 WARN 升级为 FAIL 阻断提交**。本次拦截输出：

```
[FAIL] 新增 WARN（基线外，须处理后方可提交）: import_degraded:resource_monitor.py:889
=== 汇总：FAIL=1 WARN=2（基线内豁免 53，新增阻断 1） PASS/SKIP=6 ===
```

命中位置（`agent/monitoring/resource_monitor.py:889`）：

```python
try:
    from agent.utils.singleton_manager import register_singleton, get_singleton, reset_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False      # ← 静默降级：无任何告警输出
    register_singleton = None
    get_singleton = None
    reset_singleton = None
```

- **判定规则**：`except ImportError:` 分支内出现注册降级标志（`register_singleton = None` / `get_singleton = None` / `_SINGLETON_AVAILABLE = False`），且分支内无 `logging` / `warnings` / `logger.` / `warn` 输出 → 记为 `import_degraded` WARN。
- **风险**：依赖缺失时**静默降级**，`is_registered(...)` 返回 False，与"测试期望注册成功"冲突——正是历史缺陷 BUG-20260809-001 的根因模式（测试先行、实现静默跳过注册）。

---

## 2. 修复方案与补丁

### 2.1 修复内容

在降级分支首行加显式告警（该文件 L45 已有 `logger = logging.getLogger(__name__)`，可直接使用）：

```python
except ImportError:
    logger.warning("singleton 注册降级：可选依赖缺失，功能受限")
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None
    reset_singleton = None
```

### 2.2 补丁文件

已生成补丁：`scripts/dev/resource_monitor_889_fix.patch`

应用方式（在仓库根目录）：

```bash
git apply --check scripts/dev/resource_monitor_889_fix.patch   # 预检，无输出即可通过
git apply scripts/dev/resource_monitor_889_fix.patch           # 应用
python scripts/pre_commit_ci_guard.py --static-only --strict   # 验证：FAIL=0 即解除
```

### 2.3 模拟验证结果（临时仓库，未动真实文件）

| 场景 | 结果 |
|---|---|
| 补丁应用前 | `FAIL=1 新增阻断 1（import_degraded:resource_monitor.py:889）`，exit=1 拦截 |
| `git apply --check` | 通过（exit=0） |
| 补丁应用后 | `FAIL=0 新增阻断 0`，exit=0 **不再拦截** |
| 模拟提交流程（提交基线走 hook） | hook 通过，提交成功 |

> 注意：该修复由**并行会话**负责落地（真实文件本会话未改动）。并行会话应用补丁后，再运行 `python scripts/pre_commit_ci_guard.py --update-baseline` 刷新基线（顺带清除已失效的 `serial_dirs:["missing"]` 历史签名）。

---

## 3. WARN 项排查指南（2026-08-10 基线）

当前仓库：`FAIL=0 WARN=3 PASS/SKIP=5`（strict 下存量豁免 54、新增阻断 0）。按影响从高到低：

### WARN-A：except ImportError 注册降级且无告警（import_degraded，47 处存量 + 889 新增）

- **判定**：`except ImportError:` 分支含降级标志且无告警输出（见 §1）。
- **排查**：
  1. `python scripts/pre_commit_ci_guard.py --static-only` 查看前 5 个示例（文件:行号）。
  2. 逐处判断缺依赖是**预期内**（可选特性）还是**配置错误**。
  3. 修复：分支内加显式告警（参考 §2.1 模板）。
  4. 全量清单命令：
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
- **修复后收紧**：存量清零后 `--update-baseline`，防止同类问题死灰复燃。

### WARN-B：模块顶层副作用（top_side_effect，6 处，均在 agent/tests/）

- **判定**：`agent/` 下 `.py` 顶层（非 def/class/import）出现 `logging.disable` / `logging.basicConfig` / `os.environ[` / `os.setenv` / `os.chdir` / `sys.path.append` / `warnings.simplefilter`。
- **风险**：pytest **collection 阶段 import 即执行** → 全局改日志/环境，污染其他测试（历史 Shard 4 串行段 10 failed 即 `logging.disable` 顶层调用所致）。
- **修复**：副作用移入 fixture：
  ```python
  @pytest.fixture(autouse=True)
  def _silence_logs():
      logging.disable(logging.CRITICAL)
      yield
      logging.disable(logging.NOTSET)
  ```
- 幂等配置（`os.environ.setdefault` / `os.getenv`）已在规则中排除，不告警。

### WARN-C：分片脚本未纳入 performance/stress 串行段（serial_dirs）

- **判定**：`split_unit_tests.py` / `split_tests.py` 需同时含 `tests/performance` 与 `tests/stress`。
- **风险**：性能/压力测试混入 `-n 2` 并行矩阵 → 共享 runner 上微秒级断言 flake。
- **修复**：划入串行段（参考 `observability-ci.yml` L946-968 模式），串行段 pytest 尾加 `|| [ $? -eq 5 ]` 容错。

---

## 4. 常见误报处理

guard 已内置消噪，以下场景**不会**告警：

| 场景 | 排除规则 |
|---|---|
| 测试自行注册的桩名 | 集成断言期望名减去测试内 `register_singleton` 注册的桩名 |
| 方法调用 `barrier.is_registered(...)` | `(?<!\.)is_registered` 负向断言，只统计模块级调用 |
| 幂等配置初始化 `os.environ.setdefault` | 顶层副作用排除 setdefault / getenv |
| 未启用 SingletonManager 的仓库 | 相关检查项 SKIP（不计入 WARN/FAIL） |

**仍觉得误报时**：
1. 对照 §3 判定逻辑复核命中行，确认非真问题（例如 `except ImportError` 分支确实无告警输出）；
2. 临时放行（仅本次）：`git commit --no-verify`（会跳过**所有** hook，慎用）；
3. 反馈维护者修规则：把命中示例（文件:行号 + 代码片段）发给维护者，排除逻辑集中在 guard 各检查函数（注释已标注"排除"）。

> 原则：**先假定是真问题，再怀疑误报**。本项目历史上"静默降级"与"顶层副作用"都造成过 CI 集体失败。

---

## 5. 关联文档

- [部署操作手册](pre_commit_ci_guard_部署操作手册_20260810.md)：安装/卸载/阻断处理（v1.2）
- [使用指南](pre_commit_ci_guard_使用指南_20260810.md)：排查指南全文 + 验证记录（v1.2）
- 《Singleton 与覆盖率并行测试_避坑指南_20260809》（`docs/zh/知识库重构计划/`）：检查清单来源
- 补丁：`scripts/dev/resource_monitor_889_fix.patch`
