# -*- coding: utf-8 -*-
"""审计全链核验作业（2026-11-13，零 token）：把"只在交接卡里跑一次"的全链重算变成每日作业。

【为什么不是直接调用 scripts/audit_governance_check.py】
那个脚本的四门里 **G1 是治理门**（工具确认级别的豁免名单），它反映的是**人的风险接受**
（本机 2026-10-13 由 UI 显式豁免了 delegate/fan_out/write_file/schedule_task/edit），
不是"审计链坏了"。把它塞进每日作业会让作业天天红，最后必然被 || true 掉 ——
那正是本项目反复踩过的"门禁变装饰"。

所以本作业只跑**完整性**三门：
    G2 全链 verify_chain 重算（84k 条）
    G3 每日 Merkle 根（已知历史缺陷只记 warn，不阻断）
    G4 统计口径卫生（非业务 action 占比）
报告落 data/maintenance/audit/audit_verify_<UTC日>.json，控制台打印一行摘要。

用法：python scripts/audit_chain_verify_job.py [--json]
退出码：0 = 完整性通过（含 G3 的历史 warn）；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = REPO_ROOT / "data" / "maintenance" / "audit"
#: 只跑完整性三门（G1 是治理门，见模块 docstring）
GATES = "G2,G3,G4"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="审计全链核验（完整性三门）")
    parser.add_argument("--json", action="store_true", help="只输出一行 JSON 摘要")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import audit_governance_check as check  # type: ignore  # noqa: PLC0415 延迟 import（脚本同目录）

    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            code = check.main(["--only", GATES, "--json"])
    except SystemExit as exc:  # 被调脚本可能直接 sys.exit
        code = int(exc.code or 0)
    text = buffer.getvalue()
    payload = {}
    for line in reversed(text.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    report = {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "gates": GATES,
        "exit_code": code,
        "result": {
            "gates": payload.get("gates"),
            "fails": payload.get("fails") or [],
            "warns": payload.get("warns") or [],
        },
    }
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        target = REPORT_DIR / ("audit_verify_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%d"))
        target.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        report["report"] = str(target.relative_to(REPO_ROOT))
    except OSError as exc:  # noqa: BLE001 报告写不进去不影响判定
        report["report"] = "写入失败:%s" % type(exc).__name__

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print("审计全链核验 %s | %s | 报告 %s"
              % (report["at"], "PASS" if code == 0 else "FAIL", report.get("report", "")))
        for gate, value in (report["result"]["gates"] or {}).items():
            print("  %s %s" % (gate, value))
    return 0 if code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
