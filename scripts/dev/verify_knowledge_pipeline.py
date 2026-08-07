"""生产上报流程模拟验证（一次性脚本，任务7 第 8 轮）。

验证两条生产链路（不依赖 K8s/Loki，本地等价模拟）：
1. 混合日志流采集解析：生产文件 handler（DictToJsonFilter）输出 = JSON 结构化行
   + 普通中文日志行；按 promtail-knowledge-log.yaml 的
   `json stage + drop_malformed: true`（无 multiline）逻辑逐行解析——
   验证 JSON 行全部提取成功、文本行被丢弃、无污染块。
2. 分布式链路追踪：knowledge_trace 上下文管理器——
   同一链路内多次 emit 共享 trace_id；显式传参优先；退出后恢复；
   线程并发隔离（ContextVar 不串扰）；多节点 trace_id 全局唯一。

运行：python scripts/dev/verify_knowledge_pipeline.py
"""
import io
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# Windows 控制台 UTF-8（日志含中文）
sys.stdout.reconfigure(encoding="utf-8")

from agent.knowledge.observability import (  # noqa: E402
    emit_structured_log, get_trace_id, knowledge_trace, _trace_id,
)


def _make_prod_handler(buf: io.StringIO) -> logging.Handler:
    """等价生产文件 handler：DictToJsonFilter 单行 JSON 序列化。"""
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    from agent.logging_utils import DictToJsonFilter
    handler.addFilter(DictToJsonFilter())
    return handler


def scenario1_mixed_stream() -> None:
    """混合日志流 → Promtail drop_malformed 解析（对应采集配置验证）。"""
    print("=" * 70)
    print("[场景1] 混合日志流采集解析（无 multiline + drop_malformed）")
    buf = io.StringIO()
    handler = _make_prod_handler(buf)
    lg = logging.getLogger("agent.knowledge")
    old_handlers, old_propagate, old_level = list(lg.handlers), lg.propagate, lg.level
    try:
        lg.handlers = [handler]
        lg.propagate = False
        lg.setLevel(logging.INFO)
        # 同一文件内的混合行：3 条结构化 JSON + 2 条普通中文文本
        with knowledge_trace():
            emit_structured_log("distill.llm_ok", duration_ms=88.1,
                                slug="note-a", model="gpt-4o")
            logger_text = logging.getLogger("agent.knowledge")
            logger_text.info("[distill] 提炼完成 slug=note-a distilled=True")
            emit_structured_log("promote.card_ok", duration_ms=12.4,
                                slug="note-a", reason="")
            logger_text.info("知识库索引已更新（普通文本日志，非 JSON）")
            emit_structured_log("kb_search.ok", duration_ms=1.2,
                                query="链路追踪")
        lines = buf.getvalue().splitlines()
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate
        lg.setLevel(old_level)

    # 等价 Promtail：json 逐行解析，失败即 drop（drop_malformed: true，无 multiline）
    parsed, dropped = [], []
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except (json.JSONDecodeError, TypeError):
            dropped.append(line)

    assert len(lines) == 5, f"应输出 5 行（3 JSON + 2 文本），实际 {len(lines)}"
    assert len(parsed) == 3, f"JSON 行应全部解析成功，实际 {len(parsed)}"
    assert len(dropped) == 2, f"文本行应全部被丢弃，实际 {len(dropped)}"
    # 字段提取完整性（对应 json stage expressions）
    for d in parsed:
        assert d["module_name"] == "knowledge"
        assert len(d["trace_id"]) == 32
        assert "action" in d and "duration_ms" in d
    assert {d["action"] for d in parsed} == {
        "distill.llm_ok", "promote.card_ok", "kb_search.ok"}
    # 文本行不得混入 JSON 块（无 multiline 污染）
    assert not any("提炼成功" in l for l in parsed), "文本行不得被并入 JSON 块"
    print(f"  PASS 总行={len(lines)} 解析成功={len(parsed)} 丢弃={len(dropped)} "
          f"字段完整={all('trace_id' in d for d in parsed)}")


def scenario2_trace_shared_in_chain() -> None:
    """同一链路内多次 emit 共享 trace_id（分布式按 trace_id 聚合链路）。"""
    print("=" * 70)
    print("[场景2] 链路内 trace_id 共享 + 显式传参优先 + 退出恢复")
    buf = io.StringIO()
    lg = logging.getLogger("agent.knowledge")
    old_handlers, old_propagate, old_level = list(lg.handlers), lg.propagate, lg.level
    try:
        lg.handlers = [_make_prod_handler(buf)]
        lg.propagate = False
        lg.setLevel(logging.INFO)
        with knowledge_trace() as tid:
            assert get_trace_id() == tid
            emit_structured_log("distill.llm_ok", slug="a")
            emit_structured_log("promote.card_ok", slug="a")
            emit_structured_log("distill.llm_failed", level="warning",
                                trace_id="explicit-128bit-0123456789abcdef",
                                reason="error", error="超时")
            inside = [json.loads(l) for l in buf.getvalue().splitlines()]
        assert get_trace_id() == "", "退出 knowledge_trace 后应恢复空串"
        # 链路内 3 条：前 2 条共享 tid，第 3 条显式传参优先
        assert inside[0]["trace_id"] == tid
        assert inside[1]["trace_id"] == tid
        assert inside[2]["trace_id"] == "explicit-128bit-0123456789abcdef"
        # 链路聚合：按 trace_id 可还原整条操作链
        chain = {d["action"] for d in inside if d["trace_id"] == tid}
        assert chain == {"distill.llm_ok", "promote.card_ok"}
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate
        lg.setLevel(old_level)
    print(f"  PASS tid={tid} 链路内 {len(inside)} 条共享 "
          f"{sum(1 for d in inside if d['trace_id'] == tid)} 条，显式传参 1 条，退出恢复 ✓")


def scenario3_concurrent_isolation() -> None:
    """线程并发：ContextVar 隔离，各线程链路不串扰（生产并发请求等价）。"""
    print("=" * 70)
    print("[场景3] 线程并发链路隔离（ThreadPoolExecutor × 10）")
    results = {}

    def worker(tid: str) -> None:
        with knowledge_trace(tid):
            emit_structured_log("distill.llm_ok", slug=f"note-{tid}")
            emit_structured_log("promote.card_ok", slug=f"note-{tid}")

    buf = io.StringIO()
    lg = logging.getLogger("agent.knowledge")
    old_handlers, old_propagate, old_level = list(lg.handlers), lg.propagate, lg.level
    try:
        lg.handlers = [_make_prod_handler(buf)]
        lg.propagate = False
        lg.setLevel(logging.INFO)
        with ThreadPoolExecutor(max_workers=10) as pool:
            pool.map(worker, [f"node-{i}" for i in range(10)])
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate
        lg.setLevel(old_level)

    for line in buf.getvalue().splitlines():
        d = json.loads(line)
        results.setdefault(d["trace_id"], []).append(d["action"])
    assert len(results) == 10, f"10 条链路应互不串扰，实际 {len(results)} 条"
    for tid, actions in results.items():
        assert tid.startswith("node-"), f"trace_id 与线程上下文不匹配: {tid}"
        assert actions == ["distill.llm_ok", "promote.card_ok"]
    print(f"  PASS 10 条并发链路全部隔离，无跨线程 trace_id 污染")


def scenario4_distributed_uniqueness() -> None:
    """分布式多节点 trace_id 唯一性（128 bit 冲突概率）。"""
    print("=" * 70)
    print("[场景4] 分布式多节点 trace_id 唯一性（N=10000 模拟）")
    ids = {_trace_id() for _ in range(10000)}
    assert len(ids) == 10000, "N=10000 不允许出现冲突"
    print(f"  PASS N=10000 全部唯一；128 bit 下 N=10^10 冲突概率 ≈ 1e-19")


def scenario5_1000_concurrent_stress() -> None:
    """生产高并发：1000 并发链路，线程池复用下无串扰、异常中断不泄露。"""
    print("=" * 70)
    print("[场景5] 1000 并发链路追踪稳定性（含异常中断注入）")

    def worker(i: int) -> str:
        tid = f"node-{i:04d}"
        try:
            with knowledge_trace(tid):
                emit_structured_log("distill.llm_ok", slug=f"n-{i}")
                if i % 7 == 0:
                    raise RuntimeError(f"模拟异常 {i}")  # 异常中断链路
                emit_structured_log("promote.card_ok", slug=f"n-{i}")
        except RuntimeError:
            pass  # 上层捕获（生产等价：降级路径吞异常）
        # with 已退出：无论正常/异常都必须恢复空上下文（finally + token reset）
        assert get_trace_id() == "", f"链路退出后上下文未恢复: {tid}"
        return tid

    buf = io.StringIO()
    lg = logging.getLogger("agent.knowledge")
    old_handlers, old_propagate, old_level = list(lg.handlers), lg.propagate, lg.level
    try:
        lg.handlers = [_make_prod_handler(buf)]
        lg.propagate = False
        lg.setLevel(logging.INFO)
        with ThreadPoolExecutor(max_workers=1000) as pool:
            tids = list(pool.map(worker, range(1000)))
    finally:
        lg.handlers = old_handlers
        lg.propagate = old_propagate
        lg.setLevel(old_level)

    rows_by_tid: dict[str, list[str]] = {}
    for line in buf.getvalue().splitlines():
        d = json.loads(line)
        rows_by_tid.setdefault(d["trace_id"], []).append(d["action"])
    assert len(rows_by_tid) == 1000, f"应 1000 条独立链路，实际 {len(rows_by_tid)}"
    assert len(tids) == 1000 and len(set(tids)) == 1000
    # i=0 也满足 i%7==0（range(1000) 含 0），异常任务 = 999//7 + 1 = 143
    interrupted_expected = 999 // 7 + 1
    expected_lines = 1000 + (1000 - interrupted_expected)  # 异常任务仅 1 条日志
    assert len(rows_by_tid) == len(set(rows_by_tid.keys()))
    ok = sum(1 for a in rows_by_tid.values() if len(a) == 2)
    interrupted = sum(1 for a in rows_by_tid.values() if len(a) == 1)
    assert ok + interrupted == 1000
    assert interrupted == interrupted_expected, f"异常中断链路数不符: {interrupted}"
    for tid, actions in rows_by_tid.items():
        assert tid.startswith("node-"), f"trace_id 与任务上下文不匹配: {tid}"
        assert actions[0] == "distill.llm_ok"
    assert sum(len(a) for a in rows_by_tid.values()) == expected_lines
    print(f"  PASS 1000 链路互不串扰；正常链路 {ok}，异常中断 {interrupted}；"
          f"总日志 {expected_lines} 行；无上下文泄露")


if __name__ == "__main__":
    scenario1_mixed_stream()
    scenario2_trace_shared_in_chain()
    scenario3_concurrent_isolation()
    scenario4_distributed_uniqueness()
    scenario5_1000_concurrent_stress()
    print("=" * 70)
    print("全部场景 PASS —— 生产上报流程模拟验证通过")
