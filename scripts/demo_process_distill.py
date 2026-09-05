# -*- coding: utf-8 -*-
"""过程蒸馏演示 — 真实 LLM 从知识库素材蒸馏并固化（临时仓库，不污染生产）。

前置：项目根 .env 配置了 LLM_API_KEY 且模型可达；否则自动降级规则提取。

用法：python scripts/demo_process_distill.py
"""
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from agent.process_distill.service import ProcessDistillService, build_default_llm
from agent.skills_mgmt import SkillsMgmtService
from agent.workflow_learning.generator import WorkflowGenerator
from agent.workflow_learning.matcher import WorkflowMatcher
from agent.workflow_learning.repository import WorkflowRepository


def main():
    llm = build_default_llm()
    print("llm built:", llm is not None, "| model:", getattr(llm, "model", ""))

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="pd_real_"))
    repo = WorkflowRepository(path=str(tmp / "wf.json"))
    wf_svc = type("_WF", (), {
        "get": lambda self, wid: repo.get(wid),
        "generator": WorkflowGenerator(repo, WorkflowMatcher()),
    })()
    skills_svc = SkillsMgmtService(
        store_path=str(tmp / "skills.json"),
        repo_path=str(tmp / "skills_repo"),
    )
    svc = ProcessDistillService(llm=llm, wf_svc=wf_svc,
                                skills_svc=skills_svc)

    # 真实素材: 知识库 wiki 复盘卡(只读)
    kb = pathlib.Path(
        "knowledge/wiki/insights/git-维护复盘-task06-gc优化.md")
    print("material exists:", kb.exists())
    res = svc.distill(paths=[str(kb)], artifacts=["workflow", "skill"],
                      available_tools=["bash", "read_file", "write_file"],
                      max_workers=1)
    print("ok:", res["ok"], "| llm_used:", res["llm_used"],
          "| method:", res["process"]["method"])
    print("name:", res["process"]["name"])
    print("steps:", len(res["process"]["steps"]))
    for s in res["process"]["steps"][:5]:
        print("  -", s["seq"], s["action"], "| tool:", s.get("tool", "(纯指令)"))
    print("workflow:", res["artifacts"]["workflow"])
    print("skill:", res["artifacts"]["skill"])
    print("tmp:", tmp)


if __name__ == "__main__":
    main()
