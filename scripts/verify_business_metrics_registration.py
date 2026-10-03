#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""登记完整性验证：agent/ 下每个 emit_metric 指标名都必须能从导出端点看到。

【为什么单独一个脚本】
    上一轮只验证了"技能检索 13 个指标真的出了样本行"。本轮补登记 72 个名字后，
    需要证明的不再是单个指标，而是**整张登记表的完整性**：83 个 emit_metric 名字全部
    能在 /api/business/prometheus 的导出文本里找到（否则就是"埋点写了但没人读"，告警恒不触发）。

【两段证据，分开标注，不混为一谈】
    A 段 · 真实触发：真跑代码路径（检索 / 文件读取 / Reranker 加载 / Prompt 优化 /
      工作流学习），打印**样本行**。能真跑的都真跑。
    B 段 · 调用点参数回放：对剩余名字，按 AST 扫描出的**调用点原样参数**
      （kind + labels）调用 emit_metric —— 只证明"名字已登记 + 类型与调用点一致 + 导出器
      输出该行"。它**不**证明调用点的业务逻辑被走到（那由各自的单测/集成测试负责），
      故在输出里显式标注 [REPLAY]。

用法（仓库根目录）：
    python scripts/verify_business_metrics_registration.py
"""
from __future__ import annotations

import ast
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT_ROOT = pathlib.Path(__file__).resolve().parent.parent / "agent"
NL = chr(10)

PASS = 0
FAIL = 0
REAL = []
REPLAY = []


def scan_emit_sites():
    """AST 扫描 agent/ 下所有 emit_metric 调用 → {name: {kind, label_sets, sites}}"""
    agg = {}
    dynamic = []
    for path in sorted(AGENT_ROOT.rglob("*.py")):
        rel = path.relative_to(AGENT_ROOT).as_posix()
        if rel == "monitoring/business_metrics.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "emit_metric"):
                continue
            site = "%s:%d" % (rel, node.lineno)
            if not node.args:
                dynamic.append(site)
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                dynamic.append(site)
                continue
            name = first.value
            kind = "counter"
            for kw in node.keywords:
                if kw.arg == "kind":
                    kind = kw.value.value if isinstance(kw.value, ast.Constant) else "<dynamic>"
            labels = None
            for kw in node.keywords:
                if kw.arg == "labels":
                    labels = [k.value for k in kw.value.keys if isinstance(k, ast.Constant)] if isinstance(kw.value, ast.Dict) else "<dynamic>"
            a = agg.setdefault(name, {"kind": kind, "label_sets": [], "sites": []})
            if labels not in a["label_sets"]:
                a["label_sets"].append(labels)
            a["sites"].append(site)
    return agg, dynamic


def effective_labels(label_set):
    """emit_metric 在既无 success 也无 failure 时会**自动补** success=true"""
    if label_set in (None, "<dynamic>"):
        base = []
    else:
        base = list(label_set)
    if "success" not in base and "failure" not in base:
        base.append("success")
    return {k: ("true" if k == "success" else "x") for k in base}


def lines_for(text, name):
    return [ln for ln in text.splitlines()
            if (ln.startswith(name + "{") or ln.startswith(name + " ") or ln.startswith(name + "_sum")
                or ln.startswith(name + "_count")) and not ln.startswith("#")]


def type_line(text, name):
    hits = [ln for ln in text.splitlines() if ln.startswith("# TYPE " + name + " ")]
    return hits[0] if hits else None


def main() -> int:
    global PASS, FAIL
    from agent.monitoring.business_metrics import (
        BUSINESS_METRICS_DEFINITIONS, get_business_metrics_collector,
    )
    from agent.skills_mgmt.observability import emit_metric

    agg, dynamic = scan_emit_sites()
    print("=" * 78)
    print("扫描结果：agent/ 下 emit_metric 指标名 %d 个；动态名字（无法静态判定）%d 处"
          % (len(agg), len(dynamic)))
    if dynamic:
        print("  [提示] 动态名字位置（本脚本不校验）：%s" % ", ".join(dynamic))
    print("=" * 78)

    collector = get_business_metrics_collector()
    collector.reset()

    # ── A 段：真实触发 ────────────────────────────────────────────────
    print(NL + "A 段 · 真实代码路径触发" + NL + "-" * 78)

    # A-1 技能检索（loader.match）
    from types import SimpleNamespace

    from agent.skills_mgmt.file_store import SkillFileStore
    from agent.skills_mgmt.loader import SkillLoader, _tokenize

    loader = SkillLoader()
    index = {sid: {"id": sid, "name": sid, "description": "并列探针 tieprobezeta",
                   "tags": [], "category": "test", "enabled": True}
             for sid in ["skillsynth%02d" % i for i in range(30, 0, -1)]}
    loader._tfidf_scan(index=index, query_tokens=_tokenize("并列探针 tieprobezeta"),
                       enabled_only=True, min_score=0.01, use_inverted_index=True,
                       candidate_limit=10)
    loader2 = SkillLoader()
    loader2.fs = SimpleNamespace(load_metadata_index=lambda: index)
    loader2._try_vector_match = lambda **kw: None
    loader2.match("并列探针 tieprobezeta", use_vector=True)
    print("  [真实] loader.match / _tfidf_scan 已执行")

    # A-1b query embedding LRU 缓存（vector_adapter）
    import numpy as _np

    from agent.skills_mgmt.vector_adapter import SkillVectorAdapter

    class _FakeModel:
        def encode(self, texts, normalize_embeddings=True, show_progress_bar=False):
            v = _np.ones(8, dtype="float32")
            return _np.stack([v / _np.linalg.norm(v) for _ in texts])

    va = SkillVectorAdapter(file_store=SkillFileStore())
    va._st_backend = (_FakeModel(), None, None, None)
    va._encode_query_cached("帮我解析 PDF 文件")   # miss
    va._encode_query_cached("帮我解析 PDF 文件")   # hit
    print("  [真实] SkillVectorAdapter._encode_query_cached() 已执行（miss + hit）")

    # A-2 元数据索引（file_store）
    SkillFileStore().load_metadata_index()
    print("  [真实] SkillFileStore().load_metadata_index() 已执行")

    # A-3 Reranker 加载与降级（不打真实模型：桩掉重量级后端，保留 emit 调用点）
    from agent.skills_mgmt.reranker import SkillReranker

    rr = SkillReranker()
    rr._model_name = os.path.join(os.getcwd(), "__no_such_reranker_model__9527")
    rr._load_pytorch = lambda: False          # 桩：不下载模型
    rr._load_model()
    print("  [真实] SkillReranker._load_model() 已执行（模型路径不存在 ⇒ skipped/failed 分支）")

    # A-4【2026-10-03 删除】原 A-4/A-5 验证 SafeFileReader 的
    #   yunshu_safe_file_reader_loaded_history_count 出现在**默认 REGISTRY**（= /metrics）上。
    #   该名字连同其余 4 个 yunshu_safe_file_reader_* 已于 2026-10-03 随 SafeFileReader 告警
    #   一起删除（恒为 0 的空名字不该继续挂在 /metrics 上）⇒ 这条断言已失效并删除。
    #   ⚠ 不要被"在本脚本里 import utils.file_reader 后仍能看到样本行"误导：那套同名指标是
    #     utils/file_reader.py **自己内联**的定义，只在 import 它的进程里注册；生产代码 0 处 import 它，
    #     服务进程的 /metrics 上并没有。原断言恰好会把这件事测反，故一并去掉（不再计入 PASS/FAIL）。
    #   详见 docs/closeout/死代码删除_SafeFileReader指标与作废断言_20261003.md。

    text = collector.export_prometheus()

    real_names = [
        "yunshu_skill_match_count", "yunshu_skill_match_latency_ms",
        "yunshu_skill_metadata_index_count",
        "yunshu_reranker_load_total", "yunshu_reranker_fallback_total",
        "tfidf_scan_candidate_limit_applied_total", "query_cache_hit_rate",
    ]
    for name in real_names:
        hits = lines_for(text, name)
        if hits:
            PASS += 1
            REAL.append(name)
            print("  [OK]   %s" % name)
            for h in hits[:2]:
                print("           %s" % h)
        else:
            FAIL += 1
            print("  [MISS] %s  <-- 真实路径没有产出样本行！" % name)

    # ── B 段：调用点参数回放 ──────────────────────────────────────────
    print(NL + "B 段 · 调用点参数回放（[REPLAY]，不证明业务路径被走到）" + NL + "-" * 78)
    for name, meta in sorted(agg.items()):
        if name in REAL:
            continue
        kind = meta["kind"]
        if kind == "<dynamic>":
            print("  [SKIP] %s：kind 非字面量，无法回放" % name)
            continue
        labels = effective_labels(meta["label_sets"][0])
        value = 1 if kind == "counter" else (0.5 if kind == "histogram" else 0)
        emit_metric(name, value=value, kind=kind, labels=labels)
        REPLAY.append(name)

    text = collector.export_prometheus()

    print(NL + "B 段校验：每个名字都必须有 # TYPE 行 + 至少一行样本" + NL + "-" * 78)
    missing_type = []
    missing_sample = []
    type_mismatch = []
    for name, meta in sorted(agg.items()):
        tl = type_line(text, name)
        if tl is None:
            missing_type.append(name)
            continue
        defn = BUSINESS_METRICS_DEFINITIONS.get(name)
        if defn is not None and meta["kind"] != "<dynamic>" and defn.metric_type != meta["kind"]:
            type_mismatch.append("%s: 登记=%s 调用点=%s" % (name, defn.metric_type, meta["kind"]))
        if not lines_for(text, name):
            missing_sample.append(name)
    if missing_type:
        FAIL += len(missing_type)
        print("  [MISS] 以下名字没有 # TYPE 行（= 未登记 / 未导出）：")
        for n in missing_type:
            print("           %s" % n)
    else:
        PASS += 1
        print("  [OK]   %d/%d 个名字都有 # TYPE 行（登记表覆盖完整）" % (len(agg), len(agg)))
    if type_mismatch:
        FAIL += len(type_mismatch)
        print("  [MISS] 类型不一致（会导致样本行永远为空）：")
        for t in type_mismatch:
            print("           %s" % t)
    else:
        PASS += 1
        print("  [OK]   全部名字的登记类型与调用点 kind 一致")
    if missing_sample:
        FAIL += len(missing_sample)
        print("  [MISS] 以下名字没有样本行：")
        for n in missing_sample:
            print("           %s" % n)
    else:
        PASS += 1
        print("  [OK]   %d/%d 个名字都产出了样本行" % (len(agg), len(agg)))

    print(NL + "=" * 78)
    print("真实触发 %d 个，回放 %d 个，结果 PASS=%d FAIL=%d"
          % (len(REAL), len(REPLAY), PASS, FAIL))
    print("=" * 78)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
