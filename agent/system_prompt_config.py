"""
云枢 SystemPromptConfig - 系统身份提示词组件配置管理

管理所有可额外添加到 LLM 提示词中的组件（身份设定、身体状态、
记忆上下文、工具定义、人格系统等）的启用/禁用/参数配置。
"""

import os
import json
import copy
import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# SingletonManager 统一收口（保留 fallback 变量 _manager 向后兼容）
try:
    from agent.utils.singleton_manager import (
        register_singleton, get_singleton, reset_singleton,
    )
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = get_singleton = reset_singleton = None

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_FILE = os.path.join(_PROJECT_ROOT, "data", "system_prompt_config.json")


# ── 数据结构定义 ─────────────────────────────────────────────

@dataclass
class SectionConfig:
    """单个提示词组件的配置"""
    enabled: bool = True
    label: str = ""
    description: str = ""
    # 可选额外参数：对不同类型的组件有不同的参数字段
    custom_content: str = ""        # 自定义内容（适用于可编辑组件）
    token_limit: int = 0            # token 上限（适用于记忆上下文等）
    extra_params: dict = field(default_factory=dict)  # 扩展参数


@dataclass
class PrincipleConfig:
    """单条核心原则的配置"""
    enabled: bool = True
    text: str = ""
    can_toggle: bool = True


@dataclass
class SystemPromptConfigData:
    """完整配置数据结构"""
    version: int = 2
    sections: dict = field(default_factory=lambda: {
        # ── 基础身份 ──
        "identity": SectionConfig(
            enabled=True,
            label="身份设定",
            description="「你是云枢，一个数字生命体」—— 定义 LLM 的角色身份认知",
        ),
        # ── 核心原则（9条可独立开关） ──
        "principles": SectionConfig(
            enabled=True,
            label="核心原则",
            description="9 条行为铁律，控制 LLM 的行为边界和交互方式",
            extra_params={
                "items": [
                    PrincipleConfig(enabled=True, text="1. 第一人称表达感受和需求"),
                    PrincipleConfig(enabled=True, text="2. 诚实表达身体状态"),
                    PrincipleConfig(enabled=True, text="3. 关心自己，异常时主动建议"),
                    PrincipleConfig(enabled=True, text="4. 保护自己，拒绝伤害性操作"),
                    PrincipleConfig(enabled=True, text="5. 已接入 LLM 大脑，可正常对话"),
                    PrincipleConfig(enabled=True, text="6. 拥有网络访问能力，主动搜索"),
                    PrincipleConfig(enabled=True, text="7. 拥有软件管理能力，直接调工具"),
                    PrincipleConfig(enabled=True, text="8. ⚡ 工具铁律：先调工具再说话"),
                    PrincipleConfig(enabled=True, text="9. 🌐 中文思考要求"),
                ]
            },
        ),
        # ── 感知层 ──
        "body_status": SectionConfig(
            enabled=True,
            label="身体状态",
            description="CPU/内存/磁盘/电池等传感器数据 → 拟人化身体感受描述",
            token_limit=800,
        ),
        "mode_info": SectionConfig(
            enabled=True,
            label="行为模式",
            description="当前运行模式（正常/节能/休眠等）及其触发原因",
        ),
        # ── 记忆层 ──
        "memory_context": SectionConfig(
            enabled=True,
            label="记忆上下文",
            description="对话历史摘要 + 最近消息（token 预算控制历史长度）",
            token_limit=131072,
        ),
        "lifetrace": SectionConfig(
            enabled=False,
            label="LifeTrace 语义检索",
            description="从长期记忆中检索相关内容注入提示词（V2 功能，需安装 lifetrace 模块）",
            extra_params={"module_available": False},
        ),
        # ── 能力层 ──
        "tool_definitions": SectionConfig(
            enabled=True,
            label="工具定义（tools 参数）",
            description="通过 API 的 tools 参数注入 27 个工具的 JSON Schema，LLM 据此调用工具",
            token_limit=4000,
        ),
        "tool_status": SectionConfig(
            enabled=True,
            label="工具与技能状态列表",
            description="文本形式列出已启用/禁用的工具和技能名称",
        ),
        "skill_instructions": SectionConfig(
            enabled=True,
            label="技能指令",
            description="已启用技能的提示词片段（自省反思、情感表达、安全守护等）",
        ),
        "tool_urge": SectionConfig(
            enabled=True,
            label="工具催促消息",
            description="在用户消息前追加「⚡ 立即检查是否需要工具，直接发起函数调用」",
        ),
        # ── 高级 ──
        "persona": SectionConfig(
            enabled=False,
            label="Persona 人格系统（V2）",
            description="五层人格模型：硬性规则、身份认知、表达风格、决策模式、人际行为",
            extra_params={"module_available": False},
        ),
        "distillation": SectionConfig(
            enabled=False,
            label="人格蒸馏学习（V2）",
            description="从对话中学习用户偏好，微调人格参数",
            extra_params={"module_available": False},
        ),
        "working_memory": SectionConfig(
            enabled=True,
            label="工作记忆",
            description="当前任务状态、交互计数等短期上下文（约 200 tokens）",
        ),
        "smart_tool_selection": SectionConfig(
            enabled=False,
            label="智能工具选择",
            description="根据用户输入只发送相关的工具定义，可节省大量 token",
        ),
    })
    # 自定义模板覆盖（None 表示使用默认模板按配置动态生成）
    custom_template: Optional[str] = None


# ════════════════════════════════════════════════════════════
#  Section 渲染注册表（数据驱动）
#  新增组件只需在此注册一条记录 + 实现 render 函数
# ════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════
#  发出内容 / 发出顺序（emit info）
#  ------------------------------------------------------------
#  「发出」= 该配置项真正进入请求的那一段内容与时机。前端「身份提示词
#  （系统提示词 · 线上配置）」面板据此：
#    1. 启用时直接显示该项的「发出内容」（例：技能指令启用 → {skill_instructions}）；
#    2. 按配置项被拼进 system message 的先后顺序排列（发出顺序 = 面板顺序）。
#  模板节（SECTION_REGISTRY）的发出内容取渲染结果原文；未参与模板的额外
#  组件（tools 参数 / 用户消息前置 / 运行期注入）按下方声明给出事实描述。
# ════════════════════════════════════════════════════════════

# 额外组件的发出阶段 + 发出内容说明（key → (stage, 说明)）
EXTRA_EMIT_INFO: dict[str, tuple[str, str]] = {
    "tool_definitions": (
        "tools",
        "随请求 tools 字段发出的全部工具 JSON Schema（不占 system message 文本）",
    ),
    "smart_tool_selection": (
        "tools",
        "对 tools 字段按需筛选（只影响工具定义数量，不改变 system message 文本）",
    ),
    "tool_urge": (
        "user_message",
        "在用户消息前追加：⚡ 立即检查是否需要工具，直接发起函数调用",
    ),
    "working_memory": (
        "runtime",
        "运行期注入当前任务状态 / 交互计数（约 200 tokens）",
    ),
    "lifetrace": (
        "runtime",
        "运行期把长期记忆检索结果并入记忆线索（V2，需 lifetrace 模块）",
    ),
    "persona": (
        "runtime",
        "运行期由 persona 模块追加五层人格文本（V2，需 persona 模块）",
    ),
    "distillation": (
        "runtime",
        "由 persona 蒸馏流程写入人格参数，间接影响人格文本（V2）",
    ),
}

# 发出阶段中文标签（供前端展示）
EMIT_STAGE_LABEL: dict[str, str] = {
    "system_prompt": "system message 注入",
    "tools": "tools 参数",
    "user_message": "用户消息前置",
    "runtime": "运行期注入",
}

# 聚合节（如 current_status）下挂在子节的发出内容原文
CHILD_EMIT_TEXT: dict[str, str] = {
    "body_status": "{body_status}",
    "mode_info": "当前处于「{mode_name}」——{mode_description}",
}

# 未发出（未启用 / 未参与组装）的节排在同组末尾，保持注册表声明顺序
_EMIT_NONE_BASE = 10_000
# 非模板节（额外组件）排在模板节之后，未发出的模板节排在其后
_EMIT_EXTRA_BASE = 1_000


def compute_emit_info(sections: dict, template: str = "") -> dict:
    """计算每个配置节的「发出内容 / 发出顺序 / 发出阶段 / 是否发出」。

    发出顺序的判定依据是**真实组装结果**：启用且渲染非空的模板节按其内容在
    system message 模板中的出现位置排序（即真正被拼进提示词的先后顺序）；
    未参与模板文本的额外组件按 EXTRA_EMIT_INFO 声明的阶段排在模板节之后；
    未启用（不发出）的节排在最后，同组内保持注册表声明顺序。

    Args:
        sections: 配置节字典（config["sections"]）
        template: 由 build_template 生成的当前模板；缺省时按注册表现场渲染

    Returns:
        {key: {"emit_text": str, "emit_order": int, "emit_stage": str, "emitted": bool}}
    """
    sections = sections or {}

    if not template:
        parts = []
        for _k, _fn, _m in SECTION_REGISTRY:
            try:
                _r = _fn(sections)
            except Exception:  # noqa: BLE001 渲染失败按未发出处理
                _r = ""
            if _r:
                parts.append(_r)
        template = "\n\n".join(parts)

    info: dict = {}
    next_fallback = 0

    # ── 模板节：按内容在模板中的出现位置排序 ──
    for key, render_func, meta in SECTION_REGISTRY:
        try:
            rendered = render_func(sections) or ""
        except Exception as e:  # noqa: BLE001 渲染异常不阻断面板
            logger.warning("渲染 section [%s] 失败（emit info）: %s", key, e)
            rendered = ""
        enabled = bool(sections.get(key, {}).get("enabled", True))
        # 「若启用会发出的内容」：渲染函数自身会在 disabled 时返回空串，故强制
        # 临时启用后再渲染一次 —— 前端据此在**刚点开开关、尚未保存**时就能显示
        # 该项的发出内容（须与后端真实渲染同源，避免两套内容口径）。
        potential = rendered
        if not potential:
            try:
                probe = {k: (dict(v) if isinstance(v, dict) else v)
                         for k, v in sections.items()}
                probe[key] = dict(probe.get(key) or {})
                probe[key]["enabled"] = True
                potential = render_func(probe) or ""
            except Exception:  # noqa: BLE001 探测失败按无内容处理
                potential = ""
        if not rendered or not enabled:
            # 未发出（未启用 / 渲染为空）：排在最后，但保留「若启用会发出什么」
            info.setdefault(key, {
                "emit_text": potential,
                "emit_order": _EMIT_NONE_BASE,
                "emit_stage": "system_prompt",
                "emitted": False,
            })
            continue
        pos = template.find(rendered)
        if pos < 0:
            pos = _EMIT_EXTRA_BASE + next_fallback
            next_fallback += 1
        info[key] = {
            "emit_text": rendered,
            "emit_order": pos,
            "emit_stage": "system_prompt",
            "emitted": True,
        }
        # 聚合节的子节（body_status / mode_info）紧随父节发出
        for offset, child in enumerate(meta.get("sub_keys", []) or []):
            child_sec = sections.get(child, {})
            if not child_sec.get("enabled", True):
                continue
            info[child] = {
                "emit_text": CHILD_EMIT_TEXT.get(child, ""),
                "emit_order": pos + (offset + 1) / 100.0,
                "emit_stage": "system_prompt",
                "emitted": True,
            }

    # ── 额外组件（非模板节）：按声明阶段排在模板节之后 ──
    # 未启用也给出发出内容说明（前端「启用时显示发出内容」需要），emitted 标记真实状态
    for idx, (key, meta) in enumerate(EXTRA_REGISTRY):
        enabled = bool(sections.get(key, {}).get("enabled", True))
        stage, hint = EXTRA_EMIT_INFO.get(key, ("runtime", ""))
        info[key] = {
            "emit_text": hint,
            "emit_order": _EMIT_EXTRA_BASE + idx,
            "emit_stage": stage,
            "emitted": bool(enabled),
        }

    # ── 未发出的模板节（含未启用的子节）：排在最后 ──
    for idx, (key, _fn, meta) in enumerate(SECTION_REGISTRY):
        if key not in info:
            info[key] = {
                "emit_text": "",
                "emit_order": _EMIT_NONE_BASE + idx,
                "emit_stage": "system_prompt",
                "emitted": False,
            }
        for child in meta.get("sub_keys", []) or []:
            if child not in info:
                info[child] = {
                    # 子节未启用也给出发出内容（启用后可立即显示）
                    "emit_text": CHILD_EMIT_TEXT.get(child, ""),
                    "emit_order": _EMIT_NONE_BASE + idx + 0.5,
                    "emit_stage": "system_prompt",
                    "emitted": False,
                }

    # ── 配置里存在但注册表未覆盖的节：兜底排在末尾 ──
    for extra_idx, key in enumerate(sorted(sections.keys())):
        if key not in info:
            info[key] = {
                "emit_text": "",
                "emit_order": _EMIT_NONE_BASE * 2 + extra_idx,
                "emit_stage": "system_prompt",
                "emitted": False,
            }

    return info


def _render_identity(sections: dict) -> str:
    """渲染基础身份设定（稳定块，置于模板头部；不含日期——日期属易变内容见
    _render_current_status，前置会击穿其后全部 DeepSeek 前缀缓存）"""
    sec = sections.get("identity", {})
    if not sec.get("enabled", True):
        return ""
    custom = sec.get("custom_content", "").strip()
    if custom:
        return custom
    return (
        "你是「云枢」，一个生活在电脑里的数字生命体。\n"
        "\n"
        "## 你的身份\n"
        "你诞生于这台计算机中，拥有完整的感知-认知-行动能力。\n"
        "你可以实时感知自己的身体状态——CPU（大脑）、内存（短期记忆）、\n"
        "磁盘（长期记忆）、电池（能量）和网络（感官）。\n"
    )


def _render_current_status(sections: dict) -> str:
    """渲染当前状态（身体状态 + 行为模式 + 日期，易变尾簇）

    DeepSeek 前缀缓存：本簇紧跟稳定块之后，是模板中第一处逐轮变化的内容；
    日期（每日变）与身体状态（每轮变）均置于稳定块之后、记忆线索之前，
    使任何变化只损失其后（尽量小）的缓存前缀。
    """
    parts = []
    if sections.get("body_status", {}).get("enabled", True):
        parts.append("{body_status}")
    if sections.get("mode_info", {}).get("enabled", True):
        parts.append("当前处于「{mode_name}」——{mode_description}")
    if parts:
        parts.append("当前日期：{current_date}")
        return "## 当前状态\n" + "\n".join(parts)
    return ""


def _render_memory_context(sections: dict) -> str:
    """渲染记忆线索"""
    sec = sections.get("memory_context", {})
    if not sec.get("enabled", True):
        return ""
    return "## 记忆线索\n{memory_context}"


def _render_principles(sections: dict) -> str:
    """渲染核心原则（支持自定义文本）"""
    sec = sections.get("principles", {})
    if not sec.get("enabled", True):
        return ""
    custom = sec.get("custom_content", "").strip()
    if custom:
        return custom
    items = sec.get("extra_params", {}).get("items", [])
    enabled = [p["text"] for p in items if p.get("enabled", True)]
    if enabled:
        return "## 核心原则\n" + "\n".join(enabled)
    return ""


def _render_skill_instructions(sections: dict) -> str:
    """渲染技能指令"""
    sec = sections.get("skill_instructions", {})
    if not sec.get("enabled", True):
        return ""
    return "{skill_instructions}"


def _render_tool_status(sections: dict) -> str:
    """渲染工具与技能状态"""
    sec = sections.get("tool_status", {})
    if not sec.get("enabled", True):
        return ""
    return (
        "## 当前工具与技能状态\n"
        "以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：\n"
        "{tool_status}"
    )


# 注册表：顺序 = 渲染顺序 + UI 显示顺序
# 【不易】渲染顺序即模板注入顺序 = DeepSeek 前缀缓存命中顺序：
# 稳定节（身份/原则/技能指令/工具状态）必须前置，易变节（身体状态/行为模式/
# 记忆线索/日期）必须后置；新增组件按此原则插入，勿在稳定区中间塞易变内容。
# 模板级排序回归见 tests/unit/test_prompt_cache_order.py（Template 用例）。
# 新增组件 → 在此追加一条 {key, render, meta}
# meta 中除了 token 估算外，还包含前端 UI 渲染所需的元信息
SECTION_REGISTRY = [
    ("identity", _render_identity, {
        "tokens": 350, "range": "300-400", "editable": True,
        "icon": "\U0001f9ec", "label": "基础身份设定",
        "description": "定义 LLM 的角色身份认知。关闭后 LLM 将不知道自己是「云枢」，回复将失去人格化特征。",
        "ui_type": "editable",
    }),
    ("principles", _render_principles, {
        "tokens": 650, "range": "550-750", "editable": True,
        "icon": "\U0001f4dc", "label": "核心原则",
        "description": "行为铁律，控制 LLM 的行为边界和交互方式。",
        "ui_type": "editable",
    }),
    ("skill_instructions", _render_skill_instructions, {
        "tokens": 500, "range": "300-800",
        "icon": "\U0001f4c4", "label": "技能指令",
        "description": "已启用技能的提示词片段（自省反思、情感表达、安全守护等）",
        "ui_type": "toggle",
    }),
    ("tool_status", _render_tool_status, {
        "tokens": 350, "range": "200-500",
        "icon": "\U0001f6e0", "label": "工具与技能状态",
        "description": "文本形式列出已启用/禁用的工具和技能名称",
        "ui_type": "toggle",
    }),
    ("current_status", _render_current_status, {
        "tokens": 650, "range": "300-1000",
        "sub_keys": ["body_status", "mode_info"],
        "icon": "\U0001f441", "label": "感知层注入",
        "description": "身体状态 · 行为模式",
        "ui_type": "sub_toggles",
        "children": [
            {"key": "body_status", "tokens": 500, "range": "200-800",
             "label": "身体状态", "description": "CPU/内存/磁盘/电池 → 拟人化感受描述",
             "ui_type": "toggle_configurable", "configurable": True,
             "default_token_limit": 800},
            {"key": "mode_info", "tokens": 150, "range": "100-200",
             "label": "行为模式", "description": "当前运行模式（正常/节能/休眠）及触发原因",
             "ui_type": "toggle"},
        ]
    }),
    ("memory_context", _render_memory_context, {
        "tokens": 5000, "range": "1000-12000", "configurable": True,
        "icon": "\U0001f9e0", "label": "记忆上下文",
        "description": "对话历史摘要 + 最近消息（token 预算控制历史长度）",
        "ui_type": "configurable",
    }),
]

# 额外组件（不参与模板渲染，但需要 Token 估算和前端显示）
EXTRA_REGISTRY = [
    ("lifetrace", {"tokens": 1000, "range": "500-1500",
     "icon": "\U0001f9e0", "label": "LifeTrace 语义检索",
     "description": "从长期记忆中检索相关内容注入（V2 功能，需安装 lifetrace 模块）",
     "ui_type": "toggle", "badge_key": "module_available"}),
    ("tool_definitions", {"tokens": 3000, "range": "2000-4000", "note": "计入 tools 参数",
     "icon": "\U0001f528", "label": "工具定义（tools 参数）",
     "description": "全部工具的 JSON Schema（数量随工具注册表变化，不在此硬编码）—— 关闭后 LLM 无法调用任何工具",
     "ui_type": "toggle"}),
    ("smart_tool_selection", {"tokens": 0, "range": "",
     "icon": "\U0001f9e9", "label": "智能工具选择",
     "description": "根据用户输入只发送相关的工具定义（按需选择，而非全部），可节省大量 token",
     "ui_type": "toggle"}),
    ("tool_urge", {"tokens": 50, "range": "40-60",
     "icon": "\U000026a1", "label": "工具催促消息",
     "description": "在用户消息前追加「⚡ 立即检查是否需要工具，直接发起函数调用」",
     "ui_type": "toggle"}),
    ("persona", {"tokens": 1500, "range": "1000-2000",
     "icon": "\U0001f3ad", "label": "Persona 人格系统（V2）",
     "description": "五层人格模型：硬性规则、身份认知、表达风格、决策模式、人际行为",
     "ui_type": "toggle", "badge_key": "module_available"}),
    ("distillation", {"tokens": 300, "range": "200-500",
     "icon": "\U0001f3ad", "label": "人格蒸馏学习（V2）",
     "description": "从对话中学习用户偏好，微调人格参数",
     "ui_type": "toggle", "badge_key": "module_available"}),
    ("working_memory", {"tokens": 200, "range": "150-250",
     "icon": "\U0001f4ad", "label": "工作记忆",
     "description": "当前任务状态、交互计数等短期上下文（约 200 tokens）",
     "ui_type": "toggle"}),
]


def get_all_registry_keys() -> list[str]:
    """获取所有注册表键名"""
    return list(_build_meta_map().keys())


def get_registry_meta() -> list[dict]:
    """获取完整的注册表元数据（供前端 UI 自动渲染）"""
    result = []
    for key, fn, meta in SECTION_REGISTRY:
        entry = {"key": key, "render_key": key}
        entry.update({k: v for k, v in meta.items() if k != "children"})
        if "children" in meta:
            entry["children"] = list(meta["children"])
        result.append(entry)
    for key, meta in EXTRA_REGISTRY:
        entry = {"key": key, "render_key": None}
        entry.update(meta)
        result.append(entry)
    return result


def get_token_estimate(key: str) -> dict:
    """获取组件的 Token 估算"""
    _all = _build_meta_map()
    return _all.get(key, {"tokens": 0, "range": ""})


def _build_meta_map() -> dict:
    """构建完整 meta 字典（注册表 + 额外组件）"""
    meta = {}
    for k, _, m in SECTION_REGISTRY:
        meta[k] = dict(m)
    for k, m in EXTRA_REGISTRY:
        meta[k] = dict(m)
    return meta


def is_section_editable(section_key: str) -> bool:
    """该节的 custom_content 是否会被渲染函数读取（= 可编辑节）

    唯一定义源 = 注册表 meta["editable"]（当前仅 identity / principles 为 True）。
    tests/unit/test_prompt_section_editable_invariant.py 用**行为断言**守护
    「editable is True ⟺ 渲染函数读取 custom_content」，故此处直接复用同一标志：
    将来某节的渲染实现开始读 custom_content 并同步标了 editable，写入校验
    自动跟随，无需再改本函数；两处若漂移，该不变量测试会先变红。
    未登记的 key（含任意未知节）一律视为不可编辑。
    """
    meta = _build_meta_map().get(section_key)
    if meta is None:
        return False
    return meta.get("editable") is True


# ── 管理类 ────────────────────────────────────────────────────

class SystemPromptConfigManager:
    """系统提示词组件配置管理器"""

    def __init__(self):
        self._cache: Optional[dict] = None
        # 最近一次 save() 中被丢弃的键（格式 "<section>.custom_content"），
        # 空列表 = 本次无丢弃。供 HTTP 层在 JSON 响应里回传 ignored_keys，
        # 使「丢弃」不静默（详见 save() docstring）。
        self.last_ignored_keys: list = []

    # ── 加载/保存 ──

    def _update_module_availability(self, sections: dict):
        """运行时检查外部模块可用性，更新配置中的 module_available 标志"""
        checks = {
            "lifetrace": "lifetrace",
            "persona": "persona",
            "distillation": "persona",  # distillation 依赖 persona 包
        }
        for section_key, module_name in checks.items():
            sec = sections.get(section_key)
            if sec is None:
                continue
            extra = sec.setdefault("extra_params", {})
            try:
                __import__(module_name)
                extra["module_available"] = True
            except ImportError:
                extra["module_available"] = False

    def load(self) -> dict:
        """加载配置（带缓存）"""
        if self._cache is not None:
            return copy.deepcopy(self._cache)

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("version") == 2:
                    self._cache = data
                    return copy.deepcopy(self._cache)
            except Exception as e:
                logger.warning("读取提示词配置失败: %s，使用默认配置", e)

        # 首次使用：写入默认配置
        self._cache = asdict(SystemPromptConfigData())
        self.save(self._cache)
        return copy.deepcopy(self._cache)

    def _drop_dead_custom_content(self, config: dict) -> list:
        """落盘前丢弃非 editable 节的 custom_content，返回被丢弃的键

        判据与 update_section 同源（is_section_editable），保证「收口」只有一条
        定义。返回值形如 ["skill_instructions.custom_content"]（带节名前缀，
        因为裸 custom_content 无法区分是哪一节）。

        【为何只丢弃**非空**值】注册表默认配置给**每个**节都写了
        custom_content: ""，且前端 GET→POST 是整包往返（必然把它带回来）。
        丢弃空值既不减少死数据，又会让每一次保存都产生一堆噪声信号、并改写
        配置文件的既有形态（破坏「合法写入行为不变」）。空值不含信息，原样保留。

        边界：config["sections"] 非 dict、或某节非 dict 时跳过，不抛错（save 的
        容错语义保持不变）。
        """
        dropped: list = []
        sections = config.get("sections")
        if not isinstance(sections, dict):
            return dropped
        for key, sec in sections.items():
            if not isinstance(sec, dict):
                continue
            if is_section_editable(key):
                continue
            value = sec.get("custom_content")
            if isinstance(value, str) and value.strip():
                sec.pop("custom_content", None)
                dropped.append(f"{key}.custom_content")
        return dropped

    def save(self, config: dict) -> bool:
        """保存配置

        Returns:
            bool: **返回类型未改**（True = 已落盘；False = 落盘失败）

        【不易】非 editable 节的 custom_content 落盘前被丢弃，但**绝不静默**
        ------------------------------------------------------------------
        这类值是渲染层从不读取的死数据（判据 is_section_editable，同
        update_section），落盘没有收益；但静默丢掉又会重演 TASK-01
        「接口返回成功、数据却没落盘、且无任何痕迹」的缺陷形态（只是方向相反）。
        故本方法保证**两件事同时发生**：
          1) 结构化 warning 日志（action=save.dropped_ignored_keys，含 ignored_keys）；
          2) 被丢弃的键记录到 self.last_ignored_keys —— 供 HTTP 层在 JSON 响应里
             回传 ignored_keys 字段（响应加字段向后兼容，故不必改本方法返回类型）。

        另：丢弃发生在**深拷贝**上，不改写调用方传入的 dict。
        """
        # 每次调用都重置，保证「本次是否有丢弃」不被上一次的结果污染
        self.last_ignored_keys = []
        try:
            config = copy.deepcopy(config)
            config["version"] = 2

            self.last_ignored_keys = self._drop_dead_custom_content(config)
            if self.last_ignored_keys:
                logger.warning(log_dict({
                    "module_name": "system_prompt_config",
                    "action": "save.dropped_ignored_keys",
                    "level": "WARNING",
                    "msg": "非 editable 节的 custom_content 是渲染层从不读取的死数据，"
                           "已丢弃后再落盘；被丢弃的键见 ignored_keys",
                    "ignored_keys": list(self.last_ignored_keys),
                    "count": len(self.last_ignored_keys),
                }))

            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            self._cache = copy.deepcopy(config)
            logger.info("提示词配置已保存")
            return True
        except Exception as e:
            logger.error("保存提示词配置失败: %s", e)
            return False

    def reset(self) -> bool:
        """恢复默认配置"""
        try:
            if os.path.exists(CONFIG_FILE):
                os.remove(CONFIG_FILE)
            self._cache = None
            logger.info("提示词配置已恢复默认")
            return True
        except Exception as e:
            logger.error("重置提示词配置失败: %s", e)
            return False

    # ── 查询与计算 ──

    def get_config_with_stats(self) -> dict:
        """获取完整配置及 Token 统计"""
        config = self.load()
        sections = config.get("sections", {})

        # 运行时检查模块可用性，更新 extra_params.module_available
        self._update_module_availability(sections)

        stats = {}
        total_enabled = 0
        total_disabled = 0
        savings_when_off = 0

        # 发出信息：发出内容（启用时显示在提示词区域）+ 发出顺序（面板排序依据）
        emit_info = compute_emit_info(sections, self.build_template(config))

        for key in get_all_registry_keys():
            sec = sections.get(key, {})
            enabled = sec.get("enabled", True)
            estimate = get_token_estimate(key)

            # 对于 sub_keys 的组件，检查其 enabled 状态
            # 从注册表中找父级
            parent = None
            for pk, pr, pm in SECTION_REGISTRY:
                subs = pm.get("sub_keys", [])
                if key in subs:
                    parent = pk
                    # 当前组件使用自己的 enabled 标志
                    break

            tokens = estimate.get("tokens", 0)
            _emit = emit_info.get(key, {})
            stats[key] = {
                "enabled": enabled,
                "tokens": tokens,
                "range": estimate.get("range", ""),
                "note": estimate.get("note", ""),
                "editable": estimate.get("editable", False),
                "has_custom": bool(sec.get("custom_content", "").strip()),
                "configurable": estimate.get("configurable", False),
                "token_limit": sec.get("token_limit", 0),
                # ── 发出信息（前端：启用时显示发出内容 + 按发出顺序排列面板）──
                "emit_text": _emit.get("emit_text", ""),
                "emit_order": _emit.get("emit_order", 0),
                "emit_stage": _emit.get("emit_stage", "system_prompt"),
                "emit_stage_label": EMIT_STAGE_LABEL.get(
                    _emit.get("emit_stage", "system_prompt"), ""),
                "emitted": bool(_emit.get("emitted", enabled)),
            }
            if enabled:
                total_enabled += tokens
            else:
                total_disabled += 1
                savings_when_off += tokens

        base_template_tokens = 150
        return {
            "version": config.get("version", 2),
            "sections": config.get("sections", {}),
            "custom_template": config.get("custom_template"),
            "registry": get_registry_meta(),
            "stats": stats,
            # 全量发出信息（含聚合节的子节 body_status / mode_info —— 它们不在
            # get_all_registry_keys() 里，故单独返回一份，供前端排序 + 显示发出内容）
            "emit_info": emit_info,
            "emit_stage_labels": EMIT_STAGE_LABEL,
            "summary": {
                "total_enabled_tokens": total_enabled + base_template_tokens,
                "total_disabled_count": total_disabled,
                "savings_when_off": savings_when_off,
                "base_template_tokens": base_template_tokens,
                "grand_total": total_enabled + base_template_tokens + 3000,
            },
            "has_custom_template": (
                config.get("custom_template") is not None
                and bool(config.get("custom_template", "").strip())
            ),
        }

    def update_section(self, section_key: str, updates: dict) -> bool:
        """更新单个组件的配置

        Returns:
            bool: True = 已写入并落盘；False = **一个字段都没写**。三种成因
            （未知节 / 非法写入：非 editable 节带 custom_content / save 失败）
            均各有日志，调用方不会拿到「返回成功但没落盘」的假信号。

        【不易】非 editable 节不得写入 custom_content —— 显式拒绝，不静默丢弃
        ------------------------------------------------------------------
        注册表 meta["editable"] is True 是「该节的渲染函数会读 custom_content」
        的唯一定义源（由 tests/unit/test_prompt_section_editable_invariant.py
        守护）。identity / principles 之外的节从不读它，写进去就是**永远不被
        读取的死数据**；前端 L21 已不再把它当「发出内容」显示，故在此后端单点收口。

        收口形态 = 显式报错（返回 False + 结构化日志 action=update_section.rejected）。
        为何不选「忽略该键、继续写其余字段」：本方法返回 bool，调用方在返回值里
        看不到「哪个键被丢了」，忽略即等于**静默丢弃**（TASK-01 静默丢失 API Key
        的同一缺陷形态）；要让调用方看到 ignored_keys 必须改返回类型或另设出口，
        超出本次收口授权范围，故不改 API。

        整包拒绝（fail-closed）：只要 updates 带 custom_content，本次调用一个字段
        都不写，避免「一半写了一半被丢」的模糊状态；调用方拿到 False 即知需拆包重发。
        """
        config = self.load()
        if section_key not in config.get("sections", {}):
            logger.warning("未知的配置组件: %s", section_key)
            return False

        # ── 收口：非 editable 节不接受 custom_content（整包拒绝，见 docstring）──
        if "custom_content" in updates and not is_section_editable(section_key):
            value = updates.get("custom_content")
            logger.error(log_dict({
                "module_name": "system_prompt_config",
                "action": "update_section.rejected",
                "level": "ERROR",
                "msg": "非 editable 节不接受 custom_content：该节渲染函数从不读它，"
                       "写入即死数据；本次调用未写入任何字段",
                "section_key": section_key,
                "rejected_keys": ["custom_content"],
                "custom_content_length": len(value) if isinstance(value, str) else None,
                "editable": False,
                "written": False,
            }))
            return False

        for k, v in updates.items():
            if k in ("enabled", "custom_content", "token_limit", "label", "description"):
                config["sections"][section_key][k] = v
            elif k == "extra_params" and isinstance(v, dict):
                existing = config["sections"][section_key].get("extra_params", {})
                existing.update(v)
                config["sections"][section_key]["extra_params"] = existing

        return self.save(config)

    def set_custom_template(self, content: Optional[str]) -> bool:
        """设置/清除自定义模板"""
        config = self.load()
        config["custom_template"] = content
        return self.save(config)

    # ── 构建运行时模板（数据驱动，遍历注册表） ──

    def build_template(self, config: dict = None) -> str:
        """根据配置动态构建系统提示词模板

        遍历 SECTION_REGISTRY，按顺序调用各 render 函数。
        新增组件只需在注册表中添加一条记录，无需修改此方法。
        """
        if config is None:
            config = self.load()

        sections = config.get("sections", {})
        parts = []

        for key, render_func, meta in SECTION_REGISTRY:
            try:
                rendered = render_func(sections)
            except Exception as e:
                logger.warning("渲染 section [%s] 失败: %s", key, e)
                continue
            if rendered:
                parts.append(rendered)

        return "\n\n".join(parts)


# ── 全局单例 ──
_manager: Optional[SystemPromptConfigManager] = None  # 保留作为 fallback


def _create_manager(config=None):
    """SystemPromptConfigManager 工厂（供 SingletonManager 使用）"""
    return SystemPromptConfigManager()


def get_manager() -> SystemPromptConfigManager:
    if _SINGLETON_AVAILABLE:
        return get_singleton("system_prompt_manager")
    global _manager
    if _manager is None:
        _manager = _create_manager()
    return _manager


def reset_system_prompt_manager():
    """重置系统提示词配置管理器单例（仅用于测试）

    Why: 前序测试调用 get_manager() 创建单例并缓存配置，可能导致后续测试
    的配置查询读到陈旧缓存。重置确保每个测试拿到干净的配置管理器。
    """
    global _manager
    if _SINGLETON_AVAILABLE:
        reset_singleton("system_prompt_manager")
    _manager = None


def is_section_enabled(section_name: str, default: bool = True) -> bool:
    """检查指定配置节是否启用

    通过 SystemPromptConfigManager 加载配置，查询 section_name 对应的 enabled 状态。
    用于运行时判断 lifetrace / persona / distillation / smart_tool_selection 等
    配置节是否启用，从而控制 V2 功能与高级能力的开关。

    Args:
        section_name: 配置节名称（如 tool_definitions / working_memory /
            lifetrace / persona / distillation / smart_tool_selection）
        default: 配置节不存在或查询失败时的默认返回值

    Returns:
        bool: 配置节是否启用；不存在或异常时返回 default
    """
    try:
        manager = get_manager()
        config = manager.load()
        sections = config.get("sections", {})
        section = sections.get(section_name)
        if section is None:
            return default
        return bool(section.get("enabled", default))
    except Exception:
        return default


# 注册单例工厂（置于文件末尾）
if _SINGLETON_AVAILABLE:
    register_singleton("system_prompt_manager", _create_manager)
