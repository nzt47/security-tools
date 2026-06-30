#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
钉钉通知脚本

功能：
1. 发送 CI/CD 可观测性验证结果到钉钉群
2. 支持加签安全验证
3. 支持 Markdown 格式消息

使用方法：
    python scripts/observability_dingtalk_notify.py --webhook <webhook_url> --secret <secret> --status success --message "测试通过"
"""

import argparse
import base64
import hashlib
import hmac
import json
import sys
import time
import traceback
import urllib.parse
from datetime import datetime
from typing import Dict, Any, Optional

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class DingTalkNotifier:
    """钉钉通知器"""

    def __init__(self, webhook_url: str, secret: str = None):
        self.webhook_url = webhook_url
        self.secret = secret

    def _generate_sign(self, timestamp: str) -> str:
        """生成签名"""
        if not self.secret:
            return ""

        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode('utf-8'),
            string_to_sign.encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return sign

    def _build_url(self) -> str:
        """构建带签名的请求 URL"""
        if self.secret:
            timestamp = str(round(time.time() * 1000))
            sign = self._generate_sign(timestamp)
            return f"{self.webhook_url}&timestamp={timestamp}&sign={sign}"
        return self.webhook_url

    def send_markdown(self, title: str, text: str,
                      at_mobiles: list = None,
                      at_all: bool = False) -> Dict[str, Any]:
        """发送 Markdown 格式消息"""
        if not REQUESTS_AVAILABLE:
            return {"success": False, "error": "requests 库不可用"}

        url = self._build_url()

        payload = {
            "msgtype": "markdown",
            "markdown": {
                "title": title,
                "text": text
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all
            }
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            result = response.json()

            if result.get("errcode") == 0:
                return {"success": True, "result": result}
            else:
                return {"success": False, "error": result.get("errmsg", "未知错误")}

        except Exception as e:
            return {"success": False, "error": str(e)}

    def send_text(self, content: str,
                  at_mobiles: list = None,
                  at_all: bool = False) -> Dict[str, Any]:
        """发送文本消息"""
        if not REQUESTS_AVAILABLE:
            return {"success": False, "error": "requests 库不可用"}

        url = self._build_url()

        payload = {
            "msgtype": "text",
            "text": {
                "content": content
            },
            "at": {
                "atMobiles": at_mobiles or [],
                "isAtAll": at_all
            }
        }

        try:
            response = requests.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            result = response.json()

            if result.get("errcode") == 0:
                return {"success": True, "result": result}
            else:
                return {"success": False, "error": result.get("errmsg", "未知错误")}

        except Exception as e:
            return {"success": False, "error": str(e)}


def build_observability_notification(status: str, message: str,
                                     branch: str = None,
                                     commit: str = None,
                                     actor: str = None,
                                     workflow: str = None) -> tuple:
    """构建可观测性通知消息"""

    status_emoji = {
        "success": "✅",
        "failure": "❌",
        "cancelled": "⚠️",
    }.get(status.lower(), "🔔")

    status_text = {
        "success": "通过",
        "failure": "失败",
        "cancelled": "取消",
    }.get(status.lower(), status)

    title = f"{status_emoji} 可观测性质量门禁 {status_text}"

    # 构建 Markdown 内容
    text_lines = [
        f"# {status_emoji} 可观测性质量门禁 {status_text}",
        "",
        f"**状态**: {status_text}",
        "",
        f"**消息**: {message}",
        "",
        "---",
        "",
    ]

    if branch:
        text_lines.append(f"- **分支**: {branch}")
    if commit:
        short_commit = commit[:7] if commit else "N/A"
        text_lines.append(f"- **提交**: {short_commit}")
    if actor:
        text_lines.append(f"- **触发者**: {actor}")
    if workflow:
        text_lines.append(f"- **工作流**: {workflow}")

    text_lines.extend([
        "",
        f"**时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "---",
        "",
        "> 💡 请查看详细报告了解更多信息",
    ])

    text = "\n".join(text_lines)
    return title, text


def main():
    parser = argparse.ArgumentParser(description="钉钉通知")
    parser.add_argument("--webhook", required=True,
                       help="钉钉机器人 Webhook URL")
    parser.add_argument("--secret", default=None,
                       help="加签密钥（可选）")
    parser.add_argument("--status", default="success",
                       choices=["success", "failure", "cancelled"],
                       help="通知状态 (success/failure/cancelled)")
    parser.add_argument("--message", default="可观测性验证完成",
                       help="通知消息")
    parser.add_argument("--branch", default=None,
                       help="分支名称")
    parser.add_argument("--commit", default=None,
                       help="提交哈希")
    parser.add_argument("--actor", default=None,
                       help="触发者")
    parser.add_argument("--workflow", default=None,
                       help="工作流名称")
    parser.add_argument("--msg-type", default="markdown",
                       choices=["text", "markdown"],
                       help="消息类型 (text/markdown)")
    args = parser.parse_args()

    notifier = DingTalkNotifier(args.webhook, args.secret)

    try:
        if args.msg_type == "markdown":
            title, text = build_observability_notification(
                status=args.status,
                message=args.message,
                branch=args.branch,
                commit=args.commit,
                actor=args.actor,
                workflow=args.workflow,
            )
            result = notifier.send_markdown(title, text)
        else:
            result = notifier.send_text(args.message)

        if result["success"]:
            print("✅ 通知发送成功")
            sys.exit(0)
        else:
            print(f"❌ 通知发送失败: {result.get('error', '未知错误')}")
            sys.exit(1)

    except Exception as e:
        print(f"❌ 发送异常: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
