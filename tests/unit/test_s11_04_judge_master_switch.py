"""TASK-S11-04 真实 judge **总开关**与**预算护栏**覆盖全部入选链 单测

背景（S10-02 遗留 L3）：`CP_DIGESTION_JUDGE_ENABLED` 此前**只**被
`judge_runtime.build_judge_runtime` 那条链接读。而 `ShadowRunner` 在开关关闭时根本不建
运行时，于是落回 `shadow.resolve_judge("auto")` —— 那条链只看"通道能否构造出来"：

  · `CP_DIGESTION_JUDGE_PROVIDER` / `CP_DIGESTION_JUDGE_MODEL` 在设置注册表里是 `_a`
    （可直接切的普通键），配上它们 + 适配器看得见凭证 ⇒ `auto` **直接选中真实通道**；
  · 该通道 `JudgeGuard._precheck is None` 且 `_on_call is None`
    ⇒ **既无每日预算前置拦截，也无 `utc.record_cost(source="judge")` 记账**。

本文件把修好之后的语义钉死（**四条性质**）：

1. **关着一定不花钱**：总开关关闭时，`auto` 与显式 `llm` 都**不得**选中由适配器自建的
   真实通道，且**一次传输层调用都不发**；
2. **已安全的那条路逐字不变**：通道本来就不可用时，标签仍是接入前的
   ``deterministic_local(llm_unavailable)``（不动 S10-02 已锁的口径）；
3. **入选即受护栏约束**：选中真实通道时随附预算接线（前置拦截 + UTC 记账 + 精确回落
   标签），超预算**入选前**就被拦下、如实标 ``deterministic_local(budget_exceeded)``；
4. **注入不被夺权**：显式 `invoke=` / `judge=` 不是"本部署发起的真实调用"，不受总开关
   约束（其成本属调用方，记了就是编造成本）。

隔离纪律：事件目录一律落 ``tmp_path``（`CP_EVENTS_DIR`），传输层用**桩适配器**
（**不发真实模型调用、不产生费用**）；本文件不读也不写仓库根 `.env`。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agent.digestion import judge_runtime as JR
from agent.digestion import shadow as SH
from agent.observability import events as events_mod
from agent.observability import utc as utc_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
SHADOW_PY = REPO_ROOT / "agent" / "digestion" / "shadow.py"
SECRET = "sk-s11-04-fake-secret-0123456789"


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """事件目录隔离 + 清掉 judge/凭证环境变量（结论不得随主工作区环境漂移）"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    for name in ("CP_DIGESTION_JUDGE_ENABLED", "CP_DIGESTION_JUDGE_PROVIDER",
                 "CP_DIGESTION_JUDGE_MODEL", "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS",
                 "CP_DIGESTION_JUDGE_THRESHOLD", "CP_DIGESTION_JUDGE_FOLLOW_FASTING",
                 "CP_DIGESTION_JUDGE_SECRET_FILE", "CP_DIGESTION_JUDGE_DOTENV",
                 "CP_DIGESTION_JUDGE", "CP_DIGESTION_SHADOW_ENABLED",
                 "CP_DIGESTION_GRAY_ENABLED", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "DEEPSEEK_API_KEY", "LLM_API_KEY", "LLM_PROVIDER", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


# ════════════════════════════════════════════════════════════
#  桩（**唯一**的"假"：网络通道；其余全走生产代码）
# ════════════════════════════════════════════════════════════


class StubAdapter:
    """桩适配器：`is_available()` 恒真、`generate()` 记录次数并返回结构化回复（不联网）"""

    def __init__(self, *, verdict: str = "equivalent", confidence: float = 0.93,
                 usage: Dict[str, int] | None = None) -> None:
        self.calls = 0
        self.prompts: List[str] = []
        self.verdict = verdict
        self.confidence = confidence
        self.usage = usage or {"prompt_tokens": 412, "completion_tokens": 24}

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        self.prompts.append(str(prompt))
        return {"content": json.dumps({"verdict": self.verdict,
                                       "confidence": self.confidence,
                                       "reason": "s11-04 桩回复"}, ensure_ascii=False),
                "usage": dict(self.usage)}


@pytest.fixture
def stub_channel(monkeypatch):
    """把适配器工厂换成桩适配器（返回对象，供断言"到底发了几次调用"）"""
    adapter = StubAdapter()
    from agent.model_router import adapters as adapters_mod

    def _create(provider: str, model_name: str, **kwargs: Any) -> Any:
        return adapter

    monkeypatch.setattr(adapters_mod.ModelAdapterFactory, "create",
                        staticmethod(_create))
    return adapter


def judge_env(tmp_path, **overrides: str) -> Dict[str, str]:
    """入选链环境（**非空 dict** ⇒ 不走 ``os.environ``，结论可复现）

    默认 ``CP_DIGESTION_JUDGE_ENABLED`` **不写** = 默认关闭；断食联动显式关掉，
    使"预算"成为唯一变量（不把 S5-03 的成本刹车混进来）。
    """
    env: Dict[str, str] = {
        SH.JUDGE_MODE_ENV: SH.JUDGE_MODE_AUTO,
        SH.JUDGE_PROVIDER_ENV: "deepseek",
        SH.JUDGE_MODEL_ENV: "deepseek-v4-flash",
        SH.JUDGE_BASE_URL_ENV: "https://api.deepseek.com/v1",
        JR.JUDGE_FOLLOW_FASTING_ENV: "false",
        JR.JUDGE_DOTENV_ENV: str(tmp_path / "absent.env"),
        SH.SHADOW_ENABLE_ENV: "false",
        SH.GRAY_ENABLE_ENV: "false",
        SH.SHADOW_DIR_ENV: str(tmp_path / "shadow"),
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def judge_costs(tmp_path) -> Dict[str, Any]:
    """读 UTC 的 judge 栏（与护栏同一个读侧入口）"""
    return utc_mod.judge_cost_cents(directory=str(tmp_path / "events"))


# ════════════════════════════════════════════════════════════
#  一、总开关：关着一定不花钱
# ════════════════════════════════════════════════════════════


class TestMasterSwitch:
    def test_available_channel_is_refused_when_switch_off(self, stub_channel, tmp_path):
        """★ 核心验收：通道**本来可用**，开关关 ⇒ 如实回落且**一次调用都不发**"""
        resolved = SH.resolve_judge("auto", env=judge_env(tmp_path))
        assert resolved.is_llm is False
        assert resolved.kind == SH.judge_fallback_kind(SH.JUDGE_REASON_DISABLED)
        assert resolved.detail["channel_was_available"] is True
        assert resolved.detail["master_switch"] == "off"
        assert stub_channel.calls == 0, "关着不得发出任何传输层调用"
        # 判定器确实换成了确定性打分器（不是只改了标签）
        assert resolved.scorer("same", "same") == 1.0

    def test_explicit_llm_mode_is_also_refused_when_switch_off(self, stub_channel, tmp_path):
        """显式 `mode=llm` 也走同一道闸（总开关是**总**开关）"""
        resolved = SH.resolve_judge("llm", env=judge_env(tmp_path))
        assert resolved.is_llm is False
        assert resolved.kind == SH.judge_fallback_kind(SH.JUDGE_REASON_DISABLED)
        assert stub_channel.calls == 0

    def test_switch_on_selects_the_channel(self, stub_channel, tmp_path):
        """反向对照（防"闸门焊死"的假绿灯）：开关开 ⇒ 仍然选真实通道"""
        resolved = SH.resolve_judge("auto", env=judge_env(
            tmp_path, **{JR.JUDGE_ENABLE_ENV: "true",
                         JR.JUDGE_BUDGET_ENV: "100"}))
        assert resolved.kind == SH.JUDGE_KIND_LLM and resolved.is_llm is True
        assert resolved.judge is not None
        assert stub_channel.calls == 0, "入选本身不得发调用（调用发生在判定时）"
        assert resolved.scorer("same", "same") == pytest.approx(0.93)
        assert stub_channel.calls == 1

    def test_unavailable_channel_keeps_the_legacy_label(self, tmp_path):
        """★ 口径边界：**通道本来就不可用**时标签逐字不变（只改"本来能花钱"的那种）

        S10-02 锁的是这条：开关关 ⇒ 少配 provider/model ⇒
        ``deterministic_local(llm_unavailable)``。本任务**不动**它。
        """
        env = judge_env(tmp_path)
        env.pop(SH.JUDGE_PROVIDER_ENV)
        env.pop(SH.JUDGE_MODEL_ENV)
        resolved = SH.resolve_judge("auto", env=env)
        assert resolved.kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert resolved.detail.get("master_switch") is None

    def test_injected_channel_is_not_gated(self, tmp_path):
        """★ 边界：显式注入**不是**本部署发起的真实调用 ⇒ 不被总开关夺权（S3-03 契约）"""
        resolved = SH.resolve_judge("llm", invoke=lambda p: '{"score": 0.99}',
                                    env={JR.JUDGE_ENABLE_ENV: "false"})
        assert resolved.kind == SH.JUDGE_KIND_LLM and resolved.is_llm is True
        assert resolved.scorer("a", "b") == 0.99
        assert resolved.guard_kwargs == {}, "注入通道不记成本（记了就是编造）"

    def test_runner_never_reaches_transport_when_switch_off(self, stub_channel, tmp_path):
        """★★ 端到端锁死：`ShadowRunner`（生产入口）在开关关时判定链全程不发调用"""
        env = judge_env(tmp_path)
        runner = SH.ShadowRunner(env=env, emit_events=False)
        assert runner.runtime is None, "开关关 ⇒ 不注入运行时（S10-02 安全底线）"
        before = judge_costs(tmp_path)
        score = runner.sandbox.judge("same text", "same text")
        after = judge_costs(tmp_path)
        assert score == 1.0
        assert runner.judge_guard.effective_kind == SH.judge_fallback_kind(
            SH.JUDGE_REASON_DISABLED)
        assert runner.judge_guard._precheck is None, "未选真实通道 ⇒ 无需护栏"
        assert runner.judge_guard._on_call is None
        assert runner.sandbox.judge_kind == runner.judge_guard.effective_kind
        assert stub_channel.calls == 0
        assert after["calls"] == before["calls"] == 0
        assert SECRET not in json.dumps(runner.judge.to_dict(), ensure_ascii=False)

    def test_switch_off_is_reported_as_disabled_not_as_failure(
            self, stub_channel, tmp_path, caplog):
        """"默认关闭"不得被报成故障：不发降级事件（与 judge_runtime 同纪律）"""
        import logging

        with caplog.at_level(logging.WARNING, logger="agent.digestion.shadow"):
            SH.resolve_judge("auto", env=judge_env(tmp_path))
        assert not [r for r in caplog.records if "回落" in r.getMessage()]


# ════════════════════════════════════════════════════════════
#  二、入选即受预算护栏约束（前置拦截 + UTC 记账 + 精确标签）
# ════════════════════════════════════════════════════════════


class TestSelectionBudgetGuard:
    def test_selected_channel_carries_the_wiring(self, stub_channel, tmp_path):
        """★ 选中真实通道 ⇒ 随附护栏接线（此前这条链一个都没有）"""
        resolved = SH.resolve_judge("auto", env=judge_env(
            tmp_path, **{JR.JUDGE_ENABLE_ENV: "true", JR.JUDGE_BUDGET_ENV: "100"}))
        assert resolved.kind == SH.JUDGE_KIND_LLM
        assert set(resolved.guard_kwargs) == {"precheck", "on_call", "kind_fallback_for"}
        assert resolved.guard_kwargs["kind_fallback_for"] is SH.judge_fallback_kind

    def test_over_budget_is_blocked_before_selection(self, stub_channel, tmp_path):
        """★ 超预算 ⇒ **入选前**就拦下（一次真实调用都不发），原因如实"""
        resolved = SH.resolve_judge("auto", env=judge_env(
            tmp_path, **{JR.JUDGE_ENABLE_ENV: "true", JR.JUDGE_BUDGET_ENV: "0"}))
        assert resolved.kind == SH.judge_fallback_kind(SH.JUDGE_REASON_BUDGET_EXCEEDED)
        assert resolved.is_llm is False
        assert resolved.judge is None
        assert resolved.detail["budget"]["reason_code"] == SH.JUDGE_REASON_BUDGET_EXCEEDED
        assert stub_channel.calls == 0

    def test_runner_with_explicit_llm_mode_charges_and_guards(self, stub_channel, tmp_path):
        """★ 显式 `judge_mode=llm`（绕过运行时注入的那条路）也必须受约束且记账"""
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true",
                                     JR.JUDGE_BUDGET_ENV: "100"})
        runner = SH.ShadowRunner(env=env, judge_mode=SH.JUDGE_MODE_LLM,
                                 emit_events=False)
        assert runner.runtime is None
        assert runner.judge.kind == SH.JUDGE_KIND_LLM
        assert runner.judge_guard._precheck is not None, "入选链必须带前置拦截"
        assert runner.judge_guard._on_call is not None, "入选链必须带成本记账"
        before = judge_costs(tmp_path)
        assert runner.sandbox.judge("same text", "same text") == pytest.approx(0.93)
        after = judge_costs(tmp_path)
        assert stub_channel.calls == 1
        assert after["calls"] - before["calls"] == 1, "真实调用必须计入 UTC judge 栏"
        assert after["cost_normalized_cents"] > before["cost_normalized_cents"]

    def test_runner_with_explicit_llm_mode_blocks_when_over_budget(
            self, stub_channel, tmp_path):
        """★ 同一入口、预算为 0 ⇒ 标签如实变 `budget_exceeded` 且不花钱

        拦截发生在**入选时**（比"调用时"更早）：连守卫都不必建 ——
        `judge.guard_kwargs` 为空、`_precheck is None`，因为根本没有真实通道被选中。
        """
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true",
                                     JR.JUDGE_BUDGET_ENV: "0"})
        runner = SH.ShadowRunner(env=env, judge_mode=SH.JUDGE_MODE_LLM,
                                 emit_events=False)
        before = judge_costs(tmp_path)
        score = runner.sandbox.judge("same text", "same text")
        after = judge_costs(tmp_path)
        assert score == 1.0, "回落确定性打分器（同文本 ⇒ 1.0）"
        assert runner.judge_guard.effective_kind == SH.judge_fallback_kind(
            SH.JUDGE_REASON_BUDGET_EXCEEDED)
        assert runner.judge.guard_kwargs == {}
        assert runner.judge_guard._precheck is None, "未选真实通道 ⇒ 无需调用时护栏"
        assert runner.judge.detail["budget"]["reason_code"] == \
            SH.JUDGE_REASON_BUDGET_EXCEEDED
        assert stub_channel.calls == 0
        assert after["calls"] == before["calls"]

    def test_mid_batch_exhaustion_relabels_truthfully(self, monkeypatch, tmp_path):
        """★ 批内花光预算 ⇒ 逐样本标签跟着实际判定器走（不留"说 llm 实际本地"）

        预算给一个极小值：第 1 次调用后 `spent >= 可用预算` ⇒ 第 2 次前置拦截。
        """
        adapter = StubAdapter()
        from agent.model_router import adapters as adapters_mod

        monkeypatch.setattr(adapters_mod.ModelAdapterFactory, "create",
                            staticmethod(lambda provider, model_name, **kw: adapter))
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true",
                                     JR.JUDGE_BUDGET_ENV: "0.000001"})
        runner = SH.ShadowRunner(env=env, judge_mode=SH.JUDGE_MODE_LLM,
                                 emit_events=False)
        assert runner.sandbox.judge("a", "a") == pytest.approx(0.93)
        assert runner.judge_guard.effective_kind == SH.JUDGE_KIND_LLM
        assert runner.sandbox.judge("b", "b") == 1.0            # ← 前置拦截后走本地
        assert runner.judge_guard.effective_kind == SH.judge_fallback_kind(
            SH.JUDGE_REASON_BUDGET_EXCEEDED)
        assert runner.judge_guard.precheck_blocks >= 1, "第二次调用被前置拦截（省的是钱）"
        assert adapter.calls == 1, "第二次不得发出真实调用"
        # 层③ 标签由动态解析器给出 ⇒ 与实际判定器一致
        assert runner.sandbox.judge_kind_resolver is not None
        assert runner.sandbox.judge_kind_resolver() == \
            runner.judge_guard.effective_kind


# ════════════════════════════════════════════════════════════
#  三、机械证据：入选链自身不新增 env 读取点（注册表零缺口不可被破坏）
# ════════════════════════════════════════════════════════════


def _env_reads(path: Path, *, func: str) -> List[str]:
    """某函数体内的环境读取点（``os.environ`` / ``getenv``）"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == func):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and isinstance(child.value, ast.Name) \
                    and child.value.id == "os":
                found.append(f"os.{child.attr}")
            elif isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) \
                    and child.func.attr in ("getenv", "environ"):
                found.append(child.func.attr)
    return found


class TestSelectionChainDiscipline:
    def test_no_new_env_read_in_selection_chain(self):
        """入选链的开关判定必须**转发**给 `judge_runtime`（单点解析），不得自己读环境"""
        for func in ("resolve_judge", "_judge_master_switch_on",
                     "_judge_channel_wiring"):
            reads = _env_reads(SHADOW_PY, func=func)
            assert "os.environ" not in reads and "getenv" not in reads, (
                f"{func} 内不得直接读环境（应为转发）；实测：{reads}")

    def test_extractor_is_not_a_false_green(self):
        """防假绿灯：提取器对**已知存在**的读取点必须能报出来（否则上面的空结果无意义）"""
        known = REPO_ROOT / "agent" / "digestion" / "judge_runtime.py"
        assert "os.environ" in _env_reads(known, func="_env_map")

    def test_switch_reader_delegates_to_single_definition(self):
        """总开关解析必须复用 `judge_config_from_env`（口径单点）"""
        tree = ast.parse(SHADOW_PY.read_text(encoding="utf-8"))
        body = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_judge_master_switch_on":
                body = node
        assert body is not None
        names = {n.attr for n in ast.walk(body) if isinstance(n, ast.Attribute)}
        assert "judge_config_from_env" in names

    def test_guard_wiring_reuses_the_runtime_factory(self):
        """护栏接线必须是**同一份实现**（不得在入选链里另抄一段预算逻辑）"""
        tree = ast.parse(SHADOW_PY.read_text(encoding="utf-8"))
        body = None
        for node in ast.walk(tree):
            if (isinstance(node, ast.FunctionDef)
                    and node.name == "_judge_channel_wiring"):
                body = node
        assert body is not None
        names = {n.attr for n in ast.walk(body) if isinstance(n, ast.Attribute)}
        assert "build_judge_budget_wiring" in names
        # 反向：入选链**不得**自己造护栏对象（否则两处实现必然漂移）
        assert "JudgeBudgetGuard" not in names
