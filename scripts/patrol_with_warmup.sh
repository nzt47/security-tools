#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  巡检 + 预热编排脚本
#
#  【不易】每次巡检前必须执行预热，消除 metrics-server 冷启动延迟
#  【变易】通过环境变量配置参数，适配不同环境
#  【简易】shell 串联两个 Python 脚本，零额外依赖
#
#  流程:
#    1. 执行 warmup_before_patrol.py（预热 + 延迟基准测量）
#    2. 执行 hpa_scale_patrol.py（HPA 扩容时效巡检）
#    3. 输出合并结果路径
#
#  用法:
#    # 集群内执行（CronJob）
#    bash /app/scripts/patrol_with_warmup.sh
#
#    # 本地执行（通过 kubeconfig）
#    bash scripts/patrol_with_warmup.sh
#
#    # 自定义参数
#    NAMESPACE=production HPA_NAME=skill-retrieval-hpa bash scripts/patrol_with_warmup.sh
# ════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── 参数（环境变量，有默认值）──
NAMESPACE="${NAMESPACE:-production}"
HPA_NAME="${HPA_NAME:-skill-retrieval-hpa}"
SERVICE_NAME="${SERVICE_NAME:-skill-retrieval-service}"
SERVICE_PORT="${SERVICE_PORT:-8080}"
PROBE_ENDPOINT="${PROBE_ENDPOINT:-/match}"
TARGET_REPLICAS="${TARGET_REPLICAS:-15}"
MAX_SCALE_TIME="${MAX_SCALE_TIME:-60}"
PROBE_VU="${PROBE_VU:-100}"
PROBE_DURATION="${PROBE_DURATION:-90}"
WARMUP_VU="${WARMUP_VU:-10}"
WARMUP_DURATION="${WARMUP_DURATION:-20}"
# 【变易】告警 Webhook（ConfigMap 中为 PATROL_WEBHOOK_URL，兼容裸 WEBHOOK_URL）
WEBHOOK_URL="${PATROL_WEBHOOK_URL:-${WEBHOOK_URL:-}}"

# ── 输出路径 ──
OUTPUT_DIR="${OUTPUT_DIR:-/tmp}"
WARMUP_OUTPUT="${OUTPUT_DIR}/warmup-result.json"
PATROL_OUTPUT="${OUTPUT_DIR}/patrol-result.json"
REPORT_OUTPUT="${OUTPUT_DIR}/patrol-report.md"

# ── 脚本目录（自动检测）──
SCRIPT_DIR="${SCRIPT_DIR:-$(cd "$(dirname "$0")" && pwd)}"

echo "═══════════════════════════════════════════════════════════════"
echo "  HPA 巡检 + 预热编排"
echo "  Namespace: ${NAMESPACE}"
echo "  HPA:       ${HPA_NAME}"
echo "  时间:      $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "═══════════════════════════════════════════════════════════════"

# ════════════════════════════════════════════════════════════════════
#  阶段 1: metrics-server 预热（消除冷启动指标延迟）
#  【不易】预热失败不阻断巡检（记录警告，继续执行）
# ════════════════════════════════════════════════════════════════════
echo ""
echo "─── 阶段 1: metrics-server 预热 ───"
WARMUP_EXIT=0
python "${SCRIPT_DIR}/warmup_before_patrol.py" \
    --namespace "${NAMESPACE}" \
    --hpa-name "${HPA_NAME}" \
    --service-name "${SERVICE_NAME}" \
    --service-port "${SERVICE_PORT}" \
    --probe-endpoint "${PROBE_ENDPOINT}" \
    --warmup-vu "${WARMUP_VU}" \
    --warmup-duration "${WARMUP_DURATION}" \
    --output "${WARMUP_OUTPUT}" || WARMUP_EXIT=$?

if [ ${WARMUP_EXIT} -ne 0 ]; then
    echo "  [WARN] 预热未完全成功（exit=${WARMUP_EXIT}），继续执行巡检"
    echo "         预热结果见 ${WARMUP_OUTPUT}"
else
    echo "  [OK] 预热完成，结果: ${WARMUP_OUTPUT}"
fi

# ════════════════════════════════════════════════════════════════════
#  阶段 2: HPA 扩容时效巡检
#  【不易】巡检失败必须告警（脚本内部已实现 Webhook 告警）
# ════════════════════════════════════════════════════════════════════
echo ""
echo "─── 阶段 2: HPA 扩容时效巡检 ───"
# 【简易】Webhook 可选：未配置时不传 --webhook-url（巡检仍执行，仅不发告警）
WEBHOOK_ARG=()
if [ -n "${WEBHOOK_URL}" ]; then
    WEBHOOK_ARG=(--webhook-url "${WEBHOOK_URL}")
fi
python "${SCRIPT_DIR}/hpa_scale_patrol.py" \
    --hpa-name "${HPA_NAME}" \
    --namespace "${NAMESPACE}" \
    --target-replicas "${TARGET_REPLICAS}" \
    --max-scale-time "${MAX_SCALE_TIME}" \
    --probe-vu "${PROBE_VU}" \
    --probe-duration "${PROBE_DURATION}" \
    --service-name "${SERVICE_NAME}" \
    --service-port "${SERVICE_PORT}" \
    --probe-endpoint "${PROBE_ENDPOINT}" \
    --output "${PATROL_OUTPUT}" \
    --verbose \
    ${WEBHOOK_ARG[@]+"${WEBHOOK_ARG[@]}"}

PATROL_EXIT=$?

# ════════════════════════════════════════════════════════════════════
#  阶段 3: 生成合并报告 + 汇总输出
#  【简易】报告生成失败不阻断退出码（巡检结果已落盘 JSON，可事后补生成）
# ════════════════════════════════════════════════════════════════════
echo ""
echo "─── 阶段 3: 生成巡检报告 ───"
REPORT_EXIT=0
python "${SCRIPT_DIR}/generate_patrol_report.py" \
    --warmup "${WARMUP_OUTPUT}" \
    --patrol "${PATROL_OUTPUT}" \
    --output "${REPORT_OUTPUT}" || REPORT_EXIT=$?

echo ""
echo "═══════════════════════════════════════════════════════════════"
echo "  巡检完成"
echo "═══════════════════════════════════════════════════════════════"
echo "  预热结果: ${WARMUP_OUTPUT} (exit=${WARMUP_EXIT})"
echo "  巡检结果: ${PATROL_OUTPUT} (exit=${PATROL_EXIT})"
if [ ${REPORT_EXIT} -eq 0 ] && [ -f "${REPORT_OUTPUT}" ]; then
    echo "  巡检报告: ${REPORT_OUTPUT}"
else
    echo "  巡检报告: 生成失败 (exit=${REPORT_EXIT})，可手动执行:"
    echo "    python ${SCRIPT_DIR}/generate_patrol_report.py --warmup ${WARMUP_OUTPUT} --patrol ${PATROL_OUTPUT}"
fi
echo "═══════════════════════════════════════════════════════════════"

# 巡检退出码作为整体退出码（预热/报告失败不阻断）
exit ${PATROL_EXIT}
