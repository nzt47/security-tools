# START-S11-10 —— 架构环收口（R2 六文件批次）+ master 存量红灯 CI 收口（R9）

> **本文是自包含提示词**：新会话无任何前序上下文，所需事实、命令、证据与判据**全部写在本文内**。
> 直接整份复制到新会话即可执行。

---

## 〇、任务与背景（一段话）

`architecture-check`（架构规则校验）是**阻断合并**的门禁，现已**可复现地红**：稳定报
`违规总数 19 / 未豁免 15 / 已豁免 4`，全部是 `no_circular_dependency`，落在 4 个强连通分量（SCC）上。
前序会话（S11-09）已把"门禁不可复现"（同一 commit 因文件系统遍历顺序不同而报 20/16 或 15/11）
修好（`f3659113`：`_collect_python_files` 按 `Path.as_posix()` 排序），并把"30 模块重构"收敛为
**6 文件批次**、且**已在图上模拟验证**：按该批次拆除 16 个边对后 **SCC 归零、violations=0、active=0**。

**本任务两件事**：
- **R2**：落地这 6 文件批次，让 `architecture-check` **真正转绿**（不是靠豁免），并**回收**现有 4 条监控豁免。
- **R9**：收口 master 上**存量红**的 6 个 CI job（与本批次无关，前序已逐项归因）。

---

## 一、工作区与隔离（先做）

```powershell
cd C:\Users\Administrator\agent
$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'
python scripts/dev/new_session_worktree.py create --id s1110 --base master
```

- `--base` 默认是 `develop`，**必须显式写 `--base master`**；脚本会自动供给 `.env`（符号链接）。
- 之后**所有命令都在 `C:\Users\Administrator\agent\.worktrees\s1110` 内跑**（每次 pwsh 调用都是新进程，务必先 `cd`）。
- **主工作区禁令**：✗ `git checkout`　✗ `git reset --hard`　✗ `git add -A`（只 add 具体文件）。
- 提交信息含中文/反引号时，写进临时文件再 `git commit -F <文件> -- <具体路径>`。
- 提交流程：worktree 内 add/commit → 主工作区 `git merge s1110/main --no-edit` → 双远端
  `git push origin master` 与 `git push gitee master`。
- **证据一律落 worktree 内 `.tmp-s1110/`**（`.gitignore` 的 `.tmp-*/` 已覆盖，不入库；**不要**写进
  `docs/architecture/`，那会导致产物漂移）。

---

## 二、R2：落地 6 文件批次（核心任务）

### 2.1 已验证的目标：拆掉这 16 个边对 ⇒ 门禁真绿

前序已用生产代码在图上模拟（`.tmp-s1109/r2_full_sim.txt`）：

```
移除 16 个唯一边对（原 1581 条边实例 -> 保留 1565）
剩余非平凡 SCC: []
violations=0  active=0  exempted=0
passed(active==0) = True
```

**要拆的 16 个 `(source → target)` 边对，按文件分组如下**：

| # | 文件（改这里） | 要消除的边 | 条数 |
|---|---|---|---|
| 1 | `agent/repair/__init__.py` | `agent.repair → agent.repair.{diagnose, locate, pipeline, propose, verify}` | 5 |
| 2 | `agent/settings/__init__.py` | `agent.settings → agent.settings.{resolver, service}` | 2 |
| 3 | `agent/monitoring/observability_config.py` | `agent.monitoring.observability_config → agent.monitoring.config_observability` | 1 |
| 4 | `agent/audit/__init__.py` | `agent.audit → agent.audit.{chain, facade, logger, migration, ui_middleware}` | 5 |
| 5 | `agent/utils/cross_process_lock.py` | `agent.utils.cross_process_lock → agent.audit`；`→ agent.observability.events` | 2 |
| 6 | `agent/observability/`（新增叶子契约） | `agent.observability.trace_v2 → agent.observability.events`（**二选一**拆 `events ↔ trace_v2` 这个真 2 环） | 1 |

> 前 4 项的 13 条边本质同型：**包的 `__init__.py` 急切再导出子模块**，而子模块又（直接或间接）依赖回包。
> 这就是经典的"包 ↔ 子模块"环，`__init__` 惰性化是教科书解。

**其中第 3 项最独立、最容易，建议先做它**（不涉及惰性化、不涉及 `.pyi`）：
`agent/monitoring/observability_config.py:668` 有一处**函数内**的
`from agent.monitoring.config_observability import on_config_changed`；
而反向 `config_observability.py` 里对 `observability_config` **只有 docstring 提及、并非 import**（已核实）。
所以 SCC1 的环是靠 `observability_config → config_observability → {alert_notifier,loki,prometheus}
→ … → error_handler → observability_config` 兜起来的，**砍掉这一条边即整环消散**（模拟已证：最小拆除边集就是这 1 条）。
改法 = **依赖倒置/回调注册**：在 `observability_config` 里开一个
`register_config_change_hook(fn)`，由 `config_observability` 在导入时注册自己；
边方向变为 `config_observability → observability_config`，此时 `observability_config` 无出边 ⇒ 无环。
⚠️ **附带行为影响（必须声明）**：原来"首次 `set()` 时按需 import 配置观察层"会变成
"只有该层已被导入时才回调"——与硬约束 #3 同类。请给出"服务/测试运行期该层确已被导入"的证据。

**复现当前违规清单**（在任何时候都可自查）：

```powershell
python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent `
  --exemptions docs/architecture/legacy_exemptions.json --config config.yaml `
  --json-report .tmp-s1110/arch_before.json --md-report .tmp-s1110/arch_before.md
# 期望：exit=1，total=19 active=15 exempted=4
```

---

### 2.2 三条硬约束（前序未实施的原因，**必须先解决再动手**）

#### 硬约束 #1（最关键）：惰性 `__init__` 会让 mypy 失去静态类型，而 `TYPE_CHECKING` 补救会复活环

**已实测**（前序 `.tmp-s1109/mypy_pep562_probe.txt`，合成包对照臂）：把包 `__init__` 从"急切再导出"
改成 PEP 562 `__getattr__` 惰性再导出后，mypy **不会**报"模块没有该属性"，但 `from pkg import X`
拿到的 `X` 退化为 `Any`，于是消费者里出现**新错误**：

```
STATIC (现状)  mypy exit=0  Success: no issues found in 3 source files
LAZY  (拟改)   mypy exit=1  consumer.py:6: error: Returning Any from function declared to return "str"  [no-any-return]
```

**而"用 `if TYPE_CHECKING:` 补静态 import 来救 mypy"这条路在本仓走不通**：
`agent/observability/dependency_graph.py::_parse_imports` 用 `ast.walk(tree)` 遍历**整棵树**，
**包含 `if TYPE_CHECKING:` 块与函数体**（这条事实有仓库自身文档佐证：`agent/knowledge/index.py:43`
注释逐字写"按 AST Import/ImportFrom 节点统计依赖边（**含函数内/TYPE_CHECKING**）"）。
⇒ 在 `__init__.py` 里补 `TYPE_CHECKING` 再导出，**会把这 13 条边原样加回来，环复活**。

**候选解法（按推荐序，任选其一并在报告里写明取舍）**：

- **(a) 为惰性化的包补 `.pyi` 类型存根 —— ✅ 已裁定为默认路线（最容易且不返工）**：
  `agent/{repair,settings,audit}/__init__.pyi` 里写常规的 `from .sub import X as X` 再导出声明。
  mypy 见到 `.pyi` 就**不再看 `__init__.py`**，静态类型完整恢复；
  而 `_collect_python_files` 只扫 `rglob("*.py")` ⇒ **`.pyi` 不产生依赖边**。
  语义上是站得住的：`__init__.py` 的**运行期**急切依赖已真的消失，`.pyi` 只是**类型层声明**、不执行。
  ⚠️ 必须在报告里**显式声明这一取舍**（"边消失是因为运行期惰性化 + 类型声明放在不参与运行期依赖图的 `.pyi`"），
  并给出"改动前后运行期导入行为等价"的证据（见 2.3-2）。

  **为什么不做"裸惰性化"（不补存根）**——已实测规模，风险不划算：
  | 包 | 再导出名 ≈ | 消费方 `from <pkg> import` |
  |---|---|---|
  | `agent.settings` | 29 | 14 条 / 9 文件 |
  | `agent.audit` | 40 | 55 条 / 46 文件 |
  | `agent.repair` | 46 | 32 条 / 13 文件 |
  | **合计** | **≈115** | **101 条 / 68 文件** |

  裸惰性化会让这 101 处退化为 `Any`（探针已证 mypy 不报"无此属性"，但会在需要真类型的上下文里
  报 `no-any-return` 等**新错误**）⇒ 直接触碰"不得引入新 mypy 错误"门禁。补 `.pyi` 可**保证**
  与基线逐条持平。

  **省事做法（已备好脚本，直接粘贴运行）**：先照 §2.2 把 `__init__.py` 惰性化，再跑下面这个
  生成器自动产出 `.pyi`（它只读 `__init__.py` 的 `from … import …` 列表，逐名写成
  `X as X` 的显式再导出），最后用 mypy 与基线对比：

  ```python
  # .tmp-s1110/gen_reexport_pyi.py  —— 由 __init__.py 机械生成再导出存根
  from __future__ import annotations
  import re, sys
  from pathlib import Path

  IMPORT_RE = re.compile(r"^from\s+(?P<mod>[\w.]+)\s+import\s+(?P<names>\([^)]*\)|[^(\n]+)$",
                         re.MULTILINE)

  def names_of(raw: str) -> list[str]:
      raw = raw.strip()
      if raw.startswith("("):
          raw = raw[1:-1]
      out = []
      for part in raw.split(","):
          part = part.strip()
          if not part or part == "*":
              continue
          out.append(part.split(" as ")[-1].strip())   # 记录对外名
      return out

  def main(pkg_dir: str) -> int:
      init = Path(pkg_dir) / "__init__.py"
      text = init.read_text(encoding="utf-8")
      lines = ['"""自动生成的再导出类型存根（S11-10 / R2）。**仅供类型检查，不参与运行期。**',
               '',
               '本文件的存在意义：让 `__init__.py` 可以安全地做 PEP 562 惰性再导出',
               '（打断"包 ↔ 子模块"环），同时保住 mypy 的静态类型。',
               '改动 `__init__.py` 的再导出清单后，请重跑生成器。',
               '"""',
               'from __future__ import annotations', '']
      for m in IMPORT_RE.finditer(text):
          mod, ns = m.group("mod"), names_of(m.group("names"))
          if mod == "__future__":      # ← 必须跳过！否则会生成
              continue                 #   `from __future__ import (annotations as annotations)`（已实测踩到）
          if not ns:
              continue
          lines.append("from %s import (%s)" % (mod, ", ".join("%s as %s" % (n, n) for n in ns)))
      out = init.with_suffix(".pyi")
      out.write_text("\n".join(lines) + "\n", encoding="utf-8")
      print("written:", out, "(%d 行)" % len(lines))
      return 0

  if __name__ == "__main__":
      raise SystemExit(main(sys.argv[1]))
  ```
  用法：`python .tmp-s1110/gen_reexport_pyi.py agent/settings`（三个包各跑一次）。
  前序已用该脚本的**自检模式**在副本上试跑过（`.tmp-s1109/pyi_selftest/`），产出再导出名
  **settings 29 / repair 46 / audit 40**，与手工统计一致；**自检时踩到并修掉了
  `__future__` 那条坑**（见上面 `continue`），所以请保留该判断。
  ⚠️ 生成后**必须人工过一眼**（`__all__`、`TYPE_CHECKING` 分支、重名 `as` 别名），
  并确认 `python -c "import agent.settings"` 与 `from agent.settings import <名>` 均可用。
- **(b) 先量化 mypy 代价再决定**：实测"惰性化前后 mypy 错误数/文件数"的差值
  （基线：`python -m mypy <被测文件>`，**注意换回 HEAD 复测做对照臂**）。
  若新增为 0，可省掉 `.pyi`。（本仓存量约 549~550 条，判据是"**不得引入新错误**"。）
- **(c) 不惰性化 `__init__`，改为让子模块不再依赖包**：**前序已明确否决"仅换写法"这一形式**——
  把 `from agent.repair import gitio` 改成 `import agent.repair.gitio`，解析器的 target 变了，
  但**运行期完全一致**（两种写法都会执行包 `__init__`），并未消除任何耦合，属"隐瞒形状"。
  若要走 (c)，必须是**真的把共享物下沉到包外的叶子模块**（例如把 `masking`/`gitio` 这类被
  子模块共享的东西移出包，使导入它不再触发包的 `__init__`），并承担公共路径变更的兼容成本。

#### 硬约束 #2：`events ↔ trace_v2` 是真 2 环，**延迟加载无效**，只能抽叶子契约

**已实测反例**（前序 `.tmp-s1109/r2_edge_probe.txt`）：把 import 挪进函数体、或改用
`importlib.import_module('pkg.sub')`，**边依然存在**（后者 `import_type=dynamic`），
且 `_check_circular_dependencies` 使用**全部**边、从不筛 `is_dynamic`
（`dependency_graph.py` 里 `if is_dynamic:` 分支是空 `pass`）。

⇒ 唯一解：把 `events` 与 `trace_v2` **都要用的那部分**抽到一个**叶子契约模块**
（无项目内依赖），让两者都依赖契约、而不是互相依赖。

#### 硬约束 #3：`cross_process_lock` 的依赖倒置会**改变留痕的生效时机**（行为影响面，必须写进报告）

`agent/utils/cross_process_lock.py` 现在在**函数内**惰性导入 `agent.audit`（约 L349）与
`agent.observability.events`（约 L356），做"锁降级/争用留痕"，且两处都包在 `try/except` 里
（注释明写"审计不可用不阻断""事件不可用不阻断"）。

改成叶子契约 + 由 audit/observability 侧注册后：**留痕只在该层已被导入时生效**，
不再"首次锁争用时按需 import 审计"。在运行中的服务里 audit/observability 本就会被导入，
**实际影响≈0**，但这是**行为变化**，必须：
① 在报告里显式声明；② 给出"服务/测试运行期这些模块确已被导入"的证据；
③ 或选择保留按需语义的等价实现（若选后者，注意不要落入硬约束 #2 的"延迟加载无效"陷阱）。

---

### 2.3 动手前先建立的**回归护栏**（顺序很重要）

1. **先跑基线**并留档（后面全靠它做对照臂）：

```powershell
python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent `
  --exemptions docs/architecture/legacy_exemptions.json --config config.yaml `
  --json-report .tmp-s1110/arch_before.json --md-report .tmp-s1110/arch_before.md
python -m mypy <每个将被改动的模块> *> .tmp-s1110/mypy_before.txt   # 基线错误数
lint-imports --config .importlinter *> .tmp-s1110/importlinter_before.txt   # 期望 2 kept / 0 broken
python scripts/scan_kwarg_conflicts.py --path agent --min-risk HIGH
python scripts/scan_kwarg_conflicts.py --path tests  --min-risk HIGH   # 均期望 0 处
```

2. **每个包改完后立刻验证"运行期导入等价"**（这是硬约束 #1/(a) 的必需证据）：

```powershell
# 在**改动前**记录该包被导入后 sys.modules 里出现的 agent.* 模块集合
python -c "import agent.settings, sys; print(sorted(m for m in sys.modules if m.startswith('agent.settings')))" *> .tmp-s1110/mods_before.txt
# 改动后同样记录，**逐行对比**：应完全一致（惰性化不改变"被显式导入时"的模块集合）
python -c "import agent.settings, sys; print(sorted(m for m in sys.modules if m.startswith('agent.settings')))" *> .tmp-s1110/mods_after.txt
```

3. 每改一个包，跑该包的定向套件（至少 `tests/unit/test_<pkg>*.py`），再做下一步。

---

### 2.4 验收（缺一不可）

```powershell
# ① 门禁转绿（核心判据）
python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent `
  --exemptions docs/architecture/legacy_exemptions.json --config config.yaml `
  --json-report .tmp-s1110/arch_after.json --md-report .tmp-s1110/arch_after.md
# 期望：exit=0，且 total=0 active=0 exempted=0
```

- **② 回收 4 条监控豁免**：转绿后 `docs/architecture/legacy_exemptions.json` 里
  `agent.error_handler/monitoring.{prometheus,loki,alert_notifier} → agent.monitoring.observability_config`
  这 4 条**已无对应违规，应从豁免清单删除**（回收豁免 = 门禁变强，是本次的重要附带收益）。
  删完再跑一次 `--check`，仍须 `exit=0 / active=0`。
- **③ 可复现性不许回退**：前序已修好"遍历顺序影响结论"。本次必须复验——
  把 `Path.rglob` 打补丁成"同集合、顺序反转/随机置换"，跑完整 `validate()`，
  结论（total/active/exempted 与逐条违规、`graph_stats`）必须**逐字节一致**。
  可直接复用前序脚本思路（`.tmp-s1109/verify_determinism.py`）。
- **④ mypy 无新增错误**：改后错误数与"把改动文件字节级换回 HEAD"的基线**对比**，
  新增必须为 0（换回 HEAD 的写法：`python -c "import subprocess,pathlib; pathlib.Path(p).write_bytes(subprocess.run(['git','show','HEAD:'+p],capture_output=True).stdout)"`，
  跑完**务必还原**，用 `git diff --stat` 确认）。
- **⑤ 运行期导入等价**：见 2.3-2，给出逐行对比结论。
- **⑥ 邻接回归**：`agent.settings` 是全仓"开关中心"、`agent.audit`/`agent.repair` 是生产路径，
  故必须跑覆盖面较广的套件（至少 `tests/unit` 中 settings/audit/repair/observability/monitoring
  相关文件 + `tests/integration` 相关分片）。
  ⚠️ **本机跑全量 `tests/unit` 会挂起**（见 §四 环境事实），**权威全量回归交给 CI**：
  推到 master 后看 `ci.yml` 的 `单元测试 (Python 3.12 / Shard 1..6)` 与集成分片，
  判据是"**失败集与基线逐项相同**"（基线见 §三 的 R9）。
- **⑦ 真实 CI 端到端**：本批次会改 `agent/**` ⇒ push 后 `architecture-check` **自动触发**。
  期望该 run **`completed/success`**（这是本次的最终验收）。届时同时确认
  `硬编码密码扫描` 与 `知识库重构任务检查` 仍 `success`（前序已绿，别弄回红）。

---

## 三、R9：master 存量红灯 CI（与本批次无关，但需一并收口）

### 3.1 事实与已完成的归因

`ci.yml`（云枢系统测试流程）在 master 上长期有 **6 个 job 红**。已用**两个独立证据**证明它们是**存量**、与本会话改动无关：

- **失败 job 集逐项相同**：`894f10b9`（会话前 master）与更早的 `d4afed3a` 的失败 job 集
  与改后完全一致 —— 均为：`代码质量检查`、`单元测试 (Python 3.12 / Shard 1,2,4,5)`、`文档链接预检与锚点回归测试`。
- **Shard 2 的 outcome 计数逐位相同**（最强证据）：

| 运行 | 结论行 | 失败明细 |
|---|---|---|
| `a0822391`（改动前，job `104446916416`） | `1 failed, 2958 passed, 3 skipped, 10 warnings, 6 errors in 217.72s` | `TestGitioReadonly` ×6 ERROR + `test_env_isolation_p0::test_network_config_update_does_not_touch_repo_dotenv` FAILED |
| `b54c3174`（改动后，job `104465950172`） | `1 failed, 2958 passed, 3 skipped, 10 warnings, 6 errors in 158.86s` | **完全相同的 7 条**（测试名与断言消息逐字一致） |

同时：`Shard 3`、`Shard 6`、**全部 4 个集成测试分片**、`知识库审计 CLI 冒烟` 均为 success。

### 3.2 已定位的两例根因（**都是测试自身的环境假设**，不是产品缺陷）

- `tests/unit/test_repair_trace_git.py::TestGitioReadonly`（6 个 ERROR，断言消息"夹具前提失败：该目录应位于 git 仓库之外"）：
  夹具用 `os.environ.get("SystemRoot")` 选临时目录基址 —— **`SystemRoot` 是仅 Windows 存在的环境变量**，
  Linux CI 上取空 ⇒ 走 `mkdtemp(dir=None)`，与"该目录应位于 git 仓库之外"的前提不一致。
- `tests/unit/test_env_isolation_p0.py::test_network_config_update_does_not_touch_repo_dotenv`
  （断言"隔离目标文件未收到写入 —— 同上：这不是重定向，是掩盖"）：依赖 CI 上 `CP_ENV_FILE`
  的注入方式/取值口径。

### 3.3 R9 的要求与红线

- 逐个 job 定位根因并**修根因**：`单元测试 Shard 1/2/4/5`（先跑出各分片的失败清单，
  可用 `gh api repos/nzt47/security-tools/actions/jobs/<job_id>/logs`）、
  `代码质量检查`、`文档链接预检与锚点回归测试`。
- **红线（与 S11-09 同纪律）**：
  - ✗ 不放宽断言、不 `xfail`/`skip` 真实失败、不改脚本跳过检查；
  - ✗ 不为让 job 变绿而伪造产物或删测试；
  - 判断"是环境假设问题还是产品缺陷"必须**带对照臂**，并写清依据；
  - 改不到就**如实上报并保持红**，宁可留真实红灯，不留假绿灯。
- 若某 job 的修复超出本任务范围（例如需要产品行为变更），**记录为遗留 + 归属 + 建议**，不要硬改。

---

## 四、环境事实（前序实测，能省你大量时间）

- **本机 `python` = 3.12.0**；CI = 3.12.x（Linux）。
- **本机跑全量 `tests/unit` 会挂起**：在 `tests/unit/test_resource_monitor.py` 触发 `--timeout=600`，
  堆栈停在 `psutil/_common.py:isfile_strict` ← `agent/human_in_the_loop/takeover_queue.py:272 _sweep_loop`。
  与前序改动无关，属**本机环境的 psutil 进程枚举**问题 ⇒ 全量回归认 CI，别在本机硬跑。
- **网络**：`github.com` 直连不通（`curl`/`Invoke-WebRequest` 会超时/重置），但 **`gh` CLI 有网**
  （`gh run view --log`、`gh api`、`gh release download`、`gh run watch` 都可用；已登录账号有 repo 权限）。
  ⇒ 取 CI 日志、下载二进制、触发 `workflow_dispatch` 一律用 `gh`。
- **WSL 可用**（`Ubuntu-24.04`，但 WSL 内**无外网**，可读 `/mnt/c`）。前序用它做 Linux 等价复现
  （gitleaks 8.18.1：Windows 二进制因路径分隔符会让 config 白名单失效，本地 117 条 vs CI 1 条）。
- **PowerShell 跑 Python 前设** `$env:PYTHONIOENCODING='utf-8'; $env:PYTHONUTF8='1'`。
- `gh run list` 的 `--template` 会把大整数 run id 转成科学计数法**导致 ID 失真**；
  取 ID 一律用 `--jq '.[] | "\(.databaseId)"'`（或 `--json` + `jq`）。
- **改了 `agent/**` 或 `config.yaml` 需重启 127.0.0.1:5678 才生效**；重启属运维动作，
  **不要擅自重启**，在报告里写清"是否需要重启"。本批次若只改
  `agent/observability`/`agent/monitoring`/包 `__init__` 的**惰性化**，请**逐条核实导入链**后
  再下结论（前序的判据：`git grep` 找实际 import 方，注释/文档字符串里的提及不算）。

---

## 五、门禁与纪律（逐条落证据）

- **分类实验必须含对照臂**：判"真炸弹 vs 工具伪影"要跑
  **不平移 / CONTROL（`CP_DATE_SHIFT_CONTROL=1`）/ +400 / −400** 四档；只有平移档无法区分二者。
- **先把输出落文件再读**（管道会截断）；证据统一放 `.tmp-s1110/`。
- **不得为了让检查变绿而放宽**：不放宽断言、不批量豁免架构违规、不改脚本跳过检查、
  不放宽 gitleaks 正则到 `sk-.*`。
- **不得编造数字**：每条结论可复现；做不到写"未验证/未追因"；
  **"数字变化"与"口径变化"必须分开讲**；推算值与实测值必须标明性质。
- **新增任何 env 读取点必须登记** `agent/settings/registry.py`（否则 `TestMechanicalZeroGap` 变红）。
- **测试隔离红线**：不得写仓库根 `.env`（沿用 `CP_ENV_FILE` 隔离）；需要落盘的用例显式传路径。
- **门禁四条（缺一不可）**：相关套件 + 邻接回归；`python scripts/scan_kwarg_conflicts.py --path agent
  --min-risk HIGH` 与 `--path tests` 各 0 处；`python -m mypy <改动模块>`（**无新增错误**）；
  `lint-imports --config .importlinter`（2 kept / 0 broken）；跑完 `git status` 检查产物漂移。
- **反模式清单（前序已明确否决，别重走）**：
  1. **仅换 import 写法**（`from pkg import sub` → `import pkg.sub`）来消边 ⇒ 运行期完全一致，
     只是换个方式暴露解析器不精确 ⇒ **"隐瞒形状"，不采**。
  2. **延迟加载/`lazy_loader`** 来消环 ⇒ 已实测**仍计边**（函数内 `importlib.import_module('x.y')`
     也记边），对环检测**无效**。
  3. **批量豁免**整个 SCC ⇒ 禁止（要豁免就必须覆盖该 SCC 的**全部**环内边，否则换个遍历顺序
     就有未豁免的边冒出来 = 假绿灯）。
  4. **改检测器/放宽门禁** 来变绿 ⇒ 禁止。

---

## 六、回报格式

- **交付物**：改动文件清单（含新增的叶子契约/`.pyi` 存根）。
- **验收逐条**：给命令 + **原始输出**（①门禁 `exit=0/active=0`；②4 条豁免已回收且仍绿；
  ③可复现性复验；④mypy 无新增；⑤运行期导入等价；⑥邻接回归；⑦真实 CI `architecture-check` success）。
- **根因（代码行级）**：每条边的成因与它被消除的机制。
- **行为影响面**：硬约束 #3 的留痕时机变化、以及任何其它行为差异，**逐条声明**。
- **质量证据**：套件与门禁四条的原始结果。
- **遗留**：带归属与建议（含 R9 未修完的部分）。
- **文档更新**：新增/回填 S11-10 交付结案报告（放 `docs/zh/CloudPivot_v7.2重构计划/`）。
- **双远端 SHA**：`git rev-parse HEAD` / `origin/master` / `gitee/master` 三点一致。
- **是否需要重启**：明确结论 + 依据。

---

## 七、本任务的边界（不要越界）

- 前序 S11-09 已完成并**复核通过**的项**不要重做**：gitleaks 逐行放行（1a）、
  知识库门禁契约口径变更（1c）、四臂定向面补两文件（任务 2）、±1 口径追因（任务 3）、
  门禁可复现性修复（方案 A）。
- 本任务**只做** R2（6 文件批次）+ R9（存量红灯 CI）。
- 若执行中发现 6 文件清单不足（例如改完后仍有残留环），**以"门禁 `active=0`"为唯一判据**，
  继续按同一原则（依赖倒置 / 叶子契约 / 惰性再导出）补齐，并在报告里说明新增了哪几条边、
  为什么原清单不足。
