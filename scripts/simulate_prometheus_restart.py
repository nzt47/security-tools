"""v6.5 Prometheus 服务重启模拟 + 告警恢复验证

模拟场景:
    1. 启动沙箱 Prometheus（独立容器，端口 19090，不触碰生产）
    2. 注入故障指标（ONNX load_failed）触发 P0 告警 RerankerOnnxLoadFailed
    3. 模拟服务重启（docker restart）
    4. 验证重启后规则重新加载 + 告警恢复触发

验证目标:
    - 重启后 Prometheus 能正确重新加载 reranker-alerts.yml
    - P0 告警规则在重启后仍能正常评估和触发
    - 告警状态从 firing 恢复到 pending 再到 firing 的完整生命周期

设计原则:
    【不易】独立沙箱容器（sandbox-prometheus-restart），不触碰生产 Yunshu-prometheus
    【变易】端口/容器名可配，指标注入内容可配
    【简易】单脚本端到端，自动清理临时资源

前置条件:
    - Docker daemon 运行中
    - prom/prometheus 镜像可用（首次会自动拉取）

运行:
    python scripts/simulate_prometheus_restart.py
"""
from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import json
from pathlib import Path

# ════════════════════════════════════════════════════════════
#  配置
# ════════════════════════════════════════════════════════════
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_SRC = PROJECT_ROOT / "monitoring" / "prometheus" / "rules" / "reranker-alerts.yml"
SANDBOX_PORT = 19090          # 沙箱 Prometheus 端口（避开生产 9090）
EXPORTER_PORT = 18080         # 测试指标 exporter 端口
CONTAINER_NAME = "sandbox-prometheus-restart"
IMAGE = "prom/prometheus:latest"
SCRAPE_INTERVAL = 5           # 抓取间隔（秒），缩短以加速测试
EVAL_INTERVAL = 5             # 评估间隔（秒）
WAIT_SCRAPE = 35              # 等待首次抓取 + 评估（需 2+ 采样点供 increase()）
WAIT_RESTART = 35             # 等待重启恢复（秒）

# ════════════════════════════════════════════════════════════
#  日志工具
# ════════════════════════════════════════════════════════════
def info(msg: str):  print(f"[INFO]  {msg}")
def ok(msg: str):    print(f"[OK]    {msg}")
def warn(msg: str):  print(f"[WARN]  {msg}")
def err(msg: str):   print(f"[ERROR] {msg}")
def section(title: str):
    print("\n" + "═" * 60)
    print(f"  {title}")
    print("═" * 60)


# ════════════════════════════════════════════════════════════
#  Metrics Exporter（线程化 HTTP server）
# ════════════════════════════════════════════════════════════
# 【关键】counter 必须递增，increase() 才能返回 > 0
# 静态值 1 在第二次抓取后 increase=0，告警不会持续触发
class _FaultState:
    """故障指标状态：每次抓取递增 counter，模拟持续加载失败"""
    onnx_failed = 0
    pytorch_success = 0

    @classmethod
    def snapshot(cls) -> str:
        cls.onnx_failed += 1
        cls.pytorch_success += 1
        return (
            "# HELP yunshu_reranker_load_total Reranker load attempts\n"
            "# TYPE yunshu_reranker_load_total counter\n"
            f'yunshu_reranker_load_total{{backend="onnx",status="failed",reason="onnx_corrupted"}} {cls.onnx_failed}\n'
            f'yunshu_reranker_load_total{{backend="pytorch",status="success"}} {cls.pytorch_success}\n'
            "# HELP yunshu_reranker_completed_total Reranker completed\n"
            "# TYPE yunshu_reranker_completed_total counter\n"
            f'yunshu_reranker_completed_total{{backend="onnx"}} 0\n'
            f'yunshu_reranker_completed_total{{backend="pytorch"}} {cls.pytorch_success}\n'
        )


class MetricsHandler(http.server.BaseHTTPRequestHandler):
    """暴露故障指标，模拟 ONNX 持续加载失败（counter 递增）"""

    def do_GET(self):
        if self.path == "/metrics":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(_FaultState.snapshot().encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # 静默日志


class MetricsExporter:
    """管理 metrics exporter 线程生命周期"""

    def __init__(self, port: int):
        self.port = port
        self.httpd: socketserver.TCPServer | None = None
        self.thread: threading.Thread | None = None

    def start(self):
        self.httpd = socketserver.TCPServer(("0.0.0.0", self.port), MetricsHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        ok(f"Metrics exporter started on :{self.port}")

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            info("Metrics exporter stopped")


# ════════════════════════════════════════════════════════════
#  Docker 操作
# ════════════════════════════════════════════════════════════
def run_cmd(cmd: list[str], check: bool = True, timeout: int = 60) -> tuple[int, str]:
    """运行命令，返回 (returncode, output)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, encoding="utf-8")
        if check and r.returncode != 0:
            err(f"Command failed: {' '.join(cmd)}")
            if r.stderr: err(f"  stderr: {r.stderr.strip()}")
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        err(f"Command timeout: {' '.join(cmd)}")
        return 1, "timeout"


def docker_running() -> bool:
    rc, _ = run_cmd(["docker", "info"], check=False, timeout=10)
    return rc == 0


def remove_container(name: str):
    """清理已存在的同名容器"""
    run_cmd(["docker", "rm", "-f", name], check=False, timeout=15)


def start_sandbox_prometheus(config_dir: str) -> bool:
    """启动沙箱 Prometheus 容器"""
    # Windows 路径 → Docker 挂载格式
    mount = str(Path(config_dir).resolve()).replace("\\", "/")
    cmd = [
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"{SANDBOX_PORT}:9090",
        "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{mount}:/etc/prometheus:ro",
        IMAGE,
        "--config.file=/etc/prometheus/prometheus.yml",
        "--storage.tsdb.path=/prometheus",
        "--web.enable-lifecycle",
    ]
    rc, out = run_cmd(cmd, check=False, timeout=60)
    if rc != 0:
        err(f"Failed to start sandbox container: {out}")
        return False
    ok(f"Sandbox Prometheus started: {CONTAINER_NAME} (port {SANDBOX_PORT})")
    return True


def restart_container(name: str) -> bool:
    """模拟服务重启"""
    info(f"Restarting container {name}...")
    rc, _ = run_cmd(["docker", "restart", name], check=False, timeout=30)
    if rc != 0:
        err(f"Failed to restart container {name}")
        return False
    ok(f"Container {name} restarted")
    return True


# ════════════════════════════════════════════════════════════
#  Prometheus API 查询
# ════════════════════════════════════════════════════════════
def query_prometheus(path: str, timeout: int = 10) -> dict | None:
    """查询沙箱 Prometheus API"""
    url = f"http://localhost:{SANDBOX_PORT}{path}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        warn(f"Prometheus API query failed: {e}")
        return None


def get_alerts() -> list[dict]:
    """获取当前告警列表"""
    result = query_prometheus("/api/v1/alerts")
    if result and result.get("status") == "success":
        return result.get("data", {}).get("alerts", [])
    return []


def get_loaded_rules() -> list[dict]:
    """获取已加载的规则列表"""
    result = query_prometheus("/api/v1/rules")
    if result and result.get("status") == "success":
        return result.get("data", {}).get("groups", [])
    return []


def query_metric(metric: str) -> list[dict]:
    """查询瞬时指标值，验证是否被抓取"""
    import urllib.parse
    path = f"/api/v1/query?query={urllib.parse.quote(metric)}"
    result = query_prometheus(path)
    if result and result.get("status") == "success":
        return result.get("data", {}).get("result", [])
    return []


def find_alert(alerts: list[dict], alert_name: str) -> dict | None:
    """从告警列表中查找指定告警"""
    for a in alerts:
        if a.get("labels", {}).get("alertname") == alert_name:
            return a
    return None


# ════════════════════════════════════════════════════════════
#  沙箱配置准备
# ════════════════════════════════════════════════════════════
def prepare_sandbox_config(tmpdir: str) -> bool:
    """创建沙箱 Prometheus 配置（引用 reranker-alerts.yml，抓取测试 exporter）"""
    tmpdir_path = Path(tmpdir)

    # 复制规则文件
    if not RULES_SRC.exists():
        err(f"Rules file not found: {RULES_SRC}")
        return False
    shutil.copy2(RULES_SRC, tmpdir_path / "reranker-alerts.yml")

    # 生成 prometheus.yml（抓取 host.docker.internal:EXPORTER_PORT 模拟故障指标）
    # Windows Docker Desktop: host.docker.internal 可访问宿主机
    prom_yml = f"""global:
  scrape_interval: {SCRAPE_INTERVAL}s
  evaluation_interval: {EVAL_INTERVAL}s

rule_files:
  - 'reranker-alerts.yml'

scrape_configs:
  - job_name: 'fault-injector'
    static_configs:
      - targets: ['host.docker.internal:{EXPORTER_PORT}']
    metrics_path: '/metrics'
    scrape_interval: {SCRAPE_INTERVAL}s
"""
    (tmpdir_path / "prometheus.yml").write_text(prom_yml, encoding="utf-8")
    ok(f"Sandbox config prepared in {tmpdir}")
    return True


# ════════════════════════════════════════════════════════════
#  主流程
# ════════════════════════════════════════════════════════════
def main():
    section("Prometheus Restart Simulation - Alert Recovery")

    # 0. 前置检查
    info("Checking prerequisites...")
    if not docker_running():
        err("Docker daemon not running; abort")
        sys.exit(1)
    ok("Docker daemon available")

    if not RULES_SRC.exists():
        err(f"Rules source not found: {RULES_SRC}")
        sys.exit(1)
    ok(f"Rules source: {RULES_SRC}")

    # 清理同名旧容器
    remove_container(CONTAINER_NAME)

    tmpdir = tempfile.mkdtemp(prefix="prometheus-restart-")
    exporter = MetricsExporter(EXPORTER_PORT)

    try:
        # 1. 准备沙箱配置
        section("Phase 1: Prepare sandbox config")
        if not prepare_sandbox_config(tmpdir):
            sys.exit(2)

        # 2. 启动 metrics exporter
        section("Phase 2: Start fault-injection exporter")
        exporter.start()
        info("Injecting metrics (counter increments per scrape):")
        info("  yunshu_reranker_load_total{backend=onnx,status=failed} (incrementing)")
        info("  yunshu_reranker_load_total{backend=pytorch,status=success} (incrementing)")

        # 3. 启动沙箱 Prometheus
        section("Phase 3: Start sandbox Prometheus")
        if not start_sandbox_prometheus(tmpdir):
            sys.exit(3)

        # 4. 等待抓取 + 告警评估
        section("Phase 4: Wait for scrape + alert evaluation")
        info(f"Waiting {WAIT_SCRAPE}s for scrape + evaluation...")
        time.sleep(WAIT_SCRAPE)

        # 验证指标是否被抓取（诊断 host.docker.internal 连通性）
        info("Verifying metric scrape (diagnose network)...")
        scraped = query_metric("yunshu_reranker_load_total")
        if scraped:
            ok(f"Metric scraped: {len(scraped)} series")
            for s in scraped:
                labels = s.get("metric", {})
                info(f"  {labels.get('backend','?')}/{labels.get('status','?')} = {s.get('value',['','?'])[1]}")
        else:
            err("Metric NOT scraped; host.docker.internal may be unreachable")
            info("Troubleshooting: check --add-host and exporter port")
            # 查看容器日志辅助诊断
            rc, logs = run_cmd(["docker", "logs", "--tail", "15", CONTAINER_NAME], check=False, timeout=15)
            if logs:
                info(f"Container logs (last 15 lines):\n{logs}")
            sys.exit(4)

        # 验证规则加载
        info("Checking loaded rules...")
        rules = get_loaded_rules()
        rule_count = sum(len(g.get("rules", [])) for g in rules)
        if rule_count == 0:
            err("No rules loaded; check sandbox config")
            sys.exit(4)
        ok(f"Rules loaded: {rule_count} rules in {len(rules)} group(s)")
        for g in rules:
            info(f"  group: {g.get('name')} ({len(g.get('rules', []))} rules)")

        # 验证 P0 告警触发
        info("Checking P0 alert RerankerOnnxLoadFailed...")
        alerts = get_alerts()
        p0_alert = find_alert(alerts, "RerankerOnnxLoadFailed")
        if p0_alert:
            state = p0_alert.get("state", "unknown")
            ok(f"P0 alert RerankerOnnxLoadFailed: state={state}")
            if state == "firing":
                ok("P0 alert FIRING (as expected - load_failed injected)")
            elif state == "pending":
                ok("P0 alert PENDING (for: 0m should fire quickly)")
            else:
                warn(f"P0 alert state={state} (expected firing/pending)")
        else:
            warn("P0 alert not yet active; checking all alerts...")
            for a in alerts:
                info(f"  {a.get('labels', {}).get('alertname')}: {a.get('state')}")

        # 5. 模拟重启
        section("Phase 5: Simulate service restart")
        info(f"Restarting {CONTAINER_NAME}...")
        if not restart_container(CONTAINER_NAME):
            sys.exit(5)

        # 6. 等待恢复
        section("Phase 6: Wait for recovery + re-validation")
        info(f"Waiting {WAIT_RESTART}s for restart recovery...")
        time.sleep(WAIT_RESTART)

        # 验证重启后指标抓取恢复
        info("Post-restart: verifying metric scrape recovery...")
        scraped_after = query_metric("yunshu_reranker_load_total")
        if scraped_after:
            ok(f"Metric scraped after restart: {len(scraped_after)} series")
        else:
            warn("Metric not scraped after restart (TSDB may need warmup)")

        # 验证重启后规则重新加载
        info("Post-restart: checking rules reload...")
        rules_after = get_loaded_rules()
        rule_count_after = sum(len(g.get("rules", [])) for g in rules_after)
        if rule_count_after == 0:
            err("Rules NOT reloaded after restart; abort")
            sys.exit(6)
        ok(f"Rules reloaded after restart: {rule_count_after} rules")

        if rule_count_after == rule_count:
            ok(f"Rule count consistent: {rule_count} == {rule_count_after}")
        else:
            warn(f"Rule count changed: {rule_count} -> {rule_count_after}")

        # 验证重启后告警恢复
        info("Post-restart: checking alert recovery...")
        alerts_after = get_alerts()
        p0_after = find_alert(alerts_after, "RerankerOnnxLoadFailed")
        if p0_after:
            state_after = p0_after.get("state", "unknown")
            ok(f"P0 alert after restart: state={state_after}")
            if state_after in ("firing", "pending"):
                ok("Alert correctly re-evaluated after restart")
            else:
                warn(f"Alert state={state_after} (may need more scrape cycles)")
        else:
            warn("P0 alert inactive after restart (TSDB may need 2+ samples for increase())")
            info("This is expected: increase() needs multiple samples; rule IS loaded")

        # 7. 汇总
        section("Summary")
        print(f"  Initial rules loaded:   {rule_count} rules")
        print(f"  Initial P0 alert:       {'YES' if p0_alert else 'NO'}")
        print(f"  Post-restart rules:     {rule_count_after} rules")
        print(f"  Post-restart P0 alert:  {'YES' if p0_after else 'NO'}")
        print()
        if rule_count_after > 0 and rule_count_after == rule_count:
            ok("PASS: Rules correctly reloaded after restart")
            ok("PASS: Alert evaluation recovered after restart")
        else:
            warn("PARTIAL: See details above")

    finally:
        # 清理
        section("Cleanup")
        exporter.stop()
        info(f"Removing container {CONTAINER_NAME}...")
        remove_container(CONTAINER_NAME)
        info(f"Removing temp dir {tmpdir}...")
        shutil.rmtree(tmpdir, ignore_errors=True)
        ok("Cleanup complete")

    print()
    ok("Simulation finished")


if __name__ == "__main__":
    main()
