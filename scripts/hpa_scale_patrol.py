#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HPA 扩容时效性自动化巡检脚本

【不易】SLO 不变量: HPA 3→maxReplicas 扩容时效 ≤ 60s
【变易】支持参数化配置（副本数、阈值、告警通道），适配不同环境
【简易】轻量级流量探测 + 时间线记录 + Webhook 告警，零外部依赖（仅 kubectl）

用法:
    # 本地执行（通过 kubeconfig）
    python scripts/hpa_scale_patrol.py \\
        --hpa-name skill-retrieval-hpa \\
        --namespace production \\
        --target-replicas 15 \\
        --max-scale-time 60

    # 集群内执行（CronJob，依赖 in-cluster config）
    python /app/scripts/hpa_scale_patrol.py \\
        --hpa-name skill-retrieval-hpa \\
        --namespace production \\
        --target-replicas 15 \\
        --max-scale-time 60 \\
        --webhook-url http://alertmanager.monitoring:9093/api/v2/alerts

部署:
    kubectl apply -f deploy/k8s/hpa-scale-patrol-cronjob.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# 【不易】兼容性校验逻辑复用共享模块 compat_check（与 warmup_before_patrol.py 同源），
#        避免双份维护导致判定逻辑漂移；compat_check 为纯标准库，不破坏本脚本"零外部依赖"特性
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from compat_check import (  # noqa: E402
    check_k8s_compatibility,
    print_compat_result,
)


# ════════════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════════════

@dataclass
class PatrolConfig:
    """巡检配置（不可变，初始化后只读）"""
    hpa_name: str
    namespace: str
    target_replicas: int = 15          # 目标副本数（maxReplicas）
    max_scale_time: int = 60           # 扩容时效 SLO（秒）
    probe_vu: int = 100                # 探测并发数
    probe_duration: int = 90           # 探测持续时间（秒，需 > max_scale_time 留余量）
    cooldown_time: int = 300           # 巡检后缩容等待时间（秒）
    webhook_url: Optional[str] = None  # Alertmanager / 钉钉 / Slack Webhook
    service_name: str = "skill-retrieval-service"
    service_port: int = 8080
    probe_endpoint: str = "/match"
    verbose: bool = False


@dataclass
class ScaleTimelineEvent:
    """扩容时间线事件"""
    timestamp: str
    elapsed_sec: float
    current_replicas: int
    ready_replicas: int
    cpu_utilization: Optional[float]
    event_note: str = ""


@dataclass
class PatrolResult:
    """巡检结果"""
    success: bool
    scale_time_sec: float               # 实际扩容耗时（达到目标副本数的时间）
    start_replicas: int
    peak_replicas: int
    target_replicas: int
    timeline: list[ScaleTimelineEvent] = field(default_factory=list)
    error_message: str = ""
    patrol_id: str = ""

    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "scale_time_sec": round(self.scale_time_sec, 2),
            "start_replicas": self.start_replicas,
            "peak_replicas": self.peak_replicas,
            "target_replicas": self.target_replicas,
            "slo_threshold_sec": 60,
            "patrol_id": self.patrol_id,
            "error_message": self.error_message,
            "timeline_count": len(self.timeline),
            "timeline": [
                {
                    "t": e.timestamp,
                    "elapsed_sec": round(e.elapsed_sec, 2),
                    "replicas": e.current_replicas,
                    "ready": e.ready_replicas,
                    "cpu": e.cpu_utilization,
                    "note": e.event_note,
                }
                for e in self.timeline
            ],
        }


# ════════════════════════════════════════════════════════════════════
#  Kubernetes 客户端（基于 kubectl，零依赖）
# ════════════════════════════════════════════════════════════════════

class K8sClient:
    """kubectl 封装客户端（简易：仅用 subprocess 调用 kubectl）"""

    def __init__(self, namespace: str, verbose: bool = False):
        self.namespace = namespace
        self.verbose = verbose

    def _run_kubectl(self, args: list[str], timeout: int = 30) -> dict:
        """执行 kubectl 命令并返回 JSON 结果"""
        cmd = ["kubectl"] + args + ["-n", self.namespace, "-o", "json"]
        if self.verbose:
            print(f"  [CMD] {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"kubectl 命令超时: {' '.join(cmd)}")
        except FileNotFoundError:
            raise RuntimeError("kubectl 未安装或不在 PATH 中")

        if result.returncode != 0:
            raise RuntimeError(
                f"kubectl 失败 (rc={result.returncode}): {result.stderr.strip()}"
            )

        if not result.stdout.strip():
            return {}
        return json.loads(result.stdout)

    def get_hpa(self, hpa_name: str) -> dict:
        """获取 HPA 当前状态"""
        return self._run_kubectl(["get", "hpa", hpa_name])

    def get_deployment(self, dep_name: str) -> dict:
        """获取 Deployment 状态"""
        return self._run_kubectl(["get", "deployment", dep_name])

    def get_hpa_replicas(self, hpa_name: str) -> tuple[int, int]:
        """返回 (当前副本数, 目标副本数)"""
        hpa = self.get_hpa(hpa_name)
        # 【防御】K8s API 可能返回字符串，强制 int 转换
        current = int(hpa.get("status", {}).get("currentReplicas", 0) or 0)
        desired = int(hpa.get("status", {}).get("desiredReplicas", 0) or 0)
        return current, desired

    def get_deployment_replicas(self, dep_name: str) -> tuple[int, int]:
        """返回 (当前副本数, 就绪副本数)"""
        dep = self.get_deployment(dep_name)
        status = dep.get("status", {})
        # 【防御】readyReplicas 可能缺失（Pod 启动中），默认 0
        return (
            int(status.get("replicas", 0) or 0),
            int(status.get("readyReplicas", 0) or 0),
        )

    def get_hpa_cpu_utilization(self, hpa_name: str) -> Optional[float]:
        """获取 HPA 当前 CPU 利用率（%）"""
        hpa = self.get_hpa(hpa_name)
        metrics = hpa.get("status", {}).get("currentMetrics", [])
        for m in metrics:
            if m.get("resource", {}).get("name") == "cpu":
                val = m.get("resource", {}).get("current", {}).get("averageUtilization")
                return float(val) if val is not None else None
        return None

    def exec_in_pod(
        self,
        pod_name: str,
        command: list[str],
        timeout: int = 120,
    ) -> str:
        """在指定 Pod 中执行命令"""
        cmd = ["kubectl", "exec", "-n", self.namespace, pod_name, "--"] + command
        if self.verbose:
            print(f"  [CMD] {' '.join(cmd)}", file=sys.stderr)

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"kubectl exec 超时: {command}")

        if result.returncode != 0:
            raise RuntimeError(f"kubectl exec 失败: {result.stderr.strip()}")
        return result.stdout

    def find_loadtest_pod(self, label: str = "app=in-cluster-loadtest") -> Optional[str]:
        """查找集群内压测 Pod"""
        cmd = ["kubectl", "get", "pod", "-n", self.namespace, "-l", label,
               "-o", "jsonpath={.items[0].metadata.name}"]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except subprocess.TimeoutExpired:
            pass
        return None


# ════════════════════════════════════════════════════════════════════
#  流量探测（轻量级，触发 HPA 扩容）
# ════════════════════════════════════════════════════════════════════

class TrafficProbe:
    """流量探测器：在集群内 Pod 中生成突发流量，触发 HPA 扩容"""

    def __init__(self, k8s: K8sClient, config: PatrolConfig):
        self.k8s = k8s
        self.config = config
        self.pod_name: Optional[str] = None

    def _build_inline_script(self) -> str:
        """生成内联 Python 探测脚本（在压测 Pod 内执行）

        【简易】使用 urllib 标准库（零依赖），避免镜像无 requests 库的问题
        """
        endpoint = (
            f"http://{self.config.service_name}.{self.config.namespace}"
            f".svc.cluster.local:{self.config.service_port}{self.config.probe_endpoint}"
        )
        # 【简易】内联脚本：紧循环无 sleep，最大化 QPS 触发 CPU 阈值
        # 用 urllib 替代 requests（标准库，镜像内一定可用）
        return f"""
import concurrent.futures, time, json, urllib.request
endpoint = "{endpoint}"
vu = {self.config.probe_vu}
duration = {self.config.probe_duration}
body = json.dumps({{"query": "patrol-probe"}}).encode("utf-8")

def worker(_):
    end = time.time() + duration
    while time.time() < end:
        try:
            req = urllib.request.Request(endpoint, data=body, method="POST",
                                         headers={{"Content-Type": "application/json"}})
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

print(f"probe-started endpoint={{endpoint}} vu={{vu}} duration={{duration}}", flush=True)
with concurrent.futures.ThreadPoolExecutor(max_workers=vu) as pool:
    list(pool.map(worker, range(vu)))
print("probe-finished", flush=True)
"""

    def start(self) -> str:
        """启动流量探测，返回压测 Pod 名"""
        # 查找压测 Pod
        self.pod_name = self.k8s.find_loadtest_pod()
        if not self.pod_name:
            raise RuntimeError(
                "未找到压测 Pod（label=app=in-cluster-loadtest），"
                "请先部署: kubectl apply -f deploy/k8s/loadtest-pod.yaml"
            )

        script = self._build_inline_script()
        # 【变易】用 subprocess.Popen 异步启动，不阻塞巡检主循环
        # （kubectl exec 会持续运行直到内联脚本结束或被 terminate）
        cmd = ["kubectl", "exec", "-n", self.k8s.namespace, self.pod_name, "--",
               "python", "-c", script]
        # 启动子进程，不等待完成
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        # 等待探测启动信号
        time.sleep(2)
        if self._proc.poll() is not None:
            out, err = self._proc.communicate(timeout=5)
            raise RuntimeError(f"探测脚本启动失败: {err}")
        return self.pod_name

    def wait_finished(self, timeout: int = 300) -> None:
        """等待探测完成"""
        try:
            self._proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            raise RuntimeError("探测脚本执行超时，已终止")

    def stop(self) -> None:
        """停止流量探测（封装 _proc 访问，避免外部直接操作私有属性）"""
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════
#  巡检主流程
# ════════════════════════════════════════════════════════════════════

class HPAScalePatrol:
    """HPA 扩容时效巡检"""

    def __init__(self, config: PatrolConfig):
        self.config = config
        self.k8s = K8sClient(config.namespace, config.verbose)
        self.patrol_id = f"patrol-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    def run(self) -> PatrolResult:
        """执行一次完整巡检"""
        print(f"\n{'='*60}")
        print(f"  HPA 扩容时效巡检 | ID={self.patrol_id}")
        print(f"  HPA: {self.config.hpa_name} (ns={self.config.namespace})")
        print(f"  目标: {self.config.target_replicas} 副本 ≤ {self.config.max_scale_time}s")
        print(f"{'='*60}\n")

        # ── 阶段 1: 前置检查 ──
        try:
            start_replicas, _ = self.k8s.get_hpa_replicas(self.config.hpa_name)
            current, ready = self.k8s.get_deployment_replicas(self.config.service_name)
        except RuntimeError as e:
            return PatrolResult(
                success=False,
                scale_time_sec=0,
                start_replicas=0,
                peak_replicas=0,
                target_replicas=self.config.target_replicas,
                error_message=f"前置检查失败: {e}",
                patrol_id=self.patrol_id,
            )

        if start_replicas >= self.config.target_replicas:
            # 当前副本数已达目标，无法验证扩容时效
            msg = (f"当前副本数 {start_replicas} ≥ 目标 {self.config.target_replicas}，"
                   f"需先缩容到 minReplicas 再巡检")
            print(f"  [SKIP] {msg}")
            return PatrolResult(
                success=False,
                scale_time_sec=0,
                start_replicas=start_replicas,
                peak_replicas=start_replicas,
                target_replicas=self.config.target_replicas,
                error_message=msg,
                patrol_id=self.patrol_id,
            )

        print(f"  [INFO] 起始副本数: {start_replicas}")
        print(f"  [INFO] 就绪副本数: {ready}")

        # ── 阶段 2: 启动流量探测 + 监控扩容时间线 ──
        timeline: list[ScaleTimelineEvent] = []
        t_start = time.time()

        # 记录起始事件（传入实际起始副本数，便于时间线追溯）
        timeline.append(self._snapshot(
            t_start, t_start,
            current=start_replicas, ready=ready,
            note="巡检开始"
        ))

        # 异步启动流量探测
        probe = TrafficProbe(self.k8s, self.config)
        try:
            pod = probe.start()
            print(f"  [INFO] 流量探测已启动 (pod={pod}, vu={self.config.probe_vu})")
        except RuntimeError as e:
            return PatrolResult(
                success=False,
                scale_time_sec=0,
                start_replicas=start_replicas,
                peak_replicas=start_replicas,
                target_replicas=self.config.target_replicas,
                error_message=f"流量探测启动失败: {e}",
                patrol_id=self.patrol_id,
            )

        # ── 阶段 3: 监控扩容进度（轮询 HPA 状态）──
        scale_done_time: Optional[float] = None
        peak_replicas = start_replicas
        deadline = t_start + self.config.probe_duration

        while time.time() < deadline:
            now = time.time()
            elapsed = now - t_start

            try:
                current, desired = self.k8s.get_hpa_replicas(self.config.hpa_name)
                cpu = self.k8s.get_hpa_cpu_utilization(self.config.hpa_name)
                _, ready = self.k8s.get_deployment_replicas(self.config.service_name)
            except RuntimeError as e:
                timeline.append(self._snapshot(now, t_start, note=f"指标获取失败: {e}"))
                time.sleep(5)
                continue

            peak_replicas = max(peak_replicas, current)

            # 记录关键事件
            note = ""
            if current > (timeline[-1].current_replicas if timeline else start_replicas):
                note = f"副本数增长 {timeline[-1].current_replicas if timeline else start_replicas}→{current}"
            elif current == self.config.target_replicas and scale_done_time is None:
                note = "✓ 达到目标副本数"
                scale_done_time = now
            elif current < (timeline[-1].current_replicas if timeline else start_replicas):
                note = f"副本数下降（缩容）{timeline[-1].current_replicas}→{current}"

            timeline.append(self._snapshot(
                now, t_start,
                current=current, ready=ready, cpu=cpu, note=note,
            ))

            # 每 10s 打印进度
            if int(elapsed) % 10 == 0:
                print(f"  [{elapsed:5.1f}s] replicas={current}/{self.config.target_replicas} "
                      f"ready={ready} cpu={cpu}% {note}")

            # 达到目标副本数后，继续观察 10s 确认稳定，然后退出
            if scale_done_time is not None and (now - scale_done_time) > 10:
                print(f"  [OK] 扩容完成并稳定，退出监控")
                break

            time.sleep(5)

        # 停止探测（通过封装方法，不直接访问私有属性）
        probe.stop()

        # ── 阶段 4: 计算结果 ──
        if scale_done_time is None:
            success = False
            scale_time = self.config.probe_duration
            error = (f"探测期间未达到目标副本数 {self.config.target_replicas}，"
                     f"峰值仅 {peak_replicas}")
            print(f"\n  [FAIL] {error}")
        else:
            scale_time = scale_done_time - t_start
            success = scale_time <= self.config.max_scale_time
            error = "" if success else (
                f"扩容耗时 {scale_time:.1f}s 超过 SLO 阈值 {self.config.max_scale_time}s"
            )
            status = "PASS" if success else "FAIL"
            print(f"\n  [{status}] 扩容耗时: {scale_time:.1f}s "
                  f"(SLO: ≤{self.config.max_scale_time}s)")

        # ── 阶段 5: 等待缩容回稳态（不影响下次巡检）──
        print(f"\n  [INFO] 等待 {self.config.cooldown_time}s 缩容回稳态...")
        cooldown_deadline = time.time() + self.config.cooldown_time
        while time.time() < cooldown_deadline:
            try:
                current, _ = self.k8s.get_hpa_replicas(self.config.hpa_name)
                if current <= 3:  # 回到 minReplicas
                    print(f"  [OK] 已缩容回 {current} 副本")
                    break
            except RuntimeError:
                pass
            time.sleep(15)

        result = PatrolResult(
            success=success,
            scale_time_sec=scale_time,
            start_replicas=start_replicas,
            peak_replicas=peak_replicas,
            target_replicas=self.config.target_replicas,
            timeline=timeline,
            error_message=error,
            patrol_id=self.patrol_id,
        )

        # ── 阶段 6: 发送告警（失败时）──
        if not success:
            self._send_alert(result)

        return result

    def _snapshot(
        self,
        now: float,
        t_start: float,
        *,
        current: int = 0,
        ready: int = 0,
        cpu: Optional[float] = None,
        note: str = "",
    ) -> ScaleTimelineEvent:
        return ScaleTimelineEvent(
            timestamp=datetime.fromtimestamp(now, timezone.utc).isoformat(),
            elapsed_sec=now - t_start,
            current_replicas=current,
            ready_replicas=ready,
            cpu_utilization=cpu,
            event_note=note,
        )

    # ────────────────────────────────────────────────────────────────
    #  告警通知（Alertmanager 兼容格式）
    # ────────────────────────────────────────────────────────────────
    def _send_alert(self, result: PatrolResult) -> None:
        """发送告警到 Alertmanager / Webhook"""
        if not self.config.webhook_url:
            print(f"  [WARN] 未配置 webhook_url，跳过告警发送")
            return

        # 【变易】Alertmanager v2 API 兼容格式
        alert_payload = [{
            "labels": {
                "alertname": "HPAScalePatrolFailure",
                "service": "skill-retrieval",
                "severity": "critical",
                "patrol_id": result.patrol_id,
                "namespace": self.config.namespace,
                "hpa": self.config.hpa_name,
            },
            "annotations": {
                "summary": f"HPA 扩容时效巡检失败: {result.scale_time_sec:.1f}s > "
                           f"{self.config.max_scale_time}s",
                "description": (
                    f"巡检 ID: {result.patrol_id}\n"
                    f"扩容耗时: {result.scale_time_sec:.2f}s "
                    f"(SLO: ≤{self.config.max_scale_time}s)\n"
                    f"起始副本: {result.start_replicas}\n"
                    f"峰值副本: {result.peak_replicas}\n"
                    f"目标副本: {result.target_replicas}\n"
                    f"错误: {result.error_message}\n\n"
                    f"排查建议:\n"
                    f"  1. kubectl get hpa -n {self.config.namespace}\n"
                    f"  2. kubectl describe hpa {self.config.hpa_name} -n {self.config.namespace}\n"
                    f"  3. kubectl get pod -l k8s-app=metrics-server -n kube-system\n"
                    f"  4. 检查节点资源: kubectl top nodes"
                ),
                "runbook_url": "docs/MIGRATION_PORT_FORWARD_TO_IN_CLUSTER.md",
            },
            "startsAt": datetime.now(timezone.utc).isoformat(),
        }]

        try:
            data = json.dumps(alert_payload).encode("utf-8")
            req = urllib.request.Request(
                self.config.webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                if 200 <= resp.status < 300:
                    print(f"  [OK] 告警已发送到 {self.config.webhook_url}")
                else:
                    print(f"  [WARN] 告警发送返回 {resp.status}")
        except urllib.error.URLError as e:
            print(f"  [ERR] 告警发送失败: {e}")
        except Exception as e:
            print(f"  [ERR] 告警发送异常: {e}")


# ════════════════════════════════════════════════════════════════════
#  CLI 入口
# ════════════════════════════════════════════════════════════════════

def parse_args() -> tuple[PatrolConfig, Optional[str], bool]:
    """解析 CLI 参数，返回 (配置, 输出文件路径, 是否跳过兼容性校验)"""
    parser = argparse.ArgumentParser(
        description="HPA 扩容时效自动化巡检",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--hpa-name", required=True, help="HPA 名称")
    parser.add_argument("--namespace", default="production", help="命名空间")
    parser.add_argument("--target-replicas", type=int, default=15,
                        help="目标副本数（默认 15，对齐 maxReplicas）")
    parser.add_argument("--max-scale-time", type=int, default=60,
                        help="扩容时效 SLO 阈值（秒，默认 60）")
    parser.add_argument("--probe-vu", type=int, default=100,
                        help="探测并发数（默认 100）")
    parser.add_argument("--probe-duration", type=int, default=90,
                        help="探测持续时间（秒，默认 90）")
    parser.add_argument("--cooldown-time", type=int, default=300,
                        help="巡检后缩容等待时间（秒，默认 300）")
    parser.add_argument("--webhook-url", default=None,
                        help="告警 Webhook URL（Alertmanager / 钉钉 / Slack）")
    parser.add_argument("--service-name", default="skill-retrieval-service",
                        help="目标 Service 名称")
    parser.add_argument("--service-port", type=int, default=8080,
                        help="目标 Service 端口")
    parser.add_argument("--probe-endpoint", default="/match",
                        help="探测端点路径")
    parser.add_argument("--output", default=None,
                        help="结果输出 JSON 文件路径（不指定则仅打印）")
    parser.add_argument("--verbose", action="store_true",
                        help="详细日志模式")
    parser.add_argument(
        "--skip-compat-check", action="store_true",
        help="跳过 K8s 版本与 metrics-server API 兼容性校验（紧急场景使用）",
    )
    args = parser.parse_args()

    # 【简易】output 单独提取，不传入 PatrolConfig（不属于业务配置）
    output_path = args.output
    skip_compat_check = args.skip_compat_check
    config_fields = {
        "hpa_name", "namespace", "target_replicas", "max_scale_time",
        "probe_vu", "probe_duration", "cooldown_time", "webhook_url",
        "service_name", "service_port", "probe_endpoint", "verbose",
    }
    config_kwargs = {
        k: getattr(args, k) for k in config_fields
        if getattr(args, k) is not None or k in ["hpa_name", "namespace", "verbose"]
    }
    return PatrolConfig(**config_kwargs), output_path, skip_compat_check


def main() -> int:
    config, output_path, skip_compat_check = parse_args()

    # ── 前置: K8s 兼容性校验 ──
    # 【不易】hard error 默认中止（exit 2），避免在不兼容集群上无效巡检。
    #        与 warmup_before_patrol.py 复用同一 compat_check 模块，判定逻辑同源。
    if not skip_compat_check:
        compat = check_k8s_compatibility()
        print_compat_result(compat)
        if not compat.ok:
            print(f"\n  [ERROR] 兼容性校验未通过，中止巡检。"
                  f"如需强制执行请加 --skip-compat-check")
            return 2
    else:
        print("  [WARN] 已跳过 K8s 兼容性校验（--skip-compat-check）")

    patrol = HPAScalePatrol(config)
    result = patrol.run()

    # 输出结果 JSON
    result_json = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
    print(f"\n{'─'*60}")
    print(f"  巡检结果 (ID={result.patrol_id})")
    print(f"{'─'*60}")
    print(result_json)

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(result_json)
        print(f"\n  [INFO] 结果已写入 {output_path}")

    return 0 if result.success else 2


if __name__ == "__main__":
    sys.exit(main())
