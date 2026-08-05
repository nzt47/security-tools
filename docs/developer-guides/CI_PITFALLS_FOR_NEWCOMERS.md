# 新入职开发者 CI 避坑指南

> 面向：新入职开发者（初级）| 范围：本仓库 CI 高频踩坑 | 更新：2026-08-05
> 本文所有案例均来自本仓库真实事故复盘，附修复 commit 可追溯。

---

## 一、铁律：本地绿 ≠ CI 绿

CI 在干净环境（GitHub Actions ubuntu）检出你的代码运行。任何"只存在于你本地"的东西，
CI 都没有。三条铁律：

1. **未入库 = 不存在**。`git add` 之前，你的文件只在你机器上。
2. **`__pycache__/*.pyc` 是假证**。本地能 import 不代表源文件已入库。
3. **CI 不共享你的环境变量、Python 包、PowerShell 重定向。**

> 案例（2026-08-05）：`run_ci_guard.py` 依赖的 `simulate_pr_merge_guard.py` /
> `safe_git_revert.py` 从未入库，仅残留 `.pyc`。本地全绿，CI 检出后直接
> `ModuleNotFoundError`。修复：commit `a422a64f` 重建入库。

---

## 二、坑 1：stdout 被日志污染（JSON 输出必挂）

### 现象
脚本加了 `--json` 模式供 CI 解析，但输出首行不是 JSON，导致 `json.load()` 崩溃。

### 根因
供 JSON 消费的**库函数**里用了 `print()` 输出调试日志，污染了调用方的 stdout。

```python
# ❌ 错误：dry-run 日志打进 stdout
def safe_revert(target, dry_run=True):
    if dry_run:
        print(f"[dry-run] 目标 commit: {target}")   # 污染 stdout!
        return {"affected_files": files, "exit_code": 0}

# ✅ 正确：日志走 stderr，stdout 只留给 JSON
import sys
def safe_revert(target, dry_run=True):
    if dry_run:
        print(f"[dry-run] 目标 commit: {target}", file=sys.stderr)
        return {"affected_files": files, "exit_code": 0}
```

### 约定（本仓库）
- **stdout = 机器数据**（JSON、结构化输出），必须纯净。
- **stderr = 人类日志**（提示、警告、`::error::`/`::notice::`）。
- CI 自检命令：`python scripts/xxx.py --json | python -c "import json,sys;json.load(sys.stdin)"`
- 案例：`safe_git_revert.py` 日志污染 `run_ci_guard --json` → commit `e859f22e` 修复。

---

## 三、坑 2：PowerShell 重定向 UTF-16 陷阱（本地验证假失败/假绿）

### 现象
本地跑 `python scripts/xxx.py --json > out.json`，然后 `json.load` 失败；
但 CI(bash) 却正常。

### 根因
Windows PowerShell 5.1 的 `>` 重定向**默认用 UTF-16LE 写文件**，而 CI 的 bash 用 UTF-8。
你本地"看到"的文件与 CI 的文件编码完全不同。

```powershell
# ❌ PowerShell 5.1：out.json 是 UTF-16LE，Python json.load(open(...)) 默认 UTF-8 读会崩
python scripts/run_ci_guard.py --json > out.json

# ✅ 正确 1：让 Python 自己写文件（bash 语义）
python -c "import subprocess,sys;subprocess.run([sys.executable,'scripts/run_ci_guard.py','--json'],check=True).stdout"
# ✅ 正确 2：管道直连解析，不经文件
python scripts/run_ci_guard.py --json 2>$null | python -c "import json,sys;print(json.load(sys.stdin)['overall'])"
# ✅ 正确 3：PowerShell 7+ 或显式 UTF-8
python scripts/run_ci_guard.py --json | Out-File -Encoding utf8 out.json
```

### 验证口诀
本地验证 JSON 输出**永远不要用 PowerShell 的 `>` 重定向判真伪**。
要么管道、要么 Python 内部写文件。

---

## 四、坑 3：未入库依赖 / .pyc 缓存陷阱

### 自查三步

```bash
# 1. 你 import 的每个模块，确认已入库
git ls-files | grep 你的模块名

# 2. 全仓库巡检（本仓库已提供工具）
python scripts/scan_missing_deps.py          # 文本
python scripts/scan_missing_deps.py --json   # 结构化
python scripts/scan_missing_deps.py --strict # 发现 LOST 即 exit 1（CI 守卫用）

# 3. 该工具分类说明
#    LOST[NEVER-COMMITTED]: 仅 .pyc 幸存, 源从未入库 → 必须补 add
#    LOST[HISTORY]:         曾入库后被删 → 确认是否应恢复
```

### 关键点
- `.pyc` 存在 ≠ 源文件存在。`.pyc` 不会进 git，CI 上不会有。
- 提交前 `git status` 确认所有新 `.py` 都在暂存区。
- 案例：`ci_guard_types.py` 仅存 `.pyc` 从未入库，`run_ci_guard --validate` 必挂 → commit `e859f22e` 重建。

---

## 五、坑 4：本地模拟 CI 的环境差异清单

| 项目 | 本地(PowerShell) | CI(bash) | 注意 |
|------|-----------------|----------|------|
| `>` 重定向编码 | UTF-16LE | UTF-8 | 见坑 2 |
| 已装 Python 包 | 可能多/少 | 只有 workflow 显式装的 | 以 `pip install` 清单为准 |
| 环境变量 | `.env`/系统 | 仅 workflow `env:` | 用不到就 `AssertionError` |
| 文件路径 | `\` 反斜杠 | `/` | `os.path.join` 别手写 |

**本仓库提供完整模拟工具**（按 CI bash 语义执行，规避上述差异）：

```bash
python scripts/simulate_ci_guard_pipeline.py          # 文本汇总
python scripts/simulate_ci_guard_pipeline.py --json   # 结构化
```

---

## 六、提交前 checklist

- [ ] `git status`：所有新 `.py` 已 add（无 `??` 的 `.py` 残留）
- [ ] 供 CI 解析的 JSON 输出：stdout 纯净（日志走 stderr）
- [ ] 本地验证 JSON 用管道或 Python 写文件，**不用 PowerShell `>`**
- [ ] 涉及重命名：`grep -r 旧名` 确认无引用失效
- [ ] 跑一次 `python scripts/simulate_ci_guard_pipeline.py` 全绿
- [ ] 跑一次 `python scripts/scan_missing_deps.py` 无新增 LOST

---

## 附：相关文件索引

| 文件 | 用途 |
|------|------|
| [publish_fix_to_docs.py](../../scripts/publish_fix_to_docs.py) | commit hash + 修复点 → 文档站 |
| [scan_missing_deps.py](../../scripts/scan_missing_deps.py) | 未入库依赖/.pyc 陷阱巡检 |
| [simulate_ci_guard_pipeline.py](../../scripts/simulate_ci_guard_pipeline.py) | 本地完整 CI 流水线模拟 |
| [ci_hidden_failure_fix_report_20260805.md](../../docs/observability/ci_hidden_failure_fix_report_20260805.md) | 本次隐患修复完整复盘 |
