#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HPA 扩容时效验证脚本 — 3→15 副本基准测试（双 SLO 口径 v2）

【不易】SLO 双口径: 决策时效 ≤30s（CPU 超阈值→首次扩容）+ 端到端 ≤90s（流量开始→达到目标）
【变易】自包含: 自动创建/清理临时压测 Pod，适配任意起始副本数；metrics-server 预热消除冷启动
【简易】零外部依赖（仅 kubectl + Python 标准库）

用法:
    # 标准验证（等待 HPA 缩容到 3 后触发扩容，含预热）
    python scripts/hpa_scale_3to15_benchmark.py

    # 跳过缩容等待（当前副本数 ≥3 时直接开始）
    python scripts/hpa_scale_3to15_benchmark.py --skip-wait-scale-down

    # 自定义双 SLO 阈值 + 预热参数
    python scripts/hpa_scale_3to15_benchmark.py \\
        --namespace production \\
        --hpa-name skill-retrieval-hpa \\
        --deployment skill-retrieval-service \\
        --target-replicas 15 \\
        --decision-slo-seconds 30 \\
        --end-to-end-slo-seconds 90 \\
        --warmup-vu 10 --warmup-duration 20 \\
        --probe-vu 100 \\
        --probe-duration 90

    # 禁用预热（已预热或调试用）
    python scripts/hpa_scale_3to15_benchmark.py --no-warmup
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ════════════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════════════

@dataclass
class BenchmarkConfig:
    """扩容基准测试配置"""
    namespace: str = "production"
    hpa_name: str = "skill-retrieval-hpa"
    deployment: str = "skill-retrieval-service"
    service_name: str = "skill-retrieval-service"
    service_port: int = 8080
    probe_endpoint: str = "/match"
    target_replicas: int = 15
    # 【SLO 重定义】双口径: 决策时效 + 端到端时效
    decision_slo_seconds: int = 30     # HPA 决策时效 SLO（CPU 超阈值→首次扩容）
    end_to_end_slo_seconds: int = 90   # 端到端时效 SLO（流量开始→达到目标，含指标延迟）
    probe_vu: int = 100
    probe_duration: int = 90
    scale_down_timeout: int = 900      # 等待缩容到 3 的最大时间（秒）
    scale_down_poll_interval: int = 15 # 缩容轮询间隔（秒）
    image: str = "skill-retrieval:local"
    skip_wait_scale_down: bool = False
    cleanup_pod: bool = True           # 测试完成后清理临时 Pod
    # 【预热】消除 metrics-server 冷启动延迟
    warmup_enabled: bool = True        # 是否启用预热
    warmup_vu: int = 10               # 预热并发数（少量，不触发扩容）
    warmup_duration: int = 20          # 预热持续时间（秒）
    settle_wait: int = 20              # 预热后等待指标稳定（秒）


@dataclass
class TimelineEntry:
    """扩容时间线条目"""
    timestamp: str
    elapsed_sec: float
    replicas: int
    ready_replicas: int
    cpu_util: Optional[float]
    note: str


@dataclass
class BenchmarkResult:
    """基准测试结果"""
    success: bool
    scale_time_sec: float               # 端到端耗时（流量开始→达到目标）
    decision_time_sec: float            # HPA 决策时效（CPU 超阈值→首次扩容）
    start_replicas: int
    peak_replicas: int
    target_replicas: int
    decision_slo_seconds: int           # 决策时效 SLO 阈值
    end_to_end_slo_seconds: int         # 端到端 SLO 阈值
    timeline: list[TimelineEntry] = field(default_factory=list)
    error: str = ""
    benchmark_id: str = ""
    warmup_result: Optional[dict] = None  # 预热结果

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "scale_time_sec": round(self.scale_time_sec, 2),
            "decision_time_sec": round(self.decision_time_sec, 2),
            "start_replicas": self.start_replicas,
            "peak_replicas": self.peak_replicas,
            "target_replicas": self.target_replicas,
            "decision_slo_seconds": self.decision_slo_seconds,
            "end_to_end_slo_seconds": self.end_to_end_slo_seconds,
            "benchmark_id": self.benchmark_id,
            "error": self.error,
            "warmup_result": self.warmup_result,
            "timeline": [
                {
                    "t": e.timestamp,
                    "elapsed": round(e.elapsed_sec, 2),
                    "replicas": e.replicas,
                    "ready": e.ready_replicas,
                    "cpu": e.cpu_util,
                    "note": e.note,
                }
                for e in self.timeline
            ],
        }


# ════════════════════════════════════════════════════════════════════
#  K8s 操作封装
# ════════════════════════════════════════════════════════════════════

def kubectl(args: list[str], timeout: int = 30, check: bool = True) -> str:
    """执行 kubectl 命令，返回 stdout"""
    cmd = ["kubectl"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"kubectl 超时: {' '.join(cmd)}")
    except FileNotFoundError:
        raise RuntimeError("kubectl 未安装或不在 PATH 中")
    if check and result.returncode != 0:
        raise RuntimeError(f"kubectl 失败: {result.stderr.strip()}")
    return result.stdout


def kubectl_json(args: list[str], timeout: int = 30) -> dict:
    """执行 kubectl -o json 并解析"""
    return json.loads(kubectl(args + ["-o", "json"], timeout=timeout) or "{}")


def get_hpa_replicas(namespace: str, hpa_name: str) -> tuple[int, int]:
    """返回 (当前副本数, 目标副本数)"""
    hpa = kubectl_json(["get", "hpa", "-n", namespace, hpa_name])
    current = int(hpa.get("status", {}).get("currentReplicas", 0) or 0)
    desired = int(hpa.get("status", {}).get("desiredReplicas", 0) or 0)
    return current, desired


def get_hpa_cpu(namespace: str, hpa_name: str) -> Optional[float]:
    """获取 HPA 当前 CPU 利用率（%）"""
    hpa = kubectl_json(["get", "hpa", "-n", namespace, hpa_name])
    for m in hpa.get("status", {}).get("currentMetrics", []):
        if m.get("resource", {}).get("name") == "cpu":
            val = m.get("resource", {}).get("current", {}).get("averageUtilization")
            return float(val) if val is not None else None
    return None


def get_deployment_replicas(namespace: str, dep_name: str) -> tuple[int, int]:
    """返回 (当前副本数, 就绪副本数)"""
    dep = kubectl_json(["get", "deployment", "-n", namespace, dep_name])
    status = dep.get("status", {})
    return (
        int(status.get("replicas", 0) or 0),
        int(status.get("readyReplicas", 0) or 0),
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════════════
#  临时压测 Pod 管理
# ════════════════════════════════════════════════════════════════════

PROBE_POD_NAME = "hpa-benchmark-probe"
PROBE_POD_LABEL = "app=hpa-benchmark-probe"


def ensure_probe_pod(config: BenchmarkConfig) -> str:
    """确保临时压测 Pod 存在并就绪，返回 Pod 名"""
    # 检查是否已存在
    existing = kubectl(
        ["get", "pod", "-n", config.namespace, "-l", PROBE_POD_LABEL,
         "-o", "jsonpath={.items[0].metadata.name}"],
        timeout=10, check=False,
    ).strip()

    if existing:
        print(f"  [INFO] 复用已存在的压测 Pod: {existing}")
        return existing

    # 创建临时 Pod（用 kubectl run + overrides，避免 stdin 问题）
    print(f"  [INFO] 创建临时压测 Pod: {PROBE_POD_NAME}")
    create_cmd = [
        "kubectl", "run", PROBE_POD_NAME,
        "-n", config.namespace,
        f"--image={config.image}",
        "--restart=Never",
        "--labels=app=hpa-benchmark-probe",
        "--overrides=" + json.dumps({
            "spec": {
                "containers": [{
                    "name": "probe",
                    "image": config.image,
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["sleep", "3600"],
                    "resources": {
                        "requests": {"cpu": "50m", "memory": "64Mi"},
                        "limits": {"cpu": "200m", "memory": "256Mi"},
                    },
                }]
            }
        }),
    ]
    result = subprocess.run(create_cmd, capture_output=True, text=True, timeout=30, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"kubectl run 失败: {result.stderr.strip()}")

    # 等待 Pod 就绪
    print(f"  [INFO] 等待压测 Pod 就绪...")
    kubectl(["wait", "--for=condition=Ready",
             f"pod/{PROBE_POD_NAME}", "-n", config.namespace, "--timeout=60s"],
            timeout=70, check=True)
    print(f"  [OK] 压测 Pod 就绪: {PROBE_POD_NAME}")
    return PROBE_POD_NAME


def cleanup_probe_pod(config: BenchmarkConfig) -> None:
    """清理临时压测 Pod"""
    if not config.cleanup_pod:
        print(f"  [SKIP] cleanup_pod=False，保留压测 Pod")
        return
    print(f"  [INFO] 清理临时压测 Pod: {PROBE_POD_NAME}")
    kubectl(["delete", "pod", "-n", config.namespace, PROBE_POD_NAME,
             "--ignore-not-found", "--force", "--grace-period=0"],
            timeout=30, check=False)


# ════════════════════════════════════════════════════════════════════
#  流量探测（urllib 标准库，零依赖）
# ════════════════════════════════════════════════════════════════════

def build_probe_script(config: BenchmarkConfig) -> str:
    """生成内联流量探测脚本（在压测 Pod 内执行）

    【规范】使用 urllib 标准库，不依赖 requests（镜像内可能未安装）
    """
    endpoint = (
        f"http://{config.service_name}.{config.namespace}"
        f".svc.cluster.local:{config.service_port}{config.probe_endpoint}"
    )
    return f"""
import concurrent.futures, time, json, urllib.request
endpoint = "{endpoint}"
vu = {config.probe_vu}
duration = {config.probe_duration}
body = json.dumps({{"query": "scale-benchmark"}}).encode("utf-8")

def worker(_):
    end = time.time() + duration
    while time.time() < end:
        try:
            req = urllib.request.Request(endpoint, data=body, method="POST",
                                         headers={{"Content-Type": "application/json"}})
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

print(f"probe-started vu={{vu}} duration={{duration}}s", flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=vu) as pool:
    list(pool.map(worker, range(vu)))
print("probe-finished", flush=True)
"""


def start_traffic_probe(config: BenchmarkConfig, pod_name: str) -> subprocess.Popen:
    """在压测 Pod 内启动流量探测（非阻塞）"""
    script = build_probe_script(config)
    cmd = ["kubectl", "exec", "-n", config.namespace, pod_name, "--",
           "python", "-c", script]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    # 等待启动信号
    time.sleep(3)
    if proc.poll() is not None:
        out, err = proc.communicate(timeout=5)
        raise RuntimeError(f"流量探测启动失败: {err}")
    return proc


# ════════════════════════════════════════════════════════════════════
#  扩容监控
# ════════════════════════════════════════════════════════════════════

def wait_for_scale_down(config: BenchmarkConfig) -> int:
    """等待 HPA 缩容到 minReplicas（3），返回当前副本数"""
    print(f"\n  [INFO] 等待 HPA 缩容到 3 副本（超时 {config.scale_down_timeout}s）...")

    waited = 0
    while waited < config.scale_down_timeout:
        current, _ = get_hpa_replicas(config.namespace, config.hpa_name)
        if current <= 3:
            print(f"  [OK] 已缩容到 {current} 副本（等待 {waited}s）")
            return current
        if waited % 60 == 0:
            print(f"    [{waited}s] 当前副本数: {current}，等待缩容...")
        time.sleep(config.scale_down_poll_interval)
        waited += config.scale_down_poll_interval

    current, _ = get_hpa_replicas(config.namespace, config.hpa_name)
    raise RuntimeError(
        f"等待缩容超时（{config.scale_down_timeout}s），当前仍为 {current} 副本。"
        f"建议: 1) 延长 --scale-down-timeout; 2) 使用 --skip-wait-scale-down 直接开始"
    )


def monitor_scale_up(config: BenchmarkConfig, t_start: float) -> tuple[float, float, int, list[TimelineEntry]]:
    """监控扩容进度

    返回 (端到端耗时, 决策时效, 峰值副本数, 时间线)
    - 端到端耗时: 流量开始→达到目标副本数
    - 决策时效: CPU 首次超阈值→首次扩容
    """
    timeline: list[TimelineEntry] = []
    peak = 0
    target_reached_at: Optional[float] = None
    cpu_threshold_at: Optional[float] = None   # CPU 首次超 HPA 阈值（5%）
    first_scale_at: Optional[float] = None     # 首次副本数增加
    deadline = t_start + config.probe_duration + 30

    last_replicas = -1
    CPU_THRESHOLD = 5  # HPA CPU 阈值 5%

    while time.time() < deadline:
        now = time.time()
        elapsed = now - t_start

        try:
            current, _ = get_hpa_replicas(config.namespace, config.hpa_name)
            cpu = get_hpa_cpu(config.namespace, config.hpa_name)
            _, ready = get_deployment_replicas(config.namespace, config.deployment)
        except RuntimeError as e:
            timeline.append(TimelineEntry(
                now_iso(), elapsed, -1, -1, None, f"指标获取失败: {e}"
            ))
            time.sleep(5)
            continue

        peak = max(peak, current)

        # 【决策时效】记录 CPU 首次超阈值时间
        if cpu is not None and cpu > CPU_THRESHOLD and cpu_threshold_at is None:
            cpu_threshold_at = now
            print(f"    [{elapsed:5.1f}s] ★ CPU 首次超阈值: {cpu}% > {CPU_THRESHOLD}%")

        # 【决策时效】记录首次扩容时间
        if current > last_replicas and last_replicas >= 0 and first_scale_at is None:
            first_scale_at = now
            if cpu_threshold_at is not None:
                decision_sec = first_scale_at - cpu_threshold_at
                print(f"    [{elapsed:5.1f}s] ★ HPA 首次扩容: {last_replicas}→{current} "
                      f"(决策时效: {decision_sec:.1f}s)")

        # 记录副本数变化
        note = ""
        if current != last_replicas:
            if last_replicas >= 0:
                note = f"副本数变化: {last_replicas}→{current}"
            last_replicas = current

        # 检查是否达到目标
        if current >= config.target_replicas and target_reached_at is None:
            target_reached_at = now
            note = f"✓ 达到目标副本数 {config.target_replicas}"

        timeline.append(TimelineEntry(
            now_iso(), elapsed, current, ready, cpu, note
        ))

        if int(elapsed) % 10 == 0 or note:
            print(f"    [{elapsed:5.1f}s] replicas={current}/{config.target_replicas} "
                  f"ready={ready} cpu={cpu}% {note}")

        if target_reached_at is not None and (now - target_reached_at) > 10:
            print(f"  [OK] 扩容完成并稳定")
            break

        time.sleep(5)

    # 计算端到端耗时
    if target_reached_at is None:
        end_to_end_time = config.probe_duration + 30
    else:
        end_to_end_time = target_reached_at - t_start

    # 计算决策时效（CPU 超阈值→首次扩容）
    if cpu_threshold_at is not None and first_scale_at is not None:
        decision_time = first_scale_at - cpu_threshold_at
    else:
        decision_time = -1  # 无法计算（CPU 未超阈值或未扩容）

    return end_to_end_time, decision_time, peak, timeline


# ════════════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════════════

def run_benchmark(config: BenchmarkConfig) -> BenchmarkResult:
    """执行完整的 3→15 扩容基准测试"""
    benchmark_id = f"bench-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    print(f"\n{'='*60}")
    print(f"  HPA 扩容时效基准测试 | ID={benchmark_id}")
    print(f"  HPA: {config.hpa_name} (ns={config.namespace})")
    print(f"  目标: {config.target_replicas} 副本")
    print(f"  SLO: 决策时效 ≤{config.decision_slo_seconds}s + 端到端 ≤{config.end_to_end_slo_seconds}s")
    print(f"  探测: {config.probe_vu} VU × {config.probe_duration}s")
    print(f"{'='*60}")

    # ── 阶段 1: 确保压测 Pod ──
    print(f"\n── 阶段 1: 准备压测 Pod ──")
    try:
        pod_name = ensure_probe_pod(config)
    except RuntimeError as e:
        return BenchmarkResult(
            success=False, scale_time_sec=0, decision_time_sec=0,
            start_replicas=0,
            peak_replicas=0, target_replicas=config.target_replicas,
            decision_slo_seconds=config.decision_slo_seconds,
            end_to_end_slo_seconds=config.end_to_end_slo_seconds,
            error=f"压测 Pod 准备失败: {e}",
            benchmark_id=benchmark_id,
        )

    # ── 阶段 2: 等待缩容到 3（或跳过）──
    print(f"\n── 阶段 2: 确保起始副本数为 3 ──")
    try:
        if config.skip_wait_scale_down:
            start_replicas, _ = get_hpa_replicas(config.namespace, config.hpa_name)
            print(f"  [SKIP] --skip-wait-scale-down，当前副本数: {start_replicas}")
        else:
            start_replicas = wait_for_scale_down(config)
    except RuntimeError as e:
        cleanup_probe_pod(config)
        return BenchmarkResult(
            success=False, scale_time_sec=0, decision_time_sec=0,
            start_replicas=0,
            peak_replicas=0, target_replicas=config.target_replicas,
            decision_slo_seconds=config.decision_slo_seconds,
            end_to_end_slo_seconds=config.end_to_end_slo_seconds,
            error=str(e),
            benchmark_id=benchmark_id,
        )

    if start_replicas >= config.target_replicas:
        msg = (f"起始副本数 {start_replicas} ≥ 目标 {config.target_replicas}，"
               f"无需扩容。请等待 HPA 缩容后重试。")
        cleanup_probe_pod(config)
        return BenchmarkResult(
            success=False, scale_time_sec=0, decision_time_sec=0,
            start_replicas=start_replicas,
            peak_replicas=start_replicas, target_replicas=config.target_replicas,
            decision_slo_seconds=config.decision_slo_seconds,
            end_to_end_slo_seconds=config.end_to_end_slo_seconds,
            error=msg, benchmark_id=benchmark_id,
        )

    # ── 阶段 2.5: metrics-server 预热（消除冷启动指标延迟）──
    warmup_result_dict = None
    if config.warmup_enabled:
        print(f"\n── 阶段 2.5: metrics-server 预热 ──")
        try:
            # 内联预热逻辑（避免循环依赖，不 import metrics_server_warmup）
            cpu_before = get_hpa_cpu(config.namespace, config.hpa_name)
            print(f"  [INFO] 预热前 CPU: {cpu_before}%")

            # 发送少量预热流量（不触发扩容）
            warmup_endpoint = (
                f"http://{config.service_name}.{config.namespace}"
                f".svc.cluster.local:{config.service_port}{config.probe_endpoint}"
            )
            warmup_body = json.dumps({"query": "warmup"}).encode("utf-8")

            def warmup_worker(_):
                end = time.time() + config.warmup_duration
                while time.time() < end:
                    try:
                        req = urllib.request.Request(
                            warmup_endpoint, data=warmup_body, method="POST",
                            headers={"Content-Type": "application/json"},
                        )
                        urllib.request.urlopen(req, timeout=2)
                    except Exception:
                        pass

            print(f"  [INFO] 发送预热流量: {config.warmup_vu} VU × {config.warmup_duration}s")
            with concurrent.futures.ThreadPoolExecutor(max_workers=config.warmup_vu) as pool:
                list(pool.map(warmup_worker, range(config.warmup_vu)))

            # 等待指标采集周期
            print(f"  [INFO] 等待 {config.settle_wait}s 指标稳定...")
            time.sleep(config.settle_wait)

            cpu_after = get_hpa_cpu(config.namespace, config.hpa_name)
            cpu_delta = (cpu_after - cpu_before) if (cpu_before is not None and cpu_after is not None) else None
            print(f"  [INFO] 预热后 CPU: {cpu_after}% (Δ={cpu_delta}%)")

            warmup_result_dict = {
                "success": cpu_delta is not None and cpu_delta > 0,
                "cpu_before": cpu_before,
                "cpu_after": cpu_after,
                "cpu_delta": cpu_delta,
            }
            if warmup_result_dict["success"]:
                print(f"  [OK] 预热成功，metrics-server 指标已激活")
            else:
                print(f"  [WARN] 预热可能未生效，继续测试")
        except Exception as e:
            print(f"  [WARN] 预热异常: {e}，继续测试")
            warmup_result_dict = {"success": False, "error": str(e)}

    # ── 阶段 3: 触发流量突增 + 监控扩容 ──
    print(f"\n── 阶段 3: 触发流量突增 + 监控扩容 ──")
    print(f"  [INFO] 起始副本数: {start_replicas}")
    t_start = time.time()

    try:
        probe_proc = start_traffic_probe(config, pod_name)
        print(f"  [INFO] 流量探测已启动 (vu={config.probe_vu})")
    except RuntimeError as e:
        cleanup_probe_pod(config)
        return BenchmarkResult(
            success=False, scale_time_sec=0, decision_time_sec=0,
            start_replicas=start_replicas,
            peak_replicas=start_replicas, target_replicas=config.target_replicas,
            decision_slo_seconds=config.decision_slo_seconds,
            end_to_end_slo_seconds=config.end_to_end_slo_seconds,
            error=f"流量探测启动失败: {e}",
            benchmark_id=benchmark_id, warmup_result=warmup_result_dict,
        )

    # 监控扩容（返回端到端耗时 + 决策时效）
    end_to_end_time, decision_time, peak, timeline = monitor_scale_up(config, t_start)

    # 停止探测
    try:
        probe_proc.terminate()
        probe_proc.wait(timeout=10)
    except Exception:
        probe_proc.kill()

    # ── 阶段 4: 双 SLO 判定 ──
    print(f"\n── 阶段 4: 双 SLO 判定 ──")

    # 决策时效判定（CPU 超阈值→首次扩容）
    if decision_time >= 0:
        decision_pass = decision_time <= config.decision_slo_seconds
        decision_status = "✓ PASS" if decision_pass else "✗ FAIL"
        print(f"  [{decision_status}] HPA 决策时效: {decision_time:.1f}s "
              f"(SLO: ≤{config.decision_slo_seconds}s)")
    else:
        decision_pass = False
        print(f"  [SKIP] 决策时效无法计算（CPU 未超阈值或未扩容）")

    # 端到端时效判定（流量开始→达到目标）
    e2e_pass = end_to_end_time <= config.end_to_end_slo_seconds
    e2e_status = "✓ PASS" if e2e_pass else "✗ FAIL"
    print(f"  [{e2e_status}] 端到端耗时: {end_to_end_time:.1f}s "
          f"(SLO: ≤{config.end_to_end_slo_seconds}s)")
    print(f"  起始: {start_replicas} → 峰值: {peak} → 目标: {config.target_replicas}")

    # 综合判定：两个 SLO 都通过才算成功
    success = decision_pass and e2e_pass
    errors = []
    if not decision_pass and decision_time >= 0:
        errors.append(f"决策时效 {decision_time:.1f}s > {config.decision_slo_seconds}s")
    if not e2e_pass:
        errors.append(f"端到端耗时 {end_to_end_time:.1f}s > {config.end_to_end_slo_seconds}s")
    error = "; ".join(errors) if errors else ""

    # ── 阶段 5: 清理 ──
    print(f"\n── 阶段 5: 清理 ──")
    cleanup_probe_pod(config)

    return BenchmarkResult(
        success=success,
        scale_time_sec=end_to_end_time,
        decision_time_sec=decision_time,
        start_replicas=start_replicas,
        peak_replicas=peak,
        target_replicas=config.target_replicas,
        decision_slo_seconds=config.decision_slo_seconds,
        end_to_end_slo_seconds=config.end_to_end_slo_seconds,
        timeline=timeline,
        error=error,
        benchmark_id=benchmark_id,
        warmup_result=warmup_result_dict,
    )


# ════════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════════

def parse_args() -> tuple[BenchmarkConfig, Optional[str]]:
    """解析参数，返回 (配置, 输出文件路径)"""
    parser = argparse.ArgumentParser(
        description="HPA 3→15 副本扩容时效基准测试",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--namespace", default="production")
    parser.add_argument("--hpa-name", default="skill-retrieval-hpa")
    parser.add_argument("--deployment", default="skill-retrieval-service")
    parser.add_argument("--service-name", default="skill-retrieval-service")
    parser.add_argument("--service-port", type=int, default=8080)
    parser.add_argument("--probe-endpoint", default="/match")
    parser.add_argument("--target-replicas", type=int, default=15)
    # 【SLO 重定义 v2】双口径: 决策时效 + 端到端耗时（替代旧 --slo-seconds 单一口径）
    parser.add_argument("--decision-slo-seconds", type=int, default=30,
                        help="HPA 决策时效 SLO: CPU 超阈值→首次扩容（默认 30s）")
    parser.add_argument("--end-to-end-slo-seconds", type=int, default=90,
                        help="端到端时效 SLO: 流量开始→达到目标副本数（默认 90s）")
    # 【预热】消除 metrics-server 冷启动指标延迟
    parser.add_argument("--warmup", dest="warmup_enabled", action="store_true",
                        default=True, help="启用 metrics-server 预热（默认启用）")
    parser.add_argument("--no-warmup", dest="warmup_enabled", action="store_false",
                        help="禁用 metrics-server 预热")
    parser.add_argument("--warmup-vu", type=int, default=10,
                        help="预热并发数（少量，不触发扩容，默认 10）")
    parser.add_argument("--warmup-duration", type=int, default=20,
                        help="预热持续时间（秒，默认 20）")
    parser.add_argument("--settle-wait", type=int, default=20,
                        help="预热后等待指标稳定（秒，默认 20）")
    parser.add_argument("--probe-vu", type=int, default=100)
    parser.add_argument("--probe-duration", type=int, default=90)
    parser.add_argument("--scale-down-timeout", type=int, default=900)
    parser.add_argument("--scale-down-poll-interval", type=int, default=15)
    parser.add_argument("--image", default="skill-retrieval:local")
    parser.add_argument("--skip-wait-scale-down", action="store_true",
                        help="跳过缩容等待，直接从当前副本数开始")
    parser.add_argument("--keep-probe-pod", action="store_true",
                        help="测试完成后保留压测 Pod（默认清理）")
    parser.add_argument("--output", default=None,
                        help="结果输出 JSON 文件路径")
    args = parser.parse_args()

    # 【规范】output 不传入 BenchmarkConfig，单独提取
    output_path = args.output
    # 【规范】cleanup_pod 不是 CLI 参数（由 keep_probe_pod 转换），不在 config_fields 中
    # 【规范】仅包含 BenchmarkConfig dataclass 实际存在的字段
    # slo_seconds 已弃用（v2 改为 decision_slo_seconds + end_to_end_slo_seconds）
    # cleanup_pod 是转换型参数（由 keep_probe_pod 取反），不在此集合
    config_fields = {
        "namespace", "hpa_name", "deployment", "service_name", "service_port",
        "probe_endpoint", "target_replicas",
        "decision_slo_seconds", "end_to_end_slo_seconds",
        "probe_vu", "probe_duration", "scale_down_timeout",
        "scale_down_poll_interval", "image", "skip_wait_scale_down",
        "warmup_enabled", "warmup_vu", "warmup_duration", "settle_wait",
    }
    config_kwargs = {k: getattr(args, k) for k in config_fields}
    # keep_probe_pod → cleanup_pod 取反（转换型参数，单独处理）
    config_kwargs["cleanup_pod"] = not args.keep_probe_pod
    return BenchmarkConfig(**config_kwargs), output_path


def main() -> int:
    config, output_path = parse_args()
    result = run_benchmark(config)

    # 输出结果
    result_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    print(f"\n{'─'*60}")
    print(f"  基准测试结果 (ID={result.benchmark_id})")
    print(f"{'─'*60}")
    print(result_json)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"\n  [INFO] 结果已写入 {output_path}")

    return 0 if result.success else 2


if __name__ == "__main__":
    sys.exit(main())
