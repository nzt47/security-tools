# -*- coding: utf-8 -*-
"""指标采样环（2026-11-11）：让"窗口类" PromQL（rate / increase / histogram_quantile）
能在**本进程**里求值，而不必先部署一个 Prometheus 服务器。

【为什么需要它】monitoring/alerts.yml 的规则里有一半是窗口函数：`rate(x[5m])`、
`increase(x[1m])`、`histogram_quantile(0.99, sum(rate(bucket[5m])))`。
它们本质上要「两个时间点的计数器读数」。本进程的 prometheus_client 注册表只有**当前值**，
所以要么接 Prometheus 服务器，要么自己留一份短期样本 —— 这里选后者：
每轮采集（60s）快照一次，保留最近 `WINDOW_SEC` 秒（默认 1 小时，最多 120 个点）。

【口径（与 Prometheus 对齐的地方和不对齐的地方，都写清楚）】
- `rate/increase`：用窗口两端的差值；计数器**回退**（进程重启/重置）按"从 0 重新开始"处理，
  即取当前值本身（与 Prometheus 的 reset 语义一致）。
- 样本不足一个窗口 ⇒ **不求值**（返回 `None` + reason="窗口样本不足"），不返回 0 冒充；
  刚重启后前 5 分钟就是这种状态，这是诚实的，不是故障。
- `histogram_quantile`：按 bucket 上界线性插值（与 Prometheus 同法），`+Inf` 桶存在时
  若落在最后有限桶之外，返回最后一个有限上界并注明（不编具体值）。
- 采集面 = **prometheus_client 默认 REGISTRY** + **业务指标层投影**（`snapshot_business_metrics`，
  2026-11-11 补：`yunshu_tool_call_total` / `yunshu_skill_hallucination_total` /
  `yunshu_skill_eval_score` 三族的真实写入点在业务指标层，不在注册表里）。
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 样本保留窗口（秒）与最大点数（60s 一点 ⇒ 1 小时 = 60 点；留一倍冗余）
WINDOW_SEC = 3600
MAX_SAMPLES = 120

Range = Tuple[float, float]  # (start_ts, end_ts)

#: 序列键 = (指标名, 标签对)；标签对排序保证同一序列键稳定
SeriesKey = Tuple[str, Tuple[Tuple[str, str], ...]]


def _series_key(name: str, labels: Dict[str, str]) -> SeriesKey:
    return (name, tuple(sorted((str(k), str(v)) for k, v in labels.items())))


def snapshot_registry() -> Dict[SeriesKey, float]:
    """把 prometheus_client 默认注册表拍成 {序列键: 当前值}。

    取不到注册表（未安装/被替换）⇒ 空字典（调用方据此报"无可求值序列"，不报假 0）。
    """
    try:
        from prometheus_client import REGISTRY  # noqa: PLC0415 延迟 import：仅采样路径需要
    except Exception:  # noqa: BLE001
        return {}
    out: Dict[SeriesKey, float] = {}
    try:
        families = list(REGISTRY.collect())
    except Exception as exc:  # noqa: BLE001 注册表不可读 ⇒ 不求值
        logger.debug("采样：注册表不可读（%s）", type(exc).__name__)
        return {}
    for family in families:
        for sample in family.samples:
            try:
                out[_series_key(sample.name, sample.labels or {})] = float(sample.value)
            except (TypeError, ValueError):
                continue
    return out


#: 业务指标层参与采样的族（只认 alerts.yml 真正引用的那几族，**不全量并**：
#: 业务指标里有 40 族，全并进采样环会让每轮快照多出几千个序列，而规则只用得到这几个）。
BUSINESS_ALERT_FAMILIES = (
    "yunshu_tool_call_total",
    "yunshu_skill_hallucination_total",
    "yunshu_skill_eval_score",
    "yunshu_skill_eval_score_sum",
    "yunshu_skill_eval_score_count",
)


def _parse_exposition(text: str) -> Dict[SeriesKey, float]:
    """把 Prometheus 文本导出解析成 {序列键: 值}（只认 `name{labels} value` 行）。

    不引第三方解析器：这里需要的是"能读回自己刚导出的那几行"，不是通用兼容层；
    解析不了的行直接跳过（宁可少一个序列，也不编一个值）。
    """
    import re  # noqa: PLC0415 只在本函数用

    label_re = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')
    out: Dict[SeriesKey, float] = {}
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            body, value_text = line.rsplit(" ", 1)
            value = float(value_text)
        except ValueError:
            continue
        if value != value or value in (float("inf"), float("-inf")):  # NaN / Inf 不入环
            continue
        if "{" in body and body.endswith("}"):
            name, _, label_text = body[:-1].partition("{")
            labels = {k: v for k, v in label_re.findall(label_text)}
        else:
            name, labels = body, {}
        if name not in BUSINESS_ALERT_FAMILIES and not name.startswith("yunshu_"):
            continue
        out[_series_key(name, labels)] = value
    return out


def snapshot_business_metrics() -> Dict[SeriesKey, float]:
    """把**业务指标层**的投影并进采样面（2026-11-11，为 `alarms-global-coverage` 的埋点缺口收口）。

    【为什么是读、不是写】`yunshu_tool_call_total` / `yunshu_skill_hallucination_total` /
    `yunshu_skill_eval_score` 三族在 `agent/monitoring/business_metrics.py` 里已有**真实写入点**
    （工具执行点、`emit_eval_score_metric`），并通过 `/api/business/prometheus` 对外暴露；
    但那个收集器是**自管理字典**，不在 prometheus_client 默认 REGISTRY 里 ⇒ 本进程求值
    永远看不到它们（于是 3 条规则长期报 no_data）。这里读同一份投影（`export_prometheus()`），
    既不复制一份数字，也不改业务指标的写入路径。

    【口径】只并 `BUSINESS_ALERT_FAMILIES`（规则真正引用的族）；族不存在/导出失败 ⇒ 空字典，
    规则照旧报 no_data（不把"没埋点"读成"正常"）。
    """
    try:
        from agent.monitoring.business_metrics import (  # noqa: PLC0415 延迟 import：避免启动期环
            get_business_metrics_collector,
        )
        text = get_business_metrics_collector().export_prometheus()
    except Exception as exc:  # noqa: BLE001 业务指标层不可用 ⇒ 本族本轮缺失
        logger.debug("采样：业务指标导出失败（%s）", type(exc).__name__)
        return {}
    return _parse_exposition(text)


def snapshot_all() -> Dict[SeriesKey, float]:
    """本轮采样 = 默认注册表 + 业务指标投影（注册表优先，同键不覆盖）。

    `SampleStore.record()` 缺省走这里；只要一方有数据，规则就能求值。
    """
    values = snapshot_registry()
    for key, value in snapshot_business_metrics().items():
        values.setdefault(key, value)
    # 外部 Prometheus（可选，`CP_PROMETHEUS_URL`）：只补 `up{}` 一类"只有它采得到"的序列。
    # 注册表与业务指标层优先（同键不覆盖）；拉取失败由 prometheus_source 记成"本轮不可用"。
    try:
        from agent.alarms.prometheus_source import configured, fetch_up  # noqa: PLC0415 延迟 import：避免环
        if configured():
            external = fetch_up()
            if external:
                for key, value in external.items():
                    values.setdefault(key, value)
    except Exception as exc:  # noqa: BLE001 外部源坏掉不影响本地族的采样
        logger.debug("采样：Prometheus 合并失败（%s）", type(exc).__name__)
    return values


def parse_range(text: str) -> Optional[float]:
    """`5m` / `1h` / `30s` → 秒；无法解析返回 None（调用方据此报"不支持的窗口"）"""
    text = (text or "").strip()
    if not text:
        return None
    unit = text[-1]
    try:
        amount = float(text[:-1])
    except ValueError:
        return None
    factor = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}.get(unit)
    if factor is None or amount <= 0:
        return None
    return amount * factor


class SampleStore:
    """每轮采集快照一次；提供窗口差值（rate/increase）与分位数。"""

    def __init__(self, window_sec: float = WINDOW_SEC, max_samples: int = MAX_SAMPLES) -> None:
        self._window = float(window_sec)
        self._samples: Deque[Tuple[float, Dict[SeriesKey, float]]] = deque(maxlen=max_samples)
        self._lock = threading.Lock()

    # ── 写入 ──────────────────────────────────────────────
    def record(self, values: Optional[Dict[SeriesKey, float]] = None, *, now: Optional[float] = None) -> int:
        """记录一个采样点（缺省自己拍注册表）；返回点内序列数。"""
        ts = time.monotonic() if now is None else float(now)
        snap = snapshot_all() if values is None else values
        with self._lock:
            self._samples.append((ts, dict(snap)))
            cutoff = ts - self._window
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            return len(snap)

    def size(self) -> int:
        with self._lock:
            return len(self._samples)

    def clear(self) -> None:
        with self._lock:
            self._samples.clear()

    def timestamps(self) -> List[float]:
        with self._lock:
            return [ts for ts, _ in self._samples]

    # ── 读取 ──────────────────────────────────────────────
    def latest(self) -> Dict[SeriesKey, float]:
        with self._lock:
            return dict(self._samples[-1][1]) if self._samples else {}

    def oldest_within(self, seconds: float) -> Optional[Tuple[float, Dict[SeriesKey, float]]]:
        """窗口内最早、且与最新点间隔**足够长**的采样点（不足则 None）"""
        with self._lock:
            if not self._samples:
                return None
            newest_ts = self._samples[-1][0]
            for ts, snap in self._samples:
                if newest_ts - ts >= seconds:
                    return ts, dict(snap)
            return None

    def window_edges(self, seconds: float) -> Optional[Tuple[Tuple[float, Dict[SeriesKey, float]],
                                                             Tuple[float, Dict[SeriesKey, float]]]]:
        """窗口两端 (旧, 新)；窗口样本不足 ⇒ None（调用方报"窗口样本不足"）"""
        old = self.oldest_within(seconds)
        if old is None:
            return None
        with self._lock:
            if not self._samples:
                return None
            newest = (self._samples[-1][0], dict(self._samples[-1][1]))
        return old, newest

    def rate(self, key: SeriesKey, seconds: float) -> Optional[float]:
        """每秒增量；窗口不足 ⇒ None；计数器回退按 reset 处理。"""
        edges = self.window_edges(seconds)
        if edges is None:
            return None
        (_, old), (_, new) = edges
        before = old.get(key)
        after = new.get(key)
        if after is None:
            return None
        delta = after - (before if before is not None else 0.0)
        if delta < 0:
            delta = after  # 计数器重置
        return delta / float(seconds)

    def increase(self, key: SeriesKey, seconds: float) -> Optional[float]:
        value = self.rate(key, seconds)
        return None if value is None else value * float(seconds)

    def resets(self, key: SeriesKey, seconds: float) -> Optional[int]:
        """窗口内计数器回退次数（需要逐点，而非两端）。"""
        with self._lock:
            if not self._samples:
                return None
            points = list(self._samples)
        kept = [(ts, snap) for ts, snap in points if points[-1][0] - ts <= seconds + 1e-9]
        if len(kept) < 2:
            return None
        count = 0
        previous: Optional[float] = None
        for _, snap in kept:
            value = snap.get(key)
            if value is None:
                continue
            if previous is not None and value < previous:
                count += 1
            previous = value
        return count


def percentile_from_buckets(buckets: Sequence[Tuple[float, float]], quantile: float) -> Optional[float]:
    """bucket (`le`, 累计值) → 分位数（线性插值；与 Prometheus 同法）。

    Args:
        buckets: [(上界, 累计计数)]，须按上界升序、含 `+Inf`（用 `math.inf` 表示）
        quantile: 0~1

    Returns:
        分位数估计值；样本为 0 或桶为空 ⇒ None（不编 0）
    """
    if not buckets or not (0.0 <= quantile <= 1.0):
        return None
    ordered = sorted(buckets, key=lambda item: item[0])
    total = ordered[-1][1]
    if total <= 0:
        return None
    rank = quantile * total
    previous_bound = 0.0
    previous_count = 0.0
    for bound, count in ordered:
        if count >= rank and math.isfinite(bound):
            span = count - previous_count
            if span <= 0:
                return bound
            return previous_bound + (bound - previous_bound) * ((rank - previous_count) / span)
        if count >= rank and not math.isfinite(bound):
            # 落在 +Inf 桶：只能说"超过最后一个有限上界"，不编具体值
            return previous_bound
        previous_bound, previous_count = bound, count
    return None


_default_store: Optional[SampleStore] = None
_store_lock = threading.Lock()


def get_sample_store() -> SampleStore:
    """进程内默认采样环（采集线程与规则求值共用）"""
    global _default_store
    with _store_lock:
        if _default_store is None:
            _default_store = SampleStore()
        return _default_store


def reset_sample_store(store: Optional[SampleStore] = None) -> None:
    global _default_store
    with _store_lock:
        _default_store = store
