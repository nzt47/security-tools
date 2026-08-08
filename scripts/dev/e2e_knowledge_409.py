"""知识库双链 409 保护 · 端到端（E2E）验证用例。

在真实浏览器中走完整用户链路：
  1. 通过 API 预置 3 张 demo 卡（含 [[双链]] 引用，links 由后端正文解析登记）
  2. 浏览器打开知识库视图 → 断言列表渲染 3 张 demo 卡
  3. 打开 demo-子卡 详情 → 断言入链展示 demo-母卡 / demo-伙伴卡
  4. 点击删除 demo-子卡 → 断言弹出入链保护提示（409 + 引用方列表）
  5. 清理全部 demo 卡，知识库恢复原状

前置条件：后端(5678)与前端 dev(5173) 均已启动。
用法:
    python scripts/dev/e2e_knowledge_409.py
退出码：0=全过  1=有失败（供 CI 回归判定）
"""

import json
import sys
import urllib.error
import urllib.request

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5678"
FRONT = "http://localhost:5173/static/"
PASS, FAIL = 0, 0

# ── demo 数据（title/slug 一致，满足 validate_card 的 slug == slugify(title)）──
DEMO_CARDS = [
    {
        "title": "demo-母卡", "slug": "demo-母卡", "status": "current", "type": "concepts",
        "source": "e2e-test", "date": "2026-08-08", "insight": "E2E 演示母卡",
        "content": "引用 [[demo-子卡]] 和 [[demo-伙伴卡]]",
    },
    {
        "title": "demo-子卡", "slug": "demo-子卡", "status": "draft", "type": "entities",
        "source": "e2e-test", "date": "2026-08-08", "insight": "E2E 入链目标",
        "content": "本卡作为入链目标，无自身双链",
    },
    {
        "title": "demo-伙伴卡", "slug": "demo-伙伴卡", "status": "archive", "type": "insights",
        "source": "e2e-test", "date": "2026-08-08", "insight": "E2E 归档演示",
        "content": "引用 [[demo-子卡]]",
    },
]


def http(method: str, path: str, body: dict | None = None):
    """HTTP 调用，返回 (status, json)。"""
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


def ensure_demo_cards():
    """预置 demo 卡（已存在则跳过，不重复创建）。"""
    for c in DEMO_CARDS:
        status, body = http("POST", "/api/knowledge/cards", c)
        if status == 409:  # 已存在，容忍
            continue
        assert status == 201, f"demo 卡创建失败 {c['slug']}: {body}"


def cleanup_demo_cards():
    """清理 demo 卡（先删引用方，再删被引用方），确保知识库恢复原状。"""
    for slug in ("demo-母卡", "demo-伙伴卡", "demo-子卡"):
        http("DELETE", f"/api/knowledge/cards/{urllib.request.quote(slug)}")


def main() -> int:
    global PASS, FAIL
    print("=== 知识库双链 409 保护 E2E (Playwright) ===")

    # ── 0. 预置数据 ──
    print("\n[0] 预置 demo 卡")
    ensure_demo_cards()
    status, body = http("GET", "/api/knowledge/cards")
    slugs = [c["slug"] for c in body.get("cards", []) if c["slug"].startswith("demo-")]
    check("3 张 demo 卡已就绪", len(slugs) == 3, f"got {slugs}")

    dialogs: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.accept()

        page.on("dialog", on_dialog)

        # ── 1. 打开知识库视图 ──
        print("\n[1] 打开知识库视图")
        page.goto(FRONT, wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2500)
        page.locator("button").filter(has_text="知识库").first.click()
        page.get_by_text("融合检索", exact=True).wait_for(timeout=10000)

        # ── 2. 列表渲染 demo 卡 ──
        print("\n[2] 列表渲染 demo 卡")
        page.wait_for_timeout(1500)
        body_text = page.inner_text("body")
        for c in DEMO_CARDS:
            check(f"列表显示 {c['slug']}", c["slug"] in body_text)

        # ── 3. 打开 demo-子卡 详情，验证入链 ──
        print("\n[3] demo-子卡 详情入链")
        page.locator("button").filter(has_text="demo-子卡").first.click()
        page.get_by_text("入链 (2)").wait_for(timeout=10000)
        detail_text = page.inner_text(".kb-detail-drawer")
        check("详情入链数 = 2", "入链 (2)" in detail_text)
        check("入链含 demo-母卡", "demo-母卡" in detail_text)
        check("入链含 demo-伙伴卡", "demo-伙伴卡" in detail_text)
        page.locator(".kb-detail-close").first.click()
        page.wait_for_timeout(500)

        # ── 4. 删除 demo-子卡 → 409 保护提示 ──
        print("\n[4] 删除 demo-子卡 → 409 保护")
        dialogs.clear()
        row = page.locator(".kb-card-item").filter(has_text="demo-子卡").first
        row.locator("button[title='删除']").click()
        page.wait_for_timeout(1500)
        joined = " || ".join(dialogs)
        check("弹出 confirm（删除确认）", len(dialogs) >= 1, f"dialogs={dialogs}")
        check("提示包含「删除被拒」", "删除被拒" in joined, joined)
        check("提示包含引用方 demo-母卡", "demo-母卡" in joined, joined)
        check("提示包含引用方 demo-伙伴卡", "demo-伙伴卡" in joined, joined)

        # ── 5. demo-子卡 未被删除（409 保护生效）──
        page.wait_for_timeout(800)
        body_text = page.inner_text("body")
        check("demo-子卡 仍存在（保护生效）", "demo-子卡" in body_text)

        browser.close()

    # ── 6. 清理 ──
    print("\n[6] 清理 demo 数据")
    cleanup_demo_cards()
    status, body = http("GET", "/api/knowledge/cards")
    remaining = [c["slug"] for c in body.get("cards", []) if c["slug"].startswith("demo-")]
    check("demo 卡已全部清理", len(remaining) == 0, f"remaining={remaining}")

    print(f"\n=== 结果: {PASS} PASS / {FAIL} FAIL ===")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
