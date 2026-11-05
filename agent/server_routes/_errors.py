"""路由层「安全错误文案」的唯一出口（py/stack-trace-exposure）。

【为什么要有这个模块（而不是每个路由各写一份）】
    `py/stack-trace-exposure` 的判据是**数据流**：异常对象（或其 `str()` / `args`）
    一旦进入响应体就算泄漏 —— 哪怕只是 `type(e).__name__`。而 `except Exception as e: return jsonify(..., str(e))`
    这个写法在本仓的 30 多个路由文件里出现了 70+ 次，逐处改很容易漏、也很容易改出两种口径。

    故收敛成一处：**原文只进日志，响应只回固定文案**（与 `agent/api_envelope.py` 的 500 detail 同一句话）。
    已知的领域错误（`XxxError`）仍应走自定义属性 `exc.message`（见 routes_skills_mgmt），
    因为那种文案是**我们自己写的**、对调用方有意义；本模块只服务"意外异常"。

【不易】返回值必须是**模块级常量**：不要在 helper 里做任何字符串插值（那会把 taint 带回来）。
【变易】文案是数据，`agent/api_envelope.py` 的 500 detail 与它保持同一句。
【简易】`safe_error(exc, logger)` —— 两个位置参数都是可选的。
"""
from __future__ import annotations

import logging
from typing import Optional

#: 与 `agent/api_envelope.py::_DEFAULT_DETAIL[500]` 同一句话
INTERNAL_ERROR_MESSAGE = "服务器内部错误，请稍后重试或查看服务端日志。"

_logger = logging.getLogger(__name__)


def safe_error(exc: Optional[BaseException] = None, logger: Optional[logging.Logger] = None,
               where: str = "") -> str:
    """记日志 + 回固定文案。

    Args:
        exc: 捕获到的异常（**只用于日志**，不参与返回值）
        logger: 调用方模块的 logger（缺省用本模块的）
        where: 便于检索的动作名（如 "knowledge.search"），只进日志
    """
    target = logger if logger is not None else _logger
    try:
        target.warning("[api] %s 失败: %s", where or "请求", exc)
    except Exception:  # noqa: BLE001 日志本身失败不能影响响应
        pass
    return INTERNAL_ERROR_MESSAGE
