"""深度讨论：AI 扮演作者/领域专家，人机追问校准（任务7 · Step 2）。

围绕结构化笔记（processed/）发起深度讨论，产出讨论记录
`knowledge/processed/<topic>.discussion.md`；`extract_card_insights` 从
讨论记录提炼 {one_line_insight, scope, links, conflicts} 供产卡（Step 3）。

【不易】降级铁律（与任务3 一致）：LLM 不可用（离线/超时/异常/JSON 失败）时
必须降级而非失败——产出 distilled=False 的骨架讨论记录，不抛异常。
【不易】人机边界：讨论只标记 [冲突] 矛盾，不自动裁决。
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

import yaml

from agent.knowledge.ingest import get_knowledge_root
from agent.knowledge.logbook import append_log
from agent.knowledge.observability import emit_structured_log
from agent.knowledge.prompts import (
    DISCUSS_SYSTEM_PROMPT,
    DISCUSS_USER_TEMPLATE,
    INSIGHT_EXTRACT_SYSTEM_PROMPT,
    INSIGHT_EXTRACT_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

PROCESSED_DIR = "processed"
DISCUSSION_SUFFIX = ".discussion.md"

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?(.*)$", re.DOTALL)

# 讨论正文超长上限（防 prompt 爆炸；边界防御，属变易）
MAX_DISCUSSION_CHARS = 20000

_CONFLICT_RE = re.compile(r"\[冲突:\s*([^\]\s]+)\]")


@dataclass
class Discussion:
    """讨论记录（knowledge/processed/<topic>.discussion.md）"""

    slug: str          # <topic>-discussion（文件名 = topic + .discussion.md）
    topic: str         # 被讨论的笔记 slug
    title: str         # 笔记标题（产卡时复用）
    question: str      # 用户提问
    distilled: bool    # False = LLM 不可用降级骨架
    source: str = ""   # 笔记源素材相对路径（溯源）
    status: str = "draft"
    distill_date: str = ""
    llm_model: str = ""
    reason: str = ""            # 降级原因（offline/error/json_error）
    insight: str = ""           # extract 后回填
    scope: str = ""             # extract 后回填
    links: list[str] = field(default_factory=list)        # extract 后回填
    conflicts: list[str] = field(default_factory=list)    # 讨论中标记的冲突
    content: str = ""           # Q&A 讨论正文


def _discussion_path(processed_dir: Path, topic: str) -> Path:
    return processed_dir / f"{topic}{DISCUSSION_SUFFIX}"


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


def _conflicts_from_text(text: str) -> list[str]:
    """从讨论正文提取 [冲突: <slug>] 标记（去重保序）。"""
    found = []
    for m in _CONFLICT_RE.finditer(text):
        slug = m.group(1).strip()
        if slug and slug not in found:
            found.append(slug)
    return found


def _note_context(processed_dir: Path, note_slug: str) -> tuple[str, str, str]:
    """读取笔记 frontmatter + 正文 → (title, source, content)。"""
    path = processed_dir / f"{note_slug}.md"
    if not path.is_file():
        raise FileNotFoundError(f"笔记不存在: {note_slug}（processed/ 下未找到）")
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"笔记 frontmatter 解析失败: {path}")
    data = yaml.safe_load(m.group(1)) or {}
    title = str(data.get("title") or note_slug)
    source = str(data.get("source") or "")
    body = m.group(2).strip()
    if len(body) > MAX_DISCUSSION_CHARS:
        body = body[:MAX_DISCUSSION_CHARS] + "\n…（超长已截断）"
    return title, source, body


def _read_discussion(path: Path) -> Discussion:
    """frontmatter → Discussion（未知字段忽略，向前兼容）。"""
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"讨论记录 frontmatter 解析失败: {path}")
    data = yaml.safe_load(m.group(1)) or {}
    fields = Discussion.__dataclass_fields__
    disc = Discussion(**{k: v for k, v in data.items() if k in fields})
    disc.content = m.group(2).strip()
    return disc


def load_discussion(path: str | Path) -> Discussion:
    """公开读取讨论记录（供 workflow 产卡等跨模块使用）。"""
    return _read_discussion(Path(path))


def _discussion_to_md(disc: Discussion) -> str:
    """Discussion → frontmatter + 正文 Markdown。"""
    data = asdict(disc)
    content = data.pop("content", "")
    frontmatter = yaml.safe_dump(data, allow_unicode=True, sort_keys=False).strip()
    return f"---\n{frontmatter}\n---\n\n{content.strip()}\n"


def _write_discussion(path: Path, disc: Discussion) -> None:
    """原子写讨论记录（同目录临时文件 + os.replace）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(_discussion_to_md(disc), encoding="utf-8")
    os.replace(tmp, path)


def _skeleton_discussion(topic: str, title: str, source: str, question: str,
                         reason: str) -> Discussion:
    """离线降级骨架讨论记录（distilled=False，不抛异常的核心兜底）。"""
    return Discussion(
        slug=f"{topic}-discussion",
        topic=topic,
        title=title,
        question=question,
        distilled=False,
        source=source,
        distill_date=date.today().isoformat(),
        reason=reason,
        content=f"（降级骨架讨论：{reason}，未调用 LLM）\n",
    )


def discuss(note_slug: str, question: str, llm=None,
            knowledge_root: str | None = None) -> str:
    """对结构化笔记发起深度讨论。

    返回讨论记录文件路径：knowledge/processed/<note_slug>.discussion.md。
    llm 可为 None（离线）或复用 memory.llm_service.LLMService 实例
    （duck-typing：只需 chat(messages, system_prompt=...) -> str）。

    降级（不抛异常）：
    - llm=None → distilled=False, reason=offline
    - LLM 超时/异常 → distilled=False, reason=error
    - 笔记不存在 → 抛 FileNotFoundError（使用错误，非 LLM 场景）
    """
    root = get_knowledge_root(knowledge_root)
    processed_dir = root / PROCESSED_DIR
    logger.info("[discuss] 讨论请求 note_slug=%s question=%r", note_slug, question)
    try:
        title, source, body = _note_context(processed_dir, note_slug)
    except (FileNotFoundError, ValueError) as exc:
        logger.error("[discuss] 笔记读取失败 note_slug=%s: %s", note_slug, exc)
        raise

    path = _discussion_path(processed_dir, note_slug)

    if llm is None:
        logger.warning("[discuss] LLM 不可用（llm=None），产出骨架讨论: %s", note_slug)
        disc = _skeleton_discussion(note_slug, title, source, question, "offline")
        _write_discussion(path, disc)
        append_log("discuss", disc.slug, "distilled=false reason=offline",
                   log_path=str(root / "log.md"))
        return str(path)

    _t_llm = time.perf_counter()
    try:
        user_prompt = DISCUSS_USER_TEMPLATE.format(title=title, content=body,
                                                   question=question)
        raw = llm.chat([{"role": "user", "content": user_prompt}],
                       system_prompt=DISCUSS_SYSTEM_PROMPT)
        logger.info("[discuss] LLM 调用成功 note_slug=%s", note_slug)
        emit_structured_log("discuss.llm_ok", duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            note_slug=note_slug, model=getattr(llm, "model", ""))
        disc = Discussion(
            slug=f"{note_slug}-discussion",
            topic=note_slug,
            title=title,
            question=question,
            distilled=True,
            source=source,
            distill_date=date.today().isoformat(),
            llm_model=getattr(llm, "model", ""),
            conflicts=_conflicts_from_text(raw),
            content=raw.strip(),
        )
        logger.info("[discuss] 讨论成功 slug=%s model=%s 冲突标记=%s",
                    disc.slug, disc.llm_model, disc.conflicts)
    except Exception as exc:
        # 降级铁律：LLM 超时/异常不抛异常 → 骨架讨论
        emit_structured_log("discuss.llm_failed", level="warning",
                            duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            note_slug=note_slug, reason="error", error=str(exc))
        disc = _skeleton_discussion(note_slug, title, source, question, "error")

    _write_discussion(path, disc)
    append_log("discuss", disc.slug,
               f"distilled={disc.distilled} model={disc.llm_model or 'none'}",
               log_path=str(root / "log.md"))
    return str(path)


def extract_card_insights(discussion_path: str | Path, llm=None) -> dict:
    """从讨论记录提炼 {one_line_insight, scope, links, conflicts} 供产卡。

    LLM 不可用/失败 → 降级返回空字段 dict（不抛异常）。
    """
    path = Path(discussion_path)
    if not path.is_file():
        raise FileNotFoundError(f"讨论记录不存在: {discussion_path}")
    disc = _read_discussion(path)

    if llm is None:
        logger.warning("[extract] LLM 不可用（llm=None），返回空提炼结果: %s", path.name)
        return {"one_line_insight": "", "scope": "", "links": [], "conflicts": []}

    _t_llm = time.perf_counter()
    try:
        user_prompt = INSIGHT_EXTRACT_USER_TEMPLATE.format(content=disc.content)
        raw = llm.chat([{"role": "user", "content": user_prompt}],
                       system_prompt=INSIGHT_EXTRACT_SYSTEM_PROMPT)
        logger.info("[extract] LLM 调用成功 %s", path.name)
        emit_structured_log("extract.llm_ok", duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            discussion=path.name, model=getattr(llm, "model", ""))
        data = _parse_llm_json(raw)
        result = {
            "one_line_insight": str(data.get("one_line_insight") or ""),
            "scope": str(data.get("scope") or ""),
            "links": _str_list(data.get("links")),
            "conflicts": _str_list(data.get("conflicts")),
        }
        # 回填讨论记录（便于审计与复用）
        disc.insight = result["one_line_insight"]
        disc.scope = result["scope"]
        disc.links = result["links"]
        disc.conflicts = [*result["conflicts"],
                          *(s for s in _conflicts_from_text(disc.content)
                            if s not in result["conflicts"])]
        _write_discussion(path, disc)
        logger.info("[extract] 提炼完成 %s insight=%r scope=%r links=%s conflicts=%s",
                    path.name, disc.insight, disc.scope, disc.links, disc.conflicts)
        return result
    except Exception as exc:
        # 降级铁律：JSON 解析失败/LLM 异常 → 空提炼结果，不抛异常
        reason = "json_error" if isinstance(exc, (ValueError, json.JSONDecodeError)) else "error"
        emit_structured_log("extract.llm_failed", level="warning",
                            duration_ms=(time.perf_counter() - _t_llm) * 1000,
                            discussion=path.name, reason=reason, error=str(exc))
        return {"one_line_insight": "", "scope": "", "links": [], "conflicts": []}
