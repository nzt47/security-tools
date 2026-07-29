# Release Note: v6.5 Cross-Encoder Reranker 精排集成（sigmoid + reranked 契约修复 + 可观测性增强）

**日期**: 2026-07-30
**影响模块**: `agent/skills_mgmt/reranker.py`, `agent/skills_mgmt/loader.py`, `agent/skills_mgmt/observability.py`, `scripts/eval_reranker_precision_compare.py`
**提交**: `298add72`
**严重级别**: P1（reranker 集成 + 数值正确性修复 + 可观测性缺失）
**验收**: Precision@3 从 0.11 恢复到 0.42（sigmoid 修复）；≥20% 提升目标未达标（ONNX 模型区分度不足）

---

## 变更摘要

| 类型 | 数量 | 描述 |
|------|------|------|
| 新功能 | 1 | Cross-Encoder Reranker 集成到 RRF 融合链路（ONNX 优先 + PyTorch 降级） |
| Bug 修复 | 3 | sigmoid 转换、reranked=True 漏传、observability logger 无 handler |
| 可观测性增强 | 2 | rerank.completed 分数范围日志、评估脚本 basicConfig |
| 测试 | 4 | 102 个 reranker 单元测试 + 61 个 loader 测试全部通过 |
| 变更行数 | +4905 / -148 | 9 files changed |

---

## 1. 根因分析

### 1.1 问题现象

评估发现 reranker 集成后 Precision@3 从 0.42 暴跌到 0.11，且评估报告中所有 45 个 case `reranked: false`，无法判断 reranker 是否实际生效。

### 1.2 根因链路

```
Cross-Encoder 输出 raw logits（典型 -10~+10）
  → min_score 阈值（0.001）是概率空间阈值 [0,1]
    → 负 logits 的合理匹配被 min_score 误过滤
      → rerank 后 top 为空或仅剩极少候选
        → Precision@3 暴跌到 0.11
```

```
loader.py:1911 构造 MatchResult 时漏传 reranked=True
  → 评估脚本 getattr(result, "reranked", False) 始终返回 False
    → 评估报告所有 case reranked: false
      → 误判 reranker 未被调用
        → 实际 reranker 已被调用（17.7x 耗时差证明）
```

```
observability.py logger 无 handler（库模块未配 NullHandler）
  → 评估脚本不经过 app_server.py 入口，root logger 无配置
    → INFO 日志走 lastResort（WARNING+）丢失
      → reranker.init / rrf.rerank.applied / rerank.completed 全部不可见
        → 无法从日志诊断 reranker 调用链与分数分布
```

---

## 2. 修复方案

### 2.1 sigmoid 数值正确性修复（reranker.py:84-95, 526）

新增数值稳定的 sigmoid 函数，将 Cross-Encoder raw logits 映射到 [0,1] 概率空间：

```python
def _sigmoid(x: float) -> float:
    """数值稳定的 sigmoid 函数（分段实现避免 math.exp 溢出）"""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)

# 在 rerank() 中应用
scores = [_sigmoid(s) for s in scores]
```

**特性**：
- 单调递增，不改变排序
- 让 rerank_score 落入 [0,1]，恢复 min_score 阈值"过滤极低概率匹配"语义
- 与 sentence-transformers `CrossEncoder.predict(apply_softmax=True)` 行为一致
- 分段实现避免 `math.exp` 溢出（|x| > 700）

### 2.2 loader.py:1911 reranked=True 漏传修复

```python
result = MatchResult(
    matches=top,
    ...
    retrieval_method=retrieval_method,
    reranked=(retrieval_method == "rrf_rerank"),  # 修复：与 retrieval_method 同步
    fallback_used=False,
)
```

**验证**：修复后 41/45 experiment case `reranked=true`（4 个 false 是 tricky 负样本，reranker 未被调用）。

### 2.3 rerank.completed 日志增强（reranker.py:566-592）

```python
logger.info(json.dumps({
    ...
    "top_score": float(result[0][2]) if result else 0.0,
    "score_min": round(score_min, 6),      # 新增：sigmoid 分数范围
    "score_max": round(score_max, 6),      # 新增：验证 [0,1]
    "score_mean": round(score_mean, 6),     # 新增：平均分
    "score_stddev": round(score_stddev, 6), # 新增：区分度诊断
    "duration_ms": round(elapsed, 2),
}, ensure_ascii=False))
```

**诊断价值**：`score_stddev` 直接暴露 reranker 区分度。本次评估发现 `score_stddev=0.0`，定位到 ONNX 模型对所有候选给出相同 logits 的根因。

### 2.4 observability.py NullHandler（PEP 282 库模块最佳实践）

```python
logger = logging.getLogger("agent.skills_mgmt")
logger.addHandler(logging.NullHandler())  # 阻止 "No handlers found" 警告
# propagate=True（默认）：日志传播到 root logger，由调用方配置 handler
```

**设计权衡**：
- 不在 observability.py 配置 StreamHandler（避免与 app_server.py 的 `setup_readable_logging` 重复输出）
- 通过 `propagate=True` 让日志传播到 root logger，由调用方统一配置
- 评估脚本通过 `basicConfig` 配置 root logger，让 INFO 日志可见

### 2.5 评估脚本 basicConfig（eval_reranker_precision_compare.py）

```python
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",  # observability 日志已是 JSON
    stream=sys.stderr,
    force=True,
)
```

---

## 3. 验证结果

### 3.1 单元测试

```
tests/unit/test_skill_reranker.py    ✅
tests/unit/test_reranker.py          ✅
tests/unit/test_reranker_onnx.py     ✅
tests/unit/test_reranker_regression.py ✅
================================== 102 passed in 2.84s ===================================

tests/unit/test_skills_mgmt.py       ✅
======================== 61 passed, 1 xfailed in 2.77s ========================
```

### 3.2 评估验证

| 指标 | sigmoid 修复前 | sigmoid 修复后 |
|------|----------------|----------------|
| baseline Precision@3 | 0.4222 | 0.4222 |
| experiment Precision@3 | **0.1111** | **0.4222** |
| reranked=true 比例 | 0/45 | 41/45 |
| rerank.completed 日志 | 不可见 | 可见（含 score_min/max/mean/stddev） |
| sigmoid 分数范围 | N/A | [0, 1] ✅ |
| score_stddev | N/A | 0.0（模型无区分度） |

### 3.3 关键日志证据

```
{"action": "rerank.completed", "candidate_count": 5, "result_count": 5,
 "top_score": 0.2427, "score_min": 0.242719, "score_max": 0.242719,
 "score_mean": 0.242719, "score_stddev": 0.0, "duration_ms": 34.94}
{"action": "rrf.rerank.applied", "pool_size": 5, "final_count": 3}
{"action": "match.layer1.rrf.ok", "retrieval_method": "rrf_rerank", ...}
```

---

## 4. 已知限制

### 4.1 Precision@3 提升 ≥20% 目标未达标

**现状**：experiment Precision@3 = baseline = 0.4222，相对提升 0.0%。

**根因**：jina-reranker-v2 量化 ONNX 模型对当前 8 技能黄金集的 5 个候选给出**完全相同**的 sigmoid 分数（`score_stddev=0.0`），reranker 无法区分候选优劣，排序不变。

**后续调研方向**：
1. 换用 BAAI/bge-reranker-v2-m3（~2.3GB，中文 SOTA）
2. 扩大黄金集（当前仅 8 技能，候选池过小）
3. 检查 ONNX 量化是否损失区分度（对比 PyTorch float32 原始模型）
4. 调整候选池构造（当前 2*top_k=6，可能需要扩大）

### 4.2 vector 后端不可用

评估环境 vector 后端不可用（`rrf.vector_unavailable_bm25_fallback`），RRF 走 TF-IDF+BM25 两路融合。reranker 在两路融合后二次排序，但候选池质量受限于字面匹配。

---

## 5. 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `agent/skills_mgmt/reranker.py` | 修改 | sigmoid 函数 + 日志增强 |
| `agent/skills_mgmt/loader.py` | 修改 | reranker 集成 + reranked=True 修复 |
| `agent/skills_mgmt/observability.py` | 修改 | NullHandler |
| `scripts/eval_reranker_precision_compare.py` | 新增 | 评估脚本 + basicConfig |
| `docs/RERANKER_PRECISION_EVAL_REPORT.json` | 新增 | 评估报告 |
| `tests/unit/test_skill_reranker.py` | 新增 | 4 个核心测试 |
| `tests/unit/test_reranker.py` | 修改 | sigmoid 后断言更新 |
| `tests/unit/test_reranker_onnx.py` | 新增 | ONNX 集成测试 |
| `tests/unit/test_reranker_regression.py` | 新增 | 回归测试 |

---

## 6. 回滚方案

如需回滚 sigmoid 修复（不推荐，会导致 Precision@3 暴跌到 0.11）：

```bash
git revert 298add72
```

或单独回滚 reranker.py 的 sigmoid 转换（保留其他修复）：

```python
# 删除 reranker.py:526
# scores = [_sigmoid(s) for s in scores]  # 注释此行
```
