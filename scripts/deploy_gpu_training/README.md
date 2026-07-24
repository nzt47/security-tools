# GPU 环境部署训练配置 — bge-reranker-v2-m3 LoRA 微调

> 本目录提供在 **Linux 服务器 / AWS EC2 / Google Colab** 三种 GPU 环境下部署 `bge-reranker-v2-m3` LoRA 微调任务的完整脚本集。

## 背景

- **本地 CPU 环境无法运行** 567M 参数的 `bge-reranker-v2-m3` 微调:
  - 速度极慢:27 分钟/epoch
  - 风险高:可能触发 `0xC0000005` 原生崩溃
- **训练脚本已存在**: `scripts/finetune_reranker.py`(本目录所有脚本仅调用,不修改)
- **训练数据已存在**:
  - `data/reranker_trainset.jsonl` (490 样本)
  - `data/reranker_valset.jsonl` (140 样本)

## 文件清单

| 文件 | 用途 | 适用环境 |
|------|------|----------|
| `README.md` | 本文档 | 全部 |
| `requirements-gpu.txt` | GPU 训练依赖清单 | 全部 |
| `linux_setup.sh` | Linux 通用环境配置(conda + CUDA) | Linux 服务器 |
| `aws_deploy.sh` | AWS EC2 部署脚本(后台训练 + 打包下载) | AWS EC2 |
| `colab_train.ipynb` | Colab 笔记本(交互式训练 + 下载) | Google Colab |
| `monitor_train.py` | 训练日志监控(ASCII 曲线 + 异常检测) | 全部 |

---

## 环境 1: Google Colab(免费 T4 GPU 15GB)

**适用场景**: 快速验证 / 无 GPU 设备 / 一次性训练

### 步骤
1. 打开 [Colab](https://colab.research.google.com/)
2. 菜单栏 → 文件 → 上传笔记本 → 选择 `colab_train.ipynb`
3. 菜单栏 → 代码执行程序 → 更改运行时类型 → **T4 GPU**
4. 按顺序执行单元格(Shift+Enter):
   - 步骤 1: 检查 GPU
   - 步骤 2: 安装依赖
   - 步骤 3: 设置工作目录
   - 步骤 4: 上传训练数据(弹出文件选择框,选择本地 `finetune_reranker.py` 和 2 个 jsonl)
   - 步骤 5: 预下载模型
   - 步骤 6: 启动训练(`!python` 调用,输出实时显示)
   - 步骤 7: 检查训练结果
   - 步骤 8: 打包并下载模型(浏览器自动下载 `.tar.gz`)

### 预计耗时
- 模型下载: 2-3 分钟
- 训练(5 epoch): 10-15 分钟
- 打包 + 下载: 1-2 分钟
- **总计: 约 15-20 分钟**

### 注意事项
- Colab 免费版有 12 小时断连限制,训练需在 12 小时内完成
- 训练数据需手动上传(490+140 样本,约 500KB,秒传)
- 训练完成后模型会自动下载到本地 `Downloads/` 目录

---

## 环境 2: AWS EC2 g4dn.xlarge(T4 GPU 16GB)

**适用场景**: 长时间训练 / 需要 SSH 后台运行 / 团队共享

### 前置条件
1. 已启动 EC2 `g4dn.xlarge` 实例
2. AMI: **Deep Learning AMI (Ubuntu 22.04)** — 预装 CUDA + PyTorch
3. 安全组开放 SSH(22)端口
4. 本地已配置 SSH key,能 `ssh ubuntu@<EC2_PUBLIC_IP>` 登录

### 部署步骤

#### 步骤 1: 上传项目代码到 EC2
```bash
# 在本地 Windows PowerShell 执行
# 同步训练脚本
rsync -avz -e ssh `
    c:/Users/Administrator/agent/scripts/finetune_reranker.py `
    c:/Users/Administrator/agent/scripts/deploy_gpu_training/ `
    ubuntu@<EC2_PUBLIC_IP>:~/agent/scripts/

# 同步训练数据
scp c:/Users/Administrator/agent/data/reranker_trainset.jsonl `
    c:/Users/Administrator/agent/data/reranker_valset.jsonl `
    ubuntu@<EC2_PUBLIC_IP>:~/agent/data/
```

#### 步骤 2: 在 EC2 上执行部署脚本
```bash
# SSH 登录
ssh ubuntu@<EC2_PUBLIC_IP>

# 执行部署脚本(后台启动训练 + 自动打包)
cd ~/agent
bash scripts/deploy_gpu_training/aws_deploy.sh
```

#### 步骤 3: 实时监控训练
```bash
# 方式 A: tail 日志
tail -f ~/agent/train.log

# 方式 B: 使用监控脚本(ASCII 曲线)
python3 ~/agent/scripts/deploy_gpu_training/monitor_train.py \
    --log ~/agent/train.log --follow
```

#### 步骤 4: 下载模型到本地
```bash
# 在本地 Windows PowerShell 执行
scp ubuntu@<EC2_PUBLIC_IP>:~/agent/reranker_finetuned.tar.gz `
    C:/Users/Administrator/agent/data/

# 解压
cd C:/Users/Administrator/agent/data
tar -xzf reranker_finetuned.tar.gz
```

#### 步骤 5: 停止 EC2 实例(避免持续计费)
```bash
aws ec2 stop-instances --instance-ids <INSTANCE_ID>
```

### 预计耗时
- 实例启动 + 代码同步: 5 分钟
- 依赖安装(AMI 已预装大部分): 2-3 分钟
- 训练(5 epoch): 10-15 分钟
- 打包 + 下载: 2-3 分钟
- **总计: 约 20-30 分钟**

### 成本估算(g4dn.xlarge)
- 按需价格: 约 $0.526/小时(us-east-1)
- 训练总成本: 约 $0.2 - $0.3
- **建议: 训练完成后立即停止实例**

---

## 环境 3: Linux 通用服务器(conda + CUDA)

**适用场景**: 自有 GPU 服务器 / 实验室集群 / 长期复用

### 前置条件
- Linux 服务器(Ubuntu 20.04+ / CentOS 7+)
- NVIDIA 驱动已安装(`nvidia-smi` 可用)
- Miniconda 或 Anaconda 已安装
- GPU 显存 ≥ 8GB(推荐 16GB+)

### 部署步骤

#### 步骤 1: 配置 conda 环境 + 安装 PyTorch
```bash
# 默认 CUDA 12.1
bash scripts/deploy_gpu_training/linux_setup.sh

# 或指定 CUDA 11.8
bash scripts/deploy_gpu_training/linux_setup.sh 11.8

# 或指定环境名
bash scripts/deploy_gpu_training/linux_setup.sh 12.1 reranker_env
```

脚本自动完成:
- 创建 conda 环境(Python 3.10)
- 安装 PyTorch(对应 CUDA 版本)
- 安装 PEFT / accelerate / sentence-transformers
- 配置 HF 镜像(`~/.bashrc`)
- 预下载 `BAAI/bge-reranker-v2-m3`
- 验证 GPU 可用性

#### 步骤 2: 启动训练
```bash
# 激活环境
conda activate reranker_gpu

# 进入项目根目录
cd /path/to/agent

# 前台运行(可看到实时输出)
python scripts/finetune_reranker.py \
    --train data/reranker_trainset.jsonl \
    --val data/reranker_valset.jsonl \
    --output data/reranker_finetuned/ \
    --base-model BAAI/bge-reranker-v2-m3 \
    --max-length 512 --epochs 5 --batch-size 16 \
    --lr 2e-5 --lora-rank 8 --lora-alpha 2 \
    --early-stopping-patience 2 --optimizer adamw
```

#### 步骤 3: 后台运行 + 监控(SSH 断开不中断)
```bash
# 后台运行
nohup python scripts/finetune_reranker.py \
    --train data/reranker_trainset.jsonl \
    --val data/reranker_valset.jsonl \
    --output data/reranker_finetuned/ \
    --optimizer adamw \
    > train.log 2>&1 &

# 查看进程
ps aux | grep finetune_reranker

# 实时监控日志
tail -f train.log

# 或使用监控脚本(ASCII 曲线 + 异常检测)
python scripts/deploy_gpu_training/monitor_train.py \
    --log train.log --follow
```

---

## 训练参数详解

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--train` | (必填) | 训练集 JSONL 路径 |
| `--val` | (必填) | 验证集 JSONL 路径 |
| `--output` | (必填) | 模型输出目录 |
| `--base-model` | `BAAI/bge-reranker-v2-m3` | 基础模型 |
| `--max-length` | `512` | 最大序列长度 |
| `--epochs` | `5` | 最大训练轮数 |
| `--batch-size` | `16` | 批大小(T4 16GB 可容纳) |
| `--lr` | `2e-5` | 学习率 |
| `--lora-rank` | `8` | LoRA 秩 |
| `--lora-alpha` | `2` | LoRA alpha |
| `--early-stopping-patience` | `2` | 早停耐心值 |
| `--optimizer` | `adamw` | 优化器:`adamw`(GPU)/`sgd`(CPU 低内存)/`adafactor`(CPU 中等) |

### 优化器选择
- **`adamw`**(GPU 推荐): 9GB state,收敛快,需 GPU
- **`sgd`**(CPU 低内存): 无 state,但收敛慢
- **`adafactor`**(CPU 中等): 介于两者之间

---

## 监控脚本使用

```bash
# 单次快照(打印当前状态)
python monitor_train.py --log train.log

# 持续监控(类似 tail -f,自动清屏)
python monitor_train.py --log train.log --follow

# 自定义刷新间隔
python monitor_train.py --log train.log --follow --interval 2

# 不清屏(滚动输出,适合日志收集)
python monitor_train.py --log train.log --follow --no-clear
```

### 监控功能
- ✅ 解析 epoch / train_loss / val_loss / val_acc
- ✅ 实时显示训练阶段(加载数据 → 加载模型 → 应用 LoRA → 训练 → 保存)
- ✅ 估算剩余时间(基于已完成 epoch 平均耗时)
- ✅ ASCII 训练曲线(loss + accuracy)
- ✅ 异常检测:
  - NaN / Inf loss
  - CUDA out of memory (OOM)
  - 原生崩溃 (0xC0000005)
  - CUDA 错误
- ✅ 早停检测
- ✅ 训练完成自动退出

---

## 常见问题

### Q1: 显存不足 (OOM)
**解决方案**:
```bash
# 降低 batch_size 或 max_length
python scripts/finetune_reranker.py \
    --batch-size 8 \           # 从 16 降到 8
    --max-length 384 \          # 从 512 降到 384
    --optimizer adamw
```

### Q2: 模型下载失败
**解决方案**:
```bash
# 配置 HF 镜像(已写入 ~/.bashrc)
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_DISABLE_XET=1

# 手动预下载
huggingface-cli download BAAI/bge-reranker-v2-m3
```

### Q3: 训练准确率不提升
**解决方案**:
- 检查训练数据正负样本比例(应接近 1:1)
- 调整学习率(尝试 `--lr 1e-5` 或 `--lr 5e-5`)
- 增大 LoRA rank(`--lora-rank 16 --lora-alpha 32`)
- 增加早停耐心值(`--early-stopping-patience 3`)

### Q4: SSH 断开训练中断
**解决方案**:
```bash
# 使用 nohup 后台运行(见上文)
# 或使用 tmux/screen
tmux new -s train
python scripts/finetune_reranker.py ...
# Ctrl+B D 退出 tmux(训练继续)
# 重新连接: tmux attach -t train
```

### Q5: numpy 版本冲突
**症状**: `AttributeError: module 'numpy' has no attribute 'float'`
**解决方案**:
```bash
pip install "numpy<2.0"
# requirements-gpu.txt 已锁定 numpy<2.0
```

---

## 验证微调模型

训练完成后,在本地 Windows 环境(无需 GPU)验证:

```powershell
cd C:\Users\Administrator\agent
python scripts\eval_reranker_zero_shot.py --model data\reranker_finetuned
```

预期输出: 12 个 xfail case 中部分转 pass(准确率提升)。

---

## 安全提示

- AWS EC2 实例训练完成后**立即停止**,避免持续计费
- SSH key 妥善保管,不要提交到 git
- 训练数据中可能包含敏感信息,EC2 实例停止后建议删除 `~/agent/data/`
- HF 镜像(`hf-mirror.com`)为第三方镜像,仅供开发测试使用,生产环境建议用官方源

---

## 联系与维护

- 训练脚本问题: 查看 `scripts/finetune_reranker.py` 注释
- 本目录脚本问题: 查看各脚本头部注释
- 项目根目录: `c:\Users\Administrator\agent\`
