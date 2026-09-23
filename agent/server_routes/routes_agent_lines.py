"""主线管理 HTTP 面（主线档案 CRUD + 装配预览）

【任务定位】
    `agent/lines/` 已经把「能力平面 → 主线档案 → 本轮工具集」这条链做成了
    纯数据驱动，本模块**只做 HTTP 投影**，让"看 / 建 / 改 / 激活 / 删"这五件事
    不用改代码（与 `routes_skills`/`routes_ui_panels` 同款：模块只提供 JSON，
    页面由 `yunshu-ui/src/pages/hub/tools/lines.tsx` 渲染）：

    | 层 | 唯一权威 |
    |---|---|
    | L1 工具原子 | `data/tool_definitions/*.yaml`（plane/effect/risk/tags） |
    | L2 主线档案 | `data/agent_lines/*.yaml`（平面权重 + 保底 + 效果上限 + 技能包） |

【为什么必须有 /preview】
    "改权重即时看效果"是这条链唯一能被人理解的地方。装配结果本身就是
    **可解释的 trace**（保底入选 / 打分入选 / 被效果上限拒绝 / 被 mute / 被截断），
    所以本模块把它**原样**回给前端，而不是让前端自己算一遍——前端算一遍就等于
    出现了第二份装配口径，两份口径迟早会分叉。

【写路径不新增权限实现】
    与 `routes_skills` 同款：读端点不挂令牌，写端点一律 `@require_token`。
    删除是**破坏性动作**，除令牌外还要求显式 `confirm: true`——这与
    `LineRegistry.delete(confirm=True)` 自身的纪律同源，本层不绕过、不放宽。
    `preview` / `validate` 是**纯计算、零副作用**（不落盘、不改激活指针），
    故与 GET 同级，不挂令牌：否则一旦启用 FLASK_API_TOKEN，"调权重看效果"
    这一核心动作会退化成 401。

【错误映射】
    `LineRegistryError` → 400；消息含"不存在 / not found" → 404；
    消息含"已存在" → 409；`ValueError`（档案字典非法）→ 400；
    其余未预期异常 → 500（带异常文本，不吞）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, request

from agent.lines import (
    EFFECTS,
    PLANES,
    RISKS,
    LineProfile,
    LineRegistryError,
    assemble,
    get_line_registry,
    known_skill_ids,
    load_tool_meta,
    resolve_skill_pack,
)
from agent.server_auth import log_request, require_token
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)

#: 四平面文案（UI 单一来源：前端不再自造中文名，避免两处各说各话）
PLANE_LABELS: Dict[str, str] = {
    "resident": "常驻",
    "perceive": "感知",
    "act": "行动",
    "govern": "治理",
}
PLANE_HINTS: Dict[str, str] = {
    "resident": "每轮必发（高频低 token）",
    "perceive": "只读取，不改变世界",
    "act": "改变世界",
    "govern": "改变云枢自身能力集 ⇒ 天然就是审批边界",
}

#: effect 偏序文案：read < write < execute < extend（越靠后后果越大）
EFFECT_LABELS: Dict[str, str] = {
    "read": "只读",
    "write": "写入",
    "execute": "执行",
    "extend": "扩展能力",
}

RISK_LABELS: Dict[str, str] = {
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}

#: 档案默认值（与 `LineProfile` 的 dataclass 默认一致，供 UI "新建"预填）
DEFAULT_PROFILE: Dict[str, Any] = {
    "id": "",
    "name": "",
    "description": "",
    "enabled": True,
    "plane_weights": {"resident": 1.0, "perceive": 1.0, "act": 1.0},
    "plane_floors": {"resident": 2, "perceive": 2, "act": 2},
    "boost": [],
    "mute": [],
    "tags": [],
    "max_tools": 20,
    "effect_allow": ["read", "write", "execute"],
    "requires_approval": [],
    "allow_govern": False,
    "skills": [],
    "prompt_note": "",
}


# ════════════════════════════════════════════════════════════
#  内部工具
# ════════════════════════════════════════════════════════════


def _json_body() -> Dict[str, Any]:
    """读取 JSON 请求体（非对象体一律拒绝：这里没有"顺便兼容"的余地）"""
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValueError("请求体必须是 JSON 对象")
    return body


def _fail(message: str, status: int = 400, **extra: Any) -> Tuple[Any, int]:
    """统一失败响应：`{"ok": false, "error": ...}` + HTTP 码"""
    payload: Dict[str, Any] = {"ok": False, "error": str(message)}
    payload.update({k: v for k, v in extra.items() if v is not None})
    return jsonify(payload), status


def _error_response(exc: BaseException) -> Tuple[Any, int]:
    """异常 → HTTP 状态码（映射规则见模块 docstring）"""
    message = str(exc)
    if isinstance(exc, LineRegistryError):
        lowered = message.lower()
        if "不存在" in message or "not found" in lowered:
            return _fail(message, 404)
        if "已存在" in message or "exists" in lowered:
            return _fail(message, 409)
        return _fail(message, 400)
    if isinstance(exc, ValueError):
        return _fail(message, 400)
    logger.exception("[AgentLines] 未预期异常")
    return _fail(f"{type(exc).__name__}: {message}", 500)


def _runtime_tool_names() -> List[str]:
    """运行时可挂载的工具名（`agent.tools.list_tools()`，已注册的全部）"""
    try:
        from agent import tools as T
        return [str(t.get("name")) for t in T.list_tools() if t.get("name")]
    except Exception as e:  # noqa: BLE001 注册表不可用不得让整条链挂掉
        logger.warning("[AgentLines] 工具注册表读取失败: %s", e)
        return []


def _meta_and_available() -> Tuple[Dict[str, Any], List[str], str]:
    """装配所需的两个输入：工具元数据 + 候选工具名（附来源，便于如实标注）

    【不易】候选集优先取**运行时注册表**（真正会被装配的那批）。注册表为空时
            （独立脚本/测试进程：`agent.tools` 的工具模块尚未被导入）退回
            "已声明即存在"，否则预览会把全部工具判成 `denied_unknown`，
            给出一个看起来很像结论、实际只是环境没加载的误导结果。
    """
    meta = load_tool_meta()
    names = _runtime_tool_names()
    if names:
        return meta, names, "registry"
    return meta, sorted(meta.keys()), "declarations"


#: 可调用性清单缓存（按 mtime+size 失效，避免每次请求读 100KB JSON）
_CALLABILITY_CACHE: Dict[str, Any] = {"stamp": None, "data": {}}


def _callability_index() -> Dict[str, Dict[str, Any]]:
    """读 `data/capability_manifest.json` → {能力名: 标注}

    【为什么读清单文件而不是现算】清单是**同源派生**的（`agent/lines/callability.py`），
            由 `scripts/sync_capability_manifest.py` 生成并受 `--check` 守门；
            请求线程里现算要重扫 91 个 YAML + AST，得不偿失。
    【不易】文件缺失/损坏 ⇒ 返回空表（界面退化为"无标注"，绝不因标注不可用而 500）。
    """
    import json
    import os as _os
    try:
        from agent.lines.callability import MANIFEST_PATH
    except Exception as e:  # noqa: BLE001
        logger.debug("[AgentLines] 可调用性模块不可用: %s", e)
        return {}
    try:
        st = _os.stat(MANIFEST_PATH)
    except OSError:
        return {}
    stamp = (st.st_mtime_ns, st.st_size)
    if _CALLABILITY_CACHE["stamp"] == stamp:
        return _CALLABILITY_CACHE["data"]
    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except (OSError, ValueError) as e:
        logger.warning("[AgentLines] 可调用性清单不可读: %s", e)
        return {}
    index = {
        str(e.get("tool_name")): e
        for e in (doc.get("entries") or []) if e.get("tool_name")
    }
    _CALLABILITY_CACHE["stamp"] = stamp
    _CALLABILITY_CACHE["data"] = index
    return index


def _preview_dict(profile: LineProfile, meta: Dict[str, Any],
                  available: List[str]) -> Dict[str, Any]:
    """跑一次装配并给出可解释 trace（前端直接上屏，不做二次计算）

    【为什么 `tools_meta` 覆盖"payload 里出现的**全部**工具名"而不只是入选工具】
        预览面板用同一份 meta 渲染各组 chip（入选 / 需人工确认 / 被效果上限拒绝 /
        被 mute / 被截断 / 无声明 fail-closed 拒绝）。只给入选工具的话，其余 chip
        既没有 plane 也没有可调用性标注 —— 而"被拒绝的那个工具是什么等级"恰恰是
        这条 trace 最需要看清楚的部分。
    【为什么 callability 必须与目录端点同源】标注的权威是 `data/capability_manifest.json`
        （由 `agent/lines/callability.py` 派生的产物）。预览与目录各算一次就会出现两份
        口径（这正是"十三处工具真相"的老毛病），故两处都读同一份缓存（`_callability_index`）。
    【不易】清单缺失/损坏 ⇒ `callability` 为空对象，前端退化为"无徽章"，**不报错**。
    """
    result = assemble(profile, available, meta=meta)
    payload = result.to_dict()
    callability = _callability_index()

    names: set = set(result.tools)
    for key in ("by_plane", "denied_by_effect", "denied_unknown",
                "muted", "truncated", "needs_approval"):
        value = payload.get(key)
        if isinstance(value, dict):
            for items in value.values():
                names.update(items or [])
        elif isinstance(value, list):
            names.update(value)

    payload["tools_meta"] = {
        name: {**meta[name].to_dict(), "callability": callability.get(name, {})}
        for name in sorted(names)
        if name in meta
    }
    payload["callability_source"] = "data/capability_manifest.json"
    return payload


def _issues_of(profile: LineProfile, meta: Dict[str, Any]) -> List[str]:
    """档案校验问题（空 = 通过）；已知工具集/技能目录为空时只做结构校验

    【为什么技能也要传真实目录】与工具同款口径：写错的技能 id 必须在这里被
    指出（`引用了未注册的技能: [...]`），否则它只会在运行时静默不生效。
    目录读不到（空集）⇒ 传 None，退回结构校验 —— 环境故障不该被说成数据错误。
    """
    known = set(meta.keys()) or None
    known_skills = set(known_skill_ids()) or None
    return list(profile.validate(known_tools=known, known_skills=known_skills))


def _prompt_fragments_payload(profile: LineProfile) -> Dict[str, Any]:
    """这条档案会注入系统提示词的**片段**（当前只有 role=line）——HTTP 投影。

    为什么由后端投影而不是前端自己判断：前端再判一遍就等于出现第二份"什么会被
    注入提示词"的口径，与系统提示词的真实组装结果迟早分叉（和 /preview 之所以
    由后端算装配结果完全同理）。判定本身的唯一实现在
    `agent/orchestrator/prompt_builder.py::line_fragment_for_profile`
    —— 与运行时装配（`line_prompt_fragment`）共用同一个函数，不另立口径。

    Returns:
        {"prompt_fragments": [...], "prompt_fragments_note": str}
        - 有片段：列表一项（role / source / content / chars / priority / croppable），
          note 为空串；
        - 无片段：列表为空，note 给人读原因（未启用 / prompt_note 为空 / 取用降级）。

    注：本函数只描述"这条档案自己会产生什么片段"；**是否真的生效**还取决于
    这条线是不是当前激活线、且保存内容与预览的草案一致 —— 前者由
    `GET /api/agent-lines` 的 `active` 给出，后者是前端自己手里的 draft 状态。
    """
    try:
        from agent.orchestrator.prompt_builder import line_fragment_for_profile
        from agent.prompt_manager.roles import is_croppable_role

        frag = line_fragment_for_profile(profile)
    except Exception as e:  # noqa: BLE001 片段信息不得影响预览本身
        logger.warning("[agent-lines] prompt 片段信息降级（预览照常）: %s", e)
        return {
            "prompt_fragments": [],
            "prompt_fragments_note": "片段信息不可用（已降级）：%s" % (e,),
        }
    if frag is None:
        if not getattr(profile, "enabled", False):
            note = "本线已停用（enabled=false）⇒ 按未装线处理，不注入任何 role=line 片段"
        elif not (getattr(profile, "prompt_note", "") or "").strip():
            note = ("prompt_note 为空 ⇒ 不注入 role=line 片段；"
                    "系统提示词与未装线时逐字一致")
        else:
            note = "本档案不产生 role=line 片段"
        return {"prompt_fragments": [], "prompt_fragments_note": note}
    return {
        "prompt_fragments": [{
            "role": frag.role,
            "source": frag.source,
            "content": frag.content,
            "chars": len(frag.content),
            "priority": frag.priority,
            "croppable": is_croppable_role(frag.role),
        }],
        "prompt_fragments_note": "",
    }


def _num(value: Any, default: float = 0.0) -> float:
    """宽容取数（仅用于生成提示文案，失败即回默认值）"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_notes(raw: Dict[str, Any], profile: LineProfile) -> List[str]:
    """如实说明 `LineProfile.__post_init__` 做了哪些自动规范化

    为什么单独列出来：这两条规范化是**刻意的自洽性保护**（治理平面必须能过
    effect 上限；allow_govern=False 时不留 govern 权重）。用户填完看到结果里
    多了/少了东西，必须能在这里找到原因，而不是怀疑系统有 bug。
    """
    notes: List[str] = []
    raw_effect_allow = [str(e) for e in (raw.get("effect_allow") or [])]
    if bool(raw.get("allow_govern", False)) and "extend" not in raw_effect_allow:
        notes.append(
            "allow_govern=True：已自动把 extend 加入 effect_allow"
            "（否则治理平面会被效果上限过滤成空集，「允许治理」就成了静默失效的开关）")
    raw_weights = raw.get("plane_weights")
    if not bool(raw.get("allow_govern", False)) and isinstance(raw_weights, dict):
        if any(str(k).strip().lower() == "govern" and _num(v) > 0
               for k, v in raw_weights.items()):
            notes.append(
                "allow_govern=False：已丢弃 govern 平面权重"
                "（治理平面 = 审批边界，必须显式开启）")
    return notes


# ════════════════════════════════════════════════════════════
#  路由注册
# ════════════════════════════════════════════════════════════


def register_routes(app: Any, state: Any = None) -> None:  # noqa: ARG001
    """把主线管理路由注册到 Flask app（与既有 `server_routes/*` 同款模式）"""

    # ── 1. 主线列表（含当前激活指针） ──

    @app.route("/api/agent-lines", methods=["GET"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_agent_lines_list():
        """全部主线档案 + 当前激活的主线（`active: null` = 不装线，全量工具）"""
        try:
            reg = get_line_registry()
            loaded = reg.load_all()
            lines = [loaded[k].to_dict() for k in sorted(loaded)]
            # 损坏的档案如实回报（load_all 只记 warning，不静默吞掉）
            broken = sorted(set(reg.list_ids()) - set(loaded.keys()))
            return jsonify({
                "ok": True,
                "active": reg.get_active(),
                "lines": lines,
                "broken": broken,
                "defaults": DEFAULT_PROFILE,
                "note": "active=null 表示不装线（全量工具）",
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 2. 四平面 / 效果 / 风险 分类法 + 工具目录（供 UI 建选择器） ──

    @app.route("/api/agent-lines/planes", methods=["GET"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_agent_lines_planes():
        """分类法单一来源 + 全量工具目录 + 未登记（fail-closed）工具清单

        每行工具附带 `callability`：该工具的「可被 LLM 调用」统一标注
        （`llm_callable` / `callable_mode` / `schema_registered` / `host_executor` /
        `permission_level` / `sandbox_allowed` / `reason` / `mark`），口径与
        `data/capability_manifest.json` 同源（见 agent/lines/callability.py）。
        """
        try:
            meta, available, source = _meta_and_available()
            callability = _callability_index()
            counts: Dict[str, int] = {p: 0 for p in PLANES}
            tools: List[Dict[str, Any]] = []
            for name in sorted(meta.keys()):
                m = meta[name]
                counts[m.plane] = counts.get(m.plane, 0) + 1
                row = m.to_dict()
                row["description"] = m.description[:120]
                row["callability"] = callability.get(name, {})
                tools.append(row)
            # 运行时存在、但没有 plane/effect 声明 ⇒ 装配时 fail-closed 拒绝
            undeclared = sorted(n for n in available if n not in meta)
            marks: Dict[str, int] = {}
            for row in tools:
                mark = str((row["callability"] or {}).get("mark") or "")
                if mark:
                    marks[mark] = marks.get(mark, 0) + 1
            return jsonify({
                "ok": True,
                "planes": [
                    {"key": p, "label": PLANE_LABELS[p], "hint": PLANE_HINTS[p],
                     "count": counts.get(p, 0)}
                    for p in PLANES
                ],
                "plane_counts": counts,
                "effects": [
                    {"key": e, "label": EFFECT_LABELS[e], "rank": i}
                    for i, e in enumerate(EFFECTS)
                ],
                "effect_order": list(EFFECTS),
                "effect_note": "effect 是偏序：read < write < execute < extend"
                               "（档案的 effect_allow 是「上限」，不是「只允许这几项」）",
                "risks": [
                    {"key": r, "label": RISK_LABELS[r], "rank": i}
                    for i, r in enumerate(RISKS)
                ],
                "tools": tools,
                "tool_count": len(tools),
                "tools_without_declaration": undeclared,
                "tool_source": source,
                "callability_marks": marks,
                "callability_note": "✅ 可被模型发起 / ⚠️ 可执行但触发有条件"
                                    "（需人工确认，或由系统·人工触发，如技能）"
                                    " / ❌ 不可达（无执行器·已停用·被策略拒绝）",
                "defaults": DEFAULT_PROFILE,
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 2b. 统一可调用性清单（工具 + 技能同构；派生自权威数据） ──

    @app.route("/api/capability-manifest", methods=["GET"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_capability_manifest():
        """工具 / 技能的「可被 LLM 调用」统一清单（`data/capability_manifest.json`）

        只读投影：数据由 `scripts/sync_capability_manifest.py` 从
        `data/tool_definitions/*.yaml` + `data/skill_callability.yaml` 等权威数据派生，
        `--check` 守门防止手改。

        另附 `runtime_skills`：**只在运行时**目录/台账里存在的技能标注（每条的
        `scope` 为 `runtime`）。提交产物必须能从干净 checkout 复算，故这类技能不在清单文件里；
        但界面需要给它们标识（否则那几行没有徽章，实测 id=`skill`（易之三义）就是如此），
        于是在请求期就地补算并单独成字段 —— 两类来源靠 `scope` 区分，不混成一份口径。
        """
        try:
            from agent.lines.callability import MANIFEST_PATH, runtime_only_skill_entries
        except Exception as e:  # noqa: BLE001
            return _fail(f"可调用性模块不可用: {e}", 500)
        import json
        import os as _os
        if not _os.path.exists(MANIFEST_PATH):
            return _fail("可调用性清单不存在，请运行 scripts/sync_capability_manifest.py", 404)
        try:
            with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            return _fail(f"可调用性清单不可读: {e}", 500)

        runtime_skills: List[Dict[str, Any]] = []
        try:
            existing = {str(e.get("tool_name")) for e in (doc.get("skills") or [])}
            runtime_skills = runtime_only_skill_entries(existing_names=existing)
        except Exception as e:  # noqa: BLE001 运行时补标注失败不得让清单端点挂掉
            logger.warning("[AgentLines] 运行时技能标注补算失败（界面将缺这几行的徽章）: %s", e)

        st = _os.stat(MANIFEST_PATH)
        return jsonify({
            "ok": True,
            "manifest": doc,
            "runtime_skills": runtime_skills,
            "runtime_note": "运行时目录/台账里才有的技能（scope=runtime）：随进程可见性变化，"
                            "不参与清单文件的 CI 守门；界面按同一套判定显示标识",
            "path": _os.path.relpath(MANIFEST_PATH, _os.path.dirname(_os.path.dirname(
                _os.path.dirname(_os.path.abspath(__file__))))).replace("\\", "/"),
            "updated_at": st.st_mtime,
        })

    # ── 3. 单条主线 + 装配预览（"这条线现在会长什么样"） ──

    @app.route("/api/agent-lines/<line_id>", methods=["GET"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_agent_lines_detail(line_id: str):
        """单条档案 + 现场装配预览（不保存、不改状态）"""
        try:
            reg = get_line_registry()
            profile = reg.load(line_id)
            if profile is None:
                return _fail(f"主线不存在: {line_id}", 404)
            meta, available, source = _meta_and_available()
            return jsonify({
                "ok": True,
                "line": profile.to_dict(),
                "preview": _preview_dict(profile, meta, available),
                "issues": _issues_of(profile, meta),
                "tool_source": source,
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 4. 装配预览（未保存的档案也能算：改权重即时看效果） ──

    @app.route("/api/agent-lines/preview", methods=["POST"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_agent_lines_preview():
        """按请求体里的档案跑一次装配（**零副作用**：不落盘、不动激活指针）

        响应除工具段外还带 `skills`：这条线的技能包判定（白名单/不限制 +
        allowed + unknown + 人读原因）。**纯计算、不挂令牌**：与工具预览同级，
        否则"调权重看效果"会退化成 401（口径见模块 docstring）。
        """
        try:
            body = _json_body()
            profile = LineProfile.from_dict(body)
            meta, available, source = _meta_and_available()
            preview = _preview_dict(profile, meta, available)
            issues = _issues_of(profile, meta)
            return jsonify({
                "ok": True,
                "line_id": profile.id,
                "line": profile.to_dict(),
                "preview": preview,
                "skills": resolve_skill_pack(profile).to_dict(),
                "issues": issues,
                "notes": _normalize_notes(body, profile),
                "tool_source": source,
                "saved": False,
                # 会注入系统提示词的片段（role=line 的内容与来源），与运行时同源；
                # 前端据此渲染，不再自行判断"会不会注入"
                **_prompt_fragments_payload(profile),
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 5. 新建主线（已存在 ⇒ 409，绝不静默覆盖） ──

    @app.route("/api/agent-lines", methods=["POST"])
    @trace_route("AgentLines")
    @require_token
    @log_request()
    def api_agent_lines_create():
        """新建主线档案（`registry.create`：overwrite=False，冲突即失败）"""
        try:
            body = _json_body()
            line_id = str(body.get("id") or "").strip()
            if not line_id:
                return _fail("缺少主线 id", 400)
            reg = get_line_registry()
            if reg.load(line_id) is not None:
                return _fail(f"主线已存在: {line_id}", 409)
            profile = reg.create(body)
            return jsonify({"ok": True, "line": profile.to_dict(),
                            "created": profile.id})
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 6. 保存 / 覆盖单条主线 ──

    @app.route("/api/agent-lines/<line_id>", methods=["PUT"])
    @trace_route("AgentLines")
    @require_token
    @log_request()
    def api_agent_lines_save(line_id: str):
        """保存主线档案（upsert：路径即身份，body 里的 id 不允许与路径冲突）"""
        try:
            body = _json_body()
            body_id = str(body.get("id") or "").strip()
            if body_id and body_id != line_id:
                return _fail(
                    f"请求体 id（{body_id}）与路径 id（{line_id}）不一致："
                    "改名请用「新建 + 删除」，不做静默改名", 400)
            body["id"] = line_id
            profile = LineProfile.from_dict(body)
            reg = get_line_registry()
            existed = reg.load(line_id) is not None
            reg.save(profile)
            return jsonify({"ok": True, "line": profile.to_dict(),
                            "created": not existed,
                            "note": "已覆盖保存" if existed else "已新建保存"})
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 7. 删除主线（破坏性：必须显式 confirm） ──

    @app.route("/api/agent-lines/<line_id>", methods=["DELETE"])
    @trace_route("AgentLines")
    @require_token
    @log_request()
    def api_agent_lines_delete(line_id: str):
        """删除主线档案；**必须** body 里带 `{"confirm": true}`"""
        try:
            body = _json_body()
            if not body.get("confirm"):
                return _fail(
                    "删除主线需要 confirm=true（破坏性操作，不做静默删除）", 400)
            reg = get_line_registry()
            deleted = reg.delete(line_id, confirm=True)
            if not deleted:
                return _fail(f"主线不存在: {line_id}", 404)
            return jsonify({"ok": True, "deleted": line_id,
                            "active": reg.get_active(),
                            "note": "若删除的是当前激活主线，激活指针已自动清空"})
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 8. 设置当前激活主线（null = 不装线，全量工具） ──

    @app.route("/api/agent-lines/active", methods=["POST"])
    @trace_route("AgentLines")
    @require_token
    @log_request()
    def api_agent_lines_active():
        """设置全局激活主线：`{"line_id": "<id>"}` 或 `{"line_id": null}`"""
        try:
            body = _json_body()
            if "line_id" not in body:
                return _fail("缺少 line_id（传 null 表示不装线、回到全量工具）", 400)
            target = body.get("line_id")
            if target is not None:
                if not isinstance(target, str):
                    return _fail("line_id 必须是字符串或 null", 400)
                target = target.strip() or None
            reg = get_line_registry()
            reg.set_active(target)
            return jsonify({
                "ok": True,
                "active": reg.get_active(),
                "note": "null = 不装线（全量工具）",
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)

    # ── 9. 校验档案（只报问题，不保存） ──

    @app.route("/api/agent-lines/validate", methods=["POST"])
    @trace_route("AgentLines")
    @log_request(show_response=False)
    def api_agent_lines_validate():
        """跑 `LineProfile.validate(known_tools, known_skills)` 并返回问题列表（**不保存**）

        技能段与 `/preview` 同源：`skills` 给出该档案的技能包判定，`issues` 里
        会逐条指出未注册的技能 id（写错 id 不再静默失效）。
        """
        try:
            body = _json_body()
            profile = LineProfile.from_dict(body)
            meta, _available, source = _meta_and_available()
            issues = _issues_of(profile, meta)
            return jsonify({
                "ok": True,
                "valid": not issues,
                "issues": issues,
                "line": profile.to_dict(),
                "skills": resolve_skill_pack(profile).to_dict(),
                "notes": _normalize_notes(body, profile),
                "tool_source": source,
                "saved": False,
                # 与 /preview 同源：两个端点给同一份"片段"判定
                **_prompt_fragments_payload(profile),
            })
        except Exception as e:  # noqa: BLE001
            return _error_response(e)


__all__ = ["register_routes", "PLANE_LABELS", "EFFECT_LABELS", "RISK_LABELS"]
