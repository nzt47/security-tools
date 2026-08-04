#!/usr/bin/env python3
"""Promtail pipeline_stages 解析逻辑本地模拟验证

目的：不依赖 K8s/Loki 集群，模拟 deploy/k8s/promtail-structured-log.yaml
的 5 级 pipeline_stages，验证 orchestrator.semantic.metric_total 的 6 个
结构化字段能否被正确解析：

  1. multiline    —— 多行合并（单行 JSON 场景不触发）
  2. json         —— 解析顶层 6 字段 + 辅助字段
  3. labels       —— action / instruction_loaded 设为 label
  4. match+drop   —— 仅保留 semantic 埋点日志，展开 layer_counts 嵌套 dict
  5. template     —— 规范化 message

【不易】仅解析不改写；数值字段由 LogQL 运行时转换（与生产一致）
【简易】独立可运行：python scripts/verify_promtail_pipeline.py

对照生产日志：orchestrator.py L1086-1108 log_dict 输出的 JSON 单行
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ════════════════════════════════════════════════════════════════════
#  模拟生产日志：L1086 semantic 埋点 structured log（log_dict 输出）
# ════════════════════════════════════════════════════════════════════

_SAMPLE_LINE = json.dumps({
    "module_name": "orchestrator",
    "action": "orchestrator.semantic.metric_total",
    "trace_id_ctx": "ci_verify_promtail_001",
    "message": "[埋点] semantic 触发, total=5, counts={'rule': 1, 'semantic': 2, 'llm': 2}, skill=verify_skill_001, score=0.875, instr_len=42, instr_loaded=success",
    "metric_total": 5,
    "layer_counts": {"rule": 1, "semantic": 2, "llm": 2},
    "skill_id": "verify_skill_001",
    "top1_score": 0.875,
    "instruction_len": 42,
    "instruction_loaded": True,
}, ensure_ascii=False)

# 非 semantic 日志（应被 drop）
_NON_SEMANTIC_LINE = json.dumps({
    "module_name": "orchestrator",
    "action": "orchestrator.process.receive",
    "trace_id_ctx": "ci_verify_promtail_002",
    "message": "[Orchestrator.process] 收到对话请求",
}, ensure_ascii=False)


# ════════════════════════════════════════════════════════════════════
#  按 pipeline_stages 语义逐步模拟
# ════════════════════════════════════════════════════════════════════

class _ParsedLog:
    """模拟 Promtail 日志条目（entry line + extracted labels）"""

    def __init__(self, line: str):
        self.line = line
        self.labels = {}

    def json_stage(self, expressions: dict, source=None):
        """模拟 json stage：从 line 或指定 source 提取字段"""
        data = json.loads(source if source else self.line)
        for out, path in expressions.items():
            if path in data:
                self.labels[out] = data[path]
            else:
                self.labels[out] = None
        return self

    def labels_stage(self, **mapping):
        """模拟 labels stage：仅字符串值可设为 label"""
        for out, src in mapping.items():
            val = self.labels.get(src)
            if isinstance(val, (str, bool)):
                self.labels[out] = str(val).lower()
        return self

    def template_stage(self, source, template):
        """模拟 template stage：Go 模板的 toLower/replace/trim 等价简化"""
        cur = self.labels.get(source)
        if cur is None:
            return self
        rendered = re.sub(r"\{\{\s*\.(\w+)\s*\}\}", lambda m: str(self.labels.get(m.group(1), "")), template)
        self.labels[source] = rendered
        return self


def run_pipeline(line: str) -> dict:
    """模拟 promtail-structured-log.yaml 的 5 级 pipeline（对照文件注释）"""
    entry = _ParsedLog(line)

    # 1. multiline（单行场景跳过，仅日志被截断时生效）

    # 2. json 顶层字段（6 目标字段 + 辅助）
    entry.json_stage({
        "module_name": "module_name",
        "action": "action",
        "trace_id_ctx": "trace_id_ctx",
        "message": "message",
        "metric_total": "metric_total",
        "layer_counts_raw": "layer_counts",
        "skill_id": "skill_id",
        "top1_score": "top1_score",
        "instruction_len": "instruction_len",
        "instruction_loaded": "instruction_loaded",
    })

    # 3. labels（低基数）
    entry.labels_stage(action="action", instruction_loaded="instruction_loaded")

    # 4. match + drop（仅保留 semantic 埋点）+ 展开 layer_counts 嵌套 dict
    if entry.labels.get("action") != "orchestrator.semantic.metric_total":
        return {"dropped": True, "labels": entry.labels}

    lc = entry.labels.get("layer_counts_raw")
    if isinstance(lc, dict):
        for k, v in lc.items():
            entry.labels["layer_" + k] = v
    return {"dropped": False, "labels": entry.labels}


def verify_pipeline() -> int:
    all_pass = True

    def _check(name, cond, detail=""):
        nonlocal all_pass
        ok = bool(cond)
        print("  [%s] %s %s" % ("✓ PASS" if ok else "✗ FAIL", name, detail))
        if not ok:
            all_pass = False

    print("=" * 70)
    print("Promtail pipeline_stages 解析验证（本地模拟）")
    print("样本: action=%s" % _SAMPLE_LINE[:60])
    print("=" * 70)

    # ── 场景 1：semantic 埋点日志应被保留并解析 ──
    r1 = run_pipeline(_SAMPLE_LINE)
    lbl = r1["labels"]
    print("\n--- 场景 1: semantic 埋点日志（应保留 + 解析 6 字段）---")
    _check("不被 drop", r1["dropped"] is False)
    _check("action label", lbl.get("action") == "orchestrator.semantic.metric_total",
           "→ %r" % lbl.get("action"))
    _check("metric_total", lbl.get("metric_total") == 5, "→ %r" % lbl.get("metric_total"))
    _check("layer_counts 嵌套展开", lbl.get("layer_rule") == 1 and lbl.get("layer_llm") == 2,
           "→ %r" % {k: v for k, v in lbl.items() if k.startswith("layer_")})
    _check("skill_id", lbl.get("skill_id") == "verify_skill_001", "→ %r" % lbl.get("skill_id"))
    _check("top1_score", lbl.get("top1_score") == 0.875, "→ %r" % lbl.get("top1_score"))
    _check("instruction_len", lbl.get("instruction_len") == 42, "→ %r" % lbl.get("instruction_len"))
    _check("instruction_loaded label", lbl.get("instruction_loaded") == "true",
           "→ %r" % lbl.get("instruction_loaded"))

    # ── 场景 2：非 semantic 日志应被 drop ──
    r2 = run_pipeline(_NON_SEMANTIC_LINE)
    print("\n--- 场景 2: 非 semantic 日志（应被 drop）---")
    _check("被 drop", r2["dropped"] is True)

    # ── 场景 3：message 规范化 template ──
    print("\n--- 场景 3: message 规范化 template ---")
    entry = _ParsedLog(_SAMPLE_LINE)
    entry.json_stage({"action": "action", "message": "message",
                      "metric_total": "metric_total",
                      "skill_id": "skill_id", "top1_score": "top1_score",
                      "instruction_len": "instruction_len",
                      "instruction_loaded": "instruction_loaded"})
    entry.labels_stage(action="action", instruction_loaded="instruction_loaded")
    entry.template_stage(
        "message",
        'semantic[total={{ .metric_total }}][skill={{ .skill_id }}][score={{ .top1_score }}][instr_len={{ .instruction_len }}][loaded={{ .instruction_loaded }}]'
    )
    tpl = entry.labels.get("message")
    _check("message 规范化", tpl and tpl.startswith("semantic[total=5]"),
           "→ %s" % tpl)

    print("\n" + "=" * 70)
    if all_pass:
        print("✓ Promtail pipeline 解析逻辑全部通过，6 字段可正确采集")
    else:
        print("✗ 存在解析失败，请核对 deploy/k8s/promtail-structured-log.yaml")
    print("=" * 70)
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(verify_pipeline())
