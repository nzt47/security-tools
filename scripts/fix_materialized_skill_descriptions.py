"""A 方案：给 6 个「只在主轨」的技能补正确 description，并同步文件轨 front matter。

背景（2026-10-30）：Owner 在 UI 给 8 个技能点了「生成文件轨」，其中 3 份主轨 description
是正文碎片（'```markdown' / '**Implicit signals...**' / '- Technical implementation...'），
直接入库会让「文件轨说明」成为召回的唯一来源却带着垃圾文本。
本脚本只改 description 一个字段：主轨走 SkillStore.upsert（生产写路径），文件轨走
file_store.update_meta（最小侵入，逐字节保留其余 front matter）。

用法： python scripts/fix_materialized_skill_descriptions.py [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.skills_mgmt.service import SkillsMgmtService

NEW: dict[str, str] = {
    "templates": "智能体身份与人格模板库：提供 SOUL.md 式身份底稿（核心身份、性格、语气、角色边界与示例），用来给分身写「它是谁」。",
    "adaptation": "智能体学习与适应：从隐式信号（用户忽略的部分、追问、纠正）里读偏好并据此调整行为，而不是只照显式指令改。",
    "boundaries": "智能体角色边界：把主动性分级（可直接做 / 先问再做 / 不能做）、授权范围与停手条件写清楚。",
    "san_yi_skills-py": "易经三义编程工具集：用「不易 / 变易 / 简易」三义做结构化输出（BuYiResult / BianYiResult / JianYiResult）与校验。",
    "skill-card": "Define agent identity, personality, voice, and boundaries to create assistants that feel authentic rather than generic.",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入（默认 dry-run）")
    args = ap.parse_args()
    svc = SkillsMgmtService()
    failed = 0
    for sid, new_desc in NEW.items():
        skill = svc.store.get(sid)
        if skill is None:
            print("[skip] 主轨没有 " + sid)
            continue
        old = (skill.description or "")
        tag = "apply" if args.apply else "dry  "
        print("[%s] %s" % (tag, sid))
        print("   old: %r" % old[:90])
        print("   new: %r" % new_desc[:90])
        if not args.apply:
            continue
        skill.description = new_desc
        svc.store.upsert(skill)
        try:
            svc.file_store.update_meta(sid, {"description": new_desc})
            print("   文件轨已同步")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("   [FAIL] 文件轨同步失败: %s" % e)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
