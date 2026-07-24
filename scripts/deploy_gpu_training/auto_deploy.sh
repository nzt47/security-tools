#!/bin/bash
# =====================================================================
# 生产环境 GPU 服务器自动化部署脚本 — 三阶段编排
#
# 【不易】不修改 finetune_reranker.py / run_phase2_train.sh 核心逻辑,仅做编排
# 【变易】三阶段独立可重跑:依赖安装 → 数据预热 → 断点续训启动
# 【简易】单脚本完成,幂等可重跑,失败可从任意阶段恢复
#
# 用法:
#   bash auto_deploy.sh                          # 完整三阶段
#   bash auto_deploy.sh --skip-setup             # 跳过依赖安装
#   bash auto_deploy.sh --skip-preheat           # 跳过数据预热
#   bash auto_deploy.sh --stage train            # 仅执行训练阶段
#   bash auto_deploy.sh --cuda 11.8              # 指定 CUDA 版本
#   bash auto_deploy.sh --env reranker_prod      # 指定 conda 环境名
#   bash auto_deploy.sh --dry-run                # 只做检查,不执行
#   bash auto_deploy.sh --help                   # 查看帮助
# =====================================================================
set -euo pipefail

# ---------- 参数解析 ----------
SKIP_SETUP=false
SKIP_PREHEAT=false
STAGE="all"           # all / setup / preheat / train
CUDA_VERSION="12.1"
ENV_NAME="reranker_gpu"
DRY_RUN=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-setup)    SKIP_SETUP=true; shift ;;
        --skip-preheat)  SKIP_PREHEAT=true; shift ;;
        --stage)         STAGE="$2"; shift 2 ;;
        --cuda)          CUDA_VERSION="$2"; shift 2 ;;
        --env)           ENV_NAME="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        --help|-h)
            grep '^#' "$0" | head -25
            exit 0
            ;;
        *)
            echo "未知参数: $1 (使用 --help 查看用法)"
            exit 1
            ;;
    esac
done

# ---------- 路径与日志 ----------
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "${PROJECT_ROOT}"

LOG_DIR="logs/deploy"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/auto_deploy_$(date +%Y%m%d_%H%M%S).log"

TRAIN_FILE_RAW="data/reranker_trainset.jsonl"
VAL_FILE_RAW="data/reranker_valset.jsonl"
OUTPUT_DIR="data/reranker_finetuned"
CKPT_DIR="${OUTPUT_DIR}/checkpoints"

# ---------- 工具函数 ----------
log()  { echo "[$(date '+%H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
info() { echo "[$(date '+%H:%M:%S')] [INFO] $*" | tee -a "${LOG_FILE}"; }
warn() { echo "[$(date '+%H:%M:%S')] [WARN] $*" | tee -a "${LOG_FILE}"; }
fail() { echo "[$(date '+%H:%M:%S')] [FAIL] $*" | tee -a "${LOG_FILE}"; exit 1; }

run_or_dry() {
    # 【简易】--dry-run 模式只打印命令,不执行
    if [[ "${DRY_RUN}" == "true" ]]; then
        echo "  [DRY-RUN] $*"
    else
        eval "$@"
    fi
}

# ---------- 主流程 ----------
log "========================================"
log " 生产环境 GPU 自动化部署"
log " 项目根: ${PROJECT_ROOT}"
log " 阶段: ${STAGE}  CUDA: ${CUDA_VERSION}  Conda: ${ENV_NAME}"
log " Skip-Setup: ${SKIP_SETUP}  Skip-Preheat: ${SKIP_PREHEAT}  Dry-Run: ${DRY_RUN}"
log " 日志文件: ${LOG_FILE}"
log "========================================"

# =====================================================================
# 阶段 1: 依赖安装(复用 linux_setup.sh)
# =====================================================================
stage_setup() {
    log ""
    log "========== 阶段 1/3: 依赖安装 =========="

    # 【不易】前置检查:nvidia-smi 必须可用
    if ! command -v nvidia-smi &> /dev/null; then
        fail "未检测到 nvidia-smi,请在 GPU 服务器运行(非 GPU 环境请参考 README.md 选 Colab)"
    fi
    GPU_INFO=$(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null | head -1)
    log "  GPU: ${GPU_INFO}"

    # 显存检查(警告,不 fail)
    MEM_MB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -1 | tr -d ' ')
    if [[ "${MEM_MB}" -lt 8000 ]] 2>/dev/null; then
        warn "显存 ${MEM_MB}MB < 8GB,训练可能 OOM,建议降低 batch_size"
    fi

    SETUP_SCRIPT="scripts/deploy_gpu_training/linux_setup.sh"
    if [[ ! -f "${SETUP_SCRIPT}" ]]; then
        fail "未找到 ${SETUP_SCRIPT}"
    fi

    log "  调用 linux_setup.sh (CUDA=${CUDA_VERSION}, ENV=${ENV_NAME})"
    log "  ---"
    if [[ "${DRY_RUN}" == "true" ]]; then
        run_or_dry "bash ${SETUP_SCRIPT} ${CUDA_VERSION} ${ENV_NAME}"
    else
        if ! bash "${SETUP_SCRIPT}" "${CUDA_VERSION}" "${ENV_NAME}" 2>&1 | tee -a "${LOG_FILE}"; then
            fail "依赖安装失败,请检查日志: ${LOG_FILE}"
        fi
    fi

    log "  ✅ 阶段 1 完成:依赖安装"
}

# =====================================================================
# 阶段 2: 数据预热(数据校验 + 模型预下载 + 探测缓存)
# =====================================================================
stage_preheat() {
    log ""
    log "========== 阶段 2/3: 数据预热 =========="

    # 激活 conda 环境(后续命令在已激活环境中执行)
    if command -v conda &> /dev/null; then
        source "$(conda info --base)/etc/profile.d/conda.sh" 2>/dev/null || true
        conda activate "${ENV_NAME}" 2>/dev/null || warn "无法激活 conda 环境 ${ENV_NAME},使用当前 Python"
    fi

    PYTHON="${PYTHON:-$(command -v python || command -v python3)}"
    [[ -z "${PYTHON}" ]] && fail "未找到 python"

    # 2.1 训练数据校验
    log ""
    log "  [2.1] 训练数据校验"
    for f in "${TRAIN_FILE_RAW}" "${VAL_FILE_RAW}"; do
        if [[ ! -f "${f}" ]]; then
            fail "训练数据缺失: ${f}"
        fi
        LINES=$(wc -l < "${f}")
        SIZE=$(du -h "${f}" | cut -f1)
        log "    ${f}: ${LINES} 行, ${SIZE}"
    done

    # 2.2 模型预下载(避免训练时网络中断)
    log ""
    log "  [2.2] 模型预下载 (BAAI/bge-reranker-v2-m3)"
    export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
    export HF_HUB_DISABLE_XET=1
    export HF_XET_HIGH_PERFORMANCE=0
    export ANONYMIZED_TELEMETRY=False

    if [[ "${DRY_RUN}" == "true" ]]; then
        run_or_dry "${PYTHON} -c \"from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-reranker-v2-m3')\""
    else
        ${PYTHON} -c "
from huggingface_hub import snapshot_download
import os
model_id = 'BAAI/bge-reranker-v2-m3'
print(f'  预下载: {model_id}')
path = snapshot_download(model_id)
print(f'  已缓存: {path}')
" 2>&1 | tee -a "${LOG_FILE}" || fail "模型预下载失败,检查 HF_ENDPOINT 或网络"
    fi

    # 2.3 Embedding 探测缓存(预热子进程,避免首次启动超时)
    log ""
    log "  [2.3] Embedding 探测缓存预热"
    PROBE_CACHE="data/.embedding_probe"
    if [[ -f "${PROBE_CACHE}" ]]; then
        log "    探测缓存已存在,跳过: ${PROBE_CACHE}"
        cat "${PROBE_CACHE}" | tee -a "${LOG_FILE}"
    else
        if [[ "${DRY_RUN}" == "true" ]]; then
            run_or_dry "${PYTHON} -c \"import agent.tool_router_hybrid as h; h._ensure_st_checked()\""
        else
            ${PYTHON} -c "
import sys
sys.path.insert(0, '.')
import agent.tool_router_hybrid as h
available = h._ensure_st_checked()
print(f'  探测结果: available={available}')
" 2>&1 | tee -a "${LOG_FILE}" || warn "Embedding 探测失败(训练仍可继续,Reranker 不依赖此缓存)"
        fi
    fi

    # 2.4 数据预处理(可选,生成 _processed.jsonl)
    log ""
    log "  [2.4] 数据预处理"
    TRAIN_PROCESSED="data/reranker_trainset_processed.jsonl"
    VAL_PROCESSED="data/reranker_valset_processed.jsonl"
    PREPROCESS_SCRIPT="scripts/preprocess_trainset.py"

    if [[ -f "${TRAIN_PROCESSED}" ]] && [[ -f "${VAL_PROCESSED}" ]]; then
        log "    已存在预处理数据,跳过(如需重新生成请删除 ${TRAIN_PROCESSED})"
    elif [[ -f "${PREPROCESS_SCRIPT}" ]]; then
        if [[ "${DRY_RUN}" == "true" ]]; then
            run_or_dry "${PYTHON} ${PREPROCESS_SCRIPT} --train ${TRAIN_FILE_RAW} --val ${VAL_FILE_RAW} --output-train ${TRAIN_PROCESSED} --output-val ${VAL_PROCESSED}"
        else
            if ! ${PYTHON} "${PREPROCESS_SCRIPT}" \
                --train "${TRAIN_FILE_RAW}" --val "${VAL_FILE_RAW}" \
                --output-train "${TRAIN_PROCESSED}" --output-val "${VAL_PROCESSED}" \
                2>&1 | tee -a "${LOG_FILE}"; then
                warn "数据预处理失败,将使用原始数据训练"
            fi
        fi
    else
        warn "未找到 ${PREPROCESS_SCRIPT},将使用原始数据训练"
    fi

    log "  ✅ 阶段 2 完成:数据预热"
}

# =====================================================================
# 阶段 3: 断点续训启动(检测 checkpoint → 决定 resume/full)
# =====================================================================
stage_train() {
    log ""
    log "========== 阶段 3/3: 断点续训启动 =========="

    # 3.1 checkpoint 检测
    log ""
    log "  [3.1] checkpoint 检测"
    RESUME_FLAG=""

    if [[ -d "${CKPT_DIR}" ]]; then
        CKPT_COUNT=$(find "${CKPT_DIR}" -maxdepth 1 -type d -name "epoch_*" 2>/dev/null | wc -l)
        log "    checkpoint 目录: ${CKPT_DIR}"
        log "    checkpoint 数量: ${CKPT_COUNT}"

        if [[ "${CKPT_COUNT}" -gt 0 ]]; then
            # 找最新 checkpoint
            LATEST_CKPT=$(ls -1d "${CKPT_DIR}"/epoch_* 2>/dev/null | sort -t_ -k2 -n | tail -1)
            LATEST_EPOCH=$(basename "${LATEST_CKPT}" | sed 's/epoch_//')
            log "    最新 checkpoint: ${LATEST_CKPT} (epoch ${LATEST_EPOCH})"

            # 验证 adapter 文件存在(断点续训必需)
            HAS_ADAPTER=false
            for adapter_file in "adapter_model.safetensors" "adapter_model.bin" "adapter_model.pt"; do
                if [[ -f "${LATEST_CKPT}/${adapter_file}" ]]; then
                    HAS_ADAPTER=true
                    log "    adapter 文件: ${adapter_file} ✅"
                    break
                fi
            done

            if [[ "${HAS_ADAPTER}" == "true" ]]; then
                # 检查是否有误存的完整模型文件(应清理)
                for full_model in "model.safetensors" "pytorch_model.bin"; do
                    if [[ -f "${LATEST_CKPT}/${full_model}" ]]; then
                        MODEL_SIZE=$(du -h "${LATEST_CKPT}/${full_model}" | cut -f1)
                        warn "发现误存的完整模型: ${full_model} (${MODEL_SIZE}),自动清理"
                        run_or_dry "rm -f \"${LATEST_CKPT}/${full_model}\""
                    fi
                done

                # 检查 training_state.json 可读性
                STATE_FILE="${LATEST_CKPT}/training_state.json"
                if [[ -f "${STATE_FILE}" ]]; then
                    PYTHON="${PYTHON:-$(command -v python || command -v python3)}"
                    if ! ${PYTHON} -c "import json; json.load(open('${STATE_FILE}'))" 2>/dev/null; then
                        warn "training_state.json 损坏,adapter 仍可加载但早停计数器将归零"
                    fi
                fi

                RESUME_FLAG="--resume --skip-preprocess"
                log "    ✅ 启用断点续训(从 epoch $((LATEST_EPOCH + 1)) 继续)"
            else
                warn "最新 checkpoint 无 adapter 文件,尝试回退到上一个 epoch"

                # 回退到上一个 epoch(故障排查手册 §1.4 恢复逻辑)
                PREV_CKPT=$(ls -1d "${CKPT_DIR}"/epoch_* 2>/dev/null | sort -t_ -k2 -n | tail -2 | head -1)
                if [[ -n "${PREV_CKPT}" ]] && [[ "${PREV_CKPT}" != "${LATEST_CKPT}" ]]; then
                    warn "删除损坏的 checkpoint: ${LATEST_CKPT}"
                    run_or_dry "rm -rf \"${LATEST_CKPT}\""
                    log "    回退到: ${PREV_CKPT}"

                    # 验证上一个 checkpoint 的 adapter
                    for adapter_file in "adapter_model.safetensors" "adapter_model.bin" "adapter_model.pt"; do
                        if [[ -f "${PREV_CKPT}/${adapter_file}" ]]; then
                            RESUME_FLAG="--resume --skip-preprocess"
                            log "    ✅ 启用断点续训(从上一个 epoch 恢复)"
                            break
                        fi
                    done
                fi

                if [[ -z "${RESUME_FLAG}" ]]; then
                    warn "无可恢复的 checkpoint,从头训练(删除整个 checkpoints 目录)"
                    run_or_dry "rm -rf \"${CKPT_DIR}\""
                fi
            fi
        else
            log "    checkpoint 目录为空,首次训练"
        fi
    else
        log "    无 checkpoint 目录,首次训练"
    fi

    # 3.2 构建训练命令
    log ""
    log "  [3.2] 构建训练命令"
    TRAIN_SCRIPT="scripts/deploy_gpu_training/run_phase2_train.sh"
    [[ ! -f "${TRAIN_SCRIPT}" ]] && fail "未找到 ${TRAIN_SCRIPT}"

    TRAIN_CMD="bash ${TRAIN_SCRIPT} ${RESUME_FLAG}"
    log "    训练命令: ${TRAIN_CMD}"
    log "    ---"

    if [[ "${DRY_RUN}" == "true" ]]; then
        log "  [DRY-RUN] 跳过实际训练执行"
        run_or_dry "${TRAIN_CMD}"
        return
    fi

    # 3.3 启动训练
    log ""
    log "  [3.3] 启动训练"
    TRAIN_START=$(date +%s)

    if ! ${TRAIN_CMD} 2>&1 | tee -a "${LOG_FILE}"; then
        TRAIN_END=$(date +%s)
        ELAPSED=$((TRAIN_END - TRAIN_START))
        log ""
        log "✗ 训练失败 (耗时 ${ELAPSED}s)"
        log ""
        log "  恢复方案:"
        log "    1. 查看故障排查手册: docs/troubleshooting/phase2_finetuning_troubleshooting.md"
        log "    2. 重新运行本脚本(自动检测 checkpoint 并续训):"
        log "       bash scripts/deploy_gpu_training/auto_deploy.sh --skip-setup --skip-preheat"
        log "    3. 或仅重跑训练阶段:"
        log "       bash scripts/deploy_gpu_training/auto_deploy.sh --stage train"
        exit 1
    fi

    TRAIN_END=$(date +%s)
    ELAPSED=$((TRAIN_END - TRAIN_START))
    log ""
    log "  训练总耗时: ${ELAPSED}s ($((ELAPSED/60))min $((ELAPSED%60))s)"
    log "  ✅ 阶段 3 完成:训练"
}

# =====================================================================
# 阶段编排
# =====================================================================
case "${STAGE}" in
    setup)
        stage_setup
        ;;
    preheat)
        stage_preheat
        ;;
    train)
        stage_train
        ;;
    all)
        if [[ "${SKIP_SETUP}" == "false" ]]; then
            stage_setup
        else
            log "跳过阶段 1 (--skip-setup)"
        fi

        if [[ "${SKIP_PREHEAT}" == "false" ]]; then
            stage_preheat
        else
            log "跳过阶段 2 (--skip-preheat)"
        fi

        stage_train
        ;;
    *)
        fail "未知阶段: ${STAGE} (可选: all / setup / preheat / train)"
        ;;
esac

# =====================================================================
# 完成总结
# =====================================================================
log ""
log "========================================"
log " ✅ 自动化部署完成"
log "========================================"
log ""
log "  训练输出: ${OUTPUT_DIR}"
log "  部署日志: ${LOG_FILE}"
log ""
log "  下一步:"
log "    1. 评估模型: python scripts/eval_reranker_zero_shot.py --model ${OUTPUT_DIR} --verbose"
log "    2. 端到端验证: python scripts/verify_phase1_e2e.py --verbose"
log "    3. LRU 缓存验证: python scripts/verify_lru_cache_logging.py"
log "    4. 部署到生产: 参考 docs/runbooks/GPU_PHASE2_TRAIN_RUNBOOK.md §6"
log ""
log "  如需重新训练(从头):"
log "    rm -rf ${CKPT_DIR}"
log "    bash scripts/deploy_gpu_training/auto_deploy.sh --skip-setup --skip-preheat"
log ""
