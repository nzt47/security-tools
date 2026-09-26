# -*- coding: utf-8 -*-
"""B1：工具数口径唯一 —— 「宣告的工具数」必须等于「当轮下发的 tool_defs」。

────────────────────────────────────────────────────────────────────────────
这条不变量是什么、为什么值得钉住
────────────────────────────────────────────────────────────────────────────
审计实测（`docs/audit_skill_governance/AUDIT_AND_PLAN.md` E6 / B1）：
同一件事曾有三个互相矛盾的数字 ——

    | 位置 | 数字 | 性质 |
    |---|---|---|
    | 提示词 `_build_tool_status_text()` 渲染的「【工具】…（共 86 个）」 | 86 | 发给模型的**宣告值** |
    | `data/tool_definitions/*.yaml` 文件数 | 91 | 声明总数 |
    | 实际注入的 `tools[]`（主线 `engineering` 白名单） | 26 | **真实下发值** |

为什么这不是"难看"而已：`agent/tools_prompt_guard.py` 的实测根因链证明
**"提示词宣告了工具、请求却没带 `tools`"** 会让上游 DeepSeek 退回 DSML 文本协议、
标记随正文返回 ⇒ 用户可见泄漏。宣告值与下发值两套口径，正是这条缺陷的触发条件；
口径越乱，"宣告了但没下发"越难在线上被发现。

本文件把"唯一事实源"锁成可自动执行的断言：
    ① 渲染侧只认入参 `tool_defs`（数量与名字同源，不做二次统计）
    ② **宣告数 == 实际下发 tool_defs 长度**（真实注册表 + 收窄白名单两种情形）
    ③ 下发 0 个（含"加载失败"）时提示词被就地中和
    ④ 渲染格式仍被根因标记 `【工具】` 覆盖（守卫不会因为格式改版而失明）
    ⑤ 生产文件里不再有硬编码的工具数
    ⑥ token 计量是**实测**（tiktoken cl100k_base），不是"字符÷3"换算
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from agent.tools_prompt_guard import (  # noqa: E402
    EVENT_TOOLS_PROMPT_MISMATCH,
    NEUTRAL_FOOTER,
    TOOL_ADVERT_EMPTY_LINE,
    align_system_prompt_with_tools,
    assert_invariant,
    count_tool_defs_tokens,
    hit_proven_marker,
    prompt_advertises_tools,
    render_tool_advert_line,
    resolve_dispatch_tool_defs,
    tool_names_of,
)

#: 渲染行的解析式（**唯一**格式：`【工具】本轮向模型下发(N 个): 名字, 名字…`）
_ADVERT_RE = re.compile(r"【工具】本轮向模型下发\((\d+) 个\): ([^\n]*)")

#: 本文件注册的测试工具来源标识（fixture 只注销这一来源，不碰真实注册表）
_TEST_SOURCE = "b1_count_consistency_test"


def _mk_defs(n, prefix="b1_probe_tool"):
    """造 n 条 OpenAI 形态的工具定义。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "%s_%d" % (prefix, i),
                "description": "B1 用例工具 %d" % i,
                "parameters": {"type": "object", "properties": {},
                               "additionalProperties": True},
            },
        }
        for i in range(n)
    ]


def _advert_count(text):
    """从渲染文本里取宣告的工具数；取不到返回 None。"""
    m = _ADVERT_RE.search(text or "")
    return int(m.group(1)) if m else None


def _advert_names(text):
    """从渲染文本里取宣告的工具名列表；取不到返回 None。"""
    m = _ADVERT_RE.search(text or "")
    if not m:
        return None
    body = m.group(2).strip()
    return [x.strip() for x in body.split(",") if x.strip()] if body else []


def _status_text(**kwargs):
    """用**真实**的 `DigitalLifePersonaMixin` 渲染工具状态段（非自造字符串）。"""
    from agent.digital_life_persona import DigitalLifePersonaMixin

    class _P(DigitalLifePersonaMixin):
        def __init__(self):
            self._cached_tool_status = None
            self._cached_skill_instructions = None
            self._loaded_skill_ids = []

    return _P()._build_tool_status_text(**kwargs)


def _real_prompt(tool_status):
    """把工具状态段塞进**真实**系统提示词模板（与生产同一条渲染路径）。"""
    from agent.system_prompt_manager import get_template

    return get_template().format(
        current_date="2025年1月1日", body_status="（略）", mode_name="正常",
        mode_description="正常", memory_context="（略）",
        tool_status=tool_status, skill_instructions="",
    )


@pytest.fixture
def registered_tools():
    """在真实注册表里登记若干测试工具，用完按 source 注销。

    【不易】用**真实注册表**（`agent.tools.register`）而不是打桩 `get_tool_defs`：
        本卡要证的是"宣告值来自真正下发的那一份"，打桩就把被测链路换成了桩，
        结论也就成了桩的结论。
    【不易】只注销本 fixture 登记的 source，不动真实工具（注册表在本进程里是全局的，
        同一次 pytest 会连跑本文件与 test_tools_prompt_alignment.py）。
    """
    from agent import tools as _tools

    names = ["b1_alpha", "b1_beta", "b1_gamma"]
    try:
        for n in names:
            _tools.register(
                n, "B1 用例工具 %s" % n,
                schema={"type": "object", "properties": {},
                        "additionalProperties": True},
                handler=(lambda **kw: None), source=_TEST_SOURCE)
        yield names
    finally:
        _tools.unregister_by_source(_TEST_SOURCE)


@pytest.fixture(autouse=True)
def isolated_tool_registry():
    """本文件的每个用例都在**干净的工具注册表**上跑；用例结束**逐条还原**

    ────────────────────────────────────────────────────────────────────
    为什么必须有这条隔离（TESTINFRA-2 实测，不是预防性洁癖）
    ────────────────────────────────────────────────────────────────────
    `resolve_dispatch_tool_defs(None)` 读的是 `agent.tools` 的**进程级**注册表
    （`agent/tools/__init__.py:16 _registry`）。而"登记真实工具"这件事在本仓的
    测试里**是泄漏的**——实测三个最小组合（两文件、`-p no:randomly`）：

        tests/unit/test_search_tools.py            → 留下 7 个真实工具
        tests/unit/test_policy_integration.py      → 留下 8 个
        tests/unit/test_background_tasks_routes.py → 留下 91 个（整套内建工具）

    只要其中任何一个排在本文件之前，`TestAdvertEqualsDispatched` 的
    **前置条件**（下发集 == 本文件登记的 3 个工具）就失配
    → 恰好这 2 条红（实测 `2 failed, 19 passed`，与全量里那 2 条**逐字同形**）。
    这正是 v3/v4 两次全量里"仅全量复现"的那 2 条：单跑 21 passed、两个"显而易见的"
    双文件组合也全绿，因为真正的污染源在**别处**、且随分块/顺序而变。

    【口径不降】断言**逐字未改**（一条都没放宽）：本夹具只做"隔离"——
    用例开始时把注册表清空（快照在手），结束时把**原来的每一条**原样放回
    （含 source / schema / handler / source_id），并推进 `_registry_version`
    让各级缓存失效。别的测试文件看到的状态与用例前完全一致。
    【为什么不用 `tools.clear()`】它会**连 `_tool_health` 一起清**（别处用例的健康
    计数会被抹掉）；这里只换 `_registry` 的内容，作用面最小。
    """
    from agent import tools as _tools

    saved = dict(_tools._registry)

    def _install(entries: dict) -> None:
        _tools._registry.clear()
        _tools._registry.update(entries)
        _tools._registry_version += 1

    _install({})
    try:
        yield
    finally:
        _install(saved)


# ══════════════════════════════════════════════════════════════════════
#  ① 渲染侧：数量与名字只能来自入参 tool_defs
# ══════════════════════════════════════════════════════════════════════

class TestRenderIsSameSource:
    @pytest.mark.parametrize("n", [1, 3, 26])
    def test_宣告数与名字都来自入参(self, n):
        defs = _mk_defs(n)
        line = render_tool_advert_line(defs)
        assert _advert_count(line) == n, "宣告数必须等于入参长度：%r" % line
        assert _advert_names(line) == tool_names_of(defs), "名字也必须同源同序"

    def test_入参变化时宣告随之变化(self):
        """同一函数对不同下发集给出不同数字 ⇒ 它没有在做"另算一遍"的统计。"""
        assert _advert_count(render_tool_advert_line(_mk_defs(2))) == 2
        assert _advert_count(render_tool_advert_line(_mk_defs(7))) == 7

    def test_空下发集给出0并保留根因标记(self):
        line = render_tool_advert_line([])
        assert line == TOOL_ADVERT_EMPTY_LINE
        assert "0 个" in line, "空集必须如实说 0 个：%r" % line
        # 【不易】0 个也必须带 `【工具】`：否则守卫会判定"提示词本来干净"而放行，
        # 而这句话本身就是"我有工具"式的宣告（对照实验证实的触发源形态）。
        assert hit_proven_marker(line) is True
        assert prompt_advertises_tools(line) is True

    @pytest.mark.parametrize("n", [0, 1, 26])
    def test_渲染格式仍被根因标记覆盖(self, n):
        """渲染格式若改版而不带 `【工具】`，守卫会失明（DSML 根因复发）。"""
        line = render_tool_advert_line(_mk_defs(n))
        assert hit_proven_marker(line) is True
        assert prompt_advertises_tools(line) is True

    def test_畸形入参不抛异常(self):
        """提示词渲染在对话主链路上，异常会击穿整轮对话。"""
        for bad in (None, [], [None], [{}], [{"function": None}], ["not-a-dict"]):
            assert render_tool_advert_line(bad) == TOOL_ADVERT_EMPTY_LINE


def test_隔离夹具挡住外来登记_否则前置条件必失配():
    """**非空转自证**：证明隔离夹具是"有牙齿"的（不是"加了夹具，问题自己好了"）

    做法：在本用例内**故意**模拟一次外来登记（正是别处文件泄漏的形状），
    观察它是否真的会改变下发集：
      · 隔离生效时（用例开始）注册表为空 ⇒ 下发集为空；
      · 一旦多出一个外来工具 ⇒ 它**自己**就进了下发集
        ⇒ 上面那两条"前置条件"（下发集 == 本文件登记的 3 个工具）必然失配。
    用例结束由 `isolated_tool_registry` 把这个外来条目**逐条还原**掉。
    """
    from agent import tools as _tools

    assert dict(_tools._registry) == {}, "隔离夹具没生效：注册表里还有外来条目"
    assert tool_names_of(resolve_dispatch_tool_defs(None)) == []

    _tools.register(
        "foreign_leaked_tool", "模拟别处泄漏进来的真实工具",
        schema={"type": "object", "properties": {}, "additionalProperties": True},
        handler=(lambda **kw: None), source="some_other_test_file")

    leaked = resolve_dispatch_tool_defs(None)
    assert tool_names_of(leaked) == ["foreign_leaked_tool"], (
        "外来登记没有进入下发集 ⇒ 本用例没测到东西（也无法解释那 2 条红）")
    assert tool_names_of(leaked) != [] and not set(tool_names_of(leaked)) & set(
        ["b1_alpha", "b1_beta", "b1_gamma"]), (
        "外来工具没有把本文件的工具挤出/或前置条件仍成立 ⇒ 隔离并非必要")


# ══════════════════════════════════════════════════════════════════════
#  ② 不变量本体：宣告的工具数 == 实际下发的 tool_defs 长度
# ══════════════════════════════════════════════════════════════════════

class TestAdvertEqualsDispatched:
    def test_真实注册表上宣告数与下发集长度一致(self, registered_tools):
        defs = resolve_dispatch_tool_defs(None)
        assert tool_names_of(defs) == registered_tools, (
            "前置条件：注册的测试工具必须真的出现在下发集里（实际 %r）" % tool_names_of(defs))

        status = _status_text()
        assert _advert_count(status) == len(defs), (
            "宣告 %r 个，实际下发 %d 个 —— 口径又分裂了" % (_advert_count(status), len(defs)))
        assert _advert_names(status) == tool_names_of(defs)

    def test_宣告数随白名单收窄_不等于注册表全量(self, registered_tools):
        """**本卡的核心断言**：宣告值必须跟随"当轮下发集"，而不是注册表条数。

        修复前的写法是 `list_tools()` 的条数（生产上注册表全量 vs 实际下发
        主线白名单，两个数不同），所以这里刻意让两者**不相等**再断言。
        """
        from agent.digital_life_persona import DigitalLifePersonaMixin

        registry_wide = len(resolve_dispatch_tool_defs(None))
        assert registry_wide == len(registered_tools)

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

            def _get_enabled_tools_whitelist(self):
                return ["b1_alpha"]

        narrowed = resolve_dispatch_tool_defs(["b1_alpha"])
        assert len(narrowed) == 1 < registry_wide, "前置条件：收窄必须真的少给工具"

        status = _P()._build_tool_status_text()
        assert _advert_count(status) == len(narrowed) == 1
        assert _advert_names(status) == ["b1_alpha"]
        assert _advert_count(status) != registry_wide, (
            "宣告了注册表全量（%d）而只下发 %d 个 —— 正是审计 E6 的口径分裂" % (
                registry_wide, len(narrowed)))

    def test_提示词里宣告数与下发数一致(self, registered_tools):
        """端到端（到**系统提示词文本**为止）：模板渲染后数字仍然对得上。"""
        defs = resolve_dispatch_tool_defs(None)
        status = _status_text()
        prompt = _real_prompt(status)
        assert _advert_count(prompt) == len(defs)

    def test_显式传入tool_defs时按它渲染且不污染缓存(self, registered_tools):
        """调用方拿到了收窄后的下发集 ⇒ 应能直接把那一份传进来（同源优先）。"""
        from agent.digital_life_persona import DigitalLifePersonaMixin

        class _P(DigitalLifePersonaMixin):
            def __init__(self):
                self._cached_tool_status = None
                self._cached_skill_instructions = None
                self._loaded_skill_ids = []

        p = _P()
        explicit = _mk_defs(2, prefix="b1_explicit")
        assert _advert_count(p._build_tool_status_text(tool_defs=explicit)) == 2
        # 显式口径不得污染单槽位缓存：默认分支仍应回到"真实下发集"
        assert _advert_count(p._build_tool_status_text()) == len(
            resolve_dispatch_tool_defs(None))


# ══════════════════════════════════════════════════════════════════════
#  ③ 下发 0 个（含加载失败）时，提示词必须被就地中和
# ══════════════════════════════════════════════════════════════════════

class TestZeroToolsIsNeutralized:
    def test_零工具提示词被就地中和(self, caplog):
        import logging

        prompt = _real_prompt(render_tool_advert_line([]))
        assert prompt_advertises_tools(prompt) is True, "前置条件：确实在宣传"
        with caplog.at_level(logging.WARNING):
            aligned, _ = align_system_prompt_with_tools(
                prompt, False, site="unit.b1.zero_tools", tools_count=0)
        assert assert_invariant(aligned, False) is True
        assert "【工具】" not in aligned
        assert NEUTRAL_FOOTER in aligned
        assert any(EVENT_TOOLS_PROMPT_MISMATCH in r.getMessage() for r in caplog.records)

    def test_工具集加载失败时提示词同样被中和(self, monkeypatch, caplog):
        """加载失败 ⇒ `_build_tool_status_text` 落到 `【工具】状态未知` 分支。

        这条分支同样是"宣告了工具"的文本，必须与"下发 0 个"一样被守卫接住 ——
        否则失败路径就成了 DSML 根因的复现条件（审计点名过的那条线上缺陷）。
        """
        import logging

        from agent import tools_prompt_guard as guard

        def _boom(*_a, **_k):
            raise RuntimeError("模拟工具集加载失败")

        monkeypatch.setattr(guard, "resolve_dispatch_tool_defs", _boom, raising=True)
        status = _status_text()
        assert "【工具】状态未知" in status

        prompt = _real_prompt(status)
        with caplog.at_level(logging.WARNING):
            aligned, _ = align_system_prompt_with_tools(
                prompt, False, site="unit.b1.load_failed", tools_count=0)
        assert assert_invariant(aligned, False) is True
        assert "【工具】" not in aligned
        assert NEUTRAL_FOOTER in aligned

    def test_有工具时不改写提示词(self):
        """反方向：有下发时守卫**只判定不改写**（既有契约，B1 不改动）。"""
        line = render_tool_advert_line(_mk_defs(3))
        out, _ = align_system_prompt_with_tools(line, True, tools_count=3)
        assert out == line


# ══════════════════════════════════════════════════════════════════════
#  ④ 口径唯一：生产文件里不再有硬编码/二次统计的工具数
# ══════════════════════════════════════════════════════════════════════

class TestNoIndependentToolCount:
    #: 本卡点名的三处（渲染侧 / 守卫侧 / 工作台日志侧）
    FILES = ("agent/digital_life_persona.py",
             "agent/tools_prompt_guard.py",
             "plugins/chat.py")

    @staticmethod
    def _src(rel):
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()

    def test_不再有共N个形态的硬编码工具数(self):
        """「共 N 个」是**独立统计**出来的宣告值的写法，三处都必须绝迹。"""
        bad = []
        for rel in self.FILES:
            for i, ln in enumerate(self._src(rel).splitlines(), 1):
                if re.search(r"共\s*\d+\s*个", ln):
                    bad.append("%s:%d: %s" % (rel, i, ln.strip()))
        assert not bad, "仍有硬编码工具数：\n" + "\n".join(bad)

    def test_工具侧不再有旧的清单文案(self):
        src = self._src("agent/digital_life_persona.py")
        assert "【工具】全部已启用" not in src
        assert "【工具】已启用" not in src, "工具侧旧文案（独立统计）必须已移除"

    def test_工具状态渲染只走唯一产出点(self):
        """用 AST 看**真实调用点**，不看注释/文档字符串（注释里会写到旧实现）。"""
        import ast
        import inspect
        import textwrap

        from agent.digital_life_persona import DigitalLifePersonaMixin

        tree = ast.parse(textwrap.dedent(
            inspect.getsource(DigitalLifePersonaMixin._build_tool_status_text)))
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    called.add(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    called.add(node.func.attr)
        # 别名也认（渲染侧用的是 `render_tool_advert_line as _render_advert`），
        # 所以同时校验 import 语句——避免"换个名字继续自己算"绕过这条断言。
        assert called & {"render_tool_advert_line", "_render_advert"}, \
            "宣告行必须由守卫模块的唯一产出点渲染"
        assert called & {"resolve_dispatch_tool_defs", "_resolve_defs"}, \
            "默认分支必须与出网口同源解析"
        assert "list_tools" not in called, "工具数不得再从注册表另数一遍（口径分裂来源）"

        src = inspect.getsource(DigitalLifePersonaMixin._build_tool_status_text)
        src = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
        assert "from agent.tools_prompt_guard import" in src


# ══════════════════════════════════════════════════════════════════════
#  ⑤ token 计量：实测，不是"字符÷3"
# ══════════════════════════════════════════════════════════════════════

# 【2026-09-27 CI-3：把「静默跳过」改成「响亮失败」】
#   原实现是 `pytest.importorskip("tiktoken")`。缺依赖时实测 **20 passed, 2 skipped,
#   退出码 0** —— 报告写「所有测试通过」，而本组守的正是"token 计量是实测 BPE、
#   不是字符÷3 换算"这条口径，**一次都没跑**（假绿：护栏不在，却看不出不在）。
#   为什么这里**不该**用 importorskip：本仓把 tiktoken 声明为**必装依赖**
#   （`pyproject.toml` `[project].dependencies`：`tiktoken>=0.7.0,<1.0.0`，CI 由
#   `pip install -e .` 安装；`requirements.txt:361` 亦钉 0.13.0）—— 它不是 chromadb /
#   GPU 那一类"环境可能没有"的可选件 ⇒ 缺它就说明**环境装坏了**，应当红并给出安装指引，
#   而不是让两条最硬的断言消失。`importorskip` 只适合"缺了就无意义"的可选依赖。
def _require_tiktoken():
    """取 tiktoken；**缺失即失败**（并打印安装指引），绝不静默降级为 skip"""
    try:
        import tiktoken
    except ImportError as exc:  # pragma: no cover - 只在环境缺依赖时走到
        pytest.fail(
            "缺少 tiktoken：本文件 ⑤ 组的两条守卫（token 计量 == tiktoken cl100k_base "
            "实测）无法执行。tiktoken 是 pyproject.toml 声明的**必装依赖**，缺它属于"
            "环境缺陷，按「响亮失败」处理 —— 静默 skip 会让「字符÷3」回归重新变成假绿。"
            "\n  安装：python -m pip install tiktoken"
            "\n  （CI：pip install -e . 会带入；本地最少环境请显式安装）"
            "\n  原始错误：%s" % exc,
            pytrace=False,
        )
    return tiktoken


class TestTokenCountIsMeasured:
    def test_实测口径等于tiktoken_cl100k(self):
        tiktoken = _require_tiktoken()
        defs = _mk_defs(5)
        enc = tiktoken.get_encoding("cl100k_base")
        expected = len(enc.encode(json.dumps(defs, ensure_ascii=False)))
        assert count_tool_defs_tokens(defs) == expected

    def test_中文描述下不等于字符除三(self):
        """钉住"为什么不能再用字符换算"：中文上真实 BPE 远高于 字符÷3。"""
        tiktoken = _require_tiktoken()
        defs = [{"type": "function", "function": {
            "name": "b1_zh",
            "description": "把当前任务的计划清单写下来，遇到异常时主动建议缓解方案并记录审计留痕",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": True}}}]
        n = count_tool_defs_tokens(defs)
        naive = len(json.dumps(defs, ensure_ascii=False)) // 3
        assert n > naive, "实测 %d 必须高于字符÷3 口径 %d（否则口径钉错了）" % (n, naive)
