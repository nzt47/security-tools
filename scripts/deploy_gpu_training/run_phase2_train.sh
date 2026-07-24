#!/bin/bash
# =====================================================================
# Phase 2 完整训练部署脚本 — GPU 服务器全量数据微调
#
# 【不易】不修改 finetune_reranker.py 核心逻辑,通过参数编排
# 【变易】5 步流程:环境校验 → 数据预处理 → 训练(支持续训)→ 结果报告 → 部署指引
# 【简易】单脚本完成,幂等可重跑,失败可从最后一个 checkpoint 恢复
#
# 用法:
#   bash run_phase2_train.sh                          # 完整流程
#   bash run_phase2_train.sh --resume                 # 从最后 checkpoint 恢复
#   bash run_phase2_train.sh --skip-preprocess        # 跳过数据预处理
#   bash run_phase2_train.sh --dry-run                # 只做环境校验
#   bash run_phase2_train.sh --balance                # 预处理时启用标签平衡
#   bash run_phase2_train.sh --no-lora                # 全量微调(禁用 LoRA)
#   bash run_phase2_train.sh --epochs 10 --batch-size 64
#   bash run_phase2_train.sh --monitor                # 启动训练监控
# =====================================================================
set -euo pipefail

# ---------- 参数解析 ----------
RESUME=false
SKIP_PREPROCESS=false
DRY_RUN=false
BALANCE=false
NO_LORA=false
MONITOR=false
EPOCHS_OVERRIDE=""
BATCH_SIZE_OVERRIDE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --resume)           RESUME=true; shift ;;
        --skip-preprocess)  SKIP_PREPROCESS=true; shift ;;
        --dry-run)          DRY_RUN=true; shift ;;
        --balance)          BALANCE=true; shift ;;
        --no-lora)          NO_LORA=true; shift ;;
        --monitor)          MONITOR=true; shift ;;
        --epochs)           EPOCHS_OVERRIDE="$2"; shift 2 ;;
        --batch-size)       BATCH_SIZE_OVERRIDE="$2"; shift 2 ;;
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

# ---------- Python 命令检测 ----------
# 【变易】WSL 中 python 可能不存在,优先 python3
PYTHON="${PYTHON:-$(command -v python3 || command -v python || echo '')}"
if [[ -z "${PYTHON}" ]]; then
    echo "✗ 未找到 python/python3,请先安装 Python 3.10+"
    exit 1
fi
PYTHON_VERSION=$(${PYTHON} --version 2>&1)
echo "  Python: ${PYTHON_VERSION} (${PYTHON})"

# ---------- 路径与默认配置 ----------
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${PROJECT_ROOT}"

TRAIN_FILE_RAW="data/reranker_trainset.jsonl"
VAL_FILE_RAW="data/reranker_valset.jsonl"
TRAIN_FILE="data/reranker_trainset_processed.jsonl"
VAL_FILE="data/reranker_valset_processed.jsonl"
OUTPUT_DIR="data/reranker_finetuned"
LOG_FILE="logs/phase2_train_$(date +%Y%m%d_%H%M%S).log"

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

mkdir -p logs "${OUTPUT_DIR}"

# ---------- 工具函数 ----------
log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
fail() { log "✗ $*"; exit 1; }

# ---------- 主流程 ----------
log "========================================"
log " Phase 2 完整训练部署脚本"
log " 项目根: ${PROJECT_ROOT}"
log " 日志文件: ${LOG_FILE}"
log " Resume: ${RESUME}, No-LoRA: ${NO_LORA}"
log "========================================"
log ""

# ---------- Step 1: 环境校验 ----------
log "[1/5] 环境校验..."

if ! command -v nvidia-smi &> /dev/null; then
    fail "未检测到 nvidia-smi,请在 GPU 服务器运行"
fi
GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
log "  GPU: ${GPU_INFO}"

${PYTHON} -c "
import torch, sys
print(f'  PyTorch: {torch.__version__}')
if not torch.cuda.is_available():
    sys.exit(1)
props = torch.cuda.get_device_properties(0)
mem_gb = props.total_memory / (1024**3)
print(f'  GPU[0]: {props.name} (显存 {mem_gb:.1f} GB)')
if mem_gb < 14:
    print(f'  ⚠ 显存 < 14GB,建议降低 batch_size')
" 2>&1 | tee -a "${LOG_FILE}" || fail "CUDA 不可用"

# 依赖检查
log "  依赖检查..."
${PYTHON} -c "
import peft, accelerate, sentence_transformers, transformers
print(f'  peft: {peft.__version__} ✅')
print(f'  accelerate: {accelerate.__version__} ✅')
print(f'  sentence-transformers: {sentence_transformers.__version__} ✅')
print(f'  transformers: {transformers.__version__} ✅')
" 2>&1 | tee -a "${LOG_FILE}" || fail "依赖缺失,运行 bash scripts/deploy_gpu_training/linux_setup.sh"

# ---------- Step 2: 数据预处理 ----------
if [[ "${SKIP_PREPROCESS}" == "false" ]]; then
    log ""
    log "[2/5] 数据预处理..."

    PREPROCESS_ARGS="--train ${TRAIN_FILE_RAW} --val ${VAL_FILE_RAW}"
    PREPROCESS_ARGS+=" --output-train ${TRAIN_FILE} --output-val ${VAL_FILE}"
    if [[ "${BALANCE}" == "true" ]]; then
        PREPROCESS_ARGS+=" --balance"
    fi

    if ! ${PYTHON} scripts/preprocess_trainset.py ${PREPROCESS_ARGS} 2>&1 | tee -a "${LOG_FILE}"; then
        fail "数据预处理失败"
    fi
    log "  ✅ 数据预处理完成"
else
    log ""
    log "[2/5] 跳过数据预处理(--skip-preprocess)"
    TRAIN_FILE="${TRAIN_FILE_RAW}"
    VAL_FILE="${VAL_FILE_RAW}"
fi

# ---------- Step 3: dry-run 退出 ----------
if [[ "${DRY_RUN}" == "true" ]]; then
    log ""
    log "✅ --dry-run 模式:环境校验 + 数据预处理通过,不执行训练"
    exit 0
fi

# ---------- Step 4: 训练 ----------
log ""
log "[3/5] 启动训练..."
log "  训练集: ${TRAIN_FILE}"
log "  验证集: ${VAL_FILE}"
log "  输出目录: ${OUTPUT_DIR}"
log "  Epochs: ${EPOCHS}, Batch size: ${BATCH_SIZE}"
log "  LoRA: rank=${DEFAULT_LORA_RANK}, alpha=${DEFAULT_LORA_ALPHA} (No-LoRA: ${NO_LORA})"
log "  Resume: ${RESUME}"

# 设置环境变量
export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HUB_DISABLE_XET=1
export HF_XET_HIGH_PERFORMANCE=0
export ANONYMIZED_TELEMETRY=False
export PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128

# 构建训练命令
TRAIN_CMD="${PYTHON} scripts/finetune_reranker.py \
    --train ${TRAIN_FILE} \
    --val ${VAL_FILE} \
    --output ${OUTPUT_DIR} \
    --base-model BAAI/bge-reranker-v2-m3 \
    --max-length ${DEFAULT_MAX_LENGTH} \
    --epochs ${EPOCHS} \
    --batch-size ${BATCH_SIZE} \
    --lr ${DEFAULT_LR} \
    --lora-rank ${DEFAULT_LORA_RANK} \
    --lora-alpha ${DEFAULT_LORA_ALPHA} \
    --early-stopping-patience ${DEFAULT_PATIENCE} \
    --optimizer ${DEFAULT_OPTIMIZER}"

if [[ "${NO_LORA}" == "true" ]]; then
    TRAIN_CMD+=" --no-lora"
fi
if [[ "${RESUME}" == "true" ]]; then
    TRAIN_CMD+=" --resume"
fi

log "  训练命令: ${TRAIN_CMD}"
log "  ---"

# 可选:启动监控
if [[ "${MONITOR}" == "true" ]] && [[ -f "scripts/deploy_gpu_training/monitor_train.py" ]]; then
    log "  启动训练监控(后台)..."
    ${PYTHON} scripts/deploy_gpu_training/monitor_train.py --output "${OUTPUT_DIR}" &
    MONITOR_PID=$!
    log "  监控 PID: ${MONITOR_PID}"
fi

TRAIN_START=$(date +%s)
if ! ${TRAIN_CMD} 2>&1 | tee -a "${LOG_FILE}"; then
    TRAIN_END=$(date +%s)
    ELAPSED=$((TRAIN_END - TRAIN_START))
    log ""
    log "✗ 训练失败 (耗时 ${ELAPSED}s)"
    log "  可使用 --resume 从最后 checkpoint 恢复:"
    log "  bash scripts/deploy_gpu_training/run_phase2_train.sh --resume --skip-preprocess"
    exit 1
fi
TRAIN_END=$(date +%s)
ELAPSED=$((TRAIN_END - TRAIN_START))
log ""
log "  训练总耗时: ${ELAPSED}s ($((ELAPSED/60))min $((ELAPSED%60))s)"

# 停止监控
if [[ "${MONITOR}" == "true" ]] && [[ -n "${MONITOR_PID:-}" ]]; then
    kill ${MONITOR_PID} 2>/dev/null || true
fi

# ---------- Step 5: 训练结果报告 ----------
log ""
log "[4/5] 训练结果报告..."

META_FILE="${OUTPUT_DIR}/training_meta.json"
if [[ -f "${META_FILE}" ]]; then
    log "  --- training_meta.json ---"
    cat "${META_FILE}" | tee -a "${LOG_FILE}"
    log ""
fi

# checkpoint 目录文件清单 + 大小验证
CKPT_DIR="${OUTPUT_DIR}/checkpoints"
if [[ -d "${CKPT_DIR}" ]]; then
    CKPT_COUNT=$(find "${CKPT_DIR}" -maxdepth 1 -type d -name "epoch_*" | wc -l)
    log "  Checkpoint 数量: ${CKPT_COUNT}"

    # 验证 checkpoint 大小(LoRA adapter 应 < 50MB,完整模型 ~2.2GB)
    LATEST_CKPT=$(ls -1d "${CKPT_DIR}"/epoch_* 2>/dev/null | sort -t_ -k2 -n | tail -1)
    if [[ -n "${LATEST_CKPT}" ]]; then
        log "  最新 checkpoint: ${LATEST_CKPT}"
        log "  文件清单:"
        ls -lh "${LATEST_CKPT}/" 2>/dev/null | tee -a "${LOG_FILE}"

        # 验证 adapter 文件存在(非 model.safetensors)
        ADAPTER_FILE="${LATEST_CKPT}/adapter_model.safetensors"
        ADAPTER_BIN="${LATEST_CKPT}/adapter_model.bin"
        ADAPTER_PT="${LATEST_CKPT}/adapter_model.pt"
        if [[ -f "${ADAPTER_FILE}" ]] || [[ -f "${ADAPTER_BIN}" ]] || [[ -f "${ADAPTER_PT}" ]]; then
            log "  ✅ LoRA adapter 文件存在(断点续训可用)"
        else
            log "  ⚠ 未找到 adapter 文件,断点续训可能失败"
        fi

        # 检查是否有误存的完整模型文件
        FULL_MODEL="${LATEST_CKPT}/model.safetensors"
        if [[ -f "${FULL_MODEL}" ]]; then
            MODEL_SIZE=$(du -h "${FULL_MODEL}" | cut -f1)
            log "  ⚠ 发现完整模型文件: model.safetensors (${MODEL_SIZE})"
            log "     这可能是 PEFT 误存的 base model,可安全删除以节省磁盘:"
            log "     rm ${FULL_MODEL}"
        fi
    fi
fi

# ---------- Step 6: 部署指引 ----------
log ""
log "[5/5] 部署指引..."
log ""
log "  1. 评估模型(zero-shot):"
log "     ${PYTHON} scripts/verify_phase1_e2e.py --verbose"
log ""
log "  2. 部署到生产环境:"
log "     # 将 ${OUTPUT_DIR} 复制到 agent 服务器"
log "     # 设置 .env: AGENT_RERANKER_MODEL=${OUTPUT_DIR}"
log "     # 设置 .env: AGENT_HYBRID_RERANKER=1"
log ""
log "  3. 从 checkpoint 恢复训练(如需继续训练):"
log "     bash scripts/deploy_gpu_training/run_phase2_train.sh --resume --skip-preprocess"
log ""
log "  4. 全量微调(禁用 LoRA):"
log "     bash scripts/deploy_gpu_training/run_phase2_train.sh --no-lora"
log ""
log "  完整日志: ${LOG_FILE}"
log ""
log "========================================"
log " ✅ Phase 2 训练部署完成"
log "========================================"
