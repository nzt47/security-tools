# TASK-10 · 既有路由可验证化基线报告（机器生成）

- HEAD: 220417b010b957b1e25b953fabd8231e0efb636a
- 语义层状态: {"retriever_available": true, "degraded_bm25_only": true, "embedding_available": false, "bm25_docs": 90, "alpha": 0.5, "index_path": "data/tool_index.json", "embedding_health": {"mode": "hybrid", "init_failed": false, "worker_alive": false, "available": false, "failure_total": 0, "restart_attempts": 0, "max_restart_attempts": 3, "restarting": false, "retry_exhausted": false, "next_restart_in_sec": null, "last_failure": {}, "retriever_degraded": true}}

## 1. 意图类目表（机器派生）

- 来源：agent/tool_router.py:308 TOOL_CATEGORIES（YAML 存在时由 _load_tool_categories_from_yaml() 于 :238 从 data/tool_definitions/*.yaml 派生）
- 类别数 11，工具总数 90：async, code, core, extension, file, knowledge, pdf, schedule, system, v2, web
- 每类工具数：{"async": 7, "code": 15, "core": 5, "extension": 12, "file": 13, "knowledge": 7, "pdf": 5, "schedule": 5, "system": 8, "v2": 4, "web": 9}

## 2. 每层读数

| 层 | n | 覆盖率 | 拒识率 | 准确率(全量) | 95%CI | 已声明准确率 | 声明但错 |
|---|---|---|---|---|---|---|---|
| rule | 41 | 0.9756 | 0.0244 | 1.0 | [1.0, 1.0] | 1.0 | 0 |
| template | 16 | 0.8125 | 0.1875 | 1.0 | [1.0, 1.0] | 1.0 | 0 |
| keyword_category | 8 | 1.0 | 0.0 | 0.875 | [0.625, 1.0] | 0.875 | 1 |
| hybrid_tool | 10 | 1.0 | 0.0 | 0.9 | [0.7, 1.0] | 0.9 | 1 |

三层合计（按文本去重，n=62）：覆盖率 0.9355，拒识率 0.0645

## 3. 校准（温度缩放 + ECE）

### template

- 语义：IntentRouter 的 Confidence 枚举值（HIGH=0.9/MEDIUM=0.6/LOW=0.3），直接当作系统自报正确概率 p0
- 校准集 n=7 / 测试集 n=9
- T = 1.035686（NLL 0.41925 -> 0.419032）
- ECE 改前 = 0.2334 -> 改后 = 0.238；MCE 0.7 -> 0.6938
- 补充（**非留出**，全标注集）ECE 改前 = 0.25 -> 改后 = 0.2541

| 箱 | 区间 | n | 平均置信 | 实际准确 | 差 |
|---|---|---|---|---|---|
| 0 | [0.0, 0.1] | 0 | None | None | None |
| 1 | [0.1, 0.2] | 0 | None | None | None |
| 2 | [0.2, 0.3] | 0 | None | None | None |
| 3 | [0.3, 0.4] | 1 | 0.3062 | 1.0 | 0.6938 |
| 4 | [0.4, 0.5] | 0 | None | None | None |
| 5 | [0.5, 0.6] | 2 | 0.5966 | 1.0 | 0.4034 |
| 6 | [0.6, 0.7] | 0 | None | None | None |
| 7 | [0.7, 0.8] | 0 | None | None | None |
| 8 | [0.8, 0.9] | 6 | 0.893 | 1.0 | 0.107 |
| 9 | [0.9, 1.0] | 0 | None | None | None |

### hybrid_tool

- 语义：HybridRetriever.query 的融合分（_min_max_normalize 后 min-max 归一化）
- 融合分经 min-max 归一化后 top1 恒为 1.0 ⇒ 该分数不携带置信度信息，在其上做温度缩放/ECE 无意义（改判定语义不在本任务范围，故只记录事实）

### hybrid_raw_bm25

- 语义：对原始（未 min-max 归一化）BM25 top-5 分做 softmax 后的 top1 概率
- 校准集 n=5 / 测试集 n=5
- T = 9.965784（NLL 2.763103 -> 0.500402）
- ECE 改前 = 0.0672 -> 改后 = 0.2566；MCE 0.3361 -> 0.4829
- 补充（**非留出**，全标注集）ECE 改前 = 0.1336 -> 改后 = 0.1283

| 箱 | 区间 | n | 平均置信 | 实际准确 | 差 |
|---|---|---|---|---|---|
| 0 | [0.0, 0.1] | 0 | None | None | None |
| 1 | [0.1, 0.2] | 0 | None | None | None |
| 2 | [0.2, 0.3] | 0 | None | None | None |
| 3 | [0.3, 0.4] | 0 | None | None | None |
| 4 | [0.4, 0.5] | 0 | None | None | None |
| 5 | [0.5, 0.6] | 1 | 0.5171 | 1.0 | 0.4829 |
| 6 | [0.6, 0.7] | 0 | None | None | None |
| 7 | [0.7, 0.8] | 4 | 0.8 | 1.0 | 0.2 |
| 8 | [0.8, 0.9] | 0 | None | None | None |
| 9 | [0.9, 1.0] | 0 | None | None | None |

## 4. 拒识阈值取舍曲线

- 信号：hybrid_raw_bm25 softmax top1（经 T=9.965784 温度缩放）

### 4a 标注折（n=10）—— 可给 risk

| 阈值 | 覆盖率 | 拒识率 | 接受数 | 拒绝数 | 接受子集错误率 | 被接受但错的用例 |
|---|---|---|---|---|---|---|
| 0.0 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.05 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.1 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.15 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.2 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.3 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.4 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.5 | 1.0 | 0.0 | 10 | 0 | 0.1 | TOOL-006 |
| 0.6 | 0.9 | 0.1 | 9 | 1 | 0.1111 | TOOL-006 |
| 0.7 | 0.9 | 0.1 | 9 | 1 | 0.1111 | TOOL-006 |
| 0.8 | 0.0 | 1.0 | 0 | 10 | None |  |
| 0.9 | 0.0 | 1.0 | 0 | 10 | None |  |

### 4b 仓内真实语料折（n=78，无标注）—— 只给覆盖/拒识

| 阈值 | 覆盖率 | 拒识率 | 接受数 | 拒绝数 | 分词料覆盖 |
|---|---|---|---|---|---|
| 0.0 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.05 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.1 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.15 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.2 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.3 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.4 | 1.0 | 0.0 | 78 | 0 | {"corpus_evaltasks": 1.0, "corpus_negative": 1.0, "corpus_positive": 1.0} |
| 0.5 | 0.9615 | 0.0385 | 75 | 3 | {"corpus_evaltasks": 1.0, "corpus_negative": 0.9545, "corpus_positive": 0.9512} |
| 0.6 | 0.7308 | 0.2692 | 57 | 21 | {"corpus_evaltasks": 0.8667, "corpus_negative": 0.8636, "corpus_positive": 0.6098} |
| 0.7 | 0.5769 | 0.4231 | 45 | 33 | {"corpus_evaltasks": 0.6667, "corpus_negative": 0.7273, "corpus_positive": 0.4634} |
| 0.8 | 0.4487 | 0.5513 | 35 | 43 | {"corpus_evaltasks": 0.6, "corpus_negative": 0.5, "corpus_positive": 0.3659} |
| 0.9 | 0.0 | 1.0 | 0 | 78 | {"corpus_evaltasks": 0.0, "corpus_negative": 0.0, "corpus_positive": 0.0} |

- 真实语料校准后分数分位：{"p10": 0.5243, "p50": 0.7617, "p90": 0.8, "min": 0.4921, "max": 0.8}

## 5. 外部真实语料的层声明率（无标注，只看漏斗行为）

| 语料 | n | 来源 | 规则层声明率 | 模板层声明率 | 语义层声明率 | 规则∩模板 |
|---|---|---|---|---|---|---|
| corpus_evaltasks | 15 | data\evals\chat\dialog_flows.json | 0.1333 | 0.2 | 1.0 | 0.0667 |
| corpus_negative | 25 | tests/eval/negative_samples_extended.json | 0.16 | 0.2 | 0.88 | 0.08 |
| corpus_positive | 45 | tests/eval/skill_retrieval_golden_set.json | 0.0444 | 0.0222 | 0.9111 | 0.0 |


## 5b 建议阈值

- 结论：不建议在当前信号上启用该阈值（证据不足 + 信号无区分度）
- 依据1：标注折上唯一的错误（见 F6）其校准后分数位于被接受样本的中位区间：任何能拒掉它的阈值（>=0.8）会把覆盖率打到 0（softmax 分数上限 0.8）。即该分数对本基线观测到的错误**无区分度**。
- 依据2：标注折 n=10、校准折 n=5，均远低于 drift_trigger.min_samples=200。
- 条件取值（若上级强制要一个数）：阈值 0.5，真实语料覆盖率 0.9615，拒识率 0.0385（真实语料折 n=78，该点是满足拒识率上限的最大覆盖率点）
- 条件取值的警示：它在标注折上不降低 risk（仍 0.1）⇒ 只是'少答一点'，不是'答得更准'

## 6. 结构化结论（由读数机械导出）

- **F1 [blocker] 规则层不产出置信度 ⇒ 该层不可校准**
  - 证据：agent/workflow_engine/engine.py:57 把 WorkflowResult.confidence 硬编码为 1.0；实测 rule 层 41 条用例的 confidence 全部为 1.0
  - 影响：温度缩放/ECE 在规则层无对象；规则层只能报告准确率与覆盖率
- **F2 [blocker] 语义层融合分被 min-max 归一化抹平 ⇒ 该分数不携带置信度**
  - 证据：agent/tool_router_hybrid.py:1154-1167 _min_max_normalize 把每路召回的 max 映射为 1.0；实测 fused_top_scores=[1.0]
  - 影响：直接在该分数上做拒识等价于'永不拒识'；本基线改用未归一化的原始 BM25 分作为可校准出口
- **F3 [high] 本环境 Embedding 路不可用，语义层实际运行在 BM25-only 降级路**
  - 证据：{"retriever_available": true, "degraded_bm25_only": true, "embedding_available": false, "bm25_docs": 90, "alpha": 0.5, "index_path": "data/tool_index.json", "embedding_health": {"mode": "hybrid", "init_failed": false, "worker_alive": false, "available": false, "failure_total": 0, "restart_attempts": 0, "max_restart_attempts": 3, "restarting": false, "retry_exhausted": false, "next_restart_in_sec": null, "last_failure": {}, "retriever_degraded": true}}
  - 影响：所有语义层读数描述降级路；AGENT_HYBRID_ALPHA=0.5 的融合路未被本次基线覆盖
- **F4 [medium] 原始 BM25 softmax 分数可校准：NLL 大幅下降，但 ECE 在 n=5 的测试折上不可解释**
  - 证据：T=9.965784，NLL 2.763103 -> 0.500402；测试折 ECE 0.0672 -> 0.2566，全标注集 ECE 0.1336 -> 0.1283
  - 影响：测试折 n=5，ECE 由'哪 5 条落到折里'决定；只能采信 NLL 的下降方向，不能采信 ECE 的绝对差
- **F5 [medium] 模板层的失配是枚举上限问题，不是温度问题 ⇒ 温度缩放修不动它**
  - 证据：T=1.035686（≈1，几乎未改），ECE 0.2334 -> 0.238
  - 影响：该折 accuracy=1.0 而最高自报置信度只有 0.9（Confidence.HIGH），先把上限抬到 1.0 才能谈温度；本任务不改判定语义，故只记录
- **F6 [high] 语义层在真实 90 工具索引上存在实际误召回**
  - 证据：[{"id": "TOOL-006", "text": "解析pdf", "gold": "read_pdf", "pred": "run_lint", "raw_bm25_top5": [["run_lint", 72.4669], ["decompress", 32.8902], ["split_pdf", 24.4339], ["read_pdf", 24.3426], ["read_pdf_tables", 21.824]]}]
  - 影响：例：中文查询'解析pdf'在真实索引上 top1=run_lint（合成 5 工具索引下该单测是过的）⇒ 原单测的'召回正确'结论不覆盖真实索引
- **F7 [medium] 关键词类别层存在既定误分类（仓内自带用例即已失败）**
  - 证据：[{"id": "CAT-003", "text": "执行命令", "gold": ["core", "code", "system"], "pred": ["code", "core"]}]
  - 影响：该层 8 条可用用例中 1 条不满足其自身期望集合（见上）
- **F8 [high] 语义层在真实语料上几乎不做拒识（覆盖率 0.88–1.00）**
  - 证据：{"corpus_evaltasks": {"n": 15, "source": "data\\evals\\chat\\dialog_flows.json", "label": "data/evals 三类真实任务样本（15 条）", "rule_claim_rate": 0.1333, "template_claim_rate": 0.2, "hybrid_claim_rate": 1.0, "both_rule_and_template": 0.0667}, "corpus_negative": {"n": 25, "source": "tests/eval/negative_samples_extended.json", "label": "expected_skill_ids 为空（25 条负样本）", "rule_claim_rate": 0.16, "template_claim_rate": 0.2, "hybrid_claim_rate": 0.88, "both_rule_and_template": 0.08}, "corpus_positive": {"n": 45, "source": "tests/eval/skill_retrieval_golden_set.json", "label": "expected_skill_ids 非空（45 条正样本）", "rule_claim_rate": 0.0444, "template_claim_rate": 0.0222, "hybrid_claim_rate": 0.9111, "both_rule_and_template": 0.0}}
  - 影响：当前漏斗的拒识能力完全来自 L1/L2 两层；语义层对任何中文输入都会返回候选

## 7. ECE 漂移触发条件

- PSI(分数分布)：触发 PSI >= 0.25（依据：信用评分业界通行经验阈值（<0.1 无显著漂移；0.1–0.25 中度；>=0.25 显著，须重估））
- Delta_ECE(标注窗口)：触发 Delta_ECE >= 0.10 或 ECE_cur >= 2 x ECE_calib（先到者触发）（依据：本基线 ECE 量级约 0.1，取半个量级为可感知漂移；倍数条件防小基数抖动）
- KS 检验 p 值：触发 p < 0.01（依据：与 PSI 互为交叉验证（PSI 分箱敏感，KS 无分箱））
- 最小样本量 200（依据：温度缩放是单参数拟合，样本 <200 时 T 的置信区间极宽；本基线校准集样本量（见 calibration.layers.*.n_calib）远小于该门槛 ⇒ 当前 T 只能当占位值。）

## 8. 诚实边界

- 规则层/模板层用例抄自这两个模块自己的单测（关键词/正则即规则定义）⇒ 这些准确率是同分布上界，不是对真实流量的泛化估计；仓库内不存在留出的路由标注集（已勘察 eval/**、data/evals/**、tests/**）。
- 语义层用例（10 条）抄自 tests/unit/test_tool_hybrid_lang_recall.py，是为英文别名机制定制的查询；但该单测用 5 工具合成索引，本基线用真实 90 工具索引（data/tool_index.json）⇒ 比原单测更难。
- Embedding 在本环境不可用（retriever_degraded=true）⇒ 语义层读数描述 BM25-only 降级路，不是 AGENT_HYBRID_ALPHA=0.5 的融合路。
- 标注样本量 <=80 ⇒ 温度参数 T 与 ECE 的置信区间很宽，当前数值只能当占位基线。
- 拒识阈值曲线的标注折（n=10）与真实语料折（n=78）口径不同：标注折可算 risk，语料折无标注只能算覆盖/拒识；两者不可混读。
- 真实语料折 n=78 而非 85：另 7 条查询在语义层无任何 BM25 候选（空召回），已被 4b 曲线排除（这本身也是覆盖率的一部分，见 external_corpora 的声明率）。
- logs/ 下 grep 真实路由决策记录命中 0 条 ⇒ 无法给出线上覆盖率，只能用仓内语料替代。
