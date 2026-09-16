"""``delegate`` 工具（子代理委派）单元测试

覆盖（对齐任务书交付物 3）：
- 工具已注册，schema 覆盖八要素，``required`` 与真实校验器口径一致
- 缺要素被拒（点名序号 + ``E_DELEGATION_INCOMPLETE``），且**不触达执行器**
- 目标过短（< ``MIN_GOAL_CHARS``）被拒
- 契约⑧回调地址的合法/非法形态（按 ``delegation._check_callback`` 口径）
- 成功路径：``ExecutionOutcome`` → 工具结果 dict 的字段映射与结果截断
- 未配置执行通道（无 LLM 且 ``CP_SUBAGENT_AGENT_CLI`` 空）→ 清晰错误、不抛异常
- 执行器抛异常 / 生命周期管理器不可用 → 收口为 ``ok=False``，绝不外抛

【不易】不启动子进程、不调用任何真实 LLM：生命周期管理器与 LLM 全部为 mock；
        执行器（``manager.delegate``）不被真实调用。
【简易】注册后即注销，全局工具注册表不留残留。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent import tools as _tools
from agent.subagent.delegation import (
    E_DELEGATION_INCOMPLETE,
    EIGHT_ELEMENTS,
    ELEMENT_ARTIFACT_FORMAT,
    ELEMENT_BUDGET_TOKENS,
    ELEMENT_CALLBACK_URL,
    ELEMENT_CONSTRAINTS,
    ELEMENT_GOAL,
    ELEMENT_LABELS,
    ELEMENT_PRIOR_ARTIFACTS,
    ELEMENT_PROHIBITIONS,
    ELEMENT_TIMEOUT_SECONDS,
    MIN_GOAL_CHARS,
    DelegationContext,
    element_problems,
)
from agent.subagent.executor import ExecutionOutcome
from agent.tools import subagent_tools

#: LLM 是否显式传 None 的哨兵（None 本身是一个有语义的取值）
_UNSET = object()

#: 一份合法的八要素入参（用例在此基础上做覆盖）
VALID_KWARGS = {
    ELEMENT_GOAL: "把 docs/zh 下的 12 篇设计稿抽取为可复现步骤序列",
    ELEMENT_CONSTRAINTS: ["只读仓库，不得修改任何文件"],
    ELEMENT_PRIOR_ARTIFACTS: [],
    ELEMENT_PROHIBITIONS: [],
    ELEMENT_ARTIFACT_FORMAT: "JSON Lines：每行 {name, steps[]}",
    ELEMENT_BUDGET_TOKENS: 20000,
    ELEMENT_TIMEOUT_SECONDS: 60,
    ELEMENT_CALLBACK_URL: "internal://pipeline/stage2",
}


def _kwargs(**overrides) -> dict:
    """合法入参 + 覆盖项"""
    data = dict(VALID_KWARGS)
    data.update(overrides)
    return data


def _make_dl(mgr, llm) -> MagicMock:
    """桩 dl：只暴露工具需要的两个既有属性（既有 LLM / 既有生命周期管理器）"""
    dl = MagicMock()
    dl._subagent_mgr = mgr
    dl._llm = llm
    return dl


@pytest.fixture(autouse=True)
def _isolate_delegate_registration():
    """每个用例结束后注销 delegate，避免污染全局工具注册表"""
    yield
    _tools.unregister("delegate")


@pytest.fixture()
def make_tool():
    """返回 (handler, manager_mock, llm_mock) 的工厂

    ``manager.delegate`` 是唯一的执行入口——**永远**是 mock，不会有真执行。
    """
    def _make(llm=_UNSET, mgr=None, outcome=None):
        manager = mgr if mgr is not None else MagicMock()
        if outcome is not None:
            manager.delegate.return_value = outcome
        llm_obj = MagicMock() if llm is _UNSET else llm
        subagent_tools.register_all(_make_dl(manager, llm_obj))
        return _tools._registry["delegate"]["handler"], manager, llm_obj
    return _make


def _outcome(**overrides) -> ExecutionOutcome:
    """假 ExecutionOutcome（真实 dataclass，字段映射才是真校验）"""
    data = {
        "delegation_id": "dlg-outcome-1",
        "ok": True,
        "tier": "jsonl",
        "output_text": "",
        "payload": {"summary": "已完成"},
        "artifacts": ({"path": "out/a.jsonl", "size": 12},),
        "trace_id": "tr-sub-1",
        "duration_ms": 123.456,
    }
    data.update(overrides)
    return ExecutionOutcome(**data)


def _schema() -> dict:
    return _tools._registry["delegate"]["schema"]


# ════════════════════════════════════════════════════════════
#  1. 注册与 schema
# ════════════════════════════════════════════════════════════


class TestRegistrationAndSchema:
    def test_tool_registered(self, make_tool):
        handler, _, _ = make_tool()
        assert callable(handler)
        assert _tools._registry["delegate"]["schema"]["type"] == "object"

    def test_schema_covers_all_eight_elements_in_order(self, make_tool):
        make_tool()
        props = list(_schema()["properties"].keys())
        assert props == list(EIGHT_ELEMENTS), f"八要素键名/顺序不符: {props}"

    def test_required_derived_from_real_validator(self, make_tool):
        """required 必须由 element_problems 推导：八个要素缺任一都被判不合格"""
        make_tool()
        problems = element_problems({})
        assert set(problems) == set(EIGHT_ELEMENTS), "校验器认为存在非必填要素"
        assert set(_schema()["required"]) == set(problems)
        assert set(_schema()["required"]) == set(EIGHT_ELEMENTS)

    def test_each_property_description_carries_element_label(self, make_tool):
        make_tool()
        props = _schema()["properties"]
        for name in EIGHT_ELEMENTS:
            label = ELEMENT_LABELS[name]
            assert label in props[name]["description"], (
                f"{name} 的 description 未带序号标签 {label}")

    @pytest.mark.parametrize("name", [ELEMENT_CONSTRAINTS, ELEMENT_PRIOR_ARTIFACTS,
                                      ELEMENT_PROHIBITIONS])
    def test_list_elements_are_arrays(self, make_tool, name):
        make_tool()
        prop = _schema()["properties"][name]
        assert prop["type"] == "array"
        assert prop["items"] == {"type": "string"}

    @pytest.mark.parametrize("name", [ELEMENT_BUDGET_TOKENS, ELEMENT_TIMEOUT_SECONDS])
    def test_numeric_elements_are_integer(self, make_tool, name):
        make_tool()
        assert _schema()["properties"][name]["type"] == "integer"

    @pytest.mark.parametrize("name", [ELEMENT_GOAL, ELEMENT_ARTIFACT_FORMAT,
                                      ELEMENT_CALLBACK_URL])
    def test_string_elements_are_string(self, make_tool, name):
        make_tool()
        assert _schema()["properties"][name]["type"] == "string"

    def test_goal_description_mentions_min_goal_chars(self, make_tool):
        make_tool()
        desc = _schema()["properties"][ELEMENT_GOAL]["description"]
        assert str(MIN_GOAL_CHARS) in desc, "①目标 未声明最小字符数"

    def test_tool_description_has_retrieval_keywords(self, make_tool):
        make_tool()
        desc = _tools._registry["delegate"]["description"]
        for kw in ("delegate", "subagent", "dispatch task"):
            assert kw in desc, f"工具 description 缺少检索关键词 {kw}"
        assert "八要素" in desc and "缺一即拒" in desc


# ════════════════════════════════════════════════════════════
#  2. 契约拒绝（校验先于执行，零副作用）
# ════════════════════════════════════════════════════════════


class TestContractRejection:
    def test_only_goal_rejected_with_named_missing_elements(self, make_tool):
        handler, manager, _ = make_tool()
        goal = VALID_KWARGS[ELEMENT_GOAL]
        result = handler(**{ELEMENT_GOAL: goal})

        assert result["ok"] is False
        assert result["error_code"] == E_DELEGATION_INCOMPLETE
        # 缺失清单与真实校验器口径一致（逐个要素核对）
        assert set(result["missing"]) == set(element_problems({ELEMENT_GOAL: goal}))
        # 错误文案点名缺哪几项并带序号
        for name in EIGHT_ELEMENTS:
            if name != ELEMENT_GOAL:
                assert ELEMENT_LABELS[name] in result["error"], (
                    f"错误文案未点名 {ELEMENT_LABELS[name]}")
        assert "①目标" not in result["error"]
        manager.delegate.assert_not_called()

    def test_blank_goal_rejected(self, make_tool):
        handler, manager, _ = make_tool()
        result = handler(**_kwargs(**{ELEMENT_GOAL: "   "}))
        assert result["ok"] is False
        assert result["error_code"] == E_DELEGATION_INCOMPLETE
        assert "①目标" in result["error"]
        manager.delegate.assert_not_called()

    def test_short_goal_rejected(self, make_tool):
        handler, manager, _ = make_tool()
        short_goal = "写" * (MIN_GOAL_CHARS - 1)
        result = handler(**_kwargs(**{ELEMENT_GOAL: short_goal}))

        assert result["ok"] is False
        assert result["error_code"] == E_DELEGATION_INCOMPLETE
        assert "①目标" in result["error"]
        assert result["missing"][ELEMENT_GOAL] == element_problems(
            {ELEMENT_GOAL: short_goal})[ELEMENT_GOAL]
        manager.delegate.assert_not_called()

    def test_goal_at_min_chars_accepted(self, make_tool):
        handler, manager, _ = make_tool(outcome=_outcome())
        result = handler(**_kwargs(**{ELEMENT_GOAL: "写" * MIN_GOAL_CHARS}))
        assert result["ok"] is True
        assert manager.delegate.called

    def test_empty_constraints_rejected(self, make_tool):
        """②约束为空列表 → 拒绝（§3.9：无约束的委派等同于未声明边界）"""
        handler, manager, _ = make_tool()
        result = handler(**_kwargs(**{ELEMENT_CONSTRAINTS: []}))
        assert result["ok"] is False
        assert ELEMENT_CONSTRAINTS in result["missing"]
        assert "②约束" in result["error"]
        manager.delegate.assert_not_called()

    def test_missing_prior_artifacts_and_prohibitions_rejected(self, make_tool):
        """③④「未声明」与「声明为空」是两件事：不传该字段即拒绝"""
        handler, manager, _ = make_tool()
        data = _kwargs()
        data.pop(ELEMENT_PRIOR_ARTIFACTS)
        data.pop(ELEMENT_PROHIBITIONS)
        result = handler(**data)
        assert result["ok"] is False
        assert set(result["missing"]) == {ELEMENT_PRIOR_ARTIFACTS, ELEMENT_PROHIBITIONS}
        assert "③已有成果" in result["error"] and "④禁止事项" in result["error"]
        manager.delegate.assert_not_called()

    @pytest.mark.parametrize("budget", [None, 0, -5, "20000", 1.5, True])
    def test_bad_budget_rejected(self, make_tool, budget):
        handler, manager, _ = make_tool()
        result = handler(**_kwargs(**{ELEMENT_BUDGET_TOKENS: budget}))
        assert result["ok"] is False
        assert ELEMENT_BUDGET_TOKENS in result["missing"]
        assert "⑥预算令牌" in result["error"]
        manager.delegate.assert_not_called()

    @pytest.mark.parametrize("timeout", [None, 0, -1, "60", True])
    def test_bad_timeout_rejected(self, make_tool, timeout):
        handler, manager, _ = make_tool()
        result = handler(**_kwargs(**{ELEMENT_TIMEOUT_SECONDS: timeout}))
        assert result["ok"] is False
        assert ELEMENT_TIMEOUT_SECONDS in result["missing"]
        assert "⑦超时" in result["error"]
        manager.delegate.assert_not_called()

    def test_no_element_silently_defaulted(self, make_tool):
        """空入参 → 八项全部缺失，逐项点名（不得有任何默认补齐）"""
        handler, manager, _ = make_tool()
        result = handler()
        assert result["ok"] is False
        assert set(result["missing"]) == set(EIGHT_ELEMENTS)
        for name in EIGHT_ELEMENTS:
            assert ELEMENT_LABELS[name] in result["error"]
        manager.delegate.assert_not_called()


# ════════════════════════════════════════════════════════════
#  3. 契约⑧回调地址的合法形态（按 _check_callback 口径）
# ════════════════════════════════════════════════════════════


class TestCallbackUrlContract:
    @pytest.mark.parametrize("callback_url", [
        "internal://pipeline/stage2",       # 云枢内部投递
        "https://example.com/hooks/delegate",
        "cb-1",                             # 契约只要求「非空字符串」
        "  internal://x  ",                 # 前后空白由 handler 归一
    ])
    def test_legal_forms_accepted(self, make_tool, callback_url):
        handler, manager, _ = make_tool(outcome=_outcome())
        result = handler(**_kwargs(**{ELEMENT_CALLBACK_URL: callback_url}))
        assert result["ok"] is True, result
        assert manager.delegate.called

    @pytest.mark.parametrize("callback_url", ["", "   ", None])
    def test_undeclared_forms_rejected(self, make_tool, callback_url):
        handler, manager, _ = make_tool()
        result = handler(**_kwargs(**{ELEMENT_CALLBACK_URL: callback_url}))
        assert result["ok"] is False
        assert result["error_code"] == E_DELEGATION_INCOMPLETE
        assert ELEMENT_CALLBACK_URL in result["missing"]
        assert "⑧回调地址" in result["error"]
        manager.delegate.assert_not_called()


# ════════════════════════════════════════════════════════════
#  4. 成功路径：ExecutionOutcome → 工具结果 dict
# ════════════════════════════════════════════════════════════


class TestSuccessPath:
    def test_outcome_fields_mapped(self, make_tool):
        handler, manager, llm = make_tool(outcome=_outcome())
        result = handler(**_kwargs())

        assert result["ok"] is True
        assert result["delegation_id"] == "dlg-outcome-1"
        assert result["tier"] == "jsonl"
        assert result["duration_ms"] == pytest.approx(123.46)
        assert result["trace_id"] == "tr-sub-1"
        assert result["result"] == "已完成"
        assert result["artifact_count"] == 1
        assert result["artifacts"] == [{"path": "out/a.jsonl", "size": 12}]
        assert "error" not in result and "error_code" not in result
        assert manager.delegate.called

    def test_delegate_called_with_config_ctx_llm_destroy_after(self, make_tool):
        handler, manager, llm = make_tool(outcome=_outcome())
        handler(**_kwargs())

        args, kwargs = manager.delegate.call_args
        config, ctx = args
        assert config.name.startswith("delegate-")
        assert isinstance(config.model_id, str) and config.model_id
        assert kwargs["llm"] is llm
        assert kwargs["destroy_after"] is True
        # 默认授予**只读**子集（tools 与 authorized_capabilities 同源，二者缺一即为空交集
        # ⇒ 子代理只要声称调用工具就整次失败）；写入与 Shell 刻意不在默认内。
        assert set(kwargs["tools"]) == set(subagent_tools._DEFAULT_SUBAGENT_TOOLS)
        assert set(kwargs["authorized_capabilities"]) == set(subagent_tools._DEFAULT_SUBAGENT_TOOLS)
        for _forbidden in ("write_file", "edit", "shell_execute"):
            assert _forbidden not in kwargs["tools"]

        assert isinstance(ctx, DelegationContext)
        assert ctx.validate() == ()
        assert ctx.goal == VALID_KWARGS[ELEMENT_GOAL]
        assert ctx.constraints == tuple(VALID_KWARGS[ELEMENT_CONSTRAINTS])
        assert ctx.prior_artifacts == ()
        assert ctx.prohibitions == ()
        assert ctx.artifact_format == VALID_KWARGS[ELEMENT_ARTIFACT_FORMAT]
        assert ctx.budget_tokens == 20000
        assert ctx.timeout_seconds == 60
        assert ctx.callback_url == "internal://pipeline/stage2"
        assert ctx.delegation_id.startswith("dlg-")
        assert ctx.delegate_actor == f"sub_agent:{ctx.delegation_id}"

    def test_string_elements_are_stripped(self, make_tool):
        handler, manager, _ = make_tool(outcome=_outcome())
        handler(**_kwargs(**{
            ELEMENT_GOAL: f"  {VALID_KWARGS[ELEMENT_GOAL]}  ",
            ELEMENT_ARTIFACT_FORMAT: "  JSONL  ",
        }))
        _, ctx = manager.delegate.call_args.args
        assert ctx.goal == VALID_KWARGS[ELEMENT_GOAL]
        assert ctx.artifact_format == "JSONL"

    def test_long_summary_truncated_at_3000_chars(self, make_tool):
        long_text = "长" * 4000
        handler, _, _ = make_tool(outcome=_outcome(payload={"summary": long_text}))
        result = handler(**_kwargs())

        summary = result["result"]
        assert summary[:3000] == long_text[:3000]
        assert summary.endswith("...（结果过长，已截断至 3000 字符）")
        assert len(summary) < len(long_text)

    def test_output_text_used_when_no_structured_summary(self, make_tool):
        handler, _, _ = make_tool(outcome=_outcome(payload={}, output_text="裸文本结论"))
        result = handler(**_kwargs())
        assert "裸文本结论" in result["result"]
        assert "未经云枢复核" in result["result"]      # §5.7 机制 1：外来文本需标注

    def test_failed_outcome_mapped_with_error_fields(self, make_tool):
        handler, _, _ = make_tool(outcome=_outcome(
            ok=False, tier="template", payload={}, output_text="",
            error_code="E_DELEGATION_FAILED", error="上游格式不合格",
            sub_reason="upstream_format", duration_ms=8.0))

        result = handler(**_kwargs())
        assert result["ok"] is False
        assert result["error_code"] == "E_DELEGATION_FAILED"
        assert result["error"] == "上游格式不合格"
        assert result["sub_reason"] == "upstream_format"
        assert result["delegation_id"] == "dlg-outcome-1"
        assert result["tier"] == "template"
        assert result["duration_ms"] == pytest.approx(8.0)


# ════════════════════════════════════════════════════════════
#  5. 执行通道未配置 / 管理器不可用 / 执行异常
# ════════════════════════════════════════════════════════════


class TestUnavailablePaths:
    def test_no_llm_and_no_cli_returns_clear_error(self, make_tool, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        handler, manager, _ = make_tool(llm=None)

        result = handler(**_kwargs())          # 不得抛异常

        assert result["ok"] is False
        assert "未配置执行通道" in result["error"]
        assert "既无 LLM 也无外部 agent CLI" in result["error"]
        assert "CP_SUBAGENT_AGENT_CLI" in result["error"]
        manager.delegate.assert_not_called()   # 不跑注定失败的空执行

    def test_cli_only_channel_allowed(self, make_tool, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_AGENT_CLI", "my-agent-cli")
        handler, manager, _ = make_tool(llm=None, outcome=_outcome())
        result = handler(**_kwargs())
        assert result["ok"] is True
        assert manager.delegate.call_args.kwargs["llm"] is None

    def test_llm_only_channel_allowed(self, make_tool, monkeypatch):
        monkeypatch.delenv("CP_SUBAGENT_AGENT_CLI", raising=False)
        handler, manager, llm = make_tool(outcome=_outcome())
        result = handler(**_kwargs())
        assert result["ok"] is True
        assert manager.delegate.call_args.kwargs["llm"] is llm

    def test_manager_missing_returns_clear_error(self):
        """dl._subagent_mgr 为空（subagent.enabled=False / 未初始化）→ 清晰错误"""
        subagent_tools.register_all(_make_dl(None, MagicMock()))
        handler = _tools._registry["delegate"]["handler"]

        result = handler(**_kwargs())          # 不得抛异常

        assert result["ok"] is False
        assert "子代理生命周期管理器不可用" in result["error"]

    def test_manager_without_delegate_returns_clear_error(self, make_tool):
        class _NoDelegate:
            pass

        handler, _, _ = make_tool(mgr=_NoDelegate())
        result = handler(**_kwargs())
        assert result["ok"] is False
        assert "子代理生命周期管理器不可用" in result["error"]
        assert "delegate" in result["error"]

    def test_executor_exception_swallowed(self, make_tool):
        handler, manager, _ = make_tool()
        manager.delegate.side_effect = RuntimeError("执行器炸了")

        result = handler(**_kwargs())          # 不得抛异常

        assert result["ok"] is False
        assert result["error"].startswith("委派执行异常: ")
        assert "执行器炸了" in result["error"]

    def test_invalid_context_exception_swallowed(self, make_tool, monkeypatch):
        """任何异常（含构造期异常）都收口为 ok=False"""
        monkeypatch.setattr(subagent_tools, "_collect_elements",
                            lambda kwargs: (_ for _ in ()).throw(ValueError("bad")))
        handler, manager, _ = make_tool()
        result = handler(**_kwargs())
        assert result["ok"] is False
        assert "委派执行异常" in result["error"]
        manager.delegate.assert_not_called()


# ════════════════════════════════════════════════════════════
#  委派授予的工具子集（最小权限默认 + 环境变量覆盖）
# ════════════════════════════════════════════════════════════


class TestGrantedToolSubset:
    """委派默认授予只读子集：不授会让裁剪集为空 ⇒ 子代理一报告工具调用就整次失败

    背景：``SubAgentToolset.build`` 取「申请 ∩ 授权 − 矩阵拒绝」，两者皆空则裁剪集为空；
    ``DelegationExecutor`` 发现子代理声称调用了裁剪集外的工具即判
    ``E_TOOL_NOT_AUTHORIZED`` 让整次委派失败（``agent/subagent/executor.py:555-562``）。
    """

    def test_默认严格等于内置只读子集(self):
        assert subagent_tools._default_subagent_tools() == subagent_tools._DEFAULT_SUBAGENT_TOOLS

    def test_默认子集全是只读工具且不含写入与_Shell(self):
        granted = set(subagent_tools._DEFAULT_SUBAGENT_TOOLS)
        assert granted == {"read_file", "search_files", "list_directory",
                           "get_file_info", "grep"}
        assert not granted & {"write_file", "edit", "shell_execute", "run_program",
                             "software_install", "delegate"}

    def test_默认子集里的工具名都是真实注册名(self):
        """子集必须由真实工具名构成，否则交集为空、闸门反而永远拒绝"""
        import re
        from pathlib import Path
        defs = Path(__file__).resolve().parents[2] / "data" / "tool_definitions"
        real = set()
        for path in defs.glob("*.yaml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                matched = re.match(r"^name:\s*(\S+)\s*$", line)
                if matched:
                    real.add(matched.group(1))
                    break
        assert real, "未能读到工具定义口径"
        missing = set(subagent_tools._DEFAULT_SUBAGENT_TOOLS) - real
        assert not missing, f"默认授予的子集含未注册工具名: {sorted(missing)}"

    def test_环境变量可覆盖子集(self, make_tool, monkeypatch):
        monkeypatch.setenv("CP_SUBAGENT_DEFAULT_TOOLS", "read_file , web_search")
        handler, manager, _ = make_tool(outcome=_outcome())
        handler(**_kwargs())
        kwargs = manager.delegate.call_args.kwargs
        assert set(kwargs["tools"]) == {"read_file", "web_search"}
        assert set(kwargs["authorized_capabilities"]) == {"read_file", "web_search"}

    def test_环境变量空串表示不授予任何工具(self, make_tool, monkeypatch):
        """显式选择"纯推理委派"的逃生门"""
        monkeypatch.setenv("CP_SUBAGENT_DEFAULT_TOOLS", "")
        handler, manager, _ = make_tool(outcome=_outcome())
        handler(**_kwargs())
        kwargs = manager.delegate.call_args.kwargs
        assert tuple(kwargs["tools"]) == ()
        assert tuple(kwargs["authorized_capabilities"]) == ()
