"""
app_server 认证与日志装饰器

从 app_server.py 提取，供主文件及路由模块共享。
"""

import os
import secrets
import functools
import logging
from flask import request, jsonify

logger = logging.getLogger(__name__)

# ── API 令牌 ──
_API_TOKEN = os.environ.get("FLASK_API_TOKEN", "")
_API_TOKEN_ENABLED = bool(_API_TOKEN)


def require_token(f):
    """需要 API 令牌认证的装饰器"""
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not _API_TOKEN_ENABLED:
            return f(*args, **kwargs)
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
        else:
            token = request.headers.get("X-API-Token", "")
        if not token or not secrets.compare_digest(token, _API_TOKEN):
            return jsonify({"error": "未授权：缺少或无效的 API 令牌"}), 401
        return f(*args, **kwargs)
    return decorated


def log_request(show_body=True, show_response=True):
    """接口日志装饰器"""
    def decorator(f):
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            import time
            start_time = time.time()
            endpoint = f.__name__

            logs = []
            logs.append(f"[REQUEST] 接口: {endpoint}")
            logs.append(f"[REQUEST] 方法: {request.method}")
            logs.append(f"[REQUEST] 路径: {request.path}")
            logs.append(f"[REQUEST] 查询参数: {dict(request.args)}")

            if show_body and request.method in ['POST', 'PUT', 'PATCH']:
                try:
                    body = request.get_json() if request.is_json else request.form.to_dict()
                    body_str = str(body)[:200] + ('...' if len(str(body)) > 200 else '')
                    logs.append(f"[REQUEST] 请求体: {body_str}")
                except Exception:
                    logs.append(f"[REQUEST] 请求体: 无法解析")

            try:
                response = f(*args, **kwargs)
                response_time = (time.time() - start_time) * 1000
                logs.append(f"[RESPONSE] 状态码: {response[1] if isinstance(response, tuple) else 200}")
                logs.append(f"[RESPONSE] 耗时: {response_time:.2f}ms")
                if show_response:
                    resp_body = response[0].get_data(as_text=True) if isinstance(response, tuple) and hasattr(response[0], 'get_data') else str(response)[:200]
                    logs.append(f"[RESPONSE] 内容: {resp_body[:200]}")
                logger.info("\n".join(logs))
                return response
            except Exception as e:
                response_time = (time.time() - start_time) * 1000
                logger.error("[ERROR] 接口 %s 异常: %s (耗时: %.2fms)", endpoint, e, response_time)
                raise
        return decorated
    return decorator
