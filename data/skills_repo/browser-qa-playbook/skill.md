---
id: browser-qa-playbook
name: 浏览器 QA 手册
description: 用 Selenium 4 在真实浏览器里做前端页面 QA 与回归：按元素清单定位、显式等待、断言、整页截图、采集控制台报错与失败请求，覆盖上线冒烟、改版验证与多视口布局。
version: 1.0.0
author: 云枢
category: custom
source: ingestion-rewrite
tags: [browser, qa, e2e, selenium, 前端回归]
content_type: markdown
status: published
enabled: true
description_zh: 用 Selenium 4 在真实浏览器里做前端页面 QA 与回归：按元素清单定位、显式等待、断言、整页截图、采集控制台报错与失败请求，覆盖上线冒烟、改版验证与多视口布局。
---

# 浏览器 QA 手册

把"看页面"变成可复现的脚本动作与断言。手段固定为 selenium 4.44.0 + headless Edge/Chrome，地址、端口、视口、超时全部参数化。

## 适用场景

- 部署或改版后对 `<BASE_URL><ROUTE>` 做冒烟与回归，确认区块、文案、按钮状态。
- 登录、注册、表单提交、删除确认等流程的端到端验收，含校验报错与成功提示。
- 缺陷复现留证：整页/元素截图、控制台报错、失败请求清单。
- 手机/平板/桌面三档视口巡检，以及上线前的 JS 异常与 4xx/5xx 检查。

不覆盖：接口或数据库校验、性能压测、像素级视觉基线 diff、Firefox/Safari 矩阵。

## 触发条件

用户提出"验收/回归/复现这个页面""看看前端有没有报错""截图给我看效果""上线后确认一下"，或本次改动涉及路由、表单、弹窗、上传、响应式布局，即启用。

前置条件（缺一项先补齐）：

1. 站点可访问，`<BASE_URL>` 与 `<PORT>` 已确认；
2. `python -c "import selenium; print(selenium.__version__)"` 输出 4.44.0；
3. 本机有 Edge 或 Chrome，驱动由 Selenium Manager 自动解析；
4. 账号只从环境变量读，脚本、日志、截图都不出现明文密码。

## 步骤

### 1. 建会话并挂采集钩子

钩子须在导航前注入，否则拿不到报错。

```python
import os
from selenium import webdriver
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.chrome.options import Options as ChromeOptions

HOOK = r"""
window.__qa = {errors: [], failed: []};
addEventListener('error', e => __qa.errors.push(String(e.message)));
addEventListener('unhandledrejection', e => __qa.errors.push('reject: ' + e.reason));
const f = window.fetch;
window.fetch = async (...a) => { const r = await f(...a); if (!r.ok) __qa.failed.push(r.status + ' ' + r.url); return r; };
const s = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function () { this.addEventListener('loadend', () => { if (this.status >= 400) __qa.failed.push(this.status + ' ' + this.responseURL); }); return s.apply(this, arguments); };
"""

def new_driver(browser="edge", headless=True, viewport=(1440, 900)):
    edge = browser == "edge"
    opts = EdgeOptions() if edge else ChromeOptions()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--window-size=%d,%d" % viewport)
    opts.set_capability("ms:loggingPrefs" if edge else "goog:loggingPrefs", {"browser": "ALL"})
    d = (webdriver.Edge if edge else webdriver.Chrome)(options=opts)
    d.set_page_load_timeout(30)
    d.implicitly_wait(0)          # 关闭隐式等待，暴露时序问题
    d.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": HOOK})
    return d
```

### 2. 先导出可交互元素清单，再定位

不猜 CSS。扫出当前文档可见的按钮、链接、输入框并打稳定引用，清单里的文案就是最好的定位依据。

```python
SNAP = """
const sel = 'a[href],button,input,select,textarea,[role=button]';
const out = [];
document.querySelectorAll(sel).forEach(e => {
  const r = e.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return;
  const ref = 'e' + (out.length + 1);
  e.setAttribute('data-qa-ref', ref);
  out.push({ref: '@' + ref, tag: e.tagName.toLowerCase(), text: (e.innerText || e.value || '').trim().slice(0, 40)});
});
return out.slice(0, 200);
"""

def snapshot(d):
    items = d.execute_script(SNAP)
    return [dict(it, sel='[data-qa-ref="%s"]' % it["ref"][1:]) for it in items]
```

ref 只在当前文档有效，导航、重渲染或切 iframe 后要重新 `snapshot`。

### 3. 交互一律走显式等待

```python
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException

def wait_el(d, sel, timeout=10, state="visible"):
    cond = {"visible": EC.visibility_of_element_located, "clickable": EC.element_to_be_clickable}[state]
    return WebDriverWait(d, timeout).until(cond((By.CSS_SELECTOR, sel)))

def click(d, sel, timeout=10):
    el = wait_el(d, sel, timeout, "clickable")
    d.execute_script("arguments[0].scrollIntoView({block:'center'})", el)
    try:
        el.click()
    except ElementClickInterceptedException:   # 被浮层遮挡时退化
        d.execute_script("arguments[0].click()", el)

def fill(d, sel, value, timeout=10):
    el = wait_el(d, sel, timeout)
    el.clear()
    el.send_keys(value)
```

### 4. 断言：把"应该"写成会失败的代码

```python
def must_visible(d, sel, timeout=10):
    el = wait_el(d, sel, timeout)
    assert el.is_displayed(), "应可见却不可见: " + sel
    return el

def must_text(d, sel, expect, timeout=10):
    el = wait_el(d, sel, timeout)
    WebDriverWait(d, timeout).until(lambda _: expect in (el.text or ""))
    assert expect in el.text, "文案不含 %r，实际 %r" % (expect, el.text)
```

其余用 WebElement 接口：`is_enabled()`、`is_selected()`、`get_attribute("aria-invalid")`、`value_of_css_property("display")`；整页文案读 `body.text`，个数用 `len(find_elements())`。

### 5. 截图存证

```python
def shot(d, name, out_dir, full=False):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, name + ".png")
    if full:      # 视口外内容靠 CDP 改高，拍完还原
        m = d.execute_cdp_cmd("Page.getLayoutMetrics", {})["cssContentSize"]
        d.execute_cdp_cmd("Emulation.setDeviceMetricsOverride", {"width": int(m["width"]), "height": int(m["height"]), "deviceScaleFactor": 1, "mobile": False})
    d.save_screenshot(path)
    if full:
        d.execute_cdp_cmd("Emulation.clearDeviceMetricsOverride", {})
    return path
```

元素局部图用 `wait_el(d, sel).screenshot(...)`；断言失败先截图再抛异常，证据不能丢。

### 6. 汇总控制台与失败请求

```python
def collect(d):
    js = d.execute_script("return window.__qa || {errors: [], failed: []}")
    try:
        severe = [e["message"] for e in d.get_log("browser") if e["level"] == "SEVERE"]
    except Exception:        # 个别驱动未开日志能力
        severe = []
    return {"js_errors": sorted(set(js["errors"] + severe)), "failed_requests": sorted(set(js["failed"]))}
```

钩子随导航重装，读完当前页再跳走；第三方噪声单列 noise，不从证据里删。

### 7. 多视口回归与报告

```python
for w, h in [(375, 812), (768, 1024), (1440, 900)]:
    d.set_window_size(w, h)
    must_visible(d, ".hero")                  # 每个视口都要断言，不能只截图
    assert not d.execute_script("return document.documentElement.scrollWidth > %d" % w), "出现横向滚动"
    shot(d, "hero-%dx%d" % (w, h), OUT_DIR, full=True)
```

每步结果记入 `checks`（name/ok/detail），截图路径进 `evidence`，最终写 `report.json`；`finally` 里 `d.quit()` 并清理测试数据。

## 参数

| 参数 | 取值/环境变量 | 默认 | 说明 |
| --- | --- | --- | --- |
| 站点根地址 | `<BASE_URL>` | http://127.0.0.1:`<PORT>` | 端口单独参数化 |
| 待验路由 | `<ROUTE>` | /login | 页面路径 |
| 视口 | `<W>x<H>` | 1440x900 | 传给 `--window-size` |
| 超时 | `<TIMEOUT>` | 10 | 等待上限（秒） |
| 证据目录 | `OUT_DIR` | qa-out | 截图与 report.json |
| 浏览器 | `BROWSER` | edge | edge / chrome |
| 驱动路径 | `<DRIVER_PATH>` | 空=自动 | 离线传 `Service(executable_path=...)` |
| 账号 | `TEST_EMAIL` / `TEST_PASSWORD` | 无 | 只读环境变量 |

## 示例

```python
# 上文片段存为 qa.py；设置 TEST_EMAIL / TEST_PASSWORD 后运行
import os
from qa import new_driver, snapshot, fill, click, must_visible, must_text, shot, collect

OUT, BASE = os.environ.get("OUT_DIR", "qa-out"), os.environ.get("BASE_URL", "http://127.0.0.1:<PORT>")
d = new_driver(os.environ.get("BROWSER", "edge"), viewport=(1440, 900))
try:
    d.get(BASE + "<ROUTE>")
    print(snapshot(d)[:8])              # 先看清页面元素
    fill(d, 'input[name="email"]', os.environ["TEST_EMAIL"])
    fill(d, 'input[name="password"]', os.environ["TEST_PASSWORD"])
    click(d, 'button[type="submit"]')
    must_visible(d, ".dashboard")
    must_text(d, ".dashboard h1", "工作台")
    shot(d, "after-login", OUT, full=True)
    report = collect(d)
    print(report)
    assert not report["js_errors"], "有 JS 报错"
finally:
    d.quit()
```

## 反模式与常见坑

1. **`time.sleep` 顶等待**：又慢又脆，统一 WebDriverWait。
2. **硬编码快照序号 `@e3`**：本次渲染的临时编号，页面一改就错，改用文案定位。
3. **复用 driver 跨用例**：登录态与 localStorage 互相污染，每用例新建或清 cookie。
4. **headless 拍到白屏**：未等 `readyState` 完成与关键元素可见。
5. **点击没反应**：元素在视口外或被浮层遮住；先 scrollIntoView 再 JS 兜底，并查 `pointer-events`。
6. **StaleElementReferenceException**：DOM 重渲染后旧引用失效，动作前重新定位。
7. **iframe 内定位不到**：`switch_to.frame(...)` 进入，结束 `default_content()` 回来。
8. **想自动化系统文件对话框**：做不到；`input[type=file]` 直接 `send_keys(绝对路径)`。
9. **`get_log("browser")` 为空**：能力须启动前设，Edge 用 `ms:loggingPrefs`、Chrome 用 `goog:` 前缀，写错静默失效。
10. **凭据进脚本或截图**：密码只走环境变量，日志里也不打印。
11. **只看文本就下结论**：状态断言用 `is_enabled`/`is_selected`/`aria-*`。
12. **一上来全站回归**：先单页冒烟，首个失败即停。
