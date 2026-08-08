#!/usr/bin/env python3
"""LinkCache Prometheus exporter（零第三方依赖，标准库 http.server）。

暴露指标（GET /metrics）:
  yunshu_link_cache_memory_bytes{method="deep"|"model"}  深估算/模型估算字节数
  yunshu_link_cache_used_bytes                            实际口径（max(deep, model)）
  yunshu_link_cache_cards_total                           缓存卡数
  yunshu_link_cache_links_total                           缓存链接数
  yunshu_link_cache_alerted                               0/1 当前是否超阈值
  yunshu_link_cache_alerts_total                          累计告警次数（counter）
  yunshu_link_cache_scrapes_total                         抓取总次数
  yunshu_link_cache_errors_total                          采样异常次数

用途:
  - 内存趋势:   rate(yunshu_link_cache_memory_bytes[5m]) 或直接查 bytes
  - 告警频率:   increase(yunshu_link_cache_alerts_total[24h])（Grafana 柱状图）
  - 告警规则:   yunshu_link_cache_alerted == 1

用法:
  python scripts/exporter_link_cache.py \
      --cards-dir /opt/yunshu/knowledge/wiki --port 9108 \
      --threshold-mb 256
指标采样成本保护: /metrics 结果缓存 5 秒（重建 LinkCache 有开销）。

【不易】复用 monitor_link_cache_memory 的 load_cards/估算口径（同一事实源），
不重复实现加载逻辑，避免两处口径漂移。
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (PROJECT_ROOT / "scripts", PROJECT_ROOT / "packages" / "yunshu_cache_tools" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import monitor_link_cache_memory as monitor_mod  # noqa: E402

logger = logging.getLogger("exporter_link_cache")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)

CACHE_TTL_SEC = 5.0  # 采样结果缓存秒数（重建缓存有开销，避免高频 scrape 放大）

# ── 全局状态（带锁，ThreadingHTTPServer 并发访问保护）─────────────────────
_state_lock = threading.Lock()
_state: Dict = {
    "last_ts": 0.0,
    "metrics": "",
    "alerts_total": 0,
    "last_alerted": 0,
    "scrapes_total": 0,
    "errors_total": 0,
}


def _sample(cards_dir, threshold_mb) -> dict:
    """执行一次采样（与监控脚本同口径）。"""
    cards = monitor_mod.load_cards(cards_dir)
    from yunshu_cache_tools import LinkCache

    cache = LinkCache(cards)
    deep_bytes = monitor_mod.estimate_deep_size(cache._cache)
    model_bytes = monitor_mod.model_estimate(cache)
    used_bytes = max(deep_bytes, model_bytes)
    return {
        "deep_bytes": deep_bytes,
        "model_bytes": model_bytes,
        "used_bytes": used_bytes,
        "cards": cache.size,
        "total_links": cache.total_links,
        "alarmed": used_bytes > threshold_mb * 1024 * 1024,
    }


def _render(s: dict, threshold_mb: float) -> str:
    """渲染 Prometheus 文本格式。"""
    deep_mb = s["deep_bytes"] / 1024 / 1024
    model_mb = s["model_bytes"] / 1024 / 1024
    lines = [
        "# HELP yunshu_link_cache_memory_bytes LinkCache 内存占用（字节）",
        "# TYPE yunshu_link_cache_memory_bytes gauge",
        f'yunshu_link_cache_memory_bytes{{method="deep"}} {s["deep_bytes"]}',
        f'yunshu_link_cache_memory_bytes{{method="model"}} {s["model_bytes"]}',
        "yunshu_link_cache_used_bytes " + str(s["used_bytes"]),
        "# HELP yunshu_link_cache_cards_total 缓存卡数",
        "# TYPE yunshu_link_cache_cards_total gauge",
        f"yunshu_link_cache_cards_total {s['cards']}",
        "# HELP yunshu_link_cache_links_total 缓存链接数",
        "# TYPE yunshu_link_cache_links_total gauge",
        f"yunshu_link_cache_links_total {s['total_links']}",
        "# HELP yunshu_link_cache_alerted 当前是否超阈值（1=告警）",
        "# TYPE yunshu_link_cache_alerted gauge",
        f"yunshu_link_cache_alerted {int(s['alarmed'])}",
        "# HELP yunshu_link_cache_alerts_total 累计告警次数",
        "# TYPE yunshu_link_cache_alerts_total counter",
        f"yunshu_link_cache_alerts_total {_state['alerts_total']}",
        "# HELP yunshu_link_cache_scrapes_total 抓取总次数",
        "# TYPE yunshu_link_cache_scrapes_total counter",
        f"yunshu_link_cache_scrapes_total {_state['scrapes_total']}",
        "# HELP yunshu_link_cache_errors_total 采样异常次数",
        "# TYPE yunshu_link_cache_errors_total counter",
        f"yunshu_link_cache_errors_total {_state['errors_total']}",
        f'# LinkCache 最近一次采样: deep={deep_mb:.2f}MB model={model_mb:.2f}MB '
        f'alarmed={int(s["alarmed"])} 阈值={threshold_mb}MB',
    ]
    return "\n".join(lines) + "\n"


def refresh(cards_dir, threshold_mb) -> str:
    """刷新采样（带缓存与计数）。返回 Prometheus 文本。"""
    global _state
    now = time.time()
    with _state_lock:
        if now - _state["last_ts"] < CACHE_TTL_SEC and _state["metrics"]:
            _state["scrapes_total"] += 1
            return _state["metrics"]
        try:
            s = _sample(cards_dir, threshold_mb)
        except Exception as exc:
            logger.exception("采样异常: %r", exc)
            _state["errors_total"] += 1
            _state["last_ts"] = now
            return _state["metrics"] or ""
        if s["alarmed"] and not _state["last_alerted"]:
            _state["alerts_total"] += 1  # 仅状态翻转时累计，防同一次持续告警重复计数
            logger.error("ALERT LinkCache 内存超阈值: %s", json.dumps(s, ensure_ascii=False))
        _state["last_alerted"] = int(s["alarmed"])
        _state["scrapes_total"] += 1
        _state["last_ts"] = now
        _state["metrics"] = _render(s, threshold_mb)
        return _state["metrics"]


def make_handler(cards_dir, threshold_mb):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/metrics":
                self.send_error(404, "only /metrics supported")
                return
            body = refresh(cards_dir, threshold_mb).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):  # 抑制默认访问日志刷屏
            logger.debug(fmt, *args)

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkCache Prometheus exporter")
    parser.add_argument("--cards-dir", default=None, help="知识库 wiki 根目录（缺省用内置 mock）")
    parser.add_argument("--port", type=int, default=9108)
    parser.add_argument("--threshold-mb", type=float, default=256.0)
    args = parser.parse_args()

    # 启动前先跑一次，失败早暴露（避免"指标永远为空但端口存活"的假健康）
    try:
        refresh(args.cards_dir, args.threshold_mb)
        logger.info("首次采样成功: cards_dir=%s", args.cards_dir or "(mock)")
    except Exception as exc:
        logger.error("首次采样失败，拒绝启动: %r", exc)
        return 2

    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.cards_dir, args.threshold_mb))
    logger.info("LinkCache exporter 已启动: http://0.0.0.0:%d/metrics", args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
