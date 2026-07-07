# Dockerfile for Digital Life
# 使用 Python 3.11 官方镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# 功能开关（可通过 docker run -e 或 docker-compose 覆盖）
ENV YUNSHU_FEATURE_SANDBOX=false

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖（过滤 Windows-only 包：pywin32/pypiwin32/comtypes 在 Linux 上无法安装）
RUN grep -v -E 'pywin32|pypiwin32|comtypes' requirements.txt | pip install --no-cache-dir -r /dev/stdin

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p /app/logs /app/data /app/.backups

# 设置非 root 用户运行
RUN useradd -m -u 1000 digital && chown -R digital:digital /app
USER digital

# 暴露端口（如果需要）
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "print('ok')" || exit 1

# 默认命令（可覆盖）
CMD ["python", "-c", "print('Digital Life container is ready!')"]
