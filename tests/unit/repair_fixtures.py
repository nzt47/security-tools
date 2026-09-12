"""TASK-S7-02 自修复 L1 测试公用工具与夹具（文件名刻意**不以 ``test_`` 开头）

【为什么这个文件不叫 ``test_repair_conftest.py``】
    ``pytest.ini`` 的 ``python_files = test_*.py *_test.py`` 会把 ``test_*.py`` 当
    测试模块收集——一个只放工具函数的文件被收集会产生「0 个用例」的噪声模块，也会
    让「测试文件清单」里混进非测试文件。改名后它就是一个普通可导入模块
    （``tests/unit`` 已在 ``pytest.ini`` 的 ``pythonpath`` 中）。

【为什么需要它】
    本任务的测试大量需要「造一个能过闸门的补丁」「造一个只读工具集 outcomes」这类
    构造动作。若每个测试文件各写一份，口径会漂移（例如有的地方 authorized 子集写错，
    导致「工具裁剪断言」测的其实不是同一条路径）。这里集中一处。

【隔离纪律（任务书 §八 #5）】
    委派/子代理/Trace/审计类用例都会产生运行时写入，故这里的构造器一律**不**自己
    开库；开库的夹具在各测试文件内用 ``tmp_path`` 显式指向临时路径，
    **绝不触碰生产库**（``agent/data/tool_trace.db`` 等）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence

import pytest

from agent.repair.models import (
    CheckResult,
    FailureItem,
    PatchGuardReport,
    RepairPatch,
    RepairTicket,
    SourceSlice,
    VerificationReport,
)
from agent.repair.policy import REPAIR_SUBAGENT_TOOLS, RepairPolicy
from agent.repair.trace import RepairRunLogger, RunLoggerConfig


def make_failure(**overrides: Any) -> FailureItem:
    """一条典型失败项（可覆盖任意字段）"""
    data: Dict[str, Any] = {
        "node_id": "tests/unit/test_demo_math.py::test_add",
        "file": "tests/unit/test_demo_math.py",
        "line": 12,
        "message": "assert 1 == 2",
        "text": "def test_add():\n>       assert add(1, 1) == 2\nE       assert 2 == 3",
        "stack_fingerprint": "abcdef0123456789",
        "kind": "failure",
    }
    data.update(overrides)
    return FailureItem(**data)


def make_ticket(**overrides: Any) -> RepairTicket:
    """一份工单（缺省指向 :func:`make_failure` 的失败项）"""
    data: Dict[str, Any] = {
        "ticket_id": "tkt-0123456789abcdef",
        "failure": make_failure(),
        "slices": [SourceSlice(path="tests/unit/test_demo_math.py", start_line=1,
                               end_line=20, content="     1| def test_add():\n"
                                                    "     2|     assert add(1, 1) == 2")],
        "impl_files": ["agent/demo_math.py"],
        "trace_chain": [],
        "descriptors": [],
        "recent_changes": [],
        "repo_head": "deadbeef" * 5,
        "adjacent_tests": ["tests/unit/test_demo_math.py"],
        "evidence_gaps": [],
    }
    data.update(overrides)
    return RepairTicket(**data)


def make_guard(diff: str, *, policy: Optional[RepairPolicy] = None) -> PatchGuardReport:
    from agent.repair.guardrails import guard_patch
    return guard_patch(diff, policy=policy)


def make_patch(diff: str = "", *, policy: Optional[RepairPolicy] = None,
               guard: Optional[PatchGuardReport] = None, **overrides: Any) -> RepairPatch:
    data: Dict[str, Any] = {
        "diff": diff,
        "rationale": "把 off-by-one 的边界改回 `<`",
        "files": ["agent/demo_math.py"],
        "self_eval": {"verdict": "pass", "score": 0.8},
        "delegation_id": "dlg-deadbeefcafe",
        "trace_id": "tr-0123456789abcdef",
        "toolset": {"actor": "sub_agent:dlg-deadbeefcafe",
                    "tools": list(REPAIR_SUBAGENT_TOOLS),
                    "authorized_subset": list(REPAIR_SUBAGENT_TOOLS),
                    "denied_count": 0},
    }
    data.update(overrides)
    patch = RepairPatch(**data)
    patch.guard = guard if guard is not None else (
        make_guard(diff, policy=policy) if diff.strip() else None)
    return patch


def make_verification(*, ok: bool = True, **overrides: Any) -> VerificationReport:
    """一份三关验证报告（缺省全过）"""
    report = VerificationReport(
        ok=ok,
        target=CheckResult(name="target", passed=ok, command="python -m pytest ...",
                           exit_code=0 if ok else 1, passed_count=1 if ok else 0,
                           failed_count=0 if ok else 1, output_summary="1 passed"),
        anchor=CheckResult(name="anchor", passed=ok, command="run_l0(reference=True)",
                           exit_code=0 if ok else 1, passed_count=20 if ok else 19,
                           failed_count=0 if ok else 1, output_summary="20/20"),
        adjacent=CheckResult(name="adjacent", passed=True, command="python -m pytest ...",
                             exit_code=0, passed_count=5, output_summary="5 passed"),
        target_failed_before=True,
        temp_root="/tmp/cp-repair-xyz/repo",
        diff_sha256="cafe" * 16,
    )
    for key, value in overrides.items():
        setattr(report, key, value)
    return report


#: 测试用「被修复文件」的默认内容（配合 :func:`simple_diff` 的默认参数）
DEFAULT_OLD_CONTENT = "def add(a, b):\n    return a - b\n\n"
DEFAULT_NEW_CONTENT = "def add(a, b):\n    return a + b\n\n"


def make_unified_diff(path: str, old_content: str, new_content: str, *,
                      is_new: bool = False, is_deleted: bool = False) -> str:
    """用 :mod:`difflib` 造一份**真实可用**的统一 diff

    【为什么不手写字符串】手写的 ``@@ -1,3 +1,3 @@`` 很容易与正文行数不一致
    （实测踩过：上下文行数与 hunk 头不符 → 应用器判「找不到精确匹配」）。用
    ``difflib.unified_diff`` 生成可保证 hunk 头与正文严格自洽，也让测试的输入
    与真实子代理产物同构。
    """
    import difflib

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    body = list(difflib.unified_diff(old_lines, new_lines,
                                     fromfile=f"a/{path}", tofile=f"b/{path}", n=3))
    if is_new:
        # 真实 ``git diff`` 对新增文件的头部是 ``--- /dev/null``
        body[0] = "--- /dev/null\n"
    if is_deleted:
        body[1] = "+++ /dev/null\n"
    header = [f"diff --git a/{path} b/{path}\n"]
    if is_new:
        header.append("new file mode 100644\n")
    if is_deleted:
        header.append("deleted file mode 100644\n")
    return "".join(header + [line if line.endswith("\n") else line + "\n"
                             for line in body])


def simple_diff(path: str = "agent/demo_math.py", *,
                old_content: str = DEFAULT_OLD_CONTENT,
                new_content: str = DEFAULT_NEW_CONTENT,
                old: str = "", new: str = "") -> str:
    """造一个最小可用统一 diff（单文件；默认把 ``return a - b`` 改成 ``+``）

    ``old`` / ``new`` 是**便捷写法**：给定时按行替换 ``old_content`` 中的那一行
    （便于写「上下文故意对不上」的负例）。
    """
    if old or new:
        content = old_content.replace(old or "", new or "") if old else new_content
        return make_unified_diff(path, old_content, content)
    return make_unified_diff(path, old_content, new_content)


def new_file_diff(path: str, content: str) -> str:
    """新增文件的统一 diff"""
    return make_unified_diff(path, "", content, is_new=True)


def deleted_file_diff(path: str, content: str) -> str:
    """删除文件的统一 diff"""
    return make_unified_diff(path, content, "", is_deleted=True)


class FakeOutcome:
    """``ExecutionOutcome`` 的最小替身（只带 delegate 用到的字段）"""

    def __init__(self, *, payload: Optional[Dict[str, Any]] = None, ok: bool = True,
                 toolset: Optional[Dict[str, Any]] = None, error: str = "",
                 error_code: str = "", tier: str = "jsonl",
                 tool_violations: Sequence[Dict[str, Any]] = (), tokens: int = 1200,
                 delegation_id: str = "dlg-fake0001",
                 trace_id: str = "tr-fake0001") -> None:
        self.payload = dict(payload or {})
        self.ok = bool(ok)
        self.toolset = dict(toolset) if toolset is not None else {
            "actor": "sub_agent:dlg-fake0001",
            "tools": list(REPAIR_SUBAGENT_TOOLS),
            "authorized_subset": list(REPAIR_SUBAGENT_TOOLS),
            "denied_count": 0,
        }
        self.error = error
        self.error_code = error_code
        self.tier = tier
        self.tool_violations = tuple(tool_violations)
        self.delegation_id = delegation_id
        self.trace_id = trace_id
        self.duration_ms = 12.0
        self.artifacts = ()
        self.tool_calls = ()
        self.credentials = ()
        self.credentials_destroyed = True
        self.isolation = {}
        self.callback = {}
        self.task_file = ""

        class _Cost:
            counted_tokens = int(tokens)
            counted_cost_usd = 0.0

        class _Triad:
            is_complete = True

        self.cost = _Cost()
        self.triad = _Triad()

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": self.ok, "delegation_id": self.delegation_id,
                "trace_id": self.trace_id}


class FakeDelegationExecutor:
    """派工执行器桩：按脚本依次返回预设 ``FakeOutcome``；记录调用入参"""

    def __init__(self, outcomes: Sequence[Any]) -> None:
        self._outcomes: List[Any] = list(outcomes)
        self.calls: List[Dict[str, Any]] = []

    def execute(self, ctx: Any, *, tools: Sequence[str] = (),
                authorized_capabilities: Any = None, credentials: Any = (),
                parent_trace: Any = None, input_text: str = "") -> Any:
        self.calls.append({
            "ctx": ctx, "tools": tuple(tools),
            "authorized_capabilities": tuple(authorized_capabilities or ()),
            "input_text": input_text,
            "task_file": json.dumps(
                {k: getattr(ctx, k, None) for k in
                 ("goal", "artifact_format", "budget_tokens", "timeout_seconds",
                  "callback_url")}, ensure_ascii=False, default=str),
        })
        if not self._outcomes:
            raise AssertionError("FakeDelegationExecutor 无更多预设结果")
        item = self._outcomes.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class StubProbeExecutor:
    """探针执行器桩：按 ``(匹配子串 → 预设输出)`` 或固定队列返回

    用于三关逻辑的单测（不真跑 pytest）。构造时给 ``script``：
      - dict：按命令里出现的第一个匹配 key 选输出；
      - list：按调用顺序取。
    """

    def __init__(self, script: Any = None,
                 default: Optional[Any] = None) -> None:
        self.script = script
        self.default = default
        self.calls: List[Dict[str, Any]] = []

    @property
    def call_index(self) -> int:
        """已发生的调用序号（1 基）——供"按次序改行为"的探针使用"""
        return len(self.calls)

    def run(self, argv: Sequence[str], *, cwd: str, timeout: float, env: Any = None):
        from agent.repair.diagnose import ProbeOutput
        args = [str(a) for a in argv]
        self.calls.append({"argv": tuple(args), "cwd": str(cwd), "timeout": float(timeout)})
        chosen = self.default
        if isinstance(self.script, dict):
            for key, value in self.script.items():
                if any(key in a for a in args):
                    chosen = value
                    break
        elif isinstance(self.script, list):
            chosen = self.script.pop(0) if self.script else self.default
        if chosen is None:
            chosen = ProbeOutput(argv=tuple(args), exit_code=0, stdout="1 passed in 0.01s")
        if isinstance(chosen, ProbeOutput):
            out = ProbeOutput(**{**chosen.__dict__, "argv": chosen.argv or tuple(args)})
        else:
            out = ProbeOutput(argv=tuple(args), **chosen)
        return out


def make_run_logger(tmp_path, *, run_id: str = "rep-test0001",
                    emit_events: bool = True):
    """构造一个**隔离**的留痕器（Trace 库 / 审计库 / 事件目录都在 ``tmp_path`` 下）

    为什么是工厂函数而不是 fixture：夹具定义在 ``conftest.py``（见 ``tests/unit/
    conftest_repair.py``），而这个模块刻意不是 conftest（避免被 pytest 收集）；
    工厂函数两处都能用。
    """
    from agent.audit.facade import AuditFacade

    facade = AuditFacade(db_path=str(tmp_path / "audit.db"))
    logger = RepairRunLogger(
        RunLoggerConfig(repo_root=str(tmp_path), trace_db=str(tmp_path / "trace.db"),
                        audit=facade, events_dir=str(tmp_path / "events"),
                        emit_events=bool(emit_events)),
        run_id=run_id)
    return logger, facade


def close_run_logger(logger, facade) -> None:
    """收尾：flush Trace、停 writer、关审计（best-effort）"""
    try:
        logger.flush()
    except Exception:  # noqa: BLE001
        pass
    store = logger.store
    if store is not None:
        try:
            store.stop(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
    if facade is not None:
        try:
            facade.close()
        except Exception:  # noqa: BLE001
            pass


@pytest.fixture()
def policy() -> RepairPolicy:
    """默认策略（阈值与任务书一致：3 文件 / 120 行）"""
    return RepairPolicy()
