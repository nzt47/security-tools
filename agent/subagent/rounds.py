"""二次派发轮次机制 —— 母体为中枢的「校验/修正第二轮」（P4/P5 通信收口）

【为什么需要它（不这样会怎样）】
    共享任务看板（`task_board.py`）已把每次委派落成 append-only 事件流，并按
    `task_id` 折叠出「当前态」；`TaskRecord` 也预留了 `round` /
    `previous_delegation_id` 两个字段，`status_from_outcome` 的注释更明说
    `superseded`（被新一轮取代）"由未来的轮次机制显式写入" —— 即：**字段与语义
    早已就位，缺的只是把第二轮真正接上**。

    如果没有这一段接线，就会出现两种"看起来做了、其实没做"：
      · 用户把同一任务的第二条指令再发一次 ⇒ 看板把它折叠成**另一个 task_id**，
        两次委派在板上毫无关系，"这一轮是在校验上一轮"这件事**不可追溯**；
      · 想表达"取代"时只能靠人读 goal 文本猜。看板回答了"发生了什么"，却回答不了
        "谁取代了谁" —— 而后者正是协同（多轮）与单发（一次）的分水岭。

    本模块补上这一环：把「上一轮委派记录 + 本轮请求」解析成一份**可追溯的跟单
    上下文**，并给出写板前的**机器可读判定**（缺哪些、为什么不许跟）。三段职责，
    互不越界：

      1. `link_follow_up(ctx, previous)` —— 纯函数：产出「第二轮 ctx」
         （沿用上一轮 `task_id` 以让看板折叠成**同一条逻辑任务**；metadata 带
         `round=2` / `previous_delegation_id`；③已有成果追加一条**云枢自有的
         引用行**，让第二轮知道自己在校验什么）；
      2. `validate_previous(...)` —— 纯判定：找不到 / 域不一致 / 已是第二轮 ⇒ 抛
         `RoundError`（带错误码）。**fail-closed**：绝不静默降级成"普通第一轮"，
         否则用户以为在做校验、实际是另起炉灶，而看板上看不出差别；
      3. `round_view(ctx)` —— 回显投影（给 HTTP 响应用，**不含任何正文**）。

【边界（三条不可协商）】
    · **母体是唯一写板者**：本模块不导入、不调用看板写 API，只对"已读到的记录"
      做纯计算；真正的写板仍只在 `task_board.record_outcome`（唯一写入口），
      分身侧（`channel.py` 起的子进程）永远拿不到。
    · **只做一轮跟单（round ∈ {1, 2}）**：`task_board._round_value` 已把轮次收敛为
      1|2；对"上一轮已经是第二轮"的再跟会被归一化回 `round=1`，从而产生"第三轮
      看起来像第一轮"的假象，故这里**显式拒绝**（`previous.round >= 2`）。
    · **不搬运上一轮的交付物正文**：那是外来文本且体量不可控（§5.7 机制 1）。引用行
      只用看板上本来就有的 `goal`（母体自己填的、非外来文本），并且是**引用**而不是
      把结果塞回上下文。

【依赖纪律】
    仅标准库 + `dataclasses.replace`；零 agent 内部导入（避免环）。看板记录以
    `Mapping` 形态注入，不依赖 `TaskRecord` 类型 —— 这样纯逻辑可以脱离看板单测。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Dict, Mapping, Optional

__all__ = [
    "ROUND_FIELD",
    "PREVIOUS_FIELD",
    "SUPERSEDED_STATUS",
    "MAX_ROUND",
    "RoundError",
    "E_ROUND_INVALID",
    "E_ROUND_PREVIOUS_NOT_FOUND",
    "E_ROUND_SCOPE_MISMATCH",
    "E_ROUND_LIMIT",
    "round_of",
    "reference_line",
    "validate_previous",
    "link_follow_up",
    "round_view",
]

#: metadata 里的键名（与 `task_board.TaskRecord` 的字段名**逐字一致** ——
#: 改这里就是改看板契约，必须两边同改；回归用例对拍这两个键名）。
ROUND_FIELD = "round"
PREVIOUS_FIELD = "previous_delegation_id"

#: 被新一轮取代的状态（与 `task_board.STATUSES` 里的同一个词）
SUPERSEDED_STATUS = "superseded"

#: 支持的最大轮次。>1 的跟单只做一层：第一轮 → 校验/修正第二轮。
MAX_ROUND = 2

#: 错误码（HTTP 层据此回 400；机器可读，不靠中文原因串）
E_ROUND_INVALID = "E_ROUND_INVALID"
E_ROUND_PREVIOUS_NOT_FOUND = "E_ROUND_PREVIOUS_NOT_FOUND"
E_ROUND_SCOPE_MISMATCH = "E_ROUND_SCOPE_MISMATCH"
E_ROUND_LIMIT = "E_ROUND_LIMIT"


class RoundError(Exception):
    """跟单上下文非法（fail-closed：调用方据此 400，不静默降级）"""

    def __init__(self, message: str, *, code: str = E_ROUND_INVALID) -> None:
        super().__init__(message)
        self.code = str(code or E_ROUND_INVALID)

    def to_dict(self) -> Dict[str, Any]:
        return {"error_code": self.code, "error": str(self)}


# ════════════════════════════════════════════════════════════
#  纯函数
# ════════════════════════════════════════════════════════════


def _text(value: Any) -> str:
    """任意值 → 字符串（None → ""，不产生 "None" 文本）"""
    if value is None:
        return ""
    return str(value)


def _meta(source: Any) -> Mapping[str, Any]:
    """取 metadata（source 是 ctx 时读 .metadata；是 Mapping 时直接当 metadata）

    两种入参形态都接受，是为了让「看板记录」与「委派上下文」共用同一套判定，
    不必为每种来源各写一份。
    """
    meta = getattr(source, "metadata", None)
    if isinstance(meta, Mapping):
        return meta
    if isinstance(source, Mapping):
        return source
    return {}


def round_of(source: Any) -> int:
    """轮次归一化为 1 | 2（与 `task_board._round_value` **同口径**）

    非法值回落 1（不放大口径）；`source` 可为看板记录、metadata 映射或委派上下文。
    """
    raw = _meta(source).get(ROUND_FIELD, 1)
    try:
        return MAX_ROUND if int(raw) == MAX_ROUND else 1
    except (TypeError, ValueError):
        return 1


def reference_line(previous: Mapping[str, Any], *, max_chars: int = 120) -> str:
    """上一轮委派 → 一行**引用**（③已有成果用；不含任何交付物正文）

    只取看板上本来就有的 `delegation_id` 与被截断的 `goal`。goal 是母体自己
    填写的编排目标（非子代理产出），故不涉及 §5.7 的外来文本边界。
    """
    record = previous if isinstance(previous, Mapping) else {}
    previous_id = _text(record.get("delegation_id"))
    if not previous_id:
        return ""
    goal = _text(record.get("goal")).strip()
    if not goal:
        return "上一轮委派 " + previous_id
    return "上一轮委派 %s 的目标：%s" % (previous_id, goal[:max_chars])


def validate_previous(previous: Optional[Mapping[str, Any]], *,
                      tenant_id: str = "default", subject_id: str = "",
                      previous_delegation_id: str = "") -> Mapping[str, Any]:
    """校验「上一轮委派记录」是否可作为跟单基准（不合格 ⇒ `RoundError`）

    判据（缺一即拒，fail-closed）：
      1. 记录存在（`None` ⇒ `E_ROUND_PREVIOUS_NOT_FOUND`）；
      2. 该委派**尚未被取代**（`status=superseded` ⇒ `E_ROUND_LIMIT`：说明它所属的
         逻辑任务已经进入过新一轮，不能再把它当基准；否则会对同一条任务重复开第二轮）；
      3. tenant/subject **一致**（跨租户/跨主体跟单 = 越域，`E_ROUND_SCOPE_MISMATCH`）；
      4. 上一轮**尚未是第二轮**（`round >= MAX_ROUND` ⇒ `E_ROUND_LIMIT`）。

    Returns:
        通过校验的上一轮记录（便于链式取用）。
    """
    if not previous:
        hint = _text(previous_delegation_id) or "（未提供）"
        raise RoundError(
            "上一轮委派未在看板上找到：" + hint
            + "（跟单必须指向一次真实上板的委派，不能凭空引用）",
            code=E_ROUND_PREVIOUS_NOT_FOUND)
    if _text(previous.get("status")) == SUPERSEDED_STATUS:
        raise RoundError(
            "上一轮委派已被新一轮取代（status=superseded）：不能把它当跟单基准再跟一次",
            code=E_ROUND_LIMIT)
    prev_tenant = _text(previous.get("tenant_id")) or "default"
    want_tenant = _text(tenant_id) or "default"
    if prev_tenant != want_tenant:
        raise RoundError(
            "跨租户跟单被拒：上一轮 tenant_id=%s，本轮 tenant_id=%s"
            % (prev_tenant, want_tenant),
            code=E_ROUND_SCOPE_MISMATCH)
    prev_subject = _text(previous.get("subject_id"))
    want_subject = _text(subject_id)
    if prev_subject != want_subject:
        raise RoundError(
            "跨主体跟单被拒：上一轮 subject_id=%s，本轮 subject_id=%s"
            % (prev_subject or "（空）", want_subject or "（空）"),
            code=E_ROUND_SCOPE_MISMATCH)
    if round_of(previous) >= MAX_ROUND:
        raise RoundError(
            "已到最大轮次 %d：不能对第二轮再跟单（再多会与「轮次只有 1|2」的看板口径冲突）"
            % MAX_ROUND,
            code=E_ROUND_LIMIT)
    return previous


def link_follow_up(ctx: Any, previous: Mapping[str, Any]) -> Any:
    """把本轮上下文接成「上一轮的跟单」（**纯函数**：返回新 ctx，不改原对象）

    三件事：
      · `task_id` 沿用上一轮 —— 看板按 task_id 折叠，这是"同一条逻辑任务的第二轮"
        在数据上的唯一表达；不沿用就会折成两个互不相关的任务；
      · metadata 写入 `round=2` / `previous_delegation_id`（标识级，无正文）；
      · ③已有成果**追加一条引用行**（幂等：已存在则不重复），让第二轮知道在校验谁。

    Args:
        ctx: 本轮的 `DelegationContext`（八要素已补齐）。
        previous: 上一轮看板记录（应已过 `validate_previous`）。

    Returns:
        新的 `DelegationContext`（`dataclasses.replace` 产物）。
    """
    record = previous if isinstance(previous, Mapping) else {}
    previous_id = _text(record.get("delegation_id"))
    if not previous_id:
        raise RoundError("上一轮记录缺少 delegation_id，无法建立跟单链",
                         code=E_ROUND_INVALID)
    task_id = _text(record.get("task_id")) or previous_id

    prior = list(getattr(ctx, "prior_artifacts", None) or ())
    line = reference_line(record)
    if line and line not in prior:
        prior.append(line)

    meta: Dict[str, Any] = dict(_meta(ctx))
    meta[ROUND_FIELD] = MAX_ROUND
    meta[PREVIOUS_FIELD] = previous_id

    return replace(ctx, task_id=task_id, prior_artifacts=tuple(prior), metadata=meta)


def round_view(ctx: Any) -> Dict[str, Any]:
    """本轮轮次回显（HTTP 响应用；**不含任何正文**）

    形状与 `llm` / `role` / `memory` 等回显段同款：只回"这一轮是第几轮、
    跟的是谁、逻辑任务是谁"，调用方据此在界面上区分首轮与跟单。
    """
    meta = _meta(ctx)
    previous_id = _text(meta.get(PREVIOUS_FIELD))
    return {
        "round": round_of(ctx),
        "previous_delegation_id": previous_id,
        "task_id": _text(getattr(ctx, "task_id", "")) or _text(
            getattr(ctx, "delegation_id", "")),
        "follow_up": bool(previous_id),
    }
