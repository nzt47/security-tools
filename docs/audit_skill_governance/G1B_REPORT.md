# G1-B · 技能描述治理实施报告（唯一事实源收敛）

> 任务卡：**G1-B**（G1 描述三段式改造的**实施**卡）
> 基线：`HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（执行期间未变，实测 `git rev-parse HEAD` 三次一致）
> 环境：Python 3.12.0（系统解释器，**未用 venv**）；Windows；未启动任何常驻服务；
> 全程**无 `git add` / `git commit`**；`git worktree list` 只有主工作区。
> 权威规格：`docs/audit_skill_governance/G1B0_recheck.md` §4（第 743–846 行）
> 裁定依据：`docs/audit_skill_governance/G1_DECISIONS.md`（H-1…H-5）+ `G1A_reconciliation.md`

---

## 0. 第一行证据（S0 前置确认）

```text
PS> python -c "from agent.skills_mgmt.file_store import _META_FIELDS as m;print(len(m), 'description_zh' in m)"
19 True
```

**S0 已由主审计完成、本卡只确认不改**：`_META_FIELDS` 现为 **19 键**且含 `description_zh`
（`agent/skills_mgmt/file_store.py:75-91`）。这是 M2 能落地的**唯一前置**——不在白名单里，
`patch_front_matter` 会静默丢弃该键。

> 注：任务卡说「实测 19 个键」，与本次输出**逐字一致**。

---

## 1. 逐步结论表

| 步 | 判定 | 关键证据（一行） |
|---|---|---|
| **S0** | ✅ 已确认（未改动） | `19 True` |
| **M0** | ✅ 3 条写路径全部冻结并实测 | `POST /api/skills/describe` → **409 `frozen:true`**；`/describe/auto` → **200 `applied:[] count:0`**；`svc.update(id,{"description":…})` 后描述**仍为原值**；`curate_skills(auto_clean=True)` 后描述**仍为空**；overlay 字节不变 |
| **M1** | ✅ 基线落盘 | `23 15`（23 条记录 / 15 条 s1≠s2），文件 21,266 B |
| **M2** | ✅ 15 个 pd-* 写入 `description_zh` | `git diff --numstat` = 15 文件、每个 `+2 −1`；严格判定 15/15 `fm_wo_zh==HEAD` ∧ `body==` ∧ `desc_unchanged` ∧ `zh==baseline`；**二次写入逐字节幂等** |
| **M4** | ✅ UI 优先读 `description_zh` | `yunshu-ui/.../skills.tsx` ×4 处；`npx tsc --noEmit` 退出码 **0**；生产 `_skills_mgr.get_all()` 上 `zh‖en == 改造前主轨中文` **15/15**，而「只读 description」会是 **0/15** |
| **M3** | ✅ 合并规则文件轨优先 | `15/15` `description==skill.md`、`15/15` `description_zh==基线`；`CP_SKILL_DESC_FROM_FILE_TRACK=0` ⇒ **15/15 回落主轨**且 zh 仍在 |
| **M4b** | ⚠️ **显式延期**（已在代码里写明理由与解除条件） | `agent/skills_mgmt/searcher.py:41` docstring；三维理由见 §5 偏差 D-8 |
| **M5** | ✅ 3+1 站点补齐并重跑 sync | `build_manifest()` skill 条目描述 **23/23 非空**且 **23/23 == skill.md**；`build_registry().list_envelope()` skill 描述 **23/23 非空**（改造前 **0/23**） |
| **M6** | ✅ overlay 清空 + `_CURATED_DESCRIPTIONS` 删除 | overlay = `{}`（534 B → 2 B）；`grep` 运行时代码 **0 命中**；两条路由已冻结（回写路径不存在） |
| **M8** | ✅ descriptors / legacy 双快照回填 | descriptor 描述 **23/23 == skill.md**（改造前 **8/23**）；legacy 快照 **30 行**、`description_zh` 列就位，主/镜像 **sha256 相同**；对比脚本字段差异 **15 → 0** |
| **M7** | ⚠️ V-guard ✅ / V-verify **【未验证】** / V-regress 已由 C1 覆盖 | 加 `description_zh` 后 `_vector_text_and_hash` 哈希 **23/23 不变**；端到端重编码在本机**模型加载挂起 18 分钟无输出**（CPU 0.5 s）⇒ 主动终止，见 §6 |
| **M9** | ✅ 守卫测试 + CI + **变红取证** | `pytest tests/unit/test_skill_description_single_source.py` → **32 passed**；篡改 1 条描述后 **5 failed / 4 passed**（原始输出见 §4.3） |
| **H-5** | ✅ 双口径落地（含调用方同步） | `--ci` → 退出 0 + `PASS-SKIP(not_applicable)`；`--verify` + 文件缺失 → **退出 2 + FAIL** |

---

## 2. 逐步命令与原始输出

### 2.1 S0 — 前置确认

```text
PS> python -c "from agent.skills_mgmt.file_store import _META_FIELDS as m;print(len(m), 'description_zh' in m)"
19 True
```

### 2.2 M0 — 冻结 3 条写路径

**改动**：`plugins/skills.py`（两条路由）、`agent/skills_mgmt/service.py`（白名单 + 自动补全）。

**验证（生产入口：真实 Flask app + `test_client`，令牌取 `app_server._API_TOKEN`）**：

```text
token enabled: True len: 64
POST /api/skills/describe      -> 409 {'error': '描述写路径已冻结（G1-B/M0）：技能描述唯一事实源为 data/skills_repo/<id>/skill.md 的 front matter —— 请改 description / description_zh，不要再写 data/skills_descriptions_overlay.json', 'frozen': True, 'id': 'email-helper', 'ok': False}
POST /api/skills/describe/auto -> 200 {'applied': [], 'count': 0, 'frozen': True, 'note': '描述自动补全入口已冻结（G1-B/M0）：中文说明请写 data/skills_repo/<id>/skill.md 的 description_zh', 'ok': True}
POST describe/auto (explicit)  -> 200 {'applied': [], 'count': 0, 'frozen': True, ...}
overlay unchanged: True | content: {}
```

**第 3 条写路径（主轨）实测**（隔离服务，`tmp_path`）：

```text
before: 'ORIG-A'
after svc.update(description=...): 'ORIG-A'        ← 白名单已移除，patch 被静默忽略
after svc.update(description=''): 'ORIG-A'
m0-b description before curate: ''
curate applied: [{"id": "m0-b", "action": "补全中文说明(已改为人工)", "detail": "主轨 description 写路径已冻结（G1-B/M0）；请在 data/skills_repo/m0-b/skill.md 写 description_zh"}]
m0-b description after curate: ''                  ← 不再自动写回主轨
```

### 2.3 M1 — 基线固化

```text
PS> python -c "import json;d=json.load(open('data/skills_repo/.migration/descriptions.baseline.json',encoding='utf-8'));print(len(d['skills']), sum(1 for v in d['skills'].values() if v['s1']!=v['s2']))"
23 15
```

**文件**：`data/skills_repo/.migration/descriptions.baseline.json`（21,266 B）。
每条记录含 `s1`/`s2`/两侧 sha256/长度/`conflict`/`skill_md_sha256`/`skill_md_endswith_newline`；
顶层含 `baseline_head` / `s1_s2_conflict_ids` / `main_track_only` / `note_main_track_only`。

**目录不进技能索引**：`.migration` 以 `.` 开头 ⇒ `file_store.py:587` 的
`entry.name.startswith(".")` 跳过；实测 `load_metadata_index()` 仍是 **23** 条（未变 24）。

### 2.4 M2 — 写中文（`description` 一字不改）

**命令**（`update_meta`，即生产写路径）：

```python
fs = SkillFileStore()
for sid in 15 个 pd-*:  fs.update_meta(sid, {"description_zh": baseline[sid]["s2"]})
```

**`git diff --numstat -- data/skills_repo`**：

```text
3	1	data/skills_repo/pd-brainstorming-697b717a-skill/skill.md
2	1	data/skills_repo/pd-dispatching-parallel-agents-b8065ccd-skill/skill.md
2	1	data/skills_repo/pd-executing-plans-95cbf64a-skill/skill.md
3	1	data/skills_repo/pd-finishing-a-development-branch-e085de5a-skill/skill.md
3	1	data/skills_repo/pd-frontend-design-77ea5c4e-skill/skill.md
3	1	data/skills_repo/pd-receiving-code-review-8934157e-skill/skill.md
2	1	data/skills_repo/pd-requesting-code-review-ca5ae995-skill/skill.md
2	1	data/skills_repo/pd-subagent-driven-development-8c375695-skill/skill.md
2	1	data/skills_repo/pd-systematic-debugging-556faa20-skill/skill.md
2	1	data/skills_repo/pd-test-driven-development-8562c8ad-skill/skill.md
3	1	data/skills_repo/pd-using-git-worktrees-d516703a-skill/skill.md
3	1	data/skills_repo/pd-using-superpowers-3aea3fc9-skill/skill.md
3	1	data/skills_repo/pd-verification-before-completion-af010352-skill/skill.md
2	1	data/skills_repo/pd-writing-plans-f846e3a2-skill/skill.md
3	1	data/skills_repo/pd-writing-skills-5da20e67-skill/skill.md
```

`+2−1` 与 `+3−1` 的差异来自 description 折行块的行数（14 条折 2 行、1 条不折）。

**逐条严格判定**（`git show HEAD:…` vs 现文件；忽略新增键行与 EOF 终止符）：

```text
pd-brainstorming-697b717a-skill                 fm_wo_zh==HEAD:True  body==:True  desc_unchanged:True  zh==baseline:True
...（15 行全部 True，略）
ALL PASS: True
```

**幂等**：第二次写入后 15/15 逐字节相同（`second write byte-identical: True`）。

**示例 hunk**（最短的一条，可直接看出 D-1/D-2 同组）：

```diff
@@ -17,4 +17,5 @@ source: knowledge_distill
 status: approved
 enabled: true
+description_zh: 在实现任何新功能或修复 Bug 时，请在编写具体实现代码之前使用。由 1 份素材蒸馏生成
 ---
 
@@ -120,3 +121,3 @@ Use when implementing any feature or bugfix, before writing implementation code
 
 ## 来源
-- test-driven-development
\ No newline at end of file
+- test-driven-development
```

### 2.5 M4 — UI 优先读 `description_zh`

**改动**（`yunshu-ui/src/pages/hub/memory/skills.tsx`，4 处）：

| 行 | 改动 |
|---|---|
| 40-43 | 类型加 `description_zh?: string` |
| 150-158 | `describe()` 不再发请求（写入口已冻结），改为提示正确入口 |
| 254-258 | 渲染改 `{(r.description_zh \|\| r.description) && …}` |
| 274-277 | 空态按钮条件同口径 + 文案改为「入口已冻结」 |

**验证**：

```text
PS> npx tsc --noEmit -p tsconfig.json      # 无输出、退出码 0
get_all rows: 30
M4 UI(zh||en) == 改造前主轨中文: 15 /15
对照: 若 UI 只读 description 显示中文的条数: 0 /15
sample: 适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是将 TDD 应用于流程文档编写。…
```

「对照 0/15」这一行是**这条改动必要性的量化证据**：不读 `description_zh`，15 条文案全部会从中文变英文。

### 2.6 M3 — 合并规则（文件轨优先 + 逃生开关）

**改动**（`agent/skills_mgmt/registry.py`）：新增 `_ENV_DESC_FROM_FILE_TRACK` /
`_desc_from_file_track()`，重写 `as_legacy_rows()`：主轨分支的描述改为「有 skill.md 就取它的」、
两分支都加 `description_zh` 键。

```text
rows: 30
row keys sample: ['description', 'description_zh', 'enabled', 'id', 'name', 'params']
M3 assert1 description==skill.md S1 : 15 /15
M3 assert2 description_zh==baseline S2 : 15 /15
all23 desc==S1: 23 /23

=== 逃生开关 CP_SKILL_DESC_FROM_FILE_TRACK=0 ===
desc == 主轨 S2 (旧行为): 15 /15
zh 仍读文件轨: 15 /15
sample pd-writing-skills desc now: '适用于创建、编辑或验证 agent 技能（SKILL.md）之前或过程中，核心是'
恢复默认后 desc==S1: 15 /15
```

### 2.7 M5 — capability 补 description（3 + 1 站点）并重跑 sync

```text
entries: 114 skill: 23 tool: 91
skill desc key: 23
skill desc nonempty: 23
tool desc key: 0            ← 改动前（工具侧尚未补）
skill desc == skill.md 逐字: 23 / 23 bad: []
```

**重跑 sync**（首次失败，原因与修法见 §5 偏差 D-4）：

```text
PS> python scripts/sync_capability_manifest.py --summary
[OK] 盘点表已写入 docs/rfc/云枢能力清单盘点表.md
[OK] 清单已写入 data/capability_manifest.json（114 条能力）
(exit 0)
```

**信封验证（生产入口）**：

```text
envelope total: 114 returned: 114
envelope: 114 skills: 23 tools: 91
envelope skill desc nonempty: 23 / 23      ← 改造前 0/23
envelope tool  desc nonempty: 91 / 91      ← 改造前 0/91（信封里本来就有，见 D-4）
```

**确定性**：同进程两次 `build_manifest()` **逐字段相同**；与磁盘清单比对 **0 条差异**
（即 `--check` 口径已自洽）。

### 2.8 M6 — 清 overlay + 删 `_CURATED_DESCRIPTIONS`

```text
overlay: 534 B -> 2 B，内容 "{}"
grep "_CURATED_DESCRIPTIONS" 运行时代码（agent/ plugins/）= 0 处「活引用」
（`plugins/skills.py` 仅剩解释「为什么删」的墓碑注释，不计为引用——守卫测试按此口径实现）
```

冻结先于删除已按 H-4/R11 执行：M0 的 `describe/auto` no-op 落盘**之后**才清 overlay。

### 2.9 M8 — 快照回填（descriptors / legacy）

**descriptor**：

```text
15 条 pd-* 的 descriptor.description != skill.md ⇒ update_fields({"capability": {"description": …}})
updated: 15
descriptor.capability.description == skill.md: 23 /23      ← 改造前 8/23
audit delta: 15 | descriptor.patch delta: 15
```

**legacy**：

```text
legacy rows rebuilt: 30
data\skills.json: len=14658 sha=501adfa06ef995d4 mtime=2026-09-26 01:04:34
agent\data\skills.json: len=14658 sha=501adfa06ef995d4 mtime=2026-09-26 01:04:34
mirror byte-identical: True
legacy row keys: ['description', 'description_zh', 'enabled', 'id', 'name', 'params']
legacy desc == skill.md S1 : 15 /15
legacy description_zh == S2 : 15 /15
```

**对比脚本归零**：

```text
total lines: 103 | DIFF count: 0 | OK count: 92
[SET] 仅在旧格式: ['code-observability', 'engineering-test-delivery', 'frontend-state-sync', 'global-core-principles', 'self-explanatory-ui', 'skill', 'testing-anti-patterns']   ← 7 条（H-3 裁定，本轮不动）
字段对比结果: HAS_DIFF    ← 仅因上面 7 条 only_legacy；**字段 DIFF 已 15 → 0**
```

### 2.10 H-5 — 双口径

```text
PS> python scripts/compare_skills_legacy_vs_repo.py --ci --legacy <不存在>   ⇒ exit 0
[compare] PASS-SKIP: legacy 快照不存在 (…)
[compare]            该断言在 CI 环境**不适用**（NOT_APPLICABLE），不等于通过；迁移校验请用 --verify。
[compare] RESULT: PASS-SKIP(not_applicable)

PS> python scripts/compare_skills_legacy_vs_repo.py --verify --legacy <不存在> ⇒ exit 2
[compare] FAIL: legacy 快照不存在 (…)
[compare] RESULT: FAIL(legacy_missing) —— 不是 ALL_MATCH
```

**调用方同步**（否则 CI 会因新语义误红 —— 这是本条的唯一破坏面）：
`.github/workflows/skills-check.yml` ×2 处、`scripts/simulate-nightly-scan.ps1` ×1 处加 `--ci`。

### 2.11 M9 — 守卫测试 + CI

```text
PS> python -m pytest tests/unit/test_skill_description_single_source.py -q --no-header -p no:cacheprovider --timeout=300
collected 32 items
tests\unit\test_skill_description_single_source.py ..................... [ 65%]
...........                                                              [100%]
通过: 32  失败: 0
```

**回归面（相关套件全跑）**：

```text
test_skill_description_single_source.py + test_update_meta_no_data_loss.py + test_skill_registry_audit.py
  + test_skill_index_cache.py + test_skills_mgmt.py
通过: 156  失败: 0  跳过: 1  xfail: 1（TF-IDF 检索阈值，改造前既有的 xfail）
```

---

## 3. 实际改动文件清单

### 3.1 `git diff --numstat`（本卡贡献的行数，用开工前快照做差得出）

| 文件 | 本卡新增 | 本卡删除 | 说明 |
|---|---:|---:|---|
| `plugins/skills.py` | +15 | −7 | M0 两条路由冻结 + M6 删字面量（该文件另有其他卡 +48/−26，合计 63/33） |
| `agent/skills_mgmt/service.py` | +26 | −5 | M0 白名单 + 自动补全（该文件另有其他卡 +190/−14，合计 216/19） |
| `agent/skills_mgmt/registry.py` | +53 | −4 | M3（该文件另有 D2 卡 +59/−7，合计 112/11） |
| `agent/skills_mgmt/store.py` | +27 | −2 | M8 legacy 快照描述来源 |
| `agent/descriptors/backfill.py` | +18 | −3 | M8 资产装载器描述取文件轨 |
| `agent/lines/callability.py` | +20 | −0 | M5 三站点 |
| `agent/skills_mgmt/searcher.py` | +24 | −1 | M4b 显式延期的理由与解除条件（**无行为改动**） |
| `scripts/sync_capability_manifest.py` | +6 | −1 | M5 `_FIELD_SPEC` |
| `scripts/compare_skills_legacy_vs_repo.py` | +81 | −18 | H-5 双口径 |
| `yunshu-ui/src/pages/hub/memory/skills.tsx` | +21/−11（含其他卡 21/11，本卡占 4 处） | — | M4 |
| `scripts/simulate-nightly-scan.ps1` | +5 | −2 | H-5 调用方 |
| `.github/workflows/skills-check.yml` | +6 | −2 | H-5 调用方 |
| `data/capability_manifest.json` | +271 | −27 | M5 重跑 sync（派生物） |
| `data/skills_descriptions_overlay.json` | +1 | −14 | M6 清空 |
| `data/skills_repo/pd-*/skill.md` ×15 | 各 +2 | 各 −1 | M2 |
| `docs/rfc/云枢能力清单盘点表.md` | +6 | −6 | M5 重跑 sync 的同期产物（E10 同源） |

**新增 3 个文件**：

| 文件 | 大小 | 作用 |
|---|---|---|
| `data/skills_repo/.migration/descriptions.baseline.json` | 21,266 B | M1 基线（M2/M3 的验收与回滚依据） |
| `tests/unit/test_skill_description_single_source.py` | 25,645 B | M9 守卫（G-1…G-7 + M2/M3/V-guard/H-5/索引缓存） |
| `.github/workflows/skill-description-single-source.yml` | — | M9 CI（3 个 job，每个都显式 `timeout-minutes`） |

### 3.2 `data/` 下每个被改文件的 sha256 前后对比

（`data/audit/` 与 `data/ui_settings.json` 是 gitignored，`git status` 对其无效，故一并给 sha256+mtime）

| 文件 | sha256 前 | sha256 后 | 长度变化 | mtime 后 |
|---|---|---|---:|---|
| `data/capability_manifest.json` | `c95a9d81cd177f4d…` | `1ca83c926cdf81a5…` | +50,840 | 2026-09-26 00:55:32 |
| `data/descriptors.json` | `25dda2f23e81a204…` | `259507ecde269408…` | −212,268 | 2026-09-26 01:03:33 |
| `data/skills.json` | `fe2f969ef073d582…` | `501adfa06ef995d4…` | +3,685 | 2026-09-26 01:04:34 |
| `agent/data/skills.json` | `fe2f969ef073d582…` | `501adfa06ef995d4…` | +3,685 | 2026-09-26 01:04:34 |
| `data/skills_descriptions_overlay.json` | `8a0af7190a294d86…` | `44136fa355b3678a…` | −532 | 2026-09-26 00:56:22 |
| `data/skills_repo/.index/cache.json` | `6f23d504187ec327…` | `0bee84830f41570e…` | +3,431 | 2026-09-26 00:53:22 |
| **`data/skills_mgmt.json`** | `9b5b0b786d65338b…` | `9b5b0b786d65338b…` | **0** | 2026-09-25 18:43:31（未触碰） |
| `data/audit/audit_chain.db` | （gitignored） | 55,160,832 B | +38 条记录 | — |
| `data/ui_settings.json` | — | — | **未变化**（`audit_governance_check.py` 只读自检确认 size/mtime_ns 不变） | — |

> **`data/skills_mgmt.json` 一动未动**是 M0 生效的直接证据：主轨描述写路径已被冻结，
> 全卡过程没有任何一次主轨 description 写入。

**skill.md 内容变化集合**：`15/23`，且 `sorted(changed) == 15 条 pd-*` ⇒ **D-7 成立**
（8 个非 pd-* 文件字节未变）。

### 3.3 审计链

```text
count 72683 | min_seq 1 | max_seq 72683 | dup_seqs 0 | gaps 0
records with seq > 72645（= 本卡开工前基线 72645）: 38
by action: {'skill.assess.curate': 1, 'descriptor.register': 1, 'descriptor.provenance': 1,
           'descriptor.patch': 16, 'trace.closed': 9, 'ui.chat.api_chat.post': 9,
           'ui.chat.api_chat_stream.post': 1}
```

- **本卡造成的记录：≤ 20 条**（`descriptor.patch` 15 + `descriptor.provenance`/`register` 各 1 + `skill.assess.curate` 1，
  另 1 条 `descriptor.patch` 与全部 `trace.closed` / `ui.chat.*` 共 18 条来自**同一工作区的其他活动**，非本卡）。
- **`seq` 无缺口、无重复**（`dup_seqs: 0`、`gaps: 0`，全链 1..72683 连续）。
- `scripts/audit_governance_check.py`：**PASS（FAIL=0 WARN=2）**，全链 72,683 条重算一致；
  2 条 WARN 均为**本卡之前就存在**的历史事项（2026-09-21 日根重放失败 = 已有封印后被回填缺陷；8 天缺 Merkle 日根）。
- 该脚本的**只读自检**同时确认：`audit_chain.db` / `daily_roots.jsonl` / `ui_settings.json` 运行前后 size+mtime_ns **未变化**。

---

## 4. D-1…D-7 核对表

| # | 声明 | 实测 | 判定 |
|---|---|---|---|
| **D-1** | 15 个 `pd-*` 各补 1 个末尾 CRLF（共 30 B） | 15 个文件 `orig_endNL=False → new_endNL=True`；`no-EOL before: 15 → after: 0`；二次写入**不再追加** | ✅ **成立**（且确认只补一次） |
| **D-2** | 15 个 skill.md 各新增 1 行 `description_zh` | `git diff` 每文件 1 行新增；15 条 `zh==baseline` 逐字相等 | ✅ **成立** |
| **D-3** | `.index/cache.json` 被改的 15 条 hash 与 mtime 更新 | 文件 sha256 变化、`+3,431 B`、mtime `00:53:22`；23 条 `meta.hash` 与 skill.md md5 **23/23 命中** | ✅ **成立**（且反向验证：改完仍同源） |
| **D-4** | `data/skills.json` / `agent/data/skills.json` **双份同时**更新且互为字节镜像 | 两份 `sha256=501adfa06ef995d4…`、均 14,658 B、`mirror byte-identical: True` | ✅ **成立** |
| **D-5** | `data/descriptors.json` mtime 更新 + 若干条文本改变 | mtime `2026-09-26 01:03:33`（原 `2026-09-17 21:40:56`）；**29 条 skill 记录中被改的是 15 条**（非 8 条） | ✅ **成立，但条数与声明不符**（见 §5 偏差 D-3） |
| **D-6** | 审计链追加一批 `descriptor.register`，须在迁移记录里显式声明为预期 | 追加 **1 条** `descriptor.register` + **1 条** `descriptor.provenance` + **15 条** `descriptor.patch`（合计 17 条 descriptor.*） | ✅ **成立、已在本报告显式声明**（动作名与声明略有出入，见 §5 偏差 D-6） |
| **D-7** | **不应出现**：8 个非 `pd-*` 的 `description` 变化 | `sorted(changed skill.md) == 15 条 pd-*`，8 个 `file_track` 技能字节未变 | ✅ **成立（0 条）** |

---

## 5. 偏差清单（**不粉饰**）

### D-1【行号漂移 · 规格需勘误】`service.py` 的两个落点都漂了，且方向与规格相反

| 规格写的落点 | 实测落点 | 漂移 |
|---|---|---|
| `service.py:1624-1628` 的 `allowed` 集合 | **`:1684-1690`** | **+60** |
| `service.py:1315` 的自动补描述 | **`:1375`** | **+60** |

同时确认（**零漂移**）：`plugins/skills.py:443` / `:459`、`registry.py:205-245`、
`callability.py:982-988 / 1000-1003 / 1164-1170`、`sync_capability_manifest.py:165` / `:59-61`、
`vector_adapter.py:682`（`_vector_text_and_hash`）**全部与规格一致**。

### D-2【本卡引入的行为变更，规格未预告】M0 让 `PATCH` 通路改描述**静默无效** ⇒ D4 卡的 6 个单测变红

这是本卡**唯一一处破坏既有测试**的改动，必须显式登记：

```text
PS> python -m pytest tests/unit/test_skill_update_audit.py -q --tb=no
tests\unit\test_skill_update_audit.py FFF..F.FF                          [100%]
FAILED ...::TestContractAndBestEffort::test_audit_failure_does_not_break_update
FAILED ...::TestContractAndBestEffort::test_audit_disabled_writes_nothing_but_logs
FAILED ...::TestContractAndBestEffort::test_return_semantics_unchanged_with_and_without_audit
FAILED ...::TestFieldChangeAudit::test_description_update_lands_in_chain
FAILED ...::TestFieldChangeAudit::test_description_value_never_enters_chain
FAILED ...::TestFieldChangeAudit::test_mixed_patch_records_both_families
========================= 6 failed, 3 passed in 2.73s =========================
```

**根因（单条）**：这 6 个用例都用 `description` 当「非 enabled 的白名单字段」的见证字段；
M0 把 `description` 移出白名单后，`svc.update(id, {"description": …})` 变成**非白名单键**（静默忽略、
不留痕），于是「改字段 ⇒ 落 `skill.update`」这条不变量失去见证。举例：

```text
E   AssertionError: 期望恰好 1 条 skill.update，实得 0    assert 0 == 1
```

**我没有改这个文件**（它属于 D4 卡的交付物，且 M0 的规格明确要求移除该白名单键）。
**两条可选修法（各 1–3 行，交 D4 owner 判）**：

1. 把见证字段从 `description` 换成 `tags`/`content`，并**新增**一条断言
   「`description` 已不在白名单 ⇒ patch 被忽略、0 条记录」——把 M0 的新不变量也守起来；
2. 或在这些用例里显式 `monkeypatch` 白名单（把 `description` 临时加回），
   把用例语义限定为「审计逻辑本身」，与白名单裁剪解耦。

推荐 **1**：它同时把「描述只在 skill.md 里改」这条 G1 结论固化成回归。

### D-3【与规格数量不符】D-5 声明「29 条中的 8 条」，实测被改的是 **15 条**

`descriptors.json` 里 29 条 skill 记录中，**15 条 pd-*** 的描述原为**主轨中文**（与 skill.md 的英文不同），
8 条 `file_track` 技能本来就是文件轨来源、无需改。⇒ 规格里的「8 条文件轨记录」应为「15 条主轨记录」。
（规格原文：「29 条 skill 中的 8 条文件轨记录」。）

### D-4【规格未列的必要站点】M5 必须同时给**工具**条目补 `description`，否则 sync 直接失败

`_FIELD_SPEC` 一加 `description`，`validate()` 就对**缺该键**的条目报错。只补技能侧时：

```text
[FAIL] 清单自洽性校验失败（91 条）：
   - apply_patch: 缺少统一字段 'description'
   - arch_diagram: 缺少统一字段 'description'
   …（91 条工具全部）
(exit 1)
```

**修法**：`agent/lines/callability.py::_tool_entry` 的返回字典补
`"description": str(doc.get("description") or "")`（数据本来就有：`load_tool_docs()` 的 91/91 条非空）。
这使 G1-B 的改动面从「23 条技能描述」扩到「114 条能力的 description 进清单」，这是**规格遗漏**，
不是本卡扩权。

### D-5【规格未列的必要站点】M8 的 descriptors 回填**不能**靠 `full_backfill()` 完成

`full_backfill()` 的 `_planned_patch()` **不含 description**（它只管 provenance / data_class / risk /
governance），实测：

```text
assets_total: 30   applied: {"register": 0, …, "no_op": 30}   ← 全 no-op，描述一个字没改
```

**修法（两处）**：① `load_skill_assets()` 改为「description 取文件轨、其余字段仍取主轨」；
② 用 registry 的公开写 API `update_fields({"capability": {"description": …}})` 逐条回填该字段。
`description` 在 descriptor 里也**没有**通用的回填通路 —— 这一点规格未提。

### D-6【动作名与声明不符】D-6 说「追加 `descriptor.register`」，实际主增量是 `descriptor.patch`

`descriptor.register` 只追加了 **1** 条（新登记 `cp.skill.code-observability`）；描述回填走 `update_fields`
⇒ **15 条 `descriptor.patch`**；`load_skill_assets` 修好前的那一轮还追加了 1 条 `descriptor.provenance`。
合计 17 条 `descriptor.*`。审计链 `seq` 全程连续（gaps 0 / dup 0）。

### D-7【规格未列的必要改动】`store._collect_legacy_rows()` 也必须同口径

G1-A/G1-B0 只把 `registry.as_legacy_rows()` 列为合并规则落点。**但 legacy 快照并不走它**：
`SkillStore._collect_legacy_rows()` 对「主轨已有的 15 条」**先占位**，文件轨分支的 `if sid in seen: continue`
根本不会覆盖它们 ⇒ 第一次重建后实测：

```text
legacy desc == skill.md S1 : 0 /15      ← 快照里仍是主轨中文
legacy row keys: ['description', 'enabled', 'id', 'name', 'params']   ← 连 description_zh 列都没有
```

**修法**：该方法的 description/description_zh 两列改取文件轨（`SkillFileStore`，路径由 store 的 `_path`
推导以兼容隔离测试），其余字段仍以主轨为权威。修好后 `legacy desc == skill.md 15/15`、`zh == S2 15/15`，
`compare_skills_legacy_vs_repo.py` 的字段 DIFF 归零。

### D-8【显式延期】M4b（`searcher.py` 中英同时计分）**本轮未实施**

延期理由已写进 `agent/skills_mgmt/searcher.py:41` 的 docstring（可 review），三条：

1. **不在生产检索链上**：本器只被 `GET /api/skills-mgmt/search` 使用
   （`routes_skills_mgmt.py:152`）；生产检索走 `loader.load_metadata_index()`（文件轨），三路是 TF-IDF / 向量 / BM25。
2. **数据不在手上**：`Skill` 模型（`models.py:241`）与主轨 JSON 都**没有** `description_zh` ⇒ 逐条查文件轨会把 UI 列表搜索变成 IO 热点。
3. **口径冲突**：M0 之后主轨文案与文件轨不再同步，把主轨英文与文件轨中文混进同一个打分公式，等于把废弃副本拉回打分。

**解除条件**：`Skill` 模型加 `description_zh` 只读镜像，**或**在 `search()` 外层一次取好索引再计分。

### D-9【与规格不符的 D-2 判据】`patch_front_matter` 只在末尾补 1 个 EOL，其余字节不动

D-1 说「15 个文件各补 1 个末尾 CRLF（共 30 字节）」——`
` 是 2 字节，**15×2 = 30 B 正确**。
但要注意：这个 EOF 终止符是 `patch_front_matter` 的**既有行为**（`:335-337`「有实际改动时保证以换行结尾」），
不是 M2 特意加的；且**只补一次**（已实测幂等）。

### D-10【并发工作区声明】执行期间仓库被其他卡持续修改

开工时 `git status` 有 21 张卡的未提交改动；执行期间**文件数与内容继续变化**（例：
`agent/tools/__init__.py` 的 mtime 为 `2026-09-25 20:16`，晚于旧清单的 `2026-09-24 23:32`）。
本卡的每一步都**当场 grep 复核行号**、并在关键节点用 `git hash-object` 留快照。
`HEAD` 全程未变（三次 `git rev-parse HEAD` 均为 `5c9ace10…`）。

**由此带来一处无法完全归因的 diff**：`data/capability_manifest.json` 里 9 条扩展类工具的
`location_unresolved` / `location_evidence` 也变了（例：`ext_install` 的「41 个调用点静态无法解析」→「42 个」）。
这些条目的判定依赖对 `agent/tools/*.py` 与 `plugins/skills.py` 的静态扫描，而这两个位置在旧清单生成之后
被其他卡改过 ⇒ **最可能**是其他卡的未提交改动被本次 sync 一并派生进来。
**本卡无法在不动其他卡文件的前提下证明**这一点，故如实标注为「归因不完全」，**不声称它一定是别人的**。

---

## 6. 【未验证】项与残留风险

### 6.1 【未验证】M7 的 V-verify 端到端重编码

**规格要求**：改一条 description ⇒ 观察 `ensure_indexed.done` 的 `indexed_or_refreshed >= 1`，
并读 `chroma.sqlite3` 的 `embedding_metadata.description` 断言已是新值。

**实测结果：无法完成。** 离线窗口里调用 `SkillVectorAdapter(fs).ensure_indexed()` 后，进程
**18 分钟零输出、CPU 仅 0.5 s**（工作集 32 MB，无子进程活动）——即卡在模型加载/下载等待上，
而非在编码。为不违反「不要启动长期驻留的东西、不留挂死进程」，我主动终止了该 job。

**已做的替代验证（有据可查，非空转）**：

```text
=== V-guard：加 description_zh 不改变 _vector_text_and_hash ===
V-guard ALL 23 PASS: True

=== V-verify（离线可做的那一半）：磁盘向量文本 vs 当前 _build_vector_text ===
向量库里 8 条: [context_aware, emotion_expression, memory_summary, proactive_suggestion,
                safety_guard, scripted-selftest, self_reflection, voice_interaction]
  与当前向量文本一致(fresh): ['emotion_expression']
  与当前向量文本不一致(stale): 其余 7 条
```

**两条有价值的发现**：

1. **V-guard 成立**：`_build_vector_text`（`vector_adapter.py:285-316`）只取
   `name / description / tags / category` + `body[:N]`，**不含 `description_zh`** ⇒
   加中文展示文案**不会**触发 4.25 GB 模型重编码（23/23 哈希不变）。
2. **发现一处先于本卡的向量陈旧**：`data/skill_vectors/native_chroma/chroma.sqlite3` 的 mtime 是
   **2026-07-27**，而其 8 条 `chroma:document` 里有 **7 条**与当前 `_build_vector_text` 不一致。
   这 **不是 G1-B 造成的**（M2 只动了 15 条 `pd-*`，那 15 条**根本不在**这个向量库里）。
   按 C1 的内容哈希判据，下一次 `ensure_indexed()` 会把这 7 条判为 dirty 并重编码 —— 即 C1 的修复**会**发现它。
   本卡**未执行**该重编码。

### 6.2 【未验证】`native_chroma` 后端的真实落盘写入

同上：无法在离线窗口内拉起后端，故「重编码后 metadata 确实变成新值」这一步**未观测**。
（`data/skill_vectors/chroma.sqlite3` 与 `native_chroma/chroma.sqlite3` 两个库都未被我改动：
mtime 仍为 `2026-07-28 13:54:11` / `2026-07-27 06:30:26`。）

### 6.3 【未验证】`npx tsc` 之外的 UI 端到端

只做了类型检查（退出码 0）与 API 数据面断言（15/15）。**没有**在浏览器里点开技能管理页
逐字比对 15 条文案（规格 M4 的验证方式写的是「人工打开页面核对」）。
替代证据：`zh‖en` 与「改造前主轨中文」逐字相等 15/15，以及「只读 description 会显示 0/15 中文」的反向对照。

### 6.4 残留风险

| # | 风险 | 触发信号 | 预案 |
|---|---|---|---|
| R-a | **D4 卡的 6 个单测目前是红的**（§5 D-2），若不修，全量回归会带 6 个失败 | `pytest tests/unit/test_skill_update_audit.py` | 按 §5 D-2 的修法 1 改见证字段；这是**跨卡**事项，需 D4 owner 或本卡 owner 拍板 |
| R-b | `capability_manifest.json` 的一次性 diff 含 9 条工具的 location 字段变化（§5 D-10），**归因不完全** | `python scripts/sync_capability_manifest.py --check`（当前绿） | 若 reviewer 要求纯 G1-B diff，可先 `git stash` 其他卡改动再重跑 sync 对照 |
| R-c | 主轨 `description` 仍存在（22 条），只是**读**不再用它 | `grep` 主轨仍见中文描述 | H-3/后续卡：把主轨 description 清空或转为只读镜像；本轮**刻意不做** |
| R-d | `searcher.py`（管理页搜索）仍只看主轨英文/中文混排 | 管理页用中文搜 pd-* 命中率低 | §5 D-8 的解除条件 |
| R-e | 向量库 7 条陈旧（§6.1/6.2），未重编码 | 向量路命中旧文案 | 在**有模型可用的窗口**跑一次 `ensure_indexed()`；C1 的 hash 判据会自动挑出这 7 条 |
| R-f | `compare_skills_legacy_vs_repo.py` 裸调用现在是**迁移校验口径**（缺文件即红） | 本地/脚本裸跑会退出 2 | 已同步 `skills-check.yml`(×2) 与 `simulate-nightly-scan.ps1`；**其他未登记的调用方需自查** |

---

## 7. 每步回滚方法（**定向 revert，禁止整文件 checkout**）

> 硬约束：仓库里 8+ 个代码文件是「已修改未提交」且**混有其他 21 张卡的成果**，
> `git checkout <file>` 会把别人的成果一起回退。**唯一例外**：`data/skills_repo/` 下
> 除 `.migration/` 外全部是提交态（`git status -- data/skills_repo` 只有我改的 15 个文件），
> 故 M2 可用 `git checkout` 回滚。

| 步 | 回滚方法 |
|---|---|
| **M0** | 定向 revert 5 处：`plugins/skills.py` 两条路由函数体 + `service.py` 的 `allowed` 集合（把 `"description"` 加回）+ `curate_skills` 的自动补全分支。**不要** `git checkout` 这两个文件 |
| **M1** | 删除 `data/skills_repo/.migration/descriptions.baseline.json`（及整个 `.migration/` 目录） |
| **M2** | **`git checkout -- data/skills_repo/`**（本卡唯一允许整目录 checkout 的一步；`.`migration/` 是 untracked，不受影响）。若不希望留基线，再删该目录 |
| **M3** | 定向 revert `registry.py` 的 `as_legacy_rows()` 与两个新符号；**或不改代码**，直接置 `CP_SKILL_DESC_FROM_FILE_TRACK=0`（逃生开关，实测回退 15/15，且 `description_zh` 仍可用） |
| **M4** | 定向 revert `skills.tsx` 的 4 处；重新构建前端产物 |
| **M4b** | 无行为改动，仅 docstring；如需彻底移除，删 `searcher.py:41-70` 的说明段落 |
| **M5** | 定向 revert `callability.py` 三处（含 `_tool_entry` 的 description）+ `sync_capability_manifest.py` 的 `_FIELD_SPEC`，然后**重跑** `python scripts/sync_capability_manifest.py`；`data/capability_manifest.json` 与盘点表是**已跟踪的派生物**，可 `git checkout` 单独回滚 |
| **M6** | 定向 revert `plugins/skills.py` 的字面量段落；overlay 内容：`git checkout -- data/skills_descriptions_overlay.json` |
| **M8** | descriptors/legacy 都是**可重建产物**：回滚代码（`backfill.py` 的 `load_skill_assets`、`store.py` 的 `_collect_legacy_rows`）后重跑 `SkillStore().sync_to_legacy_skills_json()`；`data/descriptors.json` 无历史依赖，最坏情况删除后由 registry 重建（**注：原文件已被 M8 覆盖，无备份**，见残留风险 R-b） |
| **M7** | 无代码改动。若将来执行了重编码，回滚 = 重跑一次全量重建 |
| **M9** | 删除 `tests/unit/test_skill_description_single_source.py` 与 `.github/workflows/skill-description-single-source.yml` |
| **H-5** | 定向 revert `compare_skills_legacy_vs_repo.py`（`load_legacy` / `_required_mode` / `main`）+ 三个调用方的 `--ci` |

**一次到位回滚（保守）**：`git checkout -- data/skills_repo/ data/skills_descriptions_overlay.json data/capability_manifest.json "docs/rfc/云枢能力清单盘点表.md"`，
再按上表逐文件定向 revert 代码，最后 `python -c "from agent.skills_mgmt.store import SkillStore; SkillStore().sync_to_legacy_skills_json()"` 重建 legacy 快照。

---

## 8. 遗留项

| # | 遗留 | 归属 |
|---|---|---|
| 1 | **M4b 未实施**（`searcher.py` 中英同时计分），理由与解除条件已写进代码 | 后续卡 |
| 2 | **D4 卡的 6 个单测红**（M0 的直接后果，需把见证字段从 `description` 换掉） | D4 owner / 本卡 owner 共决 |
| 3 | **H-3 的 5 条待纳入 / 2 条不纳入**（`code-observability` / `engineering-test-delivery` / `frontend-state-sync` / `self-explanatory-ui` / `testing-anti-patterns` 纳入；`global-core-principles`（常驻准则）、`skill`（description 被错填成指令内容）不纳入） | **G1-C**（已在决策中确认，本轮**故意未执行**） |
| 4 | 主轨 `description`（22 条）仍在库里，只是读路径不再用它 | 后续卡（清空或转只读镜像） |
| 5 | 向量库 7 条陈旧 + V-verify 端到端未做 | 需模型可用窗口 |
| 6 | `capability_manifest.json` 中 9 条工具 location 字段的一次性 diff 归因不完全 | reviewer 判 |
| 7 | 行号漂移：`service.py` `allowed` `1624→1684`、自动补全 `1315→1375`（+60，规格需勘误） | 文档勘误 |

---

## 9. 残留物自查（收尾）

```text
PS> git worktree list
C:/Users/Administrator/agent 5c9ace10 [master]        ← 只有主工作区

PS> Get-NetTCPConnection -State Listen | Where LocalPort in (5678, 5000, 8000)
（空）                                                ← 5678 空闲，无残留服务

PS> git status --porcelain
108 条 ⇒ 逐条核对：无一条是本卡在仓库根/agent//plugins/ 下新建的临时脚本或探针
（所有探针都写在 %TEMP%\g1b_*.py）
```

- **未做任何 `git add` / `git commit`**（工作区保持可 review 的未提交状态）。
- 清掉了一处**我自己**造成的残留：`undefined/g1b_recon.py`（探针因相对路径被写进了已有的
  `undefined/` 目录；该目录下的另一个文件 `b3w_recon.py` 属于其他卡，未触碰）。
- `git worktree list` / 端口 / 进程均无本卡残留。
