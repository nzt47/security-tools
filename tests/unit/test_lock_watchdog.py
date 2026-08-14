"""C2 锁看门狗单元测试 — 模拟持锁超时，验证 Prometheus 告警链路生效

覆盖:
    1. 持锁超时触发告警（核心: 计数 + 指标可查）
    2. 正常持锁不误报
    3. 锁等待超时（潜在死锁/饥饿）检测
    4. 指标已注册进 BUSINESS_METRICS_DEFINITIONS（Prometheus /metrics 暴露前提）
    5. 告警规则 YAML 存在且 PromQL expr 引用对应指标（告警规则静态校验）
    6. 指标注册幂等（重复初始化不产生重复条目）
"""

import os
import re
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.monitoring.lock_watchdog import LockWatchdog, WatchedLock
from agent.monitoring.business_metrics import BUSINESS_METRICS_DEFINITIONS

ALERT_RULE_PATH = os.path.join(PROJECT_ROOT, "monitoring", "prometheus", "rules", "lock_watchdog_alerts.yml")


class TestLockHoldTimeout:
    """模拟持锁超时 → 验证告警计数触发"""

    def test_hold_timeout_triggers_alert(self):
        """持锁 50ms > 阈值 10ms → lock_hold_timeouts_total 计数 +1（核心用例）"""
        wd = LockWatchdog(hold_ms=10, wait_ms=0)
        lock = WatchedLock(name="test_hold_lock", watchdog=wd)
        with lock:
            time.sleep(0.05)  # 模拟持锁期间的阻塞行为
        metrics = wd.get_metrics()
        assert metrics["lock_hold_timeouts_total"].get("test_hold_lock") == 1

    def test_hold_timeout_records_lock_name(self):
        """不同锁名各自独立计数（labels=lock_name 的语义）"""
        wd = LockWatchdog(hold_ms=10, wait_ms=0)
        lock_a = WatchedLock(name="lock_a", watchdog=wd)
        lock_b = WatchedLock(name="lock_b", watchdog=wd)
        with lock_a:
            time.sleep(0.05)
        # lock_b 正常持锁，不触发
        with lock_b:
            time.sleep(0.001)
        metrics = wd.get_metrics()
        assert metrics["lock_hold_timeouts_total"].get("lock_a") == 1
        assert metrics["lock_hold_timeouts_total"].get("lock_b") is None

    def test_normal_hold_no_false_positive(self):
        """正常持锁（< 阈值）→ 零告警（不误报）"""
        wd = LockWatchdog(hold_ms=1000, wait_ms=0)
        lock = WatchedLock(name="test_normal_lock", watchdog=wd)
        for _ in range(10):
            with lock:
                time.sleep(0.001)
        assert wd.get_metrics()["lock_hold_timeouts_total"] == {}


class TestLockWaitTimeout:
    """模拟锁等待超时（潜在死锁/饥饿）"""

    def test_wait_timeout_detects_starvation(self):
        """另一线程长期持锁 → 本线程 acquire 等待 50ms > 阈值 10ms → 告警"""
        wd = LockWatchdog(hold_ms=0, wait_ms=10)
        lock = WatchedLock(name="test_wait_lock", watchdog=wd)

        holder = threading.Thread(target=lambda: (lock.acquire(), time.sleep(0.2), lock.release()))
        holder.start()
        time.sleep(0.05)  # 确保 holder 已持锁

        lock.acquire(timeout=0.05)  # 等待约 50ms 后超时返回 False

        holder.join()
        metrics = wd.get_metrics()
        assert metrics["lock_wait_timeouts_total"].get("test_wait_lock") == 1


class TestPrometheusIntegration:
    """验证 Prometheus 告警链路：指标注册 + 告警规则引用"""

    def test_metrics_registered_in_definitions(self):
        """3 个指标已注册进 BUSINESS_METRICS_DEFINITIONS（/metrics 可暴露）"""
        LockWatchdog.get()  # 触发注册（幂等）
        for name in ("lock_hold_timeouts_total", "lock_wait_timeouts_total", "lock_hold_duration_ms"):
            assert name in BUSINESS_METRICS_DEFINITIONS, f"指标 {name} 未注册"
            assert BUSINESS_METRICS_DEFINITIONS[name].metric_type in ("counter", "histogram")

    def test_metrics_registration_idempotent(self):
        """重复 get()/注册不产生重复条目"""
        LockWatchdog.get()
        base = len(BUSINESS_METRICS_DEFINITIONS)
        LockWatchdog.get()
        assert len(BUSINESS_METRICS_DEFINITIONS) == base

    def test_alert_rule_yaml_exists_and_references_metric(self):
        """告警规则文件存在且 PromQL expr 引用看门狗指标（规则静态校验）"""
        assert os.path.exists(ALERT_RULE_PATH), f"告警规则缺失: {ALERT_RULE_PATH}"
        text = Path(ALERT_RULE_PATH).read_text(encoding="utf-8")
        # 两条告警规则与对应指标完整引用
        assert "LockHoldTimeout" in text
        assert re.search(r"expr:\s*.*lock_hold_timeouts_total", text), "LockHoldTimeout 未引用 lock_hold_timeouts_total"
        assert "LockWaitTimeout" in text
        assert re.search(r"expr:\s*.*lock_wait_timeouts_total", text), "LockWaitTimeout 未引用 lock_wait_timeouts_total"

    def test_alert_rule_severity_classification(self):
        """持锁超时=critical（锁纪律违规），等待超时=warning（潜在风险）"""
        text = Path(ALERT_RULE_PATH).read_text(encoding="utf-8")
        hold_block = text.split("LockWaitTimeout")[0]
        assert "severity: critical" in hold_block
        assert "severity: warning" in text
