# 技能双路检索改造设计文档

> 模块：`agent/skills_mgmt`
> 生成时间：2026-07-29
> 关联代码：[loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py)、[vector_adapter.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/vector_adapter.py)
> 关联评估：[RRF_FUSION_EVALUATION_REPORT.md](file:///c:/Users/Administrator/agent/docs/RRF_FUSION_EVALUATION_REPORT.md)（基于黄金集的指标对比，本文档聚焦架构设计与降级策略）

---

## 1. 概述

### 1.1 改造目标

在原有 TF-IDF（词频-逆文档频率）字面匹配基础上，引入向量语义检索，并通过 RRF（Reciprocal Rank Fusion，倒数排名融合）算法融合两路结果，解决两类问题：

- **语义召回缺口**：query「把对话压缩一下」字面无「总结」「记忆」，TF-IDF 无法命中 `memory_summary` 技能，但向量同义词映射可识别「压缩」→「总结/记忆」。
- **字面与语义冲突**：query「PDF文件解析压缩」中 TF-IDF 偏向 `pdf_parser`（字面命中多），向量偏向 `memory_summary`（同义词维度多），两路 top1 分歧时需公平融合。

### 1.2 设计原则（三义约束）

| 原则 | 约束 |
|---|---|
| **不易** | 不破坏旧版 `match()` 语义：默认 `use_vector=False` 走纯 TF-IDF；所有新增参数均有默认值，向后兼容 |
| **变易** | 降级链路多层兜底，任一路失败不阻塞整体；BM25/向量/重排均可按需启停 |
| **简易** | RRF 仅看排名不看原始分数量纲，天然兼容 TF-IDF（0~1）与 cosine（-1~1）异构分数 |

---

## 2. 检索路径架构

### 2.1 三条检索路径

```
match(intent, use_vector, use_bm25, fusion_mode, use_reranker)
   │
   ├─ fusion_mode="none" + use_vector=False        → 纯 TF-IDF 单路（旧版默认）
   ├─ fusion_mode="none" + use_vector=True         → 纯向量单路
   ├─ fusion_mode="rrf"   + use_vector=True        → TF-IDF + 向量 双路 RRF 融合
   ├─ use_bm25=True                                → 自动升级 fusion_mode="rrf"（三路融合）
   └─ use_reranker=True + fusion_mode="rrf"        → RRF 融合 + Cross-Encoder 精排
```

### 2.2 自动升级规则

`use_bm25=True` 时 `fusion_mode` 自动升级为 `"rrf"`（[loader.py:L1226-L1240](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1226-L1240)）。原因：BM25 单独无融合价值，必须与至少一路融合才生效。`rank_bm25` 未安装时静默降级为空列表，不抛异常。

---

## 3. RRF 融合原理

### 3.1 算法公式

**Reciprocal Rank Fusion**（Cormack et al. 2009）：

```
score(d) = Σ 1 / (k + rank_i(d))，k = 60
```

- `rank_i(d)`：文档 d 在第 i 路检索结果中的排名（从 1 开始）
- `k = 60`：业界标准平滑参数（类常量 `_RRF_K = 60`，[loader.py:L769](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L769)）
- k 越大对低位排名容错越强；k 越小越偏向头部排名

### 3.2 关键特性

| 特性 | 说明 |
|---|---|
| **无量纲依赖** | 仅看排名，天然融合 TF-IDF（0~1）与 cosine（-1~1）异构分数 |
| **两路命中加分** | 两路都命中的技能分数累加（如 `1/(60+1) + 1/(60+2) = 0.0325`），自然排名靠前 |
| **单路保留召回** | 单路命中的技能保留单次贡献（如 `1/(60+1) = 0.0164`），作为补充召回 |
| **归一化输出** | 融合后 score 归一化到 [0,1]，top1 恒为 1.0，便于下游阈值判断 |

### 3.3 加权融合扩展

`_rrf_fuse_weighted` 支持通过 `retrieval_weights={"tfidf":0.4,"vector":0.4,"bm25":0.2}` 调整各路权重，默认等权。加权公式：`score(d) = Σ w_i / (k + rank_i(d))`。

### 3.4 冲突场景验证（demo 实测）

query「PDF文件解析压缩」冲突融合结果：

| Rank | Skill ID | RRF Score | TF-IDF Rank | Vector Rank | 两路命中 |
|---|---|---|---|---|---|
| 1 | pdf_parser | 0.9919 | 1 | 2 | ✓ 是 |
| 2 | memory_summary | 0.9919 | 2 | 1 | ✓ 是 |
| 3 | context_manager | 0.4841 | 3 | — | ✗ 单路 |

两路都命中的技能以 0.9919 并列 top1（分数累加），单路命中的技能降至 0.48 以下，符合 RRF「两路共识优先」的设计预期。

---

## 4. 降级策略（核心）

### 4.1 降级决策树

```
_try_rrf_match(intent, use_vector, use_bm25)
   │
   ├─ TF-IDF 路（恒执行）
   │
   ├─ 向量路
   │   ├─ 适配器可用 + 检索成功 → vector_matches 正常
   │   ├─ 适配器可用 + 检索异常 → vector_matches=[] (rrf.vector_path.exception)
   │   └─ 适配器不可用
   │       ├─ 无 BM25 兜底  → return None (rrf.vector_adapter_unavailable) → 外层 TF-IDF 兜底
   │       └─ 有 BM25 兜底  → vector_matches=[] (rrf.vector_unavailable_bm25_fallback) → 走 tfidf+bm25
   │
   ├─ BM25 路（use_bm25=True 时启用）
   │   └─ 异常 → bm25_matches=[] (rrf.bm25_path.exception)，不阻塞融合
   │
   ├─ 三路均空？ → return None → 外层 TF-IDF 兜底（守负样本不误召回）
   │
   ├─ 单路兜底阈值检查
   │   └─ TF-IDF 空 + 向量单路 + top1 < 0.45 → return None (rrf.single_path_low_score_rejected)
   │
   └─ RRF 融合
       └─ 融合结果空 → return None → 外层 TF-IDF 兜底
```

### 4.2 降级分支详解

| # | 触发条件 | 降级动作 | 日志 action | 代码位置 |
|---|---|---|---|---|
| 1 | 向量路检索抛异常 | `vector_matches=[]`，继续融合 | `rrf.vector_path.exception` | [L1098-L1106](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1098-L1106) |
| 2 | 向量适配器不可用 + 无 BM25 | `return None`，外层降级纯 TF-IDF | `rrf.vector_adapter_unavailable` | [L1107-L1117](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1107-L1117) |
| 3 | 向量适配器不可用 + 有 BM25 | 向量路置空，走 tfidf+bm25 两路融合 | `rrf.vector_unavailable_bm25_fallback` | [L1118-L1125](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1118-L1125) |
| 4 | BM25 路检索抛异常 | `bm25_matches=[]`，继续融合 | `rrf.bm25_path.exception` | [L1139-L1147](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1139-L1147) |
| 5 | 三路均空 | `return None`，外层 TF-IDF 兜底 | （无单独日志，外层 `match.layer1.ok` 记录） | [L1149-L1152](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1149-L1152) |
| 6 | TF-IDF 空 + 向量单路 + top1<0.45 | `return None`，拒绝向量误召回 | `rrf.single_path_low_score_rejected` | [L1154-L1181](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1154-L1181) |
| 7 | RRF 融合结果为空 | `return None`，外层 TF-IDF 兜底 | （外层记录 `fallback_used=true`） | [L313-L324](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L313-L324) |

### 4.3 单路兜底阈值设计

阈值 `SINGLE_PATH_MIN_TOP1 = 0.45`（[L1165](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L1165)）用于防御 embedding 模型对中文负样本的误召回。数据支撑（BGE-m3）：

- case_038「今天天气真好」向量 top1=0.3612 → 误召回，应拒绝 ✓
- case_042「帮我订一张机票」向量 top1=0.4414 → 误召回，应拒绝 ✓
- case_043「请帮我反思」向量 top1=0.6030 → 真匹配，应保留 ✓
- case_007「帮我梳理历史记忆并压缩」向量 top1=0.6346 → 真匹配，应保留 ✓

**例外**：`use_bm25=True` 且 BM25 有结果时跳过此阈值——BM25 提供独立专有名词信号，不属于「向量单路误召回」场景。

### 4.4 降级设计原则

- **守【不易】**：所有降级最终回到 TF-IDF 单路（旧版语义），永不抛异常中断检索
- **守【不易】**：负样本 query（三路均空）返回 None 而非随机召回
- **守【变易】**：任一路失败不阻塞融合，缺失路按空列表参与计算

---

## 5. 可观测性日志

### 5.1 关键日志节点

| 阶段 | action | 模块 | 关键字段 |
|---|---|---|---|
| 向量路计算 | `st_backend.sims_computed` | vector_adapter | `query`, `doc_count`, `top1_skill_id`, `top1_similarity` |
| 融合前 | `rrf.paths_before_fuse` | loader | `tfidf_top1`, `vector_top1`, `bm25_top1`, `top1_conflict`, `tfidf_top3`, `vector_top3`, `*_candidate_count`, `rrf_k` |
| 融合后 | `rrf.fused_detail` | loader | `fused_count`, `top5_detail[]`（含 `tfidf_rank`/`vector_rank`/`bm25_rank`/`rrf_score`/`both_paths`） |
| 最终结果 | `match.layer1.rrf.ok` | loader | `duration_ms`, `tfidf_candidates`, `vector_candidates`, `fused_count`, `match_count`, `final_top_skill_ids[]`, `fallback_used` |
| 降级事件 | `rrf.*`（见上表） | loader | `intent`, 触发原因 |

### 5.2 排查要点

排序异常排查顺序：

1. 查 `rrf.paths_before_fuse` 确认两路原始 top1 与 `top1_conflict` 标志
2. 查 `rrf.fused_detail` 确认融合后 top5 的各路 rank 与 `both_paths` 标志
3. 查 `match.layer1.rrf.ok` 确认最终 `final_top_skill_ids`
4. 若向量路 top1 异常，查 `st_backend.sims_computed` 的 `top1_similarity`

---

## 6. 配置与扩展点

### 6.1 同义词配置（FakeModel / demo）

同义词映射从 `config/synonyms.json` 加载（路径由环境变量 `SYNONYMS_CONFIG_PATH` 指定），支持动态调整无需改代码（守 project_memory 约束：.env 管路径，JSON 管数据）。

```json
{
  "keywords": ["pdf", "反思", "总结", "记忆", "情绪", "安全", "建议", "上下文"],
  "synonyms": {
    "压缩": ["总结", "记忆"],
    "不高兴": ["情绪"],
    "读取": ["pdf"]
  }
}
```

### 6.2 检索参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `use_vector` | False | 启用向量检索 |
| `use_bm25` | False | 启用 BM25（自动升级 fusion_mode="rrf"） |
| `use_reranker` | False | 启用 Cross-Encoder 精排（需 use_vector=True + fusion_mode="rrf"） |
| `fusion_mode` | "none" | 融合模式：none / rrf |
| `retrieval_weights` | None | 各路权重，None 则等权 |
| `top_k` | 5 | 返回前 K 个匹配 |
| `min_score` | 0.01 | 最低匹配分阈值 |

### 6.3 向量后端切换

`SkillVectorAdapter` 支持三种后端（按优先级降级）：

1. `st_backend`：sentence-transformers（BGE-m3），生产推荐
2. native chroma：ChromaDB 原生（Windows 兼容性差，已弃用）
3. FakeModel：demo/测试用 bag-of-words 模拟

---

## 7. 验证结果

### 7.1 Demo 验证

脚本：[scripts/demo_vector_retrieval.py](file:///c:/Users/Administrator/agent/scripts/demo_vector_retrieval.py)

**Part 1 语义召回**（5 个语义相似 query）：

| 方法 | 命中率 |
|---|---|
| TF-IDF | 5/5 |
| 向量 | 5/5 |
| RRF 融合 | 5/5 |

**Part 2 冲突融合**（query「PDF文件解析压缩」）：

- ✓ 检测到冲突（TF-IDF top1=pdf_parser，向量 top1=memory_summary）
- ✓ RRF 融合后两路都命中的技能以 0.9919 并列 top1
- ✓ 冲突双方均保留在 RRF top5，未丢失召回

### 7.2 单元测试

测试类：`TestBM25AutoUpgradeRRF`（[test_skills_mgmt.py:L465-L591](file:///c:/Users/Administrator/agent/tests/unit/test_skills_mgmt.py#L465-L591)）

| 用例 | 验证点 | 结果 |
|---|---|---|
| `test_bm25_auto_upgrades_to_rrf` | use_bm25=True 时 fusion_mode 自动升级 rrf | ✓ |
| `test_bm25_without_vector_triggers_rrf` | 仅 use_bm25=True 也能触发 RRF | ✓ |
| `test_bm25_rrf_vector_unavailable_degrades` | 向量路不可用时降级逻辑 | ✓ |
| `test_bm25_rrf_empty_query_degrades_to_tfidf` | 三路均空时降级 TF-IDF | ✓ |

4 用例全部通过（5.16s）。

### 7.3 黄金集评估

详见 [RRF_FUSION_EVALUATION_REPORT.md](file:///c:/Users/Administrator/agent/docs/RRF_FUSION_EVALUATION_REPORT.md)。RRF 相对 TF-IDF 基线：Precision@3 +3.8%、Recall@3 +5.3%、MRR +2.8%，零退化。

---

## 8. 参考索引

| 主题 | 文档 |
|---|---|
| RRF 指标评估 | [RRF_FUSION_EVALUATION_REPORT.md](file:///c:/Users/Administrator/agent/docs/RRF_FUSION_EVALUATION_REPORT.md) |
| V6.5 Reranker 集成 | [RETRIEVAL_UPGRADE_V6_5_RERANKER_PLAN.md](file:///c:/Users/Administrator/agent/docs/RETRIEVAL_UPGRADE_V6_5_RERANKER_PLAN.md) |
| 技能管理可观测性 | [CHANGELOG_SKILLS_MGMT_OBSERVABILITY.md](file:///c:/Users/Administrator/agent/docs/CHANGELOG_SKILLS_MGMT_OBSERVABILITY.md) |
| 三层记忆架构（TLM） | [TLM_OVERVIEW.md](file:///c:/Users/Administrator/agent/docs/TLM_OVERVIEW.md) |
