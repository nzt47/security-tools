"""TASK-02 配置生效验证：三层优先级（环境变量 > config.yaml > 默认值）

运行: python scripts/verify_task02_config_effective.py

覆盖场景：
  A. config.yaml 真实读取（不注入、不 mock 配置层）：
     reflection_persist=true / critic_evaluation_enabled=true / experience_persist=false
  B. 环境变量覆盖 > config.yaml（运维 hotfix 生效）
  C. 环境变量清除后回落到 config.yaml 值
  D. 非法环境变量值处理（观察打印，不强断言）
  E. config.yaml 缺失 → 硬编码默认值兜底（两开关 false，接线 inert）

【不易】只读验证：不修改 config.yaml；环境变量在 finally 中恢复，不污染用户环境。
"""

import logging
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")  # 保证仓库根目录可导入

from agent.orchestrator.orchestrator import Orchestrator
from planning.core import PlanningCore

logging.basicConfig(
    level=logging.ERROR,  # 收敛既有 INFO 噪音，仅输出本脚本 PASS/FAIL
    format="%(levelname)s %(message)s",
)

_ENV_KEYS = ("LEARNING_REFLECTION_PERSIST", "CRITIC_EVALUATION_ENABLED", "LEARNING_EXPERIENCE_PERSIST")


def _snapshot_env():
    return {k: os.environ.get(k) for k in _ENV_KEYS}


def _restore_env(snap):
    for k in _ENV_KEYS:
        if snap[k] is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = snap[k]


def scenario_a_config_yaml():
    """A. config.yaml 真实读取（当前磁盘值）"""
    print("\n── 场景 A：config.yaml 真实读取（无环境变量覆盖）")
    cfg = Orchestrator._load_learning_config()
    exp = PlanningCore._load_experience_persist_config()
    print(f"    reflection_persist={cfg['reflection_persist']}  "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}  "
          f"experience_persist={exp}")
    assert cfg["reflection_persist"] is True, "reflection_persist 应为 true"
    assert cfg["critic_evaluation_enabled"] is True, "critic_evaluation_enabled 应为 true"
    assert exp is False, "experience_persist 首期应为 false（观察）"
    print("  [A] PASS: config.yaml 三开关符合上线预期")


def scenario_b_env_override():
    """B. 环境变量覆盖 > config.yaml"""
    print("\n── 场景 B：环境变量覆盖（LEARNING_REFLECTION_PERSIST=false / CRITIC_EVALUATION_ENABLED=false）")
    os.environ["LEARNING_REFLECTION_PERSIST"] = "false"
    os.environ["CRITIC_EVALUATION_ENABLED"] = "false"
    cfg = Orchestrator._load_learning_config()
    print(f"    config.yaml=true 但环境变量=false → 读到 reflection_persist={cfg['reflection_persist']} "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}")
    assert cfg["reflection_persist"] is False, "环境变量应覆盖 config.yaml"
    assert cfg["critic_evaluation_enabled"] is False, "环境变量应覆盖 config.yaml"
    print("  [B] PASS: 环境变量层优先级最高（hotfix 可一键关）")


def scenario_c_env_restore():
    """C. 环境变量清除 → 回落到 config.yaml 值"""
    print("\n── 场景 C：环境变量清除后回落")
    os.environ.pop("LEARNING_REFLECTION_PERSIST", None)
    os.environ.pop("CRITIC_EVALUATION_ENABLED", None)
    cfg = Orchestrator._load_learning_config()
    print(f"    清除环境变量后 → reflection_persist={cfg['reflection_persist']} "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}")
    assert cfg["reflection_persist"] is True, "清除后应回落到 config.yaml 的 true"
    assert cfg["critic_evaluation_enabled"] is True, "清除后应回落到 config.yaml 的 true"
    print("  [C] PASS: 回落 config.yaml 值正确")


def scenario_d_invalid_env():
    """D. 非法环境变量值（观察处理方式：不在 true/1/yes 集合 → 视为 False）"""
    print("\n── 场景 D：非法环境变量值（'abc'）")
    os.environ["LEARNING_REFLECTION_PERSIST"] = "abc"
    cfg = Orchestrator._load_learning_config()
    print(f"    非法值 'abc' → reflection_persist={cfg['reflection_persist']}"
          "（按实现：不在 true/1/yes → False，运维需知）")
    os.environ.pop("LEARNING_REFLECTION_PERSIST", None)
    print("  [D] PASS: 非法值被安全处理（不抛异常），恢复环境变量")


def scenario_e_config_missing_fallback():
    """E. config.yaml 缺失 → 硬编码默认值兜底（两开关 false，接线 inert）"""
    print("\n── 场景 E：config.yaml 缺失兜底（patch _SEM_CONFIG_PATH 指向不存在路径）")
    with patch.object(Orchestrator, "_SEM_CONFIG_PATH", Path("C:/__nonexistent_task02__/config.yaml")):
        cfg = Orchestrator._load_learning_config()
    print(f"    config 缺失 → reflection_persist={cfg['reflection_persist']} "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}")
    assert cfg["reflection_persist"] is False, "config 缺失应回落硬编码默认 false"
    assert cfg["critic_evaluation_enabled"] is False, "config 缺失应回落硬编码默认 false"
    print("  [E] PASS: 默认值兜底生效（缺配置时接线 inert，不崩主链路）")


def main():
    print("=" * 68)
    print("TASK-02 配置生效验证（环境变量 > config.yaml > 默认值）")
    print("=" * 68)
    snap = _snapshot_env()
    try:
        scenario_a_config_yaml()
        scenario_b_env_override()
        scenario_c_env_restore()
        scenario_d_invalid_env()
        scenario_e_config_missing_fallback()
    finally:
        _restore_env(snap)  # 【不易】恢复用户环境，绝不残留

    # 恢复后最终确认（防脚本中途异常导致残留）
    cfg = Orchestrator._load_learning_config()
    print(f"\n  [终态] 环境变量已恢复，config.yaml 生效值："
          f"reflection_persist={cfg['reflection_persist']} "
          f"critic_evaluation_enabled={cfg['critic_evaluation_enabled']}")
    assert cfg["reflection_persist"] is True and cfg["critic_evaluation_enabled"] is True
    print("\n" + "=" * 68)
    print("全部场景 PASS：三层优先级正确，config.yaml 开关真实生效，重启后不会回退")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    sys.exit(main())
