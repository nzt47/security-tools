# -*- coding: utf-8 -*-
"""B2-a：对话插件不再把日志内容打到 stdout，也不再回显 LLM key 前缀。

背景（2026-11-03）：B2 第八批（#1415）之后 `plugins/chat.py` 还剩 5 条 code scanning
告警，其中 2 条是 py/clear-text-logging-sensitive-data：

* `plugins/chat.py:261` —— `for log in logs: print(log)`：`logs` 的条目含请求原文与
  上游错误文本（语音合成失败原因常夹带供应商回显的凭据）；
* `plugins/chat.py:1860` —— `logger.warning(..., api_key[:8] ...)`：key 前缀也是泄漏。

结论（先查清再改，不盲目动主链路）：chat **不是**把 stdout 捕获进返回的 `logs`，
方向相反 —— `logs` 由显式 append 构建，随响应体（:175 / :346）返回给调用方，
同时被 `print` 到控制台。所以改法是「控制台只打摘要」+「上游错误文本进 logger」，
响应体的 `logs` 字段契约不变。

另外 3 条（`855/859/862` 的 `os.path.exists/makedirs/isdir`）是 py/path-injection，
属 C-6-B1 批次，不在本文件的护栏范围。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = (ROOT / "plugins" / "chat.py").read_text(encoding="utf-8")


def test_控制台摘要只回条数不回内容():
    """摘要行不得复述日志条目（否则 stdout 又成了明文 sink）。"""
    from plugins.chat import _console_log_summary

    logs = [
        "[INPUT] 用户输入: sk-live-LEAK123456",
        "[VOICE] 语音合成失败 - 错误: Authorization=Bearer sk-live-LEAK123456",
    ]
    line = _console_log_summary(logs)
    assert "sk-live" not in line and "用户输入" not in line and "语音合成失败" not in line
    assert "2 条" in line


@pytest.mark.parametrize("pat", [
    "print(log)",
    "voice_result.get('error')}",          # 上游错误原文不得进 logs/f-string
    "api_key[:",                            # key 前缀不得进任何 sink
])
def test_不再出现明文日志写法(pat):
    assert pat not in SRC, "plugins/chat.py 里又出现了 %s（见 B2-a：明文日志只留摘要）" % pat
