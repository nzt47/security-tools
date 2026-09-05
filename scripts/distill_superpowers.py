# -*- coding: utf-8 -*-
"""批量蒸馏 .superpowers/skills 外部方法论 → 云枢 skill 资产。

把 Claude 系外部 agent 的 SKILL.md（其他 agent 的编程方法论）逐条蒸馏
并固化为云枢可召回 skill（真实 skills_mgmt 服务：JSON 轨 + skills_repo
文件轨双写，幂等——重复运行不重复创建）。

用法：
    python scripts/distill_superpowers.py                # 顶层 18 个技能
    python scripts/distill_superpowers.py --all          # 含 claude-mem 子技能
    python scripts/distill_superpowers.py --name system* # 按 glob 过滤
    python scripts/distill_superpowers.py --dry-run      # 只看不写

前置：项目根 .env 配了 LLM_API_KEY 且可达；无 key 自动降级规则提取。
"""
import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.process_distill.service import ProcessDistillService
from agent.skills_mgmt import SkillsMgmtService
from agent.state_manager import get_workflow_learning_service

_SKILLS_ROOT = pathlib.Path(__file__).resolve().parents[1] / ".superpowers" / "skills"


def _collect_skill_dirs(all_skills: bool, name_filter: str):
    """收集待蒸馏的目录：优先含 SKILL.md 的技能目录。

    - 默认：顶层含 SKILL.md 的 15 个方法论技能（code-simplifier/ralph-loop
      是 agents/*.md 或其它格式，本轮跳过）；
    - --all：额外收 claude-mem 等子技能集的全部 SKILL.md 父目录。
    """
    found = []
    for sk in sorted(_SKILLS_ROOT.iterdir()):
        if not sk.is_dir():
            continue
        md = sk / "SKILL.md"
        if md.is_file():
            found.append(sk)
            continue
        if all_skills:
            for sub in sorted(sk.rglob("SKILL.md")):
                found.append(sub.parent)
    # 去重 + glob 过滤
    seen, out = set(), []
    for d in found:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        rel = d.relative_to(_SKILLS_ROOT).as_posix()
        if name_filter and not pathlib.PurePath(rel).match(name_filter):
            continue
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description="蒸馏 .superpowers/skills 到云枢")
    ap.add_argument("--all", action="store_true",
                    help="含 claude-mem 等子技能集（全部 SKILL.md）")
    ap.add_argument("--name", default="",
                    help="glob 过滤，如 'system*' / 'writing-*'")
    ap.add_argument("--dry-run", action="store_true", help="只看不写")
    ap.add_argument("--limit", type=int, default=0, help="最多蒸馏 N 个")
    args = ap.parse_args()

    dirs = _collect_skill_dirs(args.all, args.name)
    if args.limit:
        dirs = dirs[: args.limit]
    print(f"待蒸馏技能目录: {len(dirs)}")
    if args.dry_run:
        for d in dirs:
            print("  -", d.relative_to(_SKILLS_ROOT))
        return

    svc = ProcessDistillService(use_default_llm=True)
    skills_svc = SkillsMgmtService()
    # 显式注入真实服务，固化直接进生产仓库
    svc._skills_svc = skills_svc
    # workflow 走全局单例（若步骤映射到工具）
    wf_svc = get_workflow_learning_service()
    svc._wf_svc = wf_svc

    created, exists, failed, skipped = [], [], [], []
    for d in dirs:
        rel = d.relative_to(_SKILLS_ROOT).as_posix()
        md = d / "SKILL.md"
        # 只蒸馏该技能目录的主 SKILL.md（独立方法论），不递归其它 md
        target = str(md) if md.is_file() else str(d)
        try:
            res = svc.distill(paths=[target], artifacts=["skill", "workflow"],
                              session_id="superpowers-import")
            if not res.get("ok"):
                failed.append((rel, res.get("error", "?")))
                print(f"[FAIL] {rel}: {res.get('error', '?')}")
                continue
            sk = res["artifacts"].get("skill") or {}
            action = sk.get("action", "?")
            print(f"[{action.upper()}] {rel} → skill={sk.get('skill_id')} "
                  f"steps={len(res['process']['steps'])} "
                  f"method={res['process']['method']}")
            if action == "created":
                created.append(sk.get("skill_id"))
            elif action == "exists":
                exists.append(sk.get("skill_id"))
            else:
                skipped.append((rel, action))
        except Exception as e:  # noqa: BLE001
            failed.append((rel, str(e)))
            print(f"[EXC] {rel}: {e}")

    print("\n=== 汇总 ===")
    print(f"created: {len(created)} | exists: {len(exists)} | "
          f"skipped: {len(skipped)} | failed: {len(failed)}")
    if created:
        print("新建 skill:", ", ".join(created[:10]))
    if failed:
        print("失败:", failed[:5])


if __name__ == "__main__":
    main()
