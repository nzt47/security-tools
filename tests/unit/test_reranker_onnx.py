"""v6.5 SkillReranker ONNX 集成单元测试

测试覆盖:
    1. ONNX 加载（成功 + 失败降级 + 路径校验）
    2. PyTorch 降级路径
    3. _load_model 分发逻辑（ONNX 优先 → PyTorch 降级）
    4. _predict_onnx 推理（mock session + tokenizer）
    5. _predict_with_timeout 分发
    6. rerank 端到端（ONNX 路径）
    7. 环境变量（SKILL_RERANKER_USE_ONNX / ONNX_VARIANT）

设计原则:
    【不易】不依赖真实模型（mock onnxruntime + transformers + sentence_transformers）
    【变易】环境变量隔离（setUp/tearDown 清理）
    【简易】每个测试单一职责，30s 可读

运行:
    python -m pytest tests/unit/test_reranker_onnx.py -v
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import List
from unittest.mock import MagicMock, patch

import pytest

# 【不易】防止真实 import 导致 Windows 0xC0000005 崩溃或网络下载
# 必须在 import reranker 之前 mock，避免触发 C 扩展加载
for _mod_name in ("sentence_transformers", "onnxruntime", "transformers"):
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = MagicMock()

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
    """模拟 SkillMatch 对象（与 test_reranker.py 保持一致）"""
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
        MockSkillMatch(
            skill_id="self_reflection",
            name="自我反思技能",
            description="帮助用户反思和检查回答质量",
            score=0.8,
            category="reflection",
            tags=["反思", "检查", "质量"],
        ),
    ]


@pytest.fixture(autouse=True)
def clean_env():
    """每个测试前后清理 ONNX 相关环境变量 + 恢复 mock 模块"""
    keys = [
        "SKILL_RERANKER_ENABLED",
        "SKILL_RERANKER_MODEL",
        "SKILL_RERANKER_TIMEOUT",
        "SKILL_RERANKER_MIN_SCORE",
        "SKILL_RERANKER_USE_ONNX",
        "SKILL_RERANKER_ONNX_VARIANT",
    ]
    original = {key: os.environ.get(key) for key in keys}
    for key in keys:
        os.environ.pop(key, None)
    # 恢复 mock 模块（conftest 可能将其设为 None）
    for mod_name in ("sentence_transformers", "onnxruntime", "transformers"):
        if not sys.modules.get(mod_name):
            sys.modules[mod_name] = MagicMock()
    yield
    for key, val in original.items():
        if val is not None:
            os.environ[key] = val


# ════════════════════════════════════════════════════════════
#  1. 环境变量解析测试
# ════════════════════════════════════════════════════════════

class TestOnnxEnvParsing:
    """ONNX 环境变量解析"""

    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on"])
    def test_use_onnx_enabled_when_truthy(self, value):
        """truthy 值启用 ONNX"""
        os.environ["SKILL_RERANKER_USE_ONNX"] = value
        r = SkillReranker()
        assert r._use_onnx_env is True

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
    def test_use_onnx_disabled_when_falsy(self, value):
        """falsy 值禁用 ONNX（强制走 PyTorch）"""
        os.environ["SKILL_RERANKER_USE_ONNX"] = value
        r = SkillReranker()
        assert r._use_onnx_env is False

    def test_use_onnx_default_true(self):
        """默认启用 ONNX"""
        r = SkillReranker()
        assert r._use_onnx_env is True

    def test_onnx_variant_from_env(self):
        """从环境变量读取 ONNX 变体文件名"""
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model_int8.onnx"
        r = SkillReranker()
        assert r._onnx_variant == "model_int8.onnx"

    def test_onnx_variant_default_quantized(self):
        """默认变体为 model_quantized.onnx"""
        r = SkillReranker()
        assert r._onnx_variant == "model_quantized.onnx"


# ════════════════════════════════════════════════════════════
#  2. ONNX 加载测试
# ════════════════════════════════════════════════════════════

class TestLoadOnnx:
    """_load_onnx 方法测试"""

    def test_load_onnx_skip_when_model_path_not_dir(self, tmp_path):
        """模型路径非目录时跳过 ONNX（返回 False，不抛异常）"""
        # 给一个文件路径而非目录
        not_dir = tmp_path / "model.txt"
        not_dir.write_text("dummy")
        r = SkillReranker(model_name=str(not_dir))
        assert r._load_onnx() is False
        assert r._use_onnx is False
        assert r._onnx_session is None

    def test_load_onnx_skip_when_onnx_file_not_found(self, tmp_path):
        """ONNX 文件不存在时跳过（返回 False）"""
        # 模型目录存在但 onnx/ 子目录不存在
        r = SkillReranker(model_name=str(tmp_path))
        assert r._load_onnx() is False
        assert r._use_onnx is False

    def test_load_onnx_success(self, tmp_path):
        """ONNX 加载成功：构造 onnx/ 目录 + mock ort + tokenizer"""
        # 构造 onnx/model_quantized.onnx 文件
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy onnx content")

        r = SkillReranker(model_name=str(tmp_path))

        # mock onnxruntime.InferenceSession
        mock_session = MagicMock()
        mock_session.get_inputs.return_value = [
            MagicMock(name="input_ids"),
            MagicMock(name="attention_mask"),
        ]
        # 注意：MagicMock.name 属性赋值需要特殊处理，改用 configure_mock
        mock_input1 = MagicMock()
        mock_input1.name = "input_ids"
        mock_input2 = MagicMock()
        mock_input2.name = "attention_mask"
        mock_session.get_inputs.return_value = [mock_input1, mock_input2]

        with patch("onnxruntime.InferenceSession", return_value=mock_session), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()):
            result = r._load_onnx()

        assert result is True
        assert r._use_onnx is True
        assert r._onnx_session is mock_session
        assert r._onnx_tokenizer is not None
        assert r._onnx_input_names == ["input_ids", "attention_mask"]

    def test_load_onnx_failure_clears_state(self, tmp_path):
        """ONNX 加载失败时清理半初始化状态"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))

        # mock ort 抛异常
        with patch("onnxruntime.InferenceSession", side_effect=RuntimeError("invalid onnx")), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()):
            result = r._load_onnx()

        assert result is False
        assert r._use_onnx is False
        assert r._onnx_session is None
        assert r._onnx_tokenizer is None


# ════════════════════════════════════════════════════════════
#  3. _load_model 分发逻辑测试
# ════════════════════════════════════════════════════════════

class TestLoadModelDispatch:
    """_load_model 分发逻辑：ONNX 优先 → PyTorch 降级"""

    def test_load_model_uses_onnx_when_enabled_and_available(self, tmp_path):
        """ONNX 启用且可用时，走 ONNX 路径，不调 PyTorch"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))
        # 启用 ONNX
        r._use_onnx_env = True

        mock_session = MagicMock()
        mock_input = MagicMock()
        mock_input.name = "input_ids"
        mock_session.get_inputs.return_value = [mock_input]

        with patch("onnxruntime.InferenceSession", return_value=mock_session), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()), \
             patch("sentence_transformers.CrossEncoder") as mock_ce:
            result = r._load_model()

        assert result is True
        assert r._use_onnx is True
        # PyTorch 未被调用
        mock_ce.assert_not_called()

    def test_load_model_fallback_to_pytorch_when_onnx_disabled(self):
        """ONNX 禁用时直接走 PyTorch"""
        r = SkillReranker(model_name="some-model")
        r._use_onnx_env = False  # 禁用 ONNX

        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            result = r._load_model()

        assert result is True
        assert r._use_onnx is False
        assert r._model is not None
        mock_ce.assert_called_once()

    def test_load_model_fallback_to_pytorch_when_onnx_fails(self, tmp_path):
        """ONNX 加载失败时降级到 PyTorch"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))
        r._use_onnx_env = True

        # ONNX 加载失败，PyTorch 加载成功
        with patch("onnxruntime.InferenceSession", side_effect=RuntimeError("onnx broken")), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()), \
             patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            result = r._load_model()

        assert result is True
        assert r._use_onnx is False  # ONNX 未启用
        assert r._model is not None  # 走了 PyTorch
        mock_ce.assert_called_once()

    def test_load_model_returns_false_when_both_fail(self, tmp_path):
        """ONNX 和 PyTorch 都失败时返回 False（降级到 RRF）"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))
        r._use_onnx_env = True

        with patch("onnxruntime.InferenceSession", side_effect=RuntimeError("onnx broken")), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()), \
             patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("pytorch broken")
            result = r._load_model()

        assert result is False
        assert r._use_onnx is False
        assert r._model is None

    def test_load_model_not_retried_after_failure(self):
        """加载失败后不重试"""
        r = SkillReranker(model_name="nonexistent-model")
        r._use_onnx_env = False  # 跳过 ONNX，直接走 PyTorch 失败路径

        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("not found")
            r._load_model()
            # 第二次调用不应再次尝试
            result = r._load_model()

        assert result is False
        assert mock_ce.call_count == 1


# ════════════════════════════════════════════════════════════
#  4. _predict_onnx 推理测试
# ════════════════════════════════════════════════════════════

class TestPredictOnnx:
    """_predict_onnx 方法测试"""

    def test_predict_onnx_returns_float_scores(self):
        """ONNX 推理返回 float 分数列表"""
        r = SkillReranker()

        # mock tokenizer
        import numpy as np
        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": np.array([[1, 2, 3]]),
            "attention_mask": np.array([[1, 1, 1]]),
        }
        r._onnx_tokenizer = mock_tokenizer

        # mock session
        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[0.95], [0.42]])]
        r._onnx_session = mock_session
        r._onnx_input_names = ["input_ids", "attention_mask"]
        r._use_onnx = True

        pairs = [("query1", "doc1"), ("query2", "doc2")]
        scores = r._predict_onnx(pairs, "test-tid")

        assert len(scores) == 2
        assert all(isinstance(s, float) for s in scores)
        assert scores[0] == pytest.approx(0.95)
        assert scores[1] == pytest.approx(0.42)

    def test_predict_onnx_empty_pairs_returns_empty(self):
        """空 pairs 返回空列表"""
        r = SkillReranker()
        r._onnx_session = MagicMock()
        r._onnx_tokenizer = MagicMock()
        scores = r._predict_onnx([], "test-tid")
        assert scores == []

    def test_predict_onnx_no_session_returns_zeros(self):
        """session 未加载时返回零分数"""
        r = SkillReranker()
        r._onnx_session = None
        r._onnx_tokenizer = None
        scores = r._predict_onnx([("q", "d")], "test-tid")
        assert scores == [0.0]

    def test_predict_onnx_exception_returns_zeros(self):
        """推理异常时返回零分数（不抛异常）"""
        r = SkillReranker()

        mock_tokenizer = MagicMock()
        mock_tokenizer.side_effect = RuntimeError("tokenize failed")
        r._onnx_tokenizer = mock_tokenizer
        r._onnx_session = MagicMock()
        r._onnx_input_names = ["input_ids"]
        r._use_onnx = True

        scores = r._predict_onnx([("q", "d")], "test-tid")
        assert scores == [0.0]

    def test_predict_onnx_handles_token_type_ids(self):
        """支持含 token_type_ids 输入的 ONNX 模型"""
        r = SkillReranker()

        import numpy as np
        mock_tokenizer = MagicMock()
        mock_tokenizer.return_value = {
            "input_ids": np.array([[1, 2, 3]]),
            "attention_mask": np.array([[1, 1, 1]]),
            "token_type_ids": np.array([[0, 0, 0]]),
        }
        r._onnx_tokenizer = mock_tokenizer

        mock_session = MagicMock()
        mock_session.run.return_value = [np.array([[0.5]])]
        r._onnx_session = mock_session
        # 模型需要 token_type_ids
        r._onnx_input_names = ["input_ids", "attention_mask", "token_type_ids"]
        r._use_onnx = True

        scores = r._predict_onnx([("q", "d")], "test-tid")

        assert scores == [0.5]
        # 验证 feed 包含 token_type_ids
        call_args = mock_session.run.call_args
        feed = call_args[0][1]
        assert "token_type_ids" in feed


# ════════════════════════════════════════════════════════════
#  5. _predict_with_timeout 分发测试
# ════════════════════════════════════════════════════════════

class TestPredictDispatch:
    """_predict_with_timeout 分发逻辑"""

    def test_dispatch_to_onnx_when_use_onnx_true(self):
        """_use_onnx=True 时分发到 _predict_onnx"""
        r = SkillReranker()
        r._use_onnx = True
        r._onnx_session = MagicMock()  # 非 None

        with patch.object(r, "_predict_onnx", return_value=[0.5, 0.3]) as mock_onnx:
            scores = r._predict_with_timeout([("q", "d1"), ("q", "d2")], "tid")

        assert scores == [0.5, 0.3]
        mock_onnx.assert_called_once()

    def test_dispatch_to_pytorch_when_use_onnx_false(self):
        """_use_onnx=False 时走 PyTorch 路径"""
        r = SkillReranker()
        r._use_onnx = False
        r._model = MagicMock()
        r._model.predict.return_value = [0.7, 0.4]

        scores = r._predict_with_timeout([("q", "d1"), ("q", "d2")], "tid")

        assert scores == [0.7, 0.4]
        r._model.predict.assert_called_once()

    def test_pytorch_path_returns_zeros_when_model_none(self):
        """PyTorch 路径模型未加载时返回零分数"""
        r = SkillReranker()
        r._use_onnx = False
        r._model = None

        scores = r._predict_with_timeout([("q", "d1"), ("q", "d2")], "tid")
        assert scores == [0.0, 0.0]


# ════════════════════════════════════════════════════════════
#  6. rerank 端到端测试（ONNX 路径）
# ════════════════════════════════════════════════════════════

class TestRerankEndToEnd:
    """rerank 端到端测试（mock ONNX 后端）"""

    def test_rerank_with_onnx_backend(self, sample_candidates):
        """ONNX 后端 rerank 正常返回排序结果"""
        r = SkillReranker()
        # 直接注入已加载状态（绕过 _load_model）
        r._use_onnx = True
        r._onnx_session = MagicMock()
        r._onnx_tokenizer = MagicMock()
        r._onnx_input_names = ["input_ids", "attention_mask"]
        r._load_attempted = True  # 跳过加载

        # mock _predict_onnx 返回与候选顺序相反的分数（让排序翻转）
        # candidates 顺序: voice(0.6), pdf(0.4), reflection(0.8)
        # 给 reflection 最高分，应排首位
        with patch.object(r, "_predict_onnx", return_value=[0.3, 0.2, 0.95]):
            result = r.rerank("反思我的回答", sample_candidates, top_k=3)

        assert len(result) == 3
        # reflection 应排首位（分数 0.95）
        assert result[0].skill_id == "self_reflection"

    def test_rerank_disabled_returns_original_order(self, sample_candidates):
        """SKILL_RERANKER_ENABLED=false 时返回原序"""
        os.environ["SKILL_RERANKER_ENABLED"] = "false"
        r = SkillReranker()
        result = r.rerank("query", sample_candidates, top_k=2)
        assert len(result) == 2
        assert result[0].skill_id == "voice_interaction"  # 原序首位

    def test_rerank_empty_candidates(self):
        """空候选返回空列表"""
        r = SkillReranker()
        result = r.rerank("query", [], top_k=3)
        assert result == []

    def test_rerank_load_failure_fallback_to_original(self, sample_candidates):
        """模型加载失败时降级返回原序 top_k"""
        r = SkillReranker()
        r._use_onnx_env = False  # 跳过 ONNX
        # PyTorch 也失败
        with patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("load failed")
            result = r.rerank("query", sample_candidates, top_k=2)

        assert len(result) == 2
        assert result[0].skill_id == "voice_interaction"  # 原序

    def test_rerank_filters_low_score_candidates(self, sample_candidates):
        """低于 _min_score 的候选被过滤

        【变易】sigmoid 后 mock [0.3, 0.2, 0.95] → [0.5744, 0.5498, 0.7211]
        _min_score=0.6 时只保留 sigmoid(0.95)=0.7211 > 0.6（1 个）
        """
        r = SkillReranker()
        r._use_onnx = True
        r._onnx_session = MagicMock()
        r._onnx_tokenizer = MagicMock()
        r._onnx_input_names = ["input_ids", "attention_mask"]
        r._load_attempted = True
        r._min_score = 0.6  # sigmoid 后阈值

        # 只有一个候选 sigmoid 后超过 0.6
        with patch.object(r, "_predict_onnx", return_value=[0.3, 0.2, 0.95]):
            result = r.rerank("query", sample_candidates, top_k=3)

        assert len(result) == 1
        assert result[0].skill_id == "self_reflection"

    def test_rerank_predict_failure_fallback_to_original(self, sample_candidates):
        """推理异常时降级返回原序"""
        r = SkillReranker()
        r._use_onnx = True
        r._onnx_session = MagicMock()
        r._onnx_tokenizer = MagicMock()
        r._onnx_input_names = ["input_ids", "attention_mask"]
        r._load_attempted = True

        # _predict_with_timeout 抛异常
        with patch.object(r, "_predict_with_timeout", side_effect=RuntimeError("predict failed")):
            result = r.rerank("query", sample_candidates, top_k=2)

        assert len(result) == 2
        assert result[0].skill_id == "voice_interaction"  # 原序


# ════════════════════════════════════════════════════════════
#  7. 降级链完整性测试
# ════════════════════════════════════════════════════════════

class TestDowngradeChain:
    """ONNX → PyTorch → RRF 降级链完整性"""

    def test_full_chain_onnx_to_pytorch_to_rrf(self, tmp_path):
        """完整降级链：ONNX 失败 → PyTorch 失败 → RRF（原序）"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))

        candidates = [
            MockSkillMatch(skill_id="c1", name="c1", description="d1"),
            MockSkillMatch(skill_id="c2", name="c2", description="d2"),
        ]

        # ONNX 加载失败 + PyTorch 加载失败 → 返回原序 top_k
        with patch("onnxruntime.InferenceSession", side_effect=RuntimeError("onnx broken")), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()), \
             patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.side_effect = RuntimeError("pytorch broken")
            result = r.rerank("query", candidates, top_k=2)

        # RRF 降级：返回原序
        assert len(result) == 2
        assert result[0].skill_id == "c1"
        assert result[1].skill_id == "c2"

    def test_onnx_to_pytorch_partial_degradation(self, tmp_path):
        """ONNX 失败 → PyTorch 成功（部分降级）"""
        onnx_dir = tmp_path / "onnx"
        onnx_dir.mkdir()
        (onnx_dir / "model_quantized.onnx").write_bytes(b"dummy")

        r = SkillReranker(model_name=str(tmp_path))

        # ONNX 失败，PyTorch 成功
        with patch("onnxruntime.InferenceSession", side_effect=RuntimeError("onnx broken")), \
             patch("transformers.AutoTokenizer.from_pretrained", return_value=MagicMock()), \
             patch("sentence_transformers.CrossEncoder") as mock_ce:
            mock_ce.return_value = MagicMock()
            load_result = r._load_model()

        assert load_result is True
        assert r._use_onnx is False
        assert r._model is not None  # PyTorch 加载成功
