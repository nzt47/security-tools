"""TASK-02 配置生效验证：三层优先级（环境变量 > config.yaml > 默认值）

运行: python scripts/verify_task02_config_effective.py

覆盖场景（开关组合矩阵全量）：
  A. config.yaml 真实读取（不注入、不 mock 配置层）：
     reflection_persist=true / critic_evaluation_enabled=true / experience_persist=false
  B. 环境变量覆盖 > config.yaml（两开关均被 env=false 覆盖）
  C. 环境变量清除后回落到 config.yaml 值
  D. 非法环境变量值处理（reflection_persist / critic_evaluation_enabled 各一行）
  E. config.yaml 缺失 → 硬编码默认值兜底（两开关 false，接线 inert）
  F. config.yaml 组合矩阵：orchestrator 两开关 2×2 全组合
     (false,false)=全 inert / (false,true)=只评估不写 / (true,false)=只写不评 / (true,true)=全开
  G. experience_persist 环境变量覆盖（config.yaml=false 基础上：true/1/false/非法值）
  H. config.yaml 字符串值解析（运维误加引号："false"/"0" 必须判 False——bool("false")==True 陷阱）
  I. 环境变量边界：空串回落 config / 大写 / 带空格 / yes / 单开关部分覆盖

【不易】只读验证：不修改 config.yaml；环境变量在 finally 中恢复，不污染用户环境。
"""

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, ".")  # 保证仓库根目录可导入

from agent.orchestrator.orchestrator import Orchestrator
from planning.core import PlanningCore

logging.basicConfig(
    # 默认收敛既有 INFO 噪音，仅输出本脚本 PASS/FAIL；
    # 传 --verbose 时显示 orchestrator/planning 解析分支的真实 logger 打点（排查用）
    level=logging.INFO if "--verbose" in sys.argv else logging.ERROR,
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
    for env_key, cfg_key, label in [
        ("LEARNING_REFLECTION_PERSIST", "reflection_persist", "反思持久化"),
        ("CRITIC_EVALUATION_ENABLED", "critic_evaluation_enabled", "规则评估"),
    ]:
        os.environ[env_key] = "abc"
        cfg = Orchestrator._load_learning_config()
        os.environ.pop(env_key, None)
        print(f"    非法值 'abc' → {cfg_key}={cfg[cfg_key]}（{label}，按实现：不在 true/1/yes → False）")
        assert cfg[cfg_key] is False, f"{label} 非法值应视为 False"
    print("  [D] PASS: 两开关非法值均被安全处理（不抛异常），环境变量已恢复")


def scenario_f_config_combo_matrix():
    """F. config.yaml 组合矩阵：orchestrator 两开关 2×2 全组合（临时 yaml patch _SEM_CONFIG_PATH）

    不依赖真实 config.yaml 当前值（它固定为 true/true），用临时 yaml 穷举四种组合，
    覆盖"只评估不写"与"只写不评"两个现有脚本缺失的接线形态。
    """
    print("\n── 场景 F：config.yaml 组合矩阵（2×2 全组合，临时 yaml）")
    combos = [
        (False, False, "全 inert：不写不评（默认语义）"),
        (False, True, "只评估不写：critic=true + reflection_persist=false"),
        (True, False, "只写不评：reflection_persist=true + critic=false"),
        (True, True, "全开：写 + 评"),
    ]
    tmpdir = tempfile.mkdtemp(prefix="task02_combo_")
    try:
        for rp, ce, desc in combos:
            import yaml as _yaml
            cfg_path = Path(tmpdir) / "config.yaml"
            cfg_path.write_text(
                _yaml.safe_dump({
                    "learning": {"reflection_persist": rp},
                    "features": {"critic_evaluation_enabled": ce},
                }),
                encoding="utf-8",
            )
            with patch.object(Orchestrator, "_SEM_CONFIG_PATH", cfg_path):
                cfg = Orchestrator._load_learning_config()
            assert cfg["reflection_persist"] is rp, f"{desc}: reflection_persist 期望 {rp} 实际 {cfg['reflection_persist']}"
            assert cfg["critic_evaluation_enabled"] is ce, f"{desc}: critic 期望 {ce} 实际 {cfg['critic_evaluation_enabled']}"
            print(f"    ({rp}, {ce}) → ({cfg['reflection_persist']}, {cfg['critic_evaluation_enabled']})  {desc}  [OK]")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("  [F] PASS: 2×2 全组合读取正确（含只评估不写 / 只写不评）")


def scenario_g_experience_env_override():
    """G. experience_persist 环境变量覆盖（config.yaml=false 基础上，planning 侧）"""
    print("\n── 场景 G：experience_persist 环境变量覆盖（planning 侧）")
    for val, expected, desc in [
        ("true", True, "env=true → 开启落盘"),
        ("1", True, "env=1 → 开启落盘"),
        ("false", False, "env=false → 保持关闭"),
        ("abc", False, "env=非法值 → 视为 False"),
    ]:
        os.environ["LEARNING_EXPERIENCE_PERSIST"] = val
        got = PlanningCore._load_experience_persist_config()
        os.environ.pop("LEARNING_EXPERIENCE_PERSIST", None)
        assert got is expected, f"{desc}: 期望 {expected} 实际 {got}"
        print(f"    env={val!r} → experience_persist={got}  {desc}  [OK]")
    print("  [G] PASS: experience_persist 环境变量覆盖正确（true/1/false/非法值）")


def scenario_h_config_string_values():
    """H. config.yaml 字符串值解析（运维误加引号防御）

    修复 bool('false')==True 陷阱：字符串 "false"/"0" 必须判 False，
    否则关闭开关被误读为开启（违反"保留 config.yaml false 默认语义"不变式）。
    """
    print("\n── 场景 H：config.yaml 字符串值解析（误加引号防御）")
    import yaml as _yaml
    cases = [
        ("false", (False, False), '"false" 字符串 → 保持 false（bool() 陷阱修复）'),
        ("true", (True, True), '"true" 字符串 → 正确开启'),
        ("0", (False, False), '"0" 字符串 → false'),
        ("1", (True, True), '"1" 字符串 → true'),
        (" yes ", (True, True), '" yes " 字符串（去空格）→ true'),
    ]
    tmpdir = tempfile.mkdtemp(prefix="task02_strval_")
    try:
        for val, expected, desc in cases:
            cfg_path = Path(tmpdir) / "config.yaml"
            cfg_path.write_text(_yaml.safe_dump({
                "learning": {"reflection_persist": val},
                "features": {"critic_evaluation_enabled": val},
            }), encoding="utf-8")
            with patch.object(Orchestrator, "_SEM_CONFIG_PATH", cfg_path):
                cfg = Orchestrator._load_learning_config()
            got = (cfg["reflection_persist"], cfg["critic_evaluation_enabled"])
            assert got == expected, f"{desc}: 期望 {expected} 实际 {got}"
            print(f"    {desc} → {got}  [OK]")
        # planning 侧 config 路径硬编码不可 patch，直接断言两侧 helper 纯函数一致
        for val, exp in [("false", False), ("0", False), ("true", True), ("1", True),
                         (" yes ", True), (False, False), (True, True)]:
            assert Orchestrator._parse_bool_flag(val) is exp, f"orchestrator 解析 {val!r}"
            assert PlanningCore._parse_bool_flag(val) is exp, f"planning 解析 {val!r}"
        print("    _parse_bool_flag 纯函数：orchestrator/planning 双侧一致  [OK]")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("  [H] PASS: 字符串值安全解析（bool('false') 陷阱已修复）")


def scenario_i_env_edge_cases():
    """I. 环境变量边界：空串回落 / 大小写 / 带空格 / yes / 单开关部分覆盖"""
    print("\n── 场景 I：环境变量边界值")
    # 空字符串 → 不覆盖，回落 config.yaml 的 true
    os.environ["LEARNING_REFLECTION_PERSIST"] = ""
    cfg = Orchestrator._load_learning_config()
    os.environ.pop("LEARNING_REFLECTION_PERSIST", None)
    assert cfg["reflection_persist"] is True, "空串环境变量应回落 config 值（不覆盖）"
    print(f"    env='' → 回落 config.yaml → reflection_persist={cfg['reflection_persist']}  [OK]")
    # 大小写 / 空格 / yes 变体
    for val, expected, desc in [
        (" TRUE ", True, "大写+空格 → 归一化后判 true"),
        ("YES", True, "yes 大写变体"),
        ("False", False, "False 大写 → false"),
    ]:
        os.environ["LEARNING_REFLECTION_PERSIST"] = val
        cfg = Orchestrator._load_learning_config()
        os.environ.pop("LEARNING_REFLECTION_PERSIST", None)
        assert cfg["reflection_persist"] is expected, f"{desc}: 期望 {expected} 实际 {cfg['reflection_persist']}"
        print(f"    env={val!r} → reflection_persist={cfg['reflection_persist']}  {desc}  [OK]")
    # 单开关部分覆盖：只设 critic env，reflection 回落 config
    os.environ["CRITIC_EVALUATION_ENABLED"] = "false"
    cfg = Orchestrator._load_learning_config()
    os.environ.pop("CRITIC_EVALUATION_ENABLED", None)
    assert cfg["critic_evaluation_enabled"] is False and cfg["reflection_persist"] is True, \
        "部分覆盖：critic 被 env 覆盖，reflection 应回落 config"
    print(f"    部分覆盖（只设 CRITIC_EVALUATION_ENABLED=false）→ "
          f"critic={cfg['critic_evaluation_enabled']} reflection_persist={cfg['reflection_persist']}  [OK]")
    # 反向部分覆盖：只设 reflection env，critic 回落 config
    os.environ["LEARNING_REFLECTION_PERSIST"] = "false"
    cfg = Orchestrator._load_learning_config()
    os.environ.pop("LEARNING_REFLECTION_PERSIST", None)
    assert cfg["reflection_persist"] is False and cfg["critic_evaluation_enabled"] is True, \
        "反向部分覆盖：reflection 被 env 覆盖，critic 应回落 config"
    print(f"    反向部分覆盖（只设 LEARNING_REFLECTION_PERSIST=false）→ "
          f"reflection_persist={cfg['reflection_persist']} critic={cfg['critic_evaluation_enabled']}  [OK]")
    print("  [I] PASS: 环境变量边界全覆盖（空串/大小写/空格/双向部分覆盖）")


def scenario_j_config_structure_edge():
    """J. config.yaml 结构边界：段缺失 / 空文件 / 非法内容 / 数字值"""
    print("\n── 场景 J：config.yaml 结构边界")
    tmpdir = tempfile.mkdtemp(prefix="task02_struct_")
    try:
        cases = [
            # (yaml 内容, 期望 (rp, ce), 描述)
            ("learning:\n  reflection_persist: true\n", (True, False),
             "仅 learning 段（features 缺失 → critic 保持默认 false）"),
            ("features:\n  critic_evaluation_enabled: true\n", (False, True),
             "仅 features 段（learning 缺失 → reflection 保持默认 false）"),
            ("", (False, False),
             "空文件 → 全默认（safe_load 返回 None → {}）"),
            ("hello", (False, False),
             "顶层非 dict（safe_load 返回 str → .get 抛错 → except 兜底默认，不崩链路）"),
            ("learning:\n  reflection_persist: 1\nfeatures:\n  critic_evaluation_enabled: 0\n", (True, False),
             "数字值：yaml 1（int）→ true，0（int）→ false（_parse_bool_flag 走字符串归一化）"),
        ]
        for content, expected, desc in cases:
            cfg_path = Path(tmpdir) / "config.yaml"
            cfg_path.write_text(content, encoding="utf-8")
            with patch.object(Orchestrator, "_SEM_CONFIG_PATH", cfg_path):
                cfg = Orchestrator._load_learning_config()
            got = (cfg["reflection_persist"], cfg["critic_evaluation_enabled"])
            assert got == expected, f"{desc}: 期望 {expected} 实际 {got}"
            print(f"    {desc} → {got}  [OK]")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    print("  [J] PASS: 结构边界全覆盖（段缺失/空文件/非法内容/数字值）")


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
        scenario_f_config_combo_matrix()
        scenario_g_experience_env_override()
        scenario_h_config_string_values()
        scenario_i_env_edge_cases()
        scenario_j_config_structure_edge()
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
