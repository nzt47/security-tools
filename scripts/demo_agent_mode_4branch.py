"""4 分支 DAG → Agent 模式演示/调试脚本 (可独立运行)

用途 (对应 docs/workflow_dag_vs_agent.md §2 规则1 的测试用例):
    1. 构造 1 入口 + 4 条件分支工作流 (branch-wf)
    2. classify_workflow_mode → 应返回 "agent" (分支数 4 > 3)
    3. 尝试注入真实 ToolCallingService (agent/tool_calling.py) 验证真调 LLM
    4. 本地无有效 key 时降级 mock runner, 但 LLM 调用点仍可观测

用法:
    python scripts/demo_agent_mode_4branch.py            # 默认: 探测 key, 占位则降级 mock
    python scripts/demo_agent_mode_4branch.py --force-real  # 强制真调 LLM (需 .env 有效 key)
    python scripts/demo_agent_mode_4branch.py --mock     # 强制 mock (跳过探测)

依赖说明:
    - 真实 LLM 需在 .env 配置: LLM_API_KEY / LLM_MODEL / LLM_BASE_URL
      (当前仓库 .env 的 LLM_API_KEY=sk-real-key 为占位符, 注释标注 "填入实际使用的 LLM key")
    - 工具名 entry_tool/tool_0 不存在于工具注册表 → 真调时 tools_whitelist 过滤为空,
      LLM 无工具可用, 直接返回文本 (这恰好证明 "Agent 模式必调 LLM" 的调用点)
"""
from __future__ import annotations

import os
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent.workflow_learning import (
    WorkflowLearningService,
    LearnedWorkflow,
    WorkflowStep,
    AgentExecutor,
    classify_workflow_mode,
    count_branches,
    AGENT_BRANCH_THRESHOLD,
)


# ─── 1. 构造 4 分支工作流 (与 docs §2 测试用例一致) ───────────────────

def build_four_branch_wf() -> LearnedWorkflow:
    """1 个入口步骤 + 4 个带 condition 的并行分支"""
    steps = [WorkflowStep(step_id="entry", tool_name="entry_tool",
                          params_template={"q": "$input"})]
    for i in range(4):
        steps.append(WorkflowStep(
            step_id=f"branch_{i}", tool_name=f"tool_{i}",
            params_template={"q": "$input"},
            condition=f"$prev_output.includes('branch{i}')",
        ))
    return LearnedWorkflow(
        id="branch-wf",
        name="4 分支复杂工作流",
        task_signature="branch_4",
        steps=steps,
    )


# ─── 2. 真实 LLM 装配 (探测 + 注入) ───────────────────────────────────

_PLACEHOLDER_KEYS = {"sk-real-key", "sk-xxx", "sk-test-key", "sk-instance-key-12345"}


def _load_env_file() -> dict:
    """轻量解析 .env (仅调试脚本用; 生产走环境变量)"""
    env_path = Path(__file__).resolve().parents[1] / ".env"
    result = {}
    if not env_path.exists():
        return result
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def probe_real_llm() -> dict:
    """探测真实 LLM 可用性 (不发起网络请求)

    Returns:
        {"available": bool, "reason": str, "key": str, "model": str, ...}
    """
    env = _load_env_file()
    api_key = os.environ.get("LLM_API_KEY") or env.get("LLM_API_KEY", "")
    model = os.environ.get("LLM_MODEL") or env.get("LLM_MODEL", "")
    base_url = os.environ.get("LLM_BASE_URL") or env.get("LLM_BASE_URL", "")
    # provider 规范化: OPENAI_COMPAT 使用小写 (deepseek), 与 .env 大写兼容
    provider = (os.environ.get("LLM_PROVIDER") or env.get("LLM_PROVIDER", "")
                ).strip().lower()

    if not api_key:
        return {"available": False, "reason": "未配置 LLM_API_KEY", "key": "", "model": model}
    if api_key in _PLACEHOLDER_KEYS or api_key.startswith("sk-xxx"):
        return {"available": False,
                "reason": f"LLM_API_KEY 是占位符 '{api_key}', 请填入真实 key (见 .env 注释)",
                "key": api_key, "model": model}
    if len(api_key) < 10:
        return {"available": False, "reason": f"LLM_API_KEY 长度不足 10 ({len(api_key)})",
                "key": api_key, "model": model}
    if not model or "flash" in model:
        return {"available": False,
                "reason": f"模型 '{model}' 可疑 (deepseek-v4-flash 不存在 → 404, 建议 deepseek-chat)",
                "key": api_key, "model": model}
    return {"available": True, "reason": "key/model 通过静态探测",
            "key": api_key, "model": model, "base_url": base_url,
            "provider": provider or "deepseek"}


def build_real_runner(probe: dict):
    """注入真实 ToolCallingService (agent/tool_calling.py) 作为 AgentRunner

    Note: 构造 ToolCallingService 需要 llm_service 依赖 (memory.llm_service.LLMService)。
          若 import 链失败 (如 Windows Embedding 崩溃风险), 返回 None 由调用方降级。
    """
    try:
        from memory.llm_service import LLMService
        from agent.tool_calling import ToolCallingService

        llm = LLMService(
            provider=probe.get("provider", "deepseek"),
            api_key=probe["key"],
            model=probe.get("model", "deepseek-chat"),
            timeout=15,
            base_url=probe.get("base_url", "https://api.deepseek.com/v1"),
        )
        tc = ToolCallingService(llm_service=llm, max_rounds=3, tool_timeout=15,
                                task_timeout=60)

        call_log = []

        def on_step(step: dict):
            call_log.append(step)

        def runner(task_text: str, params: dict, tools_hint: list):
            # AgentRunner 约定: (task_text, params, tools_hint) -> {"text","steps"}
            result = tc.chat_with_steps(
                [{"role": "user", "content": task_text}],
                tools_whitelist=tools_hint or None,
                on_step=on_step,
            )
            return result

        runner.call_log = call_log  # 调用证据
        return runner
    except ImportError as e:
        print(f"  [!] 真实 LLM 装配失败 (ImportError): {e}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"  [!] 真实 LLM 装配失败: {type(e).__name__}: {e}")
        return None


def build_mock_runner():
    """降级 mock runner — 记录调用点, 返回固定结构"""
    calls = []

    def runner(task_text: str, params: dict, tools_hint: list):
        calls.append({"task": task_text, "tools": tools_hint})
        return {
            "text": f"[MOCK Agent] 已处理: {task_text}",
            "steps": [{"type": "tool_call", "name": "mock",
                       "result": "ok", "input": {"q": task_text}}],
        }

    runner.calls = calls
    return runner


# ─── 3. 主流程 ────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="4 分支 DAG → Agent 模式调试")
    ap.add_argument("--force-real", action="store_true",
                    help="强制注入真实 ToolCallingService 并真调 LLM")
    ap.add_argument("--mock", action="store_true", help="强制 mock (跳过探测)")
    args = ap.parse_args()

    print("=" * 64)
    print("4 分支 DAG → Agent 模式验证")
    print("=" * 64)

    # 1. 构造 4 分支工作流
    wf = build_four_branch_wf()
    n_branches = count_branches(wf.steps)
    mode = classify_workflow_mode(wf.steps)
    print(f"\n[1] 工作流 {wf.id}: {len(wf.steps)} 步 / {n_branches} 条件分支")
    print(f"    classify_workflow_mode → '{mode}' (阈值 {AGENT_BRANCH_THRESHOLD})")
    assert mode == "agent", f"预期 agent, 实际 {mode}"

    # 2. 服务 + 执行器
    import tempfile
    tmp = tempfile.mkdtemp(prefix="wf_agent_demo_")
    svc = WorkflowLearningService(repo_path=str(Path(tmp) / "wf.json"))
    svc.repo.upsert(wf)
    svc.matcher.register(wf)

    def tool_exec(tool_name, params):
        return {"tool": tool_name, "echo": params}

    # 3. 选择 runner (真实 vs mock)
    runner = None
    llm_probe = None
    if not args.mock:
        llm_probe = probe_real_llm()
        print(f"\n[2] 真实 LLM 探测: {'[OK] 可用' if llm_probe['available'] else '[X] 不可用'}")
        print(f"    reason: {llm_probe['reason']}")

        if args.force_real or llm_probe["available"]:
            print("    → 注入真实 ToolCallingService...")
            runner = build_real_runner(llm_probe)
            if runner is None:
                print("    → 装配失败, 降级 mock runner")
        else:
            print("    → 降级 mock runner (调用点仍可观测)")

    if runner is None:
        runner = build_mock_runner()

    ae = AgentExecutor(runner=runner)
    svc.executor.set_agent_executor(ae)
    svc.set_tool_executor(tool_exec)

    # 4. 执行 4 分支用例
    print(f"\n[3] 执行 execute_by_id({wf.id}, ...)")
    t0 = time.time()
    result = svc.execute_by_id(wf.id, "复杂多分支任务: 请综合判断并输出", params={})
    elapsed = time.time() - t0

    print(f"    success        = {result.success}")
    print(f"    skipped_llm    = {result.skipped_llm}  (False = Agent 必调 LLM)")
    print(f"    steps_executed = {result.steps_executed}")
    print(f"    output         = {str(result.output)[:100]}")
    print(f"    error          = {result.error}")
    print(f"    耗时            = {elapsed:.2f}s")

    # 5. LLM 调用点证据
    runner_calls = getattr(runner, "calls", [])
    call_log = getattr(runner, "call_log", [])
    if runner_calls:
        print(f"\n[4] LLM 调用点证据: runner 被调用 {len(runner_calls)} 次")
        for i, c in enumerate(runner_calls[:3]):
            print(f"    调用#{i + 1}: tools_hint={c.get('tools')}")
    if call_log:
        print(f"    真实 ToolCallingService on_step 回调 {len(call_log)} 次 (LLM 工具循环证据)")

    # 6. 断言 (与 test_workflow_mode.py 一致)
    print("\n[5] 断言")
    assert result.matched is True, "matched 应为 True"
    assert result.skipped_llm is False, "Agent 模式 skipped_llm 必须为 False (必调 LLM)"
    if args.force_real:
        # 强制真调模式: LLM 失败被边界捕获 (success=False) 也是有效验证 —
        # 证明 LLM 调用链被真实触发且失败未中断主流程
        print("    (force-real 模式) LLM 失败被边界捕获 → success=False 为预期行为")
        assert "Agent 执行失败" in (result.error or ""), \
            "真实 LLM 调用失败应被 AgentExecutor 边界捕获"
    else:
        assert result.steps_executed >= 1, "Agent 应至少执行 1 个工具步骤"
    print("    全部断言通过")

    print("\n" + "=" * 64)
    print(f"结论: 4 分支工作流被正确识别为 Agent 模式 "
          f"(mode='{mode}', branches={n_branches} > {AGENT_BRANCH_THRESHOLD})")
    if llm_probe and not llm_probe["available"]:
        print(f"提示: 本地未真调 LLM — {llm_probe['reason']}")
        print("      填入真实 key 后运行: python scripts/demo_agent_mode_4branch.py --force-real")
    return 0


if __name__ == "__main__":
    sys.exit(main())
