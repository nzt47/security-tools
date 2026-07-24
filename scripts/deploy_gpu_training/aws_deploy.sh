#!/bin/bash
# =====================================================================
# AWS EC2 部署脚本 — bge-reranker-v2-m3 LoRA GPU 微调
# 【不易】假设使用 Deep Learning AMI (Ubuntu 22.04) + g4dn.xlarge (T4 16GB)
# 【变易】支持 rsync 或 git clone 两种代码同步方式
# 【简易】单脚本完成:环境检查 → 代码同步 → 依赖安装 → 后台训练 → 打包下载
#
# 前置条件:
#   1. 已启动 EC2 g4dn.xlarge 实例,使用 Deep Learning AMI (Ubuntu 22.04)
#   2. 安全组已开放 SSH(22)端口
#   3. 本地已配置 SSH key 并能 ssh ubuntu@<EC2_PUBLIC_IP> 登录
#   4. 训练数据已在本机 c:\Users\Administrator\agent\data\ 下
#
# 用法(在 EC2 实例上执行):
#   bash aws_deploy.sh
#
# 用法(从本机一键部署到 EC2,通过 SSH):
#   ssh ubuntu@<EC2_PUBLIC_IP> 'bash -s' < scripts/deploy_gpu_training/aws_deploy.sh
# =====================================================================
set -euo pipefail

# ---------- 配置变量(可按需修改) ----------
PROJECT_ROOT="${HOME:-/home/ubuntu}/agent"
WORKDIR="${PROJECT_ROOT}"
TRAIN_LOG="${WORKDIR}/train.log"
OUTPUT_DIR="${WORKDIR}/data/reranker_finetuned"
REQUIREMENTS_URL=""  # 留空则使用项目内 requirements-gpu.txt

# 训练超参(与本地一致)
TRAIN_FILE="data/reranker_trainset.jsonl"
VAL_FILE="data/reranker_valset.jsonl"
BASE_MODEL="BAAI/bge-reranker-v2-m3"
MAX_LENGTH=512
EPOCHS=5
BATCH_SIZE=16
LR=2e-5
LORA_RANK=8
LORA_ALPHA=2
PATIENCE=2
OPTIMIZER=adamw

echo "========================================"
echo " AWS EC2 GPU 训练部署"
echo " 工作目录: ${WORKDIR}"
echo " 训练日志: ${TRAIN_LOG}"
echo " 输出目录: ${OUTPUT_DIR}"
echo "========================================"

# ---------- 步骤 1: 环境检查 ----------
echo ""
echo "[1/7] 环境检查..."

echo "  GPU 信息:"
if command -v nvidia-smi &> /dev/null; then
    nvidia-smi --query-gpu=name,memory.total,driver_version \
               --format=csv,noheader | sed 's/^/    /'
else
    echo "    ✗ nvidia-smi 不可用,请确认使用 Deep Learning AMI"
    exit 1
fi

echo "  CUDA 版本:"
if command -v nvcc &> /dev/null; then
    nvcc --version | grep "release" | sed 's/^/    /'
else
    echo "    ⚠ nvcc 不可用,但 Deep Learning AMI 通常已预装 CUDA runtime"
fi

echo "  Python 版本:"
python3 --version | sed 's/^/    /'

echo "  磁盘空间:"
df -h "${HOME}" | sed 's/^/    /'

# ---------- 步骤 2: 创建工作目录 ----------
echo ""
echo "[2/7] 创建工作目录"

mkdir -p "${WORKDIR}/scripts/finetune_reranker.py"
mkdir -p "${WORKDIR}/scripts/deploy_gpu_training"
mkdir -p "${WORKDIR}/data"
echo "  目录已就绪: ${WORKDIR}"

# ---------- 步骤 3: 同步项目代码 ----------
echo ""
echo "[3/7] 同步项目代码"

# 优先尝试 rsync(若脚本是从本机推送到 EC2,文件已就位)
if [[ -f "${WORKDIR}/scripts/finetune_reranker.py" ]]; then
    echo "  训练脚本已存在,跳过同步"
else
    echo "  ⚠ 未找到训练脚本,请通过以下方式之一同步代码:"
    echo "    方式 A (rsync 从本机推送):"
    echo "      rsync -avz --exclude '.venv*' --exclude '__pycache__' \\"
    echo "        -e ssh c:/Users/Administrator/agent/scripts/finetune_reranker.py \\"
    echo "        ubuntu@<EC2_PUBLIC_IP>:~/agent/scripts/"
    echo "      rsync -avz -e ssh \\"
    echo "        c:/Users/Administrator/agent/data/reranker_trainset.jsonl \\"
    echo "        c:/Users/Administrator/agent/data/reranker_valset.jsonl \\"
    echo "        ubuntu@<EC2_PUBLIC_IP>:~/agent/data/"
    echo "    方式 B (git clone,若项目已推送至远端):"
    echo "      cd ~/agent && git clone <REPO_URL> ."
    exit 1
fi

# 校验训练数据存在
if [[ ! -f "${WORKDIR}/${TRAIN_FILE}" ]] || [[ ! -f "${WORKDIR}/${VAL_FILE}" ]]; then
    echo "  ✗ 训练数据缺失:"
    echo "    ${WORKDIR}/${TRAIN_FILE}"
    echo "    ${WORKDIR}/${VAL_FILE}"
    echo "  请用 scp/rsync 上传:"
    echo "    scp c:/Users/Administrator/agent/data/reranker_*.jsonl \\"
    echo "        ubuntu@<EC2_PUBLIC_IP>:~/agent/data/"
    exit 1
fi
TRAIN_COUNT=$(wc -l < "${WORKDIR}/${TRAIN_FILE}")
VAL_COUNT=$(wc -l < "${WORKDIR}/${VAL_FILE}")
echo "  训练集样本数: ${TRAIN_COUNT}"
echo "  验证集样本数: ${VAL_COUNT}"

# ---------- 步骤 4: 安装依赖 ----------
echo ""
echo "[4/7] 安装依赖"

# Deep Learning AMI 通常预装 conda 环境(如 pytorch 或 tensorflow_p310)
# 优先使用预装环境,否则用系统 Python
if command -v conda &> /dev/null; then
    CONDA_BASE=$(conda info --base)
    source "${CONDA_BASE}/etc/profile.d/conda.sh"
    # 优先激活 pytorch 环境(AMI 默认)
    if conda env list | grep -qw "pytorch"; then
        conda activate pytorch
        echo "  已激活 conda 环境: pytorch"
    else
        echo "  使用 conda base 环境"
    fi
fi

echo "  当前 Python: $(python3 --version)"
echo "  当前 pip: $(pip3 --version 2>/dev/null || python3 -m pip --version)"

REQUIREMENTS="${WORKDIR}/scripts/deploy_gpu_training/requirements-gpu.txt"
if [[ -f "${REQUIREMENTS}" ]]; then
    echo "  安装依赖: ${REQUIREMENTS}"
    pip3 install --upgrade pip wheel
    pip3 install -r "${REQUIREMENTS}"
else
    echo "  ⚠ 未找到 requirements-gpu.txt,安装最小依赖集"
    pip3 install "peft>=0.7.0" "accelerate>=0.27.0" \
                 "sentence-transformers>=2.3.0" "transformers>=4.38.0" \
                 "scikit-learn>=1.3.0" "numpy<2.0" "tqdm>=4.66.0" \
                 "huggingface_hub>=0.20.0"
fi

# 配置 HF 镜像(国内镜像加速下载,海外 EC2 也可用)
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
export HF_XET_HIGH_PERFORMANCE=0
export ANONYMIZED_TELEMETRY=False

# ---------- 步骤 5: 启动训练(后台) ----------
echo ""
echo "[5/7] 启动训练(后台运行,日志写入 ${TRAIN_LOG})"

cd "${WORKDIR}"

# 若已有训练进程在跑,先停止
if pgrep -f "finetune_reranker.py" &> /dev/null; then
    echo "  检测到已有训练进程,先停止"
    pkill -f "finetune_reranker.py" || true
    sleep 2
fi

# 清空旧日志
> "${TRAIN_LOG}"

# nohup 后台启动
nohup python3 scripts/finetune_reranker.py \
    --train "${TRAIN_FILE}" \
    --val "${VAL_FILE}" \
    --output "${OUTPUT_DIR}" \
    --base-model "${BASE_MODEL}" \
    --max-length "${MAX_LENGTH}" \
    --epochs "${EPOCHS}" \
    --batch-size "${BATCH_SIZE}" \
    --lr "${LR}" \
    --lora-rank "${LORA_RANK}" \
    --lora-alpha "${LORA_ALPHA}" \
    --early-stopping-patience "${PATIENCE}" \
    --optimizer "${OPTIMIZER}" \
    > "${TRAIN_LOG}" 2>&1 &

TRAIN_PID=$!
echo "  训练进程 PID: ${TRAIN_PID}"
echo "  日志文件: ${TRAIN_LOG}"
echo ""
echo "  实时查看日志:"
echo "    tail -f ${TRAIN_LOG}"
echo ""
echo "  实时监控(ASCII 曲线):"
echo "    python3 scripts/deploy_gpu_training/monitor_train.py --log ${TRAIN_LOG}"

# ---------- 步骤 6: 等待训练完成 + 打包模型 ----------
echo ""
echo "[6/7] 等待训练完成并打包模型"

# 等待训练进程结束
WAIT_PID="${TRAIN_PID}"
echo "  等待 PID ${WAIT_PID} 结束..."
while kill -0 "${WAIT_PID}" 2>/dev/null; do
    sleep 30
    # 打印最近一行日志,提示进度
    if [[ -s "${TRAIN_LOG}" ]]; then
        LAST_LINE=$(tail -1 "${TRAIN_LOG}" 2>/dev/null || echo "")
        if [[ -n "${LAST_LINE}" ]]; then
            echo "    [$(date +%H:%M:%S)] ${LAST_LINE}"
        fi
    fi
done
echo "  训练进程已结束"

# 校验输出
if [[ ! -d "${OUTPUT_DIR}" ]] || [[ -z "$(ls -A "${OUTPUT_DIR}" 2>/dev/null)" ]]; then
    echo "  ✗ 输出目录为空,训练可能失败"
    echo "  最近 50 行日志:"
    tail -50 "${TRAIN_LOG}" | sed 's/^/    /'
    exit 1
fi

# 打包模型(tar.gz)
TARBALL="${WORKDIR}/reranker_finetuned.tar.gz"
echo "  打包模型: ${TARBALL}"
tar -czf "${TARBALL}" -C "${WORKDIR}/data" "reranker_finetuned"
TARBALL_SIZE=$(du -h "${TARBALL}" | cut -f1)
echo "  打包完成,大小: ${TARBALL_SIZE}"

# ---------- 步骤 7: 下载模型到本地(给出 scp 命令) ----------
echo ""
echo "[7/7] 下载模型到本地"
echo "  在本机(Windows PowerShell)执行以下命令下载:"
echo ""
echo "    scp ubuntu@<EC2_PUBLIC_IP>:~/agent/reranker_finetuned.tar.gz \\"
echo "        C:/Users/Administrator/agent/data/"
echo ""
echo "  下载后解压:"
echo "    cd C:/Users/Administrator/agent/data"
echo "    tar -xzf reranker_finetuned.tar.gz"
echo ""
echo "  或使用 sftp:"
echo "    sftp ubuntu@<EC2_PUBLIC_IP>"
echo "    sftp> get ~/agent/reranker_finetuned.tar.gz"
echo ""
echo "========================================"
echo " ✅ AWS 部署训练流程完成"
echo "========================================"
echo ""
echo "  训练日志: ${TRAIN_LOG}"
echo "  模型目录: ${OUTPUT_DIR}"
echo "  打包文件: ${TARBALL} (${TARBALL_SIZE})"
echo ""
echo "  ⚠ 训练完成后请及时停止 EC2 实例避免持续计费:"
echo "    aws ec2 stop-instances --instance-ids <INSTANCE_ID>"
