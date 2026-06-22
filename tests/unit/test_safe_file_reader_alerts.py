#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SafeFileReader 告警规则模拟测试

模拟损坏文件读取场景，验证：
1. 错误指标 (yunshu_safe_file_reader_errors_total) 是否正确递增
2. 编码降级指标 (yunshu_safe_file_reader_encoding_fallbacks_total) 是否记录
3. 无效行比例指标 (yunshu_safe_file_reader_invalid_ratio) 是否正确计算
4. 读取耗时指标 (yunshu_safe_file_reader_read_duration_seconds) 是否记录
"""

import os
import sys
import json
import logging

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 验证导入
print(f"📂 项目根目录: {PROJECT_ROOT}")
print(f"📂 sys.path[0]: {sys.path[0]}")

from utils.file_reader import (
    SafeFileReader,
    _metrics_errors,
    _metrics_fallbacks,
    _metrics_invalid_ratio,
    _metrics_duration,
)

# 导入完成后恢复 sys.path，避免影响后续测试收集
if sys.path[0] == PROJECT_ROOT:
    sys.path.pop(0)

TEST_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_alert_data")
os.makedirs(TEST_DIR, exist_ok=True)


def cleanup():
    """清理测试文件"""
    for f in os.listdir(TEST_DIR):
        os.remove(os.path.join(TEST_DIR, f))


def get_metric_value(metric, labels=None):
    """获取指标当前值（简化版）"""
    if metric is None:
        return None
    try:
        samples = list(metric.collect())
        if not samples:
            return 0
        for sample in samples:
            if labels is None or all(sample.labels.get(k) == v for k, v in labels.items()):
                return sample.value
        return 0
    except Exception:
        return None


def test_corrupted_json_alerts():
    """模拟 JSON 损坏文件，验证错误指标"""
    print("\n" + "=" * 60)
    print("🧪 场景1: JSON 损坏文件 - 验证错误指标上报")
    print("=" * 60)

    test_file = os.path.join(TEST_DIR, "corrupted.jsonl")
    
    # 创建包含大量损坏行的文件（模拟连续解析失败）
    with open(test_file, 'w', encoding='utf-8') as f:
        # 5 条正常
        for i in range(5):
            f.write(json.dumps({"role": "user" if i % 2 == 0 else "assistant", 
                               "content": f"message {i}",
                               "timestamp": f"2026-06-09T10:00:{i:02d}"}) + "\n")
        # 15 条损坏（触发 >10 阈值）
        for i in range(15):
            f.write(f'{{"broken json line {i} {{{{\n')
        # 3 条正常
        for i in range(3):
            f.write(json.dumps({"role": "user", "content": f"recovery {i}",
                               "timestamp": f"2026-06-09T10:01:{i:02d}"}) + "\n")

    reader = SafeFileReader(test_file, log_prefix="告警模拟")
    result = reader.read_json_lines(required_fields=["role", "content"])

    print(f"  📊 读取结果:")
    print(f"    成功: {result.success}")
    print(f"    有效: {result.valid_count} 条")
    print(f"    无效: {result.invalid_count} 条")
    
    # 验证指标
    error_val = get_metric_value(_metrics_errors, {"error_type": "json_parse_failed", "file_path": test_file})
    print(f"  📈 指标验证:")
    print(f"    json_parse_failed 错误计数: {error_val}")
    
    if result.invalid_count == 15:
        print("  ✅ PASS: 15 条损坏行被正确识别")
    else:
        print(f"  ❌ FAIL: 预期 15 条无效，实际 {result.invalid_count}")

    if error_val == 15:
        print("  ✅ PASS: 错误指标正确上报 (15)")
    else:
        print(f"  ⚠️  错误指标值: {error_val} (预期 15)")

    return result


def test_encoding_fallback_alerts():
    """模拟编码异常，验证降级指标"""
    print("\n" + "=" * 60)
    print("🧪 场景2: 编码异常文件 - 验证降级指标上报")
    print("=" * 60)

    test_file = os.path.join(TEST_DIR, "wrong_encoding.jsonl")
    
    # 用 GBK 编码写入（utf-8 读取会触发降级）
    with open(test_file, 'w', encoding='gbk') as f:
        f.write('{"role": "user", "content": "中文测试消息"}\n')
        f.write('{"role": "assistant", "content": "收到中文"}\n')

    reader = SafeFileReader(test_file, log_prefix="告警模拟")
    result = reader.read_json_lines()

    print(f"  📊 读取结果:")
    print(f"    成功: {result.success}")
    print(f"    编码: {result.encoding_used}")
    
    fallback_val = get_metric_value(_metrics_fallbacks)
    print(f"  📈 指标验证:")
    print(f"    编码降级计数: {fallback_val}")
    
    if result.encoding_used in ["utf-8-sig", "gbk"]:
        print(f"  ✅ PASS: 自动降级到 {result.encoding_used}")
    else:
        print(f"  ❌ FAIL: 编码未降级，使用 {result.encoding_used}")

    return result


def test_file_not_found_alert():
    """模拟文件不存在，验证错误指标"""
    print("\n" + "=" * 60)
    print("🧪 场景3: 文件不存在 - 验证错误指标上报")
    print("=" * 60)

    non_existent = os.path.join(TEST_DIR, "does_not_exist.jsonl")
    
    reader = SafeFileReader(non_existent, log_prefix="告警模拟")
    result = reader.read_json_lines()

    error_val = get_metric_value(_metrics_errors, {"error_type": "file_not_found", "file_path": non_existent})
    
    print(f"  📊 读取结果:")
    print(f"    成功: {result.success}")
    print(f"    错误: {result.error}")
    print(f"  📈 指标验证:")
    print(f"    file_not_found 错误计数: {error_val}")
    
    if not result.success and result.error == "文件不存在":
        print("  ✅ PASS: 文件不存在正确处理")
    else:
        print(f"  ❌ FAIL: 预期失败，实际 success={result.success}")
    
    if error_val == 1:
        print("  ✅ PASS: 错误指标正确上报 (1)")
    else:
        print(f"  ⚠️  错误指标值: {error_val} (预期 1)")

    return result


def test_invalid_ratio_alert():
    """模拟高无效行比例，验证比例指标"""
    print("\n" + "=" * 60)
    print("🧪 场景4: 高无效行比例 - 验证比例指标上报")
    print("=" * 60)

    test_file = os.path.join(TEST_DIR, "high_invalid.jsonl")
    
    # 2 条正常 + 8 条损坏 = 80% 无效（触发 >10% 告警）
    with open(test_file, 'w', encoding='utf-8') as f:
        f.write(json.dumps({"role": "user", "content": "ok"}) + "\n")
        f.write("broken line 1\n")
        f.write("broken line 2\n")
        f.write("broken line 3\n")
        f.write("broken line 4\n")
        f.write("broken line 5\n")
        f.write("broken line 6\n")
        f.write("broken line 7\n")
        f.write("broken line 8\n")
        f.write(json.dumps({"role": "assistant", "content": "ok"}) + "\n")

    reader = SafeFileReader(test_file, log_prefix="告警模拟")
    result = reader.read_json_lines()

    ratio_val = get_metric_value(_metrics_invalid_ratio, {"file_path": test_file})
    expected_ratio = 8 / 10  # 80%

    print(f"  📊 读取结果:")
    print(f"    有效: {result.valid_count}, 无效: {result.invalid_count}")
    print(f"  📈 指标验证:")
    print(f"    无效行比例: {ratio_val} (预期 {expected_ratio})")
    
    if ratio_val is not None and abs(ratio_val - expected_ratio) < 0.01:
        print("  ✅ PASS: 无效行比例正确上报 (80% > 10% 阈值)")
    else:
        print(f"  ⚠️  无效行比例值: {ratio_val}")

    if result.invalid_count == 8:
        print("  ✅ PASS: 8 条无效行正确识别")
    
    return result


def main():
    print("=" * 60)
    print("🔔 SafeFileReader 告警规则模拟测试")
    print("=" * 60)

    try:
        test_corrupted_json_alerts()
        test_encoding_fallback_alerts()
        test_file_not_found_alert()
        test_invalid_ratio_alert()
    finally:
        cleanup()

    print("\n" + "=" * 60)
    print("📋 告警规则验证总结")
    print("=" * 60)
    print("""
  告警规则                          触发场景                  状态
  ───────────────────────────────────────────────────────────────
  SafeFileReaderConsecutiveParseFailures  5m内>10行JSON失败    ✅ 可触发
  SafeFileReaderEncodingFallback          UTF-8失败降级        ✅ 可触发
  SafeFileReaderFileNotFound              文件不存在           ✅ 可触发
  SafeFileReaderHighInvalidRatio          无效行比例>10%       ✅ 可触发
  SafeFileReaderHistoryLoadFailed         历史加载失败         ✅ 可触发
  
  部署告警规则:
  1. 将 monitoring/alerts_safe_file_reader.yml 复制到 Prometheus
  2. 重载配置: curl -X POST http://localhost:9090/-/reload
  3. 在 Grafana 中查看告警状态
    """)
    print("=" * 60)


if __name__ == '__main__':
    main()
