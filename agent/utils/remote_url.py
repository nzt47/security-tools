# -*- coding: utf-8 -*-
"""远端地址 → owner/repo（只认主机名等值，不做 URL 子串判断）。

【为什么单独一个叶子模块】这段解析被两个脚本共用（`configure_branch_protection.py`
与 `gen_perf_trend.py`）。放在任一脚本里都不合适：
  - 放 `gen_perf_trend.py` 会被它顶部 `import matplotlib` 拖累（CI 的单元测试分片没装
    matplotlib，测试导入即 collection error，2026-11-07 实测 #1490 shard3 红）；
  - 放 `configure_branch_protection.py` 则让另一个脚本反向依赖"配分支保护"这种一次性动作。

【口径】CodeQL `py/incomplete-url-substring-sanitization`：不能用
`"github.com" in url` 之类的子串判断归属 —— 子串也会命中
`https://github.com.evil.tld/o/r`、`https://github.com@evil.tld/o/r`、
`https://x.tld/?github.com/o/r`。这里一律先解析出主机名再比等值。

支持的写法：`https://host/owner/repo(.git)`、`ssh://git@host/owner/repo(.git)`、
以及 scp 风格 `git@host:owner/repo(.git)`（后者不是 URL，`urlsplit` 解不了）。
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

#: 本项目只对 GitHub 远端生效；判据是**主机名等值**，不是 URL 子串
GITHUB_HOST = "github.com"


def remote_repo_slug(url: str, *, host: str = GITHUB_HOST) -> Optional[str]:
    """把 git 远端地址解析成 `owner/repo`；主机名不匹配或形状不对时返回 None。"""
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.startswith("git@"):
        # scp 风格：git@github.com:owner/repo.git（**不是** URL，urlsplit 解不了）
        rest = raw.split("@", 1)[1]
        raw_host, _, path = rest.partition(":")
        raw_host = raw_host.split("/", 1)[0]
    else:
        # https://… 与 ssh://git@host/… 都是标准 URL，交给 urlsplit
        parts = urlsplit(raw)
        raw_host = parts.hostname or ""
        path = (parts.path or "").lstrip("/")
    if (raw_host or "").lower() != host:
        return None
    slug = path.replace(".git", "").strip("/")
    return slug if "/" in slug else None
