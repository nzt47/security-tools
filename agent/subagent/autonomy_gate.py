"""分身自主权档位（L1-L5）—— SubagentConfig 接线 + config->ctx 桥 + 工具闸门叠加

【补的是什么缺口】capabilities.autonomy 此前如实登记：L1-L5 只在**主智能体侧**
（agent/autonomy.py + orchestrator）生效，分身侧没有档位字段 ⇒ 分身是"受治理的
一次性委派"，自主程度不可声明、不可执行。本模块把档位接进 SubagentConfig：

  1. 创建/热更新时**严格校验**（非法值 -> 端点 400，不静默回退默认档）；
  2. 委派时经 attach_autonomy_metadata 把生效档位写进 ctx.metadata
     （config->ctx 桥，仿 memory_broker.attach_memory_metadata）；
  3. 执行器把该档位包进 AutonomyContext，并在**工具调用闸门**里用它：子代理
     *声称*调用了档位之外类别的工具 ⇒ 计入 violations（与"裁剪集外调用"同款，
     整次委派失败），而不是只做一个只读视图。

【默认逐字旧行为】autonomy_level 为空 ⇒ 本模块什么都不做（不写 metadata、
不包 context、闸门不看档位）。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from agent.autonomy import AutonomyLevel, AutonomyPolicy, classify_action

__all__ = [
    "AUTONOMY_METADATA_KEY",
    "parse_autonomy_level",
    "attach_autonomy_metadata",
    "metadata_level",
    "out_of_level_reason",
]

#: 写进 DelegationContext.metadata 的键（**标识**，不含正文）
AUTONOMY_METADATA_KEY = "autonomy_level"


def parse_autonomy_level(value: Any) -> Optional[str]:
    """严格解析档位："" / None ⇒ ""（不接入）；合法 ⇒ "L1".."L5"；非法 ⇒ None。

    【为什么不复用 AutonomyLevel.from_value】那个函数对非法值**回退默认 L3**，
    适合运行时兜底；而创建端点的口径是"非法即拒"（不静默替使用者改档位）。
    """
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    up = text.upper()
    if up.startswith("L") and up[1:].isdigit() and 1 <= int(up[1:]) <= 5:
        return "L%d" % int(up[1:])
    if text.isdigit() and 1 <= int(text) <= 5:
        return "L%d" % int(text)
    return None


def _config_field(config: Any, name: str, default: Any = None) -> Any:
    if isinstance(config, Mapping):
        return config.get(name, default)
    return getattr(config, name, default)


def attach_autonomy_metadata(ctx: Any, config: Any) -> Any:
    """把生效档位写进委派上下文 metadata（config->ctx 桥；空/非法 ⇒ 不碰 ctx）

    与 memory_broker 的同名桥一样，就地更新 metadata（标识级、幂等）：批量路径
    delegate_many -> executor 不经过容器，故桥必须在 _prepare_config 里也做一遍。
    """
    parsed = parse_autonomy_level(_config_field(config, AUTONOMY_METADATA_KEY, ""))
    if not parsed:
        # "" = 不接入；None = 非法（创建时已 400，运行期按"不接入"fail-closed）
        return ctx
    current = getattr(ctx, "metadata", None)
    meta: Dict[str, Any] = dict(current) if isinstance(current, Mapping) else {}
    meta[AUTONOMY_METADATA_KEY] = parsed
    if isinstance(current, dict):
        current.clear()
        current.update(meta)
    else:
        object.__setattr__(ctx, "metadata", meta)
    return ctx


def metadata_level(metadata: Any) -> Optional[AutonomyLevel]:
    """从 ctx.metadata 取档位；缺失/非法 ⇒ None（= 不接入，逐字旧行为）"""
    if not isinstance(metadata, Mapping):
        return None
    parsed = parse_autonomy_level(metadata.get(AUTONOMY_METADATA_KEY))
    if not parsed:
        return None
    return AutonomyLevel.from_value(parsed)


def out_of_level_reason(tool_name: str, level: AutonomyLevel) -> str:
    """工具名是否**超出**该档位允许的类别；超出 ⇒ 人读原因，否则空串。

    分类复用 agent.autonomy.classify_action（工具名优先，关键字兜底）与
    AutonomyPolicy.get 的声明式策略表 —— 不在这里另立第二份分级口径。
    """
    name = str(tool_name or "").strip()
    if not name:
        return ""
    category = classify_action("tool:" + name, name)
    policy = AutonomyPolicy.get(level)
    if category in policy.allowed_categories:
        return ""
    return ("autonomy_outside_level: %s 类工具不在 %s 允许范围（%s）"
            % (category.value, level.value, policy.behavior))
