# -*- coding: utf-8 -*-
"""交接快照：把「交接文档里那些手写数字」变成**跑出来的**一份状态。

【为什么需要】云枢的交接文档已经 3,500+ 行，每次换会话都要先读 §11.4x 的七八个小节，
其中的版本号/条数/告警数大多是**人手抄**的：写的时候对，几小时后就漂（本轮实测：
审计条数 82,348 → 82,408、调度 15 → 16）。DeepSeek Harness 的做法相反 —— 会话状态由
session log 与插件在运行时**派生**，文档只描述契约与意图。

【产出】
- data/handoff_state.json：机器可读快照（版本/计数/看板/告警/未完成项）
- stdout：一段 Markdown「交接卡」，直接贴到交接文档最前面

【口径】只读：不重启服务、不改任何文件（除了写快照 JSON）；令牌只进请求头，不打印。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "handoff_state.json"
TOKEN_ENV = "FLASK_API_TOKEN"
BASE_URL = os.environ.get("YUNSHU_BASE_URL", "http://127.0.0.1:5678")


def _token() -> str:
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    try:
        text = (ROOT / ".env").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    for line in text.splitlines():
        if line.startswith(TOKEN_ENV + "="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _get(path: str) -> Optional[Dict[str, Any]]:
    req = urllib.request.Request(BASE_URL.rstrip("/") + path)
    token = _token()
    if token:
        req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 服务没起也要能出快照
        return None


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=20)
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _skills() -> Dict[str, int]:
    repo = ROOT / "data" / "skills_repo"
    ids: List[Path] = []
    if repo.is_dir():
        ids = [p for p in repo.iterdir()
               if p.is_dir() and not p.name.startswith(".") and (p / "skill.md").is_file()]
    cards = sum(1 for p in ids if (p / "CARD.md").is_file())
    return {"file_track": len(ids), "cards": cards}


def _scheduler() -> Dict[str, Any]:
    data = _get("/api/scheduler/overview")
    tasks = ((data or {}).get("data") or {}).get("tasks") or []
    return {"total": len(tasks), "enabled": sum(1 for t in tasks if t.get("enabled"))}


def _units() -> Dict[str, Any]:
    data = _get("/api/system/units")
    if not data:
        return {}
    summary = data.get("summary") or {}
    return {"ok": summary.get("ok"), "warn": summary.get("warn"),
            "never": summary.get("never"), "score": data.get("health_score")}


def _tools() -> Dict[str, Any]:
    data = _get("/api/tools/config")
    rows = ((data or {}).get("data") or [])
    if isinstance(rows, dict):
        rows = rows.get("tools") or []
    disabled = sorted(r.get("name") for r in rows if r.get("enabled") is False)
    return {"total": len(rows), "disabled": disabled}


def _version() -> Dict[str, Any]:
    data = _get("/api/version")
    if not data:
        return {}
    d = data.get("data") or {}
    backend = d.get("backend") or {}
    frontend = d.get("frontend") or {}
    proc = d.get("process") or {}
    return {
        "backend": str(backend.get("commit") or "")[:9],
        "frontend": str(frontend.get("build_commit") or "")[:9],
        "bundle": frontend.get("bundle") or "",
        "pid": proc.get("pid"),
    }


def _audit_rows() -> Optional[int]:
    units = _get("/api/system/units")
    if not units:
        return None
    for unit in units.get("units") or []:
        if unit.get("id") == "runtime.audit":
            return (unit.get("metrics") or {}).get("rows")
    return None


#: 【⑤ 自检进快照】四个常跑的仓库自检脚本：(键, 相对路径)。
#: 为什么进快照：交接文档里这些数字此前都是手抄的（审计 82,348 → 82,408 就是实例），
#: 而它们本来就有脚本、几秒出结果 —— 让卡片直接带出来，人就不用"抄 + 核"。
_SELF_CHECKS = (
    ("dual_track", "scripts/check_skills_dual_track.py"),
    ("skill_md", "scripts/validate_skill_md.py"),
    ("audit_chain", "scripts/verify_audit_chain.py"),
    ("layer_coverage", "scripts/audit/skill_layer_coverage.py"),
)


def _self_check(key: str, rel: str) -> Dict[str, Any]:
    """跑一个自检脚本并把**关键数字**抽出来（脚本本身不改任何文件）。

    【为什么强制子进程说 UTF-8】子进程的 stdout 是**管道**，Windows 上 Python 据此退回
    本地码页（简中 = cp936）输出，而父进程按 utf-8 解码 ⇒ 中文全成替换符、正则一条也
    匹配不上，卡片就只能写「自检：（未跑）」——2026-11-07 交接卡的实际症状。
    显式给子进程 PYTHONIOENCODING=utf-8，两端编码才一致。
    """
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run([sys.executable, str(ROOT / rel)], cwd=ROOT,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=180, env=env)
    except Exception as exc:  # noqa: BLE001 自检失败不能让快照整体失败
        return {"ok": False, "error": type(exc).__name__}
    out = (proc.stdout or "") + (proc.stderr or "")
    data: Dict[str, Any] = {"ok": proc.returncode == 0}
    if key == "dual_track":
        m = re.search(r"文件轨\s*(\d+)\s*条\s*/\s*主轨\s*(\d+)\s*条", out)
        if m:
            data["file_track"], data["main_track"] = int(m.group(1)), int(m.group(2))
        data["orphans_ok"] = ("无孤儿" in out)
    elif key == "skill_md":
        m = re.search(r"(\d+)\s*份\s*·\s*error\s*(\d+)\s*·\s*带 warning\s*(\d+)", out)
        if m:
            data["files"], data["errors"], data["warnings"] = (
                int(m.group(1)), int(m.group(2)), int(m.group(3)))
    elif key == "audit_chain":
        m = re.search(r"已校验\s*(\d+)\s*条", out)
        if m:
            data["rows"] = int(m.group(1))
        data["breaks"] = 0 if ("0 断点" in out or "链完整" in out) else -1
    elif key == "layer_coverage":
        m = re.search(r"有步骤[^（]*（(\d+(?:\.\d+)?)%）.*?有触发面[^（]*（(\d+(?:\.\d+)?)%）",
                      out, re.S)
        if m:
            data["steps_pct"], data["trigger_pct"] = float(m.group(1)), float(m.group(2))
        m2 = re.search(r"平均省\s*(\d+(?:\.\d+)?)%", out)
        if m2:
            data["saved_pct"] = float(m2.group(1))
    return data


def _self_checks() -> Dict[str, Any]:
    return {key: _self_check(key, rel) for key, rel in _SELF_CHECKS}


#: 下一步清单（机器可读）：文档只描述判据，具体条目放这里，卡片直接读。
NEXT_PATH = ROOT / "data" / "handoff_next.json"


def _next_actions() -> Dict[str, Any]:
    try:
        doc = json.loads(NEXT_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = doc.get("items") if isinstance(doc, dict) else None
    if not isinstance(items, list):
        return {}
    open_items = [i for i in items if isinstance(i, dict)
                  and str(i.get("status") or "open") == "open"]
    return {"total": len(items), "open": open_items}


def collect() -> Dict[str, Any]:
    head = _git("rev-parse", "--short", "HEAD")
    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    dirty = _git("status", "--porcelain", "--untracked-files=no")
    upstream = _git("rev-parse", "--short", "origin/master")
    return {
        "generated_at": datetime.now().astimezone().replace(microsecond=0).isoformat(),
        "repo": {"branch": branch, "head": head, "origin_master": upstream,
                 "tracked_dirty_lines": len([x for x in dirty.splitlines() if x.strip()])},
        "service": _version(),
        "skills": _skills(),
        "scheduler": _scheduler(),
        "units": _units(),
        "tools": _tools(),
        "audit_rows": _audit_rows(),
        "selfcheck": _self_checks(),
        "next": _next_actions(),
    }


def _render_selfcheck(sc: Dict[str, Any]) -> str:
    """把四项自检压成一行（失败只标注，不让卡片变噪音）。"""
    parts: List[str] = []
    dt = sc.get("dual_track") or {}
    if dt.get("file_track") is not None:
        parts.append("双轨 %s/%s%s" % (dt.get("file_track"), dt.get("main_track"),
                                       "" if dt.get("orphans_ok") else " ⚠孤儿"))
    sm = sc.get("skill_md") or {}
    if sm.get("files") is not None:
        parts.append("skill.md %s 份 %s error / %s warning" % (
            sm.get("files"), sm.get("errors"), sm.get("warnings")))
    ac = sc.get("audit_chain") or {}
    if ac.get("rows") is not None:
        parts.append("审计 %s 条 / %s 断点" % (
            ac.get("rows"), 0 if ac.get("breaks") == 0 else "**有**"))
    lc = sc.get("layer_coverage") or {}
    if lc.get("steps_pct") is not None:
        parts.append("分层 步骤 %g%% / 触发 %g%%（省 %g%%）" % (
            lc.get("steps_pct"), lc.get("trigger_pct") or 0, lc.get("saved_pct") or 0))
    bad = [k for k, v in sc.items() if isinstance(v, dict) and not v.get("ok", True)]
    if bad:
        parts.append("⚠ 未通过: " + "、".join(bad))
    return "；".join(parts) or "（未跑）"


def _render_next(nxt: Dict[str, Any]) -> str:
    open_items = nxt.get("open") or []
    if not open_items:
        return "（无）"
    # 卡片上只列 id（一眼能对上 data/handoff_next.json），标题留给 JSON
    head = [str(i.get("id") or i.get("title") or "") for i in open_items[:5]]
    more = len(open_items) - len(head)
    return "未完成 %d 项：%s%s" % (len(nxt.get("items") or open_items),
                                 "；".join(head), "；…" if more > 0 else "")


def render_card(state: Dict[str, Any]) -> str:
    svc = state.get("service") or {}
    skills = state.get("skills") or {}
    sched = state.get("scheduler") or {}
    units = state.get("units") or {}
    tools = state.get("tools") or {}
    repo = state.get("repo") or {}
    same = bool(svc.get("backend")) and svc.get("backend") == svc.get("frontend")
    lines = [
        "<!-- 由 python scripts/handoff_snapshot.py 生成，勿手改 -->",
        "**交接卡（%s）**" % state.get("generated_at", ""),
        "",
        "- 仓库：%s · HEAD %s · origin/master %s · tracked 改动 %d 行" % (
            repo.get("branch"), repo.get("head"), repo.get("origin_master"),
            repo.get("tracked_dirty_lines") or 0),
        "- 服务：%s / %s（%s）· bundle %s · pid %s" % (
            svc.get("backend") or "（未起）", svc.get("frontend") or "-",
            "三处一致" if same else "**不一致**", svc.get("bundle") or "-", svc.get("pid") or "-"),
        "- 技能：文件轨 %s · 能力卡 %s" % (skills.get("file_track", "-"), skills.get("cards", "-")),
        "- 调度：%s 个 / 启用 %s" % (sched.get("total", "-"), sched.get("enabled", "-")),
        "- 工具：%s 条 · 停用 %s" % (tools.get("total", "-"),
                                    "、".join(tools.get("disabled") or []) or "（无）"),
        "- 单位看板：%s ok / %s warn / %s never · %s" % (
            units.get("ok", "-"), units.get("warn", "-"), units.get("never", "-"),
            units.get("score", "-")),
        "- 审计：%s 条" % (state.get("audit_rows") if state.get("audit_rows") is not None else "-"),
        "- 自检：%s" % _render_selfcheck(state.get("selfcheck") or {}),
        "- 下一步：%s" % _render_next(state.get("next") or {}),
        "",
        "> 口径与未完成项见 docs/handoff/口径.md 与 data/handoff_next.json；数字以本卡为准（脚本可重跑）。",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    argv = argv or sys.argv[1:]
    state = collect()
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    card = render_card(state)
    if "--json" in argv:
        print(json.dumps(state, ensure_ascii=False, indent=2))
    else:
        print(card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())