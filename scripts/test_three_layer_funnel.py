#!/usr/bin/env python3
"""三层漏斗路由流量分配测试

模拟云枢 orchestrator 意图识别三层漏斗:
  InputGuard → 规则层(WorkflowEngine) → 模板层(IntentRouter)
  → 语义层(SkillLoader RRF) → 拒识检查 → LLM 兜底(标记,不实际调用)

用途:
1. 验证流量分配是否符合三层漏斗设计占比
2. 验证降级兜底机制（规则层/语义层失效时流量转移）
3. 观察省略句 DST 补全效果

用法:
  python scripts/test_three_layer_funnel.py --scenario all --no-vector
  python scripts/test_three_layer_funnel.py --scenario normal --no-vector --verbose

场景:
  normal      三层全开（基线）
  rule_off    规则层失效(ORCHESTRATOR_RULE_LAYER_ENABLED=false)
  semantic_off 语义层失效(ORCHESTRATOR_SEMANTIC_LAYER_ENABLED=false)
  both_off    规则+语义双失效（验证 LLM 全量兜底）
  all         依次运行以上 4 个场景对比
"""

import argparse
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

# 【日志埋点】DST 修复点专用 logger（main() 中配独立 handler 保证默认可见）
_LOGGER = logging.getLogger("three_layer_funnel")

# ════════════════════════════════════════════════════════════════════
#  测试数据集
#  期望标注仅用于人工核对，不参与断言（脚本输出路由实际结果）
# ════════════════════════════════════════════════════════════════════

TEST_SAMPLES: List[Tuple[str, str]] = [
    # ── 常规意图（期望：规则层或模板层命中）──
    ("现在几点", "rule_expected"),            # WorkflowEngine check_time
    ("今天日期", "rule_expected"),            # WorkflowEngine check_date
    ("你好", "rule_expected"),                # WorkflowEngine greeting
    # ── 规则层关键词变体（2026-08-05 扩充后新增，验证拦截率提升）──
    ("现在几点了", "rule_expected"),          # check_time 变体
    ("今天几号", "rule_expected"),            # check_date 变体
    ("你好呀", "rule_expected"),              # check_health 变体
    ("早上好", "rule_expected"),              # greeting 变体
    ("谢谢啦", "rule_expected"),              # thanks 变体
    ("拜拜", "rule_expected"),                # farewell 变体
    ("没问题", "rule_expected"),              # confirmation 变体
    ("你是谁", "template_expected"),          # IntentRouter identity
    ("你能做什么", "template_expected"),      # IntentRouter capability
    # ── 复杂语义意图（期望：语义层命中，含 PDF/财报/Excel/文档总结）──
    ("帮我把这份PDF文件转成Word文档然后压缩发到邮箱", "complex_semantic"),
    ("请分析这份财报数据并生成可视化图表", "complex_semantic"),
    ("帮我解析一下这个Excel表格里的数据", "complex_semantic"),
    ("总结一下这个文件夹里的所有文档内容", "complex_semantic"),
    # ── 连续省略句（期望：DST 补全后继承上一轮锚点"总结一下...",
    #    连续 3 条省略句不再相互覆盖上下文）──
    ("然后呢", "ellipsis"),
    ("那个呢", "ellipsis"),
    ("再来一个", "ellipsis"),
    # ── 未知/过短输入（期望：拒识或落 LLM）──
    ("哦", "reject_expected"),
    ("嗯嗯", "reject_expected"),
]


# 场景定义：规则层开关 / 语义层开关
SCENARIOS = {
    "normal":       (True, True),
    "rule_off":     (False, True),
    "semantic_off": (True, False),
    "both_off":     (False, False),
}

LAYER_NAMES = ["rule", "template", "semantic", "llm", "reject"]


class DialogStateStub:
    """最小 DST 状态（省略句补全）

    生产实现: agent/orchestrator/dialog_state.py
    测试侧复用真实 dialog_state.DialogState 的补全逻辑。
    """

    def __init__(self):
        self.last_keywords: Optional[List[str]] = None
        self.last_intent: Optional[str] = None
        self._impl = None
        try:
            from agent.orchestrator.dialog_state import DialogState
            self._impl = DialogState()
        except Exception:
            self._impl = None

    def is_ellipsis_query(self, text: str) -> bool:
        if self._impl is not None:
            return self._impl.is_ellipsis_query(text)
        return text in ("然后呢", "那个呢", "再来一个", "继续", "下一个")

    def resolve(self, text: str) -> Optional[str]:
        if self._impl is not None:
            return self._impl.resolve(text)
        return None

    def update(self, **kwargs):
        if "intent" in kwargs:
            self.last_intent = kwargs["intent"]
        if "keywords" in kwargs:
            self.last_keywords = list(kwargs["keywords"])
        if self._impl is not None:
            try:
                self._impl.update(**kwargs)
            except Exception:
                pass

    @property
    def turn_count(self) -> int:
        """委托真实 DialogState 的轮次计数（供日志埋点读取）"""
        return getattr(self._impl, "turn_count", 0) if self._impl is not None else 0


def simulate_one_turn(dst, text: str, *, semantic_enabled: bool) -> dict:
    """模拟单轮三层漏斗路由（不实际调用 LLM）

    Returns:
        路由决策 dict: {input, routing_input, final_layer, duration_ms,
                        detail, score, skill_id, matched}
    """
    result: Dict = {
        "input": text,
        "routing_input": text,
        "final_layer": "llm",
        "duration_ms": 0.0,
        "detail": "",
        "score": None,
        "skill_id": None,
        "matched": False,
    }
    t0 = time.perf_counter()

    # ── 第 0 步：DST 指代消解（省略句补全）──
    routing_input = text
    try:
        augmented = dst.resolve(text)
        if augmented:
            routing_input = augmented
            result["routing_input"] = augmented
            # 【日志埋点】省略句补全结果（sim=向量门控相似度，未启用时为 None）
            _sim = getattr(dst, "last_similarity", None)
            _LOGGER.info(
                "[埋点] 省略句补全: %r -> %r (sim=%s, last_keywords=%r, turn=%s)",
                text, augmented,
                ("%.4f" % _sim) if isinstance(_sim, float) else _sim,
                dst.last_keywords, dst.turn_count)
        # 更新 DST 关键词（供下一轮补全）
        # 【修复】连续省略句时序副作用：省略句不应覆盖上一轮真实查询的关键词，
        #        否则"然后呢"→"那个呢"时上下文被"然后呢"自身污染。
        #        仅非省略句才用 extract_keywords 刷新上下文。
        if not dst.is_ellipsis_query(text):
            from agent.orchestrator.message_handler import MessageHandler
            kw = MessageHandler.extract_keywords(text)
            _before_kw = list(dst.last_keywords or [])
            if kw:
                # 经 stub.update 同步到真实 DialogState（turn_count/关键词一起更新）
                dst.update(keywords=kw, user_input=text)
                _LOGGER.info(
                    "[埋点] 刷新上下文: %r -> %r (旧=%r, turn=%s)",
                    text, kw, _before_kw, dst.turn_count)
            else:
                # 无关键词短句（如"哦"/"嗯"）不刷新，保留上一轮锚点
                _LOGGER.info(
                    "[埋点] 无关键词不刷新上下文: %r (保留=%r, turn=%s)",
                    text, _before_kw, dst.turn_count)
        else:
            # 【修复核心】省略句不刷新关键词 → 连续省略句共享同一锚点
            _LOGGER.info(
                "[埋点] 省略句保留上下文: %r (last_keywords=%r, turn=%s)",
                text, dst.last_keywords, dst.turn_count)
    except Exception as _e:
        logging.getLogger(__name__).debug("DST 步骤异常(降级继续): %s", _e)

    # ── 第 1 步：规则层（WorkflowEngine）──
    rule_enabled = os.environ.get(
        "ORCHESTRATOR_RULE_LAYER_ENABLED", "true"
    ).strip().lower() in ("true", "1", "yes")
    if rule_enabled:
        try:
            from agent.workflow_engine.engine import WorkflowEngine
            from agent.workflow_engine.builtin_rules import register_builtin_rules
            # 【不易】与生产链路 lifecycle_manager.py 对齐：
            #        WorkflowEngine 默认不注册内置规则，须显式 register_builtin_rules
            _wf_engine = WorkflowEngine()
            if _wf_engine.registry.count() == 0:
                register_builtin_rules(_wf_engine.registry)
            wf_result = _wf_engine.try_match(routing_input)
        except Exception as _e:
            logging.getLogger(__name__).debug("规则层异常(降级继续): %s", _e)
            wf_result = None
        if wf_result is not None and getattr(wf_result, "matched", False):
            result.update({
                "final_layer": "rule",
                "detail": "规则命中: %s (conf=%.2f)" % (
                    getattr(wf_result, "intent", "?"),
                    getattr(wf_result, "confidence", 0.0)),
                "score": getattr(wf_result, "confidence", None),
                "matched": True,
            })
            result["duration_ms"] = (time.perf_counter() - t0) * 1000
            return result

    # ── 第 2 步：模板层（IntentRouter）──
    try:
        from agent.response_workflows import IntentRouter
        intent = IntentRouter.classify(routing_input)
        if intent is not None:
            result.update({
                "final_layer": "template",
                "detail": "模板命中: intent=%s" % intent,
                "skill_id": intent,
                "matched": True,
            })
            result["duration_ms"] = (time.perf_counter() - t0) * 1000
            return result
    except Exception as _e:
        logging.getLogger(__name__).debug("模板层异常(降级继续): %s", _e)

    # ── 第 3 步：语义层（SkillLoader RRF 匹配）──
    if semantic_enabled:
        try:
            from agent.skills_mgmt.service import get_skills_mgmt_service
            svc = get_skills_mgmt_service()
            mr = svc.loader.match(
                routing_input,
                top_k=3,
                enabled_only=True,
                min_score=float(os.environ.get("ORCHESTRATOR_SEMANTIC_MIN_SCORE", "0.3")),
                use_vector=not _NO_VECTOR,
                use_bm25=True,
                use_reranker=False,
                retrieval_weights=None,
                fusion_mode="rrf",
            )
            if mr is not None and getattr(mr, "served", None):
                top = mr.served[0]
                result.update({
                    "final_layer": "semantic",
                    "detail": "语义命中: skill=%s score=%.4f method=%s" % (
                        top.skill_id, top.score, getattr(mr, "retrieval_method", "rrf")),
                    "score": top.score,
                    "skill_id": top.skill_id,
                    "matched": True,
                })
                result["duration_ms"] = (time.perf_counter() - t0) * 1000
                return result
        except Exception as _e:
            logging.getLogger(__name__).debug("语义层异常(降级继续): %s", _e)

    # ── 第 4 步：拒识检查（过短/无意义 → reject）──
    if len(text.strip()) <= 1 or text.strip() in ("哦", "嗯嗯", "嗯"):
        result.update({
            "final_layer": "reject",
            "detail": "拒识: 输入过短或无意义",
            "matched": True,
        })
        result["duration_ms"] = (time.perf_counter() - t0) * 1000
        return result

    # ── 第 5 步：LLM 兜底（标记，不实际调用）──
    result.update({
        "final_layer": "llm",
        "detail": "LLM 兜底(模拟,未实际调用)",
    })
    result["duration_ms"] = (time.perf_counter() - t0) * 1000
    return result


# ════════════════════════════════════════════════════════════════════
#  连续省略句补全回归验证（todo 1 交付物）
#  验证目标：连续省略句必须继承最后一条真实查询(锚点)的关键词，
#            省略句之间不得相互覆盖上下文（DST 时序副作用修复点回归）
# ════════════════════════════════════════════════════════════════════
# (text, 期望补全前缀；None=期望不补全)
_DST_CHAIN: List[Tuple[str, Optional[str]]] = [
    ("帮我总结一下这个文件夹里的所有文档内容", None),   # 锚点（真实查询，刷新上下文）
    ("然后呢", "继续"),      # 接续句
    ("那个呢", "关于"),      # 指代句
    ("再来一个", "继续"),    # 接续句
    ("还有呢", "继续"),      # 接续句变体
    ("嗯", None),           # 非省略且无关键词 → 不刷新上下文
    ("那个呢", "关于"),      # 应仍继承锚点关键词（不被"嗯"污染）
    ("然后呢", "继续"),      # 结尾再验证接续
]


def run_dst_continuity_check() -> int:
    """连续省略句补全回归验证（修复点回归）

    与 simulate_one_turn 相同的状态回写逻辑：
    - 非省略句 + 有关键词 → update 刷新上下文
    - 非省略句 + 无关键词（"哦"/"嗯"）→ 不刷新，保留锚点
    - 省略句 → 不刷新（【修复】核心），连续省略句共享同一锚点

    Returns:
        FAIL 用例数（0=全部通过）
    """
    print("\n=== DST 连续省略句补全回归验证（修复点回归）===")
    dst = DialogStateStub()  # 独立实例，隔离场景间状态
    failures = 0
    for text, expect_prefix in _DST_CHAIN:
        augmented = dst.resolve(text)
        is_ellipsis = dst.is_ellipsis_query(text)
        # 状态回写（与 simulate_one_turn 第0步一致）
        if not is_ellipsis:
            from agent.orchestrator.message_handler import MessageHandler
            kw = MessageHandler.extract_keywords(text)
            if kw:
                dst.update(keywords=kw, user_input=text)

        _sim = getattr(dst, "last_similarity", None)
        sim_s = ("%.4f" % _sim) if isinstance(_sim, float) else str(_sim)
        ok, reason = True, ""
        if expect_prefix is None:
            if augmented is not None:
                ok, reason = False, "期望不补全但返回 %r" % augmented
        else:
            if augmented is None:
                ok, reason = False, "期望补全(前缀=%s)但返回 None" % expect_prefix
            elif not augmented.startswith(expect_prefix):
                ok, reason = False, "前缀不符: 期望 %r 实际 %r" % (
                    expect_prefix, (augmented or "")[:8])
        if not ok:
            failures += 1
        print("  [%s] %-22s -> %-44s sim=%s  ellipsis=%s turn=%d kw=%r  %s" % (
            "PASS" if ok else "FAIL", text, repr(augmented)[:44], sim_s,
            is_ellipsis, dst.turn_count, dst.last_keywords, reason))

    print("  " + "-" * 70)
    print("  连续省略句回归: %d 条, FAIL=%d" % (len(_DST_CHAIN), failures))
    if failures:
        print("  [FAIL] 存在未通过用例，请结合上方 [DST] 埋点日志排查")
    return failures


def run_scenario(name: str, rule_enabled: bool, semantic_enabled: bool,
                 dst, verbose: bool) -> dict:
    """运行一个场景，返回各层统计"""
    os.environ["ORCHESTRATOR_RULE_LAYER_ENABLED"] = "true" if rule_enabled else "false"
    if not semantic_enabled:
        os.environ["ORCHESTRATOR_SEMANTIC_LAYER_ENABLED"] = "false"
    else:
        os.environ["ORCHESTRATOR_SEMANTIC_LAYER_ENABLED"] = "true"

    counts = {k: 0 for k in LAYER_NAMES}
    rows = []
    for text, expected in TEST_SAMPLES:
        r = simulate_one_turn(dst, text, semantic_enabled=semantic_enabled)
        counts[r["final_layer"]] += 1
        rows.append((text, expected, r))
        if verbose:
            _log_verbose(r)

    total = len(TEST_SAMPLES)
    pct = {k: (v / total * 100.0 if total else 0.0) for k, v in counts.items()}
    print("场景: %s  样本数=%d" % (name, total))
    print("  rule=%.1f%% (%d) | template=%.1f%% (%d) | semantic=%.1f%% (%d) | "
          "llm=%.1f%% (%d) | reject=%.1f%% (%d)" % (
              pct["rule"], counts["rule"],
              pct["template"], counts["template"],
              pct["semantic"], counts["semantic"],
              pct["llm"], counts["llm"],
              pct["reject"], counts["reject"]))
    return {"name": name, "counts": counts, "pct": pct, "rows": rows}


def _log_verbose(r: dict) -> None:
    """逐样本明细输出（含 DST 补全结果）"""
    src = r["input"]
    rout = r["routing_input"]
    if rout != src:
        src = "%s → %s" % (src, rout)
    print("  %-45s %-9s %7.2fms  %s" % (
        src[:45], r["final_layer"], r["duration_ms"], r["detail"][:60]))


def main():
    global _NO_VECTOR
    ap = argparse.ArgumentParser(description="三层漏斗路由流量分配测试")
    ap.add_argument("--scenario", default="all",
                    choices=list(SCENARIOS.keys()) + ["all"])
    ap.add_argument("--no-vector", action="store_true",
                    help="跳过向量检索(use_vector=False)，避免模型加载耗时")
    ap.add_argument("--verbose", action="store_true",
                    help="输出逐样本明细与 DEBUG 日志")
    args = ap.parse_args()
    _NO_VECTOR = args.no_vector

    level = logging.DEBUG if args.verbose else logging.WARNING
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # 压缩第三方日志噪音
    for noisy in ("sentence_transformers", "urllib3", "httpx", "httpcore",
                  "datasets", "transformers"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # 【日志埋点】DST 修复点独立 handler：默认可见（不依赖 --verbose 的 DEBUG 级）
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    _dst_handler = logging.StreamHandler()
    _dst_handler.setLevel(logging.INFO)
    _dst_handler.setFormatter(logging.Formatter("[DST] %(message)s"))
    _LOGGER.addHandler(_dst_handler)

    # 加载 .env（配置单一数据源）
    try:
        from agent.env_config_manager import get_env_config_manager
        get_env_config_manager().reload()
    except Exception:
        pass

    dst = DialogStateStub()
    scenarios = [args.scenario] if args.scenario != "all" else list(SCENARIOS.keys())
    results = []
    for name in scenarios:
        rule_enabled, semantic_enabled = SCENARIOS[name]
        results.append(run_scenario(name, rule_enabled, semantic_enabled, dst, args.verbose))

    print("\n" + "=" * 70)
    print("降级兜底对比表（LLM 兜底占比）：")
    print("%-14s %6s %6s %6s %6s %6s" % ("场景", "rule", "tpl", "sem", "llm", "rej"))
    for r in results:
        p = r["pct"]
        print("%-14s %5.1f%% %5.1f%% %5.1f%% %5.1f%% %5.1f%%" % (
            r["name"], p["rule"], p["template"], p["semantic"], p["llm"], p["reject"]))
    print("=" * 70)
    print("验证要点:")
    print("  1. normal 场景下三层漏斗应拦截大部分流量，LLM 兜底接近 0%")
    print("  2. rule_off / semantic_off 时对应层流量应转移到后续层，LLM 占比递增")
    print("  3. both_off 时 LLM 兜底占比最高（验证降级兜底机制生效）")
    print("  4. 省略句样本应显示 DST 补全后的 routing_input（继承上一轮锚点）")

    # 连续省略句补全回归验证（todo 1）
    failures = run_dst_continuity_check()
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
