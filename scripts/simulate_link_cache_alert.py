#!/usr/bin/env python3
"""模拟 LinkCache 内存飙升场景，端到端验证监控告警机制触发与通知发送。

场景模拟（两层）:
  [飙升模拟] 内存中构造 `--cards` 张卡 × `--links-per-card` 条双链的 LinkCache
             （独立包 yunshu_cache_tools，零第三方依赖），用与监控脚本同一口径的
             estimate_deep_size 深估算真实内存占用——模拟生产大知识库缓存膨胀。
  [端到端告警] 以真实知识库目录（`--cards-dir`，缺省自动生成小规模临时库）
              + 低阈值调用监控脚本（--once --json），断言:
              退出码 1 + stderr 含 ALERT + stdout alarmed=true。
  [通知验证]  --webhook-url 指定时向告警网关 POST（Content-Type: application/json）；
              未指定时打印模拟通知（钉钉/邮件/自定义 webhook 均可按此接入）。

用法:
  python scripts/simulate_link_cache_alert.py [--cards 20000] [--links-per-card 8]
      [--cards-dir DIR] [--threshold-mb M] [--webhook-url URL] [--json]

退出码: 0 告警链路验证通过（告警已触发 + 通知已发送/模拟）/ 1 验证失败

【不易】验证必须走监控脚本真实入口（subprocess），不绕过退出码契约：
  告警触发=退出码 1，通知=ALERT 消费方动作，两者缺一不可。
【简易】轻量 Card 用 SimpleNamespace（LinkCache 仅读 slug/links），不落盘 2 万文件。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
import types
import urllib.request
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for _p in (PROJECT_ROOT / "scripts", PROJECT_ROOT / "packages" / "yunshu_cache_tools" / "src"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import monitor_link_cache_memory as monitor_mod  # noqa: E402
from yunshu_cache_tools import LinkCache  # noqa: E402

logger = logging.getLogger("simulate_link_cache_alert")
logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s", stream=sys.stderr)


def build_big_cache(cards: int, links_per_card: int) -> LinkCache:
    """内存中构造大规模 LinkCache（模拟内存飙升数据源）。"""
    store: dict[str, Any] = {}
    for i in range(cards):
        # 均匀回指已存在卡 + 少量 ghost，模拟真实链接分布
        targets = [f"card{(i + j) % cards}" for j in range(1, links_per_card + 1)]
        store[f"card{i}"] = types.SimpleNamespace(slug=f"card{i}", links=targets)
    t0 = time.perf_counter()
    cache = LinkCache(store)
    logger.info(
        "飙升模拟: 卡数=%d 链接=%d LinkCache 构建耗时=%.1fms",
        cards, cards * links_per_card, (time.perf_counter() - t0) * 1000,
    )
    return cache


def make_small_kb(cards: int) -> Path:
    """生成一个小规模真实知识库目录（监控脚本 --cards-dir 数据源）。

    用 CardStore.create 写卡：与部署演练同一机制，保证 list() 可读
    （手工 md 曾因缺 schema 字段导致解析为空、估算为 0）。
    """
    from agent.knowledge import Card, CardStore

    tmp = Path(tempfile.mkdtemp(prefix="sim_cache_alert_")) / "knowledge" / "wiki"
    store = CardStore(tmp)
    for i in range(cards):
        store.create(Card(
            title=f"Card{i}", slug=f"card{i}", status="current", type="concepts",
            source="simulate-alert", date="2026-08-07",
            content=f"内容占位 {i}", insight=f"洞察 {i}",
            links=[f"card{(i + 1) % cards}"],
        ))
    return tmp


def send_notification(payload: dict, webhook_url: Optional[str]) -> bool:
    """发送告警通知；未配置 webhook 时打印模拟通知并返回 True。"""
    if webhook_url:
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
        except Exception as exc:
            logger.error("通知发送失败: %r", exc)
            return False
        logger.info("通知已发送 webhook=%s HTTP=%d", webhook_url, status)
        return True
    # 模拟通知：钉钉/邮件/自定义网关均可按此结构接入
    logger.info(
        "【模拟通知】severity=CRITICAL %s 已发送（未配置 --webhook-url）",
        payload.get("alert", ""),
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="LinkCache 内存飙升告警链路验证")
    parser.add_argument("--cards", type=int, default=20000, help="飙升模拟卡数（默认 20000）")
    parser.add_argument("--links-per-card", type=int, default=8, help="每卡链接数（默认 8）")
    parser.add_argument("--cards-dir", default=None, help="监控数据源知识库目录（缺省自动生成）")
    parser.add_argument("--threshold-mb", type=float, default=None, help="告警阈值 MB（缺省取实际一半）")
    parser.add_argument("--webhook-url", default=None, help="告警 webhook 地址（缺省模拟通知）")
    parser.add_argument("--json", action="store_true", help="最终结果输出 JSON 到 stdout")
    args = parser.parse_args()

    issues: list[str] = []

    # ── 1. 飙升模拟：大规模 LinkCache 内存深估算 ───────────────────────────
    big_cache = build_big_cache(args.cards, args.links_per_card)
    big_bytes = monitor_mod.estimate_deep_size(big_cache._cache)
    logger.info(
        "飙升内存估算: %.2f MB（%d 卡 / %d 链接）",
        big_bytes / 1024 / 1024, args.cards, args.cards * args.links_per_card,
    )

    # ── 2. 端到端告警：调用监控脚本真实入口（两次调用） ─────────────────────
    tmp_kb = None
    cards_dir = args.cards_dir
    if not cards_dir:
        tmp_kb = make_small_kb(30)
        cards_dir = tmp_kb
    monitor_cmd = [
        sys.executable, str(PROJECT_ROOT / "scripts" / "monitor_link_cache_memory.py"),
        "--cards-dir", str(cards_dir), "--once", "--json",
    ]

    def _run_monitor(threshold_mb: float) -> Tuple[int, dict, str]:
        p = subprocess.run(
            monitor_cmd + ["--threshold-mb", str(threshold_mb)],
            capture_output=True, text=True, timeout=300,
        )
        try:
            data = json.loads(p.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            data = {}
        return p.returncode, data, p.stderr

    # 第一次：探测数据源实际内存（正常阈值下不应告警）
    _, probe, _ = _run_monitor(args.threshold_mb if args.threshold_mb is not None else 256.0)
    used_mb = probe.get("used_mb") or 0.0
    if used_mb <= 0:
        issues.append(f"监控探测失败: used_mb={used_mb} 结果={probe}")

    # 第二次：阈值收紧为实际一半 → 模拟内存突然飙升突破阈值，必须告警
    alert_threshold = (args.threshold_mb if args.threshold_mb is not None else max(0.0001, used_mb / 2))
    rc, result, stderr = _run_monitor(alert_threshold)
    has_alert = bool(result.get("alarmed"))
    if rc != 1 or not has_alert:
        issues.append(f"告警未触发: 退出码={rc}（期望 1） alarmed={has_alert}")
    # 文本告警通道：非 --json 模式下 stderr 必须输出 ALERT 标记
    text_proc = subprocess.run(
        monitor_cmd[:-1] + ["--threshold-mb", str(alert_threshold)],
        capture_output=True, text=True, timeout=300,
    )
    if "ALERT" not in text_proc.stderr:
        issues.append("文本告警通道缺失: stderr 无 ALERT 标记")
    logger.info(
        "告警触发验证: 第一次探测 %.3fMB（不告警）→ 第二次阈值 %.3fMB 退出码=%d alarmed=%s",
        used_mb, alert_threshold, rc, has_alert,
    )

    # ── 3. 通知发送/模拟 ────────────────────────────────────────────────────
    notify_payload = {
        "alert": "yunshu_link_cache_high_memory",
        "severity": "CRITICAL",
        "used_mb": result.get("used_mb"),
        "threshold_mb": alert_threshold,
        "simulated_spike_mb": round(big_bytes / 1024 / 1024, 2),
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    notified = send_notification(notify_payload, args.webhook_url)
    if not notified:
        issues.append("通知发送失败")

    if tmp_kb is not None:
        import shutil
        shutil.rmtree(tmp_kb, ignore_errors=True)

    report = {
        "verdict": "告警链路验证通过" if not issues else "验证失败",
        "simulated_spike_mb": round(big_bytes / 1024 / 1024, 2),
        "cards": args.cards,
        "links": args.cards * args.links_per_card,
        "threshold_mb": alert_threshold,
        "monitor_rc": rc,
        "alarmed": has_alert,
        "notified": notified,
        "issues": issues,
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        logger.info("验证结论: %s", report["verdict"])
        for issue in issues:
            logger.error(" - %s", issue)
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
