"""过程蒸馏数据模型 — 蒸馏中间产物与固化参数。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


# ═══════════════════════════════════════════════════════════════
#  蒸馏步骤
# ═══════════════════════════════════════════════════════════════

@dataclass
class DistilledStep:
    """一条可复现步骤。

    tool 可空（纯指令步骤，固化为 skill 正文）；tool 非空时
    固化为 workflow 的 WorkflowStep（须是云枢已注册工具名）。
    """
    seq: int                       # 步骤序号（从 1 起）
    action: str                    # 动作描述（人类可读）
    tool: str = ""                 # 云枢工具名（空 = 纯指令）
    params: Dict[str, Any] = field(default_factory=dict)  # 工具参数模板
    condition: str = ""            # 执行条件（简化表达式，可空）
    note: str = ""                 # 补充说明 / 边界
    source: str = ""               # 来源素材标识（溯源）
    confidence: float = 1.0        # 子代理置信度（0-1）

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "seq": self.seq,
            "action": self.action,
            "source": self.source,
        }
        if self.tool:
            d["tool"] = self.tool
            d["params"] = self.params
        if self.condition:
            d["condition"] = self.condition
        if self.note:
            d["note"] = self.note
        if self.confidence < 1.0:
            d["confidence"] = round(self.confidence, 3)
        return d


@dataclass
class DistilledProcess:
    """蒸馏产物 — 一份可复现的步骤序列（合并归一后）。"""
    name: str                        # 显示名
    description: str = ""
    task_signature: str = ""         # 匹配签名（关键词 | 拼接）
    trigger_patterns: List[str] = field(default_factory=list)
    steps: List[DistilledStep] = field(default_factory=list)
    expected_output: str = ""
    sources: List[str] = field(default_factory=list)   # 素材标识
    method: str = "llm"              # llm | rule（降级）
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "task_signature": self.task_signature,
            "trigger_patterns": self.trigger_patterns,
            "steps": [s.to_dict() for s in self.steps],
            "expected_output": self.expected_output,
            "sources": self.sources,
            "method": self.method,
            "tags": self.tags,
        }


# ═══════════════════════════════════════════════════════════════
#  素材
# ═══════════════════════════════════════════════════════════════

@dataclass
class DistillMaterial:
    """蒸馏输入素材 — 一段可独立派给子代理的文本。"""
    id: str                        # 素材标识（slug / 相对路径）
    title: str
    content: str
    source_ref: str = ""           # 溯源（wiki/xxx.md 或文件路径）
    kind: str = "markdown"         # markdown | text
    description: str = ""          # front matter description（供降级/描述复用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content[:500],
            "source_ref": self.source_ref,
            "kind": self.kind,
            "description": self.description[:200],
        }


# ═══════════════════════════════════════════════════════════════
#  工具
# ═══════════════════════════════════════════════════════════════

_SLUG_SAFE_RE = re.compile(r"[^a-z0-9_\-]")


def slugify(name: str, max_len: int = 80) -> str:
    """名称 → 合法 id（小写、kebab、可截断；与 workflow id 校验一致）。"""
    s = _SLUG_SAFE_RE.sub("-", (name or "").lower().strip())
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:max_len].strip("-") or "distilled-process"
