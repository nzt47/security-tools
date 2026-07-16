#!/bin/sh
# entrypoint.sh — 运维日报容器入口脚本
#
# 用法:
#   一次性模式（默认）: docker run ... tlm-ops-reporter --log-dir /app/logs --output /app/output/report.md
#   cron 模式:          docker run -d ... tlm-ops-reporter --cron
#
# cron 模式下每天 01:00 自动生成日报
# [简易] 不安装 cron 包：用 shell while + sleep 模拟定时任务，避免 apt-get 网络下载

set -e

# cron 触发时刻：从环境变量读取（K8s ConfigMap/ENV 注入），默认 01:00
# [变易] 容器化部署时通过 ENV 覆盖，无需重建镜像
CRON_HOUR="${CRON_HOUR:-1}"
CRON_MINUTE="${CRON_MINUTE:-0}"
LOG_DIR="${LOG_DIR:-/app/logs}"
OUTPUT_DIR="${OUTPUT_DIR:-/app/output}"

# 计算"下一次目标时刻"需要 sleep 的秒数
# 依赖 date 命令（python:3.11-slim 自带 GNU coreutils）
next_run_seconds() {
    # 当前 epoch 秒
    now=$(date +%s)
    # 当前时分
    cur_h=$(date +%H)
    cur_m=$(date +%M)
    # 目标时刻今天的 epoch（用 date -d 解析）
    target_today=$(date -d "today ${CRON_HOUR}:${CRON_MINUTE}:00" +%s 2>/dev/null || echo 0)
    if [ "$target_today" -le "$now" ]; then
        # 已过目标时刻，下一次是明天
        target=$(date -d "tomorrow ${CRON_HOUR}:${CRON_MINUTE}:00" +%s 2>/dev/null || echo 0)
    else
        target="$target_today"
    fi
    echo $((target - now))
}

# 生成日报的内部函数
run_report() {
    # 使用 yesterday 作为日期，让脚本只统计昨天的日志
    YESTERDAY=$(date -d "yesterday" +%Y-%m-%d 2>/dev/null || date -v-1d +%Y-%m-%d)
    OUTPUT_FILE="${OUTPUT_DIR}/${YESTERDAY}.md"
    echo "[CRON] $(date '+%Y-%m-%d %H:%M:%S') 生成 ${YESTERDAY} 运维日报..."
    python /app/generate_ops_daily_report.py \
        --log-dir "$LOG_DIR" \
        --date "$YESTERDAY" \
        --output "$OUTPUT_FILE" \
        || echo "[CRON] $(date) 日报生成失败（继续等待下次触发）"
    echo "[CRON] $(date '+%Y-%m-%d %H:%M:%S') 日报已生成: $OUTPUT_FILE"
}

# 检查是否启用 cron 模式
if [ "$1" = "--cron" ]; then
    echo "[ENTRYPOINT] 启用 cron 模式，计划: 每天 ${CRON_HOUR}:${CRON_MINUTE}"
    echo "[ENTRYPOINT] 当前时间: $(date '+%Y-%m-%d %H:%M:%S %Z')"

    # 主循环：sleep 到目标时刻，触发后计算下一次
    while true; do
        wait_sec=$(next_run_seconds)
        echo "[ENTRYPOINT] 下次执行需等待 ${wait_sec} 秒（约 $((wait_sec / 3600))h $(((wait_sec % 3600) / 60))m）"
        sleep "$wait_sec"
        run_report
        # 触发后短暂 sleep 5 秒避免循环抖动
        sleep 5
    done
else
    # 一次性模式：直接运行日报脚本
    echo "[ENTRYPOINT] 一次性模式，参数: $*"
    exec python /app/generate_ops_daily_report.py "$@"
fi
