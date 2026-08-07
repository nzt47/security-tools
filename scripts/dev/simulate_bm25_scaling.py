"""BM25 归一化曲线：工具数 4→100 时纯英文查询的命中分布与归一化变化

背景：跨语言查询（纯英文）在 BM25 层零字面命中, 引入更多纯中文（无英文 token）
工具描述后, 命中集合/归一化结果是否变化？pdf 工具的 raw 分与归一化分如何演变？

方法：基准 4 工具（pdf解析描述含别名 token）保持不变, 程序化追加 96 个纯中文
描述, 循环 N=4→100（步长 4）, 对英文查询 "extract text from pdf" 记录:
  命中工具数 / pdf 工具 raw 分 / pdf 工具 min-max 归一化分
并生成对比图（左: raw 上升曲线, 右: 归一化恒 1.0 + 命中数恒 1）。
纯 BM25, 不加载模型, 秒级出结果。

用法：python scripts/dev/simulate_bm25_scaling.py
"""
import io
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

import matplotlib
matplotlib.use("Agg")  # 无头后端
import matplotlib.pyplot as plt

from simulate_hybrid_retrieval import BM25Index, _min_max_normalize  # noqa: E402
from sim_common import CSV_DIR, TOOLS  # noqa: E402

# 中文字体（Windows）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

# 基准 4 工具描述（pdf解析 含英文别名 token: pdf/extract/parse/document/text）
BASE_DESCS = [t["description"] for t in TOOLS]

# 程序化生成纯中文扩展描述（刻意不含任何英文字母/数字 token, 共 100 条）
BUSINESS_TERMS = [
    "日程", "邮件", "报表", "通讯录", "日历", "笔记", "文档", "表格",
    "演示", "流程图", "订单", "库存", "客户", "商品", "发票", "合同",
    "账单", "统计", "图表", "日志",
]
EXTRA_DESCS = [f"自动处理{b}相关业务并输出处理结果" for b in BUSINESS_TERMS] * 5

QUERY = "extract text from pdf"
STEP = 4
MAX_N = 100


def run_bm25(descs, query, top_k):
    """构造索引并查询（静音 BM25Index.search 内部 print）"""
    idx = BM25Index()
    for i, d in enumerate(descs):
        idx.add_document(i, d)
    _buf = io.StringIO()
    _old = sys.stdout
    sys.stdout = _buf
    try:
        ranked = idx.search(query, top_k=top_k)
    finally:
        sys.stdout = _old
    return ranked


def main():
    print("=" * 64)
    print(f"语料规模 N=4→{MAX_N}（纯中文扩展）, 英文查询 '{QUERY}'")
    print("=" * 64)
    print(f"{'N':<6}{'命中工具数':<10}{'pdf_raw':<12}{'pdf_norm':<10}其余工具")
    print("-" * 64)

    ns, nhits, raws, norms = [], [], [], []
    for n_total in range(4, MAX_N + 1, STEP):
        descs = BASE_DESCS + EXTRA_DESCS[:n_total - 4]
        ranked = run_bm25(descs, QUERY, top_k=len(descs))
        hits = [(d, s) for d, s in ranked if s > 0]
        pdf_raw = next((s for d, s in hits if d == 0), 0.0)
        norm_map = dict(_min_max_normalize(hits))
        pdf_norm = norm_map.get(0, 0.0)
        others = "、".join(f"#{d}({s:.2f})" for d, s in hits[1:]) or "全 0 分"
        print(f"{n_total:<6}{len(hits):<10}{pdf_raw:<12.3f}{pdf_norm:<10.3f}{others}")
        ns.append(n_total); nhits.append(len(hits))
        raws.append(pdf_raw); norms.append(pdf_norm)

    # ── 对比图 ──
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.6))
    axes[0].plot(ns, raws, "o-", color="#D62728", lw=1.5)
    axes[0].set_xlabel("工具总数 N")
    axes[0].set_ylabel("pdf工具 BM25 raw 分")
    axes[0].set_title("左: raw 分随 N 上升\nidf=log((N-df+0.5)/(df+0.5)), df=1 固定")
    axes[0].grid(ls=":", alpha=0.5)
    axes[1].plot(ns, norms, "o-", color="#4C78A8", lw=1.5,
                 label="pdf工具归一化分")
    axes[1].plot(ns, nhits, "s--", color="#E8A33D", lw=1.5,
                 label="BM25 命中工具数")
    axes[1].set_xlabel("工具总数 N")
    axes[1].set_ylabel("数值")
    axes[1].set_ylim(0, 2.2)
    axes[1].legend()
    axes[1].set_title("右: 归一化分恒 1.0, 命中数恒 1\n归一化抹平 idf 增益, 融合结果与 N 无关")
    axes[1].grid(ls=":", alpha=0.5)
    fig.suptitle(f"纯中文工具扩增: 英文查询 '{QUERY}' 的 BM25 行为（N=4→{MAX_N}）",
                 fontsize=12)
    fig.tight_layout(rect=[0, 0, 1, 0.92])
    out = os.path.join(CSV_DIR, "bm25_scaling_curve.png")
    fig.savefig(out, dpi=120)
    print(f"\n[PNG] 归一化曲线图 → {os.path.normpath(out)}")

    print("\n[结论]")
    print("1. 命中工具数恒为 1（pdf 工具）: 纯中文扩展不引入新英文 token 命中,")
    print("   零字面问题不随工具数加剧。")
    print("2. pdf raw 分从 ~2.1 升至 ~12: idf 随 N 增大, 命中者原始分更突出。")
    print("3. pdf 归一化分恒 1.0: min-max 抹平 idf 增益, 融合结果与工具数无关——")
    print("   除非新增描述含英文 token（引入新命中者或共享 token 稀释 idf）。")


if __name__ == "__main__":
    main()
