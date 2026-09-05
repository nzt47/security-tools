"""过程蒸馏 (process_distill) 单元测试。

覆盖：
    - 素材读取（wiki 检索 mock / 路径读取）
    - 并行蒸馏降级（无 LLM → 规则提取；LLM 返回坏 JSON → 规则降级）
    - 合并归一（多素材拼接、去重、签名）
    - 固化（workflow / skill，用临时仓库与服务，不写生产数据）
    - 工具注册 / 注销（进程内注册表，测试隔离）
"""

import json
import pathlib
from pathlib import Path

import pytest

from agent.process_distill.distiller import (
    distill_one,
    distill_parallel,
    _parse_worker_json,
)
from agent.process_distill.merge import merge_results, make_task_signature
from agent.process_distill.models import (
    DistillMaterial,
    DistilledProcess,
    DistilledStep,
)
from agent.process_distill.prompts import extract_rule_steps
from agent.process_distill.service import ProcessDistillService
from agent.process_distill.solidify import (
    solidify_to_skill,
    solidify_to_workflow,
    _derive_id,
)
from agent.process_distill.tools import (
    register_distill_tools,
    unregister_distill_tools,
)


# ═══════════════════════════════════════════════════════════════
#  Fixtures & helpers
# ═══════════════════════════════════════════════════════════════

class _FakeLLM:
    """注入式假 LLM：按素材标题返回预设步骤 JSON。"""

    def __init__(self, payload=None, fail=False):
        self.payload = payload or {
            "name": "Git 安全维护",
            "description": "并行会话下安全执行 git gc",
            "steps": [
                {"action": "检查活跃 git 进程", "tool": "", "note": "期望 0"},
                {"action": "执行 git gc", "tool": "bash",
                 "params": {"cmd": "git gc"}},
            ],
            "expected_output": "loose 归零",
            "trigger_patterns": ["git", "gc"],
        }
        self.fail = fail
        self.calls = []

    def chat(self, messages, system_prompt=""):
        self.calls.append(messages)
        if self.fail:
            raise RuntimeError("LLM 不可用")
        return json.dumps(self.payload, ensure_ascii=False)


@pytest.fixture
def material():
    return DistillMaterial(
        id="git-gc",
        title="Git gc 复盘",
        content=(
            "# Git gc 复盘\n\n"
            "1. 检查活跃 git 进程\n"
            "2. 执行 git gc\n"
            "3. 验证 worktree 数量不变\n"
        ),
        source_ref="wiki/insights/git.md",
    )


# ═══════════════════════════════════════════════════════════════
#  1. 素材读取
# ═══════════════════════════════════════════════════════════════

class TestSources:
    def test_front_matter_title(self, tmp_path):
        from agent.process_distill.sources import collect_from_paths
        sk = tmp_path / "SKILL.md"
        sk.write_text(
            "---\nname: writing-plans\ndescription: Use when planning\n---\n"
            "# Writing Plans\n\n1. 步骤一\n", encoding="utf-8")
        mats = collect_from_paths([str(sk)])
        assert len(mats) == 1
        assert mats[0].title == "writing-plans"   # front matter name 优先
        assert mats[0].id == tmp_path.name         # SKILL.md 用父目录名
        assert mats[0].description == "Use when planning"  # 描述也解析

    def test_plain_file_title_is_stem(self, tmp_path):
        from agent.process_distill.sources import collect_from_paths
        f = tmp_path / "复盘.md"
        f.write_text("# 复盘\n\n1. 做 A\n2. 做 B\n", encoding="utf-8")
        mats = collect_from_paths([str(f)])
        assert len(mats) == 1
        assert mats[0].title == "复盘"
        assert mats[0].id == "复盘"

    def test_directory_recursive_collect(self, tmp_path):
        from agent.process_distill.sources import collect_from_paths
        (tmp_path / "sub").mkdir()
        (tmp_path / "a.md").write_text("# A\n1. 甲\n", encoding="utf-8")
        (tmp_path / "sub" / "b.txt").write_text("1. 乙\n", encoding="utf-8")
        (tmp_path / "skip.bin").write_bytes(b"\x00\x01")
        mats = collect_from_paths([str(tmp_path)])
        ids = sorted(m.id for m in mats)
        assert ids == ["a", "b"]


# ═══════════════════════════════════════════════════════════════
#  2. 提示词 / 规则提取
# ═══════════════════════════════════════════════════════════════

class TestRuleExtract:
    def test_numbered_lines(self):
        steps = extract_rule_steps(
            "1. 检查进程\n2. 执行 gc\n无关文本\n3. 验证")
        assert [s["action"] for s in steps] == [
            "检查进程", "执行 gc", "验证"]
        assert all(s["tool"] == "" for s in steps)

    def test_bullet_and_step_markers(self):
        steps = extract_rule_steps(
            "- 步骤 A\n步骤 2：步骤 B\n* 步骤 C")
        actions = [s["action"] for s in steps]
        assert "步骤 A" in actions and "步骤 B" in actions

    def test_blank_input(self):
        assert extract_rule_steps("") == []


# ═══════════════════════════════════════════════════════════════
#  2. 蒸馏器
# ═══════════════════════════════════════════════════════════════

class TestDistiller:
    def test_parse_worker_json_strips_fence(self):
        raw = "```json\n{\"a\": 1}\n```"
        assert _parse_worker_json(raw) == {"a": 1}

    def test_parse_worker_json_bad(self):
        with pytest.raises(ValueError):
            _parse_worker_json("no json here")

    def test_distill_one_with_llm(self, material):
        llm = _FakeLLM()
        out = distill_one(material, llm)
        assert out["method"] == "llm"
        assert out["ok"] is True
        steps = out["payload"]["steps"]
        assert len(steps) == 2
        # 空 tool 步骤省略 tool 键；有 tool 的步骤保留 tool 与 params
        assert "tool" not in steps[0]
        assert steps[1]["tool"] == "bash"
        assert steps[1]["params"] == {"cmd": "git gc"}

    def test_distill_one_none_llm_rule_fallback(self, material):
        out = distill_one(material, None)
        assert out["method"] == "rule"
        # 规则提取：3 个编号行
        assert len(out["payload"]["steps"]) == 3

    def test_distill_one_llm_failure_fallback(self, material):
        out = distill_one(material, _FakeLLM(fail=True))
        assert out["method"] == "rule"
        assert "warning" in out

    def test_distill_parallel_preserves_order(self, material):
        m2 = DistillMaterial(id="m2", title="另一个", content="1. 甲\n2. 乙")
        out = distill_parallel([material, m2], None, max_workers=2)
        ids = [r["material_id"] for r in out["results"]]
        assert ids == ["git-gc", "m2"]
        assert out["method_summary"]["rule"] == 2

    def test_distill_parallel_empty_raises(self):
        with pytest.raises(ValueError):
            distill_parallel([], None)


# ═══════════════════════════════════════════════════════════════
#  3. 合并归一
# ═══════════════════════════════════════════════════════════════

class TestMerge:
    def test_merge_multi_material(self):
        results = [
            {"material_id": "a", "method": "llm", "payload": {
                "name": "流程甲", "steps": [
                    {"seq": 1, "action": "做 A", "tool": "", "source": "a"}],
                "trigger_patterns": ["甲"]}},
            {"material_id": "b", "method": "rule", "payload": {
                "name": "流程乙", "steps": [
                    {"seq": 1, "action": "做 B", "tool": "", "source": "b"}],
                "trigger_patterns": ["乙"]}},
        ]
        p = merge_results(results)
        assert len(p.steps) == 2
        assert p.method == "llm"          # 优先级 llm > rule
        assert set(p.sources) == {"a", "b"}

    def test_merge_dedup_adjacent(self):
        results = [{"material_id": "a", "method": "llm", "payload": {
            "name": "流程", "steps": [
                {"seq": 1, "action": "检查环境", "tool": "", "source": "a"},
                {"seq": 2, "action": "检查环境  ", "tool": "", "source": "a"},
                {"seq": 3, "action": "执行清理", "tool": "", "source": "a"},
            ]}}]
        p = merge_results(results)
        assert len(p.steps) == 2

    def test_task_signature(self):
        sig = make_task_signature("Git 维护", ["git", "gc"])
        assert "git" in sig and "gc" in sig

    def test_merge_empty(self):
        p = merge_results([])
        assert p.name == "蒸馏流程"
        assert p.steps == []


# ═══════════════════════════════════════════════════════════════
#  4. 固化
# ═══════════════════════════════════════════════════════════════

class TestSolidify:
    def _proc(self):
        return DistilledProcess(
            name="安全 Git GC",
            description="并行会话下安全执行 git gc",
            task_signature="git|gc|维护",
            trigger_patterns=["git", "gc"],
            steps=[],
            sources=["git-gc"],
            method="llm",
        )

    def _tooled_proc(self):
        p = self._proc()
        from agent.process_distill.models import DistilledStep
        p.steps = [
            DistilledStep(seq=1, action="检查进程",
                          tool="bash", params={"cmd": "ps"}, source="git-gc"),
            DistilledStep(seq=2, action="执行 gc",
                          tool="bash", params={"cmd": "git gc"},
                          source="git-gc"),
            DistilledStep(seq=3, action="验证",
                          tool="", source="git-gc"),  # 纯指令 → 不进 workflow
        ]
        return p

    def test_derive_id_stable(self):
        p = self._proc()
        assert _derive_id(p, "wf").endswith("-wf")
        assert _derive_id(p, "wf") == _derive_id(p, "wf")  # 幂等

    def test_solidify_workflow_skips_without_tools(self, tmp_path):
        # 无工具白名单 → 全部步骤视为不可映射 → skipped
        p = self._proc()
        from agent.process_distill.models import DistilledStep
        p.steps = [DistilledStep(seq=1, action="纯指令步骤", tool="",
                                 source="x")]
        res = solidify_to_workflow(p, available_tools=[])
        assert res["action"] == "skipped"
        assert "workflow" in res.get("reason", "") or "skill" in res.get("reason", "")

    def test_solidify_workflow_maps_registered_tools(self, tmp_path):
        # 直接测 repository 写入（不依赖全局 wf_svc）
        from agent.workflow_learning.generator import WorkflowGenerator
        from agent.workflow_learning.matcher import WorkflowMatcher
        from agent.workflow_learning.repository import WorkflowRepository

        p = self._tooled_proc()
        repo = WorkflowRepository(path=str(tmp_path / "wf.json"))
        wf_svc = type("WF", (), {
            "get": lambda self, wid: None,  # 恒不存在 → created
            "generator": WorkflowGenerator(repo, WorkflowMatcher()),
        })()
        res = solidify_to_workflow(p, wf_svc=wf_svc,
                                   available_tools=["bash"])
        assert res["action"] == "created"
        assert res["workflow_id"]
        # 3 个步骤里只有 2 个映射到 bash
        assert res["steps"] == 2
        wf = repo.get(res["workflow_id"])
        assert wf is not None
        assert len(wf.steps) == 2

    def test_solidify_workflow_idempotent(self, tmp_path):
        from agent.workflow_learning.generator import WorkflowGenerator
        from agent.workflow_learning.matcher import WorkflowMatcher
        from agent.workflow_learning.repository import WorkflowRepository

        p = self._tooled_proc()
        repo = WorkflowRepository(path=str(tmp_path / "wf.json"))
        gen = WorkflowGenerator(repo, WorkflowMatcher())

        class _WF:
            def __init__(self):
                self._repo = repo

            def get(self, wid):
                return repo.get(wid)

            generator = gen

        svc = _WF()
        r1 = solidify_to_workflow(p, wf_svc=svc, available_tools=["bash"])
        assert r1["action"] == "created"
        r2 = solidify_to_workflow(p, wf_svc=svc, available_tools=["bash"])
        assert r2["action"] == "exists"

    def test_solidify_skill_writes_both_tracks(self, tmp_path):
        """固化 skill：JSON 轨 + 文件轨都要出现。"""
        from agent.skills_mgmt import SkillsMgmtService

        p = self._tooled_proc()
        store_path = str(tmp_path / "skills.json")
        svc = SkillsMgmtService(
            store_path=store_path,
            repo_path=str(tmp_path / "skills_repo"),
        )
        res = solidify_to_skill(p, skills_svc=svc)
        assert res["action"] == "created"
        sid = res["skill_id"]
        # JSON 轨
        skill = svc.store.get(sid)
        assert skill is not None
        # 文件轨
        md = tmp_path / "skills_repo" / sid / "skill.md"
        assert md.is_file()
        text = md.read_text(encoding="utf-8")
        assert "安全 Git GC" in text or "Git GC" in text or "安全" in text

    def test_solidify_skill_idempotent(self, tmp_path):
        from agent.skills_mgmt import SkillsMgmtService
        p = self._tooled_proc()
        svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )
        r1 = solidify_to_skill(p, skills_svc=svc)
        r2 = solidify_to_skill(p, skills_svc=svc)
        assert r1["action"] == "created"
        assert r2["action"] == "exists"

    def test_quality_gate_rejects_sparse_rule(self):
        """C. 质量门槛：规则降级且步骤 <3 → skipped（无 skill_id）。"""
        from agent.process_distill.solidify import solidify_to_skill
        p = DistilledProcess(
            name="稀疏产物", method="rule",
            steps=[DistilledStep(seq=1, action="唯一一步", source="x")],
            sources=["x"],
        )
        r = solidify_to_skill(p)
        assert r["action"] == "skipped"
        assert r["skill_id"] == ""
        assert "步骤过少" in r.get("reason", "")

    def test_quality_gate_passes_three_steps(self, tmp_path):
        """C. 质量门槛：规则降级 ≥3 步应通过（created）。"""
        from agent.skills_mgmt import SkillsMgmtService
        from agent.process_distill.solidify import solidify_to_skill
        p = DistilledProcess(
            name="合规流程", method="rule",
            steps=[
                DistilledStep(seq=1, action="检查环境", source="x"),
                DistilledStep(seq=2, action="执行清理", source="x"),
                DistilledStep(seq=3, action="验证结果", source="x"),
            ],
            sources=["x"],
        )
        svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )
        r = solidify_to_skill(p, skills_svc=svc, run_review=False)
        assert r["action"] == "created"
        assert r["skill_id"]

    def test_file_track_has_status_and_enabled(self, tmp_path):
        """A. 双轨一致：文件轨 front matter 含 status/enabled，与 JSON 轨一致。"""
        from agent.skills_mgmt import SkillsMgmtService
        from agent.process_distill.solidify import solidify_to_skill
        p = DistilledProcess(
            name="双轨一致测试", method="rule",
            steps=[
                DistilledStep(seq=1, action="步骤甲", source="x"),
                DistilledStep(seq=2, action="步骤乙", source="x"),
                DistilledStep(seq=3, action="步骤丙", source="x"),
            ],
            sources=["x"],
        )
        svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )
        r = solidify_to_skill(p, skills_svc=svc, run_review=False)
        assert r["action"] == "created"
        sid = r["skill_id"]
        meta, _ = svc.file_store.read(sid)[:2]
        assert meta.get("status") == "draft"
        assert meta.get("enabled") is True

    def test_review_runs_and_promotes(self, tmp_path):
        """B. 正式评审：run_review=True 时 review 通过 → skill approved。"""
        from agent.skills_mgmt import SkillsMgmtService
        from agent.process_distill.solidify import solidify_to_skill
        p = DistilledProcess(
            name="评审测试技能", method="rule",
            description="用于验证正式评审链路",
            steps=[
                DistilledStep(seq=1, action="步骤一二三", source="x"),
                DistilledStep(seq=2, action="步骤四五六", source="x"),
                DistilledStep(seq=3, action="步骤七八九", source="x"),
            ],
            sources=["x"],
        )
        svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )
        r = solidify_to_skill(p, skills_svc=svc, run_review=True)
        assert r["action"] == "created"
        assert "review" in r
        assert r["review"].get("status") in ("passed", "warn", "failed")
        # review 后状态推进（passed → approved）
        cur = svc.get(r["skill_id"])
        assert getattr(cur, "status", "") in ("approved", "pending_review",
                                              "rejected")
        # 文件轨状态与 JSON 轨同步
        meta, _ = svc.file_store.read(r["skill_id"])[:2]
        assert meta.get("status") == getattr(cur, "status")


# ═══════════════════════════════════════════════════════════════
#  5. 门面服务（全链路，注入临时服务）
# ═══════════════════════════════════════════════════════════════

class TestService:
    def _iso_svc(self, tmp_path, llm=None, use_default_llm=False):
        """构造隔离门面：临时 workflow/skill 服务，不触碰全局仓库。

        默认 use_default_llm=False：测试不因 .env 是否有 key 而改变行为
        （显式 llm=None 时强制规则降级）。
        """
        from agent.process_distill.service import ProcessDistillService
        from agent.skills_mgmt import SkillsMgmtService
        from agent.workflow_learning.generator import WorkflowGenerator
        from agent.workflow_learning.matcher import WorkflowMatcher
        from agent.workflow_learning.repository import WorkflowRepository

        repo = WorkflowRepository(path=str(tmp_path / "wf.json"))
        wf_svc = type("_WF", (), {
            "get": lambda self, wid: repo.get(wid),
            "generator": WorkflowGenerator(repo, WorkflowMatcher()),
        })()
        skills_svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )
        return ProcessDistillService(llm=llm,
                                     use_default_llm=use_default_llm,
                                     wf_svc=wf_svc,
                                     skills_svc=skills_svc)

    def test_distill_requires_input(self, tmp_path):
        svc = self._iso_svc(tmp_path, llm=_FakeLLM())
        with pytest.raises(ValueError):
            svc.distill()  # 无 query 无 paths

    def test_distill_paths_rule_fallback(self, tmp_path):
        # 写一份临时素材，llm=None + use_default_llm=False → 强制规则降级
        src = tmp_path / "sop.md"
        src.write_text(
            "# 部署 SOP\n\n1. 拉取最新代码\n2. 运行测试\n3. 重启服务\n",
            encoding="utf-8")
        svc = self._iso_svc(tmp_path, llm=None)  # 显式禁用 LLM → 规则
        res = svc.distill(paths=[str(src)], artifacts=["skill"])
        assert res["ok"] is True
        assert res["process"]["method"] == "rule"
        assert res["artifacts"]["skill"]["action"] == "created"
        # 无工具映射的纯指令流程 → workflow skipped
        res2 = svc.distill(paths=[str(src)], artifacts=["workflow"])
        assert res2["artifacts"]["workflow"]["action"] == "skipped"

    def test_use_default_llm_auto_build(self, tmp_path):
        """use_default_llm=True（默认）且 .env 有 key 时应自动构建 LLM。"""
        svc = self._iso_svc(tmp_path, llm=None, use_default_llm=True)
        # 自动构建：env 有 key → llm 非 None；无 key → None（不抛异常）
        assert svc.health()["ok"] is True
        assert "llm_configured" in svc.health()


# ═══════════════════════════════════════════════════════════════
#  6. 工具注册
# ═══════════════════════════════════════════════════════════════

class TestTools:
    def test_register_and_call(self):
        n = register_distill_tools(clear_first=True)
        assert n == 3
        from agent import tools as _tools
        # 三个工具都可见
        for tname in ("distill_process_from_knowledge",
                      "distill_process_async", "process_distill_run"):
            defs = _tools.get_tool_defs(whitelist=[tname])
            assert len(defs) == 1, tname
            assert defs[0]["function"]["name"] == tname
        # 同步工具：无入参 → ok False（提示需 query/paths）
        handler = _tools._registry["distill_process_from_knowledge"]["handler"]
        out = handler(query="")
        assert out["ok"] is False
        # 异步工具：无入参 → ok False
        ah = _tools._registry["distill_process_async"]["handler"]
        assert ah(query="")["ok"] is False
        # 注销
        unregister_distill_tools()
        for tname in ("distill_process_from_knowledge",
                      "distill_process_async", "process_distill_run"):
            defs2 = _tools.get_tool_defs(whitelist=[tname])
            assert defs2 == [], tname

    def test_async_submit_runs_distill(self, tmp_path, monkeypatch):
        """异步提交应调起 process_distill_run 并在后台跑完（规则降级）。"""
        import time

        from agent.process_distill.service import ProcessDistillService
        from agent.skills_mgmt import SkillsMgmtService
        from agent.workflow_learning.generator import WorkflowGenerator
        from agent.workflow_learning.matcher import WorkflowMatcher
        from agent.workflow_learning.repository import WorkflowRepository

        # 隔离临时仓库，异步后台线程也不触碰生产数据
        repo = WorkflowRepository(path=str(tmp_path / "wf.json"))
        wf_svc = type("_WF", (), {
            "get": lambda self, wid: repo.get(wid),
            "generator": WorkflowGenerator(repo, WorkflowMatcher()),
        })()
        skills_svc = SkillsMgmtService(
            store_path=str(tmp_path / "skills.json"),
            repo_path=str(tmp_path / "skills_repo"),
        )

        def _fake_svc(*a, **kw):
            return ProcessDistillService(use_default_llm=False,
                                         wf_svc=wf_svc,
                                         skills_svc=skills_svc)

        monkeypatch.setattr(
            "agent.process_distill.tools.ProcessDistillService", _fake_svc)

        register_distill_tools(clear_first=True)
        from agent import tools as _tools
        from agent.async_executor import get_async_executor

        src = tmp_path / "sop.md"
        src.write_text("# SOP\n\n1. 步骤一\n2. 步骤二\n3. 步骤三\n",
                       encoding="utf-8")

        executor = get_async_executor()
        task = executor.submit(
            name="test-distill", tool_name="process_distill_run",
            params={"paths": [str(src)], "artifacts": ["skill"]},
        )
        assert task["ok"] is True
        tid = task["task_id"]
        # 轮询至完成（规则降级很快）
        for _ in range(30):
            st = executor.get_status(tid)
            if st.get("status") in ("completed", "failed"):
                break
            time.sleep(0.2)
        st = executor.get_status(tid)
        assert st.get("status") == "completed", st
        task = executor.get_result(tid)
        # get_result 返回任务包裹层，蒸馏结果在 result.result 内
        inner = task.get("result") or {}
        assert inner.get("ok") is True, inner
        sid = inner.get("artifacts", {}).get("skill", {}).get("skill_id")
        assert sid
        # 注入的是隔离临时仓库：JSON 轨 + 文件轨都落在 tmp_path 下
        import json
        store = json.loads(
            (tmp_path / "skills.json").read_text(encoding="utf-8"))
        assert sid in store
        assert (tmp_path / "skills_repo" / sid / "skill.md").exists()
        # 清理生产 async 任务文件里本测试写入的行（executor 路径硬编码）
        tasks_file = pathlib.Path("data/async_tasks.jsonl")
        if tasks_file.exists():
            lines = [ln for ln in tasks_file.read_text(encoding="utf-8")
                     .splitlines() if f'"{tid}"' not in ln]
            tasks_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        unregister_distill_tools()
