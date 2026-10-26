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
import shutil
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
    warnings.extend([
        f"草稿只是骨架：{len(steps)} 个步骤的 tool_name / params_template 都是空的"
        "（requires_binding=true），不能直接执行。",
        f"下一步：编辑 workflows/{workflow['id']}.json，给每步填 tool_name 与 params_template"
        "（可引用上一步的 output_key），补测试跑通后再登记到工作流加载目录。",
        "本目录（data/ingestion/drafts/）不会被任何加载器扫描：不登记就不会出现在技能/工作流列表里。",
    ])
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


def list_drafts(draft_dir: Any = None) -> List[Dict[str, Any]]:
    """列出隔离区里已生成的骨架草稿（只读 manifest，按 created_at 倒序）。

    为什么需要它：tool/workflow 草稿**不注册、不进技能队列**，用户生成后去「草稿评审」
    看不到任何东西（那里读的是 /api/skills-mgmt/queue），会误以为生成失败。
    这里把隔离区的真实内容摊开给界面，附带 warnings 与目录，避免"看不见 = 没生成"。
    """
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    if not base.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "manifest.json"
        if not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        bound = total = 0
        wf_path = _workflow_file(child)
        if wf_path is not None:
            try:
                wf = json.loads(wf_path.read_text(encoding="utf-8"))
                steps = wf.get("steps") or []
                total = len(steps)
                bound = sum(1 for s in steps if str(s.get("tool_name") or "").strip())
            except (OSError, ValueError):
                pass
        out.append({
            "draft_id": str(manifest.get("draft_id") or child.name),
            "bound_steps": bound,
            "total_steps": total,
            "form": str(manifest.get("form") or ""),
            "status": str(manifest.get("status") or "draft"),
            "enabled": bool(manifest.get("enabled")),
            "skill_id": str(manifest.get("skill_id") or ""),
            "skill_name": str(manifest.get("skill_name") or ""),
            "plan_id": str(manifest.get("plan_id") or ""),
            "source_ref": str(manifest.get("source_ref") or ""),
            "created_at": str(manifest.get("created_at") or ""),
            "warnings": [str(w) for w in (manifest.get("warnings") or []) if str(w).strip()],
            "files": [str(f) for f in (manifest.get("files") or [])],
            "draft_dir": str(child),
        })
    out.sort(key=lambda d: d.get("created_at") or "", reverse=True)
    return out


def _workflow_file(root: Path) -> Any:
    wf_dir = root / "workflows"
    if not wf_dir.is_dir():
        return None
    files = sorted(wf_dir.glob("*.json"))
    return files[0] if files else None


def _find_draft(draft_id: str, base: Path) -> Any:
    """按目录 listing 等值匹配草稿（不把用户输入拼进路径）。"""
    if not base.is_dir():
        return None
    for child in base.iterdir():
        if child.is_dir() and child.name == draft_id and (child / "manifest.json").is_file():
            return child
    return None


def load_manifest(draft_id: str, *, draft_dir: Any = None) -> Dict[str, Any]:
    """读取草稿 manifest（不存在则抛 FileNotFoundError）。"""
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    root = _find_draft(draft_id, base)
    if root is None:
        raise FileNotFoundError("草稿不存在: " + str(draft_id))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return {"root": root, "manifest": manifest}


def load_workflow(draft_id: str, *, draft_dir: Any = None) -> Dict[str, Any]:
    """读取草稿的 workflow JSON 与 manifest（不存在则抛 FileNotFoundError）。"""
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    root = _find_draft(draft_id, base)
    if root is None:
        raise FileNotFoundError("草稿不存在: " + str(draft_id))
    path = _workflow_file(root)
    if path is None:
        raise FileNotFoundError("该草稿不是工作流形态（没有 workflows/*.json）")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict):
        raise ValueError("workflow JSON 根节点必须是对象")
    return {"root": root, "manifest": manifest, "workflow": workflow, "path": path}


def save_workflow(draft_id: str, workflow: Dict[str, Any], *, warnings: Any = None,
                  draft_dir: Any = None) -> Dict[str, Any]:
    """把绑定后的 workflow 写回草稿，并同步 manifest（requires_binding / warnings / meta）。"""
    data = load_workflow(draft_id, draft_dir=draft_dir)
    root, manifest, path = data["root"], data["manifest"], data["path"]
    steps = workflow.get("steps") or []
    bound = sum(1 for s in steps if str(s.get("tool_name") or "").strip())
    total = len(steps)
    workflow["requires_binding"] = bound < total
    path.write_text(json.dumps(workflow, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest["requires_binding"] = bound < total
    manifest["binding"] = {"bound": bound, "total": total,
                           "model": (workflow.get("binding_meta") or {}).get("model", "")}
    if warnings is not None:
        manifest["warnings"] = [str(w) for w in warnings]
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"draft_dir": str(root), "bound": bound, "total": total}


def delete_draft(draft_id: str, *, draft_dir: Any = None) -> bool:
    """删除一个骨架草稿目录（仅隔离区，且只接受 listing 里真实存在的名字）。

    安全：**不把用户输入拼进路径** —— 先列目录、再按等值比较选目标（同 #1283 的
    CodeQL 收口口径），删除对象因此只可能是 data/ingestion/drafts/ 下的子目录。
    """
    base = Path(draft_dir) if draft_dir else Path(DEFAULT_DRAFT_DIR)
    if not base.is_dir():
        return False
    target = None
    for child in base.iterdir():
        if child.is_dir() and child.name == draft_id and (child / "manifest.json").is_file():
            target = child
            break
    if target is None:
        return False
    shutil.rmtree(target)
    return True


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
    "delete_draft",
    "draft_id_for",
    "extract_steps",
    "list_drafts",
    "load_manifest",
    "load_workflow",
    "save_workflow",
    "write_draft",
    "write_tool_draft",
    "write_workflow_draft",
]
