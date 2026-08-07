"""中间层提炼管线：原文 → 结构化笔记（任务3 · 提炼层）。

消费任务1 的 inbox 素材（含 .meta.json），经 LLM 提炼为结构化笔记
落入 knowledge/processed/<slug>.md，人工确认（approve/reject）后作为
任务2 产卡输入。本阶段是知识系统的"降噪缓冲层"，防止原文噪声直接污染 wiki。

【不易】降级铁律：LLM 不可用（离线/超时/无 API Key/JSON 解析失败）时
必须降级而非失败——产出 distilled=False 的最小骨架笔记，绝不抛异常。
【不易】幂等：基于 source_hash 去重，同一素材重复 distill 只产生一条笔记。
【不易】敏感素材：meta.sensitive=true 时跳过提炼（不调用 LLM），
记录 distilled=false + reason=sensitive，敏感正文不进入 processed。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from agent.knowledge.card import CardConflictError, CardStore
from agent.knowledge.ingest import META_SUFFIX, _load_meta, _sha256_file, get_knowledge_root
from agent.knowledge.logbook import append_log
from agent.knowledge.observability import emit_structured_log
from agent.knowledge.prompts import DISTILL_SYSTEM_PROMPT, DISTILL_USER_TEMPLATE
from agent.knowledge.schema import Card, slugify, validate_card

logger = logging.getLogger(__name__)

PROCESSED_DIR = "processed"

# 素材正文送入 LLM 的上限（超长截断，防 prompt 爆炸；边界防御，属变易）
MAX_SOURCE_CHARS = 20000

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# 笔记状态：draft（默认，待讨论）→ approved（人工确认）→ rejected（拒绝）
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


@dataclass
class Note:
    """结构化笔记（写入 knowledge/processed/<slug>.md）"""

    title: str
    slug: str
    source: str  # 源素材相对路径
    source_hash: str  # 源素材 sha256（任务1 meta）
    distilled: bool  # False 表示离线降级骨架
    core_points: list[str] = field(default_factory=list)
    knowledge_points: list[str] = field(default_factory=list)
    inspirations: list[str] = field(default_factory=list)
    counter_examples: list[str] = field(default_factory=list)  # 反面案例（宪法要求）
    suggested_links: list[str] = field(default_factory=list)  # 建议交叉引用 slug
    one_line_insight: str = ""  # 一句话洞见（任务7 → Card.insight）
    llm_model: str = ""
    confidence: float = 0.0
    distill_date: str = ""
    status: str = "draft"  # 笔记默认 draft，待讨论
    reason: str = ""  # 降级/跳过原因（offline/error/json_error/sensitive）


# ═══════════════════════════════════════════════════════════
#  源素材读取
# ═══════════════════════════════════════════════════════════

def _meta_of(source: Path) -> dict:
    """读取任务1 的 meta（<file>.meta.json）；缺失/损坏返回 {}。"""
    meta = _load_meta(Path(str(source) + META_SUFFIX))
    return meta if isinstance(meta, dict) else {}


def _source_hash(source: Path, meta: dict) -> str:
    """源素材 sha256：优先取 meta（任务1 已算），否则现场计算。"""
    if meta.get("sha256"):
        return str(meta["sha256"])
    return _sha256_file(source)


def _rel_source(root: Path, source: Path) -> str:
    """源素材相对 knowledge 根的路径（POSIX 分隔符，写入 frontmatter）。

    源在根之外时退回绝对路径（同样归一化为 POSIX 分隔符，保证 frontmatter
    一致性，避免 Windows 反斜杠污染 YAML 文本）。
    """
    try:
        return str(source.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(source.resolve()).replace("\\", "/")


def _read_source_text(source: Path) -> str:
    """读取源素材正文（二进制容错解码 + 超长截断）。"""
    try:
        raw = source.read_bytes()
    except OSError:
        return ""
    text = raw.decode("utf-8", errors="ignore")
    if len(text) > MAX_SOURCE_CHARS:
        text = text[:MAX_SOURCE_CHARS] + "\n…（超长已截断）"
    return text


# ═══════════════════════════════════════════════════════════
#  LLM 输出解析
# ═══════════════════════════════════════════════════════════

def _parse_llm_json(raw: str) -> dict:
    """解析 LLM 返回的 JSON；容忍 markdown 代码围栏。失败抛 ValueError。"""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("LLM 输出不是 JSON 对象")
    return data


def _str_list(value) -> list[str]:
    """list[str] 安全转换（容忍缺失/单字符串/含空项）。"""
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _float01(value) -> float:
    """置信度收敛到 [0, 1]；非法输入返回 0.0。"""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _note_from_llm(data: dict, source: Path, source_hash: str, source_rel: str,
                   title: str, llm) -> Note:
    """LLM JSON → 结构化 Note（字段契约：core_points/knowledge_points/.../one_line_insight）。"""
    return Note(
        title=title,
        slug=slugify(title),
        source=source_rel,
        source_hash=source_hash,
        distilled=True,
        core_points=_str_list(data.get("core_points")),
        knowledge_points=_str_list(data.get("knowledge_points")),
        inspirations=_str_list(data.get("inspirations")),
        counter_examples=_str_list(data.get("counter_examples")),
        suggested_links=_str_list(data.get("suggested_links")),
        one_line_insight=str(data.get("one_line_insight") or ""),
        llm_model=getattr(llm, "model", ""),
        confidence=_float01(data.get("confidence", 0.0)),
        distill_date=date.today().isoformat(),
    )


def _skeleton_note(title: str, source_hash: str, source_rel: str, reason: str) -> Note:
    """离线降级骨架笔记（distilled=False，不抛异常的核心兜底）。"""
    return Note(
        title=title,
        slug=slugify(title),
        source=source_rel,
        source_hash=source_hash,
        distilled=False,
        distill_date=date.today().isoformat(),
        reason=reason,
    )


# ═══════════════════════════════════════════════════════════
#  笔记持久化（knowledge/processed/<slug>.md）
# ═══════════════════════════════════════════════════════════

def _note_body(note: Note) -> str:
    """笔记正文（人类可读，供「待讨论」；机器数据以 frontmatter 为准）。"""
    if not note.distilled:
        return f"（降级骨架笔记：{note.reason or 'LLM 不可用'}，未调用 LLM 提炼）\n"
    lines: list[str] = []
    if note.one_line_insight:
        lines += ["## 一句话洞见", note.one_line_insight, ""]
    sections = [
        ("核心观点", note.core_points),
        ("知识要点", note.knowledge_points),
        ("启发", note.inspirations),
        ("反面案例", note.counter_examples),
    ]
    for title, items in sections:
        if items:
            lines.append(f"## {title}")
            lines.extend(f"- {item}" for item in items)
            lines.append("")
    if note.suggested_links:
        lines.append("## 建议交叉引用")
        lines.extend(f"- [[{s}]]" for s in note.suggested_links)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _note_to_md(note: Note) -> str:
    """Note → frontmatter + 正文 Markdown（字段顺序与 Note 定义一致）。"""
    frontmatter = yaml.safe_dump(
        asdict(note), allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{_note_body(note)}"


def _read_note(path: Path) -> Note:
    """frontmatter → Note（未知字段忽略，保持向前兼容）。"""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"frontmatter 解析失败: {path}")
    data = yaml.safe_load(m.group(1)) or {}
    fields = Note.__dataclass_fields__
    return Note(**{k: v for k, v in data.items() if k in fields})


def _atomic_write(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，保证原子性（防并发半写）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _find_by_source_hash(processed_dir: Path, source_hash: str) -> Optional[Note]:
    """幂等检索：processed/ 下是否已有同 source_hash 的笔记。"""
    if not processed_dir.is_dir() or not source_hash:
        return None
    for p in processed_dir.glob("*.md"):
        try:
            note = _read_note(p)
        except (ValueError, TypeError):
            continue
        if note.source_hash == source_hash:
            return note
    return None


def _resolve_note_path(processed_dir: Path, slug: str) -> Path:
    """同 slug 已存在（不同素材）时追加 -2/-3 后缀，绝不覆盖。"""
    cand = processed_dir / f"{slug}.md"
    if not cand.exists():
        return cand
    n = 2
    while True:
        cand2 = processed_dir / f"{slug}-{n}.md"
        if not cand2.exists():
            return cand2
        n += 1


def _write_note(processed_dir: Path, note: Note) -> Path:
    """原子写笔记；文件名消歧后同步 note.slug（slug 与文件名对齐，便于定位）。"""
    path = _resolve_note_path(processed_dir, note.slug)
    if path.stem != note.slug:
        note.slug = path.stem
    _atomic_write(path, _note_to_md(note))
    return path


# ═══════════════════════════════════════════════════════════
#  提炼主入口
# ═══════════════════════════════════════════════════════════

def distill(source_path: str | Path, llm=None, knowledge_root: str | None = None) -> Note:
    """提炼：调用 LLM 生成结构化笔记并写入 processed/。

    llm 可为 None（离线）或复用 memory.llm_service.LLMService 实例
    （duck-typing：只需提供 chat(messages, system_prompt=...) -> str）。

    降级（不抛异常）：
    - llm=None / 无 API Key → distilled=False, reason=offline
    - LLM 超时/异常 → distilled=False, reason=error
    - JSON 解析失败 → distilled=False, reason=json_error
    - meta.sensitive=true → 跳过提炼, distilled=False, reason=sensitive
    - 同 source_hash 已有笔记 → 幂等返回既有笔记（不重复写）
    """
    root = get_knowledge_root(knowledge_root)
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"源素材不存在: {source_path}")

    meta = _meta_of(source)
    source_hash = _source_hash(source, meta)
    processed_dir = root / PROCESSED_DIR

    existing = _find_by_source_hash(processed_dir, source_hash)
    if existing is not None:
        logger.info("[distill] 幂等命中 source_hash=%s 返回既有笔记 slug=%s",
                    source_hash[:12], existing.slug)
        return existing

    title = str(meta.get("title") or Path(source.name).stem)
    source_rel = _rel_source(root, source)

    # 敏感素材：跳过提炼（不调用 LLM），只落登记笔记
    if meta.get("sensitive"):
        logger.warning("[distill] 敏感素材跳过提炼: %s", source.name)
        note = _skeleton_note(title, source_hash, source_rel, "sensitive")
        _write_note(processed_dir, note)
        append_log("distill", note.slug, "distilled=false reason=sensitive",
                   log_path=str(root / "log.md"))
        return note

    # 离线 / 无 API Key → 骨架降级
    if llm is None:
        logger.warning("[distill] LLM 不可用（llm=None），产出骨架笔记: %s", source.name)
        note = _skeleton_note(title, source_hash, source_rel, "offline")
        _write_note(processed_dir, note)
        append_log("distill", note.slug, "distilled=false reason=offline",
                   log_path=str(root / "log.md"))
        return note

    _t_llm = time.perf_counter()
    try:
        user_prompt = DISTILL_USER_TEMPLATE.format(
            title=title, source=source_rel, content=_read_source_text(source))
        raw = llm.chat(
            [{"role": "user", "content": user_prompt}],
            system_prompt=DISTILL_SYSTEM_PROMPT,
        )
        logger.info("[distill] LLM 调用成功 source=%s", source.name)
        emit_structured_log("distill.llm_ok", duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            source=source.name, model=getattr(llm, "model", ""))
        data = _parse_llm_json(raw)
        note = _note_from_llm(data, source, source_hash, source_rel, title, llm)
        logger.info("[distill] 提炼成功 slug=%s model=%s confidence=%s",
                    note.slug, note.llm_model, note.confidence)
    except Exception as exc:
        # 降级铁律：LLM 超时/异常/JSON 解析失败均不抛异常 → 骨架笔记
        reason = "json_error" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "error"
        emit_structured_log("distill.llm_failed", level="warning",
                            duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            source=source.name, reason=reason, error=str(exc))
        note = _skeleton_note(title, source_hash, source_rel, reason)

    _write_note(processed_dir, note)
    append_log("distill", note.slug,
               f"distilled={note.distilled} model={note.llm_model or 'none'}",
               log_path=str(root / "log.md"))
    return note


# ═══════════════════════════════════════════════════════════
#  人工确认
# ═══════════════════════════════════════════════════════════

def _set_status(slug: str, status: str, knowledge_root: str | None) -> bool:
    """按 slug 更新笔记状态（draft → approved/rejected），写 log.md。"""
    root = get_knowledge_root(knowledge_root)
    path = root / PROCESSED_DIR / f"{slug}.md"
    if not path.is_file():
        return False
    try:
        note = _read_note(path)
    except (ValueError, TypeError) as exc:
        logger.warning("[distill] 读取笔记失败，拒绝状态变更 slug=%s: %s", slug, exc)
        return False
    note.status = status
    _atomic_write(path, _note_to_md(note))
    append_log(status, note.slug, "", log_path=str(root / "log.md"))
    logger.info("[distill] 笔记状态变更 slug=%s → %s", note.slug, status)
    return True


def approve_note(slug: str, knowledge_root: str | None = None) -> bool:
    """人工确认笔记，标记 status=approved，供产卡流程使用。"""
    return _set_status(slug, STATUS_APPROVED, knowledge_root)


def reject_note(slug: str, knowledge_root: str | None = None) -> bool:
    """拒绝笔记：标记 status=rejected（可回 inbox 或标 Unknown 待整理）。"""
    return _set_status(slug, STATUS_REJECTED, knowledge_root)


# ═══════════════════════════════════════════════════════════
#  产卡对接（任务3 → 任务2：Note → Card）
# ═══════════════════════════════════════════════════════════

def _note_to_card_body(note: Note) -> str:
    """Note → Card 正文：一句话洞见开头 + 分节 + [[双链]]。

    建议交叉引用渲染为 [[slug]] 双链，与 Card.links 保持一致
    （任务2 双向链接一致性契约：update 时以正文解析结果同步 links）。
    """
    lines: list[str] = []
    if note.one_line_insight:
        lines.append(note.one_line_insight)
        lines.append("")
    sections = [
        ("核心观点", note.core_points),
        ("知识要点", note.knowledge_points),
        ("启发", note.inspirations),
        ("反面案例", note.counter_examples),
    ]
    for title, items in sections:
        if items:
            lines.append(f"## {title}")
            lines.extend(f"- {item}" for item in items)
            lines.append("")
    if note.suggested_links:
        lines.append("## 相关概念")
        lines.extend(f"- [[{s}]]" for s in note.suggested_links)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def card_from_note(note: Note, card_type: str = "concepts") -> Card:
    """Note → Card 字段映射（任务3 产卡输入 → 任务2 卡片）。

    映射（与任务7 对齐）：
        one_line_insight → insight
        suggested_links → links（正文渲染 [[slug]] 双链）
        source / distill_date → source / date
        source_hash / llm_model / confidence / reason → metadata（溯源）
    状态固定 draft（人机边界：须人工确认后才可转 current）。
    """
    return Card(
        title=note.title,
        slug=note.slug,
        status="draft",
        type=card_type,
        source=note.source,
        date=note.distill_date or date.today().isoformat(),
        links=list(note.suggested_links),
        insight=note.one_line_insight,
        content=_note_to_card_body(note),
        metadata={
            "source_hash": note.source_hash,
            "distilled": note.distilled,
            "llm_model": note.llm_model,
            "confidence": note.confidence,
            "reason": note.reason,
        },
    )


def promote_to_card(
    slug: str,
    card_type: str = "concepts",
    knowledge_root: str | None = None,
    wiki_root: str | None = None,
) -> Card:
    """从 processed/ 已确认笔记产卡（任务3 → 任务2 对接入口）。

    前置条件（人机边界，AGENTS.md §6：AI 不替人做判断）：
    - 笔记必须已人工确认（status=approved，先调 approve_note）。
    - 笔记必须是真提炼产物（distilled=True）；骨架/降级/敏感笔记拒绝产卡。
    违规抛 ValueError（产卡是人工动作，不适用 LLM 降级铁律）；
    同 slug 卡片已存在抛 CardConflictError（CardStore 不覆盖契约）。
    产卡后状态为 draft，须人工 transition(slug, 'current') 才转当前有效。
    """
    root = get_knowledge_root(knowledge_root)
    path = root / PROCESSED_DIR / f"{slug}.md"
    _t0 = time.perf_counter()
    logger.info("[promote] 产卡请求 slug=%s card_type=%s 笔记路径=%s 存在=%s",
                slug, card_type, path, path.is_file())
    if not path.is_file():
        logger.warning("[promote] 笔记不存在，终止产卡 slug=%s", slug)
        raise FileNotFoundError(f"笔记不存在: {slug}（processed/ 下未找到）")
    try:
        note = _read_note(path)
        logger.info("[promote] 笔记读取成功 slug=%s title=%s distilled=%s status=%s reason=%s",
                    note.slug, note.title, note.distilled, note.status,
                    note.reason or "none")
    except (ValueError, TypeError) as exc:
        logger.error("[promote] 笔记 frontmatter 解析失败 slug=%s: %s", slug, exc)
        raise ValueError(f"笔记读取失败: {slug}: {exc}") from exc
    if note.status != STATUS_APPROVED:
        logger.warning("[promote] 笔记未确认，拒绝产卡 slug=%s status=%s（要求 approved）",
                       note.slug, note.status)
        raise ValueError(f"笔记未确认: {slug}（status={note.status}，请先 approve_note）")
    if not note.distilled:
        logger.warning("[promote] 骨架笔记，拒绝产卡 slug=%s reason=%s",
                       note.slug, note.reason or "unknown")
        raise ValueError(f"笔记为降级骨架（reason={note.reason or 'unknown'}），无法产卡")
    card = card_from_note(note, card_type=card_type)
    if card.slug != slugify(card.title):
        # 笔记 slug 被消歧过（同标题不同素材，文件名带 -N 后缀）：
        # 置显式 slug 豁免（validate_card 契约），保持卡片区分性，
        # 避免与已有卡片 slug 冲突。
        logger.info("[promote] slug 消歧检测: card.slug=%s != slugify(title)=%s，置 explicit_slug 豁免",
                    card.slug, slugify(card.title))
        card.explicit_slug = True
    errors = validate_card(asdict(card))
    if errors:
        logger.warning("[promote] 卡片校验失败 slug=%s 违规项=%s", card.slug, errors)
        raise ValueError("产卡校验失败: " + "; ".join(errors))
    logger.info("[promote] 卡片校验通过 slug=%s type=%s insight=%r links=%s",
                card.slug, card.type, card.insight, card.links)
    store = CardStore(wiki_root if wiki_root is not None else root / "wiki")
    store.create(card)
    logger.info("[promote] 产卡成功 slug=%s type=%s ← note=%s 状态=draft（待人工转 current）",
                card.slug, card.type, note.slug)
    emit_structured_log("promote.card_ok", duration_ms=(time.perf_counter() - _t0) * 1000,
                        slug=card.slug, type=card.type, source=f"note:{note.slug}")
    return card
