# -*- coding: utf-8 -*-
"""L43 守卫：demos/run_evolution_demo.py 的 MockEnhancer 必须持续满足 OfflineEvolver 的鸭子类型契约

为什么需要这条守卫
------------------
该文件历史上**被并行会话覆盖回旧版 3 次**（记录见 scripts/generate_lineage_demo_data.py:4 与
07_EVO_T4 验收报告），每次都丢掉 MockEnhancer 的下列成员，导致 run_evolution_demo.py 与
verify_budget_break.py **双双报错**（即已登记的 L30 缺陷）：
    ① set_lineage_hook()          —— agent/skills_mgmt/offline_evolver.py:348 无条件调用
    ② lineage_archive 注入 + _get_lineage_archive()
    ③ bump_version(eval_result=)  —— 提交路径会 TypeError
    ④ bump_version 内触发谱系钩子  —— 否则 committed 谱系不落库

⇒ 本守卫把**契约本身**钉死（按签名 + 行为，而不是只 hasattr），使该文件再被覆盖回旧版时
   **立即变红**，而不是等到 demo 报错才被发现。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demos" / "run_evolution_demo.py"


def load_demo(path: Path):
    """按路径加载 demo 模块（demos/ 不是包，故用 importlib；模块内含 sys.path 兜底）"""
    spec = importlib.util.spec_from_file_location("_l43_demo_under_test", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def assert_ducktype_contract(M) -> None:
    """契约断言体（独立成函数，便于变异探针复用它来证明判据可红）"""
    sig_init = inspect.signature(M.__init__)
    assert "lineage_archive" in sig_init.parameters, (
        "MockEnhancer.__init__ 丢失 lineage_archive 参数 —— 这正是 L30 事故的形态")

    assert callable(getattr(M, "_get_lineage_archive", None)), (
        "丢失 _get_lineage_archive —— OfflineEvolver._resolve_archive 的兜底来源")
    assert callable(getattr(M, "set_lineage_hook", None)), (
        "丢失 set_lineage_hook —— offline_evolver.py:348 无条件调用，会直接 AttributeError")

    sig_bump = inspect.signature(M.bump_version)
    assert "eval_result" in sig_bump.parameters, (
        "bump_version 丢失 eval_result 形参 —— 提交路径会 TypeError")

    # 行为层：注入值可被取回；钩子必须由 bump_version 触发且带上 eval_result
    sentinel = object()
    inst = M(lineage_archive=sentinel)
    assert inst._get_lineage_archive() is sentinel, "lineage_archive 未按注入值返回"

    seen: list = []
    inst.set_lineage_hook(lambda ctx: seen.append(ctx))
    inst.bump_version("demo-skill", "fine_tune", changelog="c", eval_result={"score": 1})
    assert seen, "bump_version 未触发谱系钩子 ⇒ committed 谱系不会落库"
    assert seen[0].get("eval_result") == {"score": 1}, "钩子 ctx 丢失 eval_result"
    assert seen[0].get("skill_id") == "demo-skill", "钩子 ctx 丢失 skill_id"


def test_mock_enhancer_keeps_the_offline_evolver_ducktype_contract():
    assert DEMO.exists(), "demos/run_evolution_demo.py 不存在（被移动或删除？）"
    mod = load_demo(DEMO)
    assert hasattr(mod, "MockEnhancer"), "demos/run_evolution_demo.py 丢失 MockEnhancer"
    assert_ducktype_contract(mod.MockEnhancer)
