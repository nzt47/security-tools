"""回归守卫（R2-e / 审计 K8 + H-2）：路由装配失败不得静默。

【为什么值得单测】本仓有 22 处路由装配 try/except，而当前实际失败数为 0 —— 也就是说
这条路径平时根本没被走过。H-2 就是它触发一次的后果：agent/api_gateway_flask.py 缺失
导致 /api/open/* 与 /api/docs **整个 API 面没注册**，而进程照样报告健康。

测试分两层：
  ① 纯逻辑（agent.route_assembly，约 0ms，不导入 app_server）—— 默认从严、降级开关、
     失败时异常里必须带全部标签（否则排障时只知道"失败了"）。
  ② 接线守卫（静态扫描 app_server.py）—— 22 处失败点必须仍然走 _record_route_failure，
     防止有人改回 logger.error 而守卫浑然不觉。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent import route_assembly as ra

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _clean():
    ra.reset()
    old = os.environ.pop("YUNSHU_ROUTE_ASSEMBLY_STRICT", None)
    yield
    ra.reset()
    if old is None:
        os.environ.pop("YUNSHU_ROUTE_ASSEMBLY_STRICT", None)
    else:
        os.environ["YUNSHU_ROUTE_ASSEMBLY_STRICT"] = old


class TestRouteAssembly:
    def test_默认从严(self):
        assert ra.strict_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off", " off "])
    def test_显式降级(self, val, monkeypatch):
        monkeypatch.setenv("YUNSHU_ROUTE_ASSEMBLY_STRICT", val)
        assert ra.strict_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "yes", "on", ""])
    def test_其余取值仍从严(self, val, monkeypatch):
        monkeypatch.setenv("YUNSHU_ROUTE_ASSEMBLY_STRICT", val)
        assert ra.strict_enabled() is True

    def test_无失败时结算不抛(self):
        assert ra.finalize() == {"total": 0, "strict": True, "failed": []}
        assert "0 处失败" in ra.summary()

    def test_有失败且从严时拒绝启动并带全清单(self):
        ra.record("技能管理路由", ImportError("no module named x"))
        ra.record("能力层路由", RuntimeError("boom"))
        with pytest.raises(RuntimeError) as ei:
            ra.finalize()
        msg = str(ei.value)
        assert "技能管理路由" in msg and "能力层路由" in msg, "异常必须点名全部失败项"
        assert "ImportError" in msg and "RuntimeError" in msg
        assert "YUNSHU_ROUTE_ASSEMBLY_STRICT=0" in msg, "必须给出可操作的降级方式"

    def test_有失败但显式降级时可继续(self):
        ra.record("某路由", ValueError("x"))
        out = ra.finalize(strict=False)
        assert out["total"] == 1 and out["strict"] is False
        assert "某路由" in ra.summary()

    def test_failures_返回副本(self):
        ra.record("A", ValueError("x"))
        snap = ra.failures()
        snap.append({"label": "伪造", "error": "y"})
        assert len(ra.failures()) == 1, "failures() 必须返回副本，外部改不动内部状态"

    def test_reset_清空(self):
        ra.record("A", ValueError("x"))
        ra.reset()
        assert ra.failures() == []


class TestWiring:
    def test_失败点已全部改为登记(self):
        """22 处装配失败必须走 _record_route_failure，不得退回裸 logger.error。"""
        src = (ROOT / "app_server.py").read_text(encoding="utf-8", errors="replace")
        assert src.count("_record_route_failure(") >= 22, (
            "app_server.py 的装配失败登记点少于 22 —— 有人改回了 logger.error，"
            "失败又会变回静默（审计 H-2 的成因）。"
        )
        for bad in ["加载能力层路由失败", "加载 API 网关适配层失败"]:
            idx = src.find(bad)
            assert idx == -1, bad + " 仍是裸日志，未经登记"

    def test_装配结束有结算调用(self):
        src = (ROOT / "app_server.py").read_text(encoding="utf-8", errors="replace")
        assert "_finalize_route_assembly()" in src, "缺少装配结算调用，登记了也不会结算"