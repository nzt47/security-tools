#!/usr/bin/env python3
"""端到端验证: config.yaml 驱动的权重（.env 已注释掉）"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 清除 .env 环境变量（模拟 .env 注释掉的状态）
for env_name in [
    "SKILLS_FUSION_WEIGHT_TFIDF",
    "SKILLS_FUSION_WEIGHT_VECTOR",
    "SKILLS_FUSION_WEIGHT_BM25",
]:
    os.environ.pop(env_name, None)

from agent.skills_mgmt.loader import SkillLoader
SkillLoader._clear_all_caches()

weights = SkillLoader._get_default_weights()
env_bm25 = os.environ.get("SKILLS_FUSION_WEIGHT_BM25", "(未设置)")

print("=" * 60)
print("端到端验证: config.yaml 驱动的权重（.env 已注释掉）")
print("=" * 60)
print(f".env SKILLS_FUSION_WEIGHT_BM25 = {env_bm25}")
print(f"_get_default_weights() = {weights}")
print(f"bm25 = {weights['bm25']}")
print()

if abs(weights["bm25"] - 0.5) < 1e-9:
    print("PASS: config.yaml 的 bm25=0.5 已生效")
    print("  (.env 注释掉后, config.yaml 成为配置主源)")
    sys.exit(0)
else:
    print(f"FAIL: 期望 bm25=0.5, 实际 {weights['bm25']}")
    sys.exit(1)
