# DET-4 · 「set 交给稳定排序」这一族的**用户可见落点**收敛：三个最高优先落点 + 10 个逐点判定 + 补扫 4 处漏网

| 项 | 值 |
|---|---|
| 基线 HEAD | 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf（本卡未动分支） |
| 本卡改动文件 | agent/text_tools.py、agent/process_distill/solidify.py、agent/task_planner/enhanced_planner.py、agent/server_routes/routes_assets.py、agent/skills_mgmt/executor.py、agent/safety_guard.py、agent/skills_mgmt/store.py、agent/skills_mgmt/memory_abstractor.py（8 个）+ tests/unit/test_det4_stable_sort_determinism.py（新，24 条） |
| 改动合计 | **+90 / −28，27 hunk**（本卡专属 diff，见 §2；8 个文件的「改动前副本」与开工前生产位逐字节相同） |
| 探针位置（仓库外） | C:\Users\Administrator\AppData\Local\Temp\det4\ |
| 出网 | **零**（全程 AGENT_HYBRID_EMBEDDING=0，未加载向量模型，未访问网络） |
| 新增 env | **无**（确定化是收紧，不需要逃生开关；agent/settings/registry.py 一字未改，sha256 = 30A704C2…E304 与开工前相同，test_settings_registry.py 仍 **56 passed** = 基线） |
| 结论 | 点名 3 个最高优先落点**全部到达用户可见输出、全部修**；其余 10 个逐点判定为**到达 3 / 不到达 6 / 不可达 1**；另外**补扫出 4 处 DET-3 漏掉的落点**（3 处已修、1 处超文件范围只登记） |

---

## 0. 结论速览

1. **三个最高优先落点：全部到达、全部修、全部有跨进程原始输出。**
   * **text_tools.py 的 `list(set(mN))`（12 处）**——工具 `humanize_zh` 的返回值，直接进模型上下文。
     改前 5 个种子 **5 种**返回值；其中模式 1 与模式 7 因为后面还跟着 `matches[:10]` 截断，
     **成员也不同**（5 种子 5 种成员集）。改后逐位唯一。
   * **solidify.py:118 / :257**——`list({*proc.tags, …})[:8]`。改前 5 个种子 **5 种 members**：
     `git / pd / external / from_knowledge` 互相挤掉（seed=0 的产物里 **`git` 整个消失**）。改后逐位唯一。
   * **enhanced_planner.py:590**——`list(set(rollback_path))`。回退任务的 id 是 `rollback_{i}`（**位置即身份**），
     改前同一个 `rollback_0` 在不同进程里指向**不同任务**（5 种子 5 种「位置→任务」映射）。改后等于
     「失败任务序 → 各自回滚路径序」这条候选汇合序。
2. **其余 10 个落点的判定分布：到达 3（已修）／不到达 6（只登记 + 证据）／不可达 1（只登记 + 证据）。**
   两条**与 DET-3 判定相反**的结论（都有逐跳证据）：
   * `permission_system.py:400` —— DET-3 记为「到达」，实测**不到达**：产物只进 `_alert_history`/`get_alerts()`，
     而 `get_alerts` 在**生产代码里零调用点**（只有 4 个测试 + 一个手工脚本 `agent/test_permission_system.py`）。
   * `search_aggregator.py:350` —— DET-3 记为「关键词列表（进检索请求/展示）」，实测**不到达**：
     `_extract_keywords` 的返回值从未离开过打分函数，两个调用点都只取
     `sum(1 for kw in keywords if kw in combined)` 这个**计数**（`search_aggregator.py:317` / `:430`）。
3. **补扫出 4 处 DET-3 清单里没有的落点**（这是本卡对「不许有漏网者」的补正）：
   `memory_abstractor.py:688 / :803 / :934`（`_tokenize` 返回 set，调用方**取前 N 个** ⇒ 成员漂移，已修）
   与 `process_distill/merge.py:158`（solidify 的**上游**：`merge_results` 产出的 `proc.tags` 本身无序，
   已登记，**不在本卡文件范围**）。
4. **最终清单无漏网**：态一 5（前序卡 3 + DET-3 2）、态二 **13 组 / 26 个代码点**（本卡修）、
   态三 6（不到达/不可达，逐个附证据）、态四 3 类（死代码/非生产残留，DET-3 已列，本卡复核同意）；
   另附**禁区 5 个代码点**与**超文件范围 1 处**的登记。
5. **非空转自证**：把 8 个文件退回改动前副本 ⇒ 新测试 **24 failed**；还原 ⇒ **24 passed**，
   且 8 个文件 sha256 **逐字节相同**（RESTORED-BYTES-IDENTICAL=True）。
6. **影响面（24 个用户可见产物键 × 5 种子）**：**顺序变化 23 / 24，成员变化 9 / 24**；
   改前 **23/24** 个键跨种子不稳定 → 改后 **0/24**。

---

## 1. 第 1 步：逐落点判定（先判定，再决定修不修）

### 1.1 装置（全部在仓库外，走生产入口）

| 文件（%TEMP%\det4\） | 作用 |
|---|---|
| det4runner.py | 通用跑法：**每种子一个新解释器** + 固定 PYTHONHASHSEED（0/1/2/random/random），AGENT_HYBRID_EMBEDDING=0 |
| probe_text_tools.py / drive_text_tools.py | 落点 16：`humanize_zh` 返回值逐模式跨种子比对 |
| probe_solidify.py / probe_planner.py / drive_pd_planner.py | 落点 22 / 17 |
| probe_misc.py / drive_misc.py | 落点 18/19/20/21/23/24/25/26/27/28 + 补扫的 memory_abstractor 3 处 |
| probe_merge.py / probe_e2e_merge_solidify.py | 补扫落点：merge.py:158 与其**端到端**后果 |
| scan_member_shape.py | 补扫：只找「会改变**成员**」的形态（`list(<set>)[:N]` 等），全仓 402 个文件 |
| measure_impact.py / drive_impact.py | 影响面：同一批输入在「改后 / 改前」各跑 5 种子，逐键比对顺序与成员 |
| mutation_selfproof.py | 非空转自证（退回 pre ⇒ 红 ⇒ 还原 ⇒ 绿 + sha256） |
| apply_det4*.py / gen_det4_diff.py | 定向补丁与 diff 生成（**全部只做字面替换，保行尾**） |
| pre\ / post\ | 8 个文件的「改动前 / 改后」逐字节副本（回滚与自证用） |

### 1.2 判定口径（每条都落到具体出口，不推测）

* **到达** = 产物进入 (a) 喂给模型的 prompt / **工具返回值**、(b) HTTP 响应体 / UI 渲染字段、
  (c) 用户/系统会读的**落盘文件**或**检索召回**、(d) 界面上看得到的列表顺序/成员。
* **不到达** = 消费链在某个函数内**终止**（没有生产调用点，或产物只被当作无语义的中间量消费）。
* 「不到达」必须给出**逐跳证据**（谁读它、读到哪一跳停）；**「不值得修」是合格结论，但必须有证据**。

### 1.3 三个最高优先落点（点名要做的）

#### （1）agent/text_tools.py `list(set(mN))` ×12 —— **到达（模型可见，最严重）· 已修**

**消费链（逐跳）**：`humanize_zh(text)`（`text_tools.py:403`）→
`agent/tools/code_tools.py:87` 用 `@_tools.register("humanize_zh", …)` 注册、`:95 _humanize_zh(**kwargs)` →
工具返回值 → **模型上下文**。旁证：`data/tool_definitions/humanize_zh.yaml:1`（工具声明）、
`agent/tool_router.py:120`（在关键词路由的声明表里）、`data/agent_lines/recon.yaml:42`（recon 主线白名单）、
`data/tool_index.json:203`、`data/capability_manifest.json:4237`（`host_executor=agent.tools.code_tools:_humanize_zh`）。
即：**模型能看到 `detected_patterns[*].matches` 的每一个元素与它们的先后**。

**改前原始输出**（`before_text_tools.txt`，每种子一行；12 个模式里只摘关键两行）：

    seed=0       p1   ["核心的", "标志着", "至关重要的", "为后续工作奠定基础", "深深植根于", "作为", "凸显了", "彰显了", "见证了", "是关键的，也是一次织锦般的证明"]
    seed=1       p1   ["不可磨灭的印记", "深深植根于", "强调了", "是关键的，也是一次织锦般的证明", "彰显了", "极其重要的", "核心的", "为后续工作奠定基础", "反映了更广泛的", "至关重要的"]
    seed=2       p1   ["见证了", "强调了", "极其重要的", "不可磨灭的印记", "凸显了", "深深植根于", "至关重要的", "代表", "核心的", "塑造着"]
    seed=random  p1   ["标志着", "为后续工作奠定基础", "是关键的，也是一次织锦般的证明", "核心的", "见证了", "强调了", "至关重要的", "极其重要的", "凸显了", "象征着"]
    seed=random  p1   ["是关键的，也是一次织锦般的证明", "作为", "彰显了", "不可磨灭的印记", "塑造着", "代表", "象征着", "凸显了", "深深植根于", "极其重要的"]
    seed=0       p7   ["培养", "复杂", "持久的", "至关重要", "深入探讨", "宝贵的", "相互作用", "关键", "织锦", "获得"]
    seed=1       p7   ["证明", "织锦", "相互作用", "深入探讨", "宝贵的", "格局", "持久的", "充满活力的", "展示", "增强"]
    seed=2       p7   ["相互作用", "此外", "突出", "获得", "格局", "增强", "充满活力的", "深入探讨", "复杂", "强调"]
    seed=random  p7   ["持久的", "不可或缺", "关键", "格局", "深入探讨", "突出", "充满活力的", "获得", "强调", "宝贵的"]
    seed=random  p7   ["深入探讨", "强调", "培养", "格局", "至关重要", "不可或缺", "证明", "增强", "持久的", "充满活力的"]

    => full-output value-set(5)
    p1   SEQ-DIFFERS(5) SET-DIFFERS(5)     ← 有 [:10] 截断 ⇒ **成员**也变
    p7   SEQ-DIFFERS(5) SET-DIFFERS(5)     ← 同上
    p2/p4/p5/p8/p19  SEQ-DIFFERS(4~5) SET-DIFFERS(1)   ← 只变顺序
    p6/p11/p20/p22/p23 SEQ-DIFFERS(1)                   ← 本来就只有 1 个元素

**改后**：`=> full-output value-set(1)`，12 个模式全部 `SEQ-DIFFERS(1) SET-DIFFERS(1)`。

**修法**：`list(set(mN))` → `list(dict.fromkeys(mN))`（**去重且保留 findall 的文本发现序**）。
这是 DET-3 §4.2 落点 16 给的一句话改法，也是 DET-2/DET-3 的口径「保留主键不变、只补一个确定的次级键；
次级键取该子系统本来就有的那条次序」——这里的「本来就有的次序」就是**正则扫描的文本序**。

#### （2）agent/process_distill/solidify.py:118 / :257 —— **到达（且成员可变）· 已修**

**消费链（逐跳）**：
`:118` `solidify_to_workflow` 的 `LearnedWorkflow.tags` → `wf_svc.generator.generate_and_store(wf)`
→ `data/learned_workflows.json` → 主循环工作流学习层 0-Token 命中；
`:257` `solidify_to_skill` 的 `meta["tags"]` → `skills_svc.create_manual(data)`（**JSON 轨 = 管理权威 / UI 面板 / 审核链路**）
+ `file_store.create`（**文件轨 = skills_repo/<id>/skill.md，进 SkillLoader 语义召回**）。
调用方：`agent/process_distill/service.py:155/159`（`service` 是 `distill_process_from_knowledge` 工具的服务层）。

**改前原始输出**（`before_pd_planner.txt`）：

    seed=0       {"skill_tags": ["docs","release","distilled","ops","review","hotfix","from_knowledge","ci"], "wf_tags": [同上]}
    seed=1       {"skill_tags": ["ci","from_knowledge","review","hotfix","distilled","git","docs","ops"],  "wf_tags": ["pd","ci","from_knowledge","review","hotfix","distilled","git","docs"]}
    seed=2       {"skill_tags": ["distilled","review","release","git","hotfix","from_knowledge","docs","ops"], "wf_tags": ["distilled","review","release","pd","git","hotfix","from_knowledge","docs"]}
    seed=random  {"skill_tags": ["distilled","git","review","hotfix","release","docs","ops","external"], "wf_tags": ["distilled","git","pd","review","hotfix","release","docs","ops"]}
    seed=random  {"skill_tags": ["ops","distilled","review","external","from_knowledge","docs","ci","git"], "wf_tags": ["ops","distilled","review","from_knowledge","docs","pd","ci","git"]}
    => value-set(5)
       skill_tags  SEQ-DIFFERS(5) SET-DIFFERS(5)
       wf_tags     SEQ-DIFFERS(5) SET-DIFFERS(5)

逐条读法：7 个业务标签 + 3 个固定标签 = 10 > 8 ⇒ **截断点落在并列块内部** ⇒
seed=0 的产物里 **`git` 整个消失**，seed=2 的 skill 轨里 `external` 消失、`pd` 消失，
seed=random 里 `from_knowledge` 又回来 —— 这不是「顺序不同」，是**产出的标签集合不同**。

**改后**：5/5 种子逐位相同，且等于「`proc.tags` 声明序 → 固定标签」的前 8 个：

    {"skill_tags": ["git","release","hotfix","review","ci","docs","ops","distilled"],
     "wf_tags":    ["git","release","hotfix","review","ci","docs","ops","distilled"]}
    => value-set(1)

**修法**：`list({*proc.tags, …})[:8]` → `list(dict.fromkeys([*proc.tags, …]))[:8]`（DET-3 §4.2 落点 22 的一句话改法）。

#### （3）agent/task_planner/enhanced_planner.py:590 —— **到达（行为级）· 已修**

**消费链（逐跳）**：`create_rollback_plan(failed_plan)`（`:540`）→ `rollback_path = list(set(rollback_path))`（`:590`）
→ `for i, task_id in enumerate(rollback_path)`（`:596`）造 `EnhancedTaskNode(id=f"rollback_{i}", description=f"回退: {…}")`
→ `rollback_plan` 存入 `self._plans[trace_id]`（`:620`）并 `return rollback_plan`（`:622`）。
`rollback_{i}` 的**位置即身份** ⇒ 顺序变 = 同一个 `rollback_0` 指向**不同任务**。
出口是 `EnhancedTaskPlanner` 的公开 API（`:145-163` 的 docstring 就是「生成计划 → 确认 → 执行 → 失败则 create_rollback_plan」）。
**如实标注**：`EnhancedTaskPlanner` 在 agent/ 生产代码里**当前没有调用点**（全仓只有 tests/integration 与 tests/unit 引用）；
它到达的是「库 API 的返回值 / 计划对象的语义」，不是当前某条 HTTP 路由。按本卡判据它**仍然到达（行为级）**，
且修法零风险，故修。

**改前原始输出**（`before_pd_planner.txt`；输入是两条会在中间汇合的失败支路，raw 路径恒定 `[s3,s2,s1,s5,s4] + [s2,s1,s4]`）：

    seed=0       rollback_descs ["回退: 任务 s3","回退: 任务 s4","回退: 任务 s2","回退: 任务 s1","回退: 任务 s5"]
    seed=1       rollback_descs ["回退: 任务 s1","回退: 任务 s4","回退: 任务 s5","回退: 任务 s3","回退: 任务 s2"]
    seed=2       rollback_descs ["回退: 任务 s5","回退: 任务 s1","回退: 任务 s2","回退: 任务 s3","回退: 任务 s4"]
    seed=random  rollback_descs ["回退: 任务 s1","回退: 任务 s2","回退: 任务 s4","回退: 任务 s5","回退: 任务 s3"]
    seed=random  rollback_descs ["回退: 任务 s1","回退: 任务 s2","回退: 任务 s3","回退: 任务 s4","回退: 任务 s5"]
    => raw_paths           SEQ-DIFFERS(1) SET-DIFFERS(1)   ← 候选汇合序本身是确定的
       rollback_descs      SEQ-DIFFERS(5) SET-DIFFERS(1)
       rollback_task_ids   SEQ-DIFFERS(1) SET-DIFFERS(1)   ← id 是位置，恒为 rollback_0..4

**改后**：5/5 种子 `["回退: 任务 s3","回退: 任务 s2","回退: 任务 s1","回退: 任务 s5","回退: 任务 s4"]`，
**逐位等于** `dict.fromkeys(get_rollback_path("s6") + get_rollback_path("s7"))`。

**修法**：`list(set(rollback_path))` → `list(dict.fromkeys(rollback_path))`。

### 1.4 其余 10 个落点：逐个判定

| # | 位置 | 到达吗 | 判定理由（证据行） | 修了吗 |
|---|---|---|---|---|
| 18 | agent/skills_mgmt/store.py:195 `new_tags = list(set(dst.tags) \| set(src.tags))` | **到达** | `/api/skills-mgmt/merge`(:1141)/`/auto-merge`(:1200) → `store.py:250/253` → **data/skills_mgmt.json**（`json.dump` 无 `sort_keys`）→ `/api/skills-mgmt/<id>` → 前端 `skills-mgmt.js:216` 的标签 chips | **修** |
| 19 | agent/skills_mgmt/store.py:385 `new_deps = list({…})`（兜底分支） | **不可达** | 该分支只在 `except DependencyConflictError` 触发；而 `dependency_validator.merge_dependencies`（`:350-457`）**全函数没有任何 raise DependencyConflictError**，`:113` 注释明写「prefer_a / prefer_b 会自动选择保留方，不抛异常」，且 `store.py:367` 把 `strategy` **硬编码 prefer_a** ⇒ 分支不可达（本卡用 monkeypatch 强行抛出才复现出 5 种子 5 种次序） | 登记（未修） |
| 20 | agent/skills_mgmt/memory_abstractor.py:1068 `"tags": list(set(…))` | **到达** | `/api/skills-mgmt/abstract-from-memory`(:1311) 响应含完整 `draft`；`create_manual` → `data/skills_mgmt.json`；定时路径 `precipitate.py:253` 写审计 JSONL | **修** |
| 21 | agent/skills_mgmt/memory_abstractor.py:661 `for key in common_keys:`（set） | **到达** | `result` 的**插入序**经 `:1033` 变成正文里的 `默认参数: k=v, …` 文本，并经 `:1069` `default_params` → `create_manual` 落 `data/skills_mgmt.json` | **修** |
| 22 | agent/process_distill/solidify.py:118 / :257 | **到达（且成员变）** | 见 §1.3（2） | **修** |
| 23 | agent/server_routes/routes_assets.py:22（set 字面量）/ :173（备份）/ :265（导出） | **到达** | `app_server.py:1369-1375` `reg_assets(app, _assets_state)` 已挂载；`/api/assets/export` → `json.dumps(export_data)` **落盘 data/backups/assets_export_*.json**（`:266`）+ `send_file(..., as_attachment=True)` **响应体**（`:267`）；备份路径同形 | **修** |
| 24a | agent/skills_mgmt/executor.py:499 `for key in _ENV_WHITELIST`（子进程 env dict 键序） | **不到达** | `safe_env` 的唯一读者是 `executor.py:250` 的 `subprocess.run(env=safe_env)`；envp 的**顺序对子进程没有语义**（`os.environ` 是映射），成员不变；无日志、无返回值 | 登记（未修） |
| 24b | agent/skills_mgmt/executor.py:617 `list(_ENV_WHITELIST)` | **到达** | `health()` → `service.py:2422/2436` → `routes_skills_mgmt.py:64 jsonify(_svc().health())` → **GET /api/skills-mgmt/health**（app_server.py:1232-1233 注册） | **修** |
| 25a | agent/safety_guard.py:143 `list(set(m["category"] …))` | **到达** | `_record_alert`（`:135`）→ `callbacks = list(_alert_callbacks)`（`:150`）→ `app_server.py:980 register_alert_callback(_on_safety_alert)` → `_alert_queue`（`:975-978`）→ `plugins/safety.py:155-162 /api/safety/alerts` → `templates/index.html:3618` fetch + `:3627-3636` 渲染 | **修** |
| 25b | agent/permission_system.py:400 同形态 | **不到达** | 产物只进 `_alert_history`（`:402`）/ `get_alerts()`（`:406`）；`get_alerts` 的**生产调用点为零**（命中只有 tests/unit/test_permission_edge_cases.py:360/406/416、test_permission_system_concurrency.py:180/200、与手工脚本 agent/test_permission_system.py:198）；模块本身活着（`system_tools.py:112` 等只用 `check_text/check_action` 的结果，**不含 categories**） | 登记（未修） |
| 26 | agent/search_aggregator.py:350 `return list(set(keywords))` | **不到达** | 唯一两个调用点 `:316`（`_keyword_bonus`，**死代码**）与 `:426`（`score_result`），消费方式都是 `hits = sum(1 for kw in keywords if kw in combined)`（`:317`/`:430`）——**只取计数**，与次序无关，且 `list(set(…))` 不改变成员 ⇒ 无可观测差异 | 登记（未修） |
| 27 | agent/memory/adapters/holographic_adapter.py:679 `profile["tags"] = list(tags)` | **不到达** | 唯一消费者是 `MemoryRouter.get_profile`（`router.py:561-568`），而全仓**没有生产代码调用 MemoryRouter.get_profile**（dashboard 的 memory 面板读 mock）；`save`/`register_tier` 才是生产用法 | 登记（未修） |
| 28 | agent/skills_mgmt/conflict_resolver.py:184 `for key in all_keys:`（set） | **不到达** | `_merge_front_matter` → `serialize(..., sort_keys=False)` → `file_store.py:189 write_text` 确实会把键序写进 skill.md 字节，但 **ConflictResolver 全仓仅被 tests 引用**（`agent/` 里只有类定义与 `__all__`） | 登记（未修） |

**两条与 DET-3 相反的判定**（已在上表加粗）：`permission_system.py:400`（DET-3 记「到达」→ 实测不到达）、
`search_aggregator.py:350`（DET-3 记「进检索请求/展示」→ 实测只被当计数用）。

**6 个「不到达」的一句话改法**（留给后续卡，本卡按「不到达的只登记」执行）：

    19  store.py:385              new_deps = list(dict.fromkeys((d if isinstance(d, str) else str(d))
                                     for d in list(dst_skill.dependencies) + list(src_skill.dependencies)))
    24a executor.py:499          （与 24b 同源）把常量 _ENV_WHITELIST 改成元组即可一处修两处
    25b permission_system.py:400 list(dict.fromkeys(m["category"] for m in result["matches"]))
    26  search_aggregator.py:350 list(dict.fromkeys(keywords))   # 去重（保发现序）
    27  holographic_adapter.py:679 tags 用有序集合，或 profile["tags"] = sorted(tags)
    28  conflict_resolver.py:184 all_keys = dict.fromkeys(list(ours) + list(theirs))  # ours → theirs 声明序

### 1.5 补扫：DET-3 漏掉的 4 处（本卡新增点名）

DET-3 §4.1 的 P5~P10 补扫判据覆盖了「`list(set(...))` 变成有序产物」，
但**没有覆盖「有序产物之后还有截断」的组合**。本卡用 `scan_member_shape.py` 专扫这一形态
（S1 `list(<set>)[:N]`；S2 `name = list(<set>)` 之后出现 `name[:N]`/`min/max/enumerate`；S3 `name = <set>` 之后 `list(name)[:N]`），
先把「返回 set 的函数名」自动收集出来（49 个，含 `_tokenize`），再扫全仓 agent/ + memory/ + scripts/。

    候选 3 条：
    S3     agent\skills_mgmt\memory_abstractor.py:688  list(keywords)[:5]
    S2     agent\skills_mgmt\memory_abstractor.py:803  keywords[:3]
    S3     agent\skills_mgmt\memory_abstractor.py:934  list(keywords)[:3]

| # | 位置 | 到达吗 | 证据 | 修了吗 |
|---|---|---|---|---|
| 30 | memory_abstractor.py:687-688（`_extract_root_cause`） | **到达（成员变）** | `keywords = _tokenize(representative_text)`（`:114 _tokenize` **返回 set**）→ `" ".join(list(keywords)[:5])` → `keyword_str` 逐字进 `root_cause_hypothesis` → 技能草稿正文 → API / 落盘 | **修** |
| 31 | memory_abstractor.py:801-803（`_extract_trigger_conditions`） | **到达（成员变）** | `keywords[:3]` → `conditions.append(f"任务描述包含: {top_kw}")` → 草稿正文 | **修** |
| 32 | memory_abstractor.py:932-934（`_extract_anti_patterns`） | **到达（成员变）** | `list(keywords)[:3]` → `patterns.append(f"不涉及: 与 {…} 无关的任务")` → 草稿正文 | **修** |
| 33 | agent/process_distill/merge.py:158 `tags=list({*triggers[:3], "distilled", "from_knowledge"})` | **到达** | 它是 **solidify 的上游**：`service.py:24/109 merge_results(...)` → `DistilledProcess.tags` → solidify 的 tags（而 solidify 的修法**原样保留** `proc.tags` 的次序） | **不在本卡文件范围，只登记** |

**漏网 30/31/32 的改前证据**（`before_misc.txt` 的 `memory_abstractor_keywords` 逐种子）：

    seed=0       root_cause 使用 [bash] 处理「python asyncio 并 发 教」持续有效 …
    （5 个种子里 keyword_str 取到的 5 个 token 不固定：并/发/教/python/asyncio 谁进前 5 取决于 set 迭代序）
    => SET-DIFFERS(2)：改前同一个种子集里出现了**不同的关键词集合**

**修法（已实施）**：在 `_tokenize` 旁边新增**有序版** `_ordered_tokens(text)`（同一个元素集合，
次序 = 「正则 token 的文本序 → 中文字的文本序」），三个调用点改用它；新测试断言
`set(_ordered_tokens(t)) == _tokenize(t)`（成员完全一致，只定义次序）。

**漏网 33 的端到端证据**（`probe_e2e_merge_solidify.py`，真实生产链 `merge_results → solidify_to_workflow`，**修后**仍抖）：

    seed=0       {"proc_tags": ["distilled","hotfix","from_knowledge","回滚","发布"], "wf_tags": [同上,"pd"]}
    seed=1       {"proc_tags": ["from_knowledge","distilled","hotfix","发布","回滚"], "wf_tags": [同上,"pd"]}
    seed=2       {"proc_tags": ["distilled","回滚","发布","hotfix","from_knowledge"], "wf_tags": [同上,"pd"]}
    seed=random  {"proc_tags": ["hotfix","发布","distilled","回滚","from_knowledge"], "wf_tags": [同上,"pd"]}
    seed=random  {"proc_tags": ["distilled","hotfix","发布","from_knowledge","回滚"], "wf_tags": [同上,"pd"]}
    => value-set(5)

**这条必须如实说明**：本卡把 solidify **自身**的 set 迭代序消掉了（§1.3(2) 已证），
但在**真实生产链**上，`proc.tags` 本身来自 `merge.py:158` 的 set ⇒ 端到端仍然抖。
`merge.py` **不在本卡文件范围**，故只登记；一句话改法：
`tags=list(dict.fromkeys([*triggers[:3], "distilled", "from_knowledge"]))`。
（附带事实：`merge.py` 路径的 tags 恒 ≤5 个，加上 solidify 的 3 个固定标签后去重 ≤6 < 8 ⇒
该路径**不会**触发 solidify 的 `[:8]` 截断；即「成员变」在生产链上是**潜伏**的，
而「顺序变」是**当下就发生**的。）

---

## 2. 第 2 步：修复（只修判定为「到达」的落点）

### 2.1 本卡专属 diff（+90 / −28，27 hunk）

8 个文件的「改动前副本」保存在 %TEMP%\det4\pre\，与开工前生产位**逐字节相同**（sha256 见 §7），
故 `my_det4.diff` 100% 是本卡的。逐字 diff：%TEMP%\det4\my_det4.diff。摘录（注释略，正文见文件）：

    --- a/agent/text_tools.py                                   （+5 注释 / 12 行替换）
    -        matched_texts = list(set(m1))            +        matched_texts = list(dict.fromkeys(m1))
    -            "matches": list(set(m2)),            +            "matches": list(dict.fromkeys(m2)),
    …（m4/m5/m6/m7/m8/m19/m20/m21/m22/m24 同形，共 12 处）

    --- a/agent/process_distill/solidify.py                    （+6 / −2）
    -        tags=list({*proc.tags, "distilled", "from_knowledge", "pd"})[:8],
    +        tags=list(dict.fromkeys([*proc.tags, "distilled", "from_knowledge", "pd"]))[:8],
    -        "tags": list({*proc.tags, "distilled", "from_knowledge", "external"})[:8],
    +        "tags": list(dict.fromkeys([*proc.tags, "distilled", "from_knowledge", "external"]))[:8],

    --- a/agent/task_planner/enhanced_planner.py               （+4 / −1）
    -        rollback_path = list(set(rollback_path))  # 去重
    +        rollback_path = list(dict.fromkeys(rollback_path))  # 去重（保序）

    --- a/agent/server_routes/routes_assets.py                 （+8 / −1）
    -FILE_BASED_CATEGORIES = {"habits", "inspires", "hobbies", "interactions"}
    +FILE_BASED_CATEGORY_ORDER = ("habits", "inspires", "hobbies", "interactions")
    +FILE_BASED_CATEGORIES = set(FILE_BASED_CATEGORY_ORDER)
    -            cats = categories if categories else FILE_BASED_CATEGORIES
    +            cats = categories if categories else FILE_BASED_CATEGORY_ORDER
    -            for cat in FILE_BASED_CATEGORIES:
    +            for cat in FILE_BASED_CATEGORY_ORDER:   # [DET-4] 导出键序 = 声明序

    --- a/agent/skills_mgmt/executor.py                        （+5 / −1）
    -                "env_whitelist": list(_ENV_WHITELIST),
    +                "env_whitelist": sorted(_ENV_WHITELIST),

    --- a/agent/safety_guard.py                                （+3 / −1）
    -            "categories": list(set(m["category"] for m in result["matches"])),
    +            "categories": list(dict.fromkeys(m["category"] for m in result["matches"])),

    --- a/agent/skills_mgmt/store.py                           （+3 / −1）
    -            new_tags = list(set(actual_dst.tags) | set(actual_src.tags))
    +            new_tags = list(dict.fromkeys(list(actual_dst.tags) + list(actual_src.tags)))

    --- a/agent/skills_mgmt/memory_abstractor.py               （+41 / −5）
    +def _ordered_tokens(text: str) -> List[str]:        # 与 _tokenize 同集合、按文本发现序
    -        for key in common_keys:
    +        for key in [k for k in entries[0].params if k in common_keys]:
    -        keywords = _tokenize(representative_text)
    -        keyword_str = " ".join(list(keywords)[:5]) if keywords else "该任务"
    +        keywords = _ordered_tokens(representative_text)
    +        keyword_str = " ".join(keywords[:5]) if keywords else "该任务"
    -        keywords = list(_tokenize(representative_text))          （:801）
    +        keywords = _ordered_tokens(representative_text)
    -            top_kw = list(keywords)[:3]                          （:934）
    +            top_kw = keywords[:3]
    -            "tags": list(set(cluster.common_tags + ["memory-abstracted"])),
    +            "tags": list(dict.fromkeys(cluster.common_tags + ["memory-abstracted"])),

### 2.2 次级键为什么取「候选汇合序」而不是字典序（逐个交代）

| 落点 | 主键（未变） | 次级键（新增） | 为什么是它 |
|---|---|---|---|
| text_tools ×12 | 无（去重） | **findall 的文本发现序** | 那是该工具**本来就有**的次序；dict.fromkeys 只把「未定义」变「已定义」，成员一个不少 |
| solidify ×2 | 无（去重 + [:8]） | **proc.tags 的声明序 → 固定标签** | 上游 DistilledProcess.tags 是 List，声明序就是候选汇合序（DET-3 原话给的就是这条） |
| enhanced_planner | 无（去重） | **失败任务序 → 各自回滚路径序** | 就是 :577-579 的 `for failed_task in failed_tasks: rollback_path.extend(path)` 这条汇合序 |
| routes_assets | 无（枚举） | **类别的声明序** | 它是「类别枚举」，本来就该有序；且既有的 6 处 `in FILE_BASED_CATEGORIES` 与 3 条既有断言都按**集合**比较 ⇒ 用「有序元组 + 派生集合」兼顾两者（**没有放宽任何断言**） |
| executor health | 无 | **字典序** | `_ENV_WHITELIST` 是 set 字面量，本身不存在「汇合序」；该列表无既有次序契约（前端只读 `ok`）⇒ 取字典序，且**不动常量**以免打断 test_settings_registry.py 的 PASS_THROUGH_SITES 具名命中 |
| safety_guard | 无 | **matches 的发现序** | `result["matches"]` 是扫描构造的**列表**，其序就是候选汇合序 |
| store merge tags | 无 | **保留方声明序 → 被合并方声明序** | 合并语义天然是「dst 在前、src 在后」，与 new_deps 的既有次序一致 |
| memory_abstractor :661 | 无 | **entries[0].params 的键声明序** | 三个条目取交，第一条的键序就是候选汇合序（比字典序更贴近原意） |
| memory_abstractor ×3 关键词 | 无（取前 N） | **_ordered_tokens 的文本发现序** | 与 _tokenize **同一个元素集合**，只是把次序定义掉；取前 N 的**成员**从此有定义 |

### 2.3 影响面（改前 vs 改后，24 个用户可见产物键 × 5 种子）

`drive_impact.py`：同一批输入，在「改后（生产位）」与「改前（pre 副本装回）」各跑 5 个种子（每种子新解释器），
逐键比对**顺序**与**成员**：

    key                before-kinds   after-kinds    order      member
    text_tools#0       5              1              CHANGED    CHANGED
    text_tools#1       5              1              CHANGED    CHANGED
    text_tools#2       4              1              CHANGED    CHANGED
    text_tools#3       5              1              CHANGED    CHANGED
    solidify#0         5              1              CHANGED    CHANGED
    solidify#1         5              1              CHANGED    CHANGED
    solidify#2         5              1              CHANGED    -
    solidify#3         3              1              CHANGED    -
    planner#0          5              1              CHANGED    -
    planner#1          3              1              CHANGED    -
    planner#2          5              1              CHANGED    -
    routes_assets      4              1              CHANGED    -
    executor_health    5              1              CHANGED    -
    safety#0           5              1              CHANGED    -
    safety#1           4              1              CHANGED    -
    safety#2           1              1              -          -        （只命中 1 个类别，本来就确定）
    store#0/1/2        5/5/5          1/1/1          CHANGED    -
    mem_kw#0/1/2       5/5/5          1/1/1          CHANGED    CHANGED
    mem_params         5              1              CHANGED    -
    mem_tags           5              1              CHANGED    -
    ------------------------------------------------------------------
    TOTAL keys=24  order-changed=23  member-changed=9
    keys unstable across seeds: before=23  after=0（of 24）

**读法**：
* 「顺序变化 23/24」是**把未定义次序定义掉**的必然代价（改前每个种子一种答案，没有唯一基线）；
  不是语义回归 —— 成员变化的 9 条才是真正会影响「用户看到什么」的那批。
* 「成员变化 9/24」= text_tools 4 条 + solidify 2 条 + mem_kw 3 条。
* **改前 23/24 个键跨种子不稳定 → 改后 0/24**（`safety#2` 本来就只在 1 个类别上命中）。

**（附带）成员集合变化的明细**（改前→改后）：
* `solidify#0`：改前 seed=0 的 `git` **整个消失**、seed=2 的 `pd`/`external` 消失 → 改后恒为
  `[git, release, hotfix, review, ci, docs, ops, distilled]`；
* `text_tools#0`：模式 1 的 10 个成员在 5 个种子里取了 5 组不同的 10 个（候选有 18 个，截断点落在并列块内）→ 改后恒为文本发现序的前 10 个；
* `mem_kw#0/1/2`：root_cause/触发条件/反例里的关键词三处取前 3~5 个，改前成员漂移 → 改后恒为文本发现序的前 N 个。

---

## 3. 第 3 步：非空转自证（去掉修复 ⇒ 必红 ⇒ 还原 ⇒ 绿 + sha256 逐字节）

`%TEMP%\det4\mutation_selfproof.py`：把 8 个文件的 **pre 副本**（= 改动前生产位）装回仓库 → 跑新测试
→ 把 **post 副本**（= 改后生产位）装回 → 再跑新测试 → sha256 对拍（**不做整文件 git checkout**）。

    == backup (post-DET4 = production) ==
       c1ae4b5ff8af92ce77f85cf8ec37f5083e4a9b537507a2cbac2711c90814bf88  agent/text_tools.py
       cec444d931fc1c9fbc1f1ef5284a9ea2ac8f0ea0a226df476c18ec31514a8ab2  agent/process_distill/solidify.py
       220d931253adea3dc6d3adb0377144f82afe861ceedfce3d1215174d8db8607a  agent/task_planner/enhanced_planner.py
       deb80037cb4c5f6ecabaab328425ef571736b3e99c69bbd95b08e4b512c2a756  agent/server_routes/routes_assets.py
       c403ec0686de99d8a174974ce302ef237e1c76a656440a40946021a32ae34bc0  agent/skills_mgmt/executor.py
       3f6d89643cad445046ad2487772280ee8f9219f11fd94e251d6e8b911bfde098  agent/safety_guard.py
       ce5c24634c122c70906c98552cf56268f4f2e303a3df234a8f310e741c8acee7  agent/skills_mgmt/store.py
       8fb2e89cbac7e6743acf7f40d0b20bffba64d79e29e46e5fc3bf0af580ad39a2  agent/skills_mgmt/memory_abstractor.py
    == mutated (回退到 DET-4 之前) ==
       mutated: rc=1 | ============================= 24 failed in 16.04s =============================
    == restore ==
       agent/text_tools.py                            live=c1ae4b5ff8af92ce bak=c1ae4b5ff8af92ce -> RESTORED-OK
       …（8 个文件全部 RESTORED-OK）
    RESTORED-BYTES-IDENTICAL=True
       restored: rc=0 | ============================= 24 passed in 15.66s =============================
    SELFPROOF-FAILS=0

**24 条在变异下全部变红的断言原文**（`%TEMP%\det4\mutation_mutated.txt`，摘 8 条）：

    FAILED …::TestTextToolsMatchesAreDeterministic::test_matches_are_identical_across_hash_seeds
    E   AssertionError: humanize_zh 的 matches 跨进程不同（set 迭代序泄漏到用户可见输出）：
    E     seeds=['0'] -> {"1": ["核心的","标志着","至关重要的","为后续工作奠定基础","深深植根于","作为","凸显了","彰显了","见证了","是关键的，也是一次织锦般的证明"], …}
    E     seeds=['1'] -> {"1": ["不可磨灭的印记","深深植根于","强调了",…], …}
    E     seeds=['random'] -> {…}
    E   assert 5 == 1

    FAILED …::TestTextToolsMatchesAreDeterministic::test_truncated_patterns_keep_members_and_follow_discovery_order
    E   AssertionError: 模式 1 的 matches 不是「发现序前 10 个」⇒ 截断点上的成员仍取自迭代序
    E     assert ['为后续工作奠定基础', …] == ['是关键的，也是一次织锦般的证明', '强调了', …]
    E     At index 0 diff: '为后续工作奠定基础' != '是关键的，也是一次织锦般的证明'

    FAILED …::TestTextToolsMatchesAreDeterministic::test_non_truncated_patterns_follow_discovery_order
    E   AssertionError: assert ['区域媒体', '独立报道', '地方媒体'] == ['独立报道', '地方媒体', '区域媒体']

    FAILED …::TestSolidifyTagsAreDeterministic::test_tags_are_identical_across_hash_seeds
    E   AssertionError: solidify 的 wf_tags / skill_tags 跨进程不同（set 迭代序泄漏到用户可见输出）：
    E     seeds=['0']    -> {"skill_tags": ["docs","release","distilled","ops","review","hotfix","from_knowledge","ci"], …}
    E     seeds=['1']    -> {"skill_tags": ["ci","from_knowledge","review","hotfix","distilled","git","docs","ops"], …}
    E     seeds=['2']    -> {"skill_tags": ["distilled","review","release","git","hotfix","from_knowledge","docs","ops"], …}
    E   assert 5 == 1

    FAILED …::TestSolidifyTagsAreDeterministic::test_truncation_is_on_declaration_order_not_hash_order
    E   AssertionError: tags 不是「声明序 + 固定标签」的前 8 个 ⇒ 截断点上的成员仍取自 set 迭代序
    E     assert ['from_knowledge', …] == ['git', 'release', …]
    E     At index 0 diff: 'from_knowledge' != 'git'

    FAILED …::TestRollbackPlanOrderIsDeterministic::test_rollback_order_equals_failed_task_then_path_order
    （断言顺序 == dict.fromkeys(raw paths) 的次序）

    FAILED …::TestExecutorHealthWhitelistIsDeterministic::test_env_whitelist_is_identical_across_hash_seeds
    E   AssertionError: health().env_whitelist 跨进程不同（set 迭代序泄漏到用户可见输出）：
    E     seeds=['0'] -> ["PYTHONUTF8","PYTHONIOENCODING","LANG","TMP","USERPROFILE","OS","TEMP","HOME",…]
    E     seeds=['1'] -> ["SYSTEMROOT","LANG","OS","APPDATA","TMP","PYTHONUTF8","USERPROFILE",…]
    E   assert 5 == 1

    FAILED …::TestSafetyAlertCategoriesAreDeterministic::test_categories_follow_match_discovery_order
    E   AssertionError: assert ['权限提升', '系统破…系统控制', '磁盘破坏'] == ['文件破坏', '磁盘破…权限提升', '系统破坏']

    FAILED tests/unit/test_det4_stable_sort_determinism.py::test_no_bare_set_to_ordered_product_in_fixed_files
    E   AssertionError: 确定性修复被抄回去了：agent/text_tools.py 里又出现 'list(set('

**如实说明**：24 条在变异下**全部变红**（没有「变红不了」的空转守卫）。
其中 5 条是**源码级反向守卫**（禁止退回 `list(set(...))` / `list({...})[:8]` / 裸 set 迭代），
它们靠 `_code_only()` 先丢掉整行注释再匹配，因此**不会**被本卡的说明性注释误触发
（这一点在本卡第一次跑测试时真的踩到过：2 条守卫因为注释里引用了被禁写法而误红，已修掉误报）。

---

## 4. 第 3 步：收敛后的**最终四态清单**（含禁区登记，不许有漏网者）

### 态一：已修（前序卡：E1-D / DET-2 / DET-3）

| # | 位置 | 机制 | 状态与证据 |
|---|---|---|---|
| 1 | agent/tool_router_hybrid.py（_query_locked 的 all_candidates + hybrid_select_tools） | set 迭代序 → 融合的稳定排序 | E1-D 已修；sha256 = 53B3A41B…A61 与 DET-2 记录逐位相同（本卡未触碰） |
| 2 | agent/tool_router.py get_tools_for_input → helper | set → helper 稳定排序（并列块跨截断点 ⇒ 成员变） | DET-2 修；sha256 = 83448BB9…53E7（本卡未触碰）；test_det2_* 10 passed |
| 3 | agent/skills_mgmt/loader.py _tfidf_scan | set 迭代序 → 调用方稳定排序 → top-3/MRR | DET-2 修；本卡未触碰（该文件现为 45D5B358…，DET-3 §6.8 已记录它被**别的卡**在 10:50 写过） |
| 4 | agent/skills_mgmt/few_shot_injector.py:119 | set 迭代序 → **浮点求和次序** → 余弦分末位 | DET-3 修；sha256 = 9D18BD98…D96D 与 DET-3 记录一致（本卡未触碰） |
| 5 | agent/lines/assembler.py _plane_order | set（active_planes）+ 权重并列 → by_plane 键序 | DET-3 修；sha256 = 07F28E28…D246 与 DET-3 记录一致（本卡未触碰） |

### 态二：本卡修（13 组 / 26 个代码点，全部判定为「到达用户可见输出」）

| # | 位置（代码点） | 到达什么 | 证据 |
|---|---|---|---|
| 6 | agent/text_tools.py:437/453/475/486/497/504/520/635/646/657/668/690（12 点） | 工具 humanize_zh 的返回值 matches（**模型上下文**） | §1.3(1)：12 个模式由 value-set(5) → value-set(1)，p1/p7 成员由 5 种 → 1 种 |
| 7 | agent/process_distill/solidify.py:118 | LearnedWorkflow.tags → data/learned_workflows.json | §1.3(2)：SET-DIFFERS(5) → 1 |
| 8 | agent/process_distill/solidify.py:257 | 技能 meta["tags"] → JSON 轨（UI）+ 文件轨（检索） | 同上 |
| 9 | agent/task_planner/enhanced_planner.py:590 | 回退任务 rollback_{i} 的**位置→任务**映射（行为级） | §1.3(3)：SEQ-DIFFERS(5) → 1，且等于候选汇合序 |
| 10 | agent/server_routes/routes_assets.py:22（声明）/ :173（备份）/ :265（导出） | /api/assets/export 的**响应体附件** + data/backups/*.json 落盘键序 | §1.4 #23：4 种 → 1；既有 3 条断言未改一字 |
| 11 | agent/skills_mgmt/executor.py:617 | GET /api/skills-mgmt/health 的 env_whitelist | 5 种 → 1（= sorted） |
| 12 | agent/safety_guard.py:143 | /api/safety/alerts → 前端告警列表载荷 | 5 种 → 1，且等于 matches 的发现序 |
| 13 | agent/skills_mgmt/store.py:195 | 合并后的 tags → data/skills_mgmt.json + 标签 chips | 5 种 → 1 |
| 14 | agent/skills_mgmt/memory_abstractor.py:661 | 草稿 default_params 键序 → 正文「默认参数:」+ JSON 轨 | 5 种 → 1 |
| 15 | **agent/skills_mgmt/memory_abstractor.py:688（DET-3 漏）** | root_cause 正文里的前 5 个关键词 | §1.5：SET-DIFFERS(2) → 1 |
| 16 | **agent/skills_mgmt/memory_abstractor.py:803（DET-3 漏）** | 触发条件正文里的前 3 个关键词 | 同上 |
| 17 | **agent/skills_mgmt/memory_abstractor.py:934（DET-3 漏）** | 反例边界正文里的前 3 个关键词 | 同上 |
| 18 | agent/skills_mgmt/memory_abstractor.py:1068 | 草稿 tags → JSON 轨 / 面板 | 5 种 → 1 |

### 态三：仍存在，但本卡判定「不到达 / 不可达」（逐个附证据，本卡**未修**）

| # | 位置 | 判定 | 证据（逐跳） |
|---|---|---|---|
| 19 | agent/skills_mgmt/executor.py:499 | **不到达** | safe_env 唯一读者是 :250 subprocess.run(env=safe_env)；envp 顺序对子进程无语义，成员不变 |
| 20 | agent/permission_system.py:400 | **不到达**（与 DET-3 判定相反） | 只进 _alert_history / get_alerts()，而 get_alerts 生产调用点为零（命中全在 tests + 手工脚本 agent/test_permission_system.py:198） |
| 21 | agent/search_aggregator.py:350 | **不到达**（与 DET-3 判定相反） | 两个调用点都只取 sum(1 for kw in keywords if kw in combined) 计数（:317 / :430），_keyword_bonus 是死代码 |
| 22 | agent/memory/adapters/holographic_adapter.py:679 | **不到达** | 唯一消费者 MemoryRouter.get_profile（router.py:561-568）**无生产调用点**；面板读 mock |
| 23 | agent/skills_mgmt/conflict_resolver.py:184 | **不到达** | ConflictResolver 全仓仅 tests 引用（agent/ 里只有类定义与 __all__） |
| 24 | agent/skills_mgmt/store.py:385 | **不可达** | merge_dependencies 全函数无 raise DependencyConflictError，strategy 硬编码 prefer_a ⇒ except 分支不可达 |

### 态四：死代码 / 非生产残留（DET-3 已列；本卡复核同意，未参与任何修复）

| # | 位置 | 证据 |
|---|---|---|
| 25 | _scratch/、_ci_logs/、_t06_logs/、backup/、.trae/merge_backup_*、docs/archive/、.tmp-merge/、_tmp_rootcause_probe/ 下的同族命中 | 历史副本/探针残留，不参与任何运行时导入（本卡扫描器已按目录排除） |
| 26 | .devtools/pylibs/{fakeredis,redis}/** | 第三方 vendored 源码，不属于本仓代码 |
| 27 | tests/** 里刻意构造的同族形态（本卡测试的跨种子子进程探针、DET-3 的 _ShuffledSet） | 测试**故意**构造迭代序，是判据的一部分 |

### 附·态五（补）：**到达用户可见输出但不在本卡文件范围** ⇒ 只登记

| # | 位置 | 到达什么 | 一句话改法 |
|---|---|---|---|
| 28 | **agent/process_distill/merge.py:158（本卡新点名）** | DistilledProcess.tags → solidify 的 tags → data/learned_workflows.json + 技能 JSON/文件轨（**端到端仍抖，见 §1.5**） | tags=list(dict.fromkeys([*triggers[:3], "distilled", "from_knowledge"])) |

### 附·**禁区登记（3 个文件 / 5 个代码点，本卡一行未碰）**

| # | 位置 | 形态 | 本卡动作 |
|---|---|---|---|
| 29 | plugins/demo_plugin.py:46 for key in allowed:（allowed 是 set 字面量，:44） | 插件配置键序 → /api/demo/config 的 applied/config | 只登记 |
| 30 | agent/workflow_learning/skill_converter.py:265 | "tags": list(set([*wf.tags, "from_workflow", "auto_converted", "llm_reference"])) → 转换出的技能 tags | 只登记 |
| 31 | agent/workflow_learning/skill_converter.py:274 | "dependencies": list({step.tool_name …}) → 技能 dependencies 列表序 | 只登记 |
| 32 | agent/workflow_learning/skill_converter.py:644 | "dependencies": list({…})（外部技能导入轨） | 只登记 |
| 33 | agent/workflow_learning/generator.py:88 return list(set(missing)) | 缺失工具清单 → 工作流生成的告警/返回 | 只登记 |

**禁区复核证据**：Get-Item 显示这 3 个文件的 LastWriteTime 为
2026-09-13 19:53:01 / 2026-07-13 12:02:40 / 2026-08-31 02:58:03，**全部早于本卡开工**；本卡未写入一行。

### 对 DET-2/DET-3「明确排除」项的复核

DET-3 §4.3 逐条复核的那 5 类排除，本卡**全部复核同意**，并新增一条本卡独立扫描的结论：
`sorted(set(...))` / `sorted(集合变量)` 这类**全序**写法在 agent/ 里有约 100 处（本卡残扫清单可见），
它们的结果与迭代序无关 ⇒ **无害（正确样板）**，不作为本族缺陷。
本卡残扫（list({ / list(set( / list(frozenset( / sorted(set(）在 agent/ 里命中的**其余全部**是
sorted(set(...)) 形态或已登记项；**没有第五个「set → 有序产物」的漏网点**。

---

## 5. 回归结果（未放宽任何断言）

本卡要求的那批 + 同族/相邻（26 个文件，一条命令）：

    tests/unit/test_text_tools.py                            12 passed
    tests/unit/test_process_distill.py                       passed
    tests/unit/test_routes_process_distill.py                passed
    tests/unit/test_context_engineering.py                   passed
    tests/integration/test_context_engineering_verify.py     passed
    tests/integration/test_context_engineering_demo.py       passed
    tests/unit/test_routes_assets.py                         38 passed
    tests/unit/test_safety_guard.py                          17 passed
    tests/unit/test_safety_guard_concurrency.py              passed
    tests/unit/test_permission_system.py                     29 passed
    tests/unit/test_permission_edge_cases.py                 28 passed
    tests/unit/test_permission_system_concurrency.py         6 skipped（--runslow 门控，既有）
    tests/unit/test_skills_mgmt.py                           75 passed, 1 xfailed（既有 TF-IDF 基线阈值缺口）
    tests/unit/test_skill_manager.py                         62 passed
    tests/unit/test_skills_mgmt_safety.py                    passed
    tests/unit/test_skills_mgmt_lineage.py                   passed
    tests/unit/test_memory_abstractor_extreme_edge_cases.py  passed
    tests/integration/test_memory_abstractor_integration.py  passed
    tests/unit/test_holographic_adapter_concurrency.py       passed
    tests/unit/test_conflict_resolver.py                     34 passed
    tests/unit/test_agent_lines.py                           passed
    tests/unit/test_few_shot_injector.py                     17 passed
    tests/unit/test_det2_stable_sort_determinism.py          10 passed（DET-2 的守卫，未被本卡打红）
    tests/unit/test_det3_stable_sort_determinism.py          passed（DET-3 的守卫，未被本卡打红）
    tests/unit/test_settings_registry.py                     56 passed（= 基线 56；registry.py 未改）
    tests/unit/test_det4_stable_sort_determinism.py          24 passed（本卡新增）
    ---------------------------------------------------------------------------
    合跑：753 passed, 6 skipped, 1 xfailed, 0 failed（167.19s）

**中途真红过 3 条，已按「不放宽断言」的方式解决**（如实记录）：
本卡最初把 FILE_BASED_CATEGORIES 直接由 set 字面量改成元组，触发
tests/unit/test_routes_assets.py 的三条既有断言
（assert set(content.keys()) == FILE_BASED_CATEGORIES，:298 / :441 等）。
本卡**没有**改动那三条断言，而是改成「**有序元组当单一事实来源 + 由它派生同名集合**」
（FILE_BASED_CATEGORY_ORDER = (...) ; FILE_BASED_CATEGORIES = set(FILE_BASED_CATEGORY_ORDER)），
迭代点改用有序元组 ⇒ 断言一字未改、仍全绿，语义完全保留。

test_settings_registry.py 复核：**未新增任何 env**，registry.py sha256 = 30A704C2E7887474D87D9FBA8219BD99FFA331BBF773B62B1F4B1C5F3058E304（与开工前相同）。

---

## 6. 未验证项与残留风险

1. **端到端仍然不确定（最高优先的残留）**：真实蒸馏链 merge_results → solidify_to_workflow 的 tags
   **改后仍 5 种子 5 种次序**，根因是上游 agent/process_distill/merge.py:158（§1.5 漏网 33）。
   该文件**不在本卡文件范围**，故只登记。残留风险：**中**（顺序级；不影响成员，因为该路径 tags ≤5<8 不触发截断）。
   一句话改法已在 §4 附·态五给出。
2. **solidify 的「成员变」在生产链上是潜伏的**：只有当一个 DistilledProcess 携带 **≥6 个不同 tags** 时，
   [:8] 才会真的切掉成员。本卡的复现用的是 7 个 tags 的构造（DistilledProcess 是公开模型，
   solidify_to_workflow / solidify_to_skill 是公开函数）；当前两条生产构造路径（merge ≤5、digestion ≤3）
   都够不到截断点。**不要**把它读成「正在出错的 bug」，它是「能出错、已封死」。
3. **executor.py:617 的次级键选了字典序**，不是「候选汇合序」——因为 _ENV_WHITELIST 是 set 字面量，
   本身不存在汇合序；且**没有**改常量（避免打断 scripts/scan_settings.py 对
   agent/skills_mgmt/executor.py::_ENV_WHITELIST 的 PASS_THROUGH_SITES 具名命中，
   那条具名命中由 test_settings_registry.py::_NAMED_COLLECTION_SITES 锁死）。
4. **routes_assets 的「有序元组 + 派生集合」是取舍结果**：若把常量本身改成元组，会打红 3 条既有断言；
   本卡选择不动断言。代价是多了一个名字（FILE_BASED_CATEGORY_ORDER），由新测试
   test_iteration_uses_an_ordered_constant 锁死两者的派生关系。
5. **safety_guard 的出口是回调队列，不是 get_alerts()**：本卡按 app_server.py:980 →
   plugins/safety.py:161 → templates/index.html:3618 这条链判定为「到达」；
   **未做浏览器验证**（前端只验证到源码层面的 fetch + 渲染）。
6. **enhanced_planner 的到达性是「库 API 级」**：EnhancedTaskPlanner 在 agent/ 生产代码里
   **没有调用点**（只有 tests/integration 引用）。本卡按「公开 API 的返回值语义 + 行为级」判定为到达并修；
   若按「当前 HTTP 可达」的更严口径，它可以被归入态三。**这一点如实标注，不掩盖**。
7. **未跑**：向量腿就绪态（全程 AGENT_HYBRID_EMBEDDING=0，未加载任何模型）；
   UI 端未构建、未看渲染。
8. **未跑**：/api/assets/export、/api/skills-mgmt/health、/api/safety/alerts 的**真实 HTTP 端到端**
   （本卡是在进程内调用同一份函数 + 逐跳读码确认注册与出口；未起服务 —— 硬约束禁止起常驻服务）。
9. **共享工作区时序**：本卡开工时 agent/skills_mgmt/loader.py 的 sha256 是 45D5B358…，
   与 DET-2 报告里记录的 36E5DD02… 不同 ⇒ 该文件在 DET-2 之后被**别的卡**写过（DET-3 §6.8 亦记录）。
   本卡**未触碰**它，也未触碰 tool_router / tool_router_hybrid / few_shot_injector / assembler。

---

## 7. 回滚指令（定向；**不要**整文件 git checkout —— 这 8 个文件在共享工作区里）

本卡把「改动前文本」逐字节存成副本，且 sha256 与开工前生产位一致：

| 文件 | pre sha256（= 开工前生产位） | 改后 sha256（= 当前生产位） |
|---|---|---|
| agent/text_tools.py | A96ABBD043A5B9DF8C621724B659E9E2498934B721A3125F421515D300DD0541 | C1AE4B5FF8AF92CE77F85CF8EC37F5083E4A9B537507A2CBAC2711C90814BF88 |
| agent/process_distill/solidify.py | 1391A8E4263B624977D84F88B6B95F9A60C95CA34DB2DB7F522ABD2D4FF1D39F | CEC444D931FC1C9FBC1F1EF5284A9EA2AC8F0EA0A226DF476C18EC31514A8AB2 |
| agent/task_planner/enhanced_planner.py | DB1883CB49A071299D81BB5F5670D8BF49F332640FDD2AF315C067E14B0C201A | 220D931253ADEA3DC6D3ADB0377144F82AFE861CEEDFCE3D1215174D8DB8607A |
| agent/server_routes/routes_assets.py | 842BD0616E85A85DB9DBAEAE056BFB11803471C8BE13783955FA1A671F30C915 | DEB80037CB4C5F6ECABAAB328425EF571736B3E99C69BBD95B08E4B512C2A756 |
| agent/skills_mgmt/executor.py | F6336D8E98C74EB181FE015F35CED47D6B1FB061CD8821C3F09CB5245D98ADD2 | C403EC0686DE99D8A174974CE302EF237E1C76A656440A40946021A32AE34BC0 |
| agent/safety_guard.py | 82479B2C535593AC71964E1A37B2958AF58CAED52D41510FF2555C15D46FC70E | 3F6D89643CAD445046AD2487772280EE8F9219F11FD94E251D6E8B911BFDE098 |
| agent/skills_mgmt/store.py | 007106A3A2C64B4A93C9CABAEB52505586E6EDC55168281DDB6AD25AAB8E0C7B | CE5C24634C122C70906C98552CF56268F4F2E303A3DF234A8F310E741C8ACEE7 |
| agent/skills_mgmt/memory_abstractor.py | 45B000B9BADD3918493785E6C65BBC464EBE1D5BA3957351BFD2E02BF4CE61E9 | 8FB2E89CBAC7E6743ACF7F40D0B20BFFBA64D79E29E46E5FC3BF0AF580AD39A2 |

**方式 A（最快，等价于本卡从未发生）**：覆盖 pre 副本 + 删新增测试
（**行尾**：text_tools / solidify / enhanced_planner / executor / safety_guard / memory_abstractor 是 **CRLF**；
routes_assets / store 是 **LF**；pre 副本已按原样保存，直接 byte copy 即可）：

    copy %TEMP%\det4\pre\text_tools.py         <repo>\agent\text_tools.py
    copy %TEMP%\det4\pre\solidify.py           <repo>\agent\process_distill\solidify.py
    copy %TEMP%\det4\pre\enhanced_planner.py   <repo>\agent\task_planner\enhanced_planner.py
    copy %TEMP%\det4\pre\routes_assets.py      <repo>\agent\server_routes\routes_assets.py
    copy %TEMP%\det4\pre\executor.py           <repo>\agent\skills_mgmt\executor.py
    copy %TEMP%\det4\pre\safety_guard.py       <repo>\agent\safety_guard.py
    copy %TEMP%\det4\pre\store.py              <repo>\agent\skills_mgmt\store.py
    copy %TEMP%\det4\pre\memory_abstractor.py  <repo>\agent\skills_mgmt\memory_abstractor.py
    del  <repo>\tests\unit\test_det4_stable_sort_determinism.py

**方式 B（逐条反向替换，若副本丢失）**：正向 diff 在 %TEMP%\det4\my_det4.diff，反向读即得；
锚点唯一（每处都断言过出现次数 = 1）。

**回滚后的预期**（已实测，见 §3）：test_det4_stable_sort_determinism.py **24 条全红**；
text_tools 的 matches 恢复 5 种、solidify 的 tags 恢复 5 种（含成员差异）、
planner 的回退顺序恢复 5 种、routes_assets/executor/safety/store/memory_abstractor 各自恢复抖动。

---

## 8. 残留物自证

* **探针全部在仓库外**：`C:\Users\Administrator\AppData\Local\Temp\det4\`
  （det4runner.py、probe_*.py、drive_*.py、measure_impact.py、scan_member_shape.py、
  mutation_selfproof.py、apply_det4*.py、gen_det4_diff.py、pre\、post\、
  *.txt / *.diff / *.json / sub\）。仓库内**零**临时文件。
* **本卡在仓库里新增的文件只有 2 个**：tests/unit/test_det4_stable_sort_determinism.py（未跟踪，正常）
  与本报告 docs/audit_skill_governance/DET4.md（该目录本就未跟踪）。
  git status --porcelain 里与本卡相关的条目只有 8 条 " M" + 1 条 "??"（已逐条核对）。
* **本卡 diff 100% 是自己的**：8 个文件的 pre 副本与开工前生产位**逐字节相同**（sha256 见 §7）。
* **未触碰禁区**：agent/audit/、plugins/、agent/workflow_learning/、yunshu-ui/、data/skills_repo/、
  config.yaml、prompt 装配四件套 —— 一行未改（禁区文件 LastWriteTime 全部早于本卡开工）。
  **未动 data/audit/daily_roots.jsonl**（mtime 仍是 2026-09-26 10:07:00，早于本卡开工）。
* **未触碰前序卡生产位**：agent/tool_router.py = 83448BB9…53E7、agent/tool_router_hybrid.py = 53B3A41B…A61、
  agent/skills_mgmt/few_shot_injector.py = 9D18BD98…D96D、agent/lines/assembler.py = 07F28E28…D246
  —— 四者与 DET-2/DET-3 报告记录**逐位相同**；agent/skills_mgmt/loader.py（45D5B358…）本卡未改。
* **未新增 env**：agent/settings/registry.py sha256 = 30A704C2…E304（未改）；
  未启动任何常驻服务；未在仓库内建/写 sqlite（store/executor/holographic 探针全部指向 %TEMP% 下的临时路径）。
* **无 git 写操作**：未 git add、未 git commit、未做整文件 git checkout
  （唯一一次「装回旧版本」是 mutation_selfproof.py 的定向副本覆盖，且已 sha256 自证逐字节还原）。
* **无 python 残留进程**：跑完 (Get-Process python).Count == 0；
  **本卡没有 taskkill 任何进程**（全部是前台等待的子进程）。
* **行尾未被打乱**：逐文件核对了 CRLF 计数（text_tools 745→750、solidify 332→336、
  enhanced_planner 644→648、executor 622 不变、safety_guard 243 不变、memory_abstractor 1303→1334；
  routes_assets / store 全程 LF），且补丁只做**不含换行的字面替换 + 显式 CRLF 插入**。
  中途一次「多行补丁把换行拼没了」的事故（store 与 memory_abstractor）已**从 pre 副本定向还原后重做**，
  并用 ast.parse + 探针复跑确认（§3 的 sha256 即重做后的值）。
* **已知的仓库内自动产物（非本卡新增文件）**：test_reports/logs/test_*.log（pytest 报告钩子每次运行都写，
  12825 个，别的卡同样在写）、.pytest_cache/、__pycache__/。本卡跑测试一律加 -p no:cacheprovider，
  但 tests/unit/__pycache__/test_det4_*.pyc 是 pytest 导入新测试时的正常产物（已被 .gitignore 覆盖）。
