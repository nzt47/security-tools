"""agent.policy — 策略即代码（v7.2 §5.6 / §3.11 / P7.1-20 / P7.2-19）

【一句话】
    把云枢的权限判定从「散落在各调用点的 if」收敛成**可版本化、可模拟、可审计、
    可签名**的策略库；引擎**只出决策不做动作**，执行由既有执行点落实。

【模块地图（按依赖方向，上层可依赖下层，反向不允许）】

    models.py       §3.11 Policy schema + PolicyContext + PolicyDecision（纯数据）
    matcher.py      OPA 子集等效判定器（支持/不支持语法见 MATCH_SUBSET.md）
    signing.py      ed25519 签名 + sha256-self 降级占位（沿用 S2-02 口径）
    store.py        版本化策略库 + effective_range + 验签 + 内置不变量
    decisions.py    决策日志（脱敏；模拟器的重放数据源）
    taint.py        出域链路监测的「读密钥」半边（§5.7 机制 4）
    egress.py       出域**决策输入**组装与判定（P7.1-20 决策侧）
    engine.py       决策编排：缓存 / 埋点 / 审计 / break-glass / 例外路由
    inbox.py        例外收件箱（复用 hitl/takeover_queue；§5.6「只收例外」）
    simulator.py    P7.2-19 策略模拟器 + 报告渲染 + CLI

【执行点（不在本包内，按 P7.1-20 分离）】

    agent/guardrails/egress_guard.py  数据出域执行点（被 agent/web/http_client.py 调用）
    agent/permission_system.py        PermissionGateway 的策略层（deny/ask 收敛）

【三条容易做错的纪律（读代码前先读这三条）】

    1. **策略层只收敛，不放宽**。``allow`` 只表示「策略层没意见」；执行点仍须通过
       自己的既有判定。因此引入策略引擎不可能让原本被拒的操作变成允许。
       ``matched=False``（策略未覆盖）时必须回落既有判定。
    2. **引擎无网络/执行能力**。本包不 import 网络与子进程库；``match`` 里出现
       ``http.send`` 一类 token 在装载期即被拒。这是 P7.1-20 的实现方式。
    3. **一切落盘都可显式指定路径**。``data/policies/`` 下的决策日志与收件箱账是
       运行时产物；**用例必须显式传路径**（S3-02/S3-03 两次踩坑的教训）。

【快速上手】

    >>> from agent.policy import PolicyEngine, PolicyContext, PolicyStore
    >>> store = PolicyStore(path="data/policies/policies.json")
    >>> engine = PolicyEngine(store)
    >>> ctx = PolicyContext.build(capability_id="cp.filesystem.local.read",
    ...     capability={"trust": {"data_class": "secret", "risk_level": "low"}},
    ...     target={"external": True})
    >>> decision = engine.check(ctx)
    >>> decision.effect, decision.policy_id
    ('deny', 'builtin.invariant.secret-egress-deny')
"""

from __future__ import annotations

from agent.policy.decisions import (
    DEFAULT_DECISION_LOG,
    DecisionLog,
    DecisionRecord,
)
from agent.policy.egress import (
    EgressDecision,
    EgressRequest,
    build_egress_context,
    classify_target,
    decide_egress,
)
from agent.policy.engine import (
    ENV_OBSERVE,
    ENV_OBSERVE_SCOPE,
    REASON_BREAK_GLASS,
    REASON_NO_MATCH,
    REASON_POLICY_ALLOW,
    REASON_POLICY_DENY,
    REASON_POLICY_ASK,
    BreakGlassError,
    BreakGlassGrant,
    DecisionObserver,
    PolicyEngine,
    get_policy_engine,
    reset_policy_engine,
)
from agent.policy.inbox import InboxItem, PolicyInbox, get_policy_inbox, reset_policy_inbox
from agent.policy.matcher import (
    MISSING,
    OPS,
    MatchEvaluator,
    MatchResult,
    match,
    support_matrix,
    validate_match,
)
from agent.policy.models import (
    EFFECT_ALLOW,
    EFFECT_ASK,
    EFFECT_DENY,
    EFFECTS,
    FORBIDDEN_MATCH_TOKENS,
    POLICY_SCHEMA,
    Effect,
    EffectiveRange,
    Policy,
    PolicyContext,
    PolicyDecision,
    PolicyError,
    PolicyValidationError,
    render_message,
    template_fields,
)
from agent.policy.signing import (
    PolicySigner,
    SignatureResult,
    require_signature,
    verify_policy_signature,
)
from agent.policy.simulator import (
    HIGH_RISK_CHANGES,
    SimulationChange,
    SimulationReport,
    parse_since,
    render_markdown,
    simulate,
)
from agent.policy.store import (
    BUILTIN_ID_PREFIX,
    DEFAULT_POLICY_FILE,
    PolicyStore,
    PolicyStoreError,
    StoreProblem,
    builtin_policies,
    builtin_policy_dicts,
    get_policy_store,
    reset_policy_store,
    version_key,
)
from agent.policy.taint import (
    PROCESS_SCOPE,
    SecretTaintLedger,
    TaintMark,
    get_secret_taint,
    is_secret_path,
    mark_secret_read,
    reset_secret_taint,
    scan_payload,
    scan_secret_material,
    taint_state,
)

#: 本包版本（与 §3.11 schema 版本解耦：这里是**实现**版本）
__version__ = "1.0.0"

__all__ = [
    "__version__",
    # models
    "POLICY_SCHEMA", "EFFECT_ALLOW", "EFFECT_DENY", "EFFECT_ASK", "EFFECTS",
    "FORBIDDEN_MATCH_TOKENS", "PolicyError", "PolicyValidationError",
    "Effect", "EffectiveRange", "Policy", "PolicyContext", "PolicyDecision",
    "render_message", "template_fields",
    # matcher
    "MISSING", "OPS", "MatchEvaluator", "MatchResult", "match",
    "support_matrix", "validate_match",
    # signing
    "PolicySigner", "SignatureResult", "verify_policy_signature",
    "require_signature",
    # store
    "BUILTIN_ID_PREFIX", "DEFAULT_POLICY_FILE", "PolicyStore", "PolicyStoreError",
    "StoreProblem", "builtin_policies", "builtin_policy_dicts",
    "get_policy_store", "reset_policy_store", "version_key",
    # decisions
    "DEFAULT_DECISION_LOG", "DecisionLog", "DecisionRecord",
    # engine
    "ENV_OBSERVE", "ENV_OBSERVE_SCOPE",
    "REASON_NO_MATCH", "REASON_POLICY_ALLOW", "REASON_POLICY_DENY",
    "REASON_POLICY_ASK", "REASON_BREAK_GLASS",
    "BreakGlassError", "BreakGlassGrant", "DecisionObserver", "PolicyEngine",
    "get_policy_engine", "reset_policy_engine",
    # taint
    "PROCESS_SCOPE", "SecretTaintLedger", "TaintMark", "get_secret_taint",
    "is_secret_path", "mark_secret_read", "reset_secret_taint", "scan_payload",
    "scan_secret_material", "taint_state",
    # egress
    "EgressDecision", "EgressRequest", "build_egress_context", "classify_target",
    "decide_egress",
    # inbox
    "InboxItem", "PolicyInbox", "get_policy_inbox", "reset_policy_inbox",
    # simulator
    "HIGH_RISK_CHANGES", "SimulationChange", "SimulationReport", "parse_since",
    "render_markdown", "simulate",
]
