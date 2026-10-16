# -*- coding: utf-8 -*-
"""scripts/watchdog_yunshu.py 的 HEAD 留痕辅助（部署漂移可见）。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_read_repo_head_matches_git():
    from scripts.watchdog_yunshu import read_repo_head

    head = read_repo_head(str(REPO_ROOT))
    assert head is None or re.fullmatch(r"[0-9a-fA-F]{7,64}", head), head
    if head is not None:
        real = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=15,
        ).stdout.strip()
        if real:
            assert head == real


def test_read_repo_head_bad_path_is_none():
    from scripts.watchdog_yunshu import read_repo_head

    assert read_repo_head(str(REPO_ROOT / "definitely-not-a-repo")) is None
