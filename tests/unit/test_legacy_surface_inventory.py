"""遗留 SSR 页面清单守卫（2026-10-03 · legacy 收敛续作）。

【为什么需要它】legacy 收敛（提交 5866daca / 55b98258）删掉了 4 个模板 + 32 个静态文件，
但"删了什么"只存在于提交历史里：没有任何机制阻止下一批改动把旧页面加回来，
也没有任何机制要求"新删的页面"同步收缩清单。本仓已有反面先例 ——
文档把 T2.1–T4.2 标为已完成，而对应产物早已在 9b4aa8f5 被删除（审计 H-4）。

【本次实测的关键事实：这些页面当前全都不可用】
2026-10-03 在本部署（CP_API_AUTH_MODE=enforce_all）逐条活体探测：

    页面（无令牌）                      数据端点（无令牌）
    /approval-console  -> 200           /api/approval/pending    -> 401
    /logs/dashboard    -> 200           /logs/dashboard/data     -> 401
    /replay-viewer     -> 200           （无取数）
    /search-status     -> 200           /api/web/search/status   -> 401
    /chat              -> 200           （React SPA 外壳，hubGet 自带令牌 ⇒ 正常）

即：**页面壳能打开，但一个数据都取不到**（HTML 是服务端渲染的，不需要令牌；
fetch 是浏览器发的，不带令牌）。这类页面在浏览器里表现为"永久加载中/空白"，
是纯噪音 —— 也正是本次退役 /dashboard 的同一判据。
auth_debt 字段就是把这些**已知不可用**的页面显式登记，避免"看起来还在提供能力"。

【纪律】
  ① 清单**只允许收缩**：移除页面必须同步改本文件；**新增** legacy SSR 页面会被红灯挡住
     （新能力应落在 React 工作台的 hubNav 里，而不是再来一个 SSR 页）；
  ② 每个页面的取数目标集合与磁盘实况逐字对齐 —— 悄悄新增一个 tokenless 依赖即红；
  ③ 已退役的页面不得复活。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TPL = ROOT / "templates"

#: 已知不可用的遗留页：页面可达但取数端点需要令牌，而页面不带令牌。
#: 值为**实测状态码证据**，不是推断；移除页面时请一并删除对应条目。
AUTH_DEBT = {
    "templates/search-status.html": (
        "活体 /api/web/search/status = 401、/api/web/search = 401（均无令牌）"
    ),
    "templates/log_dashboard.html": "活体 /logs/dashboard/data = 401（无令牌）",
    "templates/approval_console.html": "活体 /api/approval/pending = 401（无令牌）",
}

#: 遗留 SSR 页面登记表：模板 → 路由 + 取数端点（**逐字**，改动即红）。
PAGES = {
    "templates/approval_console.html": {
        "route": "/approval-console",
        # 取数逻辑在其配对静态资源里（static/js/approval_console.js 调 /api/approval/*），
        # 故本模板自身字面量为空；配对资源由 test_遗留静态资源与配对关系一致 钉住。
        "endpoints": [],
    },
    "templates/log_dashboard.html": {
        "route": "/logs/dashboard",
        "endpoints": ["/logs/dashboard/data"],
    },
    "templates/replay_viewer.html": {
        "route": "/replay-viewer",
        "endpoints": [],
    },
    "templates/search-status.html": {
        "route": "/search-status",
        # 两处：状态轮询（:474）与真实搜索（:682）。首版登记只写了前者，
        # 被 test_每个页面的取数目标是磁盘实况 当场抓出 —— 这正是该断言存在的意义。
        "endpoints": ["/api/web/search", "/api/web/search/status"],
    },
    "templates/yunshu.html": {
        # React SPA 外壳（由 yunshu-ui 构建同步，见 npm run build:flask）。
        # 它与其余 legacy 页不同：壳内的 hubGet 会自动附带本地 API 令牌，故**不受影响**。
        "route": "/chat",
        "endpoints": [],
    },
}

#: 与遗留页配对的静态资源（保留清单；demo-ui.js 是插件演示件，同样登记以防悄悄消失）。
PAIRED_ASSETS = [
    "static/js/approval_console.js",
    "static/css/approval_console.css",
    "static/plugins/demo-ui.js",
]

#: 已退役的模板：不得复活。
RETIRED = [
    "observability_dashboard.html",  # 本次（H-7 收口）
    "index.html",                    # 5866daca / 55b98258
    "health_dashboard.html",
    "spa.html",
]

#: 同源取数字面量（排除静态资源与锚点）。
#: 【为什么用 \x60 而不是直接写反引号】模板字面量在 JS 里用反引号包裹，
#: 直接写会让本文件的正则难以阅读与转义；\x60 是 re 支持的等价写法，语义相同。
#: 【为什么**不**要求结束引号】模板字面量常写成
#: \`/logs/dashboard/data?hours=\${cur}\` —— 路径后面紧跟 ? 与 \${}，
#: 若正则要求"紧跟同类引号"，这条**真实存在**的取数依赖会被整体漏掉
#: （首版就是这么漏的，被 test_每个页面的取数目标是磁盘实况 当场抓出）。
#: 故只锚定**起始引号**，抓到第一个非路径字符为止。
_LIT = re.compile(r"""['"\x60](/[A-Za-z0-9_\-./{}$:]{2,})""")
_SKIP = ("/static/", "/static-assets/", "/assets/", "/favicon", "/chat#", "/dashboard#")


def _scan_endpoints(tpl: Path):
    """扫出模板里出现的同源取数路径（去掉查询串）。"""
    txt = tpl.read_text(encoding="utf-8", errors="replace")
    out = set()
    for hit in _LIT.findall(txt):
        if hit.startswith(_SKIP):
            continue
        out.add(hit.split("?")[0])
    return sorted(out)


def _templates_on_disk():
    return sorted(p.name for p in TPL.glob("*.html"))


class Test遗留页面清单:
    def test_清单与磁盘实况逐一对齐(self):
        """少一个 = 有人删了页面却没收缩清单；多一个 = 有人加回了 legacy SSR 页。"""
        declared = {Path(k).name for k in PAGES}
        on_disk = set(_templates_on_disk())
        assert on_disk == declared, (
            "templates/ 与登记表不一致。\n  磁盘多出（新增 legacy SSR 页？新能力请落到 "
            "React 工作台 hubNav）: " + str(sorted(on_disk - declared))
            + "\n  磁盘缺少（已删页面请从 tests/unit/test_legacy_surface_inventory.py 的 "
            "PAGES 删除 —— 本清单只允许收缩）: " + str(sorted(declared - on_disk))
        )

    def test_已退役模板不得复活(self):
        for name in RETIRED:
            assert not (TPL / name).exists(), (
                "已退役的遗留模板复活了：" + name
                + "。legacy 收敛是单向的（审计 K9：legacy 模板 14 → 0 或 ≤2）。"
            )

    def test_每个页面的取数目标是磁盘实况(self):
        """防止悄悄给遗留页新增 tokenless 依赖（审计 H-7 的复发形态）。"""
        bad = []
        for rel, spec in PAGES.items():
            path = ROOT / rel
            assert path.exists(), "登记表引用了不存在的模板：" + rel
            actual = _scan_endpoints(path)
            if actual != spec["endpoints"]:
                bad.append(rel + "\n      登记=" + json.dumps(spec["endpoints"], ensure_ascii=False)
                           + "\n      实况=" + json.dumps(actual, ensure_ascii=False))
        assert not bad, "取数目标与登记不一致：\n  " + "\n  ".join(bad)

    def test_取不到数的遗留页必须登记为已知债(self):
        """页面可达但端点需令牌 ⇒ 页面是空壳，必须显式登记（不许"看起来在提供能力"）。"""
        missing = []
        for rel, spec in PAGES.items():
            if spec["endpoints"] and rel not in AUTH_DEBT:
                missing.append(rel + " -> " + ", ".join(spec["endpoints"]))
        assert not missing, (
            "以下遗留页会向**需要令牌**的端点取数，而页面自身不携带令牌（浏览器 fetch 无凭据）"
            "⇒ 页面必然空壳。请二选一：① 退役该页并从 PAGES 删除；② 写入 AUTH_DEBT 并附"
            "活体实测状态码作为证据。\n  " + "\n  ".join(missing)
        )

    def test_已知债条目必须带实测证据(self):
        """债条目不是"备注"，是可核验的实测结论（否则会退化成一张免责单）。"""
        for rel, reason in AUTH_DEBT.items():
            assert rel in PAGES, "AUTH_DEBT 里的页面不在 PAGES 中：" + rel
            assert re.search(r"\b401\b", reason), (
                "AUTH_DEBT 条目必须写明活体实测状态码（本仓纪律：机制是否生效类判断须实测）"
                "：" + rel
            )

    def test_遗留静态资源与配对关系一致(self):
        for rel in PAIRED_ASSETS:
            assert (ROOT / rel).exists(), (
                "遗留配对资源缺失：" + rel + "（若确已退役，请从 PAIRED_ASSETS 删除）"
            )


class Test_dashboard_收敛:
    """/dashboard 的收敛必须有**代码级**证据，不能只靠本清单的删除记录。"""

    SRC = ROOT / "agent" / "server_routes" / "routes_logging.py"

    def test_改为跳转而不是渲染模板(self):
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert 'render_template("observability_dashboard.html")' not in src, (
            "/dashboard 仍在渲染已删除的 observability_dashboard.html ⇒ 打开即 500"
        )
        assert 'redirect("/chat#/panorama/monitor")' in src, (
            "/dashboard 必须重定向到工作台（与 app_server.py 的 / -> /chat#/workbench 同形态）"
        )

    def test_路由仍然存在(self):
        """重定向而非删路由：旧书签不应变成 404，应被送到工作台。"""
        src = self.SRC.read_text(encoding="utf-8", errors="replace")
        assert '@app.route("/dashboard", methods=["GET"])' in src, (
            "/dashboard 路由被整条删除 —— 旧书签会 404。预期做法是保留路由 + 改跳转。"
        )
