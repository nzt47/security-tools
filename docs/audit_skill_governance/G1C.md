# G1-C · H-3 执行报告（5 条主轨独有技能纳入事实源 + 3 条残留修复）

> 任务卡：**G1-C**（含前置 **F1b-C**）
> 基线：`HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（开工与收尾各实测一次，未变）
> 环境：Python 3.12.0（系统解释器，**未用 venv**）；Windows；未启动任何常驻服务；
> 全程**无 `git add` / `git commit`**；`git worktree list` 只有主工作区；**零出网**。
> 裁定依据：`docs/audit_skill_governance/G1_DECISIONS.md`（H-1…H-5）
> 上游：`G1B_REPORT.md`（唯一事实源收敛）+ `G1B0_recheck.md` §4 + `G1A_reconciliation.md` §9.3/§9.4
> 探针位置：`C:\Users\Administrator\AppData\Local\Temp\g1c\*.py`（**全部在仓库外**）

---

## 0. 结论速览

| 项 | 判定 | 一行证据 |
|---|---|---|
| **F1b-C**（`create()` 丢字段 / 行尾随平台变） | ✅ 已修 | 改前 3 个白名单外键**全丢**、行尾 CRLF；改后 **3/3 落盘**、行尾 LF；新单测 `10 passed` |
| **第 1 步**：5 条迁移进 `skills_repo` | ✅ 完成 | `load_metadata_index()` **23 → 28**；5 条逐条 `zh 逐字/正文逐字/合并=文件/主轨=文件zh/索引/检索/清单` 全 True |
| **真的可召回**（验收核心） | ✅ 成立 | 倒排候选池 41–60 token/条；TF-IDF `match()` **5/5 HIT**（`total_scanned=28`）；信封 skill 23→28 且描述 28/28 非空 |
| **第 2 步**：`skill` 描述修文案 | ✅ 完成 | `data/skills_mgmt.json` 全文 **1 行**变化（sha `9b5b0b78…` → `bcda9ecf…`），已无编号步骤/`<san_yi_analysis>` |
| **第 3 步**：`global-core-principles` | ✅ 调查完毕（**只给处置建议，未改 prompt 装配**） | 检索层不可达（实测）；其准则正文（1421 字符）**全仓无读者**；只在合并视图里当一行元数据 |
| **第 4 步 / R-d** | ✅ 已修（修法落在 `agent/skills_mgmt/` 内） | `searcher.py` + `service.py`；`17 passed`，含"主轨副本不再命中"红路 |
| **23 → 28 打破了什么** | ⚠️ **比预计多**：G1-B 守卫 + **4 个测试文件的 14 处能力基线锁** | 已全部更新到新契约（`114→119 / 23→28 / local 93→98`），**未放宽任何断言** |
| **守卫测试** | ✅ `32 passed`（= G1-B 基线，未退化） | 改动仅 1 处：`KNOWN_MAIN_TRACK_ONLY` 7 → 2 |
| **全相关回归** | ✅ `978 passed / 0 failed / 7 skipped / 1 xfailed` | 33 个相关测试文件；skip 全为 `--runslow`，xfail 是既有 TF-IDF 阈值项 |
| **审计链** | ✅ 链完整；本卡 **+5 条** | `count=72700 / seq 1..72700 连续 / dup=0 / gaps=0`；本卡 = `descriptor.patch` ×5（seq 72696–72700） |
| **残留物** | ✅ 无 | 无 `undefined/`；无我起的服务；无我的存活进程；探针全在 `%TEMP%\g1c` |

---

## 1. 第 0 步（F1b-C）：`SkillFileStore.create()` 的改前 / 改后原始对照

### 1.1 落点复核（**行号已漂移，规格需勘误**）

| 规格说法 | 实测落点（改前） | 漂移 |
|---|---|---|
| `file_store.py` 的 `create()` 约 `:724` 仍走 `serialize` + `write_text` | **`create()` 定义在 `:745`**，两个残余落点是 **`:775-776`**（serialize + write_text） | **+21** |
| `serialize()` 的白名单裁剪 | `:173-181` | 与规格一致 |
| `update_meta` 已是最小侵入（F1b） | `:825-860`；`patch_front_matter` 在 `:229-338` | 与规格一致 |
| `_META_FIELDS` = 19 键且含 `description_zh` | `:75-91`，实测 `19 True` | 与规格一致 |

### 1.2 改前：探针原文（`%TEMP%\g1c\f1bc_before.py`，隔离 repo，**不碰生产**）

```text
### isolated repo_path = C:\Windows\TEMP\g1c_f1bc_qufbd0z5\repo
### _META_FIELDS size = 19
### 白名单外的键 = ['created_at', 'custom_nested', 'unknown_custom_field']

### skill.md 原始字节 (repr) ###
b'---\r\nid: f1bc-probe\r\nname: Probe Skill\r\ndescription: EN description\r\ndescription_zh: \xe4\xb8\xad\xe6\x96\x87\xe8\xaf\xb4\xe6\x98\x8e\r\nenabled: true\r\nstatus: approved\r\n---\r\n\r\n# body line'

### 字节级统计 ###
len = 150 | CRLF count = 9 | lone LF = 0

### 逐键落盘检查（在原始文本里找 'key:' 行）###
   WHITELIST id                     present_in_file=True
   WHITELIST name                   present_in_file=True
   WHITELIST description            present_in_file=True
   WHITELIST description_zh         present_in_file=True
   WHITELIST enabled                present_in_file=True
   WHITELIST status                 present_in_file=True
   OUT-OF-WL unknown_custom_field   present_in_file=False
   OUT-OF-WL created_at             present_in_file=False
   OUT-OF-WL custom_nested          present_in_file=False

### 结构化判定 ###
F1b-C-1 白名单外字段被静默丢弃: ['created_at', 'custom_nested', 'unknown_custom_field']
F1b-C-2 行尾 = CRLF (write_text 默认 newline=None ⇒ Windows 翻成 CRLF)
```

**改前结论**：① 3/3 白名单外键被**静默丢弃**（无日志、无异常）；② 落盘 9 个 CRLF、0 个 LF。

### 1.3 改后：同一份 meta 的探针原文（`f1bc_after.py`）

```text
### isolated repo_path = C:\Users\Administrator\AppData\Local\Temp\g1c\scratch_after\repo
### _META_FIELDS size = 19
### 白名单外的键 = ['created_at', 'custom_nested', 'unknown_custom_field']

### skill.md 原始字节 (repr) ###
b"---\nid: f1bc-probe\nname: Probe Skill\ndescription: EN description\ndescription_zh: \xe4\xb8\xad\xe6\x96\x87\xe8\xaf\xb4\xe6\x98\x8e\nenabled: true\nstatus: approved\nunknown_custom_field: KEEP_ME\ncreated_at: '2026-01-02T03:04:05'\ncustom_nested:\n  a: 1\n  b:\n  - 1\n  - 2\n---\n\n# body line"

### 字节级统计 ###
len = 244 | CRLF count = 0 | lone LF = 16

### 逐键落盘检查 ###
   WHITELIST id/name/description/description_zh/enabled/status   present_in_file=True（6/6）
   OUT-OF-WL unknown_custom_field   present_in_file=True
   OUT-OF-WL created_at             present_in_file=True
   OUT-OF-WL custom_nested          present_in_file=True

### 结构化判定 ###
F1b-C-1 白名单外字段被静默丢弃: [] ⇒ 无丢失
F1b-C-2 行尾 = LF
F1b-C-3 parse() 仍按白名单**读**（展示契约不变）: True
F1b-C-4 update_meta 之后白名单外字段仍保留: True
F1b-C-5 update_meta 之后行尾仍 = LF
F1b-C-6 二次 create 幂等性（重读字节一致）: True

### 落盘文件全文 ###
---
id: f1bc-probe
name: Probe Skill
description: EN description
description_zh: 中文说明
enabled: true
status: approved
unknown_custom_field: KEEP_ME
created_at: '2026-01-02T03:04:05'
custom_nested:
  a: 1
  b:
  - 1
  - 2
---

# body line
```

### 1.4 改了什么（`agent/skills_mgmt/file_store.py`，2 处）

```diff
     @staticmethod
-    def serialize(meta: Dict[str, Any], body: str = "") -> str:
-        """序列化为 skill.md 文本"""
-        # 只写白名单字段
-        filtered = {k: v for k, v in meta.items() if k in _META_FIELDS}
+    def serialize(meta: Dict[str, Any], body: str = "",
+                  *, only_meta_fields: bool = True) -> str:
+        """序列化为 skill.md 文本（docstring 说明两档语义，见文件）"""
+        # 只写白名单字段（only_meta_fields=False 时按调用方给的原样写）
+        filtered = ({k: v for k, v in meta.items() if k in _META_FIELDS}
+                    if only_meta_fields else dict(meta))
```

```diff
             # 写 skill.md
             meta = {**meta, "id": skill_id}
-            md_content = SkillMDParser.serialize(meta, instruction)
-            (skill_dir / _SKILL_MD).write_text(md_content, encoding="utf-8")
+            md_content = SkillMDParser.serialize(meta, instruction,
+                                                 only_meta_fields=False)
+            with (skill_dir / _SKILL_MD).open("w", encoding="utf-8",
+                                              newline="") as _fp:
+                _fp.write(md_content)
+            _outside = sorted(k for k in meta if k not in _META_FIELDS)
+            if _outside:
+                logger.info(log_dict({'module_name': 'file_store',
+                                      'action': 'create.meta_outside_whitelist',
+                                      'skill_id': skill_id, 'keys': _outside}))
```

**设计说明（不易 / 变易 / 简易）**

- **不易**：`serialize()` 的**默认行为一字未改**（`only_meta_fields=True`）——它另有调用方（把既有
  front matter 重新序列化），默认必须仍是白名单裁剪。新单测 `test_serialize_default_contract_unchanged` 把这条钉住。
- **变易**：create 侧的白名单语义从"**保留什么**"退回"**允许改什么**"（后者只作用于 update/patch）。
  新建技能时不存在"文件现状"可裁剪，调用方给的 meta 就是初始内容 ⇒ 丢弃即数据丢失。
- **简易**：行尾修为与 `update_meta` **同一个写法**（`open(..., newline="")`），不引入新抽象。

### 1.5 为什么必须通用地"不丢"，而不是只保住 `description_zh`

`description_zh` 已在 `_META_FIELDS`（19 键）内，所以**本卡的 5 次 `create()` 里它本来就能落盘** ——
这一点必须说清楚，**不能把它说成"不修就写不进去"**。真正的风险是通用形态：
只要将来有人往 `create()` 的 meta 里加任何一个白名单外键（例如 `created_at` / 人工维护的
`source_rev` / G1 后续可能引入的 `description_policy`），它就会**静默消失**，而
`create()` 是新文件唯一的内容来源（update 侧还有 F1b 的"保留文件现状"兜底）。
本卡实测**坐实了这个通用形态**（3/3 丢弃），并把它连同行尾不确定性一起修掉。

### 1.6 新单测（`tests/unit/test_skill_create_no_data_loss.py`，**10 passed**）

覆盖：白名单外标量 + 嵌套结构的落盘与 YAML 回读、`id` 强制为入参、`parse()` 读侧白名单不变、
create 后再走一次 `update_meta` 不丢、`serialize()` 默认契约不变、落盘字节 == serialize 输出（无换行翻译）、
LF-only、与 git blob 形态一致。

---

## 2. 第 1 步：5 条技能迁移成 `skill.md`（真正可召回）

### 2.1 迁移方式：走**生产入口** `SkillFileStore.create()`

```text
### 生产 SkillFileStore repo_path = C:\Users\Administrator\agent\data\skills_repo
### 迁移前 load_metadata_index() 条数 = 23

### testing-anti-patterns        bytes=9133   CRLF=0  zh逐字=True 正文逐字=True
      sha256 = 9b90b8a3fc1acb2a0b5b61505f55620af9d816676301eada8eba833b67b91779
      body 与主轨 content 的差异 = 0 字符（仅 CRLF→LF 归一：0 处）
### code-observability           bytes=1986   CRLF=0  zh逐字=True 正文逐字=True
      sha256 = 1957feded64d545a937c7ee70e4bde617af40450c3a53d41ebb2a8d954fba96b
      body 与主轨 content 的差异 = -16 字符（仅 CRLF→LF 归一：16 处）
### engineering-test-delivery    bytes=3288   CRLF=0  zh逐字=True 正文逐字=True
      sha256 = c58a1a705730a002b4c930aa95a2e97145f8e1faefef77d807e1743aec8b1aea
      body 与主轨 content 的差异 = 0 字符（仅 CRLF→LF 归一：0 处）
### frontend-state-sync          bytes=3750   CRLF=0  zh逐字=True 正文逐字=True
      sha256 = d3feb87c085a95369d9659653064d68f68a76da497dc73749d25318d4d20d36b
      body 与主轨 content 的差异 = -34 字符（仅 CRLF→LF 归一：34 处）
### self-explanatory-ui          bytes=2049   CRLF=0  zh逐字=True 正文逐字=True
      sha256 = 12a89074393c2f7444f28a015e481a58ca9df005e5ab9a0f5eed385abd63c6e5
      body 与主轨 content 的差异 = -17 字符（仅 CRLF→LF 归一：17 处）

### 迁移后 load_metadata_index() 条数 = 28
### 5 条是否都在索引里 = {'testing-anti-patterns': True, 'code-observability': True,
       'engineering-test-delivery': True, 'frontend-state-sync': True, 'self-explanatory-ui': True}
```

> **正文"逐字搬运"的口径**：3 条主轨 `content` 内部是 CRLF（16/34/17 处），落盘归一为 LF。
> 这不是改写：生产读路径 `SkillMDParser.parse()` 本身就是按行切分再以 LF 重连 —— 即**读出来已经是 LF**。
> 归一后 `file_store.read()[1] == 主轨 content`（实测 5/5 True）。另 2 条 content 本就是 LF，差异 0 字符。

### 2.2 front matter 的形状（照抄现有语料，不自己发明）

既有 23 个 skill.md 的键频次实测：

```text
=== 23 个 skill.md 的 front matter 键频次 ===
   id/name/description/category/tags/enabled/status/author/source/content_type   23/23   ← 形状基线（10 键）
   description_zh                                                                15/23
   version                                                                        8/23
   default_params                                                                 1/23

=== 各文件键集合（去重）===
   x15  [author, category, content_type, description, description_zh, enabled, id, name, source, status, tags]
   x7   [... 无 description_zh，有 version]
   x1   [... 有 default_params]
```

5 条新文件采用的键集合 = **既有 15 条 description_zh 形态**（10 键 + `description_zh`），
键序也照抄（`id, name, description, content_type, category, tags, author, source, status, enabled, description_zh`）：

```yaml
---
id: self-explanatory-ui
name: self-explanatory-ui
description: Use when doing interface design or frontend UI development. Integrates
  feature explanations and help information directly into the visual interface — visual
  hierarchy, icon hints, state feedback and contextual help — so users understand
  and operate the UI with zero learning cost and no external documentation.
content_type: markdown
category: custom
tags:
- external
- imported
- markdown
author: unknown
source: external_agent
status: approved
enabled: true
description_zh: 进行界面设计或前端 UI 开发时使用。将功能说明与帮助信息直接集成到可视化界面中，通过视觉层次、图标提示、状态反馈和上下文帮助，实现零学习成本的自解释用户界面。
---
```

- `description` = **英文**（H-1：改中文会掉触发句式覆盖，见 §2.6）；这 5 条原本只有中文描述，
  故英文为**新撰**：忠实转写中文原意 + 补 `Use when …` 触发句式（与 pd-* 同族）。
- `description_zh` = **现有主轨文案逐字**（H-2 口径，未改一字）。
- `status` / `author` / `source` / `tags` / `category` / `content_type` = **主轨取值逐字**
  （其中 2 条 status=`published`；`callability.py:1095` 的判据接受 `approved/published/空`）。
- 未写 `version` / `default_params`：与那 15 条同款（不引入语料里少见/独有键）。

### 2.3 逐条证据表（全部经生产入口取数）

| id | sha256(前 8) | `description_zh`==主轨逐字 | 正文==主轨 content | 合并视图==skill.md | 主轨 desc==文件轨 `description_zh` | 在索引 | TF-IDF 命中 | 清单 desc==skill.md |
|---|---|---|---|---|---|---|---|---|
| testing-anti-patterns | `9b90b8a3` | True | True | True | True | True | True | True |
| code-observability | `1957fede` | True | True | True | True | True | True | True |
| engineering-test-delivery | `c58a1a70` | True | True | True | True | True | True | True |
| frontend-state-sync | `d3feb87c` | True | True | True | True | True | True | True |
| self-explanatory-ui | `12a89074` | True | True | True | True | True | True | True |

全仓扫（不是抽样）：

```text
全仓文件轨条数 = 28
合并视图 == 文件轨（全 28 条）: 28/28 OK
双轨 20 条满足 主轨 description == 文件轨 description_zh : OK
```

### 2.4 **"真的可召回"** —— 走检索链路的证据（本卡的验收核心）

```text
=== ① load_metadata_index()（生产检索的唯一数据源）===
   条数 = 28 | 5 条全部在内 = True
   基线对照：迁移前 = 23；G1-A R9 记录这 7 条结构上不可召回

=== ② SkillLoader.list_all_metadata()（模型可见的技能目录）===
   条数 = 28 | 5 条全部在内 = True

=== ③ 倒排索引候选池（loader._get_inverted_index）===
   倒排索引 token 数 = 691
   testing-anti-patterns        倒排 token 数 = 56   | 样例 ['1','add','adding','anti','apis','asserting','behavior','changing']
   code-observability           倒排 token 数 = 48   | 样例 ['a','action','adding','analytics','and','apis','backend','boundaries']
   engineering-test-delivery    倒排 token 数 = 60   | 样例 ['across','an','and','assurance','audit','automated','boundary','code']
   frontend-state-sync          倒排 token 数 = 53   | 样例 ['abortcontroller','alignment','and','async','backend','cancellation','click','code']
   self-explanatory-ui          倒排 token 数 = 41   | 样例 ['and','contextual','cost','custom','design','development','directly','documentation']

=== ④ TF-IDF 检索（生产 Layer-1）：真的能被召回吗 ===
   testing-anti-patterns      q='testing anti-patterns mock behavior'              -> top5=['testing-anti-patterns','engineering-test-delivery','pd-brainstorming-697b717a-skill','pd-systematic-debugging-556faa20-skill']  HIT
   code-observability         q='structured logs health check backend api'          -> top5=['code-observability','engineering-test-delivery','frontend-state-sync','pd-finishing-a-development-branch-e085de5a-skill']  HIT
   engineering-test-delivery  q='audit report automated test suite delivery'        -> top5=['engineering-test-delivery','pd-test-driven-development-8562c8ad-skill','testing-anti-patterns','pd-systematic-debugging-556faa20-skill']  HIT
   frontend-state-sync        q='abortcontroller race condition optimistic update'  -> top5=['frontend-state-sync']  HIT
   self-explanatory-ui        q='self explanatory interface visual hierarchy'       -> top5=['self-explanatory-ui','engineering-test-delivery']  HIT
   5/5 HIT = True
   MatchResult.total_scanned = 28 | 候选池大小 = 索引条数 28

=== ⑥ CapabilityRegistry 信封 + 清单 ===
   信封 items=119 skills=28 tools=91
   信封 skill 描述非空 = 28/28
   5 条在信封里 = {全部 True}
   build_manifest() skill 条目 = 28
   5 条清单描述 == skill.md = 5/5

=== ⑦ runtime_only 标注（迁移后应从「运行时独有」里消失）===
   runtime-only 剩余 = ['global-core-principles', 'skill']
   5 条仍在 runtime-only 里 = []
```

**⑦ 这一行同时是 H-3 另一半的证据**：`runtime_only` 集合从 7 条精确收缩到
`['global-core-principles', 'skill']` —— 正好是裁定"不纳入"的那 2 条。

**反空转断言**（`test_skill_h3_migration.py::test_recall_requires_the_file_entity`）：
把文件轨索引里的这 5 条裁掉后，倒排候选池里**一条也命中不到** ⇒ 上面那条召回结论
确实由"这 5 个 skill.md 存在"产生，而不是别处注入的 token。

### 2.5 没有制造新的双描述冲突

"双描述冲突"在本仓有**精确定义**（G1-B 建立）：同一 id 同时有主轨 `description` 与
文件轨 `description`，且两者**都是活消费者读的那一份**。G1-B 的收敛口径是：

```text
description（唯一权威）= skill.md front matter 的 description（英文：检索 + 模型可见）
description_zh（展示）  = skill.md front matter 的 description_zh（中文：UI 读它）
主轨 description        = 降级为历史副本，读路径不再取它
```

迁移后实测的两条不变量（全仓，非抽样）：

1. `合并视图 description == skill.md description` —— **28/28 OK**；
2. `主轨 description == 文件轨 description_zh` —— 双轨 **20 条**（15 条 pd-* + 本卡 5 条）
   **全部满足**。第 2 条是"**没有引入第三种冲突形态**"的判据：若我为这 5 条另写一份与主轨
   不同的中文，它会立刻变红。两条断言都已固化进 `test_skill_h3_migration.py`。

**诚实登记的边界**：主轨仍保留 22 条 `description`（含本卡这 5 条的中文），
它们与文件轨 `description`（英文）**不同** —— 这正是 G1-B 已登记的残留 R-c
（"主轨 description 仍存在，只是读不再用它"），本卡**刻意未清空**：理由见 §7-D3。

### 2.6 触发句式覆盖（G1-A §11.6 脚本 F **判据一字未改**重跑）

```text
现状(全用 skill.md):   21/28 = 75.0%   [G1-A 基线 17/23 = 73.9%]
若 5 条也用中文(主轨): 16/28 = 57.1%
条数 28（G1-A 时 23）

无触发句式的 skill.md:
    context_aware | 持续追踪对话主题、用户意图与时间线的演变…
    emotion_expression | 在对话中表达情感色彩，让回应更生动
    engineering-test-delivery | Use throughout the full life cycle of code development and generation to enforce…
    pd-brainstorming-697b717a-skill | You MUST use this before any creative work - …
    proactive_suggestion | 在用户未明确提问时，基于对话上下文识别潜在需求…
    safety_guard | 在生成回应与执行工具调用前检测潜在风险内容…
    scripted-selftest | 三层架构示例技能，演示 skill.md 元数据 + …
```

**三条读法**：

1. 覆盖率 **73.9% → 75.0%**（不降反升），H-1"留英文"的实测约束**没有被本次迁移侵蚀**；
2. 若这 5 条改用中文主轨文案，覆盖率会掉到 **57.1%**（低于基线）—— 反证英文 `description` 的选择正确；
3. `engineering-test-delivery` 虽以 `Use throughout` 开头，但**不在脚本 F 的正则表里**（`Use when` 在，
   `Use throughout` 不在）；它的中文备选同样不含触发句式 ⇒ **没有"本可保住却丢了"的情形**。
   我**没有**为了凑这个指标去改它的文案（那是为指标改文案，不是为召回改文案）。

---

## 3. 第 2 步：`skill`（易之三义）的 description 数据质量修复

### 3.1 改前（逐字）

```text
'1. 编码前必输出 <san_yi_analysis>: [不易]约束识别 → [变易]扩展性评估 → [简易]最简方案确认。'
'2. 原子推理，每步经三义校验。'
'3. 三义冲突时显式说明权衡取舍。'
'4. 生成后自检，违三义则修正再输出。'
长度 = 120 | 以编号步骤开头 = True | 含 <san_yi_analysis> = True
```

⇒ **编号步骤 + 祈使句 + 提示词片段**，是指令内容，不是描述。

### 3.2 改后（新文案）

```text
以《易经》三义（不易 / 变易 / 简易）为框架的编码行为准则：先锁定业务内核、接口契约与安全边界等不变量，
再评估可演进范围，最后收敛到最小充分解。适用于需要「改动最小、边界清晰、可回滚」的编码与重构任务，
也适用于需求模糊时按「不变的是什么 → 可能变的是什么 → 最简起步方案」逐步澄清的场景。
长度 = 148 | 以编号步骤开头 = False | 含 <san_yi_analysis> = False
```

### 3.3 依据（逐条可核）

| # | 依据 | 来源 |
|---|---|---|
| 1 | 该技能的 `name` 是 **易之三义**，`content` 是 `# Role: Yi-Jing Coding Agent`（Core Philosophy = 不易/变易/简易；Cognitive Protocol 的第 1 条**正是**旧文案那 4 步） | `data/skills_mgmt.json` 的 `skill` 记录 |
| 2 | 描述必须回答"**是什么 + 什么时候用**"，而旧文案两个都没答 | 与本仓主轨其它描述的形态一致（如 `self-explanatory-ui`："进行界面设计…时使用。将…"） |
| 3 | 旧文案直接进入任何路由/展示都会把**指令**当**描述**用（H-3 的原话） | `G1_DECISIONS.md` 决策 3 补充 |
| 4 | 新文案不含编号步骤、不含提示词标签、不重复正文的 4 步 | §3.1/§3.2 的量化对照 |
| 5 | 与技能的实际用法对齐：`content` 的 Hard Constraints 就是"不变量优先/最小变更/可读性优先"，新文案写的是同一件事的**描述语气** | `skill.content` |

### 3.4 改动的最小性自证

```text
### 原文行尾：CRLF = 1939 | lone LF = 0
### 最小侵入自证：只有 description 一个值变，其余字节逐字节相同 = True
### sha256 / len / mtime
   before: 9b5b0b786d65338b 188312 2026-09-25 18:43:31
   after : bcda9ecfbcf105b6 188467 2026-09-26 07:46:05

### 行级 unified diff（全文，不含上下文）:
    --- before
    +++ after
    @@ -1857 +1857 @@
    -    "description": "1. 编码前必输出 …，违三义则修正再输出。",
    +    "description": "以《易经》三义（不易 / 变易 / 简易）为框架的编码行为准则：…逐步澄清的场景。",
   总行数 before/after = 1940 / 1940 | diff 行数 = 5
```

**两条必须说清的处置**：

- **走的是"数据修复"而不是写路径**：M0 已把 `description` 移出 `SkillsMgmtService.update` 白名单、
  两条 overlay 路由也已冻结 ⇒ 无官方写入口。**我实测过官方 `SkillStore` 通路并放弃**：
  `model_dump()` 往返会让 **16 条**记录的 `review` 字段发生变化（不是无损），
  用它写会把 16 条无关记录一起改掉。故采用"读 → 只改一个值 → 按原格式（`ensure_ascii=False,
  indent=2` + **CRLF**）写回"，并用"换回旧串后与原文逐字节相同"自证最小侵入。
- **未更新 `updated_at`**（仍是 `2026-09-10T10:53:38.669022`）—— 见 §7-D3，刻意的最小 diff。

---

## 4. 第 3 步：`global-core-principles` 的消费点调查 + 处置建议（**未改 prompt 装配**）

### 4.1 它现在在哪里被消费（实测，9 条路径逐条给结论）

| # | 可能的消费点 | 实测结果 | 证据 |
|---|---|---|---|
| ① | 文件轨元数据索引（生产检索唯一数据源） | **不在** | `load_metadata_index()` 28 条，`含 gcp = False` |
| ② | `SkillLoader.list_all_metadata()`（模型可见技能目录） | **不在** | 28 条，`含 gcp = False` |
| ③ | `loader.match()`（生产 Layer-1 检索） | **召回不到** | `q='核心行为准则 边界保护 自主工作'` → `['pd-writing-skills-5da20e67-skill']`；`q='global core principles'` → `[]`；`q='真实透明 隐私优先'` → `[]` |
| ④ | `registry.as_legacy_rows()`（管理页 / legacy 快照 / `skills_installer`） | **在**（主轨分支） | `{'id':…, 'name':…, 'enabled': True, 'description': '资深软件工程专家的核心行为准则…', 'description_zh': '', 'params': {}}` |
| ⑤ | **它的准则正文（`content`，1421 字符）有没有读者** | **没有任何读者** | 主轨可读 `content` 长度 1421；`ContextInjector` 源码里 `.content` 出现 **0 次**（它走 `loader`/`load_instruction`，即文件轨）；全仓 grep 其正文特征句（`核心真实原则`/`人类至上`/`边界保护原则`/`资源节制`）在 prompt 装配代码里 **0 命中** |
| ⑥ | persona 常驻段 `_SKILL_PROMPTS` | **不在** | keys = 7 个（self_reflection / memory_summary / emotion_expression / proactive_suggestion / context_aware / safety_guard / voice_interaction），无 gcp |
| ⑦ | `data/skill_callability.yaml` 的显式声明 | **在**，且**明确说它"不属能力面"** | `permission_level: restricted`，reason = "指令型基础行为准则（is_sensitive=true，内容内联在 data/skills_mgmt.json）：对所有会话生效，但**不属**『模型可发起调用』的能力面" |
| ⑧ | `data/capability_manifest.json` | 只在 `runtime_only_declarations`（**不是** `entries`） | `entries` 里 0 条；`runtime_only_declarations` 含它 ⇒ 界面按"无徽章"静默退化 |
| ⑨ | `data/agent_lines/*.yaml` 的 `skills:` 列表 | **6 条主线都写了它，但字段无消费者** | engineering / digital_life / recon / knowledge / assistant / harness 各有 `- global-core-principles`；`LineProfile.skills` 只被 `agent/lines/models.py:802 to_dict()` 与 `:838` 解析，`assembler.py` 只装配**工具** |

### 4.2 结论

1. **"不纳入检索"这条裁定，执行成本为零**：它本来就不在检索里（①②③ 全不可达），
   本卡**不需要也不应该**为它做任何事。
2. 但性质要说准：它现在的状态**不是"放错了地方"，而是"没有地方"**。
   它的产品意图是"作为基础行为底线**始终生效**"，而实测它**唯一的落地形态是一行元数据**
   （名称 + 中文描述 + 启用位，供管理页列表显示）；**那 1421 字的准则正文没有任何执行路径**。
   ⇒ 这是一份**死数据**，与"常驻行为准则"之间隔着一次**接线**（属 prompt 装配卡）。
3. `data/skill_callability.yaml` 的声明是本仓**唯一**对它性质做了显式表达的权威数据，
   且它已经把结论写死了："对所有会话生效，但**不属**'模型可发起调用'的能力面"。

### 4.3 处置建议（**本卡只给建议，不动 prompt 装配**）

| 优先级 | 建议 | 理由 | 归属 |
|---|---|---|---|
| **P0** | **保住"不纳入"这个结论，别为它建 skill.md** | 建了实体会让它进清单（119→120）与检索，**反而违背 H-3** | 本卡已守住 |
| **P1** | **给它一个真正的常驻位**：由 prompt 装配卡在 system prompt 的常驻段引用它的 `content`（或把 `content` 迁到 `data/prompts/` 作为唯一源，台账只留元数据） | 这是它**唯一**能兑现"始终生效"的路径；当前是无处安放 | **prompt 装配卡**（本卡不碰） |
| **P2** | **在 `agent_lines` 里定义"常驻技能"角色**，让 `skills:` 列表有语义；或删掉这个字段 | 6 条主线都声明了它却无人消费 —— 与 R9 同族的"声明了没接线" | agent_lines / 主线卡 |
| **P3** | 台账侧**显式标注不可检索**（如 `retrieval: excluded` 元数据键） | 现在只能靠 `skill_callability.yaml` 的一句 `reason` 传递这个语义，容易被后人再当成"漏迁的技能" | 后续卡 |
| **不建议** | 清空主轨 `description` / 停用 `enabled` | 会让管理页少一行、legacy 快照少一行，收益不明；且与 §7-D3 的口径冲突 | — |

---

## 5. 第 4 步（G1-B 残留 R-d）：管理页"搜索"仍只看主轨 description

### 5.1 调查：搜索走哪个函数、读哪个存储

```text
展示链：  yunshu-ui → GET /api/skills → app_server.py:1008 → registry.as_legacy_rows()
          → description = 文件轨优先（+ 逃生开关）；description_zh = 文件轨
          → yunshu-ui/src/pages/hub/memory/skills.tsx 渲染 description_zh || description

搜索链：  yunshu-ui/src/lib/skillsApi.ts:253 → GET /api/skills-mgmt/search
          → agent/server_routes/routes_skills_mgmt.py:152 → SkillsMgmtService.search()
          → agent/skills_mgmt/service.py:1604   SkillSearcher.search(self.store.list_all(), params)
                                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^ 只喂**主轨 Skill 模型**
          → agent/skills_mgmt/searcher.py _match_score → _tokenize(skill.description)  ← 主轨文案
```

**结论（修复前的三处不一致）**：

| # | 不一致 | 具体后果 |
|---|---|---|
| R-d-1 | 描述来源不同 | 搜索打分用**主轨**文案、展示用**文件轨**文案 ⇒ "看到的是新文案、搜到的按旧文案" |
| R-d-2 | **语料不同**（本卡新发现，见 §7-U2） | 展示 30 行（合并视图），搜索只吃 22 条主轨 ⇒ **8 条文件轨独有技能（persona 内置）在管理页搜不到**，但它们**显示在列表里** |
| R-d-3 | `description_zh` 完全没进打分 | `Skill` 模型（`models.py`）没有该字段 ⇒ 用户看着中文却搜不到它 |

### 5.2 修法落在 `agent/skills_mgmt/` 内 ⇒ **已直接修**

`agent/skills_mgmt/searcher.py`

```diff
-def _match_score(skill: Skill, query_tokens: List[str]) -> float:
+def _match_score(skill: Skill, query_tokens: List[str],
+                 file_desc: str = "", file_desc_zh: str = "") -> float:
     ...
     name_tokens = _tokenize(skill.name)
-    desc_tokens = _tokenize(skill.description)
+    desc_text = str(skill.description or "")
+    if file_desc and _desc_from_file_track():
+        desc_text = str(file_desc)          # 文件轨优先（与 as_legacy_rows 同口径）
+    if file_desc_zh:
+        desc_text = (desc_text + " " + str(file_desc_zh)).strip()
+    desc_tokens = _tokenize(desc_text)
```

```diff
-    def search(self, skills: List[Skill], params: SkillSearchParams) -> SkillSearchResult:
+    def search(self, skills: List[Skill], params: SkillSearchParams,
+               meta_index: Optional[Dict[str, Dict[str, Any]]] = None,
+               ) -> SkillSearchResult:
         ...
                 if query_tokens:
-                    score = _match_score(s, query_tokens)
+                    _fm = (meta_index or {}).get(s.id) or {}
+                    score = _match_score(s, query_tokens,
+                                         file_desc=str(_fm.get("description") or ""),
+                                         file_desc_zh=str(_fm.get("description_zh") or ""))
```

`agent/skills_mgmt/service.py`

```diff
     def search(self, params: SkillSearchParams) -> SkillSearchResult:
-        return self.searcher.search(self.store.list_all(), params)
+        try:
+            meta_index = self.file_store.load_metadata_index(refresh=False) or {}
+        except Exception as e:  # noqa: BLE001 文件轨读失败不得让搜索不可用
+            logger.warning("[Service] 搜索取文件轨描述失败，退化为仅主轨: %s", e)
+            meta_index = {}
+        return self.searcher.search(self.store.list_all(), params,
+                                    meta_index=meta_index)
```

**三条设计要点**：

1. **口径与展示完全一致**，含**同一个**逃生开关 `CP_SKILL_DESC_FROM_FILE_TRACK`
   （`searcher.py` 里是**本地副本**，与本包既有的 `_WORD_RE` 本地副本约定一致；
   有 `TestEscapeHatchParity` 逐值比对两个实现，防止"回滚只回滚一半"）。
   注意该开关已在 `agent/settings/registry.py:2490` 登记（**该文件本卡未动**）。
2. **一次索引、不在打分函数里逐条查文件系统** —— 正好满足 G1-B/M4b 登记的**解除条件②**。
   同时解除条件③（"别把废弃副本拉回打分"）也解除：中文取自**文件轨**，不是主轨。
   原延期理由① 仍然成立且不冲突：**本器仍不在生产检索链上**，本条修的是**管理页搜索**这条消费者。
3. **向后兼容**：`meta_index=None`（默认）⇒ 行为与修复前**逐字相同**
   （`test_backward_compatible_when_no_index` 覆盖）。

### 5.3 R-d 的证据（`tests/unit/test_skill_search_description_source.py`，**17 passed**）

| 断言 | 守什么 |
|---|---|
| `test_file_track_english_is_searchable` | 文件轨 `description` 里的词能搜到 |
| `test_description_zh_is_searchable` | **界面真正显示的中文**能搜到 |
| `test_stale_main_track_copy_is_no_longer_matched` | **最锋利的分界**：主轨历史副本不再命中（修复前它命中、文件轨文案不命中） |
| `test_backward_compatible_when_no_index` | 不给索引 ⇒ 逐字退回旧行为 |
| `test_escape_hatch_zero_restores_main_track_english` | 开关=0 时搜索与展示**一起**回滚（镜像断言对象是 `as_legacy_rows`，不是"我期望"） |
| `test_no_file_entity_keeps_main_track` | 无 skill.md 实体的技能（如 `global-core-principles`）行为不变 |
| `TestRealRepoSearchMatchesDisplay` | 生产数据面：5 条新迁移技能按文件轨文案可搜；且**换掉入参索引命中集必变**（反空转） |
| `TestEscapeHatchParity` | searcher 与 registry 的开关语义**逐值一致**（9 组取值） |

---

## 6. 技能总数 23 → 28 打破了什么 / 怎么处理

### 6.1 打破面（**比任务卡预计的多**，如实列全）

| # | 被打断的东西 | 表现 | 处理 |
|---|---|---|---|
| 1 | G1-B 守卫的 `KNOWN_MAIN_TRACK_ONLY` | `test_main_track_only_allowlist_is_exact` 红（"消失 5 条"） | ✅ 7 → 2（**只改这一处**，见 §6.2） |
| 2 | legacy 快照与合并视图 | `TestG7LegacySnapshot::test_real_snapshot_matches_merged_view` 红 | ✅ 重建 `store.sync_to_legacy_skills_json()`（30 行、幂等、双份字节镜像） |
| 3 | descriptor 台账 | `test_descriptor_registry_description_equals_skill_md` 红（5 条描述=主轨中文） | ✅ `update_fields({"capability":{"description":…}})` ×5 回填取文件轨 |
| 4 | `compare_skills_legacy_vs_repo.py --verify` | `[SET] 仅在旧格式` 含这 5 条 → 结论 `HAS_DIFF` | ✅ 缩小为 `['global-core-principles','skill']`（**逐行字段 DIFF 仍为 0**） |
| 5 | `sync_capability_manifest.py --check`（**CI 在跑**：`skill-description-single-source.yml:118`） | 实测 **退出 1**：`+ 新增 code-observability/engineering-test-delivery/frontend-state-sync/self-explanatory-ui/testing-anti-patterns`、`runtime_only_entities 有变化`、`counts 有变化` | ✅ 重跑 sync：**119 条能力**，`--check` **退出 0** |
| 6 | **能力总数基线锁（14 处 / 4 个测试文件）** | `test_capability_spec.py`(2) / `test_capregistry_core.py`(8) / `test_capregistry_callpaths_routes.py`(4) / `test_confirm_level.py`(1) **全红** | ✅ 见 §6.3 |

> **第 6 项是任务卡未预计的**：任务卡只说"确认 G1-B 留下的守卫测试与 baseline 是否变红"。
> 实际还打断了一族"能力面基线快照"测试（锁 114 / 23 / local 93）。**必须明说。**

### 6.2 守卫测试的改动（**只改 1 处**，`tests/unit/test_skill_description_single_source.py`）

```diff
-#: 主轨独有、**本轮刻意不纳入事实源域**的 7 个 id（G1-B0 §4.5 H-3 的裁定）。
-#: ...
-KNOWN_MAIN_TRACK_ONLY = frozenset({
-    "code-observability", "engineering-test-delivery", "frontend-state-sync",
-    "global-core-principles", "self-explanatory-ui", "skill", "testing-anti-patterns",
-})
+#: ...（详细记录 7→2 的来源：哪 5 条已迁移、哪 2 条刻意留下、为什么）
+KNOWN_MAIN_TRACK_ONLY = frozenset({
+    "global-core-principles",
+    "skill",
+})
```

- **原意图如何保留**：断言文本一字未改 —— 仍是 `assert actual == set(KNOWN_MAIN_TRACK_ONLY)`
  （"多了少了都红"）。该 allowlist 的原始设计注释**已经预告了**这一步：
  「若将来为它们补 skill.md（后续卡 G1-C），本 allowlist 必须**同步缩小**」。
- **没有放宽**：集合从 7 缩到 2 是**收缩**（更严），不是把断言删掉或改成 `<=`。
- 其余 31 条断言**一字未动**，`32 passed`（= G1-B 基线）。

### 6.3 能力面基线锁的更新（14 处 / 4 个文件，**逐条**）

| 文件 | 行（改后） | 旧 → 新 | 原意图（保留不变） |
|---|---|---|---|
| `tests/unit/test_capability_spec.py` | 7（docstring）、160（**测试名**）、161、184 | `114` → `119`；`test_114_条全部有_location` → `test_119_条全部有_location` | "全部 N 条能力都有 `location`/`location_source`" + "分布统计与逐条计数一致" |
| `tests/unit/test_capregistry_core.py` | 142、143、144、156、169、213、224、277 | `total 114→119`；`by_kind {"skill":23,"tool":91} → {"skill":28,"tool":91}`；`by_location {"local":93,…} → {"local":98,…}`；`len(skills) 23→28`；`idx["primary"] 114→119`；`env["data"]["total"] 114→119` ×3 | "真实 registry 的规模/分布与清单一致""主键索引确实被构建""不支持 tool calling ⇒ 裁剪的是清单不是总数" |
| `tests/unit/test_capregistry_callpaths_routes.py` | 551、552、569、598 | `114` → `119` | "`GET /capabilities/tools` 返回全量不静默截断""`/capabilities/health` 报告注册表总数" |
| `tests/unit/test_confirm_level.py` | 16、293、340（**测试名**）、341、361、379 | `114→119`；`23→28`；`test_技能侧_23_条_…` → `test_技能侧_28_条_…` | "技能侧 `permission_level` 恒为 public/restricted（工具侧概念不外溢）""全量派生一致性对拍" |

- **每处都只改数字**，断言形式（精确等于）**未动**；共 4 处 docstring/注释/测试名同步更新以免文档说谎。
  实测复核：28 条技能在清单里**全部** `permission_level=public`、`effect=read`、`risk=low`，
  故 `assert all(got == "public")` 这条**语义断言仍然成立**，无需改写。
- ⚠️ **越界声明**：这 4 个文件**不在任务卡给出的文件范围**内。纳入的理由是任务卡的两条硬要求
  ——「不要留红灯」与「④技能总数 23→28 打破了什么、你怎么处理」。改动是**纯数字刷新**，
  已逐条列出便于 reviewer 复核或回退（§8）。
- **故意不动**的相关文件：`test_capregistry_capacity_warning.py`（用**合成** `_specs(114)`，
  行为不受影响，仅 :59 的 docstring"真实 114 条"表述变陈旧 → 见 §7-R4）、
  `test_toolset_hash.py`（`>= 114` 本就容忍增长）、`scripts/*` 与 `docs/*` 里的 114 是叙述性文字。

### 6.4 迁移的其它数据面副作用（重建后实测）

```text
legacy 快照：重建行数 n1/n2 = 30 / 30 | 幂等 = True | data/skills.json == agent/data/skills.json 逐字节 = True
descriptor：updated 5 条；复核 descriptor == skill.md : 5/5 OK
清单 counts（新）：total 119 / by_type {"tool":91,"skill":28} / by_location {"local":98,"remote":21}
                  runtime_only_declarations = ['global-core-principles', 'wf-f19dc52c-skill']
信封（live）：items=119 skills=28 tools=91；skill 描述非空 28/28
```

---

## 7. 偏差、未验证项与残留风险（**不粉饰**）

### 7.1 偏差

| # | 偏差 | 说明 |
|---|---|---|
| **D1** | **任务卡关于 F1b-C-② 的前提不准确**（"Windows 上仍写 CRLF（**与既有文件不一致**）"） | 实测：既有 23 个 skill.md 在**工作区里全是 CRLF**（0 个 LF-only）。真正的不一致在别处：`core.autocrlf=true`（system gitconfig），HEAD 里的 **blob 是 LF**（实测 `pd-test-driven-development` 的 blob 2374 B / CRLF=0），工作区的 CRLF 只是 checkout 产物。⇒ 真正的缺陷是**行尾由平台决定（不确定）**，而 `.index/cache.json` 存的正是**原始字节 md5**，平台间不通用。我据此把 create() 定为**写 LF**（= 仓库里 skill.md 的 blob 形态），并**声明副作用**：Windows 工作区上新文件是 LF、既有 23 个是 CRLF（`git add` 时 autocrlf 会把两者都归一成 LF，故**仓库层面零差异**）。 |
| **D2** | **`create()` 现在是"调用方给什么就写什么"**（白名单外键也落盘） | 这是**行为变更**（旧行为是静默丢弃）。风险：若将来有调用方把整份记录（含 `content`/`versions`）喂给 create，front matter 会变大。已核实**当前 3 个调用方都不这么干**：`skill_manager.install_from_dir` 先过 `SkillMDParser.parse()`（已白名单过滤）、`install_from_zip` 先过 `_MANIFEST_FIELDS`、`process_distill.solidify` 传显式构造的小字典；测试调用方也是小字典。并加了 `create.meta_outside_whitelist` 的 INFO 日志让"保留了哪些白名单外键"可观测。 |
| **D3** | `skill` 记录**只改了 `description`，未更新 `updated_at`** | 刻意的**最小 diff**（`diff 行数 = 5`）。理由：M0 已冻结主轨描述写路径，不存在会产生 `updated_at` 的官方写入；手动 bump 一个时间戳会伪造"一次并不存在的写入路径"。代价：该记录的 `updated_at` 不再反映描述变更 —— **建议后续卡**把这类"冻结期数据修复"纳入统一的变更留痕口径。 |
| **D4** | **改了 4 个不在文件范围内的测试文件**（14 处数字） | 见 §6.3。理由是「不留红灯」+ 任务卡第 ④ 问。可逐条回退（§8）。 |
| **D5** | 重跑 `sync_capability_manifest.py` 顺带改了 `docs/rfc/云枢能力清单盘点表.md` | 该文件是**同一脚本的同期产物**（G1-B 的 M5 也如此）。它不在 `data/` 下、也不在任务卡声明范围内，但**不重跑就会 `--check` 红灯**（CI 在跑）。 |
| **D6** | 重建/回填了 3 个"派生数据"文件（`data/skills.json`、`agent/data/skills.json`、`data/descriptors.json`） | 同样不在声明范围。**不重建则 G1-B 守卫测试红**（G-7 快照一致性 / descriptor 描述一致性）。三者都是**可重建派生物**。 |
| **D7** | `searcher.py` 里 `_desc_from_file_track()` 是 `registry.py` 同名函数的**本地副本** | 沿用本包既有约定（`_WORD_RE` 在 `loader.py`/`bm25_searcher.py`/`searcher.py` 三处同款本地副本），避免新增跨模块依赖；用 `TestEscapeHatchParity` 逐值比对两者，防止语义漂移。 |
| **D8** | 用 `SkillFileStore.create()` 建目录 ⇒ 每个新目录多出**空的** `scripts/` 与 `temp/` | 既有 22 个技能目录**没有**这两个子目录（只有 `scripted-selftest` 有 `scripts/`）。空目录**不被 git 跟踪**，故仓库层面无差异；这只是 create() 的固有契约（"创建技能目录结构"）。 |
| **D9** | **并发工作区**：执行期间其他卡持续改动共享文件 | 实测同一时刻有 3 个非本卡的 python 进程在跑；`agent/skills_mgmt/` 下 `enhancer/executor/index_cache/loader/registry/store/vector_adapter` 都是**别人**的未提交改动（本卡只动 `file_store/searcher/service`）；`data/audit/daily_roots.jsonl` 的 mtime 是 **2026-09-26 08:00:00**（非本卡行为，见 §7-R1）。`HEAD` 全程未变。 |
| **D10** | 我**没有**为 `engineering-test-delivery` 改文案去凑触发句式指标 | 它以 `Use throughout` 开头，不在脚本 F 的正则表里。为指标改文案是本末倒置 —— 如实记录它"无触发句式"，并把 5 条的覆盖率一并报出（仍高于基线）。 |

### 7.2 **未验证项**

| # | 未验证 | 为什么 | 影响 |
|---|---|---|---|
| **U1** | **中文 query 在生产 Layer-1 上召回弱** | `loader._meta_to_meta_text()` 只拼 `name/description/tags/category`，而 `description` 是**英文** ⇒ 实测 `q='测试反模式'`/`'可观测性 日志'`/`'状态同步 竞态'`/`'自解释界面'` **4/5 返回 `[]`**（`'工程化测试交付'` 命中，走的是 tags 里的 `代码与工程`） | **不是本卡引入的回归**：迁移前这 5 条**根本不在索引里**（任何 query 都召回不到）。但它意味着"5 条真正可召回"目前**只对英文 query 成立**。修法一行（`_meta_to_meta_text` 并入 `description_zh`），我**没做**：`loader.py` 不在任务卡文件范围、`tool-retrieval-ci.yml` 专门盯着该文件、且会给全部 28 条（含 15 条 pd-*）改变打分与阈值行为。**建议单开卡**并跑一次检索回归。 |
| **U2** | R-d-2（搜索语料 ⊂ 展示语料）**未修** | 修它需要为 8 条文件轨独有技能构造 `Skill` 对象（pydantic 模型、默认值、`updated_at`/`metrics`…），属**行为变更**而非"描述来源"修复 | 现状：管理页列表 30 行，搜索只能覆盖 22 条。**本卡只登记 + 给修法**（`service.search()` 里把文件轨独有的 id 也用最小 `Skill` 视图补进语料，或给它们建主轨占位记录）。 |
| **U3** | **完整 `tests/unit` 全量套件未跑完** | 第一次带 `-x` 中途作废；第二次跑 **>50 分钟、RSS 5.1 GB**，为不与同时运行的两张卡抢资源（其中一张正在跑 embedding worker）**主动终止** | 替代证据：**全部相关**测试文件（33 个）**978 passed / 0 failed**。未覆盖的是与技能/能力面无关的模块。 |
| **U4** | UI 端到端未点 | 任务卡禁止启动常驻服务；`yunshu-ui` 不在文件范围 | 替代证据：`GET /api/skills-mgmt/search` 的**进程内**等价入口（`SkillsMgmtService.search`）已实测；展示侧取数口（`as_legacy_rows`）逐条比对通过。 |
| **U5** | 向量索引未重编码 | 离线窗口无模型（G1-B 已记录 18 分钟挂起） | 5 条新技能**不在**那 8 条向量化的 persona 技能里；`_vector_text_and_hash` 不受本次改动影响（G1-B 的 V-guard 仍绿）。 |
| **U6** | `data/skills_repo/.migration/descriptions.baseline.json` **未被 git 跟踪** | 不是我能修的（禁止 `git add`） | ⚠️ **G1-B 遗留的 CI 隐患**：它 untracked 且未被 ignore（`git check-ignore` rc=1、`git ls-files` 空），而 `skill-description-single-source.yml:68` 会跑守卫测试，其 `baseline` fixture 在基线缺失时 `pytest.fail` ⇒ **干净 checkout 里 CI 会红**。G1-B 当时只在本机（有该文件）验证过。**需 G1-B owner 或 CI owner 处置。** |

### 7.3 残留风险

| # | 风险 | 触发信号 | 归属 / 预案 |
|---|---|---|---|
| **R-1** | **审计链日根（G3）现在 FAIL=9**（G1-B 收尾时是 FAIL=0 WARN=2） | `python scripts/audit_governance_check.py` → `FAIL（FAIL=9 WARN=2）`；全部是 Merkle 日根：`2026-09-13/14/16/17/18/19/22/23/25 重放失败`（含 7 条 `root_chain_broken`、`2026-09-25` 的 `root_hash_mismatch`）、缺根 7 天 | **不由本卡产生**（证据见下）。**但必须有人处置** —— checker 自己写了"封印路径正在产出错误日根"。 |
| **R-2** | 另外两张卡的未提交改动被本次 sync 一并派生进 `capability_manifest.json` / 盘点表 | `--check` 当前绿（口径自洽），但 diff 归因不完全 | 同 G1-B 的 D-10。reviewer 若需纯 G1-C diff，可先 `git stash` 其他卡改动再重跑 sync 对照。 |
| **R-3** | 主轨 `description` 仍存 22 条（R-c 残留），本卡新增 5 条也带上了 | 主轨中文 != 文件轨英文 | 本卡刻意不做（见 D3）；建议与 R-c 一并在"清空或转只读镜像"的专卡里处理。 |
| **R-4** | `test_capregistry_capacity_warning.py:59` 的 docstring 仍写"真实 114 条（TASK-00 §五基线）" | 只读文档陈旧；该测试用合成数据，行为不受影响 | 本卡**未改该文件**（未在其上做任何行为改动）；留待能力基线 owner 更新。 |
| **R-5** | `engineering-test-delivery` 的 `is_sensitive: true` 只在主轨 | 文件轨 front matter 无该键（与既有 23 条一致，0/23 有）；`callability._skill_sources` 只在 `include_runtime_catalog=True` 时读主轨的值 | 已提交的清单口径（默认不读主轨）**不受影响**；但"技能敏感标记在文件轨里的持久化"仍是空白（`_META_FIELDS` 专门为它留了键却无人在文件里写）。**建议登记。** |

**R-1 的归因证据（说明为什么不是本卡）**：

1. 本卡**没有任何代码路径**写日根。本卡实际执行过的写操作只有 5 类：
   `SkillFileStore.create/update_meta`、`scripts/sync_capability_manifest.py`（写清单 + 盘点表）、
   `DescriptorRegistry.update_fields`（写 descriptors + 入链）、`SkillStore.sync_to_legacy_skills_json()`（写两份 legacy 快照）、
   一次性的 JSON 字段修复。**没有一类触及 `daily_roots.jsonl` 或 Merkle 日根。**
2. `data/audit/daily_roots.jsonl` 的 mtime = **2026-09-26 08:00:00**（整点），而本卡的审计写入发生在 **07:47:14**
   （`audit_chain.db` 的 mtime 至今仍是 07:47:14，说明**之后没有新链记录**）。
3. 该文件里 **`2026-09-25` 有 3 条 competing 记录**（leaves=4 / 2 / 4，`prev_entry_hash` 都是 `65c70ab7b528`）
   —— 这是"**反复重封同一天**"的形态，而 checker 的结论正是"封印路径正在产出错误日根"。
   同一天多条根是**只追加日志 + 重封**的正常语义，但重封后 2026-09-23 之后的 `prev_entry_hash` 链就断了。
4. 仓里存在**另一张卡正在做的** `scripts/audit_reseal_daily_root.py`（mtime 09-25 22:08）与
   `tests/unit/test_daily_root_reseal.py`（**未跟踪**，即新文件）。该测试自述
   "全部用例只吃 `tmp_path` 假库（**不读、不写生产 data/audit/**）"，我逐一核对了它的 `subprocess` 调用
   （`--db`/`--roots`/`--key-path` 全指向 tmp）与 `script.main([...])` 调用 ⇒ **它是隔离的**。
   ⇒ 生产根里那 3 条 2026-09-25 记录**不是**测试写的，而是有人**手动跑了那个重封脚本**。
5. 失败日（2026-09-13 … 09-25）**全部早于本卡**；缺根名单里还有 **2027-10-19/20**（未来日期 ⇒ 合成数据/夹具）。
6. **诚实的边界**：我**不能** 100% 证明 08:00:00 那次重封与我无关（我的一个测试进程当时在跑）。
   但：(a) 测试路径经 `tests/conftest.py:261/342/382` 重定向到隔离目录；
   (b) 重封脚本是别人的交付物；(c) 我那个 5.1 GB 的全量扫描在 **08:12 之后**才启动，
   **晚于** 08:00:00 的那次写入。⇒ **判定为"不由本卡产生"，但不声称已完全闭合归因。**

**本卡对审计链的贡献（精确）**：

```text
current max_seq = 72700 | G1-B 报告收尾时 max_seq = 72683 ⇒ 之后共 17 条
   seq > 72683 的尾部记录（其他卡 + 本卡混在一起）= 17
      by action: {'trace.closed': 6, 'ui.chat.api_chat.post': 6, 'descriptor.patch': 5}
   其中 **payload 带 'G1-C/H-3' 理由**的记录 = 5（seq 72696..72700，全部 descriptor.patch）
        seq=72696 subject=capability:cp.skill.code-observability
        seq=72697 subject=capability:cp.skill.engineering-test-delivery
        seq=72698 subject=capability:cp.skill.frontend-state-sync
        seq=72699 subject=capability:cp.skill.self-explanatory-ui
        seq=72700 subject=capability:cp.skill.testing-anti-patterns
### 链完整性: count/min/max = (72700, 1, 72700) | dup seq = 0 | gaps = 0 | 1..N 连续 = True
```

⇒ **本卡追加 5 条**（descriptor 描述回填）；另外 12 条（`trace.closed` ×6、`ui.chat.*` ×6）
属同一工作区的其他活动。`seq` **无缺口、无重复**。
`scripts/audit_governance_check.py` 的**只读自检**同时确认它没改动
`audit_chain.db` / `daily_roots.jsonl` / `ui_settings.json`（size + mtime_ns 运行前后一致）。

### 7.4 `data/` 下每个被改动文件的 sha256 前后对比

| 文件 | sha256 前 | sha256 后 | 长度变化 | mtime 后 |
|---|---|---|---|---|
| **`data/skills_mgmt.json`** | `9b5b0b786d65338b…` | `bcda9ecfbcf105b6…` | 188,312 → 188,467（+155） | 2026-09-26 07:46:05 |
| `data/skills.json` | `501adfa06ef995d4…` | `2273db676aa0e9d1…` | 14,658 → 16,677 | 2026-09-26 07:47:14 |
| `agent/data/skills.json`（镜像） | `501adfa06ef995d4…` | `2273db676aa0e9d1…` | 14,658 → 16,677（与上逐字节相同） | 2026-09-26 07:47:14 |
| `data/descriptors.json` | `259507ecde269408…` | `9718d9161e65fe63…` | 152,113 → 156,885 | 2026-09-26 07:47:14 |
| `data/capability_manifest.json` | `1ca83c926cdf81a5…` | `3d4f3b47d01819d0…` | 750,524 → 779,856 | 2026-09-26 07:46:39 |
| `data/skills_repo/.index/cache.json` | `0bee84830f41570e…` | `5875977e80b8f6bc…` | 38,022 → 43,280 | 2026-09-26 07:46:13 |
| `data/skills_descriptions_overlay.json` | `44136fa355b3678a…` | `44136fa355b3678a…` | 2 → 2（**未变**） | 2026-09-26 00:56:22 |
| `data/ui_settings.json` | `80c2893515bc13b8…` | `80c2893515bc13b8…` | 405 → 405（**未变**） | 2026-09-25 19:26:24 |
| `docs/rfc/云枢能力清单盘点表.md` | `af725fa1e4b5d070…` | `4576dedccb279e77…` | 65,408 → 67,410 | 2026-09-26 07:46:39 |
| `data/skills_repo/.migration/descriptions.baseline.json`（G1-B 产物） | `f0026d2337f34ec2…` | `f0026d2337f34ec2…` | 21,266（**本卡未触碰**） | 2026-09-26 00:51:33 |

**新增文件**（5 个 skill.md）：

| 文件 | sha256 | 字节 |
|---|---|---|
| `data/skills_repo/testing-anti-patterns/skill.md` | `9b90b8a3fc1acb2a0b5b61505f55620af9d816676301eada8eba833b67b91779` | 9,133 |
| `data/skills_repo/code-observability/skill.md` | `1957feded64d545a937c7ee70e4bde617af40450c3a53d41ebb2a8d954fba96b` | 1,986 |
| `data/skills_repo/engineering-test-delivery/skill.md` | `c58a1a705730a002b4c930aa95a2e97145f8e1faefef77d807e1743aec8b1aea` | 3,288 |
| `data/skills_repo/frontend-state-sync/skill.md` | `d3feb87c085a95369d9659653064d68f68a76da497dc73749d25318d4d20d36b` | 3,750 |
| `data/skills_repo/self-explanatory-ui/skill.md` | `12a89074393c2f7444f28a015e481a58ca9df005e5ab9a0f5eed385abd63c6e5` | 2,049 |

### 7.5 新增/改动的测试文件（本卡）

| 文件 | 状态 | 用量 |
|---|---|---|
| `tests/unit/test_skill_create_no_data_loss.py` | **新建** | F1b-C：`10 passed` |
| `tests/unit/test_skill_search_description_source.py` | **新建** | R-d：`17 passed` |
| `tests/unit/test_skill_h3_migration.py` | **新建** | H-3 验收（形状/逐字/唯一源/可召回/第 2 步文案）：`16 passed` |
| `tests/unit/test_skill_description_single_source.py` | **改动 1 处**（allowlist 7→2） | `32 passed`（= 基线） |
| `tests/unit/test_capability_spec.py` 等 4 个 | **改动 14 处数字** | 见 §6.3 |

---

## 8. 回滚指令（**定向，禁止整文件 `git checkout`**）

> 仓库里 30 张卡的未提交改动互相夹在一起：`agent/skills_mgmt/` 下 **8 个**文件是"已修改未提交"
> （本卡只占 `file_store.py` / `searcher.py` / `service.py` 里的自己那几段）。
> `git checkout <file>` 会把别人的成果一起回退。

### 8.1 ⚠️ **对任务卡"唯一例外"的一处更正**

任务卡说「`git checkout data/skills_repo/` 对本卡新建文件的回滚是安全的」。
**本卡的 5 个新文件是 untracked（`??`），`git checkout` 不会删除它们**；而
`data/skills_repo/` 下那 15 个 `pd-*` 是 **G1-B 的未提交成果（M2 写入的 `description_zh`）**，
`git checkout data/skills_repo/` 会**把 G1-B 的 15 条中文说明一起抹掉**。
⇒ **不要用那条命令回滚本卡。** 正确做法是删除 5 个新目录：

```powershell
# 回滚第 1 步（只删本卡新建的 5 个目录；pd-* 与 .migration 一律不动）
Remove-Item -Recurse -Force 'C:\Users\Administrator\agent\data\skills_repo\code-observability'
Remove-Item -Recurse -Force 'C:\Users\Administrator\agent\data\skills_repo\engineering-test-delivery'
Remove-Item -Recurse -Force 'C:\Users\Administrator\agent\data\skills_repo\frontend-state-sync'
Remove-Item -Recurse -Force 'C:\Users\Administrator\agent\data\skills_repo\self-explanatory-ui'
Remove-Item -Recurse -Force 'C:\Users\Administrator\agent\data\skills_repo\testing-anti-patterns'
```

### 8.2 逐步回滚

| 步 | 回滚方法 |
|---|---|
| **F1b-C** | 定向 revert `file_store.py` 两处：`serialize()` 的参数与 `filtered` 表达式回成无条件白名单；`create()` 回成 `serialize(meta, instruction)` + `write_text(md_content, encoding="utf-8")`（并删掉 `_outside` 日志）。**不要 `git checkout` 该文件**（它同时载着 F1b 的 +190/−8）。 |
| **第 1 步** | §8.1 的 5 条 `Remove-Item`；然后重跑 `python -c "from agent.skills_mgmt.file_store import SkillFileStore as S; S().load_metadata_index(refresh=True)"` 让索引回到 23。 |
| **第 2 步** | 把 `data/skills_mgmt.json` 的 `skill.description` 换回原指令串（§3.1 逐字），按同格式（`ensure_ascii=False, indent=2` + CRLF）写回；或直接恢复 sha `9b5b0b786d65338b…`（188,312 B）。 |
| **第 4 步 / R-d** | 定向 revert `searcher.py`（`_match_score` 签名与 `desc_text` 段、`search()` 的 `meta_index` 入参、调用点、`_ENV_DESC_FROM_FILE_TRACK`/`_desc_from_file_track`）与 `service.py` 的 `search()`。**不要 `git checkout` 这两个文件**。运行时也可**不改代码**回滚：置 `CP_SKILL_DESC_FROM_FILE_TRACK=0`（英文侧回主轨；中文侧仍取文件轨，故界面不陪葬）。 |
| **派生数据** | 三者都是可重建产物：`python -c "from agent.skills_mgmt.store import SkillStore; SkillStore().sync_to_legacy_skills_json()"`（legacy 双份）；`python scripts/sync_capability_manifest.py`（清单 + 盘点表）；descriptor 用 `DescriptorRegistry.update_fields` 回填或由 registry 重建。**注意：回滚第 1 步之后必须重跑这三个**，否则守卫测试会红。 |
| **守卫测试** | 定向 revert `KNOWN_MAIN_TRACK_ONLY` 回 7 条（本卡只在那一处改过）。 |
| **能力面基线锁** | 4 个文件、14 处数字：`119 → 114`、`28 → 23`、`local 98 → 93`（含 4 处测试名/docstring 同步回退）。逐条见 §6.3 表。 |
| **新增测试** | 删除 `test_skill_create_no_data_loss.py` / `test_skill_search_description_source.py` / `test_skill_h3_migration.py`。 |
| **一次到位（保守）** | ①§8.1 删 5 个目录；②按上表逐文件定向 revert 代码；③重跑 `sync_to_legacy_skills_json()` + `sync_capability_manifest.py`；④`pytest tests/unit/test_skill_description_single_source.py`（应回到 32 passed）。 |

---

## 9. 验证与复现清单（跑过什么、结果是什么）

### 9.1 守卫测试（基线对照）

```text
PS> python -m pytest tests/unit/test_skill_description_single_source.py -q --no-header -p no:cacheprovider
改前（本卡开工）:       通过: 32  失败: 0      ← G1-B 基线
迁移后（未修任何东西）: 通过: 28  失败: 4      ← 4 处红灯（= §6.1 的第 1/2/3/4 项）
修完后:                 通过: 32  失败: 0      ← 与基线相同，未放宽
```

### 9.2 全相关回归（1 条命令，33 个文件）

```text
PS> python -m pytest <33 个相关测试文件> -q --no-header -p no:cacheprovider
通过: 978  失败: 0  跳过: 7  xfail: 1   (78.01s)

SKIPPED [1] tests/unit/test_skill_index_cache.py:296: 需要 --runslow 选项才跑性能测试
SKIPPED [6] tests/unit/test_tool_callability.py: 需要 --runslow 选项才跑性能测试
XFAIL tests/unit/test_skills_mgmt.py::TestRetrievalEvaluation::test_skill_retrieval_precision_above_threshold

覆盖：skill_description_single_source / skill_create_no_data_loss / skill_search_description_source /
     skill_h3_migration / update_meta_no_data_loss / skill_registry(_audit) / skill_index_cache /
     skills_mgmt / skills_cleanup / skills_manager_delete_guard / skills_remove_guard / skill_manager /
     skill_lifecycle / skill_file_store_path_traversal / skills_mgmt_safety / skills_delete_guard_failclosed /
     verify_migrated_skills / bm25_skill_searcher / retrieval_silent_failures / vector_skill_searcher /
     tool_callability / capability_spec / capregistry_core / capregistry_callpaths_routes /
     capregistry_capacity_warning / confirm_level / toolset_hash / descriptors_backfill /
     descriptors_bridge / descriptors_registry / agentskills_io_compat / skill_update_audit
```

> `xfail` 是**改造前既有**的 TF-IDF 阈值项（G1-B 报告同样记为既有 xfail），非本卡引入。
> 完整 `tests/unit` 全量套件**未跑完**（见 §7-U3）。

### 9.3 其它入口

```text
python -c "from agent.skills_mgmt.file_store import _META_FIELDS as m;print(len(m),'description_zh' in m)"
  → 19 True                                   （本卡未改白名单，仅确认）

python scripts/sync_capability_manifest.py --check
  迁移后、重跑前 → 退出 1（+5 新增 / runtime_only_entities / counts 三处）
  重跑后         → [OK] 清单与权威数据一致：119 条能力（location: local 98 / remote 21），退出 0

python scripts/compare_skills_legacy_vs_repo.py --verify
  [SET] 仅在旧格式: ['global-core-principles', 'skill']      ← 从 7 条缩到 2 条（H-3 的"不纳入"集）
  逐行 DIFF 标记: 0 处（守卫测试 test_real_repo_has_zero_field_diffs 绿）
  结论行 HAS_DIFF（由上面那 2 条 only_legacy 驱动，与 G1-B 时同源；CI 走 --ci ⇒ PASS-SKIP，不受影响）

python scripts/audit_governance_check.py
  FAIL（FAIL=9 WARN=2）—— 全部为 G3 Merkle 日根，见 §7-R1（非本卡）

G1-A §11.6 脚本 F（判据一字未改）
  触发句式覆盖 21/28 = 75.0%（基线 17/23 = 73.9%）；若这 5 条用中文则 16/28 = 57.1%
```

---

## 10. 残留物自证（收尾）

```text
PS> git rev-parse HEAD
5c9ace10a4ca4bb96860db3a48debf9ddcf496bf         ← 与开工一致，未变

PS> git worktree list
C:/Users/Administrator/agent 5c9ace10 [master]    ← 只有主工作区

PS> git status --porcelain | Measure-Object -Line
126 条 ⇒ 逐条核对：本卡产生的条目 =
        · 5 个 "?? data/skills_repo/<id>/"（迁建目录）
        · 4 个 M：agent/skills_mgmt/{file_store,searcher,service}.py + tests/unit/test_skill_description_single_source.py
        · 6 个 M（能力面基线锁 4 个 + data/capability_manifest.json + docs/rfc/云枢能力清单盘点表.md）
        · 本报告 `docs/audit_skill_governance/G1C.md`（该目录整体本就是 untracked，
          故 git status 只显示一行 "?? docs/audit_skill_governance/"）
        · "?? tests/unit/test_skill_{create_no_data_loss,search_description_source,h3_migration}.py"（本卡新测试）
        （其余条目属其他卡；agentskills 的 M 计数按 git 的归类逐条核对过）
        没有本卡在仓库根 / agent/ / plugins/ 下新建的临时脚本或探针。

⚠️ 另有一条 **`D  data/learned_workflows.json`（已暂存的删除）**：**不是本卡所为**。
   证据：本卡全程**没有执行过任何**改动 git 索引的命令（只用过 `status` / `rev-parse` /
   `show` / `diff` / `check-ignore` / `ls-files` / `worktree list`），也**未写**该文件
   （任务卡硬约束"不要写 data/learned_workflows.json"）。
   【诚实边界】本卡在开工时**没有留全量 `git status` 的逐条快照**（只有条数 125），
   故不能逐字证明它在开工前就已存在；但"我没有暂存过任何东西"是可核的。

PS> Test-Path 'C:\Users\Administrator\agent\undefined'
False                                             ← 无 undefined/ 类失败 write 残留

PS> Get-CimInstance Win32_Process -Filter "Name='python.exe'"
PID 12388 / 19512 / 17048（**其他卡**：e1f1a/probe_ready.py 与两个 embedding worker）
PID 19460（**其他卡**的 pytest）
⇒ 本卡的 pytest/探针进程**已全部退出**（那个 5.1 GB 的全量扫描已 job_kill）

PS> 监听端口 5678 / 5000 / 8000
（未启动任何常驻服务；全部验证走进程内调用）
```

- **未做任何 `git add` / `git commit`**；`data/learned_workflows.json` **未触碰**。
- 所有探针写在 `C:\Users\Administrator\AppData\Local\Temp\g1c\`（仓库外）；
  探针自建的临时 repo 用 `tempfile.mkdtemp` 并 `shutil.rmtree` 自清
  （首次探针落在 `C:\Windows\TEMP`，因为 `$env:TEMP` 就是它 —— 后续一律改为显式绝对路径）。
- 未 `taskkill` 任何非本卡进程；未启动服务；未占用端口。

---

## 11. 遗留项（交给后续卡）

| # | 遗留 | 归属 |
|---|---|---|
| 1 | **U1**：生产 Layer-1 的 `_meta_to_meta_text()` 只拼英文 `description` ⇒ 中文 query 召回弱（对全部 28 条） | 检索卡（`loader.py` 不在本卡范围；`tool-retrieval-ci.yml` 盯着它） |
| 2 | **U2 / R-d-2**：管理页搜索语料（22 条主轨）⊂ 展示语料（30 行）⇒ 8 条文件轨独有技能搜不到 | 技能管理卡 |
| 3 | **U6**：`descriptions.baseline.json` 未被 git 跟踪 ⇒ 干净 checkout 里守卫测试会红 | G1-B owner / CI owner |
| 4 | **R-1**：审计链 G3 日根 FAIL=9（重封/封印路径问题） | 审计卡（仓里已有 `scripts/audit_reseal_daily_root.py` 在改） |
| 5 | **§4.3 P1/P2**：`global-core-principles` 的常驻位（prompt 装配）+ `agent_lines.skills` 死字段 | prompt 装配卡 / 主线卡 |
| 6 | **R-3**：主轨 `description`（22 条）清空或转只读镜像 | 后续卡 |
| 7 | **R-5**：技能敏感标记（`is_sensitive`/`isolation_strategy`）在文件轨无人写 | 后续卡 |
| 8 | **R-4**：`test_capregistry_capacity_warning.py:59` 的 docstring 仍写"真实 114 条" | 能力基线 owner |



