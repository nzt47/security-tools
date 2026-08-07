"""两个检索模拟脚本 + 可视化面板的公共部分：语料、用例、CSV 导出

【简易】纯标准库，独立于 agent 包。被 simulate_workflow_matcher.py /
        simulate_hybrid_retrieval.py / plot_vector_compare.py / animate_alpha.py 复用
【变易】语料/用例集中维护，新增用例只改 TEST_CASES 一处
"""
import csv
import os

# 输出目录：agent/data/sim_results
CSV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "..", "..", "data", "sim_results")

# 工具/工作流语料（两个引擎共用同一批文档，保证可对比）
# description 追加 "(alias: ...)" 英文别名——BM25 索引 name+description,
# 英文查询靠别名 token 命中（跨语言 bridge），别名按语义独占分配避免 IDF 稀释:
#   pdf解析: pdf/extract/parse/document/text（"text" 仅此处）
#   翻译英文: translate/english/language
#   图片文字识别: ocr/image/recognize
#   数据库查询: sql/database/query
TOOLS = [
    {"name": "pdf解析",
     "description": "解析PDF文件提取文字内容 (alias: pdf, extract, parse, document, text)"},
    {"name": "翻译英文",
     "description": "把中文翻译成英文 (alias: translate, english, language)"},
    {"name": "图片文字识别",
     "description": "识别图片中的文字 (alias: ocr, image, recognize)"},
    {"name": "数据库查询",
     "description": "执行SQL查询数据库 (alias: sql, database, query)"},
]

# wf-id → 显示名（TF-IDF 脚本用 wf-id，混合脚本用 name，绘图时对齐）
WF_NAME = {
    "wf-pdf-parse": "pdf解析",
    "wf-translate-en": "翻译英文",
    "wf-img-ocr": "图片文字识别",
    "wf-sql-query": "数据库查询",
}

# 统一的 6 组中文测试用例（与 animate_alpha.py 面板共用）
#   1. 字面重叠 —— 双路都应命中（基准）
#   2. 同义改写 —— 无字面重叠，语义等价（TF-IDF 弱项，Embedding 强项）
#   3. 口语化   —— 用"图/字"等部分字面 token + 口语表达
#   4. 缩写/中英混排 —— 靠 sql 等关键词字面命中
#   5. 完全无关 —— 双路都应低分/不命中
#   6. 两路冲突 —— "图片里的英文"既是 OCR 场景又含翻译词，用于观察 alpha 翻转
#   7. 两路冲突候选 —— BM25 字面强命中 OCR（识/别/图/片/文/字 6 词），
#      Embedding 语义可能指向翻译（"翻译"动词强信号）→ 潜在 top1 翻转
#   ── 跨语言验证组（英文 / 中英混合, 验证漂移规律不依赖语言）──
#   8.  纯英文字面命中 —— "pdf" 与工具名 PDF 小写后共享 token
#   9.  纯英文零字面重叠 —— BM25 应 0 分, 仅 Embedding 跨语言语义命中
#   10. 英文混合意图 —— BM25 字面命中 pdf, Embedding 语义可能指向翻译 → 潜在翻转
#   11. 中英混合 —— "pdf/提取" 双字面命中
#   12. 英文负样本 —— 双路都应低分/不命中
TEST_CASES = [
    "帮我解析pdf文件",
    "把这段话转成英语",
    "帮我看看图里有啥字",
    "跑个sql看下数据",
    "今天天气怎么样",
    "把图片里的英文翻译成中文",
    "识别并翻译图片里的文字",
    "extract text from pdf",
    "translate this into english",
    "translate the text in this pdf",
    "把pdf里的表格提取出来",
    "what is the weather today",
]


def export_csv(rows, filename, fieldnames):
    """导出 CSV 到 data/sim_results/（utf-8-sig，Excel 直接打开不乱码）

    Args:
        rows: list[dict]，每行一个 dict
        filename: 文件名（如 "tfidf_results.csv"）
        fieldnames: 列顺序
    Returns:
        csv_path 绝对路径
    """
    os.makedirs(CSV_DIR, exist_ok=True)
    csv_path = os.path.join(CSV_DIR, filename)
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"[CSV] 已导出 {len(rows)} 行 → {os.path.normpath(csv_path)}")
    return csv_path
