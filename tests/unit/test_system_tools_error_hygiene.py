# -*- coding: utf-8 -*-
"""system_tools 插件的错误卫生（CodeQL B2 第五批）。

这个文件的告警不是 `str(e)` 直接造成的，而是 `jsonify(result)` 里**助手函数返回的 dict
带异常原文**（taint 穿过函数边界）。做法：
  1. 助手层（agent/system_tools.py）不再把异常文本写进返回的 error 字段；
  2. 插件出口再过一道 `_sanitize`（只动 error/check_error/detail/reason 四个键）。
本用例守第 2 道，以及"不要再写回 str(e)"的静态锁。
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _mod():
    from plugins import system_tools
    return system_tools


def test_sanitize_脱敏嵌套字段():
    mod = _mod()
    payload = {
        "ok": False,
        "error": "Traceback: boom",
        "detail": {"reason": "os error 123"},
        "items": [{"error": "nested boom", "name": "a"}],
        "path": "/tmp/x",          # 非敏感键：原样保留
        "count": 3,
    }
    out = mod._sanitize(payload)
    assert out["error"] == "执行失败（详见服务日志）"
    assert out["detail"]["reason"] == "执行失败（详见服务日志）"
    assert out["items"][0]["error"] == "执行失败（详见服务日志）"
    assert out["items"][0]["name"] == "a"
    assert out["path"] == "/tmp/x" and out["count"] == 3


def test_sanitize_不动非字符串():
    mod = _mod()
    out = mod._sanitize({"error": None, "check_error": 0, "ok": True})
    assert out == {"error": None, "check_error": 0, "ok": True}


def test_插件不再回吐异常原文():
    text = (ROOT / "plugins" / "system_tools.py").read_text(encoding="utf-8")
    for pat in ('"error": str(e)', "error=str(e)", 'f"沙盒模块加载失败: {e}"',
                'f"沙盒执行引擎异常: {e}"'):
        assert pat not in text, "plugins/system_tools.py 又出现 %s" % pat


def test_助手层不再把异常文本写进返回体():
    text = (ROOT / "agent" / "system_tools.py").read_text(encoding="utf-8")
    assert '"error": str(e)' not in text
    assert 'f"剪贴板读取失败: {e}"' not in text
    assert 'f"剪贴板写入失败: {e}"' not in text
    assert 'f"未知错误: {e}"' not in text
    assert 'error = f"{type(e).__name__}: {e}"' not in text
