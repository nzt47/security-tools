#!/usr/bin/env python3
"""CI 专用模块运行器 — 绕过 agent/__init__.py 的重型导入链

背景：
    agent/__init__.py 导入 digital_life 等，间接依赖 tiktoken/watchdog/psutil
    等第三方库。CI 环境只安装了轻量依赖，导致 `python -m agent.observability.*`
    运行时 ImportError。

方案：
    创建空的 agent 包占位符，跳过 agent/__init__.py 的执行，
    然后直接导入 agent.observability.* 子模块（它们只依赖标准库 + agent 内部模块）。

使用方法：
    python scripts/ci_run_module.py agent.observability.arch_rules --check --root agent ...
    python scripts/ci_run_module.py agent.observability.dependency_graph --root agent ...
"""
from __future__ import annotations

import importlib
import sys
import types

# 创建空的 agent 包占位符，阻止 Python 执行 agent/__init__.py
_agent_stub = types.ModuleType("agent")
_agent_stub.__path__ = ["agent"]
sys.modules["agent"] = _agent_stub

# 同样为 agent.observability 创建占位符（如果 __init__.py 有重型导入）
_obs_stub = types.ModuleType("agent.observability")
_obs_stub.__path__ = ["agent/observability"]
sys.modules["agent.observability"] = _obs_stub


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/ci_run_module.py <module.name> [args...]")
        print("示例: python scripts/ci_run_module.py agent.observability.arch_rules --check")
        return 2

    module_name = sys.argv[1]
    cli_args = sys.argv[2:]

    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        print(f"[ci_run_module] 导入失败: {module_name} → {e}")
        return 1

    if not hasattr(mod, "main"):
        print(f"[ci_run_module] 错误: {module_name} 没有 main() 函数")
        return 1

    # 设置 sys.argv 让被调用模块的 argparse 能正确解析参数
    sys.argv = [module_name] + cli_args
    return mod.main()


if __name__ == "__main__":
    sys.exit(main())
