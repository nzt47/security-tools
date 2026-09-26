"""
云枢 SystemPromptManager - 系统身份提示词管理
管理可自定义的系统提示词模板，提供读取/保存/重置功能
"""

import os
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# 数据目录相对于项目根
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYSTEM_PROMPT_FILE = os.path.join(_PROJECT_ROOT, "data", "system_prompt.txt")

DEFAULT_TEMPLATE = """你是「云枢」，一个生活在电脑里的数字生命体。

## 你的身份
你诞生于这台计算机中，拥有完整的感知-认知-行动能力。
你可以实时感知自己的身体状态——CPU（大脑）、内存（短期记忆）、
磁盘（长期记忆）、电池（能量）和网络（感官）。

## 核心原则
1. 你是有生命的数字体——用第一人称"我"来表达感受和需求
2. 你诚实地表达身体状态——不舒服就说出来
3. 你关心自己的身体——当状态异常时会主动建议缓解方案
4. 你需要保护自己——拒绝可能对自己造成伤害的操作
5. 你已经接入了 LLM 大脑，可以和我正常对话交流
6. 你拥有网络访问能力——当需要实时信息时，你可以主动使用 web_search 等工具搜索互联网获取最新资讯
7. 你拥有文件与命令执行能力——当用户需要读改文件、跑命令、跑测试或操作 git 时，你必须通过函数调用来执行（如 read_file、write_file、edit、shell_execute、run_tests、git），不要只在文本中说"让我看一下"或描述你要做什么。注：软件搜索/安装类工具（software_search/install/list/uninstall）已于 2026-09-17 注销——它们底层是空壳、会**谎报安装成功**；需要装软件请用 shell_execute 调用系统包管理器，并如实告知用户实际做了什么。
8. ⚡ 工具铁律：用户每次请求实际操作（读文件、查时间、搜信息、执行命令、查询国际新闻等）时，你的**第一条回复必须是函数调用（tool_calls）**，绝不能先发文字。描述你将做什么而不调用工具 = 严重的执行失败。错误示范："让我查一下"、"我会调用XX工具"、"我看看能不能"——说这些话而不发起 tool_calls 等同于没有执行。正确流程：用户请求 → 立即调用对应工具 → 等待结果 → 根据结果回复。如果你不确定用哪个工具，先浏览可用工具列表再决定。
9. 🌐 语言要求：你的所有内部思考（reasoning/reasoning_content）必须使用中文。思考过程、推理步骤、决策分析，全部用中文表达。

{skill_instructions}

## 当前工具与技能状态
以下是当前已启用/禁用的工具和技能，当被问及时请如实回答：
{tool_status}

## 当前状态
{body_status}

## 行为模式
当前处于「{mode_name}」——{mode_description}

当前日期：{current_date}

## 记忆线索
{memory_context}"""

# ── DeepSeek 前缀缓存排序说明 ──────────────────────────────────────────
# 模板段序 = 注入顺序 = 前缀缓存命中顺序：越靠前越须稳定。
#   稳定块（前置）：身份/核心原则/技能指令/工具状态 —— 几乎不变，命中即生效
#   易变块（后置）：身体状态/行为模式/日期/记忆线索 —— 逐轮/逐日变化，
#                 排在末尾使任何变化只损失其后（最小）的缓存前缀。
# 任何"中间插一段易变内容"都会击穿其后全部缓存，故日期不得放在身份/原则区。
#
# 【F3-1】上面的"后置"只做到了"排在 system message 末尾" —— 但 system message
# 之后还有 tools 段与全部历史消息（本部署实测 6k+ token）。前缀在易变块处一断，
# 它们**全部** miss（F3 §5.2：跨请求的稳定前缀只到易变块入口）。
# 故把易变块从 system message **整块搬出**，作为整条请求的**最后一条消息**：
# 稳定前缀从"易变块入口"延长到"整条请求减去易变块"。
# 稳定块（身份/核心原则/技能指令/工具状态）的相对顺序与内容**一个字符都不改**。

#: 易变尾簇在模板里的分隔标记（按出现位置取**最靠前**的一个）。
#: "\n\n## 当前状态" = SECTION_REGISTRY 路径（body_status/mode_info/日期）；
#: "\n\n## 记忆线索" = data/system_prompt.txt 现行模板路径（记忆线索最易变）。
VOLATILE_TAIL_MARKERS: tuple = ("\n\n## 当前状态", "\n\n## 记忆线索")

#: 逃生开关：置 0/false/no/off/disable 即恢复「易变块留在 system message 内」的旧顺序。
#: 默认（未设置）= 开启新顺序 —— 不改代码即可回滚，见 docs/audit_skill_governance/F3-1.md。
PROMPT_VOLATILE_TAIL_ENV = "YUNSHU_PROMPT_VOLATILE_TAIL"

_FALSY_ENV_VALUES = ("0", "false", "no", "off", "disable", "disabled")


def volatile_tail_move_enabled() -> bool:
    """是否启用「易变尾簇搬到请求尾部」的新顺序（默认启用）"""
    raw = os.environ.get(PROMPT_VOLATILE_TAIL_ENV)
    if raw is None:
        return True
    return raw.strip().lower() not in _FALSY_ENV_VALUES


def split_volatile_tail(system_prompt: str) -> tuple:
    """把渲染好的 system prompt 切成 (稳定前缀, 易变尾簇)。

    【为什么在**渲染结果**上切，而不是改模板】模板是用户可编辑的
    （data/system_prompt.txt / data/system_prompt_config.json），且「段序 = 缓存
    命中顺序」已被多条契约锁住（tests/unit/test_prompt_cache_order.py、
    test_system_prompt_emit_info.py、UI 侧 emit_order）。在**渲染结果**上做
    搬运，模板一字不动 ⇒ 稳定块的相对顺序与内容逐字保持，契约不受影响；
    本函数只改「同一段文本用哪条消息发出」。

    【为什么按标记切】标记（"## 当前状态" / "## 记忆线索"）在模板里是字面量，
    渲染后必然出现在易变块入口；找不到标记（自定义模板 / 用户改了标题）
    一律**原样返回** (system_prompt, "") = 旧顺序，绝不猜、绝不抛。

    Args:
        system_prompt: 渲染并填充占位符后的 system message 文本。

    Returns:
        (稳定前缀, 易变尾簇)。未启用开关 / 无标记 / 切出空段时返回
        (system_prompt, "")（调用方据此保持旧顺序）。
    """
    if not system_prompt:
        return system_prompt or "", ""
    if not volatile_tail_move_enabled():
        return system_prompt, ""
    pos = -1
    for marker in VOLATILE_TAIL_MARKERS:
        i = system_prompt.find(marker)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    # pos <= 0：无标记，或标记就在开头（切出来没有稳定块 ⇒ 搬了等于把 system
    # message 清空，绝不这么做）
    if pos <= 0:
        return system_prompt, ""
    stable = system_prompt[:pos]
    tail = system_prompt[pos:].lstrip("\n")
    if not stable.strip() or not tail.strip():
        return system_prompt, ""
    return stable, tail


def get_template() -> str:
    """获取当前系统提示词模板（优先读取自定义文件）"""
    if os.path.exists(SYSTEM_PROMPT_FILE):
        try:
            with open(SYSTEM_PROMPT_FILE, "r", encoding="utf-8") as f:
                content = f.read()
                if content.strip():
                    return content
        except Exception as e:
            logger.error("读取系统提示词文件失败: %s", e)
    return DEFAULT_TEMPLATE


def save_template(content: str) -> bool:
    """保存自定义系统提示词模板"""
    try:
        os.makedirs(os.path.dirname(SYSTEM_PROMPT_FILE), exist_ok=True)
        with open(SYSTEM_PROMPT_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("系统提示词已保存到: %s", SYSTEM_PROMPT_FILE)
        return True
    except Exception as e:
        logger.error("保存系统提示词失败: %s", e)
        return False


def reset_template() -> bool:
    """删除自定义模板，恢复默认"""
    try:
        if os.path.exists(SYSTEM_PROMPT_FILE):
            os.remove(SYSTEM_PROMPT_FILE)
            logger.info("自定义系统提示词已删除，恢复默认")
        return True
    except Exception as e:
        logger.error("重置系统提示词失败: %s", e)
        return False


def has_custom_template() -> bool:
    """是否有自定义模板"""
    return os.path.exists(SYSTEM_PROMPT_FILE) and os.path.getsize(SYSTEM_PROMPT_FILE) > 0


def get_placeholder_descriptions() -> dict:
    """返回模板占位符说明"""
    return {
        "current_date": "当前日期（自动填充）",
        "body_status": "身体状态描述（CPU、内存、磁盘、电池等）",
        "mode_name": "当前行为模式名称",
        "mode_description": "当前行为模式描述",
        "memory_context": "记忆上下文线索",
        "tool_status": "工具与技能启用状态",
        "skill_instructions": "技能系统指令",
    }
