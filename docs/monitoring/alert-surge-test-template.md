# 告警激增验证标准测试模板

> **文档用途**: 提供可复制的告警激增验证脚本和 pytest 用例模板，用于验证 Grafana 告警规则在指标激增时能否及时触发 Firing。
> **创建日期**: 2026-07-23
> **适用场景**: 新增/修改告警规则后的端到端验证、CI 集成测试、本地冒烟测试
> **前置依赖**: Grafana 13+ / Prometheus / Docker

## 1. 概述

本文档提供两类模板：

| 模板 | 用途 | 运行环境 |
|------|------|---------|
| 激增验证脚本 | 模拟指标激增，验证告警 Firing | 本地 / CI |
| pytest 用例 | 自动化端到端验证，纳入 CI 流水线 | 本地 / CI |

**验证链路**:
```
激增脚本发射指标
  → BusinessMetricsCollector 单例 (内存 _counters)
  → /metrics 端点 (port 5678)
  → Prometheus scrape (interval 5s)
  → Grafana alert evaluation (interval 30s)
  → ngalert.sender.router → "Sending alerts" 日志
```

## 2. 激增验证脚本模板

> **文件命名约定**: `scripts/_<场景>_surge.py`（下划线前缀，被 `.gitignore` 的 `scripts/_*.py` 屏蔽，不入库）

```python
#!/usr/bin/env python
"""_<场景>_surge.py — 指标激增模拟（临时验证用，不入库）。

验证目标: <填写告警规则名称> 的 for 时长是否在激增条件下及时触发 Firing。
验证依据: docs/monitoring/alert-verification.md
"""
from __future__ import annotations
import http.server, socketserver, threading, time, sys, os, uuid
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from agent.skills_mgmt.observability import emit_eval_score_metric
from agent.monitoring.business_metrics import get_business_metrics_collector

PORT = int(os.environ.get("MOCK_METRICS_PORT", "5678"))
BURST_COUNT = 25        # burst 数量，需超过 critical 阈值
SUSTAIN_INTERVAL = 5   # 维持间隔（秒），与 Prometheus scrape interval 对齐
SKILL_ID = "<填写测试用 skill_id>"


def _emit_hallucination(skill_id, score=0.15, trace_id=None):
    """发射一条幻觉指标。

    [不易] 使用 emit_eval_score_metric（内部走单例 collector），
    确保指标写入 /metrics 端点导出的同一实例。
    """
    emit_eval_score_metric(skill_id, {
        "task_success": False,
        "instruction_followed": False,
        "hallucination_detected": True,
        "score": score,
    }, trace_id=trace_id or f"surge-{uuid.uuid4().hex[:8]}")


def _emit_burst():
    """Burst: 启动时立即发射 BURST_COUNT 条幻觉，使 increase[5m] 立即超阈值。"""
    print(f"[surge] burst {BURST_COUNT} @ {time.strftime('%H:%M:%S')}", file=sys.stderr)
    for i in range(BURST_COUNT):
        _emit_hallucination(SKILL_ID, 0.15, f"surge-burst-{i:03d}")


def _sustain_emitter():
    """Sustain: 每 SUSTAIN_INTERVAL 秒发射 1 条，维持 increase[5m] 持续超阈值。"""
    counter = 0
    while True:
        time.sleep(SUSTAIN_INTERVAL)
        try:
            counter += 1
            _emit_hallucination(SKILL_ID, 0.18, f"surge-s-{counter:04d}")
        except Exception as e:
            print(f"[surge] sustain fail: {e}", file=sys.stderr)


class _MetricsHandler(http.server.BaseHTTPRequestHandler):
    """Prometheus /metrics 端点处理器。

    [变易] 合并 prometheus_client 默认 registry + BusinessMetricsCollector 单例导出，
    确保所有指标（含 emit_metric 发射的）均可被 Prometheus 抓取。
    """
    def do_GET(self):
        if self.path != "/metrics":
            self.send_response(404); self.end_headers(); return
        c = get_business_metrics_collector()
        parts = [generate_latest().decode("utf-8")]
        try:
            parts.append(c.export_prometheus())
        except Exception:
            pass
        body = "\n".join(parts).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a): pass


def main():
    _emit_burst()
    t = threading.Thread(target=_sustain_emitter, daemon=True)
    t.start()
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), _MetricsHandler) as httpd:
        print(f"[surge] HTTP :{PORT} @ {time.strftime('%H:%M:%S')}", file=sys.stderr)
        httpd.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
```

### 2.1 脚本使用方法

```bash
# 1. 复制模板
cp docs/monitoring/alert-surge-test-template.md /tmp/extract_script.py
# （从本文档提取脚本部分保存为 scripts/_<场景>_surge.py）

# 2. 设置环境变量
$env:PYTHONPATH = "c:\Users\Administrator\agent"
$env:PYTHONIOENCODING = "utf-8"

# 3. 后台启动激增脚本
python -u scripts/_<场景>_surge.py &

# 4. 等待 Firing（critical for=30s + 评估间隔 30s = ~60s）
sleep 90

# 5. 验证 Grafana Firing
docker logs yunshu-grafana --since 3m 2>&1 | grep "Sending alerts"

# 6. 验证 Prometheus 指标
curl "http://localhost:9090/api/v1/query?query=increase(yunshu_skill_hallucination_total[5m])"

# 7. 停止激增脚本
kill %1

# 8. 恢复原 mock server
python -u scripts/mock_metrics_server.py &
```

## 3. pytest 用例模板

> **文件位置**: `tests/integration/test_alert_<场景>_e2e.py`
> **标记**: `@pytest.mark.integration`（需 Docker + Grafana + Prometheus）

```python
"""告警规则端到端验证测试（L2 — 需 Docker + Grafana + Prometheus）

验证完整的告警状态转换周期: Normal → Pending → Firing → Normal

前置条件:
  - Docker 容器 yunshu-grafana (port 3000) + yunshu-prometheus (port 9090) 运行中
  - mock_metrics_server.py 可在 port 5678 启动
  - 环境变量 GRAFANA_ADMIN_USER / GRAFANA_ADMIN_PASSWORD 已配置

运行方式:
  # 本地运行（需 Docker 环境就绪）
  pytest tests/integration/test_alert_<场景>_e2e.py -m integration -v

  # CI 中通过 schedule 或手动触发
  pytest tests/integration/test_alert_<场景>_e2e.py -m integration --timeout=600

验证依据: docs/monitoring/alert-verification.md
"""
import os, sys, time, uuid, subprocess, textwrap, json
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.parse import urlencode
from urllib.error import URLError
import pytest

# ── 路径常量 ──
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ── 环境配置 ──
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3000")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")
GRAFANA_USER = os.getenv("GRAFANA_ADMIN_USER", "admin")
GRAFANA_PASS = os.getenv("GRAFANA_ADMIN_PASSWORD", "")
MOCK_PORT = int(os.getenv("MOCK_METRICS_PORT", "5678"))

# critical 规则: for=30s + 评估间隔 30s → 最长 60s 后 Firing
SURGE_WAIT_FIRING = 90   # 等 Firing 的最长秒数
POLL_INTERVAL = 15       # 轮询间隔


# ============================================================================
# 辅助函数
# ============================================================================

def _http_get(url, headers=None, timeout=10):
    """发送 GET 请求，返回解析后的 JSON"""
    req = Request(url, headers=headers or {})
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except URLError:
        return None


def _grafana_headers():
    """构造 Grafana API 认证头"""
    import base64
    pair = f"{GRAFANA_USER}:{GRAFANA_PASS}"
    b64 = base64.b64encode(pair.encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {b64}"}


def _prometheus_query(query):
    """查询 Prometheus 指标"""
    params = urlencode({"query": query})
    url = f"{PROMETHEUS_URL}/api/v1/query?{params}"
    result = _http_get(url)
    if result and result.get("status") == "success":
        return result.get("data", {}).get("result", [])
    return []


def _check_service_available(url, name):
    """检查服务是否可用"""
    try:
        urlopen(url, timeout=5)
        return True
    except Exception:
        return False


def _get_grafana_alert_logs(container_name="yunshu-grafana", since="2m",
                            rule_keyword="yunshu-skill-hallucination"):
    """从 Grafana 容器日志获取告警发送记录

    Returns:
        list[str]: 包含 "Sending alerts" 的日志行列表
    """
    try:
        result = subprocess.run(
            ["docker", "logs", container_name, "--since", since],
            capture_output=True, text=True, timeout=15
        )
        lines = (result.stdout + result.stderr).splitlines()
        return [l for l in lines if "Sending alerts" in l and rule_keyword in l]
    except Exception:
        return []


def _emit_surge_via_subprocess(skill_id, count, port):
    """通过子进程发射幻觉指标并启动 HTTP server

    Why: 独立子进程确保 BusinessMetricsCollector 单例干净，
         不受主测试进程状态影响。
    """
    script = textwrap.dedent(f"""
        import sys, os, uuid, time, threading
        sys.path.insert(0, {str(PROJECT_ROOT)!r})
        os.environ.setdefault("PYTHONPATH", {str(PROJECT_ROOT)!r})
        from agent.skills_mgmt.observability import emit_eval_score_metric
        from agent.monitoring.business_metrics import get_business_metrics_collector
        from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
        import http.server, socketserver

        # Burst
        for i in range({count}):
            emit_eval_score_metric({skill_id!r}, {{
                "task_success": False, "instruction_followed": False,
                "hallucination_detected": True, "score": 0.15,
            }}, trace_id=f"e2e-surge-{{uuid.uuid4().hex[:8]}}")

        # Sustain
        def _sustain():
            while True:
                time.sleep(5)
                try:
                    emit_eval_score_metric({skill_id!r}, {{
                        "task_success": False, "instruction_followed": False,
                        "hallucination_detected": True, "score": 0.18,
                    }}, trace_id=f"e2e-sustain-{{uuid.uuid4().hex[:8]}}")
                except Exception:
                    pass

        threading.Thread(target=_sustain, daemon=True).start()

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/metrics":
                    self.send_response(404); self.end_headers(); return
                c = get_business_metrics_collector()
                parts = [generate_latest().decode("utf-8")]
                try:
                    parts.append(c.export_prometheus())
                except Exception:
                    pass
                body = "\\n".join(parts).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *a): pass

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer(("0.0.0.0", {port}), H) as httpd:
            httpd.serve_forever()
    """)

    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)},
    )
    return proc


def _skip_if_no_infra():
    """如果没有 Docker + Grafana + Prometheus，跳过测试"""
    if not _check_service_available(f"{GRAFANA_URL}/api/health"):
        pytest.skip("Grafana 不可用，跳过端到端告警测试")
    if not _check_service_available(f"{PROMETHEUS_URL}/-/healthy"):
        pytest.skip("Prometheus 不可用，跳过端到端告警测试")


# ============================================================================
# 端到端测试
# ============================================================================

@pytest.mark.integration
class TestAlertEndToEnd:
    """端到端告警状态转换验证

    验证周期: Normal → Pending → Firing → Normal
    依据: docs/monitoring/alert-verification.md
    """

    @pytest.fixture(autouse=True)
    def _check_infra(self):
        """每个测试前检查基础设施可用性"""
        _skip_if_no_infra()

    def test_critical_rule_fires_on_surge(self):
        """验证 critical 告警在幻觉激增时及时 Firing

        步骤:
          1. 启动激增版 mock server（burst 25 条 + 每 5s 维持）
          2. 等待 Prometheus 抓取 + Grafana 评估
          3. 验证 Grafana 日志出现 "Sending alerts" (critical)
        """
        skill_id = f"e2e-test-{uuid.uuid4().hex[:8]}"
        proc = _emit_surge_via_subprocess(skill_id, count=25, port=MOCK_PORT)
        try:
            time.sleep(10)  # 等 Prometheus 抓取
            deadline = time.time() + SURGE_WAIT_FIRING
            fired = False
            while time.time() < deadline:
                logs = _get_grafana_alert_logs(since="2m")
                if any("critical" in l for l in logs):
                    fired = True
                    break
                time.sleep(POLL_INTERVAL)
            assert fired, (
                f"在 {SURGE_WAIT_FIRING}s 内未检测到 critical 告警 Firing。"
            )
        finally:
            proc.terminate()
            proc.wait(timeout=10)

    def test_prometheus_scrape_healthy(self):
        """验证 Prometheus 能抓取 mock server 指标"""
        targets = _http_get(f"{PROMETHEUS_URL}/api/v1/targets")
        if not targets:
            pytest.skip("无法查询 Prometheus targets")
        yunshu_targets = [
            t for t in targets.get("data", {}).get("activeTargets", [])
            if t.get("labels", {}).get("job") == "yunshu"
        ]
        assert yunshu_targets, "Prometheus 无 yunshu job 目标"
        assert yunshu_targets[0]["health"] == "up", (
            f"yunshu target 不健康: {yunshu_targets[0]['health']}"
        )

    def test_grafana_alert_rules_loaded(self):
        """验证 Grafana 已加载告警规则"""
        headers = _grafana_headers()
        rules = _http_get(f"{GRAFANA_URL}/api/v1/provisioning/alert-rules", headers=headers)
        if not rules:
            pytest.skip("无法查询 Grafana 告警规则")
        uids = {r.get("uid") for r in rules}
        assert "yunshu-skill-hallucination-warning" in uids, "warning 规则未加载"
        assert "yunshu-skill-hallucination-critical" in uids, "critical 规则未加载"

    def test_critical_for_duration_is_30s(self):
        """验证 critical 规则 for 时长已优化为 30s"""
        headers = _grafana_headers()
        rules = _http_get(f"{GRAFANA_URL}/api/v1/provisioning/alert-rules", headers=headers)
        if not rules:
            pytest.skip("无法查询 Grafana 告警规则")
        critical = next(
            (r for r in rules if r.get("uid") == "yunshu-skill-hallucination-critical"),
            None
        )
        assert critical is not None, "critical 规则不存在"
        assert critical["for"] == "30s", (
            f"critical for 时长应为 30s，实际为 {critical['for']}"
        )
```

## 4. 使用说明

### 4.1 前置条件

| 条件 | 说明 |
|------|------|
| Docker | yunshu-grafana + yunshu-prometheus 容器运行中 |
| Prometheus | yunshu job target=up, scrape interval=5s |
| Grafana | 告警规则已 provisioning, 评估间隔=30s |
| 环境变量 | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` (from .env) |
| 端口 5678 | 空闲（或已停掉原 mock_metrics_server）|

### 4.2 定制化参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `BURST_COUNT` | 25 | burst 数量，需超过 critical 阈值 (20) |
| `SUSTAIN_INTERVAL` | 5s | 维持间隔，与 Prometheus scrape interval 对齐 |
| `SKILL_ID` | `<测试用>` | 激增模拟的 skill_id，用于 Grafana 告警分组 |
| `SURGE_WAIT_FIRING` | 90s | pytest 等 Firing 的最长秒数（for + 评估间隔 + 余量）|
| `MOCK_PORT` | 5678 | /metrics 端点端口 |

### 4.3 运行方式

```bash
# ── 本地手动验证（激增脚本）──
$env:PYTHONPATH = "c:\Users\Administrator\agent"
python -u scripts/_<场景>_surge.py &
sleep 90
docker logs yunshu-grafana --since 3m 2>&1 | grep "Sending alerts"

# ── pytest 自动化验证 ──
$env:GRAFANA_ADMIN_PASSWORD = "<from .env>"
python -m pytest tests/integration/test_alert_<场景>_e2e.py -m integration -v

# ── CI 集成（observability-ci.yml 已配置）──
# L1 配置测试: tests/unit/test_alert_rules_config.py (无外部依赖, CI 必跑)
# L2 端到端测试: tests/integration/test_alert_e2e.py (@pytest.mark.integration)
```

## 5. 验证检查清单

### 5.1 激增脚本验证

- [ ] burst 发射日志出现: `[surge] burst 25 @ HH:MM:SS`
- [ ] HTTP server 启动: `[surge] HTTP :5678 @ HH:MM:SS`
- [ ] /metrics 端点返回 200
- [ ] hallucination counter 有实际值（非仅 HELP/TYPE 行）
- [ ] Prometheus target=up
- [ ] Grafana 日志出现 "Sending alerts" (critical)
- [ ] Grafana 日志出现 "Sending alerts" (warning)

### 5.2 pytest 用例验证

- [ ] `test_prometheus_scrape_healthy` PASSED
- [ ] `test_grafana_alert_rules_loaded` PASSED
- [ ] `test_critical_for_duration_is_30s` PASSED
- [ ] `test_critical_rule_fires_on_surge` PASSED（需较长运行时间）

### 5.3 时间线匹配

| 规则 | for 时长 | 预期 Firing 延迟 | 验证方法 |
|------|---------|-----------------|---------|
| critical | 30s | 60s (30s for + 30s 评估) | burst 时间 → 首条 "Sending alerts" 时间差 |
| warning | 2m | 150s (120s for + 30s 评估) | 同上 |

## 6. 常见问题

### 6.1 counter 有 HELP/TYPE 但无值

**根因**: `observability.py` 用 `BusinessMetricsCollector()` 直接实例化而非 `get_business_metrics_collector()` 单例（已于 2026-07-23 修复）。

**验证**: 修复后 `emit_eval_score_metric()` 应正确写入单例 collector，`export_prometheus()` 输出包含实际值。

### 6.2 Grafana API 401

**根因**: 密码未通过环境变量传递，或 .env 中密码已变更。

**修复**: 从 `.env` 读取 `GRAFANA_ADMIN_PASSWORD`，设置到环境变量后重试。

### 6.3 Grafana 日志显示 "stale state reason=NoData"

**根因**: Grafana 重启后 alerting 引擎初始化，首次评估可能返回 NoData。

**修复**: 等待 1 个评估周期（30s）后重新检查，NoData 是临时状态。

### 6.4 Prometheus target=down

**根因**: mock_metrics_server 未运行，或端口 5678 被占用。

**修复**: 检查 `netstat -ano | grep 5678`，确保 mock server 已启动。

## 7. 相关文件

| 文件 | 用途 |
|------|------|
| `monitoring/grafana/alerting/yunshu-skill-hallucination.yml` | 告警规则定义 |
| `scripts/mock_metrics_server.py` | 常规 mock server（基线指标）|
| `agent/skills_mgmt/observability.py` | emit_eval_score_metric 实现 |
| `agent/monitoring/business_metrics.py` | BusinessMetricsCollector 单例 + export_prometheus |
| `tests/unit/test_alert_rules_config.py` | L1 配置测试（CI 必跑）|
| `tests/integration/test_alert_e2e.py` | L2 端到端测试（需 Docker）|
| `docs/monitoring/alert-verification.md` | 验证报告归档 |
| `.github/workflows/observability-ci.yml` | CI 流水线配置 |
