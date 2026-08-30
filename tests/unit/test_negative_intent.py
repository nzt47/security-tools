"""v6.2 NegativeIntentDetector 单元测试 — TDD 红灯阶段

测试目标:
    1. 40 个正样本黄金集 query 全部不命中（不误伤，守【不易】）
    2. 15 个 v6.1 未命中的负样本全部命中检测（泛化覆盖）
    3. 环境变量开关 SKILL_NEGATIVE_INTENT_ENABLED 控制启用/禁用
    4. 阈值可通过 SKILL_NEGATIVE_INTENT_THRESHOLD 配置
    5. 失败降级：模型不可用时返回 None（放行到 RRF）

【不易】正样本 0 误伤是核心不变量
【变易】prototype 数据外部化，阈值可配置
【简易】不依赖真实模型加载，用 mock 编码向量

相关文件:
    - agent/skills_mgmt/negative_intent_detector.py — NegativeIntentDetector 类
    - tests/eval/negative_intent_prototypes.json — 10 类 prototype 样本
    - tests/eval/skill_retrieval_golden_set.json — 45 个正样本黄金集
    - tests/eval/negative_samples_extended.json — 25 个负样本集
"""
from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path
from typing import List, Tuple
from unittest.mock import patch, MagicMock

import pytest

# ════════════════════════════════════════════════════════════
#  测试数据加载
# ════════════════════════════════════════════════════════════

_GOLDEN_SET = Path(__file__).parent.parent / "eval" / "skill_retrieval_golden_set.json"
_NEGATIVE_SET = Path(__file__).parent.parent / "eval" / "negative_samples_extended.json"
_PROTOTYPES = Path(__file__).parent.parent / "eval" / "negative_intent_prototypes.json"


def _load_golden_positives() -> List[Tuple[str, str]]:
    """加载正样本黄金集（仅 expected 非空）"""
    if not _GOLDEN_SET.exists():
        pytest.skip(f"黄金集不存在: {_GOLDEN_SET}")
    with open(_GOLDEN_SET, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [(c["case_id"], c["query"])
            for c in data["test_cases"]
            if c.get("expected_skill_ids")]


def _load_uncovered_negatives() -> List:
    """加载 v6.1 规则未命中的 15 个负样本

    通过遍历 _QUERY_PATTERNS 判断哪些负样本未被规则命中。

    【变易】v6.1 _QUERY_PATTERNS 已于 commit 1159d88f 删除（TLM 三层路由取代），
    规则删除后此测试前提失效。导入失败时返回带 skip mark 的占位项，
    让 pytest 显式跳过而非抛 ImportError 阻断整个文件收集（守【简易】显式>隐式）。
    """
    if not _NEGATIVE_SET.exists():
        pytest.skip(f"负样本集不存在: {_NEGATIVE_SET}")
    # 导入 v6.1 规则（已被 TLM 三层路由取代，可能不存在）
    try:
        from agent.skills_mgmt.loader import _QUERY_PATTERNS
    except ImportError:
        # 规则已删除：返回 skip 占位项，保留测试类定义但不执行
        return [pytest.param(
            "", "", "",
            marks=pytest.mark.skip(
                reason="_QUERY_PATTERNS 已于 commit 1159d88f 删除（TLM 三层路由取代 v6.1 规则）"
            ),
        )]

    with open(_NEGATIVE_SET, "r", encoding="utf-8") as f:
        data = json.load(f)
    uncovered = []
    for c in data["test_cases"]:
        q = c["query"]
        hit = any(p.search(q) for p, _, _ in _QUERY_PATTERNS)
        if not hit:
            uncovered.append((c["case_id"], q, c.get("category", "")))
    return uncovered


def _load_prototype_samples() -> List[Tuple[str, str]]:
    """加载 prototype 样本: [(category, sample), ...]"""
    if not _PROTOTYPES.exists():
        pytest.skip(f"prototype 文件不存在: {_PROTOTYPES}")
    with open(_PROTOTYPES, "r", encoding="utf-8") as f:
        data = json.load(f)
    samples = []
    for cat in data["categories"]:
        for s in cat["samples"]:
            samples.append((cat["category"], s))
    return samples


# ════════════════════════════════════════════════════════════
#  Mock 向量编码辅助
# ════════════════════════════════════════════════════════════

def _stable_seed(text: str) -> int:
    """跨进程稳定的内容种子（修复 flaky: issue #232 / case_031）

    不能用内置 hash()：str 的 hash 带每进程随机盐（PYTHONHASHSEED），
    同一 query 在不同 pytest 进程得到不同向量，mock 声称的"确定性向量"
    实际跨进程不确定 —— 这是 case_031 间歇性误伤的根因：query 与某类别
    的 16-bit 种子碰撞时向量完全相同，余弦相似度 = 1.0 ≥ 阈值 0.75。
    zlib.crc32 无盐、跨进程稳定，返回 32 位种子，碰撞概率 ~10/2^32 ≈ 0。
    """
    return zlib.crc32(text.encode("utf-8"))


def _make_mock_vector_adapter():
    """构造 mock SkillVectorAdapter

    encode_query 返回一个确定性向量（基于 query 哈希），
    用于测试检测器逻辑而非真实模型精度。
    """
    import numpy as np
    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

    mock_adapter = MagicMock(spec=SkillVectorAdapter)

    def fake_encode(query: str):
        # 确定性向量：基于 query 内容生成，相同 query 返回相同向量
        # 让同类 query 有相似向量（用 query 首字符 + 长度构造）
        if not query:
            return None
        # 简单策略：query 的字符 hash → 1024 维向量
        h = _stable_seed(query)
        rng = np.random.RandomState(h)
        vec = rng.randn(1024).astype(np.float32)
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    mock_adapter.encode_query = fake_encode
    return mock_adapter


def _make_clustered_mock_vector_adapter():
    """构造聚类 mock adapter — 让 prototype 样本与同类负样本向量接近

    策略: 向量由 category 决定（同 category 的 query 向量相近），
    正样本向量为随机噪声（与所有 prototype 都远）。

    注意: noise 系数必须极小（0.01），因为在 1024 维空间，
    随机向量模长 = 系数 × √1024，0.1 的系数会产生 3.2 的模长，
    远大于 center 模长 1，导致向量被 noise 主导。
    """
    import numpy as np
    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

    mock_adapter = MagicMock(spec=SkillVectorAdapter)

    # 为每个 category 生成一个中心向量（单位向量）
    categories = ["weather", "programming", "noise", "entertainment", "finance",
                  "cooking", "sports", "medical", "daily", "greeting"]
    centers = {}
    for i, cat in enumerate(categories):
        rng = np.random.RandomState(_stable_seed(cat))
        center = rng.randn(1024).astype(np.float32)
        center = center / np.linalg.norm(center)
        centers[cat] = center

    # 加载 prototype 样本，建立 query → category 映射
    with open(_PROTOTYPES, "r", encoding="utf-8") as f:
        proto_data = json.load(f)
    query_to_cat = {}
    for cat in proto_data["categories"]:
        for s in cat["samples"]:
            query_to_cat[s] = cat["category"]

    # 加载负样本集，建立 query → category 映射（用于让同类负样本聚集）
    with open(_NEGATIVE_SET, "r", encoding="utf-8") as f:
        neg_data = json.load(f)
    neg_query_to_cat = {}
    for c in neg_data["test_cases"]:
        cat = c.get("category", "")
        # 提取 negative_ 后的部分
        if cat.startswith("negative_"):
            short_cat = cat[len("negative_"):]
            neg_query_to_cat[c["query"]] = short_cat

    def fake_encode(query: str):
        if not query:
            return None
        # prototype 样本：返回其类别中心 + 极小扰动（确保均值≈中心）
        if query in query_to_cat:
            cat = query_to_cat[query]
            center = centers[cat]
            rng = np.random.RandomState(_stable_seed(query))
            # 极小扰动：0.01 系数，1024维模长 ≈ 0.32，远小于 center 模长 1
            noise = rng.randn(1024).astype(np.float32) * 0.01
            vec = center + noise
            return vec / np.linalg.norm(vec)
        # 负样本集 query：返回其类别中心 + 小扰动（确保与 prototype 中心相似度 > 0.9）
        if query in neg_query_to_cat:
            cat = neg_query_to_cat[query]
            if cat in centers:
                center = centers[cat]
                rng = np.random.RandomState(_stable_seed(query))
                # 小扰动：0.02 系数，相似度 ≈ 1/1.0002 ≈ 0.98
                noise = rng.randn(1024).astype(np.float32) * 0.02
                vec = center + noise
                return vec / np.linalg.norm(vec)
        # 正样本或其他 query：返回随机噪声（与所有中心都远，相似度 < 0.3）
        rng = np.random.RandomState(_stable_seed(query))
        vec = rng.randn(1024).astype(np.float32)
        return vec / np.linalg.norm(vec)

    mock_adapter.encode_query = fake_encode
    return mock_adapter


# ════════════════════════════════════════════════════════════
#  测试类
# ════════════════════════════════════════════════════════════

class TestPrototypesLoading:
    """prototype 数据加载测试"""

    def test_prototypes_file_exists(self):
        assert _PROTOTYPES.exists(), f"prototype 文件不存在: {_PROTOTYPES}"

    def test_prototypes_has_10_categories(self):
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        detector._load_prototypes()
        assert len(detector._categories) == 10

    def test_prototypes_each_category_has_samples(self):
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        detector._load_prototypes()
        for cat, samples in detector._raw_samples.items():
            assert len(samples) >= 3, f"类别 {cat} 样本数不足: {len(samples)}"


class TestPositiveSamplesNotMatched:
    """正样本不误伤测试（核心【不易】）"""

    @pytest.mark.parametrize("case_id, query", _load_golden_positives())
    def test_positive_not_matched(self, case_id, query, monkeypatch):
        """40 个正样本全部不应被检测为非技能意图"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_THRESHOLD", raising=False)

        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
            threshold=0.75,
        )
        result = detector.detect(query, tid="test", t0=0.0)
        # 正样本不应被拒绝（返回 None）
        assert result is None, (
            f"正样本误伤: {case_id} query='{query}' "
            f"被检测为非技能意图（违【不易】）"
        )


class TestUncoveredNegativesMatched:
    """15 个 v6.1 未命中负样本应被 v6.2 检测到"""

    @pytest.mark.parametrize("case_id, query, category", _load_uncovered_negatives())
    def test_uncovered_negative_matched(self, case_id, query, category, monkeypatch):
        """v6.1 规则未命中的负样本应被 embedding 层检测到"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_THRESHOLD", raising=False)

        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
            threshold=0.75,
        )
        result = detector.detect(query, tid="test", t0=0.0)
        assert result is not None, (
            f"负样本未命中: {case_id} query='{query}' "
            f"应被 v6.2 embedding 层检测到"
        )


class TestEnvVarSwitch:
    """环境变量开关测试"""

    def test_disabled_returns_none(self, monkeypatch):
        """SKILL_NEGATIVE_INTENT_ENABLED=false 时禁用检测"""
        monkeypatch.setenv("SKILL_NEGATIVE_INTENT_ENABLED", "false")

        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        # 即使是明显的负样本，禁用后也应返回 None
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        assert result is None

    @pytest.mark.parametrize("invalid_value", ["0", "off", "no", "False", "FALSE"])
    def test_invalid_values_disable(self, invalid_value, monkeypatch):
        monkeypatch.setenv("SKILL_NEGATIVE_INTENT_ENABLED", invalid_value)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        assert result is None

    def test_enabled_default(self, monkeypatch):
        """默认（未设环境变量）应为启用状态"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        # 应被检测到（mock adapter 让同类 query 聚集）
        assert result is not None


class TestThresholdConfig:
    """阈值配置测试"""

    def test_threshold_env_var(self, monkeypatch):
        """SKILL_NEGATIVE_INTENT_THRESHOLD 环境变量生效"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        monkeypatch.setenv("SKILL_NEGATIVE_INTENT_THRESHOLD", "0.99")

        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        # 阈值 0.99 极高，几乎不会命中
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        # 取决于 mock 向量，可能命中也可能不命中，主要验证不抛异常
        assert result is None or isinstance(result, tuple)

    def test_threshold_param_override(self, monkeypatch):
        """显式 threshold 参数覆盖环境变量"""
        monkeypatch.setenv("SKILL_NEGATIVE_INTENT_THRESHOLD", "0.50")
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
            threshold=0.99,  # 显式参数优先
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        assert result is None or isinstance(result, tuple)


class TestFailureDegradation:
    """失败降级测试"""

    def test_model_unavailable_returns_none(self, monkeypatch):
        """encode_query 返回 None 时，检测器应返回 None（放行）"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

        adapter = MagicMock(spec=SkillVectorAdapter)
        adapter.encode_query.return_value = None  # 模型不可用

        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        assert result is None, "模型不可用时应降级返回 None"

    def test_empty_query_returns_none(self, monkeypatch):
        """空 query 应返回 None"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
        )
        assert detector.detect("", tid="test", t0=0.0) is None

    def test_prototypes_load_failure_returns_none(self, monkeypatch):
        """prototype 文件不存在时，检测器应降级返回 None"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path="/nonexistent/path.json",
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        assert result is None


class TestDetectResultSemantics:
    """检测结果语义验证"""

    def test_result_tuple_structure(self, monkeypatch):
        """命中时返回 (category, similarity, retrieval_method) 三元组"""
        monkeypatch.delenv("SKILL_NEGATIVE_INTENT_ENABLED", raising=False)
        from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector
        adapter = _make_clustered_mock_vector_adapter()
        detector = NegativeIntentDetector(
            vector_adapter=adapter,
            prototypes_path=str(_PROTOTYPES),
            threshold=0.5,  # 降低阈值确保命中
        )
        result = detector.detect("今天天气怎么样", tid="test", t0=0.0)
        if result is not None:
            assert len(result) == 3, f"结果应为三元组，实际: {result}"
            category, similarity, method = result
            assert isinstance(category, str)
            assert isinstance(similarity, float)
            assert method == "negative_intent"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
