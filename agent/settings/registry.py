"""开关注册表——**开关中心的唯一事实源**（TASK-S7-01 步骤 1）

【不易（本模块的地位）】
    云枢的开关散落在 `.env`（环境变量）与 `config.yaml` 两处，且读取点分布在
    一百多个模块里。本模块把它们**收拢成一张表**（`REGISTRY`），UI、`resolve()`
    与权限分级都以这张表为准。手写清单会漂移，故本表受两条机械约束守护
    （见 `tests/unit/test_settings_registry.py`）：

    1. **零缺口**：`scripts/scan_settings.py` 用 AST 从代码机械提取全部 env 读取点，
       提取结果必须 100% 落在本表内（缺口 = 测试失败）；
    2. **零重造**：`agent/monitoring/observability_config.py` 的
       `OBSERVABILITY_VALIDATION_RULES`（48 条，已有 path/校验/默认/说明）必须
       100% 被本表以 `config_path` 合并，且默认值逐条一致（缺一条 = 测试失败）。

【风险三级（唯一分级口径，与任务书 §二.步骤 1 逐字对齐）】
    | 级 | 含义 | 判定口径 |
    |---|---|---|
    | **A** | 可直接切 | 可观测采样/日志级别/非关键行为开关 |
    | **B** | 需二次认证 + 双人确认 | 自愈自动执行、熔断/回滚、关闭沙箱、审批豁免、自动合入、成本刹车阈值、**关闭即降低防护**的安全防线开关 |
    | **C** | 只读脱敏 | 密钥/凭据/口令、`*_URL`/外部端点、绝对路径；**永不返回明文** |

    B 级口径的补充说明（如实声明）：任务书列举的 B 类之外，本表把
    「关闭即降低防护的安全防线开关」也归入 B（如注入防线、egress 守卫、
    策略签名校验、审计链开关、租户隔离降级）。理由：这些开关的**切换动作
    本身就是一次安全姿态变更**，与「关沙箱」同性质。该口径在验收报告里已登记。

【变易（加开关只加一行）】
    新增开关 = 在 `REGISTRY` 里加一行 `_a/_b/_c/_p(...)`；`resolve()`、API 与前端
    零改动。分类只有六种（`CATEGORIES`），加分类需同步前端标签表。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# ════════════════════════════════════════════════════════════
#  分类（六类，与任务书 §二.步骤 1 一致）
# ════════════════════════════════════════════════════════════

CAT_SELF_HEALING = "self_healing_security"      # 自愈与安全
CAT_LEARNING = "learning_evolution"             # 学习与进化
CAT_ORCHESTRATION = "orchestration_planning"    # 编排与规划
CAT_SKILLS = "skills_retrieval"                 # 技能与检索
CAT_OBSERVABILITY = "observability_threshold"   # 可观测与阈值
CAT_EXTERNAL = "external_secrets"               # 外部依赖与密钥

#: 分类 → 人读标签（前端只展示本表给的标签，不自造）
CATEGORY_LABELS: Dict[str, str] = {
    CAT_SELF_HEALING: "自愈与安全",
    CAT_LEARNING: "学习与进化",
    CAT_ORCHESTRATION: "编排与规划",
    CAT_SKILLS: "技能与检索",
    CAT_OBSERVABILITY: "可观测与阈值",
    CAT_EXTERNAL: "外部依赖与密钥",
}

#: 分类展示顺序
CATEGORY_ORDER: Tuple[str, ...] = (
    CAT_SELF_HEALING, CAT_LEARNING, CAT_ORCHESTRATION,
    CAT_SKILLS, CAT_OBSERVABILITY, CAT_EXTERNAL,
)

# ════════════════════════════════════════════════════════════
#  风险级
# ════════════════════════════════════════════════════════════

RISK_A = "A"        # 可直接切
RISK_B = "B"        # 需二次认证 + 双人确认
RISK_C = "C"        # 只读脱敏

RISK_LABELS: Dict[str, str] = {
    RISK_A: "A 可直接切",
    RISK_B: "B 需二次认证 + 双人确认",
    RISK_C: "C 只读脱敏",
}

#: 风险级 → 是否要求二次认证（B 级；A/C 级不要求）
SECOND_FACTOR_RISKS = frozenset({RISK_B})

#: 生效方式
EFFECT_HOT = "hot"                  # 下次读取即生效
EFFECT_RESTART = "needs_restart"    # 需重启进程
EFFECT_NEXT_TASK = "next_task"      # 下一任务/下一轮生效

EFFECT_LABELS: Dict[str, str] = {
    EFFECT_HOT: "下次读取即生效",
    EFFECT_RESTART: "需重启进程后生效",
    EFFECT_NEXT_TASK: "下一任务生效",
}

# ════════════════════════════════════════════════════════════
#  校验器
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Validator:
    """值校验器（UI 据此渲染控件，API 据此拒绝非法值）

    kind:
        - `bool` 布尔
        - `int` / `float` 数值（可带 min/max）
        - `str` 任意字符串
        - `enum` 枚举（choices 非空）
        - `path` 路径（只读展示，不接受 UI 修改）
        - `regex` 正则约束
    """

    kind: str
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Tuple[str, ...] = ()
    pattern: str = ""
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.min is not None:
            out["min"] = self.min
        if self.max is not None:
            out["max"] = self.max
        if self.choices:
            out["choices"] = list(self.choices)
        if self.pattern:
            out["pattern"] = self.pattern
        if self.note:
            out["note"] = self.note
        return out


BOOL_V = Validator("bool")


def _range_validator(minimum: float, maximum: float, *, kind: str = "float",
                     note: str = "") -> Validator:
    return Validator(kind, min=minimum, max=maximum, note=note)


def _int_range(minimum: float, maximum: float) -> Validator:
    return Validator("int", min=minimum, max=maximum)


def _enum(*choices: str) -> Validator:
    return Validator("enum", choices=tuple(choices))


# ════════════════════════════════════════════════════════════
#  SettingSpec
# ════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SettingSpec:
    """一个开关的完整元数据（UI 上屏、权限分级、审计载荷的唯一来源）

    Attributes:
        key: 稳定键（URL 安全）。有 env 名时即 env 名；仅 config 项时为点分路径。
        category: 六分类之一（`CATEGORY_LABELS` 的键）。
        type: `bool|int|float|str|path`。
        default: **声明默认值**；`None` 表示「代码逻辑/配置文件决定」，
            不由本表编造（口径纪律：缺位记 None，不以 0 冒充）。
        risk: `A|B|C`。
        description: 人读说明（UI 必须展示，不允许空）。
        env_name: 环境变量名（无则空）。
        config_path: `config.yaml` / `observability_config` 的点分路径（无则空）。
        needs_restart: 是否必须重启进程才能生效。
        owner_module: 归属模块（相对仓库根，UI 展示"属于哪个子系统"）。
        validator: 值校验器。
        secret: 是否密钥类（C 级；值永不返回明文）。
        impact: 影响面（B 级二次确认面板展示）。
        rollback: 回滚方式说明。
        apply_mode: 生效方式（`EFFECT_*`）；为空时由 `needs_restart` 推导。
        dynamic_prefix: 动态家族前缀（如 `SKILLS_ASSESS_`），非空表示本条目
            代表一族动态开关，UI 只展示不改。
    """

    key: str
    category: str
    type: str
    default: Any
    description: str
    risk: str = RISK_A
    env_name: str = ""
    config_path: str = ""
    needs_restart: bool = False
    owner_module: str = ""
    validator: Validator = field(default_factory=lambda: BOOL_V)
    secret: bool = False
    impact: str = ""
    rollback: str = ""
    apply_mode: str = ""
    dynamic_prefix: str = ""

    def __post_init__(self) -> None:
        """★ fail-fast：任何一条元数据不完整/非法都在**建表时**抛错

        Why 放在 dataclass 而不是只放在 `_build_registry()`：
        单测与其它模块可能直接构造 `SettingSpec`（如合成 config-only 项），
        把校验放进结构体本身，才不会出现"绕过建表检查的半成品条目"。
        """
        if not str(self.description or "").strip():
            raise ValueError(f"开关 {self.key or '<无 key>'} 缺少说明（description 必填）")
        if not self.key:
            raise ValueError("开关缺少 key")
        if self.risk not in RISK_LABELS:
            raise ValueError(f"开关 {self.key} 风险级非法：{self.risk}")
        if self.category not in CATEGORY_LABELS:
            raise ValueError(f"开关 {self.key} 分类非法：{self.category}")
        if self.type not in ("bool", "int", "float", "str", "path"):
            raise ValueError(f"开关 {self.key} 类型非法：{self.type}")
        if not (self.env_name or self.config_path or self.dynamic_prefix):
            raise ValueError(
                f"开关 {self.key} 既无 env_name 也无 config_path / dynamic_prefix")
        if self.apply_mode and self.apply_mode not in EFFECT_LABELS:
            raise ValueError(f"开关 {self.key} 生效方式非法：{self.apply_mode}")

    # ── 派生属性 ──

    @property
    def editable(self) -> bool:
        """是否允许 UI 修改（风险级 + 是否只读族的判定在 resolver 里最终裁决）"""
        return self.risk in (RISK_A, RISK_B) and not self.dynamic_prefix

    @property
    def env_only(self) -> bool:
        """仅支持环境变量（有 env_name 但无 config_path）"""
        return bool(self.env_name) and not bool(self.config_path)

    @property
    def effect(self) -> str:
        if self.apply_mode:
            return self.apply_mode
        return EFFECT_RESTART if self.needs_restart else EFFECT_HOT

    @property
    def effect_label(self) -> str:
        return EFFECT_LABELS.get(self.effect, self.effect)

    @property
    def requires_second_factor(self) -> bool:
        return self.risk in SECOND_FACTOR_RISKS

    @property
    def requires_dual_approval(self) -> bool:
        return self.risk in SECOND_FACTOR_RISKS

    def _public_default(self) -> Any:
        """默认值的对外投影。

        **C 级（只读脱敏）一律不投影默认值原文（返回 None）**：C 级涵盖密钥 /
        路径 / 端点，其 default 可能是真实可用值；且当 `.env` 里的值恰好等于
        默认值（按默认配置填写的常见情形）时，`masking.assert_no_plaintext`
        会在投影里命中该字符串并抛错，使 `GET /api/cp/settings` **必现 500**
        （2026-09-13 实测：`ERROR_REPORTING_FILE_PATH` 触发，原因是此前只对
        `secret=True` 屏蔽，路径类 C 级项 `secret=False` 被漏掉）。
        故按"C 级永不返回明文"的统一纪律处理；A/B 级照常投影（可编辑项
        的默认值有 UI 价值）。
        """
        if self.risk == RISK_C:
            return None
        return self.default

    def to_public_dict(self) -> Dict[str, Any]:
        """元数据投影（**不含值**；值由 resolver 注入，C 级永不含明文）"""
        return {
            "key": self.key,
            "category": self.category,
            "category_label": CATEGORY_LABELS.get(self.category, self.category),
            "type": self.type,
            "default": self._public_default(),
            "risk": self.risk,
            "risk_label": RISK_LABELS.get(self.risk, self.risk),
            "description": self.description,
            "env_name": self.env_name,
            "config_path": self.config_path or "",
            "env_only": self.env_only,
            "needs_restart": bool(self.needs_restart),
            "effect": self.effect,
            "effect_label": self.effect_label,
            "editable": self.editable,
            "secret": bool(self.secret),
            "owner_module": self.owner_module,
            "validator": self.validator.to_dict(),
            "requires_second_factor": self.requires_second_factor,
            "requires_dual_approval": self.requires_dual_approval,
            "impact": self.impact,
            "rollback": self.rollback,
            "dynamic_prefix": self.dynamic_prefix,
        }


# ════════════════════════════════════════════════════════════
#  构造助手（加开关只加一行）
# ════════════════════════════════════════════════════════════

_ENV_LIKE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

_BOOL_SUFFIXES = (
    "_ENABLED", "_DRY_RUN", "_AUTO_RECORD", "_DEEP_SCAN", "_USE_ONNX",
    "_REQUIRE_SIGNATURE", "_ALLOW_DEGRADE", "_HIDE_SNIPPET", "_ENFORCE_PUBLISH",
    "_ARCHIVE", "_MIRROR", "_WATCH", "_GUARD", "_AUDIT", "_LOG", "_PROMETHEUS",
    "_LOGGING", "_GENERATE", "_FIT", "_AUTOGEN", "_EVENTS", "_SAMPLE_RATE",
)
_INT_SUFFIXES = (
    "_DAYS", "_HOURS", "_SECONDS", "_MS", "_MIN", "_COUNT", "_SIZE", "_LEN",
    "_TOKENS", "_SAMPLES", "_RETRIES", "_ROUNDS", "_INTERVAL", "_TTL", "_CAP",
    "_BATCH", "_PORT", "_TOP_N", "_PER_TOOL", "_MAX_DAILY", "_SCAN_LIMIT",
    "_GENERATIONS", "_RETENTION_WEEKS", "_SAMPLES_DIR", "_WINDOW_DAYS",
)
_FLOAT_SUFFIXES = (
    "_RATIO", "_RATE", "_PCT", "_SCORE", "_THRESHOLD", "_ALPHA", "_SIM",
    "_BACKOFF", "_RISE_PCT", "_DROP_PCT", "_FREQUENCY", "_POWER",
)
_PATH_SUFFIXES = (
    "_PATH", "_DIR", "_ROOT", "_STORE", "_FILE", "_LOG_PATH", "_ENDPOINT",
    "_COEFFICIENTS", "_PRICES", "_PATTERNS", "_DIRS", "_RECIPIENTS",
    "_MODEL", "_VARIANT", "_PROTOCOL", "_EXPORTER", "_BACKEND", "_MODE",
    "_STRATEGY", "_EVALUATOR", "_LEVEL", "_SCOPE", "_CHANNEL", "_ICON",
    "_USERNAME", "_HOST", "_FROM", "_KEY", "_DSN", "_TOKEN", "_CODE",
    "_CLI", "_WORKSPACE", "_PROVIDER", "_TYPE", "_LAYERS",
)


def _infer_type(key: str, default: Any) -> str:
    """类型推断（显式默认值优先；无默认值时按命名后缀推断）

    推断只影响 UI 控件与入参校验的严格度，不改变任何既有行为。
    """
    if isinstance(default, bool):
        return "bool"
    if isinstance(default, int):
        return "int"
    if isinstance(default, float):
        return "float"
    if isinstance(default, str):
        return "str"
    if key.endswith(_BOOL_SUFFIXES):
        return "bool"
    if key.endswith(_FLOAT_SUFFIXES):
        return "float"
    if key.endswith(_INT_SUFFIXES):
        return "int"
    if key.endswith(_PATH_SUFFIXES):
        return "str"
    return "str"


def _infer_validator(key: str, type_: str, default: Any) -> Validator:
    if type_ == "bool":
        return BOOL_V
    if type_ == "int":
        return Validator("int")
    if type_ == "float":
        return Validator("float")
    if key.endswith(("_PATH", "_DIR", "_ROOT", "_STORE", "_FILE")):
        return Validator("path", note="路径项：UI 只读展示")
    return Validator("str")


def _mk(key: str, category: str, default: Any, description: str, *,
        risk: str = RISK_A, env_name: Optional[str] = None,
        config_path: str = "", needs_restart: bool = False,
        owner: str = "", secret: bool = False, impact: str = "",
        rollback: str = "", validator: Optional[Validator] = None,
        type_: str = "", apply_mode: str = "",
        dynamic_prefix: str = "") -> SettingSpec:
    """内部构造器（三个语义化助手 `_a/_b/_c` 都走这里）"""
    name = env_name
    if name is None:
        name = key if key and all(ch in _ENV_LIKE for ch in key) else ""
    resolved_type = type_ or _infer_type(key, default)
    return SettingSpec(
        key=key, category=category, type=resolved_type, default=default,
        description=description, risk=risk, env_name=name,
        config_path=config_path, needs_restart=needs_restart,
        owner_module=owner, validator=validator or _infer_validator(
            key, resolved_type, default),
        secret=secret, impact=impact, rollback=rollback,
        apply_mode=apply_mode, dynamic_prefix=dynamic_prefix)


def _a(key: str, category: str, default: Any, description: str,
       **kw: Any) -> SettingSpec:
    """A 级：可直接切"""
    return _mk(key, category, default, description, risk=RISK_A, **kw)


def _b(key: str, category: str, default: Any, description: str,
       **kw: Any) -> SettingSpec:
    """B 级：需二次认证 + 双人确认"""
    kw.setdefault("impact", f"影响面：{description}（切换即改变系统风险姿态）")
    kw.setdefault("rollback", f"回滚：POST /api/cp/settings/{key}/reset 清除覆盖层")
    return _mk(key, category, default, description, risk=RISK_B, **kw)


def _c(key: str, category: str, default: Any, description: str,
       **kw: Any) -> SettingSpec:
    """C 级：只读脱敏（密钥/端点/路径）"""
    return _mk(key, category, default, description, risk=RISK_C, **kw)


def _secret(key: str, category: str, description: str, *,
            default: Any = None, env_name: Optional[str] = None,
            owner: str = "") -> SettingSpec:
    """C 级密钥类（值永不返回明文，UI 只显示"是否已配置"+掩码）"""
    return _c(key, category, default, description, secret=True,
              env_name=env_name, owner=owner)


# ════════════════════════════════════════════════════════════
#  注册表本体
# ════════════════════════════════════════════════════════════
# 纪律：本表**逐条**对应代码里的真实读取点（由 scripts/scan_settings.py 机械
#       提取后人工补类别/风险/说明）。`owner` 是该开关的读取点所在模块。

_REGISTRY_ROWS: List[SettingSpec] = [
    # ────────────────────────────────────────────────────────
    #  一、自愈与安全（S4/S6 域的治理与防护开关）
    # ────────────────────────────────────────────────────────

    # 自愈执行面（B：自愈自动执行）
    _b("CP_HEALING_LEVELS_ENABLED", CAT_SELF_HEALING, True,
       "自愈分级（L0-L5）总开关；关闭后自愈动作只出建议不执行",
       owner="agent/self_healing/levels.py"),
    _b("APPROVAL_ENABLED", CAT_SELF_HEALING, True,
       "技能管理审批门总开关；关闭等于审批豁免（所有变更直接放行）",
       owner="agent/skills_mgmt/approval.py",
       impact="影响面：技能/资产变更是否还需人类审批"),
    _b("CP_APPROVAL_CSRF_ENABLED", CAT_SELF_HEALING, True,
       "审批面 CSRF 防护开关；关闭即降低审批入口防伪能力",
       owner="agent/security/approval_session.py"),
    _b("CP_APPROVAL_REQUIRE_AUTHORITATIVE", CAT_SELF_HEALING, False,
       "审批是否强制要求权威身份来源（关闭时允许降级身份审批）",
       owner="agent/server_routes/routes_approval.py"),
    _b("CP_APPROVAL_LINK_TTL_SECONDS", CAT_SELF_HEALING, None,
       "一次性审批链接时效上限（秒）；放宽即延长可重放窗口",
       owner="agent/security/approval_session.py",
       validator=_range_validator(30, 900, note="§5.7⑦ 硬上限 900s")),
    _b("CP_APPROVAL_SESSION_TTL_SECONDS", CAT_SELF_HEALING, None,
       "审批会话时效（秒）；放宽即延长会话窗口",
       owner="agent/security/approval_session.py",
       validator=_range_validator(60, 3600)),
    _secret("CP_APPROVAL_SECOND_FACTOR_CODE", CAT_SELF_HEALING,
            "二次认证校验码（S4-01 审批面）；只读脱敏，永不返回明文",
            owner="agent/security/approval_session.py"),

    # 安全防线（B：关闭即降低防护）
    _b("CP_POLICY_GATEWAY_ENABLED", CAT_SELF_HEALING, False,
       "策略网关（Policy as Code 执行点）总开关",
       owner="agent/permission_system.py"),
    _b("CP_POLICY_BUILTIN_INVARIANTS", CAT_SELF_HEALING, True,
       "内置策略不变量校验；关闭后不再拒绝违反不变量的策略",
       owner="agent/policy/store.py"),
    _b("CP_POLICY_REQUIRE_SIGNATURE", CAT_SELF_HEALING, False,
       "策略文件是否强制验签；关闭即接受未签名策略",
       owner="agent/policy/signing.py"),
    _b("CP_POLICY_TAINT_ENABLED", CAT_SELF_HEALING, True,
       "污点传播（Taint）防护总开关",
       owner="agent/policy/taint.py"),
    _b("CP_POLICY_TAINT_DEEP_SCAN", CAT_SELF_HEALING, False,
       "污点深度扫描；关闭后只做浅层标记",
       owner="agent/policy/taint.py"),
    _b("CP_POLICY_TAINT_TTL_SECONDS", CAT_SELF_HEALING, None,
       "污点标记存活时长（秒）",
       owner="agent/policy/taint.py"),
    _b("CP_POLICY_EGRESS_GUARD", CAT_SELF_HEALING, True,
       "egress 外发守卫总开关（含数据外带拦截）",
       owner="agent/guardrails/egress_guard.py"),
    _b("CP_GUARDRAILS_EGRESS_CHAIN", CAT_SELF_HEALING, True,
       "egress 拦截链（多级校验）开关",
       owner="agent/guardrails/egress_chain.py"),
    _b("CP_GUARDRAILS_GUARD_CONTEXT", CAT_SELF_HEALING, False,
       "注入防线：上下文侧防护开关",
       owner="agent/guardrails/injection_defense.py"),
    _b("CP_GUARDRAILS_GUARD_TOOL", CAT_SELF_HEALING, True,
       "注入防线：工具侧防护开关",
       owner="agent/guardrails/injection_defense.py"),
    _b("CP_GUARDRAILS_INSTRUCTION_DATA", CAT_SELF_HEALING, True,
       "指令/数据分离防护开关",
       owner="agent/guardrails/instruction_data.py"),
    # 【裁定 D-20260921-09（2026-09-21）】本项默认值**以代码为准** ⇒ True。
    # 依据：agent/guardrails/foreign_taint.py:267-269 _env_flag(name, default="1")
    # 在 :290 以 _env_flag(ENV_ENABLED) 取值 ⇒ 开关未设置时 enabled=True；
    # 且注册表默认值**从不写入 os.environ**（bootstrap.py:52-56 未发现覆盖层
    # 即直接返回；resolver.py:381-385 仅当覆盖层存在条目才写 env）。
    # 旧值 False 会让设置面板显示"污点账已关"而它实际是开的 —— 声明与事实相反，
    # 故按代码对齐（只改默认值，不动描述文案与 owner）。
    _b("CP_GUARDRAILS_FOREIGN_TAINT", CAT_SELF_HEALING, True,
       "外部来源污点标记总开关",
       owner="agent/guardrails/foreign_taint.py"),
    _b("CP_GUARDRAILS_FOREIGN_TAINT_MAX_MARKS", CAT_SELF_HEALING, None,
       "单条内容允许的最大污点标记数",
       owner="agent/guardrails/foreign_taint.py", validator=Validator("int")),
    _b("CP_GUARDRAILS_FOREIGN_TAINT_TTL_SECONDS", CAT_SELF_HEALING, None,
       "外部污点标记存活时长（秒）",
       owner="agent/guardrails/foreign_taint.py", validator=Validator("int")),
    _b("CP_GUARDRAILS_BOUNDARY_TTL_SECONDS", CAT_SELF_HEALING, None,
       "「永不自动化五类」单次确认凭据时效（秒）；放宽即延长确认窗口",
       owner="agent/guardrails/boundary_words.py",
       validator=_range_validator(10, 60, kind="int", note="60s 硬上限")),
    _b("CP_GUARDRAILS_BOUNDARY_WORDS", CAT_SELF_HEALING, True,
       "边界词（五类永不自动化动作）检测开关",
       owner="agent/guardrails/boundary_words.py"),

    # ── TASK-07 安全接线（SSRF / 注入隔离 / 沙箱收口）新增开关 ──
    # 【为什么全部登记为 B 级】它们都是"关闭即降低防护姿态"的开关：关掉
    # `CP_SSRF_GUARD` 等于出站可以打 `169.254.169.254`（云元数据）；
    # 关掉 `CP_GUARDRAILS_MARK_TOOL_RESULTS` 等于让 `check_text` 退回"恒放行"
    # （注入隔离变摆设）；把 `CP_SANDBOX_SHELL_MODE` 置 off 等于 shell 回到无沙箱。
    _b("CP_SSRF_GUARD", CAT_SELF_HEALING, True,
       "SSRF 守卫总开关（出站目标判定的唯一口径）；关闭即允许访问私有/元数据网段",
       owner="agent/guardrails/ssrf_guard.py",
       impact="影响面：全部出站的地址判定（含云元数据 169.254.169.254 / 环回 / 内网段）"),
    _b("CP_SSRF_STRICT_DNS", CAT_SELF_HEALING, False,
       "SSRF 严格 DNS 模式：**解析不出结论**时也拒绝出站（默认关：解析失败通常只是网络不通）",
       owner="agent/guardrails/ssrf_guard.py"),
    _b("CP_SSRF_ALLOW_HOSTS", CAT_SELF_HEALING, None,
       "SSRF 白名单（逗号分隔的域名；支持 *.suffix）。用于确有必须访问的内网服务的部署",
       owner="agent/guardrails/ssrf_guard.py"),
    _b("CP_SSRF_DNS_TIMEOUT", CAT_SELF_HEALING, None,
       "SSRF 守卫的 DNS 解析超时（秒）；过大即把出站请求的等待时间拉长",
       owner="agent/guardrails/ssrf_guard.py",
       validator=_range_validator(1, 30, kind="float")),
    _b("CP_GUARDRAILS_MARK_TOOL_RESULTS", CAT_SELF_HEALING, True,
       "工具结果强制写外来污点账；关闭后 check_text 会退回恒放行（注入隔离形同虚设）",
       owner="agent/guardrails/untrusted_ingest.py",
       impact="影响面：网页/MCP/检索三类外来内容是否进入污点账"),
    _b("CP_SANDBOX_SHELL_MODE", CAT_SELF_HEALING, "shadow",
       "shell_execute 的沙箱档位：off（不接线）/ shadow（只告警）/ enforce（命中即拒绝）",
       owner="agent/tools/shell_tools.py"),
    _b("CP_TOOL_SANDBOX_ALLOWED_ENFORCE", CAT_SELF_HEALING, True,
       "受限会话内是否消费 YAML 的 sandbox_allowed=false（拒绝该能力）",
       owner="agent/tool_gate.py"),
    _b("CP_ESCAPE_GUARD", CAT_SELF_HEALING, False,
       "逃逸（escape）拦截守卫开关",
       owner="agent/observability/escape.py"),
    _b("MEMORY_TENANCY_ALLOW_DEGRADE", CAT_SELF_HEALING, False,
       "多租户隔离降级（关闭隔离强校验）；开启即降低租户隔离强度",
       owner="agent/memory/tenancy.py"),
    _b("CP_SUBAGENT_CRED_TTL_MAX", CAT_SELF_HEALING, None,
       "子智能体凭证最大存活时长（秒）",
       owner="agent/subagent/credentials.py", validator=Validator("float")),
    _b("YUNSHU_FEATURE_SANDBOX", CAT_SELF_HEALING, False,
       "工作区沙箱特性开关；关闭沙箱即放宽文件/命令隔离",
       owner="agent/server_routes/routes_workspace.py"),

    # 熔断与回滚（B：熔断/回滚）
    _b("ROLLBACK_SUCCESS_DROP_PCT", CAT_SELF_HEALING, 20.0,
       "成功率下降阈值（%）：超过即触发自动回滚",
       owner="agent/skills_mgmt/rollback.py",
       validator=_range_validator(0, 100)),
    _b("ROLLBACK_ERROR_RISE_PCT", CAT_SELF_HEALING, 50.0,
       "错误率上升阈值（%）：超过即触发自动回滚",
       owner="agent/skills_mgmt/rollback.py",
       validator=_range_validator(0, 1000)),
    _b("ROLLBACK_LATENCY_RISE_PCT", CAT_SELF_HEALING, 50.0,
       "延迟上升阈值（%）：超过即触发自动回滚",
       owner="agent/skills_mgmt/rollback.py",
       validator=_range_validator(0, 1000)),
    _b("ROLLBACK_WINDOW_MIN", CAT_SELF_HEALING, 1440,
       "回滚判定观察窗口（分钟）",
       owner="agent/skills_mgmt/rollback.py", validator=Validator("int")),
    _b("ROLLBACK_MAX_DAILY", CAT_SELF_HEALING, 2,
       "每日自动回滚次数上限（熔断刹车）",
       owner="agent/skills_mgmt/rollback.py", validator=Validator("int")),
    _c("ROLLBACK_STATE_PATH", CAT_SELF_HEALING, None,
       "回滚状态文件路径（绝对路径，只读）",
       owner="agent/skills_mgmt/rollback.py"),

    # 自动合入 / 自改写（B：自动合入类）
    _b("SKILLS_REVIEW_ENFORCE_PUBLISH", CAT_SELF_HEALING, None,
       "技能发布强制走评审门（关闭即允许未经评审发布）；config.yaml 路径 "
       "skills_mgmt.review.enforce_before_publish"
       "（agent/skills_mgmt/review_gate.py:158-161）",
       owner="agent/skills_mgmt/review_gate.py",
       config_path="skills_mgmt.review.enforce_before_publish"),
    _b("WF_SKILL_AUTO_UPGRADE_ENABLED", CAT_SELF_HEALING, None,
       "工作流技能自动升级（自动合入）开关",
       owner="agent/orchestrator/lifecycle_manager.py"),
    _b("VALUE_GUARD_ENABLED", CAT_SELF_HEALING, True,
       "价值守卫（技能价值闸门）开关；关闭即不再拦截低价值技能",
       owner="agent/skills_mgmt/value_guard.py"),
    _b("SKILL_CLEANUP_ENABLED", CAT_SELF_HEALING, False,
       "技能清理调度器总开关（自动归档/删除不再使用的技能）；config.yaml 路径 "
       "skills_mgmt.cleanup.enabled（agent/skills_mgmt/cleanup_scheduler.py:69/80）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.enabled"),
    _b("META_EDIT_MAX_FILES_PER_ROUND", CAT_SELF_HEALING, 1,
       "元编辑（核心自改写）每轮最大改动文件数",
       owner="agent/skills_mgmt/edit_policy.py", validator=Validator("int")),
    _b("META_EDIT_MAX_SKILLS_PER_ROUND", CAT_SELF_HEALING, None,
       "元编辑每轮最大技能数",
       owner="agent/skills_mgmt/meta_editor.py", validator=Validator("int")),
    _b("META_EDIT_MAX_TOKENS_PER_ROUND", CAT_SELF_HEALING, None,
       "元编辑每轮 token 上限",
       owner="agent/skills_mgmt/meta_editor.py", validator=Validator("int")),
    _b("META_EDIT_EVAL_MIN_SCORE", CAT_SELF_HEALING, None,
       "元编辑改动必须达到的最低评测分",
       owner="agent/skills_mgmt/meta_editor.py", validator=Validator("float")),
    _b("META_EDIT_STALL_ROUNDS", CAT_SELF_HEALING, None,
       "元编辑连续无进展轮数上限（超过即停）",
       owner="agent/skills_mgmt/meta_editor.py", validator=Validator("int")),
    _b("META_EDIT_BLOCKED_PATTERNS", CAT_SELF_HEALING, "",
       "元编辑禁止触碰的文件模式（逗号分隔）",
       owner="agent/skills_mgmt/edit_policy.py"),
    _b("META_EDIT_WHITELIST_DIRS", CAT_SELF_HEALING, "",
       "元编辑允许改动的目录白名单（逗号分隔）",
       owner="agent/skills_mgmt/edit_policy.py"),

    # ── S7-02 自修复 L1（补丁 PR）的 CP_REPAIR_* 策略上限 ──
    # 来源：`agent/repair/policy.py::policy_from_env()`（`ENV_PREFIX = "CP_REPAIR_"`）。
    # 风险口径：**放宽自动改动面 / 自动迭代轮数**的归 B（与 META_EDIT_* 同性质——
    # 自动化自改写类）；纯预算与读取参数（token 预算、超时、切片半径、读历史条数）归 A。
    # 注：这批开关的 env 名是「**二级转发家族**」形态（`_env_int(env, name, …)` →
    # `_env_text` 里 `ENV_PREFIX + name`）。机械提取器为此专门实现了转发链解析，
    # 并由 `tests/unit/test_settings_registry.py::test_two_level_family_chain_is_resolved`
    # 钉死——否则整族会以 `<unresolved>` **静默消失**在 UI 之外（S7-02 合并后实测命中）。
    _b("CP_REPAIR_MAX_CHANGED_FILES", CAT_SELF_HEALING, 3,
       "自动修复单个补丁最多改动的文件数（放宽即扩大自动改动面）",
       owner="agent/repair/policy.py", validator=_int_range(1, 100)),
    _b("CP_REPAIR_MAX_LINES_PER_FILE", CAT_SELF_HEALING, 120,
       "自动修复单文件最多改动行数（放宽即扩大自动改动面）",
       owner="agent/repair/policy.py", validator=_int_range(1, 10000)),
    _b("CP_REPAIR_MAX_ROUNDS", CAT_SELF_HEALING, 2,
       "自动修复最多迭代轮数（放宽即扩大自动改动面）",
       owner="agent/repair/policy.py", validator=_int_range(1, 20)),
    _a("CP_REPAIR_BUDGET_TOKENS", CAT_SELF_HEALING, 60000,
       "自动修复单次 token 预算", owner="agent/repair/policy.py",
       validator=_int_range(1, 10_000_000)),
    _a("CP_REPAIR_TIMEOUT_SECONDS", CAT_SELF_HEALING, 600.0,
       "自动修复单次超时（秒）", owner="agent/repair/policy.py",
       validator=_range_validator(1, 86400)),
    _a("CP_REPAIR_SLICE_RADIUS", CAT_SELF_HEALING, 40,
       "代码切片上下文半径（行）", owner="agent/repair/policy.py",
       validator=_int_range(0, 10000)),
    _a("CP_REPAIR_HISTORY_COMMITS", CAT_SELF_HEALING, 10,
       "读取历史提交条数（诊断用）", owner="agent/repair/policy.py",
       validator=_int_range(0, 1000)),

    # 安全告警（A：观测类）
    _a("CP_SECURITY_ALERTS_ENABLED", CAT_SELF_HEALING, True,
       "越权/安全告警聚合开关", owner="agent/security/alerts.py"),
    _a("CP_SECURITY_ALERT_THRESHOLD", CAT_SELF_HEALING, None,
       "安全告警触发阈值（次数）", owner="agent/security/alerts.py",
       validator=Validator("int")),
    _a("CP_SECURITY_ALERT_WINDOW_SECONDS", CAT_SELF_HEALING, None,
       "安全告警统计窗口（秒）", owner="agent/security/alerts.py",
       validator=Validator("float")),
    _a("CP_ESCAPE_WATCH", CAT_SELF_HEALING, None,
       "逃逸观测（只观察不拦截）开关",
       owner="agent/observability/escape.py"),

    # 安全域的只读路径 / 密钥（C）
    _secret("CP_POLICY_SIGNING_KEY", CAT_SELF_HEALING,
            "策略签名私钥路径（只读；永不返回明文）",
            owner="agent/policy/signing.py"),
    _secret("CP_POLICY_PUBLIC_KEY", CAT_SELF_HEALING,
            "策略验签公钥路径（只读）", owner="agent/policy/signing.py"),
    _c("CP_POLICY_FILE", CAT_SELF_HEALING, None,
       "策略文件路径（只读）", owner="agent/policy/store.py"),
    _c("CP_POLICY_DECISION_LOG", CAT_SELF_HEALING, None,
       "策略决策日志路径（只读）", owner="agent/policy/decisions.py"),
    _a("CP_POLICY_DECISION_LOG_ENABLED", CAT_SELF_HEALING, True,
       "策略决策日志（回放数据源）开关", owner="agent/policy/decisions.py"),
    _a("CP_POLICY_OBSERVE", CAT_SELF_HEALING, True,
       "策略决策可观测（观测者）开关", owner="agent/policy/engine.py"),
    _a("CP_POLICY_OBSERVE_SCOPE", CAT_SELF_HEALING, "all",
       "策略观测范围（all / governance）", owner="agent/policy/engine.py",
       validator=_enum("all", "governance")),
    _a("CP_POLICY_CACHE_SIZE", CAT_SELF_HEALING, None,
       "策略判定缓存条目上限", owner="agent/policy/engine.py",
       validator=Validator("int")),
    _c("CP_POLICY_INBOX_PATH", CAT_SELF_HEALING, None,
       "审批收件箱文件路径（只读）", owner="agent/policy/inbox.py"),
    _c("CP_POLICY_INBOX_BACKEND", CAT_SELF_HEALING, "jsonl",
       "审批收件箱后端（jsonl / sqlite）", owner="agent/policy/inbox.py",
       validator=_enum("jsonl", "sqlite")),
    _a("CP_POLICY_INBOX_DEDUPE_SECONDS", CAT_SELF_HEALING, None,
       "审批收件箱去重窗口（秒）", owner="agent/policy/inbox.py",
       validator=Validator("float")),

    # ── S11-01 原生扩展导入顺序固化（进程入口级崩溃防护）──────────────
    # 【读取点（唯一）】`agent/utils/native_preimport.py::is_enabled()` 内的
    #   `raw = os.environ.get(_ENV_KEY)`，`_ENV_KEY = "CP_NATIVE_PREIMPORT_ENABLED"`。
    #   **未设置**（`raw is None`）⇒ 代码里按**开启**处理 ⇒ 本表默认值取 `True`
    #   （bool），与真实读取点一致（`scripts/scan_settings.py` 机械核对读取点）。
    # 【语义】关闭 = 跳过 `numpy → pyarrow → pandas → sklearn` 的定序预导入，
    #   回到"谁先用到谁触发原生初始化"的旧行为（= 本模块引入之前的行为）。
    # 【生效方式】进程入口读取，故 needs_restart=True（改了必须重启进程）。
    # 【风险级裁定：B（拿不准取更严）】支撑依据与反例一并写清，便于复核：
    #   · 支撑 B：关闭后**长寿命进程**（app_server）若在已加载 torch / onnxruntime
    #     等重型原生库之后才首次 `import sentence_transformers`
    #     （→sklearn→pandas→pyarrow），可能被 `pyarrow\arrow.dll` 的原生初始化打成
    #     `0xC0000005 ACCESS_VIOLATION`：**进程被系统直接终止、没有 traceback**。
    #     这与同表 `CP_ARCHIVE_LOCK_ENABLED` / `CP_EVENTS_LOCK_ENABLED` /
    #     `CP_TRACE_LOCK_ENABLED` 的「关掉即失去完整性/存活强保护」**同型**，而那些
    #     条目按下方既有「风险级裁定口径（保守优先）」注释定为 B。
    #   · 反例（已考虑，未采纳）：同表 `CP_MODEL_FALLBACK_ENABLED` 定为 A —— 但关掉
    #     它只让调用**显式失败**、进程照常存活，不产生静默崩溃，故不同型。
    #   · 结论：A 降 B 只多一次确认成本；B 降 A 则可能让"关掉崩溃防护"变成随手可切。
    #     该裁定已随 S11-01 交付报告显式声明（口径变更需可追溯）。
    _b("CP_NATIVE_PREIMPORT_ENABLED", CAT_SELF_HEALING, True,
       "原生扩展导入顺序固化开关（默认开）；关闭后不做 numpy→pyarrow→pandas→"
       "sklearn 的定序预导入，长寿命进程可能被 arrow.dll 原生初始化打成 0xC0000005",
       owner="agent/utils/native_preimport.py", needs_restart=True),

    _c("CP_WATCHDOG_LOCK_PATH", CAT_SELF_HEALING, None,
       "看门狗单例锁文件路径（只读）",
       owner="agent/self_healing/watchdog_singleton.py"),
    _c("CP_HEALING_INCIDENTS_DIR", CAT_SELF_HEALING, None,
       "自愈事故卡目录（只读）", owner="agent/self_healing/levels.py"),
    _c("CP_RELEASE_BUNDLES_DIR", CAT_SELF_HEALING, None,
       "发布包（release bundle，回滚原子单位）目录（只读）",
       owner="agent/self_healing/release_bundle.py"),
    _c("CP_SAGA_JOURNAL_DIR", CAT_SELF_HEALING, None,
       "Saga 事务日志目录（只读）", owner="agent/self_healing/saga.py"),
    _c("CP_SUBAGENT_WORKSPACE", CAT_SELF_HEALING, None,
       "子智能体工作区路径（绝对路径，只读）",
       owner="agent/subagent/executor.py"),
    _c("YUNSHU_AGENT_LINES_DIR", CAT_ORCHESTRATION, None,
       "主线档案目录覆盖（绝对路径，只读；测试隔离用）",
       owner="agent/lines/registry.py"),
    _secret("CP_SUBAGENT_AGENT_CLI", CAT_SELF_HEALING,
            "子智能体 agent CLI 可执行路径（只读脱敏）",
            owner="agent/subagent/channel.py"),
    _a("CP_SUBAGENT_MAX_TURNS", CAT_SELF_HEALING, None,
       "子智能体单任务最大轮数", owner="agent/subagent/channel.py",
       validator=Validator("int")),
    # ── 集中式工具闸门（agent/tool_gate.py，接入点 = agent/tools/__init__.py::call()）──
    _b("CP_TOOL_GATE_ENABLED", CAT_SELF_HEALING, True,
       "集中式工具闸门总开关；置 0/false 等于关掉闸门的显式拒绝判定",
       owner="agent/tool_gate.py"),
    _b("CP_TOOL_GATE_STRICT", CAT_SELF_HEALING, False,
       "工具闸门严格模式：把 PermissionGateway 的 RBAC/ABAC 接进工具主链路（默认关闭）",
       owner="agent/tool_gate.py",
       impact="影响面：开启后未在角色白名单内的工具会被拒；请先确认角色清单与作息相关的 ABAC 规则"),
    # 【B 级理由】审批边界开关：它决定"requires_approval 的工具调用是被拒绝还是只告警"。
    #   默认**开启**（2026-09-17 反转）：审批闭环接通后，拦截是一条能走通的路径——
    #   闸门幂等挂单 → 人工在「治理 → 审批收件箱」裁决 → 模型原样重试即放行（单次有效）。
    #   此前默认关闭的唯一理由是"审批 UI 未接到工具调用上 ⇒ 拦截=永远失败"，该理由已消失。
    #   回滚 = 置 0/false/no/off（一处环境变量，不写数据文件）。
    _b("CP_TOOL_GATE_APPROVAL_ENFORCE", CAT_SELF_HEALING, True,
       "工具闸门审批边界开关：默认开启 ⇒ 描述符 trust.requires_approval 与治理平面 "
       "needs_approval 的工具调用走审批闭环（挂单 → 人工裁决 → 原样重试即放行，单次有效）；"
       "置 0/false/no/off = 退回「只告警不拦截」",
       owner="agent/tool_gate.py",
       impact="影响面：开启=审批边界生效（critical 工具如 shell_execute 需人工确认一次）；"
              "关闭=边界只告警不拦截"),
    # 【B 级理由】确认分级开关（TASK-06）：它决定 13 个 risk: high 工具是否真的逐次
    #   确认。默认**开启** —— 默认关闭等于"13 个 high 完全不触发确认"这个缺陷仍在。
    _b("CP_TOOL_CONFIRM_LEVEL_ENFORCE", CAT_SELF_HEALING, True,
       "工具侧确认分级 L0–L3 强制开关（TASK-06 / v1.4 §10.2）：默认开启 ⇒ "
       "data/tool_definitions/*.yaml 派生出的 confirm_level 生效（L0 免确认、"
       "L1 摘要确认可批量、L2 逐次确认单次有效、L3 默认禁止须显式预授权；"
       "13 个 risk: high 工具首次进入确认流）；置 0/false/no/off = 退回旧口径"
       "（只有 plane=govern / effect=extend / risk=critical 挂单）",
       owner="agent/tool_gate.py",
       impact="影响面：开启=write_file/edit/git/apply_patch/run_program 等 13 个 "
              "risk: high 工具的调用会要求人工确认（交互路径挂单，非交互路径直接拒绝）；"
              "关闭=退回改动前的二值口径。**实测当前部署没有定时任务**（0 条），"
              "故不存在「夜间任务集体失败」的既有风险",
       rollback="回滚：置 CP_TOOL_CONFIRM_LEVEL_ENFORCE=0（立即回到旧口径，不写数据文件）；"
                "或先置 CP_TOOL_CONFIRM_LEVEL_SHADOW=1 只告警不拦截"),
    # 【A 级理由】影子模式是"更宽"而非"更严"的开关：打开它只会让拦截变少（只告警），
    #   属于迁移期观测手段，不改变风险姿态，故不需要二次认证。
    _a("CP_TOOL_CONFIRM_LEVEL_SHADOW", CAT_SELF_HEALING, False,
       "工具侧确认分级**影子模式**（TASK-06 §6 迁移第一步）：置 1/true/yes/on ⇒ "
       "只记录「若按新规则将会要求确认」（告警 + 审计 decision=shadow_alert），"
       "**不实际拦截**。用于在真正开启拦截前先观察一个周期的命中面",
       owner="agent/tool_gate.py",
       impact="影响面：开启=分级判定只告警不拦截（不改变任何调用结果）；关闭=按 "
              "confirm_level 实际拦截",
       rollback="回滚：置 CP_TOOL_CONFIRM_LEVEL_SHADOW=0"),
    # ── 服务账号（第四类主体；agent/security/service_account.py，TASK-06）──
    # 【A 级理由（2026-09-22 新增）】这是"逐工具名单"，不是"整层关掉"：
    #   · 每个工具一行，**单次改动的爆炸半径就是一个工具**；一次点错不会放掉别的能力；
    #   · 真正"整层放宽"的两个入口仍是 **B 级**（CP_TOOL_GATE_APPROVAL_ENFORCE /
    #     CP_TOOL_CONFIRM_LEVEL_ENFORCE）—— 最粗那根杠杆没有被降级；
    #   · B 级要求**两位不同的人工**（`second_approver_must_differ`）。本机是单操作者
    #     部署，登记成 B 只会得到一个"永远批不过"的死按钮 —— 那比 A 级更坏：
    #     人会以为功能坏了，转而直接改 .env（绕过全部留痕）。
    #   · 变更仍受 `settings.change` 矩阵约束：**只有 human 放行**（auto / sub_agent /
    #     service_account 一律拒）⇒ 云枢无法给自己松绑；每次改动进审计链。
    _a("CP_TOOL_CONFIRM_LEVEL_EXEMPT", CAT_SELF_HEALING, "",
       "确认分级**豁免名单**（逗号分隔的工具名 / 能力 id）：列出的能力免「摘要确认」，"
       "不再进审批收件箱。界面入口：治理面板的「能力地图」与主线管理里的逐工具徽章开关",
       env_name="CP_TOOL_CONFIRM_LEVEL_EXEMPT", owner="agent/tool_gate.py",
       impact="影响面：只放大分级层里的「摘要确认」——权限策略黑名单 / 描述符 "
              "requires_approval（shell_execute、run_sandbox、ext_* 等仍要人工确认）/ "
              "伦理硬规则（禁 rm -rf /、格式化、关机…）/ RBAC 严格模式**全部不受影响**；"
              "子代理自己发出的工具调用仍各自过闸门",
       rollback="回滚：POST /api/cp/settings/CP_TOOL_CONFIRM_LEVEL_EXEMPT/reset 清除覆盖层，"
                "或把值置空（= 一个都不豁免）"),
    _c("CP_SERVICE_ACCOUNTS_PATH", CAT_SELF_HEALING,
       "data/service_accounts.json",
       "service_account 凭据文件路径（**加密落盘**，Fernet；只记账号 scope、"
       "已签发 jti 与吊销黑名单，**不存令牌原文**）。缺省在仓库 data/ 下；"
       "测试应指向临时目录（D6 不碰生产数据）",
       owner="agent/security/service_account.py"),
    _secret("CP_SERVICE_ACCOUNT_KEY", CAT_SELF_HEALING,
            "service_account 令牌的 HMAC-SHA256 签名密钥。未配置 ⇒ 本进程生成临时密钥，"
            "该进程签发的 SA 令牌在重启后一律失效（**不放宽任何权限**，故按可用性优先"
            "并留 WARNING）。生产使用必须显式配置",
            owner="agent/security/service_account.py"),
    _secret("CP_SERVICE_ACCOUNT_ENCRYPT_KEY", CAT_SELF_HEALING,
            "service_account **凭据文件**的 Fernet 加密密钥（标准 Fernet key，"
            "即 cryptography.fernet.Fernet.generate_key() 的产物）。未配置 ⇒ "
            "凭据明文落盘并在日志告警（不放宽任何权限判定）。"
            "【不易】未复用 Yunshu_ENCRYPT_KEY：该名含小写，"
            "scripts/scan_settings.py 的常量模式 ^[A-Z][A-Z0-9_]*$ 解析不了它，"
            "用作读取点会使零缺口守卫多出一个 <unresolved> 家族",
            owner="agent/security/service_account.py"),
    # 【A 级理由】这是"让一个谎报成功的工具恢复可用"的开关，方向是**更严**（默认 0
    #   = 拒绝创建任务），打开它不会放宽任何权限，只会让一个已声明的能力真的生效。
    _a("CP_SCHEDULER_ACTION_EXECUTION", CAT_SELF_HEALING, False,
       "定时任务 action 是否真的会被执行（TASK-06 E13）。实测："
       "agent/scheduling.py::_execute_task 的 action 分支是 pass ⇒ 工具面 "
       "schedule_task 创建的任务会按时触发但**不执行任何动作**（谎报成功）。"
       "默认 0 ⇒ schedule_task 直接返回 error_code=NOT_IMPLEMENTED 并拒绝创建，"
       "使模型不再收到「成功」；置 1/true/yes/on ⇒ 恢复创建（返回值显式带 "
       "action_execution_implemented=true），仅在运维已另行实现 action 分支后使用",
       owner="agent/tools/code_tools.py",
       impact="影响面：默认 0=schedule_task 不可用（拒绝创建，返回 NOT_IMPLEMENTED）；"
              "置 1=恢复创建定时任务，但动作是否真的执行取决于 action 分支是否已实现"),
    # 【B 级理由】可调用性过滤开关：它决定"声明为不可被 LLM 调用的工具是否真的从模型
    #   可见集里消失"。默认**开启**，且当前唯一被判否的 process_distill_run 本就是
    #   internal ⇒ 打开前后模型可见集完全相同（零行为变化，见
    #   tests/unit/test_tool_callability.py 的不变量断言）。回滚 = 置 0/false/no/off。
    _b("CP_TOOL_CALLABILITY_ENFORCE", CAT_SELF_HEALING, True,
       "工具可调用性过滤开关：默认开启 ⇒ data/tool_definitions/*.yaml 里 "
       "llm_callable=false / callable_mode=manual / 被权限策略拒绝的工具不进模型可见集"
       "（口径见 agent/lines/callability.py）；置 0/false/no/off = 退回「只隐藏 internal」",
       owner="agent/tools/__init__.py",
       impact="影响面：开启=按声明隐藏不可调用工具（当前与只隐藏 internal 等价）；"
              "关闭=声明只进清单、不影响模型可见集"),
    _a("CP_TOOL_APPROVAL_TTL_SEC", CAT_SELF_HEALING, 900,
       "工具审批单的有效期（秒）：人工批准后须在该窗口内完成那次调用，超时即失效需重新审批；"
       "默认 900（与审批会话 ≤15min 口径一致）",
       owner="agent/tool_approval.py"),
# 【2026-09-22 集成补登记：L2 跨进程消费互斥引入的新读取点】
# 该键由 `agent/tool_approval.py::_lock_timeout_seconds()` 读取（默认 5.0；负数/非法回落默认）。
# 必须登记的原因是可机械核验的：`tests/unit/test_settings_registry.py::TestMechanicalZeroGap`
# 要求"每个 os.environ 读取点都在登记表里"，否则开关中心与登记表之间会出现**无声缺口**
# （本次集成实测：L2 引入该键后那两条零缺口用例立刻变红，正是这个机制在起作用）。
_a("CP_TOOL_APPROVAL_LOCK_TIMEOUT_SEC", CAT_SELF_HEALING, 5.0,
   "审批消费台账**跨进程锁**的等锁上限（秒）：超过该时长仍拿不到锁即按 fail-closed 处理"
   "（**不得静默放行**）；默认 5.0，置 0 = 不等待（立即失败）",
   owner="agent/tool_approval.py"),
    _c("CP_TOOL_APPROVAL_USES_PATH", CAT_SELF_HEALING,
       "data/tool_approval_uses.jsonl",
       "工具审批消费台账路径（append-only，记录哪张审批单已被哪次调用消费）；"
       "审批流记录本身是决策权威，本台账只解决「批准不可重放」",
       owner="agent/tool_approval.py"),
    _a("CP_PERMISSION_DEFAULT_ROLE", CAT_SELF_HEALING, "guest",
       "严格模式下使用的角色（owner/admin/developer/guest）；非法值回退缺省值。"
       "【TASK-06（E15）】缺省值由 owner 改为 guest —— owner 的 allowed_tools 是 "
       "[\"*\"]（放行一切），使严格模式在缺省配置下形同虚设；治理面缺省必须是最小权限。"
       "需要旧行为请显式设为 owner",
       owner="agent/tool_gate.py"),
    _a("CP_PERMISSION_SESSION_SOURCE", CAT_SELF_HEALING, "cli",
       "严格模式下上报的会话来源（cli/web/api/scheduled），供 ABAC 的 session_source_in 规则判定",
       owner="agent/tool_gate.py"),
    # ────────────────────────────────────────────────────────
    #  能力层（TASK-05）：非 LLM 入口 + Loader + 知识库审计台账
    # ────────────────────────────────────────────────────────
    # 【B 级理由】它决定"整个 /capabilities/* 面（含 POST /capabilities/invoke）
    #   是否可达"。开启＝多出一组能触发真实能力执行的 HTTP 端点；关闭＝完全回到
    #   改动前（404）。默认**开启**是 E1 的要求（"关掉 LLM 后三条链路仍可用"
    #   必须在默认配置下成立），回滚只需置 0/false/no/off。
    _b("CP_CAPABILITY_API_ENABLED", CAT_SELF_HEALING, True,
       "能力层非 LLM 入口开关：默认开启 ⇒ /capabilities/tools、/capabilities/invoke、"
       "/capabilities/skills/search、/capabilities/<name>、/capabilities/health 可用；"
       "置 0/false/no/off ⇒ 这些端点全部不存在（404），平台行为回到改动前",
       owner="agent/server_routes/routes_capabilities.py",
       impact="影响面：是否对外暴露可查询/可调用能力的 HTTP 面（POST /capabilities/invoke "
              "会经 tools.call() 真实执行工具，闸门/审批/限流照常生效）"),
    # 【B 级理由】它决定"哪些传输层 Loader 参与能力调用"。全部禁掉会让
    #   location=remote 的能力一律 unhealthy（本地能力不受影响）。
    _a("CP_CAPABILITY_LOADER_DISABLED", CAT_SELF_HEALING, "",
       "禁用的 Loader 列表（逗号分隔，取值 local/stdio/sse/http）；空串=全部启用。"
       "用于逐 Loader 独立熔断与定位问题（TASK-05 §6 的回滚方案）",
       owner="agent/capregistry/loader.py",
       impact="影响面：被禁的传输层承载的能力会标 unhealthy（不阻断平台启动）"),
    _c("CP_CAPABILITY_STDIO_SERVER", CAT_SELF_HEALING,
       "mcp_services/yunshu_mcp_server.py",
       "stdio Loader 驱动 MCP 服务端的脚本路径（相对仓库根或绝对路径）",
       owner="agent/capregistry/loader.py"),
    _a("CP_CAPABILITY_STDIO_PREWARM", CAT_SELF_HEALING, 0,
       "stdio Loader 的预热池大小（常驻子进程数，0=按需启动 1 个）",
       owner="agent/capregistry/loader.py"),
    _c("CP_CAPABILITY_ENDPOINTS", CAT_SELF_HEALING, "",
       "远端 MCP 端点表：`kind:name=url` 逗号分隔（kind ∈ sse/http），"
       "如 `http:weather=http://127.0.0.1:9000/mcp`；空串=无远端端点登记",
       owner="agent/capregistry/loader.py"),
    _a("CP_CAPABILITY_HTTP_TIMEOUT", CAT_SELF_HEALING, 20,
       "远端 Loader（stdio/sse/http）的单次请求超时秒数",
       owner="agent/capregistry/loader.py", validator=Validator("int")),
    # 【A 级理由】它是**防护性上限**，不是能力开关：调大只会让单次响应更大更慢。
    #   为什么需要它：10,000 条合成扩容压测实测无条件全量信封 p99 = 2244ms
    #   （远超 v1.4 附录 C 的 100ms），而主键查询 p99 仅 0.001ms
    #   ⇒ 退化的是"一次全量序列化"，不是"查询"。故默认分页 500 条。
    _a("CP_CAPABILITY_DEFAULT_PAGE", CAT_SELF_HEALING, 500,
       "/capabilities/tools 的默认分页大小（1..2000）：未显式传 limit 时按此截断，"
       "响应里以 data.truncated=true 明示被截断（不静默）；调大即放大单次响应体与耗时",
       owner="agent/server_routes/routes_capabilities.py",
       validator=Validator("int")),
    # 【B 级理由】它决定"非交互来源（cron/CI）遇审批边界时是明确失败还是悬挂等单"。
    #   开启＝方案 A（返回明确错误码 denied，不产生永远等不到的单号）；
    #   关闭＝回到既有行为（挂单后非交互调用永远拿不到结果，即 TASK-05 记录的确定缺陷）。
    _b("CP_CAPABILITY_NONINTERACTIVE_APPROVAL", CAT_SELF_HEALING, True,
       "非交互调用的审批处置开关（TASK-05 方案 A）：默认开启 ⇒ 身份为 "
       "system/service_account 的调用遇到审批边界时返回明确错误码 denied + 可操作说明，"
       "不产生悬挂审批单；置 0 = 回到既有的『挂单后非交互调用等不到结果』行为",
       owner="agent/capregistry/invoke.py",
       impact="影响面：cron/CI 调用高危工具时是明确失败还是静默悬空"),
    _c("CP_KNOWLEDGE_AUDIT_LOG", CAT_SELF_HEALING, "data/audit/knowledge_audit.jsonl",
       "知识库审计的结构化审计台账路径（append-only JSONL；CI 面与 Agent 面共用）",
       owner="agent/knowledge/audit_entry.py"),
    _a("CP_SUBAGENT_DEFAULT_TOOLS",
       CAT_SELF_HEALING, "read_file,search_files,list_directory,get_file_info,grep",
       "委派默认授予子代理的工具子集（逗号分隔；空串 = 不授予任何工具，退化为纯推理委派）",
       owner="agent/tools/subagent_tools.py"),
    # 【B 级理由】它是"一次调用起 N 个真实子代理"的**爆炸半径上限**：调高即按倍数放大
    #   成本与副作用（与 CP_REPAIR_MAX_ROUNDS / META_EDIT_MAX_* 同类，故取更严的 B）。
    _b("CP_FAN_OUT_MAX_TASKS", CAT_SELF_HEALING, 16,
       "单次 fan_out（并行多线委派）最多派发的任务数（越界/非法值回退默认 16）",
       owner="agent/tools/fan_out_tools.py", validator=Validator("int"),
       impact="影响面：并发子代理数量与成本放大倍数（单次调用的爆炸半径）"),
    _c("APPROVAL_RECORDS_PATH", CAT_SELF_HEALING, None,
       "审批记录落盘路径（只读）", owner="agent/skills_mgmt/approval.py"),
    _c("SKILLS_REVIEW_AUDIT_FILE", CAT_SELF_HEALING, None,
       "技能评审审计文件路径（只读）；config.yaml 路径 "
       "skills_mgmt.review.audit_file（agent/skills_mgmt/review_gate.py:175-178）",
       owner="agent/skills_mgmt/review_gate.py",
       config_path="skills_mgmt.review.audit_file"),
    _c("VALUE_GUARD_RULES_PATH", CAT_SELF_HEALING, None,
       "价值守卫规则文件路径（只读）",
       owner="agent/skills_mgmt/value_guard.py"),
    _a("VALUE_GUARD_LLM_ENABLED", CAT_SELF_HEALING, False,
       "价值守卫是否额外调用 LLM 判定（增加成本）",
       owner="agent/skills_mgmt/value_guard.py"),
    _a("CP_EVENTS_AUDIT_MIRROR", CAT_SELF_HEALING, True,
       "治理类事件镜像写入链式审计（S2-02 联动表）",
       owner="agent/observability/events.py"),
    _b("AUDIT_CHAIN_ENABLED", CAT_SELF_HEALING, True,
       "链式审计（SQLite append-only）总开关；关闭等于审计链失效",
       owner="agent/audit/facade.py",
       impact="影响面：全部治理动作是否入链、verify_chain 是否还有意义"),
    _b("AUDIT_UI_ENABLED", CAT_SELF_HEALING, True,
       "UI 侧操作留痕开关；关闭后 UI 治理动作不再留痕",
       owner="agent/audit/ui_middleware.py"),
    _a("AUDIT_DUAL_WRITE", CAT_SELF_HEALING, True,
       "审计双写（链式 + 旧 JSONL 轨道）开关",
       owner="agent/audit/facade.py"),
    _a("AUDIT_LEGACY_WRITE", CAT_SELF_HEALING, True,
       "旧 JSONL 审计轨道写入开关", owner="agent/audit/migration.py"),
    _a("AUDIT_TRACE_EVENTS", CAT_SELF_HEALING, True,
       "trace 事件写入审计的开关",
       owner="agent/observability/trace_v2.py"),
    _a("AUDIT_UI_MAX_BODY_BYTES", CAT_SELF_HEALING, 1048576,
       "UI 审计记录的最大请求体字节数（超出即截断）",
       owner="agent/audit/ui_middleware.py", validator=Validator("int")),
    _a("AUDIT_UI_SKIP_PREFIXES", CAT_SELF_HEALING, "",
       "UI 审计跳过的路径前缀（逗号分隔）",
       owner="agent/audit/ui_middleware.py"),
    _c("AUDIT_DB_PATH", CAT_SELF_HEALING, None,
       "链式审计 SQLite 数据库路径（只读）",
       owner="agent/audit/facade.py"),
    _c("AUDIT_ROOTS_PATH", CAT_SELF_HEALING, None,
       "每日 Merkle 根文件路径（只读）", owner="agent/audit/facade.py"),
    _secret("AUDIT_SIGNING_KEY", CAT_SELF_HEALING,
            "审计链签名私钥路径（只读；永不返回明文）",
            owner="agent/audit/facade.py"),

    # ────────────────────────────────────────────────────────
    #  二、学习与进化
    # ────────────────────────────────────────────────────────

    # 【裁定 D-20260922-01（2026-09-22）】本两项登记默认值与代码事实**相反**
    # （声明 False / 代码 True）⇒ 按代码对齐为 True，只改登记值，不动业务默认。
    # 依据：agent/evolution/injector.py:112-114 —— get_evolution_config() 取值
    #   "enabled": _env_bool("EVOLUTION_ENABLED", _yaml_bool(cfg, "enabled", True))
    #   "llm_generate": _env_bool("EVOLUTION_LLM_GENERATE", _yaml_bool(cfg, "llm_generate", True))
    # ⇒ config.yaml 的 evolution 节缺项时两者均为 True。
    # 为何改登记值是安全的（与裁定 D-20260921-09 同口径）：注册表的 default
    # **从不写入 os.environ**——bootstrap.py:58-76 只遍历**覆盖层里已存在的键**，
    # resolver.py:376-385 只把 resolved.value（覆盖层值）写 env；
    # 全仓唯一会用 spec.default 作运行态落点的是 resolver.py:426（reset 时回写
    # ObservabilityConfig），它受 _is_observability_path(spec) 把关 —— 即
    # config_path 必须命中 observability_rule_paths() 的 48 条之一。
    # 【L1 更正】本两项在 L1 之后**已有** config_path（见下），故"无 config_path"
    # 不再是豁免理由；真正的豁免理由是：evolution.enabled / evolution.llm_generate
    # **不在** observability_rule_paths() 里 ⇒ _is_observability_path 仍为 False，
    # resolve() 只经 _config_lookup 读 config.yaml，**绝不写**任何运行态。
    # 故本表 default 只影响**展示与来源判定**
    # （resolve() 的 value/display_value、「当前值」来源标注）。
    # 【L1（2026-09-22）】补登记 config_path：get_evolution_config() 真实读
    #   config.yaml **顶层** evolution: 段（injector.py:106 data.get("evolution")），
    #   故 evolution.enabled / evolution.llm_generate / evolution.storage_path
    #   三条是真实读取路径，登记它们之后 resolve() 的 source 才能如实显示 config。
    #   注意：现网 config.yaml 并**没有**顶层 evolution:（只有 learning.evolution，
    #   而它无读取方）⇒ 今天这三项的取值与来源**不变**（仍是 default）；
    #   一旦有人在 config.yaml 补上该段，UI 会立刻如实跟随（这正是本项修复的目的）。
    _b("EVOLUTION_ENABLED", CAT_LEARNING, True,
       "技能进化总开关（自动产出候选技能）；"
       "真实默认 True，出处 agent/evolution/injector.py:112（config.yaml 无 evolution.enabled 时）",
       owner="agent/evolution/injector.py",
       config_path="evolution.enabled"),
    _b("EVOLUTION_LLM_GENERATE", CAT_LEARNING, True,
       "进化候选是否用 LLM 生成（关闭则只做启发式变异）；"
       "真实默认 True，出处 agent/evolution/injector.py:113-114"
       "（config.yaml 无 evolution.llm_generate 时）",
       owner="agent/evolution/injector.py",
       config_path="evolution.llm_generate"),
    _b("EVOLUTION_SCHEDULE_ENABLED", CAT_LEARNING, False,
       "离线进化调度器开关（按 cron 自动跑进化轮）",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _b("EVOLUTION_DYNAMIC_BUDGET", CAT_LEARNING, False,
       "进化预算动态调整（按历史收益自适应）",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _a("EVOLUTION_ARCHIVE_AUTO_RECORD", CAT_LEARNING, True,
       "进化谱系自动记录（不改行为，只留痕）",
       owner="agent/skills_mgmt/enhancer.py"),
    _a("EVOLUTION_CRON_EXPR", CAT_LEARNING, "",
       "离线进化调度表达式（cron）；下一次调度轮生效",
       owner="agent/skills_mgmt/offline_evolver.py",
       apply_mode=EFFECT_NEXT_TASK),
    _a("EVOLUTION_DEFAULT_EVALUATOR", CAT_LEARNING, "heuristic",
       "进化默认评测器（heuristic / 其他注册评测器）",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _a("EVOLUTION_PARENT_STRATEGY", CAT_LEARNING, "",
       "亲本选择策略", owner="agent/skills_mgmt/parent_selection.py"),
    _a("EVOLUTION_BUDGET_MIN", CAT_LEARNING, None,
       "进化单轮最小预算（token）",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _a("EVOLUTION_MAX_TOKENS_PER_ROUND", CAT_LEARNING, 500000,
       "进化单轮最大 token 预算",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _a("EVOLUTION_PER_SKILL_TOKEN_BUDGET", CAT_LEARNING, None,
       "进化单个技能的 token 预算",
       owner="agent/skills_mgmt/offline_evolver.py"),
    _a("EVOLUTION_CHILD_PENALTY_N", CAT_LEARNING, None,
       "子代惩罚基数（防止谱系过度繁殖）",
       owner="agent/skills_mgmt/parent_selection.py", validator=Validator("int")),
    _a("EVOLUTION_CHILD_PENALTY_POWER", CAT_LEARNING, None,
       "子代惩罚指数", owner="agent/skills_mgmt/parent_selection.py",
       validator=Validator("int")),
    _a("EVOLUTION_ARCHIVE_ACTIVE_GENERATIONS", CAT_LEARNING, 10,
       "进化谱系保留的活跃代数",
       owner="agent/skills_mgmt/lineage.py", validator=Validator("int")),
    _c("EVOLUTION_ARCHIVE_PATH", CAT_LEARNING, None,
       "进化谱系归档路径（只读）", owner="agent/skills_mgmt/lineage.py"),
    _c("EVOLUTION_ARCHIVE_OLD_PATH", CAT_LEARNING, None,
       "进化谱系旧归档路径（迁移兼容，只读）",
       owner="agent/skills_mgmt/lineage.py"),
    _c("EVOLUTION_STORAGE_PATH", CAT_LEARNING, None,
       "进化存储根路径（只读）", owner="agent/evolution/injector.py",
       config_path="evolution.storage_path"),

    # 【L5（2026-09-23）】补登记 config_path：四个调度器（evolution_scheduler /
    #   feedback_agent / lifecycle / precipitate）都是**同型三层读取**
    #   （env > config.yaml <段>.<key> > 硬编码默认），此前 config_path 为空
    #   ⇒ UI 只显示 default，与模块真实取值相反。逐键读取点见各条描述里的
    #   「文件:行号」；对拍见 scripts/audit_settings_config_path_parity.py。
    _b("LEARNING_EVOLVER_ENABLED", CAT_LEARNING, False,
       "在线进化调度器开关（自动跑进化轮）；config.yaml 路径 "
       "learning.evolver.enabled"
       "（agent/skills_mgmt/evolution_scheduler.py:63-66）",
       owner="agent/skills_mgmt/evolution_scheduler.py",
       config_path="learning.evolver.enabled"),
    _b("LEARNING_FEEDBACK_AGENT_ENABLED", CAT_LEARNING, False,
       "反馈智能体调度器开关（自动消化用户反馈）；config.yaml 路径 "
       "learning.feedback_agent.enabled"
       "（agent/skills_mgmt/feedback_agent.py:73-76）",
       owner="agent/skills_mgmt/feedback_agent.py",
       config_path="learning.feedback_agent.enabled"),
    _b("LEARNING_LIFECYCLE_ENABLED", CAT_LEARNING, False,
       "技能生命周期调度器开关（自动归档/升级）；config.yaml 路径 "
       "learning.lifecycle.enabled（agent/skills_mgmt/lifecycle.py:75-78）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.enabled"),
    _b("LEARNING_PRECIPITATE_ENABLED", CAT_LEARNING, False,
       "经验沉淀调度器开关（自动把经验沉淀为技能）；config.yaml 路径 "
       "learning.precipitate_enabled"
       "（agent/skills_mgmt/precipitate.py:49-51，★直接挂在 learning 下，"
       "不是 learning.precipitate.enabled）",
       owner="agent/skills_mgmt/precipitate.py",
       config_path="learning.precipitate_enabled"),
    _a("LEARNING_EVOLVER_DRY_RUN", CAT_LEARNING, True,
       "在线进化演练模式（true=只演练不落地）；config.yaml 路径 "
       "learning.evolver.dry_run"
       "（agent/skills_mgmt/evolution_scheduler.py:104-107）",
       owner="agent/skills_mgmt/evolution_scheduler.py",
       config_path="learning.evolver.dry_run"),
    _a("LEARNING_FEEDBACK_AGENT_DRY_RUN", CAT_LEARNING, True,
       "反馈智能体演练模式（true=只演练不落地）；config.yaml 路径 "
       "learning.feedback_agent.dry_run"
       "（agent/skills_mgmt/feedback_agent.py:114-117）",
       owner="agent/skills_mgmt/feedback_agent.py",
       config_path="learning.feedback_agent.dry_run"),
    _a("LEARNING_LIFECYCLE_DRY_RUN", CAT_LEARNING, True,
       "技能生命周期演练模式（true=只演练不落地）；config.yaml 路径 "
       "learning.lifecycle.dry_run（agent/skills_mgmt/lifecycle.py:187-190）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.dry_run"),
    _a("LEARNING_EVOLVER_INTERVAL_DAYS", CAT_LEARNING, None,
       "在线进化调度间隔（天）；config.yaml 路径 learning.evolver.interval_days"
       "（agent/skills_mgmt/evolution_scheduler.py:84-88）",
       owner="agent/skills_mgmt/evolution_scheduler.py",
       config_path="learning.evolver.interval_days",
       apply_mode=EFFECT_NEXT_TASK),
    _a("LEARNING_FEEDBACK_AGENT_INTERVAL_HOURS", CAT_LEARNING, None,
       "反馈智能体调度间隔（小时）；config.yaml 路径 "
       "learning.feedback_agent.interval_hours"
       "（agent/skills_mgmt/feedback_agent.py:94-98）",
       owner="agent/skills_mgmt/feedback_agent.py",
       config_path="learning.feedback_agent.interval_hours",
       apply_mode=EFFECT_NEXT_TASK),
    _a("LEARNING_LIFECYCLE_INTERVAL_HOURS", CAT_LEARNING, None,
       "技能生命周期调度间隔（小时）；config.yaml 路径 "
       "learning.lifecycle.interval_hours"
       "（agent/skills_mgmt/lifecycle.py:95-99）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.interval_hours",
       apply_mode=EFFECT_NEXT_TASK),
    _a("LEARNING_PRECIPITATE_INTERVAL_HOURS", CAT_LEARNING, None,
       "经验沉淀调度间隔（小时）；config.yaml 路径 "
       "learning.precipitate.interval_hours"
       "（agent/skills_mgmt/precipitate.py:68-72）",
       owner="agent/skills_mgmt/precipitate.py",
       config_path="learning.precipitate.interval_hours",
       apply_mode=EFFECT_NEXT_TASK),
    _a("LEARNING_LIFECYCLE_UNUSED_DAYS", CAT_LEARNING, None,
       "技能多久未使用才进入归档候选（天）；config.yaml 路径 "
       "learning.lifecycle.unused_days（agent/skills_mgmt/lifecycle.py:118-122）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.unused_days"),
    _a("LEARNING_LIFECYCLE_ARCHIVE_DAYS", CAT_LEARNING, None,
       "技能归档保留天数；config.yaml 路径 learning.lifecycle.archive_days"
       "（agent/skills_mgmt/lifecycle.py:141-145）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.archive_days"),
    _a("LEARNING_LIFECYCLE_UPGRADE_THRESHOLD", CAT_LEARNING, None,
       "技能升级判定阈值；config.yaml 路径 skills_mgmt.scale.upgrade_threshold"
       "（**兜底** learning.lifecycle.upgrade_threshold，见 "
       "agent/skills_mgmt/lifecycle.py:164-168：前者为 None 才读后者）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="skills_mgmt.scale.upgrade_threshold"),
    _c("LEARNING_EVOLVER_AUDIT_FILE", CAT_LEARNING, None,
       "在线进化审计文件路径（只读）；config.yaml 路径 "
       "learning.evolver.audit_file"
       "（agent/skills_mgmt/evolution_scheduler.py:121-124）",
       owner="agent/skills_mgmt/evolution_scheduler.py",
       config_path="learning.evolver.audit_file"),
    _c("LEARNING_FEEDBACK_AGENT_AUDIT_FILE", CAT_LEARNING, None,
       "反馈智能体审计文件路径（只读）；config.yaml 路径 "
       "learning.feedback_agent.audit_file"
       "（agent/skills_mgmt/feedback_agent.py:131-134）",
       owner="agent/skills_mgmt/feedback_agent.py",
       config_path="learning.feedback_agent.audit_file"),
    _c("LEARNING_LIFECYCLE_AUDIT_FILE", CAT_LEARNING, None,
       "技能生命周期审计文件路径（只读）；config.yaml 路径 "
       "learning.lifecycle.audit_file（agent/skills_mgmt/lifecycle.py:204-207）",
       owner="agent/skills_mgmt/lifecycle.py",
       config_path="learning.lifecycle.audit_file"),
    _c("LEARNING_PRECIPITATE_AUDIT_FILE", CAT_LEARNING, None,
       "经验沉淀审计文件路径（只读）；config.yaml 路径 "
       "learning.precipitate.audit_file（agent/skills_mgmt/precipitate.py:88-91）",
       owner="agent/skills_mgmt/precipitate.py",
       config_path="learning.precipitate.audit_file"),

    # 成本刹车阈值（B：成本刹车阈值）
    # 【L5（2026-09-23）】补登记 config_path。注意两者层级不同：
    #   CP_BUDGET_BRAKE_ENABLED 读 config.yaml **顶层** budget.enabled；
    #   LEARNING_BUDGET_* 读 learning.budget.<key>，且其优先级是
    #   env > config.yaml learning.budget > models.yaml cost_limits > 硬编码默认
    #   （agent/learning_budget.py:90-125 的三段式），config.yaml 只是其中一层。
    _b("CP_BUDGET_BRAKE_ENABLED", CAT_LEARNING, False,
       "成本刹车（预算制动）总开关；config.yaml 路径 budget.enabled"
       "（agent/monitoring/cost_brake.py:351/356/508）",
       owner="agent/monitoring/cost_brake.py",
       config_path="budget.enabled"),
    _b("LEARNING_BUDGET_MODE", CAT_LEARNING, None,
       "学习预算模式（如 normal / strict / off）；config.yaml 路径 "
       "learning.budget.mode（agent/learning_budget.py:107-110）",
       owner="agent/learning_budget.py",
       config_path="learning.budget.mode"),
    _b("LEARNING_BUDGET_MAX_DAILY_TOKENS", CAT_LEARNING, None,
       "学习预算每日 token 上限（成本刹车阈值）；config.yaml 路径 "
       "learning.budget.max_daily_tokens（agent/learning_budget.py:107-110）",
       owner="agent/learning_budget.py",
       config_path="learning.budget.max_daily_tokens"),
    _b("LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS", CAT_LEARNING, None,
       "学习预算单动作 token 上限（成本刹车阈值）；config.yaml 路径 "
       "learning.budget.max_single_action_tokens"
       "（agent/learning_budget.py:107-110）",
       owner="agent/learning_budget.py",
       config_path="learning.budget.max_single_action_tokens"),
    _b("LEARNING_BUDGET_RECOVERY_SECONDS", CAT_LEARNING, None,
       "预算耗尽后的恢复等待时长（秒）；config.yaml 路径 "
       "learning.budget.recovery_seconds（agent/learning_budget.py:107-110）",
       owner="agent/learning_budget.py",
       config_path="learning.budget.recovery_seconds"),

    # 学习观测 / 阈值（A）
    # 【L1（2026-09-22）】补登记 config_path：本族 6 项在 `novelty_hooks.py` 里都是
    #   三层取值（env > config.yaml > 硬编码默认），config 段为
    #   `learning.sensor_learning.<key>`（novelty_hooks.py:74-81 _cfg_value() 逐条读取）。
    #   此前 config_path 为空 ⇒ UI 只显示 default，**与模块真实取值相反**：
    #   例 `SENSOR_LEARNING_ENABLED` 登记默认 False 而 config.yaml:145 显式 false、
    #   `SENSOR_LEARNING_DRIFT_THRESHOLD` 登记默认 None 而 config.yaml:148 显式 0.3。
    #   补登记只改 resolve() 的取值/来源标注（真实生效值由 novelty_hooks 自己算，不变）。
    _a("SENSOR_LEARNING_ENABLED", CAT_LEARNING, False,
       "传感器学习（新颖度探测）总开关；config.yaml 路径 learning.sensor_learning.enabled"
       "（agent/learning/novelty_hooks.py:84-89）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.enabled"),
    _a("SENSOR_LEARNING_DRIFT_THRESHOLD", CAT_LEARNING, None,
       "新颖度漂移阈值；config.yaml 路径 learning.sensor_learning.drift_threshold"
       "（agent/learning/novelty_hooks.py:92-104）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.drift_threshold"),
    _a("SENSOR_LEARNING_BASELINE_RETENTION_WEEKS", CAT_LEARNING, None,
       "新颖度基线保留周数；config.yaml 路径 "
       "learning.sensor_learning.baseline_retention_weeks"
       "（agent/learning/novelty_hooks.py:107-118）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.baseline_retention_weeks"),
    # 【L5（2026-09-23）】补登记 config_path：三项都在 orchestrator.py 里读
    #   config.yaml（config 段见各条描述），此前留空 ⇒ UI 只显示 default。
    _a("LEARNING_REFLECTION_PERSIST", CAT_LEARNING, True,
       "反思产物是否写入检索面（持久化）；config.yaml 路径 "
       "learning.reflection_persist（agent/orchestrator/orchestrator.py:2810-2811）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="learning.reflection_persist"),
    _a("LEARNING_CONTEXT_ASSEMBLER_ENABLED", CAT_LEARNING, True,
       "ContextAssembler 旁路注入开关（三层记忆组装）；config.yaml 路径 "
       "learning.context_assembler.enabled"
       "（agent/orchestrator/orchestrator.py:3796-3798）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="learning.context_assembler.enabled"),
    _a("CRITIC_EVALUATION_ENABLED", CAT_LEARNING, None,
       "批评者（Critic）评测开关；config.yaml 路径 "
       "features.critic_evaluation_enabled"
       "（agent/orchestrator/orchestrator.py:2812-2813）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="features.critic_evaluation_enabled"),
    _a("PROMPT_OPT_THRESHOLD", CAT_LEARNING, None,
       "提示词优化触发阈值", owner="agent/cognitive/prompt_optimizer.py",
       validator=Validator("float")),
    _a("PROMPT_OPT_ABS_MIN_SCORE", CAT_LEARNING, None,
       "提示词优化绝对最低分", owner="agent/cognitive/prompt_optimizer.py",
       validator=Validator("float")),
    _a("PROMPT_OPT_MAX_VARIANTS", CAT_LEARNING, None,
       "提示词优化最大候选变体数",
       owner="agent/cognitive/prompt_optimizer.py", validator=Validator("int")),
    _a("PROMPT_OPT_FAILURE_BUCKET", CAT_LEARNING, None,
       "失败桶聚合条数上限",
       owner="agent/cognitive/prompt_optimizer.py", validator=Validator("int")),
    _a("PROMPT_OPT_FAILURE_TTL", CAT_LEARNING, None,
       "失败样本存活时长（秒）",
       owner="agent/cognitive/failure_bucket.py", validator=Validator("int")),
    _a("PROMPT_OPT_FAILURE_STORE", CAT_LEARNING, "memory",
       "失败样本存储后端（memory / redis）",
       owner="agent/cognitive/failure_bucket.py",
       validator=_enum("memory", "redis")),
    _a("REFLECTOR_LESSON_VERIFIABLE_TYPES", CAT_LEARNING,
       "general,analyze,query",
       "反思教训可验证类型白名单（逗号分隔）",
       owner="agent/cognitive/prompt_optimizer.py"),
    # 【L1】同上：三项在 novelty_hooks.py 里也是 env > config.yaml learning.sensor_learning.*
    #   三层取值（_audit_path/_draft_dir/_memory_dir），故登记真实 config 路径。
    _c("SENSOR_LEARNING_AUDIT_FILE", CAT_LEARNING, None,
       "新颖度审计文件路径（只读）；config.yaml 路径 "
       "learning.sensor_learning.audit_file"
       "（agent/learning/novelty_hooks.py:129-134）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.audit_file"),
    _c("SENSOR_LEARNING_DRAFT_DIR", CAT_LEARNING, None,
       "新颖度草稿目录（只读）；config.yaml 路径 "
       "learning.sensor_learning.draft_dir"
       "（agent/learning/novelty_hooks.py:121-126）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.draft_dir"),
    _c("SENSOR_LEARNING_MEMORY_DIR", CAT_LEARNING, None,
       "新颖度记忆目录（只读）；config.yaml 路径 "
       "learning.sensor_learning.memory_dir"
       "（agent/learning/novelty_hooks.py:137-142）",
       owner="agent/learning/novelty_hooks.py",
       config_path="learning.sensor_learning.memory_dir"),

    # ────────────────────────────────────────────────────────
    #  三、编排与规划
    # ────────────────────────────────────────────────────────

    _a("PLANNING_ENABLED", CAT_ORCHESTRATION, True,
       "规划引擎总开关（chat 主链路是否启用规划）",
       owner="agent/orchestrator/lifecycle_manager.py"),
    _a("PLANNING_WIRE_ENABLED", CAT_ORCHESTRATION, False,
       "规划引擎接入主链路的灰度开关（复杂任务走 PlanningCore）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="planning.wire_enabled", needs_restart=True),
    _a("PLANNING_WIRE_MIN_COMPLEXITY", CAT_ORCHESTRATION, "COMPLEX",
       "触发规划的最低复杂度（TRIVIAL/SIMPLE/NORMAL/COMPLEX）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="planning.wire_min_complexity",
       validator=_enum("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")),
    _a("PLANNING_WIRE_TIMEOUT_SECONDS", CAT_ORCHESTRATION, 30,
       "规划调用超时（秒）；超时回退 LLM",
       owner="agent/orchestrator/orchestrator.py",
       config_path="planning.wire_timeout_seconds", validator=Validator("int")),
    # 【L5（2026-09-23）】补登记 config_path：本族 8 项在 orchestrator.py 里都是
    #   三层取值（env > config.yaml > 硬编码默认），config 段逐条如下。
    #   此前 config_path 为空 ⇒ resolver 永不去读 config.yaml ⇒ UI 只显示 default，
    #   与模块真实取值相反（谎报）。补登记只改 resolve() 的来源/取值标注，
    #   模块自己的生效值由 orchestrator 自己算，**不变**（逐键对拍见
    #   scripts/audit_settings_config_path_parity.py 的 --json 结果）。
    _a("ORCHESTRATOR_REJECT_ENABLED", CAT_ORCHESTRATION, None,
       "意图拒答（reject）机制开关；config.yaml 路径 "
       "orchestrator.reject.enabled（agent/orchestrator/orchestrator.py:2690-2694）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.reject.enabled"),
    _a("ORCHESTRATOR_REJECT_THRESHOLD", CAT_ORCHESTRATION, None,
       "拒答判定阈值；config.yaml 路径 orchestrator.reject.threshold"
       "（agent/orchestrator/orchestrator.py:2690-2694）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.reject.threshold",
       validator=Validator("float")),
    _a("ORCHESTRATOR_LLM_MIN_CONFIDENCE", CAT_ORCHESTRATION, None,
       "LLM 意图判定最低置信度（低于则走规则层）；config.yaml 路径 "
       "orchestrator.reject.llm_min_confidence"
       "（agent/orchestrator/orchestrator.py:2690-2694）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.reject.llm_min_confidence",
       validator=Validator("float")),
    _a("ORCHESTRATOR_SEMANTIC_LAYER_ENABLED", CAT_ORCHESTRATION, None,
       "语义层（相似度路由）开关；config.yaml 路径 "
       "orchestrator.semantic_layer.enabled"
       "（agent/orchestrator/orchestrator.py:1879-1887）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.semantic_layer.enabled"),
    _a("ORCHESTRATOR_SEMANTIC_MIN_SCORE", CAT_ORCHESTRATION, None,
       "语义层最低命中分；config.yaml 路径 orchestrator.semantic_layer.min_score"
       "（agent/orchestrator/orchestrator.py:1879-1887）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.semantic_layer.min_score",
       validator=Validator("float")),
    _a("ORCHESTRATOR_WF_LEARN_ENABLED", CAT_ORCHESTRATION, None,
       "工作流学习层开关；config.yaml 路径 "
       "workflow_learning.learn_from_interaction.enabled"
       "（agent/orchestrator/orchestrator.py:2132-2134）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="workflow_learning.learn_from_interaction.enabled"),
    _a("ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED", CAT_ORCHESTRATION, None,
       "工作流学习层（新版接线）开关；config.yaml 路径 "
       "orchestrator.workflow_learning_layer.enabled"
       "（agent/orchestrator/orchestrator.py:1938-1946）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.workflow_learning_layer.enabled"),
    _a("ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE", CAT_ORCHESTRATION, None,
       "工作流学习命中最低分；config.yaml 路径 "
       "orchestrator.workflow_learning_layer.min_score"
       "（agent/orchestrator/orchestrator.py:1938-1946）",
       owner="agent/orchestrator/orchestrator.py",
       config_path="orchestrator.workflow_learning_layer.min_score",
       validator=Validator("float")),
    _a("ORCHESTRATOR_TRAFFIC_REPORT_INTERVAL", CAT_ORCHESTRATION, 50,
       "路由流量报告输出间隔（次）",
       owner="agent/orchestrator/routing_observability.py"),
    _a("CONTEXT_USAGE_CHECK_INTERVAL", CAT_ORCHESTRATION, 30,
       "上下文用量检查间隔（秒）",
       owner="agent/orchestrator/orchestrator.py", validator=Validator("int")),
    _a("LLM_CALL_TIMEOUT", CAT_ORCHESTRATION, 60,
       "单次 LLM 调用超时（秒）",
       owner="agent/orchestrator/orchestrator.py", validator=Validator("int")),

    # ────────────────────────────────────────────────────────
    #  【跨任务补登】S9-01 新增 2 个 env 未登记（2026-09-13）
    #
    #  背景：S9-01（对话编排答非所问与跨轮串台修复）新增了这两个读取点但未登记；
    #  合并试跑时被零缺口硬守卫抓出（`missing=['ORCHESTRATOR_TURN_STATE_MAX_SESSIONS',
    #  'ORCHESTRATOR_WF_MATERIAL_MAX_CHARS']`）。
    #  ⚠️ 为什么 S9-01 自己没发现：它开发时 master 的守卫**本来就是红的**
    #  （另有 `<unresolved>` 与 39 条未登记 env），新缺口藏在既有噪声里。
    #  这正是"守卫要长期保持常绿"的理由 —— 红着的守卫会掩盖新违规。
    #  类型/默认值逐条取自真实读取点（`os.environ.get(<字面量>, "<默认>")`），
    #  两个都在**模块导入期**读取 ⇒ needs_restart=True。
    # ────────────────────────────────────────────────────────
    _a("ORCHESTRATOR_TURN_STATE_MAX_SESSIONS", CAT_ORCHESTRATION, 256,
       "会话级 turn state 的保留会话数上限（LRU 淘汰，默认 256）",
       owner="agent/orchestrator/turn_state.py", needs_restart=True),
    _a("ORCHESTRATOR_WF_MATERIAL_MAX_CHARS", CAT_ORCHESTRATION, 6000,
       "工作流素材注入的最大字符数（默认 6000，超出即截断）",
       owner="agent/orchestrator/orchestrator.py", needs_restart=True),
    _a("AUTONOMY_DEFAULT_LEVEL", CAT_ORCHESTRATION, None,
       "自主性默认等级（config.yaml autonomy.default_level）",
       owner="agent/autonomy.py", config_path="autonomy.default_level"),
    _a("CP_ACR_DIFFICULTY_FIT", CAT_ORCHESTRATION, False,
       "ACR 难度拟合（用难度校正成本系数）",
       owner="agent/eval/calibration.py"),
    _c("CP_UTC_COEFFICIENTS", CAT_ORCHESTRATION, None,
       "UTC 成本系数表路径（只读）", owner="agent/observability/utc.py"),
    _c("CP_UTC_PRICES", CAT_ORCHESTRATION, None,
       "UTC 价格表路径（只读）", owner="agent/observability/utc.py"),
    _c("CP_UTC_ANCHOR_MODEL", CAT_ORCHESTRATION, None,
       "UTC 锚模型（成本口径基准，只读）；config.yaml 路径 llm.model"
       "（agent/observability/utc.py:356，docstring :325 自述三层优先级）",
       owner="agent/observability/utc.py",
       config_path="llm.model"),
    _a("CP_MODEL_FALLBACK_ENABLED", CAT_ORCHESTRATION, False,
       "模型降级链开关（主力模型不可用时回退）；本键**只有 env 一层**，"
       "config.yaml 无对应路径（agent/observability/model_degrade.py:57）",
       owner="agent/observability/model_degrade.py"),
    _a("CP_MODEL_FALLBACK_CHAIN", CAT_ORCHESTRATION, "",
       "模型降级链（逗号分隔的模型名，config 层可为列表或逗号串）；"
       "config.yaml 路径 llm.fallback_chain"
       "（agent/observability/model_degrade.py:84/89）",
       owner="agent/observability/model_degrade.py",
       config_path="llm.fallback_chain"),
    _a("LLM_CACHE_CONTROL_ENABLED", CAT_ORCHESTRATION, True,
       "LLM 响应缓存控制开关",
       owner="agent/tool_calling.py"),

    # ── TASK-08 子工作流 D：超时上界与重试预算 ──
    # 【为什么这几条归 B】把它们调到 0 就等于**关掉一道防护**
    #   （上界/预算本身就是防「无限阻塞」与「重试放大」的防线），
    #   与该分类既有的「关闭即降低防护的安全防线开关」口径一致。
    _b("CP_TOOL_HANDLER_TIMEOUT_SEC", CAT_ORCHESTRATION, 1800.0,
       "工具 handler 墙钟上界（秒）；0=不限。默认 1800 严格高于全部工具自述上限"
       "（test_tools 900 / lint 600 / git·shell 120），只把「无限」变「有界」",
       owner="agent/timeout_budget.py"),
    _b("CP_RETRY_MAX_ATTEMPTS", CAT_ORCHESTRATION, 6,
       "跨层总重试预算（次，只计重试不计首次尝试）；把工具/错误处理/MCP 三层"
       "相乘的重试放大收敛为任务级单一预算，超出即 deadline_exceeded",
       owner="agent/timeout_budget.py"),
    _b("CP_RETRY_DEADLINE_SEC", CAT_ORCHESTRATION, 60.0,
       "重试窗口总耗时上界（秒）；自第一次重试起算，0=不设耗时上界",
       owner="agent/timeout_budget.py"),
    _a("CP_RETRY_JITTER_FACTOR", CAT_ORCHESTRATION, 0.1,
       "工具调用重试与 MCP 重试的抖动系数（与 LLM 主链路 jitter_factor 对齐）",
       owner="agent/timeout_budget.py"),
    _a("CP_MCP_CALL_TIMEOUT_SEC", CAT_ORCHESTRATION, 30.0,
       "MCP adapter 调用上界兜底（秒）；仅在 McpServerConfig.timeout 缺省时生效",
       owner="agent/timeout_budget.py"),
    _c("VISUAL_WORKFLOWS_STORE", CAT_ORCHESTRATION, None,
       "可视化工作流存储路径（只读）",
       owner="agent/server_routes/routes_visual_workflows.py"),
    _c("CP_EVAL_ANCHOR_DIR", CAT_ORCHESTRATION, None,
       "评测锚（L0/L2 Core-50）目录（只读）", owner="agent/eval/anchor.py"),
    _c("REPLAY_STORAGE_ROOT", CAT_ORCHESTRATION, None,
       "回放存储根路径（只读）", owner="agent/monitoring/replay_storage.py"),
    _c("CP_DIGESTION_CASE_DIR", CAT_ORCHESTRATION, None,
       "判定集（cases）目录（只读）", owner="agent/digestion/cases.py"),
    _a("CP_DIGESTION_CASE_BACKEND", CAT_ORCHESTRATION, "jsonl",
       "判定集存储后端", owner="agent/digestion/cases.py",
       validator=_enum("jsonl", "sqlite")),
    _a("CP_DIGESTION_SEED_PACK", CAT_ORCHESTRATION, "",
       "判定集种子包标识", owner="agent/digestion/cases.py"),
    _a("CP_DIGESTION_PROMOTE_DIR", CAT_ORCHESTRATION, "",
       "消化晋升产物目录", owner="agent/digestion/internalize.py"),
    _a("CP_DIGESTION_REPROBE_ENABLED", CAT_ORCHESTRATION, False,
       "消化复探（reprobe）开关", owner="agent/digestion/gate.py"),
    # ── S7-04 探活设施 + S7-06 判定集成本台账（合并后补齐登记）──
    # 说明：这 5 项由 S7-04 / S7-06 交付引入；本注册表在**合并态复跑零缺口门**时
    #       如实报出缺口（`CP_DIGESTION_LIVENESS_*` / `CP_DIGESTION_CASE_COST_DIR`），
    #       故在此补登记——这正是零缺口门"合并后仍有守护力"的证据。
    _b("CP_DIGESTION_LIVENESS_ENABLED", CAT_SKILLS, False,
       "native 能力探活调度总开关（默认关闭；开启即按周期自动执行抽样探活）",
       owner="agent/digestion/probe.py",
       impact="影响面：是否自动周期性发起 native 能力探活"
              "（只读测量，但属自动化执行类）"),
    _a("CP_DIGESTION_LIVENESS_PROBE_SIZE", CAT_SKILLS, 5,
       "单次探活的抽样规模（非法值回退默认 5）",
       owner="agent/digestion/probe.py", validator=_int_range(1, 1000)),
    _a("CP_DIGESTION_LIVENESS_MAX_TARGETS", CAT_SKILLS, 20,
       "单周期探活的能力数上限（预算上限；非法值回退默认 20）",
       owner="agent/digestion/probe.py", validator=_int_range(1, 10000)),
    _c("CP_DIGESTION_LIVENESS_DIR", CAT_SKILLS, None,
       "探活基线台账目录（只读；默认 <判定集根>/_liveness）",
       owner="agent/digestion/probe.py"),
    _c("CP_DIGESTION_CASE_COST_DIR", CAT_SKILLS, None,
       "判定集构建成本台账目录（只读；默认 <判定集根>/_case_cost）",
       owner="agent/digestion/case_cost.py"),
    _a("PREFLIGHT_FAKE_FAIL", CAT_ORCHESTRATION, "",
       "预检演练：强制失败注入（仅测试/演练用）",
       owner="agent/preflight/__main__.py"),

    # ────────────────────────────────────────────────────────
    #  四、技能与检索
    # ────────────────────────────────────────────────────────

    _a("SKILL_RERANKER_ENABLED", CAT_SKILLS, True,
       "技能检索重排开关", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_MODEL", CAT_SKILLS, "",
       "技能重排模型名", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_MIN_SCORE", CAT_SKILLS, None,
       "重排最低命中分", owner="agent/skills_mgmt/reranker.py",
       validator=Validator("float")),
    _a("SKILL_RERANKER_USE_ONNX", CAT_SKILLS, True,
       "重排是否使用 ONNX 运行时", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_ONNX_VARIANT", CAT_SKILLS, "",
       "ONNX 变体（模型精度/量化档）", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_TIMEOUT", CAT_SKILLS, None,
       "重排总超时（秒）", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_RERANK_TIMEOUT", CAT_SKILLS, None,
       "单次重排调用超时（秒）", owner="agent/skills_mgmt/reranker.py"),
    _a("SKILL_RERANKER_HOT_RELOAD_INTERVAL", CAT_SKILLS, 30,
       "重排模型热更新检测间隔（秒）",
       owner="agent/skills_mgmt/reranker.py", validator=Validator("int")),
    _a("SKILL_RERANKER_MAX_CONCURRENCY", CAT_SKILLS, None,
       "重排最大并发", owner="agent/skills_mgmt/reranker_utils.py",
       validator=Validator("int")),
    _a("SKILL_NEGATIVE_INTENT_ENABLED", CAT_SKILLS, True,
       "负向意图（不该用技能的场景）识别开关",
       owner="agent/skills_mgmt/negative_intent_detector.py"),
    _a("SKILL_NEGATIVE_INTENT_THRESHOLD", CAT_SKILLS, None,
       "负向意图判定阈值",
       owner="agent/skills_mgmt/negative_intent_detector.py",
       validator=Validator("float")),
    _a("SKILL_INSTALL_MAX_RETRIES", CAT_SKILLS, 3,
       "技能安装最大重试次数",
       owner="agent/skills_mgmt/creator.py", validator=Validator("int")),
    _a("SKILL_INSTALL_RETRY_BACKOFF", CAT_SKILLS, 0.5,
       "技能安装重试退避基数（秒）",
       owner="agent/skills_mgmt/creator.py", validator=Validator("float")),
    # 【L5（2026-09-23）】补登记 config_path：六项都取自同一段
    #   config.yaml skills_mgmt.cleanup（cleanup_scheduler.py:69，逐个叶子见描述）。
    _a("SKILL_CLEANUP_UNUSED_DAYS", CAT_SKILLS, 90,
       "技能多久未使用进入清理候选（天）；config.yaml 路径 "
       "skills_mgmt.cleanup.unused_days"
       "（agent/skills_mgmt/cleanup_scheduler.py:69/87-88）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.unused_days", validator=Validator("int")),
    _a("SKILL_CLEANUP_ARCHIVED_DAYS", CAT_SKILLS, 180,
       "已归档技能保留天数；config.yaml 路径 "
       "skills_mgmt.cleanup.archived_days"
       "（agent/skills_mgmt/cleanup_scheduler.py:69/89-90）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.archived_days", validator=Validator("int")),
    _a("SKILL_CLEANUP_INTERVAL_HOURS", CAT_SKILLS, 24,
       "技能清理调度间隔（小时）；下一次调度轮生效；config.yaml 路径 "
       "skills_mgmt.cleanup.interval_hours"
       "（agent/skills_mgmt/cleanup_scheduler.py:69/81-82）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.interval_hours", validator=Validator("int"),
       apply_mode=EFFECT_NEXT_TASK),
    _a("SKILL_CLEANUP_UNUSED_DRY_RUN", CAT_SKILLS, True,
       "未使用技能清理演练模式；config.yaml 路径 "
       "skills_mgmt.cleanup.unused_dry_run"
       "（agent/skills_mgmt/cleanup_scheduler.py:69/85-86）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.unused_dry_run"),
    _a("SKILL_CLEANUP_ORPHANS_DRY_RUN", CAT_SKILLS, True,
       "孤儿技能清理演练模式；config.yaml 路径 "
       "skills_mgmt.cleanup.orphans_dry_run"
       "（agent/skills_mgmt/cleanup_scheduler.py:69/83-84）",
       owner="agent/skills_mgmt/cleanup_scheduler.py",
       config_path="skills_mgmt.cleanup.orphans_dry_run"),

    # 工具路由 / 检索阈值
    _a("AGENT_HYBRID_RERANKER", CAT_SKILLS, False,
       "工具路由是否启用混合重排",
       owner="agent/tool_router_reranker.py"),
    _a("AGENT_HYBRID_EMBEDDING", CAT_SKILLS, "",
       "工具路由混合检索的向量模型",
       owner="agent/tool_router_hybrid.py"),
    _a("AGENT_HYBRID_ALPHA", CAT_SKILLS, None,
       "混合检索权重系数（RRF/线性融合）",
       owner="agent/tool_router_hybrid.py", validator=Validator("float")),
    _a("AGENT_RERANKER_MODEL", CAT_SKILLS, "",
       "工具路由重排模型名", owner="agent/tool_router_reranker.py"),
    _a("AGENT_RERANKER_TOP_N", CAT_SKILLS, None,
       "工具路由重排保留条数", owner="agent/tool_router_reranker.py",
       validator=Validator("int")),
    _a("AGENT_RERANKER_MIN_SCORE", CAT_SKILLS, None,
       "工具路由重排最低分", owner="agent/tool_router_reranker.py",
       validator=Validator("float")),
    _a("KNOWLEDGE_MIN_SCORE", CAT_SKILLS, None,
       "知识检索最低命中分", owner="agent/knowledge/search.py",
       validator=Validator("float")),
    _a("KNOWLEDGE_RERANK_MIN_SCORE", CAT_SKILLS, None,
       "知识检索重排最低分", owner="agent/knowledge/search.py",
       validator=Validator("float")),
    _a("KNOWLEDGE_RERANK_TOP_N", CAT_SKILLS, None,
       "知识检索重排保留条数", owner="agent/knowledge/search.py",
       validator=Validator("int")),
    _a("KNOWLEDGE_RRF_K", CAT_SKILLS, None,
       "知识检索 RRF 融合参数 K", owner="agent/knowledge/search.py",
       validator=Validator("int")),
    _a("KNOWLEDGE_SENSITIVE_HIDE_SNIPPET", CAT_SKILLS, False,
       "敏感内容是否隐藏检索片段", owner="agent/knowledge/search.py"),
    _a("KNOWLEDGE_TIMING_SAMPLE_RATE", CAT_SKILLS, None,
       "知识检索耗时采样率", owner="agent/knowledge/search.py",
       validator=_range_validator(0.0, 1.0)),
    _c("KNOWLEDGE_ROOT", CAT_SKILLS, None,
       "知识库根目录（只读）", owner="agent/knowledge/ingest.py"),
    # 【裁定 D-20260922-01】登记 False / 代码 True ⇒ 按代码对齐（同 D-20260921-09 口径）。
    # 依据：agent/tool_fewshot_store.py:44
    #   FEWSHOT_ENABLED = _env_bool("FEWSHOT_ENABLED", True)
    # ⇒ env 未设置时模块常量即为 True。详情见本文件「学习与进化」段的裁定注释。
    #
    # 【L1（2026-09-22）核实结论：本族**不读** config.yaml，故 config_path 必须留空】
    #   逐条证据：agent/tool_fewshot_store.py:31-48 只有 _env_int/_env_bool 两个读取器，
    #   全部取值来自 os.environ，模块内**没有**任何 yaml 解析或 config.yaml 打开；
    #   模块 docstring（同文件:11-13）也自述【变易】"配置走 .env: FEWSHOT_*"。
    #   ⇒ 补 config_path 会让 UI 声称该值来自 config.yaml 而实际无人读它（新的假来源）。
    #   → 此处按纪律改**说明**（如实标注"仅环境变量"），不改路径。
    _a("FEWSHOT_ENABLED", CAT_SKILLS, True,
       "工具少样本（few-shot）示例注入开关；仅环境变量（该模块不读 config.yaml）；"
       "真实默认 True，出处 agent/tool_fewshot_store.py:44",
       owner="agent/tool_fewshot_store.py"),
    _a("FEWSHOT_PER_TOOL", CAT_SKILLS, None,
       "每个工具注入的示例条数；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_fewshot_store.py",
       validator=Validator("int")),
    _a("FEWSHOT_WINDOW_DAYS", CAT_SKILLS, None,
       "少样本统计窗口（天）；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_fewshot_store.py",
       validator=Validator("int")),
    _a("FEWSHOT_MAX_INPUT_LEN", CAT_SKILLS, None,
       "少样本输入最大长度；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_fewshot_store.py",
       validator=Validator("int")),
    _a("FEWSHOT_MAX_OUTPUT_LEN", CAT_SKILLS, None,
       "少样本输出最大长度；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_fewshot_store.py",
       validator=Validator("int")),
    # 【裁定 D-20260922-01】登记 False / 代码 True ⇒ 按代码对齐（同 D-20260921-09 口径）。
    # 依据：agent/tool_schema_pruner.py:78-79
    #   SCHEMA_PRUNE_ADDITIONAL_PROPS = _env_bool("SCHEMA_PRUNE_ADDITIONAL_PROPS", True)
    #   SCHEMA_PRUNE_DEPRECATED       = _env_bool("SCHEMA_PRUNE_DEPRECATED", True)
    # ⇒ env 未设置时两项裁剪均**开启**（与旧登记值所暗示的"关闭"相反）。
    #
    # 【L1（2026-09-22）核实结论：本族**不读** config.yaml，故 config_path 必须留空】
    #   逐条证据：agent/tool_schema_pruner.py:63-79 只有 _env_int/_env_bool 两个读取器，
    #   全部取值来自 os.environ，模块内**没有**任何 yaml 解析或 config.yaml 打开；
    #   模块 docstring（同文件:12-14）也自述【变易】"裁剪规则 4 个旋钮可配(.env)"。
    #   ⇒ 同 FEWSHOT 族：改**说明**（如实标注"仅环境变量"），不改路径。
    _a("SCHEMA_PRUNE_DEPRECATED", CAT_SKILLS, True,
       "工具 schema 裁剪：移除废弃字段；仅环境变量（该模块不读 config.yaml）；"
       "真实默认 True，出处 agent/tool_schema_pruner.py:79",
       owner="agent/tool_schema_pruner.py"),
    _a("SCHEMA_PRUNE_ADDITIONAL_PROPS", CAT_SKILLS, True,
       "工具 schema 裁剪：移除 additionalProperties；仅环境变量（该模块不读 config.yaml）；"
       "真实默认 True，出处 agent/tool_schema_pruner.py:78",
       owner="agent/tool_schema_pruner.py"),
    _a("SCHEMA_DESC_MAX_LEN", CAT_SKILLS, None,
       "工具描述最大长度；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_schema_pruner.py",
       validator=Validator("int")),
    _a("SCHEMA_PROP_DESC_MAX_LEN", CAT_SKILLS, None,
       "工具参数描述最大长度；仅环境变量（该模块不读 config.yaml）",
       owner="agent/tool_schema_pruner.py",
       validator=Validator("int")),
    # 【B 级理由】裁剪保护（TASK-08 E8 / v1.4 §7）：它决定**高危工具能不能被裁掉**。
    #   默认**开启**；关闭即允许 token 预算裁剪 / 主线名额截断把 `risk >= high`
    #   或 `confirm_level >= L2` 的工具（实测 20 个：write_file / edit / git /
    #   shell_execute / workspace_delete …）静默从模型可见集里拿掉 —— 风险姿态变宽，
    #   故按 B 级（需二次认证 + 双人确认）登记。回滚 = 置 0/false/no/off。
    _b("CP_TOOLSET_PRUNE_PROTECT", CAT_SKILLS, True,
       "工具裁剪保护（TASK-08 E8）：默认开启 ⇒ risk >= high **或** "
       "confirm_level >= L2 的工具**不参与裁剪**（工具级 token 预算裁剪与主线名额"
       "截断两处共用同一判据）；置 0/false/no/off = 退回无保护的旧裁剪行为",
       owner="agent/capregistry/pruning.py",
       impact="影响面：开启=实测 20 个受保护工具（13 个 risk: high + 3 个 critical "
              "+ 其余 L2/L3）在预算或名额不足时一律保留，工具数可能略超 max_tools；"
              "关闭=高危工具可能被静默裁掉（模型会改用可见的低危工具拼等价效果）",
       rollback="回滚：置 CP_TOOLSET_PRUNE_PROTECT=0（立即退回旧口径，不写数据文件）"),
    # 【A 级理由】默认 0 = **不按预算裁剪**（本仓库此前没有工具级 token 预算裁剪，
    #   保持既有行为零变化）；设成非零只是显式启用一条已有路径，不改变权限与治理
    #   姿态，且受上面那条保护开关约束 ⇒ A 级。
    _a("CP_TOOLSET_SCHEMA_TOKEN_BUDGET", CAT_SKILLS, 0,
       "工具 schema 的 token 预算上限（TASK-08 E8）：>0 ⇒ 超预算时从尾部裁掉"
       "**可裁**工具；≤0（默认）= 不裁剪。受保护工具（risk >= high 或 "
       "confirm_level >= L2）一律不裁，故实际占用可能略超预算",
       owner="agent/capregistry/pruning.py",
       validator=Validator("int")),
    _c("MEMORY_IDENTITY_ROOT", CAT_SKILLS, None,
       "身份记忆根目录（只读）", owner="agent/memory/identity.py"),
    _c("MEMORY_SNAPSHOT_ROOT", CAT_SKILLS, None,
       "记忆快照根目录（只读）", owner="agent/memory/forgetting.py"),
    _a("MEMORY_SNAPSHOT_RETENTION_DAYS", CAT_SKILLS, None,
       "记忆快照保留天数（遗忘机制的保留期）",
       owner="agent/memory/forgetting.py", validator=Validator("int")),
    _a("SKILLS_FUSION_WEIGHT_TFIDF", CAT_SKILLS, None,
       "技能检索融合权重：TF-IDF 分支（三权重之和归一）；config.yaml 路径 "
       "skills_mgmt.retrieval.fusion.weights.tfidf"
       "（agent/skills_mgmt/loader.py:1024-1034）",
       owner="agent/skills_mgmt/loader.py",
       config_path="skills_mgmt.retrieval.fusion.weights.tfidf",
       validator=Validator("float")),
    _a("SKILLS_FUSION_WEIGHT_VECTOR", CAT_SKILLS, None,
       "技能检索融合权重：向量分支；config.yaml 路径 "
       "skills_mgmt.retrieval.fusion.weights.vector"
       "（agent/skills_mgmt/loader.py:1024-1034）",
       owner="agent/skills_mgmt/loader.py",
       config_path="skills_mgmt.retrieval.fusion.weights.vector",
       validator=Validator("float")),
    _a("SKILLS_FUSION_WEIGHT_BM25", CAT_SKILLS, None,
       "技能检索融合权重：BM25 分支；config.yaml 路径 "
       "skills_mgmt.retrieval.fusion.weights.bm25"
       "（agent/skills_mgmt/loader.py:1024-1034）",
       owner="agent/skills_mgmt/loader.py",
       config_path="skills_mgmt.retrieval.fusion.weights.bm25",
       validator=Validator("float")),
    _c("MEMORY_LAYERS_ROOT", CAT_SKILLS, None,
       "分层记忆根目录（只读）", owner="agent/memory/layered_store.py"),
    _a("MEMORY_LAYERS_AUDIT", CAT_SKILLS, False,
       "分层记忆审计留痕开关", owner="agent/memory/layered_store.py"),
    _a("MEMORY_FORGET_MIN_SAMPLES", CAT_SKILLS, None,
       "遗忘判定最小样本数（<20 只披露不考核）",
       owner="agent/memory/forgetting.py", validator=Validator("float")),
    _a("MEMORY_FORGET_SUCCESS_RATIO", CAT_SKILLS, None,
       "遗忘判定成功率阈值", owner="agent/memory/forgetting.py",
       validator=Validator("float")),
    _a("MEMORY_FORGET_WINDOW_DAYS", CAT_SKILLS, None,
       "遗忘判定窗口（天）", owner="agent/memory/forgetting.py",
       validator=Validator("float")),
    _a("MEMORY_AUDIT_SCAN_LIMIT", CAT_SKILLS, None,
       "记忆审计单次扫描条数", owner="agent/memory/forgetting.py",
       validator=Validator("float")),

    # 评测锚 / 消化阈值
    _a("EVAL_STAGE1_RATIO", CAT_SKILLS, None,
       "评测一阶段抽样比例", owner="agent/skills_mgmt/evaluator.py",
       validator=_range_validator(0.0, 1.0)),
    _a("EVAL_STAGE1_MAX_SAMPLES", CAT_SKILLS, None,
       "评测一阶段最大样本数", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _a("EVAL_STAGE1_MIN_SCORE", CAT_SKILLS, None,
       "评测一阶段最低分门槛", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("float")),
    _a("EVAL_STAGE1_BUDGET_TOKENS", CAT_SKILLS, None,
       "评测一阶段 token 预算", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _a("EVAL_STAGE2_BUDGET_TOKENS", CAT_SKILLS, None,
       "评测二阶段 token 预算", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _a("EVAL_BUDGET_TOKENS", CAT_SKILLS, None,
       "评测总 token 预算", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _a("EVAL_CONSISTENCY_RUNS", CAT_SKILLS, None,
       "一致性评测重复次数", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _a("EVAL_TIMEOUT_SEC", CAT_SKILLS, None,
       "评测单次超时（秒）", owner="agent/skills_mgmt/evaluator.py",
       validator=Validator("int")),
    _c("EVAL_SAMPLES_DIR", CAT_SKILLS, None,
       "评测样本目录（只读）", owner="agent/skills_mgmt/evaluator.py"),
    _a("DISTILL_FEEDBACK_TOP_N", CAT_SKILLS, None,
       "蒸馏反馈取前 N 条", owner="agent/knowledge/distill_feedback.py",
       validator=Validator("int")),
    _a("DISTILL_FEEDBACK_MIN_FREQUENCY", CAT_SKILLS, None,
       "蒸馏反馈最低出现频次", owner="agent/knowledge/distill_feedback.py",
       validator=Validator("int")),
    _a("DISTILL_FEEDBACK_INTERVAL_DAYS", CAT_SKILLS, None,
       "蒸馏反馈调度间隔（天）；下一次调度轮生效",
       owner="agent/knowledge/distill_feedback.py", validator=Validator("int"),
       apply_mode=EFFECT_NEXT_TASK),
    _a("CP_DIGESTION_SHADOW_ENABLED", CAT_SKILLS, False,
       "消化影子（shadow）模式开关",
       owner="agent/digestion/shadow.py"),
    _a("CP_DIGESTION_GRAY_ENABLED", CAT_SKILLS, False,
       "消化灰度（gray）放量开关", owner="agent/digestion/shadow.py"),
    _a("CP_DIGESTION_SHADOW_BUDGET_RATIO", CAT_SKILLS, None,
       "影子模式预算占比", owner="agent/digestion/shadow.py",
       validator=_range_validator(0.0, 1.0)),
    _a("CP_DIGESTION_SHADOW_BUDGET_CAP", CAT_SKILLS, None,
       "影子模式预算上限（token）", owner="agent/digestion/shadow.py",
       validator=Validator("int")),
    _a("CP_DIGESTION_SHADOW_MIN_BUDGET", CAT_SKILLS, None,
       "影子模式最小预算（token）", owner="agent/digestion/shadow.py",
       validator=Validator("int")),
    _a("CP_DIGESTION_SHADOW_DIR", CAT_SKILLS, "",
       "影子模式产物目录", owner="agent/digestion/shadow.py"),
    _b("CP_DIGESTION_INTERNALIZE_ENABLED", CAT_SKILLS, False,
       "内化引擎开关（把消化产物晋升为 native 能力）",
       owner="agent/digestion/internalize.py"),
    _a("CP_DIGESTION_NATIVE_UNIT_COST_CENTS", CAT_SKILLS, None,
       "native 能力单位成本（分）", owner="agent/digestion/internalize.py",
       validator=Validator("float")),
    _a("CP_DIGESTION_NATIVE_INVESTMENT_CENTS", CAT_SKILLS, None,
       "native 能力投资额（分）", owner="agent/digestion/internalize.py",
       validator=Validator("float")),

    # ────────────────────────────────────────────────────────
    #  五、可观测与阈值
    # ────────────────────────────────────────────────────────

    _a("CP_EVENTS_ENABLED", CAT_OBSERVABILITY, True,
       "事件存储（events.jsonl）开关",
       owner="agent/observability/events.py"),
    _a("CP_EVENTS_DIR", CAT_OBSERVABILITY, "",
       "事件存储目录（测试隔离用）", owner="agent/observability/events.py"),
    _a("CP_EVENTS_ARCHIVE", CAT_OBSERVABILITY, True,
       "事件跨天归档开关", owner="agent/observability/events.py"),
    _a("AGENT_PERF_SAMPLE", CAT_OBSERVABILITY, 100,
       "性能采样率/采样基数（AGENT_PERF_* 性能观测）",
       owner="agent/utils/perf_monitor.py", validator=Validator("int")),
    _a("AGENT_PERF_LOGGING", CAT_OBSERVABILITY, False,
       "性能日志输出开关", owner="agent/utils/perf_monitor.py"),
    _a("AGENT_PERF_PROMETHEUS", CAT_OBSERVABILITY, False,
       "性能指标推送 Prometheus 开关", owner="agent/utils/perf_monitor.py"),
    _a("CONTEXT_ASSEMBLER_LOG_LEVEL", CAT_OBSERVABILITY, "",
       "上下文组装器日志级别",
       owner="agent/context/assembler.py",
       validator=_enum("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")),
    _a("MCP_LOG_LEVEL", CAT_OBSERVABILITY, "INFO",
       "MCP 执行器日志级别", owner="agent/mcp_executor.py",
       validator=_enum("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL")),
    _a("LOCK_PROFILE", CAT_OBSERVABILITY, False,
       "锁竞争剖析开关", owner="agent/monitoring/lock_profiler.py"),
    _a("LOCK_PROFILE_BATCH", CAT_OBSERVABILITY, 500,
       "锁剖析批量上报条数", owner="agent/monitoring/lock_profiler.py",
       validator=Validator("int")),
    _c("LOCK_PROFILE_LOG", CAT_OBSERVABILITY, None,
       "锁剖析输出文件路径（只读）",
       owner="agent/monitoring/lock_profiler.py"),
    _a("LOCK_WATCHDOG_ENABLED", CAT_OBSERVABILITY, False,
       "锁看门狗开关（检测长时间持锁）",
       owner="agent/monitoring/lock_watchdog.py"),
    _a("LOCK_WATCHDOG_HOLD_MS", CAT_OBSERVABILITY, None,
       "锁持有时长告警阈值（毫秒）",
       owner="agent/monitoring/lock_watchdog.py", validator=Validator("int")),
    _a("LOCK_WATCHDOG_WAIT_MS", CAT_OBSERVABILITY, None,
       "锁等待时长告警阈值（毫秒）",
       owner="agent/monitoring/lock_watchdog.py", validator=Validator("int")),
    _a("ETCD_ENABLED", CAT_OBSERVABILITY, False,
       "etcd 配置中心开关", owner="agent/config/etcd_config_client.py"),
    _c("ETCD_HOST", CAT_OBSERVABILITY, "localhost",
       "etcd 主机（外部依赖，只读）",
       owner="agent/config/etcd_config_client.py"),
    _c("ETCD_PORT", CAT_OBSERVABILITY, 2379,
       "etcd 端口（外部依赖，只读）",
       owner="agent/config/etcd_config_client.py"),

    # 追踪（tracing）兼容入口的 env 覆盖项（A：可观测采样/日志级别）
    # 说明：`_TracingConfigCompat` 的每个属性都是「env 优先 > observability 配置树」
    #       的两级读取。其中 `TRACING_ENV` / `TRACING_LOG_LEVEL` /
    #       `TRACING_SAMPLER_RATIO` 三项已由 `_merge_observability_specs()` 以
    #       `tracing.*` 配置路径合并（带 env_name），此处**不重复登记**；
    #       下面五项在配置树里没有对应 path，故单独登记。
    _a("TRACING_SAMPLER", CAT_OBSERVABILITY, "ratio",
       "追踪采样策略（ratio / rate_limit 等）",
       owner="agent/monitoring/observability_config.py"),
    _a("TRACING_SAMPLER_RATE_LIMIT", CAT_OBSERVABILITY, 100,
       "追踪限流式采样每秒上限（span/s）",
       owner="agent/monitoring/observability_config.py",
       validator=Validator("int")),
    _a("TRACING_EXPORTER", CAT_OBSERVABILITY, "console",
       "追踪导出器（console / otlp / jaeger 等）",
       owner="agent/monitoring/observability_config.py"),
    _a("TRACING_EXPORTER_ENDPOINT", CAT_OBSERVABILITY, "",
       "追踪导出端点（staging/production 默认走 OTLP 端点）",
       owner="agent/monitoring/observability_config.py"),
    _a("TRACING_EXPORTER_PROTOCOL", CAT_OBSERVABILITY, "grpc",
       "追踪导出协议（grpc / http）",
       owner="agent/monitoring/observability_config.py",
       validator=_enum("grpc", "http")),
    _a("TRACING_DATA_RETENTION_DAYS", CAT_OBSERVABILITY, 7,
       "追踪数据保留天数",
       owner="agent/monitoring/observability_config.py",
       validator=Validator("int")),

    # ────────────────────────────────────────────────────────
    #  六、外部依赖与密钥（C 级：只读脱敏）
    # ────────────────────────────────────────────────────────

    _secret("LLM_API_KEY", CAT_EXTERNAL, "LLM 服务 API Key（只读脱敏）",
            owner="agent/orchestrator/lifecycle_manager.py"),
    # 【2026-09-13 补登（自伤回归）】P0 修复（commit bbc5d821）给
    # `EnvConfigManager` 加了 `.env` 目标覆盖口 `CP_ENV_FILE`，用于让测试把写入
    # 重定向到临时文件（此前测试会把**仓库根真实 `.env`** 的 `LLM_API_KEY`
    # 覆盖成 `sk-test-key`，静默打坏在跑的服务）。**当时漏了登记本表** ⇒
    # 零缺口硬守卫变红（`missing=['CP_ENV_FILE']`），由 S10-05 的"勘误"首先发现。
    # 定为 **C 级**（绝对路径项，只读脱敏）：它能改变"密钥/配置从哪个文件读"，
    # 属于**配置完整性相关**的部署级旋钮，不应从 UI 随手切换；
    # 默认空串 = 与以往行为完全一致（仍读仓库根 `.env`）。
    _c("CP_ENV_FILE", CAT_EXTERNAL, "",
       "`.env` 目标文件覆盖（空=仓库根 .env；测试隔离用；路径项，UI 只读展示）",
       owner="agent/env_config_manager.py", validator=Validator("path")),
    _secret("OPENAI_API_KEY", CAT_EXTERNAL, "OpenAI API Key（只读脱敏）",
            owner="agent/orchestrator/lifecycle_manager.py"),
    _secret("DEEPSEEK_API_KEY", CAT_EXTERNAL, "DeepSeek API Key（只读脱敏）",
            owner="agent/process_distill/service.py"),
    _secret("FLASK_API_TOKEN", CAT_EXTERNAL,
            "云枢 HTTP 面访问令牌（只读脱敏）", owner="agent/server_auth.py"),
    _secret("SMTP_PASSWORD", CAT_EXTERNAL, "SMTP 口令（只读脱敏）",
            owner="agent/knowledge/audit_job.py"),
    _b("LLM_MODEL", CAT_EXTERNAL, "",
       "主力 LLM 模型名（变更影响全部输出与成本口径）",
       owner="agent/orchestrator/lifecycle_manager.py"),
    _b("LLM_PROVIDER", CAT_EXTERNAL, "openai",
       "LLM 提供方（变更影响全部调用链）",
       owner="agent/orchestrator/lifecycle_manager.py"),
    _c("LLM_BASE_URL", CAT_EXTERNAL, "",
       "LLM 服务 Base URL（外部端点，只读）",
       owner="agent/orchestrator/lifecycle_manager.py"),
    _c("DEEPSEEK_BASE_URL", CAT_EXTERNAL, "",
       "DeepSeek 服务 Base URL（外部端点，只读）",
       owner="agent/process_distill/service.py"),
    _c("REDIS_URL", CAT_EXTERNAL, "redis://localhost:6379/0",
       "Redis 连接串（外部依赖，只读）",
       owner="agent/cognitive/failure_bucket.py"),
    _c("LOKI_URL", CAT_EXTERNAL, "http://localhost:3100",
       "Loki 日志服务地址（外部依赖，只读）",
       owner="agent/monitoring/loki.py"),
    _c("SMTP_HOST", CAT_EXTERNAL, "", "SMTP 主机（只读）",
       owner="agent/knowledge/audit_job.py"),
    _c("SMTP_PORT", CAT_EXTERNAL, 587, "SMTP 端口（只读）",
       owner="agent/knowledge/audit_job.py"),
    _c("SMTP_USERNAME", CAT_EXTERNAL, "", "SMTP 用户名（只读脱敏）",
       owner="agent/knowledge/audit_job.py"),
    _c("MAIL_FROM", CAT_EXTERNAL, "", "审计邮件发件人（只读）",
       owner="agent/knowledge/audit_job.py"),
    _c("MAIL_RECIPIENTS", CAT_EXTERNAL, "", "审计邮件收件人（只读）",
       owner="agent/knowledge/audit_job.py"),
    _secret("SENTRY_DSN", CAT_EXTERNAL, "Sentry DSN（只读脱敏）",
            owner="agent/error_reporting_config.py"),
    _c("SENTRY_ENVIRONMENT", CAT_EXTERNAL, "development",
       "Sentry 环境标识（只读）", owner="agent/error_reporting_config.py"),
    _c("SENTRY_RELEASE", CAT_EXTERNAL, "", "Sentry 发布版本（只读）",
       owner="agent/error_reporting_config.py"),
    _c("SENTRY_SERVER_NAME", CAT_EXTERNAL, "yunshu-backend",
       "Sentry 服务名（只读）", owner="agent/error_reporting_config.py"),
    _a("SENTRY_MIN_LEVEL", CAT_EXTERNAL, "error",
       "Sentry 上报最低级别", owner="agent/error_reporting_config.py",
       validator=_enum("debug", "info", "warning", "error", "fatal")),
    _a("SENTRY_SAMPLE_RATE", CAT_EXTERNAL, 1.0,
       "Sentry 事件采样率（可观测采样，A 级）",
       owner="agent/error_reporting_config.py",
       validator=_range_validator(0.0, 1.0)),
    _a("SENTRY_TRACES_SAMPLE_RATE", CAT_EXTERNAL, 0.0,
       "Sentry 链路采样率（可观测采样，A 级）",
       owner="agent/error_reporting_config.py",
       validator=_range_validator(0.0, 1.0)),
    _a("ERROR_REPORTING_FILE_ENABLED", CAT_EXTERNAL, True,
       "错误上报：文件通道开关",
       owner="agent/error_reporting_config.py"),
    _a("ERROR_REPORTING_FILE_LEVEL", CAT_EXTERNAL, "error",
       "错误上报：文件通道级别", owner="agent/error_reporting_config.py",
       validator=_enum("debug", "info", "warning", "error", "critical")),
    _a("ERROR_REPORTING_CONSOLE_LEVEL", CAT_EXTERNAL, "warning",
       "错误上报：控制台级别", owner="agent/error_reporting_config.py",
       validator=_enum("debug", "info", "warning", "error", "critical")),
    _c("ERROR_REPORTING_FILE_PATH", CAT_EXTERNAL, "./logs/digital_life_errors.log",
       "错误上报：文件路径（只读）", owner="agent/error_reporting_config.py"),
    _a("ERROR_REPORTING_SLACK_ENABLED", CAT_EXTERNAL, False,
       "错误上报：Slack 通道开关",
       owner="agent/error_reporting_config.py"),
    _a("ERROR_REPORTING_SLACK_LEVEL", CAT_EXTERNAL, "warning",
       "错误上报：Slack 通道级别", owner="agent/error_reporting_config.py",
       validator=_enum("debug", "info", "warning", "error", "critical")),
    _c("ERROR_REPORTING_SLACK_WEBHOOK_URL", CAT_EXTERNAL, "",
       "错误上报：Slack Webhook（外部端点，只读脱敏）",
       owner="agent/error_reporting_config.py", secret=True),
    _c("ERROR_REPORTING_SLACK_CHANNEL", CAT_EXTERNAL, "#digital-life-alerts",
       "错误上报：Slack 频道（只读）", owner="agent/error_reporting_config.py"),
    _c("ERROR_REPORTING_SLACK_USERNAME", CAT_EXTERNAL, "Digital Life Bot",
       "错误上报：Slack 机器人名（只读）",
       owner="agent/error_reporting_config.py"),
    _c("ERROR_REPORTING_SLACK_ICON", CAT_EXTERNAL, ":robot_face:",
       "错误上报：Slack 图标（只读）", owner="agent/error_reporting_config.py"),
    _a("ERROR_REPORTING_WEBHOOK_ENABLED", CAT_EXTERNAL, False,
       "错误上报：通用 Webhook 通道开关",
       owner="agent/error_reporting_config.py"),
    _a("ERROR_REPORTING_WEBHOOK_LEVEL", CAT_EXTERNAL, "error",
       "错误上报：Webhook 通道级别", owner="agent/error_reporting_config.py",
       validator=_enum("debug", "info", "warning", "error", "critical")),
    _a("ERROR_REPORTING_WEBHOOK_TIMEOUT", CAT_EXTERNAL, 5,
       "错误上报：Webhook 超时（秒）",
       owner="agent/error_reporting_config.py", validator=Validator("int")),
    _c("ERROR_REPORTING_WEBHOOK_URL", CAT_EXTERNAL, "",
       "错误上报：Webhook 地址（外部端点，只读脱敏）",
       owner="agent/error_reporting_config.py", secret=True),

    # ────────────────────────────────────────────────────────
    #  【L5】扫描根扩容后补登记的「表外」开关（2026-09-23）
    # ────────────────────────────────────────────────────────
    # 背景：scripts/scan_settings.py 的扫描根原为 ("agent",)，于是 agent/ 之外的
    # 真实生产读取点**从未进过任何候选清单**（审计
    # docs/closeout/开关登记表_config口径审计_20260922.md §5.2 把这条列为已知假阴形态，
    # §七.1 已核实 planning/core.py 真的读 config.yaml 的 learning.experience_persist）。
    # 本轮把扫描根扩到「全仓生产代码」后，机械提取一次性暴露下列键；按零缺口纪律
    # **逐条核实读取点**后补登记（不是为了让扫描变绿而放水：每条都带读取点行号）。
    # 标「只有 env 一层」的键，其归属模块里**没有** config.yaml 读取
    # ⇒ config_path 必须留空（补了就是新的假来源）。
    _a("LEARNING_EXPERIENCE_PERSIST", CAT_LEARNING, False,
       "学习经验是否落盘（reflector 经验/教训写入 data/reflection/）；"
       "config.yaml 路径 learning.experience_persist"
       "（planning/core.py:462-464 读 config、:468 读 env）"
       "—— ★ 它此前**根本不在登记表里**：扫描根只有 agent/，看不到 planning/",
       owner="planning/core.py",
       config_path="learning.experience_persist"),
    _a("SENSOR_LEARNING_CHANGE_LOG_MAX_ENTRIES", CAT_LEARNING, 10000,
       "感知侧 change_log.json 的容量上限（超出即滚动裁剪最旧条目）；"
       "config.yaml 路径 learning.sensor_learning.change_log_max_entries"
       "（sensor/novelty.py:111 读 env、:118 读 config，默认 10000）",
       owner="sensor/novelty.py",
       config_path="learning.sensor_learning.change_log_max_entries",
       validator=Validator("int")),
    _a("YUNSHU_DISABLE_WINDOW_SENSOR", CAT_LEARNING, False,
       "开发/沙箱环境屏蔽窗口传感器（win32gui 依赖链在受限环境会产生访问噪音）；"
       "**只有 env 一层**（sensor/registry.py:122 与 app_server.py:605 两处同口径读取，"
       "全仓无对应 config.yaml 路径 ⇒ config_path 留空）",
       owner="sensor/registry.py"),
    _a("BM25_K1", CAT_SKILLS, 1.5,
       "BM25 词频饱和参数 k1（记忆向量库检索用；模块导入时一次性读取）；"
       "**只有 env 一层**（memory/vector_store/vector_store.py:49）",
       owner="memory/vector_store/vector_store.py", validator=Validator("float")),
    _a("BM25_B", CAT_SKILLS, 0.5,
       "BM25 文档长度归一参数 b（0.75 对短文档虚高，项目实测降到 0.5；"
       "设 BM25_B=0.75 可回滚原行为）；**只有 env 一层**"
       "（memory/vector_store/vector_store.py:50）",
       owner="memory/vector_store/vector_store.py", validator=Validator("float")),
    _c("CP_CAPABILITY_ENDPOINT", CAT_EXTERNAL, "http://127.0.0.1:5678",
       "能力服务地址（cloudshu CLI 客户端用；未设则用内置默认）；"
       "**只有 env 一层**（cloudshu/cli.py:354-356）",
       owner="cloudshu/cli.py"),
    _secret("CP_API_TOKEN", CAT_EXTERNAL,
            "能力服务访问令牌（cloudshu CLI；只读脱敏，永不返回明文）；"
            "**只有 env 一层**（cloudshu/cli.py:357）",
            owner="cloudshu/cli.py"),
    _secret("API_TOKEN", CAT_EXTERNAL,
            "能力服务访问令牌的**兼容别名**（CP_API_TOKEN 未设时兜底；只读脱敏）；"
            "**只有 env 一层**（cloudshu/cli.py:358）",
            owner="cloudshu/cli.py"),
    _b("CP_MCP_SERVER_TOOLS", CAT_EXTERNAL, "",
       "MCP 服务对外暴露的工具白名单（逗号分隔；未设则用内置默认集）；"
       "放宽即扩大对外暴露面（与「关闭即降低防护」同性质，故取 B）；"
       "**只有 env 一层**（mcp_services/yunshu_mcp_server.py:223）",
       owner="mcp_services/yunshu_mcp_server.py"),
    _a("CP_MCP_SERVER_LOG_LEVEL", CAT_OBSERVABILITY, "INFO",
       "MCP 服务进程日志级别；**只有 env 一层**"
       "（mcp_services/yunshu_mcp_server.py:539）",
       owner="mcp_services/yunshu_mcp_server.py",
       validator=_enum("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")),
    _a("ENV", CAT_OBSERVABILITY, "development",
       "运行环境标识（development / production）：production 时 MCP 服务日志只留 ERROR，"
       "否则 DEBUG；**只有 env 一层**（mcp_services/log_config.py:44/92/119）",
       owner="mcp_services/log_config.py"),
    _a("LOG_LEVEL", CAT_OBSERVABILITY, "INFO",
       "启动入口日志级别（main.py 的调试分支与 file_monitor 服务启动时读取）；"
       "**只有 env 一层**（main.py:94、file_monitor.py:24，两处同口径）",
       owner="main.py"),
    _a("LOG_REQUEST_PRINT", CAT_OBSERVABILITY, True,
       "Flask 接口日志是否打印请求/响应详情（设 0/false/no 降噪，退回 logger.debug）；"
       "**只有 env 一层**（app_server.py:371）",
       owner="app_server.py"),
    _a("MONITOR_ROOT", CAT_OBSERVABILITY, "",
       "文件监控服务的监控根目录（Docker 部署用；未设则取该文件所在目录）；"
       "**只有 env 一层**（file_monitor.py:35）",
       owner="file_monitor.py", validator=Validator("path")),
    _c("COVERAGE_XML", CAT_OBSERVABILITY, "",
       "文件监控服务读取的覆盖率报告路径（未设则为 MONITOR_ROOT/coverage.xml）；"
       "**只有 env 一层**（file_monitor.py:44）",
       owner="file_monitor.py", validator=Validator("path")),

    # ────────────────────────────────────────────────────────
    #  开关中心自身（元开关：覆盖层路径）
    # ────────────────────────────────────────────────────────
    _c("CP_UI_SETTINGS_PATH", CAT_OBSERVABILITY, "data/ui_settings.json",
       "开关中心覆盖层文件路径（只读；测试与多实例部署用它隔离覆盖层）",
       owner="agent/settings/overrides.py"),

    # ────────────────────────────────────────────────────────
    #  动态家族（前缀 + 运行时后缀；UI 只读展示）
    # ────────────────────────────────────────────────────────
    # 【L5（2026-09-23）】动态族的 config.yaml 路径**带运行时后缀**
    #   （skills_mgmt.assess.<key> 之类），而 SettingSpec.config_path 是**单值字段**：
    #   填任何固定路径都是**伪造来源**（UI 会声称值来自那个路径，而代码从不读它）。
    #   故这 5 族一律**只改 description**（如实写明路径模板 + 读取点 + 为什么留空），
    #   config_path 保持空字符串，并由 tests/unit/test_settings_registry.py::
    #   TestDynamicFamiliesDeclarePathsInDescription 钉住
    #   「描述里有路径模板 + config_path 必须为空」这两个方向。
    SettingSpec(
        key="SKILLS_ASSESS_<KEY>", category=CAT_SKILLS, type="bool",
        default=None, description=(
            "技能评估项动态开关族：SKILLS_ASSESS_<KEY> 覆盖 "
            "config.yaml skills_mgmt.assess.<key>（逐项开关，UI 只读展示）；"
            "路径模板 skills_mgmt.assess.<key>，后缀 <key> 由运行期变量拼出"
            "（agent/skills_mgmt/assessor.py:90 的 os.environ.get("
            "\"SKILLS_ASSESS_\" + key.upper())，取值见 :103-106），"
            "**故 config_path 为空**：单值字段表达不了动态后缀，填固定路径即伪造来源"),
        risk=RISK_A, env_name="", dynamic_prefix="SKILLS_ASSESS_",
        owner_module="agent/skills_mgmt/assessor.py"),
    SettingSpec(
        key="SKILLS_DIGEST_<KEY>", category=CAT_SKILLS, type="bool",
        default=None, description=(
            "技能摘要项动态开关族：SKILLS_DIGEST_<KEY>（历史键兼容，UI 只读展示）；"
            "它是一个**兜底层**：仅在 SKILLS_ASSESS_<KEY> 未设置时才读"
            "（agent/skills_mgmt/assessor.py:94），config.yaml 侧对应 "
            "skills_mgmt.digest.<旧key>（assessor.py:104-106，"
            "旧 key 映射见 _ASSESS_LEGACY_KEYS）；后缀由运行期变量拼出，"
            "**故 config_path 为空**（填固定路径即伪造来源）"),
        risk=RISK_A, env_name="", dynamic_prefix="SKILLS_DIGEST_",
        owner_module="agent/skills_mgmt/assessor.py"),
    SettingSpec(
        key="SKILL_CLEANUP_<NAME>", category=CAT_SKILLS, type="bool",
        default=None, description=(
            "技能清理动态开关族：SKILL_CLEANUP_<NAME>（由 _ENV_PREFIX + 后缀拼接，"
            "常见实例已逐条登记，UI 只读展示）；路径模板 "
            "skills_mgmt.cleanup.<小写后缀>（agent/skills_mgmt/cleanup_scheduler.py:69 "
            "取段、:74-77 按后缀取叶子），后缀由运行期变量拼出，"
            "**故 config_path 为空**（填固定路径即伪造来源）"),
        risk=RISK_A, env_name="", dynamic_prefix="SKILL_CLEANUP_",
        owner_module="agent/skills_mgmt/cleanup_scheduler.py"),

    # ────────────────────────────────────────────────────────
    #  特性开关族（TASK-03 第 6 步；阶段灰度开关，UI 只读展示）
    # ────────────────────────────────────────────────────────
    # Why 只登记**一个动态家族**、不为每个特性开关注册一条 env_name 行：
    #   特性开关的 env 名是 `YUNSHU_FEATURE_` + 开关名**拼接**出来的
    #   （agent/settings/feature_flags.py::_env_text），代码里不存在任何
    #   `os.getenv("YUNSHU_FEATURE_XXX")` 字面量。若逐个登记字面量行，它们
    #   会全部命中 `test_registry_has_no_phantom_switches`（"注册了但代码没读"）
    #   而变红。一次登记家族 ⇒ 新增开关只需在 FEATURES 表加一行，
    #   这也是 E13「新增开关成本量化」的结论，规范见 docs/rfc/特性开关规范.md。
    # 具体开关清单、灰度默认比例、归属阶段由 FEATURES 表声明，并由
    #   tests/unit/test_feature_flags.py 做双向守卫（表 ↔ 注册表 ↔ config.yaml）。
    SettingSpec(
        key="YUNSHU_FEATURE_<NAME>", category=CAT_ORCHESTRATION, type="bool",
        default=None, description=(
            "特性开关族（阶段灰度）：YUNSHU_FEATURE_<NAME> 覆盖 "
            "config.yaml 的 features.<name>；YUNSHU_FEATURE_<NAME>_ROLLOUT 控制 "
            "灰度比例 0-100。统一求值入口 "
            "agent.settings.feature_flags.feature_enabled()；UI 只读展示；"
            "路径模板 features.<name>（agent/settings/feature_flags.py:87-88 的 "
            "config_path 属性、:253/273 的 _config_lookup），<name> 由运行期变量拼出，"
            "**故 config_path 为空**（填固定路径即伪造来源）"),
        risk=RISK_A, env_name="", dynamic_prefix="YUNSHU_FEATURE_",
        owner_module="agent/settings/feature_flags.py"),

    # ────────────────────────────────────────────────────────
    #  SLO 周报调度 与 成本校准件（2026-09-13）
    # ────────────────────────────────────────────────────────
    _a("CP_SLO_SCHEDULE_ENABLED", CAT_OBSERVABILITY, False,
       "SLO 指标周报定时生成开关（每周自动生成并存档到 docs/zh/周报存档/）",
       owner="agent/monitoring/slo_report_scheduler.py",
       config_path="slo_report.enabled", needs_restart=True),
    _c("CP_SLO_SCHEDULE_OUT_DIR", CAT_OBSERVABILITY, "docs/zh/周报存档",
       "SLO 周报存档目录（只读展示）；config.yaml 路径 slo_report.out_dir"
       "（agent/monitoring/slo_report_scheduler.py:117）",
       owner="agent/monitoring/slo_report_scheduler.py",
       config_path="slo_report.out_dir",
       validator=Validator("path")),
    _c("CP_SLO_SCHEDULE_AUDIT_FILE", CAT_OBSERVABILITY,
       "data/slo_report_audit.jsonl",
       "SLO 周报运行审计文件（只读展示）；config.yaml 路径 slo_report.audit_file"
       "（agent/monitoring/slo_report_scheduler.py:122）",
       owner="agent/monitoring/slo_report_scheduler.py",
       config_path="slo_report.audit_file",
       validator=Validator("path")),
    # S7-03 成本校准引入的 env 读取点（此前未登记，2026-09-13 零缺口门抓出）
    _c("CP_UTC_CALIBRATION_FILE", CAT_OBSERVABILITY, None,
       "成本校准件路径（S7-03 实测校准结果；只读展示）",
       owner="agent/observability/cost_calibration.py",
       validator=Validator("path")),
    SettingSpec(
        key="CP_SLO_SCHEDULE_<KEY>", category=CAT_OBSERVABILITY, type="int",
        default=None, description=(
            "SLO 周报调度参数族：CP_SLO_SCHEDULE_<KEY>（DAYS / DAY_OF_WEEK / "
            "HOUR / MINUTE；由 _ENV_PREFIX + 后缀拼接，UI 只读展示）；"
            "路径模板 slo_report.<小写后缀>"
            "（agent/monitoring/slo_report_scheduler.py:64 取段、:72/:83 按后缀取叶子），"
            "后缀由运行期变量拼出，**故 config_path 为空**（填固定路径即伪造来源）"),
        risk=RISK_A, env_name="", dynamic_prefix="CP_SLO_SCHEDULE_",
        owner_module="agent/monitoring/slo_report_scheduler.py"),

    # ────────────────────────────────────────────────────────
    #  数据生命周期治理（TASK-S8-01，2026-09-13）
    # ────────────────────────────────────────────────────────
    _a("CP_RETENTION_ENABLED", CAT_OBSERVABILITY, False,
       "数据保留策略归档调度开关（默认关闭；开启后每周日 03:00 注册一次归档任务）",
       owner="agent/retention/scheduler.py",
       config_path="retention.enabled", needs_restart=True),
    _a("CP_RETENTION_DRY_RUN", CAT_OBSERVABILITY, True,
       "保留策略是否只做 dry-run（默认 true＝只列清单与体积、不落盘；"
       "**无论此值如何，本进程首跑强制 dry-run**）",
       owner="agent/retention/archiver.py",
       config_path="retention.dry_run", needs_restart=True),
    _b("CP_RETENTION_DELETE_SOURCE", CAT_OBSERVABILITY, False,
       "归档后是否删除源文件（默认 false＝只归档不删除；"
       "开启后仍需过 PurgeGuard：红线类/记忆类/未标可删/有指标依赖一律拒绝）",
       owner="agent/retention/guard.py",
       config_path="retention.delete_source", needs_restart=True),
    # 【L5（2026-09-23）】retention 段 8 个叶子此前只登记 7 个，本条是漏掉的那个
    #   （同族漏一个的典型形态）：policy.py 同一次读取里读了 section.get("archive_dir")，
    #   而本条 config_path 为空 ⇒ UI 只显示 default。
    _c("CP_RETENTION_ARCHIVE_DIR", CAT_OBSERVABILITY, "data/archive",
       "冷归档根目录（默认 data/archive；只读展示）；config.yaml 路径 "
       "retention.archive_dir（agent/retention/policy.py:637-638）",
       owner="agent/retention/policy.py",
       config_path="retention.archive_dir",
       validator=Validator("path")),
    _a("CP_RETENTION_CLASSES", CAT_OBSERVABILITY, "",
       "保留策略限定数据类（逗号分隔；空＝全部 12 类）",
       owner="agent/retention/policy.py",
       config_path="retention.classes", needs_restart=True),
    _a("CP_RETENTION_DAY_OF_WEEK", CAT_OBSERVABILITY, 6,
       "保留策略归档触发星期（0=周一 … 6=周日；Python weekday 语义）",
       owner="agent/retention/scheduler.py",
       config_path="retention.day_of_week", needs_restart=True,
       validator=_int_range(0, 6)),
    _a("CP_RETENTION_HOUR", CAT_OBSERVABILITY, 3,
       "保留策略归档触发小时（0-23）",
       owner="agent/retention/scheduler.py",
       config_path="retention.hour", needs_restart=True,
       validator=_int_range(0, 23)),
    _a("CP_RETENTION_MINUTE", CAT_OBSERVABILITY, 0,
       "保留策略归档触发分钟（0-59）",
       owner="agent/retention/scheduler.py",
       config_path="retention.minute", needs_restart=True,
       validator=_int_range(0, 59)),

    # ────────────────────────────────────────────────────────
    #  【跨任务补登】S8-05 裁定台账目录（2026-09-13）
    #  `agent/digestion/resolutions.py` 读了 `CP_RESOLUTION_DIR` 但未登记，导致
    #  `test_settings_registry.py::test_zero_gap_between_scan_and_registry` 在 master
    #  上变红（"零缺口"是硬守卫，见该文件模块头 §1/§2）。此处只**机械补登**，
    #  默认值与语义逐字取自该模块（`DEFAULT_RESOLUTION_DIR`、路径项只读展示），
    #  **不改 S8-05 的任何行为**；归属仍为 `agent/digestion/resolutions.py`（S8-05）。
    # ────────────────────────────────────────────────────────
    _c("CP_RESOLUTION_DIR", CAT_SELF_HEALING, "data/descriptors",
       "裁定台账目录覆盖（默认 data/descriptors；路径项，UI 只读展示）",
       owner="agent/digestion/resolutions.py",
       validator=Validator("path")),

    # ────────────────────────────────────────────────────────
    #  【跨任务补登】S8-02 / S8-03 / S8-04 新增 env 未登记（2026-09-13）
    #
    #  背景：S8-02（并发与运维加固）、S8-03（灰度容器隔离）、S8-04（LLM-judge）
    #  各自新增了 env 读取点，但**未登记进本表** ⇒ master 上的硬守卫
    #  `tests/unit/test_settings_registry.py::TestMechanicalZeroGap` 变红
    #  （extracted=370 / registered=331，缺 39；同一守卫此前已补过 S8-05 的
    #  `CP_RESOLUTION_DIR`，但只补了那一条）。本次**机械补登**：
    #  类型 / 默认值 / 语义逐条取自各模块的**真实读取点**（`os.getenv`、
    #  `_env_flag/_env_int/_env_float` 的 default 实参与对应常量），
    #  **不改任何行为、不改任何默认值**。
    #
    #  【风险级裁定口径（保守优先）】不作 A 级（"可直接切"）的情形：
    #    · 关掉**完整性强保护**（跨进程锁）会让写入交错/损坏 ⇒ **B**；
    #    · 打开/放宽**成本护栏**（judge 预算与断食联动、接管预算）⇒ **B**；
    #    · 打开**真实流量接管**（本系统最高危动作）⇒ **B**（代码侧另要求显式比例）；
    #    · **路径 / 密钥文件位置**（台账目录、.env、密钥文件）⇒ **C**（只读脱敏）。
    #    其余纯数值上限 / 超时 / 非安全开关保持 A。
    #    ⚠️ 若后续复核认为某条应升/降级，请**连依据一起改**，不要只改级别。
    #
    #  【needs_restart 口径】仅在**构造期读取一次**的项（事件库 / Trace 库 /
    #  决策日志在实例化时读）标 `True`；每次调用都读的项保持 `False`。
    # ────────────────────────────────────────────────────────

    # ── S8-02 跨进程锁与决策日志轮转 ─────────────────────────
    _b("CP_ARCHIVE_LOCK_ENABLED", CAT_OBSERVABILITY, True,
       "归档跨进程锁开关（默认开）；关闭后并发归档不再互斥，可能损坏归档产物",
       owner="agent/skills_mgmt/log_archiver.py"),
    _a("CP_ARCHIVE_LOCK_TIMEOUT_SEC", CAT_OBSERVABILITY, 5.0,
       "归档锁有限等待上限（秒，默认 5.0）；等不到即走既有降级路径",
       owner="agent/skills_mgmt/log_archiver.py"),
    _b("CP_EVENTS_LOCK_ENABLED", CAT_OBSERVABILITY, True,
       "事件库跨进程锁开关（默认开）；关闭后多写者不再互斥（测试/应急用）",
       owner="agent/observability/events.py", needs_restart=True),
    _a("CP_EVENTS_LOCK_TIMEOUT_SEC", CAT_OBSERVABILITY, 2.0,
       "事件库取锁有限等待（秒，默认 2.0）；等不到转排队降级",
       owner="agent/observability/events.py", needs_restart=True),
    _a("CP_EVENTS_PENDING_MAX", CAT_OBSERVABILITY, 10000,
       "事件库待写队列上限（默认 10000），满即按既有降级路径处理",
       owner="agent/observability/events.py", needs_restart=True),
    _a("CP_EVENTS_CORRUPT_LOG_EVERY", CAT_OBSERVABILITY, 100,
       "事件损坏告警限流：第 1 条必报，其后每 N 条报一次（默认 100）",
       owner="agent/observability/events.py", needs_restart=True),
    _b("CP_TRACE_LOCK_ENABLED", CAT_OBSERVABILITY, True,
       "统一 Trace 跨进程锁开关（默认开）；关闭后并发写不再互斥",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_LOCK_TIMEOUT_SEC", CAT_OBSERVABILITY, 2.0,
       "统一 Trace 取锁有限等待（秒，默认 2.0）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_QUEUE_MAXSIZE", CAT_OBSERVABILITY, 20000,
       "统一 Trace 写入队列上限（默认 20000）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_RING_BUFFER_MAXLEN", CAT_OBSERVABILITY, 1000,
       "统一 Trace 降级时内存 ring buffer 容量（默认 1000）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_DEGRADED_RETRY_SEC", CAT_OBSERVABILITY, 2.0,
       "统一 Trace 降级后重试退避间隔（秒，默认 2.0）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_WAL_AUTOCHECKPOINT", CAT_OBSERVABILITY, 1000,
       "统一 Trace SQLite WAL 自动 checkpoint 页数（默认 1000；0=关闭）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_TRACE_CORRUPT_LOG_EVERY", CAT_OBSERVABILITY, 100,
       "统一 Trace 损坏告警限流：第 1 次必报，其后每 N 次报一次（默认 100）",
       owner="agent/observability/trace_v2.py", needs_restart=True),
    _a("CP_POLICY_DECISION_LOG_MAX_BYTES", CAT_SELF_HEALING, 0,
       "策略决策日志轮转阈值（字节，默认 0=不轮转；S8 原则：默认保守）",
       owner="agent/policy/decisions.py", needs_restart=True),
    _a("CP_POLICY_DECISION_LOG_ROTATE_DAILY", CAT_SELF_HEALING, False,
       "策略决策日志是否按天轮转（默认关）",
       owner="agent/policy/decisions.py", needs_restart=True),
    _a("CP_POLICY_DECISION_LOG_KEEP_BYTES", CAT_SELF_HEALING, 0,
       "策略决策日志轮转时保留字节数（默认 0）",
       owner="agent/policy/decisions.py", needs_restart=True),
    _a("CP_POLICY_DECISION_LOG_DAILY_CHECK_SECONDS", CAT_SELF_HEALING, 60.0,
       "策略决策日志按天轮转的检查间隔（秒，默认 60.0）",
       owner="agent/policy/decisions.py", needs_restart=True),
    # 【S8-02 补登：三态开关】`None` 默认值是刻意的——语义为 **auto**
    # （仅轮转开启时才取跨进程锁）；"1" 强制取 / "0" 强制不取。
    # 该开关一度**读得到但扫不出**（读取助手 `_env_optional_flag` 不在
    # `scan_settings.KNOWN_READ_HELPERS` 内）⇒ 零缺口硬守卫假阴性；
    # 已同时补名单（见 scripts/scan_settings.py）。
    _a("CP_POLICY_DECISION_LOG_LOCK_APPENDS", CAT_SELF_HEALING, None,
       "策略决策日志 append 是否取跨进程锁（未设置=auto：仅轮转开启时取；1=强制；0=强制不取）",
       owner="agent/policy/decisions.py", needs_restart=True),

    # ── S8-03 灰度容器隔离与真实接管 ─────────────────────────
    _a("CP_DIGESTION_ISOLATION_MEMORY_MB", CAT_SKILLS, 256,
       "灰度隔离子进程内存上限 MB（默认 256，下限 16）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_CPUS", CAT_SKILLS, 1.0,
       "灰度隔离子进程 CPU 配额（默认 1.0，下限 0.1）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_PIDS_LIMIT", CAT_SKILLS, 64,
       "灰度隔离子进程 PID 上限（默认 64，下限 8）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_TMPFS_MB", CAT_SKILLS, 32,
       "灰度隔离 tmpfs 上限 MB（默认 32，下限 4）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_TIMEOUT_S", CAT_SKILLS, 30.0,
       "灰度隔离子进程超时（秒，默认 30.0，下限 0.5）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_MAX_OUTPUT_BYTES", CAT_SKILLS, 65536,
       "灰度隔离子进程输出上限字节（默认 65536）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_KEEP_WORK_DIR", CAT_SKILLS, False,
       "隔离探测后是否保留工作目录（默认否；开启会留下临时产物）",
       owner="agent/digestion/isolation.py"),
    _a("CP_DIGESTION_ISOLATION_PROBE_TTL_S", CAT_SKILLS, 60.0,
       "隔离能力探测结果缓存 TTL（秒，默认 60.0）",
       owner="agent/digestion/isolation.py"),
    _b("CP_DIGESTION_REAL_TAKEOVER", CAT_SKILLS, False,
       "灰度**真实流量接管**总开关（默认关；最高危动作，代码另要求显式接管比例）",
       owner="agent/digestion/takeover.py"),
    _b("CP_DIGESTION_REAL_TAKEOVER_BUDGET_CAP", CAT_SKILLS, 50,
       "真实接管预算上限（默认 50）；放开即放宽接管的成本护栏",
       owner="agent/digestion/takeover.py"),
    _b("CP_DIGESTION_REAL_TAKEOVER_MIN_BUDGET", CAT_SKILLS, 1,
       "真实接管所需最小预算余额（默认 1）；调低即放宽接管准入",
       owner="agent/digestion/takeover.py"),
    _b("CP_DIGESTION_REAL_TAKEOVER_FAIL_THRESHOLD", CAT_SKILLS, 3,
       "真实接管连续失败熔断阈值（默认 3）；调高即放宽熔断",
       owner="agent/digestion/takeover.py"),
    _c("CP_DIGESTION_TAKEOVER_DIR", CAT_SKILLS, "",
       "真实接管台账目录（缺省由代码按运行时数据目录解析；路径项，UI 只读展示）",
       owner="agent/digestion/takeover.py", validator=Validator("path")),

    # ── S8-04 真实 LLM-judge 与成本护栏 ──────────────────────
    _b("CP_DIGESTION_JUDGE_ENABLED", CAT_SKILLS, False,
       "真实 LLM-judge 总开关（默认关）；打开即产生真实模型调用成本",
       owner="agent/digestion/judge_runtime.py"),
    _b("CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS", CAT_SKILLS, 100.0,
       "judge 每日预算上限（分，默认 100.0）；调高即放宽 judge 成本护栏",
       owner="agent/digestion/judge_runtime.py"),
    _b("CP_DIGESTION_JUDGE_FOLLOW_FASTING", CAT_SKILLS, True,
       "judge 是否联动成本断食（默认是）；关闭后断食期间 judge 仍会花钱",
       owner="agent/digestion/judge_runtime.py"),
    _a("CP_DIGESTION_JUDGE_THRESHOLD", CAT_SKILLS, 0.85,
       "judge 软性判定阈值（默认 0.85，与 v7.2 §4.5 一致）",
       owner="agent/digestion/judge_runtime.py"),
    _a("CP_DIGESTION_JUDGE_PROVIDER", CAT_SKILLS, "",
       "judge 模型提供方（空=自动；取值见模型适配层 provider 清单）",
       owner="agent/digestion/shadow.py"),
    _a("CP_DIGESTION_JUDGE_MODEL", CAT_SKILLS, "",
       "judge 模型名（空=自动；需与 provider 匹配）",
       owner="agent/digestion/shadow.py"),
    _a("CP_DIGESTION_JUDGE_CHARS_PER_TOKEN", CAT_SKILLS, 4.0,
       "judge 成本估算的字符/token 系数（默认 4.0）",
       owner="agent/digestion/judge_runtime.py"),
    _c("CP_DIGESTION_JUDGE_SECRET_FILE", CAT_SKILLS, "",
       "judge 密钥文件位置（空=不启用文件取密钥；路径项，UI 只读展示）",
       owner="agent/digestion/judge_runtime.py", validator=Validator("path")),
    _c("CP_DIGESTION_JUDGE_DOTENV", CAT_SKILLS, "",
       "judge 读取的 .env 路径覆盖（空=默认；路径项，UI 只读展示）",
       owner="agent/digestion/judge_runtime.py", validator=Validator("path")),
]
# ════════════════════════════════════════════════════════════
#  与 observability_config 既有校验表合并（**勿重复造**）
# ════════════════════════════════════════════════════════════

#: observability 配置路径 → (env_name, 校验器, 分类, 风险, 说明补充)
#: 说明：**默认值与说明文字直接取自 `OBSERVABILITY_VALIDATION_RULES`**
#:（该表是既有事实源，本表只补 UI 需要的类别/风险/env 名）。
_OC_MERGE: Dict[str, Tuple[str, Validator, str, str, str]] = {
    "tracing.env": ("TRACING_ENV", _enum("development", "staging", "production"),
                    CAT_OBSERVABILITY, RISK_A, ""),
    "tracing.log_level": ("TRACING_LOG_LEVEL",
                          _enum("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"),
                          CAT_OBSERVABILITY, RISK_A, ""),
    "tracing.sampler_ratio": ("TRACING_SAMPLER_RATIO",
                              _range_validator(0.0, 1.0),
                              CAT_OBSERVABILITY, RISK_A, ""),
    "logging.level": ("", _enum("DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"),
                      CAT_OBSERVABILITY, RISK_A, ""),
    "logging.output_path": ("", Validator("path"), CAT_OBSERVABILITY, RISK_C,
                            "路径项：UI 只读展示"),
    "metrics.enabled": ("", BOOL_V, CAT_OBSERVABILITY, RISK_A, ""),
    "health_check.interval_sec": ("", _int_range(5, 3600),
                                  CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.enabled": ("", BOOL_V, CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.sample_interval_sec": ("", _int_range(1, 3600),
                                             CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.stress_test_interval_sec": ("", _range_validator(0.5, 10),
                                                   CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.history_size": ("", Validator("int"),
                                      CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.leak_slope_threshold": ("", Validator("float"),
                                              CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.thread_join_timeout_sec": ("", _int_range(1, 60),
                                                  CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.persist_enabled": ("", BOOL_V, CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.persist_path": ("", Validator("path"),
                                      CAT_OBSERVABILITY, RISK_C, "路径项：UI 只读展示"),
    "resource_monitor.persist_batch_size": ("", Validator("int"),
                                            CAT_OBSERVABILITY, RISK_A, ""),
    "resource_monitor.persist_max_age_hours": ("", Validator("int"),
                                                CAT_OBSERVABILITY, RISK_A, ""),
    "retry.default_max_retries": ("", Validator("int"),
                                  CAT_OBSERVABILITY, RISK_A, ""),
    "cognitive.reflection_max_retries": ("", Validator("int"),
                                         CAT_OBSERVABILITY, RISK_A, ""),
    "http.max_retries": ("", Validator("int"), CAT_OBSERVABILITY, RISK_A, ""),
    "http.timeout_sec": ("", Validator("int"), CAT_OBSERVABILITY, RISK_A, ""),
    "http.connect_timeout_sec": ("", Validator("int"),
                                 CAT_OBSERVABILITY, RISK_A, ""),
    "http.pool_size": ("", Validator("int"), CAT_OBSERVABILITY, RISK_A, ""),
    "cache.l1_max_size": ("", Validator("int"), CAT_OBSERVABILITY, RISK_A, ""),
    "tracing_cache.context_max_size": ("", Validator("int"),
                                       CAT_OBSERVABILITY, RISK_A, ""),
    "tracing_cache.span_max_size": ("", Validator("int"),
                                    CAT_OBSERVABILITY, RISK_A, ""),
    "tracing_cache.span_pool_size": ("", Validator("int"),
                                     CAT_OBSERVABILITY, RISK_A, ""),
    "scheduler.check_interval_sec": ("", Validator("int"),
                                     CAT_OBSERVABILITY, RISK_A, ""),
    "scheduler.command_timeout_sec": ("", Validator("int"),
                                      CAT_OBSERVABILITY, RISK_A, ""),
    "scheduler.max_history_lines": ("", Validator("int"),
                                    CAT_OBSERVABILITY, RISK_A, ""),
    "scheduler.heartbeat_interval_sec": ("", Validator("int"),
                                         CAT_OBSERVABILITY, RISK_A, ""),
    "scheduler.max_heartbeat_history": ("", Validator("int"),
                                        CAT_OBSERVABILITY, RISK_A, ""),
    "llm_monitor.max_records": ("", Validator("int"),
                                CAT_OBSERVABILITY, RISK_A, ""),
    "loki.push_timeout_sec": ("", Validator("int"),
                              CAT_OBSERVABILITY, RISK_A, ""),
    "loki.query_timeout_sec": ("", Validator("int"),
                               CAT_OBSERVABILITY, RISK_A, ""),
    "alert.timeout_sec": ("", Validator("int"), CAT_OBSERVABILITY, RISK_A, ""),
    "takeover.timeout_sec": ("", Validator("float"),
                             CAT_OBSERVABILITY, RISK_A, ""),
    "prometheus.max_retries": ("", Validator("int"),
                               CAT_OBSERVABILITY, RISK_A, ""),
    "chaos.thread_join_timeout_sec": ("", Validator("int"),
                                      CAT_OBSERVABILITY, RISK_A, ""),
    "search.thread_join_timeout_sec": ("", Validator("int"),
                                       CAT_OBSERVABILITY, RISK_A, ""),
    "search.config_apply_timeout_sec": ("", Validator("int"),
                                        CAT_OBSERVABILITY, RISK_A, ""),
    "search.web_search_timeout_sec": ("", Validator("int"),
                                      CAT_OBSERVABILITY, RISK_A, ""),
    "search.status_check_timeout_sec": ("", Validator("int"),
                                        CAT_OBSERVABILITY, RISK_A, ""),
    "self_healer.restart_timeout_sec": ("", Validator("int"),
                                        CAT_OBSERVABILITY, RISK_A, ""),
    "self_healer.sync_timeout_sec": ("", Validator("int"),
                                     CAT_OBSERVABILITY, RISK_A, ""),
    "self_healer.verify_timeout_sec": ("", Validator("int"),
                                       CAT_OBSERVABILITY, RISK_A, ""),
    "self_healer.thread_join_timeout_sec": ("", Validator("int"),
                                            CAT_OBSERVABILITY, RISK_A, ""),
    "time_window.max_analyze_days": ("", Validator("int"),
                                     CAT_OBSERVABILITY, RISK_A, ""),
    "knowledge.file_lock_timeout_sec": ("", Validator("int"),
                                        CAT_OBSERVABILITY, RISK_A, ""),
}

#: takeon.timeout_sec 亦在规则表内（写法与 _OC_MERGE 的键保持一致）
_OC_OWNER = "agent/monitoring/observability_config.py"


def _merge_observability_specs() -> List[SettingSpec]:
    """把 `OBSERVABILITY_VALIDATION_RULES` 机械合并进注册表

    **默认值/说明文字/路径三件事都取自该既有表**（守不易：不重复造一份），
    本函数只补 UI 需要的 env 名、类别与风险级；校验器由 `_OC_MERGE` 声明
    （因为规则表里的校验器是**闭包**，无法安全内省，故显式列出并受单测守护：
    规则表里的每个 path 必须在本表有对应 spec，且 default 逐条相等）。
    """
    from agent.monitoring.observability_config import (
        OBSERVABILITY_VALIDATION_RULES,
    )

    specs: List[SettingSpec] = []
    for rule in OBSERVABILITY_VALIDATION_RULES:
        meta = _OC_MERGE.get(rule.path)
        if meta is None:
            # 未在合并表声明 → 仍合并（默认值/说明取自规则表），但如实标注
            meta = ("", Validator("str"), CAT_OBSERVABILITY, RISK_A,
                    "未在 _OC_MERGE 声明校验器，按只读处理")
        env_name, validator, category, risk, extra = meta
        desc = rule.description or rule.error_message or rule.path
        if extra:
            desc = f"{desc}；{extra}"
        key = env_name or rule.path
        specs.append(SettingSpec(
            key=key, category=category,
            type=_infer_type(key, rule.default), default=rule.default,
            description=desc, risk=risk, env_name=env_name,
            config_path=rule.path, owner_module=_OC_OWNER,
            validator=validator, apply_mode=EFFECT_HOT,
            needs_restart=False,
            rollback=(f"回滚：POST /api/cp/settings/{key}/reset 清除覆盖层"),
            impact=(f"影响面：{desc}（运行时配置，热生效）"
                    if risk == RISK_B else "")))
    return specs


# ════════════════════════════════════════════════════════════
#  索引（对外只读）
# ════════════════════════════════════════════════════════════

def _build_registry() -> List[SettingSpec]:
    rows = list(_REGISTRY_ROWS) + _merge_observability_specs()
    seen: Dict[str, SettingSpec] = {}
    for spec in rows:
        if spec.key in seen:
            raise ValueError(f"开关注册表键重复：{spec.key}")
        if not spec.description.strip():
            raise ValueError(f"开关 {spec.key} 缺少说明（description 必填）")
        if spec.risk not in RISK_LABELS:
            raise ValueError(f"开关 {spec.key} 风险级非法：{spec.risk}")
        if spec.category not in CATEGORY_LABELS:
            raise ValueError(f"开关 {spec.key} 分类非法：{spec.category}")
        if spec.type not in ("bool", "int", "float", "str", "path"):
            raise ValueError(f"开关 {spec.key} 类型非法：{spec.type}")
        if not spec.env_name and not spec.config_path and not spec.dynamic_prefix:
            raise ValueError(f"开关 {spec.key} 既无 env_name 也无 config_path")
        seen[spec.key] = spec
    return [seen[k] for k in sorted(seen)]


#: 全部开关（按 key 排序，稳定顺序便于 UI 与断言）
REGISTRY: Tuple[SettingSpec, ...] = tuple(_build_registry())

#: key → spec
_BY_KEY: Dict[str, SettingSpec] = {s.key: s for s in REGISTRY}

#: env_name → spec（空 env_name 不入索引）
_BY_ENV: Dict[str, SettingSpec] = {
    s.env_name: s for s in REGISTRY if s.env_name}

#: config_path → spec
_BY_CONFIG: Dict[str, SettingSpec] = {
    s.config_path: s for s in REGISTRY if s.config_path}


# ════════════════════════════════════════════════════════════
#  查询 API
# ════════════════════════════════════════════════════════════

def all_specs() -> Tuple[SettingSpec, ...]:
    """全部开关（稳定顺序）"""
    return REGISTRY


def get_spec(key: str) -> Optional[SettingSpec]:
    """按键取元数据（未知键 → None，调用方 fail-closed）"""
    if not key:
        return None
    return _BY_KEY.get(str(key))


def spec_for_env(env_name: str) -> Optional[SettingSpec]:
    return _BY_ENV.get(str(env_name or ""))


def spec_for_config(config_path: str) -> Optional[SettingSpec]:
    return _BY_CONFIG.get(str(config_path or ""))


def registered_env_names() -> set:
    """注册表覆盖的环境变量名集合（`scripts/scan_settings.py` 缺口检查用）"""
    return {s.env_name for s in REGISTRY if s.env_name}


def dynamic_prefixes() -> set:
    """声明过的动态开关家族前缀（缺口检查用）"""
    return {s.dynamic_prefix for s in REGISTRY if s.dynamic_prefix}


def categories() -> List[Dict[str, Any]]:
    """分类清单（含每类条目数，UI 直接用）"""
    counts: Dict[str, int] = {c: 0 for c in CATEGORY_ORDER}
    for spec in REGISTRY:
        counts[spec.category] = counts.get(spec.category, 0) + 1
    return [{"id": c, "label": CATEGORY_LABELS[c], "count": counts.get(c, 0)}
            for c in CATEGORY_ORDER]


def counts_by_risk() -> Dict[str, int]:
    out = {RISK_A: 0, RISK_B: 0, RISK_C: 0}
    for spec in REGISTRY:
        out[spec.risk] = out.get(spec.risk, 0) + 1
    return out


def observability_rule_paths() -> set:
    """既有校验表的路径集合（供单测做「零重造」双向断言）"""
    from agent.monitoring.observability_config import (
        OBSERVABILITY_VALIDATION_RULES,
    )
    return {r.path for r in OBSERVABILITY_VALIDATION_RULES}


__all__ = [
    # 分类
    "CAT_SELF_HEALING", "CAT_LEARNING", "CAT_ORCHESTRATION", "CAT_SKILLS",
    "CAT_OBSERVABILITY", "CAT_EXTERNAL", "CATEGORY_LABELS", "CATEGORY_ORDER",
    # 风险
    "RISK_A", "RISK_B", "RISK_C", "RISK_LABELS", "SECOND_FACTOR_RISKS",
    # 生效方式
    "EFFECT_HOT", "EFFECT_RESTART", "EFFECT_NEXT_TASK", "EFFECT_LABELS",
    # 结构与表
    "Validator", "SettingSpec", "REGISTRY", "all_specs", "get_spec",
    "spec_for_env", "spec_for_config", "registered_env_names",
    "dynamic_prefixes", "categories", "counts_by_risk",
    "observability_rule_paths", "BOOL_V",
]
