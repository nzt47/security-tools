#!/bin/sh
# 本地推理对端的启动包装：先起 ollama serve，再把 container 后端传入的 argv 原样 exec。
# 【为什么需要它】container 后端通过 image 之后的位置参数传完整 argv；不先起 ollama，
# handler 会因连不上而 fail-closed（对端非零退出）。
set -e
OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_HOST
# 【为什么日志不写 /tmp】container 后端以 --read-only + 仅 tmpfs /work 运行，根文件系统的
# /tmp 不可写（实测 --read-only 下 `touch /tmp/x` 报 EROFS）。日志若重定向到 /tmp，后台的
# `ollama serve` 重定向直接失败、服务根本不会启动，对端随后必然非零退出——"全离线"跑不起来。
# 因此日志落到可写的 HOME（镜像里 HOME=/work，即那个 tmpfs）；HOME 也不可写时退回 /dev/null。
LOG_DIR="${HOME:-/tmp}"
mkdir -p "$LOG_DIR" 2>/dev/null || true
LOG="$LOG_DIR/ollama.log"
touch "$LOG" 2>/dev/null || LOG=/dev/null
ollama serve >"$LOG" 2>&1 &
for i in $(seq 1 60); do
  if ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
exec "$@"
