"""预检 CLI — python -m agent.preflight 统一入口（CI 与本地一致）

退出码约定（不易）：0 = 全部通过；1 = 任一失败或故障演练触发。
故障演练开关为环境变量 PREFLIGHT_FAKE_FAIL（任意非空值），与 scripts/chromadb_preflight.*
及 ci.yml chromadb-preflight job 保持一致，便于 CI 阻断演练。
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from agent.preflight.runner import run_preflight

_FAKE_FAIL_ENV = "PREFLIGHT_FAKE_FAIL"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m agent.preflight",
        description="ChromaDB 导入降级预检（12 条路径，全 mock，无真实 chromadb）",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="显示 memory_optimized 决策日志（logging INFO，即 probe_start/probe_ok/ready 链）",
    )
    args = parser.parse_args(argv)

    if os.environ.get(_FAKE_FAIL_ENV):
        print(
            f"== 故障演练：{_FAKE_FAIL_ENV} 已设置，模拟预检失败（CI 中 unit-tests 将被 needs 阻断跳过）==",
            file=sys.stderr,
        )
        return 1

    if args.verbose:
        # 让 _create_client 的决策日志可见（默认 WARNING 会被过滤）
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    results = run_preflight()
    for result in results:
        print(str(result))

    failed = [r for r in results if not r.ok]
    if failed:
        print(f">>> 预检失败：{len(failed)}/{len(results)} 条路径未通过 ✗", file=sys.stderr)
        for r in failed:
            print(f"    {r}", file=sys.stderr)
        return 1

    print(f">>> {len(results)} 条导入路径与边界全部验证通过 ✓")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
