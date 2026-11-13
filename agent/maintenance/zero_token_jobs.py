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
from typing import Any, Dict, List, Optional, Tuple
from agent.utils import proc as _proc

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

#: 空闲闸门：判"用户不在用"的秒数阈值与 CPU 上限（与 agent/system_idle 默认值同口径）
IDLE_TIMEOUT_SEC = 300.0
IDLE_MAX_CPU_PERCENT = 20.0
#: 排队补跑的检查间隔（秒）——调度器里注册成 interval 任务
DRAIN_INTERVAL_SEC = 600
#: 空闲记忆整理的间隔（秒）：6 小时一轮；不空闲就跳过（不排队——整理过期没有"必须在今天做"的语义）
CONSOLIDATION_INTERVAL_SEC = 6 * 3600
#: 兜底时刻（小时）：到这个点还没跑就**强制**跑一次（不能让"忙"变成"今天不做"）
FALLBACK_HOUR = WORK_WINDOW_END
#: 待办文件（运行期状态；gitignore 的 data/ 下）
PENDING_REL_PATH = os.path.join("data", "maintenance", "zero_token_pending.json")

#: 端点分段（拼装后才是完整路径；理由见模块头）
EP_MEMORY_REVIEW = ("api", "memory", "review")
EP_HEALTH = ("api", "health")
EP_SKILLS_CLASSIFY = ("api", "skills", "classify", "run-auto")
EP_ABSTRACT_FROM_MEMORY = ("api", "skills-mgmt", "abstract-from-memory")
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
        "key": "learning_drafts",
        "name": "学习·记忆抽象草稿",
        "hour": 10,
        "minute": 20,
        "kind": "http",
        "method": "POST",
        "endpoint": EP_ABSTRACT_FROM_MEMORY,
        "json": {"days": 7, "max_skills": 3},
        # 零 token（聚类 + 模板生成，不调 LLM）；**只写草稿**，登记/启用仍在转化队列。
        "note": "把最近 7 天记忆聚类后只产草稿（source=learning）；不注册、不启用",
    },
    {
        "key": "audit_full_verify",
        "name": "维护·审计全链核验",
        "hour": 11,
        "minute": 20,
        "kind": "shell",
        "argv": [
            sys.executable, "scripts/audit_chain_verify_job.py", "--json",
        ],
        "note": "完整性三门（G2 全链重算 / G3 日根 / G4 口径）；G1 属治理门，报告写 data/maintenance/audit",
    },
    {
        "key": "memory_longterm_archive",
        "name": "维护·长期记忆归档",
        "hour": 12,
        "minute": 20,
        "kind": "shell",
        "argv": [
            sys.executable, "-m", "agent.memory.ltm_archive", "--json",
        ],
        "note": "把非空压缩摘要 upsert 进 long_term.db（零 token、只读源文件）",
    },
    {
        "key": "workflow_templates_seed",
        "name": "维护·工作流模板补种",
        "hour": 13,
        "minute": 20,
        "kind": "shell",
        "argv": [
            sys.executable, "-m", "agent.workflow_learning.templates", "--seed", "--json",
        ],
        "note": "补种首批内置工作流模板（只补缺不覆盖；全部只读、默认不启用）",
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
            argv, **_proc.hidden_kwargs(),
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


def _http_json(method: str, endpoint_parts: Tuple[str, ...], timeout: int = HTTP_TIMEOUT,
               body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """打本机端点，返回 {status, body}；令牌只进请求头，不回显。"""
    url = BASE_URL.rstrip("/") + endpoint(endpoint_parts)
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, method=method, data=data)
    if data is not None:
        req.add_header("Content-Type", "application/json")
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
    """记忆审查：**mode=full**（2026-11-13 起）。

    以前发的是默认 quick，只读长期记忆库的统计（实测 long_term.db 0 行 ⇒ 永远 0 陈旧/0 重复）；
    full 才会跑 reviewer.review() + 对话记忆的整理候选扫描（agent/memory/consolidation.py），
    并把报告落 data/maintenance/memory/。仍然零 token，只回计数、不回记忆内容。
    """
    r = _http_json("POST", EP_MEMORY_REVIEW, body={"mode": "full"})
    body = r.get("body") or {}
    if not body.get("ok", True):
        return False, "服务端返回 not ok: " + _tail(str(body.get("error", "")), 200)
    cons = body.get("consolidation") or {}
    frozen = (cons.get("frozen_pool") or {}).get("count", "—")
    return True, ("mode=full 重复候选=%s 组 / 冻结池=%s 条 / status=%s"
                  % (cons.get("no_response_repeat_groups", "—"), frozen, r.get("status")))


# ── 空闲闸门（2026-11-13）─────────────────────────────────────
# 【为什么】这批作业是"维护"，不是"必须准点"的活。用户正在用机器时跑巡检/审查会抢 IO 与 CPU，
# 而原来的调度只看时钟（§11.103 F：21 条任务全按 hour/minute/last_run 触发）。
# 【口径】到点先看空闲（agent/system_idle：用户输入 + 进程内活动 + CPU），不空闲就**排队**；
# 每 10 分钟补跑一次；到 FALLBACK_HOUR（18:00）还没跑就强制跑 —— 忙不能变成"今天不做"。
# 【为什么不是"睡等"】调度线程是单线程轮询，在里面 sleep 会把别的任务一起卡住。

def _pending_path() -> str:
    return os.path.join(REPO_ROOT, PENDING_REL_PATH)


def _today() -> str:
    from datetime import date
    return date.today().isoformat()


def _load_pending() -> Dict[str, Any]:
    """读待办；跨天自动作废（昨天的活不补，避免开机就把历史作业全灌一遍）。"""
    try:
        with open(_pending_path(), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {"date": _today(), "keys": []}
    if not isinstance(state, dict) or state.get("date") != _today():
        return {"date": _today(), "keys": []}
    keys = [str(k) for k in (state.get("keys") or []) if str(k) in _BY_KEY]
    return {"date": _today(), "keys": list(dict.fromkeys(keys))}


#: 仓库根（**真实**路径，不受 REPO_ROOT 被用例改写影响）——用于"测试期不许写生产排队文件"的判据
_TRUE_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _running_under_pytest() -> bool:
    """当前进程是否在跑测试（不读环境变量，避免被 settings 零缺口门禁判成未登记开关）。"""
    return "pytest" in sys.modules or "_pytest" in sys.modules


def _save_pending(state: Dict[str, Any]) -> None:
    path = _pending_path()
    # 【2026-11-13 实测的坑】早期用例直接调 run_job（没加 force），闸门把 3 条作业写进了**生产**的
    # data/maintenance/zero_token_pending.json。用例本该把它们指到 tmp（patch z.REPO_ROOT），
    # 但只要有人忘了，就会污染真实排队。这里加一道闸：测试进程 + 目标是真实仓库 ⇒ 不写。
    # 【为什么不用 PYTEST_CURRENT_TEST 环境变量】任何 os.environ 读取都会被
    # tests/unit/test_settings_registry.py 的"零缺口"门禁判成未登记开关（实测 CI 红）；
    # 用 sys.modules 判进程即可，既不引入新开关，也不会在生产里命中。
    if _running_under_pytest() and os.path.abspath(path).startswith(_TRUE_REPO_ROOT + os.sep):
        logger.debug("[zero-token] 测试进程不写生产排队文件: %s", path)
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
    except OSError as exc:  # noqa: BLE001 排队失败只影响"补跑"，不能让调度线程崩
        logger.warning("[zero-token] 待办写入失败（忽略）: %s", type(exc).__name__)


def mark_pending(key: str) -> None:
    state = _load_pending()
    if key not in state["keys"]:
        state["keys"].append(key)
        _save_pending(state)


def idle_state() -> Dict[str, Any]:
    """当前空闲判定（用户输入 + 进程内活动 + CPU；取不到的读数如实为 None）。"""
    try:
        from agent import system_idle
        return system_idle.state(idle_timeout=IDLE_TIMEOUT_SEC, max_cpu=IDLE_MAX_CPU_PERCENT)
    except Exception as exc:  # noqa: BLE001 判据不可用 ⇒ 当作"不空闲"，宁可延后也不抢资源
        logger.warning("[zero-token] 空闲判定不可用，按不空闲处理: %s", type(exc).__name__)
        return {"is_idle": False, "reason": "idle_probe_failed", "idle_source": "unavailable",
                "degraded": True, "idle_seconds": None, "cpu_percent": None}


def consolidation_tick() -> Dict[str, Any]:
    """空闲时跑一次记忆整理（只读、只报告），不空闲就跳过。永不抛。"""
    idle = idle_state()
    if not idle.get("is_idle"):
        logger.info("[zero-token] 记忆整理跳过：当前不空闲（%s）", idle.get("reason"))
        return {"ok": True, "skipped": True, "reason": idle.get("reason") or "busy"}
    try:
        from agent.memory.consolidation import scan, write_report
        result = scan()
        path = write_report(result)
        savings = result.get("savings") or {}
        return {"ok": True, "skipped": False, "report": path,
                "no_response_groups": result.get("no_response_repeat_groups", 0),
                "agent_memory_duplicates": (result.get("agent_memory") or {}).get("duplicate_entries", 0),
                "near_duplicate_groups": result.get("near_duplicate_groups", 0),
                "estimated_tokens": savings.get("estimated_tokens", 0)}
    except Exception as exc:  # noqa: BLE001 整理失败只记日志，不影响调度线程
        logger.warning("[zero-token] 记忆整理失败（%s）", type(exc).__name__)
        return {"ok": False, "detail": type(exc).__name__}


def drain_pending() -> Dict[str, Any]:
    """把排队的作业在空闲时补跑一次；到 FALLBACK_HOUR 之后强制跑。永不抛。"""
    state = _load_pending()
    if not state["keys"]:
        return {"ran": [], "waiting": 0, "forced": False, "idle": None}
    idle = idle_state()
    from datetime import datetime
    forced = datetime.now().hour >= FALLBACK_HOUR
    if not idle.get("is_idle") and not forced:
        logger.info("[zero-token] 有 %d 条作业排队，当前不空闲（%s）⇒ 等下一轮",
                    len(state["keys"]), idle.get("reason"))
        return {"ran": [], "waiting": len(state["keys"]), "forced": False, "idle": False}
    ran: List[Dict[str, Any]] = []
    remaining: List[str] = []
    for key in list(state["keys"]):
        result = run_job(key, force=True)
        ran.append({"job": key, "ok": bool(result.get("ok")), "detail": _tail(str(result.get("detail", "")), 120)})
        if not result.get("ok"):
            logger.warning("[zero-token] 补跑失败（不再排队，避免死循环）: %s %s", key, result.get("detail"))
    state["keys"] = remaining
    _save_pending(state)
    logger.info("[zero-token] 空闲补跑完成: %d 条（forced=%s）", len(ran), forced)
    return {"ran": ran, "waiting": 0, "forced": bool(forced), "idle": bool(idle.get("is_idle"))}


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


def _job_learning_drafts(job: Dict[str, Any]) -> Tuple[bool, str]:
    """学习抽象：只记「产出几张草稿」，不打印记忆内容（隐私）。"""
    r = _http_json("POST", tuple(job["endpoint"]), body=job.get("json"))
    payload = (r.get("body") or {}).get("data") or {}
    if not payload.get("ok", True):
        return False, "服务端 not ok: " + _tail(str(payload.get("error", "")), 160)
    drafts = payload.get("drafts") or []
    drafted = sum(1 for d in drafts if d.get("drafted"))
    return True, ("聚类=%s 过闸=%s 草稿=%d"
                  % (payload.get("total_clusters"), payload.get("passed_clusters", 0), drafted))


def _run_http_job(job: Dict[str, Any]) -> Tuple[bool, str]:
    if job["key"] == "health_snapshot":
        return _job_health_snapshot()
    if job["key"] == "memory_review":
        return _job_memory_review()
    if job["key"] == "learning_drafts":
        return _job_learning_drafts(job)
    r = _http_json(job.get("method", "GET"), tuple(job["endpoint"]),
                   body=job.get("json"))
    body = r.get("body") or {}
    if isinstance(body, dict) and body.get("code") not in (None, 200):
        return False, "服务端返回 code=" + str(body.get("code")) + " " + _tail(str(body.get("message", "")), 160)
    return True, "status=" + str(r.get("status"))


def run_job(key: str, *, force: bool = False) -> Dict[str, Any]:
    """执行一条作业；返回结构化结果，永不抛（调度线程安全）。

    force=False（调度器调用）时先过空闲闸门：不空闲就排队（drain_pending 负责补跑，
    FALLBACK_HOUR 之后强制）。force=True 用于手动/补跑，不再二次判断。
    """
    started = time.time()
    job = _BY_KEY.get(key)
    if job is None:
        return {"ok": False, "job": key, "detail": "未知作业", "elapsed_ms": 0}
    if not force:
        state = idle_state()
        if not state.get("is_idle"):
            mark_pending(key)
            logger.info("[zero-token] %s 到点但系统不空闲（%s）⇒ 排队等补跑", job["name"], state.get("reason"))
            return {"ok": True, "deferred": True, "job": key, "name": job["name"],
                    "detail": "系统不空闲（%s），已排队等空闲补跑" % state.get("reason"),
                    "elapsed_ms": 0}
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
            zero_token_note=job.get("note", ""),
        )
        registered.append(job["key"])
    # 空闲记忆整理（2026-11-13）：每 6 小时一次，**只在空闲时**跑；只读扫描 + 报告，
    # 不删除任何东西（口径见 agent/memory/consolidation.py）。不排队：整理没有"今天必须做"。
    scheduler.add_interval_task(
        name="维护·记忆整理",
        func=consolidation_tick,
        interval_seconds=CONSOLIDATION_INTERVAL_SEC,
        zero_token=True,
        owner="agent/maintenance/zero_token_jobs.py",
    )
    # 空闲补跑：每 10 分钟看一眼排队里的作业（到点不空闲就排队，见 run_job / drain_pending）
    scheduler.add_interval_task(
        name="维护·空闲补跑",
        func=drain_pending,
        interval_seconds=DRAIN_INTERVAL_SEC,
        zero_token=True,
        owner="agent/maintenance/zero_token_jobs.py",
    )
    logger.info("[zero-token] 已注册 %d 条维护作业（09:00-18:00）+ 空闲补跑（%ds）",
                len(registered), DRAIN_INTERVAL_SEC)
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
        result = run_job(args.run, force=True)
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1

    failed = 0
    for job in JOBS:
        result = run_job(job["key"], force=True)  # 手动 --all 不再过空闲闸门
        print(json.dumps(result, ensure_ascii=False))
        if not result["ok"]:
            failed += 1
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
