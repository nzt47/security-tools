#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
巡检前预热 + 指标延迟基准测量

【不易】预热后指标延迟须显著低于预热前（改善率 ≥50% 为有效）
【变易】预热参数 + 延迟探测参数均可调，适配不同集群规模
【简易】通过 kubectl exec 在集群内 Pod 发送流量，零外部依赖

流程:
  1. 预热前: 发送少量探测流量，测量"流量→CPU 变化"延迟（冷启动基准）
  2. 执行 metrics-server 预热（发送少量流量激活指标采集）
  3. 预热后: 再次测量延迟（热启动基准）
  4. 输出对比 JSON，供巡检报告"预热效果对比"章节引用

指标延迟定义:
  从探测流量开始到 HPA CPU 利用率首次变化 ≥阈值（默认 2%）的时间。
  包含: 流量到达 + CPU 累积 + metrics-server 采集周期 + HPA controller 轮询。

流量发送方式:
  通过 kubectl exec 在 service Pod 内执行内联 Python 脚本（urllib 标准库），
  避免本地无法解析集群内 DNS 的问题。

用法:
    # 独立运行（巡检前手动执行）
    python scripts/warmup_before_patrol.py --output /tmp/warmup-before-patrol.json

    # 自定义预热和探测参数
    python scripts/warmup_before_patrol.py \\
        --namespace production \\
        --hpa-name skill-retrieval-hpa \\
        --warmup-vu 10 --warmup-duration 20 \\
        --probe-vu 5 --probe-duration 20

    # 集成到巡检流程（CronJob 中先执行本脚本，再执行巡检）
    python /app/scripts/warmup_before_patrol.py --output /tmp/warmup-result.json && \\
    python /app/scripts/hpa_scale_patrol.py --output /tmp/patrol-result.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# 【简易】复用 metrics_server_warmup 的 K8s 辅助函数和配置类
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from metrics_server_warmup import (  # noqa: E402
    WarmupConfig,
    get_hpa_cpu,
    get_hpa_replicas,
    kubectl_json,
)
# 【不易】兼容性校验逻辑抽取为共享模块 compat_check，与 hpa_scale_patrol.py
#        复用同一来源，避免双份维护导致判定逻辑漂移
from compat_check import (  # noqa: E402
    CompatibilityCheckResult,
    check_k8s_compatibility,
    print_compat_result,
)


# ════════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════════

@dataclass
class LatencyProbeConfig:
    """延迟探测配置（测量指标延迟，非触发扩容）"""
    probe_vu: int = 5                  # 探测并发数（少量，不触发 HPA 扩容）
    probe_duration: int = 20           # 探测持续时间（秒，需 ≥ 采集间隔 + HPA 轮询）
    poll_interval: float = 3.0         # CPU 轮询间隔（秒）
    cpu_change_threshold: float = 2.0  # CPU 变化阈值（%，判定指标已响应）


# ════════════════════════════════════════════════════════════════════
#  集群内流量发送（kubectl exec 内联脚本）
# ════════════════════════════════════════════════════════════════════

def get_service_pod(namespace: str, service_name: str) -> str:
    """获取 service 的一个运行中 Pod 名称

    【防御】优先选择 running 且 ready 的 Pod
    """
    cmd = [
        "kubectl", "get", "pod", "-n", namespace,
        f"-l=app={service_name}",
        "--field-selector=status.phase=Running",
        "-o", "jsonpath={.items[0].metadata.name}",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        # fallback: 不按 label 查找，直接获取命名空间第一个 Pod
        cmd = [
            "kubectl", "get", "pod", "-n", namespace,
            "-o", "jsonpath={.items[0].metadata.name}",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    pod = result.stdout.strip()
    if not pod:
        raise RuntimeError(f"未找到 {namespace}/{service_name} 的运行中 Pod")
    return pod


def build_inline_traffic_script(
    endpoint: str,
    vu: int,
    duration: int,
    body_query: str = "latency-probe",
) -> str:
    """生成集群内执行的内联 Python 流量发送脚本

    【简易】使用 urllib 标准库（零依赖），紧循环无 sleep 最大化 QPS
    """
    return f"""
import concurrent.futures, time, json, urllib.request
endpoint = "{endpoint}"
vu = {vu}
duration = {duration}
body = json.dumps({{"query": "{body_query}"}}).encode("utf-8")
count = 0

def worker(_):
    global count
    end = time.time() + duration
    while time.time() < end:
        try:
            req = urllib.request.Request(endpoint, data=body, method="POST",
                                         headers={{"Content-Type": "application/json"}})
            urllib.request.urlopen(req, timeout=2)
            count += 1
        except Exception:
            pass

print(f"traffic-started endpoint={{endpoint}} vu={{vu}} duration={{duration}}", flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=vu) as pool:
    list(pool.map(worker, range(vu)))
print(f"traffic-finished count={{count}}", flush=True)
"""


def start_traffic_in_pod(
    namespace: str,
    pod_name: str,
    endpoint: str,
    vu: int,
    duration: int,
    body_query: str = "latency-probe",
) -> subprocess.Popen:
    """通过 kubectl exec 在 Pod 中启动流量发送（异步，返回 Popen）

    【变易】用 Popen 异步启动，不阻塞主线程的 CPU 轮询
    """
    script = build_inline_traffic_script(endpoint, vu, duration, body_query)
    cmd = ["kubectl", "exec", "-n", namespace, pod_name, "--", "python", "-c", script]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    # 等待启动信号
    time.sleep(1)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"流量脚本启动失败: {err.strip()}")
    return proc


def stop_traffic_proc(proc: subprocess.Popen) -> int:
    """停止流量发送进程，返回发送的请求总数"""
    try:
        proc.terminate()
        out, _ = proc.communicate(timeout=10)
        # 解析 "traffic-finished count=N" 或从 stdout 提取
        for line in (out or "").splitlines():
            if "count=" in line:
                try:
                    return int(line.split("count=")[1].strip())
                except (ValueError, IndexError):
                    pass
    except subprocess.TimeoutExpired:
        proc.kill()
    except Exception:
        pass
    return 0


# ════════════════════════════════════════════════════════════════════
#  指标延迟探测器
# ════════════════════════════════════════════════════════════════════

@dataclass
class LatencyResult:
    """单次延迟探测结果"""
    latency_sec: Optional[float]       # 从流量开始到 CPU 变化的时间（秒）
    cpu_baseline: Optional[float]      # 探测前 CPU 基线（%）
    cpu_peak: Optional[float]          # 探测期间 CPU 峰值（%）
    cpu_delta: Optional[float]         # CPU 变化量（峰值 - 基线）
    timed_out: bool                    # 是否超时（探测期间 CPU 未变化 ≥ 阈值）
    request_count: int = 0             # 发送的探测请求总数
    samples: list[dict] = field(default_factory=list)  # CPU 采样时间线

    def to_dict(self) -> dict:
        return {
            "latency_sec": round(self.latency_sec, 2) if self.latency_sec is not None else None,
            "cpu_baseline": self.cpu_baseline,
            "cpu_peak": self.cpu_peak,
            "cpu_delta": round(self.cpu_delta, 2) if self.cpu_delta is not None else None,
            "timed_out": self.timed_out,
            "request_count": self.request_count,
            "sample_count": len(self.samples),
            "samples": self.samples,
        }


class MetricsLatencyProbe:
    """指标延迟探测器：测量从流量开始到 CPU 指标变化的时间

    原理:
      通过 kubectl exec 在 service Pod 内发送探测流量，
      同时在本地轮询 HPA CPU 利用率。
      当 CPU 变化超过阈值时，记录经过的时间 = 指标延迟。

    【防御】探测流量控制在少量（默认 5 VU），不触发 HPA 扩容。
    """

    def __init__(
        self,
        warmup_config: WarmupConfig,
        probe_config: LatencyProbeConfig,
        hpa_name: str,
        pod_name: str,
    ):
        self.warmup_config = warmup_config
        self.probe_config = probe_config
        self.hpa_name = hpa_name
        self.pod_name = pod_name

    def _build_endpoint(self) -> str:
        # 【关键】集群内 Pod 访问 service 用 localhost（同 Pod）或 Service DNS
        # exec 进入的是 service 自身的 Pod，用 localhost 即可
        return (
            f"http://localhost:{self.warmup_config.service_port}"
            f"{self.warmup_config.probe_endpoint}"
        )

    def measure(self, label: str = "") -> LatencyResult:
        """执行一次延迟探测

        Args:
            label: 探测标签（如 "预热前" / "预热后"），用于日志输出
        """
        endpoint = self._build_endpoint()

        # ── 记录基线 CPU ──
        try:
            cpu_baseline = get_hpa_cpu(self.warmup_config.namespace, self.hpa_name)
        except RuntimeError:
            cpu_baseline = None

        print(f"  [INFO] {label} 基线 CPU: {cpu_baseline}%")
        print(f"  [INFO] 发送探测流量: {self.probe_config.probe_vu} VU × "
              f"{self.probe_config.probe_duration}s → {endpoint} (pod={self.pod_name})")

        samples: list[dict] = []
        t_start = time.time()
        cpu_peak = cpu_baseline
        latency_sec: Optional[float] = None

        # ── 通过 kubectl exec 启动探测流量（异步）──
        try:
            proc = start_traffic_in_pod(
                self.warmup_config.namespace,
                self.pod_name,
                endpoint,
                self.probe_config.probe_vu,
                self.probe_config.probe_duration,
                body_query=f"latency-{label}",
            )
        except RuntimeError as e:
            print(f"  [ERROR] 流量启动失败: {e}")
            return LatencyResult(
                latency_sec=None, cpu_baseline=cpu_baseline, cpu_peak=cpu_peak,
                cpu_delta=0.0, timed_out=True, request_count=0, samples=[],
            )

        # ── 轮询 CPU（主线程）──
        while True:
            elapsed = time.time() - t_start
            if elapsed > self.probe_config.probe_duration:
                break
            if proc.poll() is not None:
                # 流量脚本已结束
                break

            try:
                cpu = get_hpa_cpu(self.warmup_config.namespace, self.hpa_name)
            except RuntimeError:
                cpu = None

            if cpu is not None:
                if cpu_peak is None or cpu > cpu_peak:
                    cpu_peak = cpu
                samples.append({"t": round(elapsed, 2), "cpu": cpu})
                # 【不易】检测 CPU 变化超过阈值 → 记录延迟
                if cpu_baseline is not None:
                    delta = cpu - cpu_baseline
                    if delta >= self.probe_config.cpu_change_threshold and latency_sec is None:
                        latency_sec = elapsed
                        print(f"  [OK] {label} 指标延迟: {latency_sec:.1f}s "
                              f"(CPU {cpu_baseline}% → {cpu}%, Δ={delta:.1f}%)")
                        break

            time.sleep(self.probe_config.poll_interval)

        # 停止流量并获取请求计数
        request_count = stop_traffic_proc(proc)

        timed_out = latency_sec is None
        cpu_delta = (
            (cpu_peak - cpu_baseline)
            if (cpu_peak is not None and cpu_baseline is not None)
            else None
        )

        if timed_out:
            print(f"  [WARN] {label} 探测超时: {self.probe_config.probe_duration}s 内 "
                  f"CPU 未变化 ≥{self.probe_config.cpu_change_threshold}% "
                  f"(基线={cpu_baseline}%, 峰值={cpu_peak}%, 请求={request_count})")

        return LatencyResult(
            latency_sec=latency_sec,
            cpu_baseline=cpu_baseline,
            cpu_peak=cpu_peak,
            cpu_delta=cpu_delta,
            timed_out=timed_out,
            request_count=request_count,
            samples=samples,
        )


# ════════════════════════════════════════════════════════════════════
#  巡检前预热编排器
# ════════════════════════════════════════════════════════════════════

@dataclass
class WarmupBeforePatrolResult:
    """巡检前预热 + 延迟基准综合结果"""
    warmup_id: str
    timestamp: str
    hpa_name: str
    namespace: str
    pod_name: str
    metrics_resolution_sec: Optional[int]
    latency_before: dict                   # 预热前延迟基准
    warmup_result: dict                    # 预热结果
    latency_after: dict                    # 预热后延迟基准
    latency_improvement_sec: Optional[float]
    latency_improvement_pct: Optional[float]
    success: bool
    message: str

    def to_dict(self) -> dict:
        return {
            "warmup_id": self.warmup_id,
            "timestamp": self.timestamp,
            "hpa_name": self.hpa_name,
            "namespace": self.namespace,
            "pod_name": self.pod_name,
            "metrics_resolution_sec": self.metrics_resolution_sec,
            "latency_before": self.latency_before,
            "warmup_result": self.warmup_result,
            "latency_after": self.latency_after,
            "latency_improvement_sec": (
                round(self.latency_improvement_sec, 2)
                if self.latency_improvement_sec is not None else None
            ),
            "latency_improvement_pct": (
                round(self.latency_improvement_pct, 1)
                if self.latency_improvement_pct is not None else None
            ),
            "success": self.success,
            "message": self.message,
        }


def get_metrics_resolution() -> Optional[int]:
    """获取 metrics-server 当前采集间隔（秒）"""
    try:
        result = subprocess.run(
            ["kubectl", "get", "deploy", "metrics-server", "-n", "kube-system",
             "-o", "jsonpath={.spec.template.spec.containers[0].args}"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            for part in result.stdout.replace("[", "").replace("]", "").replace('"', "").split(","):
                part = part.strip()
                if "metric-resolution" in part:
                    val = part.split("=")[1].strip().rstrip("s")
                    return int(val)
    except Exception:
        pass
    return None


def run_warmup_traffic(
    namespace: str,
    pod_name: str,
    warmup_config: WarmupConfig,
    hpa_name: str,
) -> dict:
    """执行预热流量注入（通过 kubectl exec）

    返回预热结果字典（与 metrics_server_warmup.WarmupResult.to_dict() 结构一致）
    """
    endpoint = f"http://localhost:{warmup_config.service_port}{warmup_config.probe_endpoint}"
    t_start = time.time()

    # 记录预热前 CPU
    try:
        cpu_before = get_hpa_cpu(namespace, hpa_name)
    except RuntimeError:
        cpu_before = None

    print(f"  [INFO] 预热前 CPU: {cpu_before}%")
    print(f"  [INFO] 发送预热流量: {warmup_config.warmup_vu} VU × "
          f"{warmup_config.warmup_duration}s → {endpoint}")

    # 通过 kubectl exec 发送预热流量
    try:
        proc = start_traffic_in_pod(
            namespace, pod_name, endpoint,
            warmup_config.warmup_vu, warmup_config.warmup_duration,
            body_query="warmup",
        )
        proc.wait(timeout=warmup_config.warmup_duration + 30)
        request_count = stop_traffic_proc(proc)
        print(f"  [OK] 预热请求发送: {request_count} 次")
    except Exception as e:
        return {
            "success": False,
            "warmup_id": "inline",
            "cpu_before": cpu_before,
            "cpu_after": None,
            "cpu_delta": None,
            "elapsed_sec": round(time.time() - t_start, 2),
            "message": f"预热流量发送失败: {e}",
        }

    # 等待指标稳定
    print(f"  [INFO] 等待指标稳定: {warmup_config.settle_wait}s")
    time.sleep(warmup_config.settle_wait)

    # 记录预热后 CPU
    try:
        cpu_after = get_hpa_cpu(namespace, hpa_name)
    except RuntimeError:
        cpu_after = None

    cpu_delta = (
        (cpu_after - cpu_before)
        if (cpu_before is not None and cpu_after is not None)
        else None
    )
    elapsed = time.time() - t_start

    print(f"  [INFO] 预热后 CPU: {cpu_after}%")
    print(f"  [INFO] CPU 变化: {cpu_before}% → {cpu_after}% (Δ={cpu_delta}%)")

    if cpu_delta is not None and cpu_delta > 0:
        success = True
        message = f"预热成功: CPU {cpu_before}% → {cpu_after}%，metrics-server 指标已激活"
    elif cpu_delta is not None and cpu_delta == 0:
        success = False
        message = f"预热未生效: CPU 未变化（{cpu_before}%→{cpu_after}%），建议增大 --warmup-vu"
    else:
        success = False
        message = "无法获取 CPU 指标，metrics-server 可能异常"

    return {
        "success": success,
        "warmup_id": "inline",
        "cpu_before": cpu_before,
        "cpu_after": cpu_after,
        "cpu_delta": cpu_delta,
        "elapsed_sec": round(elapsed, 2),
        "message": message,
    }


class WarmupBeforePatrol:
    """巡检前预热编排器

    流程:
      1. 获取 service Pod + metrics-server 采集间隔
      2. 预热前延迟基准（冷启动）
      3. 执行 metrics-server 预热
      4. 预热后延迟基准（热启动）
      5. 对比分析
    """

    def __init__(
        self,
        warmup_config: WarmupConfig,
        probe_config: LatencyProbeConfig,
        hpa_name: str,
    ):
        self.warmup_config = warmup_config
        self.probe_config = probe_config
        self.hpa_name = hpa_name
        self.warmup_id = f"warmup-patrol-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    def run(self) -> WarmupBeforePatrolResult:
        """执行完整的预热 + 延迟基准流程"""
        print(f"\n{'='*60}")
        print(f"  巡检前预热 + 延迟基准 | ID={self.warmup_id}")
        print(f"  HPA: {self.hpa_name} (ns={self.warmup_config.namespace})")
        print(f"{'='*60}")

        # ── 获取 service Pod ──
        try:
            pod_name = get_service_pod(
                self.warmup_config.namespace, self.warmup_config.service_name
            )
            print(f"  [INFO] service Pod: {pod_name}")
        except RuntimeError as e:
            print(f"  [ERROR] {e}")
            return WarmupBeforePatrolResult(
                warmup_id=self.warmup_id,
                timestamp=datetime.now(timezone.utc).isoformat(),
                hpa_name=self.hpa_name,
                namespace=self.warmup_config.namespace,
                pod_name="",
                metrics_resolution_sec=get_metrics_resolution(),
                latency_before={},
                warmup_result={"success": False, "message": str(e)},
                latency_after={},
                latency_improvement_sec=None,
                latency_improvement_pct=None,
                success=False,
                message=f"获取 Pod 失败: {e}",
            )

        # ── 获取 metrics-server 采集间隔 ──
        resolution = get_metrics_resolution()
        print(f"  [INFO] metrics-server 采集间隔: {resolution}s")

        probe = MetricsLatencyProbe(
            self.warmup_config, self.probe_config, self.hpa_name, pod_name
        )

        # ── 阶段 1: 预热前延迟基准（冷启动）──
        print(f"\n── 阶段 1: 预热前延迟基准（冷启动）──")
        latency_before = probe.measure(label="预热前")

        # ── 阶段 2: 执行预热 ──
        print(f"\n── 阶段 2: 执行 metrics-server 预热 ──")
        warmup_result = run_warmup_traffic(
            self.warmup_config.namespace, pod_name, self.warmup_config, self.hpa_name
        )
        print(f"  [INFO] 预热结果: {'✓ 成功' if warmup_result['success'] else '✗ 失败'}")
        print(f"  [INFO] {warmup_result['message']}")

        # ── 阶段 2.5: 等待 CPU 回落稳定（避免预热残留影响延迟测量）──
        print(f"\n── 阶段 2.5: 等待 CPU 稳定 ──")
        stable_wait = 0
        max_stable_wait = 60
        while stable_wait < max_stable_wait:
            try:
                cpu_now = get_hpa_cpu(self.warmup_config.namespace, self.hpa_name)
            except RuntimeError:
                cpu_now = None
            if cpu_now is not None and cpu_now < 3:
                print(f"  [OK] CPU 已稳定: {cpu_now}% (等待 {stable_wait}s)")
                break
            print(f"  [INFO] 等待 CPU 回落: {cpu_now}% ({stable_wait}s)")
            time.sleep(5)
            stable_wait += 5
        else:
            print(f"  [WARN] CPU 未在 {max_stable_wait}s 内稳定，继续探测")

        # ── 阶段 3: 预热后延迟基准（热启动）──
        print(f"\n── 阶段 3: 预热后延迟基准（热启动）──")
        latency_after = probe.measure(label="预热后")

        # ── 阶段 4: 对比分析 ──
        print(f"\n── 阶段 4: 延迟对比 ──")
        improvement_sec = None
        improvement_pct = None
        before_estimated = False  # 预热前延迟是否为估算值（超时下界）

        # 【不易】超时时用探测持续时间作为延迟下界（实际延迟 ≥ probe_duration）
        # 这样即使一方超时，也能给出改善量估算
        if latency_before.latency_sec is not None:
            before_val = latency_before.latency_sec
        else:
            before_val = float(self.probe_config.probe_duration)
            before_estimated = True

        if latency_after.latency_sec is not None:
            after_val = latency_after.latency_sec
        else:
            after_val = float(self.probe_config.probe_duration)

        improvement_sec = before_val - after_val
        if before_val > 0:
            improvement_pct = (improvement_sec / before_val) * 100

        before_label = f">{before_val:.0f}s(超时估算)" if before_estimated else f"{before_val:.1f}s"
        print(f"  [INFO] 冷启动延迟: {before_label}")
        print(f"  [INFO] 热启动延迟: {after_val:.1f}s")
        print(f"  [INFO] 改善: -{improvement_sec:.1f}s ({improvement_pct:.1f}%)")

        success = warmup_result["success"] and (
            improvement_sec is not None and improvement_sec > 0
        )
        parts = [f"预热{'成功' if warmup_result['success'] else '失败'}"]
        if improvement_sec is not None and improvement_pct is not None:
            parts.append(f"延迟 {before_label} → {after_val:.1f}s")
            if improvement_pct >= 50:
                parts.append(f"改善 {improvement_pct:.0f}%（有效）")
            elif improvement_pct > 0:
                parts.append(f"改善 {improvement_pct:.0f}%（部分有效）")
            else:
                parts.append(f"无改善（{improvement_pct:.0f}%）")
        else:
            parts.append("延迟无法测量")
        message = "，".join(parts)

        print(f"\n{'─'*60}")
        print(f"  [{'✓ PASS' if success else '✗ FAIL'}] {message}")
        print(f"{'─'*60}")

        return WarmupBeforePatrolResult(
            warmup_id=self.warmup_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            hpa_name=self.hpa_name,
            namespace=self.warmup_config.namespace,
            pod_name=pod_name,
            metrics_resolution_sec=resolution,
            latency_before=latency_before.to_dict(),
            warmup_result=warmup_result,
            latency_after=latency_after.to_dict(),
            latency_improvement_sec=improvement_sec,
            latency_improvement_pct=improvement_pct,
            success=success,
            message=message,
        )


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def parse_args() -> tuple[WarmupConfig, LatencyProbeConfig, Optional[str], str]:
    """解析参数

    【规范】output 单独提取，不传入业务配置
    """
    parser = argparse.ArgumentParser(
        description="巡检前预热 + 指标延迟基准测量",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    # ── HPA / Service 参数 ──
    parser.add_argument("--namespace", default="production")
    parser.add_argument("--hpa-name", default="skill-retrieval-hpa")
    parser.add_argument("--service-name", default="skill-retrieval-service")
    parser.add_argument("--service-port", type=int, default=8080)
    parser.add_argument("--probe-endpoint", default="/match")
    parser.add_argument("--image", default="skill-retrieval:local")
    parser.add_argument("--verbose", action="store_true")

    # ── 预热参数 ──
    parser.add_argument("--warmup-vu", type=int, default=10,
                        help="预热并发数（默认 10，不触发扩容）")
    parser.add_argument("--warmup-duration", type=int, default=20,
                        help="预热持续时间秒（默认 20，需 ≥ 采集间隔）")
    parser.add_argument("--settle-wait", type=int, default=20,
                        help="预热后等待指标稳定秒（默认 20）")

    # ── 延迟探测参数 ──
    parser.add_argument("--probe-vu", type=int, default=5,
                        help="延迟探测并发数（默认 5，少量不触发扩容）")
    parser.add_argument("--probe-duration", type=int, default=20,
                        help="延迟探测持续时间秒（默认 20）")
    parser.add_argument("--poll-interval", type=float, default=3.0,
                        help="CPU 轮询间隔秒（默认 3.0）")
    parser.add_argument("--cpu-change-threshold", type=float, default=2.0,
                        help="CPU 变化阈值%%，判定指标已响应（默认 2.0）")

    # ── 输出 ──
    parser.add_argument("--output", default=None,
                        help="结果输出 JSON 文件路径")
    # ── 兼容性校验 ──
    parser.add_argument(
        "--skip-compat-check", action="store_true",
        help="跳过 K8s 版本与 metrics-server API 兼容性校验（紧急场景使用）",
    )

    args = parser.parse_args()

    output_path = args.output
    hpa_name = args.hpa_name
    skip_compat_check = args.skip_compat_check

    warmup_fields = {
        "namespace", "service_name", "service_port", "probe_endpoint",
        "warmup_vu", "warmup_duration", "settle_wait", "image", "verbose",
    }
    warmup_kwargs = {k: getattr(args, k) for k in warmup_fields}
    warmup_config = WarmupConfig(**warmup_kwargs)

    probe_config = LatencyProbeConfig(
        probe_vu=args.probe_vu,
        probe_duration=args.probe_duration,
        poll_interval=args.poll_interval,
        cpu_change_threshold=args.cpu_change_threshold,
    )

    return warmup_config, probe_config, output_path, hpa_name, skip_compat_check


def main() -> int:
    warmup_config, probe_config, output_path, hpa_name, skip_compat_check = parse_args()

    # ── 前置: K8s 兼容性校验 ──
    # 【不易】hard error 默认中止（exit 2），避免在不兼容集群上无效预热
    if not skip_compat_check:
        compat = check_k8s_compatibility()
        print_compat_result(compat)
        if not compat.ok:
            print(f"\n  [ERROR] 兼容性校验未通过，中止预热。"
                  f"如需强制执行请加 --skip-compat-check")
            return 2
    else:
        print("  [WARN] 已跳过 K8s 兼容性校验（--skip-compat-check）")

    orchestrator = WarmupBeforePatrol(warmup_config, probe_config, hpa_name)
    result = orchestrator.run()

    result_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    print(f"\n{'─'*60}")
    print(f"  巡检前预热结果 (ID={result.warmup_id})")
    print(f"{'─'*60}")
    print(result_json)

    if output_path:
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"\n  [INFO] 结果已写入 {output_path}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
