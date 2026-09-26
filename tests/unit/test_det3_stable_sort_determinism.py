# -*- coding: utf-8 -*-
"""DET-3 · 「set 交给稳定排序」这一族的**收口回归**（few-shot 浮点求和序 + 主线装配的平面序）

本文件守护 DET-3 修的两处（族清单见 docs/audit_skill_governance/DET3.md §4）：

  落点 A  agent/skills_mgmt/few_shot_injector.py::_cosine_tfidf
          for term in set(q_count) | set(d_count)：set 迭代序 → **浮点加法次序**
          → 余弦分末位比特随进程变 → 该分又只参与两处判定
          （score >= min_score 与 scored.sort 的稳定排序）⇒ 只要某个分与阈值
          或与另一个分相差在 1 ulp 内，**被选中的示例**（进而拼进 prompt 的文本）
          就随进程变。**DET-3 修**：次序固定为「查询词序 → 文档词序」。

  落点 B  agent/lines/assembler.py::assemble 的 active_planes（set）+
          sorted(key=-plane_weights)：权重**并列**时 by_plane 键序 / reasons 插入序
          / to_dict() 载荷字节随进程变（真实档案 3/7 有并列）。
          **DET-3 修**：主键仍是权重降序，只补"平面声明序 → 名字"次级键。
          res.tools（喂给模型的工具表）本来就由 out_key 全序决定 ⇒ 本卡不动它。

判据（三条，全部**确定性**，不靠"这次恰好红了"）
------------------------------------------------
  A. 求和次序不含任何"迭代序"成分：换一个迭代序完全不同的集合，结果逐位相同。
  B. 真跨进程：PYTHONHASHSEED 各起新解释器，分/阈值判定/被选示例/prompt 指纹逐位相同。
  C. 装配器：并列权重的平面先后 = PLANES 声明序；7 条真实档案的载荷跨进程逐位相同，
     且**喂给模型的工具表**（res.tools）逐位不变。

【非空转自证】把两处修复去掉（for term in set(...) ｜ 两个 sorted(key=-weight) 退回），
本文件 A/B/C 三组**变红**；还原后全绿（sha256 逐字节自证见 DET3.md §3）。
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

#: 跨进程种子：0/1/2 三种确定种子 + 1 个随机种子（与 E1-D / DET-2 同款）
_SEEDS = ("0", "1", "2", "random")

#: 阈值刀刃常量：**跨进程固定**（生产形态同构 —— min_score 是调用方常量，分随进程动）。
#: 取值落在改前实测的分值带 0.7724872793364284~287 内部：改前 0/1 号种子判 True、
#: 2 号种子判 False（两份不同的"被选中集合"）；改后分是**唯一值** ⇒ 判定唯一。
_KNIFE = "0.7724872793364286"

#: 必然触发浮点求和序差异的语料（与 DET-2 §1.5 落点 7 同构造；
#: 第一份文档与查询共享多个词 ⇒ 求和项 > 2，加法次序才开始影响末位）
_CORPUS = [
    ("ex_001", "写测试时要避免哪些反模式 编写或修改测试 单元测试 反模式 测试", 5),
    ("ex_002", "结构化日志 日志 观测 排查 结构化", 5),
    ("ex_003", "竞态防御 前端 状态同步 竞态", 5),
]
_QUERY = "写测试时要避免哪些反模式"
_SKILL = "det3_tie"


class _ShuffledSet(set):
    """**迭代序与哈希无关**的 set：固定按声明的次序迭代。

    真正的 set 迭代序随 PYTHONHASHSEED 变，用它写用例会变成"这次跑恰好红了"的
    概率性判据。子类化后迭代序每次运行都相同 ⇒ "修复被拆掉必红"是**确定性**结论。
    """

    _ORDER: tuple = ()

    def __iter__(self):
        return iter(self._ORDER)


def _write_corpus(dirpath: Path) -> Path:
    """写一份 few-shot 示例库（JSONL；字段与 data/skill_few_shot/<id>.jsonl 对齐）"""
    dirpath.mkdir(parents=True, exist_ok=True)
    lines = []
    for eid, intent, rating in _CORPUS:
        lines.append(json.dumps({
            "example_id": eid, "intent": intent, "input": "输入：" + intent,
            "output": "输出：" + intent, "rating": rating, "tags": [], "created_at": "",
        }, ensure_ascii=False))
    path = dirpath / ("%s.jsonl" % _SKILL)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return dirpath


class TestFewShotSummationOrderIsDeterministic:
    """判据 A/B：few_shot_injector 的浮点求和次序不含迭代序成分"""

    _DRIVER = (
        "import hashlib, json, logging, sys\n"
        "logging.disable(logging.CRITICAL)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from agent.skills_mgmt.few_shot_injector import (FewShotInjector, _tokenize,\n"
        "    _compute_idf, _cosine_tfidf)\n"
        "corpus, skill, query, knife = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])\n"
        "inj = FewShotInjector(few_shot_dir=corpus)\n"
        "exs = inj.load_examples(skill)\n"
        "docs = [_tokenize(e.intent) for e in exs]\n"
        "idf = _compute_idf(docs)\n"
        "qt = _tokenize(query)\n"
        "scores = [_cosine_tfidf(qt, d, idf) for d in docs]\n"
        "pairs = sorted(((-s, i) for i, s in enumerate(scores)))\n"
        "sel = [e.example_id for e in inj.select_examples(skill, query, top_k=2,\n"
        "                                                 min_score=knife)]\n"
        "ctx = inj.inject(skill, query, max_tokens=500)\n"
        "print('DET3 ' + json.dumps({\n"
        "    'n': len(exs),\n"
        "    'scores': [repr(s) for s in scores],\n"
        "    'passed': [bool(s >= knife) for s in scores],\n"
        "    'order': [i for _, i in pairs],\n"
        "    'sel': sel,\n"
        "    'has_examples': ctx['has_examples'],\n"
        "    'prompt_sha16': hashlib.sha256(ctx['prompt'].encode('utf-8')).hexdigest()[:16],\n"
        "}, ensure_ascii=False))\n"
    )

    @classmethod
    def _run(cls, seed, corpus):
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = seed
        env["PYTHONUTF8"] = "1"
        env["AGENT_HYBRID_EMBEDDING"] = "0"
        proc = subprocess.run(
            [sys.executable, "-c", cls._DRIVER, str(ROOT), str(corpus),
             _SKILL, _QUERY, _KNIFE],
            env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=240)
        for ln in (proc.stdout or "").splitlines():
            if ln.startswith("DET3 "):
                return json.loads(ln[5:])
        raise AssertionError("子进程未产出分数：rc=%s stderr=%s"
                             % (proc.returncode, (proc.stderr or "")[-500:]))

    def _payloads(self, tmp_path):
        corpus = _write_corpus(tmp_path / "few_shot")
        return {seed: self._run(seed, corpus) for seed in _SEEDS}

    def test_cosine_score_is_bit_identical_across_hash_seeds(self, tmp_path):
        """同一个示例的余弦分必须**逐位**相同（改前差最后 1~2 个比特）"""
        got = self._payloads(tmp_path)
        kinds = {}
        for seed, payload in got.items():
            kinds.setdefault(json.dumps(payload["scores"]), []).append(seed)
        assert len(kinds) == 1, (
            "同一份语料 / 同一份代码，余弦分跨进程**末位不同** ⇒ 求和次序仍取自 "
            "set 迭代序（生产上就是 PYTHONHASHSEED）：\n"
            + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in kinds.items()))
        # 非空转守卫：语料必须真的落在"多求和项"的形态上，否则本用例会退化成空转
        top = max(float(s) for s in json.loads(next(iter(kinds))))
        assert repr(top).startswith("0.77248727933642"), (
            "语料没有复现出 DET-2/3 报的那族分值（实测 top=%.17g）⇒ 本用例无法判别" % top)

    def test_threshold_decision_is_identical_across_hash_seeds(self, tmp_path):
        """阈值判定（score >= min_score）与随后的被选示例必须跨进程一致"""
        got = self._payloads(tmp_path)
        for key in ("passed", "order", "sel"):
            kinds = {}
            for seed, payload in got.items():
                kinds.setdefault(json.dumps(payload[key], ensure_ascii=False), []).append(seed)
            assert len(kinds) == 1, (
                "同一份语料 / 同一个阈值常量，%s 跨进程不同 ⇒ 分与阈值相差在 1 ulp 内时，"
                "**被选中的示例**随进程翻转（这段文本是要进 LLM 上下文的）：\n"
                % key
                + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in kinds.items()))
        # 非空转守卫：语料里必须恰好有一个示例的分显著高于 0.3（否则 inject 全程空转）
        first = got[_SEEDS[0]]
        assert sum(1 for s in first["scores"] if float(s) >= 0.3) == 1, (
            "语料形态不对（没有一个'相关'示例）⇒ 本用例会退化成空转：%s" % first["scores"])

    def test_injected_prompt_is_identical_across_hash_seeds(self, tmp_path):
        """**生产入口** FewShotInjector.inject：注入的 prompt 指纹跨进程逐位相同

        （这条同时是 F3-1「同输入 ⇒ 同前缀」的关注点在技能 few-shot 段上的守卫；
        它在改前也可能恰好为绿 —— 是否变绿取决于语料数值是否踩上刀刃，
        故它只作守卫，判别力由上面两条与变异自证给出。）
        """
        got = self._payloads(tmp_path)
        kinds = {}
        for seed, payload in got.items():
            kinds.setdefault("%s|%s" % (payload["has_examples"], payload["prompt_sha16"]),
                             []).append(seed)
        assert len(kinds) == 1, (
            "注入到 prompt 的 few-shot 段跨进程不同：\n"
            + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in kinds.items()))
        assert got[_SEEDS[0]]["has_examples"] is True, "语料没触发注入 ⇒ 本用例空转"

    def test_summation_order_is_not_taken_from_a_bare_set(self):
        """结构性绊线：_cosine_tfidf 的累加循环**不得**直接迭代裸 set 并集

        （行为判别在判据 A/B；本条的用途是"改回去就报错"的显式绊线，
        避免将来有人把次序重新交还给 set 迭代序。）
        """
        import inspect
        from agent.skills_mgmt import few_shot_injector as F

        src = inspect.getsource(F._cosine_tfidf)
        assert "for term in set(" not in src, (
            "累加循环又直接迭代 set 了 ⇒ 浮点求和次序重新交给 set 迭代序：\n%s" % src)
        assert "dict.fromkeys(list(q_count) + list(d_count))" in src, (
            "累加循环的确定次序不见了：\n%s" % src)


class TestAssemblyPlaneOrderIsDeterministic:
    """判据 A/C：装配器的平面迭代序含确定次级键"""

    def test_plane_order_ignores_input_set_iteration_order(self):
        """同一成员集合、两种迭代序 ⇒ _plane_order 结果逐位相同"""
        from agent.lines.assembler import _plane_order

        members = ("resident", "perceive", "act")
        weights = {"resident": 0.6, "perceive": 1.0, "act": 1.0}

        class _Fwd(_ShuffledSet):
            _ORDER = members

        class _Rev(_ShuffledSet):
            _ORDER = tuple(reversed(members))

        a = _plane_order(_Fwd(members), weights)
        b = _plane_order(_Rev(members), weights)
        assert a == b, (
            "平面迭代序仍取自 set 迭代序（生产上就是 PYTHONHASHSEED）：fwd=%s rev=%s" % (a, b))

    def test_tied_weights_follow_plane_declaration_order(self):
        """权重并列 ⇒ 次级键取 models.PLANES 的声明序（本模块 pools 本来就按它建）"""
        from agent.lines.assembler import _plane_order
        from agent.lines.models import PLANES

        got = _plane_order(["act", "perceive", "resident"],
                           {"resident": 0.6, "perceive": 1.0, "act": 1.0})
        assert got == ["perceive", "act", "resident"], (
            "并列权重的平面先后不是声明序：got=%s（PLANES=%s）" % (got, list(PLANES)))
        all_tied = _plane_order(list(reversed(PLANES)), {p: 1.0 for p in PLANES})
        assert all_tied == [p for p in PLANES], (
            "全并列时应退化为声明序：got=%s" % all_tied)

    def test_primary_key_is_still_plane_weight_desc(self):
        """**反向守卫**：主键仍是"平面权重降序" —— 次级键不得覆盖它"""
        from agent.lines.assembler import _plane_order

        got = _plane_order(["act", "perceive", "resident"],
                           {"resident": 0.9, "perceive": 0.1, "act": 0.5})
        assert got == ["resident", "act", "perceive"], (
            "主键（权重降序）被次级键盖掉了：got=%s" % got)

    _DRIVER = (
        "import hashlib, json, logging, sys\n"
        "logging.disable(logging.CRITICAL)\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "from agent.lines.assembler import assemble\n"
        "from agent.lines.models import load_tool_meta\n"
        "from agent.lines.registry import get_line_registry\n"
        "reg, meta = get_line_registry(), load_tool_meta()\n"
        "avail = list(meta.keys())\n"
        "out = {}\n"
        "for lid in ('assistant', 'dev', 'digital_life', 'engineering', 'harness',\n"
        "            'knowledge', 'recon'):\n"
        "    res = assemble(reg.load(lid), avail)\n"
        "    d = res.to_dict()\n"
        "    out[lid] = {\n"
        "        'by_plane_keys': list(res.by_plane.keys()),\n"
        "        'reasons_keys': list(res.reasons.keys()),\n"
        "        'tools_sha16': hashlib.sha256(json.dumps(list(res.tools),\n"
        "            ensure_ascii=False).encode()).hexdigest()[:16],\n"
        "        'payload_sha16': hashlib.sha256(json.dumps(d, ensure_ascii=False,\n"
        "            sort_keys=False).encode()).hexdigest()[:16],\n"
        "        'by_plane_union_ok': sorted(n for v in res.by_plane.values() for n in v)\n"
        "            == sorted(res.tools),\n"
        "        'w': reg.load(lid).plane_weights,\n"
        "    }\n"
        "print('DET3 ' + json.dumps(out, ensure_ascii=False))\n"
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
            if ln.startswith("DET3 "):
                return json.loads(ln[5:])
        raise AssertionError("子进程未产出装配载荷：rc=%s stderr=%s"
                             % (proc.returncode, (proc.stderr or "")[-500:]))

    def test_real_profiles_payload_is_identical_across_hash_seeds(self):
        """7 条真实档案：by_plane 键序 / reasons 键序 / 载荷字节跨进程逐位相同"""
        got = {}
        for seed in _SEEDS:
            got[seed] = self._run(seed)
        first = got[_SEEDS[0]]
        # 非空转守卫：必须存在"权重并列"的真实档案，否则本用例无从判别
        tied = [lid for lid, v in first.items()
                if len({w for w in v["w"].values() if w > 0})
                != len([w for w in v["w"].values() if w > 0])]
        assert tied, "真实档案里没有权重并列的平面 ⇒ 本用例退化成空转：%s" % first

        for key in ("by_plane_keys", "reasons_keys", "payload_sha16"):
            kinds = {}
            for seed, payload in got.items():
                sig = {lid: payload[lid][key] for lid in payload}
                kinds.setdefault(json.dumps(sig, ensure_ascii=False, sort_keys=True),
                                 []).append(seed)
            assert len(kinds) == 1, (
                "真实档案的 %s 跨进程不同（并列权重下平面先后取自 set 迭代序）：\n" % key
                + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in kinds.items()))

    def test_model_facing_tools_list_is_untouched_by_the_ordering_key(self):
        """**判据 C 的核心**：喂给模型的工具表与平面分组成员跨进程逐位不变

        （DET-3 只定"平面迭代序"，不得动 res.tools 的成员与先后 —— 它由 out_key
        全序决定，本卡一个字都没改。）
        """
        got = {seed: self._run(seed) for seed in _SEEDS}
        kinds = {}
        for seed, payload in got.items():
            sig = {lid: payload[lid]["tools_sha16"] for lid in payload}
            kinds.setdefault(json.dumps(sig, sort_keys=True), []).append(seed)
        assert len(kinds) == 1, (
            "res.tools 跨进程不同 ⇒ 本卡动了喂给模型的工具表（不该动）：\n"
            + "\n".join("  seeds=%s -> %s" % (v, k) for k, v in kinds.items()))
        for lid, payload in got[_SEEDS[0]].items():
            assert payload["by_plane_union_ok"], (
                "%s 的 by_plane 分组与 tools 成员不一致 ⇒ 分组已与工具表脱钩" % lid)
