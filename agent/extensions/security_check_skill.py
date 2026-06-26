"""技能安装安全检查技能 — 允许用户手动触发安全检查

此技能提供以下功能：
1. 对即将安装的技能进行安全性评估
2. 分析与现有技能的兼容性
3. 生成详细的安全报告

使用方式：
- 直接调用：检查技能安全性
- 通过扩展管理器自动触发（安装前）
"""

import json
import logging
from typing import Dict, Any, Optional
from pathlib import Path

from agent.extensions.security_checker import get_security_checker

logger = logging.getLogger(__name__)


def _check_skill_security(source: str, skill_name: str = "", description: str = "") -> Dict[str, Any]:
    """检查技能的安全性和兼容性（内部函数，避免被pytest误识别）
    
    Args:
        source: 技能来源 (github:user/repo, url:..., local:...)
        skill_name: 技能名称
        description: 技能描述
    
    Returns:
        检查结果字典
    """
    checker = get_security_checker()
    
    skill_info = {
        "name": skill_name or source.split(":")[-1] if ":" in source else source,
        "description": description,
    }
    
    result = checker.perform_full_check(source, skill_info)
    
    # 生成可读报告
    report = format_report(result)
    result["report"] = report
    
    logger.info(f"[安全检查技能] 已完成技能检查: {skill_info['name']}")
    return result


def skill_security_check(source: str, skill_name: str = "", description: str = "") -> Dict[str, Any]:
    """检查技能的安全性和兼容性（公开接口）
    
    Args:
        source: 技能来源 (github:user/repo, url:..., local:...)
        skill_name: 技能名称
        description: 技能描述
    
    Returns:
        检查结果字典
    """
    return _check_skill_security(source, skill_name, description)


def format_report(result: Dict[str, Any]) -> str:
    """格式化检查结果为可读报告"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"技能安全检查报告")
    lines.append("=" * 60)
    lines.append(f"技能名称: {result['skill_name']}")
    lines.append(f"来源: {result['source']}")
    lines.append(f"检查时间: {result['timestamp']}")
    lines.append("")
    
    # 安全评估
    lines.append("【安全评估】")
    security = result["security"]
    lines.append(f"  安全等级: {security['level']}")
    lines.append(f"  安全分数: {security['score']}/100")
    
    if security["issues"]:
        lines.append("  发现问题:")
        for issue in security["issues"]:
            lines.append(f"    - [{issue['severity']}] {issue['category']}: {issue['message']}")
    else:
        lines.append("  未发现安全问题")
    
    if security["suggestions"]:
        lines.append("  建议:")
        for suggestion in security["suggestions"]:
            lines.append(f"    - {suggestion}")
    lines.append("")
    
    # 兼容性分析
    lines.append("【兼容性分析】")
    compatibility = result["compatibility"]
    lines.append(f"  兼容性: {'✓ 兼容' if compatibility['compatible'] else '✗ 不兼容'}")
    
    if compatibility["conflicts"]:
        lines.append("  冲突:")
        for conflict in compatibility["conflicts"]:
            lines.append(f"    - {conflict['reason']}: {conflict['existing_skill']} 与 {conflict['new_skill']}")
    
    if compatibility["overlaps"]:
        lines.append("  功能重叠:")
        for overlap in compatibility["overlaps"]:
            lines.append(f"    - {overlap['feature']}: {overlap['existing_skill']} 与 {overlap['new_skill']}")
    
    if compatibility["warnings"]:
        lines.append("  警告:")
        for warning in compatibility["warnings"]:
            lines.append(f"    - {warning}")
    lines.append("")
    
    # 结论
    lines.append("【结论】")
    if result["can_install"]:
        lines.append("  ✓ 可以安装此技能")
        if security["level"] == "WARNING":
            lines.append("  ⚠️  安装前建议仔细审查代码")
    else:
        lines.append("  ✗ 不建议安装此技能")
    
    lines.append("=" * 60)
    
    return "\n".join(lines)


def test_security_check_skill():
    """测试安全检查技能"""
    print("=" * 60)
    print("测试技能安全检查技能")
    print("=" * 60)
    
    # 测试1: 检查本地技能（仅来源检查，无代码扫描）
    print("\n1. 测试本地技能检查:")
    result = skill_security_check(
        "local:/test-skill",
        "本地测试技能",
        "简单的测试功能"
    )
    print(result["report"])
    # 本地来源会有建议但等级为 PASS
    assert result["security"]["level"] == "PASS", "无危险代码的技能应该通过检查"
    assert result["can_install"], "应该可以安装"
    print("  ✓ 本地技能检查通过")
    
    # 测试2: 检查安全技能
    print("\n2. 测试安全技能检查:")
    result = skill_security_check(
        "github:user/safe-skill",
        "安全技能",
        "提供友好的问候功能"
    )
    print(result["report"])
    assert result["can_install"], "安全技能应该可以安装"
    assert result["security"]["level"] == "PASS", "安全技能应该通过检查"
    print("  ✓ 安全技能检查通过")
    
    # 测试3: 测试带代码扫描的完整检查（危险代码）
    print("\n3. 测试危险代码扫描:")
    import tempfile
    import os
    from agent.extensions.security_checker import get_security_checker
    
    checker = get_security_checker()
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建包含危险代码的文件
        dangerous_file = os.path.join(temp_dir, "dangerous.py")
        with open(dangerous_file, "w") as f:
            f.write("""
import subprocess
subprocess.run("rm -rf /", shell=True)
""")
        
        skill_info = {
            "name": "代码危险技能",
            "description": "执行危险操作",
        }
        result = checker.perform_full_check("local:/test", skill_info, Path(temp_dir))
        report = format_report(result)
        print(report)
        assert not result["can_install"], "包含危险代码的技能应该被阻止安装"
        assert result["security"]["level"] == "BLOCK", "应该被标记为 BLOCK"
    print("  ✓ 危险代码扫描检查通过")
    
    # 测试4: 测试带代码扫描的完整检查（安全代码）
    print("\n4. 测试安全代码扫描:")
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建安全的代码文件
        safe_file = os.path.join(temp_dir, "safe.py")
        with open(safe_file, "w") as f:
            f.write("""
def greet(name):
    return f"Hello, {name}!"
""")
        
        skill_info = {
            "name": "安全代码技能",
            "description": "提供问候功能",
        }
        result = checker.perform_full_check("github:user/safe", skill_info, Path(temp_dir))
        report = format_report(result)
        print(report)
        assert result["can_install"], "安全代码的技能应该可以安装"
        assert result["security"]["level"] == "PASS", "应该被标记为 PASS"
    print("  ✓ 安全代码扫描检查通过")
    
    print("\n" + "=" * 60)
    print("✅ 所有安全检查技能测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    test_security_check_skill()