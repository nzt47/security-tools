# GPU 环境部署指南 — jina-reranker-v2 GPU 推理

**目的**: 在本地 GPU 环境部署 jina-reranker-v2，满足 500ms SLO 要求
**硬件**: NVIDIA GeForce GTX 1650（4GB 显存，CUDA 13.2 驱动）
**预期**: GPU 推理 P99 < 100ms（vs CPU 7960ms），轻松满足 500ms SLO

---

## 1. 环境概览

### 1.1 当前环境状态

| 组件 | 当前状态 | 目标状态 |
|------|---------|---------|
| GPU 硬件 | ✅ GTX 1650 4GB | ✅ 无需变更 |
| GPU 驱动 | ✅ 595.79（CUDA 13.2） | ✅ 无需变更 |
| torch | ❌ 2.13.0+cpu（不支持 CUDA） | ✅ 2.x+cu121（CUDA 12.1） |
| 模型 | ✅ jina-reranker-v2 已下载 | ✅ 无需变更 |
| SkillReranker | ⚠️ 已禁用（RRF 降级） | ✅ GPU 启用 |

### 1.2 为什么 GPU 能达标

| 指标 | CPU（PyTorch） | GPU（预期） | 说明 |
|------|---------------|------------|------|
| P99 延迟 | 7960ms | < 100ms | GPU 并行计算 80x 加速 |
| QPS | 0.15 | > 10 | 高吞吐 |
| 显存 | 966MB RAM | ~300MB VRAM | 模型 280MB 完全放入 4GB 显存 |
| SLO | ❌ | ✅ | 远低于 500ms 阈值 |

### 1.3 GTX 1650 兼容性

- **算力**: 7.5（Turing 架构）
- **CUDA 支持**: 11.0+（推荐 CUDA 12.1）
- **显存**: 4GB GDDR5（jina 模型 280MB，余量充足）
- **PyTorch 支持**: 完整支持（sm_75 架构）

---

## 2. 安装步骤

### 2.1 步骤 1: 备份当前环境

```bash
# 记录当前 torch 版本（便于回滚）
pip show torch > torch_backup.txt
pip show torchvision >> torch_backup.txt
pip show torchaudio >> torch_backup.txt
```

### 2.2 步骤 2: 卸载 CPU 版 torch

```bash
# 卸载 CPU 版本
pip uninstall torch torchvision torchaudio -y
```

### 2.3 步骤 3: 安装 GPU 版 torch

```bash
# 方案 A: CUDA 12.1（推荐，兼容 CUDA 13.2 驱动）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

# 方案 B: CUDA 11.8（备选，更稳定）
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

> **注意**: CUDA 12.1 的 torch 可以在 CUDA 13.2 驱动上运行（驱动向下兼容）。无需更新 GPU 驱动。

### 2.4 步骤 4: 验证 GPU 可用

```bash
python -c "import torch; print('torch:', torch.__version__); print('cuda:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'N/A')"
```

**预期输出**:
```
torch: 2.x.x+cu121
cuda: True
device: NVIDIA GeForce GTX 1650
```

### 2.5 步骤 5: 模型加载验证

```bash
python -c "
import os
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

model_path = 'C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual'
print('加载模型到 GPU...')
model = AutoModelForSequenceClassification.from_pretrained(model_path, trust_remote_code=True).cuda()
model.eval()
print(f'GPU 显存: {torch.cuda.memory_allocated()/1024**3:.2f}GB')
print('✅ GPU 模型加载成功')
"
```

### 2.6 步骤 6: 运行 GPU 压测

```bash
python scripts/benchmark_v65_gpu_reranker.py
```

**预期结果**: P99 < 100ms ✅

---

## 3. 生产配置切换

### 3.1 修改 .env 启用 GPU Reranker

若 GPU 压测通过，修改 `.env`：

```bash
# 启用 Reranker
SKILL_RERANKER_ENABLED=true

# 模型路径（已指向 jina 本地路径）
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual
```

### 3.2 SkillReranker GPU 适配

当前 `agent/skills_mgmt/reranker.py` 使用 `sentence_transformers.CrossEncoder`，需添加 GPU 支持：

```python
# agent/skills_mgmt/reranker.py _load_model() 方法修改
def _load_model(self) -> bool:
    if self._model is not None:
        return True
    if self._load_attempted:
        return False

    self._load_attempted = True
    try:
        import torch
        from sentence_transformers import CrossEncoder

        # GPU 检测：有 GPU 时自动启用
        use_gpu = torch.cuda.is_available()
        device = "cuda" if use_gpu else "cpu"

        self._model = CrossEncoder(
            self._model_name,
            trust_remote_code=True,
            device=device,  # 自动选择 GPU/CPU
        )

        logger.info(json.dumps({
            "module_name": "reranker",
            "action": "model.loaded",
            "model": self._model_name,
            "device": device,
            "gpu_memory_gb": round(torch.cuda.memory_allocated()/1024**3, 2) if use_gpu else 0,
        }, ensure_ascii=False))
        return True
    except Exception as e:
        logger.warning(json.dumps({
            "module_name": "reranker",
            "action": "model.load_failed",
            "model": self._model_name,
            "error": str(e)[:300],
        }, ensure_ascii=False))
        return False
```

### 3.3 验证生产环境

```bash
# 1. 确认 .env 配置
findstr "SKILL_RERANKER" .env

# 2. 运行集成测试
python -m pytest tests/test_reranker.py -v

# 3. 运行 v6.5 完整压测
python scripts/benchmark_v65_jina_reranker.py
```

---

## 4. 性能预期与监控

### 4.1 GPU 性能预期

| 指标 | CPU（基准） | GPU（预期） | 提升 |
|------|-----------|------------|------|
| 模型加载 | 49.5s | ~5s | 10x |
| 单次 P99 | 7960ms | < 100ms | 80x |
| QPS | 0.15 | > 10 | 67x |
| 内存/显存 | 966MB RAM | ~300MB VRAM | - |

### 4.2 监控指标

启用后应监控：
1. **GPU 利用率**：`nvidia-smi -l 1`（应 < 80%）
2. **显存占用**：应稳定在 ~300MB（4GB 显存余量充足）
3. **推理延迟**：rerank.completed 日志的 duration_ms 字段
4. **降级触发率**：rerank.fallback 日志应 < 1%

### 4.3 告警阈值

| 指标 | 阈值 | 告警动作 |
|------|------|---------|
| P99 延迟 | > 500ms | 检查 GPU 状态 |
| GPU 利用率 | > 95% | 考虑批处理优化 |
| 显存占用 | > 3.5GB | 检查内存泄漏 |
| 降级率 | > 5% | 检查模型加载 |

---

## 5. 故障排查

### 5.1 torch.cuda.is_available() 返回 False

```bash
# 检查 1: torch 版本是否含 +cu121
python -c "import torch; print(torch.__version__)"
# 应显示 2.x.x+cu121，若显示 +cpu 则安装错误

# 检查 2: GPU 驱动
nvidia-smi
# 应显示 GPU 信息和 CUDA 版本

# 检查 3: CUDA 匹配
python -c "import torch; print(torch.version.cuda)"
# 应显示 12.1，若为 None 则 torch 不支持 CUDA
```

### 5.2 模型加载 OOM（显存不足）

```bash
# GTX 1650 有 4GB 显存，jina 模型 280MB，通常不会 OOM
# 若 OOM，检查是否有其他进程占用显存
nvidia-smi  # 查看 Processes 部分

# 释放显存
python -c "import torch; torch.cuda.empty_cache()"
```

### 5.3 GPU 推理结果异常

```bash
# 对比 CPU 和 GPU 排序分数一致性
python -c "
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

model_path = 'C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual'
tokenizer = AutoTokenizer.from_pretrained(model_path, trust_remote_code=True)
pairs = [('语音识别', '语音助手'), ('语音识别', 'PDF解析')]

# CPU 推理
model_cpu = AutoModelForSequenceClassification.from_pretrained(model_path, trust_remote_code=True)
encoded = tokenizer([p[0] for p in pairs], [p[1] for p in pairs], return_tensors='pt', padding=True, truncation=True)
print('CPU:', model_cpu(**encoded).logits.tolist())

# GPU 推理
model_gpu = AutoModelForSequenceClassification.from_pretrained(model_path, trust_remote_code=True).cuda()
encoded_gpu = {k: v.cuda() for k, v in encoded.items()}
print('GPU:', model_gpu(**encoded_gpu).logits.cpu().tolist())
"
```

### 5.4 回滚方案

若 GPU 部署出现问题，立即回滚：

```bash
# 1. 禁用 Reranker（回滚到 RRF 降级）
# .env:
SKILL_RERANKER_ENABLED=false

# 2.（可选）恢复 CPU 版 torch
pip uninstall torch torchvision torchaudio -y
pip install torch torchvision torchaudio
```

---

## 6. 混合部署策略（推荐）

### 6.1 CPU + GPU 双路径

生产环境推荐 CPU + GPU 双路径架构：

```
用户查询
    │
    ├── 检测 GPU 可用性
    │   ├── GPU 可用 → GPU 推理（P99 < 100ms）
    │   └── GPU 不可用 → 降级 CPU ONNX（P99 ~500ms）
    │       └── ONNX 不可用 → 降级 RRF（P99 0.5ms）
    │
    ▼
  返回结果
```

### 6.2 优势

1. **高可用**: GPU 故障时自动降级，不中断服务
2. **灵活性**: 支持有/无 GPU 的混合部署环境
3. **渐进式**: 可先部署 CPU ONNX，再逐步启用 GPU

### 6.3 实现要点

```python
# agent/skills_mgmt/reranker.py 降级链
def _load_model(self) -> bool:
    # 优先级 1: GPU PyTorch
    if torch.cuda.is_available():
        self._load_gpu_model()  # GPU 路径
    # 优先级 2: CPU ONNX 量化
    elif os.path.exists(onnx_path):
        self._load_onnx_model()  # ONNX 路径
    # 优先级 3: CPU PyTorch（兜底）
    else:
        self._load_pytorch_model()  # PyTorch 路径
```

---

## 7. 部署检查清单

- [ ] GPU 驱动已安装（`nvidia-smi` 正常）
- [ ] torch GPU 版本已安装（`torch.cuda.is_available()` 返回 True）
- [ ] jina 模型已下载（路径存在）
- [ ] GPU 模型加载成功（无 OOM）
- [ ] GPU 压测通过（P99 < 500ms）
- [ ] .env 已配置 `SKILL_RERANKER_ENABLED=true`
- [ ] SkillReranker 已添加 GPU 支持（device 参数）
- [ ] 集成测试通过
- [ ] 监控指标正常（GPU 利用率、显存、延迟）
- [ ] 回滚方案已验证
