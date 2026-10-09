# 分身侧 CLI 对端的参考镜像（container 执行后端用）
#
# 为什么是最小镜像：对端默认只做协议往返（离线回执），不装任何仓库依赖 ——
# `import agent.subagent.channel` 会拉起 agent/subagent/__init__.py 的重导入链
# （executor / lifecycle / memory），在只装标准库的瘦镜像里直接 ImportError，
# 所以 scripts/subagent_peer.py 刻意零仓库导入（对拍守卫见 tests/unit/）。
#
# 构建：docker build -f docker/subagent-peer/Dockerfile -t yunshu-subagent-peer:1 .
# 配套（在母体 .env / 部署配置里）：
#   CP_SUBAGENT_CONTAINER_IMAGE=yunshu-subagent-peer:1
#   CP_SUBAGENT_AGENT_CLI="python /peer/subagent_peer.py"
#
# 不设 ENTRYPOINT：container 后端把完整 argv 放在镜像名之后（docker 视其为 command）。
# 若这里再设 ENTRYPOINT，会拼成 `python /peer/subagent_peer.py python /peer/... -p ...`，
# 多出一个位置参数，argparse 直接报错。
FROM python:3.12-slim

COPY scripts/subagent_peer.py /peer/subagent_peer.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR /work
