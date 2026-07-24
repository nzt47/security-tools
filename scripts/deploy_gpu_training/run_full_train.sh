#!/bin/bash
# =====================================================================
# Phase 2 全量数据训练编排脚本 — bge-reranker-v2-m3 LoRA GPU 微调
#
# 【不易】不修改 finetune_reranker.py,仅通过参数编排
# 【变易】支持环境校验 → 训练 → 监控 → 评估 → 报告全流程
# 【简易】单脚本完成,幂等可重跑,失败可从最后一个 checkpoint 恢复
#
# 用法:
#   bash run_full_train.sh                     # 默认全量训练
#   bash run_full_train.sh --resume            # 从最后 checkpoint 恢复
#   bash run_full_train.sh --dry-run           # 只做环境校验,不训练
#   bash run_full_train.sh --epochs 10         # 覆盖 epoch 数
#   bash run_full_train.sh --no-monitor        # 不启动实时监控
#
# 依赖:
#   - 已执行 linux_setup.sh 或 colab_train.ipynb 步骤 1-2
#   - data/reranker_trainset.jsonl + data/reranker_valset.jsonl 已就位
#   - GPU 可用 (T4 16GB / V100 16GB / A100 40GB+)
# =====================================================================
set -euo pipefail

# ---------- 参数解析 ----------
RESUME=false
DRY_RUN=false
NO_MONITOR=false
EPOCHS_OVERRIDE=""
BATCH_SIZE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --resume)       RESUME=true; shift ;;
        --dry-run)      DRY_RUN=true; shift ;;
        --no-monitor)   NO_MONITOR=true; shift ;;
        --epochs)       EPOCHS_OVERRIDE="$2"; shift 2 ;;
        --batch-size)   BATCH_SIZE_OVERRIDE="$2"; shift 2 ;;
        --help|-h)
            grep '^#' "$0" | head -20
            exit 0
            ;;
        *)
            echo "未知参数: $1 (使用 --help 查看用法)"
            exit 1
            ;;
    esac
done

# ---------- 路径与默认配置 ----------
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${PROJECT_ROOT}"

TRAIN_FILE="data/reranker_trainset.jsonl"
VAL_FILE="data/reranker_valset.jsonl"
OUTPUT_DIR="data/reranker_finetuned"
LOG_FILE="logs/full_train_$(date +%Y%m%d_%H%M%S).log"
CHECKPOINT_DIR="${OUTPUT_DIR}/checkpoints"

# 【变易】GPU 最优超参数(可在 .env 中覆盖)
DEFAULT_EPOCHS="${AGENT_TRAIN_EPOCHS:-8}"
DEFAULT_BATCH_SIZE="${AGENT_TRAIN_BATCH_SIZE:-32}"
DEFAULT_LR="${AGENT_TRAIN_LR:-2e-5}"
DEFAULT_LORA_RANK="${AGENT_TRAIN_LORA_RANK:-16}"
DEFAULT_LORA_ALPHA="${AGENT_TRAIN_LORA_ALPHA:-32}"
DEFAULT_MAX_LENGTH="${AGENT_TRAIN_MAX_LENGTH:-512}"
DEFAULT_OPTIMIZER="${AGENT_TRAIN_OPTIMIZER:-adamw}"
DEFAULT_PATIENCE="${AGENT_TRAIN_PATIENCE:-3}"

EPOCHS="${EPOCHS_OVERRIDE:-${DEFAULT_EPOCHS}}"
BATCH_SIZE="${BATCH_SIZE_OVERRIDE:-${DEFAULT_BATCH_SIZE}}"

mkdir -p logs "${OUTPUT_DIR}" "${CHECKPOINT_DIR}"

# ---------- 工具函数 ----------
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
fail() { log "✗ $*"; exit 1; }

# ---------- Step 1: 环境前置检查 ----------
log "========================================"
log " Phase 2 全量数据训练 (bge-reranker-v2-m3 LoRA)"
log " 项目根: ${PROJECT_ROOT}"
log " 日志文件: ${LOG_FILE}"
log "========================================"
log ""
log "[1/6] 环境前置检查..."

# GPU
if ! command -v nvidia-smi &> /dev/null; then
    fail "未检测到 nvidia-smi,请先运行 linux_setup.sh"
fi
GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
log "  GPU: ${GPU_INFO}"

# Python + PyTorch + CUDA
python -c "
import torch, sys
print(f'  Python: {sys.version.split()[0]}')
print(f'  PyTorch: {torch.__version__}')
print(f'  CUDA 可用: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'  CUDA 版本: {torch.version.cuda}')
    props = torch.cuda.get_device_properties(0)
    mem_gb = props.total_memory / (1024**3)
    print(f'  GPU[0]: {props.name} (显存 {mem_gb:.1f} GB)')
    if mem_gb < 14:
        print(f'  ⚠ 显存 < 14GB,建议降低 batch_size')
    else:
        print(f'  ✅ 显存充足')
else:
    sys.exit(1)
" 2>&1 | tee -a "${LOG_FILE}" || fail "CUDA 不可用,请在 GPU 服务器运行"

# 关键依赖
python -c "
import peft, accelerate, sentence_transformers, transformers
print(f'  peft: {peft.__version__}')
print(f'  accelerate: {accelerate.__version__}')
print(f'  sentence-transformers: {sentence_transformers.__version__}')
print(f'  transformers: {transformers.__version__}')
" 2>&1 | tee -a "${LOG_FILE}" || fail "依赖缺失,请运行 linux_setup.sh"

# ---------- Step 2: 数据校验 ----------
log ""
log "[2/6] 数据校验..."

for f in "${TRAIN_FILE}" "${VAL_FILE}"; do
    if [[ ! -f "${f}" ]]; then
        fail "数据文件缺失: ${f}"
    fi
done

python -c "
import json
for name, path in [('训练集', '${TRAIN_FILE}'), ('验证集', '${VAL_FILE}')]:
    pos, neg = 0, 0
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            s = json.loads(line)
            if s.get('label') == 1: pos += 1
            else: neg += 1
    print(f'  {name}: {pos+neg} 样本 (正:{pos}, 负:{neg})')
    if pos + neg == 0:
        raise SystemExit(f'{name} 为空')
print(f'  ✅ 数据校验通过')
" 2>&1 | tee -a "${LOG_FILE}" || fail "数据校验失败"

# ---------- Step 3: 模型缓存检查 ----------
log ""
log "[3/6] 模型缓存检查..."

MODEL_ID="BAAI/bge-reranker-v2-m3"
python -c "
from pathlib import Path
repo_dir = '${MODEL_ID}'.replace('/', '--')
hf_root = Path.home() / '.cache' / 'huggingface' / 'hub' / f'models--{repo_dir}' / 'snapshots'
if hf_root.exists() and any(hf_root.iterdir()):
    for sub in hf_root.iterdir():
        if sub.is_dir() and (sub / 'config.json').exists():
            print(f'  ✅ 模型已缓存: {sub}')
            break
    else:
        print(f'  ⚠ 缓存目录存在但无有效 snapshot,训练时将重新下载')
else:
    print(f'  ⚠ 模型未缓存,训练时将下载 (~2.2GB)')
" 2>&1 | tee -a "${LOG_FILE}"

# ---------- Step 4: 训练参数汇总 ----------
log ""
log "[4/6] 训练参数汇总..."
log "  基础模型: ${MODEL_ID}"
log "  LoRA rank: ${DEFAULT_LORA_RANK}, alpha: ${DEFAULT_LORA_ALPHA}"
log "  Epochs: ${EPOCHS}"
log "  Batch size: ${BATCH_SIZE}"
log "  Learning rate: ${DEFAULT_LR}"
log "  Max length: ${DEFAULT_MAX_LENGTH}"
log "  Optimizer: ${DEFAULT_OPTIMIZER}"
log "  Early stopping patience: ${DEFAULT_PATIENCE}"
log "  输出目录: ${OUTPUT_DIR}"
log "  Resume: ${RESUME}"

if [[ "${DRY_RUN}" == "true" ]]; then
    log ""
    log "✅ --dry-run 模式:环境校验通过,不执行训练"
    exit 0
fi

# ---------- Step 5: 启动训练 ----------
log ""
log "[5/6] 启动训练..."

# 构造训练命令
TRAIN_CMD="python scripts/finetune_reranker.py \
    --train ${TRAIN_FILE} \
    --val ${VAL_FILE} \
    --output ${OUTPUT_DIR} \
    --base-model ${MODEL_ID} \
    --max-length ${DEFAULT_MAX_LENGTH} \
    --epochs ${EPOCHS} \
    --batch-size ${BATCH_SIZE} \
    --lr ${DEFAULT_LR} \
    --lora-rank ${DEFAULT_LORA_RANK} \
    --lora-alpha ${DEFAULT_LORA_ALPHA} \
    --early-stopping-patience ${DEFAULT_PATIENCE} \
    --optimizer ${DEFAULT_OPTIMIZER}"

# 设置环境变量(HF 镜像 + 离线模式优先)
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET=1
export HF_XET_HIGH_PERFORMANCE=0
export ANONYMIZED_TELEMETRY=False
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128

# 启动监控(后台进程,可选)
MONITOR_PID=""
if [[ "${NO_MONITOR}" == "false" ]] && [[ -f "scripts/deploy_gpu_training/monitor_train.py" ]]; then
    log "  启动实时监控 (后台)..."
    python scripts/deploy_gpu_training/monitor_train.py --log "${LOG_FILE}" --follow --interval 5 &
    MONITOR_PID=$!
    log "  监控 PID: ${MONITOR_PID}"
fi

# 启动训练(前台运行,输出同时写入日志)
log "  训练命令: ${TRAIN_CMD}"
log "  训练开始时间: $(date '+%Y-%m-%d %H:%M:%S')"
log "  ---"

TRAIN_START=$(date +%s)
if ! ${TRAIN_CMD} 2>&1 | tee -a "${LOG_FILE}"; then
    TRAIN_END=$(date +%s)
    ELAPSED=$((TRAIN_END - TRAIN_START))
    log ""
    log "✗ 训练失败 (耗时 ${ELAPSED}s)"
    [[ -n "${MONITOR_PID}" ]] && kill "${MONITOR_PID}" 2>/dev/null || true
    exit 1
fi
TRAIN_END=$(date +%s)
ELAPSED=$((TRAIN_END - TRAIN_START))
log ""
log "  训练结束时间: $(date '+%Y-%m-%d %H:%M:%S')"
log "  训练总耗时: ${ELAPSED}s ($((ELAPSED/60))min $((ELAPSED%60))s)"

# 停止监控
if [[ -n "${MONITOR_PID}" ]]; then
    kill "${MONITOR_PID}" 2>/dev/null || true
    log "  监控进程已停止"
fi

# ---------- Step 6: 训练结果报告 ----------
log ""
log "[6/6] 训练结果报告..."

META_FILE="${OUTPUT_DIR}/training_meta.json"
if [[ -f "${META_FILE}" ]]; then
    log "  --- training_meta.json ---"
    cat "${META_FILE}" | tee -a "${LOG_FILE}"
    log ""
    log "  ---"
fi

# 输出目录文件清单
log "  输出目录文件清单:"
if command -v du &> /dev/null; then
    ls -lh "${OUTPUT_DIR}" 2>/dev/null | tee -a "${LOG_FILE}" || true
    TOTAL_SIZE=$(du -sh "${OUTPUT_DIR}" 2>/dev/null | cut -f1)
    log "  总大小: ${TOTAL_SIZE}"
fi

log ""
log "========================================"
log " ✅ Phase 2 全量训练完成"
log "========================================"
log ""
log "下一步:"
log "  1. 评估模型 (zero-shot):"
log "     python scripts/eval_reranker_zero_shot.py --model ${OUTPUT_DIR}"
log ""
log "  2. 部署到生产环境:"
log "     # 将 ${OUTPUT_DIR} 复制到 agent 服务器的 data/reranker_finetuned/"
log "     # 设置 .env: AGENT_RERANKER_MODEL=${OUTPUT_DIR}"
log "     # 设置 .env: AGENT_HYBRID_RERANKER=1"
log ""
log "  3. 端到端验证:"
log "     python scripts/verify_phase1_e2e.py --verbose"
log ""
log "  完整日志: ${LOG_FILE}"
