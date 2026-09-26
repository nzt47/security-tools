# CI-3 · 干净检出上「必红的 3 个守卫 + 1 个静默跳过」修复报告

> 分支 `audit/skill-governance-v1.0`；开工时 HEAD = `71ed2af5`，收工时 HEAD = **`d1c2b05d`**
> （另一张卡 `TESTHYG2` 在 2026-09-27 01:5x 由他人提交，只动了 9 个 `tests/unit/*.py` 污染源，
> **不含本卡 4 个文件**）。**下文两组计数都在新 HEAD `d1c2b05d` 上重跑过**，与旧 HEAD 逐字一致。
> 上游证据：`docs/audit_skill_governance/CI2.md`（原始日志 `C:\Users\Administrator\ci2_evidence\`）。
> 本卡原始日志：`C:\Users\Administrator\ci3_work\logs\*`（每个标签一个文件，含完整 stdout+rc）。

---

## 0. 结论

| 文件 | 干净检出 · 改前 | 干净检出 · 改后 | 修法 |
|---|---|---|---|
| `tests/unit/test_skill_search_description_source.py` | **2 failed, 15 passed** (rc=1) | **18 passed** (rc=0) | R-d-3 的文件轨取**真仓副本**、主轨换成夹具迷你台账（`real_repo_svc`） |
| `tests/unit/test_skill_h3_migration.py` | **模式A 1 failed, 9 passed, 6 skipped** / **模式B 7 failed, 9 passed, 0 skipped**（两种模式见 §2.5） | **17 passed, 8 skipped**（两种模式**同形**，见 §3.4） | 主轨改为**双来源**（照仓库既有约定，§3.4）：`fixture`（冻结快照写 `tmp_path` + 生产读路径 monkeypatch ⇒ CI 真跑）/ `real`（真实台账内容 ⇒ 按既有约定显式 skip，理由+本机复现方式写在 skip 里）；写死的 `20 条 / 15 条 pd-*` 期望值改为**从真仓文件轨 + H-3 契约实读拼出** |
| `tests/unit/test_s2_gate_is_not_false_green.py` | **1 failed, 3 passed** (rc=1) | **4 passed** (rc=0) | 用例改跑在**夹具根**（tmp 里造"有主轨的仓库"），`_production_recall_sets(root)` 本就 `--root` 参数化 |
| `tests/unit/test_tool_count_consistency.py` | 有 tiktoken：22 passed / **缺 tiktoken：20 passed, 2 skipped, rc=0（假绿）** | 22 passed / **缺 tiktoken：2 failed, 20 passed, rc=1 + 安装指引** | `pytest.importorskip` → 硬前置 `_require_tiktoken()`；并在 `ci.yml` 该 shard 安装行显式 `pip install tiktoken` |

**四文件合跑：干净检出（模式A）= 61 passed + 8 skipped / 干净检出（模式B，§2.5）= 61 passed + 8 skipped / 仓库内 = 69 passed + 0 skipped，全部 rc=0。**
**断言强度：一条都没**删**、没放宽**；改动只有三类 —— (i) 取数来源换成夹具/夹具根、(ii) 两处写死的期望值改成可复算的实读期望、(iii) 按**仓库既有约定**给 8 条"真实台账内容"断言加了**显式 skip**（理由 + 本机复现方式写在 skip 文案里，且每条都有同不变量、同文件内的 `[fixture]` 版本在 CI 上照跑；逐条见 §3.4、§6）。

> **覆盖面（按主审计的补充证据校正）：** 干净检出上的必红**不是 4 条而是 10 条** ——
> `2 failed`（search）+ **`7 failed`**（h3）+ `1 failed`（s2）+ 1 处静默假绿（tiktoken）。
> CI2.md 把 h3 记成 "1 failed / 9 passed / 6 skipped" 是**低估**，原因与两种模式见 §2.5。

---

## 1. 怎么造「干净检出」（可复现命令）

```powershell
# 1) 导出 HEAD（只读 git；不碰工作区）
git -C C:\Users\Administrator\agent archive --format=tar HEAD > C:\Users\Administrator\ci3_work\head.tar
# 2) 必须用 Python tarfile(encoding='utf-8') 解包（Windows bsdtar 会把非 ASCII 名解成乱码）
python -c "import tarfile;tarfile.open(r'C:\Users\Administrator\ci3_work\head.tar',mode='r:',encoding='utf-8').extractall(r'C:\Users\Administrator\ci3_work\run_x')"
# 3) 负检（CI2 结论复现）
#    data/skills_mgmt.json = False    .env = False    data/audit = False    data/skills_repo = 30 项    .git = False
```

⚠️ **本卡"改后"的干净检出 = `git archive HEAD` + 叠加本卡改过的 5 个文件**（`tests/unit/` 4 个 +
`.github/workflows/ci.yml`）—— 那正是"本卡合并进 master 后 CI 看到的那份 checkout"。
夹具脚本 `C:\Users\Administrator\ci3_work\measure.py` 每次从 `head.tar` 重新解出一份 **pristine**
目录再叠加，故"前置条件"不会因上一轮跑过而失真（这点很重要：干净检出里跑一轮测试**会**生成一个
**空的** `data/skills_mgmt.json`，见 §8）。

测试命令（与卡一致）：

```
python -m pytest <文件> -q -p no:randomly --timeout=120 --no-header -p no:cacheprovider
```

环境：Python 3.12.0 / pytest 9.1.1 / tiktoken 0.13.0（本机**装有** tiktoken，见 §4 的屏蔽实验）。

---

## 2. 改前原始输出（干净检出，逐文件）

```
# test_skill_search_description_source.py            rc=1
tests/unit/test_skill_search_description_source.py:186: in test_migrated_skills_are_searchable_by_file_track_text
    assert miss == {}, f"迁移后的技能按文件轨文案搜不到: {miss}"
...:210: in test_search_and_display_report_the_same_text
    assert "code-observability" in [
E   AssertionError: assert 'code-observability' in []
======================== 2 failed, 15 passed in 3.53s =========================

# test_skill_h3_migration.py                          rc=1
tests/unit/test_skill_h3_migration.py:266: in test_runtime_only_set_shrinks_to_the_two
E   AssertionError: runtime_only 标注 = []，期望 ['global-core-principles', 'skill']
SKIPPED [1] ...:114 / :121 / :159 / :173 / :276 / :283: 主轨 skills_mgmt.json 不存在（CI 环境被 gitignore）
=================== 1 failed, 9 passed, 6 skipped in 8.80s ====================

# test_s2_gate_is_not_false_green.py                 rc=1
tests/unit/test_s2_gate_is_not_false_green.py:102: in test_pathB_service_can_recall_main_track_skills
E   AssertionError: 服务形态路径（SkillFileStore + SkillIndexCache）丢了主轨技能
    ['global-core-principles', 'skill'] —— 该路径本应能召回它们；这属于功能回归，不是预期。
========================= 1 failed, 3 passed in 3.47s =========================

# test_tool_count_consistency.py（本机有 tiktoken）rc=0
============================= 22 passed in 6.21s =============================
```

⇒ **CI2 的三条"必红"逐字复现**（2F/15P、1F/9P/6S、1F/3P）。同根因确认：
`data/skills_mgmt.json` 被 `.gitignore:224` 排除 ⇒ 不在 HEAD ⇒ 干净检出上主轨为空。

> 旁证（一个容易误判的形态）：干净检出里**跑一轮**测试后该文件会**被创建成空对象**，
> 于是"文件不存在"变成"文件存在但没有条目"。本卡的夹具不依赖它（见 §3 的非空转自证）。

---

## 2.5 ⚠️ 更正：CI2.md 把 h3 的失败数**低估**了（1 failed → 实测 7 failed）——两种"干净检出"模式

主审计在仓库外的 `git worktree add --detach 71ed2af5` 上实测到
`test_skill_h3_migration.py => 7 failed, 9 passed`（**无 skip**），而 CI2.md 记的是
`1 failed, 9 passed, 6 skipped`。**两个都对，但描述的是两种不同前置状态** ——
差别只在"跑这个文件时 `data/skills_mgmt.json` 在不在（以及是不是空的）"：

| 模式 | 前置 | HEAD 版 h3 的实测输出 | 那 6 条主轨断言的落点 |
|---|---|---|---|
| **A** | 该文件**不存在**（刚导出的 pristine 检出） | `1 failed, 9 passed, 6 skipped` (rc=1) | **skip ⇒ 永不执行** |
| **B** | 该文件**存在但是空对象** `{}` | `7 failed, 9 passed` (rc=1) | **执行并失败**（`KeyError` / 计数不符） |

**机制**：旧 `main_track` fixture 只判 `MGMT.exists()`。文件被**创建成空对象**后
`exists()` 为真 ⇒ 不再 skip ⇒ 返回 `{}` ⇒ 4 处 `KeyError` +
"双轨应为 20 条，实得 0" + "主轨独有集合 = []"，再加 `:266` 那条硬断言的
runtime_only = **7 failed**。而"空对象从哪来"：**同一份检出里更早跑过的测试/文件会把它建出来**
（§8 记录的观察；CI-1 在 `test_skill_description_single_source.py:96-105` 也独立记过同一现象）。
`ci.yml` 的 6-shard 是 `-n 2 --dist=loadscope`，**哪些文件同批、谁先跑由分片脚本与并行调度决定**
⇒ 同一个文件在 CI 上落 A 还是落 B **不稳定**。

⇒ **结论：CI2 的 4 条（2+1+1）是下限，主审计的 10 条（2+7+1）是上限，同根因**；
修复必须让**两个模式都绿**。

**顺带把"两种模式"本身也修掉了**：修复后的 skip 判据是「**有内容**才算有台账」
（`_real_main_track_or_skip`：不存在 → skip；存在但是空对象 `{}` → 同样 skip），
不再只判 `exists()` ⇒ 干净检出上**两个模式的输出完全相同**（实测 A/B 都是
`17 passed, 8 skipped`），CI 上不再随文件顺序漂移。

**本卡实测复现（HEAD 版文件，两种模式）**：

```
# 模式 A：pristine（无 data/skills_mgmt.json）        rc=1
=================== 1 failed, 9 passed, 6 skipped in 8.80s ====================

# 模式 B：预置空台账（pre_empty_ledger，模拟"同一 shard 更早的文件已创建它"）  rc=1
======================== 7 failed, 9 passed in 9.02s =========================
tests\unit\test_skill_h3_migration.py:118: in test_description_zh_is_verbatim_main_track_text
E   KeyError: 'code-observability'
tests\unit\test_skill_h3_migration.py:125: in test_body_is_verbatim_main_track_content
E   KeyError: 'code-observability'
tests\unit\test_skill_h3_migration.py:167: in test_dual_track_invariant_main_desc_equals_file_zh
E   AssertionError: 双轨技能应为 20 条（15 pd-* + G1-C 的 5 条），实得 0
tests\unit\test_skill_h3_migration.py:176: in test_main_track_only_set_is_exactly_two
E   AssertionError: 主轨独有集合 = []，期望 ['global-core-principles', 'skill']
tests\unit\test_skill_h3_migration.py:266: in test_runtime_only_set_shrinks_to_the_two
E   AssertionError: runtime_only 标注 = []，期望 ['global-core-principles', 'skill']
tests\unit\test_skill_h3_migration.py:278: in test_is_no_longer_instruction_content
E   KeyError: 'skill'
tests\unit\test_skill_h3_migration.py:284: in test_describes_what_it_is_and_when_to_use
E   KeyError: 'skill'
```

**与主审计的 7 条逐条对齐**（:118 / :125 / :167 / :176 / :266 / :278 / :284，报文一致）。

**修复后两个模式都绿**（本卡实测）：

```
模式 A：ci3c_all4_cleanA.txt   -> 61 passed, 8 skipped（4 文件合跑；8 skip = §3.4 的 [real] 约定）
模式 B：ci3c_all4_cleanB.txt   -> 61 passed, 8 skipped（4 文件合跑，预置空台账）—— 与 A 同形
单文件：ci3c_h3_cleanA_rs.txt / ci3c_h3_cleanB_rs.txt -> 17 passed, 8 skipped（两模式同形）
仓库内：ci3c_all4_repo.txt     -> 69 passed, 0 skipped（有台账 ⇒ [real] 那 8 条也真跑）
```

**给报告 §25 引用的一句话**：
> CI2.md §"干净检出必红"把 `test_skill_h3_migration.py` 记为 `1 failed / 9 passed / 6 skipped`，
> 属**低估**：同一份干净检出上若 `data/skills_mgmt.json` 已被更早的测试创建成空对象，
> 实测是 **`7 failed / 9 passed / 0 skipped`**（CI3 两种模式都复现过，报文逐条一致）；
> CI 上的真实条数在 4～10 之间漂移，取决于同一 pytest 进程里的文件顺序。

---

## 3. 三个必红的修法（**断言未删未放宽**；只换取数来源，两处写死期望值改为可复算的实读期望）

共同原则（照 `test_s1_02_s3_01_fixpoint_guard.py::_make_inputs` 的先例）：**测试真正需要的主轨内容
构造成夹具**，写进 `tmp_path`，并把**运行期落点**用 monkeypatch/env 指到 tmp；文件轨（真仓
`data/skills_repo`，git 里有的产物）要么原样用、要么复制一份到 tmp，总之**断言的两端仍是"真仓文件"
与"夹具"**，不是拿文件轨自证。

### 3.1 `test_skill_search_description_source.py`

- **根因**：`TestRealRepoSearchMatchesDisplay` 用 `SkillsMgmtService()`（**生产默认主轨**）⇒
  `store.list_all()` 为空 ⇒ 搜索**无候选**（`[Searcher] query='observable' → 0/0 命中`）；
  第二条的空转更隐蔽——`twisted` 索引那条断言在空候选上"恒真"。
- **修法**：新增 `real_repo_svc` 夹具——`shutil.copytree(真仓 data/skills_repo)` 到 tmp，
  再经**生产入口** `svc.creator.create_manual` + `svc.store.upsert` 写入 5 条迁移技能的
  **主轨历史副本**（文案 = 文件轨 `description_zh`，正是迁移前的真实形态）。
  展示侧同步改为 `SkillRegistry(service=svc)`、取数侧改为 `svc.file_store`（同一份文件轨，
  且不再依赖仓库里那份被 gitignore 的台账）。
- **非空转自证（新增用例）**：`test_主轨确实读的是夹具台账` 断言
  `{s.id for s in store.list_all()} == set(MIGRATED)`。真仓台账有 **22** 条（15 `pd-*` + 5 迁移 +
  2 主轨独有），夹具只有这 **5** 条 ⇒ 若被测代码读的是仓库那份（或压根没读到），本条立刻红。
- **改前/改后**：`2 failed, 15 passed` → `18 passed`（15 + 修好 2 条 + 新增 1 条自证）。

### 3.2 `test_skill_h3_migration.py`

- **根因**：本文件守的「逐字搬运 / 双轨不变量 / 主轨独有集合 / `skill` 描述质量」**都要主轨内容**；
  旧实现用 `pytest.skip("主轨不存在")` 兜底 6 处，**唯独** `test_runtime_only_set_shrinks_to_the_two`
  吃硬断言 ⇒ 同一文件里"6 处 skip + 1 处硬断言"，在 CI 上就是
  **模式 A："6 条永不执行 + 1 条必红"（1 failed / 6 skipped）/ 模式 B："7 条必红"（§2.5）**。
- **修法（按仓库既有约定分两类，见 §3.4）**：
  1. 主轨内容**冻结**成 `MAIN_TRACK_FIXTURE`（22 条，逐字；出处/sha256 见 §7）；
  2. 主轨来源做成**参数化夹具** `main_track_source`（`params=["fixture","real"]`）：
     · `fixture` —— 把同一份 JSON 写进 `tmp_path/skills_mgmt.json`，并
       `monkeypatch.setattr(callability, "SKILLS_MGMT_PATH", …)` 把**生产读路径**指过去
       ⇒ **CI 冷启动真跑**；
     · `real` —— 读仓库里那份运行期台账；**没有内容**（不存在，或被更早的用例创建成空对象）
       ⇒ `_real_main_track_or_skip()` 按既有约定 `pytest.skip` 并打印理由 + 本机复现方式；
  3. `main_track` / `main_track_file` 两个夹具都从 `main_track_source` 取数 ⇒
     **7 条主轨用例的断言体一字未改**（只有 `test_runtime_only_set_shrinks_to_the_two`
     多了一个夹具入参），同一批用例在两个来源上各跑一遍。
- **非空转自证（用例，也跑两个来源）**：`test_主轨读路径读到的就是本参数这一份`：
  `callability.SKILLS_MGMT_PATH` 必须指到**本参数那一份**，且 `_skill_sources(...)` 里
  该份的 id 全部出现在 `in_mgmt` 里。
- **写死的期望值改为可复算的实读期望（主审计要求 3）**：原 `:167` 是
  `assert len(dual) == 20, "双轨技能应为 20 条（15 pd-* + G1-C 的 5 条）"` —— 主轨换成夹具后，
  "20"与**真仓**之间的锚点断了（夹具少写一条也不会红）。现改为两端**各自实读**再比对：

  ```python
  pd_from_repo = frozenset(sid for sid in meta_index if sid.startswith("pd-"))   # 真仓文件轨（git 里的产物）
  expected_dual = set(pd_from_repo) | set(MIGRATED)                              # ∪ H-3 裁定纳入的 5 条
  assert set(meta_index) & set(main_track) == expected_dual, (…多出/缺少…)
  assert len(dual) == 20 == len(expected_dual), (…)                              # 契约数字仍留在断言里
  ```

  **依据**：迁移前的 15 条双轨技能在真仓里就是 `data/skills_repo/pd-*/skill.md`（`git ls-files`
  可复算），H-3 纳入的 5 条是文件顶部的 `MIGRATED` 契约 ⇒ 15 + 5 = 20 是**推论**而不是拍脑袋。
  **断言没删、没放宽，反而更紧**（夹具多一条/少一条、或有人动真仓文件轨都会红）——
  见 §5 反证 3。
- **改前/改后**：模式 A `1 failed, 9 passed, 6 skipped` / 模式 B **`7 failed, 9 passed`** →
  **两种模式都是 `17 passed, 8 skipped`**（25 collected：9 条与主轨无关的照旧 + 8 条 `[fixture]` 真跑绿
  + 8 条 `[real]` 按约定 skip）；仓库内（有台账）= **`25 passed`**、0 skipped。

### 3.3 `test_s2_gate_is_not_false_green.py`

- **根因**：本门判的是**两条生产路径的差**（pathA 缺 2 / pathB 缺 0）。干净检出上**没有主轨就没有差**：
  pathB 必红；pathA 的两条断言虽然"绿"，但**绿的理由是主轨不存在**（空转——将来真把主轨接进
  裸入口它们也不会红）。
- **修法**：新增 `fixture_root` 夹具，在 tmp 下造**布局等价**的仓库根
  （`data/skills_repo` = 真仓副本；`data/skills_mgmt.json` = **只含这 2 条主轨独有技能**的夹具台账），
  三个用例改喂 `_production_recall_sets(fixture_root)`（`verify_index_drift.py` 自身就用 `--root`
  参数化这条路）。**判据与断言强度一条未改**。
- **非空转自证（写进用例内）**：
  · pathA 用例前加 `set(MAIN_TRACK_ONLY) <= set(mod.main_track_ids(fixture_root))`；
  · pathB 用例追加 `set(svc) - set(bare) == set(MAIN_TRACK_ONLY)` —— pathB 比 pathA 多出来的
    **恰好**是夹具主轨那 2 条 ⇒ 证明读的是**注入的那份台账**。
- **改前/改后**：`1 failed, 3 passed` → `4 passed`（用例数不变；空转的两条变成真断言）。

### 3.4 按**仓库既有约定**分成两类口径（机制 → 夹具；真实台账内容 → 显式 skip）

主审计指出的既有约定（本卡逐行核对过）：

```text
tests/unit/test_skill_description_single_source.py:453-454   pytest.skip("descriptor 台账不存在（本机未跑过 M8）")
tests/unit/test_skill_description_single_source.py:493-494   pytest.skip("legacy 快照不存在（CI 环境 data/skills.json 被 gitignore）")
tests/unit/test_skill_description_single_source.py:531-532   pytest.skip("两份 legacy 快照之一不存在（CI 环境被 gitignore）")
tests/unit/test_s3_01_handover.py:245-246                    pytest.skip("运行时台账不存在（CI 冷启动）—— 契约由上一用例覆盖")   ← 连"下游用例已覆盖"的写法都是既有的
tests/unit/test_s3_01_handover.py:572-573 / 642-643 / 651-652 pytest.skip("运行时台账不存在（CI 冷启动）")
```

按这个约定，`test_skill_h3_migration.py` 改成**同一批用例跑两个来源**（`main_track_source`
夹具参数 `fixture` / `real`），而不是二选一：

| 来源 | 断言的**性质** | CI 冷启动的落点 |
|---|---|---|
| `fixture` | **机制**：判据本身成不成立、集合**恰好**等于、注入是否被真读到 | **真跑**（冻结快照写 `tmp_path` + 生产读路径 monkeypatch） |
| `real` | **真实台账的具体内容**：这 5 条的中文/正文逐字等于**那份台账**的原文；双轨恰好 20 条 = 15 个 pd-* + 5；`skill` 的描述质量；主轨独有恰好 2 条 | **显式 skip**（`_real_main_track_or_skip`，文案含 gitignore 说明 + 本机复现方式） |

**为什么 search / s2 两个文件不需要 skip**：按约定，它们断言的都是**机制** ——
search 断言"管理页搜索能不能按**文件轨文案**召回"（文件轨是 git 里的产物，CI 一定有），
s2 断言"**SkillFileStore + SkillIndexCache 服务形态**能不能召回主轨技能"（用夹具根喂
`_production_recall_sets`）。两条路径的被测对象都能用夹具喂饱 ⇒ **全跑，不 skip**（§3.1 / §3.3）。

#### CI 冷启动上**不再执行**的 8 条断言（逐条登记）+ 它们的夹具版

`-v` 实测（干净检出，模式 A/B 同）：`17 passed, 8 skipped`，25 条 collected：

| # | `[real]` 断言（CI 冷启动 skip） | 不变量 | 同文件内的 `[fixture]` 版本（CI 上照跑） |
|---|---|---|---|
| 1 | `test_description_zh_is_verbatim_main_track_text[real]` | 迁移的中文逐字等于真实台账 | `…[fixture]` PASSED（同一断言，取夹具台账） |
| 2 | `test_body_is_verbatim_main_track_content[real]` | 正文逐字等于真实台账 | `…[fixture]` PASSED |
| 3 | `test_dual_track_invariant_main_desc_equals_file_zh[real]` | 双轨恰好 20 = 15 pd-* + 5 | `…[fixture]` PASSED |
| 4 | `test_main_track_only_set_is_exactly_two[real]` | 主轨独有恰好 2 条 | `…[fixture]` PASSED |
| 5 | `test_runtime_only_set_shrinks_to_the_two[real]` | runtime_only 标注恰好 2 条 | `…[fixture]` PASSED |
| 6 | `test_is_no_longer_instruction_content[real]` | `skill` 描述不再是指令内容 | `…[fixture]` PASSED |
| 7 | `test_describes_what_it_is_and_when_to_use[real]` | `skill` 描述质量 | `…[fixture]` PASSED |
| 8 | `test_主轨读路径读到的就是本参数这一份[real]` | 生产读路径确实读到台账 | `…[fixture]` PASSED |

（1–7 就是 CI2 §5.2 表 A 里那 6 条 at :114/:121/:159/:173/:276/:283 + runtime_only 那条；
第 8 条是本卡新增的自证用例。）

**⇒ 每一条不变量在 CI 上都**没有**失守**：`[fixture]` 版本断言同一件事（同一段代码、
同一批比较），只是取数换成夹具。实测 25 条里 **8 条 `[fixture]` 全部 PASSED、8 条 `[real]` 全部 SKIPPED、
9 条与主轨无关的照旧 PASSED**。

**skip 的理由（原文，含既有约定要求的"本机怎么复现"）**：

```text
SKIPPED [1] tests\unit\test_skill_h3_migration.py:182: 技能主轨 data/skills_mgmt.json 没有内容
（CI 冷启动：该文件被 .gitignore:224 排除、不在 HEAD；干净检出上要么不存在，要么被更早的
用例创建成一个空对象）。本条断言的是**这份真实台账的具体内容**，故按仓库既有约定显式 skip；
同一条不变量已由本文件的 fixture 来源用例覆盖（同一批用例的另一个参数）。本机复现：在**有台账**
的工作区直接跑本文件即可（该台账是运行期状态、由技能管理的写路径产生，本仓不提供重建脚本；
本卡核对过的内容 sha256 = bcda9ecf…，见 docs/audit_skill_governance/CI3.md §7）。
```

---

## 4. tiktoken 的「静默跳过」怎么消掉的

**事实基础**：`tiktoken` 在本仓是**必装依赖**，不是可选件 ——
`pyproject.toml` `[project].dependencies` 第 35 行 `"tiktoken>=0.7.0,<1.0.0"`，
`requirements.txt:361` 钉 `tiktoken==0.13.0`；`ci.yml` 各 job 的 `pip install -e .` 会带入它。
⇒ 它缺席意味着**环境装坏了**，而 `pytest.importorskip` 是给"缺了就无意义"的可选依赖用的，
用在这里等于给"token 计量必须实测"这条护栏装了一个"缺依赖就消失"的开关。

**改法（两处，互相兜底）**：

1. `tests/unit/test_tool_count_consistency.py`：两条断言里的 `pytest.importorskip("tiktoken")`
   → 新的模块级硬前置 `_require_tiktoken()`，缺依赖时 `pytest.fail(..., pytrace=False)`，
   消息里写清"为什么不能 skip"与**安装指引**（`python -m pip install tiktoken`）。
2. `.github/workflows/ci.yml`（**已存在的 workflow**，只动该 job 的 pip 安装行）：6-shard 单元测试
   job 的安装步骤加一行 `pip install tiktoken`（附 6 行注释说明理由）。**理由**：本 shard 会收集
   本文件；tiktoken 虽在 `pyproject` 里、会被下一行 `pip install -e .` 带入，但它的缺席会让
   **本文件从"2 条实测断言"退化为"2 条 skip"**——既然已把断言侧改成响亮失败，安装侧就必须把
   依赖**显式钉住**，不能依赖依赖解析结果（部分 job 的 `pip install -e .` 还挂着 `|| true`）。

**改前/改后原始输出**（用探针 `C:\Users\Administrator\ci3_work\ci3_probe.py` 屏蔽 tiktoken，
抛 `ModuleNotFoundError` —— 复现"runner 上压根没装"这一形态；普通 `ImportError` 会被
pytest≥8.2 当作"模块找到了但导入失败"而报错，不是"缺依赖"）：

```
# 改前（HEAD 版文件 + 屏蔽 tiktoken）        rc=0   ← 假绿
tests\unit\test_tool_count_consistency.py ....................ss         [100%]
SKIPPED [1] tests\unit\test_tool_count_consistency.py:437: could not import 'tiktoken': No module named 'tiktoken'
SKIPPED [1] tests\unit\test_tool_count_consistency.py:445: could not import 'tiktoken': No module named 'tiktoken'
======================== 20 passed, 2 skipped in 2.33s ========================

# 改后（本卡版文件 + 屏蔽 tiktoken）         rc=1   ← 响亮失败
tests\unit\test_tool_count_consistency.py ....................FF         [100%]
缺少 tiktoken：本文件 ⑤ 组的两条守卫（token 计量 == tiktoken cl100k_base 实测）无法执行。
tiktoken 是 pyproject.toml 声明的**必装依赖**，缺它属于环境缺陷，按「响亮失败」处理 ——
静默 skip 会让「字符÷3」回归重新变成假绿。

  安装：python -m pip install tiktoken
  （CI：pip install -e . 会带入；本地最少环境请显式安装）

======================== 2 failed, 20 passed in 2.58s ========================
```

**未改 workflow 的其它 job**：`coverage-ci.yml` / `observability-ci.yml` / `full-regression.yml` /
`test.yml` 等凡是跑 `tests/unit` 或 `tests/` 全量的 job，都执行 `pip install -e .`，
会从 `pyproject` 带入 tiktoken；**若哪一轮真的没装成，本文件现在会红而不是变绿**——这正是本卡要的
方向（响亮 > 静默），故没有再改第二个 workflow。

---

## 5. 反证：夹具是「有牙齿」的（不是"加了夹具问题自己好了"）

把夹具清空（只在**一次性干净目录**里 patch，不动仓库文件）后必须变红 —— 实测：

```
# 反证 1：把夹具 dict 清空（MAIN_TRACK_FIXTURE = {}）                rc=1
tests\unit\test_skill_h3_migration.py .s..FsFs..FsFs......sFsFs
FAILED ...::test_description_zh_is_verbatim_main_track_text[fixture]
E   KeyError: 'code-observability'
FAILED ...::test_body_is_verbatim_main_track_content[fixture]
FAILED ...::test_dual_track_invariant_main_desc_equals_file_zh[fixture]
E   AssertionError: 双轨集合与「真仓文件轨 15 条 pd-* ∪ H-3 纳入的 5 条」不一致：…缺少 [20 条 id]
FAILED ...::test_main_track_only_set_is_exactly_two[fixture]
FAILED ...::test_is_no_longer_instruction_content[fixture]
FAILED ...::test_describes_what_it_is_and_when_to_use[fixture]
=================== 6 failed, 11 passed, 8 skipped in 8.08s =====================
```

**这 6 条 `[fixture]` 断言正是 CI2 记的"永不执行"的那 6 条不变量** ⇒ 它们现在真的在 CI 上跑，
且真的会因主轨内容变化而红（`[real]` 那 8 条仍然按约定 skip，与预期一致）。

```
# 反证 1b：只把**注入的那份文件**清空（dict 不动）⇒ 只有"读文件"的两条红   rc=1
[post_patch] path.write_text(_MAIN_TRACK_FIXTURE_JSON, …) → path.write_text("{}", …)
FAILED ...::test_主轨读路径读到的就是本参数这一份[fixture]
E   AssertionError: 主轨来源(fixture)里的 id 没被生产读路径读到，缺 [22 条 id] ⇒ 主轨断言会是空转的
E   assert {…} <= set()
FAILED ...::TestActuallyRecallable::test_runtime_only_set_shrinks_to_the_two[fixture]
E   AssertionError: runtime_only 标注 = []，期望 ['global-core-principles', 'skill']
=================== 2 failed, 15 passed, 8 skipped in 8.08s ====================
```

**反证 1b 是本卡最关键的一条证据**：dict 没变、只把**注入到 `tmp_path` 的那个文件**清空，
就恰好打红"读文件"的两条（`runtime_only` 与自证用例）—— 说明
`agent.lines.callability.SKILLS_MGMT_PATH` 这条**被测生产路径**读的是**我注入的那个文件**，
既不是内存里的夹具 dict，也不是仓库里那份台账。

```
# 反证 2：test_s2_gate_is_not_false_green.py 里夹具主轨清空            rc=1
tests\unit\test_s2_gate_is_not_false_green.py FF.F
FAILED ...::test_pathA_bare_loader_cannot_recall_main_track_skills
E   AssertionError: 夹具根里没有主轨独有技能 ⇒ 本条会退回「主轨不存在所以通过」的空转形态
FAILED ...::test_pathB_service_can_recall_main_track_skills
FAILED ...::test_drift_script_s2_fails_on_pathA_gap_now
（::test_s2_judges_both_paths_separately_not_disk_union 仍绿 —— 它读的是脚本源码，与夹具无关）
```

```
# 反证 3：夹具里**少写一条 pd-*** ⇒ 新的"实读期望值"必须抓住    rc=1
[post_patch] MAIN_TRACK_FIXTURE = {k: v for k, v in json.loads(...).items() if k != "pd-writing-skills-5da20e67-skill"}
tests\unit\test_skill_h3_migration.py:239: in test_dual_track_invariant_main_desc_equals_file_zh
E   AssertionError: 双轨集合与「真仓文件轨 15 条 pd-* ∪ H-3 纳入的 5 条」不一致：
    夹具/文件轨多出 []、缺少 ['pd-writing-skills-5da20e67-skill']
======================== 1 failed, 16 passed in 8.64s ========================
```

⇒ 这三条反证合起来说明：h3 的 6 条主轨断言、s2 的 pathA/pathB 判据、以及 h3 的"20 条"期望值
**都真的依赖夹具**，不是"加了夹具问题自己好了"。

---

## 6. 「CI 上永不执行的断言」重统计（只统计**本卡这 3 个文件**）

CI2 §5.2 表 A / §5.3 表 B 里属于本卡的条目，与修复后的状态：

| # | 位置 | 改前 | 改后 | 处置 |
|---|---|---|---|---|
| 1–6 | `test_skill_h3_migration.py` :114 / :121 / :159 / :173 / :276 / :283 | **模式 A 永不执行（skip）/ 模式 B 执行但必红（`KeyError`）**（§2.5） | **`[fixture]` 版本执行且绿**（CI）；`[real]` 版本按仓库既有约定**显式 skip**（8 条，逐条见 §3.4） | ⚠️ 部分：不变量在 CI 上由夹具版覆盖，真实台账版按约定不执行 |
| 7–8 | `test_tool_count_consistency.py` :437 / :445 | 缺 tiktoken ⇒ **静默跳过、exit 0** | **执行**；缺依赖则红并给安装指引 | ✅ 消掉（§4） |
| 9 | `test_s2_gate_is_not_false_green.py` pathA（两条断言） | **执行但空转**（绿的理由=主轨不存在） | 执行且非空转（夹具根 + 主轨存在前置） | ✅ 消掉（CI2 未列此类） |
| 10 | `test_skill_search_description_source.py` twisted 索引那一条（:213 `not in [...]`） | **执行不到**（同一用例在 :210 就先红了 ⇒ 后面的断言根本没跑；即便跑也是空候选上的恒真） | 执行且非空转（主轨夹具 ⇒ 候选非空，"换索引 ⇒ 命中集必须变"真的成立） | ✅ 消掉（CI2 未列此类） |

**本卡 4 个文件在 CI 冷启动上的最终形态：61 passed + 8 skipped（模式 A/B 同形）；仓库内（有台账）69 passed + 0 skipped。**

| 类别 | 改前 | 改后 |
|---|---|---|
| 必红 | 模式 A 4 条 / 模式 B **10 条**（§2.5） | **0** |
| 按约定**不执行**（有理由 + 夹具版覆盖，§3.4） | 6 条（h3，其中模式 B 会退化成红） | **8 条**（h3 的 `[real]`：原 6 条 + runtime_only + 自证用例；**每条都写明理由与复现方式，且同不变量在 CI 上由 `[fixture]` 版真跑**） |
| 静默假绿（缺依赖 ⇒ 绿且无提示） | 2 条（tiktoken） | **0**（缺依赖 ⇒ 红 + 安装指引） |
| 执行但空转 | 3 条（s2 pathA ×2、search twisted ×1） | **0** |

**这 8 条 skip 是**照仓库既有约定**做的取舍（主审计明确要求），不是"把红变绿"**：
每条的 `[fixture]` 孪生断言在 CI 上真跑、且实测"清空夹具即红"（§5 反证 1 / 1b）。
若评审认为"真实台账内容"这一半也必须在 CI 上判，唯一办法是**把该台账纳入版本控制**
（或把它的内容作为夹具入库，等价于本卡已做的冻结快照）—— 那是数据治理决定，不在本卡权限内。

**我没能消掉 / 不在我文件里的（如实登记）**：

| 位置 | 状态 | 为什么没动 |
|---|---|---|
| `test_skill_description_single_source.py` 5 条 skip（:454 / :494 / :523 / :525 / :587） | 仍**永不执行** | **不是本卡的文件**（CI-1 在途），本卡不得改 |
| `test_s10_03_retrieval_quality_gate.py` 的 `rank_bm25` / `numpy` `importorskip`（缺依赖 2 skipped / exit 0） | 仍**静默假绿**（其它 job） | 同上（另一张卡的文件）；CI2 §5.3 已登记 |
| `test_date_shift_blindspots_guard.py:1074` | 设计性 skip（需 `--runslow`） | 与本卡无关 |
| `test_s2_gate_is_not_false_green.py` 里 `svc is None → pytest.skip` | **保留**（1 处） | 这是**失败路径**上的既有兜底（临时副本创建失败时），不是"因为缺运行期数据所以跳过"，删掉它没有依据；它在 CI 上不会触发（实测 0 skipped） |

---

## 7. 主轨夹具的出处与复算

- 出处：HEAD 工作区的 `data/skills_mgmt.json`（22 条），
  **sha256 = `bcda9ecfbcf105b6965055418aa2ac1d5a2cbfa791ea2428520b19517af07a77`**。
- 冻结后的 JSON（`_MAIN_TRACK_FIXTURE_JSON`）**sha256 = `5f3354ac9dd7ea5f45d7ac6b5ba154e45aa525e633b753dd5cbd48515dd3da2b`**（27541 字符 / 546 行）。
- **逐字保留**：`id / name / category / source / status / author / description / content / content_type /
  tags / enabled / is_sensitive`；**两处归一**：`config_schema` / `output_schema` 收敛为
  `_make_inputs` 同款最小形状；15 条 `pd-*` 的 `content` 留空（本文件不依赖）。
- 复算：

```python
import json, hashlib
R = r"C:\Users\Administrator\agent"
mg = json.load(open(R + r"\data\skills_mgmt.json", encoding="utf-8"))
KEEP_CONTENT = {"code-observability","engineering-test-delivery","frontend-state-sync",
                "self-explanatory-ui","testing-anti-patterns","global-core-principles","skill"}
out = {sid: {"id": sid, "name": str(r.get("name") or sid), "category": str(r.get("category") or "custom"),
             "source": str(r.get("source") or "manual"), "status": str(r.get("status") or "published"),
             "author": str(r.get("author") or "workbench"), "description": str(r.get("description") or ""),
             "content": str(r.get("content") or "") if sid in KEEP_CONTENT else "",
             "content_type": str(r.get("content_type") or "markdown"), "tags": list(r.get("tags") or []),
             "enabled": bool(r.get("enabled", True)), "is_sensitive": bool(r.get("is_sensitive", False)),
             "config_schema": {"type": "object", "properties": {}}, "output_schema": {}}
       for sid, r in mg.items()}
txt = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
print(hashlib.sha256(txt.encode("utf-8")).hexdigest())   # 5f3354ac...
```

**已知边界（如实登记）**：夹具是**冻结快照**——它让"迁移逐字落地"这条断言在**没有主轨数据**的 CI 上
也能真判，代价是**它不会跟随运行期台账漂移**（dev 机上有人改了 `data/skills_mgmt.json`，本文件
不会发现）。要在 CI 上"读实时台账"物理上不可能（该文件不在 HEAD）；需要这层校验时，应由台账侧的
守卫（如 descriptors/backfill 那条线）在**有台账的环境**里做。

---

## 8. 变更清单与仓库卫生

**改了的文件（5 个，均在允许范围内）**：

| 文件 | 改动规模 |
|---|---|
| `tests/unit/test_skill_h3_migration.py` | 主轨改**双来源参数化夹具**（`fixture`/`real`）+ 自证用例 + 实读期望值；末尾 +546 行夹具数据（985 行） |
| `tests/unit/test_skill_search_description_source.py` | +`real_repo_svc` 夹具、+`test_主轨确实读的是夹具台账`、R-d-3 改隔离（294 行） |
| `tests/unit/test_s2_gate_is_not_false_green.py` | +`fixture_root` 夹具与非空转前置（原 `_production_recall_sets(ROOT)` → `(fixture_root)`） |
| `tests/unit/test_tool_count_consistency.py` | `importorskip` → `_require_tiktoken()`（仅"静默跳过"这一处） |
| `.github/workflows/ci.yml` | 6-shard job 安装步骤 +1 行 `pip install tiktoken` +6 行理由注释 |

**没动的**：`tests/unit/test_search_tools.py` 等 9 个文件（另一张卡在途）、`tests/conftest.py`、
`tests/unit/conftest.py`、`agent/` 下任何生产代码、`scripts/verify_index_drift.py`（本卡只**读**它）。

**仓库卫生（硬约束 2）**：全部夹具落点都在 `tmp_path`（或 `C:\Users\Administrator\ci3_work`），
**没有写任何生产台账**。实测证据：在仓库内跑这 4 个文件前后，
`data/skills_mgmt.json` 的 sha256 **都是** `bcda9ecf…`（字节未变）；
`data/audit/**`、`data/agent_lines/_active.json`、`data/descriptors.json` 未被触碰。
**git 状态只读**：本卡未执行任何 `commit/add/checkout/switch/stash/reset/clean/merge/rebase/push/worktree`；
唯一的 git 动作是 `git archive HEAD` 与 `git show HEAD:<file>`（只读）。

**一个观察（已被 §2.5 证明是"模式 A/B 之分的成因"，本卡未改它）**：干净检出里跑一轮测试后会出现
一个**空的** `data/skills_mgmt.json`（实测 `import agent.skills_mgmt` / `agent.capregistry` /
`agent.lines.callability` **单独 import 都不会**创建它，是测试期某条默认服务构造创建的；
具体调用点未定位）。**它正是 h3 在 CI 上"4 条红"还是"10 条红"的开关**（§2.5）。
本卡 4 个文件的**判定**都不再依赖它：h3 的 `[fixture]` 走**注入**的主轨（tmp）、
`[real]` 判「**有内容**才算有」⇒ 空对象与不存在同类（都走显式 skip）、search 走**注入**的主轨（tmp）、
s2 走**夹具根**（tmp）—— 故"主轨不存在 / 存在但为空"两种形态下**输出完全相同**。

**最终计数器（可复现）**：

| 场景 | 命令 | 结果 |
|---|---|---|
| 干净检出 · 模式 A | `measure.py {"tests":[4 文件],"mode":"clean"}` | **61 passed, 8 skipped**, rc=0 |
| 干净检出 · 模式 B | 同上 + `"pre_empty_ledger":true` | **61 passed, 8 skipped**, rc=0（与 A **同形**） |
| 仓库内（有台账） | 同上 + `"mode":"repo"` | **69 passed**, rc=0, 0 skipped |
| 干净检出 · 逐文件 | 4 次单文件运行 | **18 / 17(+8 skipped) / 4 / 22 passed** |
| 缺依赖（tiktoken） | 同上 + 探针屏蔽 tiktoken | **2 failed, 20 passed**, rc=1（+安装指引） |
