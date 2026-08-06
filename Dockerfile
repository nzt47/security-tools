# 预检工具包容器 — 守护 agent/memory_optimized 导入降级逻辑
#
# 轻量设计（变易）：预检全 mock，不依赖 chromadb/torch 等重库。因此只 COPY
# agent/ + tests/ 源码并安装 pytest，绝不执行 `pip install -e .`（项目依赖
# 含 torch 等约 3GB，容器无需背负；agent/preflight 仅依赖标准库 +
# agent.logging_utils，且 agent/__init__.py 采用 PEP 562 懒加载）。
#
# 用法：
#   docker build -t yunshu-preflight .
#   docker run --rm yunshu-preflight                        # 12 条导入路径
#   docker run --rm --entrypoint python yunshu-preflight -m pytest \
#       tests/unit/test_memory_optimized_import.py \
#       tests/unit/test_preflight_runner.py -q              # pytest 用例
#   PREFLIGHT_FAKE_FAIL=1 docker run --rm yunshu-preflight  # 故障演练（CI 阻断验证）

FROM python:3.12-slim

WORKDIR /app

# 仅复制预检所需源码（conftest 链：tests/conftest.py + tests/unit/conftest.py）
COPY agent/ /app/agent/
COPY tests/conftest.py /app/tests/conftest.py
COPY tests/unit/ /app/tests/unit/

RUN pip install --no-cache-dir pytest

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

ENTRYPOINT ["python", "-m", "agent.preflight"]
