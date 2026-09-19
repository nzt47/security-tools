"""能力平面与主线 —— 数据模型

【为什么有这个包】
    云枢的工具装配此前只有一个**全局开关**（`data/tools_config.json` 的扁平
    `tool_states`），对"不同 Agent 各管一条线"这件事没有任何支持面
    （见 docs/工具集评估与重分类报告.md §4.7）。

    本包把它换成两层：
        L1  工具原子 → 由 `data/tool_definitions/*.yaml` 声明
                        plane / effect / risk / tags（唯一权威）
        L2  主线档案 → 由 `data/agent_lines/*.yaml` 声明
                        平面权重 + 核心工具 + 效果上限 + 技能包

【四平面】
    resident  常驻 —— 每轮必发（高频低 token）
    perceive  感知 —— 只读取，不改变世界
    act       行动 —— 改变世界
    govern    治理 —— **改变云枢自身能力集** ⇒ 天然就是审批边界

【三维正交】
    plane   决定"放在装配阶梯的哪一层"（组装用）
    effect  决定"最多能造成什么后果"（治理用：read<write<execute<extend）
    risk    决定"要不要人工确认"（审批用）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOL_DEFS_DIR = os.path.join(_ROOT, "data", "tool_definitions")
AGENT_LINES_DIR = os.path.join(_ROOT, "data", "agent_lines")

PLANES = ("resident", "perceive", "act", "govern")
EFFECTS = ("read", "write", "execute", "extend")
RISKS = ("low", "medium", "high", "critical")

#: 「可被 LLM 调用」声明的取值域（唯一来源：agent/lines/callability.py，此处只做解析）
#: 为什么在这里再列一次而不 import：`callability` 反过来要 import 本模块的目录常量，
#: 互相 import 会形成环；两处取值域由 tests/unit/test_tool_callability.py 对拍锁死。
TOOL_TYPES = ("tool", "skill", "api", "script")
CALLABLE_MODES = ("auto", "required", "manual")
PERMISSION_LEVELS = ("public", "internal", "restricted")

#: effect 的偏序：用于 policy.effect_allow 的包含判定
_EFFECT_ORDER = {"read": 0, "write": 1, "execute": 2, "extend": 3}

#: 风险的推荐审批阈值：risk >= 此值则默认需要人工确认
_APPROVAL_FROM_RISK = {"critical"}


@dataclass(frozen=True)
class ToolMeta:
    """单个工具的能力元数据（来自 data/tool_definitions/<name>.yaml）"""

    name: str
    category: str = ""
    plane: str = "act"
    effect: str = "execute"
    risk: str = "medium"
    tags: tuple = ()
    description: str = ""
    #: 内部工具：保留注册（供 AsyncExecutor 等按名调用），但不进模型可见集。
    #: 为什么需要它：`call()` 要求名字在 `_registry` 中，所以"内部执行体"不能注销，
    #: 只能从 `get_tool_defs()` 里隐藏。
    internal: bool = False

    # ── 「可被 LLM 调用」声明（见 agent/lines/callability.py）──
    #: 能力形态：tool | skill | api | script
    tool_type: str = "tool"
    #: 声明：是否允许 LLM 发起调用（**生效值**还要过 schema/执行器/权限三关）
    llm_callable: bool = True
    #: auto（模型自主判断）| required（必须调用）| manual（仅人工/系统）
    callable_mode: str = "auto"
    #: public | internal | restricted（与 plane/effect/risk 的派生值必须一致）
    permission_level: str = ""
    #: 是否允许在沙箱（受限会话，默认只读）中执行
    sandbox_allowed: bool = True
    #: 不可调用原因（llm_callable=false 时必填）
    reason: str = ""

    @property
    def needs_approval(self) -> bool:
        """治理平面 / 改变能力集 / 高危 ⇒ 需要人工确认"""
        return (
            self.plane == "govern"
            or self.effect == "extend"
            or self.risk in _APPROVAL_FROM_RISK
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "plane": self.plane,
            "effect": self.effect,
            "risk": self.risk,
            "tags": list(self.tags),
            "needs_approval": self.needs_approval,
            "internal": bool(self.internal),
            "tool_type": self.tool_type,
            "llm_callable": bool(self.llm_callable),
            "callable_mode": self.callable_mode,
            "permission_level": self.permission_level,
            "sandbox_allowed": bool(self.sandbox_allowed),
            "reason": self.reason,
        }


def _norm(value: Any, allowed: tuple, default: str) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else default


def _as_bool(value: Any, default: bool) -> bool:
    """把 YAML 的布尔写法收敛成 bool（`true/false/1/0/yes/no/on/off`）

    【不易·名字有讲究，勿改回 `_flag`】`scripts/scan_settings.py` 的
    `KNOWN_READ_HELPERS` 把 `_flag` 登记为"环境开关读取助手"的名字契约，
    于是 `_flag(doc.get("llm_callable"), True)` 会被扫描器当成**开关读取点**、
    参数不是字面量 ⇒ 产出一个 `<unresolved>` 动态家族 ⇒
    `test_settings_registry.py::TestMechanicalZeroGap` 两条零缺口守卫变红（CI 实测）。
    故本助手取 `_as_bool`（不带 env/getenv 词干，正则也匹配不到）。
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return default


# ── 元数据读取加速（P0-1，2026-09-19）────────────────────────────────
# Why 两处都改：
#   1) loader —— 用 C 实现解析。实测 91 个 YAML：纯 Python `SafeLoader` 156.4ms
#      vs `CSafeLoader` 32.8ms（**4.77x**，省 123.6ms），且解析结果逐字段完全相同
#      （已做 JSON 序列化对拍，见 tests/unit/test_load_tool_meta_cache.py）。
#      回落原因：PyYAML 的 C 扩展是可选的（纯 Python 环境没有 `_yaml`），
#      故用 try/except 取 C 实现、缺则回落 —— 与仓库既有的"缺依赖不崩溃"取舍一致
#      （参考 `_as_bool` 的保守默认思路）。
try:  # pragma: no cover - 取决于是否装了 PyYAML 的 C 扩展
    from yaml import CSafeLoader as _YamlLoader
except ImportError:  # pragma: no cover
    from yaml import SafeLoader as _YamlLoader

# Why 缓存（比换 loader 更关键）：
#   本模块原 docstring 写着"【简易】单次读取，**调用方自行缓存**"，但实际上
#   `agent/tool_gate.py:627`、`agent/rate_limiter.py:76`、`agent/tool_approval.py:354`、
#   `agent/human_in_the_loop/hitl.py:85` **各自维护了一份独立缓存**，互不共享
#   ⇒ 同一次请求里全量扫 YAML 最多发生 **4 次**（4 × 155ms ≈ 620ms）。
#   把缓存下沉到本函数（唯一权威读取入口）后，4 处调用共享同一份结果。
# Why 用 mtime 签名而不是 TTL：
#   本仓库大量存在"改 YAML 立即生效"的使用方式（如 `scripts/sync_capability_manifest.py`
#   之后人工核对、UI 里改 plane/risk）。TTL 会让改动静默延迟生效 —— 那是一个比慢更糟的缺陷。
#   mtime 签名只统计 `数据文件改动 + 目录增删`，因此：
#     · 正常运行（无人改文件）→ 单次 `os.scandir` + stat，**实测 < 1ms**
#     · 改了任何 YAML / 增删文件 → 签名变化 → 自动重读，**语义与改动前完全一致**
_META_CACHE: Dict[str, Dict[str, ToolMeta]] = {}
_META_CACHE_SIG: Dict[str, tuple] = {}


def _defs_signature(root: str) -> tuple:
    """`data/tool_definitions/` 的轻量签名：文件名 + mtime_ns。

    Why 不含文件内容哈希：哈希要把 91 个文件全读一遍（正是我们要避免的开销）。
    `mtime_ns` 精度足以覆盖"人工编辑 YAML"这一唯一现实变更路径；
    极端情况（同一纳秒内改两次）由 `load_tool_meta(force=True)` 兜底。
    """
    entries = []
    with os.scandir(root) as it:
        for e in it:
            if e.name.endswith(".yaml") and e.is_file():
                try:
                    entries.append((e.name, e.stat().st_mtime_ns))
                except OSError:
                    # 读不到 stat 的文件视为"已变化"，逼一次重读而不是静默漏掉
                    entries.append((e.name, -1))
    entries.sort()
    return tuple(entries)


def load_tool_meta(defs_dir: Optional[str] = None, force: bool = False) -> Dict[str, ToolMeta]:
    """读取全部工具 YAML 的能力元数据。

    【不易】缺字段时**给保守默认**（act/execute/medium）而不是崩溃——
            未登记的工具按"会改变世界"对待，安全侧从严。
    【变易】进程级缓存 + mtime 失效：本函数是**唯一权威读取入口**，
            缓存下沉到这里可使原先 4 份独立缓存（tool_gate / rate_limiter /
            tool_approval / hitl）共享同一份结果。
    【简易】`force=True` 可强制重读（治理脚本/测试用）。

    ⚠️ 返回的是**缓存内的同一份 dict 对象**（不是副本）。调用方**不得原地修改**
       返回值；需要修改请自行 `dict(...)` 浅拷贝（既有调用点已是这一用法，
       如 `agent/tool_gate.py:642` 的 `dict(load_tool_meta())`）。
    """
    root = defs_dir or TOOL_DEFS_DIR
    if not os.path.isdir(root):
        return {}
    try:
        sig = _defs_signature(root)
    except OSError:
        # 目录不可读时不缓存、也不返回陈旧数据
        sig = None
    if not force and sig is not None and _META_CACHE_SIG.get(root) == sig:
        return _META_CACHE.get(root, {})

    out: Dict[str, ToolMeta] = {}
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".yaml"):
            continue
        path = os.path.join(root, fname)
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = yaml.load(f, Loader=_YamlLoader)
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(doc, dict):
            continue
        name = str(doc.get("name") or os.path.splitext(fname)[0])
        raw_tags = doc.get("tags") or ()
        if isinstance(raw_tags, str):
            raw_tags = [raw_tags]
        tags = tuple(str(t) for t in raw_tags if str(t).strip())
        out[name] = ToolMeta(
            name=name,
            category=str(doc.get("category") or ""),
            plane=_norm(doc.get("plane"), PLANES, "act"),
            effect=_norm(doc.get("effect"), EFFECTS, "execute"),
            risk=_norm(doc.get("risk"), RISKS, "medium"),
            tags=tags,
            description=str(doc.get("description") or "")[:200],
            internal=bool(doc.get("internal", False)),
            tool_type=_norm(doc.get("tool_type"), TOOL_TYPES, "tool"),
            llm_callable=_as_bool(doc.get("llm_callable"), True),
            callable_mode=_norm(doc.get("callable_mode"), CALLABLE_MODES, "auto"),
            permission_level=_norm(doc.get("permission_level"), PERMISSION_LEVELS, ""),
            sandbox_allowed=_as_bool(doc.get("sandbox_allowed"), True),
            reason=str(doc.get("reason") or "").strip(),
        )
    if sig is not None:
        _META_CACHE[root] = out
        _META_CACHE_SIG[root] = sig
    return out


def invalidate_tool_meta_cache() -> None:
    """清空元数据缓存（测试与治理脚本用）。

    Why 需要它：`tests/` 里大量用例会临时替换 `data/tool_definitions/`
    （例如 `agent/tool_gate.py:1002` 附近自述"测试若替换了 data/tool_definitions/
    或想验证元数据刷新"）。mtime 签名通常能自动兜住，但同一纳秒内的
    替换 + 恢复会让签名回到原值 —— 显式失效是这种情况下的唯一可靠手段。
    """
    _META_CACHE.clear()
    _META_CACHE_SIG.clear()


@dataclass
class LineProfile:
    """一条主线（可组装的能力档案）

    【主线是权重，不是分区】——同一条工具可以同时被多条主线使用；
    主线只决定"在本次装配里它排多前、要不要被排除"，不决定归属。
    """

    id: str
    name: str = ""
    description: str = ""
    enabled: bool = True

    #: 平面权重（0 = 该平面不参与；越大越优先且召回越多）
    plane_weights: Dict[str, float] = field(default_factory=dict)
    #: 每个平面的**保底召回数**（解决"高优先级平面吃光名额"的饥饿问题）
    plane_floors: Dict[str, int] = field(default_factory=dict)

    #: 主线核心工具：权重加成（可跨平面）
    boost: List[str] = field(default_factory=list)
    #: 明确排除的工具
    mute: List[str] = field(default_factory=list)
    #: 关注的标签：命中的工具获得小幅加成
    tags: List[str] = field(default_factory=list)

    #: 单轮最多暴露给模型的工具数
    max_tools: int = 20

    #: 治理策略
    effect_allow: List[str] = field(default_factory=lambda: ["read", "write", "execute"])
    requires_approval: List[str] = field(default_factory=list)
    #: 是否允许本主线调用 govern 平面（改变自身能力集）
    allow_govern: bool = False

    #: 绑定的技能（skills.json 的 id）
    skills: List[str] = field(default_factory=list)
    #: 绑定的系统提示词片段（可选）
    prompt_note: str = ""

    def __post_init__(self) -> None:
        if not self.plane_weights:
            self.plane_weights = {"resident": 1.0, "perceive": 1.0, "act": 1.0}
        # 归一化平面权重：未知平面丢弃，负值归零
        self.plane_weights = {
            _norm(k, PLANES, ""): float(v)
            for k, v in self.plane_weights.items()
            if _norm(k, PLANES, "")
        }
        self.plane_weights = {k: max(0.0, v) for k, v in self.plane_weights.items()}
        if not self.allow_govern:
            self.plane_weights.pop("govern", None)
        else:
            # 【自洽性】allow_govern=True 必须同时放行 extend 效果，否则治理平面会被
            # effect_allow 过滤成空集 —— 那样"允许治理"就是个静默失效的开关。
            # 注意：放行 ≠ 免确认；确认由 requires_approval 决定（风险仍由 ToolMeta 标注）。
            if "extend" not in self.effect_allow:
                self.effect_allow = list(self.effect_allow) + ["extend"]

    # ── 校验 ──

    def validate(self, known_tools: Optional[set] = None) -> List[str]:
        """返回问题列表（空 = 通过）"""
        issues: List[str] = []
        if not self.id or not isinstance(self.id, str):
            issues.append("id 不能为空")
        if self.max_tools <= 0:
            issues.append("max_tools 必须为正整数")
        for eff in self.effect_allow:
            if eff not in EFFECTS:
                issues.append(f"effect_allow 含非法值: {eff}")
        for plane, floor in self.plane_floors.items():
            if plane not in PLANES:
                issues.append(f"plane_floors 含非法平面: {plane}")
            if floor < 0:
                issues.append(f"plane_floors[{plane}] 不能为负")
        for eff in self.requires_approval:
            if eff not in EFFECTS:
                issues.append(f"requires_approval 含非法值: {eff}")
        if known_tools is not None:
            unknown = [t for t in list(self.boost) + list(self.mute) if t not in known_tools]
            if unknown:
                issues.append(f"引用了未注册的工具: {sorted(unknown)}")
        return issues

    @property
    def max_effect_rank(self) -> int:
        """允许的最高 effect 等级（用于快速判定）"""
        if not self.effect_allow:
            return 0
        return max(_EFFECT_ORDER.get(e, 0) for e in self.effect_allow)

    def allows_effect(self, effect: str) -> bool:
        return _EFFECT_ORDER.get(effect, 99) <= self.max_effect_rank

    # ── 序列化 ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name or self.id,
            "description": self.description,
            "enabled": bool(self.enabled),
            "plane_weights": dict(self.plane_weights),
            "plane_floors": dict(self.plane_floors),
            "boost": list(self.boost),
            "mute": list(self.mute),
            "tags": list(self.tags),
            "max_tools": int(self.max_tools),
            "effect_allow": list(self.effect_allow),
            "requires_approval": list(self.requires_approval),
            "allow_govern": bool(self.allow_govern),
            "skills": list(self.skills),
            "prompt_note": self.prompt_note,
        }

    def to_yaml(self) -> str:
        return yaml.safe_dump(self.to_dict(), allow_unicode=True, sort_keys=False)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LineProfile":
        if not isinstance(raw, dict):
            raise ValueError("主线档案必须是字典")
        lid = str(raw.get("id") or "").strip()
        if not lid:
            raise ValueError("主线档案缺少 id")
        # 【坑】不能用 `raw.get("max_tools") or 20`：`0` 是 falsy，会被静默归一成 20，
        # 于是 validate() 的"max_tools 必须为正整数"从 REST 层永远不可达
        # （实测 {"max_tools":0} 会通过校验并静默变成 20）。必须只对 None/空串兜底。
        _mt = raw.get("max_tools")
        try:
            max_tools = 20 if _mt is None or _mt == "" else int(_mt)
        except (TypeError, ValueError):
            raise ValueError(f"max_tools 必须是整数，收到: {_mt!r}")
        return cls(
            id=lid,
            name=str(raw.get("name") or lid),
            description=str(raw.get("description") or ""),
            enabled=bool(raw.get("enabled", True)),
            plane_weights=dict(raw.get("plane_weights") or {}),
            plane_floors={k: int(v) for k, v in (raw.get("plane_floors") or {}).items()},
            boost=[str(t) for t in (raw.get("boost") or [])],
            mute=[str(t) for t in (raw.get("mute") or [])],
            tags=[str(t) for t in (raw.get("tags") or [])],
            max_tools=max_tools,
            effect_allow=[str(e) for e in (raw.get("effect_allow") or ["read", "write", "execute"])],
            requires_approval=[str(e) for e in (raw.get("requires_approval") or [])],
            allow_govern=bool(raw.get("allow_govern", False)),
            skills=[str(s) for s in (raw.get("skills") or [])],
            prompt_note=str(raw.get("prompt_note") or ""),
        )


__all__ = [
    "PLANES", "EFFECTS", "RISKS", "ToolMeta", "LineProfile",
    "load_tool_meta", "invalidate_tool_meta_cache", "TOOL_DEFS_DIR", "AGENT_LINES_DIR",
    "TOOL_TYPES", "CALLABLE_MODES", "PERMISSION_LEVELS",
]
