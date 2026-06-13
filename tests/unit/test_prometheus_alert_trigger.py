#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""模拟连续解析失败场景，验证 Prometheus 告警触发

模拟步骤：
1. 向 messages.jsonl 注入 15 条损坏行
2. 重启服务触发历史加载
3. 检查 /metrics 端点确认 json_parse_failed > 10
4. 验证告警规则 SafeFileReaderConsecutiveParseFailures 满足触发条件
5. 恢复原始文件，重启服务
"""

import os
import sys
import json
import time
import logging
import subprocess
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.path.join(PROJECT_ROOT, "data")
MESSAGES_FILE = os.path.join(DATA_DIR, "messages.jsonl")
BACKUP_FILE = os.path.join(DATA_DIR, "messages.jsonl.bak_alert_test")
SERVER_URL = "http://127.0.0.1:5678"
METRICS_URL = f"{SERVER_URL}/metrics"

DAMAGED_LINES = 15  # 超过告警阈值 10


def backup_file():
    """备份原始文件"""
    if os.path.exists(MESSAGES_FILE):
        import shutil
        shutil.copy2(MESSAGES_FILE, BACKUP_FILE)
        logger.info("✅ 原始文件已备份: %s", BACKUP_FILE)
    else:
        logger.warning("⚠️ 原始文件不存在，跳过备份")


def restore_file():
    """恢复原始文件"""
    if os.path.exists(BACKUP_FILE):
        import shutil
        shutil.copy2(BACKUP_FILE, MESSAGES_FILE)
        os.remove(BACKUP_FILE)
        logger.info("✅ 原始文件已恢复")
    else:
        logger.warning("⚠️ 无备份文件，跳过恢复")


def inject_damaged_lines():
    """注入损坏行"""
    logger.info("🔧 注入 %d 条损坏行到 %s", DAMAGED_LINES, MESSAGES_FILE)
    
    # 读取现有内容
    with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 追加损坏行
    damaged = ['{"broken json line %d {{{{\n' % i for i in range(DAMAGED_LINES)]
    
    with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
        f.writelines(lines)
        f.writelines(damaged)
    
    logger.info("✅ 已注入 %d 条损坏行（总计 %d 行）", DAMAGED_LINES, len(lines) + DAMAGED_LINES)


def check_metrics_for_alert():
    """检查 /metrics 端点，确认告警条件是否满足"""
    logger.info("🔍 检查 /metrics 端点...")
    
    try:
        resp = requests.get(METRICS_URL, timeout=5)
        if resp.status_code != 200:
            logger.error("❌ /metrics 返回 %d", resp.status_code)
            return False
        
        content = resp.text
        
        # 查找 json_parse_failed 错误计数
        for line in content.split('\n'):
            if 'yunshu_safe_file_reader_errors_total' in line and 'json_parse_failed' in line:
                logger.info("📈 发现指标: %s", line.strip())
                # 提取数值
                try:
                    value = float(line.split()[-1])
                    if value > 10:
                        logger.info("✅ 告警条件满足: json_parse_failed=%d > 10", int(value))
                        return True
                    else:
                        logger.warning("⚠️ 指标值 %d 未达到阈值 10", int(value))
                        return False
                except ValueError:
                    pass
        
        # 也检查 read_duration 和 loaded_history_count
        for line in content.split('\n'):
            if 'yunshu_safe_file_reader_read_duration_seconds_count' in line:
                logger.info("📈 读取耗时计数: %s", line.strip())
            if 'yunshu_safe_file_reader_loaded_history_count' in line:
                logger.info("📈 加载历史数: %s", line.strip())
            if 'yunshu_safe_file_reader_invalid_ratio' in line and 'count' not in line:
                logger.info("📈 无效行比例: %s", line.strip())
        
        return False
        
    except requests.RequestException as e:
        logger.error("❌ 无法访问 /metrics: %s", e)
        return False


def restart_server():
    """重启服务以触发历史加载"""
    logger.info("🔄 需要手动重启服务以触发历史加载...")
    logger.info("   请在终端中按 Ctrl+C 停止，然后重新运行: python app_server.py")
    logger.info("   或使用 Docker: docker-compose -f docker-compose.monitoring.yml restart")


def main():
    logger.info("=" * 60)
    logger.info("🔔 Prometheus 告警触发验证 - 连续解析失败场景")
    logger.info("=" * 60)
    logger.info("")
    
    # 步骤 1：备份
    logger.info("步骤 1/4: 备份原始文件")
    backup_file()
    logger.info("")
    
    # 步骤 2：注入损坏行
    logger.info("步骤 2/4: 注入损坏行")
    inject_damaged_lines()
    logger.info("")
    
    # 步骤 3：提示重启
    logger.info("步骤 3/4: 重启服务")
    logger.info("⚠️  当前服务正在运行，需要重启才能触发新的历史加载")
    logger.info("   方案 A: 手动重启（推荐）")
    logger.info("   方案 B: 使用现有服务，仅验证指标结构")
    logger.info("")
    
    # 步骤 4：检查当前指标
    logger.info("步骤 4/4: 检查 /metrics 端点")
    result = check_metrics_for_alert()
    logger.info("")
    
    if result:
        logger.info("🎉 Prometheus 告警验证通过！")
        logger.info("")
        logger.info("告警规则: SafeFileReaderConsecutiveParseFailures")
        logger.info("触发条件: 5m 内 json_parse_failed > 10")
        logger.info("当前状态: ✅ 满足触发条件")
    else:
        logger.info("⚠️  告警条件未满足（可能需要重启服务）")
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("📋 后续步骤:")
    logger.info("  1. 重启服务: 在终端按 Ctrl+C，然后 python app_server.py")
    logger.info("  2. 等待启动完成后再次运行此脚本")
    logger.info("  3. 或访问 http://127.0.0.1:5678/metrics 手动检查")
    logger.info("  4. 恢复原始文件: python -c \"import shutil; shutil.copy2('%s', '%s')\"" % (BACKUP_FILE, MESSAGES_FILE))
    logger.info("=" * 60)
    
    # 自动恢复
    logger.info("")
    logger.info("🔄 自动恢复原始文件...")
    restore_file()
    logger.info("✅ 文件已恢复，服务可正常运行")


if __name__ == '__main__':
    main()
