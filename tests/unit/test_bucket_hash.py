# -*- coding: utf-8 -*-
"""分桶哈希的口径（2026-11-07：MD5 → SHA-256）。

【为什么单开一条】ab_testing 与 feature_flags 的 4 处分桶原来用 MD5（CodeQL
py/weak-sensitive-data-hashing）。分桶不需要密码学强度，但也不需要 MD5；换成 SHA-256 后
必须把三件事钉住：
  1. 同一 key 永远同一桶位（否则"同一用户始终同一变体"的承诺就没了）；
  2. 桶位落在范围内、且分布大致均匀（不能全挤在一个桶）；
  3. 算法换了 ⇒ 桶位相对 MD5 会重排（这是**已知代价**，写在这里防止有人以为"没变"）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from agent.utils.bucketing import bucket_index, bucket_int, bucket_percent


class TestStability:
    def test_同一key永远同值(self):
        for key in ("exp-1:alice", "flag-x:bob", "traffic:e:u"):
            assert bucket_int(key) == bucket_int(key)
            assert bucket_index(key, 100) == bucket_index(key, 100)
            assert bucket_percent(key) == bucket_percent(key)

    def test_不同key基本不同值(self):
        values = {bucket_percent(f"user-{i}") for i in range(50)}
        assert len(values) > 10, f"50 个 key 只落进 {len(values)} 个桶，分布可疑"


class TestRange:
    @pytest.mark.parametrize("modulo", [1, 2, 7, 100, 10000])
    def test_桶位在范围内(self, modulo):
        for i in range(200):
            b = bucket_index(f"k{i}", modulo)
            assert 0 <= b < modulo

    def test_百分比桶位与取模一致(self):
        for i in range(50):
            key = f"k{i}"
            assert bucket_percent(key) == bucket_index(key, 100)

    def test_modulo非正数直接拒绝(self):
        with pytest.raises(ValueError):
            bucket_index("k", 0)
        with pytest.raises(ValueError):
            bucket_index("k", -3)


class TestDeterminism:
    def test_同一批key的分布是大致均匀的(self):
        counts = [0] * 10
        for i in range(2000):
            counts[bucket_index(f"user-{i}", 10)] += 1
        assert min(counts) > 100, counts          # 期望 ~200，容差 50%
        assert max(counts) < 320, counts

    def test_取值不小于8字节摘要的语义(self):
        # 64 bit 摘要：相邻 key 的桶位不该呈线性（说明真的在哈希，不是取索引）
        diffs = {bucket_index(f"k{i}", 1000) - bucket_index(f"k{i - 1}", 1000) for i in range(1, 20)}
        assert len(diffs) > 1
