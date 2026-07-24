#!/bin/bash
# =====================================================================
# Linux 通用环境配置脚本 — bge-reranker-v2-m3 LoRA GPU 微调
# 【不易】不修改系统级 Python,仅在 conda 环境内安装
# 【变易】支持 CUDA 11.8 / 12.1 双版本(参数可选)
# 【简易】单脚本完成:conda 创建 → PyTorch 安装 → 依赖安装 → 模型预下载 → 验证
#
# 用法:
#   bash linux_setup.sh                  # 默认 CUDA 12.1
#   bash linux_setup.sh 11.8             # 指定 CUDA 11.8
#   bash linux_setup.sh 12.1 myenv       # 指定 CUDA 版本 + 环境名
# =====================================================================
set -euo pipefail

# ---------- 参数解析 ----------
CUDA_VERSION="${1:-12.1}"
ENV_NAME="${2:-reranker_gpu}"
PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

echo "========================================"
echo " GPU 训练环境配置"
echo " CUDA 版本: ${CUDA_VERSION}"
echo " Conda 环境名: ${ENV_NAME}"
echo " 项目根目录: ${PROJECT_ROOT}"
echo "========================================"

# ---------- 步骤 1: 环境前置检查 ----------
echo ""
echo "[1/6] 环境前置检查..."

if ! command -v nvidia-smi &> /dev/null; then
    echo "  ✗ 未检测到 nvidia-smi,请确认已安装 NVIDIA 驱动"
    exit 1
fi
echo "  GPU 信息:"
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | sed 's/^/    /'

if ! command -v conda &> /dev/null; then
    echo "  ✗ 未检测到 conda,请先安装 Miniconda 或 Anaconda"
    echo "    参考: https://docs.conda.io/en/latest/miniconda.html"
    exit 1
fi
echo "  Conda 版本: $(conda --version)"

# Python 版本检查(系统 Python 仅作参考)
if command -v python3 &> /dev/null; then
    echo "  系统 Python: $(python3 --version)"
fi

# ---------- 步骤 2: 创建 conda 环境 ----------
echo ""
echo "[2/6] 创建 conda 环境: ${ENV_NAME} (Python 3.10)"

if conda env list | grep -qw "^${ENV_NAME} "; then
    echo "  环境已存在,跳过创建(如需重建请先 conda env remove -n ${ENV_NAME})"
else
    conda create -y -n "${ENV_NAME}" python=3.10 pip
    echo "  环境创建完成"
fi

# 激活环境(在子 shell 中执行后续命令)
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate "${ENV_NAME}"
echo "  当前 Python: $(python --version)"
echo "  当前 pip: $(pip --version)"

# ---------- 步骤 3: 安装 PyTorch (CUDA) ----------
echo ""
echo "[3/6] 安装 PyTorch (CUDA ${CUDA_VERSION})"

if [[ "${CUDA_VERSION}" == "11.8" ]]; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu118"
elif [[ "${CUDA_VERSION}" == "12.1" ]]; then
    TORCH_INDEX="https://download.pytorch.org/whl/cu121"
else
    echo "  ✗ 不支持的 CUDA 版本: ${CUDA_VERSION}(仅支持 11.8 / 12.1)"
    exit 1
fi

echo "  使用索引: ${TORCH_INDEX}"
pip install --upgrade pip wheel
pip install torch torchvision torchaudio --index-url "${TORCH_INDEX}"

# ---------- 步骤 4: 安装训练依赖 ----------
echo ""
echo "[4/6] 安装训练依赖(PEFT / accelerate / sentence-transformers)"

REQUIREMENTS="${PROJECT_ROOT}/scripts/deploy_gpu_training/requirements-gpu.txt"
if [[ -f "${REQUIREMENTS}" ]]; then
    pip install -r "${REQUIREMENTS}"
else
    echo "  ⚠ 未找到 requirements-gpu.txt,改用最小依赖集"
    pip install "peft>=0.7.0" "accelerate>=0.27.0" \
                "sentence-transformers>=2.3.0" "transformers>=4.38.0" \
                "scikit-learn>=1.3.0" "numpy<2.0" "tqdm>=4.66.0" \
                "huggingface_hub>=0.20.0"
fi

# ---------- 步骤 5: 配置 HF 镜像 + 模型预下载 ----------
echo ""
echo "[5/6] 配置 HF 镜像(hf-mirror.com)并预下载模型"

# 写入 ~/.bashrc(幂等:已存在则跳过)
BASHRC="${HOME}/.bashrc"
MARKER="# HF 镜像配置(reranker 训练)"
if ! grep -qF "${MARKER}" "${BASHRC}" 2>/dev/null; then
    {
        echo ""
        echo "${MARKER}"
        echo 'export HF_ENDPOINT=https://hf-mirror.com'
        echo 'export HF_HUB_DISABLE_XET=1'
        echo 'export HF_XET_HIGH_PERFORMANCE=0'
        echo 'export ANONYMIZED_TELEMETRY=False'
    } >> "${BASHRC}"
    echo "  已写入 ~/.bashrc(重新登录或 source ~/.bashrc 生效)"
else
    echo "  ~/.bashrc 已包含 HF 镜像配置,跳过"
fi

# 当前 session 也生效
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1
export HF_XET_HIGH_PERFORMANCE=0
export ANONYMIZED_TELEMETRY=False

# 预下载模型(避免训练时网络中断)
MODEL_ID="BAAI/bge-reranker-v2-m3"
echo "  预下载模型: ${MODEL_ID}"
python -c "from huggingface_hub import snapshot_download; snapshot_download('${MODEL_ID}')"
echo "  模型已缓存到 ~/.cache/huggingface/hub"

# ---------- 步骤 6: 验证 GPU 可用性 ----------
echo ""
echo "[6/6] 验证 GPU 可用性"

python <<'PYEOF'
import torch
print(f"  PyTorch 版本: {torch.__version__}")
print(f"  CUDA 可用: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"  CUDA 版本: {torch.version.cuda}")
    print(f"  cuDNN 版本: {torch.backends.cudnn.version()}")
    n = torch.cuda.device_count()
    print(f"  GPU 数量: {n}")
    for i in range(n):
        props = torch.cuda.get_device_properties(i)
        mem_gb = props.total_memory / (1024**3)
        print(f"  GPU[{i}]: {props.name} (显存 {mem_gb:.1f} GB)")
    # 简单张量运算测试
    x = torch.randn(1000, 1000, device='cuda')
    y = torch.matmul(x, x)
    torch.cuda.synchronize()
    print(f"  GPU 张量运算测试: 通过(结果形状 {tuple(y.shape)})")
else:
    print("  ⚠ CUDA 不可用,将退化为 CPU 训练(速度极慢)")
    raise SystemExit(1)

# 验证关键依赖
import peft, accelerate, sentence_transformers, transformers
print(f"  peft: {peft.__version__}")
print(f"  accelerate: {accelerate.__version__}")
print(f"  sentence-transformers: {sentence_transformers.__version__}")
print(f"  transformers: {transformers.__version__}")

import numpy
print(f"  numpy: {numpy.__version__}")
assert numpy.__version__.split('.')[0] < '2', "numpy 必须 < 2.0"
print("  numpy 版本检查: 通过")
PYEOF

echo ""
echo "========================================"
echo " ✅ 环境配置完成"
echo "========================================"
echo ""
echo "下一步:启动训练"
echo "  conda activate ${ENV_NAME}"
echo "  cd ${PROJECT_ROOT}"
echo "  python scripts/finetune_reranker.py \\"
echo "    --train data/reranker_trainset.jsonl \\"
echo "    --val data/reranker_valset.jsonl \\"
echo "    --output data/reranker_finetuned/ \\"
echo "    --base-model BAAI/bge-reranker-v2-m3 \\"
echo "    --max-length 512 --epochs 5 --batch-size 16 \\"
echo "    --lr 2e-5 --lora-rank 8 --lora-alpha 2 \\"
echo "    --early-stopping-patience 2 --optimizer adamw"
echo ""
echo "后台运行 + 日志监控:"
echo "  nohup python scripts/finetune_reranker.py --train data/reranker_trainset.jsonl \\"
echo "    --val data/reranker_valset.jsonl --output data/reranker_finetuned/ \\"
echo "    --optimizer adamw > train.log 2>&1 &"
echo "  tail -f train.log"
echo ""
echo "实时监控(解析日志 + ASCII 曲线):"
echo "  python scripts/deploy_gpu_training/monitor_train.py --log train.log"
