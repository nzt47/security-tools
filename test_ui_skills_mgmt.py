"""端到端验证技能管理 v2 + 工作流学习 UI"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:5678"
SHOTS = Path("c:/Users/Administrator/agent/docs/ui_test_shots")
SHOTS.mkdir(parents=True, exist_ok=True)

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})

        # 收集 console 错误
        errors = []
        page.on("console", lambda msg: errors.append(f"[{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None)

        print("[1] 导航到主页...")
        page.goto(BASE, wait_until="networkidle", timeout=20000)
        page.wait_for_timeout(1000)

        print("[2] 点击 '技能管理' 导航按钮...")
        page.click('.nav-btn[data-view="skills"]')
        page.wait_for_timeout(1500)
        page.screenshot(path=str(SHOTS / "01_skills_basic.png"), full_page=False)
        print(f"    截图: 01_skills_basic.png")

        # 验证 view-skills 可见
        view_skills = page.locator('#view-skills')
        if view_skills.is_visible():
            print("    [OK] view-skills 可见")
        else:
            print("    [FAIL] view-skills 不可见!")

        # 验证 basic tab 默认激活
        basic_tab = page.locator('#sktab-basic')
        if basic_tab.is_visible():
            print("    [OK] 基础技能 Tab 默认可见")
        else:
            print("    [FAIL] 基础技能 Tab 不可见!")

        print("[3] 切换到 '技能管理 v2' Tab...")
        page.click('.skmgmt-tabbtn[data-sktab="v2"]')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "02_skills_v2.png"), full_page=False)
        print(f"    截图: 02_skills_v2.png")

        # 验证 v2 tab 可见
        v2_tab = page.locator('#sktab-v2')
        if v2_tab.is_visible():
            print("    [OK] 技能管理 v2 Tab 可见")
        else:
            print("    [FAIL] 技能管理 v2 Tab 不可见!")

        # 验证工具栏元素存在
        search_input = page.locator('#skmgmt-search')
        if search_input.count() > 0:
            print("    [OK] 搜索框存在")
        else:
            print("    [FAIL] 搜索框不存在!")

        # 验证列表区域
        list_pane = page.locator('#skmgmt-list')
        if list_pane.count() > 0:
            list_html = list_pane.inner_text()
            print(f"    [OK] 列表区域存在, 内容: {list_html[:80]}...")
        else:
            print("    [FAIL] 列表区域不存在!")

        # 验证详情区域
        detail_pane = page.locator('#skmgmt-detail')
        if detail_pane.count() > 0:
            print("    [OK] 详情区域存在")
        else:
            print("    [FAIL] 详情区域不存在!")

        # 验证健康指示器
        health = page.locator('#skmgmt-health')
        if health.count() > 0:
            health_class = health.get_attribute("class")
            print(f"    [OK] 健康指示器存在, class={health_class}")
        else:
            print("    [FAIL] 健康指示器不存在!")

        print("[4] 切换到 '工作流学习' Tab...")
        page.click('.skmgmt-tabbtn[data-sktab="workflow"]')
        page.wait_for_timeout(2000)
        page.screenshot(path=str(SHOTS / "03_workflow.png"), full_page=False)
        print(f"    截图: 03_workflow.png")

        # 验证 workflow tab 可见
        wf_tab = page.locator('#sktab-workflow')
        if wf_tab.is_visible():
            print("    [OK] 工作流学习 Tab 可见")
        else:
            print("    [FAIL] 工作流学习 Tab 不可见!")

        # 验证工作流列表区域
        wf_list = page.locator('#wf-list')
        if wf_list.count() > 0:
            wf_html = wf_list.inner_text()
            print(f"    [OK] 工作流列表区域存在, 内容: {wf_html[:80]}...")
        else:
            print("    [FAIL] 工作流列表区域不存在!")

        # 验证匹配输入框
        wf_input = page.locator('#wf-match-input')
        if wf_input.count() > 0:
            print("    [OK] 匹配输入框存在")
        else:
            print("    [FAIL] 匹配输入框不存在!")

        print("[5] 测试搜索功能（输入文字触发防抖）...")
        page.click('.skmgmt-tabbtn[data-sktab="v2"]')
        page.wait_for_timeout(500)
        page.fill('#skmgmt-search', '测试')
        page.wait_for_timeout(1000)  # 等待防抖 300ms + 请求
        page.screenshot(path=str(SHOTS / "04_search_test.png"), full_page=False)
        print(f"    截图: 04_search_test.png")

        print("[6] 测试 '新建技能' 按钮点击...")
        # 用 evaluate 直接触发，避免选择器问题
        clicked = page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll('.skmgmt-toolbar button'))
                .find(b => b.textContent.includes('新建技能'));
            if (btn) { btn.click(); return true; }
            return false;
        }""")
        page.wait_for_timeout(1000)
        if clicked:
            page.screenshot(path=str(SHOTS / "05_creator_modal.png"), full_page=False)
            print(f"    截图: 05_creator_modal.png")
            # 检查 modal 是否出现
            modal = page.evaluate("""() => {
                const m = document.querySelector('.skmgmt-modal-overlay, .confirm-overlay');
                return m ? m.innerHTML.substring(0, 100) : null;
            }""")
            if modal:
                print(f"    [OK] 创建技能弹窗已出现: {modal[:60]}...")
            else:
                print("    [WARN] 弹窗未检测到（可能选择器不同）")
        else:
            print("    [FAIL] 未找到 '新建技能' 按钮!")

        print("\n=== 控制台错误/警告 ===")
        if errors:
            for e in errors[:10]:
                print(f"  {e}")
        else:
            print("  无错误/警告")

        browser.close()
        print("\n=== 验证完成 ===")

if __name__ == "__main__":
    main()
