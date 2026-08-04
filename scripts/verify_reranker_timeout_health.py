"""验证 ToolReranker.health()['rerank_timeout'] 随环境变量正确更新

覆盖优先级: 参数 > env(AGENT_RERANKER_TIMEOUT) > 默认(60.0)
含非法 env 回退场景。全部通过退出码 0，任一失败退出码 1（CI 可用）。

用法:
    python scripts/verify_reranker_timeout_health.py
"""

import os
import sys
from typing import Optional

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.tool_router_reranker import ToolReranker, _DEFAULT_RERANK_TIMEOUT

_ENV = "AGENT_RERANKER_TIMEOUT"


def _fresh(**kwargs) -> Optional[float]:
    """用指定 kwargs 新建 ToolReranker，返回 health()['rerank_timeout']"""
    r = ToolReranker(rerank_top_n=5, **kwargs)
    return r.health()["rerank_timeout"]


def main() -> int:
    cases = []
    failed = 0

    def run_case(name: str, expected: float, setup_env: str | None, **kwargs) -> None:
        nonlocal failed
        prev = os.environ.pop(_ENV, None)
        if setup_env is not None:
            os.environ[_ENV] = setup_env
        try:
            actual = _fresh(**kwargs)
            ok = actual == expected
        except Exception as e:  # noqa: BLE001
            ok, actual = False, f"异常: {e}"
        finally:
            if prev is not None:
                os.environ[_ENV] = prev
            else:
                os.environ.pop(_ENV, None)
        cases.append((name, expected, actual, ok))
        if not ok:
            failed += 1

    # 场景矩阵：参数 > env > 默认 + 非法回退
    run_case("无 env → 默认 60.0", _DEFAULT_RERANK_TIMEOUT, None)
    run_case("env=6.0 → 6.0", 6.0, "6.0")
    run_case("env=10.5 → 10.5", 10.5, "10.5")
    run_case("env=6.0 + 参数 8.5 → 参数优先 8.5", 8.5, "6.0", rerank_timeout=8.5)
    run_case("env=abc(非法) → 回退默认 60.0", _DEFAULT_RERANK_TIMEOUT, "abc")
    run_case("env=0 → 0.0(显式禁用超时)", 0.0, "0")

    print("rerank_timeout 配置优先级验证（参数 > env > 默认）")
    print(f"{'场景':<34}{'期望':<10}{'实际':<10}{'结果'}")
    print("-" * 66)
    for name, expected, actual, ok in cases:
        mark = "PASS" if ok else "FAIL"
        print(f"{name:<34}{expected!s:<10}{str(actual):<10}{mark}")
    print("-" * 66)
    print(f"通过 {len(cases) - failed}/{len(cases)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
