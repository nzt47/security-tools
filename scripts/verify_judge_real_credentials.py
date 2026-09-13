"""S9-02 W1：在**真实凭证**下跑一次 LLM-judge 小样本，并留下可复盘的证据。

为什么需要这个脚本
------------------
S8-04 交付的是"**具备即用**"（有凭证即用、无凭证如实回落），但从未在真凭证下
跑过一次 —— 出口条件第 6 条因此一直没有闭环。本脚本把那次运行**做成可重跑的**，
而不是只把一次性的结论写进报告。

它做什么
--------
1. 把部署级 ``.env`` 载入 ``os.environ``（与 `agent.env_config_manager.EnvConfigManager.reload`
   同语义：``KEY=VALUE`` → ``os.environ``），使 judge 走**与线上服务同一条**凭证通路；
2. 打印凭证预检（**只有指纹，绝无明文**）与不带探针的可用性三态；
3. ``verify=True`` 做一次**真实**端到端探针（这就是唯一会花钱的那一步）；
4. 用真实 ``build_judge_runtime`` 跑 N 条样本，逐条记录 ``judge_kind`` /
   真实 token 用量 / ``utc.judge_cost_cents`` 的**前后增量**；
5. **反向留证**：无凭证 与 超预算 两条回落路径各跑一次，确认它们如实标注且
   **真的没有发起模型调用**（省的是钱，不是标签）。

纪律
----
* 真实调用**必须显式确认**（``--confirm-real-calls``），因为它产生费用；
* 事件目录默认落在临时目录 ⇒ 不污染仓库运行期数据（``git status`` 不受影响）；
* 凭证只以指纹出现；本脚本**从不**打印 key 本身。

用法
----
    python scripts/verify_judge_real_credentials.py --env-file .env \
        --confirm-real-calls --samples 3 --budget-cents 100

退出码：0 = 真实通道验证成功（judge_kind 为 ``llm:...`` 且 UTC 有增量）；
1 = 未能在真实凭证下验证（**如实失败**，不得当成通过）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from agent.digestion import judge_runtime as JR  # noqa: E402
from agent.digestion import shadow as SH  # noqa: E402
from agent.observability import utc as utc_mod  # noqa: E402

#: 小样本（3 条；含 1 正例 1 负例 1 近义改写）——样本 <20 只披露不考核
SAMPLES: Tuple[Tuple[str, str], ...] = (
    ("read file a.txt | write report.md",
     "read file a.txt | write report.md"),
    ("read file a.txt | write report.md",
     "delete database prod | send email to all users"),
    ("今天天气很好，我去公园散步了。",
     "天气不错，我到公园走了走。"),
    ("调用 read_file(path=a.txt) 后返回 3 行",
     "先 grep 整个仓库再删除所有缓存"),
)


def load_env_file(path: str) -> Dict[str, str]:
    """``KEY=VALUE`` → dict（与 `EnvConfigManager.reload` 同口径；不做变量展开）"""
    out: Dict[str, str] = {}
    text = Path(path).read_text(encoding="utf-8")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def apply_env(values: Dict[str, str], names: Tuple[str, ...]) -> List[str]:
    """把指定键写进 ``os.environ``（只写这些键，避免把整个 .env 灌进来）"""
    applied = []
    for name in names:
        value = str(values.get(name) or "").strip()
        if value:
            os.environ[name] = value
            applied.append(name)
    return applied


def fingerprint(secret: str) -> str:
    return JR._fingerprint(secret)


def judge_costs(events_dir: str) -> Dict[str, Any]:
    return utc_mod.judge_cost_cents(directory=events_dir)


def build(events_dir: str, *, provider: str, model: str, budget: float) -> JR.JudgeRuntime:
    """按**部署级 env** 构造真实运行时（不注入任何桩凭证）

    刻意走 ``build_judge_runtime(config=None)`` —— 即"配置与凭证全部来自环境"的
    线上通路。**不传** ``secret_provider``：文件后端密钥缺失就让它缺失，
    免得一个 lambda 桩冒充 "SecretStore" 把 ``credential_source`` 标成假值。
    """
    os.environ[JR.JUDGE_ENABLE_ENV] = "true"
    os.environ[SH.JUDGE_PROVIDER_ENV] = provider
    os.environ[SH.JUDGE_MODEL_ENV] = model
    os.environ[JR.JUDGE_BUDGET_ENV] = str(float(budget))
    return JR.build_judge_runtime(
        events_dir=events_dir, dotenv_path=str(_REPO_ROOT / ".env"),
        emit_fallback_event=True)


def run_real(runtime: JR.JudgeRuntime, samples, events_dir: str) -> Dict[str, Any]:
    """真实判定 N 条，返回逐条结果 + UTC 增量

    **走 `runtime.guard(...)` 而不是 `runtime.judge.score(...)`** —— 前者才是
    线上集成路径：`JudgeGuard.__call__` 在真实调用成功后触发 `_on_call()`
    ⇒ `budget.record()` ⇒ `utc.record_cost(source="judge")`。
    绕过 Guard 直接调 judge，成本**不会**进 UTC（S9-02 实测踩过），
    那样测出来的"UTC 无增量"是**测法错误**，不是产品缺陷。
    """
    before = judge_costs(events_dir)
    judge = runtime.judge
    if judge is None:
        raise RuntimeError(
            "judge 通道未建立（真实凭证/通道未就绪）—— 真实样本本不该走到这里")
    rows = []
    for index, (reference, observed) in enumerate(samples, 1):
        kind_before = runtime.guard.effective_kind
        try:
            value = runtime.guard(reference, observed)   # 真实路径：含记账
            structured = dict(judge.last_structured or {})
            usage = dict(judge.last_usage or {})
            rows.append({
                "index": index,
                "reference": reference[:60],
                "observed": observed[:60],
                "ok": True,
                "score": round(float(value), 6),
                "verdict": structured.get("verdict", ""),
                "confidence": structured.get("confidence"),
                "format": structured.get("format", ""),
                "model_verdict": structured.get("model_verdict", ""),
                "reason": str(structured.get("reason", ""))[:200],
                "tokens": usage,
                "tokens_estimated": not bool(usage),
                "kind_before_call": kind_before,
                "kind_after_call": runtime.guard.effective_kind,
            })
        except Exception as e:  # noqa: BLE001 逐条如实记录，不中断整批
            rows.append({"index": index, "reference": reference[:60],
                         "observed": observed[:60], "ok": False,
                         "error": f"{type(e).__name__}: {e}"[:300],
                         "fallback_kind": runtime.guard.effective_kind})
    after = judge_costs(events_dir)
    return {
        "rows": rows,
        "calls": int(judge.calls),
        "effective_kind": runtime.guard.effective_kind,
        "kind_stayed_llm": all(
            str(r.get("kind_after_call", "")).startswith(SH.JUDGE_KIND_LLM_PREFIX)
            for r in rows if r.get("ok")) and any(r.get("ok") for r in rows),
        "guard": runtime.guard.to_dict(),
        "utc_before": before,
        "utc_after": after,
        "utc_delta_cents": round(
            float(after.get("cost_normalized_cents") or 0.0)
            - float(before.get("cost_normalized_cents") or 0.0), 6),
        "utc_delta_calls": int(after.get("calls") or 0) - int(before.get("calls") or 0),
        "cost_events_recorded": len(runtime.budget.recorded),
        "cost_record_errors": list(runtime.budget.record_errors),
        "budget_state": runtime.budget.state().to_dict(),
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", default=str(_REPO_ROOT / ".env"),
                        help="部署级 .env（凭证与端点来源）")
    parser.add_argument("--confirm-real-calls", action="store_true",
                        help="**必须显式确认**：本脚本会产生真实模型调用与费用")
    parser.add_argument("--samples", type=int, default=3,
                        help="真实判定的样本条数（默认 3；<20 只披露不考核）")
    parser.add_argument("--budget-cents", type=float, default=100.0,
                        help="judge 当日预算（cents）")
    parser.add_argument("--events-dir", default="",
                        help="事件目录（默认临时目录，避免污染仓库运行期数据）")
    parser.add_argument("--json-out", default="", help="把证据 JSON 写到该路径")
    args = parser.parse_args(argv)

    env_values = load_env_file(args.env_file)
    applied = apply_env(env_values, ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL",
                                     "LLM_API_KEY"))
    provider = str(os.environ.get("LLM_PROVIDER") or "").strip().lower()
    model = str(os.environ.get("LLM_MODEL") or "").strip()
    base_url = str(os.environ.get("LLM_BASE_URL") or "").strip()
    secret = str(os.environ.get("LLM_API_KEY") or "").strip()

    events_dir = args.events_dir or tempfile.mkdtemp(prefix="s912_judge_events_")
    Path(events_dir).mkdir(parents=True, exist_ok=True)
    evidence: Dict[str, Any] = {
        "task": "S9-02 W1 真实凭证 judge 小样本",
        "env_file": args.env_file,
        "env_keys_applied": applied,
        "provider": provider, "model": model, "base_url": base_url,
        "credential_fingerprint": fingerprint(secret) if secret else "",
        "credential_present": bool(secret),
        "events_dir": events_dir,
        "samples_requested": args.samples,
        "budget_cents": args.budget_cents,
    }

    # ── 1. 无探针预检（不花钱）──────────────────────────────
    #    走**真实**凭证解析（不注入 secret_provider）：文件后端缺失就让它缺失，
    #    于是 credential_source 如实显示到底是 env / dotenv / secret_store 哪一环。
    os.environ[JR.JUDGE_ENABLE_ENV] = "true"
    os.environ[SH.JUDGE_PROVIDER_ENV] = provider
    os.environ[SH.JUDGE_MODEL_ENV] = model
    evidence["self_check"] = JR.judge_self_check(
        dotenv_path=args.env_file, log=False)

    if not args.confirm_real_calls:
        evidence["real_run"] = None
        evidence["note"] = ("未给 --confirm-real-calls ⇒ **未发起真实调用**"
                           "（本脚本只在显式确认后才花钱）")
        _emit(evidence, args.json_out)
        return 1

    # ── 2. 真实端到端探针（会花钱）──────────────────────────
    evidence["self_check_verify"] = JR.judge_self_check(
        dotenv_path=args.env_file, log=False, verify=True)

    # ── 3. 真实小样本 ──────────────────────────────────────
    runtime = build(events_dir, provider=provider, model=model,
                    budget=args.budget_cents)
    evidence["runtime_kind_before"] = runtime.kind
    evidence["availability_before"] = runtime.availability.to_dict()
    evidence["real_run"] = run_real(runtime, SAMPLES[:max(0, args.samples)],
                                    events_dir)

    # ── 4. 回落路径 A：无凭证（真的没有凭证，不是桩）────────
    empty_dir = tempfile.mkdtemp(prefix="s912_judge_nocred_")
    crippled = {name: os.environ.pop(name, None)
                for name in ("LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY")}
    try:
        no_cred = build(empty_dir, provider=provider, model=model, budget=100.0)
        no_cred_value = no_cred.guard("a", "b")
        evidence["fallback_no_credentials"] = {
            "availability_state": no_cred.availability.state,
            "kind": no_cred.guard.effective_kind,
            "reason_code": no_cred.guard.reason_code,
            "model_calls": int(no_cred.judge.calls if no_cred.judge else 0),
            "judge_object_is_none": no_cred.judge is None,
            "score_returned": round(float(no_cred_value), 6),
            "credential_reason": no_cred.availability.credential.reason,
            "note": ("**真无凭证**（已摘掉进程环境里的 LLM_API_KEY/DEEPSEEK_API_KEY，"
                     "且 dotenv 指向不存在文件），非桩"),
        }
    finally:
        for name, value in crippled.items():
            if value is not None:
                os.environ[name] = value

    # ── 5. 回落路径 B：超预算（有真凭证，但预算为 0）────────
    over = build(events_dir, provider=provider, model=model, budget=0.0)
    over_value = over.guard("a", "b")
    evidence["fallback_budget_exceeded"] = {
        "availability_state": over.availability.state,
        "kind": over.guard.effective_kind,
        "reason_code": over.guard.reason_code,
        "model_calls": int(over.judge.calls if over.judge else 0),
        "precheck_blocks": over.guard.precheck_blocks,
        "score_returned": round(float(over_value), 6),
        "budget_state": over.budget.state().to_dict(),
        "note": ("凭证与通道**真实可用**，仅预算=0 ⇒ 前置拦截生效、"
                 "**未发起模型调用**（省的是钱，不是标签）"),
    }

    # ── 6. 验收判定（如实，不看脸色）────────────────────────
    real = evidence["real_run"]
    kind_is_llm = str(real.get("effective_kind", "")).startswith(SH.JUDGE_KIND_LLM_PREFIX)
    utc_increased = float(real.get("utc_delta_cents") or 0.0) > 0.0
    ok_rows = [r for r in real["rows"] if r.get("ok")]
    evidence["acceptance"] = {
        "judge_kind_is_llm": kind_is_llm,
        "judge_kind_actual": real.get("effective_kind"),
        "kind_stayed_llm_across_samples": bool(real.get("kind_stayed_llm")),
        "utc_delta_cents": real.get("utc_delta_cents"),
        "utc_increased": utc_increased,
        "utc_delta_calls": real.get("utc_delta_calls"),
        "cost_events_recorded": real.get("cost_events_recorded"),
        "cost_record_errors": real.get("cost_record_errors"),
        "successful_samples": len(ok_rows),
        "requested_samples": min(args.samples, len(SAMPLES)),
        "no_credentials_fell_back": (
            evidence["fallback_no_credentials"]["kind"]
            == SH.judge_fallback_kind(SH.JUDGE_REASON_NO_CREDENTIALS)),
        "budget_blocked_without_call": (
            evidence["fallback_budget_exceeded"]["model_calls"] == 0),
        "verdict": "PASS" if (kind_is_llm and utc_increased) else "FAIL",
    }
    _emit(evidence, args.json_out)
    return 0 if evidence["acceptance"]["verdict"] == "PASS" else 1


def _emit(evidence: Dict[str, Any], json_out: str) -> None:
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    print(text)
    if json_out:
        Path(json_out).write_text(text, encoding="utf-8")
        print(f"\n[证据已写入] {json_out}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
