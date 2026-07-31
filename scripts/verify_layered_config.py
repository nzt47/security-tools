#!/usr/bin/env python3
"""验证分层配置逻辑: .env 覆盖 config.yaml 覆盖 硬编码默认值

不改现有 loader.py, 在本脚本中独立实现分层 _get_default_weights() 逻辑,
通过 5 种场景验证优先级链路是否正确:

    场景1: config.yaml(bm25=0.5) + .env(不设置)        → 期望 0.5 (config.yaml 生效)
    场景2: config.yaml(bm25=0.5) + .env(bm25=0.8)      → 期望 0.8 (.env 覆盖)
    场景3: config.yaml(不存在)   + .env(bm25=0.8)      → 期望 0.8 (config.yaml 降级)
    场景4: config.yaml(不存在)   + .env(不设置)        → 期望 0.2 (硬编码兜底)
    场景5: config.yaml(bm25=0.5) + .env(bm25=invalid)  → 期望 0.5 (.env 非法值降级)

运行:
    python scripts/verify_layered_config.py

【不易】硬编码默认值 tfidf=0.2, vector=0.6, bm25=0.2 作为最终兜底
【变易】分层优先级: .env > config.yaml > 硬编码默认值
【简易】每场景独立隔离, 清晰展示优先级链路
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, Optional

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ════════════════════════════════════════════════════════════
#  硬编码默认值（与 loader.py:_DEFAULT_RETRIEVAL_WEIGHTS 同源）
# ════════════════════════════════════════════════════════════

_HARDCODED_DEFAULTS: Dict[str, float] = {
    "tfidf": 0.2,
    "vector": 0.6,
    "bm25": 0.2,
}


# ════════════════════════════════════════════════════════════
#  分层配置逻辑（模拟修改后的 _get_default_weights）
# ════════════════════════════════════════════════════════════

def _load_weights_from_config_yaml(
    config_path: Path,
) -> Dict[str, float]:
    """从 config.yaml 读取 fusion weights（层1: 业务配置主源）

    Returns:
        Dict — 可能空（config.yaml 不存在或读取失败时返回空 dict）
    """
    weights: Dict[str, float] = {}
    if not config_path.exists():
        return weights
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
        fusion_weights = (
            config.get("skills_mgmt", {})
            .get("retrieval", {})
            .get("fusion", {})
            .get("weights", {})
        )
        for key in ("tfidf", "vector", "bm25"):
            if key in fusion_weights:
                weights[key] = float(fusion_weights[key])
    except Exception as e:
        print(f"  [WARN] config.yaml 读取失败: {e}")
    return weights


def _load_weights_from_env() -> Dict[str, float]:
    """从 .env 环境变量读取 fusion weights（层2: 运维覆盖）

    Returns:
        Dict — 可能空（环境变量未设置或非法时返回空 dict）
    """
    weights: Dict[str, float] = {}
    for key, env_name in [
        ("tfidf", "SKILLS_FUSION_WEIGHT_TFIDF"),
        ("vector", "SKILLS_FUSION_WEIGHT_VECTOR"),
        ("bm25", "SKILLS_FUSION_WEIGHT_BM25"),
    ]:
        raw = os.environ.get(env_name)
        if raw is None or not raw.strip():
            continue
        try:
            weights[key] = float(raw)
        except (ValueError, TypeError):
            print(f"  [WARN] .env {env_name}='{raw}' 非法值, 跳过（降级到下层）")
    return weights


def get_default_weights_layered(
    config_path: Optional[Path] = None,
) -> Dict[str, float]:
    """分层获取默认权重 — 优先级: .env > config.yaml > 硬编码默认值

    这是模拟修改后的 SkillLoader._get_default_weights() 的逻辑,
    用于在不改现有代码的情况下验证分层配置正确性.

    Args:
        config_path: config.yaml 路径（测试时传入临时路径; None 则用项目根 config.yaml）

    【不易】硬编码默认值作为最终兜底, 保证代码可独立运行
    【变易】config.yaml 让权重可版本控制, .env 允许运维临时覆盖
    【简易】逐层覆盖, 每层失败静默降级到下层
    """
    # 层0: 硬编码默认值（最终兜底）
    weights = dict(_HARDCODED_DEFAULTS)

    # 层1: config.yaml（业务配置主源）
    if config_path is not None:
        yaml_weights = _load_weights_from_config_yaml(config_path)
        weights.update(yaml_weights)

    # 层2: .env（运维覆盖, 优先级最高）
    env_weights = _load_weights_from_env()
    weights.update(env_weights)

    return weights


# ════════════════════════════════════════════════════════════
#  测试场景
# ════════════════════════════════════════════════════════════

def _write_config_yaml(path: Path, bm25: float) -> None:
    """生成临时 config.yaml"""
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


def _clear_env() -> Dict[str, Optional[str]]:
    """清除 .env 环境变量, 返回原始值供恢复"""
    original = {}
    for env_name in [
        "SKILLS_FUSION_WEIGHT_TFIDF",
        "SKILLS_FUSION_WEIGHT_VECTOR",
        "SKILLS_FUSION_WEIGHT_BM25",
    ]:
        original[env_name] = os.environ.pop(env_name, None)
    return original


def _restore_env(original: Dict[str, Optional[str]]) -> None:
    """恢复 .env 环境变量"""
    for env_name, val in original.items():
        if val is not None:
            os.environ[env_name] = val
        else:
            os.environ.pop(env_name, None)


def run_scenario(
    name: str,
    desc: str,
    config_yaml_bm25: Optional[float],
    env_bm25: Optional[str],
    expected_bm25: float,
) -> bool:
    """运行单个测试场景

    Args:
        name: 场景名
        desc: 场景描述
        config_yaml_bm25: config.yaml 中的 bm25 值; None 表示不创建 config.yaml
        env_bm25: .env 中 SKILLS_FUSION_WEIGHT_BM25 的值; None 表示不设置
        expected_bm25: 期望最终读到的 bm25 值
    """
    print(f"\n┌─ {name}: {desc}")
    print(f"│  config.yaml bm25 = {config_yaml_bm25}")
    print(f"│  .env bm25        = {env_bm25!r}")
    print(f"│  期望最终 bm25    = {expected_bm25}")

    # 隔离环境: 清除 .env 变量
    original_env = _clear_env()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # 准备 config.yaml
        if config_yaml_bm25 is not None:
            config_path = tmp_dir / "config.yaml"
            _write_config_yaml(config_path, config_yaml_bm25)
            print(f"│  config.yaml      = 已创建 (bm25={config_yaml_bm25})")
        else:
            config_path = tmp_dir / "config.yaml"  # 不存在的路径
            print(f"│  config.yaml      = 不存在")

        # 准备 .env
        if env_bm25 is not None:
            os.environ["SKILLS_FUSION_WEIGHT_BM25"] = env_bm25
            print(f"│  .env             = 已设置 (bm25={env_bm25})")
        else:
            print(f"│  .env             = 未设置")

        # 执行分层读取
        weights = get_default_weights_layered(config_path=config_path)
        actual_bm25 = weights["bm25"]

        # 验证
        ok = abs(actual_bm25 - expected_bm25) < 1e-9
        mark = "✓" if ok else "✗"
        print(f"│")
        print(f"│  实际 bm25        = {actual_bm25}")
        print(f"│  完整权重         = {weights}")
        print(f"└─→ {mark} {'通过' if ok else '失败: 期望 ' + str(expected_bm25) + ', 实际 ' + str(actual_bm25)}")

    # 恢复环境
    _restore_env(original_env)
    return ok


def main():
    print("=" * 80)
    print("分层配置逻辑验证: .env 覆盖 config.yaml 覆盖 硬编码默认值")
    print("=" * 80)
    print()
    print("优先级链路:")
    print("  层0: 硬编码默认值 (tfidf=0.2, vector=0.6, bm25=0.2)")
    print("  层1: config.yaml  (业务配置主源, 可版本控制)")
    print("  层2: .env         (运维覆盖, 优先级最高)")
    print()

    results = []

    # 场景1: config.yaml(bm25=0.5) + .env(不设置) → 期望 0.5
    results.append(run_scenario(
        "场景1", "config.yaml 生效（.env 未设置）",
        config_yaml_bm25=0.5, env_bm25=None,
        expected_bm25=0.5,
    ))

    # 场景2: config.yaml(bm25=0.5) + .env(bm25=0.8) → 期望 0.8
    results.append(run_scenario(
        "场景2", ".env 覆盖 config.yaml（核心验证）",
        config_yaml_bm25=0.5, env_bm25="0.8",
        expected_bm25=0.8,
    ))

    # 场景3: config.yaml(不存在) + .env(bm25=0.8) → 期望 0.8
    results.append(run_scenario(
        "场景3", "config.yaml 不存在, .env 兜底",
        config_yaml_bm25=None, env_bm25="0.8",
        expected_bm25=0.8,
    ))

    # 场景4: config.yaml(不存在) + .env(不设置) → 期望 0.2
    results.append(run_scenario(
        "场景4", "两者都无, 硬编码兜底",
        config_yaml_bm25=None, env_bm25=None,
        expected_bm25=0.2,
    ))

    # 场景5: config.yaml(bm25=0.5) + .env(bm25=invalid) → 期望 0.5
    results.append(run_scenario(
        "场景5", ".env 非法值, 降级到 config.yaml",
        config_yaml_bm25=0.5, env_bm25="not_a_number",
        expected_bm25=0.5,
    ))

    # 汇总
    print()
    print("═" * 80)
    print("【汇总】")
    print("═" * 80)
    passed = sum(results)
    total = len(results)
    for i, (ok, name) in enumerate(zip(results, [
        "config.yaml 生效",
        ".env 覆盖 config.yaml",
        "config.yaml 不存在 + .env",
        "硬编码兜底",
        ".env 非法值降级",
    ]), 1):
        mark = "✓" if ok else "✗"
        print(f"  场景{i}: {mark} {name}")
    print()
    print(f"通过: {passed}/{total}")
    if passed == total:
        print()
        print("【结论】分层配置逻辑验证全部通过:")
        print("  ✓ .env 能正确覆盖 config.yaml（场景2: 0.5 → 0.8）")
        print("  ✓ config.yaml 不存在时 .env 兜底（场景3）")
        print("  ✓ 两者都无时硬编码兜底（场景4）")
        print("  ✓ .env 非法值时降级到 config.yaml（场景5）")
        print()
        print("可以安全实施分层配置方案（修改 loader.py + config.yaml）")
    else:
        print()
        print("【结论】有场景失败, 需排查后再实施")
    print("=" * 80)


if __name__ == "__main__":
    main()
