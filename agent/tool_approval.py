"""工具调用审批桥（``object_type="tool_call"``）——把"需人工审批的工具调用"接进既有审批流

【为什么需要这一层】
    集中式工具闸门（``agent/tool_gate.py``）能判断"这个工具**要不要**人工审批"
    （``ToolMeta.needs_approval``），但**不知道"这一次调用是否已经被人批准过"**：
    审批流（``agent/skills_mgmt/approval.py``）里的记录长的是
    ``(object_type, object_id)``（技能参数提交 / 提示词应用 / 提案合并），
    没有任何字段能表达"批准的是**哪一次**带**什么参数**的调用"。
    于是缺的正是这一层：把一次具体的 ``(工具名, 参数)`` 变成审批流认得的对象
    （``object_type="tool_call"``、``object_id=<工具名>``），并给出**可判定的批准凭据**。
    只做桥接，不改闸门（闸门由主流程接线）、不改审批流、不改路由/UI。

【为什么单次有效（防"批准一次、命令跑无数次"）】
    人批准的是**当次调用**（一次 ``shell_execute`` 执行一条具体命令），不是
    "此后该工具永久放行"。若批准可以反复消费，收件箱里的一次点击就等价于把该工具
    的审批边界永久关掉；更糟的是模型只要重试就能复用同一张批准做**另一次**执行。
    故 ``consume()`` 把批准标记为已用，每次放行都必须**新批准一次**。

【为什么有 TTL（``CP_TOOL_APPROVAL_TTL_SEC``，默认 900s）】
    对齐审批面的既有时效口径：``agent/security/approval_session.py`` 的审批链接
    **默认 900s、硬上限 900s**（"≤15min"）。工具调用的批准若长期不失效，等于把
    审批面钉死的时效从背后绕开——人 10:00 批的一条命令，模型 14:00 仍能凭它执行。
    故本层用同一口径：自**裁决时刻**（``updated_at``；未决记录退回 ``created_at``）起
    超过 TTL 的批准一律视为无效（不生效、不消费、不报"已驳回"，让调用方重新走审批）。
    锚点取裁决时刻而非创建时刻，是因为人工可能在挂单很久之后才批准——按创建时刻计时会
    让"刚批的单"瞬间过期（详见 :func:`_within_ttl`）。

【为什么审批记录只读、另立一张消费台账（``data/tool_approval_uses.jsonl``）】
    **审批流是决策的唯一权威**：谁批的、什么时候批的、批的是什么，只能由
    ``data/approval_records.jsonl`` 说话，本层绝不改写/删除其中的任何记录
    （不制造第二真相，也不给"旁路改状态"留一个入口）。而"不可重放"不是决策，
    只是**消费事实**——它天然是追加型的（一次消费一行），且必须能被本进程之外
    看到（否则重启即可重放）。故单独一张 append-only 台账按 ``approval_id``
    记消费，进程内再加 ``threading.Lock`` + 集合缓存兜住并发；而缓存**必须按文件
    指纹失效**（``_consumed_ids``）——消费发生在**另一个进程**（UI/CLI）里，
    本进程只读文件的既成事实，缓存不失效就等于"B 用过、A 还能再用一次"
    （2026-09-22 修，复现与边界见 ``_consumed_ids`` 的 docstring）。
    审批状态为真、但台账说"已用过"⇒ 不生效（台账只做减法，不做加法）。

【唯一的例外：**系统超时清理**（2026-09-18 补）】
    本层的写操作有两个，且都**不替人做决定**：
      ① :func:`request_approval` 的 `submit()`（挂单，等人裁决）；
      ② :func:`_expire_stale_pending` 对**已过 TTL 的 ``tool_call`` 待办**调
         ``ApprovalFlow.expire_pending(object_type="tool_call")``，把它们判为
         **系统超时**（不是人工否决）并从收件箱清出。
    为什么必须有 ②：没有它，没人理的待办会永远挂在收件箱里（用户可见的堆积）；
    而清理产生的记录带 ``TIMEOUT_DENY_REASON_PREFIX``，被 :func:`_is_system_expiry`
    识别 ⇒ **既不算批准也不算驳回**，模型会重新挂一张新待办、也不会被告知
    "人工已否决、别再重试"（那是对人的操作的错误转述）。
    作用域严格限定 ``object_type="tool_call"``：技能/提示词提案的合理待办时长不同。 

【判定口径（与审批流字段的对应）】
    - **人工已批准** = 记录 ``state == "approved"``（**不是** ``effective``——
      后者是 ``state == "merged"``，语义是"系统已合并变更"，工具调用没有合并动作，
      用它会导致永远批不准）；
    - 绑定 = ``payload["args_digest"]`` 等于本次 ``(工具, 参数)`` 的摘要
      （``tool_call_digest``）⇒ **换一个参数就是另一次调用**，必须重新审批；
    - 会话 = ``payload["session_key"]``；记录侧为空串视为**通配**（兼容不区分会话的
      调用方），非空则必须与本次 ``session_key`` 相等；
    - 驳回（``state == "rejected"``）单独暴露给模型，让它知道"这条路已经被否决、
      别再重试"，与"还在等审批"区分开；
    - **幂等复用只看未决单**（``pending_review``）：消费不改审批流记录，已批准的单在
      记录里仍是 ``approved``，复用它会发出一张"收件箱里不存在、又永远兑现不了"的死单
      （详见 ``request_approval`` 的说明）；
    - **最新裁决为准（单一权威）**：``(tool, args_digest, session_key)`` 同一"待决事项"上
      取**裁决时刻**最新的一条（``approved`` / ``rejected``），再由它的状态决定结果。
      ``find_permission`` 与 ``is_rejected`` 都只基于同一个 :func:`_latest_decision`
      分支——**绝不各自比较时间**：两处比较必然出现不对称（实测过"旧驳回压掉新批准"
      且"老批准又无视新驳回"这类"审批人操作被静默忽略"的缺陷）。

【健壮性纪律】
    本模块所有公开函数**绝不抛异常**：异常一律收口为 ``{"ok": False, "error": ...}``
    或 ``None`` / ``False``，并 ``logger.warning`` / ``logger.debug`` 留痕。
    摘要算不出来（返回 ``""``）时**调用方必须视为"无法绑定审批"**——那是不放行的
    理由，绝不是放行的理由。

【为什么 payload 还带 ``undo_hint`` / ``compensating_action``】
    UI 侧有一条**硬规则**（``agent/ui_panels/data.py::approval_inbox``）：
    "审批气泡缺 ``undo_hint`` 不出现"——``bubble.visible = bool(undo_hint or
    compensating_action)``，而这两个字段对 ``object_type="tool_call"`` 只能来自
    payload（``governance_bridge`` 的 descriptor 查询不覆盖该对象类型）。
    即：**不在 payload 里给出撤销路径，人在收件箱里根本批不了这条单**。
    文案**如实**写本层真有的语义（批准不产生副作用、执行发生在模型原样重试时、单次有效、
    TTL 超时自动失效、重试前可驳回），TTL 秒数取**当前实际生效值**；
    ``compensating_action`` **留空**——工具自身的补偿动作不归本层，不臆造。

【配置（全部带默认值，环境变量口径与仓库既有模块一致）】
    CP_TOOL_APPROVAL_TTL_SEC      批准时效秒，默认 900（对齐 approval_session 的 ≤15min）
    CP_TOOL_APPROVAL_USES_PATH    消费台账 JSONL 路径，默认 data/tool_approval_uses.jsonl
    （审批记录路径与总开关沿用既有 ``APPROVAL_RECORDS_PATH`` / ``APPROVAL_ENABLED``，
      本模块**只读**这两个环境变量，不新增、不覆盖）
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  常量与配置
# ════════════════════════════════════════════════════════════

#: 审批流里承载"工具调用"的对象类型（本层与调用方（闸门）的唯一约定）
OBJECT_TYPE = "tool_call"

#: 动作名与触发源（写进审批记录，供审计/收件箱区分"这是工具调用"）
ACTION = "tool_call"
TRIGGER = "tool_gate"

#: 提交者：审批流 §7.0 外扩展允许 auto 提交提案（"自动只产出建议"，不生效）
ACTOR = "auto"

#: 批准时效环境变量（默认 900s，见模块 docstring"为什么有 TTL"）
ENV_TTL_SEC = "CP_TOOL_APPROVAL_TTL_SEC"
_DEFAULT_TTL_SEC = 900.0

#: 消费台账路径环境变量（测试隔离用；默认落 data/）
ENV_USES_PATH = "CP_TOOL_APPROVAL_USES_PATH"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_USES_PATH = _REPO_ROOT / "data" / "tool_approval_uses.jsonl"

#: 幂等复用只认这一种状态：**未决**（收件箱里真的还挂着这张待办，人点得到）
#: 【不改】已裁决的状态（approved / rejected / archived / merged）一律不复用——
#:   消费不改审批流记录，已批准的单在记录里仍是 approved，复用它会发出一张
#:   收件箱里不存在、又永远兑现不了的"死单"（详见 _find_reusable 的说明）。
_REUSE_STATES = ("pending_review",)

#: "已裁决"状态（参与"最新裁决为准"比较的两种；`latest decision wins` 见 _latest_decision）
_DECIDED_STATES = ("approved", "rejected")

#: 参数预览白名单：只取这些**可读**字段，其余一律不进描述
#: （描述是 UI 唯一展示的自由文本，且模型参数里可能有密钥/长正文）
_PREVIEW_KEYS = ("command", "query", "path", "url", "code", "pattern",
                 "prompt", "text", "target")

#: 预览截断长度：description 内 ≤120，payload 内 ≤500
_DESC_PREVIEW_MAX = 120
_PAYLOAD_PREVIEW_MAX = 500

#: description 上限（收件箱按 200 字符截断展示，本层先截好，避免关键信息被 UI 截掉）
_DESC_MAX = 200

#: 治理可回溯字段（**UI 硬规则**：审批气泡缺 undo_hint 不出现 ⇒ 人在收件箱里批不了）
#: 这就是"审批人看到的那句话"，因此**必须与实现语义逐句对应**（不是为过测试凑的空话）：
#:   - "批准本身不产生副作用、执行发生在模型原样重试时"
#:         ← 生效路径在闸门那侧：本层只挂单，不执行任何工具（见 ``agent/tool_gate.py``）；
#:   - "单次有效"        ← ``consume()`` 的 append-only 消费台账（同一张单只放行一次）；
#:   - "超时自动失效"    ← ``_within_ttl()`` 依据 ``CP_TOOL_APPROVAL_TTL_SEC``，锚点是
#:         **裁决时刻**（``_decision_time``＝``updated_at`` 优先；未决记录退回
#:         ``created_at``，见 ``_within_ttl`` 的 docstring——挂单很久之后才批的单
#:         必须仍然有效，否则人点了批准却什么也没发生）；**秒数在此处读实际生效值**
#:         （``_ttl_seconds()``），不写死；
#:   - "在该调用重试前驳回本单" ← 驳回后闸门返回 ``APPROVAL_REJECTED``，该次调用不放行。
#: 【同步契约】改了上面任一条（单次有效 / TTL / 驳回语义），**必须同时改这句话**。
#: 【2026-09-18 措辞修正】原句尾是"或等 TTL 自然过期"，那是**两处不实**：
#:   ① 对**待办**而言"等"并不撤销它（TTL 只管已批准的生效窗口，未决单过了 TTL 照样能被
#:      批准——见 ``_within_ttl`` 的锚点说明）；
#:   ② 陈旧待办由系统**惰性清理**（``_expire_stale_pending``，且标注为超时、不算人工否决），
#:      并非"等着就自动作废"。
#:   故改为只保留**真实可执行**的撤销路径（重试前驳回），不给人一个做不到的承诺。
_UNDO_HINT_TMPL = (
    "本次批准不产生副作用：执行发生在模型原样重试时，且单次有效、批准后超时自动失效"
    "（{env}={ttl} 秒，自批准时刻起算）。若要阻止本次调用，请在模型重试前驳回本单。")

#: 补偿动作**如实留空**：本层没有"工具的补偿动作"可用（那属于工具自己/其描述符），
#: 不臆造一句空话；气泡可见性由 ``undo_hint`` 单独满足（``bool(undo_hint or comp)``）。
_COMPENSATING_ACTION = ""

#: pending_snapshot 的 limit 钳制（与 routes_approval 的 200 口径一致）
_SNAPSHOT_LIMIT_MAX = 200
_SNAPSHOT_LIMIT_DEFAULT = 50

# ════════════════════════════════════════════════════════════
#  进程内缓存（消费集合 / 工具元数据 / 只读审批流实例）
# ════════════════════════════════════════════════════════════

#: 消费台账锁：保证并发下"同一 approval_id 只有一个 True"
_USES_LOCK = threading.RLock()
#: path → `(台账文件指纹, 已消费 approval_id 集合)`（按路径分开，避免测试换路径后串味）
#:
#: 【为什么缓存值里带指纹（2026-09-22 修）】原先只按**路径**缓存集合快照，而消费/审批
#: 发生在**另一个进程**（UI/CLI）里 ⇒ B 进程追写的消费行在本进程里永远不可见，
#: `consume()` 会在陈旧快照上把**同一张批准放行第二次**。做法与 `_read_flow` 同源：
#: `(mtime_ns, size)` 指纹一变就重读（覆盖/不覆盖的边界见 `_consumed_ids` 的说明）。
_USES_CACHE: Dict[str, Tuple[Optional[Tuple[int, int]], Set[str]]] = {}

#: 工具元数据缓存（``load_tool_meta()`` 要读全部工具 YAML，不能放进每次调用的热路径）
_META_LOCK = threading.Lock()
_META_CACHE: Optional[Dict[str, Any]] = None

#: 只读审批流实例缓存：(本次操作解析到的库路径取值, 记录文件路径, 文件指纹, flow)
_FLOW_LOCK = threading.Lock()
_READ_FLOW: Optional[Tuple[str, str, Tuple[int, int], Any]] = None


# ════════════════════════════════════════════════════════════
#  摘要
# ════════════════════════════════════════════════════════════

def tool_call_digest(tool: str, args: Optional[Mapping[str, Any]]) -> str:
    """``(工具名, 参数)`` 的稳定摘要：``sha256(f"{tool}\\n{canonical}")[:16]``

    canonical 用 ``json.dumps(..., sort_keys=True, separators=(",", ":"),
    ensure_ascii=False, default=str)`` ⇒ 键序无关、空参数（``None`` / ``{}``）稳定、
    不可序列化对象经 ``default=str`` 降级而不是抛异常。

    Returns:
        16 位十六进制摘要；**任何异常（含不可序列化到连 ``str`` 都失败）→ ``""``**。
        调用方必须把 ``""`` 视为"本次调用无法绑定审批"，**不得据此放行**。
    """
    try:
        canonical = json.dumps(
            args or {}, sort_keys=True, ensure_ascii=False,
            separators=(",", ":"), default=str)
        raw = f"{tool}\n{canonical}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    except Exception as e:  # noqa: BLE001 摘要算不出来 ⇒ ""（调用方不得放行）
        logger.warning("[tool_approval] 参数摘要计算失败（本次调用无法绑定审批）: %s: %s",
                       type(e).__name__, e)
        return ""


# ════════════════════════════════════════════════════════════
#  小工具（路径 / 时间 / 参数预览）
# ════════════════════════════════════════════════════════════

def _iso_now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _uses_path() -> Path:
    """消费台账路径（环境变量优先；读取异常 → 默认路径）"""
    try:
        raw = os.environ.get(ENV_USES_PATH)
    except Exception as e:  # noqa: BLE001 环境变量不可读 → 走默认路径
        logger.debug("[tool_approval] 台账路径环境变量不可读（用默认路径）: %s", e)
        raw = None
    text = str(raw).strip() if raw is not None else ""
    return Path(text) if text else _DEFAULT_USES_PATH


def _ttl_seconds() -> float:
    """批准时效（秒）：``CP_TOOL_APPROVAL_TTL_SEC`` ≥0 生效；非法 → 默认 900

    显式给 ``0`` 是**合法**取值（等价"任何批准都已过期"），便于测试与紧急关闭；
    负值/不可解析 → 回退默认并告警（负时效没有意义，静默当成 0 会变成"全部拒绝"）。
    """
    try:
        raw = os.environ.get(ENV_TTL_SEC)
    except Exception as e:  # noqa: BLE001 环境变量不可读 → 默认口径
        logger.debug("[tool_approval] 时效环境变量不可读（用默认 %ss）: %s",
                     _DEFAULT_TTL_SEC, e)
        return _DEFAULT_TTL_SEC
    text = str(raw).strip() if raw is not None else ""
    if not text:
        return _DEFAULT_TTL_SEC
    try:
        value = float(text)
    except (TypeError, ValueError):
        logger.warning("[tool_approval] %s=%r 不是数字（回退默认 %ss）",
                       ENV_TTL_SEC, text, _DEFAULT_TTL_SEC)
        return _DEFAULT_TTL_SEC
    if value < 0:
        logger.warning("[tool_approval] %s=%r 为负（回退默认 %ss）",
                       ENV_TTL_SEC, text, _DEFAULT_TTL_SEC)
        return _DEFAULT_TTL_SEC
    return value


def _ttl_text(ttl: float) -> str:
    """把 TTL 秒数格式化成人读文本（整数值不带 ``.0``；非数字/异常 → 原样字符串）"""
    try:
        value = float(ttl)
        if value == int(value):
            return str(int(value))
    except (TypeError, ValueError, OverflowError) as e:  # noqa: BLE001 格式化失败不影响挂单
        logger.debug("[tool_approval] TTL 格式化失败（原样输出）: %s: %s", ttl, e)
    return str(ttl)


def _build_undo_hint() -> str:
    """审批人看到的那句撤销说明（**TTL 秒数取当前实际生效值**，不写死）"""
    return _UNDO_HINT_TMPL.format(env=ENV_TTL_SEC, ttl=_ttl_text(_ttl_seconds()))


def _clip(text: str, limit: int) -> str:
    """截断到 ``limit`` 字符（超出时补省略号，结果**一定** ≤ limit）"""
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + "…"


def _raw_args_preview(args: Optional[Mapping[str, Any]]) -> str:
    """参数预览：只取白名单里的**可读**字段（如 ``command`` / ``query`` / ``path``）

    - 命中一个字段 ⇒ 直接给**值**（如 ``pytest -q tests/unit``，与契约示例同形）；
    - 命中多个字段 ⇒ ``key=value | key=value``（多值必须带键名，否则无法分辨谁是谁）；
    - 未命中 ⇒ ``共 N 个参数``（N 为参数个数；``None`` / ``{}`` ⇒ ``共 0 个参数``）。
    **绝不把整份参数原样倒出来**（可能含密钥、超长正文、二进制）。非标量值
      （dict/list/自定义对象）一律跳过，不做递归展开。
    """
    if args is None:
        return "共 0 个参数"
    if not isinstance(args, Mapping):
        return "共 0 个参数"
    found: List[Tuple[str, str]] = []
    for key in _PREVIEW_KEYS:
        if key not in args:
            continue
        value = args.get(key)
        if isinstance(value, bool) or isinstance(value, (int, float)):
            found.append((key, str(value)))
        elif isinstance(value, str) and value.strip():
            found.append((key, value.strip()))
    if len(found) == 1:
        return found[0][1]
    if found:
        return " | ".join(f"{key}={value}" for key, value in found)
    try:
        count = len(args)
    except Exception:  # noqa: BLE001 长度不可得 ⇒ 不影响描述生成
        count = 0
    return f"共 {count} 个参数"


def _tool_risk(tool: str) -> str:
    """工具风险等级：``data/tool_definitions/<tool>.yaml`` 的 ``risk`` 字段

    经**既有** ``agent.lines.models.load_tool_meta()`` 读取（不自己解析 YAML）。
    读不到该工具（未登记 / YAML 不可读 / 无 yaml 依赖）⇒ ``"unknown"``。
    写进 payload 的 ``risk`` 叶子字段后，审批收件箱的
    ``agent.security.approval_guard.resolve_risk`` 会直接把它显示出来（不改路由）。
    """
    name = str(tool or "").strip()
    if not name:
        return "unknown"
    metas = _tool_meta()
    if not metas:
        return "unknown"
    candidates = [name, name.lower()]
    for sep in (".", "/"):
        if sep in name:
            candidates.append(name.rsplit(sep, 1)[-1])
    for key in candidates:
        meta = metas.get(key)
        if meta is not None:
            risk = str(getattr(meta, "risk", "") or "").strip().lower()
            return risk or "unknown"
    logger.debug("[tool_approval] 工具 %s 无元数据（风险按 unknown 处理）", name)
    return "unknown"


def _tool_meta() -> Dict[str, Any]:
    """惰性加载并缓存工具元数据（失败 ⇒ 空 dict，不抛异常）

    Why 缓存：``load_tool_meta()`` 要读 ``data/tool_definitions/`` 下全部 YAML，
    而本函数在"需要审批的工具调用"这条路径上被调用 ⇒ 不缓存等于把上百次文件 IO
    放进热路径（与 ``tool_gate._tool_meta`` 同一取舍）。``data/tool_definitions``
    在运行期是静态的，故不按 mtime 失效。
    """
    global _META_CACHE
    if _META_CACHE is None:
        with _META_LOCK:
            if _META_CACHE is None:
                try:
                    from agent.lines.models import load_tool_meta
                    _META_CACHE = dict(load_tool_meta() or {})
                except Exception as e:  # noqa: BLE001 元数据不可得 ⇒ 风险 unknown
                    logger.warning("[tool_approval] 工具元数据加载失败"
                                   "（风险按 unknown 处理）: %s: %s",
                                   type(e).__name__, e)
                    _META_CACHE = {}
    return _META_CACHE


# ════════════════════════════════════════════════════════════
#  审批流实例（读：按文件指纹缓存；写：每次新建）
# ════════════════════════════════════════════════════════════

def _stamp(path: str) -> Optional[Tuple[int, int]]:
    """文件指纹 ``(mtime_ns, size)``；不可读 → ``None``（与 tool_gate 同策略）"""
    if not path:
        return None
    try:
        st = os.stat(path)
        return (int(st.st_mtime_ns), int(st.st_size))
    except Exception:  # noqa: BLE001 文件不存在/不可读 ⇒ 不缓存（每次重新探测）
        return None


def _records_path_hint() -> str:
    """本次操作要用的审批库路径（``APPROVAL_RECORDS_PATH`` 取值；空串 ⇒ 交给 ApprovalFlow 的默认口径）

    【为什么在操作入口解析一次并**显式传下去**】
        ``APPROVAL_RECORDS_PATH`` 是**进程级**环境变量，而本仓确有在运行期改写它的
        非 monkeypatch 写者（``agent/settings/resolver.py`` 的 ``os.environ[...] = ...``、
        ``import app_server`` 触发的 ``.env`` 重载 —— 见 ``tests/conftest.py`` 的记载）。
        若一次操作里的每一步都各自在调用点重读环境，同一个 ``request_approval`` 就可能
        "在 A 库挂单、去 B 库判幂等"，``consume`` 就可能"在 A 库查台账、往 B 库记一笔"。
        而"单次有效"与"最新裁决为准"这两条语义**都建立在同一个库这个前提上**：
        跨库的那一刻，台账不再能证明那张单用没用过，裁决也不再落在同一处。
        实测（2026-09-22）：瞬时改道后，"重新挂单并批准"那一步写进了另一只库，本库那张单
        仍停在 ``pending_review`` ⇒ "最新裁决"退化成更早那条驳回 = 人的操作被静默吃掉。
        故：**入口解析一次、全程显式传参**，让"一次操作对库的选择"是原子的。
    """
    try:
        raw = os.environ.get("APPROVAL_RECORDS_PATH")
    except Exception as e:  # noqa: BLE001 环境变量不可读 ⇒ 交给 ApprovalFlow 的默认口径
        logger.debug("[tool_approval] 审批记录路径环境变量不可读: %s", e)
        return ""
    return str(raw).strip() if raw is not None else ""


def _new_flow(path_hint: str = "") -> Any:
    """按**显式路径**构造审批流（``path_hint`` 为空 ⇒ 由 ``ApprovalFlow`` 自己读环境）"""
    from agent.skills_mgmt.approval import ApprovalFlow

    return ApprovalFlow(records_path=path_hint or None)


def _read_flow(path_hint: str = "") -> Any:
    """只读用的 ``ApprovalFlow``（按 ``(库路径, 文件指纹)`` 缓存）

    Why 每次都要能看见"外部刚批的那一条"：人是在**另一个进程**（UI/CLI）里审批的，
    而 ``ApprovalFlow`` 把记录 load 进内存后就不再回读文件。故实例只在**文件指纹
    未变**时复用；指纹一变（外部批准/新增记录）立即重建 ⇒ 读到最新状态。
    Why 缓存：审批记录文件是**只增**的，重建要整文件解析；不缓存等于把全量解析放进
    每次工具调用的热路径。
    Why 缓存键里带库路径取值：``ApprovalFlow()`` 的路径来自
    ``APPROVAL_RECORDS_PATH``。若只看文件指纹，换了库（测试隔离 / 多实例）却"指纹恰好
    相同"时会跨库返回旧实例——那是会读到**别的库的记录**的错误。带上库路径即可保证
    "换库立刻换实例"，同时命中缓存时**不必再构造** ``ApprovalFlow``
    （构造期在审批关闭时会打印醒目告警，热路径上不能按调用次数刷屏）。

    Args:
        path_hint: 本次操作在**入口**解析一次得到的库路径（见 :func:`_records_path_hint`）；
            空串 ＝ "环境未设置"，由 ``ApprovalFlow`` 走默认口径。
    """
    global _READ_FLOW

    key = str(path_hint or "")

    with _FLOW_LOCK:
        cached = _READ_FLOW
        if cached is not None and cached[0] == key and _stamp(cached[1]) == cached[2]:
            return cached[3]

    flow = _new_flow(path_hint)  # 构造不读文件（惰性 load），故可先建后取指纹
    path = str(getattr(flow, "_records_path", "") or "")
    stamp = _stamp(path)
    if stamp is None:
        # 文件还不存在/不可读：不做正缓存（下次仍重新探测，文件一出现即生效）
        return flow
    with _FLOW_LOCK:
        _READ_FLOW = (key, path, stamp, flow)
    return flow


def _write_flow(path_hint: str = "") -> Any:
    """写（提交审批单）用的 ``ApprovalFlow``：**每次新建**，并钉住本次操作解析到的库

    Why 不复用缓存实例：``submit()`` 是"读全文件 → 改内存 → 整文件重写"，复用一个
    陈旧实例会把外部刚写入的记录一起覆盖掉。新建实例把"读→写"的窗口压到最小。
    Why 传 ``path_hint``：见 :func:`_records_path_hint` —— 一次操作只认入口解析的那只库，
    不在调用点重读环境变量（否则运行期改道会把一次操作劈成两只库）。
    """
    return _new_flow(path_hint)


# ════════════════════════════════════════════════════════════
#  记录匹配（tool + digest + session + TTL）
# ════════════════════════════════════════════════════════════

def _valid_args(args: Any) -> bool:
    """参数是否合法（``None`` 与 Mapping 合法；其余一律视为非法，不猜）"""
    return args is None or isinstance(args, Mapping)


def _digest_of(tool: str, args: Any) -> str:
    """本次调用的摘要；工具名/参数非法或摘要算不出 → ``""``（＝无法绑定审批）"""
    name = str(tool or "").strip()
    if not name or not _valid_args(args):
        return ""
    return tool_call_digest(name, args)


def _payload_of(record: Any) -> Dict[str, Any]:
    payload = getattr(record, "payload", None)
    return payload if isinstance(payload, Mapping) else {}


def _session_key_of(record: Any) -> str:
    return str(_payload_of(record).get("session_key") or "")


def _session_matches(record: Any, session_key: str) -> bool:
    """会话匹配：记录侧空串＝通配；否则必须与本次 ``session_key`` 相等"""
    recorded = _session_key_of(record).strip()
    if not recorded:
        return True
    return recorded == str(session_key or "").strip()


def _within_ttl(record: Any) -> bool:
    """自**裁决时刻**起是否仍在 ``CP_TOOL_APPROVAL_TTL_SEC`` 内

    锚点取 :func:`_decision_time`（``updated_at`` 优先），**不是** ``created_at``：
    人工完全可能在挂单很久之后才点批准（排队、离线、夜里），若按创建时刻计时，那张
    "刚刚被批准"的单会在批准瞬间就被判过期 ⇒ **人点了批准却什么也没发生**（静默失效，
    最难排查的一类）。这条 TTL 的真实语义是"批准后 N 分钟内要用掉"，
    故锚点是人做决定的时间；未决记录没有裁决时刻，退回 ``created_at``
    （那是"这张待办挂了多久"，用于避免复用陈年待办）。

    解析失败 → ``False``（**保守**：时间不可信就不认这张批准）；未来时间戳同样不认。
    """
    raw = _decision_time(record)
    try:
        anchor = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        logger.warning("[tool_approval] 裁决/创建时间不可解析（视为过期）record=%s time=%r",
                       getattr(record, "record_id", ""), raw)
        return False
    age = (datetime.now() - anchor).total_seconds()
    ttl = _ttl_seconds()
    if age < 0:
        logger.warning("[tool_approval] 裁决时间在未来（视为无效）record=%s age=%.1fs",
                       getattr(record, "record_id", ""), age)
        return False
    return age < ttl


def _candidates(tool: str, digest: str, *, state: str, store: str = "") -> List[Any]:
    """取该工具下 ``state`` 的记录，按 created_at 降序（审批流已排好序）

    ``store`` ＝ 本次操作入口解析一次的库路径（见 :func:`_records_path_hint`）。
    """
    flow = _read_flow(store)
    records = flow.list({"object_type": OBJECT_TYPE, "object_id": tool,
                         "state": state})
    return [r for r in records if _payload_of(r).get("args_digest") == digest]


def _is_system_expiry(record: Any) -> bool:
    """该记录是否由**系统超时判定**产生（而非人工裁决）

    判据来自审批域本身的 :func:`agent.skills_mgmt.approval.is_timeout_deny`（单一来源，
    不在本层另写字符串比较——否则前缀一改就悄悄失效）。用途见 :func:`_latest_decision`：
    系统超时"既不是批准也不是驳回"，模型不该被它告知"人工已否决、别再重试"。
    """
    try:
        from agent.skills_mgmt.approval import is_timeout_deny  # noqa: PLC0415 惰性
        return bool(is_timeout_deny(record))
    except Exception as e:  # noqa: BLE001 判定不可得 ⇒ 按"不是超时"处理（从严：保留驳回）
        logger.debug("[tool_approval] 超时判定不可得（按非超时处理）: %s: %s",
                     type(e).__name__, e)
        return False


def _expire_stale_pending(store: str = "") -> None:
    """把 ``tool_call`` 名下**已过 TTL** 的待办判为系统超时（清出收件箱）

    【为什么是惰性清理而不是定时器】清理的自然时机就是"模型又发起一次需要审批的调用"：
    那一刻旧待办已无意义——同摘要的会被下面新挂的单取代，不同摘要的属于另一次调用、
    同样早过 TTL。加定时器要多一个后台线程与一处调度，收益只是"人不必在收件箱里
    看到一条早已过期的待办"。

    【为什么必须限定 ``object_type``】技能/提示词提案的合理待办时长不同
    （``expire_pending`` 默认阈值 86400s），用工具审批的 900s 去全表清理会**误伤**它们。
    故只清理 ``tool_call``（``expire_pending(object_type=...)``）。

    【为什么不算"人工否决"】清理产生的拒绝记录带 ``TIMEOUT_DENY_REASON_PREFIX``，
    被 :func:`_is_system_expiry` 识别 ⇒ 既不构成批准也不构成驳回，调用方会重新挂单。
    """
    try:
        flow = _write_flow(store)   # 状态迁移属于写操作，用写路径实例（每次新建，避免陈旧覆盖）
        expired = flow.expire_pending(
            older_than_seconds=_ttl_seconds(), object_type=OBJECT_TYPE,
            note="工具审批 TTL 到期，等待调用方重新发起（系统超时，非人工否决）")
        if expired:
            logger.info("[tool_approval] 清理陈旧待办 %d 条（object_type=%s, TTL=%ss）",
                        len(expired), OBJECT_TYPE, _ttl_seconds())
    except Exception as e:  # noqa: BLE001 清理失败不影响挂单（陈旧待办只是不好看）
        logger.debug("[tool_approval] 陈旧待办清理跳过: %s: %s", type(e).__name__, e)


# ════════════════════════════════════════════════════════════
#  消费台账（append-only）
# ════════════════════════════════════════════════════════════

def _load_uses(path: Path) -> Set[str]:
    """读消费台账 → 已消费的 ``approval_id`` 集合（文件不存在 → 空集）

    单行损坏 → 跳过并告警（与 ``skills_mgmt.approval._read_jsonl`` 同策略），
    不让一行垃圾把整张台账废掉。
    """
    consumed: Set[str] = set()
    try:
        if not path.exists():
            return consumed
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    logger.warning("[tool_approval] %s 跳过损坏行（消费台账）", path)
                    continue
                if isinstance(obj, dict):
                    aid = str(obj.get("approval_id") or "").strip()
                    if aid:
                        consumed.add(aid)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[tool_approval] 消费台账读取失败（按空集处理）: %s: %s", path, e)
    return consumed


def _consumed_ids(path: Path) -> Set[str]:
    """**给定**台账路径的已消费集合（按 ``(路径, 文件指纹)`` 缓存；**不落盘、不删文件**）

    【为什么由调用方把路径传进来】``consume()`` 必须"查台账"与"记台账"用**同一个**路径：
    台账路径同样是进程级环境变量（可被运行期改写），两次各自去读环境就可能"在 A 台账判重、
    往 B 台账记一笔" ⇒ 单次有效被绕过。故路径在 ``consume`` 入口解析一次，显式传进来。

    【为什么缓存必须按**文件指纹**失效（2026-09-22 修）】
        人是在**另一个进程**（UI/CLI）里审批并消费的，而本函数原先只按**路径**缓存集合
        快照：本进程一旦读过一次台账，B 进程此后追写的消费行就**永远不会**被本进程看到
        ⇒ :func:`consume` 会在陈旧快照上判"这张单没用过"，把**同一张批准放行第二次**
        （"单次有效"在多进程下失效，且台账里同一个 approval_id 出现两行）。
        修前实测形态::

            先读一次空台账 -> set()          # 连"文件不存在"都被缓存成空集
            外部进程追写一行 approval_id=X
            本进程再问     -> set()          # 看不到（缺口）
            _is_consumed(X) -> False         # 判"没用过"（缺口）
            consume(X, ...) -> True          # 放行第二次（缺口成立）

        故与 :func:`_read_flow` **同源**：用 :func:`_stamp` 的 ``(mtime_ns, size)`` 指纹
        判"外部是否改过文件"，**只在指纹变了才重读**。原缓存的唯一动机（"不把全量解析放进
        每次工具调用的热路径"——``_is_consumed`` 挂在 ``find_permission`` 上）因此不变：
        指纹未变时只多一次 ``os.stat``，一次解析都不多做。

    【本修复覆盖什么 / 不覆盖什么（如实说明，不宣称"彻底解决"）】
        - **覆盖**：外部进程**追加**。台账是 append-only 的，追加必然改变字节数，而
          ``size`` 是指纹的一半 ⇒ 即便文件系统的 mtime 精度很粗（同一个 mtime 刻度内
          追加），也一定被察觉（回归用例：
          ``test_消费_即使mtime被拨回原值_外部追加照样被看到``）。
        - **覆盖**：文件不存在/不可读时**不缓存**该状态（"按未消费处理"这条既有语义
          **不变**），下一次调用重新探测 ⇒ 外部进程**首次**消费（它会把台账文件创建
          出来）立刻生效。运行期绝大多数时间台账并不存在，这是最容易踩中的一种形态。
        - **不覆盖**：外部进程把台账**重写成与旧内容等长**（``size`` 不变）**且**落在
          同一个 mtime 刻度内的改写——指纹两半都没动，本进程仍会拿旧快照。本层台账
          只增不改，正常路径不会出现"等长重写"；真出现（人工手改 / 外部工具重排台账）
          时本进程看不到，须重启进程或调用 :func:`reset_cache`。
        - **不覆盖**：**跨进程并发**消费同一张单。指纹缓存只保证"下一次问能看见**已经
          落盘**的事实"；两个进程在同一窗口里各自"查台账 → 追加"仍可能都判"没用过"
          （本层不提供跨进程锁）。这是与"单次有效"相邻的另一条边界，不计入本修复。
    """
    key = str(path)
    with _USES_LOCK:
        cached = _USES_CACHE.get(key)
        # 先取指纹、后读内容：反过来（先读内容再取指纹）会把**更晚的**指纹配到**更早的**
        # 内容上，正好掩盖"读与取指纹之间外部刚追加过"这件事；当前顺序下最坏只是多读一次。
        stamp = _stamp(key)
        if cached is not None and stamp is not None and cached[0] == stamp:
            return cached[1]
        if stamp is None:
            # 文件不存在/不可读：按"未消费"处理（**既有语义，不改**），但**不缓存**该状态
            # —— 缓存了它，"文件一出现即生效"就永远兑现不了（见上面"覆盖什么"第 2 条）。
            _USES_CACHE.pop(key, None)
            return _load_uses(path)
        fresh = _load_uses(path)
        _USES_CACHE[key] = (stamp, fresh)
        return fresh


def _append_use(path: Path, entry: Dict[str, Any]) -> bool:
    """追加一行消费记录（append-only）；失败 → ``False``（**不消费**）"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, ValueError) as e:  # noqa: BLE001 fsync 不可用不致命
                logger.debug("[tool_approval] fsync 失败（已 flush）: %s", e)
        return True
    except Exception as e:  # noqa: BLE001 写不进去 ⇒ 不认为"已消费"（不放行）
        logger.warning("[tool_approval] 消费台账写入失败（本次不消费）: %s: %s",
                       type(e).__name__, e)
        return False


def _is_consumed(approval_id: str) -> bool:
    aid = str(approval_id or "").strip()
    if not aid:
        return False
    try:
        return aid in _consumed_ids(_uses_path())
    except Exception as e:  # noqa: BLE001 台账不可读 ⇒ 保守按"已消费"处理
        logger.warning("[tool_approval] 消费状态不可判定（按已消费处理）: %s: %s",
                       type(e).__name__, e)
        return True


# ════════════════════════════════════════════════════════════
#  对外 API
# ════════════════════════════════════════════════════════════

def request_approval(tool: str, args: Optional[Mapping[str, Any]], *,
                     reason: str = "", session_key: str = "",
                     source: str = "") -> Dict[str, Any]:
    """为该次调用挂一张人工审批单（**幂等**：同一次调用不重复挂单）

    【幂等判据】同 ``tool`` + 同 ``args_digest`` + 同 ``session_key`` 且
    ``state == "pending_review"`` ⇒ 复用其 ``record_id``（``reused=True``）；
    **其余一切情况（含 ``approved``）都新建一张待审单**。
    这条规则是防"模型重试把审批收件箱刷爆"的——一次真实调用可能被重试几十次，
    每次都挂单会让人面对几十条一模一样的待办。

    【为什么复用必须**只看未决单**，而不能连 ``approved`` 一起复用】
        ``consume()`` **不改审批流记录**（审批流是决策权威，消费只写 append-only
        台账），所以一张"已批准且已放行过一次"的单在审批记录里**仍然是 ``approved``**。
        若也复用 ``approved``，返回的就是一张**死单**：
          - 收件箱只列 ``pending_review`` ⇒ 人工**看不到任何待办、无从点击**；
          - ``approved`` 在审批状态机里不能再被批准一次（只能 → merged/archived）
            ⇒ 这张单**永远兑现不了**。
        两者相加 = 该次调用**永久卡死**（真实链路缺陷，非测试洁癖）。故复用只认
        "还挂在收件箱里等人点"的未决单；``approved``（无论是否已消费 / 是否过期）、
        ``rejected`` / ``archived`` / ``merged`` 一律另挂新单，让人重新看到一条待办。

    Args:
        tool: 工具名（``object_id``）。
        args: 本次调用参数（只用于派生摘要与**脱敏预览**，绝不原样落盘进描述）。
        reason: 为什么要调用（可选，落 payload；不进 description）。
        session_key: 会话标识（落 payload；记录侧为空串时在匹配阶段通配）。
        source: 调用来源（可选，落 payload，便于审计区分链路）。

    Returns:
        ``{"ok": True, "approval_id": str, "reused": bool, "state": str}``
        或 ``{"ok": False, "error": str}``。**绝不抛异常。**
    """
    try:
        name = str(tool or "").strip()
        if not name:
            logger.warning("[tool_approval] 审批请求缺少工具名（结构化失败）")
            return {"ok": False, "error": "工具名不能为空"}
        if not _valid_args(args):
            logger.warning("[tool_approval] 工具 %s 的参数不是映射（结构化失败）: %s",
                           name, type(args).__name__)
            return {"ok": False, "error": "参数必须是映射（Mapping）或 None"}
        digest = tool_call_digest(name, args)
        if not digest:
            return {"ok": False, "error": "参数摘要计算失败（无法绑定审批）"}

        skey = str(session_key or "")
        # 先把**已过 TTL** 的 tool_call 待办判为系统超时清出收件箱（见 _expire_stale_pending：
        # 惰性清理，只动本对象类型，且不算"人工否决"），再判幂等复用。
        # 顺序有讲究：先清理 ⇒ 陈旧待办不会既留在收件箱里、又拦住下面新挂的单。
        store = _records_path_hint()   # 一次操作只解析一次（见 _records_path_hint）
        _expire_stale_pending(store)
        flow = _read_flow(store)
        existing = _find_reusable(flow, name, digest, skey)
        if existing is not None:
            logger.debug("[tool_approval] 复用既有审批单 record=%s tool=%s state=%s",
                         existing.record_id, name, existing.state)
            return {"ok": True, "approval_id": existing.record_id, "reused": True,
                    "state": existing.state}

        risk = _tool_risk(name)
        preview = _raw_args_preview(args)
        description = _build_description(name, risk, preview, digest)
        payload = {
            "tool": name,
            "args_digest": digest,
            "args_preview": _clip(preview, _PAYLOAD_PREVIEW_MAX),
            "session_key": skey,
            "source": str(source or ""),
            "reason": str(reason or ""),
            "risk": risk,
            "requested_at": _iso_now(),
            # 治理可回溯（UI 气泡硬规则：缺 undo_hint 则收件箱不出气泡、人批不了）
            "undo_hint": _build_undo_hint(),
            "compensating_action": _COMPENSATING_ACTION,
        }
        record = _write_flow(store).submit(
            OBJECT_TYPE, name, action=ACTION, description=description,
            payload=payload, actor=ACTOR, trigger=TRIGGER)
        logger.info("[tool_approval] 工具调用待审批 record=%s tool=%s risk=%s "
                    "digest=%s session=%s",
                    record.record_id, name, risk, digest,
                    skey or "-（通配）")
        return {"ok": True, "approval_id": record.record_id, "reused": False,
                "state": record.state}
    except Exception as e:  # noqa: BLE001 挂单失败绝不上抛（调用方按"未获批准"处理）
        logger.warning("[tool_approval] 挂审批单失败（结构化失败）: %s: %s",
                       type(e).__name__, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _find_reusable(flow: Any, tool: str, digest: str, session_key: str) -> Any:
    """既有**未决**的审批单（只认 ``pending_review``）

    幂等的目标只有一个：**别为同一次未决请求重复挂单**（模型重试几十次不该在收件箱里
    堆几十条一模一样的待办）。判据因此只能是"这张单**还挂在收件箱里等人点**"。

    【关键：已裁决的单一律不复用，不管它是否被消费过】
        ``consume()`` **不改审批流记录**——审批流是决策权威，消费只在 append-only
        台账里记一笔（见模块 docstring）。所以一张已批准且已消费的单，在审批记录里
        **仍然是 ``approved``**；若按"state ∈ {pending_review, approved} 就复用"的规则
        复用，返回的是一张**兑现不了**的单号（``approved`` 在状态机里不能再被批准一次：
        只能 → merged/archived），而收件箱只列 ``pending_review`` ⇒ **人工看不到任何
        待办、无从点击**，这次调用永远拿不到新批准 = **死路**（真实链路，不是测试洁癖）。
        同理，``approved`` 但已过期（TTL 自**裁决时刻**起算，见 ``_within_ttl``）、
        ``rejected`` / ``archived`` / ``merged`` 也都是"已裁决"，一律另挂新单，
        让人重新看到一条待办。
    """
    for state in _REUSE_STATES:
        try:
            records = flow.list({"object_type": OBJECT_TYPE, "object_id": tool,
                                 "state": state})
        except Exception as e:  # noqa: BLE001 读失败 ⇒ 无可复用（另挂新单更安全）
            logger.warning("[tool_approval] 复用判定读取 %s 记录失败: %s: %s",
                           state, type(e).__name__, e)
            continue
        for record in records:
            if _payload_of(record).get("args_digest") != digest:
                continue
            if not _session_matches(record, session_key):
                continue
            return record
    return None


def _build_description(tool: str, risk: str, preview: str, digest: str) -> str:
    """收件箱里人要读的那一行（≤200 字符，工具名与风险必在开头，不被截断吃掉）"""
    text = (f"工具 {tool} 请求执行（风险 {risk}）："
            f"{_clip(preview, _DESC_PREVIEW_MAX)}（参数摘要 {digest[:6]}…）")
    return _clip(text, _DESC_MAX)


def find_permission(tool: str, args: Optional[Mapping[str, Any]], *,
                    session_key: str = "") -> Optional[Dict[str, Any]]:
    """本次调用是否已有**有效**的人工批准（判定完全基于"最新一次裁决"）

    判定链（**唯一权威** :func:`_latest_decision`，不在本函数里另做时间比较）：
      1. 取 ``(tool, args_digest, session_key)`` 这一"待决事项"上**最新一次裁决**
         （``approved`` / ``rejected`` 里裁决时刻最新的那条，且须在 TTL 内）；
      2. 它必须是 ``approved``（**不是** ``merged``）——最新的裁决若是 ``rejected``，
         本函数直接 ``None``（"老的批准不能压过更新的驳回"）；
      3. 该批准**未被消费过**（``consume()`` 台账）。

    Returns:
        ``{"approval_id": str, "decided_by": str, "decided_at": str, "digest": str}``
        或 ``None``。**绝不抛异常**（异常一律 ``None`` ⇒ 不放行）。
    """
    try:
        name = str(tool or "").strip()
        digest = _digest_of(name, args)
        if not name or not digest:
            return None
        record = _latest_decision(name, digest, session_key, _records_path_hint())
        if record is None:
            return None
        if str(getattr(record, "state", "") or "") != "approved":
            logger.debug("[tool_approval] 最新裁决是 %s（非批准）record=%s tool=%s",
                         getattr(record, "state", ""), record.record_id, name)
            return None
        if _is_consumed(record.record_id):
            return None
        logger.debug("[tool_approval] 命中有效批准 record=%s tool=%s",
                     record.record_id, name)
        return {
            "approval_id": record.record_id,
            "decided_by": str(getattr(record, "actor", "") or ""),
            "decided_at": _decision_time(record),
            "digest": digest,
        }
    except Exception as e:  # noqa: BLE001 判定失败 ⇒ 无批准（**不放行**）
        logger.warning("[tool_approval] 批准判定失败（按未获批准处理）: %s: %s",
                       type(e).__name__, e)
        return None


def consume(approval_id: str, tool: str,
            args: Optional[Mapping[str, Any]]) -> bool:
    """把这**一张**批准标记为已用（单次有效）

    Returns:
        ``True`` ＝本次成功消费（调用方可放行）；
        ``False`` ＝此前已被消费过（**不得再次放行**）或台账写入失败
        （写不进去就不认账——宁可拒绝一次工具调用，也不放行一次无法留痕的执行）。
    """
    try:
        aid = str(approval_id or "").strip()
        name = str(tool or "").strip()
        if not aid or not name:
            logger.warning("[tool_approval] 消费请求缺少 approval_id/工具名: aid=%r tool=%r",
                           approval_id, tool)
            return False
        digest = _digest_of(name, args)
        if not digest:
            logger.warning("[tool_approval] 消费请求无法派生摘要（不消费）aid=%s tool=%s",
                           aid, name)
            return False
        path = _uses_path()   # 入口解析一次：查重与记账必须落在同一本台账
        entry = {"approval_id": aid, "tool": name, "args_digest": digest,
                 "consumed_at": _iso_now()}
        with _USES_LOCK:
            consumed = _consumed_ids(path)
            if aid in consumed:
                logger.warning("[tool_approval] ⛔ 批准已被消费过（不可重复放行）"
                               "approval_id=%s tool=%s", aid, name)
                return False
            if not _append_use(path, entry):
                return False
            # 写成功后再更新进程内集合。台账文件此刻已变（追加必然改变字节数）⇒ 这条
            # 缓存记录的指纹即刻作废，下一次 ``_consumed_ids`` 会重读文件（而不是把
            # 陈旧快照当权威）——见 ``_consumed_ids`` 的"先取指纹、后读内容"说明。
            consumed.add(aid)
        logger.info("[tool_approval] 批准已消费（单次有效）approval_id=%s tool=%s digest=%s",
                    aid, name, digest)
        return True
    except Exception as e:  # noqa: BLE001 消费失败 ⇒ False（不放行）
        logger.warning("[tool_approval] 消费失败（按未消费成功处理，不放行）: %s: %s",
                       type(e).__name__, e)
        return False


def _decision_time(record: Any) -> str:
    """一条记录的**裁决时刻**（``updated_at`` 优先——状态迁移时会刷新，即"人做决定的时间"）

    空值回落到 ``created_at``；两者都空 → 空串（比较时排最后）。
    """
    updated = str(getattr(record, "updated_at", "") or "")
    return updated or str(getattr(record, "created_at", "") or "")


def _decision_key(record: Any) -> Tuple[str, str]:
    """裁决先后序 ``(裁决时刻, record_id)``

    Why 要拿 ``record_id`` 兜底：审批记录的时间戳**只精确到秒**
    （``ApprovalRecord`` 用 ``isoformat(timespec="seconds")``），同一秒内的两次裁决
    会打平；而 ``record_id`` 形如 ``appr-<YYYYmmddHHMMSSffffff>-<hex>``，**自带微秒级
    创建时刻**且定宽，字典序即先后序。于是"后建的那张单上的裁决"在同一秒内也能判出先后
    （实际场景是"先驳回、再重新挂单并批准"，两次操作隔了秒级以上，兜底只为同一秒的边界）。
    """
    return (_decision_time(record), str(getattr(record, "record_id", "") or ""))


def _latest_decision(tool: str, digest: str,
                     session_key: str, store: str = "") -> Optional[Any]:
    """同一"待决事项"上**最新的一次裁决**（``approved`` / ``rejected`` 里最新的那条）

    【单一权威口径】"最新裁决为准"这条规则**只在这里实现一次**：
    :func:`find_permission` 与 :func:`is_rejected` 都只基于本函数的返回值分支，
    **各自不再做时间比较**——两处比较必然会出现不对称（实测过：旧驳回压过新批准，
    而 `find_permission` 又拿老批准无视新驳回），那是"人工的操作被静默忽略"的温床。

    "同一待决事项" = ``tool`` + ``payload["args_digest"]`` + 会话匹配
    （记录侧 ``session_key`` 为空串＝通配）。只在 **TTL 内**的记录里挑：
    过期裁决不参与（陈旧决定不得压制新请求）。

    排序依据 = :func:`_decision_key`（**裁决时刻**优先，非创建时刻）：
      - 语义上"最新裁决"是**人做决定的时间**（``updated_at`` 在状态迁移时刷新）；
        若按创建时刻排序，"先建的单被更晚决定"这一真实时序会被判反
        （实测用例：``test_最新裁决以决定时刻为准_后决定的旧单胜出``）；
      - ``ApprovalRecord`` 的时间戳**只到秒**，同秒并列时用 ``record_id`` 兜底
        （它自带微秒级创建时刻）；
      - **不依赖** ``ApprovalFlow.list()`` 的返回顺序（它按 ``created_at`` 降序，同秒并列
        时顺序无保证）——这里显式取最大值。

    【**系统超时判定不参与**】``ApprovalFlow.expire_pending()`` 会把没人理的待办判为
    ``rejected``（复用既有状态机与 §6.1「超时 Deny=2」计数），但那是**系统清理**、
    不是"有人否决"：把它当成最新裁决，模型会被告知"人工已否决、别再重试"——
    而实际从没有人看过那张单（2026-09-18 补）。故这类记录（``is_timeout_deny``）
    在**候选阶段就被剔除**：它既不构成批准、也不构成驳回，本次调用会重新挂一张新待办。
    剔除发生在唯一的候选收集处，故 :func:`find_permission` 与 :func:`is_rejected`
    **自动同口径**（不会一处认、一处不认）。
    """
    latest: Optional[Any] = None
    latest_key: Optional[Tuple[str, str]] = None
    for state in _DECIDED_STATES:
        for record in _candidates(tool, digest, state=state, store=store):
            if _is_system_expiry(record):
                continue
            if not _session_matches(record, session_key):
                continue
            if not _within_ttl(record):
                continue
            key = _decision_key(record)
            if latest_key is None or key > latest_key:
                latest, latest_key = record, key
    if latest is not None:
        logger.debug("[tool_approval] 最新裁决 tool=%s digest=%s -> %s(%s) @%s",
                     tool, digest, getattr(latest, "record_id", ""),
                     getattr(latest, "state", ""), _decision_time(latest))
    return latest


def is_rejected(tool: str, args: Optional[Mapping[str, Any]], *,
                session_key: str = "") -> Optional[Dict[str, Any]]:
    """本次调用是否已被人工**驳回**（让模型别再重试，与 pending 区分）

    判定链（**唯一权威** :func:`_latest_decision`，与本函数的同级函数
    :func:`find_permission` 完全同源同口径）：
      1. 取 ``(tool, args_digest, session_key)`` 上**最新一次裁决**（TTL 内）；
      2. 它是 ``rejected`` ⇒ 命中（返回驳回理由）；是 ``approved`` 或还没有裁决 ⇒ ``None``。
    即：**最新裁决优先**——人工先误驳、随后重新挂单并批准，新的批准必须生效
    （否则旧驳回会一直挡在闸门前面，模型被明确告知"不要再重试"，
    而人以为自己已经批了 = 审批人的操作被静默忽略）。反向同理：更新的驳回压过老的批准。
    驳回不存在"消费"语义，故不判消费台账。

    Returns:
        ``{"approval_id": str, "reason": str, "decided_at": str}`` 或 ``None``。
    """
    try:
        name = str(tool or "").strip()
        digest = _digest_of(name, args)
        if not name or not digest:
            return None
        record = _latest_decision(name, digest, session_key, _records_path_hint())
        if record is None:
            return None
        if str(getattr(record, "state", "") or "") != "rejected":
            logger.debug("[tool_approval] 最新裁决是 %s（非驳回）record=%s tool=%s",
                         getattr(record, "state", ""), record.record_id, name)
            return None
        return {
            "approval_id": record.record_id,
            "reason": str(getattr(record, "decision_reason", "") or ""),
            "decided_at": _decision_time(record),
        }
    except Exception as e:  # noqa: BLE001 判定失败 ⇒ 按"未被驳回"
        logger.warning("[tool_approval] 驳回判定失败（按未被驳回处理）: %s: %s",
                       type(e).__name__, e)
        return None


def pending_snapshot(limit: int = _SNAPSHOT_LIMIT_DEFAULT) -> Dict[str, Any]:
    """只读快照（排查/测试用）：本层挂出的全部 ``tool_call`` 审批单

    列出 ``object_type == "tool_call"`` 的记录（含终态，条目里的 ``state`` 自解释），
    按 ``created_at`` 降序取前 ``limit`` 条（钳制到 ≤200）。
    **只读**：不触发任何状态迁移、不消费、不写文件。

    Returns:
        ``{"ok": True, "count": n, "items": [{approval_id, tool, args_preview,
        session_key, created_at, state, decision_reason}, ...]}``
        或 ``{"ok": False, "error": str}``。
    """
    try:
        try:
            size = int(limit)
        except (TypeError, ValueError):
            logger.warning("[tool_approval] limit=%r 非法（回退 %d）",
                           limit, _SNAPSHOT_LIMIT_DEFAULT)
            size = _SNAPSHOT_LIMIT_DEFAULT
        size = max(0, min(size, _SNAPSHOT_LIMIT_MAX))
        records = _read_flow(_records_path_hint()).list(
            {"object_type": OBJECT_TYPE}, limit=size)
        items: List[Dict[str, Any]] = []
        for record in records:
            payload = _payload_of(record)
            items.append({
                "approval_id": str(getattr(record, "record_id", "") or ""),
                "tool": str(getattr(record, "object_id", "") or ""),
                "args_preview": str(payload.get("args_preview") or ""),
                "session_key": str(payload.get("session_key") or ""),
                "created_at": str(getattr(record, "created_at", "") or ""),
                "state": str(getattr(record, "state", "") or ""),
                "decision_reason": str(getattr(record, "decision_reason", "") or ""),
            })
        return {"ok": True, "count": len(items), "items": items}
    except Exception as e:  # noqa: BLE001 快照失败 ⇒ 结构化失败
        logger.warning("[tool_approval] 待审快照读取失败（结构化失败）: %s: %s",
                       type(e).__name__, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def reset_cache() -> None:
    """清空进程内缓存（**仅供测试隔离使用**）；不删除任何文件

    清三样：已消费集合缓存、工具元数据缓存、只读审批流实例缓存。
    下一次调用会重新从磁盘读台账与审批记录 ⇒ 用来模拟"换进程重来"。
    """
    global _META_CACHE, _READ_FLOW
    with _USES_LOCK:
        _USES_CACHE.clear()
    with _META_LOCK:
        _META_CACHE = None
    with _FLOW_LOCK:
        _READ_FLOW = None
    logger.debug("[tool_approval] 进程内缓存已清空（测试隔离）")


__all__ = [
    "OBJECT_TYPE", "ACTION", "TRIGGER", "ACTOR",
    "ENV_TTL_SEC", "ENV_USES_PATH",
    "tool_call_digest", "request_approval", "find_permission", "consume",
    "is_rejected", "pending_snapshot", "reset_cache",
]
