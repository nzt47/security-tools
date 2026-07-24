"""query 模式识别单元测试 — TDD 红灯阶段

测试目标:
    1. 45 个正样本黄金集 query 全部不命中模式规则（不误伤）
    2. 5 类负样本 query 命中对应模式规则（正确拒绝）
    3. 环境变量开关 SKILL_QUERY_PATTERN_ENABLED 控制启用/禁用
    4. 命中模式时返回空 MatchResult（retrieval_method="query_pattern"）

【不易】正样本 0 误伤是核心不变量
【变易】5 类模式规则各覆盖至少 1 个负样本
【简易】不依赖模型加载，纯正则逻辑测试

相关文件:
    - agent/skills_mgmt/loader.py — _QUERY_PATTERNS 常量 + _match_query_pattern 方法
    - tests/eval/skill_retrieval_golden_set.json — 45 个正样本黄金集
    - tests/eval/negative_samples_extended.json — 25 个负样本集
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch

import pytest

from agent.skills_mgmt.loader import SkillLoader, MatchResult


# ════════════════════════════════════════════════════════════
#  测试数据加载
# ════════════════════════════════════════════════════════════

_GOLDEN_SET = Path(__file__).parent.parent / "eval" / "skill_retrieval_golden_set.json"
_NEGATIVE_SET = Path(__file__).parent.parent / "eval" / "negative_samples_extended.json"


def _load_golden_queries() -> List[Tuple[str, str, List[str]]]:
    """加载正样本黄金集: [(case_id, query, expected_skill_ids), ...]

    【不易】仅返回 expected_skill_ids 非空的正样本（真技能意图）。
    黄金集中的 expected=[] 用例是负样本（应被拒绝），不参与"不误伤"断言。
    """
    if not _GOLDEN_SET.exists():
        pytest.skip(f"黄金集不存在: {_GOLDEN_SET}")
    with open(_GOLDEN_SET, "r", encoding="utf-8") as f:
        data = json.load(f)
    # 仅保留 expected_skill_ids 非空的正样本
    return [(c["case_id"], c["query"], c.get("expected_skill_ids", []))
            for c in data["test_cases"]
            if c.get("expected_skill_ids")]


def _load_negative_queries() -> List[Tuple[str, str, str]]:
    """加载负样本集: [(case_id, query, category), ...]"""
    if not _NEGATIVE_SET.exists():
        pytest.skip(f"负样本集不存在: {_NEGATIVE_SET}")
    with open(_NEGATIVE_SET, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [(c["case_id"], c["query"], c.get("category", ""))
            for c in data["test_cases"]]


# 5 类 0% 拒绝率类别的代表性 query（硬编码，不依赖负样本集文件）
_REPRESENTATIVE_NEGATIVES = [
    # keyword_trap: "X 是什么意思"/"X 概念解释"
    ("safety 是什么意思", "keyword_trap"),
    ("memory 概念解释", "keyword_trap"),
    ("safety 的定义是什么", "keyword_trap"),
    # translation: "帮我翻译"/"请翻译"
    ("请帮我翻译这段话", "translation"),
    ("帮我翻译一下", "translation"),
    # creative: "帮我写诗/歌/故事"
    ("帮我写一首诗", "creative"),
    ("请帮我写个故事", "creative"),
    # math: "帮我算"/数学运算符
    ("帮我算一下 1+1 等于几", "math"),
    ("3 + 5 =", "math"),
    # similar: 系统操作类
    ("帮我删除文件", "similar"),
    ("重启服务器", "similar"),
    ("移动文件夹到D盘", "similar"),
]


# ════════════════════════════════════════════════════════════
#  测试 1: 正样本黄金集 0 误伤（核心不变量）
# ════════════════════════════════════════════════════════════

class TestPositiveSamplesNotMatched:
    """45 个正样本 query 全部不应命中模式规则"""

    @pytest.fixture
    def loader(self):
        """创建 SkillLoader 实例（不加载模型）"""
        # 清除环境变量，确保默认开启
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)
            return SkillLoader()

    @pytest.mark.parametrize("case_id,query,expected", _load_golden_queries())
    def test_golden_query_not_matched(
        self, loader, case_id, query, expected
    ):
        """每个正样本 query 都不应被模式规则拒绝"""
        result = loader._match_query_pattern(
            query, tid="test_tid", t0=0.0
        )
        # 正样本应返回 None（未命中模式，继续走 RRF）
        assert result is None, (
            f"{case_id} 误伤: query='{query}' "
            f"expected={expected} 被模式规则拒绝"
        )


# ════════════════════════════════════════════════════════════
#  测试 2: 5 类负样本命中模式规则
# ════════════════════════════════════════════════════════════

class TestNegativeSamplesMatched:
    """5 类负样本 query 应命中对应模式规则"""

    @pytest.fixture
    def loader(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)
            return SkillLoader()

    @pytest.mark.parametrize("query,expected_category", _REPRESENTATIVE_NEGATIVES)
    def test_negative_query_matched(
        self, loader, query, expected_category
    ):
        """负样本 query 应被模式规则拒绝"""
        result = loader._match_query_pattern(
            query, tid="test_tid", t0=0.0
        )
        # 负样本应返回 MatchResult（命中模式，空结果）
        assert result is not None, (
            f"未命中模式: query='{query}' "
            f"expected_category={expected_category}"
        )
        assert isinstance(result, MatchResult)
        assert len(result.matches) == 0, (
            f"命中模式但 matches 非空: query='{query}'"
        )

    def test_keyword_trap_pattern(self, loader):
        """keyword_trap: 'X 是什么意思'"""
        result = loader._match_query_pattern(
            "safety 是什么意思", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0

    def test_translation_pattern(self, loader):
        """translation: '帮我翻译'"""
        result = loader._match_query_pattern(
            "请帮我翻译这段话", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0

    def test_creative_pattern(self, loader):
        """creative: '帮我写诗'"""
        result = loader._match_query_pattern(
            "帮我写一首诗", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0

    def test_math_pattern(self, loader):
        """math: '帮我算'"""
        result = loader._match_query_pattern(
            "帮我算一下 1+1 等于几", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0

    def test_similar_file_operation_pattern(self, loader):
        """similar: '删除文件'"""
        result = loader._match_query_pattern(
            "帮我删除文件", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0

    def test_similar_system_operation_pattern(self, loader):
        """similar: '重启服务器'"""
        result = loader._match_query_pattern(
            "重启服务器", tid="test", t0=0.0
        )
        assert result is not None
        assert len(result.matches) == 0


# ════════════════════════════════════════════════════════════
#  测试 3: 返回值语义验证
# ════════════════════════════════════════════════════════════

class TestMatchResultSemantics:
    """命中模式时返回的 MatchResult 语义正确"""

    @pytest.fixture
    def loader(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)
            return SkillLoader()

    def test_returns_empty_match_result(self, loader):
        """命中模式时返回空 MatchResult"""
        result = loader._match_query_pattern(
            "safety 是什么意思", tid="test", t0=0.0
        )
        assert result is not None
        assert isinstance(result, MatchResult)
        assert result.matches == []
        assert result.total_scanned == 0
        assert result.estimated_total_tokens == 0
        assert result.fallback_used is False

    def test_retrieval_method_is_query_pattern(self, loader):
        """retrieval_method 应为 'query_pattern'"""
        result = loader._match_query_pattern(
            "帮我写一首诗", tid="test", t0=0.0
        )
        assert result is not None
        assert result.retrieval_method == "query_pattern"

    def test_elapsed_ms_positive(self, loader):
        """elapsed_ms 应为正数"""
        import time
        t0 = time.time()
        result = loader._match_query_pattern(
            "帮我算一下", tid="test", t0=t0
        )
        assert result is not None
        assert result.elapsed_ms >= 0


# ════════════════════════════════════════════════════════════
#  测试 4: 环境变量开关
# ════════════════════════════════════════════════════════════

class TestEnvVarSwitch:
    """SKILL_QUERY_PATTERN_ENABLED 环境变量控制启用/禁用"""

    def test_disabled_returns_none(self):
        """环境变量设为 false 时，模式识别禁用，返回 None"""
        with patch.dict(os.environ, {"SKILL_QUERY_PATTERN_ENABLED": "false"}):
            loader = SkillLoader()
            result = loader._match_query_pattern(
                "safety 是什么意思", tid="test", t0=0.0
            )
            # 禁用时应返回 None（继续走 RRF）
            assert result is None

    def test_disabled_with_0(self):
        """环境变量设为 0 时也禁用"""
        with patch.dict(os.environ, {"SKILL_QUERY_PATTERN_ENABLED": "0"}):
            loader = SkillLoader()
            result = loader._match_query_pattern(
                "帮我写一首诗", tid="test", t0=0.0
            )
            assert result is None

    def test_enabled_true(self):
        """环境变量设为 true 时启用"""
        with patch.dict(os.environ, {"SKILL_QUERY_PATTERN_ENABLED": "true"}):
            loader = SkillLoader()
            result = loader._match_query_pattern(
                "safety 是什么意思", tid="test", t0=0.0
            )
            assert result is not None
            assert len(result.matches) == 0

    def test_enabled_1(self):
        """环境变量设为 1 时启用"""
        with patch.dict(os.environ, {"SKILL_QUERY_PATTERN_ENABLED": "1"}):
            loader = SkillLoader()
            result = loader._match_query_pattern(
                "帮我翻译这段话", tid="test", t0=0.0
            )
            assert result is not None

    def test_default_enabled(self):
        """不设置环境变量时默认启用"""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)
            loader = SkillLoader()
            result = loader._match_query_pattern(
                "重启服务器", tid="test", t0=0.0
            )
            assert result is not None


# ════════════════════════════════════════════════════════════
#  测试 5: 边界情况
# ════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界情况测试"""

    @pytest.fixture
    def loader(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SKILL_QUERY_PATTERN_ENABLED", None)
            return SkillLoader()

    def test_empty_query_returns_none(self, loader):
        """空 query 应返回 None（不命中模式）"""
        result = loader._match_query_pattern("", tid="test", t0=0.0)
        assert result is None

    def test_normal_query_returns_none(self, loader):
        """正常技能查询应返回 None"""
        result = loader._match_query_pattern(
            "请帮我反思刚才的回答", tid="test", t0=0.0
        )
        assert result is None

    def test_skill_id_query_not_matched(self, loader):
        """技能 ID 查询不应命中"""
        result = loader._match_query_pattern(
            "self_reflection", tid="test", t0=0.0
        )
        assert result is None

    def test_partial_keyword_not_matched(self, loader):
        """部分关键词不应误伤：'删除' 单独出现不命中（需 '删除文件'）"""
        # '删除' 单独不匹配 similar 规则（需 '删除文件/目录'）
        result = loader._match_query_pattern(
            "帮我删除脚本中的bug", tid="test", t0=0.0
        )
        # '删除脚本' 不匹配 '删除文件'，应返回 None
        assert result is None

    def test_math_without_operator_not_matched(self, loader):
        """'算' 单独出现但无数学运算时不命中 math 表达式规则"""
        # 注意: '帮我算' 会命中 math 规则，这是预期行为
        # 此测试验证纯数字不命中
        result = loader._match_query_pattern(
            "12345", tid="test", t0=0.0
        )
        assert result is None
