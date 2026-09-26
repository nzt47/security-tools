# DET-3 · 「set 交给稳定排序」这一族的**收口卡**：三落点影响面判定 + 两处修复 + 族清单定稿

| 项 | 值 |
|---|---|
| 基线 HEAD | 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf |
| 本卡改动文件 | agent/skills_mgmt/few_shot_injector.py（+11 / −1，1 hunk）、agent/lines/assembler.py（+33 / −2，4 hunk）、tests/unit/test_det3_stable_sort_determinism.py（新，9 条） |
| 改动合计 | **+44 / −3，5 hunk**（本卡专属 diff，见 §2.1；两个文件的「改动前副本」与 HEAD **逐字节相同** ⇒ diff 100% 是本卡的） |
| 探针位置（仓库外） | C:\Users\Administrator\AppData\Local\Temp\det3\ |
| 出网 | 零（全程 AGENT_HYBRID_EMBEDDING=0，未拉起向量模型；未访问网络） |
| 新增 env | **无**（确定性化是收紧，不需要逃生开关；agent/settings/registry.py 一字未改，sha256 = 30A704C2…E304 与本卡开工前相同，test_settings_registry.py 仍 **56 passed** = 基线） |
| 结论 | 落点 A **到达用户可见输出 ⇒ 修**；落点 B **不喂模型但到 API/UI ⇒ 修**（主审计「不是模型工具表」成立、「只是诊断」不成立）；落点 C **确实无生产调用点 ⇒ 只登记不修** |

---

## 0. 结论速览

1. **落点 A（few_shot_injector._cosine_tfidf 的 `for term in set(q_count)|set(d_count)`）——到达用户可见输出，已修。**
   根因确认是「set 迭代序 → **浮点加法次序** → 余弦分末位不同」：求和次序实测 5 种子 **5 种**；
   同一个示例的分在改前取到 `0.7724872793364284 / …285 / …286 / …287 / …281` 五个值（独立复现 DET-2）。
   该分是**唯一**决定「选中哪些示例」的量，而选中的示例被拼成 prompt
   （`ContextInjector.build_context` → `full_prompt` → `POST /api/skills-mgmt/inject` 的 `prompt` 字段）。
   **刀刃可复现**：阈值取常量 `0.7724872793364286` 时，改前 0/1/random 号种子判 `True`、2 号种子判 `False`
   ⇒ 选中集合从 `['ex_001']` 翻成 `[]` ⇒ 注入的 prompt 从「有内容（103 token）」翻成「不注入」。
2. **落点 B（assembler 的 `active_planes` + `sorted(key=-plane_weights)`）——不喂模型，但到达 API/UI 载荷，已修。**
   独立复核结论：**喂给模型的 `res.tools` 完全不受影响**（7/7 条真实档案跨 5 种子逐位相同，`out_key` 末位是工具名，是全序）；
   受影响的是 `AssemblyResult.to_dict()` 的**字节**（`by_plane` 键序 + `reasons` 插入序）——
   它经 `routes_agent_lines._preview_dict` 上屏（前端 `yunshu-ui/src/pages/hub/tools/lines.tsx:456`
   按 `Object.entries(preview.by_plane)` 渲染分组），并经 `integration.describe_line` 进状态面板。
   实测：**真实档案 3/7 有并列权重**（dev / engineering / harness 的 perceive = act = 1.0；激活主线恰是 engineering），
   载荷指纹改前 2 种、改后 1 种。故主审计的「不是喂给模型的工具列表本身」**成立**，
   但「似乎只是诊断」**不成立**（它是用户可见载荷，且键序即上屏的分组先后）⇒ 修。
3. **落点 C（agent/utils/index_manager.py）——确实无生产调用点，只登记不修。**
   全仓（含非 .py）搜 `IndexManager` / `index_manager` / `global_index`：生产代码 **0 命中**，
   只有三个测试文件 + `scripts/run_full_pytest.py` 的清单字符串 + docs；
   且 `pytest.ini:66` 把 `tests/unit/test_utils_index_manager.py` **--ignore** 掉了。
   它是**已注册但未接线的库代码**（模块级 `register_singleton("global_index", _create_global_index)` 只注册工厂、不实例化），
   不是「可删的死代码」，也不是「当前可达的缺陷」。跨种子成员差异（5 种子 5 种）已独立复现。
4. **族清单定稿：无漏网者，且本卡补扫出 DET-2 判据覆盖不到的形态（P5~P10），新增点名 13 类落点**（§4）。
   DET-2 的 8 个落点全部复核；它排除的 5 类**全部成立**（逐条复核见 §4.3，其中 1 类附「观察项」条件）。

---

## 1. 第 1 步：逐落点的影响面判定（先判定，再决定修不修）

### 1.1 落点 A —— agent/skills_mgmt/few_shot_injector.py:119

**（a）调用链（实际查，不只看一处）**

| 层 | 位置 | 事实 |
|---|---|---|
| 缺陷点 | `few_shot_injector.py:119` `for term in set(q_count) | set(d_count):` | 浮点累加（`dot / q_norm / d_norm`）的**次序**取自 set 迭代序 |
| 打分 | `few_shot_injector.py:235` `score = _cosine_tfidf(...)` | 该分只有两个用途：`passed = score >= min_score`（:236）与 `scored.sort(key=score, reverse=True)`（:241，稳定排序） |
| 选择 | `select_examples`（:199-244）`scored[:top_k]` | 返回**示例对象列表** |
| 拼 prompt | `context_injector.py:851-857` | `fewshot_ctx = self._few_shot_injector.inject(...)`；`if fewshot_ctx["has_examples"]: prompts.append(fewshot_ctx["prompt"])` |
| 合成 | `context_injector.py:884` `full_prompt = "\n\n".join(prompts)`；`:908` `"prompt": full_prompt` | 示例文本**逐字进入 prompt** |
| 出口 1（API） | `routes_skills_mgmt.py:1462-1489` `POST /api/skills-mgmt/inject` → `return jsonify({"ok": True, **ctx})` | prompt 在**响应体**里 |
| 出口 2（库 API） | `skill_manager.py:437-463` `SkillManager.build_context`（docstring：「一站式构建 LLM 上下文」）；`service.py:2324-2355` `build_skill_context` | 对外承诺就是「给 LLM 的上下文」 |
| **不在**主编排器 | `agent/orchestrator/**` 搜 `ContextInjector` / `few_shot_injector` / `inject_metadata` = **0 命中**（实测） | 编排器直接用 `SkillLoader.match`（orchestrator.py:2371）；故**当前**不走主对话的模型 prompt |

**结论：到达用户可见输出（是）。** —— 它的产物是「设计上要进 LLM 上下文的 prompt 文本」，
当前经 HTTP 端点与库 API 出口（响应体 / UI），只是主编排器还没接它。按本卡判据 ⇒ **修**。

**（b）那个末位比特差异是浮点求和顺序造成的吗？——是。**

机制直证（每种子新解释器，`probe_fewshot_mech.py`）：同一条 (query, doc)，
`set(q_count)|set(d_count)` 的**迭代序**在 5 个种子里取了 **5 种**（`=> C2_det2_repro value-set(5)`），
而同一 doc 的分 repr 随之变化：

    seed=0       scores ["0.7724872793364287", "0.0", "0.0"]
    seed=1       scores ["0.7724872793364286", "0.0", "0.0"]
    seed=2       scores ["0.7724872793364285", "0.0", "0.0"]
    seed=random  scores ["0.7724872793364281", "0.0", "0.0"]
    （另一组语料 C1_uneven 的 doc2：0.6892496917703606 / …607 两值）

**（c）为什么这仍值得修（成本 1 行、收益是「确定性」本身）**

1. **它是「选中哪些示例」的唯一决定量**，而选中的示例逐字进 prompt。F3-1 的口径是
   「同输入 ⇒ 同前缀才谈得上前缀缓存命中」；这里的 prompt 前缀**现在只是碰巧稳定**
   （真实语料上恰好没有踩到刀刃），不是由构造保证的。
2. **刀刃不是理论值**：把阈值固定为跨进程常量（与生产形态同构 —— `min_score` 是调用方常量、
   分随进程动），改前实测 `knife_const_ge = [true, true, false, false]`（4 个种子）⇒
   `select_examples` 的返回从 `['ex_001']` 翻成 `[]`（见 §3 的断言原文）。
   也就是说：**分与阈值相差 ≤1 ulp 时，用户看到的 few-shot 段随进程有无**。
   另一条同源路径是「两个示例的分相差 ≤1 ulp」⇒ 稳定排序的并列先后翻转 ⇒ 示例顺序与
   token 预算贪心结果（`inject` 的 `included`）一起变。
3. **修法零风险**：`dict.fromkeys(list(q_count) + list(d_count))` 与 `set(q)|set(d)` 是**同一个元素集合**
   （项一个不少、加数一个不变），只是把「次序」从「未定义」变成「已定义：查询词序 → 文档词序」。
   `q_count`/`d_count` 是按 token **出现序**插入的 dict，而 token 序由 `_tokenize` 的**文档序**决定
   （loader.py:159-176 返回 `List[str]`，不是 set）⇒ 跨进程确定。
4. **代价为 0**：真实技能库（data/skill_few_shot/self_reflection.jsonl，3 示例 × 4 intent）
   改前改后 `scores` / `select_examples` / `inject.prompt sha256` **逐位相同**（§2.3）。

> **必须如实标注**：该修复把分值本身改成「某个确定次序下的值」，改后值**不一定等于**改前任何一次采样
> （实测 C2 语料改后 = `0.7724872793364282`，落在改前采样 {…281,284,285,286,287} 之外）。
> 这不是「改进了精度」，只是「定义掉了次序」—— 与 DET-2 §2.3 的警示同一条。

### 1.2 落点 B —— agent/lines/assembler.py 的 `by_plane` 键序（独立复核）

**（a）三个消费者复核 —— 主审的两个判断一对一错**

| # | 消费者 | 复核结果 |
|---|---|---|
| 1 | `assembler.py:89` `to_dict()` 里 `"by_plane": {k: list(v) ...}` | ✅ 成立：原样把 `by_plane`（一个 dict）放进**载荷**；键序被保留，且 `json.dumps` 的字节随之变 |
| 2 | `integration.py:116` `"by_plane": {k: len(v) for k, v in result.by_plane.items()}` | ✅ 成立：**只取 len**，但**同样保留键序**（`describe_line` 是状态面板的数据源）⇒ 不是「只取 len 就无关」 |
| 3 | `routes_agent_lines.py:228` 的 `for key in ("by_plane", ...)` | ✅ 成立且**无害**：这里只用它收集工具名，紧接着 `payload["tools_meta"] = {... for name in sorted(names)}`（:239）是**全序** |
| 4 | **（主审未列）** `yunshu-ui/src/pages/hub/tools/lines.tsx:456` `Object.entries(preview.by_plane).map(...)` | ❗**新增**：前端**按 dict 键序**渲染分组 chip ⇒ 键序**就是上屏的分组先后** |
| 5 | **（主审未列）** `res.reasons` 的**插入序**也在 `to_dict()` 里（:100） | ❗**新增**：`reasons` 由「保底循环 + 打分补足」顺序插入 ⇒ 保底循环的平面次序（就是缺陷点）泄漏进载荷字节 |

**（b）「不是喂给模型的工具列表本身」——复核通过（实测，不是推断）**

模型拿到的是 `res.tools`：`line_whitelist()` 返回 `list(result.tools)`（integration.py:97）→ `get_tool_defs(whitelist=...)`。
`res.tools` 在第 ⑥ 步 `kept.sort(key=out_key)`，`out_key` 末位是**工具名字**（全序）⇒ 跨进程确定。
实测（5 种子 × 7 条真实档案，`probe_assembler_all.py`）：**`tools_sha16` 7/7 逐位相同**（改前=改后）：
`assistant 7ea7f74d… / dev aef45d31… / digital_life b6a3980e… / engineering e3519918… / harness 941179a3… / knowledge d99cba5d… / recon f4053fc7…`。

**（c）但「只是诊断/API 元数据」不成立 —— 它到达用户可见输出**

    （改前，5 种子 × 7 档案）                                     （改后）
    dev          by_plane_keys 2 种 / payload_sha16 2 种   →      1 种 / 1 种
    engineering  by_plane_keys 2 种 / payload_sha16 2 种   →      1 种 / 1 种
    harness      by_plane_keys 2 种 / payload_sha16 2 种   →      1 种 / 1 种
    assistant/digital_life/knowledge/recon（权重互异）1 种   →      1 种（不变）
    => by_plane_keys SEQ-DIFFERS(2) | reasons_keys SEQ-DIFFERS(2) | tools SEQ-DIFFERS(1)（改前即 1 种）

**触发条件不是人造的**：`data/agent_lines/_active.json` 实测 `{"active": "engineering"}`，
而 engineering 的 `plane_weights = {act: 1.0, perceive: 1.0, resident: 0.6}` ⇒ **激活主线本身就并列**。

**结论：到达用户可见输出（UI/API 载荷），不喂模型 ⇒ 修（改动对成员与 tools 零影响，见 §2.3）。**

### 1.3 落点 C —— agent/utils/index_manager.py（独立复核「是否真的无生产调用点」）

**（a）调用点复核：确实无生产调用点**

* 全仓（含 .py 之外的扩展名）搜 `IndexManager` / `index_manager` / `global_index`：
  生产代码 **0 命中**；命中的只有
  `tests/unit/test_index_manager.py`、`tests/unit/test_index_manager_concurrency.py`、
  `tests/unit/test_utils_index_manager.py`、`scripts/run_full_pytest.py:72`（**测试文件清单字符串**）、docs 与 `CHANGELOG`。
* `pytest.ini:66` **`--ignore=tests/unit/test_utils_index_manager.py`** ⇒ 连那个测试默认都不跑。
* 模块级 `register_singleton("global_index", _create_global_index)`（:308-309）只在**被 import 时注册工厂**，
  不实例化；而全仓没有任何地方 `get_singleton("global_index")`。

**（b）缺陷本身已独立复现**（`probe_index_manager.py`，自写构造，5 种子）：

    seed=0       kw_top5 ["doc18","doc14","doc20","doc26","doc10"]
    seed=1       kw_top5 ["doc25","doc18","doc28","doc22","doc15"]
    seed=2       kw_top5 ["doc25","doc08","doc29","doc13","doc04"]
    => kw_top5 / category / time_range 三处均 SEQ-DIFFERS(5 kinds) | SET-DIFFERS(5 kinds)

（与 DET-2 §1.5 落点 5 的结论一致：**成员**集都变。）

**结论：不到达用户可见输出（不是「影响小」，而是「没有生产调用点」）⇒ 只登记不修。**
性质判定：**已注册但未接线的库代码**（不是「可删的死代码」—— 它在 `docs/SingletonManager_Migration_Guide.md` 的迁移表里、
且 `agent/utils/singleton_manager` 侧有登记；也不是「当前可达的缺陷」）。
若将来有任何模块接线到 `get_global_index()`，三处会**立即**变成真缺陷（一句话改法见 §6.5）。

---

## 2. 第 2 步：修复（只修判定为「到达用户可见输出」的两处）

### 2.1 本卡专属 diff（+44 / −3，5 hunk）

**两处改动前副本与 HEAD 逐字节相同（各 0 hunk）** ⇒ 这两个文件在本卡之前**没有任何卡动过**，diff 100% 是本卡的
（对比 DET-2 的 loader.py 有 17 个别的卡的 hunk）。
副本：`%TEMP%\det3\pre\few_shot_injector.py`（sha256 `374AB975…2B4E` = 本卡开工前的 live 值）、
`%TEMP%\det3\pre\assembler.py`（sha256 `7CEDD2CD…8AC2` = 本卡开工前的 live 值）。逐字 diff 见
`%TEMP%\det3\my_det3_few_shot_injector.py.diff`、`my_det3_assembler.py.diff`。

**（1）agent/skills_mgmt/few_shot_injector.py（+11 / −1，1 hunk）**

    --- a/agent/skills_mgmt/few_shot_injector.py
    +++ b/agent/skills_mgmt/few_shot_injector.py
    @@ -116,7 +116,17 @@
         d_norm = 0.0
    -    for term in set(q_count) | set(d_count):
    +    # 【DET-3】求和次序必须确定：set 的迭代序随进程（PYTHONHASHSEED）变，
    +    # 而浮点加法不满足结合律 ⇒ 同一份输入、同一份代码，同一个示例的余弦分
    +    # 会差最后 1~2 个比特（实测 0.7724872793364284 / …285 / …286 / …287）。
    +    # 该分参与两处判定：score >= min_score（inject 走默认 0.3）与 scored.sort
    +    # （稳定排序）⇒ 只要某个分与阈值、或与另一个分相差在 1 ulp 内，
    +    # **被选中的示例**（进而拼进 prompt 的文本）就会随进程变 —— 而这段文本
    +    # 会进 LLM 上下文（F3-1 的前缀缓存稳定性依赖「同输入 ⇒ 同前缀」）。
    +    # 次序取「查询词序 → 文档词序」：q_count / d_count 都是按 token **出现序**
    +    # 插入的 dict，而 token 序由 _tokenize 的文档序决定（跨进程确定）。
    +    # 与 DET-2「保留主键、只补确定的次级键，次级键取该子系统本来就有的次序」同口径。
    +    for term in dict.fromkeys(list(q_count) + list(d_count)):
             w = idf.get(term, 0.0)

**（2）agent/lines/assembler.py（+33 / −2，4 hunk）**

    @@ 新增（_DEFAULT_FLOOR 之后）：_PLANE_DECL_ORDER / _PLANE_ORDER_MISSING + 函数 _plane_order
        def _plane_order(planes, weights):
            return sorted(planes, key=lambda p: (-weights.get(p, 0.0),        # 主键：权重降序（未变）
                                                 _PLANE_DECL_ORDER.get(p, _PLANE_ORDER_MISSING),  # 次级键：声明序
                                                 str(p)))                       # 兜底：名字
    @@ assemble 内：active_planes = {...}（成员判定，set 保留）
    +    active_plane_order = _plane_order(active_planes, profile.plane_weights)
    @@ 第 ③ 步：- for p in sorted(active_planes, key=lambda x: -profile.plane_weights.get(x, 0.0)):
    +             for p in active_plane_order:
    @@ 第 ⑥ 步：res.by_plane = { p: [...] for p in sorted(active_planes, key=...) }
    +                              for p in active_plane_order

**次级键为什么取「平面声明序（models.PLANES）」而不是字典序**：`pools`（第 ② 步）本来就是按 `PLANES` 建的
（`{p: [] for p in PLANES if p in active_planes}`）—— 即 DET-2 §2.2 的口径「取该子系统**本来就存在**的那条确定次序」，
而不是挑一个人为的字典序。取名字字典序会让 `by_plane` 的上屏顺序变成 `act, perceive, resident`（resident 掉到最后），
与模块 docstring 的「四平面」陈述顺序不一致。

### 2.2 主键与既有契约未被削弱

* `few_shot_injector`：**主键（余弦分）未变**，只是它的求和次序有了定义；项集合与加数集合完全相同。
* `assembler`：**主键（平面权重降序）未变**（`test_primary_key_is_still_plane_weight_desc` 反向守卫，
  权重 0.9/0.5/0.1 时仍是 `resident, act, perceive`）；`res.tools` 的成员与顺序**一个字没改**。
* 打红风险点名复核：`test_degraded_path_order_matches_raw_bm25`
  = **1 passed, 17 deselected**（§5）；本卡未触碰 tool_router / tool_router_hybrid / loader。

### 2.3 影响面（改前 vs 改后）

**（1）落点 A（few-shot）**

| 语料 | 顺序变化 | 成员变化 |
|---|---|---|
| **真实技能库**（data/skill_few_shot/self_reflection.jsonl，3 示例 × 4 intent，min_score=0.3） | **0 / 4** | **0 / 4**（prompt sha256 逐位相同：4bfc593c… / af413462… / 2a05b790… / e3b0c442…） |
| **合成语料**（48 示例 × 24 查询，min_score=0.3、top_k=2，确定性生成） | **0 / 24**（4 个种子全部 0） | **0 / 24**（4 个种子全部 0） |
| 分数末位跨种子不稳定 | 改前 **19 / 24** → 改后 **0 / 24** | —（分不是输出，但它决定输出） |

即：**输出影响面 0**（当前语料上），本卡消除的是「分」这一层的未定义性 —— 而它的可达性由 §3 的刀刃实验证明。

**（2）落点 B（装配器）**

| 维度 | 改前 | 改后 |
|---|---|---|
| `by_plane` 键序（7 条真实档案） | dev / engineering / harness **2 种**（其余 4 条 1 种） | **7/7 各 1 种** |
| `reasons` 插入序 | 同上 3 条 **2 种** | **7/7 各 1 种** |
| `to_dict()` 不排序载荷指纹 | 3 条档案 **2 种** | **7/7 各 1 种** |
| **`res.tools`（喂给模型的工具表）** | 7/7 **1 种** | 7/7 **1 种，且与改前逐位相同** |
| `by_plane` 分组的**成员**集合 | = tools 的按平面划分 | 不变（新测试断言 `sorted(by_plane 并集) == sorted(tools)`） |

**顺序变化**：3/7 条档案（dev / engineering / harness）的**键序**从「2 选 1」变成「固定为声明序」；
**成员变化**：**0**（既没有工具的增减，也没有分组成员的增减）。

---

## 3. 非空转自证（去掉修复 ⇒ 必红 ⇒ 还原 ⇒ 绿 + sha256 逐字节）

`%TEMP%\det3\mutation_selfproof.py`（**定向反向替换**，不做整文件 checkout；变异后 `py_compile` 通过）：

    == backup sha256 (DET-3 后 / 生产位) ==
       9d18bd983d71ed7043c6300c3f9229e76f35e68fc80a1f848d9591c1dae4d96d  agent/skills_mgmt/few_shot_injector.py
       07f28e288b5ae6820f0b8357db55853bb0aa3c89eb0f84045651eee670b4d246  agent/lines/assembler.py
    == mutated (退回 set 迭代序) ==
    MUTATED-PYTEST-RC=1   （非 0 ⇒ 新测试真的变红）
    ========================= 7 failed, 2 passed in 6.64s =========================
    == restore ==
       few_shot_injector.py live=9d18bd983d71ed70 bak=9d18bd983d71ed70 -> RESTORED-OK
       assembler.py        live=07f28e288b5ae682 bak=07f28e288b5ae682 -> RESTORED-OK
    RESTORED-BYTES-IDENTICAL=True
    RESTORED-PYTEST-RC=0 (0 ⇒ 修复在位)
    ========================= 9 passed in 6.08s ==============================
    SELFPROOF-FAILS=0

变异下 7 条变红的断言原文（`%TEMP%\det3\mutation_mutated.txt`）：

    FAILED …::TestFewShotSummationOrderIsDeterministic::test_cosine_score_is_bit_identical_across_hash_seeds
    E   AssertionError: 同一份语料 / 同一份代码，余弦分跨进程**末位不同** ⇒ 求和次序仍取自 set 迭代序（生产上就是 PYTHONHASHSEED）：
    E       seeds=['0'] -> ["0.7724872793364287", "0.0", "0.0"]
    E       seeds=['1'] -> ["0.7724872793364286", "0.0", "0.0"]
    E       seeds=['2', 'random'] -> ["0.7724872793364285", "0.0", "0.0"]
    E   assert 3 == 1

    FAILED …::TestFewShotSummationOrderIsDeterministic::test_threshold_decision_is_identical_across_hash_seeds
    E   AssertionError: 同一份语料 / 同一个阈值常量，passed 跨进程不同 ⇒ 分与阈值相差在 1 ulp 内时，**被选中的示例**随进程翻转（这段文本是要进 LLM 上下文的）：
    E       seeds=['0', '1', 'random'] -> [true, false, false]
    E       seeds=['2'] -> [false, false, false]
    E   assert 2 == 1

    FAILED …::TestFewShotSummationOrderIsDeterministic::test_summation_order_is_not_taken_from_a_bare_set
    E   AssertionError: 累加循环又直接迭代 set 了 ⇒ 浮点求和次序重新交给 set 迭代序：
    E         for term in set(q_count) | set(d_count):
    E   assert 'for term in set(' not in 'def _cosine...t(d_norm))\n'

    FAILED …::TestAssemblyPlaneOrderIsDeterministic::test_real_profiles_payload_is_identical_across_hash_seeds
    E   AssertionError: 真实档案的 by_plane_keys 跨进程不同（并列权重下平面先后取自 set 迭代序）：
    E       seeds=['0', '1'] -> {…"dev": ["act","perceive","resident"], "engineering": ["perceive","act","resident"],
    E                              "harness": ["act","perceive","govern","resident"], …}
    E       seeds=['2'] -> {…"dev": ["perceive","act","resident"], "engineering": ["act","perceive","resident"],
    E                           "harness": ["perceive","act","govern","resident"], …}
    E   assert 3 == 1

    FAILED …::TestAssemblyPlaneOrderIsDeterministic::test_plane_order_ignores_input_set_iteration_order
    FAILED …::TestAssemblyPlaneOrderIsDeterministic::test_tied_weights_follow_plane_declaration_order
    FAILED …::TestAssemblyPlaneOrderIsDeterministic::test_primary_key_is_still_plane_weight_desc
    E   ImportError: cannot import name '_plane_order' from 'agent.lines.assembler'

**行为级追加自证（刀刃）**：变异下重跑 `probe_fewshot_mech.py`（每种子新解释器），
阈值是**源码里的常量** `0.7724872793364286`（跨进程相同，与生产形态同构）：

    变异（改前行为）：knife_const_ge = [true(seed0), true(seed1), false(seed2), false(random)]
    修复在位：        knife_const_ge = [false, false, false, false]

⇒ 同一份输入、同一个常量阈值，**改前「这个示例入不入选」随进程翻转**，改后唯一。

**必须如实说明的两条绿**（不假装「全红」）：变异下仍有 2 条为绿 ——
`test_injected_prompt_is_identical_across_hash_seeds`（生产入口 `inject` 走的是默认 `min_score=0.3`，
本语料没踩到 0.3 的刀刃 ⇒ 它在改前也是绿的，只作**守卫**）与
`test_model_facing_tools_list_is_untouched_by_the_ordering_key`（它守的是「本卡不许动 res.tools」，
变异没有动这条路径）。两者的判别力由上面 7 条与刀刃实验承担。

---

## 4. 第 3 步：「set 交给稳定排序」这一族的**最终清单**（四态分明，不许有漏网者）

### 4.1 扫描方法与口径

在 DET-2 的 `scan_family.py`（A1 `.sort` on set-name / A2 `sorted(set-name)` / A3 `for-in set-name` 写有序容器 /
B `for-in set(...)` 浮点累加）之上，本卡补了 **6 类它覆盖不到的形态**（`%TEMP%\det3\scan_family_wide.py`，
全仓 402 个文件、935 个可疑点，逐条判读）：

| 补扫判据 | 覆盖的形态 | 为什么 DET-2 会漏 |
|---|---|---|
| P5 | `for x in set(...)/{...}/集合推导式:` 且 body 写**有序产物**（list.append/extend/insert、dict 新键） | DET-2 的 A3 只认「被 set 赋值的**名字**」，不认 `set(...)` **调用**；B 只认浮点累加 |
| P6 | `min/max/next(iter(...))/list(...)[0]/pop()` over 集合（并列时「取哪个」未定义） | 完全没覆盖 |
| P7 | `list(set(...))` / `[.. for .. in set(...)]`（把无序源直接变成**有序产物**） | 完全没覆盖（这是本卡新发现最多的一类） |
| P8 | `random.choice/sample` 在集合上（无序起点 ⇒ 不可复现） | 完全没覆盖 |
| P9 | `defaultdict(set)` 的**值**（下标迭代）—— index_manager 的真实形态 | DET-2 是**人工读出来的**，不是扫出来的 |
| P10 | 迭代「无序源」并做**浮点累加**（含集合变量/下标，不限于 `set()` 调用） | B 只覆盖 `set(...)` 调用 |

**分级口径**：`sorted(set(...))` / `sorted(集合变量)` 是**全序**，与迭代序无关 ⇒ **无害（正确样板）**，
本卡实测 A2 形态占了 935 个可疑点中的绝大多数（约 440 条），不再列表。
真正需要判读的是 P3/P5~P10 与「set → 有序产物」的其余形态。

### 4.2 四态清单

**态一：已修（前序卡）**

| # | 位置 | 机制 | 证据 |
|---|---|---|---|
| 1 | agent/tool_router_hybrid.py（`_query_locked` 的 all_candidates + `hybrid_select_tools` 的 selected） | set 迭代序 → 融合的稳定排序 | E1-D 已修；DET-2 §1.4 复核（sha256 = 53B3A41B…A61 未变）；本卡回归 `test_tool_router_hybrid_e1d_determinism.py` **3 passed** |
| 2 | agent/tool_router.py：`get_tools_for_input` → `_apply_alias_merge_and_priority_sort` | set → helper 的稳定排序（并列块跨截断点 ⇒ 成员变） | DET-2 修；本卡回归 `test_det2_stable_sort_determinism.py` **10 passed** |
| 3 | agent/skills_mgmt/loader.py：`_tfidf_scan` 的 candidate_ids（含 candidate_limit 截断） | set 迭代序 → 调用方稳定排序 → top-3 / MRR | DET-2 修；同上 10 passed |

**态二：本卡修**

| # | 位置 | 机制 | 证据 |
|---|---|---|---|
| 4 | agent/skills_mgmt/few_shot_injector.py:119 `_cosine_tfidf` | set 迭代序 → **浮点加法次序** → 余弦分末位 ⇒ 阈值判定与稳定排序 | 改前分 repr 5 种、阈值常量判定翻转；改后跨种子唯一（§1.1/§3） |
| 5 | agent/lines/assembler.py:222/268/366 `_plane_order` | set（active_planes）+ 权重并列 → 稳定排序 → by_plane 键序 / reasons 插入序 / 载荷字节 | 改前 3/7 档案 2 种载荷指纹；改后 1 种；tools 0 变化（§1.2/§2.3） |

**态三：仍存在，但已判定「不到达用户可见输出」（附理由）**

| # | 位置 | 判定 | 理由（证据） |
|---|---|---|---|
| 6 | agent/utils/index_manager.py:199（`sorted(matched_docs.items(), key=命中次数)` 并列取哈希序）/ :231 `list(matched)` / :246 `list(category_index.get(...))` | **不到达** | 全仓**无生产调用点**（§1.3 实测）；成员差异已复现（5 种子 5 种）。**性质：已注册未接线的库代码** |
| 7 | agent/skills_mgmt/few_shot_injector.py:104 `_compute_idf` 的 `for t in set(tokens): df[t]=...` | **不到达** | 循环体只有**整数 +1 计数**（与次序无关）；它产出的 `df`/`idf` 是 dict，而全仓对 `idf` 只有 `idf.get(term, 0.0)`（键查找）—— 唯一调用方是 `_cosine_tfidf`（同文件）。**这是本卡补扫新点名的落点**（DET-2 的 A3 不覆盖 `set(...)` 调用 + dict 写入） |
| 8 | agent/tool_router_hybrid.py:646 `for token in set(query_tokens)`（浮点累加） | **不到达（本次仍未证实）** | DET-2 §1.5 落点 6：90 工具 × 5 查询 × 5 种子分数 repr **逐位相同**；本卡未重跑该矩阵（不重复他人探针），沿用其「未证实」结论 |
| 9 | 脚本：scripts/dev/new_session_worktree.py:85 `sorted(sessions, key=_session_no)` | **不到达** | 脚本、非生产路径；且并列需「同号 session」这种极窄条件（DET-2 §4.2 落点 8，本卡静态复核同意） |
| 10 | 脚本：scripts/check_circular_deps.py:127 `for a, b in edges`（set） | **不到达** | 脚本；写的是 `graph` dict（环检测与键次序无关） |
| 11 | 脚本：scripts/check_grafana_metric_names.py:163 `for m in dashboard_metric_set` | **不到达** | 脚本；产出的是「缺失/孤儿」**集合**的比较结果，报告文本前有 `sorted(...)` |
| 12 | 脚本（本卡补扫）：scripts/dependency_trend_report.py:251、scripts/check_boundary_coverage.py:260、scripts/apply_auto_keywords_and_test.py:67、scripts/augment_negative_samples.py:111/125 | **不到达** | 均为一次性分析/数据准备脚本（非运行时路径） |

**态四：判定为死代码 / 非生产残留（附证据）**

| # | 位置 | 证据 |
|---|---|---|
| 13 | `_scratch/`、`_ci_logs/`、`_t06_logs/`、`backup/`、`.trae/merge_backup_*`、`docs/archive/`、`.tmp-merge/`、`_tmp_rootcause_probe/` 下的同族命中（如 `.trae/merge_backup_20260723/.../agent_tool_router_hybrid.py:1091`、`_scratch/l27_head.py:1361`、`_ci_logs/l19/acp_before.py:620`） | 全是**历史副本/探针残留**（另有卡留下的工作区残留），不参与任何运行时导入；不是本族的生产落点 |
| 14 | `.devtools/pylibs/{fakeredis,redis}/**` 的同族命中（P3/P5/P7） | **第三方库源码**（本地 vendored），不属于本仓代码 |
| 15 | `tests/**` 里刻意构造的同族形态（本卡新测试的 `_ShuffledSet` 等） | 测试**故意**构造迭代序，是判据的一部分 |

**态五（补）：本卡补扫新点名、且确实到达用户可见输出，但不在本卡文件范围 ⇒ 只登记、未修**

> 本卡文件范围被限定在 few_shot_injector / assembler / index_manager / settings.registry 四者之内
> （另有明确的禁区清单），故下列落点**不能在本卡修**，但按「不许有漏网者」的要求**逐条点名**，
> 并给出与 DET-2 §7 同格式的一句话改法。

| # | 位置 | 到达什么用户可见输出 | 一句话改法 |
|---|---|---|---|
| 16 | agent/text_tools.py:437/453/475/486/497/504/520/635/646/657/668/690 `list(set(mN))` | **工具返回值**里的 `detected_patterns[*].matches`（直接进模型上下文 + 上屏） | 把 `list(set(m))` 换成 `list(dict.fromkeys(m))`（去重且保发现序） |
| 17 | agent/task_planner/enhanced_planner.py:590 `rollback_path = list(set(rollback_path))` | **行为级**：紧接着 `for i, task_id in enumerate(rollback_path)` 造 `rollback_{i}` 任务 ⇒ 回退任务的**生成顺序**随进程变 | `list(dict.fromkeys(rollback_path))` |
| 18 | agent/skills_mgmt/store.py:195 `new_tags = list(set(a.tags) | set(b.tags))` | 合并后的技能 `tags`（落盘 + UI + 面板筛选） | `list(dict.fromkeys(list(a.tags) + list(b.tags)))` |
| 19 | agent/skills_mgmt/store.py:385 `new_deps = list({...})`（依赖合并兜底路径） | 技能的 `dependencies` 列表顺序 | `list(dict.fromkeys(...))` |
| 20 | agent/skills_mgmt/memory_abstractor.py:1068 `"tags": list(set(cluster.common_tags + [...]))` | 自动生成技能的 `tags` | `list(dict.fromkeys(...))` |
| 21 | agent/skills_mgmt/memory_abstractor.py:661 `for key in common_keys:`（set）→ `result[key]=...` | `default_params` 的**键序**（写进生成技能） | `for key in sorted(common_keys):`（或按 entries[0].params 的键序） |
| 22 | agent/process_distill/solidify.py:118 / :257 `list({*tags, ...})[:8]` | 蒸馏产物（workflow / skill）的 `tags`：**先无序再截断** ⇒ 截断点上的**成员**都可能变 | `list(dict.fromkeys([*tags, "distilled", ...]))[:8]` |
| 23 | agent/server_routes/routes_assets.py:261 `for cat in FILE_BASED_CATEGORIES:`（**set 字面量**，:22） | `GET /api/assets/export` 导出 JSON 的**键序** | 把常量改成元组（它是「类别枚举」，本来就该有序） |
| 24 | agent/skills_mgmt/executor.py:499（`_ENV_WHITELIST` set → `safe_env[key]=`）/ :617 `list(_ENV_WHITELIST)` | 子进程环境变量 dict 的键序（无语义）+ `health()` 的 `env_whitelist` **列表顺序**（上屏） | 常量改元组；`health()` 返回 `sorted(_ENV_WHITELIST)` |
| 25 | agent/safety_guard.py:143 / agent/permission_system.py:400 `list(set(...))` | 告警记录的 `categories` / 权限结果的分类列表（上屏） | `sorted(set(...))` 或 `dict.fromkeys` |
| 26 | agent/search_aggregator.py:350 `return list(set(keywords))` | 关键词列表（进检索请求/展示） | `list(dict.fromkeys(keywords))` |
| 27 | agent/memory/adapters/holographic_adapter.py:679 `profile["tags"] = list(tags)`（`tags` 是 set） | 用户画像 `tags` 的顺序（面板） | `sorted(tags)` 或按 `rows` 出现序 |
| 28 | agent/skills_mgmt/conflict_resolver.py:184 `for key in all_keys:`（set）→ `merged[key]=...` | 合并后 front matter 的**键序**（落盘 skill.md） | `for key in sorted(all_keys):`（或 ours → theirs 的声明序） |
| 29 | **禁区文件（只点名，本卡不碰）**：agent/workflow_learning/skill_converter.py:265/274/644、agent/workflow_learning/generator.py:88、plugins/demo_plugin.py:46 | 技能转换产物的 tags/dependencies；插件配置键序 | 同上 `dict.fromkeys` / 元组常量 |

### 4.3 对 DET-2 那 5 类「明确排除」的复核（逐条一句话）

| DET-2 排除项 | 复核结论 |
|---|---|
| agent/skills_mgmt/bm25_searcher.py:280 `sorted(enumerate(scores), key=score, reverse=True)` | **成立**：输入是 `enumerate(scores)`（**序列**），并列先后 = `scores` 下标序 = `self._skill_ids` 的入参序；读码核实 `self._skill_ids: List[str] = []`（:156）⇒ 确定。**附观察项**：这条排除的成立**依赖 `_skill_ids` 是列表**，若将来换成 set/无序源则失效（登记为观察项） |
| agent/skills_mgmt/searcher.py:185-194（管理页四次 `results.sort`） | **成立**：`results` 是 append 构造的**列表**；`cats` / `sts` / `set(s.tags) & set(params.tags)` 三个 set 只做 `in` / `&` **成员判定**，从不产出有序物（:152-164 读码核实） |
| agent/subagent/toolset.py:514/530 + agent/capregistry/toolset_hash.py | **成立**：`candidates = name_candidates(raw)`，其定义实测为 `def name_candidates(raw) -> Tuple[str, ...]`（toolset.py:219，**有序元组**）；`toolset_hash.py` 在本卡的**宽扫里 0 命中**（四条新判据也不命中）⇒ 该文件没有「set → 有序产物」的形态 |
| agent/workflow_learning/matcher.py:107/114 `for t in set(tokens)` | **成立**：循环体只有整数 `df[t] += 1`（无浮点累加、无有序产物）；`set(...)` 只影响 `df` 这个 dict 的插入序，而它只被当**计数器**按 key 查 |
| loader.py 里其余 `.keys()/.items()` | **成立**：都是 dict **插入序**（确定），且插入序本身来自已确定化的路径（DET-2 已把 `_tfidf_scan` 的候选汇合序钉成索引序） |

---

## 5. 回归结果（未放宽任何断言）

本卡要求的那批 + 同族/相邻（21 个文件，一条命令）：

    tests/unit/test_few_shot_injector.py                    17 passed
    tests/unit/test_agent_lines.py                          41 passed
    tests/unit/test_index_manager.py                        18 passed
    tests/unit/test_index_manager_concurrency.py             4 passed
    tests/unit/test_utils_index_manager.py                  28 passed
    tests/unit/test_settings_registry.py                    56 passed   （= 基线 56；registry.py 未改）
    tests/unit/test_det2_stable_sort_determinism.py         10 passed   （DET-2 的守卫，未被本卡打红）
    tests/unit/test_tool_router_hybrid_e1d_determinism.py    3 passed   （E1-D 的守卫，未被本卡打红）
    tests/unit/test_tool_router_hybrid_fusion_calibration.py 18 passed   （含「降级路顺序 == raw BM25 顺序」契约）
    tests/unit/test_det3_stable_sort_determinism.py          9 passed   （本卡新增）
    tests/unit/test_advert_equals_dispatch_smart.py          7 passed
    tests/unit/test_tool_pruning.py                         38 passed
    tests/unit/test_load_tool_meta_cache.py                  8 passed
    tests/unit/test_capregistry_core.py                     71 passed
    tests/unit/test_tool_callability.py                     42 passed, 6 skipped（--runslow 门控，既有）
    tests/unit/test_subagent_toolset.py                     97 passed
    tests/unit/test_capability_spec.py                      49 passed
    tests/unit/test_confirm_level.py                       142 passed
    tests/unit/test_skills_mgmt.py                          75 passed, 1 xfailed
    tests/unit/test_skill_manager.py                        62 passed
    tests/unit/test_toolset_hash.py                         38 passed
    ------------------------------------------------------------------------------
    合跑：787 passed, 6 skipped, 1 xfailed（87.22s，rc=0）

契约用例**点名**复核（E1-D/DET-2 最担心的那条）：

    tests/unit/test_tool_router_hybrid_fusion_calibration.py -k degraded_path_order_matches_raw_bm25
    ====================== 1 passed, 17 deselected in 1.29s ======================

设置登记表（本卡未新增 env，仍按要求跑）：

    python -X utf8 -m pytest tests/unit/test_settings_registry.py -q
    ============================= 56 passed in 34.82s =============================

---

## 6. 未验证项与残留风险

1. **真实语料的输出影响面无法放大**：仓库里只有一个 few-shot 技能库（`data/skill_few_shot/self_reflection.jsonl`，3 条示例），
   4 个 intent 上改前改后逐位相同；合成语料（48×24）给出「顺序 0/24、成员 0/24」。
   即：**本卡修掉的是一条「当前没有产生输出差异、但能产生输出差异」的路径**（判据见 §3 的刀刃实验），
   不要把它读成「修了一个正在出错的 bug」。
2. **刀刃条件（分与阈值/另一分相差 ≤1 ulp）在 `min_score=0.3` 下本卡未在真实数据上观测到**；
   用的是「把阈值固定为常量、分随进程动」的同构实验（`select_examples(min_score=常量)`，该参数是公开 API）。
   残留风险：低（机制已钉死），但「0.3 会不会踩上」是数据问题，本卡无法终结。
3. **分值本身的数值会变**（改后 = 「查询词序→文档词序」次序下的值，最多几个 ulp）：
   实测 C2 语料改后 `0.7724872793364282` **不在**改前采样集合 {…281,284,285,286,287} 内。
   这是「定义次序」的必然代价，**不得**被当作精度改进/调优结论（同 DET-2 §2.3 的警示）。
4. **未跑**：向量腿就绪态（全程 `AGENT_HYBRID_EMBEDDING=0`，未加载任何模型）；UI 端未构建、未看渲染 ——
   `by_plane` 键序「上屏」是按 `lines.tsx:456` 的 `Object.entries(...)` 源码判定的，未做浏览器验证。
5. **未跑**：`index_manager` 的真实集成（本就无调用点）；若将来接线，§1.3 的三处会立即变成真缺陷
   （一句话改法：候选按索引序/名字序收敛，或给 `sorted` 补显式次级键；`list(matched)` → `sorted(matched)`）。
6. **态五的 13 类新落点只做了「读码判定」**，未逐个跑跨种子探针（除文本类判据外）。
   残留风险：其中「先 `list(set(...))` 再**截断/取前 N**」的形态（如 `solidify.py` 的 `[:8]`）会从「顺序差异」升级为**成员差异**，优先级最高。
7. **装配器次级键的口径代价**：改后键序与改前**某个采样**不同（perceive 在 act 之前），
   这是「把未定义定义掉」的代价；选「声明序」而非字典序的理由见 §2.1。
8. **共享工作区时序**：本卡期间 `agent/tool_router.py`、`agent/skills_mgmt/loader.py`（10:50:48）被**别的卡**写过；
   本卡未触碰这两个文件（其 sha256 未记录于本卡，如需沿用 DET-2 的对照请按新版本复核）。

---

## 7. 回滚指令（定向；**不要**整文件 git checkout）

本卡改动全部落在两处、各有唯一锚点，且已把「改动前文本」逐字节存成副本：

* `%TEMP%\det3\pre\few_shot_injector.py` sha256 = `374AB9756C3AD27472723202FA44D25050FDFB8AB737BBAB56CBF2B340E72B4E`（= 本卡开工前 live 值 = HEAD）
* `%TEMP%\det3\pre\assembler.py` sha256 = `7CEDD2CD8322511D31AF0C1F5F45970F4A90C198B372D2060C7D9A697C498AC2`（= 本卡开工前 live 值 = HEAD）
* 改后（= 当前生产位）：
  `agent/skills_mgmt/few_shot_injector.py` = `9D18BD983D71ED7043C6300C3F9229E76F35E68FC80A1F848D9591C1DAE4D96D`
  `agent/lines/assembler.py` = `07F28E288B5AE6820F0B8357DB55853BB0AA3C89EB0F84045651EEE670B4D246`

**方式 A（最快，等价于本卡从未发生）**：覆盖副本 + 删新增测试
（**注意行尾**：few_shot_injector.py 是 **CRLF**、assembler.py 是 **LF**，副本已按原样保存，直接 byte copy 即可）：

    copy %TEMP%\det3\pre\few_shot_injector.py  <repo>\agent\skills_mgmt\few_shot_injector.py
    copy %TEMP%\det3\pre\assembler.py          <repo>\agent\lines\assembler.py
    del  <repo>\tests\unit\test_det3_stable_sort_determinism.py

**方式 B（逐条反向替换，若副本丢失）**：

1. agent/skills_mgmt/few_shot_injector.py：删掉 `for term in dict.fromkeys(list(q_count) + list(d_count)):` 上方的
   11 行 `# 【DET-3】…` 注释块，并把该行换回 `    for term in set(q_count) | set(d_count):`。
2. agent/lines/assembler.py：
   * 删除 `_DEFAULT_FLOOR = 2` 之后新增的 `_PLANE_DECL_ORDER` / `_PLANE_ORDER_MISSING` / `def _plane_order(...)` 整段
     （连同其上方空行，直到 `@dataclass` 之前）；
   * 删除 `active_planes = {...}` 之后新增的 3 行注释 + `active_plane_order = _plane_order(...)`；
   * `for p in active_plane_order:` → `for p in sorted(active_planes, key=lambda x: -profile.plane_weights.get(x, 0.0)):`（第 ③ 步）；
   * `for p in active_plane_order` → `for p in sorted(active_planes, key=lambda x: -profile.plane_weights.get(x, 0.0))`（第 ⑥ 步）。
   锚点原文见 `%TEMP%\det3\my_det3_assembler.py.diff`（反向读即得）。
3. 删除 tests/unit/test_det3_stable_sort_determinism.py（它只守护本卡行为）。

**回滚后的预期**：抖动立即复现 —— 7 条新测试变红（§3 的断言原文在变异下已实测），
few-shot 的分 repr 重新跨进程分叉（`[0.7724872793364287 / …286 / …285 / …281]`），
装配器 3/7 条真实档案的 `by_plane`/`reasons`/载荷指纹重新出现 2 种；
`res.tools` 与各既有契约**不受影响**（本卡没动它们）。

---

## 8. 残留物自证

* **探针全部在仓库外**：`C:\Users\Administrator\AppData\Local\Temp\det3\`
  （`run_seeds.py`、`probe_fewshot_real.py`、`probe_fewshot_mech.py`、`probe_fewshot_impact.py`、
  `probe_assembler.py`、`probe_assembler_all.py`、`probe_index_manager.py`、`scan_family_wide.py`、
  `dump_sites*.py`、`mutation_selfproof.py`、`run_impact.py`、`gen_det3_diff.py`、`pre\` 副本、
  `*.txt` / `*.diff` / `*.log`）。仓库内**零**临时文件。
* **本卡在仓库里新增的文件只有 2 个**：`tests/unit/test_det3_stable_sort_determinism.py`（未跟踪，正常）
  与本报告 `docs/audit_skill_governance/DET3.md`。`git status --porcelain` 里与本卡相关的条目只有：
  ` M agent/lines/assembler.py`、` M agent/skills_mgmt/few_shot_injector.py`、`?? tests/unit/test_det3_stable_sort_determinism.py`
  （`docs/audit_skill_governance/` 整个目录本就未跟踪）。其余 130+ 条属于别的卡（开工前就在）。
* **本卡 diff 100% 是自己的**：两个文件的**改动前副本与 HEAD 逐字节相同（各 0 hunk）** ⇒ 本卡之前没有任何卡动过它们。
* **文件范围**：只改了上述两个 .py；`agent/utils/index_manager.py` **一字未动**（判定为不修）；
  `agent/settings/registry.py` **一字未动**（sha256 `30A704C2…E304`，与开工前相同）⇒ **本卡未新增任何 env**。
* **未触碰禁区**：`agent/audit/`、`plugins/`、`agent/workflow_learning/`、`yunshu-ui/`、`data/skills_repo/`、
  `config.yaml`、prompt 装配四件套（system_prompt_config / system_prompt_manager / digital_life / persona_injector）
  —— 一行未改。**未动 `data/audit/daily_roots.jsonl`**（其 mtime 仍是 10:07:00，早于本卡开工）。
  未改 `agent/tool_router.py` / `agent/tool_router_hybrid.py` / `agent/skills_mgmt/loader.py`（前两张卡的生产位逐位保留）。
* **行尾未被打乱**：few_shot_injector.py 全程 CRLF（368 CRLF / 368 LF，改后逐字节自证 RESTORED-OK）；
  assembler.py 全程 LF（0 CRLF）。
* **无 git 写操作**：未 `git add`、未 `git commit`、未做整文件 `git checkout`（只做定向反向替换，且已 sha256 自证还原）。
* **无 python 残留进程**：实验结束后 `(Get-Process python).Count == 0`（本卡未 taskkill 任何进程）。
* **未启动任何常驻服务**；未新建/写仓库内 sqlite（探针只读 registry/YAML）。
* **已知的仓库内自动产物（非本卡新增文件）**：`test_reports/logs/test_*.log` 由 pytest 的报告钩子在**每次**跑测试时写
  （`.gitignore:179` 已忽略 `test_reports/`，现存 12825 个，别的卡的运行同样在写）；
  `.pytest_cache/`、`.pytest_tmp/*.db` 亦为既有产物。本卡的测试运行一律加 `-p no:cacheprovider`。
