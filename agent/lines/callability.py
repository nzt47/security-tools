"""工具 / 技能「可被 LLM 调用」统一标注 —— 字段定义、判定规则与派生清单

【为什么需要这个模块】
    "哪些能力能被模型自己发起调用"此前**没有任何一处能直接回答**：它是由四处
    分散事实**隐式**决定的——
        ① `data/tool_definitions/*.yaml` 的 `internal: true`（保留注册但不给模型看）
        ② 注册表里到底有没有 handler（`agent/tools/__init__.py::_registry`）
        ③ 有没有可用的 JSON Schema（`get_tool_defs` 在缺 schema 时**静默**补一个空壳）
        ④ `data/permission_policies.json` 的角色策略与 `agent/tool_gate.py` 的闸门
    于是"模型能调什么"既看不出来、也无法被自动解析、更无法在装配/网关里按统一口径
    判定。本模块把这件事收敛成**一组统一字段 + 一条判定规则 + 一份可解析清单**。

【统一字段（八项，工具与技能同构）】
    | 字段 | 类型 | 含义 |
    |---|---|---|
    | `tool_name`     | str  | 能力名（工具名 / 技能 id）——两个清单共用同一主键名，便于合并解析 |
    | `tool_type`     | enum | `tool` / `skill` / `api` / `script` |
    | `llm_callable`  | bool | **生效值**：是否允许 LLM 发起调用 |
    | `callable_mode` | enum | `auto`（模型自主判断）/ `required`（必须调用）/ `manual`（仅人工/系统） |
    | `schema_registered` | bool | 是否已注册可用的 JSON Schema（缺 schema 时模型不知道参数，等于半残） |
    | `host_executor` | str  | 宿主执行器 ID 或执行入口（`模块:函数`） |
    | `permission_level` | enum | `public` / `internal` / `restricted` |
    | `sandbox_allowed` | bool | 是否允许在沙箱（受限会话/分身沙箱，默认只读）中执行 |
    | `reason`        | str  | 不可调用原因（`llm_callable=false` 时**必填**） |

    另附诊断字段（不属八项，供 UI 与排障）：`mark`（✅/⚠️/❌ 展示标识）、
    `blockers`（判否的逐条理由）、`conditions`（⚠️ 的条件说明）、
    `declared_llm_callable`（声明值，与生效值区分）、`declared_in`（声明出处）、
    `category`/`plane`/`effect`/`risk`/`needs_approval`/`internal`/`enabled`。

【三层归属（哪一层说了算）】
    声明层（人定的策略，L1 权威）
        工具 → `data/tool_definitions/<name>.yaml` 的
               `tool_type` / `llm_callable` / `callable_mode` / `permission_level` /
               `sandbox_allowed` / `reason`（`host_executor` 可选覆盖）
        技能 → `data/skill_callability.yaml`（技能没有等价的每技能治理文件：
               `skill.md` 的 front matter 由安装器/meta_editor 管辖，未知键可能被重写，
               故技能侧用**单一覆盖表**，集中审计、集中回滚）
    事实层（可证的事实，不由人填）
        `schema_registered` ← YAML/注册表的 schema；技能 ← `config_schema`/`output_schema`
                              / 带脚本技能的参数契约
        `host_executor`     ← 静态注册点扫描（AST，不执行工具代码；默认口径）
                              → 技能侧声明的执行器
                              →（可选）调用方注入的运行时注册表事实 `executor_facts`
        【不易】本模块**不导入 `agent.tools`**：`agent.tools` 反向依赖本模块
                （`get_tool_defs` 读 `non_callable_tool_names` 隐藏判否工具），
                双向导入构成循环依赖，会被架构规则 `no_circular_dependency` 判违规
                （实测 architecture-check 红灯：agent.lines.callability → agent.tools）。
                故运行时事实一律由调用方注入，详见 `runtime_executors()`。
    派生层（本模块算出，写进 `data/capability_manifest.json`）
        `llm_callable`（生效值）、`permission_level`（校验声明与事实是否自洽）、`mark`

【判定规则】
    **硬阻断（不可达 ⇒ ❌）**：无执行器 / 无内容实体 / 已停用 / 权限策略拒绝 /
      内部专用（`internal: true`：设计上不对模型开放，也不进检索索引）。
    **软阻断（可达，但"不由模型发起" ⇒ ⚠️）**：声明 `llm_callable: false` /
      `callable_mode: manual`（由系统或人工触发）/ 缺 JSON Schema（需人补参数）。
    `llm_callable = 无硬阻断 且 无软阻断`，其语义**只有一句**：模型能不能发起这次调用
      （权限/网关/装配读的是它，故 `manual` 恒为 false，含糊不得）。
    `mark` 才回答"这项能力可用吗"：
        ✅ 可调用        模型可发起，且不在审批边界
        ⚠️ 条件可调用    可达，但需人工确认（审批边界）或**不由模型发起**
                         （`manual` / 声明 false ⇒ 由系统或人工触发）/ 缺参数契约
        ❌ 不可调用      **不可达**（缺执行器 / 无内容实体 / 已停用 / 被策略拒绝 / 内部专用）
    ⚠️ 与 ❌ 的分界是"能不能被执行"，不是"模型能不能发起" —— 技能（提示词型由
      `ContextInjector` 注入、脚本型由 `SkillExecutor` 执行）属于**可达但不经模型发起**，
      故标 ⚠️；而"注册点里找不到执行器""已停用"这类才是真的不可用，标 ❌。
      `reason_kind` 给出成因类别（`unreachable` / `not_model_initiated` / `needs_approval`），
      `trigger` 给出真实触发者（`model` / `system` / `human` / `none`）。

【不易】
    - 本模块**只读**数据文件，不写任何文件（写清单由 `scripts/sync_capability_manifest.py` 做）；
    - 任何异常都降级为"不可达 + 原因"，**绝不**静默判成可调用（安全侧从严）；
    - `llm_callable=false` 不等于"不能执行"：`process_distill_run` 这类内部执行体
      仍可被 AsyncExecutor 按名调用（`agent.tools.call()` 不拦它），只是模型看不见。
【简易】
    from agent.lines.callability import build_manifest, non_callable_tool_names
"""

from __future__ import annotations

import ast
import datetime
import json
import logging
import os
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 工具治理声明目录（L1 权威，与 agent/lines/models.py 同源）
TOOL_DEFS_DIR = os.path.join(_REPO_ROOT, "data", "tool_definitions")
#: 技能侧可调用性声明（单一覆盖表；理由见模块 docstring"三层归属"）
SKILL_CALLABILITY_PATH = os.path.join(_REPO_ROOT, "data", "skill_callability.yaml")
#: 统一清单产物（派生，勿手改）
MANIFEST_PATH = os.path.join(_REPO_ROOT, "data", "capability_manifest.json")
#: RBAC/ABAC 策略（只读 `roles[*].denied_tools` 的并集，与 agent/tool_gate.py 同口径）
PERMISSION_POLICIES_PATH = os.path.join(_REPO_ROOT, "data", "permission_policies.json")
#: 技能实体目录 / 运行时技能目录 / 技能管理台账
SKILLS_REPO_DIR = os.path.join(_REPO_ROOT, "data", "skills_repo")
SKILLS_JSON_PATH = os.path.join(_REPO_ROOT, "data", "skills.json")
SKILLS_MGMT_PATH = os.path.join(_REPO_ROOT, "data", "skills_mgmt.json")
#: 包内注册点目录（静态扫描执行器用）
_TOOLS_PKG_DIR = os.path.join(_REPO_ROOT, "agent", "tools")
#: 包外注册点（显式列入，避免"注册点搬家后标注静默失效"）
_EXTRA_REGISTER_FILES = (
    os.path.join(_REPO_ROOT, "agent", "process_distill", "tools.py"),
    os.path.join(_REPO_ROOT, "agent", "knowledge", "tools.py"),
)

# ── 取值域（唯一来源：声明、清单、守门测试都从这里取）────────────────────────
TOOL_TYPES = ("tool", "skill", "api", "script")
CALLABLE_MODES = ("auto", "required", "manual")
PERMISSION_LEVELS = ("public", "internal", "restricted")

#: 谁真的会触发这项能力（`none` = 没有任何触发者 ⇒ 不可达）
TRIGGERS = ("model", "system", "human", "none")

#: 标识成因类别（供 UI 选措辞：不可调用 / 非模型发起 / 需审批）
REASON_KINDS = ("unreachable", "not_model_initiated", "needs_approval", "")

#: 必须在每个工具 YAML 里出现的可调用性声明字段（守门用；`reason` 只在判否时必填）
REQUIRED_DECL_FIELDS = (
    "tool_type",
    "llm_callable",
    "callable_mode",
    "permission_level",
    "sandbox_allowed",
)

#: 展示标识（与判定同源）
MARK_CALLABLE = "✅ 可调用"
MARK_CONDITIONAL = "⚠️ 条件可调用"
MARK_BLOCKED = "❌ 不可调用"

#: `permission_policies.json` 里的通配拒绝
_WILDCARD = "*"


# ════════════════════════════════════════════════════════════
#  取值归一（宽容读、从严判）
# ════════════════════════════════════════════════════════════


def _choice(value: Any, allowed: Tuple[str, ...], default: str = "") -> str:
    text = str(value if value is not None else "").strip().lower()
    return text if text in allowed else default


def _as_bool(value: Any, default: bool) -> bool:
    """把 YAML/JSON 里的布尔写法收敛成 bool（`true/false/1/0/yes/no/on/off`）"""
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


def _read_yaml(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as e:  # 读不到 = 当作没声明（fail-closed 由调用方兜）
        logger.debug("[callability] 读取失败 %s: %s", path, e)
        return None


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as e:
        logger.debug("[callability] 读取失败 %s: %s", path, e)
        return None


def schema_is_registered(schema: Any) -> bool:
    """一份 schema 是否"可用于 tool calling"

    【不易】缺 schema 时 `agent/tools/__init__.py::get_tool_defs` 会**静默**补一个
            `{"type":"object","properties":{},"additionalProperties":true}` —— 调用能过，
            但模型不知道参数名，等于半残。故这里只认真正声明过的 object schema。
    """
    if not isinstance(schema, dict):
        return False
    if schema.get("type") != "object":
        return False
    props = schema.get("properties")
    return props is None or isinstance(props, dict)


# ════════════════════════════════════════════════════════════
#  事实层：执行器与权限策略
# ════════════════════════════════════════════════════════════


def static_executors() -> Dict[str, str]:
    """静态扫描注册点 → {能力名: \"模块:函数\"}（AST，不执行任何工具代码）

    【为什么静态也要扫一遍】运行时注册表只能证明"**此刻**这个进程里注册了什么"，
            清单同步、CI 守门、离线审计都拿不到它；而注册点写在源码里是稳定事实。
    """
    out: Dict[str, str] = {}

    # ① 包内：`@_tools.register("name", ...)` 装饰的函数
    if os.path.isdir(_TOOLS_PKG_DIR):
        files = [os.path.join(_TOOLS_PKG_DIR, f)
                 for f in sorted(os.listdir(_TOOLS_PKG_DIR)) if f.endswith(".py")]
    else:
        files = []
    files += [p for p in _EXTRA_REGISTER_FILES if os.path.exists(p)]

    for path in files:
        module = _module_name_of(path)
        try:
            with open(path, "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=path)
        except (OSError, SyntaxError) as e:
            logger.debug("[callability] 跳过 %s: %s", path, e)
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                name = _register_name_of(dec)
                if name:
                    out.setdefault(name, f"{module}:{node.name}")
        # ② 数据表形式注册（knowledge/tools.py 的 `_TOOL_DEFS` + `_KB_HANDLERS`）
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
            if not any(isinstance(t, ast.Name) and t.id == "_TOOL_DEFS" for t in targets):
                continue
            try:
                items = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            for item in items or []:
                if isinstance(item, (list, tuple)) and item:
                    out.setdefault(str(item[0]), f"{module}:{item[0]}")
    return out


def _module_name_of(path: str) -> str:
    rel = os.path.relpath(path, _REPO_ROOT).replace(os.sep, "/")
    if rel.endswith(".py"):
        rel = rel[:-3]
    if rel.endswith("/__init__"):
        rel = rel[: -len("/__init__")]
    return rel.replace("/", ".")


def _register_name_of(decorator: Any) -> str:
    """从装饰器节点取被注册的工具名（非 `register(...)` 形态返回空串）"""
    call = decorator if isinstance(decorator, ast.Call) else None
    if call is None:
        return ""
    func = call.func
    fname = func.attr if isinstance(func, ast.Attribute) else (
        func.id if isinstance(func, ast.Name) else "")
    if fname != "register":
        return ""
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return ""


def runtime_executors() -> Dict[str, str]:
    """运行时注册表的执行器事实 —— **由调用方注入，不在本模块导入 `agent.tools`**

    【为什么这里是个空实现（依赖倒置，勿"顺手补个 import"）】
        本模块属于 `agent.lines`，而 `agent.tools` 会反向依赖本模块（`get_tool_defs`
        读 `non_callable_tool_names` 隐藏判否工具）。若在这里 `from agent.tools import ...`，
        就形成 `agent.lines.callability ↔ agent.tools` 的**循环依赖**，被架构规则
        `no_circular_dependency` 判违规（实测：architecture-check 红灯，源模块
        agent.lines.callability → 目标模块 agent.tools）。
        ⇒ 事实由**调用方**提供：`build_manifest(executor_facts=...)`
          （`scripts/sync_capability_manifest.py --runtime` 在脚本侧导入 `agent.tools`，
           脚本不受该架构规则约束；REST 侧读的是已落盘的清单，也不需要导入）。
    【不易】返回空表时不影响正确性：`static_executors()` 的 AST 注册点扫描是默认口径。
    """
    return {}


def denied_tool_names(policies_path: Optional[str] = None) -> Tuple[FrozenSet[str], bool]:
    """权限策略里被拒的工具名（所有角色 `denied_tools` 的并集）+ 是否全局拒绝

    与 `agent/tool_gate.py` 同口径：只读 `denied_tools`，**不读白名单**
    （`default_role=guest` 的白名单只放行 2 个工具，拿它当判据等于把系统判瘫）。
    """
    doc = _read_json(policies_path or PERMISSION_POLICIES_PATH) or {}
    roles = doc.get("roles") if isinstance(doc, dict) else None
    names: set = set()
    for spec in (roles or {}).values():
        if isinstance(spec, dict):
            for n in spec.get("denied_tools") or []:
                names.add(str(n).strip())
    return frozenset(names), _WILDCARD in names


# ════════════════════════════════════════════════════════════
#  声明层：工具 YAML / 技能覆盖表
# ════════════════════════════════════════════════════════════


def parse_declaration(doc: Dict[str, Any]) -> Dict[str, Any]:
    """从一份声明 dict（工具 YAML 或技能覆盖项）解析可调用性字段"""
    doc = doc if isinstance(doc, dict) else {}
    return {
        "tool_type": _choice(doc.get("tool_type"), TOOL_TYPES, "tool"),
        "llm_callable": _as_bool(doc.get("llm_callable"), True),
        "callable_mode": _choice(doc.get("callable_mode"), CALLABLE_MODES, "auto"),
        "permission_level": _choice(doc.get("permission_level"), PERMISSION_LEVELS, ""),
        "sandbox_allowed": _as_bool(doc.get("sandbox_allowed"), True),
        "host_executor": str(doc.get("host_executor") or "").strip(),
        "reason": str(doc.get("reason") or "").strip(),
        "missing_fields": [f for f in REQUIRED_DECL_FIELDS if f not in doc],
    }


def load_tool_docs(defs_dir: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
    """读取全部工具定义 YAML → {工具名: 文档}"""
    root = defs_dir or TOOL_DEFS_DIR
    out: Dict[str, Dict[str, Any]] = {}
    if not os.path.isdir(root):
        return out
    for fname in sorted(os.listdir(root)):
        if not fname.endswith(".yaml"):
            continue
        doc = _read_yaml(os.path.join(root, fname))
        if isinstance(doc, dict):
            name = str(doc.get("name") or os.path.splitext(fname)[0])
            doc.setdefault("name", name)
            doc["_declared_in"] = os.path.relpath(
                os.path.join(root, fname), _REPO_ROOT).replace(os.sep, "/")
            out[name] = doc
    return out


def load_skill_declarations(path: Optional[str] = None) -> Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]:
    """读取技能侧覆盖表 → (defaults, {技能 id: 声明 dict})

    【为什么有 defaults】技能侧的**默认口径是统一**的（技能不是"模型发起的工具调用"），
            逐条重复 31 遍只会制造噪声与漂移；`defaults` 写一次，个别技能只写差异。
    """
    doc = _read_yaml(path or SKILL_CALLABILITY_PATH) or {}
    if not isinstance(doc, dict):
        return {}, {}
    defaults = doc.get("defaults") if isinstance(doc.get("defaults"), dict) else {}
    skills = doc.get("skills") if isinstance(doc.get("skills"), dict) else {}
    merged: Dict[str, Dict[str, Any]] = {}
    for sid, spec in skills.items():
        merged[str(sid)] = {**defaults, **(spec if isinstance(spec, dict) else {})}
    return dict(defaults), merged


# ════════════════════════════════════════════════════════════
#  判定（唯一口径，工具与技能共用）
# ════════════════════════════════════════════════════════════


def effective_permission_level(effect: str, risk: str, needs_approval: bool,
                               denied: bool) -> str:
    """从治理轴派生权限等级（供守门测试与声明对拍）

        审批边界（govern / extend / critical）或被策略拒绝 → restricted
        只读且低风险 → public
        其余（写/执行） → internal
    """
    if needs_approval or denied:
        return "restricted"
    if effect == "read" and risk == "low":
        return "public"
    return "internal"


def judge(*, declared: Dict[str, Any], schema_registered: bool, host_executor: str,
          permission_level: str, is_internal: bool, denied: bool,
          deny_all: bool, enabled: bool = True,
          reason_override: str = "", has_entity: bool = True) -> Dict[str, Any]:
    """统一判定 —— 返回 `{llm_callable, reachable, trigger, reason_kind, blockers,
    soft_blockers, conditions, notes, mark, reason}`

    【两组阻断，语义不同（这是三档标识的根据）】
        硬阻断（hard，不可达 ⇒ ❌）：无执行器 / 无内容实体 / 已停用 / 权限策略拒绝 /
            内部专用（`internal: true`，设计上不对模型开放，也不进检索索引）
        软阻断（soft，可达但"不由模型发起" ⇒ ⚠️）：声明 `llm_callable: false` /
            `callable_mode: manual` / 缺 JSON Schema
    【为什么必须分开】此前两者都算"不可调用" ⇒ 31 个技能全被标成 ❌，看上去像"技能全坏了"。
        事实是它们**照常生效**，只是触发者不是模型（提示词型由 `ContextInjector` 按意图注入、
        脚本型由 `SkillExecutor` 显式执行）。标识要区分"不可用"与"不由模型发起"这两件事。
    【不易】`llm_callable` 的语义**不变**且必须保持严格：它只回答"模型能不能发起这次调用"，
        `manual` 一律为 false —— 权限/网关/装配读的是这个字段，含糊不得。
        三档标识才是"可用性"的表达：✅ 模型可发起 / ⚠️ 可执行但触发有条件 / ❌ 不可达。
    【为什么有 `reason_override`】同一个事实可能被多条判据同时命中
            （如 `process_distill_run`：既声明 false、又是 manual、还是 internal）。
            逐条罗列会把原因写成三段同义句；`reason_override` 让调用方给出**那一条**
            说明，其余同义判据不再重复（`blocker_codes` 仍如实保留供程序解析）。
    """
    hard: List[str] = []
    hard_codes: List[str] = []
    soft: List[str] = []
    soft_codes: List[str] = []
    conditions: List[str] = []
    notes: List[str] = []

    declared_off = not declared.get("llm_callable", True)
    manual = declared.get("callable_mode") == "manual"
    declared_reason = str(reason_override or declared.get("reason") or "").strip()

    # ── 硬阻断：执行不了 / 设计上不对外开放 ──
    if not host_executor:
        hard_codes.append("no_executor")
        hard.append("无执行器（宿主注册点里找不到执行入口）")
    if not has_entity:
        hard_codes.append("no_entity")
        hard.append("无内容实体（清单有条目但仓库里找不到可执行/可注入的内容）")
    if not enabled:
        hard_codes.append("disabled")
        hard.append(declared_reason or "已停用（enabled=false / 未发布）")
    if deny_all:
        hard_codes.append("policy_denied_all")
        hard.append("权限策略全局拒绝（denied_tools 含 '*'）")
    elif denied:
        hard_codes.append("policy_denied")
        hard.append("权限策略拒绝（命中 roles[*].denied_tools）")
    if is_internal:
        hard_codes.append("internal_only")
        hard.append(declared_reason or
                    "内部专用（internal: true）：仅供后台链路按名调用，不进模型可见集")

    # ── 软阻断：可达，但模型不是发起者 ──
    if declared_off:
        soft_codes.append("declared_false")
        soft.append(declared_reason or "声明为不可被 LLM 调用")
    elif manual:
        soft_codes.append("manual_mode")
        soft.append(declared_reason or "callable_mode=manual：仅人工/系统调用，模型不发起")
    if not schema_registered:
        soft_codes.append("no_schema")
        soft.append("无 JSON Schema（缺参数契约，模型无法按 schema 填参）")

    reachable = not hard
    llm_callable = reachable and not soft

    #: 谁真的会触发它（供"权限控制/编排"按触发者分流；`none` = 没有人能触发）
    if not reachable:
        trigger = "none"
    elif llm_callable:
        trigger = "model"
    elif manual or declared_off:
        trigger = "system"      # 宿主链路 / 人工显式调用
    else:
        trigger = "human"       # 只能是"缺参数契约"这类：需人补齐参数

    if llm_callable:
        if permission_level == "restricted":
            conditions.append("属审批边界（govern 平面 / extend 效果 / critical 风险）："
                              "调用会挂审批单，需人工确认后原样重试")
        if declared.get("callable_mode") == "required":
            conditions.append("callable_mode=required：声明为必须调用的工具")
        if not declared.get("sandbox_allowed", True):
            # 【为什么只是 note 不进 conditions】沙箱适用性是一条**独立轴**：
            # 它不改变"模型能不能发起调用"，只改变"在受限会话里能不能跑"。
            # 若把它算成条件，58/91 个工具会被打成 ⚠️，标识随即失去分辨力。
            notes.append("sandbox_allowed=false：不允许在沙箱（受限会话）中执行")

    if not reachable:
        mark = MARK_BLOCKED
    elif not llm_callable or conditions:
        mark = MARK_CONDITIONAL
    else:
        mark = MARK_CALLABLE

    #: 标识的成因类别（供 UI 决定用哪种措辞：不可调用 vs 非模型发起）
    if not reachable:
        reason_kind = "unreachable"
    elif not llm_callable:
        reason_kind = "not_model_initiated"
    elif conditions:
        reason_kind = "needs_approval"
    else:
        reason_kind = ""

    reason = _join(hard) if hard else (_join(soft) if soft else "")
    return {
        "llm_callable": llm_callable,
        "reachable": reachable,
        "trigger": trigger,
        "reason_kind": reason_kind,
        "blockers": hard,
        "blocker_codes": hard_codes,
        "soft_blockers": soft,
        "soft_codes": soft_codes,
        "conditions": conditions,
        "notes": notes,
        "mark": mark,
        "reason": reason,
    }


def _join(items: Iterable[str]) -> str:
    return "；".join(str(i) for i in items if str(i).strip())


# ════════════════════════════════════════════════════════════
#  清单构建：工具 + 技能
# ════════════════════════════════════════════════════════════


def _tool_entry(name: str, doc: Dict[str, Any], *, executors: Dict[str, str],
                denied: FrozenSet[str], deny_all: bool) -> Dict[str, Any]:
    declared = parse_declaration(doc)
    effect = _choice(doc.get("effect"), ("read", "write", "execute", "extend"), "execute")
    risk = _choice(doc.get("risk"), ("low", "medium", "high", "critical"), "medium")
    plane = _choice(doc.get("plane"), ("resident", "perceive", "act", "govern"), "act")
    is_internal = _as_bool(doc.get("internal"), False)
    needs_approval = plane == "govern" or effect == "extend" or risk == "critical"

    executor = declared["host_executor"] or executors.get(name, "")
    schema_registered = schema_is_registered(doc.get("schema"))
    denied_hit = name in denied
    declared_level = declared["permission_level"]
    derived_level = effective_permission_level(effect, risk, needs_approval, denied_hit)
    # 声明优先（人可以显式收紧），但派生值随行输出以便对拍
    level = declared_level or derived_level

    verdict = judge(
        declared=declared, schema_registered=schema_registered, host_executor=executor,
        permission_level=level, is_internal=is_internal,
        denied=denied_hit, deny_all=deny_all,
    )
    return {
        "tool_name": name,
        "tool_type": declared["tool_type"],
        "scope": "repo",
        "llm_callable": verdict["llm_callable"],
        "callable_mode": declared["callable_mode"],
        "schema_registered": schema_registered,
        "host_executor": executor,
        "permission_level": level,
        "sandbox_allowed": declared["sandbox_allowed"],
        "reason": verdict["reason"],
        # ── 诊断/展示 ──
        "mark": verdict["mark"],
        "reachable": verdict["reachable"],
        "trigger": verdict["trigger"],
        "reason_kind": verdict["reason_kind"],
        "blockers": verdict["blockers"],
        "blocker_codes": verdict["blocker_codes"],
        "soft_blockers": verdict["soft_blockers"],
        "soft_codes": verdict["soft_codes"],
        "conditions": verdict["conditions"],
        "notes": verdict["notes"],
        "declared_llm_callable": declared["llm_callable"],
        "declared_permission_level": declared_level,
        "derived_permission_level": derived_level,
        "declared_in": doc.get("_declared_in", ""),
        "category": str(doc.get("category") or ""),
        "plane": plane,
        "effect": effect,
        "risk": risk,
        "needs_approval": needs_approval,
        "internal": is_internal,
        "enabled": True,
    }


def _skill_sources(include_runtime_catalog: bool = False) -> Dict[str, Dict[str, Any]]:
    """汇总技能来源 → {技能 id: {…facts…}}

        ① `data/skills_repo/<id>/skill.md`（实体 + front matter）—— **入库**，默认口径
        ② `data/skills.json`（运行时技能目录）—— 被 .gitignore 忽略，仅 `include_runtime_catalog`
        ③ `data/skills_mgmt.json`（管理台账）—— 同上
    【不易·为什么默认只读 ①（这条是 CI 教出来的）】②③ 都在 .gitignore 里（应用运行时写的
        状态），干净的 checkout / CI 里**根本不存在** ⇒ 若清单依赖它们，提交的
        `data/capability_manifest.json` 在 CI 中重算就必然与权威"不一致"，
        `--check` 直接红灯（实测 CI 报 23 处差异：8 个只在台账里的技能 + 15 个 reason/标识漂移）。
        ⇒ 清单口径 = **仓库可复现的能力面**；运行时安装的技能（extension_store、
        内联指令型台账条目）不在清单内（界面按"无徽章"静默退化）。
        需要看在运行时多出来的那些技能时，用 `include_runtime_catalog=True`（不提交产物）。
    """
    out: Dict[str, Dict[str, Any]] = {}

    def _slot(sid: str) -> Dict[str, Any]:
        return out.setdefault(str(sid), {
            "id": str(sid), "in_repo": False, "in_catalog": False, "in_mgmt": False,
            "enabled": True, "status": "", "has_scripts": False, "inline_content": False,
            "params": {}, "config_schema": None, "output_schema": None,
            "is_sensitive": False, "declared_in": "",
        })

    # ① 仓库实体（入库，唯一默认来源）
    if os.path.isdir(SKILLS_REPO_DIR):
        for sid in sorted(os.listdir(SKILLS_REPO_DIR)):
            sdir = os.path.join(SKILLS_REPO_DIR, sid)
            md = os.path.join(sdir, "skill.md")
            if not os.path.isfile(md):
                continue
            slot = _slot(sid)
            slot["in_repo"] = True
            slot["declared_in"] = os.path.relpath(md, _REPO_ROOT).replace(os.sep, "/")
            fm = _front_matter(md)
            slot["enabled"] = _as_bool(fm.get("enabled"), True)
            slot["status"] = str(fm.get("status") or "")
            slot["params"] = fm.get("default_params") or {}
            scripts_dir = os.path.join(sdir, "scripts")
            slot["has_scripts"] = os.path.isdir(scripts_dir) and any(
                f.endswith(".py") for f in os.listdir(scripts_dir))

    if not include_runtime_catalog:
        return out

    # ② 运行时目录（gitignore，仅显式要求时读）
    cat = _read_json(SKILLS_JSON_PATH) or {}
    for item in (cat.get("skills") if isinstance(cat, dict) else None) or []:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        slot = _slot(item["id"])
        slot["in_catalog"] = True
        slot["enabled"] = slot["enabled"] and _as_bool(item.get("enabled"), True)
        slot["params"] = slot["params"] or (item.get("params") or {})

    # ③ 管理台账（gitignore，仅显式要求时读）
    mgmt = _read_json(SKILLS_MGMT_PATH) or {}
    for sid, rec in (mgmt if isinstance(mgmt, dict) else {}).items():
        if not isinstance(rec, dict):
            continue
        slot = _slot(sid)
        slot["in_mgmt"] = True
        slot["status"] = str(rec.get("status") or slot["status"])
        slot["enabled"] = slot["enabled"] and _as_bool(rec.get("enabled"), True)
        slot["inline_content"] = bool(str(rec.get("content") or "").strip())
        slot["config_schema"] = rec.get("config_schema")
        slot["output_schema"] = rec.get("output_schema")
        slot["is_sensitive"] = _as_bool(rec.get("is_sensitive"), False)
        slot["params"] = slot["params"] or (rec.get("default_params") or {})
    return out


def _front_matter(path: str) -> Dict[str, Any]:
    """读 `---` 包裹的 YAML front matter（非法/缺失 → 空表）"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    try:
        doc = yaml.safe_load(text[3:end])
    except yaml.YAMLError:
        return {}
    return doc if isinstance(doc, dict) else {}


#: 技能的执行器（哪条链路真的会执行/注入它）
SKILL_INJECTOR = "agent.skills_mgmt.context_injector:ContextInjector"
SKILL_EXECUTOR = "agent.skills_mgmt.executor:SkillExecutor"


def _skill_entry(sid: str, facts: Dict[str, Any], decl: Dict[str, Any]) -> Dict[str, Any]:
    declared = parse_declaration({**decl, "tool_type": "skill"})
    has_entity = bool(facts.get("in_repo")) or bool(facts.get("inline_content"))
    if facts.get("has_scripts"):
        executor = declared["host_executor"] or SKILL_EXECUTOR
    elif has_entity:
        executor = declared["host_executor"] or SKILL_INJECTOR
    else:
        executor = declared["host_executor"]

    params = facts.get("params") or {}
    schema_registered = (
        schema_is_registered(facts.get("config_schema"))
        or schema_is_registered(facts.get("output_schema"))
        or (bool(facts.get("has_scripts")) and bool(params))
    )
    status = str(facts.get("status") or "")
    status_ok = status in ("", "approved", "published")
    enabled = bool(facts.get("enabled", True)) and status_ok

    # 技能不是"模型发起的工具调用"：默认 manual（由注入器/执行器按意图或显式调用触发）。
    # 声明可以把它改成 auto（例如将来接上 skill 工具），但默认口径必须如实。
    if "callable_mode" not in decl:
        declared["callable_mode"] = "manual"
    level = declared["permission_level"] or (
        "restricted" if facts.get("is_sensitive") or not enabled else "public")

    # 技能侧：先由**事实**给出机制说明（无实体 / 状态非发布 / 带脚本 / 纯提示词），
    # 声明里的 reason 若更具体则优先；两者都不会被同义句重复堆叠。
    if not has_entity:
        mech = "目录/台账有条目但 data/skills_repo 下无 skill.md 实体（无内容、无执行入口）"
    elif not status_ok:
        mech = f"技能状态为 {status}（非 approved/published）：不会被注入，模型也不发起调用"
    elif facts.get("has_scripts"):
        mech = "带脚本技能：由 SkillExecutor 显式调用执行（不是模型发起的工具调用）"
    else:
        mech = "纯提示词技能：由 ContextInjector 按意图注入，模型不发起调用"

    verdict = judge(
        declared=declared,
        schema_registered=schema_registered, host_executor=executor,
        permission_level=level, is_internal=False, denied=False, deny_all=False,
        enabled=enabled, reason_override=str(declared.get("reason") or mech),
        has_entity=has_entity,
    )

    return {
        "tool_name": sid,
        "tool_type": "skill",
        "scope": "repo",
        "llm_callable": verdict["llm_callable"],
        "callable_mode": declared["callable_mode"],
        "schema_registered": schema_registered,
        "host_executor": executor,
        "permission_level": level,
        "sandbox_allowed": declared["sandbox_allowed"],
        "reason": verdict["reason"],
        "mark": verdict["mark"],
        "reachable": verdict["reachable"],
        "trigger": verdict["trigger"],
        "reason_kind": verdict["reason_kind"],
        "blockers": verdict["blockers"],
        "blocker_codes": verdict["blocker_codes"],
        "soft_blockers": verdict["soft_blockers"],
        "soft_codes": verdict["soft_codes"],
        "conditions": verdict["conditions"],
        "notes": verdict["notes"],
        "declared_llm_callable": declared["llm_callable"],
        "declared_permission_level": declared["permission_level"],
        "derived_permission_level": level,
        "declared_in": facts.get("declared_in", ""),
        "category": "skill",
        "plane": "resident",
        "effect": "read" if not facts.get("has_scripts") else "execute",
        "risk": "medium" if facts.get("is_sensitive") else "low",
        "needs_approval": False,
        "internal": False,
        "enabled": enabled,
        # 技能侧上下文
        "skill_in_repo": bool(facts.get("in_repo")),
        "skill_in_catalog": bool(facts.get("in_catalog")),
        "skill_in_mgmt": bool(facts.get("in_mgmt")),
        "has_scripts": bool(facts.get("has_scripts")),
        "skill_status": str(facts.get("status") or ""),
    }


def runtime_only_skill_entries(*, skill_decl_path: Optional[str] = None,
                               existing_names: Optional[Iterable[str]] = None
                               ) -> List[Dict[str, Any]]:
    """**只在运行时**目录/台账里存在的技能标注（供 REST 层补进界面，不进提交产物）

    【为什么需要它（实测的用户可见后果）】清单口径收紧为"仓库可复现"后，只在运行时存在的
        技能（如 id=`skill`、name=易之三义：内容内联在 `data/skills_mgmt.json`、仓库里没有
        `data/skills_repo/skill/skill.md` 实体）就不在清单里 ⇒ 界面那行**没有徽章**。
        它们的可调用性事实上是可判定的（有 `config_schema`、由 `ContextInjector` 注入），
        与其留白，不如在**运行时**把它们补上并如实标注 `scope=runtime`。
    【不易·边界】本函数只读运行时文件，**不参与** `data/capability_manifest.json` 的生成：
        提交产物必须能从干净 checkout 复算（见 `_skill_sources`），否则 CI `--check` 必红。
        故它只在 REST 端点的请求期调用，`scope` 字段把两类条目的来源分开，界面也据此提示。
    """
    existing = {str(n) for n in (existing_names or ())}
    defaults, decls = load_skill_declarations(skill_decl_path)
    facts = _skill_sources(include_runtime_catalog=True)
    out: List[Dict[str, Any]] = []
    for sid in sorted(facts):
        if sid in existing:
            continue
        fact = facts[sid]
        # 只补"仓库里没有实体、但运行时目录/台账里确实有"的那些
        if fact.get("in_repo") or not (fact.get("in_catalog") or fact.get("in_mgmt")):
            continue
        entry = _skill_entry(sid, fact, decls.get(sid, defaults))
        entry["scope"] = "runtime"
        out.append(entry)
    return out


#: 八项统一字段（清单顶部如实列出，供自动解析方按名取用）
_FIELD_SPEC = (
    "tool_name", "tool_type", "llm_callable", "callable_mode", "schema_registered",
    "host_executor", "permission_level", "sandbox_allowed", "reason",
)


def build_manifest(*, defs_dir: Optional[str] = None,
                   skill_decl_path: Optional[str] = None,
                   policies_path: Optional[str] = None,
                   executor_facts: Optional[Dict[str, str]] = None,
                   include_runtime_catalog: bool = False) -> Dict[str, Any]:
    """构建统一可调用性清单（工具 + 技能，八字段同构）

    Args:
        executor_facts: 运行时注册表的执行器事实 `{能力名: "模块:函数"}`（可选）。
            由**调用方**注入（如 `scripts/sync_capability_manifest.py --runtime`），
            本模块不导入 `agent.tools` —— 见 `runtime_executors()` 的依赖倒置说明。
        include_runtime_catalog: 是否并入运行时技能目录/台账（`data/skills.json`、
            `data/skills_mgmt.json`，两者都被 .gitignore 忽略）。**默认 False**：
            提交的清单必须只依赖入库数据，否则干净 checkout / CI 里重算必然"不一致"
            （见 `_skill_sources` 的说明）。要提交产物就别开这个开关。
    """
    docs = load_tool_docs(defs_dir)
    executors: Dict[str, str] = static_executors()
    if executor_facts:
        executors.update({str(k): str(v) for k, v in executor_facts.items() if v})
    denied, deny_all = denied_tool_names(policies_path)

    tools = [_tool_entry(n, docs[n], executors=executors, denied=denied, deny_all=deny_all)
             for n in sorted(docs)]

    skill_defaults, skill_decls = load_skill_declarations(skill_decl_path)
    facts = _skill_sources(include_runtime_catalog=include_runtime_catalog)
    # 【不易】覆盖表里声明了、但仓库里没有实体的技能（如只在运行时台账/目录里的
    # 指令型技能）**不进清单**：清单口径是"仓库可复现的能力面"，把它们列成 ❌ 会
    # 误读成"技能坏了"。它们改为登记在 runtime_only_declarations 里如实披露。
    overlay_only = sorted(sid for sid in skill_decls if sid not in facts)
    skills = [_skill_entry(sid, facts[sid], skill_decls.get(sid, skill_defaults))
              for sid in sorted(facts)]

    entries = tools + skills
    return {
        "schema_version": 1,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "scope": ("仓库可复现口径：data/tool_definitions/*.yaml + data/skill_callability.yaml "
                  "+ data/skills_repo/*/skill.md + data/permission_policies.json + 注册点静态扫描"
                  "（不含 .gitignore 里的运行时技能目录/台账）"),
        "runtime_only_declarations": overlay_only,
        "runtime_only_note": ("以下技能在 data/skill_callability.yaml 里有声明，但仓库里没有 "
                              "skill.md 实体（只在运行时目录/台账里）⇒ 不在本清单口径内，"
                              "界面按「无徽章」静默退化"),
        "field_spec": list(_FIELD_SPEC),
        "vocabulary": {
            "tool_type": list(TOOL_TYPES),
            "callable_mode": list(CALLABLE_MODES),
            "permission_level": list(PERMISSION_LEVELS),
            "mark": [MARK_CALLABLE, MARK_CONDITIONAL, MARK_BLOCKED],
            "trigger": list(TRIGGERS),
            "reason_kind": list(REASON_KINDS),
        },
        "rule": ("✅ 可被模型发起：有执行器 + 有内容实体 + 未停用 + 权限未拒 + 非内部专用"
                 " + 声明 llm_callable≠false + callable_mode≠manual + 有 JSON Schema"
                 " + 不在审批边界；"
                 "⚠️ 可执行但触发有条件：可达，但需审批（restricted）或不由模型发起"
                 "（manual / 声明 false：由系统或人工触发）/ 缺参数 Schema；"
                 "❌ 不可达：无执行器 / 无内容实体 / 已停用 / 被策略拒绝 / 内部专用。"
                 "注意 `llm_callable` 语义不变：它只回答'模型能不能发起调用'，"
                 "manual 恒为 false；可用性看 `mark`。"),
        "counts": summarize(entries),
        "tools": tools,
        "skills": skills,
        "entries": entries,
    }


def summarize(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """清单统计（按标识 / 触发者 / 类型 / 权限等级 / 判否原因）"""
    def _count(pred) -> int:
        return sum(1 for e in entries if pred(e))

    return {
        "total": len(entries),
        "callable": _count(lambda e: e["mark"] == MARK_CALLABLE),
        "conditional": _count(lambda e: e["mark"] == MARK_CONDITIONAL),
        "blocked": _count(lambda e: e["mark"] == MARK_BLOCKED),
        "model_callable": _count(lambda e: e.get("llm_callable")),
        "by_trigger": {
            t: _count(lambda e, t=t: e.get("trigger") == t) for t in TRIGGERS
        },
        "by_type": {
            t: _count(lambda e, t=t: e["tool_type"] == t) for t in TOOL_TYPES
        },
        "by_permission_level": {
            p: _count(lambda e, p=p: e["permission_level"] == p) for p in PERMISSION_LEVELS
        },
        #: ❌ 的成因（不可达）
        "blocked_reasons": _reason_histogram(entries, "blockers"),
        #: ⚠️ 的成因（可达但不由模型发起 / 缺参数契约）
        "conditional_reasons": _reason_histogram(entries, "soft_blockers"),
    }


def _reason_histogram(entries: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    """原因直方图（按逐条理由计数，一条能力可能占多行）"""
    hist: Dict[str, int] = {}
    for e in entries:
        for b in e.get(field) or []:
            key = b.split("：")[0].split("（")[0].strip()
            hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: (-kv[1], kv[0])))


# ════════════════════════════════════════════════════════════
#  运行时接线：模型可见集过滤
# ════════════════════════════════════════════════════════════


def non_callable_tool_names(*, defs_dir: Optional[str] = None,
                            policies_path: Optional[str] = None) -> FrozenSet[str]:
    """**声明层**判否的工具名（供 `get_tool_defs` 从模型可见集里剔除）

    【为什么只看声明层】运行时逐工具判 schema/执行器会把"注册表此刻缺 schema"这类
            瞬时状态变成"工具突然消失"，那是不可诊断的行为抖动。声明层是稳定的策略，
            且已覆盖 `internal: true`（内部工具）与显式 `llm_callable: false`。
    """
    docs = load_tool_docs(defs_dir)
    denied, deny_all = denied_tool_names(policies_path)
    out: set = set()
    for name, doc in docs.items():
        declared = parse_declaration(doc)
        if not declared["llm_callable"] or declared["callable_mode"] == "manual":
            out.add(name)
        elif _as_bool(doc.get("internal"), False):
            out.add(name)
        elif deny_all or name in denied:
            out.add(name)
    return frozenset(out)


__all__ = [
    "TOOL_TYPES", "CALLABLE_MODES", "PERMISSION_LEVELS", "REQUIRED_DECL_FIELDS",
    "TRIGGERS", "REASON_KINDS",
    "MARK_CALLABLE", "MARK_CONDITIONAL", "MARK_BLOCKED",    "TOOL_DEFS_DIR", "SKILL_CALLABILITY_PATH", "MANIFEST_PATH",
    "SKILLS_REPO_DIR", "SKILLS_JSON_PATH", "SKILLS_MGMT_PATH",
    "schema_is_registered", "static_executors", "runtime_executors",
    "denied_tool_names", "parse_declaration", "load_tool_docs",
    "load_skill_declarations", "effective_permission_level", "judge",
    "build_manifest", "summarize", "non_callable_tool_names",
    "runtime_only_skill_entries",
]
