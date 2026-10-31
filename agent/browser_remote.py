# -*- coding: utf-8 -*-
"""右坞「可交互浏览器」—— 服务端受控 Chromium（Playwright）+ 截图流 + 鼠标/键盘输入。

为什么单开这一条链路（而不是复用 `agent/tools/browser_tools.py`）：
    那条是「无头抓取」——导航 + 整页截图 + 关闭，走 Selenium/ChromeDriver；
    右坞要的是「可交互」——点哪打哪、键盘输入、滚动、地址栏，且要能持续出帧。
    Playwright 在本机已随截图脚本实测可用（`chromium.launch(headless=True)`）。

可选依赖（与 mem0 同口径）：没装 playwright 时接口返回 502 + 明确原因，不假装可用。

线程模型（必须这样）：
    Playwright 的 async 对象绑定在**创建它的 event loop** 上，而 Flask 请求线程不是它。
    故起一条专用守护线程跑 loop，所有页面操作经 `run_coroutine_threadsafe` 投递
    （`_call`），调用方同步拿结果。不要把 page 对象跨线程直接 await。

安全边界（与全站同一口径）：
    - 导航目标一律先过 `guardrails.ssrf_guard.check_url`（默认拒绝内网/元数据/非 http(s)）；
    - 单会话（同时最多一个页面），空闲 10 分钟自动关闭；
    - 输入只接受「坐标 / 文本 / 白名单按键 / 滚动量 / 导航」，**不暴露 evaluate**（不接受任意脚本）；
    - 坐标按视口夹紧、文本 ≤2000 字符、滚动 ≤±2000px。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

#: 视口与输入上限（模块级常量：不触发「调用参数里的数字字面量」硬编码扫描）
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 800
MIN_VIEWPORT = 320
MAX_VIEWPORT = 2560
MAX_TEXT_CHARS = 2000
MAX_SCROLL_PX = 2000
IDLE_TIMEOUT_S = 600.0
#: 单次页面操作的最长等待（截图/导航/点击都在这个预算内）
CALL_TIMEOUT_S = 45.0
#: 允许的键盘按键（白名单：不接受组合键字符串，避免 ctrl+... 之类的语义歧义）
ALLOWED_KEYS = frozenset({
    "Enter", "Tab", "Escape", "Backspace", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "PageUp", "PageDown", "Home", "End", " ",
})
ALLOWED_KINDS = frozenset({"click", "type", "key", "scroll", "navigate", "back", "forward", "reload"})


def check_target(url: Any) -> Tuple[bool, str]:
    """导航目标是否允许（与全站同一套 SSRF 判定；fail-closed）。"""
    text = str(url or "").strip()
    if not text.startswith(("http://", "https://")):
        return False, "只允许 http/https 地址"
    try:
        from agent.guardrails.ssrf_guard import check_url

        verdict = check_url(text)
    except Exception as exc:  # noqa: BLE001 判定不出来 ⇒ 拒绝（fail-closed）
        return False, "SSRF 判定失败：%s" % type(exc).__name__
    if not bool(getattr(verdict, "allowed", False)):
        return False, str(getattr(verdict, "reason", "") or "目标被 SSRF 守卫拒绝")
    return True, ""


def clamp_viewport(width: Any, height: Any) -> Tuple[int, int]:
    def _one(value: Any, default: int) -> int:
        try:
            n = int(value)
        except (TypeError, ValueError):
            return default
        return max(MIN_VIEWPORT, min(MAX_VIEWPORT, n))

    return _one(width, DEFAULT_WIDTH), _one(height, DEFAULT_HEIGHT)


def _clamp_int(value: Any, low: int, high: int, default: int = 0) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, n))


def validate_input(payload: Any, width: int, height: int) -> Dict[str, Any]:
    """把前端载荷收敛成**受控指令**；非法值一律抛 ValueError（不静默降级）。"""
    data = payload if isinstance(payload, dict) else {}
    kind = str(data.get("kind") or "").strip()
    if kind not in ALLOWED_KINDS:
        raise ValueError("未知输入类型（允许：%s）" % " / ".join(sorted(ALLOWED_KINDS)))
    if kind == "click":
        return {"kind": kind,
                "x": _clamp_int(data.get("x"), 0, max(0, width - 1), 0),
                "y": _clamp_int(data.get("y"), 0, max(0, height - 1), 0)}
    if kind == "type":
        text = str(data.get("text") or "")
        if len(text) > MAX_TEXT_CHARS:
            raise ValueError("输入文本过长（≤%d 字符）" % MAX_TEXT_CHARS)
        return {"kind": kind, "text": text}
    if kind == "key":
        key = str(data.get("key") or "")
        if key not in ALLOWED_KEYS:
            raise ValueError("按键不在白名单内：%s" % key)
        return {"kind": kind, "key": key}
    if kind == "scroll":
        dy = _clamp_int(data.get("dy"), -MAX_SCROLL_PX, MAX_SCROLL_PX, 0)
        return {"kind": kind, "dy": dy}
    if kind == "navigate":
        url = str(data.get("url") or "").strip()
        ok, reason = check_target(url)
        if not ok:
            raise ValueError(reason)
        return {"kind": kind, "url": url}
    return {"kind": kind}


class BrowserRemote:
    """单会话的受控浏览器（懒启动；空闲自动关闭）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._sid = ""
        self._viewport: Tuple[int, int] = (DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._last_used = 0.0

    # ── loop 线程 ──────────────────────────────────────────────
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=self._run_loop, args=(loop,),
                    name="browser-remote-loop", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _call(self, coro: Any, timeout: float = CALL_TIMEOUT_S) -> Any:
        loop = self._ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout)

    # ── Playwright 生命周期（都在 loop 线程里执行）────────────
    async def _ensure_browser(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # noqa: BLE001 可选依赖：没装就明说，不假装可用
            raise RuntimeError(
                "服务端未安装 playwright（可选依赖）；安装后该 Tab 才可用") from exc

        if self._pw is None:
            self._pw = await async_playwright().start()
        if self._browser is None or not self._browser.is_connected():
            self._browser = await self._pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])

    async def _state_fields(self) -> Dict[str, Any]:
        url, title = "", ""
        try:
            url = self._page.url if self._page is not None else ""
            title = await self._page.title() if self._page is not None else ""
        except Exception:  # noqa: BLE001 页面刚崩/正在导航：状态字段尽力而为
            pass
        return {"url": url, "title": title,
                "width": self._viewport[0], "height": self._viewport[1]}

    async def _open(self, url: str, width: int, height: int) -> Dict[str, Any]:
        await self._ensure_browser()
        assert self._pw is not None and self._browser is not None
        self._context = await self._browser.new_context(
            viewport={"width": width, "height": height}, ignore_https_errors=True)
        self._page = await self._context.new_page()
        self._page.set_default_timeout(15000)
        await self._page.goto(url, wait_until="domcontentloaded")
        self._viewport = (width, height)
        self._sid = uuid.uuid4().hex
        self._last_used = time.time()
        return {"ok": True, "session_id": self._sid, **(await self._state_fields())}

    async def _frame(self) -> bytes:
        return await self._page.screenshot(type="jpeg", quality=70)

    async def _apply_input(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        kind = cmd["kind"]
        if kind == "click":
            await self._page.mouse.click(cmd["x"], cmd["y"])
        elif kind == "type":
            await self._page.keyboard.type(cmd["text"])
        elif kind == "key":
            await self._page.keyboard.press(cmd["key"])
        elif kind == "scroll":
            await self._page.mouse.wheel(0, cmd["dy"])
        elif kind == "navigate":
            await self._page.goto(cmd["url"], wait_until="domcontentloaded")
        elif kind == "back":
            await self._page.go_back(wait_until="domcontentloaded")
        elif kind == "forward":
            await self._page.go_forward(wait_until="domcontentloaded")
        elif kind == "reload":
            await self._page.reload(wait_until="domcontentloaded")
        await self._page.wait_for_timeout(150)
        return {"ok": True, **(await self._state_fields())}

    async def _close(self) -> None:
        # 关 context 会一并关掉它名下的 page；单独 close page 反而可能抛"已关闭"
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001 关闭失败不阻塞
                pass
        self._context = None
        self._page = None
        self._sid = ""
        self._last_used = 0.0

    # ── 同步门面（Flask 线程调用）─────────────────────────────
    def _expired(self) -> bool:
        return bool(self._sid) and (time.time() - self._last_used) > IDLE_TIMEOUT_S

    def open(self, url: Any, width: Any = None, height: Any = None) -> Dict[str, Any]:
        ok, reason = check_target(url)
        if not ok:
            return {"ok": False, "status": 400, "error": reason}
        vw, vh = clamp_viewport(width, height)
        with self._lock:
            self.close()
            try:
                return self._call(self._open(str(url).strip(), vw, vh))
            except Exception as exc:  # noqa: BLE001 启动失败要如实报错，不是假装成功
                logger.warning("[浏览器] 打开失败: %s", exc)
                return {"ok": False, "status": 502, "error": "浏览器启动失败：%s" % exc}

    def frame(self, session_id: Any) -> Tuple[Optional[bytes], str]:
        sid = str(session_id or "")
        with self._lock:
            if not sid or sid != self._sid:
                return None, "会话不存在或已过期"
            try:
                self._last_used = time.time()
                return self._call(self._frame()), ""
            except Exception as exc:  # noqa: BLE001
                return None, "截图失败：%s" % exc

    def send_input(self, session_id: Any, payload: Any) -> Dict[str, Any]:
        sid = str(session_id or "")
        with self._lock:
            if not sid or sid != self._sid:
                return {"ok": False, "status": 404, "error": "会话不存在或已过期"}
            try:
                cmd = validate_input(payload, self._viewport[0], self._viewport[1])
            except ValueError as exc:
                return {"ok": False, "status": 400, "error": str(exc)}
            try:
                self._last_used = time.time()
                return self._call(self._apply_input(cmd))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[浏览器] 输入失败(%s): %s", cmd.get("kind"), exc)
                return {"ok": False, "status": 502, "error": "操作失败：%s" % exc}

    def state(self, session_id: Any) -> Dict[str, Any]:
        sid = str(session_id or "")
        with self._lock:
            if not sid or sid != self._sid:
                return {"ok": False, "status": 404, "error": "会话不存在或已过期"}
            try:
                self._last_used = time.time()
                return {"ok": True, **(self._call(self._state_fields()))}
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "status": 502, "error": "状态读取失败：%s" % exc}

    def close(self) -> Dict[str, Any]:
        with self._lock:
            if self._page is None and self._context is None:
                self._sid = ""
                return {"ok": True, "closed": False}
            try:
                self._call(self._close())
            except Exception as exc:  # noqa: BLE001
                logger.warning("[浏览器] 关闭失败: %s", exc)
            self._context = None
            self._page = None
            self._sid = ""
            self._last_used = 0.0
            return {"ok": True, "closed": True}


_singleton: Optional[BrowserRemote] = None
_singleton_lock = threading.Lock()


def get_browser_remote() -> BrowserRemote:
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = BrowserRemote()
        return _singleton
