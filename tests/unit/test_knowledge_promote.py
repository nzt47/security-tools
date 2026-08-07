"""产卡对接单元测试（任务3 → 任务2：Note → Card）。

验收线（不易）：
- 已确认（approved）+ 真提炼（distilled=True）笔记才能产卡（人机边界：
  AI 不自动产卡，须人工 approve）
- 未确认 / 降级骨架笔记拒绝产卡（抛 ValueError，不静默降级）
- one_line_insight → Card.insight、suggested_links → links + 正文 [[双链]]
- 同 slug 卡片已存在 → CardConflictError（不覆盖契约）
- 消歧 slug（同标题不同素材）产卡成功（explicit_slug 豁免）
"""
import hashlib
import json
from pathlib import Path

import pytest

from agent.knowledge.card import CardConflictError, CardStore
from agent.knowledge.distill import (
    Note,
    approve_note,
    card_from_note,
    distill,
    promote_to_card,
)
from agent.knowledge.links import parse_links
from agent.knowledge.schema import Card, slugify

VALID_LLM_JSON = json.dumps({
    "core_points": ["观点一：上下文工程比提示词更重要", "观点二：记忆分层存储"],
    "knowledge_points": ["知识点：双链引用约定", "知识点：幂等去重原则"],
    "inspirations": ["启发：可借鉴分层架构"],
    "counter_examples": ["反例：一次性任务不应过度分层"],
    "suggested_links": ["概念-上下文工程", "概念-知识图谱"],
    "one_line_insight": "知识系统应以降噪为核心设计目标",
}, ensure_ascii=False)


class FakeLLM:
    """mock LLM：复用 memory.llm_service.LLMService.chat 签名。"""

    def __init__(self, response=None, exc=None, model="mock-gpt"):
        self.response = response
        self.exc = exc
        self.model = model
        self.calls = 0

    def chat(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.response


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_source(kb: Path, name: str = "note.md", content: str = "# 素材\n\n正文内容",
                 title=None, sensitive: bool = False) -> Path:
    """在临时 knowledge 根下构造 inbox 素材 + meta（模拟任务1 产物）。"""
    inbox = kb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    src = inbox / name
    raw = content.encode("utf-8")
    src.write_bytes(raw)
    meta = {
        "sha256": _sha256_bytes(raw),
        "sensitive": sensitive,
        "title": title,  # None 时 distill 回退到文件名 stem
    }
    src.with_name(name + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return src


def _distill_approved(kb: Path, name: str = "note.md", title=None,
                      content: str = "# 素材\n\n正文内容") -> Note:
    """端到端：distill（LLM 成功）→ approve，返回已确认笔记。"""
    src = _make_source(kb, name=name, title=title, content=content)
    note = distill(str(src), llm=FakeLLM(response=VALID_LLM_JSON),
                   knowledge_root=str(kb))
    assert approve_note(note.slug, knowledge_root=str(kb)), "approve 应成功"
    return note


# ════════════════════════════════════════════════════════════
#  正常产卡
# ════════════════════════════════════════════════════════════

def test_promote_normal_success(tmp_path):
    kb = tmp_path / "kb"
    note = _distill_approved(kb, title="驾驭工程")

    card = promote_to_card(note.slug, knowledge_root=str(kb))

    assert isinstance(card, Card)
    assert card.title == "驾驭工程"
    assert card.slug == note.slug == slugify("驾驭工程")
    assert card.status == "draft"  # 产卡后 draft，须人工 transition 转 current
    assert card.type == "concepts"
    assert card.source == "inbox/note.md"
    assert card.date == note.distill_date
    assert card.insight == "知识系统应以降噪为核心设计目标"  # 任务7 映射
    assert card.links == ["概念-上下文工程", "概念-知识图谱"]
    assert card.metadata["source_hash"] == note.source_hash
    assert card.metadata["distilled"] is True
    assert card.metadata["llm_model"] == "mock-gpt"

    # 落盘 + index/log 同步（CardStore.create 契约）
    wiki = kb / "wiki"
    assert (wiki / "concepts" / f"{card.slug}.md").is_file()
    index_text = (kb / "index.md").read_text(encoding="utf-8")
    assert f"[[{card.slug}]]" in index_text
    assert "知识系统应以降噪为核心设计目标" in index_text
    log_text = (kb / "log.md").read_text(encoding="utf-8")
    assert "create" in log_text


def test_promote_body_contains_double_links(tmp_path):
    kb = tmp_path / "kb"
    note = _distill_approved(kb)
    card = promote_to_card(note.slug, knowledge_root=str(kb))

    assert "[[概念-上下文工程]]" in card.content
    assert "[[概念-知识图谱]]" in card.content
    assert "## 核心观点" in card.content
    assert "## 反面案例" in card.content
    # 双向链接一致性契约：links 与正文双链解析结果一致
    assert parse_links(card.content) == card.links


def test_promote_custom_card_type(tmp_path):
    kb = tmp_path / "kb"
    note = _distill_approved(kb)
    card = promote_to_card(note.slug, card_type="insights", knowledge_root=str(kb))
    assert card.type == "insights"
    assert (kb / "wiki" / "insights" / f"{card.slug}.md").is_file()


# ════════════════════════════════════════════════════════════
#  前置条件拒绝（人机边界）
# ════════════════════════════════════════════════════════════

def test_promote_rejects_unapproved(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    note = distill(str(src), llm=FakeLLM(response=VALID_LLM_JSON),
                   knowledge_root=str(kb))
    assert note.status == "draft"  # 未 approve

    with pytest.raises(ValueError, match="未确认"):
        promote_to_card(note.slug, knowledge_root=str(kb))


def test_promote_rejects_skeleton_even_if_approved(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    note = distill(str(src), llm=None, knowledge_root=str(kb))  # 离线降级
    assert note.distilled is False
    assert approve_note(note.slug, knowledge_root=str(kb))  # 即便被确认

    with pytest.raises(ValueError, match="骨架"):
        promote_to_card(note.slug, knowledge_root=str(kb))


def test_promote_note_not_found(tmp_path):
    kb = tmp_path / "kb"
    with pytest.raises(FileNotFoundError):
        promote_to_card("不存在的笔记", knowledge_root=str(kb))


def test_promote_conflict_does_not_overwrite(tmp_path):
    kb = tmp_path / "kb"
    note = _distill_approved(kb, title="驾驭工程")
    # 先手动创建同 slug 卡片
    CardStore(kb / "wiki").create(card_from_note(note))

    with pytest.raises(CardConflictError):
        promote_to_card(note.slug, knowledge_root=str(kb))


# ════════════════════════════════════════════════════════════
#  消歧 slug（同标题不同素材）
# ════════════════════════════════════════════════════════════

def test_promote_disambiguated_slug(tmp_path):
    kb = tmp_path / "kb"
    n1 = _distill_approved(kb, name="a.md", title="同题素材")
    n2 = _distill_approved(kb, name="b.md", title="同题素材",
                           content="# 素材\n\n另一篇不同的正文。")
    assert n2.slug == f"{n1.slug}-2"  # 笔记文件消歧

    card1 = promote_to_card(n1.slug, knowledge_root=str(kb))
    card2 = promote_to_card(n2.slug, knowledge_root=str(kb))
    assert card2.slug == n2.slug  # 保留消歧 slug（explicit_slug 豁免）
    assert card1.slug == n1.slug
    # 两张卡互不覆盖，各自落盘
    assert (kb / "wiki" / "concepts" / f"{n1.slug}.md").is_file()
    assert (kb / "wiki" / "concepts" / f"{n2.slug}.md").is_file()


# ════════════════════════════════════════════════════════════
#  纯映射（card_from_note）
# ════════════════════════════════════════════════════════════

def test_card_from_note_mapping():
    note = Note(
        title="测试笔记", slug="测试笔记", source="inbox/a.md",
        source_hash="abc123", distilled=True,
        core_points=["点1"], knowledge_points=["知1"], inspirations=["启1"],
        counter_examples=["反1"], suggested_links=["概念-b"],
        one_line_insight="一句话洞见", llm_model="m1", confidence=0.8,
        distill_date="2026-08-02", status="approved",
    )
    card = card_from_note(note, card_type="insights")

    assert card.insight == "一句话洞见"
    assert card.links == ["概念-b"]
    assert card.type == "insights"
    assert card.status == "draft"  # 不继承笔记 approved，产卡后固定 draft
    assert card.date == "2026-08-02"
    assert card.metadata["confidence"] == 0.8
    assert card.metadata["source_hash"] == "abc123"
    assert "一句话洞见" in card.content
    assert "[[概念-b]]" in card.content


# ════════════════════════════════════════════════════════════
#  CLI 对接（python -m agent.knowledge card-from-note）
# ════════════════════════════════════════════════════════════

def test_promote_cli_command(tmp_path, capsys):
    from agent.knowledge.__main__ import main as cli_main

    # 已确认笔记 → 产卡成功（rc=0）
    kb = tmp_path / "kb"
    note = _distill_approved(kb, title="驾驭工程")
    rc = cli_main(["card-from-note", note.slug,
                   "--wiki", str(kb / "wiki"), "--knowledge", str(kb)])
    assert rc == 0
    assert "产卡成功" in capsys.readouterr().out
    assert (kb / "wiki" / "concepts" / f"{note.slug}.md").is_file()

    # 未确认笔记 → rc=1（stderr 报原因）
    kb2 = tmp_path / "kb2"
    src = _make_source(kb2)
    note2 = distill(str(src), llm=FakeLLM(response=VALID_LLM_JSON),
                    knowledge_root=str(kb2))
    rc = cli_main(["card-from-note", note2.slug,
                   "--wiki", str(kb2 / "wiki"), "--knowledge", str(kb2)])
    assert rc == 1
    assert "产卡失败" in capsys.readouterr().err
