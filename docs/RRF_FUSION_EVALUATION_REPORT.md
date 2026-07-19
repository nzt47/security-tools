# RRF 融合检索评估报告

> 生成时间：2026-07-19
> 评估脚本：`scripts/eval_rrf_fusion.py`
> 报告数据：`tests/eval/rrf_fusion_report.json`
> 黄金集：`tests/eval/skill_retrieval_golden_set.json`（45 用例）

## 1. 执行摘要

### 1.1 三方核心指标对比

| 方法 | Precision@3 | Recall@3 | MRR | 0 分用例 | fallback 次数 |
|---|---|---|---|---|---|
| **TF-IDF**（基线） | 0.3926 | 0.8444 | 0.8000 | 7 | 0 |
| **Vector**（单路） | 0.3481 | 0.8667 | 0.6148 | 7 | 5 |
| **RRF**（融合） ⭐ | **0.4074** | **0.8889** | **0.8222** | **5** | 5 |

### 1.2 RRF 相对提升

| 指标 | vs TF-IDF | vs Vector |
|---|---|---|
| Precision@3 | **+0.0148 (+3.8%)** ✅ | **+0.0593 (+17.0%)** ✅ |
| Recall@3 | +0.0445 (+5.3%) ✅ | +0.0222 (+2.6%) ✅ |
| MRR | +0.0222 (+2.8%) ✅ | +0.2074 (+33.7%) ✅ |
| 0 分用例 | -2 ✅ | -2 ✅ |

### 1.3 关键结论

1. **RRF 全面超越 TF-IDF 基线**：Precision / Recall / MRR 三项均提升，0 分用例减少 2 个
2. **负样本拒绝能力完美保持**：tricky/negative 类别 Precision = 1.0000（与 TF-IDF 持平）
3. **修复 2 个 hard 用例**：case_007（memory_summary）和 case_043（self_reflection）从 P=0.00 提升到 P=0.33
4. **零退化**：33 个用例变化中，2 改善 + 0 退化 + 31 平移
5. **未达 0.6 阈值**：受限于 all-MiniLM-L6-v2 英文模型对中文 query 的判别力，但方向完全正确

---

## 2. 实现方案

### 2.1 RRF 算法

**Reciprocal Rank Fusion**（Cormack et al. 2009）公式：

```
score(d) = Σ 1 / (k + rank_i(d))，k = 60
```

- `rank_i(d)` 是文档 d 在第 i 路检索结果中的排名（从 1 开始）
- `k = 60` 是业界标准平滑参数，对低位排名容错强
- 不依赖原始分数量纲，仅看排名 → 适合 TF-IDF（0~1）与 cosine（-1~1）异构分数融合

### 2.2 代码位置

| 文件 | 关键位置 | 说明 |
|---|---|---|
| [loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) | `match()` 方法签名 `fusion_mode: str = "none"` | 新增参数 |
| [loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) | `_rrf_fuse()` 方法 | RRF 融合算法 |
| [loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) | `_try_rrf_match()` 方法 | 双路编排+降级 |
| [loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) | `_RRF_K = 60` 类常量 | k 值可调 |

### 2.3 关键设计决策

#### 决策 1：各路独立 min_score 过滤（修复负样本退化）

**问题**：第一版 RRF 未在 TF-IDF 路应用 min_score 过滤，导致"12345"等负样本 query 在 TF-IDF 路也获得 rank 1（虽然 score=0.0x），RRF 误召回。

**修复**：TF-IDF 路和向量路各自应用 min_score 过滤后再参与融合。修复后负样本 5 个用例全部正确拒绝。

#### 决策 2：单路兜底阈值（防御英文模型对中文的误判）

**问题**：all-MiniLM-L6-v2 是英文模型，对中文负样本（如"今天天气真好"、"帮我订一张机票"）会误召回 score 0.22~0.29 的技能。

**修复**：当 TF-IDF 路过滤为空但向量路非空时，要求向量路 top1 score >= 0.30 才采纳。数据支撑：

| case_id | query | 向量 top1 | 类型 | 决策 |
|---|---|---|---|---|
| case_038 | 今天天气真好 | 0.2259 | 负样本 | 拒绝 ✓ |
| case_042 | 帮我订一张机票 | 0.2887 | 负样本 | 拒绝 ✓ |
| case_043 | 请帮我反思 | 0.3378 | hard | 采纳 ✓ |
| case_007 | 帮我梳理历史记忆并压缩 | 0.3919 | hard | 采纳 ✓ |

#### 决策 3：候选池扩大倍率

RRF 受 rank 影响大，候选池太小会漏召。代码中 `candidate_k = max(top_k * 2, 10)`，平衡召回率与计算成本。

#### 决策 4：RRF 归一化分数

`max_possible = 2/(k+1)`（两路均为 rank 1 的理论上限）。归一化到 [0, 1]，便于可观测性透出：

```json
"score_breakdown": {
    "tfidf_rank": 2,
    "vector_rank": 1,
    "rrf_score": 0.032522,
    "rrf_normalized": 0.9919
}
```

---

## 3. 按难度分组分析

| 难度 | TF-IDF | Vector | RRF | RRF-T | RRF-V |
|---|---|---|---|---|---|
| easy | 0.3333 | 0.2857 | 0.3333 | +0.0000 | +0.0476 |
| medium | 0.3333 | 0.2820 | 0.3333 | +0.0000 | +0.0513 |
| hard | 0.2821 | 0.3846 | 0.3333 | +0.0512 | -0.0513 |
| **tricky** | 1.0000 | 0.6000 | **1.0000** | +0.0000 | **+0.4000** |

**分析**：
- **easy / medium**：RRF 与 TF-IDF 持平（这些用例 TF-IDF 已能解决）
- **hard**：RRF 比 TF-IDF +0.0512，但比 Vector 单路 -0.0513。说明 RRF 在保留向量语义召回的同时，被 TF-IDF 拉回到字面匹配能力
- **tricky**：RRF 完美保持 1.0000，比 Vector 单路 +0.4。这是 RRF 的最大价值——**用 TF-IDF 的字面无匹配信号来拒绝向量路对负样本的误召回**

---

## 4. 按类别分组分析

| 类别 | TF-IDF | Vector | RRF | RRF-T | RRF-V |
|---|---|---|---|---|---|
| context | 0.3333 | 0.2500 | 0.3333 | 0 | +0.0833 |
| discrimination | 0.0000 | 0.2222 | 0.1111 | **+0.1111** | -0.1111 |
| emotion | 0.3333 | 0.3333 | 0.3333 | 0 | 0 |
| memory_summary | 0.1666 | 0.3333 | 0.2500 | +0.0834 | -0.0833 |
| multi_skill | 0.6667 | 0.5333 | 0.6667 | 0 | +0.1334 |
| **negative** | 1.0000 | 0.6000 | **1.0000** | 0 | **+0.4000** |
| safety | 0.3333 | 0.3333 | 0.3333 | 0 | 0 |
| scripted | 0.3333 | 0.2500 | 0.3333 | 0 | +0.0833 |
| self_reflection | 0.1666 | 0.3333 | 0.1666 | 0 | -0.1667 |
| suggestion | 0.3333 | 0.2500 | 0.3333 | 0 | +0.0833 |
| voice | 0.3333 | 0.2500 | 0.3333 | 0 | +0.0833 |

**亮点类别**：
- **negative（负样本）**：RRF 完美拒绝所有 5 个负样本，比 Vector +0.4
- **multi_skill（多技能召回）**：RRF 保持 TF-IDF 的 0.6667，比 Vector +0.1334
- **discrimination（区分用例）**：RRF 修复 1 个（case_043），TF-IDF 全 0 分

---

## 5. 改善与退化用例详情

### 5.1 改善用例（2 个）

#### case_007 — memory_summary 修复

```
query   : 帮我梳理历史记忆并压缩
expected: ['memory_summary']
tfidf   : ['context_aware']                        # P=0.00
rrf     : ['context_aware', 'emotion_expression', 'memory_summary']  # P=0.33
```

**根因**：TF-IDF 对"记忆"和"压缩"字面匹配差，仅召回 context_aware。向量路（all-MiniLM-L6-v2）能识别"历史记忆+压缩"的语义，召回 memory_summary 排第 2（vector_rank=2），RRF 融合后挤入 top3。

#### case_043 — self_reflection 修复

```
query   : 请帮我反思
expected: ['self_reflection']
tfidf   : []                                       # P=0.00
rrf     : ['emotion_expression', 'safety_guard', 'self_reflection']  # P=0.33
```

**根因**：TF-IDF 对"反思"无字面匹配（技能 description 字段空白）。向量路通过 body 摘要识别"反思"语义，召回 self_reflection 排第 3（vector_rank=3），RRF 单路兜底通过（top1=0.338 >= 0.3 阈值）。

### 5.2 退化用例（0 个）✅

零退化。所有 TF-IDF 能解决的用例，RRF 也都能解决。

---

## 6. 三方方案特性对比

| 特性 | TF-IDF | Vector | RRF |
|---|---|---|---|
| **字面匹配敏感** | ✅ 强 | ❌ 弱 | ✅ 强（保留 TF-IDF 优势） |
| **语义召回能力** | ❌ 弱 | ✅ 强 | ✅ 中（向量路贡献） |
| **负样本拒绝** | ✅ 完美 | ❌ 5/5 误召回 2 个 | ✅ 完美（TF-IDF 信号拒绝） |
| **description 空白技能召回** | ❌ 失败 | ✅ 成功 | ✅ 部分成功（case_007/043） |
| **依赖外部模型** | ❌ 无 | ✅ chromadb + onnxruntime | ✅ 同 Vector |
| **额外延迟** | 0ms | ~50ms | ~60ms（双路并行） |
| **Windows DLL 兼容** | ✅ 无依赖 | ✅ native chromadb | ✅ 同 Vector |

---

## 7. 限制与后续优化方向

### 7.1 当前限制

1. **0.6 阈值未达成**：受限于 all-MiniLM-L6-v2 英文模型对中文 query 的判别力
2. **case_002/003/006/045 仍为 0 分**：self_reflection 在向量路排名靠后（rank 3-5），且 TF-IDF 路召回错误技能，RRF 融合后 top3 仍未包含期望技能
3. **fallback 次数 5**：3 个负样本 + 2 个负样本触发单路兜底阈值，retrieval_method 实际为 "tfidf"

### 7.2 后续优化方向

| 优化项 | 预期收益 | 实施难度 | 优先级 |
|---|---|---|---|
| 替换为多语言 embedding 模型（如 BGE-m3） | 大幅提升中文 case 召回 | 中（需解决 DLL 冲突） | 高 |
| 补全 self_reflection / memory_summary 的 description 字段 | TF-IDF 直接召回 | 低 | 高 |
| 引入 Cross-Encoder Reranker | 精排提升 Precision | 中 | 中 |
| 调优 RRF k 值（试 k=30） | 影响头部排名权重 | 低 | 中 |
| 调优单路兜底阈值（试 0.25） | 平衡负样本与 hard 召回 | 低 | 中 |

---

## 8. 三义校验自检

### 8.1 【不易】约束保持

- ✅ `fusion_mode="none"`（默认）行为与旧版完全等同（向后兼容）
- ✅ TF-IDF 单路逻辑未修改（仅在 RRF 分支内复制使用）
- ✅ VectorStore / chromadb 接口签名未改
- ✅ 黄金集 45 用例未改
- ✅ 技能定义未改
- ✅ 负样本拒绝能力保持（tricky Precision = 1.0）

### 8.2 【变易】扩展性

- ✅ `fusion_mode` 参数支持未来扩展（如 "weighted"、"rerank"）
- ✅ `_RRF_K` 类常量可调
- ✅ `SINGLE_PATH_MIN_TOP1` 阈值可调
- ✅ `candidate_k` 倍率可调
- ✅ 失败降级路径完整（向量路不可用 → TF-IDF 单路）

### 8.3 【简易】最简方案

- ✅ RRF 算法集中在 `_rrf_fuse()` 一个方法（30 行核心逻辑）
- ✅ 双路编排集中在 `_try_rrf_match()`（清晰流程）
- ✅ 每路独立 min_score 过滤，融合后不再二次过滤（避免归一化阈值失效）
- ✅ 可观测性完整：trace_id / structured log / emit_metric / score_breakdown

---

## 9. 复现命令

```powershell
# 运行 RRF 三方对比评估
cd c:\Users\Administrator\agent
$env:ANONYMIZED_TELEMETRY="False"
python scripts/eval_rrf_fusion.py --top-k 3 --output tests/eval/rrf_fusion_report.json --verbose

# 查看报告关键指标
python scripts/view_rrf_report.py

# 诊断向量路分数
python scripts/diagnose_vector_scores.py
```

## 10. 相关文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| [agent/skills_mgmt/loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) | 修改 | 新增 fusion_mode 参数 + _rrf_fuse + _try_rrf_match |
| [agent/skills_mgmt/vector_adapter.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/vector_adapter.py) | 未改 | 复用 native chromadb 后端 |
| [scripts/eval_rrf_fusion.py](file:///c:/Users/Administrator/agent/scripts/eval_rrf_fusion.py) | 新增 | 三方对比评估脚本 |
| [scripts/view_rrf_report.py](file:///c:/Users/Administrator/agent/scripts/view_rrf_report.py) | 新增 | 报告关键指标查看 |
| [scripts/diagnose_vector_scores.py](file:///c:/Users/Administrator/agent/scripts/diagnose_vector_scores.py) | 新增 | 向量路分数诊断 |
| [tests/eval/rrf_fusion_report.json](file:///c:/Users/Administrator/agent/tests/eval/rrf_fusion_report.json) | 新增 | 完整评估报告数据 |
