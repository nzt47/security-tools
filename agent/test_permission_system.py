"""
PermissionSystem 危险关键词检测测试

测试 check_text 方法对各种危险操作的拦截能力
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.permission_system import PermissionSystem


def test_critical_keywords():
    """测试 critical 级别危险关键词拦截"""
    ps = PermissionSystem()
    
    test_cases = [
        ("rm -rf /", "critical", "递归删除根目录"),
        ("rm -rf /var/log/*", "critical", "递归删除系统日志"),
        ("format C: /fs:ntfs", "critical", "格式化系统盘"),
        ("dd if=/dev/zero of=/dev/sda", "critical", "直接写入磁盘设备"),
        (":(){ :|:& };:", "critical", "Fork炸弹"),
        ("rm -rf /home/user/*", "critical", "递归删除用户目录"),
        ("format D: /q", "critical", "快速格式化"),
        ("dd if=backup.img of=/dev/sdb", "critical", "写入备份到磁盘"),
    ]
    
    print("\n" + "=" * 70)
    print("[CRITICAL] 测试 Critical 级别危险关键词拦截")
    print("=" * 70)
    
    all_passed = True
    for text, expected_level, description in test_cases:
        result = ps.check_text(text)
        passed = result["level"] == expected_level and not result["safe"]
        
        status = "[PASS]" if passed else "[FAIL]"
        print(f"\n{status} {description}")
        print(f"   Input: {text}")
        print(f"   Expected: {expected_level}, Actual: {result['level']}")
        print(f"   Safe: {result['safe']}")
        
        if result["matches"]:
            print(f"   Matched rules:")
            for match in result["matches"]:
                print(f"      - {match['description']} ({match['category']})")
        
        if not passed:
            all_passed = False
    
    return all_passed


def test_warning_keywords():
    """测试 warning 级别危险关键词检测"""
    ps = PermissionSystem()
    
    test_cases = [
        ("rm -r /tmp/*", "warning", "递归删除临时文件"),
        ("format floppy", "warning", "格式化软盘"),
        ("shutdown -h now", "warning", "关闭系统"),
        ("reboot -f", "warning", "强制重启"),
        ("reg delete HKLM\\Software\\Test", "warning", "删除注册表"),
        ("chmod 777 /home", "warning", "过度开放权限"),
        ("rm -rf ./cache", "warning", "递归删除缓存"),
    ]
    
    print("\n" + "=" * 70)
    print("[WARNING] 测试 Warning 级别危险关键词检测")
    print("=" * 70)
    
    all_passed = True
    for text, expected_level, description in test_cases:
        result = ps.check_text(text)
        passed = result["level"] == expected_level and not result["safe"]
        
        status = "[PASS]" if passed else "[FAIL]"
        print(f"\n{status} {description}")
        print(f"   Input: {text}")
        print(f"   Expected: {expected_level}, Actual: {result['level']}")
        print(f"   Safe: {result['safe']} (warning级别应为False)")
        
        if result["matches"]:
            print(f"   Matched rules:")
            for match in result["matches"]:
                print(f"      - {match['description']} ({match['category']})")
        
        if not passed:
            all_passed = False
    
    return all_passed


def test_safe_text():
    """测试安全文本不被拦截"""
    ps = PermissionSystem()
    
    safe_cases = [
        ("Hello, how are you?", "English greeting"),
        ("Write a Python function for Fibonacci", "Code generation"),
        ("rmdir empty_folder", "Delete empty folder"),
        ("git status", "Git status query"),
        ("ls -la", "List files"),
        ("echo hello", "Print output"),
        ("cat readme.txt", "Read file"),
        ("mkdir new_project", "Create directory"),
    ]
    
    print("\n" + "=" * 70)
    print("[SAFE] 测试安全文本（不应拦截）")
    print("=" * 70)
    
    all_passed = True
    for text, description in safe_cases:
        result = ps.check_text(text)
        passed = result["safe"] and result["level"] == "safe"
        
        status = "[PASS]" if passed else "[FAIL]"
        print(f"\n{status} {description}")
        print(f"   Input: {text}")
        print(f"   Level: {result['level']}")
        print(f"   Safe: {result['safe']}")
        
        if result["matches"]:
            print(f"   [WARN] False positive:")
            for match in result["matches"]:
                print(f"      - {match['description']}")
        
        if not passed:
            all_passed = False
    
    return all_passed


def test_edge_cases():
    """测试边界情况"""
    ps = PermissionSystem()
    
    print("\n" + "=" * 70)
    print("[EDGE] 测试边界情况")
    print("=" * 70)
    
    all_passed = True
    
    # 空文本
    result = ps.check_text("")
    passed = result["safe"] and result["level"] == "safe"
    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n{status} Empty text")
    print(f"   Result: safe={result['safe']}, level={result['level']}")
    if not passed:
        all_passed = False
    
    # None
    result = ps.check_text(None)
    passed = result["safe"] and result["level"] == "safe"
    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n{status} None input")
    print(f"   Result: safe={result['safe']}, level={result['level']}")
    if not passed:
        all_passed = False
    
    # 大小写混合
    result = ps.check_text("RM -RF /")
    passed = result["level"] == "critical"
    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n{status} Case insensitive (RM -RF /)")
    print(f"   Result: level={result['level']}")
    if not passed:
        all_passed = False
    
    # 包含空格的危险命令
    result = ps.check_text("rm  -rf  /")
    passed = result["level"] == "critical"
    status = "[PASS]" if passed else "[FAIL]"
    print(f"\n{status} Extra spaces (rm  -rf  /)")
    print(f"   Result: level={result['level']}")
    if not passed:
        all_passed = False
    
    return all_passed


def test_alert_recording():
    """测试告警记录功能"""
    ps = PermissionSystem()
    
    print("\n" + "=" * 70)
    print("[ALERTS] 测试告警记录功能")
    print("=" * 70)
    
    # 触发几个危险操作
    ps.check_text("rm -rf /")  # critical
    ps.check_text("format C:")  # warning
    ps.check_text("git status")  # safe
    
    alerts = ps.get_alerts(limit=10)
    print(f"\n[INFO] Total alerts: {len(alerts)}")
    
    for i, alert in enumerate(alerts, 1):
        print(f"\n   Alert #{i}:")
        print(f"      Time: {alert['timestamp']}")
        print(f"      Level: {alert['level']}")
        print(f"      Text: {alert['text']}")
    
    stats = ps.get_security_stats()
    print(f"\n[STATS] Security Statistics:")
    print(f"   Blocked count: {stats['blocked_count']} (critical级别)")
    print(f"   Warned count: {stats['warned_count']} (warning级别)")
    print(f"   Total alerts: {stats['total_alerts']}")
    print(f"   Keywords loaded: critical={stats['keywords_loaded']['critical']}, warning={stats['keywords_loaded']['warning']}")
    
    # 验证：至少应该有1个critical告警
    return stats["blocked_count"] >= 1


def run_all_tests():
    """运行所有测试"""
    print("\n")
    print("=" * 70)
    print(">>> PermissionSystem Danger Keyword Detection - Full Test Suite <<<")
    print("=" * 70)
    
    results = {
        "critical": test_critical_keywords(),
        "warning": test_warning_keywords(),
        "safe": test_safe_text(),
        "edge": test_edge_cases(),
        "alerts": test_alert_recording(),
    }
    
    print("\n" + "=" * 70)
    print("[SUMMARY] 测试结果汇总")
    print("=" * 70)
    
    for test_name, passed in results.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"   {test_name:12s}: {status}")
    
    all_passed = all(results.values())
    print("\n" + "=" * 70)
    if all_passed:
        print("[SUCCESS] All tests passed! PermissionSystem danger keyword detection is working!")
    else:
        print("[ERROR] Some tests failed, please check the failed cases above")
    print("=" * 70)
    
    return all_passed


if __name__ == "__main__":
    run_all_tests()
