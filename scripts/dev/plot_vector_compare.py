"""绘制用例②③ 中 TF-IDF 与 Embedding 的向量分布对比图

数据来源：
  - data/sim_results/tfidf_results.csv  （tfidf_sim）
  - data/sim_results/hybrid_results.csv  （embed_cosine, 取 alpha=0.5 行, 含全量余弦）
输出：
  - data/sim_results/vector_compare.png
【简易】独立绘图脚本，先跑两个模拟脚本生成 CSV，再运行本脚本。
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# 中文字体（Windows）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "data", "sim_results")

# wf-id → 显示名（与 hybrid 脚本 TOOLS 的 name 对齐，便于两 CSV 对表）
WF_NAME = {
    "wf-pdf-parse": "pdf解析",
    "wf-translate-en": "翻译英文",
    "wf-img-ocr": "图片文字识别",
    "wf-sql-query": "数据库查询",
}
TOOLS = ["pdf解析", "翻译英文", "图片文字识别", "数据库查询"]

# 关注用例（② 同义改写, ③ 口语化）
CASES = [2, 3]


def load():
    # tfidf: {case_id: {tool_name: sim}}
    tfidf = {}
    with open(os.path.join(BASE, "tfidf_results.csv"),
              newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = int(row["case_id"])
            tool = WF_NAME.get(row["tool"], row["tool"])
            tfidf.setdefault(cid, {})[tool] = float(row["tfidf_sim"])

    # hybrid embed_cosine: {case_id: {tool_name: cosine}}
    hybrid = {}
    with open(os.path.join(BASE, "hybrid_results.csv"),
              newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            cid = int(row["case_id"])
            if row["alpha"] != "0.5":
                continue
            tool = row["tool"]
            hybrid.setdefault(cid, {})[tool] = float(row["embed_cosine"])
    return tfidf, hybrid


def main():
    tfidf, hybrid = load()
    if not tfidf or not hybrid:
        raise SystemExit("缺少 CSV 数据，请先运行两个模拟脚本")

    # 查询文本（从任一 CSV 取）
    queries = {}
    with open(os.path.join(BASE, "tfidf_results.csv"),
              newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            queries.setdefault(int(row["case_id"]), row["query"])

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    x = range(len(TOOLS))
    for ax, cid in zip(axes, CASES):
        tf_scores = [tfidf.get(cid, {}).get(t, 0.0) for t in TOOLS]
        em_scores = [hybrid.get(cid, {}).get(t, 0.0) for t in TOOLS]

        b1 = ax.bar([i - 0.2 for i in x], tf_scores, 0.4,
                    label="TF-IDF 余弦", color="#4C78A8")
        b2 = ax.bar([i + 0.2 for i in x], em_scores, 0.4,
                    label="Embedding 余弦", color="#F58518")

        ax.axhline(0.3, color="#D62728", ls="--", lw=1.2,
                   label="TF-IDF 过滤阈值 0.3")
        ax.axhline(0.2, color="#2CA02C", ls=":", lw=1.2,
                   label="Embedding 剪枝阈值 0.2")
        ax.set_xticks(list(x))
        ax.set_xticklabels(TOOLS, rotation=12, fontsize=9)
        ax.set_ylabel("相似度")
        ax.set_ylim(-0.05, 1.0)
        ax.set_title(f"用例{cid}: {queries.get(cid, '')}", fontsize=11)
        ax.legend(fontsize=8, loc="upper right")

        # 柱顶标注数值
        for rect, v in zip(b1, tf_scores):
            ax.text(rect.get_x() + rect.get_width() / 2, v + 0.02,
                    f"{v:.2f}", ha="center", fontsize=8, color="#4C78A8")
        for rect, v in zip(b2, em_scores):
            ax.text(rect.get_x() + rect.get_width() / 2, v + 0.02,
                    f"{v:.2f}", ha="center", fontsize=8, color="#F58518")

    fig.suptitle("中文近义表达下 TF-IDF vs Embedding 向量相似度分布",
                 fontsize=13, y=1.02)
    fig.tight_layout()
    out_png = os.path.join(BASE, "vector_compare.png")
    fig.savefig(out_png, dpi=150, bbox_inches="tight")
    print(f"[PNG] 已导出 → {os.path.normpath(out_png)}")


if __name__ == "__main__":
    main()
