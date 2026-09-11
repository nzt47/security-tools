"""§6.7 指标周报（TASK-S5-02）——`scripts/report_slo_weekly.py`

用法::

    # 默认口径：截至今日的 ISO 周窗口 + data/events 事件流
    python scripts/report_slo_weekly.py
    python scripts/report_slo_weekly.py --days 7 --shadow-dir data/digestion/shadow \\
        --feedback-dir data/feedback --out data/reports/slo_weekly.json --md data/reports/slo_weekly.md

    # 拟合 ACR 难度权重（S2-03 遗留 #3；样本不足时如实拒绝产出可考核权重）
    python scripts/report_slo_weekly.py --fit-difficulty

    # 只打印指标字典（定义 / 公式 / 数据源 / 目标）
    python scripts/report_slo_weekly.py --show-dictionary

**口径纪律（本脚本的硬约束）**：

1. 每个数字都带**数据源 + 公式 + 分子/分母 + 样本量**（`--json` 输出里逐项可见）；
2. 数据源缺位 → ``value=None``，**不以 0 冒充**；
3. 样本量 < 20 → 标注"只披露不考核"；
4. 未显式传目录时**不触碰运行时目录**（不创建 `data/` 下的任何文件）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.eval import calibration as CAL  # noqa: E402
from agent.eval import metrics as M  # noqa: E402
from agent.observability.events import iter_events  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="v7.2 §6.7 指标周报（TASK-S5-02）")
    parser.add_argument("--days", type=int, default=7, help="窗口天数（缺省 7 = 周报）")
    parser.add_argument("--start", default="", help="窗口起始日 YYYY-MM-DD")
    parser.add_argument("--end", default="", help="窗口结束日 YYYY-MM-DD")
    parser.add_argument("--events-dir", default="", help="事件流目录（缺省 data/events）")
    parser.add_argument("--shadow-dir", default="", help="灰度台账目录（缺省不读）")
    parser.add_argument("--feedback-dir", default="", help="反馈库目录（缺省不读，👍率列置空）")
    parser.add_argument("--trace-db", default="", help="统一轨迹库（能力级样本量）")
    parser.add_argument("--annotations", default="", help="路由事后标注 JSON（路由准确率）")
    parser.add_argument("--delegations", default="", help="委派行 JSON（委派回收率）")
    parser.add_argument("--registry-path", default="", help="descriptor 台账路径（内化转化率）")
    parser.add_argument("--upstream-rate", type=float, default=None,
                        help="上游通过率（技能成功率对比基准；缺省只披露候选通过率）")
    parser.add_argument("--fit-difficulty", action="store_true",
                        help="拟合 ACR 难度权重（S2-03 遗留 #3）")
    parser.add_argument("--fit-out", default="", help="拟合件输出路径")
    parser.add_argument("--out", default="", help="周报 JSON 输出路径")
    parser.add_argument("--md", default="", help="周报 Markdown 输出路径")
    parser.add_argument("--show-dictionary", action="store_true", help="只打印指标字典")
    parser.add_argument("--json", action="store_true", help="以 JSON 打印（缺省打印 Markdown）")
    return parser


def _load_rows(path: str):
    if not path:
        return None
    if not os.path.exists(path):
        print(f"[WARN] 标注/行文件不存在，按未提供处理: {path}")
        return None
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        for key in ("rows", "annotations", "delegations"):
            if isinstance(data.get(key), list):
                return list(data[key])
        return None
    return list(data) if isinstance(data, list) else None


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.show_dictionary:
        payload = {"dictionary": M.metric_dictionary(),
                   "computable": M.computable_metrics(),
                   "min_samples_for_assessment": M.MIN_SAMPLES_FOR_ASSESSMENT}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    feedback_summary = None
    if args.feedback_dir:
        feedback_summary = M.feedback_summary_from_path(args.feedback_dir, days=args.days)
        if feedback_summary is None:
            print(f"[WARN] 反馈库不存在于 {args.feedback_dir} → 👍率列置空（不创建目录）")

    report = M.compute_metrics(
        days=args.days, start=args.start, end=args.end,
        events_dir=args.events_dir or None,
        registry_path=args.registry_path,
        shadow_dir=args.shadow_dir,
        feedback_summary=feedback_summary,
        annotations=_load_rows(args.annotations),
        delegations=_load_rows(args.delegations),
        upstream_rate=args.upstream_rate,
    )

    if args.fit_difficulty:
        rows = iter_events(since=report["window"]["start"],
                           until=f"{report['window']['end']}\uffff",
                           directory=args.events_dir or None)
        observations = CAL.difficulty_observations(rows)
        fit = CAL.fit_difficulty_weights(observations)
        path = CAL.write_fit(fit, args.fit_out)
        report["acr_difficulty_fit"] = fit.to_dict()
        report["acr_difficulty_fit_path"] = path
        report["acr_switch_status"] = CAL.switch_status(fit=fit)
        print(f"[OK] 难度权重拟合件已写出: {path}"
              f"（status={fit.status}，样本 {fit.sample_count}）")
        print(f"[口径] {report['acr_switch_status']['disclosure']}")

    if args.out or args.md:
        written = M.write_weekly_report(report, args.out, markdown_path=args.md)
        for kind, path in written.items():
            print(f"[OK] 周报（{kind}）已写出: {path}")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    else:
        print(M.render_weekly_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
