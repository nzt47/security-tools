"""ToolReranker 单元测试 — 子进程隔离 + 异常降级场景

测试覆盖:
    1. 子进程启动（成功 ready / 失败 init_failed / 无输出）
    2. predict 打分（正常 / worker error / 子进程退出 / 异常）
    3. rerank 接口（正常排序 + 阈值过滤 + top_k + 空候选）
    4. 异常降级（worker 不可用 / predict 失败 → 返回原顺序 rerank_score=0.0）
    5. 单例开关（AGENT_HYBRID_RERANKER）
    6. 环境变量解析（_env_float / _env_int 失败回退）
    7. health() / close() 生命周期

设计原则:
    【不易】不依赖真实模型/真实子进程（mock subprocess.Popen）
    【变易】环境变量隔离（reset_environment 自动清理）
    【简易】每个测试单一职责，断言自描述

运行:
    python -m pytest tests/unit/test_tool_router_reranker.py -v
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根目录在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.tool_router_reranker import (
    ToolReranker,
    _DEFAULT_RERANK_MIN_SCORE,
    _DEFAULT_RERANK_TOP_N,
    _env_float,
    _env_int,
    get_tool_reranker,
    reset_tool_reranker,
)


# ════════════════════════════════════════════════════════════
#  测试数据
# ════════════════════════════════════════════════════════════

_SAMPLE_CANDIDATES = [
    ("tool_search", 0.82),
    ("tool_retrieve", 0.65),
    ("tool_summarize", 0.50),
    ("tool_translate", 0.32),
]

_SAMPLE_DESCRIPTIONS = {
    "tool_search": "关键词检索工具",
    "tool_retrieve": "向量检索工具",
    "tool_summarize": "文本摘要工具",
    "tool_translate": "翻译工具",
}


def _make_mock_proc(ready_line: str):
    """构造 mock 子进程：stdout.readline 返回 ready_line 后进入 EOF"""
    proc = MagicMock()
    proc.poll.return_value = None  # 子进程存活
    proc.stdout.readline.side_effect = [ready_line, ""]
    proc.stderr.read.return_value = ""
    return proc


# ════════════════════════════════════════════════════════════
#  子进程启动（_ensure_worker）
# ════════════════════════════════════════════════════════════

class TestEnsureWorker:
    """子进程启动成功与失败路径"""

    def test_ready(self):
        """【正常】ready 消息 → worker 就绪，返回 True"""
        proc = _make_mock_proc(json.dumps({"type": "ready", "load_time_sec": 1.2,
                                           "load_source": "local"}))
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is True
            assert r._load_time_sec == 1.2
            assert r._load_source == "local"
            assert r._init_failed is False

    def test_init_failed_message(self):
        """【降级】init_failed 消息 → 标记 _init_failed，返回 False"""
        proc = _make_mock_proc(json.dumps({"type": "init_failed", "error": "model load error"}))
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is False
            assert r._init_failed is True

    def test_no_output(self):
        """【降级】子进程无输出（提前退出）→ 返回 False"""
        proc = _make_mock_proc("")  # stdout 立即 EOF
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is False
            assert r._init_failed is True

    def test_unknown_message(self):
        """【降级】未知消息类型 → 返回 False"""
        proc = _make_mock_proc(json.dumps({"type": "weird"}))
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is False

    def test_popen_raises(self):
        """【降级】Popen 抛异常 → 返回 False，不向上抛"""
        with patch("subprocess.Popen", side_effect=OSError("cannot spawn")):
            r = ToolReranker()
            assert r._ensure_worker() is False
            assert r._init_failed is True

    def test_init_failed_short_circuit(self):
        """【降级】_init_failed 后不再重复尝试启动子进程"""
        with patch("subprocess.Popen", side_effect=OSError("cannot spawn")) as mock_popen:
            r = ToolReranker()
            assert r._ensure_worker() is False
            assert r._ensure_worker() is False  # 第二次直接短路
            assert mock_popen.call_count == 1  # 只启动过一次


# ════════════════════════════════════════════════════════════
#  predict 打分（_predict_scores）
# ════════════════════════════════════════════════════════════

class TestPredictScores:
    """子进程打分正常与异常路径"""

    def _ready_reranker(self):
        """返回一个已就绪的 reranker（mock 子进程）"""
        proc = _make_mock_proc(json.dumps({"type": "ready", "load_time_sec": 0.1,
                                           "load_source": "local"}))
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is True
        return r, proc

    def test_scores_ok(self):
        """【正常】scores 消息 → 返回分数列表"""
        r, proc = self._ready_reranker()
        proc.stdout.readline.side_effect = [
            json.dumps({"type": "scores", "scores": [0.9, 0.3, 0.6]}),
        ]
        assert r._predict_scores([("q", "a"), ("q", "b"), ("q", "c")]) == [0.9, 0.3, 0.6]

    def test_worker_error_message(self):
        """【降级】worker 返回 error 消息 → 返回 None"""
        r, proc = self._ready_reranker()
        proc.stdout.readline.side_effect = [
            json.dumps({"type": "error", "error": "predict crashed"}),
        ]
        assert r._predict_scores([("q", "a")]) is None

    def test_no_response_subprocess_exited(self):
        """【降级】子进程退出（stdout EOF）→ 返回 None 并标记 _init_failed"""
        r, proc = self._ready_reranker()
        # poll 仍返回 None（进程"看似存活"），但 readline 返回 EOF → 走 no_response 分支
        proc.stdout.readline.side_effect = [""]
        assert r._predict_scores([("q", "a")]) is None
        assert r._init_failed is True

    def test_init_failed_returns_none(self):
        """【降级】_init_failed=True 时直接返回 None，不发请求"""
        r = ToolReranker()
        r._init_failed = True
        assert r._predict_scores([("q", "a")]) is None

    def test_exception_during_io(self):
        """【降级】stdin 写入抛异常 → 返回 None"""
        r, proc = self._ready_reranker()
        proc.stdin.write.side_effect = BrokenPipeError("pipe closed")
        assert r._predict_scores([("q", "a")]) is None
        assert r._init_failed is True


# ════════════════════════════════════════════════════════════
#  rerank 接口
# ════════════════════════════════════════════════════════════

class TestRerank:
    """rerank 正常排序 + 阈值过滤 + 降级"""

    def test_empty_candidates(self):
        """【边界】空候选 → 返回空列表"""
        r = ToolReranker()
        assert r.rerank("q", [], tool_descriptions={}) == []

    def test_worker_unavailable_degrade(self):
        """【降级】worker 不可用 → 返回原顺序 rerank_score=0.0"""
        r = ToolReranker()
        r._init_failed = True  # 模拟 worker 不可用
        result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        assert result == [("tool_search", 0.82, 0.0), ("tool_retrieve", 0.65, 0.0),
                          ("tool_summarize", 0.50, 0.0), ("tool_translate", 0.32, 0.0)]

    def test_predict_failed_degrade(self):
        """【降级】predict 失败 → 返回原顺序 rerank_score=0.0"""
        r = ToolReranker()
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=None):
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        assert result == [("tool_search", 0.82, 0.0), ("tool_retrieve", 0.65, 0.0),
                          ("tool_summarize", 0.50, 0.0), ("tool_translate", 0.32, 0.0)]

    def test_scores_length_mismatch_degrade(self):
        """【降级】scores 数量与候选不匹配 → 返回原顺序"""
        r = ToolReranker()
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=[0.9]):  # 只有 1 个
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        assert result[0][2] == 0.0  # 全部 0.0

    def test_normal_rerank_sorted(self):
        """【正常】按 rerank_score 降序排序"""
        r = ToolReranker()
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=[0.2, 0.9, 0.5, 0.1]):
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        # 0.9 → tool_retrieve(0.65), 0.5 → tool_summarize(0.50), 0.2 → tool_search(0.82),
        # 0.1 → tool_translate(0.32)；全部高于默认阈值 0.05，按分数降序
        assert [t for t, _, _ in result] == ["tool_retrieve", "tool_summarize",
                                             "tool_search", "tool_translate"]
        assert result[0][2] == 0.9
        assert result[-1][2] == 0.1

    def test_threshold_filter(self):
        """【变易】低于 rerank_min_score 的候选被剔除"""
        r = ToolReranker(rerank_min_score=0.5)
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=[0.2, 0.9, 0.5, 0.1]):
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        assert all(s >= 0.5 for _, _, s in result)
        assert result[0][0] == "tool_retrieve"  # 0.9

    def test_threshold_disabled_negative(self):
        """【变易】负数阈值 → 禁用过滤，全量返回"""
        r = ToolReranker(rerank_min_score=-1.0)
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=[0.2, 0.9, 0.5, 0.1]):
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        assert len(result) == 4  # 全部通过

    def test_top_k_truncation(self):
        """【边界】top_k 截断返回数量"""
        r = ToolReranker()
        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", return_value=[0.9, 0.8, 0.7, 0.6]):
            result = r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS,
                              top_k=2)
        assert len(result) == 2

    def test_rerank_top_n_pool(self):
        """【变易】只对前 rerank_top_n 个候选打分"""
        r = ToolReranker(rerank_top_n=2)
        with patch.object(r, "_ensure_worker", return_value=True) as mock_ensure, \
             patch.object(r, "_predict_scores", return_value=[0.9, 0.8]) as mock_predict:
            r.rerank("查询", _SAMPLE_CANDIDATES, tool_descriptions=_SAMPLE_DESCRIPTIONS)
        # 只对前 2 个候选构造 pairs（不直接断言 pairs 内容，验证 predict 调用 1 次）
        assert mock_ensure.call_count == 1
        assert mock_predict.call_count == 1

    def test_description_fallback_to_name(self):
        """【简易】description 缺失时用 tool_name 兜底"""
        r = ToolReranker(rerank_top_n=1)
        captured = {}

        def fake_predict(pairs):
            captured["pairs"] = pairs
            return [0.9]

        with patch.object(r, "_ensure_worker", return_value=True), \
             patch.object(r, "_predict_scores", side_effect=fake_predict):
            r.rerank("查询", [("no_desc_tool", 0.9)], tool_descriptions={})
        assert captured["pairs"] == [("查询", "no_desc_tool")]  # 用 tool_name 兜底


# ════════════════════════════════════════════════════════════
#  环境变量解析
# ════════════════════════════════════════════════════════════

class TestEnvParsing:
    """环境变量解析失败回退默认值"""

    def test_env_float_valid(self, monkeypatch):
        monkeypatch.setenv("TEST_FLOAT", "0.42")
        assert _env_float("TEST_FLOAT", 0.05) == 0.42

    def test_env_float_invalid(self, monkeypatch):
        """【降级】非法 float 值 → 回退默认"""
        monkeypatch.setenv("TEST_FLOAT", "abc")
        assert _env_float("TEST_FLOAT", 0.05) == 0.05

    def test_env_float_missing(self):
        """【边界】环境变量不存在 → 回退默认"""
        assert _env_float("NONEXISTENT_FLOAT_XYZ", 0.05) == 0.05

    def test_env_int_valid(self, monkeypatch):
        monkeypatch.setenv("TEST_INT", "20")
        assert _env_int("TEST_INT", 5) == 20

    def test_env_int_invalid(self, monkeypatch):
        """【降级】非法 int 值 → 回退默认"""
        monkeypatch.setenv("TEST_INT", "not-an-int")
        assert _env_int("TEST_INT", 5) == 5


# ════════════════════════════════════════════════════════════
#  单例
# ════════════════════════════════════════════════════════════

class TestSingleton:
    """get_tool_reranker 环境变量开关"""

    def test_disabled_by_env(self, monkeypatch):
        """【变易】AGENT_HYBRID_RERANKER != 1 → 返回 None"""
        monkeypatch.setenv("AGENT_HYBRID_RERANKER", "0")
        reset_tool_reranker()
        assert get_tool_reranker() is None

    def test_enabled_by_env(self, monkeypatch):
        """【变易】AGENT_HYBRID_RERANKER == 1 → 返回实例"""
        monkeypatch.setenv("AGENT_HYBRID_RERANKER", "1")
        reset_tool_reranker()
        try:
            inst = get_tool_reranker()
            assert inst is not None
            assert isinstance(inst, ToolReranker)
        finally:
            reset_tool_reranker()


# ════════════════════════════════════════════════════════════
#  生命周期
# ════════════════════════════════════════════════════════════

class TestLifecycle:
    """health / close"""

    def test_health_no_worker(self):
        """【边界】未启动 worker 时 health 报告不可用"""
        r = ToolReranker()
        h = r.health()
        assert h["ok"] is False
        assert h["worker_alive"] is False
        assert h["init_failed"] is False
        assert h["model"] == "BAAI/bge-reranker-v2-m3"
        assert h["rerank_min_score"] == _DEFAULT_RERANK_MIN_SCORE
        assert h["rerank_top_n"] == _DEFAULT_RERANK_TOP_N

    def test_health_after_ready(self):
        """【正常】worker 就绪后 health 报告可用"""
        proc = _make_mock_proc(json.dumps({"type": "ready", "load_time_sec": 0.1,
                                           "load_source": "local"}))
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is True
            assert r.health()["ok"] is True

    def test_close_graceful(self):
        """【正常】close 发送 exit 消息并回收子进程"""
        proc = _make_mock_proc(json.dumps({"type": "ready", "load_time_sec": 0.1,
                                           "load_source": "local"}))
        proc.wait.return_value = 0  # 2s 内正常退出
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is True
            r.close()
            # 发送过 exit 消息
            writes = [c.args[0] for c in proc.stdin.write.call_args_list]
            assert any("exit" in w for w in writes)
            assert r._proc is None

    def test_close_timeout_kill(self):
        """【边界】2s 未退出 → 强制 kill"""
        proc = _make_mock_proc(json.dumps({"type": "ready", "load_time_sec": 0.1,
                                           "load_source": "local"}))
        proc.wait.side_effect = __import__("subprocess").TimeoutExpired("wait", 2)
        with patch("subprocess.Popen", return_value=proc):
            r = ToolReranker()
            assert r._ensure_worker() is True
            r.close()
            proc.kill.assert_called_once()
            assert r._proc is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
