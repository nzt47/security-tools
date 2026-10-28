# -*- coding: utf-8 -*-
"""「立即压缩一次」运维脚本的判定口径（scripts/compress_memory_now.py）。

脚本本身只做 HTTP + 回读，真正会出错的是**判定**：服务回报 ok=true 不等于摘要变好
（#1336 之前就是"200 + 0 字节落盘"）。这里把四种结局钉死。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def _load_script():
    path = ROOT / "scripts" / "compress_memory_now.py"
    spec = importlib.util.spec_from_file_location("compress_memory_now", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_evaluate_ok_when_version_advances_and_summary_non_empty():
    ok, reason = _load_script().evaluate({"summary_version": 88, "summary_bytes": 0},
                                         {"summary_version": 89, "summary_bytes": 420})
    assert ok is True and "v88 → v89" in reason


def test_evaluate_fails_when_summary_stays_empty():
    ok, reason = _load_script().evaluate({"summary_version": 88, "summary_bytes": 0},
                                         {"summary_version": 88, "summary_bytes": 0})
    assert ok is False and "0 字节" in reason


def test_evaluate_fails_when_version_frozen():
    ok, reason = _load_script().evaluate({"summary_version": 88, "summary_bytes": 17},
                                         {"summary_version": 88, "summary_bytes": 17})
    assert ok is False and "没有前进" in reason


def test_evaluate_fails_when_summary_file_missing():
    ok, reason = _load_script().evaluate({"summary_version": 1, "summary_bytes": 10},
                                         {"summary_version": None, "summary_bytes": None})
    assert ok is False and "无法自证" in reason
