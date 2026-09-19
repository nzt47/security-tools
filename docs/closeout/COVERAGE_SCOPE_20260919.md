# 覆盖率口径统一（D1 · 2026-09-19）

> **任务**：统一覆盖率口径，解除 TASK-04「覆盖率不得下降」断言的阻塞。
> **结论先行**：权威口径 = **9 个包 + 行覆盖（branch 关闭）**，分母由**采集期**的 `--cov=` 决定；
> 历史四个数字中，**只有 CI 门禁 40% 与 77.20% 同口径可比，49.08% 不可比**；
> TASK-04 可用 `scripts/check_coverage_regression.py` 做断言（带口径一致性硬校验，退出码 3 = 拒绝比较）。
>
> 本文所有数字都是**本机实测**，命令与产物路径均已给出。凡未实测的一律标注「未测」。

---

## 1. 结论速查（给 TASK-04 的接口）

| 项 | 值 |
|---|---|
| **权威口径** | `pyproject.toml [tool.coverage.run].source` 的 **9 个包**：`agent` / `sensor` / `memory` / `planning` / `persona` / `core` / `cognitive` / `lifetrace` / `utils` |
| 覆盖类型 | **行覆盖（statement）**；`branch` 未启用 |
| omit | `*/tests/*`、`*/scripts/*` |
| 有效分母的决定者 | **采集期的 `--cov=` 列表**（不是报告期的 `source`）——见 §2 |
| **权威命令** | `python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_authoritative` |
| 产物 | `<out>/coverage.json`（含 `packages` 口径字段，供断言脚本消费）+ `coverage.xml` + 逐块日志 |
| **TASK-04 断言** | `python scripts/check_coverage_regression.py --baseline coverage_baseline.json --current <out>/coverage.json --fail-under 40` |
| 断言退出码 | `0` 通过 / `1` 回归或低于下限 / `2` 用法错误 / **`3` 口径不一致（拒绝比较，不是通过）** |
| **当前基线值** | **74.13%**（100,637 / 135,752 行，9 个包，2026-09-19 实测，10/10 块跑完、0 文件丢失） |
| 历史口径对照 | 49.08%（2026-07-11，9 源但 omit 未生效、agent 仅 56k 行）❌ 不可比 ｜ 77.20%（2026-09-19 `agent` 单包）⚠️ 与门禁 40% 同口径 ｜ 门禁 40% |

---

## 2. 机制：为什么"改 pyproject 的 source"没有效果

这是本次最关键的发现，也是"三个数字互不可比"的根因。

### 2.1 实测：分母由采集期的 `--cov=` 决定

裸 coverage 最小复现（`pyproject` 声明 `source = ["pkg_a", "pkg_b"]`，`runner.py` 只 `import pkg_a`）：

```text
$ coverage run runner.py                      # 采集期 source 继承配置 = pkg_a + pkg_b
$ coverage report
Name           Stmts   Miss  Cover
pkg_a\mod.py       4      0   100%
pkg_b\mod.py       6      6     0%     ← pkg_b 进了分母（0%）

$ coverage run --source=pkg_a runner.py       # 采集期 source 只有 pkg_a
$ coverage report
pkg_a\mod.py       4      0   100%           ← pkg_b 完全消失
```

⇒ **覆盖率的"未执行文件"是采集期由 tracer 的文件定位器扫描 `--cov=` 指定目录得到的；
报告期 `coverage report` 会读配置里的 `source`，但不会再补扫这些目录。**

### 2.2 在本仓的实测后果

| 采集方式 | XML 根 `lines-valid` | 说明 | 产物 |
|---|---|---|---|
| `pytest --cov=agent` + `coverage xml`（**CI 分片现状**） | **120,703** | `agent/` 120,676 + 一个被测试意外 import 的 `cognitive/__init__.py` 27 行；其余 7 个声明包**一行都没进分母** | `_ci_logs/d1/testB.xml` |
| `coverage run --source=<9 个包>`（正确写法） | **135,541** | 9 个包全部进分母 | `_ci_logs/d1/nine.xml` |

⇒ **只写 `--cov=agent` 时，pyproject 里那份"9 个包"的声明会静默退化成"1 个包"。**
这正是"改了配置却毫无效果"的典型陷阱。

### 2.3 9 个包的实测规模分解

`coverage run --source=agent,sensor,memory,planning,persona,core,cognitive,lifetrace,utils` 实测：

| 包 | 文件数 | 有效行（statement） |
|---|---:|---:|
| `agent` | 594 | 120,754 |
| `sensor` | 32 | 7,555 |
| `planning` | 23 | 3,160 |
| `memory` | 8 | 1,662 |
| `persona` | 3 | 883 |
| `cognitive` | 14 | 493 |
| `lifetrace` | 4 | 442 |
| `utils` | 2 | 289 |
| `core` | 1 | 34 |
| **合计（class 行数求和）** | **681** | **135,272** |

> 口径注记：XML 根属性 `lines-valid = 135,541`，比逐 `<class>` 求和多 **269 行**（0.2%）。
> 差异出现在"多 source 收集"的 `nine.xml` 上，单 source 的三个 XML（`testB.xml` /
> `task03/coverage_merged_final.xml` / 历史 `coverage.xml`）根属性与求和**完全相等**。
> 本任务未追到该 269 行的确切来源，**如实记录**；断言脚本统一使用 XML 根属性（即
> `coverage report` 打印的那个数），不依赖逐文件求和。

### 2.4 反面对照：pyproject 的 source 是"报告期配置"

```text
$ python -m coverage debug config          # 本机 coverage 7.15.4
config_file: C:\Users\Administrator\agent\pyproject.toml
config_files_attempted: .coveragerc / .coveragerc.toml / setup.cfg / tox.ini / pyproject.toml
branch: False            ← pytest.ini 里写的 `branch = True` 未生效
parallel: False          ← pytest.ini 里写的 `parallel = True` 未生效
source: agent sensor memory planning persona core cognitive lifetrace utils   ← 9 个
run_omit: */tests/*  */scripts/*
fail_under: 0.0
```

---

## 3. 四个历史数字的**确切口径**与可比性判定

| # | 数字 | 来源 | 采集时间 | 统计范围（实测核对） | 分支 | 可比性判定 |
|---|---|---|---|---|---|---|
| 1 | **49.08%**<br>27,696 / 56,432 | `coverage.xml`（`git show HEAD:coverage.xml`） | 2026-07-11<br>(ts 1783789633361) | `<sources>` = 同名 9 个包，353 个 class；**但含 `agent/tests/` 32 文件 / 5,661 行 @0.99%**（omit `*/tests/*` 当时未生效，该 omit 是 2026-08-09 才修的） | 未启用 | ❌ **与今天任何数字都不可比**：① 时间差 2 个月，`agent/` 已从 ~353 文件/56k 行涨到 595 文件/121k 行（新增 `digestion`/`knowledge`/`policy`/`retention`/`repair` 等整包）；② omit 规则不同（把测试代码算进了分母） |
| 2 | **77.20%**<br>93,337 / 120,905 | `_ci_logs/task03/coverage_merged_final.xml` | 2026-09-19 | **`agent/` 单包**（`--cov=agent` 采集）+ 意外 import 的 `cognitive/__init__.py` 27 行；omit 生效 | 未启用 | ⚠️ **与 #3 同口径可比；与 #1 不可比** |
| 3 | **门禁 40%**<br>`--fail-under=40` | `.github/workflows/ci.yml`<br>`coverage-check` job | — | 合并 6 个分片数据后 `coverage report`。**分片采集只写 `--cov=agent`** ⇒ 实际统计范围 = #2 | 未启用 | ✅ **与 #2 同口径**（见下方纠正） |
| 4 | **`fail_under = 0`**<br>（配置里的数字） | `pyproject.toml [tool.coverage.report]` | — | `coverage report` 的默认阈值；CI 用 CLI 覆盖成 40 | — | ⚠️ **不是覆盖率数字，是阈值**；与 #3 的 40 冲突，见 §5 |

### 3.1 必须纠正的一条现有结论

`docs/closeout/BASELINE_20260918.md` §3.3 的结论（2）与（4）称：

> 「**不能用它去宣称"40% 门禁通过"**：门禁统计 9 个包，本次只统计了 1 个（最大那个）。
> 典型情况下加入 `memory/`、`planning/` 等外围包后总覆盖率会**下降**。」
> 「要给 TASK-04~08 一个可直接比对的数字，应改为按 9 包口径再测一次」

**该结论的第 1 条经本次实测不成立**：
门禁 job 的 `coverage report` 读的是 pyproject 的 9 包 `source`，但**数据里没有**外围 8 个包
（采集期只扫了 `agent/`），而报告期不补扫 ⇒ **门禁实际统计的就是 agent 单包**，
与 77.20% **同口径**。实测证据：`_ci_logs/d1/testB.xml` 用 CI 同款路径采集，
分母 120,703，其中外围 8 包合计仅 27 行（一个被测试意外 import 的文件）。

**该结论的第 2 条（应按 9 包口径再测）是正确的方向**，本次已实现（§4）——
但它的意义不是"让门禁口径对上"，而是"**让门禁真的覆盖它声明要覆盖的 9 个包**"，
即修复一个**覆盖率盲区**：今天 `sensor/`、`planning/`、`memory/`、`persona/`、
`lifetrace/`、`utils/`、`core/` 的 14,787 行**从未被任何覆盖率门禁看到**。

### 3.2 把门禁改成真 9 包口径**不会**打破 40% 门禁（已核算）

分片实测 agent 单包 = 77.20%（93,337 / 120,905）。换成 9 包口径后：
分子只增不减（≥ 93,337），分母升到 135,541 ⇒ **最坏情况**（外围 8 包 0% 覆盖）
仍有 `93,337 / 135,541 = 68.86%` ≫ 40%。

⇒ 该改动是**收紧**（覆盖更多包），且**不可能**把门禁弄红。据此已在
`.github/workflows/ci.yml` 的分片步骤把 `--cov=agent` 展开为 9 个 `--cov=`。

---

## 4. 权威覆盖率命令（并已实测跑通）

### 4.1 命令

```powershell
cd C:\Users\Administrator\agent

# 权威口径（9 个包，口径自动从 pyproject.toml 读取，不可能与声明分叉）
python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_authoritative

# 子集冒烟（**单包口径，数字不可与全量比较**；脚本会在摘要里标注 subset-single-package）
python scripts/run_authoritative_coverage.py --packages agent --out _ci_logs/cov_agent_only
```

`run_authoritative_coverage.py` 的三条设计要点（都是为了消灭本次发现的两类事故）：

1. **口径唯一真相源**：`read_declared_packages()` 直接从 `pyproject.toml` 读
   `[tool.coverage.run].source` 并展开成 9 个 `--cov=`，脚本里**不硬编码**包名。
2. **分块 + 逐块完整性校验**：沿用 TASK-03 的 round-robin 10 块 + `--cov-append`；
   每块跑完检查日志里**有没有 pytest 结束摘要**（被 `pytest-timeout` thread 法
   强杀的进程没有摘要）。有块未跑完 ⇒ 落盘 `incomplete_files.txt` 并**退出码 1**，
   该数据禁止当基线（详见 `TEST_TIMEOUT_20260919.md`）。
3. **不污染仓库根**：`COVERAGE_FILE` 指向产物目录。

### 4.2 本次实测（结果与产物）

见 §4.3。原始产物：

| 产物 | 说明 |
|---|---|
| `_ci_logs/coverage_authoritative/chunks.json` | 逐块 rc / 耗时 / `completed` / 证据行 |
| `_ci_logs/coverage_authoritative/coverage.json` | **权威测量值 + 口径标签**（断言脚本的输入） |
| `_ci_logs/coverage_authoritative/coverage.xml` | 完整 XML |
| `_ci_logs/coverage_authoritative/chunk_*.log` | 逐块原始日志 |
| `_ci_logs/d1/nine.xml`、`_ci_logs/d1/nine_analysis.txt` | 9 包分母的旁证（快速探针，非全量） |
| `_ci_logs/d1/testB.xml`、`_ci_logs/d1/testB_analysis.txt` | **CI 同款采集路径**的复现（证明门禁实为单包口径） |
| `_ci_logs/d1/analyze_cov_xml2.py` | XML 口径解析器（按 `<sources>` 还原绝对路径，避免 9 个 source 的同名目录混淆） |

### 4.3 权威口径全量实测结果（**已跑通**）

```text
$ python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth
口径（包）    : 9 个 → ['agent','sensor','memory','planning','persona','core','cognitive','lifetrace','utils']
口径标注      : 全量口径（与 pyproject 声明一致）
测试文件      : 632 个（tests/unit）
分块          : 10 块 × 约 63 文件
超时          : --timeout=300 --timeout-method=thread

总块数        : 10
已完成        : 10
未跑完        : 0 → []
受影响文件    : 0 个（从未执行）

line-rate     : 74.13%  (100637/135752 行)
branch-rate   : 0.00%  (branches-valid=0)
✔ 所有块均已跑完，数据可用于基线。
```

| 结论项 | 值 |
|---|---|
| **权威覆盖率（9 个包）** | **74.13%**（100,637 / 135,752 行），行覆盖，branch 关闭 |
| 总耗时 | **5,573 s ≈ 93 分钟**（10 块串行，逐块 205 s ~ 1,858 s） |
| 丢文件 | **0 个**（10/10 块正常收尾） |
| 测试用例 | 约 19,000（逐块摘要合计：1,695+1,428+1,875+1,628+2,137+1,982+2,253+1,825+2,180+2,001 passed 等） |

**逐包分解（关键发现）**：

| 包 | 文件 | 有效行 | 覆盖行 | 覆盖率 |
|---|---:|---:|---:|---:|
| `agent` | 595 | 120,963 | 93,747 | **77.50%** |
| `persona` | 3 | 883 | 809 | 91.62% |
| `planning` | 23 | 3,160 | 2,836 | 89.75% |
| `lifetrace` | 4 | 442 | 345 | 78.05% |
| `memory` | 8 | 1,664 | 860 | 51.68% |
| `utils` | 2 | 289 | 128 | 44.29% |
| `sensor` | 32 | 7,555 | 1,606 | **21.26%** |
| `cognitive` | 14 | 493 | 57 | **11.56%** |
| `core` | 1 | 34 | 0 | **0.00%** |
| **合计（逐 class 求和）** | 682 | 135,483 | 100,388 | 74.10% |

> ⚠️ **这条正是本次口径统一的真正价值**：`sensor/`（7,555 行，21.26%）、
> `cognitive/`（493 行，11.56%）、`core/`（34 行，0%）**在旧口径下从未进入过任何覆盖率分母**。
> 门禁 40% 之所以看起来"余量很大"，一部分原因就是**它根本没在测这些包**。

**与 77.20% 的口径对齐核对**：同一次全量运行里 `agent` 单包 = **77.50%**
（93,747 / 120,963），与 TASK-03 的 **77.20%**（93,337 / 120,905）
是**同一口径**（agent 单包、行覆盖、同 omit）⇒ **+0.30pp，未下降**。
分母从 120,905 → 120,963（+58 行）是因为作业期间仓库仍在被其它子任务改动。

> 口径漂移注记（诚实记录）：快速探针测得 9 包分母 = **135,541**，全量实测 = **135,752**
> （差 211 行 / 1 个文件）。二者命令等价，差异来自**采集中途仓库源码被并发修改**
> （本仓库当前有多个并行任务在写文件）。⇒ 用本方案做断言时，
> **基线必须与断言跑在"同一个仓库状态 + 同一条命令"上**（§7.5 同理）。

---

## 5. `fail_under` 与实际门禁不一致：指出与统一建议

### 5.1 实测到的全部阈值（**五处不一致 + 一处注释失真**）

| 位置 | 阈值 | 作用对象 | 是否生效 |
|---|---:|---|---|
| `pyproject.toml [tool.coverage.report] fail_under` | **0** | `coverage report` 默认 | ✅ 生效（被 CLI 覆盖） |
| `.github/workflows/ci.yml` `env.COVERAGE_THRESHOLD` + `coverage report --fail-under=${{...}}` | **40** | 合并 6 分片后的全项目数据 | ✅ 生效 |
| `.github/workflows/coverage-ci.yml` `env.COVERAGE_THRESHOLD` | **40** | 同左（另一个 workflow） | ✅ 生效 |
| `.github/workflows/test.yml` `env.COVERAGE_THRESHOLD` | **70** | unit+integration 合并数据 | ✅ 生效 |
| `.github/workflows/web-module-tests.yml` | **100** | `agent.web.processor` / `agent.web.crawler_control` 单模块 | ✅ 生效 |
| `scripts/check_scripts_coverage.py` 默认 | **50** | `scripts/` 层（独立体系 `.coveragerc_scripts`） | ✅ 生效 |
| `.github/workflows/observability-ci.yml:373` 注释 | 声称「pyproject.toml 中的 fail_under=40」 | —— | ❌ **注释已过期失真**，pyproject 现在是 0 |

另注：`test.yml` 与 `ci.yml` 的 workflow **显示名完全相同**（都是「云枢系统测试流程」），
却挂着 70 与 40 两个不同阈值——这是另一处"两个真相源"。

### 5.2 为什么本任务**故意没有**把 `pyproject.toml` 改成 40

看似最直接的统一办法是"把 pyproject 的 `fail_under` 改成 40，让 CI 去掉 `--fail-under`"。
**这条在本仓是不可验证的高风险改动**，依据是源码实测：

```python
# site-packages/pytest_cov/plugin.py:270-271（实测原文）
if self.options.cov_fail_under is None and hasattr(cov_config, 'fail_under'):
    self.options.cov_fail_under = cov_config.fail_under
```

⇒ pytest-cov 在没有显式 `--cov-fail-under` 时会**继承** coverage 配置里的 `fail_under`。
而仓库存量存在**多个只写 `--cov=`、不写 `--cov-fail-under=` 的 job**，例如：

* `.github/workflows/tool-tests.yml:77`（`--cov=agent.system_tools --cov=agent.pdf_tools …`）
* `.github/workflows/daily_regression.yml:444`（`--cov=scripts/finetune_reranker`）

这些 job 测的是**子集**（必然远低于 40），一旦 pyproject 变成 40，它们会**全部变红**。
这是"改一处、炸多处"，且本机无法验证（CI 在 Linux runner）。
⇒ 按"不做不可验证改动"的纪律，本任务只**登记 + 给出可验证的迁移步骤**。

### 5.3 统一建议（按安全顺序）

1. **先做（零风险，本次已做）**：把**门禁口径**统一——CI 分片的 `--cov=` 展开为 pyproject 声明的 9 个包，
   并用守卫测试锁死二者一致（`tests/unit/test_coverage_scope_consistency.py`）。
   口径不一致是本任务认定的**首要问题**；阈值不一致是次要问题。
2. **再做（低风险）**：给 §5.1 里那些"子集 job"逐个补显式 `--cov-fail-under=0`
   （与 `observability-ci.yml:376`、`log-perf-guard.yml`、`ci-cd.yml:139` 的既有做法一致），
   然后才能谈把 pyproject 的 `fail_under` 提为 40。**这一步是第 3 步的前置**，缺了它就会炸。
3. **然后（需要 §5.2 前置完成）**：`pyproject.toml fail_under = 40`，
   同时把 `ci.yml` / `coverage-ci.yml` 的 `coverage report --fail-under=${{ env.COVERAGE_THRESHOLD }}`
   改为裸 `coverage report`（阈值从配置读）。收益：`coverage report` **本地也生效**，
   不再出现"本地永远绿、CI 才红"。
4. **顺带**：修掉 `observability-ci.yml:373` 的过期注释；
   给 `test.yml` 与 `ci.yml` 的同名 workflow 改名（唯一性），并复核 `test.yml` 的 70 是否仍有依据。
5. **禁止**：把任何阈值往下调来"解决"覆盖率下降——本次 D1 的立场是**只收紧**（9 包 > 1 包）。

---

## 6. `pytest.ini` 里的 `[coverage:*]` 三节：已核实为死配置并删除

| 项 | 内容 |
|---|---|
| 位置 | `pytest.ini:109-136`（`[coverage:run]` / `[coverage:report]` / `[coverage:html]`） |
| 声明的口径 | `source = agent, core, memory, persona, planning, lifetrace`（**6 个包**）、`branch = True`、`parallel = True`、另一套 `omit` 与 `exclude_lines` |
| 是否生效 | ❌ **全部不生效**。`coverage debug config` 的 `config_files_attempted` = `.coveragerc` / `.coveragerc.toml` / `setup.cfg` / `tox.ini` / `pyproject.toml`，**不含 `pytest.ini`** |
| 处置 | **删除**，并留一段说明指向 `pyproject.toml` |
| 理由 | 它不是"无用的死代码"，而是**第三份互相矛盾的口径声明**（6 包 / branch 开）。留着会让人以为 branch 覆盖是开的，也让"按 6 包测一次"看起来有依据 |

### 6.1 一个**故意没做**的动作

那三节里的 `exclude_lines`（`def __repr__` / `raise NotImplementedError` /
`if __name__ == "__main__"` / `@abstractmethod` / `pragma: no cover`）**同样从未生效**。
它们会把一批语句排除出分母 ⇒ 补进 `pyproject.toml` 会让覆盖率**凭空上升**。

**本次不补**，理由：49.08% 与 77.20% 都是在"没有这些 exclude"的口径下测出来的；
补进去属于**口径变更**而非修复，会让新数字与历史基线不可比。
若要启用，必须同时重建基线并在此文档登记（登记为债务，见 §7）。

---

## 7. TASK-04 可用的「覆盖率不得下降」断言方案

### 7.1 用法

```powershell
cd C:\Users\Administrator\agent

# 1) 采集本次（权威口径）
python scripts/run_authoritative_coverage.py --out _ci_logs\coverage_authoritative

# 2) 与基线比对（默认容忍下降 0.00pp ⇒ 只允许持平或上升）
python scripts/check_coverage_regression.py `
  --baseline coverage_baseline.json `
  --current  _ci_logs\coverage_authoritative\coverage.json `
  --fail-under 40
```

### 7.2 设计要点（为什么不能只比百分比）

* **口径一致性是硬门禁**：`packages` 列表不同、或 `branches_valid` 一开一关 ⇒
  **退出码 3，拒绝比较**。这正是历史 49.08% 与 77.20% 被误比的场景。
* **默认容忍 0.00pp**：掉 0.01pp 也红。"慢慢烂下去"没有豁免。
* **拒绝从 XML 推断口径**：`coverage.xml` 的 `<sources>` 记录的是**报告期配置**，
  与**采集期 `--cov=`** 实测会分叉（§2.2 已证），所以默认拒绝用 XML 当基线；
  确需使用时必须显式 `--allow-inferred-scope` 并承担风险。
* **绝不静默通过**：任何不确定（口径未知、文件缺失、解析失败）都走非零退出（2 或 3）。

### 7.3 负例实测（**这是本方案的核心**）

`scripts/check_coverage_regression.py` 的退出码矩阵，由
`python _ci_logs/d1/demo_coverage_regression.py` 一键重放（原始日志 `_ci_logs/d1/demo/`）：

| 用例 | 输入 | 期望退出码 | **实测退出码** | 判定 |
|---|---|---:|---:|---|
| A 无下降 | 70.00% → 70.40% | 0 | **0** | ✔ |
| **B 人为下降 5pp** | 70.00% → 65.00% | 1 | **1** | ✔ |
| **C 口径不同**（9 包 → 1 包） | 70.00% → 80.00% | 3 | **3** | ✔ |
| **D 口径从 XML 推断** | XML → JSON | 3 | **3** | ✔ |
| E 下降 5pp 但容忍 6pp | 70.00% → 65.00%，`--tolerance 6` | 0 | **0** | ✔ |
| F 下降至 65 且低于下限 70 | `--fail-under 70` | 1 | **1** | ✔ |
| G 基线文件不存在 | —— | 2 | **2** | ✔ |

**用例 B 的实际输出**（非零退出的直接证据）：

```text
覆盖率不得下降断言
基线      : ...\baseline.json
          line_rate=70.00%  (94690/135272)  口径来源=explicit
本次      : ...\cur_lower.json
          line_rate=65.00%  (87927/135272)  口径来源=explicit
容忍下降  : 0.00 pp
✔ 口径一致：9 个包 ['agent','cognitive','core','lifetrace','memory','persona','planning','sensor','utils']；分支覆盖=关
基线      :    70.00%
本次      :    65.00%
变化      :    -5.00 pp   （容忍下降 0.00 pp）
✗ **覆盖率下降**：-5.00 pp 超过容忍值 -0.00 pp
```

**用例 C 的实际输出**（口径不一致被拒绝，而不是给出"覆盖率还涨了"的假通过）：

```text
✔/✗ 包列表不同（基线独有 ['cognitive','core',...]；本次独有 []）
✗ 拒绝比较：口径不同的两个数字相减没有任何含义。
```

### 7.3b 真实数据上的负例（不是构造的）

用**本次真实基线**（9 包 74.13%）去比 TASK-03 那份**真实**的 agent 单包 77.20%
（`_ci_logs/d1/demo_real/agent_only_7720.json`）：

```text
$ python scripts/check_coverage_regression.py --baseline coverage_baseline.json ^
    --current _ci_logs/d1/demo_real/agent_only_7720.json
✗ 包列表不同（基线独有 ['cognitive','core','lifetrace','memory','persona','planning','sensor','utils']；本次独有 []）
✗ 拒绝比较：口径不同的两个数字相减没有任何含义。
   这正是本脚本要挡住的错误（历史 49.08% 与 77.20% 就是这样被误比的）。
退出码 = 3
```

**这一条的价值**：如果只比百分比，会得到"74.13% vs 77.20% ⇒ 覆盖率掉了 3.07pp"的
**错误结论**；正确读法是**拒绝比较**，而 apples-to-apples 的读法是
`agent` 单包 77.20% → 77.50%（**+0.30pp**，见 §4.3）。

同样的判定也被固化进测试套件（不依赖人工重放）：
`tests/unit/test_check_coverage_regression.py`（**14 passed**）。

### 7.3c 自检（真实基线 vs 真实本次值）

```text
$ python scripts/check_coverage_regression.py --baseline coverage_baseline.json ^
    --current _ci_logs/coverage_auth/coverage.json --fail-under 40
✔ 口径一致：9 个包 [...]；分支覆盖=关
基线      :    74.13%
本次      :    74.13%
变化      :    +0.00 pp   （容忍下降 0.00 pp）
✔ 覆盖率未下降（在容忍范围内）
✔ 达到绝对下限：74.13% ≥ --fail-under=40.00%
退出码 = 0
```

### 7.4 基线文件

`coverage_baseline.json`（仓库根）由权威采集产物 `coverage.json` 经
`_ci_logs/d1/make_baseline.py` 固化而来（该脚本会**拒绝**固化带"未跑完的块 / 丢文件"的产物）。

**当前基线值**：

| 字段 | 值 |
|---|---|
| `packages` | 9 个包（口径声明） |
| `line_rate` | **0.7413 → 74.13%** |
| `lines_covered` / `lines_valid` | 100,637 / 135,752 |
| `branches_valid` | 0（分支覆盖未启用） |
| `chunks` / `incomplete_chunks` / `lost_file_count` | 10 / 0 / 0 |
| `seconds_total` | 5,573 s |
| `scope_label` | `full-9-packages` |
| 采集日 | 2026-09-19（Windows 本机，`tests/unit` 全量 632 文件） |
| 产物 | `_ci_logs/coverage_auth/coverage.xml` |

### 7.5 ⚠️ 为什么**没有**把该断言直接接进 CI

本次**故意不接**，理由是可验证性：

* 本次的权威基线来自**本机 Windows**、用 `scripts/run_authoritative_coverage.py`
  跑 `tests/unit` 全量（含 `-p no:randomly`、`--timeout=300`）。
* CI 的 `unit-tests` job 用的是 `scripts/split_unit_tests.py --shards 6` +
  `-m "not slow and not skip_ci"` + `--dist=loadscope -n 2`，**测试集合与运行方式都不同**。
* 把本地基线接到 CI 上，**必然**因为"测的文件不一样"而红灯 —— 那是一枚假警报，
  比没有断言更糟（会训练人忽略它）。

**正确的接法（留给 TASK-04 / CI 负责方，本次未做）**：

1. 在 `coverage-check` job 里先用**CI 自己的管线**产出一份基线
   （`coverage combine` → `coverage json -o coverage_ci_baseline.json`），
   把它作为一次性产物入库（并记录产出它的 commit）；
2. 之后再在同一个 job 里加一步
   `python scripts/check_coverage_regression.py --baseline coverage_ci_baseline.json --current coverage.json`；
3. 口径一致性硬校验（退出码 3）会保证"CI 管线换成别的口径"时**立刻暴露**，
   而不是给出一个静默可比的假差值。

即：**基线必须与断言跑在同一条管线上**。这条纪律本身就是本次 D1 的结论之一。

---

## 8. 本次变更清单

| 文件 | 变更 |
|---|---|
| `pyproject.toml` | 补写 `[tool.coverage.run]` / `[tool.coverage.report]` 的口径说明与机制（**数值未改**：`source` 仍 9 包、`omit` 仍两条、`fail_under` 仍 0） |
| `pytest.ini` | **删除** `[coverage:run]`/`[coverage:report]`/`[coverage:html]` 三节死配置，留下说明与处置理由 |
| `.github/workflows/ci.yml` | 分片 `--cov=agent` → **9 个 `--cov=`**（并附机制与"不会打破 40% 门禁"的核算）；coverage-check 处补 `fail_under` 分歧与"为何不改"的说明 |
| `scripts/run_authoritative_coverage.py` | **新增**：权威覆盖率采集（口径读 pyproject / 分块 / 逐块完整性校验） |
| `scripts/check_coverage_regression.py` | **新增**：覆盖率不得下降断言（含口径一致性硬校验） |
| `coverage_baseline.json` | **新增**：断言基线（含 `packages` 口径声明） |
| `tests/unit/test_coverage_scope_consistency.py` | **新增**：口径一致性守卫（16 passed） |
| `tests/unit/test_check_coverage_regression.py` | **新增**：断言脚本行为与负例（14 passed） |
| `docs/closeout/COVERAGE_SCOPE_20260919.md` | 本文档 |

---

## 9. 遗留 / 未完成（如实登记）

| # | 事项 | 状态 |
|---|---|---|
| D-1 | `pyproject.toml fail_under` 与 CI 的 40 仍不一致 | **已定性、给出迁移步骤，未实施**（原因：pytest_cov 继承语义会波及多个不可本地验证的子集 job，见 §5.2） |
| D-2 | `test.yml` 的 70 阈值、与 `ci.yml` 的**同名 workflow** | 未处置（超出 D1 范围；已在本文件登记） |
| D-3 | `observability-ci.yml:373` 的过期注释（声称 pyproject 是 40） | 未改（同一文件正被其它任务改动，避免冲突） |
| D-4 | `nine.xml` 根属性与逐 class 求和差 269 行 | 未追根因（0.2%，不影响断言口径）；最终权威产物同样存在该差异（135,752 vs 135,483） |
| D-5 | 6 节那套 `exclude_lines` 是否启用 | 故意未启用（口径变更需重建基线），登记为债务 |
| D-6 | 外围 8 个包的真实覆盖率 | ✅ **已测**：`sensor` 21.26% / `cognitive` 11.56% / `core` 0% / `utils` 44.29% / `memory` 51.68% —— 见 §4.3。**这是本次口径统一暴露出的真实覆盖率盲区，建议单独立项** |

---

## 附录 A · 本次权威全量运行记录（实跑日志）

命令：`python scripts/run_authoritative_coverage.py --out _ci_logs/coverage_auth`
驱动日志：`_ci_logs/d1/auth_driver.log`

```text
[chunk 1/10] rc=1 321s ✔ 已跑完 :: ===== 1 failed, 1695 passed, 7 skipped, 10 warnings in 313.71s (0:05:13) ======
[chunk 2/10] rc=0 205s ✔ 已跑完 :: ========== 1428 passed, 53 skipped, 6 warnings in 194.22s (0:03:14) ===========
[chunk 3/10] rc=0 276s ✔ 已跑完 :: ==== 1875 passed, 45 skipped, 16 xfailed, 6 warnings in 268.27s (0:04:28) =====
[chunk 4/10] rc=0 583s ✔ 已跑完 :: ===== 1628 passed, 93 skipped, 1 xfailed, 9 warnings in 575.27s (0:09:35) =====
[chunk 5/10] rc=0 338s ✔ 已跑完 :: ========== 2137 passed, 80 skipped, 8 warnings in 331.10s (0:05:31) ===========
[chunk 6/10] rc=0 284s ✔ 已跑完 :: ===== 1982 passed, 1 skipped, 1 xfailed, 11 warnings in 277.34s (0:04:37) =====
[chunk 7/10] rc=0 1182s ✔ 已跑完 :: ========== 2253 passed, 13 skipped, 6 warnings in 1174.69s (0:19:34) ==========
[chunk 8/10] rc=1 1858s ✔ 已跑完 :: = 1825 passed, 19 skipped, 4 xpassed, 7 warnings, 2 errors in 1847.33s (0:30:47) =
[chunk 9/10] rc=0 307s ✔ 已跑完 :: =========== 2180 passed, 2 skipped, 4 warnings in 299.37s (0:04:59) ===========
[chunk 10/10] rc=0 219s ✔ 已跑完 :: =========== 2001 passed, 5 skipped, 3 warnings in 212.42s (0:03:32) ===========

总块数        : 10
已完成        : 10
未跑完        : 0 → []
受影响文件    : 0 个（从未执行）

line-rate     : 74.13%  (100637/135752 行)
✔ 所有块均已跑完，数据可用于基线。
```

> **为什么这里必须逐块列出"是否跑完"**：TASK-03 在同一条管线上经历过
> 「第 8 块在 73% 处被 `pytest-timeout` 强杀、63 个文件里 14 个从未执行、
> 而且日志里没有结束摘要、只看 rc 完全看不出来」。
> 本次跑完后**每一块都有明确的"✔ 已跑完 + 摘要原文"**，这才使 74.13% 可以作为基线。
> （机制与修复见 `TEST_TIMEOUT_20260919.md`；本次 chunk 8 耗时 1,858 s，
> 是历史事故同一块的所在地，这次正常跑完。）

