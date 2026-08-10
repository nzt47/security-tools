#!/usr/bin/env python3
"""
串行复现脚本 — test_metrics_modules_registered 失败（排除 xdist 干扰）

【背景】
CI（run 31322544891, Python 3.11/Shard 3, xdist gw0）中
tests/unit/test_singleton_manager.py::test_metrics_modules_registered 失败：
    assert is_registered("auto_tuner") → AssertionError: assert False
同 run 仅 3.11/Shard 3 失败，3.10/3.12 其它 shard 未报 → 疑似 xdist 分片隔离问题。

【本脚本两个模式】
  模式 A（串行复现）:  -p no:xdist 串行运行目标测试文件
     - 通过    → 确认 xdist 分片/并行隔离问题（非代码逻辑缺陷）
     - 失败    → 代码逻辑缺陷，继续用模式 B 定位
  模式 B（状态探测）:  模拟 CI 导入序列，打印注册状态与 _manager 身份
     - 验证是否出现两个 SingletonManager._manager 实例（模块重载/双份 sys.modules）
     - 验证 reset_all_singletons 是否破坏注册表（is_registered 前置检查）

【用法】
  python scripts/repro_singleton_metrics_registered.py --mode A
  python scripts/repro_singleton_metrics_registered.py --mode B
  python scripts/repro_singleton_metrics_registered.py --mode B --reset-before

【归属】BUG-20260809-001（详见 docs/zh/知识库重构计划/BUG_TRACKER_test_metrics_modules_registered_20260809.md）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 脚本以 `python scripts/xxx.py` 运行时 sys.path[0] 是 scripts/ 目录，
# 需显式将项目根加入 sys.path，否则 `import agent` 失败。
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 与 CI 测试一致的导入序列（按测试文件 L216-221）
TARGET_MODULES = [
    "agent.auto_tuner",
    "agent.monitoring.error_reporter",
    "agent.monitoring.optimized_metrics",
    "agent.monitoring.tracing_cache",
]
EXPECTED_NAMES = [
    "auto_tuner",
    "error_reporter",
    "optimized_metrics_collector",
    "trace_cache",
]


def mode_a_serial_pytest(verbose: bool) -> int:
    """模式 A：串行运行目标测试文件（排除 xdist）。"""
    cmd = [sys.executable, "-m", "pytest", "-p", "no:xdist", "-q", "--tb=short"]
    if verbose:
        cmd.append("-v")
    cmd.append(str(ROOT / "tests" / "unit" / "test_singleton_manager.py"))
    print(f"$ {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=str(ROOT))
    if proc.returncode == 0:
        print("\n[结论] 串行通过 → 确认 xdist 分片/并行隔离问题（建议模式 B 探测污染源）")
    else:
        print("\n[结论] 串行仍失败 → 代码逻辑缺陷，请运行 --mode B 定位")
    return proc.returncode


def _probe_import_state(reset_before: bool) -> None:
    """核心探测：导入序列 + _manager 身份 + 注册状态。"""
    import importlib
    import sys as _sys

    # 0) 前置快照：singleton_manager 是否已被加载
    pre_loaded = "agent.utils.singleton_manager" in _sys.modules
    print(f"[0] 探测前 agent.utils.singleton_manager 已加载: {pre_loaded}")

    # 1) 模拟测试导入序列
    for mod in TARGET_MODULES:
        importlib.import_module(mod)
        print(f"[1] import {mod} OK")

    # 2) 检查注册状态
    from agent.utils.singleton_manager import is_registered
    print("\n[2] 注册状态：")
    for name in EXPECTED_NAMES:
        print(f"    is_registered({name!r}) = {is_registered(name)}")

    # 3) _manager 身份检查：是否存在多实例（双份模块）
    import agent.utils.singleton_manager as sm
    mgr_id = id(sm._manager)
    print(f"\n[3] 当前 singleton_manager 模块: id={id(sm)}, path={sm.__file__}")
    print(f"    _manager 实例 id={mgr_id}")
    # 遍历 sys.modules 查找同模块多副本（不同路径加载）
    dupes = [
        (name, m.__file__)
        for name, m in _sys.modules.items()
        if name == "agent.utils.singleton_manager"
    ]
    print(f"    sys.modules 中 singleton_manager 条目数: {len(dupes)}")
    for name, path in dupes:
        print(f"      {name} -> {path}")
    # 校验注册使用的模块与查询模块是否同一 _manager
    import agent.auto_tuner as at
    at_mgr = getattr(at, "_manager", None)
    if at_mgr is None:
        at_get_singleton = getattr(at, "get_singleton", None)
        if at_get_singleton is not None:
            at_mgr = at_get_singleton.__globals__.get("_manager", None)
    print(f"    auto_tuner 侧可见 _manager id: {id(at_mgr) if at_mgr else 'N/A'}")
    if at_mgr is not None and id(at_mgr) != mgr_id:
        print("    ⚠️ 检测到两个不同 _manager 实例 → 注册/查询跨实例，根因实锤")
    elif at_mgr is None:
        print("    ⚠️ auto_tuner 未持有 _manager → 注册逻辑缺失（实现未同步）")
    else:
        print("    _manager 单实例一致（无多实例化）")

    # 4) reset 语义验证：reset_all 后注册表是否保留
    if reset_before:
        from agent.utils.singleton_manager import reset_all_singletons
        print("\n[4] 执行 reset_all_singletons() 后：")
        for name in EXPECTED_NAMES:
            print(f"    is_registered({name!r}) = {is_registered(name)}")
        from agent.utils.singleton_manager import is_initialized
        print("    is_initialized 检查（应为 False，实例已重置）:")
        for name in EXPECTED_NAMES:
            print(f"    is_initialized({name!r}) = {is_initialized(name)}")


def mode_b_probe(reset_before: bool) -> int:
    """模式 B：状态探测。"""
    try:
        _probe_import_state(reset_before)
    except Exception as exc:  # noqa: BLE001 - 探测脚本需捕获全部异常
        print(f"\n[ERROR] 探测过程异常: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
        return 1
    print(f"\n[sysconfig] platform={sysconfig.get_platform()}, python={sys.version.split()[0]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=["A", "B"], required=True,
        help="A=串行复现 pytest; B=状态探测",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="模式 A 下 verbose 输出",
    )
    parser.add_argument(
        "--reset-before", action="store_true",
        help="模式 B 下：探测后执行 reset_all_singletons 并复查注册表",
    )
    args = parser.parse_args()

    if args.mode == "A":
        return mode_a_serial_pytest(args.verbose)
    return mode_b_probe(args.reset_before)


if __name__ == "__main__":
    sys.exit(main())
