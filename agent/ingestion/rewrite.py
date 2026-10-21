"""P2 LLM rewrite layer: L2 material -> Yunshu skill.md + archive.

Model/temperature/cache decision (2026-10-21):
- reuse the running Yunshu LLMService (get_server_state().Yunshu._llm);
- temperature=0.2, max_tokens=2000, timeout owned by LLMService;
- cache off by default; only when CHAT_RESPONSE_CACHE is on, cache by prompt hash
  under data/ingestion/cache (TTL 1h).

Boundaries: never execute source scripts/JS; LLM unavailable or invalid output =>
ok=False, commit is refused. Original text is archived even on failure.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .adapters.base import (
    first_heading,
    first_paragraph,
    is_valid_skill_id,
    slugify,
    split_front_matter,
)
from .models import NormalizedSkill

logger = logging.getLogger("agent.ingestion.rewrite")

PROMPT_VERSION = "ingest-rewrite-v1"
REWRITE_TEMPERATURE = 0.2
REWRITE_MAX_TOKENS = 2000
MAX_PROMPT_CHARS = 20_000
MAX_ARCHIVE_FILES_BYTES = 1_000_000
CACHE_TTL_SECONDS = 3600
DEFAULT_ARCHIVE_DIR = Path(__file__).resolve().parents[2] / "data" / "ingestion"
_ARCHIVE_ID_RE = re.compile(r"^ir_[0-9a-f]{24}$")
FENCE = "\x60\x60\x60"

SYSTEM_PROMPT = """你是云枢（Yunshu）的技能整理器。把用户给的外来材料改写成一份可被
云枢技能中心直接安装的 markdown 技能说明。

硬性要求：
1. 只能使用材料里真实存在的信息。材料没有写出的 API、参数、命令、依赖、示例，
   在对应章节写"材料未提供"，绝对不许编造。
2. 只输出 markdown，不要解释、不要客套、不要代码围栏包裹整篇。
3. 必须以 YAML front matter 开头，包含 id / name / description / version / tags。
4. 正文必须包含且只使用以下五个二级标题：适用场景、触发条件、步骤、参数、示例。
5. 保留材料的主要语言；英文材料可用 When to use / Triggers / Steps / Parameters /
   Examples，但内容不要翻译成与原文无关的臆造。
6. id 使用小写 kebab-case；name 是可读名称；description 一句话说明用途与触发时机。
"""

REQUIRED_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("适用场景", ("适用场景", "when to use", "use case", "uses")),
    ("触发条件", ("触发条件", "trigger")),
    ("步骤", ("步骤", "steps", "workflow")),
    ("参数", ("参数", "parameters", "parameter", "inputs")),
    ("示例", ("示例", "examples", "example")),
)
MIN_REWRITE_BODY_CHARS = 120


@dataclass
class RewriteResult:
    ok: bool
    archive_id: str
    archive_path: str
    skill: Optional[NormalizedSkill] = None
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    model: str = ""
    raw_output: str = ""
    cached: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "archive_id": self.archive_id,
            "archive_path": self.archive_path,
            "skill": self.skill.to_dict() if self.skill else None,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
            "model": self.model,
            "cached": bool(self.cached),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def _truthy_env(name: str) -> bool:
    return str(os.environ.get(name, "") or "").strip().lower() not in ("", "0", "false", "no", "off")


def model_name(llm_client: Any) -> str:
    if llm_client is None:
        return ""
    name = getattr(llm_client, "model", "") or getattr(llm_client, "model_name", "")
    return str(name or type(llm_client).__name__)


def default_llm_client() -> Any:
    try:
        from agent.state_manager import get_server_state
        yunshu = get_server_state().Yunshu
        for attr in ("_llm", "llm", "llm_service"):
            client = getattr(yunshu, attr, None) if yunshu is not None else None
            if client is not None:
                return client
    except Exception as exc:  # noqa: BLE001 runtime not ready is not config error
        logger.warning("[ingestion.rewrite] resolve LLM client failed: %s", exc)
    return None


def build_material(source: Any, asset: Any, *, limit: int = MAX_PROMPT_CHARS) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    body = str(getattr(asset, "body", "") or "")
    if len(body) > limit:
        warnings.append(f"材料正文 {len(body)} 字超过 prompt 上限 {limit}，已截断（存档保留全文）")
        body = body[:limit]
    manifest = getattr(asset, "manifest", None) or {}
    fm = getattr(asset, "front_matter", None) or {}
    meta: Dict[str, Any] = {}
    for key in ("name", "description", "tags", "version", "category"):
        value = manifest.get(key) if isinstance(manifest, dict) else None
        if value is None and isinstance(fm, dict):
            value = fm.get(key)
        if value not in (None, "", [], {}):
            meta[key] = value
    files = sorted((getattr(asset, "files", None) or {}).keys())
    asset_meta = getattr(asset, "meta", None) or {}
    commands = list(asset_meta.get("commands") or [])
    skills = list(asset_meta.get("skills") or [])
    lines = [
        "来源引用: " + str(getattr(source, "path", "") or getattr(source, "display_name", "")),
        "适配器: " + str(getattr(asset, "adapter", "") or ""),
        "来源类型: " + str(getattr(asset, "source_kind", "") or ""),
        "元数据: " + json.dumps(meta, ensure_ascii=False, default=str),
    ]
    if files:
        lines.append("脚本清单: " + "、".join(files))
    if commands:
        lines.append("命令清单: " + "、".join(str(x) for x in commands))
    if skills:
        lines.append("子技能清单: " + "、".join(str(x) for x in skills))
    lines.extend(["", "材料正文:", body or "（无正文）"])
    return "\n".join(lines), warnings


def build_prompt(source: Any, asset: Any) -> Tuple[str, List[str]]:
    material, warnings = build_material(source, asset)
    prompt = (
        "请把下面这份外来材料改写成一份云枢技能说明。\n"
        f"（prompt 版本：{PROMPT_VERSION}）\n\n"
        "=== 材料开始 ===\n"
        f"{material}\n"
        "=== 材料结束 ===\n\n"
        "只输出 markdown；front matter 字段：id/name/description/version/tags；"
        "正文必须包含 适用场景、触发条件、步骤、参数、示例 五个二级标题。"
    )
    return prompt, warnings


def _strip_code_fence(text: str) -> str:
    out = str(text or "").strip()
    if out.startswith(FENCE):
        lines = out.splitlines()
        if lines and lines[0].strip().startswith(FENCE):
            lines = lines[1:]
        if lines and lines[-1].strip() == FENCE:
            lines = lines[:-1]
        out = "\n".join(lines).strip()
    if out and not out.startswith("---"):
        idx = out.find("\n---")
        if 0 <= idx <= 200:
            out = out[idx + 1:].strip()
    return out


def _section_hits(body: str) -> Tuple[int, List[str]]:
    text = str(body or "").lower()
    missing: List[str] = []
    hits = 0
    for canonical, keywords in REQUIRED_SECTIONS:
        if any(keyword in text for keyword in keywords):
            hits += 1
        else:
            missing.append(canonical)
    return hits, missing


def validate_rewrite(text: str, source: Any, asset: Any, *,
                     archive_id: str, model: str) -> Tuple[Optional[NormalizedSkill], List[str], List[str]]:
    errors: List[str] = []
    warnings: List[str] = []
    cleaned = _strip_code_fence(text)
    if not cleaned:
        return None, ["LLM 输出为空"], warnings
    meta, body, fm_warnings = split_front_matter(cleaned)
    meta = dict(meta or {})
    warnings.extend(fm_warnings)
    body = str(body or "").strip()
    if not body:
        body = cleaned.strip()
    if len(body) < MIN_REWRITE_BODY_CHARS:
        errors.append(f"重写正文过短（{len(body)} < {MIN_REWRITE_BODY_CHARS} 字），疑似未真正重写")
    hits, missing = _section_hits(body)
    if hits < 3:
        errors.append("缺少必备章节（至少 3/5）：" + "、".join(missing))

    name = str(meta.get("name") or first_heading(body) or source.display_name).strip()[:200]
    if not name:
        errors.append("无法确定技能名称")
        name = "未命名重写技能"
    description = str(meta.get("description") or first_paragraph(body, limit=500)).strip()[:500]
    if not description:
        warnings.append("LLM 未给出 description，已从正文首段回填")
    raw_id = str(meta.get("id") or "").strip()
    if raw_id and is_valid_skill_id(raw_id):
        skill_id = raw_id
    else:
        skill_id = slugify(raw_id or source.display_name or name) or "ingested-rewrite"
        if raw_id:
            warnings.append(f"LLM 给出的 id 不合契约，已规范化为 {skill_id}（原值 {raw_id!r}）")
    if not is_valid_skill_id(skill_id):
        errors.append(f"重写结果 id 不合法：{skill_id!r}")
        skill_id = "ingested-rewrite"
    tags: List[str] = []
    raw_tags = meta.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    elif isinstance(raw_tags, str):
        tags = [part.strip() for part in re.split(r"[,;，；\s]+", raw_tags) if part.strip()]
    for required in ("ingested", "rewritten"):
        if required not in tags:
            tags.append(required)
    version = str(meta.get("version") or "1.0.0").strip()[:40]
    category = str(meta.get("category") or "ingested").strip()[:60]

    skill = NormalizedSkill(
        id=skill_id,
        name=name,
        description=description,
        body=body,
        version=version,
        author="llm-rewrite",
        category=category,
        source="ingestion:rewrite",
        tags=tags[:10],
        content_type="markdown",
        scripts=dict(getattr(asset, "files", None) or {}),
        extra={
            "adapter": "rewrite",
            "prompt_version": PROMPT_VERSION,
            "model": model,
            "archive_id": archive_id,
            "source_ref": str(getattr(source, "path", "") or getattr(source, "display_name", "")),
            "source_adapters": [str(getattr(asset, "adapter", "") or "")],
        },
    )
    if errors:
        return None, list(dict.fromkeys(errors)), warnings
    return skill, [], warnings


def _archive_id(source_ref: str, fingerprint: Dict[str, Any], model: str,
                output: str, ok: bool) -> str:
    material = json.dumps({
        "source_ref": source_ref,
        "fingerprint": fingerprint,
        "prompt_version": PROMPT_VERSION,
        "model": model,
        "output": output,
        "ok": bool(ok),
    }, ensure_ascii=False, sort_keys=True, default=str)
    return "ir_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def archive_dir_path(archive_dir: Optional[Any] = None) -> Path:
    return Path(archive_dir) if archive_dir else Path(DEFAULT_ARCHIVE_DIR)


def _archive_original(asset: Any, warnings: List[str]) -> Dict[str, Any]:
    body = str(getattr(asset, "body", "") or "")
    files: Dict[str, Any] = {}
    total = 0
    for name, text in sorted((getattr(asset, "files", None) or {}).items()):
        size = len(str(text).encode("utf-8", errors="replace"))
        if total + size <= MAX_ARCHIVE_FILES_BYTES:
            files[name] = str(text)
            total += size
        else:
            files[name] = {"sha256": _sha256(str(text)), "chars": len(str(text)),
                           "truncated": True}
    if any(isinstance(v, dict) for v in files.values()):
        warnings.append(f"脚本内容超过存档上限 {MAX_ARCHIVE_FILES_BYTES} 字节，超出部分只存哈希")
    return {
        "text": body,
        "chars": len(body),
        "files": files,
        "manifest": getattr(asset, "manifest", None),
        "front_matter": getattr(asset, "front_matter", None),
        "adapter": str(getattr(asset, "adapter", "") or ""),
        "source_kind": str(getattr(asset, "source_kind", "") or ""),
    }


def _write_archive(archive_dir: Path, payload: Dict[str, Any]) -> Path:
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / (payload["archive_id"] + ".json")
    tmp = archive_dir / (payload["archive_id"] + ".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def _cache_path(archive_dir: Path, key: str) -> Path:
    return archive_dir / "cache" / (key + ".json")


def _cache_get(archive_dir: Path, key: str) -> Optional[str]:
    if not _truthy_env("CHAT_RESPONSE_CACHE"):
        return None
    try:
        data = json.loads(_cache_path(archive_dir, key).read_text(encoding="utf-8"))
        created = float(data.get("created_at") or 0)
        if time.time() - created > CACHE_TTL_SECONDS:
            return None
        output = data.get("output")
        return str(output) if isinstance(output, str) and output else None
    except (OSError, ValueError, TypeError):
        return None


def _cache_set(archive_dir: Path, key: str, output: str) -> None:
    if not _truthy_env("CHAT_RESPONSE_CACHE") or not output:
        return
    try:
        path = _cache_path(archive_dir, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"created_at": time.time(), "output": output},
                                   ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning("[ingestion.rewrite] write cache failed(advisory): %s", exc)


def _call_llm(client: Any, prompt: str) -> str:
    messages = [{"role": "user", "content": prompt}]
    if hasattr(client, "chat"):
        try:
            return str(client.chat(messages, system_prompt=SYSTEM_PROMPT,
                                   max_tokens=REWRITE_MAX_TOKENS,
                                   temperature=REWRITE_TEMPERATURE))
        except TypeError:
            try:
                return str(client.chat(prompt))
            except TypeError:
                return str(client.chat(messages))
    if hasattr(client, "complete"):
        return str(client.complete(prompt))
    raise TypeError("LLM client has no chat/complete method")


def rewrite_asset(source: Any, asset: Any, *,
                  source_ref: str,
                  source_fingerprint: Dict[str, Any],
                  llm_client: Any = None,
                  archive_dir: Optional[Any] = None) -> RewriteResult:
    base_dir = archive_dir_path(archive_dir)
    prompt, material_warnings = build_prompt(source, asset)
    warnings: List[str] = list(material_warnings)
    errors: List[str] = []
    model = model_name(llm_client)
    output = ""
    cached = False

    if llm_client is None:
        errors.append("LLM 客户端不可用（运行时未就绪或未注入）；原文已存档，commit 会被拒绝")
    else:
        cache_key = _sha256(json.dumps({
            "prompt": prompt, "model": model, "prompt_version": PROMPT_VERSION,
            "temperature": REWRITE_TEMPERATURE, "max_tokens": REWRITE_MAX_TOKENS,
        }, ensure_ascii=False, sort_keys=True))
        hit = _cache_get(base_dir, cache_key)
        if hit:
            output = hit
            cached = True
            warnings.append("命中 CHAT_RESPONSE_CACHE 响应缓存")
        else:
            try:
                output = _call_llm(llm_client, prompt)
                _cache_set(base_dir, cache_key, output)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"LLM 调用失败：{type(exc).__name__}: {exc}")
                logger.warning("[ingestion.rewrite] LLM call failed: %s", exc)

    archive_id = _archive_id(source_ref, source_fingerprint, model, output, False)
    skill, schema_errors, schema_warnings = validate_rewrite(
        output, source, asset, archive_id=archive_id, model=model)
    errors.extend(schema_errors)
    warnings.extend(schema_warnings)
    ok = bool(skill is not None and not errors)
    archive_id = _archive_id(source_ref, source_fingerprint, model, output, ok)
    if skill is not None:
        skill.extra["archive_id"] = archive_id
    payload = {
        "schema_version": 1,
        "archive_id": archive_id,
        "created_at": _now_iso(),
        "source": {"ref": source_ref, "fingerprint": source_fingerprint,
                   "display_name": getattr(source, "display_name", "")},
        "adapter": str(getattr(asset, "adapter", "") or ""),
        "prompt_version": PROMPT_VERSION,
        "prompt": prompt,
        "model": {
            "name": model,
            "temperature": REWRITE_TEMPERATURE,
            "max_tokens": REWRITE_MAX_TOKENS,
            "cache": bool(_truthy_env("CHAT_RESPONSE_CACHE")),
            "cached_hit": bool(cached),
        },
        "original": _archive_original(asset, warnings),
        "llm_output": output,
        "validation": {"ok": ok, "errors": list(dict.fromkeys(errors)),
                       "warnings": list(dict.fromkeys(warnings))},
        "skill": None,
    }
    if skill is not None and ok:
        payload["skill"] = {
            "id": skill.id, "name": skill.name, "description": skill.description,
            "body": skill.body, "version": skill.version, "author": skill.author,
            "category": skill.category, "source": skill.source,
            "tags": list(skill.tags), "content_type": skill.content_type,
            "scripts": dict(skill.scripts), "extra": dict(skill.extra),
        }
        payload["skill_sha256"] = _sha256(
            json.dumps(payload["skill"], ensure_ascii=False,
                       sort_keys=True, default=str))
    try:
        path = _write_archive(base_dir, payload)
    except OSError as exc:
        errors.append(f"原文存档失败：{type(exc).__name__}: {exc}")
        path = base_dir / (archive_id + ".json")
        ok = False
        skill = None
        payload["validation"] = {"ok": False, "errors": list(dict.fromkeys(errors)),
                                 "warnings": list(dict.fromkeys(warnings))}
        payload["skill"] = None
    return RewriteResult(
        ok=ok,
        archive_id=archive_id,
        archive_path=str(path),
        skill=skill if ok else None,
        warnings=list(dict.fromkeys(warnings)),
        errors=list(dict.fromkeys(errors)),
        model=model,
        raw_output=output,
        cached=cached,
    )


def load_archive(archive_id: str, archive_dir: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    aid = str(archive_id or "").strip()
    if not _ARCHIVE_ID_RE.match(aid):
        return None
    try:
        data = json.loads((archive_dir_path(archive_dir) / (aid + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("archive_id") != aid:
        return None
    source = data.get("source") or {}
    model = data.get("model") or {}
    validation = data.get("validation") or {}
    try:
        expected = _archive_id(str(source.get("ref") or ""), source.get("fingerprint") or {},
                               str(model.get("name") or ""), str(data.get("llm_output") or ""),
                               bool(validation.get("ok")))
    except Exception:  # noqa: BLE001 坏存档一律视为不可用
        return None
    if expected != aid:
        return None
    raw_skill = data.get("skill")
    if isinstance(raw_skill, dict):
        actual = _sha256(json.dumps(raw_skill, ensure_ascii=False, sort_keys=True, default=str))
        if data.get("skill_sha256") != actual:
            return None
    return data


def skill_from_archive(archive: Dict[str, Any]) -> Optional[NormalizedSkill]:
    raw = archive.get("skill") if isinstance(archive, dict) else None
    if not isinstance(raw, dict):
        return None
    skill_id = str(raw.get("id") or "")
    if not is_valid_skill_id(skill_id):
        return None
    return NormalizedSkill(
        id=skill_id,
        name=str(raw.get("name") or skill_id),
        description=str(raw.get("description") or ""),
        body=str(raw.get("body") or ""),
        version=str(raw.get("version") or "1.0.0"),
        author=str(raw.get("author") or "llm-rewrite"),
        category=str(raw.get("category") or "ingested"),
        source=str(raw.get("source") or "ingestion:rewrite"),
        tags=list(raw.get("tags") or []),
        content_type=str(raw.get("content_type") or "markdown"),
        scripts=dict(raw.get("scripts") or {}),
        extra=dict(raw.get("extra") or {}),
    )


__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULT_ARCHIVE_DIR",
    "MAX_PROMPT_CHARS",
    "PROMPT_VERSION",
    "REWRITE_MAX_TOKENS",
    "REWRITE_TEMPERATURE",
    "REQUIRED_SECTIONS",
    "RewriteResult",
    "SYSTEM_PROMPT",
    "archive_dir_path",
    "build_material",
    "build_prompt",
    "default_llm_client",
    "load_archive",
    "model_name",
    "rewrite_asset",
    "skill_from_archive",
    "validate_rewrite",
]
