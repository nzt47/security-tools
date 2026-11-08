# -*- coding: utf-8 -*-
"""工具使用台账（stage=tool）在**对话执行点**的写入 —— P1 `tool-usage-ledger-empty` 回归

【背景（2026-11-08 实测）】台账的唯一写入点原本是 `ToolCalling._execute_safe`，但本部署的
工作台对话直接用 `agent.tools.call` 执行工具（`plugins/chat.py` 的 SSE 工具循环）⇒
`data/skill_usage_events*.jsonl` 自 2026-10-29 上线起 **stage=tool 事件 0 条**：
工具页「近 7 日调用」恒 '-'，C-9 这类"看用量"的判据全部失效。

本用例把"执行点必须记一条"钉死在 `_record_tool_usage` 上（含失败判定与异常吞掉）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(120)


def _read_events(path: Path):
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CP_SKILL_USAGE_LOG", str(path))
    return path


class TestToolUsageAtChatExecutionPoint:

    def test_成功调用记一条_stage_tool(self, ledger):
        from plugins.chat import _record_tool_usage

        _record_tool_usage("read_file", '{"ok": true, "content": "x"}', 12.5)
        events = _read_events(ledger)
        assert len(events) == 1, f"对话执行点没记台账：{events}"
        ev = events[0]
        assert ev["stage"] == "tool"
        assert ev["tool"] == "read_file"
        assert ev["success"] is True
        assert ev["source"] == "workbench_chat"
        assert ev["latency_ms"] == pytest.approx(12.5, abs=0.01)

    def test_失败调用如实标失败并带错误码(self, ledger):
        from plugins.chat import _record_tool_usage

        _record_tool_usage("shell_execute",
                           '{"ok": false, "error_code": "APPROVAL_REQUIRED", "error": "需要审批"}',
                           7.0)
        ev = _read_events(ledger)[0]
        assert ev["success"] is False
        assert ev["extra"]["error_code"] == "APPROVAL_REQUIRED"

    def test_纯文本结果按成功计_不臆造失败(self, ledger):
        from plugins.chat import _record_tool_usage

        _record_tool_usage("web_search", "普通文本结果", 3.0)
        ev = _read_events(ledger)[0]
        assert ev["success"] is True
        assert "error_code" not in (ev.get("extra") or {})

    def test_空工具名不落盘_且写失败不抛(self, ledger, monkeypatch):
        from plugins.chat import _record_tool_usage

        _record_tool_usage("", "x", 1.0)
        assert _read_events(ledger) == [], "空工具名不该写台账"

        # 台账写不进去（只读目录/坏路径）也不能影响对话
        monkeypatch.setenv("CP_SKILL_USAGE_LOG", str(Path(ledger).anchor or "/"))
        _record_tool_usage("read_file", '{"ok": true}', 1.0)  # 不抛即通过

    def test_抛异常时如实记失败_不把崩溃当成功(self, ledger):
        """工具**抛异常**时结果是 `"工具执行失败: …"` 纯文本；
        按"非 JSON ⇒ 成功"推导会把崩溃记成成功 —— 调用方用 ok= 覆盖。"""
        from plugins.chat import _record_tool_usage

        _record_tool_usage("shell_execute", "工具执行失败: TimeoutError", 3.0,
                           ok=False, error_code="TimeoutError")
        ev = _read_events(ledger)[0]
        assert ev["success"] is False
        assert ev["extra"]["error_code"] == "TimeoutError"

    def test_结果判定口径(self):
        from plugins.chat import _tool_result_ok

        assert _tool_result_ok('{"ok": true}') == (True, "")
        assert _tool_result_ok('{"ok": false, "error_code": "X"}') == (False, "X")
        assert _tool_result_ok('{"ok": false, "error": "boom"}') == (False, "boom")
        assert _tool_result_ok("{不是 JSON") == (True, "")
        assert _tool_result_ok("plain") == (True, "")
        assert _tool_result_ok({"ok": False}) == (True, ""), "非字符串按成功，不猜"
