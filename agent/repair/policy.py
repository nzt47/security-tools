"""自修复 L1 策略常量：只读区黑名单 / 范围护栏 / 预算与轮次上限（TASK-S7-02）

【任务定位】
    本模块是 S7-02「自动诊断与补丁 PR」的**硬闸参数单一权威**。任务书 §一 边界 ②
    与 §三 步骤 3 的三条范围护栏（只读区 / 文件数 / 行数）都在这里有唯一落点：
    任何调用方（护栏、验证器、入口脚本）都只能读本模块，**不得各写一份阈值**——
    阈值散落是「护栏可被绕过」的典型成因。

【不易（三条宪法式护栏）】
    1. **只读区**：``READONLY_ZONES`` 为前缀清单，命中即拒（不是警告）。
       清单来源为任务书 §一 边界 ② 的逐字列举：``core/auth/``、``core/audit/``、
       ``schema/``、``agent/audit/chain.py``、``agent/security/``。
    2. **范围上限**：单次改动 ≤ ``MAX_CHANGED_FILES`` 文件、单文件 ≤ ``MAX_LINES_PER_FILE``
       行（默认 3 / 120，与任务书一致）。
    3. **改测试断言的标注纪律**：不是拒绝，而是**必须显式标注**——因为确有
       「测试写错了」这一合法情形；但「悄悄把断言改松以过关」正是本任务要防的
       自欺，所以只允许「改了但说出来」这一条路径（见 ``propose.pr_description``）。

【变易】
    阈值全部可经环境变量覆盖（``CP_REPAIR_*``），非法值一律**回退默认**并留下
    ``notes``——延续批次总表「可调参数走 .env/config 且非法值回退默认」的硬约束。
    ``policy_from_env()`` 是唯一的读取入口。

【只读区为何是「前缀 + 精确文件」两种粒度】
    ``core/audit/`` 是目录级（整目录禁止改动），``agent/audit/chain.py`` 是文件级
    （同目录下 ``facade.py`` 等允许作为调用方使用，但链本身不可改）。两种粒度都
    必须支持，否则要么放过了链文件，要么误封了整个 audit 包。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

# ════════════════════════════════════════════════════════════
#  只读区（任务书 §一 边界 ② 逐字列举）
# ════════════════════════════════════════════════════════════

#: 只读区（目录前缀，统一以 ``/`` 结尾；与仓库相对路径比较）
READONLY_ZONE_PREFIXES: Tuple[str, ...] = (
    "core/auth/",
    "core/audit/",
    "schema/",
    "agent/security/",
    # L0 锚是**系统不可写**的人工冻结标尺（S5-02）：改它等于改标尺，
    # 属「为了让补丁过关而改判分标准」，与只读区同等禁止。
    "eval/l0_anchor/",
)

#: 只读区（精确文件；同目录其他文件不在此列）
READONLY_ZONE_FILES: Tuple[str, ...] = (
    "agent/audit/chain.py",
)

#: 审计/签名相关的**文件名模式**（第三道网：fail-closed）
#: 即使未来新增 ``agent/xxx/signing_keys.py`` 一类文件，只要命中模式即拒——
#: 「未登记」不等于「允许改动」。
READONLY_NAME_PATTERNS: Tuple[str, ...] = (
    "audit_chain.py",
    "daily_roots.py",
    "roots_signer.py",
    "signing.py",
    "signature.py",
    "merkle.py",
)

#: 一次修复允许触碰的**最大文件数**（默认 3）
DEFAULT_MAX_CHANGED_FILES = 3
#: 单文件允许的**最大改动行数**（增 + 删，默认 120）
DEFAULT_MAX_LINES_PER_FILE = 120
#: 单次修复的默认 token 预算
DEFAULT_BUDGET_TOKENS = 60000
#: 单次修复的默认最大轮次（防无限自我修改）
DEFAULT_MAX_ROUNDS = 2
#: 子代理默认超时（秒）
DEFAULT_TIMEOUT_SECONDS = 600.0
#: 源码切片默认上下文行数（失败点 ±N）
DEFAULT_SLICE_RADIUS = 40
#: 近期改动回看提交数（只读 ``git log``）
DEFAULT_HISTORY_COMMITS = 10

#: 环境变量前缀
ENV_PREFIX = "CP_REPAIR_"


def _env_text(env: Mapping[str, str], name: str) -> str:
    """读取 ``CP_REPAIR_<name>``（大小写不敏感查找；缺失 → 空串）

    **纯函数**：显式传入 env 映射，不读写进程环境——这样单测可以直接构造
    任意环境而不污染全局 `os.environ`（测试隔离纪律）。
    """
    key = (ENV_PREFIX + name).lower()
    for k, v in env.items():
        if str(k).lower() == key:
            return str(v or "").strip()
    return ""


def _env_int(env: Mapping[str, str], name: str, default: int, *,
             minimum: int = 1, notes: List[str]) -> int:
    """读整数型策略值；缺失/非法/越界 → 回退默认并记 note（不抛）"""
    text = _env_text(env, name)
    if not text:
        return default
    try:
        value = int(text)
    except (TypeError, ValueError):
        notes.append(f"{ENV_PREFIX}{name}={text!r} 非法（非整数）→ 回退默认 {default}")
        return default
    if value < minimum:
        notes.append(f"{ENV_PREFIX}{name}={value} 小于下界 {minimum} → 回退默认 {default}")
        return default
    return value


def _env_float(env: Mapping[str, str], name: str, default: float, *,
               minimum: float = 1.0, notes: List[str]) -> float:
    """读浮点型策略值；缺失/非法/越界 → 回退默认并记 note（不抛）"""
    text = _env_text(env, name)
    if not text:
        return default
    try:
        value = float(text)
    except (TypeError, ValueError):
        notes.append(f"{ENV_PREFIX}{name}={text!r} 非法（非数字）→ 回退默认 {default}")
        return default
    if value < minimum:
        notes.append(f"{ENV_PREFIX}{name}={value} 小于下界 {minimum} → 回退默认 {default}")
        return default
    return value


# ════════════════════════════════════════════════════════════
#  路径归一与只读判定
# ════════════════════════════════════════════════════════════


def normalize_relpath(path: Any) -> str:
    """把 diff 头/调用方传来的路径归一为仓库相对 POSIX 路径

    - 去掉 ``a/`` / ``b/`` 前缀（unified diff 惯例）；
    - 反斜杠转正斜杠；
    - 去掉开头的 ``./``；
    - **不做** abspath 解析：只读区判定必须建立在「仓库相对路径」这一稳定口径上，
      否则同一文件在不同 worktree 下会产生不同的绝对路径，护栏形同虚设。
    """
    text = str(path or "").strip().strip('"')
    text = text.replace("\\", "/")
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def is_readonly_path(path: Any) -> bool:
    """路径是否命中只读区（前缀 / 精确文件 / 敏感文件名模式）"""
    rel = normalize_relpath(path)
    if not rel:
        return False
    for prefix in READONLY_ZONE_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return True
    if rel in READONLY_ZONE_FILES:
        return True
    name = rel.rsplit("/", 1)[-1].lower()
    return any(name == pattern for pattern in READONLY_NAME_PATTERNS)


def readonly_reason(path: Any) -> str:
    """命中只读区的可读原因（未命中返回空串）"""
    rel = normalize_relpath(path)
    if not rel:
        return ""
    for prefix in READONLY_ZONE_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            return f"只读区目录 {prefix}"
    if rel in READONLY_ZONE_FILES:
        return "只读区文件（审计链/签名相关）"
    name = rel.rsplit("/", 1)[-1].lower()
    for pattern in READONLY_NAME_PATTERNS:
        if name == pattern:
            return f"审计/签名相关文件名模式 {pattern}"
    return ""


#: 测试文件识别规则（命中即在 PR 描述中要求显式标注「修改了测试断言」）
TEST_DIR_SEGMENTS: Tuple[str, ...] = ("tests/", "test/", "testing/")


def is_test_path(path: Any) -> bool:
    """是否测试文件（目录段 ``tests/`` 或文件名 ``test_*.py`` / ``*_test.py``）"""
    rel = normalize_relpath(path).lower()
    if not rel:
        return False
    if any(seg in rel for seg in TEST_DIR_SEGMENTS):
        return True
    name = rel.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py")


# ════════════════════════════════════════════════════════════
#  策略
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RepairPolicy:
    """一次修复的全部硬闸参数（唯一权威；调用方不得自定阈值）

    Attributes:
        max_changed_files: 单次改动文件数上限。
        max_lines_per_file: 单文件改动行数上限（增 + 删）。
        budget_tokens: 单次修复 token 预算上限。
        max_rounds: 最大轮次（每轮 = 一次派工 + 一次验证）。
        timeout_seconds: 子代理单次委派超时。
        slice_radius: 源码切片上下文半径（行）。
        history_commits: 近期改动回看提交数。
        notes: 环境变量非法值回退记录（如实披露，不静默）。
    """

    max_changed_files: int = DEFAULT_MAX_CHANGED_FILES
    max_lines_per_file: int = DEFAULT_MAX_LINES_PER_FILE
    budget_tokens: int = DEFAULT_BUDGET_TOKENS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    slice_radius: int = DEFAULT_SLICE_RADIUS
    history_commits: int = DEFAULT_HISTORY_COMMITS
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_changed_files": int(self.max_changed_files),
            "max_lines_per_file": int(self.max_lines_per_file),
            "budget_tokens": int(self.budget_tokens),
            "max_rounds": int(self.max_rounds),
            "timeout_seconds": float(self.timeout_seconds),
            "slice_radius": int(self.slice_radius),
            "history_commits": int(self.history_commits),
            "notes": list(self.notes),
        }


def policy_from_env(env: Optional[Mapping[str, str]] = None) -> RepairPolicy:
    """构造策略：默认值 + ``CP_REPAIR_*`` 覆盖（非法值回退默认并记 note）

    Args:
        env: 显式环境映射（测试用）；None → 读进程环境。
    """
    notes: List[str] = []
    source: Mapping[str, str] = env if env is not None else os.environ
    return RepairPolicy(
        max_changed_files=_env_int(source, "MAX_CHANGED_FILES", DEFAULT_MAX_CHANGED_FILES,
                                   notes=notes),
        max_lines_per_file=_env_int(source, "MAX_LINES_PER_FILE", DEFAULT_MAX_LINES_PER_FILE,
                                    notes=notes),
        budget_tokens=_env_int(source, "BUDGET_TOKENS", DEFAULT_BUDGET_TOKENS, notes=notes),
        max_rounds=_env_int(source, "MAX_ROUNDS", DEFAULT_MAX_ROUNDS, notes=notes),
        timeout_seconds=_env_float(source, "TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS,
                                   notes=notes),
        slice_radius=_env_int(source, "SLICE_RADIUS", DEFAULT_SLICE_RADIUS, minimum=0,
                              notes=notes),
        history_commits=_env_int(source, "HISTORY_COMMITS", DEFAULT_HISTORY_COMMITS,
                                 minimum=0, notes=notes),
        notes=tuple(notes),
    )


#: 允许子代理**申请**的工具白名单（只读源码 + 产出补丁文本）
#:
#: 任务书 §三 步骤 3：「子代理只读源码 + 只能产出补丁文本，**不给写权限、不给审批权、
#: 不给记忆读写**」。白名单是「申请 ∩ 授权 ∩ 矩阵」三步交集里我们主动交出的那一半；
#: 另一半由 ``agent.security.actor_matrix``（矩阵）与调用方授权子集守着。
REPAIR_SUBAGENT_TOOLS: Tuple[str, ...] = (
    "read_file",
    "list_dir",
    "grep",
    "read_only_git",
)

#: 明细禁止工具（写 / 审批 / 记忆 / 治理）：即使被申请也必须不可见（自证用）
REPAIR_FORBIDDEN_TOOLS: Tuple[str, ...] = (
    "write_file", "edit_file", "apply_patch", "delete_file", "move_file",
    "run_command", "shell", "bash", "git_commit", "git_push", "git_merge",
    "approval.approve", "approval.deny", "approval.submit",
    "memory.read", "memory.write", "memory.recall",
    "governance.modify_policy", "core.rewrite", "self_rewrite",
)

#: L0 锚的层名（评测标尺，系统不可写）
ANCHOR_LAYER = "L0"

#: PR 描述中「人工复核重点」的最小条目数（少于该数视为未完成披露）
MIN_REVIEW_FOCUS_ITEMS = 3


def describe_readonly_zones() -> Dict[str, Sequence[str]]:
    """只读区清单的只读视图（供报告/PR 描述披露）"""
    return {
        "prefixes": list(READONLY_ZONE_PREFIXES),
        "files": list(READONLY_ZONE_FILES),
        "name_patterns": list(READONLY_NAME_PATTERNS),
    }


__all__ = [
    "READONLY_ZONE_PREFIXES", "READONLY_ZONE_FILES", "READONLY_NAME_PATTERNS",
    "DEFAULT_MAX_CHANGED_FILES", "DEFAULT_MAX_LINES_PER_FILE", "DEFAULT_BUDGET_TOKENS",
    "DEFAULT_MAX_ROUNDS", "DEFAULT_TIMEOUT_SECONDS", "DEFAULT_SLICE_RADIUS",
    "DEFAULT_HISTORY_COMMITS", "ENV_PREFIX", "ANCHOR_LAYER", "MIN_REVIEW_FOCUS_ITEMS",
    "RepairPolicy", "policy_from_env", "normalize_relpath", "is_readonly_path",
    "readonly_reason", "is_test_path", "describe_readonly_zones",
    "REPAIR_SUBAGENT_TOOLS", "REPAIR_FORBIDDEN_TOOLS",
]
