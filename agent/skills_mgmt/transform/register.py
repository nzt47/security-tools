# -*- coding: utf-8 -*-
"""S5 第二批：把「隔离区草稿」登记成技能（转化队列页的显式动作）。

【为什么单独一层】能力获取页从 S5 起只产出草稿（不注册、不启用）；登记动作搬到这里，
但**不静默写盘**：它写的是 tracked 的 data/skills_repo/<id>/，所以：
  1. 目标已存在 ⇒ 直接拒绝（不覆盖别人的技能）；
  2. 登记后调用既有 intake（补 front matter / 基线），失败则回滚新写的目录；
  3. 真正进 git 仍然要 `open_pr=true`（复用 intake 的 commit → push → PR），默认只停在工作区；
  4. 台账推进到 registered，**不自动启用**（启用是人的决定，走技能中心的既有开关）。
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from . import jobs as _jobs

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
SKILLS_REPO = Path("data") / "skills_repo"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
#: 只有这些状态可以登记（registered 之后只能启用/归档）
REGISTRABLE = frozenset({"candidate", "normalized", "reviewed"})


class RegisterError(Exception):
    """登记失败（带稳定 code，路由据此回 4xx）。"""

    def __init__(self, message: str, *, code: str = "register-failed",
                 status: int = 400) -> None:
        super().__init__(message)
        # 【CodeQL】异常对象的**自定义属性**不被当作「异常原文」（§11.42 B）：
        # 路由返回 exc.message，而不是 str(exc)（后者会被判 py/stack-trace-exposure）。
        self.message = str(message)
        self.code = code
        self.status = status


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _draft_skill_id(manifest: Dict[str, Any], skill_md: str) -> str:
    """草稿里的技能 id（manifest 优先，其次 front matter）"""
    sid = str(manifest.get("skill_id") or "").strip()
    if sid:
        return sid
    for line in skill_md.splitlines():
        if line.strip().startswith("id:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def register_skill_job(job_id: str, *, open_pr: bool = False,
                       root: Optional[Any] = None) -> Dict[str, Any]:
    """把一个 ingestion 技能草稿登记进文件轨（并可选开 PR）

    Raises:
        RegisterError: 作业不存在 / 状态不允许 / 形态不是 skill / 草稿缺 SKILL.md /
                       id 非法 / 目标已存在 / intake 校验失败
    """
    import json

    from scripts.intake_skill import intake_one, open_pr as _open_pr

    base = Path(root) if root else ROOT
    job = _jobs.get_job(str(job_id), root=root)
    if job is None:
        raise RegisterError("作业不存在: %s" % job_id, code="job-not-found", status=404)
    status = str(job.get("status") or "")
    if status not in REGISTRABLE:
        raise RegisterError("作业当前状态 %s 不能登记（允许：%s）"
                            % (status, ", ".join(sorted(REGISTRABLE))),
                            code="job-status", status=409)
    if str(job.get("form") or "") != "skill":
        raise RegisterError("只有 skill 形态的草稿能按技能登记（当前 %s）" % job.get("form"),
                            code="job-form", status=409)

    draft_dir = Path(str(job.get("artifacts") or ""))
    skill_md_path = draft_dir / "SKILL.md"
    if not skill_md_path.is_file():
        raise RegisterError("草稿目录里没有 SKILL.md：%s" % draft_dir,
                            code="draft-missing", status=409)
    manifest: Dict[str, Any] = {}
    manifest_path = draft_dir / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(_read_text(manifest_path) or "{}")
        except ValueError:
            manifest = {}
    skill_md = _read_text(skill_md_path)
    skill_id = _draft_skill_id(manifest if isinstance(manifest, dict) else {}, skill_md)
    if not _ID_RE.match(skill_id):
        raise RegisterError("草稿里的技能 id 非法：%r" % skill_id, code="skill-id-invalid")

    target = base / SKILLS_REPO / skill_id
    if target.exists():
        raise RegisterError("文件轨里已经有 %s，不覆盖（请先人工处理或换个 id）" % skill_id,
                            code="skill-exists", status=409)

    card_src = draft_dir / "CARD.md"
    try:
        target.mkdir(parents=True)
        (target / "skill.md").write_text(skill_md, encoding="utf-8")
        if card_src.is_file():
            (target / "CARD.md").write_text(_read_text(card_src), encoding="utf-8")
    except OSError as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise RegisterError("写文件轨失败：%s" % type(exc).__name__,
                            code="write-failed", status=500) from exc

    # 复用既有 intake：补 front matter / 基线 / 校验；失败回滚刚写的目录
    try:
        rep = intake_one(skill_id, execute=True, fix_baselines=True, fix_meta=True)
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(target, ignore_errors=True)
        raise RegisterError("入轨校验异常：%s" % type(exc).__name__,
                            code="intake-failed", status=500) from exc
    if not isinstance(rep, dict) or not rep.get("ok"):
        shutil.rmtree(target, ignore_errors=True)
        detail = (rep or {}).get("error") if isinstance(rep, dict) else ""
        raise RegisterError("入轨校验未通过：%s" % (detail or "见 intake 报告"),
                            code="intake-rejected", status=409)

    pr: Dict[str, Any] = {}
    if open_pr:
        pr = _open_pr(skill_id) or {}
        if not pr.get("ok"):
            # 文件已就绪，只是没开成 PR：如实返回，不回滚（人可以在工作区里继续）
            logger.warning("[transform.register] 开 PR 失败 skill=%s: %s",
                           skill_id, pr.get("error"))

    # 状态机要求 candidate 先到 normalized 才能 registered：登记本身就是一次规范化
    # （intake 补 front matter / 基线），所以这里补这一步，而不是放宽状态机。
    if status == "candidate":
        _jobs.transition(job_id, "normalized", root=root,
                         note="登记前规范化（intake 补 front matter）")
    registered = _jobs.transition(job_id, "registered", root=root,
                                  target={"kind": "skill", "id": skill_id},
                                  note="从草稿登记（%s）" % ("PR" if pr.get("pr_url") else "工作区"))
    return {
        "ok": True,
        "job": registered,
        "skill_id": skill_id,
        "skill_dir": str(target.relative_to(base) if str(target).startswith(str(base)) else target),
        "intake": rep,
        "pr": pr,
        "enabled": False,
        "next": "技能中心 → 该技能 → 启用（本动作只登记，不启用）",
    }


def enable_skill_job(job_id: str, *, root: Optional[Any] = None) -> Dict[str, Any]:
    """把已登记（registered）的技能作业启用（S5 第三批：启用也进队列）

    **双写**（§8 陷阱 7）：先改文件轨 skill.md 的 enabled: true（失败即中止），
    再走主轨 set_enabled（唯一落库点，自带审计 + 钩子），最后台账 registered → enabled。
    只做"启用"这一个方向：停用仍留在技能中心（避免把停用做成两个入口）。

    **主轨独有技能**（没有 `data/skills_repo/<id>/skill.md`，如 global-core-principles /
    skill）不报错：只改主轨，返回值里 `file_track=""`、`file_track_patched=False` 并带一句
    `note`。`set_enabled` 之前会先确认主轨确实有这条技能，否则报 404 —— 不谎报"启用成功"。

    Raises:
        RegisterError: 作业不存在 / 状态不是 registered / 目标不是 skill /
                       两轨都没有这条技能 / 主轨启用失败
    """
    from agent.skills_mgmt.file_store import SkillMDParser

    base = Path(root) if root else ROOT
    job = _jobs.get_job(str(job_id), root=root)
    if job is None:
        raise RegisterError("作业不存在: %s" % job_id, code="job-not-found", status=404)
    status = str(job.get("status") or "")
    if status != "registered":
        raise RegisterError("只有 registered 的作业能启用（当前 %s）" % status,
                            code="job-status", status=409)
    target = job.get("target") or {}
    if str(target.get("kind") or "") != "skill":
        raise RegisterError("只有技能作业能在这里启用（当前 %s）" % target.get("kind"),
                            code="job-form", status=409)
    skill_id = str(target.get("id") or "").strip()
    if not _ID_RE.match(skill_id):
        raise RegisterError("作业目标 id 非法：%r" % skill_id, code="skill-id-invalid")

    md_path = base / SKILLS_REPO / skill_id / "skill.md"
    # 【主轨独有技能是合法状态】global-core-principles / skill 这类技能**只有主轨**
    # （口径 §5：不给它们建文件轨，materialize 会 409）。2026-11-07 实测：转化队列里
    # 对 id 为 brainstorming 的作业点「启用」，用户看到的是
    # 「文件轨里没有 brainstorming/skill.md」—— 作业本身是对的，是这里把
    # "双写"当成了"必须两轨都存在"。文件轨不在 ⇒ 只改主轨，并如实回报。
    file_track = md_path.is_file()
    text = ""
    if file_track:
        try:
            text = md_path.read_text(encoding="utf-8")
            patched = SkillMDParser.patch_front_matter(text, {"enabled": True})
            if patched != text:
                md_path.write_text(patched, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            raise RegisterError("写文件轨失败：%s" % type(exc).__name__,
                                code="write-failed", status=500) from exc

    try:
        from agent.state_manager import get_skills_mgmt_service
        svc = get_skills_mgmt_service()
        if not file_track:
            # 没有文件轨可兜底 ⇒ 先确认主轨确实有这条技能，否则"启用成功"就是谎报
            getter = getattr(svc, "get", None)
            if callable(getter):
                try:
                    getter(skill_id)
                except Exception as exc:  # noqa: BLE001
                    raise RegisterError(
                        "主轨里也没有技能 %s（文件轨与主轨都不存在），无法启用" % skill_id,
                        code="skill-not-found", status=404) from exc
        svc.set_enabled(skill_id, True)
    except RegisterError:
        raise
    except Exception as exc:  # noqa: BLE001
        # 文件已改成 enabled: true，主轨失败 ⇒ 回滚文件，保持两轨一致
        if file_track:
            try:
                md_path.write_text(text, encoding="utf-8")
            except OSError:
                logger.warning("[transform.enable] 回滚文件轨失败 skill=%s", skill_id)
        raise RegisterError("主轨启用失败：%s" % type(exc).__name__,
                            code="enable-failed", status=500) from exc

    note = ("从转化队列启用（双写：文件轨 + 主轨）" if file_track
            else "从转化队列启用（该技能只有主轨，没有文件轨；本次只改主轨）")
    enabled_job = _jobs.transition(job_id, "enabled", root=root, note=note)
    return {"ok": True, "job": enabled_job, "skill_id": skill_id, "enabled": True,
            "file_track": str(md_path) if file_track else "",
            "file_track_patched": file_track,
            "note": "" if file_track else "该技能只有主轨（无文件轨），已只改主轨"}


def register_tool_job(job_id: str, *, root: Optional[Any] = None) -> Dict[str, Any]:
    """把工具**骨架**草稿登记进仓库（仍 status=draft / enabled=false，不会立刻可用）

    只搬三件产物：agent/tools/<name>.py（NotImplementedError 骨架）、
    data/tool_definitions/<name>.yaml（enabled: false）、tests/unit/test_<name>.py。
    任一目标已存在 ⇒ 整体拒绝（不做半套复制）。
    """
    import json

    base = Path(root) if root else ROOT
    job = _jobs.get_job(str(job_id), root=root)
    if job is None:
        raise RegisterError("作业不存在: %s" % job_id, code="job-not-found", status=404)
    if str(job.get("status") or "") not in REGISTRABLE:
        raise RegisterError("作业当前状态 %s 不能登记" % job.get("status"),
                            code="job-status", status=409)
    draft_dir = Path(str(job.get("artifacts") or ""))
    manifest_path = draft_dir / "manifest.json"
    try:
        manifest = json.loads(_read_text(manifest_path) or "{}")
    except ValueError:
        manifest = {}
    files = [str(f) for f in (manifest.get("files") or []) if str(f).strip()]
    wanted = [f for f in files if f.endswith(".py") or f.endswith(".yaml")]
    if not wanted:
        raise RegisterError("草稿 manifest 里没有可登记的产物（.py/.yaml）",
                            code="draft-missing", status=409)
    # 先整体检查目标是否存在，再复制（避免半套）
    for rel in wanted:
        if (base / rel).exists():
            raise RegisterError("目标已存在，不覆盖：%s" % rel, code="target-exists", status=409)
    copied = []
    for rel in wanted:
        src = draft_dir / rel
        if not src.is_file():
            continue
        dst = base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        copied.append(rel)
    if str(job.get("status") or "") == "candidate":
        _jobs.transition(job_id, "normalized", root=root, note="登记前规范化")
    registered = _jobs.transition(job_id, "registered", root=root,
                                  target={"kind": "tool", "id": str(manifest.get("skill_id") or "")},
                                  note="从草稿登记（工具骨架，未启用）")
    return {"ok": True, "job": registered, "form": "tool", "copied": copied,
            "enabled": False,
            "next": "人工补实现与测试 → 审批 → 在工具与接入里启用"}


def register_workflow_job(job_id: str, *, root: Optional[Any] = None) -> Dict[str, Any]:
    """把工作流草稿登记进工作流仓库（status=draft / enabled=false）

    步骤**没绑定工具**时拒绝：登记一条跑不起来的记录只会让匹配器给出假候选。
    """
    import json

    base = Path(root) if root else ROOT
    job = _jobs.get_job(str(job_id), root=root)
    if job is None:
        raise RegisterError("作业不存在: %s" % job_id, code="job-not-found", status=404)
    if str(job.get("status") or "") not in REGISTRABLE:
        raise RegisterError("作业当前状态 %s 不能登记" % job.get("status"),
                            code="job-status", status=409)
    draft_dir = Path(str(job.get("artifacts") or ""))
    wf_files = sorted((draft_dir / "workflows").glob("*.json")) if (draft_dir / "workflows").is_dir() else []
    if not wf_files:
        raise RegisterError("草稿里没有 workflows/*.json", code="draft-missing", status=409)
    try:
        raw = json.loads(_read_text(wf_files[0]) or "{}")
    except ValueError as exc:
        raise RegisterError("工作流草稿不是合法 JSON", code="draft-broken", status=409) from exc
    steps = raw.get("steps") or []
    unbound = [i for i, s in enumerate(steps, start=1) if not str(s.get("tool_name") or "").strip()]
    if unbound:
        raise RegisterError(
            "还有 %d 个步骤没绑定工具（第 %s 步）⇒ 登记后也跑不起来；先在草稿里补 tool_name/params_template"
            % (len(unbound), ", ".join(str(i) for i in unbound[:5])),
            code="workflow-unbound", status=409)

    from agent.state_manager import get_workflow_learning_service
    from agent.workflow_learning.models import LearnedWorkflow

    payload = dict(raw)
    payload.setdefault("task_signature", str(raw.get("name") or raw.get("id") or ""))
    payload["status"] = "draft"
    payload["enabled"] = False
    try:
        wf = LearnedWorkflow(**payload)
    except Exception as exc:  # noqa: BLE001 模型校验失败要如实回
        raise RegisterError("工作流模型校验未通过：%s" % type(exc).__name__,
                            code="workflow-invalid", status=409) from exc
    svc = get_workflow_learning_service()
    if svc.get(wf.id) is not None:
        raise RegisterError("工作流仓库里已有 %s，不覆盖" % wf.id,
                            code="target-exists", status=409)
    svc.register_workflow(wf)
    if str(job.get("status") or "") == "candidate":
        _jobs.transition(job_id, "normalized", root=root, note="登记前规范化")
    registered = _jobs.transition(job_id, "registered", root=root,
                                  target={"kind": "workflow", "id": wf.id},
                                  note="从草稿登记（工作流，未启用）")
    return {"ok": True, "job": registered, "form": "workflow", "workflow_id": wf.id,
            "enabled": False, "next": "在工作流页启用（本动作只登记）"}


def register_draft_job(job_id: str, *, open_pr: bool = False,
                       root: Optional[Any] = None) -> Dict[str, Any]:
    """按作业形态分派登记（skill / tool / workflow）；只有 skill 支持开 PR。"""
    job = _jobs.get_job(str(job_id), root=root)
    if job is None:
        raise RegisterError("作业不存在: %s" % job_id, code="job-not-found", status=404)
    form = str(job.get("form") or "")
    if form == "skill":
        return register_skill_job(job_id, open_pr=open_pr, root=root)
    if form == "tool":
        return register_tool_job(job_id, root=root)
    if form == "workflow":
        return register_workflow_job(job_id, root=root)
    raise RegisterError("未知形态：%r" % form, code="job-form", status=409)


__all__ = ["REGISTRABLE", "RegisterError", "enable_skill_job", "register_draft_job",
           "register_skill_job", "register_tool_job", "register_workflow_job"]
