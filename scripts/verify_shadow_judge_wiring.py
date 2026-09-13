"""S10-02 验收：judge runtime 接入生产灰度链路的**端到端**证据脚本

它验证三件事（对应任务书验收逐条）：

1. **开关关 = 与接入前逐字节一致**：用 ``--mode off`` 在**两个 checkout**（接入前 /
   接入后）各跑一次**同一条**确定性灰度，把规范化后的报告 JSON 落盘 ⇒ 两份文件
   ``diff`` 必须为空。规范化只剥掉**本质上不确定**的字段（墙钟 p99 / 生成时间 /
   事件 id / 审计序号）与本次运行自己的临时目录前缀，并把剥掉的键名如实打印出来
   （不做选择性隐藏）。
2. **开关开 = 真实通道接入**：``--mode on --confirm-real-calls`` 用真实凭证跑一次小样本
   灰度，报告 ``judge_kind`` 必须是 ``llm:<provider>:<model>``，且 UTC judge 栏有增量。
3. **回落如实**：同一次 ``--mode on`` 里再跑两条**不花钱**的回落路径 ——
   预算为 0（``budget_exceeded``）与无凭证（``no_credentials``），确认标签正确、
   **未发起真实调用**。

隔离纪律：事件目录 / 灰度目录 / 判定集一律落临时目录；``.env`` **只读**，只取
``LLM_PROVIDER`` / ``LLM_MODEL`` / ``LLM_BASE_URL`` / ``LLM_API_KEY`` 四个键写进
**进程内 env 映射**（**不把凭证写进 ``os.environ``**），凭证只以指纹出现，绝不打印明文。

用法
----
    # ① 接入前（基线 checkout）与接入后各跑一次，再 diff（必须为空）
    python scripts/verify_shadow_judge_wiring.py --repo <基线> --mode off --dump before.json
    python scripts/verify_shadow_judge_wiring.py --repo <本工作区> --mode off --dump after.json
    # ② 真实凭证（会花钱：探针 1 次 + 每样本 1 次）
    python scripts/verify_shadow_judge_wiring.py --mode on --confirm-real-calls \
        --env-file C:/Users/Administrator/agent/.env --json-out evidence.json

退出码：``off`` = 0（跑通即可，一致性由两份 dump 的 diff 判定）；``on`` = 0 仅当
"``judge_kind`` 为 ``llm:...`` 且 UTC 有增量 且 两条回落路径标签正确且零真实调用"。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

CAP = "cp.builtin.read_file"
#: 每日预算 = min(日均 × 15%, 50)（§4.5）⇒ 20 日均 = 3 条样本
SAMPLES_DAILY_AVG = 20.0
DEFAULT_ENV_FILE = "C:/Users/Administrator/agent/.env"

#: 规范化时**必须**剥掉的不确定字段（原因逐条给出，避免"看起来像选择性隐藏"）
NONDET_KEYS: Dict[str, str] = {
    "generated_at": "运行时刻",
    "event_id": "事件流 id",
    "audit_seq": "链式审计序号（全局自增）",
    "audit_hash": "链式审计哈希（取决于前序条目）",
    "p99_wall_candidate_ms": "墙钟 p99（受机器负载影响）",
    "p99_wall_upstream_ms": "墙钟 p99（受机器负载影响）",
    "wall_ms_candidate": "单次墙钟（受机器负载影响）",
    "wall_ms_upstream": "单次墙钟（受机器负载影响）",
    "shadow_overhead_ms": "灰度自身开销 = Σ 采样墙钟（shadow.py::_overhead，非业务成本）",
    "p99_latency_ms": "quality_patch 里的墙钟 p99（shadow.py::_quality_patch）",
}
PLACEHOLDER = "<nondeterministic>"


# ════════════════════════════════════════════════════════════
#  选择被验证的 checkout（`--repo`）—— 必须在 import agent 之前完成
# ════════════════════════════════════════════════════════════


def _repo_root_from_argv(argv: Sequence[str]) -> Path:
    for index, item in enumerate(argv):
        if item == "--repo" and index + 1 < len(argv):
            return Path(argv[index + 1]).resolve()
        if item.startswith("--repo="):
            return Path(item.split("=", 1)[1]).resolve()
    return Path(__file__).resolve().parents[1]


_ROOT = _repo_root_from_argv(sys.argv)
# 把**其它** checkout 的根从 sys.path 清掉（否则"测的是哪棵树"会取决于 PYTHONPATH）
sys.path[:] = [p for p in sys.path
               if not (str(p).strip()
                       and (Path(str(p)) / "agent" / "digestion" / "shadow.py").exists()
                       and Path(str(p)).resolve() != _ROOT)]
sys.path.insert(0, str(_ROOT))

from agent.digestion import cases as C  # noqa: E402
from agent.digestion import gate as G  # noqa: E402
from agent.digestion import shadow as SH  # noqa: E402
from agent.observability import events as events_mod  # noqa: E402
from agent.observability import utc as utc_mod  # noqa: E402

#: 防"测错树"的假绿灯：import 到的必须是 `--repo` 指定的那棵
_ACTUAL_ROOT = Path(SH.__file__).resolve().parents[2]
assert _ACTUAL_ROOT == _ROOT, f"import 到了另一棵树：{_ACTUAL_ROOT} ≠ {_ROOT}"


def has_enable_gate() -> bool:
    """本 checkout 是否已带 S10-02 开关门（基线没有 ⇒ `on` 模式不可用）"""
    from agent.digestion import judge_runtime as _jr
    return (hasattr(_jr, "build_judge_runtime_if_enabled")
            and hasattr(SH, "judge_runtime_from_env"))


# ════════════════════════════════════════════════════════════
#  场景构造（同一条确定性灰度；与 checkout 状态无关）
# ════════════════════════════════════════════════════════════


def make_case(index: int, root: str = "C:/sandbox") -> Any:
    path = f"{root}/out/a{index}.txt"
    steps = [C.ProgramStep(label="read_file", params={"path": path},
                           capability_id=CAP),
             C.ProgramStep(label="write_file",
                           params={"path": path, "content": f"c{index}"},
                           capability_id=CAP)]
    return C.EquivalenceCase(
        case_id=f"case-{index:03d}", capability_id=CAP, input={"path": path},
        upstream=steps, native=steps, fixtures={path: f"c{index}"},
        expected_side_effects={"files_written": [path]},
        expected_status="success", sandbox_root=root)


def build_scenario(tmp: Path) -> Tuple[Any, Any, Dict[str, str]]:
    """造判定集 + 真发通行证；返回 (case_set, passport_store, 隔离目录)

    ``CP_EVENTS_DIR`` / ``CP_DIGESTION_SHADOW_DIR`` 必须设进 ``os.environ``：
    这两个落点由既有代码直接读进程环境（`JudgeVerdictStore` 读 shadow 目录、
    `utc.record_cost` 缺省落事件目录），**不是**本脚本可选的入参。
    """
    case_dir = tmp / "cases"
    shadow_dir = tmp / "shadow"
    events_dir = tmp / "events"
    for path in (case_dir, shadow_dir, events_dir):
        path.mkdir(parents=True, exist_ok=True)
    os.environ["CP_EVENTS_DIR"] = str(events_dir)
    os.environ[SH.SHADOW_DIR_ENV] = str(shadow_dir)
    events_mod.reset_event_stores()

    case_set = C.build_case_set(CAP, [make_case(i) for i in range(24)])
    store = G.PassportStore(str(case_dir))
    result = G.acceptance_gate(CAP, case_set=case_set, passport_store=store,
                               emit_events=False)
    assert result.passed is True, result.reasons()
    return case_set, store, {"cases": str(case_dir), "shadow": str(shadow_dir),
                             "events": str(events_dir)}


def read_env_file(path: str) -> Dict[str, str]:
    """极简 ``KEY=VALUE`` 解析（**只读**；不写回文件、不灌进 os.environ）"""
    out: Dict[str, str] = {}
    file = Path(path)
    if not file.exists():
        return out
    for line in file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, _, value = text.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key.strip()] = value
    return out


def base_env(env_file: str) -> Dict[str, str]:
    """灰度用环境映射（**非空 dict** ⇒ 不落到 ``os.environ``，可复现、可审计）"""
    return {
        SH.JUDGE_MODE_ENV: SH.JUDGE_MODE_AUTO,
        SH.SHADOW_ENABLE_ENV: "false",       # 灰度开关：本次由 force=True 单次开启
        SH.GRAY_ENABLE_ENV: "false",
        SH.JUDGE_BASE_URL_ENV: "",           # 由调用方按需覆盖（部署级 LLM_BASE_URL）
        "CP_DIGESTION_JUDGE_DOTENV": env_file,
    }


def run_gray(tmp: Path, env: Dict[str, str], *, tag: str) -> Dict[str, Any]:
    """跑一次灰度并回收证据（**不传 judge/judge_runtime** ⇒ 走生产注入点）"""
    case_set, store, dirs = build_scenario(tmp)
    run_env = dict(env)
    run_env[SH.SHADOW_DIR_ENV] = dirs["shadow"]
    runner = SH.ShadowRunner(
        passport_store=store,
        case_store=C.open_case_store(dirs["cases"]),
        ledger=SH.ShadowLedger(directory=dirs["shadow"]),
        review_queue=SH.ManualReviewQueue(directory=dirs["shadow"]),
        env=run_env, emit_events=False)
    before = utc_mod.judge_cost_cents(directory=dirs["events"])
    report = runner.run(CAP, case_set=case_set, force=True,
                        daily_avg=SAMPLES_DAILY_AVG, write_ledger=False,
                        enqueue_manual=False)
    after = utc_mod.judge_cost_cents(directory=dirs["events"])
    auto_runtime = getattr(runner, "runtime", None)
    return {
        "tag": tag,
        "dirs": dirs,
        "judge_kind": report.judge_kind,
        "judge_is_llm": report.judge_is_llm,
        "samples": report.total,
        "sample_judge_kinds": sorted({s.judge_kind for s in report.samples}),
        "pass_rate": report.pass_rate,
        "allowed": report.allowed,
        "blocked_reasons": list(report.blocked_reasons),
        "auto_injected_runtime": auto_runtime is not None,
        "availability_state": (auto_runtime.availability.state
                               if auto_runtime is not None else ""),
        "guard": (auto_runtime.guard.to_dict() if auto_runtime is not None else {}),
        "budget": (auto_runtime.budget.state().to_dict()
                   if auto_runtime is not None else {}),
        "model_calls": int(getattr(getattr(auto_runtime, "judge", None), "calls", 0)),
        "utc_before": before, "utc_after": after,
        "utc_delta_calls": int(after.get("calls") or 0) - int(before.get("calls") or 0),
        "utc_delta_cents": round(float(after.get("cost_normalized_cents") or 0.0)
                                 - float(before.get("cost_normalized_cents") or 0.0), 6),
        "judge_kind_row": {"judge_kind": report.judge_kind,
                           "plan_sampled": list(report.plan.sampled),
                           "plan_budget": int(report.plan.budget),
                           "sample_kinds": [s.judge_kind for s in report.samples],
                           "layer_failures": dict(report.layer_failures)},
        "canonical_report": canonical(report.to_dict(), workdir=str(tmp)),
    }


# ════════════════════════════════════════════════════════════
#  规范化（只剥"本质上不确定"的字段；剥了哪些**如实披露**）
# ════════════════════════════════════════════════════════════


def canonical(payload: Any, *, workdir: str = "") -> Any:
    """递归替换 `NONDET_KEYS` 的值；并把本次运行的临时目录前缀归一

    临时目录前缀必须归一，否则"两份 dump 因临时路径不同而不同"会被误读成行为差异
    （那既不是回归也不是一致性证据）。归一仅针对**本次 `--workdir`** 这一个字符串。
    """
    if isinstance(payload, dict):
        return {key: (PLACEHOLDER if key in NONDET_KEYS
                      else canonical(value, workdir=workdir))
                for key, value in payload.items()}
    if isinstance(payload, list):
        return [canonical(item, workdir=workdir) for item in payload]
    if isinstance(payload, str) and workdir:
        return payload.replace(workdir, "<workdir>").replace(
            workdir.replace("\\", "/"), "<workdir>")
    return payload


def dump_canonical(result: Dict[str, Any], path: str) -> str:
    text = json.dumps(result["canonical_report"], ensure_ascii=False, indent=2,
                      sort_keys=True, default=str)
    Path(path).write_text(text, encoding="utf-8")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════
#  两种模式
# ════════════════════════════════════════════════════════════


def mode_off(args: argparse.Namespace, tmp: Path) -> Dict[str, Any]:
    """开关关：跑通即可；一致性由两个 checkout 的 dump diff 判定"""
    result = run_gray(tmp / "run_off", base_env(str(args.env_file)), tag="off")
    digest = dump_canonical(result, args.dump) if args.dump else ""
    return {
        "mode": "off", "repo": str(_ROOT), "dump": args.dump,
        "dump_digest": digest,
        "result": {k: v for k, v in result.items() if k != "canonical_report"},
        "note": ("开关关：**不得**注入运行时；报告应与接入前逐字节一致"
                 "（用另一 checkout 跑同一条命令后 diff 两个 dump）"),
    }


def mode_on(args: argparse.Namespace, tmp: Path) -> Dict[str, Any]:
    if not has_enable_gate():
        return {"mode": "on", "repo": str(_ROOT), "verdict": "FAIL", "error": (
            f"{_ROOT} 尚未带 S10-02 开关门（build_judge_runtime_if_enabled 缺失）"
            "—— 本模式只能跑在接入后的 checkout 上")}
    values = read_env_file(args.env_file)
    provider = str(values.get("LLM_PROVIDER") or "").strip().lower()
    model = str(values.get("LLM_MODEL") or "").strip()
    base_url = str(values.get("LLM_BASE_URL") or "").strip()
    secret = str(values.get("LLM_API_KEY")
                 or values.get("DEEPSEEK_API_KEY") or "").strip()
    evidence: Dict[str, Any] = {
        "mode": "on", "repo": str(_ROOT), "env_file": args.env_file,
        "env_file_found": Path(args.env_file).exists(),
        "provider": provider, "model": model, "base_url": base_url,
        "credential_present": bool(secret),
        "credential_fingerprint": (("sha256:" + hashlib.sha256(
            secret.encode("utf-8")).hexdigest()[:12]) if secret else ""),
        "confirm_real_calls": bool(args.confirm_real_calls),
    }
    if not (provider and model and secret):
        evidence["error"] = ("部署级 .env 缺 LLM_PROVIDER / LLM_MODEL / LLM_API_KEY"
                            "（或 --env-file 不对）⇒ 无法验证真实通道")
        evidence["verdict"] = "FAIL"
        return evidence
    if not args.confirm_real_calls:
        evidence["note"] = ("未给 --confirm-real-calls ⇒ **未发起真实调用**"
                           "（真实 judge 会产生费用，必须显式确认）")
        evidence["verdict"] = "SKIPPED"
        return evidence

    cred_key = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
                "claude": "ANTHROPIC_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                "zhipu": "ZHIPU_API_KEY", "qwen": "DASHSCOPE_API_KEY",
                "gemini": "GEMINI_API_KEY"}.get(provider, "LLM_API_KEY")

    def _env(*, budget: str, with_cred: bool = True) -> Dict[str, str]:
        env = base_env(str(args.env_file))
        env.update({
            "CP_DIGESTION_JUDGE_ENABLED": "true",
            SH.JUDGE_PROVIDER_ENV: provider,
            SH.JUDGE_MODEL_ENV: model,
            SH.JUDGE_BASE_URL_ENV: base_url,
            "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS": budget,
            "LLM_PROVIDER": provider,
        })
        if with_cred:
            env[cred_key] = secret
            env["LLM_API_KEY"] = secret
        else:
            # **必须同时切断 .env 回落**：凭证三级解析是 SecretStore → 进程环境 →
            # `.env` 文件；只摘掉进程环境里的键，`CP_DIGESTION_JUDGE_DOTENV` 指向的
            # 部署级 `.env` 仍会把 key 解析出来（S10-02 实测踩到：那一跑拿到了
            # `llm:deepseek:...` 并真的发了 4 次调用 —— 是**测法错**，不是回落缺陷）。
            env["CP_DIGESTION_JUDGE_DOTENV"] = str(tmp / "absent.env")
        return env

    # ── A. 真实通道（**会花钱**：探针 1 次 + 每样本 1 次）──────────────
    real = run_gray(tmp / "run_real", _env(budget=str(args.budget_cents)),
                    tag="on_real")
    # ── B/C. 两条回落路径（**不花钱**：前置拦截/无通道，都不发真实调用）──
    over = run_gray(tmp / "run_budget0", _env(budget="0"), tag="on_budget_zero")
    nocred = run_gray(tmp / "run_nocred", _env(budget="100", with_cred=False),
                      tag="on_no_credentials")

    kind_llm = str(real["judge_kind"]).startswith(SH.JUDGE_KIND_LLM_PREFIX)
    acceptance = {
        "judge_kind_is_llm": kind_llm,
        "judge_kind_actual": real["judge_kind"],
        "utc_increased": float(real["utc_delta_cents"]) > 0.0,
        "utc_delta_cents": real["utc_delta_cents"],
        "utc_delta_calls": real["utc_delta_calls"],
        "expected_utc_calls": 1 + int(real["samples"]),
        "utc_calls_match_expectation":
            int(real["utc_delta_calls"]) == 1 + int(real["samples"]),
        "all_samples_same_kind": (len(real["sample_judge_kinds"]) == 1 and kind_llm),
        "budget_zero_label_ok": (
            over["judge_kind"] == SH.judge_fallback_kind(
                SH.JUDGE_REASON_BUDGET_EXCEEDED)),
        "budget_zero_no_real_call": over["model_calls"] == 0,
        "no_credentials_label_ok": (
            nocred["judge_kind"] == SH.judge_fallback_kind(
                SH.JUDGE_REASON_NO_CREDENTIALS)),
        "no_credentials_no_real_call": nocred["model_calls"] == 0,
    }
    evidence.update({
        "real_run": {k: v for k, v in real.items() if k != "canonical_report"},
        "fallback_budget_zero": {k: v for k, v in over.items()
                                 if k != "canonical_report"},
        "fallback_no_credentials": {k: v for k, v in nocred.items()
                                    if k != "canonical_report"},
        "acceptance": acceptance,
    })
    evidence["verdict"] = "PASS" if all([
        acceptance["judge_kind_is_llm"], acceptance["utc_increased"],
        acceptance["utc_calls_match_expectation"],
        acceptance["all_samples_same_kind"], acceptance["budget_zero_label_ok"],
        acceptance["budget_zero_no_real_call"], acceptance["no_credentials_label_ok"],
        acceptance["no_credentials_no_real_call"],
    ]) else "FAIL"
    return evidence


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=str(_ROOT),
                        help="被验证的 checkout 根（默认＝本脚本所在 checkout）")
    parser.add_argument("--mode", choices=("off", "on"), default="off")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE,
                        help="部署级 .env（**只读**；只用其中 4 个 LLM_* 键）")
    parser.add_argument("--budget-cents", type=float, default=100.0,
                        help="judge 每日预算（cents，默认 100）")
    parser.add_argument("--confirm-real-calls", action="store_true",
                        help="**必须显式确认**：on 模式会产生真实模型调用与费用")
    parser.add_argument("--dump", default="", help="把规范化报告 JSON 写到该路径")
    parser.add_argument("--json-out", default="", help="把证据 JSON 写到该路径")
    parser.add_argument("--workdir", default="", help="临时目录（默认新建；不自动清理）")
    args = parser.parse_args(argv)

    tmp = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(
        prefix="s10_02_wiring_"))
    tmp.mkdir(parents=True, exist_ok=True)
    evidence = mode_off(args, tmp) if args.mode == "off" else mode_on(args, tmp)
    evidence["nondeterministic_keys_stripped"] = NONDET_KEYS
    evidence["workdir"] = str(tmp)
    evidence["python"] = sys.version.split()[0]
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
        print(f"\n[证据已写入] {args.json_out}", file=sys.stderr)
    if args.mode == "off":
        return 0
    return 0 if evidence.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
