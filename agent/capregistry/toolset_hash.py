"""工具集 hash 与「hash 变化 ⇒ 重建会话」（v1.4 §11；TASK-08 E7）

## hash 范围（v1.4 §11 逐项，全部纳入）

| # | 维度 | 取值来源 | 为什么在这个范围里 |
|---|---|---|---|
| 1 | 工具名 | `CapabilityRecord.tool_name` | 名字变了就是另一个工具（工具体是**集合**，名字是主键） |
| 2 | 版本 | `CapabilityRecord.version` | 同名不同版是**不同契约**；不纳入就无法感知升级 |
| 3 | `input_schema` | `CapabilityRecord.input_schema`（规范化后） | 模型据它生成参数；变了必须让模型重新看到 |
| 4 | `description` | `CapabilityRecord.description` | 模型据它选择工具；变了等于"工具语义"变了 |
| 5 | `llm_visible` | 见 :func:`llm_visible_of` | 从"看得见"变"看不见"（或反之）是最强的一次变化 |
| 6 | 权限 | `permission_level` + `needs_approval` + `risk` + `confirm_level` | 同一工具从"免确认"变"逐次确认"，模型与闸门的交互完全不同 |
| 7 | 模型能力 | `agent.capregistry.modelcaps.model_capability(model)` | 不支持 tool calling 的模型**根本没有工具通道**（裁剪为 0），与支持时是两个世界 |

## 🔴 明确排除：健康状态（`health`）—— v1.4 §11 原文要求

> **健康频繁变化不触发重建，仅过滤注入。**

**为什么 health 必须排除（这是本模块最容易做错的一点）**：

1. **变化频率量级不同**。契约面（上表 7 项）只在**发布/装配**时变，一天可能 0 次；
   `health` 是**运行时探针**产物，一次网络抖动、一次 429、一个上游 5xx 就会让
   某个能力在 `healthy ↔ unhealthy` 之间来回跳。若把 health 放进 hash，
   **每一次健康抖动都会让 hash 变化 ⇒ 触发"自动新建会话"** ——
   用户会看到会话被无端清空、上下文丢失，而工具集契约其实一个字都没改。
   `tests/unit/test_toolset_hash.py::TestHealthNeverRebuilds` 把这条钉死。

2. **语义归属不同**。hash 回答的是"**模型看到的工具契约**变了吗"（决定要不要
   重建会话让模型重新读到契约）；health 回答的是"这个能力**此刻**能不能用"
   （决定这一轮**过滤/注入**哪些工具）。前者是**会话生命周期**问题，
   后者是**单轮渲染**问题 —— 混在一起就是用会话重建去解决一个每轮都该解决的问题。

3. **可回滚性不同**。契约变了不可回退（模型必须重新读）；health 下一轮可能自己好。
   让"会自动好的东西"触发"不可逆的会话重建"是纯粹的损失。

**因此 health 的处理方式**：进 :attr:`ToolsetSnapshot.health`（**快照但不参与 hash**），
并只通过 :attr:`RebuildDecision.filtered_out` / `health_changed` 影响**过滤与注入**。

## 其余被排除的字段（如实列举，避免"看起来什么都算了"）

| 字段 | 为什么不进 hash |
|---|---|
| `health` | 见上（v1.4 §11 明文排除） |
| `impl_status` / `impl_status_reason` | 同一等的运行时可用性事实（"在册但是坏的"），与 health 同类：用于**提示**，不用于重建 |
| `host_executor` | 执行面接线细节，模型看不到 |
| `location` / `location_reason` / `location_source` | 执行边界（超时/SSRF/沙箱）是**执行侧重**的约束，不进模型可见契约；如需纳入，改 `HASHED_FIELDS` + `entry_of()` 一处即可（扩展点） |
| `mark` / `reachable` 之外的 `main_line_status` | 主线装配的派生痕迹，随装配策略变（非契约） |
| `spec_source` / `declared_in` | **来源标记**，不是能力内容（降级构建时它会变，但那不该重开会话） |
| `enabled` | 由清单派生；不可用能力由 `llm_visible` 与 health 过滤表达，避免同一事实两个口径 |

## 同 session 内缓存

`ToolsetSnapshot` 的构建要序列化全部能力的 schema（实测 114 条量级为毫秒级，
见 `scripts/bench_capregistry.py`），而它**每次** `/capabilities/tools` 都会被问一次。
故 :class:`SessionToolset` 以 `(源对象身份, model)` 为键缓存快照：同一
`CapabilityRegistry`（**构造后不可变**，见其 docstring）在同一 model 下只会算一次；
换了 Registry 实例（数据侧重建）或换了 model，缓存自然失效。

## 回滚

本模块是**纯派生 + 纯内存**：不被导入即等于不存在（与 `agent/capregistry`
其余模块同一纪律），无开关、无落盘、无写路径。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .modelcaps import model_capability

__all__ = [
    "HASHED_FIELDS",
    "EXCLUDED_FIELDS",
    "RebuildDecision",
    "ToolsetSnapshot",
    "SessionToolset",
    "build_entries",
    "canonical",
    "compute_toolset_hash",
    "describe_hash_scope",
    "diff_entries",
    "entry_of",
    "hash_of_entries",
    "llm_visible_of",
]

#: hash 覆盖的 7 个维度（v1.4 §11；**顺序即文档顺序**，不参与计算）
HASHED_FIELDS: Tuple[str, ...] = (
    "name", "version", "input_schema", "description",
    "llm_visible", "permission", "model_capability",
)

#: 显式排除的字段 → 理由（**health 排在第一位**：v1.4 §11 点名的那一条）
EXCLUDED_FIELDS: Mapping[str, str] = {
    "health": ("运行时探针状态。v1.4 §11：**健康频繁变化不触发重建，仅过滤注入** —— "
               "它是每轮都可能变的可用性事实，不是会话级的工具契约；"
               "纳入 hash 会让每一次健康抖动都清空会话"),
    "impl_status": "运行时可用性事实（在册但实现为空壳/依赖缺失）；与 health 同级，只用于提示",
    "impl_status_reason": "同上（证据文本）",
    "host_executor": "执行面接线细节，模型看不到",
    "location": "执行边界（超时/SSRF/沙箱）约束的是执行侧，不进模型可见契约（扩展点见模块 docstring）",
    "location_reason": "同上",
    "location_source": "同上",
    "location_declared": "同上",
    "location_confidence": "同上",
    "mark": "可调用性判定标记（装配期派生），非契约内容",
    "main_line_status": "主线装配的派生痕迹，随装配策略变",
    "spec_source": "来源标记（authority/snapshot）：降级构建会改它，但那不该重开会话",
    "declared_in": "声明文件路径（来源标记）",
    "enabled": "由清单派生；可用性由 llm_visible 与 health 两条口径表达，避免同一事实两处判定",
}


# ════════════════════════════════════════════════════════════
#  规范化（**这是"hash 稳定"的全部秘密**）
# ════════════════════════════════════════════════════════════


def canonical(obj: Any) -> Any:
    """把任意 JSON-ish 值规范成**与容器顺序无关**的形态

    【为什么必须有它】Python 的 `dict` **保序**、`set`/`frozenset` **无序且哈希随机化**
    （`PYTHONHASHSEED` 每进程不同）。若直接 `json.dumps(schema)`，则：
      · 同一个 schema 只要键的**插入顺序**不同（例如 YAML 里调换两行、或两份
        等价 schema 由不同代码路径构造）⇒ 序列化文本不同 ⇒ **hash 不同** ⇒
        假重建；
      · `set` 的字面量迭代顺序跨进程不同 ⇒ **同一份数据在两个进程里 hash 不同** ⇒
        重建判定不可复现。
    故这里统一：`dict` → 按键排序；`set/frozenset` → 先转 list 再按规范串排序；
    `list/tuple` → **保序**（数组顺序是 JSON Schema 的语义，`required` / `enum`
    的顺序对模型有意义，不能排序）。
    """
    if isinstance(obj, Mapping):
        return {str(k): canonical(obj[k]) for k in sorted(obj.keys(), key=str)}
    if isinstance(obj, (set, frozenset)):
        items = [canonical(x) for x in obj]
        return sorted(items, key=lambda x: json.dumps(x, ensure_ascii=False,
                                                      sort_keys=True, default=str))
    if isinstance(obj, (list, tuple)):
        return [canonical(x) for x in obj]
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    # 兜底：任何非 JSON 原生类型（例如 dataclass / 自定义对象）按 `str()` 收敛，
    # **不抛异常** —— hash 计算属于旁路，不得因为一个奇怪字段把主路径打断。
    return str(obj)


def _canonical_json(payload: Any) -> str:
    return json.dumps(canonical(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


# ════════════════════════════════════════════════════════════
#  单条能力的 hash 入口
# ════════════════════════════════════════════════════════════


def llm_visible_of(record: Any) -> bool:
    """该能力是否进入**模型可见集**（本仓库口径，不新造第二套）

    v1.4 §5.1 把它拆成 `llm_visible`（可见性）与 `llm_invokable`（可调用性）；
    本仓库对应的**实际落点**是 `agent/tools/__init__.py::_hidden_tool_names()`
    ——不给模型看的 = `internal` ∪ `non_callable_tool_names()`，而
    `non_callable_tool_names()` 的判据是
    `llm_callable == False` **或** `callable_mode == "manual"`（见
    `agent/lines/callability.py`）。

    故本函数 = `¬internal ∧ reachable ∧ llm_callable ∧ callable_mode != "manual"`。
    【为什么不用 v1.4 字面的 `¬internal` 一条】那会漏掉一整类真实变化：
    `llm_callable` 由 true 改 false 时模型立刻看不见该工具，而按字面口径 hash
    不变 ⇒ **该重建时不重建**。此处取"实际可见集"，与 `get_tool_defs` 同一事实。
    """
    internal = bool(getattr(record, "internal", False))
    reachable = bool(getattr(record, "reachable", True))
    callable_ = bool(getattr(record, "llm_callable", True))
    mode = str(getattr(record, "callable_mode", "auto") or "auto").strip().lower()
    return (not internal) and reachable and callable_ and mode != "manual"


def _confirm_level_of(record: Any) -> str:
    """生效确认级别（与闸门同源：优先 `effective_confirm_level`，否则按 TASK-06 派生）"""
    for attr in ("effective_confirm_level", "confirm_level"):
        raw = str(getattr(record, attr, "") or "").strip().upper()
        if raw:
            return raw
    try:
        from agent.lines.models import derive_confirm_level  # noqa: PLC0415
        return derive_confirm_level(str(getattr(record, "plane", "") or ""),
                                    str(getattr(record, "effect", "") or ""),
                                    str(getattr(record, "risk", "") or ""))
    except Exception:  # noqa: BLE001  派生不可得 ⇒ 留空（不打断 hash 计算）
        return ""


def entry_of(record: Any) -> Dict[str, Any]:
    """一条能力的**规范化 hash 输入**（6 个 per-tool 维度；第 7 维在快照层）

    返回值即"模型这一轮看到的这个工具"的最小充分描述。字段名与 `HASHED_FIELDS`
    对齐，便于 :func:`diff_entries` 出**逐字段**的变更说明（而不是只说"变了"）。
    """
    name = str(getattr(record, "tool_name", "") or getattr(record, "name", "") or "")
    tenant = str(getattr(record, "tenant_id", "default") or "default")
    return {
        "name": name,
        "tenant_id": tenant,
        "version": str(getattr(record, "version", "") or ""),
        "input_schema": canonical(getattr(record, "input_schema", None)),
        "description": str(getattr(record, "description", "") or ""),
        "llm_visible": llm_visible_of(record),
        "permission": {
            "permission_level": str(getattr(record, "permission_level", "") or ""),
            "needs_approval": bool(getattr(record, "needs_approval", False)),
            "risk": str(getattr(record, "risk", "") or ""),
            "confirm_level": _confirm_level_of(record),
        },
    }


def build_entries(records: Iterable[Any], *, model: str = "") -> Tuple[Dict[str, Any], ...]:
    """构造**全量** hash 载荷里的 `tools` 段

    【顺序无关】按 `(name, tenant_id)` 排序：工具体是**集合**，
    `CapabilityRegistry` 的迭代顺序（或调用方给的列表顺序）不得影响 hash。
    """
    entries = [entry_of(r) for r in (records or ())]
    entries.sort(key=lambda e: (e["name"], e["tenant_id"]))
    return tuple(entries)


def hash_of_entries(entries: Sequence[Mapping[str, Any]], *, model: str = "") -> str:
    """对 `{"model_capability": …, "tools": […]}` 求 sha256（第 7 维在这里入 hash）"""
    payload = {
        "model_capability": canonical(model_capability(model)),
        "tools": [canonical(e) for e in (entries or ())],
    }
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def compute_toolset_hash(records: Iterable[Any], *, model: str = "") -> str:
    """便捷入口：`records` → `toolset_hash`（纯函数，同输入恒同输出）"""
    return hash_of_entries(build_entries(records, model=model), model=model)


def diff_entries(prev: Sequence[Mapping[str, Any]],
                 cur: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    """逐字段对拍两次快照的 `tools` 段，产出**人类可读**的变更清单

    输出条目形如 `{"name": "write_file", "field": "permission", "before": …, "after": …}`；
    增删分别记为 `field="__added__"` / `"__removed__"`。
    """
    by_prev = {(e["name"], e["tenant_id"]): e for e in (prev or ())}
    by_cur = {(e["name"], e["tenant_id"]): e for e in (cur or ())}
    out: List[Dict[str, Any]] = []
    for key in sorted(set(by_prev) | set(by_cur)):
        name = key[0]
        a, b = by_prev.get(key), by_cur.get(key)
        if a is None:
            out.append({"name": name, "field": "__added__", "before": None,
                        "after": b})
            continue
        if b is None:
            out.append({"name": name, "field": "__removed__", "before": a,
                        "after": None})
            continue
        for f in sorted(set(a) | set(b)):
            if _canonical_json(a.get(f)) != _canonical_json(b.get(f)):
                out.append({"name": name, "field": f, "before": a.get(f),
                            "after": b.get(f)})
    return out


def describe_hash_scope() -> Dict[str, Any]:
    """把"纳入了什么/排除了什么/为什么"做成可查询结构（文档与验收共用一份）"""
    return {
        "included": list(HASHED_FIELDS),
        "included_reason": {
            "name": "工具名即主键；改名=另一个工具",
            "version": "同名不同版是不同契约",
            "input_schema": "模型据它生成参数",
            "description": "模型据它选择工具（语义面）",
            "llm_visible": "可见性反转是最强的一次变化",
            "permission": "免确认 ↔ 逐次确认，模型与闸门的交互不同",
            "model_capability": "模型无 tool calling 通道时工具集被清空，是两个世界",
        },
        "excluded": dict(EXCLUDED_FIELDS),
        "excluded_hard_rule": ("health：v1.4 §11「健康频繁变化不触发重建，"
                              "仅过滤注入」"),
        "payload_shape": "{'model_capability': {...}, 'tools': [entry, ...]}",
        "digest": "sha256(规范化 JSON)（键排序、集合排序、数组保序）",
    }


# ════════════════════════════════════════════════════════════
#  快照与会话
# ════════════════════════════════════════════════════════════


def _health_view(provider: Optional[Callable[[str], str]]):
    """借 `CapabilityRegistry` 的健康判定实现（**复用，不复制**）

    `agent/capregistry/view.py::CapabilityRegistry.is_healthy` 里写着那条口径
    （`unhealthy/down/open/failed` 为不健康、探针故障按可用处理）。这里刻意
    **实例化一个空 Registry** 来复用同一实现，而不是在本模块重写一份 ——
    否则"什么算不健康"就有了两处判据（D1）。
    """
    from .view import CapabilityRegistry  # noqa: PLC0415  惰性：避免 import 环
    return CapabilityRegistry((), health_provider=provider)


@dataclass(frozen=True)
class ToolsetSnapshot:
    """一次观测到的工具集（**hash 载荷 + 不参与 hash 的旁路信息**）"""

    toolset_hash: str
    model: str
    entries: Tuple[Dict[str, Any], ...]
    #: 🔴 **不参与 hash** 的健康面（v1.4 §11）
    health: Mapping[str, str] = field(default_factory=dict)
    #: 因健康不可用而被过滤掉的名字（只影响过滤/注入）
    filtered_out: Tuple[str, ...] = ()
    generation: int = 1

    @property
    def visible_names(self) -> Tuple[str, ...]:
        return tuple(e["name"] for e in self.entries if e["llm_visible"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "toolset_hash": self.toolset_hash,
            "model": self.model,
            "tool_count": len(self.entries),
            "llm_visible_count": len(self.visible_names),
            "filtered_out": list(self.filtered_out),
            "generation": int(self.generation),
            "health_included_in_hash": False,
        }


@dataclass(frozen=True)
class RebuildDecision:
    """一次"要不要重建会话"的裁决（**含提示文案**，v1.4 §11）"""

    rebuild_required: bool
    reason: str
    message: str
    toolset_hash: str
    previous_hash: str = ""
    changed: Tuple[Dict[str, Any], ...] = ()
    changed_fields: Tuple[str, ...] = ()
    changed_tools: Tuple[str, ...] = ()
    #: 健康变化（**明确不触发重建**；只影响过滤/注入）
    health_changed: Tuple[str, ...] = ()
    filtered_out: Tuple[str, ...] = ()
    generation: int = 1
    session_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rebuild_required": bool(self.rebuild_required),
            "reason": self.reason,
            "message": self.message,
            "toolset_hash": self.toolset_hash,
            "previous_hash": self.previous_hash,
            "changed_fields": list(self.changed_fields),
            "changed_tools": list(self.changed_tools),
            "health_changed": list(self.health_changed),
            "health_triggers_rebuild": False,
            "filtered_out": list(self.filtered_out),
            "generation": int(self.generation),
            "session_id": self.session_id,
        }


class SessionToolset:
    """会话级工具集跟踪器：**hash 变化 ⇒ 重建（并提示）**、**health 变化 ⇒ 只过滤**

    生命周期（v1.4 §11）：

        observe()                      # 建立基线（generation=1）
          ├─ 契约面未变  → rebuild_required=False
          │    └─ 健康变了 → health_changed/filtered_out 非空（**不重建**）
          └─ 契约面变了  → rebuild_required=True + message（提示）
               └─ 自动换基（generation += 1，新会话键）—— 下次以新基线比较

    【为什么换基是自动的】v1.4 §11 要求"**自动**新建会话并提示"。若需调用方
    手动确认，就等于把"契约已经变了"这件事挂起，模型会继续拿着过期契约调工具
    （那正是 hash 要防的事）。这里的 `generation` + `session_id` 就是新会话键；
    **真正的会话实体由宿主创建**（本模块不持有会话存储，也不写任何数据文件）——
    调用方拿 `decision.message` 提示用户、拿 `decision.session_id` 建新会话。

    【同 session 内缓存】`(源对象身份, model)` 为键。源对象应为**不可变**视图
    （`CapabilityRegistry` 构造后不可变，见其 docstring；或 tuple），
    否则缓存可能落后于被就地修改的容器 —— 这是本类对调用方的唯一要求。
    """

    def __init__(self, *, session_id: str = "", model: str = "") -> None:
        self._session_id = str(session_id or "session")
        self._model = str(model or "")
        self._generation = 0
        self._snapshot: Optional[ToolsetSnapshot] = None
        #: 契约面缓存：`(源对象身份, model)` → `(源强引用, hash, entries)`
        #: （见 `_snapshot_for`；只缓存契约面，不缓存 health）
        self._cache: Dict[Tuple[int, str],
                          Tuple[Any, str, Tuple[Dict[str, Any], ...]]] = {}

    # ── 只读属性 ──

    @property
    def session_id(self) -> str:
        """当前会话键（重建后带 `#gN` 后缀 —— 即"新建会话"的标识）"""
        if self._generation <= 1:
            return self._session_id
        return f"{self._session_id}#g{self._generation}"

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def snapshot(self) -> Optional[ToolsetSnapshot]:
        return self._snapshot

    @property
    def toolset_hash(self) -> str:
        return str(self._snapshot.toolset_hash) if self._snapshot else ""

    # ── 观测 ──

    def observe(self, source: Any, *, model: Optional[str] = None,
                health_provider: Optional[Callable[[str], str]] = None
                ) -> RebuildDecision:
        """观测一次当前工具集，返回"是否重建 + 提示文案"

        Args:
            source: `CapabilityRegistry`（推荐）或任意 `CapabilityRecord` 序列
            model: 本轮模型名；缺省沿用构造时的 model
            health_provider: 运行时健康回调；也可直接用 Registry 自带的那个
        """
        mdl = self._model if model is None else str(model or "")
        snap = self._snapshot_for(source, model=mdl, health_provider=health_provider)
        prev = self._snapshot
        if prev is None:
            # ── 首次观测：建立基线，不重建 ──
            self._snapshot = snap
            self._generation = 1
            return RebuildDecision(
                rebuild_required=False,
                reason="首次观测：建立基线（无前值可比）",
                message=(f"工具集基线已建立：hash={snap.toolset_hash[:12]}…，"
                         f"{len(snap.entries)} 个工具"),
                toolset_hash=snap.toolset_hash, generation=1,
                session_id=self.session_id, filtered_out=snap.filtered_out)

        if snap.toolset_hash == prev.toolset_hash:
            # ── 契约面未变：**健康变化绝不触发重建**（v1.4 §11 核心）──
            health_changed = tuple(sorted(
                n for n in (set(prev.health) | set(snap.health))
                if str(prev.health.get(n, "")) != str(snap.health.get(n, ""))))
            self._snapshot = snap               # 换基：health 面刷新，generation 不变
            if health_changed:
                msg = (f"工具集契约未变（hash 不变）；{len(health_changed)} 个能力"
                       f"健康态变化 ⇒ **仅影响过滤/注入**，不重建会话")
                reason = "health 变化不触发重建（v1.4 §11）"
            else:
                msg, reason = "工具集契约与健康面均未变", "无变化"
            return RebuildDecision(
                rebuild_required=False, reason=reason, message=msg,
                toolset_hash=snap.toolset_hash, previous_hash=prev.toolset_hash,
                health_changed=health_changed, filtered_out=snap.filtered_out,
                generation=self._generation, session_id=self.session_id)

        # ── 契约面变了：自动重建（换基 + 提示）──
        changed = diff_entries(prev.entries, snap.entries)
        # 快照级维度（`model_capability` 是"整批工具共享"的那一维，不在 per-tool entry 里）
        snapshot_level = []
        if canonical(model_capability(snap.model)) != canonical(
                model_capability(prev.model)):
            snapshot_level.append("model_capability")
        fields = tuple(sorted({str(c["field"]) for c in changed} | set(snapshot_level)))
        tools = tuple(sorted({str(c["name"]) for c in changed}))
        self._generation += 1
        promoted = replace(snap, generation=self._generation)
        self._snapshot = promoted
        if tools:
            detail = "、".join(f"{c['name']}.{c['field']}" for c in changed[:5])
            more = "" if len(changed) <= 5 else f" 等 {len(changed)} 处"
            scope = f"{len(tools)} 个工具：{detail}{more}"
        else:
            scope = "、".join(snapshot_level) or "（见 reason）"
        msg = (f"工具集契约已变更（{scope}）"
               f"⇒ 已新建会话（{self.session_id}）以刷新模型可见的工具视图")
        return RebuildDecision(
            rebuild_required=True,
            reason=f"toolset_hash 变化：{prev.toolset_hash[:12]}… → "
                   f"{snap.toolset_hash[:12]}…（维度：{'/'.join(fields)}）",
            message=msg, toolset_hash=snap.toolset_hash,
            previous_hash=prev.toolset_hash, changed=tuple(changed),
            changed_fields=fields, changed_tools=tools,
            health_changed=(), filtered_out=snap.filtered_out,
            generation=self._generation, session_id=self.session_id)

    # ── 内部：带缓存的快照构建 ──

    def _snapshot_for(self, source: Any, *, model: str,
                      health_provider: Optional[Callable[[str], str]]
                      ) -> ToolsetSnapshot:
        records = self._records_of(source)
        provider = health_provider
        if provider is None:
            provider = getattr(source, "health_of", None)  # Registry 自带只读回调
        view = _health_view(provider)
        health = {str(getattr(r, "tool_name", "") or ""): view.health_of(
            str(getattr(r, "tool_name", "") or "")) for r in records}
        unhealthy = tuple(sorted(
            n for n in health if not view.is_healthy(n)))
        cache_key = (id(source), model)
        cached = self._cache.get(cache_key)
        if cached is not None:
            # 【缓存纪律】缓存里**只有契约面**（hash + entries）。health 每轮都可能变，
            # 把 health 一起缓存就等于"缓存一个过期事实" ⇒ 命中时用**本轮**算出的
            # health / filtered_out 装回快照。
            # 元组第二项是**源对象的强引用**：`id()` 只在对象存活期间唯一，
            # 持引用可防止"旧源被回收 → 新对象复用同一 id"造成的缓存错配。
            _held, base_hash, base_entries = cached
        else:
            base_entries = build_entries(records, model=model)
            base_hash = hash_of_entries(base_entries, model=model)
            self._cache[cache_key] = (source, base_hash, base_entries)
        return ToolsetSnapshot(
            toolset_hash=base_hash, model=model, entries=base_entries,
            health=canonical(health), filtered_out=unhealthy,
        )

    @staticmethod
    def _records_of(source: Any) -> List[Any]:
        """`CapabilityRegistry` → 其 specs；其余可迭代对象按原样当记录序列"""
        specs = getattr(source, "specs", None)
        if specs is not None:
            return list(specs)
        return list(source or ())
