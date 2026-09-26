# CI-1 · CI 接入卡（哪些测试是 CI 安全的 + 接了哪些门 + 本地按 CI 口径实跑）

- **卡号**：CI-1（CI 接入卡）
- **日期**：2026-09-27
- **本卡起始 HEAD**：`5c9ace10`（分支未动，48 卡改动未提交）
- **本卡结束时 HEAD**：`f74dce16`（**主审计在本卡进行中把这 48 卡提交了**，提交时间 2026-09-27 00:03:43，提交信息「audit(skills): 云枢技能治理与路由方案 V1.0 · 独立审计与重构实施（48 卡批次）」）
- **本卡自身**：**没有执行任何 git commit / git add**，也没有整文件 checkout
- **判定语义决策权**：由本卡（主审计）自行裁定，未再向任何人提问
- **零出网**：本卡全程未访问网络（无 pip install、无联网搜索、无 HF 下载）；所有"依赖清单"都是**读本机已装模块 + 静态导入链**推出来的

> ⚠️ **状态变更披露**：任务书写的是「HEAD=5c9ace10，工作区已有 48 张卡的未提交改动」。实际执行中 HEAD 变成了 `f74dce16` —— 48 卡批次**已被提交**。这对本卡是**有利**的：本卡要回答的核心问题「干净 checkout 上会不会红」现在可以用**权威来源**（git ls-files / git archive HEAD）直接造出来，不必再靠"索引 + 未跟踪清单"近似。本文所有最终结论（k 系列）都跑在**从 HEAD 导出的干净副本**上。

---

## 0. 方法：怎么造"干净 checkout"（这一步决定后面所有结论可不可信）

| 编号 | 造法 | 结果 | 用途 |
|---|---|---|---|
| sim-A | `git ls-files -c` 全量复制（索引文件清单，含非 ASCII 名）→ `...\Temp\ci1\repo` | 20629 文件（提交前的索引+未跟踪口径） | 提交前的前置结论（g / w / p / c 系列） |
| sim-B | `git archive HEAD \| tar -xf` → `...\Temp\ci1\repo_head` | 23435 文件，**但 Windows bsdtar 把非 ASCII 文件名解成乱码** | **已废弃**：它造出过一条假红（见 §3.4 的 h4） |
| sim-C | `git ls-files -c` 全量复制 **HEAD 的索引清单**（重建，UTF-8 正确） | 23435 文件，0 missing | **本卡最终证据（k 系列）全部跑在这里** |

sim-C 关键事实（与"CI 干净 checkout"逐项对齐）：

```
.env                                                       ABSENT
data/skills.json                                           ABSENT
data/eval                                                  ABSENT      <- 路由冲突用例集不在里面（§1 门 6）
data/skills_mgmt.json                                      ABSENT
data/skills_repo/.migration/descriptions.baseline.json     PRESENT     <- G1-B/M1 基线已入库（§1 门 1）
data/tool_index.json                                       PRESENT
docs/rfc/云枢能力清单盘点表.md                                      PRESENT
tests/unit/test_route_conflict_cases.py                    PRESENT
```

统一执行的 CI 口径环境（与 workflow 实际命令同一行，见 §3）：

```
CI=true  GITHUB_ACTIONS=true  DISABLE_NATIVE_EXT=1
HF_HUB_OFFLINE=1  TRANSFORMERS_OFFLINE=1
CP_ENV_FILE=C:\Users\Administrator\AppData\Local\Temp\ci1\__no_such_env_file__.env   # 「CI 缺 .env」的直接模拟
PYTHONPATH=.
（检索类门另加 AGENT_HYBRID_EMBEDDING=0，与 tool-retrieval-ci.yml 的 workflow 级 env 一致）
```

**为什么还要 DISABLE_NATIVE_EXT=1**：tests/unit/conftest.py:27 的 `_CI_LINUX = sys.platform == 'linux' and bool(os.environ.get('CI'))` 只在 **Linux + CI** 下把 chromadb / sentence_transformers 封禁成 ImportError。本机是 Windows，光设 CI=true 走不到那条分支 ⇒ 用仓库自带的等价开关 DISABLE_NATIVE_EXT=1（同文件 :33）把"CI Linux 上没有原生扩展"这一环境差异补上。**这是本卡唯一的环境近似，已在 §5 登记为残留风险。**

---

## 1. 第 1 步：逐门「CI 安全 / 需要前置 / 不安全」结论与证据

> 判据：**CI 安全** = 干净 checkout 上按 workflow 原样命令跑，退出码符合该门约定，不需要任何本机独有物资；**需要前置** = 依赖某个必须显式补上的东西（否则假红或**假绿**）；**不安全** = 干净 checkout 上必然红 ⇒ 本卡不接入。

### 门 1 · 技能描述唯一源 · tests/unit/test_skill_description_single_source.py
**结论：原本「不安全」（干净 checkout 必红）→ 本卡已做最小改动使其「CI 安全」。**

| 依赖 | 是否入库 | CI 里在不在 |
|---|---|---|
| data/skills_repo/**（含 5 条新迁移技能） | 入库 | 在 |
| data/capability_manifest.json | 入库 | 在 |
| data/skills_descriptions_overlay.json | 入库 | 在 |
| plugins/skills.py | 入库 | 在 |
| data/skills_repo/.migration/descriptions.baseline.json（M1 基线） | **入库** | 在（见下） |
| data/skills.json / agent/data/skills.json | gitignored | **不在**（3 条断言显式 skip） |
| data/skills_mgmt.json（主轨） | gitignored | **不在**（1 条断言显式 skip） |
| data/descriptors.json（descriptor 台账） | gitignored | **不在**（1 条断言显式 skip） |
| data/skills_repo/.index/cache.json | gitignored | **不在**（1 条断言显式 skip） |

- **M1 基线确认（任务书点名要确认的那条）**：`git cat-file -e HEAD:data/skills_repo/.migration/descriptions.baseline.json` → **exit 0**；blob `42a657a8c37fed818e0678228fa0acc1d37efa0b`，`git cat-file -s` = 20906 B（LF 规范化后），`git ls-files -s` 显示 `100644 42a657a8… 0`；`git hash-object -w --path …` 对工作区文件算出的哈希**与索引 blob 逐字节相同**（工作区 21266 B = 20906 + 360 个 CRLF 差额）。sim-C 里该文件 PRESENT 且 JSON 可解析（keys 含 skills，23 条）。⇒ **一旦提交它就在仓库里，守卫不会因"基线缺失"而红**（k1 / k1b 两次运行里 baseline fixture 都正常加载，没有出现 "M1 基线缺失" 失败）。
- 网络 / HF：不涉及（不拉起向量腿；conftest 的原生扩展封禁 + HF 离线兜住）。
- **CI 分支差异**：文件内无 GITHUB_ACTIONS 分支；但见下方"干净 checkout 实测 2 条红"。
- **实测（本卡打到的最重要一条）**：在 sim-C 上跑 **HEAD 原版**测试文件：

```
$ python -m pytest tests/unit/test_skill_description_single_source.py -q --timeout=120 --no-header -p no:cacheprovider
E   AssertionError: 合并视图行数应为 30，实得 28
E   AssertionError: 主轨独有集合已变化：新增 [] / 消失 ['global-core-principles', 'skill']
2 failed, 27 passed, 4 skipped, 1 warning in 7.94s        <- exit 1
```

  根因（实测，不是推断）：
  1. test_merged_view_description_equals_skill_md 把 **30** 写死；30 = 文件轨 28 + 主轨独有 2，而主轨 data/skills_mgmt.json 被 .gitignore:148 排除 ⇒ 干净 checkout 上恒为 28。
  2. 更隐蔽的一条：**本仓读路径会在同一轮里把 data/skills_mgmt.json 创建成空对象 {}**（实测：跑完该文件后 `...\repo_head\data\skills_mgmt.json` 内容就是 ``，同时 data/skills_repo/.index/cache.json 被生成）。于是 test_main_track_only_allowlist_is_exact 的 `if not mgmt_path.exists(): skip` **不再成立**，它读到一个空主轨 ⇒ 判成"主轨独有集合消失了" ⇒ 红。**这条随 pytest 随机顺序在"红 / skip"之间摇摆**（本机 pytest-randomly 生效，两次运行顺序不同）。
- **最小改动（未放宽任何断言，全在 tests/unit/** 允许面内）**：
  - 新增 `_main_track_ids()`：把「主轨文件不存在」与「主轨存在但没有条目（空对象 / 坏 JSON）」并成**同一类"主轨无数据"**，并在注释里写明 CI 上这个空文件是**读路径创建**的。
  - test_merged_view_description_equals_skill_md：`len(rows) == 30` → `len(rows) == len(meta_index) + len(主轨独有)`，**两种口径都要求恰好相等**（本地 = 28+2 = 30，与改前逐字同强度；CI = 28），并新增"文件轨 id 必须全部进合并视图"的缺项断言。
  - test_main_track_only_allowlist_is_exact：主轨无数据时**显式 skip 并打印理由**（不是"通过"）；有数据时断言一字未改（仍是"恰好等于 KNOWN_MAIN_TRACK_ONLY"）。
- **改后实测**：sim-C（干净 checkout）`27 passed, 6 skipped, exit 0`；本机工作区 exit 0。6 条 skip 全部带显式理由（见 §3）。

### 门 2 · 技能/清单一致性双口径 · scripts/compare_skills_legacy_vs_repo.py（skills-check.yml）
**结论：CI 安全（作为"机制门"），但它在 CI 上**不是**数据一致性门 —— 这一点必须写清，否则会被误读成"CI 在守新旧格式一致性"。**

- `--ci` 在干净 checkout（无 data/skills.json）：exit 0，输出 `RESULT: PASS-SKIP(not_applicable)` **显式结论**（k2）。
- `--verify --legacy <不存在的路径>`：exit 1，`RESULT: FAIL(legacy_missing) —— 不是 ALL_MATCH`（k3）⇒ workflow 里那条"反过来必须非零"的断言成立。
- 依赖：实测导入链需要 pyyaml + prometheus-client（原清单只有 pyyaml，已补齐）。
- **本地/CI 反向差异（重要）**：本机（data/skills.json 存在，且是**旧快照 30 条 vs 文件轨 28 条**）跑同一命令 --ci 是 **exit 1 / HAS_DIFF**（c4）。也就是**"CI 绿、本地红"**。数据一致性在 CI 上实际由门 1（skill.md ↔ manifest/信封/overlay）与门 3（manifest 派生）守着。

### 门 3 · capability_manifest 派生一致 · scripts/sync_capability_manifest.py --check
**结论：CI 安全。**

- 干净 checkout：exit 0，`[OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21）`（k4）。
- 依赖：pyyaml；另需**入库**的 docs/rfc/云枢能力清单盘点表.md（sim-C 里 PRESENT）。
- 附注：sim-B（tar 乱码那版）上它报 `[FAIL] 盘点表不存在`（h4，exit 1）—— 那是**取件方法**把非 ASCII 文件名解坏了，不是门的问题；换 sim-C 后 exit 0（k4）。排查过程保留在 §3.4。

### 门 4 · 检索质量闸 · tests/unit/test_s10_03_retrieval_quality_gate.py
**结论：CI 安全，但「需要前置」—— 必须显式安装 rank-bm25 并断言其可用，否则会静默假绿。**

- 干净 checkout：`13 passed, 0 skipped, exit 0`（k5，含"真技能库 + 真 BM25"那一组）。
- 依赖物资：只用 data/skills_repo/**（入库）+ agent/skills_mgmt/{loader,file_store}.py；**不依赖任何 gitignored 运行期文件**。
- **假绿风险（本卡点名的重点）**：该文件 `Test真库同输入对照` 的 fixture 用 `pytest.importorskip("rank_bm25")`，而**本批各 workflow 的 pip 清单里从来没有 rank-bm25**，多个 workflow 的 `pip install -e .` 还是 `|| true` ⇒ 只要 runner 上它没装成功，那 2 条最硬的断言会**静默 skip、job 照样绿**。（补充的准确边界：`pyproject.toml:145` 确实声明了 `rank-bm25==0.2.2`，所以 `pip install -e .` **若成功**会带上它；但把最硬的断言押在"整包安装成功"上不稳妥 —— 本卡新增的那个 job 因此用显式清单 + 前置断言兜底。）处置：workflow 显式 `pip install … rank-bm25 …` + 独立前置步骤 `python -c "import rank_bm25"`（失败即点名，不许降级成 skip）。
- 网络/HF：CI Linux 下 sentence_transformers 被封禁 ⇒ 向量腿降级，不出网（conftest 另补 HF 离线键）。
- CI 分支差异：CI / GITHUB_ACTIONS 只影响 conftest 的封禁分支，不改变本门判据。

### 门 5 · 开关零缺口 · tests/unit/test_settings_registry.py（+ scripts/scan_settings.py --check）
**结论：CI 安全。**

- 干净 checkout：测试 `56 passed, exit 0`（k6）；扫描器 exit 0，`缺口（未注册）: 0 / 注册但未读到: 0 / 未声明的动态家族: 0 / 未声明的排除项: 0`、`结论：零缺口 ✅`（k7，扫 754 文件）。
- 依赖物资：只读**入库源码**（DEFAULT_ROOTS = 11 个顶层包 + mcp_services/ + 仓库根 10 个散装 .py）；不读任何 gitignored 文件；模块级第三方依赖实测**只有 pytest 系**（探测 p10）。
- 网络/HF：无。CI 分支差异：无（本文件里不出现 GITHUB_ACTIONS）。

### 门 6 · 路由冲突回归集 · scripts/eval_route_conflict.py（121 条）+ tests/unit/test_route_conflict_cases.py
**结论：不安全 —— 干净 checkout 上必然红。本卡不接入。**

- **用例集没入库**：data/eval/ 被 .gitignore:476 整目录排除；`git cat-file -e HEAD:data/eval/route_conflict_cases.v1.jsonl` 与 v2 **都失败**，`git ls-tree -r --name-only HEAD -- data/eval` **输出为空**，`git ls-files -o -i` 里它们仍是 IGNORED。⇒ 提交发生后**依然不在仓库里**。
- 干净 checkout 实测：
  - `python scripts/eval_route_conflict.py --no-embedding` → **exit 1**，`FileNotFoundError: …\data\eval\route_conflict_cases.v1.jsonl`（k10）
  - `python -m pytest tests/unit/test_route_conflict_cases.py` → **exit 1**，61 条里大量 ERROR（fixture `assert _CASES_V2.is_file()` 直接炸）+ test_CLI退出码为0当达到下限 等 FAILED（k11）
- 本机（用例集存在）对照：`--no-embedding` → exit 0，`决策层通过 48/111（43.24%）`、`下限 48（BASELINE_BY_VERSION[v2][bm25_only]）`、`下发层 expected 命中 88/111`（w1）；测试文件 `61 passed in 3.43s`（w2）。这正是任务书说的「本地绿、干净 checkout 红」。

### 顺带核实（不在任务书列表里，但属"可接入的门"的候选面）

| 对象 | 结论 | 证据 |
|---|---|---|
| scripts/verify_migrated_skills.py（**本批改过的** skills-check.yml 里同 job 的第二步） | **CI 安全** exit 0 | k8 |
| scripts/detect_dynamic_loads.py（skills-check.yml 的 dynamic-load-gate，push master 时 continue-on-error: false） | 干净 checkout 上 exit 1，**2 处 HIGH**（agent/tools/persistence.py:380、:383）；该文件**不是本批改的** ⇒ **预先存在的红**，与本卡无关但会挡住下次 master 推送 | k9；git status 显示该文件未被本批触碰 |
| tests/unit/test_tools_prompt_alignment.py（性能类门，任务书要求先读它的 _env_allowance） | **已经接入**（ci.yml 6-shard 全量单测 + serial lane，见 ci.yml:500-532）；有界余量口径 `PERF_CI_ALLOWANCE = 3.0`（CI/GITHUB_ACTIONS）/ `PERF_MAX_ENV_ALLOWANCE = 3.0`（本地满载上限，用同刻环境校准换算），**不是绝对毫秒**。本卡未改它 | 测试文件 :699-711、ci.yml:500-532 |
| 本批其它新测试（test_skill_search_description_source.py / test_three_legs_meta_zh_parity.py / test_skill_h3_migration.py / test_skill_meta_zh_recall.py / test_s2_gate_is_not_false_green.py / test_ret1r_* / test_settings_registry_e1d.py 等） | **本卡未接入**（理由见 §4） | 只做静态分诊；**未在干净 checkout 上实跑** |

---

## 2. 第 2 步：接了哪些门 + 判定语义与论证

### 2.0 改动清单

| 文件 | 动作 | 说明 |
|---|---|---|
| .github/workflows/skill-description-single-source.yml | 改 | 依赖清单按实测导入链补齐；paths 补齐；加 workflow 级离线 env；补 CI-1 说明 |
| .github/workflows/tool-retrieval-ci.yml | 改 | 新增 job skill-retrieval-quality-gate；workflow env 加 HF 离线两键；paths 补输入面 |
| .github/workflows/settings-registry-gap-guard.yml | **新增** | 开关零缺口门（此前**没有任何 workflow** 跑它） |
| tests/unit/test_skill_description_single_source.py | 改 | 让门 1 在干净 checkout 上安全（§1 门 1 的三处最小改动，未放宽断言） |
| docs/audit_skill_governance/CI1.md | 新增 | 本报告 |

### 2.1 门 1（描述唯一源）—— 判定语义

- **接入位置**：skill-description-single-source.yml（该 workflow 是本批 G1-B 新建的；本卡负责把它变成"**在 CI 上真的会绿**"）。
- **判定语义**：`python -m pytest tests/unit/test_skill_description_single_source.py -v --timeout=120 --no-header -p no:cacheprovider` 的**退出码**（全过=绿）。skip 不算通过，日志逐条打印理由。
- **基线选择**：**断言型门，无基线概念** —— 不做比例/阈值判定，避免把"改进中"判成红。
- **依赖前置（本卡补的）**：`pip install pyyaml pydantic prometheus-client pytest pytest-timeout pytest-asyncio` + 一条前置导入断言。**理由**：原清单只有 pyyaml + pytest 系，而实测导入链需要 **pydantic**（agent/descriptors/models.py ← descriptors.backfill）与 **prometheus-client**（agent/capregistry → monitoring 观测链）⇒ 原清单在干净 runner 上会在 **import 期** ImportError，表现为"门坏了"而不是"代码坏了"。
- **paths 自证**：补 agent/capregistry/**（信封断言的真实依赖）、tests/conftest.py、tests/unit/conftest.py（.env 地板 / HF 离线 / CI 原生扩展封禁）、pytest.ini；并把 push 的 paths 对齐 pull_request（原来 push 面更窄 ⇒ 同一份改动走 PR 触发、直接 push master 反而不触发）。

### 2.2 门 2 / 门 3（skills-check.yml 里那两条脚本门）—— 判定语义

- **门 2**：compare_skills_legacy_vs_repo.py --ci 必须 exit 0 **且**结论行是显式的 `PASS-SKIP|ALL_MATCH`（workflow 用 `grep -qE` 卡这一点）；外加一条**反向**断言：--verify --legacy <缺失> **必须非零**。语义 = 「缺失不得被当成通过」——哪怕它给出的结论是 NOT_APPLICABLE。
- **门 3**：sync_capability_manifest.py --check exit 0（只校验不写；清单是派生物，漂移必须红）。
- **基线选择**：都是布尔门，无阈值。

### 2.3 门 4（检索质量闸）—— 判定语义

- **接入位置**：tool-retrieval-ci.yml 新增 job skill-retrieval-quality-gate（该 workflow 已在守"工具检索 recall / 负样本 / 专有名词"，且已触发 agent/skills_mgmt/loader.py 与 bm25_searcher.py ⇒ 这是**同一主题的既有家**）。
- **判定语义**：13 条断言全过（退出码 0），且**必须 0 skip** —— 由前置步骤 `python -c "import rank_bm25"` 保证：做不到就**点名失败**。**这是本卡对"不许静默 skip 成假绿"的具体落实。**
- **基线选择**：断言型（有界相似度的量纲纪律），无阈值；不引入任何绝对毫秒。

### 2.4 门 5（开关零缺口）—— 判定语义

- **接入位置**：新 workflow settings-registry-gap-guard.yml。**Why 新开而不是塞进现有 workflow**：现有 config-drift-guard.yml 只触发 agent/monitoring/observability_config.py 等 4 条路径，而"新读一个开关"可以发生在 DEFAULT_ROOTS 里的**任何一个包**；塞进去就必须把它的 paths 扩到同一片，反而让那个 workflow 的职责变糊。新文件把"paths = 谁能新增开关"这条语义写清楚。
- **判定语义**：两段都要绿 —— ① scan_settings.py --check 退出码 0（四类计数全 0）；② test_settings_registry.py 全过（登记值不得写进 os.environ、扫描根必须覆盖生产代码等）。
- **基线选择**：零缺口是**不变量**，没有"当前基线"可钉；不给任何比例宽容。
- **paths 自证（刻意宽）**：agent/** planning/** memory/** sensor/** core/** utils/** cognitive/** cloudshu/** lifetrace/** persona/** plugins/** mcp_services/** + 仓库根 10 个散装 .py + 门自身。**理由**：窄 paths 会让"新开关从别的包溜进来"时门不触发 ⇒ 门只在纸面上成立。

### 2.5 门 6（路由冲突集）—— **本卡不接入**，但判定语义在这里定死（供 owner 照做）

任务书要求回答"下限钉在哪"。**结论：钉「不许低于当前基线」，且用 CLI 的默认下限（表驱动），不写死高目标。**

- **选定基线**：`BASELINE_BY_VERSION["v2"]["bm25_only"].decision_pass = 48`（scripts/eval_route_conflict.py:80-84），下限量纲 = **决策层通过的正样本数**（v2：121 条用例 = 111 正样本 + 10 负/澄清）。
- **实测核对**（w1，本机、用例集在、`--no-embedding`）：
```
用例集   : …\data\eval\route_conflict_cases.v2.jsonl（版本 v2）  正样本 111 / 负·澄清 10
达标判据 : 用例集自检通过 且 决策层通过数 >= 下限
下限     : 48（BASELINE_BY_VERSION[v2][bm25_only]）
实测     : 决策层通过 48/111（43.24%）；下发层 expected 命中 88/111
退出码   : 0
```
- **为什么是 48 而不是一个"高目标"**：
  1. v2 的新样本是**刻意压难**的（负/澄清 10 条 + 反向对 + 形态补充），48/111 是**现状读数**；钉 80% 之类的目标 = 把一个"正在改进中"的系统长期判红，门会被人习惯性忽略。
  2. 回归门的语义应是**棘轮**：只对"比现在更差"报警。CLI 的实现正好是 `rc = 0 if passed >= min_pass`（eval_route_conflict.py:1266）—— 是 `>=` 下限，不是 `==` 目标。
  3. **为什么不加百分比宽容**：`--no-embedding` 走纯 BM25（无模型、纯 Python 分词），是确定性的；一条用例约 0.9pp，任何"95% 基线"式的宽容都会把"退化 2 条"吞掉 ⇒ 假绿。
- **为什么用默认下限而不是在 workflow 里写 `--min-pass 48`**：避免**两个事实源**。默认值来自 BASELINE_BY_VERSION，而这张表本身被 tests/unit/test_route_conflict_cases.py 的基线断言守着；脚本还会把生效下限与来源打进日志（`下限 : 48（BASELINE_BY_VERSION[v2][bm25_only]）`）。日志里看得见，就不需要 workflow 再抄一遍。
- **为什么不直接把 tests/unit/test_route_conflict_cases.py 搬进 CI**（本卡的一个关键取舍）：该文件里 test_基线可复现（v1）与 TestEvalRunsV2 的两条是**精确相等**口径（27、48），**任何一次真改进都会判红** —— 与"钉下限、不钉高目标"直接冲突。它是**本机棘轮**（防基线数字悄悄漂移），适合留在本地；CI 上应跑 CLI 的 @@B@>>=` 下限门。
- **出网**：`--no-embedding` 关掉向量腿（AGENT_HYBRID_EMBEDDING=0），全程零 LLM、零下载。
- **它现在不能接入的唯一原因是用例集没入库**（§1 门 6）。**最小改动（3 处）**：

```
# ① .gitignore：给 .gitignore:476 的 data/eval/ 加两行例外
data/eval/
!data/eval/route_conflict_cases.v1.jsonl
!data/eval/route_conflict_cases.v2.jsonl

# ② 入库（本卡不能做：硬约束禁止 git add；且 .gitignore 不在本卡文件范围内）
git add -f data/eval/route_conflict_cases.v1.jsonl data/eval/route_conflict_cases.v2.jsonl
```

```
# ③ 新 workflow —— 判定语义即上文：默认下限、不写 --min-pass
name: Route Conflict Regression
on:
  pull_request:
    branches: [master, main]
    paths:
      - 'data/eval/route_conflict_cases.v*.jsonl'   # 用例集本身
      - 'data/tool_index.json'                     # 检索侧候选全集
      - 'agent/tool_router_hybrid.py'              # 检索/融合/下发集实现
      - 'scripts/eval_route_conflict.py'
      - '.github/workflows/route-conflict-regression.yml'
env:
  AGENT_HYBRID_EMBEDDING: '0'      # 纯 BM25 = 确定性 + 零出网（与 tool-retrieval-ci.yml 同口径）
  HF_HUB_OFFLINE: '1'
  TRANSFORMERS_OFFLINE: '1'
  PYTHONPATH: '.'
jobs:
  route-conflict-floor:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v6
      - uses: actions/setup-python@v6
        with: { python-version: '3.12', cache: 'pip' }
      - run: python -m pip install --upgrade pip && pip install pytest pytest-timeout pytest-asyncio
      - name: 用例集必须在库（缺了要点名，不许静默降级）
        run: python -c "import pathlib,sys; p=sorted(pathlib.Path('data/eval').glob('route_conflict_cases.v*.jsonl')); sys.exit('用例集缺失: %s' % p) if not p else print(p)"
      - name: 决策层下限（下限 = 脚本内 BASELINE_BY_VERSION，日志会打印来源）
        run: python scripts/eval_route_conflict.py --no-embedding
```

### 2.6 性能类门的处置

本卡**没有接入任何新的性能门**。任务书要求"若接入必须用有界余量、不要绝对毫秒"—— 本卡核实到的那条性能门（tests/unit/test_tools_prompt_alignment.py 的 100KB 对齐预算）**已经在 CI 里**（ci.yml 6-shard + serial lane），且**已经是有界余量**：`PERF_CI_ALLOWANCE = 3.0 if (CI or GITHUB_ACTIONS) else 1.0`、`PERF_MAX_ENV_ALLOWANCE = 3.0`（本地满载上限，与环境校准 `_calib_ms()` 同刻测量），并写明了"退化倍率"（本地空载约 7x / 满载与 CI 约 21x）。**本卡未改动它**（属 TESTHYG-1 与既有 lane 的处置，改动会越权）。

---

## 3. 第 3 步：本地按 CI 口径实跑（原始输出含退出码）

**全部命令与 workflow 里的命令行逐字相同**；运行目录 = sim-C（`...\Temp\ci1\repo_head`，HEAD 的干净 checkout + 本卡修好的那个测试文件）。
环境：CI=true GITHUB_ACTIONS=true DISABLE_NATIVE_EXT=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CP_ENV_FILE=<不存在路径> PYTHONPATH=.（检索门另加 AGENT_HYBRID_EMBEDDING=0）。
原始输出全文在 `C:\Users\Administrator\AppData\Local\Temp\ci1\evidence\*.txt`（每个文件头部都记了 CWD / CMD / ENV / TIME / EXITCODE / ELAPSED）。

### 3.1 新接入的各门（干净 checkout + 缺 .env）

| 证据 | 命令（= workflow 命令行） | 退出码 | 关键输出 |
|---|---|---|---|
| k1 | python -m pytest tests/unit/test_skill_description_single_source.py -v --timeout=120 --no-header -p no:cacheprovider | **0** | 27 passed, 6 skipped, 1 warning in 8.64s |
| k1b | 同上，但用 **HEAD 原版**测试文件 | **1** | 2 failed, 27 passed, 4 skipped；AssertionError: 合并视图行数应为 30，实得 28 + 主轨独有集合已变化…消失 ['global-core-principles','skill'] |
| k2 | python scripts/compare_skills_legacy_vs_repo.py --ci | **0** | [compare] PASS-SKIP: legacy 快照不存在 / RESULT: PASS-SKIP(not_applicable) |
| k3 | python scripts/compare_skills_legacy_vs_repo.py --verify --legacy data/__ci1_absent__.json | **1**（该门要求非零） | [compare] FAIL: legacy 快照不存在 / RESULT: FAIL(legacy_missing) —— 不是 ALL_MATCH |
| k4 | python scripts/sync_capability_manifest.py --check | **0** | [OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21） |
| k5 | python -m pytest tests/unit/test_s10_03_retrieval_quality_gate.py -v --timeout=120 --no-header -p no:cacheprovider | **0** | 13 passed in 1.62s（**0 skip**） |
| k6 | python -m pytest tests/unit/test_settings_registry.py -q --timeout=120 --no-header -p no:cacheprovider | **0** | 56 passed in 33.27s |
| k7 | python scripts/scan_settings.py --check | **0** | 缺口（未注册）: 0 … 结论：零缺口 ✅ |
| k8 | python scripts/verify_migrated_skills.py（skills-check.yml 同 job 第 2 步） | **0** | 无输出即通过 |
| k9 | python scripts/detect_dynamic_loads.py（dynamic-load-gate） | **1** | HIGH: 2 / agent\tools\persistence.py:380,383（**预先存在**，非本批引入） |
| k10 | python scripts/eval_route_conflict.py --no-embedding（**本卡未接入**，此处是"必红"证据） | **1** | FileNotFoundError: …\data\eval\route_conflict_cases.v1.jsonl |
| k11 | python -m pytest tests/unit/test_route_conflict_cases.py …（同上） | **1** | 61 collected，多处 ERROR + AssertionError: auto 没选到 v2（选到 v1；理由 data/eval 下没有 vN 用例集…） |

门 1 的 6 条 skip（**逐条有理由，不是静默**）：

```
SKIPPED [1] test_skill_description_single_source.py:737: 索引缓存不存在（生成物，未构建过检索缓存）
SKIPPED [1] test_skill_description_single_source.py:454: descriptor 台账不存在（本机未跑过 M8）
SKIPPED [1] test_skill_description_single_source.py:494: legacy 快照不存在（CI 环境 data/skills.json 被 gitignore）
SKIPPED [1] test_skill_description_single_source.py:532: 两份 legacy 快照之一不存在（CI 环境被 gitignore）
SKIPPED [1] test_skill_description_single_source.py:705: legacy 快照不存在（CI 环境）
SKIPPED [1] test_skill_description_single_source.py:587: 主轨无数据（CI 干净 checkout：data/skills_mgmt.json 被 .gitignore 排除，
            且读路径会在同一轮里把它创建为空对象）⇒ 「主轨独有集合」无定义，本断言在 CI **不适用**（不是通过；…）
```

### 3.2 「缺 .env」对照（任务书点名要做的那个对照）

**先给机制证据**（p12）：tests/conftest.py 在**导入期**无条件把 CP_ENV_FILE 改写成临时隔离文件，并 setdefault 两个 HF 离线键：

```
BEFORE import tests/conftest.py: CP_ENV_FILE = C:\Users\Administrator\agent\.env
AFTER  import tests/conftest.py: CP_ENV_FILE = C:\Windows\TEMP\pytest_dotenv_floor_zm47q9cx\isolated.env
FLOOR_REDIRECTED = True
HF_HUB_OFFLINE = 1 / TRANSFORMERS_OFFLINE = 1
```

⇒ **凡是走 pytest 的门，.env 在不在都不影响判据**（地板生效）。下表是"有 .env（CP_ENV_FILE 指向仓库真实 .env，本机工作区）"与"缺 .env（指向不存在路径，干净 checkout）"的实测对照：

| 门 | 有 .env（c 系列） | 缺 .env（k 系列） | 结论 |
|---|---|---|---|
| 描述唯一源（门 1） | exit 0（c2） | exit 0（k1） | 与 .env 无关 |
| 检索质量闸（门 4） | exit 0（c3） | exit 0（k5） | 与 .env 无关 |
| 开关零缺口-测试（门 5） | exit 0（c1） | exit 0（k6） | 与 .env 无关 |
| 开关零缺口-扫描器 | exit 0（c6） | exit 0（k7） | 与 .env 无关 |
| manifest --check（门 3） | exit 0（c5） | exit 0（k4） | 与 .env 无关 |
| compare --ci（门 2） | **exit 1（c4，HAS_DIFF）** | exit 0（k2，PASS-SKIP） | **差异来自本机存在陈旧的 gitignored 旧快照 data/skills.json，不来自 .env** |

**关于"缺 .env 会不会导致出网超时"**：本卡**没有观测到任何出网**。三条防线：① 运行时 HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1（与 test.yml / daily_regression.yml 同口径，我把它也写进了被改的两个 workflow 的 workflow 级 env）；② tests/conftest.py:101-102 的 setdefault 兜底；③ CI Linux 下 sentence_transformers 被 _BlockModules 封禁成 ImportError。**处置**：按任务书要求把 HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1 **显式写进 workflow**（脚本 job 尤其必要 —— 它们不加载 tests/conftest.py，拿不到 conftest 的兜底）。

### 3.3 路由冲突集：本地绿 / 干净 checkout 红（对照）

| | 本机工作区（用例集在） | 干净 checkout（用例集不在） |
|---|---|---|
| eval_route_conflict.py --no-embedding | exit 0（w1）：决策层通过 48/111、下限 48 | exit 1（k10）：FileNotFoundError |
| test_route_conflict_cases.py | exit 0（w2）：61 passed in 3.43s | exit 1（k11）：collected 61，ERROR/FAILED |

### 3.4 方法学附注（一次真实的假红，保留下来当教训）

用 `git archive HEAD | tar -xf` 造干净 checkout（sim-B）时，Windows 自带的 bsdtar 把**非 ASCII 文件名**解成了乱码：

```
tar -tf head.tar | grep rfc/     -> docs/rfc/云枢能力清单盘点表.md          （tar 内名字正确）
解出来的实际文件名                -> docs/rfc/浜戞灑鑳藉姏娓呭崟鐩樼偣琛_md   （字节被按错误代码页解释）
```

于是 sync_capability_manifest.py --check 报 `[FAIL] 盘点表不存在`（h4，exit 1）—— **这是取件方法的假红，不是门的问题**。改用 git ls-files -c 逐文件复制（sim-C）后同一命令 exit 0（k4）。后续任何"造干净 checkout"的卡请注意这一点。

---

## 4. 未接入的门与理由

| 门 / 对象 | 为什么没接 |
|---|---|
| **路由冲突回归集**（scripts/eval_route_conflict.py 121 条 + tests/unit/test_route_conflict_cases.py） | **干净 checkout 必红**：用例集 data/eval/route_conflict_cases.v*.jsonl 被 .gitignore:476 排除，且**不在 HEAD**（提交后仍不在）⇒ 接上去第一天就红。本卡不能 git add（硬约束），.gitignore 也不在本卡文件范围内 ⇒ 只给最小改动 + 现成 job（§2.5），由 owner 一条命令落地。 |
| tests/unit/test_route_conflict_cases.py（即使用例集入库，也**建议只留本地**） | 它的基线断言是**精确相等**口径（27 / 48），任何真改进都会判红 —— 与"钉下限、不钉高目标"冲突。CI 应跑 CLI 的 @@B@>>=` 下限门。 |
| 本批其它新护栏（test_skill_search_description_source.py、test_three_legs_meta_zh_parity.py、test_skill_h3_migration.py、test_skill_meta_zh_recall.py、test_s2_gate_is_not_false_green.py、test_ret1r_bm25_quality_gate.py、test_retrieval_silent_failures.py、test_settings_registry_e1d.py、test_tool_count_consistency.py、test_gate1_single_vector_quality_gate.py …） | **本卡范围点名的是 5 个门，这些不在其中**；它们各自"干净 checkout 是否绿 + 依赖是否齐"**没有被本卡实跑验证**，按"不许接一个必然红的门"的口径，未验证的不接。多数已有显式 skip / tmp_path 隔离（静态分诊），但**静态分诊不等于实测**。建议另开一张卡（或本卡验收后按同一套 sim-C 方法批量过一遍）。 |
| tests/unit/test_date_shift_blindspots_guard.py | 另有卡 TESTINFRA-2 在改它，**本卡按硬约束不碰**。 |
| detect_dynamic_loads.py 的 HIGH 阻断 | 不是"接不接"，而是它**当前就是红的**（2 处 HIGH，预先存在）——见 §5.5。 |

---

## 5. 未验证项与残留风险

1. **没在真正的 GitHub Actions runner 上跑过**（零出网预算）。本机近似手段：DISABLE_NATIVE_EXT=1 复刻"CI Linux 封禁原生扩展"。**未验证**：Ubuntu 上的分词/浮点/文件系统差异是否会让 test_s10_03 的真 BM25 对照翻转；pip install … 在 GHA 上的实际解析结果。
2. **门 1 在 CI 上恒有 6 条断言不执行**（依赖 gitignored 派生文件：legacy 快照 x3、descriptor 台账、主轨、索引缓存）。这些 skip 都有显式理由、且本地可跑，但 **CI 上确实覆盖不到**。特别是 test_main_track_only_allowlist_is_exact（主轨独有集合必须恰好 2 条）在 CI **无适用域**：主轨数据只存在于本机/生产。**残留风险：主轨被清空或塞进新 id 时，CI 不会红。**（本卡没有放宽它的断言强度，只把适用域显式化。）
3. **门 1 的修复依赖"读路径会把 data/skills_mgmt.json 创建成空对象"这一实测事实**。若将来这条副作用被修掉（那在 agent/skills_mgmt/ 里，本卡**不许碰**），_main_track_ids() 的第二个分支会变成死代码 —— 但不会产生假绿（只是回到"文件不存在 ⇒ 空集"）。
4. **依赖清单的确定性**：三个 job 的 pip 清单是按**模块级导入链实测**（dump site-packages）推的，不是"装齐再跑"。测试体内更深的路径若需要别的包，会表现为该门的失败（关键项 rank_bm25 已被前置断言变成硬失败而不是静默 skip）。**未验证**：在只装显式清单的 runner 上能否 100% 跑完（离线条件下造不出"最小依赖环境"来验证）。
5. **detect_dynamic_loads.py 在 HEAD 上是红的**（HIGH=2，agent/tools/persistence.py:380,383，importlib.util.spec_from_file_location / module_from_spec）。该文件**不是本批改的**，属**预先存在**状态；但 skills-check.yml 的 dynamic-load-gate 是 continue-on-error: false 且只在 push master 时跑 ⇒ **本批提交后的第一次 master 推送会被它挡住**。这是本卡**报出的、需要 owner 决策**的问题（白名单化 / 修代码 / 该 job 不设为 required，三选一）。
6. **路由冲突集的下限若将来接入**：48 这个基线是**本机（Windows / CPython 3.12.0）实测值**。CI 上的平台差异**未验证**；建议首次接入先跑一轮观察再设为 required check。这也是本卡坚持"先入库用例集、再开门"的原因。
7. **本机工作区跑 compare --ci 会红**（c4）：因为本机存在**陈旧**的 gitignored data/skills.json（30 条）而文件轨是 28 条。**这不是 CI 问题**，但会误导"本地复现 CI"的人：以 workflow 命令行在本地跑 --ci 时请预期 HAS_DIFF。

---

## 6. 回滚

本卡**没有** commit / add。回滚 = 把下面 4 个文件恢复成 HEAD（f74dce16，即本批的提交态，**不含** CI-1 的任何改动）：

```
# ① 两个被改的 workflow + 一个被改的测试文件（HEAD 里就是本批提交的原版）
git checkout -- .github/workflows/skill-description-single-source.yml
git checkout -- .github/workflows/tool-retrieval-ci.yml
git checkout -- tests/unit/test_skill_description_single_source.py

# ② 新增的 workflow（未跟踪，直接删）
Remove-Item .github/workflows/settings-registry-gap-guard.yml

# ③ 本报告（未跟踪，直接删）
Remove-Item docs/audit_skill_governance/CI1.md
```

- 逐文件 `git checkout -- <file>` 是**定向**恢复，不涉及"整文件 checkout 把 48 卡改动冲掉"的风险（48 卡已在 HEAD 里）。
- 回滚面（`git diff --stat HEAD`）：

```
 .../workflows/skill-description-single-source.yml  | 79 +++++++++++++++++++++-
 .github/workflows/tool-retrieval-ci.yml            | 70 +++++++++++++++++++
 tests/unit/test_skill_description_single_source.py | 51 ++++++++++++--
 3 files changed, 192 insertions(+), 8 deletions(-)
```

- 只回滚"测试文件那部分"（保留 workflow 接入）也可以，但那时门 1 在 CI 上会回到**红**（k1b 就是证据），**不建议**。

---

## 7. 残留物自证

**仓库内（本卡产生的全部改动）**：

```
$ git status --porcelain=v1 -- .github/workflows/skill-description-single-source.yml .github/workflows/tool-retrieval-ci.yml .github/workflows/settings-registry-gap-guard.yml tests/unit/test_skill_description_single_source.py docs/audit_skill_governance/CI1.md
 M .github/workflows/skill-description-single-source.yml
 M .github/workflows/tool-retrieval-ci.yml
 M tests/unit/test_skill_description_single_source.py
?? .github/workflows/settings-registry-gap-guard.yml
（CI1.md 写入后同样显示为 ??）
```

同一时刻工作区里的其它改动（agent/skills_mgmt/loader.py、docs/audit_skill_governance/AUDIT_AND_PLAN.md、tests/unit/test_date_shift_blindspots_guard.py、tests/unit/test_tool_count_consistency.py、GATE1.md、TESTINFRA2.md、test_gate1_single_vector_quality_gate.py）**属于其它卡**，本卡一个字没碰。

**没有写进仓库任何运行期文件**（本卡的干净 checkout 副本都在仓库外；工作区那几次只读运行）：

```
data/skills_mgmt.json                09-26 07:46:05   -      （本卡 00:05 之后未改动）
data/skills_repo/.index/cache.json   09-26 07:46:13   -
data/skills.json                     09-26 07:47:14   -
data/descriptors.json                09-26 15:22:34   -
data/tool_index.json                 09-18 22:47:33   -
（data/learned_workflows.json 的时间戳 00:03:44 = 主审计提交那一刻，不是本卡）
```

**仓库外（全在任务书指定的 C:\Users\Administrator\AppData\Local\Temp\ci1\）**：
- evidence/*.txt（全部原始输出，含 CMD / ENV / EXITCODE / ELAPSED）—— **保留**，本报告每条证据都能在这里找到原文；
- run.ps1 / run2.ps1 / build_sim*.ps1 / driver*.ps1 / probe/*.py —— 保留（可重跑）；
- repo/ 、repo_head/（干净 checkout 副本，各约 200MB 量级）、head.tar —— **已删除**（重建只需重跑 build_sim3.ps1）。

**进程**：全程**没有 taskkill 任何 python 进程**。中途有一个本卡自己的探针进程（pytest -p ci1_dumpmods，插件名写错导致 pytest 在重定向 stdio 下卡住）曾挂起，本卡的处理是**中止了自己的 pwsh 作业而没动那个 python**；复核时它已自行退出（Get-Process -Id 7860 无结果）。其它卡（GATE1 探针、TESTINFRA-2 等）的 python 进程本卡从未触碰。

**本卡使用过一次非写索引的 git 命令**：`git hash-object -w --path …`（用于确认暂存 blob 与工作区文件逐字节相同）。该对象在索引里已存在，**索引与 HEAD 均未改变**（git diff --cached 为空、git status 无新增暂存项）。

---

## 8. 一页结论（给 owner）

1. **本卡打到的最重要一条**：本批 G1-B 新建的「技能描述唯一源」workflow，**按 HEAD 原样接上去第一天就会红**（干净 checkout 上 2 条断言红：合并视图 30 变 28、主轨独有集合消失），因为两条断言都隐含"本机存在 gitignored 的 data/skills_mgmt.json"。已在 tests/unit/** 内做**最小、不放宽**的修复，改后干净 checkout 27 passed / 6 skipped / exit 0。
2. **第二重要**：那个 workflow 的依赖清单**缺 pydantic / prometheus-client**，干净 runner 上会在 import 期直接 ImportError（假红）。已补齐并加了前置导入断言。
3. **第三**：路由冲突集（121 条）**不能接入** —— 用例集 data/eval/route_conflict_cases.v*.jsonl 被 gitignore 排除且**不在 HEAD**。最小改动 = .gitignore 加两行例外 + git add -f 两个文件（本卡受硬约束不能做），判定语义与现成 job 已写在 §2.5：**钉"不许低于 48/111"，不钉高目标**。
4. **本卡接入/修好的门**：① 描述唯一源（修 + 补依赖）② compare 双口径（补依赖；并写明它在 CI 上只是"机制门"）③ manifest 派生一致 ④ 检索质量闸（新 job，含 rank-bm25 硬前置防静默 skip）⑤ 开关零缺口（新 workflow，此前无人跑）。
5. **需要 owner 决策的既有问题**：detect_dynamic_loads.py 在 HEAD 上有 **2 处 HIGH**（agent/tools/persistence.py:380,383，非本批引入）⇒ skills-check.yml 的 dynamic-load-gate 会挡住下次 master 推送。
