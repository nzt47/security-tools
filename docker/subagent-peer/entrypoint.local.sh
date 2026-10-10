#!/bin/sh
# 本地推理对端的启动包装：先起 ollama serve，再把 container 后端传入的 argv 原样 exec。
# 【为什么需要它】container 后端通过 `image` 之后的位置参数传完整 argv（python /peer/... -p ...）；
# 若不先起 ollama，handler 会因连不上而 fail-closed（对端非零退出）。
set -e
ollama serve >/tmp/ollama.log 2>&1 &
for i in $(seq 1 30); do
  if ollama list >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
exec "$@"
