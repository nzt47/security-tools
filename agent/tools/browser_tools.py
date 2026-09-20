"""无头浏览器控制工具——从 system_tools.py 拆出

包含：浏览器实例管理、导航、截图、关闭等操作。
"""
import logging

logger = logging.getLogger(__name__)

_browser_instance = None

# 浏览器配置参数（可注入）
_browser_config = {
    "headless": True,
    "no_sandbox": True,
    "disable_dev_shm": True,
    "disable_gpu": True,
    "disable_extensions": True,
    "disable_file_system": True,
    "remote_debugging_port": 0,
    "page_load_timeout": 15,
}


def set_browser_config(**kwargs):
    """设置浏览器配置参数"""
    global _browser_config
    _browser_config.update(kwargs)


def get_browser(webdriver_module=None):
    """获取或创建无头浏览器实例（懒加载）

    Args:
        webdriver_module: 可选的 webdriver 模块，用于测试时注入 Mock 对象

    Returns:
        浏览器实例或 None
    """
    global _browser_instance
    if _browser_instance is None:
        try:
            # 如果传入了 webdriver 模块，则使用它（用于测试）
            if webdriver_module is not None:
                wd = webdriver_module
            else:
                from selenium import webdriver
                wd = webdriver

            from selenium.webdriver.chrome.options import Options
            opts = Options()
            opts.add_argument("--headless=new")
            opts.add_argument("--no-sandbox")
            opts.add_argument("--disable-dev-shm-usage")
            opts.add_argument("--disable-gpu")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-file-system")
            opts.add_argument("--remote-debugging-port=0")
            _browser_instance = wd.Chrome(options=opts)
            logger.debug(f"Chrome浏览器实例创建成功，对象ID: {id(_browser_instance)}")

            # 关键修复: 后续配置失败时必须清理 _browser_instance，
            # 避免下次调用 get_browser 时返回部分初始化的实例。
            try:
                page_load_timeout = _browser_config.get("page_load_timeout", 15)
                _browser_instance.set_page_load_timeout(page_load_timeout)
                logger.info(f"页面加载超时时间设置为 {page_load_timeout} 秒")
            except Exception as timeout_e:
                logger.warning(f"设置页面加载超时失败: {timeout_e}")
                _cleanup_browser_instance()
                return None

            try:
                window_handles = _browser_instance.window_handles
                logger.debug(f"浏览器窗口句柄: {window_handles}")
            except Exception as handle_e:
                logger.debug(f"获取窗口句柄失败: {handle_e}")

            logger.info("无头浏览器已成功启动")
        except ImportError:
            logger.warning("selenium 未安装，浏览器功能不可用")
            return None
        except Exception as e:
            logger.warning(f"无头浏览器启动失败: {e}")
            # 任何启动失败均清理 _browser_instance，防止状态泄漏
            _cleanup_browser_instance()
            return None
    return _browser_instance


def _cleanup_browser_instance():
    """清理浏览器实例：尝试 quit, 然后将全局变量重置为 None。

    用于 get_browser 部分初始化失败时释放资源，避免下次调用返回
    已损坏/部分初始化的实例。
    """
    global _browser_instance
    if _browser_instance is not None:
        try:
            _browser_instance.quit()
        except Exception:
            # quit 失败不应阻止清理流程
            pass
        _browser_instance = None


def browser_navigate(url):
    """导航到指定 URL（仅允许 http/https；目标地址走**同一套 SSRF 判定**）

    【TASK-07 第 2 步第 6 项：删除浏览器侧的第二份黑名单（D1）】
      原实现自带一份**子串黑名单**：
          ["localhost", "127.0.0.1", "0.0.0.0", "::1", "192.168.", "10.", "172.16."]
      它漏掉了 TASK-07 §2.1 点名的关键目标：`169.254.169.254`（云元数据）、
      `172.17`–`172.31`（Docker 默认桥接网段）、十进制/十六进制 IP 写法，
      而且与 `agent/policy/egress.py` 的分类器**口径不一致**（同一目标两处两判）。
      现统一调用 `agent.guardrails.ssrf_guard.check_url()` —— 一处判定、全站复用。
    """
    # 协议检查放在最前：**与 SSRF 守卫同一结论**（都要求 http/https），
    # 但先做可以保留本接口既有的稳定文案"仅允许 http/https 协议"（D2：
    # 补精度不破坏契约），同时让非 http(s) 输入不必走到 DNS 解析。
    if not str(url).startswith(("http://", "https://")):
        return {"ok": False, "error": "仅允许 http/https 协议"}

    verdict = None
    try:
        from agent.guardrails.ssrf_guard import audit_block, check_url
        verdict = check_url(url)
    except Exception as exc:  # noqa: BLE001  守卫不可用 ⇒ fail-closed（E3）
        logger.error("[browser] SSRF 守卫不可用 ⇒ 拒绝导航: %s", exc)
        return {"ok": False, "error": f"SSRF 守卫不可用，拒绝导航: {exc}", "blocked": True}
    if not verdict.allowed:
        audit_block(verdict, url=url, surface="tools.browser_tools.navigate")
        # 【为什么保留"禁止访问内网地址"这条前缀】它是本接口此前的**稳定对外文案**
        # （既有用例/上层按它识别"内网拦截"）。现在把精确原因（哪个网段、什么写法）
        # 附在后面，语义不丢、信息更全 —— 属于"补精度而不破坏契约"（D2）。
        message = (f"禁止访问内网地址：{verdict.reason}" if verdict.is_internal
                   else verdict.reason)
        return {"ok": False, "error": message, "blocked": True,
                "blocked_by": "guardrails.ssrf_guard", "ssrf_category": verdict.category}

    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用（需要安装 selenium）"}
    try:
        browser.get(url)
        # ── 落地后复检（TASK-07 第 2 步第 3 项的浏览器侧等价物）────────────
        # 【为什么必须补这一步】`browser.get(url)` 会**自行跟随重定向**，而重定向
        # 目标从不经过上面的判定 ⇒ "公网页面 302 到 http://169.254.169.254/" 这条链
        # 在浏览器路径上结构性可绕。这里在页面已经落地之后，用 `current_url`
        # 复检一次：若最终地址是内网，**不把页面内容交出去**（内容才是敏感面），
        # 并如实报告是被拦而不是"导航失败"。
        try:
            from agent.guardrails.ssrf_guard import audit_block as _audit, check_url as _check
            landed = str(getattr(browser, "current_url", "") or "")
            if landed:
                landed_verdict = _check(landed)
                if not landed_verdict.allowed:
                    _audit(landed_verdict, url=landed,
                           surface="tools.browser_tools.navigate.landed")
                    return {
                        "ok": False,
                        "error": (f"导航落地地址被拒绝（重定向到了内网）："
                                  f"{landed_verdict.reason}"),
                        "blocked": True,
                        "blocked_by": "guardrails.ssrf_guard",
                        "ssrf_stage": "post_navigation",
                        "requested_url": url,
                        "landed_host": landed_verdict.host,
                    }
        except Exception as exc:  # noqa: BLE001  复检自身故障不得伪造"安全"
            logger.warning("[browser] 落地地址复检失败（按拒绝处理，不交出页面内容）: %s", exc)
            return {"ok": False, "error": f"落地地址复检失败，拒绝返回页面内容: {exc}",
                    "blocked": True, "blocked_by": "guardrails.ssrf_guard"}
        title = browser.title
        text = browser.find_element("tag name", "body").text[:5000]
        return {"ok": True, "title": title, "url": browser.current_url, "text": text}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_screenshot():
    """截取当前页面截图（返回 base64）"""
    import base64
    browser = get_browser()
    if not browser:
        return {"ok": False, "error": "浏览器不可用"}
    try:
        screenshot = browser.get_screenshot_as_base64()
        return {"ok": True, "screenshot_base64": screenshot[:500000]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def browser_close():
    """关闭浏览器"""
    global _browser_instance
    if _browser_instance:
        try:
            _browser_instance.quit()
        except Exception:
            pass
        _browser_instance = None


__all__ = [
    "set_browser_config", "get_browser", "_cleanup_browser_instance",
    "browser_navigate", "browser_screenshot", "browser_close",
]
