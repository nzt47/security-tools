# 云枢 T8 多租户网关 — Docker 镜像
# 说明：镜像含 torch/chromadb 等 AI 依赖（CPU 版 torch 瘦身），首次构建较慢
# 构建：docker build -t yunshu-t8-gateway .
FROM python:3.12-slim

# 系统依赖：opencv(headless) 需 libgl；chromadb 需 gomp；构建 torch 扩展需 build-essential
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先拷贝依赖清单以利用缓存（pyproject + 源码结构）
COPY pyproject.toml README.md ./
COPY agent ./agent
COPY app_server.py ./app_server.py
COPY scripts ./scripts

# CPU 版 torch（避免下载 ~2GB CUDA 包）→ 再装项目其余依赖
# 【修复 2026-08-23】--no-build-isolation 模式下 pip 不自动装 build 依赖，
# 依赖构建缺 pybind11 导致 metadata-generation-failed；显式补装构建工具链
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir pybind11 setuptools wheel \
    && pip install --no-cache-dir -e . --no-build-isolation

# 数据目录（租户/Key/审计日志），通过 volume 持久化
RUN mkdir -p /app/data && chmod -R a+rw /app/data \
    && chmod +x /app/scripts/docker_entrypoint.sh

EXPOSE 5678
ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
CMD ["python", "app_server.py"]
