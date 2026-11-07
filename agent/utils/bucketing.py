# -*- coding: utf-8 -*-
"""分桶哈希（2026-11-07 从 MD5 换成 SHA-256）。

【为什么改】CodeQL 的 py/weak-sensitive-data-hashing 对「用户/会话 id 用 MD5」给了 4 条告警
（agent/ab_testing.py ×2、scripts/feature_flags.py ×2）。分桶确实**不需要**密码学强度，
但也确实**不需要 MD5**：这里既没有「必须兼容历史桶位」的持久化契约，也没有性能理由
（每次分桶一次 SHA-256 对 Python 来说可忽略）。

【代价（明确说出来）】换算法 = **重新洗牌**：同一个用户在新旧实现下可能落进不同桶。
影响面：
  - ab_testing：实验是运行时学习的记录，进行中的实验分组会变（统计口径要在实验
    重新开跑后重置）；
  - feature_flags：百分比灰度的样本会变（有人被放进来、有人被移出去），这是
    一次性重排，之后保持稳定。

【口径】只用标准库；同一 key 永远同一桶位；分布均匀性由 SHA-256 保证。
"""
from __future__ import annotations

import hashlib

#: 取摘要前多少字节当整数：8 字节（64 bit）足够 10000 桶内均匀，且 int 转换开销小
_DIGEST_BYTES = 8


def bucket_int(key: str) -> int:
    """把任意字符串映射成一个稳定的 64 bit 整数（同一 key 永远同值）。"""
    digest = hashlib.sha256(str(key).encode("utf-8")).digest()[:_DIGEST_BYTES]
    return int.from_bytes(digest, "big")


def bucket_index(key: str, modulo: int) -> int:
    """落在 [0, modulo) 的桶位；modulo <= 0 直接拒绝（调用方先判空）。"""
    if modulo <= 0:
        raise ValueError("modulo 必须为正整数")
    return bucket_int(key) % modulo


def bucket_percent(key: str) -> int:
    """落在 [0, 100) 的百分比桶位（与 bucket_index(key, 100) 同值）。"""
    return bucket_int(key) % 100
