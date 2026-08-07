"""混合语言（中英混排）工具描述对纯英文查询 BM25 分数分布的影响

问题：如果工具描述改为"中文 + 英文业务 token"混排，纯英文查询的 BM25 分布
会有什么具体变化？分正面/负面两方面量化。

方法：基准 4 工具（带英文别名）不变，追加 20 条扩展描述；按混合比例
p = 0/25/50/75/100% 将扩展描述替换为中英混排版本（其余为纯中文）。
对两条英文查询记录：命中工具数 / 目标工具 raw 与归一化分 / 假命中工具 / top1。

- 查询 A "extract text from pdf": 目标 = pdf解析, 观察假命中抢分
- 查询 B "send email to user":   目标 = 混合描述中的 email 工具, 观察英文检索覆盖面

纯 BM25, 不加载模型。用法：python scripts/dev/simulate_mixed_language.py
"""
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

from simulate_hybrid_retrieval import BM25Index, _min_max_normalize  # noqa: E402
from sim_common import TOOLS  # noqa: E402

BASE_DESCS = [t["description"] for t in TOOLS]

# 中英混排扩展描述（20 条, 含英文业务 token; 部分与查询共享 token 模拟假命中）
MIXED_DESCS = [
    "导出报表数据到 excel 表格",
    "发送 email 通知用户结果",
    "下载 remote 服务器文件",
    "备份 database 到本地磁盘",
    "生成 report 并发送邮件",
    "监控 system 运行状态",
    "调用 api 获取数据",
    "打开 web 页面链接",
    "搜索 keyword 相关信息",
    "下载 video 视频文件",
    "压缩 pdf 文档",
    "解析 excel 报表",
    "翻译 english 文档",
    "识别 image 内容",
    "查询 sql 数据表",
    "打印 document 文件",
    "上传 file 到服务器",
    "发送 text 消息通知",
    "提取 keyword 关键词",
    "转换 format 格式",
]
# 纯中文版本（无英文 token）
PLAIN_DESCS = [
    "导出报表数据到表格",
    "发送通知给用户",
    "下载远程服务器文件",
    "备份数据到本地磁盘",
    "生成报告并发送邮件",
    "监控系统运行状态",
    "调用接口获取数据",
    "打开网页链接",
    "搜索关键词相关信息",
    "下载视频文件",
    "压缩文档",
    "解析报表",
    "翻译文档",
    "识别图像内容",
    "查询数据表",
    "打印文件",
    "上传文件到服务器",
    "发送文本消息通知",
    "提取关键词",
    "转换格式",
]

QUERIES = ["extract text from pdf", "send email to user"]
PROPS = (0, 25, 50, 75, 100)


def run_bm25(descs, query, top_k):
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


def describe(doc_id):
    """doc_id → 简述（0-3 基准工具, 4+ 扩展）"""
    if doc_id < 4:
        return TOOLS[doc_id]["name"]
    return f"扩展#{doc_id}({MIXED_DESCS[doc_id - 4][:8]})"


def main():
    print("=" * 78)
    print("混合语言描述 vs 纯英文查询: BM25 分数分布变化")
    print("=" * 78)

    for q in QUERIES:
        print(f"\n# 查询: '{q}'")
        print(f"{'混合比例':<8}{'命中数':<6}{'top1':<22}{'top1分':<8}{'假命中/其他命中'}")
        print("-" * 78)
        for p in PROPS:
            n_mix = len(MIXED_DESCS) * p // 100
            descs = BASE_DESCS + MIXED_DESCS[:n_mix] + PLAIN_DESCS[n_mix:]
            ranked = run_bm25(descs, q, top_k=len(descs))
            hits = [(d, s) for d, s in ranked if s > 0]
            norm = dict(_min_max_normalize(hits))
            top_doc, top_score = (hits[0] if hits else (None, 0.0))
            top_name = describe(top_doc) if top_doc is not None else "-"
            others = "、".join(
                f"{describe(d)}({s:.2f}→norm{norm[d]:.2f})"
                for d, s in hits[1:]) or "无"
            print(f"{p:<8}%{len(hits):<6}{top_name:<22}{top_score:<8.3f}{others}")
        print("-" * 78)

    print("""
[分析]
查询 A (extract text from pdf):
  混合比例↑ → 命中工具数↑（pdf/text/document 共享 token 的假命中扩张）
  → pdf 工具归一化分从 1.0 被稀释（假命中抢分）, top1 可能易主
查询 B (send email to user):
  纯中文描述下 0 命中; 混合描述含 email token → 新工具可被英文检索
  （正面: 描述双语化扩大 BM25 跨语言覆盖面）
结论:
  1) 混合描述 = 双刃剑: 扩大覆盖面(正面) + 假命中/IDF 稀释(负面)
  2) 影响程度取决于共享 token 的分布密度, 而非工具数量
  3) 缓解: 别名按语义独占分配(不共享), 或对英文 token 做白名单过滤
""")


if __name__ == "__main__":
    main()
