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


def _state_keys(collector):
    """采集器里 `yunshu_circuit_breaker_state` 的 **label_key 集合**。

    注意：这里刻意**不过滤** —— 需要看到"全局采集器当前到底有哪些键"（可能含别的测试写下的），
    因为本文件要断言的是"发布调用带来的**变化**"，不是"全局有多干净"。
    """
    with collector._lock:
        return set(collector._gauges.get(_STATE_METRIC, {}).keys())


def _state_values(collector):
    """同上，但取 {label_key: value} 的副本（用于断言"值也不变"）。"""
    with collector._lock:
        return dict(collector._gauges.get(_STATE_METRIC, {}))


def _for_published(lines, names=None):
    """只保留"本部署声明发布的那几个熔断器"的样本行。

    【为什么必须过滤 · 2026-10-03 CI 实测】全量跑时**全局**采集器里早已有别的测试写下的熔断器样本
    （tests/unit/test_circuit_breaker_boundary.py 等会真的驱动熔断器状态机 ⇒ 观察者往全局采集器里写，
    实测一次全量里有 29 条），把它们算进来会让"幂等 / 数量"这类断言变成**依赖测试顺序**的假红：
    CI 的 Shard 3/6 就是这么红的（`assert 29 == 4`），而单跑本文件却是绿的。
    本文件只关心自己发布的那 N 个名字 —— 过滤后断言才是真正在测"发布行为"而不是"全局采集器有多干净"。
    """
    names = names if names is not None else BM.DEPLOYED_CIRCUIT_BREAKER_NAMES
    return [ln for ln in lines
            if any(('breaker_name="' + n + '"') in ln for n in names)]


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
        """幂等的判据是"**重复调用不改变任何东西**"，不是"全局只有 4 条样本"。

        【为什么改成这样 · 2026-10-03 CI 连红两次的教训】
        原判据写的是"全局采集器里 `yunshu_circuit_breaker_state` 的样本集合恰好等于本部署的 4 个名字"，
        这在**全量跑**里必然不成立，而且有两层：
          ① 别的测试会往**同一个全局采集器**写**别的**熔断器（Shard 3/6 实测 29 条）；
          ② 更隐蔽：别的测试会把**本部署也发布的**熔断器真的打到 OPEN
             （`tests/unit/test_guardrails_egress_chain.py` 驱动 `guardrails.egress_chain` ⇒ 多出
             `{state="open"} 1.0`，于是"过滤到已发布名字"后仍是 5 条 > 4）。
        ⇒ 本用例改成测**发布这个动作本身的语义**：
          (1) 发布后每个声明的名字都有 `(breaker,state=closed)` 这条键；
          (2) 再重复调用两次，采集器里该指标的**键集合与值**都不变（这才是幂等）。
        这样它与"全局采集器里还有谁"完全解耦；而"状态 gauge 任一时刻只应有一个 1"这条性质
        由 `test_transition_clears_old_state_sample` 单独负责（那条测的是转换语义，不依赖全局干净）。
        """
        collector = isolated_metrics_state

        BM.publish_deployed_circuit_breaker_states()
        keys_after_first = _state_keys(collector)
        values_after_first = _state_values(collector)

        # (1) 发布语义：本部署声明的每个熔断器都拿到了 state="closed" 这条键
        for name in BM.DEPLOYED_CIRCUIT_BREAKER_NAMES:
            key = BM.make_label_key({"breaker_name": name, "state": "closed"})
            assert key in keys_after_first, (
                "发布后缺少键 %r；实际键集合：%r" % (key, sorted(keys_after_first)))

        # (2) 幂等：重复调用既不新增也不删除键、也不改值
        BM.publish_deployed_circuit_breaker_states()
        BM.publish_deployed_circuit_breaker_states()

        assert _state_keys(collector) == keys_after_first, (
            "重复调用改变了键集合 => 不是幂等（gauge 覆盖语义应保证同一 label_key 只一条）："
            "第一次 %r / 之后 %r" % (sorted(keys_after_first), sorted(_state_keys(collector))))
        assert _state_values(collector) == values_after_first, (
            "重复调用改变了样本值 => 不是幂等：第一次 %r / 之后 %r"
            % (values_after_first, _state_values(collector)))

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

def test_transition_clears_old_state_sample(isolated_metrics_state):
    """状态转换后，**旧**状态的样本必须被清零（状态 gauge 任一时刻只应有一个 1）

    【为什么必须用 isolated_metrics_state 夹具】本用例会通过模块级观察者写**全局**采集器；
    不还原就会把 cb_state_probe 的样本留给同会话的其它用例（实测：pytest-randomly 乱序时
    会让"幂等"用例的数量断言误报）。


    为什么单列：`yunshu_circuit_breaker_state` 带 `state` 标签，语义是"当前处于哪个状态"。
    只写新状态、不清旧状态时，一次 `closed → open` 之后 `sum by (state)` 会读成
    "两个状态同时成立"（既有行为；此前该族没有任何样本，所以错误一直不可见）。
    """
    from agent.monitoring import business_metrics as bm

    bm._on_circuit_breaker_state("cb_state_probe", "closed", "open")
    txt = bm.get_business_metrics_collector().export_prometheus()
    lines = [ln for ln in txt.splitlines()
             if ln.startswith('yunshu_circuit_breaker_state{breaker_name="cb_state_probe"')]
    assert 'yunshu_circuit_breaker_state{breaker_name="cb_state_probe",state="open"} 1.0' in lines, lines
    assert 'yunshu_circuit_breaker_state{breaker_name="cb_state_probe",state="closed"} 0.0' in lines, lines
