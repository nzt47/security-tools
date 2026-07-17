# Dockerfile for TLM Ops Reporter
# 运维日报容器：定期拉取日志生成 Markdown 日报
#
# 构建:
#   docker build -t tlm-ops-reporter -f docker/ops-reporter/Dockerfile .
#
# 运行（一次性生成日报）:
#   docker run --rm -v $(pwd)/logs:/app/logs:ro -v $(pwd)/docs/ops_daily:/app/output tlm-ops-reporter \
#     --log-dir /app/logs --output /app/output/$(date +%Y-%m-%d).md
#
# 运行（cron 模式，每天 1:00 自动生成）:
#   docker run -d --name ops-reporter -v $(pwd)/logs:/app/logs:ro -v $(pwd)/docs/ops_daily:/app/output tlm-ops-reporter --cron

# [不易] 固定基础镜像到具体补丁版本 + 发行版代号，保证构建可重现
# 升级时需人工 review changelog 并更新此版本号
FROM python:3.11.9-slim-bookworm

LABEL maintainer="TLM Team"
LABEL description="TLM 熔断器运维日报生成器"

WORKDIR /app

# 环境变量
# [简易] 不安装 tzdata：Python 3.11 内置 zoneinfo 模块 + TZ 环境变量即可正确处理时区
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV TZ=Asia/Shanghai

# 复制运维日报脚本（仅依赖 Python 标准库；zoneinfo 时区数据 Debian slim 已自带）
COPY scripts/generate_ops_daily_report.py /app/generate_ops_daily_report.py

# 复制 entrypoint 脚本（支持 cron 模式，用 shell while+sleep 模拟避免装 cron 包）
COPY docker/ops-reporter/entrypoint.sh /app/entrypoint.sh
# Windows 构建时可能引入 CRLF，转换为 LF 避免 Linux 容器执行报错
RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 创建日志和输出目录
RUN mkdir -p /app/logs /app/output

# 非 root 用户运行
RUN useradd -m -u 1000 reporter && chown -R reporter:reporter /app
USER reporter

# [不易] 健康检查：检测 PID 1 进程是 entrypoint.sh 或 generate_ops_daily_report
# [变易] 不依赖 pgrep/ps（python:3.11-slim 未安装 procps），改用 grep /proc/1/cmdline
# cron 模式: PID 1 = /bin/sh /app/entrypoint.sh; once 模式: PID 1 = python generate_ops_daily_report.py
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD grep -qaE 'entrypoint.sh|generate_ops_daily_report' /proc/1/cmdline 2>/dev/null || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]
