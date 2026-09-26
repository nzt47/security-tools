"""架构护栏：descriptors 层不得反向依赖 digestion（`no_circular_dependency`）

【这条护栏防的是什么 —— 一次真实的门禁回归】
    本批（RUNBOOK-1）让 `agent/descriptors/backfill.py` 在 S3-01 收口时调用
    `agent.digestion.stage.backfill_stages`。第一版写成「函数内懒加载 import」，
    自以为是安全的；实测把 CI 的 **架构规则校验** 从 master 的 **0 违规**顶成
    **2 违规**（`total_violations=2`，`passed=False`，exit 1）：

        descriptors.backfill → digestion.stage          ← 新增的这一条
        digestion.stage      → descriptors.bridge       （既有）
        descriptors.bridge   → skills_mgmt.store        （既有）
        skills_mgmt.store    → skills_mgmt.registry     （既有）
        skills_mgmt.registry → skills_mgmt.service      （既有）
        skills_mgmt.service  → descriptors.backfill     （既有）
        ⇒ 成环 ⇒ `no_circular_dependency`（high）

    **「挪进函数体」对本规则无效**：`dependency_graph._parse_imports` 用 `ast.walk`
    遍历整棵树**含函数体**，连 `importlib.import_module('x.y')` / `__import__('x.y')`
    这类**字面量**动态 import 也记边（`is_dynamic` 从不参与筛选）。见
    `agent/observability/arch_rules.py:126-137`（S11-09 的订正）。

【为什么既有测试没抓住它（这就是本文件存在的理由）】
    `tests/unit/test_arch_rules.py` 全程用**合成夹具**（`project_with_violations`），
    从不校验真实仓库树；真实树的架构校验**只在 CI 里跑**。于是这条回归能一路
    通过全量单测（22677 passed）却在 `架构规则校验` job 上变红。
    本文件的 `TestRealTreeNoCircularDependency` 把它拉回单测层。

【正确修法（arch_rules 自己给的）】
    依赖倒置 —— 把实现下沉到**无依赖的叶子契约模块**
    （`agent/descriptors/stage_contract.py`），由 digestion 侧在自己的导入期注册，
    底层模块只依赖契约、不依赖上层包。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DESCRIPTORS_DIR = REPO / "agent" / "descriptors"
CONTRACT_PATH = DESCRIPTORS_DIR / "stage_contract.py"

#: 被禁止从 descriptors 层反向 import 的模块前缀（= 那条成环边的形状）。
FORBIDDEN_PREFIX = "agent.digestion.stage"


def _iter_imports(tree: ast.AST):
    """产出 (模块名, 是否字面量动态 import) —— 与 dependency_graph 的口径对齐。

    `ast.walk` 遍历**整棵树含函数体**；`importlib.import_module('x.y')` 与
    `__import__('x.y')` 的**字面量**实参同样算一条边。
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, False
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                yield node.module, False
        elif isinstance(node, ast.Call):
            func = node.func
            name = ""
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    yield arg.value, True


class TestDescriptorsNeverImportsDigestionStage:
    """① AST 层：`agent/descriptors/` 下不存在指向 `agent.digestion.stage` 的边。"""

    def test_no_static_or_literal_dynamic_edge(self):
        offenders = []
        for py in sorted(DESCRIPTORS_DIR.rglob("*.py")):
            tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
            for module, is_dynamic in _iter_imports(tree):
                if module == FORBIDDEN_PREFIX or module.startswith(
                        FORBIDDEN_PREFIX + "."):
                    offenders.append(
                        f"{py.relative_to(REPO)} -> {module}"
                        f"{'（字面量动态 import，同样计边）' if is_dynamic else ''}")
        assert offenders == [], (
            "descriptors 层反向依赖 digestion.stage 会闭合 "
            "backfill→stage→bridge→…→backfill 这条环（架构规则 no_circular_dependency）："
            + "; ".join(offenders))

    def test_contract_module_is_a_pure_leaf(self):
        """② 契约模块必须是**纯叶子**：不 import 任何 agent 包，否则它自己会成为环的一环。"""
        tree = ast.parse(CONTRACT_PATH.read_text(encoding="utf-8"),
                         filename=str(CONTRACT_PATH))
        offenders = [m for m, _ in _iter_imports(tree)
                     if m == "agent" or m.startswith("agent.")]
        assert offenders == [], f"叶子契约不得依赖 agent 包，实际: {offenders}"


class TestStageRunnerContract:
    """③ 注册表语义：导入即注册；未注册**如实报错**；显式注入优先。"""

    def test_importing_digestion_stage_registers_the_runner(self):
        import agent.descriptors.stage_contract as contract
        import agent.digestion.stage as stage_mod

        assert contract.get_stage_runner() is stage_mod.backfill_stages, (
            "agent.digestion.stage 的导入期应把 backfill_stages 注册进叶子契约")

    def test_unregistered_runner_is_reported_loudly(self, monkeypatch):
        """未注册 ⇒ `auto_executed=False` **且带 error**（绝不静默跳过收口）。"""
        import agent.descriptors.stage_contract as contract
        from agent.descriptors.backfill import s3_01_followup

        monkeypatch.setattr(contract, "_STAGE_RUNNER", None)

        class _Reg:
            def list(self):
                return []

        out = s3_01_followup(_Reg(), None, auto=True)
        assert out["auto_executed"] is False
        assert "no_circular_dependency" in out.get("error", ""), out
        assert out["ingested"] == []

    def test_explicit_runner_wins_over_registry(self, monkeypatch):
        """显式注入优先于注册表（CLI / 测试不依赖导入顺序）。"""
        import agent.descriptors.stage_contract as contract
        from agent.descriptors.backfill import s3_01_followup

        def _must_not_be_used(*_a, **_kw):
            raise AssertionError("显式注入应优先，不应落到注册表")

        monkeypatch.setattr(contract, "_STAGE_RUNNER", _must_not_be_used)
        calls = []

        def _ok(reg, **kw):
            calls.append(kw)
            return {"ingested": [], "failed": [], "residual": [],
                    "policies": {}}

        class _Reg:
            def list(self):
                return []

            def save(self):
                raise AssertionError("无写入时不应 save")

        out = s3_01_followup(_Reg(), None, auto=True, stage_runner=_ok)
        assert out["auto_executed"] is True
        assert calls == [{"execute": True, "emit_events": True,
                          "actor": "digestion_pipeline"}]


@pytest.mark.slow
class TestRealTreeNoCircularDependency:
    """④ 真树级：跑**真实的**架构校验器，断言零循环依赖违规。

    这一条是「本回归本可以在单测层被抓住」的证明 —— 它跑的就是 CI
    `架构规则校验` job 的同一条判定路径（`ArchRuleValidator.validate()`）。
    """

    def test_real_tree_has_no_circular_dependency(self):
        from agent.observability.arch_rules import ArchRuleValidator

        report = ArchRuleValidator(
            root_dir="agent",
            exemptions_path="docs/architecture/legacy_exemptions.json",
            config_path="config.yaml",
        ).validate()

        cycles = [v for v in report.violations
                  if v.rule_id == "no_circular_dependency"]
        assert cycles == [], (
            "真实仓库树出现循环依赖（CI 架构规则校验会红）：\n" + "\n".join(
                f"  {v.source} -> {v.target} ({v.source_file}:{v.line})"
                for v in cycles))
