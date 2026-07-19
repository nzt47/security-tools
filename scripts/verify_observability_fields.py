"""可观测性字段端到端本地验证脚本

验证 retrieved_chunks / eval_score 是否真的被记录到日志/span 中。

用法:
    python scripts/verify_observability_fields.py

验证节点:
    1. loader.match()        → retrieved_chunks 出现在日志 + span
    2. service.record_execution() → eval_score 出现在日志 + span
    3. service.health()      → stats.observability 含新字段元信息
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import uuid
from io import StringIO
from typing import Any, Dict, List

# 确保项目根在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


# ════════════════════════════════════════════════════════════
#  日志捕获器：拦截 agent.skills_mgmt + agent.monitoring.tracing 的日志
# ════════════════════════════════════════════════════════════

class LogCapture:
    """捕获指定 logger 的日志记录，供断言检索"""

    def __init__(self, *logger_names: str):
        self.records: List[logging.LogRecord] = []
        self._handlers: Dict[str, logging.Handler] = {}
        self._logger_names = logger_names

    def __enter__(self):
        for name in self._logger_names:
            lg = logging.getLogger(name)
            handler = logging.Handler()
            handler.emit = self._emit  # type: ignore[method-assign]
            lg.addHandler(handler)
            self._handlers[name] = handler
            lg.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *exc):
        for name, handler in self._handlers.items():
            logging.getLogger(name).removeHandler(handler)

    def _emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)

    def messages(self) -> List[str]:
        return [r.getMessage() for r in self.records]

    def find(self, keyword: str) -> List[str]:
        return [m for m in self.messages() if keyword in m]


def _make_skill_data(name: str, **overrides) -> Dict[str, Any]:
    data = {
        "id": name,
        "name": name,
        "description": f"{name} 描述",
        "content": f"# {name}\nprint('hello')\n",
        "content_type": "python",
        "category": "custom",
        "tags": ["demo", name],
        "author": "verify-script",
    }
    data.update(overrides)
    return data


# ════════════════════════════════════════════════════════════
#  验证用例
# ════════════════════════════════════════════════════════════

def verify_retrieved_chunks(svc) -> bool:
    """节点1: loader.match() 的 retrieved_chunks 是否被记录到日志 + span

    使用全局服务（file_store 已有真实技能），查询 "memory" 匹配 memory_summary 等技能。
    """
    print("\n[1/4] 验证 loader.match() retrieved_chunks 上报...")
    # 全局服务的 file_store 已有技能，直接用真实查询
    idx = svc.loader.fs.load_metadata_index()
    print(f"  file_store 含 {len(idx)} 个技能: {list(idx.keys())[:5]}")

    with LogCapture("agent.skills_mgmt", "agent.monitoring.tracing") as cap:
        result = svc.match_skills("memory summary 记忆", top_k=5)

    # 断言1: MatchResult.retrieved_chunks 非空
    chunks = result.retrieved_chunks
    assert isinstance(chunks, list) and len(chunks) > 0, (
        f"MatchResult.retrieved_chunks 应非空 list，实际: {chunks!r}"
    )
    print(f"  ✓ MatchResult.retrieved_chunks 含 {len(chunks)} 项")
    print(f"    首项: {chunks[0]}")

    # 断言2: [Observability] INFO 日志含 retrieved_chunks
    obs_info = cap.find("[Observability] loader.match retrieved_chunks")
    assert obs_info, "应有 [Observability] loader.match retrieved_chunks 日志"
    print(f"  ✓ [Observability] 日志已打印 ({len(obs_info)} 条)")

    # 断言3: span_attributes 日志含 retrieved_chunks（tracing 层持久化）
    span_logs = cap.find("span_attributes")
    assert span_logs, "tracing 应有 span_attributes 日志"
    span_has_chunks = any("retrieved_chunks" in m for m in span_logs)
    assert span_has_chunks, "span_attributes 日志应含 retrieved_chunks 字段"
    print(f"  ✓ span_attributes 日志含 retrieved_chunks ({len(span_logs)} 条)")

    # 打印一条 span 日志样例
    sample = next(m for m in span_logs if "retrieved_chunks" in m)
    print(f"  样例 span 日志: {sample[:200]}...")

    return True


def verify_eval_score(svc) -> bool:
    """节点2: service.record_execution() 的 eval_score 是否被记录到日志 + span

    使用隔离的 svc（create_manual 写 JSON store），record_execution 从 JSON store 读取。
    """
    print("\n[2/4] 验证 service.record_execution() eval_score 上报...")
    svc.create_manual(_make_skill_data("eval-target", content="x\n"))

    eval_score = {
        "task_success": True,
        "instruction_followed": True,
        "hallucination_detected": False,
        "score": 0.92,
    }
    trace_id = f"verify-{uuid.uuid4().hex[:8]}"

    with LogCapture("agent.skills_mgmt", "agent.monitoring.tracing") as cap:
        svc.record_execution(
            "eval-target", success=True, latency_ms=120,
            trace_id=trace_id, eval_score=eval_score,
        )

    # 断言1: eval_score.recorded 结构化日志
    recorded = cap.find("eval_score.recorded")
    assert recorded, "应有 eval_score.recorded 结构化日志"
    has_score = any("0.92" in m for m in recorded)
    assert has_score, "eval_score.recorded 日志应含 score=0.92"
    print(f"  ✓ eval_score.recorded 日志含 score=0.92 ({len(recorded)} 条)")

    # 断言2: [Observability] INFO 日志
    obs_info = cap.find("[Observability] service.record_execution eval_score")
    assert obs_info, "应有 [Observability] eval_score 日志"
    print(f"  ✓ [Observability] 日志已打印 ({len(obs_info)} 条)")

    # 断言3: span_attributes 日志含 eval_score
    span_logs = cap.find("span_attributes")
    span_has_eval = any("eval_score" in m for m in span_logs)
    assert span_has_eval, "span_attributes 日志应含 eval_score 字段"
    print(f"  ✓ span_attributes 日志含 eval_score")

    # 断言4: hallucination counter 场景
    with LogCapture("agent.skills_mgmt", "agent.monitoring.tracing") as cap2:
        svc.record_execution(
            "eval-target", success=False, latency_ms=80,
            trace_id=trace_id,
            eval_score={
                "task_success": False,
                "instruction_followed": False,
                "hallucination_detected": True,
                "score": 0.3,
            },
        )
    hallu_logs = cap2.find("eval_score.recorded")
    assert hallu_logs, "幻觉场景也应有 eval_score.recorded 日志"
    print(f"  ✓ 幻觉场景 eval_score.recorded 已记录 ({len(hallu_logs)} 条)")

    # 断言5: skill metrics 正常更新（不受可观测性影响）
    skill = svc.get("eval-target")
    assert skill.metrics.usage_count == 2, (
        f"usage_count 应为 2，实际 {skill.metrics.usage_count}"
    )
    print(f"  ✓ skill.metrics.usage_count={skill.metrics.usage_count}（主流程正常）")

    return True


def verify_health(svc) -> bool:
    """节点3: health() 返回的 stats 是否含 observability 字段元信息"""
    print("\n[3/4] 验证 health() stats.observability...")
    health = svc.health()

    assert "stats" in health, "health 应含 stats 字段"
    stats = health["stats"]
    assert "observability" in stats, "stats 应含 observability 字段"
    obs = stats["observability"]

    # 字段元信息
    expected_fields = {"retrieved_chunks", "retrieval_precision_at_k",
                       "eval_score", "user_feedback"}
    actual_fields = set(obs.get("fields", []))
    missing = expected_fields - actual_fields
    assert not missing, f"observability.fields 缺失: {missing}"
    print(f"  ✓ fields 含全部 4 个可观测性字段: {sorted(actual_fields)}")

    # 指标元信息
    expected_metrics = {
        "yunshu_skill_retrieval_precision_at_k",
        "yunshu_skill_eval_score",
        "yunshu_skill_hallucination_total",
    }
    actual_metrics = set(obs.get("metrics", []))
    missing_m = expected_metrics - actual_metrics
    assert not missing_m, f"observability.metrics 缺失: {missing_m}"
    print(f"  ✓ metrics 含全部 3 个新指标: {sorted(actual_metrics)}")

    # 截断配置
    assert obs.get("retrieved_chunks_max") == 50, (
        f"retrieved_chunks_max 应为 50，实际 {obs.get('retrieved_chunks_max')}"
    )
    assert obs.get("truncation_enabled") is True
    print(f"  ✓ retrieved_chunks_max=50, truncation_enabled=True")

    print(f"  ✓ span_persistence={obs.get('span_persistence')}")
    print(f"\n  health.stats.observability 完整内容:")
    print(f"  {json.dumps(obs, ensure_ascii=False, indent=2)}")

    return True


def verify_metrics_values(svc) -> bool:
    """节点4: metrics 数值是否正确发射（mock emit_metric 捕获调用参数）

    [简易] 用 unittest.mock.patch 拦截 emit_metric，断言 name/value/labels/kind
    不依赖真实 prometheus 后端（_METRICS_AVAILABLE 可能 False）。
    """
    from unittest.mock import patch

    print("\n[4/4] 验证 metrics 数值发射（mock 捕获）...")
    svc.create_manual(_make_skill_data("metrics-target", content="x\n"))

    # ── 场景1: 正常 eval_score → yunshu_skill_eval_score histogram ──
    with patch("agent.skills_mgmt.observability.emit_metric") as mock_emit:
        svc.record_execution(
            "metrics-target", success=True, latency_ms=100,
            eval_score={
                "task_success": True,
                "instruction_followed": True,
                "hallucination_detected": False,
                "score": 0.88,
            },
        )

    calls = mock_emit.call_args_list
    assert len(calls) >= 1, f"应至少发射 1 个 metric，实际 {len(calls)}"

    # 找到 yunshu_skill_eval_score 调用（首个位置参数为 metric name）
    eval_calls = [c for c in calls if c.args and c.args[0] == "yunshu_skill_eval_score"]
    assert eval_calls, f"应发射 yunshu_skill_eval_score 指标，实际调用: {[c.args for c in calls if c.args]}"

    eval_call = eval_calls[0]
    assert eval_call.kwargs.get("value") == 0.88, (
        f"eval_score value 应为 0.88，实际 {eval_call.kwargs.get('value')}"
    )
    assert eval_call.kwargs.get("kind") == "histogram", (
        f"kind 应为 histogram，实际 {eval_call.kwargs.get('kind')}"
    )
    labels = eval_call.kwargs.get("labels", {})
    assert labels.get("skill_id") == "metrics-target", (
        f"labels.skill_id 应为 metrics-target，实际 {labels}"
    )
    assert labels.get("task_success") == "true", (
        f"labels.task_success 应为 'true'，实际 {labels}"
    )
    print(f"  ✓ yunshu_skill_eval_score histogram value=0.88 labels={labels}")

    # ── 场景2: 幻觉场景 → yunshu_skill_hallucination_total counter ──
    with patch("agent.skills_mgmt.observability.emit_metric") as mock_emit2:
        svc.record_execution(
            "metrics-target", success=False, latency_ms=50,
            eval_score={
                "task_success": False,
                "instruction_followed": False,
                "hallucination_detected": True,
                "score": 0.2,
            },
        )

    calls2 = mock_emit2.call_args_list
    hallu_calls = [c for c in calls2 if c.args and c.args[0] == "yunshu_skill_hallucination_total"]
    assert hallu_calls, (
        f"幻觉场景应发射 yunshu_skill_hallucination_total counter，"
        f"实际调用: {[c.args for c in calls2 if c.args]}"
    )

    hallu_call = hallu_calls[0]
    assert hallu_call.kwargs.get("value") == 1, (
        f"hallucination value 应为 1，实际 {hallu_call.kwargs.get('value')}"
    )
    assert hallu_call.kwargs.get("kind") == "counter", (
        f"kind 应为 counter，实际 {hallu_call.kwargs.get('kind')}"
    )
    hallu_labels = hallu_call.kwargs.get("labels", {})
    assert hallu_labels.get("skill_id") == "metrics-target"
    print(f"  ✓ yunshu_skill_hallucination_total counter value=1 labels={hallu_labels}")

    # ── 场景3: 无 eval_score → 不发射 eval_score metric（向后兼容）──
    with patch("agent.skills_mgmt.observability.emit_metric") as mock_emit3:
        svc.record_execution("metrics-target", success=True, latency_ms=30)

    calls3 = mock_emit3.call_args_list
    eval_calls3 = [c for c in calls3 if c.args and c.args[0] == "yunshu_skill_eval_score"]
    assert not eval_calls3, "无 eval_score 时不应发射 yunshu_skill_eval_score"
    print(f"  ✓ 无 eval_score 时不发射 eval_score metric（向后兼容）")

    # ── 场景4: score 缺失时默认 0.0（防御性）──
    with patch("agent.skills_mgmt.observability.emit_metric") as mock_emit4:
        svc.record_execution(
            "metrics-target", success=True, latency_ms=10,
            eval_score={"task_success": True},  # 无 score 字段
        )
    calls4 = mock_emit4.call_args_list
    eval_calls4 = [c for c in calls4 if c.args and c.args[0] == "yunshu_skill_eval_score"]
    assert eval_calls4, "eval_score 无 score 字段也应发射 metric（默认 0.0）"
    assert eval_calls4[0].kwargs.get("value") == 0.0, (
        f"score 缺失时应默认 0.0，实际 {eval_calls4[0].kwargs.get('value')}"
    )
    print(f"  ✓ score 缺失时默认 value=0.0（防御性）")

    return True


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

def main():
    print("=" * 60)
    print("  可观测性字段端到端本地验证")
    print("  retrieved_chunks / eval_score / health / metrics")
    print("=" * 60)

    from agent.state_manager import get_skills_mgmt_service
    from agent.skills_mgmt import SkillsMgmtService

    # 全局服务：file_store 已有真实技能，用于 match + health 验证（只读）
    global_svc = get_skills_mgmt_service()
    print(f"\n全局服务: file_store 含 {len(global_svc.loader.fs.load_metadata_index())} 个技能")

    # 隔离服务：用于 eval_score + metrics 验证（create_manual 写临时 JSON store，不污染全局）
    tmpdir = tempfile.mkdtemp(prefix="verify_obs_")
    store_path = os.path.join(tmpdir, "skills_mgmt.json")
    isolated_svc = SkillsMgmtService(store_path=store_path)
    print(f"隔离服务: {store_path}")

    results = []
    try:
        # 节点1: 全局服务 match（file_store 有真实技能）
        results.append(("retrieved_chunks", verify_retrieved_chunks(global_svc)))
        # 节点2: 隔离服务 record_execution（不污染全局 store）
        results.append(("eval_score", verify_eval_score(isolated_svc)))
        # 节点3: 全局服务 health
        results.append(("health", verify_health(global_svc)))
        # 节点4: 隔离服务 metrics 数值断言（mock 捕获 emit_metric）
        results.append(("metrics_values", verify_metrics_values(isolated_svc)))
    except AssertionError as e:
        print(f"\n✗ 验证失败: {e}")
        raise
    except Exception as e:
        print(f"\n✗ 异常: {e}")
        raise

    print("\n" + "=" * 60)
    print("  验证结果汇总")
    print("=" * 60)
    for name, ok in results:
        print(f"  [{'✓' if ok else '✗'}] {name}")
    if all(ok for _, ok in results):
        print("\n  全部通过！retrieved_chunks / eval_score / metrics 已正确记录到日志 + span")
    else:
        print("\n  存在失败项，请排查上述日志")
        sys.exit(1)


if __name__ == "__main__":
    main()
