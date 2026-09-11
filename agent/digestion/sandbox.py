"""确定性回放沙箱（TASK-S3-02 步骤 2 / v7.2 §4.5 · §5.2 TEST 约束）

**职责**：把一组 `EquivalenceCase` 在**确定性、无真实副作用**的环境里对
"上游/现有实现"与"候选原生实现"**双跑**，并按 §4.5 的**三层比对**给出 diff。

三条守不易的契约（全部可被用例断言）：

1. **副作用只记录不双写** —— 环境是**内存虚拟文件系统**：`ReplayEnv` 只把写/删/
   外部调用记入 `side_effects`，`commit()` **直接抛错**（本沙箱不提供真实落盘通道）。
   出界路径（沙箱根之外）→ `SandboxEscapeError`，运行以 `escape_blocked` 收尾。
2. **确定性** —— 无墙钟、无随机、无真实 I/O：延迟取**模型时钟**（`TOOL_LATENCY_MS`
   标称值累加），故同一输入同一环境两次回放逐字段一致（`determinism_probe()`）。
   这点是刻意的：验收门的 p99 比对若依赖墙钟，会在 CI 覆盖率插桩下抖动
   （S3-01 §4.8 的实证），故本沙箱的耗时是**模型量**，报告里如实标注来源。
3. **三层比对（§4.5）** —— 结构 schema **硬性** → 副作用集合 **硬性** →
   judge ≥0.85 **软性** + 10% 人工抽检清单。任一层失败都在 `DiffResult.layers`
   里有独立、可读的理由，而不是一个笼统的 False。

**边界（不越权）**：本沙箱是**进程内确定性执行模型**，不是容器隔离；§5.2 的
"生成代码一律 Docker" 属执行型产物的隔离要求，由 S3-03/生产化承接。回放的目标是
**行为等价性判定**，不是执行不可信代码。

**import 纪律**：只依赖同包 `cases`/`models`；judge 可注入（默认为确定性本地打分器，
LLM-judge 由 S3-03 经 `judge=` 注入），故本模块无网络、无模型调用、无文件 I/O
（唯一写盘是 `RecordReplayJournal` 自己的 journal 目录）。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .cases import (
    DEFAULT_SANDBOX_ROOT,
    SIDE_EFFECT_KINDS,
    EquivalenceCase,
    ProgramStep,
    canonical_json,
    positional_slot_names,
    synthesize_value,
)
from .generalize import is_placeholder, normalize_param_value, shape_placeholder
from .models import CandidatePattern

logger = logging.getLogger("agent.digestion.sandbox")

# ════════════════════════════════════════════════════════════
#  常量（配额与门槛单点定义）
# ════════════════════════════════════════════════════════════

#: 回放层名（§4.5 三层比对）
LAYER_STRUCTURE = "structure"
LAYER_SIDE_EFFECTS = "side_effects"
LAYER_JUDGE = "judge"
LAYER_KINDS: Tuple[Tuple[str, str], ...] = (
    (LAYER_STRUCTURE, "hard"),
    (LAYER_SIDE_EFFECTS, "hard"),
    (LAYER_JUDGE, "soft"),
)

#: judge 软性门槛与人工抽检比例（§4.5）
JUDGE_THRESHOLD = 0.85
MANUAL_SAMPLE_RATIO = 0.10

#: 观测状态
OBS_SUCCESS = "success"
OBS_ERROR = "error"
OBS_QUOTA_EXCEEDED = "quota_exceeded"
OBS_ESCAPE_BLOCKED = "escape_blocked"
OBS_UNBOUND_INPUT = "unbound_input"
OBS_DENIED = "denied"

#: 错误码（§11.6.0 归一：invalid=不可重试；quota/denied=需人工或配额调整）
ERR_UNBOUND_INPUT = "E_SANDBOX_UNBOUND_INPUT"
ERR_ESCAPE = "E_SANDBOX_ESCAPE"
ERR_QUOTA_STEPS = "E_SANDBOX_QUOTA_STEPS"
ERR_QUOTA_FILES = "E_SANDBOX_QUOTA_FILES"
ERR_QUOTA_BYTES = "E_SANDBOX_QUOTA_BYTES"
ERR_QUOTA_EXTERNAL = "E_SANDBOX_QUOTA_EXTERNAL"
ERR_QUOTA_TIME = "E_SANDBOX_QUOTA_TIME"
ERR_EXTERNAL_DENIED = "E_SANDBOX_DENIED"

#: 外部调用类标签（**永不真实执行**；仅记账并返回确定性桩结果）
EXTERNAL_LABELS = frozenset({
    "shell_execute", "run_command", "execute", "http_request", "http_get",
    "http_post", "network_call", "web_search", "git_push", "deploy",
    "send_email", "publish",
})

#: 标称延迟（毫秒；**模型时钟**，非墙钟 —— 保证确定性）
TOOL_LATENCY_MS: Dict[str, float] = {
    "read_file": 3.0, "list_dir": 2.0, "grep": 4.0, "stat": 1.0,
    "write_file": 6.0, "append_file": 6.0, "apply_patch": 8.0,
    "delete_file": 5.0,
}
DEFAULT_LATENCY_MS = 5.0

#: 各（虚拟）工具**契约要求的必需参数**（供"接口补齐"用；见 `complete_params`）
TOOL_REQUIRED_PARAMS: Dict[str, Tuple[str, ...]] = {
    "read_file": ("path",), "write_file": ("path",), "append_file": ("path",),
    "delete_file": ("path",), "apply_patch": ("path",),
    "replace_in_file": ("path",), "create_dir": ("path",), "stat": ("path",),
    "list_dir": (), "grep": ("pattern",), "shell_execute": ("cmd",),
}
#: 已知参数名（供"槽名 → 原始键名"还原；见 `restore_param_key`）
KNOWN_PARAM_NAMES = frozenset(
    {"path", "content", "text", "encoding", "pattern", "query", "patch",
     "cmd", "command", "file", "target", "dir"}
    | {name for names in TOOL_REQUIRED_PARAMS.values() for name in names})
_TRAILING_ORDINAL = re.compile(r"^(?P<base>[A-Za-z_][A-Za-z0-9_.\-]*?)_(?P<n>\d+)$")

#: 配额环境变量前缀（可调参数走 .env/config；非法值回退默认）
QUOTA_ENV_PREFIX = "CP_DIGESTION_SANDBOX"

JOURNAL_FILENAME = "journal.jsonl"
DEFAULT_JOURNAL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "digestion", "replay")

#: 视作"路径参数"的键名（沙箱根守卫的作用域）
_PATH_PARAM_HINTS = ("path", "file", "filepath", "file_path", "target", "dest",
                     "destination", "src", "source", "dir", "directory", "root")


class SandboxError(Exception):
    """回放沙箱基类异常"""


class SandboxEscapeError(SandboxError):
    """访问沙箱根之外的路径（"绝不双写真实环境"的第一道闸）"""


class SandboxQuotaExceeded(SandboxError):
    """超出资源配额（§5.2 TEST 约束：资源配额上限 + 无真实数据）"""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ════════════════════════════════════════════════════════════
#  配额
# ════════════════════════════════════════════════════════════


@dataclass
class SandboxQuota:
    """回放资源配额（默认值对齐 §5.2 TEST 约束；`from_env()` 支持可调）"""

    max_steps: int = 32
    max_files_written: int = 16
    max_deleted: int = 16
    max_external_calls: int = 8
    max_bytes: int = 65536
    sim_budget_ms: float = 60000.0
    deny_external: bool = False
    #: 只读契约：恒为 False（本沙箱**不提供**真实 I/O 通道）
    allow_real_io: bool = False

    def __post_init__(self) -> None:
        if self.allow_real_io:
            logger.warning("allow_real_io 被强制为 False：回放沙箱不提供真实 I/O 通道")
            self.allow_real_io = False
        for name in ("max_steps", "max_files_written", "max_deleted",
                     "max_external_calls", "max_bytes"):
            value = int(getattr(self, name) or 0)
            setattr(self, name, value if value > 0 else 1)
        budget = float(self.sim_budget_ms or 0.0)
        self.sim_budget_ms = budget if budget > 0 else 1.0

    @classmethod
    def from_env(cls, env: Optional[Dict[str, str]] = None) -> "SandboxQuota":
        """从 ``CP_DIGESTION_SANDBOX_*`` 读取配额（非法值回退默认，不抛错）"""
        source = os.environ if env is None else env
        mapping = {
            "max_steps": ("MAX_STEPS", int),
            "max_files_written": ("MAX_FILES_WRITTEN", int),
            "max_deleted": ("MAX_DELETED", int),
            "max_external_calls": ("MAX_EXTERNAL_CALLS", int),
            "max_bytes": ("MAX_BYTES", int),
            "sim_budget_ms": ("SIM_BUDGET_MS", float),
        }
        kwargs: Dict[str, Any] = {}
        for field_name, (suffix, caster) in mapping.items():
            raw = str(source.get(f"{QUOTA_ENV_PREFIX}_{suffix}", "") or "").strip()
            if not raw:
                continue
            try:
                kwargs[field_name] = caster(raw)
            except (TypeError, ValueError):
                logger.warning("[Sandbox] 非法 %s_%s=%r，使用默认",
                               QUOTA_ENV_PREFIX, suffix, raw)
        raw_deny = str(source.get(f"{QUOTA_ENV_PREFIX}_DENY_EXTERNAL", "") or "").strip().lower()
        if raw_deny:
            kwargs["deny_external"] = raw_deny in ("1", "true", "yes", "on")
        return cls(**kwargs)


# ════════════════════════════════════════════════════════════
#  条件求值（S3-01 遗留 #4：参数级条件扩展点）
#  —— 放在 cases 之上的最小层，供沙箱的条件步与验收门的分支覆盖共用
#  —— 实现于本模块，故 `cases` 保持纯模型；`gate` 直接复用这里的求值器
# ════════════════════════════════════════════════════════════

#: 逻辑连接词（S3-01 `mining._condition_text` 用 " 且 " 连接决策树路径）
_OR_SPLIT = re.compile(r"\s+(?:或|or)\s+")
_AND_SPLIT = re.compile(r"\s+(?:且|and)\s+")
_STEP_PRESENT = re.compile(r"步骤\s*`?([^`\s]+)`?\s*(出现|存在|缺失|不存在)")
_STEP_COUNT = re.compile(r"(?:步数|steps)\s*(>=|<=|==|>|<)\s*(\d+)")
_KV_OP = re.compile(
    r"^(?:params\.)?\$?\{?([A-Za-z_][A-Za-z0-9_.\-]*)\}?\s*"
    r"(contains|包含|==|!=|matches|匹配)\s*(.+)$")
_KEY_ONLY = re.compile(r"^(?:params\.)?\$?\{?([A-Za-z_][A-Za-z0-9_.\-]*)\}?\s*"
                       r"(出现|存在|缺失|不存在)$")

#: 破坏性动作关键词（§3.2 `risk=destructive` 的文本线索）
DESTRUCTIVE_TOKENS: Tuple[str, ...] = (
    "force", "overwrite", "覆盖", "delete", "删除", "remove", "rm ", "unlink",
    "drop", "truncate", "prune", "purge", "wipe", "destroy", "reset --hard",
    "clean -", "--force", "-rf", "清空", "破坏",
)


@dataclass
class ConditionContext:
    """条件求值上下文（步骤标签序列 + 参数 + 输出 + 步数）"""

    labels: Tuple[str, ...] = ()
    params: Dict[str, Any] = field(default_factory=dict)
    output: Dict[str, Any] = field(default_factory=dict)
    step_count: int = 0


def _strip_quotes(text: str) -> str:
    out = str(text or "").strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'`":
        out = out[1:-1]
    return out


def _param_of(ctx: ConditionContext, key: str) -> Any:
    for container in (ctx.params, ctx.output):
        if key in (container or {}):
            return container[key]
    return None


def _eval_atom(atom: str, ctx: ConditionContext) -> Tuple[Optional[bool], str]:
    """单个原子条件 → ``(True/False/None(不可判定), 说明)``"""
    text = str(atom or "").strip()
    if not text:
        return None, "空条件"
    match = _STEP_PRESENT.search(text)
    if match:
        label, verb = match.group(1), match.group(2)
        present = label in tuple(ctx.labels)
        want = verb in ("出现", "存在")
        return (present == want), f"步骤 `{label}` {'出现' if present else '缺失'}"
    match = _STEP_COUNT.search(text)
    if match:
        op, raw = match.group(1), int(match.group(2))
        count = int(ctx.step_count or len(ctx.labels))
        ok = False
        if op == ">=":
            ok = count >= raw
        elif op == "<=":
            ok = count <= raw
        elif op == ">":
            ok = count > raw
        elif op == "<":
            ok = count < raw
        else:
            ok = count == raw
        return bool(ok), f"步数 {count} {op} {raw}"
    match = _KV_OP.match(_strip_quotes(text)) or _KV_OP.match(text)
    if match:
        key = match.group(1)
        op = match.group(2)
        operand = _strip_quotes(match.group(3))
        value = _param_of(ctx, key)
        text_value = "" if value is None else str(value)
        if op in ("contains", "包含"):
            return (operand in text_value), f"{key} 含 {operand!r}"
        if op == "==":
            return (text_value == operand), f"{key} == {operand!r}"
        if op == "!=":
            return (text_value != operand), f"{key} != {operand!r}"
        try:
            return bool(re.search(operand, text_value)), f"{key} 匹配 /{operand}/"
        except re.error:
            return None, f"正则非法: {operand!r}"
    match = _KEY_ONLY.match(text)
    if match:
        key, verb = match.group(1), match.group(2)
        present = _param_of(ctx, key) is not None
        want = verb in ("出现", "存在")
        return (present == want), f"参数 {key} {'存在' if present else '缺失'}"
    return None, f"不可判定条件: {text!r}"


def evaluate_condition(condition: str, ctx: ConditionContext) -> Dict[str, Any]:
    """条件 → 求值明细（``或`` 分组的**析取**，组内 ``且`` 为**合取**）

    Returns:
        ``{"matched": bool, "atoms": [(原子, True/False/None, 说明), ...],
        "unknown": [不可判定原子], "groups": 或分组数}``

        ``None`` 表示**不可判定**（例：正则非法、未知语法）—— 刻意**不静默当作
        False**：调用方须把不可判定原子上报（`condition_unknown_atoms()`），
        否则"没覆盖"与"判不了"会被混为一谈。
    """
    atoms: List[Tuple[str, Optional[bool], str]] = []
    unknown: List[str] = []
    matched_any = False
    groups = 0
    text = str(condition or "").strip()
    if not text:
        return {"matched": True, "atoms": [], "unknown": [], "groups": 0}
    for or_part in _OR_SPLIT.split(text):
        group_atoms = [a.strip() for a in _AND_SPLIT.split(or_part) if a.strip()]
        if not group_atoms:
            continue
        groups += 1
        group_ok = True
        for atom in group_atoms:
            value, reason = _eval_atom(atom, ctx)
            atoms.append((atom, value, reason))
            if value is None:
                unknown.append(atom)
                group_ok = False
            elif not value:
                group_ok = False
        matched_any = matched_any or group_ok
    return {"matched": matched_any, "atoms": atoms, "unknown": unknown,
            "groups": groups}


def condition_matches(condition: str, ctx: ConditionContext) -> bool:
    """条件是否成立（不可判定原子按其所在分组判否，并记 debug 日志）"""
    result = evaluate_condition(condition, ctx)
    if result["unknown"]:
        logger.debug("条件含不可判定原子：%s", result["unknown"])
    return bool(result["matched"])


def condition_unknown_atoms(condition: str, ctx: ConditionContext) -> List[str]:
    """不可判定原子清单（供验收门如实上报，而非静默判 False）"""
    return list(evaluate_condition(condition, ctx)["unknown"])


def is_destructive_condition(condition: str, *, outcome: str = "",
                             advice: str = "") -> bool:
    """分支是否属**破坏性**（§4.5 验收门第 4 条覆盖对象）

    两类都算：① 条件/建议文本含破坏性动作线索（force/delete/覆盖…）；
    ② 该分支历史倾向为 **failure**（失败倾向分支必须被用例覆盖，否则无法
    证明候选实现在失败路径上等价）。
    """
    text = f"{condition} {advice}".lower()
    if any(token in text for token in DESTRUCTIVE_TOKENS):
        return True
    return str(outcome or "") == "failure"


def classify_condition(condition: str) -> Dict[str, Any]:
    """分支条件 → 可覆盖性分类（决定"必须覆盖"还是"登记观察"）

    判定集回放是**输入驱动**的确定性执行：能由输入决定的参数级分支可以被用例
    覆盖；而"步骤 ``X`` 出现/缺失""步数 ≥ N"这类**轨迹形状**分支描述的是历史
    轨迹的形态差异（S3-01 决策树的产物），不是可回放的输入 —— 把它当作"必须被
    可执行用例覆盖"会把**任务级中止**误判成可输入的分支。故：

    - ``structural``（全部原子都是步骤形状/步数）：登记为**观察项**，不阻断通行证；
    - ``addressable``（全部原子都是参数/输出原子）：**必须覆盖**，未覆盖即拒；
    - 其余（混合或含不可判定原子）：**保守归为必须覆盖**（宁可拦下不可放过）。
    """
    atoms = [atom for part in _OR_SPLIT.split(str(condition or ""))
             for atom in _AND_SPLIT.split(part) if atom.strip()]
    structural = 0
    addressable = 0
    unknown: List[str] = []
    for atom in atoms:
        text = atom.strip()
        if _STEP_PRESENT.search(text) or _STEP_COUNT.search(text):
            structural += 1
            continue
        if _KV_OP.match(_strip_quotes(text)) or _KV_OP.match(text) \
                or _KEY_ONLY.match(text):
            addressable += 1
            continue
        unknown.append(text)
    if not atoms:
        return {"kind": "empty", "structural": 0, "addressable": 0,
                "unknown": [], "required": False}
    if unknown:
        return {"kind": "unknown", "structural": structural,
                "addressable": addressable, "unknown": unknown, "required": True}
    if structural and not addressable:
        return {"kind": "structural", "structural": structural,
                "addressable": 0, "unknown": [], "required": False}
    if structural and addressable:
        return {"kind": "mixed", "structural": structural,
                "addressable": addressable, "unknown": [], "required": True}
    return {"kind": "addressable", "structural": 0, "addressable": addressable,
            "unknown": [], "required": True}


# ════════════════════════════════════════════════════════════
#  参数绑定（占位符 → 用例输入）
# ════════════════════════════════════════════════════════════


def _stable_index(name: str) -> int:
    return int(hashlib.sha1(str(name).encode("utf-8")).hexdigest()[:4], 16) % 1000


def bind_value(value: Any, case: EquivalenceCase) -> Any:
    """占位符 → 用例输入/绑定表（``${name}`` → ``case.bindings`` → ``case.input``）

    形态占位符（``${path}`` 等）无对应输入时按 `cases.synthesize_value()` 合成
    **确定性合成值**（不引入真实数据）。具名占位符无对应输入 ⇒ 原样保留，
    由 `bind_params()` 判定为"未绑定输入"。
    """
    if not isinstance(value, str) or not is_placeholder(value):
        return value
    name = value[2:-1] if value.startswith("${") else value
    if name in (case.bindings or {}):
        return case.bindings[name]
    if name in (case.input or {}):
        return case.input[name]
    if shape_placeholder(value) is not None or name in (
            "path", "timestamp", "uuid", "hex_id", "url", "number", "email", "text"):
        return synthesize_value(value, key=name, index=_stable_index(name),
                                root=case.sandbox_root)
    return value


def bind_params(params: Dict[str, Any],
                case: EquivalenceCase) -> Tuple[Dict[str, Any], List[str]]:
    """一步参数 → 绑定后参数 + **未绑定**的具名占位符清单"""
    bound: Dict[str, Any] = {}
    unbound: List[str] = []
    for key, value in (params or {}).items():
        resolved = bind_value(value, case)
        if isinstance(resolved, str) and is_placeholder(resolved):
            unbound.append(str(resolved))
        bound[str(key)] = resolved
    return bound, unbound


def bind_program(steps: Sequence[ProgramStep],
                 case: EquivalenceCase) -> Tuple[List[ProgramStep], List[str]]:
    """整段程序绑定 → ``(绑定后程序, 未绑定占位符清单)``"""
    out: List[ProgramStep] = []
    unbound: List[str] = []
    for step in steps or []:
        params, missing = bind_params(step.params, case)
        unbound.extend(missing)
        out.append(ProgramStep(label=step.label, params=params,
                               capability_id=step.capability_id,
                               condition=step.condition))
    return out, sorted(set(unbound))


def required_input_keys(steps: Sequence[ProgramStep]) -> List[str]:
    """程序所需的**具名输入键**（占位符名字去重；供用例完备性预检）"""
    keys: List[str] = []
    for step in steps or []:
        for value in (step.params or {}).values():
            if isinstance(value, str) and is_placeholder(value) \
                    and shape_placeholder(value) is None:
                keys.append(value[2:-1])
    return sorted(set(keys))


def missing_input_keys(steps: Sequence[ProgramStep],
                       case: EquivalenceCase) -> List[str]:
    """用例未提供的具名输入键（**运行前**即可判定，不进沙箱）"""
    provided = set(case.input or {}) | set(case.bindings or {})
    return [k for k in required_input_keys(steps) if k not in provided]


#: 补齐来源标签
FILL_FROM_POSITION = "upstream_position"    # 与用例上游程序**同位次**的同名步骤
FILL_FROM_LABEL = "upstream_label"          # 同标签的首个上游步骤
FILL_FROM_INPUT = "case_input"              # 用例 input/bindings


def restore_param_key(key: Any) -> str:
    """槽名 → **原始参数键名**（S3-01 骨架的键名还原）

    S3-01 的 `service.mine()` 用 ``pstep.params[slot.name] = slot.placeholder``，
    而 ``slot.name`` 在同名键多次出现时带位次后缀（``cmd_2``）；直接执行会得到
    ``{cmd_2: "${cmd_2}"}`` —— 工具契约认的是 ``cmd``。故按
    `generalize.infer_parameter_slots` 的命名规则**反向还原**：仅当去掉
    ``_<数字>`` 后的基名属于已知参数名时才还原（避免误改业务上真以 ``_2`` 结尾的键）。
    """
    text = str(key or "")
    if text in KNOWN_PARAM_NAMES:
        return text
    match = _TRAILING_ORDINAL.match(text)
    if match and match.group("base") in KNOWN_PARAM_NAMES:
        return match.group("base")
    return text


def restore_param_keys(params: Dict[str, Any]) -> Dict[str, Any]:
    """一步的参数键整体还原（同键冲突时**保留先出现者**并记 debug，不静默丢参）"""
    out: Dict[str, Any] = {}
    for key, value in (params or {}).items():
        name = restore_param_key(key)
        if name in out:
            logger.debug("参数键还原冲突，保留先出现者: %s ← %s", name, key)
            continue
        out[name] = value
    return out


def complete_params(label: str, index: int, params: Dict[str, Any],
                    steps: Sequence[ProgramStep],
                    case: EquivalenceCase) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """**接口补齐**：按工具契约补齐缺失的必需参数（来源逐条可查）

    为什么需要它：S3-01 的 `CandidatePattern` 骨架**只携带跨轨迹有差异的参数槽**
    （`generalize.infer_parameter_slots` 的判定），跨轨迹恒定的字面量/形态参数
    （如 ``write_file`` 的 ``path=${path}``）不进骨架 ⇒ 骨架单独**不足以**构成
    可执行实现。故执行前按"**位次对齐 → 同标签 → 用例输入**"三级来源补齐
    工具契约要求的必需参数，并把每条补齐记入 `Observation.filled`
    —— 补齐是**显式且可审**的，不是把候选偷偷换成上游程序。

    **按位次取槽**：同名键在多处出现时取值不同（读的 ``path`` vs 写的 ``path_2``），
    故补齐值取该位次的**槽名**（`cases.positional_slot_names`）对应的绑定，
    而非裸键名 —— 否则会把读路径当成写路径（实现期实测缺陷）。

    注意：只补**缺失**的键，绝不覆盖候选自己给出的参数（那是候选行为的一部分）。
    """
    required = TOOL_REQUIRED_PARAMS.get(str(label or ""), ())
    if not required:
        return dict(params), []
    merged = dict(params or {})
    fills: List[Dict[str, Any]] = []
    upstream = list(case.upstream or [])
    slot_names = positional_slot_names(upstream)
    for key in required:
        if key in merged:
            continue
        value: Any = None
        source = ""
        for pos, tag in _source_positions(upstream, index, label):
            if key not in (upstream[pos].params or {}):
                continue
            value = _value_at(case, upstream[pos].params[key], pos, key,
                              slot_names, index)
            source = tag
            break
        if source == "":
            for container in (case.bindings or {}, case.input or {}):
                if key in container:
                    value = container[key]
                    source = FILL_FROM_INPUT
                    break
        if source == "":
            continue
        merged[key] = value
        fills.append({"step_index": index, "label": label, "key": key,
                      "source": source})
    return merged, fills


def _source_positions(upstream: Sequence[ProgramStep], index: int,
                      label: str) -> List[Tuple[int, str]]:
    """补齐取值的候选来源位次（位次对齐优先，其次同标签）"""
    out: List[Tuple[int, str]] = []
    if index < len(upstream) and upstream[index].label == label:
        out.append((index, FILL_FROM_POSITION))
    for pos, step in enumerate(upstream):
        if step.label == label and pos != index:
            out.append((pos, FILL_FROM_LABEL))
    return out


def _value_at(case: EquivalenceCase, recorded: Any, pos: int, key: str,
              slot_names: Sequence[Dict[str, str]],
              candidate_index: int) -> Any:
    """取该位次的**绑定值**（按槽名解析；无绑定时回退字面量/合成值）

    ``candidate_index``：补齐目标在**候选程序**中的位次 —— 当候选与用例上游程序
    位次不一致（例如骨架少了一步）时，按同标签位次取槽仍能命中。
    """
    name = (slot_names[pos].get(str(key)) if pos < len(slot_names) else None) or key
    containers = (case.bindings or {}, case.input or {})
    for container in containers:
        if name in container:
            return container[name]
    if not is_placeholder(recorded):
        # 该位次记的是**字面量**（如写死的报告路径）：直接用，绝不回退到裸键名
        # —— 裸键名往往属于**别的位置**（读的 path），回退即张冠李戴。
        return recorded
    for container in containers:
        if str(key) in container:
            return container[str(key)]
    return synthesize_value(recorded, key=name,
                            index=_stable_index(f"{name}|{candidate_index}"),
                            root=case.sandbox_root)


# ════════════════════════════════════════════════════════════
#  虚拟环境
# ════════════════════════════════════════════════════════════


def _norm_path(path: Any) -> str:
    return str(path or "").replace("\\", "/")


class ReplayEnv:
    """内存虚拟执行环境（**无真实 I/O**；副作用只记录）

    工具语义全部是对 ``files`` 的纯函数 + 副作用记账，故同输入必同结果。
    """

    def __init__(self, *, fixtures: Optional[Dict[str, str]] = None,
                 root: str = DEFAULT_SANDBOX_ROOT,
                 quota: Optional[SandboxQuota] = None,
                 tools: Optional[Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]]] = None,
                 ) -> None:
        self.root = _norm_path(root).rstrip("/") or DEFAULT_SANDBOX_ROOT
        self.quota = quota or SandboxQuota()
        self.files: Dict[str, str] = {_norm_path(k): str(v)
                                      for k, v in (fixtures or {}).items()}
        self.side_effects: Dict[str, List[str]] = {k: [] for k in SIDE_EFFECT_KINDS}
        self.records: List[Dict[str, Any]] = []
        self.sim_ms: float = 0.0
        self.unbound: List[str] = []
        self.tools: Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]] = {
            "read_file": _t_read_file, "list_dir": _t_list_dir,
            "stat": _t_stat, "grep": _t_grep, "write_file": _t_write_file,
            "append_file": _t_append_file, "delete_file": _t_delete_file,
            "apply_patch": _t_apply_patch, "replace_in_file": _t_apply_patch,
            "create_dir": _t_create_dir,
        }
        if tools:
            self.tools.update(tools)

    # ── 守卫与写入契约 ──────────────────────────────────────

    def resolve(self, path: Any) -> str:
        """路径 → 沙箱内绝对键（**出界即抛** `SandboxEscapeError`）"""
        candidate = _norm_path(path)
        if not candidate:
            raise SandboxEscapeError("空路径不可访问")
        root = self.root
        inside = (candidate == root or candidate.startswith(root + "/"))
        if not inside:
            raise SandboxEscapeError(
                f"访问沙箱根之外的路径: {candidate!r}（沙箱根 {root!r}）")
        return candidate

    def commit(self) -> None:
        """**恒抛**：回放沙箱只记录副作用，绝不落真实环境（§4.5 record-and-replay）"""
        raise SandboxError(
            "回放沙箱不提供真实落盘通道：副作用只记录不双写"
            "（record-and-replay；真实落盘由 S3-03 shadow 灰度承接）")

    def _charge_quota(self, kind: str, amount: float = 1.0) -> None:
        """配额计数（超限抛 `SandboxQuotaExceeded`）"""
        q = self.quota
        if kind == "files_written" and len(self.side_effects["files_written"]) >= q.max_files_written:
            raise SandboxQuotaExceeded(
                ERR_QUOTA_FILES,
                f"写文件数超限 {q.max_files_written}")
        if kind == "files_deleted" and len(self.side_effects["files_deleted"]) >= q.max_deleted:
            raise SandboxQuotaExceeded(ERR_QUOTA_FILES,
                                       f"删文件数超限 {q.max_deleted}")
        if kind == "external_calls" and len(self.side_effects["external_calls"]) >= q.max_external_calls:
            raise SandboxQuotaExceeded(
                ERR_QUOTA_EXTERNAL, f"外部调用数超限 {q.max_external_calls}")
        if kind == "bytes" and int(amount) > q.max_bytes:
            raise SandboxQuotaExceeded(ERR_QUOTA_BYTES,
                                       f"单次写入 {int(amount)}B 超限 {q.max_bytes}B")

    # ── 文件系统（虚拟） ────────────────────────────────────

    def read_text(self, path: Any) -> Optional[str]:
        return self.files.get(self.resolve(path))

    def write_text(self, path: Any, content: str) -> str:
        key = self.resolve(path)
        payload = str(content if content is not None else "")
        self._charge_quota("bytes", len(payload.encode("utf-8")))
        self._charge_quota("files_written")
        self.files[key] = payload
        self._record("files_written", key)
        return key

    def delete(self, path: Any) -> bool:
        key = self.resolve(path)
        self._charge_quota("files_deleted")
        existed = key in self.files
        self.files.pop(key, None)
        self._record("files_deleted", key)
        return existed

    def list_paths(self, prefix: Any = "") -> List[str]:
        base = self.resolve(prefix) if prefix else self.root
        wanted = base + "/"
        return sorted(p for p in self.files
                      if p == base or p.startswith(wanted))

    def _record(self, kind: str, target: str) -> None:
        if target not in self.side_effects[kind]:
            self.side_effects[kind].append(target)
        self.records.append({"kind": kind, "target": target, "seq": len(self.records) + 1})

    # ── 工具调用 ────────────────────────────────────────────

    def latency_of(self, label: str) -> float:
        return float(TOOL_LATENCY_MS.get(str(label or ""), DEFAULT_LATENCY_MS))

    def call(self, label: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """调用一个（虚拟）工具：记账 → 计时 → 分派 → 结果"""
        name = str(label or "").strip()
        self._check_time_budget()
        if name in EXTERNAL_LABELS:
            if self.quota.deny_external:
                self._record("external_calls", name)
                self.sim_ms += self.latency_of(name)
                return {"ok": False, "error_code": ERR_EXTERNAL_DENIED,
                        "error": f"外部调用被沙箱拒绝: {name}"}
            self._charge_quota("external_calls")
            self._record("external_calls", name)
            self.sim_ms += self.latency_of(name)
            return {"ok": True, "simulated": name, "cmd": str(params.get("cmd")
                                                              or params.get("command") or "")}
        handler = self.tools.get(name)
        self.sim_ms += self.latency_of(name)
        if handler is None:
            return {"ok": True, "simulated": name}
        return handler(self, params)

    def _check_time_budget(self) -> None:
        if self.sim_ms >= self.quota.sim_budget_ms:
            raise SandboxQuotaExceeded(
                ERR_QUOTA_TIME, f"模型时钟 {self.sim_ms:.1f}ms 超预算 "
                                f"{self.quota.sim_budget_ms:.1f}ms")

    # ── 快照 ────────────────────────────────────────────────

    #: 环境快照的**形态视图**：路径按形态归一（``C:/a/b.txt`` → ``${path}``），
    #: 使"同一形态的不同具体路径"不产生伪差异 —— 与判定集其余部分的形态口径一致
    #: （S2 载荷纪律：只记标识/形态，不记原文）。
    def digest_shape(self) -> List[str]:
        return sorted(f"{normalize_param_value(p)}={_sha8(v)}"
                      for p, v in self.files.items())

    def snapshot(self) -> Dict[str, Any]:
        """环境快照（供报告/取证；内容只留指纹，不留原文）"""
        return {
            "root": self.root,
            "files": sorted(self.files),
            "digests": sorted(f"{normalize_param_value(p)}={_sha8(v)}"
                              for p, v in self.files.items()),
            "side_effects": {k: sorted(v) for k, v in self.side_effects.items()},
            "sim_ms": round(self.sim_ms, 3),
            "records": len(self.records),
        }


def _sha8(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8")).hexdigest()[:8]


def _path_param(params: Dict[str, Any]) -> str:
    for key in ("path", "file", "file_path", "target", "dest", "directory"):
        if key in (params or {}):
            return str(params[key])
    return ""


# ── 默认虚拟工具（对 env 的纯函数） ─────────────────────────


def _t_read_file(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    content = env.read_text(path)
    if content is None:
        return {"ok": False, "error_code": "ENOENT", "error": "文件不存在",
                "path": env.resolve(path)}
    return {"ok": True, "path": env.resolve(path),
            "bytes": len(content.encode("utf-8")),
            "lines": content.count("\n") + 1}


def _t_stat(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    content = env.read_text(path)
    return {"ok": content is not None, "path": env.resolve(path),
            "exists": content is not None,
            "bytes": len(content.encode("utf-8")) if content is not None else 0}


def _t_list_dir(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params) or env.root
    entries = [p[len(env.resolve(path)) + 1:] for p in env.list_paths(path)]
    return {"ok": True, "path": env.resolve(path), "entries": sorted(entries)}


def _t_grep(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    needle = str(params.get("pattern") or params.get("query") or "")
    scope = str(params.get("path") or env.root)
    matches = sum(1 for p in env.list_paths(scope) if needle and needle in env.files[p])
    return {"ok": True, "path": env.resolve(scope), "matches": matches,
            "pattern": needle}


def _t_write_file(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    content = params.get("content", params.get("text", ""))
    key = env.write_text(path, str(content))
    return {"ok": True, "path": key, "bytes": len(str(content).encode("utf-8"))}


def _t_append_file(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    existing = env.read_text(path) or ""
    content = str(params.get("content", params.get("text", "")))
    key = env.write_text(path, existing + content)
    return {"ok": True, "path": key, "bytes": len((existing + content).encode("utf-8"))}


def _t_delete_file(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    existed = env.delete(path)
    return {"ok": True, "path": env.resolve(path), "deleted": existed}


def _t_apply_patch(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = _path_param(params)
    patch = str(params.get("patch") or params.get("content") or "")
    existing = env.read_text(path)
    if existing is None:
        return {"ok": False, "error_code": "ENOENT", "error": "补丁目标不存在",
                "path": env.resolve(path)}
    updated = patch if patch.startswith("+") else existing + patch
    key = env.write_text(path, updated)
    return {"ok": True, "path": key, "applied": True,
            "bytes": len(updated.encode("utf-8"))}


def _t_create_dir(env: ReplayEnv, params: Dict[str, Any]) -> Dict[str, Any]:
    path = env.resolve(_path_param(params) or env.root)
    return {"ok": True, "path": path, "created": True}


# ════════════════════════════════════════════════════════════
#  观测
# ════════════════════════════════════════════════════════════


@dataclass
class Observation:
    """一次回放的观测结果（双跑的两臂各一份）"""

    implementation: str = ""
    status: str = OBS_SUCCESS
    steps: List[str] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    outputs: List[Dict[str, Any]] = field(default_factory=list)
    side_effects: Dict[str, List[str]] = field(default_factory=dict)
    duration_ms: float = 0.0
    #: **真实墙钟**（ms；仅在 `measure_wall=True` 时测量，见 `ReplaySandbox`）——
    #: 与 `duration_ms`（标称模型时钟量）**不同量纲、各自标注**：S3-03 的内化条件⑤
    #: 必须用墙钟口径，故本字段默认 0.0（未测量即未测量，绝不用模型时钟冒充）
    wall_ms: float = 0.0
    error_code: str = ""
    unbound: List[str] = field(default_factory=list)
    filled: List[Dict[str, Any]] = field(default_factory=list)
    env_snapshot: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == OBS_SUCCESS

    @property
    def last_output(self) -> Dict[str, Any]:
        return dict(self.outputs[-1]) if self.outputs else {}

    def output(self) -> Dict[str, Any]:
        """**被比对的结构化输出**（结构层看字段/类型/基数，值只进软性层）"""
        return {
            "status": self.status,
            "error_code": self.error_code,
            "steps": list(self.steps),
            "step_count": len(self.steps),
            "last_result": self.last_output,
            "env_files": list(self.env_snapshot.get("files") or []),
            "env_digest": list(self.env_snapshot.get("digests") or []),
        }

    def side_effect_set(self) -> Dict[str, List[str]]:
        """副作用**形态归一**后的集合（跨具体路径可比）"""
        out: Dict[str, List[str]] = {}
        for kind in SIDE_EFFECT_KINDS:
            values = [str(normalize_param_value(v))
                      for v in (self.side_effects.get(kind) or [])]
            out[kind] = sorted(set(values))
        return out

    def fingerprint(self) -> str:
        """观测指纹（确定性比对/日志用）"""
        return hashlib.sha1(self.canonical_text().encode("utf-8")).hexdigest()[:16]

    def canonical_text(self) -> str:
        """规范化文本（供 judge 相似度与日志；路径已形态归一）"""
        return canonical_json({
            "status": self.status,
            "error_code": self.error_code,
            "steps": list(self.steps),
            "skipped": list(self.skipped),
            "outputs": [normalize_param_value(o) for o in self.outputs],
            "side_effects": self.side_effect_set(),
            "env_digest": list(self.env_snapshot.get("digests") or []),
        })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "implementation": self.implementation,
            "status": self.status,
            "steps": list(self.steps),
            "skipped": list(self.skipped),
            "side_effects": self.side_effect_set(),
            "duration_ms": round(self.duration_ms, 3),
            "wall_ms": round(self.wall_ms, 3),
            "error_code": self.error_code,
            "unbound": list(self.unbound),
            "filled_params": list(self.filled),
            "fingerprint": self.fingerprint(),
            "env_files": len(self.env_snapshot.get("files") or []),
        }


def schema_of(value: Any) -> Any:
    """值 → **结构签名**（键名 + 类型 + 列表基数；不含具体标量值）

    列表基数进结构（"三步骨架退化成两步"必须被硬性层拦下），标量值不进结构
    （"值不同"由软性 judge 层判）—— 这是三层比对的分工，不是取巧。
    """
    if isinstance(value, dict):
        return {str(k): schema_of(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return f"list[{len(value)}]<{schema_of(value[0]) if value else 'empty'}>"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return "str"


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, (list, tuple)):
        return "list"
    if value is None:
        return "null"
    return type(value).__name__


def run_program(
    steps: Sequence[ProgramStep],
    case: EquivalenceCase,
    *,
    implementation: str = "",
    env: Optional[ReplayEnv] = None,
    quota: Optional[SandboxQuota] = None,
    tools: Optional[Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]]] = None,
) -> Observation:
    """在沙箱里执行一段（已绑定/待绑定的）步骤程序 → `Observation`

    执行语义（确定性、无真实 I/O）：

    - 每步参数经 `bind_params()` 绑定；**未绑定输入** ⇒ 立即以
      `unbound_input` 收尾（不静默跳过、不臆造值）；
    - 条件步：`condition` 不成立即**跳过**（记入 `skipped`，与"没执行"可区分）；
    - 任一步返回 ``ok=False`` ⇒ 以 `error` 收尾并记录 `error_code`；
    - 出界路径 / 配额超限 ⇒ 对应终止态，绝不回落成"成功"。
    """
    if env is None:
        env = ReplayEnv(fixtures=case.fixtures, root=case.sandbox_root,
                        quota=quota, tools=tools)
    obs = Observation(implementation=str(implementation or ""))
    program = list(steps or [])
    for index, step in enumerate(program):
        if index + 1 > env.quota.max_steps:
            obs.status = OBS_QUOTA_EXCEEDED
            obs.error_code = ERR_QUOTA_STEPS
            break
        params, fills = complete_params(step.label, index, step.params, program, case)
        obs.filled.extend(fills)
        params, unbound = bind_params(params, case)
        if unbound:
            obs.status = OBS_UNBOUND_INPUT
            obs.error_code = ERR_UNBOUND_INPUT
            obs.unbound = sorted(set(obs.unbound) | set(unbound))
            break
        if step.condition:
            ctx = ConditionContext(labels=tuple(str(s.label) for s in steps),
                                   params=params, step_count=len(steps))
            if not condition_matches(step.condition, ctx):
                obs.skipped.append(step.label)
                continue
        try:
            result = env.call(step.label, params)
        except SandboxEscapeError as e:
            obs.status = OBS_ESCAPE_BLOCKED
            obs.error_code = ERR_ESCAPE
            obs.outputs.append({"ok": False, "error_code": ERR_ESCAPE,
                                "error": str(e)})
            break
        except SandboxQuotaExceeded as e:
            obs.status = OBS_QUOTA_EXCEEDED
            obs.error_code = e.code
            obs.outputs.append({"ok": False, "error_code": e.code,
                                "error": e.detail})
            break
        obs.outputs.append(result)
        obs.steps.append(step.label)
        if not result.get("ok", False):
            obs.status = OBS_ERROR
            obs.error_code = str(result.get("error_code") or "E_UPSTREAM")
            break
    obs.side_effects = {k: list(v) for k, v in env.side_effects.items()}
    obs.unbound = sorted(set(obs.unbound) | set(env.unbound))
    obs.duration_ms = round(env.sim_ms, 3)
    obs.env_snapshot = env.snapshot()
    return obs


# ════════════════════════════════════════════════════════════
#  被测实现（双跑的两臂）
# ════════════════════════════════════════════════════════════


class Implementation:
    """被测实现基类（``steps_for()`` 给出**绑定前**的步骤程序）"""

    name = "implementation"

    def steps_for(self, case: EquivalenceCase) -> List[ProgramStep]:
        raise NotImplementedError

    def missing_inputs(self, case: EquivalenceCase) -> List[str]:
        return missing_input_keys(self.steps_for(case), case)

    def run(self, case: EquivalenceCase, *,
            quota: Optional[SandboxQuota] = None,
            tools: Optional[Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]]] = None,
            ) -> Observation:
        return run_program(self.steps_for(case), case,
                           implementation=self.name, quota=quota, tools=tools)


class ProgramImplementation(Implementation):
    """固定程序（**上游/现有实现**的录制程序）"""

    def __init__(self, steps: Sequence[ProgramStep], *, name: str = "upstream") -> None:
        self.steps = list(steps)
        self.name = name

    def steps_for(self, case: EquivalenceCase) -> List[ProgramStep]:
        return [ProgramStep(label=s.label, params=dict(s.params),
                            capability_id=s.capability_id, condition=s.condition)
                for s in self.steps]


class TemplateImplementation(Implementation):
    """模板程序（Seed Pack 的**候选原生实现**骨架；占位符按用例输入绑定）"""

    def __init__(self, steps: Sequence[ProgramStep], *, name: str = "seed_native") -> None:
        self.steps = list(steps)
        self.name = name

    def steps_for(self, case: EquivalenceCase) -> List[ProgramStep]:
        return [ProgramStep(label=s.label, params=dict(s.params),
                            capability_id=s.capability_id, condition=s.condition)
                for s in self.steps]


class PatternImplementation(Implementation):
    """候选原生实现：由 S3-01 的 `CandidatePattern` 骨架编译而来

    映射：`PatternStep` → `ProgramStep`（``params`` 为参数槽占位符）。

    ⚠ **`condition` 的语义接缝**（实现期实测）：S3-01 的 `PatternStep.condition`
    是**描述性**分支标注（"该位次的历史分支条件"，如 ``步骤 `write_file` 缺失``），
    渲染进 SKILL.md 供人工核对；它**不是**可执行守卫 —— 若把 ``步骤 X 缺失`` 当作
    执行条件，X 恰恰在骨架里 ⇒ 条件恒假 ⇒ 该步被永久跳过，候选行为与上游系统性
    不一致。故本类默认 ``use_conditions=False``（**不**把描述性条件当守卫）；
    需要条件执行的候选实现应在编译期显式给出 `ProgramStep.condition`
    （Seed Pack 的 `native_template` 即是该通道的示例）。
    """

    def __init__(self, pattern: CandidatePattern, *, name: str = "candidate",
                 use_conditions: bool = False,
                 drop_steps: Sequence[int] = (), add_steps: Sequence[ProgramStep] = ()) -> None:
        self.pattern = pattern
        self.name = name
        self.use_conditions = bool(use_conditions)
        self.drop_steps = tuple(int(i) for i in (drop_steps or ()))
        self.add_steps = list(add_steps or ())

    def steps_for(self, case: EquivalenceCase) -> List[ProgramStep]:
        steps: List[ProgramStep] = []
        for index, step in enumerate(getattr(self.pattern, "steps", []) or []):
            if index in self.drop_steps:
                continue
            steps.append(ProgramStep(
                label=str(step.label),
                params=restore_param_keys(dict(step.params or {})),
                capability_id=str(step.capability_id or ""),
                condition=(str(step.condition or "") if self.use_conditions else "")))
        steps.extend(self.add_steps)
        return steps


class CallableImplementation(Implementation):
    """函数式实现（测试/负样本注入用）：``fn(case, env) -> dict|Observation``"""

    def __init__(self, fn: Callable[[EquivalenceCase, ReplayEnv], Any],
                 *, name: str = "callable") -> None:
        self.fn = fn
        self.name = name

    def steps_for(self, case: EquivalenceCase) -> List[ProgramStep]:
        return []

    def run(self, case: EquivalenceCase, *, quota=None, tools=None) -> Observation:
        env = ReplayEnv(fixtures=case.fixtures, root=case.sandbox_root,
                        quota=quota, tools=tools)
        result = self.fn(case, env)
        if isinstance(result, Observation):
            return result
        obs = Observation(implementation=self.name)
        obs.outputs.append(dict(result or {}))
        obs.steps = [str((result or {}).get("label") or self.name)]
        obs.side_effects = {k: list(v) for k, v in env.side_effects.items()}
        obs.duration_ms = round(env.sim_ms, 3)
        obs.env_snapshot = env.snapshot()
        obs.status = OBS_SUCCESS if (result or {}).get("ok", True) else OBS_ERROR
        return obs


def as_implementation(target: Any, *, name: str = "") -> Implementation:
    """把"程序列表 / 候选模式 / 实现对象"统一为 `Implementation`

    已是 `Implementation` 的对象**保留其原有 name**（避免把调用方自命名的
    "上游/候选"标签覆盖掉，使双跑报告的两臂可辨识）。
    """
    if isinstance(target, Implementation):
        return target
    if isinstance(target, CandidatePattern):
        return PatternImplementation(target, name=name or "candidate")
    if isinstance(target, (list, tuple)):
        return TemplateImplementation(list(target), name=name or "candidate")
    raise SandboxError(f"不支持的被测实现类型: {type(target).__name__}")


# ════════════════════════════════════════════════════════════
#  三层比对（§4.5）
# ════════════════════════════════════════════════════════════


@dataclass
class LayerResult:
    """单层比对结果（**任一层失败都有独立、可读的理由**）"""

    layer: str
    kind: str
    passed: bool
    score: float = 1.0
    reasons: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"layer": self.layer, "kind": self.kind, "passed": self.passed,
                "score": round(self.score, 4), "reasons": list(self.reasons),
                "detail": dict(self.detail)}


@dataclass
class DiffResult:
    """双跑 diff 汇总（三层）"""

    layers: List[LayerResult] = field(default_factory=list)
    upstream_status: str = ""
    candidate_status: str = ""

    @property
    def passed(self) -> bool:
        return all(layer.passed for layer in self.layers)

    def layer(self, name: str) -> Optional[LayerResult]:
        for item in self.layers:
            if item.layer == name:
                return item
        return None

    @property
    def failed_layers(self) -> List[str]:
        return [layer.layer for layer in self.layers if not layer.passed]

    def reasons(self) -> List[str]:
        out: List[str] = []
        for layer in self.layers:
            if not layer.passed:
                out.extend(f"[{layer.layer}] {r}" for r in layer.reasons)
        return out

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "failed_layers": self.failed_layers,
                "upstream_status": self.upstream_status,
                "candidate_status": self.candidate_status,
                "layers": [layer.to_dict() for layer in self.layers]}


def diff_structure(upstream: Observation, candidate: Observation,
                   case: EquivalenceCase) -> LayerResult:
    """**层 1（硬性）**：输出结构 schema —— 上游↔候选 + 用例契约

    三条硬性检查：
    ① 上游/候选结构签名一致（键名/类型/列表基数）；
    ② 候选最终结果满足用例声明的 ``expected_output_schema``（声明键必须存在且类型相符）；
    ③ 用例声明了具体 ``expected_output`` 时，按**值**比对（确定性目标的强断言）。
    """
    reasons: List[str] = []
    upstream_schema = schema_of(upstream.output())
    candidate_schema = schema_of(candidate.output())
    if upstream_schema != candidate_schema:
        reasons.append(
            f"输出结构不一致：上游 {canonical_json(upstream_schema)} "
            f"≠ 候选 {canonical_json(candidate_schema)}")
    declared_ok = True
    if case.expected_output_schema:
        last = candidate.output().get("last_result") or {}
        for key, want in case.expected_output_schema.items():
            if key not in last:
                declared_ok = False
                reasons.append(f"候选结果缺声明字段 {key!r}（用例 expected_output_schema）")
                continue
            got = _type_name(last[key])
            if str(want) not in (got, "any"):
                declared_ok = False
                reasons.append(
                    f"字段 {key!r} 类型 {got} ≠ 声明 {want}")
    value_ok = True
    if case.expected_output:
        last = candidate.output().get("last_result") or {}
        for key, want in case.expected_output.items():
            actual = last.get(key)
            if actual != want:
                value_ok = False
                reasons.append(f"字段 {key!r} 值 {actual!r} ≠ 期望 {want!r}")
    passed = (upstream_schema == candidate_schema) and declared_ok and value_ok
    return LayerResult(
        layer=LAYER_STRUCTURE, kind="hard", passed=passed,
        score=1.0 if passed else 0.0, reasons=reasons,
        detail={"upstream_schema": upstream_schema,
                "candidate_schema": candidate_schema,
                "declared_schema": dict(case.expected_output_schema),
                "declared_schema_ok": declared_ok,
                "expected_value_ok": value_ok})


def _sets_match(expected: Sequence[str], actual: Sequence[str]) -> bool:
    """副作用集合匹配（**具体值优先，形态容忍**）

    规则：``${path}`` 这类**形态条目**可匹配任意同形态的具体路径（Authors 可只写形态）；
    其余条目按**具体值**严格比对。二者混用时逐条一对一匹配。
    """
    remaining = [str(a) for a in actual]
    for want in expected:
        text = str(want)
        if text in remaining:
            remaining.remove(text)
            continue
        if is_placeholder(text):
            hit = next((a for a in remaining
                        if str(normalize_param_value(a)) == text), None)
            if hit is None:
                return False
            remaining.remove(hit)
            continue
        return False
    return not remaining


def diff_side_effects(upstream: Observation, candidate: Observation,
                      case: EquivalenceCase) -> LayerResult:
    """**层 2（硬性）**：副作用 —— 双跑**具体值**一致 + **内容指纹**一致 + 用例契约

    副作用的完整语义是 **(目标, 内容)**。三条硬性检查：

    ① 两臂的副作用**具体目标**逐一相同（回放中两臂共享同一输入，故忠实实现必须
       写到同一处；"写到别的文件"不是等价）；
    ② 两臂的环境文件**内容指纹**相同（内容不同即硬性拒绝）；
    ③ 用例契约：``expected_side_effects`` 逐条匹配候选（**具体值优先、形态容忍** ——
       写 ``${path}`` 表示"同形态即可"，写具体路径则要求精确命中）；
       ``side_effects_source="none"``（台账未记录副作用）时不主张"无副作用"，
       契约检查跳过，但 ①② 始终执行。
    """
    reasons: List[str] = []
    up = {k: sorted(str(v) for v in (upstream.side_effects.get(k) or []))
          for k in SIDE_EFFECT_KINDS}
    cand = {k: sorted(str(v) for v in (candidate.side_effects.get(k) or []))
            for k in SIDE_EFFECT_KINDS}
    targets_ok = all(up[k] == cand[k] for k in SIDE_EFFECT_KINDS)
    for kind in SIDE_EFFECT_KINDS:
        if up[kind] != cand[kind]:
            reasons.append(
                f"{kind} 目标不一致：上游 {up[kind]} ≠ 候选 {cand[kind]}")

    up_digests = list(upstream.env_snapshot.get("digests") or [])
    cand_digests = list(candidate.env_snapshot.get("digests") or [])
    content_ok = up_digests == cand_digests
    if not content_ok:
        only_up = sorted(set(up_digests) - set(cand_digests))
        only_cand = sorted(set(cand_digests) - set(up_digests))
        reasons.append(
            f"写入内容指纹不一致：仅上游 {len(only_up)} 项 / 仅候选 "
            f"{len(only_cand)} 项（示例：{(only_up or only_cand)[:2]}）")

    contract_ok = True
    source = str(getattr(case, "side_effects_source", "authored") or "authored")
    expected = {k: list(case.expected_side_effects.get(k) or [])
                for k in SIDE_EFFECT_KINDS}
    if source != "none":
        for kind in SIDE_EFFECT_KINDS:
            if not _sets_match(expected[kind], cand[kind]):
                contract_ok = False
                reasons.append(
                    f"{kind} 与用例期望不一致：期望 {expected[kind]} ≠ 候选 {cand[kind]}")
    passed = targets_ok and content_ok and contract_ok
    return LayerResult(
        layer=LAYER_SIDE_EFFECTS, kind="hard", passed=passed,
        score=1.0 if passed else 0.0, reasons=reasons,
        detail={"upstream": up, "candidate": cand, "expected": expected,
                "expected_source": source, "contract_enforced": source != "none",
                "targets_ok": targets_ok, "content_ok": content_ok,
                "upstream_shape": upstream.side_effect_set(),
                "candidate_shape": candidate.side_effect_set(),
                "upstream_digests": up_digests,
                "candidate_digests": cand_digests})


def judge_similarity(reference: str, observed: str) -> float:
    """确定性相似度打分器（**默认 judge**；LLM-judge 由 `judge=` 注入）

    构成：token 集合 Jaccard 0.5 + 序列相似度 0.5，取 4 位小数。
    刻意不用墙钟/随机/模型调用 —— 判定集回放必须是可复现的。
    """
    left = str(reference or "")
    right = str(observed or "")
    if left == right:
        return 1.0
    left_tokens = set(re.findall(r"[0-9A-Za-z_./{}$:=\-]+|[\u4e00-\u9fff]", left))
    right_tokens = set(re.findall(r"[0-9A-Za-z_./{}$:=\-]+|[\u4e00-\u9fff]", right))
    union = left_tokens | right_tokens
    jaccard = (len(left_tokens & right_tokens) / len(union)) if union else 0.0
    ratio = difflib.SequenceMatcher(None, left, right).ratio()
    return round(0.5 * jaccard + 0.5 * ratio, 4)


def diff_judge(upstream: Observation, candidate: Observation, *,
               judge: Optional[Callable[[str, str], float]] = None,
               threshold: float = JUDGE_THRESHOLD,
               manual_flagged: bool = False,
               judge_kind: str = "") -> LayerResult:
    """**层 3（软性）**：judge ≥0.85 语义等价比对 + 人工抽检标记

    ``judge`` 可注入（如 LLM-judge）；缺省用确定性本地打分器。层 3 只对
    **两臂 canonical 文本**打分（路径已形态归一），故不因具体路径差异误判。

    ``judge_kind``：**实际所用 judge 的如实标注**（TASK-S3-03 / M1）。缺省时按
    "是否注入"推断（``injected`` / ``deterministic_local``）；S3-03 灰度期传入
    ``llm_judge`` 或 ``deterministic_local(llm_unavailable)`` 等精确标签，
    使"确定性打分器"与"真实 LLM-judge"在报告里**可区分**。
    """
    reference = upstream.canonical_text()
    observed = candidate.canonical_text()
    scorer = judge or judge_similarity
    kind = str(judge_kind or ("injected" if judge else "deterministic_local"))
    try:
        score = float(scorer(reference, observed))
    except Exception as e:  # noqa: BLE001  judge 异常不得中断回放
        return LayerResult(
            layer=LAYER_JUDGE, kind="soft", passed=False, score=0.0,
            reasons=[f"judge 执行失败: {type(e).__name__}: {e}"],
            detail={"judge_kind": kind})
    passed = score >= float(threshold)
    reasons: List[str] = []
    if not passed:
        reasons.append(f"judge 相似度 {score:.4f} < 门槛 {threshold}")
    if manual_flagged:
        reasons.append("已纳入 10% 人工抽检清单（人工复核未完成前不得视为已验收）")
    return LayerResult(
        layer=LAYER_JUDGE, kind="soft", passed=passed, score=score,
        reasons=reasons,
        detail={"judge_kind": kind,
                "threshold": float(threshold),
                "manual_review_flagged": bool(manual_flagged)})


def three_layer_diff(upstream: Observation, candidate: Observation,
                     case: EquivalenceCase, *,
                     judge: Optional[Callable[[str, str], float]] = None,
                     threshold: float = JUDGE_THRESHOLD,
                     manual_flagged: bool = False,
                     judge_kind: str = "") -> DiffResult:
    """§4.5 三层比对：结构 schema（硬）→ 副作用集合（硬）→ judge（软）"""
    return DiffResult(
        layers=[diff_structure(upstream, candidate, case),
                diff_side_effects(upstream, candidate, case),
                diff_judge(upstream, candidate, judge=judge, threshold=threshold,
                           manual_flagged=manual_flagged, judge_kind=judge_kind)],
        upstream_status=upstream.status, candidate_status=candidate.status)


def manual_sample_ids(case_ids: Iterable[str], *,
                      ratio: float = MANUAL_SAMPLE_RATIO) -> List[str]:
    """**确定性**人工抽检抽样（10%）：按 ``sha1(case_id)`` 排序取前 ratio 比例

    刻意不用随机数：抽检清单必须可复现（"同一判定集、同一抽检集"），
    否则"抽检覆盖率"本身无法被审计。
    """
    ordered = sorted({str(c) for c in case_ids},
                     key=lambda c: (hashlib.sha1(c.encode("utf-8")).hexdigest(), c))
    if not ordered:
        return []
    size = int(len(ordered) * max(0.0, min(1.0, float(ratio))) + 0.9999)
    size = max(1, min(len(ordered), size))
    return sorted(ordered[:size])


# ════════════════════════════════════════════════════════════
#  回放结果
# ════════════════════════════════════════════════════════════


@dataclass
class CaseReplay:
    """一组用例的双跑回放结果"""

    case_id: str
    capability_id: str
    upstream: Observation
    candidate: Observation
    diff: DiffResult
    expectation_ok: bool = True
    expectation_reasons: List[str] = field(default_factory=list)
    input_fingerprint: str = ""

    @property
    def passed(self) -> bool:
        return bool(self.diff.passed and self.expectation_ok)

    def failures(self) -> List[str]:
        return list(self.expectation_reasons) + self.diff.reasons()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "capability_id": self.capability_id,
            "passed": self.passed,
            "expectation_ok": self.expectation_ok,
            "failures": self.failures(),
            "upstream": self.upstream.to_dict(),
            "candidate": self.candidate.to_dict(),
            "diff": self.diff.to_dict(),
        }


def _p99(values: Sequence[float]) -> float:
    """p99（样本不足 100 时取最大值 —— 与 S5 评测口径一致：小样本不虚报分位）"""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return 0.0
    if len(ordered) < 100:
        return round(ordered[-1], 3)
    idx = min(len(ordered) - 1, int(0.99 * len(ordered)))
    return round(ordered[idx], 3)


@dataclass
class ReplayReport:
    """一次批量回放的汇总（供验收门与失败清单消费）"""

    capability_id: str
    replays: List[CaseReplay] = field(default_factory=list)
    manual_sample: List[str] = field(default_factory=list)
    layer_failures: Dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.replays)

    @property
    def passed(self) -> int:
        return sum(1 for r in self.replays if r.passed)

    @property
    def failed_ids(self) -> List[str]:
        return sorted(r.case_id for r in self.replays if not r.passed)

    @property
    def pass_rate(self) -> float:
        return round(self.passed / self.total, 4) if self.total else 0.0

    def p99_candidate_ms(self) -> float:
        return _p99([r.candidate.duration_ms for r in self.replays])

    def p99_upstream_ms(self) -> float:
        return _p99([r.upstream.duration_ms for r in self.replays])

    # ── 真实墙钟口径（TASK-S3-03 / M2；仅在 measure_wall=True 时有值） ──

    @property
    def wall_measured(self) -> bool:
        return any(r.candidate.wall_ms or r.upstream.wall_ms for r in self.replays)

    def p99_wall_candidate_ms(self) -> float:
        return _p99([r.candidate.wall_ms for r in self.replays])

    def p99_wall_upstream_ms(self) -> float:
        return _p99([r.upstream.wall_ms for r in self.replays])

    def failure_list(self) -> List[Dict[str, Any]]:
        """失败清单（哪些用例失败 / 哪层 diff 未过 —— 供 S3-03 灰度期观察）"""
        out: List[Dict[str, Any]] = []
        for replay in self.replays:
            if replay.passed:
                continue
            out.append({
                "case_id": replay.case_id,
                "failed_layers": replay.diff.failed_layers,
                "expectation_ok": replay.expectation_ok,
                "upstream_status": replay.upstream.status,
                "candidate_status": replay.candidate.status,
                "reasons": replay.failures(),
            })
        return out

    def to_dict(self, *, include_replays: bool = False) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "capability_id": self.capability_id,
            "total": self.total,
            "passed": self.passed,
            "failed": self.total - self.passed,
            "pass_rate": self.pass_rate,
            "p99_candidate_ms": self.p99_candidate_ms(),
            "p99_upstream_ms": self.p99_upstream_ms(),
            "p99_wall_candidate_ms": self.p99_wall_candidate_ms(),
            "p99_wall_upstream_ms": self.p99_wall_upstream_ms(),
            "wall_measured": self.wall_measured,
            "clock": ("模型时钟(duration_ms=标称延迟累加)；真实墙钟见 "
                      "p99_wall_*（measure_wall=True 时采集）"),
            "manual_sample": list(self.manual_sample),
            "layer_failures": dict(self.layer_failures),
            "failure_list": self.failure_list(),
        }
        if include_replays:
            payload["replays"] = [r.to_dict() for r in self.replays]
        return payload


# ════════════════════════════════════════════════════════════
#  沙箱门面
# ════════════════════════════════════════════════════════════


class ReplaySandbox:
    """回放沙箱门面：双跑 + 三层 diff + 确定性自检"""

    def __init__(self, *, quota: Optional[SandboxQuota] = None,
                 tools: Optional[Dict[str, Callable[[Any, Dict[str, Any]], Dict[str, Any]]]] = None,
                 judge: Optional[Callable[[str, str], float]] = None,
                 judge_threshold: float = JUDGE_THRESHOLD,
                 manual_ratio: float = MANUAL_SAMPLE_RATIO,
                 measure_wall: bool = False,
                 judge_kind: str = "") -> None:
        self.quota = quota or SandboxQuota.from_env()
        self.tools = dict(tools or {})
        self.judge = judge
        self.judge_threshold = float(judge_threshold)
        self.manual_ratio = float(manual_ratio)
        #: **真实墙钟**测量开关（TASK-S3-03 / M2）：默认关闭 ⇒ 既有调用方逐字段不变；
        #: 开启后每臂额外记 `Observation.wall_ms`（`perf_counter` 墙钟），供内化条件⑤
        #: 用真实墙钟判定 p99（S3-02 的 `duration_ms` 是标称**模型时钟**量，不同量纲）
        self.measure_wall = bool(measure_wall)
        #: 实际所用 judge 的如实标注（进层③ detail；见 `diff_judge`）
        self.judge_kind = str(judge_kind or "")

    # ── 单例回放 ────────────────────────────────────────────

    def sample_for_manual(self, case_ids: Iterable[str]) -> List[str]:
        """按本沙箱的抽检比例给出**确定性**人工抽检清单（§4.5 的 10%）"""
        return manual_sample_ids(case_ids, ratio=self.manual_ratio)

    def _run_arm(self, impl: Implementation, case: EquivalenceCase, *,
                 measure_wall: bool) -> Observation:
        """跑一臂（`measure_wall=True` 时记真实墙钟；墙钟**只测量不改语义**）"""
        if not measure_wall:
            return impl.run(case, quota=self.quota, tools=self.tools)
        started = time.perf_counter()
        obs = impl.run(case, quota=self.quota, tools=self.tools)
        obs.wall_ms = round((time.perf_counter() - started) * 1000.0, 3)
        return obs

    def replay_case(self, case: EquivalenceCase,
                    candidate: Any, *,
                    upstream: Any = None,
                    manual_flagged: bool = False,
                    measure_wall: Optional[bool] = None) -> CaseReplay:
        """一组用例的双跑：上游（用例录制程序）vs 候选（原生实现）

        两臂各自使用**全新环境**（同夹具种子）—— 共享环境会让第二臂看到第一臂的
        写入，"等价性"就变成了顺序依赖的假象。
        """
        wall = self.measure_wall if measure_wall is None else bool(measure_wall)
        upstream_impl = (as_implementation(upstream) if upstream is not None
                         else ProgramImplementation(list(case.upstream),
                                                   name="upstream"))
        candidate_impl = as_implementation(candidate, name="candidate")
        up_obs = self._run_arm(upstream_impl, case, measure_wall=wall)
        cand_obs = self._run_arm(candidate_impl, case, measure_wall=wall)
        expectation_reasons: List[str] = []
        if case.expected_status != "any" and cand_obs.status != case.expected_status:
            expectation_reasons.append(
                f"候选终态 {cand_obs.status} ≠ 用例期望 {case.expected_status}")
        missing = candidate_impl.missing_inputs(case)
        if missing:
            expectation_reasons.append(f"候选实现所需输入缺失: {missing}")
        diff = three_layer_diff(up_obs, cand_obs, case, judge=self.judge,
                                threshold=self.judge_threshold,
                                manual_flagged=manual_flagged,
                                judge_kind=self.judge_kind)
        return CaseReplay(case_id=case.case_id, capability_id=case.capability_id,
                          upstream=up_obs, candidate=cand_obs, diff=diff,
                          expectation_ok=not expectation_reasons,
                          expectation_reasons=expectation_reasons,
                          input_fingerprint=case.input_fingerprint())

    # ── 批量回放 ────────────────────────────────────────────

    def replay_many(self, cases: Sequence[EquivalenceCase],
                    candidate: Any, *,
                    upstream_provider: Optional[Callable[[EquivalenceCase], Any]] = None,
                    ) -> ReplayReport:
        """批量回放（候选实现可按用例定制；默认同一候选实现跑全部用例）"""
        active = [c for c in cases if c.active]
        report = ReplayReport(capability_id=(active[0].capability_id if active else ""))
        sample = set(self.sample_for_manual([c.case_id for c in active]))
        report.manual_sample = sorted(sample)
        for case in active:
            upstream = (upstream_provider(case) if upstream_provider else None)
            replay = self.replay_case(case, candidate, upstream=upstream,
                                      manual_flagged=case.case_id in sample)
            report.replays.append(replay)
            for layer in replay.diff.failed_layers:
                report.layer_failures[layer] = report.layer_failures.get(layer, 0) + 1
        return report

    # ── 确定性自检 ──────────────────────────────────────────

    def determinism_probe(self, case: EquivalenceCase, candidate: Any) -> Dict[str, Any]:
        """同输入同环境两次回放 → 逐字段比对（验收清单"回放确定性"证据）"""
        first = self.replay_case(case, candidate)
        second = self.replay_case(case, candidate)
        diffs: List[str] = []

        def _cmp(field_name: str, left: Any, right: Any) -> None:
            if left != right:
                diffs.append(f"{field_name}: {left!r} ≠ {right!r}")

        _cmp("candidate.status", first.candidate.status, second.candidate.status)
        _cmp("candidate.steps", first.candidate.steps, second.candidate.steps)
        _cmp("candidate.side_effects", first.candidate.side_effect_set(),
             second.candidate.side_effect_set())
        _cmp("candidate.canonical", first.candidate.canonical_text(),
             second.candidate.canonical_text())
        _cmp("candidate.duration_ms", first.candidate.duration_ms,
             second.candidate.duration_ms)
        _cmp("upstream.canonical", first.upstream.canonical_text(),
             second.upstream.canonical_text())
        return {"case_id": case.case_id, "deterministic": not diffs,
                "diffs": diffs,
                "fingerprint_first": first.candidate.fingerprint(),
                "fingerprint_second": second.candidate.fingerprint()}


# ════════════════════════════════════════════════════════════
#  record-and-replay 台账
# ════════════════════════════════════════════════════════════


@dataclass
class JournalEntry:
    """一条录制记录（**只记录**：副作用不落真实环境）"""

    case_id: str
    capability_id: str
    implementation: str
    input_fingerprint: str
    status: str
    steps: List[str] = field(default_factory=list)
    side_effects: Dict[str, List[str]] = field(default_factory=dict)
    output_fingerprint: str = ""
    duration_ms: float = 0.0
    recorded_at: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id, "capability_id": self.capability_id,
            "implementation": self.implementation,
            "input_fingerprint": self.input_fingerprint, "status": self.status,
            "steps": list(self.steps), "side_effects": dict(self.side_effects),
            "output_fingerprint": self.output_fingerprint,
            "duration_ms": self.duration_ms, "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "JournalEntry":
        known = set(cls.__dataclass_fields__)
        payload = {k: v for k, v in dict(data or {}).items() if k in known}
        return cls(**payload)

    @staticmethod
    def of(replay: CaseReplay, *, implementation: str = "candidate") -> "JournalEntry":
        obs = replay.candidate if implementation == "candidate" else replay.upstream
        return JournalEntry(
            case_id=replay.case_id, capability_id=replay.capability_id,
            implementation=implementation,
            input_fingerprint=replay.input_fingerprint, status=obs.status,
            steps=list(obs.steps), side_effects=obs.side_effect_set(),
            output_fingerprint=obs.fingerprint(),
            duration_ms=obs.duration_ms, recorded_at=time.time())


class RecordReplayJournal:
    """record-and-replay 台账（JSONL；同输入重放须逐字段一致）"""

    def __init__(self, directory: str = "", *, filename: str = JOURNAL_FILENAME) -> None:
        self.dir = str(directory or DEFAULT_JOURNAL_DIR)
        self.path = os.path.join(self.dir, filename)
        os.makedirs(self.dir, exist_ok=True)

    def record(self, replay: CaseReplay, *,
               implementation: str = "candidate") -> JournalEntry:
        entry = JournalEntry.of(replay, implementation=implementation)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
        return entry

    def entries(self) -> List[JournalEntry]:
        out: List[JournalEntry] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(JournalEntry.from_dict(json.loads(line)))
                    except ValueError:
                        continue
        except OSError:
            return out
        return out

    def find(self, case_id: str, *,
             implementation: str = "candidate") -> Optional[JournalEntry]:
        matched = [e for e in self.entries()
                   if e.case_id == case_id and e.implementation == implementation]
        return matched[-1] if matched else None

    def verify(self, replay: CaseReplay, *,
               implementation: str = "candidate") -> Dict[str, Any]:
        """与既有录制比对：字段级差异清单（record-and-replay 的"重放一致"证据）"""
        entry = JournalEntry.of(replay, implementation=implementation)
        recorded = self.find(entry.case_id, implementation=implementation)
        if recorded is None:
            return {"case_id": entry.case_id, "found": False, "replay_ok": False,
                    "diffs": ["无录制记录（需先 record）"]}
        diffs: List[str] = []
        for field_name in ("input_fingerprint", "status", "steps", "side_effects",
                           "output_fingerprint", "duration_ms"):
            left = getattr(recorded, field_name)
            right = getattr(entry, field_name)
            if left != right:
                diffs.append(f"{field_name}: 录制 {left!r} ≠ 重放 {right!r}")
        return {"case_id": entry.case_id, "found": True, "replay_ok": not diffs,
                "diffs": diffs}

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


__all__ = [
    "SandboxError", "SandboxEscapeError", "SandboxQuotaExceeded",
    "SandboxQuota", "ReplayEnv", "Observation", "run_program",
    "LAYER_STRUCTURE", "LAYER_SIDE_EFFECTS", "LAYER_JUDGE", "LAYER_KINDS",
    "JUDGE_THRESHOLD", "MANUAL_SAMPLE_RATIO", "EXTERNAL_LABELS",
    "TOOL_LATENCY_MS", "DEFAULT_LATENCY_MS",
    "OBS_SUCCESS", "OBS_ERROR", "OBS_QUOTA_EXCEEDED", "OBS_ESCAPE_BLOCKED",
    "OBS_UNBOUND_INPUT", "OBS_DENIED",
    "ERR_UNBOUND_INPUT", "ERR_ESCAPE", "ERR_QUOTA_STEPS", "ERR_QUOTA_FILES",
    "ERR_QUOTA_BYTES", "ERR_QUOTA_EXTERNAL", "ERR_QUOTA_TIME",
    "ERR_EXTERNAL_DENIED",
    "ConditionContext", "evaluate_condition", "condition_matches",
    "condition_unknown_atoms", "is_destructive_condition", "classify_condition",
    "DESTRUCTIVE_TOKENS",
    "bind_value", "bind_params", "bind_program", "required_input_keys",
    "missing_input_keys", "complete_params", "TOOL_REQUIRED_PARAMS",
    "KNOWN_PARAM_NAMES", "restore_param_key", "restore_param_keys",
    "FILL_FROM_POSITION", "FILL_FROM_LABEL", "FILL_FROM_INPUT",
    "Implementation", "ProgramImplementation", "TemplateImplementation",
    "PatternImplementation", "CallableImplementation", "as_implementation",
    "LayerResult", "DiffResult", "diff_structure", "diff_side_effects",
    "diff_judge", "three_layer_diff", "judge_similarity", "manual_sample_ids",
    "schema_of", "_sets_match",
    "CaseReplay", "ReplayReport", "ReplaySandbox",
    "JournalEntry", "RecordReplayJournal",
]
