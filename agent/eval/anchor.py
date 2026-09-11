"""L0 锚存储：**哈希锚定 + 系统不可写**（TASK-S5-02 / v7.2 §6.5）

L0 锚（20 条人工冻结用例）是打破"自验收循环"的客观标尺，因此它的**可信度**来自
三个互相独立的机制，缺一不可：

1. **位置独立**：锚目录默认 ``<repo>/eval/l0_anchor``，**不在任何系统数据目录下**
   （`data/`、`data/events/`、`data/digestion/`、`data/reflection/`、
   `data/feedback/`、`data/shadow/` …）。`assert_independent_of_system_data()`
   在加载时机器校验该不变量（双向：锚不得落在系统目录内，系统目录也不得落在锚内）。
2. **无写 API + 写入守门**：`AnchorStore` **不提供**任何修改用例的方法；`write_*`
   系列方法一律抛 `AnchorReadOnlyError`（这就是"扰动尝试被拒"的可测证据），
   `guard_write(path)` 供任何落盘路径调用以拒绝写入锚目录。
3. **哈希锚定 + fail-closed 校验**：`manifest.json` 记录逐条用例哈希、用例集整体
   哈希与参考解哈希；每次加载都重算比对，**任一不一致/缺失/多余即拒绝运行**
   （`AnchorIntegrityError`），绝不在锚被改动时给出"通过"的评测结论。

**诚实口径**：仓库工作区内的文件无法施加 OS 级只读（git 需要能改写工作区），
所以"系统不可写"在本任务实现为上述**逻辑不可写 + 哈希可验**；
发布包可选用 `set_os_readonly()` 施加文件属性级只读（默认关闭，见 `eval/README.md`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, NoReturn, Optional, Tuple

from agent.eval import cases as C

logger = logging.getLogger("agent.eval.anchor")

#: 锚目录可经环境变量覆盖（**只读用途**：打包/外置存储场景）
ENV_ANCHOR_DIR = "CP_EVAL_ANCHOR_DIR"

#: 默认锚目录：仓库内、**不在** `data/` 下
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_ANCHOR_DIR = os.path.join(_REPO_ROOT, "eval", "l0_anchor")

CASES_FILENAME = "cases.json"
MANIFEST_FILENAME = "manifest.json"
REFERENCE_FILENAME = "reference.json"

#: manifest schema（独立版本号：锚格式变更必须升版）
ANCHOR_SCHEMA = "eval.anchor.v1"
#: 冻结脚本版本（写入 manifest，便于追溯"谁按什么规则冻的"）
FREEZE_TOOL_VERSION = "s5-02.1"


class AnchorError(Exception):
    """锚层基类异常"""


class AnchorReadOnlyError(AnchorError):
    """对锚的写入尝试被拒（**系统不可写**：自动化流程无权修改）"""


class AnchorIntegrityError(AnchorError):
    """锚完整性校验失败（哈希不一致 / 缺失 / 多余）——fail-closed，拒绝评测"""


class AnchorIndependenceError(AnchorError):
    """锚目录与系统数据目录未隔离（违反"独立于系统数据目录"）"""


# ════════════════════════════════════════════════════════════
#  位置与哈希
# ════════════════════════════════════════════════════════════


def resolve_anchor_dir(root: str = "") -> str:
    """解析锚目录：显式参数 > ``CP_EVAL_ANCHOR_DIR`` > 默认 ``<repo>/eval/l0_anchor``"""
    explicit = str(root or "").strip()
    if explicit:
        return os.path.abspath(explicit)
    env = str(os.getenv(ENV_ANCHOR_DIR) or "").strip()
    if env:
        return os.path.abspath(env)
    return os.path.abspath(DEFAULT_ANCHOR_DIR)


def file_sha256(path: str) -> str:
    """文件内容哈希（缺失/不可读 → 空串，调用方据此判断"存在性"）"""
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except OSError:
        return ""


def text_sha256(path: str) -> str:
    """文本内容哈希：**行尾归一化为 LF** 后取哈希（锚对象的跨平台哈希口径）

    为什么不用原始字节：本仓库 `core.autocrlf=true`，同一份 JSON 在 Windows
    checkout 为 CRLF、在 Linux/CI 为 LF —— 用原始字节会让"参考解被改动"的校验
    在跨平台场景下**假阳性**（fail-closed 直接拒绝评测）。锚是**文本**对象，
    故以归一化文本为哈希口径；用例集侧的哈希本就走 `canonical_json`（与行尾无关），
    两者口径一致。
    """
    try:
        with open(path, "r", encoding="utf-8", newline=None) as fh:
            text = fh.read()
    except OSError:
        return ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def is_within(path: str, root: str) -> bool:
    """`path` 是否位于 `root` 之内（大小写不敏感前缀比较，跨平台可用）"""
    if not path or not root:
        return False
    try:
        target = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root))
    except (TypeError, ValueError):
        return False
    return target == base or target.startswith(base.rstrip(os.sep) + os.sep)


def system_data_roots() -> Dict[str, str]:
    """系统数据目录清单（锚必须与它们全部独立）

    惰性读取各子系统的**默认**落点（不触发建目录：只解析路径字符串）：
    ``data/``（总根）、``data/events/``（事件流）、``data/digestion/``（判定集/通行证）、
    ``data/reflection/``（反思）、``data/feedback/``（用户反馈）、
    ``data/eval/``（本任务的运行期产物：基线/拟合件，**与锚分离**）。
    """
    data_root = os.path.join(_REPO_ROOT, "data")
    return {
        "data_root": data_root,
        "events": os.path.join(data_root, "events"),
        "digestion": os.path.join(data_root, "digestion"),
        "reflection": os.path.join(data_root, "reflection"),
        "feedback": os.path.join(data_root, "feedback"),
        "eval_artifacts": os.path.join(data_root, "eval"),
    }


def assert_independent_of_system_data(root: str = "") -> Dict[str, Any]:
    """校验锚目录与系统数据目录**互相独立**（双向包含都算违规）

    Returns:
        ``{"anchor": ..., "roots": {...}, "independent": bool, "violations": [...]}``

    Raises:
        AnchorIndependenceError: 存在包含关系（fail-closed：宁可不评测）。
    """
    anchor = resolve_anchor_dir(root)
    roots = system_data_roots()
    violations: List[str] = []
    for name, path in roots.items():
        if is_within(anchor, path):
            violations.append(f"锚目录位于系统数据目录 {name} 内: {anchor} ⊂ {path}")
        if is_within(path, anchor):
            violations.append(f"系统数据目录 {name} 位于锚目录内: {path} ⊂ {anchor}")
    report = {"anchor": anchor, "roots": roots, "independent": not violations,
              "violations": violations}
    if violations:
        raise AnchorIndependenceError("; ".join(violations))
    return report


def is_anchor_path(path: str, root: str = "") -> bool:
    """该路径是否落在锚目录内（写入守门的判定核心）"""
    return is_within(path, resolve_anchor_dir(root))


def guard_write(path: str, root: str = "") -> None:
    """写入守门：任何落盘路径若指向锚目录 → `AnchorReadOnlyError`

    自动化流程（消化流水线 / 显影 / 自愈 / 评测执行器）在写出产物前调用本函数；
    它只做一件事：**拒绝**。
    """
    if is_anchor_path(path, root):
        raise AnchorReadOnlyError(
            f"锚目录为系统不可写（L0 冻结）：拒绝写入 {path}"
            f"（锚根 {resolve_anchor_dir(root)}）")


# ════════════════════════════════════════════════════════════
#  manifest
# ════════════════════════════════════════════════════════════


@dataclass
class AnchorManifest:
    """锚清单（逐条哈希 + 用例集整体哈希 + 参考解哈希）"""

    layer: str = C.LAYER_L0
    frozen_at: str = ""
    frozen_by: str = ""
    review_note: str = ""
    count: int = 0
    caseset_sha256: str = ""
    entries: Dict[str, str] = field(default_factory=dict)
    reference_sha256: str = ""
    schema: str = ANCHOR_SCHEMA
    tool_version: str = FREEZE_TOOL_VERSION
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema, "layer": self.layer,
            "frozen_at": self.frozen_at, "frozen_by": self.frozen_by,
            "review_note": self.review_note, "count": self.count,
            "caseset_sha256": self.caseset_sha256,
            "reference_sha256": self.reference_sha256,
            "tool_version": self.tool_version,
            "entries": dict(sorted(self.entries.items())),
            "meta": dict(self.meta),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorManifest":
        if not isinstance(data, Mapping):
            raise AnchorError("manifest 根必须是对象")
        schema = str(data.get("schema") or "")
        if schema and schema != ANCHOR_SCHEMA:
            raise AnchorError(f"未知 manifest schema: {schema!r}（期望 {ANCHOR_SCHEMA}）")
        entries = data.get("entries")
        if entries is None:
            entries = {}
        if not isinstance(entries, Mapping):
            raise AnchorError(
                f"manifest.entries 必须是对象，得到 {type(entries).__name__}")
        return cls(
            layer=str(data.get("layer") or C.LAYER_L0),
            frozen_at=str(data.get("frozen_at") or ""),
            frozen_by=str(data.get("frozen_by") or ""),
            review_note=str(data.get("review_note") or ""),
            count=int(data.get("count") or 0),
            caseset_sha256=str(data.get("caseset_sha256") or ""),
            entries={str(k): str(v) for k, v in entries.items()},
            reference_sha256=str(data.get("reference_sha256") or ""),
            schema=schema or ANCHOR_SCHEMA,
            tool_version=str(data.get("tool_version") or FREEZE_TOOL_VERSION),
            meta=dict(data.get("meta") or {}),
        )


def load_manifest(path: str) -> AnchorManifest:
    """读 manifest（缺失/非法 → `AnchorError`）"""
    if not os.path.exists(path):
        raise AnchorError(f"锚 manifest 不存在: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, ValueError) as e:
        raise AnchorError(f"锚 manifest JSON 非法（{path}）: {e}") from e
    return AnchorManifest.from_dict(data)


def _write_json_atomic(path: str, payload: Mapping[str, Any]) -> str:
    """原子写 JSON（**只有冻结脚本会走到这里**，见 `freeze_anchor`）"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return text


# ════════════════════════════════════════════════════════════
#  完整性校验（fail-closed）
# ════════════════════════════════════════════════════════════


def verify_anchor(root: str = "", *, store: Optional["AnchorStore"] = None) -> Dict[str, Any]:
    """校验锚完整性 → 结构化报告（**不抛异常**，供脚本/报告展示）

    报告字段：
      ``ok``（全绿才为真）、``caseset_sha256``（重算）、``manifest_caseset_sha256``、
      ``mismatched``（逐条哈希不一致）、``missing``（清单有而文件无）、
      ``unexpected``（文件有而清单无）、``count``/``manifest_count``、
      ``reference``（参考解哈希校验）、``independent``（位置独立性）。
    """
    target = store or AnchorStore(root)
    report: Dict[str, Any] = {
        "anchor_dir": target.root, "ok": False, "problems": [],
        "mismatched": [], "missing": [], "unexpected": [],
        "count": 0, "manifest_count": 0,
        "caseset_sha256": "", "manifest_caseset_sha256": "",
        "reference": {}, "independent": {},
    }
    try:
        report["independent"] = assert_independent_of_system_data(target.root)
    except AnchorIndependenceError as e:
        report["problems"].append(f"位置独立性失败: {e}")
        report["independent"] = {"independent": False, "violations": [str(e)]}
        return report

    if not os.path.exists(target.manifest_path):
        report["problems"].append(f"manifest 缺失: {target.manifest_path}")
        return report
    try:
        manifest = load_manifest(target.manifest_path)
    except AnchorError as e:
        report["problems"].append(f"manifest 不可读: {e}")
        return report
    report["manifest_count"] = manifest.count
    report["manifest_caseset_sha256"] = manifest.caseset_sha256

    try:
        case_set = target.load(verify=False, validate_contract=False)
    except C.CaseError as e:
        report["problems"].append(f"用例集不可读: {e}")
        return report
    report["count"] = len(case_set.cases)

    recomputed = dict(C.caseset_manifest_entries(case_set.cases))
    report["caseset_sha256"] = case_set.caseset_sha256
    for case_id, digest in sorted(recomputed.items()):
        want = manifest.entries.get(case_id)
        if want is None:
            report["unexpected"].append(case_id)
        elif want != digest:
            report["mismatched"].append({"id": case_id, "manifest": want, "actual": digest})
    for case_id in sorted(set(manifest.entries) - set(recomputed)):
        report["missing"].append(case_id)

    if manifest.caseset_sha256 != case_set.caseset_sha256:
        report["problems"].append(
            "用例集整体哈希不一致（manifest="
            f"{manifest.caseset_sha256 or '(空)'} actual={case_set.caseset_sha256}）")
    if manifest.count and manifest.count != len(case_set.cases):
        report["problems"].append(
            f"条数不一致（manifest={manifest.count} actual={len(case_set.cases)}）")
    if report["mismatched"]:
        report["problems"].append(f"逐条哈希不一致 {len(report['mismatched'])} 条")
    if report["missing"]:
        report["problems"].append(f"清单内用例缺失 {len(report['missing'])} 条")
    if report["unexpected"]:
        report["problems"].append(f"出现清单外新增用例 {len(report['unexpected'])} 条")

    ref_path = target.reference_path
    ref_digest = text_sha256(ref_path)
    report["reference"] = {
        "path": ref_path, "exists": os.path.exists(ref_path),
        "sha256": ref_digest, "manifest_sha256": manifest.reference_sha256,
        "ok": bool(ref_digest) and ref_digest == manifest.reference_sha256,
    }
    if manifest.reference_sha256 and not report["reference"]["ok"]:
        report["problems"].append("参考解哈希不一致（reference.json 被改动）")

    report["manifest"] = manifest.to_dict()
    report["ok"] = not report["problems"]
    return report


def readonly_status(root: str = "") -> Dict[str, Any]:
    """锚目录的 OS 级只读状态（**仅供参考**：仓库工作区一般不是只读）"""
    anchor = resolve_anchor_dir(root)
    files: List[Dict[str, Any]] = []
    for name in (CASES_FILENAME, MANIFEST_FILENAME, REFERENCE_FILENAME):
        path = os.path.join(anchor, name)
        exists = os.path.exists(path)
        writable = os.access(path, os.W_OK) if exists else False
        files.append({"file": name, "exists": exists, "os_writable": bool(writable)})
    return {
        "anchor_dir": anchor, "files": files,
        "note": ("仓库工作区无 OS 级只读保证；本任务的「系统不可写」由"
                 "①无写 API ②guard_write 守门 ③哈希锚定 fail-closed 三层实现"),
    }


def set_os_readonly(root: str = "", enable: bool = True) -> Dict[str, Any]:
    """对锚文件施加/解除 OS 级只读属性（发布包硬化用；posix 下用 chmod）

    **默认不启用**：仓库工作区需要 git 可改写，启用会阻塞正常提交。
    """
    anchor = resolve_anchor_dir(root)
    changed: List[Dict[str, Any]] = []
    for name in (CASES_FILENAME, MANIFEST_FILENAME, REFERENCE_FILENAME):
        path = os.path.join(anchor, name)
        if not os.path.exists(path):
            continue
        try:
            if enable:
                os.chmod(path, 0o444)
            else:
                os.chmod(path, 0o644)
            changed.append({"file": name, "readonly": bool(enable)})
        except OSError as e:
            changed.append({"file": name, "error": str(e)})
    return {"anchor_dir": anchor, "enable": bool(enable), "changed": changed}


# ════════════════════════════════════════════════════════════
#  锚存储（只读）
# ════════════════════════════════════════════════════════════


class AnchorStore:
    """L0 锚的**只读**访问器

    不提供任何写用例的方法；`write_cases` / `update_case` / `delete_case` /
    `write_reference` 均为**显式拒绝**（供"扰动尝试被拒"的验收用例调用）。
    """

    def __init__(self, root: str = "") -> None:
        self.root = resolve_anchor_dir(root)

    # ── 路径 ────────────────────────────────────────────────

    @property
    def cases_path(self) -> str:
        return os.path.join(self.root, CASES_FILENAME)

    @property
    def manifest_path(self) -> str:
        return os.path.join(self.root, MANIFEST_FILENAME)

    @property
    def reference_path(self) -> str:
        return os.path.join(self.root, REFERENCE_FILENAME)

    def path_of(self, name: str) -> str:
        return os.path.join(self.root, name)

    # ── 读 ──────────────────────────────────────────────────

    def load(self, *, verify: bool = True, validate_contract: bool = True) -> C.EvalCaseSet:
        """加载锚用例集；``verify=True`` 时**先校验完整性**（不一致即拒绝）

        Args:
            verify: 是否做哈希锚定完整性校验（评测入口必须为 True）。
            validate_contract: 是否校验"规模与场景覆盖契约"；`verify_anchor()`
                需要**只做结构读取**以便精确报告"少了哪条/多了哪条"，故传 False。

        Raises:
            AnchorIndependenceError: 锚与系统数据目录未隔离。
            AnchorIntegrityError: 哈希/条数/参考解任一不一致。
            C.CaseSetError: 用例自身不满足规模与覆盖契约。
        """
        assert_independent_of_system_data(self.root)
        if verify:
            report = verify_anchor(self.root, store=self)
            if not report["ok"]:
                raise AnchorIntegrityError(
                    "L0 锚完整性校验失败（fail-closed，拒绝评测）: "
                    + "; ".join(report["problems"]))
        case_set = C.load_case_set(self.cases_path, validate=False)
        if validate_contract:
            errors = C.validate_case_set(case_set, require_layer_size=True)
            if errors:
                raise C.CaseSetError("L0 锚用例集校验失败: " + "; ".join(errors))
        case_set.frozen = True
        return case_set

    def load_reference(self) -> Dict[str, Dict[str, Any]]:
        """加载参考解（``{case_id: 答案工件}``；缺失 → 空字典并告警）

        参考解只用于两件事：① 校验判定器本身可用（"管道自检"）；
        ② 生成"变异解"以证明判定器**有区分度**。它**不代表模型能力**。
        """
        if not os.path.exists(self.reference_path):
            logger.warning("锚参考解缺失: %s", self.reference_path)
            return {}
        try:
            with open(self.reference_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, ValueError) as e:
            raise AnchorError(f"参考解 JSON 非法（{self.reference_path}）: {e}") from e
        if not isinstance(data, Mapping):
            raise AnchorError("参考解根必须是对象")
        raw_answers: Any = data.get("answers")
        if not isinstance(raw_answers, Mapping):
            raw_answers = data
        return {str(k): dict(v) if isinstance(v, Mapping) else {"value": v}
                for k, v in raw_answers.items()}

    def manifest(self) -> AnchorManifest:
        return load_manifest(self.manifest_path)

    def integrity(self) -> Dict[str, Any]:
        return verify_anchor(self.root, store=self)

    # ── 写：**一律拒绝**（系统不可写） ──────────────────────

    def write_cases(self, case_set: Any = None) -> NoReturn:
        raise AnchorReadOnlyError(
            "L0 锚为系统不可写：自动化流程无权写入用例集"
            f"（{self.cases_path}）；冻结须人工走 scripts/freeze_eval_anchor.py")

    def update_case(self, case_id: str = "", patch: Any = None) -> NoReturn:
        raise AnchorReadOnlyError(
            f"L0 锚为系统不可写：拒绝修改用例 {case_id!r}（{self.cases_path}）")

    def delete_case(self, case_id: str = "") -> NoReturn:
        raise AnchorReadOnlyError(
            f"L0 锚为系统不可写：拒绝删除用例 {case_id!r}（{self.cases_path}）")

    def write_reference(self, answers: Any = None) -> NoReturn:
        raise AnchorReadOnlyError(
            f"L0 锚为系统不可写：拒绝写入参考解（{self.reference_path}）")

    def write_manifest(self, manifest: Any = None) -> NoReturn:
        raise AnchorReadOnlyError(
            f"L0 锚为系统不可写：拒绝写入 manifest（{self.manifest_path}）")


# ════════════════════════════════════════════════════════════
#  冻结（唯一被许可的写入路径：人工 + 显式能力）
# ════════════════════════════════════════════════════════════


def freeze_anchor(*, case_set: C.EvalCaseSet,
                  reference: Mapping[str, Mapping[str, Any]],
                  frozen_by: str, review_note: str = "", root: str = "",
                  cases_filename: str = CASES_FILENAME,
                  reference_filename: str = REFERENCE_FILENAME,
                  allow_write: bool = False,
                  now: Optional[str] = None) -> AnchorManifest:
    """人工冻结锚：写入用例集 + 参考解 + manifest（**唯一**被许可的写路径）

    写入需要**显式传入** ``allow_write=True``，而这个能力只由
    ``scripts/freeze_eval_anchor.py --confirm-freeze`` 提供（需人工执行并署名）。
    自动化流程调用的高层 API（`AnchorStore.load` / `run_l0`）永远不传该参数，
    因此它们**在结构上**无法改写锚。

    Args:
        frozen_by: 冻结人（人工署名，必填；空值直接拒绝）。
        review_note: 冻结说明（为何变更/依据）。
        allow_write: 显式写入能力（缺省 False → 抛 `AnchorReadOnlyError`）。

    Raises:
        AnchorReadOnlyError: 未显式授予写入能力，或 `frozen_by` 为空。
        AnchorIndependenceError: 锚目录与系统数据目录未隔离。
        C.CaseSetError: 用例集不满足契约（L0 = 20 条、6 类场景每类 ≥2、机械可验）。
    """
    if not allow_write:
        raise AnchorReadOnlyError(
            "冻结需显式写入能力（allow_write=True，仅由人工冻结脚本提供）")
    if not str(frozen_by or "").strip():
        raise AnchorReadOnlyError("冻结需人工署名 frozen_by（自动化流程无权冻结）")
    errors = C.validate_case_set(case_set, require_layer_size=True)
    if errors:
        raise C.CaseSetError("拒绝冻结非法用例集: " + "; ".join(errors))
    missing_reference = [c.id for c in case_set.cases if c.id not in reference]
    if missing_reference:
        raise C.CaseSetError(f"参考解缺少用例: {missing_reference[:5]}")

    directory = resolve_anchor_dir(root)
    assert_independent_of_system_data(directory)
    os.makedirs(directory, exist_ok=True)
    # 位置自检：写入目标必须在锚根内（唯一被许可写入锚目录的路径就是这里；
    # `guard_write` 对所有**其他**调用方一律拒绝，本函数是它的显式豁免）
    if not is_within(os.path.join(directory, cases_filename), directory):
        raise AnchorError(f"冻结目标越出锚根: {cases_filename!r} ⊄ {directory}")
    cases_path = os.path.join(directory, cases_filename)
    reference_path = os.path.join(directory, reference_filename)

    frozen_set = C.EvalCaseSet(layer=case_set.layer, cases=case_set.cases,
                               path=cases_path, frozen=True, meta=dict(case_set.meta))
    _write_json_atomic(cases_path, frozen_set.to_dict())
    _write_json_atomic(reference_path, {
        "schema": f"{C.SCHEMA_NAME}#reference", "layer": case_set.layer,
        "note": ("参考解仅用于判定器自检与变异解对照，不代表任何模型能力；"
                 "真实评测须由被测解算器产出答案工件"),
        "answers": {str(k): dict(v) for k, v in sorted(reference.items())},
    })

    manifest = AnchorManifest(
        layer=case_set.layer,
        frozen_at=str(now or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
        frozen_by=str(frozen_by), review_note=str(review_note),
        count=len(frozen_set.cases),
        caseset_sha256=frozen_set.caseset_sha256,
        entries=C.caseset_manifest_entries(frozen_set.cases),
        reference_sha256=text_sha256(reference_path),
        meta={"scenario_counts": frozen_set.scenario_counts(),
              "verdict_counts": frozen_set.verdict_counts()},
    )
    _write_json_atomic(os.path.join(directory, MANIFEST_FILENAME), manifest.to_dict())
    return manifest


__all__ = [
    "ENV_ANCHOR_DIR", "DEFAULT_ANCHOR_DIR", "CASES_FILENAME", "MANIFEST_FILENAME",
    "REFERENCE_FILENAME", "ANCHOR_SCHEMA", "FREEZE_TOOL_VERSION",
    "AnchorError", "AnchorReadOnlyError", "AnchorIntegrityError",
    "AnchorIndependenceError", "AnchorManifest", "AnchorStore",
    "resolve_anchor_dir", "file_sha256", "text_sha256", "is_within",
    "system_data_roots",
    "assert_independent_of_system_data", "is_anchor_path", "guard_write",
    "load_manifest", "verify_anchor", "readonly_status", "set_os_readonly",
    "freeze_anchor",
]
