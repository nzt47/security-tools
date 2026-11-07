# -*- coding: utf-8 -*-
"""S1：**统一转化作业台账**（把三套入口收敛成同一种「作业」）。

【为什么要有这一层】
现在「能力获取 → 草稿评审」「技能中心 → 转化队列」「技能中心 → 入轨助手」是三套列表、
三套状态、三套「下一步」。用户的实际问题不是"页面不好看"，而是**看不出这条外来能力现在
到哪一步、下一步点哪**（登记为工具之后只有一句 toast，草稿行状态不变，就是最典型的断头路）。

本模块只做一件最小的事：给每条转化作业一条**稳定记录**：

    candidate → normalized → reviewed → registered → enabled
    （旁路终态：archived / failed；failed 可回 candidate 重试）

来源适配器（ingestion / transform / intake）各自照旧做判定，只负责**登记作业**与
**推进状态**；界面以后只读这一张表（S1b 加 HTTP 端点，S5 收口 IA）。

【与原文的关系】台账只存**元数据**（来源、形态、目标、产物目录、状态流转时间），
不存外来正文 —— 与"读完即弃、只留记录"的口径一致（正文存放策略见 S3）。

【为什么不塞进 DB】三套入口的产物本来就是**目录**（drafts/、transforms/<tid>/、skills_repo/），
台账用一份 JSON 与之并列，回滚 = 删目录 + 改台账；不引入迁移成本。
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

#: 台账落点（相对仓库根；测试可传 root 覆盖）
JOBS_REL = Path("data") / "transforms" / "jobs.json"

#: 状态机：只允许这些前进方向（旁路终态单独列）
STATES = ("candidate", "normalized", "reviewed", "registered", "enabled",
          "archived", "failed")

_TRANSITIONS = {
    "candidate": {"normalized", "reviewed", "archived", "failed"},
    "normalized": {"reviewed", "registered", "archived", "failed"},
    "reviewed": {"registered", "archived", "failed"},
    "registered": {"enabled", "archived"},
    "enabled": {"archived"},
    "archived": set(),
    "failed": {"candidate", "archived"},
}

_LOCK = threading.RLock()

#: 到达这些状态就"读完即弃"：清掉作业目录里的外来原文副本（raw.md）
_PURGE_STATUSES = ("registered", "enabled")


def _purge_raw_copy(job: Dict[str, Any]) -> None:
    """作业内化后清掉**隔离副本** raw.md（S5「读完即弃」）

    - 只删 raw.md（外来原文副本）；evidence.json / card.md / compare / review 保留；
    - CP_RETAIN_QUARANTINE_COPY_DAYS > 0 时不删（查纠纷用的隔离期）；
    - best-effort：删不掉只记日志，**绝不**让状态流转失败。
    """
    try:
        from agent.ingestion.rewrite import retain_quarantine_copy_days
        if retain_quarantine_copy_days() > 0:
            return
        base = str(job.get("artifacts") or "")
        if not base:
            return
        raw = Path(base) / "raw.md"
        if raw.is_file():
            raw.unlink()
            logger.info("[transform.jobs] 已清掉隔离副本 raw.md: %s", raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[transform.jobs] 清理隔离副本失败（不影响流转）: %s", exc)


class JobError(Exception):
    """作业台账的显式失败（非法状态流转 / 找不到作业）。"""


def jobs_path(root: Optional[Any] = None) -> Path:
    return (Path(root) if root else Path(".")) / JOBS_REL


def job_id(source: str, source_ref: str) -> str:
    """作业 id = 来源 + 来源内标识（幂等：同一来源同一条目永远同一作业）。"""
    return "%s:%s" % (str(source or "").strip(), str(source_ref or "").strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_rev(jobs: Dict[str, Dict[str, Any]]) -> int:
    """单调递增的修订号（**排序用**）。

    【为什么不能只按 updated_at 排】Windows 的 datetime.now() 只有约 15ms 粒度，
    连续两次写入会拿到**完全相同**的时间戳（实测），队列页顺序就随机了。
    rev 由本进程单调发号，和 updated_at 一起做 (时间, rev) 排序即可稳定。
    """
    return max((int(j.get("rev") or 0) for j in jobs.values()), default=0) + 1


def _load(path: Path) -> Dict[str, Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:  # 台账坏掉不该让面板/登记挂掉
        logger.warning("[transforms.jobs] 台账读取失败，按空台账处理: %s", exc)
        return {}
    items = raw.get("jobs") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for item in items:
        if isinstance(item, dict) and item.get("job_id"):
            out[str(item["job_id"])] = item
    return out


def _save(path: Path, jobs: Dict[str, Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "jobs": list(jobs.values())}
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)   # 原子替换：半写文件不会被读到


def upsert_job(*, source: str, source_ref: str, form: str, title: str = "",
               status: str = "candidate", artifacts: str = "",
               target: Optional[Dict[str, str]] = None,
               note: str = "", root: Optional[Any] = None) -> Dict[str, Any]:
    """登记/更新一条作业（幂等；不改已到达的更远状态）。

    【为什么不覆盖状态】同一条作业会被多个钩子重复 upsert（草稿写入、AI 实现、登记）。
    谁先写成 registered，后来的 candidate 不应把它退回去 —— 状态只由 transition() 前进。
    """
    if status not in STATES:
        raise JobError("未知作业状态: %r" % (status,))
    jid = job_id(source, source_ref)
    with _LOCK:
        path = jobs_path(root)
        jobs = _load(path)
        now = _now()
        job = jobs.get(jid)
        if job is None:
            # 新建时可以直接落在给定状态：这是**导入既有产物**（老草稿/老归档）的路径，
            # 不是状态流转，不该被状态机拦住。
            job = {
                "job_id": jid,
                "source": str(source),
                "source_ref": str(source_ref),
                "form": str(form or ""),
                "title": str(title or source_ref),
                "status": status,
                "artifacts": str(artifacts or ""),
                "target": dict(target or {}),
                "note": str(note or ""),
                "history": [],
                "created_at": now,
                "updated_at": now,
            }
            jobs[jid] = job
        else:
            # 元数据可以补（标题/产物目录/目标），状态不在这里动
            for key, value in (("form", form), ("title", title),
                               ("artifacts", artifacts), ("note", note)):
                if value:
                    job[key] = value
            if target:
                job["target"] = dict(target)
            if status != job.get("status"):
                _advance(job, status, note=note)
        job["updated_at"] = now
        job["rev"] = _next_rev(jobs)
        _save(path, jobs)
        return dict(job)


def _advance(job: Dict[str, Any], status: str, *, note: str = "") -> None:
    current = str(job.get("status") or "candidate")
    if status == current:
        return
    allowed = _TRANSITIONS.get(current, set())
    if status not in allowed:
        raise JobError("作业 %s 不能从 %s 流转到 %s（允许：%s）"
                       % (job.get("job_id"), current, status,
                          ", ".join(sorted(allowed)) or "终态"))
    job.setdefault("history", []).append({"from": current, "to": status,
                                          "at": _now(), "note": str(note or "")})
    job["status"] = status


def transition(jid: str, status: str, *, target: Optional[Dict[str, str]] = None,
               note: str = "", root: Optional[Any] = None) -> Dict[str, Any]:
    """推进一条作业的状态（非法流转抛 JobError，不静默吞掉）。"""
    if status not in STATES:
        raise JobError("未知作业状态: %r" % (status,))
    with _LOCK:
        path = jobs_path(root)
        jobs = _load(path)
        job = jobs.get(jid)
        if job is None:
            raise JobError("作业不存在: %s" % jid)
        _advance(job, status, note=note)
        if target:
            job["target"] = dict(target)
        job["updated_at"] = _now()
        job["rev"] = _next_rev(jobs)
        _save(path, jobs)
        if status in _PURGE_STATUSES:
            _purge_raw_copy(job)
        return dict(job)


def get_job(jid: str, *, root: Optional[Any] = None) -> Optional[Dict[str, Any]]:
    with _LOCK:
        job = _load(jobs_path(root)).get(str(jid))
    return dict(job) if job else None


def list_jobs(*, source: Optional[str] = None, status: Optional[str] = None,
              limit: int = 200, root: Optional[Any] = None,
              include_archived: bool = False) -> List[Dict[str, Any]]:
    """按来源/状态过滤，**按更新时间倒序**（队列页默认顺序）。

    【默认不含 archived】归档的语义就是"从作业队列里消失、证据留在台账里"。
    老实现不按状态过滤 —— 于是"归档"了也照样出现在队列里（等于没归档），
    所以这里把过滤放在**读取侧**：默认 False，需要看历史时显式 include_archived=True。
    """
    with _LOCK:
        jobs = list(_load(jobs_path(root)).values())
    if not include_archived:
        jobs = [j for j in jobs if str(j.get("status")) != "archived"]
    if source:
        jobs = [j for j in jobs if j.get("source") == source]
    if status:
        jobs = [j for j in jobs if j.get("status") == status]
    jobs.sort(key=lambda j: (str(j.get("updated_at") or ""), int(j.get("rev") or 0)),
              reverse=True)
    return jobs[: max(0, int(limit))]


def summary(*, root: Optional[Any] = None) -> Dict[str, Any]:
    """队列页头部用：按状态、按来源的计数（不返回条目正文）。"""
    with _LOCK:
        jobs = list(_load(jobs_path(root)).values())
    by_status: Dict[str, int] = {}
    by_source: Dict[str, int] = {}
    for job in jobs:
        by_status[str(job.get("status"))] = by_status.get(str(job.get("status")), 0) + 1
        by_source[str(job.get("source"))] = by_source.get(str(job.get("source")), 0) + 1
    archived = int(by_status.get("archived", 0))
    return {"total": len(jobs), "archived": archived,
            "visible": len(jobs) - archived,
            "by_status": by_status, "by_source": by_source}


def archive(jid: str, *, note: str = "",
            root: Optional[Any] = None) -> Dict[str, Any]:
    """把作业归档（终态）：从队列隐藏、证据保留，可回退到"再彻底删除"。"""
    return transition(jid, "archived", note=note, root=root)


def remove_job(jid: str, *, root: Optional[Any] = None) -> Dict[str, Any]:
    """从台账**彻底删除**一条作业（只允许 archived），返回被删的那条。

    【为什么只允许 archived】"删除"要不可逆地抹掉证据，必须经过归档这一步：
    归档留下"谁在什么时候把它移出队列"的 history，删除只清台账行。
    未归档就删 = 绕过证据链，直接拒绝（比事后追责便宜）。
    """
    with _LOCK:
        path = jobs_path(root)
        jobs = _load(path)
        job = jobs.get(str(jid))
        if job is None:
            raise JobError("作业不存在: %s" % jid)
        if str(job.get("status")) != "archived":
            raise JobError("只能彻底删除**已归档**的作业（当前状态：%s）"
                           % job.get("status"))
        del jobs[str(jid)]
        _save(path, jobs)
        return dict(job)


#: 允许被"彻底删除"清掉的工件根：仓库内 + 系统临时目录（pytest/演示产物的落点）
def _allowed_artifact_roots() -> List[Path]:
    """允许被"彻底删除"清掉的工件根：仓库内 + 各类系统临时目录。

    【为什么不止 tempfile.gettempdir()】本仓的单测把 tempfile.tempdir 重定向到
    仓库内 .pytest_tmp（跨平台可诊断），而 pytest 的 tmp_path 仍可能落在系统 TEMP
    （basetemp 在夹具生效前就已定下）——只认一个来源会让"删自己的测试工件"被误拒。
    生产上这三者本来就指向同一处（系统 TEMP，Windows 上是 C:/Windows/Temp）。
    """
    candidates = [Path(".").resolve(), Path(__file__).resolve().parents[3] / ".pytest_tmp"]
    for env in ("TEMP", "TMP"):
        raw = os.environ.get(env)
        if raw:
            candidates.append(Path(raw))
    try:
        candidates.append(Path(tempfile.gettempdir()))
    except Exception:  # noqa: BLE001 取不到临时目录就只认仓库内
        pass
    out: List[Path] = []
    for c in candidates:
        try:
            out.append(c.resolve())
        except OSError:
            continue
    return out


def artifact_targets(job: Dict[str, Any]) -> List[Dict[str, Any]]:
    """列出这条作业的工件目录（**只报告，不删**）：给删除前的预览用。

    每条给出 {path, exists, allowed, files}。allowed=False 的**不会**被删除
    （例如指向用户主目录/网络盘的历史路径），只如实列出来让人知道。
    """
    raw = str(job.get("artifacts") or "").strip()
    if not raw:
        return []
    p = Path(raw)
    try:
        resolved = p.resolve()
    except OSError:
        resolved = p
    allowed = any(
        str(resolved).lower().startswith(str(r).lower() + os.sep) for r in _allowed_artifact_roots()
    )
    files: List[str] = []
    exists = resolved.is_dir()
    if exists:
        try:
            files = sorted(str(f.relative_to(resolved)) for f in resolved.rglob("*") if f.is_file())[:200]
        except OSError:
            files = []
    return [{"path": str(resolved), "exists": exists, "allowed": bool(allowed),
             "files": files}]


def purge_artifacts(job: Dict[str, Any]) -> Dict[str, List[str]]:
    """删掉作业的工件目录（**仅 allowed 的那些**）；返回 {deleted, skipped}。"""
    deleted: List[str] = []
    skipped: List[str] = []
    for item in artifact_targets(job):
        if not item["exists"]:
            continue
        if not item["allowed"]:
            skipped.append(item["path"])
            continue
        try:
            shutil.rmtree(item["path"])
            deleted.append(item["path"])
        except OSError as exc:  # noqa: BLE001 删不掉如实报告，不假装成功
            logger.warning("[transforms.jobs] 工件目录删除失败 %s: %s", item["path"], exc)
            skipped.append(item["path"])
    return {"deleted": deleted, "skipped": skipped}


__all__ = [
    "JOBS_REL",
    "STATES",
    "JobError",
    "archive",
    "artifact_targets",
    "get_job",
    "job_id",
    "purge_artifacts",
    "remove_job",
    "jobs_path",
    "list_jobs",
    "summary",
    "transition",
    "upsert_job",
]
