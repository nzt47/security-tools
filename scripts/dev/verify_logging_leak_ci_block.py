#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟 CI 阻断：故意漏写 finally 的 logging.disable 提交验证

验证目标：ci.yml code-quality job 中「logging.disable 泄漏扫描」step
（`--root . --only-under tests --exit-nonzero-on-risk`）能否正确报错阻断。

流程（与 CI 行为逐字对齐）:
1. 生成一个故意漏写 finally 的临时泄漏测试文件（tests/unit/test_tmp_ci_block_probe.py）
2. 以与 ci.yml step 完全相同的命令运行扫描器
3. 断言退出码非 0 —— CI 中该 step 会标红并阻断 workflow
4. 清理临时文件（无论结果，保证不留污染）

用法:
    python scripts/dev/verify_logging_leak_ci_block.py

退出码: 0 = 验证通过（CI 会正确阻断）；1 = 验证失败（扫描未拦截，需排查）。
"""

import subprocess
import sys
from pathlib import Path

PROBE_REL = Path("tests/unit/test_tmp_ci_block_probe.py")
# 与 ci.yml code-quality job 中该 step 逐字一致（repo root 为 cwd）
CI_COMMAND = [
    sys.executable, "scripts/check_logging_disable_leak.py",
    "--root", ".", "--only-under", "tests", "--exit-nonzero-on-risk",
]
PROBE_CONTENT = '''"""临时验证文件：故意漏写 finally 的 logging.disable 泄漏（验证后自动删除）

与 test_tmp_leak_probe 同构：未受 try/finally 保护的 disable 调用，
CI 的 logging.disable 泄漏扫描应将其识别并报错阻断。
"""

import logging


def test_leak():
    logging.disable(logging.WARNING)  # 故意泄漏：无 finally 恢复
    assert True
'''


def main():
    root = Path(__file__).resolve().parents[2]  # 仓库根（scripts/dev/ 上两级）
    probe = root / PROBE_REL

    print("=== CI 阻断模拟：故意漏写 finally 的 logging.disable ===")
    print(f"仓库根: {root}")

    # 1. 生成泄漏文件
    probe.parent.mkdir(parents=True, exist_ok=True)
    probe.write_text(PROBE_CONTENT, encoding="utf-8")
    print(f"[1/4] 已生成泄漏测试文件: {PROBE_REL}")

    # 2. 运行 CI 同款命令
    try:
        print(f"[2/4] 运行 CI 同款命令: {' '.join(CI_COMMAND)}")
        proc = subprocess.run(
            CI_COMMAND, cwd=root,
            capture_output=True, text=True, encoding="utf-8",
        )
        print(proc.stdout)
        if proc.stderr:
            print(proc.stderr, file=sys.stderr)
    finally:
        # 3. 清理（无论结果，防污染）
        if probe.exists():
            probe.unlink()
            print(f"[3/4] 已清理临时文件: {PROBE_REL}")

    # 4. 断言
    print(f"[4/4] 扫描退出码 = {proc.returncode}（CI step 期望非 0 才报错阻断）")
    if proc.returncode != 0:
        print("✅ 验证通过：CI 的 logging.disable 泄漏扫描会正确报错阻断该提交")
        return 0
    print("❌ 验证失败：扫描未拦截泄漏（退出码 0），请检查 ci.yml step 或扫描器配置")
    return 1


if __name__ == "__main__":
    sys.exit(main())
