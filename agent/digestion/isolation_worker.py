"""隔离执行体（TASK-S8-03 步骤 2/3）—— **stdlib-only，可在容器内裸跑**

本文件是**被隔离执行的那一半**：它同时被容器路径（`docker run … python
/src/agent/digestion/isolation_worker.py`）与强隔离子进程路径
（`env_mode=replace` 的子进程）调用。它刻意**不导入 `agent.*`**：容器里只读挂载
源码树，除了本文件与标准库什么都没有；任何对 `agent.digestion` 的导入都会把
"隔离执行"变成"把宿主那一整套依赖搬进边界内"。

## 契约（可被用例与探针断言）

1. **读 stdin / 写 stdout 的纯函数式协议** —— 作业 JSON 从 `--job -` 读入，
   结果 JSON 写在 ``===ISOLATION_RESULT_BEGIN===`` / ``===ISOLATION_RESULT_END===``
   之间。标记之外的一切（解释器噪声、traceback）都**不属于**结果，由调用方
   另行存档，故"解析失败"不会被伪装成"执行成功"。
2. **唯一可写根是 `work_root`** —— 任何写入/删除落到其外的路径一律
   `denied`（非探针模式），作业状态置 `escape_blocked`。这是**协作式路径守卫**，
   不是内核级隔离；容器路径另外还有只读挂载兜底（见设计文档"不保证的边界"）。
3. **副作用只记录 + 仅落临时目录** —— `side_effects` 逐条记账；真实落盘只发生在
   `work_root`（容器内为 tmpfs），故"记录不双写真实环境"在两条路径上都成立。
4. **超限即 `quota_exceeded`** —— 步数/写文件数/字节数/外部调用数超限时**不静默**，
   状态显式置 `quota_exceeded` 并带错误码。
5. **`probe_mode` 才允许越界探针** —— `escape_write` / `net_probe_raw` 这类
   "故意违反守卫以测量真实边界"的操作只在 `probe_mode=true` 的作业里可用；
   候选作业拿不到它们（否则探针本身就成了逃逸后门）。

## 与主仓的关系

主仓侧的对应物是 `agent.digestion.isolation`（等级模型 + 两条路径的执行器）。
本文件是它的**唯一**对侧实现，故两边的 op 词表与状态词表逐字对齐——不一致会被
`tests/unit/test_isolation_executors.py` 的往返用例当场抓住。
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

# ════════════════════════════════════════════════════════════
#  常量（与 agent.digestion.isolation / sandbox 逐字对齐）
# ════════════════════════════════════════════════════════════

RESULT_BEGIN = "===ISOLATION_RESULT_BEGIN==="
RESULT_END = "===ISOLATION_RESULT_END==="

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_QUOTA_EXCEEDED = "quota_exceeded"
STATUS_DENIED = "denied"
STATUS_ESCAPE_BLOCKED = "escape_blocked"

ERR_QUOTA_STEPS = "E_ISOLATION_QUOTA_STEPS"
ERR_QUOTA_FILES = "E_ISOLATION_QUOTA_FILES"
ERR_QUOTA_BYTES = "E_ISOLATION_QUOTA_BYTES"
ERR_QUOTA_EXTERNAL = "E_ISOLATION_QUOTA_EXTERNAL"
ERR_QUOTA_TIME = "E_ISOLATION_QUOTA_TIME"
ERR_ESCAPE = "E_ISOLATION_ESCAPE"
ERR_DENIED = "E_ISOLATION_DENIED"
ERR_BAD_JOB = "E_ISOLATION_BAD_JOB"

#: 副作用三类（与 `cases.SIDE_EFFECT_KINDS` 同词汇）
SIDE_EFFECT_KINDS: Tuple[str, ...] = ("files_written", "files_deleted",
                                      "external_calls")

#: 外部调用类标签（与 `sandbox.EXTERNAL_LABELS` 同源；**永不真实外发**）
EXTERNAL_LABELS = frozenset({
    "shell_execute", "run_command", "execute", "http_request", "http_get",
    "http_post", "network_call", "web_search", "git_push", "deploy",
    "send_email", "publish",
})

DEFAULT_QUOTA: Dict[str, Any] = {
    "max_steps": 32,
    "max_files_written": 16,
    "max_deleted": 16,
    "max_external_calls": 8,
    "max_bytes": 65536,
    "timeout_s": 30.0,
}

#: 只在这些变量上做「无 $HOME / 无 SSH agent」实测（探针与文档同词表）
ISOLATION_ENV_WATCH: Tuple[str, ...] = (
    "HOME", "USERPROFILE", "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_ASKPASS",
    "GIT_SSH_COMMAND", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "GITHUB_TOKEN", "GH_TOKEN",
    "KUBECONFIG", "DOCKER_HOST", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
)


class QuotaExceeded(Exception):
    """配额超限（状态置 `quota_exceeded`，**不静默**）"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class EscapeBlocked(Exception):
    """越界访问（唯一可写根之外）"""

    def __init__(self, detail: str) -> None:
        super().__init__(f"{ERR_ESCAPE}: {detail}")
        self.detail = detail


def _norm(path: Any) -> str:
    return str(path or "").replace("\\", "/")


def _abspath(path: Any, *, base: str) -> str:
    text = _norm(path)
    if not text:
        raise ValueError("空路径不可访问")
    if os.path.isabs(text) or (len(text) > 1 and text[1] == ":"):
        return os.path.normpath(text).replace("\\", "/")
    return os.path.normpath(os.path.join(base, text)).replace("\\", "/")


def _under(path: str, root: str) -> bool:
    """``path`` 是否落在 ``root`` 之内（**两侧都做分隔符归一**）

    只归一 root 是不够的：Windows 上调用方可能给出 ``C:/a/b`` 而 root 是
    ``C:\\a\\b``，比较会失败并把合法路径判成越界。归一两侧才是"同一把尺子"。
    """
    if not root:
        return False
    norm_root = os.path.normpath(str(root)).replace("\\", "/").rstrip("/")
    norm_path = os.path.normpath(str(path)).replace("\\", "/")
    return norm_path == norm_root or norm_path.startswith(norm_root + "/")


# ════════════════════════════════════════════════════════════
#  执行环境
# ════════════════════════════════════════════════════════════


class WorkerEnv:
    """一次作业的执行环境（可写根 + 只读源根 + 配额 + 网络策略）"""

    def __init__(self, job: Dict[str, Any]) -> None:
        self.job_id = str(job.get("job_id") or "")
        self.notes: List[str] = []
        self.work_root = os.path.normpath(
            str(job.get("work_root") or os.getcwd())).replace("\\", "/")
        self.source_root = os.path.normpath(
            str(job.get("source_root") or "")).replace("\\", "/") \
            if job.get("source_root") else ""
        self.network = str(job.get("network") or "none").strip().lower()
        self.network_allow = [str(x) for x in (job.get("network_allow") or [])]
        self.probe_mode = bool(job.get("probe_mode"))
        quota = dict(DEFAULT_QUOTA)
        raw_quota = job.get("quota")
        if isinstance(raw_quota, dict):
            quota.update(raw_quota)
        elif raw_quota:
            # 作业字段非法不得让执行体崩：**照默认跑**并留一条痕迹（不静默吞）
            self.notes.append(
                f"quota 字段非法（{type(raw_quota).__name__}）⇒ 按默认配额执行")
        self.quota = quota
        self.steps: List[str] = []
        self.outputs: List[Dict[str, Any]] = []
        self.side_effects: Dict[str, List[str]] = {k: [] for k in SIDE_EFFECT_KINDS}
        self.status = STATUS_SUCCESS
        self.error_code = ""
        self.error = ""
        #: 已计费的步数（**独立计数**：`_charge("steps")` 必须自洽，
        #: 不能依赖调用方有没有往 `self.steps` 里追加）
        self.charged_steps = 0
        #: 平台**原始**环境（scrub 前留档；见 run_job 的"先留证再清空"）
        self.platform_env_raw: Dict[str, str] = {}

    # ── 配额 ────────────────────────────────────────────────

    def _qint(self, key: str) -> int:
        try:
            return max(1, int(self.quota.get(key) or 1))
        except (TypeError, ValueError):
            return int(DEFAULT_QUOTA[key])

    def _charge(self, kind: str, amount: float = 1.0) -> None:
        if kind == "steps":
            if self.charged_steps >= self._qint("max_steps"):
                raise QuotaExceeded(ERR_QUOTA_STEPS,
                                    f"步数超限 {self._qint('max_steps')}")
            self.charged_steps += 1
            return
        if kind == "files_written" and \
                len(self.side_effects["files_written"]) >= self._qint("max_files_written"):
            raise QuotaExceeded(ERR_QUOTA_FILES,
                                f"写文件数超限 {self._qint('max_files_written')}")
        if kind == "files_deleted" and \
                len(self.side_effects["files_deleted"]) >= self._qint("max_deleted"):
            raise QuotaExceeded(ERR_QUOTA_FILES,
                                f"删文件数超限 {self._qint('max_deleted')}")
        if kind == "external_calls" and \
                len(self.side_effects["external_calls"]) >= self._qint("max_external_calls"):
            raise QuotaExceeded(ERR_QUOTA_EXTERNAL,
                                f"外部调用数超限 {self._qint('max_external_calls')}")
        if kind == "bytes" and int(amount) > self._qint("max_bytes"):
            raise QuotaExceeded(ERR_QUOTA_BYTES,
                                f"单次写入 {int(amount)}B 超限 "
                                f"{self._qint('max_bytes')}B")

    def _record(self, kind: str, target: str) -> None:
        if target not in self.side_effects[kind]:
            self.side_effects[kind].append(target)

    def check_time(self) -> None:
        """时间配额的统一钩子

        真正的"超时 kill"是**外层执行器**的职责（容器靠 `docker run` 的墙钟、
        子进程靠 `communicate(timeout=)`）——活在内层就杀不掉自己。此处只保留
        钩子，供后续按步计时的实现接入；当前恒为 no-op，不静默改变任何语义。
        """
        return None

    # ── 路径守卫（协作式；容器另有只读挂载兜底） ────────────

    def writable(self, path: Any) -> str:
        resolved = _abspath(path, base=self.work_root)
        if not _under(resolved, self.work_root):
            raise EscapeBlocked(
                f"写入越界: {resolved!r} 不在唯一可写根 {self.work_root!r} 之内")
        return resolved

    def readable(self, path: Any) -> str:
        resolved = _abspath(path, base=self.work_root)
        if _under(resolved, self.work_root):
            return resolved
        if self.source_root and _under(resolved, self.source_root):
            return resolved
        # 探针模式允许读任意路径（测量"宿主目录是否可见"正是探针的职责）
        if self.probe_mode:
            return resolved
        raise EscapeBlocked(
            f"读取越界: {resolved!r} 不在可读根（work_root / source_root）之内")


# ════════════════════════════════════════════════════════════
#  操作（op 词表；与 isolation.py 的 EXEC_OPS 逐字对齐）
# ════════════════════════════════════════════════════════════


def _op_read_file(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.readable(params.get("path"))
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return {"ok": True, "path": path, "content": fh.read()}


def _op_write_file(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.writable(params.get("path"))
    payload = str(params.get("content") or "")
    env._charge("bytes", len(payload.encode("utf-8")))
    env._charge("files_written")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(payload)
    env._record("files_written", path)
    return {"ok": True, "path": path, "bytes": len(payload.encode("utf-8"))}


def _op_append_file(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.writable(params.get("path"))
    payload = str(params.get("content") or "")
    env._charge("bytes", len(payload.encode("utf-8")))
    env._charge("files_written")
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(payload)
    env._record("files_written", path)
    return {"ok": True, "path": path, "bytes": len(payload.encode("utf-8"))}


def _op_delete_file(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.writable(params.get("path"))
    env._charge("files_deleted")
    existed = os.path.exists(path)
    if existed:
        os.remove(path)
    env._record("files_deleted", path)
    return {"ok": True, "path": path, "existed": existed}


def _op_create_dir(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.writable(params.get("path"))
    os.makedirs(path, exist_ok=True)
    env._record("files_written", path)
    return {"ok": True, "path": path}


def _op_stat(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.readable(params.get("path"))
    try:
        info = os.stat(path)
    except OSError as exc:
        return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": path, "size": info.st_size,
            "is_dir": os.path.isdir(path)}


def _op_list_dir(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.readable(params.get("path") or ".")
    try:
        names = sorted(os.listdir(path))
    except OSError as exc:
        return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": path, "names": names[:200]}


def _op_grep(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    pattern = str(params.get("pattern") or "")
    path = env.readable(params.get("path") or ".")
    hits: List[str] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for lineno, line in enumerate(fh, 1):
                if pattern and pattern in line:
                    hits.append(f"{lineno}:{line.rstrip()}")
                if len(hits) >= 50:
                    break
    except OSError as exc:
        return {"ok": False, "path": path, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "path": path, "hits": hits}


def _op_external_call(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    label = str(params.get("label") or "external_call")
    env._charge("external_calls")
    env._record("external_calls", label)
    # 协作式出域策略：与主仓 `guardrails.egress_guard` 同一立场（先判后发），
    # 但**永不真实外发**（隔离执行的产物是"记录"，不是"副作用"）。
    allowed = (env.network == "whitelist"
               and str(params.get("host") or "") in env.network_allow)
    if env.network == "none" or not allowed:
        return {"ok": False, "error_code": ERR_DENIED, "simulated": label,
                "error": f"出域被隔离策略拒绝: {label}（network={env.network}）"}
    return {"ok": True, "simulated": label, "recorded_only": True}


def _op_env_dump(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    names = [str(n) for n in (params.get("names") or ISOLATION_ENV_WATCH)]
    values = {n: str(os.environ.get(n, "")) for n in names}
    return {"ok": True, "names": names,
            "values": values,
            "present": sorted(n for n, v in values.items() if v != ""),
            "home_expand": os.path.expanduser("~")}


def _op_credential_scan(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """凭据可见性探测：逐路径报告"是否存在且可读"

    ``derivation`` 区分两类路径（**这是"如实标注"的关键**）：

    - ``env_derived``：**在本进程内**由 `HOME`/`USERPROFILE` 推出（如
      ``$HOME/.ssh/id_rsa``）。`env_mode=replace` 把两者置空 ⇒ 路径根本**推导不出来**
      ⇒ 子进程与容器都报告"不可见"。这是"清空环境"这一手段**真正**买到的边界。
    - ``absolute``：调用方给出的**宿主绝对路径**。只有**内核级**边界（容器的挂载
      命名空间）才挡得住它 —— 故容器"不可见"、强隔离子进程"可见"。
      这一格差距必须原样进对比表，不许用 env_derived 的结果去冒充。
    """
    entries: List[Dict[str, Any]] = []
    for item in (params.get("paths") or []):
        if isinstance(item, dict):
            derivation = str(item.get("derivation") or "absolute")
            path = str(item.get("path") or "")
            suffix = str(item.get("suffix") or "")
        else:
            derivation, path, suffix = "absolute", str(item), ""
        entry: Dict[str, Any] = {"derivation": derivation}
        if derivation == "env_derived":
            base = str(os.environ.get("HOME") or os.environ.get("USERPROFILE") or "")
            if not base:
                entry.update({
                    "path": suffix or "<未推导>", "visible": False,
                    "reason": "HOME 与 USERPROFILE 均为空 ⇒ 凭据路径不可推导"
                              "（清空环境买到的边界）"})
                entries.append(entry)
                continue
            path = os.path.join(base, suffix).replace("\\", "/")
        entry["path"] = path
        try:
            resolved = env.readable(path) if env.probe_mode else path
            with open(resolved, "rb") as fh:
                fh.read(1)
            entry.update({"visible": True, "reason": "可读"})
        except EscapeBlocked as exc:
            entry.update({"visible": False, "reason": f"路径守卫拒绝: {exc.detail}"})
        except OSError as exc:
            entry.update({"visible": False, "reason": f"{type(exc).__name__}: {exc}"})
        entries.append(entry)
    return {"ok": True, "entries": entries,
            "visible_count": sum(1 for e in entries if e.get("visible"))}


def _op_net_probe(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """协作式网络探测（**遵守作业网络策略**，故测的是"策略是否生效"）"""
    host = str(params.get("host") or "1.1.1.1")
    port = int(params.get("port") or 53)
    timeout = float(params.get("timeout_s") or 2.0)
    if env.network == "none":
        return {"ok": True, "reachable": False, "enforced_by": "policy",
                "host": host, "port": port,
                "reason": "network=none：协作式策略直接拒绝"}
    if env.network == "whitelist" and f"{host}:{port}" not in env.network_allow:
        return {"ok": True, "reachable": False, "enforced_by": "policy",
                "host": host, "port": port,
                "reason": f"不在白名单 {env.network_allow}"}
    return _raw_connect(host, port, timeout, enforced_by="policy_allow")


def _op_net_probe_raw(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """**无视策略**的真实连接尝试 —— 用它区分"内核级拒绝"与"协作式拒绝"

    这正是容器与强隔离子进程的关键差距所在：``--network none`` 下连接在
    内核/网络命名空间层面就出不去；子进程等级只能靠策略自觉。
    """
    if not env.probe_mode:
        raise EscapeBlocked("net_probe_raw 仅在 probe_mode=true 的作业中可用")
    host = str(params.get("host") or "1.1.1.1")
    port = int(params.get("port") or 53)
    timeout = float(params.get("timeout_s") or 2.0)
    return _raw_connect(host, port, timeout, enforced_by="none")


def _raw_connect(host: str, port: int, timeout: float, *,
                 enforced_by: str) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = round((time.perf_counter() - started) * 1000.0, 3)
            return {"ok": True, "reachable": True, "host": host, "port": port,
                    "elapsed_ms": elapsed, "enforced_by": enforced_by,
                    "reason": "连接成功"}
    except OSError as exc:
        elapsed = round((time.perf_counter() - started) * 1000.0, 3)
        return {"ok": True, "reachable": False, "host": host, "port": port,
                "elapsed_ms": elapsed, "enforced_by": enforced_by,
                "reason": f"{type(exc).__name__}: {exc}"}


def _op_escape_write(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """**绕过路径守卫**的真实越界写 —— 测量"边界到底是守卫还是内核"

    仅 `probe_mode` 可用。写入目标由探针harness 指定为它自己拥有的临时目录，
    故即便"越界成功"也不污染任何真实数据——它测的是**能不能**，不是"去破坏"。
    """
    if not env.probe_mode:
        raise EscapeBlocked("escape_write 仅在 probe_mode=true 的作业中可用")
    path = _norm(params.get("path"))
    payload = str(params.get("content") or "")
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(payload)
        return {"ok": True, "escaped": True, "path": path,
                "reason": "越界写入成功（本等级无内核级只读约束）"}
    except OSError as exc:
        return {"ok": True, "escaped": False, "path": path,
                "reason": f"{type(exc).__name__}: {exc}"}


def _op_mem_alloc(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """内存配额探测：尽力分配并**真的触碰**每一页（否则只是虚拟地址空间）

    ⚠️ 口径：Linux cgroup 内存超限是 **OOM kill（SIGKILL / 退出码 137）**，
    进程**来不及**抛 `MemoryError`。故：
    - ``memory_error=true`` ⇒ 分配器自己报了错（软限，如 RLIMIT_AS）；
    - 作业**根本没有结果**且外层看到 137/被 kill ⇒ 硬限生效（由执行器判定）；
    两者都记，绝不把"没测到"写成"没超限"。
    """
    mb = int(params.get("mb") or 256)
    blocks: List[bytearray] = []
    try:
        for _ in range(max(1, mb // 8)):
            block = bytearray(8 * 1024 * 1024)
            for offset in range(0, len(block), 4096):
                block[offset] = 1
            blocks.append(block)
        return {"ok": True, "allocated_mb": len(blocks) * 8,
                "touched": True, "memory_error": False}
    except MemoryError as exc:
        return {"ok": True, "allocated_mb": len(blocks) * 8,
                "touched": True, "memory_error": True,
                "reason": f"MemoryError: {exc}"}


def _op_cpu_spin(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """CPU/时间配额探测：忙转给定秒数（超时由外层 kill ⇒ 作业无结果即证据）"""
    seconds = float(params.get("seconds") or 1.0)
    started = time.perf_counter()
    while time.perf_counter() - started < seconds:
        pass
    return {"ok": True, "spun_s": round(time.perf_counter() - started, 3)}


def _op_fork_procs(env: WorkerEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    """进程数配额探测：连续 spawn 子进程并让它们**同时存活**，记录成功/失败

    被 spawn 者必须活得比 spawn 循环长，否则前面那些早就退出了，"同时存活数"
    永远达不到上限——那会把"限得太松"误报成"没有限制"。故 ``hold_s`` 默认 5s。
    """
    import subprocess  # 局部导入：只有该 op 需要

    count = int(params.get("count") or 8)
    hold_s = float(params.get("hold_s") or 5.0)
    spawned = 0
    failure = ""
    procs: List[Any] = []
    for _ in range(max(1, count)):
        try:
            proc = subprocess.Popen(
                [sys.executable, "-c", f"import time; time.sleep({hold_s})"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        except OSError as exc:
            failure = f"{type(exc).__name__}: {exc}"
            break
        procs.append(proc)
        spawned += 1
    alive = sum(1 for p in procs if p.poll() is None)
    for proc in procs:
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
    return {"ok": True, "spawned": spawned, "requested": count,
            "alive_at_peak": alive, "blocked": bool(failure), "reason": failure}


OPS: Dict[str, Any] = {
    "read_file": _op_read_file,
    "write_file": _op_write_file,
    "append_file": _op_append_file,
    "delete_file": _op_delete_file,
    "create_dir": _op_create_dir,
    "stat": _op_stat,
    "list_dir": _op_list_dir,
    "grep": _op_grep,
    "external_call": _op_external_call,
    "env_dump": _op_env_dump,
    "credential_scan": _op_credential_scan,
    "net_probe": _op_net_probe,
    "net_probe_raw": _op_net_probe_raw,
    "escape_write": _op_escape_write,
    "mem_alloc": _op_mem_alloc,
    "cpu_spin": _op_cpu_spin,
    "fork_procs": _op_fork_procs,
}


# ════════════════════════════════════════════════════════════
#  作业执行
# ════════════════════════════════════════════════════════════


def _seed_fixtures(env: WorkerEnv, fixtures: Any) -> None:
    """把作业声明的夹具写进唯一可写根（**只写 work_root，越界即拒绝**）

    与 `ReplayEnv(fixtures=...)` 同源：回放通道的读操作有夹具兜底，隔离执行若
    没有，就会因为"文件不存在"而与回放产生**系统性差异**——那时比对失败的
    是夹具，不是候选。
    """
    if not isinstance(fixtures, dict):
        if fixtures:
            env.notes.append("fixtures 字段非法（非 dict）⇒ 未播种")
        return
    for raw_path, content in fixtures.items():
        try:
            target = env.writable(raw_path)
            parent = os.path.dirname(target)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(str(content if content is not None else ""))
        except EscapeBlocked as exc:
            env.notes.append(f"夹具越界未播种: {exc.detail}")
        except OSError as exc:
            env.notes.append(f"夹具写入失败: {type(exc).__name__}: {exc}")


def run_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """执行一个隔离作业，返回结果（**永不抛**；异常转成 `error` 状态）"""
    started = time.perf_counter()
    env = WorkerEnv(job)
    # ① 先留证：平台**原始**环境（未 scrub 前）——容器里 Docker/runc 会按 passwd
    #    条目把 HOME 覆写成 /root 或 /nonexistent，`-e HOME=` 挡不住（实测）。
    #    把原始值单独留档，才谈得上"如实标注"而不是"看起来已经清空了"。
    env.platform_env_raw = {name: str(os.environ.get(name, ""))
                            for name in ISOLATION_ENV_WATCH}
    # ② 再清空：与子进程路径同口径的「存在但为空」（S4-04 同款做法）。
    #    **默认 False**：清空 `os.environ` 是有破坏性的动作，必须由调用方显式要求
    #    （隔离执行器的 `_job_for()` 两个等级都会显式置 True）。默认 True 会让
    #    "直接 import 本模块跑一跑"的调用方把自己的环境悄悄清掉——那种副作用
    #    不该藏在默认值里。
    if bool(job.get("scrub_env", False)):
        for name in ISOLATION_ENV_WATCH:
            os.environ[name] = ""
    try:
        env._qint("max_steps")
    except Exception:  # noqa: BLE001  配额字段非法不至于让作业崩
        env.quota = dict(DEFAULT_QUOTA)
    # ③ 播种夹具：与回放通道的 `ReplayEnv(fixtures=...)` 同源。**没有这一步，
    #    隔离执行里的读操作会因为"文件不存在"而失败**，比对就会在"两边都恰好
    #    记成 success"这种巧合上通过——那不是等价，是巧合。
    _seed_fixtures(env, job.get("fixtures"))
    for raw in (job.get("steps") or []):
        step = dict(raw or {})
        op = str(step.get("op") or step.get("label") or "").strip()
        params = dict(step.get("params") or {})
        if not op:
            env.status, env.error_code = STATUS_ERROR, ERR_BAD_JOB
            env.error = "步骤缺少 op"
            break
        env.steps.append(op)
        try:
            env._charge("steps")
            handler = OPS.get(op)
            if handler is None:
                env.outputs.append({"ok": True, "simulated": op})
                continue
            result = handler(env, params)
            env.outputs.append(dict(result))
            if not result.get("ok", True) and env.status == STATUS_SUCCESS:
                # 单步失败不必然终止作业：候选实现可能继续走下一步
                pass
        except QuotaExceeded as exc:
            env.status, env.error_code = STATUS_QUOTA_EXCEEDED, exc.code
            env.error = exc.detail
            env.outputs.append({"ok": False, "error_code": exc.code,
                                "error": exc.detail})
            break
        except EscapeBlocked as exc:
            env.status, env.error_code = STATUS_ESCAPE_BLOCKED, ERR_ESCAPE
            env.error = exc.detail
            env.outputs.append({"ok": False, "error_code": ERR_ESCAPE,
                                "error": exc.detail})
            break
        except Exception as exc:  # noqa: BLE001  单步异常 ⇒ 记 error 并继续
            env.outputs.append({"ok": False,
                                "error_code": type(exc).__name__,
                                "error": str(exc)})
        env.check_time()
    duration_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return {
        "job_id": env.job_id,
        "status": env.status,
        "error_code": env.error_code,
        "error": env.error,
        "steps": list(env.steps),
        "outputs": list(env.outputs),
        "side_effects": {k: list(v) for k, v in env.side_effects.items()},
        "duration_ms": duration_ms,
        "env_raw": dict(env.platform_env_raw),
        "notes": list(env.notes),
        "platform": {"sys_platform": sys.platform,
                     "pid": os.getpid(),
                     "uid": _safe_uid(),
                     "cwd": _norm(os.getcwd()),
                     "python": sys.version.split()[0]},
        "boundaries": {
            "work_root": env.work_root,
            "source_root": env.source_root,
            "network": env.network,
            "probe_mode": env.probe_mode,
        },
    }


def _safe_uid() -> Optional[int]:
    """当前进程 uid（Windows 无此概念 ⇒ ``None``，**不猜 0**）

    用 `getattr` 而不是 `os.getuid()` + `type: ignore`：在 Windows 上
    `os.getuid` 根本不存在，写死 `os.getuid()` 会让 mypy 需要一条忽略注释，
    而忽略注释正是"类型债悄悄长出来"的入口。取不到就是取不到，返回 `None`，
    由对比表如实显示"Windows 无 uid 概念"。
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return None
    value = getuid()
    return int(value) if isinstance(value, int) else None


def main(argv: Optional[List[str]] = None) -> int:
    # 结果 JSON 一律以 UTF-8 写出：Windows 下 stdout 被重定向时默认编码可能是
    # cp936（GBK），非 ASCII 理由文本会被写成 GBK 而调用方按 UTF-8 解码 ⇒ 乱码。
    # 这是 S5-01「Windows 与 Linux 语义不同」教训的同族问题，故在此显式钉死。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001  老解释器无 reconfigure ⇒ 保持原样
        pass
    args = list(sys.argv[1:] if argv is None else argv)
    job_arg = "-"
    out_arg = "-"
    if "--job" in args:
        job_arg = args[args.index("--job") + 1]
    if "--out" in args:
        out_arg = args[args.index("--out") + 1]
    try:
        if job_arg == "-":
            raw = sys.stdin.read()
        else:
            with open(job_arg, "r", encoding="utf-8") as fh:
                raw = fh.read()
        job = json.loads(raw or "{}")
    except Exception as exc:  # noqa: BLE001  作业本身不合法 ⇒ 如实报错
        payload = {"job_id": "", "status": STATUS_ERROR, "error_code": ERR_BAD_JOB,
                   "error": f"{type(exc).__name__}: {exc}", "steps": [],
                   "outputs": [], "side_effects": {k: [] for k in SIDE_EFFECT_KINDS},
                   "duration_ms": 0.0,
                   "traceback": traceback.format_exc()[-2000:]}
        _emit(payload, out_arg)
        return 2
    payload = run_job(job)
    _emit(payload, out_arg)
    return 0


def _emit(payload: Dict[str, Any], out_arg: str) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    if out_arg and out_arg != "-":
        with open(out_arg, "w", encoding="utf-8") as fh:
            fh.write(text)
    sys.stdout.write(f"\n{RESULT_BEGIN}\n{text}\n{RESULT_END}\n")
    sys.stdout.flush()


if __name__ == "__main__":  # pragma: no cover  容器/子进程入口
    raise SystemExit(main())
