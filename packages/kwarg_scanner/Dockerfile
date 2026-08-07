# kwarg-scanner Dockerfile — 关键字参数冲突扫描器镜像
#
# 构建:
#   docker build -t kwarg-scanner:latest ./packages/kwarg_scanner
#
# 运行（CI 模式，默认扫描 /project，HIGH 风险阻断）:
#   docker run --rm -v "$(pwd):/project" kwarg-scanner
#
# 自定义参数:
#   docker run --rm -v "$(pwd):/project" kwarg-scanner --min-risk MEDIUM
#   docker run --rm -v "$(pwd):/project" -e OUTPUT_FORMAT=json kwarg-scanner
#
# 健康检查:
#   docker run --rm kwarg-scanner --health
#
# CI 集成 (GitHub Actions):
#   - run: docker run --rm -v "${{ github.workspace }}:/project" kwarg-scanner
#
# 环境变量:
#   SCAN_PATH        — 扫描路径（默认 /project）
#   MIN_RISK         — 最低风险等级 LOW/MEDIUM/HIGH（默认 HIGH）
#   OUTPUT_FORMAT    — 输出格式 text/json/sonarqube（默认 text）
#   OUTPUT_FILE      — 输出文件路径（可选）
#   ENABLE_LOGGING   — 启用结构化日志 true/false（默认 false）

# ════════════════════════════════════════════════════════════
# 阶段 1: builder — 预构建 wheel，缓存构建依赖
# 优化点: 基础镜像无 setuptools，pip 构建隔离会每次下载依赖（~34s）。
#         本阶段预装构建依赖并生成 wheel，避免运行时重复下载。
# ════════════════════════════════════════════════════════════
FROM python:3.12-slim AS builder

# 预装构建依赖（仅首次构建下载，后续利用层缓存）
RUN pip install --no-cache-dir setuptools wheel

WORKDIR /build

# 先复制包描述文件（pyproject.toml 变更才使该层失效）
COPY pyproject.toml /build/
# 再复制源码（源码变更只影响该层 + wheel 层）
COPY kwarg_scanner/ /build/kwarg_scanner/

# 使用 --no-build-isolation 跳过重复下载构建依赖，生成纯 wheel
RUN pip wheel --no-cache-dir --no-build-isolation --no-deps \
    -w /tmp/wheels /build/ \
    && rm -rf /build

# ════════════════════════════════════════════════════════════
# 阶段 2: runtime — 精简运行时，从本地 wheel 安装
# ════════════════════════════════════════════════════════════
FROM python:3.12-slim AS runtime

# OCI 标准标签
LABEL org.opencontainers.image.title="kwarg-scanner" \
      org.opencontainers.image.description="Python 关键字参数冲突风险静态扫描器（CI 流水线专用镜像）" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.source="https://github.com/yunshu/kwarg-scanner" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.usage="docker run --rm -v $(pwd):/project kwarg-scanner"

# 创建非 root 用户（安全最佳实践）
RUN groupadd -r scanner && useradd -r -g scanner -d /home/scanner -m scanner

# 设置工作目录（CI 挂载点）
WORKDIR /project

# 从 builder 阶段复制 wheel 并离线安装（--no-index 无网络、无构建）
COPY --from=builder --chown=scanner:scanner /tmp/wheels/ /tmp/wheels/
RUN pip install --no-cache-dir --no-index --find-links=/tmp/wheels kwarg-scanner \
    && rm -rf /tmp/wheels \
    && pip cache purge

# 复制 CI 入口脚本
COPY --chown=scanner:scanner docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# 强制去除 CRLF:Windows 编辑器可能写入 \r\n,导致 Linux 容器
# shebang #!/bin/sh\r 找不到解释器(exec: no such file or directory)
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh

# 切换到非 root 用户
USER scanner

# 健康检查（每 30s 一次，验证 kwarg-scan 命令可用）
# 对于 CLI 工具，HEALTHCHECK 主要用于服务化部署场景
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD docker-entrypoint.sh --health || exit 1

# 入口点: CI 包装脚本
# 该脚本会输出结构化 JSON 日志（trace_id/module_name/action/duration_ms），
# 捕获扫描器退出码并转换为 CI 友好的结果（0=通过, 1=阻断, 2=参数错误, 3=内部错误）
ENTRYPOINT ["docker-entrypoint.sh"]

# 默认参数: 无（使用环境变量控制）
# CI 直接 docker run 即可，无需传参
CMD []
