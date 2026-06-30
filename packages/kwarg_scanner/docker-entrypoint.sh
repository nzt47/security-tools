#!/bin/sh
# ════════════════════════════════════════════════════════════
# kwarg-scanner CI 入口脚本
#
# 功能:
#   1. 包装 kwarg-scan 命令，输出结构化 JSON 日志（含 trace_id/module_name/action/duration_ms）
#   2. 捕获扫描器退出码并转换为 CI 友好的结果
#   3. 失败时抛出带业务错误码的明确错误
#   4. 预留 trackEvent 埋点占位符
#
# 退出码:
#   0 — 扫描成功，未发现 HIGH 风险
#   1 — 扫描成功，但发现 HIGH 风险（CI 应阻断）
#   2 — 参数错误或环境异常
#   3 — 扫描器内部错误
# ════════════════════════════════════════════════════════════
set -e

# 生成 trace_id（16 位十六进制）
TRACE_ID="$(date +%s%N | sha256sum | head -c 16 2>/dev/null || echo "ci_$(date +%s)")"
MODULE_NAME="kwarg_scanner_ci"
START_TS="$(date +%s.%N)"

# 结构化日志函数
log_json() {
    action="$1"
    shift
    payload=""
    while [ $# -gt 0 ]; do
        payload="${payload},\"$1\":\"$2\""
        shift 2
    done
    now="$(date +%s.%N)"
    duration_ms="$(awk "BEGIN {printf \"%.2f\", (${now} - ${START_TS}) * 1000}")"
    printf '{"trace_id":"%s","module_name":"%s","action":"%s","duration_ms":%s%s}\n' \
        "$TRACE_ID" "$MODULE_NAME" "$action" "$duration_ms" "$payload" >&2
}

# 埋点占位符（预留供后续接入监控系统）
trackEvent() {
    event_name="$1"
    event_payload="${2:-{}}"
    log_json "track_event" event_name "$event_name" payload "$event_payload"
}

# 错误抛出函数（带业务错误码）
die() {
    code="$1"
    msg="$2"
    exit_code="${3:-3}"
    log_json "error" error_code "$code" message "$msg" exit_code "$exit_code"
    trackEvent "scan_error" "{\"error_code\":\"$code\",\"exit_code\":$exit_code}"
    exit "$exit_code"
}

# ── 特殊模式处理 ─────────────────────────────────────────────

# 健康检查模式
if [ "$1" = "--health" ]; then
    if command -v kwarg-scan >/dev/null 2>&1; then
        log_json "health_check" status "healthy" scanner "available"
        echo '{"status":"healthy","scanner":"available","version":"1.0.0"}'
        exit 0
    else
        log_json "health_check" status "unhealthy" scanner "missing"
        echo '{"status":"unhealthy","scanner":"missing"}'
        exit 3
    fi
fi

# 版本信息
if [ "$1" = "--version" ] || [ "$1" = "-V" ]; then
    kwarg-scan --version
    exit 0
fi

# 帮助信息
if [ "$1" = "--help" ] || [ "$1" = "-h" ]; then
    cat <<EOF
kwarg-scanner CI 镜像 — 关键字参数冲突扫描器

用法:
  docker run --rm -v "\$(pwd):/project" kwarg-scanner [选项]

环境变量:
  SCAN_PATH        扫描路径（默认 /project）
  MIN_RISK         最低风险等级 LOW/MEDIUM/HIGH（默认 HIGH）
  OUTPUT_FORMAT    输出格式 text/json（默认 text）
  OUTPUT_FILE      输出文件路径（可选）
  ENABLE_LOGGING   启用结构化日志 true/false（默认 false）

特殊命令:
  --health         健康检查
  --version        显示版本
  --help           显示此帮助

示例:
  # CI 默认扫描（HIGH 风险阻断）
  docker run --rm -v "\$(pwd):/project" kwarg-scanner

  # 指定扫描子目录
  docker run --rm -v "\$(pwd):/project" -e SCAN_PATH=/project/src kwarg-scanner

  # JSON 格式输出到文件
  docker run --rm -v "\$(pwd):/project" -e OUTPUT_FORMAT=json -e OUTPUT_FILE=/project/report.json kwarg-scanner

  # 扫描 MEDIUM 及以上风险
  docker run --rm -v "\$(pwd):/project" -e MIN_RISK=MEDIUM kwarg-scanner
EOF
    exit 0
fi

# ── 环境校验 ─────────────────────────────────────────────────

# 校验 /project 挂载（CI 必须挂载代码目录）
if [ ! -d "/project" ]; then
    die "E_PROJECT_NOT_MOUNTED" "未挂载代码目录: 请使用 -v \$(pwd):/project 挂载" 2
fi

# ── 参数解析 ─────────────────────────────────────────────────

# 默认值（可被环境变量覆盖）
SCAN_PATH="${SCAN_PATH:-/project}"
MIN_RISK="${MIN_RISK:-HIGH}"
OUTPUT_FORMAT="${OUTPUT_FORMAT:-text}"
OUTPUT_FILE="${OUTPUT_FILE:-}"
ENABLE_LOGGING="${ENABLE_LOGGING:-false}"

# 解析命令行参数（覆盖环境变量）
# 支持: --path VALUE, --min-risk VALUE, --format VALUE, --output VALUE, --enable-logging
EXTRA_ARGS=""
while [ $# -gt 0 ]; do
    case "$1" in
        --path)
            if [ -z "$2" ]; then
                die "E_MISSING_VALUE" "--path 需要一个值" 2
            fi
            SCAN_PATH="$2"
            shift 2
            ;;
        --min-risk)
            if [ -z "$2" ]; then
                die "E_MISSING_VALUE" "--min-risk 需要一个值" 2
            fi
            MIN_RISK="$2"
            shift 2
            ;;
        --format)
            if [ -z "$2" ]; then
                die "E_MISSING_VALUE" "--format 需要一个值" 2
            fi
            OUTPUT_FORMAT="$2"
            shift 2
            ;;
        --output)
            if [ -z "$2" ]; then
                die "E_MISSING_VALUE" "--output 需要一个值" 2
            fi
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --enable-logging)
            ENABLE_LOGGING="true"
            shift
            ;;
        --exclude)
            if [ -z "$2" ]; then
                die "E_MISSING_VALUE" "--exclude 需要一个值" 2
            fi
            EXTRA_ARGS="$EXTRA_ARGS --exclude $2"
            shift 2
            ;;
        *)
            die "E_UNKNOWN_ARG" "未知参数: $1（支持: --path/--min-risk/--format/--output/--exclude/--enable-logging）" 2
            ;;
    esac
done

# ── 构建扫描命令 ─────────────────────────────────────────────

# 校验 MIN_RISK 值
case "$MIN_RISK" in
    LOW|MEDIUM|HIGH) ;;
    *)
        die "E_INVALID_RISK_LEVEL" "无效的风险等级: $MIN_RISK（支持: LOW/MEDIUM/HIGH）" 2
        ;;
esac

# 校验 OUTPUT_FORMAT 值
case "$OUTPUT_FORMAT" in
    text|json) ;;
    *)
        die "E_INVALID_FORMAT" "无效的输出格式: $OUTPUT_FORMAT（支持: text/json）" 2
        ;;
esac

# 校验扫描路径存在
if [ ! -e "$SCAN_PATH" ]; then
    die "E_PATH_NOT_FOUND" "扫描路径不存在: $SCAN_PATH" 2
fi

# 构建扫描参数
SCAN_ARGS="--path ${SCAN_PATH} --min-risk ${MIN_RISK} --format ${OUTPUT_FORMAT}"

if [ -n "$OUTPUT_FILE" ]; then
    SCAN_ARGS="$SCAN_ARGS --output ${OUTPUT_FILE}"
fi

if [ "$ENABLE_LOGGING" = "true" ]; then
    SCAN_ARGS="$SCAN_ARGS --enable-logging"
fi

if [ -n "$EXTRA_ARGS" ]; then
    SCAN_ARGS="$SCAN_ARGS $EXTRA_ARGS"
fi

# ── 执行扫描 ─────────────────────────────────────────────────

log_json "scan_start" \
    scan_path "$SCAN_PATH" \
    min_risk "$MIN_RISK" \
    format "$OUTPUT_FORMAT" \
    enable_logging "$ENABLE_LOGGING"

trackEvent "scan_invoked" "{\"scan_path\":\"$SCAN_PATH\",\"min_risk\":\"$MIN_RISK\"}"

# 执行扫描（允许非零退出码以捕获结果）
set +e
eval kwarg-scan $SCAN_ARGS
SCAN_EXIT_CODE=$?
set -e

END_TS="$(date +%s.%N)"
TOTAL_DURATION_MS="$(awk "BEGIN {printf \"%.2f\", (${END_TS} - ${START_TS}) * 1000}")"

# ── 结果处理 ─────────────────────────────────────────────────

case $SCAN_EXIT_CODE in
    0)
        log_json "scan_complete" \
            result "success" \
            exit_code "$SCAN_EXIT_CODE" \
            total_duration_ms "$TOTAL_DURATION_MS" \
            high_risk_count "0"
        trackEvent "scan_success" "{\"duration_ms\":$TOTAL_DURATION_MS}"
        echo "[CI] 扫描通过: 未发现 HIGH 风险" >&2
        exit 0
        ;;
    1)
        log_json "scan_complete" \
            result "blocked" \
            exit_code "$SCAN_EXIT_CODE" \
            total_duration_ms "$TOTAL_DURATION_MS" \
            reason "high_risk_detected"
        trackEvent "scan_blocked" "{\"duration_ms\":$TOTAL_DURATION_MS,\"reason\":\"high_risk_detected\"}"
        echo "[CI] 扫描阻断: 发现 HIGH 风险，请修复后再提交" >&2
        echo "[CI] 提示: 在 **kwargs 展开前过滤保留键，使用 safe_ 前缀命名变量" >&2
        exit 1
        ;;
    2)
        die "E_INVALID_ARGS" "参数错误: 请检查 --path/--min-risk 参数" 2
        ;;
    *)
        die "E_SCANNER_INTERNAL" "扫描器内部错误 (exit=$SCAN_EXIT_CODE)" 3
        ;;
esac
