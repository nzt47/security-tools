"""分身 (Subagent) API 路由

提供分身的创建、查询、执行、销毁等 REST API。

【两条执行路径的区别（2026-09-19 补）】
  - ``POST /api/subagent/<name>/execute``：走 ``SubagentContainer.execute()`` —— 设计上
    保持"占位骨架"（**不调 LLM、不做委派协议**，只回一段占位文案）。前端若接这条，
    表现就是"点了委托执行、返回 200 但什么都没干" ⇒ 即"子代理没跑通"。
  - ``POST /api/subagent/<name>/delegate``：走 ``SubagentContainer.run_delegation()``
    → ``DelegationExecutor``（八要素校验 → 工具裁剪 → 隔离执行 → Trace → 成本记账 →
    回收三件套），与模型侧 ``delegate`` 工具**同一条真执行链路**。UI 用这条。
"""

import logging
import uuid

from flask import request, jsonify
from agent.server_auth import require_token, log_request
from agent.server_routes.tracing_decorator import trace_route

logger = logging.getLogger(__name__)


def _channel_info(Yunshu) -> dict:
    """委派执行通道可用性（真委派需要 LLM 或外部 agent CLI；缺一即拒绝执行）

    与 ``agent/tools/subagent_tools.py::_run_delegate`` 步骤 6 同一判据：没有执行通道时
    **明确拒绝**，不跑注定失败的空执行。UI 据此提前提示，避免用户点了"委托执行"
    却只拿到占位文案（那正是"子代理没跑通"的表象）。
    """
    llm = getattr(Yunshu, "_llm", None)
    cli = ""
    try:
        from agent.subagent.channel import default_agent_cli
        cli = str(default_agent_cli() or "")
    except Exception:  # noqa: BLE001 通道探测失败按"无 CLI"处理
        cli = ""
    return {
        "llm": llm is not None,
        "cli": bool(cli),
        "agent_cli": cli,
        "ok": llm is not None or bool(cli),
    }


def _outcome_payload(outcome, ctx) -> dict:
    """``ExecutionOutcome`` → JSON（复用工具侧同一份字段映射，避免两套口径漂移）

    工具侧 ``subagent_tools._result_from_outcome`` 是委派结果对外形状的**单一来源**，
    这里优先复用；极端情况下（导入失败）退化为最小字段集，不让接口 500。
    """
    try:
        from agent.tools.subagent_tools import _result_from_outcome
        return _result_from_outcome(outcome, ctx)
    except Exception as e:  # noqa: BLE001 映射失败降级（不阻断响应）
        logger.debug("[SubagentAPI] 结果映射降级: %s", e)
        return {
            "ok": bool(getattr(outcome, "ok", False)),
            "delegation_id": str(getattr(outcome, "delegation_id", "") or ""),
            "tier": str(getattr(outcome, "tier", "") or ""),
            "duration_ms": round(float(getattr(outcome, "duration_ms", 0.0) or 0.0), 2),
            "trace_id": str(getattr(outcome, "trace_id", "") or ""),
            "result": str(getattr(outcome, "output_text", "") or ""),
            "error_code": str(getattr(outcome, "error_code", "") or ""),
            "error": str(getattr(outcome, "error", "") or ""),
        }


def _as_str_list(value, field: str, *, allow_empty: bool) -> list:
    """请求字段归一化为字符串列表（单串按 1 元素处理）"""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} 必须是字符串列表")
    out = [str(v).strip() for v in value if str(v).strip()]
    if not out and not allow_empty:
        raise ValueError(f"{field} 不得为空列表（无约束的委派等同于未声明边界）")
    return out


def register_routes(app, state):
    """注册所有分身管理路由"""

    Yunshu = state.Yunshu

    # ═══════════════════════════════════════════════════
    #  列表 & 状态
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/list")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_list():
        """获取所有活跃分身列表（附带委派执行通道可用性，供 UI 提前提示）"""
        try:
            subagents = Yunshu.list_subagents()
            return jsonify({
                "ok": True,
                "subagents": subagents,
                "count": len(subagents),
                # UI 用：通道不可用时"委托执行"必然失败，提前提示而不是让用户白点
                "channel": _channel_info(Yunshu),
            })
        except Exception as e:
            logger.error("[SubagentAPI] 列表查询失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/<name>")
    @trace_route("Subagent")
    @log_request(show_response=False)
    def api_subagent_get(name):
        """获取指定分身详情"""
        try:
            subagent = Yunshu.get_subagent(name)
            if subagent is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404
            return jsonify({"ok": True, "subagent": subagent})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  创建 & 销毁
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/create", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_create():
        """创建新分身

        POST JSON:
            name (str): 分身名称（唯一）
            model_id (str): LLM 模型 ID
            memory_provider (str): 记忆提供商
            tool_sources (list[str], optional): 工具源列表
            permissions (list[str], optional): 权限列表（默认 ['read']）
            context_window (int, optional): 上下文窗口大小（默认 4096）
            tags (list[str], optional): 标签
            ttl_seconds (int, optional): 存活时间（0=永久）
        """
        try:
            data = request.get_json() or {}

            required = ["name", "model_id", "memory_provider"]
            missing = [k for k in required if k not in data]
            if missing:
                return jsonify({"ok": False, "error": f"缺少必要字段: {missing}"}), 400

            config = {
                "name": data["name"],
                "model_id": data["model_id"],
                "memory_provider": data["memory_provider"],
                "tool_sources": data.get("tool_sources", []),
                "permissions": data.get("permissions", ["read"]),
                "context_window": data.get("context_window", 4096),
                "tags": data.get("tags", []),
                "ttl_seconds": data.get("ttl_seconds", 0),
            }

            container = Yunshu.create_subagent(config)
            return jsonify({
                "ok": True,
                "subagent": container.get_status(),
                "message": f"分身 '{container.config.name}' 创建成功",
            })
        except Exception as e:
            logger.error("[SubagentAPI] 创建失败: %s", e)
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.route("/api/subagent/<name>/destroy", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_destroy(name):
        """销毁指定分身"""
        try:
            report = Yunshu.destroy_subagent(name)
            return jsonify({
                "ok": True,
                "report": report,
                "message": f"分身 '{name}' 已销毁",
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════
    #  执行 & 热更新
    # ═══════════════════════════════════════════════════

    @app.route("/api/subagent/<name>/execute", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_execute(name):
        """在指定分身中执行任务

        **注意**：本端点走 ``SubagentContainer.execute()`` —— 设计上保持"占位骨架"
        （不调 LLM、不做委派协议），只回一段占位文案，供集成测试验证容器链路。
        需要**真执行**请用 ``POST /api/subagent/<name>/delegate``（同一容器，
        但走 DelegationExecutor 完整链路）。

        POST JSON:
            task (str): 任务描述
        """
        try:
            data = request.get_json() or {}
            task = data.get("task", "").strip()
            if not task:
                return jsonify({"ok": False, "error": "task 不能为空"}), 400

            result = Yunshu.execute_subagent(name, task)
            return jsonify({
                "ok": True,
                "name": name,
                "result": result,
            })
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 404
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route("/api/subagent/<name>/delegate", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_delegate(name):
        """**真委派**：把任务交给指定分身，经真实执行器跑（八要素 + 隔离 + Trace + 成本）

        与 ``/execute``（容器占位骨架，不调 LLM）不同，本端点走
        ``SubagentContainer.run_delegation`` → ``DelegationExecutor``，
        与模型侧 ``delegate`` 工具同一条链路。

        POST JSON（八要素；UI 简化入口，未传的项按下方缺省补齐并随响应回显）：
            task            ①目标（必填，≥8 字符；含糊目标会被执行器拒绝）
            constraints     ②约束（列表，缺省 ["只读为主，不得修改仓库文件"]，不得为空列表）
            prior_artifacts ③已有成果（列表，缺省 []）
            prohibitions    ④禁止事项（列表，缺省 []；UI 额外默认补"不得对外发送数据"）
            artifact_format ⑤产物格式（缺省 "文本要点"）
            budget_tokens   ⑥预算令牌（缺省 4000）
            timeout_seconds ⑦超时秒数（缺省 120）
            callback_url    ⑧回调地址（缺省 ""，即同步返回）
        """
        try:
            data = request.get_json(silent=True) or {}
            task = str(data.get("task") or data.get("goal") or "").strip()
            if len(task) < 8:
                return jsonify({
                    "ok": False, "error_code": "E_DELEGATION_INCOMPLETE",
                    "error": "目标过短：至少 8 字符且需具体可判定（含糊目标会被拒绝）",
                }), 400

            # ── 分身解析：用**既有**生命周期管理器取容器（绝不 new 第二实例） ──
            mgr = getattr(Yunshu, "_subagent_mgr", None)
            if mgr is None or not callable(getattr(mgr, "get", None)):
                return jsonify({
                    "ok": False, "error_code": "E_SUBAGENT_UNAVAILABLE",
                    "error": "分身系统未启用（subagent.enabled=False 或核心系统未初始化）",
                }), 409
            container = mgr.get(name)
            if container is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404

            # ── 执行通道：LLM 优先；都没有则明确拒绝（不跑注定失败的空执行） ──
            channel = _channel_info(Yunshu)
            if not channel["ok"]:
                return jsonify({
                    "ok": False, "error_code": "E_DELEGATION_NO_CHANNEL",
                    "error": ("未配置执行通道（既无 LLM 也无外部 agent CLI）："
                              "_llm 为空且环境变量 CP_SUBAGENT_AGENT_CLI 未设置"),
                    "channel": channel,
                }), 409

            # ── 八要素（未传项按文档缺省补齐；校验在 DelegationExecutor 内继续生效） ──
            constraints = _as_str_list(data.get("constraints"), "constraints", allow_empty=False) \
                or ["只读为主，不得修改仓库文件"]
            prohibitions = _as_str_list(data.get("prohibitions"), "prohibitions", allow_empty=True)
            if not prohibitions:
                prohibitions = ["不得对外发送数据"]
            prior_artifacts = _as_str_list(data.get("prior_artifacts"), "prior_artifacts", allow_empty=True)
            try:
                budget_tokens = int(data.get("budget_tokens") or 4000)
                timeout_seconds = int(data.get("timeout_seconds") or 120)
            except (TypeError, ValueError):
                return jsonify({"ok": False, "error": "budget_tokens / timeout_seconds 必须是整数"}), 400

            from agent.subagent.delegation import DelegationContext
            ctx = DelegationContext(
                goal=task,
                constraints=constraints,
                prior_artifacts=prior_artifacts,
                prohibitions=prohibitions,
                artifact_format=str(data.get("artifact_format") or "文本要点"),
                budget_tokens=budget_tokens,
                timeout_seconds=timeout_seconds,
                # ⑧回调地址：八要素校验只要求"已声明"（空串即不合格 ⇒ E_DELEGATION_INCOMPLETE）。
                # UI 是同步调用、结果直接由本响应返回，故用本地占位标识：投递器缺省只记审计，
                # 不会真的外呼；如需真投递，调用方显式传 callback_url 即可。
                callback_url=str(data.get("callback_url") or "ui://workbench/sync"),
                delegation_id=f"dlg-ui-{uuid.uuid4().hex[:8]}",
            )

            # 工具子集与模型侧 delegate 工具同源（同一只读白名单，避免两套授权口径）
            try:
                from agent.tools.subagent_tools import _default_subagent_tools
                granted = _default_subagent_tools()
            except Exception as e:  # noqa: BLE001 白名单不可用 → 纯推理委派（不授予工具）
                logger.warning("[SubagentAPI] 工具白名单不可用，按纯推理委派执行: %s", e)
                granted = ()

            logger.info("[SubagentAPI] 真委派 name=%s delegation=%s goal=%.60s tools=%s",
                        name, ctx.delegation_id, task, list(granted) or "（空）")
            outcome = container.run_delegation(
                ctx,
                llm=getattr(Yunshu, "_llm", None),
                tools=granted,
                authorized_capabilities=granted,
            )
            payload = _outcome_payload(outcome, ctx)
            payload["name"] = name
            payload["elements"] = {
                "goal": ctx.goal,
                "constraints": list(ctx.constraints),
                "prior_artifacts": list(ctx.prior_artifacts),
                "prohibitions": list(ctx.prohibitions),
                "artifact_format": ctx.artifact_format,
                "budget_tokens": ctx.budget_tokens,
                "timeout_seconds": ctx.timeout_seconds,
                "callback_url": ctx.callback_url,
            }
            payload["channel"] = channel
            return jsonify(payload)
        except Exception as e:
            logger.exception("[SubagentAPI] 委派失败: %s", e)
            return jsonify({"ok": False, "error_code": "E_DELEGATION_FAILED",
                            "error": str(e)}), 500

    @app.route("/api/subagent/<name>/reload", methods=["POST"])
    @trace_route("Subagent")
    @require_token
    @log_request()
    def api_subagent_reload(name):
        """热更新分身配置

        POST JSON:
            model_id (str, optional): 新模型 ID
            memory_provider (str, optional): 新记忆提供商
            tool_sources (list[str], optional): 新工具源
            permissions (list[str], optional): 新权限
            context_window (int, optional): 新上下文窗口大小
            ttl_seconds (int, optional): 新存活时间
        """
        try:
            data = request.get_json() or {}

            # 获取当前配置作为基础
            current = Yunshu.get_subagent(name)
            if current is None:
                return jsonify({"ok": False, "error": f"分身不存在: {name}"}), 404

            new_config = {
                "name": name,
                "model_id": data.get("model_id", current["model_id"]),
                "memory_provider": data.get("memory_provider", current["memory_provider"]),
                "tool_sources": data.get("tool_sources", current["tool_sources"]),
                "permissions": data.get("permissions", current["permissions"]),
                "context_window": data.get("context_window", current["context_window"]),
                "tags": data.get("tags", current.get("tags", [])),
                "ttl_seconds": data.get("ttl_seconds", current.get("ttl_seconds", 0)),
            }

            Yunshu.hot_reload_subagent(name, new_config)
            updated = Yunshu.get_subagent(name)
            return jsonify({
                "ok": True,
                "subagent": updated,
                "message": f"分身 '{name}' 热更新完成",
            })
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500
