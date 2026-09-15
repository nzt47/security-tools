"""S11-04 现象固化：开关关闭时 `resolve_judge("auto")` 是否仍可能选中**无预算护栏**的 LLM 通道

先说结论（证据在本脚本输出里，逐条可复现）：

1. **本部署口径（未配 `CP_DIGESTION_JUDGE_PROVIDER/MODEL`）**：`auto` 回落确定性打分器，
   今天**不花钱**。但回落原因是 **"未配置 provider/model"**（`llm_unavailable`），
   **不是** "开关关闭" —— 证据 = 用例 1/2：把 `CP_DIGESTION_JUDGE_ENABLED` 显式写成
   `false` 与完全不写，结果**逐字相同**（都是 `llm_unavailable`）。
2. **一旦配上 provider/model（两个键在 `settings/registry.py` 里是 `_a` = 可直接切的
   普通键）且适配器看得见凭证**：`auto` **立刻选中真实 LLM 通道**，且该通道
   ① `JudgeGuard._precheck is None` ⇒ **没有每日预算前置拦截**；
   ② `JudgeGuard._on_call is None` ⇒ **没有 `utc.record_cost(source="judge")` 记账**。
   ⇒ 这是 `CP_DIGESTION_JUDGE_ENABLED`（`_b` = 需二次认证的**总开关**）被一个
   `_a` 键旁路的问题：**护栏开关关着，钱照样能花，而且花得看不见**。

**费用纪律**：除一次性最小真调探针（另见 `s1104_judge_auto_probe.py`）外，
本脚本把**传输层**换成桩（`OpenAIAdapter.generate` 被替换为返回固定结构化回复）；
被替换的只有"网络传输"这一段，其上的 `resolve_judge` / `LLMJudge` / `JudgeGuard` /
`ShadowRunner` **全是生产代码**，未做任何 mock。

用法：
    python scripts/dev/s1104_judge_auto_repro.py
"""
from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

#: 桩密钥：只为让 `OpenAIAdapter.is_available()` 造出 client（该函数**不发网络请求**），
#: 便于在**不产生费用**的前提下走通"通道可选"这条判定。绝不打真实端点。
#: 【S11-09 取证】该字面量**不是凭证**，故按 gitleaks 官方**逐行**机制放行
#: （只放行本行，不改 `.github/gitleaks-config.toml`，不削弱仓库级检出能力）：
#:   ① 本行是显式桩：命名 `_STUB_KEY` + 本注释即声明"桩"，且 `main()` 里
#:      `os.environ["OPENAI_API_KEY"] = _STUB_KEY` 是**强制覆盖**——脚本不回退读取
#:      .env / 环境里的真 key，因此该值只能来自本文件；
#:   ② `_install_transport_stub()` 已把 `OpenAIAdapter.generate` 替换为桩（无网络），
#:      该值不存在任何"被用于发起真实请求"的代码路径；
#:   ③ `git log -S` 检索本行的桩值字面量，只命中 45297cab（本文件**新增**的那一次，
#:      179 行全为新增），即它自诞生起就是该字面量，从未承载过真实值；
#:      （注：本注释块**刻意不复写**该字面量——`# gitleaks:allow` 只放行**本行**，
#:        在此抄写一遍会立刻成为一条新的真实命中，实测已踩到并修正。）
#:   ④ gitleaks 自报 Entropy=1.000000（低熵的描述性字符串，与随机凭证的高熵特征不符）。
#: 反例（为何不退到仓库级宽正则）：实测把 `sk-.*` 加进 allowlist.regexes 后，
#: 一个 `sk-` + 40 位高熵的**真形状** key 也会变成 0 命中（`.tmp-s1109/1a_counterexample.txt`）。
_STUB_KEY = "sk-stub-s1104-not-a-real-key"  # gitleaks:allow 桩值，非凭证（理由见上）

STUB_REPLY = json.dumps({"verdict": "equivalent", "confidence": 0.93,
                         "reason": "s1104 桩回复（无网络）"}, ensure_ascii=False)

_CALLS: list = []


def _install_transport_stub() -> None:
    """把**传输层**换成桩：记录 prompt 并返回 OpenAI 形状的回复；不动其上任何一行"""
    from agent.model_router.adapters import OpenAIAdapter

    def _stub_generate(self, prompt, **kwargs):  # noqa: ANN001
        _CALLS.append({"provider": self.get_provider_name(),
                       "model": self.get_model_name(),
                       "prompt_chars": len(str(prompt))})
        return {"success": True, "content": STUB_REPLY, "finish_reason": "stop",
                "usage": {"prompt_tokens": 412, "completion_tokens": 24,
                          "total_tokens": 436}}

    OpenAIAdapter.generate = _stub_generate


def _facts() -> dict:
    return {"CP_DIGESTION_JUDGE_ENABLED": os.environ.get("CP_DIGESTION_JUDGE_ENABLED", "(未设)"),
            "CP_DIGESTION_JUDGE_PROVIDER": os.environ.get("CP_DIGESTION_JUDGE_PROVIDER", "(未设)"),
            "CP_DIGESTION_JUDGE_MODEL": os.environ.get("CP_DIGESTION_JUDGE_MODEL", "(未设)"),
            "OPENAI_API_KEY": "SET" if os.environ.get("OPENAI_API_KEY") else "UNSET"}


def _report(label: str, *, env: dict) -> dict:
    """跑一条完整链路：ShadowRunner（生产类）构造 → 判定器标签 → 护栏存在性 → 一次判定"""
    from agent.digestion import shadow as SH

    del _CALLS[:]
    runner = SH.ShadowRunner(env=dict(env))
    resolved = SH.resolve_judge("", env=dict(env))       # mode 缺省 = 读 env，默认 auto
    guard = runner.judge_guard
    row = {
        "用例": label,
        "resolve_judge(auto).kind": resolved.kind,
        "is_llm": resolved.is_llm,
        "detail.note/unavailable_reason": (resolved.detail.get("note")
                                           or resolved.detail.get("unavailable_reason") or ""),
        "ShadowRunner.judge.kind": runner.judge.kind,
        "judge_runtime 注入": (runner.runtime is not None),
        "guard._precheck(预算前置拦截)": guard._precheck is not None,
        "guard._on_call(成本记账回调)": guard._on_call is not None,
    }
    # 跑一次真实判定链（传输层是桩）——看它到底走哪条通道、发了几次调用
    score = runner.sandbox.judge("same text", "same text")
    row["一次判定的 score"] = round(float(score), 4)
    row["传输层被调次数(桩计数)"] = len(_CALLS)
    row["判定后 effective_kind"] = guard.effective_kind
    return row


def main() -> int:
    os.environ.pop("CP_DIGESTION_JUDGE_ENABLED", None)
    os.environ.pop("CP_DIGESTION_JUDGE_PROVIDER", None)
    os.environ.pop("CP_DIGESTION_JUDGE_MODEL", None)
    os.environ.pop("CP_DIGESTION_JUDGE", None)
    os.environ["OPENAI_API_KEY"] = _STUB_KEY
    _install_transport_stub()

    print("=" * 78)
    print("S11-04 现象固化：开关关闭 + resolve_judge('auto')")
    print("=" * 78)
    print("进程环境:", json.dumps(_facts(), ensure_ascii=False))
    print("传输层: 桩（OpenAIAdapter.generate 被替换，无网络、无费用）")
    print()

    base = {k: v for k, v in os.environ.items() if k.startswith(("CP_", "LLM_", "OPENAI_"))}
    off = dict(base)
    off["CP_DIGESTION_JUDGE_ENABLED"] = "false"

    rows = []
    # ① 本部署口径：开关关闭（显式 false），且没有 judge 专用 provider/model
    rows.append(_report("① 开关=false，未配 judge provider/model（≈本部署口径）", env=off))
    # ② 开关不写（默认关），同上 —— 与 ① 逐字相同即证明"回落与开关无关"
    rows.append(_report("② 开关未写（默认关），未配 judge provider/model", env=base))
    # ③ 开关=false **+ 配上 provider/model**：一个 _a 键就旁路了 _b 总开关
    configured = dict(off)
    configured["CP_DIGESTION_JUDGE_PROVIDER"] = "deepseek"
    configured["CP_DIGESTION_JUDGE_MODEL"] = "deepseek-v4-flash"
    configured["LLM_BASE_URL"] = "https://api.deepseek.com/v1"
    rows.append(_report("③ 开关=false **+** provider/model 已配（_a 键）", env=configured))
    # ④ 对照：开关=true 且预算=0（走 build_judge_runtime 的护栏链）—— 这才是"受约束"的样子
    rows.append(_guarded_budget_zero_reference(configured))

    keys = list(rows[0].keys())
    for row in rows:
        print("-" * 78)
        for k in keys:
            print(f"  {k}: {row.get(k)}")
    print("=" * 78)
    return 0


def _guarded_budget_zero_reference(env: dict) -> dict:
    """对照项：开关**开**且每日预算=0 ⇒ 护栏生效（前置拦截、不发真实调用、如实标原因）

    这条**不是**现象，而是"受约束应该长什么样"的参照：与用例③**同一条 judge 模型**、
    同样注入桩通道，只多了 `JudgeBudgetGuard` —— 预算为 0 时真实调用被前置拦下，
    `judge_kind` 如实变成 `deterministic_local(budget_exceeded)`，传输层被调次数保持 0。
    """
    import tempfile

    from agent.digestion import judge_runtime as JR

    del _CALLS[:]
    with tempfile.TemporaryDirectory(prefix="s1104_guard_") as tmp:
        runtime = JR.build_judge_runtime(
            JR.JudgeConfig(enabled=True, provider="deepseek", model="deepseek-v4-flash",
                           daily_budget_cents=0.0),
            env={}, dotenv_path=os.path.join(_ROOT, "no-such.env"),
            invoke=_guarded_stub_invoke, events_dir=os.path.join(tmp, "events"),
            emit_fallback_event=False)
        score = runtime.guard("same text", "same text")
    return {
        "用例": "④ 对照：开关=true 且每日预算=0（走 build_judge_runtime 护栏链）",
        "resolve_judge(auto).kind": runtime.resolved.kind,
        "is_llm": runtime.resolved.is_llm,
        "detail.note/unavailable_reason": runtime.availability.reason or "",
        "ShadowRunner.judge.kind": runtime.guard.kind_primary,
        "judge_runtime 注入": True,
        "guard._precheck(预算前置拦截)": runtime.guard._precheck is not None,
        "guard._on_call(成本记账回调)": runtime.guard._on_call is not None,
        "一次判定的 score": round(float(score), 4),
        "传输层被调次数(桩计数)": len(_CALLS),
        "判定后 effective_kind": runtime.guard.effective_kind,
    }


def _guarded_stub_invoke(prompt: str) -> str:
    """护栏对照项的**桩通道**（与传输层桩同款；标注为桩）"""
    _CALLS.append({"provider": "stub", "model": "stub",
                   "prompt_chars": len(str(prompt))})
    return STUB_REPLY


if __name__ == "__main__":
    sys.exit(main())
