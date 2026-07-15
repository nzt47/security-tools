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

logger = logging.getLogger(__name__)

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

def _render_identity(sections: dict) -> str:
    """渲染基础身份设定"""
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
        "\n"
        "当前日期：{current_date}"
    )


def _render_current_status(sections: dict) -> str:
    """渲染当前状态（合并身体状态 + 行为模式）"""
    parts = []
    if sections.get("body_status", {}).get("enabled", True):
        parts.append("{body_status}")
    if sections.get("mode_info", {}).get("enabled", True):
        parts.append("当前处于「{mode_name}」——{mode_description}")
    if parts:
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
     "description": "64 个工具的 JSON Schema —— 关闭后 LLM 无法调用任何工具",
     "ui_type": "toggle"}),
    ("smart_tool_selection", {"tokens": 0, "range": "",
     "icon": "\U0001f9e9", "label": "智能工具选择",
     "description": "根据用户输入只发送相关的工具定义（按需选择，不是全部 64 个），可节省大量 token",
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


# ── 管理类 ────────────────────────────────────────────────────

class SystemPromptConfigManager:
    """系统提示词组件配置管理器"""

    def __init__(self):
        self._cache: Optional[dict] = None

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

    def save(self, config: dict) -> bool:
        """保存配置"""
        try:
            config["version"] = 2
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
            stats[key] = {
                "enabled": enabled,
                "tokens": tokens,
                "range": estimate.get("range", ""),
                "note": estimate.get("note", ""),
                "editable": estimate.get("editable", False),
                "has_custom": bool(sec.get("custom_content", "").strip()),
                "configurable": estimate.get("configurable", False),
                "token_limit": sec.get("token_limit", 0),
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
        """更新单个组件的配置"""
        config = self.load()
        if section_key not in config.get("sections", {}):
            logger.warning("未知的配置组件: %s", section_key)
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
_manager: Optional[SystemPromptConfigManager] = None


def get_manager() -> SystemPromptConfigManager:
    global _manager
    if _manager is None:
        _manager = SystemPromptConfigManager()
    return _manager


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
