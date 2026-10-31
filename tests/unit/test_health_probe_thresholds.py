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
