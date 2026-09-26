# G1C-UA · "三条检索腿必须同口径"——修掉 G1C-U1 只落到 1/3 条腿的问题，并钉成护栏

> 任务卡：**G1C-UA**（G1C-U1 自己登记的 **U-A** 的专卡）
> 基线：`HEAD = 5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`（开工 / 收尾各实测一次，**未变**；分支未动）
> 环境：Python 3.12.0（系统解释器，**未用 venv**）、Windows；全程**无 `git add` / `git commit`**、
> **无整文件 `git checkout`**、**零出网**（`HF_HUB_OFFLINE=1`，模型走本地 HF cache）；
> 未启动任何常驻服务、**未 taskkill 任何 python 进程**。
> 探针位置：`C:\Users\Administrator\AppData\Local\Temp\g1cua\`（**全部在仓库外**）

---

## 0. 结论速览（先说结论，包括**与本卡预设不符**的一条）

| 腿 | 改前是否同步 | 改前中文召回 | 改后中文召回 | 改前英文 | 改后英文 | 判定 |
|---|---|---|---|---|---|---|
| **TF-IDF**（`loader._meta_to_meta_text`） | ✅ G1C-U1 已修 | **8/8** | 8/8（未动） | 8/8 | 8/8 | 无需修 |
| **BM25**（`bm25_searcher._skill_to_doc`） | ❌ **同形独立实现** | **2/8** | **8/8** | 8/8 | 8/8 | **必须修 → 已修** |
| **向量**（`vector_adapter._build_vector_text`） | ❌ 同形独立实现 | **8/8**（！） | 8/8 | 1/8（见 §7 R-2） | 1/8 | **口径未同步 → 已同步**；召回**无收益** |

**与本卡预设不符、必须如实说的一条**：任务书假设"打开向量腿 ⇒ 中文召回静默退回 2/8"。
**实测是假的**：向量腿改前中文 **8/8**（BGE-m3 跨语言 + `body` 摘要里本来就有中文），
`description_zh` 对它**不是**必要条件。向量腿真正的隐患不是"中文召回掉了"，
而是"它的文档文本**不受 `CP_SKILL_META_INCLUDE_ZH` 控制**"——换模型 / body 摘要变短 /
技能没有中文 body 时，这条腿没有任何开关能兜住。本卡仍把它并到同一口径（见 §3.3 的取舍）。

| 项 | 判定 | 一行证据 |
|---|---|---|
| BM25 腿中文召回 | ✅ **2/8 → 8/8** | §1.2 / §1.5 原始输出 |
| BM25 腿英文回归 | ✅ 8/8 → 8/8，16 条 query 里 top1 变化 6 条、top5 成员变化 11 条 | §3.2 |
| 向量腿中文召回 | ⚪ **8/8 → 8/8（本改动对它没有召回收益）** | §1.3 / §1.6 |
| 向量腿英文召回 | ⚠️ **1/8 → 1/8**（原因与本卡无关，是**既有**缺陷，见 §7 R-2） | §1.3 |
| 向量腿排序扰动 | ⚠️ 中文：top1 **0** 变 / top5 成员 **4** 变；英文：top1 **1** 变 / top5 成员 2 变 | §1.7 |
| 三腿口径 | ✅ 三条腿**只调用一个**文本构造函数，只由**一个**开关决定 | §4 |
| 逃生开关 | ✅ **复用** `CP_SKILL_META_INCLUDE_ZH`，**没有第二个开关**；置 0 ⇒ 三腿**一起**逐字回旧 | §5 |
| 新增 env | ✅ **0 个**（故 `registry.py` 未改）；`test_settings_registry.py` **56 passed** = 基线 | §6 |
| 非空转自证 | ✅ 只还原 BM25 腿 ⇒ **5 failed**；只还原向量腿 ⇒ **5 failed**；两条都还原 ⇒ **6 failed** | §4.3 |
| 还原后 | ✅ 5 个文件 sha256 **逐字节相同**，护栏 **15 passed** | §4.3 |
| 回归 | ✅ **260 passed / 1 xfailed / 0 failed**（7 个文件） | §6 |
| 残留物 | ✅ 仓库内只多 2 个文件（测试 + 本报告）；探针全在 Temp | §9 |

---

## 1. 第 1 步：先证实 / 证伪（原始输出）

### 1.1 生产入口与测量口径

```
### G1C-UA 三腿召回探针  tag=before
### 生产入口: agent.skills_mgmt.loader.SkillLoader(file_store=SkillFileStore())
### CP_SKILL_META_INCLUDE_ZH = None   (_include_description_zh()=True)
### load_metadata_index() 条数 = 28
### 带 description_zh 的技能数 = 20
### loader.py      = C:\Users\Administrator\agent\agent\skills_mgmt\loader.py
### bm25_searcher  = C:\Users\Administrator\agent\agent\skills_mgmt\bm25_searcher.py
### vector_adapter = C:\Users\Administrator\agent\agent\skills_mgmt\vector_adapter.py
```

· 每条腿都走**它自己的生产取数口**，没有在探针里另拼字符串：
  TF-IDF `ld.match(use_vector=False, use_bm25=False)`；BM25 `ld._get_bm25_searcher().search()`
  （`_get_bm25_searcher()` 是 `loader` 真用的那个工厂，索引来自 `fs.load_metadata_index()`）；
  向量 `ld._get_vector_adapter().search()`（BGE-m3 / st_backend）。
· `top_k = 5`，"命中"= 期望技能出现在 top-5。
· 中文 / 英文 query 各 8 条，与 `docs/audit_skill_governance/G1C-U1.md` §1.2/§1.3 **逐字相同**，便于对拍。
· 固定 `PYTHONHASHSEED=0`（G1C-U1 §1.5 已证：同分并列的先后受字符串哈希随机化影响）。

### 1.2 BM25 腿 —— **未同步，中文 2/8**（原始输出）

```
### BM25 索引: is_available=True  构建耗时=0.00s  indexed=28

── BM25 腿 (loader 自建 BM25SkillSearcher.search) ──
  [zh] q=写测试时要避免哪些反模式                 exp=testing-anti-patterns    -> []  MISS
  [zh] q=给测试加 Mock 有什么坑                   exp=testing-anti-patterns    -> ['testing-anti-patterns']  HIT
  [zh] q=生成后端接口时怎么加结构化日志和健康检查   exp=code-observability       -> ['memory_summary', 'self_reflection', 'pd-systematic-debugging-556faa20-skill', 'pd-test-driven-development-8562c8ad-skill', 'pd-subagent-driven-development-8c375695-skill']  MISS
  [zh] q=前后端状态不同步、有竞态该怎么防           exp=frontend-state-sync      -> []  MISS
  [zh] q=乐观更新回滚和请求取消怎么写             exp=frontend-state-sync      -> []  MISS
  [zh] q=做一个不用查文档就能看懂的自解释界面       exp=self-explanatory-ui      -> ['pd-writing-skills-5da20e67-skill']  MISS
  [zh] q=界面设计时怎么把帮助信息集成进去           exp=self-explanatory-ui      -> []  MISS
  [zh] q=代码交付前怎么做自测和审计报告            exp=engineering-test-delivery -> ['engineering-test-delivery']  HIT
  zh 命中 = 2/8
  [en] （8 条）… en 命中 = 8/8
```

**结论：证实**。"BM25 腿未同步"成立，且改前中文 **2/8 与 G1C-U1 记录的 TF-IDF 改前完全同量级**，
命中的 2 条也逐条对得上（`Mock` 英文借词 / `tags` 里的中文 `代码与工程`）。

### 1.3 向量腿 —— **中文 8/8（本卡预设被证伪）**（原始输出）

```
### 向量索引: backend=st_backend indexed=28 加载+索引耗时=57.5s degraded=False reason=None

── 向量腿 (adapter.search) ──
  [zh] q=写测试时要避免哪些反模式                 exp=testing-anti-patterns    -> ['testing-anti-patterns', ...]  HIT
  [zh] q=给测试加 Mock 有什么坑                   exp=testing-anti-patterns    -> ['testing-anti-patterns', ...]  HIT
  [zh] q=生成后端接口时怎么加结构化日志和健康检查   exp=code-observability       -> ['code-observability', ...]  HIT
  [zh] q=前后端状态不同步、有竞态该怎么防           exp=frontend-state-sync      -> ['frontend-state-sync', ...]  HIT
  [zh] q=乐观更新回滚和请求取消怎么写             exp=frontend-state-sync      -> ['frontend-state-sync', ...]  HIT
  [zh] q=做一个不用查文档就能看懂的自解释界面       exp=self-explanatory-ui      -> ['self-explanatory-ui', ...]  HIT
  [zh] q=界面设计时怎么把帮助信息集成进去           exp=self-explanatory-ui      -> ['self-explanatory-ui', ...]  HIT
  [zh] q=代码交付前怎么做自测和审计报告            exp=engineering-test-delivery -> ['engineering-test-delivery', ...]  HIT
  zh 命中 = 8/8
  [en] 第 1 条 HIT，其余 7 条 -> []  MISS ; en 命中 = 1/8     ← 既有缺陷，见 §7 R-2
```

⇒ **向量腿改前就已经 8/8**。它的中文来源是 BGE-m3 的跨语言能力 + `_build_vector_text` 拼进去的
`body` 摘要（`body` 里本来就有中文）。**`description_zh` 对它不是必要条件。**
任务书"一旦打开向量腿中文会退回 2/8"的说法，**在这一条腿上不成立**。

### 1.4 三条腿改前横评

```
TF-IDF 腿  zh 8/8   en 8/8      （G1C-U1 的成果）
BM25  腿  zh 2/8   en 8/8      ← 与 TF-IDF 改前同量级 = 定时炸弹成立
向量  腿  zh 8/8   en 1/8      ← 中文没问题；英文 1/8 是另一处既有缺陷
```

### 1.5 改后：BM25 腿 2/8 → 8/8（原始输出节选）

```
── BM25 腿 (loader 自建 BM25SkillSearcher.search) ──
  [zh] q=写测试时要避免哪些反模式                 -> ['testing-anti-patterns', ...]  HIT
  [zh] q=给测试加 Mock 有什么坑                   -> ['testing-anti-patterns', ...]  HIT
  [zh] q=生成后端接口时怎么加结构化日志和健康检查   -> ['code-observability', ...]  HIT
  [zh] q=前后端状态不同步、有竞态该怎么防           -> ['frontend-state-sync', ...]  HIT
  [zh] q=乐观更新回滚和请求取消怎么写             -> ['frontend-state-sync', ...]  HIT
  [zh] q=做一个不用查文档就能看懂的自解释界面       -> ['self-explanatory-ui', ...]  HIT
  [zh] q=界面设计时怎么把帮助信息集成进去           -> ['self-explanatory-ui', ...]  HIT
  [zh] q=代码交付前怎么做自测和审计报告            -> ['engineering-test-delivery', ...]  HIT
  zh 命中 = 8/8
  en 命中 = 8/8
```

### 1.6 改后：向量腿（原始输出节选）

```
### 向量索引: backend=st_backend indexed=28 加载+索引耗时=65.6s degraded=False reason=None
  zh 命中 = 8/8      ← 与改前相同
  en 命中 = 1/8      ← 与改前相同
```

### 1.7 向量腿 A/B 原始排序（绕开 §7 R-2 的负样本误过滤，直接比余弦 top-5）

A/B 的两边都在探针里**显式构造**（OLD = 改前四字段 front matter + body 摘要；
NEW = `loader._meta_to_meta_text(meta, name_fallback=sid)` + body 摘要），不依赖源码里
`_build_vector_text` 的当前形态；body 摘要走生产函数 `fs.load_instruction`（前 500 字符）。

```
### 向量文本变化的技能 = 20 / 28
### 字符数 old=19934 new=21498 (+1564)
### 生产 ensure_indexed 65.6s backend=st_backend
==== ZH ====
  q=写测试时要避免哪些反模式
      OLD top5=['testing-anti-patterns', 'frontend-state-sync', 'pd-systematic-debugging-556faa20-skill', 'code-observability', 'engineering-test-delivery']
      NEW top5=['testing-anti-patterns', 'frontend-state-sync', 'pd-systematic-debugging-556faa20-skill', 'pd-test-driven-development-8562c8ad-skill', 'code-observability']
      exp_sim 0.6170 -> 0.6351 (+0.0181)  top1 SAME
  q=给测试加 Mock 有什么坑
      exp_sim 0.5726 -> 0.6126 (+0.0400)  top1 SAME
  q=生成后端接口时怎么加结构化日志和健康检查
      exp_sim 0.6298 -> 0.6268 (-0.0031)  top1 SAME
  q=前后端状态不同步、有竞态该怎么防
      exp_sim 0.6459 -> 0.6628 (+0.0169)  top1 SAME
  q=乐观更新回滚和请求取消怎么写
      exp_sim 0.5115 -> 0.5325 (+0.0210)  top1 SAME
  q=做一个不用查文档就能看懂的自解释界面
      exp_sim 0.6923 -> 0.6876 (-0.0047)  top1 SAME
  q=界面设计时怎么把帮助信息集成进去
      exp_sim 0.6399 -> 0.6433 (+0.0034)  top1 SAME
  q=代码交付前怎么做自测和审计报告
      exp_sim 0.6543 -> 0.6449 (-0.0094)  top1 SAME
  ZH recall@5: OLD=8/8 NEW=8/8 ; top1 changed=0 ; top5 membership changed=4 ; exp_sim 升5/降3
==== EN ====
  q=what anti-patterns to avoid when writing tests   exp_sim 0.6605 -> 0.6689 (+0.0083)  top1 SAME
  q=pitfalls of adding mocks in tests                exp_sim 0.5782 -> 0.6078 (+0.0296)  top1 SAME
  q=structured logs and health check for backend api exp_sim 0.5847 -> 0.5784 (-0.0063)  top1 SAME
  q=frontend backend state out of sync race condition exp_sim 0.6515 -> 0.6463 (-0.0052)  top1 SAME
  q=optimistic update rollback and request cancellation exp_sim 0.4888 -> 0.5195 (+0.0307)  top1 SAME
  q=self explanatory interface without documentation exp_sim 0.6694 -> 0.6680 (-0.0013)  top1 SAME
  q=integrate help information into interface design exp_sim 0.6274 -> 0.6281 (+0.0007)  top1 SAME
  q=self testing and audit report before code delivery
      OLD top5=['engineering-test-delivery', 'pd-receiving-code-review-8934157e-skill', …]
      NEW top5=['pd-receiving-code-review-8934157e-skill', 'engineering-test-delivery', …]
      exp_sim 0.6293 -> 0.6156 (-0.0137)  top1 CHANGED
  EN recall@5: OLD=8/8 NEW=8/8 ; top1 changed=1 ; top5 membership changed=2 ; exp_sim 升4/降4
```

⇒ 把 `description_zh` 并进向量文本：**召回完全不变（中英各 8/8）**，
但排序**确有扰动**，而且**不许粉饰**成"零影响"：

· 中文：top1 **0 条**变、top5 成员 **4/8** 变；期望技能余弦 −0.0094 ~ +0.0400（升 5 降 3）；
· 英文：top1 **1 条**变（`self testing and audit report before code delivery`，
  `engineering-test-delivery` 掉到第 2，**仍在 top-5 内** ⇒ 召回率不变）；top5 成员 2/8 变；
  期望技能余弦 −0.0137 ~ +0.0307（升 4 降 4）。

⇒ **结论：向量腿这条改动"召回中性、排序有轻微扰动"**。收益是口径一致性 + 可开关性，
**不是召回**（§3.3 的取舍据此写成）。

---

## 2. 定位结论：这是"同形实现"，不是"配置不同"

改前三个函数各写了一遍"文档文本由哪些字段拼成"：

```python
# agent/skills_mgmt/loader.py:98（TF-IDF 腿；G1C-U1 已并入中文）
parts = [meta.get("name",""), meta.get("description",""),
         " ".join(meta.get("tags",[]) or []), meta.get("category","")]
if _include_description_zh():
    parts.append(meta.get("description_zh",""))

# agent/skills_mgmt/bm25_searcher.py:91（BM25 腿；无 description_zh）
parts = [meta.get("name","") or "", meta.get("description","") or "",
         " ".join(meta.get("tags",[]) or []), meta.get("category","") or ""]
return " ".join(p for p in parts if p)

# agent/skills_mgmt/vector_adapter.py:294（向量腿；无 description_zh）
parts = [meta.get("name", skill_id), meta.get("description",""),
         " ".join(meta.get("tags",[]) or []), meta.get("category","")]
front_text = " ".join(p for p in parts if p)
```

⇒ 同一语义有**三份实现**。G1C-U1 只改了其中一份，另外两份看不见 —— 这正是本审计反复出现的形态。

---

## 3. 第 2 步：改动 + 影响面

### 3.1 改动（本卡自己的行数，已剔除同工作区其它卡未提交的改动）

| 文件 | 本卡 diff | 改了什么 |
|---|---|---|
| `agent/skills_mgmt/loader.py` | **+17 / −2** | `_meta_to_meta_text(meta, *, name_fallback="")` 新增关键字参数（默认 `""` ⇒ 原调用方逐字不变）；docstring 增补"本函数是三条腿唯一 front-matter 来源" |
| `agent/skills_mgmt/bm25_searcher.py` | **+23 / −8** | `_skill_to_doc` **删掉自己的字段列表**，改为 `return _meta_to_meta_text(meta)`（函数内延迟导入，避免 loader↔bm25 导入期循环）；对象兜底 dict 补 `description_zh` |
| `agent/skills_mgmt/vector_adapter.py` | **+28 / −8** | `_build_vector_text` 的 front matter 改为 `_meta_to_meta_text(meta, name_fallback=skill_id)` |
| `tests/unit/test_three_legs_meta_zh_parity.py` | 新文件 400 行 / 15 用例 | §4 的护栏 |
| `tests/unit/test_skill_description_single_source.py` | 1 个用例被**替换为更强的 2 个** | 见 §3.4（**本卡推翻了 G1-B/M7 的裁定，必须显式报告**） |
| `agent/settings/registry.py` | **未改（0 行）** | 未新增 env ⇒ 无需登记 |
| `agent/skills_mgmt/searcher.py` | **未改（0 行）** | 第 4 个消费者，见 §5.3 |

**核心 diff（BM25 腿）**：

```diff
-    parts = [
-        meta.get("name", "") or "",
-        meta.get("description", "") or "",
-        " ".join(meta.get("tags", []) or []),
-        meta.get("category", "") or "",
-    ]
-    return " ".join(p for p in parts if p)
+    from .loader import _meta_to_meta_text  # 延迟导入：见上「为什么不再自己拼字段」
+    return _meta_to_meta_text(meta)
```

**核心 diff（向量腿）**：

```diff
-        parts = [
-            meta.get("name", skill_id),
-            meta.get("description", ""),
-            " ".join(meta.get("tags", []) or []),
-            meta.get("category", ""),
-        ]
-        front_text = " ".join(p for p in parts if p)
+        from .loader import _meta_to_meta_text  # 延迟导入
+        front_text = _meta_to_meta_text(meta, name_fallback=skill_id)
```

### 3.2 影响面（原始输出）

```
A. BM25 腿 影响面
BM25 文档文本变化的技能 = 20 / 28
token 总数: 改前 1306 -> 改后 2410 (+1104, +84.5%)
  [zh] 16 条…（逐条 top1 对照见下）
  [zh] 写测试时要避免哪些反模式   top1 -  -> testing-anti-patterns   TOP1变 成员变
  [zh] 给测试加 Mock 有什么坑     top1 testing-anti-patterns -> testing-anti-patterns  成员变
  [zh] 生成后端接口时…           top1 memory_summary -> code-observability  TOP1变 成员变
  [zh] 前后端状态不同步…         top1 - -> frontend-state-sync   TOP1变 成员变
  [zh] 乐观更新回滚…             top1 - -> frontend-state-sync   TOP1变 成员变
  [zh] 做一个不用查文档…         top1 pd-writing-skills-5da20e67-skill -> self-explanatory-ui  TOP1变 成员变
  [zh] 界面设计时…               top1 - -> self-explanatory-ui   TOP1变 成员变
  [zh] 代码交付前…               top1 engineering-test-delivery -> engineering-test-delivery  成员变
  [en] 8 条：top1 全部不变（6 条 top5 成员有尾部变化）
BM25 腿: 16 条 query 中 top1 变化 6 条、top5 成员变化 11 条
BM25 腿召回: 中文 2/8 -> 8/8 ; 英文 8/8 -> 8/8

B. 向量腿 影响面（只比文本，不加载模型）
向量文本变化的技能 = 20 / 28
字符数: 改前 19934 -> 改后 21498 (+1564)
样例 code-observability:
  OLD front = "code-observability Use when generating feature-module code or backend APIs. … external imported markdown custom"
  NEW front = "… custom 生成功能模块代码或后端 API 时使用。遵循"存在即可见"原则，强制输出结构化日志、显性错误边界、埋点预留与健康检查接口，且不引入昂贵的第三方付费依赖。"
全量重建阈值 = max(8, int(28*0.4)) = 11 ; 脏条数 = 20 => 触发一次全量重编码
```

**影响面汇总**：

| 项 | 数字 |
|---|---|
| 文档文本变化的技能 | **20 / 28**（= 全部带 `description_zh` 的技能；8 条无中文的技能逐字不变） |
| BM25 腿 16 条 query 的 top1 变化 | **6 条**（全部是中文 query；英文 8 条 top1 一条不变） |
| BM25 腿 16 条 query 的 top5 成员变化 | **11 条** |
| 向量腿 top-5 成员变化 | 中文 4/8、英文 2/8；**top1 变化：中文 0 条、英文 1 条**（该技能仍留在 top-5 ⇒ 召回不变） |
| 向量腿一次性代价 | 20 条脏 ≥ 阈值 11 ⇒ 首次启动走**一次**全量重编码：**28 条纯编码 46.7 s**（单条 1.668 s），模型加载另计 ≈ 18 s |
| 新增 env | **0 个** |

### 3.3 取舍说明：向量腿"零召回收益、有重编码代价"，为什么还是并了

· **并的理由**：不并 ⇒ 同一个语义仍然是"TF-IDF/BM25 受开关管、向量不受开关管"，
  这正是本卡要消灭的形态。向量腿的中文覆盖**当前**靠 BGE-m3 + body 摘要，不靠开关；
  一旦换模型 / 缩短 `body_summary_chars` / 技能没有中文 body，**没有任何开关能兜住它**。
· **代价**：一次性全量重编码（§3.2 实测 46.7 s）。置 `CP_SKILL_META_INCLUDE_ZH=0` ⇒
  文本与哈希**逐字回改前**（`tests/unit/test_three_legs_meta_zh_parity.py::TestVectorHashFollowsTheSameSwitch::test_switch_off_restores_pre_change_text_and_hash` 用 md5 相等证明），
  **不会重复触发重编码**。
· **不许只说好话**：这条改动**没有提升任何召回率**，收益是纯粹的口径一致性 + 可开关性；
  代价除了 46.7 s 的一次性重编码，还包括**排序扰动**（§1.7：中文 top5 成员 4/8 变、
  英文 top1 有 1 条被换，期望技能余弦最差 −0.0137）。
  如果评审认为"零召回收益 + 有排序扰动 ⇒ 不该动"，回滚方式见 §8（一行 env 或 §8.2 六行代码）。

### 3.4 【必须显式报告】本卡推翻了 G1-B/M7 的一条既有裁定

`tests/unit/test_skill_description_single_source.py` 的 `TestVectorHashUnaffected` 原断言是：

```python
def test_zh_does_not_change_vector_hash(self, meta_index, file_store):
    """V-guard：加 description_zh **不改变** _vector_text_and_hash 的哈希。
    红路：让 _build_vector_text 把 description_zh 拼进去 ⇒ 红灯（那会导致一次
    不必要的全量重编码，BGE-m3 4.25 GB）。"""
```

它把 G1-B 的"向量**永不**并入中文"钉成了断言。本卡改口径后它**必然红**（实测红：
`AssertionError: description_zh 改变了向量哈希: [...28 条...]`）。

**处理方式（不是放宽）**：把它**替换**为同一诉求的**更强**形式：

· 关开关 ⇒ 向量文本与哈希**md5 逐字回改前**（原来的"不会重编码"诉求，被加强为"可回滚"）；
· 开开关 ⇒ 变化集合**恰好等于** 20 条双轨技能（多一条少一条都红）。

⇒ 该文件 **32 passed → 33 passed**（−1 +2）。**没有任何断言被放宽**，被替换掉的那条是**方向相反**的旧口径。

---

## 4. 第 3 步：把"三腿同口径"钉成护栏（本卡最重要的产出）

新文件 `tests/unit/test_three_legs_meta_zh_parity.py`，**15 个用例**，四组：

| 组 | 抓什么 | 关键断言 |
|---|---|---|
| `TestThreeLegsShareOneFieldList` | **字段列表分叉** | `test_all_three_front_texts_are_byte_identical`：对全部 28 条技能，三腿的 front-matter 文本 **`a == b == c` 逐字相同** |
| 〃 | **"碰巧相等"也不行** | `test_legs_delegate_to_the_canonical_builder`：`inspect.getsource` 断言 BM25 / 向量两条腿的源码里**必须出现 `_meta_to_meta_text`**（结构上是委托，不是各自维护一份列表） |
| `TestOneSwitchDecidesAllThreeLegs` | **第二个开关 / 缓存开关** | `test_patching_the_single_gate_flips_all_three_together`：`monkeypatch.setattr(loader, "_include_description_zh", lambda: False)`（**不碰任何 env**）⇒ 三腿必须**一起**翻；有一条腿还带着中文 ⇒ 红 |
| 〃 | **第二个读取点** | `test_exactly_one_env_read_point_in_the_whole_agent_package`：正则扫 `agent/**.py` 的**真实读取表达式**（`os.environ.get` / `os.environ[...]` / `os.getenv`，并解析"常量间接"写法），结果必须**恰好** `{"agent/skills_mgmt/loader.py": 1}` |
| `TestLegRecallIsLocked` | **数字回退** | BM25 腿中文 **8/8**、英文 **8/8**、置 0 ⇒ 中文 **2/8**；TF-IDF 腿中文仍 **8/8** |
| `TestVectorHashFollowsTheSameSwitch` | **向量腿的开关与代价** | 置 0 ⇒ 文本/哈希 md5 回改前；置 1 ⇒ 变化集合 == 双轨集合；脏条数 ≥ 全量重建阈值 |

### 4.1 它为什么能防住"第四次只修一条腿"

未来再有人只改一条腿，会**同时**撞上三道闸：

1. **源码级**：只要那条腿不再 `from .loader import _meta_to_meta_text`（或改回自己拼字段），
   `test_legs_delegate_to_the_canonical_builder` 立刻红 —— 它不依赖行为巧合。
2. **行为级**：三腿文本必须逐字相同 ⇒ 任何字段/顺序/开关的口径差异都会被 `a == b == c` 抓到。
3. **开关级**：`_include_description_zh` 被打桩后三腿必须一起翻 ⇒ 新开一个 env、或在导入期
   缓存开关、或自己读 env，都会被 `test_patching_the_single_gate_flips_all_three_together` /
   `test_exactly_one_env_read_point_in_the_whole_agent_package` 抓住。

### 4.2 改后绿（原始输出）

```
$ python -m pytest tests/unit/test_three_legs_meta_zh_parity.py -q
15 passed in 2.47s
```

### 4.3 非空转自证（三种"去掉修复"的姿态，逐条贴断言原文）

**实验前（最终态）sha256**：

```
45D5B358F2585F76113E9A1F64B17482A59FE6757A2D261E34D7D84B31E8E59B  loader.py
F9E5C62EB59D6B80C50C5232A710B0B8FEAA0F534DE60895427961E83E1DBDD7  bm25_searcher.py
BB9009AABFB682316777B4801D3B23683CA18DDE7B6410A7CE15A1D0B154E859  vector_adapter.py
CC9854CC664F62865F00813FA6F52521F51E0AFE3D8481FCF2AF6B36FBF10C86  test_three_legs_meta_zh_parity.py
2EB7DA52C7EF2104D1C8DC2F51499498D5B32F39BF67453DD6B1A4672CEB9614  test_skill_description_single_source.py
```

#### 变体 A：**只**把 BM25 腿改回旧的四字段实现（另两条腿不动）

```
E   AssertionError: BM25 腿中文命中 = 2/8，钉死值 = 8（改前实测 2）
    assert 2 == 8

E   AssertionError: 三条腿的文档文本口径不一致（同形实现又分叉了）：20/28 条技能不等，前 3 条
    （取各自文本**末尾** 60 字符，description_zh 是追加在末尾的）= {
     'code-observability': {'tfidf': '…强制输出结构化日志…付费依赖。',
                             'bm25':  ' third-party dependencies. external imported markdown custom',
                             'vector':'…强制输出结构化日志…付费依赖。'}, …}

E   AssertionError: 有腿的文档文本里没有 description_zh（中文 query 命中不了它）：20/20 条技能，
    前 3 条 = {'code-observability': {'bm25': 0}, 'engineering-test-delivery': {'bm25': 8}, …}

E   AssertionError: 开关开时这些腿没并入 description_zh: ['bm25']
E   AssertionError: 前置不成立：默认三腿都应含中文

FAILED …::TestOneSwitchDecidesAllThreeLegs::test_patching_the_single_gate_flips_all_three_together
FAILED …::TestOneSwitchDecidesAllThreeLegs::test_env_switch_on_puts_zh_into_all_three_legs
FAILED …::TestLegRecallIsLocked::test_bm25_leg_chinese_recall
FAILED …::TestThreeLegsShareOneFieldList::test_dual_track_skills_have_chinese_in_all_three_legs
FAILED …::TestThreeLegsShareOneFieldList::test_all_three_front_texts_are_byte_identical
======================== 5 failed, 10 passed in 2.60s =========================
```

#### 变体 B：**只**把向量腿改回旧的四字段实现（另两条腿不动）

```
E   AssertionError: 三条腿的文档文本口径不一致（同形实现又分叉了）：20/28 条技能不等，前 3 条…
    {'code-observability': {'tfidf': '…付费依赖。', 'bm25': '…付费依赖。',
                            'vector': ' third-party dependencies. external imported markdown custom'}, …}
E   AssertionError: 有腿的文档文本里没有 description_zh（中文 query 命中不了它）：20/20 条技能，
    前 3 条 = {'code-observability': {'vector': 0}, 'engineering-test-delivery': {'vector': 8}, …}
E   AssertionError: 开关开时这些腿没并入 description_zh: ['vector']
E   AssertionError: 前置不成立：默认三腿都应含中文
E   AssertionError: 文本变化集合与双轨集合不符：多改 [] / 少改 ['code-observability', …共 20 条]

FAILED …::TestVectorHashFollowsTheSameSwitch::test_switch_on_changes_text_only_for_dual_track
FAILED …::TestThreeLegsShareOneFieldList::test_dual_track_skills_have_chinese_in_all_three_legs
FAILED …::TestThreeLegsShareOneFieldList::test_all_three_front_texts_are_byte_identical
FAILED …::TestOneSwitchDecidesAllThreeLegs::test_patching_the_single_gate_flips_all_three_together
FAILED …::TestOneSwitchDecidesAllThreeLegs::test_env_switch_on_puts_zh_into_all_three_legs
======================== 5 failed, 10 passed in 2.61s =========================
```

**这两条正是本卡要的证明："任何一条腿被单独改动、另两条不动" ⇒ 立刻红。**

#### 变体 C：两条腿都改回旧实现（= 整卡修复被移除）

```
FAILED …::TestVectorHashFollowsTheSameSwitch::test_switch_on_changes_text_only_for_dual_track
FAILED …::TestLegRecallIsLocked::test_bm25_leg_chinese_recall
FAILED …::TestThreeLegsShareOneFieldList::test_dual_track_skills_have_chinese_in_all_three_legs
FAILED …::TestThreeLegsShareOneFieldList::test_all_three_front_texts_are_byte_identical
FAILED …::TestOneSwitchDecidesAllThreeLegs::test_env_switch_on_puts_zh_into_all_three_legs
FAILED …::TestOneSwitchDecidesAllThreeLegs::test_patching_the_single_gate_flips_all_three_together
======================== 6 failed, 9 passed in 2.61s =========================
```

#### 还原后：逐字节相同 + 绿

```
### 还原后 vs 实验前 逐字节比对
loader.py                                  IDENTICAL  45D5B358F2585F76113E9A1F64B17482A59FE6757A2D261E34D7D84B31E8E59B
bm25_searcher.py                           IDENTICAL  F9E5C62EB59D6B80C50C5232A710B0B8FEAA0F534DE60895427961E83E1DBDD7
vector_adapter.py                          IDENTICAL  BB9009AABFB682316777B4801D3B23683CA18DDE7B6410A7CE15A1D0B154E859
test_three_legs_meta_zh_parity.py          IDENTICAL  CC9854CC664F62865F00813FA6F52521F51E0AFE3D8481FCF2AF6B36FBF10C86
test_skill_description_single_source.py    IDENTICAL  2EB7DA52C7EF2104D1C8DC2F51499498D5B32F39BF67453DD6B1A4672CEB9614

$ python -m pytest tests/unit/test_three_legs_meta_zh_parity.py -q
15 passed in 2.47s
```

---

## 5. 逃生开关的一致性说明

### 5.1 只有一个开关：`CP_SKILL_META_INCLUDE_ZH`

· **复用**，**没有**新造。新增 env = **0 个**，所以 `agent/settings/registry.py` **未改**。
· 唯一的读取点仍是 `agent/skills_mgmt/loader.py:92` 的 `os.environ.get(_ENV_META_INCLUDE_ZH)`；
  三条腿都通过调用 `_meta_to_meta_text()` 间接读它 ⇒ 结构上不可能"关了一个、另一个还开着"。
· 该结论有两条自动化护栏：`test_exactly_one_env_read_point_in_the_whole_agent_package`（静态）与
  `test_patching_the_single_gate_flips_all_three_together`（行为）。

### 5.2 有没有"某条腿结构上没法复用开关"的情况？——没有

三条腿的文档文本都是"从一个 `meta` dict 拼出一段 front matter"，语义完全同构，
所以**不需要**任何"第二个开关如何保持一致"的方案。延迟导入（`from .loader import ...` 写在函数体内）
而不是模块顶层，纯粹是为了避开 `loader ↔ {bm25_searcher, vector_adapter}` 的**导入期**循环
（`loader` 本来就是在 `_get_bm25_searcher()` / `_get_vector_adapter()` 里延迟导入这两个模块的）。

### 5.3 第 4 个消费者：`searcher._match_score`（**未改，如实登记**）

`agent/skills_mgmt/searcher.py`（**管理页搜索**，不在生产检索链上）也把 `description_zh` 并进描述打分，
但它是 **G1-C/R-d 的另一个语义**："管理页**显示**的就是 `description_zh` ⇒ 搜索必须与显示同源"，
因此它**无条件**并入，逃生口是"调用方不传 `meta_index`"（`meta_index=None` ⇒ 与修复前逐字相同）。

**本卡没有动它**，理由：① 它是第 4 个消费者不是第 3 条腿，改它会改变 G1-C 的既有裁定与测试；
② 把它挂到 `CP_SKILL_META_INCLUDE_ZH` 上会让"管理页搜到的"与"管理页看到的"不一致（比现在更糟）。
**风险登记见 §7 R-4**：如果未来要统一，正确做法是**新增**一条"展示侧同源"的口径，而不是把两者合并。

---

## 6. 回归结果（全部原始计数）

| 测试文件 | 结果 | 基线 |
|---|---|---|
| `tests/unit/test_three_legs_meta_zh_parity.py`（本卡新增） | **15 passed** | — |
| `tests/unit/test_skill_meta_zh_recall.py` | **46 passed** | 46 |
| `tests/unit/test_skill_description_single_source.py` | **33 passed** | 32 → **33**（§3.4：1 个用例被替换为 2 个更强的，**非放宽**） |
| `tests/unit/test_skills_mgmt.py` | **75 passed, 1 xfailed** | 同 |
| `tests/unit/test_vector_skill_searcher.py` | **9 passed** | 同 |
| `tests/unit/test_bm25_skill_searcher.py` | **26 passed** | 同 |
| `tests/unit/test_settings_registry.py` | **56 passed** | **56 passed（基线一致；本卡未新增 env）** |
| **7 个文件一起跑** | **260 passed, 1 xfailed, 0 failed** | — |

**没有任何断言被放宽。** 唯一的断言变更是 §3.4 那次"方向相反"的口径替换（严格性提高）。

---

## 7. 未验证项与残留风险（不许粉饰）

| # | 项 | 状态 | 说明 |
|---|---|---|---|
| **R-1** | **`use_bm25=True` 的端到端中文召回仍只有 4/8** | ⚠️ **实测，未修（不在本卡范围）** | 修好 BM25 腿后，`ld.match(use_bm25=True)` 的中文命中 **3/8 → 4/8**，**仍不是 8/8**。原因是 **RRF 质量闸**：`rrf.quality_gate.check` 只用 `bounded_similarity`（= `tfidf_score`）与阈值 0.3 比较，而 BM25 的强证据（`bm25_rank=1`、`rrf_normalized=0.9866`）**不参与**该判定。原始日志见下。**建议另开卡**。 |
| **R-2** | **向量腿英文召回 1/8（既有缺陷，本卡未修）** | ⚠️ **实测** | `vector_adapter._NEGATIVE_PATTERNS[1] = ^[a-zA-Z_][a-zA-Z0-9_ ]*$` 在 `search()` 里**对所有后端无条件生效**，把"纯 ASCII 字母/数字/空格"的 query 一律当负样本过滤掉 ⇒ 8 条英文 query 里 7 条返回 `[]`。绕开它直接比余弦，英文其实是 **8/8**（§1.7）。**建议另开卡**（`_is_negative_query` 的 docstring 自己写着"只在 BM25 fallback 模式下生效"，实现与注释不符）。 |
| **R-3** | **向量腿的回召提升未在本卡验证** | ❌ 未验证 | 本卡证明了"并入 `description_zh` 不改变向量召回"（8/8→8/8，top1 0 变化）。"换成更弱的模型 / body 摘要为空时，并入中文能否兜住"**没有验证**"（需要改模型，超出本卡预算）。 |
| **R-4** | `searcher._match_score` 的中文并入**不受本开关控制** | ⚠️ 已登记 | §5.3。它是"展示同源"语义，不是第二套开关；但**对"改一个 env 就能关掉所有中文召回"的运维期望而言，它是一个例外**。 |
| **R-5** | 向量腿首次启动的一次性重编码 | ⚠️ 已量化 | 20 条脏 ≥ 阈值 11 ⇒ **一次**全量重编码：28 条纯编码 **46.7 s**、单条 1.668 s，另加模型加载 ≈ 18 s。**上线时第一次检索会慢**（`_SEARCH_TIMEOUT_SECONDS=2.0` 覆盖不到这段，属既有残留）。置 `CP_SKILL_META_INCLUDE_ZH=0` 可完全避开。 |
| **R-6** | 向量腿召回数字来自**本机单次实测** | ⚠️ 口径 | BGE-m3 在 `HF_HUB_OFFLINE=1` 下从本地 cache 加载（391 个权重分片），未验证与在线加载的差异。 |
| **R-7** | 单元测试不覆盖向量腿的真实召回 | ⚠️ 有意 | 加载 BGE-m3（≈18 s / 450 MB）不在单元测试预算内。护栏只断言向量腿的**文本构造**（= 口径出问题的地方），召回靠探针实测。**这是本卡护栏的已知覆盖边界。** |

**R-1 的原始日志（`写测试时要避免哪些反模式`，`use_bm25=True`）**：

```
INFO agent.skills_mgmt: {'action': 'rrf.paths_before_fuse', 'tfidf_top1': 'pd-finishing-a-development-branch-e085de5a-skill', 'vector_top1': None, 'bm25_top1': 'testing-anti-patterns', 'tfidf_candidate_count': 4, 'vector_candidate_count': 0, 'bm25_candidate_count': 4, 'use_bm25': True, 'rrf_k': 60}
INFO agent.skills_mgmt: {'action': 'rrf.quality_gate.check', 'top1_skill_id': 'testing-anti-patterns', 'top1_rrf_normalized': 0.9866, 'bounded_similarity': 0.0909, 'bounded_keys_declared': ['tfidf_score', 'vector_score'], 'bm25_raw_unbounded': 2.5441, 'effective_score': 0.0909, 'threshold': 0.3, 'use_bm25': True, 'decision': 'reject'}
WARNING agent.skills_mgmt: {'action': 'rrf.quality_gate.rejected', 'reason': 'bounded_similarity_below_threshold', 'fused_count': 4}
RESULT []
```

⇒ BM25 腿把正确的技能顶到第 1 名（`rrf_normalized=0.9866`），质量闸仍因 `tfidf_score=0.0909 < 0.3` **整单拒绝**。
**这是"开了 BM25 反而比只开 TF-IDF 差"的直接原因**（8/8 → `use_bm25=True` 4/8），**本卡未修**。

---

## 8. 回滚

### 8.1 只回滚"向量腿并入中文"（保留 BM25 腿的修复，一行）

在 `.env` / 主机环境写：

```
CP_SKILL_META_INCLUDE_ZH=0
```

⇒ TF-IDF 腿、BM25 腿、**向量腿一起**逐字回到改前（护栏 `TestVectorHashFollowsTheSameSwitch` 的
`md5` 断言证明向量文本/哈希逐字相同，且**不会**触发重编码）。
代价：BM25 腿中文回到 2/8、TF-IDF 腿中文回到 2/8（G1C-U1 的成果一并回滚）。

### 8.2 只回滚"向量腿"（保留 TF-IDF/BM25 的开关行为）

把 `agent/skills_mgmt/vector_adapter.py` 的 `_build_vector_text` 里这一行：

```python
front_text = _meta_to_meta_text(meta, name_fallback=skill_id)
```

换回 §3.1 diff 里的 6 行 `parts = [...]` + `front_text = " ".join(...)`。
**注意**：这样护栏会红（`test_all_three_front_texts_are_byte_identical` /
`test_switch_on_changes_text_only_for_dual_track`）—— 那是**有意的**，它就是"又分叉了"的告警。

### 8.3 全卡回滚

把 `_skill_to_doc` 与 `_build_vector_text` 都换回 §3.1 的两个 `parts = [...]` 块，
并删除 `tests/unit/test_three_legs_meta_zh_parity.py`。
`loader.py` 的 `name_fallback` 参数**可以保留**（默认 `""` ⇒ 与改前逐字相同），
`tests/unit/test_skill_description_single_source.py` 的 V-guard 需同时换回旧断言。

---

## 9. 残留物自证

· **仓库内新增文件**：`tests/unit/test_three_legs_meta_zh_parity.py`（本卡护栏）、
  `docs/audit_skill_governance/G1C-UA.md`（本报告）。**没有别的**。
· **探针全部在仓库外**：`C:\Users\Administrator\AppData\Local\Temp\g1cua\`
  （`probe_legs.py` / `probe_vector_ab.py` / `probe_impact.py` / `probe_fusion.py` /
  `probe_enc_time.py` / `count_mine.py` / `hunks.py` / `reverse_patch.py` / `backup\` / `backup2\`）。
· **临时还原痕迹已清零**：三个源文件还原后 sha256 与实验前**逐字节相同**（§4.3）。
· **无新增 env** ⇒ `agent/settings/registry.py` 零改动。
· **未提交**：全程 `git add` / `git commit` **0 次**；`HEAD` 仍为 `5c9ace10a4ca4bb96860db3a48debf9ddcf496bf`。
· **未 kill 任何 python 进程**；收尾实测仍在跑的 2 个 python 属于**其它卡**的 pytest
  （命令行分别为 `test_settings_registry.py … test_det4_stable_sort_determinism.py` 与 `test_route_conflict_cases.py`），
  本卡启动的进程**全部已退出**。
· **未启动任何常驻服务**；未触碰 `agent/tool_router*.py`、`agent/audit/`、`plugins/`、`yunshu-ui/`、
  `data/skills_repo/`、`config.yaml`、prompt 装配四件套、`data/audit/daily_roots.jsonl`、`data/learned_workflows.json`。

---

## 10. 一张图总结

```
改前:
  TF-IDF 腿 ─┐
  BM25   腿 ─┼─ 各自一份 "name/description/tags/category" 字段列表
  向量   腿 ─┘   ⇑ 只有这一份被 G1C-U1 加上了 description_zh
                ⇒ BM25 腿中文 2/8（定时炸弹），向量腿口径不受开关管

改后:
  TF-IDF 腿 ─┐
  BM25   腿 ─┼─→ loader._meta_to_meta_text(meta, *, name_fallback="")
  向量   腿 ─┘        ↑ 唯一实现 · 唯一开关 CP_SKILL_META_INCLUDE_ZH
                ⇒ BM25 腿中文 8/8；三腿文本逐字相同（护栏钉死）
```

