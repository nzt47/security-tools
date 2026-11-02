# -*- coding: utf-8 -*-
"""路由层的「异常原文不进响应体」静态护栏（CodeQL B2 批次）。

为什么要有这条静态锁：B2 的做法是逐个文件把
`except Exception as e: return jsonify({"error": str(e)})` 收口到本地 `_fail(e)`。
手改 80+ 处，最容易在后续迭代里被"顺手加一个新路由又写回 str(e)"悄悄退回去 ——
这条用例就是防这个：**这些文件里不允许再出现 `str(e)`**（日志用 %s 参数化，不受影响）。

范围只收**已清零**的文件；其余文件在 B2 后续批次里逐批加进来。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

#: 已经完成 B2 收口、且必须保持零 `str(e)` 的路由文件。
CLEANED = (
    "agent/server_routes/routes_skills_mgmt.py",
    "plugins/admin.py",
    "plugins/memory.py",
    "plugins/system_tools.py",
)


#: 响应体里"回吐异常原文"的几种写法（注释/文档里提到 str(e) 不算）。
LEAK_PATTERNS = (
    '"error": str(e)',
    "'error': str(e)",
    'error=str(e)',
    '"check_error": str(e)',
    'f"参数错误: {e}"',
)


@pytest.mark.parametrize("rel", CLEANED)
def test_响应体不再回吐异常原文(rel):
    text = (ROOT / rel).read_text(encoding="utf-8")
    for pat in LEAK_PATTERNS:
        assert pat not in text, (
            "%s 里又出现了 %s：响应体只回固定文案，异常原文请交给 logger（exc_info=True）" % (rel, pat))
    assert "def _fail(" in text, "%s 缺少本地 _fail 收口函数" % rel
