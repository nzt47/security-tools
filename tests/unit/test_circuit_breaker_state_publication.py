# -*- coding: utf-8 -*-
"""熔断器**状态序列一定存在**守卫：启动钩子必须在应用启动路径上真的发布一次。

## 要治的病（为什么单独一个文件）

告警 CircuitBreakerMetricsMissing（monitoring/circuit_breaker_alerts.yml §4.1）改表达式后是：

    absent(yunshu_circuit_breaker_state) and absent(yunshu_circuit_breaker_trigger_total)

而 BusinessMetricsCollector.export_prometheus() 对"已登记但一条样本都没有"的族
**只输出 # HELP/# TYPE、不输出样本行**（business_metrics.py:2306-2323）——
这是**有意为之的既有契约**（否则"没有数据"与"数据是 0"又不可区分）。
⇒ 只要从来没写过一次埋点，这两条序列在 Prometheus 里就**不存在**，与"指标管道坏了"不可区分
⇒ 告警一旦 firing 就永不 resolved（C-2 3.97h 长跑实测：
docs/closeout/监控清理_evidence_20261002/c2_longrun_report.md §3.3）。

机制本来是好的（get_circuit_breaker() 访问点会发布状态、_install_circuit_breaker_observer()
也已注入观察者），缺的只是**没有任何生产路径会无条件调用它**。
本文件锁住"已经在启动路径上补上了这一次无条件调用"，并锁住它的三条边界性质：

1. **有效**：启动钩子执行后，导出文本里能搜到 yunshu_circuit_breaker_state{...} <值> 的**样本行**；
2. **幂等**：重复执行不产生重复/冲突样本（gauge 覆盖语义，同一 label_key 只有一条）；
3. **不越界**：没有改掉 export_prometheus() 对"完全没数据"的其它族的既有契约（HELP/TYPE-only 对照）。

外加两条静态守卫：4) 启动路径（app_server.py）确实调用了发布函数；
5) 发布的名字**不是编造的** —— 每个名字都能在 agent/ 的生产源码里找到字面量出处。
"""
from __future__ import annotations

import os
import re

import pytest

import agent.circuit_breaker as CB
from agent.monitoring import business_metrics as BM

#: 发布后必须出现样本行的指标名（gauge）
_STATE_METRIC = "yunshu_circuit_breaker_state"


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sample_lines(text: str, metric: str = _STATE_METRIC):
    """只取**样本行**（以 指标名{ 开头），HELP/TYPE 声明行不算。"""
    return [ln for ln in text.splitlines() if ln.startswith(metric + "{")]


@pytest.fixture
def isolated_metrics_state():
    """快照/还原全局采集器与全局熔断器注册表。

    publish_deployed_circuit_breaker_states() 走的是**全局单例**（这正是生产的真实路径，
    不能拿一个本地 collector 顶替），所以用完必须还原，避免污染同会话的其它测试。
    """
    collector = BM.get_business_metrics_collector()
    with collector._lock:
        gauges_before = {k: dict(v) for k, v in collector._gauges.items()}
        counters_before = {k: dict(v) for k, v in collector._counters.items()}
    registry = CB.get_breaker_registry()
    registry_before = dict(registry)

    yield collector

    with collector._lock:
        collector._gauges.clear()
        collector._gauges.update(gauges_before)
        collector._counters.clear()
        collector._counters.update(counters_before)
    registry.clear()
    registry.update(registry_before)


class TestStartupPublication:
    def test_启动钩子执行后导出里有状态样本行(self, isolated_metrics_state):
        """本文件的核心断言：**样本行确实出现**（而不是只有 HELP/TYPE）。"""
        collector = isolated_metrics_state
        published = BM.publish_deployed_circuit_breaker_states()
        assert published == list(BM.DEPLOYED_CIRCUIT_BREAKER_NAMES), (
            "发布函数没有把本部署声明的熔断器全部发布出去：%r" % (published,))

        text = collector.export_prometheus()
        samples = _sample_lines(text)

        # (1) 样本行非空（修前这里是 0 —— 见交付报告里的改前基线）
        assert samples, (
            "启动钩子执行后 " + _STATE_METRIC + " 仍然**没有任何样本行**"
            " => 在 Prometheus 里这条序列不存在，CircuitBreakerMetricsMissing 仍会 firing。"
            "导出里该指标只有：%r"
            % ([ln for ln in text.splitlines() if "circuit_breaker_state" in ln],))

        # (2) 每个真实熔断器都有一条 state="closed" 的样本（值是熔断器的真实状态，不是伪造常量）
        for name in BM.DEPLOYED_CIRCUIT_BREAKER_NAMES:
            expect = _STATE_METRIC + '{breaker_name="' + name + '",state="closed"} 1.0'
            assert expect in samples, (
                "缺少样本行 %r；实际样本行：%r" % (expect, samples))

        # (3) 样本行与注册表里的对象是**同一个**（不是另建了一个同名假熔断器）
        for name in BM.DEPLOYED_CIRCUIT_BREAKER_NAMES:
            breaker = CB.get_breaker_registry().get(name)
            assert breaker is not None, "发布后注册表里应有 %r" % (name,)
            assert getattr(breaker.state, "value", None) == "closed", (
                "%r 的真实状态应为 closed，导出的值必须与它一致" % (name,))

    def test_幂等_重复调用不产生重复或冲突样本(self, isolated_metrics_state):
        collector = isolated_metrics_state
        BM.publish_deployed_circuit_breaker_states()
        first = _sample_lines(collector.export_prometheus())

        BM.publish_deployed_circuit_breaker_states()
        BM.publish_deployed_circuit_breaker_states()
        second = _sample_lines(collector.export_prometheus())

        assert second == first, (
            "重复调用改变了样本集合 => 不是幂等（gauge 覆盖语义应保证同一 label_key 只一条）："
            "第一次 %r / 第二次 %r" % (first, second))
        assert len(second) == len(set(second)) == len(BM.DEPLOYED_CIRCUIT_BREAKER_NAMES), (
            "样本行出现重复或数量与熔断器数量不符：%r" % (second,))

        # 同一个熔断器不得同时挂两条互相冲突的状态（例如既 closed=1 又 open=1）
        for name in BM.DEPLOYED_CIRCUIT_BREAKER_NAMES:
            per_breaker = [ln for ln in second if ('breaker_name="' + name + '"') in ln]
            assert len(per_breaker) == 1, (
                "熔断器 %r 有多条并发状态样本（冲突）：%r" % (name, per_breaker))

    def test_不改变空族只输出_HELP_TYPE_的既有契约(self):
        """对照：**完全没数据**的族仍然只有 HELP/TYPE —— 修法没有去伪造样本。

        两个对照面：
          a) 一个全新的 collector（一条样本都没有）：熔断器族与无关族都必须只有声明行；
          b) 源码级：export_prometheus() 里不得新增"空族补一条样本"的逻辑
             （证明这是"真的写了一次埋点"，不是因为导出函数被改成"空族也吐样本"）。
        """
        fresh = BM.BusinessMetricsCollector()          # 与全局单例无关的全空采集器
        text = fresh.export_prometheus()
        cb_lines = [ln for ln in text.splitlines() if "circuit_breaker" in ln]
        assert len(cb_lines) == 4 and all(ln.startswith("#") for ln in cb_lines), (
            "空 collector 上熔断器族不应有样本行：%r" % (cb_lines,))
        # 与熔断器无关、同样没有样本的对照族
        control = [ln for ln in text.splitlines() if ln.startswith("yunshu_backup_total")]
        assert control == [], "对照族 yunshu_backup_total 本应零样本，实际：%r" % (control,)

        src_path = os.path.join(_repo_root(), "agent", "monitoring", "business_metrics.py")
        with open(src_path, encoding="utf-8") as fh:
            src = fh.read()
        body = src.split("def export_prometheus(self) -> str:", 1)[1]
        body = body.split("return " + chr(39) + chr(92) + "n" + chr(39) + ".join(lines)", 1)[0]
        assert "or 0" not in body and "default 0" not in body, (
            "export_prometheus() 里疑似出现了给空族补样本的逻辑（会重新把"
            "『没有数据』与『数据是 0』混为一谈）：%r" % (body[-200:],))


class TestStartupWiring:
    def test_app_server_启动路径确实调用了发布函数(self):
        """组合根必须真的接线（否则函数写了也没人调 —— 这正是本 bug 的形态）。"""
        path = os.path.join(_repo_root(), "app_server.py")
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        pattern = "^[ \t]*_publish_circuit_breaker_states[(][)][ \t]*$"
        calls = re.findall(pattern, src, re.M)
        assert calls, (
            "app_server.py 里找不到 _publish_circuit_breaker_states() 的调用 => "
            "熔断器状态序列又会只在状态转换时才诞生 => CircuitBreakerMetricsMissing 永不 resolved")

    def test_发布的名字都取自生产源码的真实字面量(self):
        """反"编造名字"守卫：每个发布名都能在 agent/ 下找到出处。

        理由：凭空造一个名字会**伪造**一条业务上不存在的序列（那条序列永远不会 OPEN），
        比"没有序列"更难发现。故只允许本仓生产调用点里出现过的名字。
        """
        agent_dir = os.path.join(_repo_root(), "agent")
        corpus = []
        for dirpath, _dirnames, filenames in os.walk(agent_dir):
            if "__pycache__" in dirpath:
                continue
            for fn in filenames:
                if fn.endswith(".py"):
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as fh:
                        corpus.append(fh.read())
        corpus = chr(10).join(corpus)
        for name in BM.DEPLOYED_CIRCUIT_BREAKER_NAMES:
            assert (chr(34) + name + chr(34)) in corpus, (
                "发布名 %r 在 agent/ 的生产源码里找不到字面量出处 => 可能是编造的序列" % (name,))
