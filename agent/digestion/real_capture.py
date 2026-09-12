"""真实任务轨迹采集（TASK-S7-05 / v7.2 §4.5「真实数据打通」）

【为什么需要本模块】

S3 全链（清洗 → 挖掘 → 判定集 → 验收门 → 灰度 → 内化）已交付，但**从未在真实
数据上跑通**：`data/events/` 里没有一条 `digest.stage`、运行时统一台账里没有一条
**能力级**行、治理面板「验收 / 灰度 / 内化」因此恒为 0。经 TASK-S7-05 定位，
根因是**真实工具调用没有留下能力级统一 Trace**（S2-01 的 `TraceContext` 透传点
只在 `agent/tool_calling.py::ToolCaller._execute_safe` 内生效，本部署的真实
调用路径未经过它），既不是"面板不消费"，也不是"事件被吞"。

本模块提供**受控工作区里的真实任务执行 + 能力级真实 Trace 采集**，把这条断链接上：

- **真实任务指令**：自然语言指令写进 `SideEffects.notes` 的 ``intent:<文本>``
  通道（`cleaning.intent_key_for_trace` 的预留通道），经
  `cleaning.normalize_intent()` 归一为同类判定键的第二元 —— 同一类任务的不同
  目标模块因此自然归并到同一个 `same_task_key`；
- **真实工具调用**：直接调用 `agent.tools.file_tools.read_file` /
  `agent.tools.shell_tools.execute_shell` / `agent.tools.file_tools.write_file`
  —— 真读文件、真起子进程跑 pytest、真把报告写到盘上；
- **真实结果状态**：Trace 的 ``status`` / ``error_code`` 取真实返回值，
  ``duration_ms`` 取 ``perf_counter`` 实测，``side_effects`` 取真实落盘路径。

【真实性判定依据（报告口径，逐条可核）】

| # | 依据 | 证据位置 |
|---|---|---|
| 1 | 参数即实参 | `UnifiedTrace.request.args_redacted` == 传给真实工具的实参 |
| 2 | 结果即返回值 | `UnifiedTrace.response.output_redacted` == 真实工具返回（真实字节数/行数、真实退出码、真实 stdout/stderr） |
| 3 | 状态即判定 | `status` 由真实 ``ok`` 字段决定（``ok=False`` ⇒ ``error``） |
| 4 | 时长即实测 | `duration_ms` 由 ``perf_counter`` 在真实调用前后测得 |
| 5 | 副作用即事实 | `files_written` 取真实写入路径；`external_calls` 取真实起过的子进程标签 |
| 6 | 失败即失败 | 被测模块带**真实缺陷**时，其真实测试**真的失败**，任务级 Trace 落 ``error``；不补成功轨迹、不改写结果 |

**不得合成轨迹**：本模块不生成任何虚构的 Trace 行 —— 每一行都由一次真实调用产生。
受控工作区（`data/digestion/s705_demo/workspace/`）是**被测对象**，不是轨迹来源。

【纪律】

- 本模块**只写**传入的 `TraceFacade`；运行时目录由调用方显式传入（不接受隐式默认，
  以免污染 CI）；
- 不读写 `data/descriptors.json`，不推进 stage，不 emit 事件 —— 那是消化链的事。
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.digestion.real_capture")

#: 本采集器使用的三个**规范能力键**（与 `data/descriptors.json` 同键可 join）
CAP_READ_FILE = "cp.builtin.read_file"
CAP_SHELL_EXECUTE = "cp.builtin.shell_execute"
CAP_WRITE_FILE = "cp.builtin.write_file"

#: 采集器使用的**真实工具标签**（沙箱侧 `TOOL_REQUIRED_PARAMS` 认这三者）
LABEL_READ_FILE = "read_file"
LABEL_SHELL_EXECUTE = "shell_execute"
LABEL_WRITE_FILE = "write_file"

#: 真实任务指令模板（同类任务的**同一句话**，只有目标模块在变）
#: 归一后 `normalize_intent()` 会抹掉数字/路径 ⇒ 同类任务归并到同一 intent_key
TASK_INSTRUCTION = (
    "审计模块的公开行为并产出审计报告：读取源码，运行它的真实测试，"
    "仅当测试通过时写出审计报告"
)

#: 采集器写入的本地证据文件名
EVIDENCE_FILENAME = "s705_采集记录.json"

#: 默认工作区目录名（相对运行时根）
DEFAULT_DEMO_DIRNAME = os.path.join("data", "digestion", "s705_demo")
DEFAULT_WORKSPACE_DIRNAME = "workspace"


def _slash(path: str) -> str:
    """路径 → **正斜杠绝对路径**

    为什么统一成正斜杠：`ReplayEnv` 的路径守卫用 `_norm_path()` 把 ``\\`` 归一为
    ``/``，而用例的 ``expected_side_effects`` 直接复制台账里的**真实路径**。若真实
    路径写成 ``C:\\ws\\out/x.md``（Windows `os.path.join` 与相对段混用），回放时
    候选侧记的是归一后的 ``C:/ws/out/x.md`` —— 两侧字符串不等，副作用层会误判为
    "不一致"（实现期实测的真实缺陷）。故**从采集侧就统一口径**：真实路径本来就是
    这个（Windows 接受正斜杠），不做任何修饰。
    """
    return os.path.abspath(str(path)).replace("\\", "/")


# ════════════════════════════════════════════════════════════
#  受控真实工作区（被测对象 —— 不是轨迹来源）
# ════════════════════════════════════════════════════════════


@dataclass
class RealTaskSpec:
    """一个真实任务的规格（目标模块 = 真实文件）"""

    task_id: str
    module_rel: str
    test_rel: str
    report_rel: str
    buggy: bool = False
    instruction: str = TASK_INSTRUCTION

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "module_rel": self.module_rel,
                "test_rel": self.test_rel, "report_rel": self.report_rel,
                "buggy": bool(self.buggy), "instruction": self.instruction}


def _module_source(index: int, *, buggy: bool) -> str:
    """被测模块源码（真实、可运行；``buggy=True`` 时含**真实缺陷**）"""
    bias = '    """对读数求和（公开 API）。"""\n'
    body = ("    total = 0.0\n    for row in rows:\n"
            "        total += float(row)\n")
    if buggy:
        # 真实缺陷：遗漏 -1 偏置修正 ⇒ 与其真实测试的期望值不一致
        body += "    return total\n"
    else:
        body += "    return total + BIAS_CORRECTION\n"
    return (
        f'"""mod_{index:04d}：受控真实工作区中的被测模块（TASK-S7-05 审计对象）"""\n'
        "\n"
        "from __future__ import annotations\n"
        "\n"
        "#: 采集器偏置修正（公开常量）\n"
        "BIAS_CORRECTION = 0.5\n"
        "#: 读数上限（公开常量）\n"
        "READING_LIMIT = 100\n"
        "\n"
        "\n"
        "def classify(value: float) -> str:\n"
        '    """读数分级（公开 API）。"""\n'
        "    if value < 0:\n"
        '        return "negative"\n'
        "    if value > READING_LIMIT:\n"
        '        return "overflow"\n'
        '    return "normal"\n'
        "\n"
        "\n"
        "def summarize(rows):\n"
        + bias + body +
        "\n"
        "\n"
        "class ReadingBook:\n"
        '    """读数簿（公开类）。"""\n'
        "\n"
        "    def __init__(self) -> None:\n"
        "        self.rows = []\n"
        "\n"
        "    def add(self, value: float) -> None:\n"
        "        self.rows.append(float(value))\n"
        "\n"
        "    def total(self) -> float:\n"
        "        return summarize(self.rows)\n"
    )


def _test_source(index: int) -> str:
    """被测模块的真实测试（真实断言；缺陷模块会**真的失败**）"""
    return (
        f'"""mod_{index:04d} 的真实测试（TASK-S7-05 受控工作区）"""\n'
        "\n"
        "import os\n"
        "import sys\n"
        "\n"
        "sys.path.insert(0, os.path.join(os.path.dirname(__file__), \"..\", \"src\"))\n"
        "\n"
        f"from mod_{index:04d} import ReadingBook, classify, summarize\n"
        "\n"
        "\n"
        "def test_classify_normal():\n"
        '    assert classify(1.0) == "normal"\n'
        "\n"
        "\n"
        "def test_classify_overflow():\n"
        '    assert classify(1000.0) == "overflow"\n'
        "\n"
        "\n"
        "def test_summarize_applies_bias_correction():\n"
        "    assert summarize([1.0, 2.0]) == 3.5\n"
        "\n"
        "\n"
        "def test_reading_book_total():\n"
        "    book = ReadingBook()\n"
        "    book.add(2.0)\n"
        "    book.add(3.0)\n"
        "    assert book.total() == 5.5\n"
    )


def build_workspace(root: str, *, modules: int,
                    bug_every: int = 10) -> List[RealTaskSpec]:
    """建立受控真实工作区（真实源码 + 真实测试），返回任务规格清单

    Args:
        root: 工作区根目录（会被创建）。
        modules: 被测模块数量（每个模块 = 一个真实任务的目标）。
        bug_every: 每 N 个模块注入一个**真实缺陷**（其真实测试会真的失败）；
            ``0`` 表示不注入。注入的是**被测代码**的缺陷，不是轨迹的修饰。
    """
    src_dir = os.path.join(root, "src")
    tests_dir = os.path.join(root, "tests")
    out_dir = os.path.join(root, "out")
    for path in (src_dir, tests_dir, out_dir):
        os.makedirs(path, exist_ok=True)
    with open(os.path.join(root, "pytest.ini"), "w", encoding="utf-8") as fh:
        fh.write("[pytest]\naddopts = -q\n")

    specs: List[RealTaskSpec] = []
    for i in range(int(modules)):
        buggy = bool(bug_every) and (i + 1) % int(bug_every) == 0
        module_rel = f"src/mod_{i:04d}.py"
        test_rel = f"tests/test_mod_{i:04d}.py"
        report_rel = f"out/audit_{i:04d}.md"
        with open(os.path.join(root, module_rel), "w", encoding="utf-8") as fh:
            fh.write(_module_source(i, buggy=buggy))
        with open(os.path.join(root, test_rel), "w", encoding="utf-8") as fh:
            fh.write(_test_source(i))
        specs.append(RealTaskSpec(task_id=f"s705-real-{i:04d}",
                                  module_rel=module_rel, test_rel=test_rel,
                                  report_rel=report_rel, buggy=buggy))
    logger.info("[S7-05] 受控工作区就绪：%s（%d 模块，其中缺陷 %d 个）",
                root, len(specs), sum(1 for s in specs if s.buggy))
    return specs


# ════════════════════════════════════════════════════════════
#  真实任务执行 + 真实能力级 Trace
# ════════════════════════════════════════════════════════════


@dataclass
class RealTaskResult:
    """一次真实任务的执行结果（全部字段来自真实执行）"""

    task_id: str
    status: str
    error_code: str = ""
    trace_ids: Dict[str, str] = field(default_factory=dict)
    labels: List[str] = field(default_factory=list)
    durations_ms: Dict[str, float] = field(default_factory=dict)
    test_exit_code: Optional[int] = None
    report_written: bool = False
    module_lines: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {"task_id": self.task_id, "status": self.status,
                "error_code": self.error_code, "trace_ids": dict(self.trace_ids),
                "labels": list(self.labels),
                "durations_ms": {k: round(v, 3)
                                 for k, v in self.durations_ms.items()},
                "test_exit_code": self.test_exit_code,
                "report_written": bool(self.report_written),
                "module_lines": self.module_lines}


def _public_symbols(source: str) -> List[str]:
    """源码 → 公开符号名（真实 AST 解析；解析失败即返回空表，不臆造）"""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    names: List[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not str(node.name).startswith("_"):
                names.append(str(node.name))
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and not str(target.id).startswith("_"):
                    names.append(str(target.id))
    return sorted(set(names))


class RealTaskRunner:
    """真实任务执行器：真工具 + 真结果 + 真 Trace

    用法::

        runner = RealTaskRunner(facade=facade, workspace=ws, workspace_id="ws_s705")
        result = runner.run_task(spec)     # 真实执行三步，落三条能力级 Trace + 一条任务级
    """

    def __init__(self, *, facade: Any, workspace: str, workspace_id: str = "",
                 shell: str = "cmd", timeout: int = 120,
                 python: str = "python") -> None:
        self.facade = facade
        self.workspace = _slash(workspace)
        self.workspace_id = str(workspace_id or "ws_s705_real")
        self.shell = str(shell or "cmd")
        self.timeout = int(timeout or 120)
        self.python = str(python or "python")

    # ── 真实工具（薄封装，便于单测替换为桩；**不做任何结果的修饰**） ──

    def _tool_read_file(self, path: str, **kwargs: Any) -> Dict[str, Any]:
        from agent.tools.file_tools import read_file
        return read_file(path, **kwargs)

    def _tool_execute_shell(self, command: str, **kwargs: Any) -> Dict[str, Any]:
        from agent.tools.shell_tools import execute_shell
        return execute_shell(command, **kwargs)

    def _tool_write_file(self, path: str, content: str, **kwargs: Any) -> Dict[str, Any]:
        from agent.tools.file_tools import write_file
        return write_file(path, content, **kwargs)

    # ── 一步：真调用 + 真 Trace ─────────────────────────────

    def _call(self, *, task_id: str, capability_id: str, label: str,
              args: Dict[str, Any], fn: Any, notes: Sequence[str] = (),
              side_effects: Any = None) -> Tuple[Dict[str, Any], str, float]:
        """真实调用一次工具并落一条能力级 Trace（返回 (真实结果, trace_id, 实测 ms)）"""
        from agent.observability.trace_v2 import SideEffects

        started = time.perf_counter()
        try:
            result = fn()
        except Exception as e:  # noqa: BLE001 真实工具异常 → 如实记为 error，不吞
            result = {"ok": False, "error": f"{type(e).__name__}: {e}",
                      "error_code": type(e).__name__}
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not isinstance(result, dict):
            result = {"ok": True, "result": str(result)}
        ok = bool(result.get("ok", True))
        effects = side_effects if side_effects is not None else SideEffects()
        if notes:
            effects.notes = list(effects.notes or []) + [str(n) for n in notes]
        trace = self.facade.record(
            capability_id, args=dict(args), output=result,
            status="success" if ok else "error",
            error_code=("" if ok else str(result.get("error_code")
                                          or result.get("error") or "ToolError")),
            side_effects=effects, duration_ms=duration_ms,
        )
        return result, str(getattr(trace, "trace_id", "") or ""), duration_ms

    def run_task(self, spec: RealTaskSpec, *,
                 measure_lines: bool = True) -> RealTaskResult:
        """执行一个真实任务（读源码 → 跑真实测试 → 条件产出审计报告）

        任务级 Trace 的 ``status`` 取真实结果：被审计模块带真实缺陷 ⇒ 真实测试
        退出码非 0 ⇒ 任务级落 ``error``（这就是负样本，**不修饰**）。
        按任务指令「仅当测试通过时写出审计报告」，失败任务**真的不写报告** ——
        成功/失败两条路径的骨架差异是真实行为差异，不是编造的分支。
        """
        from agent.observability.trace_v2 import SideEffects

        module_abs = f"{self.workspace}/{spec.module_rel}"
        test_abs = f"{self.workspace}/{spec.test_rel}"
        report_abs = f"{self.workspace}/{spec.report_rel}"
        out = RealTaskResult(task_id=spec.task_id, status="error")

        self.facade.start(task_id=spec.task_id, workspace_id=self.workspace_id)

        # ① 真读源码（能力级 Trace；intent 通道携带**真实任务指令**）
        read_args = {"path": module_abs, "encoding": "utf-8"}
        result, trace_id, duration = self._call(
            task_id=spec.task_id, capability_id=CAP_READ_FILE,
            label=LABEL_READ_FILE, args=read_args,
            fn=lambda: self._tool_read_file(module_abs, encoding="utf-8"),
            notes=[f"intent:{spec.instruction}"])
        out.trace_ids[LABEL_READ_FILE] = trace_id
        out.labels.append(LABEL_READ_FILE)
        out.durations_ms[LABEL_READ_FILE] = duration
        content = str(result.get("content") or "")
        if measure_lines:
            # 真实行数取自**真实读取回来的正文**（read_file 的 `lines` 字段是行范围
            # 选择器，缺省为空 ⇒ 不能当成行数；此处不臆造，直接数真实内容）
            out.module_lines = len(content.splitlines()) if content else None

        # ② 真跑测试（能力级 Trace；真实子进程 + 真实退出码）
        cmd = f"{self.python} -m pytest {spec.test_rel} -q"
        shell_args = {"cmd": cmd, "shell": self.shell,
                      "cwd": self.workspace, "timeout": self.timeout}
        shell_result, trace_id, duration = self._call(
            task_id=spec.task_id, capability_id=CAP_SHELL_EXECUTE,
            label=LABEL_SHELL_EXECUTE, args=shell_args,
            fn=lambda: self._tool_execute_shell(
                cmd, shell=self.shell, cwd=self.workspace, timeout=self.timeout),
            side_effects=SideEffects(external_calls=[LABEL_SHELL_EXECUTE]))
        out.trace_ids[LABEL_SHELL_EXECUTE] = trace_id
        out.labels.append(LABEL_SHELL_EXECUTE)
        out.durations_ms[LABEL_SHELL_EXECUTE] = duration
        raw_code = shell_result.get("exit_code")
        try:
            out.test_exit_code = (None if raw_code is None
                                  else int(str(raw_code)))
        except (TypeError, ValueError):
            out.test_exit_code = None
        tests_ok = bool(shell_result.get("ok", False))

        # ③ 条件产出审计报告（真实写盘；仅当真实测试通过）
        if tests_ok:
            report = self._build_report(spec, content=content,
                                        shell_result=shell_result,
                                        module_abs=module_abs)
            write_args = {"path": report_abs, "content": report}
            write_result, trace_id, duration = self._call(
                task_id=spec.task_id, capability_id=CAP_WRITE_FILE,
                label=LABEL_WRITE_FILE, args=write_args,
                fn=lambda: self._tool_write_file(report_abs, report),
                side_effects=SideEffects(files_written=[report_abs]))
            out.trace_ids[LABEL_WRITE_FILE] = trace_id
            out.labels.append(LABEL_WRITE_FILE)
            out.durations_ms[LABEL_WRITE_FILE] = duration
            out.report_written = bool(write_result.get("ok", False))
            if not out.report_written:
                out.status = "error"
                out.error_code = str(write_result.get("error") or "WriteFailed")
                self.facade.finish(status="error", error_code=out.error_code)
                return out
            out.status = "success"
            self.facade.finish(status="success")
            return out

        # 真实测试失败 ⇒ 按指令不产出报告；任务级如实落 error
        out.status = "error"
        out.error_code = f"REAL_TEST_FAILED(exit={out.test_exit_code})"
        self.facade.finish(status="error", error_code=out.error_code)
        return out

    def _build_report(self, spec: RealTaskSpec, *, content: str,
                      shell_result: Dict[str, Any], module_abs: str) -> str:
        """审计报告正文 —— **全部来自真实结果**（真实行数/符号/退出码/stdout）"""
        lines = [line for line in str(shell_result.get("stdout") or "").splitlines()
                 if line.strip()]
        symbols = _public_symbols(content)
        return (
            f"# 审计报告 {os.path.basename(spec.module_rel)}\n"
            "\n"
            f"- 源码：`{module_abs}`\n"
            f"- 源码行数（真实 read_file 结果）：{len(content.splitlines())}\n"
            f"- 公开符号（真实 AST 解析）：{', '.join(symbols) or '(无)'}\n"
            f"- 真实测试：`{self.python} -m pytest {spec.test_rel} -q`"
            f" → exit {shell_result.get('exit_code')}\n"
            f"- 真实测试输出末行：{lines[-1] if lines else '(空)'}\n"
            "- 结论：通过\n"
        )


# ════════════════════════════════════════════════════════════
#  同类轨迹统计（门槛核对 —— 未达标如实保留，不硬凑）
# ════════════════════════════════════════════════════════════


def same_kind_summary(store: Any, capability_id: str, *,
                      threshold: int = 0, registry: Any = None,
                      intent: str = "") -> Dict[str, Any]:
    """按 `same_task_key()` 核对同类轨迹条数（含**未达门槛清单**）

    返回每个同类键的条数、成功/失败分布、代表 trace_id，以及
    ``below_threshold``（未达门槛的能力/键清单 —— 如实保留）。
    """
    from . import capability as capability_mod
    from .cleaning import group_by_same_task
    from .service import DigestionService

    svc = DigestionService(store=store, registry=registry, persist_drafts=False,
                           emit_events=False)
    rows, collect_meta = capability_mod.collect_rows(
        store, capability_id, limit=0, registry=registry)
    trajectories, clean_meta = svc.build_trajectories(
        rows, capability_id=capability_id, intent=intent, limit=0)
    buckets = group_by_same_task(trajectories)
    groups: List[Dict[str, Any]] = []
    for key, bucket in sorted(buckets.items()):
        groups.append({
            "key": key,
            "capability_id": bucket.key.capability_id,
            "intent_key": bucket.key.intent_key,
            "outcome": bucket.key.outcome,
            "size": bucket.size,
            "negative": bucket.negative_count,
            "meets_threshold": bool(threshold) and bucket.size >= int(threshold),
            "trace_ids": sorted({str(t.source_trace_id) for t in bucket.trajectories
                                 if str(t.source_trace_id)})[:5],
            "task_ids": sorted({str(t.trajectory_id) for t in bucket.trajectories})[:5],
        })
    success = [g for g in groups if g["outcome"] == "success"]
    best = max((g["size"] for g in success), default=0)
    return {
        "capability_id": capability_id,
        "threshold": int(threshold),
        "collect": {k: collect_meta.get(k) for k in
                    ("keys", "legacy_keys", "matched", "legacy_matched",
                     "trajectories", "trajectory_rows", "total_in_ledger")},
        "cleanup": {"trajectories": clean_meta["trajectories"],
                    "negative_trajectories": clean_meta["negative_trajectories"],
                    "intent_keys": list(clean_meta["intent_keys"]),
                    "noise_flags": list(clean_meta["noise_flags"])},
        "groups": groups,
        "success_groups": len(success),
        "max_success_size": best,
        "meets_threshold": bool(threshold) and best >= int(threshold),
        "below_threshold": [g for g in groups
                            if threshold and g["size"] < int(threshold)],
        "clock": "wall_clock(perf_counter；每条 Trace 的真实调用耗时)",
    }


def registry_coverage(store: Any, registry: Any, *,
                      threshold: int = 20) -> Dict[str, Any]:
    """台账全部能力 × 正式门槛 → **未达门槛能力清单**（如实保留，不硬凑）

    任务书步骤 2 要求"采集后核对同类轨迹条数，并保留未达门槛的能力清单"。单看目标
    能力会得出"已达标"的结论，却掩盖了"其余能力一条真实同类轨迹都没有"这件事 ——
    那正是本任务要暴露的真实瓶颈。故这里对**注册表的每一个 descriptor** 都算一次
    同类成功轨迹的最大组规模，并把未达标的逐条列出（含 `0 条`的能力）。
    """
    rows: List[Dict[str, Any]] = []
    try:
        descriptors = registry.list() if registry is not None else []
    except Exception as e:  # noqa: BLE001  台账不可用 ⇒ 如实标注，不猜
        return {"available": False, "reason": f"{type(e).__name__}: {e}",
                "threshold": int(threshold), "capabilities": [],
                "below_threshold": [], "meets": 0}
    for desc in descriptors:
        cid = str(getattr(getattr(desc, "meta", None), "id", "") or "")
        if not cid:
            continue
        summary = same_kind_summary(store, cid, threshold=int(threshold),
                                   registry=registry)
        stage = getattr(getattr(desc, "evolution", None), "stage", None)
        rows.append({
            "capability_id": cid,
            "stage": str(getattr(stage, "value", stage) or ""),
            "max_success_size": int(summary["max_success_size"]),
            "meets_threshold": bool(summary["meets_threshold"]),
        })
    below = [r for r in rows if not r["meets_threshold"]]
    return {
        "available": True,
        "threshold": int(threshold),
        "threshold_kind": "正式门槛（models.MIN_SAME_KIND_TRACES）",
        "capabilities": sorted(rows, key=lambda r: (-r["max_success_size"],
                                                    r["capability_id"])),
        "meets": len(rows) - len(below),
        "below_threshold": below,
        "below_threshold_count": len(below),
        "note": ("未达门槛的能力**如实保留**：真实流量只覆盖了主演示能力；"
                 "其余能力需要多少条真实任务详见报告『未完成事项与所需条件』"),
    }


def write_evidence(payload: Dict[str, Any], path: str) -> str:
    """把采集证据写成 JSON（调用方给显式路径 —— 不使用任何隐式默认）"""
    import json

    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=1, default=str)
    return path


def summarize_results(results: Iterable[RealTaskResult]) -> Dict[str, Any]:
    """真实执行结果汇总（成功/失败条数、退出码分布、trace 数）"""
    rows = list(results)
    ok = [r for r in rows if r.status == "success"]
    codes: Dict[str, int] = {}
    for r in rows:
        key = str(r.test_exit_code)
        codes[key] = codes.get(key, 0) + 1
    return {
        "tasks": len(rows),
        "success": len(ok),
        "failure": len(rows) - len(ok),
        "test_exit_codes": codes,
        "capability_traces": sum(len(r.trace_ids) for r in rows),
        "labels": sorted({label for r in rows for label in r.labels}),
        "buggy_task_ids": sorted(r.task_id for r in rows if r.report_written is False
                                 and r.status == "error"),
    }


__all__ = [
    "CAP_READ_FILE", "CAP_SHELL_EXECUTE", "CAP_WRITE_FILE",
    "LABEL_READ_FILE", "LABEL_SHELL_EXECUTE", "LABEL_WRITE_FILE",
    "TASK_INSTRUCTION", "EVIDENCE_FILENAME",
    "DEFAULT_DEMO_DIRNAME", "DEFAULT_WORKSPACE_DIRNAME",
    "RealTaskSpec", "RealTaskResult", "RealTaskRunner",
    "build_workspace", "same_kind_summary", "registry_coverage",
    "write_evidence", "summarize_results",
]
