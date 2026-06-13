#!/usr/bin/env python3
"""
安全配置自动诊断脚本

用于快速定位脱敏过滤器、加密解密、审计日志等安全功能的问题。

使用方法:
    python diagnose.py
    python diagnose.py --full  # 运行完整诊断（包括性能测试）
"""

import sys
import os
import subprocess
import json
from typing import Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def check_python_version() -> Tuple[bool, str]:
    """检查Python版本"""
    version = sys.version_info
    if version >= (3, 10):
        return True, f"✅ Python版本: {version.major}.{version.minor}.{version.micro}"
    else:
        return False, f"❌ Python版本不足: 需要3.10+, 当前{version.major}.{version.minor}"

def check_dependencies() -> List[Tuple[str, bool, str]]:
    """检查依赖库"""
    dependencies = [
        ("cryptography", "42.0.0", "加密库"),
        ("psutil", None, "系统监控库"),
    ]
    
    results = []
    for name, min_version, desc in dependencies:
        try:
            module = __import__(name)
            version = getattr(module, '__version__', 'unknown')
            
            if min_version and version < min_version:
                results.append((name, False, f"❌ {desc}: 版本不足 ({version} < {min_version})"))
            else:
                results.append((name, True, f"✅ {desc}: {version}"))
        except ImportError:
            results.append((name, False, f"❌ {desc}: 未安装"))
    
    return results

def check_encryption_key() -> Tuple[bool, str]:
    """检查加密密钥文件"""
    key_path = '.encryption_key'
    
    if not os.path.exists(key_path):
        return False, "❌ 密钥文件不存在"
    
    try:
        with open(key_path, 'rb') as f:
            key_data = f.read()
        
        if len(key_data) != 32:
            return False, f"❌ 密钥长度不正确: 期望32字节, 实际{len(key_data)}字节"
        
        # 检查文件权限
        try:
            import stat
            file_stat = os.stat(key_path)
            permissions = oct(file_stat.st_mode)[-4:]
            if permissions != '0600':
                return False, f"⚠️ 密钥文件权限建议设置为0600, 当前{permissions}"
        except:
            pass
        
        return True, "✅ 加密密钥文件正常"
    
    except Exception as e:
        return False, f"❌ 读取密钥文件失败: {e}"

def check_secure_config() -> Tuple[bool, str]:
    """检查加密配置文件"""
    config_path = '.secure_config.json'
    
    if not os.path.exists(config_path):
        return False, "⚠️ 加密配置文件不存在（首次运行时会自动创建）"
    
    try:
        with open(config_path, 'r') as f:
            content = f.read()
            if content:
                try:
                    json.loads(content)
                    return True, "✅ 加密配置文件格式正确"
                except json.JSONDecodeError:
                    return False, "❌ 加密配置文件格式错误"
            else:
                return True, "✅ 加密配置文件存在（空文件）"
    except Exception as e:
        return False, f"❌ 读取配置文件失败: {e}"

def test_sanitize_filter() -> Tuple[bool, str]:
    """测试脱敏过滤器"""
    try:
        from agent.logging_utils import SensitiveDataFilter
        
        filter = SensitiveDataFilter()
        
        test_cases = [
            ("password=secret123", 'password="***"'),
            ("api_key=sk-proj-abc123", 'api_key="***"'),
            ("13812345678", "138****5678"),
            ("110101199003071234", "110101********1234"),
        ]
        
        all_passed = True
        messages = []
        
        for input_val, expected in test_cases:
            result = filter._sanitize(input_val)
            if result == expected:
                messages.append(f"  ✅ {input_val[:20]}... -> {result}")
            else:
                messages.append(f"  ❌ {input_val[:20]}... -> {result} (期望: {expected})")
                all_passed = False
        
        if all_passed:
            return True, "\n".join(["✅ 脱敏过滤器测试通过"] + messages)
        else:
            return False, "\n".join(["❌ 脱敏过滤器测试失败"] + messages)
    
    except Exception as e:
        return False, f"❌ 加载脱敏过滤器失败: {type(e).__name__}: {e}"

def test_encryption() -> Tuple[bool, str]:
    """测试加密解密功能"""
    try:
        from config_secure import SecureConfigManager
        
        manager = SecureConfigManager()
        test_data = "test_secret_data"
        
        encrypted = manager.encrypt(test_data)
        decrypted = manager.decrypt(encrypted)
        
        if decrypted == test_data:
            return True, f"✅ 加密解密测试通过\n  加密: {encrypted[:20]}...\n  解密: {decrypted}"
        else:
            return False, f"❌ 解密结果不匹配\n  加密: {encrypted[:20]}...\n  解密: {decrypted} (期望: {test_data})"
    
    except Exception as e:
        return False, f"❌ 加密解密测试失败: {type(e).__name__}: {e}"

def test_audit_logger() -> Tuple[bool, str]:
    """测试审计日志功能"""
    try:
        from agent.logging_utils import get_audit_logger
        
        audit_logger = get_audit_logger()
        
        # 测试记录日志
        audit_logger.log_config_access('test_key', 'test_user')
        
        # 检查日志文件
        log_path = 'logs/audit.log'
        if os.path.exists(log_path):
            with open(log_path, 'r') as f:
                content = f.read()
                if 'CONFIG_ACCESS' in content:
                    return True, "✅ 审计日志功能正常"
                else:
                    return False, "❌ 审计日志未记录"
        else:
            return False, "⚠️ 审计日志文件不存在（首次记录时会创建）"
    
    except Exception as e:
        return False, f"❌ 审计日志测试失败: {type(e).__name__}: {e}"

def run_performance_test() -> Tuple[bool, str]:
    """运行性能测试"""
    try:
        from agent.logging_utils import SensitiveDataFilter
        import time
        
        filter = SensitiveDataFilter()
        test_text = "API Key: sk-proj-abc123 password=secret phone=13812345678"
        
        start = time.time()
        for _ in range(10000):
            filter._sanitize(test_text)
        elapsed = time.time() - start
        
        throughput = int(10000 / elapsed)
        
        if throughput > 10000:
            return True, f"✅ 性能测试通过\n  吞吐量: {throughput:,} 条/秒\n  延迟: {(elapsed/10000*1000):.4f} ms/条"
        else:
            return False, f"⚠️ 性能低于预期\n  吞吐量: {throughput:,} 条/秒 (期望>10000)"
    
    except Exception as e:
        return False, f"❌ 性能测试失败: {type(e).__name__}: {e}"

def check_log_files() -> List[Tuple[str, bool, str]]:
    """检查日志文件"""
    log_files = [
        ('logs/application.log', '应用日志'),
        ('logs/audit.log', '审计日志'),
        ('logs/error.log', '错误日志'),
    ]
    
    results = []
    for path, desc in log_files:
        if os.path.exists(path):
            size = os.path.getsize(path)
            results.append((path, True, f"✅ {desc}: {size} 字节"))
        else:
            results.append((path, False, f"⚠️ {desc}: 不存在"))
    
    return results

def main():
    """主函数"""
    print("=" * 70)
    print("          安全配置自动诊断工具")
    print("=" * 70)
    print(f"诊断时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    full_test = len(sys.argv) > 1 and sys.argv[1] == '--full'
    
    # 系统环境检查
    print("📋 系统环境检查")
    print("-" * 40)
    
    version_ok, version_msg = check_python_version()
    print(version_msg)
    
    print("\n📦 依赖库检查")
    print("-" * 40)
    for name, ok, msg in check_dependencies():
        print(msg)
    
    # 文件检查
    print("\n📁 文件检查")
    print("-" * 40)
    
    key_ok, key_msg = check_encryption_key()
    print(key_msg)
    
    config_ok, config_msg = check_secure_config()
    print(config_msg)
    
    print("\n📝 日志文件检查")
    print("-" * 40)
    for path, ok, msg in check_log_files():
        print(msg)
    
    # 功能测试
    print("\n🔧 功能测试")
    print("-" * 40)
    
    sanitize_ok, sanitize_msg = test_sanitize_filter()
    print(sanitize_msg)
    
    encrypt_ok, encrypt_msg = test_encryption()
    print(encrypt_msg)
    
    audit_ok, audit_msg = test_audit_logger()
    print(audit_msg)
    
    # 性能测试（完整模式）
    if full_test:
        print("\n⚡ 性能测试")
        print("-" * 40)
        perf_ok, perf_msg = run_performance_test()
        print(perf_msg)
    
    # 汇总
    print("\n" + "=" * 70)
    print("📊 诊断结果汇总")
    print("=" * 70)
    
    all_checks = [
        ("Python版本", version_ok),
        ("加密密钥", key_ok),
        ("配置文件", config_ok),
        ("脱敏过滤器", sanitize_ok),
        ("加密解密", encrypt_ok),
        ("审计日志", audit_ok),
    ]
    
    passed = sum(1 for _, ok in all_checks if ok)
    total = len(all_checks)
    
    print(f"通过: {passed}/{total}")
    print()
    
    if passed == total:
        print("🎉 所有检查通过！安全配置运行正常。")
        return 0
    else:
        print("⚠️ 部分检查未通过，请查看上面的详细信息。")
        return 1

if __name__ == '__main__':
    sys.exit(main())
