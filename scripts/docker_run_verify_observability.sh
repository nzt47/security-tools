#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════
#  可观测性验证脚本 Docker 启动命令
#  目标：在本地容器中跑一遍 scripts/verify_observability_fields.py
#        全链路验证 retrieved_chunks / eval_score / health / metrics 4 个节点
#
#  设计【不易】基于现有 Dockerfile.test，不引入新镜像
#       【变易】--entrypoint python 覆盖 pytest 入口
#       【简易】单条 build + 单条 run，最少认知负担
# ════════════════════════════════════════════════════════════
set -euo pipefail

# 0. 切到项目根目录（脚本所在目录的上一级）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE_NAME="yunshu-verify-obs"
IMAGE_TAG="latest"
CONTAINER_NAME="yunshu-verify-obs-run"

echo "==> [1/3] 构建镜像 $IMAGE_NAME:$IMAGE_TAG"
docker build -f Dockerfile.test -t "$IMAGE_NAME:$IMAGE_TAG" .

echo "==> [2/3] 运行验证脚本（覆盖 entrypoint）"
# 关键参数说明：
#   --rm                   容器退出后自动清理
#   -v test_results         挂载结果卷（Dockerfile.test 已声明 VOLUME）
#   --entrypoint python    覆盖原 pytest ENTRYPOINT
#   scripts/verify_observability_fields.py  作为 python 的参数
mkdir -p test_results
docker run --rm \
  --name "$CONTAINER_NAME" \
  -v "${PWD}/test_results:/app/test_results" \
  -e PYTHONPATH=/app \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONUNBUFFERED=1 \
  --entrypoint python \
  "$IMAGE_NAME:$IMAGE_TAG" \
  scripts/verify_observability_fields.py

echo "==> [3/3] 验证完成，退出码 $?"
echo "如需保留容器排查：去掉 --rm 并加 -it 进入交互模式"
echo "如需查看测试结果：cat test_results/*.log"
