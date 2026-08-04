"""训练监控脚本 — 实时解析 finetune_reranker.py 日志并可视化

【不易】不修改训练脚本,仅作为旁路监控(只读日志)
【变易】支持 tail -f 式持续监控 + 异常检测 + ASCII 曲线
【简易】单文件运行,无外部依赖(仅标准库)

用法:
    python monitor_train.py --log train.log
    python monitor_train.py --log train.log --follow        # 持续监控(类似 tail -f)
    python monitor_train.py --log train.log --interval 2    # 每 2 秒刷新一次

功能:
    1. 解析日志中的 epoch / train_loss / val_loss / val_acc
    2. 实时显示训练进度
    3. 检测异常: NaN loss / OOM / 早停
    4. 估算剩余时间(基于已完成 epoch 的平均耗时)
    5. 输出 ASCII 训练曲线
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------- 数据结构 ----------
@dataclass
class EpochRecord:
    """单个 epoch 的训练记录"""
    epoch: int
    total_epochs: int
    train_loss: float
    val_loss: float
    val_acc: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class TrainState:
    """训练整体状态"""
    stage: str = "pending"  # pending / loading_data / loading_model / applying_lora / training / saving / done / failed
    epochs: list[EpochRecord] = field(default_factory=list)
    total_epochs: int = 0
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    anomalies: list[str] = field(default_factory=list)
    early_stopped: bool = False
    best_epoch: int = 0
    best_val_loss: float = float("inf")
    final_val_acc: Optional[float] = None
    train_time_sec: Optional[float] = None


# ---------- 日志正则 ----------
# 匹配: "[1/5] 加载训练数据..."
STAGE_PATTERN = re.compile(
    r"\[(\d+)/\d+\]\s*(.+?)(?:\.\.\.|$)"
)
# 匹配: "Epoch 3/5: train_loss=0.1234 val_loss=0.2345 val_acc=85.71%"
EPOCH_PATTERN = re.compile(
    r"Epoch\s+(\d+)/(\d+):\s*train_loss=([\d.]+)\s*val_loss=([\d.]+)\s*val_acc=([\d.]+)%"
)
# 匹配: "训练集: 490 样本 (正:245, 负:245)"
DATA_INFO_PATTERN = re.compile(r"训练集:\s*(\d+)\s*样本")
# 匹配: "早停:连续 2 个 epoch 无改善"
EARLY_STOP_PATTERN = re.compile(r"早停:连续\s*(\d+)\s*个\s*epoch\s*无改善")
# 匹配: "最佳 epoch: 3(val_loss=0.2345)"
BEST_EPOCH_PATTERN = re.compile(r"最佳\s*epoch:\s*(\d+)\s*\(val_loss=([\d.]+)\)")
# 匹配: "训练耗时: 123.4s"
TRAIN_TIME_PATTERN = re.compile(r"训练耗时:\s*([\d.]+)s")
# 匹配: "验证集准确率: 85.71%"
FINAL_ACC_PATTERN = re.compile(r"验证集准确率:\s*([\d.]+)%")
# 匹配: "=== 训练完成 ==="
DONE_PATTERN = re.compile(r"===\s*训练完成\s*===")


def detect_anomaly(line: str) -> Optional[str]:
    """检测单行日志中的异常信号

    Returns:
        异常描述字符串,无异常返回 None
    """
    line_lower = line.lower()
    # NaN / Inf 检测
    if "nan" in line_lower and ("loss" in line_lower or "loss=" in line_lower):
        return f"检测到 NaN loss: {line.strip()}"
    if "inf" in line_lower and "loss=" in line_lower:
        return f"检测到 Inf loss: {line.strip()}"
    # OOM 检测
    if "out of memory" in line_lower or "cuda oom" in line_lower:
        return f"显存溢出 (OOM): {line.strip()}"
    if "runtimeerror" in line_lower and "memory" in line_lower:
        return f"运行时内存错误: {line.strip()}"
    # 原生崩溃
    if "0xc0000005" in line_lower:
        return f"原生崩溃 (0xC0000005): {line.strip()}"
    # CUDA 错误
    if "cuda error" in line_lower or "cudafetching" in line_lower:
        return f"CUDA 错误: {line.strip()}"
    return None


def parse_line(line: str, state: TrainState) -> Optional[str]:
    """解析单行日志,更新 state,返回该行触发的异常(若有)

    Returns:
        异常字符串或 None
    """
    line = line.rstrip()
    if not line:
        return None

    # 异常检测(优先级最高)
    anomaly = detect_anomaly(line)
    if anomaly:
        state.anomalies.append(anomaly)
        return anomaly

    # 阶段切换
    stage_match = STAGE_PATTERN.search(line)
    if stage_match:
        stage_num = int(stage_match.group(1))
        stage_msg = stage_match.group(2).strip()
        stage_map = {
            1: "loading_data",
            2: "loading_model",
            3: "applying_lora",
            4: "training",
            5: "saving",
        }
        state.stage = stage_map.get(stage_num, state.stage)
        if state.started_at is None:
            state.started_at = time.time()
        return None

    # Epoch 记录
    epoch_match = EPOCH_PATTERN.search(line)
    if epoch_match:
        epoch = int(epoch_match.group(1))
        total = int(epoch_match.group(2))
        train_loss = float(epoch_match.group(3))
        val_loss = float(epoch_match.group(4))
        val_acc = float(epoch_match.group(5)) / 100.0
        state.total_epochs = total
        record = EpochRecord(
            epoch=epoch, total_epochs=total,
            train_loss=train_loss, val_loss=val_loss, val_acc=val_acc,
        )
        state.epochs.append(record)
        return None

    # 早停
    early_match = EARLY_STOP_PATTERN.search(line)
    if early_match:
        state.early_stopped = True
        return None

    # 最佳 epoch
    best_match = BEST_EPOCH_PATTERN.search(line)
    if best_match:
        state.best_epoch = int(best_match.group(1))
        state.best_val_loss = float(best_match.group(2))
        return None

    # 训练耗时
    time_match = TRAIN_TIME_PATTERN.search(line)
    if time_match:
        state.train_time_sec = float(time_match.group(1))
        return None

    # 最终准确率
    acc_match = FINAL_ACC_PATTERN.search(line)
    if acc_match:
        state.final_val_acc = float(acc_match.group(1)) / 100.0
        return None

    # 训练完成
    if DONE_PATTERN.search(line):
        state.stage = "done"
        state.finished_at = time.time()
        return None

    return None


def estimate_remaining(state: TrainState) -> Optional[float]:
    """估算剩余时间(秒)

    基于已完成 epoch 的平均耗时,推算剩余 epoch 所需时间。
    """
    if len(state.epochs) < 1:
        return None
    if state.started_at is None:
        return None
    now = time.time()
    elapsed = now - state.started_at
    completed = len(state.epochs)
    if completed == 0:
        return None
    avg_per_epoch = elapsed / completed
    remaining_epochs = max(state.total_epochs - completed, 0)
    return avg_per_epoch * remaining_epochs


def format_duration(seconds: Optional[float]) -> str:
    """将秒数格式化为人类可读时长"""
    if seconds is None:
        return "未知"
    if seconds < 0:
        return "未知"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m{seconds % 60}s"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}h{m}m"


def render_ascii_curve(state: TrainState, width: int = 50, height: int = 10) -> str:
    """渲染 ASCII 训练曲线(loss 趋势)

    【简易】用字符画双曲线(train_loss + val_loss),便于终端查看
    """
    if not state.epochs:
        return "  (暂无 epoch 数据)"

    losses = [(e.train_loss, e.val_loss) for e in state.epochs]
    all_vals = [v for pair in losses for v in pair]
    if not all_vals:
        return "  (暂无 loss 数据)"

    max_loss = max(all_vals) if all_vals else 1.0
    min_loss = min(all_vals) if all_vals else 0.0
    range_loss = max(max_loss - min_loss, 1e-6)

    n = len(losses)
    # 每列对应一个 epoch(若 epoch 数 > width,按 epoch 等距采样)
    cols = max(n, 1)

    lines = []
    lines.append("  Loss 趋势 (T=train, V=val, *=both):")
    lines.append(f"  {max_loss:.4f} |" + " " * (cols + 2))

    for row in range(height, 0, -1):
        threshold = min_loss + range_loss * (row - 1) / height
        line_str = f"  {threshold:.4f} |"
        for i in range(cols):
            if i < n:
                tl, vl = losses[i]
                has_t = tl >= threshold
                has_v = vl >= threshold
                if has_t and has_v:
                    line_str += "*"
                elif has_t:
                    line_str += "T"
                elif has_v:
                    line_str += "V"
                else:
                    line_str += " "
            else:
                line_str += " "
        lines.append(line_str)

    lines.append(f"  {min_loss:.4f} |" + "-" * cols)
    x_axis = "        " + "".join(
        str(e.epoch) if e.epoch < 10 else "X" for e in state.epochs
    )
    lines.append(x_axis)
    lines.append("        (epoch)")

    # 准确率迷你图
    if all(e.val_acc > 0 for e in state.epochs):
        accs = [e.val_acc for e in state.epochs]
        max_acc = max(accs)
        min_acc = min(accs)
        range_acc = max(max_acc - min_acc, 1e-6)
        lines.append("")
        lines.append(f"  Val Acc 趋势 (max={max_acc:.2%}, min={min_acc:.2%}):")
        bar = ""
        for a in accs:
            normalized = (a - min_acc) / range_acc if range_acc > 0 else 0.5
            bar_len = int(normalized * 8)
            bar += "█" * bar_len + "░" * (8 - bar_len) + " "
        lines.append("  " + bar)

    return "\n".join(lines)


def render_status(state: TrainState) -> str:
    """渲染当前训练状态(单次输出)"""
    lines = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  bge-reranker-v2-m3 LoRA 训练监控  "
                 f"@ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    # 阶段
    stage_zh = {
        "pending": "等待中",
        "loading_data": "加载数据",
        "loading_model": "加载模型",
        "applying_lora": "应用 LoRA",
        "training": "训练中",
        "saving": "保存模型",
        "done": "已完成",
        "failed": "失败",
    }
    lines.append(f"  阶段: {stage_zh.get(state.stage, state.stage)}")

    # 进度
    if state.total_epochs > 0:
        completed = len(state.epochs)
        pct = 100.0 * completed / state.total_epochs
        lines.append(f"  进度: {completed}/{state.total_epochs} epoch ({pct:.0f}%)")

        # 进度条
        bar_width = 40
        filled = int(bar_width * completed / state.total_epochs)
        bar = "█" * filled + "░" * (bar_width - filled)
        lines.append(f"  [{bar}]")

    # 时间
    if state.started_at is not None:
        if state.finished_at is not None:
            elapsed = state.finished_at - state.started_at
            lines.append(f"  总耗时: {format_duration(elapsed)}")
        else:
            elapsed = time.time() - state.started_at
            lines.append(f"  已耗时: {format_duration(elapsed)}")
            remaining = estimate_remaining(state)
            lines.append(f"  预估剩余: {format_duration(remaining)}")

    # 最新 epoch
    if state.epochs:
        last = state.epochs[-1]
        lines.append("")
        lines.append(f"  最近 epoch: {last.epoch}/{last.total_epochs}")
        lines.append(f"    train_loss: {last.train_loss:.4f}")
        lines.append(f"    val_loss:   {last.val_loss:.4f}")
        lines.append(f"    val_acc:    {last.val_acc:.2%}")

    # 最佳
    if state.best_epoch > 0:
        lines.append("")
        lines.append(f"  最佳 epoch: {state.best_epoch} (val_loss={state.best_val_loss:.4f})")

    # 最终结果
    if state.final_val_acc is not None:
        lines.append("")
        lines.append(f"  ✅ 最终验证准确率: {state.final_val_acc:.2%}")
    if state.train_time_sec is not None:
        lines.append(f"  ✅ 训练总耗时: {format_duration(state.train_time_sec)}")

    # 早停
    if state.early_stopped:
        lines.append("")
        lines.append("  ⚠ 触发早停")

    # 异常
    if state.anomalies:
        lines.append("")
        lines.append(f"  🚨 检测到 {len(state.anomalies)} 个异常:")
        for i, a in enumerate(state.anomalies[-5:], 1):  # 仅显示最近 5 条
            lines.append(f"    [{i}] {a}")

    # ASCII 曲线
    if state.epochs:
        lines.append("")
        lines.append(render_ascii_curve(state))

    lines.append("")
    lines.append("=" * 70)
    return "\n".join(lines)


def monitor_once(log_path: Path) -> TrainState:
    """读取整个日志文件并解析(单次快照)"""
    state = TrainState()
    if not log_path.exists():
        state.stage = "pending"
        state.anomalies.append(f"日志文件不存在: {log_path}")
        return state
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                parse_line(line, state)
    except PermissionError:
        state.anomalies.append(f"无权限读取日志文件: {log_path}")
    return state


def monitor_follow(log_path: Path, interval: float = 1.0,
                   clear_screen: bool = True) -> None:
    """持续监控日志(类似 tail -f)

    【变易】按 interval 秒数轮询,支持 Ctrl+C 退出
    """
    last_size = 0
    state = TrainState()
    print(f"持续监控: {log_path} (Ctrl+C 退出)")
    try:
        while True:
            try:
                if not log_path.exists():
                    time.sleep(interval)
                    continue
                current_size = log_path.stat().st_size
                # 处理日志被截断(如重启训练)
                if current_size < last_size:
                    last_size = 0
                    state = TrainState()
                # 读取新增内容
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    if last_size > 0:
                        f.seek(last_size)
                    new_content = f.read()
                    last_size = f.tell()
                for line in new_content.splitlines():
                    parse_line(line, state)
                # 渲染
                if clear_screen:
                    os.system("cls" if os.name == "nt" else "clear")
                print(render_status(state))
                print(f"\n  (每 {interval:.1f}s 刷新,Ctrl+C 退出)")
                # 训练完成则自动退出
                if state.stage == "done":
                    print("\n  ✅ 训练已完成,监控退出")
                    return
                if state.anomalies and any(
                    "OOM" in a or "0xC0000005" in a or "CUDA 错误" in a
                    for a in state.anomalies
                ):
                    print("\n  🚨 检测到致命异常,监控退出(请检查日志)")
                    return
            except KeyboardInterrupt:
                print("\n\n  监控已停止")
                return
            except Exception as e:
                # 不中断监控,仅打印错误
                print(f"\n  监控异常(继续): {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\n  监控已停止")


def main():
    parser = argparse.ArgumentParser(
        description="监控 finetune_reranker.py 训练日志"
    )
    parser.add_argument(
        "--log", required=True,
        help="训练日志文件路径(如 train.log)",
    )
    parser.add_argument(
        "--follow", "-f", action="store_true",
        help="持续监控模式(类似 tail -f),Ctrl+C 退出",
    )
    parser.add_argument(
        "--interval", type=float, default=1.0,
        help="follow 模式下的刷新间隔(秒,默认 1.0)",
    )
    parser.add_argument(
        "--no-clear", action="store_true",
        help="follow 模式下不清屏(滚动输出)",
    )
    args = parser.parse_args()

    log_path = Path(args.log).resolve()
    if not log_path.is_absolute():
        log_path = Path.cwd() / args.log

    if args.follow:
        monitor_follow(
            log_path,
            interval=args.interval,
            clear_screen=not args.no_clear,
        )
    else:
        # 单次快照
        state = monitor_once(log_path)
        print(render_status(state))
        # 退出码:有致命异常 → 1,否则 0
        fatal = any(
            "OOM" in a or "0xC0000005" in a or "CUDA 错误" in a
            for a in state.anomalies
        )
        sys.exit(1 if fatal else 0)


if __name__ == "__main__":
    main()
