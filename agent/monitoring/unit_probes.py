"""功能单位探测 —— 把云枢各能力面的「工作状况」读成一个统一的快照。

每个 probe 只读已有数据（文件 / SQLite / 单例），不做任何写操作、不发网络请求，
返回 unit_monitor 约定的形状：{status, summary, metrics, issues, last_activity}。

【为什么是文件而不是指标】2026-10-28 实测：业务指标只在进程内存（重启归零），
而「上次压缩是几号」「长期记忆几行」「调度器有没有跑过」这类问题必须看落盘状态。
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .unit_monitor import Unit, register

_ROOT = Path(__file__).resolve().parent.parent.parent
_PROCESS_START = time.time()


def _p(rel: str) -> Path:
    return _ROOT / rel


def _read_json(rel: str) -> Any:
    try:
        return json.loads(_p(rel).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 缺文件/坏 JSON 一律当空
        return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _mtime(path: Path) -> Optional[str]:
    try:
        return _iso(path.stat().st_mtime)
    except OSError:
        return None


#: 只读连接等待上限（秒）：探测是旁路，库被写连接占住时宁可记「未知」也不排队。
_SQLITE_TIMEOUT_SEC = 1.0


def _sqlite_rows(rel: str, table: str) -> Optional[int]:
    path = _p(rel)
    if not path.exists():
        return None
    try:
        con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=_SQLITE_TIMEOUT_SEC)
        try:
            return int(con.execute("select count(*) from %s" % table).fetchone()[0])
        finally:
            con.close()
    except Exception:  # noqa: BLE001 库被占用/表不存在都按未知
        return None


def _skill_registry() -> Dict[str, Any]:
    doc = _read_json("data/skills_mgmt.json") or {}
    skills = doc.get("skills") if isinstance(doc, dict) else None
    if not isinstance(skills, dict):
        skills = doc if isinstance(doc, dict) else {}
    return skills


# ── 运行时 ────────────────────────────────────────────────
def probe_service() -> Dict[str, Any]:
    uptime = time.time() - _PROCESS_START
    return {
        "status": "ok",
        "summary": "pid %d · 已运行 %.1f 小时" % (os.getpid(), uptime / 3600.0),
        "metrics": {"pid": os.getpid(), "uptime_s": round(uptime, 1)},
        "last_activity": datetime.now(timezone.utc).isoformat(),
    }


def probe_audit_chain() -> Dict[str, Any]:
    rows = _sqlite_rows("data/audit/audit_chain.db", "audit_chain")
    last = None
    path = _p("data/audit/audit_chain.db")
    if path.exists():
        try:
            con = sqlite3.connect("file:%s?mode=ro" % path.as_posix(), uri=True, timeout=_SQLITE_TIMEOUT_SEC)
            try:
                last = con.execute("select max(seq) from audit_chain").fetchone()[0]
            finally:
                con.close()
        except Exception:  # noqa: BLE001
            last = None
    if rows is None:
        return {"status": "error", "summary": "审计库不可读", "issues": ["data/audit/audit_chain.db 打不开"]}
    return {"status": "ok", "summary": "%d 条 · 最新 seq=%s" % (rows, last),
            "metrics": {"rows": rows, "last_seq": last}, "last_activity": _mtime(path)}


# ── 技能 ──────────────────────────────────────────────────
def probe_skills_registry() -> Dict[str, Any]:
    skills = _skill_registry()
    total = len(skills)
    enabled = sum(1 for s in skills.values() if isinstance(s, dict) and s.get("enabled", True))
    published = sum(1 for s in skills.values()
                    if isinstance(s, dict) and s.get("status") in ("published", "approved"))
    repo = _p("data/skills_repo")
    file_track = 0
    if repo.exists():
        for d in repo.iterdir():
            if d.is_dir() and not d.name.startswith(".") and (d / "skill.md").exists():
                file_track += 1
    issues: List[str] = []
    status = "ok" if total else "never"
    if total and file_track < total - 2:
        issues.append("主轨 %d 个技能只有 %d 个有文件轨（skill.md）" % (total, file_track))
    return {"status": status, "summary": "%d 个技能 · 启用 %d · 文件轨 %d" % (total, enabled, file_track),
            "metrics": {"total": total, "enabled": enabled, "published_or_approved": published,
                        "file_track": file_track},
            "issues": issues, "last_activity": _mtime(_p("data/skills_mgmt.json"))}


#: 「0 命中」告警的最小样本量：match 次数太少时，0 命中是噪声而不是缺陷
#: （2026-10-29 实测：1 次 miss 就让「43 个启用技能 0 命中」变 warn）。
#: 放模块常量而不是调用点字面量 —— 硬编码边界门禁只认字面量。
USAGE_WARN_MIN_MATCH_CALLS = 20
#: 启用技能里「近 7 天 0 命中」的占比阈值，超过才 warn（少量冷门技能不命中是常态）。
USAGE_WARN_ZERO_HIT_RATIO = 0.8
#: 台账聚合窗口（天）：技能与工具两段共用同一窗口，避免「技能看 7 天、工具看 30 天」两套口径。
USAGE_WINDOW_DAYS = 7


def probe_skills_usage() -> Dict[str, Any]:
    try:
        from agent.skills_mgmt import usage_log
        s = usage_log.stats(USAGE_WINDOW_DAYS)
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "summary": "台账读取失败: %s" % exc}
    tot = s["totals"]
    match_calls = int(tot["match_calls"])
    skills = _skill_registry()
    enabled_ids = {sid for sid, row in skills.items()
                   if isinstance(row, dict) and row.get("enabled", True)}
    hit_ids = {sid for sid, row in s["skills"].items() if row.get("hits")}
    zero_hit = sorted(enabled_ids - hit_ids)
    enabled_total = len(enabled_ids)
    zero_ratio = (len(zero_hit) / enabled_total) if enabled_total else 0.0
    sample_ok = match_calls >= USAGE_WARN_MIN_MATCH_CALLS
    issues: List[str] = []
    if match_calls == 0:
        status = "never"
        issues.append("近 7 天没有任何命中记录（语义层没跑过或台账未接上）")
    elif not sample_ok:
        # 小样本下「0 命中」是噪声：1 次 miss 不能证明 43 个技能都没用（2026-10-29 实测误报）。
        status = "ok"
    elif zero_ratio > USAGE_WARN_ZERO_HIT_RATIO:
        status = "warn"
        issues.append("启用技能中 %d/%d（%.0f%%）近 7 天 0 命中（例：%s）"
                      % (len(zero_hit), enabled_total, zero_ratio * 100,
                         "、".join(zero_hit[:3])))
    else:
        status = "ok"
    summary = "近 7 天：命中 %d 次（命中率 %.0f%%）· 加载 %d · 执行 %d 成功/%d 失败" % (
        tot["hit_calls"], tot["hit_rate"] * 100, tot["load_calls"],
        tot["exec_ok"], tot["exec_fail"])
    if 0 < match_calls < USAGE_WARN_MIN_MATCH_CALLS:
        summary += " · 样本不足（match %d<%d），暂不判定 0 命中" % (
            match_calls, USAGE_WARN_MIN_MATCH_CALLS)
    return {"status": status, "summary": summary,
            "metrics": dict(tot, zero_hit_enabled=len(zero_hit),
                            zero_hit_ratio=round(zero_ratio, 3),
                            sample_sufficient=sample_ok,
                            zero_hit_sample=zero_hit[:10]),
            "issues": issues, "last_activity": _mtime(usage_log.usage_path())}


# ── 工具 / 工作流 ─────────────────────────────────────────
def probe_tools() -> Dict[str, Any]:
    defs = _p("data/tool_definitions")
    static = len(list(defs.glob("*.yaml"))) if defs.exists() else 0
    dyn_doc = _read_json("data/dynamic_tools.json") or {}
    dyn = dyn_doc.get("tools") if isinstance(dyn_doc, dict) else None
    dynamic = len(dyn) if isinstance(dyn, (dict, list)) else 0
    status = "ok" if static else "never"
    # 工具使用（2026-10-29）：与技能台账同源，stage=tool；读失败只降级 summary，不改状态。
    calls = fails = 0
    try:
        from agent.skills_mgmt import usage_log
        t = usage_log.stats(USAGE_WINDOW_DAYS)["totals"]
        calls, fails = int(t.get("tool_calls", 0)), int(t.get("tool_fail", 0))
    except Exception:  # noqa: BLE001 台账读不到不影响「登记了多少工具」这个事实
        calls = fails = 0
    usage_note = (" · 近 7 天调用 %d 次（失败 %d）" % (calls, fails)) if calls else \
        " · 近 7 天无工具调用记录"
    return {"status": status,
            "summary": "静态 %d · 动态登记 %d%s" % (static, dynamic, usage_note),
            "metrics": {"static": static, "dynamic": dynamic,
                        "calls_7d": calls, "fails_7d": fails},
            "last_activity": _mtime(_p("data/dynamic_tools.json"))}


def probe_workflows() -> Dict[str, Any]:
    doc = _read_json("data/learned_workflows.json")
    items = doc.get("workflows") if isinstance(doc, dict) else doc
    if isinstance(items, dict):
        items = list(items.values())
    items = items if isinstance(items, list) else []
    enabled = sum(1 for w in items if isinstance(w, dict) and w.get("enabled", True))
    return {"status": "ok" if items else "never",
            "summary": "%d 条工作流（启用 %d）" % (len(items), enabled),
            "metrics": {"total": len(items), "enabled": enabled},
            "last_activity": _mtime(_p("data/learned_workflows.json"))}


def probe_scheduler() -> Dict[str, Any]:
    try:
        from agent.task_scheduler import get_scheduler
        sched = get_scheduler()
        tasks = sched.list_tasks() if sched is not None else []
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "summary": "调度器不可用: %s" % exc}
    enabled = [t for t in tasks if t.get("enabled", True)]
    never = [t for t in enabled if not t.get("last_run")]
    issues: List[str] = []
    status = "ok"
    if not enabled:
        status = "off"
        issues.append("没有任何启用的调度任务")
    return {"status": status,
            "summary": "%d 个任务 · 启用 %d · 从未运行 %d" % (len(tasks), len(enabled), len(never)),
            "metrics": {"total": len(tasks), "enabled": len(enabled), "never_run": len(never),
                        "names": [str(t.get("name")) for t in enabled[:8]]},
            "issues": issues, "last_activity": None}


# ── 记忆 ──────────────────────────────────────────────────
def probe_memory_compress() -> Dict[str, Any]:
    summary_path = _p("data/summary.txt")
    if not summary_path.exists():
        return {"status": "never", "summary": "还没有任何摘要",
                "issues": ["data/summary.txt 不存在（压缩从未成功落盘）"]}
    size = summary_path.stat().st_size
    version = ""
    try:
        version = _p("data/summary_version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        version = ""
    if size == 0:
        return {"status": "warn",
                "summary": "摘要 v%s 是 0 字节（压缩产出为空）" % (version or "?"),
                "metrics": {"bytes": 0, "version": version},
                "issues": ["压缩返回空摘要（推理预算被吃满）；#1336 起空摘要不再落盘"],
                "last_activity": _mtime(summary_path)}
    return {"status": "ok", "summary": "摘要 v%s · %d 字节" % (version or "?", size),
            "metrics": {"bytes": size, "version": version},
            "last_activity": _mtime(summary_path)}


def probe_memory_longterm() -> Dict[str, Any]:
    rows = _sqlite_rows("data/memory/long_term.db", "long_term_memory")
    profile = _sqlite_rows("data/memory/long_term.db", "user_profile")
    if rows is None:
        return {"status": "never", "summary": "长期记忆库不存在",
                "issues": ["data/memory/long_term.db 不可读"]}
    status = "ok" if rows else "warn"
    issues = [] if rows else ["长期记忆 0 行（短期→长期提升链路没有产出）"]
    return {"status": status,
            "summary": "长期记忆 %d 行 · 用户画像 %d 行" % (rows, profile or 0),
            "metrics": {"long_term": rows, "user_profile": profile or 0},
            "issues": issues, "last_activity": _mtime(_p("data/memory/long_term.db"))}


def probe_memory_reflection() -> Dict[str, Any]:
    exp = _read_json("data/reflection/experiences.json")
    les = _read_json("data/reflection/lessons.json")
    exp = exp if isinstance(exp, list) else []
    les = les if isinstance(les, list) else []
    status = "ok" if (exp or les) else "never"
    issues = [] if (exp or les) else ["反思经验/教训均为空（装配侧长期 0 注入）"]
    return {"status": status, "summary": "经验 %d 条 · 教训 %d 条" % (len(exp), len(les)),
            "metrics": {"experiences": len(exp), "lessons": len(les)},
            "issues": issues, "last_activity": _mtime(_p("data/reflection/lessons.json"))}


def probe_vector_store() -> Dict[str, Any]:
    # vec0 是扩展虚拟表：标准库 sqlite3 打不开（no such module: vec0），
    # 用它的普通影子表 meta_agent_memory 计行数（一条向量对应一行元数据）。
    rows = _sqlite_rows("data/memory/agent_memory_vec.db", "meta_agent_memory")
    if rows is None:
        return {"status": "never", "summary": "向量库不可读"}
    return {"status": "ok" if rows else "warn", "summary": "向量 %d 条" % rows,
            "metrics": {"rows": rows},
            "issues": [] if rows else ["向量库 0 条（记忆检索会全程退化）"],
            "last_activity": _mtime(_p("data/memory/agent_memory_vec.db"))}


def probe_ingestion() -> Dict[str, Any]:
    """隔离区待评审草稿数。

    只数**真草稿**（吞噬草稿目录必有 manifest.json）：历史上别的流程也在同一父目录
    落过单文件目录（2026-10-23 的 browser-qa-playbook/skill.md），只数子目录会把
    它算成一条待评审草稿 ⇒ 2026-10-29 实测「探针 3 条 / GET /api/ingestion/drafts 2 条」。
    口径以接口为准，非草稿目录单独计数（不静默丢，summary 里说明）。
    """
    drafts_dir = _p("data/ingestion/drafts")
    dirs = [d for d in drafts_dir.iterdir() if d.is_dir()] if drafts_dir.exists() else []
    real = [d for d in dirs if (d / "manifest.json").is_file()]
    stray = len(dirs) - len(real)
    summary = "隔离区草稿 %d 条" % len(real)
    if stray:
        summary += "（另有 %d 个非草稿目录无 manifest.json，已忽略）" % stray
    return {"status": "ok", "summary": summary,
            "metrics": {"drafts": len(real), "ignored_dirs": stray},
            "last_activity": _mtime(drafts_dir)}


def _all() -> List[Unit]:
    return [
        Unit("runtime.service", "服务进程", "runtime", probe_service, "pid/运行时长"),
        Unit("runtime.audit", "审计链", "runtime", probe_audit_chain, "行数与最新 seq"),
        Unit("skills.registry", "技能资产", "skills", probe_skills_registry, "主轨/文件轨/启用数"),
        Unit("skills.usage", "技能使用（近 7 天）", "skills", probe_skills_usage, "命中/加载/执行"),
        Unit("tools.registry", "工具登记", "tools", probe_tools, "静态 YAML + 动态登记"),
        Unit("workflows.store", "工作流", "workflows", probe_workflows, "已学成的条目"),
        Unit("scheduler.tasks", "调度任务", "scheduler", probe_scheduler, "任务与从未运行数"),
        Unit("memory.compress", "记忆压缩", "memory", probe_memory_compress, "摘要大小/版本"),
        Unit("memory.longterm", "长期记忆", "memory", probe_memory_longterm, "行数"),
        Unit("memory.reflection", "反思经验", "memory", probe_memory_reflection, "经验/教训条数"),
        Unit("memory.vector", "记忆向量", "memory", probe_vector_store, "向量条数"),
        Unit("ingestion.drafts", "吞噬草稿", "ingestion", probe_ingestion, "隔离区待评审数"),
    ]


def register_all() -> int:
    """登记全部内置单位（幂等）。返回登记后总数。"""
    for unit in _all():
        register(unit)
    from .unit_monitor import registered
    return len(registered())