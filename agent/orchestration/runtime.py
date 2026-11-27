"""E4-1 第一期：脚本编排运行时（独立 node 子进程 + JSONL 工具 RPC）

【不易·这条链上只有一个权威】安全边界全部来自设计稿
docs/plan/2026-11-27-p4-编排安全设计.md（§2 运行时 / §3 协议 / §4 上限 / §6 审计）：

- 脚本跑在**独立 node 子进程**里：node --permission 只允许读 run_dir 与 runner，
  写 run_dir；--allow-net **不给** ⇒ 网络由权限模型拒绝。启动时探测 --allow-net
  旗标，探测不到就**拒绝运行**（no_net_fence，fail-closed；决策 2：不提供绕过开关）。
- 脚本**没有工具句柄**：只能发 {"t":"call"} / {"t":"spawn"}，工具由宿主执行，
  且每次都过 agent.tools.call（权限预设 / 审批 / 限流 / 注入防御 / 超时上界）。
- 五道账（墙钟 / 内存 / 输出字节 / 工具调用次数 / 嵌套=0）全部在**宿主**侧记账，
  越界即 proc_tree.kill_tree 杀整棵树。
- **不是安全沙箱**：--permission 是能力削减，不防引擎 0day；残余风险见设计稿 §7。

【变易·上限从 env 来】七个读点全部登记 agent/settings/registry.py；默认值即设计稿 §4
第一列。越界值一律**夹回区间**（不静默采用一个更大的值）。

【简易·失败姿态】任何异常都回**稳定 code**（disabled / no_node / no_net_fence /
confined / line_unavailable / denied_tool / cap_calls / cap_output / cap_memory /
timeout / crashed），响应体里不放异常原文（CodeQL py/stack-trace-exposure）。

【为什么不用线程/eval 在服务进程内跑】崩溃与死循环会拖死后端，且权限模型、cwd、env
都是进程级设置（设计稿 §2 末段）。node 缺失 ⇒ no_node，绝不在服务进程内降级执行。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[2]
#: run 目录根（常量路径；每个 run 一个子目录，只写这里）
RUN_ROOT = _ROOT / "data" / "orchestration"
#: 冻结的 JS runner（与运行时同目录；node --allow-fs-read 会把它一起放行）
RUNNER_PATH = Path(__file__).resolve().parent / "node_runner.mjs"
#: 入口脚本在 run 目录里的固定文件名
ENTRY_NAME = "main.mjs"

# ── env 读点（全部登记 registry.py）────────────────────────────────────
ENABLED_ENV = "CP_ORCH_SCRIPT_ENABLED"
NODE_PATH_ENV = "CP_ORCH_NODE_PATH"
TIMEOUT_ENV = "CP_ORCH_TIMEOUT_SEC"
MAX_CALLS_ENV = "CP_ORCH_MAX_CALLS"
MAX_OUTPUT_ENV = "CP_ORCH_MAX_OUTPUT"
MAX_MEMORY_ENV = "CP_ORCH_MAX_MEMORY_MB"
RESULT_CHARS_ENV = "CP_ORCH_RESULT_CHARS"

# ── 默认值 / 上限（设计稿 §4：默认值即上限，越界杀树）──────────────────
DEFAULT_TIMEOUT_SEC = 120
MAX_TIMEOUT_SEC = 600
DEFAULT_MAX_CALLS = 50
MAX_MAX_CALLS = 200
DEFAULT_MAX_OUTPUT_BYTES = 1048576
MAX_MAX_OUTPUT_BYTES = 8388608
DEFAULT_MAX_MEMORY_MB = 512
MAX_MAX_MEMORY_MB = 2048
DEFAULT_RESULT_CHARS = 8000
MAX_RESULT_CHARS = 64000

#: 模型给的脚本源码上限（字符）。超过直接拒，不进 run_dir。
MAX_SCRIPT_CHARS = 200000
#: 保留的 run 目录个数（超出按 mtime 回收；只动 data/orchestration 下的子目录）
MAX_KEPT_RUNS = 50
#: node 探测（--help / --version）的超时秒数
PROBE_TIMEOUT_SEC = 10
#: 主循环等下一行协议消息的最大空转（秒）
PROTOCOL_POLL_SEC = 0.2
#: 单次工具调用在宿主侧的等待上限（秒）；超出按墙钟超时处理（工具自身另有上界）
CALL_WAIT_SEC = 60
#: 回给模型的 stderr 尾巴（字符）
STDERR_TAIL_CHARS = 2000
#: kill 之后等进程退出的秒数
KILL_WAIT_SEC = 5
#: 子进程环境变量白名单（不放任何密钥；NODE_OPTIONS 等一律不进）
_ENV_ALLOWLIST = (
    "PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "NUMBER_OF_PROCESSORS",
)

#: 失败码 → 给模型的人读说明（中文唯一一份）
CODE_MESSAGES: Dict[str, str] = {
    "disabled": "脚本编排未启用（CP_ORCH_SCRIPT_ENABLED=0）。这是默认姿态；"
                "打开它属于 B 级操作，需 Owner 明确同意。",
    "no_node": "本机没有可用的 Node.js（建议 >= 24）。请安装后重试；"
               "本工具**不会**降级成在服务进程内 eval 执行。",
    "no_net_fence": "当前 Node.js 没有 --allow-net 旗标 ⇒ 无法证明网络被权限模型拦住，"
                    "按 fail-closed 拒绝运行（设计决策 2：不提供绕过开关）。",
    "confined": "当前权限预设不是 full-access（受限档）：脚本会起独立子进程并执行任意 JS，"
                "与持久终端同口径一律拒绝。",
    "line_unavailable": "指定的主线不存在或已停用 ⇒ 无法确定工具白名单（不回退成全量授权）。",
    "bad_script": "脚本为空或超过长度上限。",
    "denied_tool": "脚本请求的工具不在本次 run 的白名单内，或该工具始终禁止（治理平面/编排自身/终端）。",
    "needs_approval": "工具需要人工确认（L2+），而脚本内无法弹审批框 ⇒ 按 fail-closed 拒绝。"
                      "要放行请先人工批准该工具、或改用无需逐次确认的工具。",
    "cap_calls": "工具调用次数超过上限，已杀树。",
    "cap_output": "脚本输出超过字节上限，已杀树。",
    "cap_memory": "脚本内存超过上限（node 堆上限），进程已被终止。",
    "timeout": "脚本墙钟超时，已杀整棵进程树。",
    "crashed": "脚本异常退出（见 stderr 尾巴）。",
    "tool_error": "工具执行抛异常（详见服务日志）。",
}

_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}
_PROBE_LOCK = threading.Lock()


def _env_str(name: str) -> str:
    try:
        return str(os.environ.get(name, "") or "").strip()
    except Exception:  # noqa: BLE001 读环境变量失败按未设置处理
        return ""


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _clamp_int(raw: str, default: int, low: int, high: int) -> int:
    """读整数 env：缺失/非法/越界一律夹回 [low, high]（不静默放大）。"""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(int(low), min(int(high), value))


def enabled() -> bool:
    """开关（默认关）。判据只有这一处。"""
    return _truthy(_env_str(ENABLED_ENV))


def limits() -> Dict[str, int]:
    """本次 run 的上限快照（审计与结果面用它，别在别处重算）"""
    return {
        "timeout_sec": _clamp_int(_env_str(TIMEOUT_ENV), DEFAULT_TIMEOUT_SEC,
                                  1, MAX_TIMEOUT_SEC),
        "max_calls": _clamp_int(_env_str(MAX_CALLS_ENV), DEFAULT_MAX_CALLS,
                                1, MAX_MAX_CALLS),
        "max_output_bytes": _clamp_int(_env_str(MAX_OUTPUT_ENV), DEFAULT_MAX_OUTPUT_BYTES,
                                       4096, MAX_MAX_OUTPUT_BYTES),
        "max_memory_mb": _clamp_int(_env_str(MAX_MEMORY_ENV), DEFAULT_MAX_MEMORY_MB,
                                    64, MAX_MAX_MEMORY_MB),
        "result_chars": _clamp_int(_env_str(RESULT_CHARS_ENV), DEFAULT_RESULT_CHARS,
                                   512, MAX_RESULT_CHARS),
    }


def node_binary() -> str:
    """node 可执行文件路径（CP_ORCH_NODE_PATH 优先，否则 PATH 里的 node）"""
    return _env_str(NODE_PATH_ENV) or "node"


def probe_node(binary: Optional[str] = None) -> Dict[str, Any]:
    """探测 node 的版本与关键旗标（结果按可执行文件路径缓存）

    Returns:
        {"ok": bool, "version": str, "permission": bool, "allow_net": bool,
         "error": str}；error 只放**类型/稳定短语**，不放异常原文。
    """
    exe = str(binary or node_binary())
    with _PROBE_LOCK:
        cached = _PROBE_CACHE.get(exe)
        if cached is not None:
            return dict(cached)
    from agent.utils import proc as _proc

    out: Dict[str, Any] = {"ok": False, "version": "", "permission": False,
                           "allow_net": False, "error": ""}
    try:
        version = subprocess.run(
            [exe, "--version"], **_proc.hidden_kwargs(),
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC,
        )
        help_out = subprocess.run(
            [exe, "--help"], **_proc.hidden_kwargs(),
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SEC,
        )
    except FileNotFoundError:
        out["error"] = "not_found"
        return _remember(exe, out)
    except (OSError, subprocess.SubprocessError) as exc:
        out["error"] = type(exc).__name__
        return _remember(exe, out)
    if version.returncode != 0 or help_out.returncode != 0:
        out["error"] = "probe_failed"
        return _remember(exe, out)
    text = (help_out.stdout or "") + (help_out.stderr or "")
    out["version"] = str(version.stdout or "").strip()[:40]
    # 【为什么用纯文本包含判定】--help 的行宽会随终端变化，逐行解析比包含更脆。
    out["permission"] = "--permission" in text
    out["allow_net"] = "--allow-net" in text
    out["ok"] = bool(out["permission"] and out["allow_net"])
    if not out["permission"]:
        out["error"] = "no_permission_flag"
    elif not out["allow_net"]:
        out["error"] = "no_allow_net_flag"
    return _remember(exe, out)


def _remember(exe: str, value: Dict[str, Any]) -> Dict[str, Any]:
    with _PROBE_LOCK:
        _PROBE_CACHE[exe] = dict(value)
    return dict(value)


def reset_probe_cache() -> None:
    """清缓存（**测试专用**：换 PATH / 换假 node 后要重新探测）"""
    with _PROBE_LOCK:
        _PROBE_CACHE.clear()


def _child_env(run_dir: Path, caps: Mapping[str, int]) -> Dict[str, str]:
    """子进程环境：只给最小集 + run_dir 下的临时目录（不放任何密钥）

    caps 只透传 runner **自己也要数一遍**的两个数（ORCH_MAX_OUTPUT /
    ORCH_RESULT_CHARS）：宿主侧仍独立计数，runner 侧是防绕过的那一份。
    """
    env: Dict[str, str] = {}
    for key in _ENV_ALLOWLIST:
        value = os.environ.get(key)
        if value:
            env[key] = value
    tmp = str(run_dir / "tmp")
    env["TMP"] = tmp
    env["TEMP"] = tmp
    env["HOME"] = str(run_dir)
    env["USERPROFILE"] = str(run_dir)
    env["ORCH_MAX_OUTPUT"] = str(int(caps.get("max_output_bytes") or DEFAULT_MAX_OUTPUT_BYTES))
    env["ORCH_RESULT_CHARS"] = str(int(caps.get("result_chars") or DEFAULT_RESULT_CHARS))
    return env


def _audit(event: str, payload: Mapping[str, Any], *, subject: str = "") -> None:
    """审计旁路（失败只记 debug：审计坏了不该让编排失败）"""
    try:
        from agent.audit.facade import record as _record
        _record(event, actor="orchestration", subject=subject or event,
                payload=dict(payload))
    except Exception as exc:  # noqa: BLE001
        logger.debug("[orchestration] 审计写入失败(%s): %s", event, type(exc).__name__)


def _prune_runs(keep: int = MAX_KEPT_RUNS) -> None:
    """回收旧的 run 目录（只动 RUN_ROOT 的直接子目录，按 mtime 保留最新 keep 个）"""
    try:
        if not RUN_ROOT.is_dir():
            return
        dirs = [p for p in RUN_ROOT.iterdir() if p.is_dir()]
        if len(dirs) <= keep:
            return
        dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in dirs[keep:]:
            shutil.rmtree(stale, ignore_errors=True)
    except OSError as exc:
        logger.debug("[orchestration] 回收旧 run 目录失败: %s", type(exc).__name__)


def _write_run_dir(script: str) -> Path:
    """在常量目录下建 run 目录并写入口脚本；返回 run_dir"""
    run_id = "run-" + time.strftime("%Y%m%dT%H%M%S", time.gmtime()) + "-" + uuid.uuid4().hex[:8]
    run_dir = RUN_ROOT / run_id
    (run_dir / "tmp").mkdir(parents=True, exist_ok=True)
    (run_dir / ENTRY_NAME).write_text(str(script), encoding="utf-8")
    return run_dir


def _decode(chunk: bytes) -> str:
    return chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else str(chunk)


def _shape(value: Any, result_chars: int) -> Tuple[Any, int]:
    """把工具结果压成可 JSON 化、可传回脚本的形态（超长截断并标记）"""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        text = json.dumps(str(value), ensure_ascii=False)
    size = len(text)
    if size > result_chars:
        return {"truncated": True, "chars": size,
                "text": text[:result_chars]}, size
    try:
        return json.loads(text), size
    except ValueError:
        return text, size


def _denial_code(payload: Any) -> str:
    """从工具返回里判「被闸门拒了」还是「工具自己失败」"""
    if not isinstance(payload, Mapping):
        return ""
    if payload.get("ok") is not False:
        return ""
    err = str(payload.get("error_code") or "")
    if err in ("APPROVAL_REQUIRED", "APPROVAL_REJECTED"):
        return "needs_approval"
    if err == "PERMISSION_DENIED" or payload.get("blocked") is True:
        return "needs_approval"
    return "tool_error"


def _call_tool(name: str, args: Mapping[str, Any], *, run_id: str,
               result_chars: int, scope_tools: Sequence[str] = ()) -> Tuple[bool, Dict[str, Any]]:
    """宿主执行一次工具调用（**唯一执行点**：仍然过 agent.tools.call 的全部闸门）

    Returns:
        (ok, envelope)。envelope 直接回给脚本：成功 {ok, value, truncated?}，
        失败 {ok: False, code, error}。
    """
    started = time.monotonic()
    try:
        from agent import tools as _tools
        from agent import tool_gate as _gate

        # 【E4-1b】整 run 一次性授权：把本次 run 的授权集放进闸门的 L2 窄旁路
        # （单次消费；L3 不认。详见 agent/tool_gate.py 的 run-scope 小节）。
        # 设置点必须在**执行工具的这个线程**里 —— contextvars 不跨线程继承。
        scope_token = _gate.set_run_scope(scope_tools, run_id=run_id)
        try:
            payload = _tools.call(name, **dict(args or {}))
        finally:
            _gate.reset_run_scope(scope_token)
    except Exception as exc:  # noqa: BLE001 已知领域失败只回稳定 code（不回原文）
        logger.error("[orchestration] 工具 %s 执行抛异常: %s", name, type(exc).__name__)
        envelope = {"ok": False, "code": "tool_error",
                    "error": CODE_MESSAGES["tool_error"]}
        _audit("orchestration_tool_call", {
            "run_id": run_id, "tool": name, "ok": False, "code": "tool_error",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }, subject=run_id)
        return False, envelope
    denial = _denial_code(payload)
    if denial:
        envelope = {"ok": False, "code": denial, "error": CODE_MESSAGES.get(denial, "")}
        _audit("orchestration_tool_call", {
            "run_id": run_id, "tool": name, "ok": False, "code": denial,
            "duration_ms": int((time.monotonic() - started) * 1000),
        }, subject=run_id)
        return False, envelope
    value, size = _shape(payload, result_chars)
    envelope = {"ok": True, "value": value}
    if isinstance(value, Mapping) and value.get("truncated") is True:
        envelope["truncated"] = True
    _audit("orchestration_tool_call", {
        "run_id": run_id, "tool": name, "ok": True,
        "result_bytes": size,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }, subject=run_id)
    return True, envelope


def _run_in_thread(fn, timeout: float) -> Tuple[bool, Any]:
    """在 daemon 线程里跑 fn，最多等 timeout 秒（超时返回 (False, None)）

    【为什么用 daemon 线程而不是线程池】工具 handler 挂了也不能阻止解释器退出；
    handler 自身另有超时上界（agent.tools.call 的 timeouts 机制），这里只兜住本层的账。
    """
    box: Dict[str, Any] = {}

    def _target() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 线程内异常经 ok=False 如实带回
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=_target, name="orch-call", daemon=True)
    worker.start()
    worker.join(max(0.1, float(timeout)))
    if worker.is_alive():
        return False, None
    if "error" in box:
        return False, box["error"]
    return True, box.get("value")


class _Protocol:
    """JSONL 协议写端（stdin）；写失败（脚本已死）不抛，只置 closed"""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._lock = threading.Lock()
        self.closed = False

    def send(self, obj: Mapping[str, Any]) -> bool:
        if self.closed:
            return False
        try:
            line = json.dumps(dict(obj), ensure_ascii=False)
        except (TypeError, ValueError):
            line = json.dumps({"t": "result", "ok": False, "code": "tool_error"})
        data = (line + "\n").encode("utf-8")
        with self._lock:
            try:
                if self._proc.stdin is None:
                    return False
                self._proc.stdin.write(data)
                self._proc.stdin.flush()
                return True
            except (OSError, ValueError):
                self.closed = True
                return False


def run(script: str, *, line: str = "", allow_tools: Sequence[str] = (),
        allow_spawn: bool = False, timeout_sec: Optional[int] = None,
        max_calls: Optional[int] = None, session_id: str = "") -> Dict[str, Any]:
    """跑一次脚本编排（E4-1 第一期的唯一入口）

    Args:
        script: 模型给的 JS 源码（跑在 vm 沙箱里；tools.call / spawn / console / sleep）
        line: 主线 id（白名单 = 该线装配集 ∩ 静态只读清单 ∩ allow_tools）
        allow_tools: 额外放行的工具名（仍受静态清单约束；治理平面/编排自身/终端永不放行）
        allow_spawn: 是否允许脚本调 spawn（fan_out；仍过全部闸门）
        timeout_sec / max_calls: 本次收窄上限（只能小于 env 给的上限）
        session_id: 调用方所属会话（结果面按会话过滤用；空串 = 未声明）

    Returns:
        结构化结果（ok / run_id / code / exit_code / duration_ms / calls / spawned /
        whitelist / limits / result / stderr_tail）。**不抛异常**。
    """
    if not enabled():
        return _fail("disabled")
    from agent.sandbox import is_confined
    if is_confined():
        return _fail("confined")
    source = str(script or "")
    if not source.strip() or len(source) > MAX_SCRIPT_CHARS:
        return _fail("bad_script")
    probe = probe_node()
    if not probe.get("ok"):
        return _fail("no_net_fence" if probe.get("permission") else "no_node")

    from agent.orchestration import whitelist as _whitelist
    resolved = _whitelist.resolve(line=line, allow_tools=allow_tools)
    if not resolved.get("ok"):
        payload = _fail(str(resolved.get("code") or "line_unavailable"))
        payload["line"] = str(resolved.get("line") or "")
        return payload

    caps = limits()
    if timeout_sec:
        try:
            caps["timeout_sec"] = max(1, min(caps["timeout_sec"], int(timeout_sec)))
        except (TypeError, ValueError):
            pass
    if max_calls:
        try:
            caps["max_calls"] = max(1, min(caps["max_calls"], int(max_calls)))
        except (TypeError, ValueError):
            pass

    tools_allowed: Tuple[str, ...] = tuple(resolved.get("tools") or ())
    # 【E4-1b】run-scope = 本次白名单 ∪（allow_spawn 时的 fan_out）。
    #   fan_out 不在静态只读清单里（它是 execute），所以它只可能通过 spawn 入口
    #   被授权；把"整 run 已批准"这件事显式写进作用域，别在别处再判一次。
    scope_tools: Tuple[str, ...] = tools_allowed + (("fan_out",) if allow_spawn else ())
    run_dir = _write_run_dir(source)
    run_id = run_dir.name
    _prune_runs()
    _audit("orchestration_run", {
        "run_id": run_id, "line": resolved.get("line", ""),
        "whitelist_size": len(tools_allowed),
        "allow_spawn": bool(allow_spawn),
        "limits": caps, "node": probe.get("version", ""),
    }, subject=run_id)

    started = time.monotonic()
    deadline = started + float(caps["timeout_sec"])
    argv = [
        node_binary(), "--permission",
        "--allow-fs-read=" + str(run_dir),
        "--allow-fs-read=" + str(RUNNER_PATH),
        "--allow-fs-write=" + str(run_dir),
        "--max-old-space-size=" + str(caps["max_memory_mb"]),
        "--no-warnings",
        str(RUNNER_PATH), str(run_dir / ENTRY_NAME),
    ]
    from agent.utils import proc as _proc
    try:
        proc = subprocess.Popen(
            argv, **_proc.hidden_kwargs(),
            cwd=str(run_dir), env=_child_env(run_dir, caps),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    except (OSError, ValueError) as exc:
        logger.error("[orchestration] 起 node 失败: %s", type(exc).__name__)
        return _fail("no_node", run_id=run_id)

    state: Dict[str, Any] = {"stdout": 0, "stderr": 0, "calls": 0,
                             "spawned": 0, "denied": 0, "stderr_tail": "",
                             "stderr_head": "", "memory_hit": False}
    state_lock = threading.Lock()
    out_q: "queue.Queue[Optional[bytes]]" = queue.Queue()
    protocol = _Protocol(proc)

    def _pump(stream, kind: str) -> None:
        while True:
            try:
                chunk = stream.readline()
            except (OSError, ValueError):
                break
            if not chunk:
                break
            size = len(chunk)
            with state_lock:
                state[kind] += size
                if kind == "stderr":
                    text = _decode(chunk)
                    # 【为什么同时留头尾】node 的 OOM 致命行在**最前面**，后面是几十行
                    # 原生栈；只留尾巴会把"为什么死"的那一句截掉（实测踩到）。
                    if len(str(state["stderr_head"])) < STDERR_TAIL_CHARS:
                        state["stderr_head"] = (
                            str(state["stderr_head"]) + text)[:STDERR_TAIL_CHARS]
                    if ("heap out of memory" in text
                            or "Reached heap limit" in text):
                        state["memory_hit"] = True
                    state["stderr_tail"] = (state["stderr_tail"] + text)[-STDERR_TAIL_CHARS:]
            if kind == "stdout":
                out_q.put(chunk)
        if kind == "stdout":
            out_q.put(None)

    threading.Thread(target=_pump, args=(proc.stdout, "stdout"),
                     name="orch-out", daemon=True).start()
    threading.Thread(target=_pump, args=(proc.stderr, "stderr"),
                     name="orch-err", daemon=True).start()

    code = ""
    result_value: Any = None

    def _reply(msg_id: Any, envelope: Mapping[str, Any]) -> None:
        payload = {"t": "result", "id": msg_id}
        payload.update(dict(envelope))
        protocol.send(payload)

    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                code = "timeout"
                break
            with state_lock:
                total = state["stdout"] + state["stderr"]
            if total > caps["max_output_bytes"]:
                code = "cap_output"
                break
            try:
                chunk = out_q.get(timeout=min(remaining, PROTOCOL_POLL_SEC))
            except queue.Empty:
                if proc.poll() is not None and out_q.empty():
                    code = "crashed"
                    break
                continue
            if chunk is None:
                code = "crashed"
                break
            text = _decode(chunk).strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if not isinstance(msg, Mapping):
                continue
            kind = str(msg.get("t") or "")
            if kind == "done":
                result_value, _n = _shape(msg.get("result"), caps["result_chars"])
                code = ""
                break
            if kind == "error":
                tail = str(msg.get("stack") or msg.get("message") or "")
                with state_lock:
                    state["stderr_tail"] = (state["stderr_tail"] + tail)[-STDERR_TAIL_CHARS * 2:]
                code = "crashed"
                break
            if kind in ("call", "spawn"):
                with state_lock:
                    state["calls"] += 1
                    calls_now = state["calls"]
                if calls_now > caps["max_calls"]:
                    _reply(msg.get("id"), {"ok": False, "code": "cap_calls",
                                           "error": CODE_MESSAGES["cap_calls"]})
                    code = "cap_calls"
                    break
                msg_id = msg.get("id")
                if kind == "spawn":
                    if not allow_spawn:
                        with state_lock:
                            state["denied"] += 1
                        _reply(msg_id, {"ok": False, "code": "denied_tool",
                                        "error": "本次 run 未开启 allow_spawn"})
                        continue
                    tasks = msg.get("tasks")
                    call_args: Dict[str, Any] = {"tasks": tasks if isinstance(tasks, list) else []}
                    fn = lambda: _call_tool("fan_out", call_args, run_id=run_id,
                                            result_chars=caps["result_chars"],
                                            scope_tools=scope_tools)
                else:
                    name = str(msg.get("name") or "")
                    args = msg.get("args")
                    if name not in tools_allowed:
                        with state_lock:
                            state["denied"] += 1
                        _audit("orchestration_tool_call", {
                            "run_id": run_id, "tool": name, "ok": False,
                            "code": "denied_tool",
                        }, subject=run_id)
                        _reply(msg_id, {"ok": False, "code": "denied_tool",
                                        "error": CODE_MESSAGES["denied_tool"] + "（" + name + "）"})
                        continue
                    call_args = dict(args) if isinstance(args, Mapping) else {}
                    fn = lambda: _call_tool(name, call_args, run_id=run_id,
                                            result_chars=caps["result_chars"],
                                            scope_tools=scope_tools)
                wait_s = max(0.1, min(float(CALL_WAIT_SEC), deadline - time.monotonic()))
                finished, value = _run_in_thread(fn, wait_s)
                if not finished:
                    code = "timeout"
                    break
                ok, envelope = value
                if ok and kind == "spawn":
                    with state_lock:
                        state["spawned"] += 1
                _reply(msg_id, envelope)
                continue
            # 未知消息类型：忽略（runner 只发上述几种）
    finally:
        try:
            from agent.proc_tree import kill_tree
            kill_tree(proc.pid)
        except Exception as exc:  # noqa: BLE001 清理失败只记 debug
            logger.debug("[orchestration] kill_tree 失败: %s", type(exc).__name__)
        try:
            proc.wait(timeout=KILL_WAIT_SEC)
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass
        protocol.closed = True

    duration_ms = int((time.monotonic() - started) * 1000)
    with state_lock:
        calls = int(state["calls"])
        spawned = int(state["spawned"])
        denied = int(state["denied"])
        head = str(state["stderr_head"])
        tail = str(state["stderr_tail"])
        memory_hit = bool(state["memory_hit"])
        out_bytes = int(state["stdout"])
        err_bytes = int(state["stderr"])
    if head and tail and head not in tail:
        stderr_tail = (head + "\n...（中略）...\n" + tail)[-STDERR_TAIL_CHARS * 2:]
    else:
        stderr_tail = tail or head
    # 【为什么在循环之后还要再判一次】脚本可能"先洪泛、后退出"：EOF 先到，循环顶部的
    # 字节检查就没机会跑（实测：runner 自己 exit(3) ⇒ 宿主只看到 crashed）。
    if code in ("", "crashed"):
        if out_bytes + err_bytes > caps["max_output_bytes"] or proc.returncode == 3:
            code = "cap_output"
        elif memory_hit:
            code = "cap_memory"
    _audit("orchestration_exit", {
        "run_id": run_id, "code": code or "ok", "exit_code": proc.returncode,
        "duration_ms": duration_ms, "calls": calls, "spawned": spawned,
        "stdout_bytes": out_bytes, "stderr_bytes": err_bytes,
    }, subject=run_id)
    payload: Dict[str, Any] = {
        "ok": not code,
        "run_id": run_id,
        "code": code,
        "line": resolved.get("line", ""),
        "exit_code": proc.returncode,
        "duration_ms": duration_ms,
        "calls": calls,
        "spawned": spawned,
        "denied": denied,
        "whitelist": list(tools_allowed),
        "limits": caps,
        "result": result_value,
        "stdout_bytes": out_bytes,
        "stderr_bytes": err_bytes,
        "stderr_tail": stderr_tail,
    }
    if code:
        payload["error"] = CODE_MESSAGES.get(code, "")
    # ── E4-2：只读投影（checkpoint + run 索引）────────────────────────────
    # 【为什么是旁路】结果面坏了只是"看不到这次 run"，不该让已经跑完的 run 报错。
    try:
        from agent.orchestration import runs as _runs

        _runs.write_checkpoint(run_dir, payload)
        _runs.record_run({**payload, "session_id": session_id,
                          "whitelist_size": len(tools_allowed)})
    except Exception as exc:  # noqa: BLE001 投影失败不影响本次 run 的返回
        logger.debug("[orchestration] 结果投影失败: %s", type(exc).__name__)
    return payload


def _fail(code: str, *, run_id: str = "") -> Dict[str, Any]:
    """稳定失败信封（不 spawn、不写 run_dir 的那几种都走这里）"""
    return {"ok": False, "code": code, "run_id": run_id,
            "error": CODE_MESSAGES.get(code, "")}


__all__ = [
    "CODE_MESSAGES", "ENTRY_NAME", "RUNNER_PATH", "RUN_ROOT",
    "DEFAULT_MAX_CALLS", "DEFAULT_MAX_MEMORY_MB", "DEFAULT_MAX_OUTPUT_BYTES",
    "DEFAULT_RESULT_CHARS", "DEFAULT_TIMEOUT_SEC",
    "MAX_MAX_CALLS", "MAX_MAX_MEMORY_MB", "MAX_MAX_OUTPUT_BYTES",
    "MAX_RESULT_CHARS", "MAX_TIMEOUT_SEC",
    "enabled", "limits", "node_binary", "probe_node", "reset_probe_cache", "run",
]
