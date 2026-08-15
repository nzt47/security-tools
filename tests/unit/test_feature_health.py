"""feature.new-dev 开发起点 — _classify_health 边界条件测试

覆盖:
    1. 健康阈值 0.8 精确命中 -> healthy
    2. 0.8 下方临界 -> degraded
    3. 退化阈值 0.5 精确命中 -> degraded
    4. 0.5 下方临界 -> critical
    5. 满分 1.0 / 超界 1.5 -> healthy
    6. 零分 0.0 / 负值 -0.1 -> critical

守【不易】: 纯函数测试不触碰 agent 业务依赖; feature.py 在仓库根
（非包），pytest 未将根目录加入 sys.path 时需显式插入。
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from feature import _classify_health  # noqa: E402


def test_exactly_healthy_threshold():
    """0.8 精确命中 healthy 下界。"""
    assert _classify_health(0.8) == "healthy"


def test_just_below_healthy_threshold():
    """0.7999 -> degraded。"""
    assert _classify_health(0.7999) == "degraded"


def test_exactly_degraded_threshold():
    """0.5 精确命中 degraded 下界。"""
    assert _classify_health(0.5) == "degraded"


def test_just_below_degraded_threshold():
    """0.4999 -> critical。"""
    assert _classify_health(0.4999) == "critical"


def test_perfect_score():
    """1.0 -> healthy。"""
    assert _classify_health(1.0) == "healthy"


def test_zero_score():
    """0.0 -> critical。"""
    assert _classify_health(0.0) == "critical"


def test_negative_score():
    """负值（防御性输入）-> critical。"""
    assert _classify_health(-0.1) == "critical"


def test_above_range_score():
    """超界 1.5（防御性输入）-> healthy。"""
    assert _classify_health(1.5) == "healthy"
