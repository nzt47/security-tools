# v6.5 Reranker 实现计划

**版本**: v6.5-draft
**创建时间**: 2026-07-27
**前置条件**: v6.4 向量引擎恢复已完成（commit 9915e13e）
**目标**: P@3 从 0.3750 提升到 ≥ 0.4444

---

## 1. 背景与动机

### 1.1 v6.4 现状

| 指标 | v6.4 实测 | 目标 | 状态 |
|------|----------|------|------|
| v6.2 层命中 | 12 | ≥ 8 | ✅ 已达标 |
| 负样本延迟 | 97ms | ≤ 200ms | ✅ 已达标 |
| 拒绝率 | 100% | 100% | ✅ 已达标 |
| **P@3** | **0.3750** | **0.4444** | ❌ 未达标 |

### 1.2 P@3 未达标根因

[loader.py:360](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py#L360) 的 `use_reranker` 仍未实现：

```python
# use_bm25 / use_reranker 仍未实现，记录 warning
if use_bm25 or use_reranker:
    logger.warning({
        "action": "match.extension_not_implemented",
        "fallback": "tfidf",
    })
    fallback_used = True
```

当前检索流程：TF-IDF 单路 → RRF 融合（退化）→ 无 Reranker 二次排序

### 1.3 P@3=0.3750 的含义

- 40 个正样本中，35 个 P@3=0.3333（1/3 命中），5 个 P@3=0.6667（2/3 命中）
- 平均 0.3750 = (35×0.3333 + 5×0.6667) / 40
- **目标 0.4444** 意味着平均命中 1.33/3，需要 Reranker 将正确技能提升到 top-3

---

## 2. 目标（【不易】约束）

### 2.1 硬约束

| 约束 | 当前值 | 目标值 | 说明 |
|------|--------|--------|------|
| P@3 | 0.3750 | **≥ 0.4444** | 核心不变量 |
| v6.2 层命中 | 12 | ≥ 8 | 不下降 |
| 拒绝率 | 100% | 100% | 不下降 |
| 10 个正样本误拒 | 0 | 0 | 不误伤 |
| 负样本延迟 | 97ms | ≤ 200ms | 不退化 |

### 2.2 软目标

| 目标 | 期望值 | 说明 |
|------|--------|------|
| 正样本延迟 | ≤ 1000ms | Reranker 增加 ~200-500ms |
| Reranker 命中率 | ≥ 60% | top-3 含 expected 的比例 |

---

## 3. 技术方案

### 3.1 Reranker 模型选型

| 模型 | 大小 | 延迟 | 中文支持 | 推荐度 |
|------|------|------|---------|--------|
| BAAI/bge-reranker-v2-m3 | ~2.3GB | ~200ms | ✅ 优秀 | ⭐⭐⭐ 推荐 |
| BAAI/bge-reranker-base | ~1.1GB | ~100ms | ✅ 良好 | ⭐⭐ 备选 |
| jinaai/jina-reranker-v2-base-multilingual | ~280MB | ~80ms | ✅ 良好 | ⭐ 轻量备选 |

**推荐**: `BAAI/bge-reranker-v2-m3`（与 BGE-m3 embedding 同系列，中文优秀）

### 3.2 架构设计

```
用户 query
    │
    ├─ v6.1 规则层（<1ms）───────────────── 命中 → 拒绝
    │
    ├─ v6.2 embedding 拒绝层（~30-80ms）── 命中 → 拒绝
    │
    ├─ TF-IDF 检索（~10ms）─────────────── 候选 top-20
    │
    ├─ 向量检索（~50ms）────────────────── 候选 top-20
    │
    ├─ RRF 融合（~5ms）─────────────────── 融合 top-20
    │
    └─ 【v6.5 新增】Reranker 二次排序（~200ms）→ top-3 最终结果
```

### 3.3 Reranker 接口设计

```python
class SkillReranker:
    """v6.5 Cross-Encoder Reranker — 对 RRF 融合候选二次排序

    【不易】不改变 match() 公共接口签名
    【变易】模型可通过环境变量配置
    【简易】单次 predict，O(n) 复杂度
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self._model = None  # 懒加载
        self._model_name = model_name

    def rerank(
        self,
        query: str,
        candidates: List[SkillMatch],
        top_k: int = 3,
    ) -> List[SkillMatch]:
        """对候选技能重新排序

        Args:
            query: 用户意图
            candidates: RRF 融合后的候选列表
            top_k: 返回 top-k

        Returns:
            重排序后的 top-k 候选
        """
        ...
```

### 3.4 loader.py 集成点

在 [loader.py](file:///c:/Users/Administrator/agent/agent/skills_mgmt/loader.py) 的 `match()` 方法中，替换 `use_reranker` 的 warning 为实际调用：

```python
# v6.5: Reranker 二次排序
if use_reranker and candidates:
    reranker = self._get_reranker()  # 懒加载
    if reranker is not None:
        candidates = reranker.rerank(intent, candidates, top_k=top_k)
        reranked = True
    else:
        # 降级：Reranker 不可用，使用 RRF 排序
        fallback_used = True
```

---

## 4. 实施步骤

### Step 1: 创建 SkillReranker 类（TDD）
- 文件: `agent/skills_mgmt/reranker.py`
- 测试: `tests/unit/test_reranker.py`
- 内容: 懒加载 + rerank 接口 + 环境变量开关 + 降级处理

### Step 2: 集成到 loader.py
- 修改: `agent/skills_mgmt/loader.py` 的 `match()` 方法
- 替换: `use_reranker` warning → 实际 Reranker 调用
- 守护: `SKILL_RERANKER_ENABLED` 环境变量开关

### Step 3: 阈值校准
- 脚本: `scripts/calibrate_v65_reranker.py`
- 目标: 找到最优 rerank 阈值（min_score）
- 约束: P@3 ≥ 0.4444 + 10 正样本 0 误拒

### Step 4: 端到端验证
- 脚本: `scripts/verify_v65_reranker.py`
- 验证: P@3 ≥ 0.4444 + 拒绝率 100% + 延迟 ≤ 1000ms

### Step 5: 文档与监控
- 报告: `docs/RETRIEVAL_UPGRADE_V6_5_REPORT.md`
- 运维: 更新 `V6_OPS_RUNBOOK.md`
- 告警: 更新 Prometheus 规则

---

## 5. 风险与回滚

### 5.1 风险评估

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| Reranker 模型加载失败 | 中 | 正样本延迟增加 | 降级到 RRF 排序 |
| P@3 仍不达标 | 中 | v6.5 目标失败 | 回退到 v6.4（P@3=0.3750） |
| 正样本延迟超 1000ms | 低 | 用户体验下降 | 限制候选数 top-10 |
| Windows 崩溃（0xC0000005）| 中 | 主进程崩溃 | 子进程隔离（multiprocessing） |

### 5.2 回滚方案

```bash
# 一键禁用 Reranker（环境变量，立即生效）
export SKILL_RERANKER_ENABLED=false

# 或 git revert
git revert <v6.5-commit>
```

### 5.3 Windows 崩溃防护

根据 project_memory 记录：
> Embedding 检索在 Windows CPU 环境下无隔离时会导致主进程 0xC0000005 崩溃

Reranker 同样需要子进程隔离：
```python
# 使用 multiprocessing.Process + terminate() 实现可靠超时
# 参考 negative_intent_detector 的子进程隔离模式
```

---

## 6. 预期收益

| 指标 | v6.4 | v6.5 预期 | 提升 |
|------|------|----------|------|
| P@3 | 0.3750 | ≥ 0.4444 | +18.5% |
| 正样本延迟 | ~500ms | ~700ms | +200ms（可接受） |
| 拒绝率 | 100% | 100% | 持平 |
| v6.2 层命中 | 12 | 12 | 持平 |

---

## 7. 验收标准

- [ ] P@3 ≥ 0.4444（硬约束）
- [ ] 10 个正样本 0 误拒（硬约束）
- [ ] 拒绝率 = 100%（硬约束）
- [ ] 正样本延迟 ≤ 1000ms（软目标）
- [ ] Reranker 降级正常（模型不可用时回退 RRF）
- [ ] Windows 无崩溃（子进程隔离）
- [ ] 单元测试通过
- [ ] 端到端验证通过
