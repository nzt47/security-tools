# -*- coding: utf-8 -*-
"""DET-2 · 「set 交给稳定排序」这一族的**收敛回归**（工具路由 helper + 技能检索扫描）

缺陷族（本卡 §1 实测）
----------------------
把 set 交给**稳定排序** ⇒ 分数/优先级**并列**项的先后 = set 迭代序
= 字符串哈希随机化 ⇒ 同一个查询在**不同进程**里可能得到不同的结果；
截断点落在并列块内部时，连**成员**都会变（不只是顺序）。

本族的三个落点（第三处由本文件的前身E1-D补上）：
  1. agent/tool_router_hybrid.py 融合入口 + 下发阶 —— E1-D 已修（本文件不重复守护）；
  2. agent/tool_router.get_tools_for_input -> _apply_alias_merge_and_priority_sort
     —— **DET-2 修，且修在 helper 内部一处**（覆盖全部调用方）；
  3. agent/skills_mgmt/loader._tfidf_scan 的候选集 —— **DET-2 修**。

本文件把两处修复钉死（三条判据）
--------------------------------
  A. **helper 内部一处收敛**：无论调用方递进来的是 set 还是有序序列，
     并列项的先后都不得含任何"迭代序"成分；且**有序序列的次序必须被原样保留**
     （那是 hybrid 的相关度序，helper 无权改 —— E1-D 的修复不能被本卡的收敛改回去）。
  B. **真跨进程**：PYTHONHASHSEED 各起新解释器，生产入口的完整**有序**结果逐位相同。
  C. **技能检索的扫描序 = 索引序**（= 全量遍历路径本来就用的文档序），
     且判据在**真实索引 + 必然并列的查询**上成立。

【非空转自证】把 helper 的规范化去掉（_ordered_candidates 退回 list(selected)），
并把 _tfidf_scan 的 scan_items 退回直接迭代 set：本文件 A/B/C 三组**全部变红**，
还原后全绿（sha256 逐字节自证见 docs/audit_skill_governance/DET2.md §3）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.timeout(300)

#: 判据 B 的种子序列：0/1/2 三种确定种子 + 2 个随机种子（与 E1-D 同款）
_SEEDS = ("0", "1", "2", "random", "random")

#: 并列查询：命中真实索引里全部含"并列探针"字样的技能（分数**严格相等**）
_TIE_QUERY = "写测试时要避免哪些反模式"


class _ShuffledSet(set):
    """**迭代序与哈希无关**的 set：固定按"声明的倒序"迭代。

    为什么需要它：真正的 set 迭代序随 PYTHONHASHSEED 变，用它写测试会变成
    "这次跑恰好红了"的概率性判据。子类化后迭代序**每次运行都相同**，
    于是"修复被拆掉 ⇒ 判据必红"成为**确定性**结论，而不是碰运气。
    """

    _ORDER: tuple = ()

    def __iter__(self):
        return iter(self._ORDER)


class TestHelperIsTheSingleConvergencePoint:
    """判据 A：determinism 收敛在 _apply_alias_merge_and_priority_sort 内部一处"""

    @staticmethod
    def _code_tools():
        from agent.tool_router import TOOL_CATEGORIES
        return list(TOOL_CATEGORIES["code"]["tools"])

    def test_set_input_equals_sequence_input(self):
        """同一个成员集合，以 set 递进去与以**声明序序列**递进去，结果必须逐位相同"""
        from agent.tool_router import _apply_alias_merge_and_priority_sort

        decl = self._code_tools()

        class _Rev(_ShuffledSet):
            _ORDER = tuple(reversed(decl))

        got = _apply_alias_merge_and_priority_sort(_Rev(decl), {"code"}, 6)
        want = _apply_alias_merge_and_priority_sort(decl, ["code"], 6)
        assert got == want, (
            "set 迭代序泄漏进了并列块的先后 —— 同优先级并列项的顺序不该由"
            "输入是 set 还是 list 决定。set 侧=%s / 序列侧=%s" % (got, want))

    def test_set_input_is_stable_under_any_iteration_order(self):
        """换一个迭代序完全不同的 set，结果仍必须逐位相同（不含任何迭代序成分）"""
        from agent.tool_router import _apply_alias_merge_and_priority_sort

        decl = self._code_tools()

        class _Fwd(_ShuffledSet):
            _ORDER = tuple(decl)

        class _Rev(_ShuffledSet):
            _ORDER = tuple(reversed(decl))

        a = _apply_alias_merge_and_priority_sort(_Fwd(decl), {"code"}, 6)
        b = _apply_alias_merge_and_priority_sort(_Rev(decl), {"code"}, 6)
        assert a == b, (
            "同一成员集合、两种迭代序得到了**不同结果** ⇒ 并列项先后仍取自迭代序"
            "（生产上就是 PYTHONHASHSEED）\n  正序=%s\n  逆序=%s" % (a, b))

    def test_categories_set_iteration_cannot_reorder_floors(self):
        """类别集合同理：同优先级类别的先后不得取自迭代序（它们决定 floors 的先后）"""
        from agent.tool_router import TOOL_CATEGORIES, _apply_alias_merge_and_priority_sort

        # 取两个**同优先级**类别，外加 core（优先级最低，一定排在前面）
        groups = [(c, info.get("priority", 99), list(info.get("tools", [])))
                  for c, info in TOOL_CATEGORIES.items() if info.get("tools")]
        by_pri = {}
        for c, pri, tools in groups:
            by_pri.setdefault(pri, []).append((c, tools))
        pair = next((v for v in by_pri.values() if len(v) >= 2), None)
        if pair is None:
            pytest.skip("TOOL_CATEGORIES 里没有同优先级类别，本判据无从构造并列")
        cats = [c for c, _ in pair[:2]]
        selected = [t for _, tools in pair[:2] for t in tools]

        class _Rev(_ShuffledSet):
            _ORDER = tuple(reversed(cats))

        got = _apply_alias_merge_and_priority_sort(set(selected), _Rev(cats), 25)
        want = _apply_alias_merge_and_priority_sort(set(selected), cats, 25)
        assert got == want, (
            "同优先级类别（%s，priority=%d）的先后随**迭代序**变了 ⇒ floors（类别保底）"
            "的先后随之变，截断点上的成员也会变。\n  shuffled=%s\n  decl=%s"
            % (cats, pair[0][0] and TOOL_CATEGORIES[cats[0]].get("priority"), got, want))

    def test_sequence_input_order_is_preserved(self):
        """**反向守卫**：有序序列输入的次序必须被原样保留（hybrid 的相关度序不可被改）

        Why：本卡把确定化收敛进 helper，最危险的副作用就是"helper 自作主张重排"，
        那会把 E1-D 的修复（hybrid 传相关度序）改回去。这条用例把它钉死：
        传逆序序列 ⇒ 并列块就按逆序（而不是被重新排成声明序/字典序）。
        """
        from agent.tool_router import _apply_alias_merge_and_priority_sort

        decl = self._code_tools()
        rev = list(reversed(decl))
        res_rev = _apply_alias_merge_and_priority_sort(rev, ["code"], 25)
        res_decl = _apply_alias_merge_and_priority_sort(decl, ["code"], 25)

        assert res_rev != res_decl, (
            "逆序输入与声明序输入得到了同一结果 ⇒ helper 覆盖了调用方给的次序"
            "（E1-D 的相关度序会因此失效）")
        # 保底块（floors）由类别内声明序决定、与输入序无关 ⇒ 它是两个结果的**公共前缀**
        n = 0
        for a, b in zip(res_rev, res_decl):
            if a != b:
                break
            n += 1
        assert n > 0, "保底块为空 ⇒ 本用例没测到保底与相关度序的分离"
        assert res_rev[:n] == res_decl[:n]
        tail_rev, tail_decl = res_rev[n:], res_decl[n:]
        assert tail_rev == list(reversed(tail_decl)), (
            "并列块的先后没有跟随调用方给的序列次序：\n  rev=%s\n  decl=%s"
            % (tail_rev, tail_decl))


class TestKeywordRouteIsCrossProcessDeterministic:
    """判据 B：关键词路由（get_tools_for_input）跨进程逐位相同"""

    _DRIVER = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from agent.observability.tool_trace import ToolTraceRecorder\n"
        "ToolTraceRecorder._instance = ToolTraceRecorder(':memory:')\n"
        "from agent.tool_router import TOOL_CATEGORIES, get_tools_for_input\n"
        "wl = list(TOOL_CATEGORIES['code']['tools'])\n"
        "out = {}\n"
        "for mt in (6, 25):\n"
        "    out['mt%d' % mt] = get_tools_for_input('帮我写代码并运行测试',\n"
        "                                          enabled_whitelist=wl, max_tools=mt)\n"
        "out['nowl'] = get_tools_for_input('帮我写代码并运行测试', max_tools=25)\n"
        "print('DET2 ' + json.dumps(out, ensure_ascii=False))\n"
    )

    @classmethod
    def _run(cls, seed):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONUTF8"] = "1"
        env["AGENT_HYBRID_EMBEDDING"] = "0"
        proc = subprocess.run([sys.executable, "-c", cls._DRIVER, str(ROOT)],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
        for ln in (proc.stdout or "").splitlines():
            if ln.startswith("DET2 "):
                return json.loads(ln[5:])
        raise AssertionError("子进程未产出发下集：rc=%s stderr=%s"
                             % (proc.returncode, (proc.stderr or "")[-500:]))

    def test_payload_is_identical_across_hash_seeds(self):
        seen = {}
        for seed in _SEEDS:
            seen.setdefault(json.dumps(self._run(seed), ensure_ascii=False, sort_keys=True),
                            []).append(seed)
        assert len(seen) == 1, (
            "同一查询 / 同一份代码，关键词路由跨进程下发了**不同的工具集**：\n"
            + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in seen.items()))

    def test_tie_block_follows_declaration_order(self):
        """并列块（code 类全部同 priority=3）必须是 TOOL_CATEGORIES 的声明序"""
        from agent.tool_router import TOOL_CATEGORIES

        got = self._run("0")["mt25"]
        decl = [t for t in TOOL_CATEGORIES["code"]["tools"] if t in set(got)]
        assert got == decl, (
            "同优先级并列块不是声明序 ⇒ 仍是（或又被）迭代序决定：\n  got =%s\n  decl=%s"
            % (got, decl))
        assert len(got) == len(decl) == 15, "本判据依赖 code 类 15 个工具，实测 %d" % len(got)


class TestSkillScanOrderIsDeterministic:
    """判据 C：技能检索 _tfidf_scan 的候选汇合序 = 索引序"""

    @staticmethod
    def _synthetic_index(n=30):
        """n 个**元数据逐字相同**的技能：命中率严格相等 ⇒ 必然并列。

        键序故意取"名字的逆序"，这样"索引序"与"名字字典序"**不同**，
        可以区分"按索引序"与"按字典序"两种实现。
        """
        ids = ["skillsynth%02d" % i for i in range(n, 0, -1)]
        return {sid: {"id": sid, "name": sid,
                      "description": "并列探针 tieprobezeta",
                      "tags": [], "category": "test", "enabled": True}
                for sid in ids}

    def _scan(self, index, use_inverted_index, candidate_limit=0):
        from agent.skills_mgmt.loader import SkillLoader, _tokenize
        ld = SkillLoader()
        ms = ld._tfidf_scan(index=index, query_tokens=_tokenize("并列探针 tieprobezeta"),
                            enabled_only=True, min_score=0.01,
                            use_inverted_index=use_inverted_index,
                            candidate_limit=candidate_limit)
        return [m.skill_id for m in ms], {round(m.score, 12) for m in ms}

    def test_inverted_scan_order_equals_index_order(self):
        index = self._synthetic_index()
        got, scores = self._scan(index, use_inverted_index=True)
        assert len(scores) == 1, "候选分不相等 ⇒ 本用例没测到并列：%s" % scores
        assert got == list(index.keys()), (
            "倒排路径的候选汇合序不是索引序（很可能又退回 set 迭代序）：\n  got =%s\n"
            "  idx =%s" % (got[:6], list(index.keys())[:6]))

    def test_inverted_and_fullscan_orders_agree(self):
        """两条路径的并列先后必须**逐位一致**（这是取"索引序"当次级键的理由）"""
        index = self._synthetic_index()
        inv, _ = self._scan(index, use_inverted_index=True)
        full, _ = self._scan(index, use_inverted_index=False)
        assert inv == full, (
            "倒排路径与全量遍历路径的候选序不一致 ⇒ 同一查询会因 use_inverted_index "
            "开关不同而给出不同的并列先后：\n  inverted=%s\n  fullscan=%s"
            % (inv[:6], full[:6]))

    def test_candidate_limit_ties_are_broken_by_index_order(self):
        """candidate_limit 截断：命中数并列时取**索引序**前 N 个（改前是哈希序）"""
        index = self._synthetic_index()
        got, _ = self._scan(index, use_inverted_index=True, candidate_limit=10)
        assert got == list(index.keys())[:10], (
            "并列候选中被截断保留的不是索引序前 N 个 ⇒ 截断成员随进程变：\n  got=%s"
            % got)

    _DRIVER = (
        "import json, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from agent.skills_mgmt.loader import SkillLoader, _tokenize\n"
        "ld = SkillLoader()\n"
        "idx = ld.fs.load_metadata_index()\n"
        "ms = ld._tfidf_scan(index=idx, query_tokens=_tokenize(sys.argv[2]),\n"
        "                    enabled_only=True, min_score=0.01, use_inverted_index=True)\n"
        "from collections import Counter\n"
        "sc = Counter(round(m.score, 12) for m in ms)\n"
        "top = sorted(ms, key=lambda m: m.score, reverse=True)[:3]\n"
        "print('DET2 ' + json.dumps({'scan': [m.skill_id for m in ms],\n"
        "                            'tied': max(sc.values()) if sc else 0,\n"
        "                            'top3': [m.skill_id for m in top]}, ensure_ascii=False))\n"
    )

    @classmethod
    def _run(cls, seed):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONUTF8"] = "1"
        proc = subprocess.run([sys.executable, "-c", cls._DRIVER, str(ROOT), _TIE_QUERY],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=240)
        for ln in (proc.stdout or "").splitlines():
            if ln.startswith("DET2 "):
                return json.loads(ln[5:])
        raise AssertionError("子进程未产出扫描序：rc=%s stderr=%s"
                             % (proc.returncode, (proc.stderr or "")[-500:]))

    def test_real_index_scan_order_is_identical_across_hash_seeds(self):
        seen = {}
        for seed in ("0", "1", "2", "random"):
            seen.setdefault(json.dumps(self._run(seed), ensure_ascii=False, sort_keys=True),
                            []).append(seed)
        assert len(seen) == 1, (
            "同一查询 / 同一索引，技能检索的候选序（进而 top-3）跨进程不同：\n"
            + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in seen.items()))
        payload = json.loads(next(iter(seen)))
        assert payload["tied"] >= 3, (
            "真实查询的并列块太小（%d）⇒ 本判据会退化成空转" % payload["tied"])
