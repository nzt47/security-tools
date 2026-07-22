#!/usr/bin/env python
"""TLM 架构图导出为 PNG（熔断器状态机 + 三表数据流向）

使用 matplotlib 绘制，无外部依赖（仅需 matplotlib + PIL）。

输出：
- docs/images/circuit_breaker_state_machine.png
- docs/images/tlm_three_table_data_flow.png

运行方式：
    python scripts/export_arch_diagrams.py
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")  # 无头模式，不需要 GUI
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# 确保项目根在 sys.path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 输出目录
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "docs", "images")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 中文字体支持
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


# ──────────────────────────────────────────────────────────────
# 图 1: 熔断器状态机
# ──────────────────────────────────────────────────────────────

def draw_circuit_breaker_state_machine():
    """绘制熔断器三态状态机"""
    fig, ax = plt.subplots(figsize=(14, 8))
    ax.set_xlim(0, 14)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_title("TLM 熔断器状态机（Circuit Breaker State Machine）",
                 fontsize=16, fontweight="bold", pad=20)

    # 三态方框
    states = {
        "normal": {
            "pos": (1.5, 5), "size": (3.5, 2),
            "label": "正常状态\n(CLOSED)",
            "sub": "_vec_available=True\n_vec_fail_count=0",
            "color": "#c8e6c9", "edge": "#2e7d32",
        },
        "open": {
            "pos": (8.5, 5), "size": (3.5, 2),
            "label": "熔断状态\n(OPEN)",
            "sub": "_vec_available=False\nfail_count>=5",
            "color": "#ffcdd2", "edge": "#c62828",
        },
        "recovery": {
            "pos": (5, 1), "size": (3.5, 2),
            "label": "恢复状态\n(HALF-OPEN)",
            "sub": "_reset_vec_circuit()\nfail_count=0",
            "color": "#fff9c4", "edge": "#f9a825",
        },
    }

    for key, s in states.items():
        x, y = s["pos"]
        w, h = s["size"]
        box = FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0.1",
            facecolor=s["color"], edgecolor=s["edge"], linewidth=2.5,
        )
        ax.add_patch(box)
        ax.text(x + w / 2, y + h * 0.72, s["label"],
                ha="center", va="center", fontsize=13, fontweight="bold")
        ax.text(x + w / 2, y + h * 0.28, s["sub"],
                ha="center", va="center", fontsize=9, color="#333")

    # 状态转换箭头
    arrows = [
        # 正常 → 熔断
        {
            "start": (5, 6), "end": (8.5, 6),
            "label": "连续失败 >= 5 次\n_record_vec_failure()",
            "color": "#c62828", "style": "->", "lw": 2.2,
            "label_offset": (0, 0.5),
        },
        # 熔断 → 恢复
        {
            "start": (10.25, 5), "end": (7.5, 3),
            "label": "后台探活成功\n_reset_vec_circuit()",
            "color": "#1565c0", "style": "->", "lw": 2.2,
            "label_offset": (0.5, 0),
        },
        # 恢复 → 正常
        {
            "start": (5, 3), "end": (3.25, 5),
            "label": "状态完全重置\n可再次熔断",
            "color": "#2e7d32", "style": "->", "lw": 2.2,
            "label_offset": (-0.8, 0.2),
        },
        # 正常自循环（失败未达阈值）
        {
            "start": (2, 5), "end": (2, 5),
            "label": "失败 < 5 次\n计数+1，继续",
            "color": "#f9a825", "style": "->", "lw": 1.8,
            "label_offset": (-1.5, -0.3),
            "self_loop": True,
        },
    ]

    for a in arrows:
        if a.get("self_loop"):
            # 自循环用弧形箭头
            arrow = FancyArrowPatch(
                a["start"], a["end"],
                connectionstyle="arc3,rad=2.5",
                arrowstyle=a["style"], mutation_scale=20,
                color=a["color"], linewidth=a["lw"],
            )
            ax.add_patch(arrow)
            lx, ly = a["start"][0] + a["label_offset"][0], a["start"][1] + a["label_offset"][1]
            ax.text(lx, ly, a["label"], ha="center", va="center",
                    fontsize=9, color=a["color"], fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor=a["color"], alpha=0.9))
        else:
            arrow = FancyArrowPatch(
                a["start"], a["end"],
                arrowstyle=a["style"], mutation_scale=22,
                color=a["color"], linewidth=a["lw"],
            )
            ax.add_patch(arrow)
            mx = (a["start"][0] + a["end"][0]) / 2 + a["label_offset"][0]
            my = (a["start"][1] + a["end"][1]) / 2 + a["label_offset"][1]
            ax.text(mx, my, a["label"], ha="center", va="center",
                    fontsize=9, color=a["color"], fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.3", facecolor="white",
                              edgecolor=a["color"], alpha=0.9))

    # 熔断状态行为说明
    ax.text(10.25, 2.5,
            "熔断时行为:\n"
            "• search_vector → 返回 []\n"
            "• save_with_embedding 跳过向量层\n"
            "• 不再调用 _get_conn (短路)\n"
            "• 主表 + FTS 仍正常工作",
            ha="center", va="center", fontsize=9,
            bbox=dict(boxstyle="round,pad=0.5", facecolor="#ffebee",
                      edgecolor="#c62828", alpha=0.9))

    # 图例
    legend_handles = [
        mpatches.Patch(color="#c8e6c9", label="正常 (CLOSED)"),
        mpatches.Patch(color="#ffcdd2", label="熔断 (OPEN)"),
        mpatches.Patch(color="#fff9c4", label="恢复 (HALF-OPEN)"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", fontsize=10)

    plt.tight_layout()
    output_path = os.path.join(OUTPUT_DIR, "circuit_breaker_state_machine.png")
    plt.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print(f"[OK] 熔断器状态机已导出: {output_path}")
    return output_path


# ──────────────────────────────────────────────────────────────
# 图 2: 三表数据流向
# ──────────────────────────────────────────────────────────────

def draw_three_table_data_flow():
    """绘制三表数据流向（正常 + 降级模式）"""
    fig, ax = plt.subplots(figsize=(16, 10))
    ax.set_xlim(0, 16)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("TLM 三表数据流向（Three-Table Data Flow）",
                 fontsize=16, fontweight="bold", pad=20)

    # ── 客户端调用 ──
    client_box = FancyBboxPatch((0.5, 8.5), 3, 1, boxstyle="round,pad=0.1",
                                 facecolor="#e3f2fd", edgecolor="#1565c0", linewidth=2)
    ax.add_patch(client_box)
    ax.text(2, 9, "客户端调用\nsave_with_embedding / search_vector",
            ha="center", va="center", fontsize=10, fontweight="bold")

    # ── 模式判断 ──
    decision_box = FancyBboxPatch((5, 8.3), 2.5, 1.4, boxstyle="round,pad=0.1",
                                   facecolor="#f3e5f5", edgecolor="#6a1b9a", linewidth=2)
    ax.add_patch(decision_box)
    ax.text(6.25, 9, "向量层可用?", ha="center", va="center",
            fontsize=11, fontweight="bold")
    ax.text(6.25, 8.6, "(_vec_available)", ha="center", va="center",
            fontsize=9, color="#555")

    # 客户端 → 判断
    ax.add_patch(FancyArrowPatch((3.5, 9), (5, 9), arrowstyle="->",
                                  mutation_scale=20, color="#333", linewidth=2))

    # ── 正常模式（右侧）──
    normal_box = FancyBboxPatch((9, 6.5), 6.5, 3, boxstyle="round,pad=0.15",
                                 facecolor="#e8f5e9", edgecolor="#2e7d32", linewidth=2.5)
    ax.add_patch(normal_box)
    ax.text(12.25, 9.2, "正常模式 (vec_available=True)",
            ha="center", va="center", fontsize=12, fontweight="bold", color="#2e7d32")

    # 三表
    tables_normal = [
        {"pos": (9.3, 7.8), "label": "memory_items\n(主表)\nINSERT/UPSERT",
         "color": "#c8e6c9", "edge": "#2e7d32"},
        {"pos": (11.5, 7.8), "label": "memory_fts\n(FTS5)\nDELETE+INSERT",
         "color": "#fff3e0", "edge": "#ef6c00"},
        {"pos": (13.7, 7.8), "label": "memories_vec\n(vec0)\n异步写入",
         "color": "#e3f2fd", "edge": "#1565c0"},
    ]
    for t in tables_normal:
        x, y = t["pos"]
        box = FancyBboxPatch((x, y), 1.8, 1.3, boxstyle="round,pad=0.08",
                              facecolor=t["color"], edgecolor=t["edge"], linewidth=2)
        ax.add_patch(box)
        ax.text(x + 0.9, y + 0.65, t["label"], ha="center", va="center",
                fontsize=9, fontweight="bold")

    # 同事务标注
    ax.text(11.25, 7.3, "↑ 同事务 (self._lock)", ha="center", va="center",
            fontsize=9, color="#2e7d32", fontstyle="italic")
    ax.text(14.6, 7.3, "↑ 异步线程", ha="center", va="center",
            fontsize=9, color="#1565c0", fontstyle="italic")

    # KNN 检索
    ax.text(12.25, 6.8, "search_vector: KNN 查询 → confidence = max(0, 1 - distance/2)",
            ha="center", va="center", fontsize=9, color="#1565c0")

    # ── 降级模式（左侧）──
    degraded_box = FancyBboxPatch((0.5, 3.5), 6.5, 3, boxstyle="round,pad=0.15",
                                   facecolor="#fafafa", edgecolor="#9e9e9e",
                                   linewidth=2.5, linestyle="--")
    ax.add_patch(degraded_box)
    ax.text(3.75, 6.2, "降级模式 (vec_available=False)",
            ha="center", va="center", fontsize=12, fontweight="bold", color="#9e9e9e")

    tables_degraded = [
        {"pos": (0.8, 4.5), "label": "memory_items\n(主表)\nINSERT/UPSERT",
         "color": "#c8e6c9", "edge": "#2e7d32"},
        {"pos": (3, 4.5), "label": "memory_fts\n(FTS5)\nDELETE+INSERT",
         "color": "#fff3e0", "edge": "#ef6c00"},
        {"pos": (5.2, 4.5), "label": "向量层\n跳过\n(仅日志)",
         "color": "#f5f5f5", "edge": "#9e9e9e"},
    ]
    for t in tables_degraded:
        x, y = t["pos"]
        box = FancyBboxPatch((x, y), 1.8, 1.3, boxstyle="round,pad=0.08",
                              facecolor=t["color"], edgecolor=t["edge"],
                              linewidth=2, linestyle="--")
        ax.add_patch(box)
        ax.text(x + 0.9, y + 0.65, t["label"], ha="center", va="center",
                fontsize=9, fontweight="bold")

    ax.text(2.25, 4, "↑ 同事务 (self._lock)", ha="center", va="center",
            fontsize=9, color="#2e7d32", fontstyle="italic")
    ax.text(3.75, 3.7, "search_vector → 返回 [] 不抛异常 | search → FTS5+BM25 兜底",
            ha="center", va="center", fontsize=9, color="#9e9e9e")

    # ── 兜底补偿 ──
    fallback_box = FancyBboxPatch((9, 3.5), 6.5, 2.5, boxstyle="round,pad=0.15",
                                   facecolor="#ffebee", edgecolor="#c62828", linewidth=2)
    ax.add_patch(fallback_box)
    ax.text(12.25, 5.6, "兜底补偿 (Fallback)",
            ha="center", va="center", fontsize=12, fontweight="bold", color="#c62828")

    fb_tables = [
        {"pos": (9.3, 4.2), "label": "memories_vec_failed\n(兜底表)\n重试耗尽写入"},
        {"pos": (12, 4.2), "label": "_retry_vec_write\nRetryPolicy\nexp max=3"},
        {"pos": (14.2, 4.2), "label": "replay_vec_failed()\n后台补偿重放"},
    ]
    for t in fb_tables:
        x, y = t["pos"]
        box = FancyBboxPatch((x, y), 1.8, 1.2, boxstyle="round,pad=0.08",
                              facecolor="#ffcdd2", edgecolor="#c62828", linewidth=1.8)
        ax.add_patch(box)
        ax.text(x + 0.9, y + 0.6, t["label"], ha="center", va="center",
                fontsize=8, fontweight="bold")

    # 兜底表 → 重放回向量表
    ax.add_patch(FancyArrowPatch((15.1, 4.8), (14.6, 7.8), arrowstyle="->",
                                  mutation_scale=18, color="#c62828", linewidth=1.8,
                                  connectionstyle="arc3,rad=-0.3"))
    ax.text(15.3, 6.3, "重放", ha="center", va="center", fontsize=8,
            color="#c62828", fontstyle="italic")

    # ── sqlite-vec 加载策略 ──
    loader_box = FancyBboxPatch((0.5, 0.5), 15, 2.5, boxstyle="round,pad=0.15",
                                 facecolor="#fff8e1", edgecolor="#ff8f00", linewidth=2)
    ax.add_patch(loader_box)
    ax.text(8, 2.6, "sqlite-vec 加载策略 (_init_vec_table) — 三级降级防线",
            ha="center", va="center", fontsize=12, fontweight="bold", color="#ff8f00")

    load_steps = [
        (1.5, 1.3, "1. sqlite_vec.load(conn)\nPython 适配器 (首选)"),
        (5.5, 1.3, "2. conn.load_extension('sqlite_vec')\n原生扩展 (fallback)"),
        (9.5, 1.3, "3. except 兜底\n_vec_available=False"),
        (13.5, 1.3, "thread-local 缓存\nvec_loaded 标志"),
    ]
    for x, y, label in load_steps:
        box = FancyBboxPatch((x, y), 3, 1, boxstyle="round,pad=0.08",
                              facecolor="#fff3e0", edgecolor="#ff8f00", linewidth=1.5)
        ax.add_patch(box)
        ax.text(x + 1.5, y + 0.5, label, ha="center", va="center",
                fontsize=8, fontweight="bold")

    # 加载策略箭头
    for i in range(3):
        x_start = 1.5 + (i * 4) + 3
        x_end = 1.5 + ((i + 1) * 4)
        ax.add_patch(FancyArrowPatch((x_start, 1.8), (x_end, 1.8), arrowstyle="->",
                                      mutation_scale=15, color="#ff8f00", linewidth=1.5))

    # 模式判断 → 正常/降级
    ax.add_patch(FancyArrowPatch((7.5, 9), (9, 9), arrowstyle="->",
                                  mutation_scale=22, color="#2e7d32", linewidth=2.5))
    ax.text(8.25, 9.3, "True", ha="center", va="center", fontsize=10,
            color="#2e7d32", fontweight="bold")

    ax.add_patch(FancyArrowPatch((6.25, 8.3), (4, 6.5), arrowstyle="->",
                                  mutation_scale=22, color="#9e9e9e", linewidth=2.5,
                                  connectionstyle="arc3,rad=0.2"))
    ax.text(5, 7.5, "False", ha="center", va="center", fontsize=10,
            color="#9e9e9e", fontweight="bold")

    # 正常模式 → 兜底补偿（重试耗尽时）
    ax.add_patch(FancyArrowPatch((12.25, 6.5), (12.25, 6), arrowstyle="->",
                                  mutation_scale=18, color="#c62828", linewidth=1.8,
                                  linestyle="--"))
    ax.text(13, 6.25, "重试耗尽", ha="center", va="center", fontsize=8,
            color="#c62828", fontstyle="italic")

    plt.tight_layout()
    output_path = os.path.join(OUTPUT_DIR, "tlm_three_table_data_flow.png")
    plt.savefig(output_path, dpi=150, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close()
    print(f"[OK] 三表数据流向已导出: {output_path}")
    return output_path


# ──────────────────────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  TLM 架构图导出为 PNG")
    print("=" * 60)

    p1 = draw_circuit_breaker_state_machine()
    p2 = draw_three_table_data_flow()

    print(f"\n[DONE] 两张架构图已导出:")
    print(f"  1. {p1}")
    print(f"  2. {p2}")
    print(f"\n可直接插入到技术分享 PPT 中。")


if __name__ == "__main__":
    main()
