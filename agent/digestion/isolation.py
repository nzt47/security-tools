"""灰度执行隔离（TASK-S8-03 步骤 1/2/3 / v7.2 §5.1 · §5.2）

**它解决什么问题**：S3-02 的回放沙箱是**进程内确定性执行模型**——它能判"行为是否
等价"，但**不能**拿来跑不可信的生成代码。于是 shadow 灰度只能"记录选中"而不能
"真实接管"（`real_takeover=false`）。本模块补齐**候选执行的安全环境**，从而解锁
真实接管，并且**默认仍然关闭**。

## 三档隔离等级（本模块的核心词表）

| 等级 | 何时生效 | 边界性质 |
|---|---|---|
| ``in_process`` | 显式要求，或容器与子进程均不可用 | **无执行隔离**——沿用 S3-02 进程内回放模型，**拒绝** `real_takeover` |
| ``subprocess_hardened`` | 有 Python 子进程能力但无 Docker（Windows 开发机/无 Docker 的 CI） | **进程级 + 环境级**隔离，**无内核级隔离**（无 namespace/cgroup） |
| ``container`` | Docker CLI 与 daemon 均可用 | **内核级**隔离（namespace + cgroup + 只读挂载 + 非 root） |

⚠️ **诚实底线**：`subprocess_hardened` **不是**容器。它既没有独立的文件系统视图，
也没有内核级网络拒绝与内存上限。本模块把这一差距写成**机器可读的字段**
（`BoundarySpec.not_guaranteed`）并让探针**实测**它（`net_probe_raw` /
`escape_write`），而不是靠文档口径含糊过去。

## 与既有设施的关系（不自建第二套）

- **判定与比对**：三层比对、`ReplayEnv`、`RecordReplayJournal` 全部沿用 S3-02
  （`sandbox.py`）；本模块只负责"把候选送进哪个边界里跑"。
- **环境清空范式**：子进程路径复用 S4-04 已验证的 `apply_isolation_env`
  （`agent/subagent/sandbox.py`，`env_mode=replace` 语义），函数体内懒加载。
- **出域控制**：候选的每一次外部调用都记成 `external_calls` 副作用并**永不真实
  外发**；真实调用点仍归 `agent.guardrails.egress_guard`（S4-02）。
- **自愈**：连续失败回落时开事故卡走 `agent.self_healing.levels.raise_incident`
  （在 `takeover.py` 侧接线，本模块不碰）。

## record-and-replay 语义**不变**

隔离执行**不引入**任何"真实落盘/真实外发"通道：

1. 被隔离进程唯一可写根是 **临时工作目录**（容器内是 tmpfs，宿主上是 `tempfile`
   建的临时目录，用后即删）；
2. 源码树对容器是**只读挂载**，对子进程是**协作式路径守卫**（其差距由探针如实暴露）；
3. 副作用一律只进 `side_effects` 记账；
4. `snapshot_paths()/diff_snapshot()` 供调用方在运行前后**实测**真实环境未被改动。

故"副作用只记录不双写"在两条路径上都成立，且可被用例断言。

## import 纪律

模块级只依赖标准库与同包叶子（`cases`/`sandbox` 的常量）；`agent.subagent.sandbox`
（环境清空范式）与 `agent.self_healing`（事故卡）一律**函数体内懒加载**，故
`import agent.digestion.isolation` 无文件/DB/网络副作用（唯一副作用是显式调用
`probe_docker()` 时的一次 `docker info`，且带 TTL 缓存）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.digestion.isolation")

# ════════════════════════════════════════════════════════════
#  隔离等级词表
# ════════════════════════════════════════════════════════════

ISOLATION_IN_PROCESS = "in_process"
ISOLATION_SUBPROCESS_HARDENED = "subprocess_hardened"
ISOLATION_CONTAINER = "container"
ISOLATION_LEVELS: Tuple[str, ...] = (ISOLATION_IN_PROCESS,
                                     ISOLATION_SUBPROCESS_HARDENED,
                                     ISOLATION_CONTAINER)
#: 等级强弱序（用于"降级"判定与对比表排序；**不用于冒充**：数值高只表示边界更强）
ISOLATION_RANK: Dict[str, int] = {ISOLATION_IN_PROCESS: 0,
                                  ISOLATION_SUBPROCESS_HARDENED: 1,
                                  ISOLATION_CONTAINER: 2}

#: 请求 `auto` = 按探测结果择优
ISOLATION_REQUEST_AUTO = "auto"
#: 兼容别名（显式关闭隔离的常见写法）
ISOLATION_REQUEST_OFF = "off"
ISOLATION_REQUEST_ALIASES: Dict[str, str] = {
    "": ISOLATION_REQUEST_AUTO,
    "auto": ISOLATION_REQUEST_AUTO,
    "off": ISOLATION_IN_PROCESS,
    "none": ISOLATION_IN_PROCESS,
    "in_process": ISOLATION_IN_PROCESS,
    "in-process": ISOLATION_IN_PROCESS,
    "process": ISOLATION_IN_PROCESS,
    "subprocess": ISOLATION_SUBPROCESS_HARDENED,
    "subprocess_hardened": ISOLATION_SUBPROCESS_HARDENED,
    "hardened": ISOLATION_SUBPROCESS_HARDENED,
    "container": ISOLATION_CONTAINER,
    "docker": ISOLATION_CONTAINER,
}

#: 等级来源（进报告，让"为什么是这一档"可解释）
LEVEL_SOURCE_DEFAULT = "auto_probe"
LEVEL_SOURCE_ENV = "env"
LEVEL_SOURCE_ARG = "argument"
LEVEL_SOURCE_FALLBACK = "honest_downgrade"

#: 探测结果缓存
ENV_PROBE_TTL = "CP_DIGESTION_ISOLATION_PROBE_TTL_S"
DEFAULT_PROBE_TTL_S = 60.0

# ── 环境变量（全部 `CP_DIGESTION_ISOLATION_*`，非法值回退默认） ──
ENV_LEVEL = "CP_DIGESTION_ISOLATION_LEVEL"
ENV_DOCKER_CLI = "CP_DIGESTION_ISOLATION_DOCKER_CLI"
ENV_DOCKER_IMAGE = "CP_DIGESTION_ISOLATION_DOCKER_IMAGE"
ENV_NETWORK = "CP_DIGESTION_ISOLATION_NETWORK"
ENV_NETWORK_ALLOW = "CP_DIGESTION_ISOLATION_NETWORK_ALLOW"
ENV_MEMORY_MB = "CP_DIGESTION_ISOLATION_MEMORY_MB"
ENV_CPUS = "CP_DIGESTION_ISOLATION_CPUS"
ENV_PIDS_LIMIT = "CP_DIGESTION_ISOLATION_PIDS_LIMIT"
ENV_TMPFS_MB = "CP_DIGESTION_ISOLATION_TMPFS_MB"
ENV_CONTAINER_USER = "CP_DIGESTION_ISOLATION_CONTAINER_USER"
ENV_TIMEOUT_S = "CP_DIGESTION_ISOLATION_TIMEOUT_S"
ENV_MAX_OUTPUT_BYTES = "CP_DIGESTION_ISOLATION_MAX_OUTPUT_BYTES"
ENV_SOURCE_MOUNT = "CP_DIGESTION_ISOLATION_SOURCE_MOUNT"
ENV_WORK_DIR = "CP_DIGESTION_ISOLATION_WORK_DIR"
ENV_KEEP_WORK_DIR = "CP_DIGESTION_ISOLATION_KEEP_WORK_DIR"

#: 网络模式
NETWORK_NONE = "none"           # 容器：`--network none`（内核级拒绝）
NETWORK_WHITELIST = "whitelist"  # 容器：默认 bridge + 应用层白名单（**非内核级**）
NETWORK_MODES: Tuple[str, ...] = (NETWORK_NONE, NETWORK_WHITELIST)

#: 容器默认镜像（**本地已存在优先**；生产可换内部镜像）
DEFAULT_DOCKER_IMAGE = "python:3.12-slim"
DEFAULT_DOCKER_CLI = "docker"
#: 非 root 用户（`nobody`）；**不允许** root，也不允许 --privileged
DEFAULT_CONTAINER_USER = "65534:65534"
#: 容器启动/回收的固定开销余量（`docker run` 到进程就绪、以及 kill 后的 rm）
CONTAINER_STARTUP_ALLOWANCE_S = 15.0

#: 容器内代表"边界之外"的绝对路径
#:
#: 【为什么必须有它】越界探针要问的是"能不能写到边界外"。宿主那侧的真实路径
#: （如 ``C:\Windows\TEMP\...``）在容器里**根本不是绝对路径**（Linux 下没有盘符
#: 概念），会被当成相对路径落到 ``/work`` 里 —— 于是探针会误报"越界成功"，
#: 而实际上什么也没越出去。故两条路径各自给出**同义**的"边界外"：
#: 子进程用真实宿主临时目录，容器用这个未被挂载的绝对路径（根只读 ⇒ 必然失败）。
CONTAINER_OUTSIDE_ROOT = "/host-secrets"

#: 源码只读探针的落点目录（调用方创建并在用后删除；已入 .gitignore）
SOURCE_PROBE_DIRNAME = ".tmp_iso_ro_probe"

#: 默认配额（与 `SandboxQuota` 口径对齐；内存/CPU/进程数为容器等级新增维度）
DEFAULT_MEMORY_MB = 256
DEFAULT_CPUS = 1.0
DEFAULT_PIDS_LIMIT = 64
DEFAULT_TMPFS_MB = 32
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_OUTPUT_BYTES = 65536

#: 隔离执行体的状态词表（与 `isolation_worker.py` 逐字对齐）
STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_QUOTA_EXCEEDED = "quota_exceeded"
STATUS_DENIED = "denied"
STATUS_ESCAPE_BLOCKED = "escape_blocked"
STATUS_TIMEOUT = "timeout"
STATUS_KILLED = "killed"
#: 执行器级状态（**不在** worker 词表内：进程都没起来）
STATUS_NOT_RUN = "not_run"
STATUS_REFUSED = "refused"

ERR_TIMEOUT = "E_ISOLATION_TIMEOUT"
ERR_KILLED = "E_ISOLATION_KILLED"
ERR_SPAWN = "E_ISOLATION_SPAWN_FAILED"
ERR_NO_RESULT = "E_ISOLATION_NO_RESULT"
ERR_REFUSED = "E_ISOLATION_REFUSED"
ERR_DOCKER = "E_ISOLATION_DOCKER_UNAVAILABLE"

#: worker 结果标记（与 `isolation_worker.py` 逐字对齐）
RESULT_BEGIN = "===ISOLATION_RESULT_BEGIN==="
RESULT_END = "===ISOLATION_RESULT_END==="

#: 隔离执行体的 op 词表（与 `isolation_worker.OPS` 逐字对齐；用例双向断言）
EXEC_OPS: Tuple[str, ...] = (
    "read_file", "write_file", "append_file", "delete_file", "create_dir",
    "stat", "list_dir", "grep", "external_call", "env_dump",
    "credential_scan", "net_probe", "net_probe_raw", "escape_write",
    "mem_alloc", "cpu_spin", "fork_procs",
)
#: 仅探针可用（候选作业拿不到；见 worker 的 probe_mode 门）
PROBE_ONLY_OPS: Tuple[str, ...] = ("net_probe_raw", "escape_write",
                                   "credential_scan", "mem_alloc",
                                   "cpu_spin", "fork_procs", "env_dump")

#: worker 脚本相对仓库根的位置
WORKER_REL_PATH = os.path.join("agent", "digestion", "isolation_worker.py")
#: 仓库根（容器只读挂载源）
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 探针类别
PROBE_ENV = "env"
PROBE_CREDENTIALS = "credentials"
PROBE_FILES = "files"
PROBE_NETWORK = "network"
PROBE_MEMORY = "memory"
PROBE_CPU = "cpu"
PROBE_PIDS = "pids"
PROBE_KINDS: Tuple[str, ...] = (PROBE_ENV, PROBE_CREDENTIALS, PROBE_FILES,
                                PROBE_NETWORK, PROBE_MEMORY, PROBE_CPU,
                                PROBE_PIDS)


# ════════════════════════════════════════════════════════════
#  环境读取（非法值一律回退默认，不抛不静默）
# ════════════════════════════════════════════════════════════


def _env(env: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    return dict(os.environ if env is None else env)


def _env_flag(name: str, default: bool = False,
              env: Optional[Dict[str, str]] = None) -> bool:
    raw = str(_env(env).get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    logger.warning("%s=%r 非法布尔值，回退默认 %s", name, raw, default)
    return bool(default)


def _env_int(name: str, default: int, env: Optional[Dict[str, str]] = None,
             *, minimum: int = 0) -> int:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return int(default)
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法整数，回退默认 %s", name, raw, default)
        return int(default)
    if value < minimum:
        logger.warning("%s=%s 小于下界 %s，回退默认 %s", name, value, minimum, default)
        return int(default)
    return value


def _env_float(name: str, default: float, env: Optional[Dict[str, str]] = None,
               *, minimum: float = 0.0) -> float:
    raw = str(_env(env).get(name, "") or "").strip()
    if not raw:
        return float(default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning("%s=%r 非法数值，回退默认 %s", name, raw, default)
        return float(default)
    if value < minimum:
        logger.warning("%s=%s 小于下界 %s，回退默认 %s", name, value, minimum, default)
        return float(default)
    return value


def normalize_level(value: Any) -> Optional[str]:
    """任意写法 → 标准等级名（不可识别返回 ``None``，**不猜**）"""
    key = str(value or "").strip().lower()
    if key in ISOLATION_REQUEST_ALIASES:
        mapped = ISOLATION_REQUEST_ALIASES[key]
        return None if mapped == ISOLATION_REQUEST_AUTO else mapped
    return None


# ════════════════════════════════════════════════════════════
#  Docker 探测
# ════════════════════════════════════════════════════════════


@dataclass
class DockerProbe:
    """Docker 可用性探测结果（**机器可读证据**，进报告与验收表）"""

    cli: str = DEFAULT_DOCKER_CLI
    image: str = DEFAULT_DOCKER_IMAGE
    cli_available: bool = False
    daemon_available: bool = False
    server_version: str = ""
    image_available: bool = False
    reasons: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        """容器等级可用 ⇔ CLI + daemon 都在（镜像缺失可拉取，只记提示不降级）"""
        return bool(self.cli_available and self.daemon_available)

    def to_dict(self) -> Dict[str, Any]:
        return {"cli": self.cli, "image": self.image,
                "cli_available": self.cli_available,
                "daemon_available": self.daemon_available,
                "server_version": self.server_version,
                "image_available": self.image_available,
                "available": self.available,
                "reasons": list(self.reasons), "detail": dict(self.detail)}


_PROBE_CACHE: Dict[str, Tuple[float, DockerProbe]] = {}


def reset_isolation_probe_cache() -> None:
    """清空探测缓存（用例与演示须显式调用，避免互相污染）"""
    _PROBE_CACHE.clear()


def probe_docker(*, env: Optional[Dict[str, str]] = None, cli: str = "",
                 image: str = "", timeout_s: float = 15.0,
                 refresh: bool = False) -> DockerProbe:
    """探测 Docker CLI 与 daemon（**永不抛**；失败即"不可用"并给出理由）

    两次独立探测：`<cli> --version`（CLI 是否存在）与 `<cli> info`（daemon 是否
    在跑）。**必须分开**——"装了 CLI 但 daemon 没起"是开发机最常见状态，把它当成
    "容器可用"正是任务书 §五 禁止的"冒充容器"。镜像缺失只记提示（可拉取），
    不作为降级理由。

    ⚠️ **实测注意（15s 超时的由来）**：Docker Desktop 刚跑完一轮容器、daemon
    正忙时，`docker info` 可能超过 8s——那会让 `auto` 在"容器"与"子进程"之间
    抖动。超时阈值因此放宽，但**抖动方向是保守的**（判不可用 ⇒ 退到子进程等级
    或直接拒绝），绝不会把探测超时当成"容器可用"。
    """
    env_map = _env(env)
    cli_name = str(cli or env_map.get(ENV_DOCKER_CLI) or DEFAULT_DOCKER_CLI).strip()
    image_name = str(image or env_map.get(ENV_DOCKER_IMAGE)
                     or DEFAULT_DOCKER_IMAGE).strip()
    ttl = _env_float(ENV_PROBE_TTL, DEFAULT_PROBE_TTL_S, env, minimum=0.0)
    cache_key = f"{cli_name}|{image_name}"
    now = time.time()
    if not refresh:
        cached = _PROBE_CACHE.get(cache_key)
        if cached is not None and (now - cached[0]) <= ttl:
            return cached[1]

    probe = DockerProbe(cli=cli_name, image=image_name)
    version = _run_quiet([cli_name, "--version"], timeout_s)
    probe.detail["version_rc"] = version[0]
    probe.detail["version_out"] = version[1][:200]
    if version[0] == 0 and version[1].strip():
        probe.cli_available = True
    else:
        probe.reasons.append(
            f"{cli_name} --version 不可用（rc={version[0]}）："
            f"{(version[2] or version[1]).strip()[:160] or '无输出'}"
            " ⇒ 无法使用容器等级（不冒充容器）")
        _PROBE_CACHE[cache_key] = (now, probe)
        return probe

    info = _run_quiet([cli_name, "info", "--format", "{{.ServerVersion}}"], timeout_s)
    probe.detail["info_rc"] = info[0]
    probe.detail["info_out"] = info[1][:200]
    if info[0] == 0 and info[1].strip():
        probe.daemon_available = True
        probe.server_version = info[1].strip().splitlines()[0][:64]
    else:
        probe.reasons.append(
            f"{cli_name} info 失败（daemon 未运行，rc={info[0]}）："
            f"{(info[2] or info[1]).strip()[:160] or '无输出'}"
            " ⇒ 容器等级不可用（不冒充容器）")

    if probe.daemon_available:
        listing = _run_quiet([cli_name, "image", "inspect", image_name,
                              "--format", "{{.Id}}"], timeout_s)
        probe.detail["image_rc"] = listing[0]
        if listing[0] == 0 and listing[1].strip():
            probe.image_available = True
        else:
            probe.reasons.append(
                f"镜像 {image_name} 本地不存在（可拉取，不因此降级）")
    _PROBE_CACHE[cache_key] = (now, probe)
    return probe


def _run_quiet(argv: Sequence[str], timeout_s: float) -> Tuple[int, str, str]:
    """跑一条探测命令（返回 rc/stdout/stderr；**任何异常都转成 rc=-1**）"""
    try:
        proc = subprocess.run(list(argv), capture_output=True, timeout=timeout_s)
    except FileNotFoundError as exc:
        return -1, "", f"FileNotFoundError: {exc}"
    except subprocess.TimeoutExpired:
        return -1, "", f"TimeoutExpired({timeout_s}s)"
    except Exception as exc:  # noqa: BLE001  探测绝不抛
        return -1, "", f"{type(exc).__name__}: {exc}"

    def _decode(raw: Any) -> str:
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw or "")

    return proc.returncode, _decode(proc.stdout), _decode(proc.stderr)


def docker_available(*, env: Optional[Dict[str, str]] = None) -> bool:
    """纯布尔便捷入口（`probe_docker` 的薄包装）"""
    return probe_docker(env=env).available


def subprocess_available() -> bool:
    """子进程能力探测（Python 有 `subprocess` 模块即可；`sys.executable` 须存在）"""
    return bool(sys.executable) and os.path.exists(sys.executable)


# ════════════════════════════════════════════════════════════
#  边界声明（"保证什么" / "不保证什么" —— 诚实清单）
# ════════════════════════════════════════════════════════════


@dataclass
class BoundarySpec:
    """一个隔离等级的边界声明（**机器可读的诚实清单**）"""

    level: str
    display: str
    kernel_isolation: bool
    container_isolated: bool
    guarantees: List[str] = field(default_factory=list)
    not_guaranteed: List[str] = field(default_factory=list)
    enforcement: Dict[str, str] = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "display": self.display,
                "kernel_isolation": self.kernel_isolation,
                "container_isolated": self.container_isolated,
                "guarantees": list(self.guarantees),
                "not_guaranteed": list(self.not_guaranteed),
                "enforcement": dict(self.enforcement), "note": self.note}


def isolation_boundaries(level: Any) -> BoundarySpec:
    """等级 → 边界声明（不可识别即按 `in_process` 处理，**保守**）"""
    normalized = normalize_level(level) or ISOLATION_IN_PROCESS
    if normalized == ISOLATION_CONTAINER:
        return BoundarySpec(
            level=ISOLATION_CONTAINER, display="容器（Docker，内核级隔离）",
            kernel_isolation=True, container_isolated=True,
            guarantees=[
                "根文件系统只读（--read-only），仅 tmpfs /work、/tmp 可写",
                "源码树只读挂载（-v <repo>:/src:ro）——候选改不动源码",
                "无宿主网络（--network none）：出域在内核/网络命名空间层面被拒",
                "内存/CPU/进程数硬上限（--memory/--cpus/--pids-limit，cgroup 强制）",
                "非 root 运行（--user 65534:65534），--cap-drop ALL + no-new-privileges",
                "宿主 $HOME / SSH agent / 云凭据既不挂载也不注入（独立挂载命名空间）",
                "被隔离进程看不到宿主文件系统（除显式只读源码挂载外）",
            ],
            not_guaranteed=[
                "不是虚拟机：与宿主共享内核，内核漏洞可逃逸（本任务不做 VM 级隔离）",
                "镜像供应链：按 tag 固定（python:3.12-slim），未按 digest 锁定",
                "未加载自定义 seccomp/AppArmor 策略（沿用 Docker 默认档）",
                "network=whitelist 时白名单是**应用层**协作策略，非内核级拒绝",
                "侧信道（计时/缓存）与 DoS 类攻击不在本任务范围",
                "Docker daemon 自身被攻陷时全部约束失效（信任边界在 daemon 之外）",
            ],
            enforcement={"env": "clean_namespace+explicit_empty",
                         "network": "kernel(netns)",
                         "filesystem": "readonly_mount+readonly_rootfs",
                         "memory": "cgroup", "cpu": "cgroup", "pids": "cgroup",
                         "user": "non_root_uid",
                         "credentials": "not_mounted"},
            note="唯一提供内核级隔离的等级；容器不可用时**不得**降格冒称。")

    if normalized == ISOLATION_SUBPROCESS_HARDENED:
        return BoundarySpec(
            level=ISOLATION_SUBPROCESS_HARDENED,
            display="强隔离子进程（进程级 + 环境级；**非**内核级）",
            kernel_isolation=False, container_isolated=False,
            guarantees=[
                "环境整体替换（env_mode=replace）：HOME/USERPROFILE/SSH_AUTH_SOCK "
                "等显式置空，宿主凭据类变量（AWS_*/GH_TOKEN/KUBECONFIG/…）一律删除",
                "代理出口清空（HTTP(S)_PROXY/ALL_PROXY/NO_PROXY），出域默认拒绝",
                "工作目录隔离：候选在一次性临时目录内运行，cwd 与宿主工作区无关",
                "源码树受协作式路径守卫保护：唯一可写根是临时工作目录",
                "墙钟超时 kill + stdout/stderr 截断上限",
                "POSIX 平台另有 RLIMIT_AS/CPU/NPROC/FSIZE 硬限（preexec 阶段设置）",
                "候选的每一次外部调用只记账、**永不真实外发**（record-and-replay）",
            ],
            not_guaranteed=[
                "**没有内核级隔离**：无 mount/net/pid namespace，无 cgroup",
                "宿主文件系统按**绝对路径**仍可读可写——路径守卫是协作式的，"
                "一个不理会守卫的二进制可以越界",
                "无内核级网络拒绝：只有代理清空 + 应用层策略，直连 socket 仍可能成功",
                "Windows 上没有 rlimit 等价物：内存/CPU/进程数**无硬上限**，"
                "只有墙钟超时兜底",
                "与宿主同用户、同会话：宿主凭据文件若被绝对路径寻址仍可读",
                "不做系统调用过滤（无 seccomp/AppArmor 等价物）",
                "因此**不得**用于执行来源不可信的第三方二进制，只用于"
                "云枢自己生成、已被判定集约束的候选实现",
            ],
            enforcement={"env": "env_replace",
                         "network": "policy(proxy_cleared)",
                         "filesystem": "path_guard(cooperative)",
                         "memory": "rlimit_posix|timeout_only_windows",
                         "cpu": "rlimit_posix|timeout_only_windows",
                         "pids": "rlimit_posix|none_windows",
                         "user": "same_user_as_host",
                         "credentials": "env_removed(absolute_path_still_readable)"},
            note="Windows 开发机无 Docker 时的**如实降级**目标：比进程内模型强，"
                 "但明确弱于容器。")

    return BoundarySpec(
        level=ISOLATION_IN_PROCESS, display="进程内确定性模型（**无**执行隔离）",
        kernel_isolation=False, container_isolated=False,
        guarantees=[
            "副作用只记录不双写（ReplayEnv.commit() 恒抛）",
            "出界路径即 SandboxEscapeError（虚拟文件系统根守卫）",
            "无墙钟、无随机、无真实 I/O ⇒ 同输入同结果（确定性可复现）",
        ],
        not_guaranteed=[
            "**不是**执行隔离环境：候选仍未在独立边界内运行",
            "无法承载真实代码执行 ⇒ 因此**拒绝** real_takeover",
        ],
        enforcement={"env": "none", "network": "none", "filesystem": "virtual_only",
                     "memory": "none", "cpu": "none", "pids": "none",
                     "user": "same_process", "credentials": "inherited"},
        note="S3-02/S3-03 的既有模型；本任务保持其语义不变，只把它标成"
             "「不足以接管真实流量」的最低档。",
    )


def boundary_table() -> List[Dict[str, Any]]:
    """三档边界对比表（文档/CLI/验收报告共用同一数据源，避免口径漂移）"""
    return [isolation_boundaries(level).to_dict() for level in ISOLATION_LEVELS]


# ════════════════════════════════════════════════════════════
#  等级解析（探测 + 选择 + **如实降级**）
# ════════════════════════════════════════════════════════════


@dataclass
class IsolationPlan:
    """隔离等级决议（进报告与审计；"为什么是这一档"可解释）"""

    requested: str = ISOLATION_REQUEST_AUTO
    level: str = ISOLATION_IN_PROCESS
    source: str = LEVEL_SOURCE_DEFAULT
    reasons: List[str] = field(default_factory=list)
    docker: Dict[str, Any] = field(default_factory=dict)
    #: 请求的等级是否被**如实降级**（True ⇒ 报告中必须出现降级理由）
    downgraded: bool = False

    @property
    def capable(self) -> bool:
        """是否具备承载 `real_takeover` 的执行边界"""
        return self.level != ISOLATION_IN_PROCESS

    @property
    def container_isolated(self) -> bool:
        return self.level == ISOLATION_CONTAINER

    @property
    def boundaries(self) -> Dict[str, Any]:
        return isolation_boundaries(self.level).to_dict()

    def to_dict(self) -> Dict[str, Any]:
        return {"requested": self.requested, "level": self.level,
                "source": self.source, "reasons": list(self.reasons),
                "downgraded": self.downgraded, "capable": self.capable,
                "container_isolated": self.container_isolated,
                "kernel_isolation": self.boundaries["kernel_isolation"],
                "display": self.boundaries["display"],
                "docker": dict(self.docker),
                "not_guaranteed": list(self.boundaries["not_guaranteed"])}


def resolve_isolation_level(*, requested: str = "",
                            env: Optional[Dict[str, str]] = None,
                            prober: Optional[Callable[..., DockerProbe]] = None,
                            source: str = LEVEL_SOURCE_ENV) -> IsolationPlan:
    """解析生效隔离等级（**探测 → 择优 → 不可用即如实降级**）

    规则（任务书 §步骤 1）：

    1. **显式请求优先**：`requested` > `CP_DIGESTION_ISOLATION_LEVEL` > `auto`。
    2. `auto` ⇒ Docker 可用取 `container`；否则子进程可用取 `subprocess_hardened`；
       两者皆不可用 ⇒ `in_process`（**并因此拒绝 real_takeover**）。
    3. **请求 container 但 Docker 不可用 ⇒ 如实降级到 subprocess_hardened**，
       `downgraded=True` 且理由入 `reasons`——**绝不冒称容器**。
    4. 非法取值 ⇒ 回退 `auto` 并说明（不静默）。
    """
    env_map = _env(env)
    raw_arg = str(requested or "").strip()
    raw_env = str(env_map.get(ENV_LEVEL, "") or "").strip()
    raw_request = raw_arg or raw_env
    plan = IsolationPlan(requested=raw_request or ISOLATION_REQUEST_AUTO,
                         source=LEVEL_SOURCE_ARG if raw_arg else LEVEL_SOURCE_ENV)

    key = str(raw_request).strip().lower()
    is_auto = (not raw_request) or key == ISOLATION_REQUEST_AUTO
    explicit = normalize_level(raw_request) if (raw_request and not is_auto) else None
    if raw_request and not is_auto and explicit is None:
        plan.reasons.append(
            f"{ENV_LEVEL}={raw_request!r} 非法（合法值：auto / "
            f"{' / '.join(ISOLATION_LEVELS)}）⇒ 回退 auto 走探测")
        plan.source = LEVEL_SOURCE_DEFAULT
        is_auto = True
    if not raw_request:
        plan.source = LEVEL_SOURCE_DEFAULT

    # 只在**真的需要**时探测：`auto` 要择优，显式 `container` 要验证 daemon。
    # 显式 `in_process`/`subprocess_hardened` 与探测结果无关，何必付一次
    # `docker info` 的代价？此时如实标注 `probed=False`（而不是伪造一个"不可用"）。
    need_probe = bool(is_auto or explicit == ISOLATION_CONTAINER)
    if need_probe:
        probe_runner = prober or probe_docker
        probe = probe_runner(env=env)
    else:
        probe = DockerProbe(
            cli=str(env_map.get(ENV_DOCKER_CLI) or DEFAULT_DOCKER_CLI),
            image=str(env_map.get(ENV_DOCKER_IMAGE) or DEFAULT_DOCKER_IMAGE),
            reasons=[f"显式等级 {explicit} 与 Docker 可用性无关 ⇒ 本次未探测"])
        probe.detail["probed"] = False
    plan.docker = probe.to_dict()
    plan.docker.setdefault("probed", need_probe)

    if not is_auto and explicit is not None:
        plan.level = explicit
        if explicit == ISOLATION_CONTAINER and not probe.available:
            plan.level = (ISOLATION_SUBPROCESS_HARDENED if subprocess_available()
                          else ISOLATION_IN_PROCESS)
            plan.downgraded = True
            plan.source = LEVEL_SOURCE_FALLBACK
            plan.reasons.append(
                "显式请求 container 但 Docker 不可用 ⇒ **如实降级**到 "
                f"{plan.level}（不冒称容器）：" + "；".join(probe.reasons))
        elif explicit == ISOLATION_SUBPROCESS_HARDENED and not subprocess_available():
            plan.level = ISOLATION_IN_PROCESS
            plan.downgraded = True
            plan.source = LEVEL_SOURCE_FALLBACK
            plan.reasons.append(
                "显式请求 subprocess_hardened 但子进程能力不可用（sys.executable "
                "缺失）⇒ 降级 in_process 并拒绝 real_takeover")
        else:
            plan.reasons.append(f"显式请求 {explicit}（来源 {plan.source}）")
        return plan

    # auto：容器 → 子进程 → 进程内
    if probe.available:
        plan.level = ISOLATION_CONTAINER
        plan.reasons.append(
            f"Docker 可用（server {probe.server_version or 'unknown'}）"
            f"⇒ 自动选择 container")
        if not probe.image_available:
            plan.reasons.append(
                f"镜像 {probe.image} 本地不存在（首次执行会拉取，"
                "不作为降级理由）")
        return plan
    if subprocess_available():
        plan.level = ISOLATION_SUBPROCESS_HARDENED
        plan.reasons.append(
            "Docker 不可用（" + "；".join(probe.reasons)
            + "）⇒ 自动降级到 subprocess_hardened（**非内核级隔离**，"
              "该差距见 not_guaranteed）")
        return plan
    plan.level = ISOLATION_IN_PROCESS
    plan.reasons.append(
        "Docker 与子进程能力均不可用 ⇒ 保持 in_process（现状）并**拒绝** "
        "real_takeover")
    return plan


def isolation_capability_report(*, env: Optional[Dict[str, str]] = None,
                                requested: str = "",
                                refresh: bool = False) -> Dict[str, Any]:
    """当前环境的隔离能力快照（供报告、`--doctor`、UI 如实展示）"""
    if refresh:
        reset_isolation_probe_cache()
    plan = resolve_isolation_level(requested=requested, env=env)
    return {"plan": plan.to_dict(), "boundaries": plan.boundaries,
            "levels": boundary_table(),
            "platform": {"sys_platform": sys.platform,
                         "python": sys.version.split()[0],
                         "executable": sys.executable}}


# ════════════════════════════════════════════════════════════
#  执行结果
# ════════════════════════════════════════════════════════════


@dataclass
class IsolationResult:
    """一次隔离执行的完整结果（**机器可读证据**；可 JSON 序列化）"""

    level: str = ISOLATION_IN_PROCESS
    job_id: str = ""
    ran: bool = False
    status: str = STATUS_NOT_RUN
    error_code: str = ""
    error: str = ""
    steps: List[str] = field(default_factory=list)
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    side_effects: Dict[str, List[str]] = field(default_factory=dict)
    duration_ms: float = 0.0
    wall_ms: float = 0.0
    quota_exceeded: bool = False
    killed: bool = False
    exit_code: Optional[int] = None
    command: List[str] = field(default_factory=list)
    isolation: Dict[str, Any] = field(default_factory=dict)
    refused_reason: str = ""
    stdout_tail: str = ""
    stderr_tail: str = ""
    honest_notes: List[str] = field(default_factory=list)
    #: 被隔离进程自报的平台信息（uid/pid/python/cwd）——「非 root 运行」的实测依据
    platform: Dict[str, Any] = field(default_factory=dict)
    #: 平台**原始**环境（scrub 前；只含被监视的那几个键）——与生效值并列，如实标注
    env_raw: Dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.ran and self.status == STATUS_SUCCESS

    @property
    def side_effect_set(self) -> Dict[str, List[str]]:
        return {k: sorted(set(v)) for k, v in self.side_effects.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "job_id": self.job_id, "ran": self.ran,
                "status": self.status, "ok": self.ok,
                "error_code": self.error_code, "error": self.error,
                "steps": list(self.steps), "outputs": list(self.outputs),
                "side_effects": self.side_effect_set,
                "duration_ms": round(self.duration_ms, 3),
                "wall_ms": round(self.wall_ms, 3),
                "quota_exceeded": self.quota_exceeded, "killed": self.killed,
                "exit_code": self.exit_code, "command": list(self.command),
                "isolation": dict(self.isolation),
                "refused_reason": self.refused_reason,
                "stdout_tail": self.stdout_tail, "stderr_tail": self.stderr_tail,
                "honest_notes": list(self.honest_notes),
                "platform": dict(self.platform),
                "env_raw": dict(self.env_raw)}


def _substitute_roots(value: Any, *, work_root: str, source_root: str,
                      outside_root: str = "") -> Any:
    """把作业里的 ``${work_root}`` / ``${source_root}`` / ``${outside_root}`` 换成实际路径

    **为什么需要占位符**：容器与子进程的工作根/源码根/边界外路径**天然不同**
    （容器内是 ``/work``、``/src``、``/host-secrets``；宿主机上是临时目录、仓库
    路径、宿主临时目录）。探针作业若写死路径，就会变成"两条路径各测各的"，
    对比表也就失去意义。占位符让**同一份探针定义**在两条路径上跑，故单元格可比。
    """
    if isinstance(value, str):
        return (value.replace("${work_root}", work_root)
                .replace("${source_root}", source_root)
                .replace("${outside_root}", outside_root))
    if isinstance(value, dict):
        return {k: _substitute_roots(v, work_root=work_root,
                                     source_root=source_root,
                                     outside_root=outside_root)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_substitute_roots(v, work_root=work_root,
                                  source_root=source_root,
                                  outside_root=outside_root)
                for v in value]
    return value


def parse_worker_result(stdout: str) -> Optional[Dict[str, Any]]:
    """从 stdout 里取 worker 结果 JSON（标记之外的内容一律不算结果）"""
    if RESULT_BEGIN not in stdout or RESULT_END not in stdout:
        return None
    body = stdout.split(RESULT_BEGIN, 1)[1].split(RESULT_END, 1)[0].strip()
    try:
        payload = json.loads(body)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _result_from_payload(payload: Dict[str, Any], *, level: str,
                         wall_ms: float, exit_code: Optional[int],
                         command: Sequence[str], isolation: Dict[str, Any],
                         ) -> IsolationResult:
    status = str(payload.get("status") or STATUS_ERROR)
    return IsolationResult(
        level=level, job_id=str(payload.get("job_id") or ""), ran=True,
        status=status, error_code=str(payload.get("error_code") or ""),
        error=str(payload.get("error") or ""),
        steps=[str(s) for s in (payload.get("steps") or [])],
        outputs=[dict(o) for o in (payload.get("outputs") or [])],
        side_effects={str(k): [str(x) for x in (v or [])]
                      for k, v in (payload.get("side_effects") or {}).items()},
        duration_ms=float(payload.get("duration_ms") or 0.0), wall_ms=wall_ms,
        quota_exceeded=(status == STATUS_QUOTA_EXCEEDED),
        killed=False, exit_code=exit_code, command=list(command),
        isolation=dict(isolation),
        platform={str(k): v for k, v in (payload.get("platform") or {}).items()},
        env_raw={str(k): str(v) for k, v in (payload.get("env_raw") or {}).items()})


# ════════════════════════════════════════════════════════════
#  执行器
# ════════════════════════════════════════════════════════════


@dataclass
class IsolationQuota:
    """隔离执行配额（内存/CPU/进程数为容器等级新增维度）"""

    memory_mb: int = DEFAULT_MEMORY_MB
    cpus: float = DEFAULT_CPUS
    pids_limit: int = DEFAULT_PIDS_LIMIT
    tmpfs_mb: int = DEFAULT_TMPFS_MB
    timeout_s: float = DEFAULT_TIMEOUT_S
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_steps: int = 32
    max_files_written: int = 16
    max_deleted: int = 16
    max_external_calls: int = 8
    max_bytes: int = 65536

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "IsolationQuota":
        return cls(
            memory_mb=_env_int(ENV_MEMORY_MB, DEFAULT_MEMORY_MB, env, minimum=16),
            cpus=_env_float(ENV_CPUS, DEFAULT_CPUS, env, minimum=0.1),
            pids_limit=_env_int(ENV_PIDS_LIMIT, DEFAULT_PIDS_LIMIT, env, minimum=8),
            tmpfs_mb=_env_int(ENV_TMPFS_MB, DEFAULT_TMPFS_MB, env, minimum=4),
            timeout_s=_env_float(ENV_TIMEOUT_S, DEFAULT_TIMEOUT_S, env, minimum=0.5),
            max_output_bytes=_env_int(ENV_MAX_OUTPUT_BYTES, DEFAULT_MAX_OUTPUT_BYTES,
                                      env, minimum=1024),
        )

    def worker_quota(self) -> Dict[str, Any]:
        return {"max_steps": self.max_steps,
                "max_files_written": self.max_files_written,
                "max_deleted": self.max_deleted,
                "max_external_calls": self.max_external_calls,
                "max_bytes": self.max_bytes,
                "timeout_s": self.timeout_s}

    def to_dict(self) -> Dict[str, Any]:
        return {"memory_mb": self.memory_mb, "cpus": self.cpus,
                "pids_limit": self.pids_limit, "tmpfs_mb": self.tmpfs_mb,
                "timeout_s": self.timeout_s,
                "max_output_bytes": self.max_output_bytes,
                "worker_quota": self.worker_quota()}


class IsolationExecutor:
    """隔离执行器基类（所有等级共用同一 `run(job) -> IsolationResult` 契约）"""

    level: str = ISOLATION_IN_PROCESS

    def __init__(self, *, quota: Optional[IsolationQuota] = None,
                 env: Optional[Dict[str, str]] = None,
                 network: str = "", network_allow: Sequence[str] = (),
                 source_root: str = "", work_dir: str = "",
                 keep_work_dir: bool = False,
                 outside_root: str = "") -> None:
        self.env = dict(env or {})
        self.quota = quota or IsolationQuota.from_env(self.env or None)
        self.network = str(network or self.env.get(ENV_NETWORK)
                           or NETWORK_NONE).strip().lower()
        if self.network not in NETWORK_MODES:
            logger.warning("%s=%r 非法网络模式，回退 %s",
                           ENV_NETWORK, self.network, NETWORK_NONE)
            self.network = NETWORK_NONE
        raw_allow = self.env.get(ENV_NETWORK_ALLOW, "")
        self.network_allow = [str(x).strip() for x in (network_allow or [])] or \
            [x.strip() for x in str(raw_allow or "").split(",") if x.strip()]
        self.source_root = str(source_root or self.env.get(ENV_SOURCE_MOUNT)
                               or REPO_ROOT)
        self.work_dir = str(work_dir or self.env.get(ENV_WORK_DIR) or "")
        self.keep_work_dir = bool(keep_work_dir
                                  or _env_flag(ENV_KEEP_WORK_DIR, False, self.env))
        self._outside_root = str(outside_root or "")

    # ── 公共 ────────────────────────────────────────────────

    @property
    def outside_root(self) -> str:
        """本等级视角下"边界之外"的绝对路径（越界探针的落点；见模块常量注释）"""
        return self._outside_root

    @property
    def worker_path(self) -> str:
        return os.path.join(self.source_root, WORKER_REL_PATH)

    def describe(self) -> Dict[str, Any]:
        return {"level": self.level, "quota": self.quota.to_dict(),
                "network": self.network, "network_allow": list(self.network_allow),
                "source_root": self.source_root,
                "worker": self.worker_path,
                "boundaries": isolation_boundaries(self.level).to_dict()}

    def _make_work_dir(self) -> str:
        if self.work_dir:
            os.makedirs(self.work_dir, exist_ok=True)
            return self.work_dir
        return tempfile.mkdtemp(prefix="cp-iso-")

    def _cleanup(self, work_dir: str) -> None:
        if self.keep_work_dir or self.work_dir:
            return
        shutil.rmtree(work_dir, ignore_errors=True)

    def _job_for(self, job: Dict[str, Any], *, work_root: str,
                 source_root: str, outside_root: str = "") -> Dict[str, Any]:
        payload = dict(job or {})
        payload.setdefault("job_id", f"job-{int(time.time() * 1000)}")
        payload["work_root"] = work_root
        payload["source_root"] = source_root
        payload.setdefault("network", self.network)
        payload.setdefault("network_allow", list(self.network_allow))
        #: 执行体内统一清空 HOME/USERPROFILE/SSH_AUTH_SOCK 等（"存在但为空"），
        #: 与子进程路径的 env_mode=replace 同口径；平台**原始**值另存 `env_raw`，
        #: 故"清了什么"与"平台本来给的是什么"两件事都能被审计。
        payload.setdefault("scrub_env", True)
        quota = dict(self.quota.worker_quota())
        quota.update(dict(payload.get("quota") or {}))
        payload["quota"] = quota
        rendered = _substitute_roots(payload, work_root=work_root,
                                     source_root=source_root,
                                     outside_root=outside_root or self.outside_root)
        return rendered if isinstance(rendered, dict) else dict(payload)

    def run(self, job: Dict[str, Any]) -> IsolationResult:  # pragma: no cover
        raise NotImplementedError


class InProcessExecutor(IsolationExecutor):
    """进程内等级执行器：**拒绝**真实接管（fail-closed，不静默降格执行）"""

    level = ISOLATION_IN_PROCESS

    def run(self, job: Dict[str, Any]) -> IsolationResult:
        reason = ("隔离等级为 in_process（无执行隔离环境）⇒ 拒绝在边界外执行候选；"
                  "启用 real_takeover 需要 container 或 subprocess_hardened。"
                  "候选等价性判定请走 ReplaySandbox 的进程内回放通道。")
        return IsolationResult(
            level=self.level, job_id=str((job or {}).get("job_id") or ""),
            ran=False, status=STATUS_REFUSED, error_code=ERR_REFUSED,
            error=reason, refused_reason=reason,
            isolation={"level": self.level, "refused": True},
            honest_notes=["in_process 不提供任何执行隔离，故本执行器永不执行作业"])


class SubprocessHardenedExecutor(IsolationExecutor):
    """强隔离子进程执行器（S4-04 范式的执行侧复用；**非**内核级隔离）"""

    level = ISOLATION_SUBPROCESS_HARDENED

    #: 允许从宿主继承的环境变量（**白名单**：运行 Python 必需且非凭据）
    HOST_ENV_ALLOWLIST: Tuple[str, ...] = ("SystemRoot", "WINDIR", "COMSPEC",
                                           "PATHEXT", "NUMBER_OF_PROCESSORS",
                                           "PROCESSOR_ARCHITECTURE")

    def _hardened_env(self, work_root: str) -> Dict[str, str]:
        """构造子进程环境（**replace 语义**：宿主环境整块丢弃）

        复用 S4-04 已验证的 `apply_isolation_env`（函数体内懒加载，避免包级耦合）；
        导入失败时用本模块内置的同规则实现兜底（fail-closed：仍清空全部敏感项）。

        ``container_root`` 刻意传 **空串**：任务书要求"HOME/USERPROFILE 为空"，
        而 S4-04 的 `container_root` 非空时会把它当 HOME——那是给第三方 CLI 用的
        （空 HOME 会让部分 CLI 异常退出），这里不需要那个妥协。
        """
        try:
            from agent.subagent.sandbox import apply_isolation_env
            env = apply_isolation_env({}, container_root="", trusted=False)
        except Exception as exc:  # noqa: BLE001  兜底路径：绝不因此放弃清空
            logger.warning("apply_isolation_env 不可用（改用内置同规则实现）: %s", exc)
            env = _fallback_isolation_env()
        for name in self.HOST_ENV_ALLOWLIST:
            value = os.environ.get(name)
            if value:
                env[name] = value
        env["PATH"] = os.path.dirname(os.path.abspath(sys.executable))
        env["TEMP"] = work_root
        env["TMP"] = work_root
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = ""
        return env

    def run(self, job: Dict[str, Any]) -> IsolationResult:
        work_root = self._make_work_dir()
        try:
            return self._run_inner(job, work_root=work_root)
        finally:
            self._cleanup(work_root)

    def _run_inner(self, job: Dict[str, Any], *, work_root: str) -> IsolationResult:
        payload = self._job_for(job, work_root=work_root,
                                source_root=os.path.abspath(self.source_root))
        if not os.path.exists(self.worker_path):
            return IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=False, status=STATUS_ERROR, error_code=ERR_SPAWN,
                error=f"隔离执行体不存在: {self.worker_path}",
                isolation={"level": self.level})

        argv = [sys.executable, "-I", self.worker_path, "--job", "-"]
        env = self._hardened_env(work_root)
        started = time.perf_counter()
        preexec = _posix_limits(self.quota)
        #: 作业级墙钟覆盖（探针靠它把"超时 kill"这一条测出来：忙转秒数 > 本值）
        job_timeout = _job_timeout(payload, self.quota)
        try:
            proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, cwd=work_root, env=env,
                preexec_fn=preexec,  # 仅 POSIX 非 None（Windows 返回 None）
                text=True, encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001  起不来就是起不来，如实报
            return IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=False, status=STATUS_ERROR, error_code=ERR_SPAWN,
                error=f"{type(exc).__name__}: {exc}", command=argv,
                isolation=self.describe())
        try:
            stdout, stderr = proc.communicate(
                input=json.dumps(payload, ensure_ascii=False),
                timeout=job_timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        wall_ms = round((time.perf_counter() - started) * 1000.0, 3)
        cap = self.quota.max_output_bytes
        stdout = (stdout or "")[:cap]
        stderr = (stderr or "")[:cap]
        result = self._shape(payload, stdout=stdout, stderr=stderr, argv=argv,
                             exit_code=proc.returncode, wall_ms=wall_ms,
                             timed_out=timed_out)
        return result

    def _shape(self, payload: Dict[str, Any], *, stdout: str, stderr: str,
               argv: Sequence[str], exit_code: Optional[int], wall_ms: float,
               timed_out: bool) -> IsolationResult:
        parsed = parse_worker_result(stdout)
        isolation = self.describe()
        job_timeout = _job_timeout(payload, self.quota)
        if timed_out:
            result = IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=True, status=STATUS_TIMEOUT, error_code=ERR_TIMEOUT,
                error=f"墙钟超时 {job_timeout}s 被 kill（quota_exceeded）",
                quota_exceeded=True, killed=True, exit_code=exit_code,
                command=list(argv), isolation=isolation, wall_ms=wall_ms,
                stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:])
            result.honest_notes.append(
                "超时 kill 由宿主 `communicate(timeout=)` 完成——本等级没有 cgroup，"
                "这是其唯一的硬上限手段")
            return result
        if parsed is None:
            return IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=True, status=STATUS_KILLED if (exit_code or 0) != 0 else STATUS_ERROR,
                error_code=ERR_NO_RESULT,
                error=("被隔离进程未产出结果（疑似被资源硬限/信号杀死）；"
                       f"exit_code={exit_code}"),
                killed=(exit_code or 0) != 0, exit_code=exit_code,
                command=list(argv), isolation=isolation, wall_ms=wall_ms,
                stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:],
                honest_notes=["无结果 ≠ 成功：解析不到标记一律按失败处理"])
        result = _result_from_payload(parsed, level=self.level, wall_ms=wall_ms,
                                      exit_code=exit_code, command=argv,
                                      isolation=isolation)
        result.stdout_tail = stdout[-2000:]
        result.stderr_tail = stderr[-2000:]
        return result


class ContainerExecutor(IsolationExecutor):
    """容器路径执行器（Linux/Docker；**唯一**提供内核级隔离的等级）"""

    level = ISOLATION_CONTAINER

    def __init__(self, *, quota: Optional[IsolationQuota] = None,
                 env: Optional[Dict[str, str]] = None,
                 cli: str = "", image: str = "", user: str = "",
                 network: str = "", network_allow: Sequence[str] = (),
                 source_root: str = "", work_dir: str = "",
                 keep_work_dir: bool = False,
                 prober: Optional[Callable[..., DockerProbe]] = None) -> None:
        super().__init__(quota=quota, env=env, network=network,
                         network_allow=network_allow, source_root=source_root,
                         work_dir=work_dir, keep_work_dir=keep_work_dir)
        env_map = self.env
        self.cli = str(cli or env_map.get(ENV_DOCKER_CLI) or DEFAULT_DOCKER_CLI)
        self.image = str(image or env_map.get(ENV_DOCKER_IMAGE)
                         or DEFAULT_DOCKER_IMAGE)
        self.user = str(user or env_map.get(ENV_CONTAINER_USER)
                        or DEFAULT_CONTAINER_USER)
        self._prober = prober

    @property
    def outside_root(self) -> str:
        """容器视角的"边界之外"：``/host-secrets``

        **刻意不采用宿主的真实路径**：宿主路径在容器里既不存在、也不是绝对路径，
        拿它做越界探针只会得到"相对路径写进 /work"的假证据。
        """
        return CONTAINER_OUTSIDE_ROOT

    def probe(self, *, refresh: bool = False) -> DockerProbe:
        runner = self._prober or probe_docker
        return runner(env=self.env or None, cli=self.cli, image=self.image,
                      refresh=refresh)

    def container_mounts(self) -> Dict[str, str]:
        """挂载映射（**只读源码** + 临时工作根；无 $HOME/SSH/凭据挂载）"""
        return {"source": self.source_root, "source_target": "/src",
                "work_target": "/work", "tmp_target": "/tmp"}

    def build_argv(self) -> List[str]:
        """构造 `docker run` 命令行（**全部隔离参数逐条显式**，便于用例断言）"""
        quota = self.quota
        argv = [
            self.cli, "run", "--rm", "-i",
            # 网络：none ⇒ 内核级拒绝；whitelist ⇒ 应用层策略（如实标注）
            "--network", "none" if self.network == NETWORK_NONE else "bridge",
            # 资源硬限（cgroup）
            "--memory", f"{quota.memory_mb}m",
            "--memory-swap", f"{quota.memory_mb}m",
            "--cpus", str(quota.cpus),
            "--pids-limit", str(quota.pids_limit),
            # 文件系统：根只读 + 仅 tmpfs 临时目录可写（mode=1777：非 root 可写）
            "--read-only",
            "--tmpfs", f"/work:rw,nosuid,nodev,size={quota.tmpfs_mb}m,mode=1777",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=16m,mode=1777",
            # 权限：非 root、丢弃全部 capability、禁止提权
            "--user", self.user,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            # 源码只读挂载（唯一挂载；**不挂** $HOME / ~/.ssh / 凭据目录）
            "--mount",
            f"type=bind,source={os.path.abspath(self.source_root)},"
            "target=/src,readonly",
            "-w", "/work",
            # 「存在但为空」比「不存在」更能挡住 getenv 的默认值回退（S4-04 同款）
            "-e", "HOME=", "-e", "USERPROFILE=", "-e", "SSH_AUTH_SOCK=",
            "-e", "SSH_AGENT_PID=", "-e", "SSH_ASKPASS=",
            "-e", "GIT_SSH_COMMAND=",
            "-e", "HTTP_PROXY=", "-e", "HTTPS_PROXY=", "-e", "ALL_PROXY=",
            "-e", "NO_PROXY=",
            "-e", "PYTHONIOENCODING=utf-8", "-e", "PYTHONDONTWRITEBYTECODE=1",
            self.image,
            "python", f"/src/{WORKER_REL_PATH.replace(os.sep, '/')}",
            "--job", "-",
        ]
        return argv

    def forbidden_flags(self) -> List[str]:
        """**禁止出现**的参数（用例断言 argv 里没有它们）"""
        return ["--privileged", "--pid=host", "--network=host",
                "--userns=host", "--cap-add", "-v", "--volume"]

    def run(self, job: Dict[str, Any]) -> IsolationResult:
        argv = self.build_argv()
        probe = self.probe()
        isolation = self.describe() | {"docker": probe.to_dict(),
                                       "mounts": self.container_mounts(),
                                       "user": self.user, "image": self.image}
        if not probe.available:
            return IsolationResult(
                level=self.level, job_id=str((job or {}).get("job_id") or ""),
                ran=False, status=STATUS_REFUSED, error_code=ERR_DOCKER,
                error="Docker 不可用，容器路径拒绝执行："
                      + "；".join(probe.reasons),
                refused_reason="；".join(probe.reasons), command=argv,
                isolation=isolation,
                honest_notes=["容器不可用时**不降格冒充**：拒绝执行而不是退回子进程；"
                              "需要子进程等级请显式选择 subprocess_hardened"])

        payload = self._job_for(job, work_root="/work", source_root="/src")
        started = time.perf_counter()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding="utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=False, status=STATUS_ERROR, error_code=ERR_SPAWN,
                error=f"{type(exc).__name__}: {exc}", command=argv,
                isolation=isolation)
        # 容器启动有固定开销，超时按「作业墙钟 + 启动余量」计
        job_timeout = _job_timeout(payload, self.quota)
        hard_timeout = job_timeout + CONTAINER_STARTUP_ALLOWANCE_S
        try:
            stdout, stderr = proc.communicate(
                input=json.dumps(payload, ensure_ascii=False), timeout=hard_timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            timed_out = True
        wall_ms = round((time.perf_counter() - started) * 1000.0, 3)
        cap = self.quota.max_output_bytes
        stdout = (stdout or "")[:cap]
        stderr = (stderr or "")[:cap]
        if timed_out:
            result = IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=True, status=STATUS_TIMEOUT, error_code=ERR_TIMEOUT,
                error=f"容器墙钟超时 {hard_timeout}s 被 kill（quota_exceeded）",
                quota_exceeded=True, killed=True, exit_code=proc.returncode,
                command=argv, isolation=isolation, wall_ms=wall_ms,
                stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:])
            return result
        parsed = parse_worker_result(stdout)
        if parsed is None:
            return IsolationResult(
                level=self.level, job_id=str(payload.get("job_id") or ""),
                ran=True,
                status=(STATUS_KILLED if (proc.returncode or 0) != 0
                        else STATUS_ERROR),
                error_code=ERR_NO_RESULT,
                error=("容器内进程未产出结果（疑似被 cgroup 硬限 OOM kill 或异常退出）；"
                       f"exit_code={proc.returncode}"),
                killed=(proc.returncode or 0) != 0, exit_code=proc.returncode,
                command=argv, isolation=isolation, wall_ms=wall_ms,
                stdout_tail=stdout[-2000:], stderr_tail=stderr[-2000:],
                honest_notes=["退出码 137 = SIGKILL（cgroup 内存超限的典型表现）；"
                              "无结果 ≠ 成功"])
        result = _result_from_payload(parsed, level=self.level, wall_ms=wall_ms,
                                      exit_code=proc.returncode, command=argv,
                                      isolation=isolation)
        result.stdout_tail = stdout[-2000:]
        result.stderr_tail = stderr[-2000:]
        return result


def executor_for(plan: IsolationPlan, *, env: Optional[Dict[str, str]] = None,
                 quota: Optional[IsolationQuota] = None,
                 **kwargs: Any) -> IsolationExecutor:
    """按决议等级取执行器（**等级与执行器一一对应**，不做隐式升/降级）"""
    if plan.level == ISOLATION_CONTAINER:
        return ContainerExecutor(quota=quota, env=env, **kwargs)
    if plan.level == ISOLATION_SUBPROCESS_HARDENED:
        return SubprocessHardenedExecutor(quota=quota, env=env, **kwargs)
    return InProcessExecutor(quota=quota, env=env, **kwargs)


def _job_timeout(payload: Dict[str, Any], quota: IsolationQuota) -> float:
    """作业级墙钟：``job["timeout_s"]`` 优先，否则用配额

    探针需要把"超时 kill"这条边界**测出来**（忙转秒数 > 本值），故必须有作业级
    覆盖；候选作业不设该项，行为与配额完全一致（既有语义零变化）。
    """
    try:
        value = float(payload.get("timeout_s") or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return value if value > 0 else float(quota.timeout_s)


#: 内置兜底：与 `agent.subagent.sandbox` 同规则（懒加载失败时使用）
def _fallback_isolation_env() -> Dict[str, str]:
    env = {"HOME": "", "USERPROFILE": "", "SSH_AUTH_SOCK": "", "SSH_AGENT_PID": "",
           "SSH_ASKPASS": "", "GIT_SSH_COMMAND": "",
           "HTTP_PROXY": "", "HTTPS_PROXY": "", "ALL_PROXY": "", "NO_PROXY": "",
           "http_proxy": "", "https_proxy": "", "all_proxy": "",
           "CP_SANDBOX_HOST_NETWORK": "0", "CP_SANDBOX_SSH_AGENT": "0",
           "CP_SANDBOX_HOME": "0"}
    return env


def _posix_limits(quota: IsolationQuota) -> Optional[Callable[[], None]]:
    """POSIX 资源硬限（`preexec_fn`）；Windows 返回 ``None``（**如实差距**）

    Windows 无 `resource` 模块等价物 ⇒ 内存/CPU/进程数只能靠墙钟超时兜底。
    这个差距写在 `isolation_boundaries()` 的 `not_guaranteed` 里，并由探针实测。
    """
    if sys.platform == "win32":
        return None
    try:
        import resource  # 仅 POSIX 存在
    except Exception:  # noqa: BLE001
        return None

    def _apply() -> None:  # pragma: no cover  仅在 POSIX 子进程 fork 前执行
        limits = [
            (resource.RLIMIT_AS, quota.memory_mb * 1024 * 1024),
            (resource.RLIMIT_CPU, max(1, int(quota.timeout_s)) + 2),
            (resource.RLIMIT_NPROC, quota.pids_limit),
            (resource.RLIMIT_FSIZE, quota.max_bytes * 16),
            (resource.RLIMIT_CORE, 0),
        ]
        for which, value in limits:
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                continue

    return _apply


# ════════════════════════════════════════════════════════════
#  探针（机器可读证据：env / 凭据 / 文件 / 网络 / 资源）
# ════════════════════════════════════════════════════════════


def env_derived_credentials() -> List[Dict[str, Any]]:
    """由 `HOME`/`USERPROFILE` 推出的凭据路径（**在子进程内推导**，故测的是真实值）"""
    suffixes = [".ssh/id_rsa", ".ssh/id_ed25519", ".aws/credentials",
                ".config/gcloud/credentials.db", ".docker/config.json"]
    return [{"derivation": "env_derived", "suffix": s} for s in suffixes]


def build_probe_jobs(*, level: str, work_root: str = "", source_root: str = "",
                     outside_root: str = "", network_target: str = "1.1.1.1:53",
                     memory_mb: int = 512, cpu_seconds: float = 20.0,
                     pids_count: int = 32,
                     timeout_s: float = DEFAULT_TIMEOUT_S) -> List[Dict[str, Any]]:
    """构造探针作业集（每个探针一个作业；**超限探针必须独立**——它会把进程杀掉）

    Args:
        outside_root: "边界之外"的绝对路径。留空则用占位符 ``${outside_root}``，
            由执行器按等级替换（子进程＝宿主临时目录；容器＝``/host-secrets``）。
        work_root / source_root: 生效的根。同样支持留空走占位符。
        memory_mb / cpu_seconds / pids_count: 探针量级。**必须超过被测等级的配额**，
            `run_probe_suite()` 会按执行器配额自动推导；写死会让"没超限"与
            "限得太松"分不清。
        timeout_s: 作业墙钟（超时 kill 是**每条路径都该有**的边界，故两条路径都测）。
    """
    host, _, port = str(network_target).partition(":")
    host = host or "1.1.1.1"
    port = port or "53"
    normalized = normalize_level(level) or ISOLATION_IN_PROCESS
    work = work_root or "${work_root}"
    source = source_root or "${source_root}"
    outside = outside_root or "${outside_root}"
    secret_target = f"{outside}/host-credential.txt"
    escape_target = f"{outside}/escape-probe.txt"
    #: 源码只读探针落在一个由调用方创建/删除的临时子目录里：**测的是"能不能写进
    #: 源码树"，而容器那边整棵 /src 都是只读挂载，故该子目录存在与否不影响结论；
    #: 但它让子进程路径的"越界成功"不会把文件撒进仓库。
    read_only_target = f"{source}/{SOURCE_PROBE_DIRNAME}/iso-probe-should-not-exist.txt"
    #: 探针作业一律 probe_mode=true：只有它能用越界/直连这类"故意违反守卫"的操作
    #: （它们正是用来区分"协作式拒绝"与"内核级拒绝"的）。候选作业**永不**开这个门。
    common: Dict[str, Any] = {"probe_mode": True}

    boundaries = {
        "job_id": f"probe-{PROBE_ENV}-{normalized}",
        "meta": {"probe_kind": PROBE_ENV, "level": normalized},
        **common,
        "steps": [
            {"op": "env_dump", "params": {}},
            {"op": "credential_scan",
             "params": {"paths": env_derived_credentials()}},
            # 宿主凭据（绝对路径）：子进程应"可见"（诚实差距），容器应"不可见"
            {"op": "credential_scan",
             "params": {"paths": [{"derivation": "absolute",
                                   "path": secret_target}]}},
        ],
    }
    files = {
        "job_id": f"probe-{PROBE_FILES}-{normalized}",
        "meta": {"probe_kind": PROBE_FILES, "level": normalized},
        **common,
        "steps": [
            # 临时工作目录（唯一可写根）应可写
            {"op": "write_file",
             "params": {"path": f"{work}/isolated-write.txt",
                        "content": "isolation-probe"}},
            {"op": "read_file",
             "params": {"path": f"{work}/isolated-write.txt"}},
            # 源码树写入：容器应被只读挂载拒绝；子进程应被路径守卫拒绝
            {"op": "escape_write",
             "params": {"path": read_only_target, "content": "must-not-land"}},
            # 宿主目录写入：容器应被文件系统视图挡住；子进程可越界（诚实差距）
            {"op": "escape_write",
             "params": {"path": escape_target, "content": "must-not-land"}},
        ],
    }
    network = {
        "job_id": f"probe-{PROBE_NETWORK}-{normalized}",
        "meta": {"probe_kind": PROBE_NETWORK, "level": normalized},
        "network": NETWORK_NONE,
        **common,
        "steps": [
            {"op": "net_probe",
             "params": {"host": host, "port": int(port), "timeout_s": 2.0}},
            {"op": "net_probe_raw",
             "params": {"host": host, "port": int(port), "timeout_s": 2.0}},
            {"op": "external_call", "params": {"label": "http_request",
                                               "host": host}},
        ],
    }
    memory = {
        "job_id": f"probe-{PROBE_MEMORY}-{normalized}",
        "meta": {"probe_kind": PROBE_MEMORY, "level": normalized},
        **common,
        "timeout_s": float(timeout_s),
        "steps": [{"op": "mem_alloc", "params": {"mb": int(memory_mb)}},
                  {"op": "env_dump", "params": {"names": ["PROBE_SURVIVED"]}}],
    }
    cpu = {
        "job_id": f"probe-{PROBE_CPU}-{normalized}",
        "meta": {"probe_kind": PROBE_CPU, "level": normalized},
        **common,
        # 忙转秒数**必须**大于作业墙钟，否则测的是"跑完了"而不是"被杀掉"
        "timeout_s": float(timeout_s),
        "steps": [{"op": "cpu_spin", "params": {"seconds": float(cpu_seconds)}}],
    }
    pids = {
        "job_id": f"probe-{PROBE_PIDS}-{normalized}",
        "meta": {"probe_kind": PROBE_PIDS, "level": normalized},
        **common,
        # 子进程需**同时存活**才谈得上"进程数超限"，故被 spawn 者要活得比 spawn 循环长
        "timeout_s": max(15.0, timeout_s),
        "steps": [{"op": "fork_procs", "params": {"count": int(pids_count),
                                                  "hold_s": 5.0}}],
    }
    credentials = {
        "job_id": f"probe-{PROBE_CREDENTIALS}-{normalized}",
        "meta": {"probe_kind": PROBE_CREDENTIALS, "level": normalized},
        **common,
        "steps": [
            {"op": "env_dump",
             "params": {"names": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                                  "GITHUB_TOKEN", "GH_TOKEN", "KUBECONFIG",
                                  "DOCKER_HOST", "OPENAI_API_KEY",
                                  "ANTHROPIC_API_KEY", "HOME", "USERPROFILE",
                                  "SSH_AUTH_SOCK"]}},
            {"op": "credential_scan",
             "params": {"paths": env_derived_credentials()}},
        ],
    }
    return [boundaries, credentials, files, network, memory, cpu, pids]


def probe_kind_of(job: Dict[str, Any], default: str = "") -> str:
    return str((dict(job or {}).get("meta") or {}).get("probe_kind") or default)


@dataclass
class ProbeRun:
    """一次探针执行的记录（作业 + 结果 + 派生判定）"""

    probe_kind: str
    job: Dict[str, Any] = field(default_factory=dict)
    result: IsolationResult = field(default_factory=IsolationResult)
    findings: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"probe_kind": self.probe_kind,
                "job_id": self.result.job_id,
                "job_steps": [s.get("op") for s in (self.job.get("steps") or [])],
                "result": self.result.to_dict(),
                "findings": dict(self.findings)}


def run_probe_suite(executor: IsolationExecutor, *,
                    jobs: Optional[Sequence[Dict[str, Any]]] = None,
                    host_secret_dir: str = "",
                    job_timeout_s: float = 4.0) -> List[ProbeRun]:
    """跑完一整套探针（**每个等级分别跑**；结果原样保留，不美化）

    作业里的根路径一律用占位符（见 `build_probe_jobs`），由执行器在提交时按实际
    根替换 —— 故容器与子进程跑的是**同一份探针定义**，对比表才有意义。

    探针量级**由执行器配额推导**，不写死：内存探针要配额的两倍、进程数探针要配额
    之上、CPU 探针忙转超过作业墙钟。写死量级会让"没超限"与"限得太松"分不清——
    那等于把"未测到"伪装成"没超限"。
    """
    quota = executor.quota
    owned_secret_dir = ""
    secret_dir = str(host_secret_dir or "")
    if not secret_dir:
        # 调用方没给就自己建一个：越界探针**总要**有个落脚点，且必须用完即删
        secret_dir = tempfile.mkdtemp(prefix="cp-iso-outside-")
        owned_secret_dir = secret_dir
        try:
            with open(os.path.join(secret_dir, "host-credential.txt"),
                      "w", encoding="utf-8") as fh:
                fh.write("ISOLATION-PROBE-SENTINEL")
        except OSError as exc:  # noqa: BLE001  哨兵写不出不影响其余探针
            logger.warning("宿主哨兵文件写入失败: %s", exc)
    elif executor.level == ISOLATION_SUBPROCESS_HARDENED:
        executor._outside_root = secret_dir  # noqa: SLF001  同模块协作
    try:
        prepared = list(jobs or build_probe_jobs(
            level=executor.level, work_root="", source_root="", outside_root="",
            memory_mb=max(64, int(quota.memory_mb * 2)),
            pids_count=int(quota.pids_limit + 16),
            # CPU 探针必须**活过**容器那条"作业墙钟 + 启动余量"的硬超时，
            # 否则测到的是"跑完了"而不是"被杀掉"——那是把未测到当成没超限。
            cpu_seconds=max(job_timeout_s + CONTAINER_STARTUP_ALLOWANCE_S + 5.0,
                            job_timeout_s * 3.0),
            timeout_s=job_timeout_s))
        runs: List[ProbeRun] = []
        for job in prepared:
            result = executor.run(job)
            runs.append(ProbeRun(probe_kind=probe_kind_of(job),
                                 job=dict(job), result=result,
                                 findings=summarize_probe(probe_kind_of(job),
                                                          result)))
        return runs
    finally:
        if owned_secret_dir:
            shutil.rmtree(owned_secret_dir, ignore_errors=True)


def _output_at(result: IsolationResult, index: int) -> Dict[str, Any]:
    if 0 <= index < len(result.outputs):
        return dict(result.outputs[index])
    return {}


def summarize_probe(kind: str, result: IsolationResult) -> Dict[str, Any]:
    """把一次探针结果压成**结论字段**（每条都能追到原始输出）"""
    findings: Dict[str, Any] = {"level": result.level, "ran": result.ran,
                                "status": result.status,
                                "uid": result.platform.get("uid"),
                                "platform": dict(result.platform)}
    if kind == PROBE_ENV:
        env_out = _output_at(result, 0)
        values = dict(env_out.get("values") or {})
        raw = dict(result.env_raw or {})
        findings["env"] = {
            "HOME": values.get("HOME", "<未测>"),
            "USERPROFILE": values.get("USERPROFILE", "<未测>"),
            "SSH_AUTH_SOCK": values.get("SSH_AUTH_SOCK", "<未测>"),
            "present_nonempty": list(env_out.get("present") or []),
            "home_expand": env_out.get("home_expand", ""),
        }
        #: 平台**原始**值（scrub 前）——容器里 Docker 会把 HOME 覆写成 /nonexistent，
        #: 单看生效值会掩盖这一点，故两者并列（如实标注）
        findings["env_raw"] = {
            "HOME": raw.get("HOME", "<未测>"),
            "USERPROFILE": raw.get("USERPROFILE", "<未测>"),
            "SSH_AUTH_SOCK": raw.get("SSH_AUTH_SOCK", "<未测>"),
        }
        env_cred = _output_at(result, 1)
        abs_cred = _output_at(result, 2)
        findings["env_derived_credentials"] = {
            "visible": int(env_cred.get("visible_count") or 0),
            "entries": list(env_cred.get("entries") or []),
        }
        findings["absolute_host_credentials"] = {
            "visible": int(abs_cred.get("visible_count") or 0),
            "entries": list(abs_cred.get("entries") or []),
        }
    elif kind == PROBE_CREDENTIALS:
        env_out = _output_at(result, 0)
        cred_out = _output_at(result, 1)
        values = dict(env_out.get("values") or {})
        findings["credentials"] = {
            "present_nonempty": list(env_out.get("present") or []),
            "values": {k: v for k, v in values.items()},
            "env_derived_visible": int(cred_out.get("visible_count") or 0),
        }
    elif kind == PROBE_FILES:
        findings["files"] = {
            "work_write_ok": bool(_output_at(result, 0).get("ok")),
            "work_read_ok": bool(_output_at(result, 1).get("ok")),
            "source_write_escaped": bool(_output_at(result, 2).get("escaped")),
            "source_write_reason": str(_output_at(result, 2).get("reason") or ""),
            "host_dir_write_escaped": bool(_output_at(result, 3).get("escaped")),
            "host_dir_write_reason": str(_output_at(result, 3).get("reason") or ""),
        }
    elif kind == PROBE_NETWORK:
        policy = _output_at(result, 0)
        raw = _output_at(result, 1)
        external = _output_at(result, 2)
        findings["network"] = {
            "policy_reachable": bool(policy.get("reachable")),
            "policy_enforced_by": str(policy.get("enforced_by") or ""),
            "raw_reachable": bool(raw.get("reachable")),
            "raw_reason": str(raw.get("reason") or ""),
            "external_call_denied": not bool(external.get("ok", True)),
        }
    elif kind == PROBE_MEMORY:
        mem = _output_at(result, 0)
        findings["memory"] = {
            "allocated_mb": int(mem.get("allocated_mb") or 0),
            "memory_error": bool(mem.get("memory_error")),
            "survived": result.status == STATUS_SUCCESS and result.ran,
            "killed": bool(result.killed) or result.status in (STATUS_KILLED,
                                                               STATUS_TIMEOUT),
            "exit_code": result.exit_code,
            "error_code": result.error_code,
        }
    elif kind == PROBE_CPU:
        spin = _output_at(result, 0)
        findings["cpu"] = {
            "spun_s": float(spin.get("spun_s") or 0.0),
            "killed": bool(result.killed) or result.status == STATUS_TIMEOUT,
            "timeout": result.status == STATUS_TIMEOUT,
            "error_code": result.error_code,
        }
    elif kind == PROBE_PIDS:
        pids = _output_at(result, 0)
        findings["pids"] = {
            "spawned": int(pids.get("spawned") or 0),
            "requested": int(pids.get("requested") or 0),
            "alive_at_peak": int(pids.get("alive_at_peak") or 0),
            "blocked": bool(pids.get("blocked")),
            "reason": str(pids.get("reason") or ""),
        }
    return findings


def comparison_rows(runs_by_level: Dict[str, List[ProbeRun]]) -> List[Dict[str, Any]]:
    """容器 vs 子进程 vs 进程内 **对比表**（每一步都来自实测，缺测如实写"未测"）"""
    rows: List[Dict[str, Any]] = []
    aspects = [
        ("env_home", "HOME 为空", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV, lambda d: repr(d.get("env", {}).get("HOME")))),
        ("env_userprofile", "USERPROFILE 为空", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV,
                         lambda d: repr(d.get("env", {}).get("USERPROFILE")))),
        ("env_ssh", "SSH_AUTH_SOCK 为空", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV,
                         lambda d: repr(d.get("env", {}).get("SSH_AUTH_SOCK")))),
        ("env_home_raw", "HOME 平台原始值（≠ 宿主 HOME）", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV,
                         lambda d: _raw_home_cell(d.get("env_raw", {}).get("HOME")))),
        ("cred_env_derived", "宿主凭据（env 推导路径）不可见", PROBE_CREDENTIALS,
         lambda f: _cell(f, PROBE_CREDENTIALS,
                         lambda d: "不可见" if d.get("credentials", {})
                         .get("env_derived_visible", 1) == 0 else "可见")),
        ("cred_absolute", "宿主凭据（绝对路径）不可见", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV,
                         lambda d: "不可见" if d.get("absolute_host_credentials", {})
                         .get("visible", 1) == 0 else "**可见（差距）**")),
        ("fs_work_writable", "临时目录可写", PROBE_FILES,
         lambda f: _cell(f, PROBE_FILES,
                         lambda d: "可写" if d.get("files", {}).get("work_write_ok")
                         else "不可写")),
        ("fs_source_ro", "源码树只读", PROBE_FILES,
         lambda f: _cell(f, PROBE_FILES,
                         lambda d: "写入被拒" if not d.get("files", {})
                         .get("source_write_escaped") else "**写入成功（差距）**")),
        ("fs_host_invisible", "宿主目录不可写（文件系统视图）", PROBE_FILES,
         lambda f: _cell(f, PROBE_FILES,
                         lambda d: "写入被拒" if not d.get("files", {})
                         .get("host_dir_write_escaped") else "**越界写入成功（差距）**")),
        ("net_policy", "出域被策略拒绝", PROBE_NETWORK,
         lambda f: _cell(f, PROBE_NETWORK,
                         lambda d: "拒绝" if not d.get("network", {})
                         .get("policy_reachable") else "可达")),
        ("net_kernel", "出域被**内核**拒绝（直连探针）", PROBE_NETWORK,
         lambda f: _cell(f, PROBE_NETWORK,
                         lambda d: "拒绝（内核级）" if not d.get("network", {})
                         .get("raw_reachable") else "**可达（无内核级网络隔离）**")),
        ("mem_limit", "内存超限被终止", PROBE_MEMORY,
         lambda f: _cell(f, PROBE_MEMORY,
                         lambda d: _limit_cell(d.get("memory", {})))),
        ("cpu_limit", "CPU/时间超限被终止", PROBE_CPU,
         lambda f: _cell(f, PROBE_CPU,
                         lambda d: "被终止" if d.get("cpu", {}).get("killed")
                         else "未终止")),
        ("pids_limit", "进程数超限被拒", PROBE_PIDS,
         lambda f: _cell(f, PROBE_PIDS,
                         lambda d: ("被拒" if d.get("pids", {}).get("blocked")
                                    else f"未拒（同时存活 "
                                         f"{d.get('pids', {}).get('alive_at_peak')}/"
                                         f"{d.get('pids', {}).get('requested')}）"))),
        ("user", "非 root 运行", PROBE_ENV,
         lambda f: _cell(f, PROBE_ENV,
                         lambda d: _user_cell(d))),
    ]
    for key, label, kind, extractor in aspects:
        row: Dict[str, Any] = {"aspect": key, "label": label, "probe": kind}
        for level in ISOLATION_LEVELS:
            runs = runs_by_level.get(level) or []
            row[level] = extractor(runs) if runs else "未测"
        rows.append(row)
    return rows


def _cell(runs: Sequence[ProbeRun], kind: str,
          extractor: Callable[[Dict[str, Any]], str]) -> str:
    for run in runs:
        if run.probe_kind == kind:
            if not run.result.ran:
                return f"未运行（{run.result.status}）"
            try:
                return extractor(run.findings)
            except Exception as exc:  # noqa: BLE001
                return f"解析失败（{type(exc).__name__}）"
    return "未测"


def _limit_cell(memory: Dict[str, Any]) -> str:
    if not memory:
        return "未测"
    if memory.get("killed"):
        return "被终止（硬限）"
    if memory.get("memory_error"):
        return "被拒（软限 MemoryError）"
    if memory.get("survived"):
        return f"未终止（分配 {memory.get('allocated_mb')}MB 仍存活）"
    return "未测"


def _raw_home_cell(value: Any) -> str:
    """平台原始 HOME 的单元格：**只要不等于宿主 HOME** 就可接受，但必须原样写出"""
    text = "" if value is None else str(value)
    if text == "":
        return "`''`"
    return f"`{text}`（非宿主 HOME）"


def _user_cell(findings: Dict[str, Any]) -> str:
    uid = findings.get("uid")
    if uid is None:
        uid = (findings.get("platform") or {}).get("uid")
    if isinstance(uid, int):
        return f"uid={uid}（{'非 root' if uid != 0 else '**root**'}）"
    return "非 root（Windows 无 uid 概念，以 --user 参数为准）"


def render_comparison_markdown(rows: Sequence[Dict[str, Any]], *,
                               header: str = "") -> str:
    """对比表 → Markdown（验收报告与 CLI 共用；**不美化缺失单元格**）"""
    labels = {"in_process": "in_process", "subprocess_hardened": "subprocess_hardened",
              "container": "container"}
    lines = [header or "## 隔离等级实测对比表", "",
             "| 边界 | " + " | ".join(labels[l] for l in ISOLATION_LEVELS) + " |",
             "|---|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['label']} | "
                     + " | ".join(str(row.get(l, "未测")) for l in ISOLATION_LEVELS)
                     + " |")
    lines.append("")
    lines.append("> 单元格逐格来自 `scripts/verify_isolation.py` 的实测输出；"
                 "「未测 / 未运行」表示该等级在该环境下无法执行（如 Docker 不可用），"
                 "**不以后验推断补写**。")
    return "\n".join(lines) + "\n"


# ════════════════════════════════════════════════════════════
#  "未双写"实测工具（真实环境未被改动的前后比对）
# ════════════════════════════════════════════════════════════


def snapshot_paths(paths: Sequence[str]) -> Dict[str, Any]:
    """对真实环境做**只读**快照（文件指纹 + 目录清单），供"未双写"实测比对"""
    snapshot: Dict[str, Any] = {"files": {}, "dirs": {}, "missing": []}
    for raw in paths or []:
        path = os.path.abspath(str(raw))
        if os.path.isdir(path):
            entries: List[str] = []
            for root, dirnames, filenames in os.walk(path):
                dirnames.sort()
                for name in sorted(filenames):
                    full = os.path.join(root, name)
                    rel = os.path.relpath(full, path).replace("\\", "/")
                    entries.append(f"{rel}:{_file_digest(full)}")
            snapshot["dirs"][path] = entries
        elif os.path.isfile(path):
            snapshot["files"][path] = _file_digest(path)
        else:
            snapshot["missing"].append(path)
    return snapshot


def _file_digest(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha1(fh.read()).hexdigest()[:16]
    except OSError as exc:
        return f"unreadable:{type(exc).__name__}"


def diff_snapshot(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """两次快照的差异（空差异 = 真实环境未被改动 ⇒ "未双写"证据）

    目录条目按 ``相对路径`` 分桶（而不是拿 ``rel:digest`` 整串做集合差）：
    **同一个文件内容变了**属于"被改动"，不是"删了一个又建了一个"——把改动说成
    删除+新建会把"被改写"这件严重的事说轻，那是口径问题，不是措辞问题。
    """
    changed: List[str] = []
    created: List[str] = []
    removed: List[str] = []
    for path, digest in (before.get("files") or {}).items():
        now = (after.get("files") or {}).get(path, "<缺失>")
        if now == "<缺失>":
            removed.append(path)
        elif now != digest:
            changed.append(path)
    for path in (after.get("files") or {}):
        if path not in (before.get("files") or {}):
            created.append(path)
    for path, entries in (before.get("dirs") or {}).items():
        before_map = _split_entries(entries)
        after_map = _split_entries((after.get("dirs") or {}).get(path) or [])
        for rel, digest in before_map.items():
            if rel not in after_map:
                removed.append(f"{path}/{rel}")
            elif after_map[rel] != digest:
                changed.append(f"{path}/{rel}")
        for rel in after_map:
            if rel not in before_map:
                created.append(f"{path}/{rel}")
    unchanged = not (changed or created or removed)
    return {"unchanged": unchanged, "changed": sorted(changed),
            "created": sorted(created), "removed": sorted(removed)}


def _split_entries(entries: Sequence[Any]) -> Dict[str, str]:
    """``["rel:digest", ...]`` → ``{rel: digest}``（digest 自身含冒号也不怕）"""
    out: Dict[str, str] = {}
    for item in entries or []:
        text = str(item)
        rel, _, digest = text.rpartition(":")
        if not rel:
            out[text] = ""
        else:
            out[rel] = digest
    return out


__all__ = [
    # 等级
    "ISOLATION_IN_PROCESS", "ISOLATION_SUBPROCESS_HARDENED", "ISOLATION_CONTAINER",
    "ISOLATION_LEVELS", "ISOLATION_RANK", "ISOLATION_REQUEST_AUTO",
    "ISOLATION_REQUEST_OFF", "ISOLATION_REQUEST_ALIASES", "normalize_level",
    # 开关与配额
    "ENV_LEVEL", "ENV_DOCKER_CLI", "ENV_DOCKER_IMAGE", "ENV_NETWORK",
    "ENV_NETWORK_ALLOW", "ENV_MEMORY_MB", "ENV_CPUS", "ENV_PIDS_LIMIT",
    "ENV_TMPFS_MB", "ENV_CONTAINER_USER", "ENV_TIMEOUT_S",
    "ENV_MAX_OUTPUT_BYTES", "ENV_SOURCE_MOUNT", "ENV_WORK_DIR",
    "ENV_KEEP_WORK_DIR", "ENV_PROBE_TTL",
    "NETWORK_NONE", "NETWORK_WHITELIST", "NETWORK_MODES",
    "DEFAULT_DOCKER_IMAGE", "DEFAULT_DOCKER_CLI", "DEFAULT_CONTAINER_USER",
    "DEFAULT_MEMORY_MB", "DEFAULT_CPUS", "DEFAULT_PIDS_LIMIT",
    "DEFAULT_TMPFS_MB", "DEFAULT_TIMEOUT_S", "DEFAULT_MAX_OUTPUT_BYTES",
    "IsolationQuota",
    # 状态与词表
    "STATUS_SUCCESS", "STATUS_ERROR", "STATUS_QUOTA_EXCEEDED", "STATUS_DENIED",
    "STATUS_ESCAPE_BLOCKED", "STATUS_TIMEOUT", "STATUS_KILLED",
    "STATUS_NOT_RUN", "STATUS_REFUSED",
    "ERR_TIMEOUT", "ERR_KILLED", "ERR_SPAWN", "ERR_NO_RESULT", "ERR_REFUSED",
    "ERR_DOCKER", "RESULT_BEGIN", "RESULT_END", "EXEC_OPS", "PROBE_ONLY_OPS",
    "WORKER_REL_PATH", "REPO_ROOT",
    # 探测与决议
    "DockerProbe", "probe_docker", "docker_available", "subprocess_available",
    "reset_isolation_probe_cache", "resolve_isolation_level", "IsolationPlan",
    "isolation_capability_report",
    # 边界
    "BoundarySpec", "isolation_boundaries", "boundary_table",
    "LEVEL_SOURCE_DEFAULT", "LEVEL_SOURCE_ENV", "LEVEL_SOURCE_ARG",
    "LEVEL_SOURCE_FALLBACK",
    # 执行器
    "IsolationResult", "IsolationExecutor", "InProcessExecutor",
    "SubprocessHardenedExecutor", "ContainerExecutor", "executor_for",
    "parse_worker_result",
    # 探针
    "PROBE_ENV", "PROBE_CREDENTIALS", "PROBE_FILES", "PROBE_NETWORK",
    "PROBE_MEMORY", "PROBE_CPU", "PROBE_PIDS", "PROBE_KINDS", "ProbeRun",
    "build_probe_jobs", "probe_kind_of", "run_probe_suite", "summarize_probe",
    "comparison_rows", "render_comparison_markdown", "env_derived_credentials",
    # 未双写实测
    "snapshot_paths", "diff_snapshot",
]
