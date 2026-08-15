"""feature/new-dev 开发起点。

调用现有健康看板 API（agent.health.dashboard.get_probe_overview）作为真实
业务入口：五层探针状态 + 健康评分。验证分支环境可联通既有模块，
后续新功能在此基础上迭代。
"""

from agent.health.dashboard import get_probe_overview


def business_entrypoint() -> dict:
    """调用健康看板探针概览 API，返回系统健康状态。

    Returns:
        结构: {"overall": float, "issues": list, "layers": list[dict]}
    """
    return get_probe_overview()


if __name__ == "__main__":
    result = business_entrypoint()
    print(
        f"overall={result['overall']} "
        f"issues={len(result['issues'])} "
        f"layers={len(result['layers'])}"
    )
