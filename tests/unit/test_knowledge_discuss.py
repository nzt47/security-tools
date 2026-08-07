"""深度讨论（discuss / extract_card_insights）单元测试（任务7 · Step 2）。

验收线（不易）：
- 降级铁律：LLM 不可用（None/异常/超时）→ 骨架讨论（distilled=False，
  reason=offline/error），绝不抛异常。
- extract_card_insights 失败（None/异常/超时/JSON 解析失败）→ 返回空字段
  dict，绝不抛异常。
- 笔记不存在 → 抛 FileNotFoundError（使用错误，非 LLM 场景，允许抛）。
- 正常讨论解析 [冲突] 标记（AI 只标记矛盾，不自动裁决）。
"""
import json
from pathlib import Path

import pytest

from agent.knowledge.discuss import discuss, extract_card_insights, load_discussion

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


def _make_note(kb: Path, name: str = "降噪设计", content: str = "# 降噪设计\n\n正文内容") -> Path:
    """在临时 knowledge 根下构造 processed/ 笔记（discuss 的输入）。"""
    processed = kb / "processed"
    processed.mkdir(parents=True, exist_ok=True)
    p = processed / f"{name}.md"
    p.write_text(
        f"---\ntitle: {name}\nslug: {name}\nsource: inbox/{name}.md\n"
        f"source_hash: abc123\nstatus: approved\ndistilled: true\nreason: ''\n"
        f"---\n\n{content}",
        encoding="utf-8",
    )
    return p


# ════════════════════════════════════════════════════════════
#  正常讨论（含 [冲突] 标记解析）
# ════════════════════════════════════════════════════════════

def test_discuss_normal_parses_conflict_marker(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "降噪与检索如何权衡？",
                   llm=FakeLLM(response=DISCUSS_TEXT), knowledge_root=str(kb))
    disc = load_discussion(path)
    assert disc.distilled is True
    assert disc.conflicts == ["概念-即时检索"]  # 只标记不裁决
    assert disc.llm_model == "mock-gpt"
    assert (kb / "processed" / "降噪设计.discussion.md").is_file()
    # log.md 同步
    log_text = (kb / "log.md").read_text(encoding="utf-8")
    assert "discuss" in log_text


# ════════════════════════════════════════════════════════════
#  降级铁律（【不易】不抛异常）
# ════════════════════════════════════════════════════════════

def test_discuss_llm_none_degrades_offline(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "这个观点成立吗？", llm=None,
                   knowledge_root=str(kb))  # 离线
    disc = load_discussion(path)
    assert disc.distilled is False
    assert disc.reason == "offline"
    assert "降级" in disc.content


def test_discuss_llm_exception_degrades(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=FakeLLM(exc=RuntimeError("服务不可用")),
                   knowledge_root=str(kb))
    disc = load_discussion(path)
    assert disc.distilled is False
    assert disc.reason == "error"


def test_discuss_llm_timeout_degrades(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=FakeLLM(exc=TimeoutError("请求超时")),
                   knowledge_root=str(kb))
    disc = load_discussion(path)
    assert disc.distilled is False
    assert disc.reason == "error"


def test_discuss_note_missing_raises(tmp_path):
    kb = tmp_path / "kb"
    # 笔记不存在 = 使用错误 → 允许抛 FileNotFoundError（非 LLM 场景）
    with pytest.raises(FileNotFoundError):
        discuss("不存在的笔记", "问题", llm=None, knowledge_root=str(kb))


# ════════════════════════════════════════════════════════════
#  extract_card_insights 降级（返回空 dict，不抛异常）
# ════════════════════════════════════════════════════════════

def test_extract_llm_none_returns_empty(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=None, knowledge_root=str(kb))
    result = extract_card_insights(path, llm=None)
    assert result == {"one_line_insight": "", "scope": "", "links": [], "conflicts": []}


def test_extract_llm_timeout_returns_empty(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=FakeLLM(response=DISCUSS_TEXT),
                   knowledge_root=str(kb))
    result = extract_card_insights(path, llm=FakeLLM(exc=TimeoutError("超时")))
    assert result == {"one_line_insight": "", "scope": "", "links": [], "conflicts": []}


def test_extract_json_failure_returns_empty(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=FakeLLM(response=DISCUSS_TEXT),
                   knowledge_root=str(kb))
    result = extract_card_insights(path, llm=FakeLLM(response="这不是 JSON"))
    assert result == {"one_line_insight": "", "scope": "", "links": [], "conflicts": []}


def test_extract_normal(tmp_path):
    kb = tmp_path / "kb"
    _make_note(kb)
    path = discuss("降噪设计", "问题", llm=FakeLLM(response=DISCUSS_TEXT),
                   knowledge_root=str(kb))
    result = extract_card_insights(path, llm=FakeLLM(response=EXTRACT_JSON))
    assert result["one_line_insight"] == "知识系统降噪与检索速度存在权衡"
    assert result["scope"] == "仅适用于高频写入的知识系统"
    assert result["links"] == ["概念-上下文工程"]
    assert result["conflicts"] == ["概念-即时检索"]
    # 提炼结果回填讨论记录（便于审计与复用）
    disc = load_discussion(path)
    assert disc.insight == result["one_line_insight"]
