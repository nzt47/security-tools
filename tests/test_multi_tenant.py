"""多租户模块单元测试"""

import unittest
import tempfile

from agent.multi_tenant import (
    TenantManager, TenantConfigManager, BillingManager,
    TenantType, RoleType, PermissionType
)


class TestTenantManager(unittest.TestCase):
    """测试租户管理器"""
    
    def test_create_user(self):
        """测试创建用户"""
        manager = TenantManager()
        user = manager.create_user("test@example.com", "Test User")
        
        self.assertEqual(user.email, "test@example.com")
        self.assertEqual(user.name, "Test User")
        self.assertIsNotNone(user.id)
    
    def test_create_organization(self):
        """测试创建组织"""
        manager = TenantManager()
        user = manager.create_user("owner@example.com", "Owner")
        
        org = manager.create_organization("Test Org", user.id)
        
        self.assertEqual(org.name, "Test Org")
        self.assertEqual(org.type, TenantType.ORGANIZATION)
    
    def test_create_workspace(self):
        """测试创建工作空间"""
        manager = TenantManager()
        user = manager.create_user("admin@example.com", "Admin")
        org = manager.create_organization("Test Org", user.id)
        
        workspace = manager.create_workspace("Test Workspace", org.id, user.id)
        
        self.assertEqual(workspace.name, "Test Workspace")
        self.assertEqual(workspace.type, TenantType.WORKSPACE)
        self.assertEqual(workspace.parent_id, org.id)
    
    def test_assign_role(self):
        """测试分配角色"""
        manager = TenantManager()
        user = manager.create_user("user@example.com", "User")
        org = manager.create_organization("Test Org", user.id)
        
        manager.assign_role(user.id, org.id, RoleType.ADMIN)
        
        roles = manager.get_user_roles(user.id, org.id)
        self.assertIn(RoleType.ADMIN, roles)
    
    def test_has_permission(self):
        """测试权限检查"""
        manager = TenantManager()
        user = manager.create_user("user@example.com", "User")
        org = manager.create_organization("Test Org", user.id)
        
        self.assertTrue(manager.has_permission(user.id, org.id, PermissionType.READ))
        self.assertTrue(manager.has_permission(user.id, org.id, PermissionType.WRITE))
        self.assertTrue(manager.has_permission(user.id, org.id, PermissionType.DELETE))
        
        user2 = manager.create_user("user2@example.com", "User2")
        manager.assign_role(user2.id, org.id, RoleType.VIEWER)
        
        self.assertTrue(manager.has_permission(user2.id, org.id, PermissionType.READ))
        self.assertFalse(manager.has_permission(user2.id, org.id, PermissionType.WRITE))


class TestTenantConfigManager(unittest.TestCase):
    """测试租户配置管理器"""
    
    def test_set_and_get_config(self):
        """测试设置和获取配置"""
        config_manager = TenantConfigManager()
        
        config_manager.set_config("tenant1", "theme", "dark")
        config_manager.set_config("tenant1", "language", "zh-CN")
        
        self.assertEqual(config_manager.get_config("tenant1", "theme"), "dark")
        self.assertEqual(config_manager.get_config("tenant1", "language"), "zh-CN")
    
    def test_config_inheritance(self):
        """测试配置继承"""
        config_manager = TenantConfigManager()
        
        config_manager.set_config("parent_org", "default_setting", "parent_value")
        config_manager.set_config("child_workspace", "custom_setting", "child_value")
        
        from agent.multi_tenant import tenant_manager
        tenant_manager._tenants["parent_org"] = type('Tenant', (), {'id': 'parent_org', 'parent_id': ''})()
        tenant_manager._tenants["child_workspace"] = type('Tenant', (), {'id': 'child_workspace', 'parent_id': 'parent_org'})()
        
        self.assertEqual(config_manager.get_config("child_workspace", "default_setting"), "parent_value")
        self.assertEqual(config_manager.get_config("child_workspace", "custom_setting"), "child_value")


class TestBillingManager(unittest.TestCase):
    """测试计费管理器"""
    
    def test_record_usage(self):
        """测试记录用量"""
        billing = BillingManager()
        
        billing.record_usage("tenant1", "api_calls", 5)
        billing.record_usage("tenant1", "api_calls", 3)
        
        usage = billing.get_usage("tenant1", "api_calls")
        self.assertEqual(usage["total"], 8)
    
    def test_check_limit(self):
        """测试检查限制"""
        billing = BillingManager()
        
        from agent.multi_tenant import tenant_config_manager
        tenant_config_manager.set_config("tenant1", "billing_plan", "free")
        
        self.assertTrue(billing.check_limit("tenant1", "api_calls", 500))
        self.assertTrue(billing.check_limit("tenant1", "api_calls", 1000))
        self.assertFalse(billing.check_limit("tenant1", "api_calls", 1001))
    
    def test_get_plan_info(self):
        """测试获取方案信息"""
        billing = BillingManager()
        
        plan = billing.get_plan_info("pro")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["name"], "专业版")
        self.assertEqual(plan["price"], 99)


if __name__ == "__main__":
    unittest.main()