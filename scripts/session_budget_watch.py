# -*- coding: utf-8 -*-
"""会话预算看护：读 DeepSeek Harness 当前会话的 token 用量，超过阈值就提示换会话。

【为什么需要】Harness 的自动压缩并不是界面文案写的 80%：实际触发点是
    floor(min(W * thresholdRatio, W - O - headroomTokens))
其中 W=上下文窗口、O=模型单次最大输出、headroom=安全余量（默认 65,536）。
本机 W=1,000,000、O=256,000、headroom=65,536 ⇒ 实际在 678,464 tokens（67.8%）就压缩了。
所以在 65% 左右提醒「可以换会话」才来得及 —— 压缩一旦发生，早期上下文只剩摘要。

【它做什么】只读最新 session.v4.jsonl.zstd 的最后一次 usage，算占比：
  - 打印一行中文结论 + JSON；
  - 超过 --warn（默认 65%）时 exit code = 1（便于挂计划任务/脚本判断）。

【口径】不写任何 DSH 文件；不打印提示词内容（只看 usage 数字）。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

try:
    import zstandard as _zstd
except Exception:  # noqa: BLE001
    _zstd = None

DSH_HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
DEFAULT_WINDOW = 1_000_000
USAGE_RE = re.compile(r'"inputTokens":(\d+),"outputTokens":(\d+),"cacheReadTokens":(\d+)')


def newest_session_log() -> Optional[Path]:
    paths = glob.glob(str(DSH_HOME / "sessions" / "*" / "*" / "session.v4.jsonl.zstd"))
    if not paths:
        return None
    return Path(max(paths, key=lambda p: os.path.getmtime(p)))


def read_window() -> int:
    """从 profile patch 读 contextWindow（读不到就用 1,000,000）。"""
    patch = DSH_HOME / "profiles" / "desktop" / "cordis.patch.yml"
    try:
        text = patch.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DEFAULT_WINDOW
    hits = re.findall(r"contextWindow:\s*(\d+)", text)
    if not hits:
        return DEFAULT_WINDOW
    return max(int(h) for h in hits)


def last_usage(path: Path) -> Dict[str, int]:
    if _zstd is None:
        raise SystemExit("缺 zstandard：pip install zstandard")
    dctx = _zstd.ZstdDecompressor()
    with open(path, "rb") as f:
        text = dctx.stream_reader(f).read().decode("utf-8", "replace")
    last = None
    for m in USAGE_RE.finditer(text):
        last = m
    if last is None:
        return {"prompt": 0, "output": 0, "cache": 0}
    prompt_in = int(last.group(1))
    output = int(last.group(2))
    cache = int(last.group(3))
    return {"prompt": prompt_in + cache, "output": output, "cache": cache}


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="会话预算看护（只读）")
    parser.add_argument("--warn", type=float, default=65.0, help="提醒阈值（百分比，默认 65）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    args = parser.parse_args(argv)

    path = newest_session_log()
    if path is None:
        print("找不到会话日志（DSH 至少跑过一轮才会写）")
        return 2
    usage = last_usage(path)
    window = read_window()
    used = usage["prompt"]
    ratio = (used / window * 100.0) if window else 0.0
    trigger = window - 256_000 - 65_536  # W - O - headroom（与 Harness 公式同口径）
    trigger_ratio = (trigger / window * 100.0) if window else 0.0
    payload: Dict[str, Any] = {
        "session": path.parent.name,
        "prompt_tokens": used,
        "output_tokens": usage["output"],
        "window": window,
        "used_percent": round(ratio, 1),
        "auto_compact_at_percent": round(trigger_ratio, 1),
        "warn_at_percent": args.warn,
    }
    payload["should_switch"] = ratio >= args.warn
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print("会话 %s：约 %s / %s tokens（%.1f%%）" % (
            payload["session"], f"{used:,}", f"{window:,}", ratio))
        print("自动压缩实际触发点在 %.1f%%（W - 最大输出 256k - 余量 64k），不是 80%%" % trigger_ratio)
        if payload["should_switch"]:
            print("⚠ 已过 %.0f%%：建议现在换新会话，从交接卡继续（python scripts/handoff_snapshot.py 生成）" % args.warn)
        else:
            print("还有余量：到 %.0f%% 我会提示换会话" % args.warn)
    return 1 if payload["should_switch"] else 0


if __name__ == "__main__":
    raise SystemExit(main())