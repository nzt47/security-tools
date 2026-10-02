#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""进程内验证：技能检索业务指标埋点是否**真的出现在导出文本里**。

【为什么必须做这个验证】
    BusinessMetricsCollector.export_prometheus() 只导出 BUSINESS_METRICS_DEFINITIONS
    里"已登记"的指标名。历史上出现过"埋点写了、实例隔离了 / 名字没登记"两种静默丢失
    （交付报告 §9.18/§9.19），因此每个指标都必须打印出**原始样本行**才算数。

【验证内容】
    导入被测模块 → 触发真实代码路径 → get_business_metrics_collector().export_prometheus()
    → 打印该指标名对应的样本行。

用法（仓库根目录）：
    python scripts/verify_skill_retrieval_metrics.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = 0
FAIL = 0
EVIDENCE = {}


def _line(text: str, name: str):
    """从导出文本里挑出某指标名的样本行（不含 # HELP/# TYPE）"""
    hits = [ln for ln in text.splitlines()
            if ln.startswith(name) and not ln.startswith("#")]
    return hits


def check(name: str, text: str, must_prefix: bool = True):
    global PASS, FAIL
    hits = _line(text, name)
    if hits:
        PASS += 1
        EVIDENCE[name] = hits
        print("  [OK]   %s" % name)
        for h in hits:
            print("         %s" % h)
    else:
        FAIL += 1
        print("  [MISS] %s  <-- 导出文本里没有该样本行！" % name)


def main() -> int:
    from agent.monitoring.business_metrics import (
        BUSINESS_METRICS_DEFINITIONS, get_business_metrics_collector,
    )

    collector = get_business_metrics_collector()
    collector.reset()

    print("=" * 78)
    print("A. TF-IDF 扫描 / 倒排索引 / 降级开关（agent/skills_mgmt/loader.py）")
    print("=" * 78)
    from agent.skills_mgmt.loader import SkillLoader, _tokenize

    loader = SkillLoader()
    index = {
        sid: {"id": sid, "name": sid, "description": "并列探针 tieprobezeta",
              "tags": [], "category": "test", "enabled": True}
        for sid in ["skillsynth%02d" % i for i in range(30, 0, -1)]
    }
    tokens = _tokenize("并列探针 tieprobezeta")
    # 第 1 次：不打截断（candidate_limit=0）—— 首次调用会真建一次倒排索引
    loader._tfidf_scan(index=index, query_tokens=tokens, enabled_only=True,
                       min_score=0.01, use_inverted_index=True, candidate_limit=0)
    # 第 2 次：candidate_limit=10 < 候选数 30 ⇒ 截断分支真实生效
    loader._tfidf_scan(index=index, query_tokens=tokens, enabled_only=True,
                       min_score=0.01, use_inverted_index=True, candidate_limit=10)

    print("  （另触发：use_inverted_index=False 全量遍历路径，验证 gauge 会翻到 0）")
    loader._tfidf_scan(index=index, query_tokens=tokens, enabled_only=True,
                       min_score=0.01, use_inverted_index=False, candidate_limit=0)
    print("  （再触发一次倒排路径，把 gauge 翻回 1，便于观察）")
    loader._tfidf_scan(index=index, query_tokens=tokens, enabled_only=True,
                       min_score=0.01, use_inverted_index=True, candidate_limit=10)

    print()
    print("=" * 78)
    print("B. query embedding LRU 缓存（agent/skills_mgmt/vector_adapter.py）")
    print("=" * 78)
    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

    class _FakeModel:
        """假模型：只回一个确定的归一化向量，避免真拉 BGE-m3"""

        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            import numpy as np
            v = np.ones(8, dtype="float32")
            return np.stack([v / np.linalg.norm(v) for _ in texts])

    from agent.skills_mgmt.file_store import SkillFileStore

    va = SkillVectorAdapter(file_store=SkillFileStore())
    va._st_backend = (_FakeModel(), None, None, None)
    q = "帮我解析 PDF 文件"
    va._encode_query_cached(q)   # 未命中（miss）
    va._encode_query_cached(q)   # 命中（hit）

    print()
    print("=" * 78)
    print("C. 技能匹配 fallback（agent/skills_mgmt/loader.py match()）")
    print("=" * 78)
    from types import SimpleNamespace

    loader2 = SkillLoader()
    loader2.fs = SimpleNamespace(load_metadata_index=lambda: index)
    # 强制向量腿不可用 ⇒ 走 match.vector_fallback_to_tfidf 降级分支
    loader2._try_vector_match = lambda **kw: None
    loader2.match("并列探针 tieprobezeta", use_vector=True)
    # 强制"要求精排但没开向量" ⇒ 走 match.reranker_not_applied 降级分支
    loader2.match("并列探针 tieprobezeta", use_vector=False, use_reranker=True)

    print()
    print("=" * 78)
    print("D. v6.2 negative_intent 检测器（agent/skills_mgmt/negative_intent_detector.py）")
    print("=" * 78)
    from agent.skills_mgmt.negative_intent_detector import NegativeIntentDetector

    class _StubAdapter:
        """prototype 与 query 用同一个向量 ⇒ 相似度恒为 1.0，必然过阈值"""

        def encode_query(self, query):
            import numpy as np
            v = np.ones(8, dtype="float32")
            return v / np.linalg.norm(v)

    # D-1 失败路径：prototype 文件不存在 ⇒ detector_failed{reason=prototypes_not_found}
    bad = NegativeIntentDetector(vector_adapter=_StubAdapter(),
                                 prototypes_path=os.path.join(tempfile.gettempdir(),
                                                              "no_such_prototypes_9527.json"))
    bad.detect("今天天气怎么样", tid="verify-fail", t0=0.0)

    # D-2 成功路径：真加载 tests/eval/negative_intent_prototypes.json，命中 ⇒ rejected
    good = NegativeIntentDetector(vector_adapter=_StubAdapter())
    good.detect("今天天气怎么样", tid="verify-hit", t0=0.0)

    print()
    print("=" * 78)
    print("E. Reranker 耗时（agent/skills_mgmt/reranker.py:662 的既有埋点）")
    print("=" * 78)
    print("  【说明】rerank.py 的 emit_metric 调用已存在（L662），此处按**完全相同的参数**")
    print("          直接调用埋点函数，验证的是'登记后能否导出'，而非重新跑一次推理。")
    from agent.skills_mgmt.observability import emit_metric

    emit_metric("yunshu_rerank_duration_ms", value=258.0, kind="histogram",
                labels={"backend": "onnx", "success": "true"})
    emit_metric("yunshu_reranker_completed_total", value=1, kind="counter",
                labels={"backend": "onnx"})

    text = collector.export_prometheus()

    print()
    print("=" * 78)
    print("F. /api/business/prometheus 导出文本中的样本行（原始输出）")
    print("=" * 78)
    for name in [
        "tfidf_scan_candidate_limit_applied_total",
        "tfidf_scan_candidate_truncated_total",
        "tfidf_scan_candidate_total_total",
        "query_cache_hit_rate",
        "query_cache_misses_total",
        "inverted_index_built_total",
        "skill_use_inverted_index",
        "skill_total_count",
        "skill_candidate_limit_current",
        "yunshu_skill_match_fallback_total",
        "yunshu_negative_intent_detector_failed_total",
        "yunshu_negative_intent_duration_ms",
        "yunshu_rerank_duration_ms",
    ]:
        check(name, text)

    print()
    print("=" * 78)
    print("G. SafeFileReader 读取耗时（agent/monitoring/prometheus.py:601 既有定义）")
    print("=" * 78)
    import json as _json
    from prometheus_client import CollectorRegistry, generate_latest

    from utils.file_reader import SafeFileReader

    fpath = os.path.join(tempfile.gettempdir(), "verify_sfr_9527.jsonl")
    with open(fpath, "w", encoding="utf-8") as f:
        f.write(_json.dumps({"role": "user", "content": "hi"}, ensure_ascii=False) + "\n")
        f.write("{ 坏行 \n")
    SafeFileReader(fpath).read_json_lines(required_fields=["role", "content"])
    _sfr = "yunshu_safe_file_reader_read_duration_seconds"
    _default_reg = generate_latest().decode("utf-8")
    _fresh_reg = generate_latest(CollectorRegistry()).decode("utf-8")
    for label, blob in (("默认 REGISTRY（generate_latest()）", _default_reg),
                        ("全新空 REGISTRY（routes_logging /metrics 实际用法）", _fresh_reg)):
        hits = [ln for ln in blob.splitlines()
                if ln.startswith(_sfr + "_bucket") or ln.startswith(_sfr + "_count")]
        print("  -- %s: %d 行命中" % (label, len(hits)))
        for h in hits[:2]:
            print("       %s" % h)
    _biz = _line(text, _sfr)
    print("  -- 业务指标导出 /api/business/prometheus: %d 行命中" % len(_biz))

    print()
    print("=" * 78)
    print("H. 登记表自检：上述指标是否都在 BUSINESS_METRICS_DEFINITIONS 里")
    print("=" * 78)
    for name in EVIDENCE:
        reg = "已登记" if name in BUSINESS_METRICS_DEFINITIONS else "**未登记**"
        print("  %-52s %s" % (name, reg))

    print()
    print("=" * 78)
    print("结果：PASS=%d FAIL=%d" % (PASS, FAIL))
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
