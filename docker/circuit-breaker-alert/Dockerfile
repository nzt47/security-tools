FROM python:3.11-slim

LABEL maintainer="circuit_breaker team"
LABEL description="熔断器状态异常自动化告警通知脚本（独立 Docker 镜像）"
LABEL version="1.0"

# 设置工作目录
WORKDIR /app

# 设置环境变量（可通过 docker run -e 覆盖）
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ALERT_WEBHOOK_URL="" \
    ALERT_LOG_FILE="/var/log/circuit_breaker_alerts.jsonl" \
    ALERT_INTERVAL=30 \
    ALERT_STUCK_THRESHOLD=600 \
    ALERT_FREQUENT_THRESHOLD=3 \
    ALERT_WATCH_MODE=false

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制 requirements 并安装 Python 依赖
# 注意：只安装告警脚本所需的最小依赖（pydantic + 项目核心模块）
COPY requirements.txt /tmp/requirements.txt
RUN grep -E '^(pydantic|pydantic-core)' /tmp/requirements.txt | pip install --no-cache-dir -r /dev/stdin \
    || pip install --no-cache-dir pydantic

# 复制告警脚本和相关模块
# [不易] 只复制必要文件，不复制整个项目，减小镜像体积
COPY scripts/circuit_breaker_alert.py /app/scripts/circuit_breaker_alert.py
COPY scripts/post_deploy_inspection.py /app/scripts/post_deploy_inspection.py
COPY agent/ /app/agent/
COPY config.py /app/config.py
COPY agent/config_validation.py /app/agent/config_validation.py

# 创建日志目录
RUN mkdir -p /var/log /app/monitoring

# 复制监控配置（供参考）
COPY monitoring/circuit_breaker_alerts.yml /app/monitoring/circuit_breaker_alerts.yml
COPY monitoring/grafana_circuit_breaker_dashboard.json /app/monitoring/grafana_circuit_breaker_dashboard.json

# 设置 Python 路径
ENV PYTHONPATH=/app

# 健康检查：运行单次告警检查，退出码 0 表示健康
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python scripts/circuit_breaker_alert.py --json || exit 1

# 入口脚本：根据环境变量决定运行模式
ENTRYPOINT ["sh", "-c", \
    "if [ \"$ALERT_WATCH_MODE\" = \"true\" ]; then \
        python scripts/circuit_breaker_alert.py --watch \
            --interval ${ALERT_INTERVAL} \
            ${ALERT_WEBHOOK_URL:+--webhook $ALERT_WEBHOOK_URL} \
            ${ALERT_LOG_FILE:+--log-file $ALERT_LOG_FILE} \
            --stuck-threshold ${ALERT_STUCK_THRESHOLD} \
            --frequent-threshold ${ALERT_FREQUENT_THRESHOLD}; \
    else \
        python scripts/circuit_breaker_alert.py \
            ${ALERT_WEBHOOK_URL:+--webhook $ALERT_WEBHOOK_URL} \
            ${ALERT_LOG_FILE:+--log-file $ALERT_LOG_FILE} \
            --stuck-threshold ${ALERT_STUCK_THRESHOLD} \
            --frequent-threshold ${ALERT_FREQUENT_THRESHOLD}; \
    fi"]
