"""云枢能力平面与主线装配

把"哪些工具存在/多危险/谁能用"这件事收敛到 `data/tool_definitions/*.yaml`
（plane/effect/risk/tags），再让"不同 Agent 各管一条线"变成
`data/agent_lines/*.yaml` 里的一份**权重档案**。

    from agent.lines import get_line_registry, assemble

    reg = get_line_registry()
    profile = reg.effective("engineering")
    result = assemble(profile, [t["name"] for t in tools.list_tools()])
    # result.tools → 本轮该给模型看的工具（已按常驻/平面权重排好）

同一份档案的 `skills:` 走 `line_skill_pack()` → `SkillPack`：本线允许
注入哪些技能（空声明 = 不限制；详见 agent/lines/skillpack.py）。

四平面：resident（常驻）/ perceive（感知）/ act（行动）/ govern（治理）。
详见 docs/工具集评估与重分类报告.md §7 与 docs/主线装配指南.md。
"""

from .assembler import AssemblyResult, assemble, estimate_tokens
from .integration import (
    assemble_for_line,
    describe_line,
    line_skill_pack,
    line_whitelist,
    resolve_line_id,
)
from .models import (
    AGENT_LINES_DIR,
    EFFECTS,
    PLANES,
    RISKS,
    TOOL_DEFS_DIR,
    LineProfile,
    ToolMeta,
    invalidate_tool_meta_cache,
    load_tool_meta,
)
from .registry import (
    LineRegistry,
    LineRegistryError,
    get_line_registry,
    lines_dir,
)
from .skillpack import (
    MODE_UNRESTRICTED,
    MODE_WHITELIST,
    SOURCE_LABELS,
    SkillPack,
    invalidate_known_skills_cache,
    known_skill_ids,
    resolve_skill_pack,
)

__all__ = [
    # 模型
    "PLANES", "EFFECTS", "RISKS", "ToolMeta", "LineProfile",
    "load_tool_meta", "invalidate_tool_meta_cache", "TOOL_DEFS_DIR", "AGENT_LINES_DIR",
    # 注册表
    "LineRegistry", "LineRegistryError", "get_line_registry", "lines_dir",
    # 装配
    "assemble", "AssemblyResult", "estimate_tokens",
    # 接线层
    "assemble_for_line", "line_whitelist", "resolve_line_id", "describe_line",
    "line_skill_pack",
    # 技能包（L2 的 skills 声明 → 注入侧身份减法）
    "SkillPack", "resolve_skill_pack", "known_skill_ids",
    "invalidate_known_skills_cache", "MODE_UNRESTRICTED", "MODE_WHITELIST",
    "SOURCE_LABELS",
]
