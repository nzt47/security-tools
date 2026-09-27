# -*- coding: utf-8 -*-
"""外部会话 -> 知识卡片 转换器单测（agent/knowledge/convert.py）。

重点守护：
  1. 产物必须能通过文档库的 _md_to_card 解析与 validate_card 校验
     （insight 必填、slug == slugify(title)、type/status 白名单）
  2. 去重后缀不得用数字 —— slugify 会剥除尾部 '-数字'，用数字会导致仍然冲突
"""
from __future__ import annotations

import json
import os

import pytest

from agent.knowledge.convert import (_card_text, _first_paragraph, _slugify,
                                     _unique_slug, convert_trae)
from agent.knowledge.card import _md_to_card
from agent.knowledge.schema import validate_card

zstd = pytest.importorskip("zstandard")


def test_unique_slug_never_uses_numeric_suffix():
    """slugify 会循环剥除尾部 '-数字'（保幂等），故去重后缀必须非纯数字。"""
    used = set()
    t1 = _unique_slug("配置校验架构不一致", used)
    t2 = _unique_slug("配置校验架构不一致", used)
    assert _slugify(t1) != _slugify(t2), "同题必须得到不同 slug"
    import re as _re
    assert not _re.search(r"-\d+$", _slugify(t2)), \
        "去重后缀不得是纯数字（会被 slugify 剥掉）"


def test_card_text_has_all_required_fields():
    text = _card_text("测试标题", "dsh:s1#turn1", "2026-09-27", "正文",
                      insight="一句话", scope="适用边界")
    for k in ("title:", "slug:", "status:", "type:", "source:", "date:",
              "insight:", "scope:"):
        assert k in text, k
    # 必须能往返解析
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.md"
        p.write_text(text, encoding="utf-8")
        card = _md_to_card(p, p.read_text(encoding="utf-8"))
    assert validate_card(card.__dict__) == [], validate_card(card.__dict__)
    assert card.status == "draft" and card.type == "insights"


def test_card_text_insight_placeholder_when_empty():
    """未提供洞见时必须落占位而非空串 —— 空串会触发「缺少一句话核心洞见」。"""
    text = _card_text("t", "s", "2026-01-01", "b")
    assert "待人工补充" in text


def test_first_paragraph_skips_heading_and_code():
    doc = "# 1. 问题\n\n" + chr(96)*3 + "python\nx=1\n" + chr(96)*3 + "\n\n真正的第一段。\n"
    assert _first_paragraph(doc) == "真正的第一段。"


def test_convert_trae_end_to_end(tmp_path):
    src = tmp_path / "trae" / "abc123" / "documents"
    src.mkdir(parents=True)
    (src / "重构洞察-重复实现.md").write_text(
        "# 1. 问题\n\n存在两套重复实现，维护成本高。\n\n## 1.1 细节\n- a\n",
        encoding="utf-8")
    (src / "重构洞察-单例不一致.md").write_text(
        "# 1. 问题\n\n单例管理不一致。\n", encoding="utf-8")
    out = tmp_path / "staging"
    r = convert_trae(str(tmp_path / "trae"), str(out))
    assert r["written"] == 2 and r["blocked"] == 0

    ok = 0
    for p in sorted(out.glob("*.md")):
        card = _md_to_card(p, p.read_text(encoding="utf-8"))
        assert validate_card(card.__dict__) == []
        assert card.source.startswith("trae:")
        assert card.insight and "待人工补充" not in card.insight, "Trae 文档应能抽出真实洞见"
        ok += 1
    assert ok == 2


def test_convert_trae_blocks_hard_block(tmp_path):
    src = tmp_path / "trae" / "h" / "documents"
    src.mkdir(parents=True)
    (src / "含密钥.md").write_text(
        "# t\n\nkey: -----BEGIN RSA PRIVATE KEY-----\n", encoding="utf-8")
    out = tmp_path / "staging"
    r = convert_trae(str(tmp_path / "trae"), str(out))
    assert r["blocked"] == 1 and r["written"] == 0
    assert not list(out.glob("*.md")), "硬阻断内容不得落盘"


def _mk_session(dirpath, records):
    os.makedirs(dirpath, exist_ok=True)
    p = os.path.join(dirpath, "session.jsonl.zstd")
    c = zstd.ZstdCompressor()
    with open(p, "wb") as fh:
        for r in records:
            fh.write(c.compress((json.dumps(r, ensure_ascii=False) + "\n").encode()))
    return p


def test_convert_dsh_end_to_end(tmp_path):
    root = tmp_path / "sessions" / "--ws--" / "sess-1"
    recs = [
        {"type": "session", "cwd": str(tmp_path), "id": "s1"},
        {"type": "user/message", "seq": 1, "time": "2026-09-20T10:00:00",
         "data": {"content": "修复 pytest 断言失败并补回归测试", "source": {"kind": "user"}}},
        {"type": "turn/start", "seq": 2, "time": "2026-09-20T10:00:01", "data": {"turn": 1}},
        {"type": "tool/call", "seq": 3, "time": "2026-09-20T10:00:02",
         "data": {"turn": 1, "step": 1, "callId": "c1", "name": "edit",
                  "arguments": json.dumps({"file_path": str(tmp_path / "a.py"),
                                           "old_string": "x=1", "new_string": "x=2"})}},
        {"type": "turn/end", "seq": 4, "time": "2026-09-20T10:00:03", "data": {"turn": 1}},
    ]
    _mk_session(str(root), recs)
    out = tmp_path / "staging"
    from agent.knowledge.convert import convert_dsh
    r = convert_dsh(str(tmp_path / "sessions"), str(out))
    assert r["written"] == 1
    p = sorted(out.glob("*.md"))[0]
    card = _md_to_card(p, p.read_text(encoding="utf-8"))
    assert validate_card(card.__dict__) == []
    assert card.source.startswith("dsh:")
    assert "pytest" in card.content
    assert card.metadata.get("lang") == "python"
