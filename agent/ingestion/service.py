"""吞噬服务（P0）：preview / commit 的**唯一**域入口

流程（与交接单的「三级判定」一致）：

    source → 适配器选择（registry）→ 抽取（只读）→ 三级判定（L0/L1/L2）
           → 归一化（L0/L1）→ 吸收形态建议 → 可预览计划（plan_id）
           → 用户确认 → commit（复用 skills_mgmt.install_from_zip）

**P0 的边界**（写死在代码而非口头）：

- 不接 LLM：L2 计划里 `requires_rewrite=True`，commit 抛 `RewriteRequired`；
- 不碰真实目录：适配器只读用户传入的路径，测试用 tmp_path fixture；
- 不执行来源里的任何脚本：只读 `scripts/*.py` 的**内容**并原样搬运；
- 不另造安装链路：commit 在临时目录拼出 `manifest.json + skill.md + scripts/`，
  调既有 `SkillsMgmtService.install_from_zip`（P1/P3/P4 都复用这一条）。

**plan_id 是"预览即凭据"**：commit 会**重算**预览并比对 plan_id。来源在预览后
变了一个字节/一个 mtime，plan_id 就不同 ⇒ 409 `plan-stale`，用户必须重新预览。
这样不需要在服务端保存计划（无内存状态、无 TTL、重启不失效），也避免"按旧计划
安装新内容"的 TOCTOU。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import registry as registry_mod
from .adapters import ExtractedAsset
from .models import (
    FORM_SKILL,
    LEVEL_L0,
    LEVEL_L1,
    LEVEL_L2,
    Conformance,
    ConfirmationRequired,
    IngestPlan,
    IngestResult,
    IngestSource,
    IngestionError,
    InstallFailed,
    InvalidSource,
    MergeFailed,
    NormalizedSkill,
    PlanStale,
    Recommendation,
    RewriteRequired,
    SourceNotFound,
    SourceTooLarge,
    plan_fingerprint,
)
from .adapters.base import first_heading, first_paragraph, is_valid_skill_id, slugify

logger = logging.getLogger("agent.ingestion.service")

#: 纯文本来源上限（HTTP 层也按同一常量校验）
MAX_SOURCE_TEXT_CHARS = 200_000
#: 目录指纹最多采集的条目数（防止对超大目录做 O(n) 扫描）
MAX_FINGERPRINT_ENTRIES = 200
#: 计划里正文预览的默认上限
PLAN_BODY_LIMIT = 4000
#: 来源摘录上限（L2 预览用）
RAW_EXCERPT_LIMIT = 1200
#: 指纹递归跳过的目录（与 adapters.base._SKIP_DIRS 同口径；不 import 私有名）
_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".svn", ".hg", "dist", "build"}

#: 名称/描述等字段的别名（front matter 键名不同的来源在这里映射；P1 可继续追加）
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "id": ("id", "skill_id", "slug"),
    "name": ("name", "title", "display_name", "skill_name"),
    "description": ("description", "summary", "desc", "description_zh"),
    "version": ("version",),
    "author": ("author", "maintainer"),
    "category": ("category", "type"),
    "tags": ("tags", "keywords"),
    "content_type": ("content_type", "format"),
}


# ════════════════════════════════════════════════════════════
#  来源准备 / 指纹
# ════════════════════════════════════════════════════════════


def _prepare_source(raw: Any) -> IngestSource:
    source = IngestSource.from_mapping(raw)
    if source.kind == "text":
        if len(source.text) > MAX_SOURCE_TEXT_CHARS:
            raise SourceTooLarge(
                f"原文超过 {MAX_SOURCE_TEXT_CHARS} 字上限",
                detail=f"当前 {len(source.text)} 字；请裁剪后再试（P0 不缓存超长原文）",
            )
        return source
    expanded = os.path.expandvars(os.path.expanduser(source.path))
    path = Path(expanded)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SourceNotFound(
            f"来源不存在或不可访问：{source.path}",
            detail=f"{type(exc).__name__}: {exc}",
        ) from exc
    if not resolved.exists():
        raise SourceNotFound(f"来源不存在：{source.path}")
    return IngestSource(
        kind="path",
        path=str(resolved),
        label=source.label,
        ecosystem=source.ecosystem,
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="replace")).hexdigest()


def _source_fingerprint(source: IngestSource) -> Dict[str, Any]:
    """来源指纹：路径 + 结构/时间戳；文本 + 内容摘要。"""
    if source.kind == "text":
        return {"kind": "text", "chars": len(source.text), "sha256": _sha256_text(source.text)}
    root = Path(source.path)
    if root.is_file():
        try:
            stat = root.stat()
            return {"kind": "file", "path": str(root), "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns}
        except OSError as exc:
            raise SourceNotFound(f"来源文件不可读：{source.path}", detail=str(exc)) from exc
    entries: List[Dict[str, Any]] = []
    queue: List[Path] = [root]
    truncated = False
    idx = 0
    while idx < len(queue) and len(entries) < MAX_FINGERPRINT_ENTRIES:
        current = queue[idx]
        idx += 1
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            continue
        for child in children:
            if child.name in _SKIP_DIRS or child.name.startswith("."):
                continue
            try:
                if child.is_symlink():
                    continue
                if child.is_dir():
                    queue.append(child)
                    continue
                if child.is_file():
                    stat = child.stat()
                    entries.append({
                        "rel": child.relative_to(root).as_posix(),
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                    })
            except OSError:
                continue
            if len(entries) >= MAX_FINGERPRINT_ENTRIES:
                truncated = True
                break
    return {"kind": "directory", "path": str(root), "entries": entries,
            "truncated": truncated or idx < len(queue)}


def _extraction_digest(asset: ExtractedAsset) -> Dict[str, Any]:
    """抽取结果摘要（plan_id 的实质部分；正文/脚本只进哈希）。"""
    return {
        "adapter": asset.adapter,
        "source_kind": asset.source_kind,
        "manifest": asset.manifest,
        "manifest_kind": asset.manifest_kind,
        "front_matter": asset.front_matter,
        "body_sha256": _sha256_text(asset.body),
        "body_chars": asset.body_chars,
        "files": {name: _sha256_text(text) for name, text in sorted(asset.files.items())},
        "canonical_layout": asset.canonical_layout,
        "primary_file": asset.primary_file,
    }


def _source_ref(source: IngestSource) -> str:
    if source.kind == "text":
        return "text:sha256:" + _sha256_text(source.text)
    return "path:" + source.path


# ════════════════════════════════════════════════════════════
#  三级判定
# ════════════════════════════════════════════════════════════


def _filled(mapping: Dict[str, Any], key: str) -> bool:
    if not isinstance(mapping, dict) or key not in mapping:
        return False
    value = mapping.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


def _filled_any(mapping: Dict[str, Any], keys: Tuple[str, ...]) -> bool:
    return any(_filled(mapping, key) for key in keys)


def _pick(mapping: Dict[str, Any], aliases: Tuple[str, ...]) -> Tuple[Any, str]:
    for key in aliases:
        if _filled(mapping, key):
            return mapping.get(key), key
    return None, ""


def _as_text(value: Any, limit: int = 0) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = "、".join(str(v) for v in value)
    elif isinstance(value, dict):
        value = json.dumps(value, ensure_ascii=False, default=str)
    text = str(value).strip()
    return text[:limit] if limit > 0 else text


def _as_tags(value: Any) -> List[str]:
    raw: List[str] = []
    if isinstance(value, str):
        import re
        raw = [part for part in re.split(r"[,;，；\s]+", value) if part]
    elif isinstance(value, (list, tuple, set)):
        raw = [str(v) for v in value]
    out: List[str] = []
    for item in raw:
        token = str(item).strip()
        if token and token not in out:
            out.append(token)
    return out[:10]


def _merge_front_matter(fm: Dict[str, Any]) -> Dict[str, Any]:
    """front matter 顶层优先；`metadata:` 嵌套只补缺失键（不覆盖显式顶层）。"""
    merged = dict(fm or {})
    nested = merged.get("metadata")
    if isinstance(nested, dict):
        for key, value in nested.items():
            merged.setdefault(key, value)
    return merged


def classify(asset: ExtractedAsset) -> Conformance:
    """L0 / L1 / L2 三级判定（纯函数，不读文件）。"""
    manifest = asset.manifest if isinstance(asset.manifest, dict) else {}
    fm = _merge_front_matter(asset.front_matter if isinstance(asset.front_matter, dict) else {})
    has_body = bool(str(asset.body or "").strip())

    canonical_manifest = (
        asset.manifest_kind == "skill_json"
        and _filled(manifest, "id")
        and _filled(manifest, "name")
        and (_filled(manifest, "content") or has_body)
    )
    packaging = str((asset.meta or {}).get("packaging") or "")
    # zip 内 manifest.json + skill.md 已是既有 install_from_zip 的安装包结构 ⇒ L0。
    # 【为什么 zip 的单文件 SKILL.md 不算 L0】它缺 manifest，仍需适配器补写（L1）。
    zip_canonical = (
        packaging == "zip"
        and asset.manifest_kind == "manifest_json"
        and _filled(manifest, "id")
        and _filled(manifest, "name")
        and has_body
    )
    canonical_front = (
        packaging != "zip"
        and bool(asset.canonical_layout)
        and _filled_any(fm, _ALIASES["id"])
        and _filled_any(fm, _ALIASES["name"])
        and _filled_any(fm, _ALIASES["description"])
        and has_body
    )
    if canonical_manifest or canonical_front or zip_canonical:
        reasons: List[str] = []
        if canonical_manifest:
            reasons.append("skill.json 含 id/name/content")
        if canonical_front:
            reasons.append("SKILL.md front matter 含 id/name/description 且目录结构合规")
        if zip_canonical:
            reasons.append("zip 内 manifest.json + skill.md 已是云枢安装包结构")
        return Conformance(level=LEVEL_L0, reasons=tuple(reasons), missing=())

    missing: List[str] = []
    if not manifest and not fm:
        missing.append("manifest/front_matter")
    for canonical, aliases in (("id", _ALIASES["id"]), ("name", _ALIASES["name"]),
                               ("description", _ALIASES["description"])):
        if not (_filled_any(manifest, aliases) or _filled_any(fm, aliases)):
            missing.append(canonical)
    if not has_body:
        missing.append("content")
    if not asset.has_scripts:
        # 脚本不是必需项；只有"目录+脚本"这种 L1 形态需要它在 missing 里被提到
        pass

    if manifest or fm or asset.canonical_layout or asset.has_scripts:
        reasons = ["来源含部分技能标记，但不符合云枢 L0 schema"]
        if asset.manifest_kind == "manifest_json":
            reasons.append("使用的是 zip manifest.json，而不是云枢 skill.json / skill.md front matter")
        if asset.canonical_layout and fm and not _filled_any(fm, _ALIASES["id"]):
            reasons.append("SKILL.md front matter 缺少 id（将按来源名生成）")
        if asset.source_kind == "file":
            reasons.append("单文件来源，需补齐目录结构/清单")
        if not has_body:
            reasons.append("缺少技能正文")
        if asset.has_scripts and not has_body:
            reasons.append("只有目录与脚本，没有说明正文")
        return Conformance(level=LEVEL_L1, reasons=tuple(dict.fromkeys(reasons)),
                           missing=tuple(dict.fromkeys(missing)))

    reasons = ["没有任何技能清单/入口标记（无 skill.json、SKILL.md front matter、manifest.json）"]
    if (asset.meta or {}).get("js_plugin"):
        reasons.append("JS 插件包只抽取 README/commands/tools 清单，不能执行，需要重写")
    elif asset.source_kind == "text":
        reasons.append("纯文本/提示词原文，需要重写为云枢 skill.md")
    else:
        reasons.append("文档/说明集合，需要重写为云枢 skill.md")
    return Conformance(level=LEVEL_L2, reasons=tuple(reasons),
                       missing=tuple(dict.fromkeys(missing or ["manifest/front_matter"])))


# ════════════════════════════════════════════════════════════
#  归一化
# ════════════════════════════════════════════════════════════


def _derive_id(source: IngestSource, manifest: Dict[str, Any], fm: Dict[str, Any],
               name: str) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    explicit, _ = _pick(manifest, _ALIASES["id"])
    if explicit is None:
        explicit, _ = _pick(fm, _ALIASES["id"])
    if explicit is not None:
        candidate = str(explicit).strip()
        if is_valid_skill_id(candidate):
            return candidate, warnings
        slug = slugify(candidate)
        if slug:
            warnings.append(f"来源 id 不符合云枢契约，已规范化为 {slug}（原值 {candidate!r}）")
            return slug, warnings
        warnings.append(f"来源 id 无法转换为合法技能 id，已忽略（原值 {candidate!r}）")

    for raw in (source.display_name, Path(source.path).stem if source.kind == "path" else "",
                name):
        slug = slugify(raw)
        if slug:
            return slug, warnings
    digest = hashlib.sha256(json.dumps(source.identity(), sort_keys=True).encode("utf-8")).hexdigest()
    generated = "ingested-" + digest[:8]
    warnings.append(f"无法从来源名生成技能 id，已用确定性 id {generated}")
    return generated, warnings


def _skeleton_body(name: str, description: str, scripts: Dict[str, str]) -> str:
    """只有脚本/描述时生成**不编造能力**的说明骨架。"""
    lines = [f"# {name}", "", "## 说明", "",
             description.strip() or "（来源未提供说明）", ""]
    if scripts:
        lines.extend(["## 脚本", ""])
        for script_name in sorted(scripts):
            lines.append(f"- `scripts/{script_name}`")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def normalize(source: IngestSource, asset: ExtractedAsset) -> Tuple[NormalizedSkill, Dict[str, str], List[str]]:
    """适配器抽取结果 → 云枢技能（不落库）。

    Returns:
        (skill, mapped_keys, warnings)
    """
    warnings: List[str] = list(asset.warnings or [])
    manifest = asset.manifest if isinstance(asset.manifest, dict) else {}
    fm = _merge_front_matter(asset.front_matter if isinstance(asset.front_matter, dict) else {})
    mapped: Dict[str, str] = {}

    def take(field: str) -> Any:
        value, key = _pick(manifest, _ALIASES[field])
        if value is None:
            value, key = _pick(fm, _ALIASES[field])
        if key and key != field:
            mapped[key] = field
        return value

    name = _as_text(take("name"), 200)
    if not name:
        name = first_heading(asset.body) or (
            source.display_name if source.kind == "path" else "粘贴的原文")
    description = _as_text(take("description"), 500)
    if not description:
        description = first_paragraph(asset.body)
    if not description:
        warnings.append("来源未提供 description（计划里留空，不编造）")

    skill_id, id_warnings = _derive_id(source, manifest, fm, name)
    warnings.extend(id_warnings)

    body = str(asset.body or "")
    if not body.strip():
        if asset.has_scripts or description:
            body = _skeleton_body(name, description, asset.files)
            warnings.append("正文为空：已按 description/脚本清单生成骨架（不编造能力）")
    if not body.strip():
        warnings.append("正文为空：没有可安装的说明内容")

    tags = _as_tags(take("tags"))
    for required in ("ingested", asset.adapter):
        if required and required not in tags:
            tags.append(required)
    category = _as_text(take("category"), 60) or "ingested"
    version = _as_text(take("version"), 40) or "1.0.0"
    author = _as_text(take("author"), 120)
    content_type = _as_text(take("content_type"), 40) or "markdown"

    meta = asset.meta or {}
    extra = {
        "adapter": asset.adapter,
        "manifest_kind": asset.manifest_kind,
        "source_kind": asset.source_kind,
        "source_ref": _source_ref(source),
        "front_matter_keys": sorted(str(k) for k in (asset.front_matter or {})),
        "mapped_keys": dict(mapped),
        "primary_file": asset.primary_file,
        "ecosystem": str(meta.get("ecosystem") or ""),
        "packaging": str(meta.get("packaging") or ""),
        "js_plugin": bool(meta.get("js_plugin")),
        "clawhub_meta": meta.get("clawhub_meta") or {},
    }
    skill = NormalizedSkill(
        id=skill_id,
        name=name,
        description=description,
        body=body,
        version=version,
        author=author,
        category=category,
        source="ingestion:" + asset.adapter,
        tags=tags,
        content_type=content_type,
        scripts=dict(asset.files or {}),
        extra=extra,
    )
    return skill, mapped, warnings


# ════════════════════════════════════════════════════════════
#  建议 / 解释
# ════════════════════════════════════════════════════════════


def recommend(conformance: Conformance) -> Recommendation:
    if conformance.level == LEVEL_L0:
        return Recommendation(
            form=FORM_SKILL,
            reason="来源已是云枢技能结构，默认按 markdown 技能直入；工作流/代码工具需 P4 证据。",
            confidence=0.8,
        )
    if conformance.level == LEVEL_L1:
        return Recommendation(
            form=FORM_SKILL,
            reason="部分符合：适配器可补齐清单/front matter，默认仍按 markdown 技能吸收。",
            confidence=0.6,
        )
    return Recommendation(
        form=FORM_SKILL,
        reason="不符合：需 LLM 重写成 markdown 技能（P2）；不能直接执行原文。",
        confidence=0.3,
    )


def _script_names(asset: ExtractedAsset) -> str:
    names = sorted(asset.files)
    shown = "、".join(names[:5])
    if len(names) > 5:
        shown += f" 等 {len(names)} 个"
    return shown or "0 个"


def _actions(source: IngestSource, asset: ExtractedAsset, conformance: Conformance,
             skill: Optional[NormalizedSkill], mapped: Dict[str, str]) -> List[str]:
    actions: List[str] = []
    if conformance.level == LEVEL_L0:
        actions.append("直接安装：来源已是云枢技能结构，无需改造")
        if asset.has_scripts:
            actions.append(f"附带 scripts/*.py：{_script_names(asset)}（只搬运，不执行）")
        return actions

    if conformance.level == LEVEL_L1:
        actions.append("补写 manifest.json（id/name/description/version/category）")
        manifest = asset.manifest if isinstance(asset.manifest, dict) else {}
        fm = _merge_front_matter(asset.front_matter if isinstance(asset.front_matter, dict) else {})
        if skill is not None and not (_filled_any(manifest, _ALIASES["id"])
                                      or _filled_any(fm, _ALIASES["id"])):
            actions.append(f"按来源名生成技能 id：{skill.id}")
        if mapped:
            pairs = "、".join(f"{src}→{dst}" for src, dst in sorted(mapped.items()))
            actions.append("front matter 键映射：" + pairs)
        if not str(asset.body or "").strip():
            actions.append("用 description/脚本清单生成说明骨架（不编造能力）")
        if asset.has_scripts:
            actions.append(f"搬运 scripts/*.py：{_script_names(asset)}（只搬运，不执行）")
        actions.append("保留来源引用：" + _source_ref(source))
        return actions

    actions.append("等待 LLM 重写层（P2）：把原文改写成云枢 skill.md（适用场景/触发条件/步骤/参数/示例）")
    actions.append("原文全文存档（P2）")
    actions.append("P0 阶段 commit 会被拒绝（rewrite_required）")
    return actions


def _raw_summary(asset: ExtractedAsset) -> Dict[str, Any]:
    body = str(asset.body or "")
    meta = asset.meta or {}
    return {
        "chars": len(body),
        "files": sorted(asset.files),
        "doc_files": list(meta.get("doc_files") or []),
        "manifest_kind": asset.manifest_kind,
        "ecosystem": str(meta.get("ecosystem") or ""),
        "packaging": str(meta.get("packaging") or ""),
        "js_plugin": bool(meta.get("js_plugin")),
        "commands": list(meta.get("commands") or []),
        "skills": list(meta.get("skills") or []),
        "excerpt": body[:RAW_EXCERPT_LIMIT],
        "excerpt_truncated": len(body) > RAW_EXCERPT_LIMIT,
    }


# ════════════════════════════════════════════════════════════
#  preview / commit
# ════════════════════════════════════════════════════════════


def _extract(selection: registry_mod.Selection, source: IngestSource) -> ExtractedAsset:
    try:
        asset = selection.adapter.extract(source)
    except IngestionError:
        raise
    except OSError as exc:
        raise SourceNotFound(
            f"来源不可读：{source.path or source.display_name}",
            detail=str(exc),
        ) from exc
    except Exception as exc:  # noqa: BLE001 抽取失败要响亮，不静默降级
        logger.exception("[ingestion] adapter extract failed name=%s", selection.name)
        raise IngestionError(
            "适配器抽取失败",
            detail=f"{selection.name}: {type(exc).__name__}: {exc}",
            status=500,
            code="extract-failed",
            title="来源抽取失败",
        ) from exc
    if not isinstance(asset, ExtractedAsset):
        raise IngestionError("适配器返回了非法抽取结果", status=500, code="extract-failed")
    asset.adapter = asset.adapter or selection.name
    return asset


def _get_skills_service() -> Any:
    """读取技能库服务（dedupe 用；失败返回 None，不阻断预览）。"""
    try:
        from agent.state_manager import get_skills_mgmt_service
        return get_skills_mgmt_service()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ingestion] 技能库服务不可用，跳过去重分析: %s", exc)
        return None


def _analyze_duplicates(*, skill_id: str, name: str, description: str,
                        content: str, skills_service: Any = None
                        ) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]], List[str]]:
    """P3 去重/合并建议（复用 assessor / SkillReviewer，不另造口径）。

    Returns:
        (dedupe, merge_suggestion, warnings)
    """
    dedupe: List[Dict[str, Any]] = []
    warnings: List[str] = []
    existing: List[Dict[str, Any]] = []
    svc = skills_service if skills_service is not None else _get_skills_service()
    target_id = skill_id if is_valid_skill_id(skill_id) else "incoming-skill"
    others: List[Any] = []
    if svc is not None:
        try:
            others = list(svc.store.list_all())
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"技能库读取失败，去重仅含原生重叠：{type(exc).__name__}")
            others = []

    # 同名 id / 同名 name（不依赖 Jaccard，优先提示）
    for other in others:
        other_id = str(getattr(other, "id", "") or "")
        if not other_id:
            continue
        if other_id == target_id:
            existing.append({
                "kind": "existing", "id": other_id,
                "name": str(getattr(other, "name", "") or other_id),
                "similarity": 1.0, "action": "overwrite", "strong": True,
                "matched": [], "match": "id",
                "reason": "同名技能 id 已存在；commit 会覆盖原技能（或选择 merge_into 其他目标）",
            })
            warnings.append(f"技能 id 已存在：{other_id}；本次 commit 将覆盖")
        elif name and (str(getattr(other, "name", "") or "").strip().lower()
                       == str(name).strip().lower()):
            existing.append({
                "kind": "existing", "id": other_id,
                "name": str(getattr(other, "name", "") or other_id),
                "similarity": 0.0, "action": "review", "strong": True,
                "matched": [], "match": "name",
                "reason": "技能名称完全相同；建议人工确认是否合并",
            })

    # Jaccard / 内容哈希（复用 SkillReviewer.find_duplicates_for）
    if svc is not None and others and hasattr(svc, "reviewer"):
        try:
            from agent.skills_mgmt.models import Skill
            target = Skill(id=target_id, name=name or target_id,
                           description=description or "", content=content or "")
            dups = svc.reviewer.find_duplicates_for(
                target, [o for o in others if str(getattr(o, "id", "")) != target_id],
                min_jaccard=0.7)
        except Exception as exc:  # noqa: BLE001
            dups = []
            warnings.append(f"既有技能去重失败（advisory）：{type(exc).__name__}")
        by_id = {item["id"]: item for item in existing}
        for dup in dups:
            other_id = str(dup.get("other_id") or "")
            if not other_id:
                continue
            sim = float(dup.get("jaccard") or 0.0)
            action = str(dup.get("recommend_action") or "review")
            reason = ("内容哈希完全一致（重复）" if dup.get("content_hash_match")
                      else f"Jaccard {sim:.2f} ≥ 0.70")
            if other_id in by_id:
                item = by_id[other_id]
                # 同名但内容也高度相似：把 review 升级为 merge；同 id 的 overwrite 保持
                if item.get("action") == "review" and sim >= float(item.get("similarity") or 0):
                    item.update({"similarity": round(sim, 4), "action": action,
                                 "strong": bool(dup.get("content_hash_match")),
                                 "match": "content", "reason": reason})
                continue
            existing.append({
                "kind": "existing", "id": other_id,
                "name": str(dup.get("other_name") or other_id),
                "similarity": round(sim, 4), "action": action,
                "strong": bool(dup.get("content_hash_match")),
                "matched": [], "match": "content",
                "reason": reason,
            })

    dedupe.extend(existing)

    # 原生能力重叠（detect_native_duplicates；只做吸收提示，不建议 merge）
    try:
        from agent.skills_mgmt.assessor import detect_native_duplicates
        for nat in detect_native_duplicates(name or "", description or "", content or ""):
            dedupe.append({
                "kind": "native", "id": str(nat.get("id") or ""),
                "name": str(nat.get("name") or nat.get("id") or ""),
                "similarity": 0.0, "action": "absorb",
                "strong": bool(nat.get("strong")),
                "matched": list(nat.get("matched") or []), "match": "native",
                "reason": "与云枢原生能力重叠；按增量吸收保留，不整包拒绝",
            })
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"原生能力重叠检测失败（advisory）：{type(exc).__name__}")

    # 合并建议：只指向「既有的其他技能」，同 id 不能 merge 到自身
    candidates = [d for d in existing
                  if d["id"] != target_id and d["action"] in ("merge", "review")]
    merge_suggestion: Optional[Dict[str, Any]] = None
    if candidates:
        top = max(candidates, key=lambda d: (d["similarity"], d["match"] == "name"))
        merge_suggestion = {
            "target_id": top["id"],
            "target_name": top["name"],
            "jaccard": top["similarity"],
            "action": top["action"],
            "reason": top["reason"],
        }
    return dedupe, merge_suggestion, warnings


def _native_overlap_text(skill: NormalizedSkill) -> str:
    try:
        from agent.skills_mgmt.assessor import detect_native_duplicates
        hits = detect_native_duplicates(skill.name or "", skill.description or "", skill.body or "")
    except Exception:  # noqa: BLE001
        return ""
    return "、".join(f"「{h['name']}」({h['id']})" for h in hits)


def _default_llm_client() -> Any:
    """生产 LLM 客户端：复用运行中云枢实例（函数体内导入，保持叶子包）。"""
    from . import rewrite as rewrite_mod
    return rewrite_mod.default_llm_client()


def _rewritten_plan_id(source: IngestSource, archive_id: str) -> str:
    """重写计划的 id：只由来源指纹 + archive_id 决定（不依赖再次调 LLM）。"""
    return plan_fingerprint({
        "source": {"identity": source.identity(),
                   "fingerprint": _source_fingerprint(source)},
        "archive_id": archive_id,
        "rewritten": True,
    })


def _rewrite_actions(archive_id: str, scripts: List[str]) -> List[str]:
    actions = [
        "已由 LLM 重写为云枢 skill.md（请先人工预览草稿）",
        "原文/重写结果已存档：" + archive_id,
    ]
    if scripts:
        shown = "、".join(scripts[:5]) + (f" 等 {len(scripts)} 个" if len(scripts) > 5 else "")
        actions.append(f"附带 scripts/*.py：{shown}（只搬运，不执行）")
    return actions


def preview(source: Any, *, registry: Optional[registry_mod.AdapterRegistry] = None,
            rewrite: bool = False, llm_client: Any = None,
            archive_dir: Optional[Any] = None, dedupe: bool = False,
            skills_service: Any = None) -> IngestPlan:
    """生成可预览的吞噬计划（dry-run；不落库、不执行来源脚本）。

    Args:
        rewrite: L2 来源是否调用 LLM 重写（默认 False = 只预览原料）。
        llm_client: 测试/调用方注入的 LLM 客户端；None = 运行时云枢实例。
        archive_dir: 原文/重写结果存档目录（默认 data/ingestion）。
        dedupe: 是否分析既有技能/原生能力重复（默认 False；HTTP 层传 True）。
        skills_service: 去重分析用的技能库服务；None = 运行时单例（测试可注入）。
    """
    prepared = _prepare_source(source)
    reg = registry or registry_mod.get_registry()
    selection = reg.select(prepared)
    asset = _extract(selection, prepared)
    conformance = classify(asset)

    skill: Optional[NormalizedSkill] = None
    mapped: Dict[str, str] = {}
    normalize_warnings: List[str] = []
    if conformance.level != LEVEL_L2:
        skill, mapped, normalize_warnings = normalize(prepared, asset)

    warnings = list(dict.fromkeys(list(asset.warnings or []) + normalize_warnings))
    archive_id = ""
    rewrite_errors: List[str] = []
    rewritten = False
    requires_rewrite = conformance.level == LEVEL_L2
    recommendation = recommend(conformance)
    actions = _actions(prepared, asset, conformance, skill, mapped)

    if conformance.level == LEVEL_L2 and rewrite:
        from . import rewrite as rewrite_mod
        client = llm_client if llm_client is not None else _default_llm_client()
        result = rewrite_mod.rewrite_asset(
            prepared, asset,
            source_ref=_source_ref(prepared),
            source_fingerprint=_source_fingerprint(prepared),
            llm_client=client,
            archive_dir=archive_dir,
        )
        archive_id = result.archive_id
        warnings = list(dict.fromkeys(warnings + list(result.warnings or [])))
        rewrite_errors = list(result.errors or [])
        if result.ok and result.skill is not None:
            skill = result.skill
            rewritten = True
            requires_rewrite = False
            recommendation = Recommendation(
                form=FORM_SKILL,
                reason="L2 来源已由 LLM 重写并通过 schema 校验；请人工预览草稿后确认。",
                confidence=0.5,
            )
            actions = _rewrite_actions(archive_id, sorted(skill.scripts))
        else:
            requires_rewrite = True
            warnings.append("LLM 重写未通过校验：保持 L2，commit 会被拒绝（原文已存档）")
            actions = [
                "LLM 重写未通过 schema 校验，需修正后重新 preview(rewrite=true)",
                "原文已存档：" + archive_id,
                "不对未通过校验的草稿执行安装",
            ]
    elif conformance.level == LEVEL_L2:
        warnings.append("尚未触发 LLM 重写：先 preview(rewrite=true) 生成草稿并人工预览")

    if conformance.level != LEVEL_L2 and rewrite:
        warnings.append("来源已是 L0/L1，忽略 rewrite=true（不调用 LLM）")

    dedupe_items: List[Dict[str, Any]] = []
    merge_suggestion: Optional[Dict[str, Any]] = None
    if dedupe:
        use_name = skill.name if skill else prepared.display_name
        use_desc = skill.description if skill else first_paragraph(asset.body, 500)
        use_content = skill.body if skill else str(asset.body or "")
        use_id = skill.id if skill else (slugify(prepared.display_name) or "")
        dedupe_items, merge_suggestion, dedupe_warnings = _analyze_duplicates(
            skill_id=use_id, name=use_name, description=use_desc,
            content=use_content, skills_service=skills_service)
        warnings = list(dict.fromkeys(warnings + dedupe_warnings))

    fingerprint = {
        "source": {"identity": prepared.identity(), "fingerprint": _source_fingerprint(prepared)},
        "adapter": selection.name,
        "extraction": _extraction_digest(asset),
        "conformance": conformance.level,
    }
    if archive_id:
        plan_id = _rewritten_plan_id(prepared, archive_id)
    else:
        plan_id = plan_fingerprint(fingerprint)
    plan = IngestPlan(
        plan_id=plan_id,
        source=prepared.to_public(),
        source_ref=_source_ref(prepared),
        adapter=selection.name,
        adapter_scores=dict(selection.scores),
        conformance=conformance,
        recommendation=recommendation,
        skill=skill,
        actions=actions,
        warnings=warnings,
        requires_rewrite=requires_rewrite,
        requires_confirm=True,
        rewritten=rewritten,
        archive_id=archive_id,
        rewrite_errors=rewrite_errors,
        dedupe=dedupe_items,
        merge_suggestion=merge_suggestion,
        raw=_raw_summary(asset),
    )
    logger.info("[ingestion] preview plan=%s adapter=%s level=%s rewritten=%s id=%s",
                plan.plan_id, plan.adapter, conformance.level, plan.rewritten,
                skill.id if skill else "-")
    return plan


def _default_installer() -> Any:
    """生产安装器：复用 `agent.state_manager` 的单例（函数体内导入，保持叶子包）。"""
    from agent.state_manager import get_skills_mgmt_service
    return get_skills_mgmt_service()


def _build_zip(skill: NormalizedSkill, tmp_dir: Path) -> Path:
    """把归一化技能拼成 install_from_zip 认识的包（不手拼 skill.md，复用序列化器）。"""
    from agent.skills_mgmt.file_store import SkillMDParser

    skill_dir = tmp_dir / skill.id
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest = skill.to_manifest()
    # 【为什么用 write_bytes 而不是 write_text】Windows 的 write_text 会把 \n
    # 再翻译成 \r\n；来源正文是按字节读入的（可能已含 \r\n），二次翻译会变成
    # \r\r\n，zip 里的脚本就不再与来源逐字节一致。写入一律用 UTF-8 字节。
    (skill_dir / "manifest.json").write_bytes(
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    skill_md = SkillMDParser.serialize(skill.to_meta(), skill.body or skill.description)
    (skill_dir / "skill.md").write_bytes(skill_md.encode("utf-8"))
    if skill.scripts:
        scripts_dir = skill_dir / "scripts"
        scripts_dir.mkdir()
        for name, text in skill.scripts.items():
            safe_name = Path(name).name
            if not safe_name or safe_name.startswith("."):
                continue
            (scripts_dir / safe_name).write_bytes(str(text).encode("utf-8"))
    zip_path = tmp_dir / (skill.id + ".zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(skill_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(skill_dir).as_posix())
    return zip_path


def _audit(payload: Dict[str, Any]) -> None:
    """审计链留痕（best-effort；失败不影响安装结果）。"""
    try:
        from agent.audit.facade import audit
        audit.record("ingestion.commit", actor="agent.ingestion",
                     subject=str(payload.get("skill_id") or ""),
                     payload=payload, source="agent")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[ingestion] 审计留痕失败(advisory): %s", exc)


def _fingerprint_key(fingerprint: Any) -> str:
    return json.dumps(fingerprint, ensure_ascii=False, sort_keys=True, default=str)


def _safe_delete(svc: Any, skill_id: str) -> None:
    deleter = getattr(svc, "delete", None)
    if callable(deleter):
        try:
            deleter(skill_id)
        except Exception:  # noqa: BLE001 回滚失败不掩盖原始合并错误
            pass


def _finalize_import(svc: Any, skill: NormalizedSkill, skill_id: str) -> None:
    """安装后衔接既有技能域：advisory 评审 / 自动归类 / 原生重叠吸收标记。

    `install_from_zip` 是"落文件"的底层通道，不会跑 `install()` 的评审钩子；
    P3 的队列/吸收需要这些标记，故在此补跑（全部 advisory，失败不阻断安装）。
    """
    for name in ("_advisory_assess", "_auto_classify"):
        fn = getattr(svc, name, None)
        if callable(fn):
            try:
                fn(skill_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ingestion] %s(%s) 失败(advisory): %s", name, skill_id, exc)
    overlap = _native_overlap_text(skill)
    if overlap:
        fn = getattr(svc, "absorb_overlap", None)
        if callable(fn):
            try:
                fn(skill_id, overlap=overlap, source="ingestion")
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ingestion] absorb_overlap(%s) 失败(advisory): %s", skill_id, exc)


def _install_plan(plan: IngestPlan, *, installer: Any = None,
                  rewritten: bool = False, archive_id: str = "",
                  merge_into: str = "") -> IngestResult:
    """把计划里的 NormalizedSkill 拼包并复用 install_from_zip。

    P3：`merge_into` 非空时，先安装得到来源 skill_id，再复用
    `SkillsMgmtService.merge_duplicate_skills(src, target)`；失败会回滚新装技能。
    否则安装后进草稿队列（`import_queue` 已识别 `ingestion:` 来源）。
    """
    skill = plan.skill
    if skill is None:
        raise RewriteRequired("计划中没有可安装的 skill（需要先重写）")
    svc = installer if installer is not None else _default_installer()
    merge_into = str(merge_into or "").strip()
    if merge_into:
        if not is_valid_skill_id(merge_into):
            raise MergeFailed("merge_into 不是合法技能 id", detail=repr(merge_into))
        if merge_into == skill.id:
            raise MergeFailed(
                "merge_into 不能等于来源生成的技能 id",
                detail="安装会覆盖目标技能；请换一个目标 id，或去掉 merge_into 走草稿队列。",
            )
        getter = getattr(svc, "get", None)
        if callable(getter):
            try:
                target = getter(merge_into)
            except Exception as exc:  # noqa: BLE001
                raise MergeFailed(
                    f"merge_into 目标不存在：{merge_into}",
                    detail=f"{type(exc).__name__}: {exc}",
                ) from exc
            if target is None:
                raise MergeFailed(f"merge_into 目标不存在：{merge_into}")

    with tempfile.TemporaryDirectory(prefix="yunshu_ingest_") as tmp:
        zip_path = _build_zip(skill, Path(tmp))
        try:
            raw = svc.install_from_zip(str(zip_path))
        except Exception as exc:  # noqa: BLE001 安装链路失败要带原因
            logger.exception("[ingestion] install failed plan=%s", plan.plan_id)
            raise InstallFailed(
                "技能安装失败",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc

    if not isinstance(raw, dict):
        raw = {}
    skill_id = str(raw.get("skill_id") or skill.id)

    if merge_into:
        merger = getattr(svc, "merge_duplicate_skills", None)
        if not callable(merger):
            _safe_delete(svc, skill_id)
            raise MergeFailed("安装器不支持合并（缺少 merge_duplicate_skills）")
        try:
            merged = merger(skill_id, merge_into, strategy="auto")
        except Exception as exc:  # noqa: BLE001 合并失败回滚新装技能
            logger.exception("[ingestion] merge failed src=%s dst=%s", skill_id, merge_into)
            _safe_delete(svc, skill_id)
            raise MergeFailed(
                f"合并进 {merge_into} 失败",
                detail=f"{type(exc).__name__}: {exc}",
            ) from exc
        merged = merged if isinstance(merged, dict) else {}
        merged_id = str(merged.get("merged_id") or merge_into)
        result = IngestResult(
            plan_id=plan.plan_id,
            skill_id=merged_id,
            name=str(merged.get("merged_name") or merge_into),
            version=str(merged.get("version") or skill.version),
            scripts_count=int(raw.get("scripts_count") or 0),
            adapter=plan.adapter,
            conformance=plan.conformance.level,
            warnings=list(plan.warnings) + [f"已合并进 {merge_into}（来源 {skill_id} 已删除）"],
            installed=True,
            rewritten=bool(rewritten or plan.rewritten),
            archive_id=str(archive_id or plan.archive_id),
            merged=True,
            merged_into=merge_into,
            removed_id=skill_id,
            queued=False,
        )
        _audit({
            "plan_id": result.plan_id,
            "skill_id": result.skill_id,
            "adapter": result.adapter,
            "conformance": result.conformance,
            "source_ref": plan.source_ref,
            "scripts_count": result.scripts_count,
            "rewritten": result.rewritten,
            "archive_id": result.archive_id,
            "merged": True,
            "merged_into": merge_into,
            "removed_id": skill_id,
            "queued": False,
        })
        logger.info("[ingestion] merge plan=%s src=%s dst=%s",
                    plan.plan_id, skill_id, merge_into)
        return result

    _finalize_import(svc, skill, skill_id)
    result = IngestResult(
        plan_id=plan.plan_id,
        skill_id=skill_id,
        name=str(raw.get("name") or skill.name),
        version=str(raw.get("version") or skill.version),
        scripts_count=int(raw.get("scripts_count") or len(skill.scripts)),
        adapter=plan.adapter,
        conformance=plan.conformance.level,
        warnings=list(plan.warnings),
        installed=True,
        rewritten=bool(rewritten or plan.rewritten),
        archive_id=str(archive_id or plan.archive_id),
        queued=True,
    )
    _audit({
        "plan_id": result.plan_id,
        "skill_id": result.skill_id,
        "adapter": result.adapter,
        "conformance": result.conformance,
        "source_ref": plan.source_ref,
        "scripts_count": result.scripts_count,
        "rewritten": result.rewritten,
        "archive_id": result.archive_id,
        "merged": False,
        "queued": True,
    })
    logger.info("[ingestion] commit plan=%s skill=%s adapter=%s rewritten=%s queued=%s",
                result.plan_id, result.skill_id, result.adapter,
                result.rewritten, result.queued)
    return result


def _plan_from_archive(prepared: IngestSource, archive: Dict[str, Any],
                       archive_id: str, skill: NormalizedSkill) -> IngestPlan:
    validation = archive.get("validation") or {}
    model = archive.get("model") or {}
    return IngestPlan(
        plan_id=_rewritten_plan_id(prepared, archive_id),
        source=prepared.to_public(),
        source_ref=_source_ref(prepared),
        adapter=str(archive.get("adapter") or "rewrite"),
        adapter_scores={},
        conformance=Conformance(LEVEL_L2, reasons=("LLM 重写存档",)),
        recommendation=Recommendation(
            form=FORM_SKILL,
            reason="从已通过 schema 校验的重写存档安装（不再调用 LLM）。",
            confidence=0.5,
        ),
        skill=skill,
        actions=[
            "从重写存档安装：" + archive_id,
            "模型：" + str(model.get("name") or ""),
            "原文/重写结果存档：" + archive_id,
        ],
        warnings=list(validation.get("warnings") or []),
        requires_rewrite=False,
        requires_confirm=True,
        rewritten=True,
        archive_id=archive_id,
        raw={"archive_id": archive_id, "model": model.get("name", "")},
    )


def commit(source: Any, *, plan_id: str, confirm: bool = False,
           registry: Optional[registry_mod.AdapterRegistry] = None,
           installer: Any = None, archive_id: str = "", rewrite: bool = False,
           llm_client: Any = None, archive_dir: Optional[Any] = None,
           merge_into: str = "") -> IngestResult:
    """按**已预览的计划**落库（复用 install_from_zip）。

    P2：L2 来源必须先用 preview(rewrite=true) 生成存档，再带 archive_id 提交；
    commit 只读存档，不重新调 LLM，保证"预览的就是安装的"。

    Raises:
        ConfirmationRequired: 未显式 confirm=True
        PlanStale: 来源变化 / 存档缺失 / plan_id 不匹配
        RewriteRequired: L2 未重写或重写未通过 schema 校验
        MergeFailed: merge_into 目标不存在/合并失败（新装技能会回滚）
        InstallFailed: 既有安装链路失败
    """
    if confirm is not True:
        raise ConfirmationRequired(
            "commit 需要显式 confirm=true（必须先看预览计划）",
            detail="未确认的请求不会写任何文件；请先调用 preview 并把 plan_id 原样带回。",
        )
    if not str(plan_id or "").strip():
        raise InvalidSource("commit 需要 plan_id（先调用 preview）")
    plan_id = str(plan_id).strip()

    if archive_id:
        from . import rewrite as rewrite_mod
        aid = str(archive_id).strip()
        archive = rewrite_mod.load_archive(aid, archive_dir=archive_dir)
        if archive is None:
            raise PlanStale(
                "重写存档不存在或已失效",
                detail="请重新 preview(rewrite=true) 生成新的 archive_id；旧存档不会复用。",
            )
        prepared = _prepare_source(source)
        archived_fp = (archive.get("source") or {}).get("fingerprint")
        if _fingerprint_key(archived_fp) != _fingerprint_key(_source_fingerprint(prepared)):
            raise PlanStale(
                "来源在重写后已变化，重写存档不再对应当前内容",
                detail="请重新 preview(rewrite=true)；旧 archive_id 不会被安装。",
            )
        expected = _rewritten_plan_id(prepared, aid)
        if expected != plan_id:
            raise PlanStale(
                "plan_id 与重写存档不匹配",
                detail=f"期望 plan_id={expected}，收到 {plan_id}；请重新 preview(rewrite=true)。",
            )
        validation = archive.get("validation") or {}
        if not validation.get("ok"):
            raise RewriteRequired(
                "重写结果未通过 schema 校验，不能安装",
                detail="；".join(validation.get("errors") or []) or "请重新 preview(rewrite=true)",
            )
        skill = rewrite_mod.skill_from_archive(archive)
        if skill is None:
            raise RewriteRequired(
                "重写存档里没有可安装的 skill",
                detail="请重新 preview(rewrite=true) 生成通过校验的草稿。",
            )
        plan = _plan_from_archive(prepared, archive, aid, skill)
        return _install_plan(plan, installer=installer, rewritten=True,
                             archive_id=aid, merge_into=merge_into)

    if rewrite:
        raise InvalidSource("commit 的 rewrite=true 必须同时带 archive_id（先 preview(rewrite=true)）")

    plan = preview(source, registry=registry)
    if plan.plan_id != plan_id:
        raise PlanStale(
            "来源在预览后已变化，计划已失效",
            detail=f"预览 plan_id={plan_id}，当前 plan_id={plan.plan_id}；请重新 preview。",
        )
    if plan.requires_rewrite or plan.skill is None:
        raise RewriteRequired(
            "该来源不符合云枢 schema（L2），需要先 LLM 重写并人工预览",
            detail="请 preview(rewrite=true) 生成草稿与 archive_id，再带 archive_id 提交。",
        )
    return _install_plan(plan, installer=installer, merge_into=merge_into)


__all__ = [
    "MAX_SOURCE_TEXT_CHARS",
    "PLAN_BODY_LIMIT",
    "classify",
    "commit",
    "normalize",
    "preview",
    "recommend",
]
