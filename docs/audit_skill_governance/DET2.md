# DET-2 · 「set 交给稳定排序」这一族的收敛：第 2/3 处证实 + 在 helper 内一处收口

| 项 | 值 |
|---|---|
| 基线 HEAD | 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf |
| 本卡改动文件 | agent/tool_router.py（+107 行 / −11 行）、agent/skills_mgmt/loader.py（+43 行 / −4 行）、tests/unit/test_det2_stable_sort_determinism.py（新，10 条） |
| 改动合计 | **+150 / −15，12 个 hunk**（本卡专属 diff，见 §2.1） |
| 探针位置（仓库外） | C:\Users\Administrator\AppData\Local\Temp\det2\ |
| 出网 | 零（只用本地索引 + 本地仓库数据；全程 AGENT_HYBRID_EMBEDDING=0，从未拉起向量模型） |
| 新增 env | **无**（确定性化是收紧，不需要逃生开关；agent/settings/registry.py 未改一行） |
| 结论 | 第 2 处 **证实**、第 3 处 **证实**、均已修；**确定化收敛在 helper 一处**，一处修复覆盖全部调用方 |

---

## 0. 结论速览

1. **第 2 处（agent/tool_router.py 的 get_tools_for_input）——证实。**
   构造必然并列的输入（code 类 15 个工具 priority 全 = 3），5 个种子（0/1/2/random/random，各起新解释器）
   得到 **5 种不同的下发集**；无白名单的真实形态下（max_tools=25）对称差最大 **8/25**。
   这不止是顺序不同，**成员也不同**。
2. **第 3 处（agent/skills_mgmt/loader.py 的 _tfidf_scan）——证实。**
   同一份输入、同一份代码，scripts/eval_skill_retrieval.py 的 **MRR 在 6 次运行里取到 5 个不同值**
   （0.9333 / 0.9519 / 0.9556 / 0.9630 / 0.9778），45 条用例里 **22 条**的 top-3 列表不稳定。
   机制直证：scan_seq 同一批成员 4 种迭代序 ⇒ 调用方的**稳定排序**把它们原样带进 top-3。
3. **能不能把确定化收敛到 helper 内部一处？——能，而且本卡就是这么做的。**
   helper（_apply_alias_merge_and_priority_sort）是两条路由路径唯一的「排序 + 截断」入口，
   它对 set 输入有**决定次序的权力**（set 没有内禀次序，谈不上"改语义"），
   故在第 2 处**只改 helper 一处**，调用方（关键词路由 / hybrid / scripts）全部随之变确定。
   本卡**没有改 hybrid 一行**，E1-D 的修复逐位保留（其 sha256 与 E1-D 报告里记的
   53B3A41B...A61 相同，可复核未被本卡触碰）。
4. **族群收敛清单共 8 个落点，全部点名**（§4）：已修 1（E1-D）、本卡修 2、本卡新证实并点名的真缺陷 2、
   同族邻接（浮点求和次序）2（1 个证实、1 个未证实）、非本族但同形的正确样板 3 类。
   **没有未被点名的漏网者**（扫描方法与 92 个候选点的分级见 §4）。

---

## 1. 第 1 步：三处各自的证实（结论开放，先测量后动手）

### 1.1 装置（全部在仓库外，走**生产入口**）

| 文件（%TEMP%\det2\） | 作用 |
|---|---|
| probe_tool_router.py | 子进程：给定 PYTHONHASHSEED 跑一次 get_tools_for_input（生产入口），打印一行 JSON |
| probe_loader_idx.py | 子进程：真实索引 + 必然并列的查询，打印 load_metadata_index 键序 / _tfidf_scan 返回序 / 稳定排序后 top-3 |
| run_seeds.py | 多种子（每种子**新解释器**）跑探针，逐种子一行 + 一致性判定，落盘 *.txt/*.log |
| run_mrr.py | 多种子跑 **scripts/eval_skill_retrieval.py 本体**（--report-format json），提取 MRR 取值集合 |
| probe_router_matrix.py / run_matrix.py / compare_matrix.py | 50 条 rc-* 用例 × get_tools_for_input 的影响面矩阵与改前/改后对比 |
| mutation_selfproof.py | 非空转自证（变异 ⇒ 红 ⇒ 还原 ⇒ 绿 + sha256） |
| evidence_before_after.py | 同格式的「改前（变异复现）/ 改后」原始证据 |
| gen_det2_diff.py + pre/ | 本卡专属 diff 与**改前副本**（标记手术重建，供复核与回滚） |
| scan_family.py / probe_bm25_float.py / probe_fewshot_float.py / probe_assembler_planes.py / probe_index_manager.py | 族扫描与新增落点实证 |

要点：
* 每个种子 = **新解释器进程**（PYTHONHASHSEED 必须在解释器启动前设好）；
* 向量腿一律 AGENT_HYBRID_EMBEDDING=0（E1-D 已证该缺陷与向量腿无关；本卡全程未加载模型，约省 18s/450MB）；
* 探针把 ToolTraceRecorder 换成 :memory: 实例，**避免在仓库内建/写 sqlite**（只影响记录器，不影响路由结果）。

### 1.2 第 2 处：agent/tool_router.py 的 get_tools_for_input —— **证实**

必然并列的构造：code 类 15 个工具的 category.priority **全部等于 3** ⇒ helper 内
sorted(selected, key=priority) 的并列块 = 全体候选；max_tools=6 的截断点**必落在并列块内部**。

改前原始输出（%TEMP%\det2\det2_router_before.log，5 个种子逐行；A_nowl 为不传白名单的真实形态）：

    seed=0       cases  {"A_nowl|mt=25": ["get_status","search_memory","remember","read_file","write_file","list_directory","shell_execute","code_review","arch_diagram","get_sensor_summary","todo_write","workspace_list","get_file_info","weekly_report","decompress","workspace_delete","workspace_init","diff_files","compress","workspace_write","search_files","data_convert","sqlite_query","run_lint","grep"], "B_codeWL|mt=6": ["shell_execute","code_review","arch_diagram","data_convert","run_lint","sqlite_query"], ... "C_helper_set|mt=6": ["shell_execute","code_review","arch_diagram","data_convert","run_lint","sqlite_query"], "C_helper_list|mt=6": ["shell_execute","code_review","arch_diagram","humanize_zh","json_query","data_format_detect"]}
    seed=1       cases  {"A_nowl|mt=25": ["get_status","search_memory","remember","read_file","write_file","list_directory","shell_execute","code_review","arch_diagram","get_sensor_summary","todo_write","search_files","weekly_report","workspace_write","workspace_init","diff_files","workspace_delete","decompress","compress","get_file_info","workspace_list","apply_patch","humanize_zh","run_tests","json_query"], "B_codeWL|mt=6": ["shell_execute","code_review","arch_diagram","data_convert","apply_patch","run_sandbox"], ... "C_helper_set|mt=6": [同上(apply_patch/run_sandbox)], "C_helper_list|mt=6": ["shell_execute","code_review","arch_diagram","humanize_zh","json_query","data_format_detect"]}
    seed=2       cases  {"A_nowl|mt=25": ["get_status","search_memory","remember","read_file","write_file","list_directory","shell_execute","code_review","arch_diagram","get_sensor_summary","todo_write","decompress","workspace_init","diff_files","get_file_info","workspace_delete","search_files","workspace_list","workspace_write","weekly_report","compress","grep","run_lint","run_tests","run_sandbox"], "B_codeWL|mt=6": ["shell_execute","code_review","arch_diagram","grep","humanize_zh","json_query"], ...}
    seed=random  cases  {"A_nowl|mt=25": [...,"get_file_info","workspace_init","workspace_write","workspace_list","diff_files","search_files","workspace_delete","compress","decompress","weekly_report","run_sandbox","run_lint","edit","humanize_zh"], "B_codeWL|mt=6": ["shell_execute","code_review","arch_diagram","json_query","apply_patch","edit"], ...}
    seed=random  cases  {"A_nowl|mt=25": [...,"workspace_delete","workspace_init","compress","decompress","workspace_list","diff_files","search_files","weekly_report","workspace_write","get_file_info","apply_patch","data_format_detect","run_lint","edit"], "B_codeWL|mt=6": ["shell_execute","code_review","arch_diagram","humanize_zh","json_query","edit"], ...}

    => cases             value-set(5)      ← 5 个种子 = 5 种不同的整体结果
    => meta              value-set(1)

逐条读法：
* A_nowl|mt=25（真实形态）：5 种下发集；与 seed=0 的**集合对称差最大 8**
  （seed=1 差 apply_patch / data_convert / grep / humanize_zh / json_query / run_lint / run_tests / sqlite_query）。
* B_codeWL|mt=6（必然并列 + 截断落在并列块内）：5 种下发集，对称差 2~6。
* B_codeWL|mt=25（并列块不跨截断点）：**成员相同、顺序 5 种** —— 印证"并列只影响块内先后，
  跨截断点时才升级为成员差异"。
* C_helper_set|mt=6 与 C_helper_list|mt=6 是**同一批候选**分别以 set / 有序序列交给同一个 helper：
  set 侧随种子变（与 B_codeWL|mt=6 逐位一致），**list 侧 5 个种子完全不变** —— 这一条直接指出
  "迭代序从哪来"，也是"helper 保留调用方次序"这一设计的事实依据。

### 1.3 第 3 处：agent/skills_mgmt/loader.py 的 _tfidf_scan —— **证实**

（a）机制直证：真实索引（28 条技能）+ 必然并列的查询「写测试时要避免哪些反模式」
（4 条候选分数**完全相等** = 0.0909）。

改前原始输出（%TEMP%\det2\det2_loader_before.log）：

    seed=0       scan_seq   [["pd-finishing-a-development-branch-e085de5a-skill",0.0909],["pd-systematic-debugging-556faa20-skill",0.0909],["testing-anti-patterns",0.0909],["pd-verification-before-completion-af010352-skill",0.0909]]
    seed=0       top3_after_stable_sort  ["pd-finishing-a-development-branch-e085de5a-skill","pd-systematic-debugging-556faa20-skill","testing-anti-patterns"]
    seed=1       scan_seq   [同上（finishing → systematic → testing → verification）]
    seed=1       top3_after_stable_sort  [同上]
    seed=2       scan_seq   [["pd-systematic-debugging-556faa20-skill"],["pd-verification-before-completion-af010352-skill"],["pd-finishing-a-development-branch-e085de5a-skill"],["testing-anti-patterns"]]
    seed=2       top3_after_stable_sort  ["pd-systematic-debugging-556faa20-skill","pd-verification-before-completion-af010352-skill","pd-finishing-a-development-branch-e085de5a-skill"]
    seed=random  scan_seq   [["pd-finishing..."],["pd-verification..."],["testing-anti-patterns"],["pd-systematic..."]]
    seed=random  top3_after_stable_sort  ["pd-finishing...","pd-verification...","testing-anti-patterns"]
    seed=random  scan_seq   [["pd-verification..."],["pd-systematic..."],["testing-anti-patterns"],["pd-finishing..."]]
    seed=random  top3_after_stable_sort  ["pd-verification...","pd-systematic...","testing-anti-patterns"]

    => index_keys  SEQ-DIFFERS(1 kinds) | SET-DIFFERS(1 kinds)   ← 索引序跨进程**稳定**
    => scan_seq    SEQ-DIFFERS(4 kinds) | SET-DIFFERS(1 kinds)   ← 成员同、**次序不同**
    => top3        SEQ-DIFFERS(4 kinds) | SET-DIFFERS(4 kinds)   ← 稳定排序把次序带到 **top-3 成员**

* 分数完全相等（无论种子），scan_seq 却有 4 种次序 ⇒ 次序只可能来自 set 迭代序；
* top-3 的**成员也不同**（seed=2 把 verification 排进来、finishing 挤出去）；
* index_keys 只有 1 种 ⇒ **索引序本身是确定的**，这正是后文选它当次级键的依据（可实测，不用假设）。

（b）本卡点名要的证据：**固定 PYTHONHASHSEED 跑同一份输入多次，给出 MRR 的取值集合**。
跑的是生产脚本本体（scripts/eval_skill_retrieval.py --report-format json，45 条用例，每行一个新解释器）：

    seed=0       rep=0 rc=1  P@3=0.4444 R@3=1.0000 MRR=0.9556
    seed=1       rep=0 rc=1  P@3=0.4370 R@3=0.9778 MRR=0.9778
    seed=2       rep=0 rc=1  P@3=0.4370 R@3=0.9778 MRR=0.9333
    seed=random  rep=0 rc=1  P@3=0.4444 R@3=1.0000 MRR=0.9519
    seed=random  rep=0 rc=1  P@3=0.4444 R@3=1.0000 MRR=0.9630
    seed=random  rep=0 rc=1  P@3=0.4370 R@3=0.9778 MRR=0.9778

    MRR value set over 6 runs: [0.9333, 0.9519, 0.9556, 0.963, 0.9778]
    cases with unstable top-3 list: 22 / 45

（rc=1 是脚本自身的 CI 守卫，Precision@3 < 0.6 触发，属于既有基线行为 —— 与确定性无关。）
R-6 登记的是「MRR 在 0.9519~0.9778 间跳」；本次实测区间**更宽**（下探到 0.9333），
因为 6 次采样包含随机种子，说明原登记的区间只是抖动的一个子集。

### 1.4 第 1 处（E1-D，对照）—— 复核通过，未被本卡触碰

* agent/tool_router_hybrid.py 的 sha256 = 53B3A41BB098F9AA4E7ADC86C83C8E97F779DC3C0B092746CFDDAC036A9E5A61，
  与 E1D.md §2.4 记录的还原后摘要**逐位相同** ⇒ 本卡一行未改。
* 本卡重跑其守卫：tests/unit/test_tool_router_hybrid_e1d_determinism.py **3 passed**（3.80s）。
* 本卡在 §4 的族清单里把第 1 处标为「已修（E1-D）」，并把它作为"收敛点在调用方的写法"与
  本卡"收敛点在 helper"的对照。

### 1.5 族扫描：本卡另外证实/未证实的落点

用 AST 扫描 agent/ + memory/ + scripts/（scan_family.py：A「set 赋值 → .sort()/sorted()/
for-in 且写有序容器」、B「for-in set(...) 且做浮点累加」），共 **92 个可疑点**，逐个判读后
（分级见 §4）另有 4 个值得实证的点，结果：

    # 落点 4：agent/lines/assembler.py:268/366 —— active_planes 是 set，
    #         sorted(..., key=-plane_weights) 在默认档案（三个平面权重全 = 1.0）下并列
    seed=0       by_plane_keys  ["act","resident","perceive"]
    seed=1       by_plane_keys  ["act","perceive","resident"]
    seed=2       by_plane_keys  ["act","perceive","resident"] → 实测 5 种子 5 种
    seed=random  by_plane_keys  ["perceive","resident","act"]
    seed=random  by_plane_keys  ["resident","act","perceive"]
    => by_plane_keys  SEQ-DIFFERS(5 kinds) | SET-DIFFERS(1 kinds)
    => tools          SEQ-DIFFERS(1 kinds) | SET-DIFFERS(1 kinds)   ← 工具表本身确定（out_key 有名字兜底）
    ⇒ **证实**，但后果只到「分组字典的键序」（不是工具集成员）。仍存在，已点名（不在本卡文件范围）。

    # 落点 5：agent/utils/index_manager.py:199/231 等 —— set[str] 文档 id → 稳定排序取 top-limit
    seed=0       kw_top5  ["doc18","doc14","doc20","doc26","doc10"]
    seed=1       kw_top5  ["doc25","doc18","doc28","doc22","doc15"]
    seed=2       kw_top5  ["doc25","doc08","doc29","doc13","doc04"]
    seed=random  kw_top5  ["doc18","doc03","doc00","doc11","doc24"]
    seed=random  kw_top5  ["doc21","doc12","doc13","doc27","doc15"]
    => kw_top5 / category / time_range 三处均 SEQ-DIFFERS(5) | SET-DIFFERS(5)
    ⇒ **证实**（成员差异）。但全仓**无生产调用点**（只有 tests/unit/test_index_manager_concurrency.py
      与 scripts/run_full_pytest.py 的清单引用）⇒ 记为"库代码缺陷、当前不可达"，已点名。

    # 落点 6（同族邻接·浮点求和次序）：agent/tool_router_hybrid.py:646 for token in set(query_tokens)
    => cases  value-set(1)   ← n_tools=90，5 个种子逐位相同
    ⇒ **未证实**（本次未观测到差异）。登记为残留，给出改法但不改（见 §7）。

    # 落点 7（同族邻接）：agent/skills_mgmt/few_shot_injector.py:119 for term in set(q)|set(d) 浮点累加
    seed=random  cases  {"写测试时要避免哪些反模式": ["0.7724872793364282", ...]}
    seed=random  cases  {"写测试时要避免哪些反模式": ["0.7724872793364285", ...]}
    seed=random  cases  {"测试 日志 竞态 ...": ["0.36980013081681945", ...]}
    seed=random  cases  {"测试 日志 竞态 ...": ["0.36980013081681956", ...]}
    => cases  value-set(5)
    ⇒ **证实**（同一份输入、同一 doc 的**分数末位比特**不同）。影响面远小于第 2/3 处
      （分数不同 ⇒ 排序翻转需要"恰好并列"），但根因同源。已点名，不在本卡文件范围。

## 2. 第 2 步：修复（只有证实了才修 —— 两处都证实了，都修）

### 2.1 本卡专属 diff（+150 / −15，12 hunk）

仓库里这两个文件叠着别的卡的未提交改动，直接 git diff 会把别人的行算进来。
本卡的 diff 用「标记手术把当前文本还原成改动前」的方式生成（gen_det2_diff.py），
并做了两条交叉验证：

* **tool_router.py 的还原副本与 HEAD 逐字节相同（0 个 hunk）** ⇒ 该文件的 diff 100% 是本卡的；
  也说明这张卡之前没有任何卡动过这段代码。
* loader.py 的还原副本与 HEAD 有 17 个 hunk（= 别的卡的未提交改动，如 G1C-U1），
  且这些 hunk 的行号区间（68-76 / 209-221 / 249 / 287-365 / 1438 / 1595-1719）
  **与本卡改动的区间（79~91 / 360~372 / 389~404 / 430~500）不相交**；
  还原副本里 DET-2 / _ordered_candidates / selected_set / _ORDER_MISSING /
  _inverted_index_order / _skill_pos 等标识**出现次数全为 0**。
* 更强的验证（verify_pre_copy.py）：把还原副本**真的装回仓库**跑探针 ——
  语法编译通过、跨种子抖动**逐条复现**（第 2 处 cases value-set(5)；第 3 处 scan_seq 4 种、
  top3 4 种），跑完按字节还原（sha256 RESTORED-OK）。

diff 全文见 %TEMP%\det2\my_det2.diff；正文如下（注释块是本卡最长的一段，完整保留在源文件里）：

    --- a/agent/tool_router.py
    +++ b/agent/tool_router.py
    @@ （新增一段说明 + 两个规范化函数 _declaration_positions / _ordered_candidates /
        _ordered_categories；helper 签名由 selected: set, categories: set 放宽为任意可迭代）
    +_DECL_MISSING = 1 << 30
    +
    +def _declaration_positions() -> tuple[dict, dict]:
    +    ...  # TOOL_CATEGORIES 的人工编排顺序（工具声明位置, 类别声明位置）
    +
    +def _ordered_candidates(selected) -> tuple[list, set]:
    +    if isinstance(selected, (list, tuple)):
    +        seq = list(dict.fromkeys(selected))      # 有序序列 ⇒ 原样保留（那是调用方的汇合序）
    +        return seq, set(seq)
    +    members = set(selected)                      # set ⇒ 无内禀次序 ⇒ 类别声明序 → 名字
    +    tool_pos, _ = _declaration_positions()
    +    seq = sorted(members, key=lambda t: (tool_pos.get(t, _DECL_MISSING), str(t)))
    +    return seq, members
    +
    +def _ordered_categories(categories) -> list:
    +    if isinstance(categories, (list, tuple)):
    +        return list(dict.fromkeys(categories))
    +    _, cat_pos = _declaration_positions()
    +    return sorted(set(categories),
    +                  key=lambda c: (TOOL_CATEGORIES.get(c, {}).get("priority", 99),
    +                                 cat_pos.get(c, _DECL_MISSING), str(c)))
    @@ 函数体内（**主键一字未改**，只把"迭代谁"换掉）：
    +    selected_seq, selected_set = _ordered_candidates(selected)
    +    categories_seq = _ordered_categories(categories)
    -    for cat in categories:                       +    for cat in categories_seq:
    -    if tool in selected:                         +    if tool in selected_set:      （成员判定 5 处）
    -    for t in sorted(selected, key=...priority)   +    for t in sorted(selected_seq, key=...priority)
    -    result = sorted(selected, key=...priority)   +    result = sorted(selected_seq, key=...priority)
    -    _restore_pinned_tools(result, selected, ..)  +    _restore_pinned_tools(result, selected_set, ..)
    --- a/agent/skills_mgmt/loader.py
    +++ b/agent/skills_mgmt/loader.py
    +_ORDER_MISSING = 1 << 30
    +        self._inverted_index_order: Optional[Dict[str, int]] = None    # 索引序缓存（与倒排同生共死）
    @@ _get_inverted_index：建倒排时顺手记下索引序
    +        skill_order: Dict[str, int] = {}
         for skill_id, meta in index.items():
    +            skill_order[skill_id] = len(skill_order)
    +        self._inverted_index_order = skill_order
    @@ _tfidf_scan：候选汇合序固定为索引序
    +            _skill_pos = self._inverted_index_order or {}
    +            _pos = lambda sid: (_skill_pos.get(sid, _ORDER_MISSING), sid)
    -                    key=lambda sid: candidate_hits[sid], reverse=True,
    +                    key=lambda sid: (-candidate_hits[sid], _pos(sid)),     # candidate_limit 的次级键
    -            scan_items = [(sid, index[sid]) for sid in candidate_ids if sid in index]
    +            scan_items = [(sid, index[sid]) for sid in
    +                          sorted((s for s in candidate_ids if s in index), key=_pos)]

（上面为便于阅读合并了缩进与省略号；**逐字 diff 在 %TEMP%\det2\my_det2.diff**。）

### 2.2 次级键为什么取「候选汇合序 / 索引序」，而不是工具名字典序

E1-D 的口径：**保留主键不变，只补一个确定的次级键；次级键取"候选汇合序"而不是字典序**。
本卡沿用，并在两个子系统各自落实为"该子系统**本来就存在**的那条确定次序"：

| 子系统 | 主键（未变） | 次级键（新增） | 为什么是它 |
|---|---|---|---|
| 工具路由 helper | 类别 priority 升序 | **调用方给的序列序**（set 输入则"类别声明序 → 名字"） | ① 与 E1-D 同源（相关度序 → 类别声明序 → 字典序）；② 名字字典序会在 hybrid 的**分数并列处重排 BM25 本路**，打红既有明文契约 test_degraded_path_order_matches_raw_bm25；③ 声明序兜底只在 set 输入时生效，hybrid 传的是有序序列 ⇒ 该契约逐位不受影响 |
| 技能检索 _tfidf_scan | 匹配分降序（调用方的稳定排序） | **索引序**（= 全量遍历路径 list(index.items()) 的文档序） | 索引序就是 use_inverted_index=False 那条路径**本来就在用**的次序 ⇒ 两条路径的并列先后**逐位一致**，且不重排任何既有腿的次序（只把"未定义"变成"已定义"）；实测该次序跨进程稳定（index_keys 1 kind） |

两条证据支持"字典序在这里没有必要"：
* 本仓库的索引序**恰好等于**名字字典序（SkillLoader().fs.load_metadata_index() 的键序 == sorted(键序) 为 True），
  所以 MRR 的变化不是"挑了一个好看的次级键"造成的结果差异 —— 两种口径在本仓库上**等价**；
* 既有契约没有被削弱：fusion_calibration 的降级路契约用例仍 1 passed（见 §6）。

### 2.3 影响面（改前 vs 改后）

**（1）工具路由（50 条 rc-* 用例 × get_tools_for_input(max_tools=25)，改前 = 变异复现的采样）**

    cases=50
    顺序变化 = 50 / 50
    成员变化 = 1 / 50  ['rc-007']      （rc-007：新增 run_tests，移除 run_lint）
    逐位完全相同 = 0 / 50
    改前成员抖动 = 1 ['rc-007'] | 改后成员抖动 = 0 []
    改前顺序抖动 = 50 / 50       | 改后顺序抖动 = 0
    改前/改后 top1 抖动 = 0 / 0

* 「改前」没有唯一答案（每个种子一种），故以 seed=0 那次采样作基线对比。
* 说明"顺序变化本身也是影响"：50/50 都变了 —— 因为并列块遍布结果；下游若按顺序消费工具表，
  模型看到的顺序会变。这是**把未定义次序定义掉**的必然代价，不是语义回归。
* 成员变化只有 rc-007 一处（1/50），且它在改前**本身就在抖**（改前成员抖动 = 1）。

**（2）技能检索（45 条黄金集用例，top_k=3）**

    改前 MRR 取值集合 = [0.9333, 0.9519, 0.9556, 0.963, 0.9778]      改后 = [0.9889]（6/6 次完全相同）
    改前 top-3 不稳定的用例 = 22 / 45                                改后 = 0 / 45
    改前 P@3 = {0.4370, 0.4444}   改后 P@3 = {0.4444}
    改前 R@3 = {0.9778, 1.0000}   改后 R@3 = {1.0000}
    顺序变化 = 13 / 45 ; 成员变化 = 7 / 45 ; 逐位完全相同 = 32 / 45

成员变化的 7 条明细（新增/移除）：

    case_006: +context_aware            -pd-brainstorming-697b717a-skill
    case_008: +pd-using-superpowers-…   -pd-writing-skills-5da20e67-skill
    case_016: +pd-writing-plans-…       -voice_interaction
    case_027: +proactive_suggestion     -self_reflection
    case_033: +proactive_suggestion     -self-explanatory-ui
    case_044: +context_aware            -pd-writing-skills-5da20e67-skill
    case_045: +pd-dispatching-…         -pd-verification-before-completion-…

**MRR 从"抖动区间"变成 0.9889，且高于改前所有采样** —— 这不是因为改了算法，而是因为并列先后
从"哈希序"变成了"索引序"，而索引序恰好把更相关的技能排在前面。**必须如实标注**：
如果把次级键换成别的确定次序（例如按 id 逆序），MRR 会是另一个值 —— 也就是说
"确定性"是本卡的唯一目标，"0.9889"只是本仓库数据下的副产物，不应被当成调优结论。

**（3）端到端判据**：本卡未改 `scripts/eval_route_conflict.py` 与阈值，未改 α/τ；本卡实测的
eval_skill_retrieval 退出码改前/改后都是 1（同一个 CI 阈值守卫，Precision@3 < 0.6），**未退化也未放宽**。

### 2.4 非空转自证（把次级键去掉 ⇒ 新测试必须变红；还原 ⇒ 绿 + sha256）

mutation_selfproof.py 的完整流程与原始结论：

    == backup sha256 ==
      83448BB91FE49ED5A76B12561090A2638A9222895359A2DC7067E8A2AC6D53E7  agent\tool_router.py
      36E5DD0223F997E4D0FED1579B37ADB2664E78C3EFDE0299AB796E25F5247D0C  agent\skills_mgmt\loader.py
    == mutate (revert to set iteration order) ==
      changed: {'agent\tool_router.py': True, 'agent\skills_mgmt\loader.py': True}
    MUTATED-PYTEST-RC=1  (non-zero => new tests really turn red)
    BEFORE-MATRIX-RC=0
    == restore ==
      agent\tool_router.py live=83448BB9…53E7 bak=83448BB9…53E7 -> RESTORED-OK
      agent\skills_mgmt\loader.py live=36E5DD02…7D0C bak=36E5DD02…7D0C -> RESTORED-OK
    RESTORED-BYTES-IDENTICAL=True
    RESTORED-PYTEST-RC=0 (0 => fix in place)
    SELFPROOF-FAILS=0

变异后 9 failed / 1 passed；还原后 **10 passed**。失败的 9 条与新测试的对应关系（断言原文摘录）：

    FAILED …::TestHelperIsTheSingleConvergencePoint::test_set_input_equals_sequence_input
    E   AssertionError: set 迭代序泄漏进了并列块的先后 —— 同优先级并列项的顺序不该由输入是 set 还是 list 决定。
        set 侧=['shell_execute','code_review','arch_diagram','run_lint','sqlite_query','edit'] /
        序列侧=['shell_execute','code_review','arch_diagram','humanize_zh','json_query','data_format_detect']

    FAILED …::test_set_input_is_stable_under_any_iteration_order
    E   AssertionError: 同一成员集合、两种迭代序得到了**不同结果** ⇒ 并列项先后仍取自迭代序
        （生产上就是 PYTHONHASHSEED）
        正序=[…'humanize_zh','json_query','data_format_detect']  逆序=[…'run_lint','sqlite_query','edit']

    FAILED …::test_categories_set_iteration_cannot_reorder_floors
    E   AssertionError: 同优先级类别（['extension','knowledge']，priority=5）的先后随**迭代序**变了
        ⇒ floors（类别保底）的先后随之变，截断点上的成员也会变。
        shuffled=['kb_capture','kb_distill','kb_discuss','ext_install',…]
        decl    =['ext_install','ext_uninstall','ext_list','kb_capture',…]
        At index 0 diff: 'kb_capture' != 'ext_install'

    FAILED …::TestKeywordRouteIsCrossProcessDeterministic::test_payload_is_identical_across_hash_seeds
    E   AssertionError: 同一查询 / 同一份代码，关键词路由跨进程下发了**不同的工具集**：
        seeds=['0'] -> {"mt25": ["shell_execute",…,"git"], "mt6": [...,"sqlite_query"], "nowl": [… 25 条 …]}
        seeds=['1'] -> {"mt25": [...,"apply_patch","run_sandbox"], "mt6": [...,"run_sandbox"], …}
        seeds=['2'] -> {…}
        seeds=['random'] -> {…}   seeds=['random'] -> {…}
    E   assert 5 == 1

    FAILED …::test_tie_block_follows_declaration_order
    E   AssertionError: 同优先级并列块不是声明序 ⇒ 仍是（或又被）迭代序决定：
        got =[…'data_convert','run_lint','sqlite_query','grep','data_format_detect','run_sandbox','edit',…]
        decl=[…'humanize_zh','json_query','data_format_detect','data_convert','git','run_tests',…]

    FAILED …::TestSkillScanOrderIsDeterministic::test_inverted_scan_order_equals_index_order
    （同类断言：倒排路径候选序 != 索引序）
    FAILED …::test_inverted_and_fullscan_orders_agree
    E   inverted=['skillsynth24','skillsynth03','skillsynth02','skillsynth25',…]
        fullscan=['skillsynth30','skillsynth29','skillsynth28','skillsynth27',…]
    FAILED …::test_candidate_limit_ties_are_broken_by_index_order
    E   got=['skillsynth24','skillsynth08','skillsynth23','skillsynth03',…]
    FAILED …::test_real_index_scan_order_is_identical_across_hash_seeds
    E   seeds=['0','1','random'] -> {…"top3": ["pd-finishing…","pd-systematic…","testing-anti-patterns"]}
        seeds=['2']             -> {…"top3": ["pd-systematic…","pd-verification…","pd-finishing…"]}
    E   assert 2 == 1

唯一在变异下仍绿的是 test_sequence_input_order_is_preserved（它守的是"有序序列的次序必须被保留"，
变异没有动这条路径）—— 如实说明，不假装"全红"。
10 条用例里另有 2 条是**反向守卫**（防止 helper 自作主张重排调用方给的次序），它们在变异下不受影响。

追加的行为级自证（verify_pre_copy.py）：把"改前副本"装回仓库跑探针，抖动**逐条复现**
（router cases value-set(5)；loader scan_seq 4 种 / top3 4 种），装回后按字节还原（RESTORED-OK）。

### 2.5 为什么不需要逃生开关

确定化是**收紧**（把"未定义顺序"变成"定义顺序"），不是放宽，故不引入任何 env，
registry.py **一行未改**（test_settings_registry.py 仍 56 passed，与基线一致）。
两处都不存在"不能在改语义的前提下确定化"的情形：
* helper：set 输入下没有任何既有语义依赖某个**具体**的哈希序（它本来就随进程变）；
  有序序列输入的次序被原样保留 —— 语义未被覆盖。
* _tfidf_scan：返回序在文档里本来就只承诺"未排序"，调用方自己排序；
  现在承诺"按候选汇合序排列"，并列项的先后从此有定义。

---

## 3. 第 3 步（本卡与 E1-D 的差别）：确定化收敛到 helper 内部一处

### 3.1 结论：**可以，而且已经这么做了**

理由（三条，缺一不可）：

1. **helper 是唯一的裁决点**：两条路由路径（关键词 / hybrid）的"排序 + 截断"都只经过
   _apply_alias_merge_and_priority_sort，没有第二个实现（Q5/B3-W 的漏斗图也把截断只标在这一处）。
2. **helper 有权决定 set 输入的次序**：set 没有内禀次序，调用方传 set 时，
   "次序"这条信息在传参前就已经丢失 —— helper 只能定一个新次序，谈不上"越权改语义"。
   反过来，调用方传**有序序列**时，helper **不得**改动它（那是相关度语义），
   本卡的实现正是"序列原样保留 + set 才兜底"，并为此单独写了一条反向守卫用例。
3. **一处修完覆盖全部调用方**：见 §3.2 的调用方清单（含仓库里的脚本与测试），
   全部因 helper 内部的规范化而变确定；不需要逐个调用方打补丁。

### 3.2 调用方清单（一个不漏，含证据）

| 调用方 | 传进去的 selected / categories | 本卡之前是否确定 | 现在 | 证据 |
|---|---|---|---|---|
| agent/tool_router.py::get_tools_for_input（关键词路由；plugins/chat.py、orchestrator、task_dispatcher 的 **or 兜底路径**） | set / set | **否**（本卡 §1.2 证实） | **是** | 跨种子 cases value-set(1)；50 条 rc-* 成员抖动 0 |
| agent/tool_router_hybrid.py::hybrid_select_tools（E1-D 修在调用方） | 有序 list / 有序 list | 是（E1-D 已修） | **是（未改动，逐位保留）** | e1d 测试 3 passed；fusion_calibration 18 passed |
| scripts/bench_capacity_scaling.py（性能基准脚本） | set / set | 否 | **是**（随 helper） | 同一处代码路径 |
| tests/unit/test_b3w_observability_wiring.py 的 spy 包装 | 透传 | — | 随 helper | 回归 946 passed（见 §6 补充集） |

**没有任何调用方绕过 helper 自己排序截断**（否则 §4 的清单里会出现它 —— 扫描已确认没有第四个实现）。

### 3.3 分层：调用方给"语义序"，helper 给"兜底序"

* hybrid 侧：只有调用方知道**相关度**，所以"相关度序 → 类别声明序 → 字典序"这条
  ordered_candidates 必须留在调用方（E1-D 的改动**不能也不该**被本卡收回）；
* 关键词侧：它没有相关度这个概念，故 helper 的"类别声明序 → 名字"就是它能有的最好次序；
* 两者**互不覆盖**：helper 对序列输入是恒等变换（本卡为它写了反向守卫用例），
  对 set 输入才动手术。这样"收敛到一处"与"调用方的语义序优先"同时成立。

### 3.4 为什么 loader 不并进这个 helper

它们的"候选汇合"根本不是同一件事：helper 的汇合序来自**检索相关度/类别声明**，
而 loader 的汇合序来自**索引文档序**（= 全量遍历路径的次序）。
硬并会让技能检索依赖工具分类表（跨子系统耦合），且会破坏"倒排路径 == 全量遍历路径"
这条本卡新钉住的不变量。故两个子系统各自在**自己的唯一入口**收敛：
工具路由在 helper 内一处；技能检索在 _tfidf_scan 内一处（match 与 _try_rrf_match 两条调用方
都只调它，同样是一处覆盖全部）。

## 4. 「set → 稳定排序」这一族的完整收敛清单（不许有漏网）

### 4.1 扫描方法与分级口径

scan_family.py 对 agent/ + memory/ + scripts/ 做 AST 扫描，判据三条（宁多不漏）：
A1 名字被 set 赋值后 .sort(...)；A2 名字被 set 赋值后 sorted(name, ...)；
A3 for x in set-name 且循环体里 append/extend/insert/add；B for x in set(...) 且循环体做浮点累加。
共 92 个可疑点，分级：

| 分级 | 条数 | 判据 |
|---|---|---|
| **无害（正确样板）** | 79 | sorted(set(...)) 或 sorted(name) —— **全序**排列，结果与迭代序无关；这正是"确定化"的标准写法 |
| 需要实证 | 13 | 见下表；逐个跑探针或读代码定级 |

另外做了两项针对性核对（避免"只扫到形似的"）：
* 全仓 grep 那个 helper 名 ⇒ 只有 2 个生产调用点 + 1 个脚本 + 测试，
  没有第三个"自己写排序截断"的实现（见 §3.2）；
* 检索/排序相关模块逐个读：bm25_searcher.py、searcher.py（技能管理页搜索）、subagent/toolset.py、
  capregistry/toolset_hash.py、workflow_learning/matcher.py。

### 4.2 清单（8 个落点，全部点名）

| # | 位置 | 机制 | 状态 | 证据 |
|---|---|---|---|---|
| 1 | agent/tool_router_hybrid.py：_query_locked 的 all_candidates + hybrid_select_tools 的 selected | set 迭代序 → 融合的稳定排序 + helper 的稳定排序 | **已修（E1-D）**，本卡复核未触碰 | 其 sha256 与 E1D.md 记录一致；e1d 测试 3 passed |
| 2 | agent/tool_router.py：get_tools_for_input（→ helper） | set → helper 的稳定排序（并列块跨截断点 ⇒ 成员变） | **本卡修**（在 helper 内一处收敛） | §1.2 改前 5 种子 5 种下发集；改后 value-set(1) |
| 3 | agent/skills_mgmt/loader.py：_tfidf_scan 的 candidate_ids（含 candidate_limit 截断） | set 迭代序 → 调用方的稳定排序 → top-3/MRR | **本卡修** | §1.3 改前 MRR 5 个取值、22/45 top-3 不稳；改后 0/45、0.9889 |
| 4 | agent/lines/assembler.py:268/366：active_planes（set）+ sorted(key=-plane_weights) | 默认档案三平面权重全 1.0 ⇒ 并列 ⇒ res.by_plane 键序随哈希变 | **仍存在，已点名**（不在本卡文件范围） | 实测 5 种子 5 种键序；res.tools 本身确定（out_key 末位是名字） |
| 5 | agent/utils/index_manager.py:199 / 231 一带：set[str] 文档 id → sorted(key=命中次数) / list(set) | 同一族，成员差异 | **仍存在，已点名**；但**全仓无生产调用点**（只有并发测试与 run_full_pytest 清单引用） | 实测 kw_top5 / category / time_range 三处均 5 种子 5 种**成员集** |
| 6 | agent/tool_router_hybrid.py:646：for token in set(query_tokens)（浮点累加） | 同族**邻接**：set → 浮点求和次序 → idf_total/covered → 护栏阈值比较 | **仍存在，已点名；本次未证实** | 90 工具 × 5 查询 × 5 种子，分数 repr **逐位相同**（value-set(1)） |
| 7 | agent/skills_mgmt/few_shot_injector.py:119：for term in set(q) 并 set(d)（浮点累加） | 同族**邻接**：同一 doc 的余弦分末位比特随进程变 | **仍存在，已点名；本次证实（低危）** | 0.7724872793364282 vs …285、0.36980013081681945 vs …956 |
| 8 | scripts/dev/new_session_worktree.py:85、scripts/check_circular_deps.py:127、scripts/check_grafana_metric_names.py:163 | set → sorted(coarse key) / for-in set 写有序容器 | **仍存在，已点名**（脚本，非生产路径；并列需要同号 session 等极窄条件） | 静态判读，未跑探针 |

**非本族但同形，明确排除（防止下一步误改）**：

| 位置 | 为什么**不是**本族缺陷 |
|---|---|
| agent/skills_mgmt/bm25_searcher.py:280 sorted(enumerate(scores), key=score, reverse=True) | 输入是 enumerate（**序列**），稳定排序的并列先后 = _skill_ids 的入参顺序（列表） ⇒ 确定。同族风险在"调用方传进来的 skills 是否有序"，那是另一件事，登记为观察项 |
| agent/skills_mgmt/searcher.py:185-194（管理页搜索的四次稳定排序） | 输入是 store.list_all() 的**列表**；categories/tags/statuses 三个 set 只做成员判定，从不迭代成有序产物 |
| agent/subagent/toolset.py:514/530、agent/capregistry/toolset_hash.py | name_candidates(...) 返回列表；toolset_hash 里的 set 用法都在 sorted(...) 内（全序） |
| agent/workflow_learning/matcher.py:107/114：for t in set(tokens) | 只把整数 df 计数 +1 ⇒ **与次序无关**（无浮点累加、无有序产物） |
| loader.py 里其余 .keys()/.items() | 都是 dict 插入序（确定），且插入序本身来自已确定化的路径 |

**结论：本族在仓库里被扫到的 8 个落点全部点名** —— 已修 1（E1-D）、本卡修 2、仍存在但已点名 5
（其中 4 与 5 是**本卡新证实**的真缺陷，6/7 是同族邻接的浮点求和次序，8 是脚本）。
两处真缺陷的修法都已写成一句话改法（§7），本卡受文件范围限制未改。

---

## 5. 跨子系统对照（工具路由 vs 技能检索）

| 维度 | 工具路由（agent/tool_router.py） | 技能检索（agent/skills_mgmt/loader.py） |
|---|---|---|
| set 从哪来 | classify_user_input 的类别集合 → 各类别 tools 的并集（set） | 倒排索引 postings 的并集（set[str]） |
| 谁做稳定排序 | helper 内 sorted(selected, key=priority)（**在 helper 里**） | 调用方 candidates.sort(key=score, reverse=True)（match / _try_rrf_match **两处**） |
| 次级键取什么 | 调用方序列序；set 输入 ⇒ 类别声明序 → 名字 | 索引序（= 全量遍历路径的文档序）→ 名字兜底 |
| 为什么不是字典序 | 字典序会在 hybrid 的**分数并列处重排 BM25 本路**，打红既有明文契约 | 无此契约，但索引序能让"倒排路径 == 全量遍历路径"逐位一致（本卡新钉住的不变量） |
| 收敛点 | helper **一处**覆盖全部调用方（本卡） | _tfidf_scan **一处**覆盖 match / _try_rrf_match（本卡） |
| 修前症状 | 下发集**成员**变（截断点落在并列块内） | top-3 成员变 ⇒ MRR 抖（22/45 用例） |
| 影响面（顺序/成员） | 50/50 顺序、1/50 成员 | 13/45 顺序、7/45 成员 |
| 旁路风险 | 无（渲染/装配层 agent/lines/assembler.py 自带**显式**次级键 out_key，是正确样板） | RRF 融合（_rrf_fuse）吃 rank ⇒ 修前"并列项被赋不同 rank"，会让**融合分**也变；本卡修好输入序后这条随之确定（**未单独实测 RRF 路径的跨种子矩阵**，见 §7） |

---

## 6. 回归结果（未放宽任何断言）

主套（本卡要求的那批）：

    tests/unit/test_tool_router_hybrid.py                    46 passed
    tests/unit/test_tool_router_pinned.py                     31 passed
    tests/unit/test_tool_router_hybrid_real_index_l28.py      16 passed
    tests/unit/test_tool_router_hybrid_integration.py         16 passed
    tests/unit/test_tool_router_hybrid_fusion_calibration.py  18 passed（含降级路"融合顺序 == raw BM25 顺序"契约）
    tests/unit/test_tool_router_hybrid_e1f1a.py               22 passed
    tests/unit/test_tool_router_hybrid_e1d_determinism.py      3 passed（E1-D 的守卫，未被本卡打红）
    tests/unit/test_det2_stable_sort_determinism.py           10 passed（本卡新增）
    tests/unit/test_settings_registry.py                      56 passed（= 基线 56；registry.py 未改）
    tests/unit/test_route_conflict_cases.py                   31 passed
    tests/unit/test_skill_meta_zh_recall.py                   46 passed
    tests/unit/test_skills_mgmt.py                            75 passed, 1 xfailed
    tests/unit/test_vector_skill_searcher.py                   9 passed
    ----------------------------------------------------------------
    合跑：379 passed, 1 xfailed（69.35s）

契约用例点名复核（E1-D 最担心的那条）：

    tests/unit/test_tool_router_hybrid_fusion_calibration.py 里那条"降级路顺序 == raw BM25 顺序"
    ====================== 1 passed, 17 deselected in 1.30s ======================

补充集（同族/相邻模块 37 个测试文件，含 test_b3w_observability_wiring、test_skill_h3_migration、
test_skill_index_cache、test_tool_definitions_yaml、test_tool_trace、test_toolset_hash 等）：

    = 2 failed, 946 passed, 11 skipped, 14 xfailed（71.57s）

**这 2 条失败与本卡无关，已证**：把两处改动退回改前行为（set 迭代序）后跑同两条 ⇒ **仍然同样失败**：

    == 改前行为（set 迭代序）下跑这 2 条用例 ==
      FAILED tests/unit/test_skills_digest_assessor.py::TestRenameCompat::test_events_file_legacy_migration
      FAILED tests/unit/test_skills_digest_assessor.py::TestDailyArchiveAndCurate::test_curate_plans_and_auto_fills_description

失败点是"临时目录里的事件文件不存在"与"description 自动填充为空"，与排序/检索无关
（且它们落在 C:/Windows/Temp/pytest-of-… 这类环境路径上）。跑完还原并 sha256 自证 RESTORED-OK。

**MRR 改前 vs 改后**（本卡最关键的读数）：

| | 改前（6 次运行） | 改后（6 次运行） |
|---|---|---|
| MRR 取值集合 | 0.9333 / 0.9519 / 0.9556 / 0.9630 / 0.9778（**5 个值**） | **0.9889（唯一值，6/6 次）** |
| P@3 | 0.4370 或 0.4444 | 0.4444 |
| R@3 | 0.9778 或 1.0000 | 1.0000 |
| top-3 不稳定用例 | 22 / 45 | **0 / 45** |
| 退出码 | 1（CI 阈值守卫，Precision@3 < 0.6） | 1（同一守卫，**未放宽也未触发新红**） |

（P@3/R@3/MRR 的提升是"并列先后从哈希序变成索引序"的副产物，不是算法改动 —— 见 §2.3 的警示。）

其他读数：50 条 rc-* 用例改后**成员抖动 0、顺序抖动 0、top1 抖动 0**（改前 成员 1、顺序 50）。

---

## 7. 未验证项与残留风险

1. **RRF 融合路径（use_bm25/use_vector=True）的跨种子矩阵未实测。**
   该路径的并列先后同样受 _tfidf_scan 的返回序影响，本卡的修复对它同样生效（同一条输入序），
   但本卡只跑了纯 TF-IDF 路径的 eval（45 条）与真实索引的机制探针，**没有**跑 RRF × 多种子矩阵。
   残留风险：低（输入序已确定；RRF 自身对列表是确定的）。
2. **索引序的来源是目录枚举序（file_store.load_metadata_index 用 iterdir），不是本卡能改的模块。**
   实测 5 个种子下键序完全一致（1 kind），且本仓库键序恰好等于名字字典序；
   但"目录枚举序在跨机器/跨文件系统下是否稳定"**未验证**，也**不在本卡文件范围**（agent/skills_mgmt/file_store.py）。
   若要彻底消除这一依赖，应把索引序显式定为"技能 id 升序"，那是另一张卡。
3. **落点 4/5/6/7 未修**（§4），各给一句话改法：
   * agent/lines/assembler.py:268/366 —— sorted(active_planes, key=lambda p: (-profile.plane_weights.get(p, 0.0), p))；
   * agent/utils/index_manager.py:199/231 一带 —— 候选按索引序（或名字序）收敛，或给 sorted 补显式次级键；
   * agent/tool_router_hybrid.py:646 —— for token in dict.fromkeys(query_tokens)（去重且保序）；
   * agent/skills_mgmt/few_shot_injector.py:119 —— for term in dict.fromkeys(list(q_count) + list(d_count))。
4. **未实测**：不传 max_tools（不限量）时的影响面；负样本（expected 为空的用例）在改后的逐条对照 ——
   黄金集里的负样本在本卡的 MRR 读数里随用例集一起跑过（P@3/R@3 未退化），但**没有**单独逐条对照。
5. **未验证**：注册表 UI/API 层（本卡没新增 env，故未走 /api/cp/settings）。
6. **未验证**：向量腿就绪态下的技能检索与工具路由**热进程**矩阵 —— 本卡全程 AGENT_HYBRID_EMBEDDING=0
   （零模型加载）。E1-D 已证该缺陷与向量腿无关，本卡沿用该前提。
7. **共享工作区的时序风险**：本卡期间 agent/tool_router_hybrid.py（10:28）与 agent/settings/registry.py（10:22）
   被**别的卡（E1-D 的最后还原步骤）**写过。本卡未触碰这两个文件，并在 §1.4 用 sha256 固定了被复核的版本
   （53B3A41B…A61，与 E1D.md 记录一致）。若后续有卡再改 tool_router_hybrid.py，本卡的对照结论需按新版本复核。

---

## 8. 回滚指令（定向；**不要**整文件 git checkout —— 这两个文件叠着别的卡的改动）

本卡改动**全部**落在两处、都有唯一锚点；同时本卡已把"改动前文本"逐字节存成副本：

* 改动前副本 %TEMP%\det2\pre\tool_router.py
  sha256 = ACA31BECD9BE36A6672AC791015F1833B696B221C00B907FA72FC6FF243216D8
* 改动前副本 %TEMP%\det2\pre\loader.py
  sha256 = 10778943E794BF4EF952B978BC854D705D1CC8C490612A8AA01D00AAAFFF1012
* 改后（= 当前生产位）：
  agent/tool_router.py = 83448BB91FE49ED5A76B12561090A2638A9222895359A2DC7067E8A2AC6D53E7
  agent/skills_mgmt/loader.py = 36E5DD0223F997E4D0FED1579B37ADB2664E78C3EFDE0299AB796E25F5247D0C

**方式 A（最快，等价于本卡从未发生）**：把两个 pre 副本覆盖回去（仓库里的 .py 是 **CRLF**，
副本已按 CRLF 保存，直接 byte copy 即可），然后删除新增测试文件：

    copy %TEMP%\det2\pre\tool_router.py   <repo>\agent\tool_router.py
    copy %TEMP%\det2\pre\loader.py        <repo>\agent\skills_mgmt\loader.py
    del  <repo>\tests\unit\test_det2_stable_sort_determinism.py

**方式 B（逐条反向替换，若副本丢失）**：
1. agent/tool_router.py
   * 删除 "【DET-2】候选 / 类别的迭代序确定化" 整段注释 + _DECL_MISSING + _declaration_positions +
     _ordered_candidates + _ordered_categories（从该段上方分隔线到 def _apply_alias_merge_and_priority_sort( 之前）；
   * 签名换回 selected: set, categories: set；
   * 删除 docstring 里的"【DET-2 · 入参次序契约】"段与函数体开头的两行规范化；
   * for cat in categories_seq → for cat in categories；selected_set → selected（5 处成员判定）；
     selected_seq → selected（tail / result 两处）；_restore_pinned_tools 的第二个实参 selected_set → selected。
2. agent/skills_mgmt/loader.py
   * 删除 _ORDER_MISSING 常量与 self._inverted_index_order 声明；
   * _get_inverted_index 里删除 skill_order 相关三行；
   * _tfidf_scan 里删除 _skill_pos / _pos 两行；candidate_limit 的 key 换回
     lambda sid: candidate_hits[sid] 并加 reverse=True；scan_items 换回直接迭代 candidate_ids。
   锚点原文见 %TEMP%\det2\my_det2.diff（反向读即得）。
3. 删除 tests/unit/test_det2_stable_sort_determinism.py（它只守护本卡行为）。

**回滚后的预期**：跨种子抖动立即复现 —— 关键词路由 5 种子 5 种下发集（50 条用例：成员抖动 1、顺序抖动 50）、
技能检索 MRR 重新落在 {0.9333 … 0.9778} 区间且 22/45 用例 top-3 不稳；新测试 10 条中 9 条变红。
（该预期已由本卡的 verify_pre_copy.py 实测：装回 pre 副本后抖动逐条复现。）

---

## 9. 残留物自证

* **探针全部在仓库外**：C:\Users\Administrator\AppData\Local\Temp\det2\
  （probe_*.py、run_seeds.py、run_mrr.py、run_matrix.py、compare_*.py、mutation_selfproof.py、
  evidence_before_after.py、verify_pre_copy.py、gen_det2_diff.py、scan_family.py、pre\ 下的副本、
  matrix_*.json、mrr_*.json、*.txt / *.log）。仓库内**零**临时文件。
* **本卡在仓库里新增的文件只有 2 个**：tests/unit/test_det2_stable_sort_determinism.py（未跟踪，正常）
  与本报告 docs/audit_skill_governance/DET2.md。其余 ?? 条目属于别的卡（开工前就在）。
* **未触碰禁区**：agent/audit/、plugins/、agent/workflow_learning/、yunshu-ui/、data/skills_repo/、
  config.yaml、prompt 装配文件 —— 一行未改。**未动 data/audit/daily_roots.jsonl**；
  探针使用 :memory: 的 ToolTraceRecorder，**未在仓库内建/写 sqlite**；未写 data/learned_workflows.json。
* **文件范围**：改的只有 agent/tool_router.py 与 agent/skills_mgmt/loader.py；
  agent/tool_router_hybrid.py 与 agent/settings/registry.py **一字未动**（sha256 已记录在 §1.4/§7）。
* **无 git 写操作**：未 git add、未 git commit、未做整文件 checkout。
* **无 python 残留进程**：实验结束后 (Get-Process python).Count == 0（本卡未 taskkill 任何进程）。
* **行尾未被打乱**：两个文件全程保持 CRLF；本卡中途一次"文本模式读写"曾把行尾折成 LF，
  已用 sha256 对拍还原为逐字节相同的 CRLF（§2.4 的 sha256 即还原后的值）。
* **未启动任何常驻服务**。


