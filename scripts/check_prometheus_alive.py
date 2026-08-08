#!/usr/bin/env python3
"""Prometheus 存活探针（独立守护，Prometheus 宕机时的降级告警兜底）。

【为什么需要它】Prometheus 宕机时，自身规则评估停止、Alertmanager 收不到推送，
ExporterDown 等告警**无法**产生——这是 Prometheus 单点故障的固有盲区。
本探针独立于 Prometheus/Alertmanager 运行（仅依赖 HTTP），由 cron 或
systemd timer 周期驱动，探测 Prometheus 健康端点，失败即告警通知，
形成监控链路的最后一道降级防线。

探测目标: GET {prom-url}/-/healthy（Prometheus 标准健康端点）
判定:   连续 --fail-threshold 次失败 → ALERT + 退出码 1（可在 webhook 通知）

用法（systemd/cron 集成，每 1 分钟）:
  */1 * * * * /opt/yunshu/venv/bin/python3 /opt/yunshu/scripts/check_prometheus_alive.py \
      --prom-url http://127.0.0.1:9090 --once --fail-threshold 3 \
      --webhook-url https://your-ops-webhook/alert \
      >> /var/log/yunshu/prom-health.log 2>&1

退出码: 0 健康 / 1 Prometheus 宕机（连续失败达阈值）/ 2 探针自身错误
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import urllib.request
from typing import Optional

logger = logging.getLogger("check_prometheus_alive")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)

HEALTH_PATH = "/-/healthy"


def probe(prom_url: str, timeout: float) -> bool:
    """单次健康探测。成功=HTTP 200 且响应体含 Healthy。"""
    url = prom_url.rstrip("/") + HEALTH_PATH
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return False
            body = resp.read(200).decode("utf-8", "replace")
            return "Healthy" in body
    except Exception:
        return False


def notify(payload: dict, webhook_url: Optional[str]) -> None:
    """告警通知；未配置 webhook 时记日志（journald 可见，可被采集）。"""
    if webhook_url:
        req = urllib.request.Request(
            webhook_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10):
                logger.info("告警通知已发送: %s", webhook_url)
        except Exception as exc:
            logger.error("告警通知发送失败: %r", exc)
    else:
        logger.error("ALERT Prometheus 不可达（未配置 webhook，请检查告警网关）")


def main() -> int:
    parser = argparse.ArgumentParser(description="Prometheus 存活探针（降级告警兜底）")
    parser.add_argument("--prom-url", default="http://127.0.0.1:9090")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--fail-threshold", type=int, default=3, help="连续失败次数达到即告警")
    parser.add_argument("--interval", type=float, default=5.0, help="失败重试间隔秒")
    parser.add_argument("--once", action="store_true", help="单次探测后退出（cron 模式）")
    parser.add_argument("--webhook-url", default=None, help="告警通知地址（缺省仅日志）")
    args = parser.parse_args()
    if args.fail_threshold < 1:
        print("[ERROR] --fail-threshold 必须 >= 1", file=sys.stderr)
        return 2

    consecutive_fail = 0
    while True:
        ok = probe(args.prom_url, args.timeout)
        if ok:
            consecutive_fail = 0
            logger.info("ok: Prometheus 健康 (%s)", args.prom_url)
            if args.once:
                return 0
        else:
            consecutive_fail += 1
            logger.warning("探测失败 %d/%d: %s", consecutive_fail, args.fail_threshold, args.prom_url)
            if consecutive_fail >= args.fail_threshold:
                payload = {
                    "alert": "PrometheusDown",
                    "severity": "critical",
                    "target": args.prom_url,
                    "consecutive_failures": consecutive_fail,
                    "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.error("ALERT Prometheus 服务宕机（连续 %d 次失败）", consecutive_fail)
                notify(payload, args.webhook_url)
                if args.once:
                    return 1
                consecutive_fail = 0  # 告警后重置，防刷屏（周期性重新告警）
            elif args.once:
                # 单次失败但未达连续失败阈值 → 视为健康（阈值语义）
                return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
