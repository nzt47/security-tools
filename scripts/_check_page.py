"""Check page rendering with Selenium"""
import sys
import time

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--window-size=1280,900')

    driver = webdriver.Chrome(options=opts)
    driver.get('http://127.0.0.1:5678/')
    time.sleep(3)

    print("=== PAGE INFO ===")
    print(f"Title: {driver.title}")

    # Check console errors
    logs = driver.get_log('browser')
    errors = [l for l in logs if l['level'] == 'SEVERE']
    warnings = [l for l in logs if l['level'] == 'WARNING']
    print(f"\nConsole errors: {len(errors)}")
    for e in errors[:5]:
        print(f"  [{e['level']}] {e['message'][:200]}")
    print(f"Console warnings: {len(warnings)}")
    for w in warnings[:5]:
        print(f"  [{w['level']}] {w['message'][:200]}")

    # Check DOM structure
    print("\n=== #app ===")
    app = driver.find_element('id', 'app')
    print(f"Classes: {app.get_attribute('class')}")

    print("\n=== #main ===")
    main = driver.find_element('id', 'main')
    print(f"Display: {main.value_of_css_property('display')}")
    print(f"Width: {main.size['width']}px Height: {main.size['height']}px")

    print("\n=== #chat-view ===")
    chat = driver.find_element('id', 'chat-view')
    print(f"Classes: {chat.get_attribute('class')}")
    print(f"Display: {chat.value_of_css_property('display')}")
    print(f"Size: {chat.size}")
    print(f"Inline style: {chat.get_attribute('style')}")

    print("\n=== #chat-input-area ===")
    try:
        input_area = driver.find_element('id', 'chat-input-area')
        print(f"Display: {input_area.value_of_css_property('display')}")
        print(f"Size: {input_area.size}")
    except:
        print("NOT FOUND!")

    print("\n=== #panorama-view ===")
    pano = driver.find_element('id', 'panorama-view')
    print(f"Classes: {pano.get_attribute('class')}")
    print(f"Display: {pano.value_of_css_property('display')}")
    print(f"Size: {pano.size}")

    # Take screenshot
    driver.save_screenshot('c:/Users/Administrator/agent/render_check.png')
    print("\nScreenshot saved to render_check.png")

    driver.quit()
    print("\nDone!")
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()
