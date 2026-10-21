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
                   "label": body.get("label")}
        else:
            raise InvalidSource("缺少 source（或顶层 path / text）")
    return IngestSource.from_mapping(raw)


def _plan_payload(plan: Any) -> Dict[str, Any]:
    return {"plan": plan.to_dict()}


@bp.route("/api/ingestion/preview", methods=["POST"])
@_require_token
def api_ingestion_preview():
    """吞噬预览：识别来源并生成可预览的 L0/L1/L2 计划，不落库、不执行来源脚本。"""
    try:
        source = _source_from_body(_json_body())
        from agent.ingestion import service
        plan = service.preview(source)
    except IngestionError as exc:
        return _fail(exc)
    return _ok(_plan_payload(plan), message="预览已生成（未写任何文件）")


@bp.route("/api/ingestion/commit", methods=["POST"])
@_require_token
def api_ingestion_commit():
    """吞噬落库：按已预览的 plan_id 安装；L2 需 P2 重写，P0 明确拒绝。"""
    try:
        body = _json_body()
        source = _source_from_body(body)
        plan_id = str(body.get("plan_id") or "").strip()
        if not plan_id:
            raise InvalidSource("缺少 plan_id（先调用 /api/ingestion/preview）")
        from agent.ingestion import service
        result = service.commit(source, plan_id=plan_id, confirm=body.get("confirm") is True)
    except IngestionError as exc:
        return _fail(exc)
    return _ok({"result": result.to_dict()}, message="已安装为云枢技能")


PLUGIN = register_plugin(Plugin(
    name="ingestion",
    version="0.1.0",
    description="吞噬：外来资产（技能包/文档/提示词）→ 可预览的云枢技能计划（preview/commit）",
    blueprint=bp,
))
