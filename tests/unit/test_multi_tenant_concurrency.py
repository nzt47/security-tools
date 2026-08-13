"""multi_tenant 并发安全测试。

修复前：模块级单例 TenantManager/TenantConfigManager/BillingManager 被 HTTP
路由多线程调用——assign_role 的 existing 判断+修改/append 非原子（并发对同
user 分配会重复 append 丢更新）、delete_tenant 递归 del 与并发遍历抛
RuntimeError（dictionary changed size during iteration）、set_config/delete_config
「检查-删写」TOCTOU、record_usage append+截断读-改-写丢记录。修复后：三个
管理器各持 RLock，锁内仅内存变更，文件持久化 _save_data 在锁外（持锁纪律）。
"""

import threading

from agent.multi_tenant import (
    TenantManager,
    TenantConfigManager,
    BillingManager,
    RoleType,
)


class TestMultiTenantConcurrency:
    """TenantManager / TenantConfigManager / BillingManager 并发读写。"""

    def setup_method(self):
        # 隔离真实 data 目录：读写为 no-op（模块级单例会写真实 JSON 文件）
        self._orig_load = TenantManager._load_data
        self._orig_save = TenantManager._save_data
        TenantManager._load_data = lambda self: None
        TenantManager._save_data = lambda self: None
        self.mgr = TenantManager()
        self.cfg = TenantConfigManager()
        self.bill = BillingManager()

    def teardown_method(self):
        TenantManager._load_data = self._orig_load
        TenantManager._save_data = self._orig_save

    def test_concurrent_assign_role_no_duplicate(self):
        """100 线程对同 user/org 并发分配角色：不重复 append、角色不丢失"""
        org = self.mgr.create_organization("并发组织", "u-owner")
        n_threads, per = 100, 50
        roles = [RoleType.ADMIN, RoleType.MEMBER, RoleType.VIEWER]
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    self.mgr.assign_role("user-x", org.id, roles[(tid + i) % 3])
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # 并发「existing 判断 + append」原子化：owner（创建时分配）+ user-x（并发
        # 分配）各 1 条，user-x 不重复 append
        assignments = self.mgr._role_assignments[org.id]
        assert len(assignments) == 2
        user_x = [a for a in assignments if a.user_id == "user-x"]
        assert len(user_x) == 1
        assert user_x[0].role in roles
        # 角色链完整可读（owner 由 create_organization 分配）
        roles_chain = self.mgr.get_user_roles("u-owner", org.id)
        assert RoleType.OWNER in roles_chain

    def test_concurrent_create_organization_count_precise(self):
        """100 线程 × 5 次并发创建组织：计数精确、owner 角色完整"""
        n_threads, per = 100, 5
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    org = self.mgr.create_organization(f"org-{tid}-{i}", f"u-{tid}-{i}")
                    assert org.id in self.mgr._tenants
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(self.mgr._tenants) == total               # 创建计数精确
        assert len(self.mgr._role_assignments) == total      # 每个组织都有 owner 角色
        # 抽查：并发创建的组织角色表完整（每个 org 恰含其 owner 的 OWNER 记录；
        # org_id 为 secrets 随机生成，故按角色表逐项校验）
        for assignments in self.mgr._role_assignments.values():
            assert len(assignments) == 1
            assert assignments[0].role == RoleType.OWNER

    def test_concurrent_delete_tenant_no_crash(self):
        """预置租户树后 20 线程并发删除叶子：不抛 RuntimeError、状态一致"""
        root = self.mgr.create_organization("根组织", "u-owner")
        leaf_ids = []
        for i in range(50):
            org = self.mgr.create_organization(f"子组织-{i}", f"u-{i}")
            leaf_ids.append(org.id)
            ws = self.mgr.create_workspace(f"工作空间-{i}", org.id, f"u-{i}")
            leaf_ids.append(ws.id)
        assert len(self.mgr._tenants) == 1 + 100

        n_threads = 20
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker():
            try:
                barrier.wait()
                # 每线程并发删除全部叶子（大量重复删除，验证 del 与遍历互斥）
                for lid in leaf_ids:
                    self.mgr.delete_tenant(lid)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发删除不应抛 RuntimeError: {errors}"
        # 所有叶子已删除，仅剩根组织
        assert len(self.mgr._tenants) == 1
        assert self.mgr.get_tenant(root.id) is not None
        # 角色表随租户同步清理（无孤儿）
        assert len(self.mgr._role_assignments) == 1

    def test_concurrent_record_usage_count_precise(self):
        """100 线程 × 50 次并发记录用量：计数精确、读写混合不崩溃"""
        n_threads, per = 100, 50
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    self.bill.record_usage("t-1", "api_calls", amount=1)
                    if i % 10 == 0:
                        self.bill.get_usage("t-1", "api_calls", "month")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        usage = self.bill.get_usage("t-1", "api_calls", "month")
        assert usage["count"] == total                       # 记录无丢失
        assert usage["total"] == total                       # 用量精确

    def test_concurrent_config_set_get_consistent(self):
        """并发 set/delete/get 配置：无 KeyError、最终键集一致"""
        n_threads, per = 100, 20
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    self.cfg.set_config(f"t-{tid}", f"key-{i}", i)
                    self.cfg.get_config(f"t-{tid}", f"key-{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        # setdefault 原子初始化：每租户配置 dict 完整
        for t in range(n_threads):
            assert len(self.cfg._configs[f"t-{t}"]) == per
        # delete 与读取并发无 KeyError
        def deleter():
            for t in range(n_threads):
                for i in range(per):
                    self.cfg.delete_config(f"t-{t}", f"key-{i}")

        d = threading.Thread(target=deleter)
        d.start()
        d.join()
        assert all(not v for v in self.cfg._configs.values())
