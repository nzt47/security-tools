"""TASK-02 本地模拟：验证反思产物是否真实写入向量检索面

运行: python scripts/task02_reflection_simulate.py

设计：
- 用 Orchestrator.__new__ + 注入最小依赖（同 tests/unit/test_reflection_pipeline.py 的
  _make_orch 模式），只驱动 self_reflect() 及 TASK-02 接线点，不拉起完整 process 链路；
- FakeVectorStore 提供 add/search，验证"反思产物可写入并可查回"；
- 三个场景覆盖：默认（零写入）/ 反思持久化 / 反思持久化 + 规则评估。

输出：各场景的接线日志（logger.info）与检索面记录明细，便于人工核验。
"""

import hashlib
import logging
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")  # 保证仓库根目录可导入

from agent.orchestrator.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


class FakeVectorStore:
    """轻量检索面替身：记录 add 调用并支持按内容查回（模拟向量检索面存储语义）"""

    def __init__(self):
        self._items = []
        self._seq = 0

    def add(self, content: str, metadata=None) -> str:
        self._seq += 1
        item_id = f"item_{self._seq:03d}"
        self._items.append({"id": item_id, "content": content, "metadata": metadata or {}})
        return item_id

    def search(self, query: str, top_k: int = 5):
        """简单子串匹配查询（模拟检索面可查得）"""
        hits = [it for it in self._items if query in it["content"]]
        return hits[:top_k]

    @property
    def count(self) -> int:
        return len(self._items)


class FakeMetrics:
    """评估指标收集替身（模拟 MetricsCollector 的 increment_counter/record_latency）"""

    def __init__(self):
        self.counters = {}
        self.latencies = []

    def increment_counter(self, name, value=1):
        self.counters[name] = self.counters.get(name, 0) + value

    def record_latency(self, name, duration):
        self.latencies.append((name, duration))


def make_orch(learning_cfg, vector_memory):
    behavior = MagicMock()
    behavior.can_execute.return_value = (True, "")
    behavior.profile.enable_reflection = True

    orch = Orchestrator.__new__(Orchestrator)
    setattr(orch, "_interaction_count", 1)
    setattr(orch, "_current_mode", MagicMock(value="simulate"))
    setattr(orch, "_memory", MagicMock())
    setattr(orch, "_reflection_history", [])
    setattr(orch, "_v2_lifetrace", False)
    setattr(orch, "_trace_recorder", None)
    setattr(orch, "_vector_memory", vector_memory)
    setattr(orch, "_load_learning_config", lambda: learning_cfg)
    return orch


def scenario_a_default_no_write():
    """场景 A：默认状态（两开关 false）→ 反思照常产出，检索面零写入（与现状一致）"""
    print("\n── 场景 A：默认状态（reflection_persist=false / critic_evaluation_enabled=false）")
    vec = FakeVectorStore()
    orch = make_orch({"reflection_persist": False, "critic_evaluation_enabled": False}, vec)
    entry = orch.self_reflect("帮我写一份项目报告", "好的，以下是报告正文……")
    assert vec.count == 0, "默认状态不应写检索面"
    print(f"[场景A] PASS: 反思产出 interaction={entry['interaction']}，检索面写入数={vec.count}（期望 0，行为与现状一致）")


def scenario_b_reflection_persist():
    """场景 B：reflection_persist=true → 反思写入检索面且 schema 完整、可查回"""
    print("\n── 场景 B：reflection_persist=true（仅反思持久化）")
    vec = FakeVectorStore()
    orch = make_orch({"reflection_persist": True, "critic_evaluation_enabled": False}, vec)
    task = "帮我写一份项目报告"
    entry = orch.self_reflect(task, "好的，以下是报告正文……")
    assert vec.count == 1, "开启后应写入 1 条反思记录"
    item = vec._items[0]
    md = item["metadata"]
    print(f"  检索面记录: id={item['id']}")
    print(f"    content : {item['content']}")
    print(f"    metadata: {md}")
    assert md["type"] == "reflection"
    assert md["task_id"] == "1"
    assert md["input_hash"] == hashlib.sha1(task.encode("utf-8")).hexdigest()[:12], "input_hash 与输入一致"
    assert md["score"] == 0.0, "未启用评估时 score 默认 0.0"
    assert md["suggestions"] == []
    assert md["created_at"] == entry["timestamp"]
    hits = vec.search("反思")
    print(f"  [可查回] search('反思') → {len(hits)} 条")
    assert len(hits) == 1
    print("[场景B] PASS: 反思产物已写入检索面，schema 完整且可查回")


def scenario_c_persist_and_eval():
    """场景 C：两开关均 true → 反思写入 + 规则评估（score 落记录、metrics 递增、不拦截响应）"""
    print("\n── 场景 C：reflection_persist=true + critic_evaluation_enabled=true")
    vec = FakeVectorStore()
    metrics = FakeMetrics()
    orch = make_orch({"reflection_persist": True, "critic_evaluation_enabled": True}, vec)
    task = "帮我写一份项目报告"
    with patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", True), \
         patch("agent.orchestrator.orchestrator.get_metrics_collector", return_value=metrics):
        entry = orch.self_reflect(task, "好的，以下是报告正文……")
    assert vec.count == 1
    md = vec._items[0]["metadata"]
    print(f"  检索面记录 metadata: score={md['score']} suggestions={md['suggestions']}")
    print(f"  评估指标: {metrics.counters}  latencies={metrics.latencies}")
    assert md["score"] > 0.0, "评估开启后 score 应为规则评估结果"
    assert metrics.counters.get("learning.eval.total") == 1, "评估计数应递增 1"
    assert metrics.counters.get("learning.eval.passed") == 1
    assert entry["interaction"] == 1, "保守模式：响应不被评估拦截"
    print("[场景C] PASS: 反思写入 + 规则评估指标递增，响应正常返回（保守模式）")


def main():
    print("=" * 64)
    print("TASK-02 反思产物写入检索面 — 本地模拟验证")
    print("=" * 64)
    scenario_a_default_no_write()
    scenario_b_reflection_persist()
    scenario_c_persist_and_eval()
    print("\n" + "=" * 64)
    print("全部场景 PASS：反思产物接线生效，检索面可写入并查回，评估指标随交互递增")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
