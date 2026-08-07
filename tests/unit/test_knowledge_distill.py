"""中间层提炼管线单元测试（任务3）。

验收线（不易）：
- LLM 不可用/超时/JSON 解析失败 → 骨架降级（distilled=False），绝不抛异常
- 幂等：同 source_hash 重复 distill 只产生一条笔记
- 敏感素材跳过提炼（reason=sensitive），正文不含敏感内容
- approve/reject 状态变更生效
- 提炼结果含 counter_examples 与 one_line_insight（任务7 对齐）
"""
import hashlib
import json
from pathlib import Path

import pytest

from agent.knowledge.distill import (
    PROCESSED_DIR,
    Note,
    approve_note,
    distill,
    reject_note,
)

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
        self.received_messages = None

    def chat(self, messages, system_prompt="", max_tokens=1024, temperature=0.7):
        self.calls += 1
        self.received_messages = messages
        if self.exc is not None:
            raise self.exc
        return self.response


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_source(kb: Path, name: str = "note.md", content: str = "# 素材\n\n正文内容",
                 sensitive: bool = False) -> Path:
    """在临时 knowledge 根下构造 inbox 素材 + meta（模拟任务1 产物）。"""
    inbox = kb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    src = inbox / name
    raw = content.encode("utf-8")
    src.write_bytes(raw)
    meta = {
        "source_path": str(src.resolve()),
        "source_type": "article",
        "captured_at": "2026-08-02T00:00:00Z",
        "sha256": _sha256_bytes(raw),
        "sensitive": sensitive,
        "sensitive_patterns": ["phone"] if sensitive else [],
        "layer": "inbox",
        "filename": name,
        "slug": Path(name).stem,
    }
    src.with_name(name + ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return src


def _processed(kb: Path) -> Path:
    return kb / PROCESSED_DIR


def _read_log(root: Path) -> str:
    p = root / "log.md"
    return p.read_text(encoding="utf-8") if p.is_file() else ""


# ════════════════════════════════════════════════════════════
#  正常提炼
# ════════════════════════════════════════════════════════════

def test_distill_normal_success(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb, content="# 素材\n\n原文内容，包含一些噪声。")
    llm = FakeLLM(response=VALID_LLM_JSON)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert note.source_hash == _sha256_bytes(src.read_bytes())
    assert note.source == "inbox/note.md"
    assert note.core_points == ["观点一：上下文工程比提示词更重要", "观点二：记忆分层存储"]
    assert note.counter_examples == ["反例：一次性任务不应过度分层"]
    assert note.suggested_links == ["概念-上下文工程", "概念-知识图谱"]
    assert note.one_line_insight == "知识系统应以降噪为核心设计目标"
    assert note.llm_model == "mock-gpt"
    assert note.status == "draft"
    assert llm.calls == 1

    # 笔记已落盘：frontmatter 含 schema 对齐字段
    note_file = _processed(kb) / f"{note.slug}.md"
    assert note_file.is_file()
    text = note_file.read_text(encoding="utf-8")
    for key in ("title", "slug", "source", "source_hash", "distilled", "status",
                "distill_date", "llm_model", "confidence", "one_line_insight",
                "counter_examples"):
        assert f"{key}:" in text, f"frontmatter 缺少字段 {key}"
    assert "## 一句话洞见" in text
    assert "## 反面案例" in text

    # log.md 已登记 distill 记录
    assert "## [" in _read_log(kb)
    assert "distill" in _read_log(kb)


def test_distill_normal_prompt_contains_material(tmp_path):
    kb = tmp_path / "kb"
    content = "# 素材\n\n我需要被提炼的原文。"
    src = _make_source(kb, content=content)
    llm = FakeLLM(response=VALID_LLM_JSON)

    distill(str(src), llm=llm, knowledge_root=str(kb))

    assert llm.calls == 1
    user_msg = llm.received_messages[0]["content"]
    assert "素材标题: note" in user_msg
    assert "素材内容:" in user_msg
    assert content in user_msg


def test_distill_accepts_fenced_json(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    fenced = "```json\n" + VALID_LLM_JSON + "\n```"
    llm = FakeLLM(response=fenced)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert note.core_points  # 围栏剥离后正常解析


def test_distill_non_dict_json_degrades(tmp_path):
    """LLM 返回 JSON 数组/标量（非对象）→ json_error 骨架。"""
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(response='["不是", "对象"]')

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is False
    assert note.reason == "json_error"


def test_distill_string_fields_and_bad_confidence(tmp_path):
    """LLM 字段为字符串/置信度非法时安全归一（不崩溃）。"""
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(response=json.dumps({
        "core_points": "单个观点字符串",
        "counter_examples": None,
        "confidence": "非常高",  # 非法 → 0.0
    }, ensure_ascii=False))

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert note.core_points == ["单个观点字符串"]
    assert note.counter_examples == []
    assert note.confidence == 0.0


# ════════════════════════════════════════════════════════════
#  降级路径（【不易】不抛异常）
# ════════════════════════════════════════════════════════════

def test_distill_llm_exception_degrades(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(exc=RuntimeError("LLM 服务不可用"))

    note = distill(str(src), llm=llm, knowledge_root=str(kb))  # 不抛异常

    assert note.distilled is False
    assert note.reason == "error"
    assert note.llm_model == ""
    assert _processed(kb).joinpath(f"{note.slug}.md").is_file()


def test_distill_llm_timeout_degrades(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(exc=TimeoutError("请求超时"))

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is False
    assert note.reason == "error"


def test_distill_no_llm_degrades(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)

    note = distill(str(src), llm=None, knowledge_root=str(kb))

    assert note.distilled is False
    assert note.reason == "offline"
    assert note.source_hash == _sha256_bytes(src.read_bytes())


def test_distill_json_parse_failure_degrades(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(response="这不是合法 JSON")

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is False
    assert note.reason == "json_error"


# ════════════════════════════════════════════════════════════
#  幂等
# ════════════════════════════════════════════════════════════

def test_distill_idempotent_same_source(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb)
    llm = FakeLLM(response=VALID_LLM_JSON)

    first = distill(str(src), llm=llm, knowledge_root=str(kb))
    second = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert first.slug == second.slug
    assert first.source_hash == second.source_hash
    assert llm.calls == 1, "幂等命中时不应再次调用 LLM"
    # 只产生一条笔记
    notes = list(_processed(kb).glob("*.md"))
    assert len(notes) == 1


def test_distill_idempotent_returns_existing_even_with_llm_failure(tmp_path):
    """同源第二次 distill 即便 LLM 报错，也应幂等返回既有笔记而非降级覆盖。"""
    kb = tmp_path / "kb"
    src = _make_source(kb)
    good_llm = FakeLLM(response=VALID_LLM_JSON)
    first = distill(str(src), llm=good_llm, knowledge_root=str(kb))

    bad_llm = FakeLLM(exc=RuntimeError("down"))
    second = distill(str(src), llm=bad_llm, knowledge_root=str(kb))

    assert second.distilled is True
    assert second.slug == first.slug
    assert len(list(_processed(kb).glob("*.md"))) == 1


# ════════════════════════════════════════════════════════════
#  敏感素材
# ════════════════════════════════════════════════════════════

def test_distill_sensitive_skips_llm(tmp_path):
    kb = tmp_path / "kb"
    secret = "联系电话 13812345678，银行卡号 6222xxxxxxxx"
    src = _make_source(kb, name="secret.md", content=secret, sensitive=True)
    llm = FakeLLM(response=VALID_LLM_JSON)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert llm.calls == 0, "敏感素材不得调用 LLM"
    assert note.distilled is False
    assert note.reason == "sensitive"
    # 正文不含敏感内容（不进入 processed 正文）
    note_file = _processed(kb) / f"{note.slug}.md"
    text = note_file.read_text(encoding="utf-8")
    assert "13812345678" not in text
    assert "6222" not in text
    assert "sensitive" in text
    assert "降级骨架笔记" in text
    assert "distill" in _read_log(kb)


# ════════════════════════════════════════════════════════════
#  人工确认：approve / reject
# ════════════════════════════════════════════════════════════

@pytest.fixture
def distilled_note(tmp_path):
    kb = tmp_path / "kb"
    src = _make_source(kb, name="approve-me.md")
    llm = FakeLLM(response=VALID_LLM_JSON)
    note = distill(str(src), llm=llm, knowledge_root=str(kb))
    return kb, note


def test_approve_note(distilled_note):
    kb, note = distilled_note
    assert approve_note(note.slug, knowledge_root=str(kb)) is True
    note_file = _processed(kb) / f"{note.slug}.md"
    text = note_file.read_text(encoding="utf-8")
    assert "status: approved" in text
    assert "approved" in _read_log(kb)


def test_reject_note(distilled_note):
    kb, note = distilled_note
    assert reject_note(note.slug, knowledge_root=str(kb)) is True
    note_file = _processed(kb) / f"{note.slug}.md"
    text = note_file.read_text(encoding="utf-8")
    assert "status: rejected" in text
    assert "rejected" in _read_log(kb)


def test_approve_note_not_found(tmp_path):
    kb = tmp_path / "kb"
    assert approve_note("不存在的slug", knowledge_root=str(kb)) is False
    assert reject_note("不存在的slug", knowledge_root=str(kb)) is False


# ════════════════════════════════════════════════════════════
#  边界
# ════════════════════════════════════════════════════════════

def test_distill_missing_source_raises(tmp_path):
    kb = tmp_path / "kb"
    with pytest.raises(FileNotFoundError):
        distill(str(kb / "inbox" / "nope.md"), llm=None, knowledge_root=str(kb))


def test_distill_long_content_truncated(tmp_path):
    """超长素材截断后送入 LLM（防 prompt 爆炸），仍正常提炼。"""
    kb = tmp_path / "kb"
    long_content = "# 长文\n\n" + "甲" * 30000  # 超过 MAX_SOURCE_CHARS
    src = _make_source(kb, content=long_content)
    llm = FakeLLM(response=VALID_LLM_JSON)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert llm.calls == 1
    assert len(llm.received_messages[0]["content"]) <= 20000 + 200


def test_distill_source_outside_root_stores_abs(tmp_path):
    """源素材在 knowledge 根之外 → source 字段存绝对路径，不阻断。"""
    kb = tmp_path / "kb"
    outside = tmp_path / "outside"
    outside.mkdir()
    src = outside / "ext.md"
    raw = b"outside content"
    src.write_bytes(raw)
    # 手工补 meta（与任务1 布局一致）
    src.with_name("ext.md.meta.json").write_text(json.dumps({
        "sha256": hashlib.sha256(raw).hexdigest(),
        "sensitive": False,
    }), encoding="utf-8")
    llm = FakeLLM(response=VALID_LLM_JSON)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert note.source == str(src).replace("\\", "/")


def test_distill_without_meta_computes_hash(tmp_path):
    """无 meta（非任务1 产物）时现场计算 sha256，不阻断提炼。"""
    kb = tmp_path / "kb"
    inbox = kb / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    src = inbox / "bare.md"
    raw = b"bare content"
    src.write_bytes(raw)
    llm = FakeLLM(response=VALID_LLM_JSON)

    note = distill(str(src), llm=llm, knowledge_root=str(kb))

    assert note.distilled is True
    assert note.source_hash == hashlib.sha256(raw).hexdigest()


def test_same_slug_different_source_not_overwritten(tmp_path):
    """同标题不同内容（同 slug 不同 hash）→ 追加 -2 后缀，不覆盖既有笔记。"""
    kb = tmp_path / "kb"
    src1 = _make_source(kb, name="note.md", content="内容A")
    src2 = _make_source(kb, name="note-2.md", content="内容B（不同 hash）")
    llm = FakeLLM(response=VALID_LLM_JSON)

    n1 = distill(str(src1), llm=llm, knowledge_root=str(kb))
    n2 = distill(str(src2), llm=llm, knowledge_root=str(kb))

    assert n1.slug == n2.slug or n2.slug.endswith("-2")
    files = sorted(p.name for p in _processed(kb).glob("*.md"))
    assert len(files) == 2
    # 两条笔记 source_hash 均保留各自源
    hashes = {_read_hash(_processed(kb) / f) for f in files}
    assert len(hashes) == 2


def _read_hash(path: Path) -> str:
    import yaml
    text = path.read_text(encoding="utf-8")
    body = text.split("---", 2)[1]
    return yaml.safe_load(body)["source_hash"]
