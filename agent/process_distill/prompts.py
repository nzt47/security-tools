"""过程蒸馏提示词模板（子代理蒸馏 + 规则提取降级）。

DISTILL_SYSTEM_PROMPT: 蒸馏子代理的系统提示词（约束输出为结构化步骤序列）。
DISTILL_USER_TEMPLATE: 用户输入模板，占位符 title/source/content/tool_hint。
输出 JSON 字段契约（与 DistilledStep 对齐）：
    name, description, steps[{action, tool, params, condition, note}],
    expected_output, trigger_patterns

设计约束：
    - 只允许从素材中提取内容，禁止臆造素材不存在的步骤。
    - tool 字段仅当素材明确对应某个云枢工具时才填，否则留空（纯指令步骤）。
    - 步骤须可复现：参数模板用 ${input} / ${prev_output} 引用，不写死具体值。
"""

import re  # noqa: E402  （RE 常量在文件下部使用；提示词区段保持在文件顶部便于审阅）

DISTILL_SYSTEM_PROMPT = """你是一位严谨的过程蒸馏者。阅读给定的素材（可能是其他 agent 的
编程过程复盘、SOP、技能说明或知识卡片），提取其中**可复现的步骤序列**。
要求：
1. 只提取素材中确实存在的内容，禁止添加素材中没有的步骤或细节。
2. 步骤按执行顺序编号；每步给出一句动作描述（action）。
3. 若该步可映射到一个明确的工具调用，填 tool（见可用工具提示）与参数模板；
   否则 tool 留空字符串，表示纯指令步骤。
4. 参数模板用 ${input} / ${prev_output} 引用输入与上一步输出，不写死具体值。
5. 输出 JSON，字段契约：
   {
     "name": "简短技能/流程名",
     "description": "一句话说明适用场景",
     "steps": [
       {"action": "动作描述", "tool": "tool_name 或空串",
        "params": {...}, "condition": "可选条件", "note": "可选边界说明"}
     ],
     "expected_output": "预期产出特征（可空）",
     "trigger_patterns": ["触发关键词 2-4 个"]
   }
禁止输出 JSON 以外的内容。"""

DISTILL_USER_TEMPLATE = """素材标题: {title}
素材来源: {source}
可用工具提示: {tool_hint}
素材内容:
{content}

请输出 JSON。"""

# 规则提取降级（LLM 不可用）：从正文按编号/条目行抽取步骤骨架。
# marker：数字编号（1. / 1、 / (1)）、"步骤 N"、或 -/* 列表项。
# 加粗标签行（**xxx** 单独成行）与纯装饰行不视为步骤。
RULE_STEP_RE = re.compile(
    r"^\s*(?:[-*]\s+|\d+[.、)）]|\(\d+\)|步骤\s*\d+)\s*[:：]?\s*(.+?)\s*$")

MAX_SOURCE_CHARS = 20000  # 单条素材送入 LLM 的上限（与 knowledge/distill 对齐）


def build_tool_hint(available_tools: list[str], max_tools: int = 40) -> str:
    """构造可用工具提示（供子代理把步骤映射到云枢真实工具）。"""
    if not available_tools:
        return "（未提供工具清单，tool 字段一律留空）"
    names = available_tools[:max_tools]
    return "、".join(f"`{n}`" for n in names) + (
        f"（共 {len(available_tools)} 个，仅列出前 {max_tools} 个）"
        if len(available_tools) > max_tools else ""
    )


# 清洗后若残留这些"非动作"特征则跳过（标题/标签/导航行等噪声）
_NOISE_HINTS = ("##", "###", "**", "```", "http://", "https://",
                "table", "图", "表 ")


def _clean_action(raw: str) -> str:
    """清洗单行动作文本：去 markdown 装饰、去首尾噪音。"""
    a = raw.strip()
    # 任务清单 "- [ ] **Step N: xxx**" → 保留 xxx
    m = re.match(r"\[\s*[ xX]?\]\s*\**\s*(?:step\s*\d+\s*[:：]\s*)?(.+)",
                 a, re.IGNORECASE)
    if m:
        a = m.group(1)
    a = re.sub(r"\*\*(.+?)\*\*", r"\1", a)   # 去加粗
    a = re.sub(r"\[(.+?)\]\(.+?\)", r"\1", a)  # 去链接
    a = a.replace("`", "").replace("#", "").strip()
    a = re.sub(r"\s+", " ", a)
    return a


def extract_rule_steps(content: str, source: str = "") -> list[dict]:
    """无 LLM 降级：按编号/有序条目行提取步骤骨架（仅动作描述，tool 全空）。"""
    steps: list[dict] = []
    for line in (content or "").splitlines():
        m = RULE_STEP_RE.match(line)
        if not m:
            continue
        action = _clean_action(m.group(1))
        if len(action) < 2:
            continue
        if any(h in action for h in _NOISE_HINTS):
            continue
        # 括号注释 / 纯引号短语（无动作动词上下文）→ 跳过
        if action.startswith("(") and ")" in action[:60]:
            continue
        steps.append({"action": action[:300], "tool": "", "source": source})
        if len(steps) >= 30:
            break
    return steps
