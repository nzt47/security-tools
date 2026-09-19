"""
Gunicorn 启动配置文件
用于生产环境多进程部署

⚠️ 【TASK-03 · 2026-09-18】本文件**不是当前生产运行时的配置**，且**无任何调用方**。
    实测：
      * `git grep -ln "gunicorn_config.py"` 在源码 / 脚本 / CI 中**零命中**
        （只有 docs/、.gitignore 与本文提及）；
      * 当前生产运行时是 **waitress 单进程 16 线程**：
        `app_server.py:1618  serve(app, host="127.0.0.1", port=5678, threads=16)`
      * 本文件的 `workers = min(cpu*2+1, 8)` 是**多进程**模型，与实际的单进程
        语义相反。凡涉及"并发度 / 进程模型"的判断，**一律以 `app_server.py:1618` 为准**。
    为什么保留而不是删除：`deploy/k8s/deployment.yaml` 仍引用 gunicorn
    （注：TASK-00 §0.2 已核实 `deploy/k8s/` 只服务 `skill-retrieval-service`，
    不是主平台部署路径）。删除会让该引用悬空 —— 属于"删文件没做引用检查"。
    处置：保留 + 本声明；`gunicorn` 依赖在 pyproject 中补了上限 `<24.0.0`。
    详见 docs/closeout/DEAD_CONFIG_20260918.md §3。

使用方法（**当前生产并不这样启动**）:
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
