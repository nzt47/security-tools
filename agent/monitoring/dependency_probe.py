# -*- coding: utf-8 -*-
"""依赖面主动探测（2026-11-13）。

【为什么】报警器的 7 个事件源里，channel 只能回答"最近一次真实发送成不成功"，
健康探针的 L2 只查 `configs/` 文件可读性 —— **没有任何一路主动问"那个端点现在连得上吗"**。
2026-11-13 那次邮件发不出去，事前十分钟没有任何读数变红。

【做什么（以及刻意不做什么）】
- 只做 **TCP 连接 + 可选 TLS 握手**：证明"主机在、端口开着、证书能握上"。
- **不发信、不登录、不调 LLM**：不消耗配额、不产生副作用、不碰凭据。
- 目标来自"用户真的配了的东西"：已启用通道（SMTP / webhook）+ LLM base_url。
  没配的东西不探（没配置 ≠ 故障）。

【口径】
1. 失败只记**类别**（dns / timeout / refused / tls / other），异常原文不落盘、不进响应。
2. 15 分钟一轮（`PROBE_INTERVAL_SEC`），单目标 6s 超时；探测跑在**自己的线程**里，
   采集线程只读快照 —— 网络慢不能把告警采集卡住。
3. 还没跑过 ⇒ 快照为 None ⇒ 采集源"不可用"（不报警也不判恢复），不拿"没测"当"正常"。
4. 目标清单每轮重算：通道被停用/卸载后，它的探测结果自动消失（下一轮不再报）。
"""
from __future__ import annotations

import json
import logging
import os
import socket
import ssl
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: 单目标超时（秒）：SMTP 的 TLS 握手在本机实测 103ms，6s 已经足够宽松
PROBE_TIMEOUT_SEC = 6.0
#: 轮询间隔（秒）：15 分钟。太密会给对端造成无意义的连接噪音
PROBE_INTERVAL_SEC = 900
#: 快照落盘（跨重启保留最后一次读数；仅供读数展示，不参与判定"新鲜"）
STATE_REL_PATH = os.path.join("data", "monitoring", "dependency_probe.json")

#: 失败类别（只存类别）
CODE_DNS = "dns"
CODE_TIMEOUT = "timeout"
CODE_REFUSED = "refused"
CODE_TLS = "tls"
CODE_OTHER = "other"
CODE_LABELS: Dict[str, str] = {
    CODE_DNS: "域名解析失败（DNS/网络不通）",
    CODE_TIMEOUT: "连接超时（出网被拦或对端不响应）",
    CODE_REFUSED: "连接被拒绝（端口没开/服务没起）",
    CODE_TLS: "TLS 握手失败（证书/中间设备/协议不匹配）",
    CODE_OTHER: "连接失败（详见服务日志）",
}

_ROOT = Path(__file__).resolve().parents[2]
STATE_PATH = _ROOT / STATE_REL_PATH

_lock = threading.Lock()
_thread: Optional[threading.Thread] = None
_snapshot: Optional[Dict[str, Any]] = None
#: 每目标的连续失败轮数（进程内；重启归零 —— 重启后要两轮才敢报，宁可晚 15 分钟）
_fail_counts: Dict[str, int] = {}
#: 值得当场重试一次的失败类别（抖动型）：DNS 与 TLS 证书错误重试也没用
RETRYABLE_CODES = (CODE_TIMEOUT, CODE_OTHER)
#: 单轮内重试次数（发现抖动就再来一次；仍失败才算这一轮失败）
PROBE_RETRIES = 1
RETRY_BACKOFF_SEC = 0.3


def _now_iso(now: Optional[float] = None) -> str:
    ts = time.time() if now is None else float(now)
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


def _classify(exc: BaseException) -> str:
    """异常 → 类别（永不返回原文）。"""
    if isinstance(exc, socket.gaierror):
        return CODE_DNS
    if isinstance(exc, ssl.SSLError):
        return CODE_TLS
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return CODE_TIMEOUT
    if isinstance(exc, ConnectionRefusedError):
        return CODE_REFUSED
    if isinstance(exc, ConnectionResetError):
        return CODE_OTHER
    return CODE_OTHER


def probe_target(target: Dict[str, Any], *, timeout: float = PROBE_TIMEOUT_SEC,
                 connector: Optional[Callable[..., Any]] = None,
                 retries: int = PROBE_RETRIES) -> Dict[str, Any]:
    """探一个目标：TCP 连接（tls=True 时再做一次握手）。**永不抛**。

    Args:
        target: {id, kind, host, port, tls}
        timeout: 单次超时（秒）
        connector: 注入用（测试传假的连接器；签名 (host, port, timeout) -> socket）

    Returns:
        {id, kind, ok, ms, code, detail}
    """
    target_id = str(target.get("id") or "")
    kind = str(target.get("kind") or "target")
    host = str(target.get("host") or "")
    port = int(target.get("port") or 0)
    use_tls = bool(target.get("tls"))
    started = time.monotonic()
    result: Dict[str, Any] = {"id": target_id, "kind": kind, "ok": False,
                              "ms": 0, "code": "", "detail": ""}
    if not host or not (0 < port < 65536):
        result["code"] = CODE_OTHER
        result["detail"] = "目标配置不完整（缺 host/port）"
        return result
    attempts = max(0, int(retries)) + 1
    for attempt in range(attempts):
        sock = None
        try:
            sock = (connector or socket.create_connection)((host, port), timeout)
            if use_tls:
                context = ssl.create_default_context()
                sock = context.wrap_socket(sock, server_hostname=host)
            result["ok"] = True
            result["code"] = ""
            result["detail"] = ""
        except Exception as exc:  # noqa: BLE001 任何连接失败都只记类别
            code = _classify(exc)
            result["code"] = code
            result["detail"] = CODE_LABELS.get(code, CODE_LABELS[CODE_OTHER])
            logger.debug("[dependency-probe] %s 失败（%s）", target_id, type(exc).__name__)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass
        if result["ok"]:
            break
        if attempt + 1 < attempts and result["code"] in RETRYABLE_CODES:
            time.sleep(RETRY_BACKOFF_SEC)
            continue
        break
    result["ms"] = int((time.monotonic() - started) * 1000)
    return result


def targets_from_channels(rows: List[Dict[str, Any]],
                          is_enabled: Callable[[Dict[str, Any]], bool]) -> List[Dict[str, Any]]:
    """已启用通道 → 探测目标（SMTP / webhook）。停用的通道不生成目标。"""
    out: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("ext_id"):
            continue
        try:
            if not is_enabled(row):
                continue
        except Exception:  # noqa: BLE001 判据坏掉按"不探"处理
            continue
        channel_id = str(row.get("ext_id"))
        # 【一处真相（2026-11-14）】探针必须与发送用**同一份有效配置**：
        # 通道本地值为空而 .env 里有值（回落层）时，发送能发、探针却会"没目标"，
        # 于是"通道能发但端点连不上"这件事再也看不见（探针存在的唯一理由）。
        try:
            from agent.extensions.channel_env_bridge import effective_config
            cfg = effective_config(row)
        except Exception as exc:  # noqa: BLE001 解析失败退回原始配置（不假装有/没有）
            logger.debug("[dependency-probe] 有效配置解析失败（%s），退回原始配置",
                         type(exc).__name__)
            cfg = row.get("config") or {}
        ctype = str(cfg.get("channel_type") or "")
        if ctype == "smtp":
            host = str(cfg.get("smtp_server") or "")
            raw_port = cfg.get("smtp_port") or (465 if cfg.get("use_ssl") else 587)
            try:
                port = int(raw_port)
            except (TypeError, ValueError):
                port = 465 if cfg.get("use_ssl") else 587
            tls = bool(cfg.get("use_ssl")) or port == 465
            if host:
                out.append({"id": "channel:" + channel_id, "kind": "channel",
                            "host": host, "port": port, "tls": tls,
                            "label": "SMTP " + host})
        elif ctype == "webhook":
            url = str(cfg.get("webhook_url") or "")
            parsed = urlparse(url) if url else None
            if parsed and parsed.hostname:
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                out.append({"id": "channel:" + channel_id, "kind": "channel",
                            "host": parsed.hostname, "port": int(port),
                            "tls": parsed.scheme == "https",
                            "label": "Webhook " + parsed.hostname})
    return out


def targets_from_llm(base_url: str) -> List[Dict[str, Any]]:
    """LLM base_url → 探测目标（没配就返回空：没配置 ≠ 故障）。"""
    url = str(base_url or "").strip()
    if not url:
        return []
    parsed = urlparse(url if "//" in url else "https://" + url)
    if not parsed.hostname:
        return []
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return [{"id": "llm:endpoint", "kind": "endpoint", "host": parsed.hostname,
             "port": int(port), "tls": parsed.scheme == "https",
             "label": "LLM " + parsed.hostname}]


def load_targets() -> List[Dict[str, Any]]:
    """本轮要探的全部目标（通道 + LLM）。读不到任何配置 ⇒ 空列表（不探、不报）。"""
    targets: List[Dict[str, Any]] = []
    try:
        from agent.extensions.channels_installer import ChannelInstaller
        from agent.extensions.store import ExtensionStore

        installer = ChannelInstaller(ExtensionStore())
        targets.extend(targets_from_channels(installer.list_installed_channels() or [],
                                             installer.is_channel_enabled))
    except Exception as exc:  # noqa: BLE001 扩展存储读不到 ≠ 端点坏
        logger.debug("[dependency-probe] 通道目标读取失败（%s）", type(exc).__name__)
    try:
        raw = os.environ.get("LLM_BASE_URL", "") or os.environ.get("DEEPSEEK_BASE_URL", "")
        targets.extend(targets_from_llm(raw))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dependency-probe] LLM 目标读取失败（%s）", type(exc).__name__)
    return targets


def run_once(targets: Optional[List[Dict[str, Any]]] = None,
             *, timeout: float = PROBE_TIMEOUT_SEC,
             now: Optional[float] = None,
             persist: bool = True) -> Dict[str, Any]:
    """探一轮并返回快照（不启动线程）。"""
    rows = list(targets) if targets is not None else load_targets()
    results = []
    for target in rows:
        row = probe_target(target, timeout=timeout)
        target_id = str(row.get("id") or "")
        if row["ok"]:
            count = 0
        else:
            count = int(_fail_counts.get(target_id, 0)) + 1
        _fail_counts[target_id] = count
        row["consecutive_failures"] = count
        results.append(row)
    snapshot = {
        "at": _now_iso(now),
        "ok": all(r["ok"] for r in results) if results else True,
        "targets": len(results),
        "results": results,
    }
    if persist:
        _save(snapshot)
    global _snapshot
    with _lock:
        _snapshot = snapshot
    return snapshot


def _save(snapshot: Dict[str, Any]) -> None:
    """落盘（失败只记日志：探测是旁路）。"""
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(STATE_PATH))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[dependency-probe] 快照写入失败（%s）", type(exc).__name__)


def snapshot() -> Optional[Dict[str, Any]]:
    """最近一轮结果；还没跑过 ⇒ None（调用方按"源不可用"处理）。"""
    with _lock:
        if _snapshot is not None:
            return dict(_snapshot)
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 坏文件 ⇒ 当作没测过
        logger.warning("[dependency-probe] 快照读取失败（%s）", type(exc).__name__)
        return None


def _loop(interval: float) -> None:
    while True:
        try:
            run_once()
        except Exception as exc:  # noqa: BLE001 探测异常不能杀线程
            logger.warning("[dependency-probe] 本轮失败（%s）", type(exc).__name__)
        time.sleep(max(1.0, float(interval)))


def start(interval: float = PROBE_INTERVAL_SEC) -> Optional[threading.Thread]:
    """启动后台探测线程（幂等）。"""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        _thread = threading.Thread(target=_loop, args=(interval,),
                                   name="dependency_probe", daemon=True)
        _thread.start()
    logger.info("[dependency-probe] 已启动（每 %.0fs 一轮，只做 TCP/TLS 握手）", interval)
    return _thread


def reset_for_tests() -> None:
    """清空进程内快照与连续失败计数（用例隔离用；不动磁盘）。"""
    global _snapshot
    with _lock:
        _snapshot = None
        _fail_counts.clear()


__all__ = [
    "PROBE_TIMEOUT_SEC", "PROBE_INTERVAL_SEC", "STATE_REL_PATH", "STATE_PATH",
    "CODE_DNS", "CODE_TIMEOUT", "CODE_REFUSED", "CODE_TLS", "CODE_OTHER", "CODE_LABELS",
    "probe_target", "targets_from_channels", "targets_from_llm", "load_targets",
    "run_once", "snapshot", "start", "reset_for_tests",
]
