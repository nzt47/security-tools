"""工具注册 — 把"过程蒸馏"暴露为云枢可自主调用的工具。

注册 2 个工具：
    1. distill_process_from_knowledge（同步 / 异步同一入口）
        知识库/素材 → 蒸馏 → workflow/skill 固化。
        async_=false（默认）同步返回结果（短任务）；
        async_=true 提交到 AsyncExecutor 后台执行，立即返回 task_id 不阻塞对话，
        用既有 get_task_status/get_task_result 轮询取结果（原 distill_process_async）。
    2. process_distill_run（内部，异步任务的实际执行体）
        供 AsyncExecutor submit(tool_name="process_distill_run") 调用，
        也可由任何注册工具复用作"纯蒸馏执行"。
        **必须保留注册**：AsyncExecutor 经 `call()` 按名调用，
        而 `call()` 要求名字在 `_registry` 中；它只从模型可见集隐藏
        （data/tool_definitions/process_distill_run.yaml 的 `internal: true`）。

注册方式（与 knowledge/tools.py 同风格，显式注册不侵入全局）：
    from agent.process_distill.tools import register_distill_tools
    register_distill_tools()
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from agent.process_distill.service import ProcessDistillService

logger = logging.getLogger(__name__)

# 对外注册的工具名。process_distill_run **必须保留注册**（AsyncExecutor 经 call()
# 按名调用，而 call() 要求名字在 _registry 中），只是不进模型可见集（YAML internal: true）。
_NAMES = ["distill_process_from_knowledge", "process_distill_run"]

# 历史名字：distill_process_async 已并入 distill_process_from_knowledge(async_=True)。
# 保留在清理列表里只为**热重载幂等**——同一进程内热重载时把旧注册从 _registry 摘掉，
# 否则它会作为幽灵工具继续对模型可见（新进程本就不会有它）。
_LEGACY_NAMES = ["distill_process_async"]


# ═══════════════════════════════════════════════════════════════
#  参数解析与共享执行
# ═══════════════════════════════════════════════════════════════

def _parse_params(kw: Dict[str, Any]) -> Dict[str, Any]:
    """从工具入参解析标准参数（容错：paths 允许字符串/列表）。"""
    query = str(kw.get("query") or "").strip()
    raw_paths = kw.get("paths") or []
    if isinstance(raw_paths, str):
        raw_paths = [raw_paths]
    paths = [str(p) for p in raw_paths if str(p).strip()]
    artifacts = kw.get("artifacts")
    if artifacts is not None:
        if isinstance(artifacts, str):
            artifacts = [artifacts]
        artifacts = [str(a) for a in artifacts]
    return {
        "query": query,
        "paths": paths,
        "artifacts": artifacts,
        "top_k": int(kw.get("top_k") or 5),
        "max_workers": int(kw.get("max_workers") or 4),
    }


def _slim_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """结果瘦身：素材全文不返回给 LLM 上下文/HTTP 响应。"""
    if result.get("ok"):
        result = dict(result)
        result["materials"] = [
            {k: m[k] for k in ("id", "title", "source_ref") if k in m}
            for m in result.get("materials", [])
        ]
    return result


def _execute_distill(kw: Dict[str, Any],
                     svc: Optional[ProcessDistillService] = None,
                     slim: bool = True) -> Dict[str, Any]:
    """执行蒸馏（同步核心，供 sync/async/internal 三个工具共用）。"""
    try:
        if svc is None:
            svc = ProcessDistillService()
        p = _parse_params(kw)
        if not p["query"] and not p["paths"]:
            return {"ok": False,
                    "error": "query 与 paths 至少提供一个（没有素材无法蒸馏）"}
        result = svc.distill(
            query=p["query"], paths=p["paths"], artifacts=p["artifacts"],
            top_k=p["top_k"], max_workers=p["max_workers"],
        )
        return _slim_result(result) if slim else result
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001  工具级兜底
        logger.exception("[PD] distill 执行异常")
        return {"ok": False, "error": f"蒸馏执行异常: {e}"}


# ═══════════════════════════════════════════════════════════════
#  工具注册
# ═══════════════════════════════════════════════════════════════

_SYNC_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string",
                  "description": "知识库 wiki 检索关键词（可空）"},
        "paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "素材文件/目录路径列表（可空，如 ['docs/xxx.md']"
                           " 或外部 SKILL.md 目录）",
        },
        "artifacts": {
            "type": "array",
            "items": {"type": "string", "enum": ["workflow", "skill"]},
            "description": "固化产物类型，默认 ['workflow','skill']",
        },
        "top_k": {"type": "integer", "description": "wiki 检索召回数，默认 5"},
        "max_workers": {"type": "integer",
                        "description": "并行子代理数，默认 4"},
    },
}

# 面向模型的那个入口（distill_process_from_knowledge）= _SYNC_SCHEMA + 异步开关
# + 原 distill_process_async 独有的 `name`（任务名），否则合并会丢能力。
# 注意：process_distill_run 仍用 _SYNC_SCHEMA，其 YAML 不受本次合并影响。
_ASYNC_SCHEMA = {
    **_SYNC_SCHEMA,
    "properties": {
        **_SYNC_SCHEMA["properties"],
        "async_": {
            "type": "boolean",
            "description": "是否提交后台异步执行（默认 false=同步返回结果）；"
                           "true 时立即返回 task_id 不阻塞对话，"
                           "适合素材多/耗时长的大批量蒸馏",
        },
        "name": {
            "type": "string",
            "description": "任务名称（可选，仅 async_=true 时使用）",
        },
    },
}


def _submit_async_distill(kw: Dict[str, Any]) -> Dict[str, Any]:
    """提交蒸馏任务到 AsyncExecutor 后台执行。

    即原 `distill_process_async` 的提交路径（第 0 档合并，评估报告 §6.2-7：
    它本来就是 `submit_task(tool_name="process_distill_run", …)` 的硬编码别名）,
    逐行保留：同样的参数白名单、同样的任务名默认值、同样的错误文案。
    """
    try:
        from agent.async_executor import get_async_executor
        p = _parse_params(kw)
        if not p["query"] and not p["paths"]:
            return {"ok": False,
                    "error": "query 与 paths 至少提供一个"}
        name = str(kw.get("name") or "process-distill")[:80]
        return get_async_executor().submit(
            name=name,
            tool_name="process_distill_run",
            params={k: v for k, v in kw.items()
                    if k in ("query", "paths", "artifacts",
                             "top_k", "max_workers")},
        )
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"提交异步蒸馏失败: {e}"}


def register_distill_tools(clear_first: bool = True) -> int:
    """注册蒸馏工具到 agent.tools 全局注册表。

    Args:
        clear_first: 先注销同名旧注册（热重载幂等）。
    Returns:
        注册的工具数。
    """
    from agent import tools as _tools

    if clear_first:
        for name in _NAMES + _LEGACY_NAMES:
            try:
                _tools.unregister(name)
            except Exception:  # noqa: BLE001
                pass

    @_tools.register(
        "distill_process_from_knowledge",
        "把知识库或素材路径中的其他 agent 编程过程/复盘/SOP 蒸馏为可复现"
        "步骤序列，并固化为云枢可复用的 workflow（0-Token 执行）与 skill"
        "（语义层召回）资产。参数 query 为知识库检索词，paths 为素材文件或"
        "目录列表（二选一或并用）；artifacts 指定固化产物。默认同步执行并"
        "返回结果；async_=true 时提交后台异步执行、立即返回 task_id（用 "
        "get_task_status 轮询、get_task_result 取结果），name 可指定任务名称。",
        schema=_ASYNC_SCHEMA,
    )
    def _distill_handler(**kw):  # noqa: D103
        # async_=true 走原 distill_process_async 的提交路径，否则同步执行
        if bool(kw.get("async_")):
            return _submit_async_distill(kw)
        return _execute_distill(kw)

    @_tools.register(
        "process_distill_run",
        "过程蒸馏内部执行体（供 AsyncExecutor 后台调用，通常不直接使用）。"
        "参数同 distill_process_from_knowledge，返回完整结果。",
        schema=_SYNC_SCHEMA,
    )
    def _run_handler(**kw):  # noqa: D103
        return _execute_distill(kw)

    logger.info("[PD] 蒸馏工具注册完成: %s", ", ".join(_NAMES))
    return len(_NAMES)


def unregister_distill_tools() -> int:
    """注销全部蒸馏工具（测试隔离用）。"""
    from agent import tools as _tools

    for name in _NAMES + _LEGACY_NAMES:
        try:
            _tools.unregister(name)
        except Exception:  # noqa: BLE001
            pass
    return len(_NAMES)
