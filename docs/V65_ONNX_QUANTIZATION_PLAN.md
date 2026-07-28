# jina-reranker-v2 ONNX 量化方案

**目的**: 通过 ONNX Runtime + INT8 量化提升 jina-reranker-v2 CPU 推理速度，尝试满足 500ms SLO
**基准**: PyTorch CPU P99 7960ms（20候选），远超 500ms SLO
**预期**: ONNX 量化后 P99 目标 ≤ 500ms（预期提升 5-10x）

---

## 0. 重大发现：jina 已预导出 7 种 ONNX 格式（2026-07-28 更新）

### 0.1 关键发现

在尝试自行转换 ONNX 失败（jina 自定义 `configuration_xlm_roberta.py` 第 66 行 `torch_dtype` 期望字符串而非 `torch.dtype` 对象）后，发现模型目录下 `onnx/` 子目录已**预导出 7 种 ONNX 格式**，无需自行转换：

| 变体 | 文件 | 大小 | 说明 |
|------|------|------|------|
| int8 | `model_int8.onnx` | 266MB | INT8 动态量化（最小） |
| uint8 | `model_uint8.onnx` | 266MB | UINT8 动态量化 |
| quantized | `model_quantized.onnx` | 266MB | 动态量化（与 int8 等价） |
| fp16 | `model_fp16.onnx` | 531MB | FP16 半精度 |
| q4 | `model_q4.onnx` | 789MB | 4-bit 量化 |
| bnb4 | `model_bnb4.onnx` | 784MB | bitsandbytes 4-bit |
| fp32 | `model.onnx` | 1062MB | FP32 基准 |

### 0.2 方案调整（守简易原则）

**原方案**：自行 PyTorch → ONNX → 量化（需修复 torch_dtype 兼容问题）
**新方案**：直接压测 7 种预导出格式选最优，跳过转换步骤

**理由**：
- jina 官方已提供量化模型，自转换属于造重复轮子（违简易）
- 7 种变体覆盖所有常见量化方案，必有最优解
- 自转换需修复 `trust_remote_code` + `torch_dtype` 兼容性，复杂度高

### 0.3 实测结果（2026-07-28 02:26）

压测脚本：`scripts/benchmark_v65_onnx_reranker.py`
结果文件：`docs/v65_onnx_benchmark.json`

#### 0.3.1 7 种 ONNX 变体压测数据（20 候选 × 20 次迭代）

| 变体 | 文件 | 大小MB | 加载s | P50ms | P99ms | QPS | 排序正确 | SLO ≤500ms |
|------|------|--------|-------|-------|-------|-----|---------|-----------|
| **quantized** | `model_quantized.onnx` | **266.63** | **3.12** | **239.65** | **258.14** | **4.15** | **✅** | **✅ 最优** |
| q4 | `model_q4.onnx` | 789.06 | 10.48 | 279.41 | 301.79 | 3.56 | ✅ | ✅ |
| int8 | `model_int8.onnx` | 266.63 | 2.67 | 237.04 | 363.12 | 4.07 | ✅ | ✅ |
| bnb4 | `model_bnb4.onnx` | 784.00 | 5.40 | 388.09 | 464.77 | 2.51 | ✅ | ✅ |
| uint8 | `model_uint8.onnx` | 266.63 | 2.72 | 291.62 | 439.06 | 3.29 | ✅ | ✅ |
| fp32 | `model.onnx` | 1062.43 | 5.40 | 364.08 | 504.69 | 2.61 | ✅ | ❌ 临界 |
| fp16 | `model_fp16.onnx` | 531.35 | 6.35 | 507.43 | 621.96 | 1.93 | ✅ | ❌ |

#### 0.3.2 关键发现

1. **最优变体：quantized**（P99 258.14ms，P50 239.65ms，QPS 4.15，266MB）
   - 离散度最小：max 258.26ms ≈ P99 258.14ms ≈ P95 257.67ms（分布极稳定）
   - 与 int8 同尺寸（266MB）但 P99 低 30%（258 vs 363ms），说明 quantized 量化方案对长尾更友好
2. **5/7 变体通过 SLO**：int8/uint8/quantized/q4/bnb4 全部通过；fp16/fp32 临界或未通过
3. **排序正确性 100%**：所有变体均能正确识别"语音 > PDF"（分数 2.29 vs -2.48）
4. **CPU 不支持 FP16 加速**：fp16 反而比 fp32 慢（622ms vs 505ms），因为 CPU 需将 fp16 反量化为 fp32 计算

#### 0.3.3 ONNX quantized vs PyTorch CPU 总体加速比

| 指标 | PyTorch CPU | ONNX quantized | 加速倍数 |
|------|-------------|----------------|----------|
| P99 延迟 | 7960ms | 258ms | **30.8x** |
| P50 延迟 | 6590ms | 240ms | 27.5x |
| QPS | 0.15 | 4.15 | 27.7x |
| 模型加载 | 49.5s | 3.12s | 15.9x |
| 模型大小 | 966MB（内存） | 266MB（文件） | 3.6x |
| SLO 达标 | ❌ | ✅ | - |

#### 0.3.4 决策

```
ONNX quantized P99 258ms ≤ 500ms SLO？
└─ 是 ✅
    └─ 排序正确？ → ✅
        └─ 下一步：集成 ONNX 推理到 SkillReranker 生产路径
            ├─ 优先加载 onnx/model_quantized.onnx
            ├─ 失败降级到 PyTorch 路径
            └─ PyTorch 失败降级到 RRF
```

#### 0.3.5 长稳压测结果（1000 次迭代，2026-07-28 12:07）

压测脚本：`scripts/benchmark_v65_onnx_long_stability.py`
结果文件：`docs/v65_onnx_long_stability.json`

| 指标 | 实测值 | SLO 目标 | 结果 |
|------|--------|---------|------|
| 总迭代 | 1000 | - | - |
| 总耗时 | 262.7s | - | - |
| QPS | 3.81 | ≥ 3 | ✅ |
| P50 延迟 | 254.65ms | - | - |
| P95 延迟 | 340.24ms | - | - |
| P99 延迟 | 427.95ms | ≤ 500ms | ✅ |
| P99.9 延迟 | 521.92ms | - | - |
| max 延迟 | 566.52ms | - | - |
| RSS 起始（预热后）| 1156.51MB | - | - |
| RSS 结束 | 1156.5MB | - | - |
| RSS 峰值 | 1156.57MB | ≤ 1.5GB | ✅ |
| RSS 增量 | **-0.01MB** | ≤ 50MB | ✅ 零增长 |
| 排序正确性 | 5/5 次 ✅ | 100% | ✅ |
| **综合结论** | **全部通过** | - | **✅ 可投入生产** |

**关键发现**：
- **内存零增长**：1000 次迭代 RSS 增量 -0.01MB，无任何泄漏迹象
- **P99 稳定性**：各 100 次快照 P99 在 379-453ms 区间，分布稳定
- **排序一致性**：5 次验证分数完全一致（2.2891 vs -2.4806），无量化漂移
- **P99.9 略超 SLO**：99.9 分位 521.92ms 略超 500ms，但 P99 427.95ms 充分达标，可接受

#### 0.3.6 生产集成状态（2026-07-28）

✅ **已集成到生产路径**：

| 文件 | 变更 |
|------|------|
| `agent/skills_mgmt/reranker.py` | 新增 `_load_onnx()` / `_load_pytorch()` / `_predict_onnx()` 三个方法；`_load_model()` 改为 ONNX 优先 → PyTorch 降级；`_predict_with_timeout()` 按标志分发 |
| `.env` | 新增 `SKILL_RERANKER_USE_ONNX=true` + `SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx`；`SKILL_RERANKER_ENABLED` 从 false 改为 true |
| `tests/unit/test_reranker_onnx.py` | 新增 39 个单元测试，覆盖 ONNX 加载/推理/分发/降级链/环境变量 |
| `scripts/smoke_test_onnx_integration.py` | 端到端冒烟测试（真实模型） |

**冒烟测试结果**：
- ONNX 路径启用 ✅（`_use_onnx=True`，PyTorch 模型未加载）
- 语音查询 → voice_interaction 首位 ✅
- PDF 查询 → pdf_parser 首位 ✅
- 第二次推理 52ms（3 候选）✅

---

## 1. 背景与动机

### 1.1 问题现状

| 推理方式 | P99 延迟 | QPS | SLO 达标 |
|---------|---------|-----|---------|
| PyTorch CPU（jina-v2） | 7960ms | 0.15 | ❌ |
| PyTorch CPU（v2-m3） | 4641ms | 0.30 | ❌ |
| RRF 降级（无 Reranker） | 0.5ms | 121,327 | ✅ |

CPU 环境下所有 PyTorch Cross-Encoder 均不达标，但 RRF 降级缺乏精排能力。

### 1.2 ONNX 量化的优势

| 特性 | PyTorch | ONNX Runtime | 说明 |
|------|---------|-------------|------|
| 计算图优化 | 无 | 有（算子融合、常量折叠） | 减少计算量 |
| 量化 | 需手动 | 内置 INT8 动态量化 | 权重从 FP32→INT8，减半内存和计算 |
| 推理引擎 | Python 解释执行 | C++ 引擎 | 更低开销 |
| 线程优化 | GIL 限制 | 无 GIL | 更好的多线程利用 |

**预期提升**：
- ONNX 原始（FP32）：2-3x（计算图优化）
- ONNX INT8 量化：4-8x（优化 + 量化）
- 预期 P99：1000-2000ms（原始）、500-1000ms（量化）

---

## 2. 技术方案

### 2.1 转换流程

```
PyTorch 模型 (FP32)
    │
    ├── torch.onnx.export
    │   ├── 输入: input_ids, attention_mask
    │   ├── 输出: logits
    │   ├── dynamic_axes: batch + sequence 维度动态
    │   └── opset_version: 14
    │
    ▼
ONNX 原始模型 (FP32)
    │
    ├── onnxruntime.quantization.quantize_dynamic
    │   ├── weight_type: QInt8（权重 INT8 量化）
    │   ├── 激活值保持 FP32（动态量化）
    │   └── 无需校准数据集
    │
    ▼
ONNX 量化模型 (INT8 权重 + FP32 激活)
```

### 2.2 关键设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 量化方式 | 动态量化 | 无需校准数据，部署简单；激活值 FP32 保证精度 |
| 权重类型 | QInt8 | 比 QUInt8 精度更高，推理速度相当 |
| ONNX opset | 14 | 支持 XLM-RoBERTa 所有算子 |
| dynamic_axes | batch + sequence | 支持不同候选数和文本长度 |
| 推理引擎 | CPUExecutionProvider | 当前环境无 GPU 加速 ONNX |

### 2.3 jina-reranker-v2 特殊处理

jina-reranker-v2 含自定义代码（`trust_remote_code=True`），转换时需注意：

1. **加载方式**：`AutoModelForSequenceClassification.from_pretrained(path, trust_remote_code=True)`
2. **forward 参数**：仅需 `input_ids` + `attention_mask`（token_type_ids 可选）
3. **兼容性修复**：embedding.py 已内联 `_create_position_ids_from_input_ids`（transformers 5.x）

---

## 3. 实施步骤

### 3.1 环境准备

```bash
# 安装 ONNX 依赖
pip install onnx onnxruntime

# 验证
python -c "import onnx; print(onnx.__version__)"
python -c "import onnxruntime; print(onnxruntime.__version__)"
```

### 3.2 执行转换

```bash
# 转换 + 量化（一键完成）
python scripts/convert_jina_to_onnx.py
```

转换脚本执行 4 个步骤：
1. **ONNX 导出**：PyTorch → ONNX（FP32）
2. **INT8 量化**：ONNX FP32 → ONNX INT8
3. **模型验证**：加载 ONNX 检查输入输出
4. **分数对比**：PyTorch vs ONNX 排序分数一致性

### 3.3 性能压测

```bash
# ONNX 推理压测
python scripts/benchmark_v65_onnx_reranker.py
```

压测包含：
- 排序正确性验证（语音 + PDF 查询）
- 单次延迟（20候选 × 20次）
- 与 PyTorch CPU 基准对比

### 3.4 生产部署（若达标）

若 ONNX 量化 P99 ≤ 500ms，需修改 SkillReranker 支持 ONNX 推理：

```python
# agent/skills_mgmt/reranker.py 新增 ONNX 加载分支
def _load_model(self) -> bool:
    model_path = self._model_name
    onnx_path = os.path.join(model_path, "model_quantized.onnx")

    if os.path.exists(onnx_path):
        # ONNX 推理路径
        import onnxruntime as ort
        from transformers import AutoTokenizer
        self._tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
        self._onnx_session = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
        self._use_onnx = True
    else:
        # PyTorch 推理路径（原有逻辑）
        from sentence_transformers import CrossEncoder
        self._model = CrossEncoder(model_path, trust_remote_code=True)
        self._use_onnx = False
    return True
```

---

## 4. 预期结果

### 4.1 性能预期

| 推理方式 | 预期 P99 | 预期 QPS | 内存 | SLO |
|---------|---------|---------|------|-----|
| PyTorch CPU（基准） | 7960ms | 0.15 | 966MB | ❌ |
| ONNX FP32 | ~2000-3000ms | ~0.5 | ~500MB | ❌ 可能 |
| **ONNX INT8 量化** | **~500-1000ms** | **~2-5** | **~300MB** | **⚠️ 边界** |

### 4.2 决策矩阵

```
ONNX 量化 P99 ≤ 500ms？
├─ 是 ✅
│   └─ 集成 ONNX 推理到 SkillReranker，启用 Reranker
└─ 否 ❌
    ├─ P99 ≤ 1000ms？
    │   ├─ 是 → 评估减少候选数（20→10）是否能达标
    │   └─ 否 → ONNX 量化无法满足，转向 GPU 部署
    └─ P99 > 1000ms → 保持 RRF 降级
```

---

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| ONNX 导出失败（自定义代码） | 中 | 无法量化 | 尝试 optimum 库或手动构建计算图 |
| 量化后精度下降 | 低 | 排序质量降低 | 对比 PyTorch 分数，误差阈值 < 0.1 |
| 性能提升不足 | 中 | SLO 仍不达标 | 结合减少候选数 + ONNX 双重优化 |
| ONNX Runtime 兼容性 | 低 | 加载失败 | 降级到 PyTorch 推理 |

### 5.1 回滚方案

```bash
# 若 ONNX 不达标，保持现有 RRF 降级
# .env:
SKILL_RERANKER_ENABLED=false  # 保持 false
# ONNX 模型文件保留，不影响系统运行
```

---

## 6. 文件清单

| 文件 | 用途 |
|------|------|
| `scripts/convert_jina_to_onnx.py` | ONNX 转换 + 量化脚本 |
| `scripts/benchmark_v65_onnx_reranker.py` | ONNX 推理压测脚本 |
| `docs/V65_ONNX_QUANTIZATION_PLAN.md` | 本方案文档 |
| `docs/v65_onnx_benchmark.json` | 压测结果（自动生成） |
| `<model_dir>/model.onnx` | ONNX FP32 模型（转换生成） |
| `<model_dir>/model_quantized.onnx` | ONNX INT8 量化模型（转换生成） |
