# -*- coding: utf-8 -*-
"""模块自动发现（2026-11-11）：让「新能力包」自己声明成拓扑节点。

【为什么需要】
`agent/modules_registry.py` 的 `DOMAINS` 是**手写静态清单**，docstring 只写
"新增模块只需向 DOMAINS 追加节点声明" —— 没有任何目录扫描/包发现。后果（实测）：
2026-10 之后新增的能力面（学习工作流 / 能力摄入 / 可观测配置 …）一个都没出现在
「系统监控」拓扑里，因为它们只是"没人往那张手写表里加行"，**没有任何机制会变红**。

【怎么做 —— 白名单式发现，不是"任何包都上图"】
包自己在 `agent/<pkg>/__init__.py` 里写模块级常量（只读声明，不执行任何代码）：

    MODULE_STATUS_SOURCE = "api:/api/workflow-learning/health"   # 必填才算已登记
    MODULE_DOMAIN        = "action"     # 六域之一（perception/cognitive/memory/action/service/ops）
    MODULE_NAME          = "学习工作流"  # 缺省用包名
    MODULE_DESCRIPTION   = "..."        # 一句话
    MODULE_METRICS       = ["a", "b"]   # 可选：从状态源响应里挑字段

三类结果（判据在 `scan()` 里，只有这三类）：

| 声明了什么 | 结果 | 上图 | 参与健康分 |
|---|---|---|---|
| `MODULE_STATUS_SOURCE`（合法域名） | **已登记节点** | ✓ | ✓（与手写节点同权） |
| 只有 `MODULE_NAME` / `MODULE_DESCRIPTION` | **未登记**（单列披露） | ✗ | ✗ |
| 两者都没有（utils / common / config …） | 不是模块候选，完全忽略 | ✗ | ✗ |

「未登记」= 声明了"我是个功能面"却没声明状态从哪取。此时**不生成节点**：没有状态源的
节点只能永远显示"离线"，那与事实不符；单列出来才能让人去补那一行（L5：无数据不是 0 分，
也不是"不存在"）。

【只做 AST 静态解析，绝不 import】
导入一个包 = 执行它的 `__init__.py` = 在注册表里引入启动期副作用与循环依赖
（本仓 arch_rules 会判高严重度）。`ast.parse` 只读文本，与 `scripts/audit/contract_diff.py`
同一套手法。

【开关】`CP_MODULE_AUTODISCOVERY`（默认开；置 0 完全回到"只有 DOMAINS 手写清单"的世界）。
默认开是安全的：白名单要求显式声明状态源，临时目录里丢一个包不会自己上图。

【新鲜度】结果进程内缓存（与 `DOMAINS` 同为"启动期常量"）：新增一个已声明的包需要重启
才上图；这与手写清单的行为一致，避免每次 /api/modules/topology 扫盘。
"""
from __future__ import annotations

import ast
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 开关名（必须与 agent/settings/registry.py 的登记一致）
ENV_FLAG = "CP_MODULE_AUTODISCOVERY"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})

#: 包目录里 `__init__.py` 的模块级声明前缀
DECL_PREFIX = "MODULE_"

#: 唯一"算数"的声明键：没有它就不是已登记节点
STATUS_SOURCE_KEY = "MODULE_STATUS_SOURCE"

#: 单位声明键（2026-11-12）：单个 dict 或 dict 列表，让包同时声明"我是个功能单位"
UNIT_KEY = "MODULE_UNIT"
UNITS_KEY = "MODULE_UNITS"
NAME_KEY = "MODULE_NAME"
DESCRIPTION_KEY = "MODULE_DESCRIPTION"
DOMAIN_KEY = "MODULE_DOMAIN"
METRICS_KEY = "MODULE_METRICS"

#: 节点 ID 前缀（与手写节点的 `域.名` 不冲突，且一眼看出是自动发现的）
MODULE_ID_PREFIX = "discovered."

#: 缺少 `MODULE_DOMAIN` 时落在服务层（最中性的六个域之一，不猜业务归属）
DEFAULT_DOMAIN_ID = "service"

#: 合法域名（与 modules_registry.DOMAINS 的 domain_id 一一对应；由测试钉住不许漂移）
DOMAIN_IDS: Tuple[str, ...] = (
    "perception", "cognitive", "memory", "action", "service", "ops",
)

#: 未登记原因（给人和前端看的一句实话）
REASON_NO_STATUS_SOURCE = "声明了模块元数据但未声明 MODULE_STATUS_SOURCE（无状态源 ⇒ 不生成节点、不参与健康分）"

#: 单位声明被忽略的原因（给人和前端看的一句实话）
REASON_UNIT_BAD_SHAPE = "MODULE_UNIT/MODULE_UNITS 必须含 id(str) 与 probe('模块:函数')，否则不登记"
REASON_UNIT_BAD_PROBE = "MODULE_UNIT.probe 必须是 '模块路径:函数名'（延迟 import，启动期不执行）"


def enabled() -> bool:
    """`CP_MODULE_AUTODISCOVERY` 是否开启（默认开）。"""
    raw = (os.environ.get(ENV_FLAG) or "").strip().lower()
    if not raw:
        return True
    return raw in _TRUTHY


@dataclass(frozen=True)
class DiscoveredModule:
    """一个从包里发现出来的模块声明。

    Attributes:
        package: 包名（`agent/<package>` 的最后一段）
        display_path: 展示路径（`agent/<package>/`，详情面板定位用）
        module_id: 拓扑节点 ID（`discovered.<package>`）
        name / description: 展示名与一句话
        domain_id: 落在哪个域
        status_source: `api:/...` 或 `config:...`
        metrics: 要从状态源响应里挑的字段名
        has_status_source: 是否满足白名单（False ⇒ 只在"未登记"里）
    """
    package: str
    display_path: str
    module_id: str
    name: str
    description: str = ""
    domain_id: str = DEFAULT_DOMAIN_ID
    status_source: str = ""
    metrics: Tuple[str, ...] = ()
    has_status_source: bool = False


@dataclass(frozen=True)
class DeclaredUnit:
    """包自己声明的「功能单位」（拓扑之外的第二张清单）。

    Attributes:
        package: 包名（agent/<package> 的最后一段）
        unit_id: 单位 ID（与手写 Unit 同一命名空间，重复登记时后写覆盖）
        label: 展示名
        family: 前端按 family 过滤用的族（缺省 "module"）
        probe: "模块路径:函数名"（**调用时才 import**，启动期零副作用）
        description: 一句话
    """
    package: str
    unit_id: str
    label: str
    family: str = "module"
    probe: str = ""
    description: str = ""


@dataclass(frozen=True)
class DiscoveryResult:
    """一次扫描的完整结果（不可变，便于缓存与断言）。"""
    declared: Tuple[DiscoveredModule, ...] = ()
    unregistered: Tuple[Dict[str, str], ...] = ()
    units: Tuple[DeclaredUnit, ...] = ()
    invalid_units: Tuple[Dict[str, str], ...] = ()
    scanned_packages: int = 0
    root: str = ""


# ════════════════════════════════════════════════════════════
#  AST 解析（不 import）
# ════════════════════════════════════════════════════════════

def _literal(node: ast.AST) -> Optional[object]:
    """只认字面量（str / list[str]）；其他表达式一律当作"没声明"。"""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None


def parse_declarations(source: str) -> Dict[str, object]:
    """从 `__init__.py` 源码里取 `MODULE_*` 模块级字面量声明。

    只扫**模块顶层**的赋值（`X = ...` / `X: str = ...`）：函数/类里的同名变量
    不是声明；动态赋值（`= os.environ[...]`）也不是字面量 ⇒ 取不到就是没声明。
    """
    out: Dict[str, object] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return out
    for stmt in tree.body:
        targets: List[ast.AST] = []
        value: Optional[ast.AST] = None
        if isinstance(stmt, ast.Assign):
            targets = list(stmt.targets)
            value = stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets = [stmt.target]
            value = stmt.value
        if value is None:
            continue
        literal = _literal(value)
        if literal is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id.startswith(DECL_PREFIX):
                out[target.id] = literal
    return out


def _as_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_metrics(value: object) -> Tuple[str, ...]:
    if isinstance(value, str):
        items: Sequence[object] = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        return ()
    return tuple(str(x).strip() for x in items if str(x).strip())


def _module_from_declarations(package: str, decls: Dict[str, object]) -> Optional[DiscoveredModule]:
    """把一份声明分类成「已登记 / 未登记 / 不是模块」。"""
    status_source = _as_text(decls.get(STATUS_SOURCE_KEY))
    name = _as_text(decls.get(NAME_KEY))
    description = _as_text(decls.get(DESCRIPTION_KEY))
    domain_id = _as_text(decls.get(DOMAIN_KEY)) or DEFAULT_DOMAIN_ID
    metrics = _as_metrics(decls.get(METRICS_KEY))
    if not status_source and not name and not description:
        # 不是模块候选（utils/common/config 这类工具包永远不该出现在拓扑里）
        return None
    return DiscoveredModule(
        package=package,
        display_path="agent/" + package + "/",
        module_id=MODULE_ID_PREFIX + package,
        name=name or package,
        description=description,
        domain_id=domain_id,
        status_source=status_source,
        metrics=metrics,
        has_status_source=bool(status_source),
    )


def _valid_unit_probe(probe: str) -> bool:
    """probe 必须是 '模块路径:函数名'（两边都非空）。"""
    if ":" not in probe:
        return False
    module, _, attr = probe.partition(":")
    return bool(module.strip()) and bool(attr.strip())


def _unit_from_entry(package: str, entry: object) -> Tuple[Optional[DeclaredUnit], str]:
    """一条单位声明 → (DeclaredUnit, 失败原因)。"""
    if not isinstance(entry, dict):
        return None, REASON_UNIT_BAD_SHAPE
    unit_id = _as_text(entry.get("id"))
    probe = _as_text(entry.get("probe"))
    if not unit_id or not probe:
        return None, REASON_UNIT_BAD_SHAPE
    if not _valid_unit_probe(probe):
        return None, REASON_UNIT_BAD_PROBE
    return DeclaredUnit(
        package=package,
        unit_id=unit_id,
        label=_as_text(entry.get("label")) or unit_id,
        family=_as_text(entry.get("family")) or "module",
        probe=probe,
        description=_as_text(entry.get("description")),
    ), ""


def _units_from_declarations(package: str, decls: Dict[str, object],
                             invalid: List[Dict[str, str]]) -> List[DeclaredUnit]:
    """从 MODULE_UNIT（dict）/ MODULE_UNITS（list）取出单位声明（逐条校验）。"""
    raw: List[object] = []
    single = decls.get(UNIT_KEY)
    if single is not None:
        raw.append(single)
    many = decls.get(UNITS_KEY)
    if isinstance(many, (list, tuple)):
        raw.extend(many)
    elif many is not None:
        raw.append(many)
    out: List[DeclaredUnit] = []
    for entry in raw:
        unit, reason = _unit_from_entry(package, entry)
        if unit is None:
            invalid.append({"package": "agent/" + package + "/", "reason": reason})
            continue
        out.append(unit)
    return out


# ════════════════════════════════════════════════════════════
#  扫描
# ════════════════════════════════════════════════════════════

_cache: Dict[str, DiscoveryResult] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """清扫描缓存（测试隔离 / 热重载用）。"""
    with _cache_lock:
        _cache.clear()


def default_root() -> Path:
    """默认扫描根 = 本文件所在目录（`agent/`）。"""
    return Path(__file__).resolve().parent


def _candidate_packages(root: Path) -> List[Path]:
    """根目录下的包目录（含 `__init__.py`，跳过 `__pycache__` 等）。"""
    out: List[Path] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:  # 目录不可读 ⇒ 没有候选（不抛，拓扑照常出图）
        logger.warning("模块自动发现：扫描根不可读 %s（%s）", root, type(exc).__name__)
        return out
    for path in entries:
        name = path.name
        if not path.is_dir() or name.startswith(("_", ".")):
            continue
        if not (path / "__init__.py").is_file():
            continue
        out.append(path)
    return out


def scan(root: Optional[Path] = None, use_cache: bool = True) -> DiscoveryResult:
    """扫描根目录下的包，返回「已登记 / 未登记」两桶。

    Args:
        root: 扫描根（默认 `agent/`）；测试传 tmp_path。
        use_cache: 复用进程内结果（默认 True —— 与 DOMAINS 同为启动期常量）。
    """
    base = Path(root).resolve() if root is not None else default_root()
    key = str(base)
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None:
            return hit

    declared: List[DiscoveredModule] = []
    unregistered: List[Dict[str, str]] = []
    units: List[DeclaredUnit] = []
    invalid_units: List[Dict[str, str]] = []
    packages = _candidate_packages(base)
    for pkg in packages:
        try:
            source = (pkg / "__init__.py").read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            logger.warning("模块自动发现：读取 %s 失败（%s）", pkg.name, type(exc).__name__)
            continue
        decls = parse_declarations(source)
        # 单位声明与拓扑声明**互相独立**：只声明单位、不声明状态源的包
        # 不该因此进"未登记"（那是拓扑的事，这里只是另一张清单）
        units.extend(_units_from_declarations(pkg.name, decls, invalid_units))
        module = _module_from_declarations(pkg.name, decls)
        if module is None:
            continue
        if not module.has_status_source:
            unregistered.append({
                "package": module.display_path,
                "name": module.name,
                "reason": REASON_NO_STATUS_SOURCE,
            })
            continue
        if module.domain_id not in DOMAIN_IDS:
            # 域写错 = 会被塞到别的层里去（图会撒谎）⇒ 如实归到"未登记"并说明原因
            unregistered.append({
                "package": module.display_path,
                "name": module.name,
                "reason": "MODULE_DOMAIN=" + module.domain_id + " 不是六域之一（"
                          + "/".join(DOMAIN_IDS) + "）",
            })
            continue
        declared.append(module)

    result = DiscoveryResult(
        declared=tuple(declared),
        unregistered=tuple(unregistered),
        units=tuple(units),
        invalid_units=tuple(invalid_units),
        scanned_packages=len(packages),
        root=str(base),
    )
    if use_cache:
        with _cache_lock:
            _cache[key] = result
    return result
