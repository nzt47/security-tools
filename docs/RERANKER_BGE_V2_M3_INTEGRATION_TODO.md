# BGE-reranker-v2-m3 集成方案待办事项

**创建日期**: 2026-07-30
**背景**: jina-reranker-v2 量化 ONNX 在黄金集上 `score_stddev=0.0`（所有候选 sigmoid 分数完全相同 0.242719），未改变排序，Precision@3 提升 0%。需调研 bge-reranker-v2-m3 是否能提供区分度。
**关联提交**: `298add72`（sigmoid 修复 + reranked 契约修复）
**关联脚本**: `scripts/compare_reranker_discrimination.py`（区分度对比）

> **状态更新 (2026-08-24)**: 本文档大部分步骤已被后续工作完成，汇总如下：
> - ✅ Step 1 模型下载: `download_bge_reranker_base_modelscope.py` / `download_bge_reranker_v2_m3_modelscope.py` 已存在
> - ✅ Step 2 ONNX 转换: `convert_bge_to_onnx.py` 已存在
> - ✅ Step 3 加载适配: `reranker.py` 默认模型已切换为 `BAAI/bge-reranker-v2-m3`（`_DEFAULT_MODEL`），模型选型表已更新
> - ✅ Step 4 区分度对比: `RERANKER_DISCRIMINATION_COMPARE_REPORT.json`（stddev 提升 121%, precision 提升 9.09%）+ `RERANKER_PRECISION_EVAL_REPORT.json`（Precision@3 5.26%，未达 20% 验收线但区分度已解决）
> - 遗留: Precision@3 相对提升 5.26% < 20% 验收阈值（黄金集仅 8 技能候选池小，提升空间受限）；后续如需进一步提升可扩大黄金集或引入 v2-m3 ONNX 量化验证延迟

---

## 模型选型权衡

| 模型 | 大小 | 架构 | CPU P99 | SLO (3s) | 中文支持 | 推荐度 |
|------|------|------|---------|----------|---------|--------|
| bge-reranker-v2-m3 | ~2.3GB | - | 4641ms | ❌ 超时 | ✅ SOTA | ⭐⭐ 谨慎 |
| bge-reranker-base | ~1.1GB | XLM-RoBERTa-base | 待测 | ? | ✅ 良好 | ⭐⭐⭐ 优先 |
| jina-reranker-v2 | ~280MB | XLM-RoBERTa-large | 7960ms | ❌ | ✅ 良好 | ⭐ 当前（无区分度）|

**关键风险**: v2-m3 CPU P99 4641ms > `SKILL_RERANKER_RERANK_TIMEOUT=3.0`，会触发超时降级返回原序。需先验证 ONNX 量化能否加速到 SLO 内，或调大 timeout。

**决策**: 优先测试 bge-reranker-base（~1.1GB，可能满足 SLO）；若区分度仍不足，再测试 v2-m3（需调大 timeout 或用 ONNX 量化加速）。

---

## Step 1: 模型下载

### 1.1 bge-reranker-base 下载（已有脚本）

```bash
# 通过 modelscope 镜像下载（huggingface.co 可能不可达）
python scripts/download_bge_reranker_base_modelscope.py
```

- 下载路径: `~/.cache/huggingface/hub/models--BAAI--bge-reranker-base`
- 大小: ~1.1GB
- 状态: ✅ 脚本已存在

### 1.2 bge-reranker-v2-m3 下载（需新建脚本）

- [ ] 新建 `scripts/download_bge_reranker_v2_m3_modelscope.py`
  - 复用 `download_bge_reranker_base_modelscope.py` 模板
  - 修改 `MODELSCOPE_MODEL_ID = "BAAI/bge-reranker-v2-m3"`
  - 修改 `DEFAULT_LOCAL_DIR = "~/.cache/huggingface/hub/models--BAAI--bge-reranker-v2-m3"`
  - 大小: ~2.3GB（下载耗时较长，需进度提示）
- [ ] 验证下载完整性（`config.json` + `pytorch_model.bin` + `tokenizer.json`）
- [ ] 测试网络不可达时的降级（modelscope → huggingface → 本地缓存）

---

## Step 2: ONNX 转换与量化

### 2.1 转换为 ONNX 格式

- [ ] 复用 `scripts/convert_jina_to_onnx.py` 模板，新建 `scripts/convert_bge_to_onnx.py`
  - 输入: PyTorch 模型（`BAAI/bge-reranker-base` 或 `bge-reranker-v2-m3`）
  - 输出: `onnx/model_quantized.onnx`（INT8 量化）+ `onnx/model.onnx`（FP32）
  - 量化方法: 动态量化（DynamicQuantization），与 jina 一致
- [ ] 验证 ONNX 模型推理结果与 PyTorch 一致（logits 差异 < 0.01）

### 2.2 ONNX 加速验证

- [ ] 测量 ONNX 量化版 P99 延迟，确认是否满足 3s SLO
  - bge-reranker-base ONNX 量化 P99 目标: < 2000ms
  - bge-reranker-v2-m3 ONNX 量化 P99 目标: < 3000ms（若不满足需调大 timeout）
- [ ] 若 v2-m3 ONNX 仍超时，评估是否调大 `SKILL_RERANKER_RERANK_TIMEOUT=5.0`
  - 权衡: 超时调大会阻塞主流程，需评估用户体验影响

---

## Step 3: 加载逻辑适配

### 3.1 SkillReranker 兼容性验证

**【不易】不破坏现有接口**: `SkillReranker.__init__(model_name)` 已支持任意 HuggingFace ID/路径，无需改代码。仅需通过环境变量切换。

- [ ] 验证 `SkillReranker(model_name="BAAI/bge-reranker-base")` 能正确加载
  - 检查 `_load_onnx()` 路径推断（`<model_dir>/onnx/model_quantized.onnx`）
  - 检查 `_load_pytorch()` fallback 路径
- [ ] 验证 `SkillReranker(model_name="BAAI/bge-reranker-v2-m3")` 能正确加载
- [ ] 验证 tokenizer 加载（`AutoTokenizer.from_pretrained`）兼容 BGE 系列

### 3.2 配置切换方案

通过 `.env` 环境变量切换模型（守【不易】配置统一管理约束）:

```bash
# .env 切换到 bge-reranker-base（推荐，优先测试）
SKILL_RERANKER_MODEL=BAAI/bge-reranker-base
SKILL_RERANKER_USE_ONNX=true
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
SKILL_RERANKER_ENABLED=true
# 若超时，调大 timeout（默认 3.0）
# SKILL_RERANKER_RERANK_TIMEOUT=5.0
```

```bash
# .env 切换到 bge-reranker-v2-m3（v2-m3 需更大 timeout）
SKILL_RERANKER_MODEL=BAAI/bge-reranker-v2-m3
SKILL_RERANKER_USE_ONNX=true
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
SKILL_RERANKER_ENABLED=true
SKILL_RERANKER_RERANK_TIMEOUT=5.0  # v2-m3 CPU P99 4641ms，需调大
```

- [ ] 在 `.env.example` 添加注释说明 bge 系列配置
- [ ] 文档: 在 `agent/skills_mgmt/reranker.py` 模块 docstring 更新模型选型表

---

## Step 4: 区分度对比评估

### 4.1 运行对比脚本

```bash
$env:PYTHONIOENCODING="utf-8"
$env:SKILLS_OFFLINE="1"
# 对比 jina（当前）vs bge-reranker-base
python scripts/compare_reranker_discrimination.py `
  --jina "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual" `
  --bge "BAAI/bge-reranker-base"
```

- [ ] 运行对比脚本，记录 `avg_score_stddev` 对比
- [ ] 验收: bge 的 `avg_score_stddev > 0.0`（jina 为 0.0）
- [ ] 验收: bge 的 `rank_change_rate > 0%`（jina 为 0%）

### 4.2 Precision@3 评估

```bash
# 切换到 bge-reranker-base 后跑评估
$env:SKILL_RERANKER_MODEL="BAAI/bge-reranker-base"
python scripts/eval_reranker_precision_compare.py
```

- [ ] 验收: `Precision@3 相对提升 ≥ 20%`（jina 为 0%）
- [ ] 验收: `score_stddev > 0.0`（jina 为 0.0，证明有区分度）
- [ ] 保存评估报告到 `docs/RERANKER_PRECISION_EVAL_REPORT_BGE_BASE.json`

### 4.3 若 base 不达标，测试 v2-m3

- [ ] 重复 4.1/4.2，使用 `--bge "BAAI/bge-reranker-v2-m3"`
- [ ] 确认 v2-m3 不超时（或调大 timeout 后不超时）
- [ ] 保存评估报告到 `docs/RERANKER_PRECISION_EVAL_REPORT_BGE_V2_M3.json`

---

## Step 5: 集成测试与切换

### 5.1 单元测试

- [ ] 跑 `tests/unit/test_reranker.py` 确认 bge 模型加载不破坏现有测试
- [ ] 跑 `tests/unit/test_reranker_onnx.py` 确认 ONNX 路径兼容
- [ ] 跑 `tests/unit/test_reranker_regression.py` 确认回归测试通过

### 5.2 生产切换

- [ ] 更新 `.env` 默认模型为 bge-reranker-base（若评估达标）
- [ ] 更新 `reranker.py` 的 `_DEFAULT_MODEL` 常量
- [ ] 更新模块 docstring 模型选型表
- [ ] commit message: `feat(skills_mgmt): 切换默认 reranker 到 bge-reranker-base（区分度修复）`

### 5.3 监控验证

- [ ] 部署后观察 `yunshu_reranker_fallback_total` 指标
  - 期望: fallback 率 < 5%（模型可用率高）
  - 告警: fallback 率 > 10% 时触发告警
- [ ] 观察 `rerank.completed` 日志的 `score_stddev` 字段
  - 期望: avg_stddev > 0.0（区分度正常）

---

## 验收标准

| 指标 | jina（当前） | bge 目标 | 验收 |
|------|-------------|---------|------|
| avg_score_stddev | 0.0 | > 0.0 | 必须 |
| rank_change_rate | 0% | > 0% | 必须 |
| Precision@3 相对提升 | 0% | ≥ 20% | 必须 |
| ONNX P99 延迟 | ~35ms | < 3000ms | 必须 |
| reranker fallback 率 | 0% | < 5% | 必须 |

---

## 回滚方案

若 bge 模型评估不达标或引入回归:

```bash
# 回滚到 jina-reranker-v2
# 修改 .env:
# SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual
git revert <bge-switch-commit>
```

---

## 风险与注意事项

1. **CPU SLO 风险**: bge-reranker-v2-m3 CPU P99 4641ms，可能超时降级。优先测试 base 版本。
2. **下载风险**: huggingface.co 可能不可达，需用 modelscope 镜像。
3. **ONNX 量化质量**: 量化可能损失区分度，需对比 PyTorch float32 原始模型的 stddev。
4. **黄金集局限**: 当前仅 8 技能，候选池小。即使 bge 有区分度，Precision@3 提升可能有限。考虑扩大黄金集。
5. **Windows 崩溃**: BGE 系列在 Windows CPU 上可能触发 0xC0000005（参考 project_memory），ONNX 路径豁免子进程隔离，PyTorch 路径需子进程隔离。
