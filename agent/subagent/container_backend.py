"""分身 container 执行后端（P5 / portability：第三种"在哪跑"）

【解决什么（不这样会怎样）】
    分身此前有 inproc（部署 LLM）、subprocess（外部 CLI）、local（本地推理）三档，
    都在**宿主进程/宿主文件系统**里跑。container 档把同一份 task_file-jsonl 协议
    包进 docker run：网络 none、根只读、丢弃全部 capability、非 root、资源硬限。
    没有它，"隔离开一个分身"只能靠约定，不能靠内核。

【协议零改动（换后端不改协议）】
    本档实现既有的 ChannelExecutor.__call__(invocation) -> RawOutput，**不重写**
    -p/--output-format/--max-turns：取 invocation.argv（执行器已用 build_cli_argv 生成），
    把等于 invocation.task_file 的那个 token 换成容器内路径，再前置 docker run 包裹。
    于是 task_file 输入、JSON Lines 输出、三级降级、taint、工具裁剪闸门、Trace、
    成本记账**全部不动**，只差"在哪跑"。

【不假装（fail-closed）】
    · Docker 不可用（CLI 或 daemon 缺一）⇒ **拒绝执行**，绝不降格冒充容器、也不
      静默回落 inproc/subprocess（复用 isolation.probe_docker 的探测结论）。
    · invocation.argv 里 task_file 必须恰好出现 1 次，否则 ChannelError（不猜路径）。
    · 镜像名安全地经 argv 传入（不拼 shell 字符串），宿主路径只读挂载到容器 task_dir；
      不挂 $HOME/~/.ssh/凭据目录；禁止 --privileged/--network=host/-v 等（见 FORBIDDEN_FLAGS）。
    · task_file 只读挂在**独立路径** task_mount（默认 /task），绝不与可写 tmpfs
      task_dir（默认 /work）同目标：**实测**同目标时 tmpfs 会盖住 bind 挂载，
      容器里根本读不到 task_file（真跑 100% 失败），故 task_mount != task_dir 是
      构造期硬约束，不是风格偏好。
    · 真跑需要镜像内含可执行对端：本仓提供 ``scripts/subagent_peer.py``（§3.10 协议
      对端：离线回执 + ``--handler`` 注入镜内执行体）与参考镜像
      ``docker/subagent-peer/Dockerfile``；镜内**真实推理**仍需镜像自带 local 后端，
      这一条如实登记为残余缺口，不谎报。

【依赖纪律】
    顶层仅标准库 + agent.subagent.channel；agent.digestion.isolation（容量常量与
    Docker 探测）在函数内惰性导入，不给 channel 增加导入负担。
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from agent.subagent.channel import (ChannelError, ChannelExecutor,
                                    ChannelInvocation, RawOutput)

logger = logging.getLogger(__name__)

__all__ = [
    "BACKEND_CONTAINER",
    "ENV_CONTAINER_ENABLED",
    "ENV_CONTAINER_IMAGE",
    "ENV_CONTAINER_CLI",
    "FORBIDDEN_FLAGS",
    "ContainerBackendError",
    "ContainerSpec",
    "ContainerChannelExecutor",
    "build_container_channel",
    "container_backend_enabled",
]

#: bundle / config 里的后端名（与 inproc/subprocess/local 平级）
BACKEND_CONTAINER = "container"

ENV_CONTAINER_ENABLED = "CP_SUBAGENT_CONTAINER_ENABLED"
ENV_CONTAINER_IMAGE = "CP_SUBAGENT_CONTAINER_IMAGE"
ENV_CONTAINER_CLI = "CP_SUBAGENT_CONTAINER_CLI"

_TRUTHY = ("1", "true", "yes", "on")

#: 明确禁止出现在容器 argv 里的危险参数（与 isolation.ContainerExecutor 同源）
FORBIDDEN_FLAGS: Tuple[str, ...] = (
    "--privileged", "--pid=host", "--network=host", "--userns=host",
    "--cap-add", "-v", "--volume",
)


class ContainerBackendError(Exception):
    """container 后端构造失败（fail-closed，**不回落**其它后端）"""

    code = "E_CONTAINER_BACKEND"

    def __init__(self, message: str, *, code: str = "E_CONTAINER_BACKEND") -> None:
        super().__init__(message)
        self.code = str(code or "E_CONTAINER_BACKEND")


def container_backend_enabled(environ: Optional[Any] = None) -> bool:
    """container 档总开关（默认关；关着时执行通道选择与改动前逐字相同）"""
    if environ is None:
        raw = os.environ.get(ENV_CONTAINER_ENABLED, "")
    else:
        raw = environ.get(ENV_CONTAINER_ENABLED, "")
    return str(raw or "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class ContainerSpec:
    """容器执行规格（容量复用 IsolationQuota 常量，不新增硬编码边界）"""

    image: str
    cli: str = "docker"
    user: str = "65534:65534"
    #: 容器内可写工作目录（tmpfs；根只读时唯一可写的落点）
    task_dir: str = "/work"
    #: 宿主 task_file 所在目录的**只读**挂载点（必须 != task_dir，否则 tmpfs 遮盖 bind）
    task_mount: str = "/task"
    network: str = "none"

    def run_prefix(self) -> Tuple[str, ...]:
        """docker run 的隔离前缀（网络/资源/权限**逐条显式**，便于 argv 级断言）"""
        from agent.digestion.isolation import IsolationQuota

        # 用 from_env()：容器内存/CPU 等应受 CP_DIGESTION_ISOLATION_* 约束（与
        # IsolationExecutor 同源）。裸 IsolationQuota() 只取类默认值（内存 256MiB），
        # 会把"可配置配额"变成死值——镜内本地推理（见 Dockerfile.local）恰好需要更大内存。
        quota = IsolationQuota.from_env()
        return (
            self.cli, "run", "--rm",
            "--network", self.network,
            "--memory", "%dm" % quota.memory_mb,
            "--memory-swap", "%dm" % quota.memory_mb,
            "--cpus", str(quota.cpus),
            "--pids-limit", str(quota.pids_limit),
            "--read-only",
            "--tmpfs", "%s:rw,nosuid,nodev,size=%dm,mode=1777"
                       % (self.task_dir, quota.tmpfs_mb),
            "--user", self.user,
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
        )


class ContainerChannelExecutor(ChannelExecutor):
    """第三档执行器：同一份 task_file-jsonl 协议包进 docker run

    构造期**零网络、零 docker 调用**；探测只在真正 __call__ 时发生。
    """

    def __init__(self, spec: ContainerSpec, *, runner: Any = None,
                 prober: Any = None) -> None:
        self.spec = spec
        self._runner = runner or subprocess.run
        self._prober = prober
        self._probe: Any = None

    def probe(self, *, refresh: bool = False) -> Any:
        """Docker 可用性（注入 prober 优先；否则复用 isolation.probe_docker 的缓存）"""
        if self._prober is not None:
            return self._prober()
        from agent.digestion.isolation import probe_docker

        if self._probe is None or refresh:
            self._probe = probe_docker(cli=self.spec.cli, image=self.spec.image,
                                       refresh=refresh)
        return self._probe

    def forbidden_flags(self) -> List[str]:
        return list(FORBIDDEN_FLAGS)

    def build_argv(self, invocation: ChannelInvocation) -> List[str]:
        """把 invocation.argv 包进 docker run（task_file 换成容器内路径）

        task_file 的只读挂载点（task_mount）与可写 tmpfs 工作目录（task_dir）是
        两个不同路径：同目标时 Docker 会用 tmpfs 盖住 bind，容器里读不到
        task_file —— 这是实测出来的硬约束，不是风格偏好。

        Raises:
            ChannelError: argv 里 task_file 未出现或出现多次，或 task_mount 与
                task_dir 同目标（fail-closed，不猜路径、不静默改写挂载点）。
        """
        task_file = str(invocation.task_file or "")
        task_dir = self.spec.task_dir.rstrip("/") or "/work"
        task_mount = self.spec.task_mount.rstrip("/")
        if not task_mount or task_mount == task_dir:
            raise ChannelError(
                "container 后端要求 task_mount != task_dir（当前 %r / %r）："
                "同目标时 tmpfs 会盖住只读 bind，容器读不到 task_file"
                % (self.spec.task_mount, self.spec.task_dir))
        argv = list(invocation.argv or ())
        hits = [i for i, token in enumerate(argv) if token == task_file]
        if len(hits) != 1:
            raise ChannelError(
                "container 后端要求 invocation.argv 里 task_file 恰好出现 1 次，实际 %d 次"
                % len(hits))
        host_dir = os.path.dirname(os.path.abspath(task_file))
        container_task = task_mount + "/" + os.path.basename(task_file)
        inner = [container_task if token == task_file else token for token in argv]
        mount = "type=bind,source=%s,target=%s,readonly" % (host_dir, task_mount)
        return [*self.spec.run_prefix(), "--mount", mount,
                "-w", task_dir, self.spec.image, *inner]

    def __call__(self, invocation: ChannelInvocation) -> RawOutput:
        start = time.time()
        probe = self.probe()
        if not bool(getattr(probe, "available", False)):
            reasons = getattr(probe, "reasons", ()) or ()
            return RawOutput(
                returncode=-10,
                error=("Docker 不可用，container 后端拒绝执行（不降格冒充）："
                       + "；".join(str(r) for r in reasons)),
                duration_ms=(time.time() - start) * 1000)
        try:
            argv = self.build_argv(invocation)
        except ChannelError as e:
            return RawOutput(returncode=-11, error=str(e),
                             duration_ms=(time.time() - start) * 1000)
        try:
            proc = self._runner(argv, capture_output=True, text=True,
                                encoding="utf-8", errors="replace",
                                timeout=float(invocation.timeout_seconds))
        except subprocess.TimeoutExpired:
            return RawOutput(returncode=-1, timed_out=True, error="timeout",
                             duration_ms=(time.time() - start) * 1000)
        except FileNotFoundError as e:
            return RawOutput(returncode=-2, error="容器 CLI 不存在: %s" % e,
                             duration_ms=(time.time() - start) * 1000)
        except OSError as e:
            return RawOutput(returncode=-3, error="容器启动失败: %s" % e,
                             duration_ms=(time.time() - start) * 1000)
        return RawOutput(stdout=getattr(proc, "stdout", "") or "",
                         stderr=getattr(proc, "stderr", "") or "",
                         returncode=int(getattr(proc, "returncode", 0) or 0),
                         duration_ms=(time.time() - start) * 1000)


def build_container_channel(*, image: str = "", cli: str = "", user: str = "",
                            task_dir: str = "", task_mount: str = "",
                            runner: Any = None,
                            prober: Any = None) -> ContainerChannelExecutor:
    """构造容器通道（**构造期零网络/零 docker 调用**）

    Raises:
        ContainerBackendError: 镜像名缺失（fail-closed，不回落其它后端）。
    """
    img = str(image or os.environ.get(ENV_CONTAINER_IMAGE, "") or "").strip()
    if not img:
        raise ContainerBackendError(
            "container 后端需要镜像名（CP_SUBAGENT_CONTAINER_IMAGE 未配置）",
            code="E_CONTAINER_IMAGE_MISSING")
    spec = ContainerSpec(
        image=img,
        cli=(str(cli or os.environ.get(ENV_CONTAINER_CLI, "") or "docker").strip()
             or "docker"),
        user=(str(user).strip() or "65534:65534"),
        task_dir=(str(task_dir).strip() or "/work"),
        task_mount=(str(task_mount).strip() or "/task"),
    )
    return ContainerChannelExecutor(spec, runner=runner, prober=prober)
