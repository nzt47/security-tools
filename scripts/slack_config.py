#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Slack Webhook 配置和测试工具
"""

import sys
import json
import requests
import logging
from typing import Optional

# 设置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)

# Slack 配置示例
SLACK_CONFIG = {
    'webhook_url': 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK',
    'channel': '#digital-life-alerts',
    'username': 'Digital Life Bot',
    'icon_emoji': ':robot_face:',
    'min_level': 'warning'  # warning, error, critical
}


def send_slack_alert(
    webhook_url: str,
    error_type: str,
    error_message: str,
    traceback: str,
    context: dict = None,
    channel: str = None,
    username: str = None,
    icon_emoji: str = None
) -> bool:
    """
    发送 Slack 告警
    
    Args:
        webhook_url: Slack Incoming Webhook URL
        error_type: 错误类型
        error_message: 错误消息
        traceback: 堆栈信息
        context: 上下文信息
        channel: 目标频道
        username: 机器人用户名
        icon_emoji: 机器人图标
    
    Returns:
        是否成功
    """
    try:
        # 构建 Slack 消息（Block Kit 格式）
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": "🚨 Digital Life 错误告警",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*错误类型:*\n`{error_type}`"},
                    {"type": "mrkdwn", "text": f"*时间:*\n{json.dumps(context).split('\"trace_id\": \"')[1].split('\"')[0][:19] if context else 'N/A'}"}
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*错误消息:*\n```\n{error_message}\n```"
                }
            }
        ]
        
        # 添加上下文
        if context:
            fields = []
            for key, value in context.items():
                if isinstance(value, str) and len(value) > 50:
                    value = value[:47] + "..."
                fields.append({
                    "type": "mrkdwn",
                    "text": f"*{key}:*\n`{value}`"
                })
            if fields:
                blocks.append({
                    "type": "section",
                    "fields": fields
                })
        
        # 添加堆栈信息（折叠显示）
        if traceback:
            blocks.extend([
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*堆栈跟踪:*\n```\n{traceback[:1500]}{'...' if len(traceback) > 1500 else ''}\n```"
                    }
                }
            ])
        
        payload = {
            "blocks": blocks,
            "attachments": []
        }
        
        if channel:
            payload["channel"] = channel
        if username:
            payload["username"] = username
        if icon_emoji:
            payload["icon_emoji"] = icon_emoji
        
        # 发送请求
        response = requests.post(
            webhook_url,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        
        if response.status_code == 200:
            logging.info("✅ Slack 通知发送成功")
            return True
        else:
            logging.error(f"❌ Slack 通知发送失败: {response.status_code} - {response.text}")
            return False
            
    except Exception as e:
        logging.error(f"❌ 发送 Slack 通知出错: {e}")
        return False


def test_slack_config():
    """测试 Slack 配置"""
    print("\n" + "="*80)
    print("Slack Webhook 配置测试")
    print("="*80)
    
    print(f"\n📋 当前配置:")
    print(f"   Webhook URL: {SLACK_CONFIG['webhook_url'][:50]}...")
    print(f"   频道: {SLACK_CONFIG['channel']}")
    print(f"   用户名: {SLACK_CONFIG['username']}")
    print(f"   图标: {SLACK_CONFIG['icon_emoji']}")
    print(f"   最低级别: {SLACK_CONFIG['min_level']}")
    
    print("\n⚠️ 注意：请先在 SLACK_CONFIG 中配置正确的 Webhook URL！")
    
    # 检查是否为默认 URL
    if SLACK_CONFIG['webhook_url'] == 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK':
        print("\n❌ 请配置真实的 Slack Webhook URL！")
        print("\n📖 如何获取 Slack Webhook：")
        print("   1. 访问 https://api.slack.com/messaging/webhooks")
        print("   2. 创建一个新 App")
        print("   3. 启用 Incoming Webhooks")
        print("   4. 添加一个 Webhook 到你的工作空间")
        print("   5. 将生成的 URL 填入 SLACK_CONFIG['webhook_url']")
        return False
    
    # 测试发送
    print("\n🧪 发送测试消息...")
    
    success = send_slack_alert(
        webhook_url=SLACK_CONFIG['webhook_url'],
        error_type="TestAlert",
        error_message="这是一条测试告警消息，来自 Digital Life 错误上报系统！",
        traceback="Traceback (most recent call last):\n  File \"test.py\", line 42, in <module>\n    raise ValueError('Test error')",
        context={
            "test": "true",
            "source": "slack_config.py",
            "timestamp": "2026-05-31T00:00:00"
        },
        channel=SLACK_CONFIG['channel'],
        username=SLACK_CONFIG['username'],
        icon_emoji=SLACK_CONFIG['icon_emoji']
    )
    
    if success:
        print("\n✅ 测试成功！请检查你的 Slack 频道。")
        return True
    else:
        print("\n❌ 测试失败，请检查配置。")
        return False


def configure_error_reporter_with_slack():
    """配置带有 Slack 的错误上报器"""
    from agent.monitoring import get_error_reporter, AlertLevel
    
    print("\n" + "="*80)
    print("配置带有 Slack 的错误上报器")
    print("="*80)
    
    config = {
        'console': {
            'enabled': True,
            'min_level': 'info'
        },
        'file': {
            'enabled': True,
            'file_path': './logs/digital_life_errors.log'
        },
        'slack': {
            'enabled': True,
            'webhook_url': SLACK_CONFIG['webhook_url'],
            'channel': SLACK_CONFIG['channel'],
            'username': SLACK_CONFIG['username'],
            'icon_emoji': SLACK_CONFIG['icon_emoji'],
            'min_level': SLACK_CONFIG['min_level']
        }
    }
    
    reporter = get_error_reporter(config)
    print(f"\n✅ 错误上报器已配置，包含 {len(reporter.reporters)} 个上报渠道")
    
    # 测试上报
    print("\n🧪 发送测试错误上报...")
    
    try:
        raise ValueError("这是一条测试错误，用于验证 Slack 上报！")
    except Exception as e:
        reporter.report_error(
            error=e,
            level=AlertLevel.ERROR,
            context={
                "source": "slack_config.py",
                "test": "true"
            }
        )
    
    print("\n✅ 完成！请检查 Slack 频道和日志文件。")
    return reporter


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Slack Webhook 配置工具")
    parser.add_argument(
        "--test",
        action="store_true",
        help="测试 Slack 配置"
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="配置完整的错误上报器"
    )
    
    args = parser.parse_args()
    
    if args.test:
        test_slack_config()
    elif args.configure:
        configure_error_reporter_with_slack()
    else:
        print("\n" + "="*80)
        print("Slack Webhook 配置工具")
        print("="*80)
        print("\n使用方法:")
        print("  python slack_config.py --test     # 测试 Slack 配置")
        print("  python slack_config.py --configure  # 配置完整错误上报器")
        print("\n📖 请先编辑 SLACK_CONFIG 配置你的 Webhook URL！")
