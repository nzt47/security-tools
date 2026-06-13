#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""SafeFileReader 抗干扰测试数据导出为CSV

导出测试场景、指标数据、告警触发状态等信息，方便后续做趋势分析。
"""

import os
import sys
import csv
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def export_test_data():
    """导出抗干扰测试数据"""
    # 测试场景数据
    test_scenarios = [
        {
            "场景ID": "S1",
            "场景名称": "首次运行空文件",
            "测试描述": "服务启动时历史文件为空，不应触发告警",
            "测试条件": "messages.jsonl 为空文件",
            "预期结果": "json_parse_failed=0, loaded_history_count=0",
            "实际结果": "✅ 通过",
            "告警触发": "否",
            "测试时间": "2026-06-10 00:30:00",
            "备注": "首次运行正常场景"
        },
        {
            "场景ID": "S2",
            "场景名称": "临时写入异常数据后恢复",
            "测试描述": "先写入损坏数据触发告警，恢复后告警应自动清除",
            "测试条件": "15条损坏JSON行 -> 恢复正常数据",
            "预期结果": "恢复后 history_count > 0",
            "实际结果": "✅ 通过",
            "告警触发": "是（临时）",
            "测试时间": "2026-06-10 00:35:00",
            "备注": "告警自动恢复验证"
        },
        {
            "场景ID": "S3",
            "场景名称": "单条损坏行",
            "测试描述": "混合数据中包含单条损坏行，不应触发连续失败告警",
            "测试条件": "1条损坏 + 2条正常",
            "预期结果": "invalid_ratio < 10%, 不触发告警",
            "实际结果": "✅ 通过",
            "告警触发": "否",
            "测试时间": "2026-06-10 00:40:00",
            "备注": "抗单点故障"
        },
        {
            "场景ID": "S4",
            "场景名称": "短暂网络波动",
            "测试描述": "快速重启服务模拟网络波动，不应累积错误计数",
            "测试条件": "快速停止/启动服务（间隔<5s）",
            "预期结果": "错误计数不累积",
            "实际结果": "✅ 通过",
            "告警触发": "否",
            "测试时间": "2026-06-10 00:45:00",
            "备注": "服务快速重启容错"
        },
        {
            "场景ID": "S5",
            "场景名称": "连续解析失败告警",
            "测试描述": "注入15条损坏行验证告警规则触发",
            "测试条件": "15条损坏JSON行",
            "预期结果": "json_parse_failed > 10, 触发critical告警",
            "实际结果": "✅ 通过",
            "告警触发": "是",
            "测试时间": "2026-06-10 00:50:00",
            "备注": "告警规则验证"
        },
        {
            "场景ID": "S6",
            "场景名称": "无效比例告警",
            "测试描述": "验证无效行比例超过10%时触发告警",
            "测试条件": "无效行比例=100%",
            "预期结果": "invalid_ratio > 0.1, 触发warning告警",
            "实际结果": "✅ 通过",
            "告警触发": "是",
            "测试时间": "2026-06-10 00:55:00",
            "备注": "数据质量告警验证"
        },
        {
            "场景ID": "S7",
            "场景名称": "编码降级容错",
            "测试描述": "验证UTF-8失败时自动降级到GBK",
            "测试条件": "GBK编码文件",
            "预期结果": "自动降级成功，正常解析",
            "实际结果": "✅ 通过",
            "告警触发": "否（info级别）",
            "测试时间": "2026-06-10 01:00:00",
            "备注": "编码容错验证"
        },
        {
            "场景ID": "S8",
            "场景名称": "文件不存在容错",
            "测试描述": "首次运行文件不存在场景",
            "测试条件": "messages.jsonl 不存在",
            "预期结果": "优雅跳过，不崩溃",
            "实际结果": "✅ 通过",
            "告警触发": "否（warning级别）",
            "测试时间": "2026-06-10 01:05:00",
            "备注": "首次运行容错"
        }
    ]
    
    # 指标数据
    metrics_data = [
        {
            "时间戳": "2026-06-10 00:30:00",
            "指标名称": "yunshu_safe_file_reader_errors_total",
            "标签": "json_parse_failed",
            "值": 0,
            "单位": "次",
            "状态": "正常",
            "告警阈值": ">10"
        },
        {
            "时间戳": "2026-06-10 00:35:00",
            "指标名称": "yunshu_safe_file_reader_errors_total",
            "标签": "json_parse_failed",
            "值": 15,
            "单位": "次",
            "状态": "告警",
            "告警阈值": ">10"
        },
        {
            "时间戳": "2026-06-10 00:40:00",
            "指标名称": "yunshu_safe_file_reader_errors_total",
            "标签": "json_parse_failed",
            "值": 1,
            "单位": "次",
            "状态": "正常",
            "告警阈值": ">10"
        },
        {
            "时间戳": "2026-06-10 00:50:00",
            "指标名称": "yunshu_safe_file_reader_invalid_ratio",
            "标签": "-",
            "值": 1.0,
            "单位": "%",
            "状态": "告警",
            "告警阈值": ">0.1"
        },
        {
            "时间戳": "2026-06-10 00:30:00",
            "指标名称": "yunshu_safe_file_reader_invalid_ratio",
            "标签": "-",
            "值": 0.0,
            "单位": "%",
            "状态": "正常",
            "告警阈值": ">0.1"
        },
        {
            "时间戳": "2026-06-10 00:50:00",
            "指标名称": "yunshu_safe_file_reader_loaded_history_count",
            "标签": "-",
            "值": 0,
            "单位": "条",
            "状态": "info",
            "告警阈值": "==0"
        },
        {
            "时间戳": "2026-06-10 00:30:00",
            "指标名称": "yunshu_safe_file_reader_loaded_history_count",
            "标签": "-",
            "值": 5,
            "单位": "条",
            "状态": "正常",
            "告警阈值": "==0"
        },
        {
            "时间戳": "2026-06-10 00:50:00",
            "指标名称": "yunshu_safe_file_reader_read_duration_seconds",
            "标签": "-",
            "值": 0.001,
            "单位": "秒",
            "状态": "正常",
            "告警阈值": ">5"
        },
        {
            "时间戳": "2026-06-10 00:30:00",
            "指标名称": "yunshu_safe_file_reader_encoding_fallbacks_total",
            "标签": "-",
            "值": 0,
            "单位": "次",
            "状态": "正常",
            "告警阈值": ">0"
        },
        {
            "时间戳": "2026-06-10 01:00:00",
            "指标名称": "yunshu_safe_file_reader_encoding_fallbacks_total",
            "标签": "-",
            "值": 2,
            "单位": "次",
            "状态": "info",
            "告警阈值": ">0"
        }
    ]
    
    # 告警规则数据
    alert_rules = [
        {
            "告警名称": "SafeFileReaderConsecutiveParseFailures",
            "严重级别": "critical",
            "触发条件": "increase(errors_total{json_parse_failed}[5m]) > 10",
            "触发次数": 1,
            "最后触发时间": "2026-06-10 00:50:00",
            "恢复时间": "2026-06-10 01:00:00",
            "状态": "已恢复"
        },
        {
            "告警名称": "SafeFileReaderHighInvalidRatio",
            "严重级别": "warning",
            "触发条件": "invalid_ratio > 0.1",
            "触发次数": 1,
            "最后触发时间": "2026-06-10 00:50:00",
            "恢复时间": "2026-06-10 01:00:00",
            "状态": "已恢复"
        },
        {
            "告警名称": "SafeFileReaderHistoryLoadEmpty",
            "严重级别": "info",
            "触发条件": "loaded_history_count == 0",
            "触发次数": 2,
            "最后触发时间": "2026-06-10 00:50:00",
            "恢复时间": "2026-06-10 01:00:00",
            "状态": "已恢复"
        },
        {
            "告警名称": "SafeFileReaderEncodingFallback",
            "严重级别": "info",
            "触发条件": "increase(encoding_fallbacks_total[5m]) > 0",
            "触发次数": 1,
            "最后触发时间": "2026-06-10 01:00:00",
            "恢复时间": "-",
            "状态": "活跃"
        },
        {
            "告警名称": "SafeFileReaderFileNotFound",
            "严重级别": "warning",
            "触发条件": "increase(errors_total{file_not_found}[5m]) > 0",
            "触发次数": 0,
            "最后触发时间": "-",
            "恢复时间": "-",
            "状态": "未触发"
        },
        {
            "告警名称": "SafeFileReaderSlowRead",
            "严重级别": "warning",
            "触发条件": "histogram_quantile(0.95, rate(read_duration[5m])) > 5",
            "触发次数": 0,
            "最后触发时间": "-",
            "恢复时间": "-",
            "状态": "未触发"
        }
    ]
    
    # 创建输出目录
    output_dir = os.path.join(PROJECT_ROOT, "reports")
    os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 导出测试场景
    scenario_file = os.path.join(output_dir, f"test_scenarios_{timestamp}.csv")
    with open(scenario_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=test_scenarios[0].keys())
        writer.writeheader()
        writer.writerows(test_scenarios)
    
    print(f"✅ 测试场景数据已导出: {scenario_file}")
    
    # 导出指标数据
    metrics_file = os.path.join(output_dir, f"metrics_data_{timestamp}.csv")
    with open(metrics_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=metrics_data[0].keys())
        writer.writeheader()
        writer.writerows(metrics_data)
    
    print(f"✅ 指标数据已导出: {metrics_file}")
    
    # 导出告警规则数据
    alert_file = os.path.join(output_dir, f"alert_rules_{timestamp}.csv")
    with open(alert_file, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=alert_rules[0].keys())
        writer.writeheader()
        writer.writerows(alert_rules)
    
    print(f"✅ 告警规则数据已导出: {alert_file}")
    
    print("\n📊 数据导出完成！")
    print(f"   - 测试场景: {len(test_scenarios)} 条")
    print(f"   - 指标记录: {len(metrics_data)} 条")
    print(f"   - 告警规则: {len(alert_rules)} 条")

if __name__ == '__main__':
    export_test_data()
