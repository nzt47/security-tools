#!/usr/bin/env python3
"""用实际 SkillLoader 代码验证 config.yaml mtime 缓存自动失效

与 verify_cache_invalidation.py 的区别:
    - verify_cache_invalidation.py: 独立模拟缓存逻辑（ConfigYamlCache 类）
    - 本脚本: 直接调用 SkillLoader._load_weights_from_config_yaml_cached()
      验证生产代码的正确性

测试场景:
    场景1: 首次读取 → 建立缓存
    场景2: 修改 config.yaml → mtime 变化, 缓存失效, 读到新值
    场景3: 不修改 → 缓存命中（毫秒级）
    场景4: 删除 config.yaml → 缓存清除, 降级到空 dict
    场景5: 重新创建 → 缓存重建

运行:
    python scripts/verify_real_cache_invalidation.py

【不易】验证生产代码 SkillLoader._load_weights_from_config_yaml_cached 的正确性
【变易】monkeypatch _CONFIG_YAML_PATH 指向临时文件, 隔离测试环境
【简易】5 场景覆盖缓存生命周期 + 耗时测量
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _write_config_yaml(path: Path, bm25: float) -> None:
    """写入临时 config.yaml"""
    config = {
        "skills_mgmt": {
            "retrieval": {
                "fusion": {
                    "weights": {
                        "tfidf": 0.2,
                        "vector": 0.6,
                        "bm25": bm25,
                    }
                }
            }
        }
    }
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, default_flow_style=False)


def _measure_time(func, *args, **kwargs) -> Tuple[float, any]:
    """测量函数执行耗时（毫秒）"""
    t0 = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return elapsed_ms, result


def _clear_env() -> Dict[str, str | None]:
    """清除 .env 环境变量"""
    original = {}
    for env_name in [
        "SKILLS_FUSION_WEIGHT_TFIDF",
        "SKILLS_FUSION_WEIGHT_VECTOR",
        "SKILLS_FUSION_WEIGHT_BM25",
    ]:
        original[env_name] = os.environ.pop(env_name, None)
    return original


def _restore_env(original: Dict[str, str | None]) -> None:
    for env_name, val in original.items():
        if val is not None:
            os.environ[env_name] = val
        else:
            os.environ.pop(env_name, None)


def main():
    from agent.skills_mgmt.loader import SkillLoader

    print("=" * 80)
    print("实际 SkillLoader 代码验证: config.yaml mtime 缓存自动失效")
    print("=" * 80)
    print()
    print("验证目标: 直接调用 SkillLoader._load_weights_from_config_yaml_cached()")
    print("          确认生产代码的缓存失效逻辑正确")
    print()

    original_env = _clear_env()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        config_path = tmp_dir / "config.yaml"

        # monkeypatch: 将 _CONFIG_YAML_PATH 指向临时文件
        original_yaml_path = SkillLoader._CONFIG_YAML_PATH
        SkillLoader._CONFIG_YAML_PATH = config_path
        SkillLoader._clear_all_caches()

        results = []

        # ──────────────────────────────────────────────
        # 场景1: 首次读取 config.yaml(bm25=0.5) → 建立缓存
        # ──────────────────────────────────────────────
        print("┌─ 场景1: 首次读取 config.yaml(bm25=0.5)")
        _write_config_yaml(config_path, 0.5)
        time.sleep(0.01)

        elapsed, weights = _measure_time(
            SkillLoader._load_weights_from_config_yaml_cached
        )
        bm25 = weights.get("bm25", 0.0)
        ok1 = abs(bm25 - 0.5) < 1e-9
        results.append(ok1)
        cache_state = SkillLoader._CONFIG_YAML_CACHE
        mtime_str = f"{cache_state[0]:.3f}" if cache_state else "N/A"
        print(f"│  耗时: {elapsed:.3f}ms")
        print(f"│  bm25 = {bm25}")
        print(f"│  缓存: {cache_state is not None} (mtime={mtime_str})")
        print(f"└─→ {'PASS' if ok1 else 'FAIL'}")
        print()

        # ──────────────────────────────────────────────
        # 场景2: 修改 config.yaml(bm25=0.8) → 缓存应失效
        # ──────────────────────────────────────────────
        print("┌─ 场景2: 修改 config.yaml(bm25=0.8), 缓存应失效")
        old_mtime = SkillLoader._CONFIG_YAML_CACHE[0] if SkillLoader._CONFIG_YAML_CACHE else 0
        _write_config_yaml(config_path, 0.8)
        time.sleep(0.01)

        elapsed, weights = _measure_time(
            SkillLoader._load_weights_from_config_yaml_cached
        )
        bm25 = weights.get("bm25", 0.0)
        ok2 = abs(bm25 - 0.8) < 1e-9
        results.append(ok2)
        new_mtime = SkillLoader._CONFIG_YAML_CACHE[0] if SkillLoader._CONFIG_YAML_CACHE else 0
        print(f"│  耗时: {elapsed:.3f}ms（含重新解析）")
        print(f"│  bm25 = {bm25}")
        print(f"│  mtime 变化: {old_mtime:.3f} → {new_mtime:.3f} ({'已失效' if old_mtime != new_mtime else '未失效'})")
        print(f"└─→ {'PASS: 缓存失效后读到新值 0.8' if ok2 else 'FAIL'}")
        print()

        # ──────────────────────────────────────────────
        # 场景3: 不修改 → 缓存命中（毫秒级）
        # ──────────────────────────────────────────────
        print("┌─ 场景3: 不修改 config.yaml, 缓存应命中")
        times = []
        bm25_values = []
        for _ in range(10):
            elapsed, weights = _measure_time(
                SkillLoader._load_weights_from_config_yaml_cached
            )
            times.append(elapsed)
            bm25_values.append(weights.get("bm25", 0.0))

        avg_ms = sum(times) / len(times)
        max_ms = max(times)
        min_ms = min(times)
        bm25 = bm25_values[0]
        ok3 = abs(bm25 - 0.8) < 1e-9 and avg_ms < 1.0
        results.append(ok3)
        print(f"│  10 次读取: min={min_ms:.4f}ms, avg={avg_ms:.4f}ms, max={max_ms:.4f}ms")
        print(f"│  bm25 = {bm25}")
        print(f"└─→ {'PASS: 缓存命中, 平均 <1ms' if ok3 else 'FAIL'}")
        print()

        # ──────────────────────────────────────────────
        # 场景4: 删除 config.yaml → 缓存清除
        # ──────────────────────────────────────────────
        print("┌─ 场景4: 删除 config.yaml, 缓存应清除")
        config_path.unlink()

        elapsed, weights = _measure_time(
            SkillLoader._load_weights_from_config_yaml_cached
        )
        bm25 = weights.get("bm25", 0.0)
        ok4 = weights == {} and SkillLoader._CONFIG_YAML_CACHE is None
        results.append(ok4)
        print(f"│  耗时: {elapsed:.3f}ms")
        print(f"│  weights = {weights}（应为空 dict）")
        print(f"│  缓存: {SkillLoader._CONFIG_YAML_CACHE}（应为 None）")
        print(f"└─→ {'PASS: 缓存清除, 返回空 dict' if ok4 else 'FAIL'}")
        print()

        # ──────────────────────────────────────────────
        # 场景5: 重新创建 config.yaml(bm25=0.7) → 缓存重建
        # ──────────────────────────────────────────────
        print("┌─ 场景5: 重新创建 config.yaml(bm25=0.7), 缓存应重建")
        _write_config_yaml(config_path, 0.7)
        time.sleep(0.01)

        elapsed, weights = _measure_time(
            SkillLoader._load_weights_from_config_yaml_cached
        )
        bm25 = weights.get("bm25", 0.0)
        ok5 = abs(bm25 - 0.7) < 1e-9 and SkillLoader._CONFIG_YAML_CACHE is not None
        results.append(ok5)
        print(f"│  耗时: {elapsed:.3f}ms（含重新解析）")
        print(f"│  bm25 = {bm25}")
        print(f"│  缓存: {SkillLoader._CONFIG_YAML_CACHE is not None}")
        print(f"└─→ {'PASS: 缓存重建后读到新值 0.7' if ok5 else 'FAIL'}")
        print()

        # 恢复原始路径
        SkillLoader._CONFIG_YAML_PATH = original_yaml_path
        SkillLoader._clear_all_caches()

    _restore_env(original_env)

    # ──────────────────────────────────────────────
    # 汇总
    # ──────────────────────────────────────────────
    print("═" * 80)
    print("【汇总】实际 SkillLoader 代码验证")
    print("═" * 80)
    passed = sum(results)
    total = len(results)
    scenario_names = [
        "首次读取建立缓存",
        "修改文件缓存失效",
        "缓存命中毫秒级响应",
        "删除文件缓存清除",
        "重建文件缓存重建",
    ]
    for i, (ok, name) in enumerate(zip(results, scenario_names), 1):
        mark = "PASS" if ok else "FAIL"
        print(f"  场景{i}: [{mark}] {name}")
    print()
    print(f"通过: {passed}/{total}")

    if passed == total:
        print()
        print("【结论】实际 SkillLoader 生产代码验证全部通过:")
        print(f"  PASS 首次读取建立缓存（场景1）")
        print(f"  PASS 文件修改后 mtime 变化, 缓存自动失效（场景2: 0.5→0.8）")
        print(f"  PASS 缓存命中毫秒级响应（场景3: 平均 {avg_ms:.4f}ms < 1ms）")
        print(f"  PASS 文件删除后缓存清除, 返回空 dict（场景4）")
        print(f"  PASS 文件重建后缓存重建（场景5）")
        print()
        print("生产代码 SkillLoader._load_weights_from_config_yaml_cached() 正确")
    else:
        print()
        print("【结论】有场景失败, 需排查生产代码")
    print("=" * 80)


if __name__ == "__main__":
    main()
