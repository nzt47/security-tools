# plugins/ingestion.py
"""吞噬（ingestion）HTTP 薄层：只做鉴权 / 信封 / 参数校验，调域模块。

端点（P0）：

- `POST /api/ingestion/preview`：来源 → 适配器 → 三级判定 → 可预览计划（dry-run）；
- `POST /api/ingestion/commit`：按已预览的 `plan_id` + 显式 `confirm=true` 落库。

约定（与 plugins/skills.py 的 PLAN-1 §4 同源）：

- 本模块顶层只 import flask / plugin_api / 轻量模型；`agent.ingestion.service`
  在视图函数内延迟 import（重依赖都在 service 内部懒加载）；
- 业务判定、归一化、安装全部在 `agent.ingestion` 域模块里，本文件不写第二套口径；
- 变更型接口一律 `@require_auth`（与 experience.py 同款），未确认/未预览不写文件。

【为什么 commit 不接收 plan 对象】服务端不保存计划（无状态）。commit 只收
`plan_id`，由域模块重算预览并比对 —— 这样重启不失效，也不会"按旧计划装新内容"。
"""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, request

from agent.api_envelope import ok as _ok, problem as _problem
from agent.ingestion.models import IngestionError, IngestSource, InvalidSource

from .plugin_api import Plugin, register_plugin, require_auth as _require_token

bp = Blueprint("ingestion", __name__)

#: 单个请求体上限（防止把超大 JSON 读进内存；正文上限另由 service 的 TEXT 限制兜底）
_MAX_REQUEST_BYTES = 1_000_000


def _fail(exc: IngestionError):
    """领域错误 → RFC 9457 problem（状态码/类型/标题由域模型单点定义）。"""
    detail = exc.detail
    scores = getattr(exc, "scores", None)
    if scores:
        detail = (detail + "；打分：" if detail else "打分：") + "、".join(
            f"{name}={score}" for name, score in sorted(scores.items()))
    return _problem(int(exc.status), title=exc.title, detail=detail, type_slug=exc.code)


def _json_body() -> Dict[str, Any]:
    if request.content_length and request.content_length > _MAX_REQUEST_BYTES:
        from agent.ingestion.models import SourceTooLarge
        raise SourceTooLarge(
            f"请求体超过 {_MAX_REQUEST_BYTES} 字节上限",
            detail="请改用本机路径（source.path），不要通过 HTTP 上传超大正文。",
        )
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise InvalidSource("请求体必须是 JSON 对象")
    return body


def _source_from_body(body: Dict[str, Any]) -> IngestSource:
    """接受 `{"source": ...}`，也接受顶层 `path` / `text`（便于 CLI 直接调）。"""
    raw = body.get("source")
    if raw is None:
        if "path" in body or "text" in body:
            raw = {"path": body.get("path"), "text": body.get("text"),
                   "label": body.get("label"),
                   "ecosystem": body.get("ecosystem") or body.get("ecosystem_hint")}
        else:
            raise InvalidSource("缺少 source（或顶层 path / text）")
    return IngestSource.from_mapping(raw)


def _plan_payload(plan: Any) -> Dict[str, Any]:
    return {"plan": plan.to_dict()}


def _resolve_llm_client() -> Any:
    """运行时 LLM 客户端（P2）。

    【为什么在插件层解析】app_server 的 `_Yunshu._llm` 是进程内真实实例；
    域模块不应 import app_server（循环/分层红线）。插件层允许**首次请求时**
    延迟 import app_server（与 plugin_api 的 require_token 回退同一做法），
    取不到再回退域模块的 `default_llm_client()`；两者都取不到 = None，
    preview(rewrite=true) 会如实失败并写存档，绝不伪造。
    """
    try:
        import app_server  # noqa: PLC0415 首次请求时 app_server 已加载
        yunshu = getattr(app_server, "_Yunshu", None)
        for attr in ("_llm", "llm", "llm_service"):
            client = getattr(yunshu, attr, None) if yunshu is not None else None
            if client is not None:
                return client
    except Exception:  # noqa: BLE001 app_server 未加载/未就绪不致命
        pass
    try:
        from agent.ingestion import service
        return service._default_llm_client()  # noqa: SLF001 同包域入口
    except Exception:  # noqa: BLE001
        return None


@bp.route("/api/ingestion/preview", methods=["POST"])
@_require_token
def api_ingestion_preview():
    """吞噬预览：识别来源并生成 L0/L1/L2 计划；rewrite=true 时额外生成重写草稿与存档。"""
    try:
        body = _json_body()
        source = _source_from_body(body)
        rewrite = body.get("rewrite") is True
        from agent.ingestion import service
        plan = service.preview(source, rewrite=rewrite, dedupe=True,
                               llm_client=_resolve_llm_client() if rewrite else None)
    except IngestionError as exc:
        return _fail(exc)
    if rewrite and plan.archive_id:
        message = ("重写草稿已生成并写存档（请人工预览后带 archive_id 提交）"
                   if plan.rewritten else
                   "重写未通过校验，原文/输出已存档（见 rewrite_errors）")
    elif rewrite:
        message = "来源已是 L0/L1，已忽略 rewrite（未调用 LLM、未写存档）"
    else:
        message = "预览已生成（未写任何文件）"
    return _ok(_plan_payload(plan), message=message)


@bp.route("/api/ingestion/sources", methods=["GET"])
@_require_token
def api_ingestion_sources():
    """吞噬源探测：列出配置根（默认 claude/openclaw/trae）下的可预览候选，只读不写。"""
    from agent.ingestion import sources
    data = sources.discover_sources()
    return _ok({"sources": data}, message="已探测本机吞噬源（只读）")


@bp.route("/api/ingestion/commit", methods=["POST"])
@_require_token
def api_ingestion_commit():
    """吞噬落库：按 plan_id 安装；L2 需带 preview(rewrite=true) 生成的 archive_id。"""
    try:
        body = _json_body()
        source = _source_from_body(body)
        plan_id = str(body.get("plan_id") or "").strip()
        if not plan_id:
            raise InvalidSource("缺少 plan_id（先调用 /api/ingestion/preview）")
        from agent.ingestion import service
        result = service.commit(
            source,
            plan_id=plan_id,
            confirm=body.get("confirm") is True,
            archive_id=str(body.get("archive_id") or "").strip(),
            rewrite=body.get("rewrite") is True,
            merge_into=str(body.get("merge_into") or "").strip(),
            form=str(body.get("form") or "skill").strip().lower(),
        )
    except IngestionError as exc:
        return _fail(exc)
    message = "已生成草稿（未安装/未启用）" if result.draft else "已安装为云枢技能"
    return _ok({"result": result.to_dict()}, message=message)


@bp.route("/api/ingestion/drafts", methods=["GET"])
@_require_token
def api_ingestion_drafts():
    """列出吞噬生成的骨架草稿（隔离区）。

    Why 单独一个端点：tool/workflow 草稿**不注册、不进 /api/skills-mgmt/queue**，
    用户生成后去「草稿评审」看不到任何东西。评审页据此展示真实产物 + 目录 + 警告。
    """
    from agent.ingestion import drafts
    try:
        items = drafts.list_drafts()
    except Exception as exc:  # noqa: BLE001 读取失败要如实报错，不静默返回空列表
        return _problem(500, title="读取草稿失败", detail=type(exc).__name__,
                        type_slug="draft-list-failed")
    return _ok({"items": items, "total": len(items)})


@bp.route("/api/ingestion/drafts/<draft_id>", methods=["DELETE"])
@_require_token
def api_ingestion_draft_delete(draft_id: str):
    """删除一个骨架草稿目录（不可恢复；只删隔离区，按目录 listing 等值匹配）。"""
    from agent.audit.facade import audit
    from agent.ingestion import drafts
    try:
        ok = drafts.delete_draft(draft_id)
    except Exception as exc:  # noqa: BLE001
        return _problem(500, title="删除草稿失败", detail=type(exc).__name__,
                        type_slug="draft-delete-failed")
    if not ok:
        return _problem(404, title="草稿不存在", detail="按 draft_id 未找到可删除的草稿目录",
                        type_slug="draft-not-found")
    try:
        audit.record("ingestion.draft_delete", subject=draft_id,
                     payload={"draft_id": draft_id})
    except Exception:  # noqa: BLE001 删除已生效，审计失败不回滚（只丢失一条记录）
        pass
    return _ok({"deleted": draft_id})


@bp.route("/api/ingestion/drafts/<draft_id>/install-skill", methods=["POST"])
@_require_token
def api_ingestion_draft_install_skill(draft_id: str):
    """把草稿的来源**按技能形态**重新安装（技能才有评审/放行链路）。

    Why：workflow/tool 草稿是骨架，不能直接执行，也不进技能评审队列；
    对"本来就是说明型技能"的来源（如 Claude SKILL.md），按技能安装才是正确出口。
    """
    from agent.audit.facade import audit
    from agent.ingestion import drafts, service
    try:
        body = _json_body()
    except IngestionError as exc:
        return _fail(exc)
    if body.get("confirm") is not True:
        return _problem(400, title="需要显式确认", detail="安装技能会写真实技能库；请带 confirm=true",
                        type_slug="confirm-required")
    try:
        data = drafts.load_manifest(draft_id)
    except FileNotFoundError as exc:
        return _problem(404, title="草稿不存在", detail=str(exc), type_slug="draft-not-found")
    ref = str(data["manifest"].get("source_ref") or "")
    if not ref.startswith("path:"):
        return _problem(400, title="来源不可重装",
                        detail="该草稿来源不是本机路径（文本来源没有保留原文），无法按技能重装",
                        type_slug="source-not-path")
    path = ref[5:].strip()
    try:
        plan = service.preview(path)
        existing = [d for d in (plan.dedupe or []) if d.get("match") in ("id", "name")]
        if existing and body.get("overwrite") is not True:
            names = "、".join(str(d.get("id") or d.get("name")) for d in existing[:3])
            return _problem(409, title="技能已存在", detail="已存在同 id/同名技能：" + names +
                            "；确认覆盖请带 overwrite=true", type_slug="skill-exists")
        result = service.commit(path, plan_id=plan.plan_id, confirm=True, form="skill")
    except IngestionError as exc:
        return _fail(exc)
    except Exception as exc:  # noqa: BLE001 安装链路异常：只回类型名（原文进日志）
        return _problem(500, title="按技能安装失败", detail=type(exc).__name__,
                        type_slug="install-skill-failed")
    try:
        audit.record("ingestion.draft_install_skill", subject=draft_id,
                     payload={"draft_id": draft_id, "skill_id": result.skill_id,
                              "source_ref": ref, "overwrite": body.get("overwrite") is True})
    except Exception:  # noqa: BLE001 审计失败不回滚安装
        pass
    return _ok({"result": result.to_dict()})


@bp.route("/api/ingestion/drafts/<draft_id>/autobind", methods=["POST"])
@_require_token
def api_ingestion_draft_autobind(draft_id: str):
    """AI 自动绑定：把步骤描述映射到真实工具 + 参数模板（默认 dry-run）。

    - apply=false：只回提议（写盘前先给人看）；
    - apply=true 且 confirm=true：写回草稿 JSON 与 manifest（不注册、不启用）。
    """
    from agent.audit.facade import audit
    from agent.ingestion import autobind, drafts
    try:
        body = _json_body()
    except IngestionError as exc:
        return _fail(exc)
    try:
        data = drafts.load_workflow(draft_id)
    except FileNotFoundError as exc:
        return _problem(404, title="草稿不存在或不是工作流", detail=str(exc),
                        type_slug="draft-not-found")
    except ValueError as exc:
        return _problem(400, title="草稿内容非法", detail=str(exc), type_slug="draft-invalid")
    try:
        workflow, report = autobind.propose(data["workflow"], _resolve_llm_client())
    except autobind.BindError as exc:
        return _problem(int(exc.status), title="自动绑定失败", detail=exc.detail,
                        type_slug="autobind-failed")
    saved = None
    if body.get("apply") is True:
        if body.get("confirm") is not True:
            return _problem(400, title="需要显式确认",
                            detail="写回绑定结果需要 confirm=true（会改草稿 JSON）",
                            type_slug="confirm-required")
        saved = drafts.save_workflow(draft_id, workflow,
                                     warnings=autobind.binding_warnings(report))
        try:
            audit.record("ingestion.draft_autobind", subject=draft_id,
                         payload={"draft_id": draft_id, "bound": report["bound"],
                                  "total": report["total"], "model": report.get("model", "")})
        except Exception:  # noqa: BLE001
            pass
    return _ok({"report": report, "workflow": workflow, "saved": saved})


@bp.route("/api/ingestion/drafts/<draft_id>/register", methods=["POST"])
@_require_token
def api_ingestion_draft_register(draft_id: str):
    """把已全部绑定工具的草稿登记为工作流（默认停用；需 confirm）。"""
    from agent.audit.facade import audit
    from agent.ingestion import autobind, drafts
    try:
        body = _json_body()
    except IngestionError as exc:
        return _fail(exc)
    if body.get("confirm") is not True:
        return _problem(400, title="需要显式确认", detail="登记会写工作流仓库；请带 confirm=true",
                        type_slug="confirm-required")
    try:
        data = drafts.load_workflow(draft_id)
    except FileNotFoundError as exc:
        return _problem(404, title="草稿不存在或不是工作流", detail=str(exc),
                        type_slug="draft-not-found")
    except ValueError as exc:
        return _problem(400, title="草稿内容非法", detail=str(exc), type_slug="draft-invalid")
    try:
        wf = autobind.build_learned_workflow(data["workflow"], draft_id=draft_id)
    except autobind.BindError as exc:
        return _problem(int(exc.status), title="还不能登记", detail=exc.detail,
                        type_slug="register-rejected")
    except ValueError as exc:  # 工作流 id/字段不合法（pydantic 校验）
        return _problem(400, title="工作流信息不合法", detail=str(exc),
                        type_slug="register-invalid")
    # 必须走运行中的 service 单例（仓库带内存缓存；另起 repo 写盘服务看不到，
    # 2026-10-26 E2E 实测：登记 200 但列表查不到、删除 404）
    from agent.state_manager import get_workflow_learning_service
    try:
        svc = get_workflow_learning_service()
        if svc.repo.get(wf.id) is not None:
            return _problem(409, title="工作流 id 已存在",
                            detail="工作流仓库里已有 " + wf.id + "；请改名或先删除旧工作流",
                            type_slug="workflow-exists")
        svc.register_workflow(wf)
    except Exception as exc:  # noqa: BLE001 写仓库失败：只回类型名
        return _problem(500, title="登记失败", detail=type(exc).__name__,
                        type_slug="register-failed")
    try:
        audit.record("ingestion.draft_register", subject=draft_id,
                     payload={"draft_id": draft_id, "workflow_id": wf.id,
                              "steps": len(wf.steps), "enabled": wf.enabled})
    except Exception:  # noqa: BLE001
        pass
    return _ok({"workflow_id": wf.id, "name": wf.name, "steps": len(wf.steps),
                "enabled": wf.enabled, "status": wf.status})


@bp.route("/api/ingestion/drafts/<draft_id>/implement", methods=["POST"])
@_require_token
def api_ingestion_draft_implement(draft_id: str):
    """tool 草稿：AI 生成实现 + 静态校验（默认 dry-run，apply 需 confirm）。"""
    from agent.audit.facade import audit
    from agent.ingestion import drafts, toolbuild
    try:
        body = _json_body()
    except IngestionError as exc:
        return _fail(exc)
    try:
        manifest = drafts.load_manifest(draft_id)["manifest"]
    except FileNotFoundError as exc:
        return _problem(404, title="草稿不存在", detail=str(exc), type_slug="draft-not-found")
    if str(manifest.get("form") or "") != "tool":
        return _problem(400, title="形态不支持",
                        detail="只有 tool 草稿支持 AI 实现；workflow 请用 AI 绑定",
                        type_slug="form-mismatch")
    try:
        result = toolbuild.build(draft_id, _resolve_llm_client())
    except toolbuild.BuildError as exc:
        return _problem(int(exc.status), title="AI 实现失败", detail=exc.detail,
                        type_slug="implement-failed")
    saved = None
    if body.get("apply") is True:
        if body.get("confirm") is not True:
            return _problem(400, title="需要显式确认",
                            detail="写回实现需要 confirm=true（会改草稿文件）",
                            type_slug="confirm-required")
        try:
            saved = toolbuild.apply(draft_id, result["code"], result["validation"])
        except toolbuild.BuildError as exc:
            return _problem(int(exc.status), title="写回失败", detail=exc.detail,
                            type_slug="implement-apply-failed")
        try:
            audit.record("ingestion.draft_implement", subject=draft_id,
                         payload={"draft_id": draft_id, "ok": bool(result["validation"].get("ok")),
                                  "model": result.get("model", "")})
        except Exception:  # noqa: BLE001
            pass
    return _ok({"validation": result["validation"], "model": result.get("model", ""),
                "prompt_chars": result.get("prompt_chars", 0),
                "code": result.get("code", ""), "saved": saved})


@bp.route("/api/ingestion/drafts/<draft_id>/register-tool", methods=["POST"])
@_require_token
def api_ingestion_draft_register_tool(draft_id: str):
    """tool 草稿：登记为动态工具（custom/ + 台账 + 治理 YAML），需 confirm。"""
    from agent.audit.facade import audit
    from agent.ingestion import drafts, toolbuild
    try:
        body = _json_body()
    except IngestionError as exc:
        return _fail(exc)
    if body.get("confirm") is not True:
        return _problem(400, title="需要显式确认",
                        detail="登记会写动态工具目录与治理声明；请带 confirm=true",
                        type_slug="confirm-required")
    try:
        manifest = drafts.load_manifest(draft_id)["manifest"]
    except FileNotFoundError as exc:
        return _problem(404, title="草稿不存在", detail=str(exc), type_slug="draft-not-found")
    if str(manifest.get("form") or "") != "tool":
        return _problem(400, title="形态不支持",
                        detail="只有 tool 草稿支持登记为工具；workflow 请用登记为工作流",
                        type_slug="form-mismatch")
    name = str(manifest.get("skill_id") or "")
    try:
        from agent.tools import list_tools
        exists = any(str(t.get("name")) == name for t in (list_tools() or []))
    except Exception:  # noqa: BLE001 注册表不可用时不阻断，交给 generate_persistent 校验
        exists = False
    if exists and body.get("overwrite") is not True:
        return _problem(409, title="工具已存在",
                        detail="注册表里已有 " + name + "；确认覆盖请带 overwrite=true",
                        type_slug="tool-exists")
    try:
        data = toolbuild.register(draft_id)
    except toolbuild.BuildError as exc:
        return _problem(int(exc.status), title="登记工具失败", detail=exc.detail,
                        type_slug="register-tool-failed")
    try:
        audit.record("ingestion.draft_register_tool", subject=draft_id,
                     payload={"draft_id": draft_id, "tool_name": data["tool_name"],
                              "module": data["module"]})
    except Exception:  # noqa: BLE001
        pass
    return _ok(data)


@bp.route("/api/ingestion/governance/scan", methods=["POST"])
@_require_token
def api_ingestion_governance_scan():
    """存量资产治理盘点（只读）：技能/重复/孤儿/无用 + 结构化问题。"""
    from agent.ingestion import governance
    try:
        body = _json_body()
        try:
            limit = int(body.get("limit") or 500)
        except (TypeError, ValueError):
            limit = 500
        data = governance.scan(limit=max(1, min(limit, 500)))
    except IngestionError as exc:
        return _fail(exc)
    return _ok({"scan": data}, message="治理盘点完成（只读）")


@bp.route("/api/ingestion/governance/plan", methods=["POST"])
@_require_token
def api_ingestion_governance_plan():
    """生成确定性治理计划：merge / archive / split / refactor / delete_orphans。"""
    from agent.ingestion import governance
    try:
        body = _json_body()
        try:
            limit = int(body.get("limit") or 500)
        except (TypeError, ValueError):
            limit = 500
        data = governance.plan(limit=max(1, min(limit, 500)))
    except IngestionError as exc:
        return _fail(exc)
    return _ok({"plan": data}, message="治理计划已生成（未执行）")


@bp.route("/api/ingestion/governance/apply", methods=["POST"])
@_require_token
def api_ingestion_governance_apply():
    """执行一条治理动作：默认 dry-run；真执行必须 confirm=true。"""
    from agent.ingestion import governance
    try:
        body = _json_body()
        plan_id = str(body.get("plan_id") or "").strip()
        action = str(body.get("action") or "").strip()
        target = str(body.get("target") or "").strip()
        if not plan_id or not action or not target:
            raise InvalidSource("缺少 plan_id / action / target（先调用 governance/plan）")
        data = governance.apply(
            plan_id=plan_id, action=action, target=target,
            confirm=body.get("confirm") is True,
            dry_run=body.get("dry_run") is not False,
        )
    except IngestionError as exc:
        return _fail(exc)
    except ValueError as exc:
        return _fail(InvalidSource(str(exc)))
    message = "dry-run：未改任何数据" if data.get("dry_run") else "治理动作已执行并审计"
    return _ok({"result": data}, message=message)


PLUGIN = register_plugin(Plugin(
    name="ingestion",
    version="0.6.0",
    description="吞噬：外来资产获取（sources/preview/commit）+ 存量资产治理（governance/scan|plan|apply）",
    blueprint=bp,
))
