"""feature/new-dev 开发起点。

业务逻辑: 调用现有健康看板 API（agent.health.dashboard.get_probe_overview）
获取五层探针原始状态, 转化为健康等级判定与可读报告。
作为分支首个真实业务功能, 验证与既有模块的联通。

数据流: get_probe_overview() → 等级判定(overall 阈值) → 报告汇总
"""

from agent.health.dashboard import get_probe_overview

# 健康等级阈值（与 dashboard 健康评分口径对齐）
_HEALTH_THRESHOLDS = ((0.8, "healthy"), (0.5, "degraded"))


def _classify_health(overall: float) -> str:
    """把 overall 评分映射为健康等级。

    >= 0.8 -> healthy; >= 0.5 -> degraded; 其余 -> critical
    """
    for threshold, label in _HEALTH_THRESHOLDS:
        if overall >= threshold:
            return label
    return "critical"


def business_entrypoint() -> dict:
    """调用健康看板探针概览 API, 附加业务健康等级判定。

    Returns:
        {
            "overall": float,
            "level": "healthy|degraded|critical",
            "issue_count": int,
            "unhealthy_layers": [层名, ...],
        }
    """
    overview = get_probe_overview()
    # layers 元素结构: {"layer", "score", "available", "detail"}（见
    # dashboard.get_probe_overview）; available=False 表示该层探针不可用
    unhealthy = [
        layer.get("layer", "?")
        for layer in overview.get("layers", [])
        if not layer.get("available", False)
    ]
    return {
        "overall": overview["overall"],
        "level": _classify_health(overview["overall"]),
        "issue_count": len(overview.get("issues", [])),
        "unhealthy_layers": unhealthy,
    }


if __name__ == "__main__":
    result = business_entrypoint()
    print(
        f"health={result['level']} overall={result['overall']:.3f} "
        f"issues={result['issue_count']} "
        f"unhealthy_layers={result['unhealthy_layers']}"
    )
