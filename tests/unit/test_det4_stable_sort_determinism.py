# -*- coding: utf-8 -*-
"""DET-4 · 「set 交给稳定排序 / set 直接变有序产物」这一族的用户可见落点守卫。

本卡覆盖的是 DET-3 §4.2「态五」里的落点：**到达用户可见输出**的那些。

判据分两层：
1. **跨进程**（PYTHONHASHSEED 不同 ⇒ set 迭代序不同）：产物必须逐位相同。
   每个种子起**新解释器**（PYTHONHASHSEED 必须在解释器启动前设好）。
2. **次序定义**：产物必须等于「该子系统本来就有的那条确定次序」的结果，
   而不是恰好等于某一次采样。

另外每条守卫都配一条**源码级反向守卫**（禁止退回 list(set(...)) / set 字面量），
防止"以后有人抄回去"。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SEEDS = ("0", "1", "2", "random", "random")


def _run_across_seeds(code: str) -> list:
    """每种子一个新解释器跑同一段代码，返回逐种子的 stdout（去空行）。"""
    env0 = dict(os.environ)
    env0["PYTHONUTF8"] = "1"
    env0["PYTHONIOENCODING"] = "utf-8"
    env0["PYTHONPATH"] = str(REPO)
    env0["AGENT_HYBRID_EMBEDDING"] = "0"
    outs = []
    for seed in SEEDS:
        env = dict(env0)
        env["PYTHONHASHSEED"] = seed
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env=env, cwd=str(REPO), timeout=300,
        )
        assert proc.returncode == 0, (
            "种子 %s 子进程失败：rc=%s\n%s" % (seed, proc.returncode, proc.stderr[-2000:]))
        outs.append([ln for ln in proc.stdout.splitlines() if ln.strip()])
    return outs


def _code_only(rel: str) -> str:
    """只取源码行（丢掉整行注释）：本卡的说明性注释里会**引用**被禁的写法。"""
    lines = (REPO / rel).read_text(encoding="utf-8").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def _assert_one_kind(outs: list, what: str) -> None:
    kinds = {}
    for seed, lines in zip(SEEDS, outs):
        kinds.setdefault("\n".join(lines), []).append(seed)
    assert len(kinds) == 1, (
        "%s 跨进程不同（set 迭代序泄漏到用户可见输出）：\n%s"
        % (what, "\n".join("seeds=%s -> %s" % (v, k) for k, v in kinds.items())))


# ══════════════════════════════════════════════════════════════════
#  落点 16：agent/text_tools.py —— 工具 humanize_zh 的返回值 matches
# ══════════════════════════════════════════════════════════════════

TEXT_TOOLS_CORPUS = "\n".join([
    "此外，这一实践至关重要，值得深入探讨。",
    "我们强调其持久的价值，并增强团队的培养能力。",
    "它获得了突出成果，展示了复杂的相互作用。",
    "这一格局是关键的，也是一次织锦般的证明。",
    "作为一次尝试，它见证了宝贵的积累，充满活力的成果不可或缺。",
    "项目标志着一次转变，凸显了努力，强调了协作，彰显了责任。",
    "这象征着长期主义，塑造着文化，代表了焦点。",
    "它留下了不可磨灭的印记，深深植根于实践，是极其重要的节点。",
    "这是一次至关重要的转折，也是核心的成果。",
    "它反映了更广泛的趋势，并为后续工作奠定基础。",
    "独立报道与地方媒体均有提及，区域媒体也做了转述。",
    "业内人士表示，多个来源指出，有分析指出该结论。",
    "拥有丰富的资源，致力于展示其开创性的自然之美。",
    "尽管存在这些挑战，团队仍在推进。",
    "当然！希望这对您有帮助，请告诉我你的想法。",
    "截至 2024 年，根据我最后的训练更新，信息有限。",
    "值得注意的是，总的来说，该方案可能或许大概可行。",
    "总而言之，未来可期，我们将继续努力创造更大价值。",
])

_TEXT_TOOLS_SNIPPET = '''
import json
from agent.text_tools import humanize_zh, PATTERN_1_RE, PATTERN_7_RE
CORPUS = %r
res = humanize_zh(CORPUS, aggressive=False)
print(json.dumps({str(p["pattern_id"]): p["matches"] for p in res["detected_patterns"]},
                 ensure_ascii=False, sort_keys=True))
'''

_MUTATION_TEXT = (
    "# 【DET-4】去重必须保留**发现序**：list(set(mN)) 的次序取自字符串哈希（随进程变），\r\n"
    "# 而模式 1 / 模式 7 的候选紧接着还有 matches[:10] 截断 ⇒ 不只是顺序，**成员**也会跨进程不同\r\n"
    "# （实测 5 个种子 5 种成员集）。该返回值是工具 humanize_zh 的结果，直接进模型上下文。\r\n"
    "# 改法：dict.fromkeys 去重且保留 findall 的文本发现序（= 该子系统本来就有的确定次序），\r\n"
    "# 与 DET-2/DET-3「保留主键不变、只补一个确定的次级键」同口径。\r\n"
)


class TestTextToolsMatchesAreDeterministic:
    """工具 humanize_zh 的 detected_patterns[*].matches：模型可见的返回值。"""

    def test_matches_are_identical_across_hash_seeds(self):
        outs = _run_across_seeds(_TEXT_TOOLS_SNIPPET % TEXT_TOOLS_CORPUS)
        _assert_one_kind(outs, "humanize_zh 的 matches")

    def test_truncated_patterns_keep_members_and_follow_discovery_order(self):
        """模式 1 / 7 有 matches[:10] 截断 ⇒ 必须按**文本发现序**取前 10（成员才不漂）。"""
        from agent.text_tools import humanize_zh, PATTERN_1_RE, PATTERN_7_RE
        res = humanize_zh(TEXT_TOOLS_CORPUS, aggressive=False)
        by_id = {p["pattern_id"]: p["matches"] for p in res["detected_patterns"]}
        assert 1 in by_id and 7 in by_id, "语料必须同时命中模式 1 与模式 7"
        for pid, rx in ((1, PATTERN_1_RE), (7, PATTERN_7_RE)):
            expected = list(dict.fromkeys(rx.findall(TEXT_TOOLS_CORPUS)))
            assert len(expected) > 10, "语料必须让模式 %d 的去重候选超过 10 个（踩到截断点）" % pid
            assert by_id[pid] == expected[:10], (
                "模式 %d 的 matches 不是「发现序前 10 个」⇒ 截断点上的成员仍取自迭代序" % pid)

    def test_non_truncated_patterns_follow_discovery_order(self):
        """未截断的模式（2/4/5/8/19/…）也必须是发现序，不是哈希序。"""
        from agent.text_tools import humanize_zh
        res = humanize_zh(TEXT_TOOLS_CORPUS, aggressive=False)
        by_id = {p["pattern_id"]: p["matches"] for p in res["detected_patterns"]}
        assert by_id[2] == ["独立报道", "地方媒体", "区域媒体"]
        assert by_id[19] == ["当然！", "希望这对您有帮助", "请告诉我"]

    def test_no_bare_set_dedupe_remains_in_text_tools(self):
        src = _code_only("agent/text_tools.py")
        assert not re.search(r"list\(set\(", src), (
            "text_tools.py 又出现 list(set(...)) ⇒ 模型可见的 matches 重新变成跨进程不稳定")


# ══════════════════════════════════════════════════════════════════
#  落点 22：agent/process_distill/solidify.py —— 产物 tags 先无序再 [:8]
# ══════════════════════════════════════════════════════════════════

_SOLIDIFY_SNIPPET = '''
import json, os, sys, tempfile
from agent.process_distill.models import DistilledProcess, DistilledStep
from agent.process_distill.solidify import solidify_to_workflow, solidify_to_skill

TAGS = ["git", "release", "hotfix", "review", "ci", "docs", "ops"]

def mkproc():
    return DistilledProcess(
        name="发布流程蒸馏", description="一次发布流程的蒸馏产物",
        task_signature="release | hotfix", trigger_patterns=["发布", "hotfix"],
        steps=[DistilledStep(seq=1, action="执行构建", tool="bash", params={"cmd": "make"}, source="s1"),
               DistilledStep(seq=2, action="执行发布", tool="bash", params={"cmd": "make release"}, source="s1")],
        sources=["wiki/release.md"], method="llm", tags=list(TAGS))

from agent.workflow_learning.generator import WorkflowGenerator
from agent.workflow_learning.matcher import WorkflowMatcher
from agent.workflow_learning.repository import WorkflowRepository
tmp = tempfile.mkdtemp(prefix="det4_wf_")
repo = WorkflowRepository(path=os.path.join(tmp, "wf.json"))
captured = {}
class _Gen:
    def generate_and_store(self, wf):
        captured["wf"] = wf
        return wf
wf_svc = type("WF", (), {"get": lambda self, wid: None, "generator": _Gen()})()
solidify_to_workflow(mkproc(), wf_svc=wf_svc, available_tools=["bash"])

class _FS:
    def create(self, *a, **k): return None
class _Skills:
    def __init__(self): self.created = None
    def get(self, sid): return None
    def create_manual(self, data):
        self.created = dict(data); return dict(data)
    file_store = _FS()
svc = _Skills()
solidify_to_skill(mkproc(), skills_svc=svc, run_review=False)
print(json.dumps({"wf_tags": list(captured["wf"].tags),
                  "skill_tags": list(svc.created["tags"])}, ensure_ascii=False, sort_keys=True))
'''


class TestSolidifyTagsAreDeterministic:
    """蒸馏产物（workflow + skill）的 tags：先无序再 [:8] ⇒ **成员**可变。"""

    def test_tags_are_identical_across_hash_seeds(self):
        outs = _run_across_seeds(_SOLIDIFY_SNIPPET)
        _assert_one_kind(outs, "solidify 的 wf_tags / skill_tags")

    def test_truncation_is_on_declaration_order_not_hash_order(self):
        """截断点必须落在「proc.tags 声明序 → 固定标签」上。"""
        from agent.process_distill.models import DistilledProcess, DistilledStep
        from agent.process_distill.solidify import solidify_to_workflow
        tags = ["git", "release", "hotfix", "review", "ci", "docs", "ops"]
        proc = DistilledProcess(
            name="n", description="d", task_signature="s", trigger_patterns=["t"],
            steps=[DistilledStep(seq=1, action="a", tool="bash", params={}, source="s")],
            sources=["s"], method="llm", tags=list(tags))
        captured = {}
        class _Gen:
            def generate_and_store(self, wf):
                captured["wf"] = wf
        wf_svc = type("WF", (), {"get": lambda self, wid: None, "generator": _Gen()})()
        solidify_to_workflow(proc, wf_svc=wf_svc, available_tools=["bash"])
        expected = list(dict.fromkeys([*tags, "distilled", "from_knowledge", "pd"]))[:8]
        assert list(captured["wf"].tags) == expected, (
            "tags 不是「声明序 + 固定标签」的前 8 个 ⇒ 截断点上的成员仍取自 set 迭代序")

    def test_no_bare_set_truncation_remains_in_solidify(self):
        src = _code_only("agent/process_distill/solidify.py")
        assert "list({*proc.tags" not in src, (
            "solidify.py 又出现 list({...})[:8] ⇒ 产物 tags 的成员重新跨进程可变")


# ══════════════════════════════════════════════════════════════════
#  落点 17：agent/task_planner/enhanced_planner.py —— 回退任务生成顺序
# ══════════════════════════════════════════════════════════════════

_PLANNER_SNIPPET = '''
import json
from agent.task_planner.enhanced_dag import EnhancedDAG, EnhancedTaskNode
from agent.task_planner.enhanced_planner import EnhancedTaskPlanner

def node(tid, deps, status="done"):
    n = EnhancedTaskNode(id=tid, description="任务 " + tid, depends_on=list(deps))
    n.status = status
    n.rollback_action = "undo " + tid
    return n

plan = EnhancedDAG()
plan.plan_id = "P1"
for n in [node("s1", []), node("s2", ["s1"]), node("s3", ["s2"]),
          node("s4", []), node("s5", ["s4"])]:
    plan.add_task(n)
plan.add_task(node("s6", ["s3", "s5"], status="failed"))
plan.add_task(node("s7", ["s2", "s4"], status="failed"))
rb = EnhancedTaskPlanner().create_rollback_plan(plan)
print(json.dumps({"descs": [t.description for t in rb._nodes.values()]}, ensure_ascii=False))
'''


class TestRollbackPlanOrderIsDeterministic:
    """回退任务 id 是 rollback_{i}（位置即身份）⇒ 生成顺序必须确定。"""

    def test_rollback_task_order_is_identical_across_hash_seeds(self):
        outs = _run_across_seeds(_PLANNER_SNIPPET)
        _assert_one_kind(outs, "create_rollback_plan 的任务顺序")

    def test_rollback_order_equals_failed_task_then_path_order(self):
        """次序 = 「失败任务序 → 各自回滚路径序」这条候选汇合序（去重保序）。"""
        from agent.task_planner.enhanced_dag import EnhancedDAG, EnhancedTaskNode
        from agent.task_planner.enhanced_planner import EnhancedTaskPlanner

        def node(tid, deps, status="done"):
            n = EnhancedTaskNode(id=tid, description="任务 " + tid, depends_on=list(deps))
            n.status = status
            return n

        plan = EnhancedDAG()
        plan.plan_id = "P1"
        for n in [node("s1", []), node("s2", ["s1"]), node("s3", ["s2"]),
                  node("s4", []), node("s5", ["s4"])]:
            plan.add_task(n)
        plan.add_task(node("s6", ["s3", "s5"], status="failed"))
        plan.add_task(node("s7", ["s2", "s4"], status="failed"))
        raw = plan.get_rollback_path("s6") + plan.get_rollback_path("s7")
        expected = ["回退: 任务 " + t for t in dict.fromkeys(raw)]
        rb = EnhancedTaskPlanner().create_rollback_plan(plan)
        assert [t.description for t in rb._nodes.values()] == expected

    def test_no_bare_set_dedupe_remains_in_enhanced_planner(self):
        src = _code_only("agent/task_planner/enhanced_planner.py")
        assert "list(set(rollback_path))" not in src, (
            "enhanced_planner.py 又出现 list(set(rollback_path)) ⇒ 回退任务的身份随进程变")


# ══════════════════════════════════════════════════════════════════
#  落点 23/24/25/18/20/21：其余到达用户可见输出的落点
# ══════════════════════════════════════════════════════════════════

class TestRoutesAssetsExportKeysAreDeterministic:
    """/api/assets/export 的键序（响应体 + 落盘文件）。"""

    def test_iteration_uses_an_ordered_constant(self):
        from agent.server_routes.routes_assets import (
            FILE_BASED_CATEGORIES, FILE_BASED_CATEGORY_ORDER)
        assert isinstance(FILE_BASED_CATEGORY_ORDER, tuple), (
            "迭代用的常量必须有序：:173/:265 直接迭代它生成 JSON 的键序")
        assert FILE_BASED_CATEGORY_ORDER == ("habits", "inspires", "hobbies", "interactions")
        assert FILE_BASED_CATEGORIES == set(FILE_BASED_CATEGORY_ORDER), (
            "成员集合必须由有序枚举派生（单一事实来源）")

    def test_export_key_order_matches_declaration(self):
        from agent.server_routes.routes_assets import FILE_BASED_CATEGORY_ORDER
        export_data = {}
        for cat in FILE_BASED_CATEGORY_ORDER:
            export_data[cat] = []
        assert list(export_data) == ["habits", "inspires", "hobbies", "interactions"]

    def test_export_key_order_is_identical_across_hash_seeds(self):
        code = ("import json\n"
                "from agent.server_routes.routes_assets import FILE_BASED_CATEGORY_ORDER\n"
                "d = {}\n"
                "for c in FILE_BASED_CATEGORY_ORDER:\n    d[c] = []\n"
                "print(json.dumps(list(d)))\n")
        _assert_one_kind(_run_across_seeds(code), "/api/assets/export 的键序")


class TestExecutorHealthWhitelistIsDeterministic:
    """GET /api/skills-mgmt/health 的 env_whitelist 列表序。"""

    def test_env_whitelist_is_sorted(self):
        from agent.skills_mgmt.executor import SkillExecutor, _ENV_WHITELIST
        ex = object.__new__(SkillExecutor)
        ex.python_exe = sys.executable
        ex.default_timeout = 30
        assert ex.health()["env_whitelist"] == sorted(_ENV_WHITELIST)

    def test_env_whitelist_is_identical_across_hash_seeds(self):
        code = ("import json, sys\n"
                "from agent.skills_mgmt.executor import SkillExecutor\n"
                "e = object.__new__(SkillExecutor)\n"
                "e.python_exe = sys.executable\n"
                "e.default_timeout = 30\n"
                "print(json.dumps(e.health()['env_whitelist']))\n")
        _assert_one_kind(_run_across_seeds(code), "health().env_whitelist")


DANGER_TEXT = ("rm -rf / 然后 format C: /q ，再执行 dd if=/dev/zero of=/dev/sda，"
               "接着 shutdown -h now，chmod 777 /etc，reg delete HKLM\\Software")


class TestSafetyAlertCategoriesAreDeterministic:
    """告警 categories → 回调 → /api/safety/alerts → 前端列表。"""

    def test_categories_follow_match_discovery_order(self):
        from agent.safety_guard import SafetyGuard
        g = SafetyGuard()
        result = g.check(DANGER_TEXT)
        alert = g.get_alerts(limit=1)[-1]
        assert alert["categories"] == list(
            dict.fromkeys(m["category"] for m in result["matches"]))

    def test_categories_are_identical_across_hash_seeds(self):
        code = ("import json\n"
                "from agent.safety_guard import SafetyGuard\n"
                "T = %r\n"
                "g = SafetyGuard()\n"
                "g.check(T)\n"
                "print(json.dumps(g.get_alerts(limit=1)[-1]['categories'], ensure_ascii=False))\n"
                % DANGER_TEXT)
        _assert_one_kind(_run_across_seeds(code), "告警 categories")


class TestSkillMergeTagsAreDeterministic:
    """合并后的 tags 落 data/skills_mgmt.json 并上屏。"""

    def test_merged_tags_follow_kept_then_merged_declaration_order(self, tmp_path):
        from agent.skills_mgmt.models import Skill
        from agent.skills_mgmt.store import SkillStore
        store = SkillStore(path=str(tmp_path / "skills.json"))
        store.upsert(Skill(id="dst", name="DST", tags=["t1", "t2", "t3", "t4"]))
        store.upsert(Skill(id="src", name="SRC", tags=["t5", "t6", "t7", "t8"]))
        store.merge_skills("src", "dst", strategy="keep_dst")
        assert list(store.get("dst").tags) == ["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"]

    def test_merged_tags_are_identical_across_hash_seeds(self, tmp_path):
        code = ("import json, sys\n"
                "from agent.skills_mgmt.models import Skill\n"
                "from agent.skills_mgmt.store import SkillStore\n"
                "s = SkillStore(path=sys.argv[0] if False else %r)\n"
                "s.upsert(Skill(id='dst', name='D', tags=['t1','t2','t3','t4']))\n"
                "s.upsert(Skill(id='src', name='S', tags=['t5','t6','t7','t8']))\n"
                "s.merge_skills('src','dst',strategy='keep_dst')\n"
                "print(json.dumps(list(s.get('dst').tags)))\n"
                % str(tmp_path / "seeds.json"))
        _assert_one_kind(_run_across_seeds(code), "合并后的 tags")


class TestMemoryAbstractorDraftIsDeterministic:
    """生成技能草稿的 tags / default_params 键序 / 关键词截断（DET-3 漏掉的 3 处）。"""

    def test_ordered_tokens_has_same_members_as_tokenize(self):
        from agent.skills_mgmt.memory_abstractor import _tokenize, _ordered_tokens
        text = "python asyncio 并发 教程 网络 抓取 解析 存储 报告 生成 校验"
        assert set(_ordered_tokens(text)) == _tokenize(text)
        assert _ordered_tokens(text)[:3] == ["python", "asyncio", "并"]

    def test_common_params_keys_follow_first_entry_declaration_order(self):
        from agent.skills_mgmt.memory_abstractor import MemoryEntry, MemorySkillAbstractor
        entries = [MemoryEntry(source="s", source_id="e%d" % i, task_text="t", params={
            "zeta": 1, "alpha": 1, "mu": 2, "beta": 1, "kappa": 3, "omega": 1})
            for i in range(3)]
        got = list(MemorySkillAbstractor._extract_common_params(entries))
        assert got == ["zeta", "alpha", "mu", "beta", "kappa", "omega"], (
            "default_params 的键序不是 entries[0].params 的声明序 ⇒ 仍取自 set 迭代序")

    def test_draft_tags_follow_declaration_order(self):
        from agent.skills_mgmt.memory_abstractor import MemoryCluster, MemorySkillAbstractor
        cluster = MemoryCluster(cluster_id="c1", success_rate=0.9, representative_text="t",
                                common_tags=["tagA", "tagB", "tagC", "tagD", "tagE", "tagF"])
        draft = MemorySkillAbstractor().generate_skill_draft(cluster)
        assert draft["tags"] == ["tagA", "tagB", "tagC", "tagD", "tagE", "tagF",
                                 "memory-abstracted"]

    def test_keyword_truncation_is_identical_across_hash_seeds(self):
        code = ("import json\n"
                "from agent.skills_mgmt.memory_abstractor import MemorySkillAbstractor as A, MemoryEntry\n"
                "T = 'python asyncio 并发 教程 网络 抓取 解析 存储 报告 生成 校验'\n"
                "ok = [MemoryEntry(source='s', source_id='e1', task_text=T, success=True,\n"
                "                  params={'alpha': 1, 'beta': 2}, tool_calls=[{'name': 'bash'}])]\n"
                "bad = [MemoryEntry(source='s', source_id='e2', task_text=T, success=False,\n"
                "                   params={'gamma': 3}, tool_calls=[{'name': 'curl'}])]\n"
                "print(json.dumps({'rc': A._extract_root_cause(ok, ['bash'], {'alpha': 1}, 1.0, T),\n"
                "                  'tc': A._extract_trigger_conditions(['tag1'], {'alpha': 1}, T),\n"
                "                  'ap': A._extract_anti_patterns(bad, ok, T)}, ensure_ascii=False))\n")
        _assert_one_kind(_run_across_seeds(code), "技能草稿的关键词截断")


def test_no_bare_set_to_ordered_product_in_fixed_files():
    """统一反向守卫：本卡修过的文件里不得再出现这些形态。"""
    forbidden = {
        "agent/text_tools.py": ["list(set("],  # 注释行已被 _code_only 丢掉
        "agent/process_distill/solidify.py": ["list({*proc.tags"],
        "agent/task_planner/enhanced_planner.py": ["list(set(rollback_path))"],
        "agent/safety_guard.py": ['list(set(m["category"]'],
        "agent/skills_mgmt/store.py": ["list(set(actual_dst.tags)"],
        "agent/skills_mgmt/memory_abstractor.py": ['list(set(cluster.common_tags'],
        "agent/server_routes/routes_assets.py": ["for cat in FILE_BASED_CATEGORIES:"],
    }
    bad = []
    for rel, pats in forbidden.items():
        src = _code_only(rel)
        for pat in pats:
            if pat in src:
                bad.append("%s 里又出现 %r" % (rel, pat))
    assert not bad, "确定性修复被抄回去了：" + "; ".join(bad)
