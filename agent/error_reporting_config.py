#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Digital Life 错误上报配置文件
生产环境请复制此文件为 error_reporting_config.py 并填入真实配置
"""

import os
from typing import Dict, Any

# 从环境变量读取配置，或使用默认值
def get_config() -> Dict[str, Any]:
    """获取错误上报配置"""
    
    return {
        # 控制台上报（始终启用）
        'console': {
            'enabled': True,
            'min_level': os.environ.get('ERROR_REPORTING_CONSOLE_LEVEL', 'warning')
        },
        
        # 文件上报
        'file': {
            'enabled': os.environ.get('ERROR_REPORTING_FILE_ENABLED', 'true').lower() == 'true',
            'file_path': os.environ.get('ERROR_REPORTING_FILE_PATH', './logs/digital_life_errors.log'),
            'min_level': os.environ.get('ERROR_REPORTING_FILE_LEVEL', 'error')
        },
        
        # Webhook 上报
        'webhook': {
            'enabled': os.environ.get('ERROR_REPORTING_WEBHOOK_ENABLED', 'false').lower() == 'true',
            'url': os.environ.get('ERROR_REPORTING_WEBHOOK_URL', ''),
            'headers': {
                'Content-Type': 'application/json'
            },
            'timeout': int(os.environ.get('ERROR_REPORTING_WEBHOOK_TIMEOUT', '5')),
            'min_level': os.environ.get('ERROR_REPORTING_WEBHOOK_LEVEL', 'error')
        },
        
        # Slack 上报
        'slack': {
            'enabled': os.environ.get('ERROR_REPORTING_SLACK_ENABLED', 'false').lower() == 'true',
            'webhook_url': os.environ.get('ERROR_REPORTING_SLACK_WEBHOOK_URL', ''),
            'channel': os.environ.get('ERROR_REPORTING_SLACK_CHANNEL', '#digital-life-alerts'),
            'username': os.environ.get('ERROR_REPORTING_SLACK_USERNAME', 'Digital Life Bot'),
            'icon_emoji': os.environ.get('ERROR_REPORTING_SLACK_ICON', ':robot_face:'),
            'min_level': os.environ.get('ERROR_REPORTING_SLACK_LEVEL', 'warning')
        },
        
        # Email 上报（暂未实现）
        'email': {
            'enabled': False
        }
    }
