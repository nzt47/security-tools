"""API 网关认证流程验证 Mock 数据

使用说明：
1. 将此文件中的数据导入到 API Key 管理器
2. 使用 Mock API Key 测试认证流程
3. 验证配额限制和访问日志功能

运行测试：
    python -m pytest tests/test_api_gateway.py -v
    或直接运行此文件进行手动验证
"""

import json
from datetime import datetime, timedelta
import secrets

# ============================================================
# Mock API Keys
# ============================================================

MOCK_API_KEYS = [
    {
        "key": "sk_test_abc123def456",
        "user_id": "user_001",
        "tenant_id": "tenant_demo_001",
        "description": "测试环境 API Key",
        "scopes": ["chat", "generate", "embeddings"],
        "rate_limit": 100,  # 每分钟请求数
        "daily_quota": 10000,  # 每日配额
        "enabled": True,
        "created_at": "2024-01-15T10:30:00Z",
        "expires_at": None,  # 永不过期
    },
    {
        "key": "sk_prod_xyz789ghi012",
        "user_id": "user_002",
        "tenant_id": "tenant_demo_002",
        "description": "生产环境 API Key",
        "scopes": ["chat", "generate", "admin"],
        "rate_limit": 1000,  # 每分钟请求数
        "daily_quota": 100000,  # 每日配额
        "enabled": True,
        "created_at": "2024-02-20T14:45:00Z",
        "expires_at": "2025-12-31T23:59:59Z",
    },
    {
        "key": "sk_limited_abc789xyz",
        "user_id": "user_003",
        "tenant_id": "tenant_demo_003",
        "description": "受限 API Key (测试配额限制)",
        "scopes": ["chat"],
        "rate_limit": 10,  # 每分钟请求数
        "daily_quota": 50,  # 每日配额
        "enabled": True,
        "created_at": "2024-03-01T08:00:00Z",
        "expires_at": None,
    },
    {
        "key": "sk_disabled_expired_key",
        "user_id": "user_004",
        "tenant_id": "tenant_demo_004",
        "description": "已禁用的 API Key",
        "scopes": ["chat"],
        "rate_limit": 100,
        "daily_quota": 10000,
        "enabled": False,  # 已禁用
        "created_at": "2024-01-01T00:00:00Z",
        "expires_at": "2024-06-01T00:00:00Z",  # 已过期
    },
]

# ============================================================
# Mock 租户信息
# ============================================================

MOCK_TENANTS = [
    {
        "tenant_id": "tenant_demo_001",
        "name": "演示租户 A",
        "type": "organization",
        "owner_id": "user_001",
        "plan": "free",
        "max_api_keys": 5,
        "features": ["chat", "generate"],
        "created_at": "2024-01-10T00:00:00Z",
    },
    {
        "tenant_id": "tenant_demo_002",
        "name": "企业版租户",
        "type": "organization",
        "owner_id": "user_002",
        "plan": "enterprise",
        "max_api_keys": 100,
        "features": ["chat", "generate", "embeddings", "fine_tuning", "admin"],
        "created_at": "2024-02-15T00:00:00Z",
    },
    {
        "tenant_id": "tenant_demo_003",
        "name": "受限测试租户",
        "type": "individual",
        "owner_id": "user_003",
        "plan": "trial",
        "max_api_keys": 1,
        "features": ["chat"],
        "created_at": "2024-03-01T00:00:00Z",
    },
    {
        "tenant_id": "tenant_demo_004",
        "name": "已过期租户",
        "type": "organization",
        "owner_id": "user_004",
        "plan": "expired",
        "max_api_keys": 5,
        "features": [],
        "created_at": "2024-01-01T00:00:00Z",
        "expires_at": "2024-06-01T00:00:00Z",
    },
]

# ============================================================
# Mock 用户信息
# ============================================================

MOCK_USERS = [
    {
        "user_id": "user_001",
        "email": "demo_a@example.com",
        "name": "演示用户 A",
        "tenant_id": "tenant_demo_001",
        "role": "admin",
        "created_at": "2024-01-10T00:00:00Z",
    },
    {
        "user_id": "user_002",
        "email": "enterprise@example.com",
        "name": "企业管理员",
        "tenant_id": "tenant_demo_002",
        "role": "owner",
        "created_at": "2024-02-15T00:00:00Z",
    },
    {
        "user_id": "user_003",
        "email": "trial@example.com",
        "name": "试用用户",
        "tenant_id": "tenant_demo_003",
        "role": "member",
        "created_at": "2024-03-01T00:00:00Z",
    },
    {
        "user_id": "user_004",
        "email": "expired@example.com",
        "name": "过期用户",
        "tenant_id": "tenant_demo_004",
        "role": "admin",
        "created_at": "2024-01-01T00:00:00Z",
    },
]

# ============================================================
# Mock 配额使用数据
# ============================================================

MOCK_QUOTA_USAGE = {
    "user_001": {
        "api_calls": {"used": 500, "limit": 10000, "reset_at": "2024-06-25T00:00:00Z"},
        "tokens": {"used": 150000, "limit": 1000000, "reset_at": "2024-06-25T00:00:00Z"},
    },
    "user_002": {
        "api_calls": {"used": 15000, "limit": 100000, "reset_at": "2024-06-25T00:00:00Z"},
        "tokens": {"used": 5000000, "limit": 10000000, "reset_at": "2024-06-25T00:00:00Z"},
    },
    "user_003": {
        "api_calls": {"used": 45, "limit": 50, "reset_at": "2024-06-25T00:00:00Z"},  # 接近配额上限
        "tokens": {"used": 8000, "limit": 100000, "reset_at": "2024-06-25T00:00:00Z"},
    },
}

# ============================================================
# Mock 访问日志
# ============================================================

def generate_mock_access_logs():
    """生成模拟访问日志"""
    logs = []
    base_time = datetime.now()
    
    endpoints = ["/api/v1/chat", "/api/v1/generate", "/api/v1/embeddings"]
    methods = ["GET", "POST"]
    status_codes = [200, 200, 200, 400, 401, 429, 500]  # 模拟不同状态码
    
    for i in range(100):
        timestamp = base_time - timedelta(minutes=i * 5)
        user_idx = i % 4
        endpoint_idx = i % 3
        method_idx = i % 2
        status_idx = i % 7
        
        logs.append({
            "log_id": f"log_{i:04d}",
            "timestamp": timestamp.isoformat(),
            "request_id": f"req_{timestamp.strftime('%Y%m%d%H%M%S')}_{i:04d}",
            "user_id": MOCK_USERS[user_idx]["user_id"],
            "tenant_id": MOCK_TENANTS[user_idx]["tenant_id"],
            "api_key": MOCK_API_KEYS[user_idx]["key"][:12] + "...",
            "endpoint": endpoints[endpoint_idx],
            "method": methods[method_idx],
            "status_code": status_codes[status_idx],
            "duration_ms": 50 + (i % 100) * 10,  # 50-1050ms
            "tokens_used": 100 + (i % 50) * 10,
            "ip_address": f"192.168.1.{100 + (i % 50)}",
            "user_agent": "TestClient/1.0",
        })
    
    return logs

MOCK_ACCESS_LOGS = generate_mock_access_logs()


def print_test_scenarios():
    """打印测试场景说明"""
    print("=" * 70)
    print("API 网关认证流程验证 - 测试场景")
    print("=" * 70)
    print()
    print("场景 1: 有效 API Key 认证")
    print(f"  API Key: {MOCK_API_KEYS[0]['key']}")
    print(f"  预期结果: 认证成功，返回用户信息")
    print()
    print("场景 2: 无效 API Key 认证")
    print("  API Key: sk_invalid_key_12345")
    print("  预期结果: 认证失败，返回 401 Unauthorized")
    print()
    print("场景 3: 配额耗尽测试")
    print(f"  API Key: {MOCK_API_KEYS[2]['key']} (user_003)")
    print(f"  当前使用: {MOCK_QUOTA_USAGE['user_003']['api_calls']['used']}/{MOCK_QUOTA_USAGE['user_003']['api_calls']['limit']}")
    print("  预期结果: 返回 429 Too Many Requests")
    print()
    print("场景 4: 禁用/过期 API Key")
    print(f"  API Key: {MOCK_API_KEYS[3]['key']}")
    print("  预期结果: 认证失败，返回 401 Unauthorized")
    print()
    print("场景 5: 速率限制测试")
    print(f"  API Key: {MOCK_API_KEYS[2]['key']} (rate_limit: 10/min)")
    print("  操作: 快速发送 15 个请求")
    print("  预期结果: 前 10 个成功，第 11 个开始被限流")
    print()
    print("=" * 70)


if __name__ == "__main__":
    print_test_scenarios()
    print()
    print("Mock 数据已准备就绪！")
    print(f"  - API Keys: {len(MOCK_API_KEYS)} 个")
    print(f"  - 租户: {len(MOCK_TENANTS)} 个")
    print(f"  - 用户: {len(MOCK_USERS)} 个")
    print(f"  - 访问日志: {len(MOCK_ACCESS_LOGS)} 条")
