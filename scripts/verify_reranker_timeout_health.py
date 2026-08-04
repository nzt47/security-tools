"""验证 SkillReranker 的 rerank_timeout 随环境变量正确更新

真实实现: agent/skills_mgmt/reranker.py
    env: SKILL_RERANKER_RERANK_TIMEOUT（默认 _DEFAULT_RERANK_TIMEOUT = 3.0）
    __init__ 用 float(os.environ.get(...)) 解析, 非法值抛 ValueError

覆盖场景（参数 > env > 默认 不适用: 真实实现仅 env + 默认）:
    1. 无 env → 默认 3.0
    2. env=6.0 → 6.0
    3. env=10.5 → 10.5
    4. env=0 → 0.0(显式禁用超时)
    5. env=abc(非法) → 实例化抛 ValueError(不被静默接受)
    6. 实例间 env 隔离(不同 env 各自独立)

全部通过退出码 0, 任一失败退出码 1（CI 可用）。

用法:
    python scripts/verify_reranker_timeout_health.py
"""

import os
import sys

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.skills_mgmt.reranker import SkillReranker

_DEFAULT_RERANK_TIMEOUT = SkillReranker._DEFAULT_RERANK_TIMEOUT

_ENV = "SKILL_RERANKER_RERANK_TIMEOUT"


def _timeout() -> float:
    """实例化 SkillReranker 并返回 _rerank_timeout（模型懒加载, 实例化不拉模型）"""
    return SkillReranker()._rerank_timeout


def main() -> int:
    cases = []
    failed = 0

    def run_case(name: str, expected, setup_env, raises: bool = False) -> None:
        """执行单场景: 设置 env → 实例化 → 对比期望。raises=True 时期望 ValueError。"""
        nonlocal failed
        prev = os.environ.pop(_ENV, None)
        if setup_env is not None:
            os.environ[_ENV] = setup_env
        try:
            actual = _timeout()
            ok = (not raises) and (actual == expected)
            if raises:
                actual = "未抛异常"
        except ValueError:
            ok, actual = raises, "ValueError(预期)"
        except Exception as e:  # noqa: BLE001
            ok, actual = raises, f"异常: {type(e).__name__}: {e}"
        finally:
            if prev is not None:
                os.environ[_ENV] = prev
            else:
                os.environ.pop(_ENV, None)
        cases.append((name, expected, actual, ok))
        if not ok:
            failed += 1

    # 场景矩阵：默认 > env > 显式禁用 + 非法值 + 实例隔离
    run_case("无 env → 默认 3.0", _DEFAULT_RERANK_TIMEOUT, None)
    run_case("env=6.0 → 6.0", 6.0, "6.0")
    run_case("env=10.5 → 10.5", 10.5, "10.5")
    run_case("env=0 → 0.0(显式禁用超时)", 0.0, "0")
    run_case("env=abc(非法) → ValueError", "ValueError", "abc", raises=True)

    # 实例间 env 隔离：两个实例在不同 env 下各自正确
    os.environ[_ENV] = "8.0"
    a = _timeout()
    os.environ[_ENV] = "2.0"
    b = _timeout()
    os.environ.pop(_ENV, None)
    ok_iso = (a == 8.0 and b == 2.0)
    cases.append(("实例间 env 隔离(8.0/2.0)", (8.0, 2.0), (a, b), ok_iso))
    if not ok_iso:
        failed += 1

    print("rerank_timeout 配置验证（SKILL_RERANKER_RERANK_TIMEOUT, 默认 3.0）")
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
