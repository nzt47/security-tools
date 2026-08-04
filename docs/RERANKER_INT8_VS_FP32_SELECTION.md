# Reranker INT8 量化 vs FP32 选型对比报告

> 生成时间：2026-07-31
> 数据来源：[RERANKER_DISCRIMINATION_COMPARE_REPORT.json](./RERANKER_DISCRIMINATION_COMPARE_REPORT.json) + [v65_bge_base_benchmark.json](./v65_bge_base_benchmark.json)
> 关联代码：[reranker.py](../agent/skills_mgmt/reranker.py)

## 一、结论速览

| 维度 | INT8 量化（首选 ✅） | FP32（备选） |
|------|---------------------|--------------|
| ONNX 文件 | `model_quantized.onnx` | `model.onnx` |
| 模型大小 | 266 MB | 1061 MB |
| 内存占用 | 1167 MB | 2188 MB |
| P99 延迟 | **487 ms ✅ 达标 500ms SLO** | 1080 ms ❌ 未达标 |
| 区分度（avg_score_stddev） | 0.2443 | 0.2443（一致）|
| Precision@3 | 0.3902 | 0.3902（一致）|
| 负样本拒识（case_042） | ✅ 已正确过滤 | ✅ 已正确过滤 |

**核心结论**：INT8 量化在区分度、精度、负样本拒识上与 FP32 完全一致，但延迟快 2.22 倍、内存省 47%。**INT8 是首选方案，FP32 仅在极端拒识严苛场景作为可选高精度模式**。

---

## 二、为什么 INT8 是首选

### 2.1 区分度无损失（关键发现）

INT8 与 FP32 的 `avg_score_stddev` 完全一致（0.2443），相对 jina 提升 **+121.0%**。这印证了 ONNX 动态量化的特性：

- **权重 INT8 量化**（`weight_type=QInt8`）：仅压缩模型权重
- **激活保持 FP32**：推理时激活值仍为 FP32，精度损失极小
- 对 Cross-Encoder 这种"重权重轻激活"的模型，区分度损失可忽略

详见 [convert_bge_to_onnx.py](../scripts/convert_bge_to_onnx.py) 的量化策略。

### 2.2 延迟优势显著

| 指标 | INT8 | FP32 | 加速比 |
|------|------|------|--------|
| 单次 Min | 258 ms | 562 ms | 2.18x |
| 单次 P50 | 412 ms | 644 ms | 1.56x |
| **单次 P99** | **487 ms** | **1080 ms** | **2.22x** |
| 并发 P99（4 线程）| 1.21 s | 2.78 s | 2.30x |
| QPS | 3.86 | 1.45 | 2.66x |

INT8 满足 500ms P99 SLO，FP32 不满足。在 HPA 自动扩缩容场景下，FP32 的低 QPS 会导致 Pod 扩容压力（参考 [HPA_COMPARISON_LOADTEST_PLAN.md](./HPA_COMPARISON_LOADTEST_PLAN.md)）。

### 2.3 内存优势

INT8 模型 1167 MB，FP32 模型 2188 MB。在多 Pod 部署（minReplicas=3）下：
- INT8 总内存：3.4 GB
- FP32 总内存：6.6 GB

INT8 节省 47% 内存，降低部署成本和 OOM 风险。

### 2.4 负样本拒识能力一致

[case_042](./RERANKER_DISCRIMINATION_COMPARE_REPORT.json) `帮我订一张机票`（tricky 负样本，expected 为空）：

| 模型 | top3 结果 | precision@3 | 分数范围 |
|------|-----------|-------------|----------|
| jina INT8 | `[self_reflection]`（误召回）| 0.0 ❌ | 0.0562 |
| bge INT8 | `[]`（正确拒识）| 1.0 ✅ | < 0.001 |
| bge FP32 | `[]`（正确拒识）| 1.0 ✅ | < 0.001 |

**关键洞察**：bge 在 INT8 和 FP32 模式下都能正确拒识负样本——这归功于 bge 模型本身的分数分布（对负样本给极低分），而非 ONNX 量化精度。因此 FP32 在负样本拒识上**无额外优势**。

---

## 三、FP32 在什么场景下才需要启用

FP32 仅在以下**极端严苛**场景才需要启用，且需配合超时调大：

### 3.1 启用场景

1. **负样本拒识为 P0 安全要求**：例如医疗/法律领域，误召回可能导致严重后果，需要 FP32 的更宽 logit 范围提供额外保险（虽然 INT8 已能拒识，但 FP32 提供更高置信度）

2. **延迟不敏感的离线批处理**：如批量技能检索评估、回归测试套件，无 SLO 约束，可接受 2.22x 延迟换取理论上的精度上限

3. **A/B 对比实验**：作为 INT8 的精度上界基线，用于评估 INT8 量化损失（实测：无损失）

### 3.2 启用配置

```ini
# .env 中配置
SKILL_RERANKER_USE_ONNX=true
SKILL_RERANKER_ONNX_VARIANT=model.onnx
# FP32 P99 1080ms，需放宽超时（默认 3.0s 已足够，无需调大）
SKILL_RERANKER_RERANK_TIMEOUT=3.0
```

### 3.3 不推荐启用的场景

- ❌ **生产环境在线服务**：未达 500ms SLO，HPA 扩容压力
- ❌ **内存受限环境**：2188 MB 内存可能触发 OOM
- ❌ **希望提升精度**：实测 INT8 与 FP32 精度完全一致，无提升
- ❌ **希望改善负样本拒识**：实测两者拒识能力一致，bge 模型本身已能拒识

---

## 四、当前默认配置

[reranker.py](../agent/skills_mgmt/reranker.py) 和 [.env.example](../.env.example) 的默认配置：

```ini
# 模型：jina-reranker-v2（轻量，280MB，P99 258ms）
# 备选 bge-reranker-base（区分度 +121%，P99 487ms，需手动切换）
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual

# ONNX 变体：INT8 量化（首选）
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx

# 软拒识阈值：0.05（过滤极低分候选）
# 选值依据：bge 正样本 top1 最低 0.0623（case_020），0.05 不误杀；0.1 会误杀
SKILL_RERANKER_MIN_SCORE=0.05

# 单次推理超时：3.0s（INT8/FP32 均在 3s 内，无需调大）
SKILL_RERANKER_RERANK_TIMEOUT=3.0
```

### 4.1 关于 min_score 软拒识

2026-07-31 将 `min_score` 从 `0.001` 提到 `0.05`，实现"软拒识"——过滤极低分候选，提升整体信号质量。

**局限说明**：
- 对 bge：bge 已能给负样本极低分（< 0.001），min_score 调高无额外帮助
- 对 jina：jina 对负样本给出 0.0562（[case_042](./RERANKER_DISCRIMINATION_COMPARE_REPORT.json)），0.05 仍无法过滤；要过滤需提到 0.06，但会误杀 bge 正样本 case_020（top1=0.0623）

**真正解决负样本拒识的方案**：切换到 bge 模型（在 [.env](../.env.example) 中配置 `SKILL_RERANKER_MODEL` 为 bge 路径）。bge 在 min_score=0.001 时已能正确拒识，无需调高阈值。

---

## 五、模型选型对比矩阵

| 模型 | 大小 | ONNX P99 | 区分度 | 负样本拒识 | 推荐度 |
|------|------|----------|--------|-----------|--------|
| jina-reranker-v2 INT8 | 280 MB | 258 ms ✅ | 基线 | ❌ case_042 误召回 | ⭐⭐⭐ 当前默认（最快）|
| **bge-reranker-base INT8** | 266 MB | 487 ms ✅ | +121% | ✅ 正确拒识 | ⭐⭐⭐ **推荐**（精度+拒识最优）|
| bge-reranker-base FP32 | 1061 MB | 1080 ms ❌ | +121% | ✅ 正确拒识 | ⭐ 仅离线/对比用 |
| bge-reranker-v2-m3 PyTorch | 2300 MB | 4641 ms ❌ | 未测 | 未测 | ⭐ 谨慎（Windows 崩溃风险）|

### 5.1 切换到 bge 的配置（推荐用于负样本拒识场景）

```ini
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
SKILL_RERANKER_MIN_SCORE=0.05
SKILL_RERANKER_RERANK_TIMEOUT=3.0
```

---

## 六、附录：测试方法

### 6.1 区分度对比

```powershell
# FP32 模式（需手动切 variant）
$env:SKILL_RERANKER_ONNX_VARIANT="model.onnx"
$env:BGE_MODEL_PATH="C:/.../models--BAAI--bge-reranker-base"
$env:JINA_MODEL_PATH="C:/.../models--jinaai--jina-reranker-v2-base-multilingual"
python scripts/compare_reranker_discrimination.py
```

### 6.2 延迟 benchmark

```powershell
# INT8（默认）
python scripts/benchmark_v65_bge_base_reranker.py

# FP32（需切 variant）
$env:SKILL_RERANKER_ONNX_VARIANT="model.onnx"
python scripts/benchmark_v65_bge_base_reranker.py
```

### 6.3 相关文件

- [RERANKER_DISCRIMINATION_COMPARE_REPORT.json](./RERANKER_DISCRIMINATION_COMPARE_REPORT.json)：区分度对比原始数据
- [v65_bge_base_benchmark.json](./v65_bge_base_benchmark.json)：延迟 benchmark 原始数据
- [convert_bge_to_onnx.py](../scripts/convert_bge_to_onnx.py)：INT8 量化转换脚本
- [download_bge_reranker_base_modelscope.py](../scripts/download_bge_reranker_base_modelscope.py)：bge-base 模型下载
