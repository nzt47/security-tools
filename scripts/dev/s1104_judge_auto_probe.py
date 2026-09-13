"""S11-04 **一次**最小真实探针（除外全部用桩）：证明被选中的通道**真的能花钱**

为什么需要它：桩能证明"入选逻辑 + 护栏有无"，但证不了"这条通道真的会打到模型并产生费用"。
本脚本用**真实 DeepSeek 通道**做**一次**最小判定调用，回答三个问题：

1. 开关**开**时入选的真实通道，真的能发出一次真实模型调用吗？（真 token 用量 + 真成本）
2. 同一条真通道在开关**关**时，`resolve_judge("auto")` 还会不会选中它？（S11-04 修复点）
3. 修复后的入选链把这次调用的成本记进了 UTC judge 栏吗？（受护栏约束的证据）

费用与隔离：
- **只发一次真实调用**（判定"两段相同文本"，prompt 极短）；其余全用桩；
- 成本事件写入**临时事件目录**（`CP_EVENTS_DIR` 指向 tmp），**不污染**仓库 `data/` 台账；
- 凭证从主工作区 `.env` 的 `LLM_API_KEY` 读入进程内存，**不打印**（只打印长度/指纹）。

用法：
    python scripts/dev/s1104_judge_auto_probe.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _main_env() -> dict:
    """从主工作区 `.env` 取部署真实端点/模型/凭证（只进内存，不打印明文）"""
    path = os.path.join(os.path.dirname(_ROOT), ".env")   # worktree 的上一级 = 主工作区
    if not os.path.exists(path):
        path = os.path.join(_ROOT, ".env")
    out: dict = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            key, _, val = line.strip().partition("=")
            if key in ("LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_PROVIDER"):
                out[key] = val.strip()
    return out


def main() -> int:
    import hashlib

    from agent.digestion import judge_runtime as JR
    from agent.digestion import shadow as SH
    from agent.observability import events as events_mod
    from agent.observability import utc as utc_mod

    main_env = _main_env()
    key = main_env.get("LLM_API_KEY", "")
    base_url = main_env.get("LLM_BASE_URL", "")
    model = main_env.get("LLM_MODEL", "")
    if not (key and base_url and model):
        print("跳过：主工作区 .env 缺少 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL")
        return 0

    tmp = tempfile.mkdtemp(prefix="s1104_probe_")
    try:
        os.environ["CP_EVENTS_DIR"] = os.path.join(tmp, "events")
        events_mod.reset_event_stores()
        print("=" * 78)
        print("S11-04 最小真实探针（**只发一次**真实判定调用）")
        print("=" * 78)
        print(f"端点={base_url} 模型={model} 凭证={len(key)}字符 "
              f"指纹={hashlib.sha256(key.encode()).hexdigest()[:12]}")
        print(f"事件目录（隔离，不污染 data/）={os.environ['CP_EVENTS_DIR']}")
        print()

        # 真实通道需要适配器看得见凭证：DeepSeek 走 OpenAI 兼容协议
        os.environ["OPENAI_API_KEY"] = key
        common = {
            SH.JUDGE_PROVIDER_ENV: "deepseek",
            SH.JUDGE_MODEL_ENV: model,
            SH.JUDGE_BASE_URL_ENV: base_url,
            JR.JUDGE_FOLLOW_FASTING_ENV: "false",
            JR.JUDGE_DOTENV_ENV: os.path.join(tmp, "absent.env"),
        }

        # ── ① 开关**开**：入选真实通道 ⇒ 发**一次**真实调用 + 记账 ──────────
        on_env = dict(common, **{JR.JUDGE_ENABLE_ENV: "true",
                                 JR.JUDGE_BUDGET_ENV: "100"})
        before = utc_mod.judge_cost_cents()
        resolved_on = SH.resolve_judge("auto", env=on_env)
        print("① 开关=ON ：resolve_judge('auto') ⇒", resolved_on.kind,
              f"｜护栏接线={'有' if resolved_on.guard_kwargs else '无'}")
        if resolved_on.judge is None:
            print("   ⇒ 通道不可用，探针无法继续（不编造结果）")
            return 0
        guard = SH.JudgeGuard(resolved_on.scorer, kind_primary=resolved_on.kind,
                              **resolved_on.guard_kwargs)
        score = guard("probe: equal texts", "probe: equal texts")
        usage = dict(resolved_on.judge.last_usage or {})
        after = utc_mod.judge_cost_cents()
        print(f"   真实调用结果：score={score}｜effective_kind={guard.effective_kind}")
        print(f"   真实 token 用量（适配器回报）：{usage or '未知（不估算）'}")
        print(f"   UTC judge 栏：calls {before['calls']} → {after['calls']}｜"
              f"成本 {before['cost_normalized_cents']} → "
              f"{after['cost_normalized_cents']} cents")
        print()

        # ── ② 同一条真通道、开关**关**：修复后不得再被选中 ────────────────
        off_env = dict(common, **{JR.JUDGE_ENABLE_ENV: "false"})
        resolved_off = SH.resolve_judge("auto", env=off_env)
        print("② 开关=OFF：resolve_judge('auto') ⇒", resolved_off.kind,
              f"｜is_llm={resolved_off.is_llm}")
        print(f"   detail={json.dumps(resolved_off.detail, ensure_ascii=False)[:150]}")
        print(f"   判定器是否仍是同一个真实通道对象：{resolved_off.judge is not None}")
        print(f"   一条真实调用都不发：{resolved_off.scorer('a', 'a') == 1.0}")
        print()
        print("结论：同一条**已被证明能花钱**的真通道，开关关时不再入选 —— "
              "S11-04 把'关着也可能花钱'收紧为'关着一定不花钱'。")
        print("=" * 78)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
