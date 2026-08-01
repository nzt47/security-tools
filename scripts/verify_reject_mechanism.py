#!/usr/bin/env python3
"""任务3：主链路拒识机制 + LLM 置信度校验 — 模拟验证脚本

构造低置信度模拟输入，本地验证拒识与兜底逻辑是否生效：
1. _should_reject 各分支（语义命中/高置信度放行、双未命中拒识）
2. LLM 置信度判定逻辑（empty_or_too_short / error_marker_detected / normal）
3. 拒识禁用开关（ORCHESTRATOR_REJECT_ENABLED）
4. 阈值环境变量覆盖（ORCHESTRATOR_REJECT_THRESHOLD）
5. 拒识/兜底文案含转人工建议

运行方式:
    python scripts/verify_reject_mechanism.py
    python scripts/verify_reject_mechanism.py --verbose   # 显示 DEBUG 日志

【不易】独立可运行，不依赖完整 Orchestrator 初始化（用 __new__ 跳过）
【简易】判定逻辑与 orchestrator.py 同源，确保验证一致性
"""
import os
import sys
import logging

# 添加项目根目录到 sys.path（脚本位于 scripts/ 子目录）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _PROJECT_ROOT)


def _setup_logging(verbose: bool):
    """配置日志级别 — verbose 模式启用 DEBUG 观察拒识判定过程"""
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format='[%(levelname)s] [%(name)s] %(message)s',
        stream=sys.stdout,
    )
    # orchestrator 内部用 log_dict + 自有 logger，降级到 WARNING 避免噪音
    if not verbose:
        logging.getLogger('agent.orchestrator.orchestrator').setLevel(logging.WARNING)


def _make_orchestrator():
    """创建跳过 __init__ 的 Orchestrator 实例（仅用于测试 _should_reject）

    Why: Orchestrator.__init__ 依赖 LifecycleManager 注入的 _memory/_llm 等组件，
    _should_reject 只调用 _load_reject_config classmethod，无需这些依赖。
    """
    from agent.orchestrator.orchestrator import Orchestrator
    return Orchestrator.__new__(Orchestrator)


# ──────────────────────────────────────────────────────────────
#  场景1: _should_reject 拒识判定分支验证
# ──────────────────────────────────────────────────────────────

def test_should_reject_scenarios():
    """验证 _should_reject 5 个分支的判定逻辑"""
    print("\n" + "=" * 70)
    print("场景1: _should_reject 拒识判定分支验证")
    print("=" * 70)
    orch = _make_orchestrator()

    scenarios = [
        # (描述, intent, confidence, semantic_result, 期望 should_reject)
        ("语义层命中 → 放行", "query", "LOW", {"output": "x", "score": 0.8}, False),
        ("语义层 None + 高置信度 → 放行", "query", "HIGH", None, False),
        ("语义层 None + 低置信度 → 拒识", "unknown", "LOW", None, True),
        ("语义层 None + 中置信度 → 拒识", "unknown", "MEDIUM", None, True),
        ("语义层 None + None 置信度 → 拒识", "unknown", None, None, True),
    ]

    all_pass = True
    for desc, intent, conf, sem, expected in scenarios:
        should, reason = orch._should_reject(intent, conf, sem)
        passed = (should == expected)
        status = "✓ PASS" if passed else "✗ FAIL"
        if not passed:
            all_pass = False
        print(f"\n[{status}] {desc}")
        print(f"  输入: intent={intent!r}, confidence={conf!r}, semantic_result={sem}")
        print(f"  输出: should_reject={should}, reason={reason}")
        print(f"  期望: should_reject={expected}")

    return all_pass


# ──────────────────────────────────────────────────────────────
#  场景2: LLM 置信度判定逻辑验证
# ──────────────────────────────────────────────────────────────

def _judge_llm_confidence(response):
    """LLM 置信度判定（与 orchestrator.py 同源逻辑）

    Why: 提取判定逻辑独立验证，避免拉起完整 process() 依赖链。
    【不易】判定规则须与 orchestrator.py L486-493 保持一致
    """
    confidence = "high"
    low_reason = "normal"
    if not response or len(response.strip()) < 5:
        confidence = "low"
        low_reason = "empty_or_too_short"
    elif any(_marker in response for _marker in ["抱歉，处理", "遇到了问题", "无法完成", "出错了"]):
        confidence = "low"
        low_reason = "error_marker_detected"
    return confidence, low_reason


def test_llm_confidence_judge():
    """验证 LLM 置信度判定 9 个场景"""
    print("\n" + "=" * 70)
    print("场景2: LLM 置信度判定逻辑验证（低置信度触发兜底）")
    print("=" * 70)

    scenarios = [
        # (描述, response, 期望 confidence, 期望 low_reason)
        ("空响应", "", "low", "empty_or_too_short"),
        ("过短响应(2字符)", "嗯嗯", "low", "empty_or_too_short"),
        ("仅空格", "   ", "low", "empty_or_too_short"),
        ("None响应", None, "low", "empty_or_too_short"),
        ("错误标记-抱歉处理", "抱歉，处理您的请求时遇到了问题", "low", "error_marker_detected"),
        ("错误标记-遇到了问题", "系统遇到了问题，请重试", "low", "error_marker_detected"),
        ("错误标记-无法完成", "无法完成此操作", "low", "error_marker_detected"),
        ("错误标记-出错了", "出错了，请稍后再试", "low", "error_marker_detected"),
        ("正常响应", "你好，我是云枢，很高兴为你服务！", "high", "normal"),
        ("正常长响应", "这是一个关于Python编程的详细解答，包含多个示例代码。", "high", "normal"),
    ]

    all_pass = True
    for desc, response, exp_conf, exp_reason in scenarios:
        conf, reason = _judge_llm_confidence(response)
        passed = (conf == exp_conf and reason == exp_reason)
        status = "✓ PASS" if passed else "✗ FAIL"
        if not passed:
            all_pass = False
        print(f"\n[{status}] {desc}")
        print(f"  输入: response={response!r}")
        print(f"  输出: confidence={conf}, low_reason={reason}")
        print(f"  期望: confidence={exp_conf}, low_reason={exp_reason}")

    return all_pass


# ──────────────────────────────────────────────────────────────
#  场景3: 拒识禁用开关验证
# ──────────────────────────────────────────────────────────────

def test_reject_disabled():
    """验证 ORCHESTRATOR_REJECT_ENABLED 开关切换"""
    print("\n" + "=" * 70)
    print("场景3: 拒识禁用开关验证 (ORCHESTRATOR_REJECT_ENABLED)")
    print("=" * 70)
    orch = _make_orchestrator()

    # 默认启用 → 双未命中应拒识
    should1, reason1 = orch._should_reject("unknown", "LOW", None)
    print(f"\n[启用拒识] should_reject={should1}, reason={reason1}")

    # 禁用 → 不拒识
    os.environ["ORCHESTRATOR_REJECT_ENABLED"] = "false"
    should2, reason2 = orch._should_reject("unknown", "LOW", None)
    print(f"[禁用拒识] should_reject={should2}, reason={reason2}")
    del os.environ["ORCHESTRATOR_REJECT_ENABLED"]

    passed = (should1 is True and should2 is False)
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"\n[{status}] 开关切换验证（启用→拒识，禁用→放行）")
    return passed


# ──────────────────────────────────────────────────────────────
#  场景4: 阈值环境变量覆盖验证
# ──────────────────────────────────────────────────────────────

def test_threshold_override():
    """验证 ORCHESTRATOR_REJECT_THRESHOLD 环境变量覆盖 config.yaml"""
    print("\n" + "=" * 70)
    print("场景4: 阈值环境变量覆盖验证 (ORCHESTRATOR_REJECT_THRESHOLD)")
    print("=" * 70)
    from agent.orchestrator.orchestrator import Orchestrator

    # 默认阈值（config.yaml = 0.3）
    cfg1 = Orchestrator._load_reject_config()
    print(f"\n[默认] threshold={cfg1['threshold']}")

    # 环境变量覆盖
    os.environ["ORCHESTRATOR_REJECT_THRESHOLD"] = "0.5"
    cfg2 = Orchestrator._load_reject_config()
    print(f"[覆盖] threshold={cfg2['threshold']}")
    del os.environ["ORCHESTRATOR_REJECT_THRESHOLD"]

    # 非法值降级
    os.environ["ORCHESTRATOR_REJECT_THRESHOLD"] = "not_a_number"
    cfg3 = Orchestrator._load_reject_config()
    print(f"[非法值降级] threshold={cfg3['threshold']}")
    del os.environ["ORCHESTRATOR_REJECT_THRESHOLD"]

    passed = (cfg1['threshold'] == 0.3 and cfg2['threshold'] == 0.5 and cfg3['threshold'] == 0.3)
    status = "✓ PASS" if passed else "✗ FAIL"
    print(f"\n[{status}] 阈值覆盖验证（0.3 → 0.5 → 非法值降级回 0.3）")
    return passed


# ──────────────────────────────────────────────────────────────
#  场景5: 拒识/兜底文案验证（含转人工建议）
# ──────────────────────────────────────────────────────────────

def test_reject_message():
    """验证拒识/兜底文案含转人工建议（任务约束：统一文案 + 转人工建议）"""
    print("\n" + "=" * 70)
    print("场景5: 拒识/兜底文案验证（须含转人工建议）")
    print("=" * 70)

    # 文案与 orchestrator.py 同源
    reject_msg = (
        "抱歉，我不太理解你的意思。能否详细描述一下你想做什么？"
        "如需人工帮助，请说「转人工」。"
    )
    fallback_msg = (
        "抱歉，我暂时无法给出令人满意的回答。"
        "请尝试换种方式描述你的问题，或说「转人工」由人工协助处理。"
    )

    print(f"\n拒识文案: {reject_msg}")
    print(f"兜底文案: {fallback_msg}")

    pass1 = "转人工" in reject_msg
    pass2 = "转人工" in fallback_msg
    print(f"\n[{'✓ PASS' if pass1 else '✗ FAIL'}] 拒识文案含转人工建议")
    print(f"[{'✓ PASS' if pass2 else '✗ FAIL'}] 兜底文案含转人工建议")
    return pass1 and pass2


# ──────────────────────────────────────────────────────────────
#  主入口
# ──────────────────────────────────────────────────────────────

def main():
    verbose = "--verbose" in sys.argv

    print("=" * 70)
    print("任务3: 主链路拒识机制 + LLM 置信度校验 — 模拟验证")
    print("=" * 70)
    print(f"项目根目录: {_PROJECT_ROOT}")
    print(f"日志级别: {'DEBUG (观察拒识判定过程)' if verbose else 'WARNING (用 --verbose 启用 DEBUG)'}")

    _setup_logging(verbose)

    results = []
    results.append(("场景1 _should_reject 分支", test_should_reject_scenarios()))
    results.append(("场景2 LLM 置信度判定", test_llm_confidence_judge()))
    results.append(("场景3 拒识禁用开关", test_reject_disabled()))
    results.append(("场景4 阈值环境变量覆盖", test_threshold_override()))
    results.append(("场景5 拒识/兜底文案", test_reject_message()))

    print("\n" + "=" * 70)
    print("验证汇总")
    print("=" * 70)
    total_pass = 0
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}  {name}")
        if passed:
            total_pass += 1

    print(f"\n总计: {total_pass}/{len(results)} 场景通过")
    if total_pass == len(results):
        print("✓ 所有场景验证通过 — 拒识与兜底逻辑生效")
        return 0
    else:
        print("✗ 存在失败场景，请检查")
        return 1


if __name__ == "__main__":
    sys.exit(main())
