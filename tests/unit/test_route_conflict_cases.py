# -*- coding: utf-8 -*-
"""E1 · 路由冲突用例集自检 + 评测脚本单测

【本文件守什么】
  1. 用例集契约：50 条、id 唯一、字段齐全、expect 与 forbid 不冲突、query 非空、
     note 必须说明「为什么是边界」、引用的工具名必须真实存在、必须覆盖审计 Q2 §3
     的 21 组重叠样本、必须含足量**全中文**边界句。
  2. 评测脚本能跑通，且**当前基线可复现**（低分是现状，不是失败 —— 但基线漂移必须被看见）。
  3. 一条**条数守卫**：用例集被删到 50 条以下时判失败（防止将来被删条缩集）。
  4. 一条**反「拍脑袋 τ」守卫**：当分差对正确性毫无区分度时，标定函数必须报「不可标定」
     而不是吐出一个经验值。

  不启动服务、不调 LLM、零费用：评测只走 get_hybrid_retriever().query() 与
  hybrid_select_tools() 两条检索入口。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "eval_route_conflict.py"
_CASES = _ROOT / "data" / "eval" / "route_conflict_cases.v1.jsonl"      # E1 冻结集（50 条）
_CASES_V2 = _ROOT / "data" / "eval" / "route_conflict_cases.v2.jsonl"   # E1-B 扩充集（121 条）


def _load_eval_module():
    """按路径加载被测脚本（scripts/ 不是包，不能 import）"""
    spec = importlib.util.spec_from_file_location("eval_route_conflict", str(_SCRIPT))
    assert spec is not None and spec.loader is not None, "评测脚本不存在: %s" % _SCRIPT
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_route_conflict"] = mod
    spec.loader.exec_module(mod)
    return mod


EV = _load_eval_module()


@pytest.fixture(scope="module")
def cases():
    return EV.load_cases(str(_CASES))


@pytest.fixture(scope="module")
def known_tools():
    return EV.known_tool_names(str(_ROOT))


@pytest.fixture(scope="module")
def eval_run():
    """真跑一次评测（纯 BM25，确定性），供多条断言复用"""
    cs = EV.load_cases(str(_CASES))
    trh, retriever = EV.build_retriever(str(_ROOT), no_embedding=True)
    rows = EV.run_eval(cs, trh, retriever)
    return {"rows": rows, "summary": EV.summarize(rows), "tau": EV.calibrate_tau(rows)}


@pytest.fixture(scope="module")
def cases_v2():
    """E1-B 扩充集（121 条：v1 原样 50 + 负样本 10 + 反向对 21 + 形态补充 21 + 工具覆盖 19）"""
    assert _CASES_V2.is_file(), "E1-B 扩充集缺失: %s" % _CASES_V2
    return EV.load_cases(str(_CASES_V2))


@pytest.fixture(scope="module")
def eval_run_v2():
    """在 v2 上真跑一次评测（纯 BM25，确定性）"""
    cs = EV.load_cases(str(_CASES_V2))
    trh, retriever = EV.build_retriever(str(_ROOT), no_embedding=True)
    rows = EV.run_eval(cs, trh, retriever)
    return {"rows": rows, "summary": EV.summarize(rows), "tau": EV.calibrate_tau(rows)}


def _default_version_baseline():
    """当前默认用例集版本及其基线（CLI 断言用；不写死版本号）"""
    path, ver, _why = EV.discover_cases(str(_ROOT), "auto")
    return ver, EV.BASELINE_BY_VERSION[ver]["bm25_only"]


# ════════════════════════════════════════════════════════════
#  一、用例集自检
# ════════════════════════════════════════════════════════════

class TestDatasetContract:
    def test_用例集存在且不少于50条(self, cases):
        assert _CASES.is_file(), "用例集缺失: %s" % _CASES
        assert len(cases) >= 50, "用例集只有 %d 条（守卫下限 50）" % len(cases)

    def test_用例集自检零错误(self, cases, known_tools):
        errors = EV.validate_cases(cases, known_tools=known_tools)
        assert errors == [], "用例集自检报错:\n" + "\n".join(errors)

    def test_id唯一且形如rc三位数(self, cases):
        ids = [c["id"] for c in cases]
        assert len(set(ids)) == len(ids), "存在重复 id"
        bad = [i for i in ids if not (i.startswith("rc-") and len(i) == 6 and i[3:].isdigit())]
        assert bad == [], "id 命名不合规: %s" % bad

    def test_query非空且无首尾空白(self, cases):
        for c in cases:
            assert isinstance(c["query"], str) and c["query"].strip(), c["id"]
            assert c["query"] == c["query"].strip(), "%s query 有首尾空白" % c["id"]

    def test_expect与forbid都是真实工具且互不冲突(self, cases, known_tools):
        for c in cases:
            exp, forb = set(c["expect_tools"]), set(c["forbid_tools"])
            assert exp and forb, "%s expect/forbid 不得为空" % c["id"]
            assert not (exp & forb), "%s expect 与 forbid 冲突: %s" % (c["id"], exp & forb)
            unknown = (exp | forb) - known_tools
            assert not unknown, "%s 引用了不存在的工具: %s" % (c["id"], unknown)

    def test_每条note都说明为什么是边界(self, cases):
        """note 是防止用例退化成泛泛而谈的关键字段——
        要求它既够长、又显式点名 Q2 的分组锚点、又出现「边界」二字。"""
        for c in cases:
            note = c["note"]
            assert len(note) >= 20, "%s note 过短: %r" % (c["id"], note)
            assert "边界" in note, "%s note 未说明边界: %r" % (c["id"], note)
            assert "组" in note, "%s note 未锚定 Q2 §3 的重叠组: %r" % (c["id"], note)

    def test_覆盖Q2的21组意图重叠样本(self, cases):
        groups = set()
        for c in cases:
            groups.update(c["source_groups"])
        assert groups == set(range(1, 22)), (
            "用例集未覆盖 Q2 §3 的全部 21 组重叠样本，缺失: %s"
            % sorted(set(range(1, 22)) - groups))

    def test_含足量全中文边界句(self, cases):
        """审计实测：中文在技能侧拿不到触发词、工具侧也有分词差异 ⇒
        全中文句（不含任何 ASCII 字母）必须占足量。"""
        zh = [c for c in cases if c["lang"] == "zh"]
        assert len(zh) >= 30, "全中文边界句只有 %d 条" % len(zh)
        for c in zh:
            assert not any(ch.isascii() and ch.isalpha() for ch in c["query"]), (
                "%s 标了 zh 却含 ASCII 字母: %r" % (c["id"], c["query"]))
        for c in cases:
            assert c["lang"] in ("zh", "mixed"), "%s lang 非法" % c["id"]

    def test_每条用例可序列化回JSONL(self, cases):
        for c in cases:
            line = json.dumps(c, ensure_ascii=False)
            assert json.loads(line)["id"] == c["id"]


# ════════════════════════════════════════════════════════════
#  二、条数守卫（防删条）
# ════════════════════════════════════════════════════════════

class TestSizeGuard:
    def test_少于50条必须判失败(self, cases, tmp_path, known_tools):
        trimmed = tmp_path / "trimmed.jsonl"
        trimmed.write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases[:49]) + "\n",
            encoding="utf-8")
        loaded = EV.load_cases(str(trimmed))
        assert len(loaded) == 49
        errors = EV.validate_cases(loaded, known_tools=known_tools)
        assert any("50" in e and "条数" in e for e in errors), \
            "删到 49 条竟然没被守卫拦住: %s" % errors

    def test_刚好50条可通过条数门(self, cases, tmp_path, known_tools):
        exact = tmp_path / "exact.jsonl"
        exact.write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases[:50]) + "\n",
            encoding="utf-8")
        errors = EV.validate_cases(EV.load_cases(str(exact)), known_tools=known_tools)
        assert not any("条数" in e for e in errors), errors


# ════════════════════════════════════════════════════════════
#  三、非法用例集必须被拒
# ════════════════════════════════════════════════════════════

class TestValidatorRejectsBadCases:
    def _base(self, cases):
        return [dict(c) for c in cases[:50]]

    def test_重复id被拒(self, cases):
        bad = self._base(cases)
        bad[1]["id"] = bad[0]["id"]
        assert any("id 重复" in e for e in EV.validate_cases(bad))

    def test_空query被拒(self, cases):
        bad = self._base(cases)
        bad[0]["query"] = "   "
        assert any("query 为空" in e for e in EV.validate_cases(bad))

    def test_expect与forbid冲突被拒(self, cases):
        bad = self._base(cases)
        bad[0]["forbid_tools"] = list(bad[0]["expect_tools"])
        assert any("冲突" in e for e in EV.validate_cases(bad))

    def test_不存在的工具名被拒(self, cases, known_tools):
        bad = self._base(cases)
        bad[0]["expect_tools"] = ["no_such_tool_xyz"]
        errors = EV.validate_cases(bad, known_tools=known_tools)
        assert any("不存在的工具名" in e for e in errors), errors

    def test_缺字段被拒(self, cases):
        bad = self._base(cases)
        bad[0].pop("note")
        assert any("缺字段 note" in e for e in EV.validate_cases(bad))

    def test_source_groups越界被拒(self, cases):
        bad = self._base(cases)
        bad[0]["source_groups"] = [22]
        assert any("source_groups" in e for e in EV.validate_cases(bad))


# ════════════════════════════════════════════════════════════
#  四、评测脚本可跑通 + 基线可复现
# ════════════════════════════════════════════════════════════

class TestEvalRuns:
    def test_50条全部产出判定(self, eval_run):
        rows = eval_run["rows"]
        assert len(rows) == 50
        for r in rows:
            assert isinstance(r["passed"], bool)
            assert r["gap"] >= 0.0
            assert r["n_ranked"] >= 0 and r["n_selected"] >= 0
            assert r["top1"] is None or isinstance(r["top1"], str)

    def test_基线可复现(self, eval_run):
        """本卡基线（bm25_only）：决策层通过 27/50，下发层 expected 命中 40/50。
        低分是现状 —— 这条断言守的是「数字有没有漂」，不是「分数够不够高」。"""
        base = EV.BASELINE["bm25_only"]
        summ = eval_run["summary"]
        assert summ["n_cases"] == base["cases"]
        assert summ["decision"]["passed"] == base["decision_pass"], (
            "决策层通过数从基线 %d 漂到 %d —— 检索/用例集有变动，必须复核后同步基线"
            % (base["decision_pass"], summ["decision"]["passed"]))
        assert summ["payload"]["expect_hit"] == base["payload_expect_pass"], (
            "下发层 expected 命中从基线 %d 漂到 %d"
            % (base["payload_expect_pass"], summ["payload"]["expect_hit"]))

    def test_混淆矩阵自洽(self, eval_run):
        s = eval_run["summary"]
        n = s["n_cases"]
        d, p = s["decision"], s["payload"]
        assert d["expect_hit"] + d["expect_miss"] == n
        assert d["forbid_recalled"] + d["forbid_not_recalled"] == n
        assert d["passed"] + d["failed"] == n
        assert p["expect_hit"] + p["expect_miss"] == n
        # 决策层通过 ⇒ 必然 expect 命中
        assert d["passed"] <= d["expect_hit"]

    def test_CLI退出码为0当达到下限(self, capsys):
        """E1-B 起 CLI 的默认用例集 = 最高版本（v2）⇒ 下限取**当前默认版本**的基线。
        （v1 的 27/50 由 test_基线可复现 继续守着，未放宽。）"""
        _ver, base = _default_version_baseline()
        rc = EV.main(["--root", str(_ROOT), "--no-embedding",
                      "--min-pass", str(base["decision_pass"])])
        capsys.readouterr()
        assert rc == 0

    def test_CLI退出码为1当低于下限(self, capsys):
        """门必须真的会红：把下限抬到基线之上必须返回 1"""
        _ver, base = _default_version_baseline()
        rc = EV.main(["--root", str(_ROOT), "--no-embedding",
                      "--min-pass", str(base["decision_pass"] + 1)])
        capsys.readouterr()
        assert rc == 1

    def test_CLI在用例集非法时返回1(self, tmp_path, cases, capsys):
        bad = tmp_path / "bad.jsonl"
        bad.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases[:49]) + "\n",
                       encoding="utf-8")
        rc = EV.main(["--root", str(_ROOT), "--cases", str(bad), "--no-embedding"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "用例集自检 FAIL" in out


# ════════════════════════════════════════════════════════════
#  五、τ 标定（必须数据驱动）
# ════════════════════════════════════════════════════════════

class TestTauCalibration:
    def test_给出的tau来自实测分差取值(self, eval_run):
        cal = eval_run["tau"]
        assert cal["table"], "标定表为空"
        assert cal["best"] is not None
        gaps = {r["gap"] for r in eval_run["rows"] if r["n_ranked"] > 0}
        assert cal["best"]["tau"] in set(cal["tau_candidates"])
        assert cal["best"]["tau"] in gaps or cal["best"]["tau"] == 0.0

    def test_混淆计数自洽(self, eval_run):
        cal = eval_run["tau"]
        n = cal["n_scored"]
        for t in cal["table"]:
            assert t["TP"] + t["FP"] + t["FN"] + t["TN"] == n
            assert t["TP"] + t["FN"] == sum(
                1 for r in eval_run["rows"] if r["n_ranked"] > 0 and r["expect_hit_decision"])

    def test_tau越小执行越多_FP单调不增(self, eval_run):
        table = sorted(eval_run["tau"]["table"], key=lambda t: t["tau"])
        for prev, nxt in zip(table, table[1:]):
            assert nxt["FP"] <= prev["FP"], "τ 增大反而误执行变多: %s -> %s" % (prev, nxt)

    def test_无区分度时不给经验值(self):
        """反「拍脑袋」守卫：全部 top1 都是错的 ⇒ 必须报不可标定，不许吐一个 τ"""
        rows = [{"n_ranked": 3, "gap": g, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": False}
                for g in (0.01, 0.02, 0.3, 0.9)]
        cal = EV.calibrate_tau(rows)
        assert cal["degenerate"] is True
        assert cal["best"]["f1"] == 0.0

    def test_完全可分时给出正确阈值(self):
        """构造完全可分的两簇：gap>=0.5 的都正确、<0.5 的都错误 ⇒ F1 必须为 1"""
        rows = [{"n_ranked": 2, "gap": g, "expect_hit_decision": ok,
                 "forbid_hit_decision": False, "passed": ok}
                for g, ok in ((0.9, True), (0.8, True), (0.1, False), (0.05, False))]
        cal = EV.calibrate_tau(rows)
        assert cal["degenerate"] is False
        assert cal["best"]["f1"] == 1.0
        assert 0.1 < cal["best"]["tau"] <= 0.8
        assert cal["best"]["FP"] == 0 and cal["best"]["FN"] == 0

    def test_零召回样本不计入标定(self):
        rows = [{"n_ranked": 0, "gap": 0.0, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": False}]
        cal = EV.calibrate_tau(rows)
        assert cal.get("best") is None or cal.get("n_scored", 0) == 0


# ════════════════════════════════════════════════════════════
#  六、零费用守卫（评测脚本不得调用 LLM）
# ════════════════════════════════════════════════════════════

class TestNoLlmCost:
    _FORBIDDEN = {"openai", "anthropic", "httpx", "requests", "aiohttp", "litellm",
                  "transformers", "torch"}

    def test_脚本不导入任何LLM客户端(self):
        tree = ast.parse(_SCRIPT.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        hit = imported & self._FORBIDDEN
        assert not hit, "评测脚本不得直接引入 LLM/网络客户端: %s" % sorted(hit)

    def test_脚本不引用llm_monitor(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "llm_monitor" not in node.module, "评测链路不得挂计量埋点"
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "llm_monitor" not in a.name

# ════════════════════════════════════════════════════════════
#  七、E1-B 扩充集（v2）：契约 / 负样本 / 门控 / 留出集
# ════════════════════════════════════════════════════════════

_SHAPES = ("表内词", "表外口语", "中英混写", "中文别称", "反向对")


class TestDatasetContractV2:
    """v2 的字段契约自检（沿用 E1 的判据，**没有放宽**：note 仍要含「边界」且 >= 20 字）"""

    def test_扩充集不少于100条(self, cases_v2):
        assert len(cases_v2) >= 100, "扩充集只有 %d 条（卡点要求 >= 100）" % len(cases_v2)

    def test_自检零错误(self, cases_v2, known_tools):
        errors = EV.validate_cases(cases_v2, known_tools=known_tools,
                                   min_cases=EV.MIN_CASES_BY_VERSION["v2"])
        assert errors == [], "v2 自检报错:\n" + "\n".join(errors)

    def test_保留v1的50条原样(self, cases, cases_v2):
        """★ 硬约束：旧 50 条不得被删改 —— v2 的前 50 条必须在 v1 的**每个字段**上逐字一致"""
        assert len(cases) == 50
        fields = ("id", "query", "expect_tools", "forbid_tools", "note", "source_groups", "lang")
        for old, new in zip(cases, cases_v2[:50]):
            for f in fields:
                assert old[f] == new[f], "v1 的 %s 字段 %s 被改动了: %r -> %r" % (
                    old["id"], f, old[f], new[f])

    def test_id唯一且命名合规(self, cases_v2):
        ids = [c["id"] for c in cases_v2]
        assert len(set(ids)) == len(ids), "存在重复 id"
        bad = [i for i in ids if not (i.startswith("rc-") and len(i) == 6 and i[3:].isdigit())]
        assert bad == [], "id 命名不合规: %s" % bad

    def test_每组至少4条(self, cases_v2):
        """E1 §5 方向④：把「只有 2 条」的组补到每组 >= 4 条"""
        from collections import Counter
        g = Counter()
        for c in cases_v2:
            g.update(c["source_groups"])
        assert set(g) == set(range(1, 22)), "21 组未全覆盖: 缺 %s" % sorted(set(range(1, 22)) - set(g))
        thin = {k: g[k] for k in range(1, 22) if g[k] < 4}
        assert thin == {}, "以下组的用例数 < 4: %s" % thin

    def test_覆盖五种说法形态(self, cases_v2):
        have = {c.get("shape") for c in cases_v2}
        missing = [s for s in _SHAPES if s not in have]
        assert missing == [], "缺少说法形态: %s（已有 %s）" % (missing, sorted(have))
        for c in cases_v2:
            assert c.get("shape") in _SHAPES + ("负样本", "工具覆盖"), \
                "%s shape 非法: %r" % (c["id"], c.get("shape"))

    def test_每条都有note说明为什么是边界(self, cases_v2):
        for c in cases_v2:
            note = c["note"]
            assert len(note) >= 20, "%s note 过短: %r" % (c["id"], note)
            assert "边界" in note, "%s note 未说明边界" % c["id"]
            assert "组" in note, "%s note 未锚定 Q2 §3 的重叠组" % c["id"]

    def test_expect与forbid都是真实工具且互不冲突(self, cases_v2, known_tools):
        for c in cases_v2:
            exp, forb = set(c["expect_tools"]), set(c["forbid_tools"])
            assert forb, "%s forbid 不得为空" % c["id"]
            if not c.get("should_clarify"):
                assert exp, "%s 正样本 expect 不得为空" % c["id"]
            assert not (exp & forb), "%s expect 与 forbid 冲突: %s" % (c["id"], exp & forb)
            unknown = (exp | forb) - known_tools
            assert not unknown, "%s 引用了不存在的工具: %s" % (c["id"], unknown)

    def test_全中文占比守(self, cases_v2):
        zh = [c for c in cases_v2 if c["lang"] == "zh"]
        assert len(zh) >= len(cases_v2) // 2, "全中文句只有 %d/%d 条" % (len(zh), len(cases_v2))
        for c in zh:
            assert not any(ch.isascii() and ch.isalpha() for ch in c["query"]), \
                "%s 标了 zh 却含 ASCII 字母: %r" % (c["id"], c["query"])
        for c in cases_v2:
            assert c["lang"] in ("zh", "mixed"), "%s lang 非法" % c["id"]

    def test_负样本契约(self, cases_v2):
        """★ 门的另一侧：expect_tools 为空 + should_clarify=true，且三类都要有"""
        neg = [c for c in cases_v2 if c.get("should_clarify")]
        assert len(neg) == 10, "负/澄清样本 %d 条（E1 §5 方向③要求 10 条）" % len(neg)
        cats = set()
        for c in neg:
            assert c["expect_tools"] == [], "%s 负样本的 expect_tools 必须为空" % c["id"]
            assert c["forbid_tools"], "%s 负样本必须给出最易被误召的工具" % c["id"]
            assert c["category"] in ("纯闲聊", "信息不足", "流程诉求"), \
                "%s 类目非法: %r" % (c["id"], c.get("category"))
            assert not (set(c["expect_tools"]) & set(c["forbid_tools"]))
            cats.add(c["category"])
        assert cats == {"纯闲聊", "信息不足", "流程诉求"}, "负样本三类未齐: %s" % sorted(cats)

    def test_工具覆盖面比v1更广(self, cases, cases_v2, known_tools):
        """E1 §5 方向①：按可路由工具补肯定样本"""
        def covered(cs):
            s = set()
            for c in cs:
                s.update(c["expect_tools"])
            return s
        v1_tools, v2_tools = covered(cases), covered(cases_v2)
        assert v2_tools > v1_tools, "v2 的 expect 工具集没有扩大"
        assert len(v2_tools) >= 40, "v2 只覆盖 %d 个工具" % len(v2_tools)
        assert len(v2_tools) > len(known_tools) // 3


class TestValidatorClarifySwitch:
    """『expect 可为空』必须是有开关的，不是把校验整个放开"""

    def _base(self, cases_v2):
        return [dict(c) for c in cases_v2]

    def test_负样本写了expect被拒(self, cases_v2):
        bad = self._base(cases_v2)
        idx = next(i for i, c in enumerate(bad) if c.get("should_clarify"))
        bad[idx]["expect_tools"] = ["todo_write"]
        errors = EV.validate_cases(bad, min_cases=1)
        assert any("expect_tools 必须为空" in e for e in errors), errors

    def test_负样本缺forbid被拒(self, cases_v2):
        bad = self._base(cases_v2)
        idx = next(i for i, c in enumerate(bad) if c.get("should_clarify"))
        bad[idx]["forbid_tools"] = []
        errors = EV.validate_cases(bad, min_cases=1)
        assert any("forbid_tools" in e for e in errors), errors

    def test_正样本expect为空被拒(self, cases_v2):
        bad = self._base(cases_v2)
        idx = next(i for i, c in enumerate(bad) if not c.get("should_clarify"))
        bad[idx]["expect_tools"] = []
        errors = EV.validate_cases(bad, min_cases=1)
        assert any("expect_tools" in e for e in errors), errors

    def test_条数守卫按版本收紧(self, cases_v2, tmp_path, known_tools):
        """v2 的守卫是 100 条；默认（未标版本）仍是 50 条 ⇒ 收紧密不可被静默绕开"""
        trimmed = tmp_path / "v2_trimmed_99.jsonl"
        trimmed.write_text(
            "\n".join(json.dumps(c, ensure_ascii=False) for c in cases_v2[:99]) + "\n",
            encoding="utf-8")
        loaded = EV.load_cases(str(trimmed))
        strict = EV.validate_cases(loaded, known_tools=known_tools,
                                   min_cases=EV.MIN_CASES_BY_VERSION["v2"])
        assert any("100" in e and "条数" in e for e in strict), strict
        loose = EV.validate_cases(loaded, known_tools=known_tools)
        assert not any("条数" in e for e in loose), loose


class TestCasesVersionSelection:
    def test_auto选data_eval下最高版本(self):
        path, ver, why = EV.discover_cases(str(_ROOT), "auto")
        assert ver == "v2", "auto 没选到 v2（选到 %s；理由 %s）" % (ver, why)
        assert path.endswith("route_conflict_cases.v2.jsonl")

    def test_显式版本可回退到v1(self):
        path, ver, _why = EV.discover_cases(str(_ROOT), "1")
        assert ver == "v1" and path.endswith("route_conflict_cases.v1.jsonl")

    def test_非法版本被拒(self):
        with pytest.raises(ValueError):
            EV.discover_cases(str(_ROOT), "9")

    def test_CLI仍能用v1跑出E1基线(self, capsys):
        """旧集不许被删改，也不许被新集挤掉：显式 --cases-version 1 必须复现 27/50"""
        rc = EV.main(["--root", str(_ROOT), "--cases-version", "1", "--no-embedding",
                      "--min-pass", "27"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "决策层通过 27/50" in out
        assert "τ 建议   : 0.0462" in out


class TestEvalRunsV2:
    def test_121条全部产出判定(self, eval_run_v2):
        rows = eval_run_v2["rows"]
        assert len(rows) == 121
        for r in rows:
            assert r["gap"] >= 0.0
            assert isinstance(r["should_clarify"], bool)
            if r["should_clarify"]:
                assert r["expect_tools"] == []
                assert r["passed"] is None or isinstance(r["passed"], bool)
            else:
                assert isinstance(r["passed"], bool)

    def test_基线可复现(self, eval_run_v2):
        """E1-B 基线（bm25_only，v2）：决策层 48/111 正样本，下发层 expected 88/111。
        低分是现状（新样本刻意压测难的一侧），这条断言守的是「数字有没有漂」。"""
        base = EV.BASELINE_BY_VERSION["v2"]["bm25_only"]
        s = eval_run_v2["summary"]
        assert s["n_cases"] == base["cases"]
        assert s["n_positive"] == base["positives"]
        assert s["decision"]["passed"] == base["decision_pass"], (
            "决策层通过数从基线 %d 漂到 %d" % (base["decision_pass"], s["decision"]["passed"]))
        assert s["payload"]["expect_hit"] == base["payload_expect_pass"], (
            "下发层 expected 命中从 %d 漂到 %d"
            % (base["payload_expect_pass"], s["payload"]["expect_hit"]))

    def test_负样本不进决策层通过率(self, eval_run_v2):
        s = eval_run_v2["summary"]
        d, p = s["decision"], s["payload"]
        assert s["n_cases"] == s["n_positive"] + s["n_negative"] == 121
        assert d["n"] == s["n_positive"] == 111
        assert p["n"] == s["n_positive"]
        assert d["passed"] + d["failed"] == d["n"]
        assert s["clarify"]["n"] == s["n_negative"] == 10


class TestClarifyGate:
    """★ 门的另一侧：该澄清时澄清（E1 的 50 条全正样本 ⇒ 当时结构上测不了）"""

    def test_始终执行时负样本全部算误执行(self):
        rows = [{"id": "n1", "n_ranked": 2, "gap": 0.5, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": None, "should_clarify": True},
                {"id": "n2", "n_ranked": 0, "gap": 0.0, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": None, "should_clarify": True}]
        cg = EV.clarify_gate_eval(rows, 0.0)
        # n2 零召回 = 没有可执行对象 ⇒ 必然澄清，不随 τ 变；n1 有候选且 gap=0.5 ⇒ 全放行下必被执行
        assert cg["all"]["n"] == 2 and cg["all"]["clarified"] == 1
        assert cg["all"]["executed_wrong"] == 1
        assert cg["scored"]["n"] == 1 and cg["scored"]["accuracy"] == 0.0
        assert cg["zero_recall"] == ["n2"]

    def test_负样本被执行一律记假阳(self):
        """把负样本并进标定后，执行一条本该澄清的句子必须进 FP —— 这是 E1 看不见的那类错"""
        rows = [{"id": "p1", "n_ranked": 2, "gap": 0.5, "expect_hit_decision": True,
                 "forbid_hit_decision": False, "passed": True},
                {"id": "n1", "n_ranked": 2, "gap": 0.9, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": None, "should_clarify": True}]
        cal = EV.calibrate_tau(rows)
        assert cal["n_negative_scored"] == 1 and cal["n_positive_scored"] == 1
        zero = [t for t in cal["table"] if t["tau"] == 0.0][0]
        assert zero["TP"] == 1 and zero["FP"] == 1, zero
        assert zero["precision"] == 0.5

    def test_负样本不算进正样本口径(self):
        rows = [{"id": "p1", "n_ranked": 2, "gap": 0.5, "expect_hit_decision": True,
                 "forbid_hit_decision": False, "passed": True},
                {"id": "n1", "n_ranked": 2, "gap": 0.9, "expect_hit_decision": False,
                 "forbid_hit_decision": False, "passed": None, "should_clarify": True}]
        pos_only = EV.calibrate_tau(rows, include_negatives=False)
        assert pos_only["n_negative_scored"] == 0
        assert pos_only["n_positive_scored"] == 1

    def test_回填门控判定(self):
        rows = [{"id": "n1", "n_ranked": 2, "gap": 0.1, "should_clarify": True,
                 "expect_hit_decision": False, "forbid_hit_decision": False, "passed": None},
                {"id": "n2", "n_ranked": 2, "gap": 0.9, "should_clarify": True,
                 "expect_hit_decision": False, "forbid_hit_decision": False, "passed": None}]
        ok = EV.apply_clarify_gate(rows, 0.5)
        assert ok == 1
        assert rows[0]["passed"] is True and rows[1]["passed"] is False

    def test_v2的τ下有门的另一侧读数(self, eval_run_v2):
        cal = eval_run_v2["tau"]
        cg = cal["clarify_at_best"]
        assert cg["all"]["n"] == 10
        assert cg["scored"]["n"] + len(cg["zero_recall"]) == 10
        assert 0.0 <= cg["all"]["accuracy"] <= 1.0
        # 每一条误执行的都必须是「real gap >= τ」而不是统计漏算
        best_tau = cal["best"]["tau"]
        for rid, _top1 in cg["top1_of_wrong"].items():
            row = [r for r in eval_run_v2["rows"] if r["id"] == rid][0]
            assert row["gap"] >= best_tau


class TestCrossHoldout:
    def test_双向切分并集为全集且交集为空(self, eval_run_v2, cases_v2):
        res = EV.cross_holdout(eval_run_v2["rows"], cases_v2)
        assert res["split_covers_all"] is True
        assert res["split_disjoint"] is True
        assert res["n_odd"] + res["n_even"] == len(cases_v2)
        assert res["n_rows"] == len(cases_v2)

    def test_两个方向都给出τ与验证读数(self, eval_run_v2, cases_v2):
        res = EV.cross_holdout(eval_run_v2["rows"], cases_v2)
        for key in ("odd->even", "even->odd"):
            d = res["directions"][key]
            assert d["tau_from_train"] is not None
            assert d["train"]["n_scored"] > 0 and d["valid"]["n_scored"] > 0
            assert d["f1_drop"] is not None
        assert res["tau_all"] is not None

    def test_留出集读数可被文本格式化(self, eval_run_v2, cases_v2):
        txt = EV.format_cross_holdout(EV.cross_holdout(eval_run_v2["rows"], cases_v2),
                                      str(_CASES_V2), len(cases_v2))
        assert "留出集跨集一致性" in txt and "odd->even" in txt and "even->odd" in txt
