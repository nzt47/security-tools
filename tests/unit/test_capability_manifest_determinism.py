# -*- coding: utf-8 -*-
"""能力清单（data/capability_manifest.json）的**确定性**守卫（交接单 §9 T7）。

背景：#1288 一次性把 entries 顺序归一（内容逐字节等价）；T7 的口径是"再出现大 diff，
先查生成器排序，**不要手改 manifest**"。这里把可机检的两条不变式钉成用例：

1. entries 按 **kind 分块**（tool 块在前、skill 块在后，块内不交叉），块内按名称升序、名称唯一；
2. 生成器抹时间戳的两个工具函数真的只抹时间：`_strip_time`（JSON）与
   `_strip_generated_at`（Markdown 盘点表）。

【为什么不在这里重跑整条生成器】它要读运行期台账/技能库（CI 冷启动为空），
重跑会产出与仓库不同的清单；"块内有序 + 时间戳可抹"才是 churn 的根因面。
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "data" / "capability_manifest.json"


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_entries_are_kind_blocks_sorted_within_block():
    entries = _manifest().get("entries") or []
    assert entries, "manifest 没有 entries —— 文件被清空或结构变了"
    blocks = [(kind, list(group)) for kind, group in
              itertools.groupby(entries, key=lambda x: x.get("kind"))]
    kinds = [kind for kind, _ in blocks]
    assert kinds == sorted(set(kinds), key=kinds.index), f"kind 分块不连续（交叉了）: {kinds}"
    assert len(kinds) == len(set(kinds)), f"同一个 kind 被拆成多块: {kinds}"
    for kind, group in blocks:
        names = [str(x.get("tool_name") or "") for x in group]
        assert names == sorted(names), (
            f"{kind} 块内不是按名称升序 —— 生成器排序退化了，**不要手改 manifest**（T7）。"
            f"首个逆序位置：{next((i for i in range(len(names) - 1) if names[i] > names[i + 1]), -1)}")
        assert len(names) == len(set(names)), f"{kind} 块内有重名: {sorted({n for n in names if names.count(n) > 1})[:5]}"


def test_committed_manifest_counts_match_entries():
    manifest = _manifest()
    entries = manifest.get("entries") or []
    summary = manifest.get("summary") or {}
    for key, kind in (("skills", "skill"), ("tools", "tool")):
        if key in summary:
            actual = sum(1 for e in entries if e.get("kind") == kind)
            assert summary[key] == actual, f"summary[{key}]={summary[key]} 与 entries 实读 {actual} 不一致"


def test_strip_time_removes_only_timestamp_key():
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_capability_manifest as gen  # noqa: PLC0415 生成器是脚本，不在包里

    manifest = _manifest()
    stripped = gen._strip_time(dict(manifest))
    assert "generated_at" not in stripped
    assert len(stripped.get("entries") or []) == len(manifest.get("entries") or [])
    # 两份只有时间戳不同的清单，抹掉后必须完全相等（否则"两次派生必然不等"）
    a = dict(manifest, generated_at="2026-01-01T00:00:00")
    b = dict(manifest, generated_at="2026-12-31T23:59:59")
    assert gen._strip_time(a) == gen._strip_time(b)


def test_strip_generated_at_is_idempotent_and_keeps_body():
    sys.path.insert(0, str(ROOT / "scripts"))
    import sync_capability_manifest as gen  # noqa: PLC0415

    text = "# 能力清单\n\n生成时间：2026-10-25T08:08:45\n\n| 名称 | 说明 |\n| --- | --- |\n| apply_patch | x |\n"
    once = gen._strip_generated_at(text)
    assert "生成时间" not in once
    assert "apply_patch" in once and "| 名称 |" in once, "抹时间时把正文也删了"
    assert gen._strip_generated_at(once) == once, "不是幂等的"
