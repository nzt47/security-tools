"""能力层 HTTP 面 —— 非 LLM 入口（v1.4 §5.3 / §6 / §13，TASK-05 第 4 步）

## 存在的理由（v1.4 的战略判据）

> **把 agent loop 关掉，这套东西还能不能被人、被 CI、被别的系统用起来。**

本模块就是"被别的系统用起来"那一半。端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET  | `/capabilities/tools`          | 全量工具清单（按 tenant + **模型能力**过滤） |
| POST | `/capabilities/invoke`         | 调用一个能力（**复用 `tools.call()`** ⇒ 闸门/审批/审计/限流全生效） |
| POST | `/capabilities/skills/search`  | 语义召回 Top-K（**复用**已有检索栈：chroma + rank-bm25 + RRF） |
| GET  | `/capabilities/<name>`         | 单条能力详情（`describe`） |
| GET  | `/capabilities/health`         | Registry 与 Loader 的状态（**含降级标记**） |

## 三条实现纪律

1. **不绕过闸门**：`/capabilities/invoke` 的执行由
   `agent/capregistry/invoke.py::invoke_capability` 承担，它对本地能力
   **唯一地**调用 `agent/tools/__init__.py::call()`。
2. **HTTP 与 CLI 的 JSON 逐字段一致**：两边的响应体都由
   `agent/capregistry/view.py` / `invoke.py` 的 `*_envelope()` 产出，
   **路由只负责取值与状态码**，不自己拼 JSON（E6 的结构性保证）。
3. **特性开关**：`CP_CAPABILITY_API_ENABLED` 置 0/false/no/off ⇒
   本模块注册的全部端点返回 404，平台行为与改动前完全一致（TASK-05 §6 回滚方案）。

## 鉴权口径（**与同目录既有端点保持一致**）

`agent/server_routes/routes_workflow_learning.py`、`plugins/mcp_scheduler.py` 都带
`@require_token`，而 `routes_background.py` 的四条**不带** —— `TASK-05` §2.3c 把
这个不一致列为须登记项。本模块**选择带 `@require_token`**（与多数口径一致），
但把它放在**开关之后**：开关关闭时路由根本不存在（404），
开关打开而令牌缺失时返回 401。这样"默认不可达"与"默认需鉴权"两条都成立。
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from flask import jsonify, request

logger = logging.getLogger(__name__)

__all__ = ["register_routes", "is_enabled", "ENABLED_ENV"]

#: 特性开关（D5：必须登记在 `agent/settings/registry.py`）
ENABLED_ENV = "CP_CAPABILITY_API_ENABLED"
_DISABLED_VALUES = frozenset({"0", "false", "no", "off"})

#: 默认分页大小（`CP_CAPABILITY_DEFAULT_PAGE`）
#:
#: 【不易·为什么必须有个默认上限（这是压测逼出来的结论）】
#:   `scripts/bench_capregistry.py` 的 **10,000 条合成扩容压测**实测：
#:     无条件全量信封（`list_envelope()` 不加 limit）在 10,000 条下
#:     **p99 = 2244.5ms**，远超 v1.4 附录 C 的 `p99 < 100ms`；
#:     而主键查询 `get()` 的 p99 仅 0.001ms（索引本身没有退化）。
#:   ⇒ 退化的不是"查询"，而是"**一次把 10,000 条全序列化并回传**"这一动作
#:     （每次要点亮约 40 万个 dict 键）。那是**自伤式 DoS**，
#:     而且对调用方毫无用处（没人会读 10,000 条清单）。
#:   ⇒ 故 HTTP 面**默认分页**：不带 `limit` 时按本值截断，
#:     并在响应里给出 `data.truncated=true` 与 `data.total`（**不静默截断**）。
#:     想要真正的全量请显式传 `?limit=<N>`（`N` 上限 2000，见路由层）。
DEFAULT_PAGE_ENV = "CP_CAPABILITY_DEFAULT_PAGE"
_DEFAULT_PAGE = 500


def default_page_size() -> int:
    """默认分页大小（读环境变量；非法值回落到 500）"""
    try:
        raw = os.environ.get(DEFAULT_PAGE_ENV, str(_DEFAULT_PAGE))
        n = int(str(raw).strip() or _DEFAULT_PAGE)
    except (TypeError, ValueError):
        return _DEFAULT_PAGE
    return max(1, min(2000, n))


def is_enabled() -> bool:
    """能力 HTTP 面是否启用（**默认启用**）

    【为什么默认启用】`TASK-05` 的 E1 要求"关掉 LLM 后三条链路仍可用"，
    若默认关闭，这条判据在默认配置下就不成立。回滚方式与仓库既有开关同款：
    置 `0/false/no/off` ⇒ 端点全部 404，平台行为回到改动前。
    读取异常按"启用"处理（与 `agent/tool_gate.py::_approval_enforce_enabled` 同纪律：
    宁可多开一个只读+收口端点，不可因读环境变量失败而静默关掉它）。
    """
    try:
        raw = os.environ.get(ENABLED_ENV, "1")
    except Exception:  # noqa: BLE001
        return True
    return str(raw).strip().lower() not in _DISABLED_VALUES


def _bool_arg(name: str, default: bool = False) -> bool:
    raw = request.args.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    try:
        n = int(request.args.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def register_routes(app, state=None) -> None:  # noqa: ARG001  与既有签名一致
    """注册 `/capabilities/*` 端点（开关关闭时**一个都不注册**）"""
    if not is_enabled():
        logger.info("[capabilities] HTTP 面已关闭（%s=0）⇒ /capabilities/* 不存在",
                    ENABLED_ENV)
        return

    # 令牌装饰器：取不到就退化为"无鉴权"，但**必须**留下明确告警
    # （不静默降级：静默降级正是 routes_background 那个口径问题的成因）
    try:
        from agent.server_auth import require_token as _require_token
    except Exception as exc:  # noqa: BLE001
        logger.error("[capabilities] agent.server_auth.require_token 不可用，"
                     "/capabilities/* 将以**无鉴权**注册（口径不一致，已登记）: %s", exc)

        def _require_token(fn):  # type: ignore[misc]
            return fn

    from agent.capregistry import (IDENTITY_HUMAN, get_loader_manager,
                                   get_registry, invoke_capability)
    # 【TASK-06 §3 第 4 步】`tenant_id` **服务端派生**，不再接受客户端指定。
    # 【不易·本模块第一版自己犯了它在本文件注释里点名批评的错】
    #   下面的 `:220-224` 原本写着"仓库的活 tenant_id 可由客户端在 query/body 指定
    #   （routes_ui_panels.py）—— 那是既有的、已知的信任边界问题（属 TASK-06）。
    #   本模块**不复制**该模式" —— 但同一文件的三处调用点
    #   （`:156` GET query、`:230` POST body、`:276` GET query）**恰恰复制了它**。
    #   TASK-06 复核实测抓到（`git grep 'args.get("tenant_id"'` 三处命中本文件）。
    #   ⇒ 现统一走 `agent/security/tenant.py`（唯一派生入口，D1），
    #     客户端传值只登记为 `tenant_id_declared` 并留痕。
    # 【🔴 2026-09-20 实测回归修复：必须用**能力平面**的租户，而不是 workspace-hash】
    #   本模块第一版改用了 `server_tenant_id()`（= workspace-hash），实测让
    #   `test_capregistry_callpaths_routes.py` 4 条变红：`/capabilities/tools` 返回 0 条、
    #   `describe` 对任何名字 404 —— 因为注册表是按**声明期**的 `default` 建键的
    #   （`data/tool_definitions/*.yaml` 不带 tenant ⇒ `ToolMeta.tenant_id="default"`），
    #   而 workspace-hash 是**运行期**值（且随本机路径变化）。
    #   ⇒ 改用 `capability_tenant_id()` / `capability_tenant_with_declaration()`
    #     （同模块，语义与理由见其 docstring）。"客户端不可伪造"这一目标不变：
    #     生效值仍来自**服务端**，客户端值仍只作待校验声明。
    from agent.security.tenant import (capability_tenant_id,
                                       capability_tenant_with_declaration)

    def _registry():
        """取 Registry（**健康态由 LoaderManager 提供**，Registry 自身保持只读）"""
        try:
            mgr = get_loader_manager()
        except Exception:  # noqa: BLE001  Loader 不可用不该让查询失败
            mgr = None
        provider = mgr.health_of if mgr is not None else None
        return get_registry(health_provider=provider)

    # ── ① 全量清单（按 tenant + 模型能力过滤）────────────────────────────
    @app.route("/capabilities/tools", methods=["GET"])
    @_require_token
    def api_capabilities_tools():
        try:
            reg = _registry()
            raw_limit = (request.args.get("limit") or "").strip()
            if raw_limit == "":
                # 未显式指定 ⇒ 用**默认分页**（理由见 DEFAULT_PAGE_ENV 的注释）
                limit = default_page_size()
                limit_source = "default_page"
            else:
                limit = _int_arg("limit", 0, 0, 2000) or default_page_size()
                limit_source = "explicit"
            # 【TASK-06】tenant 由服务端派生；客户端值只作待校验声明（见顶部说明）
            _tenant, _tenant_declared = capability_tenant_with_declaration(
                request.args.get("tenant_id", ""))
            envelope = reg.list_envelope(
                tenant_id=_tenant,
                namespace=(request.args.get("namespace") or "").strip() or None,
                kind=(request.args.get("kind") or "").strip() or None,
                location=(request.args.get("location") or "").strip() or None,
                owner=(request.args.get("owner") or "").strip() or None,
                impl_status=(request.args.get("impl_status") or "").strip() or None,
                identity=(request.args.get("identity") or "").strip() or None,
                name_contains=(request.args.get("q") or "").strip() or None,
                model=(request.args.get("model") or "").strip(),
                healthy_only=_bool_arg("healthy_only", False),
                enabled_only=_bool_arg("enabled_only", True),
                llm_visible_only=_bool_arg("llm_visible_only", False),
                limit=limit,
                offset=_int_arg("offset", 0, 0, 10_000_000),
            )
            # 【不静默截断】把"被截断了"这件事显式告诉调用方
            data = envelope.get("data") or {}
            data["truncated"] = bool(data.get("total", 0) > data.get("returned", 0))
            data["limit"] = limit
            data["limit_source"] = limit_source
            data["page_size_env"] = DEFAULT_PAGE_ENV
            # 【TASK-06】把"生效租户"与"客户端声明"都回传：前者是判定依据，
            # 后者是留痕（客户端"以为在看租户 X"而实际 Y 这件事必须可见）
            data["tenant_id"] = _tenant
            data["tenant_id_declared"] = _tenant_declared
            envelope["data"] = data
            return jsonify(envelope), 200
        except Exception as exc:  # noqa: BLE001  任何内部错误都不得泄漏原文
            logger.error("[capabilities] /tools 查询失败: %s", exc, exc_info=True)
            return jsonify({
                "status": "error", "code": "internal_error", "data": None,
                "error": {"code": "internal_error",
                          "message": "内部错误，已脱敏；请查看服务端日志定位",
                          "retryable": False},
                "meta": {},
            }), 500

    # ── ② 统一调用（**复用 tools.call()**）────────────────────────────────
    @app.route("/capabilities/invoke", methods=["POST"])
    @_require_token
    def api_capabilities_invoke():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({
                "status": "error", "code": "validation_error", "data": None,
                "error": {"code": "validation_error",
                          "message": "请求体必须是 JSON 对象",
                          "retryable": False}, "meta": {}}), 400
        name = str(body.get("name") or "").strip()
        if not name:
            return jsonify({
                "status": "error", "code": "validation_error", "data": None,
                "error": {"code": "validation_error",
                          "message": "缺少能力名（name）", "retryable": False},
                "meta": {}}), 400
        args = body.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            # 【不易·不要写 `body.get("args") or {}`】空列表 `[]` 是 falsy，
            # `[] or {}` 会静默变成 `{}` ⇒ 非法入参被当成合法、继续往下走
            # （实测踩到：`args=[]` 没报 400，而是一路走到 `not_found`）。
            # 必须显式区分"没给"与"给了但类型不对"。
            return jsonify({
                "status": "error", "code": "validation_error", "data": None,
                "error": {"code": "validation_error",
                          "message": "args 必须是 JSON 对象", "retryable": False},
                "meta": {}}), 400
        # 身份来源：**显式声明优先**，其次请求头，其次缺省 human。
        # 【不易·为什么不从客户端 tenant 参数派生身份】身份与租户**是两件事**：
        #   identity 回答"谁在调"，tenant_id 回答"在哪个租户的范围内调"。
        #   用 tenant 推身份会把"租户标识"提升成"授权凭据"（那是越权的经典入口）。
        #   ⇒ 身份只接受显式字段，缺省落到最保守的 `human`。
        identity = str(body.get("identity")
                       or request.headers.get("X-Yunshu-Identity")
                       or IDENTITY_HUMAN).strip().lower()
        # 【TASK-06 §3 第 4 步】租户**服务端派生**。
        # 【为什么这里用"拒绝式"而不是"纠正式"】本端点是**写路径**（会执行能力），
        #   客户端在 body 里明确声称"我要在租户 X 上执行"；若静默改到派生租户，
        #   调用方会以为写进了 X ⇒ 必须显式拒绝而不是纠正。
        #   （读接口 `/tools` 用纠正式，见该处注释。）
        _tenant = capability_tenant_id()
        _declared = str(body.get("tenant_id") or "").strip()
        if _declared and _declared != _tenant:
            logger.warning("[capabilities] /invoke 拒绝跨租户声明: declared=%r "
                           "effective=%r", _declared, _tenant)
            return jsonify({
                "status": "error", "code": "denied", "data": None,
                "error": {"code": "denied",
                          "message": "tenant_id 由服务端派生，不接受客户端指定；"
                                     "传入值与派生值不符 ⇒ 拒绝",
                          "retryable": False},
                "meta": {"tenant_id": _tenant,
                         "tenant_id_declared": _declared}}), 403
        result = invoke_capability(
            name, args, identity=identity,
            tenant_id=_tenant,
            version=str(body.get("version") or "").strip(),
            registry=_registry(), loader_manager=get_loader_manager())
        return jsonify(result.to_dict()), result.http_status()

    # ── ③ 技能语义召回（**复用**已有检索栈，不新建向量库）─────────────────
    @app.route("/capabilities/skills/search", methods=["POST"])
    @_require_token
    def api_capabilities_skills_search():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"status": "error", "code": "validation_error",
                            "data": None,
                            "error": {"code": "validation_error",
                                      "message": "请求体必须是 JSON 对象",
                                      "retryable": False}, "meta": {}}), 400
        query = str(body.get("query") or "").strip()
        if not query:
            return jsonify({"status": "error", "code": "validation_error",
                            "data": None,
                            "error": {"code": "validation_error",
                                      "message": "缺少 query", "retryable": False},
                            "meta": {}}), 400
        try:
            top_k = max(1, min(int(body.get("top_k") or 5), 50))
        except (TypeError, ValueError):
            top_k = 5
        from agent.capregistry.skillsearch import search_skills
        # 【不易·局部变量不要叫 `env`】`scripts/scan_settings.py` 的"裸 environ 读取"
        # 启发式会把 `env.get("...")` 识别成环境变量读取（`_BARE_ENV_CALLS` 含
        # `env.get`），于是它会把 `"status"` 当成一个未注册的开关报出来 ——
        # 实测踩到（`test_settings_registry.py::test_zero_gap_between_scan_and_registry`
        # 报 `missing: ['GITHUB_JOB', 'status']`）。改名为 `envelope` 即消除该假阳性。
        envelope = search_skills(query, top_k=top_k,
                                 use_vector=bool(body.get("use_vector", True)),
                                 use_bm25=bool(body.get("use_bm25", True)),
                                 use_reranker=bool(body.get("use_reranker", False)))
        code = 200 if envelope.get("status") == "ok" else 503
        return jsonify(envelope), code

    # ── ④ 单条能力详情 ──────────────────────────────────────────────────
    @app.route("/capabilities/<name>", methods=["GET"])
    @_require_token
    def api_capabilities_describe(name: str):
        reg = _registry()
        # 【TASK-06 §3 第 4 步】租户服务端派生。单条详情**读取**语义 ⇒ 用纠正式
        # （按派生值处理 + 留痕），与 `/tools` 一致；写路径 `/invoke` 用拒绝式。
        _tenant, _declared = capability_tenant_with_declaration(
            request.args.get("tenant_id", ""))
        envelope = reg.describe_envelope(name, tenant_id=_tenant)
        code = 200 if envelope["status"] == "ok" else 404
        if isinstance(envelope.get("data"), dict):
            envelope["data"]["tenant_id"] = _tenant
            envelope["data"]["tenant_id_declared"] = _declared
        return jsonify(envelope), code

    # ── ⑤ 状态（含降级标记；E5 的观测点）─────────────────────────────────
    @app.route("/capabilities/health", methods=["GET"])
    @_require_token
    def api_capabilities_health():
        reg = _registry()
        try:
            loaders = get_loader_manager().describe()
        except Exception as exc:  # noqa: BLE001
            loaders = {"error": f"{type(exc).__name__}: {exc}"}
        return jsonify({
            "status": "ok",
            "code": "ok",
            "data": {
                "registry": reg.stats(),
                "loaders": loaders,
                "conflicts": reg.name_conflicts(),
            },
            "error": None,
            "meta": {"api_enabled": True},
        }), 200

    logger.info("[capabilities] 非 LLM 入口已注册 "
                "(/capabilities/tools、/capabilities/invoke、"
                "/capabilities/skills/search、/capabilities/<name>、"
                "/capabilities/health)")
