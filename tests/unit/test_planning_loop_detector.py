"""任务 5：状态哈希与决策循环检测单元测试（loop_detector.py）

对应验收标准：
- 标准 1（单元侧）：同一状态哈希窗口内重复 max_repeats=3 次 → LoopSignal(terminate=True)
- 标准 2：状态哈希对"同动作不同参数"敏感（哈希不同）
- 文档步骤 5：相同动作序列哈希稳定；参数值变化敏感；check 达阈值返回 LoopSignal
"""

import pytest
from types import SimpleNamespace

from planning.loop_detector import LoopDetector, LoopSignal
from planning.models.action import Action, ActionType
from planning.models.react import ThoughtResult


def make_thought(action_type: str = "tool_call",
                 tool_name: str = "search",
                 params: dict | None = None) -> ThoughtResult:
    """构造思考结果：默认调用工具 search，参数 {query: "x"}"""
    action = Action.tool_action(tool_name, params if params is not None else {"query": "x"})
    return ThoughtResult(
        reasoning="reasoning",
        action_type=action_type,
        action=action,
    )


# ── 哈希稳定性与敏感性 ─────────────────────────────────────────────────────

class TestStateHash:
    def test_hash_stable_for_same_state(self):
        """同动作+同参数+同上下文 → 指纹稳定（两次调用一致）"""
        d = LoopDetector()
        t = make_thought()
        ctx = {"task": "t1"}
        assert d.state_hash(t, ctx) == d.state_hash(t, ctx)

    def test_hash_sensitive_to_param_value(self):
        """验收标准2：同动作不同参数值 → 指纹不同"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(params={"query": "a"}))
        h2 = d.state_hash(make_thought(params={"query": "b"}))
        assert h1 != h2

    def test_hash_sensitive_to_param_key(self):
        """同动作不同参数 key → 指纹不同"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(params={"query": "a"}))
        h2 = d.state_hash(make_thought(params={"keyword": "a"}))
        assert h1 != h2

    def test_hash_sensitive_to_tool_name(self):
        """不同工具名 → 指纹不同"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(tool_name="search"))
        h2 = d.state_hash(make_thought(tool_name="fetch"))
        assert h1 != h2

    def test_hash_sensitive_to_action_type(self):
        """不同动作类型 → 指纹不同"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(action_type="tool_call"))
        h2 = d.state_hash(make_thought(action_type="response"))
        assert h1 != h2

    def test_hash_sensitive_to_context_value(self):
        """关键上下文值变化 → 指纹不同"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(), {"task": "t1"})
        h2 = d.state_hash(make_thought(), {"task": "t2"})
        assert h1 != h2

    def test_private_context_keys_ignored(self):
        """下划线开头私有上下文键不参与指纹（_hints/_failure_history 等）"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(), {"task": "t1"})
        h2 = d.state_hash(make_thought(), {"task": "t1", "_hints": ["hint"]})
        assert h1 == h2

    def test_none_action_uses_action_type_only(self):
        """action 缺失（纯推理思考）时仅动作类型参与，工具名为空"""
        d = LoopDetector()
        t = ThoughtResult(reasoning="r", action_type="llm_reasoning")
        h1 = d.state_hash(t)
        h2 = d.state_hash(t)
        assert h1 == h2 and len(h1) == 16

    def test_unordered_params_same_hash(self):
        """参数 dict 键顺序不影响指纹（排序摘要）"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(params={"a": 1, "b": 2}))
        h2 = d.state_hash(make_thought(params={"b": 2, "a": 1}))
        assert h1 == h2


# ── check 循环判定 ─────────────────────────────────────────────────────────

class TestLoopDetectorCheck:
    def test_no_signal_below_threshold(self):
        """窗口内未达 max_repeats → 返回 None"""
        d = LoopDetector(max_repeats=3)
        t = make_thought()
        h = d.state_hash(t, {"task": "t1"})
        assert d.check(h) is None
        assert d.check(h) is None

    def test_signal_at_threshold(self):
        """同一状态哈希窗口内重复 3 次 → LoopSignal(terminate=True, occurrences=3)"""
        d = LoopDetector(max_repeats=3)
        t = make_thought(tool_name="search", params={"query": "a"})
        h = d.state_hash(t, {"task": "t1"})
        assert d.check(h) is None
        assert d.check(h) is None
        sig = d.check(h)
        assert isinstance(sig, LoopSignal)
        assert sig.terminate is True
        assert sig.occurrences == 3
        assert sig.repeated_hash == h

    def test_signal_summary_readable(self):
        """summary 含动作/工具/参数描述（人类可读）"""
        d = LoopDetector(max_repeats=3)
        t = make_thought(tool_name="search", params={"query": "a"})
        h = d.state_hash(t, {"task": "t1"})
        for _ in range(2):
            d.check(h)
        sig = d.check(h)
        assert "动作=tool_call" in sig.summary
        assert "工具=search" in sig.summary
        assert "query=a" in sig.summary

    def test_different_states_do_not_count(self):
        """窗口内交替出现两个不同状态且各自 ≤2 次 → 都不触发（互不累计）"""
        d = LoopDetector(max_repeats=3)
        h1 = d.state_hash(make_thought(params={"query": "a"}), {"task": "t1"})
        h2 = d.state_hash(make_thought(params={"query": "b"}), {"task": "t1"})
        assert d.check(h1) is None
        assert d.check(h2) is None
        assert d.check(h1) is None
        assert d.check(h2) is None  # 各 2 次 < 3，均无信号

    def test_window_expires_old_occurrences(self):
        """回溯窗口语义：旧状态滑出窗口后不再计入计数"""
        d = LoopDetector(max_repeats=3, window=4)
        h_a = d.state_hash(make_thought(params={"query": "a"}), {"task": "t1"})
        # 连续 2 次 h_a，然后用 2 个其他状态把它挤出窗口
        assert d.check(h_a) is None
        assert d.check(h_a) is None
        assert d.check(d.state_hash(make_thought(params={"q1": "x"}), {"task": "t1"})) is None
        assert d.check(d.state_hash(make_thought(params={"q2": "y"}), {"task": "t1"})) is None
        # 此时窗口内 h_a 仅剩 1 次，再出现 2 次也不达 3
        assert d.check(h_a) is None
        assert d.check(h_a) is None

    def test_reset_clears_counts(self):
        """reset 清空计数与描述缓存（新任务隔离）"""
        d = LoopDetector(max_repeats=3)
        t = make_thought()
        h = d.state_hash(t, {"task": "t1"})
        d.check(h)
        d.check(h)
        d.reset()
        # 重置后重新计数
        assert d.check(h) is None
        assert d.check(h) is None

    def test_cyclic_ab_oscillation_flagged_after_threshold(self):
        """A/B 交替振荡：A 在窗口内出现达 max_repeats 次后触发信号
        （"同一状态重复≥3次"语义下振荡同样构成循环，与旧 _detect_loop
        的周期振荡检测行为一致——状态哈希是它的替代实现）"""
        d = LoopDetector(max_repeats=3)
        h_a = d.state_hash(make_thought(params={"q": "a"}), {"task": "t1"})
        h_b = d.state_hash(make_thought(params={"q": "b"}), {"task": "t1"})
        assert d.check(h_a) is None
        assert d.check(h_b) is None
        assert d.check(h_a) is None
        assert d.check(h_b) is None
        # 第 3 次 h_a（窗口内 A 达 3 次）→ 触发
        sig = d.check(h_a)
        assert sig is not None and sig.terminate is True and sig.occurrences == 3


# ── 边界情况（极端参数/截断/鸭子类型）─────────────────────────────────────


class TestLoopDetectorEdgeCases:
    """任务5 验收补充：极端边界（window/截断/非标准输入/描述缓存回退）"""

    def test_max_repeats_one_signals_immediately(self):
        """max_repeats=1：首次出现即触发"""
        d = LoopDetector(max_repeats=1)
        h = d.state_hash(make_thought())
        sig = d.check(h)
        assert sig is not None and sig.terminate is True and sig.occurrences == 1

    def test_window_smaller_than_max_repeats_never_signals(self):
        """window < max_repeats：回溯窗口装不下足够样本 → 永不触发（锁定现状）"""
        d = LoopDetector(max_repeats=2, window=1)
        h = d.state_hash(make_thought())
        for _ in range(5):
            assert d.check(h) is None
        # 窗口长度被限制为 window，历史不无界增长
        assert len(d._history) == 1

    def test_window_bounds_history_growth(self):
        """连续不同状态：历史长度被封顶在 window，不随调用次数增长"""
        d = LoopDetector(window=3)
        for i in range(10):
            d.check(d.state_hash(make_thought(params={"q": str(i)})))
        assert len(d._history) == 3

    def test_long_param_value_truncated_stable(self):
        """超过 80 字符的参数值被截断：长值与 80 字符前缀指纹一致"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(params={"q": "x" * 100}))
        h2 = d.state_hash(make_thought(params={"q": "x" * 80}))
        assert h1 == h2

    def test_params_beyond_16_keys_ignored(self):
        """参数超过 16 个 key：排序后仅前 16 参与指纹（第 17 个 key 变化不敏感）"""
        d = LoopDetector()
        a = {f"k{i:02d}": i for i in range(17)}   # k00..k16，k16 被截断丢弃
        b = {f"k{i:02d}": i for i in range(16)}   # k00..k15
        b["z_last"] = 99                          # 排序后 z_last 在第 17 位被丢弃
        assert d.state_hash(make_thought(params=a)) == d.state_hash(make_thought(params=b))

    def test_context_beyond_8_keys_ignored(self):
        """上下文超过 8 个非私有 key：排序后仅前 8 参与指纹"""
        d = LoopDetector()
        ctx_a = {f"c{i}": i for i in range(9)}   # c0..c8，c8 被截断丢弃
        ctx_b = {f"c{i}": i for i in range(8)}   # c0..c7
        ctx_b["zzz"] = 99                        # 排序后 zzz 在第 9 位被丢弃
        assert d.state_hash(make_thought(), ctx_a) == d.state_hash(make_thought(), ctx_b)

    def test_step_index_not_in_fingerprint(self):
        """step_index 不参与指纹（同状态不同步号指纹一致）"""
        d = LoopDetector()
        t = make_thought()
        assert d.state_hash(t, {"task": "t"}, 1) == d.state_hash(t, {"task": "t"}, 999)

    def test_none_and_empty_context_equal(self):
        """context=None 与 context={} 指纹一致"""
        d = LoopDetector()
        t = make_thought()
        assert d.state_hash(t) == d.state_hash(t, {})

    def test_non_string_action_type_coerced(self):
        """非字符串 action_type（int）强制转 str：123 与 "123" 指纹一致（鸭子类型）"""
        d = LoopDetector()
        h1 = d.state_hash(SimpleNamespace(
            action_type=123,
            action=SimpleNamespace(tool_name="t", tool_params={"q": "x"})))
        h2 = d.state_hash(SimpleNamespace(
            action_type="123",
            action=SimpleNamespace(tool_name="t", tool_params={"q": "x"})))
        assert h1 == h2

    def test_non_string_tool_name_ignored(self):
        """非字符串 tool_name（int）视为空：不影响指纹（鸭子类型）"""
        d = LoopDetector()
        h1 = d.state_hash(SimpleNamespace(
            action_type="tool_call",
            action=SimpleNamespace(tool_name=456, tool_params={"q": "x"})))
        h2 = d.state_hash(SimpleNamespace(
            action_type="tool_call",
            action=SimpleNamespace(tool_name="", tool_params={"q": "x"})))
        assert h1 == h2

    def test_none_param_value_hashable(self):
        """参数值为 None 不崩溃且指纹稳定"""
        d = LoopDetector()
        h1 = d.state_hash(make_thought(params={"q": None}))
        h2 = d.state_hash(make_thought(params={"q": None}))
        assert h1 == h2

    def test_summary_falls_back_to_hash_after_reset(self):
        """reset 清空描述缓存后，summary 回退为哈希本身"""
        d = LoopDetector(max_repeats=3)
        h = d.state_hash(make_thought(params={"q": "a"}))  # 登记描述
        d.reset()  # 清空描述缓存
        assert d.check(h) is None
        assert d.check(h) is None
        sig = d.check(h)
        assert sig.terminate is True
        assert sig.summary == h  # 无描述可用 → 回退为哈希
