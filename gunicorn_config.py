"""
Gunicorn 启动配置文件
用于生产环境多进程部署

使用方法:
    gunicorn -c gunicorn_config.py app_server:app

参数说明:
    --workers: 工作进程数 (建议设置为 CPU 核心数 * 2 + 1)
    --worker-class: 工作进程类型 (gevent 支持异步)
    --bind: 绑定地址
    --timeout: 请求超时时间 (秒)
    --keepalive: 保持连接时间 (秒)
    --accesslog: 访问日志文件
    --errorlog: 错误日志文件
    --loglevel: 日志级别
"""

import multiprocessing
import os

# 服务器绑定
bind = "127.0.0.1:5678"

# 工作进程数
# 公式：workers = (CPU 核心数 * 2) + 1
# Windows 建议使用同步 worker
workers = min(multiprocessing.cpu_count() * 2 + 1, 8)

# 工作进程类型
# gevent: 异步高性能 (需要安装 gevent)
# sync: 同步 (默认，Windows 推荐)
worker_class = "sync"

# 单个 worker 的最大连接数 (仅异步 worker 有效)
worker_connections = 1000

# 请求超时时间 (秒)
timeout = 120

# 保持连接时间 (秒)
keepalive = 5

# 单个 worker 处理的最大请求数 (达到后自动重启，防止内存泄漏)
max_requests = 1000
max_requests_jitter = 50

# 日志配置
accesslog = "logs/gunicorn_access.log"
errorlog = "logs/gunicorn_error.log"
loglevel = "info"

# 进程命名 (便于在进程列表中识别)
proc_name = "yunshu"

# 守护进程
daemon = False

# PID 文件
pidfile = "gunicorn.pid"

# 在 worker 启动前设置环境变量
def pre_fork(server, worker):
    """主进程 fork 前调用"""
    pass

# 在 worker 启动后调用
def post_fork(server, worker):
    """worker 启动后调用"""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

# 在 worker 退出前调用
def pre_exit(server, worker):
    """worker 退出前调用"""
    pass

# 打印启动信息
print("=" * 70)
print("🚀 Gunicorn 生产环境配置")
print("=" * 70)
print(f"绑定地址：{bind}")
print(f"工作进程数：{workers}")
print(f"工作进程类型：{worker_class}")
print(f"超时时间：{timeout}秒")
print(f"访问日志：{accesslog}")
print(f"错误日志：{errorlog}")
print("=" * 70)
