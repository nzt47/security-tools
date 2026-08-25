"""API 网关 — 统一的 API 入口和管理

提供：
  - 统一认证（API Key / OAuth）
  - 访问日志和用量统计
  - 速率限制和配额管理
  - Swagger API 文档自动生成
  - 请求路由和转发
"""

import json
import logging
import threading
import time
import functools
import enum
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Callable, List
from pathlib import Path

from agent.rate_limiter import RateLimiter, get_rate_limiter
from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


class AuthMethod(str, enum.Enum):
    """认证方式枚举"""
    API_KEY = "api_key"
    OAUTH = "oauth"
    BEARER = "bearer"
    NONE = "none"


class ApiKeyManager:
    """API Key 管理器"""
    
    def __init__(self):
        self._api_keys: Dict[str, Dict] = {}
        # Why 锁：increment_usage 的「读-改-写」（quota_remaining -= 1）在并发下
        # 会超卖配额；锁仅保护内存 dict 变更（无 I/O，_save_keys 在锁外执行）
        self._lock = threading.Lock()
        self._load_keys()
    
    def _load_keys(self):
        """加载 API Keys"""
        keys_file = Path(__file__).parent / "data" / "api_keys.json"
        if keys_file.exists():
            try:
                with open(keys_file, 'r', encoding='utf-8') as f:
                    self._api_keys = json.load(f)
            except Exception as e:
                logger.warning(f"加载 API Keys 失败: {e}")
    
    def _save_keys(self):
        """保存 API Keys"""
        keys_file = Path(__file__).parent / "data" / "api_keys.json"
        keys_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(keys_file, 'w', encoding='utf-8') as f:
                json.dump(self._api_keys, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存 API Keys 失败: {e}")
    
    def create_key(self, user_id: str, description: str = "",
                  scopes: List[str] = None, tenant_id: str = "",
                  role: str = "", compat_until: str = "") -> Dict:
        """创建 API Key（T8.2 起支持可选 RBAC 绑定：tenant_id/role/compat_until）"""
        import secrets
        api_key = secrets.token_hex(32)
        key_info = {
            "key": api_key,
            "user_id": user_id,
            "description": description,
            "scopes": scopes or ["read", "write"],
            "tenant_id": tenant_id,
            "role": role,
            "compat_until": compat_until,
            "created_at": datetime.now().isoformat(),
            "last_used_at": "",
            "enabled": True,
            "usage_count": 0,
            "quota_remaining": 10000,
            "total_quota": 10000,
        }
        self._api_keys[api_key] = key_info
        self._save_keys()
        return key_info
    
    def validate_key(self, api_key: str) -> Optional[Dict]:
        """验证 API Key"""
        key_info = self._api_keys.get(api_key)
        if key_info and key_info.get("enabled"):
            return key_info
        return None
    
    def update_key(self, api_key: str, updates: Dict) -> bool:
        """更新 API Key 信息"""
        if api_key in self._api_keys:
            self._api_keys[api_key].update(updates)
            self._save_keys()
            return True
        return False
    
    def delete_key(self, api_key: str) -> bool:
        """删除 API Key"""
        if api_key in self._api_keys:
            del self._api_keys[api_key]
            self._save_keys()
            return True
        return False
    
    def increment_usage(self, api_key: str):
        """增加使用计数并扣减配额（加锁保证读-改-写原子，防并发超卖）

        Why 锁：usage_count += 1 / quota_remaining -= 1 在并发请求下会互相覆盖，
        导致配额被多扣成负数（超卖）；锁内仅内存 dict 变更，无 I/O。
        """
        with self._lock:
            info = self._api_keys.get(api_key)
            if info is None:
                return
            info["usage_count"] += 1
            if info["quota_remaining"] > 0:
                info["quota_remaining"] -= 1
            info["last_used_at"] = datetime.now().isoformat()
    
    def get_key_info(self, api_key: str) -> Optional[Dict]:
        """获取 API Key 信息"""
        return self._api_keys.get(api_key)
    
    def list_keys(self, user_id: str = None) -> List[Dict]:
        """列出 API Keys"""
        if user_id:
            return [k for k in self._api_keys.values() if k["user_id"] == user_id]
        return list(self._api_keys.values())


class AccessLogger:
    """访问日志记录器"""
    
    def __init__(self):
        self._logs: List[Dict] = []
        self._log_file = Path(__file__).parent / "data" / "api_access.log"
    
    def log_access(self, request_info: Dict):
        """记录访问日志"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "trace_id": get_trace_id(),
            **request_info,
        }
        self._logs.append(log_entry)
        
        if len(self._logs) > 10000:
            self._logs = self._logs[-10000:]
        
        self._write_log(log_entry)
    
    def _write_log(self, entry: Dict):
        """写入日志文件"""
        try:
            with open(self._log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception as e:
            logger.warning(f"写入访问日志失败: {e}")
    
    def get_logs(self, limit: int = 100, user_id: str = None) -> List[Dict]:
        """获取访问日志"""
        logs = self._logs[-limit:]
        if user_id:
            logs = [l for l in logs if l.get("user_id") == user_id]
        return logs
    
    def get_stats(self, period: str = "24h") -> Dict:
        """获取统计信息"""
        now = datetime.now()
        if period == "24h":
            start_time = now - timedelta(hours=24)
        elif period == "7d":
            start_time = now - timedelta(days=7)
        else:
            start_time = now - timedelta(hours=1)
        
        recent_logs = [
            l for l in self._logs 
            if datetime.fromisoformat(l["timestamp"]) >= start_time
        ]
        
        return {
            "total_requests": len(recent_logs),
            "status_codes": self._count_by_status(recent_logs),
            "endpoints": self._count_by_endpoint(recent_logs),
            "users": self._count_by_user(recent_logs),
            "period": period,
        }
    
    def _count_by_status(self, logs: List[Dict]) -> Dict:
        counts = {}
        for log in logs:
            status = log.get("status_code", 200)
            counts[str(status)] = counts.get(str(status), 0) + 1
        return counts
    
    def _count_by_endpoint(self, logs: List[Dict]) -> Dict:
        counts = {}
        for log in logs:
            endpoint = log.get("endpoint", "unknown")
            counts[endpoint] = counts.get(endpoint, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])
    
    def _count_by_user(self, logs: List[Dict]) -> Dict:
        counts = {}
        for log in logs:
            user_id = log.get("user_id", "anonymous")
            counts[user_id] = counts.get(user_id, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: x[1], reverse=True)[:10])


class QuotaManager:
    """配额管理器"""
    
    def __init__(self):
        self._quotas: Dict[str, Dict] = {}
        # Why 锁：consume_quota/check_quota 的「读-改-写」（used += amount）并发下
        # 会超额放行；锁内仅内存 dict 变更（无 I/O）
        self._lock = threading.Lock()
    
    def set_quota(self, user_id: str, quota_type: str, limit: int, period: str = "day"):
        """设置配额"""
        key = f"{user_id}:{quota_type}"
        with self._lock:
            self._quotas[key] = {
                "user_id": user_id,
                "quota_type": quota_type,
                "limit": limit,
                "period": period,
                "used": 0,
                "last_reset": datetime.now().isoformat(),
            }
    
    def check_quota(self, user_id: str, quota_type: str, amount: int = 1) -> bool:
        """检查配额"""
        key = f"{user_id}:{quota_type}"
        with self._lock:
            quota = self._quotas.get(key)

            if not quota:
                return True

            self._reset_if_needed(quota)

            return quota["used"] + amount <= quota["limit"]
    
    def consume_quota(self, user_id: str, quota_type: str, amount: int = 1) -> bool:
        """消耗配额（加锁保证读-改-写原子，防并发超额放行）

        Why 锁：used += amount 在并发请求下会互相覆盖（都读到同一 used 都放行），
        导致实际消耗超过 limit；锁内仅内存 dict 变更，无 I/O。
        """
        key = f"{user_id}:{quota_type}"
        with self._lock:
            quota = self._quotas.get(key)

            if not quota:
                return True

            self._reset_if_needed(quota)

            if quota["used"] + amount <= quota["limit"]:
                quota["used"] += amount
                return True
            return False
    
    def _reset_if_needed(self, quota: Dict):
        """根据周期重置配额"""
        last_reset = datetime.fromisoformat(quota["last_reset"])
        now = datetime.now()
        
        if quota["period"] == "day":
            if now.date() > last_reset.date():
                quota["used"] = 0
                quota["last_reset"] = now.isoformat()
        elif quota["period"] == "hour":
            if now.hour > last_reset.hour or now.date() > last_reset.date():
                quota["used"] = 0
                quota["last_reset"] = now.isoformat()
        elif quota["period"] == "month":
            if now.month > last_reset.month or now.year > last_reset.year:
                quota["used"] = 0
                quota["last_reset"] = now.isoformat()
    
    def get_quota_status(self, user_id: str, quota_type: str) -> Dict:
        """获取配额状态"""
        key = f"{user_id}:{quota_type}"
        with self._lock:
            quota = self._quotas.get(key)

            if not quota:
                return {"user_id": user_id, "quota_type": quota_type, "limit": -1, "used": 0}

            self._reset_if_needed(quota)

            return {
                "user_id": user_id,
                "quota_type": quota_type,
                "limit": quota["limit"],
                "used": quota["used"],
                "remaining": quota["limit"] - quota["used"],
                "period": quota["period"],
            }

    # ── 租户配额（T8.3，key 前缀 tenant:，与用户配额隔离） ─────────────

    def set_tenant_quota(self, tenant_id: str, quota_type: str, limit: int, period: str = "day"):
        """设置租户配额"""
        key = f"tenant:{tenant_id}:{quota_type}"
        with self._lock:
            self._quotas[key] = {
                "tenant_id": tenant_id,
                "quota_type": quota_type,
                "limit": limit,
                "period": period,
                "used": 0,
                "last_reset": datetime.now().isoformat(),
            }

    def check_tenant_quota(self, tenant_id: str, quota_type: str, amount: int = 1) -> bool:
        """检查租户配额（未设置默认放行）"""
        key = f"tenant:{tenant_id}:{quota_type}"
        with self._lock:
            quota = self._quotas.get(key)
            if not quota:
                return True
            self._reset_if_needed(quota)
            return quota["used"] + amount <= quota["limit"]

    def consume_tenant_quota(self, tenant_id: str, quota_type: str, amount: int = 1) -> bool:
        """消耗租户配额（加锁保证读-改-写原子，防并发超额放行）"""
        key = f"tenant:{tenant_id}:{quota_type}"
        with self._lock:
            quota = self._quotas.get(key)
            if not quota:
                return True
            self._reset_if_needed(quota)
            if quota["used"] + amount <= quota["limit"]:
                quota["used"] += amount
                return True
            return False


class ApiGateway:
    """API 网关"""
    
    def __init__(self):
        self._api_key_manager = ApiKeyManager()
        self._access_logger = AccessLogger()
        self._quota_manager = QuotaManager()
        self._rate_limiter = get_rate_limiter("api_gateway")
        self._endpoints: Dict[str, Dict] = {}
        self._middleware: List[Callable] = []
        # [2026-08-13 并发审计 D] 端点注册/遍历互斥锁：多路 HTTP 并发注册
        # 端点时 check-then-insert 非原子
        self._lock = threading.Lock()
    
    def register_endpoint(self, path: str, method: str, handler: Callable,
                         auth_required: bool = True, scopes: List[str] = None,
                         summary: str = "", description: str = ""):
        """注册 API 端点"""
        key = f"{method.upper()}:{path}"
        # [2026-08-13 并发审计 D] 锁内写入：与 generate_swagger_doc 遍历互斥，
        # 防 RuntimeError（dictionary changed size during iteration）
        with self._lock:
            self._endpoints[key] = {
                "path": path,
                "method": method.upper(),
                "handler": handler,
                "auth_required": auth_required,
                "scopes": scopes or [],
                "summary": summary,
                "description": description,
            }
    
    def add_middleware(self, middleware: Callable):
        """添加中间件"""
        self._middleware.append(middleware)
    
    def authenticate(self, request) -> Optional[Dict]:
        """认证请求"""
        auth_header = request.headers.get("Authorization", "")
        
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            return self._api_key_manager.validate_key(token)
        elif auth_header.startswith("Api-Key "):
            token = auth_header[8:]
            return self._api_key_manager.validate_key(token)
        
        api_key = request.headers.get("X-API-Key", "")
        if api_key:
            return self._api_key_manager.validate_key(api_key)
        
        return None
    
    def check_scopes(self, key_info: Dict, required_scopes: List[str]) -> bool:
        """检查权限范围（T8.2 RBAC：租户 Key 走角色权限表，旧 Key 走自带 scopes）"""
        if not required_scopes:
            return True

        tenant_id = key_info.get("tenant_id", "")
        if not tenant_id:
            # 旧 Key：无租户绑定 → 自带 scopes 白名单（不触达 tenant_manager）
            key_scopes = key_info.get("scopes", [])
            return all(scope in key_scopes for scope in required_scopes)

        # 租户 Key：scope → PermissionType 映射，逐项走角色权限表
        from agent.multi_tenant import tenant_manager, PermissionType
        scope_perm = {
            "read": PermissionType.READ,
            "write": PermissionType.WRITE,
            "delete": PermissionType.DELETE,
            "manage": PermissionType.MANAGE,
            "admin": PermissionType.ADMIN,
        }
        for scope in required_scopes:
            perm = scope_perm.get(scope)
            if perm is None:
                return False  # 未知 scope → 拒绝
            if not tenant_manager.has_permission(
                key_info.get("user_id", ""), tenant_id, perm
            ):
                return False
        return True
    
    def handle_request(self, request) -> Dict:
        """处理请求"""
        start_time = time.time()
        trace_id = get_trace_id()
        
        path = request.path
        method = request.method
        
        endpoint_key = f"{method.upper()}:{path}"
        endpoint = self._endpoints.get(endpoint_key)
        
        if not endpoint:
            return {"error": "Endpoint not found", "status_code": 404}
        
        log_entry = {
            "trace_id": trace_id,
            "endpoint": path,
            "method": method,
        }
        
        try:
            if endpoint["auth_required"]:
                key_info = self.authenticate(request)
                if not key_info:
                    log_entry["status_code"] = 401
                    log_entry["error"] = "Unauthorized"
                    self._access_logger.log_access(log_entry)
                    return {"error": "Unauthorized", "status_code": 401}
                
                user_id = key_info["user_id"]
                log_entry["user_id"] = user_id
                tenant_id = key_info.get("tenant_id", "") or None
                # T8.4：注入请求上下文，供审计/视图读取租户信息（跨租户隔离入口）
                request._gateway_key_info = {
                    "tenant_id": tenant_id or "",
                    "user_id": user_id,
                }

                # T8.2 兼容期：旧 Key 在 compat_until 之后过期，拒绝访问
                compat_until = key_info.get("compat_until", "")
                if compat_until:
                    try:
                        expired_at = datetime.fromisoformat(compat_until)
                        if datetime.now() > expired_at:
                            log_entry["status_code"] = 403
                            log_entry["error"] = "兼容期已过"
                            self._access_logger.log_access(log_entry)
                            return {"error": "兼容期已过", "status_code": 403}
                    except ValueError:
                        pass  # 非法格式视为无兼容期限制（宽松兼容）

                if not self.check_scopes(key_info, endpoint["scopes"]):
                    log_entry["status_code"] = 403
                    log_entry["error"] = "Forbidden"
                    self._access_logger.log_access(log_entry)
                    return {"error": "Forbidden", "status_code": 403}
            else:
                user_id = "anonymous"
                log_entry["user_id"] = user_id
                tenant_id = None
            
            if not self._rate_limiter.check(endpoint=path, user_id=user_id, tenant_id=tenant_id):
                log_entry["status_code"] = 429
                log_entry["error"] = "Rate limit exceeded"
                self._access_logger.log_access(log_entry)
                return {"error": "Rate limit exceeded", "status_code": 429}
            
            if not self._quota_manager.check_quota(user_id, "api_calls"):
                log_entry["status_code"] = 429
                log_entry["error"] = "Quota exceeded"
                self._access_logger.log_access(log_entry)
                return {"error": "Quota exceeded", "status_code": 429}

            if tenant_id and not self._quota_manager.check_tenant_quota(tenant_id, "api_calls"):
                log_entry["status_code"] = 429
                log_entry["error"] = "Tenant quota exceeded"
                self._access_logger.log_access(log_entry)
                return {"error": "Tenant quota exceeded", "status_code": 429}
            
            for middleware in self._middleware:
                middleware(request)
            
            handler = endpoint["handler"]
            result = handler(request)
            
            self._quota_manager.consume_quota(user_id, "api_calls")

            if tenant_id:
                self._quota_manager.consume_tenant_quota(tenant_id, "api_calls")
            
            if endpoint["auth_required"]:
                self._api_key_manager.increment_usage(key_info["key"])
            
            duration_ms = (time.time() - start_time) * 1000
            log_entry["status_code"] = result.get("status_code", 200)
            log_entry["duration_ms"] = round(duration_ms, 2)
            log_entry["success"] = True
            self._access_logger.log_access(log_entry)
            
            return result
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            log_entry["status_code"] = 500
            log_entry["error"] = str(e)
            log_entry["duration_ms"] = round(duration_ms, 2)
            log_entry["success"] = False
            self._access_logger.log_access(log_entry)
            
            return {"error": str(e), "status_code": 500}
    
    def generate_swagger_doc(self) -> Dict:
        """生成 Swagger 文档"""
        swagger = {
            "openapi": "3.0.0",
            "info": {
                "title": "云枢 API Gateway",
                "description": "云枢智能体开放 API 文档",
                "version": "1.0.0",
            },
            "servers": [
                {"url": "http://localhost:5678/api"},
            ],
            "paths": {},
            "components": {
                "securitySchemes": {
                    "ApiKeyAuth": {
                        "type": "apiKey",
                        "in": "header",
                        "name": "X-API-Key",
                    },
                    "BearerAuth": {
                        "type": "http",
                        "scheme": "bearer",
                    },
                },
            },
        }
        
        # [2026-08-13 并发审计 D] 锁内遍历：register_endpoint 并发注册时锁外
        # 遍历会抛 RuntimeError（dictionary changed size during iteration）。
        with self._lock:
            for key, endpoint in self._endpoints.items():
                method, path = key.split(":", 1)
                
                if path not in swagger["paths"]:
                    swagger["paths"][path] = {}
                
                swagger["paths"][path][method.lower()] = {
                    "summary": endpoint.get("summary", f"{method} {path}"),
                    "description": endpoint.get("description", ""),
                    "security": [{"ApiKeyAuth": []}] if endpoint["auth_required"] else [],
                    "responses": {
                        "200": {"description": "Success"},
                    },
                }
        
        return swagger
    
    def get_stats(self) -> Dict:
        """获取网关统计"""
        return {
            "endpoints": len(self._endpoints),
            "api_keys": len(self._api_key_manager.list_keys()),
            "access_logs": len(self._access_logger._logs),
            "rate_limiter": self._rate_limiter.get_status(),
        }


_api_gateway = None  # 保留作为 fallback
# [2026-08-13 并发审计 #5] fallback 懒加载单例双检锁
_api_gateway_lock = threading.Lock()

try:
    from agent.utils.singleton_manager import register_singleton, get_singleton
    _SINGLETON_AVAILABLE = True
except ImportError:
    _SINGLETON_AVAILABLE = False
    register_singleton = None
    get_singleton = None


def _create_api_gateway(config=None):
    """ApiGateway 工厂函数（供 SingletonManager 使用）"""
    return ApiGateway()


def get_api_gateway() -> ApiGateway:
    """获取 API 网关实例"""
    if _SINGLETON_AVAILABLE:
        return get_singleton("api_gateway")
    global _api_gateway
    if _api_gateway is None:
        # [2026-08-13 并发审计 #5] 双检锁：并发首次调用只创建一个实例
        with _api_gateway_lock:
            if _api_gateway is None:
                _api_gateway = _create_api_gateway()
    return _api_gateway


if _SINGLETON_AVAILABLE:
    register_singleton("api_gateway", _create_api_gateway)