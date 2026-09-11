"""Policy schema（v7.2 §3.11）与决策模型（§5.6 / §6.6 / P7.1-20）

【本模块负责什么】
    只负责「策略是什么」与「决策长什么样」——纯数据与校验，**不做任何 I/O、
    不做任何网络动作**（P7.1-20：策略引擎只出决策）。判定语义在 ``matcher.py``，
    装载/版本在 ``store.py``，编排在 ``engine.py``。

【§3.11 字段对齐】
    设计文档给出的最小规格（逐字）：

        // Policy
        { id, version, owner, effect: "allow|deny|ask",
          match: {<OPA 子集>}, message_template: "用户文案",
          effective_range, break_glass_ttl_min: null, signature: "ed25519:..." }

    本模块按此九字段落地，**不增删字段**。设计文档未闭合的三处由云枢裁定
    （下方逐条标注），裁定只落在「取值域/结构」层面，不改变字段集合：

    1. ``version``：设计文档未给形态。裁定用 **SemVer**（与 §3.2 meta.version
       同口径），因为策略版本要与 capability 版本在同一处比对，两套版本语法
       会让「策略版本变更 ⇒ 组装缓存失效」（§4.1 P7.1-18）无法统一判定。
    2. ``effective_range``：设计文档未给结构。裁定为 dict，允许四个可选键：
       ``not_before`` / ``not_after``（ISO-8601 字符串，闭区间）、
       ``tenants``（租户白名单，空/缺省＝不限）、``scopes``（capability_id
       的 fnmatch 通配白名单，空/缺省＝不限）。四个键全缺省＝全局长期生效。
       之所以不做「数组区间」：区间端点必须是可比较的标量，时间用 ISO 字符串
       前缀/字典序比较即可覆盖「按日/按周」口径。
    3. ``break_glass_ttl_min``：设计文档默认 ``null``。裁定语义为
       「该策略允许被 break-glass 例外的**上限分钟数**」；``null`` ＝ 不可例外
       （绝对 deny）。例外不是静默放行：它必须由人显式授予、有 TTL、入审计
       （见 ``engine.PolicyEngine.grant_break_glass``），且**不改变策略本身**。

【判定失效口径（守不易）】
    - 引擎**不**因策略未覆盖而拒绝：未命中任何策略 ⇒ ``matched=False``，
      执行点回落既有判定（权限网关 RBAC/ABAC/正则）。这是「零行为回归」的
      结构性保证，不是调用方的责任。
    - 引擎**不**提供 ``http.send`` 类能力：``match`` 里出现任何网络/执行类
      token 一律判为非法策略（``FORBIDDEN_MATCH_TOKENS``），在装载期拦截。
"""

from __future__ import annotations

import enum
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ════════════════════════════════════════════════════════════
#  常量
# ════════════════════════════════════════════════════════════

#: 策略文件 schema 标识（``data/policies/policies.json`` 顶层）
POLICY_SCHEMA = "policy.v1"

EFFECT_ALLOW = "allow"
EFFECT_DENY = "deny"
EFFECT_ASK = "ask"

#: 决策结果取值（§5.6 ``check(policy_ctx) -> {allow|deny|ask, policy_id}``）
EFFECTS: Tuple[str, ...] = (EFFECT_ALLOW, EFFECT_DENY, EFFECT_ASK)

#: 签名方案前缀（与 S2-02 ``agent.audit.chain`` 的 ``ed25519`` / ``sha256-self``
#: 降级口径一致：无 ``cryptography`` 或无私钥时退化为 sha256 自签占位）。
SIGN_SCHEME_ED25519 = "ed25519"
SIGN_SCHEME_SHA256_SELF = "sha256-self"

#: ``match`` 表达式中**禁止出现**的 token（P7.1-20：LLM 不得在策略中生成网络副作用）
#:
#: 判定方式：把 ``match`` 子树做 JSON 规范化后小写化，检查是否含下列任一子串。
#: 这不是「沙箱」——本引擎本就没有网络能力；该检查的作用是**在装载期拒绝**
#: 一类「意图越权」的策略文本（从设计文档 5.6 的 Rego 伪代码抄来的
#: ``http.send(_)`` 就是真实会发生的抄写错误），让它无法被静默接受。
FORBIDDEN_MATCH_TOKENS: Tuple[str, ...] = (
    "http.send", "http_send", "http.send_", "https.send",
    "http.request", "http_request", "https.request",
    "net.http", "net.send", "socket", "fetch(", "curl ",
    "subprocess", "os.system", "os.popen", "exec(", "eval(",
    "import ", "require(", "__import__",
    "requests.", "urllib", "httpx", "aiohttp",
)

_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z\-\.]+)?(?:\+[0-9A-Za-z\-\.]+)?$"
)
#: 策略 id 形态：**首字符须为小写字母**的分段小写点分
#: （如 ``sec.secret-egress-deny`` / ``builtin.invariant.secret-egress-deny``）。
#: 首字符限制为字母是为了让 id 在日志/CLI 里可读，且避免与纯数字版本号混淆。
_POLICY_ID_RE = re.compile(r"^[a-z][a-z0-9_.:-]{1,127}$")

#: ``message_template`` 只允许 ``{simple_name}`` 占位（安全渲染，见 render_message）
_TEMPLATE_FIELD_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


class PolicyError(Exception):
    """策略层基类异常"""


class PolicyValidationError(PolicyError):
    """策略校验失败（装载期 / 显式 validate 期）

    Attributes:
        policy_id: 失败策略 id（无法解析时为 None）
        errors: 人类可读错误清单
        code: 错误类别（默认 INVALID_POLICY）
    """

    def __init__(self, errors: Sequence[str], *, policy_id: Optional[str] = None,
                 code: str = "INVALID_POLICY") -> None:
        self.errors = [str(e) for e in errors]
        self.policy_id = policy_id
        self.code = code
        prefix = f"[{policy_id}] " if policy_id else ""
        super().__init__(f"{prefix}策略非法: {'; '.join(self.errors)}")


class Effect(str, enum.Enum):
    """策略效果（§3.11 ``effect: "allow|deny|ask"``）

    ``ask`` 与审批流是**两个概念**（START S4-02 §七.4）：``ask`` 表示「策略层
    要求人工介入」，它由执行点路由到审批/收件箱，**不在策略引擎内审批**。
    """

    ALLOW = EFFECT_ALLOW
    DENY = EFFECT_DENY
    ASK = EFFECT_ASK

    @classmethod
    def parse(cls, value: Any) -> "Effect":
        if isinstance(value, Effect):
            return value
        text = str(getattr(value, "value", value) or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        raise PolicyValidationError(
            [f"effect 取值非法: {value!r}（应为 {'|'.join(EFFECTS)}）"],
            code="INVALID_EFFECT")


# ════════════════════════════════════════════════════════════
#  工具：规范化 / 脱敏
# ════════════════════════════════════════════════════════════


def canonical_json(value: Any) -> str:
    """稳定 JSON（sort_keys + 紧凑分隔符）；**不可序列化时返回 ``""``**

    用于缓存键、签名材料与决策日志的幂等比较——**同一策略文本必得同一字符串**。

    **刻意不加 ``default=str``**：那会把 live 对象（Session/descriptor/Trace）序列化成
    带内存地址的 repr，于是「同一个输入每次得到不同的缓存键」——缓存永不命中，而且
    失败是静默的。宁可返回空串让调用方显式跳过缓存（``engine._cache_key`` 就是这么
    做的），也不要一个看似成功、实则失效的键。

    副作用是：签名材料与决策日志里出现不可序列化对象时同样返回空串，调用方据此
    判定「这条数据不合法」，而不是把 repr 落盘。
    """
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except Exception:  # noqa: BLE001 不可序列化 → 交由调用方决定（返回空串）
        return ""


def sha256_hex(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def now_iso(ts: Optional[Any] = None) -> str:
    """当前（或指定）时刻的本地带偏移 ISO-8601（与 observability.events.now_ts 同口径）"""
    if ts is None:
        return datetime.now().astimezone().isoformat(timespec="milliseconds")
    if isinstance(ts, datetime):
        return ts.astimezone().isoformat(timespec="milliseconds")
    return str(ts)


def render_message(template: str, values: Optional[Dict[str, Any]] = None) -> str:
    """安全渲染 ``message_template``（用户文案）

    只替换 ``{simple_name}`` 形态的占位符，**不做** ``str.format``：

        ``"{a.__class__}".format(a=x)`` 会经属性访问读到类型对象；把 LLM/策略
        作者可控的模板交给 ``format`` 等于开一个属性遍历面。这里用白名单正则
        逐名替换，未提供的占位符**原样保留**（便于发现文案缺参，而不是抛异常
        打断决策路径）。
    """
    text = str(template or "")
    mapping = {str(k): v for k, v in (values or {}).items()}

    def _sub(match: "re.Match[str]") -> str:
        name = match.group(1)
        if name not in mapping:
            return match.group(0)
        return str(mapping[name])

    return _TEMPLATE_FIELD_RE.sub(_sub, text)


def template_fields(template: str) -> List[str]:
    """模板引用的占位符名清单（供校验：占位符必须是 ctx 可提供的叶子键）"""
    return [m.group(1) for m in _TEMPLATE_FIELD_RE.finditer(str(template or ""))]


def _scan_forbidden(value: Any) -> List[str]:
    """在 ``match`` 子树中扫描禁用 token（P7.1-20 装载期拦截）"""
    blob = canonical_json(value).lower()
    if not blob:
        return []
    hits = [tok for tok in FORBIDDEN_MATCH_TOKENS if tok in blob]
    return sorted(set(hits))


# ════════════════════════════════════════════════════════════
#  effective_range（云枢裁定 #2）
# ════════════════════════════════════════════════════════════

_EFFECTIVE_RANGE_KEYS = ("not_before", "not_after", "tenants", "scopes")


@dataclass(frozen=True)
class EffectiveRange:
    """策略生效范围（设计文档未给结构，云枢裁定见模块 docstring #2）

    Attributes:
        not_before / not_after: ISO-8601 字符串，含端点；空串＝不限。
        tenants: 租户白名单（空＝不限）。
        scopes: capability_id 的 fnmatch 通配白名单（空＝不限）。
    """

    not_before: str = ""
    not_after: str = ""
    tenants: Tuple[str, ...] = ()
    scopes: Tuple[str, ...] = ()

    # ── 构造 / 校验 ──

    @classmethod
    def parse(cls, value: Any, *, policy_id: Optional[str] = None) -> "EffectiveRange":
        """从 dict / None / EffectiveRange 构造；非法键与非法类型一律报错"""
        if value is None or value == "":
            return cls()
        if isinstance(value, EffectiveRange):
            return value
        if not isinstance(value, dict):
            raise PolicyValidationError(
                [f"effective_range 应为 dict 或 null，got {type(value).__name__}"],
                policy_id=policy_id, code="INVALID_EFFECTIVE_RANGE")
        unknown = [k for k in value if k not in _EFFECTIVE_RANGE_KEYS]
        if unknown:
            raise PolicyValidationError(
                [f"effective_range 含未知键 {sorted(unknown)}"
                 f"（允许 {list(_EFFECTIVE_RANGE_KEYS)}）"],
                policy_id=policy_id, code="INVALID_EFFECTIVE_RANGE")
        return cls(
            not_before=str(value.get("not_before") or ""),
            not_after=str(value.get("not_after") or ""),
            tenants=tuple(str(t) for t in (value.get("tenants") or ())),
            scopes=tuple(str(s) for s in (value.get("scopes") or ())),
        )

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        if self.not_before:
            out["not_before"] = self.not_before
        if self.not_after:
            out["not_after"] = self.not_after
        if self.tenants:
            out["tenants"] = list(self.tenants)
        if self.scopes:
            out["scopes"] = list(self.scopes)
        return out

    # ── 判定 ──

    def covers_tenant(self, tenant_id: str) -> bool:
        return (not self.tenants) or (str(tenant_id or "") in self.tenants)

    def covers_scope(self, capability_id: str) -> bool:
        if not self.scopes:
            return True
        cid = str(capability_id or "")
        return any(fnmatchcase(cid, pattern) for pattern in self.scopes)

    def covers_time(self, ts: Optional[Any] = None) -> bool:
        """按 ISO 字符串前缀/字典序比较（带偏移与朴素本地时间混用时以字典序为准，
        与 S2 事件层 ``iter_events(since/until)`` 同口径）。"""
        moment = now_iso(ts) if ts is not None else now_iso()
        if self.not_before and moment < self.not_before:
            return False
        if self.not_after and moment > self.not_after:
            return False
        return True

    def is_active(self, *, tenant_id: str = "", capability_id: str = "",
                  ts: Optional[Any] = None) -> bool:
        return (self.covers_time(ts) and self.covers_tenant(tenant_id)
                and self.covers_scope(capability_id))

    # ── 具体度（store 排序用：范围越窄越具体） ──

    def specificity(self) -> int:
        return len(self.tenants) + len(self.scopes) + (1 if self.not_before else 0) \
            + (1 if self.not_after else 0)


# ════════════════════════════════════════════════════════════
#  Policy（§3.11）
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Policy:
    """一条策略（§3.11 九个字段，一个不多一个不少）

    不可变：策略变更＝**新对象 + 新 version**，绝不原地改写（版本化纪律）。
    """

    id: str
    version: str
    owner: str
    effect: Effect
    match: Dict[str, Any] = field(default_factory=dict)
    message_template: str = ""
    effective_range: EffectiveRange = field(default_factory=EffectiveRange)
    break_glass_ttl_min: Optional[int] = None
    signature: str = ""
    #: 派生字段（不入 §3.11 schema，由 store 在装载时填充，供审计/模拟器定位来源）
    source_ref: str = ""

    # ── 构造 ──

    @classmethod
    def parse(cls, value: Any, *, source_ref: str = "") -> "Policy":
        """从 dict 构造（严格校验；任何越界都在此处报错，不留给运行期）"""
        if isinstance(value, Policy):
            return value
        if not isinstance(value, dict):
            raise PolicyValidationError(
                [f"策略应为 dict，got {type(value).__name__}"],
                code="INVALID_POLICY_SHAPE")

        raw_id = str(value.get("id") or "").strip()
        errors: List[str] = []
        if not raw_id:
            errors.append("缺 id")
        elif not _POLICY_ID_RE.match(raw_id):
            errors.append(f"id 形态非法: {raw_id!r}（应为分段小写点分，≤128 字符）")

        pid = raw_id or None

        raw_version = str(value.get("version") or "").strip()
        if not raw_version:
            errors.append("缺 version")
        elif not _SEMVER_RE.match(raw_version):
            errors.append(f"version 非法: {raw_version!r}（应为 SemVer）")

        owner = str(value.get("owner") or "").strip()
        if not owner:
            errors.append("缺 owner（策略必须有人负责）")

        if "effect" not in value:
            errors.append("缺 effect")
        effect: Optional[Effect] = None
        if "effect" in value:
            try:
                effect = Effect.parse(value.get("effect"))
            except PolicyValidationError as e:
                errors.extend(e.errors)

        match = value.get("match")
        if match is None:
            errors.append("缺 match（空 match 会命中全部输入，必须显式声明）")
            match = {}
        elif not isinstance(match, dict):
            errors.append(f"match 应为 dict（OPA 子集表达式），got {type(match).__name__}")
            match = {}
        else:
            forbidden = _scan_forbidden(match)
            if forbidden:
                errors.append(
                    "match 含被禁 token " + ", ".join(repr(t) for t in forbidden)
                    + "（P7.1-20：策略引擎只出决策，不得含网络/执行副作用）")

        message_template = str(value.get("message_template") or "")

        try:
            eff_range = EffectiveRange.parse(value.get("effective_range"), policy_id=pid)
        except PolicyValidationError as e:
            errors.extend(e.errors)
            eff_range = EffectiveRange()

        bgt = value.get("break_glass_ttl_min", None)
        if bgt is not None and bgt != "":
            try:
                bgt = int(bgt)
            except (TypeError, ValueError):
                errors.append(f"break_glass_ttl_min 应为整数分钟或 null，got {bgt!r}")
                bgt = None
            else:
                if bgt <= 0:
                    errors.append(f"break_glass_ttl_min 必须为正整数分钟，got {bgt}")
                    bgt = None

        signature = str(value.get("signature") or "")

        if errors:
            raise PolicyValidationError(errors, policy_id=pid)
        assert effect is not None  # errors 为空时必然已解析
        return cls(id=raw_id, version=raw_version, owner=owner, effect=effect,
                   match=dict(match), message_template=message_template,
                   effective_range=eff_range, break_glass_ttl_min=bgt,
                   signature=signature, source_ref=str(source_ref or ""))

    # ── 序列化 ──

    def to_dict(self, *, include_source_ref: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "version": self.version,
            "owner": self.owner,
            "effect": self.effect.value,
            "match": dict(self.match),
            "message_template": self.message_template,
            "effective_range": self.effective_range.to_dict() or None,
            "break_glass_ttl_min": self.break_glass_ttl_min,
            "signature": self.signature,
        }
        if include_source_ref and self.source_ref:
            out["source_ref"] = self.source_ref
        return out

    def signing_payload(self) -> str:
        """签名材料：除 ``signature`` 外的全部 schema 字段（canonical JSON）

        ``source_ref`` 是装载期派生字段，不参与签名——同一个策略文件被复制到
        不同路径不应导致验签失败。
        """
        body = self.to_dict()
        body.pop("signature", None)
        return canonical_json(body)

    # ── 便捷属性 ──

    @property
    def is_break_glassable(self) -> bool:
        return self.break_glass_ttl_min is not None

    def key(self) -> str:
        """版本定位键：``<id>@<version>``"""
        return f"{self.id}@{self.version}"

    def is_active(self, *, tenant_id: str = "", capability_id: str = "",
                  ts: Optional[Any] = None) -> bool:
        return self.effective_range.is_active(
            tenant_id=tenant_id, capability_id=capability_id, ts=ts)

    def match_signature(self) -> str:
        """``match`` 子树的稳定哈希（用于 shadow 诊断与模拟器变更定位）"""
        return sha256_hex(canonical_json(self.match))[:16]


# ════════════════════════════════════════════════════════════
#  PolicyContext（§5.6 的 ``policy_ctx``）
# ════════════════════════════════════════════════════════════

#: ``input`` 的规范骨架（只列常用叶子；``attributes`` 承载扩展，不设白名单限制，
#: 因为策略作者需要按业务维度判定，硬白名单会让策略写不出来）
_CONTEXT_SKELETON: Dict[str, Dict[str, Any]] = {
    "capability": {"id": "", "trust": {}, "origin": {}, "evolution": {}},
    "tenant": {"id": "default"},
    "actor": {"id": "", "role": ""},
    "action": {"name": "", "kind": ""},
    "target": {"external": False, "host": "", "scheme": "", "path": ""},
    "attributes": {},
}


def _is_opaque(provenance: Any) -> bool:
    """``origin.opaque`` 的云枢裁定（见 ``PolicyContext.from_descriptor``）

    ``provenance == "unknown"`` ⇔ 「我们只有行为级认识」＝ §2.2 的 ``opaque:true``。
    未分级/未提供 provenance 的资产视为 opaque（**安全取向**：不知道就当黑盒），
    但 §5.6 那条策略还需 ``risk_level == "destructive"`` 同时成立才会命中。
    """
    if provenance is None:
        return True
    text = str(getattr(provenance, "value", provenance)).strip().lower()
    return text in ("", "unknown")


@dataclass(frozen=True)
class PolicyContext:
    """决策输入（Rego 的 ``input`` 文档）

    引擎按**字段路径**寻址（``capability.trust.risk_level``），因此本对象只做
    两件事：给调用方一组具名构造器（把 §3.2 descriptor 的叶子搬进来），以及
    保证 ``input`` 是**纯 JSON 数据**（可入缓存键、可入决策日志重放）。

    **不含 live 对象**：descriptor / session / trace 都必须在构造期被裁剪成叶子
    字段——这是 DSH 数据纪律，也是「决策日志可重放」的前提。
    """

    input: Dict[str, Any] = field(default_factory=dict)

    # ── 构造 ──

    @classmethod
    def build(
        cls,
        *,
        capability_id: str = "",
        capability: Optional[Dict[str, Any]] = None,
        tenant_id: str = "default",
        tenant: Optional[Dict[str, Any]] = None,
        actor: str = "",
        actor_role: str = "",
        action: str = "",
        action_kind: str = "",
        target: Optional[Dict[str, Any]] = None,
        attributes: Optional[Dict[str, Any]] = None,
    ) -> "PolicyContext":
        payload: Dict[str, Any] = {
            "capability": {"id": str(capability_id or ""), **(capability or {})},
            "tenant": {"id": str(tenant_id or "default"), **(tenant or {})},
            "actor": {"id": str(actor or ""), "role": str(actor_role or "")},
            "action": {"name": str(action or ""), "kind": str(action_kind or "")},
            "target": {"external": False, "host": "", "scheme": "", "path": "",
                       **(target or {})},
            "attributes": dict(attributes or {}),
        }
        return cls(input=payload)

    @classmethod
    def from_input(cls, payload: Any) -> "PolicyContext":
        """从已构造的 ``input`` dict 还原（**模拟器重放路径**）

        非 dict → 空 input（等价于「什么都不匹配」）；不做深拷贝，调用方
        传入的应已是纯 JSON 数据（决策日志读回来的就是这个形态）。
        """
        if isinstance(payload, PolicyContext):
            return payload
        if not isinstance(payload, dict):
            return cls(input={})
        return cls(input=payload)

    @classmethod
    def from_descriptor(cls, descriptor: Any, **overrides: Any) -> "PolicyContext":
        """从 §3.2 ``ToolDescriptor`` 裁剪出决策输入

        只读四条被 §5.6/§2.5 判定用到的叶子：
        ``trust.data_class`` / ``trust.risk_level`` / ``trust.requires_approval``
        / ``origin.external_endpoint``（外加 ``origin.source_type`` / ``evolution.stage``
        / ``tenancy.tenant_id``）。**不用 descriptor 的 live 对象做别的事**。
        """
        def _leaf(obj: Any, *path: str, default: Any = None) -> Any:
            cur = obj
            for name in path:
                if cur is None:
                    return default
                cur = getattr(cur, name, None) if not isinstance(cur, dict) \
                    else cur.get(name)
            if cur is None:
                return default
            return getattr(cur, "value", cur)

        capability_id = str(overrides.pop("capability_id", "")
                            or _leaf(descriptor, "capability_id", default="")
                            or _leaf(descriptor, "meta", "id", default=""))
        trust = {
            "risk_level": _leaf(descriptor, "trust", "risk_level"),
            "data_class": _leaf(descriptor, "trust", "data_class"),
            "requires_approval": bool(_leaf(descriptor, "trust", "requires_approval",
                                            default=False)),
        }
        origin = {
            "source_type": _leaf(descriptor, "origin", "source_type"),
            "external_endpoint": bool(_leaf(descriptor, "origin", "external_endpoint",
                                            default=False)),
            "provenance": _leaf(descriptor, "origin", "provenance"),
            # §3.2 没有 opaque 字段，而 §5.6 的 Rego 例子以 ``c.origin.opaque`` 立论。
            # 云枢裁定：``opaque := (provenance == "unknown")``——§2.2 对「闭源只做行为级
            # 萃取，标 opaque:true，永不承诺 native」的表达，在 descriptor 契约里正是
            # provenance 停在 unknown。该判定使 §5.6 的策略可写、可测，且**不新增字段**。
            "opaque": _is_opaque(_leaf(descriptor, "origin", "provenance")),
        }
        evolution = {"stage": _leaf(descriptor, "evolution", "stage")}
        tenant_id = str(overrides.pop("tenant_id", "")
                        or _leaf(descriptor, "tenancy", "tenant_id", default="")
                        or "default")
        # ``origin.external_endpoint`` 与 ``target.external`` 是**同一个事实的两处表达**
        # （§2.5「secret 且目标外部 ⇒ 拒绝」的操作数）。descriptor 侧叫前者，策略侧
        # 统一用 ``target.external``，这里做一次显式搬运——否则「可外部触达的 secret
        # 能力」在判定时看不到 target，契约级不变量会静默失效
        # （这是本任务实现期实测到的真实缺陷，已加回归用例）。
        target = dict(overrides.pop("target", None) or {})
        target.setdefault("external", bool(origin["external_endpoint"]))
        return cls.build(
            capability_id=capability_id,
            capability={"trust": trust, "origin": origin, "evolution": evolution},
            tenant_id=tenant_id,
            target=target,
            **overrides,
        )

    # ── 访问 ──

    def get(self, path: str, default: Any = None) -> Any:
        """按点分路径取值；缺失返回 ``default``（不抛异常——策略判定要能容错）"""
        cur: Any = self.input
        for part in str(path or "").split("."):
            if not part:
                continue
            if isinstance(cur, dict):
                if part not in cur:
                    return default
                cur = cur[part]
            else:
                return default
        return default if cur is None else cur

    @property
    def capability_id(self) -> str:
        return str(self.get("capability.id", "") or "")

    @property
    def tenant_id(self) -> str:
        return str(self.get("tenant.id", "default") or "default")

    @property
    def actor(self) -> str:
        return str(self.get("actor.id", "") or "")

    @property
    def action(self) -> str:
        return str(self.get("action.name", "") or "")

    @property
    def target_external(self) -> bool:
        return bool(self.get("target.external", False))

    def cache_material(self) -> str:
        """缓存键材料（canonical JSON）；不可序列化返回 ``""`` ⇒ 跳过缓存"""
        return canonical_json(self.input)

    def template_values(self) -> Dict[str, Any]:
        """``message_template`` 可用的占位符取值（扁平键 → 叶子）"""
        values: Dict[str, Any] = {}
        for head, node in (self.input or {}).items():
            if isinstance(node, dict):
                for key, leaf in node.items():
                    values[f"{head}_{key}"] = leaf
                    values[key] = leaf
            else:
                values[head] = node
        return values


# ════════════════════════════════════════════════════════════
#  PolicyDecision（§5.6 返回值 + §6.6 埋点字段）
# ════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PolicyDecision:
    """一次决策的**完整结果**——引擎的终点

    §5.6 只要求 ``{allow|deny|ask, policy_id}``；本对象在其上补齐「可审计、
    可重放、可诊断」所需的叶子字段，但**不含任何执行动作**：没有 URL、没有
    执行句柄、没有回调。执行由调用点（egress guard / 权限网关）另行完成，
    这是 P7.1-20「决策与执行分离」在类型层面的体现。

    Attributes:
        effect: allow / deny / ask。
        policy_id: 命中的策略 id；未命中为空串。
        matched: 是否有策略命中。**False ⇒ 执行点回落既有判定**。
        policy_version: 命中策略的版本。
        reason_code: 机器可读原因码（no_policy_match / policy_deny / policy_ask /
            policy_allow / break_glass / invalid_context / engine_error）。
        message: 由 ``message_template`` 渲染的用户文案（可直接上 UI）。
        break_glass: 是否由 break-glass 例外放行。
        cache_hit: 是否命中决策缓存。
        latency_ms: 本次决策耗时（毫秒，``perf_counter`` 口径）。
        tenant_id / capability_id / action / actor: 审计与模拟器定位叶子。
        cache_key: 缓存键哈希前缀（诊断用；不含原始输入）。
    """

    effect: str
    policy_id: str = ""
    matched: bool = False
    policy_version: str = ""
    reason_code: str = ""
    message: str = ""
    break_glass: bool = False
    break_glass_grant: str = ""
    cache_hit: bool = False
    latency_ms: float = 0.0
    tenant_id: str = "default"
    capability_id: str = ""
    action: str = ""
    actor: str = ""
    cache_key: str = ""

    # ── 便捷谓词（执行点用；语义写在名字里，避免调用方各自解释 effect 字符串） ──

    @property
    def allowed(self) -> bool:
        """执行点是否可放行（``ask`` **不算**放行，需人工介入）"""
        return self.effect == EFFECT_ALLOW

    @property
    def denied(self) -> bool:
        return self.effect == EFFECT_DENY

    @property
    def needs_human(self) -> bool:
        """是否需要人工介入（ask 或 break-glass 授予的例外）"""
        return self.effect == EFFECT_ASK or self.break_glass

    def to_dict(self) -> Dict[str, Any]:
        return {
            "effect": self.effect,
            "policy_id": self.policy_id,
            "matched": self.matched,
            "policy_version": self.policy_version,
            "reason_code": self.reason_code,
            "message": self.message,
            "break_glass": self.break_glass,
            "cache_hit": self.cache_hit,
            "latency_ms": round(float(self.latency_ms), 4),
            "tenant_id": self.tenant_id,
            "capability_id": self.capability_id,
            "action": self.action,
            "actor": self.actor,
        }

    def audit_leaves(self) -> Dict[str, Any]:
        """入审计/事件的**叶子投影**（§6.6 ``policy.decision`` 埋点字段）

        §6.6 规定：``policy.decision {policy_version, actor, scope, result, latency_ms}``。
        本方法严格只出这五项（``scope`` 取 capability_id，空时退化为 action），
        外加 ``cache_hit`` 与 ``reason_code`` 两个诊断位——**绝不带 match 的
        匹配值**（沿用既有脱敏口径：日志/审计里不写敏感匹配值）。
        """
        return {
            "policy_version": self.policy_version,
            "actor": self.actor,
            "scope": self.capability_id or self.action,
            "result": self.effect,
            "latency_ms": round(float(self.latency_ms), 3),
            "cache_hit": bool(self.cache_hit),
            "reason_code": self.reason_code or "",
            "policy_id": self.policy_id or "",
        }


# ════════════════════════════════════════════════════════════
#  导出
# ════════════════════════════════════════════════════════════

__all__ = [
    "POLICY_SCHEMA",
    "EFFECT_ALLOW", "EFFECT_DENY", "EFFECT_ASK", "EFFECTS",
    "SIGN_SCHEME_ED25519", "SIGN_SCHEME_SHA256_SELF",
    "FORBIDDEN_MATCH_TOKENS",
    "PolicyError", "PolicyValidationError",
    "Effect", "EffectiveRange", "Policy", "PolicyContext", "PolicyDecision",
    "canonical_json", "sha256_hex", "now_iso",
    "render_message", "template_fields",
]
