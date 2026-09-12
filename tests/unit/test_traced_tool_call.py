"""`traced_tool_call` 单元测试 —— 真用前置 D3（2026-09-13）

【背景】`ToolTraceRecorder` 原先只装在
`agent.tool_calling.ToolCallingService._execute_safe` 里，而
`agent.workflow_learning.executor` 走的是**注入的 tool_executor**
（orchestrator 注入的正是 `agent.tools.call`）⇒ 工作流回放路径的工具调用
**一条工具级轨迹都不落**。
实测证据（2026-09-13）：`list_directory` 真实执行并返回 195 项，
`tool_traces` 行数不变（停在 1052，最后写入 09-12 17:15），
`unified_traces` 只有任务级行（`capability_id` 为空）。

本文件锁死两件事：
    1. `traced_tool_call` 的记录语义（成功/失败/异常/记录器不可用四态）；
    2. **接线不回退**：`workflow_learning/executor.py` 必须真的调用它
       （用 AST 检查，与既有 `test_skill_output_guard.py` 的元测试同款手法）。
"""
from __future__ import annotations

import ast
import pathlib
import sqlite3

import pytest

from agent.observability.tool_trace import (
    HIGH_FREQ_TOOLS,
    ToolTraceRecorder,
    traced_tool_call,
)

#: 取样工具名：**必须不在** HIGH_FREQ_TOOLS 里，否则会被 10% 采样随机丢弃，
#: 让用例变成 flaky。这里显式断言这一点（见 test_probe_tool_not_sampled）。
PROBE_TOOL = "d3_probe_tool"


def _rows(db_path: str):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT tool_name, success, error_type, input_hash, output_hash "
            "FROM tool_traces ORDER BY rowid"
        ).fetchall()
    finally:
        conn.close()


@pytest.fixture()
def recorder(tmp_path):
    """把单例替换成临时库上的记录器（沿用 test_tool_trace.py 的既有做法）"""
    db = str(tmp_path / "traced_call.db")
    ToolTraceRecorder.reset()
    rec = ToolTraceRecorder(db_path=db)
    ToolTraceRecorder._instance = rec
    yield rec, db
    ToolTraceRecorder.reset()


def test_probe_tool_not_sampled():
    """前提校验：探针工具名不在高频采样名单里，否则下面的行数断言会 flaky"""
    assert PROBE_TOOL not in HIGH_FREQ_TOOLS


def test_traced_tool_call_records_success(recorder):
    """成功调用 → 恰好 1 行，success=1，无 error_type"""
    rec, db = recorder
    out = traced_tool_call(PROBE_TOOL, {"path": "."}, lambda: {"ok": True, "n": 195})
    assert out == {"ok": True, "n": 195}
    assert rec.flush(timeout=5.0) is True
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0][0] == PROBE_TOOL
    assert rows[0][1] == 1          # success
    assert rows[0][2] == ""         # error_type


def test_traced_tool_call_marks_ok_false_as_failure(recorder):
    """工具返回 ok=False（无异常）→ success=0 且 error_type=ToolError

    与 `ToolTraceRecorder.finish_trace` 的既有语义保持一致。
    """
    rec, db = recorder
    traced_tool_call(PROBE_TOOL, {}, lambda: {"ok": False, "error": "boom"})
    assert rec.flush(timeout=5.0) is True
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0][1] == 0
    assert rows[0][2] == "ToolError"


def test_traced_tool_call_records_and_reraises_exception(recorder):
    """工具抛异常 → 记 1 行 error_type=异常类名，并把异常**原样抛回**（不吞）"""
    rec, db = recorder

    def _boom():
        raise ValueError("工具炸了")

    with pytest.raises(ValueError, match="工具炸了"):
        traced_tool_call(PROBE_TOOL, {}, _boom)
    assert rec.flush(timeout=5.0) is True
    rows = _rows(db)
    assert len(rows) == 1
    assert rows[0][1] == 0
    assert rows[0][2] == "ValueError"


def test_traced_tool_call_survives_recorder_failure(monkeypatch):
    """记录器不可用 → **绝不**影响工具执行（守【不易】：观测不得阻断主路径）"""
    def _broken_instance(cls):  # noqa: ANN001
        raise RuntimeError("记录器构造失败")

    monkeypatch.setattr(ToolTraceRecorder, "instance", classmethod(_broken_instance))
    out = traced_tool_call(PROBE_TOOL, {}, lambda: {"ok": True})
    assert out == {"ok": True}


def test_traced_tool_call_still_raises_when_finish_trace_broken(monkeypatch, recorder):
    """finish_trace 抛错不得掩盖工具自身的异常"""
    rec, _db = recorder

    def _broken_finish(self, ctx, output_data, exception):  # noqa: ANN001
        raise RuntimeError("写入炸了")

    monkeypatch.setattr(ToolTraceRecorder, "finish_trace", _broken_finish)

    def _boom():
        raise KeyError("原始异常")

    with pytest.raises(KeyError, match="原始异常"):
        traced_tool_call(PROBE_TOOL, {}, _boom)


def test_traced_tool_call_hashes_input_not_plaintext(recorder):
    """入参只落脱敏哈希，不落明文（与既有纪律一致）"""
    rec, db = recorder
    secret = "sk-super-secret-token"
    traced_tool_call(PROBE_TOOL, {"api_key": secret}, lambda: {"ok": True})
    assert rec.flush(timeout=5.0) is True
    conn = sqlite3.connect(db)
    try:
        blob = " ".join(str(r) for r in conn.execute("SELECT * FROM tool_traces"))
    finally:
        conn.close()
    assert secret not in blob


# ════════════════════════════════════════════════════════════
#  接线不回退（AST 检查，与 test_skill_output_guard.py 元测试同款手法）
# ════════════════════════════════════════════════════════════

_EXECUTOR_SRC = (
    pathlib.Path(__file__).resolve().parents[2]
    / "agent" / "workflow_learning" / "executor.py"
)


def test_workflow_executor_calls_traced_tool_call():
    """`workflow_learning/executor.py` 必须真的调用 `traced_tool_call`

    否则工作流回放路径的工具调用又会静默不进工具级台账（D3 复发）。
    """
    src = _EXECUTOR_SRC.read_text(encoding="utf-8")
    tree = ast.parse(src)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "traced_tool_call" in calls, (
        "workflow_learning/executor.py 未调用 traced_tool_call —— "
        "工作流回放的工具调用将不落工具级轨迹（真用前置 D3 复发）"
    )


def test_meta_check_detects_removed_wiring():
    """元测试：去掉接线后，上面的 AST 检查必须失败（证明检查真的有效）"""
    src = _EXECUTOR_SRC.read_text(encoding="utf-8")
    tampered = src.replace(
        "output = traced_tool_call(", "output = _disabled_traced_tool_call(")
    assert tampered != src, "测试前提失败：未找到待篡改的调用点"
    tree = ast.parse(tampered)
    calls = [
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]
    assert "traced_tool_call" not in calls
