"""统一审批流（ApprovalFlow）— 任务 EVO-T6 安全护栏

【任务定位】
    为全部进化机制收口统一人机协同审批流，落地设计文档
    "可验证性 + 谱系追踪 + 人类监督 + 价值观对齐"四重护栏中的人类监督层。
    解决审计缺陷 2.3-5：技能进化自动提交（无人复核）与知识进化人工确认
    两种人机边界不一致。

【审批分级（不易）】
    L0 无需审批: 无行为影响的记录类操作（谱系写入、评估记录）→ 自动放行；
    L1 需审批:   影响生产行为的变更（技能参数提交、提示词建议应用、
                 工具编辑提案合并）→ pending_review，审批通过后系统自动合并；
    L2 需人工执行: 元智能体代码编辑、策略变更等高风险操作
                 （自动只产出建议，审批通过后由人工执行并标记归档）。

【状态机】
    draft → pending_review → approved / rejected → merged / archived
    合法迁移在 _TRANSITIONS 定义，非法迁移抛 ApprovalStateError。

【不易边界】
    - 审批流只新增，不删除现有确认逻辑（不感知知识模块内部）；
    - 未 merged 的 L1/L2 变更 is_effective()=False，绝不生效（验收 2）；
    - 用户显式关闭审批（APPROVAL_ENABLED=0）时构造期输出醒目告警日志。

【配置（.env，全部带默认值）】
    APPROVAL_ENABLED              审批总开关，默认 1（开启）
    APPROVAL_RECORDS_PATH         审批记录 JSONL 路径，默认 agent/data/approval_records.jsonl

【TASK-S4-01 Actor 矩阵接线（v7.2 §7.0 / §5.7⑦）】
    审批入口加 **执行体校验**（`agent/security/actor_matrix.py` 为唯一权威表）：
      - `approve` / `reject` / `merge` / `mark_manual_executed` → §7.0「审批
        Approve/Deny」行 ⇒ **human 专属**；auto(skill) / sub_agent 调用一律
        `ApprovalPermissionError`，并触发 **越权告警 + 链式审计**；
      - `submit` → 只判矩阵格（提交提案 ≠ 审批生效）：auto 可提交（"自动只产出
        建议"），sub_agent 不进审批面；`stage.promote` 一类强制推进对象在提交
        阶段即要求 human（§7.0「强制推进 stage」行）；
      - 二次认证 / reason 必填一类**前置条件**作用于生效动作，由路由
        （`agent/server_routes/routes_approval.py`）经 `second_factor_ok=` 传入。

    调用方以 `actor_ctx=` 传入**身份层解析出的**执行体上下文（`ActorContext`）；
    不传时按 `actor` 名推断（`reviewer` / 用户名 → human），从而**既有调用点
    行为零变化**。前端提交的 actor / actor_type **一律不可信**，路由必须经
    `agent/security/identity.py` 解析后才构造上下文。

    审批记录新增身份与 PII 叶子字段（`actor_type` / `identity_source` /
    `actor_ip_masked` / `actor_ip_hash`…）：**原始 IP 不落盘**（裁定 B）。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .observability import logger
from agent.security.approval_guard import (
    ActorContext,
    authorize_approval_action,
    resolve_risk,
)
from agent.security.governance_bridge import governance_trace_fields

# 审批级别与状态枚举
APPROVAL_LEVELS = ("L0", "L1", "L2")
APPROVAL_STATES = ("draft", "pending_review", "approved", "rejected", "merged", "archived")

#: 超时判定的 ``decision_reason`` 前缀（**结构标识**，不是文案）
#:
#: 为什么需要它：``expire_pending()`` 把"没人理的待办"判为 ``rejected``（复用既有状态机与
#: §6.1「超时 Deny=2」计数口径），但**它与"人工点了驳回"是两回事**：
#:   - 人工驳回 ⇒ 该请求被人否决，调用方**不应再重试**；
#:   - 系统超时 ⇒ 只是没人处理，调用方**应当重新发起**（重新挂一张新待办）。
#: 工具审批层（``agent/tool_approval.py::is_rejected``）据此把两者分开，否则模型会被告知
#: "人工已否决、别再重试"，而实际从没有人看过那张单。改动本前缀 = 改动那个判据，
#: 故它与使用方由测试一起钉住（``tests/unit/test_tool_approval.py``）。
TIMEOUT_DENY_REASON_PREFIX = "超时未审批（timeout deny）"


def is_timeout_deny(record: Any) -> bool:
    """该记录是否是**系统超时判定**（而非人工裁决）

    判据 = 状态 ``rejected`` 且 ``decision_reason`` 以上述前缀开头。
    只用于区分语义，不改变状态机：两者都停在 ``rejected``（§6.1 计数口径要求）。
    """
    try:
        if str(getattr(record, "state", "") or "") != "rejected":
            return False
        return str(getattr(record, "decision_reason", "") or "").startswith(
            TIMEOUT_DENY_REASON_PREFIX)
    except Exception:  # noqa: BLE001 判定失败按"不是超时"处理（从严：不解除人工否决）
        return False


# 状态机合法迁移表（验收 1: 全迁移路径测试依据）
_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "draft": ("pending_review",),
    "pending_review": ("approved", "rejected"),
    "approved": ("merged", "archived"),   # approved → merged=系统自动合并；→ archived=L2 人工执行后标记
    "rejected": ("archived",),
    "merged": (),
    "archived": (),
}

# 默认路径（与 skills_mgmt 其他数据文件对齐：agent/data/）
_DEFAULT_RECORDS_PATH = Path(__file__).parent.parent.parent / "data" / "approval_records.jsonl"

_ENV_ENABLED = "APPROVAL_ENABLED"
_ENV_RECORDS_PATH = "APPROVAL_RECORDS_PATH"

# record_id 的**进程内单调**时间段（见 ApprovalRecord._generate_id 的说明）：
#   _LAST_ID_TS 是上一次发出的 20 位 "YYYYmmddHHMMSSffffff"；_ID_TS_LOCK 保证并发生成
#   （Flask 多线程 / 后台线程）下"读-改-写"不撕裂。
_ID_TS_LOCK = threading.Lock()
_LAST_ID_TS = ""


def _bump_id_ts(ts: str) -> str:
    """把 20 位 "YYYYmmddHHMMSSffffff" 时间段加 1 微秒（解析失败原样返回，绝不抛异常）

    只服务 ApprovalRecord._generate_id 的单调性兜底：同一时钟刻度内连发两个 id 时，
    后一个被抬到"前一个 + 1µs"，使定宽时间段的**字典序 = 生成先后序**。
    """
    try:
        bumped = datetime.strptime(ts, "%Y%m%d%H%M%S%f") + timedelta(microseconds=1)
        return bumped.strftime("%Y%m%d%H%M%S%f")
    except Exception:  # noqa: BLE001 解析失败 ⇒ 原样返回（退化成改动前行为）
        return ts


def _env_enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "1").strip().lower() not in ("0", "false", "no", "off")


def _env_records_path() -> Path:
    return Path(os.getenv(_ENV_RECORDS_PATH, str(_DEFAULT_RECORDS_PATH)))


class ApprovalError(Exception):
    """审批流异常基类"""


class ApprovalStateError(ApprovalError):
    """非法状态迁移 / 不满足状态前置条件"""


class ApprovalLevelError(ApprovalError):
    """非法审批级别"""


class ApprovalPermissionError(ApprovalError):
    """执行体越权（§7.0 Actor 矩阵拒绝）

    由 `agent/security/actor_matrix.py` 判定并附带 `PermissionDecision`：
    调用方（路由）据 `decision.reason` 给出可读提示，`decision.denied_by_matrix`
    区分「矩阵拒绝」与「缺少 reason / 未完成二次认证」两类前置条件不满足。

    越权尝试在抛出前已由 `agent/security/approval_guard.py::report_denial`
    完成 **告警 + 链式审计（policy.denied 镜像）**，故本异常只承载结果。
    """

    def __init__(self, message: str, decision: Any = None) -> None:
        super().__init__(message)
        self.decision = decision


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class ApprovalRecord:
    """一条审批记录（JSONL 持久化）

    payload: 变更内容快照（params/建议文本/提案摘要），仅作审计留痕，
             真正的生效动作由 applier 回调执行（L1）或人工执行（L2）。
    """
    record_id: str = ""
    object_type: str = "skill"          # skill / prompt / knowledge_card / subagent_config / tool_code
    object_id: str = ""
    level: str = "L1"                   # L0 / L1 / L2
    action: str = ""                    # 动作名（如 params_submit / prompt_apply / edit_proposal_merge）
    description: str = ""               # 变更说明
    payload: Optional[Dict[str, Any]] = None   # 审计留痕快照
    eval_result: Optional[Dict[str, Any]] = None
    state: str = "draft"
    actor: str = "system"               # 提交者 / 审批者
    trigger: str = "api"                # manual / scheduler / feedback / api
    decision_reason: str = ""           # reject 原因 / approve 备注
    manual_required: bool = False       # L2: 需人工执行
    created_at: str = ""
    updated_at: str = ""
    merged_at: str = ""
    # ── TASK-S4-01：身份与 PII 口径（全部有默认值 ⇒ 旧 JSONL 可无损读回） ──
    actor_type: str = ""                # human / auto / sub_agent（空=按 actor 名推断）
    identity_source: str = ""           # 身份来源口径（S2-02/S2-03 统一）
    actor_scope: str = ""               # 执行体 scope
    session_id: str = ""                # 审批会话（§5.7⑦ 会话绑定）
    actor_ip_masked: str = ""           # 裁定 B：掩码（如 10.0.xxx.xxx）
    actor_ip_hash: str = ""             # 裁定 B：HMAC-SHA256（无密钥则空）
    actor_ip_hash_status: str = ""      # hmac_sha256 / degraded_no_key / no_ip

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = self._generate_id()
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法审批级别: {self.level}（允许: {APPROVAL_LEVELS}）")
        if self.state not in APPROVAL_STATES:
            raise ApprovalError(f"非法审批状态: {self.state}（允许: {APPROVAL_STATES}）")

    @staticmethod
    def _generate_id() -> str:
        """生成 ``appr-<20位时间段>-<8位随机hex>``（时间段**进程内严格单调**）

        【为什么时间段必须单调（2026-09-22 修）】
        ``agent/tool_approval.py::_decision_key`` 在"审批记录时间戳只到秒"的同一秒内，
        用 ``record_id`` 的**字典序**兜底裁决先后。那条兜底成立的前提是
        "``strftime('%f')`` 的微秒段确实能区分先后"——**在 Windows 上不成立**：
        ``datetime.now()`` 的时钟粒度约 15.6ms，同一刻度内的两次调用会取到**逐字相同**
        的 20 位时间段，于是两张单的先后交给随机 hex 段决定 ⇒ 可能"老裁决压过新裁决"
        （实测：同一测试文件整文件连跑约 8% 概率红，两次失败样本的 20 位前缀完全相同；
        证据与立项见 docs/closeout/遗留问题立项_20260922.md 的 L3 条目）。
        故本函数在**同一进程内**保证后发的 id 时间段不小于先发的（撞上同一刻度就 +1µs）。
        **格式一字未变**（仍是定宽 20 位 + 8 位 hex）⇒ 既有解析/掩码/日志链路不受影响；
        时间段最多比真实时钟**超前几微秒**（它只用于排序，真实时刻另有 created_at）。

        【仍不覆盖】**跨进程**同一刻度：两个进程在同一刻度各自生成时时间段可能相同，
        先后仍由随机 hex 段决定。要为此引入全局序号必须改 id 格式（会让既有按 id 解析的
        链路失效），而"人对同一事项的两次相反裁决落进同一 15.6ms 刻度"本就不现实 ⇒
        本层不承担该语义，如实标注而不宣称彻底解决。
        """
        global _LAST_ID_TS
        with _ID_TS_LOCK:
            now = datetime.now().strftime("%Y%m%d%H%M%S%f")
            if now <= _LAST_ID_TS:          # 同一刻度（或时钟回拨）⇒ 抬到上一个 + 1µs
                now = _bump_id_ts(_LAST_ID_TS)
            _LAST_ID_TS = now
        return f"appr-{now}-{uuid.uuid4().hex[:8]}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRecord":
        allowed = {f.name for f in fields(cls)}
        d = {k: v for k, v in data.items() if k in allowed}
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def effective(self) -> bool:
        """是否已生效（merged 才生效，守不易）"""
        return self.state == "merged"

    # ── TASK-S4-01：身份与 PII 叶子字段（审计载荷用，只含叶子） ──

    def identity_fields(self) -> Dict[str, Any]:
        """身份口径叶子字段（与 S2-02/S2-03 埋点完全同源同值）"""
        fields: Dict[str, Any] = {}
        if self.actor_type:
            fields["actor_type"] = self.actor_type
        if self.identity_source:
            fields["identity_source"] = self.identity_source
        if self.session_id:
            fields["session_id"] = self.session_id
        return fields

    def pii_fields(self) -> Dict[str, Any]:
        """IP PII 叶子字段（裁定 B；**原始 IP 不在其中**）"""
        fields: Dict[str, Any] = {}
        if self.actor_ip_masked:
            fields["actor_ip_masked"] = self.actor_ip_masked
        if self.actor_ip_hash:
            fields["actor_ip_hash"] = self.actor_ip_hash
        if self.actor_ip_hash_status:
            fields["actor_ip_hash_status"] = self.actor_ip_hash_status
        return fields

    def apply_actor_ctx(self, actor_ctx: Optional["ActorContext"]) -> None:
        """把执行体上下文落到记录（身份 + PII；无上下文则保持原值）"""
        if actor_ctx is None:
            return
        self.actor_type = actor_ctx.resolved_type()
        if actor_ctx.identity_source:
            self.identity_source = actor_ctx.identity_source
        if actor_ctx.scope:
            self.actor_scope = actor_ctx.scope
        if actor_ctx.session_id:
            self.session_id = actor_ctx.session_id
        if actor_ctx.actor_ip:
            ip_fields = actor_ctx.ip_fields()
            self.actor_ip_masked = str(ip_fields.get("actor_ip_masked", "") or "")
            self.actor_ip_hash = str(ip_fields.get("actor_ip_hash", "") or "")
            self.actor_ip_hash_status = str(
                ip_fields.get("actor_ip_hash_status", "") or "")


# ════════════════════════════════════════════════════════════
#  底层 IO 工具（原子写入 / 损坏容错，与 lineage 同策略）
# ════════════════════════════════════════════════════════════

def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as tmp:
        for line in lines:
            tmp.write(line)
            tmp.write("\n")
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == 2:
                logger.error("[Approval] 审批记录写入失败 path=%s（重试 3 次后放弃）", path)
                raise
            time.sleep(0.05)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL；文件不存在 → 空；单行损坏 → 跳过并告警"""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    logger.warning("[Approval] %s 跳过损坏行", path)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[Approval] %s 读取失败（按空处理）: %s", path, e)
    return records


# ════════════════════════════════════════════════════════════
#  审批流
# ════════════════════════════════════════════════════════════

def default_level_map() -> Dict[Tuple[str, str], str]:
    """默认审批分级（变易：可被构造参数覆盖）

    约定（不易）:
        - 动作以 record/log/eval/lineage 开头 → 记录类 → L0 自动放行；
        - skill/prompt 等对象的生产行为变更 → L1；
        - subagent_config / meta_agent / strategy / 代码编辑 → L2（需人工执行）。
    """
    return {}


class ApprovalFlow:
    """统一审批流 — 分级判定 + 状态机 + JSONL 持久化（线程安全）

    用法:
        flow = ApprovalFlow()
        rec = flow.submit("skill", "my-skill", action="params_submit",
                          payload=..., applier=applier_fn)      # L1 → pending_review
        flow.approve(rec.record_id, actor="reviewer")
        flow.merge(rec.record_id, actor="reviewer")             # 执行 applier → merged
        assert flow.is_effective(rec.record_id)

    applier 说明（简易）: 真正执行变更的闭包，仅进程内注册（JSONL 不落盘函数）。
    服务重启后 pending/approved 记录无 applier，merge() 返回 ApprovalStateError，
    由人工在 UI/CLI 侧执行（与 L2 人工执行语义一致）。
    """

    def __init__(self, records_path: Optional[str] = None,
                 enabled: Optional[bool] = None,
                 level_map: Optional[Dict[Tuple[str, str], str]] = None,
                 default_level: str = "L1"):
        """Args:
            records_path: 审批记录 JSONL 路径（None=读 .env/默认）
            enabled: 审批总开关（None=读 .env；关闭时仅告警，提交直接放行）
            level_map: 分级覆盖 { (object_type, action): level }，None=默认分级
            default_level: 未命中分级规则时的兜底级别（安全第一 → 默认 L1）
        """
        self._records_path = Path(records_path) if records_path else _env_records_path()
        self._enabled = enabled if enabled is not None else _env_enabled()
        self._level_map = dict(level_map or default_level_map())
        if default_level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法 default_level: {default_level}")
        self._default_level = default_level
        self._lock = threading.RLock()
        self._records: List[ApprovalRecord] = []
        self._index: Dict[str, ApprovalRecord] = {}
        self._appliers: Dict[str, Callable[[], Any]] = {}
        self._loaded = False
        if not self._enabled:
            # 【不易】用户显式关闭审批的开关必须醒目记录并告警
            logger.warning(
                "[Approval] ⚠ 审批总开关 APPROVAL_ENABLED=0 已关闭，"
                "L1/L2 变更将直接放行（无人类监督）——请确认这是有意为之")

    # ─── 公共查询 ───

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ─── 分级判定 ───

    def route_level(self, object_type: str, action: str = "") -> str:
        """按 (object_type, action) 判定审批级别（验收 1）

        优先级: 显式 level_map > 记录类动作约定 > 默认级别。
        """
        key = (object_type, action)
        if key in self._level_map:
            level = self._level_map[key]
        elif action and any(action.startswith(p) for p in
                            ("record", "log", "eval", "lineage")):
            # 记录类操作无行为影响 → 自动放行（L0）
            level = "L0"
        else:
            level = self._default_level
        if level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"分级映射返回非法级别: {level}")
        logger.debug(
            "[Approval] 分级判定 object_type=%s action=%s → level=%s"
            "（map_hit=%s 默认=%s）",
            object_type, action, level, key in self._level_map, self._default_level)
        return level

    # ─── 提交 ───

    def submit(self, object_type: str, object_id: str, *,
               action: str = "",
               description: str = "",
               payload: Optional[Dict[str, Any]] = None,
               eval_result: Optional[Dict[str, Any]] = None,
               actor: str = "system",
               trigger: str = "api",
               applier: Optional[Callable[[], Any]] = None,
               level: Optional[str] = None,
               actor_ctx: Optional[ActorContext] = None) -> ApprovalRecord:
        """提交一次变更进入审批流

        - L0: 自动放行（执行 applier → merged，仅作审计留痕）；
        - L1: pending_review（不执行 applier，审批通过后 merge 时执行）；
        - L2: pending_review + manual_required（自动只产出建议）。

        Args:
            actor_ctx: 执行体上下文（TASK-S4-01）。**提交阶段只判矩阵格与范围
                口径**：auto 可提交提案（"自动只产出建议"），sub_agent 不进审批面；
                `stage.promote`（强制推进）一类对象在提交阶段即要求 human。

        Raises:
            ApprovalError: object_id 为空 / level 非法
            ApprovalPermissionError: §7.0 Actor 矩阵拒绝（已告警 + 已审计）
        """
        if not object_id:
            raise ApprovalError("ApprovalRecord.object_id 不能为空")
        self._guard(action=action or "submit", object_type=object_type,
                    object_id=object_id, actor=actor, actor_ctx=actor_ctx,
                    payload=payload, enforce_preconditions=False)
        resolved = level if level is not None else self.route_level(object_type, action)
        logger.debug(
            "[Approval] 提交变更 object_type=%s object_id=%s action=%s "
            "level=%s trigger=%s", object_type, object_id, action, resolved, trigger)
        with self._lock:
            self._ensure_loaded()
            rec = ApprovalRecord(
                object_type=object_type, object_id=object_id,
                level=resolved, action=action, description=description,
                payload=payload, eval_result=eval_result,
                actor=actor, trigger=trigger,
                manual_required=(resolved == "L2"),
            )
            rec.apply_actor_ctx(actor_ctx)
            self._validate(rec)
            if not self._enabled:
                # 审批开关关闭（APPROVAL_ENABLED=0）：直接放行并执行 applier，
                # 记录仍留档（decision_reason 标记 bypass，供审计）。
                rec.decision_reason = "APPROVAL_ENABLED=0 审批关闭，直接放行（构造期已告警）"
                if applier is not None:
                    try:
                        applier()
                    except Exception as e:  # noqa: BLE001
                        rec.decision_reason += f" | applier 执行失败: {e}"
                        rec.state = "rejected"
                        self._append_record(rec)
                        return rec
                rec.state = "merged"
                rec.merged_at = datetime.now().isoformat(timespec="seconds")
                self._append_record(rec)
                logger.warning(
                    "[Approval] ⚠ 审批关闭直接放行 %s/%s action=%s record=%s"
                    "（无人类监督）", object_type, object_id, action, rec.record_id)
                return rec
            if resolved == "L0":
                # L0 自动放行：执行 applier 后直接 merged
                if applier is not None:
                    try:
                        applier()
                    except Exception as e:  # noqa: BLE001 L0 执行失败 → 标记错误留痕
                        rec.decision_reason = f"L0 applier 执行失败: {e}"
                        rec.state = "rejected"
                        self._append_record(rec)
                        logger.error("[Approval] L0 自动放行执行失败 %s/%s: %s",
                                     object_type, object_id, e)
                        return rec
                rec.state = "merged"
                rec.merged_at = datetime.now().isoformat(timespec="seconds")
                self._append_record(rec)
                logger.info(
                    "[Approval] L0 自动放行 %s/%s action=%s record=%s",
                    object_type, object_id, action, rec.record_id)
                return rec

            rec.state = "pending_review"
            self._append_record(rec)
            if applier is not None:
                self._appliers[rec.record_id] = applier
            logger.info(
                "[Approval] %s 变更待审批 %s/%s action=%s record=%s manual=%s",
                resolved, object_type, object_id, action, rec.record_id,
                rec.manual_required)
            return rec

    # ─── 审批动作 ───

    def approve(self, record_id: str, actor: str = "reviewer",
                note: str = "",
                actor_ctx: Optional[ActorContext] = None,
                second_factor_ok: bool = False) -> ApprovalRecord:
        """审批通过（pending_review → approved）

        §7.0：审批 Approve **仅 human**。auto(skill) / sub_agent 调用 →
        `ApprovalPermissionError`（已告警 + 已审计）。

        Args:
            actor_ctx: 执行体上下文（路由经身份层构造；不传则按 actor 名推断）。
            second_factor_ok: 是否已通过二次认证（§5.7⑦ destructive 强制）。
        """
        with self._lock:
            rec = self._get_required(record_id)
            self._guard(action="approve", object_type=rec.object_type,
                        object_id=rec.object_id, actor=actor, actor_ctx=actor_ctx,
                        record_id=record_id, reason=note, payload=rec.payload,
                        second_factor_ok=second_factor_ok)
            rec.apply_actor_ctx(actor_ctx)
            logger.info("[Approval] 审批通过 record=%s actor=%s note=%s state=%s",
                        record_id, actor, note, rec.state)
            self._transition(rec, "approved", actor=actor, reason=note)
            return rec

    def reject(self, record_id: str, actor: str = "reviewer",
               reason: str = "",
               actor_ctx: Optional[ActorContext] = None,
               second_factor_ok: bool = False) -> ApprovalRecord:
        """驳回（pending_review → rejected）；reason 必填（审计要求）

        §7.0：审批 Deny **仅 human**（同 approve）。
        """
        if not reason.strip():
            raise ApprovalError("reject 必须提供 reason（审计要求）")
        with self._lock:
            rec = self._get_required(record_id)
            self._guard(action="reject", object_type=rec.object_type,
                        object_id=rec.object_id, actor=actor, actor_ctx=actor_ctx,
                        record_id=record_id, reason=reason, payload=rec.payload,
                        second_factor_ok=second_factor_ok)
            rec.apply_actor_ctx(actor_ctx)
            logger.warning("[Approval] 驳回 record=%s actor=%s reason=%s state=%s",
                           record_id, actor, reason, rec.state)
            self._transition(rec, "rejected", actor=actor, reason=reason)
            return rec

    def merge(self, record_id: str, actor: str = "reviewer",
              actor_ctx: Optional[ActorContext] = None,
              second_factor_ok: bool = False) -> ApprovalRecord:
        """合并生效（approved → merged）：执行 applier

        L2（manual_required）或 applier 缺失时抛 ApprovalStateError，
        由人工执行后调用 mark_manual_executed()（守不易：绝不自动执行 L2）。
        §7.0：合并生效是审批结果落地，**仅 human**。
        """
        with self._lock:
            rec = self._get_required(record_id)
            self._guard(action="merge", object_type=rec.object_type,
                        object_id=rec.object_id, actor=actor, actor_ctx=actor_ctx,
                        record_id=record_id, payload=rec.payload,
                        second_factor_ok=second_factor_ok)
            if rec.state != "approved":
                logger.warning(
                    "[Approval] merge 被拒：record=%s 当前 state=%s 非 approved",
                    record_id, rec.state)
                raise ApprovalStateError(
                    f"仅 approved 记录可 merge（当前 state={rec.state}）")
            if rec.manual_required:
                logger.warning(
                    "[Approval] merge 被拒：record=%s 为 L2 需人工执行"
                    "（manual_required），禁止自动 merge", record_id)
                raise ApprovalStateError(
                    f"{rec.record_id} 为 L2 需人工执行，禁止自动 merge")
            applier = self._appliers.get(record_id)
            if applier is None:
                logger.warning(
                    "[Approval] merge 被拒：record=%s 无 applier"
                    "（可能进程重启），需人工执行", record_id)
                raise ApprovalStateError(
                    f"{rec.record_id} 无 applier（可能进程重启），需人工执行")
            try:
                applier()
            except Exception as e:  # noqa: BLE001
                logger.error("[Approval] merge applier 执行失败 record=%s actor=%s: %s",
                             record_id, actor, e)
                raise ApprovalStateError(f"merge applier 执行失败: {e}") from e
            rec.state = "merged"
            rec.merged_at = datetime.now().isoformat(timespec="seconds")
            rec.updated_at = rec.merged_at
            rec.actor = actor
            rec.apply_actor_ctx(actor_ctx)
            self._persist()
            self._audit("approval.merged", rec)
            # TASK-S2-03 §6.6：合并生效是「系统执行审批结果」，只测量不计介入
            self._emit_metrics("merged", rec, actor=actor, count_intervention=False)
            self._appliers.pop(record_id, None)
            logger.info("[Approval] 变更已合并生效 record=%s actor=%s",
                        record_id, actor)
            return rec

    def mark_manual_executed(self, record_id: str, actor: str = "reviewer",
                             note: str = "",
                             actor_ctx: Optional[ActorContext] = None) -> ApprovalRecord:
        """人工执行完成标记（approved/rejected → archived）

        L2 高风险操作审批通过后由人工执行，执行完调用本方法归档，
        避免长期悬挂在 approved 状态造成审计困惑。
        §7.0：归档是审批结果的人工落地，**仅 human**。
        """
        with self._lock:
            rec = self._get_required(record_id)
            self._guard(action="mark_manual_executed", object_type=rec.object_type,
                        object_id=rec.object_id, actor=actor, actor_ctx=actor_ctx,
                        record_id=record_id, reason=note, payload=rec.payload)
            if rec.state not in ("approved", "rejected"):
                raise ApprovalStateError(
                    f"仅 approved/rejected 可标记人工执行（当前 state={rec.state}）")
            rec.state = "archived"
            rec.updated_at = datetime.now().isoformat(timespec="seconds")
            if note:
                rec.decision_reason = (rec.decision_reason + f" | {note}").strip(" |")
            rec.actor = actor
            rec.apply_actor_ctx(actor_ctx)
            self._persist()
            self._audit("approval.archived", rec, detail={"note": str(note or "")[:200]})
            # TASK-S2-03 §6.6：L2 人工执行归档（测量事件；介入已在 approve 时计数）
            self._emit_metrics("archived", rec, actor=actor,
                               detail={"note": str(note or "")[:200]},
                               count_intervention=False)
            logger.info("[Approval] 人工执行完成并归档 record=%s actor=%s", record_id, actor)
            return rec

    # ─── 人工查看（§6.1「查看=0」的真实发生点） ───

    def record_view(self, record_id: str, actor: str = "human",
                    detail: Optional[Dict[str, Any]] = None) -> None:
        """人工**查看**待审批项 → §6.1 介入埋点（``view`` 权重 0，只计数不加权）

        由服务层列表接口（`SkillsMgmtService.list_pending_approvals`）逐条调用。
        **放在审批域而非服务层**：服务层导入 `agent.observability` 会与
        「observability → … → skills_mgmt.service」既有链路构成循环依赖（CI 架构规则
        `no_circular_dependency` 实测拦截），而审批域已在治理侧持有埋点接线。
        best-effort：任何失败都不影响审批查询。
        """
        try:
            from agent.observability import acr as _acr
            payload = {"record_id": str(record_id or "")}
            payload.update({k: v for k, v in (detail or {}).items() if v is not None})
            _acr.record_intervention(
                _acr.KIND_VIEW, actor=str(actor or "human"),
                source_ref=f"approval_view:{record_id}", extra=payload)
        except Exception as e:  # noqa: BLE001 埋点不得影响查询
            logger.debug("[Approval] view 埋点失败 record=%s: %s", record_id, e)

    # ─── 超时未审批（§6.1「超时 Deny=2」的真实发生点） ───
    def expire_pending(self, *, older_than_seconds: float = 86400.0,
                       actor: str = "system", note: str = "",
                       limit: Optional[int] = None,
                       now: Optional[datetime] = None,
                       object_type: str = "") -> List[ApprovalRecord]:
        """把超时未处理的 ``pending_review`` 记录判为拒绝（timeout deny）

        §6.1 计数口径中「超时 Deny=2」需要一个**真实发生点**：云枢此前没有任何审批
        超时路径（S2-02 盘点结论：审批只经服务层网关，无定时器）。本方法提供该路径，
        并保证：

        - 只处理 ``pending_review``（``draft`` / 终态一律不动）；
        - 判定依据是 ``created_at`` 的**实际待办时长**（可显式传 ``now`` 便于单测）；
        - 迁移走统一漏斗 `_transition`（状态机+审计+埋点一致），但介入 kind 显式指定为
          ``timeout_deny``（权重 2），**不与 ``reject`` 重复计数**；
        - ``decision_reason`` 留痕超时阈值与待办时长（审计可复核），且以
          ``TIMEOUT_DENY_REASON_PREFIX`` 开头 —— 使用方据此区分"系统超时"与"人工否决"。

        Args:
            object_type: **（可选）只清理该对象类型的待办**；空串＝全部（既有行为不变）。
                为什么需要它：不同对象类型的合理待办时长不同（工具调用 15 分钟就该重来，
                技能/提示词提案可以挂一整天），一把阈值全清会误伤另一类待办。

        Returns:
            被判定超时拒绝的记录列表（无超时记录 → 空列表）。
        """
        current = now or datetime.now()
        threshold_ms = max(0.0, float(older_than_seconds) * 1000.0)
        scope = str(object_type or "").strip()
        expired: List[ApprovalRecord] = []
        with self._lock:
            self._ensure_loaded()
            for rec in list(self._records):
                if rec.state != "pending_review":
                    continue
                if scope and str(rec.object_type) != scope:
                    continue
                try:
                    created = datetime.fromisoformat(str(rec.created_at))
                except (TypeError, ValueError):
                    logger.warning("[Approval] 超时判定跳过（created_at 不可解析）record=%s",
                                   rec.record_id)
                    continue
                elapsed_ms = (current - created).total_seconds() * 1000.0
                if elapsed_ms < threshold_ms:
                    continue
                reason = (f"{TIMEOUT_DENY_REASON_PREFIX}：待办 {elapsed_ms / 1000.0:.0f}s "
                          f"≥ 阈值 {older_than_seconds:.0f}s")
                if note:
                    reason = f"{reason} | {note}"
                self._transition(rec, "rejected", actor=actor, reason=reason,
                                 metrics_kind="timeout_deny", latency_ms=elapsed_ms)
                expired.append(rec)
                if limit is not None and len(expired) >= int(limit):
                    break
        if expired:
            logger.warning("[Approval] 超时未审批判定为拒绝 %d 条（§6.1 超时 Deny=2）%s",
                           len(expired), f"（object_type={scope}）" if scope else "")
        return expired

    # ─── 生效判定 ───

    def is_effective(self, record_id: str) -> bool:
        """该审批记录对应的变更是否已生效（验收 2）

        pending_review / approved / rejected 均未生效 → False。
        """
        with self._lock:
            rec = self._index.get(record_id)
            return rec.effective if rec is not None else False

    # ─── 查询 ───

    def get(self, record_id: str) -> Optional[ApprovalRecord]:
        with self._lock:
            self._ensure_loaded()
            return self._index.get(record_id)

    def list(self, filter: Optional[Dict[str, Any]] = None, *,
             limit: Optional[int] = None) -> List[ApprovalRecord]:
        """等值过滤查询（支持集合匹配）；按 created_at 降序"""
        with self._lock:
            self._ensure_loaded()
            results = [r for r in self._records if self._match(r, filter)]
            results.sort(key=lambda r: r.created_at, reverse=True)
            if limit is not None:
                results = results[:limit]
            return results

    def count_by_state(self, state: Optional[str] = None) -> int:
        with self._lock:
            self._ensure_loaded()
            if state is None:
                return len(self._records)
            return sum(1 for r in self._records if r.state == state)

    def stats(self) -> Dict[str, Any]:
        """审批统计（供审计仪表盘）"""
        with self._lock:
            self._ensure_loaded()
            by_state: Dict[str, int] = {}
            by_level: Dict[str, int] = {}
            for r in self._records:
                by_state[r.state] = by_state.get(r.state, 0) + 1
                by_level[r.level] = by_level.get(r.level, 0) + 1
            return {
                "total": len(self._records),
                "by_state": by_state,
                "by_level": by_level,
                "pending": by_state.get("pending_review", 0),
                "merged": by_state.get("merged", 0),
                "rejected": by_state.get("rejected", 0),
                "enabled": self._enabled,
            }

    # ─── TASK-S4-01：执行体校验（§7.0 Actor 矩阵） ───

    def _guard(self, *, action: str, object_type: str, object_id: str,
               actor: str = "", actor_ctx: Optional[ActorContext] = None,
               record_id: str = "", reason: str = "",
               payload: Optional[Dict[str, Any]] = None,
               second_factor_ok: bool = False,
               enforce_preconditions: Optional[bool] = None) -> Any:
        """审批入口执行体校验（**后端单表校验的唯一落点**）

        拒绝时 `authorize_approval_action` 已调用
        `agent/security/approval_guard.py::report_denial` 完成
        **告警 + 链式审计（`policy.denied` 事件镜像）**，本方法只负责中止。
        """
        decision = authorize_approval_action(
            action=action, object_type=object_type, object_id=object_id,
            actor_ctx=actor_ctx, actor=actor, record_id=record_id, reason=reason,
            second_factor_ok=second_factor_ok, payload=payload, source="agent",
            enforce_preconditions=enforce_preconditions)
        if not decision.allowed:
            logger.warning(
                "[Approval] ⛔ 执行体越权被拒 action=%s %s/%s actor=%s type=%s: %s",
                action, object_type, object_id, actor or decision.actor,
                decision.actor_type, decision.reason)
            raise ApprovalPermissionError(
                f"审批动作被拒（§7.0 Actor 矩阵）: {decision.reason}", decision)
        return decision

    # ─── 内部 ───

    def _audit(self, action: str, rec: ApprovalRecord, *,
               detail: Optional[Dict[str, Any]] = None) -> None:
        """链式审计留痕（S2-02；best-effort，绝不阻断审批主路径）

        审批是治理关键路径：**提交 / 审批 / 驳回 / 合并 / 归档**全部写入统一链式
        审计表（P7.2-24 审计平权：与 UI 写路由同表同格式），旧 JSONL 留档不变。

        TASK-S4-01 追加三类叶子字段（**同一条记录内**，不新增第二次写入）：
          - 身份口径：`actor_type` / `identity_source` / `session_id`；
          - PII 口径（裁定 B）：`actor_ip_masked` / `actor_ip_hash`（**原始 IP 不落盘**）；
          - 治理可回溯：`undo_hint` / `compensating_action` / `undo_hint_status`
            （`stage.promote` 一类对象「能不能退、怎么退」的事后可答性）。

        【关联键走 `technical` 通道】`record_id` 是**内部生成的关联键**（非用户输入），
        经普通 payload 会被统一脱敏器的启发式误伤（实测在链上被掩码为
        `appr-202609********374219-...`），导致「按 record_id 查链」失效；
        故按 S2-02 既定口径改走 `technical=`（该通道不脱敏，专为关联键设置）。
        """
        try:
            from agent.audit import audit as _audit_facade
            payload: Dict[str, Any] = {
                "object_type": rec.object_type,
                "object_id": rec.object_id,
                "level": rec.level,
                "state": rec.state,
                "trigger": rec.trigger,
                "manual_required": rec.manual_required,
                "description": str(rec.description or "")[:400],
                "legacy": "approval_records.jsonl",
            }
            payload.update(rec.identity_fields())
            payload.update(rec.pii_fields())
            payload.update(governance_trace_fields(
                rec.object_type, rec.object_id, rec.payload))
            if detail:
                payload.update(detail)
            _audit_facade.record(
                action, actor=rec.actor,
                subject=f"{rec.object_type}:{rec.object_id}", payload=payload,
                source="agent", status=rec.state,
                technical={"record_id": rec.record_id})
        except Exception as e:  # noqa: BLE001 审计失败不得影响审批
            logger.debug("[Approval] 链式审计留痕失败 action=%s: %s", action, e)

    # ─── ACR / 事件埋点（TASK-S2-03；best-effort，绝不阻断审批主路径） ───

    @staticmethod
    def _pending_since(rec: ApprovalRecord) -> Optional[float]:
        """审批待办时长（ms）：created_at → now（解析失败/未来时间 → None）"""
        try:
            created = datetime.fromisoformat(str(rec.created_at))
        except (TypeError, ValueError):
            return None
        try:
            delta_ms = (datetime.now() - created).total_seconds() * 1000.0
        except (TypeError, ValueError, OSError):
            return None
        return None if delta_ms < 0 else delta_ms

    def _emit_metrics(self, kind: str, rec: ApprovalRecord, *,
                      actor: Optional[str] = None,
                      detail: Optional[Dict[str, Any]] = None,
                      latency_ms: Optional[float] = None,
                      count_intervention: bool = True,
                      ts: Any = None) -> None:
        """审批 → events.v1 埋点（§6.6 approval + §6.1 intervention）

        ``kind`` 取 §6.1 口径：``approve`` / ``conditional`` / ``auto_pass`` /
        ``timeout_deny`` / ``reject``（扩展项，见 `agent.observability.acr`）；
        ``submit`` 一类「进入审批」动作用事件 ``approval.required``（§3.6 八事件之一）
        表达，不计介入。
        """
        try:
            from agent.observability import acr as _acr
            from agent.observability import events as _events

            if kind == "required":
                _events.emit(
                    _events.EV_APPROVAL_REQUIRED,
                    {"record_id": rec.record_id, "object_type": rec.object_type,
                     "object_id": rec.object_id, "level": rec.level,
                     "state": rec.state, "trigger": rec.trigger,
                     "manual_required": bool(rec.manual_required)},
                    actor=str(actor or rec.actor or "system"),
                    idempotency_key=f"approval.required:{rec.record_id}",
                    ts=ts)
                return

            latency = latency_ms
            if latency is None:
                latency = self._pending_since(rec)
            _acr.record_approval(
                kind=kind, record_id=rec.record_id, state=rec.state,
                object_type=rec.object_type, object_id=rec.object_id,
                level=rec.level, actor=str(actor or rec.actor or "reviewer"),
                latency_ms=latency, task_id="",
                count_intervention=count_intervention,
                extra={**(detail or {}),
                       "manual_required": bool(rec.manual_required),
                       "trigger": rec.trigger},
                ts=ts)
        except Exception as e:  # noqa: BLE001 埋点失败不得影响审批
            logger.debug("[Approval] ACR 埋点失败 kind=%s: %s", kind, e)

    def _append_record(self, rec: ApprovalRecord) -> None:
        self._records.append(rec)
        self._index[rec.record_id] = rec
        self._persist()
        self._audit("approval.submit", rec)
        # §3.6 approval.required（进入审批）；L0 / 审批关闭的「自动放行」另计介入
        self._emit_metrics("required", rec)
        if rec.state == "merged":
            self._emit_metrics("auto_pass", rec, actor=rec.actor)

    def _get_required(self, record_id: str) -> ApprovalRecord:
        self._ensure_loaded()
        rec = self._index.get(record_id)
        if rec is None:
            logger.warning("[Approval] 审批记录不存在 record=%s（调用方需核对 record_id）",
                           record_id)
            raise ApprovalError(f"审批记录不存在: {record_id}")
        return rec

    def _transition(self, rec: ApprovalRecord, to: str, *,
                    actor: str, reason: str,
                    metrics_kind: Optional[str] = None,
                    latency_ms: Optional[float] = None,
                    emit_metrics: bool = True) -> None:
        allowed = _TRANSITIONS.get(rec.state, ())
        if to not in allowed:
            raise ApprovalStateError(
                f"非法状态迁移: {rec.state} → {to}（允许: {allowed or '无'}）")
        rec.state = to
        rec.actor = actor
        rec.updated_at = datetime.now().isoformat(timespec="seconds")
        if reason:
            rec.decision_reason = reason
        self._persist()
        self._audit(f"approval.{to}", rec, detail={"reason": str(reason or "")[:400]})
        # TASK-S2-03 §6.1：人工处置 → 介入埋点（Approve=1 / 条件=0.5 / 驳回=扩展项）
        # metrics_kind 由超时路径显式指定（timeout_deny=2），避免与 reject 重复计数
        if emit_metrics:
            kind = metrics_kind
            if kind is None:
                if to == "approved":
                    kind = "conditional" if str(reason or "").strip() else "approve"
                elif to == "rejected":
                    kind = "reject"
                else:
                    kind = ""
            if kind:
                self._emit_metrics(kind, rec, actor=actor, latency_ms=latency_ms,
                                   detail={"reason": str(reason or "")[:200]})
        logger.info("[Approval] %s/%s state: %s → %s actor=%s",
                    rec.object_type, rec.object_id, rec.record_id, to, actor)

    @staticmethod
    def _validate(rec: ApprovalRecord) -> None:
        if not rec.object_id:
            raise ApprovalError("ApprovalRecord.object_id 不能为空")
        if rec.level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法审批级别: {rec.level}")

    @staticmethod
    def _match(rec: ApprovalRecord, filter: Optional[Dict[str, Any]]) -> bool:
        if not filter:
            return True
        for key, expect in filter.items():
            actual = getattr(rec, key, None)
            if isinstance(expect, (list, tuple, set)):
                if actual not in expect:
                    return False
            elif actual != expect:
                return False
        return True

    def _ensure_loaded(self) -> None:
        """把记录文件载入内存索引（**单条损坏不得拖垮整个审批面**）

        【为什么 catch ``Exception`` 而不是少数几个异常】原先只兜
        ``(ValueError, TypeError, KeyError)``，而实际会从
        ``ApprovalRecord.from_dict`` / ``__post_init__`` 冒出来的还有
        ``AttributeError``（JSON 行是 ``[1,2,3]`` 这类非对象）、``ApprovalLevelError``
        （非法 ``level``）、``ApprovalError``（非法 ``state``）等 —— 2026-09-18 实测三种
        损坏记录**全部直接抛给调用方**，于是 ``/api/approval/pending`` 与审批收件箱
        整体 500，而且 ``_loaded`` 停在 False ⇒ **每次请求都再抛一次**。
        一条坏记录不该让"人看不到任何待办"，故按"跳过该条 + 记 error 留痕"处理：
        宁可少显示一条（可审计、可人工修），不可让整个审批面不可用。
        """
        if self._loaded:
            return
        for d in _read_jsonl(self._records_path):
            try:
                rec = ApprovalRecord.from_dict(d)
                self._records.append(rec)
                self._index[rec.record_id] = rec
            except Exception as e:  # noqa: BLE001 单条损坏只跳过这一条（见 docstring）
                logger.error("[Approval] 跳过损坏审批记录（type=%s, error=%s）: %s",
                             type(d).__name__, e,
                             str(d)[:200] if not isinstance(d, dict)
                             else str(d.get("record_id") or "?"))
        self._loaded = True
        logger.info("[Approval] 加载完成 count=%s path=%s",
                    len(self._records), self._records_path)

    def _persist(self) -> None:
        lines = [json.dumps(r.to_dict(), ensure_ascii=False)
                 for r in self._records]
        _atomic_write_lines(self._records_path, lines)
