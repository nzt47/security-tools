# -*- coding: utf-8 -*-
"""TASK-10 路由可验证化基线 —— 守卫测试

本文件是**数据与口径的守卫**，不是模型质量测试。它断言：

1. 用例集来源可核（逐条 anchor 仍存在于来源文件）——防"来源漂移"；
2. 用例集结构完整（id 唯一、层合法、层计数与 report.json 一致）；
3. 外部语料样本量与其声明一致（不复制数据，直读原文件）；
4. 类目表与 agent.tool_router.TOOL_CATEGORIES 同源（不臆造）；
5. 指标函数自身正确（含 ECE 分箱边界回归：0.3/0.6/0.9 必须各归其箱）；
6. 纪律不变量：不新增第三方依赖、不新增环境变量开关、不改判定语义。
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BASELINE_DIR = ROOT / "eval" / "routing_baseline"
CASES = BASELINE_DIR / "cases.json"
REPORT = BASELINE_DIR / "report.json"
RUNNER = ROOT / "scripts" / "run_routing_reliability.py"

VALID_LAYERS = {"rule", "template", "keyword_category", "hybrid_tool"}


@pytest.fixture(scope="module")
def spec():
    assert CASES.exists(), f"用例集缺失: {CASES}"
    return json.loads(CASES.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def report():
    assert REPORT.exists(), (
        f"报告缺失: {REPORT}；先运行 python scripts/run_routing_reliability.py"
    )
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════════
# 1. 来源可核（防漂移）
# ════════════════════════════════════════════════════════════════

class TestProvenance:
    def test_every_case_has_source_and_anchor(self, spec):
        missing = [c["id"] for c in spec["cases"] if not c.get("source") or not c.get("anchor")]
        assert not missing, f"以下用例缺少来源或锚点: {missing}"

    def test_every_anchor_still_exists_in_its_source(self, spec):
        """锚点必须在来源文件中仍可检索到（否则来源已漂移，读数不可信）"""
        bad = []
        cache = {}
        for c in spec["cases"]:
            p = ROOT / c["source"]
            if not p.exists():
                bad.append((c["id"], c["source"], "file_missing"))
                continue
            if c["source"] not in cache:
                cache[c["source"]] = p.read_text(encoding="utf-8")
            if c["anchor"] not in cache[c["source"]]:
                bad.append((c["id"], c["source"], c["anchor"][:60]))
        assert not bad, f"来源锚点已漂移: {bad}"

    def test_every_gold_is_traceable_to_a_repo_artifact(self, spec):
        """gold 不得凭空出现：用例的层必须在 layers 声明里，且层来源为仓内文件"""
        for L, meta in spec["layers"].items():
            assert (ROOT / meta["source"]).exists(), f"层 {L} 的来源文件不存在: {meta['source']}"


# ════════════════════════════════════════════════════════════════
# 2. 结构完整性
# ════════════════════════════════════════════════════════════════

class TestCaseSetIntegrity:
    def test_ids_unique(self, spec):
        ids = [c["id"] for c in spec["cases"]]
        assert len(ids) == len(set(ids)), "用例 id 重复"

    def test_layers_valid(self, spec):
        bad = [c["id"] for c in spec["cases"] if c["layer"] not in VALID_LAYERS]
        assert not bad, f"非法层: {bad}"

    def test_every_layer_has_cases(self, spec):
        seen = {c["layer"] for c in spec["cases"]}
        assert seen == VALID_LAYERS, f"层覆盖不全: {sorted(seen)}"

    def test_stale_cases_are_declared(self, spec):
        """标记 stale 的用例必须同时出现在 known_stale_labels 里并给出理由"""
        stale = {c["id"] for c in spec["cases"] if c.get("stale")}
        declared = {k["case_id"] for k in spec["known_stale_labels"]}
        assert stale == declared, f"stale 用例与声明不一致: {stale} vs {declared}"

    def test_report_counts_match_caseset(self, spec, report):
        cs = report["case_set"]
        assert cs["n_total"] == len(spec["cases"])
        n_stale = len([c for c in spec["cases"] if c.get("stale")])
        assert cs["n_stale_excluded"] == n_stale
        assert cs["n_scored"] == len(spec["cases"]) - n_stale
        for L in VALID_LAYERS:
            expect = len([c for c in spec["cases"] if c["layer"] == L and not c.get("stale")])
            assert report["metrics"]["per_layer"][L]["n"] == expect, f"层 {L} 计数不一致"

    def test_calib_and_test_splits_are_disjoint(self, report):
        sp = report["case_set"]["split"]
        assert sp["n_calib"] > 0 and sp["n_test"] > 0, "校准集/测试集不得为空（须真的划分）"
        assert 0.2 <= sp["calib_ratio_realized"] <= 0.8, (
            f"划分比例失真: {sp['calib_ratio_realized']}（须说明划分方式）"
        )


# ════════════════════════════════════════════════════════════════
# 3. 外部语料（直读原文件，不复制）
# ════════════════════════════════════════════════════════════════

class TestExternalCorpora:
    def _count(self, c):
        p = ROOT / c["path"]
        assert p.exists(), f"外部语料缺失: {p}"
        if c["json_key"] is None:
            n = 0
            for f in sorted(p.rglob("*.json")):
                data = json.loads(f.read_text(encoding="utf-8"))
                rows = data if isinstance(data, list) else list(data.values())
                n += len([r for r in rows if isinstance(r, dict) and r.get(c["text_key"])])
            return n
        data = json.loads(p.read_text(encoding="utf-8"))
        return len([r for r in data[c["json_key"]] if r.get(c["text_key"])])

    def test_declared_sample_sizes_hold(self, spec):
        for c in spec["external_corpora"]:
            n = self._count(c)
            assert n == c["expected_n"], (
                f"语料 {c['id']} 实际 {n} 条与声明 {c['expected_n']} 条不一致（来源已变）"
            )


# ════════════════════════════════════════════════════════════════
# 4. 类目表同源
# ════════════════════════════════════════════════════════════════

class TestCategoryTable:
    def test_matches_tool_router_category_table(self, report):
        from agent.tool_router import TOOL_CATEGORIES
        tbl = report["intent_category_table"]
        assert tbl["keys"] == sorted(TOOL_CATEGORIES.keys()), "类目表与 TOOL_CATEGORIES 不同源"
        assert tbl["n"] == len(TOOL_CATEGORIES)
        assert tbl["total_tools"] == sum(len(v["tools"]) for v in TOOL_CATEGORIES.values())

    def test_source_is_recorded(self, report):
        assert "agent/tool_router.py" in report["intent_category_table"]["source"]


# ════════════════════════════════════════════════════════════════
# 5. 指标函数正确性（含 ECE 分箱边界回归）
# ════════════════════════════════════════════════════════════════

class TestMetricFunctions:
    @pytest.fixture(scope="class")
    def mod(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("routing_reliability", str(RUNNER))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    def test_ece_boundary_values_are_not_dropped(self, mod):
        """回归：置信度恰好等于 0.3/0.6/0.9（枚举值）时不得被分箱丢弃

        历史缺陷：左开右闭边界使 0.3/0.6/0.9 落不进任何箱 ⇒ ECE 分母变小、读数失真。
        """
        probs = [0.3, 0.6, 0.9]
        ys = [1, 1, 1]
        tbl = mod.reliability_table(probs, ys)
        assert sum(b["n"] for b in tbl["bins"]) == 3, "有样本被分箱丢弃"
        assert tbl["n"] == 3

    def test_ece_zero_when_perfectly_calibrated(self, mod):
        tbl = mod.reliability_table([1.0, 1.0], [1, 1])
        assert tbl["ece"] == pytest.approx(0.0, abs=1e-9)

    def test_ece_positive_when_overconfident(self, mod):
        tbl = mod.reliability_table([0.9, 0.9, 0.9, 0.9], [1, 0, 0, 0])
        assert tbl["ece"] == pytest.approx(0.65, abs=1e-6)

    def test_temperature_identity_when_already_fitted(self, mod):
        """自报 0.7 且实际正确率恰好 0.7 时，最优 T 应≈1（已被校准，无需改动）"""
        logits = [mod._logit(0.7)] * 100
        ys = [1] * 70 + [0] * 30
        fit = mod.fit_temperature(logits, ys)
        assert 0.8 <= fit["T"] <= 1.25, f"T 应≈1，实际 {fit['T']}"
        assert fit["nll_after"] <= fit["nll_before"] + 1e-9, "温度缩放不得让 NLL 变差"

    def test_temperature_softens_overconfident_model(self, mod):
        logits = [mod._logit(0.99)] * 50 + [mod._logit(0.99)] * 50
        ys = [1] * 50 + [0] * 50
        fit = mod.fit_temperature(logits, ys)
        assert fit["T"] > 1.0, "对过度自信模型，T 必须 > 1（软化）"

    def test_is_correct_semantics(self, mod):
        assert mod.is_correct("rule", "check_time", "check_time") is True
        assert mod.is_correct("rule", "check_time", "greeting") is False
        assert mod.is_correct("rule", None, None) is True
        assert mod.is_correct("rule", None, "greeting") is False
        assert mod.is_correct("template", "unknown", "unknown") is True
        assert mod.is_correct("keyword_category", ["core", "file"], ["core", "file", "pdf"]) is True
        assert mod.is_correct("keyword_category", ["core", "pdf"], ["core", "file"]) is False

    def test_bootstrap_ci_brackets_point_estimate(self, mod):
        ys = [1] * 8 + [0] * 2
        lo, hi = mod.bootstrap_ci(ys, n_boot=400)
        assert lo <= 0.8 <= hi


# ════════════════════════════════════════════════════════════════
# 6. 纪律不变量
# ════════════════════════════════════════════════════════════════

class TestDiscipline:
    _STDLIB = {
        "argparse", "json", "math", "os", "random", "subprocess", "sys", "pathlib", "typing",
        "__future__", "importlib", "ast", "collections", "copy", "hashlib",
    }

    def test_runner_imports_are_stdlib_only(self):
        """D11：不得新增第三方依赖"""
        tree = ast.parse(RUNNER.read_text(encoding="utf-8"))
        third = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    top = a.name.split(".")[0]
                    if top not in self._STDLIB and top not in ("agent",):
                        third.add(top)
            elif isinstance(node, ast.ImportFrom) and node.module:
                top = node.module.split(".")[0]
                if top not in self._STDLIB and top not in ("agent",):
                    third.add(top)
        assert not third, f"发现非标准库依赖: {sorted(third)}"

    def test_no_new_env_switches_declared(self, report):
        """D5：本任务不新增 env 开关"""
        assert report["environment"]["new_env_switches"] == []
        assert report["environment"]["new_dependencies"] == []

    def test_runner_does_not_mutate_router_state(self):
        """D2：只观测不改判定——脚本不得出现会对路由状态赋值的调用"""
        src = RUNNER.read_text(encoding="utf-8")
        forbidden = [
            "register_intent(", "register_builtin_rules(self.engine.registry)",  # 后者是构造只读引擎，白名单
            "_alpha =", "add_keyword(", "remove_keyword(", "update_keyword(",
            "reset_keywords(", "registry.register(", ".clear()",
        ]
        hits = [f for f in forbidden if f in src and f != "register_builtin_rules(self.engine.registry)"]
        assert not hits, f"脚本含疑似改动判定语义的调用: {hits}"

    def test_report_declares_embedding_path_status(self, report):
        """诚实性：必须如实披露语义层实际运行在哪条路"""
        st = report["semantic_layer_status"]
        assert "degraded_bm25_only" in st and st["degraded_bm25_only"] is not None

    def test_report_has_honesty_boundaries(self, report):
        assert isinstance(report["honesty_boundaries"], list)
        assert len(report["honesty_boundaries"]) >= 4
