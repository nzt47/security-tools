"""P4 代码工具/工作流骨架草稿（不自动启用）

用途：当用户显式选择 form=tool / form=workflow 时，把吞噬得到的技能
落成本地可审阅的 PR 草稿目录：

    data/ingestion/drafts/<draft_id>/
        manifest.json
        README.md
        agent/tools/<name>.py             # tool 骨架
        data/tool_definitions/<name>.yaml # tool 声明
        tests/unit/test_<name>.py
        workflows/<id>.json               # workflow 骨架

**边界**：只写草稿目录，绝不写真实的 agent/tools / data/tool_definitions /
tests/，也不注册/启用任何工具；所有产物 status=draft、enabled=false。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .adapters.base import slugify
from .models import IngestPlan, NormalizedSkill

DEFAULT_DRAFT_DIR = Path(__file__).resolve().parents[2] / "data" / "ingestion" / "drafts"
_STEP_RE = re.compile(r"^(?:\d+[.、)]|[-*])\s+(.+)$")
_PY_KEYWORDS = {
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().replace(microsecond=0).isoformat()


def draft_id_for(plan_id: str, form: str) -> str:
    return "ing_" + hashlib.sha256(f"{plan_id}|{form}".encode("utf-8")).hexdigest()[:16]


def _tool_name(skill: NormalizedSkill) -> str:
    base = (slugify(skill.id) or "ingested_tool").replace("-", "_")
    if base in _PY_KEYWORDS or not base[0].isalpha():
        base = "t_" + base
    return base


def _extract_steps(body: str) -> List[str]:
    lines = str(body or "").splitlines()
    steps: List[str] = []
    in_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped.lstrip("#").strip().lower()
            in_section = title in ("步骤", "steps", "执行步骤", "workflow")
            continue
        if in_section:
            match = _STEP_RE.match(stripped)
            if match:
                steps.append(match.group(1).strip())
    if not steps:
        for line in lines:
            match = re.match(r"^\s*\d+[.、)]\s+(.+)$", line)
            if match:
                steps.append(match.group(1).strip())
    return steps[:20]


def _tool_schema(skill: NormalizedSkill) -> Dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "input": {"type": "string", "description": "工具输入；具体参数待人工补全"},
        },
        "required": [],
        "additionalProperties": True,
        "description": skill.description or skill.name,
    }


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def _manifest(draft_id: str, form: str, plan: IngestPlan, skill: NormalizedSkill,
              files: List[str], warnings: List[str]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "draft_id": draft_id,
        "form": form,
        "status": "draft",
        "enabled": False,
        "plan_id": plan.plan_id,
        "source_ref": plan.source_ref,
        "adapter": plan.adapter,
        "skill_id": skill.id,
        "skill_name": skill.name,
        "created_at": _now_iso(),
        "files": files,
        "warnings": warnings,
        "review": [
            "人工补全实现/步骤绑定与参数 schema",
            "补测试并跑通",
            "按需登记到 tool_definitions / workflow 加载目录",
            "走审批后再启用（P4 不自动注册）",
        ],
    }


def _readme(draft_id: str, form: str, plan: IngestPlan, skill: NormalizedSkill,
            warnings: List[str]) -> str:
    lines = [
        "# 吞噬草稿 " + draft_id,
        "",
        "- 形态：" + form,
        "- 来源：" + plan.source_ref,
        "- 技能 id：" + skill.id,
        "- 计划 id：" + plan.plan_id,
        "",
        "## 说明",
        "",
        skill.description or "（来源未提供说明）",
        "",
        "## 审阅步骤",
        "",
        "1. 补全实现/步骤绑定与参数 schema。",
        "2. 补测试并跑通。",
        "3. 按需登记到真实加载目录（本目录不会被自动加载）。",
        "4. 走审批后再启用。",
        "",
    ]
    if warnings:
        lines.extend(["## 警告", ""] + ["- " + w for w in warnings] + [""])
    return "\n".join(lines)


def write_tool_draft(skill: NormalizedSkill, plan: IngestPlan, *,
                     draft_dir: Any = None) -> Dict[str, Any]:
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    did = draft_id_for(plan.plan_id, "tool")
    root = base / did
    name = _tool_name(skill)
    schema = _tool_schema(skill)
    description = skill.description or skill.name

    tool_py = '"""由吞噬 P4 生成的工具骨架（未启用）。\\n\\n' + description[:500].replace('"""', "'''") + '\\n"""\\n\\n' + \
        "from __future__ import annotations\\n\\n" + \
        "from typing import Any, Dict\\n\\n" + \
        f"TOOL_NAME = {name!r}\\n" + \
        "TOOL_SCHEMA: Dict[str, Any] = " + json.dumps(schema, ensure_ascii=False, indent=4) + "\\n\\n\\n" + \
        f"def {name}(**kwargs: Any) -> Dict[str, Any]:\\n" + \
        '    """' + description[:300].replace('"""', "'''") + '"""\\n' + \
        '    raise NotImplementedError("吞噬 P4 工具骨架：请人工实现、补测试并通过审批后启用")\\n'

    tool_py = tool_py.replace("\\n", "\n")

    tool_yaml = {
        "name": name,
        "category": "custom",
        "description": description,
        "deprecated": False,
        "version": "0.1.0",
        "location": "local",
        "plane": "act",
        "effect": "write",
        "risk": "medium",
        "tool_type": "tool",
        "llm_callable": True,
        "callable_mode": "auto",
        "permission_level": "restricted",
        "sandbox_allowed": False,
        "tags": ["ingested", "draft"],
        "status": "draft",
        "enabled": False,
        "schema": schema,
    }
    test_py = (
        "import pytest\\n"
        f"from agent.tools.{name} import {name}, TOOL_SCHEMA\\n\\n\\n"
        "def test_tool_schema_is_object():\\n"
        "    assert TOOL_SCHEMA.get('type') == 'object'\\n\\n\\n"
        "@pytest.mark.skip(reason='P4 skeleton: implement behavior before enabling')\\n"
        "def test_tool_behavior():\\n"
        f"    assert {name}() is not None\\n"
    )

    files = [
        "manifest.json",
        "README.md",
        f"agent/tools/{name}.py",
        f"data/tool_definitions/{name}.yaml",
        f"tests/unit/test_{name}.py",
    ]
    warnings = [
        "工具骨架未实现：调用会抛 NotImplementedError",
        "本目录不会被工具加载器扫描；启用前必须人工实现/测试/审批",
    ]
    _write(root / f"agent/tools/{name}.py", tool_py)
    _write(root / f"data/tool_definitions/{name}.yaml",
           yaml.safe_dump(tool_yaml, allow_unicode=True, sort_keys=False))
    _write(root / f"tests/unit/test_{name}.py", test_py)
    _write(root / "README.md", _readme(did, "tool", plan, skill, warnings))
    _write(root / "manifest.json",
           json.dumps(_manifest(did, "tool", plan, skill, files, warnings),
                      ensure_ascii=False, indent=2) + "\n")
    return {"draft_id": did, "draft_dir": str(root), "form": "tool",
            "files": files, "warnings": warnings}


def write_workflow_draft(skill: NormalizedSkill, plan: IngestPlan, *,
                         draft_dir: Any = None) -> Dict[str, Any]:
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    did = draft_id_for(plan.plan_id, "workflow")
    root = base / did
    steps = _extract_steps(skill.body)
    warnings: List[str] = []
    if len(steps) < 3:
        warnings.append(f"正文只提取到 {len(steps)} 个编号步骤（建议 ≥3）；需人工补全步骤")
    workflow = {
        "id": (slugify(skill.id) or did) + "-workflow",
        "name": skill.name,
        "description": skill.description or skill.name,
        "version": "0.1.0",
        "status": "draft",
        "enabled": False,
        "source": "ingestion",
        "source_ref": plan.source_ref,
        "plan_id": plan.plan_id,
        "requires_binding": True,
        "trigger_patterns": [skill.name],
        "steps": [
            {
                "step_id": f"step_{i}",
                "description": text,
                "tool_name": "",
                "params_template": {},
                "output_key": f"step_{i}_output",
                "need_llm": False,
                "timeout_ms": 30000,
            }
            for i, text in enumerate(steps, start=1)
        ],
    }
    warnings.append("工作流步骤的 tool_name/参数未绑定；本目录不会被自动加载")
    files = ["manifest.json", "README.md", f"workflows/{workflow['id']}.json"]
    _write(root / f"workflows/{workflow['id']}.json",
           json.dumps(workflow, ensure_ascii=False, indent=2) + "\n")
    _write(root / "README.md", _readme(did, "workflow", plan, skill, warnings))
    _write(root / "manifest.json",
           json.dumps(_manifest(did, "workflow", plan, skill, files, warnings),
                      ensure_ascii=False, indent=2) + "\n")
    return {"draft_id": did, "draft_dir": str(root), "form": "workflow",
            "files": files, "warnings": warnings,
            "workflow": workflow}


def write_draft(skill: NormalizedSkill, plan: IngestPlan, form: str, *,
                draft_dir: Any = None) -> Dict[str, Any]:
    if form == "tool":
        return write_tool_draft(skill, plan, draft_dir=draft_dir)
    if form == "workflow":
        return write_workflow_draft(skill, plan, draft_dir=draft_dir)
    raise ValueError(f"未知草稿形态: {form!r}")


#: 公开别名（service.recommend 用来数步骤，避免复制解析口径）
extract_steps = _extract_steps


__all__ = [
    "DEFAULT_DRAFT_DIR",
    "draft_id_for",
    "extract_steps",
    "write_draft",
    "write_tool_draft",
    "write_workflow_draft",
]
