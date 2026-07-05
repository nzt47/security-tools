"""交互式 SignalScorer 观察脚本

使用方式:
    # 交互式逐条输入
    python scripts/interactive_signal_scorer.py

    # 批量模式: 直接传入 JSON 数组
    python scripts/interactive_signal_scorer.py --batch '[{"success": false, "tools": 5, "params": 2, "session": "s1"}]'

    # 预置 50 条无 comment 演示数据集
    python scripts/interactive_signal_scorer.py --demo

    # 指定阈值
    python scripts/interactive_signal_scorer.py --threshold 0.5 --demo

交互模式下按 Ctrl+C 退出, 输入 'q' 也可退出。
"""
from __future__ import annotations
import argparse
import json
import sys
import logging
from typing import List, Optional

# 确保项目根目录在 sys.path
import os
sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..")))

from agent.skills_mgmt.memory_abstractor import MemoryEntry
from agent.skills_mgmt.signal_scorer import SignalScorer, SignalBreakdown


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────

def _build_entry(source_id: str,
                  success: bool,
                  tool_count: int,
                  param_count: int,
                  session_id: str = "",
                  task_text: str = "",
                  ) -> MemoryEntry:
    """构造 MemoryEntry"""
    return MemoryEntry(
        source="interactive",
        source_id=source_id,
        task_text=task_text,
        success=success,
        tool_calls=[{"name": f"tool_{i}"} for i in range(tool_count)],
        params={f"param_{i}": i for i in range(param_count)},
        tags=[],
        timestamp="2026-07-05T10:00:00",
        session_id=session_id,
    )


def _print_breakdown(source_id: str, total: float, bd: SignalBreakdown) -> None:
    """格式化输出评分明细"""
    weight_set = "DEGRADED (无 comment)" if not bd.emotion_available else "DEFAULT"
    print(f"\n┌─ {source_id} ─────────────────────────────")
    print(f"│ 总分: {total:.3f}  (权重集: {weight_set})")
    print(f"│ emotion_available: {bd.emotion_available}")
    print(f"│")
    print(f"│ 维度          原始分   权重    加权贡献")
    print(f"│ ─────────────────────────────────────────")
    for dim in ["emotion", "pain", "effort", "novelty", "recurrence"]:
        raw = getattr(bd, dim)
        w = bd.weights.get(dim, 0.0)
        contrib = raw * w
        marker = " ◀ 降级" if dim == "emotion" and not bd.emotion_available else ""
        print(f"│ {dim:12s}  {raw:6.3f}   {w:5.2f}    {contrib:6.3f}{marker}")
    print(f"│ ─────────────────────────────────────────")
    print(f"│ 合计                    1.00    {total:6.3f}")
    print(f"└─────────────────────────────────────────────")
    if total >= 0.4:
        print(f"  ✅ PASS (>= 0.4 阈值)")
    else:
        print(f"  ❌ FILTER (< 0.4 阈值)")


def _print_batch_summary(entries: List[MemoryEntry],
                          scorer: SignalScorer,
                          threshold: float,
                          ) -> None:
    """批量评分后输出汇总"""
    print(f"\n{'='*60}")
    print(f"批量评分汇总  (阈值 = {threshold})")
    print(f"{'='*60}")
    # 评分
    for e in entries:
        total, _ = scorer.score(e, entries, [])
        e.signal_strength = total
    # 过滤
    kept = scorer.filter_high_value(entries, threshold=threshold)
    # 汇总
    passed = len(kept)
    filtered = len(entries) - passed
    avg_signal = sum(e.signal_strength for e in entries) / len(entries) if entries else 0
    print(f"  输入: {len(entries)} 条")
    print(f"  通过: {passed} 条")
    print(f"  过滤: {filtered} 条")
    print(f"  平均信号强度: {avg_signal:.3f}")
    print()
    # 列表
    print(f"  {'source_id':20s}  {'signal':>7s}  {'status':>8s}  "
          f"{'success':>7s}  {'tools':>5s}  {'session':>12s}")
    print(f"  {'-'*20}  {'-'*7}  {'-'*8}  {'-'*7}  {'-'*5}  {'-'*12}")
    for e in sorted(entries, key=lambda x: -x.signal_strength):
        status = "PASS" if e.signal_strength >= threshold else "FILTER"
        sess = e.session_id or "-"
        print(f"  {e.source_id:20s}  {e.signal_strength:7.3f}  "
              f"{status:>8s}  {str(e.success):>7s}  "
              f"{len(e.tool_calls):5d}  {sess:>12s}")


# ──────────────────────────────────────────────
# 交互模式
# ──────────────────────────────────────────────

def _prompt_input(prompt: str, default: str = "") -> str:
    """带默认值的输入"""
    if default:
        s = input(f"{prompt} [{default}]: ").strip()
        return s if s else default
    return input(f"{prompt}: ").strip()


def interactive_mode(threshold: float, verbose: bool) -> None:
    """交互式逐条输入"""
    print(f"\nSignalScorer 交互式观察  (阈值 = {threshold})")
    print(f"提示: 输入 'q' 退出, 'summary' 查看汇总\n")

    scorer = SignalScorer(filter_threshold=threshold)
    entries: List[MemoryEntry] = []
    counter = 0

    while True:
        try:
            line = input(f"\n[Entry #{counter}] success (y/n, q退出): ").strip().lower()
            if line == "q":
                break
            if line == "summary":
                if entries:
                    _print_batch_summary(entries, scorer, threshold)
                else:
                    print("  (无数据)")
                continue
            success = line.startswith("y") or line.startswith("t")

            task_text = input("  task_text (回车=无comment, 模拟仅rating): ").strip()
            # 默认为空 → 触发降级
            if task_text.lower() in ("", "none", "null"):
                task_text = ""

            tools_str = input("  工具数量 (回车=0): ").strip()
            tool_count = int(tools_str) if tools_str else 0

            params_str = input("  参数数量 (回车=0): ").strip()
            param_count = int(params_str) if params_str else 0

            session = input("  session_id (回车=无): ").strip()
            if session.lower() in ("none", "null"):
                session = ""

            counter += 1
            entry = _build_entry(
                source_id=f"interactive-{counter}",
                success=success,
                tool_count=tool_count,
                param_count=param_count,
                session_id=session,
                task_text=task_text,
            )
            entries.append(entry)

            # 评分 (用所有已输入的 entries 作为 all_entries)
            total, bd = scorer.score(entry, entries, [])
            entry.signal_strength = total
            _print_breakdown(entry.source_id, total, bd)

        except ValueError as e:
            print(f"  输入错误: {e}")
        except KeyboardInterrupt:
            break
        except EOFError:
            break

    if entries:
        print("\n" + "="*60)
        print("最终汇总")
        print("="*60)
        _print_batch_summary(entries, scorer, threshold)


# ──────────────────────────────────────────────
# 批量模式
# ──────────────────────────────────────────────

def batch_mode(json_data: str, threshold: float) -> None:
    """批量模式: 从 JSON 数组读取"""
    data = json.loads(json_data)
    entries: List[MemoryEntry] = []
    for i, item in enumerate(data):
        entry = _build_entry(
            source_id=item.get("source_id", f"batch-{i}"),
            success=bool(item.get("success", True)),
            tool_count=int(item.get("tools", 0)),
            param_count=int(item.get("params", 0)),
            session_id=item.get("session", ""),
            task_text=item.get("task_text", ""),
        )
        entries.append(entry)
    scorer = SignalScorer(filter_threshold=threshold)
    for e in entries:
        total, bd = scorer.score(e, entries, [])
        e.signal_strength = total
        _print_breakdown(e.source_id, total, bd)
    _print_batch_summary(entries, scorer, threshold)


# ──────────────────────────────────────────────
# 演示模式 — 50 条无 comment 数据
# ──────────────────────────────────────────────

def demo_mode(threshold: float) -> None:
    """演示模式: 50 条无 comment 数据集"""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tests", "unit"))
    from test_signal_scorer_50_no_comment import build_50_no_comment_dataset
    entries = build_50_no_comment_dataset()
    scorer = SignalScorer(filter_threshold=threshold)
    for e in entries:
        total, _ = scorer.score(e, entries, [])
        e.signal_strength = total
    _print_batch_summary(entries, scorer, threshold)
    print(f"\n提示: 运行 `python -m pytest tests/unit/test_signal_scorer_50_no_comment.py -v`")
    print(f"      可查看完整测试断言")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SignalScorer 交互式观察脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 交互式逐条输入 (默认)
  python scripts/interactive_signal_scorer.py

  # 预置 50 条无 comment 演示
  python scripts/interactive_signal_scorer.py --demo

  # 批量模式 (JSON)
  python scripts/interactive_signal_scorer.py --batch \\
    '[{"success": false, "tools": 5, "params": 2, "session": "s1"},
      {"success": true, "tools": 0, "params": 0}]'

  # 自定义阈值
  python scripts/interactive_signal_scorer.py --demo --threshold 0.5

  # 启用 DEBUG 日志查看维度详情
  python scripts/interactive_signal_scorer.py --demo --log-level DEBUG
""",
    )
    parser.add_argument("--threshold", type=float, default=0.4,
                        help="过滤阈值 (默认 0.4)")
    parser.add_argument("--batch", type=str, default=None,
                        help="批量模式: 传入 JSON 数组")
    parser.add_argument("--demo", action="store_true",
                        help="演示模式: 50 条无 comment 数据集")
    parser.add_argument("--log-level", type=str, default="WARNING",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
                        help="日志级别 (默认 WARNING, DEBUG 显示维度明细)")
    args = parser.parse_args()

    # 配置日志
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.demo:
        demo_mode(args.threshold)
    elif args.batch:
        batch_mode(args.batch, args.threshold)
    else:
        interactive_mode(args.threshold, verbose=args.log_level == "DEBUG")


if __name__ == "__main__":
    main()
