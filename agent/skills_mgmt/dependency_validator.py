"""依赖校验与合并 — 处理技能依赖的版本冲突检测

提供:
    - Dependency: 依赖数据类 (name, version_spec, optional)
    - DependencyConflict: 冲突信息 (name, reason, dst_spec, src_spec)
    - DependencyConflictError: 多冲突异常 (聚合所有冲突一次性抛出)
    - detect_conflicts: 检测两个依赖列表间的冲突
    - merge_dependencies: 按策略合并两个依赖列表 (prefer_a / prefer_b / union)

冲突分类:
    - no_version_overlap: 硬冲突 — 两个版本区间完全不重叠 (e.g., >=1.0 vs <1.0)
    - spec_diff_but_intersect: 弱冲突 — 版本区间有交集但不相同 (e.g., >=1.0 vs >=1.5)

策略:
    - prefer_a: 冲突时保留 deps_a 的版本 (默认, store.py 使用)
    - prefer_b: 冲突时保留 deps_b 的版本
    - union: 简单 set union, 不做版本选择 (用于无版本约束场景)

兼容性:
    - 输入支持 str ("requests") 或 dict ({"name": "openai", "version_spec": ">=1.0"})
    - 可选依赖 (optional=True) 不产生硬冲突
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Union

logger = logging.getLogger(__name__)

try:
    from packaging.specifiers import SpecifierSet, InvalidSpecifier
    from packaging.version import Version, InvalidVersion
    _HAS_PACKAGING = True
except ImportError:
    _HAS_PACKAGING = False
    SpecifierSet = None  # type: ignore
    InvalidSpecifier = Exception  # type: ignore
    Version = None  # type: ignore
    InvalidVersion = Exception  # type: ignore


# 解析 "name>=1.0,<2.0" 形式的字符串 — 提取 name 和 version_spec
# 支持 PEP 508 子集: name + version_specifiers (不含 extras / markers / url)
_NAME_VERSION_RE = re.compile(
    r"""^\s*
    (?P<name>[A-Za-z0-9][A-Za-z0-9._\-]*)       # 包名
    \s*
    (?P<version_spec>[<>=!~][^;\s\[]*)?          # 版本规范 (>=1.0,<2.0 等)
    \s*$""",
    re.VERBOSE,
)


# ──────────────────────────────────────────────
# 数据类
# ──────────────────────────────────────────────

@dataclass
class Dependency:
    """依赖项 — 名称 + 版本规范 + 可选性

    Attributes:
        name: 依赖名称 (如 "requests", "openai")
        version_spec: 版本规范 (如 "*", ">=1.0,<2.0", "==1.2.3")
        optional: 是否可选依赖 (True → 不视为硬冲突)
    """
    name: str
    version_spec: str = "*"
    optional: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version_spec": self.version_spec,
            "optional": self.optional,
        }

    def __str__(self) -> str:
        return self.name


@dataclass
class DependencyConflict:
    """依赖冲突信息

    Attributes:
        name: 冲突的依赖名称
        reason: 冲突原因
            - "no_version_overlap": 硬冲突 (版本区间无交集)
            - "spec_diff_but_intersect": 弱冲突 (版本区间有交集但不相同)
        dst_spec: deps_a 中的版本规范
        src_spec: deps_b 中的版本规范
    """
    name: str
    reason: str
    dst_spec: str
    src_spec: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "reason": self.reason,
            "dst_spec": self.dst_spec,
            "src_spec": self.src_spec,
        }


class DependencyConflictError(Exception):
    """依赖冲突异常 — 聚合所有硬冲突

    merge_dependencies 在 strategy="union" 之外不应抛出此异常
    (prefer_a / prefer_b 会自动选择保留方, 不抛异常)
    """

    def __init__(self, conflicts: List[DependencyConflict]):
        self.conflicts = conflicts
        names = ", ".join(c.name for c in conflicts)
        super().__init__(
            f"Dependency conflicts detected for: {names} "
            f"({len(conflicts)} hard conflicts)"
        )


# ──────────────────────────────────────────────
# 解析与版本规范判断
# ──────────────────────────────────────────────

def _parse_dep(raw: Union[str, Dict[str, Any], Dependency]) -> Dependency:
    """解析单个依赖项 — 支持 str / dict / Dependency

    str 形式支持 PEP 508 子集:
        "requests"            → name="requests", version_spec="*"
        "requests>=1.0"       → name="requests", version_spec=">=1.0"
        "requests>=1.0,<2.0"  → name="requests", version_spec=">=1.0,<2.0"
        "requests==1.2.3"     → name="requests", version_spec="==1.2.3"
    dict: {"name": "openai", "version_spec": ">=1.0", "optional": False}
    """
    if isinstance(raw, Dependency):
        return raw
    if isinstance(raw, str):
        match = _NAME_VERSION_RE.match(raw.strip())
        if match:
            name = match.group("name")
            version_spec = (match.group("version_spec") or "").strip() or "*"
            return Dependency(name=name, version_spec=version_spec, optional=False)
        # 解析失败: 退化为整串当 name
        return Dependency(name=raw, version_spec="*", optional=False)
    if isinstance(raw, dict):
        return Dependency(
            name=raw.get("name", ""),
            version_spec=raw.get("version_spec", raw.get("version", "*")),
            optional=raw.get("optional", False),
        )
    # 兜底: 转字符串
    return Dependency(name=str(raw), version_spec="*", optional=False)


def _check_overlap_by_bounds(spec_a: str, spec_b: str) -> Tuple[bool, bool]:
    """通过解析 lower/upper bound 近似判断交集

    解析规范如 '>=1.0,<2.0' → lower=1.0, upper=2.0
    若两区间的 [lower, upper] 重叠 → 相交

    Returns:
        (has_overlap, is_identical)
        - has_overlap=False → 硬冲突 (no_version_overlap)
        - has_overlap=True, is_identical=False → 弱冲突 (spec_diff_but_intersect)
        - has_overlap=True, is_identical=True → 无冲突
    """
    def parse_bounds(spec: str) -> Tuple[Any, Any, bool, bool]:
        """返回 (lower, upper, lower_inclusive, upper_inclusive)
        None 表示无界
        """
        if not _HAS_PACKAGING or not spec:
            return None, None, True, True
        lower = None
        upper = None
        lower_incl = True
        upper_incl = True
        for part in spec.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                if part.startswith(">="):
                    v = Version(part[2:])
                    if lower is None or v > lower:
                        lower = v
                        lower_incl = True
                elif part.startswith(">"):
                    v = Version(part[1:])
                    if lower is None or v > lower:
                        lower = v
                        lower_incl = False
                elif part.startswith("<="):
                    v = Version(part[2:])
                    if upper is None or v < upper:
                        upper = v
                        upper_incl = True
                elif part.startswith("<"):
                    v = Version(part[1:])
                    if upper is None or v < upper:
                        upper = v
                        upper_incl = False
                elif part.startswith("=="):
                    v = Version(part[2:])
                    lower = v
                    upper = v
                    lower_incl = True
                    upper_incl = True
                else:
                    # 裸版本号 → 精确匹配
                    v = Version(part)
                    lower = v
                    upper = v
                    lower_incl = True
                    upper_incl = True
            except InvalidVersion:
                continue
        return lower, upper, lower_incl, upper_incl

    la, ua, la_incl, ua_incl = parse_bounds(spec_a)
    lb, ub, lb_incl, ub_incl = parse_bounds(spec_b)

    # 不相交判定: ua < lb 或 ub < la
    if ua is not None and lb is not None:
        # ua vs lb: 必须 ua >= lb (含相等需 incl 都为 True)
        cmp = (ua > lb) or (ua == lb and ua_incl and lb_incl)
        if not cmp:
            return False, False
    if ub is not None and la is not None:
        cmp = (ub > la) or (ub == la and ub_incl and la_incl)
        if not cmp:
            return False, False
    return True, False


def _specs_intersect(spec_a: str, spec_b: str) -> Tuple[bool, bool]:
    """检查两个版本规范的交集关系

    Returns:
        (has_overlap, is_identical)
        - has_overlap=False → 硬冲突 (no_version_overlap)
        - has_overlap=True, is_identical=False → 弱冲突 (spec_diff_but_intersect)
        - has_overlap=True, is_identical=True → 无冲突
    """
    # 通配符规则
    if spec_a == "*" or spec_b == "*":
        # 任一为 * → 总是相交, 但若两者不同则是弱冲突
        return True, (spec_a == spec_b)
    if spec_a == spec_b:
        return True, True

    # 都非 *, 用 packaging 解析 bounds 判断
    if not _HAS_PACKAGING:
        # 无 packaging 库, 无法精确判断 → 视为弱冲突 (保守)
        return True, False
    try:
        return _check_overlap_by_bounds(spec_a, spec_b)
    except (InvalidSpecifier, Exception) as e:
        logger.debug(
            "[DepValidator] 解析版本规范失败: spec_a=%s spec_b=%s err=%s",
            spec_a, spec_b, e,
        )
        # 解析失败 → 保守视为相交 (避免误报硬冲突阻断合并)
        return True, False


# ──────────────────────────────────────────────
# 冲突检测
# ──────────────────────────────────────────────

def detect_conflicts(deps_a: List[Union[str, Dict[str, Any], Dependency]],
                     deps_b: List[Union[str, Dict[str, Any], Dependency]],
                     *,
                     allow_optional: bool = True) -> List[DependencyConflict]:
    """检测两个依赖列表间的冲突

    Args:
        deps_a: dst 依赖列表 (str / dict / Dependency 混合)
        deps_b: src 依赖列表
        allow_optional: True → 任一方 optional=True 的依赖不产生硬冲突

    Returns:
        冲突列表 — 每个冲突包含 name, reason, dst_spec, src_spec
    """
    parsed_a = {d.name: d for d in (_parse_dep(x) for x in deps_a)}
    parsed_b = {d.name: d for d in (_parse_dep(x) for x in deps_b)}
    conflicts: List[DependencyConflict] = []

    for name, dep_a in parsed_a.items():
        if name not in parsed_b:
            continue
        dep_b = parsed_b[name]

        # 可选依赖跳过硬冲突
        if allow_optional and (dep_a.optional or dep_b.optional):
            continue

        # 同版本 → 无冲突
        if dep_a.version_spec == dep_b.version_spec:
            continue

        # 任一是 * → 弱冲突 (有交集但版本约束不同)
        if dep_a.version_spec == "*" or dep_b.version_spec == "*":
            conflicts.append(DependencyConflict(
                name=name,
                reason="spec_diff_but_intersect",
                dst_spec=dep_a.version_spec,
                src_spec=dep_b.version_spec,
            ))
            continue

        # 检查交集
        has_overlap, is_identical = _specs_intersect(
            dep_a.version_spec, dep_b.version_spec,
        )
        if not has_overlap:
            conflicts.append(DependencyConflict(
                name=name,
                reason="no_version_overlap",
                dst_spec=dep_a.version_spec,
                src_spec=dep_b.version_spec,
            ))
        elif not is_identical:
            conflicts.append(DependencyConflict(
                name=name,
                reason="spec_diff_but_intersect",
                dst_spec=dep_a.version_spec,
                src_spec=dep_b.version_spec,
            ))

    if conflicts:
        logger.info(
            "[DepValidator] detect_conflicts | a=%d b=%d | "
            "conflicts=%d (hard=%d weak=%d)",
            len(parsed_a), len(parsed_b), len(conflicts),
            sum(1 for c in conflicts if c.reason == "no_version_overlap"),
            sum(1 for c in conflicts if c.reason == "spec_diff_but_intersect"),
        )
    return conflicts


# ──────────────────────────────────────────────
# 合并
# ──────────────────────────────────────────────

def merge_dependencies(deps_a: List[Union[str, Dict[str, Any], Dependency]],
                       deps_b: List[Union[str, Dict[str, Any], Dependency]],
                       *,
                       strategy: str = "prefer_a",
                       allow_optional: bool = True) -> List[Dependency]:
    """按策略合并两个依赖列表

    Args:
        deps_a: dst 依赖列表
        deps_b: src 依赖列表
        strategy: 合并策略
            - "prefer_a": 冲突时保留 deps_a 版本 (默认, store.py 使用)
            - "prefer_b": 冲突时保留 deps_b 版本
            - "union": 简单 set union, 同名保留 deps_a 版本
        allow_optional: optional 依赖不视为硬冲突

    Returns:
        合并后的 Dependency 列表 (按 name 字典序排序)
    """
    if strategy not in ("prefer_a", "prefer_b", "union"):
        raise ValueError(
            f"Unknown merge strategy: {strategy} "
            f"(expected: prefer_a / prefer_b / union)"
        )

    parsed_a = [_parse_dep(x) for x in deps_a]
    parsed_b = [_parse_dep(x) for x in deps_b]
    by_name: Dict[str, Dependency] = {}

    if strategy == "union":
        # 简单 union, 同名时保留 deps_a 的版本
        for d in parsed_a:
            by_name[d.name] = d
        for d in parsed_b:
            if d.name not in by_name:
                by_name[d.name] = d
        logger.info(
            "[DepValidator] merge (union) | a=%d b=%d → merged=%d",
            len(parsed_a), len(parsed_b), len(by_name),
        )
        return sorted(by_name.values(), key=lambda x: x.name)

    # prefer_a / prefer_b 策略
    prefer_src = strategy == "prefer_b"
    conflicts = detect_conflicts(
        deps_a, deps_b, allow_optional=allow_optional,
    )
    hard_conflicts = {c.name for c in conflicts
                      if c.reason == "no_version_overlap"}
    weak_conflicts = {c.name for c in conflicts
                      if c.reason == "spec_diff_but_intersect"}

    # 默认填充: 先放 deps_a, 再处理 deps_b 的覆盖/新增
    for d in parsed_a:
        by_name[d.name] = d
    for d in parsed_b:
        if d.name not in by_name:
            by_name[d.name] = d
            continue
        existing = by_name[d.name]
        # 完全相同 → 跳过
        if (existing.version_spec == d.version_spec
                and existing.optional == d.optional):
            continue
        # 硬冲突 → 按 prefer_a/b 选择
        if d.name in hard_conflicts:
            if prefer_src:
                by_name[d.name] = d
                logger.info(
                    "[DepValidator] 硬冲突 → 保留 src 版本 | "
                    "name=%s | dst=%s | src=%s",
                    d.name, existing.version_spec, d.version_spec,
                )
            else:
                logger.info(
                    "[DepValidator] 硬冲突 → 保留 dst 版本 | "
                    "name=%s | dst=%s | src=%s",
                    d.name, existing.version_spec, d.version_spec,
                )
            continue
        # 弱冲突 → 按 prefer_a/b 选择 (仅日志)
        if d.name in weak_conflicts:
            if prefer_src:
                by_name[d.name] = d
                logger.info(
                    "[DepValidator] 弱冲突 → 采用 src 版本 | "
                    "name=%s | dst=%s | src=%s",
                    d.name, existing.version_spec, d.version_spec,
                )
            else:
                logger.info(
                    "[DepValidator] 弱冲突 → 保留 dst 版本 | "
                    "name=%s | dst=%s | src=%s",
                    d.name, existing.version_spec, d.version_spec,
                )
            continue
        # 非冲突但版本不同 (e.g., "* "vs ">=1.0" 已在 weak_conflicts 处理)
        # 兜底: prefer_b 用 src, prefer_a 用 dst (no-op)
        if prefer_src:
            by_name[d.name] = d

    logger.info(
        "[DepValidator] merge (%s) | a=%d b=%d → merged=%d | "
        "hard=%d weak=%d",
        strategy, len(parsed_a), len(parsed_b), len(by_name),
        len(hard_conflicts), len(weak_conflicts),
    )
    return sorted(by_name.values(), key=lambda x: x.name)
