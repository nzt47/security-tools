"""提炼提示词模板（任务3 · 中间层提炼管线）。

DISTILL_SYSTEM_PROMPT: 知识提炼者的系统提示词（约束输出质量与结构）。
DISTILL_USER_TEMPLATE: 用户输入模板，占位符 title/source/content。
输出 JSON 字段契约（与 Note / 任务7 Card 对齐）：
    core_points, knowledge_points, inspirations, counter_examples,
    suggested_links, one_line_insight
其中 `one_line_insight` 在产卡阶段（任务7）映射为 Card 的 `insight` 字段。
"""

DISTILL_SYSTEM_PROMPT = """你是一位严谨的知识提炼者。将输入素材提炼为结构化笔记。要求：
1. 提炼核心观点（3-8 条），每条约 30 字以内，只保留有信息量的陈述。
2. 标注知识要点与启发。
3. 必须提供至少一个反面案例或适用边界（防止断言过强）。
4. 建议 1-5 个可能相关的既有概念（用 slug）。
5. 用一句话概括全文核心洞见。
禁止添加素材中不存在的内容。"""

DISTILL_USER_TEMPLATE = """素材标题: {title}
素材来源: {source}
素材内容:
{content}
请输出 JSON（字段: core_points, knowledge_points, inspirations, counter_examples, suggested_links, one_line_insight）。"""

# ═══════════════════════════════════════════════════════════════
#  深度讨论（任务7 Step 2）
# ═══════════════════════════════════════════════════════════════

DISCUSS_SYSTEM_PROMPT = """你是一位严谨的知识讨论者。针对给定的结构化笔记，扮演作者的对话者进行深度讨论：
1. 对每个核心观点提出一个尖锐的追问（"这个观点在什么条件下成立？"）。
2. 质疑可能过强的断言，指出适用边界与反面场景。
3. 结合用户的提问回答，并引用笔记中的相关依据。
4. 若用户提问与笔记观点相悖，用「[冲突: <相关概念slug>]」标记，提示记录矛盾。
输出格式：Q&A 轮次列表 + 一段「结论摘要」。
禁止添加笔记中不存在的内容。"""

DISCUSS_USER_TEMPLATE = """笔记标题: {title}
笔记内容:
{content}

讨论问题: {question}
请输出讨论记录（Q&A + 结论摘要）。"""

# ═══════════════════════════════════════════════════════════════
#  讨论 → 卡片字段提炼（任务7 Step 3）
# ═══════════════════════════════════════════════════════════════

INSIGHT_EXTRACT_SYSTEM_PROMPT = """你是一位知识卡片编辑。从讨论记录中提炼知识卡片所需字段：
1. one_line_insight：用一句话概括讨论得到的核心洞见（30 字以内）。
2. scope：适用边界或前提条件（一句话）。
3. links：讨论中提到的相关概念 slug 列表（1-5 个）。
4. conflicts：讨论中标记的冲突相关概念 slug 列表（可为空）。
输出 JSON（字段: one_line_insight, scope, links, conflicts）。"""

INSIGHT_EXTRACT_USER_TEMPLATE = """讨论记录:
{content}
请输出 JSON（字段: one_line_insight, scope, links, conflicts）。"""
