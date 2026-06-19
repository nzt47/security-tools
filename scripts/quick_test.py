#!/usr/bin/env python3
"""
简单测试脚本 - 验证脱敏功能
"""
from agent.security_utils import DataSanitizer

sanitizer = DataSanitizer()

# 测试1: API Key
text1 = "API Key=sk-abcdefghijklmnopqrstuv123456"
result1 = sanitizer.sanitize_string(text1)
print(f"Test 1: {result1}")
print(f"  Expected '[REDACTED]' in result: {'[REDACTED]' in result1}")
print(f"  API Key still visible: {'sk-' in result1}")
print()

# 测试2: password
text2 = 'password="MyPassword123"'
result2 = sanitizer.sanitize_string(text2)
print(f"Test 2: {result2}")
print(f"  Expected '[REDACTED]' in result: {'[REDACTED]' in result2}")
print(f"  Password still visible: {'MyPassword' in result2}")
print()

# 测试3: email
text3 = "联系邮箱: user@example.com"
result3 = sanitizer.sanitize_string(text3)
print(f"Test 3: {result3}")
print(f"  Expected '[REDACTED]' in result: {'[REDACTED]' in result3}")
print(f"  Email still visible: {'user@example.com' in result3}")
print()

# 测试4: phone
text4 = "手机号: 13812345678"
result4 = sanitizer.sanitize_string(text4)
print(f"Test 4: {result4}")
print(f"  Expected '[REDACTED]' in result: {'[REDACTED]' in result4}")
print(f"  Phone still visible: {'13812345678' in result4}")
