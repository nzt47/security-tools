"""TASK-S10-02 judge runtime 接入生产灰度链路 单测

背景（S9-02 遗留 W1-L3）：`build_judge_runtime` 全仓仅"定义 + 导出"，
`ShadowRunner(judge_runtime=)` **没有生产注入者** ⇒ 灰度评测实际仍在走确定性打分器。
本文件钉死"接入"这件事的**四条性质**：

1. **默认关闭**：未开启时注入器返回 `None`（**不是** disabled 版运行时）——
   后者会把 `judge_kind` 改成 `deterministic_local(disabled)` 从而改变开关关时的报告；
2. **开关开启**：`ShadowRunner` 构造期自动注入运行时，报告里 `judge_kind` 为
   ``llm:<provider>:<model>``（如实反映实际所用判定器），judge 调用**计入 UTC**；
3. **回落如实**：超预算 ⇒ `deterministic_local(budget_exceeded)` 且**不发真实调用**；
4. **不夺权、不阻断**：显式 `judge=` / `judge_mode=` / `judge_kind=` / 自带判定器的
   sandbox 一律优先；注入器自身故障**不得**让灰度构造失败。

另有 `TestProductionCallChain` 用 **AST 调用图**给出"生产调用链存在"的机械证据
（不靠人读代码下结论，也不靠 `grep` 字面匹配）。

隔离纪律：事件目录 / 灰度目录 / 判定存档一律落 ``tmp_path``；本文件不发真实模型调用
（通道用桩适配器）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from agent.digestion import cases as C
from agent.digestion import gate as G
from agent.digestion import judge_runtime as JR
from agent.digestion import shadow as SH
from agent.observability import events as events_mod
from agent.observability import utc as utc_mod

REPO_ROOT = Path(__file__).resolve().parents[2]
CAP = "cp.builtin.read_file"
SECRET = "sk-s10-02-fake-secret-0123456789"


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """事件目录 + 灰度目录隔离；清掉可能存在的 judge/凭证环境变量"""
    monkeypatch.setenv("CP_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv(SH.SHADOW_DIR_ENV, str(tmp_path / "shadow"))
    for name in ("CP_DIGESTION_JUDGE_ENABLED", "CP_DIGESTION_JUDGE_PROVIDER",
                 "CP_DIGESTION_JUDGE_MODEL", "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS",
                 "CP_DIGESTION_JUDGE_THRESHOLD", "CP_DIGESTION_JUDGE_FOLLOW_FASTING",
                 "CP_DIGESTION_JUDGE_SECRET_FILE", "CP_DIGESTION_JUDGE_DOTENV",
                 "CP_DIGESTION_SHADOW_ENABLED", "CP_DIGESTION_GRAY_ENABLED",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY",
                 "LLM_API_KEY", "LLM_PROVIDER", "LLM_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    events_mod.reset_event_stores()
    yield tmp_path
    events_mod.reset_event_stores()


# ════════════════════════════════════════════════════════════
#  构造工具（与 test_digestion_shadow.py 同款：真发证、真回放）
# ════════════════════════════════════════════════════════════


def structured(verdict: str, confidence: float, reason: str = "r") -> str:
    return json.dumps({"verdict": verdict, "confidence": confidence,
                       "reason": reason}, ensure_ascii=False)


class StubAdapter:
    """桩适配器（**不联网、不花钱**；记录调用次数以断言"前置拦截真的省了钱"）"""

    def __init__(self, *, verdict: str = "equivalent", confidence: float = 0.9) -> None:
        self.calls = 0
        self.prompts: List[str] = []
        self.verdict = verdict
        self.confidence = confidence

    def is_available(self) -> bool:
        return True

    def generate(self, prompt: str, **kwargs: Any) -> Dict[str, Any]:
        self.calls += 1
        self.prompts.append(str(prompt))
        return {"content": structured(self.verdict, self.confidence),
                "usage": {"prompt_tokens": 120, "completion_tokens": 40}}


def make_case(index: int = 0, *, root: str = "C:/sandbox") -> C.EquivalenceCase:
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path},
                           capability_id=CAP),
             C.ProgramStep(label="write_file", params={"path": path,
                                                       "content": f"c{index}"},
                           capability_id=CAP)]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def ready_case_set(tmp_path, size: int = 24):
    """造一个**真发证**的判定集（≥ ``GATE_REPLAY_MIN`` 才有通行证）"""
    case_set = C.build_case_set(CAP, [make_case(i) for i in range(size)])
    store = G.PassportStore(str(tmp_path / "cases"))
    result = G.acceptance_gate(CAP, case_set=case_set, passport_store=store,
                               emit_events=False)
    assert result.passed is True, result.reasons()
    return case_set, store


def judge_env(tmp_path, **overrides: str) -> Dict[str, str]:
    """灰度用环境映射（**非空 dict** ⇒ 不走 ``os.environ``，保证可复现）"""
    env: Dict[str, str] = {
        SH.JUDGE_MODE_ENV: SH.JUDGE_MODE_AUTO,
        SH.SHADOW_ENABLE_ENV: "false",
        SH.GRAY_ENABLE_ENV: "false",
        SH.SHADOW_DIR_ENV: str(tmp_path / "shadow"),
        # dotenv 指向不存在的文件：本文件**不得**因主工作区真实 `.env` 而改变结论
        JR.JUDGE_DOTENV_ENV: str(tmp_path / "absent.env"),
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return env


def make_runner(tmp_path, store, env: Dict[str, str], **kwargs: Any) -> SH.ShadowRunner:
    return SH.ShadowRunner(
        passport_store=store,
        case_store=C.open_case_store(str(tmp_path / "cases")),
        ledger=SH.ShadowLedger(str(tmp_path / "shadow" / "l.jsonl")),
        review_queue=SH.ManualReviewQueue(str(tmp_path / "shadow" / "r.jsonl")),
        env=env, emit_events=False, **kwargs)


def judge_costs(tmp_path) -> Dict[str, Any]:
    """读入 UTC 的 judge 栏（与护栏同一个读侧入口）"""
    return utc_mod.judge_cost_cents(directory=str(tmp_path / "events"))


# ════════════════════════════════════════════════════════════
#  一、开关语义（默认关闭 ⇒ None；不是 "disabled 运行时"）
# ════════════════════════════════════════════════════════════


class TestInjectionGate:
    def test_disabled_by_default_returns_none(self):
        """★ 安全底线：未开启 ⇒ ``None``（灰度仍走确定性打分器）"""
        assert JR.build_judge_runtime_if_enabled(env={}) is None
        assert SH.judge_runtime_from_env(env={}) is None

    def test_disabled_never_returns_a_runtime_object(self):
        """★ 逐字节一致的前提：关闭时**不得**返回"disabled 版运行时"

        若返回运行时，`ShadowRunner` 会把 `judge_kind` 写成
        ``deterministic_local(disabled)``（而不是接入前的 ``deterministic_local``）——
        报告就变了。本用例钉死"必须是 None"。
        """
        runtime = SH.judge_runtime_from_env(env={JR.JUDGE_ENABLE_ENV: "false"})
        assert runtime is None
        assert not isinstance(runtime, JR.JudgeRuntime)

    def test_illegal_flag_value_counts_as_disabled(self):
        assert SH.judge_runtime_from_env(env={JR.JUDGE_ENABLE_ENV: "maybe"}) is None

    def test_enabled_returns_runtime_with_honest_fallback_label(self, tmp_path):
        """开启但无 provider/model ⇒ 仍返回运行时，标签如实标 `no_credentials`"""
        runtime = SH.judge_runtime_from_env(env=judge_env(
            tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"}))
        assert isinstance(runtime, JR.JudgeRuntime)
        assert runtime.state == JR.AVAILABILITY_NO_CREDENTIALS
        assert runtime.kind == SH.judge_fallback_kind(SH.JUDGE_REASON_NO_CREDENTIALS)
        assert runtime.judge is None, "无凭证/无通道时不得构造真实 judge 对象"

    def test_constructor_failure_does_not_break_gray(self, monkeypatch, tmp_path):
        """注入器自身故障 ⇒ 按"未启用"处理（与 `_cost_policy_factor` 同纪律）"""
        def _boom(*_a: Any, **_k: Any) -> Any:
            raise RuntimeError("配置解析炸了")

        monkeypatch.setattr(JR, "build_judge_runtime_if_enabled", _boom)
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"})
        assert SH.judge_runtime_from_env(env=env) is None
        # 灰度器照常构造，且**不静默**（runtime 为 None 是显式事实，可被报告读到）
        case_set, store = ready_case_set(tmp_path)
        runner = make_runner(tmp_path, store, env)
        assert runner.runtime is None
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        assert report.allowed is True and report.total == 3


# ════════════════════════════════════════════════════════════
#  二、ShadowRunner 构造期注入（显式优先 / 默认不变）
# ════════════════════════════════════════════════════════════


class TestRunnerInjection:
    def test_flag_off_keeps_legacy_path(self, tmp_path):
        """★ 开关关 ⇒ 与接入前同一条路径（`resolve_judge("auto")` 的标签）"""
        case_set, store = ready_case_set(tmp_path)
        runner = make_runner(tmp_path, store, judge_env(tmp_path))
        assert runner.runtime is None
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        assert report.judge_kind == SH.JUDGE_KIND_LLM_FALLBACK
        assert report.judge_is_llm is False

    def test_explicit_judge_wins_over_enabled_flag(self, tmp_path):
        """★ 不夺权：显式注入的判定器优先（flag 开着也不被替换）"""
        case_set, store = ready_case_set(tmp_path)
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"})
        runner = make_runner(tmp_path, store, env,
                             judge=SH.resolve_judge("local").scorer,
                             judge_kind=SH.JUDGE_KIND_LOCAL)
        assert runner.runtime is None
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        assert report.judge_kind == SH.JUDGE_KIND_LOCAL

    def test_explicit_judge_kind_wins(self, tmp_path):
        """显式 `judge_kind=` 也是调用方意图（不得被"注入的真实标签"覆盖）"""
        case_set, store = ready_case_set(tmp_path)
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"})
        runner = make_runner(tmp_path, store, env, judge_kind="deterministic_local")
        assert runner.runtime is None
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        assert report.judge_kind == "deterministic_local"

    def test_explicit_judge_mode_wins(self, tmp_path):
        case_set, store = ready_case_set(tmp_path)
        env = judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"})
        runner = make_runner(tmp_path, store, env, judge_mode=SH.JUDGE_MODE_LOCAL)
        assert runner.runtime is None

    def test_sandbox_with_own_judge_is_not_overridden(self, tmp_path):
        """显式 sandbox 自带判定器 ⇒ 不注入（否则触发"两套判定器"冲突）"""
        case_set, store = ready_case_set(tmp_path)
        box = SH.ReplaySandbox(judge=SH.judge_similarity, measure_wall=True,
                               judge_kind=SH.JUDGE_KIND_LOCAL)
        runner = make_runner(tmp_path, store,
                             judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true"}),
                             sandbox=box)
        assert runner.runtime is None
        assert runner.judge.kind == SH.JUDGE_KIND_LOCAL


# ════════════════════════════════════════════════════════════
#  三、开启时：真实通道接入 + 精确标签 + UTC 计费
# ════════════════════════════════════════════════════════════


class TestEnabledChannel:
    @staticmethod
    def _stub_factory(monkeypatch, adapter: StubAdapter) -> None:
        """把模型适配器工厂换成桩（**唯一**的"假"：网络通道；其余全走真实代码）"""
        from agent.model_router import adapters as adapters_mod

        def _create(provider: str, model_name: str, **kwargs: Any) -> Any:
            return adapter

        monkeypatch.setattr(adapters_mod.ModelAdapterFactory, "create",
                            staticmethod(_create))

    def test_auto_injection_reports_precise_kind_and_charges_utc(
            self, tmp_path, monkeypatch):
        """★ 核心验收：构造期自动注入 ⇒ ``judge_kind=llm:<provider>:<model>`` + UTC 增量"""
        case_set, store = ready_case_set(tmp_path)
        adapter = StubAdapter()
        self._stub_factory(monkeypatch, adapter)
        env = judge_env(tmp_path, **{
            JR.JUDGE_ENABLE_ENV: "true",
            SH.JUDGE_PROVIDER_ENV: "deepseek",
            SH.JUDGE_MODEL_ENV: "deepseek-v4-flash",
            SH.JUDGE_BASE_URL_ENV: "https://api.deepseek.com/v1",
            "DEEPSEEK_API_KEY": SECRET,
            JR.JUDGE_BUDGET_ENV: "100",
        })
        before = judge_costs(tmp_path)

        runner = make_runner(tmp_path, store, env)          # ← 不传 judge/judge_runtime
        assert isinstance(runner.runtime, JR.JudgeRuntime)

        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        after = judge_costs(tmp_path)

        assert report.allowed is True and report.total == 3
        assert report.judge_kind == "llm:deepseek:deepseek-v4-flash"
        assert report.judge_is_llm is True
        # 批内逐样本标签一致（不留"过渡样本"的不实标注）
        assert {s.judge_kind for s in report.samples} == {report.judge_kind}
        # 报告里带可用性/预算/护栏快照（无明文凭证）
        assert report.judge["runtime"]["state"] == JR.AVAILABILITY_AVAILABLE
        assert SECRET not in json.dumps(report.to_dict(), ensure_ascii=False, default=str)
        # UTC：judge 栏有真实增量（探针 1 次 + 每样本 1 次）
        assert after["calls"] - before["calls"] == 1 + report.total
        assert after["cost_normalized_cents"] > before["cost_normalized_cents"]
        assert adapter.calls == 1 + report.total
        assert not runner.runtime.budget.record_errors

    def test_budget_exceeded_falls_back_without_real_call(self, tmp_path, monkeypatch):
        """★ 超预算 ⇒ 如实回落 `budget_exceeded` 且**一次真实调用都不发**"""
        case_set, store = ready_case_set(tmp_path)
        adapter = StubAdapter()
        self._stub_factory(monkeypatch, adapter)
        env = judge_env(tmp_path, **{
            JR.JUDGE_ENABLE_ENV: "true",
            SH.JUDGE_PROVIDER_ENV: "deepseek",
            SH.JUDGE_MODEL_ENV: "deepseek-v4-flash",
            "DEEPSEEK_API_KEY": SECRET,
            JR.JUDGE_BUDGET_ENV: "0",                     # 一分钱都不允许
        })
        before = judge_costs(tmp_path)
        runner = make_runner(tmp_path, store, env)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        after = judge_costs(tmp_path)

        assert report.judge_kind == SH.judge_fallback_kind(
            SH.JUDGE_REASON_BUDGET_EXCEEDED)
        assert report.judge_is_llm is False
        assert {s.judge_kind for s in report.samples} == {report.judge_kind}
        assert adapter.calls == 0, "前置拦截必须真的省下钱（不是只换个标签）"
        assert runner.runtime.guard.precheck_blocks >= 1
        assert after["calls"] == before["calls"]
        assert after["cost_normalized_cents"] == before["cost_normalized_cents"]

    def test_callback_keeps_charging_every_sample(self, tmp_path):
        """显式注入桩通道（`judge_runtime_from_env(**kwargs)` 透传）也走同一条记账链"""
        case_set, store = ready_case_set(tmp_path)
        adapter = StubAdapter()
        events_dir = str(tmp_path / "events")
        runtime = SH.judge_runtime_from_env(
            env=judge_env(tmp_path, **{JR.JUDGE_ENABLE_ENV: "true",
                                       SH.JUDGE_PROVIDER_ENV: "deepseek",
                                       SH.JUDGE_MODEL_ENV: "deepseek-v4-flash",
                                       "DEEPSEEK_API_KEY": SECRET}),
            adapter=adapter, events_dir=events_dir)
        assert isinstance(runtime, JR.JudgeRuntime)
        before = judge_costs(tmp_path)
        runner = make_runner(tmp_path, store, judge_env(tmp_path),
                             judge_runtime=runtime)
        report = runner.run(CAP, case_set=case_set, force=True, daily_avg=20,
                            write_ledger=False, enqueue_manual=False)
        after = judge_costs(tmp_path)
        assert report.judge_kind == "llm:deepseek:deepseek-v4-flash"
        assert after["calls"] - before["calls"] == 1 + report.total
        assert runtime.verdict_store is not None


# ════════════════════════════════════════════════════════════
#  四、生产调用链（AST 调用图，机械证据）
# ════════════════════════════════════════════════════════════


def _call_targets(path: Path, *, func: str = "", cls: str = "") -> List[str]:
    """收集某函数（或某类全部方法）体内的被调名字（``Name`` / 属性尾名）

    只做"机械提取"，不做语义推断：`x.y()` 记 ``y``；`f()` 记 ``f``。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: List[str] = []

    def _collect(node: ast.AST) -> None:
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                target = child.func
                if isinstance(target, ast.Name):
                    found.append(target.id)
                elif isinstance(target, ast.Attribute):
                    found.append(target.attr)

    for node in ast.walk(tree):
        if cls and isinstance(node, ast.ClassDef) and node.name == cls:
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not func or sub.name == func:
                        _collect(sub)
        elif (not cls) and func and isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            _collect(node)
    if not cls and not func:                       # 整模块（脚本级证据用）
        _collect(tree)
    return found


def _env_reads(path: Path, *, func: str) -> List[str]:
    """某函数体内的**环境读取点**（``os.environ`` / ``getenv``；docstring 不计）"""
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
            elif isinstance(child, ast.Name) and child.id == "getenv":
                found.append(child.id)
    return found


class TestProductionCallChain:
    """`build_judge_runtime` 的生产调用链（S9-02 W1-L3 的正向封闭）"""

    SHADOW = REPO_ROOT / "agent" / "digestion" / "shadow.py"
    JUDGE = REPO_ROOT / "agent" / "digestion" / "judge_runtime.py"
    CHAIN = REPO_ROOT / "scripts" / "s705_real_digestion_chain.py"

    def test_extractor_is_not_a_false_green(self):
        """★ 防假绿灯：提取器必须**真的**能提取到已知存在的调用

        若提取器本身坏了（返回空表），后面每条断言都会"因为空而通过"。
        故先用一个已知存在的调用（`ShadowRunner.__init__` 里的 `resolve_judge`）
        证明它非空且可用。
        """
        calls = _call_targets(self.SHADOW, cls="ShadowRunner", func="__init__")
        assert "resolve_judge" in calls
        assert "ReplaySandbox" in calls
        assert len(calls) > 10

    def test_runner_init_calls_the_production_injector(self):
        calls = _call_targets(self.SHADOW, cls="ShadowRunner", func="__init__")
        assert "judge_runtime_from_env" in calls, (
            "ShadowRunner 构造期必须调用生产注入点（否则 judge_runtime 仍无生产注入者）")

    def test_injector_calls_the_enable_gate(self):
        calls = _call_targets(self.SHADOW, func="judge_runtime_from_env")
        assert "build_judge_runtime_if_enabled" in calls

    def test_gate_calls_build_judge_runtime(self):
        calls = _call_targets(self.JUDGE, func="build_judge_runtime_if_enabled")
        assert "build_judge_runtime" in calls, (
            "`build_judge_runtime` 必须真的被生产代码调用（这正是 W1-L3 的缺口）")

    def test_s705_real_chain_uses_the_injector(self):
        calls = _call_targets(self.CHAIN)
        assert "judge_runtime_from_env" in calls, "S7-05 真实链路驱动必须显式注入"

    def test_chain_is_reachable_from_a_real_call_site(self):
        """端到端机械复核：四段拼起来构成一条**不断裂**的链

        `shadow_quality()`（生产门面）→ `ShadowRunner.__init__` → 注入点 →
        开关门 → `build_judge_runtime`。
        """
        quality = _call_targets(self.SHADOW, func="shadow_quality")
        assert "ShadowRunner" in quality
        runner_calls = _call_targets(self.SHADOW, cls="ShadowRunner", func="__init__")
        injector = _call_targets(self.SHADOW, func="judge_runtime_from_env")
        gate = _call_targets(self.JUDGE, func="build_judge_runtime_if_enabled")
        assert "judge_runtime_from_env" in runner_calls
        assert "build_judge_runtime_if_enabled" in injector
        assert "build_judge_runtime" in gate

    def test_no_env_read_added_by_this_wiring(self):
        """接入不得**新增** env 读取点（否则设置注册表的零缺口门会红）

        口径：本任务只复用既有键（``CP_DIGESTION_JUDGE_*``）。注入点只做
        "转发 + 兜底"，env 解析单点在 `judge_runtime` 模块内。
        """
        reads = _env_reads(self.SHADOW, func="judge_runtime_from_env")
        assert "os.environ" not in reads and "getenv" not in reads, (
            f"注入点内不得直接读环境（应为转发）；实测读取点：{reads}")
