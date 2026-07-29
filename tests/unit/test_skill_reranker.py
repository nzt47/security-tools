"""tests/unit/test_skill_reranker.py — 任务要求验证 SkillReranker 核心契约

与 test_reranker.py 互补，聚焦任务要求的 4 项契约：
    1. test_reranker_unavailable_falls_back — is_available=False 时不影响主流程
    2. test_reranker_reranks_candidates — mock 模型返回固定分数，断言重排正确（含 dict 候选透出）
    3. test_loader_match_uses_reranker_when_available — mock reranker 可用时走精排
    4. test_reranker_timeout_falls_back — mock 超时时降级

设计原则:
    【不易】不依赖真实模型（mock CrossEncoder），不重复 test_reranker.py 已覆盖场景
    【变易】环境变量隔离（autouse fixture 清理 + 恢复 sentence_transformers mock）
    【简易】每个测试单一职责，断言聚焦契约边界
"""
from __future__ import annotations

import os
import sys
import time
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

from agent.skills_mgmt.reranker import SkillReranker, _sigmoid

# 【变易】reranker 输出经 sigmoid 映射到 [0,1] 概率空间（Cross-Encoder 标准）
# 测试断言用 round(_sigmoid(mock_logit), 4) 计算预期值，自描述"mock 返回 logits"
_exp_sigmoid = lambda x: round(_sigmoid(x), 4)


# ════════════════════════════════════════════════════════════
#  测试夹具
# ════════════════════════════════════════════════════════════

# 所有 reranker 相关环境变量（测试前后清理，避免相互污染）
_RERANKER_ENV_KEYS = [
    "SKILL_RERANKER_ENABLED",
    "SKILL_RERANKER_MODEL",
    "SKILL_RERANKER_TIMEOUT",
    "SKILL_RERANKER_RERANK_TIMEOUT",
    "SKILL_RERANKER_MIN_SCORE",
    "SKILL_RERANKER_USE_ONNX",
    "SKILL_RERANKER_ONNX_VARIANT",
]


@pytest.fixture(autouse=True)
def _clean_reranker_env():
    """每个测试前后清理 reranker 环境变量 + 恢复 sentence_transformers mock

    Why: conftest._skills_offline_mode 会把 sentence_transformers 设为 None,
         但本测试需要 patch("sentence_transformers.CrossEncoder"), None 上无法 patch
    """
    original = {k: os.environ.get(k) for k in _RERANKER_ENV_KEYS}
    for k in _RERANKER_ENV_KEYS:
        os.environ.pop(k, None)
    # 恢复为 MagicMock 让 patch 正常工作（不触发真实 C 扩展加载）
    if not sys.modules.get("sentence_transformers"):
        sys.modules["sentence_transformers"] = MagicMock()
    yield
    for k, v in original.items():
        if v is not None:
            os.environ[k] = v


# ════════════════════════════════════════════════════════════
#  1. is_available=False 时不影响主流程
# ════════════════════════════════════════════════════════════

class TestRerankerUnavailableFallsBack:
    """1. is_available=False 时不影响主流程（任务硬要求：模型加载失败时优雅降级）"""

    def test_reranker_unavailable_falls_back(self):
        """模型不可用时 is_available()=False，rerank 返回原序（降级，不抛异常）"""
        reranker = SkillReranker()
        # mock 模型加载失败（CrossEncoder 抛异常）
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("model not found")
            # is_available 触发懒加载，加载失败返回 False
            assert reranker.is_available() is False
            # rerank 应降级返回原序
            candidates = [
                {"skill_id": "a", "name": "A", "description": "desc A"},
                {"skill_id": "b", "name": "B", "description": "desc B"},
            ]
            result = reranker.rerank("test query", candidates, top_k=2)
            # 验证：降级返回原序，数量正确
            assert len(result) == 2
            assert result[0]["skill_id"] == "a"  # 原序首位
            # 验证：降级时不透出 rerank_score（模型未加载，未真正 predict）
            assert "rerank_score" not in result[0]

    def test_reranker_disabled_is_available_false(self):
        """SKILL_RERANKER_ENABLED=false 时 is_available()=False（不触发加载）

        守【不易】：环境开关禁用时不应触发模型加载，避免无谓的下载尝试
        """
        os.environ["SKILL_RERANKER_ENABLED"] = "false"
        reranker = SkillReranker()
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            # is_available 应直接返回 False，不触发加载
            assert reranker.is_available() is False
            # 验证：未尝试加载（CrossEncoder 未被调用）
            mock_ce.assert_not_called()


# ════════════════════════════════════════════════════════════
#  2. mock 模型返回固定分数，断言重排正确（含 dict 候选透出）
# ════════════════════════════════════════════════════════════

class TestRerankerReranksCandidates:
    """2. 重排正确性 + dict 候选透出 rerank_score/original_rank"""

    def test_reranker_reranks_candidates(self):
        """dict 候选重排正确，rerank_score/original_rank 透出到 dict

        场景：候选 [A, B, C]（原始位置 1/2/3），模型分数 [0.3, 0.9, 0.1]
              → 重排后 [B, A, C]，透出 rerank_score + original_rank
        """
        reranker = SkillReranker()
        mock_model = MagicMock()
        # 候选顺序 [A, B, C]，模型分数 [0.3, 0.9, 0.1] → 重排后 [B, A, C]
        mock_model.predict.return_value = [0.3, 0.9, 0.1]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            # is_available 触发加载，返回 True
            assert reranker.is_available() is True
            candidates = [
                {"skill_id": "a", "name": "A", "description": "desc A"},
                {"skill_id": "b", "name": "B", "description": "desc B"},
                {"skill_id": "c", "name": "C", "description": "desc C"},
            ]
            result = reranker.rerank("test query", candidates, top_k=3)
        # 验证重排顺序：[B, A, C]
        assert [c["skill_id"] for c in result] == ["b", "a", "c"]
        # 验证 rerank_score 透出（loader 期望字段）
        # 【变易】sigmoid 后 rerank_score = round(_sigmoid(0.9), 4) = 0.7109
        assert result[0]["rerank_score"] == _exp_sigmoid(0.9)
        assert result[0]["score"] == _exp_sigmoid(0.9)  # score 同步更新
        # 验证 original_rank 透出（候选在原始列表中的位置，1-based）
        # B 在原始列表中是第 2 个 → original_rank=2
        assert result[0]["original_rank"] == 2
        # A 在原始列表中是第 1 个 → original_rank=1
        assert result[1]["original_rank"] == 1
        # C 在原始列表中是第 3 个 → original_rank=3
        assert result[2]["original_rank"] == 3

    def test_reranker_top_k_none_returns_all(self):
        """top_k=None 时返回全部过滤后的候选（loader 用 None 表示外层切片）

        守【不易】：loader.py 调用 reranker.rerank(intent, pool_dicts, top_k=None)
                  reranker 必须返回全部过滤后候选，由 loader 外层切片
        """
        reranker = SkillReranker()
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.8, 0.3]
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            candidates = [
                {"skill_id": "a", "name": "A"},
                {"skill_id": "b", "name": "B"},
                {"skill_id": "c", "name": "C"},
            ]
            result = reranker.rerank("test", candidates, top_k=None)
        # 验证：返回全部 3 个候选（按分数降序）
        assert len(result) == 3
        assert [c["skill_id"] for c in result] == ["b", "a", "c"]


# ════════════════════════════════════════════════════════════
#  3. mock is_available=True 时走精排
# ════════════════════════════════════════════════════════════

class TestLoaderMatchUsesReranker:
    """3. use_reranker=True 时 loader.match 升级 fusion_mode 为 rrf_rerank

    验证 loader↔reranker 集成路径：
    - use_reranker=True + use_vector=True + fusion_mode="rrf"
      → fusion_mode 自动升级为 "rrf_rerank"
    - _try_rrf_match 被调用且 use_reranker=True 传入
    - 返回 retrieval_method == "rrf_rerank"
    """

    def test_loader_match_uses_reranker_when_available(self):
        """use_reranker=True 时，loader.match 调用 _try_rrf_match(use_reranker=True)"""
        from agent.skills_mgmt.loader import SkillLoader, MatchResult, SkillMatch

        loader = SkillLoader()

        # 捕获 _try_rrf_match 调用参数
        captured = {}

        def mock_rrf_match(**kwargs):
            captured.update(kwargs)
            # 模拟 reranker 精排后的结果（含 rerank_score 透出）
            return MatchResult(
                matches=[
                    SkillMatch(
                        skill_id="s1",
                        name="Test Skill",
                        description="desc",
                        score=0.9,
                        estimated_tokens=100,
                        score_breakdown={
                            "rerank_score": 0.9,
                            "original_rank": 1,
                        },
                    )
                ],
                total_scanned=1,
                elapsed_ms=10.0,
                estimated_total_tokens=100,
                retrieval_method="rrf_rerank",
            )

        with patch.object(loader, "_try_rrf_match", side_effect=mock_rrf_match):
            result = loader.match(
                "test query",
                use_vector=True,
                use_reranker=True,
                fusion_mode="rrf",
            )

        # 验证：use_reranker=True 传入 _try_rrf_match（fusion_mode 升级为 rrf_rerank）
        assert captured.get("use_reranker") is True
        # 验证：返回 rrf_rerank 结果
        assert result.retrieval_method == "rrf_rerank"
        # 验证：rerank_score 透出到 score_breakdown（loader↔reranker 契约对齐）
        assert result.matches[0].score_breakdown is not None
        assert result.matches[0].score_breakdown.get("rerank_score") == 0.9


# ════════════════════════════════════════════════════════════
#  4. mock 超时时降级
# ════════════════════════════════════════════════════════════

class TestRerankerTimeoutFallsBack:
    """4. reranker 超时（>rerank_timeout）时降级为原始排序（任务硬要求）"""

    def test_reranker_timeout_falls_back(self):
        """predict 超过 rerank_timeout 时降级返回原序，不阻塞主流程

        场景：rerank_timeout=0.1s，predict 阻塞 1s
              → 0.1s 后超时，返回原序（不应等待 predict 完成）
        """
        # 设置短超时（0.1s），让 predict 阻塞 1s 触发超时
        os.environ["SKILL_RERANKER_RERANK_TIMEOUT"] = "0.1"
        reranker = SkillReranker()  # 重新初始化读取新环境变量
        assert reranker._rerank_timeout == 0.1

        mock_model = MagicMock()

        def slow_predict(pairs):
            time.sleep(1)  # 阻塞 1s，超过 0.1s 超时
            return [0.5] * len(pairs)

        mock_model.predict.side_effect = slow_predict
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = mock_model
            reranker._load_model()
            candidates = [
                {"skill_id": "a", "name": "A"},
                {"skill_id": "b", "name": "B"},
            ]
            t0 = time.time()
            result = reranker.rerank("test", candidates, top_k=2)
            elapsed = time.time() - t0

        # 验证：超时降级，返回原序（不过滤、不重排）
        assert len(result) == 2
        assert result[0]["skill_id"] == "a"  # 原序首位
        # 验证：降级快速返回（不应等待 predict 完成）
        # 超时阈值 0.1s，加上线程池开销，应在 1s 内返回
        assert elapsed < 0.8, f"超时降级应快速返回，实际耗时 {elapsed:.2f}s"
        # 验证：降级时不透出 rerank_score（未真正 predict 成功）
        assert "rerank_score" not in result[0]
