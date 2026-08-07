# release-sim Dockerfile — 发布流程模拟镜像（CI 环境外完全模拟发布依赖环境）
#
# 构建（context 必须是本目录）:
#   docker build -t release-sim:1.0.0 ./docker/release-sim
#
# 运行（环境自检）:
#   docker run --rm release-sim:1.0.0 --health
#
# 运行（挂载仓库跑发布模拟，容器内自带完整依赖）:
#   docker run --rm -v "$(pwd):/project" -w /project release-sim \
#     bash /project/scripts/your_sim.sh
#   容器内可直接调用 /project/scripts/update_changelog.py、/project/scripts/release_shell_lib.sh
#
# 运行（交互式进入容器）:
#   docker run --rm -it -v "$(pwd):/project" -w /project release-sim
#
# 镜像内预装发布依赖（对齐 GitHub Actions ubuntu-latest runner）:
#   git / python3 / pwsh（PowerShell 7.4）/ curl / gh（GitHub CLI）
#
# 环境变量:
#   CURL_MAX_TIME    — curl 超时秒数（默认 30，见 /project/scripts/release_shell_lib.sh）
#
# 对比本地 WSL 模拟（手册 §10 Q2）：容器内 bash/pwsh/python/curl 同一 Linux 环境、
# 网络栈一致，无 WSL interop 三坑；mock 服务器可在容器内起（python3 自带 http.server）。
# 注意：发布脚本（update_changelog.py / create_gitee_release.ps1 / release_shell_lib.sh）
#       不 COPY 进镜像，运行时通过 -v 挂载 —— 与 CI checkout 后跑仓库内脚本的模式一致。

FROM mcr.microsoft.com/powershell:7.4-ubuntu-22.04

LABEL org.opencontainers.image.title="release-sim" \
      org.opencontainers.image.description="发布流程模拟环境镜像（git/python3/pwsh/curl/gh 全依赖）" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.usage="docker run --rm -v $(pwd):/project -w /project release-sim"

# 非 root 用户（安全最佳实践）
RUN groupadd -r release && useradd -r -g release -d /home/release -m release

# gh 版本与下载源（构建参数，便于换版本/换加速源）
# 国内直连 cli.github.com / github.com 极慢（~30KB/s），默认走 gh-proxy 加速镜像
# （实测 3.4MB/s）；官方源: https://github.com
ARG GH_VERSION=2.97.0
ARG GH_MIRROR=https://gh-proxy.com/https://github.com

# 安装发布依赖: git / curl / python3 / gh
# powershell 基础镜像自带 pwsh（/usr/local/bin/pwsh）
RUN apt-get update && apt-get install -y --no-install-recommends \
      git curl ca-certificates python3 \
    && curl -fsSL "${GH_MIRROR}/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" \
       -o /tmp/gh.tar.gz \
    && tar -xzf /tmp/gh.tar.gz -C /tmp \
    && mv /tmp/gh_${GH_VERSION}_linux_amd64/bin/gh /usr/local/bin/gh \
    && chmod +x /usr/local/bin/gh \
    && rm -rf /tmp/gh.tar.gz /tmp/gh_${GH_VERSION}_linux_amd64 \
    && rm -rf /var/lib/apt/lists/*

# 复制 CI 入口脚本
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# 【不易】强制去除 CRLF:Windows 编辑器可能写入 \r\n,导致 Linux 容器 shebang 找不到解释器
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh

# 工作目录（宿主仓库挂载点）
WORKDIR /project

# 切换到非 root 用户
USER release

# 健康检查: 验证核心发布依赖可用
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD docker-entrypoint.sh --health || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]

# 默认进入 bash（交互或 -c 命令）
CMD ["bash"]
