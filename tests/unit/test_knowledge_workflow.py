"""人机协同闭环工作流单元测试（任务7）。

验收线（不易）：
- 全流程 happy path：capture→distill→discuss→card→audit 一条命令跑通，
  log.md / index.md 全程同步。
- 任一中间步骤失败（LLM 离线）→ 流程降级到骨架产物，不产生半成品卡片。
- 讨论→产卡关联正确：卡片 metadata.source_card 指向讨论记录，
  one_line_insight→insight、适用边界→scope、矛盾→contradictions（status=conflict）。
- 产卡后状态恒为 draft，人工 transition 前不可转 current（人机边界）。
- 边界护栏：敏感（含 PII）素材不进入 wiki 正文。
- 与 MemoryManager 对话记忆互不污染（各自文件独立）。
"""
import json
import time
from pathlib import Path

import pytest

from agent.knowledge.card import CardStore
from agent.knowledge.discuss import load_discussion
from agent.knowledge.distill import approve_note
from agent.knowledge.workflow import WorkflowRunner

HAPPY_LLM_JSON = json.dumps({
    "core_points": ["观点一：分层降噪是知识库的核心", "观点二：提炼应保留证据"],
    "knowledge_points": ["知识点：frontmatter 契约"],
    "inspirations": ["启发：可借鉴分层架构"],
    "counter_examples": ["反例：一次性记录无需分层"],
    "suggested_links": [],  # happy path 无建议链接 → 审计无断链
    "one_line_insight": "知识库应以降噪为第一设计目标",
}, ensure_ascii=False)

DISCUSS_TEXT = (
    "Q: 分层降噪在什么条件下成立？\n"
    "A: 仅当系统写入频繁且噪声占比高时。\n"
    "[冲突: 概念-即时检索]\n"
    "结论摘要：降噪与检索速度存在权衡，需按场景取舍。"
)

EXTRACT_JSON = json.dumps({
    "one_line_insight": "知识系统降噪与检索速度存在权衡",
    "scope": "仅适用于高频写入的知识系统",
    "links": ["概念-上下文工程"],
    "conflicts": ["概念-即时检索"],
}, ensure_ascii=False)


class FakeLLM:
    """mock LLM：按调用轮换响应（discuss 文本 → extract JSON）。"""

    def __init__(self, responses=None, exc=None, model="mock-gpt"):
        self.responses = responses or []
        self.exc = exc
        self.model = model
        self.calls = 0

    def chat(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        if self.calls <= len(self.responses):
            return self.responses[self.calls - 1]
        return self.responses[-1] if self.responses else ""


class TimeoutLLM:
    """完全超时极端场景：模拟 LLM 挂起后由客户端中断抛 TimeoutError。

    真实 LLM 客户端在请求超时后抛 TimeoutError（requests/httpx timeout）；
    降级铁律要求这种情形同样降级为骨架产物，绝不向外抛异常。
    """

    model = "timeout-llm"

    def __init__(self, delay: float = 0.01):
        self.delay = delay

    def chat(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
        time.sleep(self.delay)  # 模拟挂起等待（有界，避免测试阻塞）
        raise TimeoutError("LLM 请求完全超时（模拟 60s 无响应，客户端中断）")


def _write_material(tmp_path: Path, name: str = "降噪设计.md",
                    content: str = "# 降噪设计\n\n知识库需要分层提炼。") -> Path:
    d = tmp_path / "materials"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(content, encoding="utf-8")
    return p


# ════════════════════════════════════════════════════════════
#  happy path：capture → distill → card → audit
# ════════════════════════════════════════════════════════════

def test_workflow_happy_path(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))

    # Step1 收集
    src = _write_material(tmp_path)
    slug = runner.run_ingest(src, dest_layer="inbox")
    assert slug
    assert (kb / "inbox" / src.name).is_file()

    # Step2 提炼
    note = runner.run_distill(src)
    assert note.distilled is True

    # 人工确认后 Step4 产卡
    assert approve_note(note.slug, knowledge_root=str(kb))
    card_slug = runner.run_card(note.slug)
    assert (kb / "wiki" / "concepts" / f"{card_slug}.md").is_file()

    # Step5 审计：无建议链接 → 无断链；单卡无入链 → 孤儿（links 既有语义）
    report = runner.run_audit()
    assert report["total_cards"] == 1
    assert report["broken_links"] == []
    assert report["orphans"] == ["降噪设计"]

    # log.md / index.md 全程同步
    log_text = (kb / "log.md").read_text(encoding="utf-8")
    assert "create" in log_text and "distill" in log_text
    index_text = (kb / "index.md").read_text(encoding="utf-8")
    assert f"[[{card_slug}]]" in index_text


# ════════════════════════════════════════════════════════════
#  讨论 → 产卡（Step3 → Step4 闭环）
# ════════════════════════════════════════════════════════════

def test_workflow_discussion_to_card(tmp_path):
    kb = tmp_path / "kb"
    # 响应顺序：call1 提炼（JSON）→ call2 讨论（文本）→ call3 提炼讨论字段（JSON）
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON,
                                                   DISCUSS_TEXT, EXTRACT_JSON]))

    src = _write_material(tmp_path)
    runner.run_ingest(src)
    note = runner.run_distill(src)
    assert approve_note(note.slug, knowledge_root=str(kb))

    # Step3 深度讨论
    disc_path = runner.run_discuss(note.slug, "降噪与即时检索如何权衡？")
    disc = load_discussion(disc_path)
    assert disc.distilled is True
    assert disc.conflicts == ["概念-即时检索"]  # [冲突] 标记被解析

    # Step4 从讨论产卡
    card_slug = runner.card_from_discussion(disc_path)
    assert (kb / "wiki" / "concepts" / f"{card_slug}.md").is_file()

    store = CardStore(kb / "wiki")
    card = store.get(card_slug)
    assert card.status == "draft"
    assert card.insight == "知识系统降噪与检索速度存在权衡"   # one_line_insight → insight
    assert card.scope == "仅适用于高频写入的知识系统"         # 适用边界 → scope
    assert card.links == ["概念-上下文工程"]                  # 建议交叉引用 → links
    assert card.contradictions == [{"target_slug": "概念-即时检索",
                                    "status": "conflict"}]   # 矛盾标记（不自动裁决）
    # 讨论 → 产卡关联：source_card 指向讨论记录
    assert card.metadata["source_card"] == f"processed/{Path(disc_path).name}"

    log_text = (kb / "log.md").read_text(encoding="utf-8")
    assert "card_from_discussion" in log_text


# ════════════════════════════════════════════════════════════
#  产卡后 draft，须人工确认（人机边界）
# ════════════════════════════════════════════════════════════

def test_workflow_card_draft_requires_manual_confirm(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))
    src = _write_material(tmp_path)
    runner.run_ingest(src)
    note = runner.run_distill(src)
    assert approve_note(note.slug, knowledge_root=str(kb))
    card_slug = runner.run_card(note.slug)

    store = CardStore(kb / "wiki")
    # 产卡函数不自动升级状态
    assert store.get(card_slug).status == "draft"
    # 只有显式 transition（人工确认）才转 current
    store.transition(card_slug, "current")
    assert store.get(card_slug).status == "current"


# ════════════════════════════════════════════════════════════
#  LLM 离线 → 降级，不产生半成品卡片
# ════════════════════════════════════════════════════════════

def test_workflow_offline_no_half_card(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb), llm=None)  # 离线

    src = _write_material(tmp_path)
    runner.run_ingest(src)

    # 提炼降级：骨架笔记（distilled=False），不抛异常
    note = runner.run_distill(src)
    assert note.distilled is False and note.reason == "offline"

    # 讨论降级：骨架讨论记录（不抛异常）
    disc_path = runner.run_discuss(note.slug, "这个观点成立吗？")
    disc = load_discussion(disc_path)
    assert disc.distilled is False and disc.reason == "offline"

    # 骨架讨论无 insight/scope → 产卡校验失败 → wiki 无半成品卡片
    with pytest.raises(ValueError, match="讨论产卡校验失败"):
        runner.card_from_discussion(disc_path)
    assert not (kb / "wiki" / "concepts").exists() or \
        not any((kb / "wiki" / "concepts").iterdir())


# ════════════════════════════════════════════════════════════
#  边界护栏：敏感素材不进 wiki
# ════════════════════════════════════════════════════════════

def test_sensitive_material_never_to_wiki(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))
    src = _write_material(tmp_path, "个人联系.md",
                          content="# 个人联系\n\ntest@example.com 是我的邮箱")
    runner.run_ingest(src)

    # ingest 自动标记敏感 → distill 跳过提炼（不调用 LLM）
    note = runner.run_distill(kb / "inbox" / src.name)
    assert note.distilled is False and note.reason == "sensitive"

    # 即便人工 approve，骨架笔记也无法产卡 → wiki 无该卡片
    assert approve_note(note.slug, knowledge_root=str(kb))
    with pytest.raises(ValueError, match="骨架"):
        runner.run_card(note.slug)
    assert not any((kb / "wiki").glob("**/*.md"))


# ════════════════════════════════════════════════════════════
#  与 MemoryManager 对话记忆互不污染
# ════════════════════════════════════════════════════════════

def test_workflow_memory_isolation(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))
    src = _write_material(tmp_path)
    runner.run_ingest(src)
    runner.run_distill(src)

    # 知识工作流文件全部落在 kb 内，且不触碰 memory 存储
    assert runner.root == kb.resolve()
    assert not (tmp_path / "memory_data").exists()
    # kb 内仅 knowledge 目录结构（无 memory 文件）
    names = {p.name for p in kb.rglob("*") if p.is_file()}
    assert not any(n.startswith(("memory", "blackbox")) for n in names)


# ════════════════════════════════════════════════════════════
#  工具入口
# ════════════════════════════════════════════════════════════

def test_tools_register_unregister():
    from agent.knowledge import tools

    expected = {"kb_capture", "kb_distill", "kb_discuss", "kb_card",
                "kb_lint", "kb_search"}
    try:
        assert tools.register_knowledge_tools() == 6
        from agent.tools import list_tools

        names = {t["name"] for t in list_tools()}
        assert expected <= names
    finally:
        tools.unregister_knowledge_tools()
    from agent.tools import list_tools

    names = {t["name"] for t in list_tools()}
    assert not (expected & names)


def test_kb_card_tool_guardrail_blocks_unapproved(tmp_path, monkeypatch):
    from agent.knowledge import tools

    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))
    monkeypatch.setattr(tools, "_runner", runner)
    src = _write_material(tmp_path)
    runner.run_ingest(src)
    note = runner.run_distill(src)

    # 未 approve 直接 kb_card → ok=False（AI 不能绕过人工确认）
    result = tools.kb_card(note_slug=note.slug)
    assert result["ok"] is False
    assert "未确认" in result["error"]
    assert not any((kb / "wiki").glob("**/*.md"))


def test_kb_search_tool(tmp_path, monkeypatch):
    from agent.knowledge import tools

    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb),
                            llm=FakeLLM(responses=[HAPPY_LLM_JSON]))
    monkeypatch.setattr(tools, "_runner", runner)
    src = _write_material(tmp_path, "降噪设计.md")
    runner.run_ingest(src)
    note = runner.run_distill(src)
    assert approve_note(note.slug, knowledge_root=str(kb))
    runner.run_card(note.slug)

    result = tools.kb_search(query="降噪")
    assert result["ok"] is True
    assert any("降噪" in h["title"] or "降噪" in h["snippet"]
               for h in result["hits"])
    # 缺 query → ok=False
    assert tools.kb_search()["ok"] is False


# ════════════════════════════════════════════════════════════
#  LLM 完全超时极端场景（【不易】降级铁律，全流程不抛异常）
# ════════════════════════════════════════════════════════════

def test_workflow_llm_timeout_extreme_degrades(tmp_path):
    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb), llm=TimeoutLLM())
    src = _write_material(tmp_path)
    runner.run_ingest(src)

    # 提炼降级：TimeoutError 被 except Exception 兜底 → 骨架笔记
    note = runner.run_distill(src)
    assert note.distilled is False and note.reason == "error"

    # 讨论降级：骨架讨论（不抛异常）
    disc_path = runner.run_discuss(note.slug, "超时场景测试问题")
    disc = load_discussion(disc_path)
    assert disc.distilled is False and disc.reason == "error"

    # 骨架笔记即使 approve 也拒绝产卡（可预期 ValueError，非未捕获异常）
    assert approve_note(note.slug, knowledge_root=str(kb))
    with pytest.raises(ValueError, match="骨架"):
        runner.run_card(note.slug)

    # 骨架讨论无 insight → 产卡校验失败（可预期 ValueError）
    with pytest.raises(ValueError, match="讨论产卡校验失败"):
        runner.card_from_discussion(disc_path)


def test_kb_distill_tool_offline_degrades(tmp_path, monkeypatch):
    from agent.knowledge import tools

    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb), llm=None)  # 离线
    monkeypatch.setattr(tools, "_runner", runner)
    src = _write_material(tmp_path)
    runner.run_ingest(src)

    # 离线提炼 → ok=True + distilled=False（不抛异常，返回 dict）
    result = tools.kb_distill(source_path=str(kb / "inbox" / src.name))
    assert result["ok"] is True
    assert result["distilled"] is False
    assert result["reason"] == "offline"

    # 素材不存在 → ok=False（使用错误）
    assert tools.kb_distill(source_path=str(kb / "inbox" / "nope.md"))["ok"] is False


def test_kb_card_tool_timeout_degrades(tmp_path, monkeypatch):
    from agent.knowledge import tools

    kb = tmp_path / "kb"
    runner = WorkflowRunner(knowledge_root=str(kb), llm=TimeoutLLM())
    monkeypatch.setattr(tools, "_runner", runner)
    src = _write_material(tmp_path)
    runner.run_ingest(src)
    note = runner.run_distill(src)  # 超时 → 骨架笔记

    # 即使人工 approve，超时骨架笔记产卡 → ok=False（工具层不抛异常）
    assert approve_note(note.slug, knowledge_root=str(kb))
    result = tools.kb_card(note_slug=note.slug)
    assert result["ok"] is False
    assert "骨架" in result["error"]
    assert not any((kb / "wiki").glob("**/*.md"))
