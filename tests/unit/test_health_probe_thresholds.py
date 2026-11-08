# -*- coding: utf-8 -*-
"""L1 / L5 探针的判据回归（2026-11-01）。

【为什么改】2026-10-31 实测：全景看板「系统监控」里 Web 主服务 / API 网关 / 健康系统
三个节点**同时 warning（健康分 0.68）**。拆开看只有一个原因：

- L1 = 0.5：旧判据 `mem_pct > 85` 就扣 0.5 —— 本机 15GB、模型常驻，91% 是常态；
- L5 = 0.5：近 7 天反馈只有 **2 条**（1 like / 1 dislike）⇒ 满意度 50% 也照算。

本文件钉住新判据：
- L1 看**可用内存绝对值**（<1GB 扣 0.5 / <2GB 扣 0.2），百分比只在 >95% 的极端兜底；
- L5 **样本 < 5 不评分**（available=False，detail 说明样本不足），≥5 才按满意度打分。
"""
from __future__ import annotations

import sys

import pytest

from agent.health import probes


class _VM:
    def __init__(self, percent: float, available_gb: float):
        self.percent = percent
        self.available = int(available_gb * 1024 * 1024 * 1024)


class _Disk:
    def __init__(self, free_pct: float = 50.0):
        self.total = 100 * 1024 * 1024 * 1024
        self.free = int(self.total * free_pct / 100)


def _patch_psutil(monkeypatch, *, percent: float, available_gb: float,
                  cpu: float = 10.0, disk_free_pct: float = 50.0):
    import psutil

    monkeypatch.setattr(psutil, "virtual_memory", lambda: _VM(percent, available_gb))
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: cpu)
    monkeypatch.setattr(psutil, "disk_usage", lambda _p: _Disk(disk_free_pct))
    monkeypatch.setattr(psutil.Process, "threads", lambda self: [object()] * 4)


class TestL1MemoryJudgement:

    def test_高百分比但内存充足_不再扣分(self, monkeypatch):
        """本机常态：91% 但还剩 3GB ⇒ 1.0（旧判据会扣到 0.5）"""
        _patch_psutil(monkeypatch, percent=91.0, available_gb=3.0)
        r = probes.probe_l1_process()
        assert r.available is True
        assert r.score == 1.0, r.detail
        assert "avail=3072MB" in r.detail

    def test_可用内存低于_512MB_扣一半(self, monkeypatch):
        _patch_psutil(monkeypatch, percent=97.0, available_gb=0.4)
        r = probes.probe_l1_process()
        assert r.score == 0.5, r.detail

    def test_可用内存_512MB_到_1GB_扣_0_2(self, monkeypatch):
        """实测 01:20 avail≈1019MB：偏紧但可服务 ⇒ 0.8，不该直接判 0.5"""
        _patch_psutil(monkeypatch, percent=93.0, available_gb=0.8)
        r = probes.probe_l1_process()
        assert r.score == pytest.approx(0.8), r.detail

    def test_可用内存_1GB_以上不扣分(self, monkeypatch):
        _patch_psutil(monkeypatch, percent=88.0, available_gb=1.5)
        r = probes.probe_l1_process()
        assert r.score == 1.0, r.detail

    def test_百分比极端_95_仍兜底扣分(self, monkeypatch):
        """内存充足但占用 96%：保底 -0.2（防"available 虚高"）"""
        _patch_psutil(monkeypatch, percent=96.0, available_gb=3.0)
        r = probes.probe_l1_process()
        assert r.score == pytest.approx(0.8), r.detail

    def test_cpu_过高仍扣分(self, monkeypatch):
        _patch_psutil(monkeypatch, percent=50.0, available_gb=6.0, cpu=95.0)
        r = probes.probe_l1_process()
        assert r.score == 0.5, r.detail

    def test_磁盘吃紧仍扣分(self, monkeypatch):
        _patch_psutil(monkeypatch, percent=50.0, available_gb=6.0, disk_free_pct=4.0)
        r = probes.probe_l1_process()
        assert r.score == pytest.approx(0.7), r.detail


class _Rec:
    def __init__(self, feedback_type: str):
        self.feedback_type = feedback_type


class _FakeManager:
    def __init__(self, records):
        self._records = records

    def list_feedback(self, limit=200):  # noqa: ARG002
        return list(self._records)


def _patch_feedback(monkeypatch, types):
    records = [_Rec(t) for t in types]

    class _Mgr:
        def __init__(self):
            self._r = records

        def list_feedback(self, limit=200):  # noqa: ARG002
            return list(self._r)

    fake_mod = type(sys)("agent.feedback")
    fake_mod.FeedbackManager = _Mgr
    monkeypatch.setitem(sys.modules, "agent.feedback", fake_mod)


class TestL5SampleGate:

    def test_两条反馈不评分(self, monkeypatch):
        """2026-10-31 的真实输入：2 条（1 like / 1 dislike）⇒ 不评分，而不是 0.5"""
        _patch_feedback(monkeypatch, ["like", "dislike"])
        r = probes.probe_l5_semantic()
        assert r.available is False
        assert r.score is None
        assert "样本不足" in r.detail and "2" in r.detail

    def test_五条起才评分(self, monkeypatch):
        _patch_feedback(monkeypatch, ["like", "like", "like", "like", "dislike"])
        r = probes.probe_l5_semantic()
        assert r.available is True
        assert r.score == pytest.approx(0.8)

    def test_恰好四条仍不评分(self, monkeypatch):
        _patch_feedback(monkeypatch, ["like"] * 4)
        r = probes.probe_l5_semantic()
        assert r.available is False and r.score is None

    def test_无反馈沿用旧口径_无数据(self, monkeypatch):
        _patch_feedback(monkeypatch, [])
        r = probes.probe_l5_semantic()
        assert r.available is False and "无用户反馈" in r.detail


class TestOverallAfterFix:
    """三条 warning 的根因回归：L1=1.0 / L2=1.0 / L5 不参与 ⇒ 整体回到健康区"""

    def test_只余_l1_l2_时整体为_1(self, monkeypatch):
        from agent.health.assessor import HealthAssessor
        from agent.health.probes import ProbeResult

        probes_map = {
            "l1_process": ProbeResult("l1_process", 1.0, "mem=91.0% avail=3072MB", True),
            "l2_dependency": ProbeResult("l2_dependency", 1.0, "configs 2/2", True),
            "l3_llm_tool": ProbeResult("l3_llm_tool", None, "无数据", False),
            "l4_business": ProbeResult("l4_business", None, "无数据", False),
            "l5_semantic": ProbeResult("l5_semantic", None, "样本不足（2 条）", False),
        }
        score = HealthAssessor().assess_with_probes(probes_map)
        assert score.overall == pytest.approx(1.0)

class TestL5SatisfactionDenominator:
    """L5 满意度口径（2026-11-08）：只数近 7 天的 like/dislike。

    背景（Operator 实测）：仪表盘 L5 从 50% 掉到 11.1%。根因两条 ——
    ① 分母含 suggestion（「开发随记」7 条），等于"没点赞就算差评"；
    ② 不看时间窗，7 月的 1 条 like/1 条 dislike 一直留在分母里。
    """

    def test_建议与开发随记不计入满意度(self, monkeypatch):
        _patch_feedback(monkeypatch, ["like"] * 5 + ["suggestion"] * 7)
        r = probes.probe_l5_semantic()
        assert r.available is True
        assert r.score == pytest.approx(1.0), r.detail
        assert "7 条建议/随记不计入满意度" in r.detail, r.detail
        assert r.metrics and r.metrics["suggestions"] == 7

    def test_只有建议时如实报无数据而不是0分(self, monkeypatch):
        _patch_feedback(monkeypatch, ["suggestion"] * 7)
        r = probes.probe_l5_semantic()
        assert r.available is False and r.score is None
        assert "没有 like/dislike" in r.detail
        assert "7 条建议" in r.detail

    def test_超出7天窗口的反馈被排除(self, monkeypatch):
        import time as _time

        old = _time.time() - 30 * 86400
        records = []
        for t in ["like"] * 5:
            rec = _Rec(t)
            rec.created_at = old
            records.append(rec)
        fresh = _Rec("dislike")
        fresh.created_at = _time.time() - 3600
        records.append(fresh)

        class _Mgr:
            def __init__(self):
                self._r = records

            def list_feedback(self, limit=200):  # noqa: ARG002
                return list(self._r)

        fake_mod = type(sys)("agent.feedback")
        fake_mod.FeedbackManager = _Mgr
        monkeypatch.setitem(sys.modules, "agent.feedback", fake_mod)

        r = probes.probe_l5_semantic()
        # 窗口内只剩 1 条 dislike ⇒ 样本不足；30 天前那 5 条要被明确计入 stale
        assert r.available is False and r.score is None
        assert "样本不足" in r.detail and "stale" not in r.detail
        assert r.metrics["stale"] == 5, r.metrics

    def test_窗口内五条_按点赞比例评分(self, monkeypatch):
        _patch_feedback(monkeypatch, ["like"] * 4 + ["dislike"] * 2)
        r = probes.probe_l5_semantic()
        assert r.available is True
        assert r.score == pytest.approx(round(4 / 6, 2))
        assert "satisfaction=66.7%" in r.detail

    def test_时间解析不了不算过期(self, monkeypatch):
        """`_Rec` 这类没有 created_at 的替身：不能因为"看不出时间"就被丢掉。"""
        _patch_feedback(monkeypatch, ["like"] * 5)
        r = probes.probe_l5_semantic()
        assert r.available is True and r.score == pytest.approx(1.0)


class TestProbeMetricsPassthrough:
    """结构化 metrics 必须一路透传到落盘记录（2026-11-08 实测漏了两层）。

    现象：探针自己有 metrics，但 `/api/health/probe-trend` 里是 `metrics: null`
    ⇒ 概览卡的「CPU 核心数 / 内存可用量 / 磁盘可用」仍然显示「—（端点未返回）」。
    根因：assessor 与 collector 各自重建了 {score, available, detail} 三个键。
    """

    def test_assessor_透传_metrics(self):
        from agent.health.assessor import HealthAssessor
        from agent.health.probes import ProbeResult

        probes = {
            "l1_process": ProbeResult("l1_process", 1.0, "mem ok", True,
                                      metrics={"cpu_cores": 12, "mem_available_mb": 2048}),
            "l2_dependency": ProbeResult("l2_dependency", 1.0, "configs 2/2", True),
        }
        score = HealthAssessor().assess_with_probes(probes)
        assert score.probe_details["l1_process"]["metrics"]["cpu_cores"] == 12
        # 没有 metrics 的层不许多出一个 null 键（老消费方形状不变）
        assert "metrics" not in score.probe_details["l2_dependency"]

    def test_collector_落盘记录带_metrics(self, monkeypatch):
        from agent.health import collector

        appended = []
        monkeypatch.setattr(collector.health_storage, "append",
                            lambda rec: appended.append(rec), raising=False)
        rec = collector._collect_once()
        assert appended, "采集必须落盘（append 被调用）"
        l1 = rec["probe_details"].get("l1_process") or {}
        # 本机 psutil 可用时一定有结构化数值；不可用（无 psutil）则该层 available=False
        if l1.get("available"):
            assert l1.get("metrics"), "L1 的 metrics 没进落盘记录 ⇒ 界面拿不到核心数/可用内存"
            assert "cpu_cores" in l1["metrics"]
        assert "metrics" not in (rec["probe_details"].get("l2_dependency") or {})
