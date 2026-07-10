"""生成 P2-2 性能对比报告图表 — 500 节点极端场景测试结果"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import os

# 解决中文字体
plt.rcParams['font.sans-serif'] = ['Microsoft YaHei', 'SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
fig.suptitle('P2-2 VisualEditor 500 节点极端场景性能与内存测试', fontsize=14, fontweight='bold')

# ─── 图1：UI 交互流畅度柱状图 ───────────────────────────
ops = ['updateNodeData', 'selectNode', 'undo', 'redo', 'addNode', 'removeNode']
times = [1.96, 0.87, 0.27, 0.36, 0.71, 0.38]
colors = ['#4CAF50', '#2196F3', '#FF9800', '#FF9800', '#9C27B0', '#F44336']

bars = ax1.bar(ops, times, color=colors, edgecolor='white', linewidth=0.5)
ax1.axhline(y=16, color='#4CAF50', linestyle='--', linewidth=1.5, alpha=0.7, label='流畅线 16ms (60fps)')
ax1.axhline(y=50, color='#F44336', linestyle='--', linewidth=1.5, alpha=0.7, label='卡顿线 50ms')

# 在柱顶标注数值
for bar, t in zip(bars, times):
    ax1.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.5,
             f'{t}ms', ha='center', va='bottom', fontsize=9, fontweight='bold')

ax1.set_ylabel('端到端耗时 (ms)', fontsize=11)
ax1.set_title('UI 交互流畅度（store 操作 → React rerender）', fontsize=12)
ax1.set_ylim(0, 55)
ax1.legend(loc='upper left', fontsize=9)
ax1.tick_params(axis='x', rotation=15)
ax1.grid(axis='y', alpha=0.3)

# ─── 图2：内存占用折线图 ────────────────────────────────
stages = ['基线\n(空store)', '注入\n500节点', '50步历史\n满载', '250次\n循环后', 'redoStack\n清空后']
mems = [33.81, 34.05, 34.28, 34.39, 34.28]
deltas = [0, 0.21, 0.23, 0.02, -0.12]

ax2.plot(stages, mems, marker='o', markersize=8, linewidth=2, color='#2196F3',
         markerfacecolor='#FF9800', markeredgecolor='white', markeredgewidth=1.5)

# 填充区域
ax2.fill_between(range(len(stages)), mems, min(mems) - 0.1, alpha=0.15, color='#2196F3')

# 标注数值和增量
for i, (m, d) in enumerate(zip(mems, deltas)):
    ax2.annotate(f'{m}MB', (i, m), textcoords="offset points",
                 xytext=(0, 10), ha='center', fontsize=9, fontweight='bold')
    if i > 0:
        sign = '+' if d >= 0 else ''
        color = '#F44336' if d > 0.1 else '#4CAF50' if d < 0 else '#666666'
        ax2.annotate(f'{sign}{d}MB', (i, m), textcoords="offset points",
                     xytext=(0, -15), ha='center', fontsize=8, color=color)

ax2.set_ylabel('堆内存 (MB)', fontsize=11)
ax2.set_title('撤销/重做栈内存占用（500 节点 × 50 步历史）', fontsize=12)
ax2.set_ylim(33.5, 34.6)
ax2.grid(axis='y', alpha=0.3)

plt.tight_layout()
output = os.path.join(os.path.dirname(__file__), 'p2_perf_chart.png')
plt.savefig(output, dpi=150, bbox_inches='tight', facecolor='white')
print(f'图表已保存: {output}')
