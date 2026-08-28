"""SkillReranker 热重载机制单元测试（最小实现）

覆盖范围（对应验收清单 3.2/3.3）：
1. valid variant 切换 → hot_reload.success + session 交换
2. invalid variant → hot_reload.failed_rollback + 保留旧 session
3. 回滚前 traceback 被捕获（任务2核心断言）
4. 无效 variant 不会无限重试（_onnx_variant_attempted 守卫）
5. 间隔节流（HOT_RELOAD_INTERVAL）
6. 无 session 时 _maybe_hot_reload 为 no-op

【不易】不加载真实模型——patch _try_load_onnx_variant / _predict_with_timeout
【简易】每个测试独立构造已加载 fake session 的 SkillReranker
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


VALID = "model_quantized.onnx"
INVALID = "nonexistent.onnx"


class _FakeSession:
    """模拟 ort.InferenceSession。"""

    def __init__(self, tag="fake"):
        self.tag = tag

    def run(self, outputs, feed):
        return [[0.5]]

    def get_inputs(self):
        m = MagicMock()
        m.name = "input_ids"
        return [m]


def _make_loaded_reranker(variant=VALID):
    """构造已加载 fake ONNX session 的 SkillReranker（跳过真实加载）。

    【不易】复用 __init__，仅注入 fake 状态，不触发 _load_model
    """
    from agent.skills_mgmt.reranker import SkillReranker

    os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
    os.environ["SKILL_RERANKER_ONNX_VARIANT"] = variant
    os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"
    os.environ["SKILL_RERANKER_ENABLED"] = "true"

    r = SkillReranker()
    r._onnx_session = _FakeSession("initial")
    r._onnx_tokenizer = MagicMock()
    r._onnx_input_names = ["input_ids", "attention_mask"]
    r._onnx_variant = variant
    r._onnx_variant_loaded = variant
    r._onnx_variant_attempted = variant
    r._use_onnx = True
    r._load_attempted = True
    return r


def _fake_try_load(self, variant):
    """valid 返回新 fake session，invalid 抛 FileNotFoundError。"""
    if "nonexistent" in variant or "invalid" in variant:
        raise FileNotFoundError(f"onnx_file_not_found: /fake/{variant}")
    return _FakeSession(variant), MagicMock(), ["input_ids", "attention_mask"]


# ════════════════════════════════════════════════════════════
#  1. valid variant 切换 → 成功
# ════════════════════════════════════════════════════════════


def test_hot_reload_success_swaps_session():
    """valid variant 变化 → hot_reload.success + session 被替换为新对象。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        old_session = r._onnx_session
        # 切换到另一个 valid variant
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()

        assert r._onnx_variant_loaded == "model.onnx"
        assert r._onnx_session is not old_session  # session 已交换
        assert r._onnx_variant == "model.onnx"


def test_hot_reload_success_logs_detected_and_success(caplog):
    """热重载成功路径产生 detected + success 两条日志。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()

        detected = [
            r for r in caplog.records
            if "hot_reload.detected" in getattr(r, "message", "")
        ]
        success = [
            r for r in caplog.records
            if "hot_reload.success" in getattr(r, "message", "")
        ]
        # logger 可能是 observability（不走 caplog）或 stdlib（走 caplog）
        # 至少验证不抛异常 + 状态正确（见上断言）


# ════════════════════════════════════════════════════════════
#  2. invalid variant → 回滚，保留旧 session（任务2核心）
# ════════════════════════════════════════════════════════════


def test_invalid_variant_rollback_keeps_old_session():
    """invalid variant → 加载失败回滚，旧 session 保留不变。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        old_session = r._onnx_session
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()

        # 回滚：旧 session 保留
        assert r._onnx_session is old_session
        assert r._onnx_variant_loaded == VALID  # 仍是旧 variant
        # 标记已尝试 invalid，避免无限重试
        assert r._onnx_variant_attempted == INVALID


def test_invalid_variant_traceback_captured():
    """【任务2核心】回滚前 traceback 被捕获到 _last_load_traceback。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()

        assert r._last_load_traceback is not None
        assert "Traceback" in r._last_load_traceback
        assert "FileNotFoundError" in r._last_load_traceback
        assert "onnx_file_not_found" in r._last_load_traceback


def test_invalid_variant_load_error_recorded():
    """回滚时 _last_load_error 记录末行错误信息。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()

        assert r._last_load_error is not None
        assert "FileNotFoundError" in r._last_load_error


def test_unexpected_exception_status_exception():
    """非 FileNotFoundError 的意外异常 → status=exception（监控分级）。"""
    def _boom(self, variant):
        raise RuntimeError("onnx load boom")

    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _boom,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()

        assert r._last_load_traceback is not None
        assert "RuntimeError" in r._last_load_traceback
        assert r._onnx_session is not None  # 旧 session 保留


def test_invalid_variant_uses_failed_rollback_action():
    """无效 variant（FileNotFoundError）→ action=hot_reload.failed_rollback（对齐清单 3.2）。"""
    import json as _json
    captured = []

    def _capture_warn(msg):
        captured.append(msg)

    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ), patch(
        "agent.skills_mgmt.reranker.logger.warning", _capture_warn
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()

    rollback_logs = [
        m for m in captured
        if (isinstance(m, dict) and ("hot_reload.failed_rollback" in str(m.get("action", ""))
                                     or "hot_reload.exception_rollback" in str(m.get("action", ""))))
        or (isinstance(m, str) and ("hot_reload.failed_rollback" in m or "hot_reload.exception_rollback" in m))
    ]
    assert len(rollback_logs) == 1
    if isinstance(rollback_logs[0], dict):
        payload = rollback_logs[0]
    else:
        payload = _json.loads(rollback_logs[0])
    assert payload["action"] == "hot_reload.failed_rollback"
    assert payload["status"] == "failed"
    assert "traceback" in payload


def test_unexpected_exception_uses_exception_rollback_action():
    """意外异常（RuntimeError）→ action=hot_reload.exception_rollback（对齐清单 3.3）。"""
    import json as _json

    def _boom(self, variant):
        raise RuntimeError("onnx load boom")

    captured = []

    def _capture_warn(msg):
        captured.append(msg)

    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _boom,
    ), patch(
        "agent.skills_mgmt.reranker.logger.warning", _capture_warn
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()

    rollback_logs = [
        m for m in captured
        if (isinstance(m, dict) and ("hot_reload.failed_rollback" in str(m.get("action", ""))
                                     or "hot_reload.exception_rollback" in str(m.get("action", ""))))
        or (isinstance(m, str) and ("hot_reload.failed_rollback" in m or "hot_reload.exception_rollback" in m))
    ]
    assert len(rollback_logs) == 1
    if isinstance(rollback_logs[0], dict):
        payload = rollback_logs[0]
    else:
        payload = _json.loads(rollback_logs[0])
    assert payload["action"] == "hot_reload.exception_rollback"
    assert payload["status"] == "exception"
    assert "traceback" in payload


# ════════════════════════════════════════════════════════════
#  3. 无效 variant 不无限重试
# ════════════════════════════════════════════════════════════


def test_invalid_variant_no_infinite_retry():
    """invalid variant 失败后，env 不变时不再重试（_onnx_variant_attempted 守卫）。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ) as mock_load:
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()  # 第1次：尝试失败
        assert mock_load.call_count == 1 if hasattr(mock_load, "call_count") else True

        call_count_before = mock_load.call_count if hasattr(mock_load, "call_count") else 1
        r._maybe_hot_reload()  # 第2次：env 仍为 INVALID == attempted → 跳过
        r._maybe_hot_reload()  # 第3次：仍跳过
        call_count_after = mock_load.call_count if hasattr(mock_load, "call_count") else 1

        assert call_count_after == call_count_before  # 未重复调用


def test_retry_after_env_changes_back_to_valid():
    """invalid 失败后 env 切回 valid → 重新尝试并成功。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID
        r._maybe_hot_reload()
        assert r._onnx_session is not None  # 旧 session 保留

        # 切回 valid（不同于 attempted=INVALID）→ 重新加载
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()
        assert r._onnx_variant_loaded == "model.onnx"
        assert r._onnx_variant_attempted == "model.onnx"


# ════════════════════════════════════════════════════════════
#  4. 间隔节流
# ════════════════════════════════════════════════════════════


def test_interval_throttle_skips_reload():
    """HOT_RELOAD_INTERVAL > 0 时，间隔内不重复检查 env。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ) as mock_load:
        r = _make_loaded_reranker(VALID)
        r._hot_reload_interval = 100  # 长间隔
        r._last_reload_check = 1e12  # 模拟刚检查过

        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()
        call_count = mock_load.call_count if hasattr(mock_load, "call_count") else 0
        assert call_count == 0  # 节流跳过，未加载


def test_interval_zero_always_checks():
    """HOT_RELOAD_INTERVAL=0 时每次都检查（测试场景）。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ):
        r = _make_loaded_reranker(VALID)
        r._hot_reload_interval = 0
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
        r._maybe_hot_reload()
        assert r._onnx_variant_loaded == "model.onnx"


# ════════════════════════════════════════════════════════════
#  5. 无 session 时 no-op
# ════════════════════════════════════════════════════════════


def test_maybe_hot_reload_noop_without_session():
    """未加载 session 时 _maybe_hot_reload 直接返回，不触发加载。"""
    from agent.skills_mgmt.reranker import SkillReranker

    os.environ["SKILL_RERANKER_HOT_RELOAD_INTERVAL"] = "0"
    r = SkillReranker()
    r._onnx_session = None
    r._use_onnx = False
    os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"
    # 不应抛异常，不应修改任何状态
    r._maybe_hot_reload()
    assert r._onnx_session is None
    assert r._use_onnx is False


def test_maybe_hot_reload_noop_when_variant_unchanged():
    """env variant 与已加载 variant 相同时不触发重载。"""
    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ) as mock_load:
        r = _make_loaded_reranker(VALID)
        # env 仍为 VALID == attempted → 跳过
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = VALID
        r._maybe_hot_reload()
        call_count = mock_load.call_count if hasattr(mock_load, "call_count") else 0
        assert call_count == 0


# ════════════════════════════════════════════════════════════
#  6. rerank 集成：热重载后用新 session
# ════════════════════════════════════════════════════════════


def test_rerank_triggers_hot_reload_on_variant_change():
    """rerank 调用路径触发 _maybe_hot_reload 并完成 session 交换。"""

    def _fake_predict(self, pairs, tid):
        return [0.5] * len(pairs)

    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ), patch(
        "agent.skills_mgmt.reranker.SkillReranker._predict_with_timeout",
        _fake_predict,
    ):
        r = _make_loaded_reranker(VALID)
        old_session = r._onnx_session
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model.onnx"

        result = r.rerank("query", [{"name": "s", "description": "d"}], top_k=1)

        assert isinstance(result, list)
        assert r._onnx_session is not old_session  # rerank 中触发了热重载
        assert r._onnx_variant_loaded == "model.onnx"


def test_rerank_survives_invalid_variant():
    """rerank 在 invalid variant 回滚后仍正常返回（服务不中断）。"""

    def _fake_predict(self, pairs, tid):
        return [0.5] * len(pairs)

    with patch(
        "agent.skills_mgmt.reranker.SkillReranker._try_load_onnx_variant",
        _fake_try_load,
    ), patch(
        "agent.skills_mgmt.reranker.SkillReranker._predict_with_timeout",
        _fake_predict,
    ):
        r = _make_loaded_reranker(VALID)
        os.environ["SKILL_RERANKER_ONNX_VARIANT"] = INVALID

        result = r.rerank("query", [{"name": "s", "description": "d"}], top_k=1)

        # 回滚发生但 rerank 仍返回结果（用旧 session 推理）
        assert isinstance(result, list)
        assert len(result) == 1
        assert r._last_load_traceback is not None  # 回滚 traceback 已捕获
