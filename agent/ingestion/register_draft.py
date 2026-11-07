# -*- coding: utf-8 -*-
"""把隔离草稿**登记**成真实能力（技能 / 工具 / 工作流），可选一并启用。

【为什么需要】#1434 把能力获取收敛成「只出草稿」，但草稿页此前只有一个**假提示**
（"登记去「转化队列」Tab"）—— 转化队列只认 `/api/skills-mgmt/queue` 里的**技能**草稿，
tool/workflow 骨架根本不在那儿，用户按提示走过去只会看到空队列。本模块补上真实的登记口。

【三种形态的落点】

| form | 落点 | 启用后 |
|---|---|---|
| skill | `data/skills_repo/<id>/SKILL.md` + `CARD.md`（文件轨）→ `backfill_file_track` 补主轨 | front matter `status: approved` + `enabled: true` |
| tool | `agent/tools/custom/<name>.py` + `data/tool_definitions/<name>.yaml` + `tests/unit/test_<name>.py` → `persist_dynamic_tool` 台账 | YAML `enabled: true` |
| workflow | `data/learned_workflows.json`（`WorkflowRepository.upsert`） | `enabled=true` + `status=active` |

【边界（与草稿期一致）】
- **默认不启用**：登记 = 进台账，`enable=True` 才改状态。理由同口径：新工具默认 `enabled: false` 先观察；
- 工具登记**必须过验证门**（`persist_dynamic_tool(verify=True)`）；不通过就抛错，本模块**不暴露 force**；
- 只按**目录 listing 等值匹配**定位草稿（不把用户输入拼进路径）；
- 不删草稿：登记后草稿仍在隔离区（manifest 标 `registered`），便于回看与回退。
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from . import drafts as drafts_mod

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
SKILLS_REPO = ROOT / "data" / "skills_repo"
CUSTOM_TOOLS = ROOT / "agent" / "tools" / "custom"
TOOL_DEFS = ROOT / "data" / "tool_definitions"
TESTS_DIR = ROOT / "tests" / "unit"

#: 技能 front matter 里「启用」要改的两行（登记 ≠ 上线，只在 enable=True 时改）
_SKILL_ENABLE_PATCH = (("status:", "status: approved"), ("enabled:", "enabled: true"))


class RegisterError(ValueError):
    """登记失败（HTTP 层映射 400/409）。

    带 `message` 自定义属性：HTTP 层只读它（`str(exc)` 会被判 py/stack-trace-exposure，
    见口径 §4 的两种安全出口）。
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = str(message)


def _patch_front_matter(text: str) -> str:
    """把 front matter 的 status/enabled 改写成已批准 + 启用（只动这两行）。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise RegisterError("SKILL.md 缺 front matter，无法登记为技能")
    out, in_fm = [lines[0]], True
    for line in lines[1:]:
        if in_fm and line.strip() == "---":
            in_fm = False
            out.append(line)
            continue
        if in_fm:
            key = line.split(":", 1)[0].strip()
            replaced = dict(_SKILL_ENABLE_PATCH).get(key + ":")
            if replaced:
                out.append(replaced)
                continue
        out.append(line)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _skill_id(manifest: Dict[str, Any]) -> str:
    sid = str(manifest.get("skill_id") or "").strip()
    if not sid or "/" in sid or "\\" in sid or sid in (".", ".."):
        raise RegisterError("草稿 manifest 里的 skill_id 不合法：" + repr(sid))
    return sid


def register_skill(root: Path, manifest: Dict[str, Any], *, enable: bool = False) -> Dict[str, Any]:
    """技能草稿 → 文件轨 + 主轨（走 backfill_file_track，不手写 JSON 台账）。"""
    sid = _skill_id(manifest)
    src_md = root / "SKILL.md"
    if not src_md.is_file():
        raise RegisterError("草稿里没有 SKILL.md，无法登记为技能")
    text = src_md.read_text(encoding="utf-8", errors="replace")
    if enable:
        text = _patch_front_matter(text)

    dest = SKILLS_REPO / sid
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(text, encoding="utf-8")
    card = root / "CARD.md"
    if card.is_file():
        shutil.copyfile(card, dest / "CARD.md")

    backfilled: Dict[str, Any] = {}
    try:
        from agent.skills_mgmt import SkillsMgmtService
        svc = SkillsMgmtService()
        backfilled = svc.backfill_file_track(dry_run=False) or {}
    except Exception as exc:  # noqa: BLE001 主轨补登记失败要如实说，但文件轨已落盘
        logger.warning("[register] 主轨补登记失败 skill=%s: %s", sid, exc)
        raise RegisterError("文件轨已写入，但主轨登记失败（" + type(exc).__name__
                            + "，详见服务日志）") from exc
    return {"form": "skill", "skill_id": sid, "target": str(dest),
            "enabled": bool(enable), "backfill": backfilled}


def register_tool(root: Path, manifest: Dict[str, Any], *, enable: bool = False) -> Dict[str, Any]:
    """工具草稿 → 动态工具目录 + 治理 YAML + 单测；过验证门后进台账。"""
    py_files = sorted((root / "agent" / "tools").glob("*.py")) if (root / "agent" / "tools").is_dir() else []
    yaml_files = sorted((root / "data" / "tool_definitions").glob("*.yaml")) if (root / "data" / "tool_definitions").is_dir() else []
    if not py_files or not yaml_files:
        raise RegisterError("工具草稿缺 agent/tools/*.py 或 data/tool_definitions/*.yaml（先点「AI 实现」）")
    src_py, src_yaml = py_files[0], yaml_files[0]
    declared = yaml.safe_load(src_yaml.read_text(encoding="utf-8", errors="replace")) or {}
    name = str(declared.get("name") or src_py.stem).strip()
    if not name or "/" in name or "\\" in name:
        raise RegisterError("工具名不合法：" + repr(name))

    CUSTOM_TOOLS.mkdir(parents=True, exist_ok=True)
    dest_py = CUSTOM_TOOLS / (name + ".py")
    shutil.copyfile(src_py, dest_py)

    declared["enabled"] = bool(enable)
    if not enable:
        declared.setdefault("enabled", False)
    (TOOL_DEFS / (name + ".yaml")).parent.mkdir(parents=True, exist_ok=True)
    (TOOL_DEFS / (name + ".yaml")).write_text(
        yaml.safe_dump(declared, allow_unicode=True, sort_keys=False), encoding="utf-8")

    tests = sorted((root / "tests" / "unit").glob("test_*.py")) if (root / "tests" / "unit").is_dir() else []
    dest_test = None
    if tests:
        TESTS_DIR.mkdir(parents=True, exist_ok=True)
        dest_test = TESTS_DIR / tests[0].name
        shutil.copyfile(tests[0], dest_test)

    added_shim = _ensure_register_all(dest_py, name, str(declared.get("description") or ""))
    code = dest_py.read_text(encoding="utf-8", errors="replace")
    from agent.tools import persistence
    ok = persistence.persist_dynamic_tool(
        name,
        str(declared.get("description") or ""),
        (declared.get("schema") or None),
        source="ingestion:" + str(manifest.get("draft_id") or ""),
        module_path=str(dest_py),
        plane=str(declared.get("plane") or persistence.DEFAULT_PLANE),
        effect=str(declared.get("effect") or persistence.DEFAULT_EFFECT),
        risk=str(declared.get("risk") or persistence.DEFAULT_RISK),
        category=str(declared.get("category") or "custom"),
        code=code,
    )
    if not ok:
        raise RegisterError("工具未通过验证门（导入/调用探针失败），已拒绝登记；详见服务日志")
    loaded = 0
    try:
        loaded = int(persistence.load_dynamic_tools() or 0)
    except Exception as exc:  # noqa: BLE001 运行时加载失败不回滚文件（重启即加载）
        logger.warning("[register] 运行时加载动态工具失败: %s", exc)

    # 【回读校验】台账/文件写成功 ≠ 注册表里有它。2026-11-07 实测过一条"registered 但
    # 工具根本不在 /api/tools/config"的错账（模块没进 custom/、也没 register_all）——
    # 队列据此显示「去工具页启用」，点过去必然是「未找到工具」。所以这里必须回读：
    registered = False
    try:
        from agent import tools as _tools
        registered = name in getattr(_tools, "_registry", {})
    except Exception as exc:  # noqa: BLE001
        logger.warning("[register] 回读注册表失败 tool=%s: %s", name, exc)
    if not registered:
        raise RegisterError(
            "工具文件已写入 agent/tools/custom/，但回读注册表里没有它（未真正可调用）"
            "；请检查模块能否导入、是否导出 register_all、依赖是否齐备"
        )
    return {"form": "tool", "tool_name": name, "target": str(dest_py),
            "test_file": str(dest_test) if dest_test else "", "enabled": bool(enable),
            "register_all_added": added_shim, "loaded": loaded}


def _ensure_register_all(path: Path, name: str, description: str) -> bool:
    """给拷贝过去的动态工具补一个 `register_all`（返回是否补了）。

    【为什么必须】`persistence.load_dynamic_tools()` 逐个 import 后只认 `register_all()`
    （见其 docstring：「每个 agent/tools/custom/**/*.py 都应导出 register_all」）。
    AI 实现产出的是 `TOOL_SCHEMA` + 函数，没有这个壳 ⇒ 加载器直接跳过，
    结果是**台账里有、注册表里没有**：`/api/tools/config` 数不到它，模型也调不到。
    （2026-11-07 实测：登记成功、审批闸也拦得住，但 config 里始终不出现。）
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    if "def register_all" in text:
        return False
    handler = name if ("def %s(" % name) in text else ""
    if not handler:
        m = re.search(r"^def ([A-Za-z_][A-Za-z0-9_]*)\(", text, re.M)
        handler = m.group(1) if m else ""
    if not handler:
        raise RegisterError("工具模块里找不到可注册的处理函数（先「AI 实现」）")
    safe_desc = description.replace("\\", " ").replace('"', "'")[:200]
    shim = (
        "\n\n"
        "def register_all(dl=None):  # noqa: ARG001 动态加载器契约\n"
        '    """把本模块注册进全局注册表（persistence.load_dynamic_tools 要求导出本函数）。"""\n'
        "    from agent import tools as _tools\n"
        "    _tools.register_dynamic(\n"
        '        "{name}", "{desc}",\n'
        "        handler={handler}, schema=TOOL_SCHEMA,\n"
        '        source="generated", source_id="ingestion_{name}",\n'
        "    )\n"
    ).format(name=name, desc=safe_desc, handler=handler)
    path.write_text(text + shim, encoding="utf-8")
    return True

def register_workflow(root: Path, manifest: Dict[str, Any], *, enable: bool = False) -> Dict[str, Any]:
    """工作流草稿 → data/learned_workflows.json（必须已绑定步骤）。"""
    wf_path = drafts_mod._workflow_file(root)  # noqa: SLF001 同包内共用解析
    if wf_path is None:
        raise RegisterError("草稿里没有 workflows/*.json")
    raw = json.loads(wf_path.read_text(encoding="utf-8", errors="replace"))
    steps = raw.get("steps") or []
    if not steps:
        raise RegisterError("工作流草稿 0 步，不能登记（先补步骤或改用技能形态）")

    # 【工具步 vs LLM 步】没绑到工具的步骤**不是废步**：它们本来就是「需要判断的步骤」
    # （读 diff、看方案是否合理…），云枢的工作流模型支持 need_llm=True + prompt_template。
    # 登记时把它们就地补成 LLM 步（prompt 用**原描述原文**，不编造），并如实回报两类步数；
    # 这样「绑上 8 步、剩 11 步靠模型判断」的工作流才登记得进去、也跑得起来。
    tool_steps = llm_steps = 0
    for s in steps:
        if str(s.get("tool_name") or "").strip():
            tool_steps += 1
            continue
        if s.get("need_llm") and str(s.get("prompt_template") or "").strip():
            llm_steps += 1
            continue
        prompt = str(s.get("description") or s.get("action") or "").strip()
        if not prompt:
            raise RegisterError("步骤 %s 既没绑工具也没描述，无法登记" % s.get("step_id"))
        s["need_llm"] = True
        s["prompt_template"] = prompt
        llm_steps += 1

    from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep, WorkflowStatus
    from agent.workflow_learning.repository import WorkflowRepository

    wf = LearnedWorkflow(
        id=str(raw.get("id") or manifest.get("skill_id") or "").strip(),
        name=str(raw.get("name") or "").strip() or str(manifest.get("skill_name") or ""),
        description=str(raw.get("description") or ""),
        task_signature=str(raw.get("task_signature") or raw.get("id") or ""),
        trigger_patterns=[str(x) for x in (raw.get("trigger_patterns") or [])],
        steps=[WorkflowStep(**s) for s in steps],
        enabled=bool(enable),
        status=WorkflowStatus.ACTIVE if enable else WorkflowStatus.DRAFT,
        # 有 LLM 步就必须声明 hybrid（模型里 toolchain 禁含 need_llm 步骤）；这条校验
        # 是模型自己抛的，不靠调用方记得 —— 这里只按实际步型如实声明。
        workflow_type="hybrid" if llm_steps else "toolchain",
        tags=["ingestion", "ingested"],
    )
    repo = WorkflowRepository()
    repo.upsert(wf)
    return {"form": "workflow", "workflow_id": wf.id, "steps": len(steps),
            "tool_steps": tool_steps, "llm_steps": llm_steps,
            "target": "data/learned_workflows.json", "enabled": bool(enable)}


_HANDLERS = {"skill": register_skill, "tool": register_tool, "workflow": register_workflow}


def register_draft(draft_id: str, *, enable: bool = False,
                   draft_dir: Optional[Any] = None) -> Dict[str, Any]:
    """按 manifest.form 分派；返回登记结果（HTTP 层直接回给前端）。"""
    data = drafts_mod.load_manifest(draft_id, draft_dir=draft_dir)
    root, manifest = data["root"], data["manifest"]
    form = str(manifest.get("form") or "").strip().lower()
    handler = _HANDLERS.get(form)
    if handler is None:
        raise RegisterError("未知草稿形态：" + repr(form))
    result = handler(root, manifest, enable=enable)
    manifest["status"] = "registered"
    manifest["registered_at"] = drafts_mod._now_iso()  # noqa: SLF001 同包
    manifest["enabled"] = bool(enable)
    manifest["registered"] = result
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


__all__ = ["RegisterError", "register_draft"]