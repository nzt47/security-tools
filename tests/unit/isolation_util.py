"""TASK-S8-03 测试共用工具：假 Docker 探测 / 假隔离执行器 / 合成用例

与 `tests/unit/digestion_util.py` 同风格——只做构造，不做断言。

**为什么需要"假探测器"**：等级的取舍逻辑必须能在**不去真的调用 docker** 的前提下
被穷尽测试（否则"无 Docker 的 CI"上这些用例要么失败要么永远只走一条分支）。
真实 docker 只在一个地方被直接使用：容器执行器的集成用例（且 `skipif` gate）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from agent.digestion import cases as C
from agent.digestion import isolation as ISO

CAP = "cp.builtin.read_file"


def make_case(index: int = 0, *, root: str = "C:/sandbox") -> C.EquivalenceCase:
    """一条可回放的两段任务链用例（上游程序 = 候选程序 ⇒ 三层比对全过）"""
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path}),
             C.ProgramStep(label="write_file", params={"path": path,
                                                       "content": f"c{index}"})]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def make_case_set(size: int = 4, **kwargs: Any) -> C.CaseSet:
    return C.build_case_set(CAP, [make_case(i, **kwargs) for i in range(size)])


def fake_prober(*, available: bool = True, server_version: str = "29.0.0-fake",
                image_available: bool = True, reason: str = "",
                calls: Optional[List[Dict[str, Any]]] = None) -> Any:
    """构造一个 `probe_docker` 形状的假探测器（**不碰真实 docker**）"""

    def _probe(*, env: Any = None, cli: str = "", image: str = "",
               timeout_s: float = 0.0, refresh: bool = False) -> ISO.DockerProbe:
        if calls is not None:
            calls.append({"cli": cli, "image": image, "refresh": refresh})
        probe = ISO.DockerProbe(
            cli=str(cli or ISO.DEFAULT_DOCKER_CLI),
            image=str(image or ISO.DEFAULT_DOCKER_IMAGE),
            cli_available=True, daemon_available=bool(available),
            server_version=str(server_version if available else ""),
            image_available=bool(image_available and available))
        probe.detail["fake"] = True
        if not available:
            probe.reasons.append(reason or "假探测器：Docker 不可用（模拟 daemon 未运行）")
        elif not image_available:
            probe.reasons.append("假探测器：镜像本地不存在（可拉取）")
        return probe

    return _probe


class FakeExecutor(ISO.IsolationExecutor):
    """假隔离执行器（用于**不真的起进程**地测接线、预算与失败回落）

    **它不是"能通过验收的假证据"**：真实边界一律由 `verify_isolation.py` 与
    容器/子进程集成用例实测。这里只是让"连续失败 ⇒ 回落"这类逻辑可以在毫秒级
    被穷尽地测到。
    """

    level = ISO.ISOLATION_SUBPROCESS_HARDENED

    def __init__(self, *, status: str = ISO.STATUS_SUCCESS, side_effects: Any = None,
                 quota_exceeded: bool = False, error: str = "",
                 steps: Optional[Sequence[str]] = None,
                 keep_work_dir: bool = False) -> None:
        super().__init__(keep_work_dir=keep_work_dir)
        self.status = status
        self.side_effects = dict(side_effects or {})
        self.quota_exceeded = bool(quota_exceeded)
        self.error = error
        self.steps = list(steps or [])
        self.jobs: List[Dict[str, Any]] = []

    def run(self, job: Dict[str, Any]) -> ISO.IsolationResult:
        self.jobs.append(dict(job))
        ok = self.status == ISO.STATUS_SUCCESS
        return ISO.IsolationResult(
            level=self.level, job_id=str(job.get("job_id") or ""), ran=True,
            status=self.status, error=self.error,
            error_code=("" if ok else "E_FAKE"),
            steps=list(self.steps) or [str(s.get("op")) for s in job.get("steps") or []],
            outputs=[{"ok": ok}],
            side_effects={
                "files_written": list(self.side_effects.get("files_written") or []),
                "files_deleted": list(self.side_effects.get("files_deleted") or []),
                "external_calls": list(self.side_effects.get("external_calls") or []),
            },
            duration_ms=0.5, wall_ms=1.0,
            quota_exceeded=self.quota_exceeded, killed=self.quota_exceeded,
            exit_code=0 if ok else 1,
            isolation={"level": self.level, "fake": True})


__all__ = ["CAP", "make_case", "make_case_set", "fake_prober", "FakeExecutor"]
