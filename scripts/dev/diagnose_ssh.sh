#!/usr/bin/env bash
# SSH 连接超时自动诊断（运维排错指南 §一 的自动执行版）
#
# 分层诊断：DNS 解析 → ping 可达性 → TCP 端口 → SSH banner → SSH 认证
# 每层输出 PASS/FAIL + 建议，最后汇总。
#
# 用法：
#   ./diagnose_ssh.sh --host <ip|域名> [--port 22] [--user root] [--key ~/.ssh/id_rsa]
#   ./diagnose_ssh.sh --host 10.0.0.1 --port 2222 --user deploy --key ~/.ssh/id_rsa
#
# 退出码：0 = 全部通过；1 = 存在 FAIL（定位到问题层）
set -uo pipefail

HOST=""
PORT=22
USER=""
KEY=""
FAILED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --user) USER="$2"; shift 2 ;;
    --key) KEY="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
[[ -n "$HOST" ]] || { echo "[FAIL] 需 --host" >&2; exit 2; }

echo "== SSH 连接诊断：${HOST}:${PORT} =="
echo ""

# ping 兼容（Linux -c / Windows -n）
do_ping() {
  ping -c 1 -W 2 "$HOST" >/dev/null 2>&1 || ping -n 1 -w 2000 "$HOST" >/dev/null 2>&1
}

# 1) DNS 解析
echo "[1/5] DNS 解析"
if [[ "$HOST" =~ ^[0-9.]+$ ]]; then
  echo "  [PASS] ${HOST} 为 IP 直连（跳过 DNS）"
else
  if getent hosts "$HOST" >/dev/null 2>&1 || nslookup "$HOST" >/dev/null 2>&1; then
    echo "  [PASS] ${HOST} 解析正常（$(getent hosts "$HOST" | head -1 | awk '{print $1}')）"
  else
    echo "  [FAIL] ${HOST} 无法解析（域名拼写 / DNS 配置）"
    FAILED=1
  fi
fi

# 2) ping 可达性
echo "[2/5] ping 可达性"
if do_ping; then
  echo "  [PASS] 网络可达（ping 通）"
else
  echo "  [FAIL] ping 不通 —— 网络不可达或 ICMP 被禁"
  echo "    建议: 检查路由/VPN/防火墙；ICMP 被禁时以下端口测试更有意义"
fi

# 3) TCP 端口连通性
echo "[3/5] TCP 端口 ${PORT}"
echo "  [log] 目标 ${HOST}:${PORT}；方法 /dev/tcp 探测；超时 5s"
echo "  [log] 探测命令: timeout 5 bash -c \"echo >/dev/tcp/${HOST}/${PORT}\""
echo "  [log] 若此层卡住：主机不可达 / 防火墙拦截 / 端口未监听"
if timeout 5 bash -c "echo >/dev/tcp/$HOST/$PORT" 2>/dev/null; then
  echo "  [PASS] TCP ${PORT} 端口开放"
else
  echo "  [FAIL] TCP ${PORT} 端口不可达（连接超时/拒绝）"
  echo "    建议: ① 防火墙/安全组放行 ${PORT}（云服务器安全组 + 系统防火墙）"
  echo "          ② 确认 sshd 监听: netstat -tlnp | grep :${PORT}"
  echo "          ③ 非标准端口用 --port ${PORT} 对齐"
  FAILED=1
fi

# 4) SSH banner（sshd 是否响应）
echo "[4/5] SSH 服务响应"
echo "  [log] 读取 ${HOST}:${PORT} 首个响应（banner）；超时 5s；期望前缀 SSH-2.0（RFC 4253）"
echo "  [log] 读取命令: timeout 5 bash -c \"exec 3<>/dev/tcp/${HOST}/${PORT}; head -c 200 <&3\""
echo "  [log] 若此层卡住：端口上非 SSH 服务 / sshd 未运行 / banner 格式异常"
BANNER=$(timeout 5 bash -c "exec 3<>/dev/tcp/$HOST/$PORT; head -c 200 <&3" 2>/dev/null)
if [[ "$BANNER" == SSH-* ]]; then
  echo "  [PASS] sshd 响应 banner: $(echo "$BANNER" | head -1 | cut -c1-60)"
else
  echo "  [FAIL] 未收到 SSH banner（${PORT} 上非 SSH 服务 / sshd 未运行）"
  echo "    建议: 确认目标端口确为 SSH；服务端 systemctl status sshd"
  FAILED=1
fi

# 5) SSH 认证（可选，需 --user/--key）
echo "[5/5] SSH 认证"
if [[ -n "$USER" && -n "$KEY" ]]; then
  if [[ ! -f "$KEY" ]]; then
    echo "  [FAIL] 密钥文件不存在: $KEY"
    FAILED=1
  else
    out=$(ssh -p "$PORT" -o ConnectTimeout=5 -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
          -i "$KEY" "${USER}@${HOST}" "echo auth_ok" 2>&1)
    code=$?
    if [[ $code -eq 0 ]]; then
      echo "  [PASS] 认证成功（${USER}@${HOST}）"
    elif echo "$out" | grep -q "Permission denied"; then
      echo "  [FAIL] 认证被拒（Permission denied）"
      echo "    建议: ① 密钥是否正确（--key 指向私钥） ② 私钥权限需 600: chmod 600 $KEY"
      echo "          ③ 用户名拼写（$USER） ④ 服务端是否允许密钥登录"
      FAILED=1
    elif echo "$out" | grep -q "Host key verification failed"; then
      echo "  [FAIL] Host key 校验失败"
      echo "    建议: 清除 known_hosts 冲突条目: ssh-keygen -R ${HOST}"
      FAILED=1
    else
      echo "  [FAIL] 认证阶段异常（exit=$code）: $(echo "$out" | grep -iE 'error|denied|closed' | head -1)"
      FAILED=1
    fi
  fi
else
  echo "  [SKIP] 未提供 --user/--key，跳过认证测试（连接/服务层已覆盖）"
fi

echo ""
if [[ "$FAILED" -eq 0 ]]; then
  echo "[OK] 全部诊断通过（${HOST}:${PORT}）"
  exit 0
fi
echo "[FAIL] 存在失败项，按上方 [FAIL] 层的建议排查"
exit 1
