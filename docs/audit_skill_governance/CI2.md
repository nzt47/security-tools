# CI-2 · 干净检出批量验证卡（本批新增/改动守卫 · 覆盖缺口矩阵）

- **卡号**：CI-2（独立工程卡 · 只诊断与报告）
- **日期**：2026-09-27
- **验证对象 HEAD**：`70aca896b93fc086bb74e4d56e29d8c411582501`（分支 `audit/skill-governance-v1.0`）
- **本卡改动**：**只有本报告**（`docs/audit_skill_governance/CI2.md`）。仓库内代码 / 测试 / workflow **一个字没改**；所有实验产物都在仓库外的 `C:\Users\Administrator\ci2_*`。
- **本卡未执行任何改变 git 状态的命令**：只用过 `git archive` / `git ls-files` / `git log` / `git status` / `git check-ignore` / `git cat-file`（全只读）。
- **解释器**：系统 `python`（3.12.0，pytest 9.1.1）。仓库里的 `venv/` 是空壳，全程未使用。
- **零出网**：未装任何包、未访问网络；`HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1` 全程打开。

> ⚠️ **状态变更披露（与 CI-1 同款）**：本卡的干净检出是从 **`70aca896`** 导出的，全部实测都跑在这份副本上。
> 本卡执行期间**并发工作的其它卡提交了**（HEAD 已推进到 `71ed2af5`「CI 修复批次」），工作区也出现了其它卡的未提交改动。
> **这不影响本报告的结论**（结论全部锚定在 `70aca896` 的干净副本上），但**跨 HEAD 的复核者请注意**：
> 若 `71ed2af5` 已经修掉了本卡报的某条红，请以「在新 HEAD 的干净副本上重跑 §3.1 的 14 条命令」为准。

---

## 0. 一页结论（先看这里）

1. **干净检出的构造是可信的**：`git archive HEAD` → 仓库外目录，用 **Python tarfile(encoding='utf-8')** 解包（**不要用 Windows bsdtar**，它会把非 ASCII 文件名解成乱码，CI-1 §3.4 已踩过这个假红）。核对结果：`tracked files (HEAD): 23442 / missing in clean: 0 / files on disk in clean: 23442`，**0 缺 0 多**。

2. **CI-1 ④ 登记的"其余守卫未在干净检出上验证过"，本卡给出的答案是：14 个里 3 个红。** 三条红的**根因是同一个**：`data/skills_mgmt.json`（主轨）被 `.gitignore:224` 排除、且**不在 HEAD**（`git cat-file -e HEAD:data/skills_mgmt.json` → exit 128），于是干净检出上**主轨为空**。

   | 守卫 | 干净检出 | 仓库内（非干净） |
   |---|---|---|
   | `tests/unit/test_skill_search_description_source.py` | **exit 1 · 2 failed, 15 passed** | exit 0 · 17 passed |
   | `tests/unit/test_skill_h3_migration.py` | **exit 1 · 1 failed, 9 passed, 6 skipped** | exit 0 · 16 passed |
   | `tests/unit/test_s2_gate_is_not_false_green.py` | **exit 1 · 1 failed, 3 passed** | exit 0 · 4 passed |

3. **这 3 个文件在 CI 上真的会跑**（不是"没人跑所以无所谓"）：`scripts/split_unit_tests.py` 把它们分到 ci.yml 的 shard 4 / 6（见 §5.1 表），而 ci.yml 的 `unit-tests` job 以 `exit $PYTEST_RC` 收尾、没有 `continue-on-error` ⇒ **批次的 PR 一开，ci.yml 就会红**。本卡用 ci.yml 的真实 marker 过滤条件复跑，三条红**原样复现**（§3.4b）。

4. **静默跳过（假绿）实测有 3 处，其中 2 处是本批新引入的**：
   - `tests/unit/test_s10_03_retrieval_quality_gate.py:227` 的 `pytest.importorskip("rank_bm25")` —— 缺 rank-bm25 时 **11 passed, 2 skipped, exit 0**（CI-1 点名的既有一处；已被 CI-1 在 `tool-retrieval-ci.yml` 用前置断言兜住，**但该文件同时还跑在 ci.yml / test.yml / coverage-ci.yml / observability-ci.yml 里，那几处没有前置断言**）。
   - `tests/unit/test_tool_count_consistency.py:437,445` 的 `pytest.importorskip("tiktoken")` —— 缺 tiktoken 时 **20 passed, 2 skipped, exit 0**。**这是本批新引入的、CI-1 未登记的一处静默假绿**（"token 计量必须实测"那两条断言）。
   - `tests/unit/test_skill_description_single_source.py:737` 是**状态依赖**：整文件跑时**会执行**，单独 `-k` 跑时**静默 skip 且 exit 0**（§3.5 实测）。

5. **CI-1 ⑤2 的两条登记需要更正**：
   - "路由冲突用例集没入库 ⇒ 干净 checkout 必红" —— **已失效**。用例集已在 `35f07f2d` 入库（`.gitignore:476-483` 加了 `!data/eval/*.jsonl` 例外），本卡实测 `test_route_conflict_cases.py` 干净检出 **61 passed / exit 0**。
   - "6 条断言在 CI 中永不执行" —— 本卡实测：`test_skill_description_single_source.py` 干净检出上是 **5 条** skip（`:737` 在整文件运行时其实执行了）；**另有一处 CI-1 完全没登记的 6 条 skip**：`test_skill_h3_migration.py`（全部因主轨缺失）。

6. **最严重的 3 条覆盖缺口**（详见 §5.3 / §6）：
   - **缺口 1**：三个守卫把"主轨存在"当成事实，CI 上主轨恒为空 ⇒ 3 条断言必红 **+ 11 条断言永久不执行**。
   - **缺口 2**：`tiktoken` / `rank_bm25` 的 `importorskip` 会让**最硬的措辞类断言**在缺依赖时静默变绿。
   - **缺口 3**：`test_route_conflict_cases.py` 用的是**精确相等**棘轮（`== base["decision_pass"]`、`== 27`），且**没有任何 workflow 跑 CLI 的 `>=` 下限门** ⇒ 现在进 CI 的是"改进即红"的那一份，而"允许改进"的那一份没人跑。

---

## 1. 干净检出的构造（可复现命令 + 原始输出）

### 1.1 构造命令

~~~powershell
# ① 导出（仓库外，不改 git 状态）
git -C C:\Users\Administrator\agent archive --format=tar HEAD -o C:\Users\Administrator\ci2_head.tar
#    → archive ok，445,358,080 B，exit 0

# ② 解包：**必须用 Python tarfile(encoding='utf-8')**
#    Windows 自带 bsdtar 会把非 ASCII 文件名按本地代码页解释 ⇒ 解成乱码（CI-1 §3.4 的假红教训）
#    C:\Users\Administrator\ci2_extract.py:
#      tf = tarfile.open(src, 'r:', encoding='utf-8', errors='surrogateescape'); tf.extractall(dst)
python C:\Users\Administrator\ci2_extract.py
~~~

原始输出（全文：`C:\Users\Administrator\ci2_evidence\clean_checkout_negative_checks.txt`）：

~~~
=== 1) 构造命令（原始输出）===
archive ok / exit 0 / tar 大小 = 445358080 B
HEAD   = 70aca896b93fc086bb74e4d56e29d8c411582501
branch = audit/skill-governance-v1.0

=== 2) 与 HEAD 清单逐文件核对（python ci2_verify.py 原始输出）===
tracked files (HEAD): 23442
missing in clean: 0 []
files on disk in clean: 23442
~~~

> bsdtar 解包对照（本卡实测过一次，复现了 CI-1 §3.4 的现象）：`Get-ChildItem docs\rfc` 得到 `浜戞灑鑳藉姏娓呭崟鐩樼偣琛_md`（应为 `云枢能力清单盘点表.md`）。用 Python tarfile 解包后同一目录得到正确的 `云枢能力清单盘点表.md / 特性开关规范.md / 鉴权迁移.md`。**后续任何"造干净 checkout"的卡请照抄这一点。**

### 1.2 负面清单 / 正面清单（原始输出）

~~~
=== 3) 负面清单（os.path 等价于 Get-ChildItem / Test-Path）===
--- 顶层 .env* ---
.env.example                     ← 只有 .env.example，**没有 .env**

Test-Path data\audit                           = False      ← 审计链目录不在
Test-Path data\skills_mgmt.json                = False      ← 主轨不在
Test-Path _t06_logs                            = False
Test-Path venv                                 = False
Test-Path data\skills.json                     = False
Test-Path agent\data\skills.json               = False
Test-Path data\descriptors.json                = False
Test-Path data\skills_repo\.index              = False

=== 4) data/skills_mgmt.json 的 git 状态 ===
$ git check-ignore -v data/skills_mgmt.json
.gitignore:224:data/skills_mgmt.json	data/skills_mgmt.json
$ git cat-file -e HEAD:data/skills_mgmt.json
fatal: path 'data/skills_mgmt.json' exists on disk, but not in 'HEAD'
exit = 128  （非 0 = 该路径不在 HEAD 里）

=== 5) 顶层条目 ===
目录数 = 49
_t06_logs 在目录列表里: False ; venv 在目录列表里: False ; .git 在目录列表里: False
目录列表: .ci_logs, .claude, .github, .p6_snapshots, .superpowers, .vscode, agent, backup, cloudshu,
cognitive, config, configs, core, data, demos, deploy, docker, docs, eval, hooks, knowledge, lifetrace,
mcp_services, memory, memory_data, Modules, monitoring, packages, patches, persona, planning, plugins,
release, release-readiness-action, releases, reports, scripts, sensor, static, templates, tests, test_data,
test_prompts, test_reports, utils, workspace, yunshu-ui, _t08_logs, _tmp_rootcause_probe
~~~

正面清单（**入库**的物资，确认在干净检出里）：

~~~
Test-Path data\eval\route_conflict_cases.v1.jsonl                        = True   ← CI-1 之后已入库（35f07f2d）
Test-Path data\eval\route_conflict_cases.v2.jsonl                        = True   ← 同上（CI-1 §1 门6 的"必红"已失效）
Test-Path data\tool_index.json                                           = True
Test-Path data\capability_manifest.json                                  = True
Test-Path data\skills_descriptions_overlay.json                          = True
Test-Path data\skills_repo\.migration\descriptions.baseline.json         = True
Test-Path data\tool_negative_samples.json                                = True
Test-Path tests\fixtures\tool_retrieval_eval.json                        = True
Test-Path docs\rfc\云枢能力清单盘点表.md                                          = True
~~~

### 1.3 统一执行环境（与 CI 对齐的地方 + 明确不对齐的地方）

~~~
CI=true  GITHUB_ACTIONS=true  DISABLE_NATIVE_EXT=1
HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1
PYTHONPATH='.'  PYTHONIOENCODING='utf-8'  PYTHONUTF8='1'
CP_ENV_FILE=<仓库外不存在的路径>          # 模拟"CI 没有 .env"
# 检索类门另加 AGENT_HYBRID_EMBEDDING=0（与 tool-retrieval-ci.yml 的 workflow 级 env 一致）
~~~

- `DISABLE_NATIVE_EXT=1` 的作用：复刻 CI Linux 下 `tests/unit/conftest.py:27` 的 `_CI_LINUX = sys.platform == 'linux' and bool(os.environ.get('CI'))` 分支（把 chromadb / sentence_transformers 封禁成 ImportError）。本机是 Windows，光设 `CI=true` 走不到那条分支。**这是本卡唯一的环境近似**。
- `CP_ENV_FILE` 设为不存在路径**是多余的但无害**：`tests/conftest.py:83-86` 在**导入期无条件**把 `CP_ENV_FILE` 改写成 `tempfile.mkdtemp(prefix="pytest_dotenv_floor_")/isolated.env`，所以 .env 在不在都不影响任何走 pytest 的门。
- **每个守卫开跑前都把干净副本还原到"只有 23442 个 tracked 文件"的状态**（`ci2_reset.py`：删掉一切非 tracked 文件）。理由：真实 CI 的每个 job 都是 fresh checkout；而本仓的读路径会在同一目录里**创建 `data/skills_mgmt.json`( = `{}` ) 与 `data/skills_repo/.index/cache.json`**，不还原就会互相污染。

---

## 2. 依赖枚举与"静默跳过"的机理

### 2.1 模块级导入（AST 静态枚举）

| 守卫文件 | 模块级 import（非标准库部分） |
|---|---|
| test_skill_search_description_source.py | `pytest`, `agent.*` |
| test_three_legs_meta_zh_parity.py | `pytest` |
| test_skill_h3_migration.py | `pytest` |
| test_skill_meta_zh_recall.py | `pytest` |
| test_s2_gate_is_not_false_green.py | （无第三方） |
| test_ret1r_bm25_quality_gate.py | `pytest`, `yaml`, `agent.*` |
| test_ret1r_negative_query_scope.py | `pytest`, `numpy`, `yaml`, `agent.*` |
| test_settings_registry_e1d.py | `pytest`, `agent.*` |
| test_tool_count_consistency.py | `pytest`, `agent.*` |
| test_gate1_single_vector_quality_gate.py | `pytest`, `numpy`, `yaml`, `agent.*` |
| test_date_shift_blindspots_guard.py | `pytest` |
| test_route_conflict_cases.py | `pytest` |
| test_s10_03_retrieval_quality_gate.py | `pytest`, `agent.*` |
| test_skill_description_single_source.py | `pytest` |

**光看模块级 import 会严重低估依赖面**：所有 14 个文件都继承 `tests/conftest.py` 的导入链。

### 2.2 运行期实载的 site-packages 模块（实测，非推断）

用探针插件（`C:\Users\Administrator\ci2_probe\ci2_probe2.py`，在 `pytest_sessionfinish` 里 dump `__file__` 落在 site-packages 下的顶层模块）在干净副本上逐文件实跑：

~~~
14/14 个文件都加载了同一份 conftest 链，其中包含（节选）：
yaml, numpy, pandas, sklearn, scipy, pyarrow, cv2, tiktoken, watchdog, pydantic,
prometheus_client, psutil, flask, httpx, requests, orjson, langsmith, jinja2, joblib,
blinker, itsdangerous, markupsafe, anyio, sniffio, certifi, dateutil, pytz, attrs,
sortedcontainers, pynvml, narwhals, brotli, charset_normalizer, pygments, rich, click,
colorama, six, outcome, execnet, pytest, pytest_asyncio, pytest_timeout, pytest_cov

**只有两项是"按文件区分"的**：
  rank_bm25  只有 3 个文件加载（test_three_legs_meta_zh_parity / test_ret1r_bm25_quality_gate / test_s10_03）
  regex      只有 1 个文件加载（test_tool_count_consistency）
~~~

原始 dump：`C:\Users\Administrator\ci2_evidence\deps_modules.txt`（14 段，每段含 EXIT / 模块表 / sys.path）。

**结论**：这 14 个守卫**没有一个能靠"只装一个薄依赖集"跑起来** —— `tests/conftest.py` 会把整条 agent 依赖链拉进来。任何"给某个 workflow 只装 pytest + pyyaml"的清单都会在 import 期假红（这正是 CI-1 补 `pydantic` / `prometheus-client` 的原因）。

### 2.3 "缺依赖"到底是"红"还是"静默绿"——实测（用 meta_path 封禁模块模拟 runner 缺依赖）

探针插件支持 `CI2_BLOCK=<模块名>`：在 `sys.meta_path[0]` 插入 finder，对指定顶层模块抛 `ModuleNotFoundError`，并清掉已在 `sys.modules` 的同名前缀。然后在**干净副本**上复跑守卫。

| 被封禁 | 守卫 | 结果 | 判定 |
|---|---|---|---|
| `rank_bm25` | test_s10_03_retrieval_quality_gate.py | **11 passed, 2 skipped, exit 0** | **静默假绿** |
| `rank_bm25` | test_three_legs_meta_zh_parity.py | **3 failed, 12 passed, exit 1** | 硬失败（正确） |
| `rank_bm25` | test_ret1r_bm25_quality_gate.py | **4 failed, 6 passed, exit 1** | 硬失败（正确） |
| `rank_bm25` | 其余 11 个文件 | 与基线完全一致 | 与 rank_bm25 无关 |
| `tiktoken` | test_tool_count_consistency.py | **20 passed, 2 skipped, exit 0** | **静默假绿** |
| `numpy` | test_s10_03_retrieval_quality_gate.py | 11 passed, 2 skipped, exit 0 | 静默假绿（同一处 importorskip） |
| `numpy` | test_ret1r_negative_query_scope.py | **1 error, exit 1** | 硬失败（模块级 import numpy） |
| `numpy` | test_gate1_single_vector_quality_gate.py | **1 error, exit 1** | 硬失败（模块级 import numpy） |
| `sentence_transformers` / `chromadb` / `sqlite_vec` / `watchdog` | s10_03 / ret1r_negative / gate1 | 与基线一致（无 skip 增量） | 不构成静默跳过 |

原始报文（`C:\Users\Administrator\ci2_evidence\`）：

~~~
##### nobm25__test_s10_03_retrieval_quality_gate  (EXITCODE: 0)
================================== 所有测试通过！✓ ===================================
测试统计:
  通过: 11
  失败: 0
  跳过: 2
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_s10_03_retrieval_quality_gate.py:230: BM25 路不可用时本对照无意义
SKIPPED [1] tests\unit\test_s10_03_retrieval_quality_gate.py:238: BM25 路不可用时本对照无意义
======================== 11 passed, 2 skipped in 3.73s ========================
~~~

~~~
##### notiktoken__test_tool_count_consistency  (EXITCODE: 0)
测试统计:
  通过: 20
  失败: 0
  跳过: 2
SKIPPED [1] tests\unit\test_tool_count_consistency.py:437: could not import 'tiktoken': No module named 'tiktoken' (CI2_BLOCK 模拟 runner 缺依赖)
SKIPPED [1] tests\unit\test_tool_count_consistency.py:445: could not import 'tiktoken': No module named 'tiktoken' (CI2_BLOCK 模拟 runner 缺依赖)
======================== 20 passed, 2 skipped in 3.69s ========================
~~~

~~~
##### nobm25__test_three_legs_meta_zh_parity  (EXITCODE: 1)
FAILED ...::test_bm25_leg_chinese_recall - AssertionError: BM25 腿不可用（前置不成立）
FAILED ...::test_bm25_leg_english_recall - AssertionError: BM25 腿不可用（前置不成立）
FAILED ...::test_bm25_leg_switch_off_falls_back_to_pre_change_recall - AssertionError: BM25 腿不可用（前置不成立）
======================== 3 failed, 12 passed in 4.15s =========================
~~~

> 机理旁证（`nobm25__test_ret1r_bm25_quality_gate.txt` 里的业务日志）：`agent.skills_mgmt.bm25_searcher:bm25_searcher.py:189 {'action': 'build_index.skipped', 'reason': 'rank_bm25 not installed'}` ⇒ 业务侧是**优雅降级**，只有断言侧"前置检查"写得够硬的（three_legs / ret1r）才会红。

### 2.4 rank-bm25 到底算不算依赖？

`pyproject.toml:145` **在 `[project] dependencies` 里**声明了 `rank-bm25==0.2.2`（不是 optional extra）。但多个 workflow 用的是 `pip install -e . || true`：

~~~
test.yml:58                          pip install -e .[dev] --timeout=120 || true
test.yml:128                         pip install -e . --timeout=180 || true
tool-retrieval-ci.yml:106/155/201    pip install -e . || true
~~~

⇒ **`pip install -e .` 失败会被 `|| true` 吞掉**，此时 rank-bm25 / tiktoken 可能都不在，`importorskip` 那两处就会静默变绿。

**另外发现一处与本批注释不符的事实**：`tool-retrieval-ci.yml:236` 的注释写着"rank_bm25 **不在** pyproject 依赖里"，而 `pyproject.toml:145` 明确有它（CI-1 报告正文已更正这点，但 workflow 里的注释没跟着改）。这条**只是注释陈旧，不影响判定**。

---

## 3. 逐个守卫实跑：干净检出 vs 仓库内（原始输出）

命令（两边逐字相同）：

~~~
python -m pytest tests/unit/<file> -q -p no:randomly --timeout=60
~~~

`-p no:randomly` 是为了消除顺序随机化造成的漂移；本机装了 pytest-randomly，不加这条会把"顺序敏感"的红/绿搅在一起。工作流的原命令不带 `-p no:randomly`，本卡在 §4 复刻工作流时按原样跑。

### 3.1 结果总表

| # | 守卫文件 | 干净检出（ci2_clean） | 仓库内（agent） | 判定 |
|---|---|---|---|---|
| 1 | test_skill_search_description_source.py | **exit 1 · 2 failed, 15 passed** | exit 0 · 17 passed | **红** |
| 2 | test_three_legs_meta_zh_parity.py | exit 0 · 15 passed | exit 0 · 15 passed | 绿 |
| 3 | test_skill_h3_migration.py | **exit 1 · 1 failed, 9 passed, 6 skipped** | exit 0 · 16 passed | **红** |
| 4 | test_skill_meta_zh_recall.py | exit 0 · 46 passed | exit 0 · 46 passed | 绿 |
| 5 | test_s2_gate_is_not_false_green.py | **exit 1 · 1 failed, 3 passed** | exit 0 · 4 passed | **红** |
| 6 | test_ret1r_bm25_quality_gate.py | exit 0 · 10 passed | exit 0 · 10 passed | 绿 |
| 7 | test_ret1r_negative_query_scope.py | exit 0 · 22 passed | exit 0 · 22 passed | 绿 |
| 8 | test_settings_registry_e1d.py | exit 0 · 11 passed | exit 0 · 11 passed | 绿 |
| 9 | test_tool_count_consistency.py | exit 0 · 22 passed | exit 0 · 22 passed | 绿 |
| 10 | test_gate1_single_vector_quality_gate.py | exit 0 · 6 passed | exit 0 · 6 passed | 绿 |
| 11 | test_date_shift_blindspots_guard.py | exit 0 · 24 passed, 1 skipped | exit 0 · 24 passed, 1 skipped | 绿 |
| 12 | test_route_conflict_cases.py | exit 0 · 61 passed | exit 0 · 61 passed | 绿（**CI-1 记录的"必红"已失效**） |
| 13 | test_s10_03_retrieval_quality_gate.py | exit 0 · 13 passed, **0 skipped** | exit 0 · 13 passed | 绿 |
| 14 | test_skill_description_single_source.py | exit 0 · 28 passed, 5 skipped | exit 0 · **33 passed, 0 skipped** | 绿（但 CI 上 5 条不执行） |

原始证据（每条含 CMD / CWD / EXTRA_ENV / TIME / ELAPSED / EXITCODE / 全文 stdout+stderr）：`C:\Users\Administrator\ci2_evidence\clean__*.txt`（14 个）与 `repo__*.txt`（14 个）。

### 3.2 红 #1 · test_skill_search_description_source.py（原始断言报文）

~~~
CMD: python -m pytest tests/unit/test_skill_search_description_source.py -q -p no:randomly --timeout=60
CWD: C:\Users\Administrator\ci2_clean
ELAPSED: 12.02s
EXITCODE: 1
collected 17 items
tests\unit\test_skill_search_description_source.py ...............FF     [100%]

================================== FAILURES ===================================
_ TestRealRepoSearchMatchesDisplay.test_migrated_skills_are_searchable_by_file_track_text _
tests\unit\test_skill_search_description_source.py:186: in test_migrated_skills_are_searchable_by_file_track_text
    assert miss == {}, f"迁移后的技能按文件轨文案搜不到: {miss}"
E   AssertionError: 迁移后的技能按文件轨文案搜不到: {'testing-anti-patterns': 'mock', 'code-observability': 'observable', 'engineering-test-delivery': 'audit', 'frontend-state-sync': 'abortcontroller', 'self-explanatory-ui': 'hierarchy'}
E   assert {'testing-ant...troller', ...} == {}
E     Left contains 5 more items: ...
_ TestRealRepoSearchMatchesDisplay.test_search_and_display_report_the_same_text _
tests\unit\test_skill_search_description_source.py:210: in test_search_and_display_report_the_same_text
    assert "code-observability" in [...]
E   AssertionError: assert 'code-observability' in []
======================== 2 failed, 15 passed in 5.96s =========================
~~~

**根因（实测，不是推断）**：该文件的 `TestRealRepoSearchMatchesDisplay._svc()` 用**无参** `SkillsMgmtService()`，而 `SkillsMgmtService.search()` 只把**主轨** `self.store.list_all()` 喂给检索器。干净检出上主轨为空 ⇒ 候选集为 0。两次运行的业务日志直接把数字打出来了：

~~~
INFO agent.skills_mgmt:index_cache.py:189 {'action': 'load_on_startup.ok', 'skill_count': 28, 'main_track_count': 0, ...}
INFO agent.skills_mgmt:searcher.py:208 [Searcher] query='observable' → 0/0 命中, 0.00ms
~~~

同一段代码在**仓库内**（主轨存在）的对照（本卡实跑 `ci2_probe_maintrack.py`）：

~~~
=== CLEAN ===
main track count = 0
file track count = 28
=== REPO ===
main track count = 22
file track count = 30
~~~

### 3.3 红 #2 · test_skill_h3_migration.py（原始断言报文）

~~~
CMD: python -m pytest tests/unit/test_skill_h3_migration.py -q -p no:randomly --timeout=60
CWD: C:\Users\Administrator\ci2_clean
ELAPSED: 22.72s
EXITCODE: 1
collected 16 items
tests\unit\test_skill_h3_migration.py ..ss..ss.....Fss                   [100%]

================================== FAILURES ===================================
_______ TestActuallyRecallable.test_runtime_only_set_shrinks_to_the_two _______
tests\unit\test_skill_h3_migration.py:266: in test_runtime_only_set_shrinks_to_the_two
    assert got == set(NOT_MIGRATED), (
E   AssertionError: runtime_only 标注 = []，期望 ['global-core-principles', 'skill']
E   assert set() == {'global-core...les', 'skill'}
=========================== short test summary info ===========================
SKIPPED [1] tests\unit\test_skill_h3_migration.py:114: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
SKIPPED [1] tests\unit\test_skill_h3_migration.py:121: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
SKIPPED [1] tests\unit\test_skill_h3_migration.py:159: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
SKIPPED [1] tests\unit\test_skill_h3_migration.py:173: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
SKIPPED [1] tests\unit\test_skill_h3_migration.py:276: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
SKIPPED [1] tests\unit\test_skill_h3_migration.py:283: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
=================== 1 failed, 9 passed, 6 skipped in 15.53s ===================
~~~

**要点：同一个前置条件，文件里 6 处用 `main_track` fixture 显式 skip，唯独 :260 那条没有用 fixture，直接吃硬断言 ⇒ 在 CI 上必红。**（这是"守卫自己内部口径不一致"的形态，不是"环境不该跑"。）

根因链（实测）：`agent/lines/callability.py:1193 runtime_only_skill_entries()` 只读**运行时台账**（`data/skills.json` / `data/skills_mgmt.json`，两者都被 gitignore）⇒ 干净检出上恒为空集。

### 3.4 红 #3 · test_s2_gate_is_not_false_green.py（原始断言报文）

~~~
CMD: python -m pytest tests/unit/test_s2_gate_is_not_false_green.py -q -p no:randomly --timeout=60
CWD: C:\Users\Administrator\ci2_clean
ELAPSED: 9.24s
EXITCODE: 1
collected 4 items
tests\unit\test_s2_gate_is_not_false_green.py .F..                       [100%]

================================== FAILURES ===================================
_______________ test_pathB_service_can_recall_main_track_skills _______________
tests\unit\test_s2_gate_is_not_false_green.py:102: in test_pathB_service_can_recall_main_track_skills
    assert missing == [], (
E   AssertionError: 服务形态路径（SkillFileStore + SkillIndexCache）丢了主轨技能 ['global-core-principles', 'skill'] —— 该路径本应能召回它们；这属于功能回归，不是预期。
E   assert ['global-core...les', 'skill'] == []
======================== 1 failed, 3 passed in 4.39s =========================
~~~

该文件的 `MAIN_TRACK_ONLY = ["global-core-principles", "skill"]`（:49-57）是**硬编码**的，断言"服务形态路径必须能召回它们" —— 在干净检出上这两个技能**根本不存在**，所以必然"缺 2"。

### 3.4b 用 ci.yml 的真实 marker 过滤复跑（证明"CI 上会执行且仍红"）

ci.yml 的 unit-tests job 用的是：

~~~
pytest $(python scripts/split_unit_tests.py --shard N --shards 6 --by=time) \
  -n 2 --dist=loadscope -v --tb=short ... -p no:randomly \
  -m "not slow and not skip_ci and not serial" --timeout=60 ...
~~~

本卡按同一 marker 过滤在干净副本上复跑（`marker__*.txt`）：

~~~
[marker__test_skill_search_description_source] rc=1 | 2 failed, 15 passed in 4.70s
[marker__test_skill_h3_migration]              rc=1 | 1 failed, 9 passed, 6 skipped in 13.24s
[marker__test_s2_gate_is_not_false_green]      rc=1 | 1 failed, 3 passed in 4.70s
[marker__test_s10_03_retrieval_quality_gate]   rc=0 | 13 passed in 3.14s
~~~

⇒ **没有一条是被 marker 过滤掉的**（这 14 个文件都没有 `slow / skip_ci / serial` 标记）。

### 3.5 状态依赖的静默跳过 · test_skill_description_single_source.py:737

整文件跑（干净副本，reset 后 pristine）：最终 5 条 skip 中**没有** `:737`。

单独 `-k` 跑同一条（干净副本，reset 后 pristine）：

~~~
$ python -m pytest tests/unit/test_skill_description_single_source.py -q -p no:randomly --timeout=60 \
      -k cache_hash_equals_skill_md_md5
测试统计:
  通过: 0
  失败: 0
  跳过: 1
SKIPPED [1] tests\unit\test_skill_description_single_source.py:737: 索引缓存不存在（生成物，未构建过检索缓存）
======================= 1 skipped, 32 deselected in 2.73s ======================
exit=0
$ Test-Path data\skills_repo\.index\cache.json
False
~~~

⇒ 同一条断言，**跑法不同则"执行"或"静默跳过"**：整文件跑时前面的用例已经建立了 `data/skills_repo/.index/cache.json`，所以它执行；单独跑时它静默 skip 且 **exit 0**。在 ci.yml 的 `-n 2 --dist=loadscope` 下它落在哪个 worker、与谁同批，决定了它执不执行。

### 3.6 干净检出运行的副产物（对"不许写生产数据"的核查）

因为每个守卫开跑前都会 reset，本卡能精确列出**每个守卫在干净副本上留下了什么**（注意：reset 日志里的
"tag X"记录的是 **X 的上一个**守卫留下的文件，下表已按此归位）：

| 守卫 | 运行后新增（除 `__pycache__` / `.pytest_cache`） |
|---|---|
| test_skill_search_description_source | `data/skills_mgmt.json`( = `{}` )、`data/skills_repo/.index/cache.json`、`test_reports/logs/*.log` |
| test_skill_h3_migration | `data/skills_mgmt.json`、`data/skills_repo/.index/cache.json`、`test_reports/logs/*.log` |
| test_gate1_single_vector_quality_gate | `.pytest_tmp/data-gym-cache/...`、`agent/data/extensions.json`、`test_reports/logs/*.log` |
| test_s10_03_retrieval_quality_gate | `agent/data/tool_trace.db`、`test_reports/logs/*.log` |
| 其余 10 个 | 只有 `test_reports/logs/*.log` |

**没有一个守卫写 `data/audit/**`**（这是"测试写生产审计链"类风险的一个正面结论）。`data/skills_mgmt.json` 被创建成 `{}` 是**读路径的副作用**（CI-1 §1 门1 已记录），不是断言主动写台账。

---

## 4. 按 3 个 workflow 的 run: 步骤逐条复刻（干净检出）

> 复刻口径：CWD = 干净检出；`on.*.paths` 只决定"触不触发"，不改变 `run:` 内容，故直接执行 `run:` 里的命令。workflow 级 env 按文件原样带入。
> 差异声明：workflow 里的 `/tmp/...` 路径在 Windows 上换成仓库外的等价路径（只影响比较脚本自身，不影响被测对象）。

### 4.1 .github/workflows/skill-description-single-source.yml

| 证据 | job / step | 命令（= workflow 逐字） | 退出码 | 期望 | 结果 |
|---|---|---|---|---|---|
| wf_sds_1_prereq | guards / 前置：断言依赖就位 | `python -c "…find_spec…need=['yaml','pydantic','prometheus_client','pytest','pytest_asyncio','pytest_timeout']…"` | 0 | 0 | ✅ 依赖就位 |
| wf_sds_2_guards | guards / 守卫测试 | `python -m pytest tests/unit/test_skill_description_single_source.py -v --timeout=120 --no-header -p no:cacheprovider` | **0** | 0 | ✅ 28 passed, 5 skipped |
| wf_sds_3_compare_ci | compare-dual-caliber / CI 口径 | `python scripts/compare_skills_legacy_vs_repo.py --ci`（+ `grep -qE "PASS-SKIP\|ALL_MATCH"`） | **0** | 0 且结论显式 | ✅ `[compare] RESULT: PASS-SKIP(not_applicable)` |
| wf_sds_4_compare_verify | compare-dual-caliber / 迁移校验口径 | `python scripts/compare_skills_legacy_vs_repo.py --verify --legacy <缺失>` | **2** | **非 0** | ✅ `[compare] RESULT: FAIL(legacy_missing) —— 不是 ALL_MATCH` |
| wf_sds_5_manifest | manifest-is-derived / 清单漂移检查 | `python scripts/sync_capability_manifest.py --check` | **0** | 0 | ✅ `[OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21）` |

**该 workflow 在本批 HEAD 上会全绿。**

### 4.2 .github/workflows/tool-retrieval-ci.yml（workflow env 含 `AGENT_HYBRID_EMBEDDING=0`、两个 HF 离线键）

| 证据 | job / step | 命令 | 退出码 | 结果 |
|---|---|---|---|---|
| wf_tr_1_prereq_bm25 | skill-retrieval-quality-gate / 前置 | `python -c "import rank_bm25; print('rank_bm25 OK:', rank_bm25.__file__)"` | 0 | ✅ rank_bm25 OK |
| wf_tr_2_s10_03 | skill-retrieval-quality-gate / 有界相似度质量门 | `python -m pytest tests/unit/test_s10_03_retrieval_quality_gate.py -v --timeout=120 --no-header -p no:cacheprovider` | **0** | ✅ **13 passed，0 skipped**（前置断言保证了 rank_bm25 在场） |
| wf_tr_3_recall | retrieval-quality / 运行检索质量评估测试 | `pytest tests/unit/test_tool_retrieval_quality.py -v --tb=short --timeout=60` | 0 | ✅ 22 passed, 1 xfailed |
| wf_tr_4_negative | negative-samples / 运行负样本回归测试 | `pytest tests/unit/test_tool_negative_samples.py -v --tb=short --timeout=60` | 0 | ✅ 26 passed, 13 xfailed |
| wf_tr_5_proper_noun | proper-noun / 运行专有名词匹配验证 | `python scripts/verify_bm25_proper_noun.py` | 0 | ✅ 打印"BM25 对专有名词缩写…精确匹配强（100%）"后正常退出 |

**该 workflow 在本批 HEAD 上会全绿。**（附带发现：wf_tr_3 实际 `22 passed, 1 xfailed`、wf_tr_4 实际 `26 passed, 13 xfailed`，而这两个 job 的"摘要"步骤打印的期望值是"27 passed + 12 xfailed" —— **摘要文案陈旧，不参与判定**，但会误导看日志的人。）

### 4.3 .github/workflows/settings-registry-gap-guard.yml

| 证据 | job / step | 命令 | 退出码 | 结果 |
|---|---|---|---|---|
| wf_srg_1_prereq | settings-gap / 前置：断言依赖就位 | 同 4.1 的 find_spec 断言 | 0 | ✅ 依赖就位 |
| wf_srg_2_scan | settings-gap / 开关登记零缺口（机械提取） | `python scripts/scan_settings.py --check` | **0** | ✅ 扫 754 文件；缺口(未注册) 0 / 注册但未读到 0 / 未声明的动态家族 0 / 未声明的排除项 0；结论：零缺口 |
| wf_srg_3_test | settings-gap / 注册表契约守卫 | `python -m pytest tests/unit/test_settings_registry.py -v --timeout=120 --no-header -p no:cacheprovider` | **0** | ✅ 56 passed |

**该 workflow 在本批 HEAD 上会全绿。**

### 4.4 附：.github/workflows/date-shift-guard.yml（本批唯一**显式按文件名**引用本批守卫的既有 workflow）

| 证据 | job / step | 命令 | 退出码 | 结果 |
|---|---|---|---|---|
| wf_dsg_1_scan | static-scan / 盲区静态扫描 | `python tests/unit/test_date_shift_blindspots_guard.py --scan` | 0 | ✅ 扫 932 文件 / 含时钟调用 76 / 未裁定命中 0；stderr 一条 `SyntaxWarning: invalid escape sequence`（不影响退出码） |
| wf_dsg_2_guard | static-scan / 守卫套件 | `python -m pytest tests/unit/test_date_shift_blindspots_guard.py -q -p no:randomly` | 0 | ✅ 24 passed, 1 skipped（skip = 需要 `--runslow`） |

---

## 5. 覆盖缺口矩阵

### 5.1 主表（每行 = 一个守卫文件）

"在 CI 里会执行吗"的口径：**不只是"有没有 workflow 显式点名"**，而是"会不会被某个 job 收集到"。本卡实测 `scripts/split_unit_tests.py --shard N --shards 6 --by=time` 的分配结果（`ci2_shards.py`）：

| 守卫文件 | 被哪个 job 收集（含 shard） | 干净检出 | 缺依赖会静默跳过吗（实测） | 结论 |
|---|---|---|---|---|
| test_skill_search_description_source.py | ci.yml unit-tests **shard 6**；coverage-ci；observability-ci；test.yml | **红（2 failed）** | 否 | **必须修/摘** |
| test_three_legs_meta_zh_parity.py | ci.yml **shard 6**；其余同上 | 绿 | 否（缺 rank_bm25 → 3 failed，硬失败） | 可用 |
| test_skill_h3_migration.py | ci.yml **shard 4**；其余同上 | **红（1 failed + 6 skipped）** | 否 | **必须修/摘** |
| test_skill_meta_zh_recall.py | ci.yml **shard 4**；其余同上 | 绿 | 否 | 可用 |
| test_s2_gate_is_not_false_green.py | ci.yml **shard 6**；其余同上 | **红（1 failed）** | 否 | **必须修/摘** |
| test_ret1r_bm25_quality_gate.py | ci.yml **shard 6**；其余同上 | 绿 | 否（缺 rank_bm25 → 4 failed） | 可用 |
| test_ret1r_negative_query_scope.py | ci.yml **shard 4**；其余同上 | 绿 | 否（缺 numpy → collection error） | 可用 |
| test_settings_registry_e1d.py | ci.yml **shard 5**；+ settings-registry-gap-guard.yml | 绿 | 否 | 可用 |
| test_tool_count_consistency.py | ci.yml **shard 3**；其余同上 | 绿 | **是 · 缺 tiktoken → 2 skipped / exit 0** | **有假绿** |
| test_gate1_single_vector_quality_gate.py | ci.yml **shard 6**；其余同上 | 绿 | 否（缺 numpy → collection error） | 可用 |
| test_date_shift_blindspots_guard.py | ci.yml **shard 6**；+ date-shift-guard.yml（显式点名 ×2） | 绿 | 否 | 可用（覆盖最好的一条） |
| test_route_conflict_cases.py | ci.yml **shard 6**；其余同上 | 绿 | 否 | 绿，但**语义是精确相等棘轮** |
| test_s10_03_retrieval_quality_gate.py | ci.yml **shard 3**；+ tool-retrieval-ci.yml（显式点名） | 绿（0 skip） | **是 · 缺 rank_bm25/numpy → 2 skipped / exit 0**（tool-retrieval-ci 有硬前置兜住，其它 job 没有） | **其它 job 有假绿** |
| test_skill_description_single_source.py | ci.yml **shard 1**；+ skill-description-single-source.yml（显式点名） | 绿（5 skipped） | 否 | 绿，但 CI 覆盖被削 |
| test_settings_registry.py | ci.yml **shard 5**；+ settings-registry-gap-guard.yml | 绿（56 passed） | 否 | 可用 |

注 ①：以上"其余同上" = `coverage-ci.yml`（`pytest tests/unit/ … -m "not slow and not skip_ci and not forked_incompatible"`）、`observability-ci.yml`（`--root tests` 6-shard，同 marker）、`test.yml`（`pytest tests/unit/ … -m "not slow"`）、`full-regression.yml`（`-m "not slow and not skip_ci"`）。它们的 pip 清单**都没有** rank-bm25 / tiktoken 的硬前置断言。

注 ②：ci.yml 的 `unit-tests` job **没有** `continue-on-error`（全文件仅第 1451 行有一处，属别的 job），且脚本以 `exit $PYTEST_RC` 收尾 ⇒ 分片里任何失败都会让 job 红。

**没有任何一个守卫文件"不被任何 job 收集"** —— 14/14 都落在某个 shard 里。

### 5.2 表 A：**在 CI 里永不执行 / 条件执行**的断言

| 守卫 : 行号 | 断言守什么 | CI 上的状态 | 依赖的 gitignored 物资 | 本地（仓库内） |
|---|---|---|---|---|
| test_skill_description_single_source.py:494 | legacy 快照口径 | **永不执行（skip）** | `data/skills.json` | 执行 |
| test_skill_description_single_source.py:532 | 两份 legacy 快照一致性 | **永不执行（skip）** | `data/skills.json` ×2 | 执行 |
| test_skill_description_single_source.py:705 | legacy 快照口径（第三条） | **永不执行（skip）** | `data/skills.json` | 执行 |
| test_skill_description_single_source.py:454 | descriptor 台账一致性 | **永不执行（skip）** | `data/descriptors.json` | 执行 |
| test_skill_description_single_source.py:587 | 主轨独有集合必须恰好 2 条 | **永不执行（skip，显式理由）** | `data/skills_mgmt.json` | 执行 |
| test_skill_description_single_source.py:737 | 缓存 hash == skill.md md5 | **条件执行**：整文件跑→执行；单跑→静默 skip（§3.5） | `data/skills_repo/.index/cache.json`（生成物） | 执行 |
| test_skill_h3_migration.py:114 / :121 / :159 / :173 / :276 / :283 | 逐字搬运 / 双轨不变量 / skill 描述质量（6 条） | **永不执行（skip）** | `data/skills_mgmt.json` | 执行 |
| test_date_shift_blindspots_guard.py:1074 | 慢档日期敏感面 | **永不执行（skip，"需要 --runslow"）** | 无（设计如此） | 需 `--runslow` |

合计：**CI 上永不执行的断言 ≥ 11 条**（5 + 6），另有 1 条条件执行、1 条设计性 skip。CI-1 ⑤2 记的"6 条"是它那一版 `test_skill_description_single_source.py` 的状态；本卡实测为 5 条，**且漏记了 `test_skill_h3_migration.py` 的 6 条**。

### 5.3 表 B：**依赖缺失会静默跳过（假绿）**的断言

| 守卫 : 行号 | 机制 | 缺什么就静默变绿 | 实测证据 | 现有防护 |
|---|---|---|---|---|
| test_s10_03_retrieval_quality_gate.py:227（影响 :230 / :238） | `pytest.importorskip("rank_bm25", reason=…"BM25 路不可用时本对照无意义")` 写在 `loader` fixture 里 | 缺 `rank_bm25` ⇒ **11 passed, 2 skipped, exit 0** | `nobm25__test_s10_03_retrieval_quality_gate.txt` | **只有 tool-retrieval-ci.yml 有**前置断言；ci.yml / test.yml / coverage-ci.yml / observability-ci.yml **没有** |
| test_tool_count_consistency.py:437 | `pytest.importorskip("tiktoken")` | 缺 `tiktoken` ⇒ **20 passed, 2 skipped, exit 0** | `notiktoken__test_tool_count_consistency.txt` | **无任何前置断言** |
| test_tool_count_consistency.py:445 | 同上（"中文描述下不等于字符除三"） | 同上 | 同上 | 同上 |
| test_skill_description_single_source.py:737 | `if not cache_path.exists(): pytest.skip(...)` | 单跑时静默 skip + exit 0 | §3.5 | 无 |
| test_three_legs_meta_zh_parity.py 的 `bm25_searcher.is_available()` 前置 | 不是 skip，是**断言** | — | `nobm25__…three_legs…txt`：3 failed | 自带硬前置（**正面样板**） |
| test_ret1r_bm25_quality_gate.py 同族 | 同上 | — | `nobm25__…ret1r_bm25…txt`：4 failed | 自带硬前置（正面样板） |
| test_gate1 / test_ret1r_negative 的模块级 `import numpy` | collection error | — | `blk_numpy__*.txt`：1 error | 硬失败（正面样板） |

### 5.4 与 CI-1 登记项的对照（哪几条要更正）

| CI-1 的登记 | 本卡实测 | 结论 |
|---|---|---|
| ④ "其余新增守卫没有在干净检出上验证过" | 已在干净检出上跑完 14 个 | **本卡关闭该登记**（结果见 §3.1） |
| ⑤2 "6 条断言在 CI 中永不执行" | 实测 5 条（本文件）+ 6 条（h3_migration，CI-1 未登记）= **11 条** | **需更正 + 补登** |
| ⑤2 "`test_route_conflict_cases.py` 用精确相等棘轮，CI 阈值是 >=48" | 属实：`== base["decision_pass"]`（:238 / :553）、`--min-pass 27` + `assert "决策层通过 27/50"`（:526-529） | 属实，且**该文件现在真的在 ci.yml shard 6 里跑**（CI-1 当时只是静态登记） |
| §1 门6 "路由冲突用例集没入库 ⇒ 干净 checkout 必红 ⇒ 不接入" | **已失效**：用例集在 `35f07f2d` 入库（`.gitignore:476-483` 加 `!data/eval/*.jsonl`），干净检出 61 passed / exit 0 | **需更正**；但 CLI 下限门**仍然没有任何 workflow 跑**（`grep eval_route_conflict .github/` → 无命中） |

---

## 6. 最小修法建议（**只写建议，本卡未改任何文件**）

### 6.1 红 #1 · test_skill_search_description_source.py

**现象**：`SkillsMgmtService.search()` 只喂主轨 ⇒ 干净检出上候选集为 0 ⇒ 两条断言必红。

**最小修法（二选一，都不放宽断言）**：
- **(A) 换数据面（首选）**：把 `_svc()` 换成 `SkillsMgmtService(store_path=<tmp 主轨>, repo_path=<真 skills_repo>)`，并在夹具里把真文件轨的 28 条**同步进这个 tmp 主轨**（与产品读路径同一形态），再断言"5 条迁移技能能按文件轨文案搜到"。测的是**同一件事**（搜索与展示同源），但数据面是自造的。
- **(B) 显式化适用域**：保留 `SkillsMgmtService()`，加"主轨有数据"的前置，没有就**显式 skip 并打印理由**（与 `test_skill_h3_migration.py` 的 6 处同款），并登记"该断言在 CI 不适用"。

**风险**：(B) 会让 CI 上少 2 条断言；(A) 要小心夹具退化成"自己造数据自己验"，建议同时保留一条"真库 28 条都能被搜到"的对照。**不推荐** `miss == {} or 主轨为空` 这种写法（那是放宽）。

### 6.2 红 #2 · test_skill_h3_migration.py:260（**最简单的一条**）

- **最小修法**：给 `test_runtime_only_set_shrinks_to_the_two` 穿上与同文件其余 6 处**同一个** `main_track` fixture（`def test_runtime_only_set_shrinks_to_the_two(self, main_track):`）。
- **为什么这不是削弱**：`runtime_only_skill_entries()` 的输入本来就包含主轨；主轨没有数据时"runtime_only 集合"**无定义**（不是"应该为空"）。其余 6 条已经这么判了，本条只是漏穿 fixture，断言本体一个字不改。
- **风险**：CI 上少 1 条断言（本来就无适用域）。

### 6.3 红 #3 · test_s2_gate_is_not_false_green.py:102

- **最小修法**：把 `MAIN_TRACK_ONLY` 从"常量"改为"从当前主轨实读"（`= sorted(set(主轨 id) - set(文件轨 id))`），并在**主轨无数据**时对本条显式 skip 并打印理由。
- **为什么这不是削弱**：这条断言的语义是"服务形态路径**不该比**裸 loader 路径少召回"，正确写法是 `set(MAIN_TRACK_ONLY) - svc` 为空；把 `MAIN_TRACK_ONLY` 钉成两个字面量，等于把一个**数据事实**写进了断言。
- **风险（必须一起做）**：改成实读后，主轨被清空时这条会变成空断言（`missing == []` 恒真）⇒ **必须配一个"MAIN_TRACK_ONLY 非空"的前置断言**，否则是新的假绿。
- **注意**：同文件 `test_pathA_bare_loader_cannot_recall_main_track_skills`（:69）与 `test_drift_script_s2_fails_on_pathA_gap_now`（:119）在干净检出上 **pass**（主轨为空 ⇒ "缺 2" 反而是真话），**不要一起改**。

### 6.4 假绿 #1 · test_s10_03:227（rank_bm25）

- **最小修法**：把 `pytest.importorskip` 换成**硬前置**：
  ~~~python
  try:
      import rank_bm25  # noqa: F401
  except ImportError as e:
      pytest.fail(f"本门的核心对照需要 rank-bm25（pyproject.toml:145 已声明）: {e}")
  ~~~
  （与本批 `test_three_legs_meta_zh_parity.py` / `test_ret1r_bm25_quality_gate.py` 的写法一致 —— 那两份缺依赖时是 3 failed / 4 failed。）
- **为什么这不是"加严"**：`tool-retrieval-ci.yml` 已经把这条做成硬前置了（CI-1 的决定），文件里再 importorskip 一次，等于让**别的 job**保留一条"缺依赖就绿"的后门。
- **风险**：若某 runner 上 rank-bm25 真装不上，该 job 会红 —— 但那正是我们想知道的。
- **配套**：`tool-retrieval-ci.yml:236` 的注释"rank_bm25 不在 pyproject 依赖里"应按 `pyproject.toml:145` 更正。

### 6.5 假绿 #2 · test_tool_count_consistency.py:437 / :445（tiktoken）

- **最小修法**：同上，`importorskip` → `pytest.fail`；并在跑该文件的 workflow（ci.yml shard 3 等）的安装步骤里显式 `pip install tiktoken`，或至少把 `pip install -e . || true` 的 `|| true` 收窄。
- **风险**：tiktoken 是 pyproject 主依赖，正常安装一定在；把 `|| true` 收窄会让"依赖装不上"从静默变显式 —— 那是好事，但可能暴露别的既有问题，建议单独一张卡评估。

### 6.6 假绿 #3 · test_skill_description_single_source.py:737（状态依赖）

- **最小修法**：在模块级夹具里**显式构建一次检索缓存**（把"有没有缓存"变成确定事实），让这条断言在 CI 上**稳定执行**；或至少把 skip 理由升级为"本断言在本轮未执行"的显式告警。
- **风险**：构建缓存会在 CI 工作目录里产生 `data/skills_repo/.index/cache.json`（生成物，已 gitignore），无副作用风险。

### 6.7 语义缺口 · 路由冲突（不是红，是"门装错了"）

- **现状**：`test_route_conflict_cases.py`（**精确相等**棘轮）进了 ci.yml；CLI 的 `>=` 下限门**没有任何 workflow 跑**。
- **最小修法**：新增一个最小 workflow（CI-1 §2.5 已给出可用的 job 定义），只跑 `python scripts/eval_route_conflict.py --no-embedding`；并把 `test_route_conflict_cases.py` 里的精确相等断言改成"**不许低于**基线 + 基线表本身被断言守着"（CLI 已经是 `>=` 语义）。
- **风险**：改判定语义会动到该文件多条断言，**建议另开卡**；本卡只报事实。

### 6.8 通用（防下一批再犯）

- 任何**新守卫**只要读 `data/` 下的 gitignored 运行期文件，就必须在文件顶部写明"CI 适用域"，并**统一**用同一个前置夹具（要么全 skip + 打印理由，要么全硬失败）—— `test_skill_h3_migration.py` 同一个文件里"6 处 skip + 1 处硬断言"就是反例。
- 任何 `pytest.importorskip(...)` 都要在**同一个 PR 里**配一条 workflow 级前置断言（`python -c "import X"`），否则它只是"把红变成绿"的开关。

---

## 7. 我没能确认的部分 / 残留风险（**如实登记**）

1. **没有在真正的 GitHub Actions runner 上跑过**（零出网预算）。本机近似 = Windows + `DISABLE_NATIVE_EXT=1`。**未验证**：Ubuntu 上的分片/路径/编码差异会不会让这 3 条红变成绿（本卡判断依据是"主轨是 gitignored 且不在 HEAD"这个**与平台无关**的事实，置信度高，但**不是实测**）。
2. **没跑全量 tests/unit**（任务书要求）。因此**不能排除**"同 shard 里别的测试先把内容写进主轨"这种顺序效应。定向排查：全仓只有 2 处无参 `SkillsMgmtService()`，其中 `tests/unit/test_s2_03_integration.py:420` 只调 `list_pending_approvals()`、**不写主轨**；`test_retrieval_silent_failures.py:572/617/716` 写的是 `tmp_path` 沙箱。⇒ 我**没有找到**任何会把内容写进生产主轨的测试，但这是**静态 + 定向排查**，不等于"全量跑过证明不会"。
3. **仓库工作区在本卡执行期间不是干净的**（多卡并发）：本卡开始时 `git status --porcelain` 为空，结束时出现 `DYNGATE1.md / LEDGER2.md / MINSCORE1.md / data/eval/minscore1_*.jsonl / test_arch_stage_contract.py / test_descriptor_registry_concurrent_load.py / test_dynamic_loads_high_exemption.py / test_minscore2_chinese_recall.py` 等**其它卡的改动**。**本卡一个字都没碰**（全程只用只读 git 命令 + 仓库外写入）。
4. **`data/audit/**` 的近期 mtime 不能归因**：本卡观测到 `data/audit/audit_chain.db-wal`、`knowledge_audit.jsonl` 等 mtime 是当天 01:0x–01:1x，与其它卡的活动窗口重叠。**本卡能证明的**：本卡跑的 14 个守卫在**干净副本**上运行后留下的新增文件里**没有 `data/audit/**`**（§3.6 逐条列出）。**不能证明**：仓库内那些 mtime 是谁写的。
5. **仓库内对照跑留下的残留物**：本卡的仓库内对照在 `test_reports/logs/` 下产生了 `test_20260927_01xxxx.log`（该目录已 gitignore，不在 `git status` 里）。窗口内也**有其它卡**在跑 pytest，本卡无法逐条区分归属。`data/skills_mgmt.json` / `data/skills.json` / `data/descriptors.json` / `data/skills_repo/.index/cache.json` / `data/agent_lines/_active.json` 的 mtime **全部停留在 2026-09-26 或更早**（见 §8），即**本卡没有改动这些真实台账**。
6. **"在 CI 上会不会真的执行"我只做到了"会被某个 job 收集到"**：`split_unit_tests.py` 的分配是**实测**的（本卡跑过 6 个 shard 的清单），但 ci.yml 用 `-n 2 --dist=loadscope`，**同 worker 内的执行顺序**与我在干净副本上的单文件运行不同 ⇒ "顺序敏感"的那一条（§3.5 的 :737）在 CI 上到底执行不执行，**本卡无法确定**。
7. **未做**：把 `test_date_shift_blindspots_guard.py` 的四臂（CONTROL / ±400）在干净检出上复跑（`--runslow` + 跨进程，成本高）。只复刻了 `date-shift-guard.yml` 的 static-scan 两个 step。

---

## 8. 残留物自证

**仓库内**：

~~~
$ git status --porcelain   （本卡开始时为空；结束时如下 —— 全部属于并发工作的其它卡）
 M agent/descriptors/backfill.py
 M agent/descriptors/registry.py
 M agent/digestion/stage.py
 M agent/server_port_guard.py
 M scripts/detect_dynamic_loads.py
 M scripts/run_s1_02_backfill.py
 M tests/unit/test_background_tasks_routes.py
 M tests/unit/test_digital_life_comprehensive.py
 M tests/unit/test_graceful_shutdown_persist.py
 M tests/unit/test_health_retrieval_endpoint.py
 M tests/unit/test_legacy_memory_routes.py
 M tests/unit/test_policy_integration.py
 M tests/unit/test_s1_02_s3_01_fixpoint_guard.py
 M tests/unit/test_search_tools.py
 M tests/unit/test_server_routes_registration_inventory.py
 M tests/unit/test_yunshu_mcp_server.py
?? agent/descriptors/stage_contract.py
?? data/eval/minscore1_negative_set.v1.jsonl
?? data/eval/minscore1_query_set.v1.jsonl
?? docs/audit_skill_governance/DYNGATE1.md
?? docs/audit_skill_governance/LEDGER2.md
?? docs/audit_skill_governance/MINSCORE1.md
?? tests/unit/test_arch_stage_contract.py
?? tests/unit/test_descriptor_registry_concurrent_load.py
?? tests/unit/test_dynamic_loads_high_exemption.py
?? tests/unit/test_minscore2_chinese_recall.py
（+ 本报告 docs/audit_skill_governance/CI2.md）
~~~

**HEAD 未动**：`git log --oneline -1` 仍是 `70aca896`（本卡未 commit / add / checkout / stash / worktree）。

**生产台账未被本卡写入**（当前 mtime）：

~~~
data\skills_mgmt.json                      2026-09-26 07:46:05  188467 bytes
data\skills.json                           2026-09-26 07:47:14  16677 bytes
data\descriptors.json                      2026-09-26 15:22:34  157518 bytes
data\skills_repo\.index\cache.json         2026-09-26 07:46:13  43280 bytes
data\agent_lines\_active.json              2026-09-20 00:44:58  25 bytes
data\tool_index.json                       2026-09-18 22:47:33  39741 bytes
~~~

**仓库外（全部在 `C:\Users\Administrator\` 下，不在仓库里）**：

~~~
ci2_head.tar                 445,358,080 B   git archive 的原始 tar
ci2_clean\                   干净检出（每步跑完已 reset 回 pristine：23442 文件、0 多 0 少）
ci2_evidence\                全部原始输出（clean__* / repo__* / wf_* / deps__* / nobm25__* /
                             notiktoken__* / blk_* / marker__* / deps_modules.txt /
                             clean_checkout_negative_checks.txt / untracked_*.json）
ci2_probe\ci2_probe*.py      探针插件（依赖 dump + 模块封禁）
ci2_*.py                     驱动脚本（run_tests / run_workflows / run_extra / reset / verify /
                             mkevidence / extract / shards / deps / probe_maintrack / an / an2）
~~~

**进程**：本卡全程没有 `taskkill` 任何进程；所有耗时任务都用后台 job 跑并自然结束（8 个 job 全部 completed）。
