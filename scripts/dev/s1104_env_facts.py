"""S11-04 事实核对：judge 开关 / 凭证键在**部署环境**里的实际取值（只读，不打印明文）

用途：把"开关关闭 + auto"能否走到 LLM 通道这件事的前置条件**固化下来**，
避免拿"本机恰好没配"当"代码必然回落"。

只输出 SET/UNSET 与长度，不输出任何明文值。
"""
from __future__ import annotations

import os
import sys

KEYS = [
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "CP_DIGESTION_JUDGE_ENABLED",
    "CP_DIGESTION_JUDGE_PROVIDER",
    "CP_DIGESTION_JUDGE_MODEL",
    "CP_DIGESTION_JUDGE_DAILY_BUDGET_CENTS",
    "CP_DIGESTION_JUDGE",
    "LLM_API_KEY",
    "LLM_PROVIDER",
    "LLM_MODEL",
    "LLM_BASE_URL",
]


def main() -> int:
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    dotenv = os.path.join(root, ".env")
    print("== os.environ（本进程真实环境） ==")
    for k in KEYS:
        v = os.environ.get(k)
        print(f"  {k}: {'SET(len=%d)' % len(v) if v else 'UNSET'}")

    print("== .env 文件（不打印值，只打印键与长度） ==")
    found = {}
    if os.path.exists(dotenv):
        with open(dotenv, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                key, _, val = line.strip().partition("=")
                if key in KEYS:
                    found[key] = len(val.strip())
    for k in KEYS:
        print(f"  {k}: {found.get(k, 'ABSENT')}")
    print("  CP_DIGESTION* 共:", [k for k in found if k.startswith("CP_DIGESTION")] or "NONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
