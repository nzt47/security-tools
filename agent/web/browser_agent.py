"""
浏览器自动化代理 — 动态渲染、JS 执行、表单交互、截图、PDF

基于 Selenium WebDriver，管理完整的浏览器实例。
"""

import base64
import json
import logging
import os
import time
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class BrowserAgent:
    """浏览器自动化 — 云枢的"眼睛"和"手指"

    功能：
    - 页面导航与动态渲染（JS 完全执行）
    - 页面截图 / PDF 导出
    - 表单自动填写与提交
    - JavaScript 注入执行
    - HTML / 文本内容提取
    - 等待元素 / 滚动 / 点击
    """

    def __init__(self, config: Optional[dict] = None):
        p = config or {}

        # 浏览器窗口配置
        self._window_width = p.get("window_width", 1280)
        self._window_height = p.get("window_height", 800)
        self._page_load_timeout = p.get("page_load_timeout", 30)
        self._implicit_wait = p.get("implicit_wait", 10)
        self._headless = p.get("headless", True)
        self._user_data_dir = p.get("user_data_dir", "")
        self._chrome_path = p.get("chrome_path", "")

        # Chrome 选项字符串（额外参数）
        self._extra_args = p.get("extra_args", [])

        self._driver = None
        self._stats = {
            "pages_visited": 0,
            "screenshots_taken": 0,
            "actions_performed": 0,
            "errors": 0,
            "started_at": None,
        }
        
        # 延迟导入避免循环依赖
        from agent.error_handler import (
            with_retry,
            NetworkTimeoutError
        )
        self._navigate_with_retry = with_retry(
            max_retries=2,
            initial_delay=1.0,
            strategy="linear",
            retryable_exceptions=(NetworkTimeoutError,),
            error_counter="browser.navigate"
        )(self._do_navigate)
        
        logger.info("BrowserAgent 已初始化 (headless=%s)", self._headless)
    
    def _do_navigate(self, url: str, wait_seconds: float = 2.0) -> dict:
        """实际的导航逻辑（不含重试，失败抛出异常）"""
        result = self.navigate(url, wait_seconds=wait_seconds)
        if not result.get("ok"):
            raise NetworkTimeoutError(f"导航失败: {result.get('error', '未知错误')}")
        return result

    # ── 浏览器生命周期 ────────────────────────────────────────────

    def _ensure_browser(self):
        """确保浏览器实例已启动（懒加载）"""
        if self._driver is None:
            self._start_browser()

    def _start_browser(self):
        """启动浏览器实例"""
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            opts = Options()

            # 基本配置
            if self._headless:
                opts.add_argument("--headless=new")
            opts.add_argument(f"--window-size={self._window_width},{self._window_height}")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-popup-blocking")
            opts.add_argument("--disable-notifications")
            opts.add_argument("--disable-infobars")
            opts.add_argument("--mute-audio")
            opts.add_argument("--lang=zh-CN")

            # 用户数据目录
            if self._user_data_dir:
                opts.add_argument(f"--user-data-dir={self._user_data_dir}")

            # 自定义参数
            for arg in self._extra_args:
                opts.add_argument(arg)

            # 修改指纹避免检测
            opts.add_argument("--disable-blink-features=AutomationControlled")
            opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            opts.add_experimental_option("useAutomationExtension", False)

            # 二进制路径
            if self._chrome_path:
                opts.binary_location = self._chrome_path

            self._driver = webdriver.Chrome(options=opts)
            self._driver.set_page_load_timeout(self._page_load_timeout)
            self._driver.implicitly_wait(self._implicit_wait)

            # 隐藏 webdriver 特征
            self._driver.execute_cdp_cmd(
                "Network.setUserAgentOverride",
                {
                    "userAgent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/125.0.0.0 Safari/537.36"
                    )
                },
            )
            self._driver.execute_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            self._stats["started_at"] = time.time()
            logger.info("浏览器实例已启动 (headless=%s)", self._headless)

        except Exception as e:
            self._stats["errors"] += 1
            logger.error("启动浏览器失败: %s", e)
            raise

    # ── 页面导航 ──────────────────────────────────────────────────

    def navigate(self, url: str, wait_seconds: float = 2.0) -> dict:
        """导航到指定 URL

        Args:
            url: 目标 URL
            wait_seconds: 加载后等待秒数（确保 JS 渲染完成）

        Returns:
            dict: {ok, url, title, text, html, status, ...}
        """
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "error": "仅支持 http/https 协议"}

        try:
            self._ensure_browser()
            self._driver.get(url)
            time.sleep(wait_seconds)

            self._stats["pages_visited"] += 1

            return {
                "ok": True,
                "url": self._driver.current_url,
                "title": self._driver.title,
                "text": self._get_page_text(),
                "html": self._driver.page_source[:500000],  # 限 500KB
                "status": "loaded",
            }
        except Exception as e:
            self._stats["errors"] += 1
            logger.error("导航失败 %s: %s", url, e)
            return {"ok": False, "error": f"导航失败: {e}"}

    def navigate_with_retry(self, url: str, max_retries: int = 2, wait_seconds: float = 2.0) -> dict:
        """带重试的页面导航"""
        try:
            return self._navigate_with_retry(url, wait_seconds=wait_seconds)
        except Exception as e:
            return {"ok": False, "error": f"导航失败: {e}"}

    # ── 截图与 PDF ────────────────────────────────────────────────

    def screenshot(self, filepath: Optional[str] = None, full_page: bool = False) -> dict:
        """页面截图

        Args:
            filepath: 保存路径（不传则返回 base64）
            full_page: 是否全页面截图

        Returns:
            dict: {ok, filepath, data_base64, size, ...}
        """
        try:
            self._ensure_browser()

            if full_page:
                # 全页面截图
                original_height = self._driver.execute_script(
                    "return document.body.scrollHeight"
                )
                self._driver.set_window_size(self._window_width, original_height)
                time.sleep(0.5)

            if filepath:
                self._driver.save_screenshot(filepath)
                self._stats["screenshots_taken"] += 1
                return {
                    "ok": True,
                    "filepath": filepath,
                    "size": os.path.getsize(filepath) if os.path.exists(filepath) else 0,
                }
            else:
                png_data = self._driver.get_screenshot_as_png()
                self._stats["screenshots_taken"] += 1
                return {
                    "ok": True,
                    "data_base64": base64.b64encode(png_data).decode("utf-8"),
                    "size": len(png_data),
                }
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": f"截图失败: {e}"}

    def pdf(self, filepath: str) -> dict:
        """导出页面为 PDF

        Args:
            filepath: PDF 保存路径

        Returns:
            dict: {ok, filepath, page_count, ...}
        """
        try:
            self._ensure_browser()
            pdf_data = self._driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "paperWidth": 8.27,
                "paperHeight": 11.69,
                "marginTop": 0.4,
                "marginBottom": 0.4,
            })

            pdf_bytes = base64.b64decode(pdf_data["data"])
            os.makedirs(os.path.dirname(os.path.abspath(filepath)) or ".", exist_ok=True)
            with open(filepath, "wb") as f:
                f.write(pdf_bytes)

            return {
                "ok": True,
                "filepath": filepath,
                "size": len(pdf_bytes),
                "page_count": pdf_data.get("numberOfPages", 1),
            }
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": f"PDF 导出失败: {e}"}

    # ── 页面交互 ──────────────────────────────────────────────────

    def execute_script(self, script: str, *args) -> dict:
        """执行 JavaScript

        Args:
            script: JS 代码
            *args: 传递给 JS 的参数

        Returns:
            dict: {ok, result, error}
        """
        try:
            self._ensure_browser()
            result = self._driver.execute_script(script, *args)
            self._stats["actions_performed"] += 1
            return {"ok": True, "result": str(result)[:10000]}
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": str(e)}

    def click(self, selector: str, by: str = "css") -> dict:
        """点击元素

        Args:
            selector: CSS 选择器或 XPath
            by: 选择器类型（css / xpath）

        Returns:
            dict: {ok, tag, text, error}
        """
        try:
            self._ensure_browser()
            from selenium.webdriver.common.by import By

            by_map = {"css": By.CSS_SELECTOR, "xpath": By.XPATH}
            element = self._driver.find_element(by_map.get(by, By.CSS_SELECTOR), selector)
            tag = element.tag_name
            text = (element.text or "")[:100]
            element.click()
            self._stats["actions_performed"] += 1
            return {"ok": True, "tag": tag, "text": text}
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": f"点击失败: {e}"}

    def fill_form(self, fields: Dict[str, str], submit: bool = False) -> dict:
        """填写表单并可选提交

        Args:
            fields: 字段映射表 {选择器: 值}
            submit: 是否提交表单

        Returns:
            dict: {ok, filled_count, submitted, error}
        """
        from selenium.webdriver.common.by import By

        filled = 0
        try:
            self._ensure_browser()
            for selector, value in fields.items():
                try:
                    element = self._driver.find_element(By.CSS_SELECTOR, selector)
                    element.clear()
                    element.send_keys(value)
                    filled += 1
                except Exception as e:
                    logger.warning("填写字段 %s 失败: %s", selector, e)

            submitted = False
            if submit and filled > 0:
                try:
                    submit_btn = self._driver.find_element(By.CSS_SELECTOR, "input[type=submit], button[type=submit]")
                    submit_btn.click()
                    submitted = True
                except Exception:
                    # 尝试按 Enter 键
                    from selenium.webdriver.common.keys import Keys
                    self._driver.switch_to.active_element.send_keys(Keys.RETURN)
                    submitted = True

            self._stats["actions_performed"] += 1
            return {"ok": True, "filled_count": filled, "submitted": submitted}
        except Exception as e:
            self._stats["errors"] += 1
            return {"ok": False, "error": f"表单填写失败: {e}"}

    def scroll(self, direction: str = "down", amount: int = 500) -> dict:
        """滚动页面

        Args:
            direction: up / down
            amount: 像素数
        """
        sign = 1 if direction == "down" else -1
        try:
            self._ensure_browser()
            self._driver.execute_script(f"window.scrollBy(0, {sign * amount})")
            self._stats["actions_performed"] += 1
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def wait_for_element(self, selector: str, timeout: int = 10) -> dict:
        """等待元素出现

        Args:
            selector: CSS 选择器
            timeout: 超时秒数
        """
        try:
            self._ensure_browser()
            from selenium.webdriver.common.by import By
            from selenium.webdriver.support.ui import WebDriverWait
            from selenium.webdriver.support import expected_conditions as EC

            element = WebDriverWait(self._driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, selector))
            )
            return {"ok": True, "tag": element.tag_name, "text": (element.text or "")[:200]}
        except Exception as e:
            return {"ok": False, "error": f"等待元素超时: {e}"}

    # ── 内容提取 ──────────────────────────────────────────────────

    def _get_page_text(self) -> str:
        """获取页面纯文本"""
        try:
            from selenium.webdriver.support.ui import WebDriverWait
            WebDriverWait(self._driver, 5).until(
                lambda d: d.execute_script("return document.readyState") == "complete"
            )
            body = self._driver.find_element("tag name", "body")
            return (body.text or "")[:100000]
        except Exception:
            try:
                return self._driver.execute_script("return document.body.innerText")[:100000] or ""
            except Exception:
                return ""

    def get_page_info(self) -> dict:
        """获取当前页面信息"""
        try:
            self._ensure_browser()
            return {
                "ok": True,
                "url": self._driver.current_url,
                "title": self._driver.title,
                "window_handle": self._driver.current_window_handle,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_html(self) -> dict:
        """获取当前页面 HTML 源码"""
        try:
            self._ensure_browser()
            return {"ok": True, "html": self._driver.page_source[:1000000]}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def get_cookies(self) -> list:
        """获取浏览器 Cookie"""
        try:
            self._ensure_browser()
            return [{"name": c["name"], "value": c["value"], "domain": c.get("domain", "")}
                    for c in self._driver.get_cookies()]
        except Exception:
            return []

    # ── 浏览器生命周期管理 ────────────────────────────────────────

    def close(self):
        """关闭浏览器"""
        if self._driver is not None:
            try:
                self._driver.quit()
            except Exception:
                pass
            self._driver = None
            logger.info("浏览器已关闭")

    def restart(self):
        """重启浏览器"""
        self.close()
        time.sleep(1)
        self._start_browser()

    def get_stats(self) -> dict:
        """获取浏览器操作统计"""
        return {
            **self._stats,
            "is_running": self._driver is not None,
        }

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
