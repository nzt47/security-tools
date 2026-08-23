# Reranker 黄金集扩展验证报告（2026-08-24）

## 背景

原黄金集 45 个 case 中 Precision@3 提升仅 5.26%（bge-reranker-base），未达 20% 验收阈值。本次扩展黄金集至 65 个 case 后重新验证。

## 黄金集扩展

**扩展规模**: 45 → 65 个 case（新增 20 个）

| 维度 | 原（45） | 扩展后（65） |
|------|---------|-------------|
| 总 case | 45 | 65 |
| hard | 13 | 28 |
| tricky | 5 | 10 |
| multi_skill | 5 | 9 |
| negative | 5 | 10 |
| discrimination | 3 | 7 |

**新增类别重点**：
- discrimination 4 个（语义近义查询，考验 reranker 区分度）
- multi_skill 4 个（多技能组合）
- negative 5 个（无关查询，考验拒绝能力）
- hard 7 个（单技能语义变体）

扩展脚本: `scripts/expand_golden_set.py`（守【不易】不修改原 case，仅追加 case_046~065）

## 评估结果

### 配置 A: bge-reranker-base + ONNX + min_score=0.001（默认）

| 指标 | 基线 | 实验组 | 差异 |
|------|------|--------|------|
| Precision@3 | 0.4564 | 0.4410 | **-3.4%** ❌ |
| Recall@3 | 0.9308 | 0.9000 | -0.0308 |
| MRR | 0.9308 | 0.9000 | -0.0308 |

**根因**: 14 个 case 实验组空结果——7 个 negative 正确拒绝（加分），但 **7 个 positive case 被 min_score=0.001 误过滤**（如 case_022/036/061/062），Precision 归零。

### 配置 B: bge-reranker-base + ONNX + min_score=0.0001（阈值放宽）

| 指标 | 基线 | 实验组 | 差异 |
|------|------|--------|------|
| Precision@3 | 0.4564 | 0.4564 | **+0.0%** ❌ |
| Recall@3 | 0.9308 | 0.9308 | +0.0 |
| MRR | 0.9308 | 0.9308 | +0.0 |

**结论**: 误过滤恢复后，reranker 提升为 0——**bge-reranker-base 的 rerank 排序与 RRF 融合排序在 top-3 完全一致，无区分度增益**。

### 配置 C: bge-reranker-v2-m3（PyTorch）

**无法加载**: 本地 HF 缓存不完整（`.no_exist` 目录 + LFS pointer 未拉取权重），`is_available=False`。

## 结论与建议

1. **Precision@3 未达标**: 扩展黄金集后，bge-reranker-base 提升 0%（-3.4% 若默认 min_score），远低于 20% 验收阈值。
2. **根因**: 扩展后 hard/tricky 占比从 40% 升至 58%，bge-reranker-base 在这些难样本上与 RRF 排序无差异——区分度不足是模型固有局限（与 7-30 TODO 记录一致）。
3. **min_score 风险**: 默认 0.001 在扩展黄金集上误过滤 7 个 positive case（-3.4%），阈值放宽至 0.0001 可恢复但无增益。建议将默认阈值下调或对"rerank 后空结果"增加保底逻辑（返回 RRF 原序而非空）。
4. **下一步候选**:
   - 完整下载 v2-m3 权重后验证（区分度更高，但 CPU 延迟 4.6s 超 SLO，需 ONNX 量化）
   - 扩大候选池（rerank_pool_size 从 max(2*top_k,10) 提升）增加 reranker 可排序空间
   - 接受 reranker 在黄金集提升有限的现状，聚焦其他检索优化（如 BM25 参数/负样本质量）

## 文件变更

- `tests/eval/skill_retrieval_golden_set.json`: 45 → 65 case（version 2.0）
- `scripts/expand_golden_set.py`: 新增扩展脚本
- `docs/RERANKER_PRECISION_EVAL_REPORT.json`: 更新为 65-case 评估结果（min_score=0.0001）

---

## 下一步行动建议

### 建议 1（推荐，低风险高价值）：修复 min_score 误过滤

**问题**：默认 `SKILL_RERANKER_MIN_SCORE=0.001` 在扩展黄金集上误过滤 7 个 positive case，Precision@3 由 +0.0% 恶化至 -3.4%。

**行动**：
- 将默认 `_DEFAULT_MIN_SCORE` 从 0.001 下调至 0.0001（`agent/skills_mgmt/reranker.py:134`）
- 或在 loader 层增加保底逻辑：rerank 后结果为空且原 RRF 候选非空时，回退返回 RRF 原序而非空结果（`agent/skills_mgmt/loader.py:1922-1924` 的 `filtered_empty` 分支）

**验收**：重跑扩展评估，确保 positive case 不再被误拒绝（实验组 Precision ≥ 基线）。

### 建议 2：验证 bge-reranker-v2-m3（需完整下载权重）

**现状**：v2-m3 本地 HF 缓存不完整（`.no_exist` 目录 + LFS pointer 未拉取权重），无法加载。

**行动**：
- 用 `scripts/download_bge_reranker_v2_m3_modelscope.py` 完整下载（约 2.3GB）
- 下载完成后重跑 `eval_reranker_precision_compare.py`（min_score=0.0001）
- 若 CPU 延迟超 SLO，再评估 ONNX 量化（`scripts/convert_bge_to_onnx.py` 对 v2-m3 的适配）

**验收**：Precision@3 相对提升 ≥ 20%，或记录 v2-m3 在 CPU 上不可行并关闭该方向。

### 建议 3：扩大 reranker 候选池

**现状**：loader 的 `rerank_pool_size = max(top_k * 2, 10)`（loader.py:1836），reranker 仅对 6-10 个候选排序，区分空间有限。

**行动**：将 `rerank_pool_size` 提升至 `max(top_k * 3, 15)`，重跑评估。

**验收**：观察 Precision@3 是否提升；权衡推理耗时增加（候选池增大 → predict 批次变大）。

### 建议 4：短期接受现状，聚焦检索全链路其他优化

**理由**：reranker 在黄金集提升有限的根因是 8 技能候选池小 + 模型区分度局限。若 1-3 均无法突破 20% 验收线：

**行动**：
- 保留 reranker 作为可选精排层（默认关闭或低优先级）
- 将预算转向：BM25 参数调优（b 值/d 值）、负样本质量提升（`data/tool_negative_samples_expanded.json`）、RRF k 值调优
- 扩大黄金集至 100+ case（覆盖更多查询范式）后重测基线，建立更稳健的检索基线

### 决策建议

| 行动 | 优先级 | 成本 | 预期收益 |
|------|--------|------|---------|
| 1. min_score 误过滤修复 | P0 | 低（2 行改动） | 消除 -3.4% 回归，恢复 0% 基线 |
| 2. v2-m3 完整验证 | P1 | 中（2.3GB 下载 + 评估） | 可能达 20% 验收线（待验证） |
| 3. 扩大候选池 | P2 | 低（1 行改动） | 未知，需实测 |
| 4. 聚焦检索全链路 | P2 | 中 | 稳健基线提升 |

**结论**：建议先执行行动 1（P0 修复回归），同时并行启动行动 2（v2-m3 下载验证）；若行动 2 无法达标，按行动 4 调整投入方向。
