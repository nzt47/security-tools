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


__all__ = ["REGISTRABLE", "RegisterError", "register_skill_job"]
