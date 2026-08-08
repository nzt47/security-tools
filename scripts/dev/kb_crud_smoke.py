"""知识库 CRUD 接口冒烟测试（mock 数据 · 真实服务 5678）。

用法:
    python scripts/dev/kb_crud_smoke.py

覆盖链路（真实 HTTP 调用，验证运行时路由 + CardStore 接线）:
    POST  创建（含正文双链，验证 links 解析与入链索引登记）
    GET   列表（全部 / status 过滤）
    GET   详情（验证 incoming_links）
    PATCH 更新（字段）
    PATCH transition 非法迁移 → 409
    DELETE 有入链 → 409（含 incoming_links）
    DELETE 无入链 → 200
    清理：脚本末尾删除全部 mock 卡，不污染知识库

mock 数据使用独立前缀 `smoke-*`，避免与真实卡片冲突。
"""

import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:5678"
PASS, FAIL = 0, 0


def call(method: str, path: str, body: dict | None = None):
    """发起 HTTP 请求，返回 (status_code, json_dict)。"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main() -> int:
    print("=== 知识库 CRUD 冒烟测试 (mock: smoke-*) ===")

    # ── 1. 创建：3 张卡，含双链关系 smoke-a ← smoke-b、smoke-a ← smoke-c ──
    print("\n[1] POST /api/knowledge/cards 创建")
    cards = [
        {
            "title": "Smoke Alpha", "slug": "smoke-alpha",
            "status": "current", "type": "concepts",
            "source": "smoke-test", "date": "2026-08-08",
            "insight": "冒烟测试核心洞见 A", "content": "A 的正文",
        },
        {
            "title": "Smoke Beta", "slug": "smoke-beta",
            "status": "current", "type": "concepts",
            "source": "smoke-test", "date": "2026-08-08",
            "insight": "冒烟测试核心洞见 B",
            "content": "B 引用 [[smoke-alpha]] 的双链正文",
        },
        {
            "title": "Smoke Gamma", "slug": "smoke-gamma",
            "status": "draft", "type": "insights",
            "source": "smoke-test", "date": "2026-08-08",
            "insight": "冒烟测试核心洞见 C",
            "content": "C 也引用 [[smoke-alpha]] 与 [[smoke-beta]]",
        },
    ]
    for c in cards:
        status, body = call("POST", "/api/knowledge/cards", c)
        expected_links = {"smoke-alpha": [], "smoke-beta": ["smoke-alpha"], "smoke-gamma": ["smoke-alpha", "smoke-beta"]}
        check(
            f"创建 {c['slug']} → 201",
            status == 201 and body.get("ok"),
            f"got {status} {body}",
        )
        got_links = (body.get("card") or {}).get("links", [])
        check(
            f"  {c['slug']} 正文双链已解析 links={got_links}",
            sorted(got_links) == sorted(expected_links[c["slug"]]),
            f"expected {expected_links[c['slug']]}",
        )

    # ── 2. 列表 ──
    print("\n[2] GET /api/knowledge/cards 列表")
    status, body = call("GET", "/api/knowledge/cards")
    smoke_slugs = [c["slug"] for c in body.get("cards", []) if c["slug"].startswith("smoke-")]
    check("列表含 3 张 smoke 卡", len(smoke_slugs) == 3, f"got {smoke_slugs}")
    status, body = call("GET", "/api/knowledge/cards?status=draft")
    draft_slugs = [c["slug"] for c in body.get("cards", []) if c["slug"].startswith("smoke-")]
    check("status=draft 过滤仅 smoke-gamma", draft_slugs == ["smoke-gamma"], f"got {draft_slugs}")

    # ── 3. 详情：验证 incoming_links ──
    print("\n[3] GET /api/knowledge/cards/smoke-alpha 详情")
    status, body = call("GET", "/api/knowledge/cards/smoke-alpha")
    card = body.get("card", {})
    check("详情 200 且 links/contradictions/incoming 齐全",
          status == 200 and "links" in card and "contradictions" in card and "incoming_links" in card,
          f"got {status} keys={list(card.keys())}")
    check("smoke-alpha 入链 = [smoke-beta, smoke-gamma]",
          sorted(card.get("incoming_links", [])) == ["smoke-beta", "smoke-gamma"],
          f"got {card.get('incoming_links')}")

    # ── 4. PATCH 更新字段 ──
    print("\n[4] PATCH /api/knowledge/cards/smoke-beta 更新")
    status, body = call("PATCH", "/api/knowledge/cards/smoke-beta", {"insight": "更新后的洞见 B"})
    check("更新 200 且 insight 生效", status == 200 and body.get("card", {}).get("insight") == "更新后的洞见 B",
          f"got {status} {body}")

    # ── 5. PATCH 非法 transition → 409 ──
    print("\n[5] PATCH transition 非法迁移 (draft → archive 直跳)")
    status, body = call("PATCH", "/api/knowledge/cards/smoke-gamma", {"transition": "archive"})
    check("非法迁移返回 409", status == 409 and not body.get("ok"), f"got {status} {body}")

    # ── 6. DELETE 有入链 → 409 ──
    print("\n[6] DELETE /api/knowledge/cards/smoke-alpha (有入链)")
    status, body = call("DELETE", "/api/knowledge/cards/smoke-alpha")
    check("删除被拒 409 且带 incoming_links",
          status == 409 and sorted(body.get("incoming_links", [])) == ["smoke-beta", "smoke-gamma"],
          f"got {status} {body}")

    # ── 7. 删除无入链卡：先删 smoke-gamma（无入链）──
    print("\n[7] DELETE /api/knowledge/cards/smoke-gamma (无入链)")
    status, body = call("DELETE", "/api/knowledge/cards/smoke-gamma")
    check("删除成功 200", status == 200 and body.get("ok"), f"got {status} {body}")
    # smoke-gamma 被删后，smoke-beta 入链清空
    status, body = call("GET", "/api/knowledge/cards/smoke-beta")
    check("smoke-beta 入链清空为 []",
          body.get("card", {}).get("incoming_links") == [],
          f"got {body.get('card', {}).get('incoming_links')}")
    # smoke-alpha 入链收窄为 [smoke-beta]
    status, body = call("GET", "/api/knowledge/cards/smoke-alpha")
    check("smoke-alpha 入链收窄为 [smoke-beta]",
          sorted(body.get("card", {}).get("incoming_links", [])) == ["smoke-beta"],
          f"got {body.get('card', {}).get('incoming_links')}")

    # ── 8. 清理：删除剩余 smoke 卡（smoke-beta 入链已清空）──
    print("\n[8] 清理 smoke-* 卡")
    for slug in ["smoke-beta", "smoke-alpha"]:
        status, body = call("DELETE", f"/api/knowledge/cards/{slug}")
        check(f"清理 {slug} → 200", status == 200, f"got {status} {body}")

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
