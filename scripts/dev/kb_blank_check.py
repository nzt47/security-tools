"""验证知识库视图空白状态：导航 → 打开知识库 → 截图 + DOM 断言"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto('http://localhost:5173/static/', wait_until='domcontentloaded', timeout=20000)
    page.wait_for_load_state('domcontentloaded')
    page.wait_for_timeout(2500)

    # 点击侧边栏「知识库」入口（按钮文本为 📚\n知识库）
    try:
        page.locator('button').filter(has_text='知识库').first.click()
    except Exception as e:
        print('[FAIL] 未找到知识库入口按钮:', e)
        page.screenshot(path='kb_check_no_entry.png', full_page=True)
        browser.close()
        raise SystemExit(1)
    page.wait_for_timeout(2000)

    # 等待知识库页面核心元素渲染
    try:
        page.get_by_text('融合检索', exact=True).wait_for(timeout=10000)
        page.get_by_text('健康巡检', exact=True).wait_for(timeout=10000)
    except Exception as e:
        print('[FAIL] 知识库页面未渲染:', e)
        page.screenshot(path='kb_check_no_page.png', full_page=True)
        browser.close()
        raise SystemExit(1)

    # 断言：卡片列表区显示空白提示
    body_text = page.inner_text('body')
    has_empty = '暂无卡片' in body_text
    has_demo = ('demo-' in body_text) or ('演示母卡' in body_text)
    print('[CHECK] 空白提示「暂无卡片」:', has_empty)
    print('[CHECK] 无任何演示数据残留:', not has_demo)

    # 截图（整页）
    page.screenshot(path='kb_blank_state.png', full_page=True)
    print('[OK] 截图已保存 kb_blank_state.png')

    # 侧边栏诊断（若断言失败）
    if not has_empty or has_demo:
        try:
            side = page.locator('aside, .sidebar, nav').first
            print('[DIAG] 侧边栏文本:', side.inner_text()[:300])
        except Exception:
            pass
    browser.close()
