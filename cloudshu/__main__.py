"""`python -m cloudshu` 入口

一行转发：真正的实现（与参数解析、JSON 契约）都在 `cloudshu/cli.py`。
之所以单独一个 `__main__.py`：`python -m <pkg>` 要求包内有 `__main__.py`，
而把实现留在 `cli.py` 便于测试直接 `from cloudshu.cli import main` 调用，
不必经子进程（本沙箱下 `subprocess` 的管道捕获有额外坑，见 `TASK-00` §0.2d 第 4 类）。
"""

from __future__ import annotations

import sys

from cloudshu.cli import main

if __name__ == "__main__":
    sys.exit(main())
