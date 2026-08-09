"""诊断：截图当前页面 + 输出 body 前 2000 字符"""
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto('http://localhost:5173/static/', wait_until='domcontentloaded', timeout=20000)
    page.wait_for_timeout(3000)
    page.screenshot(path='kb_diag_page.png', full_page=True)
    print('[OK] 截图 kb_diag_page.png')
    text = page.inner_text('body')
    print('[BODY] 前 1500 字符:')
    print(text[:1500])
    print('---')
    # 列出所有 button
    btns = page.locator('button').all_inner_texts()
    print('[BUTTONS]', btns[:20])
    browser.close()
