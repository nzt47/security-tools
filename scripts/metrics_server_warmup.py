#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metrics-server 预热脚本 — 消除冷启动指标延迟

【不易】SLO 不变量: 预热后 HPA 能在首个采集周期内感知 CPU 变化
【变易】预热参数可调（VU/持续时间/等待周期），适配不同环境
【简易】零外部依赖（仅 kubectl + Python 标准库 urllib）

背景:
  2026-08-01 扩容基准测试发现 metrics-server 冷启动延迟 72s：
  - 流量开始后 72s 内 CPU 报告始终为 1%（指标未更新）
  - 导致 HPA 无法及时触发扩容，端到端耗时 115s > 60s SLO
  本脚本通过发送少量预热流量，让 metrics-server 提前采集到 CPU 指标，
  消除冷启动延迟。

用法:
    # 独立运行（在正式巡检/基准测试前执行）
    python scripts/metrics_server_warmup.py

    # 自定义预热参数
    python scripts/metrics_server_warmup.py \\
        --namespace production \\
        --service-name skill-retrieval-service \\
        --service-port 8080 \\
        --probe-endpoint /match \\
        --warmup-vu 10 \\
        --warmup-duration 20 \\
        --settle-wait 20

    # 集成到巡检脚本（被 hpa_scale_patrol.py 调用）
    from metrics_server_warmup import MetricsServerWarmup
    warmup = MetricsServerWarmup(config)
    warmup.run()
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


# ════════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════════

@dataclass
class WarmupConfig:
    """预热配置"""
    namespace: str = "production"
    service_name: str = "skill-retrieval-service"
    service_port: int = 8080
    probe_endpoint: str = "/match"
    warmup_vu: int = 10               # 预热并发数（少量，不触发 HPA 扩容）
    warmup_duration: int = 20         # 预热持续时间（秒，需 ≥ metrics-server 采集周期）
    settle_wait: int = 20             # 预热后等待指标稳定时间（秒）
    image: str = "skill-retrieval:local"
    verbose: bool = False


@dataclass
class WarmupResult:
    """预热结果"""
    success: bool
    warmup_id: str
    cpu_before: Optional[float]       # 预热前 CPU
    cpu_after: Optional[float]        # 预热后 CPU
    cpu_delta: Optional[float]        # CPU 变化量
    elapsed_sec: float
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "warmup_id": self.warmup_id,
            "cpu_before": self.cpu_before,
            "cpu_after": self.cpu_after,
            "cpu_delta": self.cpu_delta,
            "elapsed_sec": round(self.elapsed_sec, 2),
            "message": self.message,
        }


# ════════════════════════════════════════════════════════════════════
#  K8s 辅助函数
# ════════════════════════════════════════════════════════════════════

def kubectl_json(args: list[str], timeout: int = 30) -> dict:
    """执行 kubectl -o json 并解析"""
    cmd = ["kubectl"] + args + ["-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"kubectl 失败: {result.stderr.strip()}")
    return json.loads(result.stdout or "{}")


def get_hpa_cpu(namespace: str, hpa_name: str) -> Optional[float]:
    """获取 HPA 当前 CPU 利用率（%）"""
    hpa = kubectl_json(["get", "hpa", "-n", namespace, hpa_name])
    for m in hpa.get("status", {}).get("currentMetrics", []):
        if m.get("resource", {}).get("name") == "cpu":
            val = m.get("resource", {}).get("current", {}).get("averageUtilization")
            return float(val) if val is not None else None
    return None


def get_hpa_replicas(namespace: str, hpa_name: str) -> int:
    """获取 HPA 当前副本数"""
    hpa = kubectl_json(["get", "hpa", "-n", namespace, hpa_name])
    return int(hpa.get("status", {}).get("currentReplicas", 0) or 0)


# ════════════════════════════════════════════════════════════════════
#  预热流量生成器（urllib 标准库，零依赖）
# ════════════════════════════════════════════════════════════════════

def send_warmup_traffic(config: WarmupConfig) -> int:
    """发送预热流量，返回发送的请求总数

    【规范】使用 urllib 标准库，不依赖 requests
    """
    endpoint = (
        f"http://{config.service_name}.{config.namespace}"
        f".svc.cluster.local:{config.service_port}{config.probe_endpoint}"
    )
    body = json.dumps({"query": "warmup-probe"}).encode("utf-8")
    request_count = 0

    def worker(_):
        nonlocal request_count
        end = time.time() + config.warmup_duration
        while time.time() < end:
            try:
                req = urllib.request.Request(
                    endpoint, data=body, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=2)
                request_count += 1
            except Exception:
                pass

    print(f"  [INFO] 预热流量: {config.warmup_vu} VU × {config.warmup_duration}s "
          f"→ {endpoint}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=config.warmup_vu) as pool:
        list(pool.map(worker, range(config.warmup_vu)))

    return request_count


# ════════════════════════════════════════════════════════════════════
#  预热主流程
# ════════════════════════════════════════════════════════════════════

class MetricsServerWarmup:
    """metrics-server 预热器"""

    def __init__(self, config: WarmupConfig):
        self.config = config
        self.warmup_id = f"warmup-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    def run(self, hpa_name: str = "skill-retrieval-hpa") -> WarmupResult:
        """执行预热流程

        流程:
        1. 记录预热前 CPU
        2. 发送少量预热流量（不触发 HPA 扩容）
        3. 等待 metrics-server 采集周期（指标更新）
        4. 记录预热后 CPU，验证指标已更新
        """
        t_start = time.time()

        print(f"\n{'='*60}")
        print(f"  metrics-server 预热 | ID={self.warmup_id}")
        print(f"  HPA: {hpa_name} (ns={self.config.namespace})")
        print(f"{'='*60}")

        # ── 阶段 1: 记录预热前 CPU ──
        print(f"\n── 阶段 1: 记录预热前状态 ──")
        try:
            cpu_before = get_hpa_cpu(self.config.namespace, hpa_name)
            replicas = get_hpa_replicas(self.config.namespace, hpa_name)
        except RuntimeError as e:
            return WarmupResult(
                success=False, warmup_id=self.warmup_id,
                cpu_before=None, cpu_after=None, cpu_delta=None,
                elapsed_sec=time.time() - t_start,
                message=f"获取 HPA 状态失败: {e}",
            )

        print(f"  [INFO] 预热前: replicas={replicas}, cpu={cpu_before}%")

        # ── 阶段 2: 发送预热流量 ──
        print(f"\n── 阶段 2: 发送预热流量 ──")
        try:
            req_count = send_warmup_traffic(self.config)
            print(f"  [OK] 预热请求发送: {req_count} 次")
        except Exception as e:
            return WarmupResult(
                success=False, warmup_id=self.warmup_id,
                cpu_before=cpu_before, cpu_after=None, cpu_delta=None,
                elapsed_sec=time.time() - t_start,
                message=f"预热流量发送失败: {e}",
            )

        # ── 阶段 3: 等待指标采集周期 ──
        print(f"\n── 阶段 3: 等待 metrics-server 指标更新（{self.config.settle_wait}s）──")
        time.sleep(self.config.settle_wait)

        # ── 阶段 4: 验证指标已更新 ──
        print(f"\n── 阶段 4: 验证指标更新 ──")
        try:
            cpu_after = get_hpa_cpu(self.config.namespace, hpa_name)
        except RuntimeError as e:
            return WarmupResult(
                success=False, warmup_id=self.warmup_id,
                cpu_before=cpu_before, cpu_after=None, cpu_delta=None,
                elapsed_sec=time.time() - t_start,
                message=f"获取预热后 CPU 失败: {e}",
            )

        cpu_delta = (cpu_after - cpu_before) if (cpu_before is not None and cpu_after is not None) else None
        elapsed = time.time() - t_start

        print(f"  [INFO] 预热后: cpu={cpu_after}%")
        print(f"  [INFO] CPU 变化: {cpu_before}% → {cpu_after}% (Δ={cpu_delta}%)")

        # 判定: CPU 有变化说明 metrics-server 指标已更新
        if cpu_delta is not None and cpu_delta > 0:
            success = True
            message = (f"预热成功: CPU 从 {cpu_before}% 升至 {cpu_after}%，"
                       f"metrics-server 指标已激活")
        elif cpu_delta is not None and cpu_delta == 0:
            # CPU 未变化，可能预热流量不足或 metrics-server 仍未更新
            success = False
            message = (f"预热可能未生效: CPU 未变化（{cpu_before}%→{cpu_after}%），"
                       f"建议增大 --warmup-vu 或 --settle-wait")
        else:
            # CPU 指标无法获取
            success = False
            message = f"无法获取 CPU 指标，metrics-server 可能异常"

        status = "✓ PASS" if success else "✗ FAIL"
        print(f"\n  [{status}] {message}")
        print(f"  [INFO] 预热总耗时: {elapsed:.1f}s")

        return WarmupResult(
            success=success,
            warmup_id=self.warmup_id,
            cpu_before=cpu_before,
            cpu_after=cpu_after,
            cpu_delta=cpu_delta,
            elapsed_sec=elapsed,
            message=message,
        )


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def parse_args() -> tuple[WarmupConfig, Optional[str], str]:
    """解析参数，返回 (配置, 输出路径, HPA名称)"""
    parser = argparse.ArgumentParser(
        description="metrics-server 预热脚本（消除冷启动指标延迟）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--namespace", default="production")
    parser.add_argument("--service-name", default="skill-retrieval-service")
    parser.add_argument("--service-port", type=int, default=8080)
    parser.add_argument("--probe-endpoint", default="/match")
    parser.add_argument("--warmup-vu", type=int, default=10,
                        help="预热并发数（默认 10，少量不触发扩容）")
    parser.add_argument("--warmup-duration", type=int, default=20,
                        help="预热持续时间秒（默认 20，需 ≥ 采集周期）")
    parser.add_argument("--settle-wait", type=int, default=20,
                        help="预热后等待指标稳定秒（默认 20）")
    parser.add_argument("--image", default="skill-retrieval:local")
    parser.add_argument("--hpa-name", default="skill-retrieval-hpa")
    parser.add_argument("--output", default=None, help="结果输出 JSON 文件路径")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # 【规范】output 单独提取，不传入 WarmupConfig
    output_path = args.output
    hpa_name = args.hpa_name
    config_fields = {
        "namespace", "service_name", "service_port", "probe_endpoint",
        "warmup_vu", "warmup_duration", "settle_wait", "image", "verbose",
    }
    config_kwargs = {k: getattr(args, k) for k in config_fields}
    return WarmupConfig(**config_kwargs), output_path, hpa_name


def main() -> int:
    config, output_path, hpa_name = parse_args()
    warmup = MetricsServerWarmup(config)
    result = warmup.run(hpa_name)

    result_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    print(f"\n{'─'*60}")
    print(f"  预热结果 (ID={result.warmup_id})")
    print(f"{'─'*60}")
    print(result_json)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"\n  [INFO] 结果已写入 {output_path}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
