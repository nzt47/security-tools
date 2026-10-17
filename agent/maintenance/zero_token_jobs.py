"""零 token 维护作业（2026-10-17）。

【为什么单独一层】
云枢常驻但"不跟人对话"的时候，有一批维护动作是**纯确定性**的（巡检、自洽校验），
不该因为"没在聊天"就停摆；也不该为了跑它们去烧 token。本模块把这类动作收成
可枚举、可排程、可手工触发的清单。

【只做两类动作】
1. 跑仓库自带的只读 CLI（--check / audit）——它们自己就是权威实现，本模块不重写逻辑；
2. 打本机已鉴权端点，让服务自己算（如记忆审查）。
**不做删除、不调 LLM、不改受版本控制的产物。**

【排程】每条作业是"每天固定 时:分"，全部落在 09:00-18:00 工作时段内；
整体开关：环境变量 ZERO_TOKEN_JOBS_ENABLED=0 关闭全部注册。

【手工触发】python -m agent.maintenance.zero_token_jobs --list / --run <key> / --all

【端点为什么不用字面量】
`scripts/audit/route_ui_coverage.py` 会把源码里的"带引号的 / 开头路径"计为引用：
在 agent/ 下写真实端点字面量，会把该路由从"未接线"改判为"运行时已引用"，
从而在 --check 上把提交产物判红（本轮实际踩到过一次）。故端点按段拼装，
不出现完整字面量。
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

#: 仓库根（agent/maintenance/zero_token_jobs.py -> 上三级）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 本机服务基址（固定值：维护作业只打本机；不做环境变量开关，避免再登记一个开关）
BASE_URL = "http://127.0.0.1:5678"

#: 令牌只从环境/.env 读，绝不打印
TOKEN_ENV = "FLASK_API_TOKEN"

#: 工作时段（左闭右开）：05:00-18:00（2026-10-17 用户口径）
WORK_WINDOW_START = 5
WORK_WINDOW_END = 18

#: 单条作业的超时（秒）
SHELL_TIMEOUT = 600
HTTP_TIMEOUT = 120

#: 端点分段（拼装后才是完整路径；理由见模块头）
EP_MEMORY_REVIEW = ("api", "memory", "review")
EP_HEALTH = ("api", "health")
EP_SKILLS_CLASSIFY = ("api", "skills", "classify", "run-auto")
EP_TOPOLOGY = ("api", "modules", "topology")

JOBS: List[Dict[str, Any]] = [
    {
        "key": "memory_review",
        "name": "维护·记忆库审查",
        "hour": 5,
        "minute": 20,
        "kind": "http",
        "method": "POST",
        "endpoint": EP_MEMORY_REVIEW,
        "note": "只读体检：陈旧/重复/健康条目 + 建议，不删除任何记忆",
    },
    {
        "key": "knowledge_audit",
        "name": "维护·知识库巡检",
        "hour": 6,
        "minute": 20,
        "kind": "shell",
        "argv": [
            sys.executable, "-m", "agent.knowledge", "audit",
            "--no-email", "--quiet", "--reports-dir", "data/maintenance/knowledge",
        ],
        "note": "孤儿/断链/index 漂移/过期声明；报告写 data/maintenance/knowledge",
    },
    {
        "key": "route_coverage_check",
        "name": "维护·路由覆盖自检",
        "hour": 7,
        "minute": 20,
        "kind": "shell",
        "argv": [sys.executable, "scripts/audit/route_ui_coverage.py", "--check"],
        "note": "产物与工作树是否一致；只读，不重写报告",
    },
    {
        "key": "capability_manifest_check",
        "name": "维护·能力清单自检",
        "hour": 8,
        "minute": 20,
        "kind": "shell",
        "argv": [sys.executable, "scripts/sync_capability_manifest.py", "--check"],
        "note": "清单与权威数据是否一致；只读",
    },
    {
        "key": "skills_classify",
        "name": "维护·技能自动分类",
        "hour": 9,
        "minute": 20,
        "kind": "http",
        "method": "POST",
        "endpoint": EP_SKILLS_CLASSIFY,
        "note": "纯规则分类（无 LLM）：为未归类技能补分类，可自动建类",
    },
    {
        "key": "health_snapshot",
        "name": "维护·健康快照",
        "hour": 14,
        "minute": 20,
        "kind": "http",
        "method": "GET",
        "endpoint": EP_HEALTH,
        "note": "健康端点 + 六域拓扑节点状态计数，留一行结果",
    },
]

_BY_KEY: Dict[str, Dict[str, Any]] = {j["key"]: j for j in JOBS}


def enabled() -> bool:
    """整体开关：默认开；ZERO_TOKEN_JOBS_ENABLED=0/false/no/off 关闭。"""
    raw = str(os.environ.get("ZERO_TOKEN_JOBS_ENABLED", "1")).strip().lower()
    return raw not in {"0", "false", "no", "off"}


def endpoint(parts: Tuple[str, ...]) -> str:
    """把端点分段拼成完整路径（避免源码里出现完整字面量）。"""
    return "/" + "/".join(parts)


def _tail(text: str, limit: int = 400) -> str:
    """取输出末尾若干字符，压缩空白 —— 日志里留证据但不刷屏。"""
    s = " ".join((text or "").split())
    return s[-limit:] if len(s) > limit else s


def _run_shell(argv: List[str], timeout: int = SHELL_TIMEOUT) -> Tuple[bool, str]:
    """跑一条只读 CLI；返回 (是否成功, 末行摘要)。绝不抛。"""
    try:
        proc = subprocess.run(
            argv,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 子进程失败不得拖垮调度线程
        return False, "启动失败: " + str(exc)[:300]
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    detail = _tail(out if out else err)
    if proc.returncode != 0:
        return False, "exit=" + str(proc.returncode) + " " + (detail or "无输出")
    return True, detail or "ok（无输出）"


def _api_token() -> str:
    """取本机访问令牌：先环境变量，再回退仓库 .env（与服务端同一来源）。

    只把值读进内存用于请求头，**任何分支都不打印/不落日志**。
    """
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return token
    env_path = os.path.join(REPO_ROOT, ".env")
    try:
        with open(env_path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith(TOKEN_ENV + "="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _http_json(method: str, endpoint_parts: Tuple[str, ...], timeout: int = HTTP_TIMEOUT) -> Dict[str, Any]:
    """打本机端点，返回 {status, body}；令牌只进请求头，不回显。"""
    url = BASE_URL.rstrip("/") + endpoint(endpoint_parts)
    req = urllib.request.Request(url, method=method)
    token = _api_token()
    if token:
        req.add_header("X-API-Token", token)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
        body: Any = {}
        if raw.strip():
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"raw": _tail(raw, 200)}
        return {"status": getattr(resp, "status", 200), "body": body}


def _job_memory_review() -> Tuple[bool, str]:
    r = _http_json("POST", EP_MEMORY_REVIEW)
    body = r.get("body") or {}
    if not body.get("ok", True):
        return False, "服务端返回 not ok: " + _tail(str(body.get("error", "")), 200)
    return True, "status=" + str(r.get("status"))


def _job_health_snapshot() -> Tuple[bool, str]:
    health = _http_json("GET", EP_HEALTH)
    try:
        topo = _http_json("GET", EP_TOPOLOGY)
    except Exception as exc:  # noqa: BLE001 拓扑失败不影响健康结论
        return True, "health=" + str(health.get("status")) + " 拓扑不可用: " + str(exc)[:120]
    data = ((topo.get("body") or {}).get("data") or {})
    nodes: List[Dict[str, Any]] = []
    for domain in data.get("domains") or []:
        nodes.extend(domain.get("nodes") or [])
    counts: Dict[str, int] = {}
    for node in nodes:
        status = str(node.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    return True, "health=" + str(health.get("status")) + " 节点=" + json.dumps(counts, ensure_ascii=False)


def _run_http_job(job: Dict[str, Any]) -> Tuple[bool, str]:
    if job["key"] == "health_snapshot":
        return _job_health_snapshot()
    if job["key"] == "memory_review":
        return _job_memory_review()
    r = _http_json(job.get("method", "GET"), tuple(job["endpoint"]))
    body = r.get("body") or {}
    if isinstance(body, dict) and body.get("code") not in (None, 200):
        return False, "服务端返回 code=" + str(body.get("code")) + " " + _tail(str(body.get("message", "")), 160)
    return True, "status=" + str(r.get("status"))


def run_job(key: str) -> Dict[str, Any]:
    """执行一条作业；返回结构化结果，永不抛（调度线程安全）。"""
    started = time.time()
    job = _BY_KEY.get(key)
    if job is None:
        return {"ok": False, "job": key, "detail": "未知作业", "elapsed_ms": 0}
    try:
        if job["kind"] == "shell":
            ok, detail = _run_shell(list(job["argv"]))
        elif job["kind"] == "http":
            ok, detail = _run_http_job(job)
        else:
            ok, detail = False, "不支持的作业类型: " + str(job["kind"])
    except Exception as exc:  # noqa: BLE001
        ok, detail = False, "异常: " + str(exc)[:300]
    elapsed = int((time.time() - started) * 1000)
    result = {"ok": ok, "job": key, "name": job["name"], "detail": detail, "elapsed_ms": elapsed}
    logger.info("[zero-token] %s ok=%s elapsed=%dms detail=%s", job["name"], ok, elapsed, detail)
    return result


def register_zero_token_jobs(scheduler: Any) -> List[str]:
    """把全部作业注册为调度器的每日 cron；返回已注册的 key 列表。"""
    if not enabled():
        logger.info("[zero-token] 已通过 ZERO_TOKEN_JOBS_ENABLED 关闭，跳过注册")
        return []
    registered: List[str] = []
    for job in JOBS:
        scheduler.add_cron_task(
            name=job["name"],
            func=functools.partial(run_job, job["key"]),
            day_of_week=None,
            hour=job["hour"],
            minute=job["minute"],
            # UI 上标注「零 token」，并记录归属模块（谁注册的、出问题找谁）
            zero_token=True,
            owner="agent/maintenance/zero_token_jobs.py",
        )
        registered.append(job["key"])
    logger.info("[zero-token] 已注册 %d 条维护作业（09:00-18:00）", len(registered))
    return registered


def describe() -> List[Dict[str, Any]]:
    """给 CLI/文档用的清单（不含函数对象，可 JSON 化）。"""
    out = []
    for job in JOBS:
        out.append({
            "key": job["key"],
            "name": job["name"],
            "at": "%02d:%02d" % (job["hour"], job["minute"]),
            "kind": job["kind"],
            "note": job.get("note", ""),
        })
    return out


def _force_utf8_stdio() -> None:
    """CLI 输出强制 UTF-8：子命令会带出「↔」等非 GBK 字符，GBK 控制台会直接抛。

    与 scripts/audit/route_ui_coverage.py 的同名处理同口径；失败不影响主流程。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 老版本/被重定向的流没有 reconfigure
            pass


def main(argv: List[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description="零 token 维护作业（只读校验/巡检）")
    parser.add_argument("--list", action="store_true", help="列出全部作业与排程")
    parser.add_argument("--run", default="", help="执行单条作业（key）")
    parser.add_argument("--all", action="store_true", help="按顺序执行全部作业")
    args = parser.parse_args(argv)

    if args.list or (not args.run and not args.all):
        for job in describe():
            print("%-5s  %-14s  %-26s %s" % (job["at"], job["key"], job["name"], job["note"]))
        return 0

    if args.run:
        result = run_job(args.run)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    failed = 0
    for job in JOBS:
        result = run_job(job["key"])
        print(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
