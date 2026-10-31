# -*- coding: utf-8 -*-
"""**不建文件轨**的技能名单（H-3 裁定，2026-09-26；2026-10-31 复述）。

## 为什么需要这份名单

「有 skill.md」不是纯格式问题 —— 它会把技能**变成可检索、可被模型加载的实体**：

1. `data/skills_repo/<id>/skill.md` 是文件轨元数据索引（`load_metadata_index`）、
   `SkillLoader.list_all_metadata()`（模型可见技能目录）与 `loader.match()`（Layer-1 检索）
   的唯一数据源 ⇒ 建了实体就**进了候选池**；
2. 文件轨同时是 `description` / `description_zh` 的**唯一事实源**（G1-B），
   主轨那份退化为历史副本（UI 改描述会「200 但没生效」）；
3. 能力清单（`data/capability_manifest.json`）会把实体算成一条**能力**，
   连 `runtime_only_declarations` 也会把它移除 —— 等于对外声称「模型可发起调用」。

## 所以这两条**只留主轨**（`docs/audit_skill_governance/G1_DECISIONS.md` 决策 3）

- `global-core-principles`：**常驻行为准则**（「适用于所有对话…始终生效」）。产品意图是进
  system prompt 常驻段，不是被相似度检索出来再注入。G1-C §4 逐条验过 9 个消费点：它本来
  检索层不可达、正文全仓无读者 ⇒ 建文件轨是**反方向**。
- `skill`（易之三义）：**注入型指令**（`1. 编码前必输出 <san_yi_analysis>…`，且主轨
  description 曾被错填成指令内容）。它按显式调用/注入使用，不该被语义检索选中。

> 2026-10-31 实测：界面上对这两条点「生成文件轨」后，`.index/cache.json` 从 44 变 47、
> `sync_capability_manifest.py --check` 报「新增 global-core-principles / skill」、
> H-3 的 4 条不变量用例转红。故把结论**固化进代码**（本模块 + materialize 的 409），
> 而不是只写在文档里。

**不要生成文件轨**：需要它们「生效」时，走 prompt 装配（G1C §4.3 P1），不要 materialize。
"""
from __future__ import annotations

from typing import Dict, Optional

#: 技能 id → 拒绝建文件轨的理由（直接展示给操作者）。
NO_FILE_TRACK: Dict[str, str] = {
    "global-core-principles": (
        "global-core-principles 按 H-3 裁定**不建文件轨**：它是「对所有会话始终生效」的"
        "常驻行为准则（正文内联在主轨），建实体只会让它进检索索引与能力清单（"
        "`.index` 里多一条、`runtime_only_declarations` 里少一条），与产品意图相反。"
        "正确落位是 system prompt 常驻段（docs/audit_skill_governance/G1C.md §4.3）。"
    ),
    "skill": (
        "skill（易之三义）按 H-3 裁定**不建文件轨**：它是注入型指令（`编码前必输出 "
        "<san_yi_analysis>…`），按显式调用/注入使用，不该被语义检索选中；"
        "建了实体同样会把它推进候选池与能力清单。"
    ),
}


def no_file_track_reason(skill_id: str) -> Optional[str]:
    """该技能是否「按裁定不建文件轨」；是则返回给操作者看的理由。"""
    return NO_FILE_TRACK.get(skill_id)


def is_no_file_track(skill_id: str) -> bool:
    return skill_id in NO_FILE_TRACK
