"""API 网关限流/配额验证（兼容旧入口）

已迁移至独立 CLI 模块 agent.api_gateway_cli（安装后可全局调用
`yunshu-gateway-check`）。本文件保留为薄壳，保持旧命令兼容：

    python scripts/gateway_health_check.py --unit-only

等价新命令:
    python -m agent.api_gateway_cli --unit-only
"""
import os
import sys

# scripts/ 目录下运行时 sys.path[0] 为 scripts/，需手动加入项目根以导入 agent 包
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.api_gateway_cli import main

if __name__ == "__main__":
    sys.exit(main())
