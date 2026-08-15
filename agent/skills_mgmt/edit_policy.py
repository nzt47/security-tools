"""受控编辑策略（Edit Policy）— 任务 EVO-T5 元智能体编辑的边界与审批数据模型

【任务定位】
    落地设计文档"工具进化（从工具使用者到工具创造者）"主张的**受控化**：
    元智能体只允许编辑白名单内的技能文件，禁止触碰系统代码（agent/ 核心模块）；
    所有编辑产物进入「提案 → 三重审核 → 人工审批 → git 合并」受控链路。

【不易边界（来自任务 05_工具进化升级_元智能体受控编辑.md）】
    1. 白名单目录默认 data/skills_repo（技能仓库），系统目录（agent/ 等）禁止编辑；
    2. 进化策略文件自身（edit_policy/meta_editor/parent_selection/lineage/evaluator 等）
       永不进入白名单 —— 本任务不开启"修改选择策略本身"的递归自修改；
    3. 编辑类型白名单：技能正文 / 参数默认值 / 工具文档；
       禁止：导入语句、依赖变更、执行逻辑核心（可配置开关）；
    4. EditProposal 状态机（与任务 6 approval.py 提案状态对齐）：
       draft → pending_review → approved / rejected → merged / archived；
       merged 只能由显式人工审批（approve）触发，不存在自动合并路径。

【配置（.env，全部带默认值）】
    META_EDIT_WHITELIST_DIRS        白名单目录（逗号分隔，相对项目根），默认 data/skills_repo
    META_EDIT_MAX_FILES_PER_ROUND   单个提案最大文件数 N，默认 1
    META_EDIT_BLOCKED_PATTERNS      内容黑名单正则（逗号分隔，追加到内置默认）
"""

from __future__ import annotations

import difflib
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .observability import logger

# ════════════════════════════════════════════════════════════
#  枚举
# ════════════════════════════════════════════════════════════


class EditType(str, Enum):
    """允许的编辑类型白名单

    - CONTENT:       技能正文（skill.md body / 使用说明）
    - PARAMS:        参数默认值（default_params / config_schema）
    - DOCUMENTATION: 工具文档（README / 使用文档等技能仓库内文档）
    """
    CONTENT = "content"
    PARAMS = "params"
    DOCUMENTATION = "documentation"


class EditStatus(str, Enum):
    """编辑提案审批状态机（与任务 6 approval.py 提案状态对齐）

    draft → pending_review → approved / rejected → merged / archived
    语义（守不易）:
        draft:          提案已生成（未提交审核）
        pending_review: 已生成谱系记录，等待人工审批（默认落点，绝不自动合并）
        approved:       人工审批通过（等待合并）
        rejected:       人工/自动审核拒绝
        merged:         已合并进 git（唯一入口 = 显式 approve 后的 mark_merged）
        archived:       归档（被取代/历史提案）
    """
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    MERGED = "merged"
    ARCHIVED = "archived"


# 状态机转移表：仅允许表中边，其余转移抛 EditStatusTransitionError
# 【变易】DRAFT 允许直接 REJECTED：Reviewer 三重审核在提交（submit）之前执行，
# 任一 critical 级问题直接拒绝（验收 2：不进入评估），提案状态须能表达"审核即拒"。
_EDIT_TRANSITIONS: Dict[EditStatus, Set[EditStatus]] = {
    EditStatus.DRAFT: {EditStatus.PENDING_REVIEW, EditStatus.REJECTED, EditStatus.ARCHIVED},
    EditStatus.PENDING_REVIEW: {EditStatus.APPROVED, EditStatus.REJECTED},
    EditStatus.APPROVED: {EditStatus.MERGED, EditStatus.ARCHIVED},
    EditStatus.REJECTED: {EditStatus.ARCHIVED},
    EditStatus.MERGED: {EditStatus.ARCHIVED},
    EditStatus.ARCHIVED: set(),
}


# ════════════════════════════════════════════════════════════
#  异常
# ════════════════════════════════════════════════════════════


class EditPolicyError(Exception):
    """受控编辑策略异常（路径/类型/内容越界）"""

    def __init__(self, message: str, *, code: str = "EDIT_POLICY_VIOLATION",
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}


class PathNotAllowedError(EditPolicyError):
    """路径越界：白名单之外 / 系统目录 / 进化策略文件"""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="EDIT_PATH_NOT_ALLOWED", details=details)


class EditTypeNotAllowedError(EditPolicyError):
    """编辑类型越界：非白名单类型 / 尝试改动导入或依赖"""

    def __init__(self, message: str, *, code: str = "EDIT_TYPE_NOT_ALLOWED",
                 details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code=code, details=details)


class EditStatusTransitionError(EditPolicyError):
    """非法状态转移（如未审批直接合并 / 已合并再审批）"""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="EDIT_STATUS_TRANSITION",
                         details=details)


class EditContentBlockedError(EditPolicyError):
    """内容命中黑名单模式（危险代码/注入痕迹）"""

    def __init__(self, message: str, *, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, code="EDIT_CONTENT_BLOCKED", details=details)


# ════════════════════════════════════════════════════════════
#  内置配置与 .env 读取
# ════════════════════════════════════════════════════════════

# 项目根 = agent/ 上一级（edit_policy.py 位于 agent/skills_mgmt/）
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# 默认白名单目录（相对项目根）—— 与 file_store.SkillFileStore 的默认仓库一致
_DEFAULT_WHITELIST_DIRS = ("data/skills_repo",)

# 进化策略文件自身（验收 7：白名单不得包含进化策略文件，杜绝递归自修改）
# 命中即拒绝，与白名单配置无关（第二层防线：即使误配白名单也拦截）
_STRATEGY_FILES = frozenset({
    "edit_policy.py", "meta_editor.py", "parent_selection.py",
    "lineage.py", "evaluator.py", "offline_evolver.py",
    "approval.py", "rollback.py", "value_guard.py",
    "git_sync.py", "creator.py", "reviewer.py", "system_tools.py",
    "enhancer.py", "service.py",
})

# 始终禁止编辑的核心目录（相对项目根；叠加在白名单之外的第二层防线）
_FORBIDDEN_REL_DIRS = (
    "agent", "core", "planning", "configs", "config", "scripts",
    "mcp_services", "memory", "sensor", "static", "templates",
)

# 禁止写入的扩展名（可执行/脚本，与 file_tools.BLOCKED_WRITE_EXTENSIONS 对齐）
_BLOCKED_EXTENSIONS = frozenset({
    ".exe", ".dll", ".sys", ".bin", ".bat", ".cmd", ".ps1", ".vbs",
    ".scr", ".pif", ".com", ".msi", ".reg", ".pyc", ".pyo", ".so", ".o",
})

# 内置内容黑名单：危险代码 / 注入痕迹（元智能体生成内容不得含执行核心与逃逸代码）
_DEFAULT_BLOCKED_PATTERNS = [
    # 危险执行（代码注入）
    re.compile(r"\beval\s*\(", re.I),
    re.compile(r"\bexec\s*\(", re.I),
    re.compile(r"__import__\s*\("),
    re.compile(r"\bcompile\s*\(", re.I),
    re.compile(r"\bos\.system\s*\(", re.I),
    # 反序列化攻击 / 反射逃逸
    re.compile(r"\bimport\s+pickle|\bfrom\s+pickle\b", re.I),
    re.compile(r"\bimport\s+marshal|\bfrom\s+marshal\b", re.I),
    re.compile(r"\bimport\s+ctypes|\bfrom\s+ctypes\b", re.I),
    re.compile(r"__globals__|__subclasses__|__bases__|__mro__"),
    # 提示词注入指令（元智能体输出不得含注入指令，见任务"提示词与技能代码隔离"）
    re.compile(r"忽略(?:上述|上面|之前)指令", re.I),
    re.compile(r"ignore\s+(?:previous|above|prior)\s+instructions?", re.I),
    re.compile(r"</?system\s*>", re.I),
]

# 导入语句探测：新增 import / from ... import 行
_IMPORT_ADDED_RE = re.compile(
    r"^\s*(?:from\s+[\w.]+|import\s+\w+)", re.MULTILINE)

# front matter 依赖字段探测（技能参数默认值 / 依赖变更在 meta 区）
# MULTILINE：依赖声明位于 front matter 中部（非字符串开头），必须逐行匹配
_DEPS_META_RE = re.compile(r"^dependencies\s*:", re.MULTILINE)


def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _env_whitelist_dirs() -> Tuple[str, ...]:
    raw = _env_str("META_EDIT_WHITELIST_DIRS",
                   ",".join(_DEFAULT_WHITELIST_DIRS))
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _env_blocked_patterns() -> List[re.Pattern]:
    raw = _env_str("META_EDIT_BLOCKED_PATTERNS", "").strip()
    if not raw:
        return []
    patterns: List[re.Pattern] = []
    for p in raw.split(","):
        p = p.strip()
        if not p:
            continue
        try:
            patterns.append(re.compile(p))
        except re.error as e:
            logger.warning("[EditPolicy] 忽略非法黑名单正则 %r: %s", p, e)
    return patterns


# ════════════════════════════════════════════════════════════
#  编辑文件与提案数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class EditFile:
    """单个文件的编辑内容（old → new）

    file_path: 相对技能仓库根目录的路径（如 my_skill/skill.md）
    """
    file_path: str
    old_content: str
    new_content: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "old_content": self.old_content,
            "new_content": self.new_content,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditFile":
        return cls(
            file_path=data.get("file_path", ""),
            old_content=data.get("old_content", ""),
            new_content=data.get("new_content", ""),
        )


def _gen_proposal_id() -> str:
    ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
    return f"edt-{ts}-{uuid.uuid4().hex[:8]}"


def _unified_diff(old: str, new: str, file_path: str) -> str:
    """生成单文件 unified diff（无第三方依赖，difflib 标准库）"""
    lines = list(difflib.unified_diff(
        (old or "").splitlines(keepends=True),
        (new or "").splitlines(keepends=True),
        fromfile=f"a/{file_path}", tofile=f"b/{file_path}",
    ))
    return "".join(lines)


@dataclass
class EditProposal:
    """一次元智能体编辑提案（一次仅一个提案，可含多个文件）

    字段语义:
        object_type:  "tool_code"（技能正文/参数）或 "tool_doc"（工具文档）
        object_id:    技能 ID（对应技能仓库目录名）
        files:        待编辑文件列表（≤ META_EDIT_MAX_FILES_PER_ROUND）
        patch:        unified diff（自动从 files 生成）
        expected_gain: 预期收益说明（供人工审批参考）
        status:       审批状态（EditStatus，默认 draft）
        lineage_record_id: pending_review 落谱系后的记录 ID
    """

    object_type: str = "tool_code"
    object_id: str = ""
    files: List[EditFile] = field(default_factory=list)
    edit_type: str = EditType.CONTENT.value
    change_summary: str = ""
    expected_gain: str = ""
    patch: str = ""
    proposal_id: str = ""
    status: str = EditStatus.DRAFT.value
    parent_record_id: Optional[str] = None
    lineage_record_id: Optional[str] = None
    review: Optional[Dict[str, Any]] = None
    eval_result: Optional[Dict[str, Any]] = None
    cost_tokens: int = 0
    decision_reason: str = ""
    merge_commit_sha: str = ""  # 合并后的 git commit SHA（回滚依据，仅 merged 状态有效）
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        if not self.proposal_id:
            self.proposal_id = _gen_proposal_id()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now
        if not self.patch and self.files:
            self.patch = self.diff()
        # 状态枚举值兼容（允许传入 EditStatus 枚举或字符串，统一归一化为 .value）
        if isinstance(self.status, EditStatus):
            self.status = self.status.value
        elif isinstance(self.status, str):
            try:
                self.status = EditStatus(self.status).value
            except ValueError:
                raise EditStatusTransitionError(
                    f"非法提案状态: {self.status!r}",
                    details={"status": self.status},
                ) from None
        else:
            raise EditStatusTransitionError(
                f"非法提案状态类型: {type(self.status).__name__}",
                details={"status": self.status},
            )

    # ─── 工具 ───

    def _touch(self) -> None:
        self.updated_at = datetime.now().isoformat(timespec="seconds")

    def _transition(self, target: EditStatus) -> None:
        """状态机守卫：非法转移直接拒绝（守不易：无自动合并路径）"""
        current = EditStatus(self.status)
        allowed = _EDIT_TRANSITIONS[current]
        if target not in allowed:
            raise EditStatusTransitionError(
                f"非法状态转移 {current.value} → {target.value}"
                f"（允许: {sorted(s.value for s in allowed) or '无'}）",
                details={"proposal_id": self.proposal_id,
                         "from": current.value, "to": target.value},
            )
        self.status = target.value
        self._touch()

    def diff(self) -> str:
        """生成全部文件的 unified diff"""
        return "\n".join(
            _unified_diff(f.old_content, f.new_content, f.file_path)
            for f in self.files
        )

    # ─── 状态机操作 ───

    def submit(self) -> None:
        """draft → pending_review：提交等待人工审批（不自动合并）"""
        self._transition(EditStatus.PENDING_REVIEW)

    def approve(self) -> None:
        """pending_review → approved：人工审批通过（仍不合并，需显式 mark_merged）"""
        self._transition(EditStatus.APPROVED)

    def reject(self, reason: str = "") -> None:
        """pending_review → rejected：拒绝"""
        self.decision_reason = reason or self.decision_reason
        self._transition(EditStatus.REJECTED)

    def mark_merged(self) -> None:
        """approved → merged：唯一合并入口（只有显式人工审批后才允许调用）"""
        self._transition(EditStatus.MERGED)

    def archive(self) -> None:
        """→ archived：归档（被取代/历史提案）"""
        self._transition(EditStatus.ARCHIVED)

    @property
    def is_mergeable(self) -> bool:
        """是否已获人工审批（可合并）"""
        return EditStatus(self.status) == EditStatus.APPROVED

    @property
    def status_enum(self) -> EditStatus:
        return EditStatus(self.status)

    # ─── 序列化 ───

    def to_dict(self) -> Dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "object_type": self.object_type,
            "object_id": self.object_id,
            "files": [f.to_dict() for f in self.files],
            "edit_type": self.edit_type,
            "change_summary": self.change_summary,
            "expected_gain": self.expected_gain,
            "patch": self.patch,
            "status": self.status,
            "parent_record_id": self.parent_record_id,
            "lineage_record_id": self.lineage_record_id,
            "review": self.review,
            "eval_result": self.eval_result,
            "cost_tokens": self.cost_tokens,
            "decision_reason": self.decision_reason,
            "merge_commit_sha": self.merge_commit_sha,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EditProposal":
        files = [EditFile.from_dict(f) for f in data.get("files") or []]
        keep = {k: v for k, v in data.items() if k != "files"}
        return cls(files=files, **keep)


# ════════════════════════════════════════════════════════════
#  受控编辑策略（白名单 + 路径校验 + 编辑类型/内容校验）
# ════════════════════════════════════════════════════════════


class EditPolicy:
    """元智能体编辑的边界控制

    双层防线（守不易）:
        第一层 白名单：仅允许 whitelist_dirs 内的文件；
        第二层 禁区：_STRATEGY_FILES（进化策略文件）与 _FORBIDDEN_REL_DIRS
               （核心系统目录）永远拦截 —— 即使白名单被误配也无法触碰。

    线程安全: 本类无共享可变状态（构造后只读），可全局复用。
    """

    def __init__(self, whitelist_dirs: Optional[List[Any]] = None, *,
                 project_root: Optional[Path] = None,
                 allowed_edit_types: Optional[Set[EditType]] = None,
                 block_import_changes: bool = True,
                 blocked_content_patterns: Optional[List[re.Pattern]] = None,
                 max_files_per_round: Optional[int] = None,
                 extra_forbidden_files: Optional[Set[str]] = None):
        """
        Args:
            whitelist_dirs: 白名单目录列表（str/Path，相对 project_root）
                           None=读 .env META_EDIT_WHITELIST_DIRS（默认 data/skills_repo）
            project_root: 项目根目录（None=agent/ 上级）
            allowed_edit_types: 允许的编辑类型（None=全部 EditType）
            block_import_changes: 是否拦截"新增导入语句/依赖变更"（默认 True）
            blocked_content_patterns: 额外内容黑名单（追加到内置默认）
            max_files_per_round: 单提案最大文件数（None=读 .env）
            extra_forbidden_files: 追加禁止编辑的文件名（第二层防线）
        """
        self._project_root = (project_root or _PROJECT_ROOT).resolve()
        raw_dirs = whitelist_dirs if whitelist_dirs is not None else list(_env_whitelist_dirs())
        self._whitelist_dirs: List[Path] = []
        for d in raw_dirs:
            p = (self._project_root / str(d)).resolve()
            self._whitelist_dirs.append(p)
        # 防线：白名单目录本身不得落在系统核心目录内（含 agent/）
        for p in self._whitelist_dirs:
            self._assert_not_core(p)
        self._allowed_edit_types = (
            set(allowed_edit_types) if allowed_edit_types is not None
            else set(EditType))
        self._block_import_changes = block_import_changes
        self._blocked_patterns = (
            list(_DEFAULT_BLOCKED_PATTERNS)
            + list(blocked_content_patterns or [])
            + _env_blocked_patterns()
        )
        self._max_files_per_round = (
            max_files_per_round if max_files_per_round is not None
            else _env_int("META_EDIT_MAX_FILES_PER_ROUND", 1))
        self._forbidden_files = _STRATEGY_FILES | set(extra_forbidden_files or ())

    # ─── 查询 ───

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def whitelist_dirs(self) -> List[Path]:
        """白名单目录（绝对路径，只读视图）"""
        return list(self._whitelist_dirs)

    @property
    def allowed_edit_types(self) -> Set[EditType]:
        return set(self._allowed_edit_types)

    @property
    def max_files_per_round(self) -> int:
        return self._max_files_per_round

    def whitelisted(self, path: Path) -> bool:
        """路径是否落在白名单内（不含第二层防线的最终放行判定）"""
        resolved = path.resolve()
        return any(self._is_within(resolved, d) for d in self._whitelist_dirs)

    def is_strategy_file(self, path: Path) -> bool:
        """是否进化策略文件（验收 7：白名单不得含进化策略文件）"""
        return path.resolve().name in self._forbidden_files

    # ─── 校验 ───

    def validate_file_path(self, path: Any) -> Path:
        """校验并解析文件路径（白名单 + 禁区 + 扩展名）

        Raises:
            PathNotAllowedError: 越界（白名单外 / 系统目录 / 策略文件 / 禁止扩展名）
        """
        p = Path(str(path))
        # 相对路径统一按 project_root 解析（避免依赖 CWD，测试/服务运行目录不可控），
        # 禁 .. 逃逸：resolve 后必须仍在白名单内。
        resolved = p.resolve() if p.is_absolute() else (self._project_root / p).resolve()
        if not self.whitelisted(resolved):
            raise PathNotAllowedError(
                f"路径不在可编辑白名单内: {path}",
                details={"path": str(p),
                         "whitelist": [str(d) for d in self._whitelist_dirs]},
            )
        self._assert_not_core(resolved)
        if self.is_strategy_file(resolved):
            raise PathNotAllowedError(
                f"进化策略文件禁止编辑（拒绝递归自修改）: {resolved.name}",
                details={"path": str(p), "file": resolved.name},
            )
        if resolved.suffix.lower() in _BLOCKED_EXTENSIONS:
            raise PathNotAllowedError(
                f"禁止编辑该扩展名文件: {resolved.suffix}",
                details={"path": str(p), "extension": resolved.suffix},
            )
        return resolved

    def validate_edit_type(self, edit_type: Any) -> None:
        """编辑类型白名单校验

        兼容 EditType 枚举成员与字符串（如 "content"/"params"/"documentation"）。

        Raises:
            EditTypeNotAllowedError: 非白名单编辑类型
        """
        if isinstance(edit_type, EditType):
            et = edit_type
        else:
            try:
                et = EditType(str(edit_type))
            except ValueError:
                raise EditTypeNotAllowedError(
                    f"非法编辑类型: {edit_type}（允许: "
                    f"{sorted(t.value for t in self._allowed_edit_types)}）",
                ) from None
        if et not in self._allowed_edit_types:
            raise EditTypeNotAllowedError(
                f"编辑类型不在白名单内: {et.value}（允许: "
                f"{sorted(t.value for t in self._allowed_edit_types)}）",
                details={"edit_type": et.value},
            )

    def validate_scope(self, old_content: str, new_content: str,
                       file_path: str = "") -> None:
        """校验编辑范围：拦截新增导入语句 / 依赖变更（可配置开关）

        判定口径:
            - 新增 import / from ... import 行（diff 后仅存在于新内容）→ 拦截；
            - front matter 出现或变更 dependencies 字段 → 拦截。
        Raises:
            EditTypeNotAllowedError: 尝试改动导入/依赖（执行逻辑核心）
        """
        if not self._block_import_changes:
            return
        old_lines = (old_content or "").splitlines()
        old_imports = {
            m.group(0) for m in
            (_IMPORT_ADDED_RE.finditer(old_content or ""))
        }
        old_has_deps = bool(_DEPS_META_RE.search(old_content or ""))
        new_has_deps = bool(_DEPS_META_RE.search(new_content or ""))
        new_imports = {
            m.group(0) for m in
            (_IMPORT_ADDED_RE.finditer(new_content or ""))
        }
        added_imports = new_imports - old_imports
        # import 行按内容整体比对不可靠（行内改动），此处只拦"全新出现的 import 行"：
        # 将新内容逐行与旧行集合比对（简易且不误伤同文件内部移动）。
        old_line_set = {ln.strip() for ln in old_lines}
        added_import_lines = [
            ln.strip() for ln in (new_content or "").splitlines()
            if ln.strip() and _IMPORT_ADDED_RE.match(ln.strip())
            and ln.strip() not in old_line_set
        ]
        if added_imports or added_import_lines:
            raise EditTypeNotAllowedError(
                f"禁止新增导入语句（执行逻辑核心）: "
                f"{', '.join(added_import_lines[:5]) or next(iter(added_imports), '')}",
                code="EDIT_IMPORT_BLOCKED",
                details={"file_path": file_path,
                         "added_imports": list(added_import_lines)[:10]},
            )
        if new_has_deps and not old_has_deps:
            raise EditTypeNotAllowedError(
                "禁止新增 dependencies 依赖声明（执行逻辑核心）",
                code="EDIT_DEPS_BLOCKED",
                details={"file_path": file_path},
            )

    def validate_content(self, new_content: str, file_path: str = "") -> None:
        """内容黑名单扫描（危险代码 / 注入痕迹）

        Raises:
            EditContentBlockedError: 命中黑名单模式
        """
        for pattern in self._blocked_patterns:
            m = pattern.search(new_content or "")
            if m:
                raise EditContentBlockedError(
                    f"新内容命中黑名单模式: {pattern.pattern!r} @ {m.start()}",
                    details={"file_path": file_path,
                             "pattern": pattern.pattern,
                             "offset": m.start()},
                )

    def validate_proposal(self, proposal: EditProposal) -> None:
        """提案整体校验（路径 + 类型 + 范围 + 内容 + 文件数）

        Raises:
            EditPolicyError 子类: 任一维度越界
        """
        if not proposal.object_id:
            raise EditPolicyError("提案缺少 object_id（技能 ID）")
        if len(proposal.files) > self._max_files_per_round:
            raise EditPolicyError(
                f"提案文件数 {len(proposal.files)} 超过单轮上限 "
                f"{self._max_files_per_round}",
                code="EDIT_MAX_FILES_EXCEEDED",
                details={"files": len(proposal.files),
                         "max": self._max_files_per_round},
            )
        self.validate_edit_type(proposal.edit_type)
        for f in proposal.files:
            self.validate_file_path(f.file_path)
            self.validate_scope(f.old_content, f.new_content, f.file_path)
            self.validate_content(f.new_content, f.file_path)

    # ─── 内部 ───

    @staticmethod
    def _is_within(target: Path, base: Path) -> bool:
        try:
            target.relative_to(base)
            return True
        except ValueError:
            return False

    def _assert_not_core(self, resolved: Path) -> None:
        """第二层防线：核心目录与策略文件永久禁止（不受白名单配置影响）"""
        for rel in _FORBIDDEN_REL_DIRS:
            core = (self._project_root / rel).resolve()
            if self._is_within(resolved, core):
                raise PathNotAllowedError(
                    f"系统核心目录禁止编辑: {rel}/",
                    details={"path": str(resolved), "forbidden": rel},
                )
