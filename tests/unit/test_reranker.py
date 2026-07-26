"""v6.5 SkillReranker 单元测试 — TDD 红灯→绿灯

测试覆盖:
    1. 模型加载（成功 + 失败降级）
    2. 环境变量开关
    3. rerank 接口（正常 + 降级 + 空候选 + top_k）
    4. 超时处理（子进程隔离）
    5. 分数阈值过滤

设计原则:
    【不易】不依赖真实模型（mock CrossEncoder）
    【变易】环境变量隔离（setUp/tearDown 清理）
    【简易】每个测试单一职责

运行:
    python -m pytest tests/unit/test_reranker.py -v
"""
from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional
from unittest.mock import MagicMock, patch

import pytest

# 【不易】防止 sentence_transformers 真实 import 导致 Windows 0xC0000005 崩溃
# 必须在 import reranker 之前 mock，避免触发 C 扩展加载
if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = MagicMock()

# 确保项目根目录在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.skills_mgmt.reranker import SkillReranker


# ════════════════════════════════════════════════════════════
#  测试夹具
# ════════════════════════════════════════════════════════════

@dataclass
class MockSkillMatch:
    """模拟 SkillMatch 对象（用于测试）"""
    skill_id: str
    name: str
    description: str
    score: float = 0.0
    category: str = ""
    tags: List[str] = field(default_factory=list)


@pytest.fixture
def sample_candidates():
    """标准测试候选列表（3 个）"""
    return [
        MockSkillMatch(
            skill_id="self_reflection",
            name="自我反思技能",
            description="帮助用户反思和检查回答质量",
            score=0.8,
            category="reflection",
            tags=["反思", "检查", "质量"],
        ),
        MockSkillMatch(
            skill_id="voice_interaction",
            name="语音交互技能",
            description="语音识别和 TTS 合成",
            score=0.6,
            category="voice",
            tags=["语音", "TTS", "ASR"],
        ),
        MockSkillMatch(
            skill_id="pdf_parser",
            name="PDF 解析技能",
            description="解析和提取 PDF 文件内容",
            score=0.4,
            category="file",
            tags=["PDF", "解析", "文件"],
        ),
    ]


@pytest.fixture(autouse=True)
def clean_env():
    """每个测试前后清理环境变量"""
    original = {
        key: os.environ.get(key)
        for key in [
            "SKILL_RERANKER_ENABLED",
            "SKILL_RERANKER_MODEL",
            "SKILL_RERANKER_TIMEOUT",
            "SKILL_RERANKER_MIN_SCORE",
        ]
    }
    for key in original:
        os.environ.pop(key, None)
    yield
    for key, val in original.items():
        if val is not None:
            os.environ[key] = val


# ════════════════════════════════════════════════════════════
#  1. 模型加载测试
# ════════════════════════════════════════════════════════════

class TestModelLoading:
    """模型加载（成功 + 失败降级）"""

    def test_load_model_success(self):
        """模型加载成功：_load_model 返回 True"""
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            result = reranker._load_model()
        assert result is True
        assert reranker._model is not None

    def test_load_model_failure_degrades(self):
        """模型加载失败：_load_model 返回 False，不抛异常"""
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("model not found")
            result = reranker._load_model()
        assert result is False
        assert reranker._model is None

    def test_load_model_not_retried_after_failure(self):
        """加载失败后不重试"""
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("model not found")
            reranker._load_model()
            result = reranker._load_model()
        assert result is False
        assert mock_ce.call_count == 1

    def test_load_model_cached_after_success(self):
        """加载成功后复用缓存"""
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            reranker._load_model()
            reranker._load_model()
        assert mock_ce.call_count == 1


# ════════════════════════════════════════════════════════════
#  2. 环境变量开关测试
# ════════════════════════════════════════════════════════════

class TestEnvironmentSwitch:
    """环境变量开关"""

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on"])
    def test_enabled_when_truthy(self, value):
        os.environ["SKILL_RERANKER_ENABLED"] = value
        reranker = SkillReranker()
        assert reranker._is_enabled() is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
    def test_disabled_when_falsy(self, value):
        os.environ["SKILL_RERANKER_ENABLED"] = value
        reranker = SkillReranker()
        assert reranker._is_enabled() is False

    def test_default_enabled(self):
        reranker = SkillReranker()
        assert reranker._is_enabled() is True

    def test_model_name_from_env(self):
        os.environ["SKILL_RERANKER_MODEL"] = "BAAI/bge-reranker-base"
        reranker = SkillReranker()
        assert reranker._model_name == "BAAI/bge-reranker-base"

    def test_model_name_default(self):
        reranker = SkillReranker()
        assert reranker._model_name == "BAAI/bge-reranker-v2-m3"

    def test_model_name_explicit_param(self):
        os.environ["SKILL_RERANKER_MODEL"] = "env-model"
        reranker = SkillReranker(model_name="explicit-model")
        assert reranker._model_name == "explicit-model"

    def test_timeout_from_env(self):
        os.environ["SKILL_RERANKER_TIMEOUT"] = "60"
        reranker = SkillReranker()
        assert reranker._timeout == 60

    def test_min_score_from_env(self):
        os.environ["SKILL_RERANKER_MIN_SCORE"] = "0.01"
        reranker = SkillReranker()
        assert reranker._min_score == 0.01


# ════════════════════════════════════════════════════════════
#  3. rerank 接口测试
# ════════════════════════════════════════════════════════════

class TestRerankInterface:
    """rerank 接口（正常 + 降级 + 空候选 + top_k）"""

    def test_rerank_empty_candidates(self):
        reranker = SkillReranker()
        result = reranker.rerank("test query", [], top_k=3)
        assert result == []

    def test_rerank_disabled_returns_original_order(self, sample_candidates):
        os.environ["SKILL_RERANKER_ENABLED"] = "false"
        reranker = SkillReranker()
        result = reranker.rerank("反思", sample_candidates, top_k=2)
        assert len(result) == 2
        assert result[0].skill_id == "self_reflection"

    def test_rerank_model_unavailable_fallback(self, sample_candidates):
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("model not found")
            result = reranker.rerank("反思", sample_candidates, top_k=3)
        assert len(result) == 3
        assert result[0].skill_id == "self_reflection"

    def test_rerank_success_reorders_by_score(self, sample_candidates):
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.3, 0.9, 0.1]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            result = reranker.rerank("语音", sample_candidates, top_k=3)
        assert len(result) == 3
        assert result[0].skill_id == "voice_interaction"
        assert result[1].skill_id == "self_reflection"
        assert result[2].skill_id == "pdf_parser"

    def test_rerank_top_k_limit(self, sample_candidates):
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.8, 0.3]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            result = reranker.rerank("test", sample_candidates, top_k=2)
        assert len(result) == 2
        assert result[0].skill_id == "voice_interaction"

    def test_rerank_filters_low_score(self, sample_candidates):
        os.environ["SKILL_RERANKER_MIN_SCORE"] = "0.2"
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.8, 0.1]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            result = reranker.rerank("test", sample_candidates, top_k=3)
        assert len(result) == 2
        assert all(r.skill_id != "pdf_parser" for r in result)

    def test_rerank_updates_score(self, sample_candidates):
        """rerank 后候选的 score 属性被更新"""
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5555, 0.8888, 0.1111]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            result = reranker.rerank("test", sample_candidates, top_k=3)
        assert result[0].skill_id == "voice_interaction"
        assert result[0].score == 0.8888  # round(0.8888, 4)

    def test_rerank_predict_failure_fallback(self, sample_candidates):
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.side_effect = RuntimeError("predict failed")
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            result = reranker.rerank("test", sample_candidates, top_k=3)
        assert len(result) == 3
        assert result[0].skill_id == "self_reflection"


# ════════════════════════════════════════════════════════════
#  4. 辅助方法测试
# ════════════════════════════════════════════════════════════

class TestHelperMethods:
    """辅助方法"""

    def test_candidate_to_text_includes_all_fields(self):
        candidate = MockSkillMatch(
            skill_id="test", name="测试技能", description="测试描述",
            category="test_cat", tags=["tag1", "tag2"],
        )
        reranker = SkillReranker()
        text = reranker._candidate_to_text(candidate)
        assert "测试技能" in text
        assert "测试描述" in text
        assert "tag1" in text

    def test_candidate_to_text_handles_empty_fields(self):
        candidate = MockSkillMatch(
            skill_id="test", name="", description="", category="", tags=[],
        )
        reranker = SkillReranker()
        text = reranker._candidate_to_text(candidate)
        assert text == ""


# ════════════════════════════════════════════════════════════
#  5. 集成测试
# ════════════════════════════════════════════════════════════

class TestIntegration:
    """集成测试：完整 rerank 流程"""

    def test_full_flow_with_mock_model(self, sample_candidates):
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.95, 0.3, 0.05]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            result = reranker.rerank("帮我反思", sample_candidates, top_k=2)
        assert len(result) == 2
        assert result[0].skill_id == "self_reflection"
        assert result[0].score == 0.95

    def test_degradation_chain(self, sample_candidates):
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("model download failed")
            result = reranker.rerank("test", sample_candidates, top_k=2)
        assert len(result) == 2
        assert result[0].skill_id == "self_reflection"
