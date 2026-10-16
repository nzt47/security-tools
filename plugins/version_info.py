# -*- coding: utf-8 -*-
"""构建/版本信息：只读端点（2026-10-16）。

## 它回答的问题

    GET /api/version ->
      backend.commit / commit_subject / committed_at / dirty
      frontend.bundle / built_at
      process.pid / started_at / python

## 为什么需要它（事故背景）

2026-10-16 实测：主仓工作树停在旧 commit（落后 14 个提交）而远端 PR 已合并，
看护进程（scripts/watchdog_yunshu.py）只探 /api/health、不换代码 ⇒
"线上长期跑旧代码却无人察觉"。前端产物又是 npm run build:flask 的独立步骤。
有了本端点，"现在到底跑的哪版"就从"翻进程/猜"变成一次可脚本化的查询：
scripts/deploy_workbench.py 在重启后就用它做最终校验。

## 口径

- backend.commit 是**进程导入时**解析的 commit（代表本进程加载的代码），
  不是每次请求现查工作树；工作树之后被改不会改变本进程的答案。
- best-effort：无 git / 无前端产物时字段为 None 并带 error，端点仍返回 200。
- 只读、无副作用、无业务内容；与其他 /api/* 一样受共享令牌保护（未加入豁免清单）。

由 plugins/loader.py 目录扫描自动加载（新增本文件即生效，无需改 app_server.py）。
"""
from flask import Blueprint

from .plugin_api import Plugin, register_plugin, require_auth as _require_token
from agent.api_envelope import ok as _ok
from agent.build_info import collect as _collect_build_info

bp = Blueprint("version_info", __name__)


@bp.route("/api/version")
@_require_token
def api_version():
    """返回本进程加载的后端版本 + 线上前端产物 + 进程信息（只读）。"""
    return _ok(_collect_build_info())


PLUGIN = register_plugin(Plugin(
    name="version_info",
    version="1.0.0",
    description="构建/版本信息（只读；后端 commit + 前端 bundle + 进程启动，用于部署漂移排查）",
    blueprint=bp,
))
