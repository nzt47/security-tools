"""运行期工具声明桥（插件 / MCP 进分身装配面）

【解决什么】插件（agent/extensions/plugins_installer.py）与 MCP（agent/tools/mcp_connector.py）
在**运行期**把工具注册进 agent.tools，但它们没有 data/tool_definitions/<name>.yaml，于是
agent/lines/assembler.py::assemble 按既定 fail-closed 规则把它们打进 denied_unknown
（注释原文：「无法证明其安全边界，就不给。这类工具应先补 YAML 定义」）。本模块提供一条
**显式声明**通道：运维在 CP_SUBAGENT_RUNTIME_TOOL_DECLS 指向的 JSON 里声明
{工具名: {plane, effect, risk}} 后，该工具才进入装配候选；**未声明者行为逐字不变**。

【为什么不做自动推导/自证】plane/effect/risk 就是安全边界。让第三方插件自报（或按名字猜）
等于把「能不能读私人记忆、能不能出网、要不要人工审批」交给被审对象自己决定。故本模块
**只读运维声明**，并校验取值合法；非法项跳过（fail-closed），不猜、不补保守默认。

【默认逐字旧行为】未配置该环境变量 ⇒ 返回原 meta，不新增任何工具。
【依赖纪律】仅标准库 + agent.lines.models.ToolMeta。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)

#: 声明文件路径（JSON；登记于 agent/settings/registry.py；空 = 关闭）
ENV_RUNTIME_TOOL_DECLS = "CP_SUBAGENT_RUNTIME_TOOL_DECLS"

#: 合法风险档（与 ToolMeta.risk 同口径；非法即跳过）
VALID_RISKS = ("low", "medium", "high", "critical")

__all__ = ["ENV_RUNTIME_TOOL_DECLS", "VALID_RISKS", "load_declarations", "augment_meta"]


def _valid_plane(plane: str) -> bool:
    from agent.lines.models import PLANES

    return str(plane) in set(PLANES)


def load_declarations(environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """读运维声明文件 → {工具名: ToolMeta}（未配置/坏文件/非法项一律跳过，fail-soft）"""
    raw = (os.environ.get(ENV_RUNTIME_TOOL_DECLS, "") if environ is None
           else environ.get(ENV_RUNTIME_TOOL_DECLS, ""))
    path_text = str(raw or "").strip()
    if not path_text:
        return {}
    path = Path(path_text)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("[RuntimeToolDecls] 声明文件不可读/非法(%s): %s", path, exc)
        return {}
    if not isinstance(data, Mapping):
        logger.warning("[RuntimeToolDecls] 声明文件根节点必须是对象：%s", path)
        return {}
    from agent.lines.models import ToolMeta

    out: Dict[str, Any] = {}
    for name, spec in data.items():
        tool = str(name or "").strip()
        if not tool or not isinstance(spec, Mapping):
            continue
        plane = str(spec.get("plane") or "").strip()
        effect = str(spec.get("effect") or "").strip()
        risk = str(spec.get("risk") or "").strip().lower()
        if not _valid_plane(plane):
            logger.warning("[RuntimeToolDecls] 跳过 %s：非法 plane=%r", tool, plane)
            continue
        if not effect:
            logger.warning("[RuntimeToolDecls] 跳过 %s：缺少 effect", tool)
            continue
        if risk not in VALID_RISKS:
            logger.warning("[RuntimeToolDecls] 跳过 %s：非法 risk=%r", tool, risk)
            continue
        tags = spec.get("tags")
        tag_tuple = (tuple(str(t) for t in tags)
                     if isinstance(tags, (list, tuple)) else ())
        out[tool] = ToolMeta(name=tool, plane=plane, effect=effect, risk=risk,
                             tags=tag_tuple,
                             description=str(spec.get("description")
                                             or "运行期声明工具（插件/MCP）"))
    return out


def augment_meta(meta: Mapping[str, Any], available: Sequence[str], *,
                 environ: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """把运维声明的运行期工具并入 meta（**不覆盖**已有 YAML 声明）

    只并入同时**已注册**（在 available 里）的声明 —— 声明文件点名一个没注册的工具是
    配置错误，不该凭空出现在候选池。
    """
    merged: Dict[str, Any] = dict(meta or {})
    decls = load_declarations(environ)
    if not decls:
        return merged
    avail = {str(x) for x in (available or ())}
    for name, tool_meta in decls.items():
        if name in merged or name not in avail:
            continue
        merged[name] = tool_meta
        logger.info("[RuntimeToolDecls] %s 按运维声明进入装配候选（plane=%s effect=%s risk=%s）",
                    name, tool_meta.plane, tool_meta.effect, tool_meta.risk)
    return merged
