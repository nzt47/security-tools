"""集成测试收集配置

check_*.py 是运维检查脚本，在模块级直接发起 HTTP 请求连接 Prometheus。
当 Prometheus 未运行时会导致收集错误（ConnectionRefusedError）。

策略：默认跳过这些脚本的收集，通过环境变量 RUN_PROM_CHECKS=1 启用。
"""
import os

if os.environ.get("RUN_PROM_CHECKS", "0") != "1":
    collect_ignore = [
        "check_5xx_source.py",
        "check_baseline.py",
        "check_targets.py",
    ]
