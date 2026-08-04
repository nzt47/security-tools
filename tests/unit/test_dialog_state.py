"""dialog_state DST 模块单元测试

覆盖：
- 省略句/指代句检测（is_ellipsis_query）
- 指代消解（resolve）— 关键词继承 + 意图继承 + 技能继承
- 状态更新（update）+ 会话隔离
- 边界处理（空输入/无历史/非省略句）
"""

import pytest
from agent.orchestrator.dialog_state import (
    DialogState,
    get_dialog_state,
    reset_session_state,
)


class TestEllipsisDetection:
    """省略句/指代句检测"""

    def setup_method(self):
        self.dst = DialogState()

    def test_deixis_that(self):
        assert self.dst.is_ellipsis_query("那个呢")
        assert self.dst.is_ellipsis_query("那个")
        assert self.dst.is_ellipsis_query("那些呢")

    def test_deixis_this(self):
        assert self.dst.is_ellipsis_query("这个")
        assert self.dst.is_ellipsis_query("这些怎么样")

    def test_continuation_then(self):
        assert self.dst.is_ellipsis_query("然后呢")
        assert self.dst.is_ellipsis_query("接着呢")
        assert self.dst.is_ellipsis_query("继续")
        assert self.dst.is_ellipsis_query("再来一个")

    def test_not_ellipsis_long_query(self):
        """长句不判定为省略句"""
        assert not self.dst.is_ellipsis_query("帮我解析这个PDF文件然后转成Word格式")

    def test_not_ellipsis_normal_query(self):
        """正常查询不判定为省略句"""
        assert not self.dst.is_ellipsis_query("你好")
        assert not self.dst.is_ellipsis_query("现在几点")

    def test_empty_input(self):
        assert not self.dst.is_ellipsis_query("")
        assert not self.dst.is_ellipsis_query("   ")


class TestResolve:
    """指代消解测试"""

    def setup_method(self):
        self.dst = DialogState()

    def test_resolve_with_keywords(self):
        """有关键词时用关键词补全"""
        self.dst.update(keywords=["PDF", "转换"])
        result = self.dst.resolve("那个呢")
        assert result is not None
        assert "PDF" in result
        assert "转换" in result

    def test_resolve_with_intent_no_keywords(self):
        """无关键词但有意图时用意图补全"""
        self.dst.update(intent="pdf_convert")
        result = self.dst.resolve("然后呢")
        assert result is not None
        assert "pdf_convert" in result
        assert "继续" in result  # 接续句

    def test_resolve_with_skill_only(self):
        """仅有技能时用技能补全"""
        self.dst.update(skill="pdf_tool_v2")
        result = self.dst.resolve("那个呢")
        assert result is not None
        assert "pdf_tool_v2" in result

    def test_resolve_no_history(self):
        """无历史状态时不补全"""
        result = self.dst.resolve("那个呢")
        assert result is None

    def test_resolve_not_ellipsis(self):
        """非省略句不补全"""
        self.dst.update(keywords=["PDF"])
        result = self.dst.resolve("帮我解析PDF")
        assert result is None

    def test_resolve_continuation_uses_continue_prefix(self):
        """接续句用"继续"前缀"""
        self.dst.update(keywords=["天气", "查询"])
        result = self.dst.resolve("然后呢")
        assert result is not None
        assert result.startswith("继续")

    def test_resolve_deixis_uses_about_prefix(self):
        """指代句用"关于"前缀"""
        self.dst.update(keywords=["PDF"])
        result = self.dst.resolve("那个呢")
        assert result is not None
        assert result.startswith("关于")

    def test_resolve_unknown_intent_not_used(self):
        """unknown 意图不被用于补全"""
        self.dst.update(intent="unknown")
        result = self.dst.resolve("那个呢")
        assert result is None  # 无可用上下文


class TestStateUpdate:
    """状态更新测试"""

    def test_update_increments_turn(self):
        dst = DialogState()
        assert dst.turn_count == 0
        dst.update(intent="test")
        assert dst.turn_count == 1
        dst.update(keywords=["a"])
        assert dst.turn_count == 2

    def test_update_preserves_previous_slots(self):
        """update 只更新传入的字段，不影响其他字段"""
        dst = DialogState()
        dst.update(intent="pdf", keywords=["PDF"])
        dst.update(skill="pdf_tool")  # 只更新 skill
        assert dst.last_intent == "pdf"  # 保留
        assert dst.last_keywords == ["PDF"]  # 保留
        assert dst.last_skill == "pdf_tool"  # 新增

    def test_reset_clears_state(self):
        dst = DialogState()
        dst.update(intent="test", keywords=["a"])
        dst.reset()
        assert dst.turn_count == 0
        assert dst.last_intent is None
        assert dst.last_keywords == []


class TestSessionIsolation:
    """会话级隔离测试"""

    def test_different_sessions_isolated(self):
        reset_session_state("session_a")
        reset_session_state("session_b")
        dst_a = get_dialog_state("session_a")
        dst_b = get_dialog_state("session_b")
        dst_a.update(intent="intent_a")
        assert dst_b.last_intent is None  # b 不受 a 影响

    def test_same_session_returns_same_instance(self):
        reset_session_state("session_c")
        dst1 = get_dialog_state("session_c")
        dst2 = get_dialog_state("session_c")
        assert dst1 is dst2

    def test_reset_session(self):
        dst = get_dialog_state("session_d")
        dst.update(intent="test")
        reset_session_state("session_d")
        dst_after = get_dialog_state("session_d")
        assert dst_after.turn_count == 0


class TestToDict:
    """状态快照导出测试"""

    def test_to_dict_contains_all_slots(self):
        dst = DialogState()
        dst.update(intent="pdf", keywords=["PDF"], skill="pdf_tool")
        snapshot = dst.to_dict()
        assert "turn_count" in snapshot
        assert "last_intent" in snapshot
        assert "last_keywords" in snapshot
        assert "last_skill" in snapshot
        assert snapshot["last_intent"] == "pdf"

    def test_to_dict_contains_last_user_input(self):
        """last_user_input 槽位出现在快照中"""
        dst = DialogState()
        dst.update(user_input="帮我转换PDF")
        snapshot = dst.to_dict()
        assert "last_user_input" in snapshot
        assert snapshot["last_user_input"] == "帮我转换PDF"


# ════════════════════════════════════════════════════════════
#  向量置信度软门控测试（任务 4 新增）
# ════════════════════════════════════════════════════════════
import numpy as np


class _MockVectorAdapter:
    """Mock 向量适配器（鸭子类型，仅实现 encode_query）

    用于隔离测试，避免拉起真实 BGE-m3 模型。
    """
    def __init__(self, vectors):
        # vectors: dict[str, np.ndarray]（已归一化）
        self._vectors = vectors

    def encode_query(self, query):
        return self._vectors.get(query)


class TestVectorConfidence:
    """向量置信度软门控 — augmented vs last_user_input 余弦相似度"""

    def setup_method(self):
        # 同向向量 sim=1.0；正交向量 sim=0.0
        self.v_same = np.array([1.0, 0.0, 0.0])
        self.v_orth = np.array([0.0, 1.0, 0.0])

    def test_resolve_high_similarity_accepted(self):
        """augmented 与 last_user_input 高相似 → 返回 augmented"""
        adapter = _MockVectorAdapter({
            "关于 PDF 呢": self.v_same,
            "帮我转换PDF": self.v_same,
        })
        dst = DialogState(vector_adapter=adapter)
        dst.update(keywords=["PDF"], user_input="帮我转换PDF")
        result = dst.resolve("那个呢")
        assert result is not None
        assert "PDF" in result

    def test_resolve_low_similarity_rejected(self):
        """augmented 与 last_user_input 正交（sim≈0 < 阈值）→ 返回 None"""
        adapter = _MockVectorAdapter({
            "关于 PDF 呢": self.v_same,
            "帮我转换PDF": self.v_orth,
        })
        dst = DialogState(vector_adapter=adapter)
        dst.update(keywords=["PDF"], user_input="帮我转换PDF")
        result = dst.resolve("那个呢")
        assert result is None  # 语义断裂，拒绝补全

    def test_resolve_vector_none_fallback_regex(self):
        """encode_query 返回 None → 回退纯正则返回 augmented"""
        adapter = _MockVectorAdapter({})  # 全部 miss → None
        dst = DialogState(vector_adapter=adapter)
        dst.update(keywords=["PDF"], user_input="帮我转换PDF")
        result = dst.resolve("那个呢")
        assert result is not None
        assert "PDF" in result

    def test_vector_adapter_none_pure_regex(self):
        """不注入 adapter → 纯正则路径，返回 augmented"""
        dst = DialogState()  # vector_adapter=None
        dst.update(keywords=["PDF"], user_input="帮我转换PDF")
        result = dst.resolve("那个呢")
        assert result is not None
        assert "PDF" in result

    def test_update_user_input_stored(self):
        """update(user_input=...) 后 last_user_input 被记录"""
        dst = DialogState()
        dst.update(user_input="帮我转换PDF")
        assert dst.last_user_input == "帮我转换PDF"

    def test_no_last_user_input_skips_gate(self):
        """有 adapter 但无 last_user_input → 跳过门控，返回 augmented"""
        adapter = _MockVectorAdapter({"关于 PDF 呢": self.v_orth})
        dst = DialogState(vector_adapter=adapter)
        # 只 update keywords，不 update user_input
        dst.update(keywords=["PDF"])
        result = dst.resolve("那个呢")
        assert result is not None  # 无法校验，回退纯正则


class TestIsFollowUpDelegation:
    """is_follow_up 委托 DST 检测省略句（接口契约修复验证）"""

    def test_follow_up_delegates_to_dst_ellipsis(self):
        """DST 已有上下文 + text=省略句 → is_follow_up 返回 True"""
        from agent.orchestrator.message_handler import MessageHandler
        reset_session_state("ifup_ellipsis")
        dst = get_dialog_state("ifup_ellipsis")
        dst.update(keywords=["PDF"], user_input="帮我转换PDF")
        assert MessageHandler.is_follow_up({
            "text": "那个呢",
            "session_id": "ifup_ellipsis",
        }) is True

    def test_follow_up_text_missing_returns_false(self):
        """无 text（原 bug：调用方未传 text）→ False，不再恒 False 但语义正确"""
        from agent.orchestrator.message_handler import MessageHandler
        assert MessageHandler.is_follow_up({
            "last_was_template": True,
            "confidence": None,
        }) is False

    def test_follow_up_normal_query_with_session(self):
        """正常查询 + session_id → DST 非省略、正则不命中、非模板短句 → False"""
        from agent.orchestrator.message_handler import MessageHandler
        reset_session_state("ifup_normal")
        get_dialog_state("ifup_normal").update(
            keywords=["PDF"], user_input="帮我转换PDF")
        assert MessageHandler.is_follow_up({
            "text": "今天天气怎么样",
            "session_id": "ifup_normal",
        }) is False

    def test_follow_up_regex_fallback_without_session(self):
        """无 session_id 时正则兜底："为什么"命中 FOLLOW_UP_PATTERNS → True"""
        from agent.orchestrator.message_handler import MessageHandler
        assert MessageHandler.is_follow_up({"text": "为什么会这样"}) is True

    def test_follow_up_template_short_query(self):
        """last_was_template + 短句 → True（保留原追问降级逻辑）"""
        from agent.orchestrator.message_handler import MessageHandler
        assert MessageHandler.is_follow_up({
            "text": "继续",
            "last_was_template": True,
        }) is True
