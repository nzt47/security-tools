"""知识检索双链扩展验证：构造边界知识库 + 启动本地服务（供 curl/Postman 实测）。

用途（配合 /api/knowledge/query 验证三路召回与双链扩展）：
1. 在临时目录构造一张覆盖边界情况的卡片库（不污染项目 knowledge/）。
2. 启动 Flask 本地服务（默认 127.0.0.1:8787），注册 routes_knowledge，
   注入 Yunshu._card_store，使 /api/knowledge/query 走完整 KnowledgeSearch 链路。
3. search.py 已加详细日志：三路召回明细 / RRF 融合归一化分 / 重排明细，
   服务终端可直接观察候选集变化。

边界数据集（关键词 + 链接关系）：
| 卡片          | 关键词命中("双链") | links                      | 预期                                 |
|---------------|-------------------|----------------------------|--------------------------------------|
| 驾驭工程      | ✓                 | [提示词工程]                | BM25 命中 + 扩展出 提示词工程          |
| 提示词工程    | ✗（不含 双/链 单字） | [驾驭工程, 多跳终点]       | 仅经双链扩展路召回（一跳）；其 links 不递归 |
| 断链引用      | ✓                 | [幽灵引用]                  | BM25 命中，断链目标跳过              |
| 归档引用      | ✓                 | [archives/旧卡片]           | BM25 命中，归档目标跳过              |
| 多跳终点      | ✗                 | []                          | 不应召回（两跳，扩展只取一跳）       |
| 无关概念      | ✗("烘焙")         | []                          | 无关查询命中，不受双链影响           |
| 高频词卡×20   | ✗("测试")         | []                          | 误召回保护：query=测试 → 空          |

误召回保护数据构造：全库 26 张卡 content 均含「测试环境占位」→ "测/试" 单字
doc_count=26，idf 极低（≈0.019），query=测试 时每卡 BM25 分数均 < 0.3
（低置信度命中），top1 的 max(原始分数) < min_score → 触发保护返回空。

复杂模式（--complex）：10 张复杂双链关系卡 + fake 向量路，查询「机器学习」
验证 RRF 融合多路累加（模型训练=BM25+向量两路 → 0.667 居首）、双链扩展
平等参与排名（模型评估仅双链 → 0.333）与去重不双计，详见 build_complex_wiki docstring。

用法（Windows PowerShell）：
    $env:PYTHONIOENCODING="utf-8"
    python scripts/dev/verify_knowledge_search.py [--port 8787] [--wiki 目录] [--complex]
"""

from __future__ import annotations

import argparse
import logging
import sys
import tempfile
from pathlib import Path

# 使脚本可在任意 cwd 下运行：把项目根加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask

from agent.knowledge import Card, CardStore
from agent.server_routes.routes_knowledge import register_routes

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


def _card(title: str, content: str, links=None, *, type: str = "concepts") -> Card:
    """构造合法卡片（slug == slugify(title)，通过 schema 校验）。"""
    return Card(
        title=title,
        slug=title,  # 中文标题 slugify 幂等
        status="current",
        type=type,
        source="scripts/dev/verify_knowledge_search.py",
        date="2026-08-07",
        links=links or [],
        insight=f"{title} 的一句话核心洞见",
        content=content,
    )


# ═══════════════════════════════════════════════════════════
#  --complex 模式：fake 向量路（演示 RRF 多路累加）
# ═══════════════════════════════════════════════════════════

class _VecItem:
    """向量条目：metadata.slug 对齐 KnowledgeSearch._slug_from_item 契约。"""

    def __init__(self, slug: str):
        self.metadata = {"slug": slug}
        self.id = slug


class _KeywordVectorStore:
    """伪向量路：content 命中「调参/数据/特征」关键词即召回（模拟语义召回）。

    Why 命中 模型训练(调参) + 特征工程(数据/特征)：
    - 模型训练 = BM25 rank1 + 向量 rank1 → 2/61 → 0.667 居首（★RRF 多路累加）；
    - 特征工程不含查询词「机器学习」（BM25 不命中），仅向量路 rank2 → 0.328，
      且因是种子（向量命中）→ 双链扩展对其「重复跳过」不双计（★去重防重复计分）。
    """

    def __init__(self, cards: list[Card]):
        self._cards = cards

    def search(self, query, top_k: int = 10):
        return [
            _VecItem(c.slug)
            for c in self._cards
            if any(k in c.content for k in ("调参", "数据", "特征"))
        ][:top_k]


def build_wiki(wiki_root: Path) -> CardStore:
    """构造边界知识库并返回 CardStore。"""
    store = CardStore(
        wiki_root,
        archives_dir=wiki_root.parent / "archives",
        index_path=wiki_root.parent / "index.md",
        log_path=wiki_root.parent / "log.md",
    )
    # 全库 content 均含「测试环境占位」→ "测/试" 单字 doc_count=26，
    # idf=(26-26+0.5)/(26+0.5)≈0.019 极低 → query=测试 时 BM25 分数<0.3，
    # 触发误召回保护（验证低置信度命中被拦截）；其余查询词不含 测/试 单字，不受影响。
    store.create(_card("驾驭工程", "通过设计人机协作边界实现双链管理（测试环境占位）", links=["提示词工程"]))
    # 提示词工程：content 不含「双/链」单字（避免 BM25 单字分词误命中），
    # 使双链扩展路成为唯一召回路；links 含 多跳终点 验证「只扩展一跳、不递归」
    store.create(_card("提示词工程", "提示词模板设计与工程实践（测试环境占位）", links=["驾驭工程", "多跳终点"]))
    store.create(_card("断链引用", "断链场景下的双链容错（测试环境占位）", links=["幽灵引用"]))
    store.create(_card("归档引用", "归档目标的双链处理（测试环境占位）", links=["archives/旧卡片"]))
    store.create(_card("多跳终点", "多跳场景不应被递归扩展（测试环境占位）"))
    store.create(_card("无关概念", "烘焙温度与发酵时间控制（测试环境占位）"))
    for i in range(20):
        store.create(_card(f"高频词卡{i}", "测试环境占位"))
    logger.info("知识库构造完成: 卡片数=%d wiki=%s", len(store.list()), wiki_root)
    return store


def build_complex_wiki(wiki_root: Path) -> CardStore:
    """构造复杂双链关系知识库（--complex 模式，验证 RRF 融合 + 双链扩展实际效果）。

    query=「机器学习」时预期（含 fake 向量路，三路召回）：
    - BM25 命中 5 张：模型训练/迁移学习/强化学习/深度学习/在线学习
    - 向量路(fake) 命中 2 张：模型训练(含调参)、特征工程(含数据/特征)
    - 双链扩展 1 张：模型评估（模型训练→）；幽灵节点断链跳过；
      迁移学习→特征工程、强化学习→模型评估、在线学习→深度学习 均重复跳过
    - RRF 融合（n_active=3，max_possible=3/61）：
        模型训练 = BM25 rank1 + 向量 rank1 = 2/61 → 0.667 居首（★多路累加）
        模型评估 = 仅双链 rank1 = 1/61 → 0.333（★双链平等参与排名，与 BM25 rank1 等分）
        特征工程 = 仅向量 rank2 = 1/62 → 0.328（向量命中故为种子 → 双链跳过不双计）
    - 不召回：调参技巧（模型评估 links 不递归）、生成对抗（反向链接不扩展）、无关卡
    """
    store = CardStore(
        wiki_root,
        archives_dir=wiki_root.parent / "archives",
        index_path=wiki_root.parent / "index.md",
        log_path=wiki_root.parent / "log.md",
    )
    store.create(_card("模型训练", "机器学习模型训练与调参的完整流程",
                       links=["特征工程", "模型评估", "幽灵节点"]))
    store.create(_card("迁移学习", "迁移学习减少标注依赖的工程实践", links=["特征工程"]))
    store.create(_card("强化学习", "强化学习探索与利用的平衡", links=["模型评估"]))
    store.create(_card("深度学习", "深度学习反向传播算法原理"))
    store.create(_card("在线学习", "在线学习的流式样本更新", links=["深度学习"]))
    store.create(_card("特征工程", "数据清洗与特征构建方法", links=["模型训练"]))
    store.create(_card("模型评估", "评估指标与交叉验证设计", links=["调参技巧"]))
    store.create(_card("调参技巧", "超参数网格搜索与早停策略"))
    store.create(_card("生成对抗", "GAN 生成对抗网络的训练技巧", links=["模型训练"]))
    store.create(_card("无关卡", "烘焙温度与发酵时间控制"))
    logger.info("复杂知识库构造完成: 卡片数=%d wiki=%s", len(store.list()), wiki_root)
    return store


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="知识检索双链扩展验证服务")
    parser.add_argument("--port", type=int, default=8787, help="监听端口（默认 8787）")
    parser.add_argument(
        "--wiki", type=str, default="",
        help="知识库目录；默认在临时目录构造边界数据",
    )
    parser.add_argument(
        "--complex", action="store_true",
        help="复杂双链关系数据集（含 fake 向量路，演示 RRF 多路累加）",
    )
    args = parser.parse_args(argv)

    wiki = Path(args.wiki) if args.wiki else Path(tempfile.mkdtemp(prefix="kb-verify-")) / "wiki"
    wiki.mkdir(parents=True, exist_ok=True)
    store = build_complex_wiki(wiki) if args.complex else build_wiki(wiki)

    # 复杂模式注入 fake 向量库（演示三路 RRF 融合）；边界模式 None → 向量路空（降级 BM25 + 双链）
    vector_store = _KeywordVectorStore(store.list()) if args.complex else None

    class _Yunshu:
        _card_store = store
        _vector_memory = vector_store

    class _State:
        Yunshu = _Yunshu

    app = Flask(__name__)
    register_routes(app, _State())

    mode = "复杂双链" if args.complex else "边界"
    print(f"\n[OK] 知识检索验证服务启动（{mode}模式）: http://127.0.0.1:{args.port}/api/knowledge/query")
    print("示例:  curl -X POST http://127.0.0.1:{0}/api/knowledge/query -H \"Content-Type: application/json\" -d '{{\"question\":\"双链\"}}'".format(args.port))
    print("      curl -X POST http://127.0.0.1:{0}/api/knowledge/query -H \"Content-Type: application/json\" -d '{{\"question\":\"测试\"}}'  # 误召回保护 → 空".format(args.port))
    if args.complex:
        print("      curl -X POST http://127.0.0.1:{0}/api/knowledge/query -H \"Content-Type: application/json\" -d '{{\"question\":\"机器学习\"}}'  # RRF 多路累加 + 双链扩展".format(args.port))
    app.run(host="127.0.0.1", port=args.port, debug=False, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
