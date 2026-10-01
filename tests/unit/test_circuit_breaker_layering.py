# -*- coding: utf-8 -*-
"""熔断器分层守卫：`agent.circuit_breaker` **不得**导入 `agent.monitoring.*`。

## 为什么要单独一条守卫

`.importlinter` 的分层契约是「monitoring 在上、error_handler 在下，只许 monitoring → error_handler」，
而 `agent/error_handler.py` **会导入 `agent.circuit_breaker`**（3 处）。
于是「circuit_breaker → agent.monitoring.*」会让底层**间接**反向依赖上层 ——
CI 的「循环依赖校验」报的是**传递链**，不是直接边：

    agent.error_handler -> agent.circuit_breaker
    agent.circuit_breaker -> agent.monitoring.business_metrics

这条链在 2026-10-02 真的断过 CI（我加熔断器埋点时踩到）：
local `lint-imports` 报 contract「error_handler 不得在模块级导入 monitoring 子模块」BROKEN。

## 正确修法（已落地）

不是往 `ignore_imports` 塞一条豁免把它盖住，而是**断开这条边**：
`agent/circuit_breaker.py` 提供 `set_state_observer()` 钩子，
由**上层** `agent/monitoring/business_metrics.py` 在导入时注入回调 ——
方向变成 monitoring → circuit_breaker，合法且无环。

## 本文件锁住什么

1. 源码级：`agent/circuit_breaker.py` 里**不得**出现 `agent.monitoring` 导入（含函数体内）；
2. 行为级：钩子机制本身可用（注入后能收到状态事件；未注入时静默跳过、不抛）。
"""
from __future__ import annotations

import os
import re

import agent.circuit_breaker as CB


def _repo_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestNoReverseImport:
    def test_源码里不得出现_agent_monitoring_导入(self):
        path = os.path.join(_repo_root(), "agent", "circuit_breaker.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        hits = [ln for ln, line in enumerate(src.splitlines(), 1)
                if re.search(r"^\s*(from|import)\s+agent\.monitoring", line)]
        assert hits == [], (
            "agent/circuit_breaker.py 出现了对 agent.monitoring 的导入（行 %r）⇒ "
            "会让 error_handler 间接反向依赖上层、CI「循环依赖校验」转红。"
            "正确做法：用 set_state_observer() 钩子，由 agent/monitoring/business_metrics.py "
            "反向注入（见该文件 _install_circuit_breaker_observer 的说明）。" % (hits,))


class TestObserverHook:
    def test_未注入时静默跳过且不抛(self):
        old = CB._state_observer
        try:
            CB.set_state_observer(None)
            CB._notify_state_observer("b", None, "closed")   # 不得抛
        finally:
            CB.set_state_observer(old)

    def test_注入后可收到状态事件(self):
        seen = []
        old = CB._state_observer
        try:
            CB.set_state_observer(lambda n, o, s: seen.append((n, o, s)))
            CB._notify_state_observer("probe", "closed", "open")
            CB._notify_state_observer("probe", None, "closed")   # None = 仅发布当前状态
        finally:
            CB.set_state_observer(old)
        assert seen == [("probe", "closed", "open"), ("probe", None, "closed")]

    def test_回调抛错不得影响熔断主路径(self):
        old = CB._state_observer
        try:
            def _boom(*a):
                raise RuntimeError("观测炸了")
            CB.set_state_observer(_boom)
            CB._notify_state_observer("probe", "closed", "open")   # 必须被吞掉
        finally:
            CB.set_state_observer(old)

    def test_business_metrics_会在导入时注入钩子(self):
        """上层的注入点必须真的执行（否则熔断器指标又会整层缺数据）。"""
        import agent.monitoring.business_metrics  # noqa: F401 导入即注入
        assert CB._state_observer is not None, (
            "agent/monitoring/business_metrics.py 未注入熔断器观测钩子 ⇒ "
            "CircuitBreakerMetricsMissing 会因 absent() 恒为 1 而长期误报")