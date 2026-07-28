# v6.5 ONNX Reranker 生产部署操作手册

**版本**: v1.0
**生效日期**: 2026-07-28
**适用环境**: CPU 生产环境（Windows/Linux）
**SLO**: P99 ≤ 500ms，QPS ≥ 3，内存 RSS ≤ 1.5GB

---

## 0. 部署前检查清单

| 检查项 | 命令 | 期望输出 | 通过 |
|--------|------|---------|------|
| Python 版本 | `python --version` | 3.10+ | ☐ |
| onnxruntime 已装 | `python -c "import onnxruntime; print(onnxruntime.__version__)"` | 1.20.1+ | ☐ |
| transformers 已装 | `python -c "import transformers; print(transformers.__version__)"` | 4.x / 5.x | ☐ |
| psutil 已装（监控用） | `python -c "import psutil; print(psutil.__version__)"` | 5.x+ | ☐ |
| 磁盘空间 ≥ 1GB | `Get-PSDrive C` (Win) / `df -h /` (Linux) | Free ≥ 1GB | ☐ |
| 内存可用 ≥ 2GB | 任务管理器 / `free -h` | Available ≥ 2GB | ☐ |

---

## 1. 模型文件迁移

### 1.1 模型文件清单

```
~/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual/
├── config.json                          # 模型配置
├── tokenizer.json                       # tokenizer
├── tokenizer_config.json
├── special_tokens_map.json
├── model.safetensors                    # PyTorch 权重（降级用）
├── pytorch_model.bin                    # PyTorch 权重（备用）
├── modeling_xlm_roberta.py              # 自定义模型代码
├── configuration_xlm_roberta.py         # 自定义配置代码
├── embedding.py                         # embedding 实现
├── block.py / mha.py / mlp.py           # 模型组件
├── xlm_padding.py                       # padding 逻辑
├── stochastic_depth.py
└── onnx/                                # ⭐ ONNX 预导出格式（生产用）
    ├── model_quantized.onnx             # ⭐ 生产推荐 (266MB, P99 258ms)
    ├── model_int8.onnx                  # 备选 (266MB, P99 363ms)
    ├── model_uint8.onnx                 # 备选 (266MB, P99 439ms)
    ├── model_q4.onnx                    # 备选 (789MB, P99 302ms)
    ├── model_bnb4.onnx                  # 备选 (784MB, P99 465ms)
    ├── model_fp16.onnx                  # ❌ 不推荐 (531MB, P99 622ms)
    └── model.onnx                       # ❌ 不推荐 (1062MB, P99 505ms)
```

### 1.2 迁移步骤

#### 场景 A：新机器首次部署

```powershell
# 1. 创建模型目录
$modelDir = "$env:USERPROFILE\.cache\huggingface\hub\models--jinaai--jina-reranker-v2-base-multilingual"
New-Item -ItemType Directory -Path $modelDir -Force

# 2. 通过 modelscope 镜像下载（避免 huggingface.co 网络问题）
python scripts/download_jina_reranker_modelscope.py

# 3. 验证 ONNX 子目录存在
Get-ChildItem "$modelDir\onnx" -Name
# 期望输出：model_bnb4.onnx, model_fp16.onnx, model_int8.onnx, model_q4.onnx,
#           model_quantized.onnx, model_uint8.onnx, model.onnx

# 4. 验证 model_quantized.onnx 大小（应 ~266MB）
(Get-Item "$modelDir\onnx\model_quantized.onnx").Length / 1MB
```

```bash
# Linux 等价命令
modelDir="$HOME/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
mkdir -p "$modelDir"
python scripts/download_jina_reranker_modelscope.py
ls -la "$modelDir/onnx/"
du -h "$modelDir/onnx/model_quantized.onnx"
```

#### 场景 B：从已有 PyTorch 部署迁移

```powershell
# 1. 确认已有模型目录（PyTorch 部署时已下载）
$modelDir = "$env:USERPROFILE\.cache\huggingface\hub\models--jinaai--jina-reranker-v2-base-multilingual"

# 2. 检查 onnx/ 子目录是否存在
if (Test-Path "$modelDir\onnx\model_quantized.onnx") {
    Write-Host "✅ ONNX 模型已存在，无需迁移"
} else {
    Write-Host "⚠️ ONNX 模型缺失，需重新下载"
    python scripts/download_jina_reranker_modelscope.py
}

# 3. 备份当前 .env
Copy-Item .env .env.backup.$(Get-Date -Format "yyyyMMddHHmmss")
```

#### 场景 C：离线环境迁移（无网络）

```powershell
# 1. 在有网机器上打包模型目录
$srcDir = "$env:USERPROFILE\.cache\huggingface\hub\models--jinaai--jina-reranker-v2-base-multilingual"
Compress-Archive -Path $srcDir -DestinationPath "jina-reranker-v2.zip" -CompressionLevel Optimal

# 2. 传输到离线机器（U盘/内网传输）

# 3. 在离线机器上解压
$dstDir = "$env:USERPROFILE\.cache\huggingface\hub\models--jinaai--jina-reranker-v2-base-multilingual"
Expand-Archive -Path "jina-reranker-v2.zip" -DestinationPath (Split-Path $dstDir -Parent) -Force

# 4. 验证
Get-ChildItem "$dstDir\onnx" -Name
```

### 1.3 模型完整性校验

```powershell
# 运行冒烟测试验证 ONNX 推理可用
python scripts/smoke_test_onnx_integration.py

# 期望输出末尾：
# 综合结果: ✅ ONNX 集成验证通过
```

---

## 2. 环境变量配置

### 2.1 .env 配置项

编辑 `.env` 文件，定位到 `v6.5 Skill Reranker 配置` 区域：

```bash
# ════════════════════════════════════════════════════════════
# v6.5 Skill Reranker 配置（agent/skills_mgmt/reranker.py）
# ════════════════════════════════════════════════════════════
# 启用开关（true/false，默认 true）
SKILL_RERANKER_ENABLED=true

# Cross-Encoder 模型名（本地路径，必须指向含 onnx/ 子目录的模型目录）
SKILL_RERANKER_MODEL=C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual

# 子进程超时秒数（仅 PyTorch 路径用；ONNX 路径 P99 258ms 远低于此值）
SKILL_RERANKER_TIMEOUT=30

# 最低分数阈值，低于此值的候选剔除（默认 0.001）
SKILL_RERANKER_MIN_SCORE=0.001

# ⭐ ONNX 推理开关（true/false，默认 true）
# 启用后优先加载 <model_dir>/onnx/<variant>，失败降级到 PyTorch
SKILL_RERANKER_USE_ONNX=true

# ⭐ ONNX 变体文件名（默认 model_quantized.onnx，P99 258ms 最优）
SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx

# 离线模式（避免 huggingface.co 网络请求）
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
```

### 2.2 配置项详解

| 变量 | 默认值 | 说明 | 何时修改 |
|------|--------|------|---------|
| `SKILL_RERANKER_ENABLED` | `true` | 总开关 | 排查问题时设为 `false` 完全禁用 |
| `SKILL_RERANKER_MODEL` | jina 本地路径 | 模型目录 | 切换其他 reranker 模型时 |
| `SKILL_RERANKER_TIMEOUT` | `30` | PyTorch 子进程超时 | ONNX 路径不使用此参数 |
| `SKILL_RERANKER_MIN_SCORE` | `0.001` | 最低分数阈值 | 召回过宽时提高到 `0.05` |
| `SKILL_RERANKER_USE_ONNX` | `true` | ONNX 开关 | A/B 对比时设为 `false` 走 PyTorch |
| `SKILL_RERANKER_ONNX_VARIANT` | `model_quantized.onnx` | ONNX 变体 | 切换其他量化格式时 |
| `HF_HUB_OFFLINE` | `1` | 离线模式 | 在线更新模型时设为 `0` |

### 2.3 配置验证

```powershell
# 1. 重新加载 .env（重启服务或新开终端）

# 2. 验证 SkillReranker 实际读取的配置
python -c "
import os, sys
sys.path.insert(0, '.')
# 手动加载 .env
with open('.env', encoding='utf-8') as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            os.environ[k.strip()] = v.strip()

from agent.skills_mgmt.reranker import SkillReranker
r = SkillReranker()
print(f'model: {r._model_name}')
print(f'use_onnx_env: {r._use_onnx_env}')
print(f'onnx_variant: {r._onnx_variant}')
print(f'min_score: {r._min_score}')
print(f'enabled: {r._is_enabled()}')
"
```

期望输出：
```
model: C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual
use_onnx_env: True
onnx_variant: model_quantized.onnx
min_score: 0.001
enabled: True
```

---

## 3. 启动与验证

### 3.1 启动前自检

```powershell
# 1. 运行单元测试（应 72 passed）
$env:PYTHONIOENCODING='utf-8'; $env:SKILLS_OFFLINE='1'
python -m pytest tests/unit/test_reranker.py tests/unit/test_reranker_onnx.py -v --tb=short

# 2. 运行冒烟测试（真实模型）
python scripts/smoke_test_onnx_integration.py

# 3. 运行 ONNX 7 变体压测（验证选最优）
python scripts/benchmark_v65_onnx_reranker.py
```

### 3.2 启动后验证

```powershell
# 1. 检查日志中 ONNX 加载成功
Get-Content logs\digital_life.log -Tail 100 | Select-String "onnx.loaded"

# 期望看到类似：
# {"module_name": "reranker", "action": "onnx.loaded", "model": "...", "onnx_file": "model_quantized.onnx", "load_time_s": 3.12}

# 2. 检查 loader.py 正确实例化（P0 bug 已修复验证）
Get-Content logs\digital_life.log -Tail 100 | Select-String "reranker.init"

# 期望看到：
# {"module_name": "loader", "action": "reranker.init", "model": "...", "use_onnx": true, ...}

# 3. 检查无降级日志
Get-Content logs\digital_life.log -Tail 100 | Select-String "onnx.load_failed|pytorch.loaded|rerank.fallback"

# 如果出现 onnx.load_failed → 检查模型路径
# 如果出现 pytorch.loaded → ONNX 降级到 PyTorch（检查 ONNX 文件是否存在）
# 如果出现 rerank.fallback → 模型完全不可用（检查 .env 配置）
```

### 3.3 性能验证

```powershell
# 运行长稳压测（1000 次，~4.5 分钟）
python scripts/benchmark_v65_onnx_long_stability.py

# 期望结果：
# SLO (P99 ≤ 500ms): ✅ 通过
# 内存稳定: ✅ 通过
# 排序正确: ✅ 通过
# 综合结论: ✅ 全部通过，可投入生产
```

---

## 4. 回滚预案

### 4.1 回滚决策树

```
生产故障？
├─ ONNX 推理慢（P99 > 500ms）
│   ├─ 突增 → 检查并发/资源竞争 → 1 分钟内自愈则观察
│   └─ 持续 → 切换 ONNX 变体（4.2.1）或降级 PyTorch（4.2.2）
├─ ONNX 加载失败
│   ├─ 模型文件缺失 → 重新下载（1.1）
│   └─ 持续 → 降级 PyTorch（4.2.2）
├─ 排序质量下降
│   ├─ 个别 case → 观察并记录
│   └─ 整体退化 → 降级 PyTorch（4.2.2）或 RRF（4.2.3）
└─ 服务完全不可用
    └─ 紧急降级 RRF（4.2.3）
```

### 4.2 回滚操作

#### 4.2.1 切换 ONNX 变体（最轻量，30s 内完成）

适用场景：`model_quantized.onnx` 性能突增或精度异常

```bash
# 编辑 .env，更换变体（按 P99 升序备选）
# 备选优先级：model_q4.onnx (302ms) > model_int8.onnx (363ms) > model_uint8.onnx (439ms)
SKILL_RERANKER_ONNX_VARIANT=model_q4.onnx

# 重启服务
# 验证
python scripts/smoke_test_onnx_integration.py
```

#### 4.2.2 降级到 PyTorch（中等，1-3 分钟）

适用场景：ONNX 完全不可用，但 PyTorch 路径可接受（P99 7960ms，仅应急）

```bash
# 编辑 .env
SKILL_RERANKER_USE_ONNX=false

# 同时调高超时（PyTorch 慢）
SKILL_RERANKER_TIMEOUT=60

# 重启服务
# 注意：PyTorch 路径 P99 7960ms，不满足 500ms SLO，仅作为应急降级
# 需尽快排查 ONNX 故障并恢复
```

#### 4.2.3 紧急降级到 RRF（最快，<10s）

适用场景：模型完全不可用，需立即恢复服务

```bash
# 编辑 .env
SKILL_RERANKER_ENABLED=false

# 重启服务
# RRF 降级后 rerank() 直接返回原序（sub-ms），主流程降级到 RRF 排序
# 精度损失：失去精排能力，P@3 预期下降 ~18.5%
```

#### 4.2.4 完全回滚到部署前状态

适用场景：ONNX 集成引入未知问题

```powershell
# 1. 恢复 .env 备份
Copy-Item .env.backup.* .env -Force

# 2. 恢复代码（git revert）
git log --oneline -10  # 找到 ONNX 集成 commit
git revert <commit-hash>

# 3. 重启服务
# 4. 验证
python scripts/smoke_test_onnx_integration.py  # 应跳过或失败（已回滚）
python -m pytest tests/unit/test_reranker.py -v  # 既有测试应通过
```

### 4.3 回滚后行动

| 回滚级别 | 后续行动 | 时限 |
|---------|---------|------|
| 4.2.1 切换变体 | 排查原变体故障原因 | 24h 内 |
| 4.2.2 降级 PyTorch | 排查 ONNX 加载失败 | 4h 内恢复 |
| 4.2.3 降级 RRF | 排查模型/配置问题 | 1h 内恢复 |
| 4.2.4 完全回滚 | 定位根因，重新规划部署 | 立即 |

---

## 5. 运维巡检

### 5.1 日常巡检（每日）

```powershell
# 1. 检查 Reranker 健康状态
Get-Content logs\digital_life.log -Tail 1000 | Select-String "rerank.completed" | Select-Object -Last 5

# 2. 检查是否有降级事件
Get-Content logs\digital_life.log -Tail 1000 | Select-String "rerank.fallback|onnx.load_failed|pytorch.load_failed"

# 3. 检查模型加载状态
Get-Content logs\digital_life.log -Tail 1000 | Select-String "onnx.loaded|pytorch.loaded"
```

### 5.2 周度压测

```powershell
# 每周运行一次长稳压测，对比基线
python scripts/benchmark_v65_onnx_long_stability.py

# 关注指标：
# - P99 是否 < 500ms（基线 427ms）
# - RSS 增量是否 < 50MB（基线 -0.01MB）
# - 排序正确性 5/5
```

### 5.3 关键指标基线

| 指标 | 部署基线 | 告警阈值 | 紧急阈值 |
|------|---------|---------|---------|
| P99 延迟 | 258-428ms | > 500ms | > 1000ms |
| QPS | 3.81-4.15 | < 3 | < 1 |
| RSS 内存 | 1156MB | > 1500MB | > 2000MB |
| RSS 增量（1h） | ~0MB | > 50MB | > 200MB |
| 加载耗时 | 3.12s | > 10s | > 30s |
| 排序正确性 | 100% | < 100% | < 90% |

---

## 6. 附录

### 6.1 文件清单

| 文件 | 用途 |
|------|------|
| `agent/skills_mgmt/reranker.py` | SkillReranker 主类（ONNX + PyTorch 双路径） |
| `agent/skills_mgmt/loader.py` | SkillLoader（调用 SkillReranker 的入口） |
| `.env` | 生产配置（含 ONNX 开关） |
| `.env.example` | 配置模板 |
| `scripts/download_jina_reranker_modelscope.py` | 模型下载脚本 |
| `scripts/benchmark_v65_onnx_reranker.py` | 7 变体压测脚本 |
| `scripts/benchmark_v65_onnx_long_stability.py` | 1000 次长稳压测脚本 |
| `scripts/smoke_test_onnx_integration.py` | 端到端冒烟测试 |
| `tests/unit/test_reranker.py` | 既有单元测试（33 个） |
| `tests/unit/test_reranker_onnx.py` | ONNX 单元测试（39 个） |
| `docs/V65_ONNX_QUANTIZATION_PLAN.md` | ONNX 量化方案设计文档 |
| `docs/V65_RERANKER_COMPARISON_REPORT.md` | 方案对比报告 |

### 6.2 故障排查 Cheat Sheet

| 症状 | 可能原因 | 排查命令 |
|------|---------|---------|
| `onnx.load_failed` 日志 | 模型路径错误 / 文件损坏 | 检查 `SKILL_RERANKER_MODEL` 路径 + `onnx/` 子目录 |
| `pytorch.loaded` 日志（非预期） | ONNX 文件缺失 | 检查 `model_quantized.onnx` 是否存在 |
| `rerank.fallback` 日志 | 模型完全不可用 | 检查 `.env` 配置 + 模型文件完整性 |
| P99 突增 > 500ms | 并发竞争 / 资源不足 | 检查 CPU/内存占用，考虑切换变体 |
| RSS 持续增长 | 内存泄漏 | 重启服务，运行长稳压测验证 |
| 排序错误 | 量化精度损失 | 切换 `model_q4.onnx` 或 `model.onnx`（FP32） |

### 6.3 联系方式

- 紧急回滚：参照 §4
- 故障排查：参照 §6.2
- 性能问题：运行 §5.2 周度压测并对比基线
- 模型更新：参照 §1.1 重新下载
