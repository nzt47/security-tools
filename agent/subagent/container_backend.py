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
    · 真跑需要镜像内含可执行对端（本仓尚无分身侧 CLI 入口脚本）—— 本档先交付
      **argv/探测/拒绝** 契约层，真机 E2E 需镜像入口，如实登记、不谎报。

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
    task_dir: str = "/work"
    network: str = "none"

    def run_prefix(self) -> Tuple[str, ...]:
        """docker run 的隔离前缀（网络/资源/权限**逐条显式**，便于 argv 级断言）"""
        from agent.digestion.isolation import IsolationQuota

        quota = IsolationQuota()
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

        Raises:
            ChannelError: argv 里 task_file 未出现或出现多次（fail-closed，不猜）。
        """
        task_file = str(invocation.task_file or "")
        argv = list(invocation.argv or ())
        hits = [i for i, token in enumerate(argv) if token == task_file]
        if len(hits) != 1:
            raise ChannelError(
                "container 后端要求 invocation.argv 里 task_file 恰好出现 1 次，实际 %d 次"
                % len(hits))
        host_dir = os.path.dirname(os.path.abspath(task_file))
        container_task = self.spec.task_dir.rstrip("/") + "/" + os.path.basename(task_file)
        inner = [container_task if token == task_file else token for token in argv]
        mount = "type=bind,source=%s,target=%s,readonly" % (host_dir, self.spec.task_dir)
        return [*self.spec.run_prefix(), "--mount", mount,
                "-w", self.spec.task_dir, self.spec.image, *inner]

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
                            task_dir: str = "", runner: Any = None,
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
    )
    return ContainerChannelExecutor(spec, runner=runner, prober=prober)
