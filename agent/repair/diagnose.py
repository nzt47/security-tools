"""步骤 1 · 体检器：L0 锚 + 目标测试套件 + 可选审计快照（TASK-S7-02）

【任务定位】
    任务书 §三 步骤 1 的「体检三件套」：
      ① L0 锚（``agent/eval`` + ``run_eval.py --layer L0``）
      ② 目标测试套件（``pytest --junitxml``）
      ③ 可选：审计链快照（复用 S2-02 ``verify_chain``）

【不易（不编造问题）】
    「无失败也是合法结果」——``DiagnosisReport.ok = True`` 时 ``failures`` 必须为空，
    且**不得**为了产出补丁而把"未评测/跳过/未运行"包装成失败。三件套里只有 L0 锚
    与测试套件能产生失败项；审计快照只作**旁证**（链校验失败会如实计入
    ``disclosures``，但不会伪造一个可修的"失败用例"）。

【不易（只读体检）】
    体检**绝不修改被体检的仓库**：pytest 只在给定仓库根内执行、junitxml 落到调用方
    指定的临时目录、git 只读。这条边界是「隔离验证必须在临时副本」的前置条件——
    如果体检本身就会写主工作区，后面再谈隔离就没有意义。

【变易】
    执行器（``ProbeExecutor``）可注入：测试用桩，真实路径走 ``SubprocessExecutor``。
    任务书 §八 已知坑 1（「不要真调外部 agent CLI」）的同类处理：体检同理——
    CI 上无需真跑全量套件，用桩即可验证逻辑。
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Mapping,
    Optional,
    Protocol as _Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

from agent.repair import gitio
from agent.repair.models import (
    AuditSnapshot,
    DiagnosisReport,
    FailureItem,
)
from agent.repair.policy import ANCHOR_LAYER, RepairPolicy
from agent.repair.trace import RepairRunLogger

logger = logging.getLogger("agent.repair.diagnose")

#: 默认体检范围（快而稳的冒烟子集）
#:
#: 【为什么不默认全量】体检是**每次修复都要跑**的一步；全量 tests/unit 有 547 个文件
#: （实测分钟级到十分钟级）。本任务只做手动触发，故默认取「与修复链路同源的冒烟集」，
#: 真实使用可用 ``--test-target`` 指定更宽的范围（口径在报告里如实标注）。
DEFAULT_TEST_TARGET: Tuple[str, ...] = (
    "tests/unit/test_subagent_delegation.py",
    "tests/unit/test_eval_runner.py",
)

#: 失败摘要与正文的截断长度（进提示词与报告；避免整段堆栈）
MESSAGE_LIMIT = 240
TEXT_LIMIT = 1200
#: junitxml 解析时的失败文本上限（防止超大报告撑爆内存）
XML_TEXT_LIMIT = 8000

#: 归一化时抹掉的不稳定片段（指纹必须可复现：同一根因恒得同一指纹）
#:
#: 【为什么**不**抹普通数字】行号与断言里的具体值是"根因"的一部分：
#: 抹掉它们会让 ``assert x == 2`` 与 ``assert x == 3`` 撞成同一指纹，
#: 归并视图随即失去意义（实测踩过）。只抹"跨机器必然不同"的四类：
#: 绝对路径、内存地址、耗时数字、带长度的指针式十六进制。
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_HEXADDR_RE = re.compile(r"0x[0-9a-fA-F]{4,}")
_WINPATH_RE = re.compile(r"[A-Za-z]:\\[^\s:'\"]+")
_POSIXPATH_RE = re.compile(r"/(?:home|Users|tmp|var|opt)/[^\s:'\"]+")
_DURATION_RE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|us|µs|s|sec|seconds|毫秒|秒)\b")
_JUNIT_LINE_RE = re.compile(r"(?P<file>[^\s:]+\.py):(?P<line>\d+)")


def _clean(text: Any, *, limit: int) -> str:
    """清洗文本：去 ANSI、压空白、截断（供人读的摘要）"""
    raw = _ANSI_RE.sub("", str(text or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    if len(raw) > int(limit):
        raw = raw[: int(limit) - 1].rstrip() + "…"
    return raw


def fingerprint(text: Any) -> str:
    """堆栈指纹：归一化后 sha256 前 16 位

    【归一化规则（为什么是这些）】同一根因在不同机器/不同运行下会产生**字面不同**的
    堆栈：绝对路径、临时目录名、内存地址、耗时数字、随机 id。若不对它们归一，
    指纹会把同一根因判成多条，归并视图就失效了。反之，**过度归一化**（如抹掉行号）
    会让不同根因撞指纹——故这里只抹"跨机器必然不同"的部分，保留代码行号与断言文本。
    """
    raw = _ANSI_RE.sub("", str(text or ""))
    raw = _WINPATH_RE.sub("<path>", raw)
    raw = _POSIXPATH_RE.sub("<path>", raw)
    raw = _HEXADDR_RE.sub("<addr>", raw)
    raw = _DURATION_RE.sub("<dur>", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════
#  执行器（可注入）
# ════════════════════════════════════════════════════════════


@dataclass
class ProbeOutput:
    """一次外部命令的结果（**只读探针**输出）

    Attributes:
        argv: 实际命令（复现用）。
        exit_code: 退出码（-1 = 未运行/超时）。
        stdout / stderr: 输出（可能被截断）。
        duration_ms: 耗时。
        timed_out: 是否超时。
        available: 命令是否真的执行了（False = 环境缺该命令）。
    """

    argv: Tuple[str, ...] = ()
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    timed_out: bool = False
    available: bool = True

    @property
    def command(self) -> str:
        return " ".join(str(a) for a in self.argv)

    @property
    def tail(self) -> str:
        """输出尾部摘要（报告用）"""
        text = (self.stdout or "").strip() or (self.stderr or "").strip()
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines[-12:])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": int(self.exit_code),
            "duration_ms": round(float(self.duration_ms), 2),
            "timed_out": bool(self.timed_out),
            "available": bool(self.available),
            "tail": self.tail,
        }


@runtime_checkable
class ProbeExecutor(_Protocol):
    """只读探针执行器协议（真实实现见 :class:`SubprocessExecutor`）

    【为什么是 ``Protocol`` 而不是普通基类】注入桩（测试）与真实实现应当平权：
    普通基类下 mypy 不认结构化实现，把 ``SubprocessExecutor`` 赋给 ``ProbeExecutor``
    标注的变量会报类型错，调用方只能靠 ``cast`` 掩盖。用协议后，任何实现了
    ``run()`` 的对象（含测试桩）都是合法执行器，类型检查与运行时一致。
    """

    def run(self, argv: Sequence[str], *, cwd: str, timeout: float,
            env: Optional[Mapping[str, str]] = None) -> ProbeOutput:  # pragma: no cover
        ...


@dataclass
class SubprocessExecutor:
    """真实执行器（``subprocess``）

    只做两件事：执行 + 超时。**不解释**输出（解释在解析层），因此换平台/换 runner
    时只需替换本类。
    """

    max_output_chars: int = 20000

    def run(self, argv: Sequence[str], *, cwd: str, timeout: float,
            env: Optional[Mapping[str, str]] = None) -> ProbeOutput:
        started = time.perf_counter()
        args = [str(a) for a in argv]
        full_env = dict(os.environ)
        if env:
            full_env.update({str(k): str(v) for k, v in env.items()})
        full_env.setdefault("PYTHONUTF8", "1")
        full_env.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            proc = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True,
                                  encoding="utf-8", errors="replace",
                                  timeout=float(timeout), env=full_env)
        except FileNotFoundError as exc:
            return ProbeOutput(argv=tuple(args), exit_code=-1, stderr=str(exc),
                               duration_ms=(time.perf_counter() - started) * 1000.0,
                               available=False)
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            if isinstance(out, bytes):  # pragma: no cover 平台相关
                out = out.decode("utf-8", "replace")
            return ProbeOutput(argv=tuple(args), exit_code=-1, stdout=str(out)[-self.max_output_chars:],
                               stderr=f"超时（{timeout}s）", timed_out=True,
                               duration_ms=(time.perf_counter() - started) * 1000.0)
        except OSError as exc:
            return ProbeOutput(argv=tuple(args), exit_code=-1, stderr=str(exc),
                               duration_ms=(time.perf_counter() - started) * 1000.0,
                               available=False)
        return ProbeOutput(
            argv=tuple(args), exit_code=int(proc.returncode),
            stdout=(proc.stdout or "")[-self.max_output_chars:],
            stderr=(proc.stderr or "")[-self.max_output_chars:],
            duration_ms=(time.perf_counter() - started) * 1000.0)


# ════════════════════════════════════════════════════════════
#  junitxml 解析
# ════════════════════════════════════════════════════════════


@dataclass
class TestRunSummary:
    """一次 pytest 运行的结构化摘要

    Attributes:
        output: 探针输出。
        failures: 失败/错误项。
        passed / failed / errors / skipped: 用例计数。
        parse_error: junitxml 缺失/损坏原因（非空时计数不可信，如实披露）。
    """

    output: ProbeOutput = field(default_factory=ProbeOutput)
    failures: List[FailureItem] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    parse_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "output": self.output.to_dict(),
            "failure_count": len(self.failures),
            "passed": int(self.passed),
            "failed": int(self.failed),
            "errors": int(self.errors),
            "skipped": int(self.skipped),
            "parse_error": self.parse_error,
        }


def _node_file(testcase: ET.Element) -> str:
    """从 ``<testcase>`` 提取测试文件（仓库相对 POSIX 路径）"""
    raw = str(testcase.get("file") or "").strip()
    if raw:
        return raw.replace("\\", "/")
    classname = str(testcase.get("classname") or "")
    if classname:
        return classname.replace(".", "/").replace("\\", "/")
    return ""


def _extract_location(text: str, fallback_file: str) -> Tuple[str, int]:
    """从失败正文里找**最贴近断言行**的文件:行号

    为什么取**最后一次**匹配：pytest 的堆栈自外向内打印，最后出现的本仓库文件行
    通常就是真正断言/报错处（前面的多是 pytest 内部帧）。
    """
    file = fallback_file
    line = 0
    for match in _JUNIT_LINE_RE.finditer(str(text or "")):
        cand = match.group("file").replace("\\", "/")
        if cand.endswith(".py"):
            file = cand
            line = int(match.group("line"))
    return file, line


def parse_junit_xml(path: str, *, repo_root: str = "") -> TestRunSummary:
    """解析 ``--junitxml`` 报告 → ``TestRunSummary``

    pytest 的 junit 结构：``testsuites`` → ``testsuite`` → ``testcase``，
    失败/错误分别以 ``<failure>`` / ``<error>`` 子元素承载正文；``<skipped>`` 计跳过。
    """
    summary = TestRunSummary()
    if not path or not os.path.exists(path):
        summary.parse_error = f"junitxml 不存在: {path or '(未指定)'}"
        return summary
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        summary.parse_error = f"junitxml 解析失败: {type(exc).__name__}: {exc}"
        return summary
    root = tree.getroot()
    suites = ([root] if root.tag == "testsuite"
              else list(root.iter("testsuite")))
    for suite in suites:
        for case in suite.iter("testcase"):
            name = str(case.get("name") or "")
            classname = str(case.get("classname") or "")
            node_id = f"{classname}::{name}" if classname else name
            file = _node_file(case)
            for kind, tag in (("failure", "failure"), ("error", "error")):
                node = case.find(tag)
                if node is None:
                    continue
                body = (node.text or node.get("message") or "")
                body = str(body)[:XML_TEXT_LIMIT]
                message = _clean(node.get("message") or body, limit=MESSAGE_LIMIT)
                loc_file, loc_line = _extract_location(body, file)
                item = FailureItem(
                    node_id=node_id, file=loc_file, line=loc_line, message=message,
                    text=_clean(body, limit=TEXT_LIMIT), kind=kind,
                    stack_fingerprint=fingerprint(
                        f"{kind}|{loc_file}|{loc_line}|{node.get('message') or ''}|{body}"))
                summary.failures.append(item)
                if kind == "failure":
                    summary.failed += 1
                else:
                    summary.errors += 1
            if case.find("skipped") is not None:
                summary.skipped += 1
            elif case.find("failure") is None and case.find("error") is None:
                summary.passed += 1
    return summary


def _extract_counts(text: str) -> Tuple[int, int, int, int]:
    """从 pytest 尾部摘要行提取计数（junitxml 不可用时的降级路径）

    形如 ``1 failed, 150 passed, 2 skipped in 3.21s`` / ``151 passed in 18.21s``。
    """
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for line in reversed(str(text or "").strip().splitlines()[-8:]):
        found = False
        for key, pattern in (("passed", r"(\d+)\s+passed"),
                             ("failed", r"(\d+)\s+failed"),
                             ("error", r"(\d+)\s+error"),
                             ("skipped", r"(\d+)\s+skipped")):
            match = re.search(pattern, line)
            if match:
                counts[key] = int(match.group(1))
                found = True
        if found:
            break
    return counts["passed"], counts["failed"], counts["error"], counts["skipped"]


# ════════════════════════════════════════════════════════════
#  最近改动（只读 git）
# ════════════════════════════════════════════════════════════


def _mtime(path: str) -> float:
    try:
        return float(os.path.getmtime(path))
    except OSError:
        return 0.0


def recent_changes(repo_root: str, *, policy: RepairPolicy,
                   interest: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """近期改动（只读 git 局部历史 + HEAD 提交文件清单 + mtime）

    【为什么三样都要】``git log`` 给「最近改了什么」，HEAD 文件清单给「上一次提交
    动了哪些文件（据此可把 mtime 归属到提交）」，mtime 给「工作区里谁刚被改过」。
    缺任何一样都会让「最近改动」这条线索变弱；但 git 不可用时**降级为空列表**并
    由调用方在 ``evidence_gaps`` 披露，绝不自造数据。
    """
    commits = gitio.recent_commits(repo_root, limit=int(policy.history_commits))
    head_files = gitio.head_changed_files(repo_root)
    out: List[Dict[str, Any]] = []
    for commit in commits:
        files = list(commit.get("files") or [])
        out.append({
            "sha": commit.get("sha", ""),
            "short": commit.get("short", ""),
            "subject": commit.get("subject", ""),
            "date": commit.get("date", ""),
            "file_count": len(files),
            "files": files[:20],
            "in_head_commit": bool(set(files) & set(head_files)),
        })
    for rel in head_files[:20]:
        full = os.path.join(repo_root, rel)
        out.append({
            "path": rel,
            "source": "head_commit",
            "mtime": _mtime(full),
            "tracked": True,
        })
    for rel in interest:
        rel_posix = str(rel).replace("\\", "/")
        full = os.path.join(repo_root, rel_posix)
        if os.path.exists(full):
            out.append({
                "path": rel_posix,
                "source": "interest",
                "mtime": _mtime(full),
                "tracked": gitio.is_tracked(repo_root, rel_posix),
            })
    return out


# ════════════════════════════════════════════════════════════
#  L0 锚 / 审计快照
# ════════════════════════════════════════════════════════════


def run_anchor(*, repo_root: str, anchor_dir: str = "") -> Tuple[Optional[bool], Dict[str, Any]]:
    """跑 L0 锚（**进程内**调用 S5-02 的 ``run_l0``，不重复实现评测）

    Returns:
        ``(ok, detail)``；``ok=None`` 表示**未运行**（锚不可用/完整性失败），
        detail 里如实写明原因——「未运行」不等于「通过」。
    """
    try:
        from agent.eval import anchor as A
        from agent.eval import runner as R
    except Exception as exc:  # noqa: BLE001
        return None, {"error": f"评测模块不可用：{type(exc).__name__}: {exc}",
                      "layer": ANCHOR_LAYER}
    try:
        # 锚目录口径交给 S5-02 的 ``resolve_anchor_dir``：显式参数 > 环境变量 >
        # 默认 ``<repo>/eval/l0_anchor``。**不要**把 repo_root 当锚目录传进去——
        # 那会把锚目录解析成仓库根，导致「找不到 manifest」被误判为"锚不可用"。
        store = A.AnchorStore(A.resolve_anchor_dir(anchor_dir))
        report = R.run_l0(store=store, reference=True, repo_root=repo_root,
                          compare_baseline=False)
    except Exception as exc:  # noqa: BLE001 锚完整性失败 → fail-closed 记未运行
        return None, {"error": f"L0 锚运行失败：{type(exc).__name__}: {exc}",
                      "layer": ANCHOR_LAYER}
    counts = report.counts()
    failed = int(counts.get(R.STATUS_FAIL, 0)) + int(counts.get(R.STATUS_ERROR, 0))
    detail: Dict[str, Any] = {
        "layer": ANCHOR_LAYER,
        "total": int(report.total),
        "counts": {str(k): int(v) for k, v in counts.items()},
        "failures": failed,
        "integrity_ok": bool((report.integrity or {}).get("ok", True)),
        "solver": report.solver,
    }
    return (failed == 0 and detail["integrity_ok"]), detail


def audit_snapshot(*, enabled: bool, limit: int = 1) -> AuditSnapshot:
    """取审计链快照（**可选第三件套**；复用 S2-02 ``verify_chain``）

    Args:
        enabled: 是否真的取（False → ``AuditSnapshot(enabled=False)``，
            **不是**"链没问题"）。
        limit: 校验条数上限（0/负数 → 全链）。
    """
    if not enabled:
        return AuditSnapshot(enabled=False)
    try:
        from agent.audit.facade import audit as _audit
    except Exception as exc:  # noqa: BLE001
        return AuditSnapshot(enabled=True, error=f"审计 facade 不可用：{type(exc).__name__}")
    try:
        kwargs: Dict[str, Any] = {}
        entries = _audit.recent(limit=1)
        head = entries[0] if entries else None
        result = _audit.verify(**kwargs)
        chain_ok = getattr(result, "ok", None)
        issues_raw = getattr(result, "issues", None) or ()
        issues: Tuple[str, ...] = tuple(str(i)[:200] for i in list(issues_raw)[:5])
        chain = getattr(_audit, "chain", None)
        count = int(getattr(chain, "next_seq", 0) or 0) - 1 if chain is not None else 0
        return AuditSnapshot(
            enabled=True, chain_ok=(bool(chain_ok) if chain_ok is not None else None),
            entries=max(0, count), head_hash=str(getattr(head, "self_hash", "") or "")[:16],
            issues=issues)
    except Exception as exc:  # noqa: BLE001
        return AuditSnapshot(enabled=True, error=f"审计快照失败：{type(exc).__name__}: {exc}")


# ════════════════════════════════════════════════════════════
#  体检主入口
# ════════════════════════════════════════════════════════════


def pytest_argv(targets: Sequence[str], *, junit_path: str,
                extra: Sequence[str] = ()) -> Tuple[str, ...]:
    """构造体检用的 pytest 命令（**只读**：不改仓库，只写 junitxml 到指定路径）

    - ``-p no:randomly``：随机顺序会让「同一根因的失败集合」在不同次运行间漂移，
      妨碍指纹归并；体检取确定性顺序（不影响被测代码）。
    - ``--continue-on-collection-errors``：单个文件收集失败时仍跑完其余用例，
      否则 junitxml 会缺一大块，"有失败但看不见"就发生了。
    """
    args: List[str] = [
        "python", "-m", "pytest", "-q", "--tb=short", "--no-header",
        "-p", "no:randomly", "--continue-on-collection-errors",
        f"--junitxml={junit_path}",
    ]
    args += [str(t) for t in (targets or DEFAULT_TEST_TARGET)]
    args += [str(e) for e in extra]
    return tuple(args)


def diagnose(*, repo_root: str, run_id: str = "", targets: Sequence[str] = (),
             policy: Optional[RepairPolicy] = None, executor: Optional[ProbeExecutor] = None,
             junit_path: str = "", timeout: float = 900.0, with_anchor: bool = True,
             anchor_dir: str = "", with_audit: bool = False,
             env: Optional[Mapping[str, str]] = None,
             run_logger: Optional[RepairRunLogger] = None) -> DiagnosisReport:
    """体检：跑目标测试套件（+ 可选 L0 锚 / 审计快照）→ ``DiagnosisReport``

    Args:
        repo_root: 被体检仓库根（**体检在调用方给定的根内进行**；隔离验证环节
            由 ``verify`` 传临时副本根，体检环节传真实根 —— 二者口径一致）。
        run_id: 运行标识（缺省由调用方生成）。
        targets: pytest 目标（文件/目录/``::用例``）；空 → ``DEFAULT_TEST_TARGET``。
        policy: 策略（切片的上下文半径等；体检本身只用默认值）。
        executor: 探针执行器（可注入；缺省 ``SubprocessExecutor``）。
        junit_path: junitxml 落盘路径（**必须在临时目录或产物目录**，不得污染仓库）。
        timeout: pytest 超时（秒）。
        with_anchor: 是否同跑 L0 锚（默认 True：体检就该看到标尺状态）。
        anchor_dir: 锚目录（缺省 ``<repo_root>/eval/l0_anchor``）。
        with_audit: 是否取审计快照（默认 **False**：审计链是旁证，且默认库可能很大）。
        env: 追加环境变量（透传给探针）。
        run_logger: 留痕器（传入则额外记录一条 ``diagnose`` 明细）。

    Returns:
        ``DiagnosisReport``（``ok=True`` 表示**未发现失败**，是合法结果）。
    """
    policy = policy or RepairPolicy()
    # 显式标注为协议类型：否则 mypy 会把 ``executor`` 推断成
    # ``ProbeExecutor | SubprocessExecutor``，进而报"None 没有 run"的假错。
    probe: ProbeExecutor = executor if executor is not None else SubprocessExecutor()
    probe_targets = tuple(targets) if targets else DEFAULT_TEST_TARGET
    started = time.perf_counter()
    disclosures: List[str] = []

    junit_path = junit_path or os.path.join(repo_root, ".pytest_junit_repair.xml")
    argv = pytest_argv(probe_targets, junit_path=junit_path)
    output = probe.run(argv, cwd=repo_root, timeout=float(timeout), env=env)
    summary = parse_junit_xml(junit_path, repo_root=repo_root)
    summary.output = output

    if summary.parse_error:
        # 降级：从 stdout 尾部摘要行提计数（**不计**失败项——没有结构化失败就不编造）
        passed, failed, errors, skipped = _extract_counts(output.stdout + output.stderr)
        summary.passed, summary.failed = passed, failed
        summary.errors, summary.skipped = errors, skipped
        disclosures.append(
            f"junitxml 不可用（{summary.parse_error}）→ 退化为按退出码/摘要行判定；"
            f"失败项清单为空，**不代表无失败**")
        if output.exit_code != 0 and not summary.failures:
            disclosures.append(
                f"pytest 退出码 {output.exit_code} 非零但无结构化失败项 → "
                f"如实标注「有失败但无法定位」，不编造问题")
    if not output.available:
        disclosures.append(f"探针不可用：{output.stderr.strip()[:200] or '未知原因'}")

    anchor_ok: Optional[bool] = None
    anchor_detail: Dict[str, Any] = {}
    if with_anchor:
        anchor_ok, anchor_detail = run_anchor(repo_root=repo_root, anchor_dir=anchor_dir)
        if anchor_ok is None:
            disclosures.append(
                f"L0 锚**未运行**（{anchor_detail.get('error', '未知')}）——"
                f"未运行不等于通过")
    else:
        disclosures.append("L0 锚本次未运行（--no-anchor）——未运行不等于通过")

    snapshot = audit_snapshot(enabled=bool(with_audit))
    if snapshot.enabled and snapshot.chain_ok is False:
        disclosures.append(
            f"审计链校验未通过（{len(snapshot.issues)} 条问题）——"
            f"审计问题**不可自动修复**（只读区），仅如实记录")

    changes = recent_changes(repo_root, policy=policy,
                             interest=[f.file for f in summary.failures if f.file][:10])

    by_fp: Dict[str, int] = {}
    for item in summary.failures:
        by_fp[item.stack_fingerprint] = by_fp.get(item.stack_fingerprint, 0) + 1
        full = os.path.join(repo_root, item.file) if item.file else ""
        item.source_mtime = _mtime(full) if full else 0.0

    report = DiagnosisReport(
        run_id=run_id, repo_root=repo_root, ok=(not summary.failures),
        failures=list(summary.failures), failures_by_fingerprint=by_fp,
        tests_passed=int(summary.passed), tests_failed=int(summary.failed),
        tests_error=int(summary.errors), tests_skipped=int(summary.skipped),
        test_command=output.command, junit_path=junit_path, anchor_ok=anchor_ok,
        anchor_detail=anchor_detail, audit=snapshot, recent_changes=changes,
        disclosures=disclosures,
        duration_ms=(time.perf_counter() - started) * 1000.0)

    if run_logger is not None:
        run_logger.record_step(
            "diagnose", subject=",".join(probe_targets)[:200],
            status="ok" if report.ok else "error",
            detail={"failure_count": report.failure_count,
                    "tests_failed": report.tests_failed,
                    "anchor_ok": report.anchor_ok,
                    "command": report.test_command[:200]},
            error="" if report.ok else f"发现 {report.failure_count} 条失败")
    return report


def clean_text(text: Any, *, limit: int = MESSAGE_LIMIT) -> str:
    """公开的文本清洗入口（``_clean`` 的导出别名，供其他模块复用同一口径）"""
    return _clean(text, limit=limit)


__all__ = [
    "DEFAULT_TEST_TARGET", "MESSAGE_LIMIT", "TEXT_LIMIT", "XML_TEXT_LIMIT",
    "ProbeOutput", "ProbeExecutor", "SubprocessExecutor", "TestRunSummary",
    "clean_text", "fingerprint", "parse_junit_xml", "recent_changes", "run_anchor",
    "audit_snapshot", "pytest_argv", "diagnose",
]
