"""TASK-02 观察窗口监控脚本（experience_persist 保持 false 期间使用）

周期性采样并记录：
  1. 三开关（reflection_persist / critic_evaluation_enabled / experience_persist）
     的三层优先级最终生效值（环境变量 > config.yaml > 默认值）
  2. learning.eval.* 指标趋势（total/passed/failed 计数器 + score 直方图统计）

用途：
  - 观察窗口期（首期 experience_persist=false）自动记录开关状态，确认开关未被
    环境变量/并行会话意外改动
  - 评估指标趋势：eval.total 随交互递增、passed 同步、failed 无异常增长、score 稳定
  - 采样值落 CSV 供趋势分析（指标绝对值随进程累积，观察增量）

用法:
  python scripts/monitor_task02_observation.py -n 5 -i 60            # 5 次，每 60s
  python scripts/monitor_task02_observation.py --forever -i 300      # 无限采样，每 5min
  python scripts/monitor_task02_observation.py -n 3 -o monitor.csv   # 落盘 CSV
  python scripts/monitor_task02_observation.py -n 1 --prometheus     # Prometheus 文本格式输出

【不易】只读监控：不修改配置、不写指标、不触发接线；采样失败单项降级不影响整体。

【输出格式】
  - 默认：自描述文本（人类可读，带时间戳），适合终端观察
  - --prometheus：符合 Prometheus 文本暴露格式（text/plain exposition），
    对齐 agent/monitoring/metrics.py 的 export_prometheus 约定：
      counter → `# TYPE <name> counter`；histogram → `# TYPE <name> summary`；
      开关建模为 gauge（1=on/0=off）；metric 名点转下划线（learning.eval.total → learning_eval_total）；
      无数据的指标不输出行

【能力边界（诚实声明）】
  - 三开关状态：跨进程真实有效（读 config.yaml + 环境变量，独立进程结论一致）
  - learning.eval.* 指标：get_metrics_collector() 为进程内单例——独立进程采样不到
    主进程的累积值（本脚本进程无对话运行时指标为 None）。生产观察窗口的指标趋势
    应从 Prometheus /metrics 端点查询 learning.eval.*（见上线检查清单 §2.4），
    本脚本的指标读取适用于同进程嵌入或调试采样。
"""

import argparse
import csv
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根目录

from agent.monitoring.metrics import get_metrics_collector
from agent.orchestrator.orchestrator import Orchestrator
from planning.core import PlanningCore

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger("monitor_task02")


def _sample_switches() -> dict:
    """采样三开关最终生效值（异常单开关降级为 None，不中断监控）"""
    out = {"reflection_persist": None, "critic_evaluation_enabled": None, "experience_persist": None}
    try:
        lc = Orchestrator._load_learning_config()
        out["reflection_persist"] = lc["reflection_persist"]
        out["critic_evaluation_enabled"] = lc["critic_evaluation_enabled"]
    except Exception as e:  # 三义：健壮但直白，不阻塞监控
        logger.warning(f"[monitor] 读取学习接线配置失败: {e}")
    try:
        out["experience_persist"] = PlanningCore._load_experience_persist_config()
    except Exception as e:
        logger.warning(f"[monitor] 读取经验落盘配置失败: {e}")
    return out


def _sample_eval_metrics() -> dict:
    """采样 learning.eval.* 指标（counters + score 直方图统计）"""
    out = {"eval_total": None, "eval_passed": None, "eval_failed": None,
           "score_mean": None, "score_min": None, "score_max": None, "score_samples": None}
    try:
        all_metrics = get_metrics_collector().get_all_metrics()
        counters = all_metrics.get("counters", {})
        histograms = all_metrics.get("histograms", {})
        for key, name in [("eval_total", "learning.eval.total"),
                          ("eval_passed", "learning.eval.passed"),
                          ("eval_failed", "learning.eval.failed")]:
            if name in counters:
                out[key] = counters[name]
        if "learning.eval.score" in histograms:
            st = histograms["learning.eval.score"]
            out["score_mean"] = st.get("mean")
            out["score_min"] = st.get("min")
            out["score_max"] = st.get("max")
            out["score_samples"] = st.get("count")
    except Exception as e:
        logger.warning(f"[monitor] 读取 eval 指标失败: {e}")
    return out


def _row(switches: dict, eval_metrics: dict) -> dict:
    row = {"ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    row.update(switches)
    row.update(eval_metrics)
    return row


def _prometheus_lines(switches: dict, eval_metrics: dict) -> str:
    """Prometheus 文本暴露格式（text/plain exposition）

    对齐 metrics.py export_prometheus 约定：
      - counter / summary 类型 + `# HELP` / `# TYPE` 注释
      - 开关建模为 gauge（1=on / 0=off）
      - metric 名点转下划线；无数据（None）的指标不输出行
    """
    lines = []
    # 开关 → gauge（布尔 None 视为无数据，不输出）
    for switch in ("reflection_persist", "critic_evaluation_enabled", "experience_persist"):
        v = switches.get(switch)
        if v is None:
            continue
        m = f"learning_config_{switch}"
        lines.append(f"# HELP {m} TASK-02 开关生效值 (1=on, 0=off)")
        lines.append(f"# TYPE {m} gauge")
        lines.append(f"{m} {1 if v else 0}")
    # eval 计数器 → counter
    for key, name in [("eval_total", "learning.eval.total"),
                      ("eval_passed", "learning.eval.passed"),
                      ("eval_failed", "learning.eval.failed")]:
        v = eval_metrics.get(key)
        if v is None:
            continue
        m = name.replace(".", "_")
        lines.append(f"# HELP {m} Counter metric")
        lines.append(f"# TYPE {m} counter")
        lines.append(f'{m}{{service="{m}"}} {v}')
    # score 直方图 → summary（对齐 export_prometheus：sum/count/p95）
    if eval_metrics.get("score_samples"):
        m = "learning_eval_score"
        lines.append(f"# HELP {m} Summary metric")
        lines.append(f"# TYPE {m} summary")
        lines.append(f'{m}_sum{{service="{m}"}} {eval_metrics["score_mean"] * eval_metrics["score_samples"]}')
        lines.append(f'{m}_count{{service="{m}"}} {eval_metrics["score_samples"]}')
        lines.append(f'{m}{{service="{m}",quantile="0.95"}} {eval_metrics["score_max"]}')
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="TASK-02 观察窗口监控（开关状态 + eval 指标趋势）")
    ap.add_argument("-n", "--samples", type=int, default=5, help="采样次数（0 表示无限，与 --forever 同义）")
    ap.add_argument("-i", "--interval", type=float, default=60.0, help="采样间隔秒（默认 60）")
    ap.add_argument("-o", "--output", default=None, help="CSV 输出路径（默认仅控制台）")
    ap.add_argument("--forever", action="store_true", help="无限采样（Ctrl+C 停止）")
    ap.add_argument("--prometheus", action="store_true", help="输出 Prometheus 文本暴露格式（对齐 export_prometheus）")
    args = ap.parse_args()

    forever = args.forever or args.samples <= 0
    csv_path = Path(args.output) if args.output else None

    print("=" * 78)
    print(f"TASK-02 观察窗口监控 | 模式={'无限' if forever else f'{args.samples} 次'} | 间隔={args.interval}s"
          + (" | 输出=Prometheus" if args.prometheus else ""))
    print("开关: reflection_persist / critic_evaluation_enabled / experience_persist | 指标: learning.eval.*")
    print("=" * 78)

    f = None
    writer = None
    if csv_path:
        new_file = not csv_path.exists()
        f = csv_path.open("a", newline="", encoding="utf-8")
        writer = csv.DictWriter(f, fieldnames=["ts", "reflection_persist", "critic_evaluation_enabled",
                                               "experience_persist", "eval_total", "eval_passed",
                                               "eval_failed", "score_mean", "score_min", "score_max",
                                               "score_samples"])
        if new_file:
            writer.writeheader()

    count = 0
    try:
        while forever or count < args.samples:
            switches = _sample_switches()
            eval_metrics = _sample_eval_metrics()
            row = _row(switches, eval_metrics)

            if args.prometheus:
                block = _prometheus_lines(switches, eval_metrics)
                print(block if block else "# (no metrics available)")
            else:
                line = (f"[{row['ts']}] reflection_persist={row['reflection_persist']} "
                        f"critic_evaluation_enabled={row['critic_evaluation_enabled']} "
                        f"experience_persist={row['experience_persist']} | "
                        f"eval.total={row['eval_total']} passed={row['eval_passed']} failed={row['eval_failed']} | "
                        f"score(mean/min/max/n)={row['score_mean']}/{row['score_min']}/{row['score_max']}/{row['score_samples']}")
                print(line)
            if writer:
                writer.writerow(row)
                f.flush()

            count += 1
            if forever or count < args.samples:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n[monitor] 采样已停止（Ctrl+C）")
    finally:
        if f:
            f.close()

    print(f"[monitor] 完成，共采样 {count} 次"
          + (f"，记录于 {csv_path}" if csv_path else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
