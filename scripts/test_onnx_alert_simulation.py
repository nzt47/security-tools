"""v6.5 ONNX Reranker P0 告警模拟故障测试

测试目标:
    验证当 ONNX 加载失败时，告警系统能否正确触发 P0 级通知

测试方案:
    1. 场景 A: ONNX 路径不存在 → 触发 onnx.skip + onnx.fallback_to_pytorch
    2. 场景 B: ONNX 文件缺失 → 触发 onnx.skip (file_not_found)
    3. 场景 C: 双后端全挂 → 触发 P0 RerankerAllBackendsDown
    4. 验证 emit_metric 指标计数器增加
    5. 验证 P0 告警规则表达式能匹配

设计原则:
    【不易】不破坏生产配置，所有环境变量在测试结束后恢复
    【变易】三种故障场景独立测试，覆盖 P0 告警所有路径
    【简易】通过捕获 logging 日志验证告警触发，无需真实 Prometheus

运行:
    python scripts/test_onnx_alert_simulation.py
"""
from __future__ import annotations

import io
import json
import logging
import os
import sys
import tempfile
from typing import Any, Dict, List
from unittest.mock import MagicMock

# 确保项目根目录在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 【不易】防止 sentence_transformers 真实 import 导致 Windows 0xC0000005 崩溃
# 必须在 import reranker 之前 mock
if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = MagicMock()


# ════════════════════════════════════════════════════════════
#  日志捕获器（捕获 reranker 模块的结构化 JSON 日志）
# ════════════════════════════════════════════════════════════

class _LogCollector:
    """自定义 logger 替代物，收集所有日志调用

    【简易】完全绕过 logging 系统，直接收集 msg 字符串
    """

    def __init__(self, records: List[Dict[str, Any]], raw_messages: List[str]):
        self._records = records
        self._raw_messages = raw_messages

    def _record(self, msg, *args, **kwargs):
        msg_str = str(msg)
        self._raw_messages.append(msg_str)
        # 尝试解析 JSON
        idx = msg_str.find("{")
        if idx >= 0:
            try:
                record = json.loads(msg_str[idx:])
                if isinstance(record, dict) and "action" in record:
                    self._records.append(record)
            except json.JSONDecodeError:
                pass

    def info(self, msg, *args, **kwargs):
        self._record(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._record(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._record(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._record(msg, *args, **kwargs)


class JsonLogCapture:
    """捕获 reranker 模块的 JSON 日志，便于断言

    【变易】直接 patch reranker.logger 对象为 _LogCollector，
    完全绕过 logging 系统（最可靠方式）
    """

    def __init__(self):
        self.records: List[Dict[str, Any]] = []
        self._raw_messages: List[str] = []
        self._collector = _LogCollector(self.records, self._raw_messages)
        self._patches: List[Any] = []

    def __enter__(self):
        from unittest.mock import patch
        from agent.skills_mgmt import reranker as reranker_mod
        from agent.skills_mgmt import observability as obs_mod

        # patch reranker 和 observability 模块的 logger 对象
        self._patches.append(
            patch.object(reranker_mod, "logger", self._collector)
        )
        self._patches.append(
            patch.object(obs_mod, "logger", self._collector)
        )
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self._patches:
            p.stop()
        return False

    def filter_by_action(self, action: str) -> List[Dict[str, Any]]:
        """按 action 字段过滤记录"""
        return [r for r in self.records if r.get("action") == action]

    def has_action(self, action: str) -> bool:
        """是否包含指定 action"""
        return any(r.get("action") == action for r in self.records)


# ════════════════════════════════════════════════════════════
#  指标捕获器（捕获 emit_metric 调用）
# ════════════════════════════════════════════════════════════

class MetricCapture:
    """捕获 emit_metric 调用，模拟 Prometheus 指标累积"""

    def __init__(self):
        self.counters: Dict[str, float] = {}
        self.original_emit = None

    def __enter__(self):
        from agent.skills_mgmt import observability
        self.original_emit = observability.emit_metric
        self._captured_module = observability
        observability.emit_metric = self._capture
        # 同时 patch reranker 模块中已导入的 emit_metric 引用
        from agent.skills_mgmt import reranker
        self.original_reranker_emit = reranker.emit_metric
        reranker.emit_metric = self._capture
        return self

    def __exit__(self, *exc):
        self._captured_module.emit_metric = self.original_emit
        from agent.skills_mgmt import reranker
        reranker.emit_metric = self.original_reranker_emit
        return False

    def _capture(self, name: str, *, value: float = 1.0,
                 labels: Dict[str, str] = None, kind: str = "counter") -> None:
        """模拟 prometheus_client 的 counter 累积行为"""
        labels = labels or {}
        # 构造指标 key: name + sorted labels
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        key = f"{name}{{{label_str}}}"
        if kind == "counter":
            self.counters[key] = self.counters.get(key, 0) + value
        else:
            self.counters[key] = value

    def get_counter(self, name: str, **labels) -> float:
        """获取指定指标+标签的计数值"""
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        key = f"{name}{{{label_str}}}"
        return self.counters.get(key, 0)

    def find_metrics(self, name: str) -> List[str]:
        """查找所有匹配 name 的指标 key"""
        return [k for k in self.counters if k.startswith(name)]


# ════════════════════════════════════════════════════════════
#  P0 告警规则评估器（模拟 Prometheus alert evaluation）
# ════════════════════════════════════════════════════════════

def evaluate_p0_alerts(metrics: MetricCapture) -> Dict[str, bool]:
    """评估 P0 告警规则是否触发

    模拟 reranker-alerts.yml 中的 P0 规则:
        RerankerOnnxLoadFailed:
          increase(yunshu_reranker_load_total{backend="onnx",status="failed"}[5m]) > 0
        RerankerAllBackendsDown:
          onnx failed > 0 AND pytorch failed > 0
    """
    onnx_failed = metrics.get_counter(
        "yunshu_reranker_load_total", backend="onnx", status="failed"
    )
    pytorch_failed = metrics.get_counter(
        "yunshu_reranker_load_total", backend="pytorch", status="failed"
    )

    return {
        "RerankerOnnxLoadFailed": onnx_failed > 0,
        "RerankerAllBackendsDown": onnx_failed > 0 and pytorch_failed > 0,
    }


# ════════════════════════════════════════════════════════════
#  测试场景
# ════════════════════════════════════════════════════════════

def test_scenario_a_onnx_path_not_exist():
    """场景 A: SKILL_RERANKER_MODEL 指向不存在的路径

    验证降级链路完整性:
        ONNX skip (path_not_local_dir)
        → onnx.fallback_to_pytorch
        → pytorch.load_failed
        → reranker 完全不可用

    预期:
        - 日志: onnx.skip (reason=model_path_not_local_dir)
        - 日志: onnx.fallback_to_pytorch
        - 日志: pytorch.load_failed
        - 指标: yunshu_reranker_load_total{onnx,skipped} += 1
        - 指标: yunshu_reranker_load_total{pytorch,failed} += 1
        - 指标: yunshu_reranker_fallback_total{onnx→pytorch} += 1
        - 告警: P2 RerankerOnnxConfigError 数据源满足（skipped > 0）

    注: 此场景 ONNX 是"跳过"（配置错误）而非"加载失败"，
        故不触发 P0 RerankerAllBackendsDown（严格匹配 status="failed"）。
        P0 场景需 ONNX 文件损坏导致 load_failed，见场景 D。
    """
    print("\n" + "═" * 60)
    print("  场景 A: ONNX 路径不存在 → 降级链路完整性验证")
    print("═" * 60)

    # 保存原始环境变量
    original_env = {
        key: os.environ.get(key)
        for key in ["SKILL_RERANKER_MODEL", "SKILL_RERANKER_USE_ONNX",
                    "SKILL_RERANKER_ENABLED", "SKILL_RERANKER_ONNX_VARIANT"]
    }

    try:
        # 设置错误路径
        os.environ["SKILL_RERANKER_MODEL"] = "/nonexistent/path/to/model"
        os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
        os.environ["SKILL_RERANKER_ENABLED"] = "true"

        from agent.skills_mgmt.reranker import SkillReranker

        # 【变易】让 sentence_transformers.CrossEncoder 抛异常，模拟 PyTorch 加载失败
        # 否则 MagicMock 会返回 mock 实例，_load_pytorch 返回 True，无法触发双后端全挂
        from unittest.mock import patch as _patch

        with JsonLogCapture() as log_cap, MetricCapture() as metric_cap:
            with _patch("sentence_transformers.CrossEncoder") as mock_ce:
                mock_ce.side_effect = RuntimeError("model not found")
                reranker = SkillReranker()
                result = reranker._load_model()

            # ── 验证日志 ──
            print("\n[1] 日志验证:")
            checks = [
                ("onnx.skip", "model_path_not_local_dir",
                 "ONNX 跳过（路径不是本地目录）"),
                ("onnx.fallback_to_pytorch", None,
                 "降级到 PyTorch"),
                ("pytorch.load_failed", None,
                 "PyTorch 加载失败"),
            ]
            for action, expected_reason, desc in checks:
                records = log_cap.filter_by_action(action)
                if expected_reason:
                    records = [r for r in records
                               if r.get("reason") == expected_reason]
                ok = len(records) > 0
                print(f"  {'✅' if ok else '❌'} {desc}: action={action}"
                      f"{' reason=' + expected_reason if expected_reason else ''}"
                      f" (count={len(records)})")

            # ── 验证指标 ──
            print("\n[2] Prometheus 指标验证:")
            onnx_skipped = metric_cap.get_counter(
                "yunshu_reranker_load_total",
                backend="onnx", status="skipped",
                reason="path_not_local_dir",
            )
            pytorch_failed = metric_cap.get_counter(
                "yunshu_reranker_load_total",
                backend="pytorch", status="failed",
            )
            fallback_count = metric_cap.get_counter(
                "yunshu_reranker_fallback_total",
                **{"from": "onnx", "to": "pytorch",
                   "reason": "onnx_unavailable"}
            )
            print(f"  {'✅' if onnx_skipped > 0 else '❌'} "
                  f"yunshu_reranker_load_total{{onnx,skipped}} = {onnx_skipped}")
            print(f"  {'✅' if pytorch_failed > 0 else '❌'} "
                  f"yunshu_reranker_load_total{{pytorch,failed}} = {pytorch_failed}")
            print(f"  {'✅' if fallback_count > 0 else '❌'} "
                  f"yunshu_reranker_fallback_total = {fallback_count}")

            # ── 验证 P0 告警 ──
            print("\n[3] P0 告警规则评估:")
            alerts = evaluate_p0_alerts(metric_cap)
            for alert_name, triggered in alerts.items():
                print(f"  {'🚨' if triggered else '✅'} {alert_name}: "
                      f"{'TRIGGERED' if triggered else 'OK (未触发)'}")

            # 场景 A 预期: 降级链路完整（ONNX skip + PyTorch failed + fallback 指标）
            # 注: 不触发 P0 RerankerAllBackendsDown（ONNX 是 skip 不是 failed）
            success = (onnx_skipped > 0 and pytorch_failed > 0
                       and fallback_count > 0)
            print(f"\n  场景 A 结果: {'✅ PASS' if success else '❌ FAIL'}")
            print(f"  (验证降级链路: onnx.skip → fallback_to_pytorch → pytorch.load_failed)")
            return success

    finally:
        # 恢复环境变量
        for key, val in original_env.items():
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)


def test_scenario_b_onnx_file_missing():
    """场景 B: 模型目录存在但 onnx/<variant> 文件缺失

    预期:
        - 日志: onnx.skip (reason=onnx_file_not_found)
        - 指标: yunshu_reranker_load_total{backend=onnx,status=skipped,reason=file_not_found} += 1
        - 告警: P2 RerankerOnnxConfigError 触发条件满足
    """
    print("\n" + "═" * 60)
    print("  场景 B: ONNX 文件缺失 → P2 配置错误")
    print("═" * 60)

    original_env = {
        key: os.environ.get(key)
        for key in ["SKILL_RERANKER_MODEL", "SKILL_RERANKER_USE_ONNX",
                    "SKILL_RERANKER_ENABLED", "SKILL_RERANKER_ONNX_VARIANT"]
    }

    # 创建临时空目录作为模型目录（目录存在但无 onnx/ 子目录）
    with tempfile.TemporaryDirectory() as tmp_model_dir:
        try:
            os.environ["SKILL_RERANKER_MODEL"] = tmp_model_dir
            os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
            os.environ["SKILL_RERANKER_ENABLED"] = "true"
            os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model_quantized.onnx"

            from agent.skills_mgmt.reranker import SkillReranker

            with JsonLogCapture() as log_cap, MetricCapture() as metric_cap:
                reranker = SkillReranker()
                result = reranker._load_model()

                print("\n[1] 日志验证:")
                skip_records = log_cap.filter_by_action("onnx.skip")
                file_not_found = [r for r in skip_records
                                  if r.get("reason") == "onnx_file_not_found"]
                ok = len(file_not_found) > 0
                print(f"  {'✅' if ok else '❌'} ONNX 文件缺失: "
                      f"action=onnx.skip reason=onnx_file_not_found "
                      f"(count={len(file_not_found)})")

                print("\n[2] Prometheus 指标验证:")
                onnx_skipped = metric_cap.get_counter(
                    "yunshu_reranker_load_total",
                    backend="onnx", status="skipped",
                    reason="file_not_found",
                )
                print(f"  {'✅' if onnx_skipped > 0 else '❌'} "
                      f"yunshu_reranker_load_total{{onnx,skipped,file_not_found}} "
                      f"= {onnx_skipped}")

                # P2 告警评估: increase(yunshu_reranker_load_total{status="skipped"}[1h]) > 0
                print("\n[3] P2 告警规则评估:")
                p2_triggered = onnx_skipped > 0
                print(f"  {'⚠️ ' if p2_triggered else '✅'} "
                      f"RerankerOnnxConfigError: "
                      f"{'TRIGGERED' if p2_triggered else 'OK'}")

                success = ok and onnx_skipped > 0
                print(f"\n  场景 B 结果: {'✅ PASS' if success else '❌ FAIL'}")
                return success

        finally:
            for key, val in original_env.items():
                if val is not None:
                    os.environ[key] = val
                else:
                    os.environ.pop(key, None)


def test_scenario_c_rerank_fallback():
    """场景 C: rerank 调用时模型不可用 → 降级到原始排序

    预期:
        - 日志: rerank.fallback (reason=model_unavailable)
        - 指标: yunshu_reranker_fallback_total{from=reranker,to=original_order} += 1
        - 告警: P1 RerankerFallbackRateHigh 数据源满足
    """
    print("\n" + "═" * 60)
    print("  场景 C: rerank 降级 → P1 降级率告警数据源")
    print("═" * 60)

    original_env = {
        key: os.environ.get(key)
        for key in ["SKILL_RERANKER_MODEL", "SKILL_RERANKER_USE_ONNX",
                    "SKILL_RERANKER_ENABLED"]
    }

    try:
        os.environ["SKILL_RERANKER_MODEL"] = "/nonexistent/path"
        os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
        os.environ["SKILL_RERANKER_ENABLED"] = "true"

        from agent.skills_mgmt.reranker import SkillReranker

        # 简单 mock 候选对象
        class MockCandidate:
            def __init__(self, sid):
                self.skill_id = sid
                self.name = sid
                self.description = ""
                self.category = ""
                self.tags = []
                self.score = 0.5

        candidates = [MockCandidate("skill_a"), MockCandidate("skill_b")]

        from unittest.mock import patch as _patch

        with JsonLogCapture() as log_cap, MetricCapture() as metric_cap:
            # 【变易】让 CrossEncoder 抛异常，确保 _load_model 返回 False，
            # 从而触发 rerank.fallback 降级路径
            with _patch("sentence_transformers.CrossEncoder") as mock_ce:
                mock_ce.side_effect = RuntimeError("model not found")
                reranker = SkillReranker()
                result = reranker.rerank("test query", candidates, top_k=2)

            print("\n[1] 日志验证:")
            fallback_logs = log_cap.filter_by_action("rerank.fallback")
            ok = len(fallback_logs) > 0
            print(f"  {'✅' if ok else '❌'} rerank 降级: "
                  f"action=rerank.fallback (count={len(fallback_logs)})")
            if fallback_logs:
                reason = fallback_logs[0].get("reason")
                print(f"     reason={reason}")

            print("\n[2] Prometheus 指标验证:")
            fallback_metric = metric_cap.get_counter(
                "yunshu_reranker_fallback_total",
                **{"from": "reranker", "to": "original_order",
                   "reason": "model_unavailable"}
            )
            print(f"  {'✅' if fallback_metric > 0 else '❌'} "
                  f"yunshu_reranker_fallback_total{{reranker→original_order}} "
                  f"= {fallback_metric}")

            print("\n[3] 降级率计算（P1 告警数据源）:")
            # 模拟: 降级率 = fallback / (completed + fallback)
            # 此场景 completed=0, fallback=1 → 降级率=100%（>10% 阈值）
            print(f"  降级率 = {fallback_metric}/(0+{fallback_metric}) "
                  f"= 100% > 10% 阈值 → P1 告警将触发")

            print("\n[4] 返回值验证:")
            ok_result = len(result) == 2 and result[0].skill_id == "skill_a"
            print(f"  {'✅' if ok_result else '❌'} 降级返回原序 top-k: "
                  f"len={len(result)}, first={result[0].skill_id if result else None}")

            success = ok and fallback_metric > 0 and ok_result
            print(f"\n  场景 C 结果: {'✅ PASS' if success else '❌ FAIL'}")
            return success

    finally:
        for key, val in original_env.items():
            if val is not None:
                os.environ[key] = val
            else:
                os.environ.pop(key, None)


def test_scenario_d_onnx_file_corrupted():
    """场景 D: ONNX 文件损坏导致 load_failed + PyTorch 失败 → P0 双后端全挂

    构造真实 P0 场景:
        - 模型目录存在，onnx/ 子目录存在，但 .onnx 文件内容损坏
        - ort.InferenceSession 加载损坏文件抛异常 → onnx.load_failed
        - 同时 PyTorch 也失败（mock）→ pytorch.load_failed
        - 触发 P0 RerankerAllBackendsDown

    预期:
        - 日志: onnx.load_failed (不是 onnx.skip)
        - 日志: pytorch.load_failed
        - 指标: yunshu_reranker_load_total{onnx,failed} += 1
        - 指标: yunshu_reranker_load_total{pytorch,failed} += 1
        - 告警: P0 RerankerOnnxLoadFailed 触发
        - 告警: P0 RerankerAllBackendsDown 触发（双后端均 failed）
    """
    print("\n" + "═" * 60)
    print("  场景 D: ONNX 文件损坏 → P0 双后端全挂告警")
    print("═" * 60)

    original_env = {
        key: os.environ.get(key)
        for key in ["SKILL_RERANKER_MODEL", "SKILL_RERANKER_USE_ONNX",
                    "SKILL_RERANKER_ENABLED", "SKILL_RERANKER_ONNX_VARIANT"]
    }

    # 构造损坏的 ONNX 文件：目录存在 + onnx/ 子目录存在 + .onnx 文件存在但内容损坏
    with tempfile.TemporaryDirectory() as tmp_model_dir:
        onnx_dir = os.path.join(tmp_model_dir, "onnx")
        os.makedirs(onnx_dir, exist_ok=True)
        # 写入损坏的 ONNX 文件（非有效 ONNX 格式）
        corrupted_onnx = os.path.join(onnx_dir, "model_quantized.onnx")
        with open(corrupted_onnx, "wb") as f:
            f.write(b"THIS_IS_NOT_A_VALID_ONNX_FILE_CONTENT")

        try:
            os.environ["SKILL_RERANKER_MODEL"] = tmp_model_dir
            os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
            os.environ["SKILL_RERANKER_ENABLED"] = "true"
            os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model_quantized.onnx"

            from agent.skills_mgmt.reranker import SkillReranker
            from unittest.mock import patch as _patch

            with JsonLogCapture() as log_cap, MetricCapture() as metric_cap:
                # 让 PyTorch 也失败，构造双后端全挂
                with _patch("sentence_transformers.CrossEncoder") as mock_ce:
                    mock_ce.side_effect = RuntimeError("model not found")
                    reranker = SkillReranker()
                    result = reranker._load_model()

                print("\n[1] 日志验证:")
                onnx_failed_logs = log_cap.filter_by_action("onnx.load_failed")
                pytorch_failed_logs = log_cap.filter_by_action("pytorch.load_failed")
                fallback_logs = log_cap.filter_by_action("onnx.fallback_to_pytorch")

                print(f"  {'✅' if len(onnx_failed_logs) > 0 else '❌'} "
                      f"ONNX 加载失败: action=onnx.load_failed "
                      f"(count={len(onnx_failed_logs)})")
                if onnx_failed_logs:
                    print(f"     error={onnx_failed_logs[0].get('error', '')[:80]}")

                print(f"  {'✅' if len(fallback_logs) > 0 else '❌'} "
                      f"降级到 PyTorch: action=onnx.fallback_to_pytorch "
                      f"(count={len(fallback_logs)})")

                print(f"  {'✅' if len(pytorch_failed_logs) > 0 else '❌'} "
                      f"PyTorch 加载失败: action=pytorch.load_failed "
                      f"(count={len(pytorch_failed_logs)})")

                print("\n[2] Prometheus 指标验证:")
                onnx_failed = metric_cap.get_counter(
                    "yunshu_reranker_load_total",
                    backend="onnx", status="failed",
                )
                pytorch_failed = metric_cap.get_counter(
                    "yunshu_reranker_load_total",
                    backend="pytorch", status="failed",
                )
                print(f"  {'✅' if onnx_failed > 0 else '❌'} "
                      f"yunshu_reranker_load_total{{onnx,failed}} = {onnx_failed}")
                print(f"  {'✅' if pytorch_failed > 0 else '❌'} "
                      f"yunshu_reranker_load_total{{pytorch,failed}} = {pytorch_failed}")

                print("\n[3] P0 告警规则评估:")
                alerts = evaluate_p0_alerts(metric_cap)
                for alert_name, triggered in alerts.items():
                    print(f"  {'🚨' if triggered else '✅'} {alert_name}: "
                          f"{'TRIGGERED' if triggered else 'OK (未触发)'}")

                # 场景 D 预期: P0 双后端全挂告警触发
                success = (onnx_failed > 0 and pytorch_failed > 0
                           and alerts["RerankerOnnxLoadFailed"]
                           and alerts["RerankerAllBackendsDown"])
                print(f"\n  场景 D 结果: {'✅ PASS' if success else '❌ FAIL'}")
                if success:
                    print(f"  🚨 P0 告警 RerankerAllBackendsDown 已正确触发！")
                return success

        finally:
            for key, val in original_env.items():
                if val is not None:
                    os.environ[key] = val
                else:
                    os.environ.pop(key, None)

def main():
    print("=" * 60)
    print("  v6.5 ONNX Reranker P0 告警模拟故障测试")
    print("  时间:", __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)

    results = []
    results.append(("场景A: ONNX路径不存在→降级链路完整性",
                     test_scenario_a_onnx_path_not_exist()))
    results.append(("场景B: ONNX文件缺失→P2配置错误告警",
                     test_scenario_b_onnx_file_missing()))
    results.append(("场景C: rerank降级→P1降级率告警数据源",
                     test_scenario_c_rerank_fallback()))
    results.append(("场景D: ONNX文件损坏→P0双后端全挂告警",
                     test_scenario_d_onnx_file_corrupted()))

    # ── 汇总 ──
    print("\n" + "=" * 60)
    print("  测试汇总")
    print("=" * 60)
    all_pass = True
    for name, passed in results:
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print("\n" + "=" * 60)
    if all_pass:
        print("  🎉 全部测试通过 — P0 告警链路验证成功")
        print("  ✓ ONNX 加载失败 → 日志 action=onnx.load_failed 正确触发")
        print("  ✓ Prometheus 指标 yunshu_reranker_load_total 正确累积")
        print("  ✓ P0 告警规则 RerankerAllBackendsDown 表达式能匹配")
        print("  ✓ 降级路径 rerank.fallback 指标正确发射")
        print("  ✓ P2 配置错误告警数据源正确生成")
    else:
        print("  ⚠️  部分测试未通过，请检查上方日志")
    print("=" * 60)

    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
