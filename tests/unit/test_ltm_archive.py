# -*- coding: utf-8 -*-
"""压缩摘要 → 长期记忆库归档（agent/memory/ltm_archive.py，2026-11-13）。

【为什么】单位看板一直把 memory.longterm 记成 never：长期记忆库 0 行。
但压缩摘要在 data/summary*.txt 里一直有产出 —— 缺的是"写进库"这一步。
本文件钉住：只读源文件、跳过空摘要、幂等、dry-run 不写。
"""
from __future__ import annotations

import sqlite3

from agent.memory import ltm_archive as arch


def _write(path, text):
    path.write_text(text, encoding="utf-8")


def test_发现摘要_跳过空文件(tmp_path):
    _write(tmp_path / "summary.txt", "全局摘要")
    _write(tmp_path / "summary_version.txt", "7")
    _write(tmp_path / "summary_abc.txt", "   ")
    _write(tmp_path / "summary_abc.version.txt", "3")

    rows = arch.discover_summaries(tmp_path)

    assert [r["name"] for r in rows] == ["summary.txt", "summary_abc.txt"]
    assert rows[0]["version"] == 7 and rows[0]["empty"] is False
    assert rows[1]["empty"] is True


def test_dry_run不写库(tmp_path):
    _write(tmp_path / "summary.txt", "abc")
    out = arch.archive(db_path=tmp_path / "lt.db", summary_dir=tmp_path, dry_run=True)

    assert out["archived"] == 1 and out["rows_after"] is None
    assert not (tmp_path / "lt.db").exists()


def test_归档写库且幂等(tmp_path):
    _write(tmp_path / "summary.txt", "会话摘要正文")
    _write(tmp_path / "summary_version.txt", "2")
    db = tmp_path / "lt.db"

    first = arch.archive(db_path=db, summary_dir=tmp_path)
    # 首次归档前库还不存在 ⇒ rows_before 是 None（"还没建库"不是 0 行）
    assert first["archived"] == 1 and first["rows_after"] == 1

    second = arch.archive(db_path=db, summary_dir=tmp_path)
    assert second["archived"] == 0 and second["skipped_same"] == 1


def test_只归档非空摘要(tmp_path):
    _write(tmp_path / "summary.txt", "")
    out = arch.archive(db_path=tmp_path / "lt.db", summary_dir=tmp_path)
    assert out["archived"] == 0 and out["skipped_empty"] == 1


def test_库不可写时返回计数不抛(tmp_path):
    """把 db 指到一个目录 ⇒ 归档失败只记日志，函数仍然返回。"""
    _write(tmp_path / "summary.txt", "abc")
    bad = tmp_path / "as_dir"
    bad.mkdir()
    out = arch.archive(db_path=bad, summary_dir=tmp_path)
    assert out["ok"] is True and out["archived"] == 0
