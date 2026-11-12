# -*- coding: utf-8 -*-
"""外部端口存活（2026-11-12）：本机 TCP 连接探测 —— 只回答"端口开没开"。

【为什么要有它（§11.94 C 节候选⑤）】六条事件源里没有任何一条回答"我要连的服务还在不在"。
§11.89 已否决"为 16 条规则引入一个 Prometheus 服务"（方案 C：报警器不能从此依赖外部进程）。
所以这里只做最薄的一层：按配置对 host:port 做一次 TCP connect（默认 0.6s 超时），
把结果读成**单位看板的一个单位**（ok / warn / never）+ 指标；不抓指标、不聚合、不新增依赖。

【口径】
- **默认空** = 不探任何端口 ⇒ status=never（"从未产生数据"），不进健康分分母。
  要探什么写在 CP_PORT_PROBES（逗号/分号分隔，例如 127.0.0.1:11434;10.0.0.5:5432）。
- 某个**配置**端口连不上 ⇒ status=warn，并逐条列出 target：这是真读数
  （"我要它活着，它没活"），不是猜测；连不上的原因只归类（超时/拒绝/其它），
  不回原始异常文本，也不落对端地址（裁定 B 同口径）。
- 只做 TCP connect，**不发任何应用层请求、不读响应**；不写盘、不建单例
  （探针每 60s 被报警采集线程调一次，§11.93/§11.95 的两次实证）。
- unit_probe() 的 issues 只写**配置里的** target 字符串（运维自己写的），
  不写 socket 解析出的对端地址。

【升级方向（别在本轮做）】要指标级探活（HTTP 200 / 响应体状态）就属于新数据面，
需要先定清"什么算不可用、失败哪几轮才报"，并且必须 Owner 明确点头。
"""
from __future__ import annotations

import logging
import os
import socket
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 配置项（已进 agent/settings/registry.py）
ENV_TARGETS = "CP_PORT_PROBES"

#: 单端口连接超时（秒）：探针整体上限 2s，留足余量
CONNECT_TIMEOUT_SEC = 0.6
#: 一次最多探多少个端口（防止一条坏配置把探针拖成"多数超时"）
MAX_TARGETS = 10

#: 单位 ID / 展示名 / 家族（与包声明里的 id 一致）
UNIT_ID = "ops.ports"
UNIT_LABEL = "外部端口存活"
UNIT_FAMILY = "ops"

Connector = Callable[[str, int, float], bool]


def _split(raw: Optional[str]) -> List[str]:
    text = (raw or "").strip()
    if not text:
        return []
    for sep in (";", "\n", " ", "\t"):
        text = text.replace(sep, ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def parse_targets(raw: Optional[str] = None) -> List[Tuple[str, int]]:
    """把配置串解析成 [(host, port)]；非法项**跳过并记日志**（不让一条坏配置拖垮全部）。"""
    text = raw if raw is not None else os.environ.get(ENV_TARGETS, "")
    out: List[Tuple[str, int]] = []
    for item in _split(text):
        host, _, port_text = item.rpartition(":")
        host = host.strip()
        if not host or not port_text:
            logger.warning("[PortProbe] 忽略非法 target=%r（要 host:port）", item)
            continue
        try:
            port = int(port_text)
        except ValueError:
            logger.warning("[PortProbe] 忽略非法端口 target=%r", item)
            continue
        if not 0 < port < 65536:
            logger.warning("[PortProbe] 忽略越界端口 target=%r", item)
            continue
        out.append((host, port))
        if len(out) >= MAX_TARGETS:
            break
    return out


def _default_connector(host: str, port: int, timeout: float) -> bool:
    """TCP connect（只握手，不发数据）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, TimeoutError):
        logger.debug("[PortProbe] %s:%d 超时（%.1fs）", host, port, timeout)
        return False
    except OSError:
        logger.debug("[PortProbe] %s:%d 连接失败", host, port)
        return False


def probe_targets(targets: Optional[Sequence[Tuple[str, int]]] = None, *,
                  timeout: float = CONNECT_TIMEOUT_SEC,
                  connector: Optional[Connector] = None) -> List[Dict[str, object]]:
    """逐个探活，返回 [{target, host, port, ok, latency_ms}]（**不抛异常**）。"""
    items = list(targets) if targets is not None else parse_targets()
    run = connector or _default_connector
    out: List[Dict[str, object]] = []
    for host, port in items:
        started = time.monotonic()
        try:
            ok = bool(run(host, port, timeout))
        except Exception as exc:  # noqa: BLE001 单端口失败不影响其它端口
            logger.warning("[PortProbe] 探测 %s:%d 异常（%s）", host, port, type(exc).__name__)
            ok = False
        out.append({
            "target": "%s:%d" % (host, port),
            "host": host,
            "port": port,
            "ok": ok,
            "latency_ms": round((time.monotonic() - started) * 1000.0, 1),
        })
    return out


def unit_probe(targets: Optional[Sequence[Tuple[str, int]]] = None, *,
               timeout: float = CONNECT_TIMEOUT_SEC,
               connector: Optional[Connector] = None) -> Dict[str, object]:
    """单位看板探针：逐端口 TCP 探活 → ok / warn / never。"""
    items = list(targets) if targets is not None else parse_targets()
    if not items:
        return {
            "status": "never",
            "summary": "未配置端口探活（CP_PORT_PROBES 为空）",
            "metrics": {"configured": 0, "open": 0, "closed": 0},
            "issues": [],
            "last_activity": None,
        }
    rows = probe_targets(items, timeout=timeout, connector=connector)
    open_n = sum(1 for r in rows if r["ok"])
    closed = [str(r["target"]) for r in rows if not r["ok"]]
    summary = "探测 %d 个端口：%d 个可达" % (len(rows), open_n)
    if closed:
        summary += "，%d 个不可达" % len(closed)
    return {
        "status": "ok" if not closed else "warn",
        "summary": summary,
        "metrics": {
            "configured": len(rows),
            "open": open_n,
            "closed": len(closed),
            "latency_ms_max": max([float(r["latency_ms"]) for r in rows] or [0.0]),
        },
        "issues": ["端口不可达：%s" % t for t in closed],
        "last_activity": None,
    }


__all__: List[str] = [
    "ENV_TARGETS", "CONNECT_TIMEOUT_SEC", "UNIT_ID", "UNIT_LABEL", "UNIT_FAMILY",
    "parse_targets", "probe_targets", "unit_probe",
]
