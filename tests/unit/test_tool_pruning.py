"""裁剪误伤率 = 0 —— 单测（TASK-08 E8 / v1.4 §7）

## 验收判据（任务书原文）

> 裁剪（token 预算裁剪）时，对 `risk >= high` **或** `confirm_level >= L2` 的工具
> **不参与裁剪**（或必须显式声明可裁）。

## 本文件怎么防"假绿"

"高危一个都没被裁"这句话，在**裁剪根本没发生**时同样成立 —— 那是最容易骗过
review 的一种绿。故每一条"零误伤"用例都同时断言三件事：

  1. `plan.applied is True`（裁剪**确实执行**了）；
  2. `plan.dropped` 非空，且**全部**是非受保护工具（低危确实被裁 —— 反例生效）；
  3. **反例对照**：把保护关掉（`protect=False` / `protect_high_risk=False` /
     `CP_TOOLSET_PRUNE_PROTECT=0`）后，同一场景下高危工具**确实会被裁**
     ⇒ 证明第 2 条里的"零误伤"是保护带来的，而不是场景凑出来的。

## 数据来源（**禁止自造夹具**）

全部工具元数据取自**真实权威源** `data/tool_definitions/*.yaml`
（经 `agent.lines.models.load_tool_meta()`，单一解析入口），
tool_defs 由真实的 `description` / `input_schema` 现场合成 ——
不手写 `{"risk": "high"}` 之类的假样本。
"""

from __future__ import annotations

import json

import pytest

from agent.capregistry import pruning as P
from agent.capregistry.pruning import (PROTECTED_CONFIRM_RANK, PROTECTED_RISKS,
                                       estimate_def_tokens, is_prune_protected,
                                       lookup_protection, prune_tool_defs_for_budget,
                                       tool_def_name)
from agent.lines import RISKS, LineProfile, assemble, get_line_registry
from agent.lines.assembler import _def_for
from agent.lines.models import CONFIRM_LEVELS, load_tool_meta
from agent.tool_schema_pruner import prune_tool_defs


# ════════════════════════════════════════════════════════════
#  夹具：真实数据
# ════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def meta():
    """真实的工具元数据（91 个 YAML）"""
    m = load_tool_meta()
    assert len(m) >= 91, f"工具定义数量异常偏少: {len(m)}"
    return m


@pytest.fixture(scope="module")
def real_defs(meta):
    """由**真实** YAML 合成的 OpenAI 格式 tool_defs（不是自造夹具）"""
    return [
        {"type": "function",
         "function": {"name": name, "description": m.description,
                      "parameters": m.input_schema or {}}}
        for name, m in sorted(meta.items())
    ]


def _protected(meta) -> set:
    """全量口径下的受保护工具集合（判据本身算出来的）"""
    return {n for n in meta if lookup_protection(n, meta=meta).protected}


def _names(defs) -> set:
    return {tool_def_name(d) for d in defs}


def _total_tokens(defs) -> int:
    return sum(estimate_def_tokens(d) for d in defs)


# ════════════════════════════════════════════════════════════
#  一、判据本体：与权威值域逐条对拍
# ════════════════════════════════════════════════════════════


class Test判据与规格一致:

    def test_风险阈值就是RISKS的后两档(self):
        assert list(PROTECTED_RISKS) == list(RISKS[-2:]) == ["high", "critical"]

    def test_确认级别阈值就是L2的序号(self):
        assert CONFIRM_LEVELS[PROTECTED_CONFIRM_RANK] == "L2"
        assert list(P.PROTECTED_CONFIRM_LEVELS) == ["L2", "L3"]

    def test_在全部真实工具上与规格逐条对拍(self, meta):
        """★ 91 个真实工具：判据结果必须等于"规格的直白写法"

        规格的直白写法 = `risk ∈ {high, critical}` **或** `confirm_level ∈ {L2, L3}`。
        本用例把两种写法在**全量真实数据**上对拍，任何一处不等价都会点名报出 ——
        这是"判据没有偷偷换口径"的证据。
        """
        mismatch = []
        for name, m in meta.items():
            got = is_prune_protected(risk=m.risk,
                                     confirm_level=m.effective_confirm_level)
            want = (m.risk in ("high", "critical")
                    or m.effective_confirm_level in ("L2", "L3"))
            if got != want:
                mismatch.append((name, m.risk, m.effective_confirm_level, got, want))
        assert mismatch == [], f"判据与规格不一致: {mismatch}"

    @pytest.mark.parametrize("risk,level,expected", [
        ("low", "L0", False),
        ("medium", "L0", False),
        ("low", "L1", False),
        ("medium", "L1", False),
        ("high", "L2", True),
        ("medium", "L2", True),      # 只看 confirm_level 也保护（"或"关系）
        ("high", "L1", True),        # 只看 risk 也保护
        ("critical", "L3", True),
    ])
    def test_判据真值表(self, risk, level, expected):
        assert is_prune_protected(risk=risk, confirm_level=level) is expected

    @pytest.mark.parametrize("risk,level", [("", ""), ("unknown", ""), ("weird", "L9")])
    def test_不可判定一律判保护(self, risk, level):
        """未知风险 ⇒ 不裁（"裁剪误伤率=0"唯一正确的失败方向）"""
        assert is_prune_protected(risk=risk, confirm_level=level) is True

    def test_元数据缺失的工具也判保护(self, meta):
        """运行时注册的 MCP/插件/生成工具不在 YAML 里 ⇒ 不得假设它可裁"""
        v = lookup_protection("这个工具不在任何YAML里", meta=meta)
        assert v.protected is True and "不可判定" in v.reason

    def test_显式声明可裁的逃逸口(self):
        """规格允许"必须显式声明可裁"；显式声明**优先于**高危推断"""
        assert is_prune_protected(risk="critical", confirm_level="L3",
                                  explicitly_prunable=True) is False
        assert is_prune_protected(risk="low", confirm_level="L0",
                                  explicitly_prunable=True) is False

    def test_判定带可读原因(self, meta):
        v = lookup_protection("shell_execute", meta=meta)
        assert v.protected is True
        assert "≥ high" in v.reason, f"原因里应写明命中了哪条轴: {v.reason}"
        assert v.to_dict()["risk"] == meta["shell_execute"].risk


# ════════════════════════════════════════════════════════════
#  二、真实数据的规模与分布（**两个口径都报**）
# ════════════════════════════════════════════════════════════


class Test真实数据两个口径:

    def test_口径一_全量(self, meta):
        """**全量口径**：91 个工具 YAML 里有多少受保护（基线，2026-09-20 实测）"""
        prot = _protected(meta)
        high = {n for n, m in meta.items() if m.risk in ("high", "critical")}
        cl2 = {n for n, m in meta.items() if m.effective_confirm_level in ("L2", "L3")}
        print(f"[全量口径] 工具 {len(meta)} 个：受保护 {len(prot)} 个"
              f"（risk>=high {len(high)}，confirm_level>=L2 {len(cl2)}，"
              f"并集 {len(high | cl2)}）")
        assert len(meta) >= 91
        assert len(high) >= 16, "risk>=high 的实测基线是 16（13 high + 3 critical）"
        assert len(cl2) >= 20
        # 判据算出的集合必须等于"两条轴取并集"（口径一致性，与规格对拍同源）
        assert prot == (high | cl2)

    def test_口径二_生产实际上限与截断(self, meta):
        """**生产实际口径**：激活主线 `engineering` 的 26 个工具里 8 个受保护

        同时如实记录一件重要事实：**当前 7 条内置主线的名额截断一次都没发生**
        （`truncated` 恒空 —— 见 `Test装配层裁剪::test_名额截断路径当前不可达`），
        工具级 token 预算也默认关闭 ⇒ 生产上"裁剪"目前只在显式启用后发生。
        """
        names = sorted(meta.keys())
        prot = _protected(meta)
        prof = get_line_registry().load("engineering")
        res = assemble(prof, names)
        in_line = [n for n in res.tools if n in prot]
        print(f"[生产口径] engineering: tools={len(res.tools)}/{res.max_tools}"
              f"，其中受保护 {len(in_line)} 个；truncated={len(res.truncated)}"
              f"；token_pruned={len(res.token_pruned)}")
        assert len(res.tools) == res.max_tools == 26
        assert len(in_line) == 8
        assert res.truncated == []
        assert res.token_pruned == []

    def test_生产实际口径_全量主线扫描(self, meta):
        """7 条内置主线：受保护工具都进了装配结果，且没有任何一条被截断"""
        reg = get_line_registry()
        names = sorted(meta.keys())
        prot = _protected(meta)
        rows = []
        for lid in ("assistant", "dev", "digital_life", "engineering",
                    "harness", "knowledge", "recon"):
            res = assemble(reg.load(lid), names)
            rows.append((lid, len(res.tools), res.max_tools,
                         len([n for n in res.tools if n in prot]),
                         len(res.truncated)))
        print("[生产口径] 主线扫描：" + "；".join(
            f"{lid}({cnt}/{cap} 受保护{p} 截断{t})" for lid, cnt, cap, p, t in rows))
        assert all(t == 0 for *_x, t in rows), "有主线发生了名额截断（需重新评估）"
        assert all(p > 0 for _lid, _c, _cap, p, _t in rows), \
            "有主线一个受保护工具都没有（可疑）"


# ════════════════════════════════════════════════════════════
#  三、token 预算裁剪：零误伤（含"裁剪确实发生"与反例）
# ════════════════════════════════════════════════════════════


class TestToken预算零误伤:

    def test_超预算_高危零误伤_且低危确实被裁(self, meta, real_defs):
        """★ E8 主用例：构造**真实超预算**场景

        基线：91 条真实 tool_defs 合计 14053 token（实测）；预算给 60% ⇒
        必然发生裁剪。断言：受保护 20 个一个不少，被裁的全部是低危。
        """
        total = _total_tokens(real_defs)
        budget = int(total * 0.6)
        kept, plan = prune_tool_defs_for_budget(real_defs, budget_tokens=budget,
                                                meta=meta)
        prot = _protected(meta)
        kept_names = _names(kept)
        print(f"[预算裁剪] 预算 {budget}/{total} token：保留 {len(kept)}，"
              f"裁掉 {len(plan.dropped)}，受保护保留 {len(plan.protected_kept)}，"
              f"误伤 {len(plan.protected_dropped)}，误伤率 {plan.misprune_rate()}")

        # ① 裁剪**确实发生**（否则"零误伤"是假绿）
        assert plan.applied is True
        assert plan.dropped, "一个都没裁 ⇒ 用例无效"
        assert plan.used_tokens < total, "token 总量没有下降 ⇒ 裁剪没生效"

        # ② 高危零误伤
        assert plan.protected_dropped == ()
        assert plan.misprune_rate() == 0.0
        assert prot <= kept_names, f"有受保护工具被裁: {sorted(prot - kept_names)}"

        # ③ 反例：被裁的**全部**是低危可裁工具（低危确实被裁了）
        dropped = set(plan.dropped)
        assert dropped, "dropped 为空"
        assert not (dropped & prot), "dropped 里混进了受保护工具"
        assert dropped <= (set(meta) - prot), "被裁工具里有判据之外的例外"
        assert len(dropped) >= 5, f"只裁了 {len(dropped)} 个，样本过少"

    def test_反例_关掉保护后高危确实会被裁(self, meta, real_defs):
        """★ 证明"零误伤"来自保护，而不是来自场景凑巧

        同一预算、同一数据，只把 `protect` 置 False ⇒ 高危工具**确实**被裁。
        若这条不成立，上面那条 `misprune_rate == 0` 就没有鉴别力。
        """
        total = _total_tokens(real_defs)
        budget = int(total * 0.6)
        prot = _protected(meta)
        _kept_off, plan_off = prune_tool_defs_for_budget(
            real_defs, budget_tokens=budget, meta=meta, protect=False)
        hit = [n for n in plan_off.dropped if n in prot]
        print(f"[反例·无保护] 裁掉 {len(plan_off.dropped)} 个，其中高危 {len(hit)} 个: "
              f"{hit[:6]}")
        assert hit, "关掉保护后高危仍未被裁 ⇒ 该场景无法证明保护有效"
        # 【口径说明】保护关闭时 `BudgetPlan` 的**账目**里没有"受保护"这一维
        # （`protected_kept` 恒空、`misprune_rate()` 恒 0）—— 那是"没有保护"，
        # 不是"没误伤"。真正的误伤要靠**规格集合**（`prot`）判定，即上面的 `hit`。
        assert plan_off.protected_kept == ()
        assert plan_off.misprune_rate() == 0.0
        assert len(hit) == 7, f"无保护口径下的高危误裁基线是 7，实测 {len(hit)}"

    def test_预算充足时不裁剪(self, meta, real_defs):
        """对照：预算充足 ⇒ `applied=False`、`dropped=[]`（防"裁剪"被误判为发生）"""
        total = _total_tokens(real_defs)
        kept, plan = prune_tool_defs_for_budget(real_defs, budget_tokens=total + 1000,
                                                meta=meta)
        assert plan.applied is False and plan.dropped == ()
        assert len(kept) == len(real_defs)

    def test_预算为零等于不裁剪(self, meta, real_defs):
        """`≤0` = 开关关闭口径（默认行为，D2 向后兼容）"""
        kept, plan = prune_tool_defs_for_budget(real_defs, budget_tokens=0, meta=meta)
        assert plan.applied is False and plan.reason.startswith("预算 ≤ 0")
        assert len(kept) == len(real_defs)
        assert plan.misprune_rate() == 0.0

    def test_预算极小_受保护一个都不裁且如实暴露超预算(self, meta, real_defs):
        """极端：预算远小于受保护工具自身的占用 ⇒ **宁可超预算也不裁高危**"""
        prot = _protected(meta)
        kept, plan = prune_tool_defs_for_budget(real_defs, budget_tokens=300, meta=meta)
        assert plan.over_budget is True, "超预算必须如实暴露（不得静默牺牲高危）"
        assert plan.protected_dropped == ()
        assert prot <= _names(kept), "受保护工具在极小预算下被裁"
        assert len(kept) == len(prot), f"应只剩受保护项，实际 {len(kept)}"

    def test_账目结构可被外部消费(self, meta, real_defs):
        _kept, plan = prune_tool_defs_for_budget(
            real_defs, budget_tokens=int(_total_tokens(real_defs) * 0.5), meta=meta)
        d = plan.to_dict()
        for key in ("budget_tokens", "used_tokens", "kept_count", "dropped",
                    "protected_kept", "protected_dropped", "protected_total",
                    "applied", "over_budget", "misprune_rate", "reason"):
            assert key in d, f"账目缺字段: {key}"
        assert d["misprune_rate"] == 0.0 and d["applied"] is True
        assert d["protected_total"] == len(_protected(meta))


# ════════════════════════════════════════════════════════════
#  四、装配层（agent/lines/assembler.py）
# ════════════════════════════════════════════════════════════


class Test装配层裁剪:

    def test_默认不裁剪_行为与改动前一致(self, meta):
        """默认口径：`token_budget=None` ⇒ 不裁剪（E8 不得改变既有行为，D2）"""
        names = sorted(meta.keys())
        res = assemble(get_line_registry().load("engineering"), names)
        assert res.token_budget == 0 and res.token_pruned == []
        assert res.token_used == 0 and res.token_over_budget is False
        assert res.protected_kept == []
        assert len(res.tools) == 26

    def test_装配层超预算_高危零误伤_低危被裁(self, meta):
        """装配结果的 token 预算裁剪：受保护工具零误伤"""
        names = sorted(meta.keys())
        prof = get_line_registry().load("engineering")
        base = assemble(prof, names)
        total = sum(estimate_def_tokens(_def_for(n, meta)) for n in base.tools)
        budget = int(total * 0.7)
        res = assemble(prof, names, token_budget=budget)
        prot = _protected(meta)
        pruned = set(res.token_pruned)
        print(f"[装配层] 预算 {budget}/{total}：保留 {len(res.tools)}，"
              f"裁掉 {len(pruned)}，受保护保留 {len(res.protected_kept)}")

        # ① 裁剪确实发生 + 低危确实被裁
        assert res.token_pruned, "装配层没有发生裁剪 ⇒ 用例无效"
        assert len(res.tools) < len(base.tools)
        assert res.token_used <= budget
        # ② 高危零误伤
        assert not (pruned & prot), f"装配层裁掉了高危: {sorted(pruned & prot)}"
        assert res.protected_kept, "没有任何「因受保护而保留」的记录"
        # ③ 被裁的全部是低危
        assert pruned <= (set(meta) - prot)

    def test_装配层反例_无保护时高危被裁(self, meta):
        """同场景关掉保护 ⇒ 高危确实被裁（证明上一条有鉴别力）"""
        names = sorted(meta.keys())
        prof = get_line_registry().load("engineering")
        base = assemble(prof, names)
        total = sum(estimate_def_tokens(_def_for(n, meta)) for n in base.tools)
        budget = int(total * 0.7)
        res = assemble(prof, names, token_budget=budget, protect_high_risk=False)
        prot = _protected(meta)
        hit = sorted(set(res.token_pruned) & prot)
        print(f"[装配层·无保护] 裁掉 {len(res.token_pruned)} 个，其中高危 {len(hit)} 个: {hit}")
        assert hit, "关掉保护后装配层仍不裁高危 ⇒ 场景无鉴别力"

    def test_装配层保护关闭且无预算_完全等价旧口径(self, meta):
        names = sorted(meta.keys())
        prof = get_line_registry().load("engineering")
        on = assemble(prof, names)
        off = assemble(prof, names, protect_high_risk=False)
        assert on.tools == off.tools, "保护开关在无预算时不应改变结果（默认零影响）"

    def test_名额截断路径当前不可达_如实记录(self, meta):
        """⚠️ 现状记录（TASK-08 发现，**任务书描述与现状不符**之一）

        `assemble()` 第 ④ 步在 `len(kept) >= cap` 时 `break` ⇒ 第 ⑤ 步的截断
        **永远拿不到可裁的尾巴**（`scored_kept` 恒空）⇒ `truncated` 恒为空列表。
        本用例把这一现状钉住：若哪天第 ④ 步被改成"先加满再截"，它会立刻变红，
        提醒复核第 ⑤ 步的保护语义（那时保护才真正开始起作用）。
        """
        names = sorted(meta.keys())
        prof = LineProfile(id="t", name="t",
                           plane_weights={"resident": 10.0, "perceive": 10.0, "act": 10.0},
                           plane_floors={"resident": 4, "perceive": 4, "act": 4},
                           max_tools=6)
        res = assemble(prof, names)
        assert res.over_budget is True, "保底超额必须如实暴露"
        assert res.truncated == [], "第 ⑤ 步的截断在现状下不可达（见 docstring）"
        assert len(res.tools) == 12


# ════════════════════════════════════════════════════════════
#  五、接线（agent/tool_schema_pruner.py）与回滚开关
# ════════════════════════════════════════════════════════════


class Test裁剪器接线与回滚:

    def test_不传预算时行为完全不变(self, real_defs):
        """D2 向后兼容：既有调用点（orchestrator / plugins.chat）不传预算 ⇒ 零变化"""
        ctx: dict = {}
        out = prune_tool_defs(list(real_defs), ctx)
        assert len(out) == len(real_defs), "真实数据里没有 deprecated 工具，数不应变"
        assert "prune_plan" not in ctx, "未启用预算却写入了裁剪账目"

    def test_传预算时裁剪并回填账目(self, meta, real_defs):
        total = _total_tokens(real_defs)
        budget = int(total * 0.6)
        ctx: dict = {}
        out = prune_tool_defs(list(real_defs), ctx, budget_tokens=budget)
        plan = ctx.get("prune_plan")
        assert plan is not None, "账目没有回填 ⇒ 外部无法证明裁剪发生"
        assert plan["applied"] is True and plan["dropped"]
        assert plan["misprune_rate"] == 0.0 and plan["protected_dropped"] == []
        assert len(out) < len(real_defs)
        assert _protected(meta) <= _names(out)

    def test_deprecated_声明式移除不受保护影响(self, meta):
        """**声明为废弃 ≠ 裁剪误伤**：那是人工的显式淘汰声明，保留既有行为"""
        td = _def_for("read_file", meta)
        td["function"]["deprecated"] = True
        out = prune_tool_defs([dict(td)], {}, budget_tokens=10)
        assert out == [], "deprecated 的整工具移除是既有语义，不应被保护拦下"

    def test_开关关闭后退回无保护的旧裁剪行为(self, meta, real_defs, monkeypatch):
        """★ 回滚路径 `CP_TOOLSET_PRUNE_PROTECT=0`（B 级开关，已登记注册表）"""
        total = _total_tokens(real_defs)
        budget = int(total * 0.6)
        monkeypatch.setattr(P, "CP_TOOLSET_PRUNE_PROTECT", False)
        assert P.protect_enabled() is False
        prot = _protected(meta)
        ctx: dict = {}
        out = prune_tool_defs(list(real_defs), ctx, budget_tokens=budget)
        plan = ctx["prune_plan"]
        hit = [n for n in plan["dropped"] if n in prot]
        print(f"[回滚·无保护] 裁掉 {len(plan['dropped'])} 个，其中高危 {len(hit)} 个")
        assert hit, "关掉开关后仍未裁到高危 ⇒ 回滚路径未生效"
        assert len(out) < len(real_defs)

    def test_判据模块不可用时判据自身拒绝裁剪(self, meta):
        """降级方向：判据不可得 ⇒ **不裁**（宁可少裁，不可误裁高危）"""
        total = _total_tokens(
            [_def_for(n, meta) for n in sorted(meta.keys())])
        defs = [_def_for(n, meta) for n in sorted(meta.keys())]
        # 用一个空 meta 模拟"元数据不可得"：所有工具都应判"不可判定 ⇒ 保护"
        kept, plan = prune_tool_defs_for_budget(defs, budget_tokens=int(total * 0.5),
                                                meta={})
        assert plan.protected_dropped == ()
        assert len(kept) == len(defs), "元数据不可得时不得裁掉任何工具"


# ════════════════════════════════════════════════════════════
#  六、估算口径
# ════════════════════════════════════════════════════════════


class TestToken估算口径:

    def test_估算公式为序列化长度除以三(self):
        td = {"type": "function", "function": {"name": "x", "description": "y" * 300}}
        expect = max(1, len(json.dumps(td, ensure_ascii=False, sort_keys=True)) // 3)
        assert estimate_def_tokens(td) == expect

    def test_非序列化对象不抛异常(self):
        class Weird:
            pass
        assert estimate_def_tokens({"x": Weird()}) >= 1
