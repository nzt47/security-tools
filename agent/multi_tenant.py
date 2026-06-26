"""多租户支持 — 用户/组织/工作空间三级体系

提供：
  - 三级租户架构：用户 → 组织 → 工作空间
  - 数据隔离（Memory、配置、日志）
  - RBAC 权限管理
  - 用量计费基础框架
  - 租户级配置覆盖
"""

import json
import logging
import enum
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from pathlib import Path
from dataclasses import dataclass, field

from agent.monitoring.tracing import get_trace_id

logger = logging.getLogger(__name__)


class TenantType(str, enum.Enum):
    """租户类型"""
    USER = "user"
    ORGANIZATION = "organization"
    WORKSPACE = "workspace"


class RoleType(str, enum.Enum):
    """角色类型"""
    ADMIN = "admin"
    OWNER = "owner"
    MEMBER = "member"
    VIEWER = "viewer"


class PermissionType(str, enum.Enum):
    """权限类型"""
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    MANAGE = "manage"
    ADMIN = "admin"


@dataclass
class Tenant:
    """租户信息"""
    id: str
    name: str
    type: TenantType
    parent_id: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class User:
    """用户信息"""
    id: str
    email: str
    name: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class RoleAssignment:
    """角色分配"""
    user_id: str
    tenant_id: str
    role: RoleType
    assigned_at: str = field(default_factory=lambda: datetime.now().isoformat())


class TenantManager:
    """租户管理器"""
    
    def __init__(self):
        self._tenants: Dict[str, Tenant] = {}
        self._users: Dict[str, User] = {}
        self._role_assignments: Dict[str, List[RoleAssignment]] = {}
        self._load_data()
    
    def _load_data(self):
        """加载数据"""
        data_dir = Path(__file__).parent / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        
        files = {
            "tenants": "tenants.json",
            "users": "users.json",
            "roles": "role_assignments.json"
        }
        
        for key, filename in files.items():
            filepath = data_dir / filename
            if filepath.exists():
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        if key == "tenants":
                            self._tenants = {t["id"]: Tenant(**t) for t in json.load(f)}
                        elif key == "users":
                            self._users = {u["id"]: User(**u) for u in json.load(f)}
                        elif key == "roles":
                            raw = json.load(f)
                            for tenant_id, assignments in raw.items():
                                self._role_assignments[tenant_id] = [
                                    RoleAssignment(**a) for a in assignments
                                ]
                except Exception as e:
                    logger.warning(f"加载 {filename} 失败: {e}")
    
    def _save_data(self):
        """保存数据"""
        data_dir = Path(__file__).parent / "data"
        
        with open(data_dir / "tenants.json", 'w', encoding='utf-8') as f:
            json.dump([vars(t) for t in self._tenants.values()], f, ensure_ascii=False, indent=2)
        
        with open(data_dir / "users.json", 'w', encoding='utf-8') as f:
            json.dump([vars(u) for u in self._users.values()], f, ensure_ascii=False, indent=2)
        
        roles_dict = {}
        for tenant_id, assignments in self._role_assignments.items():
            roles_dict[tenant_id] = [vars(a) for a in assignments]
        with open(data_dir / "role_assignments.json", 'w', encoding='utf-8') as f:
            json.dump(roles_dict, f, ensure_ascii=False, indent=2)
    
    def create_user(self, email: str, name: str, metadata: Dict = None) -> User:
        """创建用户"""
        import secrets
        user_id = "user_" + secrets.token_hex(16)
        user = User(
            id=user_id,
            email=email,
            name=name,
            metadata=metadata or {}
        )
        self._users[user_id] = user
        self._save_data()
        return user
    
    def get_user(self, user_id: str) -> Optional[User]:
        """获取用户"""
        return self._users.get(user_id)
    
    def create_organization(self, name: str, owner_id: str) -> Tenant:
        """创建组织"""
        import secrets
        org_id = "org_" + secrets.token_hex(16)
        tenant = Tenant(
            id=org_id,
            name=name,
            type=TenantType.ORGANIZATION,
            parent_id=""
        )
        self._tenants[org_id] = tenant
        self.assign_role(owner_id, org_id, RoleType.OWNER)
        self._save_data()
        return tenant
    
    def create_workspace(self, name: str, organization_id: str, creator_id: str) -> Tenant:
        """创建工作空间"""
        if organization_id not in self._tenants:
            raise ValueError(f"组织不存在: {organization_id}")
        
        import secrets
        workspace_id = "ws_" + secrets.token_hex(16)
        tenant = Tenant(
            id=workspace_id,
            name=name,
            type=TenantType.WORKSPACE,
            parent_id=organization_id
        )
        self._tenants[workspace_id] = tenant
        self.assign_role(creator_id, workspace_id, RoleType.ADMIN)
        self._save_data()
        return tenant
    
    def get_tenant(self, tenant_id: str) -> Optional[Tenant]:
        """获取租户"""
        return self._tenants.get(tenant_id)
    
    def assign_role(self, user_id: str, tenant_id: str, role: RoleType):
        """分配角色"""
        if tenant_id not in self._tenants:
            raise ValueError(f"租户不存在: {tenant_id}")
        
        if tenant_id not in self._role_assignments:
            self._role_assignments[tenant_id] = []
        
        existing = [a for a in self._role_assignments[tenant_id] if a.user_id == user_id]
        if existing:
            existing[0].role = role
            existing[0].assigned_at = datetime.now().isoformat()
        else:
            self._role_assignments[tenant_id].append(RoleAssignment(
                user_id=user_id,
                tenant_id=tenant_id,
                role=role
            ))
        
        self._save_data()
    
    def get_user_roles(self, user_id: str, tenant_id: str) -> List[RoleType]:
        """获取用户在租户中的角色"""
        roles = []
        
        current_id = tenant_id
        while current_id:
            assignments = self._role_assignments.get(current_id, [])
            for a in assignments:
                if a.user_id == user_id:
                    roles.append(a.role)
                    break
            
            tenant = self._tenants.get(current_id)
            current_id = tenant.parent_id if tenant else ""
        
        return roles
    
    def has_permission(self, user_id: str, tenant_id: str, permission: PermissionType) -> bool:
        """检查用户是否有指定权限"""
        roles = self.get_user_roles(user_id, tenant_id)
        
        permission_map = {
            RoleType.OWNER: [PermissionType.READ, PermissionType.WRITE, PermissionType.DELETE, PermissionType.MANAGE, PermissionType.ADMIN],
            RoleType.ADMIN: [PermissionType.READ, PermissionType.WRITE, PermissionType.DELETE, PermissionType.MANAGE],
            RoleType.MEMBER: [PermissionType.READ, PermissionType.WRITE],
            RoleType.VIEWER: [PermissionType.READ],
        }
        
        for role in roles:
            if permission in permission_map.get(role, []):
                return True
        
        return False
    
    def get_user_tenants(self, user_id: str, tenant_type: TenantType = None) -> List[Tenant]:
        """获取用户有权访问的租户"""
        tenants = []
        
        for tenant_id, assignments in self._role_assignments.items():
            for a in assignments:
                if a.user_id == user_id:
                    tenant = self._tenants.get(tenant_id)
                    if tenant:
                        if not tenant_type or tenant.type == tenant_type:
                            tenants.append(tenant)
        
        return tenants
    
    def delete_tenant(self, tenant_id: str):
        """删除租户"""
        if tenant_id in self._tenants:
            del self._tenants[tenant_id]
            if tenant_id in self._role_assignments:
                del self._role_assignments[tenant_id]
            
            child_ids = [t.id for t in self._tenants.values() if t.parent_id == tenant_id]
            for child_id in child_ids:
                self.delete_tenant(child_id)
            
            self._save_data()


class TenantConfigManager:
    """租户配置管理器"""
    
    def __init__(self):
        self._configs: Dict[str, Dict] = {}
    
    def set_config(self, tenant_id: str, key: str, value: Any):
        """设置配置"""
        if tenant_id not in self._configs:
            self._configs[tenant_id] = {}
        self._configs[tenant_id][key] = value
    
    def get_config(self, tenant_id: str, key: str, default: Any = None) -> Any:
        """获取配置（支持继承）"""
        config = self._configs.get(tenant_id, {})
        
        if key in config:
            return config[key]
        
        tenant = tenant_manager.get_tenant(tenant_id)
        if tenant and tenant.parent_id:
            return self.get_config(tenant.parent_id, key, default)
        
        return default
    
    def get_all_configs(self, tenant_id: str) -> Dict:
        """获取所有配置（含继承）"""
        config = {}
        
        tenant = tenant_manager.get_tenant(tenant_id)
        
        if tenant and tenant.parent_id:
            parent_config = self.get_all_configs(tenant.parent_id)
            config.update(parent_config)
        
        if tenant_id in self._configs:
            config.update(self._configs[tenant_id])
        
        return config
    
    def delete_config(self, tenant_id: str, key: str):
        """删除配置"""
        if tenant_id in self._configs and key in self._configs[tenant_id]:
            del self._configs[tenant_id][key]


class BillingManager:
    """计费管理器"""
    
    def __init__(self):
        self._usage_records: List[Dict] = []
        self._plans: Dict[str, Dict] = {
            "free": {
                "name": "免费版",
                "limits": {"api_calls": 1000, "storage": 100},
                "price": 0,
            },
            "pro": {
                "name": "专业版",
                "limits": {"api_calls": 100000, "storage": 1000},
                "price": 99,
            },
            "enterprise": {
                "name": "企业版",
                "limits": {"api_calls": -1, "storage": -1},
                "price": 999,
            },
        }
    
    def record_usage(self, tenant_id: str, usage_type: str, amount: int = 1):
        """记录用量"""
        record = {
            "tenant_id": tenant_id,
            "usage_type": usage_type,
            "amount": amount,
            "timestamp": datetime.now().isoformat(),
            "trace_id": get_trace_id(),
        }
        self._usage_records.append(record)
        
        if len(self._usage_records) > 100000:
            self._usage_records = self._usage_records[-100000:]
    
    def get_usage(self, tenant_id: str, usage_type: str = None, period: str = "month") -> Dict:
        """获取用量统计"""
        now = datetime.now()
        
        if period == "month":
            start_time = datetime(now.year, now.month, 1)
        elif period == "week":
            start_time = now - timedelta(weeks=1)
        else:
            start_time = now - timedelta(days=1)
        
        filtered = [
            r for r in self._usage_records
            if r["tenant_id"] == tenant_id
            and datetime.fromisoformat(r["timestamp"]) >= start_time
            and (not usage_type or r["usage_type"] == usage_type)
        ]
        
        total = sum(r["amount"] for r in filtered)
        
        return {
            "tenant_id": tenant_id,
            "period": period,
            "total": total,
            "count": len(filtered),
            "usage_type": usage_type,
        }
    
    def check_limit(self, tenant_id: str, usage_type: str, amount: int = 1) -> bool:
        """检查是否超出限制"""
        plan = self._get_tenant_plan(tenant_id)
        limit = plan["limits"].get(usage_type, -1)
        
        if limit < 0:
            return True
        
        current = self.get_usage(tenant_id, usage_type, "month")["total"]
        return current + amount <= limit
    
    def _get_tenant_plan(self, tenant_id: str) -> Dict:
        """获取租户的计费方案"""
        plan_id = tenant_config_manager.get_config(tenant_id, "billing_plan", "free")
        return self._plans.get(plan_id, self._plans["free"])
    
    def get_plan_info(self, plan_id: str) -> Optional[Dict]:
        """获取方案信息"""
        return self._plans.get(plan_id)


tenant_manager = TenantManager()
tenant_config_manager = TenantConfigManager()
billing_manager = BillingManager()