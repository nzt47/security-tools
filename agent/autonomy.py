"""自主权分级（L1-L5）— 聚合视图 + 运行时上下文注入（TASK-07）

设计思路要求"自主权分级管控（L1-L5）"。审计发现云枢现状是三套正交机制
（PermissionSystem 两级阻断/确认、ThreeLevelCircuitBreaker SESSION→USER→GLOBAL
三级熔断、GracefulDegrade NORMAL→…→EMERGENCY 五级健康降级），均非"按自主程度
划分的行为分级"。

【不易】本模块是纯"映射与视图"层：
- 不修改 permission_system / circuit_breaker / graceful_degrade 任一现有机制的
  判定逻辑与接口；
- L1-L5 只把既有机制按"行为自主程度"归类聚合（POLICY_TABLE 声明式表），供
  上层查询、日志、审计使用；
- aggregate() 返回的 AutonomyVerdict 仅是叠加视图，绝不改变 base_result
  （既有 PermissionResult 语义零变化）。

【变易】等级可配置：config.yaml `autonomy.default_level` / `autonomy.per_level_policy`
可覆盖，环境变量 AUTONOMY_DEFAULT_LEVEL 最高优先；会话级可通过
set_session_level() 覆盖。

【S5-1 可表达策略（2026-10-12）】"档位→动作"不再只靠关键字表：
    config.yaml `autonomy.tool_policy`（工具名 / `前缀*` → 类别）是操作员可表达的
    声明策略；未声明的工具按权威能力元数据（`data/tool_definitions/*.yaml` 的
    plane/effect/risk，取**最保守**）派生；两者都判不出时按
    `autonomy.unknown_tool_category`（默认 **high_risk**）**保守兜底**，不再默认
    放行。逐级保守的可判定形式 = 档位越高允许类别只增不减（L1⊆L2⊆L3⊆L4=L5），
    由 `AutonomyPolicy.required_level()` 与测试 `TestMonotonicLevels` 钉住。
    详见 `classify_action` 上方注释。

【简易】纯声明式策略表 + ContextVar 上下文注入（遵 agent/monitoring/tracing.py
的 Token 式恢复模式），无 I/O 持锁。

用法:
    with AutonomyContext(resolve_autonomy_level(session_id="sess_1")):
        ...  # 会话内 get_autonomy_level() 返回当前等级

    verdict = AutonomyPolicy.aggregate(base_result, "write_file:foo.txt")
    if verdict.escalation:
        logger.info("越级操作（聚合视图，不影响既有判定）: %s", verdict.escalation)
"""

import logging
import os
import threading
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# 默认等级（保守）：config.yaml autonomy.default_level 与 AUTONOMY_DEFAULT_LEVEL 可覆盖
_DEFAULT_LEVEL_NAME = "L3"


class AutonomyLevel(Enum):
    """行为自主权等级（L1 最保守 → L5 完全自主）"""

    L1 = "L1"  # 只读观察
    L2 = "L2"  # 低风险自主
    L3 = "L3"  # 中风险需确认（默认，保守）
    L4 = "L4"  # 高风险专家
    L5 = "L5"  # 完全自主

    @property
    def level(self) -> int:
        """数值序号（1-5）"""
        return {"L1": 1, "L2": 2, "L3": 3, "L4": 4, "L5": 5}[self.value]

    @classmethod
    def from_value(cls, value: Any) -> "AutonomyLevel":
        """宽容解析：'L3' / 'l3' / '3' / AutonomyLevel.L3 → AutonomyLevel.L3"""
        if isinstance(value, AutonomyLevel):
            return value
        s = str(value or "").strip().upper()
        if s.startswith("L") and s[1:].isdigit() and 1 <= int(s[1:]) <= 5:
            return cls(f"L{int(s[1:])}")
        if s.isdigit() and 1 <= int(s) <= 5:
            return cls(f"L{int(s)}")
        logger.warning("[自主权] 非法等级 %r，回退默认 %s", value, _DEFAULT_LEVEL_NAME)
        return cls(_DEFAULT_LEVEL_NAME)


class ToolCategory(Enum):
    """工具行为类别（按副作用大小分类）"""

    READONLY = "readonly"        # 只读：检索/查询/读文件/计算，零副作用
    LOW_RISK = "low_risk"        # 低风险：无副作用的计算/转换
    MEDIUM_RISK = "medium_risk"  # 中风险：写文件/改配置等需确认
    HIGH_RISK = "high_risk"      # 高风险：系统级操作


class ConfirmationScope(Enum):
    """确认范围（聚合视图用，不改变既有确认链触发语义）"""

    NONE = "none"                # 无需额外确认
    ALL = "all"                  # 一切非只读操作均要求确认
    MEDIUM_AND_ABOVE = "medium_and_above"  # 中风险及以上要求确认
    HIGH_ONLY = "high_only"      # 仅高风险要求确认


@dataclass(frozen=True)
class LevelPolicy:
    """单等级策略（纯声明式，表驱动）"""

    level: AutonomyLevel
    behavior: str                                   # 行为边界描述
    allowed_categories: frozenset                   # 允许的工具类别
    confirmation_scope: ConfirmationScope           # 聚合确认要求
    audit_required: bool                            # 是否要求全审计
    mechanism: str                                  # 映射到的既有机制说明


@dataclass
class AutonomyVerdict:
    """聚合视图：等级 × 工具类别 × 既有机制判定结果的叠加

    【不易】base_* 字段与既有 PermissionResult 语义一致（零变化）；
    escalation / confirmation_required / audit_required 是本模块叠加的视图层。
    """

    level: AutonomyLevel
    tool_category: ToolCategory
    within_level: bool                              # 操作是否在等级允许范围内
    escalation: List[str] = field(default_factory=list)  # 越级/风险标注（只读视图）
    confirmation_required: bool = False             # 聚合确认要求（视图）
    audit_required: bool = False                    # 聚合审计要求（视图）
    base_allowed: Optional[bool] = None             # 既有判定结果（原样透传）
    base_requires_confirmation: bool = False        # 既有确认链结果（原样透传）

    def to_dict(self) -> Dict[str, Any]:
        """可查询的聚合视图（供日志/审计/接口消费）"""
        return {
            "level": self.level.value,
            "tool_category": self.tool_category.value,
            "within_level": self.within_level,
            "escalation": list(self.escalation),
            "confirmation_required": self.confirmation_required,
            "audit_required": self.audit_required,
            "base_allowed": self.base_allowed,
            "base_requires_confirmation": self.base_requires_confirmation,
        }


# ── 工具类别分类关键字表（声明式，可按需扩展）──────────────────────

# 只读操作标记（命中即 READONLY，零副作用）
_READONLY_MARKERS = (
    "read", "search", "query", "get_", "lookup", "calc", "count", "check",
    "list", "查看", "查询", "检索", "搜索", "读取", "计算",
)

# 中风险操作标记（写/改/删/配置）
_MEDIUM_RISK_MARKERS = (
    "write", "create", "delete", "remove", "move", "copy", "rename", "mkdir",
    "config", "set ", "update", "edit", "save", "upload", "download",
    "install", "uninstall", "备份", "写入", "创建", "删除", "移动", "复制",
    "修改", "编辑", "保存", "配置", "安装", "卸载",
)

# 高风险操作标记（系统级）
_HIGH_RISK_MARKERS = (
    "system", "exec", "shell", "process", "kill", "shutdown", "reboot",
    "format", "diskpart", "reg ", "chmod", "chown", "sudo", "注册表",
    "进程", "系统", "关机", "重启", "格式化", "权限",
)

# 工具名 → 类别 的显式声明（工具名优先于动作串关键字）
_TOOL_NAME_CATEGORY: Dict[str, ToolCategory] = {
    "web_search": ToolCategory.READONLY,
    "calculator": ToolCategory.READONLY,
    "file_read": ToolCategory.READONLY,
    "read_file": ToolCategory.READONLY,
    "search": ToolCategory.READONLY,
    "get_current_time": ToolCategory.READONLY,
    "knowledge_query": ToolCategory.READONLY,
    "file_write": ToolCategory.MEDIUM_RISK,
    "write_file": ToolCategory.MEDIUM_RISK,
    "code_execute": ToolCategory.MEDIUM_RISK,
    "shell": ToolCategory.HIGH_RISK,
    "system": ToolCategory.HIGH_RISK,
}


# ── S5-1 可表达策略：操作员声明 + 权威元数据派生（覆盖自定义工具）──────────
#
# 【为什么】原先 classify_action 只有"工具名硬编码表 + 关键字表"，两处都覆盖不到
#   自定义工具（运行时注册 / LLM 自生成）：它们会落到 LOW_RISK 兜底，而 LOW_RISK 在
#   L2 档即被允许 ⇒ **新工具默认按最宽口径放行**，与"逐级保守"相反。
# 新的判定顺序（工具名给定时）：
#   ① 操作员在 config.yaml autonomy.tool_policy 里显式声明的类别（精确名或 前缀* glob）
#      ——"可表达策略"的落点，也是唯一能覆盖内置工具的口子；
#   ② 内置 _TOOL_NAME_CATEGORY（保持既有语义逐字不变）；
#   ③ 权威能力元数据派生（data/tool_definitions/*.yaml 的 plane/effect/risk，取**最保守**
#      的一条）——覆盖所有已登记工具，含自定义工具；
#   ④ 动作串关键字（既有表）；
#   ⑤ 都判不出来 ⇒ autonomy.unknown_tool_category（默认 **high_risk**，保守）：
#      判不了归属的工具**不再默认放行**。
# 未传 tool_name（纯动作串，既有调用方）⇒ 仍走关键字 → LOW_RISK 兜底，逐字不变。
_CATEGORY_ORDER: Dict[ToolCategory, int] = {
    ToolCategory.READONLY: 0,
    ToolCategory.LOW_RISK: 1,
    ToolCategory.MEDIUM_RISK: 2,
    ToolCategory.HIGH_RISK: 3,
}

_EFFECT_CATEGORY: Dict[str, ToolCategory] = {
    "read": ToolCategory.READONLY,
    "write": ToolCategory.MEDIUM_RISK,
    "execute": ToolCategory.HIGH_RISK,
    "extend": ToolCategory.HIGH_RISK,
}
_RISK_CATEGORY: Dict[str, ToolCategory] = {
    "low": ToolCategory.LOW_RISK,
    "medium": ToolCategory.MEDIUM_RISK,
    "high": ToolCategory.MEDIUM_RISK,
    "critical": ToolCategory.HIGH_RISK,
}
_PLANE_CATEGORY: Dict[str, ToolCategory] = {
    "resident": ToolCategory.LOW_RISK,
    "perceive": ToolCategory.READONLY,
    "act": ToolCategory.MEDIUM_RISK,
    "govern": ToolCategory.HIGH_RISK,
}

#: 操作员声明（归一化键 → 类别；键可写 前缀*）
_DECLARED_TOOL_CATEGORY: Dict[str, ToolCategory] = {}
#: 未登记工具的类别（None ⇒ 用 _DEFAULT_UNKNOWN_TOOL_CATEGORY）
_UNKNOWN_TOOL_CATEGORY: Optional[ToolCategory] = None
_DEFAULT_UNKNOWN_TOOL_CATEGORY = ToolCategory.HIGH_RISK
_TOOL_POLICY_LOADED = False
_TOOL_POLICY_LOCK = threading.RLock()
_META_CATEGORY_CACHE: Dict[str, Optional[ToolCategory]] = {}


def _parse_category(value: Any) -> Optional[ToolCategory]:
    """宽容解析类别字符串；非法 ⇒ None（并告警，便于配置纠错）"""
    try:
        return ToolCategory(str(value or "").strip().lower())
    except ValueError:
        logger.warning("[自主权] tool_policy 的类别取值非法: %r（可选 %s）",
                       value, [c.value for c in ToolCategory])
        return None


def _load_tool_policy() -> None:
    """载入 autonomy.tool_policy / autonomy.unknown_tool_category（进程内一次）"""
    global _TOOL_POLICY_LOADED, _UNKNOWN_TOOL_CATEGORY
    if _TOOL_POLICY_LOADED:
        return
    with _TOOL_POLICY_LOCK:
        if _TOOL_POLICY_LOADED:
            return
        declared: Dict[str, ToolCategory] = {}
        unknown: Optional[ToolCategory] = None
        try:
            cpath = Path(__file__).resolve().parent.parent / "config.yaml"
            if cpath.exists():
                import yaml as _yaml
                data = _yaml.safe_load(cpath.read_text(encoding="utf-8")) or {}
                cfg = data.get("autonomy") or {}
                raw = cfg.get("tool_policy") or {}
                if isinstance(raw, Mapping):
                    for key, val in raw.items():
                        cat = _parse_category(val)
                        k = str(key or "").strip()
                        if cat is not None and k:
                            declared[k] = cat
                if "unknown_tool_category" in cfg:
                    unknown = _parse_category(cfg.get("unknown_tool_category"))
        except Exception as e:  # noqa: BLE001 配置不可读 ⇒ 保持保守默认，绝不放开
            logger.debug("[自主权] tool_policy 读取失败（用保守默认）: %s", e)
            declared = {}
        _DECLARED_TOOL_CATEGORY.clear()
        _DECLARED_TOOL_CATEGORY.update(declared)
        _UNKNOWN_TOOL_CATEGORY = unknown
        _TOOL_POLICY_LOADED = True


def declared_category(tool_name: str) -> Optional[ToolCategory]:
    """操作员显式声明的类别（精确名优先于 前缀*）；未声明 ⇒ None"""
    _load_tool_policy()
    name = str(tool_name or "").strip()
    if not name:
        return None
    exact = _DECLARED_TOOL_CATEGORY.get(name)
    if exact is not None:
        return exact
    for pattern, cat in _DECLARED_TOOL_CATEGORY.items():
        if pattern.endswith("*") and name.startswith(pattern[:-1]):
            return cat
    return None


def _derive_category_from_meta(meta: Any) -> Optional[ToolCategory]:
    """从能力元数据（plane/effect/risk）派生类别，取**最保守**的一条"""
    cands: List[ToolCategory] = []
    for attr, table in (("effect", _EFFECT_CATEGORY),
                        ("risk", _RISK_CATEGORY),
                        ("plane", _PLANE_CATEGORY)):
        val = str(getattr(meta, attr, "") or "").strip().lower()
        cat = table.get(val)
        if cat is not None:
            cands.append(cat)
    if not cands:
        return None
    return max(cands, key=lambda c: _CATEGORY_ORDER[c])


def metadata_category(tool_name: str) -> Optional[ToolCategory]:
    """按权威元数据派生类别；元数据不可用/未登记 ⇒ None（**不缓存失败**）"""
    name = str(tool_name or "").strip()
    if not name:
        return None
    if name in _META_CATEGORY_CACHE:
        return _META_CATEGORY_CACHE[name]
    cat: Optional[ToolCategory] = None
    try:
        from agent.lines.models import load_tool_meta
        meta = (load_tool_meta() or {}).get(name)
        if meta is not None:
            cat = _derive_category_from_meta(meta)
    except Exception as e:  # noqa: BLE001 元数据不可用 ⇒ 交给下一级（关键字/保守默认）
        logger.debug("[自主权] 工具元数据不可用（跳过派生）: %s", e)
        return None
    _META_CATEGORY_CACHE[name] = cat
    return cat


def unknown_tool_category() -> ToolCategory:
    """未登记/判不出归属的工具类别（默认 high_risk，保守；可由 config 覆盖）"""
    _load_tool_policy()
    return _UNKNOWN_TOOL_CATEGORY or _DEFAULT_UNKNOWN_TOOL_CATEGORY


def _keyword_category(action: str) -> Optional[ToolCategory]:
    """动作串关键字命中 ⇒ 类别；一条都不命中 ⇒ None（不猜、不兜底）"""
    text = (action or "").lower()
    # 高风险优先判定（最保守：系统级操作不因含只读词而被低估）
    for marker in _HIGH_RISK_MARKERS:
        if marker.lower() in text:
            return ToolCategory.HIGH_RISK
    for marker in _MEDIUM_RISK_MARKERS:
        if marker.lower() in text:
            return ToolCategory.MEDIUM_RISK
    for marker in _READONLY_MARKERS:
        if marker.lower() in text:
            return ToolCategory.READONLY
    return None


def classify_action(action: str, tool_name: Optional[str] = None) -> ToolCategory:
    """动作/工具名 → 行为类别（可表达策略 + 权威元数据 + 关键字，逐级保守）

    判定顺序见上方「可表达策略」注释：
      声明策略 → 内置工具名表 → 权威元数据派生 → 关键字 → 未知工具保守默认。
    **未传 tool_name** 时保持既有口径：关键字 → LOW_RISK 兜底（逐字不变）。
    """
    name = (tool_name or "").strip()
    if name:
        declared = declared_category(name)
        if declared is not None:
            return declared
        explicit = _TOOL_NAME_CATEGORY.get(name)
        if explicit is not None:
            return explicit
        derived = metadata_category(name)
        if derived is not None:
            return derived
        keyword = _keyword_category(action)
        if keyword is not None:
            return keyword
        return unknown_tool_category()
    return _keyword_category(action) or ToolCategory.LOW_RISK


class AutonomyPolicy:
    """L1-L5 分级策略表（与 TASK-07 任务书分级表逐行对应）+ 聚合方法"""

    # 分级表（任务书 Step 1 表格驱动的单向验证锚点）
    POLICY_TABLE: Dict[AutonomyLevel, LevelPolicy] = {
        AutonomyLevel.L1: LevelPolicy(
            level=AutonomyLevel.L1,
            behavior="仅感知/检索/对话，零副作用",
            allowed_categories=frozenset({ToolCategory.READONLY}),
            confirmation_scope=ConfirmationScope.ALL,
            audit_required=True,
            mechanism="工具白名单=只读集；PermissionSystem 全黑名单兜底",
        ),
        AutonomyLevel.L2: LevelPolicy(
            level=AutonomyLevel.L2,
            behavior="可执行低风险工具（检索/计算/读文件）",
            allowed_categories=frozenset({ToolCategory.READONLY, ToolCategory.LOW_RISK}),
            confirmation_scope=ConfirmationScope.NONE,
            audit_required=False,
            mechanism="现有 BLOCKLIST 之外的默认路径",
        ),
        AutonomyLevel.L3: LevelPolicy(
            level=AutonomyLevel.L3,
            behavior="写文件/改配置等需二次确认",
            allowed_categories=frozenset({
                ToolCategory.READONLY, ToolCategory.LOW_RISK, ToolCategory.MEDIUM_RISK}),
            confirmation_scope=ConfirmationScope.MEDIUM_AND_ABOVE,
            audit_required=False,
            mechanism="DANGEROUS_PATTERNS / SENSITIVE_DIRS 确认链",
        ),
        AutonomyLevel.L4: LevelPolicy(
            level=AutonomyLevel.L4,
            behavior="系统级操作，全审计",
            allowed_categories=frozenset(ToolCategory),
            confirmation_scope=ConfirmationScope.HIGH_ONLY,
            audit_required=True,
            mechanism="熔断 SESSION 级 + 审计日志 + HITL 确认",
        ),
        AutonomyLevel.L5: LevelPolicy(
            level=AutonomyLevel.L5,
            behavior="受全局熔断与日配额约束的全能力",
            allowed_categories=frozenset(ToolCategory),
            confirmation_scope=ConfirmationScope.NONE,
            audit_required=False,
            mechanism="GLOBAL 级熔断 + rate_limiter 配额",
        ),
    }

    # 配置覆盖缓存（config.yaml autonomy.per_level_policy；空 = 无覆盖）
    _overrides: Dict[str, Dict[str, Any]] = {}
    _overrides_loaded = False
    _lock = threading.RLock()

    @classmethod
    def get(cls, level: AutonomyLevel) -> LevelPolicy:
        """获取等级策略（应用 per_level_policy 配置覆盖后返回）"""
        cls._ensure_overrides_loaded()
        policy = cls.POLICY_TABLE.get(level)
        if policy is None:
            policy = cls.POLICY_TABLE[AutonomyLevel(_DEFAULT_LEVEL_NAME)]
        return cls._apply_overrides(policy)

    @classmethod
    def aggregate(
        cls,
        base_result: Any,
        action: str,
        tool_name: Optional[str] = None,
        level: Optional[AutonomyLevel] = None,
    ) -> AutonomyVerdict:
        """把既有机制判定结果与等级策略聚合为只读视图

        Args:
            base_result: PermissionResult（或带 allowed / requires_confirmation 属性）
            action: 操作描述
            tool_name: 工具名（可选，分类优先）
            level: 当前等级（None 时读 ContextVar）

        Returns:
            AutonomyVerdict：聚合视图。base_* 与 base_result 原样透传，
            escalation / confirmation_required / audit_required 为本层叠加。
        """
        lv = level or get_autonomy_level()
        policy = cls.get(lv)
        category = classify_action(action, tool_name)

        escalation: List[str] = []
        if category not in policy.allowed_categories:
            escalation.append(
                f"operation_outside_level: {category.value} not allowed at {lv.value}"
            )
        if category == ToolCategory.HIGH_RISK and policy.audit_required:
            escalation.append("high_risk_operation")

        base_allowed = getattr(base_result, "allowed", True)
        base_confirmation = bool(getattr(base_result, "requires_confirmation", False))
        # 聚合确认要求 = 既有确认链要求（原样） ∪ 等级策略确认要求（视图）
        scope = policy.confirmation_scope
        if scope == ConfirmationScope.ALL:
            view_confirmation = category != ToolCategory.READONLY
        elif scope == ConfirmationScope.MEDIUM_AND_ABOVE:
            view_confirmation = category in (ToolCategory.MEDIUM_RISK, ToolCategory.HIGH_RISK)
        elif scope == ConfirmationScope.HIGH_ONLY:
            view_confirmation = category == ToolCategory.HIGH_RISK
        else:
            view_confirmation = False

        return AutonomyVerdict(
            level=lv,
            tool_category=category,
            within_level=not escalation,
            escalation=escalation,
            confirmation_required=bool(base_confirmation or view_confirmation),
            audit_required=policy.audit_required,
            base_allowed=base_allowed,
            base_requires_confirmation=base_confirmation,
        )

    # ════════════════════════════════════════════════════════════════
    #  配置加载（优先级: 环境变量 > config.yaml > 硬编码默认值）
    # ════════════════════════════════════════════════════════════════

    @classmethod
    def _ensure_overrides_loaded(cls) -> None:
        if cls._overrides_loaded:
            return
        with cls._lock:
            if cls._overrides_loaded:
                return
            try:
                _overrides = {}
                cpath = Path(__file__).resolve().parent.parent / "config.yaml"
                if cpath.exists():
                    import yaml as _yaml
                    with open(cpath, "r", encoding="utf-8") as f:
                        data = _yaml.safe_load(f) or {}
                    autonomy_cfg = data.get("autonomy") or {}
                    per_level = autonomy_cfg.get("per_level_policy") or {}
                    for key, val in per_level.items():
                        if isinstance(val, dict):
                            _overrides[str(key).upper()] = val
                cls._overrides = _overrides
            except Exception as e:
                logger.debug("[自主权] per_level_policy 配置读取失败，使用默认表: %s", e)
                cls._overrides = {}
            cls._overrides_loaded = True

    @classmethod
    def _apply_overrides(cls, policy: LevelPolicy) -> LevelPolicy:
        """应用 per_level_policy 配置覆盖（返回新策略，不污染默认表）"""
        override = cls._overrides.get(policy.level.value)
        if not override:
            return policy
        allowed = policy.allowed_categories
        scope = policy.confirmation_scope
        audit = policy.audit_required
        if isinstance(override.get("allowed_categories"), (list, tuple, set)):
            cats = set()
            for c in override["allowed_categories"]:
                try:
                    cats.add(ToolCategory(str(c).lower()))
                except ValueError:
                    continue
            if cats:
                allowed = frozenset(cats)
        scope_name = str(override.get("confirmation_scope", "")).strip()
        if scope_name:
            for s in ConfirmationScope:
                if s.value == scope_name:
                    scope = s
                    break
        if "audit_required" in override:
            audit = bool(override["audit_required"])
        return LevelPolicy(
            level=policy.level,
            behavior=policy.behavior,
            allowed_categories=allowed,
            confirmation_scope=scope,
            audit_required=audit,
            mechanism=policy.mechanism,
        )

    @classmethod
    def reset_config_cache(cls) -> None:
        """重置配置缓存（仅测试用）"""
        global _TOOL_POLICY_LOADED, _UNKNOWN_TOOL_CATEGORY
        with cls._lock:
            cls._overrides = {}
            cls._overrides_loaded = False
        with _TOOL_POLICY_LOCK:
            _DECLARED_TOOL_CATEGORY.clear()
            _UNKNOWN_TOOL_CATEGORY = None
            _TOOL_POLICY_LOADED = False
        _META_CATEGORY_CACHE.clear()

    @classmethod
    def required_level(cls, category: ToolCategory) -> AutonomyLevel:
        """该行为类别所需的最低自主权档位（逐级保守：L1 最严 → L5 最宽）

        「逐级保守」的可判定形式：档位越高允许的类别**只增不减**（L1⊆L2⊆L3⊆L4=L5），
        故任一类别都存在唯一的最低档位。由 ``TestMonotonicLevels`` 钉住。
        """
        for lv in (AutonomyLevel.L1, AutonomyLevel.L2, AutonomyLevel.L3,
                   AutonomyLevel.L4, AutonomyLevel.L5):
            if category in cls.get(lv).allowed_categories:
                return lv
        return AutonomyLevel.L5

    @classmethod
    def table(cls) -> Dict[str, Dict[str, Any]]:
        """导出分级表（文档/审计用，含配置覆盖）"""
        return {
            lv.value: {
                "behavior": cls.get(lv).behavior,
                "allowed_categories": sorted(c.value for c in cls.get(lv).allowed_categories),
                "confirmation_scope": cls.get(lv).confirmation_scope.value,
                "audit_required": cls.get(lv).audit_required,
                "mechanism": cls.get(lv).mechanism,
            }
            for lv in (AutonomyLevel.L1, AutonomyLevel.L2, AutonomyLevel.L3,
                       AutonomyLevel.L4, AutonomyLevel.L5)
        }


# ── 运行时上下文（ContextVar，遵 tracing.py 的 Token 式恢复模式）──────

_current_autonomy_level: ContextVar[AutonomyLevel] = ContextVar(
    "autonomy_level", default=AutonomyLevel.L3)


def get_autonomy_level() -> AutonomyLevel:
    """获取当前上下文自主权等级（线程/协程隔离）"""
    return _current_autonomy_level.get()


def set_autonomy_level(level: Any) -> Token:
    """显式设置当前上下文等级，返回 Token 供恢复（并发安全）"""
    return _current_autonomy_level.set(AutonomyLevel.from_value(level))


class AutonomyContext:
    """自主权上下文管理器（栈式，Token 恢复，__exit__ 绝不抛异常）

    【不易】与 tracing.TraceContext 同构：__enter__ 保存旧值、__exit__ 用
    reset(Token) 精确恢复；异常安全（__exit__ 不抛异常，不掩盖 with 块内原始异常）。
    """

    def __init__(self, level: Any):
        self.level = AutonomyLevel.from_value(level)
        self._old_level: Optional[AutonomyLevel] = None
        self._token: Optional[Token] = None

    def __enter__(self) -> "AutonomyContext":
        self._old_level = _current_autonomy_level.get()
        self._token = _current_autonomy_level.set(self.level)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token is not None:
            try:
                _current_autonomy_level.reset(self._token)
            except ValueError:
                # 跨线程/协程配对时 reset 失败 → 降级手动恢复，绝不抛异常
                _current_autonomy_level.set(self._old_level)
        return False


# ── 会话级等级覆盖（线程安全注册表，仅内存变更）────────────────────

_session_levels: Dict[str, AutonomyLevel] = {}
_session_lock = threading.RLock()


def set_session_level(session_id: str, level: Any) -> None:
    """按会话设置自主等级（会话级覆盖，最高优先于环境变量/配置）"""
    if not session_id:
        return
    with _session_lock:
        _session_levels[str(session_id)] = AutonomyLevel.from_value(level)


def get_session_level(session_id: str) -> Optional[AutonomyLevel]:
    """查询会话等级（无覆盖返回 None）"""
    if not session_id:
        return None
    with _session_lock:
        return _session_levels.get(str(session_id))


def clear_session_level(session_id: str) -> None:
    """清除会话等级覆盖（仅测试/管理用）"""
    if not session_id:
        return
    with _session_lock:
        _session_levels.pop(str(session_id), None)


def resolve_autonomy_level(session_id: Optional[str] = None) -> AutonomyLevel:
    """解析等级 — 优先级: 会话覆盖 > AUTONOMY_DEFAULT_LEVEL 环境变量 >
    config.yaml autonomy.default_level > 硬编码默认 L3"""
    sess = get_session_level(session_id)
    if sess is not None:
        return sess
    env = os.environ.get("AUTONOMY_DEFAULT_LEVEL", "").strip()
    if env:
        return AutonomyLevel.from_value(env)
    cfg = _load_autonomy_config()
    default_name = cfg.get("default_level") or _DEFAULT_LEVEL_NAME
    return AutonomyLevel.from_value(default_name)


_AUTONOMY_CFG_CACHE: Optional[Dict[str, Any]] = None


def _load_autonomy_config() -> Dict[str, Any]:
    """读取 config.yaml autonomy 段（失败降级为空 dict，不影响主链路）"""
    global _AUTONOMY_CFG_CACHE
    if _AUTONOMY_CFG_CACHE is not None:
        return _AUTONOMY_CFG_CACHE
    cfg: Dict[str, Any] = {}
    try:
        cpath = Path(__file__).resolve().parent.parent / "config.yaml"
        if cpath.exists():
            import yaml as _yaml
            with open(cpath, "r", encoding="utf-8") as f:
                data = _yaml.safe_load(f) or {}
            cfg = data.get("autonomy") or {}
    except Exception as e:
        logger.debug("[自主权] config.yaml autonomy 段读取失败，使用默认: %s", e)
    _AUTONOMY_CFG_CACHE = cfg
    return cfg


def reset_config_cache() -> None:
    """重置配置缓存（仅测试用）"""
    global _AUTONOMY_CFG_CACHE
    _AUTONOMY_CFG_CACHE = None
    AutonomyPolicy.reset_config_cache()


__all__ = [
    "AutonomyLevel",
    "ToolCategory",
    "ConfirmationScope",
    "LevelPolicy",
    "AutonomyVerdict",
    "AutonomyPolicy",
    "AutonomyContext",
    "classify_action",
    "get_autonomy_level",
    "set_autonomy_level",
    "set_session_level",
    "get_session_level",
    "clear_session_level",
    "resolve_autonomy_level",
    "reset_config_cache",
]
