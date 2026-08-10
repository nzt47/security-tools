"""知识库 API 服务入口（容器专用）——仅注册知识库路由的最小 Flask 应用。

【不易】知识库 API 依赖面最小化：
  - agent/__init__.py 采用 PEP 562 懒加载，本入口只 import flask + agent.knowledge
    子模块，不背负 torch/chromadb 等约 3GB 重库；
  - query 检索在无向量/精排接线时降级为 BM25 + 双链扩展（原序降级）。

【变易】wiki 根目录可通过 KNOWLEDGE_WIKI_ROOT 环境变量覆盖（默认 knowledge/wiki，
与宿主端一致）；生产环境必须设置 FLASK_API_TOKEN 开启鉴权。
"""
import os

from flask import Flask

from agent.knowledge.card import CardStore
from agent.server_routes.routes_knowledge import register_routes


def create_app() -> Flask:
    wiki_root = os.environ.get("KNOWLEDGE_WIKI_ROOT", "knowledge/wiki")
    store = CardStore(wiki_root)

    Yunshu = type("_Yunshu", (), {"_card_store": store})()
    state = type("_State", (), {"Yunshu": Yunshu})()

    app = Flask(__name__)
    register_routes(app, state)
    return app


app = create_app()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5678"))
    app.run(host="0.0.0.0", port=port, threaded=True)
