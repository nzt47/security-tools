#!/usr/bin/env python3
"""验证 config.yaml 读取失败时的降级逻辑

不改现有 loader.py, 在本脚本中独立实现分层 _get_default_weights() 逻辑,
通过 6 种失败场景验证系统是否能正确降级到硬编码默认值:

    场景1: config.yaml 不存在                    → 降级到硬编码 (bm25=0.2)
    场景2: config.yaml YAML 语法错误             → 降级到硬编码 (bm25=0.2)
    场景3: config.yaml 存在但 fusion 路径缺失    → 降级到硬编码 (bm25=0.2)
    场景4: config.yaml bm25 值为字符串 "invalid" → float() 失败, 降级到硬编码
    场景5: config.yaml 文件完全为空              → 降级到硬编码 (bm25=0.2)
    场景6: config.yaml bm25 值为 None            → 降级到硬编码 (bm25=0.2)

运行:
    python scripts/verify_config_yaml_degradation.py

【不易】硬编码默认值 tfidf=0.2, vector=0.6, bm25=0.2 是最终兜底, 不可删除
【变易】每种失败场景都应静默降级, 不抛异常影响主流程
【简易】失败时记录 WARNING 日志, 返回硬编码默认值
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
#  分层配置逻辑（模拟修改后的 _get_default_weights, 含降级）
# ════════════════════════════════════════════════════════════

def _load_weights_from_config_yaml(
    config_path: Path,
) -> Dict[str, float]:
    """从 config.yaml 读取 fusion weights（层1: 业务配置主源）

    任何异常都静默降级, 返回空 dict, 让上层用硬编码默认值兜底.

    【不易】失败时不抛异常, 返回空 dict
    【变易】覆盖文件不存在/语法错误/路径缺失/类型错误等多种失败
    【简易】单一 try/except 包裹, 日志记录降级原因
    """
    weights: Dict[str, float] = {}
    if not config_path.exists():
        print(f"  [DEGRADE] config.yaml 不存在: {config_path}")
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
                # float() 转换, 字符串/None 等非法值会抛 ValueError/TypeError
                val = fusion_weights[key]
                if val is None:
                    raise ValueError(f"{key} 值为 None")
                weights[key] = float(val)
    except yaml.YAMLError as e:
        print(f"  [DEGRADE] config.yaml YAML 语法错误: {str(e)[:80]}")
        return {}
    except (ValueError, TypeError) as e:
        print(f"  [DEGRADE] config.yaml 值类型错误: {e}")
        return {}
    except Exception as e:
        print(f"  [DEGRADE] config.yaml 读取异常: {type(e).__name__}: {str(e)[:80]}")
        return {}
    return weights


def _load_weights_from_env() -> Dict[str, float]:
    """从 .env 环境变量读取（层2, 本测试中不设置, 返回空）"""
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
            pass
    return weights


def get_default_weights_layered(
    config_path: Optional[Path] = None,
) -> Dict[str, float]:
    """分层获取默认权重 — 优先级: .env > config.yaml > 硬编码默认值"""
    # 层0: 硬编码默认值（最终兜底）
    weights = dict(_HARDCODED_DEFAULTS)

    # 层1: config.yaml（失败时静默降级）
    if config_path is not None:
        yaml_weights = _load_weights_from_config_yaml(config_path)
        weights.update(yaml_weights)

    # 层2: .env
    env_weights = _load_weights_from_env()
    weights.update(env_weights)

    return weights


# ════════════════════════════════════════════════════════════
#  测试场景
# ════════════════════════════════════════════════════════════

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
    for env_name, val in original.items():
        if val is not None:
            os.environ[env_name] = val
        else:
            os.environ.pop(env_name, None)


def run_scenario(
    name: str,
    desc: str,
    config_content: Optional[str],
    expected_bm25: float,
) -> bool:
    """运行单个降级测试场景

    Args:
        name: 场景名
        desc: 场景描述
        config_content: config.yaml 文件内容; None 表示不创建文件
        expected_bm25: 期望最终读到的 bm25 值（应降级到 0.2）
    """
    print(f"\n┌─ {name}: {desc}")

    original_env = _clear_env()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        config_path = tmp_dir / "config.yaml"

        if config_content is not None:
            with open(config_path, "w", encoding="utf-8") as f:
                f.write(config_content)
            print(f"│  config.yaml = 已创建（{len(config_content)} 字节）")
        else:
            print(f"│  config.yaml = 未创建（模拟文件不存在）")

        weights = get_default_weights_layered(config_path=config_path)
        actual_bm25 = weights["bm25"]

        ok = abs(actual_bm25 - expected_bm25) < 1e-9
        mark = "✓" if ok else "✗"
        print(f"│")
        print(f"│  期望 bm25 = {expected_bm25}（硬编码默认值）")
        print(f"│  实际 bm25 = {actual_bm25}")
        print(f"│  完整权重 = {weights}")
        print(f"└─→ {mark} {'降级成功' if ok else '降级失败: 期望 ' + str(expected_bm25)}")

    _restore_env(original_env)
    return ok


def main():
    print("=" * 80)
    print("config.yaml 读取失败降级验证")
    print("=" * 80)
    print()
    print("验证目标: config.yaml 各种读取失败场景下, 系统是否正确降级到硬编码默认值")
    print(f"硬编码默认值: {_HARDCODED_DEFAULTS}")
    print(f"期望 bm25 降级值: {_HARDCODED_DEFAULTS['bm25']}")
    print()

    results = []

    # 场景1: config.yaml 不存在
    results.append(run_scenario(
        "场景1", "config.yaml 不存在",
        config_content=None,
        expected_bm25=0.2,
    ))

    # 场景2: config.yaml YAML 语法错误
    results.append(run_scenario(
        "场景2", "config.yaml YAML 语法错误",
        config_content="""
skills_mgmt:
  retrieval:
    fusion:
      weights:
        tfidf: 0.2
        vector: 0.6
        bm25: [invalid: yaml: syntax
""",
        expected_bm25=0.2,
    ))

    # 场景3: config.yaml 存在但 fusion 路径缺失
    results.append(run_scenario(
        "场景3", "config.yaml fusion 路径缺失",
        config_content="""
skills_mgmt:
  retrieval:
    method: tfidf
    # fusion 节点完全缺失
""",
        expected_bm25=0.2,
    ))

    # 场景4: config.yaml bm25 值为字符串 "invalid"
    results.append(run_scenario(
        "场景4", "config.yaml bm25 值为字符串 'invalid'",
        config_content="""
skills_mgmt:
  retrieval:
    fusion:
      weights:
        tfidf: 0.2
        vector: 0.6
        bm25: invalid
""",
        expected_bm25=0.2,
    ))

    # 场景5: config.yaml 文件完全为空
    results.append(run_scenario(
        "场景5", "config.yaml 文件完全为空",
        config_content="",
        expected_bm25=0.2,
    ))

    # 场景6: config.yaml bm25 值为 None（YAML 中写 null）
    results.append(run_scenario(
        "场景6", "config.yaml bm25 值为 None",
        config_content="""
skills_mgmt:
  retrieval:
    fusion:
      weights:
        tfidf: 0.2
        vector: 0.6
        bm25: null
""",
        expected_bm25=0.2,
    ))

    # 汇总
    print()
    print("═" * 80)
    print("【汇总】")
    print("═" * 80)
    passed = sum(results)
    total = len(results)
    scenario_names = [
        "文件不存在",
        "YAML 语法错误",
        "fusion 路径缺失",
        "bm25 值为字符串",
        "文件为空",
        "bm25 值为 None",
    ]
    for i, (ok, name) in enumerate(zip(results, scenario_names), 1):
        mark = "✓" if ok else "✗"
        print(f"  场景{i}: {mark} {name}")
    print()
    print(f"通过: {passed}/{total}")
    if passed == total:
        print()
        print("【结论】config.yaml 读取失败降级逻辑验证全部通过:")
        print("  ✓ 文件不存在时降级到硬编码默认值（场景1）")
        print("  ✓ YAML 语法错误时降级（场景2）")
        print("  ✓ 路径缺失时降级（场景3）")
        print("  ✓ 值类型错误时降级（场景4）")
        print("  ✓ 空文件时降级（场景5）")
        print("  ✓ None 值时降级（场景6）")
        print()
        print("所有失败场景都静默降级到 bm25=0.2, 不抛异常, 不影响主流程")
        print("可以安全实施分层配置方案")
    else:
        print()
        print("【结论】有场景未正确降级, 需排查")
    print("=" * 80)


if __name__ == "__main__":
    main()
