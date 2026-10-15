"""统一路由注册入口（历史聚合器已退役，2026-10-15）

⚠️ 本文件**不再提供** `register_all_routes`。
------------------------------------------------------------------
真实的路由注册发生在 `app_server.py`，那里**逐个模块显式注册**（大量
`from agent.server_routes.routes_x import register_routes as reg_x` + try/except 块）。

历史教训：`register_all_routes` 从未被调用，且它聚合的 9 个模块
（chat / sessions / panorama / config / personality / permission / workspace /
monitoring / extensions）从未接线、端点由 plugins/* 提供 —— 它只会让人误以为
「只改本文件就生效」。2026-10-15 这批未接线模块一次性退役，本聚合器一并删除。

⇒ 新增路由模块的正确做法：在 `app_server.py` 里照抄一个既有 try/except 块显式注册。
   只改本文件不会生效。
"""
