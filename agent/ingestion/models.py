"""吞噬（ingestion）域模型（P0）

「吞噬 → 消化 → 吸收」中的**入口资产**模型。本包只回答三件事：

1. **来源是什么**（`IngestSource`）：本机目录 / 单文件 / 一段原文；
2. **与云枢有多符合**（`Conformance`）：L0 符合 / L1 部分符合 / L2 不符合；
3. **应该吸收成什么**（`Recommendation`）：默认 markdown 技能；工作流 / 代码工具
   只在 P4 证据充分且用户显式选择后才会生成。

设计边界（与 `agent/digestion` 的 import 纪律同源）：

- 本模块只依赖标准库，**不读文件、不联网、不写库**；
- 所有字段都可 JSON 序列化（`to_dict()`），供 HTTP 层/前端直接消费；
- L2 的"原文 → 云枢 skill.md"由 P2 的 `rewrite` 层负责，本模块只标记
  `requires_rewrite`，**绝不在这里伪造能力描述**。

【为什么 plan_id 不放在本模块算】plan_id 需要来源指纹（文件 mtime/内容摘要）与
适配器抽取结果，属于 service 的职责；模型只承载已确定的字段，避免"同一份数据
在模型里又算一遍"的双事实源。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  常量（单点定义，供适配器 / 服务 / HTTP 层 / 测试共同引用）
# ════════════════════════════════════════════════════════════

#: 三级判定：L0 直接落库 / L1 适配器改造 / L2 LLM 重写
LEVEL_L0 = "L0"
LEVEL_L1 = "L1"
LEVEL_L2 = "L2"
LEVELS: Tuple[str, ...] = (LEVEL_L0, LEVEL_L1, LEVEL_L2)
LEVEL_LABELS: Dict[str, str] = {
    LEVEL_L0: "符合（直接安装）",
    LEVEL_L1: "部分符合（适配器改造）",
    LEVEL_L2: "不符合（需 LLM 重写）",
}

#: 吸收形态（P0 默认 skill；workflow/tool 由 P4 证据与用户显式选择决定）
FORM_SKILL = "skill"
FORM_WORKFLOW = "workflow"
FORM_TOOL = "tool"
FORMS: Tuple[str, ...] = (FORM_SKILL, FORM_WORKFLOW, FORM_TOOL)
FORM_LABELS: Dict[str, str] = {
    FORM_SKILL: "markdown 技能",
    FORM_WORKFLOW: "工作流",
    FORM_TOOL: "代码工具",
}

#: 来源类型
SOURCE_KIND_PATH = "path"
SOURCE_KIND_TEXT = "text"


# ════════════════════════════════════════════════════════════
#  错误（HTTP 层只做映射，不重新判定）
# ════════════════════════════════════════════════════════════


class IngestionError(Exception):
    """吞噬域错误基类。

    `status` / `code` / `title` 是**给 HTTP 层**的契约：视图捕获本类后直接
    交给 `agent.api_envelope.problem()`，不在这里 import flask（域模块保持纯）。
    """

    status: int = 500
    code: str = "ingestion-error"
    title: str = "吞噬失败"

    def __init__(self, message: str, *, detail: str = "",
                 status: Optional[int] = None, code: Optional[str] = None,
                 title: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = str(message)
        self.detail = str(detail or message)
        if status is not None:
            self.status = int(status)
        if code is not None:
            self.code = str(code)
        if title is not None:
            self.title = str(title)


class InvalidSource(IngestionError):
    status = 400
    code = "invalid-source"
    title = "来源参数无效"


class SourceNotFound(IngestionError):
    status = 404
    code = "source-not-found"
    title = "来源不存在"


class SourceTooLarge(IngestionError):
    status = 413
    code = "source-too-large"
    title = "来源过大"


class UnsupportedSource(IngestionError):
    status = 422
    code = "unsupported-source"
    title = "无法识别的来源"

    def __init__(self, message: str, *, detail: str = "",
                 scores: Optional[Dict[str, int]] = None) -> None:
        super().__init__(message, detail=detail)
        self.scores: Dict[str, int] = dict(scores or {})


class ConfirmationRequired(IngestionError):
    status = 400
    code = "confirmation-required"
    title = "需要显式确认"


class PlanStale(IngestionError):
    status = 409
    code = "plan-stale"
    title = "预览计划已失效"


class RewriteRequired(IngestionError):
    status = 409
    code = "rewrite-required"
    title = "需要 LLM 重写"


class InstallFailed(IngestionError):
    status = 500
    code = "install-failed"
    title = "技能安装失败"


# ════════════════════════════════════════════════════════════
#  来源
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class IngestSource:
    """一次吞噬的输入。

    - `kind="path"`：本机目录或单文件（`path` 必填）；
    - `kind="text"`：一段原文（`text` 必填；P0 仅用于预览，L2 提交会被拒绝）。

    文本来源在 `to_public()` 里**不回显原文**，只给长度与摘要，避免把用户粘贴的
    长文/密钥原样带进日志或响应缓存。
    """

    kind: str = SOURCE_KIND_PATH
    path: str = ""
    text: str = ""
    label: str = ""
    #: 可选的生态提示（claude/openclaw/trae/...）。默认空 = 由路径推断；
    #: 自定义扫描根（INGEST_SCAN_ROOTS 的 label=path）下由前端把 label 带回来。
    ecosystem: str = ""

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip().lower()
        if not kind:
            kind = SOURCE_KIND_PATH if self.path else SOURCE_KIND_TEXT
        if kind not in (SOURCE_KIND_PATH, SOURCE_KIND_TEXT):
            raise ValueError(f"未知来源类型: {self.kind!r}")
        object.__setattr__(self, "kind", kind)
        if kind == SOURCE_KIND_PATH:
            if not str(self.path or "").strip():
                raise ValueError("path 来源必须提供 path")
            object.__setattr__(self, "path", str(self.path).strip())
        else:
            if not str(self.text or "").strip():
                raise ValueError("text 来源必须提供非空 text")
            object.__setattr__(self, "text", str(self.text))
        object.__setattr__(self, "label", str(self.label or "").strip())
        object.__setattr__(self, "ecosystem", str(self.ecosystem or "").strip().lower())

    @classmethod
    def from_mapping(cls, raw: Any) -> "IngestSource":
        """把 HTTP 请求里的 `source` 字段归一成 IngestSource。

        接受的形状（互斥）：
          - `"C:/path/to/skill"`
          - `{"path": "...", "label": "..."}`
          - `{"text": "...", "label": "..."}`
        """
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, (str, os.PathLike)):
            return cls(kind=SOURCE_KIND_PATH, path=os.fspath(raw))
        if not isinstance(raw, dict):
            raise InvalidSource("source 必须是路径字符串或 {path|text, label, ecosystem} 对象")
        ecosystem = str(raw.get("ecosystem") or raw.get("ecosystem_hint") or "")
        path = raw.get("path")
        text = raw.get("text")
        if path and text:
            raise InvalidSource("source.path 与 source.text 不能同时提供")
        if path:
            return cls(kind=SOURCE_KIND_PATH, path=str(path),
                       label=str(raw.get("label") or ""), ecosystem=ecosystem)
        if text is not None:
            return cls(kind=SOURCE_KIND_TEXT, text=str(text),
                       label=str(raw.get("label") or ""), ecosystem=ecosystem)
        raise InvalidSource("source 必须包含 path 或 text")

    @property
    def display_name(self) -> str:
        if self.label:
            return self.label
        if self.kind == SOURCE_KIND_PATH:
            import os
            return os.path.basename(self.path.rstrip("\\/")) or self.path
        return "粘贴的原文"

    def text_digest(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", errors="replace")).hexdigest()

    def to_public(self) -> Dict[str, Any]:
        common = {"ecosystem": self.ecosystem} if self.ecosystem else {}
        if self.kind == SOURCE_KIND_PATH:
            return {"kind": self.kind, "path": self.path,
                    "label": self.display_name, **common}
        return {
            "kind": self.kind,
            "label": self.display_name,
            "chars": len(self.text),
            "sha256": self.text_digest()[:16],
            **common,
        }

    def identity(self) -> Dict[str, Any]:
        """来源身份（不含内容指纹；内容指纹由 service 叠加）。"""
        if self.kind == SOURCE_KIND_PATH:
            return {"kind": self.kind, "path": self.path, "ecosystem": self.ecosystem}
        return {"kind": self.kind, "sha256": self.text_digest(), "ecosystem": self.ecosystem}


# ════════════════════════════════════════════════════════════
#  判定 / 建议 / 归一化产物
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Conformance:
    """三级判定结果。

    `reasons` 说明**为什么**是这个级别；`missing` 列出 L1 需要适配器补齐的
    规范字段（id/name/description/content/manifest 等），前端据此展示"将改什么"。
    """

    level: str
    reasons: Tuple[str, ...] = ()
    missing: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.level not in LEVELS:
            raise ValueError(f"未知符合级别: {self.level!r}")

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def is_direct(self) -> bool:
        return self.level == LEVEL_L0

    @property
    def needs_rewrite(self) -> bool:
        return self.level == LEVEL_L2

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "label": self.label,
            "reasons": list(self.reasons),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class Recommendation:
    """吸收形态建议（P0 恒为 skill；P4 用证据升级）。"""

    form: str = FORM_SKILL
    reason: str = ""
    confidence: float = 0.5
    estimated_tokens_per_month: Optional[int] = None

    def __post_init__(self) -> None:
        if self.form not in FORMS:
            raise ValueError(f"未知吸收形态: {self.form!r}")

    @property
    def label(self) -> str:
        return FORM_LABELS[self.form]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "form": self.form,
            "label": self.label,
            "reason": self.reason,
            "confidence": round(float(self.confidence), 3),
            "estimated_tokens_per_month": self.estimated_tokens_per_month,
        }


@dataclass
class NormalizedSkill:
    """适配器归一化后的云枢技能（尚未落库）。

    `scripts` 只保存脚本**内容**（供 commit 写入 zip），计划响应里只暴露文件名。
    `extra` 保存来源侧保留字段（如 openclaw/trae 的原始键），P2 的原文存档会用到。
    """

    id: str
    name: str
    description: str = ""
    body: str = ""
    version: str = "1.0.0"
    author: str = ""
    category: str = "ingested"
    source: str = ""
    tags: List[str] = field(default_factory=list)
    content_type: str = "markdown"
    scripts: Dict[str, str] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def script_names(self) -> List[str]:
        return sorted(self.scripts)

    def to_manifest(self) -> Dict[str, Any]:
        """install_from_zip 的 manifest.json（字段取 skill_manager 白名单的子集）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "source": self.source,
            "tags": list(self.tags),
            "content_type": self.content_type,
        }

    def to_meta(self) -> Dict[str, Any]:
        """skill.md front matter（含 id；供 SkillMDParser.serialize）。"""
        return self.to_manifest()

    def body_preview(self, limit: int = 4000) -> Dict[str, Any]:
        text = str(self.body or "")
        return {
            "chars": len(text),
            "truncated": len(text) > limit,
            "text": text[:limit],
        }

    def to_dict(self, *, body_limit: int = 4000) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "author": self.author,
            "category": self.category,
            "source": self.source,
            "tags": list(self.tags),
            "content_type": self.content_type,
            "scripts": self.script_names,
            "body_preview": self.body_preview(body_limit),
            "extra_keys": sorted(self.extra),
        }


@dataclass
class IngestPlan:
    """可预览的吞噬计划（dry-run；用户确认后才 commit）。"""

    plan_id: str
    source: Dict[str, Any]
    source_ref: str
    adapter: str
    adapter_scores: Dict[str, int] = field(default_factory=dict)
    conformance: Conformance = field(default_factory=lambda: Conformance(LEVEL_L2))
    recommendation: Recommendation = field(default_factory=Recommendation)
    skill: Optional[NormalizedSkill] = None
    actions: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    requires_rewrite: bool = False
    requires_confirm: bool = True
    dedupe: List[Dict[str, Any]] = field(default_factory=list)
    merge_suggestion: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self, *, body_limit: int = 4000) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "source": dict(self.source),
            "source_ref": self.source_ref,
            "adapter": self.adapter,
            "adapter_scores": dict(self.adapter_scores),
            "conformance": self.conformance.to_dict(),
            "recommendation": self.recommendation.to_dict(),
            "skill": self.skill.to_dict(body_limit=body_limit) if self.skill else None,
            "actions": list(self.actions),
            "warnings": list(self.warnings),
            "requires_rewrite": bool(self.requires_rewrite),
            "requires_confirm": bool(self.requires_confirm),
            "dedupe": list(self.dedupe),
            "merge_suggestion": dict(self.merge_suggestion) if self.merge_suggestion else None,
            "raw": dict(self.raw),
        }


@dataclass
class IngestResult:
    """commit 结果（P0 只做 install；P3 会补 merge/skip）。"""

    plan_id: str
    skill_id: str
    name: str
    version: str
    scripts_count: int
    adapter: str
    conformance: str
    warnings: List[str] = field(default_factory=list)
    installed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "skill_id": self.skill_id,
            "name": self.name,
            "version": self.version,
            "scripts_count": self.scripts_count,
            "adapter": self.adapter,
            "conformance": self.conformance,
            "warnings": list(self.warnings),
            "installed": bool(self.installed),
        }


def plan_fingerprint(payload: Dict[str, Any]) -> str:
    """计划指纹（确定性；commit 用同一函数重算，不依赖内存状态）。"""
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return "ig_" + hashlib.sha256(raw).hexdigest()[:24]
