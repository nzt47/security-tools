"""v6.5 SkillReranker 热重载机制单元测试

【不易】不依赖真实 ONNX 模型（mock _load_onnx / _onnx_session）
【变易】临时 .env 文件隔离（tmp_path fixture），不污染项目 .env
【简易】每个测试单一职责，覆盖 mtime/variant/回滚/节流四类场景

测试覆盖:
    1. mtime 未变化 → 不触发重载
    2. mtime 变化但 variant 未变 → 仅更新 mtime
    3. variant 变化且加载成功 → _onnx_variant_loaded 更新
    4. variant 变化但加载失败 → 回滚保留旧会话
    5. .env 文件不存在 → 不抛异常
    6. 节流机制 → 间隔内不重复检查
    7. .env 解析 → 支持 KEY=VALUE 和 KEY="VALUE"
    8. 加载过程异常 → 异常回滚

运行:
    python -m pytest tests/unit/test_reranker_hot_reload.py -v
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 【不易】防止 sentence_transformers 真实 import 导致 Windows 0xC0000005 崩溃
if "sentence_transformers" not in sys.modules:
    sys.modules["sentence_transformers"] = MagicMock()

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.skills_mgmt.reranker import SkillReranker


# ════════════════════════════════════════════════════════════
#  测试夹具
# ════════════════════════════════════════════════════════════

@pytest.fixture
def isolated_env_file(tmp_path: Path):
    """创建隔离的 .env 文件，避免污染项目真实 .env

    【不易】每个测试用独立 .env 文件，互不影响
    【变易】通过 SKILL_RERANKER_ENV_FILE 环境变量指定路径
    """
    env_file = tmp_path / "test_hot_reload.env"
    env_file.write_text(
        'SKILL_RERANKER_ENABLED=true\n'
        'SKILL_RERANKER_USE_ONNX=true\n'
        'SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx\n',
        encoding="utf-8",
    )
    # 保存原值用于恢复
    original_env_file = os.environ.get("SKILL_RERANKER_ENV_FILE")
    os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
    # 关闭节流，让测试立即触发检查
    original_interval = os.environ.get("SKILL_RERANKER_HOT_RELOAD_INTERVAL")
    os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"
    yield env_file
    # 恢复环境变量
    if original_env_file is not None:
        os.environ["SKILL_RERANKER_ENV_FILE"] = original_env_file
    else:
        os.environ.pop("SKILL_RERANKER_ENV_FILE", None)
    if original_interval is not None:
        os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = original_interval
    else:
        os.environ.pop("SKILL_RERANKER_HOT_RELOAD_INTERVAL", None)


@pytest.fixture
def reranker_with_onnx(isolated_env_file: Path):
    """已加载 ONNX 会话的 reranker（mock _load_onnx 返回 True）

    【不易】mock _onnx_session 为 MagicMock，不触发真实 ONNX 加载
    【变易】_use_onnx=True + _onnx_session 非 None，模拟"已加载"状态
    """
    reranker = SkillReranker()
    # 模拟 ONNX 已加载状态
    reranker._use_onnx = True
    reranker._onnx_session = MagicMock()
    reranker._onnx_tokenizer = MagicMock()
    reranker._onnx_input_names = ["input_ids", "attention_mask"]
    reranker._load_attempted = True
    reranker._onnx_variant_loaded = "model_quantized.onnx"
    # 同步 _env_mtime 为当前文件 mtime
    reranker._env_mtime = reranker._get_env_mtime()
    # 重置 _last_env_check 触发首次检查
    reranker._last_env_check = 0.0
    return reranker


# ════════════════════════════════════════════════════════════
#  1. mtime 未变化 → 不触发重载
# ════════════════════════════════════════════════════════════

class TestMtimeUnchanged:
    """mtime 未变化时不触发重载"""

    def test_no_reload_when_mtime_unchanged(self, reranker_with_onnx):
        """mtime 未变 → _onnx_variant_loaded 不变"""
        r = reranker_with_onnx
        original_variant = r._onnx_variant_loaded
        original_session = r._onnx_session

        r._check_hot_reload()

        assert r._onnx_variant_loaded == original_variant
        assert r._onnx_session is original_session  # 会话未被替换


# ════════════════════════════════════════════════════════════
#  2. mtime 变化但 variant 未变 → 仅更新 mtime
# ════════════════════════════════════════════════════════════

class TestMtimeChangedVariantSame:
    """mtime 变化但 variant 未变"""

    def test_only_update_mtime_when_variant_unchanged(
        self, reranker_with_onnx, isolated_env_file: Path
    ):
        """修改 .env mtime（内容不变）→ 仅更新 mtime，不重载"""
        r = reranker_with_onnx
        old_mtime = r._env_mtime

        # 修改文件 mtime（内容相同，通过 touch 更新时间戳）
        time.sleep(0.1)  # 确保 mtime 变化
        isolated_env_file.touch()

        r._check_hot_reload()

        # mtime 已更新
        assert r._env_mtime > old_mtime
        # variant 未变
        assert r._onnx_variant_loaded == "model_quantized.onnx"
        # session 未被替换
        assert r._onnx_session is not None


# ════════════════════════════════════════════════════════════
#  3. variant 变化且加载成功 → _onnx_variant_loaded 更新
# ════════════════════════════════════════════════════════════

class TestVariantChangedReloadSuccess:
    """variant 变化且加载成功"""

    def test_reload_success_updates_variant(self, reranker_with_onnx, isolated_env_file: Path):
        """variant 变化 + 加载成功 → _onnx_variant_loaded 更新"""
        r = reranker_with_onnx

        # 修改 .env：variant 从 model_quantized.onnx → model.onnx
        time.sleep(0.1)
        isolated_env_file.write_text(
            'SKILL_RERANKER_ENABLED=true\n'
            'SKILL_RERANKER_USE_ONNX=true\n'
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )

        # mock _load_onnx 返回 True（模拟新会话加载成功）
        with patch.object(r, "_load_onnx", return_value=True) as mock_load:
            r._check_hot_reload()
            assert mock_load.called

        # variant 已更新为新值
        assert r._onnx_variant_loaded == "model.onnx"
        # _onnx_variant（期望值）也同步更新
        assert r._onnx_variant == "model.onnx"
        # _use_onnx 保持 True
        assert r._use_onnx is True


# ════════════════════════════════════════════════════════════
#  4. variant 变化但加载失败 → 回滚保留旧会话
# ════════════════════════════════════════════════════════════

class TestVariantChangedReloadFailed:
    """variant 变化但加载失败 → 回滚"""

    def test_reload_failed_rollback(self, reranker_with_onnx, isolated_env_file: Path):
        """variant 变化 + 加载失败 → 保留旧会话"""
        r = reranker_with_onnx
        original_session = r._onnx_session
        original_variant = r._onnx_variant_loaded

        # 修改 .env：variant → model.onnx
        time.sleep(0.1)
        isolated_env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )

        # mock _load_onnx 返回 False（模拟新会话加载失败）
        with patch.object(r, "_load_onnx", return_value=False) as mock_load:
            r._check_hot_reload()
            assert mock_load.called

        # 回滚：variant 和 session 保留旧值
        assert r._onnx_variant_loaded == original_variant
        assert r._onnx_session is original_session
        assert r._use_onnx is True  # 旧会话仍可用


# ════════════════════════════════════════════════════════════
#  5. .env 文件不存在 → 不抛异常
# ════════════════════════════════════════════════════════════

class TestEnvFileMissing:
    """."""

    def test_missing_env_file_no_exception(self, tmp_path: Path):
        """.env 文件不存在 → _get_env_mtime 返回 0，不抛异常"""
        nonexistent = tmp_path / "nonexistent.env"
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(nonexistent)
        os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"
        try:
            r = SkillReranker()
            assert r._get_env_mtime() == 0.0
            # _check_hot_reload 不抛异常
            r._check_hot_reload()
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)
            os.environ.pop("SKILL_RERANKER_HOT_RELOAD_INTERVAL", None)


# ════════════════════════════════════════════════════════════
#  6. 节流机制 → 间隔内不重复检查
# ════════════════════════════════════════════════════════════

class TestThrottling:
    """节流机制"""

    def test_throttle_skips_check_within_interval(
        self, reranker_with_onnx, isolated_env_file: Path
    ):
        """间隔内不重复检查文件系统"""
        r = reranker_with_onnx
        # 设置 60s 间隔
        r._env_check_interval = 60.0
        # 设置上次检查时间为当前
        r._last_env_check = time.time()

        # 修改 .env 内容（模拟变化）
        time.sleep(0.1)
        isolated_env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )

        with patch.object(r, "_get_env_mtime") as mock_mtime:
            # 不应被调用（节流跳过）
            r._check_hot_reload()
            assert not mock_mtime.called

        # variant 未变（节流跳过了检查）
        assert r._onnx_variant_loaded == "model_quantized.onnx"


# ════════════════════════════════════════════════════════════
#  7. .env 解析 → 支持 KEY=VALUE 和 KEY="VALUE"
# ════════════════════════════════════════════════════════════

class TestEnvFileParsing:
    """."""

    def test_parse_plain_value(self, tmp_path: Path):
        """KEY=VALUE 格式"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
        try:
            r = SkillReranker()
            assert r._read_variant_from_env_file() == "model.onnx"
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)

    def test_parse_quoted_value(self, tmp_path: Path):
        """KEY="VALUE" 格式（带双引号）"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT="model.onnx"\n',
            encoding="utf-8",
        )
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
        try:
            r = SkillReranker()
            assert r._read_variant_from_env_file() == "model.onnx"
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)

    def test_parse_single_quoted_value(self, tmp_path: Path):
        """KEY='VALUE' 格式（带单引号）"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            "SKILL_RERANKER_ONNX_VARIANT='model.onnx'\n",
            encoding="utf-8",
        )
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
        try:
            r = SkillReranker()
            assert r._read_variant_from_env_file() == "model.onnx"
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)

    def test_parse_skips_comments(self, tmp_path: Path):
        """跳过注释行"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            '# 这是注释\n'
            'SKILL_RERANKER_ENABLED=true\n'
            '# SKILL_RERANKER_ONNX_VARIANT=should_not_match\n'
            'SKILL_RERANKER_ONNX_VARIANT=model_quantized.onnx\n',
            encoding="utf-8",
        )
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
        try:
            r = SkillReranker()
            assert r._read_variant_from_env_file() == "model_quantized.onnx"
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)

    def test_parse_returns_none_when_key_missing(self, tmp_path: Path):
        """未配置 ONNX_VARIANT → 返回 None"""
        env_file = tmp_path / "test.env"
        env_file.write_text(
            'SKILL_RERANKER_ENABLED=true\n',
            encoding="utf-8",
        )
        os.environ["SKILL_RERANKER_ENV_FILE"] = str(env_file)
        try:
            r = SkillReranker()
            assert r._read_variant_from_env_file() is None
        finally:
            os.environ.pop("SKILL_RERANKER_ENV_FILE", None)


# ════════════════════════════════════════════════════════════
#  8. 加载过程异常 → 异常回滚
# ════════════════════════════════════════════════════════════

class TestReloadExceptionRollback:
    """."""

    def test_exception_rollback(self, reranker_with_onnx, isolated_env_file: Path):
        """_load_onnx 抛异常 → 异常回滚保留旧会话"""
        r = reranker_with_onnx
        original_session = r._onnx_session
        original_variant = r._onnx_variant_loaded

        # 修改 .env
        time.sleep(0.1)
        isolated_env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )

        # mock _load_onnx 抛异常
        with patch.object(r, "_load_onnx", side_effect=RuntimeError("unexpected error")):
            # 不抛异常（被 _hot_reload_onnx_variant 内部 try/except 捕获）
            r._check_hot_reload()

        # 回滚：variant 和 session 保留旧值
        assert r._onnx_variant_loaded == original_variant
        assert r._onnx_session is original_session
        assert r._use_onnx is True


# ════════════════════════════════════════════════════════════
#  9. 未加载 ONNX 时不触发热重载
# ════════════════════════════════════════════════════════════

class TestNoReloadWhenOnnxNotLoaded:
    """."""

    def test_no_reload_when_use_onnx_false(self, isolated_env_file: Path):
        """_use_onnx=False → _check_hot_reload 直接返回"""
        r = SkillReranker()
        r._use_onnx = False
        r._onnx_session = None

        # 修改 .env
        time.sleep(0.1)
        isolated_env_file.write_text(
            'SKILL_RERANKER_ONNX_VARIANT=model.onnx\n',
            encoding="utf-8",
        )

        with patch.object(r, "_get_env_mtime") as mock_mtime:
            r._check_hot_reload()
            # 不应检查 mtime（use_onnx=False 时早返回）
            assert not mock_mtime.called

    def test_no_reload_when_session_none(self, isolated_env_file: Path):
        """_onnx_session=None → _check_hot_reload 直接返回"""
        r = SkillReranker()
        r._use_onnx = True
        r._onnx_session = None  # session 为 None

        with patch.object(r, "_get_env_mtime") as mock_mtime:
            r._check_hot_reload()
            assert not mock_mtime.called
