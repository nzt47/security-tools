"""工作流草稿的 AI 自动绑定（叙述步骤 → 真实工具 + 参数模板）。

背景（2026-10-26 用户实测）：吞噬生成的 workflow 草稿只有步骤描述，tool_name/参数全空，
不能执行；人工把 18 步逐条映射到工具成本太高。本模块用 LLM 做"提议"，但**边界很硬**：

- tool_name 必须出现在 data/tool_definitions 里，否则丢弃并记入 unbound（不臆造工具）；
- 只写草稿目录，不注册、不启用；绑定结果一律标注"AI 建议、需人工复核"；
- 全部步骤绑定成功后才允许登记（register 会再校验一次，不信任本模块的输出）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from .tool_catalog import load_catalog, tool_names

#: 单次最多绑定的步骤数（prompt/输出可控；超过请先拆分工作流）
MAX_STEPS = 40
#: prompt 字符预算（工具目录 + 步骤；超了就降级描述）
MAX_PROMPT_CHARS = 28000
#: LLM 输出 token 上限
MAX_TOKENS = 4000

SYSTEM_PROMPT = (
    "你是工作流编排助手。你的任务是把工作流步骤映射到给定的真实工具，并给出参数模板。"
    "必须只使用工具清单里的名字；没有合适工具时留空，不要编造。只输出 JSON。"
)


class BindError(Exception):
    """绑定失败（输入不合法 / 模型输出不可用 / 校验不过）。"""

    def __init__(self, message: str, *, status: int = 400, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.status = int(status)
        self.detail = detail or message


def _strip_fence(text: str) -> str:
    raw = str(text or "").strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.DOTALL | re.IGNORECASE)
    return m.group(1) if m else raw


def _call_llm(client: Any, prompt: str) -> str:
    """调用 LLM（兼容云枢 LLMService 的 chat / 旧客户端）。"""
    messages = [{"role": "user", "content": prompt}]
    if hasattr(client, "chat"):
        try:
            return str(client.chat(messages, system_prompt=SYSTEM_PROMPT,
                                   max_tokens=MAX_TOKENS, temperature=0.1))
        except TypeError:
            pass
        try:
            return str(client.chat(prompt, max_tokens=MAX_TOKENS))
        except TypeError:
            pass
        return str(client.chat(messages))
    return str(client.complete(prompt))


def build_prompt(workflow: Dict[str, Any], *, tool_dir: Optional[str] = None,
                 desc_limit: int = 120) -> str:
    steps = workflow.get("steps") or []
    if len(steps) > MAX_STEPS:
        raise BindError(f"步骤过多（{len(steps)} > {MAX_STEPS}），请先拆分成更小的工作流")
    catalog = "\n".join(t.compact(desc_limit) for t in load_catalog(tool_dir))
    step_view = [
        {"step_id": str(s.get("step_id") or ""), "description": str(s.get("description") or "")[:300]}
        for s in steps
    ]
    prompt = (
        "把下面工作流步骤映射到工具清单里的工具。\n\n"
        "工具清单（每行：name: 说明 params=参数名 required=必填）：\n"
        + catalog + "\n\n"
        "步骤（JSON）：\n" + json.dumps(step_view, ensure_ascii=False, indent=1) + "\n\n"
        "要求：\n"
        "1. 每个 step_id 输出一条 binding；\n"
        "2. tool_name 必须是工具清单里的 name；没有合适工具就填 \"\"，并在 reason 里说明；\n"
        "3. params_template 必须是对象，可引用 $input / $prev_output / $step.<n>.output / $param.<key>；\n"
        "4. 不要编造工具名，不要输出任何解释。\n"
        "输出格式：{\"bindings\":[{\"step_id\":\"step_1\",\"tool_name\":\"...\","
        "\"params_template\":{},\"reason\":\"...\"}]}"
    )
    return prompt


def parse_response(text: str) -> List[Dict[str, Any]]:
    raw = _strip_fence(text)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise BindError("模型输出不是 JSON 对象", status=502, detail=str(text)[:300])
    try:
        obj = json.loads(raw[start:end + 1])
    except ValueError as exc:
        raise BindError("模型输出 JSON 解析失败", status=502, detail=str(exc)) from exc
    bindings = obj.get("bindings") if isinstance(obj, dict) else None
    if not isinstance(bindings, list):
        raise BindError("模型输出缺少 bindings 数组", status=502)
    return [b for b in bindings if isinstance(b, dict)]


def apply_bindings(workflow: Dict[str, Any], bindings: List[Dict[str, Any]], *,
                   names: Optional[frozenset] = None, model: str = "",
                   max_steps: int = MAX_STEPS) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """把模型提议合并进 workflow；未知工具/空工具一律记 unbound，不写入假值。"""
    names = names if names is not None else tool_names()
    by_id: Dict[str, Dict[str, Any]] = {}
    for b in bindings:
        sid = str(b.get("step_id") or "")
        if sid and sid not in by_id:
            by_id[sid] = b
    new_steps: List[Dict[str, Any]] = []
    unbound: List[Dict[str, str]] = []
    seen_unbound = set()
    for step in (workflow.get("steps") or [])[:max_steps]:
        sid = str(step.get("step_id") or "")
        b = by_id.get(sid) or {}
        name = str(b.get("tool_name") or "").strip()
        params = b.get("params_template")
        if name and name not in names:
            unbound.append({"step_id": sid, "reason": "模型给出的工具不在工具目录：" + name})
            seen_unbound.add(sid)
            name = ""
        if not name:
            if sid not in seen_unbound:
                unbound.append({"step_id": sid, "reason": str(b.get("reason") or "没有合适的工具")})
                seen_unbound.add(sid)
            params = {}
        if not isinstance(params, dict):
            params = {}
        merged = dict(step)
        merged["tool_name"] = name
        merged["params_template"] = params
        new_steps.append(merged)
    total = len(new_steps)
    bound = sum(1 for s in new_steps if s.get("tool_name"))
    out = dict(workflow)
    out["steps"] = new_steps
    out["requires_binding"] = bound < total
    out["binding_meta"] = {"model": model, "bound": bound, "total": total,
                           "unbound": [u["step_id"] for u in unbound]}
    return out, {"bound": bound, "total": total, "model": model, "unbound": unbound}


def propose(workflow: Dict[str, Any], llm: Any, *, tool_dir: Optional[str] = None,
            names: Optional[frozenset] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """一次完整的 AI 绑定（不改盘）：返回 (绑定后的 workflow, 报告)。"""
    if llm is None:
        raise BindError("未配置 LLM：无法自动绑定；可手工编辑草稿 JSON 后再登记", status=503)
    prompt = build_prompt(workflow, tool_dir=tool_dir)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = build_prompt(workflow, tool_dir=tool_dir, desc_limit=60)
        if len(prompt) > MAX_PROMPT_CHARS:
            raise BindError(f"prompt 过长（{len(prompt)} > {MAX_PROMPT_CHARS}），请先拆分工作流")
    try:
        text = _call_llm(llm, prompt)
    except Exception as exc:  # noqa: BLE001 网络/鉴权/超时 → 502，不把原文进响应
        raise BindError("LLM 调用失败", status=502, detail=type(exc).__name__) from exc
    bindings = parse_response(text)
    try:
        from . import rewrite
        model = rewrite.model_name(llm)
    except Exception:  # noqa: BLE001 模型名只用于记录
        model = ""
    return apply_bindings(workflow, bindings, names=names, model=model)


def binding_warnings(report: Dict[str, Any]) -> List[str]:
    """把绑定结果写成给人看的注意事项（写进 manifest.warnings）。"""
    bound, total = int(report.get("bound") or 0), int(report.get("total") or 0)
    model = str(report.get("model") or "未知模型")
    if total and bound >= total:
        return [
            f"AI 已自动绑定全部 {total} 步（模型 {model}）：执行前请复核工具选择与参数。",
            "草稿仍在隔离区：点「登记为工作流」后才会出现在工作流页（默认停用，需手动启用）。",
        ]
    unbound = "、".join(str(u.get("step_id")) for u in (report.get("unbound") or [])[:8])
    return [
        f"AI 只绑定了 {bound}/{total} 步；未绑定：{unbound or '（无）'}。",
        "未绑定的步骤不能执行；可手工编辑 workflows/*.json 补全后再登记。",
    ]


def build_learned_workflow(workflow: Dict[str, Any], *, draft_id: str = "",
                           names: Optional[frozenset] = None):
    """校验并构造 LearnedWorkflow（不落库）；未绑定/未知工具一律拒绝。"""
    from agent.workflow_learning.models import LearnedWorkflow, WorkflowStep

    names = names if names is not None else tool_names()
    steps = workflow.get("steps") or []
    if not steps:
        raise BindError("工作流没有步骤，不能登记")
    missing = [str(s.get("step_id") or "") for s in steps if not str(s.get("tool_name") or "").strip()]
    unknown = sorted({str(s.get("tool_name")) for s in steps
                      if s.get("tool_name") and str(s.get("tool_name")) not in names})
    if missing or unknown:
        parts = []
        if missing:
            parts.append("未绑定步骤：" + "、".join(missing))
        if unknown:
            parts.append("未知工具：" + "、".join(unknown))
        raise BindError("还不能登记为工作流：" + "；".join(parts) + "（先补齐绑定再试）")
    wf_steps = []
    for s in steps:
        wf_steps.append(WorkflowStep(
            step_id=str(s.get("step_id") or ""),
            tool_name=str(s.get("tool_name") or ""),
            params_template=dict(s.get("params_template") or {}),
            output_key=str(s.get("output_key") or ""),
            description=str(s.get("description") or ""),
            need_llm=bool(s.get("need_llm")),
            timeout_ms=int(s.get("timeout_ms") or 30000),
        ))
    name = str(workflow.get("name") or workflow.get("id") or "ingested-workflow")
    wf_type = "hybrid" if any(s.need_llm for s in wf_steps) else "toolchain"
    return LearnedWorkflow(
        id=str(workflow.get("id")),
        name=name,
        description=str(workflow.get("description") or ""),
        task_signature=str(workflow.get("task_signature") or name),
        trigger_patterns=[str(x) for x in (workflow.get("trigger_patterns") or [name])],
        steps=wf_steps,
        workflow_type=wf_type,
        status="active",
        enabled=False,
        source_user_input=f"ingestion draft {draft_id}".strip(),
        tags=["ingestion"],
    )
