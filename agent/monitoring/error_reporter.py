#!/usr/bin/env python3
"""
错误上报模块

支持将错误自动上报到外部系统：
- Webhook (通用HTTP回调)
- Slack (即时通讯)
- Email (邮件通知)
- 日志文件 (本地记录)

使用方法:
    from agent.monitoring.error_reporter import ErrorReporter, get_error_reporter
    
    reporter = get_error_reporter()
    reporter.report_error(
        error=Exception("Test error"),
        context={"user_id": "123", "action": "test"}
    )
"""

import json
import smtplib
import logging
import threading
import traceback
import uuid
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum

# 结构化日志必需：get_trace_id() 提供上下文追踪 ID
# set_trace_id() 用于跨线程传递 trace_id（ContextVar 不自动继承到子线程）
from agent.monitoring.tracing import get_trace_id, set_trace_id
from agent.error_handler import with_retry, TemporaryNetworkError
from pathlib import Path
import queue
import time

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """告警级别"""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ReporterType(Enum):
    """上报类型"""
    WEBHOOK = "webhook"
    SLACK = "slack"
    EMAIL = "email"
    FILE = "file"
    CONSOLE = "console"


@dataclass
class ErrorReport:
    """错误报告"""
    error_type: str
    error_message: str
    traceback: str
    timestamp: str
    level: str
    context: Dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    service: Optional[str] = None
    user_id: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            'error_type': self.error_type,
            'error_message': self.error_message,
            'traceback': self.traceback,
            'timestamp': self.timestamp,
            'level': self.level,
            'context': self.context,
            'trace_id': self.trace_id,
            'service': self.service,
            'user_id': self.user_id
        }
    
    def to_json(self) -> str:
        """转换为JSON"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class BaseReporter:
    """上报器基类"""
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.enabled = config.get('enabled', True)
        self.min_level = AlertLevel(config.get('min_level', 'error'))
    
    def should_report(self, level: AlertLevel) -> bool:
        """检查是否应该上报"""
        if not self.enabled:
            return False
        
        levels = [AlertLevel.DEBUG, AlertLevel.INFO, AlertLevel.WARNING, 
                  AlertLevel.ERROR, AlertLevel.CRITICAL]
        return levels.index(level) >= levels.index(self.min_level)
    
    def send(self, report: ErrorReport) -> bool:
        """发送报告"""
        raise NotImplementedError


class ConsoleReporter(BaseReporter):
    """控制台上报器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        super().__init__(config or {})
    
    def send(self, report: ErrorReport) -> bool:
        """发送到控制台"""
        if not self.should_report(AlertLevel(report.level)):
            return False
        
        symbols = {
            'debug': '🔍',
            'info': 'ℹ️',
            'warning': '⚠️',
            'error': '❌',
            'critical': '🚨'
        }
        
        symbol = symbols.get(report.level, '❓')
        
        print(f"\n{symbol} Error Report [{report.level.upper()}]")
        print(f"   Time: {report.timestamp}")
        print(f"   Type: {report.error_type}")
        print(f"   Message: {report.error_message}")
        if report.trace_id:
            print(f"   Trace ID: {report.trace_id}")
        if report.context:
            print(f"   Context: {json.dumps(report.context, ensure_ascii=False)}")
        if report.traceback:
            print(f"   Traceback:\n{report.traceback}")
        
        return True


class WebhookReporter(BaseReporter):
    """WebHook上报器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.url = config.get('url', '')
        self.headers = config.get('headers', {})
        self.timeout = config.get('timeout', 5)
        self.retry_times = config.get('retry_times', 3)
        self.retry_delay = config.get('retry_delay', 1)
        
        self._send_webhook_with_retry = with_retry(
            max_retries=self.retry_times,
            initial_delay=self.retry_delay,
            strategy="fixed",
            retryable_exceptions=(TemporaryNetworkError,),
            error_counter="error_reporter.webhook"
        )(self._do_send_webhook)
    
    def _do_send_webhook(self, report: ErrorReport, url: str, headers: dict, timeout: int) -> bool:
        """实际发送 Webhook（不含重试，失败抛出异常）"""
        import urllib.request
        import urllib.error
        
        payload = json.dumps(report.to_dict()).encode('utf-8')
        
        req = urllib.request.Request(
            url, 
            data=payload, 
            headers=headers,
            method='POST'
        )
        
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if 200 <= response.status < 300:
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "error_reporter",
                    "action": "webhook_report_success",
                    "duration_ms": 0,
                    "url": url,
                    "status_code": response.status
                }, ensure_ascii=False))
                return True
            else:
                raise TemporaryNetworkError(f"Webhook returned {response.status}")
    
    def send(self, report: ErrorReport) -> bool:
        """通过WebHook发送"""
        if not self.should_report(AlertLevel(report.level)):
            return False
        
        if not self.url:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "webhook_not_configured",
                "duration_ms": 0
            }, ensure_ascii=False))
            return False
        
        import urllib.request
        import urllib.error
        
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'Yunshu-ErrorReporter/1.0'
        }
        headers.update(self.headers)
        
        try:
            return self._send_webhook_with_retry(report, self.url, headers, self.timeout)
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "webhook_report_error",
                "duration_ms": 0,
                "error": str(e),
                "url": self.url
            }, ensure_ascii=False))
            return False


class SlackReporter(BaseReporter):
    """Slack上报器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.webhook_url = config.get('webhook_url', '')
        self.channel = config.get('channel', '#errors')
        self.username = config.get('username', 'Yunshu Error Bot')
        self.icon_emoji = config.get('icon_emoji', ':robot_face:')
    
    def send(self, report: ErrorReport) -> bool:
        """发送到Slack"""
        if not self.should_report(AlertLevel(report.level)):
            return False
        
        if not self.webhook_url:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "slack_not_configured",
                "duration_ms": 0
            }, ensure_ascii=False))
            return False
        
        colors = {
            'debug': '#808080',
            'info': '#36a64f',
            'warning': '#ff9800',
            'error': '#f44336',
            'critical': '#b71c1c'
        }
        
        payload = {
            'channel': self.channel,
            'username': self.username,
            'icon_emoji': self.icon_emoji,
            'attachments': [{
                'color': colors.get(report.level, '#808080'),
                'title': f"{report.error_type} [{report.level.upper()}]",
                'text': report.error_message,
                'fields': [
                    {'title': 'Time', 'value': report.timestamp, 'short': True},
                    {'title': 'Trace ID', 'value': report.trace_id or 'N/A', 'short': True}
                ],
                'footer': 'Yunshu Error Reporter'
            }]
        }
        
        if report.context:
            context_text = '\n'.join(f"• {k}: {v}" for k, v in report.context.items())
            payload['attachments'][0]['fields'].append({
                'title': 'Context',
                'value': context_text,
                'short': False
            })
        
        if report.traceback:
            payload['attachments'][0]['text'] += f"\n```{report.traceback[:500]}```"
        
        try:
            import urllib.request
            import json as json_lib
            
            data = json_lib.dumps(payload).encode('utf-8')
            headers = {'Content-Type': 'application/json'}
            
            req = urllib.request.Request(
                self.webhook_url,
                data=data,
                headers=headers,
                method='POST'
            )
            
            with urllib.request.urlopen(req, timeout=5) as response:
                logger.info(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "error_reporter",
                    "action": "slack_report_success",
                    "duration_ms": 0,
                    "channel": self.channel,
                    "status_code": response.status
                }, ensure_ascii=False))
                return 200 <= response.status < 300

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "slack_report_error",
                "duration_ms": 0,
                "error": str(e),
                "channel": self.channel
            }, ensure_ascii=False))
            return False


class EmailReporter(BaseReporter):
    """邮件上报器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.smtp_host = config.get('smtp_host', 'smtp.gmail.com')
        self.smtp_port = config.get('smtp_port', 587)
        self.smtp_user = config.get('smtp_user', '')
        self.smtp_password = config.get('smtp_password', '')
        self.from_addr = config.get('from_addr', '')
        self.to_addrs = config.get('to_addrs', [])
        self.use_tls = config.get('use_tls', True)
    
    def send(self, report: ErrorReport) -> bool:
        """发送邮件"""
        if not self.should_report(AlertLevel(report.level)):
            return False
        
        if not self.to_addrs:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "email_not_configured",
                "duration_ms": 0
            }, ensure_ascii=False))
            return False
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"[{report.level.upper()}] {report.error_type} - Yunshu"
        msg['From'] = self.from_addr or self.smtp_user
        msg['To'] = ', '.join(self.to_addrs)
        
        # 纯文本版本
        text_content = f"""
Error Report
============

Level: {report.level.upper()}
Time: {report.timestamp}
Type: {report.error_type}
Message: {report.error_message}

Trace ID: {report.trace_id or 'N/A'}

Context:
{json.dumps(report.context, indent=2, ensure_ascii=False)}

Traceback:
{report.traceback}
"""
        
        # HTML版本
        html_content = f"""
<html>
<body>
<h2 style="color: {'red' if report.level in ['error', 'critical'] else 'orange'};">
{report.level.upper()}: {report.error_type}
</h2>

<table style="border-collapse: collapse; width: 100%;">
<tr><td style="padding: 5px;"><b>Time:</b></td><td>{report.timestamp}</td></tr>
<tr><td style="padding: 5px;"><b>Message:</b></td><td>{report.error_message}</td></tr>
<tr><td style="padding: 5px;"><b>Trace ID:</b></td><td><code>{report.trace_id or 'N/A'}</code></td></tr>
</table>

<h3>Context:</h3>
<pre>{json.dumps(report.context, indent=2, ensure_ascii=False)}</pre>

<h3>Traceback:</h3>
<pre style="background: #f5f5f5; padding: 10px; overflow-x: auto;">{report.traceback}</pre>

<hr>
<small>Sent by Yunshu Error Reporter</small>
</body>
</html>
"""
        
        msg.attach(MIMEText(text_content, 'plain', 'utf-8'))
        msg.attach(MIMEText(html_content, 'html', 'utf-8'))
        
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.use_tls:
                    server.starttls()
                if self.smtp_user and self.smtp_password:
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "email_report_success",
                "duration_ms": 0,
                "recipients": self.to_addrs
            }, ensure_ascii=False))
            return True

        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "email_report_error",
                "duration_ms": 0,
                "error": str(e),
                "recipients": self.to_addrs
            }, ensure_ascii=False))
            return False


class FileReporter(BaseReporter):
    """文件上报器"""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.file_path = Path(config.get('file_path', './logs/errors.log'))
        self.max_size_mb = config.get('max_size_mb', 10)
        self.backup_count = config.get('backup_count', 5)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
    
    def send(self, report: ErrorReport) -> bool:
        """写入文件"""
        if not self.should_report(AlertLevel(report.level)):
            return False
        
        # 检查文件大小
        if self.file_path.exists():
            size_mb = self.file_path.stat().st_size / (1024 * 1024)
            if size_mb > self.max_size_mb:
                self._rotate_log()
        
        # 写入日志
        try:
            with open(self.file_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*80}\n")
                f.write(f"Level: {report.level.upper()}\n")
                f.write(f"Time: {report.timestamp}\n")
                f.write(f"Type: {report.error_type}\n")
                f.write(f"Message: {report.error_message}\n")
                f.write(f"Trace ID: {report.trace_id or 'N/A'}\n")
                f.write(f"\nContext:\n{json.dumps(report.context, indent=2, ensure_ascii=False)}\n")
                f.write(f"\nTraceback:\n{report.traceback}\n")
            
            return True
            
        except Exception as e:
            logger.error(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "file_write_error",
                "duration_ms": 0,
                "error": str(e),
                "file_path": str(self.file_path)
            }, ensure_ascii=False))
            return False
    
    def _rotate_log(self):
        """轮转日志文件"""
        if self.file_path.exists():
            # 删除最旧的备份
            oldest = self.file_path.with_suffix(f'.{self.backup_count}.log')
            if oldest.exists():
                oldest.unlink()
            
            # 轮转现有备份
            for i in range(self.backup_count - 1, 0, -1):
                src = self.file_path.with_suffix(f'.{i}.log')
                dst = self.file_path.with_suffix(f'.{i+1}.log')
                if src.exists():
                    src.rename(dst)
            
            # 重命名当前文件
            self.file_path.rename(self.file_path.with_suffix('.1.log'))


class ErrorReporter:
    """错误上报管理器"""
    
    def __init__(self, config: Dict[str, Any] = None):
        """
        初始化错误上报器
        
        Args:
            config: 配置字典，格式如下:
                {
                    'console': {'enabled': True},
                    'webhook': {
                        'enabled': True,
                        'url': 'https://your-webhook-url',
                        'headers': {'Authorization': 'Bearer xxx'}
                    },
                    'slack': {
                        'enabled': False,
                        'webhook_url': 'https://hooks.slack.com/...',
                        'channel': '#errors'
                    },
                    'email': {
                        'enabled': False,
                        'smtp_host': 'smtp.gmail.com',
                        'to_addrs': ['admin@example.com']
                    },
                    'file': {
                        'enabled': True,
                        'file_path': './logs/errors.log'
                    }
                }
        """
        self.config = config or {}
        self.reporters: List[BaseReporter] = []
        self._report_queue = queue.Queue(maxsize=1000)
        self._async_worker = None
        self._stop_worker = threading.Event()
        # 模块专属 trace_id：用于后台线程上下文追踪
        # Python ContextVar 不自动继承到子线程，需在 _async_worker_loop 入口显式 set
        self._reporter_trace_id = f"error-reporter-{uuid.uuid4().hex[:16]}"

        self._init_reporters()
    
    def _init_reporters(self):
        """初始化所有上报器"""
        # 控制台上报器
        console_config = self.config.get('console', {})
        if console_config.get('enabled', True):
            self.reporters.append(ConsoleReporter(console_config))
        
        # WebHook上报器
        webhook_config = self.config.get('webhook', {})
        if webhook_config.get('enabled', False):
            self.reporters.append(WebhookReporter(webhook_config))
        
        # Slack上报器
        slack_config = self.config.get('slack', {})
        if slack_config.get('enabled', False):
            self.reporters.append(SlackReporter(slack_config))
        
        # 邮件上报器
        email_config = self.config.get('email', {})
        if email_config.get('enabled', False):
            self.reporters.append(EmailReporter(email_config))
        
        # 文件上报器
        file_config = self.config.get('file', {})
        if file_config.get('enabled', True):
            self.reporters.append(FileReporter(file_config))
        
        if self.reporters:
            logger.info(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "init",
                "duration_ms": 0,
                "reporters_count": len(self.reporters),
                "reporter_types": [type(r).__name__ for r in self.reporters]
            }, ensure_ascii=False))
        else:
            logger.warning(json.dumps({
                "trace_id": get_trace_id(),
                "module_name": "error_reporter",
                "action": "no_reporters",
                "duration_ms": 0
            }, ensure_ascii=False))
    
    def report_error(
        self,
        error: Exception,
        level: AlertLevel = AlertLevel.ERROR,
        context: Dict[str, Any] = None,
        trace_id: Optional[str] = None,
        service: Optional[str] = None,
        user_id: Optional[str] = None,
        async_report: bool = False
    ) -> bool:
        """
        上报错误
        
        Args:
            error: 异常对象
            level: 告警级别
            context: 额外上下文信息
            trace_id: 追踪ID
            service: 服务名称
            user_id: 用户ID
            async_report: 是否异步上报
        
        Returns:
            是否上报成功
        """
        # 创建错误报告
        report = ErrorReport(
            error_type=type(error).__name__,
            error_message=str(error),
            traceback=traceback.format_exc(),
            timestamp=datetime.now().isoformat(),
            level=level.value,
            context=context or {},
            trace_id=trace_id,
            service=service,
            user_id=user_id
        )
        
        # 打印到控制台
        for reporter in self.reporters:
            if isinstance(reporter, ConsoleReporter):
                reporter.send(report)
                break
        
        if async_report:
            # 异步上报
            try:
                self._report_queue.put_nowait(report)
                self._ensure_worker_started()
            except queue.Full:
                logger.warning(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "error_reporter",
                    "action": "queue_full",
                    "duration_ms": 0,
                    "queue_maxsize": self._report_queue.maxsize
                }, ensure_ascii=False))
            return True
        else:
            # 同步上报
            return self._send_to_all(report)
    
    def report_message(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        context: Dict[str, Any] = None,
        trace_id: Optional[str] = None,
        service: Optional[str] = None
    ) -> bool:
        """上报消息（不是错误）"""
        report = ErrorReport(
            error_type='Message',
            error_message=message,
            traceback='',
            timestamp=datetime.now().isoformat(),
            level=level.value,
            context=context or {},
            trace_id=trace_id,
            service=service
        )
        
        return self._send_to_all(report)
    
    def _send_to_all(self, report: ErrorReport) -> bool:
        """发送到所有上报器"""
        success = False
        for reporter in self.reporters:
            try:
                if reporter.send(report):
                    success = True
            except Exception as e:
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "error_reporter",
                    "action": "reporter_failed",
                    "duration_ms": 0,
                    "error": str(e),
                    "reporter_type": type(reporter).__name__
                }, ensure_ascii=False))
        return success
    
    def _ensure_worker_started(self):
        """确保异步工作线程已启动"""
        if self._async_worker is None or not self._async_worker.is_alive():
            self._stop_worker.clear()
            self._async_worker = threading.Thread(target=self._async_worker_loop, daemon=True)
            self._async_worker.start()
    
    def _async_worker_loop(self):
        """异步工作线程"""
        # 关键修复：后台线程不继承父线程 ContextVar，需在入口显式设置 trace_id
        # 否则后续日志中的 get_trace_id() 将返回 None，导致 trace_id 丢失
        set_trace_id(self._reporter_trace_id)
        while not self._stop_worker.is_set():
            try:
                report = self._report_queue.get(timeout=1)
                self._send_to_all(report)
                self._report_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                # 后台线程：trace_id 已在入口 set，此处 get_trace_id() 返回模块专属 ID
                logger.error(json.dumps({
                    "trace_id": get_trace_id(),
                    "module_name": "error_reporter",
                    "action": "async_worker_error",
                    "duration_ms": 0,
                    "error": str(e)
                }, ensure_ascii=False))
    
    def stop(self):
        """停止异步工作线程"""
        self._stop_worker.set()
        if self._async_worker and self._async_worker.is_alive():
            self._async_worker.join(timeout=5)
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        return {
            'queue_size': self._report_queue.qsize(),
            'reporters': len(self.reporters),
            'reporter_types': [type(r).__name__ for r in self.reporters],
            'worker_alive': self._async_worker.is_alive() if self._async_worker else False
        }


# 全局单例
_global_reporter = None
_global_lock = threading.Lock()

def get_error_reporter(config: Dict[str, Any] = None) -> ErrorReporter:
    """获取全局错误上报器"""
    global _global_reporter
    if _global_reporter is None:
        with _global_lock:
            if _global_reporter is None:
                _global_reporter = ErrorReporter(config)
    return _global_reporter


def report_error(
    error: Exception,
    level: AlertLevel = AlertLevel.ERROR,
    context: Dict[str, Any] = None,
    **kwargs
) -> bool:
    """快捷函数：上报错误"""
    return get_error_reporter().report_error(error, level, context, **kwargs)
