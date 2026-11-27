"""run_orchestration_script 工具（E4-1 第一期）：脚本编排的模型侧入口。

【为什么是**一个**工具】脚本 + 工具白名单 + 五道上限本来就是"一次 run 的五个参数"；
拆成多个工具会让工具索引 / 能力清单 / BM25 半饱和点 / 路由 τ 连着改多次。
schema 用属性表达，错误码由运行时回稳定 code（见 agent/orchestration/runtime.py）。

【默认关】真正判定在 agent/orchestration/runtime.py（唯一事实源）：开关
CP_ORCH_SCRIPT_ENABLED=0 ⇒ 回 disabled 且**不 spawn、不建 run_dir**；受限权限预设
一律拒绝。工具层只做参数归一化，不写第二份阈值判断（否则改一处必漂移）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from agent import tools as _tools

#: 【为什么 description 必须**字面量**写在 register 调用里】守门测试
#: tests/unit/test_tool_definitions_yaml.py 用 AST 抽取 @register 的字面量参数与 YAML
#: 逐字对拍；写成模块常量会被抽成空串（那时"两边都不一致"反而更难发现）。

SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "script": {
            "type": "string",
            "description": "脚本本体（JavaScript；可用 await tools.call(名, 参数) / "
                           "await spawn(tasks) / console.log / sleep(ms)）",
        },
        "line": {
            "type": "string",
            "description": "按哪条主线的装配集算白名单（缺省=全局激活主线；"
                           "都没有时用只读默认集）",
        },
        "allow_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": "额外放行的工具名（仍受静态只读清单约束；治理平面/编排自身/终端永不放行）",
        },
        "allow_spawn": {
            "type": "boolean",
            "description": "是否允许脚本调 spawn（fan_out；仍过全部闸门）",
        },
        "timeout_seconds": {
            "type": "integer",
            "description": "本次墙钟上限（秒；只能小于服务端上限，缺省 120）",
        },
        "max_calls": {
            "type": "integer",
            "description": "本次工具调用次数上限（只能小于服务端上限，缺省 50）",
        },
    },
    "required": ["script"],
}


def _normalize_tools(raw: Any) -> List[str]:
    """allow_tools 归一化：接受数组或逗号分隔字符串（模型常把数组写成字符串）"""
    if raw is None:
        return []
    if isinstance(raw, str):
        items: Any = raw.replace("，", ",").split(",")
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        return []
    out: List[str] = []
    for item in items:
        name = str(item or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def register_all(*_args, **_kwargs) -> int:
    """注册 run_orchestration_script（与其它 *_tools.register_all 同形；返回个数）"""

    @_tools.register(
        "run_orchestration_script",
        "脚本编排（E4-1）：把一段 JavaScript 当程序跑，用 tools.call(名, 参数) 调工具、"
        "spawn(tasks) 派子代理，一次 run 内变量保留。脚本跑在独立 node 子进程的 vm 沙箱里"
        "（无 require/process、无网络），工具由宿主执行并过全部闸门；"
        "白名单 = 主线装配集 ∩ 只读静态清单 ∩ allow_tools。"
        "需要 CP_ORCH_SCRIPT_ENABLED=1 且权限预设 full-access。"
        "Run a JavaScript orchestration script in an isolated node subprocess",
        schema=SCHEMA,
    )
    def _run_orchestration_script(**kwargs) -> Dict[str, Any]:
        from agent.orchestration import runtime

        # 会话 id 只用于**结果面按会话过滤**（agent/context/session_bind.py 是唯一来源；
        # 取不到就留空，绝不臆造一个共享键）
        try:
            from agent.context import session_bind as _session_bind

            session_id = _session_bind.current()
        except Exception:  # noqa: BLE001 绑定不可用不影响本次 run
            session_id = ""
        return runtime.run(
            str(kwargs.get("script") or ""),
            line=str(kwargs.get("line") or ""),
            allow_tools=_normalize_tools(kwargs.get("allow_tools")),
            allow_spawn=bool(kwargs.get("allow_spawn")),
            timeout_sec=kwargs.get("timeout_seconds"),
            max_calls=kwargs.get("max_calls"),
            session_id=session_id,
        )

    return 1


__all__ = ["SCHEMA", "register_all"]
