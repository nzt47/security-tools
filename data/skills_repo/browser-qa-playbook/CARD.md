# 浏览器 QA 手册

> LLM 整理，逐条经**原文引用校验**：verified 38 / 43（未通过的已丢弃）。

## 何时使用

- 部署或改版后对指定路由做冒烟与回归，确认区块、文案、按钮状态。
- 登录、注册、表单提交、删除确认等流程的端到端验收，含校验报错与成功提示。
- 缺陷复现留证：整页或元素截图、控制台报错、失败请求清单。
- 手机、平板、桌面三档视口巡检，以及上线前检查 JS 异常与 4xx/5xx。
- 不覆盖接口或数据库校验、性能压测、像素级视觉基线 diff、Firefox/Safari 矩阵。
- 用户提出验收、回归、复现页面、看前端报错、截图看效果、上线后确认，或改动涉及路由、表单、弹窗、上传、响应式布局时启用。

## 步骤

- 采集钩子必须在导航前注入，否则拿不到报错。
- 建会话时关闭隐式等待，以暴露时序问题。
- 先导出可交互元素清单再定位，不猜 CSS，用清单文案作为定位依据。
- 把预期写成会失败的断言，如必须可见、文案必须包含。
- 其余断言用 WebElement 接口：is_enabled、is_selected、get_attribute、value_of_css_property，整页文案读 body.text，个数用 find_elements 长度。
- 整页截图通过 CDP 改高后拍摄，拍完还原；元素局部图用元素 screenshot。
- 断言失败先截图再抛异常，证据不能丢。
- 汇总控制台与失败请求，钩子随导航重装，读完当前页再跳走。
- 多视口回归时每个视口都要断言，不能只截图，并检查是否出现横向滚动。
- 每步结果记入 checks，截图路径进 evidence，最终写 report.json，finally 里 quit 并清理测试数据。

## 参数

- 站点根地址默认 http://127.0.0.1:<PORT>，端口单独参数化。
- 待验路由默认 /login。
- 视口默认 1440x900，传给 --window-size。
- 超时默认 10 秒，为等待上限。
- 证据目录默认 qa-out，存放截图与 report.json。
- 浏览器默认 edge，可选 edge 或 chrome。
- 驱动路径默认空即自动，离线时传 Service(executable_path=...)。
- 账号只读环境变量 TEST_EMAIL / TEST_PASSWORD。

## 约束 / 反例

- 前置条件缺一项先补齐：站点可访问且 BASE_URL 与 PORT 已确认。
- 前置条件：确认 selenium 版本为 4.44.0。
- 前置条件：本机有 Edge 或 Chrome，驱动由 Selenium Manager 自动解析。
- 账号只从环境变量读，脚本、日志、截图都不出现明文密码。
- ref 只在当前文档有效，导航、重渲染或切 iframe 后要重新 snapshot。
- 反模式：用 time.sleep 顶等待，应统一 WebDriverWait。
- 反模式：硬编码快照序号 @e3，页面一改就错，应改用文案定位。
- 反模式：复用 driver 跨用例，登录态与 localStorage 互相污染，每用例新建或清 cookie。
- 坑：headless 拍到白屏，因未等 readyState 完成与关键元素可见。
- 坑：点击没反应，元素在视口外或被浮层遮住；先 scrollIntoView 再 JS 兜底，并查 pointer-events。
- 坑：StaleElementReferenceException，DOM 重渲染后旧引用失效，动作前重新定位。
- 坑：iframe 内定位不到，用 switch_to.frame 进入，结束用 default_content 回来。
- 坑：无法自动化系统文件对话框，input[type=file] 直接 send_keys 绝对路径。
- 坑：get_log("browser") 为空，日志能力须启动前设，Edge 用 ms:loggingPrefs、Chrome 用 goog: 前缀，写错静默失效。

## 示例

（原文没有这一节，或没有能通过引用校验的条目）

## 未通过校验（已丢弃）

- quote 在原文里找不到（可能是模型改写或编造）：交互一律走显式等待，点击前先 scrollIntoView，被浮层遮挡时用 JS 点击兜底。
- quote 在原文里找不到（可能是模型改写或编造）：示例流程：打开路由、打印元素清单、填账号密码、提交、断言仪表盘可见与标题文案、整页截图、采集报告并断言无 JS 报错，finally 退出。
