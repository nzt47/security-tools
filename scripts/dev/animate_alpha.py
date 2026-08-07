"""交互式 alpha 排序可视化面板：拖动滑块实时看融合排序变化

数据驱动：读取 data/sim_results/hybrid_results.csv 中的 bm25_norm/embed_norm
（两者与 alpha 无关，融合前已完成 min-max 归一化），对任意 alpha 即时重建
    fused = alpha*bm25_norm + (1-alpha)*embed_norm
因此无需重新加载 Embedding 模型，动画流畅。

交互：
  - 拖动 Slider 调整 alpha（0~1），柱状图实时更新
  - 按键盘 1-7 切换用例
  - 点击播放按钮自动演示（alpha 0→1→0 往返循环）
用法：
  python scripts/dev/animate_alpha.py             # 交互窗口
  python scripts/dev/animate_alpha.py --selftest  # 无头自检, 存快照 PNG 后退出
  python scripts/dev/animate_alpha.py --gif demo.gif --case 7   # 导出用例7 的 alpha 0→1 演示 GIF
"""
import argparse
import csv
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

from sim_common import CSV_DIR, TOOLS

# 工具显示顺序（与语料一致）
TOOL_NAMES = [t["name"] for t in TOOLS]

# 动画往返周期（秒）
ANIM_PERIOD = 2.0
# 帧间隔（ms）
ANIM_INTERVAL = 40
# 自检帧数
SELFTEST_FRAMES = 30


def load_data():
    """{case_id: {"query": str, "scores": {tool: {"bm25_norm": f, "embed_norm": f}}}}"""
    path = os.path.join(CSV_DIR, "hybrid_results.csv")
    if not os.path.exists(path):
        raise SystemExit(
            f"缺少 {path}\n请先运行: python scripts/dev/simulate_hybrid_retrieval.py")
    data = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = int(row["case_id"])
            if row["alpha"] != "0.5":
                continue  # 归一化结果与 alpha 无关，任取一份即可
            case = data.setdefault(cid, {"query": row["query"], "scores": {}})
            case["scores"][row["tool"]] = {
                "bm25_norm": float(row["bm25_norm"]),
                "embed_norm": float(row["embed_norm"]),
            }
    return data


def fused_scores(scores, alpha):
    return {t: alpha * s["bm25_norm"] + (1 - alpha) * s["embed_norm"]
            for t, s in scores.items()}


class AlphaPanel:
    def __init__(self, data):
        import matplotlib.pyplot as plt
        from matplotlib.widgets import Button, Slider

        # 中文字体（Windows），避免方框乱码
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
        plt.rcParams["axes.unicode_minus"] = False

        self.plt = plt
        self.data = data
        self.case_ids = sorted(data.keys())
        self.current = self.case_ids[0]
        self.alpha = 0.5
        self.animating = False

        # 布局：主图 + Slider + 动画按钮
        self.fig = plt.figure(figsize=(11, 6.5))
        self.ax = self.fig.add_axes([0.08, 0.20, 0.86, 0.70])
        ax_slider = self.fig.add_axes([0.12, 0.09, 0.60, 0.04])
        ax_button = self.fig.add_axes([0.78, 0.09, 0.14, 0.04])

        x = range(len(TOOL_NAMES))
        self.bars = self.ax.bar(x, [0] * len(x), 0.6, color="#4C78A8")
        self.value_labels = [self.ax.text(0, 0, "", ha="center", va="top",
                                          fontsize=9) for _ in x]
        self.rank_labels = [self.ax.text(0, 0, "", ha="center", va="bottom",
                                         fontsize=11, fontweight="bold")
                            for _ in x]
        self.ax.set_xticks(list(x))
        self.ax.set_xticklabels(TOOL_NAMES, fontsize=10)
        self.ax.set_ylim(0, 1.15)
        self.ax.set_ylabel("融合分数  fused = alpha*bm25 + (1-alpha)*embed")
        self.ax.grid(axis="y", ls=":", alpha=0.4)

        self.slider = Slider(ax_slider, "alpha", 0.0, 1.0, valinit=self.alpha,
                             valstep=0.01)
        self.slider.on_changed(self._on_slider)

        self.button = Button(ax_button, "播放")
        self.button.on_clicked(self._toggle_anim)

        self.fig.text(0.5, 0.015,
                      "拖动滑块调 alpha ｜ 按 1-6 切换用例 ｜ 点击播放自动演示",
                      ha="center", fontsize=9, color="#666666")

        self.fig.canvas.mpl_connect("key_press_event", self._on_key)

        # 动画（初始暂停, 点击播放按钮启动）
        from matplotlib.animation import FuncAnimation
        self.anim = FuncAnimation(self.fig, self._animate_frame,
                                  interval=ANIM_INTERVAL, blit=False,
                                  cache_frame_data=False)
        self.anim.event_source.stop()

        self._update_plot()

    # ── 数据/重绘 ──
    def _update_plot(self, _=None):
        case = self.data[self.current]
        scores = fused_scores(case["scores"], self.alpha)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        rank_map = {t: r for r, (t, s) in enumerate(ranked, 1) if s > 0}

        for i, name in enumerate(TOOL_NAMES):
            val = max(scores.get(name, 0.0), 0.0)
            self.bars[i].set_height(val)
            rank = rank_map.get(name)
            self.bars[i].set_color("#D62728" if rank == 1 else "#4C78A8")
            self.value_labels[i].set_position((i, val - 0.06))
            self.value_labels[i].set_text(f"{val:.3f}" if val > 0.005 else "")
            self.rank_labels[i].set_position((i, val + 0.03))
            self.rank_labels[i].set_text(f"#{rank}" if rank else "-")
            self.rank_labels[i].set_color("#D62728" if rank == 1 else "#555555")

        top = ranked[0] if ranked and ranked[0][1] > 0 else None
        top_txt = (f"top1: {top[0]} ({top[1]:.3f})" if top else "无命中")
        self.ax.set_title(
            f"用例{self.current}: {case['query']}\n"
            f"alpha = {self.alpha:.2f}  （BM25 权重 {self.alpha:.0%} + "
            f"Embedding 权重 {1 - self.alpha:.0%}）  {top_txt}",
            fontsize=11)
        self.fig.canvas.draw_idle()

    # ── 交互回调 ──
    def _on_slider(self, val):
        self.alpha = float(val)
        self._update_plot()

    def _toggle_anim(self, event):
        if self.animating:
            self.anim.event_source.stop()
            self.button.label.set_text("播放")
            self.animating = False
        else:
            self.anim.event_source.start()
            self.button.label.set_text("暂停")
            self.animating = True
        self.fig.canvas.draw_idle()

    def _on_key(self, event):
        if event.key.isdigit() and int(event.key) in self.data:
            self.current = int(event.key)
            self._update_plot()

    def _animate_frame(self, frame):
        """alpha 0→1→0 往返（三角波），改滑块触发重绘"""
        t = (frame * ANIM_INTERVAL / 1000.0) % ANIM_PERIOD
        self.slider.set_val(1 - abs(t - 1.0))
        return ()


def main():
    parser = argparse.ArgumentParser(description="alpha 排序动画面板")
    parser.add_argument("--selftest", action="store_true",
                        help="无头自检：跑 30 帧后保存快照 PNG 退出")
    parser.add_argument("--gif", metavar="PATH",
                        help="导出 alpha 0→1 演示 GIF（无头, 无需弹窗）")
    parser.add_argument("--case", type=int, default=None,
                        help="GIF 演示的用例编号（默认用例1）")
    parser.add_argument("--frames", type=int, default=40,
                        help="GIF 帧数（默认 40, alpha 从 0 线性到 1）")
    parser.add_argument("--fps", type=int, default=15,
                        help="GIF 帧率（默认 15, 40 帧约 2.7 秒）")
    args = parser.parse_args()

    if args.gif or args.selftest:
        import matplotlib
        matplotlib.use("Agg")  # 无头后端

    data = load_data()

    # ── GIF 模式：alpha 0→1 线性演示, PillowWriter 逐帧抓取 ──
    if args.gif:
        from matplotlib.animation import PillowWriter
        panel = AlphaPanel(data)
        if args.case is not None:
            if args.case not in data:
                raise SystemExit(f"无效用例编号 {args.case}，可选 {sorted(data)}")
            panel.current = args.case
            panel._update_plot()
        writer = PillowWriter(fps=args.fps)
        with writer.saving(panel.fig, args.gif, 120):
            for f in range(args.frames):
                alpha = f / (args.frames - 1)   # 0→1 线性
                panel.slider.set_val(alpha)
                writer.grab_frame()
        print(f"[GIF] 已导出 → {os.path.normpath(args.gif)}"
              f"（{args.frames}帧, {args.fps}fps, "
              f"用例{panel.current}: {data[panel.current]['query']}）")
        return

    panel = AlphaPanel(data)

    if args.selftest:
        snapshot = os.path.join(CSV_DIR, "alpha_panel_snapshot.png")
        for f in range(SELFTEST_FRAMES):
            panel._animate_frame(f)
        panel.fig.savefig(snapshot, dpi=120)
        print(f"[PNG] 自检快照 → {os.path.normpath(snapshot)}")
    else:
        panel.plt.show()


if __name__ == "__main__":
    main()
