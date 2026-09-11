"""agent.security —— 审批 Actor 矩阵与审批面安全（v7.2 §7.0 / §5.7⑦）

TASK-S4-01 交付包。四个关注点，各自一个模块，互不越界：

| 模块 | 关注点 | 对应验收 |
|---|---|---|
| `actor_matrix` | ActorType + **权限判定表**（表驱动，后端唯一权威） | §7.0 矩阵核心行全部可判定 |
| `approval_guard` | 审批入口 actor 校验 + 越权**拒绝/审计/告警** | auto/sub_agent 越权用例 |
| `identity` | **令牌 → 用户映射**（裁定 A3）+ `identity_source` 统一口径 | S2-02 #1 / S2-03 #13 |
| `pii` | IP **掩码 + HMAC** 双字段（裁定 B），原始 IP 不落盘 | S2-02 #11 |
| `approval_session` | 会话绑定 / 时效 ≤15min / CSRF / destructive 二次认证 | §5.7⑦ |
| `alerts` | 越权告警通道（日志 + 计数 + 可注入外部通道） | §5.7⑦ 告警 |

【依赖方向（零循环）】
    `actor_matrix`（纯表，无依赖）
        ↑
    `pii` / `identity`（stdlib + `agent.utils`）
        ↑
    `alerts` / `approval_session`
        ↑
    `approval_guard`（惰性触碰 `agent.observability.events` / `agent.audit`）

消费方：`agent/skills_mgmt/approval.py`（审批状态机）、
`agent/server_routes/routes_approval.py`（HTTP 审批面）、
`agent/audit/ui_middleware.py`（UI 写路由身份与 PII）——
三者都**只经本包**判定，前端不拥有额外权限。
"""

from agent.security.actor_matrix import (
    ACTOR_AUTO,
    ACTOR_HUMAN,
    ACTOR_SUB_AGENT,
    ACTOR_TYPES,
    APPROVAL_OPERATIONS,
    GOVERNANCE_OPERATIONS,
    MATRIX_DOC,
    MATRIX_DOC_ROWS,
    OPERATIONS,
    OP_APPROVE,
    OP_DENY,
    OP_EXECUTE_CAPABILITY,
    OP_FORCE_STAGE,
    OP_MODIFY_POLICY,
    OP_REMOVE_SOURCE,
    OP_SUBMIT_APPROVAL,
    OP_SWITCH_FORGE,
    OP_VIEW_MEMORY,
    OP_VIEW_PANEL,
    OP_VIEW_TRACE,
    OP_WRITE_MEMORY,
    PermissionContext,
    PermissionDecision,
    PermissionRule,
    decide,
    infer_actor_type,
    is_destructive,
    matrix_rows,
    normalize_actor_type,
    normalize_operation,
    register_rule,
    reset_rules,
    rule_for,
)
from agent.security.alerts import (
    ALERT_MARKER,
    get_denial_stats,
    recent_denials,
    reset_alerts,
    set_alert_sink,
)
from agent.security.approval_guard import (
    ActorContext,
    authorize,
    authorize_approval_action,
    context_from_identity,
    context_from_request,
    operation_for,
    register_object_operation,
    report_denial,
    resolve_risk,
    set_risk_resolver,
)
from agent.security.approval_session import (
    CHECK_ALREADY_USED,
    CHECK_CSRF_MISMATCH,
    CHECK_EXPIRED,
    CHECK_OK,
    CHECK_SECOND_FACTOR_INVALID,
    CHECK_SESSION_MISMATCH,
    CSRF_HEADER_NAME,
    MAX_LINK_TTL_SECONDS,
    SESSION_COOKIE_NAME,
    ApprovalLink,
    ApprovalSession,
    ApprovalSessionStore,
    LinkCheck,
    get_session_store,
    link_ttl_seconds,
    reset_approval_sessions,
    set_session_store,
)
from agent.security.identity import (
    AUTHORITY_AUTHORITATIVE,
    AUTHORITY_DEGRADED,
    SRC_EXPLICIT,
    SRC_NO_IDENTITY,
    SRC_REMOTE_ADDR,
    SRC_SESSION,
    SRC_TOKEN_MAP,
    IdentityResolver,
    ResolvedIdentity,
    TokenMap,
    resolve_identity,
    reset_identity,
    set_resolver,
    set_token_map,
    token_fingerprint,
)
from agent.security.pii import (
    FIELD_IP_HASH,
    FIELD_IP_HASH_STATUS,
    FIELD_IP_MASKED,
    STATUS_DEGRADED,
    STATUS_HMAC,
    STATUS_NO_IP,
    hash_ip,
    ip_pii_fields,
    mask_ip,
    reset_pii,
    same_source,
    set_hmac_key,
)

__all__ = [
    # actor_matrix
    "ACTOR_HUMAN", "ACTOR_AUTO", "ACTOR_SUB_AGENT", "ACTOR_TYPES",
    "OP_VIEW_TRACE", "OP_VIEW_MEMORY", "OP_VIEW_PANEL", "OP_APPROVE", "OP_DENY",
    "OP_SWITCH_FORGE", "OP_MODIFY_POLICY", "OP_FORCE_STAGE", "OP_REMOVE_SOURCE",
    "OP_EXECUTE_CAPABILITY", "OP_WRITE_MEMORY", "OP_SUBMIT_APPROVAL",
    "OPERATIONS", "GOVERNANCE_OPERATIONS", "APPROVAL_OPERATIONS",
    "MATRIX_DOC", "MATRIX_DOC_ROWS",
    "PermissionRule", "PermissionContext", "PermissionDecision",
    "decide", "rule_for", "register_rule", "reset_rules", "matrix_rows",
    "normalize_actor_type", "normalize_operation", "infer_actor_type",
    "is_destructive",
    # guard
    "ActorContext", "authorize", "authorize_approval_action",
    "context_from_identity", "context_from_request", "operation_for",
    "register_object_operation", "report_denial", "resolve_risk",
    "set_risk_resolver",
    # identity
    "SRC_TOKEN_MAP", "SRC_EXPLICIT", "SRC_SESSION", "SRC_REMOTE_ADDR",
    "SRC_NO_IDENTITY", "AUTHORITY_AUTHORITATIVE", "AUTHORITY_DEGRADED",
    "ResolvedIdentity", "TokenMap", "IdentityResolver", "resolve_identity",
    "reset_identity", "set_resolver", "set_token_map", "token_fingerprint",
    # pii
    "FIELD_IP_MASKED", "FIELD_IP_HASH", "FIELD_IP_HASH_STATUS",
    "STATUS_HMAC", "STATUS_DEGRADED", "STATUS_NO_IP",
    "mask_ip", "hash_ip", "ip_pii_fields", "same_source",
    "reset_pii", "set_hmac_key",
    # alerts
    "ALERT_MARKER", "get_denial_stats", "recent_denials", "reset_alerts",
    "set_alert_sink",
    # approval_session
    "CHECK_OK", "CHECK_EXPIRED", "CHECK_SESSION_MISMATCH", "CHECK_ALREADY_USED",
    "CHECK_CSRF_MISMATCH", "CHECK_SECOND_FACTOR_INVALID",
    "SESSION_COOKIE_NAME", "CSRF_HEADER_NAME", "MAX_LINK_TTL_SECONDS",
    "ApprovalSession", "ApprovalLink", "LinkCheck", "ApprovalSessionStore",
    "get_session_store", "set_session_store", "reset_approval_sessions",
    "link_ttl_seconds",
]
