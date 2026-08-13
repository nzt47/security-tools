# -*- coding: utf-8 -*-
"""promtail-evolution-log.yaml 的本地 dry-run 验证脚本

用途：正式部署前快速验证 promtail 配置能正确解析 verdict/category 字段。
等价模拟 promtail pipeline 的 regex/labels/template stage（本地无需安装
promtail 二进制）。正则从配置文件动态读取，防止配置与脚本漂移。

运行方式：
  python scripts/verify_promtail_evolution_dryrun.py            # 内置 4 行样例
  python scripts/verify_promtail_evolution_dryrun.py --line "<日志行>"  # 自定义验证

真实 K8s 环境验证（部署后）：
  promtail -config.file=/etc/promtail/evolution-log.yaml -dry-run
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / "deploy" / "k8s" / "promtail-evolution-log.yaml"

# 内置样例（取自 verify_evot4_flow.py 真实输出，覆盖 3 类判定 + 混流）
SAMPLE_LINES = [
    ("proposed", "[PromptOpt] 对比评估 prompt_id=prompt:search:8f91d2a1 category=search "
                 "orig_score=0.3000 orig_status=completed cand_score=0.9997 cand_status=completed "
                 "improvement=2.3323 threshold=0.0300 verdict=proposed"),
    ("no_improvement", "[PromptOpt] 对比评估 prompt_id=prompt:search:9c2e4b77 category=search "
                 "orig_score=0.9997 orig_status=completed cand_score=0.9997 cand_status=completed "
                 "improvement=0.0000 threshold=0.0300 verdict=no_improvement"),
    ("no_samples", "[PromptOpt] 对比评估 prompt_id=prompt:chat:7a1f9d02 category=chat "
                 "orig_score=0.0000 orig_status=no_samples cand_score=0.0000 cand_status=no_samples "
                 "improvement=N/A threshold=0.0300 verdict=no_samples"),
    ("mixed_line", "2026-08-13 00:00:00 [INFO] agent.utils.singleton_manager | "
                 "[SingletonManager] 注册单例: audit_logger"),
]


def _load_expressions() -> list[re.Pattern]:
    """从 promtail-evolution-log.yaml 读取 regex stage 的 expression（防漂移）。

    零依赖实现：正则提取 `expression: "..."` 行；顺序 = 配置中 category→verdict。
    """
    text = CONFIG_PATH.read_text(encoding="utf-8")
    exprs = re.findall(r'expression:\s*"([^"]+)"', text)
    if len(exprs) < 2:
        print(f"FAIL 配置中未找到 2 个 regex expression（实际 {len(exprs)} 个），"
              f"请检查 {CONFIG_PATH}", file=sys.stderr)
        raise SystemExit(2)
    return [re.compile(e) for e in exprs]


def parse(line: str, exprs: list[re.Pattern]) -> dict:
    """模拟 pipeline_stages：regex×N → labels（仅返回匹配的命名组）"""
    labels = {}
    for rx in exprs:
        m = rx.search(line)
        if m:
            labels.update(m.groupdict())
    return labels


def run() -> int:
    exprs = _load_expressions()
    print("== promtail -dry-run 模拟（job=yunshu-evolution）==")
    print(f"  读取配置: {CONFIG_PATH}")
    for e in exprs:
        print(f"  expression: {e.pattern!r}")
    print()

    ok = True
    for expect, line in SAMPLE_LINES:
        labels = parse(line, exprs)
        msg = f'prompt_opt[verdict={labels.get("verdict", "")}][cat={labels.get("category", "")}]'
        print(f'level=info ts=2026-08-13T00:00:00.000Z msg="entry" '
              f'job=yunshu-evolution labels={labels} line={msg!r}')
        expected = {
            "proposed": {"category": "search", "verdict": "proposed"},
            "no_improvement": {"category": "search", "verdict": "no_improvement"},
            "no_samples": {"category": "chat", "verdict": "no_samples"},
            "mixed_line": {},
        }[expect]
        ok &= labels == expected

    print("\nPASS verdict/category 提取正确，3 类判定 + 混流行均符合预期"
          if ok else "\nFAIL 解析与预期不符")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="promtail-evolution-log.yaml dry-run 模拟")
    ap.add_argument("--line", help="自定义日志行验证（替换内置样例）")
    args = ap.parse_args()
    if args.line:
        exprs = _load_expressions()
        labels = parse(args.line, exprs)
        print(f"labels={labels}")
        sys.exit(0 if labels.get("verdict") else 1)
    sys.exit(run())
