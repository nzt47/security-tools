# -*- coding: utf-8 -*-
"""URL 判源的"主机名等值"口径（2026-11-07 C-10 第一批：py/incomplete-url-substring-sanitization）。

【为什么单开一条】CodeQL 这一族告警的教训是同一条：**别用子串判断 URL 的归属**。
  - `"github.com" in remote_url` 也会命中 `https://github.com.evil.tld/o/r`；
  - `"hooks.slack.com" in webhook_url` 也会命中 `https://attacker.tld/?hooks.slack.com`；
  - `"example.com" in url` 同理（路径/查询串里都能出现）。

下面把三处"解析成主机名再比等值"的实现钉住（含 scp 风格 git 地址这种非 URL 形式）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.utils.remote_url import remote_repo_slug
from scripts.configure_branch_protection import _remote_repo_slug
from scripts.notify_arch_violation import detect_webhook_type
# 【为什么不 import scripts.gen_perf_trend】它顶部 import matplotlib，
# CI 的单元测试分片没装该依赖 ⇒ 测试导入即 collection error（2026-11-07 实测 shard3 红）。
# 该脚本的 _repo_slug_from_remote 现在只是这个叶子函数的转发，故这里直接测叶子实现。


class TestGitRemoteSlug:
    """git 远端 → owner/repo（https / scp / ssh 三种写法）"""

    @pytest.mark.parametrize("url", [
        "https://github.com/o/r.git",
        "https://github.com/o/r",
        "git@github.com:o/r.git",
        "ssh://git@github.com/o/r.git",
    ])
    def test_接受的写法(self, url):
        assert _remote_repo_slug(url) == "o/r"
        assert remote_repo_slug(url) == "o/r"

    @pytest.mark.parametrize("url", [
        "https://github.com.evil.tld/o/r",
        "https://github.com@evil.tld/o/r",
        "https://gist.github.com/o/r",
        "https://x.tld/?github.com/o/r",
        "https://gitlab.com/o/r",
        "",
    ])
    def test_拒绝近似主机(self, url):
        assert _remote_repo_slug(url) == ""
        assert remote_repo_slug(url) is None


class TestWebhookType:
    """webhook 判型只看主机名，不看 URL 子串"""

    def test_slack(self):
        assert detect_webhook_type("https://hooks.slack.com/services/T/B/X") == "slack"

    @pytest.mark.parametrize("url", [
        "https://webhook.office.com/webhookb2/xxx",
        "https://hooks.microsoft.com/webhookb2/xxx",
    ])
    def test_teams(self, url):
        assert detect_webhook_type(url) == "teams"

    @pytest.mark.parametrize("url", [
        "https://attacker.tld/?hooks.slack.com",
        "https://attacker.tld/hooks.slack.com/services",
        "https://hooks.slack.com.evil.tld/services",
        "https://example.com/webhook",
    ])
    def test_近似或无关一律_generic(self, url):
        assert detect_webhook_type(url) == "generic"
