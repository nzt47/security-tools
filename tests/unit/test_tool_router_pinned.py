"""``agent/tool_router.py`` 的 PINNED_TOOLS「永不被截断」关注名单测试

被守护的 bug（真实事故）：
    ``get_tools_for_input()`` 按分类 ``priority`` 升序截断到 ``max_tools=25``，
    ``delegate`` 归 ``async``（priority 8）。**单一委派意图**没问题，但**复合请求**
    （命中 5+ 分类、候选总量 > 25）时截断点落在低优先级组内部，``delegate`` 被挤掉 ——
    表现为「用户说了要委派，模型却看不到委派工具」。实测：
        「把这个调研任务委派给一个子代理去做」            → async, core（12 个）✅
        「读取文件、搜索内容、执行命令…然后委派子代理汇总」→ async, code, core, file,
                                                          system, web（截到 25）❌

修法（定向，不做全局调优）：
    模块常量 :data:`agent.tool_router.PINNED_TOOLS` + 截断后由
    ``_restore_pinned_tools`` 补回**类别已命中却被数量上限丢掉**的工具；
    **补回后允许总数略超 max_tools**（宁可多一个工具，也不让「委派」这种跨步骤能力
    在复合请求里凭空消失）。不做全局调优的理由写在 ``PINNED_TOOLS`` 的注释里
    （提优先级会挤掉 ``code`` 的工具、放 ``core`` 会每轮多 500–800 token，且实测不可靠）。

被守护的不变量：
    1. pinned 工具在**复合输入**下仍出现在结果里；
    2. pinned 工具在**未命中其类别**（或未过白名单）时**不得**被加入 —— 补回不等于绕过路由；
    3. 非 pinned 工具**仍被正常截断**（回归护栏：截断逻辑本身没被放宽）；
    4. ``max_tools=None`` / ``<=0`` 时不限制，行为与改动前逐项一致（无截断即无补回）；
    5. 结果总数 ≤ ``max_tools + len(补回的 pinned)``，且无重复项。

口径纪律：**只用真实分类表与真实工具名**（``data/tool_definitions/*.yaml`` 的 ``name``），
不臆造工具名 —— 与 ``tests/unit/test_permission_policies_consistency.py`` 同款口径。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent.tool_router import (
    PINNED_TOOLS,
    TOOL_ALIASES,
    TOOL_CATEGORIES,
    _restore_pinned_tools,
    classify_user_input,
    get_tools_for_input,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFS_DIR = _PROJECT_ROOT / "data" / "tool_definitions"
_NAME_RE = re.compile(r"^name:\s*(\S+)\s*$")

#: 默认上限（与 ``get_tools_for_input`` 的签名默认值一致；改动它等于全局调优，本测试会失败提醒）
MAX_TOOLS = 25

#: 复合输入：命中 6 个分类（async/code/core/file/system/web），候选总量 > 25
COMPOSITE_INPUT = "读取文件、搜索内容、执行命令…然后委派子代理汇总"

#: 单一委派意图（回归基线：改动前后都应命中 async）
SINGLE_INTENT_INPUT = "把这个调研任务委派给一个子代理去做"

#: 与上面同构但**不含** async 关键词的输入：用于证明「未命中类别不补回」
NO_ASYNC_INPUT = "读取文件、搜索内容、执行命令并联网搜索最新资料"


# ════════════════════════════════════════════════════════════
#  fixtures / 口径
# ════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _no_evolution_injection(monkeypatch):
    """关掉「进化策略注入」的 fallback_tools 追加，保证结果总数只由路由本身决定

    ``get_tools_for_input`` 末尾会把策略里的 ``fallback_tools`` 追加到结果（"追加末尾,
    不破坏排序"）。那是另一条无关机制，会让本测试的"总数"断言随策略库漂移。
    """
    import agent.evolution.injector as injector

    monkeypatch.setattr(injector, "get_injector", lambda *a, **k: None)


@pytest.fixture(scope="module")
def real_tool_names() -> set:
    """注册表口径：``data/tool_definitions/*.yaml`` 的 ``name``（与一致性测试同源）"""
    names = set()
    for path in _DEFS_DIR.glob("*.yaml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            matched = _NAME_RE.match(line)
            if matched:
                names.add(matched.group(1))
                break
    assert names, f"未能从 {_DEFS_DIR} 读到任何工具定义（口径失效，测试本身需修）"
    return names


def _candidates(categories: set) -> set:
    """某组命中分类下的候选工具集合（复刻 get_tools_for_input 的「分类收集 + 别名合并」）"""
    selected = set()
    for cat in categories:
        info = TOOL_CATEGORIES.get(cat)
        if info:
            selected.update(info["tools"])
    for main_tool, alias_list in TOOL_ALIASES.items():
        if main_tool in selected:
            selected -= set(alias_list)
    return selected


def _non_pinned(tools) -> list:
    return [t for t in tools if t not in PINNED_TOOLS]


# ════════════════════════════════════════════════════════════
#  零、前置事实（口径自检：用例真的落在复合场景里）
# ════════════════════════════════════════════════════════════


class TestPreconditions:
    """先证明"用例确实构成复合请求"，否则后面所有断言都会变成空转"""

    def test_关注名单里的工具都是真实注册工具(self, real_tool_names):
        assert PINNED_TOOLS, "关注名单为空 ⇒ 本机制失效"
        unknown = [t for t in PINNED_TOOLS if t not in real_tool_names]
        assert not unknown, f"PINNED_TOOLS 含未注册工具名: {unknown}"

    def test_delegate_确实归_async_分类(self):
        assert "delegate" in TOOL_CATEGORIES["async"]["tools"]
        assert TOOL_CATEGORIES["async"]["priority"] > TOOL_CATEGORIES["core"]["priority"]

    def test_复合输入命中至少五个分类且候选超上限(self):
        categories = classify_user_input(COMPOSITE_INPUT)
        assert len(categories) >= 5, f"复合输入只命中 {sorted(categories)}"
        assert "async" in categories, "复合输入必须命中 async（否则测不到本 bug）"
        assert len(_candidates(categories)) > MAX_TOOLS + len(PINNED_TOOLS), (
            f"候选只有 {len(_candidates(categories))} 个，无法同时触发截断与补回"
        )

    def test_无_async_关键词的输入确实不命中_async(self):
        assert "async" not in classify_user_input(NO_ASYNC_INPUT)


# ════════════════════════════════════════════════════════════
#  一、核心：复合输入下 pinned 工具不被截断
# ════════════════════════════════════════════════════════════


class TestPinnedSurvivesTruncation:

    def test_复合输入下_delegate_仍可见(self):
        """本 bug 的直接回归：修复前这里为 False"""
        result = get_tools_for_input(COMPOSITE_INPUT)
        assert "delegate" in result, f"复合输入下 delegate 又被截断了: {result}"

    def test_复合输入下所有被命中的_pinned_工具都可见(self):
        categories = classify_user_input(COMPOSITE_INPUT)
        expected = [t for t in PINNED_TOOLS if t in _candidates(categories)]
        assert expected, "复合输入下没有任何 pinned 工具被类别命中 ⇒ 用例失效"
        result = get_tools_for_input(COMPOSITE_INPUT)
        missing = [t for t in expected if t not in result]
        assert not missing, f"这些 pinned 工具被类别命中却消失了: {missing}"

    def test_补回的部分只可能是_pinned_工具(self):
        """补回是"追加末尾"，不扰乱原有 priority 排序的头部"""
        result = get_tools_for_input(COMPOSITE_INPUT)
        overflow = result[MAX_TOOLS:]
        assert overflow, "复合输入下应有 pinned 工具被补回（否则本用例失去意义）"
        assert all(t in PINNED_TOOLS for t in overflow), (
            f"超过 max_tools 的部分只允许是 pinned 工具，实际: {overflow}"
        )
        assert result[MAX_TOOLS - 1] not in PINNED_TOOLS, (
            "第 25 位仍是普通工具 ⇒ 补回确实发生在截断之后"
        )

    def test_单一委派意图不受影响(self):
        """回归基线：单意图输入本来就能看到 delegate（改动不得把它弄丢或弄重）"""
        result = get_tools_for_input(SINGLE_INTENT_INPUT)
        assert "delegate" in result
        assert len(result) <= MAX_TOOLS
        assert len(result) == len(set(result))

    def test_补回不产生重复项(self):
        result = get_tools_for_input(COMPOSITE_INPUT)
        assert len(result) == len(set(result))


# ════════════════════════════════════════════════════════════
#  二、反面：未命中类别 / 未过白名单 ⇒ 不得补回
# ════════════════════════════════════════════════════════════


class TestPinnedNeverBypassesRouting:

    def test_未命中其类别时不得加入(self):
        """NO_ASYNC_INPUT 不含 async 关键词 ⇒ 即使发生截断也不能凭空出现 delegate"""
        categories = classify_user_input(NO_ASYNC_INPUT)
        assert "async" not in categories
        result = get_tools_for_input(NO_ASYNC_INPUT)
        for tool in PINNED_TOOLS:
            assert tool not in result, (
                f"{tool} 未命中其类别却被加入 ⇒ 等于绕过路由: {result}"
            )

    def test_空输入只有_core_类工具(self):
        """空输入 → 只命中 core（且不截断）⇒ 不得出现任何 async 工具"""
        result = get_tools_for_input("")
        assert set(result) <= set(TOOL_CATEGORIES["core"]["tools"])
        for tool in PINNED_TOOLS:
            assert tool not in result

    def test_白名单剔除时不得补回(self):
        """补回必须发生在白名单交集**之后**：被禁用的 pinned 工具不得复活"""
        categories = classify_user_input(COMPOSITE_INPUT)
        assert "async" in categories
        whitelist = sorted(_candidates(categories) - set(PINNED_TOOLS))
        result = get_tools_for_input(COMPOSITE_INPUT, enabled_whitelist=whitelist)
        for tool in PINNED_TOOLS:
            assert tool not in result, f"白名单已剔除 {tool}，补回却把它复活了"

    def test_helper_对未命中工具不补回(self):
        """直接钉住 helper 的补回条件（不经过分类关键词）"""
        sorted_tools = [f"tool_{i}" for i in range(30)]
        selected = set(sorted_tools[:28])          # 后 2 个不在 selected（＝未命中）
        assert _restore_pinned_tools(sorted_tools, selected, 25) == sorted_tools[:25]

    def test_helper_只补排在上限之外且已被命中的_pinned(self):
        sorted_tools = [f"tool_{i}" for i in range(30)]
        sorted_tools[27] = PINNED_TOOLS[0]          # 命中但排在截断点之外 ⇒ 应补回
        selected = set(sorted_tools[:28])
        assert _restore_pinned_tools(sorted_tools, selected, 25) == (
            sorted_tools[:25] + [PINNED_TOOLS[0]]
        )

    def test_helper_对已在上限内的_pinned_不重复补(self):
        sorted_tools = [f"tool_{i}" for i in range(30)]
        sorted_tools[3] = PINNED_TOOLS[0]           # 命中且在上限内 ⇒ 不重复追加
        selected = set(sorted_tools)
        assert _restore_pinned_tools(sorted_tools, selected, 25) == sorted_tools[:25]


# ════════════════════════════════════════════════════════════
#  三、回归护栏：非 pinned 工具仍被正常截断
# ════════════════════════════════════════════════════════════


class TestTruncationStillWorks:

    def test_非_pinned_工具数仍等于上限(self):
        result = get_tools_for_input(COMPOSITE_INPUT)
        assert len(_non_pinned(result)) == MAX_TOOLS, (
            f"非 pinned 工具应当被截到 {MAX_TOOLS} 个，实际 {len(_non_pinned(result))}"
        )

    def test_截断仍然真的丢掉了工具(self):
        """候选集合严格大于结果集合 ⇒ 截断没被"补回"逻辑整体放宽"""
        categories = classify_user_input(COMPOSITE_INPUT)
        candidates = _candidates(categories)
        result = set(get_tools_for_input(COMPOSITE_INPUT))
        assert result < candidates, "结果等于全部候选 ⇒ 截断失效"
        dropped = candidates - result
        assert dropped, "没有任何工具被丢掉 ⇒ 本用例失去意义"
        assert not (dropped & set(PINNED_TOOLS)), "pinned 工具不该出现在被丢集合里"

    def test_低优先级类别不再被整体挤掉(self):
        """【行为变更 2026-09-17】每个命中类别都有保底，不再整体归零

        原用例断言"复合输入下 code/system 组仍在截断点之外"。**那正是被修掉的缺陷**：
        旧口径 core(6)+web(9)+file(10)=25 恰好等于 max_tools ⇒ 复合输入下
        code/system/extension/pdf/software/schedule/v2 七个类别**全部返回 0 个**，
        连 `shell_execute` 都拿不到（评估报告 §4.3 头号缺陷）。

        现改为**类别保底**：每个命中类别先各取 floor_n 个（floor_n 按预算缩放，
        且保底总额不超过半预算），剩余名额再按相关度/优先级分配。
        故本用例翻转为"必须都能拿到"，并把保底失效当作回归来拦。
        """
        matched = classify_user_input(COMPOSITE_INPUT)
        result = set(get_tools_for_input(COMPOSITE_INPUT))
        # 只对**本输入真正命中的**类别断言保底：
        #   COMPOSITE_INPUT = "读取文件、搜索内容、执行命令…然后委派子代理汇总"
        #   命中 core/file/web/code/async，**不含 system**（没有进程/天气类关键词）。
        #   对未命中类别断言"有工具"是错的——保底不能凭空引入未命中类别的工具。
        assert "code" in matched, "用例前提失效：该输入应命中 code 类别"
        assert "system" not in matched, "用例前提失效：该输入本不应命中 system"
        for cat in sorted(matched):
            cat_tools = set(TOOL_CATEGORIES.get(cat, {}).get("tools", []))
            if not cat_tools:
                continue
            got = cat_tools & result
            assert got, (
                f"{cat} 组被整体挤掉 —— 类别保底失效（正是 §4.3 饥饿缺陷回归）"
            )

    @pytest.mark.parametrize("limit", [1, 5, 25, 40])
    def test_不同上限下都只多出_pinned(self, limit):
        """硬上限必须成立：总数 ≤ max_tools + pinned 补回数

        【2026-09-17】类别保底**不得**突破 max_tools（它是调用方的 token 预算契约）：
        预算够时每类保底 floor_n 个，预算不足"每类 1 个"时干脆不保底，让顺序决定。
        故此处仍以 limit + PINNED 为上界（实测 limit=1/2/5/10/25/40 全部满足）。
        """
        result = get_tools_for_input(COMPOSITE_INPUT, max_tools=limit)
        assert len(result) <= limit + len(PINNED_TOOLS), result
        assert len(result) == len(set(result))


# ════════════════════════════════════════════════════════════
#  四、max_tools 不限制时行为不变
# ════════════════════════════════════════════════════════════


class TestUnlimitedUnchanged:

    def test_max_tools_None_等于全部候选(self):
        """None ⇒ 不截断、无补回：结果必须与"分类收集 + 别名合并"逐项一致"""
        categories = classify_user_input(COMPOSITE_INPUT)
        result = get_tools_for_input(COMPOSITE_INPUT, max_tools=None)
        assert set(result) == _candidates(categories)
        assert len(result) == len(set(result))
        assert len(result) > MAX_TOOLS          # 确实超了默认上限却没被截

    def test_max_tools_None_与零与超大值三者一致(self):
        none_result = set(get_tools_for_input(COMPOSITE_INPUT, max_tools=None))
        assert none_result == set(get_tools_for_input(COMPOSITE_INPUT, max_tools=0))
        assert none_result == set(get_tools_for_input(COMPOSITE_INPUT, max_tools=-1))
        assert none_result == set(get_tools_for_input(COMPOSITE_INPUT, max_tools=10_000))

    def test_max_tools_None_时_delegate_本来就在(self):
        result = get_tools_for_input(COMPOSITE_INPUT, max_tools=None)
        assert "delegate" in result      # 不截断时无需补回也应在


# ════════════════════════════════════════════════════════════
#  五、上限公式
# ════════════════════════════════════════════════════════════


class TestCountBound:

    def test_总数不超过上限加补回数(self):
        result = get_tools_for_input(COMPOSITE_INPUT)
        restored = [t for t in result if t in PINNED_TOOLS]
        assert len(result) <= MAX_TOOLS + len(restored)
        assert len(result) == len(_non_pinned(result)) + len(restored)

    def test_复合输入下总数就是上限加一(self):
        """实测口径：25 个截断结果 + 1 个补回的 delegate = 26"""
        result = get_tools_for_input(COMPOSITE_INPUT)
        assert len(result) == MAX_TOOLS + 1, f"实际 {len(result)}: {result}"

    @pytest.mark.parametrize("limit", [1, 5, 25, 40])
    def test_helper_层同样满足上限公式(self, limit):
        categories = classify_user_input(COMPOSITE_INPUT)
        candidates = _candidates(categories)
        assert len(candidates) > limit
        result = _restore_pinned_tools(sorted(candidates), candidates, limit)
        restored = [t for t in result if t in PINNED_TOOLS]
        assert len(result) <= limit + len(restored)
        assert len(result) == len(set(result))
        assert set(result[:limit]) <= candidates
