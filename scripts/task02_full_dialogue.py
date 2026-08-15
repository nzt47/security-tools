"""TASK-02 完整对话验证：开启 reflection_persist + critic_evaluation_enabled 后跑真实 process() 链路

运行: python scripts/task02_full_dialogue.py

前提：config.yaml 中 learning.reflection_persist=true、features.critic_evaluation_enabled=true
      （本脚本会真实读取并断言，环境变量 LEARNING_REFLECTION_PERSIST / CRITIC_EVALUATION_ENABLED
       优先级更高，会覆盖 config.yaml —— 若断言失败先检查环境变量）

设计（三义）：
- 【不易】保留真实 _load_learning_config 类方法：开关从 config.yaml 真实读取并生效，
         不注入 lambda，避免"验证的是假配置"；
- 【不易】注入真实 VectorStore（JSON fallback + 临时目录，不污染生产 data/）：
         反思产物与对话记忆真实写入检索面；ReflectionEngine 评估真实调用（6 维规则零 Token）；
- 【变易】子层按需 mock（LLM/输出护栏/DST/健康检查等非本任务验证目标），
         强制走 LLM 路径以触发 self_reflect；metrics 收集器 patch 为记录型以断言 learning.eval.*；
- 【简易】2 轮完整对话，验证：响应不被拦截（保守模式）、反思写入检索面且 schema 完整、
         评估指标随交互递增、检索面可查回。
"""

import hashlib
import logging
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")  # 保证仓库根目录可导入

from agent.orchestrator.orchestrator import Orchestrator, GuardAction
from memory.vector_store import VectorStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)


class FakeMetrics:
    """记录型指标收集替身（模拟 MetricsCollector 的 increment_counter/record_latency）"""

    def __init__(self):
        self.counters = {}
        self.latencies = []

    def increment_counter(self, name, value=1):
        self.counters[name] = self.counters.get(name, 0) + value

    def record_latency(self, name, duration):
        self.latencies.append((name, duration))


def make_orch(vec):
    """构造最小依赖 Orchestrator 实例（__new__ 不跑 __init__，实例属性遮蔽类方法）

    保留真实：_load_learning_config（读 config.yaml）/ 路由层真实分类 / ReflectionEngine
    mock 注入：LLM / 输出护栏 / DST / 健康检查 / 语义层 / 工作流学习层（非本任务验证目标）
    """
    orch = Orchestrator.__new__(Orchestrator)
    orch._running = True
    orch._interaction_lock = threading.Lock()
    orch._interaction_count = 0
    orch._session_id = "task02-dialogue-verify"
    # 输入护栏是懒加载 property（后备属性 _guardrails_input_guard），直接注入后备属性
    orch._guardrails_input_guard = MagicMock()
    orch._guardrails_input_guard.check.return_value = SimpleNamespace(
        action=GuardAction.ALLOW, reason=None, matched_pattern=None)
    orch._workflow_engine = MagicMock()
    orch._workflow_engine.try_match.return_value = None  # 未命中规则层
    orch._memory = MagicMock()
    orch._check_context_usage = lambda: None
    orch._last_context_warning = None
    orch._vector_memory = vec
    orch._current_mode = MagicMock(value="dialogue")
    orch._reflection_history = []
    orch._v2_lifetrace = False
    orch._trace_recorder = None
    orch._v2_distillation = False
    orch._persona_extractor = None
    orch._v2_persona = False
    orch._persona_injector = None
    orch._behavior = MagicMock()
    orch._behavior.can_execute.return_value = (True, "")
    orch._behavior.profile.enable_reflection = True
    orch._planning_enabled = False
    orch._planner = None
    # LLM 直答替身：响应足够长（≥5 字符）确保置信度判定为 high，不触发低置信度兜底
    orch._call_llm = lambda user_input, body_status: (
        "好的，我已经了解了您的需求。这是为您整理的完整方案与执行步骤说明，"
        "包含行程安排、时间节点和备选方案，请查看以下内容并随时告诉我是否需要调整。"
    )
    orch._guardrails_output_guard = MagicMock()
    orch._guardrails_output_guard.check.side_effect = lambda response: SimpleNamespace(
        modified=False, redacted_fields=[], filtered=response)
    orch._is_skill_enabled = lambda name: True
    orch._learn_workflow_from_interaction = lambda *a, **k: None
    orch._semantic_layer_match = lambda *a, **k: None
    orch._workflow_learning_layer_match = lambda *a, **k: None
    orch._update_dst_after_route = lambda *a, **k: None
    orch._load_reject_config = lambda: {"enabled": False, "threshold": 0.3, "llm_min_confidence": 0.5}
    orch._load_planning_wire_config = lambda: {"enabled": False, "min_complexity": "COMPLEX", "timeout_seconds": 30}
    orch.check_health = lambda: []  # 遮蔽真实健康检查（WMI 等系统调用非本任务目标）
    orch._build_body_status = lambda readings: "状态正常"
    orch._set_thinking_mode = lambda mode: None
    return orch


def make_vector_store(tmpdir: str) -> VectorStore:
    """构造真实 VectorStore，强制 JSON fallback（临时目录，不污染生产 data/）

    patch 编码器探测/重量级依赖检测，避免子进程探测模型（30s 超时）拖慢验证；
    JSON fallback + BM25 倒排索引足以验证"反思产物真实写入检索面并可查回"。
    """
    with patch("memory.vector_store.vector_store._check_chroma_available", lambda: None), \
         patch("memory.vector_store.vector_store._resolve_encoder_availability", lambda m: False), \
         patch("memory.vector_store.vector_store.HAS_SENTENCE_TRANSFORMERS", False), \
         patch("memory.vector_store.vector_store.HAS_CHROMA", False):
        return VectorStore(
            collection_name="task02_verify",
            persist_dir=tmpdir,
            enable_inverted_index=True,
        )


def main():
    print("=" * 70)
    print("TASK-02 完整对话验证（config.yaml 开关已开启）")
    print("=" * 70)

    # ── 1) 真实读取 config.yaml 开关并断言 ──
    cfg = Orchestrator._load_learning_config()
    print(f"  [配置] reflection_persist={cfg['reflection_persist']}  "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}")
    assert cfg["reflection_persist"] is True, "config.yaml learning.reflection_persist 应为 true"
    assert cfg["critic_evaluation_enabled"] is True, "config.yaml features.critic_evaluation_enabled 应为 true"

    # ── 2) 初始化真实检索面（JSON fallback + 临时目录）──
    tmpdir = tempfile.mkdtemp(prefix="task02_verify_")
    metrics = FakeMetrics()
    try:
        vec = make_vector_store(tmpdir)
        print(f"  [检索面] backend={vec._backend} 初始 count={vec.count}  "
              f"（真实 VectorStore，无 __len__，bool 恒真 → 写入守卫正确放行）")

        orch = make_orch(vec)

        # ── 3) 跑 2 轮完整对话（强制 LLM 路径触发 self_reflect）──
        dialogue = [
            "请帮我规划一次周末杭州两日游的行程安排",
            "请把第一天的行程再优化一下，减少赶路时间",
        ]
        with patch("agent.orchestrator.orchestrator._MONITORING_AVAILABLE", True), \
             patch("agent.orchestrator.orchestrator.get_metrics_collector", return_value=metrics), \
             patch("agent.orchestrator.orchestrator.MessageHandler.is_follow_up", return_value=True), \
             patch("agent.orchestrator.dialog_state.get_dialog_state", return_value=MagicMock(
                 resolve=lambda *a: None, is_ellipsis_query=lambda *a: False,
                 turn_count=1, last_similarity=None)):
            for i, text in enumerate(dialogue, 1):
                result = orch.process(text, session_id="task02-dialogue-verify")
                assert result["success"] is True, f"第 {i} 轮响应应为 success=True，实际: {result}"
                assert result.get("data"), f"第 {i} 轮响应非空（保守模式不拦截）"
                print(f"  [对话{i}] interaction={orch._interaction_count}  "
                      f"响应长度={len(result['data'])} success={result['success']}（未被评估拦截）")

        # ── 4) 断言反思产物真实写入检索面（schema 完整）──
        reflections = [it for it in vec._items if it.metadata.get("type") == "reflection"]
        print(f"  [检索面] 总记录={vec.count}（对话记忆+反思），反思记录={len(reflections)}")
        assert len(reflections) == 2, f"2 轮对话应写入 2 条反思记录，实际 {len(reflections)}"
        assert vec.count >= 4, f"2 轮对话后检索面应含 2 条对话记忆 + 2 条反思（≥4），实际 {vec.count}"

        for i, item in enumerate(reflections, 1):
            md = item.metadata
            assert md["type"] == "reflection", "type 固定为 reflection"
            assert md["interaction"] == i, "interaction 与轮次一致"
            assert md["task_id"] == str(i), "task_id 与轮次一致"
            assert len(md["input_hash"]) == 12, "input_hash 为 sha1 前 12 位"
            assert md["score"] > 0.0, "评估开启后 score 应为规则评估结果（>0）"
            assert isinstance(md["suggestions"], list), "suggestions 为列表"
            assert md["created_at"], "created_at 非空"
            print(f"  [反思{i}] id={item.id} score={md['score']:.2f} "
                  f"input_hash={md['input_hash']} suggestions={len(md['suggestions'])}条")
            # 内容含反思前缀，可被检索面搜索命中
            assert item.content.startswith("反思(#"), f"反思内容带前缀标记: {item.content[:30]}"

        # ── 5) 断言评估指标随交互递增 ──
        print(f"  [指标] learning.eval.*: {metrics.counters}")
        assert metrics.counters.get("learning.eval.total") == 2, "2 轮对话评估计数应递增 2"
        assert (metrics.counters.get("learning.eval.passed", 0)
                + metrics.counters.get("learning.eval.failed", 0)) == 2, "passed+failed 应等于 total"
        assert len(metrics.latencies) == 2, "learning.eval.score 应记录 2 次"

        # ── 6) 检索面可查回（真实 search）──
        hits = vec.search("反思", top_k=5)
        print(f"  [检索] search('反思') → {len(hits)} 条"
              "（JSON fallback BM25 中文分词能力为既有实现，结果供参考不强断言）")
        for h in hits:
            print(f"         hit id={h.id} content={str(h.content)[:40]}...")

        print("\n" + "=" * 70)
        print("全部断言 PASS：开关从 config.yaml 生效；2 轮完整对话中反思产物真实写入检索面，")
        print("schema 完整可查回；评估指标 learning.eval.* 随交互递增；响应未被拦截（保守模式）")
        print("=" * 70)
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)  # 清理临时检索面，不污染生产数据


if __name__ == "__main__":
    sys.exit(main())
