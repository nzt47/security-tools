# -*- coding: utf-8 -*-
"""可选的外部 Prometheus 数据源（2026-11-11，`alarms-global-coverage` ②"上 Prometheus"）。

【解决什么】`monitoring/alerts.yml` 里有 5 条规则引用 `up{job="yunshu|prometheus|grafana"}`：
`up` 是 **Prometheus 自己 scrape 出来的**指标，本进程的 prometheus_client 注册表里永远没有它
⇒ 没有外部 Prometheus 时这 5 条只能如实标 no_data（§11.89 的现状）。

【做法】环境变量 `CP_PROMETHEUS_URL`（默认空 = 不启用）指向 Prometheus 的 HTTP API。
每轮采集（60s）查一次 instant `up`，把结果并进采样环；标签原样保留，
于是 `up{job="yunshu"} == 0` / `resets(up[1h]) > 3` 这些规则按本进程的 PromQL 子集就能求值。

【本部署的立场（写在最前面，防止下一个人顺着这个文件去装服务）】
`monitoring/alerts.yml` 的 16 条规则按 §11.89 的**方案 C** 在本进程求值；方案 A（为这 16 条规则
再引入一个 Prometheus 服务）**已被否决**（安装/持久化/端口/权限，报警器从此依赖外部进程）。
所以：**本部署不启用外部 Prometheus**，`CP_PROMETHEUS_URL` 保持空；本模块存在只为一件事 ——
让「up{} 到底缺什么」在 `/api/alarms/coverage` 与告警 Tab 上**可见**（未配置 ⇒ up{} 类规则只能
如实报 no_data），而不是把盲区藏起来。若将来真要 up{}，必须 Owner 明确点头（那是重新引入方案 A）。

【三条口径】
1. **没配置 = 完全不参与**：不造假 `up`、不假装在监控（宁可 no_data，也不给"已覆盖"的错觉）。
2. **配置了但查不到 ⇒ 本轮"不可用"**（返回 None）：采集层据此**保留**旧的规则告警
   —— 数据源挂掉的那一轮既不报警、也不判恢复（keep 语义）。
3. **只拉 `up`**：其它外部族（node_exporter / grafana 等）规则里没有引用，不扩大拉取面，
   避免把报警器变成"通用 Prometheus 代理"。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 环境变量名（已进 agent/settings/registry.py）
ENV_URL = "CP_PROMETHEUS_URL"

#: 只查询 `up`（见模块 docstring 第 3 条）
UP_QUERY = "up"

#: HTTP 超时（秒）：报警器的采集线程不能被一个挂掉的 Prometheus 拖住
HTTP_TIMEOUT_SEC = 3.0

#: 最近一次拉取的可用性（供采集层判断"这一轮规则源能不能判恢复"）
_last_ok: Optional[bool] = None


def configured() -> bool:
    """是否配置了外部 Prometheus（空串 = 未配置 = 完全不参与）"""
    return bool((os.environ.get(ENV_URL) or "").strip())


def last_ok() -> Optional[bool]:
    """最近一轮拉取是否成功；None = 还没拉过"""
    return _last_ok


def reset_state() -> None:
    """清可用性状态（测试隔离用）"""
    global _last_ok
    _last_ok = None


def fetch_up() -> Optional[List[Tuple[str, Dict[str, str], float]]]:
    """查 instant `up` → [(指标名, 标签, 值)]（**只回原始数据，不构造采样环的序列键**）。

    【为什么不再回 {序列键: 值}（2026-11-12）】原实现 import 了
    `agent.alarms.samples` 的 `SeriesKey/_series_key` 来拼键，而 samples 又（延迟）
    import 本模块来合并外部样本 ⇒ 依赖成环，架构规则校验判 no_circular_dependency
    高严重度（CI「架构规则校验」自 34f1d4509 起一直红）。修法是把依赖方向理顺：
    **本模块只负责取数，键由采样环（samples）自己拼**。

    Returns:
        - 未配置 ⇒ `None`（不参与；与"不可用"共用返回值，由调用方先用 :func:`configured` 判断）
        - 配置了但查询失败/超时/返回体不认识 ⇒ `None`（本轮不可用，调用方保留旧告警）
        - 成功 ⇒ 列表（可能为空：Prometheus 在跑但还没有 target —— 那也是**真的没有**）
    """
    global _last_ok
    if not configured():
        return None
    base = (os.environ.get(ENV_URL) or "").strip().rstrip("/")
    query = urllib.parse.urlencode({"query": UP_QUERY})
    url = base + "/api/v1/query?" + query
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_SEC) as resp:  # noqa: S310 只读 GET
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001 拉不到 = 本轮不可用
        _last_ok = False
        logger.warning("Prometheus 不可用（%s）：规则里的 up{} 本轮不参与求值", type(exc).__name__)
        return None
    data = (payload or {}).get("data") or {}
    result = data.get("result")
    if not isinstance(result, list):
        _last_ok = False
        logger.warning("Prometheus 返回体不认识（缺 data.result）：本轮按不可用处理")
        return None
    out: List[Tuple[str, Dict[str, str], float]] = []
    for row in result:
        if not isinstance(row, dict):
            continue
        metric = row.get("metric") or {}
        try:
            value = float((row.get("value") or [None, None])[1])
        except (TypeError, ValueError, IndexError):
            continue
        name = str(metric.get("__name__") or UP_QUERY)
        labels = {str(k): str(v) for k, v in metric.items() if k != "__name__"}
        out.append((name, labels, value))
    _last_ok = True
    return out


__all__ = ["ENV_URL", "UP_QUERY", "configured", "last_ok", "reset_state", "fetch_up"]
