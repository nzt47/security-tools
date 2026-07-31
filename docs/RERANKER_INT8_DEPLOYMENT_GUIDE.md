# Reranker INT8 部署配置清单

> 生成时间：2026-07-31
> 适用版本：v6.5 SkillReranker
> 关联代码：[reranker.py](../agent/skills_mgmt/reranker.py) | [.env.example](../.env.example)
> 选型依据：[RERANKER_INT8_VS_FP32_SELECTION.md](./RERANKER_INT8_VS_FP32_SELECTION.md)

## 一、部署前检查

### 1.1 模型文件清单

| 文件 | 路径 | 大小 | 用途 |
|------|------|------|------|
| INT8 ONNX | `models--BAAI--bge-reranker-base/onnx/model_quantized.onnx` | 266 MB | **主推理后端** |
| FP32 ONNX | `models--BAAI--bge-reranker-base/onnx/model.onnx` | 1061 MB | 备选（高精度场景）|
| Tokenizer | `models--BAAI--bge-reranker-base/tokenizer.json` 等 | ~5 MB | 文本编码 |
| Config | `models--BAAI--bge-reranker-base/config.json` | <1 KB | 模型配置 |

**下载方式**：
```powershell
python scripts/download_bge_reranker_base_modelscope.py
```

### 1.2 依赖版本

```
onnxruntime>=1.16.0          # ONNX 推理引擎（C++ 后端，无 GIL 问题）
transformers>=4.30.0         # Tokenizer 加载
sentence-transformers>=2.2.0 # PyTorch 降级后端（可选）
```

### 1.3 系统资源要求

| 资源 | 最低 | 推荐 | 实测 |
|------|------|------|------|
| 内存 | 2 GB | 3 GB | 1167 MB（INT8）|
| CPU | 2 核 | 4 核 | 单次推理 487ms（4 核）|
| 磁盘 | 500 MB | 1 GB | 模型 266 MB + 缓存 |
| Python | 3.10+ | 3.12 | 3.12.0 |

---

## 二、环境变量配置（.env）

### 2.1 生产环境配置（INT8，推荐）

```ini
# ========================================
# v6.5 Skill Reranker 配置（agent/skills_mgmt/reranker.py）
# ========================================

# 启用 Reranker（true/false，默认 true）
SKILL_RERANKER_ENABLED=true

# Cross-Encoder 模型路径（本地目录，需含 onnx/ 子目录）
# 推荐：bge-reranker-base INT8 量化（P99 487ms ✅ 达标 500ms SLO，区分度 +121%）
# 备选：jina-reranker-v2（P99 258ms 最快，但负样本拒识弱）
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base

# ONNX 推理开关（true/false，默认 true）
# 启用后优先加载 <model_dir>/onnx/<variant>，失败降级到 PyTorch
SKILL_RERANKER_USE_ONNX=true

# ONNX 变体文件名（默认 model_quantized.onnx 即 INT8 量化）
# 可选值：
#   model_quantized.onnx (P99 487ms ✅推荐) | model.onnx (P99 1080ms FP32)
#   model_int8.onnx (P99 363ms) | model_q4.onnx (P99 302ms) | model_uint8.onnx (P99 439ms)
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx

# 单次 rerank predict 超时秒数（默认 3.0）
# INT8 P99 487ms，3.0s 足够余量；FP32 P99 1080ms 仍在 3s 内
# 仅 v2-m3 PyTorch（P99 4.6s）需调到 6.0
SKILL_RERANKER_RERANK_TIMEOUT=3.0

# 最低分数阈值，低于此值的候选剔除（默认 0.05，软拒识）
# 选值依据：bge-base 正样本 top1 最低 0.0623，0.05 不误杀；0.1 会误杀
SKILL_RERANKER_MIN_SCORE=0.05

# 子进程超时秒数（默认 30，仅 PyTorch 后端用，ONNX 豁免）
SKILL_RERANKER_TIMEOUT=30
```

### 2.2 配置验证

启动后检查日志，确认以下字段：
```json
{
  "module_name": "reranker",
  "action": "onnx.loaded",
  "model": ".../models--BAAI--bge-reranker-base",
  "onnx_file": "model_quantized.onnx",
  "inputs": ["input_ids", "attention_mask"],
  "load_time_s": 3.24
}
```

- `onnx_file` 应为 `model_quantized.onnx`（INT8）
- `inputs` 应为 `["input_ids", "attention_mask"]`（非空）
- `load_time_s` 应 < 10s（冷启动）

### 2.3 推理日志验证

每次 rerank 成功后检查：
```json
{
  "action": "rerank.completed",
  "candidate_count": 10,
  "result_count": 3,
  "top_score": 0.98,
  "score_min": 0.003,
  "score_max": 0.98,
  "score_mean": 0.34,
  "score_stddev": 0.31,
  "duration_ms": 487
}
```

- `score_stddev > 0`：reranker 有区分度（非 0.0）
- `duration_ms < 500`：满足 500ms SLO
- `result_count > 0`：候选未被全部过滤

---

## 三、启动脚本

### 3.1 Windows PowerShell 启动脚本

```powershell
# scripts/start_reranker_int8.ps1
# 启动 Reranker INT8 服务（Windows 环境）

$ErrorActionPreference = "Stop"

# ── 环境变量 ──
$env:PYTHONIOENCODING = "utf-8"
$env:SKILLS_OFFLINE = "1"
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

$env:SKILL_RERANKER_ENABLED = "true"
$env:SKILL_RERANKER_MODEL = "C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base"
$env:SKILL_RERANKER_USE_ONNX = "true"
$env:SKILL_RERANKER_ONNX_VARIANT = "model_quantized.onnx"
$env:SKILL_RERANKER_RERANK_TIMEOUT = "3.0"
$env:SKILL_RERANKER_MIN_SCORE = "0.05"

# ── 健康检查 ──
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Reranker INT8 部署启动" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "模型: $env:SKILL_RERANKER_MODEL"
Write-Host "ONNX 变体: $env:SKILL_RERANKER_ONNX_VARIANT"
Write-Host "超时: $env:SKILL_RERANKER_RERANK_TIMEOUT s"
Write-Host "min_score: $env:SKILL_RERANKER_MIN_SCORE"
Write-Host ""

# ── 模型可用性验证 ──
Write-Host "[Step 1] 验证模型可用性..." -ForegroundColor Yellow
python -c @"
import os, sys
sys.path.insert(0, '.')
from agent.skills_mgmt.reranker import SkillReranker
r = SkillReranker()
avail = r.is_available()
print(f'  is_available: {avail}')
print(f'  use_onnx: {r._use_onnx}')
print(f'  onnx_variant: {r._onnx_variant}')
if not avail:
    print('  ERROR: 模型加载失败')
    sys.exit(1)
print('  OK: 模型可用')
"@
if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ 模型验证失败，请检查路径和文件" -ForegroundColor Red
    exit 1
}
Write-Host ""

# ── 启动主服务 ──
Write-Host "[Step 2] 启动主服务..." -ForegroundColor Yellow
python -m agent.app_server
```

### 3.2 Linux Bash 启动脚本

```bash
#!/bin/bash
# scripts/start_reranker_int8.sh
# 启动 Reranker INT8 服务（Linux 环境）

set -e

# ── 环境变量 ──
export PYTHONIOENCODING=utf-8
export SKILLS_OFFLINE=1
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1

export SKILL_RERANKER_ENABLED=true
export SKILL_RERANKER_MODEL=/root/.cache/huggingface/hub/models--BAAI--bge-reranker-base
export SKILL_RERANKER_USE_ONNX=true
export SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
export SKILL_RERANKER_RERANK_TIMEOUT=3.0
export SKILL_RERANKER_MIN_SCORE=0.05

# ── 健康检查 ──
echo "========================================"
echo "  Reranker INT8 部署启动"
echo "========================================"
echo "模型: $SKILL_RERANKER_MODEL"
echo "ONNX 变体: $SKILL_RERANKER_ONNX_VARIANT"
echo ""

# ── 模型可用性验证 ──
echo "[Step 1] 验证模型可用性..."
python -c "
import sys
sys.path.insert(0, '.')
from agent.skills_mgmt.reranker import SkillReranker
r = SkillReranker()
avail = r.is_available()
print(f'  is_available: {avail}')
print(f'  use_onnx: {r._use_onnx}')
if not avail:
    print('  ERROR: 模型加载失败')
    sys.exit(1)
print('  OK: 模型可用')
"
if [ $? -ne 0 ]; then
    echo "❌ 模型验证失败"
    exit 1
fi

# ── 启动主服务 ──
echo "[Step 2] 启动主服务..."
python -m agent.app_server
```

### 3.3 Docker 启动（可选）

```dockerfile
# Dockerfile.reranker
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制代码
COPY . .

# 复制模型（或挂载卷）
# COPY models/ /root/.cache/huggingface/hub/

ENV SKILL_RERANKER_ENABLED=true
ENV SKILL_RERANKER_MODEL=/root/.cache/huggingface/hub/models--BAAI--bge-reranker-base
ENV SKILL_RERANKER_USE_ONNX=true
ENV SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
ENV SKILL_RERANKER_RERANK_TIMEOUT=3.0
ENV SKILL_RERANKER_MIN_SCORE=0.05
ENV HF_HUB_OFFLINE=1
ENV TRANSFORMERS_OFFLINE=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD python -c "from agent.skills_mgmt.reranker import SkillReranker; assert SkillReranker().is_available()"

CMD ["python", "-m", "agent.app_server"]
```

---

## 四、监控与告警

### 4.1 Prometheus 指标

| 指标 | 类型 | 标签 | 用途 |
|------|------|------|------|
| `yunshu_reranker_load_total` | counter | backend, status, reason | 加载成功率 |
| `yunshu_reranker_load_time_seconds` | gauge | backend | 加载耗时 |
| `yunshu_rerank_duration_ms` | histogram | backend, success | 推理延迟 P99 |
| `yunshu_reranker_completed_total` | counter | backend | 成功推理计数 |
| `yunshu_reranker_fallback_total` | counter | from, to, reason | 降级率 |
| `yunshu_reranker_model_size_gb` | gauge | model | 模型大小 |

### 4.2 告警规则

| 指标 | 阈值 | 级别 | 处理 |
|------|------|------|------|
| rerank P99 | > 500 ms | warning | 检查并发/模型 |
| rerank P99 | > 1000 ms | critical | 切换更轻量模型 |
| 降级率 | > 5% | warning | 检查模型可用性 |
| 降级率 | > 20% | critical | 立即排查 |
| 加载失败 | > 0 | critical | 检查模型文件 |

### 4.3 日志关键字

- `onnx.loaded`：加载成功
- `onnx.load_failed`：加载失败
- `rerank.completed`：推理成功
- `rerank.fallback`：降级（model_unavailable）
- `predict.timeout`：超时降级

---

## 五、故障排查

### 5.1 模型加载失败

```json
{"action": "onnx.skip", "reason": "onnx_file_not_found", "expected_path": "..."}
```

**处理**：
1. 检查 `SKILL_RERANKER_MODEL` 路径是否存在
2. 检查 `<model_dir>/onnx/model_quantized.onnx` 文件是否存在
3. 重新下载：`python scripts/download_bge_reranker_base_modelscope.py`

### 5.2 推理超时

```json
{"action": "predict.timeout", "rerank_timeout": 3.0}
```

**处理**：
1. 检查 CPU 负载（`top`/`htop`）
2. 确认 ONNX 变体是 `model_quantized.onnx`（非 `model.onnx` FP32）
3. 临时调大 `SKILL_RERANKER_RERANK_TIMEOUT=5.0`

### 5.3 区分度丢失

```json
{"action": "rerank.completed", "score_stddev": 0.0}
```

**处理**：
1. 检查候选文本是否为空（`_candidate_to_text` 返回空字符串）
2. 确认候选是 dict 还是对象，reranker 已支持两者
3. 检查模型是否加载正确（`onnx_file` 应为 `model_quantized.onnx`）

### 5.4 降级到 RRF

```json
{"action": "rerank.fallback", "reason": "model_unavailable"}
```

**处理**：
1. 检查 `SKILL_RERANKER_ENABLED` 是否为 `true`
2. 检查模型加载日志（`onnx.load_failed`）
3. 确认 `SKILL_RERANKER_USE_ONNX=true` 且 ONNX 文件存在

---

## 六、回滚方案

### 6.1 回滚到 jina INT8（最快）

```ini
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx
```

- P99 258ms（最快）
- 但负样本拒识弱（case_042 误召回）

### 6.2 回滚到 FP32（高精度）

```ini
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--BAAI--bge-reranker-base
SKILL_RERANKER_ONNX_VARIANT=model.onnx
```

- P99 1080ms（未达 500ms SLO）
- 区分度与 INT8 一致，仅负样本拒识略优

### 6.3 完全禁用 Reranker

```ini
SKILL_RERANKER_ENABLED=false
```

- 降级到 RRF 原序
- 适用：紧急回滚、模型全部不可用

---

## 七、附录

### 7.1 性能基准（INT8 bge-base）

| 指标 | 值 | 达标 |
|------|-----|------|
| 单次 P50 | 412 ms | ✅ |
| 单次 P99 | 487 ms | ✅ < 500ms |
| 并发 P99 (4线程) | 1.21 s | ⚠️ |
| QPS | 3.86 | ✅ |
| 内存 | 1167 MB | ✅ < 1.5GB |
| 加载时间 | 3.24 s | ✅ |

### 7.2 相关文档

- [RERANKER_INT8_VS_FP32_SELECTION.md](./RERANKER_INT8_VS_FP32_SELECTION.md)：INT8 vs FP32 选型对比
- [RERANKER_BGE_V2_M3_INTEGRATION_TODO.md](./RERANKER_BGE_V2_M3_INTEGRATION_TODO.md)：集成计划
- [RERANKER_DISCRIMINATION_COMPARE_REPORT.json](./RERANKER_DISCRIMINATION_COMPARE_REPORT.json)：区分度对比数据
- [v65_bge_base_benchmark.json](./v65_bge_base_benchmark.json)：延迟 benchmark 数据

### 7.3 相关脚本

- [download_bge_reranker_base_modelscope.py](../scripts/download_bge_reranker_base_modelscope.py)：模型下载
- [convert_bge_to_onnx.py](../scripts/convert_bge_to_onnx.py)：INT8 量化转换
- [benchmark_v65_bge_base_reranker.py](../scripts/benchmark_v65_bge_base_reranker.py)：延迟 benchmark
- [compare_reranker_discrimination.py](../scripts/compare_reranker_discrimination.py)：区分度对比
- [eval_reranker_precision_compare.py](../scripts/eval_reranker_precision_compare.py)：精度评估
