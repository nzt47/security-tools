#!/usr/bin/env bash
# release_shell_lib.sh — 发布相关 Shell 工具函数库（其他项目直接 source 引用）
#
# 来源：release-auto.yml / release-precheck.yml 实战验证过的逻辑（2026-08-07 提炼）
# 背景问题（详见 docs/release_workflow_manual.md §6.1 / §10 Q3）：
#   1. `CODE=$(curl ...)` 在 set -e 下，curl 网络层失败（超时 exit 28/拒连 exit 7）会
#      直接终止 step、跳过重试循环 → 用 curl_http_code 把网络失败映射为 HTTP 500 进重试
#   2. `gh api` 失败时错误 JSON 可能混入 stdout（2>/dev/null 挡不住），数值结果变成
#      "JSON垃圾+0" → 用 safe_num_or_zero 正则兜底，防 `-gt` 比较报错
#
# 用法：在调用脚本中 `source /path/to/release_shell_lib.sh` 后直接调用函数。
# 与 release-auto.yml / release-precheck.yml 中的内联写法行为完全一致。

# =============================================================================
# curl 三件套封装：网络失败映射 500 + --max-time 防挂起 + 响应文件落盘
# -----------------------------------------------------------------------------
# 用法: CODE=$(curl_http_code -X POST "$API" -H "Authorization: Bearer $TOKEN" -d "$BODY")
#       [ -s gh_resp.json ] && echo "响应体: $(head -c 200 gh_resp.json)"
# 输出: HTTP 状态码（纯数字）。网络层失败（超时/拒连）输出 500，不会触发 set -e。
# 环境变量: CURL_MAX_TIME（默认 30，防 API 挂起拖满 step 超时）
# 注意: 响应体写入调用者当前目录的 gh_resp.json；读前用 read_resp_file 容错。
# =============================================================================
curl_http_code() {
  local out rc=0
  # 用变量捕获 http_code（curl 失败时 -w 仍会输出 000，不能直接透传）；
  # || rc=$? 短路形式：curl 失败时 rc=退出码（成功时为 0），且不受 set -e 影响
  out=$(curl -s -o gh_resp.json -w '%{http_code}' --max-time "${CURL_MAX_TIME:-30}" "$@" 2>/dev/null) || rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "500"   # 网络失败（exit 28 超时 / exit 7 拒连等）映射为 HTTP 500 进入重试
  else
    echo "$out"
  fi
}

# =============================================================================
# 数值安全兜底：非纯数字一律输出 0
# -----------------------------------------------------------------------------
# 用法: N=$(safe_num_or_zero "$RAW"); [ "$N" -gt 0 ] && ...   # 永不因非数字报错
# 背景: gh/curl 出错时变量可能混入 JSON/HTML 垃圾文本，`-gt` 比较会报
#       "integer expression expected" 并可能误走错误分支。
# =============================================================================
safe_num_or_zero() {
  local v="${1:-}"
  if [[ "$v" =~ ^[0-9]+$ ]]; then
    echo "$v"
  else
    echo 0
  fi
}

# =============================================================================
# gh api 数值结果安全获取（JSON 污染兜底）
# -----------------------------------------------------------------------------
# 用法: N=$(gh_api_len "repos/$REPO/commits/$SHA/check-runs" \
#         '[.check_runs[] | select(.conclusion != "success")] | length')
# 说明: gh 命令失败时错误 JSON 打到 stdout（`2>/dev/null` 挡不住），
#       必须先完整捕获再交给 safe_num_or_zero 校验，而不是管道截断。
# =============================================================================
gh_api_len() {
  local path="$1" jq_expr="$2" out
  out=$(gh api "$path" --jq "$jq_expr" 2>/dev/null || echo 0)
  safe_num_or_zero "$out"
}

# =============================================================================
# 响应体容错读取：文件缺失/为空时给出明确提示
# -----------------------------------------------------------------------------
# 用法: echo "响应体: $(read_resp_file gh_resp.json)"
# 说明: 网络层失败时 curl 可能没写文件或写空文件，直接 head 会报错；
#       该函数统一输出可读说明，便于日志定位。
# =============================================================================
read_resp_file() {
  local f="${1:-gh_resp.json}"
  if [ -s "$f" ]; then
    head -c 300 "$f"
  else
    echo "(无——网络层失败，已按 HTTP 500 进入重试)"
  fi
}

# =============================================================================
# 快速自检（开发调试用）: source 后执行 `_release_shell_lib_selfcheck`
# =============================================================================
_release_shell_lib_selfcheck() {
  echo "curl_http_code(正常) -> $(curl_http_code -s -o /dev/null -w '%{http_code}' --max-time 2 https://example.com || true)"
  echo "safe_num_or_zero('12')   -> $(safe_num_or_zero 12)"
  echo "safe_num_or_zero('ab12') -> $(safe_num_or_zero ab12)"
  echo "gh_api_len(gh 缺失)      -> $(gh_api_len repo/x/check-runs '[] | length' 2>/dev/null)"
}
