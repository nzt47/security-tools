"""重试预算与重试放大收敛单测（TASK-08 子工作流 D / E1e · E1e2 · E1k）

【本文件证明什么】
    1. **三层重试相乘被收敛为单一预算**：工具调用层 + 错误处理层 + MCP 层
       共享同一份 `RetryBudget`，上游**持续失败**时总 HTTP 次数 ≤ 预算。
    2. **E1k**：`MCPConfig.max_retries` 与 `initialize(max_retries=N)`
       现在**真正生效**（改动前被定义期求值的装饰器常量静默顶掉）。
    3. **E1e2 分层缺口补齐**：工具调用重试与 MCP 重试**补上抖动**
       （注意：LLM 主链路 `memory/llm_service.py` **本来就有**
       `jitter_factor=0.1`，缺口只在另外两处 —— 本文件按此分层断言，
       不笼统声称"全链路无抖动"）。

【纪律（D12）】
    时间类断言**注入时钟 / 打桩 sleep**，不真等。`RetryBudget` 可注入 `time_fn`；
    `time.sleep` 在 tool_calling 命名空间内被打桩并记录实参（同时也用于断言抖动）。
"""

from __future__ import annotations

import asyncio
import types

import pytest


# ════════════════════════════════════════════════════════════
#  一、预算本体（注入时钟，不真等）
# ════════════════════════════════════════════════════════════

class TestRetryBudgetUnit:
    def test_attempts_budget_is_enforced(self):
        from agent.timeout_budget import RetryBudget
        b = RetryBudget(max_retries=2, deadline_sec=0)
        assert b.try_consume("a") is True
        assert b.try_consume("b") is True
        assert b.try_consume("c") is False     # 预算耗尽
        assert b.used == 2
        assert b.denied == 1
        assert b.exhausted is True

    def test_deadline_budget_with_injected_clock(self):
        """注入假时钟：窗口耗尽即拒绝，**不真等**（D12）"""
        from agent.timeout_budget import RetryBudget
        now = {"t": 1000.0}
        b = RetryBudget(max_retries=100, deadline_sec=10.0, time_fn=lambda: now["t"])
        assert b.try_consume("first") is True      # 惰性启动窗口
        now["t"] += 5.0
        assert b.try_consume("ok") is True
        now["t"] += 6.0                            # 累计 11s > 10s 窗口
        assert b.try_consume("denied") is False
        assert b.snapshot()["elapsed_sec"] == 11.0

    def test_window_starts_lazily_not_at_construction(self):
        """窗口自**第一次重试**起算 ⇒ 长任务的晚期瞬时抖动不会被误杀"""
        from agent.timeout_budget import RetryBudget
        now = {"t": 0.0}
        b = RetryBudget(max_retries=5, deadline_sec=10.0, time_fn=lambda: now["t"])
        now["t"] = 3600.0                          # 任务已跑 1 小时
        assert b.try_consume("late_but_first_retry") is True

    def test_no_active_budget_means_unlimited_backward_compat(self):
        """D2：没有活动预算时 `consume_retry` 返回 True（= 与改动前一致）"""
        from agent.timeout_budget import consume_retry, current_budget
        assert current_budget() is None
        assert consume_retry("anything") is True


# ════════════════════════════════════════════════════════════
#  二、错误处理层（agent/error_handler.py）接入预算
# ════════════════════════════════════════════════════════════

class TestErrorHandlerRespectsBudget:
    def _handler_and_policy(self, max_retries):
        from agent.error_handler import ErrorHandler, RetryPolicy, RecoverableError
        # initial_delay=0 ⇒ 重试不真等（D12）
        policy = RetryPolicy(max_retries=max_retries, initial_delay=0.0)
        return ErrorHandler(), policy, RecoverableError

    def test_without_budget_uses_policy_retries(self):
        """基线（无预算）：`max_retries=4` ⇒ 1 + 4 = 5 次调用"""
        handler, policy, exc = self._handler_and_policy(4)
        calls = {"n": 0}

        def _always_fail():
            calls["n"] += 1
            raise exc("upstream down")

        with pytest.raises(Exception):
            handler.execute_with_retry(_always_fail, retry_policy=policy,
                                       retryable_exceptions=(exc,))
        assert calls["n"] == 5, f"基线应为 5 次，实际 {calls['n']}"

    def test_with_budget_total_calls_bounded(self):
        """★核心：共享预算 2 ⇒ 总调用 ≤ 1 + 2 = 3（而不是策略的 5）"""
        from agent.timeout_budget import RetryBudget, use_budget, reset_budget
        handler, policy, exc = self._handler_and_policy(4)
        calls = {"n": 0}

        def _always_fail():
            calls["n"] += 1
            raise exc("upstream down")

        budget = RetryBudget(max_retries=2, deadline_sec=0)
        token = use_budget(budget)
        try:
            with pytest.raises(Exception):
                handler.execute_with_retry(_always_fail, retry_policy=policy,
                                           retryable_exceptions=(exc,))
        finally:
            reset_budget(token)

        assert calls["n"] <= 1 + 2, f"总调用 {calls['n']} 超出预算 1+2"
        assert calls["n"] == 3, f"应为 1+2=3 次，实际 {calls['n']}"
        assert budget.denied >= 1, "应至少拒绝过一次重试（放大被截断的证据）"


# ════════════════════════════════════════════════════════════
#  三、MCP 层：E1k 配置生效 + 预算 + 抖动
# ════════════════════════════════════════════════════════════

def _make_mcp_client(max_retries, timeout=5):
    """构造 MCPClient，并把 `start` / `_send_request` 打桩掉（不起子进程）"""
    from mcp_services.mcp_client import MCPClient, MCPConfig
    client = MCPClient("python", ["-c", "pass"],
                       config=MCPConfig(timeout=timeout, max_retries=max_retries))
    calls = {"n": 0}

    async def _noop_start():
        client._is_running = True

    async def _always_timeout(method, params=None):
        calls["n"] += 1
        raise TimeoutError("simulated upstream timeout")

    client.start = _noop_start
    client._send_request = _always_timeout
    return client, calls


class TestMcpMaxRetriesActuallyApplies:
    """E1k：`max_retries` 不再是装饰器定义期常量，配置与传参真正生效"""

    def test_config_max_retries_takes_effect(self):
        """`MCPConfig(max_retries=2)` ⇒ 恰好 2 次尝试（改动前恒为 DEFAULT=3）"""
        client, calls = _make_mcp_client(max_retries=2)
        with pytest.raises(TimeoutError):
            asyncio.run(client.initialize())
        assert calls["n"] == 2, f"配置 max_retries=2 应生效，实际尝试 {calls['n']} 次"

    def test_explicit_kwarg_overrides_config(self):
        """`initialize(max_retries=4)` 显式传参优先（改动前该形参被接收后丢弃）"""
        client, calls = _make_mcp_client(max_retries=1)
        with pytest.raises(TimeoutError):
            asyncio.run(client.initialize(max_retries=4))
        assert calls["n"] == 4, f"显式 max_retries=4 应生效，实际 {calls['n']}"

    def test_default_config_uses_default_max_retries(self):
        """不显式配置 ⇒ 回落到 `DEFAULT_MAX_RETRIES`（D2：旧行为不变）"""
        from mcp_services.mcp_client import DEFAULT_MAX_RETRIES
        client, calls = _make_mcp_client(max_retries=DEFAULT_MAX_RETRIES)
        with pytest.raises(TimeoutError):
            asyncio.run(client.initialize())
        assert calls["n"] == DEFAULT_MAX_RETRIES

    def test_mcp_retry_respects_shared_budget(self):
        """★MCP 层也接入同一份预算：预算 1 ⇒ 总尝试 ≤ 1 + 1"""
        from agent.timeout_budget import RetryBudget, use_budget, reset_budget
        client, calls = _make_mcp_client(max_retries=8)
        budget = RetryBudget(max_retries=1, deadline_sec=0)
        token = use_budget(budget)
        try:
            with pytest.raises(TimeoutError):
                asyncio.run(client.initialize())
        finally:
            reset_budget(token)
        assert calls["n"] <= 2, f"预算 1 下总尝试应 ≤2，实际 {calls['n']}"
        assert budget.denied >= 1

    def test_decorator_still_accepts_explicit_int(self):
        """D2 向后兼容：`retry_on_failure(max_retries=2)` 的旧用法语义不变"""
        from mcp_services.mcp_client import retry_on_failure
        calls = {"n": 0}

        @retry_on_failure(max_retries=2, delay=0.0)
        async def _f():
            calls["n"] += 1
            raise TimeoutError("x")

        with pytest.raises(TimeoutError):
            asyncio.run(_f())
        assert calls["n"] == 2


# ════════════════════════════════════════════════════════════
#  四、抖动（分层：缺口只在工具调用重试与 MCP 重试两处）
# ════════════════════════════════════════════════════════════

class TestJitterOnToolAndMcpRetry:
    def test_jitter_band_matches_llm_main_chain_factor(self):
        """抖动系数与 LLM 主链路一致（0.1）⇒ 落在 ±10% 带内且随机会变化"""
        from agent.timeout_budget import jittered_delay
        samples = [jittered_delay(10.0) for _ in range(200)]
        assert all(9.0 <= s <= 11.0 for s in samples), "抖动应落在 ±10% 带内"
        assert len(set(samples)) > 1, "抖动必须是随机的（去相关），不能恒为 10.0"

    def test_zero_factor_keeps_deterministic_backoff(self):
        """系数 0 ⇒ 保持确定性退避（可回滚，D2）"""
        from agent.timeout_budget import jittered_delay
        assert jittered_delay(2.0, factor=0.0) == 2.0

    def test_mcp_retry_delay_is_jittered(self, monkeypatch):
        """MCP 重试的等待时长带抖动（改动前是纯确定性倍增）"""
        import mcp_services.mcp_client as mc
        slept = []

        async def _fake_sleep(sec):
            slept.append(sec)

        monkeypatch.setattr(mc.asyncio, "sleep", _fake_sleep)
        client, _calls = _make_mcp_client(max_retries=3)

        with pytest.raises(TimeoutError):
            asyncio.run(client.initialize())

        assert len(slept) == 2, f"3 次尝试应有 2 次等待，实际 {slept}"
        # 基础退避为 1.0、2.0（INITIAL_DELAY / ×BACKOFF_FACTOR），带 ±10% 抖动
        assert 0.9 <= slept[0] <= 1.1, slept
        assert 1.8 <= slept[1] <= 2.2, slept


# ════════════════════════════════════════════════════════════
#  五、端到端：三层嵌套相乘被单一预算截断（含真实 tool_calling 循环）
# ════════════════════════════════════════════════════════════

class _FakeOpenAIResponse:
    def __init__(self, message):
        self.choices = [types.SimpleNamespace(message=message)]


def _build_fake_llm(http_calls, always_fail=True):
    """构造一个最小假 LLM service：`_call_llm_openai` 会直接打 `create()`"""

    class _Completions:
        def create(self, **kwargs):
            http_calls["n"] += 1
            if always_fail:
                raise RuntimeError("simulated LLM HTTP 500")
            return _FakeOpenAIResponse(types.SimpleNamespace(
                content="ok", tool_calls=None, reasoning_content=None))

    class _Chat:
        completions = _Completions()

    class _Client:
        chat = _Chat()

    class _FakeLLM:
        model = "fake-model"
        provider = "fake"
        timeout = 5
        max_retries = 3

        def _get_client(self):
            return _Client()

        def _is_openai_compat(self):
            return True

        def chat(self, messages, system_prompt="", max_tokens=16, temperature=0.0):
            # 降级分支：真实实现会走 `_chat_with_retry`（error_handler 4 次重试）。
            # 这里直接抛错，把放大源隔离到被测的那一层。
            http_calls["n"] += 1
            raise RuntimeError("simulated fallback chat failure")

    return _FakeLLM()


class TestEndToEndRetryAmplification:
    """端到端：真实 `ToolCallingService` 循环 + 打桩 sleep 的 HTTP 计数器

    【实测校准 —— 断言按**测到的**语义写，不按直觉写】
        `tool_calling` 的工具循环重试本身是 `for retry_attempt in range(3)`
        硬编码 3 次。因此单轮里即使预算很大，本层最多也只打 3 次 HTTP；
        预算的作用是**在 3 次之前截断**（预算 1 ⇒ 2 次后即 break 并置 denied）。

        另有一处**必须单独计入**的调用：首轮 LLM 失败后会走「降级为纯文本」
        分支 `self._current_llm.chat(...)`，那是一次**额外的** HTTP 尝试
        （真实实现里它落到 `llm_service._chat_with_retry` ⇒ error_handler 的
        4 次重试 —— 这正是 3×4 相乘的来源）。本测试的假 LLM 让该分支直接抛错，
        把放大源隔离在被测层之外，故总次数 = 工具循环次数 + 1。
    """

    def _run(self, monkeypatch, budget_max_retries):
        import agent.tool_calling as tc
        from agent.timeout_budget import RetryBudget, use_budget, reset_budget

        http_calls = {"n": 0}
        slept = []
        monkeypatch.setattr(tc.time, "sleep", lambda s: slept.append(s))
        monkeypatch.setenv("CP_RETRY_DEADLINE_SEC", "0")

        caller = tc.ToolCallingService(_build_fake_llm(http_calls), max_rounds=0,
                                       task_timeout=0)
        budget = RetryBudget(max_retries=budget_max_retries, deadline_sec=0)
        token = use_budget(budget)
        try:
            # 上游持续失败时 chat_with_steps 最终会抛 ToolCallError（降级也失败）
            # —— 这是**预期**的终态，不是测试错误；计数已在抛错前完成。
            try:
                caller.chat_with_steps(messages=[{"role": "user", "content": "hi"}],
                                       tools_whitelist=[])
            except Exception as exc:      # noqa: BLE001
                print(f"  [预期终态] chat_with_steps 抛出 {type(exc).__name__}")
        finally:
            reset_budget(token)
        return http_calls["n"], budget, slept

    def test_http_calls_are_truncated_by_budget(self, monkeypatch):
        """★硬要求：上游**持续失败**时总 HTTP 次数被预算截断（附实测输出）"""
        calls, budget, slept = self._run(monkeypatch, budget_max_retries=1)
        print(f"\n[E1e 实测/预算=1] 总 LLM HTTP 次数 = {calls}, "
              f"used={budget.used}, denied={budget.denied}, sleeps={slept}")

        # 预算 1 ⇒ 工具循环应在第 2 次尝试后即被截断（而不是走满 range(3)）
        assert budget.denied >= 1, "预算应拒绝过重试（截断发生的直接证据）"
        # 总次数 = 工具循环(1 首次 + 1 预算内重试) + 降级分支(1) = 3
        assert calls <= 3, f"预算=1 时总 HTTP 应 ≤3，实际 {calls}"
        # 抖动已生效（sleep 实参带 ±10% 抖动，不等于确定的 1.0）
        assert slept, "应发生过至少一次退避等待"
        assert all(0.8 <= s <= 1.2 for s in slept), f"退避应带抖动，实际 {slept}"

    def test_larger_budget_allows_more_calls_monotonic(self, monkeypatch):
        """单调性反证：预算放大 ⇒ 允许更多重试（证明次数确由预算决定，
        而不是恒等于某个写死的数字）"""
        tight, _b1, _s1 = self._run(monkeypatch, budget_max_retries=1)
        loose, _b2, _s2 = self._run(monkeypatch, budget_max_retries=6)
        print(f"\n[E1e 反证] 预算=1 → {tight} 次；预算=6 → {loose} 次")
        assert loose > tight, (
            f"预算放宽后应允许更多重试（{loose} 应 > {tight}）")
        # 上限由工具循环的 range(3) + 降级分支 1 次封顶
        assert loose <= 4, f"总次数应被 range(3)+降级 封顶，实际 {loose}"

    def test_three_layer_nesting_bounded_by_single_budget(self):
        """★三层嵌套（外层循环 3 × 内层 error_handler 5）用一份预算收敛

        模拟 `tool_calling`（外层，3 次逻辑调用）→ `error_handler.execute_with_retry`
        （内层，max_retries=4 ⇒ 原本每次逻辑调用重试 5 次）的相乘结构。

        【预算的**准确**语义（实测校准，勿按直觉写断言）】
            预算计的是**重试**，不是逻辑调用。因此：
                总尝试 = 逻辑调用数 + min(各层请求的重试总数, 预算)
            实测：外层 3 次逻辑调用、预算 4 ⇒ 3 + 4 = **7** 次
            （外层第 1 次逻辑调用吃掉全部 4 个额度，后两次各只拿到首次尝试）。
            无预算时 = 3 × 5 = **15** 次。
            所以正确的判据是 `总尝试 ≤ 逻辑调用数 + 预算`，
            而不是「≤ 1 + 预算」——后者只在单次逻辑调用下成立。
            关键性质是**次数不再随层数相乘**（15 → 7，且与内层 max_retries 解耦）。
        """
        from agent.error_handler import ErrorHandler, RetryPolicy, RecoverableError
        from agent.timeout_budget import RetryBudget, use_budget, reset_budget

        OUTER = 3
        BUDGET = 4

        def run_nested(budget):
            handler = ErrorHandler()
            policy = RetryPolicy(max_retries=4, initial_delay=0.0)
            http = {"n": 0}

            def _one_attempt():
                http["n"] += 1
                raise RecoverableError("upstream down")

            token = use_budget(budget) if budget else None
            try:
                for _outer in range(OUTER):      # 外层：工具循环的 3 次逻辑调用
                    try:
                        handler.execute_with_retry(
                            _one_attempt, retry_policy=policy,
                            retryable_exceptions=(RecoverableError,))
                    except Exception:
                        pass
            finally:
                if token is not None:
                    reset_budget(token)
            return http["n"]

        baseline = run_nested(None)
        bounded = run_nested(RetryBudget(max_retries=BUDGET, deadline_sec=0))
        print(f"\n[E1e 三层实测] 无预算={baseline} 次, 预算={BUDGET} → {bounded} 次")

        # 反证基线：无预算时确为相乘（3 × 5 = 15）
        assert baseline == OUTER * (4 + 1), f"无预算应 3×5=15 次，实际 {baseline}"
        # 核心断言：总尝试 = 逻辑调用数 + 预算（不再随层数相乘）
        assert bounded <= OUTER + BUDGET, \
            f"有预算应 ≤{OUTER + BUDGET} 次，实际 {bounded}"
        # 且必须**严格优于**未收敛的基线（证明截断真的发生）
        assert bounded < baseline, f"预算未生效：{bounded} 不小于基线 {baseline}"
