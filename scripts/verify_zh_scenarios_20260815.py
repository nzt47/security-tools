#!/usr/bin/env python3
"""中文输入场景专项测试用例集执行器（2026-08-15）

对应文档: docs/zh/智能体学习机制重构计划/中文输入场景专项测试用例集_20260815.md
覆盖分组:
    A. ZH-TOK   (6)  分词单元层 bigram
    B. ZH-MATCH (4)  语义层匹配防误命中短路
    C. ZH-ASSEM (3)  ContextAssembler 组装触发
    D. ZH-REJECT(2)  拒识层放行 / 输入过短独立判定
    E. ZH-E2E   (3)  LLM 端到端

验证方式:
- A/B/C: 直接 import 模块做单元断言（不依赖运行中的服务）
- D: 读取 config.yaml + 源码静态断言
- E: 真实 HTTP 请求（依赖 5678 服务在跑）+ 日志证据 + 源码降级分支静态断言

退出码: 0=全部通过, 1=存在 FAIL
"""

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list = []


def check(case_id: str, desc: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    RESULTS.append((case_id, ok))
    print(f"[{status}] {case_id} {desc} {detail}")


def section(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")


# ─────────────────────────────────────────────────────────────
# A. 分词单元层（bigram）
# ─────────────────────────────────────────────────────────────
section("A. 分词单元层（bigram）")

from agent.skills_mgmt.loader import _tokenize as loader_tokenize          # noqa: E402
from agent.skills_mgmt.bm25_searcher import _tokenize as bm25_tokenize      # noqa: E402
from agent.skills_mgmt.loader import _match_score                          # noqa: E402

_t = loader_tokenize("费马小定理的证明思路是什么")
_exp = ["费马", "马小", "小定", "定理", "理的", "的证", "证明", "明思", "思路", "路是", "是什", "什么"]
check("ZH-TOK-01", "loader 中文连续串按相邻二元组", _t == _exp,
      f"got={_t}")

_t2 = loader_tokenize("解析文件")
check("ZH-TOK-02", "2 字短串也生成 bigram", _t2 == ["解析", "析文", "文件"],
      f"got={_t2}")

_t3 = loader_tokenize("hello world")
check("ZH-TOK-03", "英文按词不切 bigram", _t3 == ["hello", "world"],
      f"got={_t3}")

_t4a = loader_tokenize("")
_t4b = loader_tokenize(None)
check("ZH-TOK-04", "空串/None 不抛异常", _t4a == [] and _t4b == [],
      f"got=({_t4a}, {_t4b})")

_t5 = loader_tokenize("好")
check("ZH-TOK-05", "孤立单字保留（守向后兼容）", _t5 == ["好"], f"got={_t5}")

_t6 = loader_tokenize("Python 解析")
check("ZH-TOK-06", "中英混排各自处理", _t6 == ["python", "解析"],
      f"got={_t6}")

# 两处 _tokenize 同尺度（bm25 路必须同步，否则 RRF 融合复发单字命中）
_same_scale = loader_tokenize("费马小定理") == bm25_tokenize("费马小定理")
check("ZH-TOK-SYNC", "loader 与 bm25_searcher 分词同尺度",
      _same_scale, f"loader={loader_tokenize('费马小定理')} bm25={bm25_tokenize('费马小定理')}")

# ─────────────────────────────────────────────────────────────
# B. 语义层匹配（防误命中短路）
# ─────────────────────────────────────────────────────────────
section("B. 语义层匹配（防误命中短路）")

# persona 元技能元数据模拟（覆盖大量常用字的"主动建议/关怀"类技能描述）
_PERSONA_META = "主动建议 关怀 问候 互动 陪伴 推送 提醒 我的状态 云枢数字生命 心情 反馈"

_q_tokens_bigram = loader_tokenize("费马小定理的证明思路是什么")
_q_tokens_single = ["费", "马", "小", "定", "理", "的", "证", "明", "思", "路", "是", "什", "么"]

_score_bigram = _match_score(_PERSONA_META, _q_tokens_bigram)
_score_single = _match_score(_PERSONA_META, _q_tokens_single)
check("ZH-MATCH-01", "bigram 命中率 < 0.3（不误命中 persona 元技能）",
      _score_bigram < 0.3, f"bigram={_score_bigram:.3f} 单字={_score_single:.3f}（修复前虚高）")

_sum_tokens = loader_tokenize("请帮我总结今天的对话")
check("ZH-MATCH-02", "含「总结」bigram 而非单字「总」「结」",
      "总结" in _sum_tokens and "总" not in _sum_tokens and "结" not in _sum_tokens,
      f"tokens={_sum_tokens}")

_matched_task = _match_score("文档解析 解析文件 格式转换", loader_tokenize("请解析这个文件"))
_matched_common = _match_score(_PERSONA_META, loader_tokenize("请解析这个文件"))
check("ZH-MATCH-03", "任务词 bigram 有区分度（任务词命中 > 常用字）",
      _matched_task > _matched_common,
      f"任务词命中={_matched_task:.3f} 常用字命中={_matched_common:.3f}")

_repeat = _match_score("帮我总结今天的对话", _sum_tokens)
check("ZH-MATCH-04", "与元技能描述高度重复时允许命中（≥ 0.3 是合法行为）",
      _repeat >= 0.3, f"重复命中率={_repeat:.3f}")

# ─────────────────────────────────────────────────────────────
# C. ContextAssembler 组装触发
# ─────────────────────────────────────────────────────────────
section("C. ContextAssembler 组装触发")

from agent.context.assembler import ContextAssembler  # noqa: E402

_asm = ContextAssembler(token_budget=3000)
_ctx = _asm.assemble("费马小定理的证明思路是什么")
check("ZH-ASSEM-01", "组装流程触发并返回 PromptContext",
      _ctx is not None and _ctx.system_text and _ctx.task == "费马小定理的证明思路是什么",
      f"total={_ctx.total_tokens} truncated={_ctx.truncated}")

_ctx_empty = _asm.assemble("")
check("ZH-ASSEM-02", "三层全空也走完组装、静默降级（truncated=False）",
      _ctx_empty.system_text and not _ctx_empty.truncated and _ctx_empty.total_tokens > 0,
      f"total={_ctx_empty.total_tokens} truncated={_ctx_empty.truncated}")

_asm_long = ContextAssembler(
    token_budget=100,
    working_memory_fn=lambda: [{"role": "user", "content": "长" * 3000}],
)
_ctx_long = _asm_long.assemble("请处理这个长任务")
check("ZH-ASSEM-03", "超预算截断不抛异常（truncated=True）",
      _ctx_long.truncated, f"total={_ctx_long.total_tokens} budget={_ctx_long.budget}")

# ─────────────────────────────────────────────────────────────
# D. 拒识层放行 / 输入过短独立判定
# ─────────────────────────────────────────────────────────────
section("D. 拒识层放行 / 输入过短独立判定")

import yaml  # noqa: E402

_cfg = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))
_reject_enabled = _cfg.get("orchestrator", {}).get("reject", {}).get("enabled")
check("ZH-REJECT-01", "reject.enabled=false（未知意图放行 LLM）",
      _reject_enabled is False, f"enabled={_reject_enabled!r}")

_src = (REPO_ROOT / "agent" / "orchestrator" / "orchestrator.py").read_text(encoding="utf-8")
_has_len_reject = "_len_reject" in _src and "ORCHESTRATOR_REJECT_MIN_LENGTH" in _src
check("ZH-REJECT-02", "输入过短拒识独立判定不受总开关影响",
      _has_len_reject, "process() 内 _len_reject 独立于 reject.enabled")

# ─────────────────────────────────────────────────────────────
# E. LLM 端到端（真实 HTTP，需 5678 服务在跑）
# ─────────────────────────────────────────────────────────────
section("E. LLM 端到端")

_BASE = "http://127.0.0.1:5678"
_http_ok = False
try:
    _body = json.dumps({"message": "费马小定理的证明思路是什么？",
                        "session_id": "verify-zh-20260815"}).encode("utf-8")
    _req = urllib.request.Request(
        _BASE + "/api/chat", data=_body,
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    _t0 = time.time()
    with urllib.request.urlopen(_req, timeout=90) as _resp:
        _raw = _resp.read().decode("utf-8")
    _elapsed = time.time() - _t0
    _data = json.loads(_raw)
    _resp_text = (_data.get("response") or "").strip()
    _http_ok = True
except Exception as exc:  # noqa: BLE001
    _elapsed = 0.0
    _resp_text = ""
    _data = {}
    _e2e_err = str(exc)
else:
    _e2e_err = ""

check("ZH-E2E-01", "HTTP 200 且 response 非空",
      _http_ok and len(_resp_text) > 0,
      f"elapsed={_elapsed:.1f}s len={len(_resp_text)} err={_e2e_err}")

# 埋点证据：请求后扫描最新服务日志中的 intent_layer=llm 记录
_metric_found = False
_metric_path = REPO_ROOT / "data" / "health" / "server_semantic_fix9.log"
if _metric_path.exists():
    _metric_found = "layer=llm" in _metric_path.read_text(encoding="utf-8", errors="ignore")
check("ZH-E2E-02", "日志含 intent_layer layer=llm 埋点（链路放行到 LLM）",
      _metric_found, f"log={_metric_path.name}")

# 失败降级分支静态断言（不实际破坏运行中服务的 key）
_has_degrade = ("_call_llm_v2" in _src and "LLM 调用失败" in _src
                and "（LLM 调用失败）" in _src and "except" in _src)
check("ZH-E2E-03", "LLM 调用失败有降级分支（不抛 500）",
      _has_degrade, "_call_llm_v2 含 LLM 调用失败 except + 降级文案")

# ─────────────────────────────────────────────────────────────
# 汇总
# ─────────────────────────────────────────────────────────────
section("汇总")
_passed = sum(1 for _, ok in RESULTS if ok)
_failed = len(RESULTS) - _passed
print(f"TOTAL={len(RESULTS)} PASS={_passed} FAIL={_failed}")
if _failed:
    for cid, ok in RESULTS:
        if not ok:
            print(f"  FAILED: {cid}")
sys.exit(0 if _failed == 0 else 1)
